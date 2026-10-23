/**
 * 音频引擎（技术设计.md §11 · Web Audio 三分轨的务实版）
 *
 * 设计决策：
 * - BGM/SE 都用 HTMLAudioElement —— loop / currentTime / volume 原生支持，
 *   单 BGM 场景不需要 Web Audio 混音图
 * - **Voice（日配语音）**：★ 2026-09-18 落地。与 SE 同为「播完即止」，但有三点不同：
 *   ① **单声道独占** —— 同一时刻只允许一条语音；新语音起，旧语音**立刻停**
 *      （不能淡出：两句话叠着响会听不清，且台词是一条一条推进的）；
 *   ② **可被打断** —— 玩家点「继续」跳过这句时，`stopVoice()` 立即掐断；
 *   ③ **引擎侧不参与节流** —— 语音长度天然决定「这句读多久」，不去猜时间。
 * - **无素材静默降级**：与 AssetLoader 一致 —— 文件 404 只试一次，游戏零卡顿
 * - BGM 交叉淡入淡出（400ms），循环标记来自 assets.manifest.json 的 loop 字段
 *   （唯一 loop:false = 樱_主题（完整），章末收束专用；「无」= 停止）
 * - 音量由 SettingsPanel 写入、StorageAdapter 持久化
 *
 * ★ 2026-09-18 重写 BGM 切换路径（修主人报的「UI 显示新曲、响的还是上一首」）
 *   旧实现有三个洞：
 *   ① `fadeTo()` 是「一次性 400ms 时钟」—— **新元素还没开始发声就先跑淡入**，
 *      且**没有取消机制**；淡出窗口（400ms）内再切歌，同一个元素上会同时跑
 *      两条音量 ramp，而代码只跟踪「最新一个」元素（`bgmEl`），更早的元素
 *      仅靠各自那次淡出的回调停掉 ⇒ 一旦某次 ramp 被顶掉/提前返回，
 *      旧元素就带着非零音量继续响。
 *   ② `playBgm()` **先改 label 再尝试播放**；遇到 `failed` 记忆里的键直接 return
 *      ⇒ UI 变了、声音不动，而且永不重试。
 *   ③ `unlock()` 每次点击都 `new Audio()` ⇒ 泄漏。
 *   现在：
 *   · **所有**元素登记在 `tracks` 表；切歌时先把「非当前」的**全部**淡出停掉；
 *   · 每个元素的 fade 带**代号 `gen`**，新 fade 一开、在跑的旧 tick 立刻自杀；
 *   · 淡入**只在 `play()` 成功之后**开始（元素真的在发声了才调音量）；
 *   · 同向 fade 已在跑就不重启（避免连点把淡出拖成无限长）；
 *   · 因自动播放策略没播成的键**不**记入 `failed`，下次手势由 `unlock()` 重试。
 */

import manifestJson from '../generated/assets.manifest.json';
import { DEFAULT_SETTINGS } from '../engine/settings.ts';

interface BgmEntry {
  file: string;
  loop?: boolean;
  note?: string;
}

const manifest = manifestJson as unknown as {
  bgm: Record<string, BgmEntry & { count: number }>;
  /**
   * ★ 日配语音表（2026-09-18）—— `"台本行号" → "voice/v_00409.ogg"`。
   *
   * 与其它资源不同：**不由台本 op 引用**，而是「台词行 → 音频」的外挂映射，
   * 由 `tools/compiler/manifest.ts` 扫 `public/assets/audio/voice/` 生成。
   * 查不到＝该行没有配音 ⇒ **静默跳过**（旁白、未录音的角色都走这条路）。
   */
  voice?: Record<string, string>;
};

const FADE_MS = 400;
const FADE_STEP = 40;
/**
 * 环境型 SE 的淡出时长（ms）—— 比 BGM 的 400 ms 略长。
 * 环境底噪是「一整片声场」，硬切会有明显断口；600 ms 与场景黑场转场（350 ms）错开，听感更自然。
 */
const ENV_FADE_MS = 600;

