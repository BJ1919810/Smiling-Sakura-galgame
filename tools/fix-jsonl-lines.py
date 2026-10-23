# -*- coding: utf-8 -*-
"""★ 通用「台本行号反查器」—— 把任务表里的 line 一律校正为「角色标签行」。

为什么需要它（三次踩坑的总结）：
  ① 语音映射键是**角色标签行**，不是台词正文行；
  ② 舞台提示若单独占一行（`（又补一句）`），偏移**不是固定 ±1** ⇒ 不能硬修；
  ③ **正文可能重复**（「这长椅是我的。」出现 2 次；「龙司同学。」出现 2 次）
     ⇒ 按内容查表会撞车 ⇒ 改成「按出现次序分配」：第 n 次 → 台本里第 n 个同名标签行。

用法：
    python tools/fix-jsonl-lines.py --char 龙司 --jsonl <任务表.jsonl> [--dry-run]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT_MD = os.path.join(ROOT, '笑樱_galgame台本.md')


def norm(s: str) -> str:
    return re.sub(r'[\s\u3000、。，．,\.!！?？：:；;「」『』（）()【】\[\]…—―\-～~·・'
                  r'"\'“”‘’]+', '', s)


def build_map(char: str) -> tuple[dict[str, list[int]], list[str]]:
    md = io.open(SCRIPT_MD, encoding='utf-8').read().splitlines()
    tag = f'【{char}】'
    m: dict[str, list[int]] = defaultdict(list)
    for i, line in enumerate(md):
        if line.strip() != tag:
            continue
        n = i + 1  # 1 起
        body = ''
        for k in range(n, min(n + 8, len(md))):
            t = md[k].strip()
            if not t:
                continue
            if t.startswith(('【', '#', '■', '▸', '▲', '◆', '〔', '`')):
                break
            if t.startswith(('（', '(')):
                continue
            body = t
            break
        if body:
            m[norm(body)].append(n)
    total = sum(len(v) for v in m.values())
    print(f'台本【{char}】标签行 {total} 条（去重后 {len(m)} 个正文）')
    for k, v in m.items():
        if len(v) > 1:
            print(f'  ⚠️ 正文重复 {len(v)} 次 → 行号 {v}')
    return m, md


def main() -> int:
    ap = argparse.ArgumentParser(description='把任务表的 line 校正为「角色标签行」')
    ap.add_argument('--char', required=True, help='角色名（台本里的【…】）')
    ap.add_argument('--jsonl', required=True)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.jsonl):
        print(f'✗ 文件不存在：{args.jsonl}')
        return 1

    m, md = build_map(args.char)
    rows = [json.loads(l) for l in io.open(args.jsonl, encoding='utf-8') if l.strip()]
    print(f'任务表 {len(rows)} 条')

    used: Counter[str] = Counter()
    changed, miss, bad_tag = [], [], []
    for r in rows:
        key = norm(r['zh'])
        lst = m.get(key)
        if not lst:
            miss.append((r['id'], r['zh'][:32]))
            continue
        i = used[key]
        used[key] += 1
        if i >= len(lst):
            miss.append((r['id'], f'{r["zh"][:26]}（同名第 {i+1} 次，台本仅 {len(lst)} 次）'))
            continue
        new = lst[i]
        if md[new - 1].strip() != f'【{args.char}】':
            bad_tag.append((r['id'], new, md[new - 1].strip()[:20]))
            continue
        if r['line'] != new:
            changed.append((r['id'], r['line'], new))
            r['line'] = new

    print(f'\n需要修正：{len(changed)} 条')
    for i, old, new in changed[:40]:
        print(f'  {i}: {old} → {new}')
    if len(changed) > 40:
        print(f'  …（共 {len(changed)} 条）')

    if miss:
        print(f'\n✗ 有 {len(miss)} 条在台本里找不到对应正文（**不写盘**）：')
        for i, z in miss[:12]:
            print(f'    {i}  {z}')
    if bad_tag:
        print(f'\n✗ 有 {len(bad_tag)} 条校正后仍未指向【{args.char}】：')
        for x in bad_tag[:8]:
            print(f'    {x}')

    if miss or bad_tag:
        return 1
    if args.dry_run:
        print('\n（演练，未写盘）')
        return 0

    with io.open(args.jsonl, 'w', encoding='utf-8') as f:
        f.write('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n')
    print(f'\n✓ 已写回 {args.jsonl}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
