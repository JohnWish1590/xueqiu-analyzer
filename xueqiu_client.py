"""雪球 timeline 抓取客户端（全新独立副本，复用 v2 xueqiu.py 的 WAF 绕过思路）。

核心思路（照搬已验证可用的 v2）：
- 用 api.xueqiu.com 子域直连绕开阿里云 WAF（主域 /v4/statuses/* 会被 JS 挑战页拦成 HTML）；
- 请求头带 Cookie / User-Agent / Referer / X-Requested-With；
- _is_waf 检测返回是不是 JSON（被 WAF 拦则返回 HTML，抛 WafBlocked）；
- _ts 解析雪球各种 created_at 格式；
- resolve_user_ids 用 /query/v1/search/user.json 把昵称解析成数字 id。
- fetch_user_timeline 走 /v4/statuses/user_timeline.json?user_id=&page=&count=&since_id=。

本文件只做「抓取 + 归一化」，不做任何 AI 分析。

依赖：标准库 + requests（playwright 不在此用）。
"""
import json
import logging
import re
import time

log = logging.getLogger("xueqiu_client")

# ---------- 常量 ----------
XQ_BASE = "https://xueqiu.com"
# 关键：api.xueqiu.com 子域不挂阿里云 WAF，服务器/本地直连可用；
# 主域 xueqiu.com 的 /v4/statuses/* 会被 WAF JS 挑战拦截（返回 HTML 而非 JSON）。
XQ_API = "https://api.xueqiu.com"
XQ_SEARCH = "/query/v1/search/user.json"

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

# 单页最大条数 / 单用户翻页上限（防失控）
_PAGE_COUNT = 20
_MAX_PAGES = 200
_LONGPOST_CHARS = 400  # text 超过此长度视为长文


class WafBlocked(Exception):
    """请求被阿里云 WAF 挑战页拦截（返回的不是 JSON）。"""


# ---------- 通用工具 ----------
def _is_waf(body: str) -> bool:
    """判断响应体是不是被 WAF 拦下来的 HTML 挑战页。"""
    if not body:
        return False
    head = body.lstrip()[:1]
    if head in ("[", "{"):
        return False
    return ("aliyun_waf" in body) or ("renderData" in body) or head == "<"


def _req(url: str, cookie_header: str, timeout: int = 12) -> str:
    """发起请求，读取 body；被 WAF 拦截则抛 WafBlocked，否则返回字符串。"""
    headers = {
        "Cookie": cookie_header,
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
        "Referer": "https://xueqiu.com/",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        import requests
    except Exception as e:
        raise RuntimeError("requests 未安装，无法发起网络请求：%s" % e)
    r = requests.get(url, headers=headers, timeout=timeout)
    body = r.text
    if _is_waf(body):
        raise WafBlocked(url)
    return body


def _extract_list(data: str):
    """从响应体里尽量抽取出帖子列表（兼容 list / {statuses|list|items|data}）。"""
    try:
        d = json.loads(data)
    except Exception:
        return []
    if isinstance(d, list):
        return d
    for k in ("statuses", "list", "items", "data"):
        v = d.get(k)
        if isinstance(v, list):
            return v
    return []


def _strip(html: str) -> str:
    """去掉 HTML 标签并规整空白，得到纯文本。"""
    if not html:
        return ""
    t = re.sub(r"<[^>]+>", "", html or "")
    return (t.replace("&nbsp;", " ").replace("\r", " ")
            .replace("\n", " ").strip())


def _ts(s) -> int:
    """把雪球 created_at 解析成 epoch 秒。

    支持纯数字时间戳，以及 'Fri Aug 08 15:30:00 +0800 2025' 这类格式。
    解析失败返回 0。
    """
    if not s:
        return 0
    try:
        v = int(s)
    except Exception:
        v = None
    if v is not None:
        # 雪球部分接口返回毫秒时间戳（13 位，>1e11），秒级（<=1e11，<2033 年）原样。
        # 毫秒转秒，避免 time.gmtime 年份溢出报 OSError(22)。
        if v > 10 ** 11:
            v = v / 1000.0
        return v
    try:
        return int(time.mktime(time.strptime(str(s),
                          "%a %b %d %H:%M:%S %z %Y")))
    except Exception:
        return 0


def _iso(s) -> str:
    """把雪球 created_at 规整成 'YYYY-MM-DD HH:MM:SS'（按北京时间 +0800 输出）。"""
    epoch = _ts(s)
    if not epoch:
        return ""
    # _ts 已得到正确 UTC epoch，这里加 8 小时按东八区格式化，结果不受本机时区影响
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(epoch + 8 * 3600))
    except Exception:
        return ""


