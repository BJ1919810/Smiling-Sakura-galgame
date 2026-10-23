# -*- coding: utf-8 -*-
"""独立复核：exe 内嵌的语音资源完整性（绕开 exe_probe 的白名单）。

比对三处：
  A. manifest.voice（256 键）
  B. dist/assets/audio/voice/*.ogg（磁盘）
  C. exe 二进制里出现的 voice 路径字符串
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MANIFEST = os.path.join(ROOT, 'src', 'generated', 'assets.manifest.json')
VOICE_DIR = os.path.join(ROOT, 'dist', 'assets', 'audio', 'voice')
EXE = os.path.join(ROOT, '笑樱-测试版.exe')

fails = []

# A
with io.open(MANIFEST, encoding='utf-8') as f:
    mf = json.load(f)
mv = mf.get('voice') or {}
print(f'[A] manifest.voice：{len(mv)} 键')
if len(mv) != 256:
    fails.append(f'manifest.voice {len(mv)} != 256')

# B
disk = {f for f in os.listdir(VOICE_DIR) if f.endswith('.ogg')}
print(f'[B] dist 磁盘：{len(disk)} 个 ogg')
if len(disk) != 256:
    fails.append(f'dist ogg {len(disk)} != 256')
# 逐条对照 manifest → 磁盘
miss = [k for k, v in mv.items() if os.path.basename(v) not in disk]
if miss:
    fails.append(f'manifest 有但磁盘无：{len(miss)} 条 {miss[:5]}')
else:
    print('     manifest 256 键全部在磁盘找到 ✓')

# C
data = open(EXE, 'rb').read()
print(f'[C] exe：{len(data) / 1048576:.1f} MB')
found = set(re.findall(rb'assets/audio/voice/(v_\d{5}\.ogg)', data))
found = {x.decode() for x in found}
print(f'     exe 内命中的语音文件名：{len(found)} 个')
if len(found) != 256:
    lacks = sorted(disk - found)
    fails.append(f'exe 缺失 {len(lacks)} 个：{lacks[:8]}')
else:
    print('     exe 内 256 个语音文件名全部命中 ✓')

# 抽查若干条的时间戳/字节序列是否真实嵌入（取一个 ogg 头部特征）
sample = sorted(disk)[0]
blob = open(os.path.join(VOICE_DIR, sample), 'rb').read()
head = blob[:64]
print(f'[D] 抽样 {sample}：{len(blob)} B，exe 内含头部字节：{head in data}')

print('\n' + '=' * 50)
if fails:
    print('✗ 复核失败：')
    for x in fails:
        print('  -', x)
    sys.exit(1)
print('✓ exe 语音资源完整')
