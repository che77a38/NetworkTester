"""NetWatch 命令行入口。"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from . import config as config_mod
from .checker import check_connectivity

LOG_FILE = "netwatch.log"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netwatch",
        description="NetWatch - 跨平台联网检测小工具（图形界面 + 多通道通知）",
    )
    parser.add_argument("--check", action="store_true", help="只做一次检测并输出结果后退出")
    parser.add_argument("--config", metavar="PATH", help="指定配置文件路径")
    parser.add_argument("--minimized", action="store_true", help="启动后最小化到后台/托盘")
    parser.add_argument("--version", action="version", version="NetWatch %s" % __version__)
    return parser


def run_check(config_file: Optional[str] = None) -> int:
    """命令行单次检测，联网返回 0，断网返回 1。"""
    cfg = config_mod.load_config(config_mod.Path(config_file) if config_file else None)
    targets = cfg.get("targets") or []
    timeout = cfg.get("timeout", 5)
    mode = cfg.get("target_mode", "any")

    if not targets:
        print("未配置检测目标，请先运行 GUI 添加目标。")
        return 2

    print("正在检测 %d 个目标 ..." % len(targets))
    result = check_connectivity(targets, timeout, mode)
    for item in result.results:
        flag = "OK  " if item.online else "FAIL"
        detail = "%d ms" % item.latency_ms if item.online else (item.error or "不可达")
        print("  [%s] %-30s %s" % (flag, item.target, detail))
    print("结果: %s（判定方式: %s）" % (result.summary(), "全部通" if mode == "all" else "任一通"))
    return 0 if result.online else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return run_check(args.config)

    from .gui import main as gui_main

    return gui_main(config_file=args.config, minimized=args.minimized)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
