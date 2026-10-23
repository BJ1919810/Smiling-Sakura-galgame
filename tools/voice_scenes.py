# -*- coding: utf-8 -*-
"""按「章节 + 场景」给角色台词分组，输出上下文清单，供批量翻译/标注使用。

★ 为什么不用 voice_extract.build_units 的 scene 字段
  它的 META_PREFIX 里含 `■`，SCENE_RE 判断在 META_PREFIX 之后 ⇒ 场景永远抓不到（实测 261 条全归「未标场景」）。
  这里直接从原始行扫 `■ SCENE`，自己维护场景游标。

章节边界：`## 第 N 章`（台本用二级标题分章）。

用法：
    python tools/voice_scenes.py --speaker 樱
    python tools/voice_scenes.py --speaker 樱 --chapter 3
    python tools/voice_scenes.py --speaker 樱 --out 路径.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "笑樱_galgame台本.md")

SCENE_RE = re.compile(r"^■\s*SCENE\s*([0-9]{0,3})\s*[｜|:：]?\s*(.*)$")
# ⚠️ 章节是**一级标题**（`# 第 1 章`），不是二三级 —— 实测踩过
CHAP_RE = re.compile(r"^#\s+第\s*([0-9一二三四五六七八九十]+)\s*章")
TAG_RE = re.compile(r"^【([^】]{1,12})】\s*(.*)$")
PAREN_RE = re.compile(r"^（(.*)）$")
META_PREFIX = ("#", ">", "|", "-", "*", "◆", "▲", "▸", "〔", "%%", "※", "■")

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def parse(script_path: str) -> list[dict]:
    """返回发言单元列表，每个单元带 chapter / scene / scene_no。"""
    lines = open(script_path, encoding="utf-8").read().split("\n")
    units: list[dict] = []
    chap, chap_title = 0, ""
    scene, scene_no = "", ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        # ⚠️ 场景行进台本时是**反引号包裹**的（`■ SCENE 03｜…`），
        #    strip() 后首字符是 `` ` `` ⇒ 必须先剥反引号，否则场景全空（实测踩过）
        s_probe = s.strip("`").strip()

        m = CHAP_RE.match(s)
        if m:
            tok = m.group(1)
            chap = CN_NUM.get(tok, 0)
            if not chap:
                try:
                    chap = int(tok)
                except ValueError:
                    chap = 0
            chap_title = s.lstrip("# ").strip()
            continue

        m = SCENE_RE.match(s_probe)
        if m:
            scene_no = m.group(1).strip()
            scene = m.group(2).strip()
            continue

        if s.startswith(META_PREFIX):
            continue

        m = TAG_RE.match(s)
        if m:
            units.append({
                "who": m.group(1), "rest": m.group(2).strip(),
                "line": i + 1, "chapter": chap, "chapter_title": chap_title,
                "scene": scene, "scene_no": scene_no, "paren": [], "text": "",
            })
            continue

        if not units:
            continue
        u = units[-1]
        p = PAREN_RE.match(s)
        if p:
            u["paren"].append(p.group(1).strip())
        elif not u["text"]:
            u["text"] = s
    return units


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", default="樱")
    ap.add_argument("--chapter", type=int, default=0)
    ap.add_argument("--before", type=int, default=2)
    ap.add_argument("--after", type=int, default=1)
    ap.add_argument("--out", default="")
    ap.add_argument("--include-empty", action="store_true",
                    help="包含「无台词、只有括注动作」的条目（默认排除 —— 它们不该配音）")
    args = ap.parse_args()

    units = parse(SCRIPT)
    # ⚠️ 台本里有「【樱】/（低头看）」这种**只有动作、没有台词**的条目（樱 5 条）。
    #    它们不该配音 —— 强行合成会让 TTS 去念「低头看」。默认过滤。
    empties = [(u["line"], u["paren"]) for u in units if u["who"] == args.speaker and not u["text"].strip()]
    hits = [i for i, u in enumerate(units)
            if u["who"] == args.speaker
            and (not args.chapter or u["chapter"] == args.chapter)
            and (args.include_empty or u["text"].strip())]
    if empties:
        print(f"⚠️ 已排除 {len(empties)} 条「纯动作、无台词」条目（不该配音）：")
        for ln, p in empties:
            print(f"     行{ln}: （{'）'.join(p)}）")
        print()

    print(f"=== {args.speaker} 台词按章/场分布 ===")
    cur = None
    for i in hits:
        u = units[i]
        key = (u["chapter"], u["scene_no"], u["scene"])
        if key != cur:
            cur = key
            print(f"  第{u['chapter']}章 SCENE {u['scene_no'] or '--'}　{u['scene'][:44]}")
    print(f"\n合计 {len(hits)} 条\n")

    out = [f"# {args.speaker} 台词上下文（{len(hits)} 条）", ""]
    cur = None
    n = 0
    for i in hits:
        u = units[i]
        key = (u["chapter"], u["scene_no"], u["scene"])
        if key != cur:
            cur = key
            out.append(f"\n---\n\n## 第{u['chapter']}章｜SCENE {u['scene_no'] or '--'}｜{u['scene']}\n")
        n += 1
        out.append(f"### [{n}] 台本行 {u['line']}")
        for v in units[max(0, i - args.before):i]:
            label = "叙" if v["who"] == "我" else v["who"]
            body = ("（" + "）".join(v["paren"]) + "）" if v["paren"] else "") + v["text"]
            out.append(f"- [{label}] {body}".rstrip())
        body = ("（" + "）".join(u["paren"]) + "）" if u["paren"] else "") + u["text"]
        out.append(f"- **【{args.speaker}】{body}**")
        for v in units[i + 1:i + 1 + args.after]:
            label = "叙" if v["who"] == "我" else v["who"]
            body = ("（" + "）".join(v["paren"]) + "）" if v["paren"] else "") + v["text"]
            out.append(f"- [{label}] {body}".rstrip())
        out.append("")

    path = args.out or os.path.join(ROOT, ".workbuddy", "tmp",
                                    f"分场台词_{args.speaker}" + (f"_ch{args.chapter}" if args.chapter else "") + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"[ok] -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
