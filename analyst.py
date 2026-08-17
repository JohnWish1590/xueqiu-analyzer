"""雪球大V观点印证分析工具 —— 发言结构化分析层（analyst.py）。

把一条雪球发言转成严格结构化的观点结论，供后续回测使用。

单主体识别（核心约定）
----------------------
一条发言常同时提到多只股票/板块，但本工具只产出「一个」主体事件（subject，进回测），
其余标的放进 contrast（仅展示、不进回测）。启发式规则（config.SUBJECT_RULE=="single" 时生效）：

  * 情形 A「A 涨得好、对比 B 差」        → subject=A(看多)，contrast=[B(看空)]。
  * 情形 B「同时看多 A、看空 B」        → 只取「主要意图」那个做 subject。
        主要意图启发式优先级：
          1) 发言中更靠前出现的标的；
          2) 着墨更多 / 局部情绪更明确的标的；
          3) 默认取第一个被识别为「看多」的标的；若无看多则取第一个被提及标的。
        另一个标的放 contrast，并在 note 注明其方向（如「对比/衬托：看空」）。
  * 无论 LLM 还是启发式路径，最终输出都强制 single-subject：subject 永远只有一个。

两条路径
--------
  * 有 api_key：直连 OpenAI 兼容接口（requests，无需 SDK），要求模型只输出 JSON。
  * 无 key / 调用异常：回退 heuristic_analyze（纯规则，完全离线）。

自定义分析 skill
----------------
  * analysis_skills/ 下每个 .py（非 __init__）若暴露 analyze(post_text, base_result)->dict，
    且被 config.load_settings()["skills"] 启用，则依次对 base_result 做覆盖/补充。
    「不同用户分析逻辑可差异化」即由此实现。
"""
import re
import json
import importlib.util

import config


# ============================================================================
# 1) 内置实体词典（启发式兜底用，纯本地、无联网）
# ============================================================================
# 常见 A 股个股：中文名 -> 代码（code 留空串表示未覆盖）
STOCK_DICT = {
    "中芯国际": "688981",
    "贵州茅台": "600519",
    "五粮液": "000858",
    "宁德时代": "300750",
    "比亚迪": "002594",
    "中国平安": "601318",
    "招商银行": "600036",
    "平安银行": "000001",
    "兴业银行": "601166",
    "东方财富": "300059",
    "中信证券": "600030",
    "华泰证券": "601688",
    "隆基绿能": "601012",
    "通威股份": "600438",
    "阳光电源": "300274",
    "立讯精密": "002475",
    "京东方A": "000725",
    "TCL科技": "000100",
    "美的集团": "000333",
    "格力电器": "000651",
    "伊利股份": "600887",
    "海康威视": "002415",
    "三一重工": "600031",
    "紫金矿业": "601899",
    "北方华创": "002371",
    "韦尔股份": "603501",
    "中际旭创": "300308",
    "工业富联": "601138",
    "中国移动": "600941",
    "长江电力": "600900",
    "中国神华": "601088",
    "陕西煤业": "601225",
    "宝钢股份": "600019",
    "万华化学": "600309",
    "药明康德": "603259",
    "恒瑞医药": "600276",
    "迈瑞医疗": "300760",
}

# 板块关键词：被提及即识别为 sector（name 用板块名本身，code 留空）
SECTOR_KEYWORDS = [
    "半导体", "芯片", "集成电路", "消费电子", "信创",
    "新能源", "光伏", "锂电", "锂电池", "储能", "风电",
    "白酒", "食品饮料", "家电",
    "医药", "创新药", "生物科技", "CRO", "中药",
    "煤炭", "有色", "稀土", "钢铁", "化工",
    "券商", "证券", "银行", "保险", "地产", "房地产",
    "红利", "高股息", "军工", "国防", "汽车", "整车",
]

# 局部/全局态度词
BULL_WORDS = ["看多", "看好", "买入", "建仓", "加仓", "机会", "低估", "要涨",
              "涨得好", "涨停", "突破", "上行", "反弹", "利好", "强势",
              "新高", "真香", "值得", "布局", "涨", "好"]
