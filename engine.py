"""分析引擎（UI 无关）：发言 → 个股 → 板块 → 大盘 三级递进事件 + β 剥离归因 + 命中率/IC 回测 + 预测。

设计原则（与已定稿功能树一致）：
  * 单主体识别：每条发言只产出一个主体事件（其余对比/衬托标的仅展示、不进回测）。
  * β 剥离：个股实际涨跌 = 大盘β + 板块α + 个股α；回测只看 板块α + 个股α（发言的真实增量信息）。
  * 无未来函数：验证窗口只用发言时点之后的数据。
  * 小样本必报 N：命中率一律带样本量。
  * 预测层核心：抓到发言自动预测走向 + 置信度 + 挂历史胜率/擅长板块 + 跟随/观望/反向信号。
"""
import json
import math
from datetime import datetime, date
from config import VERIFY_DAYS, BENCHMARK_INDEX, HORIZONS
import db


# ---------- 基础工具 ----------
def _parse_date(s: str) -> date:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if " " in s else s, fmt).date()
        except Exception:
            continue
    return date.today()


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _series_after(series, d: date, k: int):
    """返回从 d 收盘起第 k 个交易日的累计收益率；越界或找不到返回 None。
    series: [(date_str, close)] 升序。"""
    if not series:
        return None
    # 找 d 或最后一个 <= d 的索引
    idx = None
    for i, (ds, _) in enumerate(series):
        if _parse_date(ds) <= d:
            idx = i
        else:
            break
    if idx is None:
        return None
    tgt = idx + k
    if tgt >= len(series):
        return None
    base = series[idx][1]
    if base == 0:
        return None
    return series[tgt][1] / base - 1.0


# ---------- 事件重建（三级递进 + β 剥离） ----------
def rebuild_events(conn=None, as_of: date = None):
    own = conn is None
    c = conn or db.get_conn()
    as_of = as_of or db.get_trading_dates()
    if isinstance(as_of, list):
        as_of = _parse_date(as_of[-1]) if as_of else date.today()
    as_of = _parse_date(str(as_of))

    # 缓存行情序列
    cache = {}

    def series(code):
        if code not in cache:
            cache[code] = db.get_market_series(code)
        return cache[code]

    idx_code = BENCHMARK_INDEX["code"]
    idx_series = series(idx_code)

    posts = c.execute(
        "SELECT * FROM posts WHERE subject_stocks IS NOT NULL AND subject_stocks != '[]' "
        "AND stance IN ('看多','看空') ORDER BY created_at"
    ).fetchall()

    n_built = 0
    for p in posts:
        p = dict(p)
        try:
            subs = json.loads(p["subject_stocks"] or "[]")
        except Exception:
            subs = []
        if not subs:
            continue
        sub = subs[0]  # 单主体
        stock_code = sub.get("code")
        sector_code = sub.get("sector_code")
        stance = p["stance"]
        ed = _parse_date(p["created_at"])
        s_series = series(stock_code)
        sec_series = series(sector_code) if sector_code else None

        rets = {k: _series_after(s_series, ed, k) for k in (1, 3, 5, 10, 20)}
        idx_rets = {k: _series_after(idx_series, ed, k) for k in (1, 3, 5, 10, 20)}
        sec_rets = {k: _series_after(sec_series, ed, k) for k in (1, 3, 5, 10, 20)} if sec_series else {}

        if rets[5] is None or idx_rets[5] is None:
            continue  # 窗口未闭合，暂不建事件

        # β 剥离（以 3 日窗口展示四宫格；以 5 日做验证锚点）
        sec_ret_3 = sec_rets.get(3)
        sector_alpha_3d = (sec_ret_3 - idx_rets[3]) if sec_ret_3 is not None else None
        stock_alpha_3d = (rets[3] - sec_ret_3) if sec_ret_3 is not None else None

        # 命中判定：个股实际方向与观点一致
        hits = {}
        for k in (1, 3, 5, 10, 20):
            r = rets[k]
            if r is None:
                hits[k] = None
            else:
                want_up = stance == "看多"
                hits[k] = 1 if (want_up and r > 0) or (not want_up and r < 0) else 0

        # 验证条件：距离 as_of >= VERIFY_DAYS 且 5 日窗口闭合
        verified = 1 if (as_of - ed).days >= VERIFY_DAYS else 0

        db.upsert_event(
            pid=p["pid"], user_xid=p["user_xid"], created_at=p["created_at"], stance=stance,
            stock_code=stock_code, sector_code=sector_code, idx_code=idx_code,
            ret_1d=rets[1], ret_3d=rets[3], ret_5d=rets[5], ret_10d=rets[10], ret_20d=rets[20],
            idx_ret_1d=idx_rets[1], idx_ret_3d=idx_rets[3], idx_ret_5d=idx_rets[5],
            idx_ret_10d=idx_rets[10], idx_ret_20d=idx_rets[20],
            sector_alpha_3d=sector_alpha_3d, stock_alpha_3d=stock_alpha_3d,
            verified=verified,
            hit_1d=hits[1], hit_3d=hits[3], hit_5d=hits[5], hit_10d=hits[10], hit_20d=hits[20],
            computed_at=datetime.now().isoformat(timespec="seconds"),
        )
        n_built += 1

    if own:
        c.close()
    return n_built


