/**
 * 白线定位 · 第三轮（2026-09-18 晚）
 *
 * 已确认：素材侧无斜线（主人已明确），问题在引擎渲染。第二轮浏览器 A/B 显示
 * 「只显示 CG 层」时斜向长线命中 8 条，「只显示 BG 层」只 1 条。
 *
 * 但 CG 与 BG 走的是同一个 makeRealBg，所以本轮做**同纹理对照**：
 *   把 CG 的纹理塞给 BG 层 → 两层显示的是同一张图、同一缩放
 *   ⇒ 此时若 CG 层仍有线、BG 层没有，则铁证是「CG 这条路径」的问题，与图无关。
 *
 * 另测：camera.scale = 1（不推镜） vs 1.06（推镜），看线是否由非整数缩放采样产生。
 *
 * 用法：node --experimental-strip-types tools/diag-whiteline3.ts
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '../.workbuddy/tmp/whiteline3');
const URL = 'http://localhost:5173';

async function shoot(page: import('playwright').Page, name: string) {
  await page.waitForTimeout(700);
  await page.screenshot({ path: resolve(OUT, `${name}.png`) });
  console.log('  📷', name);
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

  // 推进到出现 CG
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
  console.log('到达 CG:', label);

  // 把 CG 的纹理复制给 BG 层（同图对照）
  const setup = await page.evaluate(() => {
    const g = (window as any).__game;
    const L = g.layers;
    // 冻结相机，排除推镜干扰
    L.camera.scale.set(1);
    L.camera.pivot.set(0, 0);
    L.camera.x = 0;
    L.camera.y = 0;
    L.sakura.container.visible = false; // 花瓣先排除
    const cgC = L.cg.children[0];
    const cgSp = cgC?.children?.[0];
    if (!cgSp) return { ok: false };
    const tex = cgSp.texture;
    // 清掉 bg 层，塞一张同纹理同缩放的 sprite
    while (L.bg.children.length) L.bg.removeChild(L.bg.children[0]);
    const holder = new (cgC.constructor)();
    const sp = new (cgSp.constructor)(tex);
    sp.scale.set(cgSp.scale.x);
    sp.x = cgSp.x;
    sp.y = cgSp.y;
    holder.addChild(sp);
    L.bg.addChild(holder);
    return {
      ok: true,
      texW: tex.width, texH: tex.height,
      cgScale: cgSp.scale.x, cgX: cgSp.x, cgY: cgSp.y,
    };
  });
  console.log('同图对照设置:', JSON.stringify(setup));
  log.push('setup=' + JSON.stringify(setup));

  // ① CG 层（同纹理）
  await page.evaluate(() => {
    const L = (window as any).__game.layers;
    L.bg.visible = false;
    L.char.visible = false;
    L.cg.visible = true;
  });
  await shoot(page, 'A_cg_only_sameTex');

  // ② BG 层（同纹理）
  await page.evaluate(() => {
    const L = (window as any).__game.layers;
    L.cg.visible = false;
    L.bg.visible = true;
  });
  await shoot(page, 'B_bg_only_sameTex');

  // ③ CG 层 + 推镜缩放 1.06
  await page.evaluate(() => {
    const g = (window as any).__game;
    const L = g.layers;
    L.cg.visible = true;
    L.bg.visible = false;
    const cx = 1920 / 2, cy = 1080 / 2, s = 1.06;
    L.camera.scale.set(s);
    L.camera.pivot.set(cx * (1 - 1 / s), cy * (1 - 1 / s));
  });
  await shoot(page, 'C_cg_pushin106');

  // ④ 两层都显示（模拟真实）
  await page.evaluate(() => {
    const L = (window as any).__game.layers;
    L.camera.scale.set(1);
    L.camera.pivot.set(0, 0);
    L.cg.visible = true;
    L.bg.visible = true;
  });
  await shoot(page, 'D_both_sameTex');

  writeFileSync(resolve(OUT, 'log.txt'), log.join('\n'), 'utf-8');
  await browser.close();
  console.log('done');
}

void main();
