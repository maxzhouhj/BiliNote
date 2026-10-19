import datetime
import json
import os
import logging
import subprocess
import tempfile
import re
from typing import Union, Optional
from urllib.parse import quote, urlencode

import httpx
import requests
from pydantic import BaseModel

from app.downloaders.base import Downloader
from app.downloaders.douyin_helper.abogus import ABogus
from app.enmus.note_enums import DownloadQuality
from app.models.audio_model import AudioDownloadResult
from app.services.cookie_manager import CookieConfigManager
from app.utils.path_helper import get_data_dir
from dotenv import load_dotenv

load_dotenv()
DOUYIN_DOMAIN = "https://www.douyin.com"

logger = logging.getLogger(__name__)
cfm = CookieConfigManager()
def get_timestamp(unit: str = "milli"):
    """
    根据给定的单位获取当前时间 (Get the current time based on the given unit)

    Args:
        unit (str): 时间单位，可以是 "milli"、"sec"、"min" 等
            (The time unit, which can be "milli", "sec", "min", etc.)

    Returns:
        int: 根据给定单位的当前时间 (The current time based on the given unit)
    """

    now = datetime.datetime.utcnow() - datetime.datetime(1970, 1, 1)
    if unit == "milli":
        return int(now.total_seconds() * 1000)
    elif unit == "sec":
        return int(now.total_seconds())
    elif unit == "min":
        return int(now.total_seconds() / 60)
    else:
        raise ValueError("Unsupported time unit")


class DouyinConfig:
    HEADERS = {
        "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36",
        "Referer": "https://www.douyin.com/",
        "Accept": "application/json, text/plain, */*",
        "x-tt-argus": "1",
        "Cookie": None
    }

    PROXIES = {
        "http": None,
        "https": None,
    }

    MS_TOKEN = {
        "url": "https://mssdk.bytedance.com/web/report",
        "magic": 538969122,
        "version": 1,
        "dataType": 8,
        "strData": "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",
        "User-Agent": "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36 Edg/117.0.2045.47"
    }

    TTWID = {
        "url": "https://ttwid.bytedance.com/ttwid/union/register/",
        "data": '{"region":"cn","aid":1768,"needFid":false,"service":"www.ixigua.com","migrate_info":{"ticket":"","source":"node"},"cbUrlProtocol":"https","union":true}'
    }


class BaseRequestModel(BaseModel):
    device_platform: str = "webapp"
    aid: str = "6383"
    channel: str = "channel_pc_web"
    pc_client_type: int = 1
    version_code: str = "290100"
    version_name: str = "29.1.0"
    cookie_enabled: str = "true"
    screen_width: int = 1920
    screen_height: int = 1080
    browser_language: str = "zh-CN"
    browser_platform: str = "Win32"
    browser_name: str = "Chrome"
    browser_version: str = "130.0.0.0"
    browser_online: str = "true"
    engine_name: str = "Blink"
    engine_version: str = "130.0.0.0"
    os_name: str = "Windows"
    os_version: str = "10"
    cpu_core_num: int = 12
    device_memory: int = 8
    platform: str = "PC"
    downlink: str = "10"
    effective_type: str = "4g"
    from_user_page: str = "1"
    locate_query: str = "false"
    need_time_list: str = "1"
    pc_libra_divert: str = "Windows"
    publish_video_strategy_type: str = "2"
    round_trip_time: str = "0"
    show_live_replay_strategy: str = "1"
    time_list_query: str = "0"
    whale_cut_token: str = ""
    update_version_code: str = "170400"
    msToken: str = None


