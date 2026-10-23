/**
 * 画面 / 窗口适配自检（任务 7 补丁：16:9 锁定 + 右键隐藏对话框）
 *
 * 引擎侧看不到窗口，故用浏览器把「舞台矩形 ↔ DOM UI」的几何关系量出来：
 *   1. 各比例视口下，.screen 是否严格等于 Stage.fit() 算出的设计矩形；
 *   2. UI 是否始终落在画面内（不漂到黑边上）；
 *   3. 右键是否被 preventDefault，且能否切换「隐藏对话框」；
 *   4. 隐藏态下的左键 / 空格是否只恢复 UI、不跳剧情。
 *
 * 用法：`node --experimental-strip-types tools/diag-frame.ts`
 * 前置：`npm run dev` 已在 5173 运行。
 */

import { chromium } from 'playwright';

const URL = 'http://localhost:5173';

const DESIGN_W = 1920;
const DESIGN_H = 1080;

/** 覆盖：标准 16:9、含标题栏的 1600×869（模拟 windowed 非 16:9）、21:9、16:10 */
const VIEWPORTS = [
  { name: '16:9-1600x900', width: 1600, height: 900 },
  { name: '非16:9-1600x869', width: 1600, height: 869 },
  { name: '超宽-2560x1080', width: 2560, height: 1080 },
  { name: '16:10-1280x800', width: 1280, height: 800 },
];

interface Frame {
  viewport: string;
  inner: string;
  expected: { x: number; y: number; w: number; h: number };
  screenRect: { x: number; y: number; w: number; h: number };
  canvasCss: string;
  canvasBuf: string;
  rootScale: number | null;
  dialogRect: { x: number; y: number; w: number; h: number } | null;
  ok: boolean;
  notes: string[];
}

async function startGame(page: import('playwright').Page) {
  const btn = page.locator('.title-btn').first();
  if (await btn.count()) {
    await btn.click();
    await page.waitForTimeout(900);
  }
}

