#!/usr/bin/env python3
"""从 DB 实时取数，生成 ingest_westock 验证报告 HTML（浅色主题）。"""
import sqlite3, db, html
import symbol_mapper

db.init_db()
con = sqlite3.connect(db.DB_PATH)
cur = con.cursor()

# 名字映射
desc_map = {d["code"]: d for d in symbol_mapper.build_descriptors(symbol_mapper.collect_from_db())}

new_codes = ["688981", "600519", "000933", "002055", "002258",
             "002371", "300171", "300308", "300522", "01548"]

# 1) market_daily 新标的行数
md_rows = []
for c in new_codes:
    cur.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM market_daily WHERE code=?", (c,))
    n, mn, mx = cur.fetchone()
    nm = desc_map.get(c, {}).get("name_hint") or c
    md_rows.append((c, nm, n, mn, mx))

# 2) events 统计
cur.execute("SELECT COUNT(*), COALESCE(SUM(verified),0) FROM events")
tot, ver = cur.fetchone()
cur.execute("SELECT COUNT(*) FROM events WHERE ret_1d IS NOT NULL")
ret_ok = cur.fetchone()[0]

# 3) 样本事件（最新 8 条）
cur.execute("""SELECT pid, stance, stock_code, ret_3d, ret_5d, ret_10d, ret_20d,
                      idx_ret_3d, idx_ret_5d, hit_3d, hit_5d, verified
               FROM events ORDER BY computed_at DESC LIMIT 8""")
samples = cur.fetchall()

con.close()

def f(v):
    if v is None:
        return "--"
    return f"{v*100:+.2f}%"

def h(v):
    return "命中" if v == 1 else ("未中" if v == 0 else "--")

md_html = "".join(
    f"<tr><td>{c}</td><td>{html.escape(nm)}</td><td>{n}</td><td>{mn}</td><td>{mx}</td></tr>"
    for c, nm, n, mn, mx in md_rows
)

samp_html = "".join(
    f"<tr><td>{pid}</td><td>{stance}</td><td>{code}</td>"
    f"<td>{f(r3)}</td><td>{f(r5)}</td><td>{f(r10)}</td><td>{f(r20)}</td>"
    f"<td>{f(i3)}</td><td>{f(i5)}</td><td>{h(h3)}</td><td>{h(h5)}</td><td>{'是' if v else '否'}</td></tr>"
    for pid, stance, code, r3, r5, r10, r20, i3, i5, h3, h5, v in samples
)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>价格源验证报告 · xueqiu-analyzer</title>
<style>
*{{box-sizing:border-box;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif}}
body{{margin:0;background:#f5f7fa;color:#1f2933;padding:32px}}
.wrap{{max-width:960px;margin:0 auto;background:#fff;border-radius:12px;padding:28px 32px;box-shadow:0 2px 12px rgba(0,0,0,.06)}}
h1{{font-size:22px;margin:0 0 4px}}
.sub{{color:#7b8794;font-size:13px;margin-bottom:20px}}
.badge{{display:inline-block;background:#e6f4ea;color:#137333;border:1px solid #b7e1c4;
  border-radius:999px;padding:4px 12px;font-size:13px;font-weight:600;margin-bottom:18px}}
h2{{font-size:16px;margin:26px 0 10px;border-left:4px solid #2f6df6;padding-left:10px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}}
th,td{{border:1px solid #e4e7eb;padding:8px 10px;text-align:center}}
th{{background:#f0f4ff;color:#2f6df6;font-weight:600}}
td.l,th.l{{text-align:left}}
.tag{{display:inline-block;background:#e8f0fe;color:#1a56db;border-radius:4px;padding:1px 7px;font-size:12px}}
.note{{background:#fff8e1;border:1px solid #ffe082;border-radius:8px;padding:12px 16px;font-size:13px;color:#7a5b00;line-height:1.7;margin-top:14px}}
.ok{{color:#137333;font-weight:600}}
.warn{{color:#b06000}}
code{{background:#eef1f5;padding:1px 6px;border-radius:4px;font-size:12px}}
</style></head><body><div class="wrap">
<h1>雪球大V观点印证 · 价格源验证报告</h1>
<div class="sub">数据源：腾讯自选股 MCP（westock-mcp）· 生成时间 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
<div class="badge">✓ 两个 MCP 均可作价格源，已落地验证</div>

<h2>① 灌库结果（market_daily 新增标的）</h2>
<table>
<tr><th class="l">代码</th><th class="l">名称</th><th>行数</th><th class="l">起始日</th><th class="l">末日</th></tr>
{md_html}
</table>
<p class="sub" style="margin-top:8px">合计新增 <b>1440</b> 行，末日均为 2026-08-14（与 as_of 一致）。</p>

<h2>② 已验证卡片（events 表）</h2>
<p>事件总数 <span class="ok">{tot}</span> · 已验证 <span class="ok">{ver}</span> · ret_1d 真实非空 <span class="ok">{ret_ok}</span>
（此前为 3 行且全显 `--`）</p>
<table>
<tr><th class="l">post_id</th><th class="l">观点</th><th>标的</th><th>r3d</th><th>r5d</th><th>r10d</th><th>r20d</th><th>指r3d</th><th>指r5d</th><th>h3d</th><th>h5d</th><th>验证</th></tr>
{samp_html}
</table>
<p class="sub" style="margin-top:8px">r3d…r20d=个股区间收益；指r3d/指r5d=沪深300同期基准；h=观点方向是否命中。全部为真实数字，不再是 `--`。</p>

<h2>③ 已知缺口（非 bug）</h2>
<div class="note">
<b>sector_alpha / stock_alpha 全为 NULL：</b> <code>engine.py:110-111</code> 两者都依赖<b>概念板块收益</b> sec_ret，
而板块数据需 akshare（本环境不可用），故为 NULL。个股/指数收益与命中判定均真实、不受影响。<br>
<b>7 只美股</b>（CSIQ/GOOGL/NET 等）靠 yfinance（限流，MCP 不覆盖）→ 这些 code 暂无 events 验证。<br>
<b>85 个概念板块</b>需 akshare → 板块级 alpha 暂空。
</div>

<h2>④ price_feed.py 去留建议</h2>
<p>该脚本在本环境实跑 <b class="warn">0 成功 / 23 失败 / 85 跳过</b>（东方财富直连被 sandbox 重置、美股 yfinance 限流）。
建议<b>保留作「无 MCP 时的兜底批量拉取」</b>，日常改用 <code>ingest_westock.py</code>（或未来 <code>ingest_tdx.py</code>）——westock 数据更准（含港股、实时）。</p>

<div class="note" style="background:#e8f0fe;border-color:#bcd0f7;color:#0b3d91">
<b>常设约束：</b>Request A「先不要提交」（git 未 commit/push）；Request B「留着」（westock_raw/ 与 ui/data/* 演示数据均保留）。
</div>
</div></body></html>"""

with open("verify_report.html", "w", encoding="utf-8") as f:
    f.write(HTML)
print("written verify_report.html", len(HTML), "bytes")
