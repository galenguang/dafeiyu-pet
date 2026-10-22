# -*- coding: utf-8 -*-
"""把 app/actions.py 的写死注册表 + 素材实测数据，编译成 build/manifest.json。

为什么要这一步：
1. 桌面版把 FPS=24 / CANVAS=640x360 写死在 actions.py，手机端换分辨率要改代码。
   manifest 让播放器只读数据，不读常量。
2. 桌面版所有片段共享 640x360 画布，所以不需要锚点；手机要裁要缩放，
   没有锚点切动作时角色会跳一下。
3. 帧数和时长要实测，不能用 `ACT_DURATION.get(id, 9.6)` 这种猜的默认值
   （桌面版每条尾巴 0.44s 从来没播到）。

依赖 build/report/alpha_histogram.csv（由 tools/analyze_alpha.py 生成）。
没有就先跑那个。

产物：
    build/manifest.json
    build/report/validate.txt   与 actions.py 的逐字段比对结果
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.actions import (ACTIONS, CANVAS_H, CANVAS_W, CATEGORIES,  # noqa: E402
                         FEATURES, FPS)

FFPROBE = r"D:/Software/ffmpeg-7.1-full_build/bin/ffprobe.exe"
SRC = os.path.join(ROOT, "assets", "webm")
REPORT = os.path.join(ROOT, "build", "report")
CSV_IN = os.path.join(REPORT, "alpha_histogram.csv")
OUT_JSON = os.path.join(ROOT, "build", "manifest.json")
OUT_VALIDATE = os.path.join(REPORT, "validate.txt")

MARGIN = 4          # bbox 向外扩，防压缩振铃被切边
ALPHA_HIT = 20      # 命中测试阈值，与桌面版 pet.py:1071 一致


def probe(path: str) -> dict:
    """读真实编码参数，用来断言「素材没被悄悄换掉」。"""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=codec_name,width,height,avg_frame_rate,pix_fmt:format=duration",
         "-of", "json", path],
        capture_output=True, text=True, errors="replace", timeout=30)
    if r.returncode != 0:
        return {}
    try:
        d = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    st = (d.get("streams") or [{}])[0]
    fmt = d.get("format") or {}
    return {
        "codec": st.get("codec_name"),
        "w": st.get("width"),
        "h": st.get("height"),
        "fps": st.get("avg_frame_rate"),
        "pix_fmt": st.get("pix_fmt"),
        "duration": float(fmt.get("duration") or 0),
    }


def load_bbox() -> dict[str, dict]:
    """从分析报告读 bbox20 与实测帧数。"""
    if not os.path.exists(CSV_IN):
        raise SystemExit(
            "缺少 " + CSV_IN + "\n先跑：python tools/analyze_alpha.py")
    out = {}
    with open(CSV_IN, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            out[row["file"]] = row
    return out


def main() -> int:
    bbox = load_bbox()
    os.makedirs(REPORT, exist_ok=True)

    clips = []
    problems: list[str] = []      # 会让构建失败
    notes: list[str] = []         # 只记录，不阻断
    codec_stats: dict[str, int] = {}

    for aid in sorted(ACTIONS):
        a = ACTIONS[aid]
        fn = a.clip
        path = os.path.join(SRC, fn)
        if not os.path.exists(path):
            problems.append(f"缺文件 {fn}（动作 {aid}）")
            continue
        info = probe(path)
        codec_stats[info.get("codec") or "?"] = \
            codec_stats.get(info.get("codec") or "?", 0) + 1
        if info.get("codec") != "vp9":
            problems.append(f"{fn} 编码不是 vp9 而是 {info.get('codec')}")
        if (info.get("w"), info.get("h")) != (CANVAS_W, CANVAS_H):
            problems.append(f"{fn} 分辨率 {info.get('w')}x{info.get('h')} "
                            f"不是 {CANVAS_W}x{CANVAS_H}")
        if info.get("fps") not in ("24/1", "24"):
            problems.append(f"{fn} 帧率 {info.get('fps')} 不是 24")

        row = bbox.get(fn)
        if row is None:
            problems.append(f"{fn} 不在 alpha 分析报告里")
            continue
        frames = int(row["frames"])
        box = row.get("bbox20") or ""
        if not box:
            problems.append(f"{fn} 整段全透明，bbox 为空")
            continue
        x1, y1, x2, y2 = (int(v) for v in box.split(","))

        # 扩边 + 夹回画布
        x1 = max(0, x1 - MARGIN)
        y1 = max(0, y1 - MARGIN)
        x2 = min(CANVAS_W - 1, x2 + MARGIN)
        y2 = min(CANVAS_H - 1, y2 + MARGIN)
        if x2 <= x1 or y2 <= y1:
            problems.append(f"{fn} 扩边后 bbox 非法 {x1},{y1},{x2},{y2}")
            continue

        # 时长以帧数为准，容器 duration 只做交叉校验。
        # ffprobe 对 webm 的 format.duration 单位是**秒**（如 "10.042"），
        # 曾经误当毫秒用，结果 100 条全变成 durationMs=10。
        # 帧数 / FPS 才是唯一可靠来源——实测全部素材就是 24fps 恒定帧率。
        dur_ms = int(round(frames * 1000.0 / FPS))
        if info.get("duration"):
            container_ms = int(round(info["duration"] * 1000))
            if abs(container_ms - dur_ms) > max(200, dur_ms * 0.05):
                notes.append(
                    f"{fn} 帧算时长 {dur_ms}ms 与容器 {container_ms}ms 不符")
        if frames < 200:
            # 实测 muse-wipe.webm 只有 192 帧（其余都是 241），
            # ffmpeg 解码时报 "File ended prematurely"——源素材本身是截断的。
            # 桌面版用 ACT_DURATION.get(id, 9.6) 猜时长，这条会多播 1.6s 静止帧。
            # 源素材我们改不了，只记录，不算构建失败。
            notes.append(f"{fn} 只有 {frames} 帧（其余 241），源素材疑似截断")
        clips.append({
            "id": a.id,
            "file": fn,
            "name": a.name,
            "cat": a.cat,
            "kind": a.kind,
            "weight": a.weight,
            "loop": a.loop,
            "frames": frames,
            "durationMs": dur_ms,
            "crop": [x1, y1, x2 - x1 + 1, y2 - y1 + 1],
            # 锚点 = 画布水平中心 + bbox 底边。
            # 只用于漫游/落地定位，**不要**拿它去缩放片段——
            # 渲染一律以画布坐标系为准，crop 只用来减少绘制面积。
            "anchor": [CANVAS_W // 2, y2],
        })

    # 图标从 actions.ICONS 补进来（保留 emoji，同时在报告里标出要换的）
    from app.actions import ICONS
    for c in clips:
        c["icon"] = ICONS.get(c["id"], "")

    manifest = {
        "version": 1,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stage": {"w": CANVAS_W, "h": CANVAS_H},
        "fps": FPS,
        "alphaHitThreshold": ALPHA_HIT,
        "cropMargin": MARGIN,
        "categories": [{"key": k, "label": v[0], "icon": v[1]}
                       for k, v in CATEGORIES.items()],
        "features": [{"key": f.key, "label": f.label, "icon": f.icon,
                      "kind": f.kind, "target": f.target} for f in FEATURES],
        "clips": clips,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # ---------------- 与 actions.py 逐字段比对 ----------------
    lines = ["manifest 与 app/actions.py 逐字段比对", "=" * 60, ""]
    lines.append(f"动作数   actions.py {len(ACTIONS)}   manifest {len(clips)}")
    diff = 0
    for c in clips:
        a = ACTIONS[c["id"]]
        for field, want in (("file", a.clip), ("cat", a.cat),
                            ("kind", a.kind), ("weight", a.weight),
                            ("loop", a.loop), ("name", a.name)):
            got = c[field]
            if got != want:
                lines.append(f"  DIFF {c['id']}.{field}: "
                             f"manifest={got!r} actions={want!r}")
                diff += 1
    # 反向：actions 里有但 manifest 没有的
    got_ids = {c["id"] for c in clips}
    for aid in ACTIONS:
        if aid not in got_ids:
            lines.append(f"  MISSING {aid}（actions.py 有，manifest 没有）")
            diff += 1

    # crop 越界检查
    for c in clips:
        x, y, w, h = c["crop"]
        if x < 0 or y < 0 or x + w > CANVAS_W or y + h > CANVAS_H:
            lines.append(f"  CROP-OOB {c['id']} {c['crop']}")
            diff += 1

    lines += ["", f"字段差异 {diff} 处", ""]
    if diff == 0:
        lines.append("✅ 逐字段一致，0 差异")

    # emoji 兼容性：Unicode 13+ 在 Android 12 及以下显示豆腐
    import unicodedata
    tofu = []
    for c in clips:
        ic = c.get("icon") or ""
        if ic and ord(ic[0]) >= 0x1FA70:
            tofu.append(f"  {ic} U+{ord(ic[0]):04X} "
                        f"{unicodedata.name(ic[0], '?')} ← {c['id']}")
    lines += ["", f"图标需要换掉的（Emoji 13+，Android 12 及以下显示豆腐）："
                  f"{len(tofu)} 个"]
    lines += tofu

    lines += ["", "编码统计：" + ", ".join(
        f"{k}×{v}" for k, v in sorted(codec_stats.items())), ""]
    if problems:
        lines.append(f"⚠ 问题 {len(problems)} 条（阻断）：")
        lines += ["  " + p for p in problems]
    else:
        lines.append("✅ 素材编码/分辨率/帧率全部符合预期")
    if notes:
        lines += ["", f"ℹ 备注 {len(notes)} 条（不阻断）："]
        lines += ["  " + s for s in notes]

    text = "\n".join(lines)
    print(text)
    with open(OUT_VALIDATE, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print(f"\nmanifest → {OUT_JSON}")
    return 0 if diff == 0 and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
