#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
笑樱 · 分章副本 → 源文件 合并（把副本里的手改抢救回唯一作者源）

为什么需要它：
  `台本分章/*.md` 是 `tools/split-script.py` 生成的**只读派生副本**。
  一旦你直接在副本上改，再跑一次 split 就被整体覆盖 —— 改动会静默丢失。
  本脚本把副本的改动**合并回 `笑樱_galgame台本.md`**，之后就能安全刷新副本。

  ★ 从此工作流可以反过来（推荐）：
      改 `台本分章/0N_第N章_*.md`  →  `python tools/merge-chapter.py N --apply`
      →  `python tools/split-script.py`（刷新其余副本）
  split 现在带护栏：检测到某章副本有未合并的手改会**跳过该章**而不是覆盖。

用法：
  python tools/merge-chapter.py --list          # 逐章体检：哪些副本与源文件不一致
  python tools/merge-chapter.py 2               # dry-run：只看差异，不写任何文件
  python tools/merge-chapter.py 2 --apply       # 真合并（先自动备份源文件）

  章号：1–8 为正文；0 = 规范区；99 = 附录。
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, '笑樱_galgame台本.md')
OUT = os.path.join(ROOT, '台本分章')
BACKUP = os.path.join(ROOT, '.workbuddy', 'backup')

RE_CH = re.compile(r'^# 第 (\d+) 章[　\s]*(.+?)\s*$')


def read_lines(path: str):
    """返回 (全文, 换行符, 行列表)。换行符原样保留，避免整份文件被改写行尾。"""
    raw = open(path, encoding='utf-8', newline='').read()
    nl = '\r\n' if '\r\n' in raw else '\n'
    return raw, nl, raw.splitlines()


def strip_header(copy_lines: list[str]) -> list[str]:
    """剥掉 split-script.py 写入的 banner（`> ` 行 + 范围行）与 `---` 分隔。"""
    i, n = 0, len(copy_lines)
    while i < n and (copy_lines[i].startswith('> ') or copy_lines[i].strip() == ''):
        i += 1
    if i < n and copy_lines[i].strip() == '---':
        i += 1
        if i < n and copy_lines[i].strip() == '':
            i += 1
    elif i < n:
        print('[!] 副本头部没有 `---` 分隔，按原样处理 —— 请人工确认首行内容。')
    return copy_lines[i:]


def locate(src_lines: list[str]) -> dict:
    """定位每一块在源文件里的行区间 {键: (start, end, 标题)}；键为 '0'..'8' / '99'。"""
    ch = [(i, int(m.group(1)), m.group(2)) for i, l in enumerate(src_lines)
          for m in [RE_CH.match(l)] if m]
    tail = next((i for i, l in enumerate(src_lines) if l.startswith('# 樱线・全篇完')), None)
    total = len(src_lines)
    spans = {}
    if ch:
        spans['0'] = (0, ch[0][0], '规范区')
    for k, (st, no, title) in enumerate(ch):
        en = ch[k + 1][0] if k + 1 < len(ch) else (tail if tail is not None else total)
        spans[str(no)] = (st, en, title)
    if tail is not None:
        spans['99'] = (tail, total, '附录')
    return spans


def find_copy(key: str):
    k = int(key)
    for f in sorted(os.listdir(OUT)):
        if f.startswith(f'{k:02d}_') and f.endswith('.md'):
            return os.path.join(OUT, f)
    return None


def diff_of(src_body: list[str], copy_body: list[str]) -> list[str]:
    return list(difflib.unified_diff(src_body, copy_body,
                                     fromfile='源文件', tofile='副本', lineterm='', n=2))


