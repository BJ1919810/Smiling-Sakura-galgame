/**
 * 截图自检工具
 *
 * 用途：引擎侧是文本 AI，**看不到画面** —— 本脚本把游戏运行状态截成图，
 * 让引擎侧能自查视觉效果（配色 / 比例 / 立绘位置 / 对话框）。
 *
 * 用法：`npm run shot [输出目录]`
 * 产出：一系列 PNG + 一份 `console.log` / 错误汇总
 *
 * 复用系统 Edge（不下载 Chromium），故无需 `npx playwright install`。
 */

import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, '../.shots');
const URL = 'http://localhost:5173';

interface Step {
  name: string;
  /** 点击次数（推进剧情） */
  clicks?: number;
  /** 点击后等待 ms（等打字机 / 转场；headless rAF 被节流，需留足动画时间） */
  wait?: number;
  /** 若出现选项，点第几个 */
  pick?: number;
}

/** 演示流程：开屏 → 推进若干句 → 长椅戏（有立绘）→ 选项 */
const STEPS: Step[] = [
  { name: '01-开屏', clicks: 0, wait: 1500 },
  { name: '02-第2句', clicks: 1, wait: 900 },
  { name: '03-第4句', clicks: 2, wait: 900 },
  { name: '04-推进8句', clicks: 4, wait: 900 },
  { name: '05-长椅戏立绘', clicks: 3, wait: 1400 },
  { name: '06-选项检测', clicks: 6, wait: 1200, pick: 0 },
  { name: '07-选项后', clicks: 2, wait: 1200 },
];

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

  const logs: string[] = [];
  const errors: string[] = [];
  page.on('console', (m) => {
    const t = `${m.type()}: ${m.text()}`;
    logs.push(t);
    if (m.type() === 'error') errors.push(t);
  });
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

  console.log('▶ 打开 ' + URL);
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  // ★ 先点「开始游戏」—— 标题画面不拦截任意点击，不点这里后面全是空拍
  const startBtn = page.locator('.title-btn').first();
  if (await startBtn.count()) {
    await startBtn.click();
    await page.waitForTimeout(1200);
    console.log('  ✓ 已点击「开始游戏」');
  }

  for (const s of STEPS) {
    if (s.clicks) {
      for (let i = 0; i < s.clicks; i++) {
        // ★ 点 (800, 250)：避开选项按钮区（中央 y≈390-650）与对话框（下方）
        await page.mouse.click(800, 250);
        await page.waitForTimeout(130);
      }
    }

    // 若出现选项，按 pick 点选
    if (s.pick !== undefined) {
      const btns = page.locator('.choice-btn');
      const n = await btns.count();
      if (n > 0) {
        const idx = Math.min(s.pick, n - 1);
        console.log(`  → 出现选项（${n} 项），点第 ${idx + 1} 个`);
        await btns.nth(idx).click();
        await page.waitForTimeout(s.wait ?? 800);
      }
    } else {
      await page.waitForTimeout(s.wait ?? 800);
    }

    const file = resolve(OUT_DIR, `${s.name}.png`);
    await page.screenshot({ path: file });

    // 顺便抓当前状态 + 舞台实况（各层子节点 alpha —— 诊断 fade 是否被节流卡住）
    const status = await page.evaluate(() => {
      const bar = document.querySelector('.status-bar')?.textContent ?? '';
      const name = document.querySelector('.name-box')?.textContent ?? '(无姓名框)';
      const text = document.querySelector('.text-box p')?.textContent ?? '';
      const hasChoice = !!document.querySelector('.choice-layer');
      const canvas = document.querySelector('canvas');
      return {
        bar: bar.replace(/\s+/g, ' ').trim(),
        name,
        text: text.slice(0, 60),
        hasChoice,
        canvasSize: canvas ? `${canvas.width}x${canvas.height}` : 'none',
      };
    });
    console.log(`  ✓ ${s.name}.png  | ${status.bar} | canvas=${status.canvasSize}`);
    console.log(`      姓名框=${status.name} 选项=${status.hasChoice ? '有' : '无'}`);
    console.log(`      正文=「${status.text}」`);
  }

  await browser.close();

  console.log('\n=== 控制台错误 ===');
  if (errors.length === 0) {
    console.log('  ✓ 无错误');
  } else {
    for (const e of errors) console.log('  ✗ ' + e);
  }
  console.log(`\n截图目录：${OUT_DIR}`);
  console.log(`控制台消息总数：${logs.length}`);
}

main().catch((e) => {
  console.error('截图失败：', e);
  process.exit(1);
});
