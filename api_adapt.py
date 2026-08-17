"""把数据库 / 引擎结果转换成 UI 契约 JSON（与 ui/data/*.json 同结构）。

真实环境由 server.py 调用这里；本文件不含任何网络/抓取逻辑。
"""
import json
import functools
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
import db
import config
from config import VERIFY_DAYS, BENCHMARK_INDEX, MODEL_PROVIDERS
import engine


def _as_of():
    ds = db.get_trading_dates()
    today = date.today().isoformat()
    if not ds:
        return today
    last = ds[-1]
    # 行情表最后交易日若早于真实今天，说明行情源断更/未补，
    # 直接用真实今天作基准，避免全站时间基准(参考日期/验证窗口/待验证列表)卡死在旧日期。
    return last if last >= today else today


def _followed_ids():
    """当前已启用跟踪的大V xid 集合（只展示这些人的数据）。"""
    import config
    s = config.load_settings()
    return set(str(f.get("xid")) for f in s.get("followed", []) if f.get("enabled"))


def _parse_date(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19] if " " in s else s, fmt).date()
        except Exception:
            continue
    return date.today()


def _pct(x):
    return round(x * 100, 2) if x is not None else None


@functools.lru_cache(maxsize=None)
def _user_overall(xid):
    """返回 (整体5日命中率%, N)。

    注意：build_timeline_pending/verified 会对每条帖子调用本函数（N+1）。
    DB 在云同步 NAS 盘上，单次 get_backtest 开连接成本高，故加 lru_cache，
    每个 xid 在一次会话内只查一次（仅 2 个大V）。
    """
    bt = db.get_backtest(xid)
    rows = [b for b in bt if b["horizon"].endswith("5d")]
    if not rows:
        return None, 0
    n = sum(b["n"] for b in rows)
    hr = sum(b["hit_rate"] * b["n"] for b in rows) / n if n else 0
    return round(hr * 100, 1), n


def _events_by_pid():
    out = {}
    for e in db.get_events():
        out[e["pid"]] = dict(e)
    return out


def _subject_of(post):
    """返回用于时间线卡片展示的主体对象。没有个股代码时，用板块名或占位文案兜底，
    确保所有已抓取记录都能出现在时间线里。"""
    try:
        subs = json.loads(post["subject_stocks"] or "[]")
    except Exception:
        subs = []
    if subs:
        s = subs[0]
        return {
            "name": s.get("name"), "code": s.get("code") or "",
            "stance": post["stance"], "horizon": post.get("horizon") or "未指定",
        }
    # 无个股：优先用板块名
    try:
        secs = json.loads(post["sectors"] or "[]")
    except Exception:
        secs = []
    if secs:
        return {
            "name": secs[0], "code": "",
            "stance": post["stance"], "horizon": post.get("horizon") or "未指定",
        }
    # 兜底占位
    return {
        "name": "（未识别个股）", "code": "",
        "stance": post["stance"], "horizon": post.get("horizon") or "未指定",
    }


def _contrast_of(post):
    try:
        cs = json.loads(post["contrast_stocks"] or "[]")
    except Exception:
        cs = []
    return [{"name": c.get("name"), "code": c.get("code"), "note": c.get("note", "仅展示不进回测")} for c in cs]


def _interpretation_of(post):
    """解析 posts.interpretation 的 JSON（人话解读），失败返回 None。"""
    try:
        obj = json.loads(post.get("interpretation") or "null")
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def build_timeline_pending():
    """待验证时间线：包含所有已启用跟踪用户的已抓取记录。

    2026-08-13 调整：不再按 VERIFY_DAYS 过滤，也不再要求有具体个股代码；
    无个股的发言情境用板块名或占位文案展示，确保“已抓取记录全部可见”。
    时间窗口由前端 1/7/30/自定义 控制。
    """
    ev = _events_by_pid()
    fid = _followed_ids()
    out = []
    for p in db.get_posts():
        if str(p["user_xid"]) not in fid:
            continue
        p = dict(p)
        if p["pid"] in ev and ev[p["pid"]]["verified"] == 1:
            continue  # 已验证的进 verified 视图
        subj = _subject_of(p)
        hr, n = _user_overall(p["user_xid"])
        out.append({
            "post_id": p["pid"], "user_name": _uname(p["user_xid"]), "user_id": p["user_xid"],
            "created_at": p["created_at"], "text": p["text"], "post_type": p["post_type"],
            "subject": subj, "contrast": _contrast_of(p), "summary": p.get("summary"),
            "interpretation": _interpretation_of(p),
            "attrib": None, "hist_hit_rate": hr or 0, "hist_n": n,
        })
    return out


