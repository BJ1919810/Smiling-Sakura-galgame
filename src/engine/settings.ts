/**
 * 游戏设置 —— 持久化 + 应用
 *
 * textSpeed：打字机速度（字/秒，默认 45；30 慢 / 45 标准 / 80 快）
 * autoPlay：自动播放（完整显示后 autoDelayMs 自动推进）
 * sceneToast：场景切换时弹「■ SCENE_xx 地点」提示条（玩家可关，默认开）
 * textScale：正文字号倍率（1 = 标准）★ 2026-09-18 加（主人：菜单要能调字体大小）
 *            App.tsx 把它写进根节点的 CSS 变量 `--text-scale`，
 *            由 global.css 的 `.text-box p` / `.name-box` 消费 —— **只改字号，不动配色与排版**。
 * uiScale：**整体 UI 缩放倍率**（2026-09-20 加，默认 1 ＝ 纯自动）
 *            乘在「按舞台高度自动算的因子」之上（见 Stage.applyFit / global.css 的 .screen）。
 *            主人两轮相反反馈（纯 px 手机偏大 / 等比缩放手机偏小）后，
 *            把最终定档权交给玩家 —— 真机上一拖就好，不必反复出包。
 *
 * ★ 2026-09-18：BGM 默认音量 0.6 → 0.4（主人定）。
 * ★ 2026-09-19：**SE 默认音量 0.7 → 0.4**（主人令「bgm se 默认音量设为 40」）。
 *   至此三轨默认档位＝ BGM 40 / SE 40 / 语音 80。
 *   ⚠️ 只改 `DEFAULT_SETTINGS` 是**不够**的：老玩家的 localStorage 里存着旧值，
 *   `{...DEFAULT, ...saved}` 合并后仍是被存下来的旧值 ⇒ 新默认对老档完全不生效。
 *   故加 `v`（设置结构版本）做一次性迁移 —— 见 `loadSettings()`。
 *   ★★ 规矩：以后凡是改**默认值**，必须同时 `SETTINGS_VERSION += 1`。
 * ★ 2026-09-19（第 4 版）：三轨 30 / 30 / 80；字号默认取原「大」档（1.12）。
 */

import { storage } from '../platform/StorageAdapter.ts';

export interface GameSettings {
  bgmVolume: number;
  seVolume: number;
  /** ★ 语音音量（2026-09-18 加）—— 日配语音轨；与 BGM 同档起步 */
  voiceVolume: number;
  textSpeed: number;
  autoPlay: boolean;
  autoDelayMs: number;
  sceneToast: boolean;
  textScale: number;
  /** ★ 2026-09-20：整体 UI 缩放倍率（乘在自动因子之上；1 ＝ 纯自动） */
  uiScale: number;
  /** 设置结构版本（`saveSettings` 写入；UI 不显示，只用于迁移判断） */
  v?: number;
}

export const DEFAULT_SETTINGS: GameSettings = {
  // ★ 2026-09-19（主人令）：三轨默认 30 / 30 / 80。
  bgmVolume: 0.3,
  seVolume: 0.3,
  voiceVolume: 0.8,
  textSpeed: 45,
  autoPlay: false,
  autoDelayMs: 1600,
  sceneToast: true,
  // ★ 2026-09-19（主人令）：字号默认取原「大」档（1.12）—— 原来的字太小了。
  textScale: 1.12,
  // ★ 2026-09-20：1 ＝ 完全交给「按舞台高度自动适配」，玩家不动它。
  uiScale: 1,
};

/** 整体 UI 缩放倍率的可调范围（设置面板滑杆） */
export const UI_SCALE_MIN = 0.6;
export const UI_SCALE_MAX = 1.4;

/** 当前设置结构版本。★ 改默认值时必须 +1，否则老档不会跟着动 */
export const SETTINGS_VERSION = 5;

/** 语音音量档位（设置面板用） */
export const VOICE_VOLUME_STEPS: { label: string; v: number }[] = [
  { label: '关', v: 0 },
  { label: '小', v: 0.4 },
  { label: '中', v: 0.8 },
  { label: '大', v: 1 },
];

/** 正文字号档位（设置面板用；值＝倍率） */
export const TEXT_SCALE_STEPS: { label: string; v: number }[] = [
  { label: '小', v: 0.9 },
  { label: '标准', v: 1 },
  { label: '大', v: 1.12 },
  { label: '特大', v: 1.25 },
];

const KEY = 'settings';

export function loadSettings(): GameSettings {
  const raw = storage.get(KEY);
  if (!raw) return { ...DEFAULT_SETTINGS, v: SETTINGS_VERSION };
  try {
    const saved = JSON.parse(raw) as Partial<GameSettings>;
    const s: GameSettings = { ...DEFAULT_SETTINGS, ...saved };
    // ——— 默认值迁移 ———
    // ⚠️ 必须按**来源版本**逐级判断，⛔ 不能写成 `(saved.v ?? 1) < SETTINGS_VERSION`：
    //    那样每加一版迁移都会把**所有**改过默认值的项重刷一遍，
    //    把玩家在中间版本里手动调过的音量冲掉（例：v2 玩家把 BGM 调到 0.8，
    //    升 v3 时被旧写法刷回 0.4）。
    const from = saved.v ?? 1;
    if (from < 2) s.bgmVolume = DEFAULT_SETTINGS.bgmVolume; // v1 → v2：BGM 0.6 → 0.4
    if (from < 3) s.seVolume = DEFAULT_SETTINGS.seVolume; // v2 → v3：SE 0.7 → 0.4
    if (from < 4) {
      // v3 → v4：BGM/SE 0.4 → 0.3；字号默认取原「大」档（主人令「原来的字太小了」）
      s.bgmVolume = DEFAULT_SETTINGS.bgmVolume;
      s.seVolume = DEFAULT_SETTINGS.seVolume;
      s.textScale = DEFAULT_SETTINGS.textScale;
    }
    if (from < 5) s.uiScale = DEFAULT_SETTINGS.uiScale; // v4 → v5：新增「界面大小」，默认交给自动
    s.v = SETTINGS_VERSION;
    return s;
  } catch {
    return { ...DEFAULT_SETTINGS, v: SETTINGS_VERSION };
  }
}

export function saveSettings(s: GameSettings): void {
  storage.set(KEY, JSON.stringify({ ...s, v: SETTINGS_VERSION }));
}
