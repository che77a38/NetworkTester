# -*- coding: utf-8 -*-
"""NetWatch 多通道通知模块。

支持的通道（对应配置 ``config["channels"]`` 的 key）：

==============  ===================  ==================================================
key             通道                 实现要点
==============  ===================  ==================================================
``desktop``     桌面通知             macOS ``osascript`` / Windows PowerShell toast /
                                   Linux ``notify-send``（不可用回退 ``zenity``）
``sound``       系统声音             macOS ``afplay``（不可用回退 ``beep``） /
                                   Windows PowerShell ``[console]::beep`` /
                                   Linux ``paplay``（不可用回退终端响铃 ``\\a``）
``wecom``       企业微信机器人        POST ``{"msgtype":"markdown","markdown":{"content":...}}``
                                   到用户填写的完整 webhook 地址，``errcode != 0`` 视为失败
``dingtalk``    钉钉机器人           POST ``{"msgtype":"markdown","markdown":{"title":..,"text":..}}``
                                   到用户填写的完整 webhook 地址（含 access_token）
``bark``        Bark 推送           POST ``{"title":..,"body":..}``
``serverchan``  Server酱            纯 SendKey 拼 ``https://sctapi.ftqq.com/{key}.send`` 后
                                   POST 表单 ``title``/``desp``；填完整 URL 则直接 POST
``telegram``    Telegram Bot        POST ``https://api.telegram.org/bot{token}/sendMessage``
                                   ，body ``{"chat_id":..,"text":..}``
``smtp``        邮件                ``smtplib`` + SSL/STARTTLS，HTML + 纯文本混合邮件，
                                   收件人 ``smtp_to`` 逗号分隔
``webhook``     自定义 Webhook      GET/POST；body 可为 JSON（能解析成 JSON 就按 JSON 发，
                                   否则按表单发），支持 ``{{title}}`` ``{{message}}``
                                   ``{{kind}}`` ``{{timestamp}}`` 占位符
==============  ===================  ==================================================

约定（重要）：

1. 本模块**只依赖 Python 标准库**，且**不 import netwatch 包内任何其它模块**
   （自包含，便于单元测试独立运行）；面向用户的文案直接写成中文常量。
2. :meth:`Notifier.notify` 逐通道执行，**任何异常都不会向外抛出**。返回
   ``{通道名: "ok" | "错误原因" | "skipped"}``：

   * ``"ok"``     —— 该通道发送成功；
   * ``"skipped"``—— 通道已启用但缺少必要配置（如企业微信 webhook 地址为空），
     此时**不会产生任何网络请求**，也不算错误；
   * 其它字符串   —— 错误原因（异常类型 + 描述，或服务端返回的错误码/消息）。

   通道开关为关闭的通道**不会出现在返回值里**。
3. 所有网络请求超时统一为 :data:`HTTP_TIMEOUT`（10 秒）。
4. 服务端返回体不是合法 JSON（例如反向代理返回 HTML）时无法校验错误码，
   按"请求已送达"处理，只写一条日志，不判定为失败。
"""

import json
import os
import platform
import shutil
import smtplib
import subprocess
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as html_escape
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 所有网络请求的统一超时时间（秒）
HTTP_TIMEOUT = 10

#: 调用系统命令的统一超时时间（秒）
CMD_TIMEOUT = 10

#: 播放提示音命令的超时时间（秒）——提示音不宜长时间阻塞调用方
SOUND_CMD_TIMEOUT = 5

# 通道名常量
CHANNEL_DESKTOP = "desktop"
CHANNEL_SOUND = "sound"
CHANNEL_WECOM = "wecom"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_BARK = "bark"
CHANNEL_SERVERCHAN = "serverchan"
CHANNEL_TELEGRAM = "telegram"
CHANNEL_SMTP = "smtp"
CHANNEL_WEBHOOK = "webhook"

#: 全部通道（notify 的执行顺序）
ALL_CHANNELS: Tuple[str, ...] = (
    CHANNEL_DESKTOP,
    CHANNEL_SOUND,
    CHANNEL_WECOM,
    CHANNEL_DINGTALK,
    CHANNEL_BARK,
    CHANNEL_SERVERCHAN,
    CHANNEL_TELEGRAM,
    CHANNEL_SMTP,
    CHANNEL_WEBHOOK,
)

