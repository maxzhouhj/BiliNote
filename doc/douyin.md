# 抖音解析配置规范（2026-10 实测）

本文说明 BiliNote 抓取抖音作品时，**链接、Cookie、请求头、签名参数、响应**各需要满足什么条件，
以及为什么旧版会报 `('请求失败:', JSONDecodeError('Expecting value: line 1 column 1 (char 0)'))`。

---

## 0. 一句话结论

抖音网页端详情接口 `/aweme/v1/web/aweme/detail/` 现在由边缘网关的 **ArgusSecurityPlugin** 保护，
实测（2026-10）**只带 cookie + `a_bogus` 会被 403 拦截**。网关返回的是 `text/plain` 或空 body，
旧代码直接 `response.json()`，于是就得到 `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`。

要拿到数据，必须同时满足：

| 条件 | 说明 |
| --- | --- |
| 请求头 `x-tt-argus: 1` | 缺这个头 → `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 请求头 `uifid: <UIFID>` | 取值必须是 cookie 里的 `UIFID`；与 cookie 不同源 → `Signature Not Found` |
| query 参数 `uifid` / `verifyFp` / `fp` | 三者成套；`verifyFp` 与 `fp` 必须都等于 cookie 里的 `s_v_web_id` |
| `a_bogus` | 必须在以上参数全部确定**之后**，对最终 query 串签名 |
| 合法 Cookie | 标准 `name=value; name=value` 请求头格式，且包含 `UIFID`、`s_v_web_id` |

---

## 1. 原视频链接规范

**支持**

- `https://www.douyin.com/video/<19 位数字 aweme_id>`，可带多余 query
- `https://v.douyin.com/<短码>/`（短链，需要能正常重定向）
- 直接粘贴 19 位数字 ID
- 含链接的分享文案（会从文案里抽出第一个 URL 并解析短链）

**不支持 / 注意**

- 图文作品 `https://www.douyin.com/note/<id>`：没有音轨，本项目的目标产物是音频转写，不要提交
- 直播、用户主页、合辑/收藏页：不是单个作品，解析不出 `aweme_id`
- 短链过期或失效：拿不到真实 `aweme_id`，请回到 App「分享 → 复制链接」重新获取
- 提交给后端 `POST /api/generate_note` 的 `url` 字段必须是**合法 URL**（`pydantic.AnyUrl`），
  分享文案请只在客户端里抽取链接后再提交

`aweme_id` 是 `/video/` 后面那串 **19 位数字**，不是 `sec_uid`，不是短码。

---

## 2. Cookie 规范（最容易出错的一项）

### 2.1 格式

必须是浏览器请求头里 `Cookie:` 的原始值：

```text
ttwid=1%7C...; odin_tt=...; sessionid=...; s_v_web_id=verif...; UIFID=...; UIFID_TEMP=...; passport_csrf_token=...
```

**不能**是下面这些形式（旧配置里就踩过）：

| 错误形式 | 典型来源 | 处理 |
| --- | --- | --- |
| `[{"name": "ttwid", "value": "...", "domain": ...}, ...]` | 插件/DevTools 的「导出 JSON」 | 新实现会自动归一化成标准头 |
| `{"ttwid": "...", "sessionid": "..."}` | 手工整理 | 同上 |
| 带 `Cookie:` 前缀 | 复制整行请求头 | 新实现会去掉前缀 |
| 含换行 / 制表符 | 从文本编辑器粘贴 | 新实现会按 `;` 拼接 |

### 2.2 必需字段

| 字段 | 作用 | 缺失后果 |
| --- | --- | --- |
| `UIFID`（或 `UIFID_TEMP`） | Argus 设备指纹 | `403 Uifid Not Found` |
| `s_v_web_id` | verifyFp / fp 参数来源 | `403 Signature Not Found` |
| `ttwid`、`odin_tt` | 基础设备标识 | 大概率被风控 |
| `sessionid` / `sessionid_ss` | 登录态（部分作品、去水印地址需要） | 作品可能返回空或降级 |
| `passport_csrf_token` | 登录态完整性 | 同上 |
| `msToken` | 可选；有则优先使用 | 实测缺省仍能 200，但不保证长期 |

