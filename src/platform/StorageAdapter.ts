/**
 * 存储抽象层（技术设计.md §10 / 任务 6）
 *
 * 目的：把「存档存哪里」从业务逻辑里隔离出来。
 * - Web：localStorage（容量 5MB，足够存档 JSON；IndexedDB 待多媒体素材量大后升级）
 * - Tauri（任务 7）：fs 写 AppData/saves/*.json —— 实现同一接口即可无缝切换
 *
 * 接口刻意做小：get / set / remove / keys —— 存档系统只需要这四个动作。
 */

export interface StorageAdapter {
  get(key: string): string | null;
  set(key: string, value: string): void;
  remove(key: string): void;
  /** 所有属于本应用的 key（不含前缀） */
  keys(): string[];
}

const PREFIX = 'xiaoying.';

/** Web 实现：localStorage */
export class WebStorage implements StorageAdapter {
  get(key: string): string | null {
    try {
      return localStorage.getItem(PREFIX + key);
    } catch {
      return null; // 隐私模式 / 配额满 —— 降级为不持久化
    }
  }

  set(key: string, value: string): void {
    try {
      localStorage.setItem(PREFIX + key, value);
    } catch {
      // 同上：静默失败，游戏不因存储问题崩溃
    }
  }

  remove(key: string): void {
    try {
      localStorage.removeItem(PREFIX + key);
    } catch {
      /* 同上 */
    }
  }

  keys(): string[] {
    const out: string[] = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k?.startsWith(PREFIX)) out.push(k.slice(PREFIX.length));
      }
    } catch {
      /* 同上 */
    }
    return out;
  }
}

/** 测试用：内存实现（与 Web 语义一致） */
export class MemoryStorage implements StorageAdapter {
  private map = new Map<string, string>();
  get(key: string): string | null {
    return this.map.get(key) ?? null;
  }
  set(key: string, value: string): void {
    this.map.set(key, value);
  }
  remove(key: string): void {
    this.map.delete(key);
  }
  keys(): string[] {
    return [...this.map.keys()];
  }
}

/** 应用级单例：Tauri 上线时替换实现即可 */
export const storage: StorageAdapter = typeof localStorage !== 'undefined' ? new WebStorage() : new MemoryStorage();