/** 播放中的一条 BGM 轨（同名曲目复用同一个元素 —— 省掉重复解码，切歌更跟手） */
interface Track {
  el: HTMLAudioElement;
  /** fade 代号：每次新 fade 递增；在跑的旧 tick 见到代号不符即自杀 */
  gen: number;
  timer: number | null;
  /** 当前/最近一次 fade 的目标音量（用于「同向 fade 已在跑就别重启」） */
  target: number | null;
}

export class AudioEngine {
  /** 当前 BGM key（label 显示用，含「无」） */
  currentName = '—';

  private tracks = new Map<string, Track>();
  private currentKey: string | null = null;
  private failed = new Set<string>();
  private unlockEl: HTMLAudioElement | null = null;
  private sePool: HTMLAudioElement[] = [];
  /** 当前正在播放的**环境型** SE（同一时刻最多一个）；场景切换时由 stopEnvSe 停掉 */
  private envSe: HTMLAudioElement | null = null;
  /** 环境 SE 淡出的代号：每次 stopEnvSe / 新环境 SE 都 +1，让在跑的旧淡出立刻自杀（同 fadeTo 的防重入思路） */
  private envFadeGen = 0;

  // ————————————————————————————————————————————————————————————
  // 语音（Voice）
  // ————————————————————————————————————————————————————————————
  /** 当前正在播的语音（**同一时刻最多一条**）；停掉用 stopVoice() */
  private voiceEl: HTMLAudioElement | null = null;
  /** 语音播放代号：playVoice / stopVoice 都 +1，让旧元素的 async 回调认得出自己已过期 */
  private voiceGen = 0;
  /** 语音文件已知 404 的台本行号（只试一次，不反复打网络） */
  private voiceFailed = new Set<number>();
  /**
   * ★ 本条语音「尚未播完」—— **意图级**标记（2026-09-19 加）。
   *
   * 用途：`Director` 的自动播放要「有语音就跟语音长度」，判断依据就是它。
   *
   * ⚠️ 为什么**不能**直接用 `voicePlaying`（实时 `paused` 判据）：
   *   ① `play()` 是**异步**的 —— `new Audio()` 之后立刻查 `paused` 多半仍是 `true`
   *      （还要等元数据/解码），自动播放那一帧会误判成「这句没语音」而按字数推进，
   *      语音当场被下一句掐断 ⇒ 这正是主人报的「有语音时没跟语音」。
   *   ② 「加载中 / 将要播」与「真的在响」在编排层是同一件事（都该等），
   *      但对 `paused` 来说不是 —— 所以这里用**意图**而非**实时状态**。
   * 本标记：语音被成功发起 → `true`；`ended` / `error` / 被 `stopVoice()` 掐断 → `false`。
   */
  private voiceActive = false;

  /**
   * 音量（0–1），SettingsPanel 写入。
   * ★ 默认值统一取自 `engine/settings.ts` —— 以前这里各写一份字面量，
   *   改默认值时极易漏掉一处（本次把 BGM 0.6 → 0.4 时就差点踩到）。
   */
  bgmVolume = DEFAULT_SETTINGS.bgmVolume;
  seVolume = DEFAULT_SETTINGS.seVolume;
  /** ★ 语音音量与 BGM 同档（语音是「主线内容」，不该比 BGM 还轻） */
  voiceVolume = DEFAULT_SETTINGS.voiceVolume;

  setVolumes(bgm: number, se: number, voice?: number): void {
    this.bgmVolume = bgm;
    this.seVolume = se;
    if (typeof voice === 'number') {
      this.voiceVolume = voice;
      // 正在播的语音立刻跟随（玩家调音量要即时听得到）
      if (this.voiceEl) this.voiceEl.volume = voice;
    }
    const t = this.currentKey !== null ? this.tracks.get(this.currentKey) : undefined;
    if (t && !t.el.paused) t.el.volume = bgm;
  }

  /** 诊断用：此刻**真的在播**的 BGM 元素个数（正确实现下应恒 ≤ 1） */
  get playingCount(): number {
    let n = 0;
    for (const t of this.tracks.values()) if (!t.el.paused) n += 1;
    return n;
  }

