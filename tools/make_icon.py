# -*- coding: utf-8 -*-
"""从动画里抽一帧生成程序图标（icon.png + icon.ico）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import av
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBM = os.path.join(ROOT, "assets", "webm")
OUT_PNG = os.path.join(ROOT, "assets", "icon.png")
OUT_ICO = os.path.join(ROOT, "assets", "icon.ico")


def grab(clip: str, index: int = 40) -> Image.Image:
    container = av.open(os.path.join(WEBM, f"{clip}.webm"))
    ctx = av.CodecContext.create("libvpx-vp9", "r")
    n = 0
    last = None
    for pkt in container.demux(container.streams.video[0]):
        for fr in ctx.decode(pkt):
            img = Image.fromarray(fr.to_ndarray(format="rgba"), "RGBA")
            last = img
            n += 1
            if n >= index:
                container.close()
                return img
    container.close()
    return last


def main() -> None:
    img = grab("idle", 30)
    if img is None:
        raise SystemExit("抽帧失败")
    bbox = img.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        raise SystemExit("找不到角色区域")
    x0, y0, x1, y1 = bbox
    # 取头部（上 62%），做成正方形并稍微上移，让脸居中
    side = int((y1 - y0) * 0.62)
    cx = (x0 + x1) // 2
    top = y0 - int(side * 0.02)
    crop = img.crop((cx - side // 2, top, cx - side // 2 + side, top + side))

    size = 256
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = int(size * 0.92)
    face = crop.resize((inner, inner), Image.LANCZOS)

    from PIL import ImageDraw
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
    pos = ((size - inner) // 2, (size - inner) // 2)
    # 白色描边 + 头像，用圆形蒙版裁切
    ring = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    rd.ellipse((pos[0] - 3, pos[1] - 3, pos[0] + inner + 2, pos[1] + inner + 2),
               fill=(255, 255, 255, 235))
    canvas.alpha_composite(ring)
    canvas.paste(face, pos, mask)

    canvas.save(OUT_PNG)
    canvas.save(OUT_ICO, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("icon ->", OUT_PNG, OUT_ICO)


if __name__ == "__main__":
    main()
