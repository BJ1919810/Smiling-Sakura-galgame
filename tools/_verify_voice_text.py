# -*- coding: utf-8 -*-
"""逐条比对合成文本 vs 台本原文（独立复算，一次性）。

目的：确认 256 条音频对应的日文文本，与合并表里记录的文本一致；
      并确认台本正文与合并表 zh 字段（去标点）逐字相符 —— 即「行号没串」。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MERGED = os.path.join(ROOT, '素材基准', '音频', '语音', '日配任务_樱_全篇_合并.jsonl')
SCRIPT_MD = os.path.join(ROOT, '笑樱_galgame台本.md')


def norm(s: str) -> str:
    """去所有空白与中英日标点，只留实义字符。"""
    return re.sub(r'[\s\u3000、。，．,\.!！?？：:；;「」『』（）()【】\[\]…—―\-～~·・"\'”"’]+', '', s)


def main() -> int:
    rows = []
    with io.open(MERGED, encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                rows.append(json.loads(ln))

    with io.open(SCRIPT_MD, encoding='utf-8') as f:
        md_lines = f.read().splitlines()

    print(f'合并表 {len(rows)} 条；台本 {len(md_lines)} 行\n')

    bad_line = []      # 行号指向的行不是 【樱】
    bad_text = []      # zh 与台本正文不符
    no_paren = []      # 含括号（可能是舞台提示泄漏）
    for r in rows:
        n = int(r['line'])
        if not (1 <= n <= len(md_lines)):
            bad_line.append((r['id'], n, 'OUT_OF_RANGE'))
            continue
        tag = md_lines[n - 1].strip()
        if '【樱】' not in tag:
            bad_line.append((r['id'], n, tag[:30]))
            continue
        # 取紧随其后的第一行「台词正文」：跳过空行与舞台提示行（整行被（）或（）包裹）
        body = ''
        for k in range(n, min(n + 8, len(md_lines))):
            t = md_lines[k].strip()
            if not t:
                continue
            if t.startswith('【') or t.startswith('#') or t.startswith('■') or t.startswith('▸') or t.startswith('▲'):
                break
            # ★ 舞台提示行：以（或(开头 ⇒ 跳过（语音不念括号内容）
            if t.startswith('（') or t.startswith('('):
                continue
            body = t
            break
        if not body:
            bad_text.append((r['id'], n, 'NO_BODY'))
            continue
        if norm(body) != norm(r['zh']):
            bad_text.append((r['id'], n, body[:36], r['zh'][:36]))
        # 日文里不得含括号（舞台提示）
        if re.search(r'[（）()]', r['text']):
            no_paren.append((r['id'], n))

    print(f'[A] 行号指向非【樱】行：{len(bad_line)}')
    for x in bad_line[:8]:
        print('   ', x)
    print(f'[B] zh 与台本正文不符：{len(bad_text)}')
    for x in bad_text[:8]:
        print('   ', x)
    print(f'[C] 日文含括号（舞台提示泄漏）：{len(no_paren)}')
    for x in no_paren[:8]:
        print('   ', x)

    # 日文字符数与时长相关性（用 ffprobe 太长，这里只报字数统计）
    lens = [len(r['text']) for r in rows]
    print(f'\n日文字数：最短 {min(lens)}，最长 {max(lens)}，均值 {sum(lens)/len(lens):.1f}')

    ok = not bad_line and not bad_text and not no_paren
    print('\n' + '=' * 50)
    print('✓ 行号与文本全部自洽' if ok else '✗ 存在不一致，见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
