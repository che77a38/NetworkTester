"""NetWatch 配置存储。

配置为纯 JSON，存放于各平台的用户配置目录。读取时与默认配置做深合并，
保证旧版本配置文件在新增配置项后依然可用。
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

APP_NAME = "netwatch"
APP_TITLE = "NetWatch"

DEFAULT_CONFIG: Dict[str, Any] = {
    # 检测目标：支持 "example.com"、"example.com:8443"、"https://example.com/path"
    "targets": ["www.baidu.com", "www.qq.com", "223.5.5.5"],
    # "any" 任一目标成功即视为联网；"all" 需要全部成功
    "target_mode": "any",
    # 单个目标超时（秒）
    "timeout": 5,
    # 检测间隔（秒）
    "interval": 30,
    # 断网 -> 恢复联网时通知
    "notify_on_connect": True,
    # 联网 -> 断网时通知
    "notify_on_disconnect": True,
    # 程序启动时通知一次
    "notify_on_start": False,
    # 用户点击“测试通知”时通知
    "notify_test": True,
    # 开机自启动
    "start_with_os": False,
    # 关闭窗口时最小化到托盘
    "minimize_to_tray": True,
    # 界面语言：zh / en
    "language": "zh",
    # 通知通道开关
    "channels": {
        "desktop": True,
        "sound": True,
        "wecom": False,
        "dingtalk": False,
        "bark": False,
        "serverchan": False,
        "telegram": False,
        "smtp": False,
        "webhook": False,
    },
    # 各通道的参数
    "endpoints": {
        # 企业微信机器人完整 webhook 地址
        "wecom": "",
        # 钉钉机器人完整 webhook 地址（含 access_token）
        "dingtalk": "",
        # Bark 推送地址，例如 https://api.day.app/你的Key
        "bark": "",
        # Server酱 SendKey 或完整 URL
        "serverchan": "",
        # Telegram Bot
        "telegram_token": "",
        "telegram_chat_id": "",
        # SMTP 邮件
        "smtp_host": "",
        "smtp_port": 465,
        "smtp_user": "",
        "smtp_password": "",
        "smtp_to": "",
        "smtp_use_ssl": True,
        # 自定义 Webhook
        "webhook_url": "",
        "webhook_method": "POST",
        "webhook_body": "",
    },
}


def config_dir() -> Path:
    """返回当前平台的配置目录（不存在则创建）。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / APP_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def config_path() -> Path:
    """配置文件完整路径。"""
    return config_dir() / "config.json"


def log_path() -> Path:
    """日志文件完整路径。"""
    return config_dir() / "netwatch.log"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """把 override 合并进 base 的副本，dict 递归合并。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path = None) -> Dict[str, Any]:
    """读取配置文件；文件不存在或损坏时返回默认配置。"""
    target = Path(path) if path else config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, data)


def save_config(cfg: Dict[str, Any], path: Path = None) -> bool:
    """原子写入配置文件，成功返回 True。"""
    target = Path(path) if path else config_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    merged = _deep_merge(DEFAULT_CONFIG, cfg or {})
    payload = json.dumps(merged, ensure_ascii=False, indent=2)
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".config-", suffix=".json", dir=str(target.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, target)
        tmp_name = None
        return True
    except OSError:
        return False
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def update_config(**kwargs: Any) -> Dict[str, Any]:
    """以补丁方式更新配置并保存，返回合并后的配置。"""
    cfg = load_config()
    cfg = _deep_merge(cfg, kwargs)
    save_config(cfg)
    return cfg


def clamp_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """把数值型配置限制在合理区间，防止用户输入异常值。"""
    result = copy.deepcopy(cfg or {})
    try:
        result["timeout"] = max(1, min(30, int(result.get("timeout", 5))))
    except (TypeError, ValueError):
        result["timeout"] = 5
    try:
        result["interval"] = max(5, min(3600, int(result.get("interval", 30))))
    except (TypeError, ValueError):
        result["interval"] = 30
    if result.get("target_mode") not in ("any", "all"):
        result["target_mode"] = "any"
    if result.get("language") not in ("zh", "en"):
        result["language"] = "zh"
    targets = result.get("targets")
    if isinstance(targets, str):
        targets = [line.strip() for line in targets.splitlines() if line.strip()]
    if not isinstance(targets, list):
        targets = []
    cleaned = []
    for item in targets:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item:
            cleaned.append(item)
    result["targets"] = cleaned
    return result
