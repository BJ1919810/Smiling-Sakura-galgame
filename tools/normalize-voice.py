# -*- coding: utf-8 -*-
"""《笑樱》角色语音响度归一化 —— 把逐条合成的台词**感知响度**拉齐。

★ 为什么需要（2026-09-18 首批 13 条实测）
  IndexTTS 逐条独立合成，每条的音量是各自随机的结果，实测
  **峰值 0.320 ~ 0.758（离散 2.37×）、RMS 0.044 ~ 0.121（2.7×）**。
  直接进游戏就是「有的轻有的响」—— 这比采样率（22050 vs 44100）重要得多。

★ 口径与 `normalize-se.py` **完全一致**（同一套代码逻辑，别各写一版）：
  · 有效响度 = 100 ms block 的 dB，取**较响的 20%** 求均值
    ⛔ 不用积分 LUFS：台词里有大量停顿，积分均值被拉低，但听感并不轻。
  · 峰值保护 −3 dBFS ＋ tanh 软限幅（knee 8 dB）
    ⇒ **提响度必须先把峰压下来**，否则「越界就整体下调」会把增益原样抵消。
  · 增益限幅 ±12 dB

★ 语音的分档（与 SE 的三档对齐，不抢戏也不被盖）
  | 类别 | 目标 | 理由 |
  |---|---|---|
  | 台词 | **−14 dB** | 比事件音（−16）略响，仍是主角；留 2 dB 给 BGM 垫底 |
  | 心声 / 独白 | −17 dB | 内心独白要明显比实音轻一档（台本的（心声）） |
  ⚠️ 环境底噪 −22 / 提示 −13 是 SE 的档，语音不参与这两档。

★ 非破坏性：默认写到**另一个目录**（保留原始版以便 A/B 试听）。

用法：
    python tools/normalize-voice.py --src <原始目录> --dst <输出目录> --dry-run
    python tools/normalize-voice.py --src <原始目录> --dst <输出目录>
    python tools/normalize-voice.py --src ... --dst ... --target -16   # 改总目标
    python tools/normalize-voice.py --src ... --dst ... --resample 44100
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent

PEAK_DB = -3.0
MAX_GAIN_DB = 12.0
BLOCK_S = 0.1
TOP_PCT = 20.0
KNEE_DB = 8.0

TARGET_LINE = -14.0     # 台词
TARGET_ASIDE = -17.0    # 心声 / 独白


def effective_loudness(x: np.ndarray, sr: int) -> float:
    """有效响度：100 ms block 的 dB 中，较响的 20% 求均值。（与 normalize-se.py 同口径）"""
    w = max(1, int(sr * BLOCK_S))
    n = len(x) // w
    if n < 1:
        w, n = len(x), 1
    rms = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1)) + 1e-12
    db = 20 * np.log10(rms)
    k = max(1, int(round(n * TOP_PCT / 100.0)))
    return float(np.sort(db)[-k:].mean())


def soft_limit(x: np.ndarray, ceiling_db: float = PEAK_DB, knee_db: float = KNEE_DB) -> np.ndarray:
    """软限幅：越过 knee 的部分用 tanh 渐进压到 ceiling（不是硬削平）。"""
    ceil = 10 ** (ceiling_db / 20.0)
    knee = ceil * (10 ** (-knee_db / 20.0))
    a = np.abs(x)
    out = a.copy()
    m = a > knee
    if m.any():
        t = (a[m] - knee) / (ceil - knee)
        out[m] = knee + (ceil - knee) * np.tanh(t)
    return np.sign(x) * out


def resample_poly(x: np.ndarray, sr: int, target_sr: int) -> tuple[np.ndarray, int]:
    """高质量重采样（scipy 多相滤波，带抗混叠）。⛔ 不增加信息，只为统一管线。"""
    if sr == target_sr:
        return x, sr
    from math import gcd
    from scipy.signal import resample_poly as rp
    g = gcd(sr, target_sr)
    return rp(x, target_sr // g, sr // g).astype(np.float32), target_sr


def main() -> int:
    ap = argparse.ArgumentParser(description="角色语音响度归一化（有效响度 → 台词目标）")
    ap.add_argument("--src", required=True, help="原始 wav 目录")
    ap.add_argument("--dst", required=True, help="输出目录（非破坏性）")
    ap.add_argument("--target", type=float, default=TARGET_LINE, help="台词目标有效响度 dB")
    ap.add_argument("--aside-keys", nargs="*", default=[], help="按心声处理的文件 id")
    ap.add_argument("--resample", type=int, default=0, help="可选：重采样到该采样率")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出")
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    wavs = sorted(p for p in src.glob("*.wav") if not p.name.startswith("_"))
    if not wavs:
        print(f"✗ {src} 里没有 wav", file=sys.stderr)
        return 1
    if not args.dry_run:
        dst.mkdir(parents=True, exist_ok=True)

    print(f"源 {src}  →  {dst}")
    print(f"目标：台词 {args.target:+.1f} dB" + (f"，心声 {TARGET_ASIDE:+.1f} dB（{len(args.aside_keys)} 个）" if args.aside_keys else ""))
    print()
    print(f"{'文件':<18}{'目标':>7}{'前(有效)':>10}{'峰':>7}{'增益':>8}{'后(有效)':>10}{'峰':>7}{'时长':>8}  备注")
    print("-" * 92)

    rows = []
    for p in wavs:
        x, sr = sf.read(str(p))
        if x.ndim > 1:
            x = x.mean(axis=1)
        x = x.astype(np.float64)
        tgt = TARGET_ASIDE if p.stem in args.aside_keys else args.target

        before = effective_loudness(x, sr)
        gain_db = float(np.clip(tgt - before, -MAX_GAIN_DB, MAX_GAIN_DB))
        y = x * (10 ** (gain_db / 20.0))
        y = soft_limit(y)
        pk = 20 * np.log10(float(np.max(np.abs(y))) + 1e-12)
        after = effective_loudness(y, sr)

        note = ""
        if abs(after - tgt) > 1.0:
            note = "⚠️ 未达目标（峰值受限）" if gain_db >= MAX_GAIN_DB - 0.01 else "⚠️ 偏差>1dB"
        rows.append((p.name, tgt, before, gain_db, after, pk, len(y) / sr, note))

        print(f"{p.stem:<18}{tgt:>+7.1f}{before:>10.1f}{'':>7}{gain_db:>+8.1f}{after:>10.1f}{pk:>7.1f}{len(y)/sr:>7.2f}s  {note}")

        if args.dry_run:
            continue
        out = dst / p.name
        if out.exists() and not args.overwrite:
            print(f"    （已存在，跳过：{out.name}）")
            continue
        if args.resample:
            y, sr = resample_poly(y, sr, args.resample)
        peak = float(np.max(np.abs(y)))
        if peak > 1.0:
            y = y / peak * 0.999  # 重采样可能产生微小过冲，兜底
        sf.write(str(out), y.astype(np.float32), sr, subtype="PCM_16")

    if rows:
        aft = np.array([r[4] for r in rows])
        bef = np.array([r[2] for r in rows])
        gns = np.array([r[3] for r in rows])
        print("-" * 92)
        print(f"有效响度  前 {bef.min():.1f} ~ {bef.max():.1f} （极差 {bef.max()-bef.min():.1f} dB）")
        print(f"          后 {aft.min():.1f} ~ {aft.max():.1f} （极差 {aft.max()-aft.min():.1f} dB）")
        print(f"增益区间  {gns.min():+.1f} ~ {gns.max():+.1f} dB")
    print("\n演练模式，未写盘。" if args.dry_run else f"\n已写出到 {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
