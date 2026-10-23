#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
《笑樱》SE 导入器 —— 把外部生成（即梦／火山方舟等）的音效成品收进工程。

主人工作流（2026-09-18 定）：
    导出音频 → 丢进 Downloads → 跑本脚本 → 自动转码／归一化／按 manifest 对号落盘。

它做的事：
    ① 扫描来源目录（默认 ~/Downloads）里的音频文件（wav/mp3/m4a/flac/ogg/aac）
    ② 把每个文件**匹配到一个 SE 键**（三种方式，见下）
    ③ 解码 → 转单声道 44.1kHz → 归一到 −3.0 dBFS 峰值（与全项目口径一致）
    ④ 写引擎成品 `public/assets/audio/se/se_0XX.ogg`（编号由 manifest/SE_PIN 决定）
       ＋ 同步母带 `素材基准/音频/SE/se_<中文键名>.wav`
    ⑤ 打印一张对照表 + 声学体检（峰值／时长／crest），方便立刻判断对没对

匹配方式（按优先级）：
    A. `--map "文件名=键名"` 手动指定（最可靠，可重复给多个）
    B. 文件名里**包含** SE 中文键名（如 `脚步声.mp3` / `se_妙纸轻响_v2.wav`）
    C. `--order` 按给定顺序与待导入键列表一一对应（适合批量导出、文件名是随机串）

⚠️ 本脚本**不删除来源文件**（来源是你手动下载的，留着你自己判断）。
⚠️ 落盘前会备份被覆盖的旧文件到 `.workbuddy/backup/se_import_<时间戳>/`。

用法：
    python tools/import-se.py --dry-run                    # 先看会怎么对号，不写盘
    python tools/import-se.py                              # 正式导入（自动匹配）
    python tools/import-se.py 脚步声.mp3 书页轻响.wav       # 只导入指定的几个文件
    python tools/import-se.py --map "a.wav=脚步声" --map "b.mp3=风声"
    python tools/import-se.py --order 教室嘈杂 脚步声 风声   # 按顺序对应
    python tools/import-se.py --src "D:/somewhere"         # 换来源目录
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
DEFAULT_SRC = Path.home() / "Downloads"

PEAK_DB = -3.0          # ★ 全项目统一口径（见 制作手册 §9.6）
SR = 44100
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".opus", ".wma"}


# ---------------------------------------------------------------- 工具

def safe_name(key: str) -> str:
    """
    键名 → 文件名安全形式。
    ⚠️ 必须与 `gen-se.py` 的 `safe_name()` **逐字一致**，否则母带目录会对不上
    （它把全角括号转半角、中点转下划线）。别凭记忆重写 —— 从 gen-se.py 抄。
    """
    s = re.sub(r'[\\/:*?"<>|]', "_", key)
    return s.replace("（", "(").replace("）", ")").replace("・", "_")


def find_ffmpeg() -> str | None:
    """
    定位 ffmpeg。
    ⚠️ 本机真实路径是 `D:\\ffmpeg\\bin\\ffmpeg.exe`（09-18 实测）；`D:\\` 必须放在候选首位，
    因为它同时是「本机装在哪」和「gen-se.py 已验证可用」的那一份。别改成先查 C 盘。
    """
    for c in (os.environ.get("FFMPEG"), r"D:\ffmpeg\bin\ffmpeg.exe",
              r"C:\ffmpeg\bin\ffmpeg.exe"):
        if c and Path(c).exists():
            return c
    return shutil.which("ffmpeg")


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """
    读任意格式 → (mono float64, sr)。
    ⚠️ soundfile 读不了 mp3/m4a（部分构建）⇒ 先试 soundfile，失败则回退 ffmpeg 解码为临时 wav。
    """
    try:
        x, sr = sf.read(str(path), always_2d=True)
        return x.astype(np.float64), sr
    except Exception:
        ff = find_ffmpeg()
        if not ff:
            raise RuntimeError(f"{path.name} 无法解码，且找不到 ffmpeg")
        tmp = ROOT / ".workbuddy" / "tmp" / "_import_tmp.wav"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(path), "-c:a", "pcm_f32le", "-ar", str(SR),
                        "-ac", "1", str(tmp)], check=True)
        x, sr = sf.read(str(tmp), always_2d=True)
        return x.astype(np.float64), sr


def as_mono(x: np.ndarray, sr: int) -> np.ndarray:
    """降混到单声道。SE 一律单声道（引擎侧如此，与 gen-se.py 一致）。"""
    if x.ndim == 1:
        return x
    return x.mean(axis=1)


def resample(x: np.ndarray, sr: int, target: int = SR) -> np.ndarray:
    """重采样（线性插值足够 —— SE 不做音高分析）。"""
    if sr == target:
        return x
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)


