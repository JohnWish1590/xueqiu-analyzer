"""标的代码 / 板块名 → 统一可抓取描述符。

职责：把 posts.subject_stocks 里五花八门的原始代码（A股 6 位 / 港股 00700·9992·9992.HK /
美股 GOOGL / 指数 000300·399006 / 板块中文名）归一化成 price_feed 能直接用的描述符：

    {
      "code":      "标准化代码（入库 market_daily 用的 code，保证同一标的唯一）",
      "name_hint": "展示用名称（可空，抓取时以接口返回为准）",
      "kind":      "stock" | "index" | "sector",
      "source":    "em"      -> 东方财富 kline（A股/港股/指数）
                   "yf"      -> yfinance（美股）
                   "em_concept" -> 东方财富概念板（按中文名解析）",
      "em_secid":  "东方财富 secid，如 1.600519 / 116.09992 / 0.399006（source=em 时必填）",
      "yf_symbol":"yfinance 代码，如 GOOGL（source=yf 时必填）",
      "concept":   "中文板块名（source=em_concept 时必填）",
    }

注意：
  * 本模块**纯本地、零网络**，只做字符串归一与规则映射，可在任何环境 import。
  * 港股代码归一为 5 位（9992 -> 09992，9992.HK -> 09992）。
  * 指数用「已知指数代码表」优先判定，避免被 A股 前缀规则误判（如 000300 虽以 0 开头却是沪市指数）。
"""

# ---------- 指数代码表（精确判定，避免被前缀规则误伤） ----------
SH_INDEX = {
    "000001",  # 上证指数
    "000016",  # 上证50
    "000300",  # 沪深300
    "000688",  # 科创50
    "000903",  # 中证100
    "000905",  # 中证500
    "000906",  # 中证800
    "000010",  # 上证180
    "000009",  # 上证380
    "000852",  # 中证1000
}
SZ_INDEX = {
    "399001",  # 深证成指
    "399006",  # 创业板指
    "399005",  # 中小板指
    "399100",  # 深证100
    "399004",  # 深证60
    "399106",  # 深证综指
    "399303",  # 国证2000
    "399330",  # 深证100等权
    "399673",  # 创业板50
}
INDEX_CODES = SH_INDEX | SZ_INDEX

# 已知指数中文名 → 代码（用于板块/指数名解析兜底）
INDEX_NAME_TO_CODE = {
    "上证指数": "000001", "沪深300": "000300", "沪深300指数": "000300",
    "上证50": "000016", "科创50": "000688", "中证500": "000905",
    "中证1000": "000852", "创业板指": "399006", "深证成指": "399001",
    "中小板指": "399005",
}


def _is_int(s):
    return bool(s) and s.isdigit()


def normalize_raw_code(raw: str):
    """把单条原始代码/名称归一为描述符；无法识别返回 None。"""
    if not raw:
        return None
    s = raw.strip().upper()
    if not s:
        return None

    has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in s)

    # 1) 中文（板块名 / 指数名）—— 必须最先判定，因为中文 isalpha() 为真、
    #    且像「沪深300」含数字会被误判为港股。
    if has_cn:
        if s in INDEX_NAME_TO_CODE:
            digits = INDEX_NAME_TO_CODE[s]
            prefix = "1" if digits in SH_INDEX else "0"
            return {
                "code": digits, "name_hint": s, "kind": "index",
                "source": "em", "em_secid": prefix + "." + digits,
                "yf_symbol": "", "concept": "",
            }
        return {
            "code": s, "name_hint": s, "kind": "sector",
            "source": "em_concept", "em_secid": "", "yf_symbol": "", "concept": s,
        }

    # 2) 港股变体：00700 / 09992 / 9992 / 9992.HK（纯数字、长度 3~5、且不含字母）
    hk = s
    if hk.endswith(".HK"):
        hk = hk[:-3]
    hk_digits = "".join(ch for ch in hk if ch.isdigit())
    if hk_digits and hk_digits.isdigit() and 3 <= len(hk_digits) <= 5 and hk == hk_digits:
        code = hk_digits.zfill(5)
        return {
            "code": code, "name_hint": "", "kind": "stock",
            "source": "em", "em_secid": "116." + code,
            "yf_symbol": "", "concept": "",
        }

    # 3) 美股：纯 ASCII 字母（GOOGL / NET / CSIQ）
    if s.isalpha():
        return {
            "code": s, "name_hint": "", "kind": "stock",
            "source": "yf", "em_secid": "", "yf_symbol": s, "concept": "",
        }

    # 4) A股 6 位 / 指数
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) == 6:
        if digits in INDEX_CODES:
            prefix = "1" if digits in SH_INDEX else "0"
            return {
                "code": digits, "name_hint": "", "kind": "index",
                "source": "em", "em_secid": prefix + "." + digits,
                "yf_symbol": "", "concept": "",
            }
        # A股个股 / 基金（LOF/ETF）：6/9 开头沪市；0/2/3 开头深市；1/5 开头多为沪市基金
        if digits[0] in ("6", "9", "1", "5"):
            secid = "1." + digits
        else:  # 0 / 2 / 3 开头
            secid = "0." + digits
        return {
            "code": digits, "name_hint": "", "kind": "stock",
            "source": "em", "em_secid": secid, "yf_symbol": "", "concept": "",
        }

    # 4) 中文板块名 / 指数名 → 概念板（best-effort，price_feed 联网解析）
    if any("\u4e00" <= ch <= "\u9fff" for ch in s):
        if s in INDEX_NAME_TO_CODE:
            digits = INDEX_NAME_TO_CODE[s]
            prefix = "1" if digits in SH_INDEX else "0"
            return {
                "code": digits, "name_hint": s, "kind": "index",
                "source": "em", "em_secid": prefix + "." + digits,
                "yf_symbol": "", "concept": "",
            }
        # 其它中文词当作概念板块名
        return {
            "code": s, "name_hint": s, "kind": "sector",
            "source": "em_concept", "em_secid": "", "yf_symbol": "", "concept": s,
        }

    return None


