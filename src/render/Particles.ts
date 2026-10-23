/**
 * 樱花粒子系统
 *
 * 对应「樱」母题（技术设计.md §8 P1）：花瓣常驻飘落，落出屏幕后回收再利用。
 * 纯 PixiJS Graphics，无外部贴图 —— 占位阶段即可运行，正式素材可无缝替换纹理。
 *
 * 设计：
 * - 花瓣 = 2~3 色粉系小椭圆，随机大小 / 下落速度 / 摇摆相位 / 旋转速度
 * - 密度可调：默认 36 片；「樱」场景可加强（Director 侧调用 setDensity）
 * - update(dtMs) 由 Layers 每帧驱动；dt 用秒做单位避免数值过大
 */

import { Container, Graphics } from 'pixi.js';
import { DESIGN_WIDTH, DESIGN_HEIGHT } from './Stage.ts';

interface Petal {
  g: Graphics;
  /** 下落速度 px/s */
  fall: number;
  /** 水平摇摆幅度 px */
  swayAmp: number;
  /** 摇摆角速度 rad/s */
  swayFreq: number;
  /** 旋转速度 rad/s */
  spin: number;
  /** 相位偏移 */
  phase: number;
  /** 基准 x（摇摆围绕它） */
  baseX: number;
}

const PETAL_COLORS = [0xf5c6d2, 0xe8a0b4, 0xf0d0da];

export class SakuraParticles {
  readonly container = new Container();
  private petals: Petal[] = [];
  private elapsed = 0;

  constructor(density = 36) {
    this.container.label = 'sakura-particles';
    this.container.eventMode = 'none';
    this.setDensity(density);
  }

  setDensity(n: number): void {
    // 增加
    while (this.petals.length < n) {
      const g = new Graphics();
      const size = 5 + Math.random() * 7;
      const color = PETAL_COLORS[Math.floor(Math.random() * PETAL_COLORS.length)]!;
      // 花瓣形：椭圆 + 轻微透明
      g.ellipse(0, 0, size, size * 0.55).fill({ color, alpha: 0.55 + Math.random() * 0.3 });
      this.container.addChild(g);
      this.petals.push({
        g,
        fall: 22 + Math.random() * 46,
        swayAmp: 18 + Math.random() * 40,
        swayFreq: 0.4 + Math.random() * 0.8,
        spin: (Math.random() - 0.5) * 1.6,
        phase: Math.random() * Math.PI * 2,
        baseX: Math.random() * DESIGN_WIDTH,
      });
    }
    // 减少
    while (this.petals.length > n) {
      const p = this.petals.pop()!;
      p.g.destroy();
    }
  }

  /** 每帧推进；dtMs 毫秒 */
  update(dtMs: number): void {
    const dt = Math.min(dtMs, 100) / 1000; // 防切后台后 dt 爆炸
    this.elapsed += dt;
    for (const p of this.petals) {
      p.g.y += p.fall * dt;
      p.g.rotation += p.spin * dt;
      p.g.x = p.baseX + Math.sin(this.elapsed * p.swayFreq * Math.PI * 2 + p.phase) * p.swayAmp;
      // 落出底部 → 回收至顶部
      if (p.g.y > DESIGN_HEIGHT + 20) {
        p.g.y = -20;
        p.baseX = Math.random() * DESIGN_WIDTH;
      }
    }
  }

  destroy(): void {
    for (const p of this.petals) p.g.destroy();
    this.petals = [];
    this.container.destroy({ children: true });
  }
}
