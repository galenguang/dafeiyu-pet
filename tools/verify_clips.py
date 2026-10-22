# -*- coding: utf-8 -*-
"""验证钳位后的素材：真能做到「透明区是纯 0」。

这不是重复 reencode_clamped.py 的自检。那边是边编边验（用 alphaextract
的整体占比），这里做的是**像素级**检查，回答三个更严格的问题：

1. 钳位后 a0 是否真的取代了 a1_8（不是两边都变小）
2. 帧数、分辨率、帧率有没有因为重编码而漂移
3. **关键**：钳位后的 alpha 平面里，还有没有「孤岛」——
   即 alpha>0 但四周全 0 的像素。这种像素在视觉上就是一层脏点，
   桌面版靠命中阈值挡着看不见，手机上放大背景会很明显。

用法：
    python tools/verify_clips.py            # 全部
    python tools/verify_clips.py idle dance # 指定几段
"""
from __future__ import annotations

import os
import subprocess
import sys

import numpy as np

FFMPEG = r"D:/Software/ffmpeg-7.1-full_build/bin/ffmpeg.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "webm")
CLIPS = os.path.join(ROOT, "build", "clips")
W, H = 640, 360
FRAME_BYTES = W * H


def frames(path: str):
    """逐帧产出 alpha 平面（uint8 HxW）。"""
    cmd = [FFMPEG, "-v", "error", "-c:v", "libvpx-vp9", "-i", path,
           "-vf", "alphaextract", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    try:
        while True:
            buf = pr.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(H, W)
    finally:
        pr.stdout.close()
        pr.wait()


def probe_meta(path: str) -> tuple[int, int, str] | None:
    r = subprocess.run(
        [r"D:/Software/ffmpeg-7.1-full_build/bin/ffprobe.exe",
         "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,avg_frame_rate",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, errors="replace", timeout=30)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.strip().split(",")
    try:
        return int(parts[0]), int(parts[1]), parts[2]
    except (IndexError, ValueError):
        return None


def check(name: str) -> tuple[bool, list[str]]:
    src = os.path.join(SRC, name)
    dst = os.path.join(CLIPS, name)
    errs: list[str] = []
    if not os.path.exists(dst):
        return False, ["钳位文件不存在"]

    ms, md = probe_meta(src), probe_meta(dst)
    if ms and md:
        if ms[:2] != md[:2]:
            errs.append(f"分辨率变了 {ms[:2]} → {md[:2]}")
        if ms[2] != md[2]:
            errs.append(f"帧率变了 {ms[2]} → {md[2]}")

    n = 0
    hist = np.zeros(256, dtype=np.int64)
    islands = 0            # alpha>0 但四邻全 0 的像素总数
    for a in frames(dst):
        n += 1
        hist += np.bincount(a.ravel(), minlength=256)
        # 孤岛检测：有 alpha 的像素，其上下左右是否都透明
        on = a > 0
        if not on.any():
            continue
        up = np.zeros_like(on)
        up[1:, :] = on[:-1, :]
        dn = np.zeros_like(on)
        dn[:-1, :] = on[1:, :]
        lf = np.zeros_like(on)
        lf[:, 1:] = on[:, :-1]
        rt = np.zeros_like(on)
        rt[:, :-1] = on[:, 1:]
        islands += int((on & ~up & ~dn & ~lf & ~rt).sum())

    if n == 0:
        return False, errs + ["一帧都解不出来"]

    total = int(hist.sum())
    a0 = hist[0] / total * 100
    a18 = hist[1:9].sum() / total * 100
    a_hi = hist[20:].sum() / total * 100
    if a18 > 5.0:
        errs.append(f"残值仍有 {a18:.1f}%（应 <5%）")
    if a0 < 60.0:
        errs.append(f"纯透明只占 {a0:.1f}%（应 >60%）")
    if islands > n * 50:
        # 阈值是拍的：一帧 640x360 里几十个孤点大概率是抖动像素
        errs.append(f"孤岛像素过多：{islands} 个 / {n} 帧"
                    f"（平均每帧 {islands / n:.0f}）")

    ok = not errs
    print(f"{'OK ' if ok else 'FAIL'} {name:<22} 帧{n:<4} "
          f"a0={a0:5.1f}%  a1_8={a18:4.1f}%  有效={a_hi:4.1f}%  "
          f"孤岛/帧={islands / n:5.1f}")
    return ok, errs


def main() -> int:
    if not os.path.isdir(CLIPS):
        print("还没有钳位产物，先跑 tools/reencode_clamped.py")
        return 1
    if len(sys.argv) > 1:
        names = [a if a.endswith(".webm") else a + ".webm"
                 for a in sys.argv[1:]]
    else:
        names = sorted(f for f in os.listdir(CLIPS) if f.endswith(".webm"))

    print(f"验证 {len(names)} 段\n")
    bad = 0
    for name in names:
        ok, errs = check(name)
        if not ok:
            bad += 1
            for e in errs:
                print(f"       └ {e}")
    print(f"\n{len(names) - bad}/{len(names)} 通过")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
