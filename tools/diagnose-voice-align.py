# -*- coding: utf-8 -*-
"""语音错位：精确诊断 + 保序重映射方案（2026-09-20）。

★ 机理
  语音＝「台本行号 → 音频」的外挂映射。合成时记下行号、存成 `v_<行号5位>.ogg`；
  运行期用编译期带出的 `say.line` 查表。台本后来增删行 ⇒ 同一行号指向别的台词 ⇒ 错位。

★ 判据（两条必须同时成立，否则会误判）
  ① **比「说出口的字」**：任务表的 `zh` 不含括注，而台本行可能是
     `嗯。（把书翻开，重新看回去）` ⇒ 直接比字符串会把**正确**的判成错位。
     故两侧都剥掉括注 `（…）` 再比。
  ② **保序**：短句（`嗯。` / `……`）在台本里重复出现几十次，靠文本查行号必然认错。
     台本只发生「插入/删除」⇒ 行号映射**单调不减** ⇒ 用 difflib 的最长公共子序列
     做保序比对，重复项也能按先后正确对上。

★ 输出
  · 错位分布（按章）
  · old 行号 → new 行号 的完整映射（--plan 打印，--dump 落盘 JSON）
  · 音频原文已不在台本者（只能重录/放弃）

用法：
  python tools/diagnose-voice-align.py                 # 只报告
  python tools/diagnose-voice-align.py --plan          # 打印映射样例
  python tools/diagnose-voice-align.py --dump out.json # 落盘完整映射
"""
import difflib
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
SCENARIO = ROOT / '笑樱_galgame台本.md'
SCRIPT = ROOT / 'src' / 'generated' / 'script.json'
TASKS = ROOT / '素材基准' / '音频' / '语音'
VOICE_DIR = ROOT / 'public' / 'assets' / 'audio' / 'voice'

TABLES = {
    '樱': '日配任务_樱_全篇_合并.jsonl',
    '阳太': '日配任务_阳太_全篇.jsonl',
    '龙司': '日配任务_龙司_全篇.jsonl',
    '九条院': '日配任务_九条院_全篇.jsonl',
    '亚里沙': '日配任务_亚里沙_全篇.jsonl',
    '泽渡': '日配任务_泽渡_全篇.jsonl',
}

RE_PAREN = re.compile(r'（[^）]*）|\([^)]*\)')


def core(s: str) -> str:
    """归一化成「说出口的字」：剥括注 → 去标点空白。"""
    s = RE_PAREN.sub('', s or '')
    return re.sub(r'[\s　·、。，．！？…—―「」『』:：;；"\'’“”]+', '', s)


def main() -> int:
    lines = SCENARIO.read_text(encoding='utf-8').split('\n')
    chapters = []
    for i, l in enumerate(lines, 1):
        m = re.match(r'^#+\s*第\s*(\d+)\s*章', l.strip())
        if m:
            chapters.append((i, int(m.group(1))))
    sorted_ch = sorted(chapters)

    def chap_of(ln: int) -> int:
        c = 0
        for start, num in sorted_ch:
            if ln >= start:
                c = num
        return c

    # —— 新序列：当前编译产物的 say（行号, 说出口的字）——
    prog = json.loads(SCRIPT.read_text(encoding='utf-8'))
    new_items: list[tuple[int, str]] = []
    for o in prog['ops']:
        if o.get('op') == 'say':
            txt = ''.join(b.get('text', '') for b in (o.get('blocks') or []))
            c = core(txt)
            if c:
                new_items.append((o['line'], c))
    new_items.sort()
    # 同一行号只留一条（差分/条件块的 base 与 cases 可能共用行号）
    seen = set()
    new_items = [x for x in new_items if not (x[0] in seen or seen.add(x[0]))]

    # —— 旧序列：任务表记录（行号, 说出口的字, 角色）——
    recs = []
    for who, fname in TABLES.items():
        p = TASKS / fname
        if not p.exists():
            print(f'[跳过] {who}: 找不到 {fname}')
            continue
        for r in (json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()):
            recs.append((r['line'], core(r.get('zh') or ''), who, r.get('zh') or ''))
    recs.sort()
    recs = [r for r in recs if r[1]]

    print(f'当前台本 say（有字）{len(new_items)} 条；日配任务记录 {len(recs)} 条')
    print()

    # —— 保序比对 ——
    a = [r[1] for r in recs]
    b = [x[1] for x in new_items]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)

    mapping: dict[int, int] = {}
    orphans: list = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                mapping[recs[i1 + k][0]] = new_items[j1 + k][0]
        elif tag in ('delete', 'replace'):
            for k in range(i1, i2):
                orphans.append(recs[k])

    bad = {old: new for old, new in mapping.items() if old != new}
    print('=== 结果 ===')
    print(f'  ✓ 行号本来就对   : {len(mapping) - len(bad)}')
    print(f'  ✗ 需要改行号     : {len(bad)}')
    print(f'  ? 音频原文已不在台本: {len(orphans)}  ← 只能重录或放弃')
    print()

    if bad:
        by: dict[int, list] = {}
        for old, new in bad.items():
            by.setdefault(chap_of(old), []).append((old, new))
        print('=== 错位分布（按章）===')
        for ch in sorted(by):
            g = sorted(by[ch])
            deltas = sorted({n - o for o, n in g})
            print(f'  第 {ch} 章：{len(g):3d} 条需改，Δ {deltas}'
                  f'   行 {g[0][0]}→{g[0][1]} … {g[-1][0]}→{g[-1][1]}')
        print()

    if orphans:
        print('=== 音频原文已不在台本（需重录/放弃）===')
        by: dict[int, int] = {}
        for ln, c, who, zh in orphans:
            by[chap_of(ln)] = by.get(chap_of(ln), 0) + 1
        print('  ' + '、'.join(f'第{k}章 {v} 条' for k, v in sorted(by.items())))
        for ln, c, who, zh in orphans[:10]:
            print(f'    v_{ln:05d}.ogg [{who}] {zh[:34]!r}')
        print()

    if '--plan' in sys.argv and bad:
        print('=== 映射样例（前 15 条）===')
        for old, new in sorted(bad.items())[:15]:
            print(f'  v_{old:05d}.ogg → v_{new:05d}.ogg')
        print()

    if '--dump' in sys.argv:
        out = Path(sys.argv[sys.argv.index('--dump') + 1])
        out.write_text(json.dumps({
            'mapping': {str(k): v for k, v in sorted(mapping.items())},
            'renames': {str(k): v for k, v in sorted(bad.items())},
            'orphans': [{'line': ln, 'who': who, 'zh': zh} for ln, c, who, zh in orphans],
        }, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'✓ 映射已落盘 {out}')

    # —— 安全性核对 ——
    print('=== 安全性核对 ===')
    targets = list(bad.values())
    dup = {t for t in targets if targets.count(t) > 1}
    print(f'  目标行号重复：{len(dup)} 个 {sorted(dup)[:5]}' if dup else '  目标行号重复：无 ✓')
    mono = all(sorted(bad)[i] <= sorted(bad.values(), key=lambda v: v)[i] for i in range(len(bad))) \
        if bad else True
    print('  映射单调性：' + ('✓（保序比对保证）' if mono else '✗ 需人工检查'))
    occupied = set(mapping.keys())
    clash = sorted(set(targets) & occupied)
    print(f'  与未移动文件冲突：{len(clash)} 个 {clash[:5]}' if clash else '  与未移动文件冲突：无 ✓')
    return 0


if __name__ == '__main__':
    sys.exit(main())
