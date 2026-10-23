/**
 * 舞台图层与立绘 actor
 *
 * 图层顺序（自下而上）：bg → char → cg → fx
 * - bg：背景（切换时 cross-fade）
 * - char：立绘（每个角色一个容器，支持表情 cross-fade 与呼吸）
 * - cg：全屏 CG（覆盖立绘）
 * - fx：粒子 / 滤镜层（任务 5 用）
 *
 * 边界：本模块只做「画面呈现」，不解释剧情。
 * 谁显示、显示什么由 director 层通过这里的方法告知。
 */

import { Container, Graphics, Sprite, Texture } from 'pixi.js';
import { DESIGN_HEIGHT, DESIGN_WIDTH } from './Stage.ts';
import { makeBgPlaceholder, makeCgPlaceholder, makeSpritePlaceholder, slotOnRight, slotToX } from './PlaceholderFactory.ts';
import { SakuraParticles } from './Particles.ts';
import { assets } from './AssetLoader.ts';
import { buildTimeFilter } from './timeTint.ts';

/** 转场时长（ms）—— 与 技术设计.md §9 一致 */
const FADE_MS = 420;

/**
 * 立绘地面线：内容底（脚）所在的设计坐标 y。
 * ★ 上半身取景（2026-09-12 主人定）：立绘 1:1 渲染、脚底沉到画面外 ——
 *   画面里只露头到腰腹，脸远离对话框。全身塞进画面的旧做法作废。
 *   数值取「最高角色（阳太 内容高 2449px）的头顶落在 y≈100」：1470 + 1080 - 2449 ≈ 101。
 */
const SPRITE_GROUND = DESIGN_HEIGHT + 1470;

/**
 * 取景锚点：**头顶落点**（设计坐标 y，越小越靠上）。
 *
 * ★ 性别分组（主人 2026-09-12 晚定案）：
 * - **女生组基准 = 樱的呈现高度，不调低**；九条院 / 亚里沙对齐樱 → 全组头顶一致。
 * - **男生组整体抬高**，使「女生头顶 = 男生鼻尖」（鼻尖 ≈ 男生头顶下方 200px，7 头身）。
 * - **樱归女生组**（此前已归，现以显式集合固化）。
 * 新角色必须加入下面两个集合之一；漏归会在控制台报警并按女生组兜底（不再默默归男）。
 */
const NOSE_OFFSET = 200;
const HEAD_Y_FEMALE = 410; // = 樱的当前呈现头顶（主人认可的基准，勿随手改）
const HEAD_Y_MALE = HEAD_Y_FEMALE - NOSE_OFFSET;

const FEMALE_CHARS = new Set(['樱', '亚里沙', '九条院']);
const MALE_CHARS = new Set(['龙司', '阳太', '泽渡']);

const CHAR_HEAD_Y: Record<string, number> = {
  龙司: HEAD_Y_MALE,
  阳太: HEAD_Y_MALE,
  泽渡: HEAD_Y_MALE,
  樱: HEAD_Y_FEMALE,
  亚里沙: HEAD_Y_FEMALE,
  九条院: HEAD_Y_FEMALE,
};

/** 台本角色名 → 素材 stem */
const STEMS: Record<string, string> = {
  龙司: 'ryuji',
  樱: 'sakura',
  阳太: 'yota',
  亚里沙: 'arisa',
  九条院: 'kujo',
  泽渡: 'sawtari',
};

/** 简单插值器；避免引入 GSAP 依赖做基础淡入淡出 */
function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

/**
 * 立绘元数据（`tools/deploy-sprites.py` 生成 → `assets/char/meta.json`）。
 * ★ baseTop = **基础图**在公共裁切框内的 alpha 顶偏移：
 *   某张差分若画得比基础图高，会把并集框顶撑高 → 基础图上方留出透明带。
 *   锚定必须按「基础图可见发顶」算（用 contentH − baseTop 反推缩放），
 *   否则基础图发顶会下沉 baseTop×scale（九条院实测 121px → 沉了 ~100px，主人一眼看穿）。
 *   公共框本身不动 → 表情切换仍然零漂移；差分自身发顶的浮动属素材固有差异。
 */
let spriteMetaP: Promise<Record<string, { baseTop?: number }>> | null = null;
function ensureSpriteMeta(): Promise<Record<string, { baseTop?: number }>> {
  spriteMetaP ??= fetch('/assets/char/meta.json')
    .then((r) => (r.ok ? r.json() : {}))
    .catch(() => ({}));
  return spriteMetaP;
}

