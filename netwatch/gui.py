"""NetWatch 图形界面（tkinter）。

界面分四块：顶部状态卡、「检测目标 + 检测设置」、「通知通道」、「运行日志」。
检测与通知在后台线程执行，界面通过队列 + after 轮询更新，保证不卡顿。
"""

from __future__ import annotations

import datetime
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

from . import config as config_mod
from . import startup
from .checker import CheckResult, check_connectivity
from .i18n import tr
from .notifier import Notifier, NotifyEvent

CHANNEL_ORDER: List[str] = [
    "desktop",
    "sound",
    "wecom",
    "dingtalk",
    "bark",
    "serverchan",
    "telegram",
    "smtp",
    "webhook",
]

# 需要额外参数（除默认单行地址外）的通道
COMPLEX_CHANNELS = {"telegram", "smtp", "webhook"}


def _now_text() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Monitor(threading.Thread):
    """后台监控线程：定时检测网络，状态变化时触发通知。"""

    def __init__(
        self,
        get_config: Callable[[], Dict[str, Any]],
        post: Callable[[Dict[str, Any]], None],
        log: Callable[[str], None],
    ) -> None:
        super().__init__(daemon=True, name="netwatch-monitor")
        self._get_config = get_config
        self._post = post
        self._log = log
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._state: Optional[bool] = None

    # -- 对外控制 ------------------------------------------------------------
    def trigger(self) -> None:
        """请求立即检测一次。"""
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- 主循环 --------------------------------------------------------------
    def run(self) -> None:
        self._log("监控线程已启动")
        while not self._stop.is_set():
            self._wake.clear()
            cfg = self._get_config()
            targets = cfg.get("targets") or []
            timeout = cfg.get("timeout", 5)
            interval = cfg.get("interval", 30)
            mode = cfg.get("target_mode", "any")

            self._post({"type": "checking"})
            result = check_connectivity(targets, timeout, mode)
            self._post({"type": "result", "result": result})
            try:
                self._handle_transition(cfg, result)
            except Exception as exc:  # pragma: no cover - 通知异常不应中断监控
                self._log("通知发送异常: %s" % exc)

            self._post({"type": "next", "ts": time.time() + interval})
            self._wake.wait(max(1, int(interval)))

    # -- 状态机 --------------------------------------------------------------
    def _handle_transition(self, cfg: Dict[str, Any], result: CheckResult) -> None:
        state = result.online
        prev = self._state
        self._state = state

        if prev is None:
            if state and cfg.get("notify_on_start"):
                self._send(
                    cfg,
                    NotifyEvent(
                        kind="start",
                        title="🚀 NetWatch 已启动",
                        message="开始监控网络状态\n检测目标: %s" % ", ".join(cfg.get("targets") or []),
                        online=True,
                        ok_count=result.ok_count,
                        total=result.total,
                    ),
                )
            return

        if state == prev:
            return

        if state:
            self._send(
                cfg,
                NotifyEvent(
                    kind="connect",
                    title="✅ 网络已连接",
                    message=self._failed_text(result),
                    online=True,
                    ok_count=result.ok_count,
                    total=result.total,
                    detail=self._latency_text(result),
                ),
            )
        else:
            self._send(
                cfg,
                NotifyEvent(
                    kind="disconnect",
                    title="❌ 网络已断开",
                    message=self._failed_text(result),
                    online=False,
                    ok_count=result.ok_count,
                    total=result.total,
                    detail=self._latency_text(result),
                ),
            )

    @staticmethod
    def _failed_text(result: CheckResult) -> str:
        """失败目标明细；全部成功时返回空串（通知模块会统一补时间与可用目标数）。"""
        failed = [r.label for r in result.results if not r.online]
        if not failed:
            return ""
        return "失败目标: %s" % "; ".join(failed[:3])

    @staticmethod
    def _latency_text(result: CheckResult) -> str:
        latency = result.avg_latency_ms
        if latency >= 0:
            return "平均延迟 %d ms" % latency
        return "所有目标均不可达"

    def _send(self, cfg: Dict[str, Any], event: NotifyEvent) -> None:
        self._log("发送通知: %s" % event.title)
        notifier = Notifier(cfg, logger=self._log)
        results = notifier.notify(event)
        for channel, status in results.items():
            if status == "ok":
                self._log("  [%s] 发送成功" % channel)
            else:
                self._log("  [%s] %s" % (channel, status))


