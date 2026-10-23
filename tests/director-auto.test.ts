/**
 * Director · 自动播放节奏回归测试（2026-09-19）
 *
 * 主人报障：「自动在无语音时跟随字长；但是有语音时，要跟随语音长度，**你没跟语音**」。
 *
 * 旧实现 tick() 里只有一条路：`delay = 1600 + 字数×120`，与音频层完全无关
 * ⇒ 语音还在念，计时到点就跳句，语音被下一句当场掐断。
 *
 * 这里用**手动喂时间**的方式驱动 tick（不使用 fake timers），精确断言两套节奏：
 *   · 有配音的行 → 等语音播完（+ 呼吸），**字数公式早就到期也不能走**
 *   · 无配音的行 → 按字数（且不早不晚）
 *   · 语音加载失败 / 超时 → 必须能走下去（⛔ 否则自动播放永久卡死）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import manifestJson from '../src/generated/assets.manifest.json';
import { Director } from '../src/director/Director.ts';
import { DEFAULT_SETTINGS } from '../src/engine/settings.ts';
import type { Op, ScriptProgram } from '../src/engine/script/ops.ts';

const VOICE: Record<string, string> =
  (manifestJson as unknown as { voice?: Record<string, string> }).voice ?? {};
/** 一条**真的有配音**的台本行 */
const VOICE_LINE = Number(Object.keys(VOICE)[0]);
/** 一条**一定没有配音**的行号（远大于台本行数） */
const PLAIN_LINE = 9_000_001;

/** 语音播完后的呼吸间隔（与 Director 里的 VOICE_TAIL_MS 对齐） */
const TAIL = 350;
/** 拿不到语音时长时的保底上限（对齐 Director 的 VOICE_WAIT_FALLBACK_MS） */
const FALLBACK = 45_000;
/** 拿到时长后额外给的余量（对齐 Director 的 VOICE_EXTRA_MS） */
const EXTRA = 8_000;

class FakeAudio {
  static all: FakeAudio[] = [];
  src: string;
  volume = 1;
  paused = true;
  loop = false;
  preload = '';
  currentTime = 0;
  /** 真实 Audio 也是只读；测试里手动赋值模拟「元数据已就绪」 */
  duration = NaN;
  dataset: Record<string, string> = {};
  private listeners = new Map<string, Array<() => void>>();

  constructor(src?: string) {
    this.src = src ?? '';
    FakeAudio.all.push(this);
  }
  play(): Promise<void> {
    this.paused = false;
    return Promise.resolve();
  }
  pause(): void {
    this.paused = true;
  }
  addEventListener(type: string, fn: () => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(fn);
    this.listeners.set(type, list);
  }
  emit(type: string): void {
    for (const fn of this.listeners.get(type) ?? []) fn();
  }
  static lastVoice(): FakeAudio | undefined {
    return FakeAudio.all.filter((a) => a.src.includes('/voice/')).at(-1);
  }
  static reset(): void {
    FakeAudio.all = [];
  }
}

/** 造一个「一条 say + end」的最小程序 */
function makeProgram(text: string, line: number): ScriptProgram {
  const say = { op: 'say', who: '樱', kind: 'speech', blocks: [{ text }], line };
  return {
    version: 1,
    generatedAt: '2026-09-19T00:00:00.000Z',
    source: 'test',
    chapters: [],
    ops: [say, { op: 'end' }] as unknown as Op[],
  };
}

