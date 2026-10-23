# -*- coding: utf-8 -*-
"""CG 角落水印修补：拉普拉斯扩散（泊松式平滑填充）。

用法：python tools/_cg_inpaint.py <图片路径> <x0> <y0> <x1> <y1>
区域右/下越界时用最近内侧一圈作边界。补丁后轻噪点匹配颗粒、20px 软融合。
"""
import sys

import numpy as np
from PIL import Image


def main() -> int:
    path, x0, y0, x1, y1 = sys.argv[1], *map(int, sys.argv[2:6])
    im = np.array(Image.open(path).convert('RGB')).astype(np.float64)
    H, W = im.shape[:2]
    x1 = min(x1, W)
    y1 = min(y1, H)
    patch = im[y0:y1, x0:x1].copy()
    h, w = patch.shape[:2]

    top = im[y0 - 1, x0:x1]            # (w,3)
    bottom = im[min(y1, H - 1), x0:x1]  # (w,3)
    left = im[y0:y1, max(0, x0 - 1)]   # (h,3)
    right = im[y0:y1, min(x1, W - 1) - 1]  # (h,3)

    u = patch.copy()
    for c in range(3):
        col = np.linspace(top[:, c], bottom[:, c], h, axis=0)   # (h,w)
        row = np.linspace(left[:, c], right[:, c], w, axis=1)   # (h,w)
        u[:, :, c] = (col + row) / 2.0

    for _ in range(1500):
        u[1:-1, 1:-1] = 0.25 * (u[:-2, 1:-1] + u[2:, 1:-1] + u[1:-1, :-2] + u[1:-1, 2:])

    rng = np.random.default_rng(42)
    u = np.clip(u + rng.normal(0, 1.5, u.shape), 0, 255)

    fy = np.minimum(np.arange(h) / 20.0, 1.0)[:, None, None]
    fx = np.minimum(np.arange(w) / 20.0, 1.0)[None, :, None]
    a = np.clip(fy * fx, 0, 1)
    im[y0:y1, x0:x1] = patch * (1 - a) + u * a

    Image.fromarray(im.astype(np.uint8)).save(path)
    print(f'  inpaint ok  区域 ({x0},{y0})-({x1},{y1})  -> {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
