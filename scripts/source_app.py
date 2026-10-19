"""Run the source backend and built frontend as a local desktop application."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "source-runtime"
STATE = RUNTIME / "application.json"
APP_ID = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:20]
for environment in (".venv-source", ".venv-douyin-debug", ".venv"):
    packages = ROOT / environment / "Lib/site-packages"
    if packages.is_dir():
        sys.path.insert(0, str(packages))
        break

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


class SourceFrontend(StaticFiles):
    """SPA fallback for pages, preserving API and asset 404 responses."""
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404:
                raise
            first = path.replace("\\", "/").lstrip("/").split("/", 1)[0]
            if first in {"api", "static", "uploads", "assets", "__source_app__"} or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def bind_loopback_socket(preferred):
    for port in range(preferred, min(preferred + 30, 65536)):
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", port))
            listener.listen(128)
            return listener
        except OSError:
            listener.close()
    raise RuntimeError("本地启动端口均被占用，请停止旧的源码版实例后重试")


def request_json(url, data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-BiliNote-Token"] = token
    request = urllib.request.Request(url, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        return json.load(response)


def running_state():
    try:
        state = json.loads(STATE.read_text(encoding="utf-8"))
        port = int(state["port"])
        if not 1 <= port <= 65535 or state.get("app_id") != APP_ID:
            return None
        health = request_json(f"http://127.0.0.1:{port}/__source_app__/health")
        return state if health.get("app_id") == APP_ID else None
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return None


def prepare_runtime(installed=None):
    for folder in ("config", "logs", "note_results", "static/screenshots", "uploads", "media"):
        (RUNTIME / folder).mkdir(parents=True, exist_ok=True)
    if installed:
        installed = Path(installed)
        for config in (installed / "config").glob("*.json"):
            target = RUNTIME / "config" / config.name
            if not target.exists():
                shutil.copyfile(config, target)
        database = installed / "bili_note.db"
        target = RUNTIME / "bili_note.db"
        if database.exists() and not target.exists():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
                with sqlite3.connect(target) as destination:
                    source.backup(destination)
    downloader = RUNTIME / "config/downloader.json"
    latest_cookie = ROOT / "data/douyin-cookie.txt"
    if not downloader.exists() and latest_cookie.exists():
        downloader.write_text(json.dumps({"douyin": {"cookie": latest_cookie.read_text(
            encoding="utf-8-sig").strip()}}, ensure_ascii=False), encoding="utf-8")
    transcriber = RUNTIME / "config/transcriber.json"
    if not transcriber.exists():
        transcriber.write_text('{"transcriber_type":"bcut"}', encoding="utf-8")


def configure_ffmpeg():
    if shutil.which("ffmpeg"):
        return
    import imageio_ffmpeg
    folder = RUNTIME / "bin"
    folder.mkdir(exist_ok=True)
    target = folder / "ffmpeg.exe"
    if not target.exists():
        shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(), target)
    os.environ["FFMPEG_BIN_PATH"] = str(folder)
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def serve(preferred):
    import uvicorn
    from fastapi import BackgroundTasks, Header
    prepare_runtime()
    frontend = ROOT / "BillNote_frontend/dist"
    if not (frontend / "index.html").exists():
        raise RuntimeError("前端构建文件不存在，请先运行 scripts/build_source_app.ps1")
    listener = bind_loopback_socket(preferred)
    port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    os.chdir(RUNTIME)
    os.environ.update({
        "BACKEND_HOST": "127.0.0.1", "BACKEND_PORT": str(port),
        "DATABASE_URL": "sqlite:///bili_note.db", "NOTE_OUTPUT_DIR": str(RUNTIME / "note_results"),
        "OUT_DIR": str(RUNTIME / "static/screenshots"), "STATIC": "/static",
        "DATA_DIR": str(RUNTIME / "media"),
        "APP_DATA_DIR": str(RUNTIME / "work"),
        "IMAGE_BASE_URL": "/static/screenshots", "API_BASE_URL": "http://127.0.0.1",
    })
    configure_ffmpeg()
    sys.path.insert(0, str(ROOT / "backend"))
    from main import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))

    @app.get("/__source_app__/health")
    def application_health():
        return {"app_id": APP_ID, "port": port, "kind": "bilinote-source"}

    @app.post("/__source_app__/stop")
    def stop_application(background_tasks: BackgroundTasks, x_bilinote_token: str = Header(default="")):
        if not secrets.compare_digest(x_bilinote_token, token):
            raise HTTPException(403, "无效的停止请求")
        background_tasks.add_task(setattr, server, "should_exit", True)
        return {"stopping": True}

    app.mount("/", SourceFrontend(directory=str(frontend), html=True), name="source-frontend")
    STATE.write_text(json.dumps({"app_id": APP_ID, "port": port, "pid": os.getpid(), "token": token}), encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()
        try:
            if json.loads(STATE.read_text(encoding="utf-8")).get("token") == token:
                STATE.unlink()
        except (OSError, ValueError):
            pass


def open_window(port):
    url = f"http://127.0.0.1:{port}/"
    candidates = [Path(os.getenv(base, "")) / relative for base, relative in (
        ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
    )]
    browser = next((path for path in candidates if path.is_file()), None)
    if browser:
        subprocess.Popen([str(browser), f"--app={url}", "--no-first-run",
                          "--window-size=1600,1000",
                          f"--user-data-dir={RUNTIME / 'browser-profile'}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


@contextmanager
def startup_lock():
    """Serialize double-clicks while the worker is still starting."""
    prepare_runtime()
    with (RUNTIME / "startup.lock").open("a+b") as stream:
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        if os.name == "nt":
            import msvcrt
            for _ in range(120):
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                raise RuntimeError("另一个启动操作尚未完成，请查看后台日志")
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


def launch(port, no_open=False):
    with startup_lock():
        state = ensure_backend(port)
    if not no_open:
        open_window(state["port"])
    print(f"BiliNote 源码版已启动：http://127.0.0.1:{state['port']}/")


def ensure_backend(port):
    prepare_runtime()
    state = running_state()
    if not state:
        logfile = (RUNTIME / "logs/backend-console.log").open("a", encoding="utf-8")
        child = subprocess.Popen([sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                                  "--serve", "--port", str(port)], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        logfile.close()
        for _ in range(120):
            state = running_state()
            if state:
                break
            if child.poll() is not None:
                raise RuntimeError("源码后端启动失败，详情见 source-runtime/logs/backend-console.log")
            time.sleep(0.5)
        else:
            child.terminate()
            raise RuntimeError("源码后端启动超时，详情见 source-runtime/logs/backend-console.log")
    return state


def main():
    parser = argparse.ArgumentParser(description="BiliNote 源码一键应用")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", type=int, default=8485)
    parser.add_argument("--import-installed", type=Path)
    args = parser.parse_args()
    if args.import_installed:
        prepare_runtime(args.import_installed)
    if args.stop:
        state = running_state()
        if state:
            request_json(f"http://127.0.0.1:{state['port']}/__source_app__/stop", data=b"{}", token=state["token"])
        print("BiliNote 源码后端已停止")
    elif args.serve:
        serve(args.port)
    else:
        launch(args.port, args.no_open)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        import traceback
        with (RUNTIME / "startup-error.log").open("a", encoding="utf-8") as stream:
            traceback.print_exc(file=stream)
        if os.name == "nt" and "--serve" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                f"启动失败，请查看日志：{RUNTIME / 'startup-error.log'}", "BiliNote 源码版", 16)
        raise