def normalize(x: np.ndarray, peak_db: float = PEAK_DB) -> np.ndarray:
    """归一化到指定峰值（不削顶）—— 与 gen-se.py 同款。"""
    m = float(np.max(np.abs(x)))
    if m < 1e-9:
        return x
    return x * ((10 ** (peak_db / 20.0)) / m)


def fade_edges(x: np.ndarray, sr: int, ms: float = 3.0) -> np.ndarray:
    """首尾强制淡入淡出，防 click —— 与 gen-se.py 同款。"""
    n = max(1, int(sr * ms / 1000.0))
    n = min(n, len(x) // 2)
    if n <= 0:
        return x
    x = x.copy()
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    return x


def envelope_crest(x: np.ndarray, sr: int, win_ms: float = 10.0) -> tuple[float, float]:
    """
    包络峰值因子（离散 vs 连续的正确判据）＋ 每秒有效事件数。
    ⚠️ 别用「数包络峰」—— 宽带噪声会报 95 峰/秒，完全没区分度（见 audit-se.py 注释）。
    """
    w = max(1, int(sr * win_ms / 1000.0))
    n = len(x) // w
    if n < 2:
        return 0.0, 0.0
    rms = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1)) + 1e-12
    crest = float(rms.max() / rms.mean())
    events = float((rms > rms.mean() * 1.8).sum() / (len(x) / sr))
    return crest, events


def energy_start(x: np.ndarray, sr: int, rel_db: float = -30.0) -> int:
    """
    首个能量点（相对峰值 -30 dB）。
    ⚠️ 公共素材库的文件开头常带静音/淡入 ⇒ 硬从 0 剪会剪出一段空白，必须据此对齐。
    """
    w = max(1, int(sr * 0.01))
    n = len(x) // w
    if n < 1:
        return 0
    rms = np.sqrt((x[: n * w].reshape(n, w) ** 2).mean(axis=1)) + 1e-12
    thr = rms.max() * (10 ** (rel_db / 20.0))
    hit = rms > thr
    return int(np.argmax(hit)) * w if hit.any() else 0


def trim_to(x: np.ndarray, sr: int, seconds: float, fade_ms: float = 40.0) -> np.ndarray:
    """从首个能量点起截取 seconds 秒 ＋ 末尾余弦淡出（防硬切爆音）。"""
    st = energy_start(x, sr)
    end = st + int(sr * seconds)
    y = x[st:] if end >= len(x) else x[st:end]
    f = min(int(sr * fade_ms / 1000.0), len(y) // 2)
    if f > 0:
        y = y.copy()
        y[-f:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, f)))
    return y


def lowpass_fft(x: np.ndarray, sr: int, fc: float) -> np.ndarray:
    """FFT 域 4 阶 Butterworth 幅度响应低通 —— 免 scipy，简单可靠。"""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / sr)
    return np.fft.irfft(np.fft.rfft(x) * (1.0 / (1.0 + (f / fc) ** 4)), n=n)


def apply_approach(x: np.ndarray, sr: int, far_gain: float = 0.40,
                   far_lp: float = 1600.0) -> np.ndarray:
    """
    把「恒定响度」素材做成「由远及近」：音量渐强 ＋ 高频渐进（远景高频被空气吸收）。
    ★ 只动包络与频带、不改素材内容 —— 属后期处理，不是美术意义上的「二次修正」。
    ★ 起始增益 0.40（约 −8 dB）：实测整段动态 ≈ 24 dB，贴近「15 m 走到 1 m」的
      物理衰减（20·log10(15) ≈ 23.5 dB）。**别调到 0.16 以下** —— 那样首段会掉到
      −53 dBFS，在游戏里（SE 音量已被压低）等于听不见。
    """
    n = len(x)
    t = np.linspace(0.0, 1.0, n)
    gain = far_gain + (1.0 - far_gain) * (t ** 1.6)
    return (lowpass_fft(x, sr, far_lp) * (1 - t) + x * t) * gain


