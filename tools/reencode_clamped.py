# -*- coding: utf-8 -*-
"""把 100 段素材重编一遍，只做一件事：把 alpha 钳干净。

为什么必须做：
实测全量 100 段，透明区**根本没有 alpha=0**——
  a0（纯透明）     0.58%
  a1_8（残值）    81.54%
  a251_255（实心） 16.55%
所谓「透明背景」解出来是 alpha 1-8 的灰雾，铺满 100% 画布。
桌面版靠 `alpha > 20` 的命中阈值挡住了交互问题，但视觉上那层雾还在。
手机上背景更亮、屏幕更小，这层雾会变成一块淡淡的矩形，必须修。

钳位表达式：alpha < 20 → 0，其余原样。这是有损压缩的产物，
不是素材本身的设计，钳掉不损失任何有效信息。

编码参数怎么定的（实测 idle / fireworks / dance 三段扫描）：
  crf36 → 118%    crf40 → 100%    crf44 → 80%    crf38+best → 100%
原素材本身就编在 crf≈40 这一档，所以 **crf40 体积持平、画质几乎无损**。
选 44 能省 20%，但那是拿画质换的，一个 640x360 的宠物动画不值当。

产物：
    build/clips/*.webm      钳位后的素材（原文件不动）
    build/report/reencode.txt
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import numpy as np

FFMPEG = r"D:/Software/ffmpeg-7.1-full_build/bin/ffmpeg.exe"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "webm")
OUT = os.path.join(ROOT, "build", "clips")
REPORT = os.path.join(ROOT, "build", "report")

W, H = 640, 360
FRAME_BYTES = W * H
CLAMP_AT = 20
CRF = 40

CLAMP_FILTER = (
    "format=yuva420p,"
    "geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':"
    f"a='if(lt(alpha(X,Y),{CLAMP_AT}),0,alpha(X,Y))'"
)


def alpha_stats(path: str) -> tuple[int, float, float]:
    """返回 (帧数, a0 占比%, a1_8 占比%)。用来断言钳位真的生效了。"""
    cmd = [FFMPEG, "-v", "error", "-c:v", "libvpx-vp9", "-i", path,
           "-vf", "alphaextract", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL)
    hist = np.zeros(256, dtype=np.int64)
    frames = 0
    try:
        while True:
            buf = pr.stdout.read(FRAME_BYTES)
            if len(buf) < FRAME_BYTES:
                break
            hist += np.bincount(np.frombuffer(buf, dtype=np.uint8).ravel(),
                                minlength=256)
            frames += 1
    finally:
        pr.stdout.close()
        pr.wait()
    if frames == 0:
        return 0, 0.0, 0.0
    total = hist.sum()
    return (frames,
            float(hist[0]) / total * 100,
            float(hist[1:9].sum()) / total * 100)


def reencode(src: str, dst: str) -> bool:
    cmd = [FFMPEG, "-v", "error", "-y",
           "-c:v", "libvpx-vp9", "-i", src,
           "-vf", CLAMP_FILTER,
           "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
           "-crf", str(CRF), "-b:v", "0",
           "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
           "-an", dst]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       errors="replace", timeout=600)
    if r.returncode != 0:
        print(f"  ! 编码失败 {os.path.basename(src)}: {r.stderr[:200]}")
        return False
    return True


def main() -> int:
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(REPORT, exist_ok=True)
    files = sorted(f for f in os.listdir(SRC) if f.endswith(".webm"))
    print(f"共 {len(files)} 段，crf={CRF}，钳位阈值 alpha<{CLAMP_AT}→0\n")

    # 源文件的 alpha 统计直接读 analyze_alpha.py 的报告，不重复解一遍
    src_stats: dict[str, tuple[int, float, float]] = {}
    csv_path = os.path.join(REPORT, "alpha_histogram.csv")
    if os.path.exists(csv_path):
        import csv as _csv
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in _csv.DictReader(f):
                src_stats[row["file"]] = (
                    int(row["frames"]), float(row["a0"]),
                    float(row["a1_8"]))

    t0 = time.time()
    lines = [f"alpha 钳位重编码报告（crf={CRF}）", "=" * 70, ""]
    total_in = total_out = 0
    bad = []
    for i, name in enumerate(files, 1):
        src = os.path.join(SRC, name)
        dst = os.path.join(OUT, name)
        size_in = os.path.getsize(src)
        total_in += size_in

        if os.path.exists(dst) and not force:
            size_out = os.path.getsize(dst)
        else:
            if not reencode(src, dst):
                bad.append(f"{name}: 编码失败")
                continue
            size_out = os.path.getsize(dst)
        total_out += size_out

        nf, a0, a18 = alpha_stats(dst)
        nf_in, a0_in, a18_in = src_stats.get(name, (nf, 0.0, 0.0))
        pct = size_out / size_in * 100
        lines.append(
            f"{name:<24} {size_in:>8} → {size_out:>8} ({pct:5.1f}%)  "
            f"帧 {nf_in}→{nf}  a0 {a0_in:5.1f}→{a0:5.1f}%  "
            f"a1_8 {a18_in:5.1f}→{a18:5.1f}%")

        if nf != nf_in:
            bad.append(f"{name}: 帧数变了 {nf_in}→{nf}")
        if a0 < 60.0:
            bad.append(f"{name}: 钳位没生效，a0 只有 {a0:.1f}%")
        if a18 > 5.0:
            bad.append(f"{name}: 还有 {a18:.1f}% 残值")

        if i % 10 == 0 or i == len(files):
            print(f"  {i}/{len(files)}  {time.time() - t0:.0f}s  "
                  f"{total_out / 1048576:.1f}MB")

    lines += ["", "-" * 70,
              f"原始合计 {total_in / 1048576:.2f} MB",
              f"钳位后   {total_out / 1048576:.2f} MB "
              f"({total_out / total_in * 100:.1f}%)",
              f"耗时     {time.time() - t0:.0f}s", ""]
    if bad:
        lines.append(f"⚠ 问题 {len(bad)} 条：")
        lines += ["  " + b for b in bad]
    else:
        lines.append("✅ 全部钳位成功，帧数一致，无残留")

    text = "\n".join(lines)
    print("\n" + text[-1200:])
    with open(os.path.join(REPORT, "reencode.txt"), "w",
              encoding="utf-8") as f:
        f.write(text + "\n")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
