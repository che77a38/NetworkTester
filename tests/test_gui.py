"""GUI 冒烟测试。

无图形环境时自动跳过；CI 环境（``CI=true``）同样跳过——CI runner 没有
真实桌面会话，Windows runner 上 Tk 窗口的创建与销毁可能长时间挂起，
拖垮整条流水线。GUI 的实际行为由发布产物在真实桌面环境验证。
"""

import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _has_display() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:
        return False


#: CI 环境标识（GitHub Actions 会注入 CI=true）
IS_CI = os.environ.get("CI", "").lower() in ("1", "true", "yes")

HAS_DISPLAY = _has_display() and not IS_CI


@unittest.skipUnless(
    HAS_DISPLAY, "当前环境没有可用的图形界面，或处于 CI 环境（跳过 GUI 冒烟测试）"
)
class GuiTestCase(unittest.TestCase):
    def setUp(self):
        from netwatch.gui import NetWatchApp

        self.tmp = tempfile.TemporaryDirectory()
        self.config_file = os.path.join(self.tmp.name, "config.json")
        # start_monitor=False：不启动监控线程，测试过程不发起真实网络探测
        self.app = NetWatchApp(config_file=self.config_file, start_monitor=False)
        self.app.update_idletasks()

    def tearDown(self):
        try:
            self.app.monitor.stop()
            self.app.destroy()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_widgets_created(self):
        self.assertTrue(hasattr(self.app, "targets_text"))
        self.assertTrue(hasattr(self.app, "log_text"))
        self.assertEqual(len(self.app.channel_vars), 9)

    def test_collect_config_targets(self):
        self.app.targets_text.delete("1.0", "end")
        self.app.targets_text.insert("1.0", "example.com\nexample.org:8443")
        cfg = self.app._collect_config()
        self.assertEqual(cfg["targets"], ["example.com", "example.org:8443"])

    def test_placeholder_is_not_saved_as_value(self):
        """灰色占位提示不应被当成用户填写的值保存。"""
        cfg = self.app._collect_config()
        self.assertEqual(cfg["endpoints"]["wecom"], "")

    def test_real_value_is_saved(self):
        self.app.field_vars["wecom"].set("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
        cfg = self.app._collect_config()
        self.assertTrue(cfg["endpoints"]["wecom"].endswith("key=x"))

    def test_save_creates_config_file(self):
        self.app.targets_text.delete("1.0", "end")
        self.app.targets_text.insert("1.0", "example.com")
        self.app._save(silent=True)
        self.assertTrue(os.path.exists(self.config_file))

    def test_monitor_not_started_in_test_mode(self):
        """测试模式下监控线程不运行，避免测试触发真实网络探测。"""
        self.assertFalse(self.app.monitor.is_alive())


if __name__ == "__main__":
    unittest.main()
