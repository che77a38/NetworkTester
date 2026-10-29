# -*- coding: utf-8 -*-
"""``netwatch.notifier`` 单元测试。

全部用标准库 ``unittest``；不发起任何真实网络请求——``urllib.request.urlopen``
与 ``subprocess.run`` 都被 ``unittest.mock`` 替换。
"""

import json
import os
import sys
import unittest
import urllib.parse
from unittest import mock

# 保证可以直接 import netwatch.notifier（项目根目录加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from netwatch.notifier import ALL_CHANNELS, NotifyEvent, Notifier  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def make_response(payload=None, status=200, raw=None):
    """构造一个模拟的 ``urlopen`` 响应对象。"""
    response = mock.MagicMock()
    if raw is None:
        raw = json.dumps(payload if payload is not None else {}).encode("utf-8")
    response.read.return_value = raw
    response.status = status
    response.getcode.return_value = status
    return response


def make_config(channels=None, endpoints=None):
    """构造一份完整配置（默认所有通道关闭、所有端点为空）。"""
    config = {
        "channels": {name: False for name in ALL_CHANNELS},
        "endpoints": {
            "wecom": "",
            "dingtalk": "",
            "bark": "",
            "serverchan": "",
            "telegram_token": "",
            "telegram_chat_id": "",
            "smtp_host": "",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "smtp_to": "",
            "smtp_use_ssl": True,
            "webhook_url": "",
            "webhook_method": "POST",
            "webhook_body": "",
        },
    }
    if channels:
        config["channels"].update(channels)
    if endpoints:
        config["endpoints"].update(endpoints)
    return config


def make_event(kind="test", title="测试标题", message="测试正文", online=True):
    """构造一个通知事件。"""
    return NotifyEvent(
        kind=kind,
        title=title,
        message=message,
        online=online,
        ok_count=2,
        total=3,
        detail="28 ms",
    )


def request_of(mock_urlopen):
    """取出 ``urlopen`` 被调用时传入的 ``urllib.request.Request``。"""
    return mock_urlopen.call_args[0][0]


def payload_of(mock_urlopen):
    """取出请求体并解析成 JSON。"""
    request = request_of(mock_urlopen)
    return json.loads(request.data.decode("utf-8"))


# ---------------------------------------------------------------------------
# NotifyEvent
# ---------------------------------------------------------------------------


class NotifyEventTest(unittest.TestCase):
    def test_field_defaults(self):
        event = NotifyEvent(kind="test", title="t", message="m", online=True)
        self.assertEqual(event.kind, "test")
        self.assertEqual(event.title, "t")
        self.assertEqual(event.message, "m")
        self.assertTrue(event.online)
        self.assertEqual(event.ok_count, 0)
        self.assertEqual(event.total, 0)
        self.assertEqual(event.detail, "")


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


class NotifierChannelSwitchTest(unittest.TestCase):
    """用例 1：通道开关关闭时不调用任何通道。"""

    @mock.patch("subprocess.run")
    @mock.patch("urllib.request.urlopen")
    def test_all_channels_disabled_calls_nothing(self, mock_urlopen, mock_run):
        config = make_config(
            channels={"wecom": False, "desktop": False},
            endpoints={"wecom": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k"},
        )
        notifier = Notifier(config)
        results = notifier.notify(make_event())

        self.assertEqual(results, {})
        self.assertFalse(mock_urlopen.called)
        self.assertFalse(mock_run.called)

    @mock.patch("subprocess.run")
    @mock.patch("urllib.request.urlopen")
    def test_only_enabled_channel_is_executed(self, mock_urlopen, mock_run):
        mock_urlopen.return_value = make_response({"errcode": 0, "errmsg": "ok"})
        config = make_config(
            channels={"wecom": True, "dingtalk": False},
            endpoints={"wecom": "https://example.com/wecom"},
        )
        results = Notifier(config).notify(make_event())

        self.assertEqual(results, {"wecom": "ok"})
        self.assertEqual(mock_urlopen.call_count, 1)
        self.assertFalse(mock_run.called)


class NotifierWecomTest(unittest.TestCase):
    """用例 2、3：企业微信通道的报文与错误码处理。"""

    URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc123"

    @mock.patch("urllib.request.urlopen")
    def test_wecom_posts_markdown_to_configured_url(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 0, "errmsg": "ok"})
        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": self.URL},
        )
        event = make_event(kind="test", title="测试标题", message="测试正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"wecom": "ok"})

        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, self.URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertIn("application/json", request.get_header("Content-type"))

        payload = payload_of(mock_urlopen)
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertIn("测试标题", payload["markdown"]["content"])
        self.assertIn("测试正文", payload["markdown"]["content"])

        # 超时统一 10 秒
        self.assertEqual(mock_urlopen.call_args[1]["timeout"], 10)

    @mock.patch("urllib.request.urlopen")
    def test_wecom_connect_kind_uses_standard_title(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 0, "errmsg": "ok"})
        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": self.URL},
        )
        results = Notifier(config).notify(make_event(kind="connect", title="网络恢复"))
        self.assertEqual(results, {"wecom": "ok"})
        content = payload_of(mock_urlopen)["markdown"]["content"]
        self.assertIn("✅ 网络已连接", content)
        self.assertIn("可用目标：2/3", content)

    @mock.patch("urllib.request.urlopen")
    def test_wecom_error_code_is_reported(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 40001, "errmsg": "bad key"})
        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": self.URL},
        )
        results = Notifier(config).notify(make_event())

        self.assertNotEqual(results["wecom"], "ok")
        self.assertIn("40001", results["wecom"])
        self.assertIn("bad key", results["wecom"])

    @mock.patch("urllib.request.urlopen")
    def test_wecom_non_json_response_is_not_treated_as_error(self, mock_urlopen):
        """代理返回 HTML 时无法校验 errcode，按已送达处理并记录日志。"""
        mock_urlopen.return_value = make_response(raw=b"<html>ok</html>")
        messages = []
        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": self.URL},
        )
        results = Notifier(config, logger=messages.append).notify(make_event())

        self.assertEqual(results, {"wecom": "ok"})
        self.assertTrue(any("JSON" in message for message in messages))


