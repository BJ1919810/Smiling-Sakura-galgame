/**
 * 台本解析器 —— `笑樱_galgame台本.md` → `Op[]`
 *
 * 规则来源（接口唯一依据）：
 * - 台本开头的「记号约定」（作者源格式的唯一依据）
 * - C-1 ～ C-5 约定（§5.5 / R-012）
 *
 * 设计要点：
 * - 纯函数式：输入文本，输出指令流 + 报告，不碰文件系统（读写在 index.ts）
 * - **容错优先**：能解析就解析，不确定的降级为 `note` 并记警告，绝不静默丢弃内容
 */

import type {
  Op,
  SayBlock,
  SayPayload,
  SayKind,
  VarEffect,
  CompileIssue,
} from '../../src/engine/script/ops.ts';

// ============================ 记号正则 ============================

const RE_CHAPTER = /^#+\s*第\s*(\d+)\s*章[　\s]+(.+?)\s*$/;
const RE_SCENE = /^■\s*SCENE\s*(\d+)\s*｜\s*(.+)$/;
const RE_RESOURCE = /^(BG|BGM|SE)：(.*)$/;
const RE_SPRITE = /^■\s*立绘：(.*)$/;
const RE_CG = /^■\s*CG：(.*)$/;
const RE_STAR = /^※\s*(.*)$/;
const RE_CHOICE = /^◆\s*选项[　\s]*([A-Z]-\d+)(?:\s*｜\s*(.*))?$/;
const RE_CHOICE_ITEM = /^[\s　]*([A-D])｜(.*?)　*→\s*(W-\d+)·([A-D])\s*(.*)$/;
/** 选项行末尾的可选 ▲ 效果段，例：　　▲ S-warm +1　　▲ flag.kept = true */
const RE_VAR_EFFECT = /▲\s*([A-Za-z0-9_-]+)\s*([+\-=])\s*(\d+|true|false)\s*/g;
const RE_WIN_OPEN = /^〔(W-\d+)｜窗口\s*(\d+)\s*条台词(?:｜\s*(.*?))?〕$/;
const RE_WIN_CLOSE = /^〔\/(W-\d+)〕$/;
const RE_VARIANT = /^▸\s*([A-D]|base)\s*$/;
const RE_COND = /^▲\s*变量\s*([A-Za-z0-9_-]+)\s*(≥|<=|≤|>=|==)\s*(\d+)\s*(?:｜\s*(.*))?$/;
const RE_SAY = /^【([^】]+)】(.*)$/;
/** 整行就是一个括注 —— 台本中「孤立括注」共 20 处（见 R-014 附带二） */
const RE_NOTE_ONLY = /^（(.+)）$/;

/**
 * 已知角色表（与 `素材需求表.md` 1.1 一致）。
 * 立绘行若出现表外名字，说明那是**动作画面描述**而非角色
 * （例：L3400「两人各拆一颗糖」），降级为演出提示并告警。
 */
const KNOWN_CHARS = new Set(['龙司', '樱', '阳太', '亚里沙', '九条院', '泽渡']);

