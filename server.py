"""本地 Web 服务：托管 ui/ 静态页 + 提供 /api/* JSON（由 api_adapt 生成）。

双击 exe 后会自动起本服务并打开浏览器到 http://localhost:8765 。
开发期：python server.py  然后浏览器开 http://localhost:8765
"""
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
import api_adapt

PORT = 8765
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")


def _normalize_cookie(raw):
    """把用户粘贴的任意 Cookie 文本规范成 'k=v; k2=v2' 头部字符串。

    支持三种输入：
    1) 纯 header 字符串：'xq_a_token=...; xq_r_token=...'
    2) Chrome 扩展「Cookie 管家 / cookie-picker」复制的 JSON：
       {"cookies":{"xueqiu":{"domain":"xueqiu.com","header":"xq_a_token=...; ..."}}}
       -> 自动提取其中的雪球(xueqiu) header。
    3) JSON 数组 [{"name":"xq_a_token","value":"..."}, ...] 或对象 {k:v}。
    返回 (header_str, error_msg)；error_msg 为 None 表示成功。
    """
    text = (raw or "").strip()
    if not text:
        return "", "cookie 不能为空"
    # 纯字符串（不以 { 或 [ 开头）直接当 header
    if not (text.startswith("{") or text.startswith("[")):
        return text, None
    # 尝试 JSON
    try:
        obj = json.loads(text)
    except Exception:
        return text, None  # 不是合法 JSON，当作普通 header 字符串

    header = None
    if isinstance(obj, dict):
        cookies = obj.get("cookies")
        if isinstance(cookies, dict):
            candidates = []
            for v in cookies.values():
                if isinstance(v, dict) and isinstance(v.get("header"), str) and v["header"].strip():
                    domain = str(v.get("domain", "")).lower()
                    candidates.append(("xueqiu" in domain, v["header"].strip()))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)  # 优先雪球
                header = candidates[0][1]
        if not header and isinstance(obj.get("header"), str) and obj["header"].strip():
            header = obj["header"].strip()
        if not header and obj and all(isinstance(x, dict) for x in obj.values()):
            header = "; ".join(f"{k}={v}" for k, v in obj.items())
    elif isinstance(obj, list):
        parts = []
        for item in obj:
            if isinstance(item, dict):
                name = item.get("name") or item.get("k") or item.get("key")
                val = item.get("value") or item.get("v") or item.get("val")
                if name is not None and val is not None:
                    parts.append(f"{name}={val}")
        if parts:
            header = "; ".join(parts)

    if not header:
        return "", ("无法从粘贴内容中识别 Cookie：请直接粘贴 'k=v; k2=v2' 字符串，"
                    "或 Chrome 扩展 Cookie 管家复制的 JSON")
    return header, None



