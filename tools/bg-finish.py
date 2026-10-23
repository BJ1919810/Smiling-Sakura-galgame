# -*- coding: utf-8 -*-
"""BG 出图收尾：把 ImageGen 的原始产物变成部署用成品。

用法：
    python tools/bg-finish.py <键名> [--dry]

做三件事（幂等、每步都校验）：
  1. 在 `素材基准/背景/` 里找**最新**的 ImageGen 原始产物（文件名以「只参考」开头，秒级时间戳）
  2. 改名成 `<键名>_源_2560x1536.png`（★ ImageGen 同目录并行会互相覆盖，必须出一张立刻改名）
  3. 裁掉底部 96px → `<键名>_2560x1440.png`（★ 水印恒在右下角、整块落在被裁掉的 96px 内，免 delogo）
  4. 体检：尺寸 / 黑图（RGB 均值 <20 判失败）/ 右下角水印残留（邻域偏离，目视复核）

★ 写盘一律用 PIL —— `cv2.imwrite` 在**非 ASCII 路径上会静默失败**（不报错、不写盘、返回 False）。

★ 水印**不做像素检测**：水印恒在源图 y 1448–1526（`dewm.py` 实测规律），而本脚本裁掉底部 96px，
  **"裁干净"是构造保证的**。曾试过「成品右下角 vs 邻域比色」，**连跑 4 张全部误报**（19/24/11/11）——
  透视渐变与材质差异本身就大，那个指标在量普通画面内容，是**假指标**。复核方式＝**目视**右下角切片。
"""
import argparse
import os
import re
import sys

from PIL import Image
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG_DIR = os.path.join(ROOT, "素材基准", "背景")
TMP_DIR = os.path.join(ROOT, ".workbuddy", "tmp")

SRC_H = 1536
OUT_H = 1440
# ★ 裁切量按**实际高度**算，不写死 96px：
# 出图高度随提示词/工具档位会变（实测出现过 2560×1600），而成品必须恒为 2560×1440（部署尺寸）。
# 水印恒在底部约 y 0.94–0.99 且是「固定像素尺寸＋15px 边距」，裁掉底部 (h-OUT_H) 后仍在被裁区内。

# ★ ImageGen 的产物名＝「提示词前若干字」＋秒级时间戳。
#   提示词一变，前缀就变（踩过：第一版的「只参考」前缀在重出时失效了）
#   ⇒ 一律**按文件名里的时间戳识别**，不看前缀。
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}")


def newest_raw():
    cands = [os.path.join(BG_DIR, f) for f in os.listdir(BG_DIR) if TS.search(f)]
    if not cands:
        sys.exit("[中止] 没找到 ImageGen 原始产物（应为带时间戳的文件名）")
    cands.sort(key=os.path.getmtime)
    return cands[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", help="BG 键名，如 操场_晴天_大风")
    a = ap.parse_args()

    raw = newest_raw()
    # ★ 先量尺寸再改名 —— 源图文件名必须写真实高度（工具档位会让高度不是 1536，写死会骗人）
    _probe = Image.open(raw)
    w, h = _probe.size
    _probe.close()
    if w != 2560:
        sys.exit(f"[中止] 宽度不是 2560：{w}")
    src = os.path.join(BG_DIR, f"{a.key}_源_2560x{h}.png")
    out = os.path.join(BG_DIR, f"{a.key}_2560x1440.png")

    for p in (src, out):
        if os.path.exists(p):
            sys.exit(f"[中止] 目标已存在，先人工确认：{os.path.basename(p)}")

    os.replace(raw, src)
    print(f"① 改名 → {os.path.basename(src)}")
    left = [f for f in os.listdir(BG_DIR) if TS.search(f)]
    print(f"   旧名残留：{left if left else '无 ✓'}")

    im = Image.open(src).convert("RGB")
    print(f"② 源图 {w}×{h}" + ("  ✓" if h == SRC_H else f"  ⚠ 非标准高 {SRC_H}（按实际高度裁到 {OUT_H}）"))

    cut = h - OUT_H
    if cut <= 0:
        sys.exit(f"[中止] 源图高度 {h} ≤ 目标 {OUT_H}，无法裁切")
    cropped = im.crop((0, 0, w, OUT_H))
    cropped.save(out, "PNG")
    print(f"③ 裁底 {cut}px → {os.path.basename(out)}  {os.path.getsize(out):,} B ✓"
          + ("" if cut == 96 else f"  （非 96px：源图高 {h}）"))

    arr = np.asarray(cropped).astype(np.float32)
    mean = float(arr.mean())
    print(f"④ 体检：RGB 均值 {mean:.1f}" + ("  ✗ 疑似黑图！" if mean < 20 else "  ✓"))
    print(f"   水印：**构造保证已去除**（水印恒在底部、是固定像素尺寸＋15px 边距；裁掉底部 {cut}px 即在其上方截断）——"
          f"不另做像素检测（试过，是假指标，4/4 误报）")

    # 右下角切片（ASCII 路径）供目视复核 —— 这才是水印的复核方式
    os.makedirs(TMP_DIR, exist_ok=True)
    pv = os.path.join(TMP_DIR, "bg_bottomright.png")
    bw, bh = cropped.size
    cropped.crop((bw - 660, bh - 300, bw, bh)).resize((1056, 480), Image.NEAREST).save(pv)
    print(f"   右下角切片（目视复核）→ {os.path.relpath(pv, ROOT)}")


main()