# 通知文案常量（中文）
TEXT_CONNECT_TITLE = "✅ 网络已连接"
TEXT_DISCONNECT_TITLE = "❌ 网络已断开"
TEXT_OK = "ok"
TEXT_SKIPPED = "skipped"

# 通知类型
KIND_CONNECT = "connect"
KIND_DISCONNECT = "disconnect"
KIND_START = "start"
KIND_TEST = "test"

# macOS 系统提示音
MACOS_SOUND_FILE = "/System/Library/Sounds/Ping.aiff"
# Linux 系统提示音
LINUX_SOUND_FILE = "/usr/share/sounds/freedesktop/stereo/message.oga"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class NotifyEvent:
    """一次通知事件。

    :param kind: 事件类型，``"connect"`` / ``"disconnect"`` / ``"start"`` / ``"test"``
    :param title: 标题（``kind`` 为 connect/disconnect 时由通知模块统一改写）
    :param message: 正文
    :param online: 事件发生时是否处于联网状态
    :param ok_count: 成功检测的目标数
    :param total: 目标总数
    :param detail: 附加明细（可选，例如耗时/延迟）
    """

    kind: str
    title: str
    message: str
    online: bool
    ok_count: int = 0
    total: int = 0
    detail: str = ""


# ---------------------------------------------------------------------------
# 通知模块
# ---------------------------------------------------------------------------


