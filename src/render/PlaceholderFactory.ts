/**
 * 占位素材工厂
 *
 * 用途：零素材状态下跑通全流程（技术设计.md §8.3）。
 * 每个资源按名称生成**稳定**的占位图 —— 同一个 key 每次生成完全一样（不会闪烁）。
 *
 * 设计：
 * - 用字符串 hash → 色相，保证「同一 BG 永远同一颜色」
 * - 立绘是「人物剪影 + 名字 + 表情提示」的可辨识色块，不是纯色
 * - 缺素材时自动降级为占位，不报错、不白屏
 */

import { Container, Graphics, Text } from 'pixi.js';
import { DESIGN_HEIGHT, DESIGN_WIDTH } from './Stage.ts';

/** FNV-1a 32bit：把资源名稳定映射为一个数（决定色相/构图） */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function hsl(h: number, s: number, l: number): number {
  // HSL → RGB → 0xRRGGBB
  const a = (s / 100) * Math.min(l / 100, 1 - l / 100);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    const c = l / 100 - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    return Math.round(255 * c);
  };
  return (f(0) << 16) | (f(8) << 8) | f(4);
}

/** 场景类型推断：按 BG 名字里的关键词决定构图 */
type SceneKind = '室内' | '樱树' | '操场' | '走廊' | '长椅' | '夜' | '通用';

function guessKind(name: string): SceneKind {
  if (/夜|书桌/.test(name)) return '夜';
  if (/樱/.test(name)) return '樱树';
  if (/操场|看台|体育/.test(name)) return '操场';
  if (/走廊|廊/.test(name)) return '走廊';
  if (/长椅|椅/.test(name)) return '长椅';
  if (/教室|房间|中庭|校舍|小路|岔路/.test(name)) return '室内';
  return '通用';
}

/** 时辰 → 天光色（用于背景上部） */
function skyTint(name: string): number {
  if (/夜/.test(name)) return 0x1a1c2e;
  if (/黄昏|夕阳|暮|斜阳|傍晚/.test(name)) return 0x4a3340;
  if (/清晨|早晨|朝/.test(name)) return 0x3a4256;
  if (/午后|午休|午间|正午/.test(name)) return 0x4a5568;
  return 0x3f4756;
}

/**
 * 生成背景占位。
 * @param name 中文资源名（如 `教室_午休`）
 */
