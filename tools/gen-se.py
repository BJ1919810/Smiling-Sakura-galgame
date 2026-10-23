# -*- coding: utf-8 -*-
"""
《笑樱》SE（音效）合成器 —— 纯代码合成，零外部依赖（numpy + scipy + ffmpeg）

★ 为什么是合成而不是 AI 生成（2026-09-18 主人拍板）
  查过市场：beatra 只有 `beatra.music.generate`（Suno 6 = 360 积分/次，
  MiniMax 3.0 = 240 积分/次）与 `beatra.speech.synthesize`，**没有音效通道**。
  拿歌曲模型去要「糖纸轻响」＝ 用量级完全不成比例的费用换一个几毛钱的效果。
  而这 30 个音效的本质都是「包络噪声 + 频带滤波 + 共振」—— 合成能做，
  而且零成本、可无限次调参、完全可复现。

★ 设计口径
  · 采样率 44.1 kHz，**单声道**（游戏音效不需要立体声，engines 也一样放）
    —— 例外：环境类做轻微立体声宽度（见 stereo()），提升包裹感
  · 每个音效 = 若干「层」相加，每层 = 包络 × 声源（噪声/正弦/脉冲串）
  · 归一化到 **−3 dBFS 峰值**（不削顶），响度差异留给引擎侧 seVolume 调
    （主人 2026-09-18 定：「先出素材，响度等实机调」）
  · 首尾**强制淡入淡出**（1–5 ms）—— 防爆音（click），这是数字音频最常见的坑

★ 输出
  WAV 母带 → `素材基准/音频/SE/se_<中文名>.wav`（归档，人可读）
  OGG 引擎 → `public/assets/audio/se/se_0NN.ogg`（编号来自 assets.manifest.json）

用法：
  python tools/gen-se.py --list               # 列出全部配方与状态
  python tools/gen-se.py --only 风声 脚步声 放学铃   # 只出这几个（试听用）
  python tools/gen-se.py --all                # 全出
  python tools/gen-se.py --all --no-ogg       # 只出 wav 母带
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "src" / "generated" / "assets.manifest.json"
MASTER_DIR = ROOT / "素材基准" / "音频" / "SE"
DST = ROOT / "public" / "assets" / "audio" / "se"

SR = 44100
RNG = np.random.default_rng(20260918)  # 固定种子 ⇒ 可复现


# ============================================================
# 基础工具
# ============================================================


def t(dur: float) -> np.ndarray:
    """时间轴"""
    return np.arange(int(SR * dur)) / SR


def noise(dur: float, kind: str = "white") -> np.ndarray:
    """噪声源：white / pink / brown（低频越来越重）"""
    n = int(SR * dur)
    w = RNG.standard_normal(n)
    if kind == "white":
        return w
    if kind == "pink":
        # Voss 近似：白噪声 → 累积滤波
        b = np.array([0.049922035, -0.095993537, 0.050612699, -0.004408786])
        a = np.array([1.0, -2.494956002, 2.017265875, -0.522189400])
        return signal.lfilter(b, a, w) * 0.7
    if kind == "brown":
        return np.cumsum(w) / np.sqrt(n) * 3.0
    raise ValueError(kind)


def env_ad(dur: float, attack: float, decay: float, curve: float = 2.0) -> np.ndarray:
    """
    攻击-衰减包络（最常用）。attack/decay 单位秒。
    curve 越大，衰减前段越"抱得住"（像真实物体振动）。
    """
    n = int(SR * dur)
    e = np.ones(n)
    na = max(1, int(SR * attack))
    nd = max(1, int(SR * decay))
    e[:na] = np.linspace(0, 1, na) ** 0.6          # 攻击：稍软，免爆
    tail = np.linspace(0, 1, min(nd, n))
    e[n - len(tail):] = (1 - tail) ** curve
    return e


def env_ar(dur: float, attack: float, release: float) -> np.ndarray:
    """攻击-保持-释放（持续类音效：风、嘈杂、水声）"""
    n = int(SR * dur)
    e = np.ones(n)
    na = max(1, int(SR * attack))
    nr = max(1, int(SR * release))
    e[:na] = np.linspace(0, 1, na)
    e[n - nr:] = np.linspace(1, 0, nr)
    return e


def bp(x: np.ndarray, lo: float, hi: float, order: int = 4) -> np.ndarray:
    """带通滤波（频带是音色性格的关键）"""
    hi = min(hi, SR * 0.45)
    lo = max(lo, 20.0)
    if lo >= hi:
        return x
    sos = signal.butter(order, [lo, hi], btype="band", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def lp(x: np.ndarray, cut: float, order: int = 4) -> np.ndarray:
    sos = signal.butter(order, min(cut, SR * 0.45), btype="low", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def hp(x: np.ndarray, cut: float, order: int = 4) -> np.ndarray:
    sos = signal.butter(order, max(cut, 20.0), btype="high", fs=SR, output="sos")
    return signal.sosfilt(sos, x)


def sine(dur: float, freq: float, detune: float = 0.0) -> np.ndarray:
    """正弦；detune>0 时做轻微失谐（多个振荡器叠加，听感更"真"）"""
    if detune <= 0:
        return np.sin(2 * np.pi * freq * t(dur))
    out = np.zeros(int(SR * dur))
    for k, d in enumerate([-detune, 0.0, detune]):
        out += np.sin(2 * np.pi * (freq + d) * t(dur))
    return out / 3.0


def bell(dur: float, base: float, partials: list[tuple[float, float]]) -> np.ndarray:
    """
    钟/铃类：多个非整数倍泛音，各带独立衰减时间。
    partials = [(倍率, 衰减秒), ...] —— 真实钟声的泛音**不是整数倍**（inharmonic）。
    """
    out = np.zeros(int(SR * dur))
    for ratio, dec in partials:
        seg = np.sin(2 * np.pi * base * ratio * t(dur))
        out += seg * env_ad(dur, 0.001, dec, 2.5)
    return out


def click(dur: float, cut_lo: float, cut_hi: float) -> np.ndarray:
    """短促撞击：极短包络 × 窄带噪声"""
    e = env_ad(dur, 0.0005, dur, 4.0)
    return bp(noise(dur), cut_lo, cut_hi) * e


def mix(*parts: np.ndarray) -> np.ndarray:
    """
    叠加任意长度的波形 —— **按最长者对齐，短的补零**。
    ⚠️ 别直接用 `a + b`：numpy 广播要求长度一致，长度不同会炸
    （2026-09-18 踩过：click(0.16) + click(0.07) → broadcast error）。
    本文件内所有"不同长度相加"一律走这个函数。
    """
    if not parts:
        return np.zeros(1)
    n = max(len(p) for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[:len(p)] += p
    return out


def steps(dur: float, count: int, spread: float = 0.6, jitter: float = 0.18,
          lo: float = 180.0, hi: float = 1600.0, gain_decay: float = 0.0) -> np.ndarray:
    """
    脚步串：count 次撞击，间隔带抖动（真人脚步不会精确等距）。
    spread——整段占用的比例；gain_decay>0 时逐步减弱（走远）。
    """
    out = np.zeros(int(SR * dur))
    usable = dur * spread
    for i in range(count):
        base = usable * (i / max(1, count - 1)) if count > 1 else usable / 2
        base += RNG.uniform(-jitter, jitter) * (usable / max(1, count))
        idx = int(SR * base)
        g = 1.0 - gain_decay * (i / max(1, count - 1)) if count > 1 else 1.0
        # 每一步＝低频"落" + 高频"擦"
        thud = click(0.16, lo, hi * 0.5) * 0.7 * g
        scuff = click(0.07, hi * 0.8, hi * 2.2) * 0.45 * g
        seg = mix(thud, scuff)
        end = min(len(out), idx + len(seg))
        out[idx:end] += seg[:end - idx]
    return out


def shimmer(dur: float, base: float, rate: float, depth: float = 0.6,
            lo: float = 2000, hi: float = 9000) -> np.ndarray:
    """
    金属/玻璃碎响：高频噪声被一个低频振荡器调幅 —— 「风铃感」的来源。
    铝板琴（有音高）≠ 风铃（无音高），这里做的是后者。
    """
    n = int(SR * dur)
    am = 1.0 - depth + depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t(dur)))
    return bp(noise(dur), lo, hi) * am


def stereo(x: np.ndarray, width: float = 0.35) -> np.ndarray:
    """
    单声道 → 轻微立体声：左右用略微不同的延迟 + 增益，制造包裹感。
    环境类音效专用（脚步/糖纸这类点音效保持单声道更省）。
    """
    d = int(SR * 0.004 * width / 0.35)  # ≤4ms 延迟，不会相位打架
    l = x
    r = np.concatenate([np.zeros(d), x[:-d]]) if d > 0 else x
    return np.stack([l, r * 0.92 + l * 0.08], axis=1)


def fade_edges(x: np.ndarray, ms: float = 3.0) -> np.ndarray:
    """首尾强制淡入淡出 —— 防 click 爆音（数字音频第一铁律）"""
    n = max(2, int(SR * ms / 1000))
    if x.ndim == 2:
        x = x.copy()
        x[:n] *= np.linspace(0, 1, n)[:, None]
        x[-n:] *= np.linspace(1, 0, n)[:, None]
    else:
        x = x.copy()
        x[:n] *= np.linspace(0, 1, n)
        x[-n:] *= np.linspace(1, 0, n)
    return x


def normalize(x: np.ndarray, peak_db: float = -3.0) -> np.ndarray:
    """归一化到指定峰值（dBFS），安全不削顶"""
    m = float(np.max(np.abs(x)))
    if m < 1e-9:
        return x
    target = 10 ** (peak_db / 20.0)
    return x * (target / m)


# ============================================================
# 第二组工具：**事件化 / 谐振 / 共振峰**
# ------------------------------------------------------------
# ★ 2026-09-18 主人听感反馈后新增。上一版失败的根因（重要）：
#   把「本该是一连串离散撞击的声音」做成了「一整段连续的带通噪声」。
#   听感上就变成「沙锤摇晃」而不是「糖纸窸窣」——
#   因为真实世界这类声音的能量是**抖动分布的一颗颗瞬态**，
#   连续噪声听起来一定像「摩擦/摇晃」而不是「脆响」。
#   ★★ 铁律：**动作类音效先想「它由几声组成、每声多长、间隔多少」，
#      再想频带。** 频带只决定「材质」，节奏才决定「是什么动作」。
# ============================================================


def bursts(dur: float, events: list[tuple[float, float]],
           lo: float, hi: float, curve: float = 3.0, decay: float | None = None) -> np.ndarray:
    """
    事件串：按 (起始秒, 增益) 列表摆放一串**离散瞬态**，每个 = 极短攻击 × 窄带噪声。
    这是「脆响类」音效的正确骨架（糖纸 / 落叶 / 纸页 / 粉笔）。
    decay=None 时按 0.02–0.08s 随机取（碎响的自然长度）。
    """
    out = np.zeros(int(SR * dur))
    for off, g in events:
        d2 = decay if decay is not None else float(RNG.uniform(0.018, 0.075))
        seg = click(d2, lo, hi) * env_ad(d2, 0.0008, d2, curve)
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        if i >= len(out) or end <= i:
            continue
        out[i:end] += seg[:end - i] * g
    return out


def scatter(n: int, dur: float, lo_t: float = 0.0, hi_t: float | None = None,
            gmin: float = 0.35, gmax: float = 1.0) -> list[tuple[float, float]]:
    """在 [lo_t, hi_t] 内**非均匀**撒 n 个事件（脆响类用；均匀撒会听出机械感）"""
    hi_t = dur if hi_t is None else hi_t
    ts = np.sort(RNG.uniform(lo_t, hi_t, n))
    return [(float(x), float(RNG.uniform(gmin, gmax))) for x in ts]


def resonator(x: np.ndarray, freqs: list[float], q: float = 12.0,
              gains: list[float] | None = None) -> np.ndarray:
    """
    并联谐振器组 —— 给噪声「形状」，是材质感的关键（缺了它，噪声就只是噪声）。
    典型用法：
      · 塑料膜（糖纸）→ [2600, 4300, 6800] Q≈8
      · 纸             → [1400, 2600, 5200] Q≈5
      · 金属薄板       → [1800, 3100, 5400] Q≈22
    实现：用 scipy 的 iirpeak（二阶带通谐振），并联相加。
    """
    if gains is None:
        gains = [1.0] * len(freqs)
    out = np.zeros_like(x)
    for f, g in zip(freqs, gains):
        if f >= SR * 0.45:
            continue
        b, a = signal.iirpeak(f / (SR / 2), Q=q)
        out += signal.lfilter(b, a, x) * g
    return out


def thump(dur: float, f_lo: float, f_hi: float, decay: float,
          noise_amt: float = 0.35, curve: float = 2.2) -> np.ndarray:
    """
    低频撞击：「落」的感觉 —— 下滑正弦（音高下坠）+ 少量噪声。
    ★ 音高下坠是「重物落地」的核心线索：纯低频正弦听起来像"咚"的电子鼓，
      而真东西是 f 从高往下滑（f_lo 高 → f_hi 低）。
    """
    n = int(SR * dur)
    tt = np.arange(n) / SR
    # 频率指数下滑：f(t) = f_lo * (f_hi/f_lo)^(t/dur)
    k = np.log(max(f_hi, 1.0) / max(f_lo, 1.0))
    phase = 2 * np.pi * f_lo * dur * np.expm1(k * tt / dur) / k
    body = np.sin(phase) * env_ad(dur, 0.002, decay, curve)
    body += lp(noise(dur), 220) * env_ad(dur, 0.001, decay * 0.55, 3.0) * noise_amt
    return body


def voice_blip(dur: float, f0: float, formants: list[tuple[float, float]],
               syll_n: int = 1) -> np.ndarray:
    """
    「像人说话」的元音片段 —— 只为**听得出来是人**，不追求可懂。
    做法：脉冲串（声带）→ 过一组共振峰（formants = [(中心频率, 带宽), ...]）。
    典型元音共振峰（男声）：/a/ = (730,80)(1090,90)(2440,120)
                            /i/ = (270,60)(2290,100)(3010,120)
                            /o/ = (570,80)(840,90)(2410,120)
    ★ 上一版「教室嘈杂/周围议论」失败的根因就是**没有共振峰**：
      纯带通噪声没有「元音」的峰，于是永远只是"呼呼"的风，不像人在讲话。
    """
    n = int(SR * dur)
    tt = np.arange(n) / SR
    # 声带脉冲串（基频 f0 + 轻微抖动，避免电子感）
    jitter = 1.0 + 0.02 * np.sin(2 * np.pi * 3.1 * tt) + 0.01 * np.sin(2 * np.pi * 7.3 * tt)
    pulse = np.zeros(n)
    period = SR / max(f0, 1.0)
    pos = 0.0
    while pos < n:
        i = int(pos)
        if i < n:
            # 每个声门脉冲 = 极短指数衰减
            l = min(n - i, int(SR * 0.004))
            pulse[i:i + l] += np.exp(-np.arange(l) / (SR * 0.0008))
        pos += period * float(jitter[min(int(pos), n - 1)] if n > 0 else 1.0)
    # 过共振峰
    out = np.zeros(n)
    for f, bw in formants:
        if f >= SR * 0.45:
            continue
        q = max(1.0, f / max(bw, 1.0))
        b, a = signal.iirpeak(f / (SR / 2), Q=q)
        out += signal.lfilter(b, a, pulse)
    # 音节包络（让它是"一句一句"而不是连续嗡嗡）
    env = np.zeros(n)
    seg = n / max(syll_n, 1)
    for k in range(max(syll_n, 1)):
        i0, i1 = int(k * seg), int((k + 1) * seg)
        if i1 <= i0:
            continue
        m = i1 - i0
        e = np.ones(m)
        a = max(2, int(m * 0.25))
        e[:a] = np.linspace(0, 1, a)
        e[-a:] = np.linspace(1, 0, a)
        env[i0:i1] = e
    return out * env * env_ar(dur, 0.01, 0.05)


def whoosh(dur: float, lo: float, hi: float, sweep: tuple[float, float] = (0.25, 0.75),
           am_rate: float = 0.0, am_depth: float = 0.0) -> np.ndarray:
    """
    「划过」的声音：滤带中心随时间滑动（扇子/挥动/衣料摩擦）。
    sweep=(起比例, 终比例) 指中心频率在 lo..hi 中的位置。
    """
    n = int(SR * dur)
    x = noise(dur, "white")
    out = np.zeros(n)
    # 分 24 段做时变滤波（够平滑，且比逐样本快得多）
    segn = max(1, n // 24)
    for k in range(24):
        i0, i1 = k * segn, min(n, (k + 1) * segn)
        if i1 <= i0:
            continue
        p = k / 23.0
        c = lo * (hi / lo) ** (sweep[0] + (sweep[1] - sweep[0]) * p)
        f_lo = max(60.0, c / 2.2)
        f_hi = min(SR * 0.44, c * 2.2)
        seg = bp(x[i0:i1], f_lo, f_hi)
        out[i0:i1] = seg
    env = env_ad(dur, 0.05, dur * 0.85, 1.6)
    if am_rate > 0:
        env = env * (1.0 - am_depth + am_depth *
                     (0.5 + 0.5 * np.sin(2 * np.pi * am_rate * t(dur) + RNG.uniform(0, 6.28))))
    return out * env



# ============================================================
# 30 个音效配方
# 每个函数返回 float32 [-1,1] 的波形（单声道或 Nx2）
# ============================================================


def se_铃声预备(dur=2.4):
    """校内预备铃：电子钟声，三连短促（中国校园经典铃声）"""
    out = np.zeros(int(SR * dur))
    for k, off in enumerate([0.0, 0.42, 0.84]):
        seg = bell(1.2, 784.0, [(1.0, 0.55), (2.0, 0.38), (2.76, 0.28), (5.4, 0.15)])
        seg += bell(1.2, 1046.5, [(1.0, 0.42), (2.01, 0.25)]) * 0.5
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        out[i:end] += seg[:end - i] * (1.0 - k * 0.12)
    return lp(out, 6000)


def se_教室嘈杂(dur=4.5):
    """
    教室午休的嘈杂：**多个人在说话，但不听清单句**。
    ★ 重做原因（主人：不符合意象）—— 上一版是「带通粉噪 × 慢调制」，
      那是**风的模型**：只有能量起伏，没有人声最关键的**共振峰**。
      人说话之所以被听成"人"，靠的是元音共振峰（/a/ /i/ /o/ 的峰位）。
    ⇒ 现在：多个 voice_blip（不同 f0、不同元音）错落叠放 + 底下垫一层低语底噪。
      男声 f0 ≈ 110–140 Hz，女声 ≈ 190–230 Hz。
    """
    out = np.zeros(int(SR * dur))
    # 元音共振峰库（女声偏高；教室里男女都有）
    VOWELS = [
        [(730, 90), (1090, 100), (2440, 130)],    # /a/
        [(270, 70), (2290, 110), (3010, 130)],    # /i/
        [(570, 90), (840, 100), (2410, 130)],     # /o/
        [(440, 80), (1020, 100), (2240, 130)],    # /e/
    ]
    # 撒 14 段"人声碎片"，每段 1–3 个音节 —— 重叠起来就是"听不懂的嘈杂"
    for _ in range(14):
        f0 = float(RNG.uniform(105, 235))
        d2 = float(RNG.uniform(0.28, 0.62))
        syll = int(RNG.integers(1, 4))
        voice = voice_blip(d2, f0, VOWELS[int(RNG.integers(0, len(VOWELS)))], syll_n=syll)
        voice = bp(voice, 120, 3600)
        i = int(RNG.uniform(0, max(0.01, dur - d2)) * SR)
        g = float(RNG.uniform(0.5, 1.0))
        end = min(len(out), i + len(voice))
        if end > i:
            out[i:end] += voice[:end - i] * g
    # 底噪：远处没法分辨的说话（低语层），让整体有"满屋子人"的稠密感
    mur = bp(noise(dur, "pink"), 180, 2600)
    am = np.clip(0.55 + 0.45 * np.sin(2 * np.pi * 1.3 * t(dur) + 1.1), 0, 1)
    out += mur * am * 0.42
    # 点缀：椅子 / 桌面 / 纸张（让场景是"教室"而不只是"有人"）
    for _ in range(6):
        i = int(RNG.uniform(0, dur - 0.3) * SR)
        seg = (click(0.09, 1500, 5000) * 0.22 if RNG.random() < 0.5
               else click(0.14, 120, 900) * 0.26)
        end = min(len(out), i + len(seg))
        if end > i:
            out[i:end] += seg[:end - i]
    out = hp(out, 100) * env_ar(dur, 0.25, 0.5)
    return stereo(out, 0.45)


def footstep(dur: float = 0.19, lo: float = 110.0, hi: float = 900.0,
             heel: float = 0.6, scuff: float = 0.4, sole: str = "shoe") -> np.ndarray:
    """
    单次脚步 —— 三段式，这是脚步"像脚步"的关键：
      ① 落跟（heel strike）：**极短、宽带**的冲击。这是脚步最标志性的成分，
         时间只有 2–5 ms，频带很宽（几百 Hz 一直到 6 kHz）。
         ★ 上一版只做了「低频 thud + 高频 scuff」，缺了这个瞬态 ⇒ 听成"闷响"而非"脚步"。
      ② 体重（body）：低频下滑，脚踩实、身体重量压上去。
      ③ 擦地（scuff）：鞋底与地面的摩擦，"沙"一声，鞋种不同音色不同。
    sole: "shoe" 运动鞋 / "leather" 皮鞋 / "slipper" 拖鞋
    """
    n = int(SR * dur)
    out = np.zeros(n)

    # ① 落跟：2–4 ms 的宽带瞬态
    hd = float(RNG.uniform(0.002, 0.004))
    h = bp(noise(hd), 300, min(9000, SR * 0.44)) * env_ad(hd, 0.0002, hd, 2.0)
    h += lp(noise(hd), 400) * env_ad(hd, 0.0002, hd * 0.6, 2.5) * 0.5
    out[:len(h)] += h * heel

    # ② 体重：低频下滑（落跟之后 30–50 ms 才压到底）
    bd = min(dur, 0.13)
    body = thump(bd, hi * 0.85, lo, decay=bd * 0.75, noise_amt=0.16, curve=2.0)
    off = int(SR * 0.012)
    end = min(n, off + len(body))
    if end > off:
        out[off:end] += body[:end - off] * 0.85

    # ③ 擦地：鞋底材质
    sd = float(RNG.uniform(0.06, 0.11))
    if sole == "leather":
        sc = bp(noise(sd), 1800, 7000) * env_ad(sd, 0.006, sd, 2.2)   # 皮底：亮、硬
    elif sole == "slipper":
        sc = bp(noise(sd), 700, 3500) * env_ad(sd, 0.01, sd, 1.8)     # 拖鞋：闷、拖
    else:
        sc = bp(noise(sd), 1200, 6000) * env_ad(sd, 0.004, sd, 2.6)   # 运动鞋：橡胶"吱"
    off = int(SR * 0.022)
    end = min(n, off + len(sc))
    if end > off:
        out[off:end] += sc[:end - off] * scuff
    return out


def footstep_track(dur: float, offsets: list[float], gains: list[float] | None = None,
                   lo: float = 110.0, hi: float = 900.0, sole: str = "shoe",
                   lp_cut: float = 9000.0) -> np.ndarray:
    """把一串 footstep 按给定时间点摆好（时间点由调用方决定，便于做"犹豫/渐近"等节奏）"""
    if gains is None:
        gains = [1.0] * len(offsets)
    out = np.zeros(int(SR * dur))
    for off, g in zip(offsets, gains):
        st = footstep(lo=lo, hi=hi, sole=sole)
        i = int(SR * off)
        end = min(len(out), i + len(st))
        if i >= len(out) or end <= i:
            continue
        out[i:end] += st[:end - i] * g
    return lp(out, lp_cut)


def se_脚步声(dur=2.2):
    """
    普通脚步：走廊里匀速走近的 6 步。
    ★ 重做原因：上一版 = steps() 里 click()×2，缺「落跟」这个宽带瞬态，
      听感是"闷响"而不是"脚步"。且 interval 用 spread 均分 → 太机械。
    """
    # 6 步，间隔 ~0.42s（约 143 步/分，正常走路），带 8% 抖动
    step_iv = 0.42
    offs, gs = [], []
    tt = 0.06
    for i in range(6):
        tt += step_iv * float(RNG.uniform(0.92, 1.08))
        if tt > dur - 0.2:
            break
        offs.append(tt)
        gs.append(float(RNG.uniform(0.82, 1.0)))   # 左右脚力度不同
    return footstep_track(dur, offs, gs, lo=110, hi=880, sole="shoe")


def se_风声(dur=4.0):
    """
    风声：**穿过树冠/窗缝的风**。
    ★ 重做原因（主人：不符合意象）—— 上一版是「brown 噪声 × 慢调制 + 高音哨」，
      问题在于**频带固定不动**。真实的风不是"音量在变的噪声"：
      它是「阵风」——每次来风，**滤带的中心频率会滑动**（起风时偏暗，风紧时偏亮），
      而且阵风之间有明显强弱起伏（不是平滑正弦）。
    ⇒ 现在：whoosh() 做时变频带 + 两层不同周期的阵风包络（不对称、更像天气）。
    """
    n = int(SR * dur)
    # 主体：低频"呼"的时变带通
    body = whoosh(dur, 90.0, 1400.0, sweep=(0.2, 0.95))
    body = lp(body, 2600)
    # 阵风包络：两个不同周期叠加（0.23Hz 主阵 + 0.07Hz 长起伏），并抬高到非负
    g1 = 0.55 + 0.45 * np.sin(2 * np.pi * 0.23 * t(dur) + 0.4)
    g2 = 0.70 + 0.30 * np.sin(2 * np.pi * 0.067 * t(dur) + 2.1)
    gust = np.clip(g1 * g2, 0.05, 1.0)
    body = body * gust
    # 树冠摩擦：中高频的"沙沙"，只在风紧时出现（跟随同一个包络，但阈值更高）
    leaf_rub = bp(noise(dur), 1600, 7000) * np.clip(gust - 0.45, 0, 1) * 0.5
    leaf_rub *= np.clip(0.5 + 0.5 * np.sin(2 * np.pi * 1.7 * t(dur)), 0, 1)
    # 缝隙哨音：很轻，只在高潮处露一点
    whistle = bp(noise(dur), 1900, 4200) * np.clip(gust - 0.7, 0, 1) * 0.22
    out = mix(body, leaf_rub, whistle)
    return stereo(hp(out, 80), 0.55)


def se_书页轻响(dur=0.7):
    """
    书页轻响：**一页纸被翻起的"唰"**。
    ★ 重做原因：上一版只有一段噪声包络 ⇒ 没有"纸"的材质。
      纸的声音特征：宽频但**中高频有清晰的摩擦带**，且**开头有一声极短的"起振"**
      （纸边离开手指/空气突然被扰动），然后才是"唰"。
    """
    out = np.zeros(int(SR * dur))
    # 起振：极短的宽带瞬态（纸边弹起）
    onset = bp(noise(0.006), 1800, 11000) * env_ad(0.006, 0.0003, 0.006, 2.5)
    out[:len(onset)] += onset * 0.5
    # 主体"唰"：纸面摩擦 —— 时变带通（从亮扫到暗），带纸的谐振
    body = whoosh(0.42, 1200.0, 9000.0, sweep=(0.95, 0.25))
    body = resonator(body, [1600.0, 3000.0, 5800.0], q=4.0,
                     gains=[1.0, 0.75, 0.45])
    body *= env_ad(0.42, 0.012, 0.40, 2.2)
    out[SR // 100:SR // 100 + len(body)] += body[:len(out) - SR // 100] * 0.85
    return hp(out, 500) * 0.9


def whistle(dur: float, f: float = 3200.0, pea: bool = True,
            attack: float = 0.012, decay: float = 0.55,
            vibrato: float = 0.055, vib_rate: float = 38.0) -> np.ndarray:
    """
    哨子的物理模型 —— 这是"哨声"的核心，之前的版本太粗糙（两个正弦 + 噪声）。
    真实哨子发声 = **边缘音（edge tone）**：气流冲过哨口棱边，
      产生一个基频，同时**强烈激励腔体共振**（哨腔的管共振）。
    所以频谱不是"一个正弦 + 一个泛音"，而是：
      基频 + 一串**腔体共振峰**（近似奇次谐波，但会略微失谐）
      + 气流的宽带噪声（吹气声）
    再叠 pea（豌豆哨）的滚珠颤动 —— 这会**大幅调制音量**（不是纯频率颤音），
    这正是体育老师那种哨子"哇噜噜"的声音来源。
    """
    n = int(SR * dur)
    tt = np.arange(n) / SR
    # ① 边缘音基频（带滚珠造成的音高抖动）
    if pea:
        # 滚珠在腔里高速旋转 —— 频率 + 幅度都被调制
        fp = 1.0 + vibrato * np.sin(2 * np.pi * vib_rate * tt)
        amp = 0.65 + 0.35 * (0.5 + 0.5 * np.sin(2 * np.pi * vib_rate * 1.15 * tt + 1.0))
    else:
        fp = 1.0 + 0.012 * np.sin(2 * np.pi * vib_rate * 0.4 * tt)
        amp = np.ones(n)
    # 相位＝频率的累积积分（必须用 cumsum，不能 f*t，否则会破音）
    phase = 2 * np.pi * np.cumsum(f * fp) / SR
    core = np.sin(phase)
    # ② 腔体共振峰：近似奇次谐波，略微失谐（真实哨腔不是完美整数倍）
    for k, (mult, g) in enumerate([(2.98, 0.55), (5.05, 0.30), (7.2, 0.16), (9.4, 0.08)]):
        core += np.sin(phase * mult + 0.3 * k) * g
    core *= amp
    # ③ 吹气噪声（宽带，被同一包络整形）
    breath = bp(noise(dur), 2500, 9000) * 0.22
    seg = mix(core, breath)
    # ④ 包络：起振很快（气流建立），收尾是"气不够了"的下落
    seg *= env_ad(dur, attack, dur * decay, 1.3)
    return bp(seg, 1900, min(11000, SR * 0.44)) * 0.9


def se_哨声风(dur=2.2):
    """
    哨声（远）＋风 —— 操场大风体育课的场景合成体。

    ★ 2026-09-18：原有两个几乎同源的键「哨声、风」与「哨声、风声」
      （实测两者谱重心只差 14 Hz，主人听感判定为重复），已**合并到本键**。
      合并后需同时覆盖台本两处场景（行 902 `操场_晴天_大风`、行 2357 `操场_大风`），
      两处都是「风大、白灰被卷起来」⇒ 取原来偏大的那一档风量（0.45）。
    """
    return mix(se_哨声长(2.2) * 0.72, se_风声(2.2)[:, 0] * 0.45)


def se_广播站音乐(dur=5.0):
    """广播站音乐（远处）：闷在墙后的旋律 —— 低通 + 带通"喇叭"染色 + 低频嗡"""
    notes = [392, 440, 523.25, 587.33, 523.25, 440, 392, 349.23]  # G4 A4 C5 D5...
    step_dur = dur / len(notes)
    mel = np.zeros(int(SR * dur))
    for i, f in enumerate(notes):
        seg = np.zeros(int(SR * step_dur))
        env = env_ad(step_dur, 0.02, step_dur * 0.9, 1.6)
        for h, amp in [(1, 1.0), (2, 0.32), (3, 0.14)]:
            seg += np.sin(2 * np.pi * f * h * np.arange(len(env)) / SR) * amp * env
        i0 = int(SR * i * step_dur)
        mel[i0:i0 + len(seg)] += seg
    # "喇叭"染色：中频共振 + 切掉高频（远处 + 隔墙）
    mel = bp(mel, 300, 2600)
    mel = lp(mel, 2200)
    hum = sine(dur, 100.0, 0.6) * 0.05  # 电源嗡
    return (mel * 0.85 + hum) * env_ar(dur, 0.25, 0.6)


def se_翻书声(dur=0.6):
    """
    翻书声：**整本书的纸页一起被翻**（比"书页轻响"更大、更厚、更顿）。
    ★ 与"书页轻响"的区分必须做出来：
      书页轻响 = 单手捻起**一页**，轻、脆、快
      翻书声   = 整本翻过去，**多页同时摩擦**、更闷、更"厚"、带一点"啪"的落定
    """
    out = np.zeros(int(SR * dur))
    onset = bp(noise(0.01), 1200, 8000) * env_ad(0.01, 0.0005, 0.01, 2.2)
    out[:len(onset)] += onset * 0.7
    layers = []
    for lo, hi, g in [(600, 4000, 1.0), (1100, 6500, 0.8), (1800, 9000, 0.55)]:
        seg = whoosh(0.36, lo, hi, sweep=(0.9, 0.3))
        seg = resonator(seg, [lo * 1.8, hi * 0.55], q=3.5, gains=[1.0, 0.6])
        layers.append(seg * g)
    body = mix(*layers) * env_ad(0.36, 0.015, 0.34, 2.0)
    off = SR // 80
    end = min(len(out), off + len(body))
    out[off:end] += body[:end - off] * 0.9
    pd = 0.05
    settle = bp(noise(pd), 900, 5000) * env_ad(pd, 0.001, pd, 3.0)
    os_ = int(SR * 0.42)
    end = min(len(out), os_ + len(settle))
    if end > os_:
        out[os_:end] += settle[:end - os_] * 0.45
    return lp(hp(out, 400), 11000) * 0.92


def se_自动贩卖机(dur=2.6):
    """自动贩卖机：投币（叮）→ 机械滚动 → 落货（咚）"""
    out = np.zeros(int(SR * dur))
    # 投币：两个高频叮
    coin = bell(0.5, 2093.0, [(1.0, 0.22), (2.02, 0.14)]) * 0.5
    out[:len(coin)] += coin
    i = int(SR * 0.22)
    out[i:i + len(coin)] += coin * 0.8
    # 机械滚动：宽频噪声，起伏
    i = int(SR * 0.55)
    roll_d = 1.5
    roll = bp(noise(roll_d, "pink"), 200, 2400)
    roll *= (0.6 + 0.4 * np.sin(2 * np.pi * 7 * t(roll_d)))
    out[i:i + len(roll)] += roll * 0.35
    # 落货：低频闷响 + 金属余韵
    i = int(SR * 2.0)
    drop = mix(click(0.5, 60, 500) * 0.9,
               bell(0.5, 320.0, [(1.0, 0.18), (2.4, 0.1)]) * 0.3)
    end = min(len(out), i + len(drop))
    out[i:end] += drop[:end - i]
    return lp(out, 7000)


def se_踩落叶(dur=3.2):
    """
    踩过落叶的脚步声（由远及近）：**每一步 = 脚步 + 叶子碎裂**。
    ★ 重做原因：上一版用 click(0.16,150,700) 当局步（缺落跟瞬态），
      叶子用一段带通噪声——两者都偏"闷响/沙沙"。
      现在用真正的 footstep() + 每步叠加 8–14 声极短的叶子脆裂。
    """
    n = int(SR * dur)
    out = np.zeros(n)
    count = 9
    for i in range(count):
        p = i / (count - 1)
        tt = 0.12 + (dur - 1.0) * (p ** 1.05) + float(RNG.uniform(-0.04, 0.04))
        g = 0.22 + 0.78 * p                            # 渐近 → 渐响
        # ① 脚本身（落跟瞬态）
        st = footstep(lo=110, hi=850, sole="shoe")
        j = int(SR * tt)
        end = min(n, j + len(st))
        if j < n and end > j:
            out[j:end] += st[:end - j] * g * 0.85
        # ② 叶子碎裂：踩下去那一刻，脚下 12–20 片叶子碎响
        #    ★ 叶子脆响要能"听得见"：上一版 8–14 声 × 0.7 增益，
        #      实测高频（2k–8k）只占 3.4%，几乎被脚步的低频淹掉。
        #      ⇒ 提高数量与增益，并把叶子的谐振整体上移（干叶更脆更亮）。
        nleaf = int(RNG.integers(12, 21))
        ev = [(float(RNG.uniform(0.0, 0.20)), float(RNG.uniform(0.45, 1.0)))
              for _ in range(nleaf)]
        lf = bursts(0.24, ev, 3200, 14000, curve=3.4, decay=0.018)
        lf = resonator(lf, [3800.0, 6200.0, 9800.0], q=6.0,
                       gains=[1.0, 0.75, 0.5])
        j2 = int(SR * (tt - 0.03))
        end2 = min(n, j2 + len(lf))
        if j2 < n and end2 > j2:
            out[j2:end2] += lf[:end2 - j2] * g * 1.15
    return stereo(lp(out, 12000), 0.35)


def se_午休预备铃(dur=2.6):
    """午休结束预备铃：比上课预备铃更"柔"一点的两连音"""
    out = np.zeros(int(SR * dur))
    for k, off in enumerate([0.0, 0.55]):
        seg = bell(1.4, 659.25, [(1.0, 0.62), (2.0, 0.4), (2.8, 0.26)]) * 0.9
        seg += bell(1.4, 523.25, [(1.0, 0.5), (2.02, 0.3)]) * 0.45
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        out[i:end] += seg[:end - i] * (1.0 - k * 0.1)
    return lp(out, 5500)


def se_糖纸轻响(dur=1.1):
    """
    糖纸轻响：**捏皱玻璃纸**。
    ★ 重做原因（主人：「都成沙锤摇晃的声音了」）——
      上一版是「连续高频噪声 × 快调幅」，调幅本质上就是**摇晃沙锤**的物理模型。
      真实捏糖纸是**一连串离散的褶皱声**：手一捏，薄膜在不同位置先后屈服，
      每一下都是极短的「啪/嚓」，间隔不均。
    ⇒ 骨架改为 bursts（离散事件），材质由 resonator 给（塑料膜的几个谐振峰）。
    """
    # 捏的动作分 3 簇（手指第一次收紧、调整、再收紧），簇内 6–10 下
    ev = []
    for c_off, c_n in [(0.02, 7), (0.34, 9), (0.66, 6)]:
        ev += scatter(c_n, dur, lo_t=c_off, hi_t=min(dur - 0.02, c_off + 0.22),
                      gmin=0.4, gmax=1.0)
    raw = bursts(dur, ev, 1800, 11000, curve=3.2)
    # 塑料膜的谐振 → 从「噪声」变成「有材质的薄膜声」
    body = resonator(raw, [2600.0, 4300.0, 6800.0, 9200.0], q=7.0,
                     gains=[1.0, 0.8, 0.55, 0.3])
    # 一点点高频空气感（纯谐振会太「干」）
    air = bp(raw, 6000, 13000) * 0.25
    out = mix(body, air) * env_ar(dur, 0.008, 0.12)
    return lp(out, 14000) * 0.92



def se_发令哨(dur=1.1):
    """
    发令哨：**两声急促的短哨**（体育课发令）。
    ★ 重做原因：上一版哨音模型不成立（正弦加泛音，没有腔体共振），听感偏"电子哔"。
      现在用 whistle() 的完整模型；发令哨的特点是**音更高、更短、更用力**（气压大）。
    """
    out = np.zeros(int(SR * dur))
    for k, off in enumerate([0.0, 0.30]):
        d2 = 0.36
        seg = whistle(d2, f=3450.0, pea=True, attack=0.006, decay=0.62,
                      vibrato=0.07, vib_rate=44.0)
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        if end > i:
            out[i:end] += seg[:end - i] * (1.0 - k * 0.18)
    return out


def se_心跳(dur=2.4):
    """
    心跳：咚-咚（lub-dub），**贴近胸腔听**的心跳。
    ★ 重做原因：上一版是「低通噪声 + 52/78Hz 稳态正弦」，听感像电子鼓。
      真实心音的物理：**瓣膜关闭**造成的冲击在胸腔里共振。
      lub（第一心音）比 dub 更低、更长、更"闷"；dub 更短更脆、音高略高。
      关键：每次都是**音高下坠**的短促冲击（用 thump），而不是稳态正弦。
    ★ 节奏：3 组完整 lub-dub，最后一组收在 2.04s（留出 0.36s 余韵，
      避免上一版第 3 组压在 2.4s 边界被切掉、只能数到 5 次冲击）。
    """
    out = np.zeros(int(SR * dur))
    # (起始, 增益, 基频高, 基频低, 衰减) —— lub 低长，dub 高短
    beats = [
        (0.00, 1.00, 78.0, 42.0, 0.16),   # lub
        (0.30, 0.70, 92.0, 55.0, 0.11),   # dub
        (0.92, 0.94, 76.0, 41.0, 0.16),   # 第二次 lub
        (1.22, 0.66, 90.0, 54.0, 0.11),   # 第二次 dub
        (1.76, 0.88, 77.0, 41.5, 0.15),   # 第三次（轻微渐弱，呼吸感）
        (2.02, 0.60, 91.0, 54.0, 0.10),
    ]
    for off, g, f_hi, f_lo, dec in beats:
        d2 = dec + 0.12
        h = thump(d2, f_hi, f_lo, decay=dec, noise_amt=0.28, curve=2.4)
        # 胸腔共鸣：叠一层很低的共振，让它"在里面"
        h = resonator(h, [55.0, 88.0], q=3.0, gains=[0.7, 0.45]) + h * 0.8
        i = int(SR * off)
        end = min(len(out), i + len(h))
        if end > i:
            out[i:end] += h[:end - i] * g
    # 心跳是低沉的：砍掉 600Hz 以上（隔着一层肉听到的效果）
    return lp(out, 600) * 0.95


def se_椅子后撞(dur=0.9):
    """
    椅子向后一撞：**刮地 → 撞上 → 金属腿余震**。
    ★ 重做原因：上一版刮地是固定带通噪声，没有"刮擦"的时变感；
      撞击用 click + bell，偏低频，不够"椅子的金属管"。
      现在：刮地 = whoosh（带域滑动，像腿在地面拖动）+ 椅子是空心金属管 → 强共振。
    """
    n = int(SR * dur)
    out = np.zeros(n)
    # ① 刮地：椅子腿在地面拖（时变带通，扫过去）
    sc = whoosh(0.32, 400.0, 5000.0, sweep=(0.25, 0.95))
    sc = resonator(sc, [1300.0, 2600.0, 4200.0], q=5.0, gains=[1.0, 0.6, 0.35])
    sc *= env_ad(0.32, 0.02, 0.30, 1.9)
    out[:len(sc)] += sc * 0.55
    # ② 撞：空心金属管 —— 强、非谐、衰减快
    i = int(SR * 0.30)
    d2 = 0.55
    hit = thump(d2, 320.0, 105.0, decay=0.16, noise_amt=0.3, curve=2.0) * 0.9
    # ★ 必须用 mix()：thump(0.67s) 与 bell(0.55s) 与 30ms 冲击长度都不同，
    #   直接 `+` 会 numpy 广播报错（2026-09-18 当场复现过一次）。
    hit = mix(
        hit,
        bell(d2, 480.0, [(1.0, 0.22), (2.42, 0.15), (3.86, 0.09), (6.1, 0.05)]) * 0.42,
        bp(noise(0.03), 1200, 7000) * env_ad(0.03, 0.0005, 0.03, 2.2) * 0.45,
    )
    end = min(n, i + len(hit))
    if end > i:
        out[i:end] += hit[:end - i]
    # ③ 撞后余震：椅子晃两下
    for off, g in [(0.50, 0.16), (0.62, 0.08)]:
        r = lp(noise(0.1), 700) * env_ad(0.1, 0.004, 0.09, 2.4)
        j = int(SR * off)
        end = min(n, j + len(r))
        if end > j:
            out[j:end] += r[:end - j] * g
    return lp(out, 8000) * 0.95


def se_周围议论(dur=4.0):
    """
    周围的议论声：**压低了声音、在议论你**——比"教室嘈杂"更近、更暗、更有压迫感。
    ★ 同样是共振峰缺失问题。与教室嘈杂的差异必须做出来，否则两个键听起来一样：
      · f0 更集中（人群在压低嗓子议论，音高偏低、范围窄）
      · 共振峰整体下移、Q 更高（"捂着嘴说话"的感觉）
      · 密度更低但每段更清楚（能听出「一句一句」，只是听不清内容）
      · 高频更少（不是吵，是"窃窃"）
    """
    out = np.zeros(int(SR * dur))
    MURMUR = [
        [(500, 80), (900, 90), (2300, 120)],     # 压低后的 /a/
        [(400, 70), (780, 80), (2150, 120)],     # 压低后的 /o/
        [(340, 70), (1900, 100), (2600, 130)],   # 压低后的 /i/
    ]
    for _ in range(11):
        f0 = float(RNG.uniform(95, 165))          # 比教室更低的音高带
        d2 = float(RNG.uniform(0.34, 0.75))
        voice = voice_blip(d2, f0, MURMUR[int(RNG.integers(0, len(MURMUR)))],
                           syll_n=int(RNG.integers(2, 5)))
        voice = lp(bp(voice, 110, 2400), 2600)     # 砍掉高频 → "窃窃"而非"嚷嚷"
        i = int(RNG.uniform(0, max(0.01, dur - d2)) * SR)
        g = float(RNG.uniform(0.55, 1.0))
        end = min(len(out), i + len(voice))
        if end > i:
            out[i:end] += voice[:end - i] * g
    # 底噪更薄更暗（不是满屋子，是"几个人在旁边"）
    mur = lp(bp(noise(dur, "pink"), 150, 1800), 2200)
    am = np.clip(0.6 + 0.4 * np.sin(2 * np.pi * 0.85 * t(dur) + 2.2), 0, 1)
    out += mur * am * 0.3
    out = hp(out, 95) * env_ar(dur, 0.35, 0.75)
    return stereo(out, 0.4)


def se_水声(dur=2.4):
    """
    水声：中庭水龙头，**水柱打在池底的持续声**。
    ★ 重做原因：上一版用「正弦上滑音」做气泡 —— 那是"电子哔"不是气泡。
      真实水声有两个成分：
      ① 水幕：宽带噪声，但**高频占主导**（水的特征是"嘶——"的亮噪声）
      ② 气泡：千万个微小气泡在水中溃灭，每一下是**极短的高频瞬态**（不是滑音）
         —— 密度极高（每秒几十到几百个），叠起来才是那种"咕嘟/沙沙"的水感。
    """
    n = int(SR * dur)
    sheet = bp(noise(dur), 800, 11000) * 0.55
    sheet += bp(noise(dur), 2000, 9000) * 0.4
    sheet *= np.clip(0.85 + 0.15 * np.sin(2 * np.pi * 3.3 * t(dur) + 0.7), 0, 1)
    nevents = int(dur * 90)
    ev = scatter(nevents, dur, gmin=0.08, gmax=0.5)
    bubbles = bursts(dur, ev, 3000, 14000, curve=4.0, decay=0.006)
    bubbles = bp(bubbles, 2500, 14000) * 0.85
    pool = lp(noise(dur), 300) * 0.12 * np.clip(0.6 + 0.4 * np.sin(2 * np.pi * 1.9 * t(dur)), 0, 1)
    out = mix(sheet, bubbles, pool)
    out = hp(out, 350) * env_ar(dur, 0.06, 0.2)
    return stereo(out, 0.35)


def se_一阵风(dur=2.0):
    """
    一阵风：**明确的"来—去"**，不是一个稳定风场。
    ★ 与"风声"必须区分：风声是持续的、可以垫在整场戏底下；
      一阵风是**事件**——快速涨起来，扫过去，然后落下去。
    ⇒ 包络用 AR（不是正弦起伏），峰值出现在 0.35 处，尾部迅速衰减。
    """
    n = int(SR * dur)
    body = whoosh(dur, 100.0, 1600.0, sweep=(0.3, 0.85))
    e = np.zeros(n)
    pi_ = int(n * 0.35)
    e[:pi_] = (np.linspace(0, 1, pi_) ** 0.75)
    tail = np.linspace(0, 1, n - pi_)
    e[pi_:] = (1 - tail) ** 0.85
    body = body * e
    grit = bp(noise(dur), 2200, 8000) * (e ** 1.6) * 0.4
    grit *= np.clip(0.4 + 0.6 * np.sin(2 * np.pi * 3.1 * t(dur)), 0, 1)
    out = mix(lp(body, 3000), grit)
    return stereo(hp(out, 85), 0.5)


def se_脚步犹豫(dur=2.8):
    """脚步（犹豫・拖沓）：间隔不规则、步与步之间拖长（鞋底摩擦）"""
    out = np.zeros(int(SR * dur))
    positions = [0.1, 0.62, 1.05, 1.72, 2.1]
    for i, p in enumerate(positions):
        p += RNG.uniform(-0.06, 0.06)
        idx = int(SR * p)
        g = 0.55 + 0.45 * RNG.random()
        thud = click(0.18, 140, 800) * 0.55 * g
        scuff = bp(noise(0.22), 1800, 6500) * env_ad(0.22, 0.02, 0.2, 1.6) * 0.4 * g
        seg = mix(thud, scuff)
        end = min(len(out), idx + len(seg))
        out[idx:end] += seg[:end - idx]
    return lp(out, 6500)


def se_风卷枯叶(dur=2.8):
    """
    风卷枯叶：**风把干叶子卷起来、在地上来回擦**。
    ★ 重做原因：上一版是「风底 + 高频噪声调幅」，调幅 → 又是"摇晃感"。
      真实的风卷枯叶 = 连续的**离散擦响**（叶子一片片被吹动、互相碰撞），
      密度和响度随风势起伏。
    """
    g = (0.35 + 0.65 * np.clip(np.sin(2 * np.pi * 0.55 * t(dur) + 0.3), 0, 1) ** 1.2)
    wind = bp(noise(dur, "brown"), 100, 1300) * g * 0.4
    ev = []
    for _ in range(46):
        ts = float(RNG.uniform(0.05, dur - 0.05))
        gg = float(np.interp(ts, np.linspace(0, dur, len(g)), g))
        if RNG.random() < gg:
            ev.append((ts, float(RNG.uniform(0.3, 1.0)) * gg))
    leaves = bursts(dur, ev, 2600, 12000, curve=3.5)
    leaves = resonator(leaves, [3000.0, 5200.0, 8000.0], q=5.0,
                       gains=[1.0, 0.7, 0.4]) * 0.75
    out = mix(lp(wind, 4000), leaves)
    return stereo(lp(out, 13000), 0.5)


def se_闷响(dur=1.0):
    """
    闷响：**隔着东西的低频撞击**（撞到墙 / 桌子 / 人体）。
    ★ 重做原因：上一版 = 低通噪声 + 62/95Hz 稳态正弦，像"电子底鼓"。
      闷响的核心是：**能量几乎全在低频，且没有任何高频瞬态**（被挡掉了），
      同时因为是"隔着东西"，起始也不脆（没有落跟那种宽带尖）。
    """
    d2 = min(dur, 0.6)
    out = thump(d2, 110.0, 48.0, decay=0.30, noise_amt=0.5, curve=1.8)
    # 加一层"墙的共振"（低频窄带，让它是"某样东西"而不是纯气流）
    out = resonator(out, [72.0, 118.0], q=4.0, gains=[0.8, 0.5]) + out * 0.7
    out = lp(out, 320) * env_ad(len(out) / SR, 0.004, len(out) / SR * 0.85, 1.6)
    if len(out) < int(SR * dur):
        out = np.concatenate([out, np.zeros(int(SR * dur) - len(out))])
    return out * 0.95


def se_台灯开关(dur=0.35):
    """台灯开关：清脆的一声"嗒"—— 机械开关的短促冲击"""
    out = mix(click(0.09, 1200, 7000) * 0.85,
              click(0.05, 2500, 9000) * 0.5,
              sine(0.12, 2200.0) * env_ad(0.12, 0.002, 0.08, 3.5) * 0.25)
    pad = np.zeros(int(SR * dur))
    pad[:len(out)] = out
    return pad


def se_脚步由远及近(dur=2.8):
    """脚步（由远及近）：音量与高频同步增长"""
    out = np.zeros(int(SR * dur))
    count = 7
    for i in range(count):
        p = i / (count - 1)
        base = 0.12 + (dur - 0.8) * p ** 1.2
        idx = int(SR * (base + RNG.uniform(-0.04, 0.04)))
        g = 0.2 + 0.8 * p
        thud = click(0.17, 150, 900) * 0.6
        scuff = bp(noise(0.09), 2000, 7000) * env_ad(0.09, 0.005, 0.07, 3.0) * 0.35
        seg = mix(thud, scuff) * g
        end = min(len(out), idx + len(seg))
        out[idx:end] += seg[:end - idx]
    return lp(out, 7000)


def se_脚步声远去(dur=2.6):
    """脚步声远去：音量和低频同步衰减（越远越闷）"""
    out = np.zeros(int(SR * dur))
    count = 7
    for i in range(count):
        p = i / (count - 1)
        idx = int(SR * (0.1 + (dur - 0.7) * p + RNG.uniform(-0.03, 0.03)))
        g = 1.0 - 0.8 * p
        thud = click(0.17, 150, 900) * 0.6
        scuff = bp(noise(0.08), 2200, 7500) * env_ad(0.08, 0.005, 0.06, 3.0) * 0.3 * (1 - 0.9 * p)
        seg = mix(thud, scuff) * g
        end = min(len(out), idx + len(seg))
        out[idx:end] += seg[:end - idx]
    return lp(out, 7000)  # 恒定低通，衰减交给增益


def se_哨声(dur=1.0):
    """哨声（体育课 · 单声）：一声利落的哨"""
    return whistle(dur, f=3100.0, pea=True, attack=0.01, decay=0.72)


def se_放学铃(dur=4.9):
    """
    放学铃：**威斯敏斯特钟声**（Westminster Chimes，大本钟那串）—— 一天收尾的钟。

    ★ 2026-09-18 主人令：「放学铃来点不一样的音阶呗？」。
      旧版是**同一个音（880Hz）敲四遍**，只有节奏没有旋律 ⇒ 听不出"放学了"。
      威斯敏斯特动机是四个**不同音高**的钟音，落回主音，天生带"结束/收束"感：
          标准 Westminster Quarters 取 E 大调四个音：E4 → D#4 → F#4 → E4
          （导音 D#4 制造"未落定"的张力，最后落回主音 E4 ＝ 收束）
      音高换算（十二平均律，A4=440）：
          E4 = 329.63 Hz   D#4 = 311.13 Hz   F#4 = 369.99 Hz

    ★★ 间隔的取值是实测调出来的（两个极端都试过）：
      · 0.62s（第一版）⇒ **音阶糊成一片**：钟声余韵比间隔长，下一个音在前一个
        还没衰减完就进来，谱峰互相遮盖（复算时第 4 音窗口测出的还是第 3 音的 F#4）。
      · 1.55s（第二版）⇒ 四音清晰，但整段 7.0s 对放学过场太长。
      · **1.10s（本版，总长 4.9s）⇒ 复算四音峰为 329/310/369/329Hz，全部命中**，
        且长度回到可接受范围。钟体衰减同步缩到 1.8s 以匹配更密的间隔。
    """
    GAP = 1.10
    DEC = 1.8
    # 威斯敏斯特四音（E 大调动机，落回主音 E4）
    MELODY = [
        (329.63, 0.00),        # E4   主音
        (311.13, GAP),         # D#4  导音（下方半音，产生"未落定"的张力）
        (369.99, 2 * GAP),     # F#4  上中音
        (329.63, 3 * GAP),     # E4   落回主音 —— 收束
    ]
    out = np.zeros(int(SR * dur))
    for k, (f0, off) in enumerate(MELODY):
        # 主钟：非谐泛音（真实钟声泛音不是整数倍），各泛音独立衰减
        seg_main = bell(DEC, f0, [(1.0, DEC * 0.65), (2.0, DEC * 0.44),
                                  (2.76, DEC * 0.28), (4.07, DEC * 0.16),
                                  (5.43, DEC * 0.09)])
        # 低八度衬底：给钟"体量"，单靠基频会像电子铃
        seg_low = bell(DEC, f0 / 2, [(1.0, DEC * 0.78), (2.01, DEC * 0.35)]) * 0.34
        # 高泛音闪光（钟口的高频"叮"），让音头有金属质感
        seg_shine = bell(0.8, f0 * 2, [(1.0, 0.18), (2.04, 0.09)]) * 0.28
        # ⚠️ 三段的 dur 不同 ⇒ 必须走 mix() 按最长对齐补零，
        #    直接 `+` 会 numpy 广播失败（2026-09-18 又踩一次）。
        seg = mix(seg_main, seg_low, seg_shine)
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        # 尾音略衰减，避免四音完全一样死板（真人敲钟力度有差）
        out[i:end] += seg[:end - i] * (1.0 - k * 0.045)
    # 钟声要"温"，砍掉 7k 以上的齿音；同时留一点空气感
    return lp(out, 7000)


def se_午休结束铃(dur=3.4):
    """午休结束铃：三连音，中速（介于预备铃与放学铃之间）"""
    out = np.zeros(int(SR * dur))
    for k, off in enumerate([0.0, 0.6, 1.2]):
        seg = bell(1.6, 698.46, [(1.0, 0.7), (2.0, 0.45), (2.76, 0.3)]) * 0.95
        seg += bell(1.6, 932.33, [(1.0, 0.5), (2.01, 0.3)]) * 0.4
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        out[i:end] += seg[:end - i] * (1.0 - k * 0.1)
    return lp(out, 5800)


def se_粉笔(dur=1.6):
    """
    粉笔：**在黑板上写字**——连续、有节奏、中高频"吱吱+嗒嗒"。
    ★ 重做原因：上一版是 6 下随机 click + 一段摩擦底噪声，听不出"写字"，不符意象。
      粉笔写字的特征：
      ① 一串**短促的划擦**（每个笔画一段），不是一整段连续噪声
      ② 频带集中在中高频 2–6 kHz，且**有明显颗粒感**（粉笔在粗糙板面上跳）
      ③ 偶尔有一两下更亮的"吱"（用力处）
    """
    out = np.zeros(int(SR * dur))
    tt = 0.05
    while tt < dur - 0.2:
        seg_d = float(RNG.uniform(0.10, 0.28))
        seg = bp(noise(seg_d), 1500, 7000)
        # 颗粒感：粉笔在板面纹理上跳动 → 8–20 Hz 的不规则调幅
        gran = np.ones(int(SR * seg_d))
        for f, d in [(9.0, 0.35), (14.0, 0.25), (21.0, 0.18)]:
            gran += d * np.sin(2 * np.pi * f * np.arange(len(gran)) / SR + RNG.uniform(0, 6.28))
        gran = np.clip(gran / np.max(np.abs(gran)), 0, 1)
        seg = seg * gran * env_ad(seg_d, 0.008, seg_d * 0.85, 2.0)
        seg = resonator(seg, [2200.0, 3800.0, 5600.0], q=5.0,
                        gains=[1.0, 0.7, 0.4])
        j = int(SR * tt)
        end = min(len(out), j + len(seg))
        if end > j:
            out[j:end] += seg[:end - j] * float(RNG.uniform(0.6, 1.0))
        # 偶尔一声更亮的"吱"
        if RNG.random() < 0.3:
            zd = 0.06
            z = bp(noise(zd), 3500, 9500) * env_ad(zd, 0.002, zd, 2.6)
            j2 = int(SR * (tt + seg_d * 0.6))
            end2 = min(len(out), j2 + len(z))
            if end2 > j2:
                out[j2:end2] += z[:end2 - j2] * 0.4
        tt += seg_d + float(RNG.uniform(0.04, 0.16))     # 笔画之间的停顿
    return hp(lp(out, 12000), 700) * 0.9


def se_提示音(dur=1.2):
    """提示音：手机短信提示 —— 两个上行纯音（亲切不刺耳）"""
    out = np.zeros(int(SR * dur))
    for k, (f, off) in enumerate([(880.0, 0.0), (1174.7, 0.16)]):
        seg = np.sin(2 * np.pi * f * t(0.6)) * env_ad(0.6, 0.006, 0.5, 1.8)
        seg += np.sin(2 * np.pi * f * 2 * t(0.6)) * 0.2 * env_ad(0.6, 0.006, 0.4, 2.2)
        i = int(SR * off)
        end = min(len(out), i + len(seg))
        out[i:end] += seg[:end - i] * 0.85
    return lp(out, 8000)


def se_哨声长(dur=1.6):
    """长哨（内部复用：给"哨声、风"用）—— 吹得更久、更稳"""
    return whistle(dur, f=3000.0, pea=True, attack=0.014, decay=0.5)


# 合成配方表：台本键名 → 生成函数
RECIPES: dict[str, callable] = {
    "上课预备铃": se_铃声预备,
    "教室嘈杂": se_教室嘈杂,
    "脚步声": se_脚步声,
    "风声": se_风声,
    "书页轻响": se_书页轻响,
    "哨声、风": se_哨声风,
    "广播站音乐（远处）": se_广播站音乐,
    "翻书声": se_翻书声,
    "自动贩卖机": se_自动贩卖机,
    "踩过落叶的脚步声（由远及近）": se_踩落叶,
    "午休结束的预备铃": se_午休预备铃,
    "糖纸轻响": se_糖纸轻响,
    "发令哨": se_发令哨,
    "心跳": se_心跳,
    "椅子向后一撞": se_椅子后撞,
    "周围的议论声": se_周围议论,
    "水声": se_水声,
    "一阵风": se_一阵风,
    "脚步（犹豫・拖沓）": se_脚步犹豫,
    "风卷枯叶": se_风卷枯叶,
    "闷响": se_闷响,
    "台灯开关": se_台灯开关,
    "脚步（由远及近）": se_脚步由远及近,
    "脚步声远去": se_脚步声远去,
    "哨声": se_哨声,
    "放学铃": se_放学铃,
    "午休结束铃": se_午休结束铃,
    "粉笔": se_粉笔,
    "提示音": se_提示音,
}


# ============================================================
# 渲染 / 导出
# ============================================================


def render(key: str) -> np.ndarray:
    fn = RECIPES.get(key)
    if fn is None:
        raise KeyError(f"没有配方：{key}")
    x = fn()
    x = np.asarray(x, dtype=np.float64)
    # ★ 硬校验：出现 NaN/Inf 立即失败，绝不产出坏文件。
    #   典型成因：对可能为负的数组开分数次幂（np.sin(x) ** 1.5）、除零。
    if not np.isfinite(x).all():
        bad = int((~np.isfinite(x)).sum())
        raise ValueError(f"波形含 {bad} 个 NaN/Inf —— 检查是否有『负数开分数次幂』或除零")
    if x.ndim == 2:
        x = np.stack([fade_edges(x[:, i]) for i in range(x.shape[1])], axis=1)
    else:
        x = fade_edges(x)
    x = normalize(x, -3.0)
    return x.astype(np.float32)


def safe_name(key: str) -> str:
    """台本键 → 安全文件名（去非法字符，保留中文）"""
    s = re.sub(r'[\\/:*?"<>|]', "_", key)
    return s.replace("（", "(").replace("）", ")").replace("・", "_")


def find_ffmpeg() -> str | None:
    for c in (os.environ.get("FFMPEG"), r"D:\ffmpeg\bin\ffmpeg.exe",
              r"C:\ffmpeg\bin\ffmpeg.exe"):
        if c and Path(c).exists():
            return c
    import shutil as sh
    return sh.which("ffmpeg")


def to_ogg(wav: Path, ogg: Path) -> bool:
    ff = find_ffmpeg()
    if not ff:
        print("  ✗ 找不到 ffmpeg，跳过 ogg 转码", file=sys.stderr)
        return False
    ogg.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav),
                    "-c:a", "libvorbis", "-q:a", "5", "-ar", "44100", "-ac", "1",
                    str(ogg)], check=True)
    return True


def load_manifest() -> dict:
    if not MANIFEST.exists():
        print(f"✗ 找不到 manifest：{MANIFEST}（先跑 npm run compile && npm run manifest）",
              file=sys.stderr)
        raise SystemExit(2)
    return json.loads(MANIFEST.read_text(encoding="utf-8")).get("se", {})


def main() -> int:
    ap = argparse.ArgumentParser(description="《笑樱》音效合成器")
    ap.add_argument("--list", action="store_true", help="列出配方与状态")
    ap.add_argument("--only", nargs="+", metavar="KEY", help="只出指定的键")
    ap.add_argument("--all", action="store_true", help="全部生成")
    ap.add_argument("--no-ogg", action="store_true", help="只出 wav 母带，不转 ogg")
    ap.add_argument("--preview-dir", help="额外输出到该目录（试听用）")
    args = ap.parse_args()

    se = load_manifest()
    if args.list:
        print(f"manifest se 键：{len(se)} 个 ｜ 配方：{len(RECIPES)} 个\n")
        for k, ent in se.items():
            hit = "✅" if k in RECIPES else "❌ 无配方"
            exists = (DST / Path(ent["file"]).name).exists()
            print(f"  {hit}  {k:26s} → {ent['file']:22s} "
                  f"{'已存在' if exists else '待生成'}  用 {ent.get('count', 0)} 次")
        missing = [k for k in se if k not in RECIPES]
        extra = [k for k in RECIPES if k not in se]
        if missing:
            print(f"\n  ⚠ manifest 有但无配方：{missing}")
        if extra:
            print(f"\n  ⚠ 有配方但 manifest 没有：{extra}")
        return 0

    keys = list(se.keys()) if args.all else (args.only or [])
    if not keys:
        ap.print_help()
        return 1

    MASTER_DIR.mkdir(parents=True, exist_ok=True)
    ok = err = 0
    print(f"输出母带：{MASTER_DIR}")
    print(f"输出引擎：{DST}\n")
    for k in keys:
        ent = se.get(k)
        if ent is None:
            print(f"  ✗ {k} —— manifest 里没有这个键")
            err += 1
            continue
        try:
            x = render(k)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {k} —— 合成失败：{e}")
            err += 1
            continue
        wav = MASTER_DIR / f"se_{safe_name(k)}.wav"
        sf.write(str(wav), x, SR, subtype="PCM_16")
        dur = len(x) / SR
        peak = float(np.max(np.abs(x)))
        tag = f"{dur:5.2f}s  峰值 {20 * np.log10(peak + 1e-12):5.1f} dBFS"
        line = f"  ✓ {k:26s} → {wav.name:32s} {tag}"
        if not args.no_ogg:
            ogg = DST / Path(ent["file"]).name
            if to_ogg(wav, ogg):
                line += f"  → {ogg.name}"
        print(line)
        if args.preview_dir:
            pv = Path(args.preview_dir)
            pv.mkdir(parents=True, exist_ok=True)
            sf.write(str(pv / f"{safe_name(k)}.wav"), x, SR, subtype="PCM_16")
        ok += 1

    print(f"\n合计：成功 {ok} / 失败 {err}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
