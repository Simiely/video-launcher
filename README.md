# video-launcher · 视频方案启动器

> 一个窗口管住本地三套视频 AI 方案：**拉起 ComfyUI 服务 → 选输入 → 开跑**。
> 纯 Python 标准库实现（Tkinter），零第三方依赖；单文件源码，可直接运行，也可打包成单文件 exe。

## 它解决什么

本地部署的视频放大 / 生成方案分散在多个目录、十几个 `.ps1` 里，每次都要手开终端、记参数、盯日志。本启动器把「启动服务 + 挑输入 + 传参 + 实时看日志」收进一个 GUI：

| 页签 | 干什么 | 背后调用 |
|---|---|---|
| ① FlashVSR 放大 | 视频放大（快） | `video-upscale-deploy/02-FlashVSR/批量放大.ps1` |
| ② SeedVR2 放大 | 视频放大（画质优先，音轨自动回接） | `video-upscale-deploy/03-SeedVR2/批量放大.ps1` |
| ③ MiniMax H3 生成 | 文生视频 + 音画同出 | ComfyUI 服务 + 网页工作流（参数在网页里调） |

顶部状态条常驻显示：ComfyUI 服务是否在跑、版本号、显存占用（每 5 秒刷新；服务未运行则退回 `nvidia-smi` 读显卡）。

## 环境要求

- Windows + Python **3.12+**（本机实测 3.13）
- **零第三方依赖**：只用标准库（`tkinter` / `subprocess` / `threading` / `queue` / `urllib`）
- 需要已有一套本地部署（默认路径见下表），或用 `启动器配置.json` 指到你自己的路径

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `comfy_dir` | `C:\AI\ComfyUI` | ComfyUI 底座目录 |
| `comfy_py` | `C:\AI\ComfyUI\.venv\Scripts\python.exe` | 底座使用的解释器 |
| `deploy_root` | `C:\AI\video-upscale-deploy` | 批量放大脚本所在仓库 |
| `ffmpeg_dir` | `C:\AI\ffmpeg\bin` | 显式前置到子进程 PATH，不依赖注册表 |
| `input_dir` | `E:\VideoUpscale\input` | 素材目录 |
| `output_dir` | `E:\VideoUpscale\output` | 成品目录 |
| `comfy_url` | `http://127.0.0.1:8188` | ComfyUI 服务地址 |

## 快速开始

```bash
python 视频方案启动器.py
```

1. 点「启动服务」，等状态条变 **● 运行中**（首次加载约 15~60 秒，就绪后自动打开网页）
2. 切到对应页签，选输入（支持单个文件或整个文件夹）、设参数、点「开跑」
3. 日志区实时回传子进程输出，结束时打印 `rc` 与耗时

页签上的实测参考值（RTX 4070 Ti SUPER 16G）：

- FlashVSR：854×480 → 3416×1920（4x），2 分 39 秒
- SeedVR2：480p → 1080p（48 帧），约 23 分钟，音轨自动回接

## 覆盖默认路径

把 `启动器配置.example.json` 复制为 **`启动器配置.json`**，放在**程序同目录**（exe 则放 exe 旁边），只写需要改的键即可：

```json
{ "deploy_root": "D:\\my\\video-upscale-deploy" }
```

## 打包

```bash
pyinstaller VideoLauncher.spec
```

产物 `dist/VideoLauncher.exe`（单文件、无控制台窗口、UPX 压缩，约 11 MB）。
**打包产物不入库，走 Releases 发行。**

## 相关仓库

- [`video-upscale-deploy`](https://github.com/Simiely/video-upscale-deploy) —— Video2X / FlashVSR / SeedVR2 三套部署包（本启动器调用其中 02 / 03）
- [`minimax-h3-local-deploy`](https://github.com/Simiely/minimax-h3-local-deploy) —— MiniMax H3 本地部署说明
- 索引：[`design-tools`](https://github.com/Simiely/design-tools)

## 文档

| 文档 | 给谁看 |
|---|---|
| 本文 | 安装 / 使用 |
| [`DEVELOPMENT.md`](./DEVELOPMENT.md) | 架构说明 + 关键问题与方案（一坑一篇） |
| [`AGENTS.md`](./AGENTS.md) | 项目规则（给 AI / 未来的自己） |
| [`CHANGELOG.md`](./CHANGELOG.md) | 版本记录 |
