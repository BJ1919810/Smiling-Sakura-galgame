# -*- coding: utf-8 -*-
"""去水印（ImageGen「AI生成 WORKBUDD>」右下角水印）—— 本地方案。

★ 一句话：这类「延续周围像素」的活**不要让生成模型干**，cv2 传统算法毫秒级完胜。
  实测走廊源图（墙面）：cv2 NS r=3 = 0.03s 水印彻底消失；云卡 Qwen-Image inpaint
  两次都生成莫名其妙的新纹理（绒毛／横纹），且要 44~278s。
  → 根因：Qwen-Image 2512 是 T2I 基座不是 inpaint 模型，mask 区「自由创作」。

★ 两种模式：
  ┌ 快速（默认）：cv2.inpaint NS r=3。0.03s/张。
  │   ✅ 纯色/近乎纯色背景（如墙面，邻域 std<1）→ 完美。
  │   ❌ 有颗粒/渐变的背景 → 遗留矩形「糊块」+ 方向性条纹 + V 形折角，肉眼可见。
  └ 高质量（--smooth，**推荐**）：Laplace+IDW 混合低频 + 匹配邻域颗粒 + 羽化。
      1~2s/张。实测旧长椅源图（水泥地+花瓣）：糊块消失，1x 视距下与周围无法区分。
      ⚠️ 必须用**紧致 bbox**（--rect 精确框或默认 tight_rect），**不要外扩**：
        实测外扩 8px 会让掩膜区与邻域色差从 0.6 涨到 8+（把周围真实渐变吞进去了）。

★ 水印定位铁律：**右下角固定像素尺寸 + 15px 边距，不随图缩放**（两条独立实测）：
      1440x2560 立绘 → x[1269,1425] y[2474,2545]（右边距 15、下边距 15）
      2560x1536 源图 → x[2380,2545] y[1445,1521]（右边距 15、下边距 15）
    所以按时**别按比例外推**（对 1536 高的图会偏 100+px → mask 盖不全 → 残留半截水印）。
    自动检测（--auto）在背景干净时可用，但画面有地砖缝/阴影等暗线时会被带偏，
    批量场景请用 --smooth 的默认 tight_rect。

★ 别忘了先问：**这张图真的需要去水印吗？**
    立绘 _raw_*.png 是已抠好的 RGBA，水印只在 alpha=0 透明区的 RGB 残留 → 合成后不可见；
    BG/CG 成品 *_2560x1440 已通过裁底 96px 把水印裁掉。只有「要重新用源图构图」才需要处理。

用法：
    # 高质量（推荐）：自动取右下角紧致 bbox
    python tools/dewm.py --src a.png --out b.png --smooth
    # 精确指定水印 bbox（最稳）
    python tools/dewm.py --src a.png --out b.png --smooth --rect 2380,1445,2545,1521
    # 快速（纯色背景够用）
    python tools/dewm.py --src a.png --out b.png
    # 自动检测（仅背景干净时可靠）
    python tools/dewm.py --src a.png --out b.png --auto
    # 批量（同尺寸同位置的整套素材）
    python tools/dewm.py --src-dir 素材基准/背景 --suffix _源 --out-dir 输出目录 --smooth
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2
import numpy as np
from PIL import Image


def imread_u(path: str) -> np.ndarray:
    """读图（兼容中文路径）→ RGB ndarray。"""
    return np.asarray(Image.open(path).convert("RGB"))


def imwrite_u(path: str, rgb: np.ndarray) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    Image.fromarray(rgb).save(path)


def detect_watermark(rgb: np.ndarray, search: float = 0.20,
                     thr: int = 26, min_area: int = 300) -> tuple[int, int, int, int] | None:
    """在右下角 search 比例窗口内检测「亮于局部背景」的水印文字块，返回 (x0,y0,x1,y1)。"""
    g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    h, w = g.shape
    y0, x0 = int(h * (1 - search)), int(w * (1 - search))
    sub = g[y0:, x0:]
    kh = max(3, (min(sub.shape) // 3) | 1)
    bg = cv2.medianBlur(sub, kh)
    d = sub.astype(np.int16) - bg.astype(np.int16)
    m = (d > thr).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    boxes = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if area < min_area:
            continue
        if ww > sub.shape[1] * 0.95 and hh > sub.shape[0] * 0.95:
            continue                      # 覆盖整窗 → 多半是画面内容，丢弃
        boxes.append((x0 + x, y0 + y, x0 + x + ww, y0 + y + hh))
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def default_rect(rgb: np.ndarray, size: tuple[int, int] = (200, 115)) -> tuple[int, int, int, int]:
    """右下角保守水印区域：向内取 size，水印必然落在其中（快速模式用）。"""
    h, w = rgb.shape[:2]
    dw, dh = min(size[0], w), min(size[1], h)
    return (w - dw, h - dh, w, h)


def tight_rect(rgb: np.ndarray, wm_size: tuple[int, int] = (180, 90),
               margin: int = 15) -> tuple[int, int, int, int]:
    """水印的**紧致** bbox（smooth 模式用）。

    实测规律：水印是右下角**固定像素尺寸 + 恒定 15px 边距**，不随图缩放：
        1440x2560 → 156x71 px @ 边距 15/15
        2560x1536 → 165x76 px @ 边距 15/15
    取 (180,90) 保证覆盖。★ 不要外扩：实测外扩 8px 就让邻域色差从 0.6 涨到 8+，
    因为 inpaint/插值把周围的真实渐变也吞了进去。
    """
    h, w = rgb.shape[:2]
    x1, y1 = max(1, w - margin), max(1, h - margin)
    return (max(0, x1 - wm_size[0]), max(0, y1 - wm_size[1]), x1, y1)


def remove(rgb: np.ndarray, rect: tuple[int, int, int, int], dilate: int = 8,
           method: str = "ns", radius: int = 3) -> tuple[np.ndarray, float]:
    """cv2.inpaint 去水印。返回 (结果 RGB, 耗时秒)。"""
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = rect
    x0 = max(0, x0 - dilate); y0 = max(0, y0 - dilate)
    x1 = min(w, x1 + dilate); y1 = min(h, y1 + dilate)
    mask = np.zeros((h, w), np.uint8)
    mask[y0:y1, x0:x1] = 255
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    t = time.time()
    out = cv2.inpaint(bgr, mask, radius, flag)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB), time.time() - t


def remove_smooth(rgb: np.ndarray, rect: tuple[int, int, int, int],
                  feather: float = 2.0, noise: bool = True,
                  seed: int = 5) -> tuple[np.ndarray, float]:
    """高质量模式：IDW+Laplace 混合低频 + 匹配邻域颗粒 + 羽化。

    为什么不用 cv2.inpaint：它把边界梯度沿等照度线推进，在大面积平滑区会留下
    **方向性条纹／V 形折角**（肉眼可见的「糊块」），且边界是硬接缝。
    本函数三步：
      ① 低频：四边逆距离加权（IDW）与 Laplace 方程数值解各半
         —— Laplace 全局最平滑、无方向性伪影；IDW 保证边缘颜色准。两者混合取长补短。
      ② 高频：从左右邻域（排除花瓣/暗线等污染）统计颗粒 std，生成同幅度灰度相关噪声
         —— 补回 inpaint 丢失的颗粒感。
      ③ 羽化 2px 贴回，mask 外像素零改动。
    ★ rect 必须是**水印精确 bbox**，不要外扩（实测外扩会让邻域色差从 0.6 涨到 8+）。
    """
    t = time.time()
    H0, W0 = rgb.shape[:2]
    x0, y0, x1, y1 = rect
    x0 = max(0, min(x0, W0 - 1)); x1 = max(x0 + 2, min(x1, W0))
    y0 = max(0, min(y0, H0 - 1)); y1 = max(y0 + 2, min(y1, H0))
    H, W = y1 - y0, x1 - x0
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).astype(np.float32)

    # ① 低频
    top = bgr[max(0, y0 - 1), x0:x1]
    bot = bgr[min(H0 - 1, y1), x0:x1]
    lef = bgr[y0:y1, max(0, x0 - 1)]
    rig = bgr[y0:y1, min(W0 - 1, x1)]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    wy = yy / max(H - 1, 1); wx = xx / max(W - 1, 1); eps = 0.02
    Wt = 1 / (wy ** 2 + eps); Wb = 1 / ((1 - wy) ** 2 + eps)
    Wl = 1 / (wx ** 2 + eps); Wr = 1 / ((1 - wx) ** 2 + eps)
    low_idw = (top[None] * Wt[..., None] + bot[None] * Wb[..., None]
               + lef[:, None] * Wl[..., None] + rig[:, None] * Wr[..., None]) \
        / (Wt + Wb + Wl + Wr)[..., None]
    u = bgr.copy()
    u[y0:y1, x0:x1] = bgr[y0:y1, x0:x1].reshape(-1, 3).mean(0)
    for _ in range(1500):                      # Gauss-Seidel 解 Laplace 方程
        u[y0:y1, x0:x1] = 0.25 * (u[y0 - 1:y1 - 1, x0:x1] + u[y0 + 1:y1 + 1, x0:x1]
                                  + u[y0:y1, x0 - 1:x1 - 1] + u[y0:y1, x0 + 1:x1 + 1])
    low = 0.5 * low_idw + 0.5 * u[y0:y1, x0:x1]

    # ② 颗粒（匹配邻域 std，灰度噪声避免彩噪）
    if noise:
        sig = 4.0
        hf = bgr - cv2.GaussianBlur(bgr, (0, 0), sig)
        nb = np.concatenate([
            hf[y0:y1, max(0, x0 - 40):x0].reshape(-1, 3),
            hf[y0:y1, x1:min(W0, x1 + 40)].reshape(-1, 3)])
        if nb.size:
            tgt = float(nb.std())
            rng = np.random.default_rng(seed)
            nz = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 0.7)[:, :, None]
            nz = np.repeat(nz, 3, axis=2)
            low = low + nz * (tgt / max(float(nz.std()), 1e-3))

    # ③ 羽化贴回
    res = bgr.copy()
    res[y0:y1, x0:x1] = np.clip(low, 0, 255)
    mm = np.zeros((H0, W0), np.float32)
    mm[y0:y1, x0:x1] = 1.0
    if feather > 0:
        mm = cv2.GaussianBlur(mm, (0, 0), feather)
    out = res * mm[..., None] + bgr * (1 - mm[..., None])
    return cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_BGR2RGB), time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser(description="去水印（ImageGen 右下角水印）——本地方案")
    ap.add_argument("--src", help="输入图")
    ap.add_argument("--out", help="输出图")
    ap.add_argument("--src-dir", help="批量：输入目录")
    ap.add_argument("--out-dir", help="批量：输出目录")
    ap.add_argument("--suffix", default="", help="批量：只处理含该后缀的文件")
    ap.add_argument("--rect", default=None, help="手动区域 x0,y0,x1,y1（默认＝右下角固定区域）")
    ap.add_argument("--auto", action="store_true", help="用自动检测代替固定区域（仅背景干净时可靠）")
    ap.add_argument("--dilate", type=int, default=8, help="快速模式区域外扩像素（默认 8；smooth 模式忽略）")
    ap.add_argument("--method", default="ns", choices=["ns", "telea"])
    ap.add_argument("--radius", type=int, default=3)
    ap.add_argument("--smooth", action="store_true",
                    help="高质量模式（推荐）：Laplace/IDW 低频 + 颗粒补偿 + 羽化。慢 1~2s，无糊块")
    ap.add_argument("--feather", type=float, default=2.0, help="smooth 模式羽化半径（默认 2.0px）")
    ap.add_argument("--no-noise", action="store_true", help="smooth 模式不补颗粒")
    ap.add_argument("--preview", action="store_true", help="额外输出带框预览便于目验")
    a = ap.parse_args()

    jobs: list[tuple[str, str]] = []
    if a.src and a.out:
        jobs.append((a.src, a.out))
    elif a.src_dir and a.out_dir:
        for fn in sorted(os.listdir(a.src_dir)):
            if not fn.lower().endswith(".png"):
                continue
            if a.suffix and a.suffix not in fn:
                continue
            jobs.append((os.path.join(a.src_dir, fn), os.path.join(a.out_dir, fn)))
        if not jobs:
            sys.exit("目录里没有匹配的文件")
    else:
        sys.exit("需要 --src/--out 或 --src-dir/--out-dir")

    for src, out in jobs:
        rgb = imread_u(src)
        if a.rect:
            rect = tuple(int(v) for v in a.rect.split(","))
        elif a.auto:
            r = detect_watermark(rgb)
            if r is None:
                print("[跳过] %s 未检出右下角水印" % os.path.basename(src))
                continue
            rect = tuple(int(v) for v in r)
        elif a.smooth:
            rect = tight_rect(rgb)
        else:
            rect = default_rect(rgb)
        res, dt = remove(rgb, rect, a.dilate, a.method, a.radius) if not a.smooth \
            else remove_smooth(rgb, rect, a.feather, not a.no_noise)
        imwrite_u(out, res)
        h, w = rgb.shape[:2]
        by0, by1 = int(h * 0.55), int(h * 0.90)
        bx0, bx1 = int(w * 0.55), int(w * 0.95)
        bg = rgb[by0:by1, bx0:bx1].reshape(-1, 3).mean(0)
        x0, y0, x1, y1 = rect
        before = np.abs(rgb[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16) - bg).mean()
        after = np.abs(res[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16) - bg).mean()
        mode = "smooth" if a.smooth else a.method
        print("[完成] %-38s 区域%s 模式%-6s 耗时 %.3fs  水印区偏离邻域 %.1f→%.1f" % (
            os.path.basename(src), rect, mode, dt, before, after))
        if a.preview:
            pv = res.copy()
            cv2.rectangle(pv, (rect[0] - a.dilate, rect[1] - a.dilate),
                          (rect[2] + a.dilate, rect[3] + a.dilate), (255, 80, 80), 3)
            imwrite_u(out.replace(".png", "_预览.png"), pv)


if __name__ == "__main__":
    main()
