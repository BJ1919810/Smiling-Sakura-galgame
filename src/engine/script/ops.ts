/**
 * 《笑樱》剧本指令流（script.json）—— 类型定义
 *
 * ★ 本文件的字段定义**遵循台本开头的「记号约定」与 `技术设计.md` §六**（接口唯一依据）。
 *   （原《台本-程序对接.md》已于 2026-09-16 删除，仍活跃的契约存续要点见 `技术设计.md` 附录 B。）
 *   引擎侧不擅自改动字段名；如需变更，先在契约文档里改，再动这里。
 *
 * 设计约束（技术设计.md §3.1）：
 * - 本目录属**引擎核心**，禁止依赖 react / pixi.js / gsap
 * - 所有 Op 扁平、可 JSON 序列化、可通过 Zod 校验
 */

/** 一个文本框 */
export interface SayBlock {
  /** 正文 */
  text: string;
  /** 括注行内容（语气 / 动作 / 表情提示）；无则省略 */
  stage?: string;
}

/**
 * 文本性质 —— 决定姓名框与排版（技术设计.md §9.3）
 *
 * 注意：`narration` 与 `inner` 的 `who` 恒为 null，
 * **不会出现「我」这个人名框**。
 */
export type SayKind =
  | 'narration' // 【我】主角叙述（地の文）→ 无姓名框
  | 'inner' // 【我】（心声）→ 无姓名框，斜体 + 降明度
  | 'speech'; // 【角色】台词 → 显示姓名框

/** 差分取值条件 */
export type VariantWhen =
  | { kind: 'window'; id: string; in: Record<string, string> }
  | { kind: 'var'; id: string; gte?: number; lte?: number; eq?: number };

/**
 * 一条 say 的完整载荷。
 *
 * ★ 为什么 `cases` 必须存**完整载荷**而不只是 `blocks`：
 *   实测台本 W-01 / W-05 的三条分支，**说话人本身就会变**——
 *   ```
 *   ▸A  龙司(speech) | 我(narration) | 樱(speech)
 *   ▸B  龙司(speech) | 我(narration) | 樱(speech)
 *   ▸C  我(narration) | 樱(speech)    | 我(narration)   ← 第 1 条换人了
 *   ```
 *   若只让 `blocks` 随分支变、`who`/`kind` 固定在外层，
 *   C 版第 1 条会**顶着「龙司」的姓名框播出叙述文**——语义直接错乱。
 *   详见 技术设计.md 附录 B（原 R-014：tick 按 say 计数）。
 */
export interface SayPayload {
  who: string | null;
  kind: SayKind;
  blocks: SayBlock[];
  /**
   * ★ 台本行号（1 起）—— **语音映射键**。
   *
   * 日配音频按台本行号索引（`素材基准/音频/语音/日配任务_樱_*.jsonl` 的 `line` 字段），
   * `AudioEngine.playVoice(line)` 拿它去 manifest.voice 里查文件。
   * 必须在编译期带上：运行时的 say 序号会被 `▸` 差分 / 条件块打乱，不能当键用。
   */
  line: number;
}

/**
 * 文本差分 —— 覆盖 `▸A / ▸B / ▸C` 与 `▲ 变量` 条件块两种用法。
 *
 * 求值（台本侧契约 §3.2）：活跃窗口内 → `cases[当前值]`；
 * 窗口已关闭 / 变量条件不满足 → `base`。
 */
export interface Variant {
  when: VariantWhen;
  /** 分支 → 完整载荷；键为窗口取值 'A' | 'B' | 'C' */
  cases: Record<string, SayPayload>;
  /** 基准版；省略时取 `say` 外层的 who / kind / blocks */
  base?: SayPayload;
}

/** 变量写入模式（R-013 确认） */
export type VarMode = 'set' | 'inc';

/** 选项被选中时写入的效果；多条 effects 写进 ops 后立即执行 */
export interface VarEffect {
  /** 变量名，如 'S-warm'；或窗口值槽 'W-01' */
  var: string;
  mode: VarMode;
  /** 与 `var` 指令的 value 字段保持一致（schema 当前允许 number | boolean） */
  value: number | boolean;
}

