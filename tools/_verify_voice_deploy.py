# -*- coding: utf-8 -*-
"""语音部署独立复算（一次性核验，不进入常规链路）。

检查项：
  1. 合并表 256 条 line 唯一、id 唯一
  2. 每个 id 对应的 `v_<行号5位>.ogg` 存在
  3. 反查：产出目录里每个 ogg 都能映射回一条合并表记录（无孤儿）
  4. 时长一致性（源 wav vs ogg，用 ffprobe，Δ < 0.05s）
  5. manifest voice 表（重跑后）与目录一致
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MERGED = os.path.join(ROOT, '素材基准', '音频', '语音', '日配任务_樱_全篇_合并.jsonl')
VOICE_DIR = os.path.join(ROOT, 'public', 'assets', 'audio', 'voice')
RAW_DIR = os.path.join(ROOT, '.workbuddy', 'backup', 'voice_raw')
MANIFEST = os.path.join(ROOT, 'src', 'generated', 'assets.manifest.json')
FFPROBE = r'D:\ffmpeg\bin\ffprobe.exe'

fails: list[str] = []


def load_merged():
    rows = []
    with io.open(MERGED, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))
    return rows


def dur(path: str) -> float:
    r = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=nw=1:nk=1', path],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip())


def main() -> int:
    rows = load_merged()
    print(f'[1] 合并表：{len(rows)} 条')
    if len(rows) != 256:
        fails.append(f'合并表条数 {len(rows)} != 256')

    ids = [r['id'] for r in rows]
    lines = [int(r['line']) for r in rows]
    if len(set(ids)) != len(ids):
        dup = [x for x in set(ids) if ids.count(x) > 1]
        fails.append(f'id 重复：{dup[:5]}')
    if len(set(lines)) != len(lines):
        dup = sorted({x for x in lines if lines.count(x) > 1})
        fails.append(f'line 重复：{dup[:5]}')
    print(f'     id 唯一 {len(set(ids))}，line 唯一 {len(set(lines))}')

    print(f'[2] 逐条检查 ogg 存在性')
    missing = []
    for r in rows:
        p = os.path.join(VOICE_DIR, f"v_{int(r['line']):05d}.ogg")
        if not os.path.exists(p):
            missing.append(f"{r['id']} → v_{int(r['line']):05d}.ogg")
    if missing:
        fails.append(f'缺失 ogg {len(missing)} 个：{missing[:5]}')
    else:
        print('     256/256 全部存在 ✓')

    print(f'[3] 反查孤儿 ogg')
    expected = {f"v_{int(r['line']):05d}.ogg" for r in rows}
    actual = {f for f in os.listdir(VOICE_DIR) if f.endswith('.ogg')}
    orphan = actual - expected
    if orphan:
        fails.append(f'孤儿 ogg {len(orphan)} 个：{sorted(orphan)[:5]}')
    else:
        print(f'     目录 {len(actual)} 个，无孤儿 ✓')

    print(f'[4] 时长关系（ogg 已裁静音 ⇒ 必须 0 < ogg ≤ wav + 0.05）')
    n = 0
    bad: list[tuple[str, float, float]] = []
    shrink: list[float] = []
    for r in rows:
        src = os.path.join(RAW_DIR, f"{r['id']}.wav")
        dst = os.path.join(VOICE_DIR, f"v_{int(r['line']):05d}.ogg")
        if not os.path.exists(src):
            fails.append(f"源 wav 缺失：{r['id']}.wav")
            continue
        a, b = dur(src), dur(dst)
        n += 1
        shrink.append(a - b)
        # ★ 裁静音后 ogg 必然**短于** wav；但不能长、更不能为 0 / 负
        if b <= 0 or b > a + 0.05:
            bad.append((r['id'], a, b))
    avg = sum(shrink) / len(shrink) if shrink else 0.0
    print(f'     比对 {n} 条；裁掉静音均值 {avg:.3f}s，'
          f'最大 {max(shrink, default=0):.3f}s，最小 {min(shrink, default=0):.3f}s')
    if bad:
        fails.append(f'时长关系异常 {len(bad)} 条：{bad[:3]}')
    else:
        print('     全部满足 0 < ogg ≤ wav ✓')

    print(f'[5] 核对 manifest voice 表')
    if os.path.exists(MANIFEST):
        with io.open(MANIFEST, encoding='utf-8') as f:
            mf = json.load(f)
        v = mf.get('voice') or {}
        print(f'     manifest.voice 键数：{len(v)}')
        if len(v) != 256:
            fails.append(f'manifest.voice 键数 {len(v)} != 256（需重跑 manifest）')
        # 抽查映射正确性
        bad = []
        for r in rows[:]:
            k = str(int(r['line']))
            if v.get(k) != f"voice/v_{int(r['line']):05d}.ogg":
                bad.append((k, v.get(k)))
        if bad:
            fails.append(f'manifest 映射错 {len(bad)} 条：{bad[:3]}')
        else:
            print('     全部映射正确 ✓')
    else:
        fails.append(f'manifest 不存在：{MANIFEST}')

    # 体积
    files = [f for f in os.listdir(VOICE_DIR) if f.endswith('.ogg')]
    total = sum(os.path.getsize(os.path.join(VOICE_DIR, f)) for f in files)
    print(f'\n产出：{len(files)} 个 ogg，{total / 1048576:.2f} MB')

    print('\n' + '=' * 50)
    if fails:
        print('✗ 核验失败：')
        for x in fails:
            print('  -', x)
        return 1
    print('✓ 全部核验通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
