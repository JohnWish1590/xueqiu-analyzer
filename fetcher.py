"""抓取 + 分析 + 回测 编排守护。

流程（每轮）：
  1. 取 cookie（cookie_provider）
  2. 对每位「已启用」跟踪用户：增量抓取（每 POLL_MINUTES）或全量回填（首次/手动）
  3. 每条新帖经 analyst 结构化分析（单主体识别）→ 入库
  4. 为涉及的主体标的补抓行情（market，akshare）
  5. engine.run_all() 重算三级递进事件、β 剥离、命中率/IC、预测

设计：所有网络/分析异常均被捕获并打日志，单条失败不影响整体；无 cookie / 未配置用户时守护安静退出。
"""
import json
import logging
import os
import threading
import time
from datetime import datetime

import config
import db
import xueqiu_client
import analyst
import engine

log = logging.getLogger("fetcher")

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fetcher_state.json")
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fetch_log.json")

# 后台自动轮询 worker
_worker_thread = None
_worker_stop = threading.Event()
_fetch_running = threading.Event()



def write_state(state: dict):
    """把抓取进度写入 data/fetcher_state.json，供前端轮询。"""
    try:
        state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def read_state() -> dict:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"running": False, "stage": "idle", "message": "", "current_user": "",
            "current_index": 0, "total_users": 0, "fetched_count": 0, "error": ""}


def _user_latest_post(xid):
    try:
        con = db.get_conn()
        r = con.execute("SELECT MAX(created_at) FROM posts WHERE user_xid=?", (str(xid),)).fetchone()
        con.close()
        return r[0] if r else None
    except Exception:
        return None


def update_fetch_log(xid, name, new_count):
    """记录单用户抓取结果：抓取时间、最新帖时间、本次新增数。供设置页显示「已抓取到几号」。"""
    try:
        data = {}
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        data[str(xid)] = {
            "name": name,
            "fetched_at": _now(),
            "last_post_at": _user_latest_post(xid) or "",
            "new_count": new_count,
        }
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("更新 fetch_log 失败: %s", e)


def read_fetch_log() -> dict:
    try:
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------
# 后台自动轮询 worker（方案 A：服务常驻，UI 控制启停）
def worker_running() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


def start_worker():
    """启动后台自动轮询：先全量回填一次，之后每 POLL_MINUTES 增量。"""
    global _worker_thread
    if worker_running():
        return False
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, args=(config.POLL_MINUTES,), daemon=True)
    _worker_thread.start()
    return True


def stop_worker():
    _worker_stop.set()
    return True


def _worker_loop(interval_minutes):
    log.info("自动轮询 worker 启动：间隔 %d 分钟", interval_minutes)
    # 首轮全量回填
    try:
        run_once(clear_first=False)
    except Exception as e:
        log.warning("worker 首轮回填异常（跳过）: %s", e)
    while not _worker_stop.is_set():
        if _worker_stop.wait(interval_minutes * 60):
            break
        try:
            cookie = _cookie_header()
            if not cookie:
                continue
            ids = [f["xid"] for f in _enabled_followed()]
            if ids:
                _run_poll(ids, cookie, _fetch_types())
                engine.run_all()
        except Exception as e:
            log.warning("worker 轮询异常（已跳过本轮）: %s", e)
    log.info("自动轮询 worker 已停止")


# --------------------------------------------------------------------------
def _enabled_followed():
    s = config.load_settings()
    return [f for f in s.get("followed", []) if f.get("enabled")]


def _fetch_types():
    s = config.load_settings()
    return set(s.get("fetch_types") or config.DEFAULT_FETCH_TYPES)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
def _store_post(p, user_xid, user_name):
    """分析并入库单条帖子；已存在则跳过。返回 True 表示新增。"""
    if db.get_post(p["id"]):
        return False
    res = analyst.analyze_post(p["text"], user_name=user_name)
    subj = res.get("subject")
    db.upsert_post(
        pid=p["id"], user_xid=user_xid, text=p["text"], created_at=p["created_at"],
        post_type=p["post_type"], stance=res["stance"], horizon=res["horizon"],
        sentiment=res["sentiment"], summary=res["summary"],
        subject_stocks=json.dumps([subj] if subj else [], ensure_ascii=False),
        contrast_stocks=json.dumps(res.get("contrast") or [], ensure_ascii=False),
        sectors=json.dumps(res.get("sectors") or [], ensure_ascii=False),
        analyzed_at=_now(), model=res.get("model"),
    )
    log.info("新增发言 %s @%s stance=%s subject=%s", p["id"], user_name,
             res["stance"], (subj or {}).get("name"))
    return True


