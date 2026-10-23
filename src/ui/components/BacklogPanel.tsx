/**
 * 历史对话记录（backlog）面板 ★ 2026-09-19 加。
 *
 * - 列出本次会话读过的全部台词（含旁白），旧的在上、新的在下，自动滚到底部；
 * - 有日配语音的条目显示 ▶ 重播按钮（`director.replayVoice`）；
 * - 样式复用 `.panel` / `.panel-backdrop` / `.panel-close`，仅新增 `.backlog-*` 少量排版类。
 */

import { useEffect, useRef } from 'react';
import type { Director, HistoryEntry } from '../../director/Director.ts';

export function BacklogPanel(props: { director: Director; onClose: () => void }) {
  const entries = props.director.getHistory();
  const bottomRef = useRef<HTMLDivElement>(null);

  // 打开面板时滚到最新一条
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' });
  }, []);

  const replay = (e: HistoryEntry) => {
    if (e.line !== null && props.director.audio.hasVoice(e.line)) {
      props.director.replayVoice(e.line);
    }
  };

  return (
    <div className="panel-backdrop" onClick={props.onClose}>
      <div className="panel backlog-panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-title">历史记录</div>
        <div className="backlog-list">
          {entries.length === 0 && (
            <div className="backlog-empty">还没有读过任何台词。</div>
          )}
          {entries.map((e) => {
            const voiced = e.line !== null && props.director.audio.hasVoice(e.line);
            return (
              <div key={e.seq} className="backlog-item">
                <div className="backlog-head">
                  {e.who && <span className="backlog-who">{e.who}</span>}
                  {voiced && (
                    <button
                      className="backlog-play"
                      title="重播这句的语音"
                      onClick={() => replay(e)}
                    >
                      ▶
                    </button>
                  )}
                </div>
                <p className={`backlog-text ${e.who ? '' : 'backlog-narration'}`}>{e.text}</p>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
        <button className="panel-close" onClick={props.onClose}>
          返回
        </button>
      </div>
    </div>
  );
}
