"""离线演示数据生成器（不依赖 cookie / 网络）。

核心技巧：先生成客观行情（指数→板块→个股 三级相关随机游走），再为每位大V在各板块设定
「能力概率 p」（看对的概率）。生成发言时，按 p 决定其立场与该股后续实际方向是否一致——
这样回测会自然还原出"顾序半导体准、消费差"这类差异，而不是无意义的 50%。

运行：python sample_data.py
"""
import json
import math
import random
from datetime import date, timedelta
import db

random.seed(20260813)

# ---------- 资产宇宙 ----------
INDEX_CODE = "000300"
INDEX_NAME = "沪深300"

SECTORS = {
    "SW半导体": "半导体", "SW消费": "消费", "SW新能源": "新能源", "SW医药": "医药",
    "SW煤炭": "煤炭", "SW券商": "券商", "SW地产": "地产链", "SW有色": "有色", "SW红利": "红利",
}
STOCKS = {
    "688981": ("中芯国际", "SW半导体"), "002371": ("北方华创", "SW半导体"), "600584": ("长电科技", "SW半导体"),
    "600519": ("贵州茅台", "SW消费"), "000858": ("五粮液", "SW消费"),
    "300750": ("宁德时代", "SW新能源"), "002594": ("比亚迪", "SW新能源"),
    "603259": ("药明康德", "SW医药"), "600276": ("恒瑞医药", "SW医药"),
    "601088": ("中国神华", "SW煤炭"), "600030": ("中信证券", "SW券商"),
    "600048": ("保利发展", "SW地产"), "601899": ("紫金矿业", "SW有色"), "600900": ("长江电力", "SW红利"),
}

# 每位大V：在各板块的「看对概率 p」（决定回测板块胜率）
PERSONS = {
    "18210001": {"name": "顾序", "desc": "半导体 / 算力", "sectors": [
        ("SW半导体", 0.80), ("SW消费", 0.40), ("SW新能源", 0.45), ("SW红利", 0.55)]},
    "33900002": {"name": "谢尔盖", "desc": "周期 / 新能源", "sectors": [
        ("SW新能源", 0.45), ("SW券商", 0.55), ("SW地产", 0.53), ("SW有色", 0.60), ("SW煤炭", 0.62)]},
    "58720003": {"name": "老talk", "desc": "宏观 / 策略", "sectors": [
        ("SW红利", 0.66), ("SW半导体", 0.63), ("SW医药", 0.58), ("SW消费", 0.55), ("SW券商", 0.60)]},
    "77120004": {"name": "林深", "desc": "医药 / 消费", "sectors": [
        ("SW医药", 0.62), ("SW消费", 0.58), ("SW新能源", 0.47), ("SW煤炭", 0.55)]},
}

STANCE_VERB = {
    "看多": ["景气上行，{s}回调即是布局窗口。", "二阶导转正，{s}估值要重估。", "国产化率提速，{s}后续空间打开。"],
    "看空": ["边际转弱，{s}反弹就是减仓机会。", "库存高企，{s}短期难有催化。", "产能出清遥遥无期，别追 {s}。"],
}


# ---------- 行情生成 ----------
def gen_trading_dates(n=52, end=date(2026, 8, 13)):
    ds = []
    d = end
    while len(ds) < n:
        if d.weekday() < 5:  # 周一到周五
            ds.append(d)
        d -= timedelta(days=1)
    return ds[::-1]


def gen_market(dates):
    """返回 {code: [(date_str, close)]} 以及写入 rows。"""
    series = {}
    rows = []

    # 指数
    idx = [3200.0]
    idx_rets = []
    for i in range(1, len(dates)):
        r = random.gauss(0.0003, 0.011)
        idx_rets.append(r)
        idx.append(idx[-1] * (1 + r))
    series[INDEX_CODE] = list(zip([d.isoformat() for d in dates], idx))
    for (ds, cl), r in zip(series[INDEX_CODE], [0.0] + idx_rets):
        rows.append({"date": ds, "code": INDEX_CODE, "name": INDEX_NAME, "close": round(cl, 2), "pct": round(r * 100, 3), "kind": "index"})

    # 板块（与指数相关 + 特异 drift）
    for code, name in SECTORS.items():
        beta = random.uniform(0.85, 1.25)
        drift = random.gauss(0.0, 0.0008)
        price = random.uniform(800, 2500)
        ser = []
        for i in range(len(dates)):
            if i == 0:
                r = 0.0
            else:
                r = beta * idx_rets[i - 1] + drift + random.gauss(0, 0.008)
            price *= (1 + r)
            ser.append((dates[i].isoformat(), price))
            rows.append({"date": dates[i].isoformat(), "code": code, "name": name,
                         "close": round(price, 2), "pct": round(r * 100, 3), "kind": "sector"})
        series[code] = ser

    # 个股（与板块相关 + 特异噪声）
    for code, (name, sec) in STOCKS.items():
        sec_ser = series[sec]
        beta = random.uniform(0.8, 1.2)
        price = random.uniform(15, 380)
        ser = []
        for i in range(len(dates)):
            if i == 0:
                r = 0.0
            else:
                r = beta * (sec_ser[i][1] / sec_ser[i - 1][1] - 1) + random.gauss(0, 0.012)
            price *= (1 + r)
            ser.append((dates[i].isoformat(), price))
            rows.append({"date": dates[i].isoformat(), "code": code, "name": name,
                         "close": round(price, 2), "pct": round(r * 100, 3), "kind": "stock"})
        series[code] = ser

    return series, rows


