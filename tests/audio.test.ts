/**
 * AudioEngine · BGM 切换回归测试（2026-09-18）
 *
 * 背景（主人实机报障）：「切换太快还是什么原因，bgm 不会及时切换，
 * UI 显示的是现在的 bgm，但响的还是上一个 bgm」。
 *
 * 根因是旧实现的三处洞：
 *   ① fade 是「一次性 400ms 时钟」，**不可取消**，且只跟踪**最新一个**元素
 *      ⇒ 淡出窗口内再切歌，同一个元素上叠两条 ramp，旧元素可能带着非零音量继续响；
 *   ② `playBgm` 先改 label 再尝试播放，遇到 `failed` 记忆里的键直接 return；
 *   ③ `unlock()` 每次点击都 new 一个 Audio。
 *
 * 这个测试把引擎跑在假的 `Audio` / `window` 上，断言**连续快切之后只能有一首在响**
 * —— 这正是修好与没修好的分界。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import manifestJson from '../src/generated/assets.manifest.json';
import { AudioEngine } from '../src/audio/AudioEngine.ts';

const BGM: Record<string, { file: string }> = (manifestJson as unknown as {
  bgm: Record<string, { file: string }>;
}).bgm;

/** 假 Audio：只实现引擎用到的那几个成员，`play()` 同步把 paused 置 false（照规格） */
class FakeAudio {
  static all: FakeAudio[] = [];
  src: string;
  volume = 1;
  paused = true;
  loop = false;
  preload = '';
  currentTime = 0;
  dataset: Record<string, string> = {};

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
  addEventListener(): void {
    /* 测试里不触发 error 事件 */
  }
  /** 此刻在播的 BGM 元素（排除 unlock 用的静音元素） */
  static playingBgm(): FakeAudio[] {
    return FakeAudio.all.filter((a) => !a.paused && a.src.includes('/bgm/'));
  }
  static reset(): void {
    FakeAudio.all = [];
  }
}

/** manifest 里某个 BGM key 对应的文件名（别写死编号 —— 编号会漂） */
function fileOf(key: string): string {
  return BGM[key]!.file.split('/').pop()!;
}

describe('AudioEngine · BGM 切换', () => {
  let audio: AudioEngine;

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

  it('连续快切（A→B→C，各隔 10ms）之后，只剩最后一首在响', async () => {
    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(10);
    audio.playBgm('悬');
    await vi.advanceTimersByTimeAsync(10);
    audio.playBgm('日常');
    await vi.advanceTimersByTimeAsync(1000); // 跑完所有淡出（400ms）

    const playing = FakeAudio.playingBgm();
    expect(playing).toHaveLength(1);
    expect(playing[0]!.src).toContain(fileOf('日常'));
    expect(playing[0]!.volume).toBeCloseTo(audio.bgmVolume, 5);
    expect(audio.playingCount).toBe(1);
  });

  it('切歌后旧曲必须彻底停掉（不是「淡到很小」）', async () => {
    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(500);
    const first = FakeAudio.playingBgm()[0]!;
    expect(first.volume).toBeCloseTo(audio.bgmVolume, 5);

    audio.playBgm('悬');
    await vi.advanceTimersByTimeAsync(1000);

    expect(first.paused).toBe(true);
    expect(first.volume).toBe(0);
  });

  it('「无」＝全部停掉', async () => {
    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(500);
    expect(audio.playingCount).toBe(1);

    audio.playBgm('无');
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeAudio.playingBgm()).toHaveLength(0);
    expect(audio.playingCount).toBe(0);
    expect(audio.currentName).toBe('—');
  });

  it('同曲重复点不重播（元素实例不增加）', async () => {
    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(500);
    const n = FakeAudio.all.length;

    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(500);
    expect(FakeAudio.all.length).toBe(n);
    expect(audio.playingCount).toBe(1);
  });

  it('键不在 manifest 里 → 不留上一首在响，也不抛异常', async () => {
    audio.playBgm('静');
    await vi.advanceTimersByTimeAsync(500);

    expect(() => audio.playBgm('根本没有这首')).not.toThrow();
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeAudio.playingBgm()).toHaveLength(0);
  });

  it('连点 6 次也只留最后的在响（旧实现会叠出多轨）', async () => {
    const keys = ['静', '悬', '日常', '晚风', '静・樱', '樱_主题'];
    for (const k of keys) {
      audio.playBgm(k);
      await vi.advanceTimersByTimeAsync(5); // 远小于 400ms 淡出窗口
    }
    await vi.advanceTimersByTimeAsync(2000);

    const playing = FakeAudio.playingBgm();
    expect(playing).toHaveLength(1);
    expect(playing[0]!.src).toContain(fileOf('樱_主题'));
  });
});
