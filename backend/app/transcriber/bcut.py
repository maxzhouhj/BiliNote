import json
import logging
from pathlib import Path
import threading
import time
from typing import Optional, List, Dict, Union

import requests

from app.decorators.timeit import timeit
from app.models.transcriber_model import TranscriptSegment, TranscriptResult
from app.transcriber.base import Transcriber
from app.utils.logger import get_logger
from events import transcription_finished

__version__ = "0.0.3"

API_BASE_URL = "https://member.bilibili.com/x/bcut/rubick-interface"

# 申请上传
API_REQ_UPLOAD = API_BASE_URL + "/resource/create"

# 提交上传
API_COMMIT_UPLOAD = API_BASE_URL + "/resource/create/complete"

# 创建任务
API_CREATE_TASK = API_BASE_URL + "/task"

# 查询结果
API_QUERY_RESULT = API_BASE_URL + "/task/result"

logger = get_logger(__name__)


class _BcutTaskFailed(ValueError):
    """The remote task failed or is no longer available for resume."""


class BcutTranscriber(Transcriber):
    """必剪 语音识别接口"""
    MODEL_ID = "8"
    POLL_INTERVAL_SECONDS = 5
    MAX_QUERY_ERRORS = 3
    MAX_WAIT_SECONDS = 1800
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Referer': 'https://member.bilibili.com/',
        'Accept': 'application/json, text/plain, */*',
    }

    def __init__(self):
        self.session = requests.Session()
        self.task_id = None
        self.__etags = []
        # 实例被 transcriber_provider 缓存复用，并发任务会交错读写上传会话状态
        # （etags/upload_id/task_id），必须整段串行化
        # ponytail: 全局实例锁，并发转写会排队；如需吞吐量应改为每任务独立实例
        self._lock = threading.Lock()

        self.__in_boss_key: Optional[str] = None
        self.__resource_id: Optional[str] = None
        self.__upload_id: Optional[str] = None
        self.__upload_urls: List[str] = []
        self.__per_size: Optional[int] = None
        self.__clips: Optional[int] = None

        self.__etags: List[str] = []
        self.__download_url: Optional[str] = None
        self.task_id: Optional[str] = None

    @property
    def json_headers(self):
        # GET queries must use the tested browser header set without this POST-only header.
        return {**self.headers, "Content-Type": "application/json"}

    def _task_cache(self, file_path):
        return Path(str(file_path) + ".bcut-task.json")

    def _saved_task(self, file_path):
        try:
            stat = Path(file_path).stat()
            saved = json.loads(self._task_cache(file_path).read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                return None
            if (saved.get("size") == stat.st_size and saved.get("mtime_ns") == stat.st_mtime_ns
                    and saved.get("model_id") == self.MODEL_ID
                    and isinstance(saved.get("task_id"), str) and saved["task_id"].strip()):
                return saved["task_id"]
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _save_task(self, file_path):
        temporary = self._task_cache(file_path).with_suffix(".json.tmp")
        try:
            stat = Path(file_path).stat()
            temporary.write_text(json.dumps({
                "task_id": self.task_id, "model_id": self.MODEL_ID,
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            }), encoding="utf-8")
            temporary.replace(self._task_cache(file_path))
        except OSError:
            logger.warning("必剪任务续查信息未能保存；本次查询继续执行")
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        
    def _load_file(self, file_path: str) -> bytes:
        """读取文件内容"""
        with open(file_path, 'rb') as f:
            return f.read()

    def _upload(self, file_path: str) -> None:
        """申请上传"""
        # 实例被 transcriber_provider 缓存复用，必须清掉上一次上传的会话状态，
        # 否则 __etags 跨上传残留，提交的 etag 数与本次分片数不符，B 站会拒绝合并
        self.__etags = []
        self.__in_boss_key = None
        self.__resource_id = None
        self.__upload_id = None
        self.__upload_urls = []
        self.__download_url = None

        file_binary = self._load_file(file_path)
        if not file_binary:
            raise ValueError("无法读取文件数据")
            
        payload = json.dumps({
            "type": 2,
            "name": "audio.mp3",
            "size": len(file_binary),
            "ResourceFileType": "mp3",
            "model_id": self.MODEL_ID,
        })

        resp = self.session.post(
            API_REQ_UPLOAD,
            data=payload,
            headers=self.json_headers,
            timeout=30,
        )
        resp.raise_for_status()
        resp = resp.json()
        resp_data = resp["data"]

        self.__in_boss_key = resp_data["in_boss_key"]
        self.__resource_id = resp_data["resource_id"]
        self.__upload_id = resp_data["upload_id"]
        self.__upload_urls = resp_data["upload_urls"]
        self.__per_size = resp_data["per_size"]
        self.__clips = len(resp_data["upload_urls"])

        logger.info(
            f"申请上传成功, 总计大小{resp_data['size'] // 1024}KB, {self.__clips}分片, 分片大小{resp_data['per_size'] // 1024}KB"
        )
        self.__upload_part(file_binary)
        self.__commit_upload()

    def __upload_part(self, file_binary: bytes) -> None:
        """上传音频数据"""
        for clip in range(self.__clips):
            start_range = clip * self.__per_size
            end_range = min((clip + 1) * self.__per_size, len(file_binary))
            logger.info(f"开始上传分片{clip}: {start_range}-{end_range}")
            resp = self.session.put(
                self.__upload_urls[clip],
                data=file_binary[start_range:end_range],
                headers={'Content-Type': 'application/octet-stream'},
                timeout=120,
            )
            resp.raise_for_status()
            etag = resp.headers.get("Etag", "").strip('"')
            self.__etags.append(etag)
            logger.info(f"分片{clip}上传成功")

    def __commit_upload(self) -> None:
        """提交上传数据"""
        data = json.dumps({
            "InBossKey": self.__in_boss_key,
            "ResourceId": self.__resource_id,
            "Etags": ",".join(self.__etags),
            "UploadId": self.__upload_id,
            "model_id": self.MODEL_ID,
        })
        resp = self.session.post(
            API_COMMIT_UPLOAD,
            data=data,
            headers=self.json_headers,
            timeout=30,
        )
        resp.raise_for_status()
        resp = resp.json()
        if resp.get("code") != 0:
            error_msg = f"上传提交失败: {resp.get('message', '未知错误')}"
            logger.error(error_msg)
            raise Exception(error_msg)
            
        self.__download_url = resp["data"]["download_url"]
        logger.info("音频上传已提交")

    def _create_task(self) -> str:
        """开始创建转换任务"""
        resp = self.session.post(
            API_CREATE_TASK, json={"resource": self.__download_url, "model_id": self.MODEL_ID},
            headers=self.json_headers, timeout=30,
        )
        resp.raise_for_status()
        resp = resp.json()
        if resp.get("code") != 0:
            error_msg = f"创建任务失败: {resp.get('message', '未知错误')}"
            logger.error(error_msg)
            raise Exception(error_msg)
            
        self.task_id = resp["data"]["task_id"]
        logger.info(f"任务已创建: {self.task_id}")
        return self.task_id

    def _query_result(self) -> dict:
        """查询转换结果"""
        resp = self.session.get(
            API_QUERY_RESULT, 
            params={"model_id": self.MODEL_ID, "task_id": self.task_id},
            headers=self.headers,
            timeout=20,
        )
        if resp.status_code >= 400:
            logger.warning("必剪查询响应：HTTP %s，Content-Type=%s", resp.status_code,
                           resp.headers.get("Content-Type", "unknown"))
        resp.raise_for_status()
        try:
            resp = resp.json()
        except ValueError:
            raise ValueError("必剪查询返回非 JSON 内容，可能触发服务端验证") from None
        if resp.get("code") != 0:
            error_msg = f"查询结果失败: {resp.get('message', '未知错误')}"
            logger.error(error_msg)
            message = str(resp.get("message", "")).lower()
            if any(marker in message for marker in (
                    "任务不存在", "任务已过期", "任务过期", "任务已失效",
                    "task not found", "task does not exist", "task expired", "task has expired")):
                raise _BcutTaskFailed(error_msg)
            raise Exception(error_msg)
            
        return resp["data"]

    def _wait_for_result(self):
        errors = 0
        deadline = time.monotonic() + self.MAX_WAIT_SECONDS
        for attempt in range(360):
            if time.monotonic() >= deadline:
                break
            try:
                result = self._query_result()
                errors = 0
            except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as error:
                status = error.response.status_code if isinstance(error, requests.HTTPError) and error.response is not None else None
                if status in {404, 410}:
                    raise _BcutTaskFailed(f"必剪查询失败：HTTP {status}，已清除失效任务，重试时将重新创建") from None
                if status is not None and status not in {412, 429, 500, 502, 503, 504}:
                    raise ValueError(f"必剪查询失败：HTTP {status}，远端任务已保存，可稍后重试") from None
                errors += 1
                reason = f"HTTP {status}" if status is not None else "网络连接异常或超时"
                if errors >= self.MAX_QUERY_ERRORS:
                    raise ValueError(f"必剪查询暂时不可用：{reason}，已停止连续重试；任务已保存，请稍后重试或切换转写器") from None
                delay = min(20 * 2 ** (errors - 1), 60, max(0, deadline - time.monotonic()))
                logger.warning("必剪结果查询遇到 %s，%s 秒后续查同一任务", reason, delay)
                time.sleep(delay)
                continue
            if result["state"] == 4:
                return result
            if result["state"] == 3:
                raise _BcutTaskFailed("B站ASR任务失败，状态码: 3")
            if attempt % 12 == 0:
                logger.info("必剪转录进行中，等待远端任务完成...")
            time.sleep(min(self.POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
        raise ValueError("必剪转写等待超时，任务已保存，可稍后重试续查")

    @timeit
    def transcript(self, file_path: str) -> TranscriptResult:
        """执行识别过程，符合 Transcriber 接口"""
        with self._lock:
            return self._transcript_locked(file_path)

    def _transcript_locked(self, file_path: str) -> TranscriptResult:
        try:
            logger.info(f"开始处理文件: {file_path}")
            
            saved_task = self._saved_task(file_path)
            if saved_task:
                self.task_id = saved_task
                logger.info("复用已保存的必剪远端任务，跳过音频上传")
            else:
                logger.info("正在上传文件...")
                self._upload(file_path)
                logger.info("提交转录任务...")
                self._create_task()
                self._save_task(file_path)
            
            # 轮询检查任务状态
            logger.info("等待转录结果...")
            try:
                task_resp = self._wait_for_result()
            except _BcutTaskFailed:
                try:
                    self._task_cache(file_path).unlink(missing_ok=True)
                except OSError:
                    logger.warning("无法清理已失败的必剪任务续查信息")
                raise
                
            # 解析结果
            logger.info("转录成功，处理结果...")
            result_json = json.loads(task_resp["result"])
            
            # 提取分段数据
            segments = []
            full_text = ""
            
            for u in result_json.get("utterances", []):
                text = u.get("transcript", "").strip()
                # B站ASR返回的时间戳是毫秒，需要转换为秒
                start_time = float(u.get("start_time", 0)) / 1000.0
                end_time = float(u.get("end_time", 0)) / 1000.0
                
                full_text += text + " "
                segments.append(TranscriptSegment(
                    start=start_time,
                    end=end_time,
                    text=text
                ))
            
            # 创建结果对象
            result = TranscriptResult(
                language=result_json.get("language", "zh"),
                full_text=full_text.strip(),
                segments=segments,
                raw=result_json
            )
            
            # 触发完成事件
            # self.on_finish(file_path, result)
            
            return result
            
        except Exception as e:
            logger.error(f"B站ASR处理失败: {str(e)}")
            raise

    def on_finish(self, video_path: str, result: TranscriptResult) -> None:
        """转录完成的回调"""
        logger.info(f"B站ASR转写完成: {video_path}")
        transcription_finished.send({
            "file_path": video_path,
        })