class Notifier:
    """多通道通知发送器。

    :param config: 完整配置字典（结构见 ``docs/SPEC.md`` 的 ``DEFAULT_CONFIG``）
    :param logger: 可选的日志回调 ``Callable[[str], None]``，用于把内部过程写进日志
    """

    def __init__(self, config: Dict[str, Any], logger: Optional[Callable[[str], None]] = None) -> None:
        self.config: Dict[str, Any] = config if isinstance(config, dict) else {}
        self.logger: Optional[Callable[[str], None]] = logger

    # -- 公开 API ----------------------------------------------------------

    def channel_configured(self, channel: str) -> bool:
        """判断通道是否具备发送所需的配置。

        只看"是否填了必要的信息"，**不考虑通道开关**：

        * ``desktop`` / ``sound`` —— 本地通道，恒为 ``True``；
        * ``wecom`` / ``dingtalk`` / ``bark`` / ``serverchan`` —— 地址（SendKey）非空即可；
        * ``telegram`` —— 需要 ``telegram_token`` 与 ``telegram_chat_id`` 都非空；
        * ``smtp`` —— 需要 ``smtp_host`` 与 ``smtp_to`` 非空；
        * ``webhook`` —— ``webhook_url`` 非空。

        未配置的通道在 :meth:`notify` 中会被跳过（记为 ``"skipped"``，不产生网络请求）。
        """
        if channel in (CHANNEL_DESKTOP, CHANNEL_SOUND):
            return True
        if channel == CHANNEL_WECOM:
            return bool(self._endpoint(CHANNEL_WECOM))
        if channel == CHANNEL_DINGTALK:
            return bool(self._endpoint(CHANNEL_DINGTALK))
        if channel == CHANNEL_BARK:
            return bool(self._endpoint(CHANNEL_BARK))
        if channel == CHANNEL_SERVERCHAN:
            return bool(self._endpoint(CHANNEL_SERVERCHAN))
        if channel == CHANNEL_TELEGRAM:
            return bool(self._endpoint("telegram_token")) and bool(self._endpoint("telegram_chat_id"))
        if channel == CHANNEL_SMTP:
            return bool(self._endpoint("smtp_host")) and bool(self._endpoint("smtp_to"))
        if channel == CHANNEL_WEBHOOK:
            return bool(self._endpoint("webhook_url"))
        return False

    def notify(self, event: NotifyEvent) -> Dict[str, str]:
        """按 ``config["channels"]`` 逐个通道发送通知。

        :param event: 通知事件
        :returns: ``{通道名: "ok" | "错误原因" | "skipped"}``

        约定：

        * 开关关闭的通道不会出现在返回值中，也不会调用任何外部命令/网络请求；
        * 开关打开但缺少必要配置的通道记为 ``"skipped"``，不产生任何请求；
        * 单个通道的任何异常都被捕获，写进返回值并通过 ``logger`` 记录，
          **不会影响其它通道继续执行**，也**不会向外抛出**。
        """
        results: Dict[str, str] = {}
        for channel in ALL_CHANNELS:
            if not self._channel_enabled(channel):
                continue
            if not self.channel_configured(channel):
                results[channel] = TEXT_SKIPPED
                self._log("通知通道 %s 未配置，已跳过" % channel)
                continue
            try:
                error = self._dispatch(channel, event)
            except Exception as exc:  # 任何异常都不外抛
                error = "%s: %s" % (type(exc).__name__, exc)
            if error:
                results[channel] = error
                self._log("通知通道 %s 失败：%s" % (channel, error))
            else:
                results[channel] = TEXT_OK
                self._log("通知通道 %s 成功" % channel)
        return results

    # -- 配置访问 ----------------------------------------------------------

    def _channels(self) -> Dict[str, Any]:
        """返回 ``config["channels"]``（缺失或类型不对时返回空字典）。"""
        value = self.config.get("channels")
        return value if isinstance(value, dict) else {}

    def _endpoints(self) -> Dict[str, Any]:
        """返回 ``config["endpoints"]``（缺失或类型不对时返回空字典）。"""
        value = self.config.get("endpoints")
        return value if isinstance(value, dict) else {}

    def _endpoint(self, key: str, default: Any = "") -> Any:
        """读取 ``config["endpoints"][key]``，字符串会去掉首尾空白。"""
        value = self._endpoints().get(key, default)
        if value is None:
            return default
        if isinstance(value, str):
            return value.strip()
        return value

    def _channel_enabled(self, channel: str) -> bool:
        """通道开关是否打开（缺省视为关闭）。"""
        return bool(self._channels().get(channel, False))

    def _log(self, message: str) -> None:
        """调用日志回调；回调自身出错也不影响通知流程。"""
        if self.logger is None:
            return
        try:
            self.logger(message)
        except Exception:
            pass

    # -- 文案 --------------------------------------------------------------

    def _compose(self, event: NotifyEvent) -> Tuple[str, str]:
        """根据事件类型生成统一的 ``(标题, 正文)``。

        * ``connect``   → 标题固定为 ``✅ 网络已连接``；
        * ``disconnect``→ 标题固定为 ``❌ 网络已断开``；
        * 其它 kind    → 使用事件自带的 title / message。

        正文统一包含：事件正文、时间、可用目标数（``ok_count/total``）、
        以及明细（耗时/延迟）。
        """
        title = self._title_for(event)
        lines: List[str] = []
        if event.message:
            lines.append(str(event.message))
        lines.append("时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        if event.total:
            lines.append("可用目标：%d/%d" % (event.ok_count, event.total))
        if event.detail:
            label = "耗时/延迟" if event.kind in (KIND_CONNECT, KIND_DISCONNECT) else "详情"
            lines.append("%s：%s" % (label, event.detail))
        # 事件自带标题且与统一标题不同时保留一份，避免调用方自定义文案丢失
        if event.title and str(event.title) != title:
            lines.insert(0, str(event.title))
        return title, "\n".join(lines)

    def _title_for(self, event: NotifyEvent) -> str:
        """按事件类型决定通知标题。"""
        if event.kind == KIND_CONNECT:
            return TEXT_CONNECT_TITLE
        if event.kind == KIND_DISCONNECT:
            return TEXT_DISCONNECT_TITLE
        if event.title:
            return str(event.title)
        return TEXT_CONNECT_TITLE if event.online else TEXT_DISCONNECT_TITLE

    # -- 通道分发 ----------------------------------------------------------

    def _dispatch(self, channel: str, event: NotifyEvent) -> str:
        """执行单个通道。

        :returns: 空字符串表示成功，非空表示错误原因。
        """
        title, message = self._compose(event)
        if channel == CHANNEL_DESKTOP:
            return self._send_desktop(title, message)
        if channel == CHANNEL_SOUND:
            return self._send_sound(title, message)
        if channel == CHANNEL_WECOM:
            return self._send_wecom(title, message)
        if channel == CHANNEL_DINGTALK:
            return self._send_dingtalk(title, message)
        if channel == CHANNEL_BARK:
            return self._send_bark(title, message)
        if channel == CHANNEL_SERVERCHAN:
            return self._send_serverchan(title, message)
        if channel == CHANNEL_TELEGRAM:
            return self._send_telegram(title, message)
        if channel == CHANNEL_SMTP:
            return self._send_smtp(title, message)
        if channel == CHANNEL_WEBHOOK:
            return self._send_webhook(event, title, message)
        return "未知通道：%s" % channel

    # -- 桌面通知 ----------------------------------------------------------

    def _send_desktop(self, title: str, message: str) -> str:
        """桌面弹窗通知（按平台分派）。"""
        system = platform.system()
        if system == "Darwin":
            return self._desktop_macos(title, message)
        if system == "Windows":
            return self._desktop_windows(title, message)
        return self._desktop_linux(title, message)

    def _desktop_macos(self, title: str, message: str) -> str:
        """macOS：用 ``osascript`` 显示系统通知。"""
        script = 'display notification "%s" with title "%s"' % (
            _escape_applescript(message),
            _escape_applescript(title),
        )
        proc, error = _run_cmd(["osascript", "-e", script])
        if error:
            return "osascript 执行失败：%s" % error
        if _proc_failed(proc):
            return "osascript 执行失败：%s" % _proc_stderr(proc)
        return ""

    def _desktop_windows(self, title: str, message: str) -> str:
        """Windows：PowerShell toast，``New-BurntToastNotification`` 不可用时回退 NotifyIcon。"""
        script = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                "$title = %s" % _ps_quote(title),
                "$text = %s" % _ps_quote(message),
                "if (Get-Module -ListAvailable -Name BurntToast) {",
                "  New-BurntToastNotification -Text $title, $text",
                "} else {",
                "  Add-Type -AssemblyName System.Windows.Forms",
                "  Add-Type -AssemblyName System.Drawing",
                "  $icon = New-Object System.Windows.Forms.NotifyIcon",
                "  $icon.Icon = [System.Drawing.SystemIcons]::Information",
                "  $icon.Visible = $true",
                "  $icon.BalloonTipTitle = $title",
                "  $icon.BalloonTipText = $text",
                "  $icon.ShowBalloonTip(5000)",
                "  Start-Sleep -Milliseconds 5000",
                "  $icon.Dispose()",
                "}",
            ]
        )
        proc, error = _run_cmd(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        )
        if error:
            return "PowerShell 桌面通知失败：%s" % error
        if _proc_failed(proc):
            return "PowerShell 桌面通知失败：%s" % _proc_stderr(proc)
        return ""

    def _desktop_linux(self, title: str, message: str) -> str:
        """Linux：``notify-send``，不可用时回退 ``zenity --notification``，都没有则失败。"""
        notify_send = shutil.which("notify-send")
        if notify_send:
            proc, error = _run_cmd([notify_send, "-a", "NetWatch", title, message])
            if not error and not _proc_failed(proc):
                return ""
        zenity = shutil.which("zenity")
        if zenity:
            proc, error = _run_cmd(
                [zenity, "--notification", "--text", message, "--window-title", title]
            )
            if not error and not _proc_failed(proc):
                return ""
        return "未找到 notify-send 或 zenity，无法显示桌面通知"

    # -- 系统声音 ----------------------------------------------------------

    def _send_sound(self, title: str, message: str) -> str:
        """播放提示音（按平台分派）。"""
        system = platform.system()
        if system == "Darwin":
            return self._sound_macos()
        if system == "Windows":
            return self._sound_windows()
        return self._sound_linux()

    def _sound_macos(self) -> str:
        """macOS：``afplay`` 播放 Ping.aiff，文件不存在时回退 ``beep``。"""
        if os.path.exists(MACOS_SOUND_FILE):
            proc, error = _run_cmd(["afplay", MACOS_SOUND_FILE], SOUND_CMD_TIMEOUT)
            if not error and not _proc_failed(proc):
                return ""
        if shutil.which("beep"):
            proc, error = _run_cmd(["beep"], SOUND_CMD_TIMEOUT)
            if not error and not _proc_failed(proc):
                return ""
        return "未找到 afplay 或 beep，无法播放提示音"

    def _sound_windows(self) -> str:
        """Windows：PowerShell 控制台蜂鸣。"""
        proc, error = _run_cmd(
            ["powershell", "-NoProfile", "-Command", "[console]::beep(880,300)"],
            SOUND_CMD_TIMEOUT,
        )
        if error:
            return "PowerShell 播放提示音失败：%s" % error
        if _proc_failed(proc):
            return "PowerShell 播放提示音失败：%s" % _proc_stderr(proc)
        return ""

    def _sound_linux(self) -> str:
        """Linux：``paplay`` 播放系统提示音，不可用时终端响铃。"""
        if shutil.which("paplay"):
            proc, error = _run_cmd(["paplay", LINUX_SOUND_FILE], SOUND_CMD_TIMEOUT)
            if not error and not _proc_failed(proc):
                return ""
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass
        return ""

    # -- 企业微信 ----------------------------------------------------------

    def _send_wecom(self, title: str, message: str) -> str:
        """企业微信机器人：POST markdown 消息到用户填写的完整 webhook 地址。"""
        url = str(self._endpoint(CHANNEL_WECOM))
        content = "%s\n%s" % (title, message)
        payload = {"msgtype": "markdown", "markdown": {"content": content}}
        body = self._http_post_json(url, payload)
        return self._check_errcode(body, "企业微信")

    # -- 钉钉 --------------------------------------------------------------

    def _send_dingtalk(self, title: str, message: str) -> str:
        """钉钉机器人：POST markdown 消息到用户填写的完整 webhook 地址。"""
        url = str(self._endpoint(CHANNEL_DINGTALK))
        payload = {"msgtype": "markdown", "markdown": {"title": title, "text": message}}
        body = self._http_post_json(url, payload)
        return self._check_errcode(body, "钉钉")

    # -- Bark --------------------------------------------------------------

    def _send_bark(self, title: str, message: str) -> str:
        """Bark 推送：POST ``{"title":..,"body":..}``。"""
        url = str(self._endpoint(CHANNEL_BARK))
        payload = {"title": title, "body": message}
        body = self._http_post_json(url, payload)
        data = self._parse_body(body, "Bark")
        if data:
            code = _as_int(data.get("code"))
            # 官方返回 code=200，部分自建/兼容服务返回 code=0，均视为成功
            if code is not None and code not in (0, 200):
                return "Bark 返回错误 code=%s message=%s" % (
                    code,
                    data.get("message") or data.get("msg") or "",
                )
        return ""

    # -- Server酱 ----------------------------------------------------------

    def _send_serverchan(self, title: str, message: str) -> str:
        """Server酱：SendKey 拼 URL 后 POST 表单；填了完整 URL 则直接 POST。"""
        raw = str(self._endpoint(CHANNEL_SERVERCHAN))
        if raw.lower().startswith("http://") or raw.lower().startswith("https://"):
            url = raw
        else:
            url = "https://sctapi.ftqq.com/%s.send" % raw
        form = urllib.parse.urlencode({"title": title, "desp": message}).encode("utf-8")
        body = self._http_post_form(url, form)
        data = self._parse_body(body, "Server酱")
        if data:
            code = _as_int(data.get("code"))
            if code is not None and code not in (0, 200):
                return "Server酱 返回错误 code=%s message=%s" % (
                    code,
                    data.get("message") or data.get("msg") or "",
                )
        return ""

    # -- Telegram ----------------------------------------------------------

    def _send_telegram(self, title: str, message: str) -> str:
        """Telegram Bot：调用 ``sendMessage`` 接口。"""
        token = str(self._endpoint("telegram_token"))
        chat_id = str(self._endpoint("telegram_chat_id"))
        if not token.lower().startswith("bot"):
            token = "bot%s" % token
        url = "https://api.telegram.org/%s/sendMessage" % token
        payload = {"chat_id": chat_id, "text": "%s\n%s" % (title, message)}
        body = self._http_post_json(url, payload)
        data = self._parse_body(body, "Telegram")
        if data and data.get("ok") in (False, 0):
            return "Telegram 返回错误：%s" % (
                data.get("description") or data.get("error_code") or "未知错误"
            )
        return ""

    # -- SMTP 邮件 ---------------------------------------------------------

    def _send_smtp(self, title: str, message: str) -> str:
        """SMTP 邮件：发送 HTML + 纯文本混合邮件，收件人逗号分隔。"""
        host = str(self._endpoint("smtp_host"))
        port = _as_int(self._endpoint("smtp_port", 465)) or 465
        user = str(self._endpoint("smtp_user"))
        password = str(self._endpoint("smtp_password"))
        use_ssl = bool(self._endpoint("smtp_use_ssl", True))
        recipients = [
            item.strip()
            for item in str(self._endpoint("smtp_to")).replace(";", ",").split(",")
            if item.strip()
        ]
        if not recipients:
            return "SMTP 收件人为空"

        mail = MIMEMultipart("alternative")
        mail["Subject"] = title
        mail["From"] = user or "netwatch@localhost"
        mail["To"] = ", ".join(recipients)
        html_body = "<html><body>%s</body></html>" % "<br/>".join(
            html_escape(line) for line in message.splitlines()
        )
        mail.attach(MIMEText(message, "plain", "utf-8"))
        mail.attach(MIMEText(html_body, "html", "utf-8"))

        sender = mail["From"]
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=HTTP_TIMEOUT)
        else:
            server = smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT)
            server.starttls()
        try:
            if user:
                server.login(user, password)
            server.sendmail(sender, recipients, mail.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        return ""

    # -- 自定义 Webhook ----------------------------------------------------

    def _send_webhook(self, event: NotifyEvent, title: str, message: str) -> str:
        """通用 Webhook：GET 直接把占位符替换进 URL；POST 按 JSON / 表单 / 纯文本发送。"""
        url = str(self._endpoint("webhook_url"))
        method = str(self._endpoint("webhook_method", "POST") or "POST").strip().upper()
        template = str(self._endpoint("webhook_body", "") or "")
        subs = {
            "title": title,
            "message": message,
            "kind": str(event.kind),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if method == "GET":
            target = _replace_placeholders(url, subs)
            request = urllib.request.Request(target, method="GET")
            self._http_send(request)
            return ""
        data, content_type = _build_webhook_body(template, subs)
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": content_type},
            method="POST",
        )
        self._http_send(request)
        return ""

    # -- HTTP 工具 ---------------------------------------------------------

    def _http_post_json(self, url: str, payload: Dict[str, Any]) -> str:
        """POST 一个 JSON 报文，返回响应正文（失败时抛异常，由上层捕获）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        return self._http_send(request)

    def _http_post_form(self, url: str, form: bytes) -> str:
        """POST 一个表单报文，返回响应正文。"""
        request = urllib.request.Request(
            url,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            method="POST",
        )
        return self._http_send(request)

    def _http_send(self, request: urllib.request.Request) -> str:
        """执行请求并读取响应正文。

        * 统一 10 秒超时；
        * 不使用 ``with`` 语法，方便单元测试用简单的 mock 替换 ``urlopen``；
        * HTTP 状态码 >= 400 视为失败。
        """
        response = urllib.request.urlopen(request, timeout=HTTP_TIMEOUT)
        try:
            status = _http_status(response)
            raw = response.read()
        finally:
            _close_quietly(response)
        if status is not None and status >= 400:
            raise RuntimeError("HTTP %d" % status)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        if isinstance(raw, str):
            return raw
        return ""

    def _check_errcode(self, body: str, channel_name: str) -> str:
        """校验返回体中的 ``errcode``；``errcode != 0`` 视为失败。

        返回体不是合法 JSON 时无法校验返回码，按成功处理（仅记录日志）。
        """
        data = self._parse_body(body, channel_name)
        if not data:
            return ""
        errcode = _as_int(data.get("errcode"))
        if errcode is not None and errcode != 0:
            return "%s 返回错误 errcode=%s errmsg=%s" % (
                channel_name,
                errcode,
                data.get("errmsg") or data.get("message") or "",
            )
        return ""

    def _parse_body(self, body: str, channel_name: str) -> Optional[Dict[str, Any]]:
        """解析响应正文。

        响应不是合法 JSON（例如某些代理返回 HTML）时无法校验返回码，
        此时按"请求已送达"处理，只记录一条日志，不算失败。
        """
        data = _load_json(body)
        if data is None and body and body.strip():
            self._log(
                "%s 响应不是 JSON，无法校验返回码：%s" % (channel_name, body[:200])
            )
        return data


# ---------------------------------------------------------------------------
# 模块级辅助函数
# ---------------------------------------------------------------------------


def _run_cmd(cmd: List[str], timeout: int = CMD_TIMEOUT) -> Tuple[Optional[Any], str]:
    """执行系统命令。

    :param cmd: 命令参数列表
    :param timeout: 超时时间（秒）
    :returns: ``(子进程对象, 错误信息)``。命令无法执行时子进程为 ``None``，
        错误信息为异常描述（例如 ``FileNotFoundError: osascript``）。

    这里使用 ``subprocess.run`` 的模块属性调用方式，单元测试可以直接
    ``unittest.mock.patch("subprocess.run")``。
    """
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        return proc, ""
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def _proc_failed(proc: Any) -> bool:
    """判断命令是否执行失败。

    只有当 ``returncode`` 是明确的非零整数时才判定为失败；这样单元测试里
    未配置返回值的 mock 不会被误判成失败。
    """
    if proc is None:
        return True
    code = getattr(proc, "returncode", None)
    if isinstance(code, int) and code != 0:
        return True
    return False


def _proc_stderr(proc: Any) -> str:
    """提取命令的 stderr 文本（尽力而为）。"""
    if proc is None:
        return "命令无法执行"
    data = getattr(proc, "stderr", None)
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace").strip()
    if isinstance(data, str):
        return data.strip()
    return ""


def _http_status(response: Any) -> Optional[int]:
    """读取 HTTP 状态码；无法确定时返回 ``None``。"""
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    getcode = getattr(response, "getcode", None)
    if callable(getcode):
        try:
            value = getcode()
        except Exception:
            return None
        if isinstance(value, int):
            return value
    return None


def _close_quietly(response: Any) -> None:
    """关闭响应对象，忽略任何异常。"""
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _load_json(body: str) -> Optional[Dict[str, Any]]:
    """把响应正文解析成字典；不是合法 JSON 对象时返回 ``None``。"""
    if not body or not body.strip():
        return None
    try:
        data = json.loads(body)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _as_int(value: Any) -> Optional[int]:
    """宽松地把值转换成整数；转换不了返回 ``None``。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _escape_applescript(text: str) -> str:
    """转义 AppleScript 字符串中的反斜杠与双引号。"""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _ps_quote(text: str) -> str:
    """把字符串安全地包成 PowerShell 单引号字符串。"""
    return "'%s'" % str(text).replace("'", "''")


def _replace_placeholders(text: str, subs: Dict[str, str]) -> str:
    """替换 ``{{title}}`` ``{{message}}`` ``{{kind}}`` ``{{timestamp}}`` 占位符。"""
    result = str(text)
    for key, value in subs.items():
        result = result.replace("{{%s}}" % key, str(value))
    return result


def _substitute_obj(node: Any, subs: Dict[str, str]) -> Any:
    """递归替换 JSON 结构中的占位符（只替换字符串值）。"""
    if isinstance(node, str):
        return _replace_placeholders(node, subs)
    if isinstance(node, list):
        return [_substitute_obj(item, subs) for item in node]
    if isinstance(node, dict):
        return {key: _substitute_obj(value, subs) for key, value in node.items()}
    return node


def _build_webhook_body(template: str, subs: Dict[str, str]) -> Tuple[bytes, str]:
    """构造 Webhook 请求体。

    返回 ``(请求体字节, Content-Type)``：

    1. 模板本身是合法 JSON → 替换占位符后按 JSON 发送；
    2. 替换占位符后是合法 JSON → 按 JSON 发送；
    3. 模板里含 ``=`` → 视为表单（``a=1&b=2``），按表单发送；
    4. 其余 → 按纯文本发送。
    """
    obj = _load_json(template)
    if obj is not None:
        payload = json.dumps(_substitute_obj(obj, subs), ensure_ascii=False)
        return payload.encode("utf-8"), "application/json; charset=utf-8"

    replaced = _replace_placeholders(template, subs)
    obj = _load_json(replaced)
    if obj is not None:
        return (
            json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    if "=" in template:
        pairs: List[Tuple[str, str]] = []
        for chunk in replaced.split("&"):
            if not chunk:
                continue
            if "=" in chunk:
                key, value = chunk.split("=", 1)
            else:
                key, value = chunk, ""
            pairs.append((key, value))
        return (
            urllib.parse.urlencode(pairs).encode("utf-8"),
            "application/x-www-form-urlencoded; charset=utf-8",
        )

    return replaced.encode("utf-8"), "text/plain; charset=utf-8"
