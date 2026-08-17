"""行情获取层（依赖 akshare，需联网）。

本模块只负责从 akshare 拉取真实行情，并统一转换为
db.upsert_market 所需的 dict 列表结构：
    {date, code, name, close, pct, kind}
其中 kind ∈ {"index", "sector", "stock"}，与 market_daily 表一致。

注意：所有函数依赖 akshare 与网络。akshare 偶发网络中断或返回字段变动时，
各取数函数会打印警告并返回空列表 []，不会导致主程序崩溃。

依赖：仅标准库 + akshare（akshare 可能未安装，用 ensure_akshare 给出安装提示，
本模块不会自动 pip install）。
"""

from datetime import datetime, timedelta

# 分析层常用的中文板块名集合（analyst 会用这些名字映射到概念板）
COMMON_SECTORS = {
    "半导体", "新能源", "消费", "白酒", "医药", "创新药", "煤炭", "有色", "钢铁",
    "券商", "银行", "红利", "地产", "军工", "光伏", "锂电", "芯片", "汽车",
    "人工智能", "机器人", "算力", "猪肉", "农业", "化工", "电力", "保险", "建材",
}

# akshare 常用返回列名 → 标准字段的映射（处理 日期/收盘/涨跌幅 这类中文列）
_CN_DATE = "日期"
_CN_CLOSE = "收盘"
_CN_PCT = "涨跌幅"


def _ak():
    """惰性导入 akshare，返回 ak 模块对象。"""
    import akshare as ak
    return ak


def ensure_akshare():
    """确保 akshare 已安装；未安装时抛出清晰异常提示，不自动安装。"""
    try:
        return _ak()
    except ImportError:
        raise ImportError(
            "未检测到 akshare。请先安装：\n    pip install akshare\n"
            "（本模块不会自动执行 pip install，请手动安装后重试。）"
        )


def _date_range(days: int):
    """返回 (start_date, end_date) 的 YYYYMMDD 字符串，用于 akshare 查询。"""
    end = datetime.today()
    start = end - timedelta(days=days)
    start_s = start.strftime("%Y%m%d")
    end_s = end.strftime("%Y%m%d")
    return start_s, end_s


def _norm_row(row, code: str, name: str, kind: str) -> dict:
    """将 akshare 的某一行（dict 形式）映射为标准结构。"""
    date_val = row.get(_CN_DATE)
    if date_val is None:
        return None
    # akshare 日期可能是 datetime / Timestamp / 字符串
    if isinstance(date_val, (datetime,)):
        date_s = date_val.strftime("%Y-%m-%d")
    else:
        date_s = str(date_val)[:10]
    try:
        close = float(row.get(_CN_CLOSE))
    except (TypeError, ValueError):
        close = None
    try:
        pct = float(row.get(_CN_PCT))
    except (TypeError, ValueError):
        pct = None
    return {
        "date": date_s,
        "code": code,
        "name": name,
        "close": close,
        "pct": pct,
        "kind": kind,
    }


def _cn_index_to_yahoo(code: str) -> str:
    """把 akshare 的指数代码(如 000300) 转成雅虎代码(000300.SS)。"""
    if code.endswith((".SS", ".SZ", ".SH")):
        return code
    if code.startswith("6") or code.startswith("0"):
        return code + ".SS"
    if code.startswith("3"):
        return code + ".SZ"
    return code + ".SS"


def fetch_index_daily_yahoo(code: str = "000300", name: str = "沪深300", days: int = 180) -> list:
    """雅虎兜底取 A 股指数日线（当 akshare/东财不可用时）。返回同结构 list，失败返回 []。

    注：雅虎对个别 A 股指数历史稀疏(可能仅返回最近一个交易日)，属已知限制；
    能取到多少真实数据就写入多少，绝不编造。
    """
    try:
        import urllib.request, json as _json, datetime as _dt
        tk = _cn_index_to_yahoo(code)
        end = _dt.datetime.today()
        start = end - _dt.timedelta(days=days)
        p1 = int(start.timestamp())
        p2 = int(end.timestamp())
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}"
               f"?interval=1d&period1={p1}&period2={p2}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = _json.load(urllib.request.urlopen(req, timeout=20))
        res = d["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        closes = q.get("close", [])
        highs = q.get("high", [])
        lows = q.get("low", [])
        out = []
        prev = None
        for i, t in enumerate(ts):
            c = closes[i] if i < len(closes) else None
            if c is None:
                prev = c
                continue
            day = _dt.datetime.fromtimestamp(t, _dt.UTC).strftime("%Y-%m-%d")
            pct = None
            if prev is not None:
                try:
                    pct = round((c - prev) / prev * 100, 4)
                except Exception:
                    pct = None
            rec = {
                "date": day,
                "code": code,
                "name": name,
                "close": float(c),
                "pct": pct,
                "kind": "index",
                "high": (float(highs[i]) if i < len(highs) and highs[i] is not None else None),
                "low": (float(lows[i]) if i < len(lows) and lows[i] is not None else None),
            }
            out.append(rec)
            prev = c
        return out
    except Exception as e:
        print(f"[market] fetch_index_daily_yahoo({name}/{code}) 失败: {e}")
        return []


def fetch_index_daily(code: str = "000300", name: str = "沪深300", days: int = 180) -> list:
    """获取 A 股指数日线：优先 akshare(东财)，失败/无数据时回退雅虎。

    返回 list[dict(date,code,name,close,pct,kind="index")]，失败返回 []。
    常见指数代码：沪深300=000300，上证指数=000001，创业板指=399006。
    """
    try:
        ak = ensure_akshare()
        start, end = _date_range(days)
        df = ak.index_zh_a_hist(symbol=code, period="daily",
                                start_date=start, end_date=end, adjust="")
        if df is not None and len(df) > 0:
            out = []
            for _, r in df.iterrows():
                rec = _norm_row(r.to_dict(), code, name, "index")
                if rec:
                    out.append(rec)
            return out
        print(f"[market] 指数 {name}({code}) akshare 无数据，尝试 Yahoo 兜底")
    except Exception as e:
        print(f"[market] akshare 取指数 {name}/{code} 失败，尝试 Yahoo: {e}")
    return fetch_index_daily_yahoo(code, name, days)


def fetch_stock_daily(code: str, name: str = "", days: int = 180) -> list:
    """获取 A 股个股日线（akshare: stock_zh_a_hist，前复权 qfq，需联网）。

    返回 list[dict(date,code,name,close,pct,kind="stock")]，失败返回 []。
    """
    try:
        ak = ensure_akshare()
        start, end = _date_range(days)
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start, end_date=end, adjust="qfq")
        if df is None or len(df) == 0:
            print(f"[market] 个股 {name or code}({code}) 无数据返回")
            return []
        out = []
        for _, r in df.iterrows():
            rec = _norm_row(r.to_dict(), code, name or code, "stock")
            if rec:
                out.append(rec)
        return out
    except Exception as e:
        print(f"[market] fetch_stock_daily({name or code}/{code}) 失败: {e}")
        return []


