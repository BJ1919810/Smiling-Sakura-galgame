#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
笑樱 · 桌面 exe 探针（出包最后一步）

干什么：
  1. 从 dist/index.html 解析出真正的入口 chunk 名（如 index-B13q7zIO.js）
  2. 校验该 hash 在 exe 里**明文出现**（tauri 的资源路径表是明文，压缩区里 grep 不到）
  3. 统计 exe 资源路径表里的素材条目（bgm / bg / cg / char），与 dist 实际文件数对照
  4. 校验时序链：dist < exe
  5. --deploy 时把 exe 复制到项目根目录 `笑樱-测试版.exe`，并打印 md5

⚠️ 为什么不能只 grep 台词/文件名：
   release 内嵌资源经 brotli 压缩，**压缩区里明文 grep 必 miss**；
   真正的判据是 ①资源路径表（明文）②dist 与 exe 的时序链 ③体积增量。

⚠️ 为什么入口 chunk hash 可能和上一版相同：
   JS 代码没改（src/ 无改动）时 rollup 的 chunk hash 就是一样的，**这不算异常**。
   manifest 里的 `generatedAt` 会被 tree-shaking 去掉（没人引用它），所以重跑 manifest 不影响 hash。
   要看"素材有没有更新"，得看**资源路径表里的条目**，而不是 chunk hash。

用法：
  python tools/exe_probe.py              # 只探针，不部署
  python tools/exe_probe.py --deploy     # 探针通过后部署到 笑樱-测试版.exe
  python tools/exe_probe.py --exe 别的.exe
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILT = ROOT / "src-tauri" / "target" / "release" / "xiaoying.exe"
DEFAULT_TARGET = ROOT / "笑樱-测试版.exe"

CHUNK_RE = re.compile(rb"assets/(index-[A-Za-z0-9_-]{6,}\.js)")
# 相对 dist 的路径（exe 资源路径表里也是这个形态）
KINDS = ("assets/audio/bgm", "assets/bg", "assets/cg", "assets/char")


def fmt_time(p: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))


def md5_of(p: Path, block: int = 1 << 22) -> tuple[str, int]:
    h = hashlib.md5()
    n = 0
    with open(p, "rb") as f:
        while True:
            b = f.read(block)
            if not b:
                break
            n += len(b)
            h.update(b)
    return h.hexdigest(), n


def ok(msg: str) -> None:
    print("  \033[32m✓\033[0m " + msg if sys.stdout.isatty() else "  [ok] " + msg)


