# -*- coding: utf-8 -*-
"""
把「原先由引擎自动触发」的演出，一比一转录成台本里的**显式指令**（2026-09-15）。

起因：主人令「程序死跟台本走，没有任何隐藏的小巧思」。
      引擎侧的自动触发已删除（src/director/Director.ts）：
        · 立绘括注命中情绪词表 → 自动雀跃      （已删）
        · 每个 ■ CG 行          → 自动白闪      （已删）
        · SCENE 地点含「樱」    → 花瓣密度加倍  （已删）
      若不转录，台本演出会**静默改变**。本脚本负责把旧行为原样搬到台面上。

⚠️ 只做**插入**，绝不修改任何既有行 —— 转录后演出结果与改造前逐帧一致。

转录三类：
  ① 雀跃   ← 立绘括注命中 emotion.ts 词表 → 该行**后**插 `※ 雀跃：<角色>`
  ② 白闪   ← 每个 ■ CG 行                 → 该行**前**插 `※ 白闪`
  ③ 花瓣密 ← SCENE 地点含「樱」           → 该行**后**插 `※ 花瓣密`

用法：python tools/transcribe-implicit-fx.py [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "笑樱_galgame台本.md"
BACKUP_DIR = ROOT / ".workbuddy" / "backup"

# ── 与 src/engine/text/emotion.ts 同源（改造前驱动雀跃的词表）────────────
POSITIVE = re.compile(r"亮|睁大|瞪大|眼睛.{0,4}弯|弯.{0,4}眼|意外|惊|嬉|雀跃|蹦|兴奋|激动|欢呼|惊喜")
NEGATIVE = re.compile(r"怒|生气|恼|沉下|阴沉|冷冷|板起|咬牙|皱眉|不耐|压低|铁青|瞪眼")

RE_SPRITE = re.compile(r"^`■ 立绘：([^（]+)（([^）]*)）")
RE_CG = re.compile(r"^`■ CG：")
RE_SCENE = re.compile(r"^`■ SCENE[^｜]*｜([^｜]*)｜")
RE_STAR = re.compile(r"^`※")


def main() -> int:
    ap = argparse.ArgumentParser(description="转录隐式演出为显式 ※ 指令")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    args = ap.parse_args()

    text = SRC.read_text(encoding="utf-8")
    lines = text.split("\n")

    inserts: dict[int, list[str]] = {}   # 行号 → 要插入的行（在该行**前**）
    hits = {"雀跃": [], "白闪": [], "花瓣密": []}

    for i, L in enumerate(lines):
        # 已经显式写过的跳过（幂等：重复跑不会叠加）
        prev = lines[i - 1] if i > 0 else ""
        nxt = lines[i + 1] if i + 1 < len(lines) else ""

        m = RE_SPRITE.match(L)
        if m:
            char, pose = m.group(1), m.group(2)
            if POSITIVE.search(pose) and not NEGATIVE.search(pose):
                if "雀跃" not in nxt:
                    inserts.setdefault(i + 1, []).append(f"`※ 雀跃：{char}`")
                    hits["雀跃"].append((i + 1, char, pose))
            continue

        if RE_CG.match(L):
            if "白闪" not in prev:
                inserts.setdefault(i, []).append("`※ 白闪`")
                hits["白闪"].append((i + 1, L.strip("`")))
            continue

        ms = RE_SCENE.match(L)
        if ms and "樱" in ms.group(1):
            if "花瓣密" not in nxt:
                inserts.setdefault(i + 1, []).append("`※ 花瓣密`")
                hits["花瓣密"].append((i + 1, ms.group(1)))
            continue

    # 从后往前插，避免行号偏移
    out = list(lines)
    for at in sorted(inserts, reverse=True):
        out[at:at] = inserts[at]

    print(f"台本：{SRC.name}（{len(lines)} 行）")
    print(f"备份：{BACKUP_DIR}")
    print()
    for k, label in (("雀跃", "① 雀跃   ← 原「括注命中情绪词表自动跳」"),):
        print(f"{label}：{len(hits[k])} 处")
        for ln, c, p in hits[k]:
            print(f"     L{ln:<5d} {c}（{p}）")
    print(f"② 白闪   ← 原「每个 ■ CG 自动白闪」：{len(hits['白闪'])} 处")
    for ln, name in hits["白闪"]:
        print(f"     L{ln:<5d} {name}")
    print(f"③ 花瓣密 ← 原「SCENE 含樱自动加倍」：{len(hits['花瓣密'])} 处")
    for ln, place in hits["花瓣密"]:
        print(f"     L{ln:<5d} {place}")
    total = sum(len(v) for v in hits.values())
    print(f"\n合计插入 {total} 行 / 共 {len(inserts)} 个位置")

    if args.dry_run:
        print("\n（--dry-run，未写文件）")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = BACKUP_DIR / f"笑樱_galgame台本_转录前_{stamp}.md"
    shutil.copy2(SRC, backup)
    SRC.write_text("\n".join(out), encoding="utf-8")
    print(f"\n✓ 已备份原稿 → {backup.name}")
    print(f"✓ 已写回 {SRC.name}（{len(out)} 行）")
    print("\n⚠️ 下一步：npm run compile 重编 script.json，并检查 fx 数量是否与转录数吻合。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
