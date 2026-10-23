/**
 * 白线取证 · 第六轮：headed GPU + 完整 UI（2026-09-18）
 *
 * 背景：headless（软件渲染）+ hideUI 的五轮实验全部干净（E2−E0 像素差为 0），
 * 但主人 exe（WebView2，GPU 硬件渲染，完整 UI）仍见「左上→右下白线」。
 * 本轮把环境换成最接近 exe 的浏览器等价物：
 *   - headed（真窗口、真 GPU 硬件渲染）
 *   - system Edge（与 WebView2 同内核同 GPU 栈）
 *   - deviceScaleFactor 1.25（常见 Windows 显示缩放）
 *   - UI 不隐藏、花瓣开着 —— 完整还原主人视角
 * 四种状态各抓一张：G1 早期 CG / G2 后期 CG / G3 后期 BG / G4 强制泄漏 BG
 *
 * 用法：node --experimental-strip-types tools/diag-gpu.ts
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const OUT = resolve(ROOT, '.workbuddy/tmp/gpu');
const URL = 'http://localhost:5173';
const DSF = Number(process.env.DSF ?? 1.25);

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: false });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: DSF });

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1500);

  const state = () =>
    page.evaluate(() => {
      const L = (window as any).__game.layers;
      const d = (window as any).__game.director;
      const cgKids = L.cg.children as any[];
      const top = cgKids[cgKids.length - 1];
      return {
        cg: cgKids.length,
        cgAlpha: top?.alpha ?? -1,
        bg: L.bg.children.length,
        typing: d.isTyping,
        choice: !!d.choice,
      };
    });

  const snap = (n: string) =>
    page.screenshot({ path: resolve(OUT, n) }).then(() => console.log('  📷', n));

  // —— 阶段一：抓两张 CG（第一张=early；再推进 120 步以上遇到的下一张=late）——
  let cgShots = 0;
  let earlyAt = -1;
  for (let i = 0; i < 800 && cgShots < 2; i++) {
    const st = await state();
    if (st.cg > 0 && st.cgAlpha > 0.99 && !st.typing) {
      await page.waitForTimeout(300); // 淡入余量
      if (cgShots === 0) {
        earlyAt = i;
        await snap('G1_cg_early.png');
        cgShots++;
      } else if (i > earlyAt + 120) {
        await snap('G2_cg_late.png');
        cgShots++;
      }
    }
    await page.evaluate(() => {
      const d = (window as any).__game.director;
      if (d.choice) d.pick(0);
      else d.advance();
    });
    await page.waitForTimeout(90);
  }
  console.log(`阶段一：CG 截图 ${cgShots} 张（early@${earlyAt}）`, JSON.stringify(await state()));

  // —— 阶段二：推进到无 CG 的稳定 BG ——
  for (let i = 0; i < 250; i++) {
    const st = await state();
    if (st.cg === 0 && st.bg >= 1 && !st.typing) break;
    await page.evaluate(() => {
      const d = (window as any).__game.director;
      if (d.choice) d.pick(0);
      else d.advance();
    });
    await page.waitForTimeout(90);
  }
  await page.waitForTimeout(1200); // 等淡出彻底结束
  await snap('G3_bg_late.png');
  console.log('G3 状态', JSON.stringify(await state()));

  // —— 阶段三：连发 setBg 制造层泄漏（间隔 250ms < 淡出 420ms）——
  const curName = await page.evaluate(() =>
    String((window as any).__game.layers.bg.children.slice(-1)[0].label).replace(/^bg:/, '')
  );
  for (let i = 0; i < 30; i++) {
    await page.evaluate((n: string) => (window as any).__game.layers.setBg(n), curName);
    await page.waitForTimeout(250);
  }
  await page.waitForTimeout(2500);
  await snap('G4_bg_leak.png');
  console.log('G4 状态', JSON.stringify(await state()));

  await browser.close();
  console.log('完成。输出目录：', OUT);
}

void main();
