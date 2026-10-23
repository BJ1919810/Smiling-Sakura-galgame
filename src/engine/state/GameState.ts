/**
 * 《笑樱》GameState
 *
 * 状态机的唯一数据源：剧情进度 + 舞台现状 + 读档相关。
 * 纯 TS，不依赖 DOM / WebGL / 任何渲染层 —— 可被 Node 终端调用，可被 Tauri 桌面调用，可被 Web 浏览器调用。
 *
 * 设计准则（详见 `技术设计.md` §7.2）：
 * - 舞台现状（背景 / BGM / 立绘）属于状态，**不是表现** —— 必须进存档，否则读档画面是空的。
 * - 窗口 TTL 按**台词条数**递减，不是秒数。
 * - 跨场景无条件关闭所有窗口。
 *
 * 使用：
 *   const state = new GameState();
 *   state.openWindow('W-01', 3, 'scene');
 *   state.tickSay();           // 消费 1 条 say
 *   state.closeAllWindows();   // 场景切换
 */

// 类型在本文件内导出（见下方）



/** 窗口在场景切换时会被强制关闭；这里是记录用 */
export interface WindowState {
  /** 当前剩余可消费条数；<= 0 时自动关闭 */
  remaining: number;
  /** 当前分支值（窗口打开后第一个 say 决定） */
  currentKey: string | null;
  /** 场景内 / 跨场景（本作仅用 scene） */
  scope: 'scene' | 'global';
}

export interface GameStateSnapshot {
  version: 1;
  savedAt: number;
  playTimeMs: number;
  pc: number;
  vars: Record<string, number | boolean | string>;
  flags: Record<string, boolean>;
  currentChapter: number;
  currentSceneId: string;
  currentBg: string | null;
  currentBgm: string | null;
  sprites: Record<string, { pose: string; slot: string }>;
  currentCg: string | null;
  readLineIds: string[];
  windows: Record<string, WindowState>;
}

export class GameState {
  // —— 剧情进度 ——
  pc = 0;
  currentChapter = 0;
  currentSceneId = '';

  // —— 变量与标志 ——
  vars: Record<string, number | boolean | string> = {};
  flags: Record<string, boolean> = {};

  // —— 舞台现状 ——
  currentBg: string | null = null;
  currentBgm: string | null = null;
  sprites: Record<string, { pose: string; slot: string }> = {};
  currentCg: string | null = null;

  // —— 阅读记录 ——
  readLineIds: Set<string> = new Set();

  // —— 窗口系统 ——
  windows: Record<string, WindowState> = {};

  // —— 时间 ——
  startedAt = Date.now();
  playTimeMs = 0;

  // ============================================================
  // 变量
  // ============================================================

  /**
   * 求变量值。var 不存在时返回 `defaultValue`（默认 0），方便 `if aff.sakura >= 3` 这种判空。
   */
  getVar(id: string, defaultValue: number | boolean | string = 0): number | boolean | string {
    return this.vars[id] ?? defaultValue;
  }

  /**
   * 执行 `var` 指令。
   * - mode='set'：直接覆盖
   * - mode='inc'：旧值必须为 number，相加（亦支持 value 为负数）
   */
  setVar(id: string, mode: 'set' | 'inc', value: number | boolean | string): void {
    if (mode === 'set') {
      this.vars[id] = value;
      return;
    }
    // inc
    const cur = this.vars[id];
    const curNum = typeof cur === 'number' ? cur : 0;
    const delta = typeof value === 'number' ? value : 0;
    this.vars[id] = curNum + delta;
  }

  // ============================================================
  // 窗口系统（技术设计.md 附录 A）
  // ============================================================

  /**
   * 打开一个窗口。**同场景已存在活跃窗口时覆盖之**（满足 R-014 三条必测规则之 ③）。
   */
  openWindow(id: string, ttl: number, scope: 'scene' | 'global' = 'scene'): void {
    this.windows[id] = { remaining: ttl, currentKey: null, scope };
  }