class DouyinDownloader(Downloader):
    def __init__(self, cookie=None):
        super().__init__()
        self.headers_config = DouyinConfig.HEADERS.copy()
        self.cookie = cookie
        self.headers_config["Cookie"] = cookie if cookie is not None else cfm.get("douyin")
        self.proxies_config = DouyinConfig.PROXIES.copy()
        self.ttwid_config = DouyinConfig.TTWID.copy()
        self.ms_token_config = DouyinConfig.MS_TOKEN.copy()

    def _request_headers(self):
        headers = self.headers_config.copy()
        cookie = self.cookie if self.cookie is not None else cfm.get("douyin")
        if cookie:
            headers["Cookie"] = cookie
        else:
            headers.pop("Cookie", None)
        return headers

    @staticmethod
    def find_url(string: str) -> list:
        return re.findall(r"https?://[^\s<>\"]+", string)

    def extract_video_id(self, url: str) -> str:
        def match_id(value):
            match = re.search(r"(?:/video/|aweme_id=)(\d+)", value)
            return match.group(1) if match else ""

        if match_id(url):
            return match_id(url)
        urls = self.find_url(url)
        if not urls:
            return ""
        try:
            response = requests.head(urls[0], allow_redirects=True, timeout=20)
            return match_id(response.url)
        except requests.RequestException:
            return ""

    def gen_real_msToken(self) -> str:
        try:
            payload = json.dumps(
                {
                    "magic": self.ms_token_config["magic"],
                    "version": self.ms_token_config["version"],
                    "dataType": self.ms_token_config["dataType"],
                    "strData": self.ms_token_config["strData"],
                    "tspFromClient": get_timestamp(),
                }
            )
            headers = {
                "User-Agent": self.headers_config["User-Agent"],
                "Content-Type": "application/json",
            }
            transport = httpx.HTTPTransport(retries=5)
            with httpx.Client(transport=transport) as client:
                try:
                    response = client.post(
                        self.ms_token_config["url"], content=payload, headers=headers
                    )
                    response.raise_for_status()

                    msToken = str(httpx.Cookies(response.cookies).get("msToken"))
                    if len(msToken) not in [120, 128]:
                        raise ValueError("响应内容：{0}， Douyin msToken API 的响应内容不符合要求。".format(msToken))

                    return msToken
                except Exception as e:
                    raise ValueError("Douyin msToken API 请求失败：{0}".format(e))
        except Exception as e:
            raise ValueError("Douyin msToken API{0}".format(e))

    def fetch_video_info(self, video_url: str) -> dict:
        aweme_id = self.extract_video_id(video_url)
        if not aweme_id:
            raise ValueError("无法提取抖音视频 ID，请使用有效的单个视频链接")
        headers = self._request_headers()
        params = BaseRequestModel().model_dump()
        params["msToken"] = self.gen_real_msToken()
        params["aweme_id"] = aweme_id
        signature = quote(ABogus().get_value(params), safe="")
        url = f"{DOUYIN_DOMAIN}/aweme/v1/web/aweme/detail/?{urlencode(params)}&a_bogus={signature}"
        try:
            response = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException:
            raise ValueError("抖音详情请求失败：网络连接异常或请求超时") from None
        logger.info("抖音详情响应：video_id=%s HTTP=%s content_type=%s bytes=%s",
                    aweme_id, response.status_code,
                    response.headers.get("Content-Type", "unknown"), len(response.content))
        if response.status_code >= 400:
            # Only include recognized gateway messages; never log arbitrary response bodies or signed URLs.
            reason = next((message for message in (
                "Uifid Not Found", "Signature Not Found") if message in response.text), "请求被拒绝")
            raise ValueError(f"抖音详情请求失败：HTTP {response.status_code}，{reason}；请检查最新 Cookie 和请求签名")
        if not response.content.strip():
            raise ValueError("抖音详情接口返回空响应，请检查 Cookie 或稍后重试")
        try:
            data = response.json()
        except ValueError:
            raise ValueError("抖音详情接口返回非 JSON 内容，可能触发验证或接口已变更") from None
        if not isinstance(data, dict):
            raise ValueError("抖音详情接口返回的数据格式无效")
        if not isinstance(data.get("aweme_detail"), dict) or not data["aweme_detail"]:
            filter_detail = data.get("filter_detail") or {}
            reason = filter_detail.get("filter_reason", "作品不可见或已失效") if isinstance(filter_detail, dict) else "作品不可见或已失效"
            raise ValueError(f"无法获取抖音作品：{reason}")
        return data

    @staticmethod
    def _media_url(video: dict) -> str:
        for key in ("play_addr_h264", "play_addr", "download_addr"):
            urls = (video.get(key) or {}).get("url_list") or []
            if urls:
                return urls[0]
        raise ValueError("该抖音作品没有可下载的视频音轨，暂不支持图文或直播作品")

    def _save_video(self, detail: dict, output_dir: str) -> str:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{detail['aweme_id']}.mp4")
        if os.path.isfile(path) and os.path.getsize(path):
            return path
        url = self._media_url(detail.get("video") or {})
        # Media CDNs do not need the account Cookie from www.douyin.com.
        headers = {key: self.headers_config[key] for key in ("User-Agent", "Referer")}
        temp_path = None
        try:
            with requests.get(url, headers=headers, stream=True, timeout=60) as response:
                if response.status_code >= 400:
                    raise ValueError(f"抖音视频下载失败：HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    raise ValueError("抖音视频下载返回了验证页面，请刷新 Cookie 后重试")
                with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".part", delete=False) as stream:
                    temp_path = stream.name
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        if chunk:
                            stream.write(chunk)
            if not os.path.getsize(temp_path):
                raise ValueError("抖音视频下载返回空文件")
            os.replace(temp_path, path)
        except requests.RequestException:
            raise ValueError("抖音视频下载失败：网络连接异常或请求超时") from None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
        return path

    def download(self, video_url: str, output_dir: Union[str, None] = None,
                 quality: DownloadQuality = "fast", need_video: Optional[bool] = False,
                 skip_download: bool = False) -> AudioDownloadResult:
        output_dir = output_dir or get_data_dir() or self.cache_data
        detail = self.fetch_video_info(video_url)["aweme_detail"]
        video = detail.get("video") or {}
        video_path = None
        audio_path = os.path.join(output_dir, f"{detail['aweme_id']}.mp3")
        if not skip_download:
            video_path = self._save_video(detail, output_dir)
            if not os.path.isfile(audio_path) or not os.path.getsize(audio_path):
                bitrate = {"fast": "64k", "medium": "128k", "slow": "192k"}.get(quality, "128k")
                with tempfile.NamedTemporaryFile(dir=output_dir, suffix=".mp3", delete=False) as temporary:
                    temporary_audio = temporary.name
                try:
                    subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", video_path,
                                    "-vn", "-acodec", "libmp3lame", "-b:a", bitrate, temporary_audio],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if not os.path.getsize(temporary_audio):
                        raise ValueError("抖音视频音轨提取返回空文件")
                    os.replace(temporary_audio, audio_path)
                except (OSError, subprocess.CalledProcessError):
                    raise ValueError("抖音视频音轨提取失败，请确认 FFmpeg 已安装且视频包含音轨") from None
                finally:
                    if os.path.exists(temporary_audio):
                        os.unlink(temporary_audio)
        cover_url = None
        for key in ("cover", "origin_cover", "cover_original_scale", "dynamic_cover"):
            urls = (video.get(key) or {}).get("url_list") or []
            if urls:
                cover_url = urls[0]
                break
        tags = [tag.get("tag_name", "") for tag in detail.get("video_tag", [])]
        return AudioDownloadResult(
            file_path=audio_path, title=detail.get("item_title") or detail.get("desc") or detail["aweme_id"],
            duration=(video.get("duration") or 0) / 1000, cover_url=cover_url,
            platform="douyin", video_id=detail["aweme_id"],
            raw_info={"tags": " ".join([detail.get("desc", "")] + tags)},
            video_path=video_path if need_video else None,
        )

    def download_video(self, video_url: str, output_dir: Union[str, None] = None) -> str:
        detail = self.fetch_video_info(video_url)["aweme_detail"]
        return self._save_video(detail, output_dir or get_data_dir() or self.cache_data)
