# -*- coding: utf-8 -*-
"""音色漂移诊断：把一个音频**按时间分段**，逐段与参考比谱包络。

动机（主人反馈）：`これが戦争なわけねえだろ、災害だよ災害。` 的后半句「災害」处
**音色丢了**。先量化「音色随时间是否漂移」，再谈原因。

口径**复用 `tools/voice_spectrum.py`**：
  · 必须**倒谱平滑**（liftering）—— 单句 LTAS 被 F0 谐波梳支配，
    直接比 1/3 倍频程会把「音高不同」误读成「音色不同」
  · 参考与输出重采样到同一采样率，并以同一锚点频率对齐（比的是**倾斜**）
  · 跳过静音帧，否则静音段污染长时谱

输出：逐窗口的「谱质心」+「与参考的谱差」，看后半句是否偏离。

用法：
    python tools/_analyze_timbre.py --ref <参考.wav> --src <待查.wav> --win 0.6 --hop 0.3
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REF_DIR = os.path.join(ROOT, '.workbuddy', 'tmp', 'ref')

OCTAVE_CENTERS = [80, 110, 160, 220, 320, 450, 640, 900, 1250,
                  1800, 2500, 3500, 5000, 7000, 10000]
TARGET_SR = 22050


def load_mono(path: str, sr: int = TARGET_SR) -> np.ndarray:
    x, s = sf.read(path, always_2d=True, dtype='float64')
    x = x.mean(axis=1)
    if s != sr:  # 线性重采样（与 voice_spectrum.py 的 resample_poly 同目的）
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(s, sr)
        x = resample_poly(x, sr // g, s // g)
    return x


def envelope(x: np.ndarray, sr: int, nfft: int = 2048, hop: int = 512,
             lifter_ms: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """倒谱平滑的谱包络（照抄 voice_spectrum.envelope，nfft 调小以适应短窗）。"""
    win = np.hanning(nfft)
    acc = np.zeros(nfft // 2 + 1)
    cnt = 0
    for i in range(0, max(0, len(x) - nfft), hop):
        seg = x[i:i + nfft]
        if np.sqrt((seg ** 2).mean()) < 1e-4:
            continue
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        cnt += 1
    if cnt == 0:
        return np.fft.rfftfreq(nfft, 1 / sr), np.zeros(nfft // 2 + 1)
    lt = acc / cnt + 1e-20
    ceps = np.fft.irfft(np.log(lt))
    cut = int(lifter_ms / 1000.0 * sr)
    ceps[cut:len(ceps) - cut] = 0
    env = np.fft.rfft(ceps).real[: nfft // 2 + 1]
    return np.fft.rfftfreq(nfft, 1 / sr), 20 * env / np.log(10)


def band_mean(f: np.ndarray, e: np.ndarray, c: float, half_oct: float = 0.3) -> float:
    i0, i1 = np.searchsorted(f, c / 2 ** half_oct), np.searchsorted(f, c * 2 ** half_oct)
    return float(e[i0:i1].mean()) if i1 > i0 else float('nan')


def centroid(f: np.ndarray, e: np.ndarray) -> float:
    """谱质心（Hz）——「音色变亮/变暗」的粗指标。"""
    p = 10 ** (e / 10.0)
    m = f <= 8000
    return float((f[m] * p[m]).sum() / max(p[m].sum(), 1e-20))


def profile(x: np.ndarray, sr: int, anchor: float = 640.0) -> tuple[np.ndarray, float]:
    """返回 (相对锚点的谱包络, 谱质心)。"""
    f, e = envelope(x, sr)
    a = band_mean(f, e, anchor)
    return np.array([band_mean(f, e, c) - a for c in OCTAVE_CENTERS]), centroid(f, e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default=os.path.join(REF_DIR, 'yota_ref_A.wav'))
    ap.add_argument('--src', required=True)
    ap.add_argument('--win', type=float, default=0.6)
    ap.add_argument('--hop', type=float, default=0.3)
    args = ap.parse_args()

    ref = load_mono(args.ref)
    src = load_mono(args.src)
    rp, rc = profile(ref, TARGET_SR)
    print(f'参考 {os.path.basename(args.ref)}  {len(ref)/TARGET_SR:.2f}s  '
          f'谱质心 {rc:.0f} Hz')
    print(f'待查 {os.path.basename(args.src)}  {len(src)/TARGET_SR:.2f}s\n')
    print('参考包络（相对 640Hz）:')
    print('  ' + '  '.join(f'{c}:{v:+.1f}' for c, v in zip(OCTAVE_CENTERS, rp)))

    n = int(args.win * TARGET_SR)
    h = int(args.hop * TARGET_SR)
    print(f'\n{"窗口(s)":<16}{"谱质心":>9}{"Δtone":>9}{"谱距":>9}  最大偏移频段')
    print('-' * 66)
    rows = []
    for i in range(0, max(1, len(src) - n + 1), h):
        seg = src[i:i + n]
        sp, sc = profile(seg, TARGET_SR)
        d = sp - rp
        rows.append((i / TARGET_SR, sc, d))
        j = int(np.argmax(np.abs(d)))
        bar = '#' * int(abs(d[j]))
        print(f'{i/TARGET_SR:>5.2f}–{(i+n)/TARGET_SR:<5.2f}      {sc:>9.0f}'
              f'{sc - rc:>+9.0f}{np.abs(d).mean():>9.2f}  {OCTAVE_CENTERS[j]}Hz '
              f'{d[j]:+.1f} {bar}')

    print('\n' + '=' * 66)
    if len(rows) >= 2:
        cs = [r[1] for r in rows]
        # ★ 判据（2026-09-19 修正过一次）：
        #   ⛔ 别用「与参考的**绝对**谱距」——短窗谱估计方差大，且语调本身就会移动共振峰，
        #      实测**正常条目**也能到 22.9 dB，用它必然全报异常（我踩过）。
        #   改用两个**只在异常时才会越界**的指标：
        #     ① 谱质心：正常日语男声约 400–1200 Hz，> 1500 Hz 视为异常
        #     ② 高频偏移：≥2500 Hz 相对锚点 640 Hz 的**绝对**倾斜
        #        （正常语音该处应低于 640 Hz 20~50 dB；被 +20 dB 以上＝异常明亮/噪声）
        hi_idx = [k for k, c in enumerate(OCTAVE_CENTERS) if c >= 2500]
        hi_max = [max(abs(r[2][k]) for k in hi_idx) for r in rows]
        i_hi = int(np.argmax(hi_max))
        print(f'谱质心范围 {min(cs):.0f} – {max(cs):.0f} Hz')
        print(f'高频(≥2500Hz)最大偏移 {max(hi_max):.1f} dB（窗口 {rows[i_hi][0]:.2f}s）')
        bad_c = [t for t, c, _ in rows if c > 1500]
        bad_h = [rows[i][0] for i, v in enumerate(hi_max) if v > 20]
        if bad_c or bad_h:
            print(f'⚠️ 异常：谱质心越界窗口 '
                  f'{", ".join(f"{t:.2f}s" for t in bad_c) or "无"}；'
                  f'高频爆表窗口 {", ".join(f"{t:.2f}s" for t in bad_h) or "无"}')
        else:
            print('✓ 未见异常（谱质心与高频均在正常范围）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
