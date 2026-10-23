/** 素材接入验证：樱真图立绘 + 旧长椅真图 BG */
import { chromium } from 'playwright';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '../.shots');

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs: string[] = [];
  page.on('pageerror', (e) => errs.push(`[PAGEERROR] ${e.message.slice(0, 300)}`));
  page.on('console', (m) => errs.push(`[${m.type()}] ${m.text().slice(0, 400)}`));
  page.on('pageerror', (e) => e.message);
  // ★ unhandledrejection —— void promise 吞掉的异常
  await page.addInitScript(`
    window.__unhandled = [];
    window.addEventListener('unhandledrejection', (e) => window.__unhandled.push(String(e.reason).slice(0, 300)));
  `);

  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  // 开始游戏
  await page.click('.title-btn:not([disabled])');
  await page.waitForTimeout(400);

  // 推进到樱立绘出现（pc≈31）
  for (let i = 0; i < 80; i++) {
    const pc = (await page.evaluate(`window.__game.director.progress.pc`)) as number;
    const hasSakura = await page.evaluate(`!!window.__game.layers.char.children.find((c) => c.label?.includes?.('sakura') || c.children?.some?.((x) => x.texture))`);
    if (pc > 40 && hasSakura) break;
    await page.mouse.click(800, 250);
    await page.waitForTimeout(70);
    if ((await page.evaluate(`window.__game.director.isTyping`)) as boolean) {
      await page.mouse.click(800, 250);
      await page.waitForTimeout(40);
    }
  }
  await page.waitForTimeout(1200); // 等真图加载

  const s1 = resolve(OUT, '14-樱真图立绘.png');
  await page.screenshot({ path: s1 });
  const info1 = (await page.evaluate(`(() => {
    const g = window.__game;
    return {
      pc: g.director.progress.pc,
      charChildren: g.layers.char.children.length,
      loadedAssets: g.layers ? '见 assets 单例' : '-',
    };
  })()`)) as Record<string, unknown>;
  console.log('① 长椅初遇场景：', JSON.stringify(info1));

  // 强制切到有真图的 BG（诊断快捷方式，等价于剧情走到 CH7-SC04）
  await page.evaluate(`window.__game.layers.setBg('旧长椅_午间')`);
  // 轮询等真图容器出现（最多 10s）
  let bgInfo = { n: 0, labels: [] as string[] };
  for (let i = 0; i < 20; i++) {
    await page.waitForTimeout(500);
    bgInfo = (await page.evaluate(`(() => {
      const bg = window.__game.layers.bg;
      return { n: bg.children.length, labels: bg.children.map((c) => c.label) };
    })()`)) as { n: number; labels: string[] };
    if (bgInfo.labels.includes("bg-real")) break;
  }
  console.log('② bg 层内容：', JSON.stringify(bgInfo), 'layers.id=', (await page.evaluate('window.__game.layers.id')));
  // await 直测（拿真实错误）
  const direct = (await page.evaluate(`(async () => {
    try {
      const tex = await window.__game.assets.bg('旧长椅_午间');
      return tex ? 'ok ' + tex.width + 'x' + tex.height : 'null（failed 记忆 / 无 entry）';
    } catch (e) { return 'ERR ' + String(e).slice(0, 250); }
  })()`)) as string;
  console.log('   assets.bg 直测：', direct);
  const s2 = resolve(OUT, '15-真图背景同框.png');
  await page.screenshot({ path: s2 });

  console.log('页面错误：', errs.length ? errs : '无');
  console.log('截图：', s1, '|', s2);
  await browser.close();
}

main();
