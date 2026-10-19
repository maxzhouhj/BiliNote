# 源码一键应用

双击项目根目录的 `Start BiliNote.vbs`。后台就绪后自动打开 Edge/Chrome 独立窗口；系统无这些浏览器时使用默认浏览器。
双击 `Stop BiliNote.vbs` 停止后台。仅关闭界面会保留后台，方便再次打开。
发生启动错误时，用 `Start BiliNote.cmd` 查看错误，或检查 `source-runtime/logs/backend-console.log`。

默认端口为 8485，冲突时自动选择后续端口；前端与 API 共用一个本地端口。
内置 FFmpeg；系统没有 ffprobe 时使用已包含的 PyAV 读取视频时长，支持截图和视频理解的取帧流程。
重复打开会复用同一项目已经运行的后台。配置、数据库、日志、截图和任务结果在 `source-runtime`。
视频和音频缓存存储在 `source-runtime/media`。此版本复用本机 Python 3.12；运行不需要 Node 或 Rust。

首次迁移旧版后端设置（只复制，目标已有配置时不覆盖）：

```powershell
python -X utf8 scripts/source_app.py --import-installed "D:\Program Files\BiliNote"
```

复制模型供应商、模型和转写器等后端配置，不迁移旧界面的浏览器本地历史或模型选择状态。
如果已迁移配置，启动后的模型下拉框可直接选择已有模型。Cookie 在设置页正常保存后即时用于后续请求。

修改前端后重新打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_source_app.ps1
```

产物在 `releases/BiliNote-Source-<时间>` 及同名 ZIP。ZIP 不包含 Cookie、API Key、个人数据库或历史任务；
它面向当前电脑的 Python 环境，不是跨电脑的完整独立安装包。保留整个解压目录即可双击启动。

抖音修复包括 `x-tt-argus` 请求头、HTTP/JSON 错误提示、每次请求读取新 Cookie，以及下载视频后提取实际音轨。
