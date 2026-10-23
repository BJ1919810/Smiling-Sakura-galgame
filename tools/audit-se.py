# -*- coding: utf-8 -*-
"""
声学指标审计：验证重做的音效**真的具备设计中的结构**，而不只是"没报错"。
★ 为什么要这一步：合成最容易犯的错是「参数写对了但听感没变」——
  比如 bursts 撒了 20 个事件，但如果 decay 太长，叠起来还是连续噪声。
  这里用能量包络 / 频谱 / 瞬态密度把"设计意图"验成数字。
"""
from pathlib import Path
import sys

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
SE = ROOT / "素材基准" / "音频" / "SE"


def load(name):
    """
    读母带。
    ⚠️ 立体声**不要**用 `x.mean(axis=1)` 降混 —— stereo() 给左右加了不同的延迟/增益，
       两个声道有相位差，直接平均会**相位抵消**，峰值普遍虚低 3–4 dB
       （2026-09-18 踩过：审计报「风声 −6.9 dBFS」，实际是 −3.0，白查一轮）。
       ⇒ 求峰值/能量时直接用 max per-sample 的绝对值；只要单声道就取第 0 通道。
    """
    x, sr = sf.read(SE / f"se_{name}.wav", always_2d=True)
    return x.astype(np.float64), sr


def peak_db(x):
    return 20 * np.log10(np.max(np.abs(x)) + 1e-12)


def find_file(name: str) -> Path | None:
    """按中文名找母带（文件名里的 ・（） 会被 safe_name 替换成 _，需要模糊匹配）"""
    direct = SE / f"se_{name}.wav"
    if direct.exists():
        return direct
    key = name.replace("・", "").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    for q in SE.glob("se_*.wav"):
        stem = q.stem[3:]
        s2 = stem.replace("_", "").replace("・", "").replace("（", "").replace("）", "").replace("(", "").replace(")", "")
        if s2 == key:
            return q
    return None


def as_mono(x):
    """分析用单声道：取声道平均只在**同相**时安全；这里保守取两者平均后归一化到原始峰值"""
    if x.ndim == 1:
        return x
    m = x.mean(axis=1)
    p_raw = np.max(np.abs(x))
    p_m = np.max(np.abs(m))
    if p_m > 1e-9:
        m = m * (p_raw / p_m)          # 补回降混损失的增益，避免指标虚低
    return m


def transient_density(x, sr, thresh=0.18):
    """
    ★ 已弃用（保留供参考）。原因：这个指标在**宽带噪声**上会假阳性 ——
      纯白噪会报 95/s，因为包络的随机起伏本身就被算成"峰"。
      ⇒ 判定「离散 vs 连续」改用 envelope_crest()，见下。
    瞬态密度：包络局部极大值 / 秒。
    """
    env = np.abs(signal.hilbert(x))
    w = max(1, int(sr * 0.004))
    env = np.convolve(env, np.ones(w) / w, mode="same")
    env /= (env.max() or 1)
    pk, _ = signal.find_peaks(env, height=thresh, distance=int(sr * 0.008))
    return len(pk) / (len(x) / sr)


def envelope_crest(x, sr, win_ms: float = 10.0):
    """
    ★ 「离散 vs 连续」的正确判据 —— **包络峰值因子（crest factor）**。
    做法：把信号切成 10ms 小块，算每块 RMS，再用
        crest = max(RMS块) / mean(RMS块)
    含义：
      · 离散事件（脚步、糖纸、落叶）= 大部分时间是安静的，少数块很响
        ⇒ RMS 分布**极不均** ⇒ crest 大（> 4~6）
      · 连续声（风、嘈杂、水幕）= 能量平摊在每个块上
        ⇒ crest 小（< 3）
    ★ 为什么不用"数峰"：宽带噪声的包络天然毛刺，数峰会把纯噪声报成 95/s（实测），
      完全失去区分度。crest 对"毛刺"不敏感，对"有事件"敏感。
    返回 (crest, 每秒有效事件数)：后者＝ RMS 超过均值 1.8 倍的段数 / 秒。
    """
    n = len(x)
    w = max(1, int(sr * win_ms / 1000))
    nblk = n // w
    if nblk < 4:
        return 1.0, 0.0
    blk = x[:nblk * w].reshape(nblk, w)
    rms = np.sqrt((blk ** 2).mean(axis=1)) + 1e-12
    crest = float(rms.max() / rms.mean())
    events = float((rms > rms.mean() * 1.8).sum() / (n / sr))
    return crest, events


def spectral_centroid(x, sr):
    """频谱重心：量化"亮/暗"。脚步应偏低，糖纸应偏高。"""
    f, p = signal.welch(x, fs=sr, nperseg=min(4096, len(x)))
    p = p / (p.sum() or 1)
    return float((f * p).sum())