def _collect_market_targets(posts):
    """从一批帖子汇总需要补抓行情的 (code/name) 与 板块名。"""
    stock_targets = {}   # code -> name
    sector_targets = set()
    for p in posts:
        post = db.get_post(p["id"])
        if not post:
            continue
        try:
            subs = json.loads(post["subject_stocks"] or "[]")
        except Exception:
            subs = []
        for s in subs:
            if s.get("code"):
                stock_targets[s["code"]] = s.get("name", "")
        try:
            secs = json.loads(post["sectors"] or "[]")
        except Exception:
            secs = []
        sector_targets.update(secs)
    return stock_targets, sector_targets


def _ensure_market(stock_targets, sector_targets, days=180):
    """为涉及标的补抓行情（akshare 懒加载；失败返回空，不阻断）。"""
    import market
    rows = []
    try:
        idx = market.fetch_index_daily(
            code=config.BENCHMARK_INDEX["code"],
            name=config.BENCHMARK_INDEX["name"], days=days)
        rows += idx
    except Exception as e:
        log.warning("基准指数行情获取失败: %s", e)
    for code, name in stock_targets.items():
        try:
            rows += market.fetch_stock_daily(code, name=name, days=days)
        except Exception as e:
            log.warning("个股 %s 行情获取失败: %s", code, e)
    for sec in sector_targets:
        try:
            rows += market.fetch_sector_daily_by_name(sec, days=days)
        except Exception as e:
            log.warning("板块 %s 行情获取失败: %s", sec, e)
    if rows:
        db.upsert_market(rows)
        log.info("行情已写入 %d 行", len(rows))
    return len(rows)


# --------------------------------------------------------------------------
def _pipeline(posts, user_xid, user_name):
    """对一批归一化帖子：入库 + 汇总行情目标。返回新增条数。"""
    new = 0
    stored = []
    for p in posts:
        if _store_post(p, user_xid, user_name):
            new += 1
            stored.append(p)
    if stored:
        stock_targets, sector_targets = _collect_market_targets(stored)
        _ensure_market(stock_targets, sector_targets)
    return new


def _run_backfill(ids, cookie, types, days=None):
    days = days or config.load_settings().get("backfill_days", config.DEFAULT_BACKFILL_DAYS)
    posts = xueqiu_client.fetch_backfill(ids, cookie, days=days, types=types)
    # 按 user_xid 归并（fetch_backfill 不带 user 信息，需回查 resolved）
    # 简化：以 followed 配置为主，逐用户回填更稳妥
    total = 0
    name_by_id = {f["xid"]: f.get("name", "") for f in _enabled_followed()}
    for f in _enabled_followed():
        uid = f["xid"]
        ups = xueqiu_client.fetch_backfill([uid], cookie, days=days, types=types)
        total += _pipeline(ups, uid, name_by_id.get(uid, ""))
    log.info("回填完成：新增 %d 条（范围 %d 天）", total, days)
    return total


def _run_poll(ids, cookie, types):
    name_by_id = {f["xid"]: f.get("name", "") for f in _enabled_followed()}
    total = 0
    for f in _enabled_followed():
        uid = f["xid"]
        # 增量：取最近 3 页，upsert 去重即只落新帖
        ups = []
        for pg in (1, 2, 3):
            batch = xueqiu_client.fetch_user_timeline(
                uid, cookie, page=pg, count=20, types=types)
            if not batch:
                break
            ups += batch
            if len(batch) < 20:
                break
        total += _pipeline(ups, uid, name_by_id.get(uid, ""))
    if total:
        log.info("增量轮询：新增 %d 条", total)
    return total


