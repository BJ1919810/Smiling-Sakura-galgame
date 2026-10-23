/**
 * 白线取证 · 第四轮（修正版）：累积型状态（2026-09-18）
 *
 * 主人新线索：**同一张 BG，开局干净，剧情推进后就出现白线**（举例：教室那张）。
 * ⇒ 排除静态渲染路径与素材，指向「随游玩累积的状态」。
 *
 * 上一版脚本的教训：快跑 480 步后画面冻住 —— 因为 `Director.advance()` 在**有选项等待**时
 * 直接 return（Director.ts:425 `if (this._choice) return`）。本版自动 `pick(0)`。
 *
 * 本版做两件事：
 *   ① 记录累积指标：bg 层孩子数 / cg 层孩子数 / 已加载纹理数 / 相机 scale·pivot·xy / pc
 *   ② 每当「cg 层为空」就抓一张无 UI 无花瓣的纯画面（文件名带 BG key 与步号），
 *      以便用「同一张图的前后截图互差」把渲染引入的东西剥出来。
 *
 * 用法：node --experimental-strip-types tools/diag-accum.ts
 */
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '../.workbuddy/tmp/accum');
const URL = 'http://localhost:5173';
const BATCH = 10;
const MAX_STEPS = 4000;

async function probe(page: import('playwright').Page, step: number) {
  return page.evaluate((st) => {
    const g = (window as any).__game;
    const L = g.layers;
    const d = g.director;
    const prog = d.progress;
    const kids = L.bg.children.map((c: any) => c.label || '?');
    return {
      step: st,
      pc: prog.pc,
      total: prog.total,
      chapter: prog.chapter,
      scene: prog.sceneId,
      bgKids: kids.length,
      bgTop: kids[kids.length - 1] || '',
      cgKids: L.cg.children.length,
      textures: g.assets.loadedCount,
      camScale: +L.camera.scale.x.toFixed(6),
      camPivot: [+L.camera.pivot.x.toFixed(2), +L.camera.pivot.y.toFixed(2)],
      camXY: [+L.camera.x.toFixed(2), +L.camera.y.toFixed(2)],
    };
  }, step);
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs: string[] = [];
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));

  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await page.getByRole('button', { name: /开始|继续/ }).first().click().catch(() => {});
  await page.waitForTimeout(1500);

  // GPU 与渲染器参数（判断显存/驱动是否是伪影的诱因）
  const gpu = await page.evaluate(() => {
    const canvas = document.querySelector('canvas') as HTMLCanvasElement | null;
    const ctx = canvas?.getContext('webgl2') as WebGL2RenderingContext | null;
    if (!ctx) return { err: 'no-webgl2' };
    const ext = ctx.getExtension('WEBGL_debug_renderer_info');
    return {
      renderer: ext ? ctx.getParameter(ext.UNMASKED_RENDERER_WEBGL) : ctx.getParameter(ctx.RENDERER),
      vendor: ext ? ctx.getParameter(ext.UNMASKED_VENDOR_WEBGL) : ctx.getParameter(ctx.VENDOR),
      maxTexture: ctx.getParameter(ctx.MAX_TEXTURE_SIZE),
      aa: ctx.getContextAttributes()?.antialias,
      dpr: window.devicePixelRatio,
      canvas: `${canvas?.width}x${canvas?.height} (css ${canvas?.clientWidth}x${canvas?.clientHeight})`,
    };
  });
  console.log('GPU/渲染器:', JSON.stringify(gpu));

  const snaps: any[] = [];
  const shots: { file: string; bg: string; step: number; pc: number; tag?: string }[] = [];
  /** 每个 BG 标签已抓次数（首次→A_，之后→B_ 覆盖） */
  const seen = new Map<string, number>();
  let finished = false;

  const hideUI = (v: boolean) =>
    page.evaluate((on) => {
      const L = (window as any).__game.layers;
      L.sakura.container.visible = !on;
      document.querySelectorAll('.status-bar,.text-box,.name-box,.choice-panel').forEach((e) => {
        (e as HTMLElement).style.visibility = on ? 'hidden' : '';
      });
    }, v);

  for (let s = 0; s < MAX_STEPS && !finished; s += BATCH) {
    finished = await page.evaluate((n) => {
      const d = (window as any).__game.director;
      for (let i = 0; i < n; i++) {
        if (d.choice) d.pick(0);
        else if (!d.finished) d.advance();
      }
      return d.finished;
    }, BATCH);
    await page.waitForTimeout(70);

    if (s % 200 === 0) {
      const p = await probe(page, s);
      snaps.push(p);
      console.log(
        `pc ${String(p.pc).padStart(4)}/${p.total} 第${p.chapter}章 | bg层${p.bgKids}(${p.bgTop}) | cg层${p.cgKids} | ` +
        `纹理${String(p.textures).padStart(2)} | cam${p.camScale} piv${p.camPivot} xy${p.camXY}`
      );
    }

    const st = await page.evaluate(() => {
      const L = (window as any).__game.layers;
      const kids = L.bg.children;
      return {
        cgKids: L.cg.children.length,
        top: (kids[kids.length - 1] as any)?.label ?? '',
        typing: (window as any).__game.director.isTyping,
      };
    });
    // ★ 同一 BG 标签只留「首次(A)」与「最后一次(B)」两张 —— 两张是同一张 BG 文件、相机不动、
    //   UI/花瓣全隐 ⇒ 互差＝把渲染引入的东西从画面里剥出来。跨大段累积的同图 A/B 才看得出恶化。
    if (st.cgKids === 0 && st.top && !st.typing) {
      const label = st.top.replace(/^bg:/, '').replace(/[\\/:*?"<>|]/g, '_');
      const p = await probe(page, s);
      await hideUI(true);
      await page.waitForTimeout(300);
      const isFirst = !seen.has(label);
      const file = `${isFirst ? 'A' : 'B'}_${label}.png`;
      await page.screenshot({ path: resolve(OUT, file) });
      await hideUI(false);
      seen.set(label, (seen.get(label) ?? 0) + 1);
      if (isFirst) shots.push({ file, bg: st.top, step: s, pc: p.pc, tag: 'first' });
      else if (file.includes('B_')) shots.push({ file, bg: st.top, step: s, pc: p.pc, tag: 'late' });
      console.log(`      📷 ${file}  pc${p.pc}  纹理${p.textures}  bg层${p.bgKids}`);
    }
  }

  const last = await probe(page, MAX_STEPS);
  snaps.push(last);

  writeFileSync(resolve(OUT, 'snaps.json'), JSON.stringify({ snaps, shots, errs, finished }, null, 2), 'utf-8');
  console.log('\n=== 累积指标 ===');
  for (const p of snaps) {
    console.log(`pc${String(p.pc).padStart(4)} | bg层${p.bgKids} cg层${p.cgKids} 纹理${String(p.textures).padStart(2)} cam${p.camScale} piv${p.camPivot}`);
  }
  console.log('\n抓图', shots.length, '张；到达结尾:', finished, '；错误', errs.length);
  await browser.close();
}

void main();
