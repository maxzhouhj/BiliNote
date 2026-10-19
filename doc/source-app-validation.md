# 源码一键版验收记录

验收日期：2026-10-19（北京时间）。适用范围：当前 Windows 电脑，复用本机 Python 3.12 及基础依赖。

## 已验证

- `pnpm install --frozen-lockfile` 安装成功，`pnpm build:source` 生产构建成功。
- 实际执行 `Start BiliNote.vbs`，后台自动启动并打开 Edge 独立应用窗口。
- 前端主页、设置页深链接、后端健康接口返回 200；不存在的 API 和资源保持 404。
- 同时执行两个启动命令，复用同一个后台进程；占用端口会被跳过。
- 缺少停止令牌的请求返回 403；正式停止入口可正常关闭后台。
- 原安装版的数据库及配置只复制、不覆盖已有目标配置。已有 8 个服务商和 1 个模型；最新 Cookie 与 TXT 一致。
- ZIP 含前端成品、后端源码、启动脚本及补充依赖，排除 Cookie、API Key、个人数据库和运行数据。
- 解包后的应用目录可独立启动，主页、深链接与 API 实测正常。

## 抖音真实链接

视频 ID：`7596231869977333033`。原始短链：`https://v.douyin.com/OeRUqxerfAM/`。

- 详情接口已返回 HTTP 200，并取得正确视频数据。
- 视频时长：7364.997 秒（约 122 分钟）。
- 下载的视频：744544560 字节；从视频实际音轨提取的 MP3：58920953 字节。
- 标题、时长、封面正常。后续用户原任务已完成远端转写，查询曾被 412 拦截；本次测试取回并恢复其转写缓存，详见 `bcut-412-diagnosis.md`。未发起付费模型总结。
- 系统无独立 ffprobe 时，PyAV 时长读取、实际两帧提取和拼图生成均成功。

## 测试

相关测试：29 项通过，覆盖抖音请求头、Cookie 热读取、HTTP/JSON 错误、真实视频音轨、音频缓存原子提交、
媒体/工作目录配置、SPA 路由、端口冲突、无 ffprobe 取帧，以及必剪请求头、有限退避、任务续查及失效任务恢复。

最新全量后端测试实际结果：105 passed、7 failed、8 subtests passed。详细日志位于项目 `source-runtime/bcut-investigation/pytest-full-after-bcut.txt`。
以下未修改模块/测试仍有问题，未宣称全量测试通过：

1. `test_task_serial_executor.py::TestTaskSerialExecutor::test_executor_runs_tasks_one_by_one`：测试要求串行，现有实现默认允许并发。
2. `test_whisper_config_forwarding.py` 的三个测试：隔离子进程未继承本次补充依赖目录，报缺少 sqlalchemy。
3. `test_ydl_retry_opts.py::RetryOptsValueTest` 的三个测试：整组运行时，其他测试留下的模块替身缺少 TranscriptResult。
   单独运行该文件时通过。

前端构建有既存的 Browserslist 数据时间、lottie eval 和大 chunk 警告，不阻止构建。
未迁移旧 Tauri 浏览器本地历史及已选模型状态；新窗口首次使用时选择已有模型即可。
