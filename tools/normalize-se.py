#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
《笑樱》SE 响度归一化 —— 把 29 个音效的**感知响度**拉齐，而不是只对齐峰值。

★ 为什么需要（2026-09-18 主人实机反馈「有的响有的轻」）
  `import-se.py` 只做**峰值归一化**（全部 −3 dBFS），但峰值齐 ≠ 听感齐：
  瞬态类（脚步声，峰均比 30.9 dB）听感很轻，持续类（心跳，9.9 dB）听感很响。
  实测：峰值全在 −3 附近，而**有效响度极差 21.2 dB**（脚步 −29.3 ～ 闷响 −8.1）。

★ 用哪个口径：**有效响度**
  取 100 ms block 的 dB，再取**较响的那 20%** 求均值。
  ⛔ 不用积分 LUFS：脚步/纸页这类「稀疏瞬态」大部分 block 是静音，
     积分均值被拉低（脚步积分 −34.2，有效 −29.3），但听感上它并不轻。
  ⚠️ 短音效（台灯开关 0.35 s）连 400 ms 的 BS.1770 标准窗都装不满，
     所以全程用 100 ms block —— 绝对值不是标准 LUFS，但**全体同尺、可比**。

★ 三类目标（刻意不搞一刀切）
  | 类别 | 目标 | 理由 |
  |---|---|---|
  | 提示 / 铃声 | −13 dB | 上课铃、预备铃、哨声、提示音 —— 要醒目 |
  | 环境底噪 | −22 dB | 教室嘈杂 / 议论 / 风声 / 水声 / 广播 —— **背景，不能抢台词** |
  | 其余事件音 | −16 dB | 脚步、纸页、椅子、糖纸、心跳…… 主体音量 |
  环境底噪必须比事件音轻，这是游戏音频的常识；一刀切会让底噪盖住对话。

★ 两条安全绳
  ① **增益限幅 ±12 dB** —— 防止把某个离群值拉爆。
  ② **峰值保护到 −3 dBFS** —— 增益后若峰值越界则整体下调。
     代价：峰均比高的瞬态类可能达不到目标响度（物理限制，优先不削波）。

用法：
    python tools/normalize-se.py --dry-run   # 只看会怎么调，不写盘
    python tools/normalize-se.py             # 正式写回母带 + 重新转 ogg
    python tools/normalize-se.py --only 脚步声 风声
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "src" / "generated" / "assets.manifest.json"
MASTER_DIR = ROOT / "素材基准" / "音频" / "SE"
DST_DIR = ROOT / "public" / "assets" / "audio" / "se"
BACKUP_ROOT = ROOT / ".workbuddy" / "backup"

PEAK_DB = -3.0          # 与全项目口径一致
MAX_GAIN_DB = 12.0      # 单次调整上限
BLOCK_S = 0.1           # 有效响度的 block
TOP_PCT = 20.0          # 取较响的 20%

# —— 提示 / 铃声：要醒目 ——
TARGET_NOTIFY = -13.0
# —— 环境底噪：背景，不能抢台词（与 manifest.ts 的 SE_ENV 同一批）——
TARGET_ENV = -22.0
# —— 其余事件音 ——
TARGET_EVENT = -16.0

NOTIFY_KEYS = {
    "上课预备铃", "午休结束的预备铃", "午休结束铃", "放学铃",
    "提示音", "哨声", "发令哨", "哨声、风",
}


def find_ffmpeg() -> str | None:
    """⚠️ 本机真实路径是 D:\\ffmpeg\\bin\\ffmpeg.exe（工具链装在 D 盘是老习惯，别只搜 C 盘）"""
    import os
    for c in (os.environ.get("FFMPEG"), r"D:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"):
        if c and Path(c).exists():
            return c
    return shutil.which("ffmpeg")


def effective_loudness(x: np.ndarray, sr: int) -> float:
    """有效响度：100 ms block 的 dB 中，较响的 20% 求均值。"""
    w = max(1, int(sr * BLOCK_S))
    n = len(x) // w
    if n < 1:
        w, n = len(x), 1
    rms = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1)) + 1e-12
    db = 20 * np.log10(rms)
    k = max(1, int(round(n * TOP_PCT / 100.0)))
    return float(np.sort(db)[-k:].mean())


