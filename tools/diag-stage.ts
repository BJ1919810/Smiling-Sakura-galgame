/** 诊断舞台实况：读 PixiJS 各层 children 的 alpha/位置，定位立绘不可见问题 */
import { chromium } from 'playwright';

const URL = 'http://localhost:5173';

interface SpriteInfo {
  label: string;
  alpha: number;
  x: number;
  y: number;
  visible: boolean;
  children: number;
}

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);

  // 推进到立绘出现
  for (let i = 0; i < 10; i++) {
    await page.mouse.click(800, 250);
    await page.waitForTimeout(220);
  }
  await page.waitForTimeout(1600);

  const dump = await page.evaluate(`(() => {
    const g = window.__game;
    if (!g) return { error: '__game 未挂载（DEV 模式才有）' };
    const dumpLayer = (layer) =>
      layer.children.map((c) => ({
        label: c.label,
        alpha: Number(c.alpha.toFixed(3)),
        x: Math.round(c.x),
        y: Math.round(c.y),
        visible: c.visible,
        children: c.children.length,
      }));
    return {
      pc: g.director.progress.pc,
      dialogWho: g.director.dialog ? g.director.dialog.who : null,
      bg: dumpLayer(g.layers.bg),
      char: dumpLayer(g.layers.char),
      cg: dumpLayer(g.layers.cg),
    };
  })()`) as {
    error?: string;
    pc?: number;
    dialogWho?: string | null;
    bg?: SpriteInfo[];
    char?: SpriteInfo[];
    cg?: SpriteInfo[];
  };

  console.log(JSON.stringify(dump, null, 2));
  console.log('\nerrors:', errors.length ? errors : '无');
  await browser.close();
}

main();
