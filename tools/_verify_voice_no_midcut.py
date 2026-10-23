# -*- coding: utf-8 -*-
"""★ 铁证级验证：裁剪后的 ogg 是否**只是源 wav 的一段连续切片**（没动中间）。

主人问：「确定没裁中间的静音是吧？」

代码上 `plan_trim` 只取「第一个超门限帧」与「最后一个超门限帧」两个边界
（`start = first_loud - 0.04`、`end = last_loud + 0.08`），中间**没有任何删减逻辑**。
但「代码看着对」不算证据，这里做**波形级**验证：

  把 wav 与 ogg 各自切成 20ms 帧的峰值包络，在 wav 的包络上滑窗找 ogg 包络的
  最佳匹配位置，算归一化互相关系数 r。
  · 若 ogg 是 wav 的**连续切片** ⇒ r ≈ 1.00（有损编码只带来极小差异）
  · 若中间被删过 ⇒ 后半段会整体错位 ⇒ r 明显掉下来

输出最差 r 与不达标条数；任何一条 r < 0.99 就算失败。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MERGED = os.path.join(ROOT, '素材基准', '音频', '语音', '日配任务_樱_全篇_合并.jsonl')
VOICE_DIR = os.path.join(ROOT, 'public', 'assets', 'audio', 'voice')
RAW_DIR = os.path.join(ROOT, '.workbuddy', 'backup', 'voice_raw')
FFMPEG = r'D:\ffmpeg\bin\ffmpeg.exe'

SR = 44100
FRAME = int(SR * 0.02)  # 20ms
PASS_R = 0.99
# 内容长度的容差。★ 与**帧粒度**绑定：裁切起点不是帧对齐的（ogg 的包络帧相对 wav
# 整体错开一个亚帧），且 libvorbis 会让紧邻边界的那 1–2 帧峰值衰减、可能跌破门限
# ⇒ 最多 3 帧（60ms）的偏差属于**测量分辨率极限**，不能当作「动了中间」的证据。
# 真正的决定性判据是相关系数 r（任何中间删除都会让后半段错位、r 掉到 0.9 以下）。
LEN_TOL = 4 * 0.02  # 0.08s


def env_of(path: str) -> np.ndarray:
    """解码 → 20ms 帧峰值包络（归一化到峰值 1）。"""
    r = subprocess.run(
        [FFMPEG, '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', str(SR), '-'],
        capture_output=True,
    )
    x = np.frombuffer(r.stdout, dtype='<f4')
    n = x.size // FRAME
    if n < 2:
        return np.zeros(0, dtype=np.float64)
    e = np.abs(x[: n * FRAME].reshape(n, FRAME)).max(axis=1).astype(np.float64)
    p = e.max()
    return e / p if p > 0 else e


def best_match(a: np.ndarray, b: np.ndarray) -> tuple[float, int]:
    """在 a 上滑窗找 b 的最佳位置，返回 (最大归一化相关系数, 起点帧)。"""
    la, lb = a.size, b.size
    if la < lb or lb < 3:
        return -1.0, -1
    nb = float(np.dot(b, b))
    if nb <= 0:
        return -1.0, -1
    best_r, best_i = -2.0, -1
    for i in range(0, la - lb + 1):
        seg = a[i : i + lb]
        ns = float(np.dot(seg, seg))
        if ns <= 0:
            continue
        r = float(np.dot(seg, b)) / (ns ** 0.5 * nb ** 0.5)
        if r > best_r:
            best_r, best_i = r, i
    return best_r, best_i


def content_len(e: np.ndarray) -> float:
    """「有效内容长度」（秒）：第一个超门限帧 → 最后一个超门限帧之间的时长。

    ⚠️ 这是**正交**于互相关的第二重证据：包络已归一化到峰值 1，
    故门限 0.01 ≈ 峰值 −40 dB。
    若中间有静音被删，首末有声帧之间的总时长必然**变短**（内容被压缩）；
    只裁头尾则**完全不变**。
    """
    idx = np.nonzero(e > 0.01)[0]
    if idx.size == 0:
        return 0.0
    return float(idx[-1] - idx[0] + 1) * 0.02


def main() -> int:
    rows = []
    with io.open(MERGED, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))

    print(f'逐条验证 {len(rows)} 条：ogg 是否为源 wav 的连续切片\n')
    rs: list[tuple[float, str, int]] = []
    bad: list[tuple[str, float]] = []
    len_diffs: list[tuple[float, str]] = []
    skipped = 0

    for k, r in enumerate(rows):
        wav = os.path.join(RAW_DIR, f"{r['id']}.wav")
        ogg = os.path.join(VOICE_DIR, f"v_{int(r['line']):05d}.ogg")
        if not (os.path.exists(wav) and os.path.exists(ogg)):
            bad.append((r['id'], -1.0))
            continue
        a, b = env_of(wav), env_of(ogg)
        if b.size == 0:
            bad.append((r['id'], -1.0))
            continue
        # 证据 ①：波形级 —— ogg 是不是 wav 的连续切片
        corr, at = best_match(a, b)
        rs.append((corr, r['id'], at))
        if corr < PASS_R:
            bad.append((r['id'], corr))
        # 证据 ②：内容长度 —— 首末有声帧之间的距离有没有被压缩
        len_diffs.append((abs(content_len(a) - content_len(b)), r['id']))
        skipped += 1
        if (k + 1) % 64 == 0:
            print(f'  … {k + 1}/{len(rows)}')

    rs.sort()
    len_diffs.sort(reverse=True)
    print(f'\n比对完成：{skipped} 条')
    print(f'  最小 r = {rs[0][0]:.5f}（{rs[0][1]}）')
    print(f'  中位 r = {rs[len(rs) // 2][0]:.5f}')
    print(f'  最大 r = {rs[-1][0]:.5f}')
    print('  最差的 5 条：')
    for corr, rid, at in rs[:5]:
        print(f'    {rid}  r={corr:.5f}  起点帧={at}')

    print(f'\n证据②「有效内容长度」（首末有声帧之间的距离，中间若被删必然变短）：')
    print(f'  最大偏差 = {len_diffs[0][0]:.3f}s（{len_diffs[0][1]}）')
    print(f'  中位偏差 = {len_diffs[len(len_diffs) // 2][0]:.3f}s')
    buckets = {'=0': 0, '≤1帧(20ms)': 0, '≤2帧(40ms)': 0, '≤4帧(80ms)': 0, '>4帧': 0}
    for d, _ in len_diffs:
        if d == 0:
            buckets['=0'] += 1
        elif d <= 0.021:
            buckets['≤1帧(20ms)'] += 1
        elif d <= 0.041:
            buckets['≤2帧(40ms)'] += 1
        elif d <= LEN_TOL + 1e-9:
            buckets['≤4帧(80ms)'] += 1
        else:
            buckets['>4帧'] += 1
    print('  偏差分布：' + '  '.join(f'{k}={v}' for k, v in buckets.items()))

    over = [x for x in len_diffs if x[0] > LEN_TOL + 1e-9]
    print(f'  超过容差 {LEN_TOL:.2f}s 的条数：{len(over)}/{len(len_diffs)}')
    # ★ 关键：容差外的条目**同时**看它的相关系数 ——
    #   若 r 仍很高（≈0.999）说明波形整体对齐、必然是切片，Δ 只是边界帧的测量噪声；
    #   若 r 也低，才是真的动了中间。
    corr_by_id = {rid: c for c, rid, _ in rs}
    for d, rid in over[:8]:
        print(f'    {rid}  Δ={d:.3f}s   r={corr_by_id.get(rid, float("nan")):.5f}')

    print('\n' + '=' * 56)
    if bad or over:
        if bad:
            print(f'✗ {len(bad)} 条相关系数未达标（r < {PASS_R}）：')
            for rid, c in bad[:10]:
                print(f'    {rid}  r={c:.5f}')
        if over:
            print(f'✗ {len(over)} 条内容长度被压缩 > {LEN_TOL:.2f}s —— 疑似动过中间')
            for d, rid in over[:10]:
                print(f'    {rid}  Δ={d:.3f}s')
        return 1
    print(f'✓ 全部 {skipped} 条 r ≥ {PASS_R}，且内容长度偏差 ≤ {LEN_TOL:.2f}s（4 帧）')
    print('  ⇒ ogg 均为源 wav 的**一段连续切片**，中间内容零改动')
    return 0


if __name__ == '__main__':
    sys.exit(main())
