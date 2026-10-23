/**
 * Director —— 演出编排
 *
 * 职责：把 ScriptVM 的事件流翻译成「画面 + 文本」的实际呈现，
 * 并管理播放节奏（点击推进 / 自动播放 / 打字机）。
 *
 * 边界（技术设计.md §3.1）：
 * - 不解释剧情语义（那是 engine 层的事）
 * - 不直接操作 PixiJS 原语（那是 render 层的事）
 * - 只做「什么时候让什么出现、以什么节奏出现」
 *
 * 本版为任务 4 的第一版：能从头播放到尾、能点选选项、能存读档（内存）。
 * 打字机 / 转场 / 粒子等特效在任务 5 深化。
 */

import type { ScriptProgram } from '../engine/script/ops.ts';
import { GameState, type GameStateSnapshot } from '../engine/state/GameState.ts';
import { ScriptVM, type VmEvent } from '../engine/script/ScriptVM.ts';
import { Layers } from '../render/Layers.ts';
import { AudioEngine } from '../audio/AudioEngine.ts';
import type { GameSettings } from '../engine/settings.ts';
import { DEFAULT_SETTINGS } from '../engine/settings.ts';
import { storage } from '../platform/StorageAdapter.ts';

/** 当前对话框要显示的内容 */
export interface DialogView {
  /** 说话人；null = 旁白（不显示姓名框） */
  who: string | null;
  /** narration = 旁白 / speech = 台词 / inner = 心声 */
  kind: 'narration' | 'speech' | 'inner';
  /** 已显示文本（打字机逐字增长） */
  text: string;
  /** 全部文本 */
  fullText: string;
}

/** 选项视图 */
export interface ChoiceView {
  id: string;
  options: { key: string; text: string }[];
}

/** 提示条（场景切换 / CG 等瞬时信息，淡出） */
export interface Toast {
  id: number;
  text: string;
}

/**
 * 历史对话记录（backlog）★ 2026-09-19 加。
 * `line`＝台本行号（有配音的条目可重播语音）；旁白 / 未录音的行为 null。
 */
export interface HistoryEntry {
  seq: number;
  who: string | null;
  text: string;
  line: number | null;
}

/** 供 React `useSyncExternalStore` 消费的只读视图快照 */
export interface DirectorSnapshot {
  dialog: DialogView | null;
  choice: ChoiceView | null;
  toast: Toast | null;
  finished: boolean;
  scene: string;
  bgm: string;
  isTyping: boolean;
  chapter: number;
  pc: number;
  total: number;
}

export type DirectorListener = () => void;

/**
 * 角色简称 → 全名（姓名框显示用）。
 * 台本用简称【樱】【龙司】，对话框上应显示全名。
 * 未映射的名字原样显示（如群像）。
 */
const CHAR_FULL_NAME: Record<string, string> = {
  樱: '篠宫樱',
  龙司: '芥川龙司',
  阳太: '木下阳太',
  亚里沙: '高梨亚里沙',
  九条院: '九条院丽华',
  泽渡: '泽渡拓真',
};

/** 打字机速度：字/秒（默认值；运行时由 settings.textSpeed 驱动） */
const TYPE_SPEED_CPS = 45;

// ————————————————————————————————————————————————————————————————
// 自动播放的两套节奏 ★ 2026-09-19（主人令：「无语音时跟随字长；有语音时跟随语音长度」）
// ————————————————————————————————————————————————————————————————
//
// · **有语音**（该台本行有配音）：等语音**自然播完**，再留一个呼吸间隔。
//   推进节奏＝语音长度。⛔ **不再叠加字数延迟** —— 语音念的就是这句台词，
//   听完＝读完；叠上去只会让每句都多等两秒。
// · **无语音**（旁白 / 未录音角色）：按字数估算阅读时间，见下面三个常量。
//
/** 语音播完后的呼吸间隔（ms）—— 让相邻两句之间不「撞在一起」 */
const VOICE_TAIL_MS = 350;
/**
 * 等语音的**保底**超时上限（ms）—— 兜底，防自动播放永久停住。
 *
 * 若语音加载卡死（网络慢 / 解码异常但 `error` 没触发），`voicePending` 会一直是 `true`，
 * 而自动播放是「等它落回才排计时」⇒ 没有这道兜底就再也走不动了。
 *
 * ⚠️ 只在**拿不到语音时长**时才用这个值；正常走 `voiceDuration + VOICE_EXTRA_MS`。
 *   固定上限本身是错的 —— 本项目实测最长台词 **25.1 s**（`tools/_probe_voice_silence.py`），
 *   任何小于它的常量都会把那句语音**当场切断**。
 */
