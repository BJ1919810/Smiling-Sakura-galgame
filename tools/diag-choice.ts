/** 选项链路专项验证：推进到 C-01 → 截图选项 UI → 分别选 A / C，对比窗口内台词 */
import { chromium } from 'playwright';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function runChoice(pick: number): Promise<string[]> {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  // 疯狂点击直到出现选项（C-01 在 pc≈51；每句需 2 次点击：补全+推进）
  for (let i = 0; i < 200; i++) {
    const has = await page.evaluate(`!!document.querySelector('.choice-btn')`);
    if (has) break;
    await page.mouse.click(800, 250);
    await page.waitForTimeout(70);
    // 长句打字中：多点一次瞬补
    const typing = await page.evaluate(`window.__game.director.isTyping`);
    if (typing) {
      await page.mouse.click(800, 250);
      await page.waitForTimeout(40);
    }
  }

  const choiceInfo = await page.evaluate(`(() => {
    const btns = [...document.querySelectorAll('.choice-btn')].map((b) => b.textContent);
    return btns;
  })()`);

  if (pick === 0) {
    const file = resolve(__dirname, '../.shots/09-选项UI.png');
    await page.screenshot({ path: file });
    console.log('选项 UI 截图：' + file);
    console.log('按钮：', JSON.stringify(choiceInfo));
  }

  // 点选第 pick 项
  await page.locator('.choice-btn').nth(pick).click();

  // 收集选项之后 5 条 say（窗口内变体）
  const says: string[] = [];
  for (let i = 0; i < 12; i++) {
    const t = (await page.evaluate(`(() => {
      const g = window.__game;
      return { typing: g.director.isTyping, text: g.director.dialog ? g.director.dialog.text : null, who: g.director.dialog ? g.director.dialog.who : null };
    })()`)) as { typing: boolean; text: string | null; who: string | null };
    if (t.text && t.text.length > 0) {
      const last = says[says.length - 1];
      // 打字完成态才记录（避免同句多次收集）
      const done = await page.evaluate(`!window.__game.director.isTyping`);
      if (done && (!last || last !== `${t.who ?? ''}::${t.text}`)) {
        says.push(`${t.who ?? ''}::${t.text}`);
      }
    }
    await page.mouse.click(800, 250);
    await page.waitForTimeout(200);
  }
  await browser.close();
  return says.slice(0, 5);
}

async function main() {
  console.log('=== 选 A（你怎么知道的？）===');
  const a = await runChoice(0);
  for (const s of a) console.log('  ' + s);

  console.log('\n=== 选 C（沉默，不接话）===');
  const c = await runChoice(2);
  for (const s of c) console.log('  ' + s);

  console.log('\n=== 差异对比 ===');
  console.log('分支生效：', JSON.stringify(a) !== JSON.stringify(c) ? '✓ A/C 台词不同' : '✗ 相同（仍有问题）');
}

main();
