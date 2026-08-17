# 更新日志 · 雪球大V观点印证分析工具（xueqiu-analyzer）

本项目自首个可用版本起采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范记录每次发布的 notable changes。
所有行情数据均来自真实市场，**绝不编造数字**；数据抓取与分析全部在用户本机完成。

格式约定：`Added` 新功能 / `Changed` 变更 / `Fixed` 修复 / `Pitfalls` 踩过的坑（留存备查）。

---

## [2026-08-17] 启动器改进：控制台最小化 + 浏览器应用模式

### Changed
- **`start_server.bat`**：服务改用 `start /MIN` 在**最小化控制台**中运行（日志仍可还原查看）；启动器自身用 PowerShell 自最小化，`pause` 改为自动退出，运行后桌面上只剩浏览器窗口。
- **浏览器打开方式**：`--start-fullscreen`（F11 式无任务栏全屏）改为 `msedge --app=<URL> --start-maximized` 的**应用模式 + 最大化**——保留任务栏、无标签栏、页面占满可视区。
- **关旧开新**：每次启动先用 PowerShell 关掉上一轮的 `localhost:8765` Edge 应用窗口（`Get-CimInstance` 按命令行匹配 `--app=http://localhost:8765` 后 `Terminate`），避免每次双击都累加一个窗口/标签。
- **`launch_app.bat`**：同步改为应用模式 + 关旧开新（服务本就用 `pythonw` 隐藏运行）。

### Pitfalls
- 沙箱内 PowerShell `Add-Type` / `WScript.Shell` COM 被安全策略拦截，无法在此实测"自最小化"与改快捷方式属性；标准写法在真机可运行。若自最小化偶发失效，可手动把桌面快捷方式属性「运行方式」设为「最小化」兜底。

---

## [2026-08-17] 修复：时间线「最新停在 08-14」（前端缓存 + 行情时间基准）

### Fixed
- **前端静态资源长期缓存**：服务端此前只给 `/api/*` 加了 `Cache-Control: no-store`，`index.html / app.js / style.css` 可被浏览器长期缓存。旧版 `app.js` 带 `daysAgo >= 0` 过滤，把 8/14 之后（8/15–8/16）的发言全排除，导致时间线只显示到 8/14。修复：
  - `server.py` 静态资源统一加 `Cache-Control: no-store, must-revalidate`；
  - `index.html` 内 `app.js`/`style.css` 的 `?v=` 改成**进程启动时间戳**动态戳，发版后强制浏览器拉最新；
  - `index.html` 加 `no-store` meta 兜底。
- **行情时间基准卡死（根因）**：`market_daily` 行情表停在 2026-08-14（缺 8/15 周五、8/17 今天），`api_adapt._as_of()` 用 `get_trading_dates()[-1]` 当「今天」→ 全站参考日期、T+7 验证窗口、相对时间（age 天数）全错乱，8/15–8/16 被算成「未来 / 负天数」。修复：
  - `market.py` 的 `fetch_index_daily` 改为 **akshare 优先、雅虎兜底**（沙箱东财被墙、雅虎可通），已用雅虎实拉真实日线补 `market_daily`，交易日历延伸到 8/17；
  - `api_adapt._as_of()` 加兜底：行情表最后交易日若早于真实今天，直接用 `date.today()`，杜绝行情源断更时再卡死；
  - `ui/app.js` 的 `refDate` 兜底：后端参考日期若早于真实今天，相对时间改以真实今天计算，消除 `age -1 天` 怪显示。

### Pitfalls
- 8/15（周五）雅虎沪深300、上证两端都缺数据（指数在雅虎上 8/15 是空档，已知稀疏限制）。真机 akshare（东财）下次抓取会自动补全；时间基准已用 `_as_of` 兜底兜住，不影响展示与验证窗口。
- 沙箱 `taskkill` 无权限杀 8765 旧实例，需 PowerShell `Stop-Process -Force` 才能干净重启；批处理 `launch_app.bat` 已能正常净端口，日常双击即可。
- 改动文件：`server.py`、`index.html`、`market.py`、`api_adapt.py`、`ui/app.js`，全部 `py_compile` / `node --check` 通过。

---

## [2026-08-17] 工作流重构：实时解读 + 证据账本 + 画像

从「事后判定大V对错」转向「实时解读 + 证据归档 + 画像沉淀」的工作流。

