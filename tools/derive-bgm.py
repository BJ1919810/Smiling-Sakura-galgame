#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""derive-bgm.py —— 从成品母带 DSP 派生 BGM 变奏（变奏跑偏时的兜底）

背景：《笑樱》BGM 的变奏若在即梦上跑偏（即梦不支持指定种子，变奏各写一版文字容易跑偏），
      与其反复重抽，不如直接从主人**已经认可的成品母带**做时间／音高／空间处理。
      **同一个录音，主题不可能跑偏** —— 这是保真度最高的一条路。

流水线（三个变奏统一走这条，最终都收成 target 秒的无缝循环）：
    ① 从母带裁出「target × 速度系数 ＋ 淡化」那么长的一段
    ② 施加该变奏的特征处理（放慢／降调／柔化／混响）
    ③ 尾→头交叉淡化，收成 target 秒的无缝循环
    ④ 峰值归一到 -1 dBFS，编成 ogg/vorbis（引擎口径）

用法：
  G="C:/Users/zbj/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

  "$G" tools/derive-bgm.py info  --src 母带.ogg
  "$G" tools/derive-bgm.py batch --src 母带.ogg            # 一次出齐三件套 → 素材基准/音频/BGM/_派生/
  "$G" tools/derive-bgm.py mute  --src 母带.ogg --out 出.ogg [--tempo 0.82]
  "$G" tools/derive-bgm.py low   --src 母带.ogg --out 出.ogg [--semitones -5] [--tempo 0.82]
  "$G" tools/derive-bgm.py loop  --src 母带.ogg --out 出.ogg [--start 0:00] [--len 100] [--fade 2]

已知取舍（写清免得返工）：
  · DSP 改得出「更慢／更低／更静」，**改不出「更稀疏」** —— 音符数是死的，要真稀疏只能回即梦重生成。
  · 降调会**改调性**（A大调 −5 半音 → E大调）。**变不出升F小调** —— 那要换和声中心，不是加降号的事。
