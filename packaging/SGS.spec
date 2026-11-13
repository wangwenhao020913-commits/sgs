# -*- mode: python ; coding: utf-8 -*-
"""SGS 便携版构建规格（PyInstaller 6.22.x / onedir / console）。

**为什么是 onedir**：单文件包每次启动都要把整包解到 `%TEMP%` 再跑，崩溃时还会留下
`_MEIxxxx` 残留目录；文件夹形态启动是瞬时的，`static/` 也还在磁盘上可查。代价是分发
的时候要发 zip——`build.ps1` 负责压。

**为什么 console=True**：`--noconsole` 下 `sys.stdout` / `sys.stderr` 是 `None`，而
`BaseHTTPRequestHandler.log_message` 会写 stdout ⇒ **每个请求都静默失败**
（PyInstaller #8952）。启动器的日志也要有地方落。

**三个来源根**（都不改源码）：

- `src/`   → 引擎与数据面（`sgs`）与内容包 `files/*.json`（走 `importlib.resources`）；
- `tools/` → 接入层（`sgs_web`）+ 前端静态资源 + `descriptions.json`；
- `packaging/` → 本文件、`launcher.py`、`assets/SGS.ico`。

打包后 `sys._MEIPASS == <exe 目录>/_internal`，模块 `__file__` 也指向那里面，所以
`serve.py` 的 `Path(__file__).parent / "static"` 与 `descriptions.py` 的
`with_name("descriptions.json")` **原样成立**，一行不用改。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

_SPEC_PATH = Path(SPECPATH)
SPEC_ROOT = _SPEC_PATH if _SPEC_PATH.is_dir() else _SPEC_PATH.parent  # packaging/
ROOT = SPEC_ROOT.parent                                               # 仓根（或 staging 树根）
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
WEB = TOOLS / "sgs_web"

# `collect_submodules` / `collect_data_files` 走的是导入系统，先把两个来源根挂上去。
for entry in (str(SRC), str(TOOLS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

# 数据面：内容包 `sgs/data/files/*.json`（PyInstaller 的 datas 目的路径按包内相对路径给）。
#
# **目的路径按"导入布局"而不是源码路径**：`tools/sgs_web` 在源码里是这样放的，但冻结后
# `sgs_web` 是个**顶层包**，模块 `__file__` 是 `_internal/sgs_web/serve.py`。所以
# `serve.py` 的 `Path(__file__).parent / "static"` 与 `descriptions.py` 的
# `with_name("descriptions.json")` 要找的是 `_internal/sgs_web/…`，不是
# `_internal/tools/sgs_web/…`。（第一版就错在这里，自检一次就抓到了。）
datas = collect_data_files("sgs")
datas += [
    (str(WEB / "static"), "sgs_web/static"),
    (str(WEB / "descriptions.json"), "sgs_web"),
]

# 构建戳由 `build.ps1` 写进 staging 树的 `packaging/build-info.json`（不入源码树）。
build_info = SPEC_ROOT / "build-info.json"
if build_info.is_file():
    datas.append((str(build_info), "."))

# 引擎里没有 `importlib.import_module(动态名)`，但技能/效果族的登记面很宽，
# 静态分析漏一个就是运行期 ImportError——整包收进来，成本只有几百 KB。
hiddenimports = collect_submodules("sgs") + collect_submodules("sgs_web")

a = Analysis(
    [str(SPEC_ROOT / "launcher.py")],
    pathex=[str(SRC), str(TOOLS)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pydoc_data", "lib2to3", "pytest", "_pytest", "pip", "setuptools"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SGS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(SPEC_ROOT / "assets" / "SGS.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SGS",
)