  /**
   * 设置当前窗口的分支值（窗口打开后、第一个变体 say 求值时由 ScriptVM 写入）。
   */
  setWindowKey(id: string, key: string): void {
    const w = this.windows[id];
    if (w) w.currentKey = key;
  }

  /**
   * 给**当前唯一活跃窗口**设分支值（同场景最多 1 个活跃窗口，见 R-014 规则③）。
   * 玩家做出选择时由 ScriptVM.pickChoice 调用 —— 选项的 `branch` 写进窗口，
   * 之后窗口内的变体 say 才会按所选分支求值。
   */
  setActiveWindowKey(key: string): void {
    for (const w of Object.values(this.windows)) {
      w.currentKey = key;
      return; // 最多 1 个活跃窗口
    }
  }

  /**
   * 查询当前活跃窗口；最多 1 个（求值时按 id 取）。
   */
  getActiveWindow(id: string): WindowState | undefined {
    return this.windows[id];
  }

  /**
   * 消费 1 条 say：所有**场景内**窗口 remaining -1；归零则自动关闭。
   * **跨场景**窗口不参与计数（防御性：本作只用了 scene）。
   * 返回本次消费后**已关闭**的窗口 id 列表，供 ScriptVM 触发 close 副作用。
   */
  tickSay(): string[] {
    const closed: string[] = [];
    for (const [id, w] of Object.entries(this.windows)) {
      if (w.scope !== 'scene') continue;
      w.remaining -= 1;
      if (w.remaining <= 0) {
        closed.push(id);
      }
    }
    for (const id of closed) delete this.windows[id];
    return closed;
  }

  /** 显式关闭窗口（如 window close op） */
  closeWindow(id: string): void {
    delete this.windows[id];
  }

  /**
   * 跨场景无条件关闭所有窗口（R-014 三条必测规则之 ②）。
   * **必须在每次 scene op 之前调用**。
   */
  closeAllWindows(): string[] {
    const ids = Object.keys(this.windows);
    this.windows = {};
    return ids;
  }

  // ============================================================
  // 舞台现状
  // ============================================================

  setBg(name: string): void {
    this.currentBg = name;
  }

  setBgm(name: string): void {
    this.currentBgm = name;
  }

  showSprite(char: string, pose: string, slot: string): void {
    this.sprites[char] = { pose, slot };
  }

  hideSprite(char: string): void {
    delete this.sprites[char];
  }

  // ============================================================
  // 阅读记录
  // ============================================================

  markRead(lineId: string): void {
    this.readLineIds.add(lineId);
  }

  hasRead(lineId: string): boolean {
    return this.readLineIds.has(lineId);
  }

  // ============================================================
  // 存档
  // ============================================================

  snapshot(pc: number): GameStateSnapshot {
    return {
      version: 1,
      savedAt: Date.now(),
      playTimeMs: this.playTimeMs,
      pc,
      vars: { ...this.vars },
      flags: { ...this.flags },
      currentChapter: this.currentChapter,
      currentSceneId: this.currentSceneId,
      currentBg: this.currentBg,
      currentBgm: this.currentBgm,
      sprites: structuredClone(this.sprites),
      currentCg: this.currentCg,
      readLineIds: Array.from(this.readLineIds),
      windows: structuredClone(this.windows),
    };
  }

  restore(snap: GameStateSnapshot): void {
    if (snap.version !== 1) {
      throw new Error(`存档版本不兼容：${snap.version}`);
    }
    this.playTimeMs = snap.playTimeMs;
    this.vars = { ...snap.vars };
    this.flags = { ...snap.flags };
    this.currentChapter = snap.currentChapter;
    this.currentSceneId = snap.currentSceneId;
    this.currentBg = snap.currentBg;
    this.currentBgm = snap.currentBgm;
    this.sprites = structuredClone(snap.sprites);
    this.currentCg = snap.currentCg;
    this.readLineIds = new Set(snap.readLineIds);
    this.windows = structuredClone(snap.windows);
  }
}
