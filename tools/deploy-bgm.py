# -*- coding: utf-8 -*-
"""
BGM 部署脚本：素材基准/音频/BGM/*  →  public/assets/audio/bgm/<manifest 编号>.ogg

★ 为什么需要这一步
  引擎的音频路径由 `src/generated/assets.manifest.json` **锁定**：
      key（曲名，如「樱_主题（完整）」）→ file（编号名，如 `bgm/bgm_013.ogg`）
  —— key 来自台本、编号来自编译；素材却是按**可读名**归档的（`bgm_樱_主题_完整.ogg`）。
  本脚本做「可读名 ↔ manifest key」的自动对号，免得靠人记编号。

★ 匹配规则（归一化）
  把两侧的 `_` `・` `（` `）` `(` `)` 空格都当成同一个分隔符再比较，于是：
      bgm_樱_主题_完整.ogg   ↔   樱_主题（完整）   → bgm_013.ogg
      bgm_静_樱.ogg          ↔   静・樱           → bgm_002.ogg

★ 格式约定
  **引擎侧统一 ogg/vorbis**：Chromium 的 <audio> 原生支持，且 ogg 循环点**无缝**
  （mp3 有容器填充，循环处会多出零点几秒静默）。源若是 wav/mp3 会自动转码：
      ffmpeg -c:a libvorbis -q:a 6 -ar 44100

★ 素材目录的元文件
  `_` 开头的文件/目录一律跳过（`_参考_*.wav`、`_派生/`、`_旧ogg归档/`）。
  同一曲名同时有 `.wav` 与 `.ogg` 时，**wav 母带优先**（ogg 只是它的转码产物）。

用法：
  python tools/deploy-bgm.py            # 部署（复制/覆盖，幂等）
  python tools/deploy-bgm.py --dry-run  # 只打印计划，不写文件
  python tools/deploy-bgm.py --check    # 只报告缺口（素材缺 / 名对不上），有缺口则退出码 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "素材基准" / "音频" / "BGM"
DST = ROOT / "public" / "assets" / "audio" / "bgm"
MANIFEST = ROOT / "src" / "generated" / "assets.manifest.json"

AUDIO_EXT = {".ogg", ".mp3", ".wav", ".m4a", ".flac"}
SEP = re.compile(r"[_・·（）()\s　]")
PREFIX = "bgm_"


def find_ffmpeg() -> str | None:
    for cand in (os.environ.get("FFMPEG"), shutil.which("ffmpeg"),
                 r"D:\ffmpeg\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


def norm(name: str) -> str:
    """归一化曲名：全角→半角，再把各种分隔符统一成 `·`。"""
    return SEP.sub("·", unicodedata.normalize("NFKC", name)).strip("·")


def to_ogg(src: Path, dst: Path) -> None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print(f"  ✗ 需要转码 {src.name}，但找不到 ffmpeg（设 FFMPEG 环境变量）", file=sys.stderr)
        raise SystemExit(2)
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-c:a", "libvorbis", "-q:a", "6", "-ar", "44100", str(dst)],
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="把 BGM 素材部署进引擎（按 manifest 编号）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--check", action="store_true", help="只报告缺口，有缺口则退出码 1")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"✗ 找不到 manifest：{MANIFEST}（先跑 npm run manifest）", file=sys.stderr)
        return 2
    bgm = json.loads(MANIFEST.read_text(encoding="utf-8")).get("bgm", {})

    want: dict[str, tuple[str, str, bool]] = {}       # norm(key) → (原 key, 目标文件名, loop)
    for key, ent in bgm.items():
        want[norm(key)] = (key, Path(ent["file"]).name, bool(ent.get("loop", True)))

    have: dict[str, Path] = {}
    for p in sorted(SRC.glob("*")) if SRC.exists() else []:
        # `_` 开头＝元文件（_参考_*、_旧ogg归档/、_派生/），不是待部署曲目
        if p.name.startswith("_") or p.suffix.lower() not in AUDIO_EXT:
            continue
        # 同一曲名同时存在多种格式时，**母带 wav 优先于派生 ogg**
        key = norm(p.stem[len(PREFIX):] if p.stem.startswith(PREFIX) else p.stem)
        if key not in have or p.suffix.lower() == ".wav":
            have[key] = p

    matched = [(have[k], *want[k]) for k in sorted(set(want) & set(have))]
    missing = [want[k][0] for k in sorted(set(want) - set(have))]
    unknown = [have[k].name for k in sorted(set(have) - set(want))]

    print(f"素材目录：{SRC}")
    print(f"目标目录：{DST}")
    print(f"manifest：{len(want)} 首 ｜ 素材：{len(have)} 个\n")

    for src, key, fname, loop in matched:
        exists = (DST / fname).exists()
        tag = "更新" if exists else "新增"
        print(f"  [{tag}] {src.name}  →  {fname}   「{key}」{'（不循环）' if not loop else ''}")
        if args.dry_run or args.check:
            continue
        DST.mkdir(parents=True, exist_ok=True)
        dst = DST / fname
        if src.suffix.lower() == ".ogg" and src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        elif src.suffix.lower() != ".ogg":
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / (dst.stem + ".ogg")
                to_ogg(src, tmp)
                shutil.copy2(tmp, dst)

    if missing:
        print("\n  ⏳ 还没有素材（manifest 有、素材目录没有）：")
        for k in missing:
            print(f"      · {k}")
    if unknown:
        print("\n  ⚠ 素材目录里对不上 manifest 的文件（名字写错了？）：")
        for n in unknown:
            print(f"      · {n}")

    done = 0 if (args.dry_run or args.check) else len(matched)
    print(f"\n合计：匹配 {len(matched)} 首 / 未出 {len(missing)} 首 / 名不符 {len(unknown)} 个"
          + (f"，已写入 {done} 个" if done else "（未写文件）"))

    if args.check and (missing or unknown):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
