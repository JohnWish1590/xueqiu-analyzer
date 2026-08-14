"""Cookie 统一入口：对外只暴露一个函数 get_cookie_header()，返回可直接塞进请求头
``Cookie`` 的 ``"k=v; k2=v2"`` 字符串。

设计目标：
- 普通用户主路径 = 内置登录窗（Playwright 起真实浏览器，手动登录后自动读 cookie）；
- 高级用户可复用 v2 的加密 cookie 库（Fernet）；
- 手动粘贴兜底；游客 cookie 仅应急降级。
- 任何来源失败都不让程序崩溃：层层回退 manual -> visitor 并打日志。

仅依赖：标准库 + config（本项目） + 可选 cryptography / playwright。
cryptography 与 playwright 缺失时均优雅降级，绝不抛未捕获异常。
"""
import base64
import json
import logging
import os
import time
from pathlib import Path

import config

log = logging.getLogger("cookie_provider")

# ---------- 常量 ----------
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 登录窗 cookie 的本地加密缓存（与 v2 同款 Fernet 思路，密钥同目录 secret.key）
_COOKIE_CACHE = config.DATA_DIR / "cookies_cache.enc"

# 等待用户手动登录的最长秒数
_LOGIN_TIMEOUT = 300

# 游客 cookie（应急，多数接口会失效）
VISITOR_COOKIE = (
    'xq_a_token=visitor_placeholder; xq_r_token=visitor; '
    'device_id=visitor; u=visitor'
)


# ---------- cryptography 优雅降级 ----------
try:
    from cryptography.fernet import Fernet
    _HAVE_CRYPTO = True
except Exception:
    _HAVE_CRYPTO = False


def _fernet_for(store_path: Path):
    """为某个加密存储文件找到/生成 Fernet 密钥（secret.key 与存储文件同目录）。

    找不到密钥文件则生成；若环境无 cryptography，则生成一段固定填充串做
    base64 轻量混淆（仅本地使用可接受的降级），并打 warning。
    """
    key_file = Path(store_path).parent / "secret.key"
    if key_file.exists():
        return _make_cipher(key_file.read_bytes())
    if _HAVE_CRYPTO:
        key = Fernet.generate_key()
    else:
        key = base64.b64encode(b"xueqiu-analyzer-local-key-pad-1234567890ab")
        log.warning("cryptography 未安装，cookie 仅做轻量混淆而非加密存储。"
                    "建议：pip install cryptography")
    key_file.write_bytes(key)
    try:
        os.chmod(key_file, 0o600)
    except Exception:
        pass
    return _make_cipher(key)


def _make_cipher(key: bytes):
    if _HAVE_CRYPTO:
        return Fernet(key)
    return None  # 降级：调用方改用 base64


# ---------- 加密缓存读写（登录窗用） ----------
def _write_cache_header(header: str) -> None:
    """把 cookie 头加密写入本地缓存文件。"""
    try:
        cipher = _fernet_for(_COOKIE_CACHE)
        data = header.encode("utf-8")
        tok = cipher.encrypt(data) if cipher else base64.b64encode(data)
        _COOKIE_CACHE.write_bytes(tok)
    except Exception as e:
        log.warning("缓存 cookie 失败: %s", e)


def _read_cache_header() -> str:
    """读本地加密缓存；不存在或失败返回空串。"""
    if not _COOKIE_CACHE.exists():
        return ""
    try:
        cipher = _fernet_for(_COOKIE_CACHE)
        tok = _COOKIE_CACHE.read_bytes()
        data = cipher.decrypt(tok) if cipher else base64.b64decode(tok)
        return data.decode("utf-8")
    except Exception as e:
        log.warning("读缓存 cookie 失败: %s", e)
        return ""


