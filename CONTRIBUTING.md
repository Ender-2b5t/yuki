# 参与贡献

欢迎 PR / issue。

## 开发环境

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r server\requirements.txt
copy server\config.example.json server\config.json
:: 填好 api_key，按 docs/SETUP_ASSETS.md 准备 vendor / models / Electron
```

启动：

```bat
npm start
:: 或 runtime\electron\electron.exe --no-sandbox .
```

## 提交前检查

```bat
.venv\Scripts\python.exe -m py_compile server\app.py server\tools.py server\screen.py server\stt.py server\sing.py server\awareness.py
node --check main.js
node --check web\pet.js
```

## 约定

- 代码风格：Python 3.11，类型标注尽量加上；JS 用 2 空格缩进、单引号
- 人设文本在 `server/persona.json`、`server/persona_dark.json`，不要提交个人隐私
- **不要**提交 `server/config.json`、`data/`、`models/`、`runtime/`、`vendor/`、`tools/`
- 新增工具请在 `server/tools.py` 的 `TOOL_SCHEMAS` 和 `execute_tool` 里同时加，并在 README 特性列表更新
- 涉及电脑控制的改动请说明安全影响（会不会误操作、能否关闭）

## 提交信息

用简短的英文/中文都可以，例如：

```
feat: add clipboard tools
fix: prevent duplicate summarize task
docs: update setup guide
```