def classify_post(raw: dict) -> str:
    """对单条原始帖子分类：'reply' / 'longpost' / 'original'。

    规则：
    - 含 in_reply_to_status_id，或 retweeted_status（转发），或纯文本以 '@' / '//@' 开头，
      或含 '回复 @' → reply；
    - 否则 text 长度 > 400 或存在 long_text 字段（且非回复）→ longpost；
    - 其余 → original。
    """
    text = _strip(raw.get("text", ""))
    if (raw.get("in_reply_to_status_id")
            or raw.get("retweeted_status")
            or text.startswith("@")
            or text.startswith("//@")
            or ("回复 @" in text)):
        return "reply"
    if len(text) > _LONGPOST_CHARS or raw.get("long_text"):
        return "longpost"
    return "original"


def _normalize(p: dict, post_type: str) -> dict:
    """把单条原始帖子归一成统一结构。"""
    user = (p.get("user") or {}).get("screen_name", "")
    ca = p.get("created_at", "")
    epoch = _ts(ca)
    return {
        "id": str(p.get("id")),            # 字符串帖子 id（pid）
        "user": user,                       # 昵称
        "text": _strip(p.get("text", "")),  # 纯文本
        "created_at": _iso(ca),             # ISO 'YYYY-MM-DD HH:MM:SS'
        "time": epoch,                      # epoch 秒
        "post_type": post_type,             # original/longpost/reply
        "raw": p,                           # 原始 dict，供上层审计/扩展
    }


# ---------- 核心抓取 ----------
def fetch_user_timeline(user_id, cookie_header: str, page: int = 1,
                        count: int = 20, since_id=None, types=None) -> list:
    """拉取某用户一页时间线，按 types 过滤后返回归一化 list[dict]。

    types 为允许保留的 post_type 集合（如 {'original','longpost'}），None 表示全抓。
    候选 URL 顺序：api 子域（无 WAF，首选）→ 主域 v4 → 主域旧版。
    """
    since = "&since_id=%s" % since_id if since_id else ""
    path = ("/v4/statuses/user_timeline.json?user_id=%s&page=%s&count=%s%s"
            % (user_id, page, count, since))
    candidates = [
        XQ_API + path,
        XQ_BASE + path,
        "%s/statuses/user_timeline.json?user_id=%s&page=%s&count=%s%s"
        % (XQ_BASE, user_id, page, count, since),
    ]
    posts = None
    last_err = None
    for u in candidates:
        try:
            posts = _extract_list(_req(u, cookie_header))
            break
        except WafBlocked:
            last_err = "WAF"
            continue
        except Exception as e:
            last_err = e
            continue
    if posts is None:
        log.warning("fetch_user_timeline 失败: user=%s err=%s", user_id, last_err)
        return []

    out = []
    for p in posts:
        pt = classify_post(p)
        if types and pt not in types:
            continue
        out.append(_normalize(p, pt))
    return out


def resolve_user_ids(entries, cookie_header: str) -> list:
    """把 followed 列表里的非数字项当昵称，用搜索接口解析成数字 id。

    解析失败保留原值（纯数字原样返回）。返回 list[str]。
    """
    import urllib.parse
    out = []
    for e in entries:
        s = str(e)
        if s.isdigit():
            out.append(s)
            continue
        try:
            q = urllib.parse.quote(s)
            sub = "%s?q=%s&count=5" % (XQ_SEARCH, q)
            data = None
            for u in (XQ_API + sub, XQ_BASE + sub):
                try:
                    data = _req(u, cookie_header)
                    break
                except Exception:
                    continue
            if data is None:
                raise RuntimeError("search 接口全部失败")
            d = json.loads(data)
            users = d.get("list") or []
            hit = None
            for u in users:
                if u.get("screen_name") == s or s in (u.get("screen_name") or ""):
                    hit = u
                    break
            if not hit and users:
                hit = users[0]
            if hit and hit.get("id"):
                log.info("resolve %s -> %s (%s)", s, hit["id"], hit.get("screen_name"))
                out.append(str(hit["id"]))
                continue
        except Exception as ex:
            log.warning("昵称 %s 解析失败: %s", s, ex)
        out.append(s)
    return out


def fetch_incremental(followed_ids, cookie_header: str,
                      since_id_map: dict = None, types=None) -> list:
    """对每位用户用 since_id（每用户上次最大 pid）拉增量，返回新帖子 list[dict]。

    since_id_map: {user_id: last_pid}。非数字 id 直接跳过（无法拉取）。
    """
    since_id_map = since_id_map or {}
    all_posts = []
    seen = set()
    for uid in followed_ids:
        uid = str(uid)
        if not uid.isdigit():
            continue
        last_pid = since_id_map.get(uid)
        for page in range(1, _MAX_PAGES + 1):
            batch = fetch_user_timeline(uid, cookie_header, page=page,
                                        count=_PAGE_COUNT, since_id=last_pid, types=types)
            if not batch:
                break
            for p in batch:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                all_posts.append(p)
    all_posts.sort(key=lambda x: x["time"], reverse=True)
    return all_posts


