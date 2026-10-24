# 安全说明

## 报告问题

如果发现安全问题（例如：可以让她执行危险操作、密钥泄露、路径穿越等），请**不要**直接开公开 issue，
而是通过 GitHub 的 [Security Advisories](../../security/advisories/new) 私下报告，或联系仓库维护者。

## 不要提交的东西

仓库已通过 `.gitignore` 排除以下内容，但提交前仍建议自查：

- `server/config.json`（包含 DeepSeek API Key）
- `data/`（聊天记录、长期记忆、日志、截图）
- `models/`、`runtime/`、`vendor/`（体积大/有独立许可）

自查命令：

```bash
git grep -n "sk-" -- .        # 应该没有输出
git ls-files | grep -E "config\.json$|^data/|^models/|^runtime/"
```

## 运行安全

- 「操作电脑」能力默认开启（`tools_enabled`），这意味着模型可以调用工具移动鼠标、打字、打开程序。
  如果不想让 AI 动你的电脑，请在设置面板关闭「允许控制电脑」。
- 「看屏幕」只在你主动询问或你开启「看屏幕吐槽」时截图。
- 使用 `--no-sandbox` 是因为在管理员会话下 Electron 沙箱会崩溃；请只在本机可信目录运行本项目。
- 云端只有两个请求：大模型（DeepSeek，内容会发到 API）和 edge-tts 语音合成（文本会发到微软合成服务）。
  如果你对此敏感，请关闭语音或换成本地 TTS。