BEAR_WORDS = ["看空", "不看好", "卖出", "减仓", "清仓", "风险", "高估", "要跌",
              "大跌", "破位", "下行", "回调", "避雷", "警惕", "差", "弱",
              "不行", "坑", "套牢", "暴雷", "减持", "跌", "烂"]

HORIZON_WORDS = ["短线", "中线", "长线", "观察"]

VALID_STANCE = ("看多", "看空", "中性")
VALID_HORIZON = ("短线", "中线", "长线", "观察")


# ============================================================================
# 2) 纯规则兜底 heuristic_analyze（完全离线、无 key 也能产出结构化结果）
# ============================================================================
def _find_stocks(text):
    """返回 [(name, code, start, end)]，按出现位置排序。"""
    found = []
    for name, code in STOCK_DICT.items():
        idx = text.find(name)
        if idx != -1:
            found.append((name, code, idx, idx + len(name)))
    found.sort(key=lambda x: x[2])
    return found


def _find_sectors(text):
    """返回 [name]（字符串，已去重并消除子串包含关系）。"""
    hits = []
    for kw in SECTOR_KEYWORDS:
        if kw in text and kw not in hits:
            hits.append(kw)
    # 去掉被其它命中项包含的关键词（如「电子」被「消费电子」包含）
    cleaned = []
    for h in hits:
        if not any(h != other and h in other for other in hits):
            cleaned.append(h)
    return cleaned


_CLAUSE_SPLIT = re.compile(r"[，。；！？、\n~]")


def _clause_sentiment(name, clauses):
    """判断某实体所在分句的态度：看多 / 看空 / 中性。

    把发言按标点切成分句，实体归属其出现的分句，仅在该分句内统计态度词，
    避免「A 涨得好、对比 B 差」中 A 的「好/涨」误判到 B 身上。
    """
    clause = ""
    for c in clauses:
        if name in c:
            clause = c
            break
    if not clause:
        return "中性"
    b = sum(w in clause for w in BULL_WORDS)
    s = sum(w in clause for w in BEAR_WORDS)
    if b > s:
        return "看多"
    if s > b:
        return "看空"
    return "中性"


def _detect_horizon(text):
    for h in HORIZON_WORDS:
        if h in text:
            return h
    return "中线"


def _clamp_sentiment(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, v))


def heuristic_analyze(text, user_name=""):
    """纯规则结构化分析（无 key / 离线兜底）。返回严格结构 dict。"""
    text = text or ""
    stocks = _find_stocks(text)
    sectors = _find_sectors(text)

    # 候选实体：个股 + 板块关键词（都可作为 subject/contrast 候选）
    candidates = []
    for name, code, s, e in stocks:
        candidates.append({"name": name, "code": code, "kind": "stock",
                           "start": s, "end": e})
    for name in sectors:
        # 板块位置取其在文本中首次出现
        idx = text.find(name)
        candidates.append({"name": name, "code": "", "kind": "sector",
                           "start": idx, "end": idx + len(name)})
    candidates.sort(key=lambda x: x["start"])

    # 分句，逐候选判断局部态度（实体归属其所在分句）
    clauses = _CLAUSE_SPLIT.split(text)
    for c in candidates:
        c["local"] = _clause_sentiment(c["name"], clauses)

    # ---- 单主体选择（config.SUBJECT_RULE） ----
    subject = None
    if config.SUBJECT_RULE == "single":
        # 优先：第一个局部看多的候选；否则第一个候选；否则 None
        bullish = [c for c in candidates if c["local"] == "看多"]
        if bullish:
            subject = bullish[0]
        elif candidates:
            subject = candidates[0]
    else:
        if candidates:
            subject = candidates[0]

    # ---- contrast：其余候选 ----
    contrast = []
    if subject is not None:
        for c in candidates:
            if c is subject:
                continue
            note = "提及"
            if c["local"] == "看空":
                note = "对比/衬托：看空"
            elif c["local"] == "看多":
                note = "衬托：看多"
            contrast.append({"name": c["name"], "code": c.get("code", ""),
                             "note": note})

    # ---- 高层字段 ----
    if subject is not None:
        stance = subject["local"] if subject["local"] in VALID_STANCE else "中性"
        subj_stance = stance
        subj_horizon = _detect_horizon(text)
        subject_out = {"name": subject["name"], "code": subject.get("code", ""),
                       "stance": subj_stance, "horizon": subj_horizon}
    else:
        stance = "中性"
        subject_out = None

    # 全局情绪强度（基于全文字数统计微调）
    b_all = sum(w in text for w in BULL_WORDS)
    s_all = sum(w in text for w in BEAR_WORDS)
    if stance == "看多":
        sentiment = _clamp_sentiment(0.6 + 0.1 * max(0, b_all - 1))
    elif stance == "看空":
        sentiment = _clamp_sentiment(-0.6 - 0.1 * max(0, s_all - 1))
    else:
        sentiment = 0.0

    # summary：一句话观点提炼
    parts = []
    if subject_out is not None:
        parts.append(f"对{subject_out['name']}持{subject_out['stance']}观点")
    if contrast:
        parts.append("对比" + "、".join(c["name"] for c in contrast))
    if parts:
        summary = "；".join(parts)
    else:
        summary = text.strip()[:40]

    return {
        "stance": stance,
        "horizon": _detect_horizon(text),
        "sentiment": round(sentiment, 3),
        "summary": summary,
        "subject": subject_out,
        "contrast": contrast,
        "sectors": sectors,
        "model": "heuristic",
    }


