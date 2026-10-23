/**
 * 《笑樱》ScriptVM —— 指令流执行器
 *
 * 职责：顺序执行 `Op[]`，遇到 `say` / `scene` / `bg` / `bgm` / `sprite` / `cg` / `choice` 等指令时
 * 通过回调通知渲染层。**不渲染任何东西** —— 这是为什么「纯 TS 可单测、终端可跑通」。
 *
 * 选项处理：
 * - 调 `runUntilChoice()` 会一直跑到下一个 choice op，**停下等待**玩家选
 * - 选完后调 `pickChoice(index)` 继续
 *
 * 窗口求值（按 R-014 / 技术设计.md 附录 A）：
 * - 遇到 `say` op 且带 `variant.when.kind === 'window'`：先按窗口 currentKey 取 cases；
 *   无窗口或 currentKey 不在 cases 中则取 `base`。
 * - 遇到 `say` op 且带 `variant.when.kind === 'var'`：按 vars[id] ≥ gte 取 cases.cond；
 *   否则取 `base`。
 *
 * 跨场景关闭（按 R-014 三条必测规则之 ②）：
 * - 遇到 `scene` op 时先 `state.closeAllWindows()`，再切换 sceneId。
 *
 * 跨场景选项系统（按 R-014 三条必测规则之 ③）：
 * - 新的 window open op 会**覆盖**已存在的活跃窗口（即使 ttl > 0）。
 *
 * TTL 计数（按 R-014 三条必测规则之 ①）：
 * - 每次执行 say op 之前调 `state.tickSay()`；关闭的窗口会被剔除。
 */

import type {
  Op,
  ScriptProgram,
  SayBlock,
  SayPayload,
  Variant,
} from '../script/ops.ts';
import { GameState } from '../state/GameState.ts';

/** ScriptVM 推送给宿主的事件；宿主用它驱动渲染 */
export type VmEvent =
  | { type: 'chapter'; chapter: number; title: string }
  | { type: 'scene'; id: string; place: string; time: string; weather: string | null }
  | { type: 'bg'; name: string }
  | { type: 'bgm'; name: string }
  | { type: 'se'; name: string }
  | { type: 'sprite-show'; char: string; pose: string; slot: string }
  | { type: 'sprite-hide'; char: string }
  | { type: 'cg'; name: string }
  | {
      type: 'say';
      who: string | null;
      kind: string;
      text: string;
      lineId: string;
      /**
       * ★ 台本行号（1 起）—— **语音映射键**。
       * 音频按台本行号索引（`素材基准/音频/语音/日配任务_樱_*.jsonl`），
       * Director 拿它调 `AudioEngine.playVoice(line)`。
       * 注意与 `lineId` 区分：lineId 是「已读标记」用的运行期 id（`pc12_3`）。
       */
      line: number;
      note?: string;
    }
  | { type: 'fx'; fxType: string; params?: Record<string, number | string> }
  | { type: 'note'; text: string }
  | { type: 'window'; action: 'open' | 'close'; id: string; ttl?: number }
  | { type: 'var'; id: string; mode: 'set' | 'inc'; value: number | boolean | string }
  | { type: 'choice'; id: string; options: { key: string; text: string }[] }
  | { type: 'end' };

/** 玩家选择结果：被选中选项的 key（如 'A'） */
export type Choice = string;

export interface ScriptVMOptions {
  /** 由宿主提供；用于 lineId 哈希等可复现 */
  lineIdSeed?: number;
}

export class ScriptVM {
  private program: ScriptProgram;
  private state: GameState;
  private onEvent: (e: VmEvent) => void;
  private pc = 0;
  private _paused = false;
  /** 暂停时记住的 choice（pickChoice 时用它的 branch 写窗口） */
  private _pendingChoice: { id: string; options: { branch: string }[] } | null = null;
  /**
   * 玩家选中的分支 key，待 window open 时应用。
   * ★ 编译产物顺序是 choice → var → window open → 变体 say，
   *   pickChoice 时窗口**尚未打开**，所以 key 只能暂存、由 open 消费。
   */
  private _pendingBranch: string | null = null;
  private _lineCounter = 0;

  constructor(program: ScriptProgram, state: GameState, onEvent: (e: VmEvent) => void, _opts: ScriptVMOptions = {}) {
    this.program = program;
    this.state = state;
    this.onEvent = onEvent;
  }

  /** 当前 pc（用于存档） */
  getProgramCounter(): number {
    return this.pc;
  }

  /** 是否停在 choice 上 */
  isPaused(): boolean {
    return this._paused;
  }