# ---------- 各来源实现 ----------
def launch_login_window() -> str:
    """用 Playwright 起真实 Chromium 打开雪球，等用户手动登录。

    检测到 xq_a_token cookie 出现即视为登录成功，读取 xueqiu 域全部 cookie 拼成
    header 返回，并：1) 加密缓存到本地；2) 明文写回 config 的 cookie 字段。

    Playwright 未安装时抛出带清晰安装指引的 RuntimeError，由 get_cookie_header 兜底。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright 未安装，无法启动登录窗。请先执行：\n"
            "  pip install playwright\n"
            "  playwright install chromium\n"
            f"（底层错误：{e}）"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(user_agent=UA)
        page = context.new_page()
        page.goto("https://xueqiu.com", wait_until="domcontentloaded")
        log.info("请在打开的浏览器中登录雪球（扫码/账号密码）。"
                 "检测到登录成功后窗口会自动关闭…")

        deadline = time.time() + _LOGIN_TIMEOUT
        token = None
        while time.time() < deadline:
            for c in context.cookies():
                if c.get("name") == "xq_a_token" and c.get("value"):
                    token = c
                    break
            if token:
                break
            time.sleep(1)

        if not token:
            browser.close()
            raise TimeoutError("等待登录超时（%d 秒），未检测到 xq_a_token。" % _LOGIN_TIMEOUT)

        # 只取 xueqiu 域 cookie，拼成 header
        cookies = [c for c in context.cookies()
                   if "xueqiu.com" in (c.get("domain") or "")]
        header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        browser.close()

    # 加密缓存 + 写回设置
    _write_cache_header(header)
    try:
        s = config.load_settings()
        s["cookie"] = header
        config.save_settings(s)
    except Exception as e:
        log.warning("写回 cookie 到 settings.json 失败: %s", e)

    log.info("登录成功，已缓存 cookie。")
    return header


def _read_v2_store() -> str:
    """从 v2 的 Fernet 加密 cookie 库读取 xueqiu 的 header 字符串。

    config.V2_COOKIE_STORE 指向一个 cookies.json.enc（结构 {cookies:{site:{domain,header}}}），
    密钥 secret.key 与存储文件同目录（照 v2 cookies_store.py 的逻辑）。
    """
    store = config.V2_COOKIE_STORE
    if not store or not Path(store).exists():
        raise FileNotFoundError("v2 cookie 库不存在：%s" % store)
    cipher = _fernet_for(store)
    tok = Path(store).read_bytes()
    data = cipher.decrypt(tok) if cipher else base64.b64decode(tok)
    cookies = json.loads(data.decode("utf-8")).get("cookies", {})
    hdr = cookies.get("xueqiu", {}).get("header", "")
    if not hdr:
        raise ValueError("v2 库中未找到 xueqiu 的 cookie 记录")
    return hdr


def visitor_cookie() -> str:
    """返回一个尽力而为的游客 cookie。

    注意：可能无效，仅应急降级使用——雪球多数接口需要有效登录态。
    """
    return VISITOR_COOKIE


def cookie_status(header: str) -> bool:
    """简单判断 header 是否看起来有效（至少含 xq_a_token 或 xqat）。"""
    if not header:
        return False
    return ("xq_a_token=" in header) or ("xqat=" in header)


# ---------- 统一入口 ----------
def _login_window_or_cache() -> str:
    """优先用加密缓存；缓存无效才真正弹登录窗。"""
    cached = _read_cache_header()
    if cached and cookie_status(cached):
        return cached
    return launch_login_window()


def get_cookie_header(source: str = None) -> str:
    """获取雪球 Cookie 头的统一入口，返回 'k=v; k2=v2'。

    source 默认 None 时取 config.load_settings()['cookie_source']
    （候选值 login_window / manual / v2_store / visitor）。
    任何来源失败都回退到 manual、再回退到 visitor，绝不让程序崩溃。
    """
    s = source or config.load_settings().get("cookie_source", "login_window")
    log.debug("cookie 来源: %s", s)

    try:
        if s == "login_window":
            hdr = _login_window_or_cache()
        elif s == "manual":
            hdr = config.load_settings().get("cookie", "")
        elif s == "v2_store":
            hdr = _read_v2_store()
        elif s == "visitor":
            hdr = visitor_cookie()
        else:
            log.warning("未知 cookie 来源 %s，按 visitor 处理", s)
            hdr = visitor_cookie()

        if hdr and cookie_status(hdr):
            return hdr
        log.warning("来源 %s 未给出有效 cookie，回退 manual/visitor", s)
    except Exception as e:
        log.warning("来源 %s 获取失败：%s，回退", s, e)

    # 回退：manual
    try:
        hdr = config.load_settings().get("cookie", "")
        if hdr and cookie_status(hdr):
            return hdr
    except Exception:
        pass

    # 回退：v2_store（雪哨 / cookie管家，若已缓存则自动复用，无需用户再粘贴）
    try:
        hdr = _read_v2_store()
        if hdr and cookie_status(hdr):
            return hdr
    except Exception:
        pass

    # 最终回退：visitor
    return visitor_cookie()


if __name__ == "__main__":
    print("cookie_provider loaded; source =",
          config.load_settings().get("cookie_source"))
