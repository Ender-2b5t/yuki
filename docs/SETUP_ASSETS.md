# 资源准备（不随仓库分发）

本仓库只包含**代码**。以下资源体积大或另有许可，需要你自己准备：

| 资源 | 放到 | 说明 |
|---|---|---|
| Electron 便携版（v33.2.0） | `runtime/electron/` | 或用 `npm install` 安装 Electron |
| pixi.js 6.5.10 | `vendor/pixi.min.js` | MIT |
| pixi-live2d-display 0.4.0 | `vendor/cubism4.min.js` | MIT |
| Live2D Cubism Core 5.1 | `vendor/live2dcubismcore.min.js` | **Live2D 专有许可**，需自行下载，不可再分发 |
| 你自己的 Live2D 模型 | `models/<名字>/` | 模型和立绘各有许可，请使用你有权使用的模型 |
| sherpa-onnx 中文流式识别模型 | `models/asr/` | Apache-2.0，KK 的模型 |

## 1. Electron

- 官方发布页：https://github.com/electron/electron/releases/tag/v33.2.0
- 下载 `electron-v33.2.0-win32-x64.zip`，解压后把里面的文件放到 `runtime/electron/`（`electron.exe` 应位于 `runtime/electron/electron.exe`）

参考 SHA-256（`runtime/electron/electron.exe`）：
`18FEEFA1ECEB947D94165504B9B5EC509EC4D5F38F4F65CB632EF58D6FBA8101`

或者（需要 Node.js）：

```bat
npm install
npm start
```

## 2. 前端库（vendor/）

需要三个文件，`index.html` 会按顺序加载：

```html
<script src="/vendor/pixi.min.js"></script>
<script src="/vendor/live2dcubismcore.min.js"></script>
<script src="/vendor/cubism4.min.js"></script>
```

| 文件 | 来源 | 说明 |
|---|---|---|
| `pixi.min.js` | https://registry.npmjs.org/pixi.js/-/pixi.js-6.5.10.tgz → `package/dist/browser/pixi.min.js` | 460,333 字节，UMD 全局 `PIXI` |
| `cubism4.min.js` | https://registry.npmjs.org/pixi-live2d-display/-/pixi-live2d-display-0.4.0.tgz → `package/dist/cubism4.min.js` | 119,922 字节，UMD 全局 `PIXI.live2d` |
| `live2dcubismcore.min.js` | https://www.live2d.com/en/sdk/download/web/ （Cubism SDK for Web 中的 Cubism Core） | **专有许可**，请按 Live2D 条款自行下载 |

参考 SHA-256：

- `pixi.min.js`: `403F2F2EE8145FA17F60C5C89403056EFE2680E5096EC2762036486914ED19C5`
- `cubism4.min.js`: `AF1267E6D52759B245766C578D905BFA025B532D5C3CC727C370957C4409E21B`
- `live2dcubismcore.min.js`: `25AE938CB4FE282CE189B357BCC97E603D1E1F7EC78BF04150D401C23CDC792F`

## 3. Live2D 模型

在 `models/` 下放一个文件夹，里面至少包含：

```
models/<名字>/
  <名字>.model3.json
  <名字>.moc3
  textures/*.png
  physics3.json / cdi3.json / userdata3.json（可选）
  expressions/*.exp3.json（可选）
  motions/*.motion3.json（可选）
```

然后编辑 `models/current.json` 指向它（也可以在设置面板里选）：

```json
{
  "model": "<名字>/<名字>.model3.json",
  "scale": 0.96,
  "offsetX": 0.0,
  "offsetY": -0.005
}
```

> 模型（尤其是别人画的角色/买的模型）请先确认许可，不要公开再分发。

## 4. 离线语音识别模型

任选一个中文流式 zipformer，例如
`sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23`：

- 模型列表：https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models
- 下载 `sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23.tar.bz2`，解压后把包含
  `tokens.txt` 和 `*.onnx`（encoder / decoder / joiner）的目录放到 `models/asr/` 下面，例如：

```
models/asr/sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23/
  tokens.txt
  encoder-epoch-99-avg-1.onnx
  decoder-epoch-99-avg-1.onnx
  joiner-epoch-99-avg-1.onnx
```

`server/stt.py` 会自动在 `models/asr/` 下查找模型。

## 5. 检查资源是否齐全

```bat
dir runtime\electron\electron.exe
dir vendor\pixi.min.js vendor\live2dcubismcore.min.js vendor\cubism4.min.js
dir models\asr
```

都齐了之后：`copy server\config.example.json server\config.json`，填好 `api_key`，再启动。
