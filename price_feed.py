"""真实行情回填层（price_feed）。

把 posts 中引用过的标的代码 / 板块名，归一化后抓取真实日线，写入 market_daily，
供 engine.rebuild_events / run_backtest 计算 β 剥离收益与命中率。

数据源：
  * 东方财富 kline 接口（A股 / 港股 / 指数）：直接 urllib 调用，稳定且无需 akshare。
    本环境 akshare 的 requests 偶发被重置，故主路径走直连 API；akshare 仅作概念板兜底。
  * yfinance（美股 GOOGL/NET/CSIQ 等）：仅在抓取美股时调用，需外网（走系统代理）。

设计要点：
  * 网络失败一律返回 [] / 跳过，绝不抛异常中断主流程。
  * 东方财富为国内源，抓取时临时绕过系统代理（本机 Clash 等可能不通），抓取后恢复，
    以免干扰 server 进程里 xueqiu 抓取用的代理设置。
  * 全部请求带超时 + 多次退避重试，避免沙箱/网络抖动导致整批失败。
  * 已存在的 code 默认跳过（增量更新）；force=True 全量重抓。

用法：
  python price_feed.py            # 增量回填所有被引用的标的
  python price_feed.py --force     # 全量重抓
  python price_feed.py --days 600  # 回看窗口（交易日近似）
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from contextlib import contextmanager

import db
import config
import symbol_mapper


_EM_KLINE = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?fields1=f1,f2,f3&fields2=f51,f53,f59"
    "&ut=fa5fd1943c7b386f172d6893dbfba10b&klt=101&fqt=1"
    "&secid={secid}&beg={beg}&end={end}"
)
_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


@contextmanager
def _bypass_proxy():
    """临时清空代理环境变量（仅东方财富国内源抓取时用），退出后还原，不影响进程内其它请求。"""
    saved = {k: os.environ.get(k) for k in _PROXY_VARS}
    for k in _PROXY_VARS:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _http_get_json(url, timeout=15, headers=None):
    if headers is None:
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_em_kline(secid, kind="stock", days=600, tries=5):
    """东方财富 kline 直连。返回 list[dict(date,code,name,close,pct,kind)]，失败返回 []。

    klines 字段顺序：f51 日期, f53 收盘, f59 涨跌幅%。
    """
    end = datetime.today()
    beg = end - timedelta(days=days)
    beg_s = beg.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    url = _EM_KLINE.format(secid=secid, beg=beg_s, end=end_s)
    last_err = None
    for attempt in range(tries):
        try:
            with _bypass_proxy():
                d = _http_get_json(url, timeout=15)
            kd = d.get("data") if isinstance(d, dict) else None
            if not kd or not kd.get("klines"):
                # rc!=1 或空数据：可能代码暂无历史，非网络问题，直接返回空
                if isinstance(d, dict) and d.get("rc") not in (1, None) and d.get("data") is None:
                    last_err = f"rc={d.get('rc')} msg={d.get('msg')}"
                    # 空数据也直接返回，不重试
                    return []
                return []
            name = kd.get("name") or ""
            code = kd.get("code") or secid.split(".", 1)[-1]
            out = []
            for line in kd["klines"]:
                parts = line.split(",")
                if len(parts) < 9:
                    continue
                date_s = parts[0][:10]
                try:
                    close = float(parts[2])
                    pct = float(parts[8])
                except (ValueError, TypeError):
                    continue
                out.append({
                    "date": date_s, "code": code, "name": name,
                    "close": close, "pct": pct, "kind": kind,
                })
            return out
        except Exception as e:  # 网络/解析异常，退避后重试
            last_err = repr(e)
            time.sleep(1.2 * (attempt + 1))
    print(f"[price_feed] EM {secid} 失败（{tries}次重试）: {last_err}")
    return []


def fetch_yf(ticker, kind="stock", days=600, tries=3):
    """yfinance 抓取美股日线。失败返回 []。需外网（走系统代理）。"""
    try:
        import yfinance as yf
    except ImportError:
        print(f"[price_feed] 未安装 yfinance，跳过美股 {ticker}（pip install yfinance）")
        return []
    end = datetime.today()
    start = end - timedelta(days=days)
    last_err = None
    for attempt in range(tries):
        try:
            df = yf.Ticker(ticker).history(start=start.strftime("%Y-%m-%d"),
                                           end=end.strftime("%Y-%m-%d"), interval="1d")
            if df is None or len(df) == 0:
                return []
            out = []
            closes = df["Close"].dropna()
            for i in range(len(closes)):
                dt = closes.index[i]
                date_s = dt.strftime("%Y-%m-%d")
                close = float(closes.iloc[i])
                pct = None
                if i > 0:
                    prev = float(closes.iloc[i - 1])
                    if prev:
                        pct = (close / prev - 1.0) * 100.0
                out.append({
                    "date": date_s, "code": ticker, "name": ticker,
                    "close": close, "pct": round(pct, 4) if pct is not None else None,
                    "kind": kind,
                })
            return out
        except Exception as e:
            last_err = repr(e)
            time.sleep(1.5 * (attempt + 1))
    print(f"[price_feed] yfinance {ticker} 失败: {last_err}")
    return []


def _descriptors_to_fetch(force, days):
    """构造待抓取描述符列表：被引用的标的 + 基准指数（若未含）。"""
    raw = symbol_mapper.collect_from_db()
    descs = symbol_mapper.build_descriptors(raw)

    # 确保基准指数必抓（β 剥离依赖 idx_code）
    bench = config.BENCHMARK_INDEX
    bench_code = bench.get("code")
    has_bench = any(d["code"] == bench_code for d in descs)
    if not has_bench and bench_code:
        descs.insert(0, {
            "code": bench_code, "name_hint": bench.get("name", ""),
            "kind": "index", "source": "em",
            "em_secid": ("1." if bench_code in symbol_mapper.SH_INDEX else "0.") + bench_code
                if bench_code in symbol_mapper.INDEX_CODES else "1." + bench_code,
            "yf_symbol": "", "concept": "",
        })

    # 过滤：已存在且非 force 的跳过（增量）
    if not force:
        existing = set()
        for r in db.get_conn().execute("SELECT DISTINCT code FROM market_daily").fetchall():
            existing.add(r[0])
        db.get_conn().close()
        filtered = []
        for d in descs:
            if d["code"] in existing:
                continue
            filtered.append(d)
        descs = filtered
    return descs


def backfill_missing(force=False, days=600):
    """回填所有被引用标的的真实日线到 market_daily。返回汇总 dict。"""
    descs = _descriptors_to_fetch(force, days)
    summary = {"total": len(descs), "fetched": 0, "rows": 0, "failed": 0, "skipped_sector": 0}
    print(f"[price_feed] 待抓取标的 {len(descs)} 个（force={force}, days={days}）")

    for d in descs:
        src = d["source"]
        if src == "em":
            rows = fetch_em_kline(d["em_secid"], kind=d["kind"], days=days)
        elif src == "yf":
            rows = fetch_yf(d["yf_symbol"], kind=d["kind"], days=days)
        elif src == "em_concept":
            # 概念板需先解析板块代码，akshare 在本环境不稳；暂跳过并提示。
            summary["skipped_sector"] += 1
            print(f"[price_feed] 跳过概念板块（需 akshare 解析）: {d['concept']}")
            continue
        else:
            summary["failed"] += 1
            continue

        if not rows:
            summary["failed"] += 1
            continue
        db.upsert_market(rows)
        summary["fetched"] += 1
        summary["rows"] += len(rows)
        print(f"[price_feed] ✓ {d['code']} ({d.get('name_hint') or d.get('concept') or ''}) "
              f"{len(rows)} 行  [{rows[0]['date']} ~ {rows[-1]['date']}]")

    print(f"[price_feed] 完成：成功 {summary['fetched']} 个 / 失败 {summary['failed']} 个 "
          f"/ 跳过概念板 {summary['skipped_sector']} 个 / 共 {summary['rows']} 行")
    return summary


if __name__ == "__main__":
    force = "--force" in sys.argv
    no_rebuild = "--no-rebuild" in sys.argv
    days = 600
    for i, a in enumerate(sys.argv):
        if a == "--days" and i + 1 < len(sys.argv):
            try:
                days = int(sys.argv[i + 1])
            except ValueError:
                pass
    print("=== price_feed backfill ===")
    s = backfill_missing(force=force, days=days)
    print("=== done ===", json.dumps(s, ensure_ascii=False))
    if not no_rebuild:
        # 抓完真实行情后立即重算：事件 β 剥离收益 + 命中率/IC 回测 + 预测校准，
        # 这样一次命令即可让「已验证」卡片从 -- 变为真实数字、校准曲线出现样本。
        print("=== 重算事件/回测/预测 (engine.run_all) ===")
        import engine
        r = engine.run_all()
        print("=== run_all ===", r)
