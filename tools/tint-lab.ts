/**
 * tint lab —— 用**真实 PixiJS** 渲染各时段滤镜，供目视验收。
 *
 * 为什么需要它：`timeTint.ts` 的配方先用 Python 复现矩阵做过一轮预览，
 * 但那是「照公式推的」，不等于 PixiJS 真跑出来的结果。这个页面直接调
 * `buildTimeFilter()`（生产代码本体），把同一张底图按各时段渲染成网格，
 * 一眼就能看出 ① 配方对不对 ② 与 Python 预览是否一致。
 *
 * 用法：起 vite dev server 后访问 /tools/tint-lab.html
 */
import { Application, Assets, Container, Sprite, Text, Texture } from 'pixi.js';
import { buildTimeFilter } from '../src/render/timeTint.ts';

/** 覆盖所有配方分支 + 一个基准（教室_午后 是 bg_001 的原生时段） */
const KEYS = [
  '教室_清晨', '教室_早晨', '教室_午后', '教室_午后斜阳',
  '教室_夕阳', '教室_黄昏', '教室_傍晚', '教室_暮色',
  '教室_夜', '教室_阴天', '教室_晴天', '教室_大风',
];

const TW = 300;
const TH = 169;
const COLS = 4;
const PAD = 8;
const LBL = 20;

const app = new Application();
await app.init({
  width: COLS * (TW + PAD) + PAD,
  height: Math.ceil(KEYS.length / COLS) * (TH + LBL + PAD) + PAD,
  background: 0xfafafa,
  antialias: false,
});
document.body.appendChild(app.canvas);

const tex = (await Assets.load('/assets/bg/bg_001.webp')) as Texture;

KEYS.forEach((key, i) => {
  const holder = new Container();
  holder.x = PAD + (i % COLS) * (TW + PAD);
  holder.y = PAD + Math.floor(i / COLS) * (TH + LBL + PAD);

  const sp = new Sprite(tex);
  sp.width = TW;
  sp.height = TH;
  sp.y = LBL;

  const f = buildTimeFilter(key);
  if (f) sp.filters = [f];

  const label = new Text({ text: `${key.replace('教室_', '')}${f ? '' : ' (无滤镜)'}`, style: { fontSize: 13, fill: 0x111111 } });

  holder.addChild(label, sp);
  app.stage.addChild(holder);
});

// 供 playwright 判断渲染完成
(window as unknown as { __ready: boolean }).__ready = true;