def band_energy_ratio(x, sr, lo, hi):
    """某频带占总能量的比例（判断"高频占比"这类特征）"""
    f, p = signal.welch(x, fs=sr, nperseg=min(4096, len(x)))
    tot = p.sum() or 1
    m = (f >= lo) & (f <= hi)
    return float(p[m].sum() / tot)


def formant_check(x, sr, expect_peaks, tol=350.0):
    """
    共振峰检测：在 0–3.6kHz 找谱峰，看预期的共振峰附近是否有峰。
    用于验证「人声」类音效真的有元音结构（这是它像不像人的关键）。
    """
    f, p = signal.welch(x, fs=sr, nperseg=min(8192, len(x)))
    ps = p.copy()
    # 取对数并平滑
    ps = np.log10(ps + 1e-12)
    w = 21
    ps = np.convolve(ps, np.ones(w) / w, mode="same")
    m = (f >= 100) & (f <= 3600)
    fm, pm = f[m], ps[m]
    pk, _ = signal.find_peaks(pm, distance=int(150 / (f[1] - f[0])))
    hits = []
    for want in expect_peaks:
        near = [fm[k] for k in pk if abs(fm[k] - want) <= tol]
        hits.append(bool(near))
    return hits, [round(float(fm[k])) for k in pk]


def report(title, name, expect):
    p = find_file(name)
    if p is None:
        print(f"\n【{title}】⚠ 找不到 se_{name}.wav")
        return
    x, sr = load(p.stem[3:])
    mono = as_mono(x)
    d = len(x) / sr
    crest, nev = envelope_crest(mono, sr)
    sc = spectral_centroid(mono, sr)
    hi = band_energy_ratio(mono, sr, 2000, 12000)
    ch = "立体声" if x.ndim == 2 and x.shape[1] == 2 else "单声道"
    kind = "离散" if crest > 4 else ("偏连续" if crest > 2.2 else "连续")
    print(f"\n【{title}】{p.name}  {d:.2f}s  {ch}  峰值 {peak_db(x):.1f} dBFS")
    print(f"   包络峰值因子 {crest:5.2f} ({kind})  有效事件 {nev:5.1f}/s  谱重心 {sc:7.0f} Hz  高频占比 {hi*100:5.1f}%")
    print(f"   设计预期: {expect}")


def main():
    if not SE.exists():
        print(f"✗ 找不到 {SE}")
        return 1

    print("=" * 72)
    print("声学指标审计（对照设计意图）")
    print("=" * 72)

    report("糖纸轻响", "糖纸轻响", "离散脆响 → 瞬态密度应高（>8/s），谱重心偏高")
    report("风卷枯叶", "风卷枯叶", "离散擦响+风底 → 中高瞬态密度，高频占比高")
    report("水声", "水声", "极密微瞬态 → 瞬态密度很高，高频占比很高")
    report("粉笔", "粉笔", "有节奏划擦 → 中瞬态密度，中高频集中")

    report("风声", "风声", "连续 → 瞬态密度低（<3/s）")
    report("一阵风", "一阵风", "事件但连续 → 瞬态密度低，包络有明确峰")
    report("教室嘈杂", "教室嘈杂", "人声叠加 → 瞬态密度中低，中频为主")

    report("脚步声", "脚步声", "脚 → 每次落跟应有宽带瞬态；谱重心偏低")
    report("踩过落叶的脚步声（由远及近）", "踩过落叶的脚步声（由远及近）",
           "脚步+叶碎 → 瞬态密度应高于普通脚步")
    report("闷响", "闷响", "被挡的低频撞击 → 谱重心应极低（<250Hz），高频极少")
    report("心跳", "心跳", "胸腔 → 谱重心极低，有 4–6 次冲击")

    report("哨声", "哨声", "边缘音+腔体共振 → 谱重心高，能量集中在 2–6kHz")
    report("发令哨", "发令哨", "两声 → 应有 2 个明显冲击")

    # ---- 人声共振峰验证（最关键的一项）----
    print("\n" + "=" * 72)
    print("人声共振峰验证（「像人说话」的核心指标）")
    print("=" * 72)
    for name, wants in [("教室嘈杂", [730, 1090]),
                        ("周围的议论声", [500, 900])]:
        p = find_file(name)
        if p is None:
            print(f"\n【{name}】⚠ 找不到文件")
            continue
        x, sr = load(p.stem[3:])
        hits, peaks = formant_check(as_mono(x), sr, wants)
        print(f"\n【{name}】期望共振峰 {wants} Hz")
        print(f"   检出谱峰: {peaks}")
        print(f"   命中: " + "  ".join(f"{'✓' if h else '✗'}{w}" for h, w in zip(hits, wants))
              + f"  → {'通过' if all(hits) else '⚠ 未命中（人声感可能不足）'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
