/**
 * 游戏内「菜单」面板（2026-09-18 主人定：状态条只留一个「菜单」入口）
 *
 * 条目：继续 / 存档 / 读档 / 设置 / 跳章 / 主页
 *
 * - **跳章**＝「章首 · 重置进度」：跳到该章第一个场景，vars / 背景 / CG 按新章重建
 *   （等同从头玩到那一章）—— 最稳，不会出现挂错背景/错 CG 的中间态。
 * - **主页**＝把进度复位到开头 + 停音乐。未存档的进度会丢 ⇒ 面板内二次确认。
 *
 * ★ 样式**全部复用**已有面板类（`.panel` / `.slot-*` / `.panel-close`），
 *   不新增 CSS、不动主题与配色。
 */

import { useState } from 'react';

export function MenuPanel(props: {
  /** 章节表（来自 script.json 的 `chapters`） */
  chapters: { index: number; title: string }[];
  onClose: () => void;
  onSave: () => void;
  onLoad: () => void;
  onSettings: () => void;
  onBacklog: () => void;
  onJump: (index: number) => void;
  onTitle: () => void;
}) {
  const [mode, setMode] = useState<'menu' | 'chapters'>('menu');
  const [confirmTitle, setConfirmTitle] = useState(false);

  /** 一行菜单项：名称（`.slot-name`）＋ 说明（`.slot-line2`） */
  const row = (name: string, desc: string, onClick: () => void) => (
    <button className="slot-row" onClick={onClick}>
      <span className="slot-name">{name}</span>
      <span className="slot-info">
        <span className="slot-line2">{desc}</span>
      </span>
    </button>
  );

  return (
    <div className="panel-backdrop" onClick={props.onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        {mode === 'menu' ? (
          <>
            <div className="panel-title">菜单</div>
            <div className="slot-list">
              {row('继续', '关掉菜单，接着往下读', props.onClose)}
              {row('历史记录', '回看读过的台词 · 可重播语音', props.onBacklog)}
              {row('存档', '写进四个槽位之一', props.onSave)}
              {row('读档', '从已有的存档继续', props.onLoad)}
              {row('设置', '音量 / 文字大小 / 文字速度 / 自动播放', props.onSettings)}
              {row('跳章', '跳到某一章的开头（进度重置）', () => setMode('chapters'))}
              {confirmTitle ? (
                <>
                  <div className="slot-row slot-disabled">
                    <span className="slot-name">确认</span>
                    <span className="slot-info">
                      <span className="slot-line2">未存档的进度会丢失，确定回主页？</span>
                    </span>
                  </div>
                  {row('确定', '回主页，进度重置到开头', props.onTitle)}
                  {row('取消', '留在这一页', () => setConfirmTitle(false))}
                </>
              ) : (
                row('主页', '回到标题画面（进度重置）', () => setConfirmTitle(true))
              )}
            </div>
            <button className="panel-close" onClick={props.onClose}>
              返回
            </button>
          </>
        ) : (
          <>
            <div className="panel-title">跳章 · 章首</div>
            <div className="slot-list">
              {props.chapters.map((c) => (
                <button key={c.index} className="slot-row" onClick={() => props.onJump(c.index)}>
                  <span className="slot-name">第{c.index}章</span>
                  <span className="slot-info">
                    <span className="slot-line1">{c.title}</span>
                    <span className="slot-line2">跳到本章第一个场景 · 进度重置</span>
                  </span>
                </button>
              ))}
            </div>
            <button className="panel-close" onClick={() => setMode('menu')}>
              返回菜单
            </button>
          </>
        )}
      </div>
    </div>
  );
}
