# video-launcher · 视频方案启动器

> 一个窗口管住本地三套视频 AI 方案：**拉起 ComfyUI 服务 → 选输入 → 开跑**。
> 纯 Python 标准库实现（Tkinter，深色 UI 全自绘），零第三方依赖；源码可直接运行，也可打包成单文件 exe。

## 它解决什么

本地部署的视频放大 / 生成方案分散在多个目录、十几个 `.ps1` 里，每次都要手开终端、记参数、盯日志。本启动器把「启动服务 + 挑输入 + 传参 + 实时看日志」收进一个 GUI：

| 能力 | 干什么 | 背后调用 |
|---|---|---|
| FlashVSR 放大 | 视频放大（快） | `video-upscale-deploy/02-FlashVSR/批量放大.ps1` |
| SeedVR2 放大 | 视频放大（画质优先，音轨自动回接） | `video-upscale-deploy/03-SeedVR2/批量放大.ps1` |
| MiniMax H3 生成 | 文生视频 + 音画同出 | ComfyUI 服务 + 网页工作流（参数在网页里调） |

## 界面（v1.2.0 深色三栏控制台）

```
┌──────────┬────────────────────────────────────┬──────────────┐
│ 左导航    │ 中内容                              │ 右日志        │
│ 服务      │  ● ComfyUI 服务  运行中  URL  ▸按钮  │ 运行日志      │
│ 工作流 6  │  工作流卡（下拉 / 加载 .json / 详情） │ （着色输出）  │
│ FlashVSR │  引擎标签：FlashVSR│SeedVR2│MiniMax H3 │              │
│ SeedVR2  │  引擎参数卡（点标签切换）            │  ─────────   │
│ MiniMax  │  设置卡（路径只读 + 打开目录）       │  任务进度 65% │
│ 设置      │                                     │              │
└──────────┴────────────────────────────────────┴──────────────┘
```

- 配色：背景 `#15171c` / 卡片 `#232730` / 蓝 `#4a9eff` / 紫 `#b389f0` / 青 `#34d3b0`，圆角 12px 自绘卡片与柔和阴影
- 设计蓝本见分支 `ui-redesign` 的 `redesign/video_launcher_sidebar.html`
- 服务状态（圆点 / 「运行中」徽标 / 版本 / 显存）每 5 秒刷新；服务未运行时显存退回 `nvidia-smi` 读显卡
- 日志按类别着色（成功绿 / 工作流青 / 警告琥珀），并从输出里解析 `x/y` 或 `n%` 驱动右下进度条

## 环境要求

- Windows + Python **3.12+**（本机实测 3.13 / 3.14 打包）
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

## 源码结构（v1.2.0 起为两文件）

| 文件 | 说明 |
|---|---|
| `视频方案启动器.py` | 配置 + 业务逻辑 + 界面装配（PyInstaller 入口） |
| `ui_kit.py` | 深色自绘控件库 + 设计令牌（不 import 业务代码） |

> 按 `AGENTS.md`「单文件交付，超阈值才拆分」：主程序逻辑行超过 pylint 默认上限（1000）时才抽出 `ui_kit.py`。

## 快速开始

```bash
python 视频方案启动器.py
```

1. 点顶栏「启动服务」，等状态变 **● 运行中**（首次加载约 15~60 秒，就绪后自动打开网页）
2. 点左导航到对应引擎（或直接点引擎标签），选输入（支持单个文件或整个文件夹）、设参数、点「运行放大」
3. 日志栏实时回传子进程输出，结束时打印 `rc` 与耗时，右下进度条同步

参考实测值（RTX 4070 Ti SUPER 16G）：

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

产物 `dist/VideoLauncher.exe`（单文件、无控制台窗口、UPX 压缩，约 13 MB；会跟随 `import ui_kit` 自动打包）。
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
