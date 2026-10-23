# -*- coding: utf-8 -*-
"""把多条参考音频合并成一条（本地跑，产出去云卡处理）。

为什么合并：
  主人要把 樱.wav(3.09s) + 樱2.wav(2.68s) 合起来当 IndexTTS 的参考音频。
  IndexTTS-2.5 参考音频上限 15s（infer_v2_5.py:627），5.8s 离上限还很远。

处理约定：
  · 统一重采样到 48000（两条原始都是 48k，不引入额外损失）
  · 声道数取输入最大值，少的做复制补齐
  · 段间插 0.35s 静音：太短会让 ASR 把两句黏成一句，太长浪费参考窗口
    0.35s 是项目内 ASR 的 min_silence_duration_ms=700 的一半 —— 半句内停顿
    (~0.2s) 不会被误切，句间停顿(>0.7s) 也不会被误连，取中间值最稳。
  · **不做任何响度/EQ 处理**，保持原始素材（后续交给专门的管线）

用法：
    python tools/merge-voice-ref.py --out <输出> <输入1> <输入2> ...
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import soundfile as sf


def load_uniform(path: str, target_sr: int, target_ch: int) -> np.ndarray:
    x, sr = sf.read(path, always_2d=True, dtype="float64")
    if sr != target_sr:
        # 简单线性重采样（参考音频很短，且我们尽量不让这一步发生）
        n_out = int(round(len(x) * target_sr / sr))
        idx = np.linspace(0, len(x) - 1, n_out)
        lo = np.floor(idx).astype(int)
        hi = np.minimum(lo + 1, len(x) - 1)
        w = (idx - lo)[:, None]
        x = x[lo] * (1 - w) + x[hi] * w
        print(f"    [重采样] {os.path.basename(path)}: {sr} -> {target_sr}")
    if x.shape[1] != target_ch:
        if x.shape[1] == 1:
            x = np.repeat(x, target_ch, axis=1)
        else:
            x = x[:, :target_ch]
        print(f"    [声道] {os.path.basename(path)} -> {target_ch}ch")
    return x


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gap", type=float, default=0.35, help="段间静音秒数")
    ap.add_argument("--sr", type=int, default=48000)
    args = ap.parse_args()

    for p in args.inputs:
        if not os.path.isfile(p):
            sys.exit(f"[错误] 输入不存在：{p}")

    # 定采样率与声道：取所有输入的最大声道数
    infos = [sf.info(p) for p in args.inputs]
    target_ch = max(i.channels for i in infos)
    target_sr = args.sr

    print(f">>> 目标：{target_sr}Hz {target_ch}ch，间隔 {args.gap}s")
    parts = []
    gap = np.zeros((int(round(args.gap * target_sr)), target_ch), dtype="float64")
    for n, p in enumerate(args.inputs):
        dur = infos[n].frames / infos[n].samplerate
        print(f"  [{n+1}] {os.path.basename(p)}  {dur:.2f}s  {infos[n].samplerate}Hz {infos[n].channels}ch")
        x = load_uniform(p, target_sr, target_ch)
        if n > 0:
            parts.append(gap)
        parts.append(x)

    merged = np.concatenate(parts, axis=0)
    total = len(merged) / target_sr
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sf.write(args.out, merged, target_sr, subtype="PCM_16")

    peak = float(np.abs(merged).max())
    rms = float(np.sqrt((merged ** 2).mean()))
    print(f"\n>>> 输出 {args.out}")
    print(f"    时长 {total:.2f}s | 峰值 {20*np.log10(max(peak,1e-10)):.1f} dBFS"
          f" | RMS {20*np.log10(max(rms,1e-10)):.1f} dBFS")
    if total > 15.0:
        print(f"    ⚠️ 超过 IndexTTS-2.5 的 15s 上限！需要裁短。")


if __name__ == "__main__":
    main()
