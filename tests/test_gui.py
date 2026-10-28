"""GUI 冒烟测试（无图形环境时自动跳过）。"""

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


HAS_DISPLAY = _has_display()


@unittest.skipUnless(HAS_DISPLAY, "当前环境没有可用的图形界面")
class GuiTestCase(unittest.TestCase):
    def setUp(self):
        from netwatch.gui import NetWatchApp

        self.tmp = tempfile.TemporaryDirectory()
        self.config_file = os.path.join(self.tmp.name, "config.json")
        self.app = NetWatchApp(config_file=self.config_file)
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

    def test_monitor_thread_alive(self):
        self.assertTrue(self.app.monitor.is_alive())


if __name__ == "__main__":
    unittest.main()
