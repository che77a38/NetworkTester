"""开机自启动支持。

各平台实现方式：
  - macOS：``~/Library/LaunchAgents/com.netwatch.launcher.plist`` + ``launchctl``
  - Windows：注册表 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
  - Linux：``~/.config/autostart/netwatch.desktop``
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config as config_mod

LABEL = "com.netwatch.launcher"
DESKTOP_FILE_NAME = "netwatch.desktop"
WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WIN_RUN_VALUE = "NetWatch"


def _is_frozen() -> bool:
    """是否为 PyInstaller 打包后的可执行文件。"""
    return bool(getattr(sys, "frozen", False))


def _launcher_path() -> Path:
    """源码模式下的启动脚本路径。"""
    return config_mod.config_dir() / ("launch.cmd" if sys.platform.startswith("win") else "launch.sh")


def _build_launch_command(exec_path: Optional[str] = None) -> Tuple[str, List[str]]:
    """返回 (可执行文件, 参数列表)。"""
    if exec_path:
        return exec_path, []
    if _is_frozen():
        return sys.executable, []
    # 源码模式：生成一个带 cd 的启动脚本，保证 -m netwatch 能找到包
    root = Path(__file__).resolve().parent.parent
    python = sys.executable or "python3"
    script = _launcher_path()
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            script.write_text(
                "@echo off\r\ncd /d \"%s\"\r\n\"%s\" -m netwatch\r\n" % (root, python),
                encoding="utf-8",
            )
            return str(script), []
        script.write_text(
            "#!/bin/sh\ncd \"%s\"\nexec \"%s\" -m netwatch\n" % (root, python),
            encoding="utf-8",
        )
        try:
            os.chmod(script, 0o755)
        except OSError:
            pass
        return "/bin/sh", [str(script)]
    except OSError:
        # 无法写脚本时退化为 python -c 直接启动，保证功能可用
        code = (
            "import sys; sys.path.insert(0, %r); "
            "from netwatch.main import main; sys.exit(main())" % str(root)
        )
        return python, ["-c", code]


# --------------------------------------------------------------------------- macOS


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % LABEL)


def _macos_enable(exec_path: Optional[str] = None) -> Tuple[bool, str]:
    plist_path = _macos_plist_path()
    program, args = _build_launch_command(exec_path)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [program] + args,
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
    }
    try:
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "wb") as fh:
            plistlib.dump(payload, fh)
    except (OSError, TypeError) as exc:
        return False, "写入 plist 失败: %s" % exc

    # 先卸载旧的，避免重复注册
    subprocess.run(
        ["launchctl", "unload", "-w", str(plist_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    proc = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        # 加载失败不影响下次登录自启，仅作提示
        return True, "已写入 %s，但 launchctl 加载失败（下次登录仍会自启）" % plist_path
    return True, "已设置开机自启动: %s" % plist_path


def _macos_disable() -> Tuple[bool, str]:
    plist_path = _macos_plist_path()
    if not plist_path.exists():
        return True, "未设置开机自启动"
    subprocess.run(
        ["launchctl", "unload", "-w", str(plist_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        plist_path.unlink()
    except OSError as exc:
        return False, "删除 plist 失败: %s" % exc
    return True, "已取消开机自启动"


def _macos_enabled() -> bool:
    return _macos_plist_path().exists()


# --------------------------------------------------------------------------- Windows


def _windows_enable(exec_path: Optional[str] = None) -> Tuple[bool, str]:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False, "当前系统不支持注册表方式自启动"

    program, args = _build_launch_command(exec_path)
    command = program
    if args:
        command = '"%s" %s' % (program, " ".join('"%s"' % a for a in args))
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        with key:
            winreg.SetValueEx(key, WIN_RUN_VALUE, 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)
    except OSError as exc:
        return False, "写入注册表失败: %s" % exc
    return True, "已设置开机自启动: %s" % command


def _windows_disable() -> Tuple[bool, str]:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False, "当前系统不支持注册表方式自启动"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_SET_VALUE
        )
        with key:
            try:
                winreg.DeleteValue(key, WIN_RUN_VALUE)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except OSError as exc:
        return False, "修改注册表失败: %s" % exc
    return True, "已取消开机自启动"


def _windows_enabled() -> bool:
    try:
        import winreg  # type: ignore
    except ImportError:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, WIN_RUN_KEY, 0, winreg.KEY_READ)
        with key:
            value, _ = winreg.QueryValueEx(key, WIN_RUN_VALUE)
            return bool(value)
    except (OSError, FileNotFoundError):
        return False


# --------------------------------------------------------------------------- Linux


def _linux_desktop_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart" / DESKTOP_FILE_NAME


def _linux_enable(exec_path: Optional[str] = None) -> Tuple[bool, str]:
    program, args = _build_launch_command(exec_path)
    exec_line = program
    if args:
        exec_line += " " + " ".join(args)
    else:
        exec_line = '"%s"' % program
    content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=NetWatch\n"
        "Comment=联网检测小工具\n"
        "Exec=%s\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n" % exec_line
    )
    path = _linux_desktop_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return False, "写入 autostart 文件失败: %s" % exc
    return True, "已设置开机自启动: %s" % path


def _linux_disable() -> Tuple[bool, str]:
    path = _linux_desktop_path()
    if not path.exists():
        return True, "未设置开机自启动"
    try:
        path.unlink()
    except OSError as exc:
        return False, "删除 autostart 文件失败: %s" % exc
    return True, "已取消开机自启动"


def _linux_enabled() -> bool:
    return _linux_desktop_path().exists()


# --------------------------------------------------------------------------- 公共接口


def is_supported() -> bool:
    """当前平台是否支持开机自启动。"""
    return sys.platform.startswith("win") or sys.platform == "darwin" or sys.platform.startswith("linux")


def is_enabled() -> bool:
    """当前是否已设置开机自启动。"""
    try:
        if sys.platform.startswith("win"):
            return _windows_enabled()
        if sys.platform == "darwin":
            return _macos_enabled()
        if sys.platform.startswith("linux"):
            return _linux_enabled()
    except Exception:
        return False
    return False


def enable(exec_path: Optional[str] = None) -> Tuple[bool, str]:
    """设置开机自启动，返回 (是否成功, 说明)。"""
    if sys.platform.startswith("win"):
        return _windows_enable(exec_path)
    if sys.platform == "darwin":
        return _macos_enable(exec_path)
    if sys.platform.startswith("linux"):
        return _linux_enable(exec_path)
    return False, "当前平台暂不支持开机自启动"


def disable() -> Tuple[bool, str]:
    """取消开机自启动，返回 (是否成功, 说明)。"""
    if sys.platform.startswith("win"):
        return _windows_disable()
    if sys.platform == "darwin":
        return _macos_disable()
    if sys.platform.startswith("linux"):
        return _linux_disable()
    return False, "当前平台暂不支持开机自启动"


def status() -> Dict[str, Any]:
    """返回自启动状态信息，用于界面展示。"""
    return {
        "platform": sys.platform,
        "supported": is_supported(),
        "enabled": is_enabled(),
        "detail": _status_detail(),
    }


def _status_detail() -> str:
    if sys.platform.startswith("win"):
        return WIN_RUN_KEY + "\\" + WIN_RUN_VALUE
    if sys.platform == "darwin":
        return str(_macos_plist_path())
    if sys.platform.startswith("linux"):
        return str(_linux_desktop_path())
    return ""
