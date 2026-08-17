"""证据账本归档：把「发言 + 人话解读 + 实际走势对比」归档进 evidence_ledger 表。

每晚跑一次（或手动 python archive_evidence.py 触发）。核心逻辑：
1. 找有观点（看多/看空）且已有解读的发言。
2. 按解读出的时间尺度映射预期窗口：短线→T+3、中线→T+7、长线→T+20、观察→T+7。
3. 从 events 表取实际走势：超额收益（个股 − 沪深300，剥离 Beta）+ 方向命中 + 回撤指标。
4. 归档进 evidence_ledger；manual_tag 留空，等人工打标签（AI 只给证据、不判对错）。

注意：只归档「窗口已闭合」（events.verified=1）的发言，未闭合的等下一轮。
"""
import json
import logging
from datetime import datetime

import db

log = logging.getLogger("archive_evidence")

# 时间尺度 → 预期兑现窗口（交易日）
HORIZON_WINDOW = {"短线": 3, "中线": 7, "长线": 20, "观察": 7}


def _excess_and_hit(e, stance, k):
    """取 k 窗口的超额收益（个股 − 基准）与超额方向命中。剥离 Beta，与主锚点口径一致。"""
    ret = e.get(f"ret_{k}d")
    idx = e.get(f"idx_ret_{k}d")
    if ret is None or idx is None:
        return None, None
    excess = ret - idx
    want_up = stance == "看多"
    hit = 1 if (want_up and excess > 0) or (not want_up and excess < 0) else 0
    return excess, hit


def archive_evidence(limit=None):
    """归档证据账本，返回归档条数。"""
    db.init_db()
    events = {e["pid"]: dict(e) for e in db.get_events()}
    posts = db.get_posts()
    n = 0
    for p in posts:
        p = dict(p)
        if p.get("stance") not in ("看多", "看空"):
            continue
        if not p.get("interpretation"):
            continue
        e = events.get(p["pid"])
        if not e or e.get("verified") != 1:
            continue  # 窗口未闭合，暂不归档
        try:
            interp = json.loads(p["interpretation"])
        except Exception:
            interp = {}
        horizon_value = (interp.get("horizon") or {}).get("value") or p.get("horizon") or "观察"
        k = HORIZON_WINDOW.get(horizon_value, 7)
        excess, hit = _excess_and_hit(e, p["stance"], k)
        db.upsert_evidence(
            pid=p["pid"], user_xid=p["user_xid"], created_at=p["created_at"],
            stance=p["stance"], horizon=horizon_value, expected_window_days=k,
            interpretation_snapshot=p["interpretation"],
            actual_ret=e.get(f"ret_{k}d"), idx_ret=e.get(f"idx_ret_{k}d"),
            excess_ret=excess, hit=hit,
            mdd=e.get("mdd"), peak_to_close=e.get("peak_to_close"),
            drawdown_speed=e.get("drawdown_speed"), limit_down_days=e.get("limit_down_days"),
            manual_tag=None,  # 留空待人工；upsert 用 COALESCE 保留已有人工标签
            archived_at=datetime.now().isoformat(timespec="seconds"),
        )
        n += 1
    log.info("证据归档完成：%d 条", n)
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("归档条数：", archive_evidence())
