# NetWatch 开发契约（团队并行开发用）

所有模块**只依赖 Python 标准库**，目标 Python 3.8+，跨平台（Windows / macOS / Linux）。
可选依赖只有 `pystray`（托盘图标，缺失时自动降级为最小化到后台）。

包名：`netwatch`，入口：`netwatch.main:main`。

---

## 1. `netwatch/config.py` — 配置存储

```python
DEFAULT_CONFIG: Dict[str, Any]   # 见下
def config_dir() -> Path          # Windows: %APPDATA%/netwatch ; macOS: ~/Library/Application Support/netwatch ; Linux: ~/.config/netwatch
def config_path() -> Path         # config_dir()/"config.json"
def load_config() -> Dict[str, Any]     # 读文件，失败或缺字段时用默认值补齐（深合并）
def save_config(cfg: Dict[str, Any]) -> None   # 原子写入（tmp + os.replace），UTF-8
```

`DEFAULT_CONFIG` 结构（**键名固定，其他模块不得私自改名**）：

```python
{
  "targets": ["www.baidu.com", "www.qq.com", "223.5.5.5"],  # list[str]，支持 "example.com"、"example.com:443"、"https://example.com"
  "target_mode": "any",        # "any" 任一成功即联网 | "all" 全部成功才联网
  "timeout": 5,               # 单目标超时（秒，int 1-30）
  "interval": 30,             # 检测间隔（秒，int 5-3600）
  "notify_on_connect": True,  # 断网 -> 恢复联网时通知
  "notify_on_disconnect": True,
  "notify_on_start": False,   # 程序启动时通知一次
  "notify_test": True,        # 用户点击"测试通知"时通知
  "start_with_os": False,      # 开机自启动开关（写入系统前需用户确认）
  "minimize_to_tray": True,
  "language": "zh",           # "zh" | "en"
  "channels": {               # 每个通道独立开关
      "desktop": True, "sound": True, "wecom": False, "dingtalk": False,
      "bark": False, "serverchan": False, "telegram": False,
      "smtp": False, "webhook": False
  },
  "endpoints": {
      "wecom": "",             # 企业微信机器人完整 webhook 地址（用户填写）
      "dingtalk": "",          # 钉钉机器人完整 webhook 地址（含 access_token）
      "bark": "",              # Bark 推送地址，如 https://api.day.app/xxxx
      "serverchan": "",        # ServerChan SendKey 或完整 URL
      "telegram_token": "", "telegram_chat_id": "",
      "smtp_host": "", "smtp_port": 465, "smtp_user": "", "smtp_password": "",
      "smtp_to": "", "smtp_use_ssl": True,
      "webhook_url": "", "webhook_method": "POST", "webhook_body": ""
  }
}
```

---

## 2. `netwatch/checker.py` — 检测核心

```python
@dataclass
class TargetResult:
    target: str
    online: bool
    latency_ms: int      # -1 表示失败
    error: str           # 失败原因，空串表示成功
    ok_port: int         # 实际连通的端口

@dataclass
class CheckResult:
    online: bool
    results: List[TargetResult]
    timestamp: float     # time.time()
    @property
    def ok_count(self) -> int
    @property
    def total(self) -> int

def parse_target(raw: str) -> Tuple[str, int]:
    """解析目标 -> (host, port)。支持 'example.com' / 'example.com:8443' /
    'https://example.com/path' / 'http://1.1.1.1'。默认端口：https->443, http->80, 无协议->443。"""

def probe(host: str, port: int, timeout: float) -> Tuple[bool, int, str]:
    """TCP 探测，返回 (成功?, 延迟毫秒, 错误信息)。"""

def check_target(raw: str, timeout: float) -> TargetResult:
    """单个目标检测；无端口/协议信息时，先试默认端口，失败再试备用端口(443/80)。"""

def check_connectivity(targets: List[str], timeout: float, mode: str = "any") -> CheckResult:
    """并发检测所有目标，按 mode 汇总 online。"""
```

规则：
- 目标为空 → 返回 `CheckResult(online=False, results=[])`。
- 检测必须**并发**执行（`ThreadPoolExecutor`，并发数 min(8, len(targets))），整体耗时 ≈ 单目标耗时而非累加。
- `mode="all"`：全部成功才 online；`mode="any"`：任一成功即 online。
- 目标列表为空时视为"无法判定"→ online=False。

---

## 3. `netwatch/notifier.py` — 多通道通知

```python
@dataclass
class NotifyEvent:
    kind: str        # "connect" | "disconnect" | "start" | "test"
    title: str
    message: str
    online: bool
    ok_count: int = 0
    total: int = 0
    detail: str = ""     # 附加明细（可选）

class Notifier:
    def __init__(self, config: Dict[str, Any], logger: Optional[Callable[[str], None]] = None)
    def channel_configured(self, channel: str) -> bool   # 通道是否已配置（webhook 非空等）
    def notify(self, event: NotifyEvent) -> Dict[str, str]
        """按 config['channels'] 逐个发送；返回 {channel: "ok" 或 "错误信息"}。
        任何单个通道异常不得抛出，必须捕获并记录到返回值与日志。"""
```

通道实现要求（全部用 urllib 标准库，不引入 requests）：

