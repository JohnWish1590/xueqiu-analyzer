"""程序入口（双击 exe 后由 PyInstaller 引导到此）。

职责：
  - 初始化数据库
  - 启动本地 Web 服务（server.py，端口 8765，托管 ui/ + /api/*）
  - 若已配置有效 cookie 与跟踪用户，后台启动抓取守护（fetcher.run_daemon）
  - 自动打开浏览器到 http://localhost:8765
"""
import logging
import os
import threading
import time
import webbrowser

import config
import db
import server

log = logging.getLogger("run")
PORT = server.PORT


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db.init_db()
    log.info("数据库就绪：%s", config.DB_PATH)

    # Web 服务（后台线程，常驻）
    srv = threading.Thread(target=server.main, daemon=True)
    srv.start()
    log.info("Web 服务线程已启动")

    # 抓取守护（按配置决定是否启动）
    s = config.load_settings()
    if s.get("followed") and any(f.get("enabled") for f in s.get("followed", [])):
        try:
            import cookie_provider
            if cookie_provider.get_cookie_header() and cookie_provider.cookie_status(
                    cookie_provider.get_cookie_header()):
                import fetcher
                threading.Thread(target=fetcher.run_daemon, daemon=True).start()
                log.info("抓取守护线程已启动")
            else:
                log.info("尚未配置有效 cookie，抓取守护未启动（可在设置页登录）")
        except Exception as e:
            log.warning("抓取守护启动失败: %s", e)
    else:
        log.info("未配置跟踪用户，仅启动 Web 服务（演示/设置模式）")

    # 打开浏览器
    time.sleep(1.2)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("收到中断，退出")


if __name__ == "__main__":
    main()