# ---------- 回测（命中率矩阵 + 分板块胜率 + IC） ----------
def run_backtest(conn=None):
    own = conn is None
    c = conn or db.get_conn()
    events = c.execute("SELECT * FROM events WHERE verified=1").fetchall()
    events = [dict(e) for e in events]

    # 整体：user_xid × stance × 窗口
    groups = {}
    for e in events:
        key = (e["user_xid"], e["stance"])
        groups.setdefault(key, []).append(e)

    bt_rows = []
    for (uxid, stance), evs in groups.items():
        for k in HORIZONS:
            col = f"hit_{k}d"
            ret_col = f"ret_{k}d"
            sub = [e for e in evs if e.get(col) is not None]
            n = len(sub)
            if n == 0:
                continue
            hits = sum(e[col] for e in sub)
            hr = hits / n
            avg_ret = sum((e.get(ret_col) or 0) for e in sub) / n
            # IC = mean(pred_sign * actual_sign)，与该行窗口 k 一致
            ic_vals = []
            for e in sub:
                rk = e.get(ret_col)
                if rk is None:
                    continue
                ps = 1 if e["stance"] == "看多" else -1
                ic_vals.append(ps * _sign(rk))
            ic = sum(ic_vals) / len(ic_vals) if ic_vals else 0.0
            bt_rows.append({
                "user_xid": uxid, "horizon": f"{stance}_{k}d", "n": n,
                "hit_rate": round(hr, 4), "avg_ret": round(avg_ret, 5),
                "ic": round(ic, 4), "computed_at": datetime.now().isoformat(timespec="seconds"),
            })
    db.upsert_backtest(bt_rows)

    # 分板块：user_xid × sector（取 5 日窗口）
    sec_groups = {}
    for e in events:
        if not e.get("sector_code"):
            continue
        sname = db.get_market_name(e["sector_code"])
        sec_groups.setdefault((e["user_xid"], sname), []).append(e)
    sec_rows = []
    for (uxid, sname), evs in sec_groups.items():
        sub = [e for e in evs if e.get("hit_5d") is not None]
        n = len(sub)
        if n == 0:
            continue
        hits = sum(e["hit_5d"] for e in sub)
        avg_ret = sum((e.get("ret_5d") or 0) for e in sub) / n
        sec_rows.append({
            "user_xid": uxid, "sector": sname, "n": n,
            "hit_rate": round(hits / n, 4), "avg_ret": round(avg_ret, 5),
            "computed_at": datetime.now().isoformat(timespec="seconds"),
        })
    db.upsert_backtest_sector(sec_rows)

    if own:
        c.close()
    return len(bt_rows), len(sec_rows)


