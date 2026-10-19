"""Exercise Douyin's HTTP failure handling and real-video audio path offline."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def dy(monkeypatch, tmp_path):
    for package in ("app", "app.downloaders", "app.models", "app.services", "app.utils", "app.enmus"):
        namespace = types.ModuleType(package)
        namespace.__path__ = [str(BACKEND.joinpath(*package.split(".")))]
        monkeypatch.setitem(sys.modules, package, namespace)
    for name in ("app.downloaders.base", "app.models.audio_model", "app.models.notes_model",
                 "app.models.transcriber_model", "app.enmus.note_enums", "app.utils.path_helper"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    cookie = {"value": "sessionid=private-old-value"}
    manager = types.ModuleType("app.services.cookie_manager")
    manager.CookieConfigManager = lambda: types.SimpleNamespace(get=lambda _platform: cookie["value"])
    monkeypatch.setitem(sys.modules, manager.__name__, manager)
    spec = importlib.util.spec_from_file_location("douyin_under_test", BACKEND / "app/downloaders/douyin_downloader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    downloader = module.DouyinDownloader()
    monkeypatch.setattr(downloader, "extract_video_id", lambda _url: "123456789")
    monkeypatch.setattr(downloader, "gen_real_msToken", lambda: "private-token")
    return module, downloader, cookie


def response(body, status=200, content_type="application/json"):
    result = requests.Response()
    result.status_code = status
    result._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    result._content_consumed = True
    result.headers["Content-Type"] = content_type
    return result


def test_detail_request_supplies_argus_and_refreshes_cookie(dy, monkeypatch, capsys):
    module, downloader, cookie = dy
    cookie["value"] = "sessionid=private-new-value"
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return response({"aweme_detail": {"aweme_id": "123456789"}})
    monkeypatch.setattr(module.requests, "get", get)
    downloader.fetch_video_info("https://www.douyin.com/video/123456789")
    assert calls[0]["headers"]["x-tt-argus"] == "1"
    assert calls[0]["headers"]["Cookie"] == cookie["value"]
    assert calls[0]["timeout"] > 0
    output = capsys.readouterr().out
    assert "private-old-value" not in output
    assert "private-new-value" not in output
    assert "private-token" not in output


def test_forbidden_response_reports_gateway_error_instead_of_json_error(dy, monkeypatch):
    module, downloader, _ = dy
    monkeypatch.setattr(module.requests, "get", lambda *a, **kw: response(
        b"Blocked by ArgusSecurityPlugin Uifid Not Found", 403, "text/plain"))
    with pytest.raises(ValueError, match=r"HTTP 403.*Uifid Not Found"):
        downloader.fetch_video_info("https://www.douyin.com/video/123456789")


@pytest.mark.parametrize("body,content_type,expected", [
    (b"", "application/json", "空响应"),
    (b"<html>challenge</html>", "text/html", "非 JSON"),
])
def test_non_json_success_is_readable(dy, monkeypatch, body, content_type, expected):
    module, downloader, _ = dy
    monkeypatch.setattr(module.requests, "get", lambda *a, **kw: response(body, content_type=content_type))
    with pytest.raises(ValueError, match=expected):
        downloader.fetch_video_info("https://www.douyin.com/video/123456789")


def test_unavailable_video_is_reported_before_downloading(dy, monkeypatch):
    module, downloader, _ = dy
    monkeypatch.setattr(module.requests, "get", lambda *a, **kw: response(
        {"aweme_detail": None, "filter_detail": {"filter_reason": "private"}}))
    with pytest.raises(ValueError, match="private"):
        downloader.fetch_video_info("https://www.douyin.com/video/123456789")


def test_invalid_link_does_not_request_detail_or_token(dy, monkeypatch):
    _, downloader, _ = dy
    monkeypatch.setattr(downloader, "extract_video_id", lambda _url: "")
    monkeypatch.setattr(downloader, "gen_real_msToken", lambda: pytest.fail("invalid link requested a token"))
    with pytest.raises(ValueError, match="视频 ID"):
        downloader.fetch_video_info("https://www.douyin.com/user/example")


def test_audio_is_extracted_from_video_and_metadata_uses_seconds(dy, monkeypatch, tmp_path):
    module, downloader, _ = dy
    detail = {"aweme_id": "123456789", "item_title": "", "desc": "口播内容",
              "music": {"play_url": {"uri": "https://media.example/bgm.mp3"}},
              "video": {"duration": 12500,
                        "play_addr": {"url_list": ["https://media.example/video.mp4"]},
                        "cover": {"url_list": ["https://media.example/cover.jpg"]}}}
    monkeypatch.setattr(downloader, "fetch_video_info", lambda _url: {"aweme_detail": detail})
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        result = response(b"real-video", content_type="video/mp4")
        return result
    monkeypatch.setattr(module.requests, "get", get)
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"extracted-audio")
        return types.SimpleNamespace(returncode=0)
    # The subprocess boundary is the only external encoder in this offline test.
    import subprocess
    monkeypatch.setattr(subprocess, "run", run)
    result = downloader.download("https://www.douyin.com/video/123456789", str(tmp_path), need_video=True)
    assert urls == ["https://media.example/video.mp4"]
    assert "-vn" in commands[0]
    assert Path(result.file_path).read_bytes() == b"extracted-audio"
    assert Path(result.video_path).read_bytes() == b"real-video"
    assert result.duration == 12.5
    assert result.title == "口播内容"
    assert result.cover_url == "https://media.example/cover.jpg"


def test_audio_cache_is_published_only_after_encoder_finishes(dy, monkeypatch, tmp_path):
    _, downloader, _ = dy
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(downloader, "fetch_video_info", lambda _url: {
        "aweme_detail": {"aweme_id": "123456789", "video": {"duration": 1000}}})
    monkeypatch.setattr(downloader, "_save_video", lambda *a: str(video))
    final = tmp_path / "123456789.mp3"
    def encode(command, **kwargs):
        Path(command[-1]).write_bytes(b"encoding-in-progress")
        assert not final.exists(), "another task could reuse an unfinished audio file"
    import subprocess
    monkeypatch.setattr(subprocess, "run", encode)
    downloader.download("https://www.douyin.com/video/123456789", str(tmp_path))
    assert final.read_bytes() == b"encoding-in-progress"