def run_once(clear_first=False, days=None, types=None):
    """手动触发一次：全量回填选中人员 + 重算。

    参数：
      clear_first: 是否清空现有帖子（默认 False，测试/普通数据共存）。
      days:        抓取时间范围（天）。None 时取 settings.backfill_days。
      types:       抓取发言类型集合（None = 全部类型，确保所选时间范围内不漏帖）。

    抓取过程实时写入 data/fetcher_state.json，供前端轮询。
    供设置页「立即抓取」按钮调用。
    """
    if _fetch_running.is_set():
        log.info("已有抓取在进行，本次忽略")
        return 0
    _fetch_running.set()
    try:
        write_state({"running": True, "stage": "准备中", "message": "正在准备抓取选中人员数据…",
                     "current_user": "", "current_index": 0, "total_users": 0,
                     "fetched_count": 0, "error": ""})
        cookie = _cookie_header()
        if not cookie:
            write_state({"running": False, "stage": "错误", "message": "未配置 cookie",
                         "error": "no_cookie"})
            return 0
        followed = _enabled_followed()
        if not followed:
            write_state({"running": False, "stage": "错误", "message": "未配置任何已启用的跟踪用户",
                         "error": "no_users"})
            return 0
        # 2026-08-13：测试数据与普通数据共存，不再默认清库
        if clear_first:
            try:
                db.clear_demo_data()
                log.info("已清空现有数据")
            except Exception as e:
                log.warning("清空数据失败: %s", e)
        # 用户显式抓取时默认抓全部类型（不漏帖）；未指定则按设置
        types = types if types is not None else None
        days = days if days is not None else config.load_settings().get("backfill_days", config.DEFAULT_BACKFILL_DAYS)
        base_posts = db.count_posts()
        total_users = len(followed)
        write_state({"running": True, "stage": "抓取中", "message": "正在抓取选中人员数据",
                     "total_users": total_users, "current_index": 0,
                     "fetched_count": db.count_posts() - base_posts, "current_user": ""})
        for i, f in enumerate(followed, 1):
            if _worker_stop.is_set():
                log.info("收到停止信号，抓取中断（已完成 %d/%d 位用户）", i - 1, total_users)
                break
            uid = f["xid"]
            name = f.get("name", "")
            write_state({"running": True, "stage": "抓取中",
                         "message": "正在抓取 %s (%d/%d)" % (name, i, total_users),
                         "current_user": name, "current_index": i, "total_users": total_users,
                         "fetched_count": db.count_posts() - base_posts})
            try:
                ups = xueqiu_client.fetch_backfill([uid], cookie, days=days, types=types,
                                                   stop_event=_worker_stop)
                new = _pipeline(ups, uid, name)
                update_fetch_log(uid, name, new)
                write_state({"running": True, "stage": "抓取中",
                             "message": "正在抓取 %s (%d/%d)" % (name, i, total_users),
                             "current_user": name, "current_index": i, "total_users": total_users,
                             "fetched_count": db.count_posts() - base_posts})
            except Exception as e:
                log.warning("用户 %s 抓取失败（跳过）: %s", name, e)
        try:
            engine.run_all()
            log.info("回测重算完成")
        except Exception as e:
            log.warning("回测重算失败: %s", e)
        total = db.count_posts() - base_posts
        write_state({"running": False, "stage": "完成",
                     "message": "抓取完成，新增 %d 条待验证" % total,
                     "total_users": total_users, "current_index": total_users,
                     "fetched_count": total, "current_user": ""})
        return total
    finally:
        _fetch_running.clear()


def run_daemon():
    """守护主循环：首次回填 → 每 POLL_MINUTES 增量。"""
    cookie = _cookie_header()
    if not cookie:
        log.warning("无有效 cookie，抓取守护未启动；请在设置页登录或粘贴 cookie。")
        return
    followed = _enabled_followed()
    if not followed:
        log.warning("未配置任何已启用的跟踪用户，抓取守护未启动。")
        return
    ids = [f["xid"] for f in followed]
    types = _fetch_types()
    log.info("抓取守护启动：%d 位用户，每 %d 分钟增量", len(followed), config.POLL_MINUTES)

    # 首次全量回填
    _run_backfill(ids, cookie, types)
    engine.run_all()

    while True:
        time.sleep(config.POLL_MINUTES * 60)
        try:
            _run_poll(ids, cookie, types)
            engine.run_all()
        except Exception as e:
            log.warning("轮询异常（已跳过本轮）: %s", e)


def _cookie_header():
    try:
        import cookie_provider
        hdr = cookie_provider.get_cookie_header()
        if hdr and cookie_provider.cookie_status(hdr):
            return hdr
    except Exception as e:
        log.warning("获取 cookie 失败: %s", e)
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_once()