const VOICE_WAIT_FALLBACK_MS = 45000;
/** 拿到时长后，在「语音时长」之上多给的余量（ms）—— 覆盖解码 / 首播延迟 */
const VOICE_EXTRA_MS = 8000;
/** 无语音时的字数公式：基础 + 每字，上限 */
const AUTO_BASE_MS = 1600;
const AUTO_PER_CHAR_MS = 120;
const AUTO_MAX_MS = 8000;

/**
 * 花瓣粒子密度（瓣/屏）。★ 2026-09-15 起**只由台本 `※ 花瓣密/疏/停` 驱动**：
 * 每个 SCENE 进场先回默认值，不再按场景名含「樱」自动加倍。
 */
const PETAL_DEFAULT = 24;
const PETAL_DENSE = 64;

/**
 * 台本指纹（FNV-1a 32 位）：只吃**结构性**字段（op 种类 / name / id / char）。
 *
 * 用途：存档校验。`pc` 是**结构位置**，台本一改结构它就指向别的内容
 * （今晚 Ch1–Ch3 大改即是）—— 而这类错位**全程无声**：读档后画面/台词/音乐
 * 一起错，看起来像"剧情跳了"。
 *
 * ★ 刻意**不**吃台词文本：纯润色不动结构、pc 不变，这种存档依然有效，
 *   不该被判为过期（否则每次文风打磨都会废掉玩家的档）。
 */
function fingerprintOps(ops: readonly Record<string, unknown>[]): string {
  let h = 0x811c9dc5;
  const feed = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
  };
  for (const o of ops) {
    feed(String(o.op));
    feed(String(o.name ?? ''));
    feed(String(o.id ?? ''));
    feed(String(o.char ?? ''));
    feed('\u0001');
  }
  return `${ops.length}-${(h >>> 0).toString(16)}`;
}

export class Director {
  private program: ScriptProgram;
  /**
   * 台本指纹（构造时算一次）—— 存档校验用，见 `fingerprintOps`。
   * 标题页／存读档面板拿它判断「这个档还读不读得出来」。
   */
  readonly scriptFingerprint: string;
  readonly state = new GameState();
  readonly audio = new AudioEngine();
  settings: GameSettings;
  private vm: ScriptVM;
  private layers: Layers | null = null;
  private listeners = new Set<DirectorListener>();

  // —— 播放状态 ——
  private _dialog: DialogView | null = null;
  private _choice: ChoiceView | null = null;
  private _toast: Toast | null = null;
  /** ★ 历史对话记录（backlog）；toTitle / 跳章 / 读档时清空（新会话） */
  private history: HistoryEntry[] = [];
  private histSeq = 0;
  private _finished = false;

  private typeTarget = '';
  private typeShown = 0;
  private typeLastMs = 0;
  private typeActive = false;
  /** 自动播放的下次推进时刻（0 = 未计时） */
  private _autoAt = 0;
  /**
   * 自动播放的等待阶段（★ 2026-09-19）：
   * - `idle`  本句尚未开始判定（还在打字 / 刚进入这句）
   * - `voice` 正在等语音播完（这句有配音）
   * - `timer` 已排定 `_autoAt` 倒计时
   */
  private _autoPhase: 'idle' | 'voice' | 'timer' = 'idle';
  /** 进入 `voice` 阶段的时刻 —— 配合 `VOICE_WAIT_MAX_MS` 做超时兜底 */
  private _autoVoiceSince = 0;

  private sceneLabel = '';
  private bgmLabel = '';
  private toastSeq = 0;
  private toastUntil = 0;

  constructor(program: ScriptProgram, settings?: GameSettings) {
    this.program = program;
    this.settings = settings ?? { ...DEFAULT_SETTINGS, textSpeed: TYPE_SPEED_CPS };
    this.scriptFingerprint = fingerprintOps(program.ops as unknown as Record<string, unknown>[]);
    this.audio.setVolumes(this.settings.bgmVolume, this.settings.seVolume, this.settings.voiceVolume);
    this.vm = new ScriptVM(program, this.state, (e) => this.onEvent(e));
  }

