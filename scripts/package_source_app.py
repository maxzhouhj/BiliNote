"""Create an unpack-and-launch source bundle; never package user credentials."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build():
    if not (ROOT / "BillNote_frontend/dist/index.html").exists():
        raise RuntimeError("Build the frontend with pnpm build:source first")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = ROOT / "releases" / f"BiliNote-Source-{stamp}"
    output.mkdir(parents=True)
    for folder in ("app", "events", "fonts"):
        shutil.copytree(ROOT / "backend" / folder, output / "backend" / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "*.log"))
    for name in ("main.py", "ffmpeg_helper.py", "requirements.txt"):
        shutil.copyfile(ROOT / "backend" / name, output / "backend" / name)
    shutil.copytree(ROOT / "BillNote_frontend/dist", output / "BillNote_frontend/dist")
    (output / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/source_app.py", output / "scripts/source_app.py")
    (output / "doc").mkdir()
    for document in ("source-app.md", "source-app-validation.md", "bcut-412-diagnosis.md"):
        if (ROOT / "doc" / document).exists():
            shutil.copyfile(ROOT / "doc" / document, output / "doc" / document)
    for name in ("Start BiliNote.vbs", "Start BiliNote.cmd", "Stop BiliNote.vbs", "LICENSE"):
        shutil.copyfile(ROOT / name, output / name)
    packages = next((ROOT / name / "Lib/site-packages" for name in
                     (".venv-source", ".venv-douyin-debug", ".venv")
                     if (ROOT / name / "Lib/site-packages").is_dir()), None)
    if packages is None:
        raise RuntimeError("No local source runtime dependency directory found")
    shutil.copytree(packages, output / ".venv-source/Lib/site-packages",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "pip", "pip-*.dist-info"))
    readme = """BiliNote 源码一键启动版

双击 Start BiliNote.vbs 启动，自动打开独立窗口。
双击 Stop BiliNote.vbs 停止后台；关闭窗口本身不会停止后台。
启动异常时双击 Start BiliNote.cmd 查看报错。

这是为当前电脑准备的源码应用，复用本机已安装的 Python 3.12 及其基础依赖。
包含前端成品和补充的 Python 依赖，运行无需 Node / Rust / 旧版 BiliNote.exe。
请保留整个目录。启动脚本与 backend、scripts、BillNote_frontend、.venv-source 同级。

默认本地端口 8485，冲突时自动尝试后续端口。不会停止其他应用。
Cookie、模型配置、日志及任务存储在 source-runtime 目录。
本压缩包不包含 Cookie、API Key、模型配置或历史任务。
在设置页添加模型服务商、模型与抖音 Cookie，转写引擎默认必剪。
当前项目主目录的一键入口已另行迁移你的旧版后端设置，可直接使用。
"""
    (output / "使用说明.txt").write_text(readme, encoding="utf-8-sig")
    (output / "build-info.json").write_text(json.dumps({
        "built_at": datetime.now().isoformat(), "python": sys.version,
        "source_cookie_included": False, "user_runtime_included": False,
        "douyin_downloader_sha256": hashlib.sha256(
            (ROOT / "backend/app/downloaders/douyin_downloader.py").read_bytes()).hexdigest(),
        "bcut_transcriber_sha256": hashlib.sha256(
            (ROOT / "backend/app/transcriber/bcut.py").read_bytes()).hexdigest(),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = output.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as bundle:
        for path in output.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(output.parent))
    print(json.dumps({"directory": str(output), "archive": str(archive),
                      "archive_bytes": archive.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    build()
