# 雪球大V观点印证分析工具（xueqiu-analyzer）

> 把雪球大V的「观点」变成可验证的「事实」：自动抓取发言 → 结构化识别多空观点与标的 → 用真实行情做 **β 剥离归因** → 历史 **命中率 / IC 回测** → 抓到新发言即时给出 **跟随 / 观望 / 反向** 信号。

本地优先、数据自持、纯个人研究用途。后端 Python（本地 Web 服务 + SQLite），前端是零构建的单页应用。所有行情数据均来自真实市场——**主价格源为雅虎财经（Yahoo Finance）**，一口井覆盖 A股（.SS/.SZ）/ 港股（.HK）/ 美股（字母 ticker）/ 指数（沪深300 `000300.SS`、创业板指 `399006.SZ`、上证指数 `000001.SS`）；A股概念板块（稀土 / 半导体等）雅虎不提供，仍由 akshare 兜底。绝不编造数字。

---

## 一、它解决什么问题

财经社区里大V观点满天飞，但「他说得准不准」很少被系统量化。本工具把这件事工程化：

- 你关注的某位大V说「某某板块看多」——这句话到底对不对？
- 他历史上看多/看空的各种标的，T+5、T+10 的真实胜率是多少？
- 他擅长哪些板块？新发一条观点，该跟、该观望、还是该反向？

工具用**真实行情**回测给出答案，并把「观点 → 标的 → 实际涨跌 → 是否命中」整条链路沉淀下来，长期积累成你自己的大V「战绩档案」。

---

## 二、核心功能

- **多源抓取**：通过雪球 API 子域直连（绕过阿里云 WAF）抓取关注大V的发言，支持原帖 / 长文 / 回帖多种类型。
- **结构化观点识别**：每条发言自动识别「单主体标的 + 多空立场 + 时间维度 + 对比/衬托标的」。内置 A 股实体词典 + 板块词典，纯规则即可离线运行；配置 API Key 后走 LLM 路径，识别更强。
- **β 剥离归因**：个股实际涨跌 = 大盘 β + 板块 α + 个股 α。回测只看「板块 α + 个股 α」（观点真正带来的增量信息），剔除大盘和板块整体波动的干扰。
- **命中率 / IC 回测**：分窗口（T+1 / 3 / 5 / 10 / 20）统计多空命中率，分板块统计胜率；IC = mean(观点方向 × 实际方向)。
- **自动预测（核心）**：抓到新发言自动预测走向，挂上该人历史命中率与擅长板块，输出**置信度 + 跟随/观望/反向信号 + 校准曲线**。
- **五视图前端**：
  1. **时间线**（默认）：待验证 / 已验证分栏，已验证卡片含 β 剥离四宫格、T+5 实际收益、命中标记。
  2. **人物分析**：单人大 V 的命中率矩阵（多/空 × T+1~20）、分板块胜率、历史发言下钻。
  3. **预测中心**：自动预测卡片 + 预测校准曲线（可靠性图）。
  4. **设置**：跟踪对象管理、Cookie 鉴权、AI 模型、抓取范围、自定义分析 Skill。
  5. **抓取监控**：API / Cookie / WAF 状态、调度日志、实时进度。
- **后台自动轮询**：可一键开启后台守护，按设定间隔（默认 10 分钟）增量抓取并自动重算。
- **自定义分析 Skill**：`analysis_skills/` 下每个 `.py` 模块可对不同大V套用差异化分析逻辑（如某位只聊半导体、某位只聊红利）。

---

## 三、技术架构

