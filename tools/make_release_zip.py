"""把 PyInstaller 产物打成发布用 zip。

只做一件事：让 zip 内部的顶层目录固定叫「大肥鱼桌宠」，用户解压出来
就是一个文件夹，不会散一地。顺带把体积和条目数打出来，方便判断
要不要给 Gitee 拆卷（单文件上限 100MB）。

拆卷用 ``--parts N``。注意这里**不是**二进制分卷（那种得让用户敲
``copy /b`` 合并），而是把文件摊成 N 份、每份都是一个能独立解压的 zip，
顶层目录都是「大肥鱼桌宠」。用户把 N 个包都解压到同一个文件夹就齐了。
体积分配按**压缩后**的大小贪心算，否则 DLL 和 webm 的压缩率差太多，
按原始体积分会一边爆一边空。

用法：
    python tools/make_release_zip.py <产物目录> [输出 zip]
    python tools/make_release_zip.py <产物目录> --parts 2 --max-mb 95
"""
from __future__ import annotations

import os
import sys
import time
import zipfile
import zlib

ROOT_NAME = "大肥鱼桌宠"          # zip 里的顶层目录名，不要跟着产物目录改名
COMPRESS_LEVEL = 6
CHUNK = 1 << 20


def _collect(src: str) -> list[tuple[str, str, int]]:
    """返回 [(磁盘路径, zip 内路径, 原始字节数)]，已排序。"""
    items: list[tuple[str, str, int]] = []
    for base, dirs, names in os.walk(src):
        dirs.sort()
        rel = os.path.relpath(base, src)
        arc_dir = ROOT_NAME if rel == "." else \
            ROOT_NAME + "/" + rel.replace(os.sep, "/")
        for name in sorted(names):
            full = os.path.join(base, name)
            if os.path.isfile(full):
                items.append((full, arc_dir + "/" + name,
                              os.path.getsize(full)))
    return items


def _deflate_size(path: str) -> int:
    """只压不写，估算这个文件进 zip 后占多少字节。"""
    co = zlib.compressobj(COMPRESS_LEVEL, zlib.DEFLATED, -15)
    total = 0
    with open(path, "rb") as f:
        while True:
            buf = f.read(CHUNK)
            if not buf:
                break
            total += len(co.compress(buf))
    total += len(co.flush())
    return total


def _partition(items: list[tuple[str, str, int]], parts: int
               ) -> list[list[tuple[str, str, int]]]:
    """按压缩后体积贪心均分：大文件先放，每次塞进当前最轻的那一份。"""
    sized = [(p, a, _deflate_size(p)) for p, a, _ in items]
    sized.sort(key=lambda x: -x[2])
    buckets: list[list[tuple[str, str, int]]] = [[] for _ in range(parts)]
    loads = [0] * parts
    for path, arc, csize in sized:
        i = loads.index(min(loads))
        buckets[i].append((path, arc, csize))
        loads[i] += csize
    return buckets


def _write(pairs: list[tuple[str, str]], out: str) -> int:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=COMPRESS_LEVEL) as z:
        # 顶层目录先写一条，解压工具才会建出这个文件夹
        z.writestr(zipfile.ZipInfo(ROOT_NAME + "/"), b"")
        for path, arc in sorted(pairs, key=lambda x: x[1]):
            z.write(path, arc)
    return os.path.getsize(out)


def build(src: str, out: str) -> tuple[int, int]:
    items = _collect(src)
    files = _write([(p, a) for p, a, _ in items], out)
    return len(items), files


def build_parts(src: str, out_base: str, parts: int,
                max_mb: float) -> list[tuple[str, int, int]]:
    """返回 [(输出路径, 文件数, 字节数)]。"""
    items = _collect(src)
    buckets = _partition(items, parts)
    results = []
    for i, bucket in enumerate(buckets, 1):
        out = f"{out_base}-part{i}.zip"
        if os.path.exists(out):
            raise SystemExit("输出已存在，先改名再跑: " + out)
        size = _write([(p, a) for p, a, _ in bucket], out)
        if size > max_mb * 1024 * 1024:
            print(f"  ! part{i} 仍然超过 {max_mb:.0f}MB，需要更多份数")
        results.append((out, len(bucket), size))
    return results


def main() -> int:
    args = [a for a in sys.argv[1:]]
    parts = 1
    max_mb = 95.0
    if "--parts" in args:
        i = args.index("--parts")
        parts = int(args[i + 1])
        del args[i:i + 2]
    if "--max-mb" in args:
        i = args.index("--max-mb")
        max_mb = float(args[i + 1])
        del args[i:i + 2]

    if not args:
        print(__doc__)
        return 2
    src = args[0]
    if not os.path.isdir(src):
        print("目录不存在:", src)
        return 1

    t0 = time.time()
    if parts <= 1:
        out = args[1] if len(args) > 1 else os.path.join(
            os.path.dirname(os.path.abspath(src)),
            ROOT_NAME + "-v2.0-win64.zip")
        if os.path.exists(out):
            print("输出已存在，先改名再跑:", out)
            return 1
        n, size = build(src, out)
        print(f"文件数 {n}")
        print(f"体积   {size / 1024 / 1024:.1f} MB")
        print(f"输出   {out}")
    else:
        base = args[1] if len(args) > 1 else os.path.join(
            os.path.dirname(os.path.abspath(src)),
            ROOT_NAME + "-v2.0-win64")
        for out, n, size in build_parts(src, base, parts, max_mb):
            print(f"{os.path.basename(out)}  文件数 {n}  "
                  f"体积 {size / 1024 / 1024:.1f} MB")
    print(f"耗时   {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
