"""Offline coverage of the BCut gateway contract and bounded result polling."""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest
import requests

BACKEND = Path(__file__).resolve().parents[1]
RESULT = {"state": 4, "result": json.dumps({"language": "zh", "utterances": [
    {"start_time": 0, "end_time": 1000, "transcript": "测试语音"}]})}


@pytest.fixture
def bcut(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for package in ("app", "app.transcriber", "app.models", "app.utils", "app.decorators"):
        namespace = types.ModuleType(package)
        namespace.__path__ = [str(BACKEND.joinpath(*package.split(".")))]
        monkeypatch.setitem(sys.modules, package, namespace)
    for name in ("app.transcriber.base", "app.models.transcriber_model", "app.decorators.timeit", "app.utils.logger"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    events = types.ModuleType("events")
    events.transcription_finished = types.SimpleNamespace(send=lambda *args: None)
    monkeypatch.setitem(sys.modules, "events", events)
    spec = importlib.util.spec_from_file_location("bcut_under_test", BACKEND / "app/transcriber/bcut.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    transcriber = module.BcutTranscriber()
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio")
    return module, transcriber, audio


def http_error(status):
    response = requests.Response()
    response.status_code = status
    response._content = b"<html>blocked</html>"
    return requests.HTTPError(f"HTTP {status}", response=response)


def test_result_query_uses_browser_headers_without_json_content_type(bcut):
    _, transcriber, _ = bcut
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"code": 0, "data": RESULT}).encode()
        return response
    transcriber.session = types.SimpleNamespace(get=get)
    transcriber.task_id = "existing-task"
    assert transcriber._query_result()["state"] == 4
    request = calls[0]
    assert request["params"]["model_id"] == "8"
    assert request["headers"]["Referer"] == "https://member.bilibili.com/"
    assert "application/json" in request["headers"]["Accept"]
    assert "Content-Type" not in request["headers"]
    assert request["timeout"] > 0


def prepare_task(monkeypatch, transcriber):
    calls = {"upload": 0, "create": 0}
    def upload(_file):
        calls["upload"] += 1
    def create():
        calls["create"] += 1
        transcriber.task_id = "existing-task"
        return transcriber.task_id
    monkeypatch.setattr(transcriber, "_upload", upload)
    monkeypatch.setattr(transcriber, "_create_task", create)
    return calls


def test_transient_rejection_retries_same_task_with_backoff(bcut, monkeypatch):
    module, transcriber, audio = bcut
    calls = prepare_task(monkeypatch, transcriber)
    replies = iter([http_error(412), {"state": 2}, RESULT])
    def query():
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(transcriber, "_query_result", query)
    delays = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    transcript = transcriber.transcript(str(audio))
    assert transcript.full_text == "测试语音"
    assert delays == [20, 5]
    assert calls == {"upload": 1, "create": 1}


def test_repeated_412_is_bounded_and_next_attempt_reuses_remote_task(bcut, monkeypatch):
    module, transcriber, audio = bcut
    calls = prepare_task(monkeypatch, transcriber)
    monkeypatch.setattr(transcriber, "_query_result", lambda: (_ for _ in ()).throw(http_error(412)))
    delays = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    with pytest.raises(ValueError, match="HTTP 412"):
        transcriber.transcript(str(audio))
    assert delays == [20, 40]
    monkeypatch.setattr(transcriber, "_query_result", lambda: RESULT)
    transcript = transcriber.transcript(str(audio))
    assert transcript.full_text == "测试语音"
    assert calls == {"upload": 1, "create": 1}


def test_service_task_failure_is_not_retried(bcut, monkeypatch):
    module, transcriber, audio = bcut
    prepare_task(monkeypatch, transcriber)
    monkeypatch.setattr(transcriber, "_query_result", lambda: {"state": 3})
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: pytest.fail("failed ASR task was retried"))
    with pytest.raises(Exception, match="状态码: 3"):
        transcriber.transcript(str(audio))


def test_failed_remote_task_does_not_trap_future_retries(bcut, monkeypatch):
    _, transcriber, audio = bcut
    calls = prepare_task(monkeypatch, transcriber)
    monkeypatch.setattr(transcriber, "_query_result", lambda: {"state": 3})
    with pytest.raises(ValueError, match="状态码: 3"):
        transcriber.transcript(str(audio))
    monkeypatch.setattr(transcriber, "_query_result", lambda: RESULT)
    assert transcriber.transcript(str(audio)).full_text == "测试语音"
    assert calls == {"upload": 2, "create": 2}


@pytest.mark.parametrize("saved", [None, [], {"task_id": ["invalid"]}])
def test_invalid_sidecar_is_a_cache_miss(bcut, monkeypatch, saved):
    _, transcriber, audio = bcut
    stat = audio.stat()
    if isinstance(saved, dict):
        saved.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns, model_id="8")
    transcriber._task_cache(audio).write_text(json.dumps(saved), encoding="utf-8")
    calls = prepare_task(monkeypatch, transcriber)
    monkeypatch.setattr(transcriber, "_query_result", lambda: RESULT)
    assert transcriber.transcript(str(audio)).full_text == "测试语音"
    assert calls == {"upload": 1, "create": 1}


@pytest.mark.parametrize("status", [404, 410])
def test_expired_http_task_is_cleared_for_next_attempt(bcut, monkeypatch, status):
    _, transcriber, audio = bcut
    calls = prepare_task(monkeypatch, transcriber)
    monkeypatch.setattr(transcriber, "_query_result", lambda: (_ for _ in ()).throw(http_error(status)))
    with pytest.raises(ValueError, match=f"HTTP {status}"):
        transcriber.transcript(str(audio))
    monkeypatch.setattr(transcriber, "_query_result", lambda: RESULT)
    assert transcriber.transcript(str(audio)).full_text == "测试语音"
    assert calls == {"upload": 2, "create": 2}


@pytest.mark.parametrize("message,cleared", [("任务不存在", True), ("task expired", True), ("系统繁忙", False)])
def test_only_explicit_invalid_task_business_errors_clear_cache(bcut, monkeypatch, message, cleared):
    _, transcriber, audio = bcut
    calls = prepare_task(monkeypatch, transcriber)
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({"code": -1, "message": message}).encode()
    transcriber.session = types.SimpleNamespace(get=lambda *args, **kwargs: response)
    with pytest.raises(Exception, match=message):
        transcriber.transcript(str(audio))
    monkeypatch.setattr(transcriber, "_query_result", lambda: RESULT)
    assert transcriber.transcript(str(audio)).full_text == "测试语音"
    expected = 2 if cleared else 1
    assert calls == {"upload": expected, "create": expected}
