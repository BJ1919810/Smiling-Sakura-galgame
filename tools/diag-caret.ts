/** 光标位置专项验证：点击后立即截图，抓打字机中间态 */
import { chromium } from 'playwright';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);

  // 推进若干句 → 循环点击直到「恰好处于打字中」→ 立即测量
  await page.mouse.click(800, 250);
  await page.waitForTimeout(600);
  for (let i = 0; i < 30; i++) {
    const typing = await page.evaluate('window.__game.director.isTyping');
    if (typing) break;
    await page.mouse.click(800, 250);
    await page.waitForTimeout(60);
  }
  const pos = await page.evaluate(`(() => {
    const p = document.querySelector('.text-box p');
    const caret = document.querySelector('.caret');
    const typing = window.__game ? window.__game.director.isTyping : '?';
    if (!p) return { err: 'p 未找到', typing };
    const pr = p.getBoundingClientRect();
    if (!caret) return { err: 'caret 未找到（打字已结束？）', typing, pText: p.textContent.slice(0, 30) };
    const cr = caret.getBoundingClientRect();
    return {
      typing,
      pTop: Math.round(pr.top), caretTop: Math.round(cr.top),
      caretLeft: Math.round(cr.left), pRight: Math.round(pr.right),
      caretInsideVertically: cr.top >= pr.top - 2 && cr.bottom <= pr.bottom + 2,
      pText: p.textContent.slice(0, 30),
    };
  })()`);
  console.log(JSON.stringify(pos, null, 2));
  const file = resolve(__dirname, '../.shots/08-光标中间态.png');
  await page.screenshot({ path: file });
  console.log('截图：' + file);
  await browser.close();
}

main();