def bad(msg: str) -> None:
    print(("  \033[31m✗\033[0m " if sys.stdout.isatty() else "  [x] ") + msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=str(BUILT), help="待检 exe（默认构建产物）")
    ap.add_argument("--deploy", action="store_true", help="探针通过后部署到项目根目录")
    ap.add_argument("--out", default=str(DEFAULT_TARGET), help="部署目标名")
    args = ap.parse_args()

    exe = Path(args.exe)
    fails: list[str] = []

    print("=" * 64)
    print("笑樱 exe 探针")
    print("=" * 64)

    # ---------- 0. 前置 ----------
    for p in (DIST / "index.html", exe):
        if not p.exists():
            bad("找不到 %s" % p)
            return 2

    # ---------- 1. 入口 chunk ----------
    html = (DIST / "index.html").read_bytes()
    m = CHUNK_RE.search(html)
    if not m:
        bad("dist/index.html 里没找到入口 chunk 名")
        return 2
    chunk = m.group(1).decode()
    print()
    print("[1] 入口 chunk：%s" % chunk)
    if not (DIST / "assets" / chunk).exists():
        bad("dist/assets/%s 不存在" % chunk)
        fails.append("入口 chunk 文件缺失")
    else:
        ok("dist/assets/%s 存在（%.1f kB）"
           % (chunk, (DIST / "assets" / chunk).stat().st_size / 1024))

    # ---------- 2. exe 里明文出现 ----------
    print()
    print("[2] exe 内嵌校验")
    data = exe.read_bytes()
    # ⚠️ 路径表里 `.gitkeep` 会与相邻条目粘连成正则假匹配
    #    （assets/bg/.gitkeep/assets/cg/cg_008.png），使每个素材目录边界多算 1 条，
    #    看着像「exe 比 dist 多」其实是假阳性。下面等长抹除（8 字节→8 字节，
    #    不改变偏移），与本文件 dist 侧「排除 . 开头文件」的口径对齐。别改回 data。
    probe = data.replace(b".gitkeep", b"\x00" * 8)
    nchunk = data.count(("assets/" + chunk).encode())
    if nchunk:
        ok("资源路径表含 %s（%d 次）" % (chunk, nchunk))
    else:
        bad("exe 里找不到 %s → dist 没被嵌入（是否漏了 --features custom-protocol？）" % chunk)
        fails.append("入口 chunk 未嵌入")

    # ---------- 3. 素材条目 ----------
    print()
    print("[3] 资源路径表里的素材条目")
    for kind in KINDS:
        d = DIST / kind
        on_disk = len([f for f in d.rglob("*") if f.is_file() and not f.name.startswith(".")]) if d.exists() else 0
        in_exe = len(set(re.findall((re.escape(kind) + r"/[^\x00\"\\]{1,120}?\.[a-zA-Z0-9]{2,5}").encode(), probe)))
        flag = " " if in_exe else "  ← 0，检查是否真需要"
        print("    %-18s dist %3d 个 ｜ exe 路径表 %3d 条%s" % (kind, on_disk, in_exe, flag))
        if kind.endswith("audio/bgm"):
            bgms = sorted({x.decode() for x in re.findall(rb"assets/audio/bgm/bgm_[0-9]{3}\.ogg", probe)})
            print("        BGM 明细：%s" % (", ".join(os.path.basename(b) for b in bgms) or "（无）"))
            if not bgms:
                fails.append("exe 里没有 BGM")

    # ---------- 4. 时序链 ----------
    print()
    print("[4] 时序链（后半必须晚于前半）")
    chain = [
        (ROOT / "src/generated/script.json", "script.json"),
        (ROOT / "src/generated/assets.manifest.json", "manifest"),
        (DIST, "dist"),
        (exe, "exe"),
    ]
    prev = 0.0
    for p, label in chain:
        t = p.stat().st_mtime
        late = "" if t >= prev else "   ← 早于上一环！"
        print("    %-12s %s%s" % (label, fmt_time(p), late))
        if late:
            fails.append("%s 早于上一环" % label)
        prev = t

    # ---------- 5. 体积 ----------
    print()
    print("[5] 体积")
    size = exe.stat().st_size
    print("    exe %.1f MB" % (size / 1e6))
    if args.out and Path(args.out).exists() and Path(args.out) != exe:
        old = Path(args.out).stat().st_size
        print("    旧版 %.1f MB  →  增量 %+.1f MB" % (old / 1e6, (size - old) / 1e6))

    # ---------- 结论 ----------
    print()
    print("=" * 64)
    if fails:
        print("探针未通过：")
        for f in fails:
            print("  - " + f)
        return 1
    print("探针通过 ✓")

    if args.deploy:
        out = Path(args.out)
        shutil.copyfile(exe, out)
        h_src, n_src = md5_of(exe)
        h_dst, n_dst = md5_of(out)
        print()
        print("[部署] %s" % out.name)
        print("    %12d B  md5 %s  ← 构建产物" % (n_src, h_src))
        print("    %12d B  md5 %s  ← 根目录" % (n_dst, h_dst))
        if h_src != h_dst:
            bad("复制后 md5 不一致！")
            return 1
        ok("md5 一致，部署完成")
    else:
        print("（未部署；加 --deploy 复制到项目根目录）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
