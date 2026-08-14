# 雪球大V观点印证分析 · 前端骨架（UI / 占位数据）

本目录是**纯前端静态骨架**，用于对接后续 Python 后端的真实数据。

- 仅包含 UI 与占位数据，**不含任何真实抓取 / 分析逻辑**。
- 可直接被任意静态服务器 serve（后续 Python 后端会 serve 这个目录）。
- 单页应用，原生 JS + ECharts（CDN），无构建步骤。

## 运行

```bash
# 在本目录（ui/）下启动静态服务器
python -m http.server 8000
# 浏览器打开 http://localhost:8000
```

> 注意：请用静态服务器访问，勿用 `file://` 直接打开——`fetch('./data/*.json')` 在 file 协议下会被浏览器拦截。

## 文件结构

```
ui/
├── index.html          单页应用，五视图导航（默认打开「时间线」）
├── app.js              视图渲染 / tab 切换 / 窗口选择器 / fetch 数据 / ECharts
├── style.css           A股配色（涨=红 #e23c39 / 跌=绿 #16a36a）
├── README.md
└── data/
    ├── timeline_pending.json   待验证发言
    ├── timeline_verified.json  已验证发言
    ├── persons.json            人物命中率矩阵 / 分板块胜率&IC / 历史
    ├── predictions.json        预测中心 + 校准点
    ├── settings.json           配置样例
    └── monitor.json            抓取监控状态 + 日志
```

## 五个视图

1. **时间线（默认）**：全局状态条 + 待验证/已验证选项卡 + 待验证窗口选择器（1天/3天/自定义，超出窗口自动归入已验证）。卡片含主体识别块、β 剥离四宫格、历史命中率。已验证卡片额外显示 T+5 实际与命中标记。
2. **人物分析**：左侧选人，右侧命中率矩阵（看多/看空 × T+1/5/10/20）+ 分板块胜率&IC 条形图（ECharts）+ 历史发言下钻。
3. **预测中心**：自动预测卡片（置信度/涉及板块/跟随·观望·反向信号）+ 预测校准曲线（ECharts 散点 + y=x 参考线）。
4. **设置**：跟踪大V、Cookie 状态、模型与密钥、发言类型开关、回填范围、Skill 开关（均从 `settings.json` 读取）。
5. **抓取监控**：API/Cookie/WAF/回填进度状态卡 + 调度日志。

## 数据契约（占位 JSON 字段，需与后端保持一致）

- `timeline_pending.json`：`post_id,user_name,user_id,created_at,text,post_type(original|long|reply),subject{name,code,stance,horizon},contrast[{name,code,note}],summary,attrib{index_beta,sector_alpha,stock_actual,stock_alpha},hist_hit_rate,hist_n`
- `timeline_verified.json`：在上方基础上增 `actual{t1,t5,t10,t20},hit(bool),stance_hit(看多命中/看空命中)`
- `persons.json`：`name,user_id,desc,hit_rate,n,ic,matrix{bullish{t1,t5,t10,t20,n},bearish{...}},sectors[{sector,hit,n,ic}],history[{post_id,created_at,text,subject,stance,hit}]`
- `predictions.json`：`post_id,user,subject,pred_stance,confidence,sector,involved_sectors[],hist_hit_rate,hist_sector_hit,signal(跟随/观望/反向),calibration[{conf,actual}]`
- `settings.json`：`followed_users[],model{provider,api_key},post_types{original,long,reply},backfill_days,skill_enabled`
- `monitor.json`：`api_status,cookie_status(valid|expired),cookie_expire,last_fetch,next_poll,fetched_total,pending,verified,backfill_progress,waf,logs[]`

> 字段命名尽量与 `db.py` 表字段兼容；其中 `post_type` 在契约中用 `original|long|reply`（对应 `db.py` 的 `original|longpost|reply`），后端适配时做一层映射即可。

## 预期后端路由（同结构 JSON 替换占位）

前端当前用 `fetch('./data/xxx.json')`。后续 Python 后端上线后，应提供以下路由并返回同结构 JSON：

| 路由 | 说明 |
|------|------|
| `GET /api/timeline?tab=pending\|verified&window=3` | 时间线（待验证/已验证 + 窗口天数） |
| `GET /api/persons` | 人物列表与汇总 |
| `GET /api/persons/:id` | 单人物命中率矩阵 / 分板块 / 历史 |
| `GET /api/predictions` | 预测中心列表 |
| `GET /api/settings` | 当前配置 |
| `GET /api/monitor` | 抓取监控状态与日志 |

接入时只需把 `app.js` 中的 `fetch('./data/xxx.json')` 改为对应的 `/api/...` 地址（可集中在一个 `API_BASE` 常量里切换）。
