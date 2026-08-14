"""配置层：本地程序（不部署 NAS）。路径、设置、常量，以及抓取/模型/窗口等可配置项。

所有路径均为本地；程序最终用 PyInstaller 打包成单 exe 发布（MIT），源码一并开源。
"""
from pathlib import Path
import json
import os

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "xueqiu_analyzer.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

# ---------- Cookie 来源 ----------
# 普通用户主路径 = 内置登录窗(Playwright)；高级用户可用 v2 加密库；手动粘贴兜底；游客应急降级。
COOKIE_SOURCES = ["login_window", "manual", "v2_store", "visitor"]
DEFAULT_COOKIE_SOURCE = "login_window"
# v2 加密 cookie 库（NAS 上那份，可选复用，不影响默认流程）
V2_COOKIE_STORE = Path(r"D:/SynologyDrive/CODING/xueqiu-watch/cookies_store.json")

# ---------- 回测与窗口 ----------
# 回测最小验证窗口（自然日）：发言 age >= 此值且收益窗口已闭合，才算「已验证」（分析口径，可配置）
VERIFY_DAYS = 3
# 时间线页「待验证」展示窗口（天）：1 / 3 / 自定义；age <= 此值显示待验证，超出归已验证
DEFAULT_PENDING_WINDOW = 3
# 对照基准指数（akshare 代码）
BENCHMARK_INDEX = {"code": "000300", "name": "沪深300"}
# 回测窗口（交易日）
HORIZONS = [3, 5, 10, 20]

# ---------- 发言类型 ----------
# original=原帖, longpost=长文, reply=回帖；默认只抓原帖+长文（回帖观点片面，不抓）
POST_TYPES = {"original": "原帖", "longpost": "长文", "reply": "回帖"}
DEFAULT_FETCH_TYPES = ["original", "longpost"]

# ---------- 抓取调度 ----------
POLL_MINUTES = 10              # 增量轮询间隔（分钟）
DEFAULT_BACKFILL_DAYS = 30     # 默认回填时间范围（天）
BACKFILL_CHOICES = [10, 30, 90]

# ---------- 模型选择 ----------
# DeepSeek V4 系列：flash（默认·高速低价）/ pro（高精度）。均 OpenAI 兼容格式。
# 注意：deepseek-chat / deepseek-reasoner 已是 deepseek-v4-flash 非思考/思考模式的别名，将逐步弃用。
MODEL_PROVIDERS = {
    "deepseek-flash": {"label": "DeepSeek V4 Flash（默认·高速低价）", "model": "deepseek-v4-flash",
                       "base": "https://api.deepseek.com"},
    "deepseek-pro":   {"label": "DeepSeek V4 Pro（高精度）", "model": "deepseek-v4-pro",
                       "base": "https://api.deepseek.com"},
    "qwen":           {"label": "通义千问", "model": "qwen-plus",
                       "base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    "glm":            {"label": "智谱 GLM", "model": "glm-4-flash",
                       "base": "https://open.bigmodel.cn/api/paas/v4"},
}
DEFAULT_PROVIDER = "deepseek-flash"

# ---------- 自定义分析 skill ----------
SKILL_DIR = BASE_DIR / "analysis_skills"
SKILL_DIR.mkdir(parents=True, exist_ok=True)

# ---------- 主体识别规则 ----------
# single = 每条发言仅产出一个主体事件；其余对比/衬托标的仅展示、不进回测（情形 B：只取主要意图那个）
SUBJECT_RULE = "single"


# ---------- settings.json 读写 ----------
DEFAULT_SETTINGS = {
    "cookie": "",
    "cookie_source": DEFAULT_COOKIE_SOURCE,
    "provider": DEFAULT_PROVIDER,
    "api_key": "",
    "fetch_types": DEFAULT_FETCH_TYPES,
    "backfill_days": DEFAULT_BACKFILL_DAYS,
    "pending_window": DEFAULT_PENDING_WINDOW,
    "followed": [],   # [{xid, name, enabled}]
    "skills": [],     # 启用的 skill 模块名
}


def load_settings() -> dict:
    if SETTINGS_PATH.exists():
        try:
            s = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            merged = dict(DEFAULT_SETTINGS)
            merged.update(s)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def add_followed(xid: str, name: str = "", enabled: bool = True) -> dict:
    s = load_settings()
    for it in s["followed"]:
        if it.get("xid") == xid:
            it["name"] = name or it.get("name", "")
            it["enabled"] = enabled
            break
    else:
        s["followed"].append({"xid": xid, "name": name, "enabled": enabled})
    save_settings(s)
    return s


def remove_followed(xid: str) -> dict:
    s = load_settings()
    s["followed"] = [it for it in s["followed"] if it.get("xid") != xid]
    save_settings(s)
    return s