def fetch_concept_daily(symbol: str, name: str = "", days: int = 180) -> list:
    """获取东方财富概念板块日K（akshare: stock_board_concept_hist_em，需联网）。

    参数 symbol 为概念板代码（由 resolve_concept_code 得到）。
    返回 list[dict(date,code:symbol,name,close,pct,kind="sector")]，失败返回 []。
    """
    try:
        ak = ensure_akshare()
        df = ak.stock_board_concept_hist_em(symbol=symbol)
        if df is None or len(df) == 0:
            print(f"[market] 概念板 {name or symbol}({symbol}) 无数据返回")
            return []
        # 概念板接口可能不带时间范围参数，按 days 截断到最近 N 个交易日
        out = []
        for _, r in df.iterrows():
            rec = _norm_row(r.to_dict(), symbol, name or symbol, "sector")
            if rec:
                out.append(rec)
        if days and len(out) > days:
            out = out[-days:]
        return out
    except Exception as e:
        print(f"[market] fetch_concept_daily({name or symbol}/{symbol}) 失败: {e}")
        return []


def resolve_concept_code(name: str):
    """按中文板块名模糊匹配概念板代码（akshare: stock_board_concept_name_em，需联网）。

    返回匹配到的代码字符串；未匹配到返回 None。
    """
    try:
        ak = ensure_akshare()
        df = ak.stock_board_concept_name_em()
        if df is None or len(df) == 0:
            print("[market] 概念板列表为空")
            return None
        # 常见列：板块名称 / 代码
        name_col = "板块名称" if "板块名称" in df.columns else df.columns[0]
        code_col = "代码" if "代码" in df.columns else df.columns[1]
        key = name.strip()
        # 先精确匹配，再包含匹配
        matched = df[df[name_col] == key]
        if len(matched) == 0:
            matched = df[df[name_col].str.contains(key, na=False)]
        if len(matched) == 0:
            print(f"[market] 未找到概念板: {name}")
            return None
        return str(matched.iloc[0][code_col])
    except Exception as e:
        print(f"[market] resolve_concept_code({name}) 失败: {e}")
        return None


def fetch_sector_daily_by_name(name: str, days: int = 180) -> list:
    """按中文板块名取概念板日线：resolve_concept_code + fetch_concept_daily。

    返回 list[dict(date,code,name,close,pct,kind="sector")]，失败返回 []。
    """
    code = resolve_concept_code(name)
    if not code:
        return []
    return fetch_concept_daily(symbol=code, name=name, days=days)


def fetch_snapshot_stocks(codes: list) -> dict:
    """取全市场实时快照，按 codes 过滤返回 {code: 当日涨跌幅%}（akshare: stock_zh_a_spot_em，需联网）。

    用于盘中每小时快照。失败返回 {}。
    """
    try:
        ak = ensure_akshare()
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            print("[market] 全市场快照为空")
            return {}
        # 常见列：代码 / 涨跌幅；兼容 符号
        code_col = "代码" if "代码" in df.columns else df.columns[0]
        pct_col = "涨跌幅" if "涨跌幅" in df.columns else df.columns[-1]
        sub = df[df[code_col].isin(codes)]
        result = {}
        for _, r in sub.iterrows():
            c = str(r[code_col])
            try:
                result[c] = float(r[pct_col])
            except (TypeError, ValueError):
                result[c] = None
        return result
    except Exception as e:
        print(f"[market] fetch_snapshot_stocks 失败: {e}")
        return {}


if __name__ == "__main__":
    # 自测：仅验证导入与常量，不联网
    print("COMMON_SECTORS:", sorted(COMMON_SECTORS))
