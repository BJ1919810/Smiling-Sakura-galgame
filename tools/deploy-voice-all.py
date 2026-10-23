# -*- coding: utf-8 -*-
"""六角色语音统一部署：日配成品/<角色>/*.wav → public/assets/audio/voice/v_<行号5位>.ogg

★ 复用 deploy-voice.py 的 plan_trim（裁首尾静音）与转码参数（44.1k mono q5）。
★ 跨角色校验：所有角色的台本行号必须全局唯一（每个 say op 一行一行号）。
★ 部署后核对：输出目录 ogg 数 == 各角色任务表行数之和，且无孤儿文件。
"""
from __future__ import annotations

import importlib.util as iu
import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
V = os.path.join(ROOT, '素材基准', '音频', '语音')
OUT_DIR = os.path.join(ROOT, 'public', 'assets', 'audio', 'voice')

spec = iu.spec_from_file_location('dv', os.path.join(HERE, 'deploy-voice.py'))
dv = iu.module_from_spec(spec)
spec.loader.exec_module(dv)

# (角色, 任务表, 归一化成品目录)
CHARS = [
    ('樱',   os.path.join(V, '日配任务_樱_全篇_合并.jsonl'), os.path.join(V, '日配成品', '樱')),
    ('阳太',  os.path.join(V, '日配任务_阳太_全篇.jsonl'),  os.path.join(V, '日配成品', '阳太')),
    ('龙司',  os.path.join(V, '日配任务_龙司_全篇.jsonl'),  os.path.join(V, '日配成品', '龙司')),
    ('亚里沙', os.path.join(V, '日配任务_亚里沙_全篇.jsonl'), os.path.join(V, '日配成品', '亚里沙')),
    ('九条院', os.path.join(V, '日配任务_九条院_全篇.jsonl'), os.path.join(V, '日配成品', '九条院')),
    ('泽渡',  os.path.join(V, '日配任务_泽渡_全篇.jsonl'),  os.path.join(V, '日配成品', '泽渡')),
]

# --raw：未经响度归一化的原始音量（EQ 保留——EQ 是逐角色定过稿的音色）
RAW_SOURCES = {
    '樱':   os.path.join(ROOT, '.workbuddy', 'backup', 'voice_raw'),
    '阳太':  os.path.join(ROOT, '.workbuddy', 'tmp', 'yota_all'),
    '龙司':  os.path.join(V, '日配输出_龙司'),
    '亚里沙': os.path.join(V, '日配输出_亚里沙'),
    '九条院': os.path.join(V, '日配输出_九条院'),
    '泽渡':  os.path.join(ROOT, '.workbuddy', 'tmp', 'sawa_full_eq'),
}


def main() -> int:
    use_raw = '--raw' in sys.argv
    chars = CHARS
    if use_raw:
        chars = [(ch, jl, RAW_SOURCES[ch]) for ch, jl, _ in CHARS]
        print('★ --raw 模式：使用未经响度归一化的原始音量（EQ 保留）')
    # ① 建立全部映射 + 跨角色行号唯一性校验
    jobs: list[tuple[str, int, str]] = []  # (src_path, line, stem)
    line_owner: dict[int, str] = {}
    for ch, jsonl, src in chars:
        id2line: dict[str, int] = {}
        for ln in io.open(jsonl, encoding='utf-8'):
            ln = ln.strip()
            if ln:
                r = json.loads(ln)
                id2line[r['id']] = int(r['line'])
        wavs = sorted(f for f in os.listdir(src) if f.endswith('.wav') and not f.startswith('_'))
        if len(wavs) != len(id2line):
            print(f'✗ {ch}: 成品 {len(wavs)} wav ≠ 任务表 {len(id2line)} 条')
            return 1
        for f in wavs:
            stem = os.path.splitext(f)[0]
            if stem not in id2line:
                print(f'✗ {ch}/{f} 不在任务表')
                return 1
            line = id2line[stem]
            if line in line_owner:
                print(f'✗ 行号冲突 L{line}: {ch} 与 {line_owner[line]}')
                return 1
            line_owner[line] = ch
            jobs.append((os.path.join(src, f), line, stem))
    print(f'映射：{len(jobs)} 条，行号全局唯一 ✓')

    # ② 转码部署（裁静音）
    os.makedirs(OUT_DIR, exist_ok=True)
    ok = fail = trimmed = 0
    for i, (src_path, line, stem) in enumerate(jobs, 1):
        dst = os.path.join(OUT_DIR, f'v_{line:05d}.ogg')
        cmd = [dv.FFMPEG, '-y', '-hide_banner', '-loglevel', 'error']
        plan = dv.plan_trim(src_path)
        if plan is not None:
            start, length, _, _ = plan
            cmd += ['-i', src_path, '-ss', f'{start:.4f}', '-t', f'{length:.4f}']
            trimmed += 1
        else:
            cmd += ['-i', src_path]
        cmd += ['-ac', '1', '-ar', str(dv.TARGET_SR),
                '-af', 'afade=t=in:st=0:d=0.003',
                '-c:a', 'libvorbis', '-q:a', dv.OGG_Q, dst]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            fail += 1
            print(f'  ✗ {stem}: {r.stderr.decode("utf-8", "replace")[:160]}')
        else:
            ok += 1
        if i % 100 == 0:
            print(f'  … {i}/{len(jobs)}')
    print(f'部署 {ok} 成功 / {fail} 失败；裁静音 {trimmed} 条')

    # ③ 孤儿核对
    expected = {f'v_{line:05d}.ogg' for _, line, _ in jobs}
    got = {f for f in os.listdir(OUT_DIR) if f.endswith('.ogg')}
    orphans = got - expected
    missing = expected - got
    print(f'输出目录：{len(got)} 个 ogg（预期 {len(expected)}，孤儿 {len(orphans)}，缺 {len(missing)}）')
    if orphans:
        print('  孤儿样例：', sorted(orphans)[:5])
    if fail or orphans or missing:
        return 1
    total = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in got)
    print(f'合计 {total / 1048576:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
