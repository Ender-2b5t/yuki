# yuki · 桌面 AI 少女

> 一个跑在 Windows 桌面上的 Live2D AI 少女：能聊天、能听你说话、能看屏幕、能动手帮你操作电脑，有长期记忆和自己的「生命状态」。
>
> 灵感来自 Neuro-sama，但**全部本地运行**（只有大模型走云端 API）。

<p>
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D6?logo=windows">
  <img alt="python" src="https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-green">
</p>

> 本仓库（源码）**不包含**任何 Live2D 模型、角色立绘、Live2D Cubism Core 或便携运行时；需要的话看 [docs/SETUP_ASSETS.md](docs/SETUP_ASSETS.md)。
> 想直接下载能跑的版本，见下面「下载便携版」。

## 下载便携版（Windows）

不想装 Python / Node 的话：到 [Releases](https://github.com/Ender-2b5t/yuki/releases/latest) 下载
`yuki-*-portable-win64.zip`，解压到新文件夹 → 双击 `yuki.exe` 即可启动。

- 包内已带便携 Python、Electron、语音识别模型、Live2D 模型和前端库
- **不含任何 API Key**：第一次运行按包内 `README-FIRST.txt`，在设置面板（⚙）里填自己的 DeepSeek Key
- 包内 Live2D 模型由本项目作者提供，仅供个人使用；Live2D Cubism Core 版权归 Live2D 公司，请遵守其许可条款


## 特性

- **Live2D 桌宠**：透明置顶窗口、可拖动、视线跟随鼠标、点身体不同部位有不同反应、35 个表情、空闲小动作
- **对话**：DeepSeek 大模型 + 本地记忆（SQLite 聊天记录 + 长期记忆自动总结 + 生命状态落盘，重启不失忆）
- **语音输入**：离线中文语音识别（sherpa-onnx），支持免提唤醒（喊「yuki」）
- **语音输出**：edge-tts 在线合成 + 口型同步（默认关闭，可在设置里打开）
- **电脑助手**：26 个工具——打开软件/网页/文件、打字、移动/点击/滚动/拖拽鼠标、快捷键、切窗口、读写剪贴板、调音量、媒体键、设提醒、截图、看屏幕文字、锁屏
- **多步任务**：一句话交代「打开记事本，打上这段话，再把鼠标移到中间」，她会连续调用工具做完再汇报
- **看屏幕**：截图 + 离线中文 OCR，能读屏幕上的文字；可开启「看屏幕自动吐槽」
- **人格**：可爱、软、有点傲娇、体贴；双人格（普通 / 黑 yuki）
- **唱歌**：三首儿歌（小星星 / 生日快乐 / 两只老虎），有音高和旋律
- **设置面板**：改名字、音色、语速、模型大小位置、各项开关、查看心情/关系
- **本地优先**：记忆、聊天记录、截图、密钥全部在本机 `data/`，不上传

## 架构

```
┌─────────────────────────────  Electron 桌面壳 (main.js) ─────────────────────────────┐
│  透明置顶窗口 / 托盘菜单 / 全局鼠标坐标广播 / 窗口拖动 / 设置窗口                        │
└───────────────┬─────────────────────────────────────────────────────────┬────────────┘
                │ WebSocket (/ws)                                         │ HTTP (/api/*)
        ┌───────▼────────┐                                   ┌────────────▼─────────────┐
        │ Live2D 渲染前端  │                                   │ FastAPI 后端 (server/app.py)│
        │ web/pet.js      │                                   │ 对话/工具/记忆/语音/设置   │
        │ pixi.js         │                                   └────────────┬─────────────┘
        │ pixi-live2d     │                                                │
        └─────────────────┘                          ┌─────────────────────┼─────────────────────┐
                                                     │                     │                     │
                                             DeepSeek API          edge-tts / sherpa-onnx    Windows 工具
                                            （云端大模型）          （语音合成 / 识别）       （鼠标/键盘/屏幕/窗口）
```

```
server/
  app.py          FastAPI 入口：会话、对话循环、工具调用、TTS 队列、免提唤醒、记忆、状态、设置
  tools.py        26 个电脑操作工具（Win32 SendInput / 窗口 / 剪贴板 / 屏幕）
  screen.py       截图 + OCR（rapidocr-onnxruntime）
  stt.py          离线语音识别（sherpa-onnx streaming zipformer）
  sing.py         唱歌合成（edge-tts 逐字 + pyworld 变调）
  awareness.py    前台窗口 / 空闲检测
  persona.json    普通 yuki 人设
  persona_dark.json 黑 yuki 人设
  config.example.json 配置模板（复制为 config.json 后填 key）
web/
  index.html / pet.js / style.css   桌宠前端
  settings.html                      设置面板
main.js / preload.js                 Electron 桌面壳
launcher.cs                          yuki.exe 启动器源码（.NET Framework 编译）
```

## 快速开始（从源码）

### 0. 环境要求

- Windows 10 / 11 x64
- Python **3.11**
- Node.js（可选，用于安装 Electron；也可以直接用便携版 Electron）
- 一个 DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com)）

