/**
 * 雀跃演出 · 最小可运行合集（只搭目标场景，不进正式台本）
 *
 * 用途：主人验收「雀跃」演出用 —— 点按推进，每一步的括注都会标注「触发 / 不触发」。
 * 打开方式：`npm run dev` 后访问 **http://localhost:5173/preview.html**
 *
 * ★ 设计对照（这就是触发器的判定口径，见 src/engine/text/emotion.ts）：
 *   ① 中性括注（面无表情）            → 不跳
 *   ② 阳太「嬉笑」                    → 跳（欢愉）
 *   ③ 樱「抬头・眼睛微微睁大」        → 跳（震惊）
 *   ④ 九条院「完美微笑」              → 不跳（伪装面，反派脸上蹦一下会毁人设）
 *   ⑤ 樱「眼睛微微弯了一下」          → 跳（欢愉）
 *
 * ⚠️ 本文件只给 preview.html 用：vite build 默认只打包 index.html，
 *    所以这段测试剧本**不会**被打进正式 exe。
 */

import type { Op, ScriptProgram } from '../engine/script/ops.ts';

/**
 * 预览专用 say 构造器。
 *
 * ★ `line` 是**语音映射键**（真台本里＝台本行号）。预览是手写假数据，没有真行号 ——
 *   从一个远大于台本行数（实测 max 6798）的值起**递减**：
 *   既满足 schema 的 `positive()`，又保证**永远不会**在 manifest.voice 里查到，
 *   于是预览页不播任何语音，符合预期。
 */
let previewLine = 990000;
const say = (who: string | null, text: string): Op => ({
  op: 'say',
  who,
  kind: who === null ? 'narration' : 'speech',
  blocks: [{ text }],
  line: previewLine--,
});

const sprite = (char: string, pose: string, slot: string, action: 'show' | 'update' = 'show'): Op => ({
  op: 'sprite',
  char,
  pose,
  slot,
  action,
});

export const previewProgram: ScriptProgram = {
  version: 1,
  generatedAt: '2026-09-12T00:00:00.000Z',
  source: 'preview（手写最小合集 · 非台本产物）',
  chapters: [{ index: 1, title: '雀跃预览', startPc: 0 }],
  ops: [
    { op: 'scene', id: 'PV-SC01', chapter: 1, title: '雀跃预览', place: '旧校舍・樱树下长椅', time: '午休', weather: '晴' },
    { op: 'bg', name: '旧长椅_午间' },
    { op: 'note', text: '雀跃预览：点按推进，括注会标注触发与否' },

    // ① 对照组：中性括注 → 不跳
    sprite('龙司', '面无表情', '长椅右端'),
    say(null, '【预览 ①·对照组】括注「面无表情」——不含正向高情绪词，立绘应当一动不动。'),
    say('龙司', '……没什么好说的。'),

    // ② 阳太 嬉笑 → 跳（欢愉）
    sprite('阳太', '嬉笑', '长椅左端'),
    say(null, '【预览 ②·触发】括注「嬉笑」——欢愉。立绘应当快速向上跳一下再落回原位。'),
    say('阳太', '跟你说，今天食堂的炸猪排盖饭绝对能行！'),

    // ③ 樱 抬头・眼睛微微睁大 → 跳（震惊）
    sprite('樱', '抬头・眼睛微微睁大', '长椅中央'),
    say(null, '【预览 ③·触发】括注「抬头・眼睛微微睁大」——震惊。跳。'),
    say('樱', '……你知道？'),

    // ④ 九条院 完美微笑 → 不跳（伪装面）
    sprite('九条院', '完美微笑', '长椅右外侧'),
    say(null, '【预览 ④·反向对照】括注「完美微笑」——她的伪装面，刻意不触发（在反派脸上蹦一下会毁人设）。'),
    say('九条院', '哎呀，气氛真好呢。'),

    // ⑤ 樱 眼睛微微弯了一下 → 跳（欢愉）
    sprite('樱', '眼睛微微弯了一下', '长椅中央', 'update'),
    say(null, '【预览 ⑤·触发】括注「眼睛微微弯了一下」——欢愉。跳。'),
    say('樱', '嗯。很甜。'),

    { op: 'end' },
  ],
};