/** 行首标记：出现即表示「上一条 say 结束」 */
const MARK_START = /^(【|■|`|#|◆|▸|〔|▲|%%|---)/;

// ============================ 工具 ============================

/** 去掉 markdown 反引号包裹 */
function unwrap(line: string): string {
  const t = line.trim();
  if (t.length >= 2 && t.startsWith('`') && t.endsWith('`')) return t.slice(1, -1).trim();
  return t;
}

/** 去掉最外层「」；没有则原样返回 */
function stripQuotes(s: string): string {
  const t = s.trim();
  if (t.startsWith('「') && t.endsWith('」') && t.length >= 2) return t.slice(1, -1);
  return t;
}

/** 由角色标签推出 say 的 who / kind */
function resolveSpeaker(tag: string, inlineRest: string): { who: string | null; kind: SayKind } {
  const isInner = inlineRest.trim().startsWith('（心声）');
  if (tag === '我') return { who: null, kind: isInner ? 'inner' : 'narration' };
  return { who: tag, kind: 'speech' };
}

/**
 * 把一个标签下收集到的行切成 blocks。
 *
 * 切分规则（对齐契约 §2.2 的示例）：
 * - **括注行**开启一个新 block，作为该 block 的 `stage`
 * - **空行**结束当前 block
 * - 孤立括注（后面没有正文）→ 独立 block，`text` 保留原括注（含括号）
 */
function buildBlocks(body: string[], sayLine: number, issues: CompileIssue[]): SayBlock[] {
  const blocks: SayBlock[] = [];
  let cur: SayBlock | null = null;

  const flush = () => {
    if (!cur) return;
    if (!cur.text) {
      // 孤立括注：把 stage 还原为正文（保留括号），让渲染层用括注样式呈现
      blocks.push({ text: `（${cur.stage}）` });
    } else {
      blocks.push(cur);
    }
    cur = null;
  };

  for (const raw of body) {
    const s = raw.trim();
    if (s === '') {
      flush();
      continue;
    }
    const note = RE_NOTE_ONLY.exec(s);
    if (note) {
      flush();
      cur = { stage: note[1], text: '' };
      continue;
    }
    if (cur === null) cur = { text: '' };
    cur.text = cur.text ? `${cur.text}\n${s}` : s;
  }
  flush();

  if (blocks.length === 0) {
    issues.push({ line: sayLine, code: 'EMPTY_SAY', message: '该 say 没有任何内容，已跳过' });
  }
  return blocks;
}

/**
 * `※` 自由文本 → fx 类型（白名单见 R-002），映射不中则 note。
 *
 * ★ 2026-09-15（主人令：「程序死跟台本走，没有任何隐藏的小巧思」）
 *   把原先由**引擎自动触发**的三类效果**收进白名单**，改由台本显式写：
 *     · `雀跃`        → 立绘跳一下   （原：立绘括注命中情绪词表就自动跳 —— 已从 Director 删除）
 *     · `白闪`        → 画面白闪     （原：每个 ■ CG 都自动闪 —— 已删）
 *     · `花瓣密/疏/停` → 花瓣粒子密度 （原：场景标题含「樱」自动加倍 —— 已删）
 *   设计依据见 技术设计.md 附录 B。
 *
 * ⚠️ 新增判定一律**追加在既有判定之后**，保证旧台本落点不变。
 * ⚠️ 本函数在 `tools/台本编排器.html` 里有一份**手抄复刻**，改动必须同步。
 */
function starToFx(text: string):
  | { op: 'fx'; type: string; params?: Record<string, number | string> }
  | { op: 'note'; text: string } {
  const t = text;
  if (/FADE\s*OUT/i.test(t)) return { op: 'fx', type: 'fadeOut' };
  if (t.startsWith('特写')) return { op: 'fx', type: 'push' };
  if (t.startsWith('镜头') || t.startsWith('远景') || t.startsWith('镜头切')) return { op: 'fx', type: 'camera' };
  if (t.includes('慢镜')) return { op: 'fx', type: 'blur' };
  if (/(沉默|静默|静止|停留|静止一拍|隔了三秒|时间流逝)/.test(t)) return { op: 'fx', type: 'beat' };
  // ── 原「引擎自动」效果，收编为显式指令 ──────────────────────────
  if (t.includes('花瓣停') || t.includes('花瓣无')) return { op: 'fx', type: 'petalStop' };
  if (t.includes('花瓣密')) return { op: 'fx', type: 'petalDense' };
  if (t.includes('花瓣疏')) return { op: 'fx', type: 'petalSparse' };
  if (t.includes('白闪')) return { op: 'fx', type: 'flash' };
  if (t.includes('雀跃')) {
    // 可选指定角色：`※ 雀跃：樱`；不写则台上全部立绘一起跳
    const who = /雀跃\s*[：:]\s*([^\s　，。、]+)/.exec(t)?.[1];
    return who ? { op: 'fx', type: 'hop', params: { char: who } } : { op: 'fx', type: 'hop' };
  }
  return { op: 'note', text: t };
}

// ============================ 主解析 ============================

export interface ParseResult {
  ops: Op[];
  issues: CompileIssue[];
  stats: {
    lines: number;
    chapters: number;
    scenes: number;
    says: number;
    sprites: number;
    choices: number;
    windows: number;
    notes: number;
  };
}

export function parseScript(text: string): ParseResult {
  const lines = text.split(/\r?\n/);
  const ops: Op[] = [];
  const issues: CompileIssue[] = [];

  const stats = {
    lines: lines.length,
    chapters: 0,
    scenes: 0,
    says: 0,
    sprites: 0,
    choices: 0,
    windows: 0,
    notes: 0,
  };

  let chapter = 0;
  let chapterTitle = '';
  let lastPlace = ''; // 处理「同前」
  /** 场景跨度追踪：标题含「→」（一景两地点）但中途没换 BG → 出口前发 warning（2026-09-12 主人实测踩中） */
  const sceneSpans: { line: number; place: string; bgCount: number }[] = [];
  const spriteSlots = new Map<string, string>(); // 角色 → 上次 slot（位置省略时沿用）

  // 收集「标签 + 其后的行」，直到下一个行首标记
  const collectBody = (start: number): { body: string[]; next: number } => {
    const body: string[] = [];
    let j = start;
    while (j < lines.length) {
      const s = lines[j].trim();
      if (MARK_START.test(s) || RE_WIN_CLOSE.test(unwrap(s))) break;
      body.push(lines[j]);
      j++;
    }
    return { body, next: j };
  };

  // —— 解析一整段 say 序列（`▸` 变体块 / 条件块 复用） ——
  const parseSayRun = (start: number): { payloads: SayPayload[]; next: number } => {
    const payloads: SayPayload[] = [];
    let j = start;
    while (j < lines.length) {
      const t = unwrap(lines[j]);
      if (t === '') {
        j++;
        continue;
      }
      if (RE_VARIANT.test(t) || RE_WIN_CLOSE.test(t) || RE_WIN_OPEN.test(t)) break;
      if (!t.startsWith('【')) {
        // 变体块内出现非 say 行：停止（块边界，见 C-1）
        break;
      }
      const m = RE_SAY.exec(t)!;
      const { body, next } = collectBody(j + 1);
      const { who, kind } = resolveSpeaker(m[1].trim(), m[2]);
      const blocks = buildBlocks(body, j + 1, issues);
      if (blocks.length === 0) {
        j = next;
        continue;
      }
      // ★ line = 角色标签行的行号（1 起）。语音映射键，别再往下丢。
      payloads.push({ who, kind, blocks, line: j + 1 });
      stats.says++;
      j = next;
    }
    return { payloads, next: j };
  };

  // —— 解析 `▸A / ▸B / ▸C` 变体（窗口内） ——
  const parseWindowVariants = (start: number, winId: string, ttl: number): { next: number } => {
    const variants: Record<string, SayPayload[]> = {};
    let j = start;
    while (j < lines.length) {
      const t = unwrap(lines[j]);
      if (RE_WIN_CLOSE.test(t)) break;
      const mv = RE_VARIANT.exec(t);
      if (!mv) {
        j++;
        continue;
      }
      const label = mv[1];
      const { payloads, next } = parseSayRun(j + 1);
      variants[label] = payloads;
      j = next;
    }

    const branches = Object.keys(variants).filter((k) => k !== 'base');
    if (branches.length === 0) {
      issues.push({ line: start, code: 'WINDOW_NO_VARIANT', message: `${winId} 没有任何 ▸ 分支` });
      return { next: j };
    }

    // C-3 硬校验：每个分支的 say 条数必须等于窗口声明的 TTL
    for (const b of branches) {
      const n = variants[b].length;
      if (n !== ttl) {
        issues.push({
          line: start,
          code: 'TTL_MISMATCH',
          message: `${winId} 分支 ▸${b} 有 ${n} 条 say，与窗口声明的 ${ttl} 条不符（C-3 硬约束）`,
        });
      }
    }

    const count = Math.max(...branches.map((b) => variants[b].length));
    const when = {
      kind: 'window' as const,
      id: winId,
      in: Object.fromEntries(branches.map((b) => [b, b])),
    };

    // 逐条合并：同一序号的三条分支 → 一个带 cases 的 say
    for (let idx = 0; idx < count; idx++) {
      const cases: Record<string, SayPayload> = {};
      let fallback: SayPayload | null = null;
      for (const b of branches) {
        const p = variants[b][idx];
        if (p) {
          cases[b] = p;
          if (!fallback) fallback = p;
        }
      }
      if (!fallback) continue;
      const base = variants['base']?.[idx];
      ops.push({
        op: 'say',
        who: fallback.who,
        kind: fallback.kind,
        blocks: fallback.blocks,
        line: fallback.line,
        variant: { when, cases, ...(base ? { base } : {}) },
      });
    }

    // `▸base` 若比分支多出条数，补成普通 say
    if (variants['base']) {
      for (let idx = count; idx < variants['base'].length; idx++) {
        const p = variants['base'][idx];
        ops.push({ op: 'say', who: p.who, kind: p.kind, blocks: p.blocks, line: p.line });
      }
    }
    return { next: j };
  };

  // —— 解析 `▲ 变量` 条件块（C-2：`▲` 行 → `▸base` 之间＝条件版；`▸base` 之后＝基准版） ——
  const parseCondBlock = (start: number, varId: string, gte: number): { next: number } => {
    const condRun = parseSayRun(start + 1);
    let j = condRun.next;

    const mv = j < lines.length ? RE_VARIANT.exec(unwrap(lines[j])) : null;
    if (!mv || mv[1] !== 'base') {
      issues.push({
        line: start + 1,
        code: 'COND_NO_BASE',
        message: `${varId} 条件块缺少 ▸base 基准版（C-2）；条件版已按普通 say 处理`,
      });
      for (const p of condRun.payloads) {
        ops.push({ op: 'say', who: p.who, kind: p.kind, blocks: p.blocks, line: p.line });
      }
      return { next: j };
    }

    const baseRun = parseSayRun(j + 1);
    const when = { kind: 'var' as const, id: varId, gte };
    const count = Math.max(condRun.payloads.length, baseRun.payloads.length);
    for (let idx = 0; idx < count; idx++) {
      const cond = condRun.payloads[idx];
      const base = baseRun.payloads[idx];
      const outer = base ?? cond;
      if (!outer) continue;
      ops.push({
        op: 'say',
        who: outer.who,
        kind: outer.kind,
        blocks: outer.blocks,
        line: outer.line,
        variant: { when, cases: cond ? { cond } : {} },
      });
    }
    return { next: baseRun.next };
  };

  // ============================ 行循环 ============================

  let i = 0;
  while (i < lines.length) {
    const t = unwrap(lines[i]);

    // 忽略：空行 / 分隔线 / 注释 / markdown 表格与列表
    if (t === '' || t.startsWith('%%') || t.startsWith('---') || t.startsWith('|') || t.startsWith('- ') || t.startsWith('>')) {
      i++;
      continue;
    }

    // 章节标题 —— 不产出独立指令，只更新状态（章号/章名随后续 scene 指令携带）
    const mc = RE_CHAPTER.exec(t);
    if (mc) {
      chapter = Number(mc[1]);
      chapterTitle = mc[2].trim();
      stats.chapters++;
      i++;
      continue;
    }

    // 场景头
    const ms = RE_SCENE.exec(t);
    if (ms) {
      const sceneNo = ms[1];
      const parts = ms[2].split('｜').map((s) => s.trim());
      let place = parts[0] ?? '';
      if (place === '同前' || place === '') place = lastPlace || place;
      lastPlace = place;
      sceneSpans.push({ line: i + 1, place, bgCount: 0 });
      const time = parts[1] ?? '';
      const weather = parts.length >= 3 ? parts[2] : null;
      stats.scenes++;
      ops.push({
        op: 'scene',
        id: `CH${chapter}-SC${sceneNo}`,
        chapter,
        title: chapterTitle,
        place,
        time,
        weather,
      });
      i++;
      continue;
    }

    // 资源行（可含 BG / BGM / SE 任意组合，全角空格分隔）
    if (RE_RESOURCE.test(t)) {
      for (const seg of t.split(/[　]+/).map((s) => s.trim()).filter(Boolean)) {
        const mr = RE_RESOURCE.exec(seg);
        if (!mr) continue;
        const [, kind, valueRaw] = mr;
        const value = valueRaw.trim();
        if (kind === 'BG') {
          if (sceneSpans.length) sceneSpans[sceneSpans.length - 1]!.bgCount++;
          const first = value.split('→')[0]!.trim();
          ops.push({ op: 'bg', name: first });
          if (value.includes('→')) {
            issues.push({
              line: i + 1,
              code: 'BG_TRANSITION_AMBIGUOUS',
              message: `BG 值含「→」（${value}），已取前半段；转场型待台本侧明确（R-008 位置二）`,
            });
          }
        } else if (kind === 'BGM') {
          ops.push({ op: 'bgm', name: value });
        } else {
          ops.push({ op: 'se', name: value });
        }
      }
      i++;
      continue;
    }

    // 立绘
    const msp = RE_SPRITE.exec(t);
    if (msp) {
      const raw = msp[1].trim();
      const [mainPart, slotPart] = raw.split('｜');
      const mch = /^([^（]+)(?:（(.+)）)?\s*$/.exec((mainPart ?? '').trim());
      if (mch) {
        const char = mch[1]!.trim();
        const pose = (mch[2] ?? '').trim();
        const slot = (slotPart ?? '').trim() || spriteSlots.get(char) || 'center';

        // ★ 兜底：非角色名的「立绘」行（如「两人各拆一颗糖」这类动作画面描述）
        // 降级为演出提示 note 并告警，而不是当成立绘 —— 否则会污染 manifest 的角色表
        if (!KNOWN_CHARS.has(char)) {
          issues.push({
            line: i + 1,
            code: 'SPRITE_UNKNOWN_CHAR',
            message: `「${char}」不是已知角色，已降级为演出提示。若为双人动作画面，建议改用 ※ 或 CG；若要保留则在角色表中登记。`,
          });
          ops.push({ op: 'note', text: `立绘（非角色）：${raw}` });
          i++;
          continue;
        }

        const action: 'show' | 'update' = spriteSlots.has(char) ? 'update' : 'show';
        spriteSlots.set(char, slot);
        stats.sprites++;
        ops.push({ op: 'sprite', char, pose, slot, action });
      } else {
        issues.push({ line: i + 1, code: 'SPRITE_UNPARSED', message: `立绘行无法解析：${raw}` });
      }
      i++;
      continue;
    }

    // CG
    const mcg = RE_CG.exec(t);
    if (mcg) {
      const raw = mcg[1].trim();
      const name = raw.replace(/（.*?）\s*/g, '').trim();
      ops.push({ op: 'cg', name });
      i++;
      continue;
    }

    // ※ 演出指示（可能夹带 SE：/BGM）
    const mst = RE_STAR.exec(t);
    if (mst) {
      let body = mst[1]!.trim();
      // 切出内嵌的音频段
      const seM = /SE：([^　]+)/.exec(body);
      if (seM) {
        ops.push({ op: 'se', name: seM[1]!.trim() });
        body = body.replace(seM[0], '').trim();
      }
      const bgmM = /BGM\s*(?:转为|变|停|渐强|起)?[^　]*/.exec(body);
      if (bgmM && /BGM/.test(body) && !/BGM：/.test(body)) {
        // ★ 升级为 error（2026-09-11，回应台本侧 §19.3）
        // 旧行为：只发 warning 后把这段文字**删掉** —— 若本行只剩它，整条 ※ 拍会凭空消失，
        // 与 R-022「拍没落地」是同一类事故。这类问题必须在编译期拦住，不能静默改演出。
        issues.push({
          line: i + 1,
          code: 'BGM_DIRECTIVE_IN_STAR',
          level: 'error',
          message: `※ 行内不能写 BGM 指示（${bgmM[0]}）—— 停 BGM 请另起一行写「BGM：无」`,
        });
        body = body.replace(bgmM[0], '').trim();
        if (!body) {
          // 兜底：即使已经在报错，这一拍也不能消失（保证 ops 结构自洽、节奏统计不失真）
          stats.notes++;
          ops.push({ op: 'note', text: '（此处原为 ※ 行内 BGM 指示）' });
          i++;
          continue;
        }
      }
      if (body) {
        const mapped = starToFx(body);
        if (mapped.op === 'fx') ops.push(mapped);
        else {
          stats.notes++;
          ops.push(mapped);
        }
      }
      i++;
      continue;
    }

    // 选项组
    const mch2 = RE_CHOICE.exec(t);
    if (mch2) {
      const id = mch2[1]!;
      const options: { key: string; text: string; effects: VarEffect[]; branch: string }[] = [];
      let j = i + 1;
      while (j < lines.length) {
        const s = unwrap(lines[j]);
        if (s === '') {
          j++;
          continue;
        }
        const mi = RE_CHOICE_ITEM.exec(s);
        if (!mi) break;
        // 解析选项行末尾的可选 ▲ 效果段
        const tail = mi[5] ?? '';
        const effects: VarEffect[] = [];
        for (const m of tail.matchAll(RE_VAR_EFFECT)) {
          const op = m[2];
          const mode: 'inc' | 'set' = op === '=' ? 'set' : 'inc';
          let value: number | boolean;
          if (m[3] === 'true') value = true;
          else if (m[3] === 'false') value = false;
          else value = Number(m[3]);
          if (op === '-') value = -value;
          effects.push({ var: m[1], mode, value });
        }
        options.push({
          key: mi[1]!,
          text: stripQuotes(mi[2]!),
          effects,
          // ★ 窗口分支 key（`→ W-02·C` 的 `·C`）—— 变体求值的依据，不能丢
          branch: mi[4]!,
        });
        j++;
      }
      if (options.length < 2) {
        issues.push({ line: i + 1, code: 'CHOICE_TOO_FEW', message: `${id} 选项少于 2 项` });
      }
      stats.choices++;
      ops.push({
        op: 'choice',
        id,
        options: options.map(({ key, text, branch, effects }) => ({ key, text, branch, effects })),
      });
      // 效果段编译为 var 指令（紧跟 choice 之后立即执行）
      for (const opt of options) {
        for (const e of opt.effects) {
          ops.push({ op: 'var' as const, id: e.var, mode: e.mode, value: e.value });
        }
      }
      i = j;
      continue;
    }

    // 窗口开启 → 解析其内的 ▸ 变体
    const mw = RE_WIN_OPEN.exec(t);
    if (mw) {
      const winId = mw[1]!;
      const ttl = Number(mw[2]);
      stats.windows++;
      ops.push({ op: 'window', action: 'open', id: winId, ttl, scope: 'scene' });
      const { next } = parseWindowVariants(i + 1, winId, ttl);
      ops.push({ op: 'window', action: 'close', id: winId });
      i = next;
      continue;
    }

    // 窗口关闭（防御：万一上面没吃掉）
    if (RE_WIN_CLOSE.test(t)) {
      i++;
      continue;
    }

    // ▲ 变量条件块
    const mcd = RE_COND.exec(t);
    if (mcd) {
      const cmp = mcd[2]!;
      if (cmp !== '≥' && cmp !== '>=') {
        issues.push({
          line: i + 1,
          code: 'COND_UNSUPPORTED_CMP',
          message: `条件运算符「${cmp}」暂不支持（当前仅 ≥ / >=）`,
        });
        i++;
        continue;
      }
      const { next } = parseCondBlock(i, mcd[1]!, Number(mcd[3]));
      i = next;
      continue;
    }

    // 普通 say
    if (t.startsWith('【')) {
      const { payloads, next } = parseSayRun(i);
      for (const p of payloads) {
        ops.push({ op: 'say', who: p.who, kind: p.kind, blocks: p.blocks, line: p.line });
      }
      i = next > i ? next : i + 1;
      continue;
    }

    i++;
  }

  // 场景级启发式检查：标题含「→」= 一景两地点，中途应有第二次 BG 行（引擎只认 BG 行，缺了就一路挂旧背景）
  for (const sp of sceneSpans) {
    if (sp.place.includes('→') && sp.bgCount < 2) {
      issues.push({
        line: sp.line,
        code: 'SCENE_TRANSIT_NO_BG_SWITCH',
        level: 'warning',
        message: `场景「${sp.place}」标题含「→」（途中换地点）但 BG 行仅 ${sp.bgCount} 次——换地点时请补独立的 \`BG：\` 行（写法参考「BG：校舍间小路_紫藤　　BGM：…」）；若「→」仅为路径修辞可忽略`,
      });
    }
  }

  return { ops, issues, stats };
}
