# -*- coding: utf-8 -*-
"""语音音色诊断 —— 对比「合成输出」与「参考音频」的**谱包络**，量化模型染色。

★ 为什么不用 1/3 倍频程原始 LTAS
  参考音频是**单句**，它的长时谱被 F0 谐波梳支配（实测 250 Hz 比 200 Hz 高 18 dB ——
  那是谐波，不是音色）。窄带比较会把"音高不同"误读成"音色不同"。
  ⇒ 必须**倒谱平滑（liftering）**：对平均对数谱做 IFFT，掐掉高quefrency 分量
    （F0 周期），再变回来。留下的才是**音色包络**。

★ 口径
  · 参考先重采样到与输出同采样率（模型内部也是这么做的），避免带宽差异混入
  · 两条包络都以同一个锚点频率对齐（默认 640 Hz），才比得出**倾斜**差异
  · 跳过能量过低的帧（静音），否则静音段污染长时谱

用法：
    python tools/voice_spectrum.py --ref <参考.wav> --dir <输出目录>
    python tools/voice_spectrum.py --ref ... --dir ... --anchor 1000 --json 结果.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import wave
from math import gcd

import numpy as np
from scipy.signal import resample_poly

OCTAVE_CENTERS = [80, 110, 160, 220, 320, 450, 640, 900, 1250,
                  1800, 2500, 3500, 5000, 7000, 10000]


def load_wav(path: str, target_sr: int | None = None) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as w:
        sr, n, ch = w.getframerate(), w.getnframes(), w.getnchannels()
        x = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float64) / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
    if target_sr and sr != target_sr:
        g = gcd(sr, target_sr)
        x = resample_poly(x, target_sr // g, sr // g)
        sr = target_sr
    return x, sr


def envelope(x: np.ndarray, sr: int, nfft: int = 4096, hop: int = 1024,
             lifter_ms: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    """倒谱平滑的长时谱包络。返回 (频率轴, 包络 dB)。"""
    win = np.hanning(nfft)
    acc = np.zeros(nfft // 2 + 1)
    cnt = 0
    for i in range(0, max(0, len(x) - nfft), hop):
        seg = x[i:i + nfft]
        if np.sqrt((seg ** 2).mean()) < 1e-4:
            continue  # 静音帧不参与
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        cnt += 1
    lt = acc / max(cnt, 1) + 1e-20
    ceps = np.fft.irfft(np.log(lt))
    cut = int(lifter_ms / 1000.0 * sr)
    ceps[cut:len(ceps) - cut] = 0
    env = np.fft.rfft(ceps).real[: nfft // 2 + 1]
    return np.fft.rfftfreq(nfft, 1 / sr), 20 * env / np.log(10)


def band_mean(f: np.ndarray, e: np.ndarray, c: float, half_oct: float = 0.25) -> float:
    i0, i1 = np.searchsorted(f, c / 2 ** half_oct), np.searchsorted(f, c * 2 ** half_oct)
    return float(e[i0:i1].mean()) if i1 > i0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description="合成输出 vs 参考音频 的谱包络对比")
    ap.add_argument("--ref", required=True)
    ap.add_argument("--dir", required=True, help="输出 wav 目录")
    ap.add_argument("--anchor", type=float, default=640.0, help="对齐锚点频率 Hz")
    ap.add_argument("--json", default="", help="把结果写一份 JSON")
    args = ap.parse_args()

    ref, sr = load_wav(args.ref)
    ref_x, sr = load_wav(args.ref, 22050)  # 与输出同采样率，排除带宽差异
    fr, er = envelope(ref_x, sr)

    files = [p for p in sorted(glob.glob(os.path.join(args.dir, "*.wav")))
             if not os.path.basename(p).startswith("_")]
    if not files:
        print(f"✗ {args.dir} 里没有 wav")
        return 1

    envs = []
    for p in files:
        x, s = load_wav(p, sr)
        _, e = envelope(x, s)
        envs.append(e)
    eo = np.mean(envs, axis=0)

    a_ref, a_out = band_mean(fr, er, args.anchor), band_mean(fr, eo, args.anchor)
    na = np.array([band_mean(fr, er, c) - a_ref for c in OCTAVE_CENTERS])
    nb = np.array([band_mean(fr, eo, c) - a_out for c in OCTAVE_CENTERS])

    print(f"参考 {os.path.basename(args.ref)}  输出 {len(files)} 条（均值）")
    print(f"对齐锚点 {args.anchor:.0f} Hz\n")
    print(f"{'频率':>9}{'参考包络':>10}{'输出包络':>10}{'差(输出-参考)':>15}")
    print("-" * 62)
    result = []
    for i, c in enumerate(OCTAVE_CENTERS):
        d = nb[i] - na[i]
        bar = ("#" * int(abs(d) * 2)) if d >= 0 else ("<" * int(abs(d) * 2))
        flag = "  <<< 明显" if abs(d) >= 3 else ("  <  轻微" if abs(d) >= 2 else "")
        print(f"{c:>7}Hz{na[i]:>10.1f}{nb[i]:>10.1f}{d:>+12.2f} dB  {bar}{flag}")
        result.append({"hz": c, "ref": round(na[i], 2), "out": round(nb[i], 2), "diff": round(d, 2)})

    print("\n★ 正=输出比参考多（模型加的）；负=输出比参考少（模型削的）")
    print("★ 判读：+2 ~ +6 dB 的宽峰＝可闻染色；>+6 dB 往往是神经声码器的高频过冲")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"ref": os.path.basename(args.ref), "files": len(files),
                       "anchor_hz": args.anchor, "bands": result}, fh,
                      ensure_ascii=False, indent=2)
        print(f"\n已写出 {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