def main() -> int:
    argv = sys.argv[1:]
    apply_ = '--apply' in argv
    list_ = '--list' in argv
    args = [a for a in argv if not a.startswith('--')]

    if not os.path.exists(SRC):
        print(f'[x] 找不到源文件：{SRC}')
        return 2

    _, nl, src_lines = read_lines(SRC)
    spans = locate(src_lines)

    # ---------- --list：逐章体检 ----------
    if list_ or not args:
        print(f'源文件 {len(src_lines)} 行 ｜ 共 {len(spans)} 块')
        print(f'{"块":<10}{"行数":>7}{"副本":>8}  状态')
        dirty = []
        for key, (st, en, title) in sorted(spans.items(), key=lambda x: int(x[0])):
            path = find_copy(key)
            if not path:
                print(f'第{key:>3}章　　{title:<16}    [x] 缺副本')
                continue
            _, _, copy_all = read_lines(path)
            copy_body = strip_header(copy_all)
            if src_body_eq := (src_lines[st:en] == copy_body):
                state = '一致 ✅'
            else:
                state = '★ 副本有改动 → 需合并'
                dirty.append(key)
            print(f'第{key:>3}章　　{title:<16}{en - st:>5} 行{len(copy_body):>5} 行  {state}')
        print()
        if dirty:
            print('需要合并的块：' + '、'.join(f'第{k}章' for k in dirty))
            print('逐个跑：python tools/merge-chapter.py <章号>        # 先看差异')
            print('         python tools/merge-chapter.py <章号> --apply  # 确认后合并')
        else:
            print('全部一致 ✅ —— 没有副本改动待合并。')
        return 0

    # ---------- 指定块：diff / 合并 ----------
    raw_key = args[0]
    key = raw_key if raw_key == '99' else str(int(raw_key))
    if key not in spans:
        print(f'[x] 源文件里找不到「{raw_key}」（可用：0–8、99）')
        return 2

    st, en, title = spans[key]
    src_body = src_lines[st:en]
    path = find_copy(key)
    if not path:
        print(f'[x] 在 {OUT} 里找不到 {int(key):02d}_*.md 副本')
        return 2
    _, _, copy_all = read_lines(path)
    copy_body = strip_header(copy_all)

    label = f'第 {key} 章' if key not in ('0', '99') else title
    print(f'=== {label}　{title} ===')
    print(f'源文件第 {st + 1}–{en} 行（{len(src_body)} 行）　副本 {len(copy_body)} 行')

    if src_body == copy_body:
        print('\n[ok] 完全一致，没有待合并的改动。')
        return 0

    d = diff_of(src_body, copy_body)
    changed = sum(1 for l in d[3:] if l[:1] in '+-')
    print(f'差异 {changed} 行：\n')
    for l in d[2:]:
        print(l)

    if not apply_:
        print('\n（dry-run：未写任何文件。核对无误后加 --apply 合并。）')
        return 0

    os.makedirs(BACKUP, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    bak = os.path.join(BACKUP, f'笑樱_galgame台本_合并{key}前_{ts}.md')
    shutil.copy2(SRC, bak)

    out = src_lines[:st] + copy_body + src_lines[en:]
    with open(SRC, 'w', encoding='utf-8', newline='') as f:
        f.write(nl.join(out) + nl)

    # 同步基线：合并后副本正文 == 源文件该块正文，
    # 下次 split 应把它当「源文件改动」正常刷新，而不是误判成手改。
    bl_path = os.path.join(OUT, '.baseline.json')
    try:
        with open(bl_path, encoding='utf-8') as f:
            bl = json.load(f)
        bl = bl if isinstance(bl, dict) else {}
    except Exception:
        bl = {}
    bl[os.path.basename(path)] = hashlib.sha1('\n'.join(copy_body).encode('utf-8')).hexdigest()
    with open(bl_path, 'w', encoding='utf-8') as f:
        json.dump(bl, f, ensure_ascii=False, indent=1, sort_keys=True)

    print()
    print(f'[ok] 已合并：{label} {len(src_body)} → {len(copy_body)} 行')
    print(f'     源文件 {len(src_lines)} → {len(out)} 行 ｜ 备份：{os.path.relpath(bak, ROOT)}')
    print('     下一步：`python tools/split-script.py` 刷新其余副本，再编译校验。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