# ---------- 预测层（核心） ----------
def compute_prediction(c, post, backtest_map, sector_map):
    """对一条发言生成预测。post 为 dict。返回 predictions 行 dict。"""
    try:
        subs = json.loads(post.get("subject_stocks") or "[]")
    except Exception:
        subs = []
    if not subs:
        return None
    sub = subs[0]
    stance = post["stance"]
    uxid = post["user_xid"]
    sector = sub.get("sector") or (db.get_market_name(sub.get("sector_code")) if sub.get("sector_code") else "")

    # 历史整体命中率（取该 stance 5 日，否则任意可用窗口）
    hr_user = None
    for hk in (f"{stance}_5d", f"{stance}_10d", f"{stance}_3d"):
        if hk in backtest_map.get(uxid, {}):
            hr_user = backtest_map[uxid][hk]["hit_rate"]
            break
    # 板块命中率
    hr_sec = sector_map.get((uxid, sector), {}).get("hit_rate")

    avail = [x for x in (hr_user, hr_sec) if x is not None]
    avg_hit = sum(avail) / len(avail) if avail else 0.5
    # 置信度：以 0.5 为中轴映射，能力越强越偏离
    confidence = _clamp(0.5 + (avg_hit - 0.5) * 1.15, 0.05, 0.95)

    # 信号：跟随 / 观望 / 反向（非无脑跟）
    if confidence >= 0.62 and (hr_sec or 0) >= 0.58:
        signal = "跟随"
    elif confidence <= 0.42:
        signal = "反向"
    else:
        signal = "观望"

    return {
        "pid": post["pid"], "user_xid": uxid, "created_at": post["created_at"],
        "pred_stance": stance, "confidence": round(confidence, 3),
        "sectors": post.get("sectors") or json.dumps([sector], ensure_ascii=False),
        "signal": signal,
        "hist_hit_rate": round(hr_user, 4) if hr_user is not None else None,
        "hist_sector_hit": round(hr_sec, 4) if hr_sec is not None else None,
        "verified": 0, "actual_ret": None, "hit": None,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


def generate_predictions_for_pending(conn=None):
    """为「待验证」发言（距 as_of 窗口内、未闭合）生成预测并入库。"""
    own = conn is None
    c = conn or db.get_conn()
    as_of = db.get_trading_dates()
    as_of = _parse_date(as_of[-1]) if as_of else date.today()

    # 载入回测/分板块映射
    bt = c.execute("SELECT * FROM backtest").fetchall()
    bt_map = {}
    for r in bt:
        r = dict(r)
        bt_map.setdefault(r["user_xid"], {})[r["horizon"]] = r
    sec = c.execute("SELECT * FROM backtest_sector").fetchall()
    sec_map = {}
    for r in sec:
        r = dict(r)
        sec_map[(r["user_xid"], r["sector"])] = r

    posts = c.execute(
        "SELECT * FROM posts WHERE subject_stocks IS NOT NULL AND subject_stocks != '[]' "
        "AND stance IN ('看多','看空')"
    ).fetchall()
    n = 0
    for p in posts:
        p = dict(p)
        ed = _parse_date(p["created_at"])
        # 待验证 = 距 as_of <= 默认展示窗口（用 3 天为锚，与 VERIFY_DAYS 一致）
        if (as_of - ed).days > VERIFY_DAYS:
            continue
        pred = compute_prediction(c, p, bt_map, sec_map)
        if pred:
            db.upsert_prediction(**pred)
            n += 1
    if own:
        c.close()
    return n


# ---------- 一键运行 ----------
def run_all():
    db.init_db()
    nb = rebuild_events()
    nbt, nsec = run_backtest()
    npred = generate_predictions_for_pending()
    return {"events": nb, "backtest_rows": nbt, "sector_rows": nsec, "predictions": npred}


if __name__ == "__main__":
    print(run_all())
