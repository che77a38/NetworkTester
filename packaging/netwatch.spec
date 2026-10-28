# -*- mode: python ; coding: utf-8 -*-
"""
NetWatch —— PyInstaller 打包配置（单文件 / onefile）

用法（在仓库根目录执行）::

    python -m pip install -r requirements-dev.txt
    pyinstaller packaging/netwatch.spec --noconfirm --clean

产物
----
* Windows / Linux：``dist/netwatch``（Windows 为 ``dist/netwatch.exe``），单文件可执行程序。
* macOS：``dist/netwatch.app`` 应用包。PyInstaller 在 macOS 上不支持
  "onefile + .app" 组合（.app 必须包裹 onedir 目录），因此 macOS 走标准的
  COLLECT + BUNDLE 路线。若必须在 macOS 输出单文件，可设置环境变量
  ``NETWATCH_ONEFILE=1``（此时不再生成 .app）。

说明
----
* 入口函数为 ``netwatch.main:main``。本文件在构建时动态生成一个极小的启动脚本
  （不修改 ``netwatch/`` 包内任何源文件），再交给 PyInstaller 做依赖分析。
* ``tkinter`` 相关模块按显式 hiddenimports 列出，避免部分环境下被静态分析漏掉。
* ``pystray`` 为可选依赖：装了才打包，并按当前平台补齐其后端依赖
  （macOS: pyobjc，Windows: pywin32，Linux: python-xlib / PyGObject）。
* 兼容 PyInstaller >= 6.0（6.x 已移除 ``cipher`` / ``win_no_prefer_redirects``
  等历史参数，本文件不使用它们）。
"""

import os
import sys
import tempfile
from pathlib import Path

APP_NAME = "netwatch"
BUNDLE_ID = "com.netwatch.app"
VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 定位仓库根目录（含 netwatch/main.py 的那一层）
# ---------------------------------------------------------------------------


def _find_project_root():
    """在若干候选位置向上查找包含 ``netwatch/main.py`` 的目录。"""
    candidates = []
    spec_path = globals().get("SPECPATH")
    if spec_path:
        candidates.append(Path(spec_path))
    candidates.append(Path.cwd())

    for candidate in candidates:
        current = Path(candidate).resolve()
        for _ in range(6):
            if (current / "netwatch" / "main.py").is_file():
                return current
            if current.parent == current:
                break
            current = current.parent
    return Path(os.getcwd()).resolve()


PROJECT_ROOT = _find_project_root()

# 强制 onefile（默认：macOS 输出 .app，其余平台输出单文件）
FORCE_ONEFILE = os.environ.get("NETWATCH_ONEFILE", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAKE_APP = sys.platform == "darwin" and not FORCE_ONEFILE

# ---------------------------------------------------------------------------
# 生成入口启动脚本（不触碰 netwatch/ 包内源码）
# ---------------------------------------------------------------------------

LAUNCHER_DIR = Path(tempfile.mkdtemp(prefix="netwatch-pyinstaller-"))
LAUNCHER = LAUNCHER_DIR / "netwatch_launcher.py"
LAUNCHER.write_text(
    "# -*- coding: utf-8 -*-\n"
    '"""NetWatch 打包入口（由 packaging/netwatch.spec 自动生成，请勿手工修改）。"""\n'
    "import sys\n"
    "\n"
    "from netwatch.main import main\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    sys.exit(main())\n",
    encoding="utf-8",
)

# ---------------------------------------------------------------------------
# hiddenimports
# ---------------------------------------------------------------------------


def _module_exists(name):
    """判断模块是否可导入（用于可选依赖探测）。"""
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _collect_pystray_imports():
    """pystray 是可选依赖：只有装了才收集其子模块与平台后端。"""
    if not _module_exists("pystray"):
        return []

    imports = {"pystray"}
    try:
        from PyInstaller.utils.hooks import collect_submodules

        imports.update(collect_submodules("pystray"))
    except Exception:
        pass

    if sys.platform == "darwin":
        # pystray 的 macOS 后端依赖 pyobjc
        imports.update({"Cocoa", "objc", "Foundation", "AppKit"})
    elif sys.platform.startswith("win"):
        # pystray 的 Windows 后端依赖 pywin32
        imports.update({"win32api", "win32con", "win32gui", "win32ui"})
    else:
        # pystray 的 Linux 后端：X11 走 python-xlib，AppIndicator 走 PyGObject
        imports.update({"Xlib", "Xlib.display", "Xlib.X", "gi"})
        if _module_exists("gi"):
            imports.update(
                {
                    "gi.repository",
                    "gi.repository.AyatanaAppIndicator3",
                    "gi.repository.Gtk",
                }
            )
        if _module_exists("PIL"):
            imports.update({"PIL.Image", "PIL.ImageDraw"})

    return sorted(imports)


HIDDEN_IMPORTS = [
    # tkinter —— GUI 必需
    "tkinter",
    "tkinter.ttk",
    "tkinter.font",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.scrolledtext",
    "tkinter.simpledialog",
    # 纯标准库但容易被静态分析漏掉的模块
    "encodings.idna",
    "email.mime.text",
    "email.mime.multipart",
    "http.client",
    "urllib.parse",
    "concurrent.futures",
    "xml.etree.ElementTree",
]
HIDDEN_IMPORTS += _collect_pystray_imports()

EXCLUDES = [
    "tkinter.test",
    "test",
    "pytest",
    "_pytest",
    "pydoc_data",
    "matplotlib",
    "numpy",
    "pandas",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "IPython",
    "notebook",
]

# ---------------------------------------------------------------------------
# 附加数据文件（README / LICENSE / CHANGELOG）
# ---------------------------------------------------------------------------

DATAS = []
for _name in ("README.md", "LICENSE", "CHANGELOG.md"):
    _src = PROJECT_ROOT / _name
    if _src.is_file():
        DATAS.append((str(_src), "."))


# ---------------------------------------------------------------------------
# 可选图标（存在才使用；Windows 用 .ico，macOS 用 .icns）
# ---------------------------------------------------------------------------


def _find_icon():
    names = ("icon.ico", "netwatch.ico") if sys.platform.startswith("win") else ("icon.icns", "netwatch.icns")
    for folder in (PROJECT_ROOT / "docs", PROJECT_ROOT / "packaging", PROJECT_ROOT):
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    return None


ICON = _find_icon()

# ---------------------------------------------------------------------------
# 分析与打包
# ---------------------------------------------------------------------------

a = Analysis(
    [str(LAUNCHER)],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

if MAKE_APP:
    # ---- macOS：onedir + BUNDLE -> dist/netwatch.app ----
    exe = EXE(
        pyz,
        a.scripts,
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=APP_NAME,
    )
    app = BUNDLE(
        coll,
        name="{}.app".format(APP_NAME),
        icon=ICON,
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": "NetWatch",
            "CFBundleDisplayName": "NetWatch",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
        },
    )
else:
    # ---- Windows / Linux（以及 macOS 强制 onefile）：单文件 ----
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
    )
