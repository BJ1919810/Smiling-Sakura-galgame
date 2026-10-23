/**
 * 时段/天气滤镜 —— 由 BG 键名后缀推导画面调色
 *
 * ★ 为什么需要（见 `AssetLoader.ts` 的 `LOCATION_FALLBACK` 注释）：
 *   同一地点的多个 BG 标识**共用一张中性底** —— 「教室_早晨」「教室_午后斜阳」
 *   「教室_空旷_傍晚」在 manifest 里都指向 `bg_001`。时段/天气的差异**不在素材里**，
 *   由本模块在渲染时补上。这是当初「同地点共用中性底」那个决定的另一半。
 *
 * ★ 只作用于 BG，**不作用于 CG**：
 *   CG 是特写画面，光线在出图时就定死了；再叠一层滤镜等于改掉美术的意图。
 *
 * ★ 基准 = 「午后」：
 *   bg_001 的母带文件名就是 `教室_午后_2560x1440.png`，其余底图同批同光。
 *   所以「午后 / 午间 / 正午 / 午休」一律 **identity（不挂滤镜）** —— 挂一个恒等
 *   矩阵只会白白多一次全屏 draw call。
 *
 * ══════════════════════════════════════════════════════════════════════
 * ⚠️⚠️ PixiJS ColorMatrixFilter 的取值语义**反直觉**，改动前务必看清 ⚠️⚠️
 *   本模块的配方用「人话」写（1 = 不变），在 `buildTimeFilter()` 里换算成 PixiJS 的
 *   「机器话」。换算关系来自 `node_modules/pixi.js/.../ColorMatrixFilter.mjs` 源码：
 *
 *     brightness(b)        ：对角线乘 b           ⇒ 1 = 不变   ✅ 直觉一致
 *     contrast(amount)     ：`v = amount + 1`      ⇒ **0 = 不变**（不是 0.5！）
 *                             d.ts 的注释写「0.5 represents normal」是**错的**，
 *                             以源码为准。换算：amount = 倍率 - 1
 *     saturate(amount)     ：`x = amount*2/3 + 1`  ⇒ **0 = 不变**
 *                             换算：amount = (倍率 - 1) * 1.5
 *     tint(color)          ：取 `Color.toArray()` ⇒ 分量是 **0~1 归一化**
 *                             ⇒ ⛔ **只能压暗通道、不能提亮**（>1 会被截断）
 *                             所以「暖」要写成「压蓝」而不是「提红」。
 * ══════════════════════════════════════════════════════════════════════
 */

import { ColorMatrixFilter } from 'pixi.js';

/** 一个时段的调色配方（「人话」口径：1 = 不变） */
export interface TintRecipe {
  /** 亮度倍数，1 = 不变 */
  brightness: number;
  /** 对比度倍数，1 = 不变 */
  contrast: number;
  /** 饱和度倍数，1 = 不变 / <1 更灰 / >1 更艳 */
  saturate: number;
  /** 乘法染色 [r, g, b]，各通道 **≤1**（1 = 不变），省略 = 不染 */
  tint?: [number, number, number];
}

/**
 * 时段/天气配方表。
 * ⚠️ 数组顺序即**匹配优先级**，必须**先长后短** ——
 *    「午后斜阳」排在「午后」前面，否则「斜阳」永远轮不到被匹配。
 */
export const TINT_RECIPES: [string, TintRecipe][] = [
  // —— 清晨 / 早晨：清冷通透（提亮、微降饱和、压红偏青）——
  ['清晨', { brightness: 1.07, contrast: 0.98, saturate: 0.93, tint: [0.94, 0.99, 1.0] }],
  ['早晨', { brightness: 1.04, contrast: 1.0, saturate: 0.97, tint: [0.97, 0.99, 1.0] }],

  // —— 基准：午后 / 午间 / 正午 / 午休 ——
  ['午后斜阳', { brightness: 1.03, contrast: 1.04, saturate: 1.06, tint: [1.0, 0.94, 0.86] }],
  ['午后', { brightness: 1.0, contrast: 1.0, saturate: 1.0 }],
  ['午间', { brightness: 1.0, contrast: 1.0, saturate: 1.0 }],
  ['正午', { brightness: 1.0, contrast: 1.0, saturate: 1.0 }],
  ['午休', { brightness: 1.0, contrast: 1.0, saturate: 1.0 }],

  // —— 日落段：压蓝偏暖、提饱和、压暗 ——
  ['夕阳', { brightness: 1.0, contrast: 1.08, saturate: 1.14, tint: [1.0, 0.88, 0.74] }],
  ['黄昏', { brightness: 0.94, contrast: 1.06, saturate: 1.04, tint: [1.0, 0.86, 0.80] }],
  ['傍晚', { brightness: 0.97, contrast: 1.04, saturate: 1.0, tint: [1.0, 0.91, 0.85] }],

  // —— 暮色 / 夜：显著压暗 ＋ 压红绿偏蓝 ——
  ['暮色', { brightness: 0.80, contrast: 0.98, saturate: 0.88, tint: [0.90, 0.93, 1.0] }],
  ['夜', { brightness: 0.68, contrast: 0.94, saturate: 0.80, tint: [0.82, 0.88, 1.0] }],

  // —— 天气 ——
  ['阴天', { brightness: 0.96, contrast: 0.92, saturate: 0.76, tint: [0.98, 1.0, 1.0] }],
  ['晴天', { brightness: 1.05, contrast: 1.04, saturate: 1.08, tint: [1.0, 1.0, 0.97] }],
  ['大风', { brightness: 1.02, contrast: 1.02, saturate: 1.04 }],
];

/**
 * 从 BG 键名解析配方。匹配不到 → `null`（＝不加滤镜）。
 * ⚠️ 匹配顺序即 `TINT_RECIPES` 的数组顺序，见上。
 */
export function recipeFor(bgKey: string): TintRecipe | null {
  for (const [word, recipe] of TINT_RECIPES) {
    if (bgKey.includes(word)) return recipe;
  }
  return null;
}

/** 配方是否等价于「不变」（是则不必挂滤镜，省一次全屏 pass） */
export function isIdentity(r: TintRecipe): boolean {
  return r.brightness === 1 && r.contrast === 1 && r.saturate === 1 && !r.tint;
}

/** [0~1 三通道] → 0xRRGGBB（供 `Color.toArray()` 还原成同样的系数） */
function packTint([r, g, b]: [number, number, number]): number {
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
  return (c(r) << 16) | (c(g) << 8) | c(b);
}

/**
 * 为某个 BG 键构造滤镜；基准时段返回 `null`（调用方据此跳过挂载）。
 *
 * ⚠️ `brightness/contrast/saturate/tint` 都是**累积**作用于同一条矩阵的，
 *    所以每次都要 **new 一个新实例**，不能复用单例 ——
 *    复用会让滤镜随场景切换越叠越重。
 */
export function buildTimeFilter(bgKey: string): ColorMatrixFilter | null {
  const r = recipeFor(bgKey);
  if (!r || isIdentity(r)) return null;

  const f = new ColorMatrixFilter();
  f.brightness(r.brightness, true);
  f.contrast(r.contrast - 1, true); // v = amount + 1 ⇒ amount = 倍率 - 1
  f.saturate((r.saturate - 1) * 1.5, true); // x = amount*2/3 + 1 ⇒ amount = (x-1)*1.5
  if (r.tint) f.tint(packTint(r.tint), true);
  return f;
}
