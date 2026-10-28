"""NetWatch —— 跨平台联网检测小工具。

模块划分：
  - ``config``   配置读写（跨平台配置目录）
  - ``checker``  联网检测核心（TCP 探测 + 并发汇总）
  - ``notifier`` 多通道通知（桌面/声音/企业微信/钉钉/Bark/Server酱/Telegram/SMTP/Webhook）
  - ``startup``  开机自启动（Windows 注册表 / macOS LaunchAgent / Linux autostart）
  - ``gui``      tkinter 图形界面
  - ``i18n``     中英文案
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
