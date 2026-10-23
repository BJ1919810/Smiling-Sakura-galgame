# -*- coding: utf-8 -*-
"""生成「音色丢失」对照试听台（单文件 HTML，媒体 base64 内联）。

背景（主人反馈）：`これが戦争なわけねえだろ、災害だよ災害。` 后半句「災害」处音色丢了。
主人已明确：**这与情绪标注无关**，是独立问题。

本页把所有对照条件并排放，靠耳朵判：
  · yota_05          原始问题样本（yc 首次生成）
  · probe_orig       同文本 + 同情绪 **重跑一次** → 若也丢＝稳定复现；若不丢＝生成随机性
  · probe_nat        同文本，纯自然（去掉高兴）
  · probe_emo        同文本，高兴占大头
  · probe_alpha07    同文本同情绪，emo_alpha 0.7
  · probe_short2     只念后半句「災害だよ災害。」→ 若正常＝长句尾部漂移
  · probe_excl       「見て！！！」激动台词（主人举的例子）
  · probe_line143    原示例改激动口径，对比 yota_01

⚠️ 页面上的「谱质心」**仅供参考**：本项目实测它**不能单独当判据**
   （日语某些音素天然偏高；正常条目也能到 1690 Hz）。
   ⛔ 也别用「与参考的绝对谱距」——短窗谱估计方差大，正常条目能到 22.9 dB（绕过两次弯路）。
"""
from __future__ import annotations

import base64
import json
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.join(ROOT, '.workbuddy', 'tmp')
OUT_HTML = os.path.join(TMP, '阳太音色对照.html')
FFMPEG = r'D:\ffmpeg\bin\ffmpeg.exe'
SPEC_DIR = os.path.join(TMP, 'spec')


def db(v: float) -> float:
    return 20 * np.log10(max(float(v), 1e-10))


