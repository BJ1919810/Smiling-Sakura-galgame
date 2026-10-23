# -*- coding: utf-8 -*-
"""在 raws/<角色>.mp3 里定位各切片（<角色>.wav / <角色>2.wav …）的时间位置（只读）。

用途：给 `prep-voice-ref.py` 提供上下文裁剪所需的坐标，以及核对切片确实取自这条长档。

方法：对长档做 **FFT 归一化互相关**，找与切片波形最匹配的位置。
切片本来就是从长档剪的 ⇒ 相关系数应接近 1.0000。
⚠️ 若切片经过重编码/降噪，相关系数会明显下降，届时**不要硬用上下文版**。

★ 为什么需要位置：BS-Roformer 的 chunk_size = 352800 样本（8s），
  比参考片段长；若只喂 2–3 秒短片段，它会用 reflect pad 补「假内容」，
  且首尾 fade 窗口（chunk/10 ≈ 0.8s）会吃掉真实语音。
  给足上下文再分离、最后从中间裁出目标段，才干净。

用法：
    python tools/locate-voice-clips.py --char 阳太
    python tools/locate-voice-clips.py --char 樱 --clips 樱.wav 樱2.wav
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VOICE_DIR = os.path.join(ROOT, '素材基准', '音频', '语音')
RAW_DIR = os.path.join(VOICE_DIR, 'raws')


def load_mono(path: str, sr: int | None = None) -> tuple[np.ndarray, int]:
    x, s = sf.read(path, always_2d=True, dtype='float32')
    x = x.mean(axis=1)
    if sr is not None and s != sr:
        n_out = int(round(len(x) * sr / s))
        idx = np.linspace(0, len(x) - 1, n_out)
        lo = np.floor(idx).astype(int)
        hi = np.minimum(lo + 1, len(x) - 1)
        w = idx - lo
        x = x[lo] * (1 - w) + x[hi] * w
    return x, (sr or s)


def find_position(long_x: np.ndarray, clip_x: np.ndarray) -> tuple[int | None, float]:
    """FFT 归一化互相关 → (最佳起始样本, 相关系数)。"""
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
    win_energy = sq[n:] - sq[:-n]
    corr = cc / (np.sqrt(np.maximum(win_energy, 1e-12)) * cn)
    best = int(np.argmax(corr))
    return best, float(corr[best])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', required=True, help='角色名（对应 raws/<角色>.mp3）')
    ap.add_argument('--clips', nargs='*', default=None,
                    help='切片文件名，默认 <角色>.wav <角色>2.wav …（有几个就找几个）')
    ap.add_argument('--raw', default=None, help='源长档路径，默认 raws/<角色>.mp3')
    args = ap.parse_args()

    raw_path = args.raw or os.path.join(RAW_DIR, f'{args.char}.mp3')
    if not os.path.isfile(raw_path):
        print(f'✗ 源长档不存在：{raw_path}')
        return 1

    clips = args.clips
    if not clips:
        clips = []
        for suf in [''] + [str(i) for i in range(2, 10)]:
            name = f'{args.char}{suf}.wav'
            if os.path.isfile(os.path.join(VOICE_DIR, name)):
                clips.append(name)
    if not clips:
        print(f'✗ 没找到 {args.char} 的切片（<角色>.wav / <角色>2.wav …）')
        return 1

    print(f'>>> 载入长档 {raw_path}')
    raw, raw_sr = load_mono(raw_path)
    print(f'    时长 {len(raw) / raw_sr:.1f}s  {raw_sr}Hz\n')

    results = []
    ok = True
    for name in clips:
        p = name if os.path.isabs(name) else os.path.join(VOICE_DIR, name)
        if not os.path.isfile(p):
            print(f'✗ 切片不存在：{p}')
            ok = False
            continue
        clip, clip_sr = load_mono(p, sr=raw_sr)
        dur = len(clip) / raw_sr
        idx, score = find_position(raw, clip)
        if idx is None:
            print(f'>>> {name}：匹配失败（可能全是静音 / 比长档还长）')
            ok = False
            continue
        start = idx / raw_sr
        end = start + dur
        results.append((name, start, end, score))
        flag = '✓' if score >= 0.99 else ('⚠️' if score >= 0.9 else '✗')
        print(f'>>> {name}  {dur:.2f}s')
        print(f'    位置 {start:.2f}s → {end:.2f}s   相关系数 {score:.4f}  {flag}')

        def db(v: float) -> float:
            return 20 * np.log10(max(float(v), 1e-10))

        seg = raw[idx : idx + len(clip)]
        print(f'    长档该段 RMS {db(np.sqrt((seg ** 2).mean())):.1f} dBFS '
              f'/ 切片 RMS {db(np.sqrt((clip ** 2).mean())):.1f} dBFS')
        if score < 0.9:
            ok = False

    if results:
        print('\n' + '=' * 56)
        print('# CLIPS = [' )
        for name, s, e, sc in results:
            base = os.path.splitext(name)[0]
            print(f'#     ("{base}", {s:.2f}, {e:.2f}),   # r={sc:.4f}')
        print('# ]')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
