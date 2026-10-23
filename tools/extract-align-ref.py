# -*- coding: utf-8 -*-
"""从分离结果里**裁出目标段**，并把音量**对齐到基准参考**。

为什么必须裁：上下文版有 19~39s，而 IndexTTS-2.5 的参考音频**上限 15s**
（`infer_v2_5.py:627`），直接送会被拒或被截。
分离**不改变时间轴**，所以按 `prep-voice-ref.py` 落下的 `_layout.json` 坐标裁即可。

为什么必须对齐音量：参考音频的响度会被 IndexTTS 带进输出 ——
本批实测阳太参考比樱响 **~9.5 dB**，不对齐的话两个角色在游戏里音量不一致。
口径**照抄 `normalize-voice.py`**：有效响度 = 100ms block 的 RMS 取较响 20%。

用法：
    python tools/extract-align-ref.py --layout .workbuddy/tmp/ref/阳太_layout.json \
        --variant A_ctx8 --sep-dir .workbuddy/tmp/ref/out --suffix _vocal \
        --out .workbuddy/tmp/ref/yota_ref_A.wav
    # 加 --no-gain 只看裁剪结果；--target-eff-db 覆盖对齐目标
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
DEFAULT_REF = os.path.join(ROOT, '.workbuddy', 'tmp', 'ref', '樱_merged_raw.wav')

# 峰值保护：对齐后峰值不得高于此值（留一点余量）
PEAK_CEIL_DB = -1.0
MAX_GAIN_DB = 20.0


def db(v: float) -> float:
    return 20 * np.log10(max(float(v), 1e-10))


def eff_loudness(x: np.ndarray, sr: int) -> float:
    """有效响度（项目口径）：100ms block 的 RMS，取较响的 20% 求均值。"""
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
    ap.add_argument('--layout', required=True, help='prep-voice-ref.py 产出的 _layout.json')
    ap.add_argument('--variant', required=True, help='如 A_ctx8 / B_ctx3 / C_裸拼接')
    ap.add_argument('--sep-dir', required=True, help='分离结果目录')
    ap.add_argument('--suffix', default='_vocal', help='分离文件后缀（默认 _vocal）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--target-eff-db', type=float, default=None,
                    help='对齐目标（dB）；不给则自动测 --match-ref 的有效响度')
    ap.add_argument('--match-ref', default=DEFAULT_REF,
                    help=f'基准参考（默认 {os.path.basename(DEFAULT_REF)}）')
    ap.add_argument('--no-gain', action='store_true', help='只裁，不做音量对齐')
    args = ap.parse_args()

    with open(args.layout, encoding='utf-8') as f:
        layout = json.load(f)
    if args.variant not in layout['variants']:
        print(f'✗ 变体不存在：{args.variant}（可选：{list(layout["variants"])}）')
        return 1
    var = layout['variants'][args.variant]
    sr_l = layout['sr']
    gap_s = layout['gap']

    src = os.path.join(args.sep_dir,
                       os.path.splitext(var['file'])[0] + args.suffix + '.wav')
    if not os.path.isfile(src):
        print(f'✗ 分离结果不存在：{src}')
        return 1

    x, sr = sf.read(src, always_2d=True, dtype='float32')
    print(f'>>> 源 {os.path.basename(src)}  {len(x)/sr:.2f}s {sr}Hz {x.shape[1]}ch')
    # ⚠️ 采样率**不必相等**：layout 的坐标单位是「秒」，下面一律用**实际 sr**
    #   换算样本索引 ⇒ 时间轴天然一致。
    #   Roformer 内部固定重采样到 44100，故 48k 源（如龙司）分离后必然变 44100 ——
    #   这是**预期行为**，早期版本在这里 return 1 属过严，已放宽为警告。
    if sr != sr_l:
        print(f'⚠️ 采样率不符：分离结果 {sr}Hz ≠ layout 记录 {sr_l}Hz'
              f'（Roformer 固定输出 44100，属预期；按秒换算，时间轴一致）')

    # 1) 按 layout 坐标裁出各目标段
    parts = []
    for i, seg in enumerate(var['segs']):
        s = int(round(seg['file_start'] * sr))
        e = s + int(round(seg['dur'] * sr))
        parts.append(x[s:e])
        print(f'    裁 {seg["name"]:<10} {seg["file_start"]:.3f}s +{seg["dur"]:.3f}s '
              f'→ 样本 [{s}, {e})')
        if i < len(var['segs']) - 1:
            parts.append(np.zeros((int(round(gap_s * sr)), x.shape[1]), dtype='float32'))
    out_x = np.concatenate(parts, axis=0)
    dur = len(out_x) / sr
    print(f'>>> 裁后 {dur:.2f}s')
    if dur > 15.0:
        print(f'✗ 超过 IndexTTS 的 15s 上限！缩短参考段或减上下文')

    # 2) 音量对齐
    eff = eff_loudness(out_x, sr)
    pk = db(float(np.abs(out_x).max()))
    print(f'>>> 裁后 有效响度 {eff:.1f} dB  峰值 {pk:.1f} dBFS')

    if not args.no_gain:
        if args.target_eff_db is not None:
            target = args.target_eff_db
            src_desc = '命令行指定'
        elif os.path.isfile(args.match_ref):
            ry, rsr = sf.read(args.match_ref, always_2d=True, dtype='float32')
            target = eff_loudness(ry, rsr)
            src_desc = os.path.basename(args.match_ref)
        else:
            print(f'✗ 基准参考不存在：{args.match_ref}（请用 --target-eff-db 指定）')
            return 1

        gain = target - eff
        gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain))
        if pk + gain > PEAK_CEIL_DB:
            capped = PEAK_CEIL_DB - pk
            print(f'    ⚠️ 峰值保护：增益 {gain:+.2f} → {capped:+.2f} dB'
                  f'（{pk + gain:.1f} 会超 {PEAK_CEIL_DB:.1f} dBFS）')
            gain = capped
        print(f'>>> 对齐目标 {target:.1f} dB（基准：{src_desc}）  ⇒ 增益 {gain:+.2f} dB')
        out_x = out_x * (10 ** (gain / 20.0))
        eff2 = eff_loudness(out_x, sr)
        pk2 = db(float(np.abs(out_x).max()))
        print(f'>>> 对齐后 有效响度 {eff2:.1f} dB  峰值 {pk2:.1f} dBFS  '
              f'削顶 {int((np.abs(out_x) >= 0.999).sum())}')

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sf.write(args.out, out_x, sr, subtype='PCM_16')
    print(f'\n>>> 输出 {args.out}  ({os.path.getsize(args.out):,} B)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
