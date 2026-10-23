# -*- coding: utf-8 -*-
"""语音错位精确修复（2026-09-20）。

★★ 根因（已用数据锁定，非推测）
  对全部历史台本备份逐个打分（判据：任务表 `line` 处的原文 == 音频原文）：

     100.0%  256/256  台本_合并哨声前_20260918.md  ← **合成时用的就是这一版**
      41.8%  107/256  笑樱_galgame台本.md（当前）
      57.4%  147/256  台本_岔路收CG前_20260918.md …

  当前台本 = 那一版 **+4 行**，新增在 **[2480, 2481] 与 [2595, 2596]**（都在第 3 章内），
  另改写 3 行。备份名 `合并哨声前` 即那次改动。
  ⇒ 第 3 章 2480 行之后的所有行号整体 +2/+4 ⇒ **第三章往后的语音贴到了别的台词上**
    （正与主人的观察一致；第 1 章与第 2 章在 2480 之前，所以没受影响）。

★ 为什么这次不用文本查找
  台本里大量重复短句（`嗯。`），文本反查必然认错行。
  两份台本只差 4 行 ⇒ **行级 diff 的映射唯一确定**，不受重复影响。

★ 修的问题有两层（都要改，否则将来重新部署会复发）
  ① 已部署的 `public/assets/audio/voice/v_<旧行号>.ogg` → 改成新行号；
  ② `素材基准/音频/语音/日配任务_*.jsonl` 里记录的行号字段（**台账**）——
     否则下次再跑 deploy-voice 又会按旧行号命名，bug 复发。

用法：
  python tools/fix-voice-align.py            # dry-run，只报告 + 校验每张表
  python tools/fix-voice-align.py --apply    # 执行（先自动留底）
"""
import difflib
import json
import re
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent.parent
OLD_SCENARIO = ROOT / '.workbuddy' / 'backup' / '台本_合并哨声前_20260918.md'
NEW_SCENARIO = ROOT / '笑樱_galgame台本.md'
TASKS = ROOT / '素材基准' / '音频' / '语音'
VOICE_DIR = ROOT / 'public' / 'assets' / 'audio' / 'voice'
BACKUP = ROOT / '.workbuddy' / 'backup' / '语音重编号前_20260920'

RE_PAREN = re.compile(r'（[^）]*）|\([^)]*\)')


def core(s: str) -> str:
    s = RE_PAREN.sub('', s or '')
    return re.sub(r'[\s　·、。，．！？…—―「」『』:：;；"\'’“”]+', '', s)


def build_omap(old: list, new: list) -> tuple[dict, set]:
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    omap, deleted = {}, set()
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                omap[i1 + k + 1] = j1 + k + 1
        elif tag in ('delete', 'replace'):
            for i in range(i1, i2):
                deleted.add(i + 1)
    return omap, deleted


