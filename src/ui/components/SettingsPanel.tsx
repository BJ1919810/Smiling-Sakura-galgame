/**
 * 设置面板 —— 音量（BGM/SE）/ 文字速度 / 自动播放
 * 变更即时生效并持久化（StorageAdapter）
 */

import {
  TEXT_SCALE_STEPS,
  UI_SCALE_MAX,
  UI_SCALE_MIN,
  type GameSettings,
} from '../../engine/settings.ts';

export function SettingsPanel(props: {
  settings: GameSettings;
  onChange: (s: GameSettings) => void;
  onClose: () => void;
}) {
  const s = props.settings;
  const set = (patch: Partial<GameSettings>) => props.onChange({ ...s, ...patch });

  return (
    <div className="panel-backdrop" onClick={props.onClose}>
      <div className="panel" onClick={(e) => e.stopPropagation()}>
        <div className="panel-title">设置</div>

        <div className="setting-row">
          <span className="setting-label">BGM 音量</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={s.bgmVolume}
            onChange={(e) => set({ bgmVolume: Number(e.target.value) })}
          />
          <span className="setting-value">{Math.round(s.bgmVolume * 100)}</span>
        </div>

        <div className="setting-row">
          <span className="setting-label">SE 音量</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={s.seVolume}
            onChange={(e) => set({ seVolume: Number(e.target.value) })}
          />
          <span className="setting-value">{Math.round(s.seVolume * 100)}</span>
        </div>

        {/* ★ 2026-09-18：语音音量（日配语音轨）—— 与 BGM/SE 并列，构成音量三轨 */}
        <div className="setting-row">
          <span className="setting-label">语音音量</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={s.voiceVolume}
            onChange={(e) => set({ voiceVolume: Number(e.target.value) })}
          />
          <span className="setting-value">{Math.round(s.voiceVolume * 100)}</span>
        </div>

        <div className="setting-row">
          <span className="setting-label">文字速度</span>
          <div className="setting-seg">
            {[
              { label: '慢', v: 28 },
              { label: '标准', v: 45 },
              { label: '快', v: 85 },
              { label: '瞬间', v: 9999 },
            ].map((o) => (
              <button
                key={o.v}
                className={`seg-btn ${s.textSpeed === o.v ? 'seg-on' : ''}`}
                onClick={() => set({ textSpeed: o.v })}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {/* ★ 2026-09-18：正文字号（主人令「菜单要能调字体大小」）—— 复用既有分段控件样式 */}
        <div className="setting-row">
          <span className="setting-label">文字大小</span>
          <div className="setting-seg">
            {TEXT_SCALE_STEPS.map((o) => (
              <button
                key={o.v}
                className={`seg-btn ${s.textScale === o.v ? 'seg-on' : ''}`}
                onClick={() => set({ textScale: o.v })}
              >
                {o.label}
              </button>
            ))}
          </div>
        </div>

        {/* ★ 2026-09-20：整体 UI 大小 —— 乘在「按舞台高度自动适配」之上。
            手机横屏会自动缩到约 60%；这里再按个人口味微调，省得反复出包。 */}
        <div className="setting-row">
          <span className="setting-label">界面大小</span>
          <input
            type="range"
            min={UI_SCALE_MIN}
            max={UI_SCALE_MAX}
            step={0.05}
            value={s.uiScale}
            onChange={(e) => set({ uiScale: Number(e.target.value) })}
            title="相对「按屏幕自动适配」的大小；100% 即完全自动"
          />
          <span className="setting-value">{Math.round(s.uiScale * 100)}%</span>
        </div>

        <div className="setting-row">
          <span className="setting-label">自动播放</span>
          <button
            className={`toggle ${s.autoPlay ? 'toggle-on' : ''}`}
            onClick={() => set({ autoPlay: !s.autoPlay })}
          >
            {s.autoPlay ? '开' : '关'}
          </button>
        </div>

        {/* ★ 2026-09-15：场景提示条从「台本管不着的强制行为」改为玩家设置项 */}
        <div className="setting-row">
          <span className="setting-label">场景提示</span>
          <button
            className={`toggle ${s.sceneToast ? 'toggle-on' : ''}`}
            onClick={() => set({ sceneToast: !s.sceneToast })}
            title="场景切换时是否弹出「■ SCENE_xx 地点」提示条"
          >
            {s.sceneToast ? '开' : '关'}
          </button>
        </div>

        <button className="panel-close" onClick={props.onClose}>
          返回
        </button>
      </div>
    </div>
  );
}
