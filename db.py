"""SQLite 数据层（本地单文件，ARM/x86 均可跑）。

七张表：
  users          被跟踪大V
  posts          发言（含单主体 subject_stocks 与对比 contrast_stocks）
  market_daily   指数/板块/个股日线
  events         发言→标的 的三级递进事件 + β剥离 + 命中
  predictions    抓到发言后自动生成的预测（核心层）
  backtest       分窗口历史命中率与 IC
  backtest_sector 分板块历史胜率
"""
import sqlite3
import threading
from config import DB_PATH


_CONN = None
_CONN_LOCK = threading.Lock()


class _KeepAliveConn(sqlite3.Connection):
    """单例连接：重写 close() 为 no-op，避免业务代码关闭共享连接。"""

    def close(self):
        pass


def get_conn() -> sqlite3.Connection:
    """获取 SQLite 连接（进程内单例，避免每次调用都重新连接云同步 NAS 盘上的 DB）。

    DB 位于 SynologyDrive 云同步盘，冷连接耗时可达数秒；复用单例连接后，
    除首次冷连外其余访问均为毫秒级。单例连接不关闭（close 置为 no-op），
    由进程退出时自动回收。多线程（请求线程 + 抓取 worker 线程）共享同一
    连接，配合 WAL + busy_timeout 保证并发安全。
    """
    global _CONN
    if _CONN is None:
        with _CONN_LOCK:
            if _CONN is None:
                conn = sqlite3.connect(DB_PATH, timeout=20.0,
                                       check_same_thread=False, factory=_KeepAliveConn)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=20000")
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except Exception:
                    pass
                _CONN = conn
    return _CONN


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            xid TEXT PRIMARY KEY,
            name TEXT,
            avatar TEXT,
            desc TEXT,
            added_at TEXT
        );

        CREATE TABLE IF NOT EXISTS posts (
            pid TEXT PRIMARY KEY,
            user_xid TEXT,
            text TEXT,
            created_at TEXT,
            post_type TEXT,            -- original/longpost/reply
            stance TEXT,               -- 看多/看空/中性
            horizon TEXT,              -- 短线/中线/长线/观察
            sentiment REAL,            -- -1~1
            summary TEXT,
            subject_stocks TEXT,       -- JSON: 主体标的（单主体，进回测）
            contrast_stocks TEXT,      -- JSON: 对比/衬托标的（仅展示，不进回测）
            sectors TEXT,              -- JSON: 主体涉及板块
            analyzed_at TEXT,
            model TEXT
        );

        CREATE TABLE IF NOT EXISTS market_daily (
            date TEXT,
            code TEXT,
            name TEXT,
            close REAL,
            pct REAL,
            kind TEXT,                 -- index/sector/stock
            high REAL,                -- 当日最高价（区间极值验证用）
            low REAL,                 -- 当日最低价
            PRIMARY KEY (date, code)
        );

        CREATE TABLE IF NOT EXISTS events (
            pid TEXT PRIMARY KEY,
            user_xid TEXT,
            created_at TEXT,
            stance TEXT,
            stock_code TEXT,
            sector_code TEXT,
            idx_code TEXT,
            ret_1d REAL, ret_3d REAL, ret_5d REAL, ret_7d REAL, ret_10d REAL, ret_20d REAL,
            idx_ret_1d REAL, idx_ret_3d REAL, idx_ret_5d REAL, idx_ret_7d REAL, idx_ret_10d REAL, idx_ret_20d REAL,
            sector_alpha_3d REAL, stock_alpha_3d REAL,
            verified INTEGER,
            hit_1d INTEGER, hit_3d INTEGER, hit_5d INTEGER, hit_7d INTEGER, hit_10d INTEGER, hit_20d INTEGER,
            peak_ret REAL,         -- 区间内最高价相对起点收益（峰值）
            trough_ret REAL,       -- 区间内最低价相对起点收益（谷值）
            proc_hit INTEGER,      -- 过程验证：区间内峰值/谷值方向是否触达观点方向
            computed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS predictions (
            pid TEXT PRIMARY KEY,
            user_xid TEXT,
            created_at TEXT,
            pred_stance TEXT,          -- 预测观点走向（看多/看空/中性）
            confidence REAL,           -- 置信度 0~1
            sectors TEXT,              -- 涉及板块 JSON
            signal TEXT,               -- 跟随 / 观望 / 反向
            hist_hit_rate REAL,        -- 该人历史整体命中率
            hist_sector_hit REAL,      -- 该板块历史命中率
            verified INTEGER,          -- 是否已有实际结果可验证
            actual_ret REAL,           -- 实际区间收益（验证后回填）
            hit INTEGER,               -- 预测是否命中
            computed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS backtest (
            user_xid TEXT,
            horizon TEXT,
            n INTEGER,
            hit_rate REAL,
            avg_ret REAL,
            ic REAL,
            computed_at TEXT,
            PRIMARY KEY (user_xid, horizon)
        );

        CREATE TABLE IF NOT EXISTS backtest_sector (
            user_xid TEXT,
            sector TEXT,
            n INTEGER,
            hit_rate REAL,
            avg_ret REAL,
            computed_at TEXT,
            PRIMARY KEY (user_xid, sector)
        );
        """
    )
    # 兼容已存在库：补齐新列（初次建表已含，此处幂等）
    for stmt in (
        "ALTER TABLE market_daily ADD COLUMN high REAL",
        "ALTER TABLE market_daily ADD COLUMN low REAL",
        "ALTER TABLE events ADD COLUMN ret_7d REAL",
        "ALTER TABLE events ADD COLUMN idx_ret_7d REAL",
        "ALTER TABLE events ADD COLUMN hit_7d INTEGER",
        "ALTER TABLE events ADD COLUMN peak_ret REAL",
        "ALTER TABLE events ADD COLUMN trough_ret REAL",
        "ALTER TABLE events ADD COLUMN proc_hit INTEGER",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass
    conn.commit()
    conn.close()


# ---------- 写入 ----------
def upsert_user(xid, name="", avatar="", desc=""):
    conn = get_conn()
    conn.execute(
        "INSERT INTO users(xid,name,avatar,desc,added_at) VALUES(?,?,?,?,datetime('now')) "
        "ON CONFLICT(xid) DO UPDATE SET name=excluded.name, avatar=excluded.avatar, desc=excluded.desc",
        (xid, name, avatar, desc),
    )
    conn.commit()
    conn.close()


def upsert_post(**kw):
    conn = get_conn()
    conn.execute(
        "INSERT INTO posts(pid,user_xid,text,created_at,post_type,stance,horizon,sentiment,summary,"
        "subject_stocks,contrast_stocks,sectors,analyzed_at,model) "
        "VALUES(:pid,:user_xid,:text,:created_at,:post_type,:stance,:horizon,:sentiment,:summary,"
        ":subject_stocks,:contrast_stocks,:sectors,:analyzed_at,:model) "
        "ON CONFLICT(pid) DO UPDATE SET post_type=excluded.post_type,stance=excluded.stance,"
        "horizon=excluded.horizon,sentiment=excluded.sentiment,summary=excluded.summary,"
        "subject_stocks=excluded.subject_stocks,contrast_stocks=excluded.contrast_stocks,"
        "sectors=excluded.sectors,analyzed_at=excluded.analyzed_at,model=excluded.model",
        kw,
    )
    conn.commit()
    conn.close()


def upsert_market(rows):
    """rows: list of dict(date,code,name,close,pct,kind,high,low)"""
    conn = get_conn()
    conn.executemany(
        "INSERT INTO market_daily(date,code,name,close,pct,kind,high,low) "
        "VALUES(:date,:code,:name,:close,:pct,:kind,:high,:low) "
        "ON CONFLICT(date,code) DO UPDATE SET close=excluded.close,pct=excluded.pct,name=excluded.name,"
        "high=excluded.high,low=excluded.low",
        rows,
    )
    conn.commit()
    conn.close()


def upsert_event(**kw):
    conn = get_conn()
    conn.execute(
        "INSERT INTO events(pid,user_xid,created_at,stance,stock_code,sector_code,idx_code,"
        "ret_1d,ret_3d,ret_5d,ret_7d,ret_10d,ret_20d,idx_ret_1d,idx_ret_3d,idx_ret_5d,idx_ret_7d,idx_ret_10d,idx_ret_20d,"
        "sector_alpha_3d,stock_alpha_3d,verified,hit_1d,hit_3d,hit_5d,hit_7d,hit_10d,hit_20d,"
        "peak_ret,trough_ret,proc_hit,computed_at) "
        "VALUES(:pid,:user_xid,:created_at,:stance,:stock_code,:sector_code,:idx_code,"
        ":ret_1d,:ret_3d,:ret_5d,:ret_7d,:ret_10d,:ret_20d,:idx_ret_1d,:idx_ret_3d,:idx_ret_5d,:idx_ret_7d,:idx_ret_10d,:idx_ret_20d,"
        ":sector_alpha_3d,:stock_alpha_3d,:verified,:hit_1d,:hit_3d,:hit_5d,:hit_7d,:hit_10d,:hit_20d,"
        ":peak_ret,:trough_ret,:proc_hit,:computed_at) "
        "ON CONFLICT(pid) DO UPDATE SET ret_1d=excluded.ret_1d,ret_3d=excluded.ret_3d,ret_5d=excluded.ret_5d,"
        "ret_7d=excluded.ret_7d,ret_10d=excluded.ret_10d,ret_20d=excluded.ret_20d,"
        "idx_ret_1d=excluded.idx_ret_1d,idx_ret_3d=excluded.idx_ret_3d,idx_ret_5d=excluded.idx_ret_5d,"
        "idx_ret_7d=excluded.idx_ret_7d,idx_ret_10d=excluded.idx_ret_10d,idx_ret_20d=excluded.idx_ret_20d,"
        "sector_alpha_3d=excluded.sector_alpha_3d,stock_alpha_3d=excluded.stock_alpha_3d,verified=excluded.verified,"
        "hit_1d=excluded.hit_1d,hit_3d=excluded.hit_3d,hit_5d=excluded.hit_5d,hit_7d=excluded.hit_7d,"
        "hit_10d=excluded.hit_10d,hit_20d=excluded.hit_20d,peak_ret=excluded.peak_ret,trough_ret=excluded.trough_ret,"
        "proc_hit=excluded.proc_hit,computed_at=excluded.computed_at",
        kw,
    )
    conn.commit()
    conn.close()


def upsert_prediction(**kw):
    conn = get_conn()
    conn.execute(
        "INSERT INTO predictions(pid,user_xid,created_at,pred_stance,confidence,sectors,signal,"
        "hist_hit_rate,hist_sector_hit,verified,actual_ret,hit,computed_at) "
        "VALUES(:pid,:user_xid,:created_at,:pred_stance,:confidence,:sectors,:signal,"
        ":hist_hit_rate,:hist_sector_hit,:verified,:actual_ret,:hit,:computed_at) "
        "ON CONFLICT(pid) DO UPDATE SET pred_stance=excluded.pred_stance,confidence=excluded.confidence,"
        "sectors=excluded.sectors,signal=excluded.signal,hist_hit_rate=excluded.hist_hit_rate,"
        "hist_sector_hit=excluded.hist_sector_hit,verified=excluded.verified,actual_ret=excluded.actual_ret,"
        "hit=excluded.hit,computed_at=excluded.computed_at",
        kw,
    )
    conn.commit()
    conn.close()


def upsert_backtest(rows):
    conn = get_conn()
    conn.executemany(
        "INSERT INTO backtest(user_xid,horizon,n,hit_rate,avg_ret,ic,computed_at) "
        "VALUES(:user_xid,:horizon,:n,:hit_rate,:avg_ret,:ic,:computed_at) "
        "ON CONFLICT(user_xid,horizon) DO UPDATE SET n=excluded.n,hit_rate=excluded.hit_rate,"
        "avg_ret=excluded.avg_ret,ic=excluded.ic,computed_at=excluded.computed_at",
        rows,
    )
    conn.commit()
    conn.close()


def upsert_backtest_sector(rows):
    conn = get_conn()
    conn.executemany(
        "INSERT INTO backtest_sector(user_xid,sector,n,hit_rate,avg_ret,computed_at) "
        "VALUES(:user_xid,:sector,:n,:hit_rate,:avg_ret,:computed_at) "
        "ON CONFLICT(user_xid,sector) DO UPDATE SET n=excluded.n,hit_rate=excluded.hit_rate,"
        "avg_ret=excluded.avg_ret,computed_at=excluded.computed_at",
        rows,
    )
    conn.commit()
    conn.close()


# ---------- 读取 ----------
def get_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_posts(user_xid=None, limit=None):
    conn = get_conn()
    if user_xid:
        sql = "SELECT * FROM posts WHERE user_xid=? ORDER BY created_at DESC"
        args = (user_xid,)
    else:
        sql = "SELECT * FROM posts ORDER BY created_at DESC"
        args = ()
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_post(pid):
    conn = get_conn()
    r = conn.execute("SELECT * FROM posts WHERE pid=?", (pid,)).fetchone()
    conn.close()
    return dict(r) if r else None


def count_posts():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    conn.close()
    return n


def get_events(user_xid=None):
    conn = get_conn()
    if user_xid:
        rows = conn.execute("SELECT * FROM events WHERE user_xid=? ORDER BY created_at", (user_xid,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM events ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_predictions(user_xid=None):
    conn = get_conn()
    if user_xid:
        rows = conn.execute("SELECT * FROM predictions WHERE user_xid=? ORDER BY created_at", (user_xid,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM predictions ORDER BY created_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_backtest(user_xid=None):
    conn = get_conn()
    if user_xid:
        rows = conn.execute("SELECT * FROM backtest WHERE user_xid=?", (user_xid,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM backtest").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_backtest_sector(user_xid):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM backtest_sector WHERE user_xid=?", (user_xid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_market_series(code):
    """返回按日期升序的 (date, close, high, low) 列表；high/low 可能为空（旧数据）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT date,close,high,low FROM market_daily WHERE code=? ORDER BY date", (code,)
    ).fetchall()
    conn.close()
    return [(r["date"], r["close"], r["high"], r["low"]) for r in rows]


def get_market_window(code, start_date, end_date):
    """返回 [start_date, end_date] 闭区间内的最高价 max(high)、最低价 min(low)。
    用于区间极值（峰值/谷值）验证。区间无数据返回 (None, None)。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT high, low FROM market_daily WHERE code=? AND date>=? AND date<=? "
        "AND high IS NOT NULL AND low IS NOT NULL ORDER BY date",
        (code, start_date, end_date),
    ).fetchall()
    conn.close()
    if not rows:
        return None, None
    hi = max(r["high"] for r in rows)
    lo = min(r["low"] for r in rows)
    return hi, lo


def get_market_name(code):
    conn = get_conn()
    r = conn.execute("SELECT name FROM market_daily WHERE code=? LIMIT 1", (code,)).fetchone()
    conn.close()
    return r["name"] if r else code


def get_trading_dates():
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT date FROM market_daily ORDER BY date").fetchall()
    conn.close()
    return [r["date"] for r in rows]


def market_codes_by_kind(kind):
    conn = get_conn()
    rows = conn.execute("SELECT DISTINCT code,name FROM market_daily WHERE kind=?", (kind,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_demo_data() -> None:
    """清空演示/错误数据：帖子 + 事件 + 预测 + 回测 + 行情。"""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM posts")
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM predictions")
        conn.execute("DELETE FROM backtest")
        conn.execute("DELETE FROM backtest_sector")
        conn.execute("DELETE FROM market_daily")
        conn.commit()
    finally:
        conn.close()