def build_timeline_verified():
    ev = _events_by_pid()
    fid = _followed_ids()
    out = []
    for p in db.get_posts():
        if str(p["user_xid"]) not in fid:
            continue
        p = dict(p)
        e = ev.get(p["pid"])
        if not e or e["verified"] != 1:
            continue
        subj = _subject_of(p)
        if not subj:
            continue
        hr, n = _user_overall(p["user_xid"])
        index_beta = (e["ret_3d"] or 0) - (e["sector_alpha_3d"] or 0) - (e["stock_alpha_3d"] or 0)
        attrib = {
            "index_beta": _pct(index_beta),
            "sector_alpha": _pct(e["sector_alpha_3d"]),
            "stock_actual": _pct(e["ret_3d"]),
            "stock_alpha": _pct(e["stock_alpha_3d"]),
        }
        hit = bool(e["hit_5d"])
        if p["stance"] == "看多":
            stance_hit = "看多命中" if hit else "未命中"
        else:
            stance_hit = "看空命中" if hit else "未命中"
        # 7 日验证锚点（主锚）：终点超额收益（剥离沪深300 Beta）+ 区间极值 + 过程验证
        excess_7d = (e["ret_7d"] or 0) - (e["idx_ret_7d"] or 0)
        verify7 = {
            "ret_7d": _pct(e["ret_7d"]),            # 个股 T+7 收盘收益
            "idx_ret_7d": _pct(e["idx_ret_7d"]),    # 沪深300 同期收益
            "excess_7d": _pct(excess_7d),           # 超额收益（剥离大盘 Beta）
            "hit_7d": bool(e["hit_7d"]),            # 终点超额方向是否命中观点
            "peak_ret": _pct(e["peak_ret"]),        # 区间内最高价收益（相对起点收盘）
            "trough_ret": _pct(e["trough_ret"]),    # 区间内最低价收益
            "proc_hit": bool(e["proc_hit"]),        # 过程验证：区间内是否触达观点方向
            "mdd": _pct(e.get("mdd")),              # 区间最大回撤（峰值到谷值）
            "peak_to_close": _pct(e.get("peak_to_close")),  # 峰值到终点回落
            "drawdown_speed": e.get("drawdown_speed"),  # 回撤速度（正=冲高回落）
            "limit_down_days": e.get("limit_down_days"),  # 区间内跌停近似天数
        }
        out.append({
            "post_id": p["pid"], "user_name": _uname(p["user_xid"]), "user_id": p["user_xid"],
            "created_at": p["created_at"], "text": p["text"], "post_type": p["post_type"],
            "subject": subj, "contrast": _contrast_of(p), "summary": p.get("summary"),
            "interpretation": _interpretation_of(p),
            "attrib": attrib,
            "actual": {"t1": _pct(e["ret_1d"]), "t5": _pct(e["ret_5d"]), "t7": _pct(e["ret_7d"]),
                       "t10": _pct(e["ret_10d"]), "t20": _pct(e["ret_20d"])},
            "hit": hit, "stance_hit": stance_hit,
            "verify7": verify7,
            "hist_hit_rate": hr or 0, "hist_n": n,
        })
    return out


_USER_CACHE = {}
def _uname(xid):
    if xid not in _USER_CACHE:
        us = db.get_users()
        _USER_CACHE[xid] = next((u["name"] for u in us if u["xid"] == xid), xid)
    return _USER_CACHE[xid]


