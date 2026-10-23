/**
 * 标题画面 —— 「开始游戏 / 继续游戏」
 *
 * 「继续游戏」：自动读最近一次存档（含 quick）。无档则置灰。
 * 背景交给 PixiJS Stage（Director 尚未 start，此时是纯舞台）——标题层只是 DOM 覆盖。
 *
 * ★ 2026-09-18 两处改：
 *   ① 按钮文案原先只有 `月/日`（显示成「继续（9/12）」）—— 看起来像页码或测试残留，
 *      主人就问过「9/12 什么意思？」。改成**章号 + 存档时间**，一眼看清是哪个档。
 *   ② 加**台本指纹**校验：旧版本留下的档（`pc` 的语义已经变了）直接标为不可用，
 *      不让玩家读进去落在一个错位的位置上（见 Director.load）。
 */

import { listSaves, loadMeta, type SaveMeta } from '../../director/Director.ts';

/** 存档时间：M/D HH:MM（本地时区） */
function fmtStamp(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 构建号：由 vite.config.ts 的 define 注入（形如 260920-0015）。
 *
 *  ★ 2026-09-20：主人两次反馈「手机上还是旧样子」，而包内容经核验是新的。
 *    加这个标记后，装到手机上**第一眼**就能确认是哪次构建，不再靠猜。
 *    类型见 src/globals.d.ts；vitest 等非 vite 环境取不到时回退 'dev'。 */
const BUILD_STAMP = typeof __BUILD_STAMP__ === 'string' ? __BUILD_STAMP__ : 'dev';

export function TitleScreen(props: {
  /** 当前台本指纹（`director.scriptFingerprint`）—— 用来判断存档还兼不兼容 */
  fingerprint: string;
  onStart: () => void;
  onContinue: (slot: string) => void;
}) {
  // 最近档：按 savedAt 排；顺带把 meta 留下来，按钮要显示章号与时间
  let latest: { slot: string; meta: SaveMeta } | null = null;
  for (const s of listSaves()) {
    const m = loadMeta(s);
    if (m && (!latest || m.savedAt > latest.meta.savedAt)) latest = { slot: s, meta: m };
  }
  const stale = latest !== null && latest.meta.scriptFingerprint !== props.fingerprint;

  const label = !latest
    ? '继续游戏'
    : stale
      ? '继续游戏（旧存档 · 不可用）'
      : `继续游戏（第 ${latest.meta.chapter} 章 · ${fmtStamp(latest.meta.savedAt)}）`;

  return (
    <div className="title-screen">
      <div className="title-crest">❀</div>
      <h1 className="title-main">笑樱</h1>
      <div className="title-sub">— 篠宫樱线 · 视觉小说 —</div>
      <div className="title-menu">
        <button className="title-btn" onClick={props.onStart}>
          开始游戏
        </button>
        <button
          className="title-btn title-btn-dim"
          disabled={!latest || stale}
          title={stale ? '这个存档是旧版本留下的（台本已改动），pc 已对不上' : undefined}
          onClick={() => {
            if (latest) props.onContinue(latest.slot);
          }}
        >
          {label}
        </button>
      </div>
      <div className="title-build">build {BUILD_STAMP}</div>
    </div>
  );
}