class _IdleMonitor:
    """不执行任何检测的空监控器。

    仅用于单元测试：避免测试过程中发起真实网络探测，
    让 CI（尤其是无外网或网络栈较慢的 runner）保持稳定。
    """

    def __init__(self, inner: Monitor) -> None:
        self._inner = inner

    def trigger(self) -> None:
        """测试模式下忽略立即检测请求。"""

    def stop(self) -> None:
        self._inner.stop()

    def is_alive(self) -> bool:
        return False


class NetWatchApp(tk.Tk):
    """主窗口。"""

    def __init__(
        self,
        config_file: Optional[str] = None,
        minimized: bool = False,
        start_monitor: bool = True,
    ) -> None:
        super().__init__()
        self.cfg = config_mod.load_config(config_mod.Path(config_file) if config_file else None)
        self.lang = self.cfg.get("language", "zh")
        self._config_file = config_mod.Path(config_file) if config_file else None
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._tray = None
        self._closing = False
        self._next_check_at = 0.0
        self._minimized = minimized

        self.title(tr("app_title", self.lang))
        self.geometry("1020x780")
        self.minsize(880, 620)
        self.configure(bg=self._bg())

        self._build_style()
        self._build_ui()
        self._load_config_to_ui()

        # start_monitor=False 用于单元测试：避免测试过程发起真实网络探测
        self.monitor = Monitor(self._current_config, self._post, self._log)
        if start_monitor:
            self.monitor.start()
        else:
            self.monitor = _IdleMonitor(self.monitor)

        self.after(200, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_tray()
        if self._minimized:
            self.after(500, self._minimize_startup)

    # ------------------------------------------------------------------ 样式
    @staticmethod
    def _bg() -> str:
        return "#f4f6fa"

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Card.TFrame", background=self._bg())
        style.configure("Card.TLabelframe", background=self._bg())
        style.configure("Card.TLabelframe.Label", background=self._bg(), font=("", 11, "bold"))
        style.configure("Head.TLabel", background=self._bg(), font=("", 22, "bold"))
        style.configure("Sub.TLabel", background=self._bg(), font=("", 11))
        style.configure("Muted.TLabel", background=self._bg(), foreground="#5a6478")
        style.configure("TButton", padding=(10, 5))
        style.configure("TNotebook.Tab", padding=(14, 6))

    # ------------------------------------------------------------------ 界面
    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        self._build_status_card()
        self._build_notebook()
        self._build_bottom_bar()

    def _build_status_card(self) -> None:
        card = ttk.Frame(self, style="Card.TFrame", padding=(18, 14))
        card.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        card.columnconfigure(2, weight=1)

        self.lamp = tk.Canvas(card, width=42, height=42, bg=self._bg(), highlightthickness=0)
        self.lamp.grid(row=0, column=0, rowspan=2, padx=(0, 14))
        self.lamp_dot = self.lamp.create_oval(6, 6, 36, 36, fill="#9aa4b8", outline="")

        self.status_label = ttk.Label(
            card, text=tr("status_unknown", self.lang), style="Head.TLabel"
        )
        self.status_label.grid(row=0, column=0, columnspan=3, sticky="w")

        self.info_label = ttk.Label(card, text="", style="Muted.TLabel")
        self.info_label.grid(row=1, column=0, columnspan=3, sticky="w")

        self.summary_label = ttk.Label(card, text="", style="Sub.TLabel")
        self.summary_label.grid(row=0, column=2, rowspan=2, sticky="e")

    def _build_notebook(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew", padx=14, pady=6)

        tab_basic = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        tab_notify = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        tab_log = ttk.Frame(notebook, style="Card.TFrame", padding=12)
        notebook.add(tab_basic, text=tr("targets_title", self.lang))
        notebook.add(tab_notify, text=tr("channels_title", self.lang))
        notebook.add(tab_log, text=tr("log_title", self.lang))
        self._notebook = notebook

        self._build_basic_tab(tab_basic)
        self._build_notify_tab(tab_notify)
        self._build_log_tab(tab_log)

    def _build_basic_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        # ---- 左：检测目标
        left = ttk.Frame(parent, style="Card.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))

        ttk.Label(left, text=tr("targets_title", self.lang)).grid(row=0, column=0, sticky="w")
        self.targets_text = tk.Text(left, height=14, wrap="none", font=("Menlo", 11))
        self.targets_text.grid(row=1, column=0, sticky="nsew", pady=(6, 4))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text=tr("targets_hint", self.lang), style="Muted.TLabel", wraplength=420).grid(
            row=2, column=0, sticky="w"
        )

        # ---- 右：检测设置
        right = ttk.Frame(parent, style="Card.TFrame")
        right.grid(row=0, column=1, sticky="nsew")

        ttk.Label(right, text=tr("settings_title", self.lang)).grid(row=0, column=0, columnspan=2, sticky="w")

        self.mode_var = tk.StringVar(value="any")
        self.timeout_var = tk.IntVar(value=5)
        self.interval_var = tk.IntVar(value=30)
        self.lang_var = tk.StringVar(value=self.lang)

        row = 1
        ttk.Label(right, text=tr("mode_label", self.lang)).grid(row=row, column=0, sticky="w", pady=(10, 2))
        mode_box = ttk.Frame(right, style="Card.TFrame")
        mode_box.grid(row=row, column=1, sticky="w", pady=(10, 2))
        ttk.Radiobutton(
            mode_box, text=tr("mode_any", self.lang), value="any", variable=self.mode_var
        ).pack(anchor="w")
        ttk.Radiobutton(
            mode_box, text=tr("mode_all", self.lang), value="all", variable=self.mode_var
        ).pack(anchor="w")

        row += 1
        ttk.Label(right, text=tr("timeout_label", self.lang)).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(right, from_=1, to=30, textvariable=self.timeout_var, width=8).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(right, text=tr("interval_label", self.lang)).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(right, from_=5, to=3600, increment=5, textvariable=self.interval_var, width=8).grid(
            row=row, column=1, sticky="w", pady=4
        )

        row += 1
        ttk.Label(right, text=tr("language_label", self.lang)).grid(row=row, column=0, sticky="w", pady=4)
        lang_box = ttk.Combobox(
            right, textvariable=self.lang_var, values=["zh", "en"], state="readonly", width=8
        )
        lang_box.grid(row=row, column=1, sticky="w", pady=4)
        lang_box.bind("<<ComboboxSelected>>", lambda _evt: self._on_language_change())

        row += 1
        self.notify_connect_var = tk.BooleanVar(value=True)
        self.notify_disconnect_var = tk.BooleanVar(value=True)
        self.notify_start_var = tk.BooleanVar(value=False)
        self.startup_var = tk.BooleanVar(value=False)
        self.tray_var = tk.BooleanVar(value=True)

        checks = [
            (self.notify_connect_var, tr("notify_connect", self.lang)),
            (self.notify_disconnect_var, tr("notify_disconnect", self.lang)),
            (self.notify_start_var, tr("notify_start", self.lang)),
            (self.startup_var, tr("start_with_os", self.lang)),
            (self.tray_var, tr("minimize_tray", self.lang)),
        ]
        for var, label in checks:
            row += 1
            ttk.Checkbutton(right, text=label, variable=var).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=2
            )

    def _build_notify_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        # 通道较多，放进可滚动区域，避免窗口不够高时被裁掉
        canvas = tk.Canvas(parent, highlightthickness=0, bd=0, background=self._bg())
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas, style="Card.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def on_inner_configure(_event: Any = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event: Any) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        inner.bind("<Configure>", on_inner_configure)
        canvas.bind("<Configure>", on_canvas_configure)
        canvas.bind_all(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
        )

        self.channel_vars: Dict[str, tk.BooleanVar] = {}
        self.field_vars: Dict[str, Any] = {}
        self.field_widgets: Dict[str, Any] = {}
        self.placeholders: Dict[str, str] = {}

        inner.columnconfigure(1, weight=1)

        row = 0
        for channel in CHANNEL_ORDER:
            var = tk.BooleanVar(value=bool(self.cfg["channels"].get(channel)))
            self.channel_vars[channel] = var

            ttk.Checkbutton(
                inner,
                text=tr("channel_%s" % channel, self.lang),
                variable=var,
                command=lambda c=channel: self._on_channel_toggle(c),
            ).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=(6 if row else 0, 0))

            body = ttk.Frame(inner, style="Card.TFrame")
            body.grid(row=row, column=1, sticky="ew", pady=(6 if row else 0, 0))
            body.columnconfigure(0, weight=1)

            self._build_channel_fields(body, channel)

            ttk.Button(
                inner,
                text=tr("test_btn", self.lang),
                width=8,
                command=lambda c=channel: self._test_channel(c),
            ).grid(row=row, column=2, sticky="e", padx=(10, 0), pady=(6 if row else 0, 0))

            row += 1

        ttk.Label(
            inner,
            text="提示：各通道的「测试」按钮会单独向该通道发送一条测试消息。",
            style="Muted.TLabel",
        ).grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _build_channel_fields(self, body: ttk.Frame, channel: str) -> None:
        """为通道构建参数输入区。"""
        endpoints = self.cfg["endpoints"]

        if channel in ("desktop", "sound"):
            ttk.Label(body, text="—", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
            return

        if channel == "telegram":
            self._entry(body, 0, "telegram_token", tr("placeholder_telegram", self.lang),
                        endpoints.get("telegram_token", ""))
            self._entry(body, 1, "telegram_chat_id", "Chat ID", endpoints.get("telegram_chat_id", ""))
            return

        if channel == "smtp":
            self._entry(body, 0, "smtp_host", "smtp.example.com", endpoints.get("smtp_host", ""))
            self._entry(body, 1, "smtp_port", "465", str(endpoints.get("smtp_port", 465)))
            self._entry(body, 2, "smtp_user", "user@example.com", endpoints.get("smtp_user", ""))
            self._entry(body, 3, "smtp_password", "授权码/密码", endpoints.get("smtp_password", ""))
            self._entry(body, 4, "smtp_to", "a@example.com,b@example.com", endpoints.get("smtp_to", ""))
            ssl_var = tk.BooleanVar(value=bool(endpoints.get("smtp_use_ssl", True)))
            self.field_vars["smtp_use_ssl"] = ssl_var
            ttk.Checkbutton(body, text="使用 SSL", variable=ssl_var).grid(row=5, column=0, sticky="w")
            return

        if channel == "webhook":
            self._entry(body, 0, "webhook_url", tr("placeholder_webhook", self.lang),
                        endpoints.get("webhook_url", ""))
            method = tk.StringVar(value=str(endpoints.get("webhook_method", "POST")).upper())
            self.field_vars["webhook_method"] = method
            self.field_widgets["webhook_method"] = ttk.Combobox(
                body, textvariable=method, values=["POST", "GET"], state="readonly", width=8
            )
            ttk.Label(body, text="Method").grid(row=1, column=0, sticky="w")
            self.field_widgets["webhook_method"].grid(row=1, column=1, sticky="w")
            self._entry(body, 2, "webhook_body", '{"title":"{{title}}","body":"{{message}}"}',
                        endpoints.get("webhook_body", ""))
            return

        # 单一地址类通道
        key = channel
        placeholder = tr("placeholder_%s" % channel, self.lang)
        self._entry(body, 0, key, placeholder, endpoints.get(key, ""))

    def _entry(self, body: ttk.Frame, row: int, key: str, placeholder: str, value: Any) -> None:
        """构建一个带灰色占位提示的输入框。"""
        text = "" if value is None else str(value)
        var = tk.StringVar(value=text)
        self.field_vars[key] = var
        self.placeholders[key] = placeholder
        entry = ttk.Entry(body, textvariable=var)
        entry.grid(row=row, column=0, sticky="ew", pady=1)
        self.field_widgets[key] = entry

        grey = "#5a6478"
        dark = "#1f2430"

        def show_placeholder() -> None:
            if not var.get():
                var.set(placeholder)
                entry.configure(foreground=grey)

        def on_focus_in(_event: Any = None) -> None:
            if var.get() == placeholder:
                var.set("")
                entry.configure(foreground=dark)

        def on_focus_out(_event: Any = None) -> None:
            if not var.get():
                show_placeholder()
            else:
                entry.configure(foreground=dark)

        def on_change(*_args: Any) -> None:
            if var.get() and var.get() != placeholder:
                entry.configure(foreground=dark)

        var.trace_add("write", on_change)
        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        if not text:
            show_placeholder()
        else:
            entry.configure(foreground=dark)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self.log_text = tk.Text(parent, height=12, state="disabled", font=("Menlo", 10))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

        bar = ttk.Frame(parent, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text=tr("clear_log", self.lang), command=self._clear_log).pack(side="left")
        ttk.Button(bar, text=tr("check_now", self.lang), command=self._check_now).pack(side="left", padx=6)

    def _build_bottom_bar(self) -> None:
        bar = ttk.Frame(self, style="Card.TFrame", padding=(14, 6))
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self.hint_label = ttk.Label(bar, text="", style="Muted.TLabel")
        self.hint_label.grid(row=0, column=0, sticky="w")

        ttk.Button(bar, text=tr("check_now", self.lang), command=self._check_now).grid(
            row=0, column=1, padx=4
        )
        ttk.Button(bar, text=tr("save_btn", self.lang), command=self._save).grid(row=0, column=2, padx=4)
        ttk.Button(bar, text=tr("exit_btn", self.lang), command=self._quit).grid(row=0, column=3, padx=4)

    # ------------------------------------------------------------------ 配置读写
    def _load_config_to_ui(self) -> None:
        cfg = self.cfg
        self.targets_text.delete("1.0", "end")
        self.targets_text.insert("1.0", "\n".join(cfg.get("targets") or []))
        self.mode_var.set(cfg.get("target_mode", "any"))
        self.timeout_var.set(cfg.get("timeout", 5))
        self.interval_var.set(cfg.get("interval", 30))
        self.lang_var.set(cfg.get("language", "zh"))
        self.notify_connect_var.set(cfg.get("notify_on_connect", True))
        self.notify_disconnect_var.set(cfg.get("notify_on_disconnect", True))
        self.notify_start_var.set(cfg.get("notify_on_start", False))
        self.startup_var.set(startup.is_enabled())
        self.tray_var.set(cfg.get("minimize_to_tray", True))
        self.cfg["start_with_os"] = self.startup_var.get()

    def _collect_config(self) -> Dict[str, Any]:
        cfg = config_mod.load_config(self._config_file)
        raw_targets = self.targets_text.get("1.0", "end").splitlines()
        cfg["targets"] = [line.strip() for line in raw_targets if line.strip()]
        cfg["target_mode"] = self.mode_var.get()
        try:
            cfg["timeout"] = int(self.timeout_var.get())
        except (TypeError, ValueError):
            cfg["timeout"] = 5
        try:
            cfg["interval"] = int(self.interval_var.get())
        except (TypeError, ValueError):
            cfg["interval"] = 30
        cfg["language"] = self.lang_var.get()
        cfg["notify_on_connect"] = bool(self.notify_connect_var.get())
        cfg["notify_on_disconnect"] = bool(self.notify_disconnect_var.get())
        cfg["notify_on_start"] = bool(self.notify_start_var.get())
        cfg["minimize_to_tray"] = bool(self.tray_var.get())

        channels = dict(cfg.get("channels") or {})
        for channel, var in self.channel_vars.items():
            channels[channel] = bool(var.get())
        cfg["channels"] = channels

        endpoints = dict(cfg.get("endpoints") or {})
        for key, var in self.field_vars.items():
            if isinstance(var, tk.BooleanVar):
                endpoints[key] = bool(var.get())
            else:
                value = var.get()
                # 灰色占位提示文字不算用户填写的值
                if value == self.placeholders.get(key):
                    value = ""
                endpoints[key] = value
        cfg["endpoints"] = endpoints
        return config_mod.clamp_config(cfg)

    def _current_config(self) -> Dict[str, Any]:
        """供监控线程读取的最新配置（不触碰 Tk 变量，只用内存中的副本）。"""
        return dict(self.cfg)

    def _save(self, silent: bool = False) -> bool:
        cfg = self._collect_config()
        ok = config_mod.save_config(cfg, self._config_file)
        self.cfg = cfg

        # 开机自启动同步
        want_startup = bool(self.startup_var.get())
        if want_startup != startup.is_enabled():
            if want_startup:
                success, message = startup.enable()
            else:
                success, message = startup.disable()
            self._log(message)
            if not success:
                self.startup_var.set(not want_startup)
                if not silent:
                    messagebox.showwarning(tr("save_btn", self.lang), message)

        if not silent:
            if ok:
                self.hint_label.configure(text=tr("saved", self.lang))
                self._log("%s -> %s" % (tr("saved", self.lang), config_mod.config_path()))
            else:
                self.hint_label.configure(text=tr("save_failed", self.lang))
                messagebox.showerror(tr("save_btn", self.lang), tr("save_failed", self.lang))
        # 让监控线程立刻按新配置跑一轮
        if hasattr(self, "monitor"):
            self.monitor.trigger()
        return ok

    # ------------------------------------------------------------------ 交互
    def _on_language_change(self) -> None:
        """切换语言后重建界面文案。"""
        self.lang = self.lang_var.get()
        self.title(tr("app_title", self.lang))
        self._retranslate()
        self._save(silent=True)

    def _retranslate(self) -> None:
        self.status_label.configure(text=tr("status_unknown", self.lang))
        self.info_label.configure(text="")
        self.summary_label.configure(text="")
        self._notebook.tab(0, text=tr("targets_title", self.lang))
        self._notebook.tab(1, text=tr("channels_title", self.lang))
        self._notebook.tab(2, text=tr("log_title", self.lang))
        self.hint_label.configure(text="")

    def _check_now(self) -> None:
        self._log("手动触发检测…")
        if hasattr(self, "monitor"):
            self.monitor.trigger()
        self.status_label.configure(text=tr("status_checking", self.lang))

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _test_channel(self, channel: str) -> None:
        """只向指定通道发送一条测试通知。"""
        cfg = self._collect_config()
        one = config_mod.load_config(self._config_file)
        one["channels"] = {name: name == channel for name in CHANNEL_ORDER}
        one["endpoints"] = cfg["endpoints"]
        one["language"] = cfg.get("language", self.lang)

        if channel in ("wecom", "dingtalk", "bark", "serverchan", "webhook"):
            key = channel
            if not (cfg.get("endpoints") or {}).get(key):
                messagebox.showwarning(
                    tr("test_btn", self.lang),
                    "请先填写 %s 的地址" % tr("channel_%s" % channel, self.lang),
                )
                return
        if channel == "telegram":
            ep = cfg.get("endpoints") or {}
            if not ep.get("telegram_token") or not ep.get("telegram_chat_id"):
                messagebox.showwarning(
                    tr("test_btn", self.lang), "请先填写 Telegram Bot Token 与 Chat ID"
                )
                return
        if channel == "smtp":
            ep = cfg.get("endpoints") or {}
            if not ep.get("smtp_host") or not ep.get("smtp_to"):
                messagebox.showwarning(tr("test_btn", self.lang), "请先填写 SMTP 服务器与收件人")
                return

        event = NotifyEvent(
            kind="test",
            title="🔔 NetWatch 测试通知",
            message="这是一条来自 NetWatch 的测试通知。\n时间: %s" % _now_text(),
            online=True,
        )
        self._log("发送测试通知: %s" % channel)

        def worker() -> None:
            notifier = Notifier(one, logger=self._log)
            results = notifier.notify(event)
            status = results.get(channel, "unknown")
            if status == "ok":
                self._post({"type": "log", "text": "  [%s] 测试通知发送成功" % channel})
            else:
                self._post({"type": "log", "text": "  [%s] 测试失败: %s" % (channel, status)})
            self._post({"type": "test_done", "channel": channel, "status": status})

        threading.Thread(target=worker, daemon=True).start()

    def _on_channel_toggle(self, channel: str) -> None:
        enabled = self.channel_vars[channel].get()
        for key, widget in self.field_widgets.items():
            if key.endswith("_placeholder"):
                continue
            try:
                if key in ("wecom", "dingtalk", "bark", "serverchan", "telegram_token",
                           "telegram_chat_id", "smtp_host", "smtp_port", "smtp_user",
                           "smtp_password", "smtp_to", "webhook_url", "webhook_body"):
                    widget.configure(state="normal" if enabled else "disabled")
                elif key == "webhook_method":
                    widget.configure(state="readonly" if enabled else "disabled")
                elif key == "smtp_use_ssl":
                    widget.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass

    def _setup_tray(self) -> None:
        """可选的系统托盘图标（缺少 pystray/Pillow 时自动降级）。"""
        try:
            import pystray  # type: ignore
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            return

        try:
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((6, 6, 58, 58), fill=(38, 166, 91, 255))
            menu = pystray.Menu(
                pystray.MenuItem(tr("tray_show", self.lang), lambda: self._restore()),
                pystray.MenuItem(tr("tray_check", self.lang), lambda: self._check_now()),
                pystray.MenuItem(tr("tray_quit", self.lang), lambda: self._quit()),
            )
            self._tray = pystray.Icon("netwatch", image, "NetWatch", menu)
            self._tray.run_detached()
        except Exception:
            self._tray = None

    def _restore(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def _minimize_startup(self) -> None:
        """以 --minimized 启动：优先藏到托盘，没有托盘则保持窗口可见。"""
        if self._tray is not None:
            self.withdraw()
            self._log(tr("minimized", self.lang))
        else:
            self.deiconify()

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.cfg.get("minimize_to_tray", True):
            if self._tray is not None:
                self.withdraw()
                self._log(tr("minimized", self.lang))
                return
            answer = messagebox.askyesno(
                tr("app_title", self.lang),
                "未检测到系统托盘支持，关闭窗口后程序仍会在后台运行。\n"
                "是否直接退出程序？\n\n(选择「否」则隐藏窗口继续后台运行)",
            )
            if not answer:
                self.withdraw()
                self._log(tr("minimized", self.lang))
                return
        self._quit()

    def _quit(self) -> None:
        self._closing = True
        try:
            if hasattr(self, "monitor"):
                self.monitor.stop()
            if self._tray is not None:
                self._tray.stop()
        except Exception:
            pass
        self.destroy()

    # ------------------------------------------------------------------ 队列更新
    def _post(self, message: Dict[str, Any]) -> None:
        self._queue.put(message)

    def _log(self, text: str) -> None:
        self._post({"type": "log", "text": text})

    def _drain_queue(self) -> None:
        try:
            while True:
                message = self._queue.get_nowait()
                self._handle_message(message)
        except queue.Empty:
            pass
        except Exception as exc:  # pragma: no cover
            self._append_log("界面刷新异常: %s" % exc)
        if not self._closing:
            self.after(200, self._drain_queue)

    def _handle_message(self, message: Dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "log":
            self._append_log(message.get("text", ""))
        elif kind == "checking":
            self.status_label.configure(text=tr("status_checking", self.lang))
        elif kind == "result":
            self._show_result(message.get("result"))
        elif kind == "next":
            self._next_check_at = float(message.get("ts") or 0)
            self._update_countdown()
        elif kind == "test_done":
            channel = message.get("channel", "")
            status = message.get("status", "")
            if status == "ok":
                self.hint_label.configure(text="[%s] ok" % channel)
            else:
                self.hint_label.configure(text="[%s] %s" % (channel, status))

    def _show_result(self, result: Optional[CheckResult]) -> None:
        if result is None:
            return
        if result.online:
            self.status_label.configure(text=tr("status_online", self.lang))
            self.lamp.itemconfigure(self.lamp_dot, fill="#26a65b")
        else:
            self.status_label.configure(text=tr("status_offline", self.lang))
            self.lamp.itemconfigure(self.lamp_dot, fill="#d64545")

        latency = result.avg_latency_ms
        parts = [
            "%s: %s" % (tr("last_check", self.lang), _now_text()),
            "%s: %s" % (tr("next_check", self.lang), "%ds" % max(0, int(self._next_check_at - time.time()))),
        ]
        if result.total:
            parts.append("%d/%d" % (result.ok_count, result.total))
        if latency >= 0:
            parts.append("%d ms" % latency)
        self.info_label.configure(text="    |    ".join(parts))
        self.summary_label.configure(text=result.summary())

        for item in result.results:
            self._append_log(
                "  %s %s" % ("✔" if item.online else "✘", item.label)
            )

    def _update_countdown(self) -> None:
        remain = max(0, int(self._next_check_at - time.time()))
        self.info_label.configure(
            text="    |    ".join(
                [
                    "%s: %s" % (tr("last_check", self.lang), _now_text()),
                    "%s: %ds" % (tr("next_check", self.lang), remain),
                ]
            )
        )

    def _append_log(self, text: str) -> None:
        if not text:
            return
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "%s  %s\n" % (_now_text(), text))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass


def main(config_file: Optional[str] = None, minimized: bool = False) -> int:
    """启动图形界面。"""
    app = NetWatchApp(config_file=config_file, minimized=minimized)
    app.mainloop()
    return 0
