/** 存读档 + 标题画面专项验证 */
import { chromium } from 'playwright';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '../.shots');

async function clickThrough(page: import('playwright').Page, times: number): Promise<void> {
  for (let i = 0; i < times; i++) {
    const has = await page.evaluate(`!!document.querySelector('.choice-btn')`);
    if (has) return; // 到选项了，停
    await page.mouse.click(800, 250);
    await page.waitForTimeout(80);
    const typing = await page.evaluate(`window.__game.director.isTyping`);
    if (typing) {
      await page.mouse.click(800, 250);
      await page.waitForTimeout(40);
    }
  }
}

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  // 1. 标题画面
  const t1 = resolve(OUT, '10-标题画面.png');
  await page.screenshot({ path: t1 });
  const hasTitle = await page.evaluate(`!!document.querySelector('.title-screen')`);
  console.log('① 标题画面：', hasTitle ? '✓' : '✗ 未找到 .title-screen');

  // 2. 开始游戏 → 推进 20 句
  await page.click('.title-btn:not([disabled])');
  await page.waitForTimeout(500);
  await clickThrough(page, 25);
  const inGame = await page.evaluate(`!document.querySelector('.title-screen')`);
  console.log('② 开始游戏进入剧情：', inGame ? '✓' : '✗');

  // 3. 打开存档面板 → 存到 s1
  await page.evaluate(`document.querySelector('.chip-btn')?.click()`); // 第一个按钮 = 存档
  await page.waitForTimeout(400);
  const rows = await page.evaluate(`[...document.querySelectorAll('.slot-row')].map((b) => b.textContent?.slice(0, 40))`);
  console.log('③ 存档面板槽位：', JSON.stringify(rows));
  const s1file = resolve(OUT, '11-存档面板.png');
  await page.screenshot({ path: s1file });
  // 点第二个槽（s1）
  await page.locator('.slot-row').nth(1).click();
  await page.waitForTimeout(300);

  // 4. 推进几句后读档回退
  await clickThrough(page, 8);
  const before = (await page.evaluate(`window.__game.director.progress.pc`)) as number;
  await page.evaluate(`[...document.querySelectorAll('.chip-btn')].find((b) => b.textContent === '读档')?.click()`);
  await page.waitForTimeout(300);
  await page.locator('.slot-row').nth(1).click(); // 读 s1
  await page.waitForTimeout(500);
  const after = (await page.evaluate(`window.__game.director.progress.pc`)) as number;
  console.log(`④ 存档 pc=${before} → 推进 8 句 → 读档 pc=${after}：`, after < before ? '✓ 回退成功' : '✗ 未回退');
  const s2file = resolve(OUT, '12-读档后.png');
  await page.screenshot({ path: s2file });

  // 5. 刷新页面 → 标题「继续」按钮应有档
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForTimeout(800);
  const cont = (await page.evaluate(`(() => {
    const b = [...document.querySelectorAll('.title-btn')].find((x) => x.textContent?.startsWith('继续'));
    return b ? { disabled: b.disabled, text: b.textContent } : null;
  })()`)) as { disabled: boolean; text: string } | null;
  console.log('⑤ 刷新后「继续」按钮：', JSON.stringify(cont), cont && !cont.disabled ? '✓ 持久化成功' : '✗');
  const t3 = resolve(OUT, '13-刷新后标题.png');
  await page.screenshot({ path: t3 });

  await browser.close();
  console.log('\n截图目录：' + OUT);
}

main();
