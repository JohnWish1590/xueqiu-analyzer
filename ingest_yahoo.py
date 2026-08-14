#!/usr/bin/env python3
"""从雅虎财经拉取日线 OHLC 灌进 market_daily（覆盖 A股 / 港股 / 美股 / 指数）。

为什么用雅虎当主价格源：
  * 一口井通吃三市场 + 指数：A股 .SS/.SZ、港股 .HK、美股字母 ticker、沪深300(000300.SS)、
    创业板指(399006.SZ)、上证指数(000001.SS) 等。
  * 每日 bar 自带 open/high/low/close，盘中最高价与收盘价齐全，正好喂 verification 的
    peak_ret（区间最高）/ trough_ret（区间最低）/ ret_7d（T+7 收盘）。
  * 本机与用户真机（Clash 代理）均可直连，无需 MCP / 东方财富。

调用要点（踩过的坑）：
  * 雅虎 chart API 必须带浏览器 User-Agent，否则返回 429；批量拉取要串行 + 429 指数退避
    + query1/query2 双 host 轮换。
  * 雅虎不含 A股概念板块（稀土 / 半导体概念等），那部分仍走 akshare（sector_alpha 暂为空）。

用法：
    python ingest_yahoo.py            # 自动扫描 DB 全部标的 + 美股回填，灌库后重算事件
    python ingest_yahoo.py --dry      # 只打印将要拉取的标的，不实际请求
也可设置环境变量 YAHOO_PROXY（如 http://127.0.0.1:7890）走代理。
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import db
import symbol_mapper
import engine

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
PROXY = os.environ.get("YAHOO_PROXY", "")

# events 当前未引用、但雅虎可覆盖的美股，顺手回填历史（按需增删）
US_BACKFILL = ["GOOGL", "CSIQ", "NET", "NVDA", "TSLA", "AMD"]

# 雅虎覆盖不全 / 历史数据缺失的标的：跳过雅虎，保留既有 westock 数据。
# 例：399006（创业板指）雅虎 chart API 仅返回 1 个数据点，无法替代完整历史。
YAHOO_SKIP = {"399006"}


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    if PROXY:
        handler = urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})
        opener = urllib.request.build_opener(handler)
        return opener.open(req, timeout=15).read()
    return urllib.request.urlopen(req, timeout=15).read()


def fetch_yahoo(symbol, period1, period2, max_retry=5):
    """拉取单标的日线，返回 list of (date_str, open, high, low, close, name)。失败返回 []。"""
    for attempt in range(max_retry):
        host = HOSTS[attempt % len(HOSTS)]
        url = (f"{host}/v8/finance/chart/{symbol}"
               f"?interval=1d&period1={period1}&period2={period2}")
        try:
            raw = _http_get(url)
            data = json.loads(raw)
            r = data.get("chart", {}).get("result")
            if not r:
                err = data.get("chart", {}).get("error")
                if err:
                    print(f"  [warn] {symbol} 雅虎返回错误: {err}")
                return []
            res = r[0]
            ts = res.get("timestamp") or []
            q = res.get("indicators", {}).get("quote", [{}])[0]
            opens = q.get("open", [])
            highs = q.get("high", [])
            lows = q.get("low", [])
            closes = q.get("close", [])
            name = res.get("meta", {}).get("shortName", "")
            rows = []
            for i, t in enumerate(ts):
                c = closes[i]
                if c is None:
                    continue
                d = datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
                rows.append((d, opens[i], highs[i], lows[i], c, name))
            return rows
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = min(2 ** attempt, 30)
                print(f"  [429] {symbol} 限流，退避 {wait}s (第{attempt + 1}次)")
                time.sleep(wait)
                continue
            print(f"  [http {e.code}] {symbol}: {e}")
            return []
        except Exception as e:  # noqa: BLE001
            wait = min(2 ** attempt, 30)
            print(f"  [err] {symbol}: {e}, 退避 {wait}s")
            time.sleep(wait)
    return []


def collect_codes():
    """返回需要拉取的 {yahoo_symbol: (canonical_code, kind)}。

    按雅虎 ticker 去重（9992 / 9992.HK / 09992 都归一到 09992.HK），
    canonical_code 用 symbol_mapper 归一化后的规范代码入库 market_daily。
    """
    out = {}
    conn = db.get_conn()

    def _add(raw):
        d = symbol_mapper.normalize_raw_code(str(raw))
        if not d:
            return
        if d["code"] in YAHOO_SKIP:
            return
        yf = symbol_mapper.to_yahoo_symbol(d["code"], d["kind"])
        if yf and yf not in out:
            out[yf] = (d["code"], d["kind"])

    for r in conn.execute("SELECT DISTINCT stock_code FROM events WHERE stock_code IS NOT NULL"):
        _add(r["stock_code"])
    for r in conn.execute("SELECT DISTINCT idx_code FROM events WHERE idx_code IS NOT NULL"):
        _add(r["idx_code"])
    # posts 里引用的标的（含可能尚未生成 events 的）
    for p in db.get_posts():
        try:
            subs = json.loads(p.get("subject_stocks") or "[]")
        except Exception:
            subs = []
        for s in subs:
            code = s.get("code") or s.get("sector_code")
            if code:
                _add(code)
    # 美股回填
    for t in US_BACKFILL:
        _add(t)
    conn.close()
    return out


def _period_bounds():
    now = int(time.time())
    period2 = now
    # 历史深度：取最早发帖日往前 90 天；至少覆盖 3 年，确保任何历史事件都够用
    try:
        conn = db.get_conn()
        row = conn.execute("SELECT MIN(created_at) FROM posts").fetchone()
        conn.close()
        mn = row[0] if row else None
        if mn:
            # created_at 形如 '2026-07-31 13:10:14'
            dt = datetime.datetime.strptime(mn[:19], "%Y-%m-%d %H:%M:%S")
            period1 = int(dt.timestamp()) - 90 * 86400
        else:
            period1 = now - 3 * 365 * 86400
    except Exception:
        period1 = now - 3 * 365 * 86400
    return period1, period2


def main():
    dry = "--dry" in sys.argv
    db.init_db()
    codes = collect_codes()
    period1, period2 = _period_bounds()
    print(f"[yahoo] 待拉取 {len(codes)} 个标的（period1={datetime.datetime.utcfromtimestamp(period1).date()} "
          f"~ period2={datetime.datetime.utcfromtimestamp(period2).date()}）")
    for yf, (code, kind) in sorted(codes.items()):
        print(f"  - {code:10} -> {yf} ({kind})")
    if dry:
        return

    total = 0
    for yf, (code, kind) in sorted(codes.items()):
        rows = fetch_yahoo(yf, period1, period2)
        if not rows:
            print(f"  [skip] {code} ({yf}) 无数据")
            time.sleep(0.3)
            continue
        prev = None
        mrows = []
        name = ""
        for (d, o, h, lo, c, nm) in rows:
            name = nm or name
            pct = None
            if prev is not None and prev != 0:
                pct = (c - prev) / prev * 100.0
            prev = c
            mrows.append({
                "date": d, "code": code, "name": name,
                "close": c, "pct": pct,
                "kind": kind, "high": h, "low": lo,
            })
        db.upsert_market(mrows)
        total += len(mrows)
        print(f"  [ok] {code} ({yf}) {len(mrows)} 行, 最新 {mrows[-1]['date']} close={mrows[-1]['close']}")
        time.sleep(0.3)  # 礼貌限速

    print(f"[yahoo] 共写入 {total} 行")

    print("[yahoo] 重算事件/回测/预测...")
    res = engine.run_all()
    print("[yahoo] run_all:", res)


if __name__ == "__main__":
    main()
