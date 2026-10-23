# -*- coding: utf-8 -*-
"""对比「云端 JoinImageWithAlpha」与「本地 mask_compose+edge_clean」两种透明化结果。

用法：
    python tools/cloud_alpha_check.py 云端alpha.png 本地成品透明.png 颜色层.png
"""
from __future__ import annotations

import sys

import numpy as np
from PIL import Image


def alpha_stats(p: str) -> dict:
    with Image.open(p) as im:
        if im.mode != "RGBA":
            return {"file": p, "mode": im.mode, "rgba": False}
        a = np.asarray(im.getchannel("A"), dtype=np.uint8)
    total = a.size
    hist = np.bincount(a.ravel(), minlength=256)
    solid = int(hist[255]); clear = int(hist[0])
    soft = total - solid - clear
    ys, xs = np.nonzero(a > 127)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if xs.size else None
    return {"file": p, "mode": "RGBA", "rgba": True, "total": total,
            "clear": clear, "solid": solid, "soft": soft,
            "soft_pct": 100.0 * soft / total,
            "solid_pct": 100.0 * solid / total,
            "bbox": bbox, "alpha": a}


def main() -> None:
    cloud_p, local_p, color_p = sys.argv[1], sys.argv[2], sys.argv[3]
    cs = alpha_stats(cloud_p)
    ls = alpha_stats(local_p)
    for s in (cs, ls):
        if not s["rgba"]:
            print("!! %s 不是 RGBA（mode=%s）" % (s["file"], s["mode"]))
            return
        print("%-42s 不透明 %5.1f%%  全透明 %5.1f%%  半透明 %5.2f%% (%d px)  bbox=%s" % (
            s["file"].split("/")[-1], s["solid_pct"], 100 * s["clear"] / s["total"],
            s["soft_pct"], s["soft"], s["bbox"]))

    cb = cs["alpha"] > 127
    lb = ls["alpha"] > 127
    inter = int(np.logical_and(cb, lb).sum())
    union = int(np.logical_or(cb, lb).sum())
    print("\n不透明区 IoU = %.4f" % (inter / union if union else 0.0))
    print("云端有而本地无：%d px ／ 本地有而云端无：%d px" % (
        int(np.logical_and(cb, ~lb).sum()), int(np.logical_and(lb, ~cb).sum())))

    with Image.open(color_p) as c:
        rgb = np.asarray(c.convert("RGB"), dtype=np.int16)
    with Image.open(cloud_p) as ci:
        cloud_rgb = np.asarray(ci.convert("RGB"), dtype=np.int16)
    diff = np.abs(rgb - cloud_rgb).max(axis=2)
    changed = int((diff > 2).sum())
    print("颜色层是否被改动：%s（maxdiff=%d, 改动像素 %d）" % (
        "否 ✅ 纯 alpha 合成" if changed == 0 else "是 ⚠️", int(diff.max()), changed))


if __name__ == "__main__":
    main()