/** 转场；未指定时由引擎取默认值 */
export type Transition =
  | { type: 'none' }
  | { type: 'fade'; duration: number }
  | { type: 'dissolve'; duration: number }
  | { type: 'wipe'; duration: number; direction: 'left' | 'right' | 'up' | 'down' }
  | { type: 'sakura'; duration: number };

/** 立绘动作 */
export type SpriteAction = 'show' | 'update' | 'hide';

export interface ChoiceOption {
  /** 'A' | 'B' | 'C' …（台本约定 2–4 项） */
  key: string;
  /** 选项文本（编译时去掉外层「」） */
  text: string;
  /**
   * 窗口分支 key（`→ W-02·C` 的 `·C`）。
   * 通常与 key 相同，但**契约上允许不同**——分支归属以本字段为准。
   */
  branch: string;
  effects: VarEffect[];
}

/**
 * 指令流的最小单元。
 *
 * 字段顺序与命名严格对齐 `技术设计.md` §六 的 op 表。
 */
export type Op =
  // —— 场景与资源 ——
  | {
      op: 'chapter';
      chapter: number;
      title: string;
    }
  | {
      op: 'scene';
      id: string; // 如 'CH1-SC03'
      chapter: number;
      title: string;
      place: string;
      time: string;
      weather: string | null;
    }
  | { op: 'bg'; name: string; transition?: Transition }
  /** `name === '无'` 表示**停止** BGM（台本中出现 2 次） */
  | { op: 'bgm'; name: string; fade?: number }
  | { op: 'se'; name: string }
  // —— 画面 ——
  | {
      op: 'sprite';
      char: string;
      pose: string;
      /** 舞台锚点；台本写中文站位，由 assets.manifest.json 映射为 slot */
      slot: string;
      action: SpriteAction;
    }
  | { op: 'cg'; name: string }
  // —— 文本 ——
  | {
      op: 'say';
      who: string | null;
      kind: SayKind;
      blocks: SayBlock[];
      /** 台本行号（1 起）—— 语音映射键；见 SayPayload.line */
      line: number;
      variant?: Variant;
    }
  // —— 选项系统 ——
  | { op: 'choice'; id: string; options: ChoiceOption[] }
  | {
      op: 'window';
      action: 'open' | 'close';
      id: string;
      /** 仅 open：TTL，按**被消费的 say 条数**计 */
      ttl?: number;
      scope?: string;
    }
  | {
      op: 'var';
      id: string;
      /**
       * 注意：台本侧契约原文写作 `op: set|inc`，但 JSON 中**会与外层 `op` 键冲突**，
       * 故此处用 `mode`。（原 R-013，2026-09-10 双向确认）
       */
      mode: 'set' | 'inc';
      value: number | boolean;
    }
  // —— 演出 ——
  | { op: 'fx'; type: string; params?: Record<string, number | string> }
  /** `※` 自由文本中无法映射为已知 fx 的，**原样透传**，不报错 */
  | { op: 'note'; text: string }
  | { op: 'end' };

/** 编译产物根结构 */
export interface ScriptProgram {
  version: number;
  generatedAt: string;
  /** 来源台本文件名 */
  source: string;
  chapters: { index: number; title: string; startPc: number }[];
  ops: Op[];
}

/** 编译报告（`compile-report.txt` 的结构化形式） */
export interface CompileReport {
  ok: boolean;
  stats: {
    lines: number;
    scenes: number;
    chapters: number;
    says: number;
    sprites: number;
    choices: number;
    windows: number;
    ops: number;
  };
  errors: CompileIssue[];
  warnings: CompileIssue[];
}

export interface CompileIssue {
  /** 台本行号（1 起） */
  line: number;
  code: string;
  message: string;
  /**
   * 严重级别。缺省 = warning。
   * 用于「写法本身合法、但会静默改变演出/事件」的那类问题 —— 这类必须在编译期拦住，
   * 不能只警告后放行（R-022 的教训：静默丢拍比报错可怕得多）。
   */
  level?: 'error' | 'warning';
}
