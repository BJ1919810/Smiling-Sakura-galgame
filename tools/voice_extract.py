# -*- coding: utf-8 -*-
"""抽取《笑樱》台本中指定角色的台词 + 上下文，供情绪标注使用。

台本结构（见 笑樱_galgame台本.md 记号约定）：
    ■ SCENE：地点｜时间｜天气
    【我】 / 【我】（心声）   → 主角第一人称叙述
    【樱】                   → 角色台词
    （……）                   → 紧跟标签后＝演出提示（不显示）；孤立成行＝旁白

⚠️ 坑：`【樱】` **单独成一行**，台词在紧接着的下一行。所以不能只认
   `【樱】xxx` 这种内联写法 —— 会在解析阶段就把台词丢掉（实测 0 条）。

用法：
    python tools/voice_extract.py                        # 抽 樱，全部
    python tools/voice_extract.py --speaker 龙司
    python tools/voice_extract.py --limit 40             # 只看前 40 条
    python tools/voice_extract.py --out 路径.md
"""
from __future__ import annotations

import argparse
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(ROOT, "笑樱_galgame台本.md")

SCENE_RE = re.compile(r"^■\s*SCENE\s*[:：]?\s*(.+)$")
TAG_RE = re.compile(r"^【([^】]{1,12})】\s*(.*)$")
PAREN_RE = re.compile(r"^（(.*)）$")
META_PREFIX = ("#", ">", "|", "-", "*", "◆", "▲", "▸", "〔", "%%", "■", "※")


def build_units(lines: list[str]) -> list[dict]:
    """把台本切成「发言单元」：一个标签 + 它吸走的后续括注/正文行。"""
    units: list[dict] = []
    scene = ""
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        m = SCENE_RE.match(s)
        if m:
            scene = m.group(1).strip()
            continue
        if s.startswith(META_PREFIX):
            continue  # 资源行 / 运镜 / 标题 / 表格

        m = TAG_RE.match(s)
        if m:
            tag, rest = m.group(1), m.group(2).strip()
            units.append({
                "who": tag,
                "line": i + 1,
                "scene": scene,
                "paren": [],
                "text": rest,
            })
            continue

        if not units:
            continue
        u = units[-1]
        p = PAREN_RE.match(s)
        if p:
            u["paren"].append(p.group(1).strip())
        elif not u["text"]:
            u["text"] = s          # 只在尚无正文时吸收（否则是孤立旁白/异常行）
        # 已有正文的裸行 → 视为旁白，丢弃（避免污染台词）
    return units


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--speaker", default="樱")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--before", type=int, default=3, help="台词前保留的上下文单元数")
    ap.add_argument("--after", type=int, default=1, help="台词后保留的上下文单元数")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    with open(SCRIPT, encoding="utf-8") as f:
        lines = f.read().split("\n")
    units = build_units(lines)

    hits = [i for i, u in enumerate(units) if u["who"] == args.speaker]
    if args.limit:
        hits = hits[: args.limit]

    out: list[str] = [f"# {args.speaker} 台词上下文抽取（共 {len(hits)} 条）", ""]
    for n, i in enumerate(hits, 1):
        u = units[i]
        out.append(f"## #{n}　台本行 {u['line']}　场景：{u['scene']}")
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

    path = args.out or os.path.join(ROOT, ".workbuddy", "tmp", f"台词上下文_{args.speaker}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))

    # 同时落一份纯台词清单（无上下文），便于批量标注
    pure = [f"{u['line']}\t{('（'+'）'.join(u['paren'])+'）' if u['paren'] else '')}{u['text']}"
            for u in (units[i] for i in hits)]
    ppath = os.path.join(os.path.dirname(path), f"台词清单_{args.speaker}.txt")
    with open(ppath, "w", encoding="utf-8") as f:
        f.write("\n".join(pure))

    total = len([u for u in units if u["who"] == args.speaker])
    print(f"[ok] 命中 {len(hits)} / 全篇 {total} 条")
    print(f"     上下文 -> {path}")
    print(f"     纯清单 -> {ppath}")


if __name__ == "__main__":
    main()