def collect_from_db():
    """扫描 DB（posts + settings）里出现过的所有标的代码/板块名，返回去重后的原始字符串列表。"""
    import db
    raw = set()
    for p in db.get_posts():
        try:
            subs = __import__("json").loads(p.get("subject_stocks") or "[]")
        except Exception:
            subs = []
        for s in subs:
            if s.get("code"):
                raw.add(str(s["code"]))
            if s.get("sector_code"):
                raw.add(str(s["sector_code"]))
            if s.get("sector"):
                raw.add(str(s["sector"]))
        try:
            secs = __import__("json").loads(p.get("sectors") or "[]")
        except Exception:
            secs = []
        for s in secs:
            if isinstance(s, str):
                raw.add(s)
            elif isinstance(s, dict) and s.get("name"):
                raw.add(str(s["name"]))
    return sorted(raw)


def build_descriptors(raw_list):
    """原始字符串列表 → 去重描述符列表（过滤无法识别的）。"""
    out = {}
    order = []
    for r in raw_list:
        d = normalize_raw_code(r)
        if not d:
            continue
        key = (d["source"], d["code"], d["em_secid"], d["yf_symbol"], d["concept"])
        if key not in out:
            out[key] = d
            order.append(key)
    return [out[k] for k in order]


def to_yahoo_symbol(code: str, kind: str = None):
    """把归一化后的 code（normalize_raw_code 返回的 'code' 字段）转成雅虎财经 ticker。

    规则：
      * 已是 .HK/.SS/.SZ 结尾 -> 原样返回
      * 纯字母 -> 美股 ticker（GOOGL / CSIQ / NET 等）
      * 6 位数字 -> 指数用 INDEX_CODES 精确判定（000300->000300.SS，399006->399006.SZ）；
                    个股：6/9/1/5 开头沪市(.SS)，0/2/3 开头深市(.SZ)
      * 3~5 位数字 -> 港股 5 位补零 + .HK（09988 -> 09988.HK）
    返回 None 表示无法映射（如中文概念板块名，雅虎不含 A股概念板）。
    """
    if not code:
        return None
    c = code.strip().upper()
    if c.endswith((".HK", ".SS", ".SZ")):
        return c
    if c.isalpha():
        return c
    if c.isdigit():
        if len(c) == 6:
            if c in INDEX_CODES:
                return f"{c}.{'SS' if c in SH_INDEX else 'SZ'}"
            if c[0] in ("6", "9", "1", "5"):
                return f"{c}.SS"
            return f"{c}.SZ"
        if 3 <= len(c) <= 5:
            # 雅虎港股用「去前导零后补足 4 位」：00700->0700.HK, 01548->1548.HK, 09992->9992.HK
            hk = c.lstrip("0") or "0"
            return f"{hk.zfill(4)}.HK"
    return None


def yahoo_symbol_for_raw(raw: str):
    """原始代码/名称 -> 雅虎 ticker（组合 normalize_raw_code + to_yahoo_symbol）。"""
    d = normalize_raw_code(raw)
    if not d:
        return None
    return to_yahoo_symbol(d["code"], d.get("kind"))


if __name__ == "__main__":
    samples = ["600519", "000300", "399006", "00700", "09992", "9992", "9992.HK",
               "GOOGL", "NET", "CSIQ", "162605", "002055", "半导体", "沪深300", "XYZ123"]
    for s in samples:
        print(s, "->", normalize_raw_code(s))