```
┌─────────────────────────────────────────────┐
│  浏览器 (ui/index.html, 原生 JS + ECharts)    │
│  五视图 SPA，fetch('/api/*')                  │
└───────────────┬─────────────────────────────┘
                │  HTTP / JSON
┌───────────────▼─────────────────────────────┐
│  server.py  (ThreadingHTTPServer, :8765)      │
│  静态托管 ui/  +  /api/* 路由                  │
└───────┬───────────────┬───────────┬──────────┘
        │               │           │
┌───────▼──────┐ ┌──────▼──────┐ ┌─▼─────────────┐
│ fetcher.py   │ │ api_adapt.py│ │ engine.py      │
│ 抓取编排+守护 │ │ 结果→UI契约 │ │ 事件/回测/预测 │
└───┬──────┬───┘ └─────────────┘ └───────┬───────┘
    │      │                              │
┌───▼───┐ ┌▼────────────┐         ┌──────▼──────┐
│xueqiu │ │analyst.py   │         │ market.py   │
│client │ │观点结构化    │         │ 雅虎行情    │
└───────┘ └─────────────┘         └─────────────┘
        │
┌───────▼────────┐   ┌──────────────┐
│cookie_provider │   │  db.py       │
│手动粘贴 Cookie │   │ SQLite 7 表  │
└────────────────┘   └──────────────┘
```

