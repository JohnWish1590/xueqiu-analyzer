# 更新日志 · 雪球大V观点印证分析工具（xueqiu-analyzer）

本项目自首个可用版本起采用 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 规范记录每次发布的 notable changes。
所有行情数据均来自真实市场，**绝不编造数字**；数据抓取与分析全部在用户本机完成。

格式约定：`Added` 新功能 / `Changed` 变更 / `Fixed` 修复 / `Pitfalls` 踩过的坑（留存备查）。

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