def main() -> int:
    apply = '--apply' in sys.argv
    old = OLD_SCENARIO.read_text(encoding='utf-8').split('\n')
    new = NEW_SCENARIO.read_text(encoding='utf-8').split('\n')
    omap, deleted = build_omap(old, new)

    ins = sorted(j + 1 for tag, i1, i2, j1, j2 in
                 difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
                 if tag == 'insert' for j in range(j1, j2))
    print(f'旧（合成时）{len(old)} 行  →  新（当前）{len(new)} 行   差 {len(new) - len(old):+d}')
    print(f'  新增行号 {ins}    被删/改写行 {sorted(deleted)}')
    print(f'  可映射 {len(omap)} 行')
    print()

    # ——— ① 校验每张任务表是否同一坐标系 ———
    tables = sorted(TASKS.glob('日配任务_*.jsonl'))
    print('=== 各任务表坐标系校验（旧台本命中率；同时也要看当前台本命中率）===')
    rows_by_table: dict[Path, list] = {}
    for p in tables:
        try:
            rows = [json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
        except Exception as e:
            print(f'  [跳过] {p.name}: 解析失败 {e}')
            continue
        if not rows or 'line' not in rows[0]:
            print(f'  [跳过] {p.name}: 无 line 字段')
            continue
        rows_by_table[p] = rows
        h_old = h_new = 0
        for r in rows:
            ln, want = r['line'], core(r.get('zh') or r.get('text') or '')
            if not want:
                continue
            if any(core(x) == want or (len(want) > 3 and want in core(x))
                   for x in old[max(0, ln - 1): ln + 3]):
                h_old += 1
            if any(core(x) == want or (len(want) > 3 and want in core(x))
                   for x in new[max(0, ln - 1): ln + 3]):
                h_new += 1
        n = len(rows) or 1
        flag = '✓ 同一版台本' if h_old / n > 0.95 else '⚠ 需人工确认'
        print(f'  {h_old / n * 100:5.1f}% / 当前 {h_new / n * 100:5.1f}%  '
              f'{len(rows):4d} 条  {p.name:34s} {flag}')
    print()

    # ——— ② 统计需要改名的文件 ———
    deployed = {int(m.group(1)) for f in VOICE_DIR.iterdir()
                if (m := re.match(r'^v_(\d+)\.ogg$', f.name))}
    lines_in_tables = {r['line'] for rows in rows_by_table.values() for r in rows}
    renames = sorted((ln, omap[ln]) for ln in (deployed & lines_in_tables)
                     if ln in omap and omap[ln] != ln)
    gone = sorted(ln for ln in (deployed & lines_in_tables) if ln in deleted or ln not in omap)
    same = len(deployed & lines_in_tables) - len(renames) - len(gone)

    print('=== 已部署文件 ===')
    print(f'  文件 {len(deployed)} 个；任务表覆盖 {len(deployed & lines_in_tables)} 个')
    print(f'  ✓ 行号正确不用动 : {same}')
    print(f'  ✗ 需重命名       : {len(renames)}')
    print(f'  ? 落在改写区间   : {len(gone)}  {gone if gone else "（无 ⇒ 不必重录）"}')
    if renames:
        d: dict[int, int] = {}
        for o, t in renames:
            d[t - o] = d.get(t - o, 0) + 1
        print(f'  偏移分布：' + '，'.join(f'Δ{k:+d} {v} 条' for k, v in sorted(d.items())))
    print()

    # ——— ③ 安全性核对 ———
    tgts = [t for _, t in renames]
    dup = sorted({t for t in tgts if tgts.count(t) > 1})
    stay = {ln for ln in deployed if ln in omap and omap[ln] == ln} - {o for o, _ in renames}
    clash = sorted(set(tgts) & stay)
    print('=== 安全性核对 ===')
    print(f'  目标行号重复        ：{dup if dup else "无 ✓"}')
    print(f'  被静止文件占用      ：{clash if clash else "无 ✓"}')
    print(f'  需要两阶段重命名    ：{"是（目标互相占用）" if set(tgts) & deployed else "否"}')
    print()

    if not apply:
        print('（dry-run，未改动文件。加 --apply 执行）')
        return 0

    assert not dup and not clash, '安全性核对未通过，中止'

    # ============ 执行 ============
    BACKUP.mkdir(parents=True, exist_ok=True)

    # ① 部署文件：两阶段重命名 + 留底
    staged = []
    for o, t in renames:
        src = VOICE_DIR / f'v_{o:05d}.ogg'
        if not src.exists():
            continue
        shutil.copy2(src, BACKUP / src.name)
        tmp = VOICE_DIR / f'__tmp_{o:05d}.ogg'
        src.rename(tmp)
        staged.append((tmp, VOICE_DIR / f'v_{t:05d}.ogg'))
    for tmp, dst in staged:
        tmp.rename(dst)
    print(f'✓ 已重命名 {len(staged)} 个音频文件（原文件留底于 {BACKUP.name}/）')

    # ② 任务表（台账）：更新 line 字段
    edited = 0
    for p, rows in rows_by_table.items():
        shutil.copy2(p, BACKUP / p.name)
        changed = False
        for r in rows:
            ln = r['line']
            if ln in omap and omap[ln] != ln:
                r['line'] = omap[ln]
                changed = True
        if changed:
            with p.open('w', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
            edited += 1
    print(f'✓ 已更新 {edited} 张任务表的 line 字段（原表留底于 {BACKUP.name}/）')
    print()
    print('  下一步：npm run manifest → 重新出包（APK / exe）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