"""

import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

try:
    import numpy as np
    import soundfile as sf
except ImportError:
    print("[x] 缺 numpy/soundfile：pip install numpy soundfile（隔离 venv 里已有）")
    sys.exit(2)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BGM_DIR = os.path.join(ROOT, "素材基准", "音频", "BGM")
SR_BASE = 44100
OGG_ARGS = ["-c:a", "libvorbis", "-q:a", "6", "-ar", str(SR_BASE)]


# ---------------------------------------------------------------- 基础工具

def resolve_ffmpeg() -> str:
    cands = [os.environ.get("FFMPEG"),
             r"D:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe",
             shutil.which("ffmpeg")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    print("[x] 找不到 ffmpeg（设 FFMPEG 环境变量，或放到 D:\\ffmpeg\\bin\\）")
    sys.exit(2)


FFMPEG = resolve_ffmpeg()


def run(args) -> None:
    p = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if p.returncode != 0:
        print("[x] ffmpeg 失败（exit %d）" % p.returncode)
        print("    " + " ".join(args))
        for t in (p.stderr or "").strip().splitlines()[-5:]:
            print("    " + t)
        sys.exit(2)


def parse_time(s):
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if ":" in s:
        v = 0.0
        for x in s.split(":"):
            v = v * 60 + float(x)
        return v
    return float(s)


def load(p):
    y, sr = sf.read(p, always_2d=True, dtype="float32")
    return y, sr


def dur_of(p) -> float:
    y, sr = load(p)
    return y.shape[0] / sr


def peak_dbfs(p) -> float:
    y, _ = load(p)
    pk = float(abs(y).max()) if y.size else 0.0
    return 20 * math.log10(max(pk, 1e-9))


def encode_normalized(src_wav, out_ogg, peak_target=-1.0) -> None:
    gain = peak_target - peak_dbfs(src_wav)
    d = os.path.dirname(os.path.abspath(out_ogg))
    os.makedirs(d or ".", exist_ok=True)
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-i", src_wav, "-af", "volume=%.2fdB" % gain, *OGG_ARGS, out_ogg])


# ---------------------------------------------------------------- 特征处理

def af_mute(tempo=0.82, tone=8500):
    """淡：放慢、高频柔化、加长混响 —— 更安静的一遍。"""
    return ("atempo=%.4f,lowpass=f=%d,"
            "aecho=0.8:0.88:120|240|360:0.30|0.18|0.10" % (tempo, tone))


def af_low(semitones=-5, tempo=0.82):
    """低：整体降调（只用 asetrate 降调、再用 atempo 还原速度）＋ 低架提升 ＋ 减高频。

    最终 atempo = (1/ratio) × tempo：前者抵消降调带来的减速，后者才是我们要的「更慢」。
    """
    ratio = 2.0 ** (semitones / 12.0)
    return ("asetrate=%d,aresample=%d,atempo=%.6f,"
            "bass=g=6:f=180,treble=g=-4:f=6000,"
            "aecho=0.8:0.9:90|180:0.25|0.13"
            % (int(round(SR_BASE * ratio)), SR_BASE, (1.0 / ratio) * tempo))


def crossfade_loop(src, out_wav, target, fade) -> None:
    """把 src（长 target+fade）收成 target 秒的**无缝**循环（numpy 实现）。

    为什么不用 ffmpeg `acrossfade`：本机 ffmpeg 上 [0:a]→asplit→两条 atrim→acrossfade 给出空流
    （102 s 进、0.13 s 出，且 exit 0 不报错），排查成本高于自己写。

    做法（比 acrossfade 更规范）：把「接续素材」seg[L:L+F] 按 1→0 的权重**混进开头** seg[0:F]，
    开头同时按 0→1 淡入本来的内容。这样 wrap 时 out[L-1]=seg[L-1] → out[0]≈seg[L]，时间轴真正连续；
    acrossfade 那套则是把开头又完整放了一遍，接缝处会听到开头素材重复。
    """
    y, sr = load(src)
    need = int(round((target + fade) * sr))
    n = y.shape[0]
    if n < need:                       # 编解码的帧对齐误差：用开头补齐（不足 1 帧，听不出）
        pad = np.zeros((need - n, y.shape[1]), dtype=y.dtype)
        pad[:min(need - n, n)] = y[:min(need - n, n)]
        y = np.concatenate([y, pad], axis=0)
    seg = y[:need]

    L = int(round(target * sr))
    F = max(1, int(round(fade * sr)))
    out = seg[:L].copy()
    tail = seg[L:L + F]
    head = out[:F].copy()
    w = np.linspace(0.0, 1.0, F, dtype="float32")[:, None]
    out[:F] = head * w + tail * (1.0 - w)      # 0→1 淡入本身内容，1→0 淡出接续素材
    sf.write(out_wav, out, sr, subtype="FLOAT")


# ---------------------------------------------------------------- 核心

KINDS = {
    "mute": dict(label="淡：放慢＋高频柔化＋加长混响", file="bgm_樱_主题_淡.ogg"),
    "low":  dict(label="低：整体降调＋低架提升＋减高频", file="bgm_樱_主题_低.ogg"),
    "loop": dict(label="主曲：原样裁段＋交叉淡化循环",   file="bgm_樱_主题.ogg"),
}


def derive(src, out, kind, start=0.0, target=100.0, fade=2.0, tempo=0.82, semitones=-5):
    """走完整流水线，返回 (实际时长, 说明)。"""
    if kind == "loop":
        tempo_eff, af = 1.0, None
    elif kind == "mute":
        tempo_eff, af = tempo, af_mute(tempo)
    elif kind == "low":
        tempo_eff, af = tempo, af_low(semitones, tempo)
    else:
        raise SystemExit("[x] 未知 kind：%s" % kind)

    need_src = (target + fade) * tempo_eff          # 处理前需要裁多长
    total = dur_of(src)
    if start + need_src > total + 1e-3:
        print("[!] 母带只剩 %.2f s（从 %.2f s 起），不足 %.2f s → 自动把起点前移"
              % (total - start, start, need_src))
        start = max(0.0, total - need_src)
        if start + need_src > total + 1e-3:
            print("[x] 母带总长 %.2f s < 需要 %.2f s" % (total, need_src)); raise SystemExit(2)

    tmpd = tempfile.mkdtemp(prefix="derive_")
    mid1 = os.path.join(tmpd, "mid1.wav")
    mid2 = os.path.join(tmpd, "mid2.wav")

    # ① 裁段（＋② 特征处理）
    chain = "atrim=start=%.4f:end=%.4f,asetpts=PTS-STARTPTS" % (start, start + need_src)
    if af:
        chain += "," + af
    run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-af", chain, mid1])

    # ③ 交叉淡化 → 目标长度的无缝循环
    crossfade_loop(mid1, mid2, target, fade)

    # ④ 归一化 ＋ 编 ogg
    encode_normalized(mid2, out)
    shutil.rmtree(tmpd, ignore_errors=True)

    got = dur_of(out)
    note = ("裁 %.1f s（母带 %.1f–%.1f s）→ %s → 交叉淡化 %.1f s"
            % (need_src, start, start + need_src, KINDS[kind]["label"].split("：", 1)[-1], fade))
    return got, note


def cmd_batch(args) -> int:
    if not os.path.exists(args.src):
        print("[x] 找不到源：%s" % args.src); return 2
    outdir = os.path.join(BGM_DIR, "_派生")
    os.makedirs(outdir, exist_ok=True)
    print("母带：%s  (%.2f s)" % (os.path.basename(args.src), dur_of(args.src)))
    print("产出 → %s\n" % outdir)
    rc = 0
    for kind in ("mute", "low", "loop"):
        out = os.path.join(outdir, KINDS[kind]["file"])
        print("── %s ──" % kind)
        try:
            got, note = derive(args.src, out, kind, 0.0, args.len, args.fade, args.tempo, args.semitones)
        except SystemExit as e:
            rc = 1; print(e); continue
        print("[ok] %-22s %7.2f s  %5.2f MB  峰值 %5.1f dBFS"
              % (os.path.basename(out), got, os.path.getsize(out) / 1e6, peak_dbfs(out)))
        print("     %s\n" % note)
    print("★ 选中的那版：移到上一层（`素材基准/音频/BGM/`）再跑 `python tools/deploy-bgm.py` 部署进引擎。")
    return rc


def cmd_info(args) -> int:
    srcs = args.src if isinstance(args.src, list) else [args.src]
    for p in srcs:
        if not os.path.exists(p):
            print("[x] 找不到：%s" % p); continue
        y, sr = load(p)
        print("%-40s %8.2f s  %6d Hz  %d ch  峰值 %5.1f dBFS"
              % (os.path.basename(p), y.shape[0] / sr, sr, y.shape[1], peak_dbfs(p)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="从成品母带 DSP 派生 BGM 变奏（ogg）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p, need_out=True):
        p.add_argument("--src", required=True, help="成品母带（ogg/wav/mp3）")
        if need_out:
            p.add_argument("--out", required=True, help="输出 ogg 路径")
        p.add_argument("--len", type=float, default=100.0, help="目标循环长度秒，默认 100")
        p.add_argument("--fade", type=float, default=2.0, help="交叉淡化秒数，默认 2")

    p = sub.add_parser("mute", help="淡：放慢+高频柔化+加长混响")
    add_common(p); p.add_argument("--tempo", type=float, default=0.82, help="速度倍率（<1 更慢），默认 0.82")

    p = sub.add_parser("low", help="低：整体降调+低架提升")
    add_common(p)
    p.add_argument("--semitones", type=int, default=-5, help="半音数（负=降），默认 -5")
    p.add_argument("--tempo", type=float, default=0.82, help="速度倍率，默认 0.82")

    p = sub.add_parser("loop", help="原样裁段 + 交叉淡化循环")
    add_common(p); p.add_argument("--start", default="0:00")

    p = sub.add_parser("batch", help="一次出齐「淡/低/循环」到 音频/BGM/_派生/")
    p.add_argument("--src", required=True)
    p.add_argument("--len", type=float, default=100.0)
    p.add_argument("--fade", type=float, default=2.0)
    p.add_argument("--tempo", type=float, default=0.82)
    p.add_argument("--semitones", type=int, default=-5)

    p = sub.add_parser("info", help="看时长/采样率/峰值")
    p.add_argument("--src", required=True, nargs="+")

    args = ap.parse_args()
    if args.cmd == "info":
        return cmd_info(args)
    if args.cmd == "batch":
        return cmd_batch(args)
    if not os.path.exists(args.src):
        print("[x] 找不到源：%s" % args.src); return 2
    got, note = derive(args.src, args.out, args.cmd,
                       parse_time(getattr(args, "start", 0.0)),
                       args.len, args.fade,
                       getattr(args, "tempo", 0.82), getattr(args, "semitones", -5))
    print("[ok] %s  %.2f s  %.2f MB  峰值 %.1f dBFS"
          % (os.path.basename(args.out), got, os.path.getsize(args.out) / 1e6, peak_dbfs(args.out)))
    print("     %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
