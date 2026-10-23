/**
 * 运行时诊断总线（2026-09-19）
 *
 * 背景：发给别人的 exe 在别人机器上「卡死」，而我这边复现不了 ——
 * 远程机器上的 JS 异常过去是无声的（F12 打不开 / 用户不会看 console），
 * 出了问题就只能得到「卡死了」三个字，无法定位。
 *
 * 本模块把两类此前**不可见**的失败变成**屏上可见**：
 * 1. rAF 心跳里的异常（过去一抛就断链 → 整个游戏冻结且无任何提示）；
 * 2. window.onerror / unhandledrejection（全局兜底）。
 *
 * 设计约束：
 * - **绝不 throw**：本总线自己出问题也不能影响游戏（内部全 try/catch）。
 * - 订阅者很少（App 挂一个错误 chip），消息不必排队，丢旧保新。
 */

export interface RuntimeErr {
  /** 自增序号（key 用） */
  id: number;
  /** 单行摘要（屏上显示） */
  msg: string;
  /** 发生时刻（performance.now()） */
  t: number;
}

type Listener = (e: RuntimeErr) => void;

let seq = 0;
const listeners = new Set<Listener>();

/** 上报一条运行时异常（屏上会弹出红色提示条，点击可关） */
export function pushRuntimeError(msg: string): void {
  try {
    const e: RuntimeErr = { id: ++seq, msg, t: performance.now() };
    console.error('[runtime]', msg);
    for (const fn of listeners) {
      try {
        fn(e);
      } catch {
        /* 订阅者出错不影响别的订阅者 */
      }
    }
  } catch {
    /* 总线自身绝不出错 */
  }
}

/** 订阅错误事件；返回退订函数 */
export function subscribeRuntimeErrors(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * 安装全局兜底（window.onerror / unhandledrejection）。
 * 在 App 挂载时调用一次；重复调用只装一次。
 */
let installed = false;
export function installGlobalErrorHooks(): void {
  if (installed) return;
  installed = true;
  window.addEventListener('error', (e) => {
    // 资源加载失败的 error（img/script）没有 error 对象细节，也照报
    const msg = e.message || (e.target ? '资源加载失败' : '未知错误');
    pushRuntimeError(`脚本异常：${msg}`);
  });
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason;
    const msg = r instanceof Error ? r.message : typeof r === 'string' ? r : JSON.stringify(r)?.slice(0, 120) ?? '未知 Promise 拒绝';
    pushRuntimeError(`未处理的 Promise 拒绝：${msg}`);
  });
}