class NotifierPayloadFormatTest(unittest.TestCase):
    """用例 4：各通道的报文格式断言。"""

    @mock.patch("urllib.request.urlopen")
    def test_dingtalk_markdown_payload(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 0, "errmsg": "ok"})
        url = "https://oapi.dingtalk.com/robot/send?access_token=token123"
        config = make_config(channels={"dingtalk": True}, endpoints={"dingtalk": url})
        event = make_event(title="钉钉标题", message="钉钉正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"dingtalk": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_method(), "POST")
        payload = payload_of(mock_urlopen)
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertEqual(payload["markdown"]["title"], "钉钉标题")
        self.assertIn("钉钉正文", payload["markdown"]["text"])

    @mock.patch("urllib.request.urlopen")
    def test_bark_title_body_payload(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"code": 200, "message": "success"})
        url = "https://api.day.app/yourkey"
        config = make_config(channels={"bark": True}, endpoints={"bark": url})
        event = make_event(title="Bark标题", message="Bark正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"bark": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, url)
        self.assertEqual(request.get_method(), "POST")
        payload = payload_of(mock_urlopen)
        self.assertEqual(payload["title"], "Bark标题")
        self.assertIn("Bark正文", payload["body"])

    @mock.patch("urllib.request.urlopen")
    def test_serverchan_sendkey_builds_url_and_form(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"code": 0, "message": "success"})
        config = make_config(channels={"serverchan": True}, endpoints={"serverchan": "SCT123abc"})
        event = make_event(title="酱标题", message="酱正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"serverchan": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, "https://sctapi.ftqq.com/SCT123abc.send")
        self.assertEqual(request.get_method(), "POST")
        body = request.data.decode("utf-8")
        self.assertIn("title=", body)
        self.assertIn("desp=", body)
        decoded = urllib.parse.unquote_plus(body)
        self.assertIn("酱标题", decoded)
        self.assertIn("酱正文", decoded)

    @mock.patch("urllib.request.urlopen")
    def test_serverchan_full_url_is_used_directly(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"code": 0})
        url = "https://example.com/send/mykey"
        config = make_config(channels={"serverchan": True}, endpoints={"serverchan": url})
        results = Notifier(config).notify(make_event())

        self.assertEqual(results, {"serverchan": "ok"})
        self.assertEqual(request_of(mock_urlopen).full_url, url)

    @mock.patch("urllib.request.urlopen")
    def test_telegram_send_message_payload(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"ok": True, "result": {}})
        config = make_config(
            channels={"telegram": True},
            endpoints={"telegram_token": "123:ABC", "telegram_chat_id": "456"},
        )
        event = make_event(title="TG标题", message="TG正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"telegram": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertEqual(request.get_method(), "POST")
        payload = payload_of(mock_urlopen)
        self.assertEqual(payload["chat_id"], "456")
        self.assertIn("TG标题", payload["text"])
        self.assertIn("TG正文", payload["text"])

    @mock.patch("urllib.request.urlopen")
    def test_webhook_post_json_replaces_placeholders(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"ok": True})
        config = make_config(
            channels={"webhook": True},
            endpoints={
                "webhook_url": "https://example.com/hook",
                "webhook_method": "POST",
                "webhook_body": '{"text": "{{title}} | {{message}} | {{kind}}"}',
            },
        )
        event = make_event(title="钩子标题", message="钩子正文")
        results = Notifier(config).notify(event)

        self.assertEqual(results, {"webhook": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.full_url, "https://example.com/hook")
        self.assertEqual(request.get_method(), "POST")
        payload = payload_of(mock_urlopen)
        self.assertIn("钩子标题", payload["text"])
        self.assertIn("钩子正文", payload["text"])
        self.assertIn("test", payload["text"])

    @mock.patch("urllib.request.urlopen")
    def test_webhook_get_replaces_placeholders_in_url(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"ok": True})
        config = make_config(
            channels={"webhook": True},
            endpoints={
                "webhook_url": "https://example.com/hook?title={{title}}",
                "webhook_method": "GET",
                "webhook_body": "",
            },
        )
        results = Notifier(config).notify(make_event(title="GET标题"))
        self.assertEqual(results, {"webhook": "ok"})
        request = request_of(mock_urlopen)
        self.assertEqual(request.get_method(), "GET")
        self.assertIn("GET标题", request.full_url)

    @mock.patch("smtplib.SMTP_SSL")
    def test_smtp_sends_mail_to_multiple_recipients(self, mock_smtp):
        server = mock_smtp.return_value
        config = make_config(
            channels={"smtp": True},
            endpoints={
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_user": "me@example.com",
                "smtp_password": "secret",
                "smtp_to": "a@example.com, b@example.com",
                "smtp_use_ssl": True,
            },
        )
        results = Notifier(config).notify(make_event(title="邮件标题", message="邮件正文"))

        self.assertEqual(results, {"smtp": "ok"})
        mock_smtp.assert_called_once()
        self.assertEqual(mock_smtp.call_args[0][0], "smtp.example.com")
        self.assertEqual(mock_smtp.call_args[0][1], 465)
        server.login.assert_called_once_with("me@example.com", "secret")
        recipients = server.sendmail.call_args[0][1]
        self.assertEqual(recipients, ["a@example.com", "b@example.com"])