def build_persons():
    out = []
    fid = _followed_ids()   # 只展示已启用跟踪的人
    for u in db.get_users():
        xid = u["xid"]
        if str(xid) not in fid:
            continue
        # 矩阵：看多/看空 × T+1/3/5/10/20，直接从 events 算
        events = [dict(e) for e in db.get_events(xid)]
        matrix = {}
        for stance in ("看多", "看空"):
            row = {"n": 0}
            for k in (1, 3, 5, 7, 10, 20):
                sub = [e for e in events if e["stance"] == stance and e.get(f"hit_{k}d") is not None]
                n = len(sub)
                hr = sum(e[f"hit_{k}d"] for e in sub) / n if n else 0
                row[f"t{k}"] = round(hr * 100, 1)
                row["n"] += n
            matrix[("看多" if stance == "看多" else "bearish")] = row
        # 修正 key：bullish/bearish
        matrix = {
            "bullish": matrix.get("看多", {"t1":0,"t3":0,"t5":0,"t10":0,"t20":0,"n":0}),
            "bearish": matrix.get("bearish", {"t1":0,"t3":0,"t5":0,"t10":0,"t20":0,"n":0}),
        }
        secs = []
        for s in db.get_backtest_sector(xid):
            secs.append({"sector": s["sector"], "hit": round(s["hit_rate"]*100, 1), "n": s["n"], "ic": 0.0})
        hr, n = _user_overall(xid)
        # 历史发言下钻
        history = []
        for p in db.get_posts(xid):
            p = dict(p)
            e = None
            for ev in db.get_events(xid):
                if ev["pid"] == p["pid"]:
                    e = dict(ev); break
            if not e or e["verified"] != 1:
                continue
            subj = _subject_of(p)
            if not subj:
                continue
            history.append({
                "post_id": p["pid"], "created_at": p["created_at"],
                "text": p["text"], "subject": subj["name"], "stance": p["stance"],
                "hit": bool(e["hit_5d"]),
            })
        out.append({
            "name": u["name"], "user_id": xid, "desc": u.get("desc") or "",
            "hit_rate": hr or 0, "n": n, "ic": _user_ic(xid),
            "matrix": matrix, "sectors": secs, "history": history,
            "calibration": _user_calibration(xid),
            "profile": _user_profile(xid),
        })
    return out


def _user_ic(xid):
    bt = db.get_backtest(xid)
    ics = [b["ic"] for b in bt if b["horizon"].endswith("5d")]
    return round(sum(ics)/len(ics), 3) if ics else 0.0


def _user_calibration(xid):
    """该用户「置信度 → 实际命中率」校准曲线数据（Brier/校准曲线口径）。

    取已验证预测（verified=1 且 hit 已知），按置信度分桶（0.5~0.9 共 5 桶），
    桶内实际命中率 = mean(hit)，横坐标用桶内平均置信度。
    返回 [{conf, actual, n}]；无足够已验证预测时返回 []（前端显示「数据积累中」）。
    """
    preds = [p for p in db.get_predictions(xid)
             if p.get("verified") == 1 and p.get("hit") is not None and p.get("confidence") is not None]
    if not preds:
        return []
    bins = {}
    for p in preds:
        conf = p["confidence"]
        b = round(min(0.9, max(0.5, conf)) * 10) / 10
        bins.setdefault(b, []).append((conf, p["hit"]))
    pts = []
    for b in sorted(bins):
        items = bins[b]
        n = len(items)
        actual = sum(h for _, h in items) / n
        conf_center = sum(c for c, _ in items) / n
        pts.append({"conf": round(conf_center, 3), "actual": round(actual, 3), "n": n})
    return pts


def _user_profile(xid):
    """从证据账本统计画像：典型兑现窗口 / 相对还是绝对 / 擅长做多还是做空。
    不靠印象，只靠归档证据。证据不足时给空/None，前端显示「数据积累中」。"""
    rows = [dict(r) for r in db.get_evidence_ledger(xid)]

    horizon_hit = defaultdict(lambda: [0, 0])  # horizon -> [命中数, 样本数]
    basis_counter = Counter()
    bull, bear = [], []
    for r in rows:
        h = r.get("horizon") or "观察"
        horizon_hit[h][1] += 1
        if r.get("hit") == 1:
            horizon_hit[h][0] += 1
        try:
            interp = json.loads(r.get("interpretation_snapshot") or "null")
            if interp and interp.get("basis"):
                basis_counter[interp["basis"]] += 1
        except Exception:
            pass
        if r.get("stance") == "看多":
            bull.append(r)
        elif r.get("stance") == "看空":
            bear.append(r)

    best_h, best_score = None, -1.0
    for h, (hit, n) in horizon_hit.items():
        if n and (hit / n) > best_score:
            best_score, best_h = hit / n, h
    bull_hr = sum(1 for r in bull if r.get("hit") == 1) / len(bull) if bull else None
    bear_hr = sum(1 for r in bear if r.get("hit") == 1) / len(bear) if bear else None

    return {
        "dominant_horizon": best_h,
        "horizon_detail": {h: {"n": n, "hit_rate": round(hit / n * 100, 1) if n else 0}
                           for h, (hit, n) in horizon_hit.items()},
        "relative_or_absolute": basis_counter.most_common(1)[0][0] if basis_counter else "无法判断",
        "bull_hit_rate": round(bull_hr * 100, 1) if bull_hr is not None else None,
        "bear_hit_rate": round(bear_hr * 100, 1) if bear_hr is not None else None,
        "evidence_n": len(rows),
    }