  /**
   * 播放 / 切换 BGM。name='无' → 停止。
   * 无素材：**停掉上一首**并静默返回（label 照常更新，游戏节奏不受影响）。
   */
  playBgm(name: string): void {
    if (name === '无') {
      this.currentName = '—';
      this.stopBgm();
      return;
    }
    if (this.currentKey === name) return; // 同曲不重播
    this.currentName = name;

    // ① 先把「非当前」的全部淡出并停掉 —— 一个都不能留在响（旧实现漏在这里）
    for (const key of [...this.tracks.keys()]) {
      if (key !== name) this.fadeTo(key, 0, true);
    }

    const entry = manifest.bgm[name];
    if (!entry || this.failed.has(name)) {
      // 无素材 / 文件损坏：不指认任何曲子在播，也不留上一首继续响
      this.currentKey = null;
      return;
    }

    // ② 当前曲目：复用缓存元素，或新建
    let t = this.tracks.get(name);
    if (!t) {
      const el = new Audio(`/assets/audio/bgm/${entry.file.split('/').pop()}`);
      el.loop = entry.loop ?? true;
      el.preload = 'auto';
      el.volume = 0;
      el.addEventListener('error', () => this.failed.add(name), { once: true });
      t = { el, gen: 0, timer: null, target: null };
      this.tracks.set(name, t);
    }
    const track = t;
    this.currentKey = name;
    try {
      track.el.currentTime = 0;
    } catch {
      // 元数据还没就绪时 seek 会抛：忽略，start 位置由浏览器决定
    }
    void track.el
      .play()
      .then(() => {
        // ★ 只在**真的开始播放之后**才淡入 —— 否则 ramp 跑在一个还没发声的元素上，
        //   淡入跑完了声音才起来，或（更糟）淡入被守卫提前掐死、永远停在 0 音量
        if (this.currentKey === name) {
          this.fadeTo(name, this.bgmVolume);
        } else {
          // 播放期间已经被切走了 ⇒ 别把它留在「无声播放」态（否则它既不响又占着播放器）
          track.el.pause();
          track.el.volume = 0;
        }
      })
      .catch(() => {
        // 自动播放策略拦截：**不**标 failed（不是文件问题），下次手势由 unlock() 重试
      });
  }

  /** 停止 BGM（全部轨淡出后暂停） */
  stopBgm(): void {
    this.currentKey = null;
    for (const key of [...this.tracks.keys()]) this.fadeTo(key, 0, true);
  }

  /**
   * 音量渐变。
   *
   * @param stopAfter 淡到 0 之后把元素暂停（元素留在表里复用，不销毁）
   *
   * ★ 每次调用都换新代号 ⇒ 任何在跑的旧 tick 立刻自杀 —— 这是旧实现缺的那一环：
   *   旧码只跟踪最新元素、ramp 又不可取消，连点切歌时同一元素上会叠两条 ramp。
   */
  private fadeTo(key: string, target: number, stopAfter = false): void {
    const t = this.tracks.get(key);
    if (!t) return;
    const el = t.el;

    // 同向 fade 已在跑 → 别重启（否则连点会把这一轨的淡出无限往后拖）
    if (t.timer !== null && t.target !== null && Math.abs(t.target - target) < 1e-6) return;

    t.gen += 1;
    const gen = t.gen;
    if (t.timer !== null) {
      clearTimeout(t.timer);
      t.timer = null;
    }
    t.target = target;

    // 已经静止的（暂停 + 音量为 0）不必再跑时钟
    if (el.paused && el.volume === 0) {
      el.volume = target;
      if (stopAfter) el.pause();
      t.target = null;
      return;
    }

    const start = el.volume;
    const t0 = performance.now();
    const tick = () => {
      if (t.gen !== gen) return; // 已被更新的 fade 取代
      const p = Math.min(1, (performance.now() - t0) / FADE_MS);
      el.volume = Math.max(0, Math.min(1, start + (target - start) * p));
      if (p < 1) {
        t.timer = window.setTimeout(tick, FADE_STEP);
        return;
      }
      t.timer = null;
      t.target = null;
      el.volume = target;
      if (stopAfter) el.pause();
    };
    t.timer = window.setTimeout(tick, FADE_STEP);
  }

