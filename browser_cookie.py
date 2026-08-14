"""从本机浏览器(Chrome / Edge)已登录的雪球 cookie 中静默提取登录态。

设计理念（移植自「雪哨」思路）：
  用户已经在浏览器里登录了雪球，工具直接复用这个登录态，
  而不是再弹一个窗口让用户登录第二次。

实现：
  - 读取浏览器本地 SQLite cookie 库（%LOCALAPPDATA% 下的 Network/Cookies）
  - Windows 上 cookie 值用 DPAPI 加密，用 win32crypt 解密
  - 过滤 xueqiu.com 域，拼成 `name=value; ...` 的 Cookie 头字符串
  - 全程不弹窗、不依赖任何 Chrome 扩展

注意：Chrome 127+ 对部分站点启用 App-Bound Encryption（encrypted_value 以 b'v11' 开头），
此时 win32crypt 无法解密，会返回空；本模块对 v11 明确报错提示，不静默失败。
"""
import os
import sqlite3
import tempfile
import ctypes
import ctypes.wintypes as wt

try:
    import win32crypt  # Windows 上解密 DPAPI
    _HAVE_WIN32 = True
except Exception:
    _HAVE_WIN32 = False

# 浏览器默认 profile 的 cookie 数据库路径（支持环境变量展开）
_PROFILES = {
    "chrome": r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies",
    "edge": r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies",
}


def _resolve(path_tmpl):
    return os.path.expandvars(path_tmpl)


def _decrypt(value, encrypted_value):
    """优先用明文 value；否则用 win32crypt 解 DPAPI（v10）。v11 返回 None 表示解不了。"""
    if value:
        try:
            return value
        except Exception:
            pass
    if not encrypted_value:
        return None
    # v11 = App-Bound Encryption，win32crypt 解不了
    if encrypted_value[:4] == b"v11":
        return None
    if not _HAVE_WIN32:
        return None
    try:
        # 去掉 4 字节前缀（v10\x00\x00\x00 之类）再解
        blob = encrypted_value[4:] if encrypted_value[:4] == b"v10" else encrypted_value
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return data.decode("utf-8", "ignore")
    except Exception:
        return None


def _copy_locked(src, dst):
    """以 FILE_SHARE_READ|WRITE|DELETE 共享标志复制被浏览器独占锁定的文件。"""
    k32 = ctypes.windll.kernel32
    # 必须声明正确的参数/返回类型，否则 HANDLE（64位指针）会被当 32 位截断开
    k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                wt.DWORD, wt.DWORD, wt.HANDLE]
    k32.CreateFileW.restype = wt.HANDLE
    k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                             ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    k32.ReadFile.restype = wt.BOOL
    k32.CloseHandle.argtypes = [wt.HANDLE]
    k32.CloseHandle.restype = wt.BOOL

    GENERIC_READ = 0x80000000
    # 只请求只读共享：浏览器关闭后才能打开；运行中会被独占锁拒（ERROR_SHARING_VIOLATION）
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    h = k32.CreateFileW(src, GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING, 0, None)
    if not h or int(h) in (0, -1, 0xFFFFFFFF):
        raise OSError("无法打开被锁定的 cookie 文件: %s" % src)
    try:
        size = os.path.getsize(src)
        data = bytearray()
        while len(data) < size:
            to_read = min(size - len(data), 1024 * 1024)
            buf = ctypes.create_string_buffer(to_read)
            nread = wt.DWORD(0)
            ok = k32.ReadFile(h, buf, to_read, ctypes.byref(nread), None)
            if not ok or nread.value == 0:
                break
            data.extend(buf.raw[: nread.value])
        if len(data) != size:
            raise OSError("读取被锁定文件不完整: 期望 %d 字节, 实际 %d" % (size, len(data)))
        with open(dst, "wb") as f:
            f.write(bytes(data))
    finally:
        k32.CloseHandle(h)


def _read_xueqiu_rows(db_path):
    """复制 db 到临时文件避免被浏览器锁，读 xueqiu.com 全部 cookie 行。"""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db").name
    try:
        _copy_locked(db_path, tmp)
        conn = sqlite3.connect(tmp)
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT host_key, name, value, encrypted_value FROM cookies "
            "WHERE host_key LIKE ?",
            ("%xueqiu.com%",),
        ).fetchall()
        conn.close()
        return rows
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def build_xueqiu_cookie(browser="chrome"):
    """返回 (cookie_header_str, error_msg)。成功时 error_msg 为 None。"""
    if browser not in _PROFILES:
        return None, "不支持的浏览器标识: %s（可选 chrome / edge）" % browser
    db_path = _resolve(_PROFILES[browser])
    if not os.path.exists(db_path):
        return None, "未找到浏览器 cookie 文件：%s（请确认已安装并登录过雪球）" % db_path

    try:
        rows = _read_xueqiu_rows(db_path)
    except OSError as e:
        msg = str(e)
        if "锁定" in msg or "sharing" in msg.lower() or "Permission" in msg or "32" in msg:
            return None, (
                "浏览器（%s）正在运行并独占锁定了 cookie 文件，无法读取。\n"
                "请先完全关闭 %s（任务栏右键退出，确认进程已结束），再点一次「从浏览器导入」即可。"
                % (browser, browser)
            )
        return None, "读取 cookie 数据库失败：%s" % msg
    except Exception as e:
        return None, "读取 cookie 数据库失败：%s" % e

    if not rows:
        return None, "该浏览器未登录雪球（没有 xueqiu.com 的 cookie）。请先在 %s 打开并登录 xueqiu.com" % browser

    parts = []
    v11_count = 0
    for host_key, name, value, enc in rows:
        if enc and enc[:4] == b"v11":
            v11_count += 1
        v = _decrypt(value, enc)
        if name and v:
            parts.append("%s=%s" % (name, v))

    if not parts:
        if v11_count:
            return None, (
                "该浏览器对雪球 cookie 启用了 App-Bound Encryption（v11），"
                "无法通过本机 DPAPI 解密。请改用 Edge，或在浏览器内手动复制 Cookie。"
            )
        return None, "未能解密出任何雪球 cookie（可能登录态已失效，请重新登录雪球）"

    return "; ".join(parts), None


def auto_detect():
    """依次尝试 chrome / edge，返回第一个成功的 (browser, cookie_str)。"""
    for b in ("chrome", "edge"):
        cookie, err = build_xueqiu_cookie(b)
        if cookie:
            return b, cookie
    return None, None


if __name__ == "__main__":
    for b in ("chrome", "edge"):
        cookie, err = build_xueqiu_cookie(b)
        if err:
            print("[%s] 失败: %s" % (b, err))
        else:
            # 只打印条数与字段名，不打印真实值，避免泄露到终端
            n = cookie.count("=")
            names = ", ".join(p.split("=")[0] for p in cookie.split("; ")[:8])
            print("[%s] 成功: 共 %d 个字段，示例名: %s" % (b, n, names))