| 通道 key | 平台 | 实现要点 |
|---|---|---|
| `desktop` | 全平台 | macOS: `osascript -e 'display notification "msg" with title "t"'`；Windows: PowerShell toast（`New-BurntToastNotification` 不可用时回退 `System.Windows.Forms.NotifyIcon` 弹窗）；Linux: `notify-send`，不可用时回退 `zenity --notification`，都没有则记为失败 |
| `sound` | 全平台 | macOS: `afplay /System/Library/Sounds/Ping.aiff`（找不到则 `beep`）；Windows: PowerShell `[console]::beep(880,300)`；Linux: `paplay /usr/share/sounds/freedesktop/stereo/message.oga`，不可用则终端响铃 `\a` |
| `wecom` | 全平台 | POST JSON `{"msgtype":"markdown","markdown":{"content": "..."}}` 到用户填写的完整 webhook URL；超时 10s；企业微信返回 `errcode != 0` 视为失败 |
| `dingtalk` | 全平台 | POST JSON `{"msgtype":"markdown","markdown":{"title": t, "text": m}}` 到用户填写的完整 webhook URL（含 access_token） |
| `bark` | 全平台 | POST JSON `{"title": t, "body": m}` 到用户填写的 URL（可为 base+key 或完整 URL） |
| `serverchan` | 全平台 | 若填的是纯 SendKey 则拼 `https://sctapi.ftqq.com/{key}.send`，POST 表单 `title`/`desp`；若填的是完整 URL 则直接 POST |
| `telegram` | 全平台 | `https://api.telegram.org/bot{token}/sendMessage`，POST JSON `{"chat_id":..., "text":...}` |
| `smtp` | 全平台 | smtplib + SSL/STARTTLS，发送 HTML 纯文本混合邮件到 `smtp_to`（逗号分隔多个收件人） |
| `webhook` | 全平台 | 通用：`webhook_method` 为 GET/POST；POST 时 body 可为 JSON 字符串（若能解析为 JSON 则按 JSON 发送，否则表单发送），可用 `{{title}}` `{{message}}` `{{kind}}` `{{timestamp}}` 占位符替换 |

通知文案统一格式（中文）：
- 联网恢复：标题 `✅ 网络已连接`，正文包含时间、可用目标数、耗时/延迟。
- 断网：标题 `❌ 网络已断开`。
- 其它 kind 用传入的 title/message。

---

## 4. `netwatch/startup.py` — 开机自启动

```python
def is_supported() -> bool
def is_enabled() -> bool
def enable(exec_path: Optional[str] = None) -> Tuple[bool, str]   # 返回 (成功?, 说明)
def disable() -> Tuple[bool, str]
def status() -> Dict[str, Any]
```

- macOS：写 `~/Library/LaunchAgents/com.netwatch.launcher.plist`（`RunAtLoad=true`），指向可执行文件；卸载删除 plist。
- Windows：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 键 `NetWatch` 指向 exe（用 winreg）。
- Linux：写 `~/.config/autostart/netwatch.desktop`（`[Desktop Entry] X-GNOME-Autostart-enabled=true`）。
- `exec_path` 为 None 时自动推断：如果是打包后的 exe/frozen 用 `sys.executable`，否则用 `python 脚本路径`。
- macOS 用 `launchctl load/unload` 生效；失败不致命（下次登录仍会加载）。

---

## 5. `netwatch/gui.py` — 图形界面（tkinter）

- `class NetWatchApp(tk.Tk)`，入口 `main()` 创建并运行。
- 界面分区：
  1. **顶部状态卡片**：大号状态灯（绿=已联网 / 红=未联网 / 灰=检测中）、文字状态、最后一次检测时间、下次检测倒计时。
  2. **左侧「检测目标」**：多行文本框（每行一个目标）+ 说明文字；右侧「检测设置」：判定方式(任一/全部)、超时、间隔、开关（恢复通知/断开通知/启动通知/开机自启/启动即最小化/语言）。
  3. **「通知通道」**：每个通道一行：启用勾选 + 输入框 + 「测试」按钮。通道顺序：桌面通知、系统声音、企业微信机器人、钉钉机器人、Bark、Server酱、Telegram、SMTP 邮件、自定义 Webhook。
  4. **底部日志区**：滚动文本框（只读）+ 「清空日志」按钮 + 「立即检测」按钮。
- 顶部状态栏保留「保存配置」「退出」。
- 后台线程做检测与通知，GUI 通过 `queue.Queue` + `root.after(200, self._drain_queue)` 更新，避免阻塞。
- 关闭窗口：若开启"最小化到托盘"则隐藏（tray 不可用时退化为隐藏），并提示"后台仍在运行"；托盘右键菜单含 显示/立即检测/退出。
- 所有文案通过 `netwatch/i18n.py` 的 `tr(key, lang)` 取（中/英）。

---

## 6. `netwatch/main.py` — 入口

```
python -m netwatch              # 启动 GUI
python -m netwatch --check      # 单次检测，命令行输出，退出码 0=联网 1=断网
python -m netwatch --config PATH
python -m netwatch --version
```
`main()` 返回 int 退出码。

---

## 7. 其它

- `netwatch/i18n.py`：`TRANSLATIONS: Dict[str, Dict[str, str]]` + `tr(key, lang="zh")`。
- `netwatch/__main__.py`：`from .main import main; sys.exit(main())`。
- 测试放 `tests/`，用标准库 `unittest`，命令 `python -m unittest discover -s tests -v`。
- 代码注释与文档中文为主，标识符英文。
