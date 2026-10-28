# 界面截图说明

本目录下的 `screenshot.png` 是 NetWatch 界面的**占位示意图**，用于让
[README](../README.md) 中的截图引用不失效。正式发布前请替换为真实截图。

## 如何替换

1. 启动 NetWatch：`python -m netwatch`
2. 按需要调整界面（例如展开「通知通道」区域、填好企业微信等通道的地址），
   使截图能展示工具的主要能力。
3. 截图建议：

   | 项目 | 建议值 |
   |---|---|
   | 图像格式 | PNG |
   | 图像宽度 | 1120 px（与占位图一致，可避免 README 中图片被拉伸变形） |
   | 主题 | 浅色或深色均可，保持与占位图相近的留白比例 |

4. 覆盖本目录下的文件即可：

   ```bash
   # macOS
   screencapture -i -x -t png docs/screenshot.png

   # Windows（PowerShell）
   # 用系统截图工具截取 NetWatch 窗口后另存为 docs\screenshot.png
   ```

5. 提交改动：

   ```bash
   git add docs/screenshot.png
   git commit -m "docs: 更新界面截图"
   ```

## 其他图片

如果后续需要增加更多截图（例如「通知通道设置」「开机自启设置」等细节图），
建议统一放在本目录，并在 [README](../README.md) 中以相对路径引用，例如：

```markdown
![通知通道设置](docs/screenshots/channels.png)
```