# ---------- 发言生成 ----------
def _dir_after(series, d_iso, k):
    lst = [(x[0], x[1]) for x in series]
    idx = None
    for i, (ds, _) in enumerate(lst):
        if ds <= d_iso:
            idx = i
        else:
            break
    if idx is None or idx + k >= len(lst):
        return None
    return _sign(lst[idx + k][1] / lst[idx][1] - 1)


def _sign(x):
    return 1 if x > 0 else (-1 if x < 0 else 0)


def gen_posts(dates, series):
    posts = []
    pid = 0
    n_dates = len(dates)
    recent = [d.isoformat() for d in dates[-3:]]  # 待验证窗口

    for xid, info in PERSONS.items():
        # 选取该人关注板块对应的股票池
        sec_pool = {sec: [c for c, (_, s) in STOCKS.items() if s == sec] for sec, _ in info["sectors"]}
        # 历史发言：分布在前段交易日（多数可验证）
        hist_dates = dates[: max(1, n_dates - 6)]
        n_hist = random.randint(28, 38)
        for _ in range(n_hist):
            d = random.choice(hist_dates)
            sec, p = random.choice(info["sectors"])
            stock = random.choice(sec_pool[sec])
            sname = STOCKS[stock][0]
            # 实际方向（5 日）
            direction = _dir_after(series[stock], d.isoformat(), 5)
            if direction is None:
                direction = random.choice([-1, 1])
            # 按能力 p 决定是否看对
            if random.random() < p:
                stance = "看多" if direction > 0 else "看空"
            else:
                stance = "看空" if direction > 0 else "看多"
            posts.append(_make_post(xid, info["name"], d.isoformat(), stock, sec, stance, sname))

        # 待验证发言：最近 3 个交易日各 2-3 条
        for d in recent:
            for _ in range(random.randint(2, 3)):
                sec, p = random.choice(info["sectors"])
                stock = random.choice(sec_pool[sec])
                sname = STOCKS[stock][0]
                direction = _dir_after(series[stock], d, 5)
                if direction is None:
                    direction = random.choice([-1, 1])
                if random.random() < p:
                    stance = "看多" if direction > 0 else "看空"
                else:
                    stance = "看空" if direction > 0 else "看多"
                posts.append(_make_post(xid, info["name"], d, stock, sec, stance, sname))

    return posts


def _make_post(xid, name, d_iso, stock, sec, stance, sname):
    pid = f"p{random.randint(100000,999999)}"
    post_type = random.choice(["original", "original", "longpost"])
    text = f"{name}：" + random.choice(STANCE_VERB[stance]).format(s=sname)
    # 对比/衬托标的（仅展示，不进回测）
    others = [c for c, (_, s) in STOCKS.items() if s != sec]
    contrast = []
    if random.random() < 0.4:
        cc = random.choice(others)
        contrast = [{"name": STOCKS[cc][0], "code": cc, "note": "对比衬托"}]
    return {
        "pid": pid, "user_xid": xid, "text": text, "created_at": d_iso, "post_type": post_type,
        "stance": stance, "horizon": random.choice(["短线", "中线", "长线"]), "sentiment": 0.5,
        "summary": text.replace(f"{name}：", ""),
        "subject_stocks": json.dumps([{"name": sname, "code": stock, "stance": stance,
                                       "sector": SECTORS[sec], "sector_code": sec}], ensure_ascii=False),
        "contrast_stocks": json.dumps(contrast, ensure_ascii=False),
        "sectors": json.dumps([SECTORS[sec]], ensure_ascii=False),
        "analyzed_at": d_iso, "model": "deepseek-chat",
    }


def seed():
    db.init_db()
    dates = gen_trading_dates()
    series, mrows = gen_market(dates)
    db.upsert_market(mrows)
    for xid, info in PERSONS.items():
        db.upsert_user(xid, info["name"], desc=info["desc"])
    posts = gen_posts(dates, series)
    for p in posts:
        db.upsert_post(**p)
    return len(posts), len(mrows)


if __name__ == "__main__":
    n_posts, n_mkt = seed()
    print(f"seeded: posts={n_posts}, market_rows={n_mkt}")