function easeInOutSine(t: number): number {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

interface Anim {
  obj: Container;
  from: number;
  to: number;
  t0: number;
  dur: number;
  ease: (t: number) => number;
  /** 每帧回调（用于推镜 / 震动等非 alpha 动画） */
  onTick?: (p: number) => void;
  onDone?: () => void;
}

/** 演出钩子：Director 通过这些方法驱动舞台（技术设计.md §8） */
export interface StageFx {
  /** 推镜（特写）：整台向目标轻微推近再回位 */
  pushIn(strength?: number): void;
  /** 镜头轻震 */
  shake(intensity?: number, durMs?: number): void;
  /** 白闪（CG 弹出） */
  flash(durMs?: number): void;
  /** 黑场转场：淡入黑 → 回调（切景）→ 淡出 */
  dipToBlack(mid: () => void, durMs?: number): void;
  /** 樱花密度 */
  sakuraDensity(n: number): void;
}

export class Layers implements StageFx {
  readonly bg = new Container();
  readonly char = new Container();
  readonly cg = new Container();
  readonly fx = new Container();

  /** 樱花粒子（挂在 char 与 cg 之间 —— 花瓣飘在人物前、CG 后） */
  readonly sakura: SakuraParticles;

  /** 全屏遮罩（转场黑场 / 白闪） */
  private wipe: Graphics;

  /** 活跃动画（每帧推进） */
  private anims: Anim[] = [];
  /** 角色名 → 立绘容器 */
  private sprites = new Map<string, Container>();
  /**
   * 雀跃进行中的角色（抛物线跳一下，见 hop()）。
   * ★ 刻意不走 this.anims：tween() 对同对象会「先移除再触发 onDone」，
   *   若复用会把进行中的 cross-fade 拦腰掐断（alpha 卡在半路）。
   *   位置动画与透明度动画分两条通道，互不干扰。
   */
  private hops = new Map<string, { t0: number; dur: number; height: number }>();
  /** 表情切换请求序号（char → seq）：连续切换时只让最后一次生效 */
  private faceSeq = new Map<string, number>();
  /** 角色登场时刻（clearSprites 的黑场保护用，见 clearSprites(beforeMs)） */
  private bornAt = new Map<string, number>();
  /** 当前 CG */
  private currentCg: Container | null = null;

  /** 镜头容器：震动 / 推镜作用在它上面（不污染各层自身变换） */
  private camera = new Container();

  /** 实例编号（诊断双实例问题用） */
  static instanceSeq = 0;
  readonly id = ++Layers.instanceSeq;

  constructor(root: Container) {
    this.camera.label = 'camera';
    this.bg.label = 'layer-bg';
    this.char.label = 'layer-char';
    this.cg.label = 'layer-cg';
    this.fx.label = 'layer-fx';
    // camera 承载全部内容层；遮罩与粒子在最上
    this.camera.addChild(this.bg, this.char, this.cg, this.fx);

    this.sakura = new SakuraParticles(36);
    this.sakura.container.zIndex = 5;

    this.wipe = new Graphics();
    this.wipe.label = 'wipe';
    this.wipe.eventMode = 'none';
    this.wipe.alpha = 0;

    root.addChild(this.camera, this.sakura.container, this.wipe);
  }

  /** 由 Stage 的 ticker 每帧调用 */
  update(nowMs: number, dtMs: number): void {
    // 推进动画。
    // ★ 先完成 filter（移除结束的动画），再统一触发 onDone ——
    //   onDone 可能对同一容器发起新 tween（如 dipToBlack 的后半段），
    //   若在 filter 回调内同步调用，会再次 filter 同一数组 → 无限递归（栈溢出）。
    if (this.anims.length) {
      const finished: Anim[] = [];
      this.anims = this.anims.filter((a) => {
        const p = Math.min(1, (nowMs - a.t0) / a.dur);
        const e = a.ease(p);
        a.obj.alpha = a.from + (a.to - a.from) * e;
        a.onTick?.(e);
        if (p >= 1) {
          finished.push(a);
          return false;
        }
        return true;
      });
      for (const a of finished) a.onDone?.();
    }

    // 立绘平时**静止站立**（2026-09-12 主人定：取消上下浮动，安安静静呆着）。
    // 唯一的纵向动作是 hop() 的雀跃 —— 见下方雀跃段。

    // 雀跃：y = H・4p(1-p) 的抛物线。
    //   p=0 与 p=1 时斜率最大（向上最快 / 向下最快），p=0.5 顶点速度为 0 ——
    //   正是主人要的「向上快 → 慢 → 顶点 → 向下慢 → 快 → 归位停住」。
    for (const [name, h] of this.hops) {
      const sp = this.sprites.get(name);
      if (!sp || sp.destroyed) {
        this.hops.delete(name);
        continue;
      }
      const p = Math.min(1, (nowMs - h.t0) / h.dur);
      sp.y = SPRITE_GROUND - h.height * 4 * p * (1 - p);
      if (p >= 1) {
        sp.y = SPRITE_GROUND; // 精确归位，不靠浮点尾巴
        this.hops.delete(name);
      }
    }

    this.sakura.update(dtMs);
  }

  private fade(obj: Container, to: number, dur = FADE_MS, onDone?: () => void): void {
    this.tween(obj, obj.alpha, to, dur, easeOutCubic, undefined, onDone);
  }

  /** 通用补间：alpha 变化由 from/to 驱动，额外每帧回调交给 onTick */
  private tween(
    obj: Container,
    from: number,
    to: number,
    dur: number,
    ease: (t: number) => number,
    onTick?: (p: number) => void,
    onDone?: () => void,
  ): void {
    // 同对象已有动画：先移除，再触发其 onDone（★ 移除后调用，防 filter 重入递归）
    const replaced: Anim[] = [];
    this.anims = this.anims.filter((a) => {
      if (a.obj === obj) {
        replaced.push(a);
        return false;
      }
      return true;
    });
    for (const a of replaced) a.onDone?.();
    this.anims.push({ obj, from, to, t0: performance.now(), dur, ease, onTick, onDone });
  }

  // ============================================================
  // 背景
  // ============================================================

  /** 背景切换请求序号：快速连续切背景时只让最后一次生效 */
  private bgSeq = 0;
  /** CG 弹出请求序号（hideCg 也会 +1，使在途的 showCg 失效） */
  private cgSeq = 0;

  /**
   * 切换背景 —— **真图对真图淡入淡出**（主人 2026-09-12 定）。
   * 旧实现「占位剪影先淡入 → 真图到位瞬间替换」，中间会露出一张剪影，观感像闪一下。
   * 现在：先等真图就绪，再让新旧两张真图互为淡入淡出；真图缺失（404）才退占位。
   */
  setBg(name: string): void {
    const seq = ++this.bgSeq;

    void assets
      .bg(name)
      .then((tex) => {
        if (this.bg.destroyed || seq !== this.bgSeq) return; // 已被更新的切换覆盖
        const next = tex ? this.makeRealBg(tex) : makeBgPlaceholder(name);
        next.label = `bg:${name}`;
        next.alpha = 0;
        // ★ 时段/天气滤镜：同一张中性底，按 BG 键名的时段后缀调色（见 timeTint.ts）。
        //   ⛔ CG 不挂 —— CG 是特写，光线在出图时已定死；再叠滤镜等于改美术意图。
        //   基准时段（午后/午间/正午）返回 null ⇒ 不挂，省一次全屏 pass。
        const tint = buildTimeFilter(name);
        if (tint) next.filters = [tint];
        // ★ 2026-09-18 修层泄漏：旧层不再用「调用时刻的 children[0]」捕获——
        //   竞态窗口 = 上一张的 420ms 淡出还没走完时下一个 setBg 到来：
        //   children[0] 仍是被淡出中的最老层，中间那张已淡入完成的层被跳过、
        //   以 alpha=1 永久滞留（diag-inject 实测连发 30 次 → 滞留 15 层；
        //   实机剧情推进后 bg.children 从 1 涨到 17，全屏 overdraw ×17）。
        //   现改为：真图落地时收走 next 之外的**全部**既有层；其中已在淡出
        //   路径上的（anims 里已有 to===0 的 tween）跳过，避免 replace 链把
        //   它们的销毁回调顶掉。过期请求（seq 不匹配）仍被乱序防护丢弃。
        const olds = this.bg.children.slice() as Container[];
        this.bg.addChild(next);
        this.fade(next, 1, FADE_MS);
        for (const o of olds) {
          if (o.destroyed) continue;
          if (this.anims.some((a) => a.obj === o && a.to === 0)) continue; // 已在淡出
          this.fade(o, 0, FADE_MS, () => {
            if (!o.destroyed) o.destroy({ children: true });
          });
        }
      })
      .catch((e) => {
        console.error('[setBg] 背景切换失败:', e instanceof Error ? `${e.message}\n${e.stack?.slice(0, 400)}` : e);
      });
  }

  /** 真图 BG：铺满设计分辨率（cover，保持比例居中裁切） */
  private makeRealBg(tex: Texture): Container {
    const c = new Container();
    c.label = `bg-real`;
    // ── 待命实验（2026-09-18，**默认关闭**，勿与 antialias A/B 同轮启用）──────────────
    // 目的：判定白线是否「CG/BG quad 未覆盖到的漏缝」（缝里露出的是下层内容）。
    // 做法：在 Sprite **背后**垫一张纯红 Graphics ——
    //   若线变红 ⇒ 漏缝实锤（是覆盖缺失，不是画上去的像素）；
    //   若仍为白 ⇒ 不是覆盖缝，而是 GPU/Pixi 绘制该 Sprite 时额外产生的颜色伪影。
    // 启用：仅在 antialias=false 那一刀**没治好**时单独出一版包（取消下面 3 行注释即可，
    //   Graphics 已在文件头 import，无需改导入）。
    // const testBack = new Graphics();
    // testBack.rect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT).fill(0xff0000);
    // c.addChild(testBack);
    const sp = new Sprite(tex);
    const scale = Math.max(DESIGN_WIDTH / tex.width, DESIGN_HEIGHT / tex.height);
    sp.scale.set(scale);
    sp.x = (DESIGN_WIDTH - tex.width * scale) / 2;
    sp.y = (DESIGN_HEIGHT - tex.height * scale) / 2;
    c.addChild(sp);
    return c;
  }

  // ============================================================
  // 立绘
  // ============================================================

  /** 基础图 alpha 顶偏移（meta.json；无记录 → 0） */
  private async baseTopOf(char: string): Promise<number> {
    const stem = STEMS[char];
    if (!stem) return 0;
    const meta = await ensureSpriteMeta();
    return meta[stem]?.baseTop ?? 0;
  }

  /** 头顶落点反推缩放：按「基础图可见发顶」（内容高 − 顶偏移）对齐 HEAD_Y */
  private scaleFor(char: string, contentH: number, baseTop: number): number {
    if (!FEMALE_CHARS.has(char) && !MALE_CHARS.has(char)) {
      console.warn(`[Layers] 角色「${char}」未归类性别组（FEMALE_CHARS/MALE_CHARS），暂按女生组处理`);
    }
    const visibleH = contentH - baseTop;
    return (SPRITE_GROUND - (CHAR_HEAD_Y[char] ?? HEAD_Y_FEMALE)) / (visibleH > 0 ? visibleH : contentH);
  }

  /**
   * 立绘统一落位：x＝站位比例，y＝地面线（沉到画面外），scale＝头顶落点反推＋朝向。
   * ★ 朝向（主人 2026-09-12 定）：中轴及以左面朝右（原画方向），中轴以右**轴对称**面朝左。
   *   翻转用 scale.x 取负 —— 锚点是底部中心，翻转后立绘仍居中在站位上、不位移。
   *   ⚠️ 侧马尾/侧挂物件等非对称特征会跟着镜像（galgame 惯例，接受）。
   * `contentH`（素材内容高）给定 → 缩放按头顶落点反推；未给定 → 只定朝向（等真图到位后再补）。
   * `baseTop`（基础图顶偏移）→ 传给 scaleFor 做可见发顶补偿，见 ensureSpriteMeta 注释。
   */
  private standAt(sp: Container, char: string, slot: string, contentH?: number, baseTop = 0): void {
    const flip = slotOnRight(slot) ? -1 : 1;
    sp.x = slotToX(slot);
    sp.y = SPRITE_GROUND;
    if (contentH) {
      const s = this.scaleFor(char, contentH, baseTop);
      sp.scale.set(flip * s, s);
    } else {
      sp.scale.set(flip, 1);
    }
  }

  /** 加载该角色该括注对应的立绘纹理（face 差分缺失自动回退基础图） */
  private async loadFace(char: string, pose: string): Promise<Texture | null> {
    const stem = STEMS[char];
    if (!stem) return null;
    const faceIdx = Layers.poseToFace(pose);
    const tex = await assets.charFace(stem, faceIdx);
    if (faceIdx > 0 && !tex) return assets.charFull(stem); // 差分缺失 → 回退基础
    return tex;
  }

  /**
   * 显示 / 更新立绘（full 模式：一张整图一个表情）。
   * - 不存在 → 新建并淡入（占位剪影先上场，真图到位后在容器内淡入替换，不硬切）。
   * - 已存在 → **表情 cross-fade**：先等新表情纹理就绪，再让**新旧两张真图**互为淡入淡出。
   *   （2026-09-12 主人要求「表情的切换可以是淡入淡出」——旧实现先淡入占位、真图到位瞬间替换，
   *   看起来就是硬切。）
   */
  showSprite(char: string, pose: string, slot: string): void {
    const existing = this.sprites.get(char);
    if (existing) {
      void this.crossFadeFace(char, pose, slot);
      return;
    }

    const holder = this.buildActor(char, pose, slot);
    this.standAt(holder, char, slot);
    holder.alpha = 0;
    this.char.addChild(holder);
    this.fade(holder, 1, FADE_MS);
    this.sprites.set(char, holder);
    this.bornAt.set(char, performance.now());
  }

  /** 表情切换：纹理就绪后才建新容器 → 真图对真图的淡入淡出 */
  private async crossFadeFace(char: string, pose: string, slot: string): Promise<void> {
    // 连续切换的乱序防护：只让最后一次请求生效
    const seq = (this.faceSeq.get(char) ?? 0) + 1;
    this.faceSeq.set(char, seq);

    const tex = await this.loadFace(char, pose);
    if (!tex) return; // 没图（多半是未部署立绘的角色）→ 保持现状
    if (this.faceSeq.get(char) !== seq) return; // 已被更新的表情请求覆盖
    const old = this.sprites.get(char);
    if (!old || old.destroyed) return;

    const next = new Container();
    const sp = new Sprite(tex);
    sp.anchor.set(0.5, 1); // 底部中心锚点：脚底踩地面线
    next.addChild(sp);
    // 纹理就绪 → 头顶落点反推缩放（锚定基础图可见发顶，见 ensureSpriteMeta）
    this.standAt(next, char, slot, tex.height, await this.baseTopOf(char));
    next.alpha = 0;
    this.char.addChild(next);
    this.sprites.set(char, next);
    this.bornAt.set(char, performance.now());

    this.fade(next, 1, FADE_MS);
    this.fade(old, 0, FADE_MS, () => old.destroy({ children: true }));
  }

  /**
   * 雀跃：立绘快速向上跳一下再落回原位（正向高情绪台词的演出，见 emotion.ts）。
   *
   * 轨迹是**抛物线** `y = H・4p(1-p)`（p∈[0,1]）：
   *   起跳速度最大 → 顶点速度归零 → 落地速度最大 → 精确归位停住。
   * 与打字机/转场无耦合；同角色重复调用会**重新起跳**（连续两句激动的话就连跳）。
   *
   * @param char   角色名（须已在台上）
   * @param height 跳跃高度（设计坐标系 px；默认 120 ≈ 立绘内容高的 5%，立绘 1:1 后同比例放大）
   * @param durMs  全程时长（默认 500ms）
   */
  hop(char: string, height = 120, durMs = 500): void {
    if (!this.sprites.has(char)) return;
    this.hops.set(char, { t0: performance.now(), dur: durMs, height });
  }

  /**
   * 让**台上全部**立绘一起雀跃 —— `※ 雀跃` 未指定角色时用（2026-09-15 新增）。
   * 要给单个角色跳就写 `※ 雀跃：樱`。
   * @returns 实际被触发的角色名（诊断/自检用）
   */
  hopAll(height = 120, durMs = 500): string[] {
    const chars = [...this.sprites.keys()];
    for (const c of chars) this.hop(c, height, durMs);
    return chars;
  }

  /** 该角色是否正在雀跃（诊断用） */
  isHopping(char: string): boolean {
    return this.hops.has(char);
  }

  /**
   * pose 文本 → 表情差分编号（与 pose-split 的 face 规则同源，运行时版）。
   * 0 = 基础（平静）；1-5 对应 char/<stem>_01..05.png（台本侧表情组）。
   */
  private static poseToFace(pose: string): number {
    if (/眼眶热|含泪/.test(pose)) return 5;
    if (/眼睛微亮|眼神|极细微/.test(pose)) return 2;
    if (/微弯|弯/.test(pose)) return 1;
    if (/歪头/.test(pose)) return 4;
    if (/抬眼|抬头/.test(pose)) return 3;
    return 0;
  }

  /**
   * 首次登场：占位剪影先上场，真图到位后在容器内**淡入**替换（避免真图瞬间硬切）。
   * （表情切换不走这里——那条路在 crossFadeFace，等纹理就绪才建容器。）
   */
  private buildActor(char: string, pose: string, slot: string): Container {
    const holder = new Container();
    const ph = makeSpritePlaceholder(char, pose, slot);
    holder.addChild(ph);

    void this.loadFace(char, pose)
      .then(async (tex) => {
        if (!tex || holder.destroyed || holder.parent !== this.char) return;
        const sp = new Sprite(tex);
        sp.anchor.set(0.5, 1); // 底部中心锚点
        sp.scale.set(this.scaleFor(char, tex.height, await this.baseTopOf(char))); // 头顶落点反推缩放
        sp.alpha = 0;
        holder.addChild(sp);
        this.tween(sp, 0, 1, 300, easeOutCubic, undefined, () => {
          if (!holder.destroyed && ph.parent === holder) {
            holder.removeChild(ph);
            ph.destroy({ children: true });
          }
        });
      })
      .catch(() => {}); // 加载失败保持占位

    return holder;
  }

  hideSprite(char: string): void {
    const sp = this.sprites.get(char);
    if (!sp) return;
    this.sprites.delete(char);
    this.hops.delete(char);
    this.bornAt.delete(char);
    this.fade(sp, 0, FADE_MS, () => sp.destroy({ children: true }));
  }

  /**
   * 清空立绘（跨场景时调用，与 GameState.closeAllWindows 呼应）。
   * ★ `beforeMs`（可选）＝黑场保护：**只清在该时刻之前登场的立绘**。
   *   场景切换是「先 dipToBlack → 顶点才清场」，黑场期间新场景的立绘就可能登场
   *   （快速点进时立绘指示可落在切换后 700ms 内）——无差别清场会把他们静默吞掉（实测踩中）。
   */
  clearSprites(beforeMs?: number): void {
    for (const [char, sp] of this.sprites) {
      const born = this.bornAt.get(char) ?? 0;
      if (beforeMs !== undefined && born > beforeMs) continue; // 黑场开始后才登场 → 留下
      this.fade(sp, 0, FADE_MS, () => sp.destroy({ children: true }));
      this.sprites.delete(char);
      this.hops.delete(char);
      this.bornAt.delete(char);
    }
  }

  // ============================================================
  // CG
  // ============================================================

  /**
   * 弹出 CG —— **真图对真图淡入淡出**（主人 2026-09-12 定，与 setBg 同思路）。
   * 旧实现占位剪影先上、真图瞬间替换 → 观感硬切。
   */
  showCg(name: string): void {
    const seq = ++this.cgSeq;
    const old = this.currentCg;

    void assets
      .cg(name)
      .then((tex) => {
        if (this.cg.destroyed || seq !== this.cgSeq) return; // 已被隐藏 / 更新的 CG 覆盖
        const next = tex ? this.makeRealBg(tex) : makeCgPlaceholder(name);
        next.label = `cg:${name}`;
        next.alpha = 0;
        this.cg.addChild(next);
        this.fade(next, 1, 600);
        if (old) this.fade(old, 0, 400, () => old.destroy({ children: true }));
        this.currentCg = next;
      })
      .catch((e) => {
        console.error('[showCg] CG 加载失败:', e instanceof Error ? `${e.message}\n${e.stack?.slice(0, 400)}` : e);
      });
  }

  hideCg(immediate = false): void {
    this.cgSeq += 1; // 使在途的 showCg 失效（否则隐藏后 CG 又淡回来）
    if (!this.currentCg) return;
    const old = this.currentCg;
    this.currentCg = null;
    // ★ 2026-09-18：场景切换走 immediate —— 此刻黑场正浓（dipToBlack 顶点），直接清即可。
    //   旧做法无条件 400ms 淡出：黑场后段只有 350ms，两者重叠 ⇒ 黑幕刚开始变淡、
    //   CG 也在淡出，玩家看到的就是「CG 慢慢消失 → 露出 BG → 新 CG 又出现」（主人反馈「很突兀」）。
    if (immediate) {
      old.destroy({ children: true });
      return;
    }
    this.fade(old, 0, 400, () => old.destroy({ children: true }));
  }

  // ============================================================
  // 演出钩子（StageFx）
  // ============================================================

  /**
   * 推镜（特写）：镜头向中心推近再回位。strength 为最大缩放增量（默认 0.06）。
   *
   * ⛔ **[已停用] 2026-09-18（主人令）** —— `※ 特写` / `※ 镜头` 不再调用它（见 Director.applyFx）。
   * 停用理由：① 主人观感「看得恶心」；② 它让相机做**非整数缩放**，是同内容 A/B 里
   * 斜向重采样伪影最多的配置（相机 scale 1 → 3 条，scale 1.06 → 26 条）。
   * 代码保留：日后要给别的对象做轻推（例如某个 UI 面板）可以直接捡回来。
   */
  pushIn(strength = 0.06): void {
    const cx = DESIGN_WIDTH / 2;
    const cy = DESIGN_HEIGHT / 2;
    this.tween(
      this.camera,
      0,
      1,
      900,
      easeInOutSine,
      (p) => {
        // 0 → 1 → 0 的钟形推近
        const bell = Math.sin(p * Math.PI);
        const s = 1 + strength * bell;
        this.camera.scale.set(s);
        this.camera.pivot.set(cx * (1 - 1 / s), cy * (1 - 1 / s));
      },
      () => {
        this.camera.scale.set(1);
        this.camera.pivot.set(0, 0);
      },
    );
  }

  /** 镜头轻震：快速随机偏移，衰减至零 */
  shake(intensity = 10, durMs = 420): void {
    this.tween(
      this.camera,
      1,
      0,
      durMs,
      (t) => t,
      (p) => {
        const damp = 1 - p;
        this.camera.x = (Math.random() - 0.5) * 2 * intensity * damp;
        this.camera.y = (Math.random() - 0.5) * 2 * intensity * damp;
      },
      () => {
        this.camera.x = 0;
        this.camera.y = 0;
      },
    );
  }

  /** 白闪（CG 弹出）：全屏白幕快速闪现 */
  flash(durMs = 260): void {
    this.wipe.clear();
    this.wipe.rect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT).fill(0xffffff);
    this.tween(
      this.wipe,
      0.9,
      0,
      durMs,
      easeOutCubic,
      undefined,
      () => {
        this.wipe.alpha = 0;
      },
    );
  }

  /** 黑场转场：淡入黑 →（黑场中切景，mid 收到黑场开始时刻）→ 淡出 */
  dipToBlack(mid: (dipStartedAt: number) => void, durMs = 700): void {
    this.wipe.clear();
    this.wipe.rect(0, 0, DESIGN_WIDTH, DESIGN_HEIGHT).fill(0x000000);
    this.wipe.alpha = 0;
    const startedAt = performance.now();
    // 前半：淡入黑
    this.tween(this.wipe, 0, 1, durMs / 2, easeInOutSine, undefined, () => {
      mid(startedAt);
      // 后半：淡出
      this.tween(this.wipe, 1, 0, durMs / 2, easeInOutSine);
    });
  }

  /** 樱花密度（樱树场景可加强） */
  sakuraDensity(n: number): void {
    this.sakura.setDensity(n);
  }

  /**
   * 压暗 / 恢复立绘（§9.4 选项演出规则：选项出现时压暗立绘，让玩家聚焦选项）。
   * 只调 char 层；背景不动（保持场景感）。
   */
  dimChar(dim: boolean): void {
    this.tween(this.char, this.char.alpha, dim ? 0.45 : 1, 240, easeOutCubic);
  }

  destroy(): void {
    this.anims = [];
    this.sprites.clear();
    this.hops.clear();
    this.faceSeq.clear();
    this.currentCg = null;
    this.sakura.destroy();
    this.bg.destroy({ children: true });
    this.char.destroy({ children: true });
    this.cg.destroy({ children: true });
    this.fx.destroy({ children: true });
    this.wipe.destroy();
    this.camera.destroy();
  }
}
