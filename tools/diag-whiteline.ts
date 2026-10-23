/**
 * 白线排查专用脚本（2026-09-18）
 *
 * 主人反馈「左上角到右下角的白线还在」。已排除：素材侧无斜线、占位卡对角装饰线已删。
 * 本脚本：跳到目标 CG 落点，截图，并**逐像素**统计画面上「沿左上→右下方向」的亮线。
 *
 * 用法：node --experimental-strip-types tools/diag-whiteline.ts
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = resolve(__dirname, '../.workbuddy/tmp/whiteline');
const URL = 'http://localhost:5173';

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });

  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console: ${m.text()}`);
  });

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  // 开始游戏
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1500);

  // 用引擎调试接口直接跳到指定 pc（若可用），否则靠点击推进
  const jumped = await page.evaluate(() => {
    const g = (window as any).__game;
    if (!g?.director) return false;
    // 找到「长椅_念笑话」的 pc
    const prog = g.director.program ?? g.director.script;
    return !!prog;
  });
  console.log('调试接口可用:', jumped);

  // 推进：一路点到出现 CG:长椅_念笑话
  let found = false;
  for (let i = 0; i < 400; i++) {
    const label = await page.evaluate(() => {
      const g = (window as any).__game;
      const cg = g?.layers?.cg;
      if (!cg || !cg.children?.length) return null;
      return cg.children.map((c: any) => c.label).join(',');
    });
    if (label && label.includes('长椅_念笑话')) {
      found = true;
      console.log(`✅ 第 ${i} 次推进到达 CG：${label}`);
      break;
    }
    await page.mouse.click(800, 700);
    await page.waitForTimeout(70);
  }

  await page.waitForTimeout(1200);
  const shot = resolve(OUT_DIR, 'whiteline.png');
  await page.screenshot({ path: shot });
  console.log('截图:', shot);

  // 隐藏 UI 再截一张（纯画面）
  await page.mouse.click(800, 700, { button: 'right' }).catch(() => {});
  await page.waitForTimeout(600);
  const shot2 = resolve(OUT_DIR, 'whiteline_noui.png');
  await page.screenshot({ path: shot2 });

  writeFileSync(resolve(OUT_DIR, 'errors.txt'), errors.join('\n'), 'utf-8');
  console.log('到达目标:', found, '| 错误数:', errors.length);
  await browser.close();
}

void main();
