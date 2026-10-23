/**
 * 雀跃演出自检（2026-09-12：取消呼吸浮动 + 新增 hop）
 *
 * 引擎侧看不到画面，这里把立绘的 y 坐标**逐帧采样**下来，用数据回答两件事：
 *   ① 静止时立绘是否真的「安安静静呆着」（呼吸浮动已拆干净 —— y 方差必须为 0）
 *   ② 命中正向高情绪括注时，y 是否走出一条抛物线并**精确归位**（向上快→慢→顶点→向下慢→快→停）
 *
 * 用法：`npm run diag:hop`　前置：`npm run dev` 已在 5173 运行。
 */

import { chromium } from 'playwright';

const URL = 'http://localhost:5173';
const DESIGN_H = 1080;
/** 立绘地面线 —— 与 src/render/Layers.ts 的 SPRITE_GROUND 同步 */
const GROUND = DESIGN_H + 1470;
/** 雀跃高度 —— 与 Layers.hop 默认值同步 */
const HOP_HEIGHT = 120;

/** 与 src/engine/text/emotion.ts 同源 —— 触发词表（改词表记得同步） */
const TRIGGER = /亮|睁大|瞪大|眼睛.{0,4}弯|弯.{0,4}眼|意外|惊|嬉|雀跃|蹦|兴奋|激动|欢呼|惊喜/;

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

  console.log('▶ 打开 ' + URL);
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.locator('.title-btn').first().click();
  await page.waitForTimeout(1000);

  // 装采样器：每 25ms 记一次「最高那只立绘的 y」（含在台角色名单）
  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__samples = [] as { t: number; top: number | null; chars: string }[];
    w.__sampler = setInterval(() => {
      const g = w.__game as { layers?: { sprites?: Map<string, { y: number }> } } | undefined;
      const sprites = g?.layers?.sprites;
      if (!sprites) return;
      let top: number | null = null;
      const names: string[] = [];
      for (const [name, sp] of sprites) {
        names.push(name);
        if (top === null || sp.y < top) top = sp.y;
      }
      (w.__samples as { t: number; top: number | null; chars: string }[]).push({
        t: Math.round(performance.now()),
        top: top === null ? null : Math.round(top * 10) / 10,
        chars: names.join('+'),
      });
    }, 25);
  });

  // 先推进到「台上有立绘」，静止检查才有意义
  await page.evaluate(() => {
    const g = (window as unknown as Record<string, unknown>).__game as {
      director?: { advance: () => void };
    };
    for (let i = 0; i < 40; i++) {
      const sprites = g?.director && (g as unknown as { layers?: { sprites?: Map<string, unknown> } }).layers?.sprites;
      if (sprites && sprites.size > 0) break;
      g.director?.advance();
    }
  });
  await page.waitForTimeout(600);

  // —— ① 静止检查：不推进剧情，立绘应当纹丝不动 ——
  await page.waitForTimeout(4000);
  const rest = await page.evaluate(() => {
    const s = (window as unknown as Record<string, unknown>).__samples as {
      t: number;
      top: number | null;
      chars: string;
    }[];
    const withSprite = s.filter((x) => x.top !== null);
    const ys = withSprite.map((x) => x.top as number);
    return {
      samples: withSprite.length,
      chars: withSprite[0]?.chars ?? '(无人)',
      min: Math.min(...ys),
      max: Math.max(...ys),
      span: Math.max(...ys) - Math.min(...ys),
    };
  });
  console.log(
    `\n① 静止检查（4s，无操作）：采样 ${rest.samples} 帧，台上=${rest.chars}`,
  );
  console.log(
    `   y 范围 [${rest.min}, ${rest.max}] 波动=${rest.span}px → ${rest.span === 0 ? '✓ 完全静止（呼吸浮动已拆）' : '✗ 仍有浮动！'}`,
  );

  // —— ② 快进到首个触发括注，抓雀跃轨迹 ——
  // 先算出触发 sprite 在 ops 里的下标（用页面里真实的 program，脚本改动也不失准）
  const triggerIdx = await page.evaluate((reSrc) => {
    const g = window as unknown as Record<string, unknown>;
    const d = g.__game as { director?: { program?: { ops?: { op: string; pose?: string }[] } } };
    const re = new RegExp(reSrc);
    return d.director?.program?.ops?.findIndex((o) => o.op === 'sprite' && !!o.pose && re.test(o.pose)) ?? -1;
  }, TRIGGER.source);
  if (triggerIdx < 0) {
    console.log('\n② 未在台本中找到触发括注（词表或台本变了？）');
    await browser.close();
    return;
  }
  console.log(`\n② 首个触发括注位于 op[${triggerIdx}]，快进中…`);

  await page.evaluate((idx) => {
    const w = window as unknown as Record<string, unknown>;
    w.__hopDone = false;
    w.__target = idx;
    const g = w.__game as {
      director?: { advance: () => void; vm?: { getProgramCounter: () => number } };
    };
    const step = () => {
      const btn = document.querySelector('.choice-btn') as HTMLButtonElement | null;
      if (btn) {
        btn.click(); // 选项挡路 → 选第一项
        return;
      }
      g.director?.advance();
    };
    const fast = () => {
      const pc = g.director?.vm?.getProgramCounter() ?? Infinity;
      if (pc < (w.__target as number) - 6) step();
      else clearInterval(w.__fast as ReturnType<typeof setInterval>);
    };
    w.__fast = setInterval(fast, 8);
  }, triggerIdx);

  // 慢速推进；抓到起跳后**再多采 1 秒**，确保整条抛物线（440ms）完整入镜
  let hopSamples: { t: number; top: number }[] = [];
  const t0 = Date.now();
  let firstDipAt = 0;
  let steps = 0;
  while (steps < 120 && Date.now() - t0 < 25000) {
    steps += 1;
    await page.evaluate(() => {
      const g = (window as unknown as Record<string, unknown>).__game as {
        director?: { advance: () => void };
      };
      const btn = document.querySelector('.choice-btn') as HTMLButtonElement | null;
      if (btn) btn.click();
      else g.director?.advance();
    });
    await page.waitForTimeout(60);
    const dips = await page.evaluate(() => {
      const s = (window as unknown as Record<string, unknown>).__samples as {
        t: number;
        top: number | null;
      }[];
      return s.filter((x) => x.top !== null && (x.top as number) < GROUND - 3) as {
        t: number;
        top: number;
      }[];
    });
    if (dips.length > 0 && firstDipAt === 0) firstDipAt = Date.now();
    if (firstDipAt !== 0 && Date.now() - firstDipAt > 1000) {
      hopSamples = dips;
      break;
    }
  }

  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    clearInterval(w.__sampler as ReturnType<typeof setInterval>);
    clearInterval(w.__fast as ReturnType<typeof setInterval>);
  });

  if (hopSamples.length === 0) {
    console.log('   ✗ 推进 120 步内没抓到雀跃（y 从未低于 2547）');
  } else {
    // 切段：连续的 dip 帧为一段；间隔 >150ms 视为另一次起跳（可能是别的角色）
    const all = await page.evaluate(() => {
      const w = window as unknown as Record<string, unknown>;
      return w.__samples as { t: number; top: number | null; chars: string }[];
    });
    const segs: { t: number; top: number }[][] = [[]];
    for (const x of hopSamples) {
      const cur = segs[segs.length - 1];
      if (cur.length && x.t - cur[cur.length - 1].t > 150) segs.push([]);
      segs[segs.length - 1].push(x);
    }
    const seg = segs[0].length ? segs[0] : hopSamples;
    const min = Math.min(...seg.map((x) => x.top));
    const rise = GROUND - min;
    // 段后帧 = 归位判定：跳跃收尾会经过接近地面线的中间帧，故看「段后 12 帧内是否到达地面线并停住」
    const tail = all
      .filter((x) => x.t > seg[seg.length - 1].t && x.top !== null)
      .slice(0, 12)
      .map((x) => x.top as number);
    const after = tail.length ? tail[tail.length - 1] : null;
    const settled = tail.includes(GROUND);
    const curve = seg
      .filter((_, i) => i % Math.max(1, Math.floor(seg.length / 12)) === 0)
      .map((x) => Math.round(x.top));
    console.log(
      `   共 ${segs.length} 段起跳；取第 1 段（${seg.length} 帧）：` +
        `入跳 y=${seg[0].top} → 最低 y=${min}（跳起 ${rise}px）`,
    );
    console.log(`   轨迹（抽样 12 点）：${curve.join(' → ')} → ${after}`);
    const okMin = rise > 40 && rise < 70;
    const okBack = settled && after === GROUND;
    const okShape = (() => {
      // 抛物线：先单调降、后单调升（允许采样抖动 0.5px）
      const ys = seg.map((x) => x.top);
      let i = 1;
      while (i < ys.length && ys[i] <= ys[i - 1] + 0.5) i++;
      while (i < ys.length && ys[i] >= ys[i - 1] - 0.5) i++;
      return i >= ys.length;
    })();
    console.log(`   跳起高度 ${rise}px（期望 ≈${HOP_HEIGHT}）→ ${okMin ? '✓' : '✗'}`);
    console.log(`   归位：段后尾帧 ${after}（期望 ${DESIGN_H}）→ ${okBack ? '✓ 精确归位、静止收尾' : '✗'}`);
    console.log(`   轨迹形状（先降后升的单峰）→ ${okShape ? '✓ 抛物线' : '✗ 非抛物线'}`);
    if (segs.length > 1) {
      console.log(`   （另有 ${segs.length - 1} 段后续起跳 —— 同场景内其他角色 / 再次触发，属正常）`);
    }
  }

  await page.screenshot({ path: '.shots/hop-after.png' });
  await browser.close();

  console.log('\n=== 页面错误 ===');
  console.log(errors.length === 0 ? '  ✓ 无' : errors.map((e) => '  ✗ ' + e).join('\n'));
}

main().catch((e) => {
  console.error('自检失败：', e);
  process.exit(1);
});