describe('Director · 自动播放节奏', () => {
  let d: Director;
  /** 足够大的假「当前时刻」，保证打字机一帧内走完 */
  const T0 = 1_000_000_000;

  beforeEach(() => {
    FakeAudio.reset();
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('window', globalThis);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** 起播并让打字机完成，返回打字结束后的「此刻」 */
  function startDone(text: string, line: number): number {
    d = new Director(makeProgram(text, line), {
      ...DEFAULT_SETTINGS,
      autoPlay: true,
      textSpeed: 60,
    });
    d.start();
    d.tick(T0); // 一帧内打完（dt 极大）
    expect(d.isTyping).toBe(false);
    return T0;
  }

  it('前置：能挑到有配音的行、且台本确实有配音表', () => {
    expect(VOICE_LINE).toBeGreaterThan(0);
    expect(Number.isInteger(VOICE_LINE)).toBe(true);
  });

  // ★★ 这条就是主人报的 bug 的回归测试
  it('★ 有配音：字数公式早已到期也不推进，必须等语音播完', () => {
    // 3 个字 → 字数公式只要 1600 + 360 = 1960ms
    const t = startDone('あいう', VOICE_LINE);

    // 5 秒后（远超字数公式）语音还没念完 ⇒ 必须原地不动
    d.tick(t + 5_000);
    expect(d.finished).toBe(false);

    // 语音自然播完
    FakeAudio.lastVoice()!.emit('ended');

    // 播完后还要留一个呼吸间隔，不能立刻跳
    d.tick(t + 5_100);
    expect(d.finished).toBe(false);

    // 呼吸走完 → 推进（第二条是 end ⇒ finished）
    d.tick(t + 5_100 + TAIL + 1);
    expect(d.finished).toBe(true);
  });

  it('无配音：按字数推进（早一刻不走，晚一刻走）', () => {
    const text = 'あいうえお'; // 5 字
    const t = startDone(text, PLAIN_LINE);
    const chars = d.dialog!.fullText.length;
    const delay = Math.min(8000, 1600 + chars * 120);

    d.tick(t + delay - 1);
    expect(d.finished).toBe(false);

    d.tick(t + delay + 1);
    expect(d.finished).toBe(true);
  });

  it('语音加载失败（error）⇒ 不卡死，照常推进', () => {
    const t = startDone('あいう', VOICE_LINE);
    d.tick(t + 5_000);
    expect(d.finished).toBe(false);

    FakeAudio.lastVoice()!.emit('error'); // 加载失败

    d.tick(t + 5_100); // 下一帧发现 pending 已落回 → 排呼吸计时
    expect(d.finished).toBe(false);
    d.tick(t + 5_100 + TAIL + 1);
    expect(d.finished).toBe(true);
  });

  it('★ 语音永远不结束、且拿不到时长 ⇒ 保底超时必须能走下去', () => {
    const t = startDone('あいう', VOICE_LINE); // FakeAudio.duration 默认 NaN
    expect(d.finished).toBe(false);

    // 语音一直「在播」，从不 ended
    d.tick(t + FALLBACK - 1_000);
    expect(d.finished).toBe(false); // 还没到上限，继续等

    d.tick(t + FALLBACK + 100); // 越过上限 → 强制排呼吸计时
    expect(d.finished).toBe(false);
    d.tick(t + FALLBACK + 100 + TAIL + 1);
    expect(d.finished).toBe(true);
  });

  // ★★ 这条是「长台词不能被写死的上限切断」的回归测试
  it('★ 长语音（25s）不得被超时兜底误切 —— 上限必须按真实时长算', () => {
    const t = startDone('あいう', VOICE_LINE);
    const el = FakeAudio.lastVoice()!;
    el.duration = 25; // 本项目实测最长台词 25.1s（tools/_probe_voice_silence.py）

    // 20 秒时仍在等 —— 若上限是写死的 15s，这里早就被切走了
    d.tick(t + 20_000);
    expect(d.finished).toBe(false);

    // 27 秒仍在等（上限＝25s + 余量 8s＝33s），因为语音还没 ended
    d.tick(t + 27_000);
    expect(d.finished).toBe(false);

    // 越过「时长 + 余量」⇒ 即使没 ended 也放行（防永久卡住）
    d.tick(t + 25_000 + EXTRA + 100);
    expect(d.finished).toBe(false);
    d.tick(t + 25_000 + EXTRA + 100 + TAIL + 1);
    expect(d.finished).toBe(true);
  });
});