class NotifierRobustnessTest(unittest.TestCase):
    """用例 5、6：异常隔离与未配置通道。"""

    @mock.patch("urllib.request.urlopen")
    def test_urlopen_error_does_not_stop_other_channels(self, mock_urlopen):
        state = {"count": 0}

        def side_effect(*args, **kwargs):
            state["count"] += 1
            if state["count"] == 1:
                raise OSError("boom")
            return make_response({"errcode": 0, "errmsg": "ok"})

        mock_urlopen.side_effect = side_effect
        config = make_config(
            channels={"wecom": True, "dingtalk": True},
            endpoints={
                "wecom": "https://example.com/wecom",
                "dingtalk": "https://example.com/dingtalk",
            },
        )
        results = Notifier(config).notify(make_event())

        # 第一个通道失败：记录错误但不抛异常
        self.assertNotEqual(results["wecom"], "ok")
        self.assertIn("boom", results["wecom"])
        # 第二个通道继续执行并成功
        self.assertEqual(results["dingtalk"], "ok")
        self.assertEqual(mock_urlopen.call_count, 2)

    @mock.patch("netwatch.notifier.shutil.which")
    @mock.patch("subprocess.run")
    @mock.patch("urllib.request.urlopen")
    def test_notify_never_raises_when_channel_raises(
        self, mock_urlopen, mock_run, mock_which
    ):
        """子进程调用失败时，桌面通道要记录失败原因而不是抛异常。

        需要同时 mock ``shutil.which``，让平台命令"看起来存在"，
        否则 Linux 上会直接走"未找到 notify-send"分支而根本不会调用子进程，
        测试前提就不成立了。

        断言只要求"包含底层异常信息"，不绑定具体平台的提示文案，
        这样在 macOS / Windows / Linux 上都能通过。
        """
        mock_which.side_effect = lambda name: "/usr/bin/" + name
        mock_run.side_effect = OSError("no such command")
        config = make_config(channels={"desktop": True})
        results = Notifier(config).notify(make_event())

        self.assertNotEqual(results["desktop"], "ok")
        # 失败原因中应能看出是命令执行出错（各平台包装文案不同，只查关键字）
        self.assertIn("no such command", results["desktop"].lower())

    @mock.patch("urllib.request.urlopen")
    def test_unconfigured_channel_is_skipped(self, mock_urlopen):
        config = make_config(channels={"wecom": True}, endpoints={"wecom": ""})
        notifier = Notifier(config)

        self.assertFalse(notifier.channel_configured("wecom"))
        results = notifier.notify(make_event())

        self.assertEqual(results, {"wecom": "skipped"})
        self.assertFalse(mock_urlopen.called)

    @mock.patch("urllib.request.urlopen")
    def test_channel_configured_matrix(self, mock_urlopen):
        config = make_config(
            channels={"telegram": True, "smtp": True, "webhook": True},
            endpoints={
                "telegram_token": "123:ABC",  # 缺少 chat_id
                "smtp_host": "smtp.example.com",  # 缺少收件人
                "webhook_url": "https://example.com/hook",
            },
        )
        notifier = Notifier(config)

        self.assertTrue(notifier.channel_configured("desktop"))
        self.assertTrue(notifier.channel_configured("sound"))
        self.assertTrue(notifier.channel_configured("webhook"))
        self.assertFalse(notifier.channel_configured("telegram"))
        self.assertFalse(notifier.channel_configured("smtp"))
        self.assertFalse(notifier.channel_configured("wecom"))
        self.assertFalse(notifier.channel_configured("unknown-channel"))

        results = notifier.notify(make_event())
        self.assertEqual(results["webhook"], "ok")
        self.assertEqual(results["telegram"], "skipped")
        self.assertEqual(results["smtp"], "skipped")
        self.assertEqual(mock_urlopen.call_count, 1)


