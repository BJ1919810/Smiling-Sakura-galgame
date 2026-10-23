# -*- coding: utf-8 -*-
"""把一条参考音频的音量**对齐到基准**（默认对齐樱的参考）。

为什么需要：参考音频的响度会被 IndexTTS 带进输出 —— 各角色响度不齐，
游戏里就会出现「阳太比樱响 9 dB」这种问题。
口径照抄 `normalize-voice.py`：有效响度 = 100ms block 的 RMS，取较响的 20%。

与 `extract-align-ref.py` 的分工：
  · extract-align-ref = 先把上下文版**裁出目标段**再对齐（有 layout 时用）
  · 本脚本 = **只对齐、不裁剪**（片段本身就是目标段，如单文件素材）

用法：
    python tools/align-ref-loudness.py --src 亚里沙_原始_vocal.wav --out 亚里沙_ref.wav
    # 默认对齐到 .workbuddy/tmp/ref/樱_merged_raw.wav
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_REF = os.path.join(ROOT, '.workbuddy', 'tmp', 'ref', '樱_merged_raw.wav')

PEAK_CEIL_DB = -1.0
MAX_GAIN_DB = 20.0


def db(v: float) -> float:
    return 20 * np.log10(max(float(v), 1e-10))


def eff_loudness(x: np.ndarray, sr: int) -> float:
    mono = x.mean(axis=1) if x.ndim > 1 else x
    blk = int(0.1 * sr)
    n = len(mono) // blk
    if n == 0:
        return -120.0
    r = np.array([np.sqrt((mono[i * blk:(i + 1) * blk] ** 2).mean()) for i in range(n)])
    k = max(1, int(round(n * 0.2)))
    return db(float(np.sort(r)[-k:].mean()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--target-eff-db', type=float, default=None)
    ap.add_argument('--match-ref', default=DEFAULT_REF)
    ap.add_argument('--no-gain', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.src):
        print(f'✗ 输入不存在：{args.src}')
        return 1
    x, sr = sf.read(args.src, always_2d=True, dtype='float32')
    eff = eff_loudness(x, sr)
    pk = db(float(np.abs(x).max()))
    print(f'>>> {os.path.basename(args.src)}  {len(x)/sr:.3f}s {sr}Hz {x.shape[1]}ch')
    print(f'    有效响度 {eff:.1f} dB  峰值 {pk:.1f} dBFS')
    if len(x) / sr > 15.0:
        print(f'    ⚠️ 超过 IndexTTS 的 15s 参考上限（{len(x)/sr:.2f}s）')

    if not args.no_gain:
        if args.target_eff_db is not None:
            target, desc = args.target_eff_db, '命令行指定'
        elif os.path.isfile(args.match_ref):
            ry, rsr = sf.read(args.match_ref, always_2d=True, dtype='float32')
            target, desc = eff_loudness(ry, rsr), os.path.basename(args.match_ref)
        else:
            print(f'✗ 基准不存在：{args.match_ref}')
            return 1
        gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, target - eff))
        if pk + gain > PEAK_CEIL_DB:
            capped = PEAK_CEIL_DB - pk
            print(f'    ⚠️ 峰值保护：{gain:+.2f} → {capped:+.2f} dB')
            gain = capped
        print(f'>>> 对齐目标 {target:.1f} dB（基准 {desc}）⇒ 增益 {gain:+.2f} dB')
        x = x * (10 ** (gain / 20.0))
        print(f'>>> 对齐后 有效 {eff_loudness(x, sr):.1f} dB  '
              f'峰值 {db(float(np.abs(x).max())):.1f} dBFS  '
              f'削顶 {int((np.abs(x) >= 0.999).sum())}')

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sf.write(args.out, x, sr, subtype='PCM_16')
    print(f'\n>>> 输出 {args.out}  ({os.path.getsize(args.out):,} B)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