# ============================================================================
# 3) LLM 路径（有 key 时）：requests 直连 OpenAI 兼容接口
# ============================================================================
_SYSTEM_PROMPT = (
    "你是一名严谨的卖方分析师助理，负责把散户/大V的雪球发言转成结构化观点。"
    "请只输出一个 JSON 对象，不要任何解释或 Markdown 围栏。JSON 字段严格如下：\n"
    "{\n"
    '  "stance": "看多" | "看空" | "中性",\n'
    '  "horizon": "短线" | "中线" | "长线" | "观察",\n'
    '  "sentiment": 介于 -1.0 到 1.0 的数字（看多偏正、看空偏负）,\n'
    '  "summary": "一句话中文观点提炼（不超过40字）",\n'
    '  "subject": {"name":"主体标的名(中文)","code":"股票代码或空串","stance":"看多|看空","horizon":"短线|中线|长线|观察"} 或 null,\n'
    '  "contrast": [{"name":"对比/衬托标的名","code":"","note":"仅展示不进回测"}],  // 可能为空数组\n'
    '  "sectors": ["板块名(中文)"]\n'
    "}\n"
    "规则：\n"
    "1) 一条发言可能有多个标的，但 subject 只取「用户主要意图」的那个（最靠前/着墨最多/首个看多标的），其余放进 contrast。\n"
    "2) 若用户只是拿某标的做对比/衬托、自己无独立方向，必须放 contrast 而非 subject。\n"
    "3) 无法判断时 stance 填中性、subject 填 null。\n"
    "\n"
    "示例输入：中芯国际涨得真好，对比消费电子太差了\n"
    "示例输出：{\"stance\":\"看多\",\"horizon\":\"中线\",\"sentiment\":0.6,"
    "\"summary\":\"对中芯国际看多，对比消费电子偏弱\","
    "\"subject\":{\"name\":\"中芯国际\",\"code\":\"688981\",\"stance\":\"看多\",\"horizon\":\"中线\"},"
    "\"contrast\":[{\"name\":\"消费电子\",\"code\":\"\",\"note\":\"对比/衬托：看空\"}],"
    "\"sectors\":[\"消费电子\"]}"
)


def _extract_json(content):
    """鲁棒提取 JSON：去除 ```json 围栏，截取首个 { 到末个 }。失败返回 None。"""
    if not content:
        return None
    s = content.strip()
    # 去 code fence
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    frag = s[start:end + 1]
    try:
        return json.loads(frag)
    except Exception:
        return None