export function makeBgPlaceholder(name: string): Container {
  const c = new Container();
  c.label = `bg:${name}`;
  const W = DESIGN_WIDTH;
  const H = DESIGN_HEIGHT;

  const h = hash(name);
  const kind = guessKind(name);
  const sky = skyTint(name);
  // 地面色：按 hash 抖动，但保持低饱和
  const ground = hsl(h % 360, 18, 22 + (h % 7));

  const g = new Graphics();

  // 天空 / 上部
  g.rect(0, 0, W, H * 0.66).fill(sky);

  // 地面
  g.rect(0, H * 0.66, W, H * 0.34).fill(ground);

  // 按场景类型加特征
  if (kind === '室内') {
    // 窗格：几道竖向光带
    for (let i = 0; i < 4; i++) {
      const x = W * 0.12 + i * W * 0.2;
      g.rect(x, H * 0.12, W * 0.09, H * 0.4).fill({ color: 0xffffff, alpha: 0.05 });
    }
    g.rect(0, H * 0.62, W, 8).fill({ color: 0x000000, alpha: 0.25 }); // 踢脚线
  } else if (kind === '樱树') {
    // 树冠（几个交叠圆）+ 树干
    const cx = W * (0.28 + ((h % 40) / 100));
    g.circle(cx, H * 0.3, 210).fill({ color: 0xe8a0b4, alpha: 0.22 });
    g.circle(cx - 130, H * 0.36, 150).fill({ color: 0xd98fa4, alpha: 0.18 });
    g.circle(cx + 140, H * 0.34, 165).fill({ color: 0xf0b0c2, alpha: 0.16 });
    g.rect(cx - 26, H * 0.36, 52, H * 0.3).fill(0x4a3f38);
  } else if (kind === '长椅') {
    g.roundRect(W / 2 - 330, H * 0.7, 660, 42, 8).fill(0x4a3f38);
    g.rect(W / 2 - 310, H * 0.7 + 42, 24, 110).fill(0x3d342e);
    g.rect(W / 2 + 286, H * 0.7 + 42, 24, 110).fill(0x3d342e);
  } else if (kind === '操场') {
    g.ellipse(W / 2, H * 0.78, W * 0.42, H * 0.16).fill({ color: 0x5a4a3a, alpha: 0.55 });
    g.rect(0, H * 0.66, W, 4).fill({ color: 0xffffff, alpha: 0.12 }); // 白线
  } else if (kind === '走廊') {
    for (let i = 0; i < 6; i++) {
      const x = W * 0.06 + i * W * 0.16;
      g.rect(x, H * 0.18, W * 0.055, H * 0.46).fill({ color: 0x000000, alpha: 0.22 });
    }
  } else if (kind === '夜') {
    g.rect(0, 0, W, H).fill({ color: 0x000000, alpha: 0.32 });
    for (let i = 0; i < 26; i++) {
      const x = (h * (i + 3) * 97) % W;
      const y = ((h * (i + 7) * 53) % (H * 0.5));
      g.circle(x, y, 1.6).fill({ color: 0xffffff, alpha: 0.35 });
    }
  }

  // 底部压暗（让对话框区域更沉）
  g.rect(0, H * 0.82, W, H * 0.18).fill({ color: 0x000000, alpha: 0.28 });

  c.addChild(g);

  // 标签
  const label = new Text({
    text: `BG：${name}`,
    style: { fontFamily: 'sans-serif', fontSize: 34, fill: 0xffffff, letterSpacing: 2 },
  });
  label.alpha = 0.34;
  label.position.set(40, 36);
  c.addChild(label);

  const kindTag = new Text({
    text: `[占位·${kind}]`,
    style: { fontFamily: 'sans-serif', fontSize: 22, fill: 0xe8a0b4 },
  });
  kindTag.alpha = 0.5;
  kindTag.position.set(40, 82);
  c.addChild(kindTag);

  return c;
}

/** 角色 → 主色（用于剪影） */
const CHAR_COLOR: Record<string, number> = {
  龙司: 0x5a7a9a,
  樱: 0xe8a0b4,
  阳太: 0x7aa87a,
  亚里沙: 0xd8b060,
  九条院: 0xa07ac0,
  泽渡: 0x8a8a9a,
};

/** 立绘尺寸（技术设计.md §12.3：角色实际高 950–1100px，底部锚点对齐） */
const SPRITE_H = 1000;

/**
 * 生成立绘占位：人物剪影 + 名字 + 表情提示。
 * 底部锚点对齐（y = DESIGN_HEIGHT 为脚底）。
 */
export function makeSpritePlaceholder(char: string, pose: string, slot: string): Container {
  const c = new Container();
  c.label = `char:${char}`;

  const color = CHAR_COLOR[char] ?? 0x9a9a9a;
  const W = 420;
  const H = SPRITE_H;

  const g = new Graphics();

  // 剪影：头 + 身（简化为人形）
  const headR = W * 0.16;
  const headY = H * 0.14;
  g.circle(W / 2, headY, headR).fill(color); // 头
  g.ellipse(W / 2, H * 0.58, W * 0.3, H * 0.32).fill(color); // 身
  g.rect(W / 2 - W * 0.3, H * 0.3, W * 0.6, H * 0.28).fill(color); // 肩到腰

  // 描边，让剪影在深色背景上可辨
  g.circle(W / 2, headY, headR).stroke({ width: 4, color: 0xffffff, alpha: 0.25 });
  g.ellipse(W / 2, H * 0.58, W * 0.3, H * 0.32).stroke({ width: 3, color: 0xffffff, alpha: 0.14 });

  c.addChild(g);

  // 名字
  const name = new Text({
    text: char,
    style: {
      fontFamily: 'sans-serif',
      fontSize: 46,
      fill: 0xffffff,
      fontWeight: 'bold',
      stroke: { color: 0x000000, width: 6 },
    },
  });
  name.anchor.set(0.5);
  name.position.set(W / 2, H * 0.16);
  c.addChild(name);

  // 表情 / 姿态提示（截断，避免过长）
  if (pose) {
    const p = pose.length > 14 ? pose.slice(0, 14) + '…' : pose;
    const t = new Text({
      text: `（${p}）`,
      style: { fontFamily: 'sans-serif', fontSize: 26, fill: 0xffffff },
    });
    t.alpha = 0.72;
    t.anchor.set(0.5);
    t.position.set(W / 2, H * 0.26);
    c.addChild(t);
  }

  // 站位标签
  const s = new Text({
    text: `@${slot}`,
    style: { fontFamily: 'sans-serif', fontSize: 20, fill: 0xffffff },
  });
  s.alpha = 0.4;
  s.anchor.set(0.5);
  s.position.set(W / 2, H * 0.94);
  c.addChild(s);

  // 底部锚点：把容器原点设在脚底中心
  c.pivot.set(W / 2, H);
  return c;
}