def make_handler(ui_dir):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=ui_dir, **kw)

        def _json(self, obj):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/timeline_pending":
                return self._json(api_adapt.build_timeline_pending())
            if path == "/api/timeline_verified":
                return self._json(api_adapt.build_timeline_verified())
            if path == "/api/persons":
                return self._json(api_adapt.build_persons())
            if path == "/api/predictions":
                return self._json(api_adapt.build_predictions())
            if path == "/api/settings":
                return self._json(api_adapt.build_settings())
            if path == "/api/monitor":
                return self._json(api_adapt.build_monitor())
            if path == "/api/status":
                return self._json(api_adapt.build_status())
            if path == "/api/worker/status":
                return self._json(api_adapt.build_worker_status())
            return super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except Exception:
                length = 0
            raw = self.rfile.read(length) if length else b""
            if path == "/api/followed_groups":
                return self._post_followed_groups(raw)
            if path == "/api/save_followed":
                return self._post_save_followed(raw)
            if path == "/api/start_fetch":
                return self._post_start_fetch(raw)
            if path == "/api/resolve_user":
                return self._post_resolve_user(raw)
            if path == "/api/worker/start":
                return self._post_worker_start(raw)
            if path == "/api/worker/stop":
                return self._post_worker_stop(raw)
            if path == "/api/save_backfill_days":
                return self._post_save_backfill_days(raw)
            if path == "/api/save_cookie":
                return self._post_save_cookie(raw)
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        def _json_err(self, code, msg):
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode("utf-8"))

        def _post_followed_groups(self, raw):
            try:
                import config
                import xueqiu_client
                settings = config.load_settings()
                cookie = settings.get("cookie", "")
                if not cookie:
                    return self._json_err(401, "未配置 cookie，请先在设置页导入雪球 cookie")
                users = xueqiu_client.fetch_followed_groups(cookie)
                self._json({"ok": True, "group_name": "特别关注", "users": users})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_save_followed(self, raw):
            try:
                import config
                import db
                payload = json.loads(raw.decode("utf-8")) if raw else []
                if not isinstance(payload, list):
                    raise ValueError("body 应为 list")
                # 完全替换：只保留界面提交的人；enabled 以提交的 enabled 字段为准
                # （提交项带 enabled 则尊重，否则默认 True）
                new_followed = []
                seen = set()
                for u in payload:
                    xid = str(u.get("id") or u.get("xid") or "")
                    if not xid or xid in seen:
                        continue
                    seen.add(xid)
                    name = u.get("name") or ""
                    enabled = u.get("enabled", True)
                    if not isinstance(enabled, bool):
                        enabled = True
                    new_followed.append({"xid": xid, "name": name, "enabled": enabled})
                    try:
                        db.upsert_user(xid, name=name)
                    except Exception:
                        pass
                settings = config.load_settings()
                settings["followed"] = new_followed
                config.save_settings(settings)
                self._json({"ok": True, "followed": new_followed})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_start_fetch(self, raw):
            try:
                import threading
                import fetcher
                import config
                body = json.loads(raw.decode("utf-8")) if raw else {}
                days = body.get("days")
                if days is not None:
                    try:
                        days = int(days)
                        if days < 1:
                            days = 1
                    except Exception:
                        days = None
                # 把所选范围写回设置，下次打开保留
                if days is not None:
                    s = config.load_settings()
                    s["backfill_days"] = days
                    config.save_settings(s)
                kwargs = {"clear_first": False}
                if days is not None:
                    kwargs["days"] = days
                t = threading.Thread(target=fetcher.run_once, kwargs=kwargs, daemon=True)
                t.start()
                self._json({"ok": True, "message": "抓取已启动", "days": days})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_worker_start(self, raw):
            try:
                import fetcher
                ok = fetcher.start_worker()
                self._json({"ok": True, "started": ok, "running": fetcher.worker_running()})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_worker_stop(self, raw):
            try:
                import fetcher
                fetcher.stop_worker()
                self._json({"ok": True, "running": fetcher.worker_running()})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_save_backfill_days(self, raw):
            try:
                import config
                body = json.loads(raw.decode("utf-8")) if raw else {}
                days = body.get("days")
                try:
                    days = int(days)
                    if days < 1:
                        days = 1
                    if days > 365:
                        days = 365
                except Exception:
                    return self._json_err(400, "days 应为 1~365 的整数")
                s = config.load_settings()
                s["backfill_days"] = days
                config.save_settings(s)
                self._json({"ok": True, "backfill_days": days})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_resolve_user(self, raw):
            try:
                import config
                import xueqiu_client
                import urllib.parse
                settings = config.load_settings()
                cookie = settings.get("cookie", "")
                if not cookie:
                    return self._json_err(401, "未配置 cookie，请先在设置页导入雪球 cookie")
                body = json.loads(raw.decode("utf-8")) if raw else {}
                q = str(body.get("q", "")).strip()
                if not q:
                    return self._json_err(400, "请输入昵称或 user_id")
                ids = xueqiu_client.resolve_user_ids([q], cookie)
                if not ids or not ids[0].isdigit():
                    return self._json_err(404, "未解析到有效 ID: " + (ids[0] if ids else q))
                uid = ids[0]
                # 用搜索结果补全昵称
                name = uid
                try:
                    sq = urllib.parse.quote(q)
                    sub = "%s?q=%s&count=5" % (xueqiu_client.XQ_SEARCH, sq)
                    data = None
                    for u in (xueqiu_client.XQ_API + sub, xueqiu_client.XQ_BASE + sub):
                        try:
                            data = xueqiu_client._req(u, cookie)
                            break
                        except Exception:
                            continue
                    if data:
                        d = json.loads(data)
                        for u in d.get("list") or []:
                            if str(u.get("id")) == uid:
                                name = u.get("screen_name") or name
                                break
                except Exception:
                    pass
                self._json({"ok": True, "user": {"id": uid, "name": name}})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_save_cookie(self, raw):
            try:
                import config
                body = json.loads(raw.decode("utf-8")) if raw else {}
                cookie = str(body.get("cookie", "")).strip()
                header, err = _normalize_cookie(cookie)
                if err:
                    return self._json_err(400, err)
                if "xq_a_token=" not in header and "xqat=" not in header:
                    return self._json_err(
                        400,
                        "未找到雪球登录态（需含 xq_a_token 或 xqat）。"
                        "若使用 Chrome 扩展 Cookie 管家，请确认已勾选「雪球」并点过「测试读取」后再复制。",
                    )
                s = config.load_settings()
                s["cookie"] = header
                s["cookie_source"] = "manual"
                config.save_settings(s)
                self._json({"ok": True, "message": "Cookie 已保存（手动模式）"})
            except Exception as e:
                self._json_err(500, str(e))

    return Handler


def main():
    handler = make_handler(UI_DIR)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    print(f"xueqiu-analyzer 已启动： http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
