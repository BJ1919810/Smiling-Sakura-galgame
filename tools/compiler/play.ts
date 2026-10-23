/**
 * 终端版 runner —— 把整个剧本从头跑一遍。
 *
 * 用途：任务 3 终验的"在终端里玩完整个剧本"。
 * 用法：`npm run play` —— 全程无人工干预：遇到 choice 自动选第 0 项。
 *
 * 输出格式（每行一条事件）：
 *   [scene]   CH1-SC01  教室  午休
 *   [bg]      教室_午休
 *   [bgm]     日常・轻
 *   [say]     我  走进教室的时候，她已经坐在窗边了。
 *   [say]     樱  「……」
 *   ...
 *   [choice]  C-01: A=「你怎么知道的？」 | B=「你是怎么看出来的？」 | C=「……」(默认选 A)
 *   [var]     S-warm inc 1
 *   ...
 *   [end]
 */

import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { ScriptProgramSchema } from '../../src/engine/script/schema.ts';
import { GameState } from '../../src/engine/state/GameState.ts';
import { ScriptVM, type VmEvent } from '../../src/engine/script/ScriptVM.ts';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = resolve(__dirname, '../../src/generated/script.json');

function loadProgram() {
  if (!existsSync(SCRIPT)) {
    console.error('找不到 ' + SCRIPT + '。请先 `npm run compile`。');
    process.exit(1);
  }
  const raw = readFileSync(SCRIPT, 'utf-8');
  return ScriptProgramSchema.parse(JSON.parse(raw));
}

function fmtEvent(e: VmEvent, index: number): string {
  const pad = (n: number, w = 5) => String(n).padStart(w, ' ');
  const line = (() => {
    switch (e.type) {
    case 'scene':
      return `scene   ${e.id.padEnd(10)} ${e.place} · ${e.time}`;
    case 'bg':
      return `bg      ${e.name}`;
    case 'bgm':
      return `bgm     ${e.name}`;
    case 'se':
      return `se      ${e.name}`;
    case 'sprite-show':
      return `sprite  +${e.char}(${e.pose}) @${e.slot}`;
    case 'sprite-hide':
      return `sprite  -${e.char}`;
    case 'cg':
      return `cg      ${e.name}`;
    case 'say': {
      const who = e.who ? e.who : '旁白';
      const text = e.text.replace(/\n/g, ' ');
      return `${e.kind.padEnd(10)} ${who.padEnd(6)} ${text}`;
    }
    case 'fx':
      return `fx      ${e.fxType}${e.params ? ' ' + JSON.stringify(e.params) : ''}`;
    case 'note':
      return `note    ${e.text.slice(0, 60)}`;
    case 'window':
      return `window  ${e.action} ${e.id}${e.ttl ? ' ttl=' + e.ttl : ''}`;
    case 'var':
      return `var     ${e.id} ${e.mode} ${e.value}`;
    case 'choice':
      return `choice  ${e.id} → 自动选 ${e.options[0]?.key}「${e.options[0]?.text}」`;
    case 'end':
      return `end`;
    }
  })();
  return `[${pad(index)}] ${line}`;
}

function main() {
  const program = loadProgram();
  const state = new GameState();
  let count = 0;
  let choices = 0;
  let says = 0;
  const vm = new ScriptVM(program, state, (e: VmEvent) => {
    count++;
    if (e.type === 'say') says++;
    if (e.type === 'choice') choices++;
    if (process.env.QUIET !== '1') {
      console.log(fmtEvent(e, count));
    }
  });

  console.log(`▶ 加载剧本：${program.ops.length} 条 op`);
  console.log('▶ 开始播放（自动选第 0 项）…\n');

  // 跑
  let r = vm.runUntilChoice();
  while (r !== 'end') {
    vm.pickChoice(0);
    r = vm.runUntilChoice();
  }

  console.log('\n— 跑完 —');
  console.log(`事件总数：${count} | say：${says} | choice：${choices}`);
  console.log(`vars：${JSON.stringify(state.vars)}`);
  console.log(`最终章节：${state.currentChapter} | 场景：${state.currentSceneId}`);
}

main();