def soft_limit(x: np.ndarray, ceiling_db: float = PEAK_DB, knee_db: float = 8.0) -> np.ndarray:
    """
    软限幅：把越过 knee 的部分**渐进**压到 ceiling（tanh 拐点，不是硬削平）。

    ★ 为什么必须有它 —— 这是「太轻的音效提不上来」的解法：
      上一版只做「增益 + 越界就整体下调」，结果脚步声 +12 dB 后仍和原来一样
      （因为峰值本就在 −3，一加就超、一超就被整体拉回）
      ⇒ **提响度必须先把峰压下来**，凭空加增益是加不动的。
      道理：瞬态类峰均比高达 26–31 dB，峰值和平均电平差了这么远，
      不压缩峰值就没有任何提升空间。
    """
    ceil = 10 ** (ceiling_db / 20.0)
    knee = ceil * (10 ** (-knee_db / 20.0))
    a = np.abs(x)
    out = a.copy()
    m = a > knee
    if m.any():
        t = (a[m] - knee) / (ceil - knee)
        out[m] = knee + (ceil - knee) * np.tanh(t)
    return np.sign(x) * out


def target_for(key: str, is_env: bool) -> float:
    if is_env:
        return TARGET_ENV
    if key in NOTIFY_KEYS:
        return TARGET_NOTIFY
    return TARGET_EVENT


def to_ogg(wav: Path, ogg: Path) -> bool:
    ff = find_ffmpeg()
    if not ff:
        print("  ✗ 找不到 ffmpeg", file=sys.stderr)
        return False
    r = subprocess.run(
        [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libvorbis", "-q:a", "5", "-ar", "44100", "-ac", "1", str(ogg)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ✗ ffmpeg 转码失败：{r.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="SE 响度归一化（有效响度 → 分类目标）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", nargs="*", default=None, help="只处理这些键")
    args = ap.parse_args()

    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    se = m["se"]
    keys = args.only or list(se)

    backup = None
    if not args.dry_run:
        backup = BACKUP_ROOT / f"se_loudness_{datetime.now():%Y%m%d_%H%M%S}"
        backup.mkdir(parents=True, exist_ok=True)

    print(f"{'键':<26}{'类别':<7}{'调整前':>9}{'目标':>8}{'增益':>8}{'调整后':>9}{'峰值':>8}  ")
    print("-" * 80)
    rows = []
    for k in keys:
        if k not in se:
            print(f"  ✗ 键不在 manifest：{k}")
            return 1
        is_env = bool(se[k].get("env"))
        cat = "环境" if is_env else ("提示" if k in NOTIFY_KEYS else "事件")
        target = target_for(k, is_env)

        master = MASTER_DIR / f"se_{k.replace('（', '(').replace('）', ')').replace('・', '_')}.wav"
        if not master.exists():
            print(f"  ✗ 母带缺失：{master.name}")
            continue
        x, sr = sf.read(str(master))
        if x.ndim > 1:
            x = x.mean(axis=1)

        before = effective_loudness(x, sr)
        gain_db = float(np.clip(target - before, -MAX_GAIN_DB, MAX_GAIN_DB))
        y = x * (10 ** (gain_db / 20.0))

        # ★ 增益后**软限幅**把峰压回 −3，而不是「越界就整体下调」——
        #   后者会把刚加的增益原样抵消（见 soft_limit 的说明）。
        y = soft_limit(y)
        pk = 20 * np.log10(float(np.max(np.abs(y))) + 1e-12)
        after = effective_loudness(y, sr)

        rows.append((k, cat, before, target, gain_db, after, pk))
        mark = "" if abs(after - target) < 1.0 else ("  ⚠️ 未达目标（峰值受限）" if gain_db >= MAX_GAIN_DB - 0.01 else "")
        print(f"{k:<26}{cat:<7}{before:>9.1f}{target:>8.1f}{gain_db:>+8.1f}{after:>9.1f}{pk:>8.1f}{mark}")

        if args.dry_run:
            continue

        shutil.copy2(master, backup / master.name)
        sf.write(str(master), y, sr, subtype="PCM_16")
        ogg = DST_DIR / Path(se[k]["file"]).name
        if ogg.exists():
            shutil.copy2(ogg, backup / ogg.name)
        if not to_ogg(master, ogg):
            return 1

    if rows:
        aft = np.array([r[5] for r in rows])
        print("-" * 80)
        print(f"调整后有效响度 {aft.min():.1f} ~ {aft.max():.1f}  极差 {aft.max() - aft.min():.1f} dB")
        bef = np.array([r[2] for r in rows])
        print(f"（调整前 {bef.min():.1f} ~ {bef.max():.1f}  极差 {bef.max() - bef.min():.1f} dB）")

    if args.dry_run:
        print("\n演练模式，未写盘。")
    else:
        print(f"\n已写回母带 + 重转 ogg。备份：{backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
