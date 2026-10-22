# -*- coding: utf-8 -*-
"""把素材同步进 Flutter 工程的 assets。

为什么要这一步而不是手工拷贝：
1. `flutter_app/assets/clips/` **不入库**（50MB，且是 assets/webm 的派生文件）。
   克隆下来必须显式跑一次这个脚本才有素材，否则 App 一启动就是
   「本地素材服务启动失败」。
2. 49.7MB 素材每次全拷太慢。这里按 mtime+size 判断，只拷变化的。
3. 顺手做一致性校验：manifest 里每条 clip 都必须有对应文件，
   文件数也必须等于 100，多一个少一个都报错。

**素材源有两个，按优先级取：**

- `build/clips/` —— 跑过 `tools/reencode_clamped.py` 的钳位版，
  大小减半、手机上解码更快。**优先用这个。**
- `assets/webm/` —— 仓库里已提交的原始素材。没跑过钳位流程时回退到它。
  原始素材的 alpha 有 1~8 的灰雾（见 docs），在手机上会有一层薄边，
  但画面正常，能直接跑起来。

manifest 同理：优先 `build/manifest.json`（新生成），
没有再回退到 `flutter_app/assets/manifest.json`（已入库的那份）。

用法：
    python tools/sync_assets.py            # 增量同步
    python tools/sync_assets.py --force    # 全部重拷
    python tools/sync_assets.py --check    # 只检查不拷贝
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(ROOT, "flutter_app", "assets")
DST_CLIPS = os.path.join(DST, "clips")
DST_MANIFEST = os.path.join(DST, "manifest.json")


def _pick_clip_dir() -> str | None:
    """挑一个可用的素材源目录。"""
    for rel in ("build/clips", "assets/webm"):
        p = os.path.join(ROOT, rel)
        if os.path.isdir(p) and any(f.endswith(".webm") for f in os.listdir(p)):
            return p
    return None


def _pick_manifest(clip_dir: str) -> str | None:
    """挑一个可用的 manifest。优先跟着素材源走。"""
    candidates = [
        os.path.join(ROOT, "build", "manifest.json"),
        DST_MANIFEST,  # 已入库的那份，克隆后唯一存在的
    ]
    if clip_dir.endswith(os.path.join("assets", "webm")):
        candidates.reverse()
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    src_clips = _pick_clip_dir()
    if src_clips is None:
        print("找不到素材源：既没有 build/clips/ 也没有 assets/webm/")
        print("先跑 tools/reencode_clamped.py，或从上游仓库取 assets/webm/")
        return 1
    src_manifest = _pick_manifest(src_clips)
    if src_manifest is None:
        print("找不到 manifest.json（build/ 和 flutter_app/assets/ 都没有）")
        return 1

    if src_clips != DST_CLIPS:
        print(f"素材源：{os.path.relpath(src_clips, ROOT)}")
    print(f"manifest：{os.path.relpath(src_manifest, ROOT)}")

    manifest = json.load(open(src_manifest, encoding="utf-8"))
    wanted = {c["file"] for c in manifest["clips"]}
    on_disk = {f for f in os.listdir(src_clips) if f.endswith(".webm")}

    errs = []
    if wanted - on_disk:
        errs.append(f"manifest 有但磁盘没有: {sorted(wanted - on_disk)[:5]}")
    if on_disk - wanted:
        errs.append(f"磁盘有但 manifest 没引用: {sorted(on_disk - wanted)[:5]}")
    if len(wanted) != 100:
        errs.append(f"manifest 里只有 {len(wanted)} 条，预期 100")
    if errs:
        print("一致性校验失败：")
        for e in errs:
            print("  " + e)
        return 1
    print(f"一致性 OK：{len(wanted)} 条 clip，manifest 与磁盘完全对应")

    if args.check:
        have = len([f for f in os.listdir(DST_CLIPS)
                    if f.endswith(".webm")]) if os.path.isdir(DST_CLIPS) else 0
        print(f"flutter_app/assets/clips 现有 {have} 个文件")
        return 0

    os.makedirs(DST_CLIPS, exist_ok=True)
    copied = skipped = 0
    total = 0
    for name in sorted(wanted):
        src = os.path.join(src_clips, name)
        dst = os.path.join(DST_CLIPS, name)
        s_size = os.path.getsize(src)
        total += s_size
        if (not args.force and os.path.isfile(dst)
                and os.path.getsize(dst) == s_size
                and int(os.path.getmtime(dst)) >= int(os.path.getmtime(src))):
            skipped += 1
            continue
        shutil.copy2(src, dst)
        copied += 1
    print(f"素材：拷贝 {copied}，跳过 {skipped}，合计 {total / 1048576:.1f}MB")

    # manifest 每次都覆盖（很小，而且改了必须立刻生效）
    if os.path.abspath(src_manifest) != os.path.abspath(DST_MANIFEST):
        shutil.copy2(src_manifest, DST_MANIFEST)
    print(f"manifest → {DST_MANIFEST} ({os.path.getsize(DST_MANIFEST)} 字节)")

    # 清掉 assets 里多余的旧文件（比如改名后的残留）
    stale = [f for f in os.listdir(DST_CLIPS)
             if f.endswith(".webm") and f not in wanted]
    for f in stale:
        os.remove(os.path.join(DST_CLIPS, f))
    if stale:
        print(f"清掉 {len(stale)} 个过期文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