  /**
   * 只移动程序计数器，**不执行任何指令**（读档用）。
   * 画面由宿主持 GameState 的舞台现状重建 —— 见 Director.rebuildStage()。
   */
  seekTo(pc: number): void {
    this.pc = Math.max(0, Math.min(pc, this.program.ops.length));
    this._paused = false;
    this._pendingChoice = null;
    this._pendingBranch = null;
  }

  /**
   * 顺序执行直到遇到 choice 或 end。返回 'choice'（在 choice 处停下） / 'end'（跑完）。
   */
  runUntilChoice(): 'choice' | 'end' {
    while (this.pc < this.program.ops.length) {
      const op = this.program.ops[this.pc++]!;
      const r = this.execOp(op);
      if (r === 'pause') {
        this._paused = true;
        return 'choice';
      }
      if (r === 'end') return 'end';
    }
    return 'end';
  }

  /**
   * **单步执行**：执行到「下一条 say 播完」就返回，供有画面的 Director 逐句推进。
   *
   * 与 `runUntilChoice()` 的区别（★ 关键）：
   * - `runUntilChoice()` 一路跑到 choice / end —— 终端 headless 用，中间 say 会被覆盖
   * - `stepToSay()` 每遇到一条 say 就停 —— 让打字机有机会把这句播完
   *
   * @returns 'say'（停在一条 say 上）/ 'choice'（停下等待选择）/ 'end'
   */
  stepToSay(): 'say' | 'choice' | 'end' {
    while (this.pc < this.program.ops.length) {
      const op = this.program.ops[this.pc]!;
      this.pc++;
      const r = this.execOp(op);
      if (r === 'pause') {
        this._paused = true;
        return 'choice';
      }
      if (r === 'end') return 'end';
      // ★ 非 say 指令（bg / bgm / sprite / scene / var / window / fx…）继续跑，
      //   它们只是铺垫；只有 say 才需要停下来让玩家读。
      if (op.op === 'say') return 'say';
    }
    return 'end';
  }

  /**
   * 玩家选中第 index 个选项（0-based）。从 R-014 看，options 顺序与 choice op 写入时一致。
   * **应用 effects 后**继续执行。
   */
  /**
   * 玩家做出选择，**只解除暂停态，不自动推进**。
   *
   * 推进方式由调用方决定：
   * - 终端 headless：接着调 `runUntilChoice()`
   * - 有画面的 Director：接着调 `stepToSay()`（逐句显示）
   *
   * ★ 选中项的 `branch` 会写入当前活跃窗口（`currentKey`）——
   *   之后窗口内的变体 say 才会按所选分支求值。这是「选项有区别」的关键链路。
   */
  pickChoice(index: number): void {
    if (!this._paused) {
      throw new Error('ScriptVM 未处于 choice 暂停态');
    }
    this._paused = false;
    const pending = this._pendingChoice;
    this._pendingChoice = null;
    const opt = pending?.options[index];
    // ★ 窗口尚未打开（window open op 在 choice 之后），先暂存，open 时消费
    this._pendingBranch = opt?.branch ?? null;
  }

  // ============================================================
  // 单条指令执行
  // ============================================================

