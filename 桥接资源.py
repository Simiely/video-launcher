"""画布加载功能的资源与构造逻辑（v1.2.18）。

`运行时` 把这里的函数当作本模块的一部分使用（安装桥接扩展 / 构造 I2V 画布）。
单独成模块只因运行时.py 的逻辑行预算吃满（>1000 必拆的铁律）；与界面无关，
只依赖标准库，不 import 业务代码。
"""

BRIDGE_INIT = '''"""视频方案启动器桥接扩展（v1.2.18）。

只做一件事：把启动器发来的「加载到画布」请求转给前端。
- GET /video_launcher/ping    → 存活探测（启动器用它判断桥是否已生效）
- GET /video_launcher/pending → 取走一条待加载请求（取走即删）
不注册节点、不执行任何图逻辑。
"""
import json
import os

from aiohttp import web
from server import PromptServer

_REQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending.json")
_ROUTES = PromptServer.instance.routes


async def _ping(request):
    return web.json_response({"ok": True, "name": "video-launcher-bridge"})


async def _pending(request):
    data = None
    try:
        with open(_REQ, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as e:
        print("[video-launcher-bridge] 读取 pending 失败：%r" % e)
        data = None
    if data is not None:
        try:
            os.remove(_REQ)
        except OSError as e:
            print("[video-launcher-bridge] pending 删除失败（下次取时再删）：%r" % e)
    return web.json_response({"workflow": data})


_ROUTES.get("/video_launcher/ping")(_ping)
_ROUTES.get("/video_launcher/pending")(_pending)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
'''

BRIDGE_JS = '''// 视频方案启动器桥接（前端侧）：轮询待加载请求，收到就把工作流载入画布（不执行）。
import { app } from "../../scripts/app.js";

app.registerExtension({
    name: "VideoLauncher.Bridge",
    setup() {
        const poll = async () => {
            try {
                const res = await fetch("/video_launcher/pending", { cache: "no-store" });
                if (res.ok) {
                    const j = await res.json();
                    if (j && j.workflow) {
                        await app.loadGraphData(j.workflow);
                        console.log("[video-launcher-bridge] 已载入画布：", j.name || "(未命名)");
                    }
                }
            } catch (err) {
                // 服务没起 / 桥还没生效：下一轮再试
            }
            setTimeout(poll, 800);
        };
        poll();
    },
});
'''


# 内置方案 → 画布版 UI 原件（相对 workflows_dir 的文件名）。宁缺毋滥：没有可靠
# 原件的就给 None，按钮会给出诚实提示，而不是猜一份错图。
WF_UI_SOURCES = {
    "fv_2x": "FlashVSR-放大2x-提速.json",
    "fv_4x": "FlashVSR-放大4x-画质优先.json",
    "sv_std": "SeedVR2-放大1080p-标准.json",
    "sv_max": None,        # 2160 是补绑时改的参数，没有现成 UI 原件
    "mm_t2v": "MiniMax-H3-文生视频T2V.json",
    "mm_i2v": "MiniMax-H3-文生视频T2V.json",   # 通用画布，载入时补 LoadImage 接 first_frame
}


def build_i2v_ui(data):
    """从 MiniMax 通用画布构造 I2V 变体：加一个 LoadImage 接进子图实例的 first_frame。

    那份画布是 T2V/I2V 通用的（first_frame 没接线 = 文生视频；接上 = 图生视频，
    内部 ComfySwitchNode 自动切）。构造失败返回 (None, 原因)，调用方降级处理。
    """
    import copy as _copy
    d = _copy.deepcopy(data)
    sg_ids = {s.get("id") for s in (d.get("definitions") or {}).get("subgraphs") or []}
    inst = next((n for n in d.get("nodes", []) if n.get("type") in sg_ids), None)
    if inst is None:
        return None, "画布里找不到 MiniMax 子图实例"
    slot = next((i for i, inp in enumerate(inst.get("inputs") or [])
                 if inp.get("name") == "first_frame"), None)
    if slot is None:
        return None, "子图实例没有 first_frame 输入"
    nid = max([n.get("id", 0) for n in d.get("nodes", [])] + [0]) + 1
    links = d.get("links")
    if isinstance(links, list):
        lid = max([l[0] for l in links if isinstance(l, (list, tuple))] + [0]) + 1
        links.append([lid, nid, 0, inst["id"], slot, "IMAGE"])
    elif isinstance(links, dict):
        lid = max([int(k) for k in links] + [0]) + 1
        links[str(lid)] = {"id": lid, "origin_id": nid, "origin_slot": 0,
                           "target_id": inst["id"], "target_slot": slot, "type": "IMAGE"}
    else:
        return None, "画布连线表格式不认识"
    pos = list(inst.get("pos") or [0, 0])
    d["nodes"].append({
        "id": nid, "type": "LoadImage", "pos": [pos[0] - 320, pos[1] + 40],
        "size": [280, 320], "flags": {}, "order": 0, "mode": 0, "inputs": [],
        "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [lid], "slot_index": 0},
                    {"name": "MASK", "type": "MASK", "links": [], "slot_index": 1}],
        "widgets_values": ["launcher_first_frame.png", "image"],
    })
    inst["inputs"][slot]["link"] = lid
    return d, ""