  /** 播放 SE（从资源池取空闲实例；无素材静默） */
  playSe(name: string): void {
    // SE 文件清单在 manifest.se —— 同样降级逻辑。
    // env=true 的是**环境型**（持续底噪），名单见 manifest.ts 的 SE_ENV。
    const entry = (manifest as unknown as { se?: Record<string, { file: string; env?: boolean }> }).se?.[name];
    if (!entry || this.failed.has(`se:${name}`)) return;
    // ★ 环境型：新环境替换旧环境 —— 否则「教室嘈杂」还没停、「周围的议论声」又起，两层底噪叠着响。
    if (entry.env) this.stopEnvSe();
    let el = this.sePool.find((e) => e.paused && e.dataset.se === name);
    if (!el) {
      el = new Audio(`/assets/audio/se/${entry.file.split('/').pop()}`);
      el.dataset.se = name;
      el.addEventListener('error', () => this.failed.add(`se:${name}`), { once: true });
      this.sePool.push(el);
    }
    el.volume = this.seVolume;
    el.currentTime = 0;
    void el.play().catch(() => {});
    if (entry.env) this.envSe = el; // 记进槽位，供场景切换时停掉
  }

  /**
   * 停掉当前**环境型** SE（淡出后暂停并归零）。
   *
   * ★ 必须由 `Director` 在**场景切换**时调用 —— 这是「议论声一直响到长椅戏」的修复点。
   *   根因：台本里**没有任何 SE 停止语义**（37 处 SE 标记 / 0 处停止），
   *   而 `playSe` 是「播完为止、无人管理」，所以 60.8 s 的议论声会跨场响到底。
   *
   * ⚠️ 用代号（gen）做取消：连续切场景时，让在跑的旧淡出立刻自杀 —— 与 `fadeTo` 同款防重入。
   * ⚠️ 收尾要 `volume` 还原成 seVolume：元素留在池里复用，音量被留在 0 的话下次播就哑了。
   */
  stopEnvSe(): void {
    const el = this.envSe;
    this.envSe = null;
    if (!el || el.paused) return;

    this.envFadeGen += 1;
    const gen = this.envFadeGen;
    const start = el.volume;
    const t0 = performance.now();

    const tick = () => {
      if (gen !== this.envFadeGen) return; // 已被更新的停止/播放取代
      const p = Math.min(1, (performance.now() - t0) / ENV_FADE_MS);
      el.volume = Math.max(0, start * (1 - p));
      if (p < 1) {
        window.setTimeout(tick, FADE_STEP);
        return;
      }
      el.pause();
      el.currentTime = 0;
      el.volume = this.seVolume; // 还原，供池子复用
    };
    window.setTimeout(tick, FADE_STEP);
  }