def _llm_analyze(text, user_name="", provider=None, api_key=None, model=None):
    """调用 LLM。任何异常都返回 None（交由调用方回退 heuristic）。"""
    try:
        import requests  # 延迟导入，避免无 requests 时连 heuristic 都用不了

        settings = config.load_settings()
        if provider is None:
            provider = settings.get("provider") or config.DEFAULT_PROVIDER
        if api_key is None:
            api_key = settings.get("api_key", "")
        if not api_key:
            return None
        prov = config.MODEL_PROVIDERS.get(provider)
        if not prov:
            return None
        base = prov["base"]
        if model is None:
            model = settings.get("model", "")
        if not model:
            model = prov["model"]

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"发言内容：\n{text}"},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{base}/chat/completions",
            headers=headers, json=payload, timeout=40,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        obj = _extract_json(content)
        if not obj:
            return None
        return _normalize_llm(obj, model)
    except Exception:
        return None


def _normalize_llm(obj, model):
    """把 LLM 返回的 dict 规整为严格结构；依赖 base(heuristic) 在合并时补缺。"""
    stance = obj.get("stance")
    if stance not in VALID_STANCE:
        stance = "中性"
    horizon = obj.get("horizon")
    if horizon not in VALID_HORIZON:
        horizon = "中线"
    sectors = obj.get("sectors") or []
    if not isinstance(sectors, list):
        sectors = [str(sectors)]
    sectors = [str(s) for s in sectors]

    # subject：强制单主体
    subject = None
    raw_subj = obj.get("subject")
    if isinstance(raw_subj, list):
        raw_subj = raw_subj[0] if raw_subj else None
    if isinstance(raw_subj, dict) and raw_subj.get("name"):
        s_stance = raw_subj.get("stance")
        if s_stance not in ("看多", "看空"):
            s_stance = stance if stance in ("看多", "看空") else "中性"
        s_horizon = raw_subj.get("horizon")
        if s_horizon not in VALID_HORIZON:
            s_horizon = horizon
        subject = {
            "name": str(raw_subj.get("name", "")),
            "code": str(raw_subj.get("code", "") or ""),
            "stance": s_stance,
            "horizon": s_horizon,
        }

    # contrast：强制列表
    contrast = []
    raw_c = obj.get("contrast") or []
    if isinstance(raw_c, list):
        for item in raw_c:
            if isinstance(item, dict) and item.get("name"):
                contrast.append({
                    "name": str(item.get("name", "")),
                    "code": str(item.get("code", "") or ""),
                    "note": str(item.get("note", "仅展示不进回测")),
                })

    return {
        "stance": stance,
        "horizon": horizon,
        "sentiment": _clamp_sentiment(obj.get("sentiment", 0.0)),
        "summary": str(obj.get("summary", "") or ""),
        "subject": subject,
        "contrast": contrast,
        "sectors": sectors,
        "model": model,
    }


def _merge_base_with_llm(base, llm):
    """LLM 优先，缺失字段用 heuristic base 补齐；强制 single subject。"""
    result = dict(base)
    for k in ("stance", "horizon", "sentiment", "summary"):
        if llm.get(k) not in (None, ""):
            result[k] = llm[k]
    # subject：LLM 有效则用它，否则保留 base
    if isinstance(llm.get("subject"), dict) and llm["subject"].get("name"):
        result["subject"] = llm["subject"]
    # contrast / sectors：LLM 有则覆盖
    if llm.get("contrast"):
        result["contrast"] = llm["contrast"]
    if llm.get("sectors"):
        result["sectors"] = llm["sectors"]
    result["model"] = llm.get("model") or base.get("model")
    return result


# ============================================================================
# 4) 自定义分析 skill 动态加载与合并
# ============================================================================
def load_skills(names=None):
    """从 config.SKILL_DIR 动态 import 每个 .py（非 __init__）。

    names 为启用列表时只返回其中的模块；为 None 时返回全部暴露 analyze 的模块。
    返回 [(mod_name, module), ...]。
    """
    modules = []
    skill_dir = config.SKILL_DIR
    if not skill_dir.exists():
        return modules
    for p in sorted(skill_dir.glob("*.py")):
        if p.name == "__init__.py":
            continue
        mod_name = p.stem
        if names is not None and mod_name not in names:
            continue
        try:
            spec = importlib.util.spec_from_file_location(mod_name, str(p))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "analyze"):
                modules.append((mod_name, mod))
        except Exception:
            continue
    return modules


