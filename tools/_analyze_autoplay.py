# -*- coding: utf-8 -*-
"""分析自动播放实测记录：推进间隔是否＝语音时长（+ 呼吸）。

输入：`.workbuddy/tmp/autoplay-probe.json`（probe-autoplay.cjs 产出）
判定：对每次推进，找它紧后出现的语音请求 → 那句的语音时长；
     若「本次推进 → 下次推进」的间隔 ≈ 该语音时长 + 0.35s，即证明节奏跟的是语音。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, '.workbuddy', 'tmp', 'autoplay-probe.json')
VOICE_DIR = os.path.join(ROOT, 'public', 'assets', 'audio', 'voice')
FFPROBE = r'D:\ffmpeg\bin\ffprobe.exe'
TAIL = 0.35  # Director 里的 VOICE_TAIL_MS

_cache: dict[str, float] = {}


def dur(name: str) -> float:
    if name in _cache:
        return _cache[name]
    p = os.path.join(VOICE_DIR, name)
    if not os.path.exists(p):
        _cache[name] = -1.0
        return -1.0
    r = subprocess.run(
        [FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=nw=1:nk=1', p],
        capture_output=True, text=True,
    )
    d = float(r.stdout.strip())
    _cache[name] = d
    return d


def main() -> int:
    with io.open(SRC, encoding='utf-8') as f:
        data = json.load(f)

    adv = data['advances']
    vr = data['voiceReqs']
    auto = [a for a in adv if a['phase'] == 'auto']
    print(f'推进记录 {len(adv)} 条（自动阶段 {len(auto)}），语音请求 {len(vr)} 个\n')
    if len(auto) < 2:
        print('✗ 自动阶段推进太少，无法比对')
        return 1

    print(f'{"推进":<6}{"pc":>6}{"间隔s":>9}{"该句语音":>12}{"语音时长":>10}{"预期间隔":>10}{"偏差":>8}')
    print('-' * 64)

    hit, miss = 0, 0
    devs = []
    # 匹配窗口 ±0.8s —— `a['t']` 是**轮询检测到** pc 变化的时刻，比真实推进晚一点
    # （poll 周期 40–60ms，且每次要读 DOM），语音请求则发生在真实推进的当口。
    WIN = 800
    for i in range(len(auto) - 1):
        a, nxt = auto[i], auto[i + 1]
        gap = (nxt['t'] - a['t']) / 1000.0
        v = next((v for v in vr if -WIN <= v['t'] - a['t'] <= WIN), None)
        if v:
            d = dur(v['url'])
            expect = d + TAIL
            dev = gap - expect
            devs.append(dev)
            hit += 1
            mark = '✓' if abs(dev) < 0.5 else '✗'
            print(f'{i:<6}{a["pc"]:>6}{gap:>9.2f}{v["url"]:>12}{d:>10.2f}'
                  f'{expect:>10.2f}{dev:>+8.2f} {mark}')
        else:
            miss += 1
            print(f'{i:<6}{a["pc"]:>6}{gap:>9.2f}{"—（无配音）":>12}{"—":>10}'
                  f'{"字数公式":>10}{"—":>8}')

    print('-' * 64)
    print(f'有配音的推进：{hit} 条，无配音：{miss} 条')
    if devs:
        avg = sum(devs) / len(devs)
        print(f'与「语音时长 + {TAIL}s」的平均偏差：{avg:+.2f}s')
        ok = sum(1 for d in devs if abs(d) < 0.45)
        print(f'落在 ±0.45s 内的：{ok}/{len(devs)}')
        print()
        if ok >= max(1, len(devs) * 0.7):
            print('✓ 结论：自动播放节奏确实**跟随语音长度**')
        else:
            print('✗ 结论：间隔与语音长度不符，需排查')
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