def spec_centroid(x: np.ndarray, sr: int, nfft: int = 2048) -> float:
    """谱质心（Hz）—— 粗指标，仅参考。"""
    win = np.hanning(nfft)
    acc = np.zeros(nfft // 2 + 1)
    cnt = 0
    for i in range(0, max(0, len(x) - nfft), nfft // 4):
        seg = x[i:i + nfft]
        if np.sqrt((seg ** 2).mean()) < 1e-4:
            continue
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
        cnt += 1
    if cnt == 0:
        return 0.0
    p = acc / cnt
    f = np.fft.rfftfreq(nfft, 1 / sr)
    m = f <= 8000
    return float((f[m] * p[m]).sum() / max(p[m].sum(), 1e-20))


def measure(path: str) -> dict:
    x, sr = sf.read(path, always_2d=True, dtype='float64')
    mono = x.mean(axis=1)
    n = len(mono)
    c_all = spec_centroid(mono, sr)
    c_head = spec_centroid(mono[: n // 2], sr)
    c_tail = spec_centroid(mono[n // 2:], sr)
    blk = int(0.1 * sr)
    nb = len(mono) // blk
    eff = 0.0
    if nb:
        r = np.array([np.sqrt((mono[i * blk:(i + 1) * blk] ** 2).mean()) for i in range(nb)])
        k = max(1, int(round(nb * 0.2)))
        eff = float(np.sort(r)[-k:].mean())
    return {'sec': round(n / sr, 2), 'peak': round(db(float(np.abs(mono).max())), 1),
            'eff': round(db(eff), 1),
            'c_all': round(c_all), 'c_head': round(c_head), 'c_tail': round(c_tail)}


CARDS = [
    ('yota_05', '原始问题样本', os.path.join(TMP, 'yota_demo', 'yota_05.wav'),
     '主人反馈「災害」处音色丢失的那条（首次生成）。', 'bad'),
    ('probe_orig', '同条件重跑 ★', os.path.join(TMP, 'yota_probe', 'probe_orig.wav'),
     '**完全相同的文本 + 情绪**，重新生成一次。'
     '若这条正常 ⇒ 原问题是**生成随机性**；若同样丢 ⇒ 稳定复现、要找 稳因。', 'key'),
    ('probe_nat', '纯自然（去掉高兴）', os.path.join(TMP, 'yota_probe', 'probe_nat.wav'),
     '同文本，emo 只有「自然 0.60」。', ''),
    ('probe_emo', '高兴占大头', os.path.join(TMP, 'yota_probe', 'probe_emo.wav'),
     '同文本，emo「高兴 0.50 + 自然 0.22」。', ''),
    ('probe_alpha07', '同情绪 · alpha 0.7', os.path.join(TMP, 'yota_probe', 'probe_alpha07.wav'),
     '同文本同情绪，但情绪强度降到 0.7。', ''),
    ('probe_short2', '只念后半句 ★', os.path.join(TMP, 'yota_probe', 'probe_short2.wav'),
     '只合成「災害だよ災害。」。**若这条正常 ⇒ 问题出在长句尾部漂移**。', 'key'),
    ('probe_excl', 'が見て！！！（激动）', os.path.join(TMP, 'yota_probe', 'probe_excl.wav'),
     '主人举的「非常激动的台词」例子，`高兴 0.55 + 自然 0.18`（自然压低）。', 'new'),
    ('probe_excl2', '見て！！！ alpha 0.85', os.path.join(TMP, 'yota_probe', 'probe_excl2.wav'),
     '同上但情绪强度 0.85。', 'new'),
    ('probe_line143', '原示例 L143 改激动', os.path.join(TMP, 'yota_probe', 'probe_line143.wav'),
     '「どけどけー、通して通してー！」改用 `高兴 0.55 + 自然 0.18`，和 yota_01 对比。', 'new'),
]

ZH = {
    'yota_05': ('这不是仗，这是灾难。', 'これが戦争なわけねえだろ、災害だよ災害。'),
    'probe_orig': ('这不是仗，这是灾难。', 'これが戦争なわけねえだろ、災害だよ災害。'),
    'probe_nat': ('这不是仗，这是灾难。', 'これが戦争なわけねえだろ、災害だよ災害。'),
    'probe_emo': ('这不是仗，这是灾难。', 'これが戦争なわけねえだろ、災害だよ災害。'),
    'probe_alpha07': ('这不是仗，这是灾难。', 'これが戦争なわけねえだろ、災害だよ災害。'),
    'probe_short2': ('这是灾难。', '災害だよ災害。'),
    'probe_excl': ('你看！！！', '見て！！！'),
    'probe_excl2': ('你看！！！', '見て！！！'),
    'probe_line143': ('让让让让——借过借过——', 'どけどけー、通して通してー！'),
}


def main() -> int:
    os.makedirs(SPEC_DIR, exist_ok=True)
    cards = []
    for cid, name, path, desc, tag in CARDS:
        if not os.path.isfile(path):
            print(f'  ⚠️ 缺 {path}')
            continue
        m = measure(path)
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('ascii')
        zh, ja = ZH.get(cid, ('', ''))
        cards.append({'id': cid, 'name': name, 'desc': desc, 'tag': tag,
                      'zh': zh, 'ja': ja, 'm': m,
                      'src': f'data:audio/wav;base64,{b64}'})
        print(f'  {cid:<16} {m["sec"]:>5.2f}s  质心 整{m["c_all"]:>5} '
              f'前{m["c_head"]:>5} 后{m["c_tail"]:>5}  峰值 {m["peak"]}')

    payload = json.dumps(cards, ensure_ascii=False)
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>阳太 · 音色对照</title>
<style>
  :root { --bg:#f7f7f5; --card:#fff; --line:#e4e4e0; --ink:#23262b; --dim:#6b7078;
          --accent:#2f6fb0; --good:#1f7a4d; --warn:#a8600f; --star:#b8860b; --bad:#b03030; }
  * { box-sizing:border-box; }
  body { margin:0; padding:28px 20px 60px; background:var(--bg); color:var(--ink);
         font:14px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
  .wrap { max-width:960px; margin:0 auto; }
  h1 { font-size:21px; margin:0 0 4px; font-weight:600; }
  .sub { color:var(--dim); font-size:13px; margin-bottom:22px; line-height:1.8; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px;
          padding:15px 18px; margin-bottom:12px; }
  .card.key { border-color:var(--star); box-shadow:0 0 0 1px rgba(184,134,11,.18); }
  .card.bad { border-color:#e8c4c4; }
  .card h3 { font-size:14.5px; margin:0 0 3px; font-weight:600; }
  .card .desc { color:var(--dim); font-size:12.5px; margin-bottom:11px; }
  .card .desc b { color:var(--ink); }
  .zh { font-size:14px; margin:0 0 3px; }
  .ja { font-size:13px; color:var(--accent); margin-bottom:10px; }
  .metrics { display:flex; flex-wrap:wrap; gap:5px 16px; font-size:12.5px;
             margin-bottom:11px; color:var(--dim); }
  .metrics b { font-weight:600; color:var(--ink); }
  audio { width:100%; height:34px; }
  .tag { display:inline-block; font-size:11px; padding:1px 7px; border-radius:4px;
         background:#eef3f9; color:var(--accent); margin-left:7px; vertical-align:2px; }
  .tag.key { background:#fdf6e3; color:var(--star); }
  .tag.bad { background:#fbeaea; color:var(--bad); }
  .tag.new { background:#eaf6ef; color:var(--good); }
  .foot { color:var(--dim); font-size:12px; margin-top:22px; line-height:1.9; }
</style>
</head>
<body>
<div class="wrap">
  <h1>阳太 · 「音色丢失」对照</h1>
  <div class="sub">同一参考音频、同一模型，只改<b>一个</b>变量。点是听、会自动互斥。<br>
    ★ 优先听两张<b>金色边框</b>的卡：它们最能定位原因。</div>
  <div id="cards"></div>
  <div class="foot">
    ⚠️ 卡片上的「谱质心」<b>仅供参考</b>，不能单独当判据 ——
    日语某些音素天然偏高，正常条目也能到 1690 Hz。<br>
    ⛔ 也别用「与参考的绝对谱距」判断：短窗谱估计方差大，正常条目能到 22.9 dB。
  </div>
</div>
<script>
const DATA = __PAYLOAD__;
let cur = null;
function stopAll() {
  document.querySelectorAll('audio').forEach(a => {
    a.onended = null;
    if (!a.paused) a.pause();
    try { a.currentTime = 0; } catch (e) {}
  });
  cur = null;
}
const host = document.getElementById('cards');
DATA.forEach(c => {
  const card = document.createElement('div');
  card.className = 'card' + (c.tag ? ' ' + c.tag : '');
  const h = document.createElement('h3');
  h.textContent = c.name;
  if (c.tag === 'key') { const t=document.createElement('span'); t.className='tag key'; t.textContent='关键对照'; h.appendChild(t); }
  if (c.tag === 'bad') { const t=document.createElement('span'); t.className='tag bad'; t.textContent='问题样本'; h.appendChild(t); }
  if (c.tag === 'new') { const t=document.createElement('span'); t.className='tag new'; t.textContent='新口径'; h.appendChild(t); }
  const d = document.createElement('div');
  d.className = 'desc';
  d.innerHTML = c.desc.replace(/\\*\\*(.+?)\\*\\*/g, '<b>$1</b>');
  card.appendChild(h); card.appendChild(d);
  if (c.zh) { const z=document.createElement('div'); z.className='zh'; z.textContent=c.zh; card.appendChild(z); }
  if (c.ja) { const j=document.createElement('div'); j.className='ja'; j.textContent=c.ja; card.appendChild(j); }
  const m = document.createElement('div');
  m.className = 'metrics';
  m.innerHTML = '时长 <b>' + c.m.sec + 's</b>'
    + ' · 峰值 <b>' + c.m.peak + ' dBFS</b>'
    + ' · 有效响度 <b>' + c.m.eff + ' dB</b>'
    + ' · 谱质心 <b>' + c.m.c_all + '</b> Hz（前 ' + c.m.c_head + ' / 后 ' + c.m.c_tail + '）';
  card.appendChild(m);
  const au = document.createElement('audio');
  au.controls = true; au.preload = 'none'; au.src = c.src;
  au.addEventListener('play', () => { if (cur && cur !== au) stopAll(); cur = au; });
  au.addEventListener('ended', () => { if (cur === au) cur = null; });
  card.appendChild(au);
  host.appendChild(card);
});
</script>
</body>
</html>
"""
    html = html.replace('__PAYLOAD__', payload)
    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n>>> {OUT_HTML}  {os.path.getsize(OUT_HTML)/1048576:.2f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main())
