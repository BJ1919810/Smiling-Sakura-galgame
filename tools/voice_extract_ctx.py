# -*- coding: utf-8 -*-
"""抽取某角色的全部台词 + 上下文，供逐条翻译/标注时参考。

为什么要带上下文：翻译要贴原意、情绪标注要贴剧情 —— 单看一句「不吃拉倒。」
判断不了是耍脾气还是亲昵打趣，必须看前后文。

输出：`.workbuddy/tmp/<角色>_lines.txt`（分章排列，每行标注台本行号）

用法：
    python tools/voice_extract_ctx.py --char 阳太
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, '笑樱_galgame台本.md')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', required=True)
    ap.add_argument('--ctx', type=int, default=5, help='前后各取几行上下文')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    lines = io.open(SCRIPT, encoding='utf-8').read().splitlines()

    # 先标出每一行属于哪章 / 哪个 SCENE
    ch_of: list[int] = [0] * len(lines)
    scene_of: list[str] = [''] * len(lines)
    ch = 0
    scene = ''
    for i, l in enumerate(lines):
        s = l.strip()
        m = re.match(r'^#\s*(?:第\s*(\d+)\s*章|(.*))$', s)
        if m:
            if m.group(1):
                ch = int(m.group(1))
                scene = ''
            elif m.group(2):
                scene = m.group(2).strip()
        if s.startswith('■ SCENE') or s.startswith('■'):
            scene = s
        ch_of[i] = ch
        scene_of[i] = scene

    tag = f'【{args.char}】'
    out: list[str] = []
    out.append(f'# {args.char} 全部台词（含上下文） ｜ 源：笑樱_galgame台本.md\n')
    n = 0
    last_ch = -1
    for i, l in enumerate(lines):
        if l.strip() != tag:
            continue
        # 台词正文
        stage = ''
        body = ''
        for k in range(i + 1, min(i + 8, len(lines))):
            t = lines[k].strip()
            if not t:
                continue
            if t.startswith(('【', '#', '■', '▸', '▲')):
                break
            if t.startswith(('（', '(')):
                stage = t
                continue
            body = t
            break
        if not body:
            continue
        n += 1
        if ch_of[i] != last_ch:
            out.append(f'\n{"=" * 70}\n## 第 {ch_of[i]} 章\n{"=" * 70}')
            last_ch = ch_of[i]
        out.append(f'\n---------- #{n}  L{i+1}  [{scene_of[i]}] ----------')
        for k in range(max(0, i - args.ctx), i):
            t = lines[k].rstrip()
            if t.strip():
                out.append(f'  {k+1:>5}| {t}')
        out.append(f'  {i+1:>5}| {tag}   ← 目标')
        if stage:
            out.append(f'       |   {stage}   （舞台提示，不念）')
        out.append(f'       |   「{body}」   ← 中文台词')
        for k in range(i + 1, min(len(lines), i + 1 + args.ctx)):
            t = lines[k].rstrip()
            if t.strip():
                out.append(f'  {k+1:>5}| {t}')

    outpath = args.out or os.path.join(ROOT, '.workbuddy', 'tmp', f'{args.char}_lines.txt')
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    io.open(outpath, 'w', encoding='utf-8').write('\n'.join(out))
    print(f'>>> 抽出 {n} 条 → {outpath}  ({os.path.getsize(outpath):,} B)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
