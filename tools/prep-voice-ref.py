# -*- coding: utf-8 -*-
"""生成某角色的「参考音频输入」——本地拼接，产出去做人声分离。

产出三份（沿用樱那次的命名习惯）：
  <角色>_输入_A_ctx8.wav     两段各取 ±8s 上下文，段间 0.35s 静音
  <角色>_输入_B_ctx3.wav     两段各取 ±3s 上下文
  <角色>_输入_C_裸拼接.wav    两段本身直接拼（对照 / 快速试听）

★ 为什么要有「上下文版」（不是多余）：
  BS-Roformer 的 chunk_size = 352800 样本（**8s**），比 6~7s 的参考片段长。
  只喂裸拼接会：
    · 用 **reflect pad** 补足 8s —— 补出来的是「假内容」；
    · 首尾 **fade 窗口**（chunk_size/10 ≈ 0.8s）会吃掉真实语音。
  给足上下文、分离完再从中间裁出目标段，才是干净的。

⚠️ 采样率默认取**源长档的原生采样率**（不重采样，少一层损失）。
   樱是 48k（源就是 48k）、阳太是 44.1k —— 两者不同没关系，
   IndexTTS 内部本来就会重采样到自己的工作采样率。

用法：
    python tools/prep-voice-ref.py --char 阳太
    python tools/prep-voice-ref.py --char 阳太 --ctx 8 3 --gap 0.35
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VOICE_DIR = os.path.join(ROOT, '素材基准', '音频', '语音')
RAW_DIR = os.path.join(VOICE_DIR, 'raws')
OUT_DIR = os.path.join(ROOT, '.workbuddy', 'tmp', 'ref')


def load_any(path: str, sr: int | None = None) -> tuple[np.ndarray, int]:
    """读成 2D (frames, channels) —— **保留声道**，供输出用。"""
    x, s = sf.read(path, always_2d=True, dtype='float32')
    if sr is not None and s != sr:
        n_out = int(round(len(x) * sr / s))
        idx = np.linspace(0, len(x) - 1, n_out)
        lo = np.floor(idx).astype(int)
        hi = np.minimum(lo + 1, len(x) - 1)
        w = (idx - lo)[:, None]
        x = x[lo] * (1 - w) + x[hi] * w
    return x, (sr or s)


def to_stereo(x: np.ndarray) -> np.ndarray:
    """统一成 2 声道。

    ★ 必须立体声：BS-Roformer 的 config 是 `stereo: True`，
      mono 输入会在 `demix_track` 里 `mix.shape[1]` 越界报
      `IndexError: tuple index out of range`（踩过）。
    """
    if x.shape[1] == 2:
        return x
    if x.shape[1] == 1:
        return np.repeat(x, 2, axis=1)
    return x[:, :2]


def find_position(long_x: np.ndarray, clip_x: np.ndarray) -> tuple[int | None, float]:
    """FFT 归一化互相关（同 tools/locate-voice-clips.py 的算法）。"""
    c = clip_x.astype(np.float64)
    c = c - c.mean()
    cn = float(np.linalg.norm(c))
    if cn < 1e-9 or len(c) >= len(long_x):
        return None, 0.0
    L = long_x.astype(np.float64)
    Lm = L - L.mean()
    n = len(c)
    fft_size = 1 << int(np.ceil(np.log2(len(L) + n)))
    cc = np.fft.irfft(np.fft.rfft(Lm, fft_size) * np.conj(np.fft.rfft(c, fft_size)),
                      fft_size)[: len(L) - n + 1]
    sq = np.concatenate([[0.0], np.cumsum(Lm ** 2)])
    corr = cc / (np.sqrt(np.maximum(sq[n:] - sq[:-n], 1e-12)) * cn)
    best = int(np.argmax(corr))
    return best, float(corr[best])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', required=True)
    ap.add_argument('--clips', nargs='*', default=None)
    ap.add_argument('--ctx', nargs='+', type=float, default=[8.0, 3.0],
                    help='上下文秒数，每个值产出一份（默认 8 3）')
    ap.add_argument('--gap', type=float, default=0.35, help='段间静音秒数')
    args = ap.parse_args()

    raw_path = os.path.join(RAW_DIR, f'{args.char}.mp3')
    if not os.path.isfile(raw_path):
        print(f'✗ 源长档不存在：{raw_path}')
        return 1

    names = args.clips
    if not names:
        names = []
        for suf in [''] + [str(i) for i in range(2, 10)]:
            if os.path.isfile(os.path.join(VOICE_DIR, f'{args.char}{suf}.wav')):
                names.append(f'{args.char}{suf}.wav')
    if not names:
        print(f'✗ 没找到 {args.char} 的切片')
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)

    print(f'>>> 载入长档 {raw_path}')
    raw, sr = load_any(raw_path)  # 2D，**保留声道**
    raw_mono = raw.mean(axis=1)  # 定位用单声道
    total = len(raw) / sr
    print(f'    {total:.1f}s  {sr}Hz {raw.shape[1]}ch  →  输出 {sr}Hz 2ch（保持原生，不重采样）\n')

    # 1) 定位 + 校验
    clips: list[tuple[str, float, float, np.ndarray]] = []
    for name in names:
        p = os.path.join(VOICE_DIR, name)
        y, csr = sf.read(p, always_2d=True, dtype='float32')
        assert csr == sr, f'{p} 采样率 {csr} != 源 {sr}（本脚本要求同源采样率）'
        cm = y.mean(axis=1)
        idx, score = find_position(raw_mono, cm)
        if idx is None:
            print(f'✗ {name} 定位失败')
            return 1
        start = idx / sr
        end = start + len(cm) / sr
        flag = '✓' if score >= 0.99 else '⚠️'
        print(f'  {name:<12} {start:>7.2f}s → {end:>7.2f}s   r={score:.4f} {flag}')
        clips.append((os.path.splitext(name)[0], start, end, to_stereo(y)))

    # 2) 上下文版（按给定顺序命名 A/B/C…）——★ 全程 2D，输出保持立体声
    #
    # ★ 同时落一份 layout.json：记录**目标段在各变体文件中的位置**。
    #   分离不改变时间轴 ⇒ 分离完成后按这份坐标裁出目标段即可（给 extract 用）。
    layout: dict = {
        'char': args.char, 'sr': sr, 'gap': args.gap,
        'clips': [{'name': n, 'src_start': a, 'src_end': b, 'dur': b - a}
                  for n, a, b, _ in clips],
        'variants': {},
    }
    gap = np.zeros((int(round(args.gap * sr)), 2), dtype='float32')
    for i, ctx in enumerate(args.ctx):
        tag = chr(ord('A') + i)
        parts = []
        cursor = 0.0
        segs_meta = []
        for n, (nm, a, b, _) in enumerate(clips):
            s = max(0.0, a - ctx)
            e = min(total, b + ctx)
            seg = to_stereo(raw[int(round(s * sr)):int(round(e * sr))])
            if n:
                parts.append(gap)
                cursor += args.gap
            parts.append(seg)
            segs_meta.append({'name': nm, 'file_start': cursor + (a - s), 'dur': b - a})
            cursor += len(seg) / sr
        merged = np.concatenate(parts, axis=0)
        out = os.path.join(OUT_DIR, f'{args.char}_输入_{tag}_ctx{ctx:g}.wav')
        sf.write(out, merged, sr, subtype='PCM_16')
        pk = 20 * np.log10(max(float(np.abs(merged).max()), 1e-10))
        print(f'\n>>> {os.path.basename(out)}  {len(merged)/sr:.2f}s  峰值 {pk:.1f} dBFS')
        for name, a, b, _ in clips:
            print(f'    {name:<10} 目标 {a:.2f}-{b:.2f}s → 取 '
                  f'{max(0.0, a - ctx):.2f}-{min(total, b + ctx):.2f}s（±{ctx:g}s 上下文）')
        layout['variants'][f'{tag}_ctx{ctx:g}'] = {
            'ctx': ctx, 'file': os.path.basename(out),
            'total': len(merged) / sr, 'segs': segs_meta,
        }

    # 3) 裸拼接（对照）
    parts = []
    cursor = 0.0
    segs_meta = []
    for n, (nm, a, b, data) in enumerate(clips):
        if n:
            parts.append(gap)
            cursor += args.gap
        parts.append(data)
        segs_meta.append({'name': nm, 'file_start': cursor, 'dur': b - a})
        cursor += len(data) / sr
    merged = np.concatenate(parts, axis=0)
    out = os.path.join(OUT_DIR, f'{args.char}_输入_C_裸拼接.wav')
    sf.write(out, merged, sr, subtype='PCM_16')
    pk = 20 * np.log10(max(float(np.abs(merged).max()), 1e-10))
    print(f'\n>>> {os.path.basename(out)}  {len(merged)/sr:.2f}s  峰值 {pk:.1f} dBFS')
    sf.write(os.path.join(OUT_DIR, f'{args.char}_merged_raw.wav'), merged, sr, subtype='PCM_16')
    layout['variants']['C_裸拼接'] = {
        'ctx': 0.0, 'file': os.path.basename(out),
        'total': len(merged) / sr, 'segs': segs_meta,
    }
    print('（裸拼接另存一份 _merged_raw，便于比对）')

    lp = os.path.join(OUT_DIR, f'{args.char}_layout.json')
    with open(lp, 'w', encoding='utf-8') as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    print(f'\n>>> 布局元数据 {os.path.basename(lp)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
