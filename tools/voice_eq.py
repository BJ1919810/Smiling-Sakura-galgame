# -*- coding: utf-8 -*-
"""语音频谱修正 —— 按 `voice_spectrum.py` 测出的「输出−参考」差值反推 EQ，把音色拉回参考。

★ 思路（数据驱动，不是拍脑袋调旋钮）
  `voice_spectrum.py` 给出每个 1/2 倍频程上「输出比参考多/少多少 dB」，
  本脚本就把这条曲线**反向**作为目标响应：
      H(f) = 10 ^ ( −k · diff(f) / 20 )
  k = 修正强度（0~1）：
      k=1.0 = 完全拉平到参考
      k=0.7 = 保守（推荐）—— 差异里有一部分是参考自身录制/编码的色调，
              全量反推会把参考的缺陷一起刻进来。
  ⇒ **不直接抄绝对值，而是先测再反推**，这样换参考音频也能自动重算。

★ 为什么用 FIR 而不是一串 peaking 滤波器
  测量点是 1/2 倍频程间距，若用一串 `equalizer` peaking，邻带会互相叠加
  （0.5 oct 带宽 × 0.5 oct 间距 ⇒ 严重重复计入），实际增益远超设计值。
  频率采样法设计的**线性相位 FIR** 可以精确落在目标响应上，且与相位无关。

★ 线性相位 FIR 有 (numtaps-1)/2 的群延迟 ⇒ 卷积后必须**掐掉**这段，
  否则每条音频都会多出 20+ ms 的头部静音（在游戏里就是对话延迟）。

用法：
    python tools/voice_eq.py --diff 谱差.json --src <wav目录> --dst <输出目录> --k 0.7
    python tools/voice_eq.py --diff 谱差.json --src ... --dst ... --k 1.0 --dry-run
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import firwin2, fftconvolve

BAND_HZ = [80, 110, 160, 220, 320, 450, 640, 900, 1250, 1800, 2500, 3500, 5000, 7000, 10000]
NUMTAPS = 511          # @22.05k ≈ 23 ms，对非实时足够
MAX_BOOST_DB = 6.0     # 正向补偿上限（防止把参考的低频缺陷放大）
MAX_CUT_DB = -12.0     # 衰减上限


def build_response(diff: list[dict], k: float, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """由测量差值构造 FIR 频率采样点。"""
    hz = np.array([d["hz"] for d in diff], dtype=float)
    df = np.array([d["diff"] for d in diff], dtype=float)

    # 目标增益 dB = −k·diff，并限幅
    gain_db = np.clip(-k * df, MAX_CUT_DB, MAX_BOOST_DB)

    # 频率点：补 0Hz 与 Nyquist 端点，用最靠边的值外推（不起突跳）
    nyq = sr / 2.0
    freqs = list(hz)
    gains = list(gain_db)
    if freqs[0] > 20:
        freqs.insert(0, 20.0)
        gains.insert(0, gain_db[0])
    freqs.append(nyq)
    gains.append(gain_db[-1])

    freqs = np.array(freqs)
    gains = np.array(gains)
    keep = freqs <= nyq
    freqs, gains = freqs[keep], gains[keep]
    # firwin2 的 freq 必须在 [0,1] 且单调不减
    w = np.clip(freqs / nyq, 0.0, 1.0)
    order = np.argsort(w)
    w, gains = w[order], gains[order]
    uniq = np.concatenate([[True], np.diff(w) > 1e-9])
    w, gains = w[uniq], gains[uniq]
    if w[0] > 0:
        w = np.concatenate([[0.0], w])
        gains = np.concatenate([[gains[0]], gains])
    if w[-1] < 1.0:
        w = np.concatenate([w, [1.0]])
        gains = np.concatenate([gains, [gains[-1]]])

    taps = firwin2(NUMTAPS, w, 10 ** (gains / 20.0))
    # ⚠️ 表要用**最终**的 w/gains 建：freqs 中途被 keep 过滤过，而 w 还被
    #    order/uniq 处理过，两数组长度会不一致（曾经在这里 ValueError）。
    return taps, np.column_stack([w * nyq, gains])


def main() -> int:
    ap = argparse.ArgumentParser(description="按实测谱差反推 EQ，把音色拉回参考")
    ap.add_argument("--diff", required=True, help="voice_spectrum.py --json 的输出")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--k", type=float, default=0.7, help="修正强度 0~1")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    data = json.loads(Path(args.diff).read_text(encoding="utf-8"))
    src, dst = Path(args.src), Path(args.dst)
    wavs = sorted(p for p in src.glob("*.wav") if not p.name.startswith("_"))
    if not wavs:
        print(f"✗ {src} 没有 wav")
        return 1

    x0, sr = sf.read(str(wavs[0]))
    taps, table = build_response(data["bands"], args.k, sr)

    print(f"强度 k={args.k}  抽头 {NUMTAPS} @ {sr}Hz  源 {src.name} → {dst.name}")
    print(f"\n{'频率':>9}{'施加增益':>11}")
    print("-" * 22)
    for hz, g in table:
        print(f"{hz:>7.0f}Hz{g:>+10.2f} dB")

    delay = (NUMTAPS - 1) // 2
    if not args.dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    for p in wavs:
        x, s = sf.read(str(p))
        if s != sr:
            print(f"  ✗ {p.name} 采样率 {s} != {sr}，跳过")
            continue
        if x.ndim > 1:
            x = x.mean(axis=1)
        y = fftconvolve(x.astype(np.float64), taps)[delay:delay + len(x)]
        peak = float(np.max(np.abs(y)))
        if peak > 0.999:
            y = y / peak * 0.999
        if not args.dry_run:
            out = dst / p.name
            if out.exists() and not args.overwrite:
                continue
            sf.write(str(out), y.astype(np.float32), s, subtype="PCM_16")
    print(f"\n{'演练，未写盘。' if args.dry_run else f'已写出到 {dst}'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
