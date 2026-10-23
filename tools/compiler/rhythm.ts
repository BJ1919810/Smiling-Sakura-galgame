/**
 * 节奏清单生成器（台本侧 §14.4 要求）
 *
 * 产出：每场景的「say 数 / 总字数 / BGM 切点 / 留白审计」。
 * 台本同学在此之上做**留白审计**——say 连续 >6 条而无 ※ 停顿 / SE 垫场的场景
 * 标记为「该喘口气」，由她做纯内容侧判断。
 *
 * 用法：`npm run rhythm` → `src/generated/rhythm-report.json`
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = resolve(__dirname, '../../src/generated/script.json');
const OUT_DIR = resolve(__dirname, '../../src/generated');
const OUT = resolve(OUT_DIR, 'rhythm-report.json');

/** 留白审计阈值（台本侧 16.1 定：12 —— 对话密度即节奏，超 12 必须有一拍不说话） */
const BREATH_THRESHOLD = 12;

/** 台本侧判定口径（阈值变更历史：v1=6 初筛 → v2=12 台本侧审计口径） */

/** 中文朗读速度估算：约 4.5 字/秒（ADV 旁白偏慢） */
const CHARS_PER_SEC = 4.5;

interface SceneRow {
  sceneId: string;
  place: string;
  bg: string;
  bgm: string[];
  sayCount: number;
  totalChars: number;
  /** 朗读时长估算（秒） */
  readSec: number;
  /** ※ 停顿/演出（fx + note）数量 */
  pauses: number;
  seCount: number;
  /** 最长连续无停顿 say 段 */
  longestRun: number;
  /** 留白审计：连续 say > 阈值且无垫场 */
  needsBreath: boolean;
}

function main(): void {
  if (!existsSync(SCRIPT)) {
    console.error('找不到 script.json，请先 `npm run compile`');
    process.exit(1);
  }
  const program = JSON.parse(readFileSync(SCRIPT, 'utf-8'));
  const ops = program.ops as Array<Record<string, unknown>>;

  const rows: SceneRow[] = [];
  let cur: SceneRow | null = null;
  let run = 0;
  /** 本场景内出现过的「最长连续 say 段」。修复 §18/R-022：原实现只记尾部 run，漏报长连跑 */
  let maxRun = 0;

  const flush = () => {
    if (cur) {
      cur.longestRun = maxRun;
      cur.needsBreath = maxRun > BREATH_THRESHOLD;
      rows.push(cur);
      cur = null;
      run = 0;
      maxRun = 0;
    }
  };

  for (const op of ops) {
    switch (op.op) {
      case 'scene':
        flush();
        cur = {
          sceneId: String(op.id),
          place: `${op.place ?? ''}・${op.time ?? ''}`,
          bg: '',
          bgm: [],
          sayCount: 0,
          totalChars: 0,
          readSec: 0,
          pauses: 0,
          seCount: 0,
          longestRun: 0,
          needsBreath: false,
        };
        run = 0;
        break;
      case 'bg':
        if (cur) cur.bg = String(op.name);
        break;
      case 'bgm':
        if (cur) cur.bgm.push(String(op.name));
        break;
      case 'se':
        if (cur) {
          cur.seCount++;
          run = 0; // SE 垫场打断连续段
        }
        break;
      case 'fx':
      case 'note':
        if (cur) {
          cur.pauses++;
          run = 0; // ※ 停顿打断连续段
        }
        break;
      case 'say': {
        if (!cur) break;
        const blocks = (op.blocks ?? []) as Array<{ text?: string }>;
        const chars = blocks.reduce((n, b) => n + (b.text?.length ?? 0), 0);
        cur.sayCount++;
        cur.totalChars += chars;
        cur.readSec += chars / CHARS_PER_SEC;
        run++;
        if (run > maxRun) maxRun = run;
        break;
      }
      case 'choice':
        if (cur) cur.pauses++; // 选项本身就是节拍
        run = 0;
        break;
      case 'cg':
        if (cur) {
          cur.pauses++;
          run = 0;
        }
        break;
    }
  }
  flush();

  const breathList = rows.filter((r) => r.needsBreath);

  // ★ paceNote 合并（台本侧 16.1 要求，照 posesplit v2 做法）：
  //   台本同学的留白审计写在**旧 report 的 paceNote** 里——生成物重跑会丢，
  //   所以合并逻辑：读取**旧 report**（若存在），按 sceneId 把 paceNote 原样带回。
  //   台本侧改判定 → 更新旧 report 的 paceNote → 重跑即持久合并。
  const OLD = OUT;
  const oldNotes: Record<string, string> = {};
  try {
    if (existsSync(OLD)) {
      const prev = JSON.parse(readFileSync(OLD, 'utf-8')) as {
        scenes?: Array<{ sceneId: string; paceNote?: string }>;
      };
      for (const s of prev.scenes ?? []) {
        if (s.paceNote) oldNotes[s.sceneId] = s.paceNote;
      }
    }
  } catch {
    // 旧文件损坏：忽略，从零开始
  }
  for (const r of rows) {
    if (oldNotes[r.sceneId]) (r as SceneRow & { paceNote: string }).paceNote = oldNotes[r.sceneId]!;
  }
  const paceNoteCount = Object.keys(oldNotes).length;

  const output = {
    version: 2,
    generatedAt: new Date().toISOString(),
    note:
      '每场景节奏清单（台本侧 §14.4 / §16.1）。readSec 按 4.5 字/秒估算，仅作相对比较用。' +
      `needsBreath = 连续 say > ${BREATH_THRESHOLD} 条且中途无 ※/SE 垫场。` +
      '★ paceNote 由台本侧维护：写入本文件对应 sceneId 后重跑 rhythm 不会丢失（合并保留）。',
    totalScenes: rows.length,
    totalReadSec: Math.round(rows.reduce((n, r) => n + r.readSec, 0)),
    breathAudit: {
      threshold: BREATH_THRESHOLD,
      flagged: breathList.length,
      list: breathList.map((r) => r.sceneId),
    },
    paceNoteCarried: paceNoteCount,
    scenes: rows,
  };

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT, JSON.stringify(output, null, 2), 'utf-8');

  console.log('产出：' + OUT);
  console.log(`场景 ${rows.length} 个 | 全篇朗读估算 ${Math.round(output.totalReadSec / 60)} 分钟`);
  console.log(`\n=== 留白审计（连续 say > ${BREATH_THRESHOLD} 无垫场）===`);
  for (const r of breathList) {
    console.log(`  ⚠ ${r.sceneId}  ${r.place}  连续 ${r.longestRun} 条`);
  }
  if (breathList.length === 0) console.log('  ✓ 无');

  console.log('\n=== 全场景明细 ===');
  for (const r of rows) {
    console.log(
      `  ${r.sceneId.padEnd(10)} say ${String(r.sayCount).padStart(3)} | ${String(r.totalChars).padStart(5)} 字 ≈${String(Math.round(r.readSec)).padStart(3)}s | ※${String(r.pauses).padStart(2)} SE${String(r.seCount).padStart(2)} | ${r.bgm.join('→') || '—'}`,
    );
  }
}

main();