  /**
   * 播放某条台词的日配语音。
   *
   * @param line 台本行号（＝ `script.json` 里 say op 的 `line` 字段）
   *
   * ★ 三条语义（与 SE 刻意不同，别照抄 playSe）：
   *   ① **独占**：先把上一条**立即停掉**（不淡出）—— 两句话叠响会糊成一片；
   *   ② **无素材静默**：manifest.voice 里查不到该行（旁白 / 未录音角色 / 语音目录为空）
   *      ⇒ 直接 return，不报错、不占播放器；
   *   ③ **不阻塞**：不返回 Promise、不通知播放结束 —— 台词推进节奏由玩家的点击/自动播放决定，
   *      语音只负责「发出声音」。玩家点快了就 `stopVoice()` 掐断，这是正常的。
   */
  playVoice(line: number): void {
    this.stopVoice();

    const file = manifest.voice?.[String(line)];
    if (!file || this.voiceFailed.has(line)) return;

    const el = new Audio(`/assets/audio/${file}`);
    el.preload = 'auto';
    el.volume = this.voiceVolume;
    // ★ 意图标记先立起来（此刻播放尚未真正开始，见 voiceActive 的字段注释）
    this.voiceActive = true;

    el.addEventListener(
      'ended',
      () => {
        // ★ 自然播完 ⇒ 自动播放可以往下走了
        if (this.voiceEl === el) this.voiceActive = false;
      },
      { once: true },
    );
    el.addEventListener(
      'error',
      () => {
        this.voiceFailed.add(line);
        if (this.voiceEl === el) {
          this.voiceEl = null;
          this.voiceActive = false; // ★ 别让自动播放干等一条永远不响的语音
        }
      },
      { once: true },
    );

    this.voiceGen += 1;
    const gen = this.voiceGen;
    this.voiceEl = el;

    void el
      .play()
      .then(() => {
        // 播放期间被新语音/stopVoice 取代 ⇒ 别把它留在「无声播放」态
        if (gen !== this.voiceGen) {
          el.pause();
          el.volume = 0;
        }
      })
      .catch(() => {
        // 自动播放策略拦截：不标 failed（不是文件问题），下次手势自然会响
        // ★ 同时落回 voiceActive —— 否则自动播放会一直等这条永远不响的语音
        if (this.voiceEl === el) {
          this.voiceEl = null;
          this.voiceActive = false;
        }
      });
  }

  /** 停掉当前语音（立即，不淡出）。玩家点击跳过 / 切场景 / 返回标题时调用 */
  stopVoice(): void {
    const el = this.voiceEl;
    this.voiceEl = null;
    this.voiceActive = false; // ★ 主动掐断＝本条到此为止，自动播放不必再等
    this.voiceGen += 1; // 让在跑的 play().then 认得出自己已过期
    if (!el) return;
    el.pause();
    try {
      el.currentTime = 0;
    } catch {
      // 元数据未就绪时 seek 会抛：忽略
    }
  }

  /** 诊断用：此刻是否有语音在播（实时 `paused` 判据） */
  get voicePlaying(): boolean {
    return this.voiceEl !== null && !this.voiceEl.paused;
  }

  /**
   * ★ 该台本行是否有可播的语音（backlog 重播按钮的显隐依据）。
   * 只查 manifest 与失败缓存，**不**发网络请求。
   */
  hasVoice(line: number): boolean {
    return !!manifest.voice?.[String(line)] && !this.voiceFailed.has(line);
  }

  /**
   * ★ 本条语音尚未播完（**意图级**；自动播放的等待依据）。
   * 无素材（旁白 / 未录音角色）恒为 `false` ⇒ 自动播放自然回落到按字数计时。
   */
  get voicePending(): boolean {
    return this.voiceActive;
  }

  /**
   * ★ 本条语音的时长（秒）；元数据未就绪 / 无元素时返回 `0`。
   *
   * 用途：`Director` 的**等语音超时上限**要按真实时长算 —— 固定上限会把长语音
   * 当场切断（本项目实测最长台词 **25.1 s**，见 `tools/_probe_voice_silence.py`）。
   */
  get voiceDuration(): number {
    const d = this.voiceEl?.duration;
    return typeof d === 'number' && Number.isFinite(d) && d > 0 ? d : 0;
  }

  /** 预热：用户首次手势后调用，解锁自动播放限制 */
  unlock(): void {
    // 播放一个静音音频触发解锁。★ 复用同一个元素（旧实现每次点击都 new，是泄漏）
    if (!this.unlockEl) {
      this.unlockEl = new Audio(
        'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=',
      );
      this.unlockEl.volume = 0;
    }
    void this.unlockEl.play().catch(() => {});

    // 当前曲目若因策略被拦下过，重试
    const key = this.currentKey;
    if (key === null) return;
    const t = this.tracks.get(key);
    if (t && t.el.paused) {
      void t.el
        .play()
        .then(() => {
          if (this.currentKey === key) this.fadeTo(key, this.bgmVolume);
        })
        .catch(() => {});
    }
  }
}
