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
            if path == "/api/import_browser_cookie":
                return self._post_import_browser_cookie(raw)
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

        def _post_import_browser_cookie(self, raw):
            try:
                import config
                import browser_cookie
                body = json.loads(raw.decode("utf-8")) if raw else {}
                browser = str(body.get("browser", "chrome")).strip().lower()
                if browser not in ("chrome", "edge"):
                    browser = "chrome"
                cookie, err = browser_cookie.build_xueqiu_cookie(browser)
                if err:
                    return self._json_err(400, err)
                if not cookie:
                    return self._json_err(400, "未能提取到雪球 cookie（请确认已在 %s 登录雪球）" % browser)
                s = config.load_settings()
                s["cookie"] = cookie
                s["cookie_source"] = "browser"
                config.save_settings(s)
                self._json({"ok": True,
                            "message": "已从 %s 导入雪球登录态（%d 个字段），本页状态已刷新。"
                                       % (browser, cookie.count("="))})
            except Exception as e:
                self._json_err(500, str(e))

        def _post_save_cookie(self, raw):
            try:
                import config
                body = json.loads(raw.decode("utf-8")) if raw else {}
                cookie = str(body.get("cookie", "")).strip()
                if not cookie:
                    return self._json_err(400, "cookie 不能为空")
                if "xq_a_token=" not in cookie and "xqat=" not in cookie:
                    return self._json_err(400, "cookie 格式不正确，需包含 xq_a_token 或 xqat")
                s = config.load_settings()
                s["cookie"] = cookie
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