### Added
- **人话解读层 `analyst.interpret_post()`**：把每条发言翻译成「这句话什么意思 / 指什么板块 / 点的个股 / 相对还是绝对 / 时间尺度+置信度 / 客观风险提示」。**只做理解、不给操作建议**（跟/反/观望留给证据账本+画像）。LLM 生成 + `heuristic_interpret` 离线降级。
- **证据账本 `evidence_ledger` 表 + `archive_evidence.py`**：发言 + 解读 + 实际走势对比的归档。按解读出的时间尺度映射预期窗口（短线→T+3、中线→T+7、长线→T+20、观察→T+7），用超额方向命中（个股 − 沪深300，剥离 Beta）判定；`manual_tag` 留空待人工打标签（对/错/部分对/存疑）。
- **画像 `_user_profile`**：从证据账本自动统计「典型兑现窗口 / 基准倾向（相对大盘还是绝对收益）/ 看多看空命中率」，不靠印象。证据不足显示「数据积累中」。
- **回撤修复**：`events` 表新增 `mdd`（峰值到谷值真回撤）、`peak_to_close`（峰值到终点回落）、`drawdown_speed`（峰→谷交易日数，正=冲高回落）、`limit_down_days`（跌停近似天数）。
- **前端**：时间线卡片新增「AI 解读」子卡；新增「证据账本」视图（归档 / 补解读 / 人工打标签按钮）；人物分析页新增「画像」卡片。
- **路由**：`GET /api/evidence_ledger`、`POST /api/backfill_interpretation`（存量补解读）、`POST /api/archive_evidence`、`POST /api/tag_evidence`。

### Fixed
- **伪回撤**：旧 `trough_ret` 只算「区间最低相对起点」，会把「冲高回落」误判成「没跌」。新 `mdd`（峰值到谷值）才反映真实回撤。实测案例：某看空发言 `trough_ret=-3.4%`（看似没兑现），实际 `mdd=10.4%`（从峰值回撤 10%）。
- **启动器假活**：`launch_app.bat` / `start_server.bat` 增加「先清空 8765 端口所有进程（含假活/僵尸）→ 启动 → 等就绪 → 自动开浏览器」，杜绝多实例抢端口导致的 `ERR_EMPTY_RESPONSE`。

### Pitfalls
- 归档 upsert 用 `COALESCE(excluded.manual_tag, evidence_ledger.manual_tag)`，避免重跑归档时把人工标签覆盖回空。
- 存量补解读只针对「看多/看空」发言（中性/闲聊无解读价值），按 `created_at` 升序补最老的先补。

---

## [2026-08-14] 价格源全面切雅虎 + 模型/Key 自管理 + 服务可启停

### Added
- **价格源全面迁移至雅虎财经（Yahoo Finance chart API）**：一口井覆盖 A 股（`.SS`/`.SZ`）、港股（`.HK`）、美股（字母 ticker）、指数（沪深300 `000300.SS`、创业板指 `399006.SZ`、上证指数 `000001.SS`），自带每日 `open/high/low/close`，盘中最高价与收盘价齐全，可直接喂「区间极值 / T+N 收盘」类验证。
- **`symbol_mapper.py`**：新增 `to_yahoo_symbol()` / `yahoo_symbol_for_raw()`，把任意写法（A/港/美/指数、原始雪球码、带前缀码）归一化为雅虎 ticker。港股特殊处理「去前导零后补足 4 位」：`00700→0700.HK`、`09992→9992.HK`。
- **`ingest_yahoo.py`**：主价格源灌库脚本。带浏览器 `User-Agent`，429 指数退避（最大 30s）+ `query1`/`query2` 双 host 轮换；逐标的拉日线 OHLC 写入 `market_daily`；`YAHOO_SKIP={"399006"}` 跳过雅虎不全的指数，保留既有 westock 完整历史。
- **模型 / Key 自管理三件套**：
  - `POST /api/save_model`：保存 `provider` + `model` + `api_key` 到 `settings.json`；Key 为空则「保留原 Key」（修掉之前把掩码当真 Key 的隐患）。
  - `POST /api/detect_models`：用 Key 逐家向 `/models` 接口探测真实可用模型，下拉只显示探测结果，避免写死 4 个内置模型。
  - `POST /api/clear_model`：清 Key + 选中模型，自动降级为本地启发式分析。
- **「关闭服务」按钮（网页右上角红框）**：`POST /api/shutdown` 仅退出当前 server 进程（`os._exit(0)`），**不影响剪思盒 / 雪哨等其他 Python 程序**；点完显示全屏「服务已关闭」遮罩，提示双击 `launch_app.bat` 重启。
- **`launch_app.bat`**：双击即启动——检查 8765 端口（已在跑就不重复起，杜绝多实例）→ 用 `pythonw` 无窗口起服务 → 等端口就绪 → Edge `--start-fullscreen` 打开 `http://localhost:8765` → 启动器退出，**完全脱离 WorkBuddy**。
- **统一 Socials 页脚**（与作者其他 20 个 GitHub 仓库完全一致）：
  ```
  Socials: @下一站澳门. DM for inquiries.
  ```
  即「下一站：澳门 / Next Stop: Macau」统一署名。

