# -*- coding: utf-8 -*-
# Generates two dark-themed, flat card-based UI mockups for the video-launcher redesign.
FONT = "'Segoe UI', system-ui, -apple-system, sans-serif"

BG     = "#15171c"
CARD   = "#232730"
CARD2  = "#2b303b"
BORDER = "#353b47"
TXT    = "#eef1f5"
TXT2   = "#9aa3b2"
BLUE   = "#4a9eff"
PURP   = "#b389f0"
TEAL   = "#34d3b0"
GREEN  = "#3fb950"
GRAY   = "#8b8b94"
AMBER  = "#e3b341"
RED    = "#f85149"
DARKTX = "#0b1220"

def R(x,y,w,h,fill,rx=12,stroke=None,sw=1):
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"'
    if stroke: s += f' stroke="{stroke}" stroke-width="{sw}"'
    return s + '/>'

def T(x,y,t,size=13,color=TXT,weight=400,anchor="start",family=FONT):
    esc = (str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;"))
    return f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{esc}</text>'

def line(x1,y1,x2,y2,color=BORDER,sw=1,dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"{d}/>'

def circle(cx,cy,r,fill,stroke=None):
    s = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"'
    if stroke: s += f' stroke="{stroke}" stroke-width="1"'
    return s + '/>'

def btn(x,y,w,h,t,fill,color=DARKTX,rx=8,size=13,stroke=None):
    s = R(x,y,w,h,fill,rx,stroke)
    s += T(x+w/2, y+h/2+5, t, size, color, 500, "middle")
    return s

def pill(x,y,w,h,t,fill,color):
    return R(x,y,w,h,fill,rx=h//2) + T(x+w/2, y+h/2+4, t, 12, color, 500, "middle")

def chip(x,y,w,h,t,fill,color,rx=6,size=12):
    return R(x,y,w,h,fill,rx) + T(x+w/2, y+h/2+4, t, size, color, 500, "middle")

def check(x,y,color):
    return line(x,y+3,x+3,y+6,color,2) + line(x+3,y+6,x+9,y-2,color,2)

# ============================================================ LAYOUT A: card grid + status strip
def layout_a():
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 660" width="720" height="660" role="img">')
    p.append('<title>卡片网格 + 顶部状态条方案（主推）</title>')
    p.append('<desc>去掉笔记本分页，改为顶部常驻 ComfyUI 状态条 + 三个工具卡片网格 + 输入/输出与日志卡片</desc>')
    p.append(R(0,0,720,660,BG,0))
    # header
    p.append(T(28,40,"视频方案启动器",18,TXT,500))
    p.append(T(28,60,"本地视频放大 · ComfyUI 控制台",12,TXT2))
    p.append(btn(636,22,64,30,"设置",CARD2,TXT,rx=8,stroke=BORDER))
    # status strip
    p.append(R(20,76,680,60,CARD))
    p.append(circle(44,106,7,GREEN))
    p.append(T(60,111,"ComfyUI 服务",14,TXT,500))
    p.append(pill(150,94,84,24,"运行中","#173a25",GREEN))
    p.append(T(262,111,"http://127.0.0.1:8188",13,TXT2))
    p.append(R(600,90,86,32,CARD2,8,BORDER))
    p.append(T(643,110,"停止服务",13,TXT,500,"middle"))
    # tool cards
    cx = [20,250,480]; y0=148; w=218; h=258
    cards = [
        ("FlashVSR 放大","2x / 4x",BLUE,
         [("模式","通用 · 动画",None),
          ("放大倍数",None,[("2x",BLUE),("4x",None)])],
         "服务没跑就自动拉起","运行放大",BLUE),
        ("SeedVR2 放大","短边自适应",PURP,
         [("档位","标准 / 高质量 / 极致",None),
          ("短边分辨率",None,[("720",None),("1080",None),("原画",None)])],
         "服务没跑就自动拉起","运行放大",PURP),
        ("MiniMax H3 生成","网页工作流",TEAL,
         [("说明","在网页 ComfyUI 中搭工作流：",None),
          ("说明2","Video -> MiniMax H3 -> T2V",None),
          ("说明3","启动器只负责拉起服务与网页。",None)],
         None,"打开 ComfyUI 网页",TEAL),
    ]
    for i,(title,tag,col,rows,chk,btnt,btc) in enumerate(cards):
        x = cx[i]
        p.append(R(x,y0,w,h,CARD))
        p.append(circle(x+34,y0+40,16,col))
        p.append(T(x+34,y0+45,title[0],14,DARKTX,600,"middle"))
        p.append(T(x+60,y0+46,title,14,TXT,500))
        p.append(chip(x+w-70,y0+26,58,22,tag,CARD2,col))
        p.append(line(x+16,y0+66,x+w-16,y0+66,BORDER))
        yy = y0+92
        for r in rows:
            label = r[0]; sel = r[1]; chips = r[2]
            if label.startswith("说明"):
                p.append(T(x+20,yy,sel,12,TXT2)); yy += 22; continue
            p.append(T(x+20,yy,label,12,TXT2)); yy += 10
            if sel is not None:
                p.append(R(x+20,yy,w-40,30,CARD2,6,BORDER))
                p.append(T(x+32,yy+20,sel,13,TXT))
                yy += 44
            if chips is not None:
                cw = (w-40- (len(chips)-1)*8)//len(chips)
                for j,(ct,cf) in enumerate(chips):
                    cx2 = x+20 + j*(cw+8)
                    if cf is None:
                        p.append(chip(cx2,yy,cw,30,ct,CARD2,TXT))
                    else:
                        p.append(chip(cx2,yy,cw,30,ct,cf,DARKTX))
                yy += 44
        if chk:
            p.append(R(x+20,yy+4,16,16,CARD2,4,BORDER))
            p.append(check(x+22,yy+6,BLUE))
            p.append(T(x+44,yy+16,chk,12,TXT2))
            yy += 24
        p.append(btn(x+20,yy+6,w-40,30,btnt,btc))
    # bottom row
    bx0=20; by0=422; bw=334; bh=192
    p.append(R(bx0,by0,bw,bh,CARD))
    p.append(T(bx0+24,by0+34,"输入 / 输出",14,TXT,500))
    p.append(T(bx0+24,by0+62,"输入目录",12,TXT2))
    p.append(R(bx0+24,by0+70,250,32,CARD2,6,BORDER))
    p.append(T(bx0+36,by0+90,"E:\\VideoUpscale\\input",12,TXT2))
    p.append(R(bx0+280,by0+70,30,32,CARD2,6,BORDER))
    p.append(T(bx0+295,by0+90,"浏览",12,TXT,500,"middle"))
    p.append(T(bx0+24,by0+120,"输出目录",12,TXT2))
    p.append(R(bx0+24,by0+128,250,32,CARD2,6,BORDER))
    p.append(T(bx0+36,by0+148,"E:\\VideoUpscale\\output",12,TXT2))
    p.append(R(bx0+280,by0+128,30,32,CARD2,6,BORDER))
    p.append(T(bx0+295,by0+148,"浏览",12,TXT,500,"middle"))
    p.append(btn(bx0+24,by0+168,124,26,"打开输出目录",CARD2,TXT,rx=6,stroke=BORDER))

    rx0=366; ry0=422; rw=334; rh=192
    p.append(R(rx0,ry0,rw,rh,CARD))
    p.append(T(rx0+24,ry0+34,"运行日志",14,TXT,500))
    p.append(T(rx0+rw-24,ry0+34,"清空",12,TXT2,400,"end"))
    logs = ["[服务] ComfyUI 已就绪 :8188","[FlashVSR] 批量放大 2x 开始…",
            "[FlashVSR] 处理 3/12 clip_04.mp4","[FlashVSR] 完成 12/12"]
    ly = ry0+66
    for lg in logs:
        p.append(T(rx0+24,ly,lg,12,TXT2)); ly += 22
    p.append(R(rx0+24,ry0+156,rw-48,8,CARD2,4))
    p.append(R(rx0+24,ry0+156,int((rw-48)*0.65),8,BLUE,4))
    p.append(T(rx0+rw-24,ry0+150,"65%",12,TXT,500,"end"))
    p.append('</svg>')
    return "\n".join(p)

# ============================================================ LAYOUT B: Fluent sidebar control deck
def layout_b():
    p = []
    p.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 720 600" width="720" height="600" role="img">')
    p.append('<title>Fluent 侧栏控制台方案（备选）</title>')
    p.append('<desc>左侧 Fluent 风格侧栏导航，右侧英雄服务卡 + 当前工具卡片 + 日志卡</desc>')
    p.append(R(0,0,720,600,BG,0))
    # sidebar
    p.append(R(20,20,170,560,CARD,12))
    p.append(R(36,36,40,40,BLUE,10))
    p.append(T(56,62,"V",18,DARKTX,600,"middle"))
    p.append(T(36,96,"视频启动器",13,TXT,500))
    items = [("服务",BLUE,True),("FlashVSR",BLUE,False),("SeedVR2",PURP,False),("MiniMax",TEAL,False)]
    iy = 132
    for name,col,active in items:
        if active:
            p.append(R(28,iy,154,40,CARD2,8))
            p.append(R(28,iy,4,40,BLUE,2))
        p.append(R(40,iy+8,24,24,col,6))
        p.append(T(74,iy+26,name,13,TXT if active else TXT2,500 if active else 400))
        iy += 48
    p.append(line(36,330,174,330,BORDER))
    for name in ["设置","日志"]:
        p.append(R(40,iy+8,24,24,CARD2,6,BORDER))
        p.append(T(74,iy+26,name,13,TXT2))
        iy += 48
    # content: hero service
    cx0=210; cw=490
    p.append(R(cx0,20,cw,84,CARD))
    p.append(circle(cx0+24,62,7,GREEN))
    p.append(T(cx0+40,67,"ComfyUI 服务",14,TXT,500))
    p.append(pill(cx0+150,50,84,24,"运行中","#173a25",GREEN))
    p.append(T(cx0+150,100,"http://127.0.0.1:8188",12,TXT2))
    p.append(R(cx0+cw-106,54,86,32,CARD2,8,BORDER))
    p.append(T(cx0+cw-63,74,"停止服务",13,TXT,500,"middle"))
    # tool card (FlashVSR active)
    p.append(R(cx0,120,cw,330,CARD))
    p.append(circle(cx0+34,160,16,BLUE))
    p.append(T(cx0+34,165,"F",14,DARKTX,600,"middle"))
    p.append(T(cx0+60,166,"① FlashVSR 放大",14,TXT,500))
    p.append(chip(cx0+cw-90,144,74,22,"2x / 4x",CARD2,BLUE))
    p.append(line(cx0+16,188,cx0+cw-16,188,BORDER))
    # left column
    lx=cx0+24
    p.append(T(lx,222,"模式",12,TXT2))
    p.append(R(lx,230,210,32,CARD2,6,BORDER))
    p.append(T(lx+12,251,"通用 · 动画",13,TXT))
    p.append(T(lx,292,"放大倍数",12,TXT2))
    p.append(chip(lx,300,100,32,"2x",BLUE,DARKTX))
    p.append(chip(lx+108,300,104,32,"4x",CARD2,TXT))
    # right column
    rx=cx0+270
    p.append(R(rx,222,16,16,CARD2,4,BORDER))
    p.append(check(rx+2,224,BLUE))
    p.append(T(rx+26,234,"服务没跑就自动拉起",12,TXT2))
    p.append(T(rx,276,"提示",12,TXT2))
    p.append(T(rx,298,"批量放大.ps1 会被调用，",12,TXT2))
    p.append(T(rx,316,"输出写入输出目录。",12,TXT2))
    p.append(btn(rx,344,210,34,"运行放大",BLUE))
    # log card bottom
    p.append(R(cx0,466,cw,114,CARD))
    p.append(T(cx0+24,500,"运行日志",14,TXT,500))
    p.append(T(cx0+cw-24,500,"清空",12,TXT2,400,"end"))
    for k,lg in enumerate(["[服务] ComfyUI 已就绪 :8188","[FlashVSR] 处理 3/12 clip_04.mp4"]):
        p.append(T(cx0+24,524+k*22,lg,12,TXT2))
    p.append(R(cx0+24,560,cw-48,8,CARD2,4))
    p.append(R(cx0+24,560,int((cw-48)*0.4),8,BLUE,4))
    p.append('</svg>')
    return "\n".join(p)

out = "D:/workbuddy/2026-10-09-17-30-19/video-launcher-redesign"
open(out+"/video_launcher_cardgrid.svg","w",encoding="utf-8").write(layout_a())
open(out+"/video_launcher_sidebar.svg","w",encoding="utf-8").write(layout_b())
print("A bytes:", len(layout_a()))
print("B bytes:", len(layout_b()))
print("written to", out)
