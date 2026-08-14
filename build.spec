# PyInstaller 打包规格：将本项目打包成单文件 exe。
# 用法：pyinstaller build.spec  （在含 .venv 的环境里执行）
# 说明：
#  - ui/ 作为数据文件打进 exe，运行时解压到临时目录（server.py 会据此托管静态页）
#  - akshare / playwright 采用懒加载（函数内 import），安装时若未装不会阻断打包；
#    若希望 exe 自带真实行情/登录窗能力，请在打包环境 pip install akshare playwright 后再打包。
#  - 数据目录（data/、settings.json、cookie 缓存）运行时落在 exe 同目录，需写权限。

import os
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# ui 目录作为数据打包
datas = [("ui", "ui")]

a = Analysis(
    ["run.py"],
    pathex=[os.getcwd()],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "config", "db", "engine", "api_adapt", "server",
        "xueqiu_client", "analyst", "cookie_provider", "fetcher", "market",
        "requests", "cryptography",
        # 可选依赖（打包环境装了才会真正打入）
        "akshare", "playwright", "playwright.sync_api",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="xueqiu-analyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,   # 改为 False 可隐藏控制台窗口（双击 exe 体验更好）
    icon=None,
)
