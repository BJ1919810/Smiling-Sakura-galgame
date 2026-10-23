/**
 * 白线定位（2026-09-18 · 第二轮）
 *
 * 主人反馈：「只要是 CG 必有白线，BG 就没有」，截图放大后是**斜向阶梯锯齿**。
 * 本脚本：跳到一张 CG → 截三张图做 A/B 对照：
 *   ① 原样
 *   ② 隐藏 bg 层（只剩 CG）
 *   ③ 隐藏 cg 层（只剩 bg）
 * 然后对三张图做同一套「斜向亮线」检测，看线出现在哪一层。
 *
 * 用法：node --experimental-strip-types tools/diag-whiteline2.ts
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '../.workbuddy/tmp/whiteline');
const URL = 'http://localhost:5173';

/** 沿「左上→右下」主对角线检测亮线：算对角线上像素与邻域的平均亮度差 */
async function diagLine(page: import('playwright').Page) {
  return page.evaluate(() => {
    const cv = document.querySelector('canvas') as HTMLCanvasElement;
    if (!cv) return null;
    const off = document.createElement('canvas');
    off.width = cv.width; off.height = cv.height;
    const ctx = off.getContext('2d')!;
    ctx.drawImage(cv, 0, 0);
    const { width: w, height: h } = off;
    const d = ctx.getImageData(0, 0, w, h).data;
    const lum = (x: number, y: number) => {
      const i = (y * w + x) * 4;
      return (d[i]! + d[i + 1]! + d[i + 2]!) / 3;
    };
    const n = Math.min(w, h);
    let sumOn = 0, sumOff = 0, cnt = 0, peak = 0;
    for (let t = 0; t < n; t += 2) {
      const x = Math.floor((t * (w - 1)) / (n - 1));
      const y = Math.floor((t * (h - 1)) / (n - 1));
      let on = 0, o = 0, k = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) on += lum(x + dx, y + dy), k++;
      on /= k;
      for (const dd of [6, -6, 12, -12]) {
        o += lum(Math.min(w - 1, Math.max(0, x + dd)), y);
        o += lum(x, Math.min(h - 1, Math.max(0, y + dd)));
      }
      o /= 8;
      const diff = on - o;
      sumOn += on; sumOff += o; cnt++;
      if (diff > peak) peak = diff;
    }
    return { avgOn: sumOn / cnt, avgOff: sumOff / cnt, peakDiff: peak, w, h };
  });
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const log: string[] = [];
  page.on('pageerror', (e) => log.push('pageerror: ' + e.message));

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1200);

  // 推进到出现任意 CG
  let label = '';
  for (let i = 0; i < 500; i++) {
    label = await page.evaluate(() => {
      const g = (window as any).__game;
      const cg = g?.layers?.cg;
      if (!cg?.children?.length) return '';
      return cg.children.map((c: any) => c.label).join(',');
    });
    if (label) break;
    await page.mouse.click(800, 700);
    await page.waitForTimeout(60);
  }
  await page.waitForTimeout(1500);
  log.push(`到达 CG: ${label}`);

  const shots: Record<string, unknown> = {};

  // ① 原样
  await page.screenshot({ path: resolve(OUT, 'a_原样.png') });
  shots['原样'] = await diagLine(page);

  // ② 隐藏 bg 层
  await page.evaluate(() => {
    const g = (window as any).__game;
    g.layers.bg.visible = false;
    g.layers.char.visible = false;
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(OUT, 'b_只CG.png') });
  shots['只CG'] = await diagLine(page);

  // ③ 恢复 bg，隐藏 cg 层
  await page.evaluate(() => {
    const g = (window as any).__game;
    g.layers.bg.visible = true;
    g.layers.cg.visible = false;
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(OUT, 'c_只BG.png') });
  shots['只BG'] = await diagLine(page);

  // ④ 全部隐藏（纯背景色，看是不是 canvas 本身）
  await page.evaluate(() => {
    const g = (window as any).__game;
    g.layers.bg.visible = false;
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: resolve(OUT, 'd_全隐藏.png') });
  shots['全隐藏'] = await diagLine(page);

  const out = JSON.stringify(shots, null, 2);
  console.log(out);
  writeFileSync(resolve(OUT, 'result.json'), out, 'utf-8');
  writeFileSync(resolve(OUT, 'log.txt'), log.join('\n'), 'utf-8');
  await browser.close();
}

void main();
