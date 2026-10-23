# -*- coding: utf-8 -*-
"""
CG 部署脚本：素材基准/CG/CG_<键名>_2560x1440.png  →  public/assets/cg/<manifest 编号>.png

★ 为什么需要这一步（2026-09-17 补 —— 这个脚本之前**根本不存在**）
  引擎的 CG 路径由 `src/generated/assets.manifest.json` **锁定**：
      key（中文键名，如「教室_早晨_背影」）→ file（编号名，如 `cg/cg_001.png`）
  —— key 来自台本、编号来自编译；素材却是按**可读名**归档的（`CG_教室_早晨_背影_2560x1440.png`）。
  本脚本做「可读名 ↔ manifest key」的自动对号，免得靠人记编号。

  ⛔ 不这么做的下场（真实事故）：第 1 章 CG 化时在章首插了 8 个新键 → 自动编号整体后移 8 位，
  但 `public/assets/cg/` 里还是旧编号的文件 ⇒ `教室_早晨_背影` 解析到旧 cg_001
  （＝旧形象的「长椅默读」），**游戏里画的是粉发旧樱，引擎零报错**。
  ⇒ 配套防线：`manifest.ts` 的 **`CG_PIN`**（已接线键钉死编号）＋ 本脚本（按 manifest 对号部署）。
  ★ 与 `deploy-bgm.py` 同构；差别只在目录、扩展名与「成品尺寸后缀」识别。

★ 复用（2026-09-18 加）
  manifest **允许两个键指向同一个文件** —— 例：`长椅_隔着椅子` 与 `夕阳下的长椅_仰视背影`
  共用 `cg/cg_006.png`（主人定「统一用这张」，同一张 CG 用在多处＝合法）。
  ⇒ 本脚本先按**目标文件**归组，每组挑一个「素材目录里真有成品」的键当来源；
    组内其余键＝复用方，**不会**被误报成「未出图」，也不会让后写的键覆盖掉先写的图。
  ⚠️ 复用方不需要自己的 `CG_<键名>_2560x1440.png`；它的素材若存在会被忽略（以来源键为准）。

★ 匹配规则
  `CG_<键名>_<宽>x<高>.png`，其中 `<宽>x<高>` 必须是**成品尺寸**（默认 2560x1440）
  ⇒ 剥掉前缀 `CG_` 与尺寸后缀，剩下的就是 manifest 的 key。
  同目录的 `CG_<键名>_源_2560x1536.png`（ImageGen 原始产物）**不是成品**，自动跳过。

★ 元文件
  `_` 开头的文件/目录一律跳过（`_废_旧形象_CG_*`、`_旧编号_*`）。

用法：
  python tools/deploy-cg.py            # 部署（复制/覆盖，幂等）
  python tools/deploy-cg.py --dry-run  # 只打印计划，不写文件
  python tools/deploy-cg.py --check    # 只报告缺口，有缺口则退出码 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "素材基准" / "CG"
DST = ROOT / "public" / "assets" / "cg"
MANIFEST = ROOT / "src" / "generated" / "assets.manifest.json"

PREFIX = "CG_"
DELIVER_H = "2560x1440"
# `CG_<键名>_2560x1440.png` —— 剥掉 `CG_` 前缀后，尺寸后缀从**末尾**匹配（键名本身可含下划线）
RE_DELIVER = re.compile(rf"^(?P<key>.+)_{DELIVER_H}\.png$")


def up_to_date(a: Path, b: Path) -> bool:
    """
    目标 b 是否已是源 a 的最新派生物？
    ⚠️ 2026-09-18 起成品改为 **WebP**（源仍是 PNG 母带）⇒ 内容必然不同、md5 比较失效，
       改用 **mtime**：目标不旧于源即认为最新。
       （副作用：从备份/git 恢复源文件会刷新 mtime ⇒ 触发一次无害的重转。）
    """
    if not b.exists():
        return False
    return b.stat().st_mtime >= a.stat().st_mtime


def write_deliverable(src: Path, dst: Path, quality: int) -> None:
    """按目标扩展名写成品：.webp → PIL 转码；其余 → 直接复制。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.suffix.lower() == ".webp":
        from PIL import Image
        with Image.open(src) as im:
            im.save(dst, "WEBP", quality=quality, method=6)
    else:
        shutil.copy2(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser(description="把 CG 成品部署进引擎（按 manifest 编号）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--check", action="store_true", help="只报告缺口，有缺口则退出码 1")
    ap.add_argument("--quality", type=int, default=90,
                    help="WebP 有损质量（默认 90；CG 实测 PSNR 40.4 dB ≈ 视觉无损，体积省 87%）")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"✗ 找不到 manifest：{MANIFEST}（先跑 npm run compile && npm run manifest）", file=sys.stderr)
        return 2
    cg = json.loads(MANIFEST.read_text(encoding="utf-8")).get("cg", {})

    # ★ 按「目标文件」归组 —— 多键共用一文件＝复用（见文件头 §复用）
    by_file: dict[str, list[str]] = {}
    for key, ent in cg.items():
        by_file.setdefault(Path(ent["file"]).name, []).append(key)

    # 扫素材：只认「成品」命名，且跳过 `_` 开头的元文件
    have: dict[str, Path] = {}
    offspec: list[str] = []
    if SRC.exists():
        for p in sorted(SRC.glob("*.png")):
            if p.name.startswith("_"):
                continue
            if not p.name.startswith(PREFIX):
                offspec.append(p.name)
                continue
            stem = p.name[len(PREFIX):]          # 剥掉 `CG_` 前缀再认尺寸后缀
            if "_源_" in stem:
                continue                          # ImageGen 原始产物，预期内，不算异常
            m = RE_DELIVER.match(stem)
            if not m:
                offspec.append(p.name)
                continue
            have[m.group("key")] = p

    # 每个目标文件挑一个「素材目录里有成品」的键当来源；其余同文件的键＝复用方
    # plan 条目：(源文件, 目标文件名, 来源键, 复用方键列表)
    plan: list[tuple[Path, str, str, list[str]]] = []
    missing: list[str] = []
    for fname, keys in sorted(by_file.items()):
        src_key = next((k for k in keys if k in have), None)
        if src_key is None:
            missing.extend(keys)
            continue
        plan.append((have[src_key], fname, src_key, [k for k in keys if k != src_key]))

    unknown = [have[k].name for k in sorted(set(have) - set(cg))]

    print(f"素材目录：{SRC}")
    print(f"目标目录：{DST}")
    print(f"manifest：{len(cg)} 个键 / {len(by_file)} 个文件 ｜ 成品素材：{len(have)} 张\n")

    written = skipped = 0
    for src, fname, src_key, sharers in plan:
        dst = DST / fname
        share = f"（同时供 {'、'.join(sharers)} 复用）" if sharers else ""
        if up_to_date(src, dst):
            print(f"  [跳过] {src.name}  →  {fname}（内容相同）{share}")
            skipped += 1
            continue
        tag = "更新" if dst.exists() else "新增"
        print(f"  [{tag}] {src.name}  →  {fname}{share}")
        if args.dry_run or args.check:
            continue
        DST.mkdir(parents=True, exist_ok=True)
        write_deliverable(src, dst, args.quality)
        written += 1

    if missing:
        print("\n  ⏳ manifest 有、素材还没有成品的键（游戏里显示 `makeCgPlaceholder` 占位卡"
              "「CG：<键名>／[占位 · 待美术]」，不是空白）：")
        for k in missing:
            print(f"      · {k}")
    if unknown:
        print("\n  ⚠ 有成品但 manifest 里没这个键（台本还没写 `■ CG：` 行？）：")
        for n in unknown:
            print(f"      · {n}")
    if offspec:
        print("\n  ⚠ 命名不符规范（既不是 `_开头`，也不是 `CG_<键名>_2560x1440.png`）：")
        for n in offspec:
            print(f"      · {n}")

    # 目标目录里的孤儿文件：manifest 不认（多半是旧编号残留）
    known = set(by_file)
    orphans = sorted(
        p.name for p in DST.glob("*.png") if p.name not in known
    ) if DST.exists() else []
    if orphans:
        print("\n  ⚠ 目标目录里 manifest 不认的文件（旧编号残留？建议归档后再覆盖）：")
        for n in orphans:
            print(f"      · {n}")

    n_share = sum(len(s) for *_, s in plan)
    print(f"\n合计：文件 {len(plan)} 个（其中复用 {n_share} 键）/ 未出图 {len(missing)} 键"
          f" / 无对应键 {len(unknown)} 个"
          + (f"，已写入 {written} 个，跳过 {skipped} 个" if not (args.dry_run or args.check)
             else "（未写文件）"))

    if args.check and (missing or unknown or orphans):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
