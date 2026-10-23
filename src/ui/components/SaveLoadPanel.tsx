/**
 * 存读档面板 —— 存/读一体（4 槽：quick + s1..s3）
 *
 * 交互（ADV 惯例）：
 * - 读档模式：有档可点，无档置灰
 * - 存档模式：任意槽可覆盖写入（有档的显示旧信息，覆盖前不再二次确认——本作无删档惩罚）
 */

import { loadMeta, type SaveMeta } from '../../director/Director.ts';
import type { Director } from '../../director/Director.ts';

const SLOTS = ['quick', 's1', 's2', 's3'] as const;
const SLOT_LABEL: Record<string, string> = {
  quick: '快速',
  s1: '档 1',
  s2: '档 2',
  s3: '档 3',
};

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtPlay(ms: number): string {
  const m = Math.floor(ms / 60000);
  return m > 0 ? `${m} 分钟` : '刚刚';
}

export function SaveLoadPanel(props: {
  mode: 'save' | 'load';
  director: Director;
  onClose: () => void;
  onDone: (slot: string) => void;
}) {
  const metas: Record<string, SaveMeta | null> = {};
  for (const s of SLOTS) metas[s] = loadMeta(s);

  const handle = (slot: string) => {
    if (props.mode === 'save') {
      props.director.save(slot);
      props.onDone(slot);
    } else {
      if (props.director.load(slot)) props.onDone(slot);
    }
  };

  return (
    <div className="panel-backdrop" onClick={props.onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-title">{props.mode === 'save' ? '保存进度' : '读取进度'}</div>
        <div className="slot-list">
          {SLOTS.map((slot) => {
            const meta = metas[slot];
            const disabled = props.mode === 'load' && !meta;
            return (
              <button
                key={slot}
                className={`slot-row ${disabled ? 'slot-disabled' : ''} ${meta ? 'slot-occupied' : 'slot-empty'}`}
                disabled={disabled}
                onClick={() => handle(slot)}
              >
                <span className="slot-name">{SLOT_LABEL[slot]}</span>
                {meta ? (
                  <span className="slot-info">
                    <span className="slot-line1">
                      第 {meta.chapter || 1} 章 · {meta.sceneId}
                    </span>
                    <span className="slot-line2">
                      {meta.excerpt ? `「${meta.excerpt}…」` : '（未开始阅读）'}
                    </span>
                  </span>
                ) : (
                  <span className="slot-info slot-info-empty">— 空 —</span>
                )}
                <span className="slot-time">{meta ? `${fmtTime(meta.savedAt)} · ${fmtPlay(meta.playTimeMs)}` : ''}</span>
              </button>
            );
          })}
        </div>
        <button className="panel-close" onClick={props.onClose}>
          返回
        </button>
      </div>
    </div>
  );
}
