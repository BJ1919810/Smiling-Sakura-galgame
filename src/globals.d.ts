/**
 * 全局编译期常量声明（由 vite.config.ts 的 `define` 注入）。
 *
 * ★ 2026-09-20：构建号 `__BUILD_STAMP__`（形如 `260920-0015`），
 *   标题页右下角显示，用于**一眼确认装的是哪次构建**。
 */

/** 构建时间戳：`YYMMDD-HHMM`（本地时区） */
declare const __BUILD_STAMP__: string;
