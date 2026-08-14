"""示例分析 skill：演示如何基于 base_result 做差异化补充/覆盖。

启用方式：在 settings.json 的 "skills": ["example_skill"] 中加入本模块名即可。
（空列表 = 不调用任何 skill）

逻辑演示：
  * 若主体(subject)或涉及板块(sectors)含「半导体」且 stance 为看多，
    在 summary 末尾追加提示「[自选分析：注意估值]」，提醒半导体板块估值风险。
  * 返回新 dict（analyst 会用其覆盖原 result）。
"""


def analyze(post_text, base_result):
    # 不修改入参，返回新的 dict
    result = dict(base_result)

    subject = result.get("subject") or {}
    name = subject.get("name", "")
    stance = subject.get("stance", "")
    sectors = result.get("sectors", []) or []

    is_semi = ("半导体" in name) or any("半导体" in s for s in sectors)
    if is_semi and stance == "看多":
        tag = "[自选分析：注意估值]"
        summary = result.get("summary", "")
        if tag not in summary:
            result["summary"] = (summary + tag).strip()

    return result