async function measure(page: import('playwright').Page, name: string): Promise<Frame> {
  const raw = await page.evaluate(
    ({ dw, dh }) => {
      const g = (window as unknown as Record<string, unknown>).__game as
        | { stage?: { root?: { scale?: { x: number } } } }
        | undefined;
      const screenEl = document.querySelector('.screen') as HTMLElement | null;
      const canvas = document.querySelector('canvas') as HTMLCanvasElement | null;
      const dialog = document.querySelector('.dialog-wrap') as HTMLElement | null;
      const cs = getComputedStyle(document.documentElement);
      const rect = (el: HTMLElement | null) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      };
      return {
        inner: `${window.innerWidth}x${window.innerHeight}`,
        stageX: parseFloat(cs.getPropertyValue('--stage-x')),
        stageY: parseFloat(cs.getPropertyValue('--stage-y')),
        stageW: parseFloat(cs.getPropertyValue('--stage-w')),
        stageH: parseFloat(cs.getPropertyValue('--stage-h')),
        screenRect: rect(screenEl),
        dialogRect: rect(dialog),
        canvasCss: canvas ? `${canvas.clientWidth}x${canvas.clientHeight}` : 'none',
        canvasBuf: canvas ? `${canvas.width}x${canvas.height}` : 'none',
        rootScale: g?.stage?.root?.scale?.x ?? null,
        expected: (() => {
          const w = window.innerWidth;
          const h = window.innerHeight;
          const s = Math.min(w / dw, h / dh);
          return { x: (w - dw * s) / 2, y: (h - dh * s) / 2, w: dw * s, h: dh * s };
        })(),
      };
    },
    { dw: DESIGN_W, dh: DESIGN_H },
  );

  const notes: string[] = [];
  const near = (a: number, b: number, tol = 1.5) => Math.abs(a - b) <= tol;

  if (!raw.screenRect) notes.push('✗ .screen 不存在');
  else {
    if (!near(raw.screenRect.x, raw.expected.x)) notes.push(`✗ screen.x=${raw.screenRect.x} 期望 ${raw.expected.x}`);
    if (!near(raw.screenRect.y, raw.expected.y)) notes.push(`✗ screen.y=${raw.screenRect.y} 期望 ${raw.expected.y}`);
    if (!near(raw.screenRect.w, raw.expected.w)) notes.push(`✗ screen.w=${raw.screenRect.w} 期望 ${raw.expected.w}`);
    if (!near(raw.screenRect.h, raw.expected.h)) notes.push(`✗ screen.h=${raw.screenRect.h} 期望 ${raw.expected.h}`);
  }
  if (!near(raw.stageX, raw.expected.x) || !near(raw.stageW, raw.expected.w)) {
    notes.push(`✗ CSS 变量与 fit() 不一致 (${raw.stageX},${raw.stageW})`);
  }
  if (raw.rootScale !== null && !near(raw.rootScale, raw.expected.w / DESIGN_W, 0.0005)) {
    notes.push(`✗ Pixi root.scale=${raw.rootScale} 期望 ${raw.expected.w / DESIGN_W}`);
  }
  // 对话框必须完全落在画面矩形内
  if (raw.dialogRect && raw.screenRect) {
    const r = raw.dialogRect;
    if (r.x < raw.screenRect.x - 1 || r.x + r.w > raw.screenRect.x + raw.screenRect.w + 1) {
      notes.push(`✗ 对话框横向越出画面 (dialog ${r.x}..${r.x + r.w} vs screen ${raw.screenRect.x}..${raw.screenRect.x + raw.screenRect.w})`);
    }
    if (r.y + r.h > raw.screenRect.y + raw.screenRect.h + 1) {
      notes.push('✗ 对话框纵向越出画面');
    }
  }

  return {
    viewport: name,
    inner: raw.inner,
    expected: raw.expected,
    screenRect: raw.screenRect ?? { x: 0, y: 0, w: 0, h: 0 },
    canvasCss: raw.canvasCss,
    canvasBuf: raw.canvasBuf,
    rootScale: raw.rootScale,
    dialogRect: raw.dialogRect,
    ok: notes.length === 0,
    notes,
  };
}

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });

  console.log('=== ① 舞台矩形 ↔ DOM UI 对齐 ===');
  for (const v of VIEWPORTS) {
    const page = await browser.newPage({ viewport: { width: v.width, height: v.height } });
    await page.goto(URL, { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    await startGame(page);
    const f = await measure(page, v.name);
    const fmt = (n: number) => Math.round(n).toString().padStart(5);
    console.log(
      `  ${f.ok ? '✓' : '✗'} ${f.viewport.padEnd(18)} 视口=${f.inner.padEnd(10)} ` +
        `画面=(${fmt(f.screenRect.x)},${fmt(f.screenRect.y)}) ${fmt(f.screenRect.w)}×${fmt(f.screenRect.h)} ` +
        `scale=${f.rootScale?.toFixed(4)}`,
    );
    for (const n of f.notes) console.log(`      ${n}`);
    await page.screenshot({ path: `.shots/frame-${v.name}.png` });
    await page.close();
  }

  console.log('\n=== ② 右键 = 隐藏对话框 ===');
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await startGame(page);

  // 记录：右键事件是否被 preventDefault
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__ctxPrevented = null;
    window.addEventListener('contextmenu', (e) => {
      (window as unknown as Record<string, unknown>).__ctxPrevented = e.defaultPrevented;
    });
  });

  const read = () =>
    page.evaluate(() => {
      const app = document.querySelector('.app') as HTMLElement | null;
      const dialog = document.querySelector('.dialog-wrap') as HTMLElement | null;
      return {
        hiddenClass: app?.classList.contains('ui-hidden') ?? false,
        dialogCount: document.querySelectorAll('.dialog-wrap').length,
        dialogOpacity: dialog ? getComputedStyle(dialog).opacity : 'n/a',
        text: document.querySelector('.text-box p')?.textContent?.slice(0, 30) ?? '(无)',
        pc: document.querySelector('.chip-dim')?.textContent?.replace(/\s+/g, ' ').trim() ?? '?',
        choices: document.querySelectorAll('.choice-btn').length,
        prevented: (window as unknown as Record<string, unknown>).__ctxPrevented,
      };
    });

  const before = await read();
  console.log(`  推进前：正文=「${before.text}」 进度=${before.pc}`);

  await page.mouse.click(800, 250, { button: 'right' });
  await page.waitForTimeout(400);
  const hidden = await read();
  console.log(
    `  右键后：ui-hidden=${hidden.hiddenClass} 对话框 opacity=${hidden.dialogOpacity} 菜单被拦截=${hidden.prevented}`,
  );
  console.log(`    ${hidden.hiddenClass && hidden.dialogOpacity === '0' ? '✓ 对话框已隐藏' : '✗ 未隐藏'}`);
  console.log(`    ${hidden.prevented === true ? '✓ 右键菜单已 preventDefault' : '✗ 右键菜单未被拦截'}`);

  await page.mouse.click(800, 250);
  await page.waitForTimeout(400);
  const restored = await read();
  console.log(
    `  左键后：ui-hidden=${restored.hiddenClass} 进度=${restored.pc} 正文=「${restored.text}」`,
  );
  const sameText = restored.text === hidden.text;
  const samePc = restored.pc === hidden.pc;
  console.log(`    ${!restored.hiddenClass ? '✓ UI 已恢复' : '✗ UI 未恢复'}`);
  console.log(
    `    ${samePc && sameText ? '✓ 恢复动作没有推进剧情' : `✗ 剧情被推进了（${hidden.pc} → ${restored.pc}）`}`,
  );

  await page.mouse.click(800, 250, { button: 'right' });
  await page.waitForTimeout(250);
  await page.keyboard.press('Space');
  await page.waitForTimeout(250);
  const afterSpace = await read();
  console.log(
    `  空格恢复：ui-hidden=${afterSpace.hiddenClass} 进度=${afterSpace.pc} → ${!afterSpace.hiddenClass ? '✓' : '✗'}`,
  );

  await page.screenshot({ path: '.shots/frame-16x9.png' });

  await page.close();
  await browser.close();
  console.log('\n（截图：.shots/frame-16x9.png）');
}

main().catch((e) => {
  console.error('自检失败：', e);
  process.exit(1);
});
