# 视频方案启动器 · UI 重设计（交接文档 / Handoff）

> 本目录 `redesign/` 是「视频方案启动器」的 **UI 重设计调研 + 可交互示意**，**尚未集成进主程序**。
> 接手人请在读完本文后再动手实现。

---

## 1. 背景与目标

- 原仓库：`Simiely/video-launcher`（默认分支 `main`）。
- 原主程序：`视频方案启动器.py`（tkinter GUI），作用是**一键拉起本地 ComfyUI 服务**，并驱动
  **FlashVSR / SeedVR2 视频放大**与 **MiniMax H3 生成**三个能力。
- 用户反馈：原界面不够好，要求更**卡片化**的设计。
- 本阶段交付：**设计调研结论 + 可交互 HTML 示意 + 实测对齐截图**，作为后续重写的蓝本。

---

## 2. 已确认的设计方向（最重要，别再改方向）

采用 **三栏控制台** 布局：**左导航 / 中内容 / 右日志**。

- **左导航**（固定 6 项，点击在中间滚动定位；引擎项同时切 tab）：
  服务 / 工作流 / FlashVSR / SeedVR2 / MiniMax H3 / 设置
- **中间**（纵向，核心）：
  1. **ComfyUI 服务状态卡（置顶，不随下方滚动）**：绿点 + 名称 + 运行中徽标 + URL + 重启/打开网页/停止按钮
  2. **工作流加载卡**：下拉选 6 套内置工作流 + 「加载 .json」按钮（选本地 ComfyUI 工作流，解析显示节点数 → 推送/入队）
  3. **引擎标签切换单卡**：FlashVSR / SeedVR2 / MiniMax H3 三个标签，**点击才显示、默认 FlashVSR**
  4. **设置卡**
- **右日志**（常驻栏，含进度条）。
- **视觉**：深色 Fluent 风 —— 背景 `#15171c`、卡片 `#232730`、强调蓝 `#4a9eff` / 紫 `#b389f0` / 青 `#34d3b0`，
  圆角 12px，柔和双层阴影。**颜色只用于表状态 / 区分引擎**，不滥用。
- **关键交互细节**（已实测）：顶栏/标签/徽标/按钮全部 `white-space:nowrap`，否则中文会断行错位；
  服务卡用固定组 + URL `flex:1 + ellipsis`，窄窗口先缩 URL 不挤按钮。

---

## 3. 文件清单（本目录）

| 文件 | 说明 |
|---|---|
| `video_launcher_sidebar.html` | **⭐ 最终采用的主示意（三栏版）**，浏览器直接打开即可交互（切标签/选工作流/加载 .json） |
| `video_launcher_cardgrid.html` / `.svg` | 早期「竖向卡片网格」示意，仅供参考，非最终方向 |
| `video_launcher_sidebar.svg` | 早期三栏静态示意，仅供参考 |
| `gen_mockups.py` | 生成两张 SVG 的脚本 |
| `shot.js` / `shot2.js` | 用 playwright-core + 本机 Edge 无头渲染截图脚本（用于验证对齐） |
| `shot_before.png` | 修复**前**的错位截图（顶栏按钮/标题/徽标文字断行）——对比用 |
| `shot_after.png` | 修复**后**的实测截图（已对齐） |
| `shot_wf_detail.png` | 选工作流后详情面板的实测截图（已对齐） |
| `README.md` | 本文件 |

---

## 4. 关键约束与已知事实（接手前必读）

- **桌面路径是 `E:\desktop`**（不是 `C:\Users\2504\Desktop`）。已交付的 exe 在 `E:\desktop\VideoLauncher.exe`。
- 程序从 **exe 同目录读取 `启动器配置.json`**，需要填的路径：
  `comfy_dir` / `comfy_py` / `deploy_root` / `ffmpeg_dir` / `input_dir` / `output_dir`。
- 启动器**只负责拉起 ComfyUI + 调用放大/生成的 `.ps1` 脚本**，**不含 ComfyUI 本体**；
  需本机已装 ComfyUI 与 `video-upscale-deploy` 部署包（`02-FlashVSR/批量放大.ps1`、`03-SeedVR2/批量放大.ps1` 等）。
- **打包环境**：系统 Python **3.14.6**（自带 Tcl/Tk 8.6）+ PyInstaller 6.22.3；
  ⚠️ **托管 Python 3.13.12 不带 tkinter，不能用来打包 GUI**。打包用的独立 venv 在
  `D:\workbuddy\2026-10-09-17-30-19\buildvenv\`（此为会话工作目录，非仓库内容）。
- 原仓库默认分支 `main`；本设计在分支 **`ui-redesign`**。
- 原程序全为标准库（tkinter 等），无第三方 Python 依赖。

---

## 5. 下一步任务清单（TODO，给接手人）

1. **定 GUI 框架（先定再做）**：当前示意是 HTML，真实程序是 tkinter。落地选项——
   - ① tkinter 自绘卡片（最贴合现有代码，圆角/阴影需用 Frame+边框近似）
   - ② ttkbootstrap（自带卡片感主题，改动小）
   - ③ PyQt6 / PySide6（最地道，改动最大）
   - ④ 直接嵌 webview（HTML 示意可复用，但打包体积大）
   - 建议：若想最小改动接回原逻辑 → ②；若想最贴合示意 → ④。
2. 按 `video_launcher_sidebar.html` 的结构把界面**重写为真实控件**。
3. 接回原逻辑：服务拉起/停止、工作流下拉 + 加载 .json（解析节点数）、三个引擎参数、日志输出。
4. 保留配置读取（`启动器配置.json`）与 exe 打包流程。
5. 本地验证（参照原打包 venv）后重新出 exe。

---

## 6. 如何本地预览 / 验证

- **预览示意**：浏览器打开 `redesign/video_launcher_sidebar.html`（可点标签 / 选工作流 / 加载 .json 交互）。
- **复现截图**：`node shot.js <html路径> <输出png>`
  （需本机有 `playwright-core` 与 Edge：`NODE_PATH` 指向 `.../node_modules`，
  `executablePath` 用本机 Edge 路径，如 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`）。
- ⚠️ 这些示意是**设计稿，不是可运行程序**；真实功能仍需回到 `视频方案启动器.py` 实现。

---

## 7. 交接状态

| 阶段 | 状态 |
|---|---|
| 设计调研（卡片化方案对比） | ✅ 100% |
| 可交互示意（三栏版） | ✅ 100% |
| 错位修复 + 实测截图验证 | ✅ 100% |
| 代码实现（落地到 视频方案启动器.py） | ⬜ 0% 未开始 |

> 本分支 `ui-redesign` **仅含设计稿**，请勿合并进 `main` 直到实现完成。
