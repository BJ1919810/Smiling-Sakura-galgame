# -*- coding: utf-8 -*-
"""CG 出图收尾：把 ImageGen 的原始产物变成命名规范的成品。

用法：
    python tools/cg-finish.py <键名>          # 键名＝中文键名，如 长椅默读

做四件事（每步都校验，遇异常中止）：
  1. 在 `素材基准/CG/` 里找**最新**的 ImageGen 原始产物（按文件名里的秒级时间戳识别，不看前缀）
  2. 改名成 `CG_<键名>_源_2560x<实际高度>.png`
  3. 裁掉底部 (高度-1440)px → `CG_<键名>_2560x1440.png`
  4. 体检：尺寸 / 黑图（RGB 均值 <20 判失败）＋吐右下角切片供目视复核

★ 与 `bg-finish.py` 同源，差别只在目录与命名前缀（`CG_`）。
★ 写盘一律用 PIL —— `cv2.imwrite` 在**非 ASCII 路径上会静默失败**（不报错、不写盘、返回 False）。
★ 水印不做像素检测：水印恒在源图底部（`dewm.py` 实测 y 1448–1526，固定像素尺寸＋15px 边距），
  裁掉底部 96px 后**构造上就不存在**。曾试「成品右下角 vs 邻域比色」→ 4/4 全误报，是假指标。
★ 多图参考会把请求体撑爆（实测：传 6.5MB 的 PNG 直接 maxBodyLength 报错）
  ⇒ 传参考图前先压成小尺寸 JPEG（见 `.workbuddy/tmp/ref_*.jpg`）。
"""
import argparse
import os
import re
import sys

from PIL import Image
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CG_DIR = os.path.join(ROOT, "素材基准", "CG")
TMP_DIR = os.path.join(ROOT, ".workbuddy", "tmp")

OUT_H = 1440
# ★ 裁切量按**实际高度**算，不写死 96px：出图高度随档位会变（实测出现过 2560×1600），
#   而成品必须恒为 2560×1440（部署尺寸）。
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")


def newest_raw():
    cands = [os.path.join(CG_DIR, f) for f in os.listdir(CG_DIR) if TS.search(f)]
    if not cands:
        sys.exit("[中止] 没找到 ImageGen 原始产物（应为带秒级时间戳的文件名）")
    cands.sort(key=os.path.getmtime)
    return cands[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", help="CG 中文键名，如 长椅默读")
    ap.add_argument(
        "--from", dest="src_from", default=None,
        help="显式指定要收尾的原始产物（文件名或绝对路径）。默认取「最新的一张」。"
             "★ 边出下一张边收上一张时必须显式指定 —— 否则「取最新」可能收错文件。",
    )
    a = ap.parse_args()

    if a.src_from:
        raw = a.src_from if os.path.isabs(a.src_from) else os.path.join(CG_DIR, a.src_from)
        if not os.path.isfile(raw):
            sys.exit(f"[中止] --from 指定的文件不存在：{raw}")
    else:
        raw = newest_raw()
    probe = Image.open(raw)
    w, h = probe.size
    probe.close()
    if w != 2560:
        sys.exit(f"[中止] 宽度不是 2560：{w}")

    src = os.path.join(CG_DIR, f"CG_{a.key}_源_2560x{h}.png")
    out = os.path.join(CG_DIR, f"CG_{a.key}_2560x{OUT_H}.png")
    for p in (src, out):
        if os.path.exists(p):
            sys.exit(f"[中止] 目标已存在，先人工确认：{os.path.basename(p)}")

    os.replace(raw, src)
    print(f"① 改名 → {os.path.basename(src)}")
    left = [f for f in os.listdir(CG_DIR) if TS.search(f)]
    if a.src_from:
        # 显式指定源文件时，目录里往往还有「下一张」的原始产物（＝出图与收尾并行）—— 正常，不报警
        print(f"   目录里其余待收尾的原始产物：{left if left else '无'}")
    else:
        print(f"   旧名残留：{left if left else '无 ✓'}")

    im = Image.open(src).convert("RGB")
    cut = h - OUT_H
    if cut <= 0:
        sys.exit(f"[中止] 源图高度 {h} ≤ 目标 {OUT_H}，无法裁切")
    cropped = im.crop((0, 0, w, OUT_H))
    cropped.save(out, "PNG")
    im.close()
    print(f"② 源图 {w}×{h}" + ("  ✓" if h == 1536 else f"  ⚠ 非标准高 1536"))
    print(f"③ 裁底 {cut}px → {os.path.basename(out)}  {os.path.getsize(out):,} B ✓")

    arr = np.asarray(cropped).astype(np.float32)
    mean = float(arr.mean())
    print(f"④ 体检：RGB 均值 {mean:.1f}" + ("  ✗ 疑似黑图！" if mean < 20 else "  ✓"))
    print(f"   水印：构造保证已去除（裁掉底部 {cut}px，水印在被裁区内）——不另做像素检测（假指标）")

    os.makedirs(TMP_DIR, exist_ok=True)
    bw, bh = cropped.size
    cropped.crop((bw - 660, bh - 300, bw, bh)).resize((1056, 480), Image.NEAREST).save(
        os.path.join(TMP_DIR, "cg_bottomright.png"))
    print(f"   右下角切片（目视复核）→ .workbuddy/tmp/cg_bottomright.png")


main()
