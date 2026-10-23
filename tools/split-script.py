#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
笑樱 · 台本分章（把单一作者源切成可独立投喂的分章副本）

为什么：
  `笑樱_galgame台本.md` 是唯一作者源（5,471 行 / 145 KB）。把整份塞给外部 AI 会浪费上下文，
  而 8 章之间互相独立度高 —— 按章拆开后，一次只需投喂「规范区 + 目标章」。

铁律：
  ⛔ 本脚本的输出是**只读派生副本**，不是第二份剧本。
     任何内容改动都必须改 `笑樱_galgame台本.md`，然后重跑本脚本。
  ✅ 幂等：重复运行结果一致（每次整体重写）。
  ✅ 自校验：所有切片行数之和必须等于源文件行数，否则报错退出（防丢内容）。

护栏（2026-09-16 加）：
  ★ 写入前逐文件比对 —— 若某块副本的内容与「由源文件重新生成的结果」不一致，
    说明你在副本上直接改过。脚本会**跳过该块并警告**，而不是把你的改动覆盖掉。
    正确处理：`python tools/merge-chapter.py <章号> --apply` 合并回源文件，再重跑本脚本。

用法：
  python tools/split-script.py            # 生成/更新 台本分章/（带护栏）
  python tools/split-script.py --check    # 只校验，不写文件
  python tools/split-script.py --force    # 无视护栏，强制覆盖（★ 会丢副本上的改动）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, '笑樱_galgame台本.md')
OUT = os.path.join(ROOT, '台本分章')
# 基线＝上次同步时各块副本正文的指纹。护栏靠它区分两种「不一致」：
#   副本指纹 == 基线  → 副本没被动过，只是源文件改了 → **该覆盖**（正常刷新）
#   副本指纹 != 基线  → 副本被手改过                → **跳过我，别覆盖**
BASELINE = os.path.join(OUT, '.baseline.json')

RE_CH = re.compile(r'^# 第 (\d+) 章[　\s]*(.+?)\s*$')
RE_SCENE = re.compile(r'^`■ SCENE')
RE_SAY = re.compile(r'^【')

BANNER = """> ⚠️ **派生副本** —— 由 `tools/split-script.py` 从 `笑樱_galgame台本.md` 自动生成。
> **唯一作者源是 `笑樱_galgame台本.md`**：在本文件上的手改**不会自动回流**。
> 改完请跑 `python tools/merge-chapter.py <章号> --apply` 把改动合并回源文件，
> 再跑 `python tools/split-script.py` 刷新（同步时会**自动跳过仍有手改的块**，不会覆盖你）。

"""


def safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|　\s]+', '_', name).strip('_')


def body_of(content: str) -> list[str]:
    """剥掉自动生成的头部（banner + 范围行 + `---`），只留正文行。

    护栏必须**只比正文**：范围行里的行号会随源文件增删而变，
    那是派生信息，不是手改 —— 拿整份文件比会大面积误报。
    """
    ls = content.splitlines()
    i, n = 0, len(ls)
    while i < n and (ls[i].startswith('> ') or ls[i].strip() == ''):
        i += 1
    if i < n and ls[i].strip() == '---':
        i += 1
        if i < n and ls[i].strip() == '':
            i += 1
    return ls[i:]


def body_hash(lines: list[str]) -> str:
    return hashlib.sha1('\n'.join(lines).encode('utf-8')).hexdigest()


