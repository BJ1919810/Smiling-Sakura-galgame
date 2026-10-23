/**
 * AudioEngine · `voicePending` 回归测试（2026-09-19）
 *
 * 背景（主人报障）：「自动播放时，有语音要跟随语音长度，你没跟语音」。
 *
 * 根因：`Director.tick()` 的自动播放延迟**只按字数算**（`1600 + 字×120`），
 * 一个字都没问过音频层 ⇒ 语音还在念，字已打完、计时到点就跳走，语音被下一句掐断。
 *
 * 修复需要一个「本条语音尚未播完」的信号给 Director 用。这个测试盯的就是它：
 *   · 为什么**不能**用实时的 `paused`：`play()` 的状态变化是异步的，
 *     刚 `new Audio()` 时 `paused` 仍是 `true` ⇒ 那一帧会被误判成「没语音」。
 *   · 所以用**意图级**标记：发起即 true，ended / error / stopVoice 才落回 false。
 *
 * 另外两条硬要求（关系到自动播放会不会**永久卡住**）：
 *   · 无配音的行（旁白 / 未录音角色）必须恒为 false ⇒ 自动播放正常回落到按字数；
 *   · 加载出错必须落回 false ⇒ 不会干等一条永远不响的语音。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import manifestJson from '../src/generated/assets.manifest.json';
import { AudioEngine } from '../src/audio/AudioEngine.ts';

const VOICE: Record<string, string> =
  (manifestJson as unknown as { voice?: Record<string, string> }).voice ?? {};

/** 假 Audio：能手动触发 ended / error（真元素由浏览器触发，测试里自己来） */
class FakeAudio {
  static all: FakeAudio[] = [];
  src: string;
  volume = 1;
  paused = true;
  loop = false;
  preload = '';
  currentTime = 0;
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
  /** 手动派发事件（模拟自然播完 / 加载失败） */
  emit(type: string): void {
    for (const fn of this.listeners.get(type) ?? []) fn();
  }
  /** 本测试关心的语音元素（排除 BGM / SE / unlock 用的静音元素） */
  static voiceEls(): FakeAudio[] {
    return FakeAudio.all.filter((a) => a.src.includes('/voice/'));
  }
  static reset(): void {
    FakeAudio.all = [];
  }
}

describe('AudioEngine · voicePending（自动播放的等待依据）', () => {
  let audio: AudioEngine;
  /** 随便挑一条**真的有配音**的台本行（别写死行号 —— 台本会改） */
  const line = Number(Object.keys(VOICE)[0]);

  beforeEach(() => {
    FakeAudio.reset();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance'] });
    vi.stubGlobal('Audio', FakeAudio);
    vi.stubGlobal('window', globalThis);
    audio = new AudioEngine();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('前置：manifest 里有配音行，且挑到的是合法行号', () => {
    expect(Object.keys(VOICE).length).toBeGreaterThan(0);
    expect(Number.isInteger(line)).toBe(true);
    expect(line).toBeGreaterThan(0);
  });

  it('发起语音 ⇒ true；自然播完（ended）⇒ false', () => {
    expect(audio.voicePending).toBe(false);

    audio.playVoice(line);
    expect(audio.voicePending).toBe(true);

    FakeAudio.voiceEls().at(-1)!.emit('ended');
    expect(audio.voicePending).toBe(false);
  });

  // ★ 这条是「自动播放不会卡死」的保证
  it('无配音的行（旁白 / 未录音角色）⇒ 恒为 false', () => {
    audio.playVoice(999_999);
    expect(audio.voicePending).toBe(false);
    expect(FakeAudio.voiceEls()).toHaveLength(0);
  });

  it('玩家掐断（stopVoice）⇒ false', () => {
    audio.playVoice(line);
    expect(audio.voicePending).toBe(true);

    audio.stopVoice();
    expect(audio.voicePending).toBe(false);
  });

  it('加载出错（error）⇒ false，不会让自动播放干等', () => {
    audio.playVoice(line);
    expect(audio.voicePending).toBe(true);

    FakeAudio.voiceEls().at(-1)!.emit('error');
    expect(audio.voicePending).toBe(false);
  });

  // ★ 这条验证「旧元素的事件不会误清掉新语音的等待」
  it('新语音顶掉旧语音：旧元素迟到的 ended 不得清掉新语音的 pending', () => {
    audio.playVoice(line);
    const first = FakeAudio.voiceEls().at(-1)!;
    audio.playVoice(line); // 顶掉
    expect(audio.voicePending).toBe(true);

    first.emit('ended'); // 旧元素迟到的事件
    expect(audio.voicePending).toBe(true); // 仍在等新那条
  });

  it('播放被浏览器拦截（play 抛错）⇒ false', async () => {
    class Rejecting extends FakeAudio {
      play(): Promise<void> {
        return Promise.reject(new Error('NotAllowedError'));
      }
    }
    vi.stubGlobal('Audio', Rejecting);
    const a2 = new AudioEngine();
    a2.playVoice(line);
    expect(a2.voicePending).toBe(true); // 先立起意图
    await Promise.resolve();
    await Promise.resolve();
    expect(a2.voicePending).toBe(false); // 被拦后落回
  });
});
