/**
 * 资源加载器 —— 「素材投放即生效」（R-019 承诺）
 *
 * 机制：
 * - 按 `assets.manifest.json` 的 file 字段定位真实文件（`assets/bg/bg_025.png` 等）
 * - **失败记忆**：404 的 key 记入 `failed` 集，之后同 key 直接走占位，不重复发请求
 * - 成功加载的纹理缓存在 `textures`，同 key 二次使用零开销
 *
 * 接入方式（fire-and-forget，不阻塞演出）：
 *   const tex = await assets.bg('旧长椅_午间');   // null = 无真图，用占位
 *
 * Vite 注意：`public/` 下的文件运行时按 URL 直接访问（/assets/...），
 * 不参与打包指纹 —— 这正是「美术扔文件即生效」的前提。
 */

import { Assets, Texture } from 'pixi.js';
import manifestJson from '../generated/assets.manifest.json';

interface ManifestEntry {
  file: string;
  count: number;
  note?: string;
  loop?: boolean;
}

interface Manifest {
  bg: Record<string, ManifestEntry>;
  cg: Record<string, ManifestEntry>;
  char: Record<string, { stem: string; poses: Record<string, number> }>;
}

const manifest = manifestJson as unknown as Manifest;

/**
 * BG 地点归并（台本侧 §17.2）：同一地点的多个 BG 标识共用一张中性底，
 * 时段/天气后续由滤镜处理。键 = BG key 前缀（正则），值 = 代表 BG key（有真实文件）。
 */
const LOCATION_FALLBACK: [RegExp, string][] = [
  [/^旧长椅|^旧校舍/, '旧长椅_午间'],
  [/^教室/, '教室_午休_喧闹'],
  [/^走廊/, '走廊_午后'],
  [/^校舍间岔路/, '校舍间岔路_杂草'],
];

export class AssetLoader {
  private failed = new Set<string>();
  private textures = new Map<string, Texture | null>();

  /** 解析后的 URL（public/ 下 → /assets/...） */
  private url(rel: string): string {
    return `/assets/${rel}`;
  }

  /**
   * 加载 BG 纹理。null = 无真图（调用方画占位）。
   * ★ location 归并：本键无文件时，按地点前缀回退到代表中性底（§17.2）。
   */
  async bg(name: string): Promise<Texture | null> {
    const direct = await this.loadByKey(manifest.bg[name]?.file, `bg:${name}`);
    if (direct) return direct;
    for (const [re, repKey] of LOCATION_FALLBACK) {
      if (re.test(name) && repKey !== name && manifest.bg[repKey]) {
        return this.loadByKey(manifest.bg[repKey]!.file, `bg:${repKey}`);
      }
    }
    return null;
  }

  /** 加载 CG 纹理 */
  async cg(name: string): Promise<Texture | null> {
    return this.loadByKey(manifest.cg[name]?.file, `cg:${name}`);
  }

  /**
   * 加载角色立绘（full 模式）。
   * - 基础：`char/<stem>.png`
   * - 表情差分：`char/<stem>_<NN>.png`（台本侧表情组，编号见 pose-split 的 face 轴）
   */
  async charFull(stem: string): Promise<Texture | null> {
    return this.loadByKey(`char/${stem}.png`, `char:${stem}`);
  }

  /** 表情差分立绘（full 模式整图切换）。faceIdx 越界/无文件 → null（调用方回退基础图） */
  async charFace(stem: string, faceIdx: number): Promise<Texture | null> {
    if (faceIdx <= 0) return this.charFull(stem);
    return this.loadByKey(`char/${stem}_${String(faceIdx).padStart(2, '0')}.png`, `char:${stem}:f${faceIdx}`);
  }

  private async loadByKey(file: string | undefined, cacheKey: string): Promise<Texture | null> {
    if (!file) return null;
    if (this.failed.has(cacheKey)) return null;
    const cached = this.textures.get(cacheKey);
    if (cached !== undefined) return cached;

    try {
      // ★ PixiJS v8：Texture.from(url) 对未加载资源返回**空纹理**（同步），
      //   必须用 Assets.load 异步加载
      const tex = await Assets.load(this.url(file));
      this.textures.set(cacheKey, tex as Texture);
      return tex as Texture;
    } catch {
      this.failed.add(cacheKey); // 404 / 解码失败 → 永久记忆，之后直接占位
      return null;
    }
  }

  /** 当前已就绪的真图数量（诊断用） */
  get loadedCount(): number {
    return [...this.textures.values()].filter((t) => t !== null).length;
  }
}

/** 应用级单例 */
export const assets = new AssetLoader();
