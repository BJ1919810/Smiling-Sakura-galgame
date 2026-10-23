# -*- coding: utf-8 -*-
"""
台词分镜拆分（2026-09-16）—— 按主人要求，让台本符合视觉小说的阅读节奏。

规则：
  ① **非叙事性**的、**能成两个独立句子**的，不要凑到一句去 → 一句一屏（一个 say）
  ② 叙述（【我】的地の文）**不动** —— 它本来就是成段的
  ③ ⚠️ **`▸` / `▲` 变体块内的 say 一律不拆** —— 窗口 TTL 按 say 计数，
     台本规定每个 `▸` 支线「恰好 3 条 say」，拆了会破坏窗口逻辑
  ④ 行内切分遵守：引号 「」 内不切；以 `——` 结尾的片段与下一片段合并（话没说完）
     `……` 不作切点（表示停顿，不是句末）

用法：python tools/split-beats.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "笑樱_galgame台本.md"
BACKUP_DIR = ROOT / ".workbuddy" / "backup"

RE_HEAD = re.compile(r"^【([^】]+)】(.*)$")
RE_STRUCT = re.compile(r"^(`|#|\||>|〔|▸|▲|%%|◆|-)")
# 只拆台词；「我」是叙述（地の文），「我（心声）」同属叙事性 → 都不动
NARRATIVE = {"我"}
END_PUNCT = "。？！"


def is_bracket(s: str) -> bool:
    return s.startswith("（") and s.endswith("）")


def split_sentences(line: str) -> list[str]:
    """把一行切成独立句。引号内不切；`——` 结尾与下句合并。"""
    out: list[str] = []
    buf = ""
    depth = 0
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        buf += ch
        if ch in "「『":
            depth += 1
        elif ch in "」』":
            depth = max(0, depth - 1)
        elif ch in END_PUNCT and depth == 0:
            # 吞掉紧随其后的连续标点（如「！？」「？！」）
            while i + 1 < n and line[i + 1] in END_PUNCT + "…":
                i += 1
                buf += line[i]
            out.append(buf)
            buf = ""
        i += 1
    if buf:
        out.append(buf)

    merged: list[str] = []
    for p in out:
        if merged and merged[-1].rstrip().endswith("——"):
            merged[-1] += p
        else:
            merged.append(p)
    return merged


def main() -> int:
    ap = argparse.ArgumentParser(description="台词分镜拆分")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lines = SRC.read_text(encoding="utf-8").split("\n")

    # ── 标出「逻辑块」的行范围（块内禁拆）────────────────────
    # 两类块：
    #   ① 窗口块：〔W-xx …〕 → 〔/W-xx〕（内含 ▸A/▸B/▸C 分支）—— TTL 按 say 计数，
    #      各分支必须恰好 3 条 say，拆了会破坏窗口逻辑
    #   ② 条件块：▲ … → ▸base …（条件版 / 基准版）
    # ⚠️ 块内 say 之间**有空行**，所以不能「遇空行即结束」（初版就栽在这）
    locked: set[int] = set()
    in_logic = False
    for i, L in enumerate(lines):
        s = L.strip()
        if s.startswith("〔/W-"):
            in_logic = False
            locked.add(i)
            continue
        if s.startswith("▲") or s.startswith("▸") or s.startswith("〔W-"):
            in_logic = True
            locked.add(i)
            continue
        if in_logic and (s.startswith("`") or s.startswith("#") or s.startswith("◆")):
            in_logic = False          # 撞上新的结构行 → 块结束
            continue
        if in_logic:
            locked.add(i)

    # ── 扫描 say 段 ──────────────────────────────────────────
    out: list[str] = []
    changed: list[tuple[str, list[str], int]] = []   # (角色, 新句列表, 行号)
    skipped_locked: list[int] = []
    i = 0
    total = len(lines)

    while i < total:
        s = lines[i].strip()
        m = RE_HEAD.match(s) if s else None
        if not m:
            out.append(lines[i])
            i += 1
            continue

        who = m.group(1)
        head_line = lines[i]
        start_no = i + 1
        body: list[str] = []
        if m.group(2).strip():
            body.append(m.group(2).strip())

        j = i + 1
        while j < total:
            t = lines[j].strip()
            # ★ 遇到下一个【标签】必须停 —— 否则会把下一个 say 的标签吞进自己的正文
            #   （台本里有「【樱】正文」紧跟「【我】正文」而无空行的写法，初版就栽在这）
            if t == "" or t.startswith("【") or RE_STRUCT.match(t):
                break
            body.append(t)
            j += 1

        # ★ 括注归属：编译器规则「括注行开启一个新 block，作为该 block 的 stage」
        #   → 括注属于**它后面紧跟的那句**，不能统一提到段首
        groups: list[tuple[list[str], str]] = []
        pending: list[str] = []
        for b in body:
            if is_bracket(b):
                pending.append(b)
            else:
                groups.append((pending, b))
                pending = []
        if pending and groups:            # 悬空括注（段尾）→ 挂到最后一句
            groups[-1][0].extend(pending)

        if who in NARRATIVE or not groups:
            out.extend(lines[i:j])
            i = j
            continue

        # 该段是否落在 ▸ / ▲ 块内（任一行被锁即整段不拆）
        if any(k in locked for k in range(i, j)):
            skipped_locked.append(start_no)
            out.extend(lines[i:j])
            i = j
            continue

        # 逐句切分：保留「括注 → 正文」的配对关系
        items: list[tuple[list[str], str]] = []
        for notes, text in groups:
            for k, p in enumerate(split_sentences(text)):
                items.append((notes if k == 0 else [], p))
        items = [(n, p) for n, p in items if p.strip()]

        if len(items) <= 1:
            out.extend(lines[i:j])
            i = j
            continue

        block: list[str] = []
        for k, (notes, p) in enumerate(items):
            if k > 0:
                block.append("")
            block.append(head_line)
            for nt in notes:
                block.append(nt)
            block.append(p)
        out.extend(block)
        changed.append((who, [p for _, p in items], start_no))
        i = j

    print(f"台本 {total} 行")
    print(f"✓ 拆分 {len(changed)} 段（say 数增加 {sum(len(p) - 1 for _, p, _ in changed)}）")
    if skipped_locked:
        print(f"⏭ 跳过 {len(skipped_locked)} 段（落在 ▸/▲ 变体块内，禁拆）：L{skipped_locked}")
    print()
    print("=== 对照清单（前 18 段）===")
    for who, pieces, ln in changed[:18]:
        print(f"  L{ln} 【{who}】 → {len(pieces)} 屏")
        for p in pieces:
            print(f"        「{p}」")
    if len(changed) > 18:
        print(f"  …另 {len(changed) - 18} 段")

    if args.dry_run:
        print("\n（--dry-run，未写文件）")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"笑樱_galgame台本_拆屏前_{stamp}.md"
    shutil.copy2(SRC, backup)
    SRC.write_text("\n".join(out), encoding="utf-8")
    print(f"\n✓ 已备份 → {backup.name}")
    print(f"✓ 已写回：{total} → {len(out)} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
