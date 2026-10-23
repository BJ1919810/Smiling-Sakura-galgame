/**
 * 白线取证 · 第五轮：受控注入（2026-09-18）
 *
 * 思路：把「同一画面、无动画、无 UI、无花瓣」固定住，然后**只注入一个变量**，前后互差。
 *   基线 E0  ：稳定后抓图
 *   E1        ：强制载入全部 47 张 CG 纹理后再抓（模拟"剧情推进后纹理/显存累积"）
 *   E2        ：再连发 30 次 setBg 人为制造 bg 层泄漏后抓（复现真实的层泄漏）
 * 三张内容完全相同（都是同一张 BG、相机不动）⇒ 两两互差里出现的东西就是累积伪影。
 *
 * 同时把每张的「bg 层孩子数 / 顶层 alpha」打出来，确认基线确实已经淡完。
 *
 * 用法：node --experimental-strip-types tools/diag-inject.ts
 */
import { chromium } from 'playwright';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const OUT = resolve(ROOT, '.workbuddy/tmp/inject');
const URL = 'http://localhost:5173';

const manifest = JSON.parse(
  readFileSync(resolve(ROOT, 'src/generated/assets.manifest.json'), 'utf-8')
) as { cg: Record<string, unknown> };
const CG_KEYS = Object.keys(manifest.cg);
console.log('CG 键数 =', CG_KEYS.length);

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs: string[] = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));

  const hideUI = (on: boolean) =>
    page.evaluate((v) => {
      const L = (window as any).__game.layers;
      L.sakura.container.visible = !v;
      document.querySelectorAll('.status-bar,.text-box,.name-box,.choice-panel,.toast').forEach((e) => {
        (e as HTMLElement).style.visibility = v ? 'hidden' : '';
      });
    }, on);

  /** 抓到「顶层 bg 已淡完(alpha≈1)」且无 CG 的稳定帧 */
  const waitStable = async (): Promise<any> => {
    for (let i = 0; i < 60; i++) {
      const st = await page.evaluate(() => {
        const L = (window as any).__game.layers;
        const kids = L.bg.children as any[];
        const top = kids[kids.length - 1];
        return {
          kids: kids.length,
          topLabel: top?.label ?? '',
          topAlpha: top?.alpha ?? -1,
          cgKids: L.cg.children.length,
          typing: (window as any).__game.director.isTyping,
        };
      });
      if (st.kids >= 1 && st.topAlpha > 0.995 && st.cgKids === 0 && !st.typing) return st;
      await page.waitForTimeout(250);
    }
    return await page.evaluate(() => {
      const L = (window as any).__game.layers;
      const kids = L.bg.children as any[];
      return { kids: kids.length, topLabel: kids[kids.length - 1]?.label, topAlpha: kids[kids.length - 1]?.alpha, cgKids: L.cg.children.length, typing: false };
    });
  };

  const snap = async (name: string) => {
    await page.screenshot({ path: resolve(OUT, name) });
    console.log('  📷', name);
  };

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1500);

  // 推进到一个「无 CG 的稳定 BG」
  for (let i = 0; i < 400; i++) {
    const st = await page.evaluate(() => {
      const L = (window as any).__game.layers;
      return { cg: L.cg.children.length, bg: L.bg.children.length };
    });
    if (st.cg === 0 && st.bg >= 1) break;
    await page.evaluate(() => {
      const d = (window as any).__game.director;
      if (d.choice) d.pick(0);
      else d.advance();
    });
    await page.waitForTimeout(60);
  }

  await hideUI(true);
  let st = await waitStable();
  console.log('基线状态:', JSON.stringify(st));
  await page.waitForTimeout(800);
  await snap('E0_base.png');

  // ① 强制载入全部 CG 纹理
  const t0 = Date.now();
  await page.evaluate(async (keys: string[]) => {
    const assets = (window as any).__game.assets;
    for (const k of keys) {
      try { await assets.cg(k); } catch { /* 忽略单张失败 */ }
    }
  }, CG_KEYS);
  console.log(`① 载入 ${CG_KEYS.length} 张 CG 纹理，用时 ${((Date.now() - t0) / 1000).toFixed(1)}s`);
  await page.waitForTimeout(2500);
  st = await waitStable();
  console.log('  状态:', JSON.stringify(st), '已载纹理', await page.evaluate(() => (window as any).__game.assets.loadedCount));
  await snap('E1_afterTextures.png');

  // ② 连发 setBg 制造层泄漏（同一张图，画面内容不变）
  //   ⚠️ 同步连发会被 seq 乱序防护整个吞掉（同步循环里 seq 全加完，只有最后一次落地）。
  //   真实泄漏窗口 = 间隔 > 加载耗时（缓存命中 ≈ microtask）、< 420ms 淡出期：
  //   每次落地时 children[0] 还是被淡出中的旧层 → 中间已淡入的层被跳过销毁、永久滞留。
  const curName = await page.evaluate(() => {
    const L = (window as any).__game.layers;
    return String((L.bg.children[L.bg.children.length - 1] as any).label).replace(/^bg:/, '');
  });
  console.log(`② 连发 setBg(${curName}) ×30，间隔 250ms …`);
  for (let i = 0; i < 30; i++) {
    await page.evaluate((n: string) => (window as any).__game.layers.setBg(n), curName);
    await page.waitForTimeout(250);
  }
  await page.waitForTimeout(2500);
  st = await waitStable();
  console.log('② 泄漏后状态 →', JSON.stringify(st));
  await snap('E2_afterLeak.png');

  writeFileSync(resolve(OUT, 'errs.txt'), errs.join('\n'), 'utf-8');
  console.log('\n错误', errs.length);
  await browser.close();
}

void main();