def load_baseline() -> dict:
    try:
        with open(BASELINE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_baseline(d: dict) -> None:
    with open(BASELINE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)


def main() -> int:
    check_only = '--check' in sys.argv
    force = '--force' in sys.argv

    if not os.path.exists(SRC):
        print(f'[x] 找不到源文件：{SRC}')
        return 2

    with open(SRC, encoding='utf-8') as f:
        lines = f.read().splitlines(keepends=True)
    total = len(lines)

    ch = [(i, int(m.group(1)), m.group(2)) for i, l in enumerate(lines)
          for m in [RE_CH.match(l)] if m]
    tail = next((i for i, l in enumerate(lines) if l.startswith('# 樱线・全篇完')), None)

    if not ch:
        print('[x] 源文件里没找到任何「# 第 N 章」标题')
        return 2

    # ---- 切片 ----
    pieces: list[tuple[str, str, str, int, int, list[str]]] = []
    pieces.append(('00_规范区.md', '规范区', '记号约定 · 叙述视角 · 立绘对照 · 程序行为总表',
                   0, ch[0][0], lines[0:ch[0][0]]))
    for k, (st, no, title) in enumerate(ch):
        en = ch[k + 1][0] if k + 1 < len(ch) else (tail if tail is not None else total)
        fname = f'{no:02d}_第{no}章_{safe(title)}.md'
        pieces.append((fname, f'第 {no} 章', title, st, en, lines[st:en]))
    if tail is not None:
        pieces.append(('99_附录.md', '附录', '全篇完 · 演出备注（开发用）',
                       tail, total, lines[tail:total]))

    # ---- 自校验：切片必须无损覆盖源文件 ----
    covered = sum(p[4] - p[3] for p in pieces)
    ok = (covered == total) and all(
        pieces[i][4] == pieces[i + 1][3] for i in range(len(pieces) - 1))
    print(f'源 {total} 行 ｜ 切片覆盖 {covered} 行 ｜ 衔接{"连续" if ok else "有断裂"}')
    if not ok:
        print('[x] 切片未能无损覆盖源文件，终止（不写任何文件）')
        return 1

    # ---- 统计 ----
    rows = []
    for fname, label, title, st, en, body in pieces:
        text = ''.join(body)
        rows.append(dict(fname=fname, label=label, title=title, st=st + 1, en=en,
                         lines=en - st, scenes=sum(1 for l in body if RE_SCENE.match(l)),
                         says=sum(1 for l in body if RE_SAY.match(l))))

    if check_only:
        print('\n（--check：只校验，未写文件）')
        for r in rows:
            print(f'  {r["fname"]:<34} {r["lines"]:>5} 行  {r["scenes"]:>3} 场  {r["says"]:>4} 屏')
        return 0

    # ---- 写文件（基线护栏：副本被手改则跳过，源文件改动则正常刷新）----
    os.makedirs(OUT, exist_ok=True)
    base = load_baseline()
    new_base = dict(base)
    skipped: list[str] = []
    for fname, label, title, st, en, body in pieces:
        content = (BANNER
                   + f'> 范围：{label}　{title}　｜　源文件第 {st + 1}–{en} 行\n\n---\n\n'
                   + ''.join(body))
        path = os.path.join(OUT, fname)
        body_new = ''.join(body).splitlines()
        if not force and os.path.exists(path):
            with open(path, encoding='utf-8', newline='') as f:
                old_body = body_of(f.read())
            recorded = base.get(fname)
            if recorded is None:
                # 没有基线（首次运行 / 基线丢失）→ 退回保守判断：
                # 副本正文与「源文件重新生成的结果」不一致就跳过
                if old_body != body_new:
                    skipped.append(fname)
                    continue
            elif body_hash(old_body) != recorded:
                # ★ 副本相对基线被动过 = 手改 → 保护，不覆盖
                skipped.append(fname)
                continue
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
        new_base[fname] = body_hash(body_new)
    save_baseline(new_base)

    # ---- README ----
    tbl = '\n'.join(
        f'| `{r["fname"]}` | {r["label"]} | {r["title"]} | {r["lines"]} | {r["scenes"]} | {r["says"]} |'
        for r in rows)
    readme = f"""# 台本分章（派生副本 · 可安全编辑）

> ⚠️ 这里的文件都是自动生成的**派生副本**。**唯一作者源是根目录的 `笑樱_galgame台本.md`。**
> 但你**可以直接在这里改** —— 改完跑一次合并命令把改动送回去就行（见下）。

## 为什么要拆

源文件 5,471 行 / 145 KB。把它整份投喂给外部 AI 会浪费上下文，
而 8 章之间独立度高 —— 按章拆开后，一次只投喂需要的部分。

## 怎么用

给外部 AI 处理某一章时，投喂**两份**即可：

```
台本分章/00_规范区.md          ← 记号约定 + 程序行为总表（每章都要）
台本分章/0N_第N章_xxx.md       ← 目标章
```

规范区是独立一份（不复制进每个章文件），因为它约 100 行且内容不变 —— 复制 8 份纯粹浪费。

## 文件清单

| 文件 | 内容 | 标题 | 行数 | 场数 | 屏数 |
|---|---|---|---|---|---|
{tbl}

> 「屏数」＝ `【…】` 标签数（＝一屏一次点击）。统计口径为**原始行**，未做去重。

## 改内容的工作流（★ 推荐：改小文件，不怕覆盖）

源文件有 5900+ 行，直接在里面找一段很累。所以**改成在章文件里改**：

```bash
# 1. 直接在 台本分章/0N_第N章_*.md 里编辑（文件小，好定位）
# 2. 把这一章的改动合并回唯一作者源（先 dry-run 看 diff）
python tools/merge-chapter.py 2              # 只看差异，不写
python tools/merge-chapter.py 2 --apply      # 确认后合并（自动备份源文件）
# 3. 刷新其余副本
python tools/split-script.py
```

**护栏**：`split-script.py` 同步时会逐块比对正文，**若某块副本有未合并的手改，就跳过该块并警告**，
而不是把你的改动覆盖掉。所以最坏情况也只是"这次没刷新"，**改动不会丢**。

```bash
python tools/merge-chapter.py --list         # 体检：哪些块与源文件不一致
python tools/split-script.py --check         # 只校验切片无损，不写
python tools/split-script.py --force         # ★ 无视护栏强制覆盖（会丢副本上的改动）
```

脚本每次重写整份文件，**幂等**；写之前先做无损校验：
所有切片的行数之和必须等于源文件行数，且切片首尾必须衔接 —— 校验不过就直接终止、不写任何文件。

---
*由 `tools/split-script.py` 生成 ｜ 源文件：`笑樱_galgame台本.md`*
"""
    with open(os.path.join(OUT, 'README.md'), 'w', encoding='utf-8', newline='') as f:
        f.write(readme)

    print(f'\n已刷新 {len(pieces) + 1 - len(skipped)} 个文件 → {OUT}')
    for r in rows:
        mark = '  [跳过·有手改]' if r['fname'] in skipped else ''
        print(f'  {r["fname"]:<34} {r["lines"]:>5} 行  {r["scenes"]:>3} 场  {r["says"]:>4} 屏{mark}')

    if skipped:
        print(f'\n[!] {len(skipped)} 块副本有**未合并的手改**，已跳过覆盖（防止丢改动）：')
        for f in skipped:
            print(f'      {f}')
        print('    正确处理：先把它合并回源文件，再重跑本脚本 ——')
        print('      python tools/merge-chapter.py <章号>          # 看差异')
        print('      python tools/merge-chapter.py <章号> --apply  # 合并回源文件')
        print('    （若确认要丢弃副本上的改动，用 --force 强制覆盖。）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
