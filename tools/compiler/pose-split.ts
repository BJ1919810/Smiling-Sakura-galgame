/**
 * pose 分解表生成器 v3
 *
 * v3 变化（2026-09-12 主人拍板「**坐姿全线取消**」，归则见 制作手册.md 附录 A / 对接 §21.1）：
 * 1. **body 恒为「站」** —— 不再产出 `body=坐` / `蹲`。座位场景的画面感由 CG／演出承担，
 *    弯腰/蹲/系鞋带等复杂姿势一律取消（「喘・弯腰撑膝」→ face=喘 ＋ body=站，
 *    弯腰感由台词与 `※` 拍承担）。
 * 2. hands 词表更新：「系鞋带」随蹲姿取消移除；新增「**思索手势**」。
 * 3. 沿用 v2：归则① 纯动作/位置类 → 角色默认 face（樱=平静 龙司=平静 阳太=笑
 *    亚里沙=打量 九条院=笑 泽渡=笑）；归则② 情绪词 → 表情组。
 *
 * 输出 `src/generated/pose-split.json`。用法：`npm run posesplit`
 * ⚠️ schema 变更：v2 的 `bodyByScene` / `sceneFixedTotal` 已随归则③作废删除（对接 §24 备案）。
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = resolve(__dirname, '../../src/generated/script.json');
const OUT_DIR = resolve(__dirname, '../../src/generated');
const OUT = resolve(OUT_DIR, 'pose-split.json');

// ============================================================
// hands 词表（2026-09-12 定稿 10 个：系鞋带移除、新增思索手势）
// ============================================================
const HANDS_RULES: [RegExp, string][] = [
  [/拆糖|拿糖|递糖|掏出糖|含着糖|吃了颗|放糖|糖纸/, '糖'],
  [/翻书|默读|合上书|看书|皱巴巴的书/, '书'],
  [/拧螺丝/, '螺丝'],
  [/翻纸|文件|板夹/, '文件'],
  [/便当|筷子/, '便当'],
  [/写|便条|字条/, '便条'],
  [/手机|短信/, '手机'],
  [/思索|沉思|抱胸|托腮|叉腰|揣|插兜/, '思索手势'],
  [/攥饮料|饮料|面包袋|拎/, '饮料'],
  [/撑膝|捧|蒙|伸|捏|递/, '其他手部'],
];

// ============================================================
// 归则②：情绪词 → 表情组
// ============================================================
const FACE_RULES: [RegExp, string][] = [
  [/眼眶热|含泪/, '眼眶热'],
  [/眼睛微亮|眼睛亮|眼神|极细微|微微弯|眼.*弯/, '眼睛微弯'],
  [/歪头/, '微微歪头'],
  [/抬眼/, '抬眼'],
  [/不耐/, '不耐'],
  [/无语|无奈/, '无语'],
  [/脸沉|沉|僵|认真|盯|打量/, '脸沉'],
  [/泛红|红/, '泛红'],
  [/喘/, '喘'],
  [/笑|嬉/, '笑'],
];

// ============================================================
// 归则①：face 默认（纯动作/位置类 pose 无情绪词时）
// ============================================================
const DEFAULT_FACE: Record<string, string> = {
  樱: '平静',
  龙司: '平静',
  阳太: '笑',
  亚里沙: '打量',
  九条院: '笑',
  泽渡: '笑',
};

interface PoseEntry {
  pose: string;
  count: number;
  /** v3：恒为「站」（坐姿全线取消） */
  body: '站';
  hands: string | null;
  face: string;
  /** 场景上下文（溯源用） */
  scene: string;
}

interface OpLike {
  op: string;
  [k: string]: unknown;
}

function main(): void {
  if (!existsSync(SCRIPT)) {
    console.error('找不到 script.json，请先 `npm run compile`');
    process.exit(1);
  }
  const program = JSON.parse(readFileSync(SCRIPT, 'utf-8'));
  const ops = program.ops as OpLike[];

  const result: Record<
    string,
    { stem: string; poses: PoseEntry[]; summary: Record<string, unknown> }
  > = {};
  const stems: Record<string, string> = {
    龙司: 'ryuji', 樱: 'sakura', 阳太: 'yota',
    亚里沙: 'arisa', 九条院: 'kujo', 泽渡: 'sawtari',
  };

  let place = '';
  let bgName = '';
  /** (char, pose) → entry（同 pose 多次出现只记一条，count 累加，场景取首次） */
  const seen = new Map<string, PoseEntry>();

  const classify = (char: string, pose: string, scene: string): PoseEntry => {
    // —— body：v3 恒为「站」——
    const body: '站' = '站';

    // —— hands ——
    let hands: string | null = null;
    for (const [re, name] of HANDS_RULES) {
      if (re.test(pose)) {
        hands = name;
        break;
      }
    }

    // —— face（归则② → 归则① 兜底） ——
    let face: string | null = null;
    for (const [re, name] of FACE_RULES) {
      if (re.test(pose)) {
        face = name;
        break;
      }
    }
    if (!face) face = DEFAULT_FACE[char] ?? '平静'; // 归则①：角色默认

    return { pose, count: 1, body, hands, face, scene };
  };

  for (const op of ops) {
    if (op.op === 'scene') {
      place = `${op.place ?? ''}`;
      continue;
    }
    if (op.op === 'bg') {
      bgName = String(op.name ?? '');
      continue;
    }
    if (op.op !== 'sprite') continue;
    const char = String(op.char);
    const pose = String(op.pose || 'default');
    const key = `${char}::${pose}`;
    const scene = `${place}｜${bgName}`;
    const exist = seen.get(key);
    if (exist) {
      exist.count++;
    } else {
      seen.set(key, classify(char, pose, scene));
    }
  }

  // —— 分角色汇总 ——
  for (const [key, entry] of seen) {
    const char = key.split('::')[0]!;
    if (!result[char]) result[char] = { stem: stems[char] ?? char, poses: [], summary: {} };
    result[char]!.poses.push(entry);
  }
  for (const r of Object.values(result)) {
    r.poses.sort((a, b) => b.count - a.count);
    const uniq = (arr: (string | null)[]) => [...new Set(arr.filter((x): x is string => x !== null))];
    r.summary = {
      body: uniq(r.poses.map((p) => p.body)),
      hands: uniq(r.poses.map((p) => p.hands)),
      face: uniq(r.poses.map((p) => p.face)),
      needsReview: 0,
    };
  }

  const output = {
    version: 3,
    generatedAt: new Date().toISOString(),
    note:
      'pose 三轴分解 v3（坐姿全线取消版，2026-09-12 主人拍板；归则见 制作手册.md 附录 A）。' +
      'body 恒为「站」，不再产出 body=坐/蹲；座位画面感由 CG／演出承担。' +
      '归则① 纯动作→角色默认 face；② 情绪词→表情组；hands 词表 10 个（系鞋带移除、新增思索手势）。' +
      'schema 变更：v2 的 bodyByScene / sceneFixedTotal 已随归则③作废删除。',
    bodyPolicy: '站（坐姿全线取消）',
    chars: result,
  };

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT, JSON.stringify(output, null, 2), 'utf-8');

  console.log('产出：' + OUT);
  for (const [char, r] of Object.entries(result)) {
    const s = r.summary as { body: string[]; hands: string[]; face: string[] };
    console.log(
      `  ${char.padEnd(4)} pose ${String(r.poses.length).padStart(2)} → body ${s.body.join('/')} | hands ${s.hands.join('/')} | face ${s.face.join('/')}`,
    );
  }
}

main();
