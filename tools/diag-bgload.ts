/** BG 真图加载失败的隔离测试：浏览器 Image 解码 + PixiJS Assets.load 各测一遍 */
import { chromium } from 'playwright';

async function main() {
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const p = await b.newPage();
  const errs: string[] = [];
  p.on('console', (m) => errs.push(`[${m.type()}] ${m.text().slice(0, 200)}`));
  p.on('pageerror', (e) => errs.push(`[PAGEERROR] ${e.message.slice(0, 300)}`));

  await p.goto('http://localhost:5173', { waitUntil: 'domcontentloaded' });
  await p.waitForTimeout(1500);

  // ① 浏览器原生 Image 解码
  const imgResult = (await p.evaluate(`(async () => {
    const results = {};
    for (const [name, url] of Object.entries({ bg: '/assets/bg/bg_025.png', char: '/assets/char/sakura.png' })) {
      results[name] = await new Promise((res) => {
        const img = new Image();
        img.onload = () => res('ok ' + img.naturalWidth + 'x' + img.naturalHeight);
        img.onerror = (e) => res('ERR ' + String(e.type));
        img.src = url;
      });
    }
    return results;
  })()`)) as Record<string, string>;
  console.log('① 原生 Image 解码:', JSON.stringify(imgResult));

  // ② fetch 响应
  const fetchResult = (await p.evaluate(`(async () => {
    const r = await fetch('/assets/bg/bg_025.png');
    const buf = await r.arrayBuffer();
    return r.status + ' ' + buf.byteLength + 'B 头=' + new Uint8Array(buf.slice(0, 4)).join(',');
  })()`)) as string;
  console.log('② fetch:', fetchResult);

  // ③ PixiJS Assets.load（用应用同款依赖）
  const pixiResult = (await p.evaluate(`(async () => {
    try {
      const mod = await import('/src/render/AssetLoader.ts');
      const tex = await mod.assets.bg('旧长椅_午间');
      return tex ? 'ok ' + tex.width + 'x' + tex.height : 'null';
    } catch (e) {
      return 'ERR ' + String(e).slice(0, 250);
    }
  })()`)) as string;
  console.log('③ PixiJS Assets.load:', pixiResult);

  console.log('--- 页面 console ---');
  for (const e of errs.slice(0, 10)) console.log(' ', e);
  await b.close();
}

main();
