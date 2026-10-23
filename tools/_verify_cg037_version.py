# -*- coding: utf-8 -*-
"""验明：游戏包里的 cg_037.webp 究竟是哪一版（与各候选做像素比对）。

候选：
  C = 原版提示词重跑版（主人 19:26 认可「笑脸正常」）—— 现 素材基准 内
  A = 昨天的 2K 定稿（提示词文档记载带「两道黑斜条像眼罩」，疑为「吓人」源头）
  D1 = 无参考图改写版（主人 19:0x 否决）—— 已标记 _废_
"""
import io
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, '.workbuddy', 'tmp', 'cg_regen')

# ① 从包里取出 webp 解成 png
webp = os.path.join(ROOT, 'public', 'assets', 'cg', 'cg_037.webp')
cur = Image.open(webp).convert('RGB')
# ② 同时从 dist 取一份（确认打包源一致）
webp_dist = os.path.join(ROOT, 'dist', 'assets', 'cg', 'cg_037.webp')
d = Image.open(webp_dist).convert('RGB') if os.path.exists(webp_dist) else None
same = d is not None and np.array_equal(np.array(cur), np.array(d))
print(f'包里 webp     : {cur.size}  {os.path.getsize(webp):,} B')
print(f'dist webp 一致: {same}')

cands = {
    'C 重跑版(认可过)': os.path.join(ROOT, '素材基准', 'CG', 'CG_长椅_木片樱花回礼_2560x1440.png'),
    'A 昨天定稿':       os.path.join(ROOT, '.workbuddy', 'backup', 'CG_重出前_木片_20260919', 'CG_长椅_木片樱花回礼_2560x1440.png'),
    'D1 被否版':        os.path.join(TMP, '_废_樱花回礼_ImageGen版_主人否决_20260919.png'),
}


def rmse(a: Image.Image, b: Image.Image) -> float:
    size = (256, 144)
    x = np.asarray(a.resize(size, Image.LANCZOS), dtype=np.float64)
    y = np.asarray(b.resize(size, Image.LANCZOS), dtype=np.float64)
    return float(np.sqrt(((x - y) ** 2).mean()))


print()
print('与各候选的 RMSE（越小越像）：')
best, best_v = None, 1e9
for name, p in cands.items():
    if not os.path.exists(p):
        print(f'  {name}: 文件缺失')
        continue
    v = rmse(cur, Image.open(p).convert('RGB'))
    print(f'  {name:<18} RMSE {v:7.2f}')
    if v < best_v:
        best, best_v = name, v
print(f'\n⇒ 包里的图最接近：{best}（RMSE {best_v:.2f}）')

# ③ 把包里的图导出成 PNG，供主人直接对照
out = os.path.join(TMP, '当前游戏内_书签_cg037.png')
cur.save(out)
print('已导出对照图 ->', out)
