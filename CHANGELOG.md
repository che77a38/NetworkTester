# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [v1.0.0] - 2026-10-28

首个正式版本。

### 新增

- **联网检测核心**：支持自定义检测目标列表（域名 / IP / 域名:端口 / 带协议的 URL），
  TCP 探测 + 并发检测，支持「任一目标通」与「全部目标通」两种判定方式。
- **图形界面（tkinter）**：顶部状态灯与下次检测倒计时、检测目标编辑区、检测设置区、
  逐通道「启用 + 地址 + 测试」列表、底部实时运行日志；中英双语界面。
- **9 条通知通道**，可独立开关：
  - 桌面通知（macOS `osascript` / Windows PowerShell 气泡 / Linux `notify-send` → `zenity`）
  - 系统声音（macOS `afplay` / Windows 蜂鸣 / Linux `paplay`）
  - **企业微信群机器人**（Markdown 消息，用户填写完整 Webhook 地址）
  - 钉钉群机器人
  - Bark 推送
  - Server酱
  - Telegram Bot
  - SMTP 邮件（支持 SSL / STARTTLS、多收件人）
  - 自定义 Webhook（GET / POST，支持 `{{title}}` `{{message}}` `{{kind}}` `{{timestamp}}` 占位符）
- **开机自启动**：Windows 注册表 `Run` 键、macOS `LaunchAgent`、Linux XDG Autostart，
  界面一键开关，状态与系统实际状态同步。
- **状态机提醒**：断网、恢复联网、程序启动、手动测试四种触发时机。
- **系统托盘**（可选）：安装 `pystray` 后提供托盘图标菜单。
- **命令行模式**：`--check` 单次检测、`--config` 指定配置、`--minimized` 静默启动。
- **跨平台打包**：PyInstaller spec + 三个平台构建脚本 + GitHub Actions 自动构建与 Release。
- **单元测试**：58 个用例，覆盖配置读写、检测逻辑、通知报文格式、开机自启与 GUI 冒烟。
