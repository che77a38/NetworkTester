"""startup 模块单元测试（不真正改动系统自启动设置）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import startup  # noqa: E402


class StartupTestCase(unittest.TestCase):
    def test_is_supported(self):
        self.assertTrue(startup.is_supported())

    def test_status_keys(self):
        status = startup.status()
        for key in ("platform", "supported", "enabled", "detail"):
            self.assertIn(key, status)

    def test_enable_disable_return_tuple(self):
        """启用/停用必须返回 (bool, str) 且不抛异常。"""
        ok, message = startup.enable()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(message, str)
        ok2, message2 = startup.disable()
        self.assertIsInstance(ok2, bool)
        self.assertIsInstance(message2, str)

    def test_is_enabled_returns_bool(self):
        self.assertIsInstance(startup.is_enabled(), bool)


if __name__ == "__main__":
    unittest.main()