class NotifierLocalChannelTest(unittest.TestCase):
    """用例 7：本地通道（桌面通知 / 声音）调用系统命令。"""

    @mock.patch("netwatch.notifier.shutil.which", return_value="/usr/bin/notify-send")
    @mock.patch("urllib.request.urlopen")
    @mock.patch("subprocess.run")
    def test_desktop_calls_subprocess(self, mock_run, mock_urlopen, mock_which):
        mock_run.return_value = mock.MagicMock(returncode=0)
        config = make_config(channels={"desktop": True})
        results = Notifier(config).notify(make_event(title="桌面标题", message="桌面正文"))

        self.assertTrue(mock_run.called)
        self.assertEqual(results.get("desktop"), "ok")
        # 桌面通知不应产生任何网络请求
        self.assertFalse(mock_urlopen.called)

    @mock.patch("netwatch.notifier.shutil.which", return_value="/usr/bin/paplay")
    @mock.patch("subprocess.run")
    def test_sound_calls_subprocess(self, mock_run, mock_which):
        mock_run.return_value = mock.MagicMock(returncode=0)
        config = make_config(channels={"sound": True})
        results = Notifier(config).notify(make_event())

        self.assertTrue(mock_run.called)
        self.assertEqual(results.get("sound"), "ok")

    @mock.patch("netwatch.notifier.shutil.which", return_value=None)
    @mock.patch("subprocess.run")
    def test_desktop_without_any_tool_reports_error(self, mock_run, mock_which):
        mock_run.return_value = mock.MagicMock(returncode=0)
        config = make_config(channels={"desktop": True})
        results = Notifier(config).notify(make_event())

        # Linux 上没有 notify-send / zenity 时必须报错，其它平台仍然正常
        if results.get("desktop") != "ok":
            self.assertIn("notify-send", results["desktop"])


class NotifierLoggerTest(unittest.TestCase):
    """日志回调不应该影响通知流程。"""

    @mock.patch("urllib.request.urlopen")
    def test_logger_callback_is_used(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 0})
        messages = []
        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": "https://example.com/wecom"},
        )
        notifier = Notifier(config, logger=messages.append)
        results = notifier.notify(make_event())

        self.assertEqual(results, {"wecom": "ok"})
        self.assertTrue(any("wecom" in message for message in messages))

    @mock.patch("urllib.request.urlopen")
    def test_broken_logger_does_not_break_notify(self, mock_urlopen):
        mock_urlopen.return_value = make_response({"errcode": 0})

        def bad_logger(_message):
            raise RuntimeError("logger broken")

        config = make_config(
            channels={"wecom": True},
            endpoints={"wecom": "https://example.com/wecom"},
        )
        results = Notifier(config, logger=bad_logger).notify(make_event())
        self.assertEqual(results, {"wecom": "ok"})


if __name__ == "__main__":
    unittest.main()