### 2.3 同源要求（重要）

`User-Agent`、Cookie、`UIFID`、`s_v_web_id` 必须来自**同一个浏览器会话**。
把 A 浏览器的 UIFID 和 B 浏览器的 `s_v_web_id` 拼在一起会被判 `Signature Not Found`；
自行编造 `verifyFp` 同理。

### 2.4 时效与安全

- UIFID / ttwid 会过期，403 时优先重新导出 cookie（一般几天内有效）
- Cookie 等价于账号凭据：不要提交进仓库、不要贴到 issue、不要留在日志里
  （旧实现 `print(self.headers_config)` 会把整个 cookie 打进日志，已移除）

---

## 3. 请求头规范

| 头 | 值 |
| --- | --- |
| `User-Agent` | 与 cookie 同源的桌面 Chrome UA，例如 `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36` |
| `Cookie` | 第 2 节的完整 Cookie 头 |
| `Referer` | `https://www.douyin.com/video/<aweme_id>`（或 `https://www.douyin.com/`） |
| `Accept` | `application/json, text/plain, */*` |
| `Accept-Language` | `zh-CN,zh;q=0.9` |
| `x-tt-argus` | `1`（网关当前不校验取值，不能缺） |
| `uifid` | cookie 里的 `UIFID` |

---

## 4. query 参数规范

除 `aweme_id` 外，还需要一组与浏览器一致的公共参数（缺了不一定 403，但建议全带）：

```text
device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1
version_code=190500&version_name=19.5.0&cookie_enabled=true
browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome
browser_version=130.0.0.0&browser_online=true
engine_name=Blink&engine_version=130.0.0.0&os_name=Windows&os_version=10
cpu_core_num=12&device_memory=8&platform=PC
screen_width=1920&screen_height=1080&downlink=10&effective_type=4g&round_trip_time=50
```

**风控三件套（缺一不可）**：

```text
uifid=<cookie UIFID>&verifyFp=<cookie s_v_web_id>&fp=<cookie s_v_web_id>
```

`msToken` 可选。最后再追加 `a_bogus`。

---

## 5. 签名顺序规范

1. 组装**最终** query 参数（公共参数 + `aweme_id` + `uifid`/`verifyFp`/`fp` + `msToken`）
2. 用 `ABogus().get_value(params)` 对这份参数生成 `a_bogus`
3. `urlencode(params) + "&a_bogus=" + quote(a_bogus, safe="")`

顺序反了（先签名后追加参数）、或签名参数与实际发出的参数不一致，都会被判签名无效。

---

## 6. 响应处理规范

接口有 4 种"看起来像成功"的返回，必须分别处理，**禁止直接 `response.json()`**：

| HTTP | body | 含义 | 正确做法 |
| --- | --- | --- | --- |
| 200 | `{"aweme_detail": {...}}` | 正常 | 解析作品字段 |
| 200 | `{"aweme_detail": null, "filter_detail": {"filter_reason": "..."}}` | 作品不可见 | 映射成可读中文提示 |
| 403 | `text/plain`: `Blocked by ArgusSecurityPlugin Uifid Not Found` | 缺 `UIFID` / 缺 `x-tt-argus` 头 | 提示重新导出 cookie |
| 403 | `text/plain`: `Blocked by ArgusSecurityPlugin Signature Not Found` | `verifyFp` 与 cookie 不同源 / 签名无效 | 提示 cookie 与 UA 同源 |
| 200/其它 | 空 body | 风控或接口变更 | 提示 cookie 失效 |
| 其它 | HTML / 纯文本 | 风控页 / 反爬页 | 提示非 JSON 响应 |

`filter_reason` 常见值：

| filter_reason | 含义 |
| --- | --- |
| `status_audit_not_pass` | 作品未通过审核或已下架 |
| `status_deleted` / `delete` | 作品已删除 |
| `music_deleted_video` | 背景音乐被下架导致作品不可见 |
| `private` / `is_private` | 仅作者可见 |
| `friend` | 仅好友可见 |
| `region` | 地区限制 |

---

## 7. 音视频字段规范