def to_ogg(wav: Path, ogg: Path) -> bool:
    """转 ogg vorbis（单声道 44.1k q5）—— 与 gen-se.py 完全同参数，保证听感一致。"""
    ff = find_ffmpeg()
    if not ff:
        print("  ✗ 找不到 ffmpeg，无法转 ogg", file=sys.stderr)
        return False
    r = subprocess.run(
        [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
         "-c:a", "libvorbis", "-q:a", "5", "-ar", str(SR), "-ac", "1", str(ogg)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  ✗ ffmpeg 转码失败：{r.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------- 匹配

def match_key(filename: str, keys: list[str]) -> str | None:
    """
    文件名 → SE 键：文件名里**包含**某个键名即命中。
    取**最长命中**（避免「脚步声」把「踩过落叶的脚步声（由远及近）」也抢走 ——
    后者更长、更具体，应当优先）。
    """
    stem = Path(filename).stem
    hits = [k for k in keys if k in stem]
    if not hits:
        return None
    return max(hits, key=len)


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="把外部生成的 SE 音频收进工程")
    ap.add_argument("files", nargs="*", help="要导入的文件（默认扫描来源目录全部音频）")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help=f"来源目录（默认 {DEFAULT_SRC}）")
    ap.add_argument("--map", action="append", default=[],
                    metavar="文件=键名", help="手动指定映射，可重复")
    ap.add_argument("--order", nargs="*", default=None,
                    help="按给定键名顺序与找到的文件一一对应")
    ap.add_argument("--dry-run", action="store_true", help="只打印对照表，不写盘")
    ap.add_argument("--keep-loudness", action="store_true",
                    help="跳过 −3 dBFS 归一化（保留原响度，供人工比对时用）")
    ap.add_argument("--trim", action="append", default=[], metavar="键名=秒数",
                    help="把该键裁到指定时长（从能量起点起算），可重复。例：--trim 翻书声=0.6")
    ap.add_argument("--approach", action="append", default=[], metavar="键名",
                    help="给该键加「由远及近」包络（音量渐强＋高频渐进），可重复")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"✗ 找不到 manifest：{MANIFEST}\n  先跑 node --experimental-strip-types tools/compiler/manifest.ts")
        return 1
    se: dict[str, dict] = json.loads(MANIFEST.read_text(encoding="utf-8"))["se"]
    keys = list(se)

    # ---- 收集待处理文件 ----
    if args.files:
        srcs = [Path(f) if Path(f).is_absolute() else Path(args.src) / f for f in args.files]
    else:
        src_dir = Path(args.src)
        if not src_dir.exists():
            print(f"✗ 来源目录不存在：{src_dir}")
            return 1
        srcs = sorted(p for p in src_dir.iterdir()
                      if p.is_file() and p.suffix.lower() in AUDIO_EXT)
    srcs = [p for p in srcs if p.exists()]
    if not srcs:
        print(f"✗ 没有找到任何音频文件（来源：{args.src}）")
        print(f"  支持的扩展名：{', '.join(sorted(AUDIO_EXT))}")
        return 1

    # ---- 建映射 ----
    manual = {}
    for m in args.map:
        if "=" not in m:
            print(f"✗ --map 格式应为 文件=键名，收到：{m}")
            return 1
        fn, k = m.split("=", 1)
        if k not in se:
            print(f"✗ --map 里的键名不在 manifest：{k}")
            return 1
        manual[fn] = k

    # --trim / --approach 解析（键名必须存在于 manifest）
    trim_map: dict[str, float] = {}
    for s in args.trim:
        if "=" not in s:
            print(f"✗ --trim 格式应为 键名=秒数，收到：{s}")
            return 1
        k, v = s.split("=", 1)
        if k not in se:
            print(f"✗ --trim 里的键名不在 manifest：{k}")
            return 1
        try:
            trim_map[k] = float(v)
        except ValueError:
            print(f"✗ --trim 的秒数不是数字：{v}")
            return 1
    approach_keys: set[str] = set()
    for k in args.approach:
        if k not in se:
            print(f"✗ --approach 里的键名不在 manifest：{k}")
            return 1
        approach_keys.add(k)

    plan: list[tuple[Path, str]] = []
    if args.order is not None:
        # 顺序模式：文件按名字排序，与给定键名列表一一对应
        if len(args.order) != len(srcs):
            print(f"✗ --order 给了 {len(args.order)} 个键，但有 {len(srcs)} 个文件，数量不等")
            return 1
        for k in args.order:
            if k not in se:
                print(f"✗ --order 里的键名不在 manifest：{k}")
                return 1
        plan = list(zip(srcs, args.order))
    else:
        unmatched = []
        for p in srcs:
            if p.name in manual:
                plan.append((p, manual[p.name]))
            else:
                k = match_key(p.name, keys)
                if k:
                    plan.append((p, k))
                else:
                    unmatched.append(p)
        if unmatched:
            print(f"⚠️ 有 {len(unmatched)} 个文件匹配不到 SE 键（已跳过）：")
            for p in unmatched:
                print(f"    · {p.name}")
            print("   ⇒ 把文件名改成键名，或用 --map \"文件名=键名\" 指定")

    if not plan:
        print("✗ 没有任何文件被匹配上，什么都没做")
        return 1

    # 同一键被多个文件命中 → 取**最后一个**（通常是最新导出的 v2），并提示
    by_key: dict[str, list[Path]] = {}
    for p, k in plan:
        by_key.setdefault(k, []).append(p)
    final: list[tuple[Path, str]] = []
    for k, ps in by_key.items():
        if len(ps) > 1:
            print(f"⚠️ 键「{k}」被 {len(ps)} 个文件命中，取最后一个（最新）：")
            for p in ps[:-1]:
                print(f"    · 忽略 {p.name}")
        final.append((ps[-1], k))

    # ---- 打印对照表 ----
    print()
    print("=" * 78)
    print(f"待导入 {len(final)} 项（{'演练模式，不写盘' if args.dry_run else '正式写入'}）")
    print("=" * 78)
    print(f"  {'来源文件':<40s} {'→ SE 键':<24s} {'目标'}")
    print("-" * 78)
    for p, k in final:
        print(f"  {p.name[:38]:<40s} {k:<24s} {se[k]['file']}")

    # ---- 备份目录（只有真要写盘时才建）----
    backup: Path | None = None
    if not args.dry_run:
        backup = BACKUP_ROOT / f"se_import_{datetime.now():%Y%m%d_%H%M%S}"

    # ---- 逐个处理 ----
    print()
    ok, fail = 0, []
    rows = []
    for p, k in final:
        try:
            x, sr = load_audio(p)
        except Exception as e:
            print(f"  ✗ {k:<22s} 解码失败：{e}")
            fail.append((k, str(e)))
            continue

        mono = as_mono(x, sr)
        mono = resample(mono, sr)
        # ★ 顺序要紧：裁剪/渐入 → 再归一化（归一化是线性缩放，不破坏包络形状）
        if k in trim_map:
            mono = trim_to(mono, SR, trim_map[k])
        if k in approach_keys:
            mono = apply_approach(mono, SR)
        mono = fade_edges(mono, SR)
        # ★ 归一化放最后：才能保证成品峰值精确达标。
        #   若排在 fade_edges 之前，首尾那 3 ms 的淡入淡出会把峰值再削掉一点
        #   （实测裁剪后的脚步声曾落到 −3.60 dB，就是峰值恰好压在起点那段）。
        if not args.keep_loudness:
            mono = normalize(mono)

        if not np.all(np.isfinite(mono)):
            print(f"  ✗ {k:<22s} 含 NaN/Inf，拒绝写出（这是坏文件）")
            fail.append((k, "NaN/Inf"))
            continue

        dur = len(mono) / SR
        crest, ev = envelope_crest(mono, SR)
        peak = 20 * np.log10(float(np.max(np.abs(mono))) + 1e-12)
        rows.append((k, p.name, dur, peak, crest, ev))

        if args.dry_run:
            print(f"  ✓ {k:<22s} {dur:5.2f}s  峰值 {peak:6.2f} dB  crest {crest:5.2f}  {p.name}")
            continue

        # 写母带 wav（先备份同名旧文件）
        MASTER_DIR.mkdir(parents=True, exist_ok=True)
        DST_DIR.mkdir(parents=True, exist_ok=True)
        master = MASTER_DIR / f"se_{safe_name(k)}.wav"
        ogg = DST_DIR / Path(se[k]["file"]).name
        assert backup is not None
        for old in (master, ogg):
            if old.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, backup / old.name)

        sf.write(str(master), mono, SR, subtype="PCM_16")
        if not to_ogg(master, ogg):
            fail.append((k, "ogg 转码失败"))
            continue

        size_kb = ogg.stat().st_size / 1024
        print(f"  ✓ {k:<22s} {dur:5.2f}s  峰值 {peak:6.2f} dB  crest {crest:5.2f}  "
              f"→ {ogg.name} ({size_kb:.0f} KB)")
        ok += 1

    # ---- 汇总 ----
    print()
    print("=" * 78)
    if args.dry_run:
        print(f"演练完成：{len(final)} 项可导入。去掉 --dry-run 即正式写入。")
    else:
        print(f"导入完成：成功 {ok} / 失败 {len(fail)}")
        if fail:
            for k, e in fail:
                print(f"    ✗ {k}：{e}")
        if ok and backup and backup.exists():
            print(f"  被覆盖的旧文件备份在：{backup}")
            print("  ⚠️ 旧文件是 exe 里正在用的；要听效果请重出包或直接开 SE试听台.html")

    if rows:
        print()
        print("声学体检（对照 制作手册 §9.4 每条写明的预期）：")
        print(f"  {'键':<24s}{'时长':>7s}{'峰值dB':>9s}{'crest':>8s}{'事件/s':>9s}  判读")
        for k, fn, dur, peak, crest, ev in rows:
            verdict = "离散" if crest > 4 else ("偏连续" if crest > 2.5 else "连续")
            print(f"  {k:<24s}{dur:6.2f}s{peak:9.2f}{crest:8.2f}{ev:9.1f}  {verdict}")
        print()
        print("  ★ crest 判读：离散事件（脚步/纸页/落叶）应 >4；连续声（风/嘈杂）应 <3。")
        print("    与自己预期不符 ⇒ 生成跑偏了，回去调提示词重出。")
    return 0 if (args.dry_run or not fail) else 1


if __name__ == "__main__":
    raise SystemExit(main())
