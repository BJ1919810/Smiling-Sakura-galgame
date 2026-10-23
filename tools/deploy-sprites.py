# -*- coding: utf-8 -*-
"""
立绘部署脚本：素材基准 → public/assets/char/<stem>.png / <stem>_NN.png

★ 为什么需要这一步
  引擎按 `char/<stem>.png`（基础）与 `char/<stem>_NN.png`（表情差分）取图
  （AssetLoader.charFull / charFace），而美术产线交出的是 1440×2560 大图（含透明边距）。

★ 推导规则（2026-09-12 第二版：**不再缩放**，主人拍板改「上半身取景」）
  1. 取该角色「基础图 + 全部表情」的 **alpha 并集裁切框**——所有张共用同一框，
     保证表情切换时身体**零漂移**；
  2. **只裁切、不缩放**：原始分辨率交给引擎。立绘 1:1 渲染、脚底沉到画面外，
     只露头到腰腹——「全身缩到 1050px 塞进画面」的旧做法作废（小人站地板观感的根源）。
     高 DPI（DPR 1.25）下物理像素≈3000px，原图 2418px 内容高反而略欠采样，不能再小。
  3. 保留 RGBA。

用法：
  python tools/deploy-sprites.py            # 全量部署（幂等，直接覆盖）
  python tools/deploy-sprites.py --dry-run  # 只打印计划，不写文件
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "素材基准" / "立绘"
DST = ROOT / "public" / "assets" / "char"

# 角色 → (stem, 基础图, 表情图列表)
# 表情编号 = 引擎 face 轴（0=基础，01..NN=差分），与 pose-split / poseToFace 对齐
CHARACTERS: list[tuple[str, str, Path, list[tuple[int, Path]]]] = [
    (
        "樱",
        "sakura",
        SRC / "樱_定稿_透明_1440x2560.png",
        [(i, SRC / "樱_表情组" / f"樱_表情_{i:02d}{name}.png") for i, name in [
            (1, "眼睛微弯"), (2, "眼睛微亮"), (3, "抬眼"), (4, "微微歪头"), (5, "眼眶热（含泪不落）"),
        ]],
    ),
    (
        "龙司",
        "ryuji",
        SRC / "龙司_表情组" / "龙司_表情_00平静（基准）_透明.png",
        [(i, SRC / "龙司_表情组" / f"龙司_表情_{i:02d}{name}_透明.png") for i, name in [
            (1, "不耐"), (2, "无语"), (3, "脸沉"), (4, "极淡的松"), (5, "泛红"),
        ]],
    ),
    (
        "阳太",
        "yota",
        SRC / "阳太_表情组" / "阳太_表情_00笑（基准·不漏齿）_透明.png",
        [(i, SRC / "阳太_表情组" / f"阳太_表情_{i:02d}{name}_透明.png") for i, name in [
            (1, "嬉笑（露齿）"), (2, "尴尬"), (3, "喘"), (4, "敛笑"), (5, "瞪眼咬牙"), (6, "无语"),
        ]],
    ),
    (
        "亚里沙",
        "arisa",
        SRC / "亚里沙_定稿_透明_1440x2560.png",
        [(i, SRC / "亚里沙_表情组" / f"亚里沙_表情_{i:02d}{name}_透明_1440x2560.png") for i, name in [
            (1, "偷笑"), (2, "无语"), (3, "认真"), (4, "无奈"),
        ]],
    ),
    (
        "九条院",
        "kujo",
        SRC / "九条院_定稿_透明_1440x2560.png",
        [(i, SRC / "九条院_表情组" / f"九条院_表情_{i:02d}{name}_透明_1440x2560.png") for i, name in [
            (1, "皮笑肉不笑"), (2, "微笑裂开一瞬"), (3, "意味深长的打量"), (4, "居高临下的冷笑"),
        ]],
    ),
    (
        "泽渡",
        "sawtari",
        SRC / "泽渡_定稿_透明_1440x2560.png",
        [(i, SRC / "泽渡_表情组" / f"泽渡_表情_{i:02d}{name}_透明_1440x2560.png") for i, name in [
            (1, "闭眼笑"), (2, "惊讶"), (3, "认真"),
        ]],
    ),
]


def union_bbox(images: list[Image.Image]) -> tuple[int, int, int, int]:
    """所有图的 alpha 并集裁切框（表情切换零漂移的关键）"""
    boxes = [im.getchannel("A").getbbox() for im in images]
    boxes = [b for b in boxes if b]
    if not boxes:
        raise ValueError("全部图片 alpha 为空")
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def main() -> int:
    dry = "--dry-run" in sys.argv
    total_bytes = 0
    failures: list[str] = []
    meta: dict[str, dict[str, int]] = {}  # stem → { baseTop }（引擎锚定补偿，见下）

    for name, stem, base_path, exprs in CHARACTERS:
        if not base_path.exists():
            failures.append(f"{name}: 缺基础图 {base_path.name}")
            continue
        missing = [p for _, p in exprs if not p.exists()]
        if missing:
            failures.append(f"{name}: 缺表情图 " + ", ".join(p.name for p in missing))
            continue

        base = Image.open(base_path).convert("RGBA")
        expr_imgs = [(i, Image.open(p).convert("RGBA"), p) for i, p in exprs]

        # ① 公共裁切框（基础 + 全部表情的并集）
        box = union_bbox([base, *(im for _, im, _ in expr_imgs)])
        # ② 只裁切、不缩放（上半身取景由引擎完成，见 Layers.ts）
        crop = base.crop(box)
        out_w, out_h = crop.size

        # ③ 基础图 alpha 顶在公共框内的偏移：
        #    差分里若有内容更高的图会把并集框顶撑高 → 基础图上方留透明带，
        #    引擎若按「框顶」对齐 HEAD_Y，基础图的**可见发顶**就会下沉 baseTop×scale。
        #    引擎读 meta.json 用 (texH - baseTop) 反推缩放，让**基础图发顶**精确落在 HEAD_Y。
        base_bb = base.getchannel("A").getbbox()
        base_top = max(0, (base_bb[1] - box[1]) if base_bb else 0)
        meta[stem] = {"baseTop": base_top}
        if base_top:
            print(f"    ⚠ 基础图顶部有 {base_top}px 透明带（某张差分更高）→ 已写入 meta 供引擎补偿")

        plan: list[tuple[Path, Image.Image]] = [(DST / f"{stem}.png", crop)]
        for i, im, _p in expr_imgs:
            plan.append((DST / f"{stem}_{i:02d}.png", im.crop(box)))

        print(f"{name}（{stem}）：源 {base.size} → 公共裁切 {out_w}x{out_h}（不缩放），共 {len(plan)} 张")
        for dest, img in plan:
            if dry:
                print(f"    [dry] {dest.relative_to(ROOT)}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest, optimize=True)
            size = dest.stat().st_size
            total_bytes += size
            print(f"    ✓ {dest.relative_to(ROOT)}  {img.size[0]}x{img.size[1]}  {size/1024:.0f} KB")

    print(f"\n合计 {total_bytes/1024/1024:.1f} MB")
    if dry:
        print("(dry-run，未写文件)")
    else:
        meta_path = DST / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"✓ 立绘元数据 → {meta_path.relative_to(ROOT)}（baseTop 供 Layers.ts 锚定补偿）")
        print("✓ 部署完成")
    if failures:
        print("\n✗ 失败：")
        for f in failures:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
