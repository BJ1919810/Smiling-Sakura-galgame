/**
 * 编译器 CLI —— 台本 md → script.json
 *
 * 用法：`npm run compile:script`
 *
 * 产出：
 * - `src/generated/script.json`  —— 指令流
 * - `compile-report.txt`         —— 编译报告（统计 + 错误 + 警告）
 *
 * 退出码：0 = 成功（允许有 warning）；1 = 有 error
 */

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { parseScript } from './parser.ts';
import { ScriptProgramSchema, SCRIPT_VERSION } from '../../src/engine/script/schema.ts';
import type { CompileIssue, Op } from '../../src/engine/script/ops.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '../..');

const SRC_FILE = resolve(ROOT, '笑樱_galgame台本.md');
const OUT_DIR = resolve(ROOT, 'src/generated');
const OUT_JSON = resolve(OUT_DIR, 'script.json');
const OUT_REPORT = resolve(ROOT, 'compile-report.txt');

/** 章节索引：每个 scene 指令的章号/章名（首次出现处记 pc） */
function collectChapters(ops: Op[]) {
  const seen = new Set<number>();
  const out: { index: number; title: string; startPc: number }[] = [];
  ops.forEach((op, pc) => {
    if (op.op === 'scene' && !seen.has(op.chapter)) {
      seen.add(op.chapter);
      out.push({ index: op.chapter, title: op.title, startPc: pc });
    }
  });
  return out.sort((a, b) => a.index - b.index);
}

function fmtIssues(title: string, list: CompileIssue[]): string {
  if (list.length === 0) return `## ${title}\n（无）\n`;
  const byCode = new Map<string, CompileIssue[]>();
  for (const it of list) {
    const arr = byCode.get(it.code) ?? [];
    arr.push(it);
    byCode.set(it.code, arr);
  }
  let s = `## ${title}（共 ${list.length} 条）\n\n`;
  for (const [code, arr] of [...byCode.entries()].sort((a, b) => b[1].length - a[1].length)) {
    s += `### ${code} × ${arr.length}\n`;
    for (const it of arr.slice(0, 40)) {
      s += `- L${it.line}：${it.message}\n`;
    }
    if (arr.length > 40) s += `- …（另有 ${arr.length - 40} 条同类，已省略）\n`;
    s += '\n';
  }
  return s;
}

function main(): number {
  const started = Date.now();
  process.stdout.write(`读取台本：${SRC_FILE}\n`);

  const text = readFileSync(SRC_FILE, 'utf-8');
  const { ops, issues, stats } = parseScript(text);

  const program = {
    version: SCRIPT_VERSION,
    generatedAt: new Date().toISOString(),
    source: '笑樱_galgame台本.md',
    chapters: collectChapters(ops),
    ops,
  };

  // —— schema 自检 ——
  const zodErrors: CompileIssue[] = [];
  const checked = ScriptProgramSchema.safeParse(program);
  if (!checked.success) {
    for (const iss of checked.error.issues.slice(0, 30)) {
      zodErrors.push({
        line: 0,
        code: 'SCHEMA',
        message: `${iss.path.join('.')}: ${iss.message}`,
      });
    }
  }

  // 级别判定：优先看 issue.level，缺省时按 code 前缀兜底（SCHEMA = error）
  const isError = (x: CompileIssue) =>
    x.level === 'error' || (x.level === undefined && x.code.startsWith('SCHEMA'));
  const errors = [...issues.filter(isError), ...zodErrors];
  const warnings = issues.filter((x) => !isError(x));

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_JSON, JSON.stringify(program, null, 2), 'utf-8');

  const opCount = ops.reduce<Record<string, number>>((acc, op) => {
    acc[op.op] = (acc[op.op] ?? 0) + 1;
    return acc;
  }, {});

  const report = [
    '# 《笑樱》台本编译报告',
    '',
    `- 时间：${program.generatedAt}`,
    `- 来源：${program.source}（${stats.lines} 行）`,
    `- 结果：${errors.length === 0 ? '✅ 通过' : '❌ 失败'}（error ${errors.length} / warning ${warnings.length}）`,
    `- 耗时：${Date.now() - started} ms`,
    '',
    '## 解析统计',
    '',
    '| 项 | 数量 |',
    '|---|---|',
    `| 章节 | ${stats.chapters} |`,
    `| 场景 | ${stats.scenes} |`,
    `| say | ${stats.says} |`,
    `| 立绘 | ${stats.sprites} |`,
    `| 选项组 | ${stats.choices} |`,
    `| 窗口 | ${stats.windows} |`,
    `| 无法映射的 ※（note） | ${stats.notes} |`,
    '',
    '## 指令流分布',
    '',
    '| op | 数量 |',
    '|---|---|',
    ...Object.entries(opCount)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `| \`${k}\` | ${v} |`),
    '',
    `**总指令数：${ops.length}**`,
    '',
    fmtIssues('错误', errors),
    fmtIssues('警告', warnings),
  ].join('\n');

  writeFileSync(OUT_REPORT, report, 'utf-8');

  process.stdout.write(
    `\n章节 ${stats.chapters} / 场景 ${stats.scenes} / say ${stats.says} / 立绘 ${stats.sprites} / 选项 ${stats.choices} / 窗口 ${stats.windows}\n` +
      `指令总数 ${ops.length}；error ${errors.length}，warning ${warnings.length}\n` +
      `产出：${OUT_JSON}\n报告：${OUT_REPORT}\n`,
  );

  return errors.length === 0 ? 0 : 1;
}

process.exit(main());
