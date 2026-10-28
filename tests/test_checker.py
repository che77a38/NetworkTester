"""checker 模块单元测试。"""

import socket
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import checker  # noqa: E402


class ParseTargetTestCase(unittest.TestCase):
    def test_plain_host(self):
        self.assertEqual(checker.parse_target("example.com"), ("example.com", 443))

    def test_host_with_port(self):
        self.assertEqual(checker.parse_target("example.com:8443"), ("example.com", 8443))

    def test_https_scheme(self):
        self.assertEqual(checker.parse_target("https://example.com"), ("example.com", 443))

    def test_http_scheme(self):
        self.assertEqual(checker.parse_target("http://example.com"), ("example.com", 80))

    def test_scheme_with_path(self):
        self.assertEqual(checker.parse_target("https://example.com/health"), ("example.com", 443))

    def test_ip_address(self):
        self.assertEqual(checker.parse_target("1.1.1.1"), ("1.1.1.1", 443))

    def test_userinfo_stripped(self):
        self.assertEqual(checker.parse_target("http://user:pw@example.com/x"), ("example.com", 80))

    def test_empty(self):
        self.assertEqual(checker.parse_target(""), ("", 443))


class CheckConnectivityTestCase(unittest.TestCase):
    def test_empty_targets_is_offline(self):
        result = checker.check_connectivity([], 1)
        self.assertFalse(result.online)
        self.assertEqual(result.total, 0)

    def test_mode_any(self):
        results = [
            checker.TargetResult("a", False, -1, "超时"),
            checker.TargetResult("b", True, 12),
        ]

        def fake_check(raw, timeout):
            for item in results:
                if item.target == raw:
                    return item
            return checker.TargetResult(raw, False, -1, "未知")

        original = checker.check_target
        checker.check_target = fake_check
        try:
            any_result = checker.check_connectivity(["a", "b"], 1, "any")
            all_result = checker.check_connectivity(["a", "b"], 1, "all")
        finally:
            checker.check_target = original
        self.assertTrue(any_result.online)
        self.assertFalse(all_result.online)
        self.assertEqual(any_result.ok_count, 1)
        self.assertEqual(any_result.total, 2)

    def test_all_mode_success(self):
        original = checker.check_target
        checker.check_target = lambda raw, timeout: checker.TargetResult(raw, True, 5)
        try:
            result = checker.check_connectivity(["a", "b"], 1, "all")
        finally:
            checker.check_target = original
        self.assertTrue(result.online)
        self.assertEqual(result.avg_latency_ms, 5)

    def test_summary_and_latency(self):
        result = checker.CheckResult(
            online=True,
            results=[
                checker.TargetResult("a", True, 10),
                checker.TargetResult("b", True, 30),
                checker.TargetResult("c", False, -1, "超时"),
            ],
        )
        self.assertEqual(result.ok_count, 2)
        self.assertEqual(result.total, 3)
        self.assertEqual(result.avg_latency_ms, 20)
        self.assertIn("2/3", result.summary())

    def test_probe_failure_path(self):
        """未监听的本地端口必须探测失败（不依赖外网 DNS 环境）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
        sock.close()
        ok, latency, error = checker.probe(host, port, 2)
        self.assertFalse(ok)
        self.assertEqual(latency, -1)
        self.assertTrue(error)

    def test_probe_empty_host(self):
        ok, latency, error = checker.probe("", 443, 1)
        self.assertFalse(ok)
        self.assertTrue(error)


class ProbeTestCase(unittest.TestCase):
    def test_probe_local_listener(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()
        try:
            ok, latency, error = checker.probe(host, port, 3)
        finally:
            server.close()
        self.assertTrue(ok, error)
        self.assertGreaterEqual(latency, 0)

    def test_probe_closed_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        host, port = sock.getsockname()
        sock.close()
        ok, latency, error = checker.probe(host, port, 2)
        self.assertFalse(ok)
        self.assertTrue(error)


if __name__ == "__main__":
    unittest.main()
