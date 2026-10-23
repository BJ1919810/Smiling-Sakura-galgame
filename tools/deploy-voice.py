# -*- coding: utf-8 -*-
"""
把云卡合成出的 sakura_NNNN.wav 部署进游戏。

流程（三步）：
  1. 读「合成 id → 台本行号」映射：
     - 合成时用的 `jobs_sakura_all.jsonl` 是按行号排序后重编号的
       ⇒ 第 N 条（1 起）= 合并表里排序后的第 N 条 ⇒ id `sakura_%04d`
     - 所以映射 = 「合并表按 line 排序后，第 N 条的 line」
  2. 从云卡下载 wav（由 tools/cloud_sh.py --get，本脚本只做本地整理）
  3. 转 44.1kHz mono Ogg（libvorbis q5，与项目音频规范一致）→ 命名 `v_<行号5位>.ogg`
     放进 `public/assets/audio/voice/`

★ 为什么不归一化响度：主人令「归一化到14dB先不做了」。
★ 为什么必须转 ogg：256 个 22k wav 约 45 MB，且项目音频一律 44.1k mono ogg。

★★ `--trim`：裁掉语音自带的**首尾静音**（2026-09-19 主人定）
  背景：IndexTTS 的输出**每条都自带一小段静音**（抽样 24/256 实测：头中位 0.30s、
  尾中位 0.34s）。后果有二：
    ① 玩家点完要等 ~0.3s 才听到开口（很"迟钝"）；
    ② 自动播放「等语音播完 + 呼吸 350ms」的实际停顿变成 ~0.69s（拖）。
  ⇒ 裁掉静音后：开口跟手，且「播完」＝「说完」，呼吸间隔才有准确含义。
  ⚠️ 必须从**原始 wav** 裁再转码（只压一次）；⛔ 别拿 ogg 反复裁（二次有损）。

用法：
  python tools/deploy-voice.py --src <下载目录>        # 从目录取 wav → 转码部署
  python tools/deploy-voice.py --src <目录> --dry-run  # 只报告
  python tools/deploy-voice.py --src <目录> --trim     # ★ 裁首尾静音后再转码
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MERGED = os.path.join(ROOT, '素材基准', '音频', '语音', '日配任务_樱_全篇_合并.jsonl')
OUT_DIR = os.path.join(ROOT, 'public', 'assets', 'audio', 'voice')
FFMPEG = r'D:\ffmpeg\bin\ffmpeg.exe'

# 与项目音频规范一致（见 normalize-se.py）
TARGET_SR = 44100
OGG_Q = '5'

# ——— 裁静音参数（--trim）———
# 门限 = 峰值 − 40 dB。实测语音峰值约 −3 dBFS ⇒ 门限约 −43 dBFS，
# 低于此电平的基本只有底噪，裁掉安全。
TRIM_FLOOR_DB = -40.0
# 头部保留量（s）——避免把起音的辅音/气音削掉
TRIM_HEAD_KEEP = 0.04
# 尾部保留量（s）——留出自然衰减，别"戛然而止"
TRIM_TAIL_KEEP = 0.08
# 首尾静音都小于这个值就**不裁**（本来就紧，别折腾）
TRIM_MIN_SILENCE = 0.15
# 裁剪后不得短于这个长度（s）——防御性下限
TRIM_MIN_LEN = 0.10
# 帧长（s）——包络分析的粒度
TRIM_FRAME = 0.02


def load_id_to_line() -> dict[str, int]:
    """合成 id（sakura_NNNN）→ 台本行号。

    ★ 依据：`voice_sakura_merge.py` 先把各章合并、**按 line 排序**、再重编号为
      `sakura_%04d`（从 1 起）；云卡 `index-tts-gen.py` 逐行读同一个 jsonl、
      第 N 行输出 `sakura_%04d`（同样的编号口径）。
      ⚠️ 两边编号口径必须一致 —— 本函数不猜，**直接从合并表里读 id 字段**。
    """
    rows = []
    with io.open(MERGED, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    m: dict[str, int] = {}
    for r in rows:
        m[r['id']] = int(r['line'])
    return m


def decode_mono(path: str, sr: int = TARGET_SR) -> np.ndarray:
    """用 ffmpeg 解码成单声道 f32 的 numpy 数组（绕开 wave 模块的位深差异）。"""
    r = subprocess.run(
        [FFMPEG, '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', str(sr), '-'],
        capture_output=True,
    )
    return np.frombuffer(r.stdout, dtype='<f4')


def plan_trim(path: str) -> tuple[float, float, float, float] | None:
    """算裁剪区间。

    返回 `(start, length, head_silence, tail_silence)`；**不需要裁**时返回 `None`。

    判定：把音频切成 20ms 帧取峰值包络，门限＝`峰值 − TRIM_FLOOR_DB`（相对值，
    这样可以自适应不同响度的语音）。找第一个/最后一个超门限的帧，其外侧即为静音。

    ⚠️ 保守策略（宁可少裁）：
      · 首尾静音**都**小于 `TRIM_MIN_SILENCE` ⇒ 不裁（本来就紧）；
      · 头部多留 `TRIM_HEAD_KEEP`、尾部多留 `TRIM_TAIL_KEEP`，避免削到起音辅音与自然衰减；
      · 裁完短于 `TRIM_MIN_LEN` ⇒ 不裁（防御性）。
    """
    x = decode_mono(path)
    if x.size == 0:
        return None
    frame = max(1, int(TARGET_SR * TRIM_FRAME))
    n = x.size // frame
    if n < 3:
        return None

    env = np.abs(x[: n * frame].reshape(n, frame)).max(axis=1)
    peak = float(env.max())
    if peak <= 0:
        return None
    thr = peak * (10 ** (TRIM_FLOOR_DB / 20))
    loud = np.nonzero(env > thr)[0]
    if loud.size == 0:
        return None

    total = x.size / TARGET_SR
    head = float(loud[0] * frame) / TARGET_SR
    tail = float((n - 1 - loud[-1]) * frame) / TARGET_SR

    if head < TRIM_MIN_SILENCE and tail < TRIM_MIN_SILENCE:
        return None

    start = max(0.0, head - TRIM_HEAD_KEEP)
    end = min(total, total - tail + TRIM_TAIL_KEEP)
    length = end - start
    if length < TRIM_MIN_LEN or length >= total - 1e-3:
        return None
    return start, length, head, tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='存放 sakura_NNNN.wav 的目录')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--keep-wav', action='store_true', help='保留转换前的 wav（默认不删源）')
    ap.add_argument('--trim', action='store_true',
                    help='★ 裁掉语音自带的头/尾静音（见文件头说明）。'
                         '⚠️ 会**忽略 mtime 幂等检查**（否则已存在的旧 ogg 不会被重裁）')
    args = ap.parse_args()

    id2line = load_id_to_line()
    print(f'映射表：{len(id2line)} 条（id → 台本行号）')
    if args.trim:
        print(f'★ 裁剪模式：门限 峰值{TRIM_FLOOR_DB:+.0f}dB，'
              f'头留 {TRIM_HEAD_KEEP}s / 尾留 {TRIM_TAIL_KEEP}s，'
              f'静音 < {TRIM_MIN_SILENCE}s 的不裁')

    if not os.path.isdir(args.src):
        print(f'✗ 源目录不存在：{args.src}')
        return 1

    wavs = sorted(f for f in os.listdir(args.src) if f.lower().endswith('.wav'))
    print(f'源目录：{args.src}\n找到 wav：{len(wavs)} 个\n')

    os.makedirs(OUT_DIR, exist_ok=True)

    ok = 0
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    trimmed: list[tuple[str, float, float]] = []  # (id, 省下头部, 省下尾部)
    saved_total = 0.0
    for f in wavs:
        stem = os.path.splitext(f)[0]
        if stem not in id2line:
            skipped.append(f'{f}（id 不在映射表里）')
            continue
        line = id2line[stem]
        dst = os.path.join(OUT_DIR, f'v_{line:05d}.ogg')
        src_path = os.path.join(args.src, f)

        # ⚠️ 裁剪模式下**不做** mtime 幂等跳过 —— 否则上一轮未裁的 ogg 会被当成"已完成"
        if not args.trim and os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src_path):
            ok += 1
            continue
        if args.dry_run:
            ok += 1
            continue

        cmd = [FFMPEG, '-y', '-hide_banner', '-loglevel', 'error']
        if args.trim:
            plan = plan_trim(src_path)
            if plan is not None:
                start, length, head, tail = plan
                # -ss 放在 -i **之后**（输出定位）：解码后精确裁剪，不受容器 seek 影响
                cmd += ['-i', src_path, '-ss', f'{start:.4f}', '-t', f'{length:.4f}']
                save_h = max(0.0, head - TRIM_HEAD_KEEP)
                save_t = max(0.0, tail - TRIM_TAIL_KEEP)
                trimmed.append((stem, save_h, save_t))
                saved_total += save_h + save_t
            else:
                cmd += ['-i', src_path]
        else:
            cmd += ['-i', src_path]

        cmd += [
            '-ac', '1',
            '-ar', str(TARGET_SR),
            # 3ms 淡入：裁点虽在静音里（电平极低），仍防一下爆音
            '-af', 'afade=t=in:st=0:d=0.003',
            '-c:a', 'libvorbis',
            '-q:a', OGG_Q,
            dst,
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            failed.append((f, r.stderr.decode('utf-8', 'replace')[:200]))
        else:
            ok += 1

    print(f'部署成功：{ok}')
    if args.trim:
        print(f'\n★ 裁剪：{len(trimmed)} 条有静音可裁，'
              f'合计省下 {saved_total:.1f}s（均值 {saved_total / max(1, len(trimmed)):.2f}s/条）')
        if trimmed:
            top = sorted(trimmed, key=lambda t: -(t[1] + t[2]))[:5]
            print('  裁得最多的 5 条：')
            for stem, h, t in top:
                print(f'    {stem}  头 −{h:.2f}s  尾 −{t:.2f}s')
    if skipped:
        print(f'跳过：{len(skipped)}')
        for x in skipped[:10]:
            print('  -', x)
    if failed:
        print(f'✗ 转码失败：{len(failed)}')
        for name, err in failed[:10]:
            print('  -', name, err)

    # 统计产出
    if os.path.isdir(OUT_DIR) and not args.dry_run:
        files = [x for x in os.listdir(OUT_DIR) if x.endswith('.ogg')]
        total = sum(os.path.getsize(os.path.join(OUT_DIR, x)) for x in files)
        print(f'\n产出目录：{OUT_DIR}')
        print(f'  ogg 文件 {len(files)} 个，合计 {total / 1048576:.1f} MB')
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
