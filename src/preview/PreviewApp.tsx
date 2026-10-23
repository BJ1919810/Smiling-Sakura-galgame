/**
 * 雀跃演出预览页（dev 专用，vite build 不打包 → 不进正式 exe）
 *
 * 复用正式运行时的 Stage / Layers / Director，只是把剧本换成
 * 「只搭目标场景」的最小合集（program.ts）。看到的手感＝正式游戏的手感。
 */

import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Stage } from '../render/Stage.ts';
import { Layers } from '../render/Layers.ts';
import { Director } from '../director/Director.ts';
import { previewProgram } from './program.ts';

export default function PreviewApp() {
  const hostRef = useRef<HTMLDivElement>(null);
  const [director] = useState(() => new Director(previewProgram));
  const view = useSyncExternalStore(director.subscribe, director.getSnapshot);
  const d = view.dialog;

  // —— PixiJS 舞台（与 App.tsx 同一套装配，保证手感一致） ——
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const stage = new Stage();
    let layers: Layers | null = null;
    let raf = 0;
    let last = performance.now();

    void stage.mount(host).then(() => {
      layers = new Layers(stage.root);
      director.bindLayers(layers);

      // 本页整体就是 dev 工具 → 直接暴露给诊断脚本（tools/*.ts / 控制台）
      const w = window as unknown as Record<string, unknown>;
      w.__mountCount = ((w.__mountCount as number) ?? 0) + 1;
      w.__preview = { stage, layers, director };

      // ★ start() 在 promise 链里，异常会被静默吞掉（.then 回调的 throw = unhandled rejection）
      //   —— 显式接住并暴露，诊断不抓瞎
      try {
        director.start(); // 预览页跳过标题画面，直接进场景
      } catch (e) {
        (window as unknown as Record<string, unknown>).__startErr =
          e instanceof Error ? `${e.message}\n${e.stack}` : String(e);
        console.error('[preview] start 失败:', e);
      }

      const loop = (now: number) => {
        const dt = now - last;
        last = now;
        layers?.update(now, dt);
        director.tick(now);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    });

    return () => {
      cancelAnimationFrame(raf);
      stage.unmount();
      director.dispose();
    };
  }, [director]);

  const advance = () => director.advance();

  return (
    <div className="app">
      <div ref={hostRef} className="stage-host" />

      <div className="screen">
        <div className="click-layer" onClick={advance} />

        <div
          style={{
            position: 'absolute',
            top: 12,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 35,
            padding: '6px 18px',
            borderRadius: 999,
            background: 'rgba(26,23,32,0.82)',
            border: '1px solid rgba(232,160,180,0.28)',
            color: '#f5f2ee',
            fontSize: 13,
            letterSpacing: 1,
            pointerEvents: 'none',
          }}
        >
          雀跃预览 · 点按推进 · 每步括注标注「触发 / 不触发」 · 对应正式游戏 CH2-SC03 等处
        </div>

        {d && (
          <div className="dialog-wrap" onClick={advance}>
            {d.who && <div className="name-box">{d.who}</div>}
            <div className={`text-box ${d.kind === 'inner' ? 'text-inner' : ''}`}>
              <p className={d.kind === 'narration' ? 'text-narration' : ''}>
                {d.text}
                {view.isTyping && <span className="caret">▍</span>}
              </p>
              {!view.isTyping && <span className="next-hint">▼</span>}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