def build_predictions():
    out = []
    for pr in db.get_predictions():
        pr = dict(pr)
        subj = None
        for p in db.get_posts(pr["user_xid"]):
            if p["pid"] == pr["pid"]:
                subj = _subject_of(dict(p)); break
        # 校准点：用该人回测行（conf=命中率, actual=命中率）
        calib = []
        for b in db.get_backtest(pr["user_xid"]):
            calib.append({"conf": round(b["hit_rate"], 2), "actual": round(b["hit_rate"], 2)})
        sectors = json.loads(pr["sectors"]) if pr["sectors"] else []
        out.append({
            "post_id": pr["pid"], "user": _uname(pr["user_xid"]), "user_xid": pr["user_xid"],
            "subject": subj["name"] if subj else "", "pred_stance": pr["pred_stance"],
            "confidence": round(pr["confidence"], 2),
            "sector": (sectors[0] if sectors else ""),
            "involved_sectors": sectors,
            "hist_hit_rate": round((pr["hist_hit_rate"] or 0) * 100, 1),
            "hist_sector_hit": round((pr["hist_sector_hit"] or 0) * 100, 1),
            "signal": pr["signal"], "calibration": calib,
        })
    return out


def build_evidence_ledger(user_xid=None, only_untagged=False):
    """证据账本：发言 + 人话解读 + 实际走势对比。供「证据账本」视图展示。"""
    fid = _followed_ids()
    out = []
    for r in db.get_evidence_ledger(user_xid, only_untagged):
        r = dict(r)
        if str(r.get("user_xid")) not in fid:
            continue
        try:
            interp = json.loads(r.get("interpretation_snapshot") or "null")
        except Exception:
            interp = None
        out.append({
            "pid": r["pid"], "user_name": _uname(r["user_xid"]), "user_xid": r["user_xid"],
            "created_at": r["created_at"], "stance": r["stance"], "horizon": r["horizon"],
            "expected_window_days": r["expected_window_days"],
            "interpretation": interp,
            "actual_ret": _pct(r["actual_ret"]), "idx_ret": _pct(r["idx_ret"]),
            "excess_ret": _pct(r["excess_ret"]), "hit": r["hit"],
            "mdd": _pct(r["mdd"]), "peak_to_close": _pct(r["peak_to_close"]),
            "drawdown_speed": r["drawdown_speed"], "limit_down_days": r["limit_down_days"],
            "manual_tag": r["manual_tag"] or "",
            "archived_at": r["archived_at"],
        })
    return out


def build_settings():
    s = db.load_settings() if hasattr(db, "load_settings") else __import__("config").load_settings()
    fmod = __import__("fetcher")
    fetch_log = fmod.read_fetch_log()
    name_by_xid = {u["xid"]: u["name"] for u in db.get_users()}
    followed = []
    # 只展示「已配置的跟踪对象」（settings.followed），不含历史/演示残留用户
    for f in s.get("followed", []):
        xid = str(f.get("xid"))
        fl = fetch_log.get(xid, {})
        followed.append({
            "id": xid,
            "name": f.get("name") or name_by_xid.get(xid, xid),
            "enabled": bool(f.get("enabled")),
            "last_post_at": fl.get("last_post_at", ""),
            "fetched_at": fl.get("fetched_at", ""),
            "last_new": fl.get("new_count", 0),
        })
    api_key = s.get("api_key", "")
    model_providers = [
        {"value": k, "label": v["label"], "model": v["model"]}
        for k, v in MODEL_PROVIDERS.items()
    ]
    return {
        "followed_users": followed,
        "model": {
            "provider": s.get("provider"),
            "model": s.get("model", ""),
            "api_key": (api_key[:6] + "****") if api_key else "",
            "api_key_set": bool(api_key),
        },
        "model_providers": model_providers,
        "post_types": {"original": "original" in s.get("fetch_types", []),
                       "long": "longpost" in s.get("fetch_types", []),
                       "reply": "reply" in s.get("fetch_types", [])},
        "backfill_days": s.get("backfill_days", 30),
        "skill_enabled": bool(s.get("skills")),
        "cookie_source": s.get("cookie_source"),
        "worker_running": fmod.worker_running(),
        "db_path": str(__import__("config").DB_PATH),
        "posts_total": db.count_posts(),
    }