| 目标 | 正确字段 | 说明 |
| --- | --- | --- |
| 无音轨原视频 | `video.play_addr_h264.url_list[0]` → `video.play_addr.url_list[0]` | 无水印；`download_addr` 常带水印，仅兜底 |
| 抽音频 | 下载上面的视频 → `ffmpeg -vn -acodec libmp3lame` | 项目其它平台同样依赖 ffmpeg |
| ~~BGM~~ | ~~`music.play_url.uri`~~ | **不要用**：那是背景音乐，不是口播内容；旧实现用它会导致转写出来全是音乐歌词 |
| 时长 | `video.duration`，**单位毫秒** | `AudioDownloadResult.duration` 需要秒，要 `/1000` |
| 标题 | `item_title` → `desc` | `item_title` 经常为空字符串 |
| 封面 | `video.cover` / `origin_cover` / `cover_original_scale` / `dynamic_cover` 的 `url_list[0]` | 旧代码的 fallback 键写错会 KeyError |
| 标签 | `desc` + `text_extra[].hashtag_name` + `video_tag[].tag_name` | 旧代码取不存在的 `caption` 会 KeyError |

---

## 8. 实测证据（2026-10，用真实 cookie 复现）

```text
# 旧实现（cookie + a_bogus，无 x-tt-argus）
HTTP 403 len=46  text/plain  Blocked by ArgusSecurityPlugin Uifid Not Found
→ response.json() → JSONDecodeError: Expecting value: line 1 column 1 (char 0)
   与线上报错完全一致

# 补 x-tt-argus / uifid 头 + uifid/verifyFp/fp 参数后
HTTP 200  {"aweme_detail": {...}}   → 正常解析
```

同一批测试里，`aweme_detail` 为 `null` 的两个作品分别是作品自身状态问题
（`status_audit_not_pass`、`music_deleted_video`），不是代码问题。

另外验证过：`yt-dlp 2026.08.19` 的抖音 extractor 不做 Argus 签名，
带同一份 cookie 也只报 `Fresh cookies (not necessarily logged in) are needed`，
所以**不能指望 yt-dlp 走抖音**，必须走本文的签名方案。

---

## 9. 配置落在哪里

- Cookie 存储：`backend/config/downloader.json`（打包版在 exe 同级 `config/downloader.json`）
- 写入接口：`POST /api/update_downloader_cookie`（`platform=douyin`）
- 新实现每次请求都重新读取配置，**更新 cookie 后无需重启后端**
- 旧版在 `DouyinDownloader.__init__` 里缓存 cookie，而 `SUPPORT_PLATFORM_MAP` 是单例，
  所以旧版必须重启后端才生效

---

## 10. 排查清单

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` | 接口返回 403 文本 / 空 body | 看后端日志里的 HTTP 状态与 body；按第 6 节处理 |
| `403 ... Uifid Not Found` | cookie 缺 `UIFID`，或没带 `x-tt-argus` | 重新导出完整 cookie；确认请求头 |
| `403 ... Signature Not Found` | `verifyFp` 与 cookie 不同源 / 签名参数顺序错 | 用 cookie 里的 `s_v_web_id`；先定参后签名 |
| `200 + aweme_detail=null` | 作品审核/删除/地区限制 | 换作品；不是配置问题 |
| 转写出来是歌词/音乐 | 用了 `music.play_url` | 改用 `video.play_addr` 抽音轨 |
| 时长明显放大 1000 倍 | 毫秒当秒用 | `/1000` |
| 更新 cookie 不生效 | 旧实现启动时缓存 | 重启，或升级到本实现 |

---

## 11. 让改动生效

- **源码运行**：`backend/app/downloaders/douyin_downloader.py` 已按本文实现，直接重启后端即可
- **打包版（桌面端）**：`D:\Program Files\BiliNote\BiliNoteBackend.exe` 里是旧代码，
  需要重新打包（`backend/build.bat` / `build.sh`，PyInstaller）后才会带上新逻辑；
  没重新打包前，在设置页里怎么改 cookie 都仍然会 403
- 更新 cookie 后无需重启（新实现每次请求都重新读 `config/downloader.json`）