  bindLayers(layers: Layers): void {
    this.layers = layers;
  }

  /** 箭头函数属性：保证引用稳定，且不会丢 `this` */
  subscribe = (fn: DirectorListener): (() => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  /**
   * 视图快照。
   * ★ 必须是**稳定引用**：`useSyncExternalStore` 要求 getSnapshot 在没有变化时
   * 返回同一个对象，否则 React 会判定「每次都变了」而无限重渲染。
   * 因此只在 notify() 时重建一次。
   */
  private _snapshot: DirectorSnapshot = {
    dialog: null,
    choice: null,
    toast: null,
    finished: false,
    scene: '',
    bgm: '',
    isTyping: false,
    chapter: 0,
    pc: 0,
    total: 0,
  };

  private notify(): void {
    this._snapshot = {
      dialog: this._dialog,
      choice: this._choice,
      toast: this._toast,
      finished: this._finished,
      scene: this.sceneLabel,
      bgm: this.bgmLabel,
      isTyping: this.typeActive,
      chapter: this.state.currentChapter,
      pc: this.vm.getProgramCounter(),
      total: this.program.ops.length,
    };
    for (const fn of this.listeners) fn();
  }

  /** 供 `useSyncExternalStore` 使用：始终返回同一引用，直到 notify() 重建 */
  getSnapshot = (): DirectorSnapshot => this._snapshot;

  // ============================================================
  // 只读视图
  // ============================================================

  get dialog(): DialogView | null {
    return this._dialog;
  }
  get choice(): ChoiceView | null {
    return this._choice;
  }
  get toast(): Toast | null {
    return this._toast;
  }

  /** ★ 历史对话记录（backlog 面板用；只读引用，写入只在 onEvent 里发生） */
  getHistory(): readonly HistoryEntry[] {
    return this.history;
  }

  /**
   * ★ 重播某条历史台词的语音（backlog 的 ▶ 按钮）。
   * `playVoice` 自带「先停上一条」的独占语义；无素材的行（旁白等）静默不响。
   */
  replayVoice(line: number | null): void {
    if (line === null) return;
    this.audio.playVoice(line);
  }
  get finished(): boolean {
    return this._finished;
  }
  get scene(): string {
    return this.sceneLabel;
  }
  get bgm(): string {
    return this.bgmLabel;
  }
  /** 打字机是否正在逐字显示 */
  get isTyping(): boolean {
    return this.typeActive;
  }
  get progress(): { chapter: number; sceneId: string; pc: number; total: number } {
    return {
      chapter: this.state.currentChapter,
      sceneId: this.state.currentSceneId,
      pc: this.vm.getProgramCounter(),
      total: this.program.ops.length,
    };
  }

  // ============================================================
  // 事件处理
  // ============================================================

  private onEvent(e: VmEvent): void {
    switch (e.type) {
      case 'scene':
        this.sceneLabel = `${e.place}・${e.time}`;
        // ★ 2026-09-15：粒子密度**不再**按场景名含「樱」自动加倍 —— 每个场景回默认，
        //   要密/疏/停由台本显式写 `※ 花瓣密` / `※ 花瓣疏` / `※ 花瓣停`。
        //   ⚠️ 必须**同步**重置：若放进 dipToBlack 回调（黑场顶点才执行），
        //      会晚于紧随 SCENE 的 `※ 花瓣密`，把它覆盖掉。
        this.layers?.sakuraDensity(PETAL_DEFAULT);
        // ★ 环境型 SE 的生命周期管理（2026-09-18）——
        //   台本里**没有 SE 停止语义**（37 处 SE 标记 / 0 处停止），而 playSe 是「播完为止、
        //   无人管理」⇒ 60.8 s 的「周围的议论声」会一路响到长椅戏（主人实机反馈）。
        //   场景边界＝环境边界：在这里淡出停掉；一次性事件音（纸页/脚步/铃）不受影响。
        this.audio.stopEnvSe();
        // ★ 语音：场景边界＝语音边界（与 stopEnvSe 同理）—— 上一场的台词不该跨场继续响
        this.audio.stopVoice();
        // ★ 场景切换走黑场转场：黑场顶点清「切换前已在台上」的立绘（与 GameState.closeAllWindows 对应）
        //   —— clearSprites 带黑场开始时刻，黑场期间才登场的新场景立绘不会被吞（2026-09-12 修）
        this.layers?.dipToBlack((dipAt) => {
          this.layers?.clearSprites(dipAt);
          this.layers?.hideCg(true); // ★ CG 随场景切换自动收起（N-014：收起时机＝场景边界）。
          //   immediate=true：此刻黑场正浓，直接清——旧做法 400ms 淡出会与黑场后段（350ms）重叠，
          //   玩家看到「CG 慢慢消失露出 BG、然后新 CG 又出现」（主人 2026-09-18 反馈「很突兀」）。
        });
        // ★ 2026-09-15：场景提示条改为**玩家设置项**（可关），不再是台本管不着的强制行为
        if (this.settings.sceneToast) this.showToast(`■ ${e.id}　${e.place}`);
        break;

      case 'bg':
        this.layers?.setBg(e.name);
        break;

      case 'bgm':
        this.bgmLabel = e.name === '无' ? '—' : e.name;
        this.audio.playBgm(e.name);
        break;

      case 'se':
        this.audio.playSe(e.name);
        break;

      case 'sprite-show':
        // ★ 2026-09-15（主人令：「程序死跟台本走，没有任何隐藏的小巧思」）：
        //   立绘括注**不再自动触发雀跃**。旧行为是「括注命中 emotion.ts 的 14 个情绪词
        //   → 立绘自动跳一下」——台本里一个「跳」字都没有，作者也看不出哪句会跳。
        //   现在要跳，请在台本里显式写 `※ 雀跃`（或 `※ 雀跃：樱`）。
        this.layers?.showSprite(e.char, e.pose, e.slot);
        break;

      case 'sprite-hide':
        this.layers?.hideSprite(e.char);
        break;

      case 'cg':
        // 收起写法＝`■ CG：无`（与 `BGM：无` 约定一致，N-014）
        if (e.name === '无') {
          this.layers?.hideCg();
          break;
        }
        // ★ 2026-09-15：**不再默认配白闪**。要闪请在台本里显式写 `※ 白闪`。
        this.layers?.showCg(e.name);
        break;

      case 'fx':
        this.applyFx(e.fxType, e.params);
        break;

      case 'say': {
        // ★ 日配语音（2026-09-18）—— 按**台本行号**取音频；无素材静默降级
        //   （旁白 / 未录音角色查不到键，什么都不做，不影响游戏节奏）
        this.audio.playVoice(e.line);
        const full = this.blocksToText(e.text);
        // ★ 历史记录（2026-09-19）：每条 say 都进 backlog（含旁白），供历史面板回看/重播
        this.histSeq += 1;
        this.history.push({
          seq: this.histSeq,
          who: e.who ? (CHAR_FULL_NAME[e.who] ?? e.who) : null,
          text: full,
          line: e.line,
        });
        if (this.history.length > 300) this.history.shift(); // 上限 300 条，防长跑内存膨胀
        this.typeTarget = full;
        this.typeShown = 0;
        this.typeActive = true;
        this.typeLastMs = performance.now();
        this._dialog = {
          who: e.who ? (CHAR_FULL_NAME[e.who] ?? e.who) : null,
          kind: e.kind as DialogView['kind'],
          text: '',
          fullText: full,
        };
        break;
      }

      case 'choice':
        // §9.4 选项演出：压暗立绘（BGM 不变、无过场——由 choice/pick 的同步性保证）
        this.layers?.dimChar(true);
        this._choice = { id: e.id, options: e.options };
        break;

      case 'end':
        this._finished = true;
        this.showToast('— 未完待续 —');
        break;
    }
  }

  private blocksToText(text: string): string {
    return text;
  }

  /**
   * fx 演出分派（parser 的 starToFx 白名单，见 R-001 / R-002 / R-018）。
   * - push（特写）/ camera（镜头）→ **【已停用】不产生任何画面动作**（见下方 case 的说明）
   * - fadeOut → 黑场渐入 1.4s
   * - hop（雀跃）→ 立绘跳一下；params.char 指定角色，不指定＝台上全部
   * - flash（白闪）→ 白闪一次
   * - petalDense / petalSparse / petalStop → 花瓣粒子密度
   * - beat / blur → 纯节拍 / 滤镜，本期不做画面动作
   *
   * ★ 2026-09-15：hop / flash / petal* 这四类**原先是引擎自动触发**，
   *   现全部改由台本 `※` 显式写（主人令「没有任何隐藏的小巧思」）。
   */
  private applyFx(type: string, params?: Record<string, number | string>): void {
    switch (type) {
      // ★ 2026-09-18（主人令）：**推镜整体删除** —— `※ 特写` / `※ 镜头` 不再推镜。
      //   原因：① 主人观感「看得恶心」（每次切场景都像被推一下）；
      //        ② 排查「CG 上的斜向白线」时发现：推镜＝相机做**非整数缩放**，
      //           在同内容 A/B（同一张纹理、只改相机 scale）里，斜向伪影由 3 条涨到 26 条。
      //   算法与调用点保留在 Layers.pushIn（标了 [已停用]），日后要压别的东西（如 UI 面板）还能捡回来。
      //   ⛔ 台本里的 `※ 特写／※ 镜头` 标记**保留**（它们还承担语义标注与节奏作用），只是不再产生画面动作。
      case 'push':
      case 'camera':
        break;
      case 'fadeOut':
        this.layers?.dipToBlack(() => {}, 1400);
        break;
      case 'shake':
        this.layers?.shake();
        break;
      case 'hop': {
        const who = typeof params?.char === 'string' ? params.char : undefined;
        if (who) this.layers?.hop(who);
        else this.layers?.hopAll();
        break;
      }
      case 'flash':
        this.layers?.flash();
        break;
      case 'petalDense':
        this.layers?.sakuraDensity(PETAL_DENSE);
        break;
      case 'petalSparse':
        this.layers?.sakuraDensity(PETAL_DEFAULT);
        break;
      case 'petalStop':
        this.layers?.sakuraDensity(0);
        break;
      case 'beat':
      case 'blur':
        // 纯节奏/滤镜：不产生画面动作
        break;
    }
  }

  private showToast(text: string): void {
    this.toastSeq += 1;
    this._toast = { id: this.toastSeq, text };
    this.toastUntil = performance.now() + 2200;
  }

  // ============================================================
  // 播放控制
  // ============================================================

  start(): void {
    this.advance();
  }

  /**
   * 推进：打字中 → 立即显示全部；否则执行到下一条 say。
   */
  advance(): void {
    if (this._finished) return;

    // ★ 玩家主动点击 → 自动播放计时器清零，下一句重新判定节奏
    //   否则上句遗留的旧计时会在补全后立刻到期跳句（「字还没出完就又跳了」）
    this._autoAt = 0;
    this._autoPhase = 'idle';

    if (this.typeActive) {
      // 打字中：瞬间补全
      this.typeShown = this.typeTarget.length;
      this.typeActive = false;
      if (this._dialog) this._dialog.text = this.typeTarget;
      // ★ 2026-09-19（主人令）：补全**不打断语音** —— 语音是这句台词的「读完」信号，
      //   字补完了语音还该继续念（自动播放也仍按 voicePending 等它念完）。
      //   语音只在两种时机停：① 推进到下一句（playVoice 自带独占停止）；
      //   ② 场景切换 / 读档 / 回标题（onEvent 的 stopVoice）。此处一律不停。
      this.notify();
      return;
    }

    if (this._choice) return; // 等玩家选择

    // ★ 单步：执行到「下一条 say」就停，让打字机播完这句
    const r = this.vm.stepToSay();
    if (r === 'end') {
      this._finished = true;
    }
    this.notify();
  }

  /** 玩家选择 */
  pick(index: number): void {
    if (!this._choice) return;
    this._choice = null;
    this._autoAt = 0; // 选择后下一句重新判定节奏
    this._autoPhase = 'idle';
    this.layers?.dimChar(false); // 恢复立绘亮度
    this.vm.pickChoice(index);
    // 选完继续推进到下一条 say
    const r = this.vm.stepToSay();
    if (r === 'end') this._finished = true;
    this.notify();
  }

  /** 每帧推进打字机与 toast 淡出 */
  tick(nowMs: number): void {
    let changed = false;

    if (this.typeActive && this._dialog) {
      const dt = nowMs - this.typeLastMs;
      this.typeLastMs = nowMs;
      this.typeShown += (dt / 1000) * this.settings.textSpeed;
      const n = Math.min(Math.floor(this.typeShown), this.typeTarget.length);
      const next = this.typeTarget.slice(0, n);
      if (next !== this._dialog.text) {
        this._dialog.text = next;
        changed = true;
      }
      if (n >= this.typeTarget.length) {
        this.typeActive = false;
        changed = true;
      }
    }

    if (this._toast && nowMs > this.toastUntil) {
      this._toast = null;
      changed = true;
    }

    // ——— 自动播放：文本完整显示、无选项 → 排定推进时刻 ———
    // ★ 2026-09-19 主人令：**有语音跟语音长度，无语音跟字长**。
    //   旧实现只有「字数」一条路（`1600 + 字×120`），`audio` 一个字都没问 ——
    //   于是语音还在念，字已打完、计时到点就跳走，语音被下一句当场掐断。
    if (this.settings.autoPlay && !this.typeActive && this._dialog && !this._choice) {
      if (this._autoPhase === 'idle') {
        if (this.audio.voicePending) {
          // 这句有配音 ⇒ 等它自然念完（不叠字数延迟：语音念的就是这句，听完＝读完）
          this._autoPhase = 'voice';
          this._autoVoiceSince = nowMs;
        } else {
          const chars = this._dialog.fullText.length;
          const delay = Math.min(AUTO_MAX_MS, AUTO_BASE_MS + chars * AUTO_PER_CHAR_MS);
          this._autoAt = nowMs + delay;
          this._autoPhase = 'timer';
        }
      } else if (this._autoPhase === 'voice') {
        // 播完 / 被掐断 / 出错 ⇒ 落回 false；超时兜底防「加载卡死 ⇒ 永久停住」
        if (
          !this.audio.voicePending ||
          nowMs - this._autoVoiceSince > this.voiceWaitLimitMs()
        ) {
          this._autoAt = nowMs + VOICE_TAIL_MS;
          this._autoPhase = 'timer';
        }
      }
    }
    if (this._autoAt !== 0 && nowMs >= this._autoAt) {
      this._autoAt = 0;
      this._autoPhase = 'idle';
      if (!this._choice && !this.typeActive) {
        this.advance();
        changed = true;
      }
    }

    if (changed) this.notify();
  }

  /**
   * 等语音的上限（ms）：优先按**语音真实时长 + 余量**，拿不到时长才用保底常量。
   * ★ 别改回固定值 —— 见 `VOICE_WAIT_FALLBACK_MS` 的注释（本项目有 25.1 s 的长台词，
   *   固定上限会把那句语音当场切断）。
   */
  private voiceWaitLimitMs(): number {
    const dur = this.audio.voiceDuration; // 秒；元数据未就绪时为 0
    return dur > 0 ? dur * 1000 + VOICE_EXTRA_MS : VOICE_WAIT_FALLBACK_MS;
  }

  /** 应用设置（SettingsPanel 变更时调用；打字机/自动播放下 tick 生效） */
  applySettings(s: GameSettings): void {
    this.settings = s;
    // 打字中速度变化立即生效：按新速度重排已显示字符
    this.typeShown = this._dialog?.text.length ?? this.typeShown;
    // ★ 音量即时生效（含语音）—— 玩家拖音量条要立刻听得到，别等下一句
    this.audio.setVolumes(s.bgmVolume, s.seVolume, s.voiceVolume);
    // ★ 关掉自动播放时清掉残留计时（2026-09-19 顺手修）——
    //   旧实现只清 `_autoAt` 的**到期检查**、不清值本身，而 `_autoAt` 那条分支
    //   在 `autoPlay` 判断之外 ⇒ 关掉自动播放后，那句已排定的计时到期**仍会推一句**。
    if (!s.autoPlay) {
      this._autoAt = 0;
      this._autoPhase = 'idle';
    }
  }

  // ============================================================
  // 存档（StorageAdapter 持久化 —— Web localStorage / Tauri fs 同接口）
  // ============================================================

  /** 存档槽位（固定 4 个：quick + s1..s3） */
  static readonly SLOTS = ['quick', 's1', 's2', 's3'] as const;

  save(slot: string): boolean {
    const snap = this.state.snapshot(this.vm.getProgramCounter());
    // ★ 存档里带一份「台本指纹」。台本一改结构，pc 的语义就变了（今晚 Ch1–Ch3 大改即是），
    //   旧档读出来会落在错误的行/场景 —— 见 load() 的校验。
    const meta: SaveMeta = {
      savedAt: Date.now(),
      chapter: snap.currentChapter,
      sceneId: snap.currentSceneId,
      excerpt: this._dialog?.fullText.slice(0, 30) ?? '',
      playTimeMs: snap.playTimeMs,
      scriptFingerprint: this.scriptFingerprint,
    };
    storage.set(`save.${slot}.meta`, JSON.stringify(meta));
    storage.set(`save.${slot}.data`, JSON.stringify({ ...snap, scriptFingerprint: this.scriptFingerprint }));
    return true;
  }

  load(slot: string): boolean {
    const raw = storage.get(`save.${slot}.data`);
    if (!raw) return false;
    let snap: GameStateSnapshot & { scriptFingerprint?: string };
    try {
      snap = JSON.parse(raw) as GameStateSnapshot & { scriptFingerprint?: string };
    } catch {
      return false; // 损坏档：当作不存在
    }

    // ★ 台本指纹校验。字段缺失＝本校验上线前存的档（台本其后已被改造过），
    //   一律按不匹配处理 —— 宁可让玩家重开，也别把他扔到一个语义错位的 pc 上。
    if (snap.scriptFingerprint !== this.scriptFingerprint) {
      this.flash('这个存档是旧版本留下的（台本已改动），读不出来。请用「开始游戏」。');
      return false;
    }

    // 1. 恢复状态（vars / 舞台现状 / 阅读记录）
    this.state.restore(snap);

    // 2. 重建 VM，只移动 pc，不重跑指令
    this.vm = new ScriptVM(this.program, this.state, (e) => this.onEvent(e));
    this.vm.seekTo(snap.pc);
    // ★ 读档：掐掉刚才那句没听完的语音（接着往下读的应该是新位置的台词）
    this.audio.stopVoice();

    // 3. 用恢复出的舞台现状重建画面
    this.rebuildStage();

    this._finished = false;
    this._choice = null;
    this._dialog = null;
    this._toast = null;
    this.history = []; // ★ 读档＝新会话：历史记录清空
    this.notify();
    return true;
  }

  /**
   * 按当前 GameState 的舞台现状重建画面（读档后调用）。
   * 因为「舞台现状属于状态」—— 所以不需要重放指令就能还原（技术设计.md §7.2）。
   */
  private rebuildStage(): void {
    const l = this.layers;
    if (!l) return;
    l.clearSprites();
    l.hideCg();
    if (this.state.currentBg) l.setBg(this.state.currentBg);
    for (const [char, info] of Object.entries(this.state.sprites)) {
      l.showSprite(char, info.pose, info.slot);
    }
    if (this.state.currentCg) l.showCg(this.state.currentCg);
    this.sceneLabel = this.state.currentSceneId;
    // ★ 2026-09-18 修：这里原先只改 label、**不碰音频** ⇒ 读档/继续之后
    //   顶部显示的是存档里的曲名，耳朵听到的却还是进入前那一首
    //   （主人报的「UI 显示的是现在的 bgm，但响的还是上一个 bgm」）。
    //   playBgm 对「同曲」是 no-op，所以重复调用安全。
    this.audio.playBgm(this.state.currentBgm ?? '无');
    this.bgmLabel = this.state.currentBgm ?? '—';
  }

  /**
   * 给玩家的一条即时提示（复用脚本提示条那套显示通道，2.2s 自动消失）。
   * 用于「这个档读不出来」这类**系统侧**的反馈 —— 脚本侧走 `※` 提示。
   */
  flash(text: string): void {
    this.showToast(text);
    this.notify();
  }

  /**
   * 跳到第 N 章的**开头**（主人 2026-09-18 定：跳章＝章首 · 重置进度）。
   *
   * 语义＝「等同于从头玩到那一章开头」：状态清零 → VM 重建并 seek 到该章第一个
   * `scene` op → 跑出第一条 say（bg / bgm / cg 都在章首，会顺带把画面与音频立起来）。
   *
   * ⚠️ 不复用 `load()` 那条「只 seek、不重跑指令」的路 —— 章首没有可重建的舞台现状，
   *    必须让指令真的执行一遍。
   */
  jumpToChapter(index: number): boolean {
    const ch = this.program.chapters.find((c) => c.index === index);
    if (!ch) return false;

    // 1) 状态清零（音量/设置不在 GameState 上，不会被清）
    this.state.restore({
      version: 1,
      savedAt: Date.now(),
      playTimeMs: 0,
      pc: ch.startPc,
      vars: {},
      flags: {},
      currentChapter: ch.index,
      currentSceneId: '',
      currentBg: null,
      currentBgm: null,
      sprites: {},
      currentCg: null,
      readLineIds: [],
      windows: {},
    });

    // 2) 重建 VM，seek 到章首
    this.vm = new ScriptVM(this.program, this.state, (e) => this.onEvent(e));
    this.vm.seekTo(ch.startPc);

    // 3) 画面与音频先复位，免得带着上一章的 BG / BGM / 台词声音进新章
    this.layers?.clearSprites();
    this.layers?.hideCg();
    this.audio.stopBgm();
    this.audio.stopVoice();

    this.sceneLabel = '';
    this.bgmLabel = '—';
    this._finished = false;
    this._choice = null;
    this._dialog = null;
    this._toast = null;
    this._autoAt = 0;
    this._autoPhase = 'idle';
    this.typeActive = false;
    this.typeShown = 0;
    this.history = []; // ★ 新会话：历史记录清空

    // 4) 跑出第一条 say —— 让画面/音频立刻跟上
    this.advance();
    return true;
  }

  /**
   * 回主页：把进度**复位到开头**、停掉音乐、清空舞台。
   * 于是标题页的「开始游戏」真的从第 1 章开始（而不是接着刚才的位置继续），
   * 「继续游戏」则仍从最近一次存档进来 —— 未存档的进度会丢，所以 UI 要二次确认。
   */
  toTitle(): void {
    this.state.restore({
      version: 1,
      savedAt: Date.now(),
      playTimeMs: 0,
      pc: 0,
      vars: {},
      flags: {},
      currentChapter: 0,
      currentSceneId: '',
      currentBg: null,
      currentBgm: null,
      sprites: {},
      currentCg: null,
      readLineIds: [],
      windows: {},
    });
    this.vm = new ScriptVM(this.program, this.state, (e) => this.onEvent(e));
    this.vm.seekTo(0);

    this.audio.stopBgm();
    this.audio.stopVoice();
    this.layers?.clearSprites();
    this.layers?.hideCg();

    this.sceneLabel = '';
    this.bgmLabel = '';
    this._finished = false;
    this._choice = null;
    this._dialog = null;
    this._toast = null;
    this._autoAt = 0;
    this._autoPhase = 'idle';
    this.typeActive = false;
    this.typeShown = 0;
    this.history = []; // ★ 新会话：历史记录清空
    this.notify();
  }

  /** 组件卸载时调用：清订阅与画面引用，防泄漏 */
  dispose(): void {
    this.listeners.clear();
    this.layers = null;
    this._dialog = null;
    this._choice = null;
    this._toast = null;
  }
}

/** 存档元信息（列表 UI 用，与快照分开存 —— 列表页不用反序列化全部状态） */
export interface SaveMeta {
  savedAt: number;
  chapter: number;
  sceneId: string;
  /** 当前句前 30 字 */
  excerpt: string;
  playTimeMs: number;
  /**
   * 台本指纹（＝`fingerprintOps(program.ops)`，构造时算一次）。
   * ⚠️ **本校验上线前存的档没有这个字段** ⇒ 一律视为不兼容（台本其后已被改造过）。
   */
  scriptFingerprint?: string;
}

/** 读取槽位元信息（无档返回 null） */
export function loadMeta(slot: string): SaveMeta | null {
  const raw = storage.get(`save.${slot}.meta`);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SaveMeta;
  } catch {
    return null;
  }
}

/** 列出有存档的槽位 */
export function listSaves(): string[] {
  return Director.SLOTS.filter((s) => storage.get(`save.${s}.data`) !== null);
}
