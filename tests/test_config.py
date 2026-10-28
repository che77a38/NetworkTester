"""config 模块单元测试。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import config as config_mod  # noqa: E402


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_config_shape(self):
        cfg = config_mod.load_config(self.path)
        self.assertIn("targets", cfg)
        self.assertIn("channels", cfg)
        self.assertIn("endpoints", cfg)
        self.assertEqual(cfg["target_mode"], "any")
        self.assertTrue(cfg["channels"]["desktop"])
        self.assertFalse(cfg["channels"]["wecom"])

    def test_missing_file_returns_defaults(self):
        cfg = config_mod.load_config(self.path)
        self.assertEqual(cfg["timeout"], config_mod.DEFAULT_CONFIG["timeout"])

    def test_save_and_load_roundtrip(self):
        cfg = config_mod.load_config(self.path)
        cfg["targets"] = ["example.com", "1.1.1.1:443"]
        cfg["endpoints"]["wecom"] = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc"
        self.assertTrue(config_mod.save_config(cfg, self.path))
        loaded = config_mod.load_config(self.path)
        self.assertEqual(loaded["targets"], ["example.com", "1.1.1.1:443"])
        self.assertTrue(loaded["endpoints"]["wecom"].endswith("key=abc"))

    def test_partial_config_is_merged(self):
        self.path.write_text(json.dumps({"timeout": 9}), encoding="utf-8")
        cfg = config_mod.load_config(self.path)
        self.assertEqual(cfg["timeout"], 9)
        self.assertIn("endpoints", cfg)
        self.assertEqual(cfg["interval"], config_mod.DEFAULT_CONFIG["interval"])

    def test_broken_json_falls_back(self):
        self.path.write_text("{not json", encoding="utf-8")
        cfg = config_mod.load_config(self.path)
        self.assertEqual(cfg["timeout"], config_mod.DEFAULT_CONFIG["timeout"])

    def test_clamp_config(self):
        cfg = config_mod.load_config(self.path)
        cfg["timeout"] = 999
        cfg["interval"] = 1
        cfg["target_mode"] = "weird"
        cfg["targets"] = [" a.com ", "", "  ", "b.com"]
        clamped = config_mod.clamp_config(cfg)
        self.assertEqual(clamped["timeout"], 30)
        self.assertEqual(clamped["interval"], 5)
        self.assertEqual(clamped["target_mode"], "any")
        self.assertEqual(clamped["targets"], ["a.com", "b.com"])

    def test_clamp_accepts_string_targets(self):
        cfg = config_mod.load_config(self.path)
        cfg["targets"] = "x.com\ny.com"
        clamped = config_mod.clamp_config(cfg)
        self.assertEqual(clamped["targets"], ["x.com", "y.com"])

    def test_config_paths(self):
        directory = config_mod.config_dir()
        self.assertTrue(str(directory).endswith("netwatch"))
        self.assertTrue(str(config_mod.config_path()).endswith("config.json"))
        self.assertTrue(str(config_mod.log_path()).endswith("netwatch.log"))


if __name__ == "__main__":
    unittest.main()
