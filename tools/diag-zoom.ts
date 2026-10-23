/**
 * 白线取证 · 第七轮：推镜进行中的实时抓帧（2026-09-18）
 *
 * 背景：此前所有实验抓的都是静止帧（推镜早已归位、camera.scale=1），
 * 而 pushIn 的斜向重采样伪影只在 900ms 动画窗口内存在（同内容 A/B 实测
 * scale 1 → 3 条斜向命中、scale 1.06 → 26 条）。主人报「CG 上有白线」
 * 的场景若对应 ※ 特写触发的推镜窗口，静止帧永远抓不到 —— 本轮在
 * pushIn 进行中逐帧抓拍，验证伪影是否肉眼可见地成线。
 *
 * 对照组：归位后（scale=1）再抓一张 —— 同一画面，前后差异即推镜伪影。
 *
 * 用法：node --experimental-strip-types tools/diag-zoom.ts
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const OUT = resolve(ROOT, '.workbuddy/tmp/zoom');
const URL = 'http://localhost:5173';

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: false });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.25 });

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1500);

  // 推进到无 CG、无打字的稳定 BG 场景
  for (let i = 0; i < 400; i++) {
    const st = await page.evaluate(() => {
      const L = (window as any).__game.layers;
      const d = (window as any).__game.director;
      return { cg: L.cg.children.length, bg: L.bg.children.length, typing: d.isTyping };
    });
    if (st.cg === 0 && st.bg >= 1 && !st.typing) break;
    await page.evaluate(() => {
      const d = (window as any).__game.director;
      if (d.choice) d.pick(0);
      else d.advance();
    });
    await page.waitForTimeout(90);
  }
  // 等上一次 BG 淡出彻底结束
  await page.waitForTimeout(1500);

  const snap = (n: string) =>
    page.screenshot({ path: resolve(OUT, n) }).then(() => console.log('  📷', n));

  await snap('Z0_rest.png'); // 归位基线

  // 触发推镜（主人 09-18 删除的正是这个效果 —— 方法保留，此处注入调用复现）
  await page.evaluate(() => (window as any).__game.layers.pushIn(0.06));

  // 900ms 钟形窗口内抓 4 帧（顶点 ~450ms，scale ≈1.06）
  const marks: Array<[number, string]> = [
    [200, 'Z1_push200ms.png'],
    [350, 'Z2_push350ms.png'],
    [500, 'Z3_push500ms.png'],
    [650, 'Z4_push650ms.png'],
  ];
  let prev = 0;
  for (const [ms, name] of marks) {
    await page.waitForTimeout(ms - prev);
    prev = ms;
    await snap(name);
  }
  await page.waitForTimeout(700); // 等归位（900ms 动画走完）
  await snap('Z5_back.png');      // 归位对照

  const scale = await page.evaluate(() => {
    const L = (window as any).__game.layers;
    return { sx: (L as any).camera.scale.x, x: (L as any).camera.x, y: (L as any).camera.y };
  });
  console.log('归位后 camera 状态', JSON.stringify(scale));

  await browser.close();
  console.log('完成。输出目录：', OUT);
}

void main();
