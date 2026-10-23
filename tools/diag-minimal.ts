/** 最小实验：不推进剧情，加载后直接 setBg，隔离「推进过程干扰」假设 */
import { chromium } from 'playwright';

async function main() {
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const p = await b.newPage({ viewport: { width: 1600, height: 900 } });
  // ★ 网络监听：bg_025 是否被请求
  p.on('response', (r) => {
    if (r.url().includes('bg_025') || r.url().includes('sakura'))
      console.log(`[net] ${r.status()} ${r.url().slice(-30)}`);
  });
  p.on('requestfailed', (r) => {
    if (r.url().includes('bg_025') || r.url().includes('sakura'))
      console.log(`[reqfail] ${r.url().slice(-30)} ${r.failure()?.errorText}`);
  });
  const errs: string[] = [];
  p.on('console', (m) => {
    const t = m.text();
    if (t.includes('[setBg]') || t.includes('Error') || m.type() === 'error') errs.push(`[${m.type()}] ${t.slice(0, 300)}`);
  });
  p.on('pageerror', (e) => errs.push(`[PAGEERROR] ${e.message.slice(0, 300)}`));
  await p.addInitScript(`
    window.__un = [];
    window.addEventListener('unhandledrejection', (e) => window.__un.push(String(e.reason).slice(0, 300)));
  `);

  await p.goto('http://localhost:5173', { waitUntil: 'networkidle' });
  for (let i = 0; i < 40; i++) {
    if (await p.evaluate(`!!window.__game`)) break;
    await p.waitForTimeout(200);
  }
  await p.waitForTimeout(500);

  // 实验分组：A = 先直测（预热缓存）再 setBg；B = 直接 setBg
  const mode = process.env.MODE ?? 'A';
  if (mode === 'A') {
    const r = (await p.evaluate(`window.__game.assets.bg('旧长椅_午间').then((t) => t ? '预热ok' : '预热null')`)) as string;
    console.log('预热直测:', r);
  }
  await p.evaluate(`window.__bgState = {}; window.__game.layers.setBg('旧长椅_午间')`);
  for (let i = 0; i < 16; i++) {
    await p.waitForTimeout(600);
    const n = (await p.evaluate(`window.__game.layers.bg.children.length`)) as number;
    if (n >= 2) {
      console.log(`✓ [${mode}] 第 ${i * 600}ms 真图加入，children=${n}`);
      break;
    }
    if (i === 15) console.log(`✗ [${mode}] 10s 内真图未加入，children=${n}`);
  }
  const st = (await p.evaluate(`window.__bgState`)) as Record<string, unknown> | undefined;
  console.log('__bgState:', JSON.stringify(st));
  const un = (await p.evaluate(`window.__un`)) as string[];
  console.log('unhandledrejection:', un.length ? un : '无');
  console.log('console 错误:', errs.length ? errs : '无');
  await p.screenshot({ path: 'D:/0Coding/pythonworkspace/My_program/笑樱-galgame/.shots/16-最小实验.png' });
  await b.close();
}

main();
