#!/usr/bin/env python3
"""将腾讯自选股 MCP (westock-mcp) 返回的日线 JSON 灌进 market_daily。

支持两种格式：
  1) 批量格式（传 codes）：{"ok":true,"data":{"success":..,"data":[{symbol, data:{nodes:[...]}}, ...],"errors":[]}}
  2) 单码格式（传单个 code）：{"ok":true,"data":{"nodes":[...]}}  —— 无 symbol，需从文件名推断

用法:
    python ingest_westock.py <file1.txt> [<file2.txt> ...]
每个 .txt 是 mcp__westock-mcp__data_kline 返回的完整 JSON。
单码文件命名需含 symbol，如 westock_sh600519.txt / westock_hk01548.txt。
只用到每个 node 的 date + last（收盘价）；pct 由本脚本按时间顺序重算。
"""
import json
import os
import re
import sys
import db
import symbol_mapper


def _code_from_symbol(symbol: str) -> str:
    """sh600519 -> 600519, sz000933 -> 000933, hk00700 -> 00700"""
    if symbol.startswith(("sh", "sz", "hk")):
        return symbol[2:]
    return symbol


def _prefix_from_code(code: str) -> str:
    """根据代码反推腾讯自选股前缀（备用，当前解析走文件名）"""
    if code.startswith(("6", "68", "5")):
        return "sh"
    if code.startswith(("0", "3", "1")):
        return "sz"
    if code.isdigit() and len(code) <= 5:
        return "hk"
    return ""


_SYMBOL_RE = re.compile(r"(sh|sz|hk)\d+")


def _symbol_from_filename(path: str):
    m = _SYMBOL_RE.search(os.path.basename(path))
    return m.group(0) if m else None


def _rows_from_nodes(code, nodes):
    """nodes 最新在前；反向遍历按时间顺序算 pct。解析 high/low 供区间极值验证。"""
    prev_close = None
    for node in reversed(nodes):
        date = node.get("date")
        close = node.get("last")
        if date is None or close is None:
            continue
        pct = None
        if prev_close is not None and prev_close != 0:
            pct = (close - prev_close) / prev_close * 100.0
        prev_close = close
        yield {
            "date": date,
            "code": code,
            "name": "",
            "close": close,
            "pct": pct,
            "high": node.get("high"),
            "low": node.get("low"),
        }


def load_rows(path: str):
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    data_field = payload.get("data", {})

    # 单码格式：{"data":{"nodes":[...]}}  无 symbol
    if isinstance(data_field, dict) and "nodes" in data_field:
        symbol = _symbol_from_filename(path)
        if not symbol:
            print(f"[warn] 单码格式但文件名无法解析 symbol，已跳过: {path}")
            return
        code = _code_from_symbol(symbol)
        for r in _rows_from_nodes(code, data_field.get("nodes", [])):
            yield r
        return

    # 批量格式：{"data":{"data":[{symbol, data:{nodes}}]}}
    items = data_field.get("data", []) if isinstance(data_field, dict) else []
    for item in items:
        symbol = item.get("symbol")
        code = _code_from_symbol(symbol)
        nodes = item.get("data", {}).get("nodes", [])
        for r in _rows_from_nodes(code, nodes):
            yield r


def main():
    if len(sys.argv) < 2:
        print("Usage: python ingest_westock.py <westock-result1.txt> [<westock-result2.txt> ...]")
        sys.exit(1)

    db.init_db()
    desc_map = {d["code"]: d for d in symbol_mapper.build_descriptors(symbol_mapper.collect_from_db())}

    total = 0
    for path in sys.argv[1:]:
        rows = []
        for r in load_rows(path):
            d = desc_map.get(r["code"])
            r["kind"] = d["kind"] if d else "stock"
            r["name"] = (d.get("name_hint") or "") if d else ""
            rows.append(r)
        if rows:
            db.upsert_market(rows)
            total += len(rows)
            print(f"[ingest] {os.path.basename(path)}: {len(rows)} 行 (code={rows[0]['code']})")

    print(f"[ingest] 共写入 {total} 行")

    print("[ingest] 重算事件/回测/预测...")
    import engine
    result = engine.run_all()
    print("[ingest] run_all:", result)


if __name__ == "__main__":
    main()