- **后端**：纯标准库 + `requests`，本地进程内运行，数据全在本地 SQLite，不上传任何云端。行情主源为**雅虎财经（Yahoo Finance chart API，零依赖直连）**，A股概念板块由 `akshare` 兜底。
- **前端**：单页应用，原生 JS + [ECharts](https://echarts.apache.org/)（CDN），无构建步骤、无框架。
- **鉴权**：雪球 Cookie 通过**手动粘贴**获取（从浏览器登录态复制 Cookie 字符串粘贴到设置页），失败时层层优雅降级。
- **模型**：可接 DeepSeek / 通义千问 / 智谱 GLM（OpenAI 兼容）。**不配置 Key 也能跑**——自动回退到纯规则分析。

---

## 四、目录结构

```
xueqiu-analyzer/
├── server.py              # 本地 Web 服务入口（端口 8765）
├── run.py                 # 程序入口（初始化DB + 起服务 + 开浏览器 + 可选守护）
├── config.py              # 路径/常量/设置读写
├── db.py                  # SQLite 数据层（7 张表，单例连接 + WAL）
├── fetcher.py             # 抓取 + 分析 + 回测编排，后台自动轮询 worker
├── xueqiu_client.py       # 雪球抓取客户端（WAF 绕过、用户解析、时间线分页）
├── analyst.py             # 发言结构化分析（LLM + 纯规则兜底，单主体识别）
├── engine.py              # 分析引擎：事件重建 + β剥离 + 命中率/IC回测 + 预测
├── market.py              # 行情获取层（akshare，仅 A股概念板块兜底）
├── symbol_mapper.py       # 标的代码归一化（A/港/美/指数 → 雅虎 ticker）
├── ingest_yahoo.py        # 价格回填：从雅虎财经拉日线 OHLC 灌 market_daily（主价格源）
├── cookie_provider.py     # Cookie 统一入口（手动/v2/游客兜底）
├── sample_data.py         # 示例/演示数据生成
├── build.spec             # （可选）想自行打包成 exe 时用的 PyInstaller 配置
├── requirements.txt       # Python 依赖
├── .gitignore
├── data/                  # 运行时生成（DB / settings.json / 运行态，已 gitignore）
├── analysis_skills/       # 自定义分析 Skill 扩展点
├── prototype/             # 早期原型（参考用）
└── ui/
    ├── index.html         # 五视图单页应用
    ├── app.js             # 视图渲染 / 路由 / fetch / ECharts
    ├── style.css          # 配色（A股：涨=红 #e23c39 / 跌=绿 #16a36a）
    ├── README.md          # 前端骨架说明
    └── data/              # 占位示例 JSON（前端无后端时可直接预览）
```

---

## 五、快速开始

### 环境要求

- Python 3.10+
- Windows / macOS / Linux（已在 Windows 11 实测）

### 安装

```bash
cd xueqiu-analyzer
pip install -r requirements.txt
```

> 本工具需手动粘贴 Cookie；推荐使用配套的 Chrome 扩展 **[Cookie 管家（cookie-picker）](https://github.com/JohnWish1590/cookie-picker)** 一键取 Cookie（详见下文「Cookie 鉴权」）。

### 运行

```bash
python server.py
# 浏览器打开 http://localhost:8765
```

首次进入是「设置 / 演示模式」：可以直接用 `ui/data/` 里的占位数据预览界面；要分析真实大V，按下面「配置」步骤接好 Cookie 和跟踪对象即可。

### 价格数据回填（分析前必做）

「已验证 / 回测」依赖 `market_daily` 里的真实日线。本工具**主价格源是雅虎财经（Yahoo Finance）**，一条命令即可把数据库里所有被引用的标的（A股 / 港股 / 美股 / 沪深300 等指数）拉全：

```bash
python ingest_yahoo.py            # 自动扫描 DB 全部标的 + 美股回填，灌库后自动重算事件
python ingest_yahoo.py --dry     # 只打印将要拉取的标的，不实际请求
```

- 雅虎 chart API 需带浏览器 `User-Agent`（脚本已内置），批量拉取会自动串行 + 429 退避 + `query1`/`query2` 双 host 轮换。
- A股概念板块（稀土 / 半导体等）雅虎不提供，`sector_alpha` 暂为空；如需可由 `market.py`（akshare）补充。
- 个别雅虎覆盖不全的标的（如创业板指 `399006` 历史数据缺失）会自动跳过、保留既有数据，不影响分析。
- 曾用腾讯自选股 MCP（`ingest_westock.py`）或东方财富（`price_feed.py`）回填的，可继续作为离线兜底，互不冲突。

> **🤖 给 Agent / 朋友安装**：本仓库**只有源码、没有发布 exe**。把仓库地址 **https://github.com/JohnWish1590/xueqiu-analyzer** 发给任意会写代码的 AI Agent（或懂 Python 的朋友），它就能照下面三步自己装好并跑起来：① `git clone` 或「Download ZIP」下载 → ② `pip install -r requirements.txt` → ③ `python server.py` → 浏览器打开 `http://localhost:8765`。Cookie 一律配合下方「Cookie 管家」扩展获取（也支持手动粘贴）。

### 可直接转发给 Agent 的安装话术

复制下面代码块发给 agent 即可，它会自己 clone + 安装 + 起服务；**Cookie 那步需人在浏览器里登录雪球并点扩展复制，agent 无法代登录**。

```
请帮我把下面这个本地研究工具在本地跑起来（纯本地、数据不上传）：

仓库：https://github.com/JohnWish1590/xueqiu-analyzer

步骤：
1. 下载：git clone https://github.com/JohnWish1590/xueqiu-analyzer.git（或 Download ZIP）
2. 安装：cd xueqiu-analyzer && pip install -r requirements.txt
3. 启动：python server.py（会起本地 Web 服务并提示打开 http://localhost:8765）
4. 浏览器打开 http://localhost:8765 即可使用

Cookie（必须）：装 Chrome 扩展 Cookie 管家 https://github.com/JohnWish1590/cookie-picker
  → chrome://extensions/ 开开发者模式 → 加载已解压的扩展程序 → 选该目录
  → 浏览器登录 xueqiu.com → 点橙色图标 → 勾选「雪球」→ 测试读取 → 复制
  → 回工具「设置 → Cookie 鉴权」直接粘贴（JSON 自动提取雪球部分）→ 保存 Cookie → 显示 ✅ 保存成功

环境：Python 3.10+，Windows/macOS/Linux；首次抓取需联网（雅虎财经行情 + 雪球 API）；
不填 API Key 也能跑（纯规则兜底）；数据全在本机 SQLite，不上传云端。
```

---

## 六、配置

所有配置写在 `data/settings.json`（已 gitignore，不入库）。也可在网页「设置」页完成。

### 1. 添加跟踪大V

两种来源：

- **按 user_id / 昵称解析**：设置页输入雪球数字 ID 或昵称（如 `顾序`），自动解析为 ID 并加入跟踪。
- **读取「特别关注」分组**：一键从雪球拉取你的「特别关注」名单，弹窗里勾选要跟踪的人，点「保存选中」。勾选即生效、取消即排除，且与原有已勾选状态**增量合并**（不会清掉之前选的人）。

### 2. Cookie 鉴权（必须）

抓取需要登录态 Cookie。**推荐配合 Chrome 扩展「Cookie 管家」（[cookie-picker](https://github.com/JohnWish1590/cookie-picker)）一键取 Cookie**；也支持纯手工从 DevTools 复制。

#### 方式一（推荐）：用 Chrome 扩展 Cookie 管家 取 Cookie

1. **安装扩展**：打开 `chrome://extensions/` → 右上角开「开发者模式」→ 「加载已解压的扩展程序」→ 选择 [cookie-picker](https://github.com/JohnWish1590/cookie-picker) 目录。工具栏出现橙色图标即成功。
2. **登录雪球**：浏览器登录 [xueqiu.com](https://xueqiu.com)（确保已登录）。
3. **取 Cookie → 复制**：点工具栏橙色图标打开 Cookie 管家 → 确认「雪球」已勾选 → 点「测试读取」（显示 `✓ N 条`）→ 点「复制」。
4. **粘贴到这里**：回到本工具「设置 → ② Cookie 鉴权」文本框，**直接粘贴**刚才复制的内容（是一段 JSON，本工具会自动提取其中的雪球 Cookie），点「**保存 Cookie**」→ 页面立即显示 **✅ 保存成功**。

> 复制的内容形如 `{"cookies":{"xueqiu":{"domain":"xueqiu.com","header":"xq_a_token=...; ..."}}}`；本工具会自动解析并只取雪球部分，无需你手动挑字段。若未勾选「雪球」就复制，会提示「未找到雪球登录态」。

#### 方式二（兜底）：纯手工从 DevTools 复制

1. 浏览器登录 [xueqiu.com](https://xueqiu.com)；
2. 打开开发者工具（F12 → Network → 任意雪球请求 → 复制 `Cookie` 请求头）；
3. 把整段 `k=v; k2=v2` 字符串粘贴到设置页文本框，点「**保存 Cookie**」，页面会立即显示「✅ 保存成功」。

> Cookie 仅在本地使用，不会上传。雪球登录态失效时（定期过期）抓取会失败并提示，重新取一次并保存即可。

### 3. AI 模型（可选）

设置页选择模型供应商并填入 API Key：

- DeepSeek V4 Flash（默认·高速低价）/ DeepSeek V4 Pro（高精度）
- 通义千问 / 智谱 GLM

**不填 Key 也能用**：自动回退到 `analyst.py` 的纯规则分析（内置实体词典 + 板块词典 + 态度词），完全离线。

### 4. 抓取范围

- **发言类型**：原帖 / 长文 / 回帖 可单独开关（默认抓原帖 + 长文，回帖观点片面默认不抓）。
- **时间范围**：1 天 / 3 天 / 自定义 天。
- **立即抓取 vs 后台轮询**：设置页「立即抓取选中人员」手动触发；顶部状态条「开启自动轮询」启动后台守护（默认每 10 分钟增量）。

---

## 七、分析方法论（为什么可信）

工具的回测严格遵循以下原则，**拒绝「拍脑袋」**：

1. **单主体识别**：一条发言常提多只股票，但只产出一个「主体事件」进回测；其余对比/衬托标的仅展示、不进回测。避免把「A 涨好、对比 B 差」误算成两次独立预测。
2. **β 剥离**：个股实际涨跌 = 大盘 β + 板块 α + 个股 α。回测只统计**板块 α + 个股 α**（观点真实增量信息），剔除大盘和板块整体波动干扰。
3. **无未来函数**：验证窗口只用发言**时点之后**的数据，杜绝用未来信息造假。
4. **小样本必报 N**：所有命中率一律带样本量，样本太小（如 N=1）不会被包装成「高胜率」。
5. **自动预测 + 校准**：抓到新发言即预测走向，置信度由该人历史命中率映射；随样本回写持续校准（预测校准曲线）。

> ⚠️ 任何回测都有幸存者偏差、样本偏差、幸存期偏差等局限。本工具输出**仅供研究参考，不构成任何投资建议**。

---

## 八、数据模型（SQLite，7 张表）

| 表 | 作用 |
|----|------|
| `users` | 被跟踪大V（xid、昵称、简介） |
| `posts` | 发言（含单主体 `subject_stocks`、对比 `contrast_stocks`、板块、立场、时间维度、摘要、所用模型） |
| `market_daily` | 指数 / 板块 / 个股日线（**雅虎财经**真实数据，A/港/美/沪深300 全覆盖；A股概念板块留空待 akshare） |
| `events` | 发言→标的 的三级递进事件 + β 剥离 + 各窗口命中 |
| `predictions` | 抓到发言后自动生成的预测（核心层） |
| `backtest` | 分窗口历史命中率与 IC |
| `backtest_sector` | 分板块历史胜率 |

数据库路径：`data/xueqiu_analyzer.db`（位于项目内，随项目走，不依赖外部服务）。

---

## 九、API 路由（供二次开发）

本地 `server.py` 在 `:8765` 暴露以下接口（返回 JSON，`Cache-Control: no-store`）：

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/timeline_pending` | 待验证时间线 |
| GET | `/api/timeline_verified` | 已验证时间线 |
| GET | `/api/persons` | 人物汇总（命中率矩阵 / 分板块 / 历史） |
| GET | `/api/predictions` | 预测中心 |
| GET | `/api/settings` | 当前配置 |
| GET | `/api/monitor` | 抓取监控状态与日志 |
| GET | `/api/status` | 简版状态 |
| GET | `/api/worker/status` | 后台轮询 worker 状态 |
| POST | `/api/save_followed` | 保存跟踪对象（合并保留勾选状态） |
| POST | `/api/followed_groups` | 拉取雪球「特别关注」分组 |
| POST | `/api/resolve_user` | 昵称 / ID 解析为 user_id |
| POST | `/api/start_fetch` | 立即抓取选中人员 `{days?}` |
| POST | `/api/worker/start` | 启动后台自动轮询 |
| POST | `/api/worker/stop` | 停止后台自动轮询 |
| POST | `/api/save_backfill_days` | 保存默认回填天数 `{days}` |

---

## 十、常见问题 / 故障排查

**Q：页面打不开 / 端口 8765 没反应？**
本地服务进程不跨会话存活。重新运行 `python server.py` 即可；或在 Windows 把 `start_hidden.bat` 放进「启动」文件夹实现开机自启。

**Q：抓取提示 Cookie 失效？**
雪球 Cookie 会定期失效。到设置页「Cookie 鉴权」重新粘贴最新 Cookie 并点「保存 Cookie」即可。

**Q：为什么有的大V抓不到近期帖子？**
- 自动轮询默认只抓「原帖 + 长文」，不抓回帖；如需回帖，到设置页开启。
- 抓取时间范围默认 3 天，可调大或自定义。
- 该大V近期确实无对应类型新帖。

**Q：行情相关功能报错？**
行情依赖 `akshare` 联网获取。未安装 `akshare` 时，market 模块会优雅降级（仅演示数据可用）；安装 `pip install akshare` 后即为真实数据。

**Q：没有 API Key 能用吗？**
能。`analyst.py` 提供纯规则兜底，离线即可结构化分析发言。

---

## 十一、免责声明

- 本工具为**纯个人研究 / 学习用途**的本地软件，所有数据抓取与分析均在用户本机完成，不提供任何在线服务。
- 数据来源为雪球公开内容与第三方行情接口（akshare），**不对数据准确性、完整性作任何担保**。
- 工具输出的命中率、预测、信号**仅供研究参考，不构成任何投资建议**。据此操作风险自负，开发者不承担任何直接或间接责任。
- 请遵守雪球及相关平台的服务条款与当地法律法规，勿用于任何商业或违规用途。

---

## 十二、License

[MIT](./LICENSE) —— 可自由使用、修改、分发，请保留版权与免责声明。

---

完整更新日志（过程、踩坑、数据来源）见 [CHANGELOG.md](./CHANGELOG.md)。

Socials: @下一站澳门. DM for inquiries.