def _apply_skills(text, result):
    """依次调用已启用 skill 的 analyze(post_text, base_result)->dict 合并结果。"""
    settings = config.load_settings()
    enabled = settings.get("skills") or []
    if not enabled:
        return result
    for mod_name, mod in load_skills(enabled):
        try:
            new = mod.analyze(text, result)
            if isinstance(new, dict):
                result = new
        except Exception:
            continue
    return result


# ============================================================================
# 5) 主入口
# ============================================================================
def analyze_post(text, user_name="", provider=None, api_key=None, model=None):
    """分析一条发言，返回严格结构 dict。

    流程：heuristic 兜底先算 base_result → 有 key 时尝试 LLM 覆盖 → 启用 skill 依次合并。
    任何 LLM/网络异常都静默回退 heuristic，绝不抛错。
    """
    base = heuristic_analyze(text, user_name)
    result = base
    llm = _llm_analyze(text, user_name, provider, api_key, model)
    if llm is not None:
        result = _merge_base_with_llm(base, llm)
    result = _apply_skills(text, result)
    return result


def analyze_batch(texts, **kw):
    """批量分析，返回 list[dict]，顺序与输入一致。"""
    return [analyze_post(t, **kw) for t in (texts or [])]


# ============================================================================
# 6) 人话解读层 interpret_post（与结构化 analyze_post 并列，不替换）
#    —— 把发言翻译成"这句话什么意思 / 指什么板块 / 点的个股 / 相对还是绝对 /
#       时间尺度 / 客观风险提示"。只做理解，不给操作建议（跟/反/观望留给证据账本）。
# ============================================================================
_INTERPRET_PROMPT = (
    "你是一名资深卖方分析师，负责把雪球大V的发言用大白话解读给普通投资者，"
    "帮他们快速看懂「这句话到底在说什么」。\n"
    "请只输出一个 JSON 对象，不要任何解释或 Markdown 围栏。JSON 字段严格如下：\n"
    "{\n"
    '  "paraphrase": "一句话人话翻译：他在表达什么观点、对什么标的、什么方向（不超过60字）",\n'
    '  "sectors": ["他指涉的板块/行业（中文，语义理解，非关键词堆砌）"],\n'
    '  "stocks": [{"name":"提到的个股中文名","code":"股票代码或空串","note":"他对这只票的具体看法（一句话）"}],\n'
    '  "basis": "这句话的基准，取：相对大盘 | 绝对收益 | 风格板块轮动 | 无法判断",\n'
    '  "horizon": {"value":"短线|中线|长线|观察","confidence":0.0到1.0,"reason":"判断时间尺度的依据（一句话）"},\n'
    '  "risks": ["客观风险/不确定性提示（如：未提目标价 / 未给止损 / 依据不足），不给买卖建议"]\n'
    "}\n"
    "规则：\n"
    "1) 只做「理解」和「客观提示」，绝不输出「建议买入/卖出/跟随/反向」等操作指令。\n"
    "2) basis 判断他在说绝对涨跌还是相对强弱（例：说「芯片涨但大盘跌」通常是相对/风格判断）。\n"
    "3) horizon 结合用词：「短期/反弹/超跌」→短线；「趋势/主线/布局」→中线；「周期/时代/长期」→长线。\n"
    "4) risks 只列客观不确定性，不做方向判断。\n"
    "5) 无法判断的字段给保守默认（basis=无法判断，confidence 取低值）。\n"
)


def _llm_interpret(text, base_result, user_name="", provider=None, api_key=None, model=None):
    """调用 LLM 生成人话解读。任何异常返回 None（交由 heuristic 降级）。"""
    try:
        import requests

        settings = config.load_settings()
        if provider is None:
            provider = settings.get("provider") or config.DEFAULT_PROVIDER
        if api_key is None:
            api_key = settings.get("api_key", "")
        if not api_key:
            return None
        prov = config.MODEL_PROVIDERS.get(provider)
        if not prov:
            return None
        base = prov["base"]
        if model is None:
            model = settings.get("model", "")
        if not model:
            model = prov["model"]

        subj = base_result.get("subject") or {}
        hint = (
            f"（辅助参考，已结构化识别：stance={base_result.get('stance')}, "
            f"horizon={base_result.get('horizon')}, subject={subj.get('name','')}, "
            f"summary={base_result.get('summary','')}）"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _INTERPRET_PROMPT},
                {"role": "user", "content": f"发言内容：\n{text}\n\n{hint}"},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            f"{base}/chat/completions",
            headers=headers, json=payload, timeout=40,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        obj = _extract_json(content)
        if not obj:
            return None
        return _normalize_interpret(obj, model)
    except Exception:
        return None


