# -*- coding: utf-8 -*-
"""从 raws/樱.mp3 截出目标段 + 上下文，拼成送去云卡的输入。

为什么不是直接传 樱.wav/樱2.wav 的合并：
  那两段是从长档剪出来的（相关系数 1.0000，见 locate_clips.py 的输出）。
  单独喂 2~3 秒的短片段给 BS-Roformer，它会：
    · 用 reflect pad 补足 chunk_size=352800(8s)，补出来的内容是"假的"
    · 首尾 fade 窗口（fade_size = chunk/10 ≈ 0.8s）会吃掉真实语音
  给足上下文就没有这两个问题。

产出两份输入：
  A) 上下文版：两段各取 [start-8s, end+8s]，中间用静音连起来
  B) 精确版：两段本身 + 各 3s 上下文（更短，处理更快，用于对照）
"""
from __future__ import annotations

import os

import numpy as np
import soundfile as sf

ROOT = r"D:\0Coding\pythonworkspace\My_program\笑樱-galgame\素材基准\音频\语音"
OUT = r"D:\0Coding\pythonworkspace\My_program\笑樱-galgame\.workbuddy\tmp\ref"

# 由 locate_clips.py 实测得出（相关系数 1.0000）
CLIPS = [
    ("樱",   65.23, 68.31),
    ("樱2", 295.92, 298.59),
]


def main():
    os.makedirs(OUT, exist_ok=True)
    src = os.path.join(ROOT, "raws", "樱.mp3")
    x, sr = sf.read(src, always_2d=True, dtype="float32")
    total = len(x) / sr
    print(f">>> 源 {src}  {total:.1f}s {sr}Hz {x.shape[1]}ch")

    for label, ctx in (("A_ctx8", 8.0), ("B_ctx3", 3.0)):
        parts = []
        gap = np.zeros((int(round(0.35 * sr)), x.shape[1]), dtype="float32")
        meta = []
        for n, (name, a, b) in enumerate(CLIPS):
            s = max(0.0, a - ctx)
            e = min(total, b + ctx)
            i0, i1 = int(round(s * sr)), int(round(e * sr))
            seg = x[i0:i1]
            parts.append(gap if n else seg)
            if n:
                parts.append(seg)
            meta.append((name, a, b, s, e))
        merged = np.concatenate(parts, axis=0)
        out = os.path.join(OUT, f"樱_输入_{label}.wav")
        sf.write(out, merged, sr, subtype="PCM_16")
        print(f"\n>>> {out}")
        print(f"    总时长 {len(merged)/sr:.2f}s")
        for name, a, b, s, e in meta:
            print(f"    {name:<4} 目标 {a:.2f}-{b:.2f}s  →  取 {s:.2f}-{e:.2f}s"
                  f"（+{ctx:.0f}s 上下文）")
        print(f"    峰值 {20*np.log10(max(float(np.abs(merged).max()),1e-10)):.1f} dBFS")

    # 对照：原始两段直接拼接（不带走上下文）
    plain = []
    gap = np.zeros((int(round(0.35 * sr)), x.shape[1]), dtype="float32")
    for n, (name, a, b) in enumerate(CLIPS):
        p = os.path.join(ROOT, f"{name}.wav")
        c, csr = sf.read(p, always_2d=True, dtype="float32")
        assert csr == sr, f"{p} 采样率 {csr} != {sr}"
        plain.append(gap)
        plain.append(c)
    merged = np.concatenate(plain[1:], axis=0)
    out = os.path.join(OUT, "樱_输入_C_裸拼接.wav")
    sf.write(out, merged, sr, subtype="PCM_16")
    print(f"\n>>> {out}\n    总时长 {len(merged)/sr:.2f}s（原始两段直接拼，作对照）")


if __name__ == "__main__":
    main()
