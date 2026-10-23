/**
 * 指令流的 Zod 校验 —— 编译期与运行期共用。
 *
 * 用途：
 * 1. **编译期**：`script.json` 产出前自检，字段写错立刻报错（而非运行时崩）
 * 2. **运行期**：加载存档 / 外部剧本时的防御性校验
 *
 * 字段定义与 `ops.ts` 严格一一对应，来源见 `技术设计.md` §六（原《台本-程序对接.md》已删）。
 */
import { z } from 'zod';

// —— 基础 ——

export const SayBlockSchema = z.object({
  text: z.string().min(1, '文本框不能为空'),
  stage: z.string().optional(),
});

export const SayKindSchema = z.enum(['narration', 'inner', 'speech']);

export const VariantWhenSchema = z.union([
  z.object({
    kind: z.literal('window'),
    id: z.string().min(1),
    in: z.record(z.string()),
  }),
  z.object({
    kind: z.literal('var'),
    id: z.string().min(1),
    gte: z.number().optional(),
    lte: z.number().optional(),
    eq: z.number().optional(),
  }),
]);

/** 一条 say 的完整载荷 —— who / kind / blocks 都会随分支变化（见 ops.ts 的说明） */
export const SayPayloadSchema = z.object({
  who: z.string().nullable(),
  kind: SayKindSchema,
  blocks: z.array(SayBlockSchema).min(1),
  /** 台本行号（1 起）—— 语音映射键；见 ops.ts 的 SayPayload.line */
  line: z.number().int().positive(),
});

export const VariantSchema = z.object({
  when: VariantWhenSchema,
  cases: z.record(SayPayloadSchema),
  /** 省略时取 say 外层的 who / kind / blocks */
  base: SayPayloadSchema.optional(),
});

export const VarEffectSchema = z.object({
  var: z.string().min(1),
  mode: z.enum(['set', 'inc']),
  value: z.union([z.number(), z.boolean()]),
});

export const TransitionSchema = z.union([
  z.object({ type: z.literal('none') }),
  z.object({ type: z.literal('fade'), duration: z.number().nonnegative() }),
  z.object({ type: z.literal('dissolve'), duration: z.number().nonnegative() }),
  z.object({
    type: z.literal('wipe'),
    duration: z.number().nonnegative(),
    direction: z.enum(['left', 'right', 'up', 'down']),
  }),
  z.object({ type: z.literal('sakura'), duration: z.number().nonnegative() }),
]);

export const ChoiceOptionSchema = z.object({
  key: z.string().min(1),
  text: z.string().min(1),
  /** 窗口分支 key（`→ W-02·C` 的 `·C`） */
  branch: z.string().min(1),
  effects: z.array(VarEffectSchema),
});

// —— 指令 ——

/**
 * 全量 op。
 *
 * 注意 `var` 分支用 `mode` 而非契约原文的 `op`（JSON 键冲突），
 * 详见 技术设计.md 附录 B。
 */
export const OpSchema = z.discriminatedUnion('op', [
  z.object({
    op: z.literal('chapter'),
    chapter: z.number().int().nonnegative(),
    title: z.string(),
  }),
  z.object({
    op: z.literal('scene'),
    id: z.string().min(1),
    chapter: z.number().int().nonnegative(),
    title: z.string(),
    place: z.string().min(1),
    time: z.string(),
    weather: z.string().nullable(),
  }),
  z.object({
    op: z.literal('bg'),
    name: z.string().min(1),
    transition: TransitionSchema.optional(),
  }),
  z.object({
    op: z.literal('bgm'),
    name: z.string().min(1),
    fade: z.number().nonnegative().optional(),
  }),
  z.object({ op: z.literal('se'), name: z.string().min(1) }),
  z.object({
    op: z.literal('sprite'),
    char: z.string().min(1),
    pose: z.string(),
    slot: z.string().min(1),
    action: z.enum(['show', 'update', 'hide']),
  }),
  z.object({ op: z.literal('cg'), name: z.string().min(1) }),
  z.object({
    op: z.literal('say'),
    who: z.string().nullable(),
    kind: SayKindSchema,
    blocks: z.array(SayBlockSchema).min(1),
    /** 台本行号（1 起）—— 语音映射键；加进 schema 才不会被 z.object 的 strip 丢掉 */
    line: z.number().int().positive(),
    variant: VariantSchema.optional(),
  }),
  z.object({
    op: z.literal('choice'),
    id: z.string().min(1),
    // 台本约定 2–4 项（见 技术设计.md 附录 A）
    options: z.array(ChoiceOptionSchema).min(2).max(4),
  }),
  z.object({
    op: z.literal('window'),
    action: z.enum(['open', 'close']),
    id: z.string().min(1),
    ttl: z.number().int().positive().optional(),
    scope: z.string().optional(),
  }),
  z.object({
    op: z.literal('var'),
    id: z.string().min(1),
    mode: z.enum(['set', 'inc']),
    value: z.union([z.number(), z.boolean()]),
  }),
  z.object({
    op: z.literal('fx'),
    type: z.string().min(1),
    params: z.record(z.union([z.number(), z.string()])).optional(),
  }),
  z.object({ op: z.literal('note'), text: z.string() }),
  z.object({ op: z.literal('end') }),
]);

// —— 编译产物 ——

export const ScriptProgramSchema = z.object({
  version: z.number().int().positive(),
  generatedAt: z.string(),
  source: z.string(),
  chapters: z.array(
    z.object({
      index: z.number().int().nonnegative(),
      title: z.string(),
      startPc: z.number().int().nonnegative(),
    }),
  ),
  ops: z.array(OpSchema),
});

/** 当前 `script.json` 的 schema 版本；结构变更时 +1 */
export const SCRIPT_VERSION = 1;

export type OpParsed = z.infer<typeof OpSchema>;
export type ScriptProgramParsed = z.infer<typeof ScriptProgramSchema>;
