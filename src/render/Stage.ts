import { Application, Container, Graphics, Text } from 'pixi.js';

/** 设计分辨率 —— 全项目统一的演出坐标系（技术设计.md §11） */
export const DESIGN_WIDTH = 1920;
export const DESIGN_HEIGHT = 1080;

/** 渲染缓冲延后重分配的静默期：窗口静止这么久后才真正 renderer.resize()（修拖动闪烁） */
const BUFFER_QUIET_MS = 150;

/**
 * 整体 UI 缩放（2026-09-20）—— 见 global.css 的 `.screen`。
 *
 * `--ui-auto = clamp(舞台高 / UI_REF_STAGE_H, UI_AUTO_MIN, 1)`，再由玩家倍率相乘。
 *
 * ★ 基准取 720：1280×720 / 1600×900 / 1920×1080 这类桌面窗口算出来都 ≥1
 *   ⇒ 夹到 1.0，**桌面视觉零变化**（主人从没抱怨过桌面大小，不能动它）。
 * ★ 下限取 0.6：手机横屏（891×410，舞台 729×410）得 0.569 → 夹到 0.6
 *   ⇒ 对话框由占屏高 50% 降到 30%。⛔ 别往下调 —— 0.38 那档正文只剩 8px，主人已否决。
 * ⛔ 千万别改成 `stageH / 1080`：那正是被否掉的「设计画布」，手机会缩到 0.38。
 */
const UI_REF_STAGE_H = 720;
const UI_AUTO_MIN = 0.6;

/**
 * PixiJS 舞台：只管画布、图层与「设计坐标系 → 窗口」的等比缩放（letterbox）。
 *
 * 边界（技术设计.md §3.1）：本类**不承载任何剧情逻辑**。
 * 指令解释在 engine 层，演出编排在 director 层，这里只是它们落笔的画布。
 */
export class Stage {
  readonly app = new Application();

  /** 设计坐标系根容器：所有图层挂它下面，缩放只作用于它 */
  readonly root = new Container();

  private host: HTMLElement | null = null;
  private mounted = false;
  /** rAF 合并标记：尺寸变化高频到来时合并到每帧最多一次 applyFit() */
  private fitQueued = false;
  /** 合并期间最近一次请求的尺寸 */
  private wPending = 1;
  private hPending = 1;
  /** 渲染缓冲延后重分配的定时器（拖动中不重建 WebGL 缓冲，修闪烁） */
  private bufferTimer: number | null = null;
  /** 渲染缓冲当前对应的视口尺寸（拖动中锁定不变，letterbox 以它为基准） */
  private viewW = 1;
  private viewH = 1;

  /** 尺寸同步回调（挂在 ResizeObserver / resize / visualViewport 上，卸载时解绑） */
  private syncSize: (() => void) | null = null;
  /** 尺寸观察器（观察宿主盒子，2026-09-19） */
  private sizeObserver: ResizeObserver | null = null;
  /** 定时校验兜底（切分辨率等不派发事件的场景，2026-09-19） */
  private sizeTimer: number | null = null;
  /** dpr 监听（matchMedia resolution）与其解绑句柄 */
  private dprMq: MediaQueryList | null = null;
  private dprHandler: (() => void) | null = null;
  /** focus / visibilitychange 的解绑句柄 */
  private onFocusSync: (() => void) | null = null;
  private onVisibilitySync: (() => void) | null = null;

  /**
   * WebGL 上下文丢失/恢复的外部通知（2026-09-19，修「别人电脑卡死」）。
   * true＝丢失（GPU 驱动重置等，Pixi 不再出帧）；false＝已恢复。
   * App 层接它弹屏上提示 —— 过去这种失败是无声的，看起来就是「整个游戏冻住」。
   */
  onContextLost: ((isLost: boolean) => void) | null = null;

  /** preventDefault 是规范要求：声明「我们会恢复」，浏览器才会派发 restored 事件 */
  private handleContextLost = (e: Event): void => {
    e.preventDefault();
    this.onContextLost?.(true);
  };

  private handleContextRestored = (): void => {
    // Pixi v8 对恢复的支持有限，强制补一帧让画面立刻回来；失败也不致命
    try {
      this.app.render();
    } catch {
      /* 下一帧 ticker 会再试 */
    }
    this.onContextLost?.(false);
  };

