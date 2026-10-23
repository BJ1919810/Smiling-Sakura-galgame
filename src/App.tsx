/**
 * 游戏主界面
 *
 * 结构（技术设计.md §3.1：画面走 PixiJS，文本与 UI 走 DOM）：
 * - 下层：PixiJS canvas（背景 / 立绘 / CG / 特效）
 * - 上层：React DOM（对话框 / 姓名框 / 选项 / 状态条 / 菜单）
 *
 * 中文排版、可选中复制、无障碍 —— DOM 天生强于 Canvas 手绘，故文本框走 DOM。
 */

import { useEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { Stage } from './render/Stage.ts';
import { Layers } from './render/Layers.ts';
import { assets } from './render/AssetLoader.ts';
import { Director } from './director/Director.ts';
import { TitleScreen } from './ui/screens/TitleScreen.tsx';
import { SaveLoadPanel } from './ui/components/SaveLoadPanel.tsx';
import { SettingsPanel } from './ui/components/SettingsPanel.tsx';
import { MenuPanel } from './ui/components/MenuPanel.tsx';
import { BacklogPanel } from './ui/components/BacklogPanel.tsx';
import { loadSettings, saveSettings, type GameSettings } from './engine/settings.ts';
import {
  installGlobalErrorHooks,
  pushRuntimeError,
  subscribeRuntimeErrors,
  type RuntimeErr,
} from './engine/runtimeDiag.ts';
import type { ScriptProgram } from './engine/script/ops.ts';
import { ScriptProgramSchema } from './engine/script/schema.ts';
import scriptJson from './generated/script.json';

const program: ScriptProgram = ScriptProgramSchema.parse(scriptJson);

/** 游戏内界面状态：标题 → 游戏（存读档/设置面板是叠加层） */
type Screen = 'title' | 'game';

/**
 * 括注旁白：编译器会把「孤立成行的括注」还原成正文（见 parser.ts 的 buildBlocks），
 * 这里按行判断 —— 整行就是一个（…）的，用旁白色渲染（.text-aside），与角色台词区分。
 * ★ 行结构按 fullText 判定，而不是已显示的 text —— 否则打字机打到一半时颜色会闪。
 */
const ASIDE_RE = /^（[^）]*）$/;

/** 移动端「长按进鉴赏模式」判定阈值（见下方 touch 手势） */
const LONG_PRESS_MS = 550;
/** 长按期间手指允许的抖动（超过就当成滑动/翻页，取消长按） */
const LONG_PRESS_MOVE_TOL = 12;
/** 长按与浏览器 contextmenu 可能双触发，这段时间内只认一次 */
const TOGGLE_DEDUP_MS = 600;

function renderDialogue(text: string, fullText: string) {
  const full = fullText.split('\n');
  const shown = text.split('\n');
  return shown.map((line, i) => (
    <span key={i} className={ASIDE_RE.test((full[i] ?? '').trim()) ? 'text-aside' : undefined}>
      {line}
      {i < shown.length - 1 ? '\n' : ''}
    </span>
  ));
}

export default function App() {
  const hostRef = useRef<HTMLDivElement>(null);
  const [screen, setScreen] = useState<Screen>('title');
  /** 叠加面板。`menu`＝游戏菜单（存/读/设置/跳章/主页的入口，2026-09-18 加）；`backlog`＝历史记录 */
  const [panel, setPanel] = useState<'save' | 'load' | 'settings' | 'menu' | 'backlog' | null>(null);
  const [settings, setSettings] = useState<GameSettings>(() => loadSettings());
  /** 右键隐藏 UI（看画面用）。任何左键 / 空格 / 回车都会先恢复 UI，不跳剧情 */
  const [uiHidden, setUiHidden] = useState(false);
  /** 鉴赏模式开关的去重时间戳（长按 与 右键 可能同时到达） */
  const lastToggleRef = useRef(0);
  /** 吃过长按的那次 click 要丢掉，否则会立刻把刚开的鉴赏模式又关回去 */
  const suppressNextClickRef = useRef(false);
  const [director] = useState(() => new Director(program, loadSettings()));

  // —— 运行时错误提示（2026-09-19）：发给别人的版本出问题时，屏上直接显示原因 ——
  //   过去远程机器上的异常完全不可见，只能得到「卡死了」三个字。
  const [runtimeErr, setRuntimeErr] = useState<RuntimeErr | null>(null);
  useEffect(() => {
    installGlobalErrorHooks();
    const unsub = subscribeRuntimeErrors((e) => setRuntimeErr(e));
    return unsub;
  }, []);
  // 错误条 10 秒自动消退（新错误到来会重置计时）
  useEffect(() => {
    if (!runtimeErr) return;
    const t = window.setTimeout(() => setRuntimeErr(null), 10_000);
    return () => clearTimeout(t);
  }, [runtimeErr]);

  // 订阅 Director 的视图更新（快照引用稳定，不会无限重渲染）
  const view = useSyncExternalStore(director.subscribe, director.getSnapshot);

  const inTitle = screen === 'title';
  const hasChoice = !!view.choice;
  /** 有遮罩层（面板 / 选项 / 结局）时不允许隐藏 UI */
  const uiLocked = panel !== null || hasChoice || view.finished;

  /**
   * 鉴赏模式（隐藏对话框 / 状态条 / 场景提示，纯看画面）的**唯一开关**。
   *
   * ★ 为什么不用 `setUiHidden(v => !v)` 直接切：
   *   移动端长按会**同时**触发两路 —— 我们自己的 touch 定时器，以及 WebView 合成的
   *   `contextmenu`。两路都切一次 ⇒ 开了又关，看起来「长按没反应」。
   *   ⇒ 这里用 600ms 去重，无论谁先到，只生效一次。
   */
  const toggleUiHidden = () => {
    if (inTitle || uiLocked) return;
    const now = performance.now();
    if (now - lastToggleRef.current < TOGGLE_DEDUP_MS) return;
    lastToggleRef.current = now;
    setUiHidden((v) => !v);
  };
  /** 供事件监听器读取最新闭包（避免每次 inTitle/uiLocked 变化都重绑监听） */
  const toggleUiHiddenRef = useRef(toggleUiHidden);
  toggleUiHiddenRef.current = toggleUiHidden;

  // —— 右键（桌面）：屏蔽 WebView 菜单 + 切换鉴赏模式 ——
  useEffect(() => {
    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault(); // 桌面端（WebView2）右键菜单由这里关掉
      toggleUiHiddenRef.current();
    };
    window.addEventListener('contextmenu', onContextMenu);
    return () => window.removeEventListener('contextmenu', onContextMenu);
  }, []);

  // —— 移动端：**自己识别长按**进鉴赏模式 ——
  //   ★★ 2026-09-20 必读：这里**不能**依赖浏览器的 `contextmenu`。
  //   本轮为修「界面可被选中变蓝」加了 `-webkit-touch-callout: none` +
  //   `user-select: none`（见 global.css 顶部）——这两条正是 WebView 用来判断
  //   「长按要不要弹菜单 / 要不要派发 contextmenu」的开关。一旦不再派发，
  //   手机就**再也进不了鉴赏模式，而且一点报错都没有**。
  //   ⇒ 自己起定时器，与浏览器行为完全解耦。
  useEffect(() => {
    let timer = 0;
    let startX = 0;
    let startY = 0;

    const cancel = () => {
      if (timer) {
        clearTimeout(timer);
        timer = 0;
      }
    };

    const onStart = (e: TouchEvent) => {
      cancel();
      if (e.touches.length !== 1) return;
      // 落在按钮/链接/输入控件上的长按不参与（那是要按它，不是看画面）
      const t = e.target as Element | null;
      if (t?.closest?.('button, a, input, textarea, select, [role="button"]')) return;

      const touch = e.touches[0];
      startX = touch.clientX;
      startY = touch.clientY;
      // 新手势开始：上一轮遗留的「抑制 click」标记作废
      suppressNextClickRef.current = false;

      timer = window.setTimeout(() => {
        timer = 0;
        // 先挂标记再切状态 —— 手指抬起时产生的 click 必须被丢掉
        suppressNextClickRef.current = true;
        toggleUiHiddenRef.current();
      }, LONG_PRESS_MS);
    };

    const onMove = (e: TouchEvent) => {
      if (!timer) return;
      const touch = e.touches[0];
      if (!touch) {
        cancel();
        return;
      }
      if (
        Math.abs(touch.clientX - startX) > LONG_PRESS_MOVE_TOL ||
        Math.abs(touch.clientY - startY) > LONG_PRESS_MOVE_TOL
      ) {
        cancel(); // 滑动了 ⇒ 不是长按
      }
    };

    const onEnd = () => cancel();

    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: true });
    window.addEventListener('touchend', onEnd, { passive: true });
    window.addEventListener('touchcancel', onEnd, { passive: true });
    return () => {
      cancel();
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onEnd);
      window.removeEventListener('touchcancel', onEnd);
    };
  }, []);

  // 遮罩层出现时强制恢复 UI，避免「隐藏态 + 选项」叠加导致点不到
  useEffect(() => {
    if (uiLocked) setUiHidden(false);
  }, [uiLocked]);

  // —— PixiJS 舞台 ——
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const stage = new Stage();
    let layers: Layers | null = null;
    let raf = 0;
    let last = performance.now();
    /** 看门狗基准：心跳最后一次跳动的时间（rAF 链意外断掉时据此重新拉起） */
    let lastBeat = performance.now();
    let watchdog = 0;

    // ★ WebGL 上下文丢失 → 屏上提示（过去这种失败无声无息，看起来就是整个游戏冻住）
    stage.onContextLost = (isLost) => {
      pushRuntimeError(
        isLost
          ? '检测到图形环境丢失（GPU 驱动重置？），画面可能冻结，正在等待恢复…'
          : '图形环境已恢复，画面继续。',
      );
    };

    void stage.mount(host).then(() => {
      layers = new Layers(stage.root);
      director.bindLayers(layers);

      // dev 模式挂全局 —— 供 tools/shot.ts 等诊断脚本读取舞台实况
      if (import.meta.env.DEV) {
        (window as unknown as Record<string, unknown>).__game = { stage, layers, director, assets };
      }

      // 每帧：推进舞台动画 + Director 打字机
      // ★★ 2026-09-19 加固（修「别人电脑卡死」）：旧写法异常会杀死 rAF 链 ——
      //   `layers.update()` / `director.tick()` 任何一帧抛错，`requestAnimationFrame(loop)`
      //   就执行不到 → 心跳永久停 → 打字机冻住、Pixi 不再出帧（拖窗口画面也不跟随）。
      //   新写法：**先排下一帧，再执行本帧逻辑**，逻辑全部 try/catch ——
      //   单帧异常只丢这一帧（错误上屏），心跳永不断。
      const loop = (now: number) => {
        raf = requestAnimationFrame(loop);
        const dt = now - last;
        last = now;
        lastBeat = now;
        try {
          layers?.update(now, dt);
        } catch (err) {
          pushRuntimeError(`舞台动画异常：${err instanceof Error ? err.message : String(err)}`);
        }
        try {
          director.tick(now);
        } catch (err) {
          pushRuntimeError(`剧情推进异常：${err instanceof Error ? err.message : String(err)}`);
        }
      };
      raf = requestAnimationFrame(loop);

      // ★ 看门狗：rAF 链万一还是断了（如 WebView 在回调外杀死调度），2 秒内重新拉起
      watchdog = window.setInterval(() => {
        if (performance.now() - lastBeat > 5000) {
          cancelAnimationFrame(raf);
          raf = requestAnimationFrame(loop);
          pushRuntimeError('心跳看门狗：检测到渲染循环停转，已重新拉起。');
        }
      }, 2000);
      // ★ start() 只由标题画面「开始游戏 / 继续」按钮触发 ——
      //   此处自动 start 会让对话框在标题层下面透出来
    });

    // ★ 自适应在 Stage 内部完成（ResizeObserver 观察宿主盒子，2026-09-19）——
    //   App 不再自己监听 resize，避免两处各算一次、结果不同源。

    return () => {
      cancelAnimationFrame(raf);
      if (watchdog) clearInterval(watchdog);
      stage.onContextLost = null;
      stage.unmount();
      director.dispose();
      if (import.meta.env.DEV) {
        delete (window as unknown as Record<string, unknown>).__game;
      }
    };
  }, [director]);

  // —— 键盘推进（空格 / 回车）＋ F11 全屏 ＋ Esc 菜单 ——
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // F11 全屏（主人 2026-09-12 定：不要浏览器式提示条）
      // 桌面端走 Tauri 的**系统原生全屏**（无边框窗口，非 HTML fullscreen → WebView2 不弹提示）。
      // ★ 全屏只由 F11 切换；Esc 与全屏解耦（主人 2026-09-12 晚定：Esc = 菜单 / 返回上一级）。
      if (e.code === 'F11') {
        const tauri = (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
        if (!tauri) return; // 浏览器 dev 环境没有 Tauri，保持原生行为不拦
        e.preventDefault();
        void import('@tauri-apps/api/window').then(async ({ getCurrentWindow }) => {
          const win = getCurrentWindow();
          await win.setFullscreen(!(await win.isFullscreen()));
        });
        return;
      }
      // Esc：有面板开着 → 关掉（返回上一级）；没面板且在游戏内 → 唤出菜单
      if (e.code === 'Escape') {
        e.preventDefault();
        setPanel((p) => (p ? null : screen === 'game' ? 'menu' : p));
        return;
      }
      if (e.code === 'Space' || e.code === 'Enter') {
        e.preventDefault();
        if (screen !== 'game') return;
        // 隐藏态：任意按键先恢复 UI，不跳剧情
        if (uiHidden) {
          setUiHidden(false);
          return;
        }
        director.advance();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [director, screen, uiHidden]);

  /** 设置变更：即时生效 + 持久化 */
  const changeSettings = (s: GameSettings) => {
    setSettings(s);
    saveSettings(s);
    director.applySettings(s);
    // 语音音量一并同步（applySettings 内部也会刷，这里保持显式调用的一致性）
    director.audio.setVolumes(s.bgmVolume, s.seVolume, s.voiceVolume);
  };

  /** 点击推进：隐藏态下只负责恢复 UI */
  const advance = () => {
    // ★ 长按刚开完鉴赏模式，手指抬起又送来一个 click ⇒ 必须丢掉。
    //   不丢的话会走到下面 `if (uiHidden) setUiHidden(false)`，
    //   等于长按开了又立刻关 —— 表现为「手机长按完全没反应」。
    if (suppressNextClickRef.current) {
      suppressNextClickRef.current = false;
      return;
    }
    if (uiHidden) {
      setUiHidden(false);
      return;
    }
    director.audio.unlock(); // 浏览器自动播放策略：首次手势解锁
    director.advance();
  };

  const d = view.dialog;

  return (
    /* 正文字号倍率 / 整体 UI 倍率 → CSS 变量
       （--text-scale 见 .text-box p / .name-box；--ui-user 见 .screen 的 --ui-zoom） */
    <div
      className={`app ${uiHidden ? 'ui-hidden' : ''}`}
      style={
        {
          '--text-scale': settings.textScale,
          '--ui-user': settings.uiScale,
        } as CSSProperties
      }
    >
      <div ref={hostRef} className="stage-host" />

      {/* 舞台矩形：画面与 UI 共用同一坐标原点（尺寸由 Stage.fit() 写入 CSS 变量） */}
      <div className="screen">
        {/* —— 标题画面 —— */}
        {inTitle && (
          <TitleScreen
            fingerprint={director.scriptFingerprint}
            onStart={() => {
              setScreen('game');
              director.start();
            }}
            onContinue={(slot) => {
              // ★ 先读档，成功了才切画面 —— 否则失败时会把玩家丢进一个空场景。
              //   失败原因由 Director.flash 显示在标题页上。
              if (!director.load(slot)) return;
              setScreen('game');
              // ★ 存档的 pc 指向「下一句」：读档后要再走一步，画面才不是空白
              director.advance();
            }}
          />
        )}

        {/* 点击层：整屏可点，推进剧情（标题画面时不接管） */}
        {!inTitle && (
          <div
            className="click-layer"
            onClick={advance}
            style={{ pointerEvents: hasChoice || panel ? 'none' : 'auto' }}
          />
        )}

        {/* 顶部状态条（游戏内） */}
        {!inTitle && (
          <div className="status-bar">
            <span className="chip">第 {view.chapter || 1} 章</span>
            <span className="chip">{view.scene || '—'}</span>
            <span className="chip chip-bgm">♪ {view.bgm || '—'}</span>
            <span className="chip chip-dim">
              {view.pc} / {view.total}
            </span>
            <button
              className={`chip chip-btn ${settings.autoPlay ? 'chip-on' : ''}`}
              onClick={() => changeSettings({ ...settings, autoPlay: !settings.autoPlay })}
            >
              自动 {settings.autoPlay ? '开' : '关'}
            </button>
            {/* ★ 2026-09-18（主人定）：存档 / 读档 / 设置 / 跳章 / 主页 全部收进「菜单」面板，
                状态条只留状态显示 + 自动开关 + 这一个入口 */}
            {/* ★ 2026-09-19（主人令）：历史回放提到屏幕上边、设置（菜单）左边 */}
            <button className="chip chip-btn" onClick={() => setPanel('backlog')}>
              历史
            </button>
            <button className="chip chip-btn" onClick={() => setPanel('menu')}>
              菜单
            </button>
          </div>
        )}
        {/* 存读档 / 设置面板 */}
        {panel === 'save' && !inTitle && (
          <SaveLoadPanel mode="save" director={director} onClose={() => setPanel(null)} onDone={() => setPanel(null)} />
        )}
        {panel === 'load' && !inTitle && (
          <SaveLoadPanel mode="load" director={director} onClose={() => setPanel(null)} onDone={() => setPanel(null)} />
        )}
        {panel === 'settings' && !inTitle && (
          <SettingsPanel settings={settings} onChange={changeSettings} onClose={() => setPanel(null)} />
        )}
        {panel === 'backlog' && !inTitle && (
          <BacklogPanel director={director} onClose={() => setPanel(null)} />
        )}
        {panel === 'menu' && !inTitle && (
          <MenuPanel
            chapters={program.chapters}
            onClose={() => setPanel(null)}
            onSave={() => setPanel('save')}
            onLoad={() => setPanel('load')}
            onSettings={() => setPanel('settings')}
            onBacklog={() => setPanel('backlog')}
            onJump={(index) => {
              setPanel(null);
              setUiHidden(false);
              director.jumpToChapter(index);
            }}
            onTitle={() => {
              setPanel(null);
              director.toTitle();
              setScreen('title');
            }}
          />
        )}

        {/* 场景切换提示 */}
        {view.toast && (
          <div className="toast" key={view.toast.id}>
            {view.toast.text}
          </div>
        )}

        {/* 对话框 */}
        {d && (
          <div className="dialog-wrap" onClick={advance}>
            {d.who && <div className="name-box">{d.who}</div>}
            <div className={`text-box ${d.kind === 'inner' ? 'text-inner' : ''}`}>
              <p className={d.kind === 'narration' ? 'text-narration' : ''}>
                {renderDialogue(d.text, d.fullText)}
                {/* ★ 光标在文本流末尾（<p> 内部），而不是兄弟节点 —— 否则另起一行 */}
                {view.isTyping && <span className="caret">▍</span>}
              </p>
              {!view.isTyping && <span className="next-hint">▼</span>}
            </div>
          </div>
        )}

        {/* 选项 */}
        {view.choice && (
          <div className="choice-layer">
            <div className="choice-title">{view.choice.id}</div>
            {view.choice.options.map((o, i) => (
              <button key={o.key} className="choice-btn" onClick={() => director.pick(i)}>
                <span className="choice-key">{o.key}</span>
                {o.text}
              </button>
            ))}
          </div>
        )}

        {view.finished && (
          <div className="finished">
            <div className="finished-inner">
              <h1>未完待续</h1>
              <p>《笑樱》· 篠宫樱线</p>
              <p className="finished-vars">S-warm = {String(director.state.getVar('S-warm', 0))}</p>
              <button className="chip chip-btn" onClick={() => location.reload()}>
                重新开始
              </button>
            </div>
          </div>
        )}
      </div>

      {/* 运行时错误提示：右上角、点击关闭、10s 自动消退（见 runtimeDiag.ts） */}
      {runtimeErr && (
        <div className="runtime-err" key={runtimeErr.id} onClick={() => setRuntimeErr(null)}>
          ⚠ {runtimeErr.msg}
        </div>
      )}
    </div>
  );
}