### Changed
- README 技术架构图、后端依赖、目录结构、数据表说明全部更新为「行情主源 = 雅虎财经，概念板块 akshare 兜底，399006 等雅虎不全者 westock 兜底」。
- `config.DEFAULT_SETTINGS` 新增 `model` 字段；`analyst._llm_analyze` 优先使用用户选定的具体模型，未选则回退厂家默认。
- 前端 `app.js` 模型下拉改为「输入 Key → 探测可用模型 → 选哪个存哪个」，不再硬编码 4 个厂商模型。
- 顶部状态栏：把「正在抓取 xxx」移到最前并加 `min/max-width` + 超长截断，后面「API 正常 / Cookie 有效」不再随人名长短跳动。
- 左侧导航顶部新增固定品牌标题「雪球大V观点印证分析」，滚动后不再消失（之前依赖头部 logo，滚动视觉上会丢）。
- 前端缓存戳 `v18 → v20`。

### Fixed
- **模型/Key 掩码回填 bug**：原 `build_settings` 把 Key 掩码成 `sk-****` 返前端，`renderSettings` 又写回可编辑输入框；用户不改动直接保存会把掩码当真 Key 存进去。现输入框留空 = 保留原 Key，只有真输入新 Key 才覆盖。
- **本地服务「假活」进程**：多个 `server.py` 同时绑 8765 时，其中一个端口挂着但主线程已死，新连接被立刻掐断 → 浏览器 `ERR_EMPTY_RESPONSE`。现启动严格走「先净端口、后起单例」。

### Pitfalls（踩过的坑，留存备查）
1. **雅虎 429**：不带 `User-Agent` 会被当成机器人直接 429。必须带浏览器 UA；批量抓取要串行 + 429 指数退避 + `query1`/`query2` 双 host 轮换。
2. **港股 4 位 ticker 坑**：雅虎要「去前导零后补 4 位」，不是 5 位（`09992.HK` 应写 `9992.HK`）。一开始按 5 位填，4 只港股全 404。
3. **399006 / 162605 雅虎历史不全**：创业板指雅虎只返 1 个数据点，基金 162605 直接 404。这两类**跳过雅虎、保留 westock 完整历史**作兜底，否则回测区间会塌。
4. **去重冗余**：`posts` 里 `9992` / `9992.HK` / `09992` 三种写法会被当三个标的一起拉。改为按雅虎 ticker 去重，最终 22 标的。
5. **sqlite schema 字段名**：误用 `posts.name`/`posts.title`/`events.view`，实际是 `events.stock_code`/`events.stance`、`posts.summary`。
6. **DualStack 双栈绑定**：服务同时监听 IPv4 与 IPv6（`localhost` 解析到 `::1`），`query1`/`query2` 与 `127.0.0.1`/`::1` 任一都能访问。
7. **网页无法「自启动」服务**：网页由 server 提供，server 一关网页就没了，故网页里只能做「关闭」，启动统一交给 `launch_app.bat`（已带端口检测，不会重复起）。

---

## 数据从哪来（抓取链路总览）

| 数据 | 来源 | 方式 | 备注 |
|------|------|------|------|
| 雪球大V发言 / 观点 | 雪球公开内容 | `xueqiu_client`（需 Cookie 鉴权） | 观点结构化在 `analyst.py` 完成 |
| 个股 / 指数日线（OHLC） | **雅虎财经 chart API** | `ingest_yahoo.py` 直连 `query{1,2}.finance.yahoo.com` | 主价格源，覆盖 A/港/美/指数 |
| A 股概念板块（稀土 / 半导体等） | akshare | 兜底脚本 | 雅虎不提供概念板块 |
| 399006 / 162605 等雅虎不全标的 | westock | 保留既有完整历史 | `YAHOO_SKIP` 跳过雅虎 |
| 回测 / 命中率 / IC | 本地 SQLite + `engine.py` | β 剥离归因 | 个股收益 − 沪深300收益 = 超额方向 |

**验证方法论（为什么可信）**：主锚点 **T+7**（7 交易日），命中判定用 7 日超额方向（个股 T+7 收益 − 沪深300 T+7 收益）剥离 Beta；区间极值取发帖日起第 7 个交易日内最高/最低价 → `peak_ret`/`trough_ret`；`proc_hit` 标记区间内峰值/谷值方向是否触达观点。

---

## [2026-08 早些时候] 基础骨架与验证方法论落地

### Added
- 本地 Web 服务（ThreadingHTTPServer + DualStackServer 双栈，端口 8765）；前端每 2s 轮询 `/api/*` JSON。
- 验证四件套：7 日价格通道 + T+7 超额 + 区间极值 + 过程验证（T64–T67）。
- `engine` 收益存小数分数，API 层 ×100 转百分比。
- 跟踪大V管理、Cookie 导入、抓取监控、预测中心等前端骨架。

### Pitfalls
- 沙箱直连东方财富被墙，雅虎可通 → 这是后续全面切雅虎的直接动因。
- 雅虎**不含 A 股概念板块**，该部分仍需 akshare 兜底（沿用至今）。