  async mount(host: HTMLElement): Promise<void> {
    this.host = host;

    try {
      // ★ 渲染缓冲跟随视口（不是固定 1920×1080）
      // 否则 canvas 只有设计分辨率大小，右侧/下方会留黑边。
      // 设计坐标系由 root 容器统一缩放（见 fit()）。
      // ★★ 尺寸取自 **宿主盒子的布局尺寸**（不是 window.innerWidth/innerHeight）：
      //    2026-09-19 修「手机 & 朋友电脑 UI 不动态适配」—— Android 沉浸模式下
      //    window.innerHeight 与 `position:fixed; inset:0` 的盒子**不同源**
      //    （innerHeight 含已隐藏的系统栏、随缩放/键盘变化），于是 JS 算出的舞台矩形
      //    与真实可视区对不上 ⇒ UI 整体错位（「继续游戏」被挤出下边框）。
      //    尺寸唯一来源＝宿主盒子，见 App.tsx 的 syncStageSize()（ResizeObserver 驱动）。
      const w0 = Math.max(1, Math.round(host.clientWidth) || window.innerWidth);
      const h0 = Math.max(1, Math.round(host.clientHeight) || window.innerHeight);
      await this.app.init({
        width: w0,
        height: h0,
        background: '#12101a',
        // ★★ `antialias: false` ＝ **白线修复项**（2026-09-18 定案，主人实机确认），**不是实验残留**。
        //   症状：画面出现一条左上→右下的细白线（CG 必有；剧情推进后同一张底图也出现），**只在打包版 WebView2 里可见**。
        //   判别关键＝**线不随 CG 的 alpha 变淡，只在 CG 被销毁的瞬间消失** ⇒ 说明它不是「画上去的像素」，
        //   而是全屏 Sprite 的 **quad coverage 漏缝**：MSAA 沿 quad 的三角共享对角边留下一条不退场的亮缝。
        //   ⛔ 不要顺手"还原成 true"——若确要改，必须重新走一遍白线验证。
        //   完整排查与结论：`技术设计.md` 附录 B.4 #9；备份 `.workbuddy/backup/Stage.ts_关antialias前_20260918.ts`。
        antialias: false,
        autoDensity: true,
        resolution: window.devicePixelRatio || 1,
        // ★ 强制 WebGL（2026-09-12）：Pixi v8 默认优先 WebGPU，在 WebView2（exe）里
        //   会把 GPU 进程撞崩 → 开屏黑屏 → 数十秒后整只 exe 闪退；浏览器端 GPU 栈不同
        //   才侥幸能跑。钉死 WebGL 保住硬件加速，且 Web/WebGL 两端行为一致。
        preference: 'webgl',
      });
    } catch (err) {
      console.error('[Stage] PixiJS 初始化失败', err);
      this.host = null;
      return;
    }

    // 初始化是异步的，期间可能已被卸载
    if (!this.host) {
      this.app.destroy(true);
      return;
    }

    this.root.label = 'design-root';
    this.app.stage.addChild(this.root);
    host.appendChild(this.app.canvas);
    this.mounted = true;

    // ★ WebGL 上下文丢失自救（2026-09-19）：老 GPU / 驱动重置会让 Pixi 静默停帧，
    //   玩家看到的就是「画面定格、拖窗口也不跟随」——即「卡死 + 不实时适配」。
    //   preventDefault 允许浏览器恢复；恢复后补一帧 + 通知外层弹提示。
    this.app.canvas.addEventListener('webglcontextlost', this.handleContextLost);
    this.app.canvas.addEventListener('webglcontextrestored', this.handleContextRestored);

    // ★ 自适应（2026-09-19 重做，统一在 Stage 内部驱动）：
    //   ① 尺寸永远取自**宿主盒子**（不是 window.innerWidth/innerHeight）—— Android
    //      沉浸模式下后者与 `fixed; inset:0` 的盒子不同源（innerHeight 含已隐藏的系统栏），
    //      算出的舞台矩形与真实可视区对不上 ⇒ UI 整体错位（手机「继续游戏」被挤出下边框的主因）。
    //   ② 用 ResizeObserver 观察盒子本身 —— 系统栏隐藏/显示、安全区变化、旋转、全屏、
    //      键盘、窗口拖动，任何布局变化都会回调，且与 CSS 同源、时机最准。
    //      旧实现只听 window resize 事件，上述场景在 Android 上常常**不派发** resize。
    const syncSize = () => {
      if (!this.host) return;
      const box = this.host.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) this.fitTo(box.width, box.height);
    };
    this.sizeObserver = new ResizeObserver(syncSize);
    this.sizeObserver.observe(host);
    // 兜底触发源（尺寸仍取自盒子，只是多打一次）：老 WebView 无 ResizeObserver 时顶上；
    // visualViewport 覆盖 Android 缩放与键盘场景。
    window.addEventListener('resize', syncSize);
    window.addEventListener('orientationchange', syncSize);
    window.visualViewport?.addEventListener('resize', syncSize);
    // ★ 2026-09-19 补「切分辨率 / 切显示缩放」场景：
    //   ① dpr 变化：窗口的 CSS 尺寸**可能完全不变**（Windows 显示缩放 100%→125%），
    //      于是 ResizeObserver / resize 事件都不触发，但渲染缓冲该按新 dpr 重算了
    //      ⇒ 用 matchMedia 监听 resolution 变化。
    //   ② 切回前台 / 从系统设置返回：补一次同步（这些场景 Windows 常不派发 resize）。
    this.watchDpr();
    window.addEventListener('focus', syncSize);
    document.addEventListener('visibilitychange', syncSize);
    this.onFocusSync = syncSize;
    this.onVisibilitySync = syncSize;
    // ★ 定时校验兜底（400ms）：**任何**没派发事件的尺寸变化，最迟 0.4s 自愈。
    //   症状参照：主人切分辨率后画面仍按旧比例 letterbox（上下黑边），
    //   手动拖一下窗口才恢复 —— 说明只是没捕获到，而不是适配算错。
    this.sizeTimer = window.setInterval(syncSize, 400);
    this.syncSize = syncSize;
    syncSize();
  }

  /**
   * 监听 **devicePixelRatio 变化**（切显示缩放 / 拖到不同 DPI 的显示器）。
   * 这类变化的坑：CSS 尺寸不变 ⇒ ResizeObserver 静默，但渲染缓冲必须按新 dpr 重算。
   */
  private watchDpr(): void {
    try {
      const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
      const onChange = () => {
        const dpr = window.devicePixelRatio || 1;
        try {
          // Pixi v8：resolution 可变；变更后必须 resize 才会重建缓冲
          this.app.renderer.resolution = dpr;
        } catch {
          /* 不支持就不改，只做尺寸同步 */
        }
        // ★ 强制重建缓冲：dpr 变了而 CSS 尺寸可能一模一样，此时 fitTo 会认为
        //   「尺寸没变」而跳过 renderer.resize —— 但缓冲的物理像素数必须跟着变。
        //   把基准置为非法值即可让下次 fitTo 走「尺寸已变」分支（120ms 后对齐）。
        this.viewW = -1;
        this.viewH = -1;
        this.syncSize?.();
        // 重新挂一次：新的 dpr 需要新的 media query
        mq.removeEventListener('change', onChange);
        this.watchDpr();
      };
      mq.addEventListener('change', onChange);
      this.dprMq = mq;
      this.dprHandler = onChange;
    } catch {
      /* 老环境没有 matchMedia，跳过（定时校验兜底仍在） */
    }
  }

  unmount(): void {
    this.sizeObserver?.disconnect();
    this.sizeObserver = null;
    if (this.sizeTimer !== null) {
      clearInterval(this.sizeTimer);
      this.sizeTimer = null;
    }
    if (this.dprMq && this.dprHandler) this.dprMq.removeEventListener('change', this.dprHandler);
    this.dprMq = null;
    this.dprHandler = null;
    if (this.onFocusSync) {
      window.removeEventListener('focus', this.onFocusSync);
      this.onFocusSync = null;
    }
    if (this.onVisibilitySync) {
      document.removeEventListener('visibilitychange', this.onVisibilitySync);
      this.onVisibilitySync = null;
    }
    if (this.syncSize) {
      window.removeEventListener('resize', this.syncSize);
      window.removeEventListener('orientationchange', this.syncSize);
      window.visualViewport?.removeEventListener('resize', this.syncSize);
      this.syncSize = null;
    }
    if (this.mounted) {
      this.app.canvas.removeEventListener('webglcontextlost', this.handleContextLost);
      this.app.canvas.removeEventListener('webglcontextrestored', this.handleContextRestored);
    }
    if (this.bufferTimer !== null) {
      clearTimeout(this.bufferTimer);
      this.bufferTimer = null;
    }
    this.host = null;
    if (this.mounted) {
      this.app.destroy(true, { children: true });
      this.mounted = false;
    }
  }

  /**
   * 按**宿主盒子的布局尺寸**重新适配（外部驱动：App.tsx 的 ResizeObserver）。
   * @param w 宿主盒子宽度（px，与 CSS 同源；不是 window.innerWidth）
   * @param h 宿主盒子高度（px，与 CSS 同源；不是 window.innerHeight）
   */
  fitTo(w: number, h: number): void {
    const width = Math.max(1, Math.round(w));
    const height = Math.max(1, Math.round(h));

    // ★ 高频调用合并到每帧最多一次（拖动/系统栏动画时可能一帧多次回调）
    if (this.wPending === width && this.hPending === height && this.fitQueued) return;
    this.wPending = width;
    this.hPending = height;
    if (this.fitQueued) return;
    this.fitQueued = true;
    requestAnimationFrame(() => {
      this.fitQueued = false;
      this.applyFit(this.wPending, this.hPending);
    });
  }

  /** 实际执行适配（由 fitTo 的 rAF 合并后调用） */
  private applyFit(w: number, h: number): void {
    // ★ 渲染缓冲「延后」跟随视口（2026-09-13 修拖动闪烁）
    //   旧做法：拖动中每步 renderer.resize() → WebGL 绘图缓冲整个重建 → 重建后头一两帧
    //   是空白/半成品，WebView2 合成器抓到这种帧 → 背景一闪一闪。
    //   新做法：拖动中**不重建缓冲**——旧画面由 CSS 拉伸跟随窗口（见 global.css
    //   .stage-host canvas 100%），静止 BUFFER_QUIET_MS 后才真正重分配、精确对齐。
    //   缓冲已对齐（尺寸相同）时不排 timer：避免松手对齐后再排一个同尺寸的重分配
    //   （同尺寸 resize 也会重建缓冲 → 松手后又闪一下）。
    if (w !== this.viewW || h !== this.viewH) {
      if (this.bufferTimer !== null) clearTimeout(this.bufferTimer);
      const bw = w;
      const bh = h;
      this.bufferTimer = window.setTimeout(() => {
        this.bufferTimer = null;
        this.app.renderer.resize(bw, bh);
        this.viewW = bw;
        this.viewH = bh;
        this.applyFit(bw, bh); // 缓冲已对齐：按新基准精确重算（此处尺寸相同，不会再排 timer）
      }, BUFFER_QUIET_MS);
    }

    // ★★ DOM UI 的舞台矩形：**永远基于「当前真实尺寸」(w, h) 计算**，不用缓冲基准。
    //   2026-09-19（主人实证「手机 UI 依旧非常大 / 继续游戏在框外」）：旧写法用
    //   缓冲基准 viewW/viewH 再乘 fx/fy 补偿 —— 基准一旦陈旧（首次 fit 发生在
    //   布局尚未稳定时，实测手机上是 891×501 而非真实的 891×410），
    //   `min(viewW/1920, viewH/1080)` 就退化成**按宽度对齐** ⇒ 舞台高 501 > 视口 410：
    //   UI 被放大溢出下边框、画面下半也被裁（人物只露上半身，看着像"立绘"）。
    //   改为按真实尺寸取 min 后：`stageH = 1080 * min(...) ≤ h` 恒成立 ⇒ 永不溢出。
    //   拖动中 UI 立刻贴合新尺寸（跟手），画面由 CSS 拉伸跟随，静止后两者像素级一致。
    const scale = Math.min(w / DESIGN_WIDTH, h / DESIGN_HEIGHT);
    const stageW = DESIGN_WIDTH * scale;
    const stageH = DESIGN_HEIGHT * scale;
    const stageX = (w - stageW) / 2;
    const stageY = (h - stageH) / 2;

    // ★ PixiJS 画面用**缓冲基准**（viewW/viewH）：拖动中缓冲不重建，旧画面由 CSS
    //   拉伸跟随（fx/fy），此处若改用实时尺寸会「缓冲里缩放一次 + CSS 又拉伸一次」
    //   → 双重缩放、画面缩放比鼠标快（2026-09-13 的教训）。静止时基准==真实尺寸，二者等价。
    const bScale = Math.min(this.viewW / DESIGN_WIDTH, this.viewH / DESIGN_HEIGHT);
    this.root.scale.set(bScale);
    this.root.position.set(
      (this.viewW - DESIGN_WIDTH * bScale) / 2,
      (this.viewH - DESIGN_HEIGHT * bScale) / 2,
    );

    // ★ 把舞台矩形广播给 DOM UI（.screen 消费，见 global.css）。
    //   DOM 不受渲染缓冲影响 ⇒ **直接按当前真实尺寸算**，拖动跟手、且永不溢出。
    const css = document.documentElement.style;
    css.setProperty('--stage-x', `${stageX}px`);
    css.setProperty('--stage-y', `${stageY}px`);
    css.setProperty('--stage-w', `${stageW}px`);
    css.setProperty('--stage-h', `${stageH}px`);
    // ★★ 2026-09-20（第二轮）：UI **整体缩放因子**交给 DOM（`.screen` 的 transform）。
    //   演进：纯 px（因子 1.0）手机**偏大**（对话框吃掉半个屏、状态条把「菜单」挤出屏幕）；
    //         设计画布（因子 0.380）手机**偏小**（正文只剩 8px）。现行是折中 ——
    //         按舞台高度相对基准窗口缩放并夹在 [0.6, 1]，详见 UI_REF_STAGE_H 注释。
    //   ⚠️ 这里只写「自动」部分；玩家倍率（设置 → 界面大小）由 App.tsx 写 `--ui-user`，
    //      两者在 CSS 里相乘（见 global.css `.screen` 的 `--ui-zoom`）。
    //   ⚠️ `scale` 仍用于算**舞台矩形**（与 PixiJS 画面 letterbox 同比例）。
    const uiAuto = Math.min(1, Math.max(UI_AUTO_MIN, stageH / UI_REF_STAGE_H));
    css.setProperty('--ui-auto', `${uiAuto}`);
  }
}

