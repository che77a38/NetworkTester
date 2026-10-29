"""startup 模块单元测试。

**不会真正改动系统自启动设置**：所有会写注册表 / LaunchAgent / autostart 文件
的调用都被 mock 掉，测试只验证接口契约与平台分发逻辑。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import startup  # noqa: E402


class StartupTestCase(unittest.TestCase):
    def test_is_supported(self):
        self.assertTrue(startup.is_supported())

    def test_status_keys(self):
        status = startup.status()
        for key in ("platform", "supported", "enabled", "detail"):
            self.assertIn(key, status)

    def test_enable_disable_contract(self):
        """启用/停用必须返回 (bool, str) 且不抛异常（不触碰真实系统设置）。"""
        with mock.patch.object(startup, "_macos_enable", return_value=(True, "ok")), \
             mock.patch.object(startup, "_macos_disable", return_value=(True, "ok")), \
             mock.patch.object(startup, "_windows_enable", return_value=(True, "ok")), \
             mock.patch.object(startup, "_windows_disable", return_value=(True, "ok")), \
             mock.patch.object(startup, "_linux_enable", return_value=(True, "ok")), \
             mock.patch.object(startup, "_linux_disable", return_value=(True, "ok")):
            ok, message = startup.enable()
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(message, str)
            ok2, message2 = startup.disable()
            self.assertIsInstance(ok2, bool)
            self.assertIsInstance(message2, str)

    def test_enable_returns_failure_when_platform_rejects(self):
        """平台实现返回失败时，enable() 应如实透传，不能吞掉错误。"""
        with mock.patch.object(startup, "_linux_enable", return_value=(False, "权限不足")), \
             mock.patch.object(startup, "_macos_enable", return_value=(False, "权限不足")), \
             mock.patch.object(startup, "_windows_enable", return_value=(False, "权限不足")):
            ok, message = startup.enable()
            self.assertFalse(ok)
            self.assertIn("权限不足", message)

    def test_is_enabled_returns_bool(self):
        self.assertIsInstance(startup.is_enabled(), bool)

    def test_build_launch_command_frozen(self):
        """打包后的可执行文件应直接指向自身。"""
        with mock.patch.object(startup, "_is_frozen", return_value=True):
            program, args = startup._build_launch_command()
        self.assertEqual(program, sys.executable)
        self.assertEqual(args, [])

    def test_build_launch_command_explicit_path(self):
        program, args = startup._build_launch_command("/opt/netwatch/run")
        self.assertEqual(program, "/opt/netwatch/run")
        self.assertEqual(args, [])


if __name__ == "__main__":
    unittest.main()