def fetch_backfill(followed_ids, cookie_header: str, days: int = 30,
                   types=None, stop_event=None) -> list:
    """对每位用户从 page=1 翻页回填，直到帖子早于 days 天前停止（时间范围全量回填）。

    停止条件：(now - created_at).days > days 。返回归一化 list[dict]。
    诊断：每个用户首页原始响应落盘到 data/debug_first_page.json，并记录时间范围日志。
    """
    now = time.time()
    all_posts = []
    seen = set()
    for uid in followed_ids:
        uid = str(uid)
        if not uid.isdigit():
            continue
        if stop_event is not None and stop_event.is_set():
            log.info("fetch_backfill 收到停止信号，中断翻页")
            break
        for page in range(1, _MAX_PAGES + 1):
            if stop_event is not None and stop_event.is_set():
                log.info("fetch_backfill 收到停止信号，中断翻页(uid=%s)", uid)
                break
            since_id = -1 if page == 1 else None   # 首页强制 latest
            batch = fetch_user_timeline(uid, cookie_header, page=page,
                                        count=_PAGE_COUNT, types=types,
                                        since_id=since_id)
            if not batch:
                break
            if page == 1:
                try:
                    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
                    os.makedirs(data_dir, exist_ok=True)
                    dbg = os.path.join(data_dir, "debug_first_page_%s.json" % uid)
                    with open(dbg, "w", encoding="utf-8") as fh:
                        json.dump(batch, fh, ensure_ascii=False)
                except Exception:
                    pass
            times = [p["time"] for p in batch if p["time"]]
            earliest = time.strftime("%Y-%m-%d", time.gmtime(min(times) + 8 * 3600)) if times else "?"
            latest = time.strftime("%Y-%m-%d", time.gmtime(max(times) + 8 * 3600)) if times else "?"
            log.info("fetch_backfill uid=%s page=%d 条数=%d 时间范围=%s~%s", uid, page, len(batch), earliest, latest)
            stop = False
            for p in batch:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                # 早于 days 天前的帖子：结束该用户翻页；时间解析失败(=0)的帖不丢弃，仅告警
                if p["time"]:
                    if (now - p["time"]) / 86400 > days:
                        stop = True
                        continue
                elif page == 1:
                    log.warning("uid=%s 有帖子 created_at 解析失败(time=0)，已保留不丢弃", uid)
                all_posts.append(p)
            if stop:
                break
            if len(batch) < _PAGE_COUNT:
                break  # 末页
    all_posts.sort(key=lambda x: x["time"], reverse=True)
    return all_posts


def fetch_followed_groups(cookie_header: str, group_name: str = "特别关注") -> list:
    """读取雪球指定分组（默认「特别关注」）的成员列表。

    返回 list[dict]: [{"id": "<str>", "name": "<screen_name>"}]
    两步：① groups.json 找分组 id；② members.json?gid=<id> 翻页取成员。
    """
    # 1) 找分组 id
    gbody = _req(XQ_BASE + "/friendships/groups.json", cookie_header)
    try:
        groups = json.loads(gbody)
    except Exception:
        raise RuntimeError("雪球分组接口返回非 JSON（可能被 WAF 拦截）")
    if not isinstance(groups, list):
        raise RuntimeError("雪球分组接口返回格式异常")
    gid = None
    for g in groups:
        if g.get("name") == group_name:
            gid = g.get("id")
            break
    if gid is None:
        names = [str(g.get("name")) for g in groups]
        raise RuntimeError("未找到「%s」分组，已有分组: %s" % (group_name, "、".join(names)))

    # 2) 翻页取成员（members 接口用 gid 参数，非 group_id）
    out, seen = [], set()
    for page in range(1, 50):
        url = XQ_BASE + "/friendships/groups/members.json?gid=%s&page=%d&count=20" % (gid, page)
        try:
            d = json.loads(_req(url, cookie_header))
        except Exception as e:
            raise RuntimeError("读取分组成员失败: %s" % e)
        users = d.get("users") if isinstance(d, dict) else None
        if not users:
            break
        for u in users:
            uid = u.get("id")
            if uid is None:
                continue
            uid = str(uid)
            if uid in seen:
                continue
            seen.add(uid)
            name = u.get("screen_name") or u.get("name") or uid
            out.append({"id": uid, "name": name})
        if page >= int(d.get("maxPage", 1) or 1):
            break
    return out


if __name__ == "__main__":
    # 仅结构自测；真正抓取需要有效 cookie
    print("xueqiu_client loaded")