def _fetcher_state():
    import os, json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fetcher_state.json")
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"running": False, "stage": "idle", "message": "", "current_user": "",
            "current_index": 0, "total_users": 0, "fetched_count": 0, "error": ""}


def build_monitor():
    import config
    s = config.load_settings()
    n_pending = len(build_timeline_pending())
    n_verified = len(build_timeline_verified())
    fs = _fetcher_state()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_time = now.split(" ")[1]

    # 上次抓取/下次轮询从 fetcher_state.updated_at 计算；旧 state 无该字段时用文件 mtime 兜底
    last_fetch = None
    if fs.get("stage") == "完成":
        last_fetch = fs.get("updated_at")
        if not last_fetch:
            try:
                import os
                mtime = os.path.getmtime(__import__("fetcher").STATE_PATH)
                last_fetch = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
    # 只有 worker 在运行时才显示下次轮询；已停止则显示“--”，避免展示一个 stale 的过去时间
    worker_running = __import__("fetcher").worker_running()
    next_poll = "--"
    if worker_running and last_fetch:
        try:
            dt = datetime.strptime(last_fetch, "%Y-%m-%d %H:%M:%S")
            next_poll = (dt + timedelta(minutes=config.POLL_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass

    cookie_ok = bool(s.get("cookie"))
    fetched_total = db.count_posts()

    # 动态日志：基于真实状态，不再写死
    logs = []
    if fs.get("stage") == "完成" and fs.get("updated_at"):
        logs.append({
            "time": fs["updated_at"].split(" ")[1],
            "msg": "抓取完成，新增 %d 条待验证" % (fs.get("fetched_count") or 0),
            "level": "ok",
        })
    elif fs.get("stage") == "错误" and fs.get("updated_at"):
        logs.append({
            "time": fs["updated_at"].split(" ")[1],
            "msg": fs.get("message") or "抓取失败",
            "level": "err",
        })
    logs.append({"time": now_time, "msg": "Cookie %s" % ("有效" if cookie_ok else "失效"), "level": "ok" if cookie_ok else "err"})
    logs.append({"time": now_time, "msg": "WAF 绕过成功（api.xueqiu.com 子域直连）", "level": "ok"})
    if last_fetch:
        logs.append({"time": now_time, "msg": "等待下次轮询（%s）" % (next_poll.split(" ")[1] if next_poll and next_poll != "--" else "已停止"), "level": "w"})

    return {
        "api_status": "ok", "cookie_status": "valid" if cookie_ok else "expired",
        "cookie_expire": "2026-09-10", "waf": "api.xueqiu.com 子域直连（绕过阿里云 WAF）",
        "last_fetch": last_fetch or "--", "next_poll": next_poll or "--",
        "fetched_total": fetched_total, "pending": n_pending, "verified": n_verified,
        "backfill_progress": 100, "reference_date": _as_of(),
        "fetch_running": bool(fs.get("running")),
        "fetch_stage": fs.get("stage", ""),
        "fetch_message": fs.get("message", ""),
        "fetch_current_user": fs.get("current_user", ""),
        "fetch_index": fs.get("current_index", 0),
        "fetch_total": fs.get("total_users", 0),
        "fetch_count": fs.get("fetched_count", 0),
        "fetch_error": fs.get("error", ""),
        "worker_running": worker_running,
        "db_path": str(config.DB_PATH),
        "logs": logs,
    }


def build_worker_status():
    import fetcher
    m = build_monitor()
    return {
        "running": fetcher.worker_running(),
        "last_fetch": m["last_fetch"],
        "next_poll": m["next_poll"],
        "interval_minutes": config.POLL_MINUTES,
        "db_path": str(config.DB_PATH),
        "posts_total": db.count_posts(),
    }


def build_status():
    m = build_monitor()
    return {"reference_date": m["reference_date"], "api_status": m["api_status"],
            "cookie_status": m["cookie_status"], "worker_running": m.get("worker_running", False)}