### 1. 克隆并安装依赖

```bat
git clone https://github.com/<your-name>/yuki.git
cd yuki

py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r server\requirements.txt

:: 可选：用 npm 安装 Electron
npm install
```

### 2. 准备资源

按 [docs/SETUP_ASSETS.md](docs/SETUP_ASSETS.md) 把这些文件放到对应位置（都**不随仓库提供**）：

| 需要 | 放到 |
|---|---|
| Electron 便携版（或 npm 安装） | `runtime/electron/`（或 `npm install`） |
| pixi.js / pixi-live2d-display / Live2D Cubism Core | `vendor/` |
| 你自己的 Live2D 模型（`.model3.json` 及贴图） | `models/<名字>/` |
| sherpa-onnx 中文流式识别模型 | `models/asr/` |

### 3. 配置

```bat
copy server\config.example.json server\config.json
```

然后二选一填 API Key：

- 直接编辑 `server/config.json` 的 `"api_key"`
- 或启动后打开设置面板（⚙）在界面里填

### 4. 启动

```bat
:: 用 npm 装的 Electron
npm start

:: 或用便携版 Electron
runtime\electron\electron.exe --no-sandbox .
```

> 以管理员身份运行 Windows 时 Chromium 沙箱会崩溃，所以启动参数里带了 `--no-sandbox`；本项目默认也是这么启动的。

### 5. 打包成便携版 / 单文件启动器（可选）

```bat
:: 把 Python / Electron 复制进 runtime/，模型放进 models/，密钥写进 server/config.json
build_launcher.bat
:: 生成 yuki.exe，双击即启动
```

## 配置说明（server/config.json）

| 键 | 说明 |
|---|---|
| `api_key` / `base_url` / `model` | 大模型配置（默认 DeepSeek，OpenAI 兼容接口） |
| `temperature` / `max_tokens` / `history_turns` | 对话参数 |
| `voice` / `rate` / `pitch` | edge-tts 音色 / 语速 / 音调 |
| `voice_enabled` | 语音输出总开关（**默认 false**，只出文字） |
| `tools_enabled` | 是否允许她操作电脑 |
| `awareness` / `proactive` | 知觉 / 主动搭话 |
| `screen_comment` / `screen_comment_cooldown` | 看屏幕文字自动吐槽 / 冷却秒数 |
| `summary_every` | 每多少条消息总结一次长期记忆 |
| `auto_greet` / `wake_autostart` | 自动打招呼 / 开机自动免提 |

人设可以直接编辑 `server/persona.json`（普通）和 `server/persona_dark.json`（黑化）。

## 数据与隐私

- 所有数据都在本机 `data/` 目录：
  - `nurow.db`：聊天记录（SQLite）
  - `memory.md`：长期记忆笔记
  - `yuki_state.json`：心情 / 好感 / 关系 / 互动次数
  - `logs/`、`screenshots/`、`songs/`
- 只有大模型请求和 edge-tts 合成会联网；其余（语音识别、OCR、记忆）全部离线
- 「看屏幕」「操作电脑」都是显式开启的能力，关闭后她不会截屏/动鼠标
- **不要把 `server/config.json` 提交到仓库**（已加入 `.gitignore`）

## 已知限制

- 屏幕理解只有「文字」层：纯画面（游戏、视频）她看不懂，只能读界面上的文字
- 语音是 edge-tts 合成音，不是真人音色；想要更像真人需要接本地声音克隆或云端 TTS
- 复杂 GUI 流程（找图标、看懂画面后点击）做不到，需要视觉模型
- 目前只支持 Windows

## 路线图

- [ ] 视觉模型接入（看懂画面 / 游戏解说）
- [ ] 声音克隆（GPT-SoVITS 等）或云端高自然度 TTS
- [ ] 打包安装器（而不是便携文件夹）
- [ ] 插件式人设 / 技能
- [ ] Linux / macOS 适配

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请见 [SECURITY.md](SECURITY.md)。

## 许可

代码以 **MIT** 发布（见 [LICENSE](LICENSE)）。

**注意：仓库不包含也不授权分发**任何 Live2D 模型、角色立绘、Live2D Cubism Core 或示例模型；这些资源各有其许可，请自行获取并遵守对应条款。如果使用他人创作的模型/音色，请先取得授权，不要公开传播。

---

## English

**yuki** is a Neuro-sama-inspired desktop AI companion for Windows: a Live2D character that chats (DeepSeek), listens (offline sherpa-onnx ASR), speaks (edge-tts), reads your screen as text (RapidOCR), and can genuinely operate your PC through 26 tools (mouse, keyboard, windows, clipboard, volume, timers, screenshots…). It keeps long-term local memory and a small "life state" (mood / affection) that survives restarts. Voice output is **off by default**; everything is local except the LLM and TTS calls.

No Live2D model, Cubism Core, or runtime binaries are shipped in this repository — see [docs/SETUP_ASSETS.md](docs/SETUP_ASSETS.md). Code is MIT licensed.