/**
 * 任务 1 的验收占位场景：一帧可验证的「北校舍・樱树长椅」。
 * 任务 4 会用真正的占位素材工厂（PlaceholderFactory）替换它 —— 见 技术设计.md §8.3。
 */
export function addBootScene(stage: Stage): void {
  const W = DESIGN_WIDTH;
  const H = DESIGN_HEIGHT;

  const g = new Graphics();
  g.rect(0, 0, W, H).fill('#1a1720');
  g.rect(0, 0, W, H * 0.62).fill('#2a2430'); // 天空 / 树影
  g.rect(0, H * 0.62, W, H * 0.38).fill('#241f28'); // 地面
  g.roundRect(W / 2 - 320, H * 0.72, 640, 46, 8).fill('#4a3f38'); // 椅面
  g.rect(W / 2 - 300, H * 0.72 + 46, 26, 120).fill('#3d342e'); // 左腿
  g.rect(W / 2 + 274, H * 0.72 + 46, 26, 120).fill('#3d342e'); // 右腿
  stage.root.addChild(g);

  const title = new Text({
    text: '笑樱',
    style: { fontFamily: 'sans-serif', fontSize: 96, fill: '#F5F2EE', letterSpacing: 16 },
  });
  title.anchor.set(0.5);
  title.position.set(W / 2, H * 0.34);
  stage.root.addChild(title);

  const sub = new Text({
    text: '任务 1 · 骨架验证：PixiJS 舞台已就绪',
    style: { fontFamily: 'sans-serif', fontSize: 26, fill: '#E8A0B4' },
  });
  sub.anchor.set(0.5);
  sub.position.set(W / 2, H * 0.34 + 92);
  stage.root.addChild(sub);
}
