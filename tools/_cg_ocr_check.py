# -*- coding: utf-8 -*-
"""用 RapidOCR 客观检测图片角落是否有文字水印（当前会话模型看不了图，靠 OCR 判定）。"""
import glob
import os
import sys

from PIL import Image
from rapidocr_onnxruntime import RapidOCR

ocr = RapidOCR()
d = r'.workbuddy\tmp\cg_regen'

targets = [
    ('生成图·右下角(修补前)', os.path.join(d, '_corner.png')),
    ('生成图·右下角(修补后)', os.path.join(d, '_corner5.png')),
    ('成品·右下角', None),
]
for label, p in targets:
    if p is None:
        continue
    if not os.path.exists(p):
        print(f'{label}: 文件不存在 {p}')
        continue
    res, _ = ocr(p)
    texts = [t[1] for t in res] if res else []
    print(f'{label}: {texts if texts else "无文字"}')

# 对照：老成品 CG 的同位置（若老图也有水印 ⇒ 水印是常态）
old = r'素材基准\CG\CG_长椅_隔着椅子_2560x1440.png'
if os.path.exists(old):
    im = Image.open(old).convert('RGB')
    W, H = im.size
    im.crop((W - 460, H - 200, W, H)).save(os.path.join(d, '_oldcorner.png'))
    res, _ = ocr(os.path.join(d, '_oldcorner.png'))
    texts = [t[1] for t in res] if res else []
    print(f'老成品(隔着椅子)·右下角: {texts if texts else "无文字"}')

# 新成品（木片樱花回礼）右下角
new = r'素材基准\CG\CG_长椅_木片樱花回礼_2560x1440.png'
im = Image.open(new).convert('RGB')
W, H = im.size
im.crop((W - 460, H - 200, W, H)).save(os.path.join(d, '_newcorner.png'))
res, _ = ocr(os.path.join(d, '_newcorner.png'))
texts = [t[1] for t in res] if res else []
print(f'新成品(木片樱花回礼)·右下角: {texts if texts else "无文字"}')

# 全图扫描（不只角落）——检测任何文字
res, _ = ocr(new)
texts = [t[1] for t in res] if res else []
print(f'新成品 全图文字: {texts if texts else "无文字"}')