  private execOp(op: Op): 'continue' | 'pause' | 'end' {
    switch (op.op) {
      case 'chapter':
        // 章节头是 metadata，不发独立事件（由 scene 头带出）
        this.state.currentChapter = op.chapter;
        return 'continue';

      case 'scene': {
        // ★ 跨场景无条件关闭所有窗口（R-014 三条必测规则之 ②）
        const closed = this.state.closeAllWindows();
        for (const id of closed) {
          this.onEvent({ type: 'window', action: 'close', id });
        }
        // ★ 2026-09-18 修：`chapters[]` 是从 **scene op** 收集的，而程序里**没有 `chapter` op**
        //   （实测 script.json：chapter 0 条）⇒ `currentChapter` 从来没人写，永远是 0
        //   ⇒ 顶部状态条恒显示「第 1 章」、存档里记的章号也恒为 1。
        //   章号本来就在 scene op 上，这里补上即可（也供「跳章」用）。
        this.state.currentChapter = op.chapter;
        this.state.currentSceneId = op.id;
        this.state.currentCg = null; // CG 随场景切换收起（N-014）→ 读档不复活
        this.onEvent({
          type: 'scene',
          id: op.id,
          place: op.place,
          time: op.time,
          weather: op.weather,
        });
        return 'continue';
      }

      case 'bg':
        this.state.setBg(op.name);
        this.onEvent({ type: 'bg', name: op.name });
        return 'continue';

      case 'bgm':
        this.state.setBgm(op.name);
        this.onEvent({ type: 'bgm', name: op.name });
        return 'continue';

      case 'se':
        this.onEvent({ type: 'se', name: op.name });
        return 'continue';

      case 'var':
        this.state.setVar(op.id, op.mode, op.value);
        this.onEvent({ type: 'var', id: op.id, mode: op.mode, value: op.value });
        return 'continue';

      case 'sprite':
        if (op.action === 'hide') {
          this.state.hideSprite(op.char);
          this.onEvent({ type: 'sprite-hide', char: op.char });
        } else {
          this.state.showSprite(op.char, op.pose, op.slot);
          this.onEvent({ type: 'sprite-show', char: op.char, pose: op.pose, slot: op.slot });
        }
        return 'continue';

      case 'cg':
        // ★ `CG：无`＝收起（N-014）：状态清空 → 读档不会复活已收的 CG
        this.state.currentCg = op.name === '无' ? null : op.name;
        this.onEvent({ type: 'cg', name: op.name });
        return 'continue';

      case 'fx':
        this.onEvent({ type: 'fx', fxType: op.type, params: op.params });
        return 'continue';

      case 'note':
        this.onEvent({ type: 'note', text: op.text });
        return 'continue';

      case 'window':
        if (op.action === 'open') {
          // ★ 同场景已存在活跃窗口时覆盖（最多 1 个）
          this.state.openWindow(op.id, op.ttl ?? 0, 'scene');
          // 应用玩家刚选的分支（pickChoice 暂存、此处消费 —— 一次性）
          if (this._pendingBranch) {
            this.state.setWindowKey(op.id, this._pendingBranch);
            this._pendingBranch = null;
          }
          this.onEvent({ type: 'window', action: 'open', id: op.id, ttl: op.ttl });
        } else {
          this.state.closeWindow(op.id);
          this.onEvent({ type: 'window', action: 'close', id: op.id });
        }
        return 'continue';

      case 'choice':
        // 记住暂停中的 choice —— pickChoice 时用 options[index].branch 写窗口
        this._pendingChoice = { id: op.id, options: op.options };
        this.onEvent({
          type: 'choice',
          id: op.id,
          options: op.options.map((o) => ({ key: o.key, text: o.text })),
        });
        return 'pause';

      case 'say': {
        // ★ 先按当前窗口状态求值，再消费 TTL ——
        //   若先 tick，最后一条变体 say 会因窗口被 TTL 关闭而错走 base 分支
        const payload = this.resolveVariant(op.variant, op.who, op.kind, op.blocks, op.line);
        const closed = this.state.tickSay();
        for (const id of closed) {
          this.onEvent({ type: 'window', action: 'close', id });
        }
        // 拼正文
        // ★ 2026-09-19（主人反馈「括号里的内容不显示」）：**括注（stage）要进正文** ——
        //   旧行为只拼 b.text，带正文的括注被整个丢掉（孤立括注本来就还原成正文所以看得到）。
        //   现在把 stage 还原成 `（…）` 行放在正文前，渲染层按 ASIDE_RE 走括注暗色样式。
        //   ⚠️ 只影响显示文本；语音按 line 映射、与文本内容无关，不受影响。
        const text = payload.blocks
          .map((b) => (b.stage ? `（${b.stage}）\n${b.text}` : b.text))
          .join('\n')
          .trim();
        const lineId = `pc${this.pc}_${this._lineCounter++}`;
        if (text) {
          this.state.markRead(lineId);
        }
        this.onEvent({
          type: 'say',
          who: payload.who,
          kind: payload.kind,
          text,
          lineId,
          line: payload.line,
        });
        return 'continue';
      }

      case 'end':
        this.onEvent({ type: 'end' });
        return 'end';
    }
  }

  /**
   * 求值 variant：返回最终 say 文本所用的 payload。
   * - kind='speech' 的 who 仍可能是 null（旁白）；用 outer.who 兜底。
   * - 注意：variant 命中时，**blocks 替换**，但 who / kind 也按 R-014 完整替换。
   */
  private resolveVariant(
    variant: Variant | undefined,
    outerWho: string | null,
    outerKind: SayPayload['kind'],
    outerBlocks: SayBlock[],
    outerLine: number,
  ): SayPayload {
    if (!variant) {
      return { who: outerWho, kind: outerKind, blocks: outerBlocks, line: outerLine };
    }
    let picked: SayPayload | undefined;
    if (variant.when.kind === 'window') {
      const w = this.state.getActiveWindow(variant.when.id);
      const key = w?.currentKey ?? null;
      if (key && variant.cases[key]) picked = variant.cases[key];
    } else {
      // var
      const v = this.state.getVar(variant.when.id, 0);
      const n = typeof v === 'number' ? v : 0;
      if (n >= (variant.when.gte ?? 0) && variant.cases['cond']) {
        picked = variant.cases['cond'];
      }
    }
    if (picked) return picked;
    if (variant.base) return variant.base;
    return { who: outerWho, kind: outerKind, blocks: outerBlocks, line: outerLine };
  }
}