/**
 * 站位 → 设计坐标 x 比例（0–1，相对舞台宽）。
 * 锚点枚举与 `assets.manifest.json` 的 slot 映射一致。
 */
const SLOT_RATIO: Record<string, number> = {
  // 长椅系
  '长椅左端': 0.3,
  '长椅': 0.42,
  '长椅中央': 0.42,
  '长椅右端': 0.56,
  '长椅右外侧': 0.66,
  '长椅右侧': 0.6,
  // 通用左右
  '左': 0.24,
  '左端': 0.18,
  '右': 0.76,
  '右端': 0.82,
  '画面右侧': 0.8,
  '画面左侧': 0.2,
  '中央': 0.5,
  'center': 0.5,
  // 站位
  '走廊左': 0.26,
  '走廊右': 0.74,
  '站立': 0.5,
  // 看台
  '看台左': 0.24,
  '看台右': 0.76,
  '看台第三排': 0.5,
  // 操场
  '操场边线外': 0.16,
  '操场对面': 0.62,
  '操场': 0.5,
};

/** 站位 → x 比例（0–1） */
export function slotRatio(slot: string): number {
  return SLOT_RATIO[slot] ?? 0.5;
}

/** 该站位是否在中轴以右 —— 是则立绘轴对称（面朝左），中轴及以左保持原画方向（面朝右） */
export function slotOnRight(slot: string): boolean {
  return slotRatio(slot) > 0.5;
}

/** 站位 → 设计坐标 x（像素） */
export function slotToX(slot: string): number {
  return DESIGN_WIDTH * slotRatio(slot);
}

/** 生成 CG 占位：全屏框 + 标题 */
export function makeCgPlaceholder(name: string): Container {
  const c = new Container();
  c.label = `cg:${name}`;
  const W = DESIGN_WIDTH;
  const H = DESIGN_HEIGHT;
  const h = hash(name);

  const g = new Graphics();
  g.rect(0, 0, W, H).fill(hsl(h % 360, 26, 16));
  // 内框
  g.rect(60, 60, W - 120, H - 120).stroke({ width: 6, color: 0xe8a0b4, alpha: 0.5 });
  // ★ 2026-09-18：原有一条「对角装饰线」（white α0.08，左下→右上）已删除 ——
  //   主人反馈「每张 CG 图上都有一条从左上延伸到右下角的彩线」，
  //   而全引擎只有这一处会画斜线（BG 的占位卡没有）。占位卡本身靠内框＋文字即可辨识。
  c.addChild(g);

  const t = new Text({
    text: `CG：${name}`,
    style: { fontFamily: 'sans-serif', fontSize: 64, fill: 0xffffff, letterSpacing: 6 },
  });
  t.anchor.set(0.5);
  t.position.set(W / 2, H / 2 - 20);
  c.addChild(t);

  const sub = new Text({
    text: '[占位 · 待美术]',
    style: { fontFamily: 'sans-serif', fontSize: 26, fill: 0xe8a0b4 },
  });
  sub.alpha = 0.7;
  sub.anchor.set(0.5);
  sub.position.set(W / 2, H / 2 + 48);
  c.addChild(sub);

  return c;
}