def _normalize_interpret(obj, model):
    """把 LLM 解读规整为严格结构；缺字段给保守默认。"""
    def _s(v):
        return str(v or "").strip()

    horizon = obj.get("horizon") or {}
    if not isinstance(horizon, dict):
        horizon = {}
    hv = horizon.get("value")
    if hv not in VALID_HORIZON:
        hv = "观察"
    try:
        conf = float(horizon.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    sectors = obj.get("sectors") or []
    if not isinstance(sectors, list):
        sectors = [str(sectors)]
    sectors = [_s(s) for s in sectors if _s(s)]

    stocks = obj.get("stocks") or []
    if not isinstance(stocks, list):
        stocks = []
    stocks_norm = []
    for it in stocks:
        if isinstance(it, dict) and (_s(it.get("name"))):
            stocks_norm.append({
                "name": _s(it.get("name")),
                "code": _s(it.get("code")),
                "note": _s(it.get("note")),
            })

    basis = obj.get("basis")
    if basis not in ("相对大盘", "绝对收益", "风格板块轮动", "无法判断"):
        basis = "无法判断"

    risks = obj.get("risks") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [_s(r) for r in risks if _s(r)]

    return {
        "paraphrase": _s(obj.get("paraphrase")),
        "sectors": sectors,
        "stocks": stocks_norm,
        "basis": basis,
        "horizon": {"value": hv, "confidence": round(conf, 2), "reason": _s(horizon.get("reason"))},
        "risks": risks,
        "model": model,
    }


def heuristic_interpret(text, base_result):
    """离线兜底解读：基于结构化结果拼一段简短的人话解读，不做深层语义理解。"""
    subj = base_result.get("subject") or {}
    stance = base_result.get("stance", "中性")
    horizon = base_result.get("horizon", "中线")
    sectors = base_result.get("sectors") or []
    contrast = base_result.get("contrast") or []

    parts = []
    if subj.get("name"):
        parts.append(f"对{subj['name']}持{stance}观点")
    else:
        parts.append(f"表达{stance}观点")
    if sectors:
        parts.append("涉及板块：" + "、".join(sectors[:3]))
    if contrast:
        parts.append("同时提及对比标的：" + "、".join(c["name"] for c in contrast[:3]))
    paraphrase = "；".join(parts) + "。"

    stocks = []
    if subj.get("name"):
        stocks.append({"name": subj.get("name", ""), "code": subj.get("code", ""),
                       "note": stance})

    return {
        "paraphrase": paraphrase,
        "sectors": list(sectors),
        "stocks": stocks,
        "basis": "无法判断",
        "horizon": {"value": horizon, "confidence": 0.5, "reason": "离线兜底，按关键词/默认"},
        "risks": ["离线兜底解读，未做深层语义理解"],
        "model": "heuristic",
    }


def interpret_post(text, base_result=None, user_name="", provider=None, api_key=None, model=None):
    """解读一条发言（人话解读），返回 dict（存 posts.interpretation 的 JSON）。

    流程：LLM 解读 → 失败/无 key 时 heuristic_interpret 降级。
    base_result 建议由调用方传入（避免重复做结构化），为 None 时用启发式兜底。
    任何异常都静默降级，绝不抛错。
    """
    if base_result is None:
        base_result = heuristic_analyze(text, user_name)
    interp = _llm_interpret(text, base_result, user_name, provider, api_key, model)
    if interp is None:
        interp = heuristic_interpret(text, base_result)
    return interp


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else "中芯国际涨得真好，对比消费电子太差了"
    print(json.dumps(analyze_post(sample), ensure_ascii=False, indent=2))
