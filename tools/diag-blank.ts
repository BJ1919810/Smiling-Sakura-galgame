/** 白屏诊断：抓 console/pageerror/请求失败 */
import { chromium } from 'playwright';

async function main() {
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const p = await b.newPage();
  const errs: string[] = [];
  p.on('console', (m) => {
    if (m.type() === 'error' || m.type() === 'warning') errs.push(m.type() + ': ' + m.text().slice(0, 250));
  });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message.slice(0, 400)));
  p.on('requestfailed', (r) => errs.push('REQFAIL: ' + r.url().slice(0, 140)));
  await p
    .goto('http://localhost:5173', { waitUntil: 'networkidle', timeout: 15000 })
    .catch((e) => errs.push('GOTO-ERR: ' + e.message.slice(0, 200)));
  await p.waitForTimeout(2000);
  const rootHtml = (await page_safe(p)) ?? -1;
  console.log('#root innerHTML 长度:', rootHtml);
  console.log('--- 错误/警告（前 15 条）---');
  for (const e of errs.slice(0, 15)) console.log(' ', e);
  if (!errs.length) console.log('  无');
  await b.close();
}

async function page_safe(p: import('playwright').Page): Promise<number | null> {
  try {
    return (await p.evaluate(`document.getElementById('root')?.innerHTML.length ?? -1`)) as number;
  } catch {
    return null;
  }
}

main();
