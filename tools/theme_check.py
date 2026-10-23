#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""主题一致性检测 —— 变奏到底有没有偏离主题？

背景：即梦不支持指定种子，变奏靠文字各写一版时**主题容易越飘越远**。
真正的主题身份证是「动机文字 + 织体四件套 + 调性关系」；本工具负责最后一步：
**把「像不像」变成可测的数字**，并指出**在哪一段最不像**。

用法::

    P="C:/Users/zbj/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

    # 变奏出来后，跟主题比一比
    "$P" tools/theme_check.py check \
        --theme 素材基准/音频/BGM/bgm_樱_主题_完整.wav \
        --test  素材基准/音频/BGM/bgm_樱_主题_低.ogg \
        --out   素材基准/音频/BGM/_校验_樱_主题_低.png

判定（阈值已用合成样本标定，见下）::

    >= 0.85    放行（同一主题）
    0.78-0.85  可疑（能听出是一家，但主题印象变淡 → 重抽一版择优）
    < 0.78     跑偏（动机丢了 → 重出，或改走「从母带 DSP 派生」）

★ 标定（2026-09-15 用 bgm_樱_主题_完整 做的合成样本，工具自带 --self 会打印自比基准）::

    主题 vs 自己              1.000   （标尺）
    主题 vs 自己（降八度）     0.940   音区质心 -41% → 这就是「变奏（低）」的形态
    主题 vs 自己（升八度×2速） 0.906   
    主题 vs 自己（升高2半音）  0.598   调性指纹 0.076 ← 调性一漂，指纹立刻塌
    主题 vs 白噪声            0.581   调性指纹 -0.106 ← **无关内容的天然地板 ≈0.58**

  → 所以 0.85 这条线离地板有 0.27 的余量，是有效阈值；而**调性指纹最灵敏**（0.97 vs 0.08），
    先看它——低于 0.85 基本就是调性/音阶漂了，不用再看别的。

测得的东西：
  · 和声-旋律走向相似度（chroma + DTW，对速度/时长差异免疫）
  · 调性指纹（全曲平均 chroma 的余弦 —— 同调/关系小调会很高）
  · 音区（谱质心中位数 —— 验「低」变奏是不是真的低）
  · 速度（起始点自相关估 BPM —— 验「淡」变奏是不是真的慢）
  · 逐段相似度曲线（指出哪一段最不像）

⚠️ 它测的是**和声＋旋律的合体走向**，不区分"右手旋律/左手伴奏"。所以它能抓
   「调性漂了」「主题动机被换掉了」，但抓不到「旋律还在、只是伴奏换了织体」这种细差。
   **它是筛子，不是替代耳朵**——曲线最低点那一段，还是自己听一遍最准。

⚠️ chroma 必须先「按音级 bin 数归一化」**再**「逐帧去均值」，两步缺一不可：
   不去均值 → 白噪 chroma 恒为 1/√12 的平坦向量，跟任何音乐余弦都 >0.96，指标直接失效（实测 0.98）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np
import soundfile as sf
from scipy.signal import get_window, resample_poly

SR = 22050
N_FFT = 4096
HOP = 4096                      # chroma 帧率 ≈ 5.4 fps
FMIN, FMAX = 55.0, 2000.0       # A1 ~ B6，覆盖钢琴主要音域
ONSET_HOP = 512                 # 估速度用的细粒度

PASS, MAYBE = 0.85, 0.78


# ---------------------------------------------------------------- 载入 / 切片

def parse_time(s) -> float:
    """'30' / '0:30' / '1:02.5' → 秒。"""
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if ":" in s:
        parts = s.split(":")
        t = 0.0
        for p in parts:
            t = t * 60.0 + float(p)
        return t
    return float(s)


def load_mono(path: str, sr: int = SR) -> np.ndarray:
    y, osr = sf.read(path, always_2d=True, dtype="float32")
    y = y.mean(axis=1)
    if osr != sr:
        g = math.gcd(int(osr), int(sr))
        y = resample_poly(y, sr // g, osr // g).astype(np.float32)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0:
        y = y / peak * 0.95
    return y.astype(np.float32)


# ---------------------------------------------------------------- 特征

def chroma(y: np.ndarray, sr: int = SR) -> np.ndarray:
    """12 维色度谱（每帧 L2 归一化）。对音色/响度差异不敏感，专注和声-旋律走向。"""
    win = get_window("hann", N_FFT, fftbins=True).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    mask = (freqs >= FMIN) & (freqs <= FMAX)
    fk = freqs[mask]
    pc = 12.0 * np.log2(np.maximum(fk, 1e-6) / 440.0) % 12.0
    lo = np.floor(pc).astype(int) % 12
    frac = pc - np.floor(pc)
    W = np.zeros((fk.size, 12), dtype=np.float32)
    idx = np.arange(fk.size)
    np.add.at(W, (idx, lo), (1.0 - frac).astype(np.float32))
    np.add.at(W, (idx, (lo + 1) % 12), frac.astype(np.float32))
    # ★★ 必须按 bin 数把每个音级归一化。FFT 分到 12 个音级的 bin 数量天生不均匀
    #   （低频一个半音只占几个 bin、高频占几十个），不归一化时白噪声的 chroma 会变成
    #   一条固定的「bin 密度曲线」——音乐里也含同一条曲线 → 两者天然高相关（实测白噪 0.98）。
    W /= np.maximum(W.sum(axis=0, keepdims=True), 1e-9)

    n = 1 + max(0, len(y) - N_FFT) // HOP
    C = np.zeros((max(n, 1), 12), dtype=np.float32)
    for i in range(C.shape[0]):
        seg = y[i * HOP: i * HOP + N_FFT]
        if seg.size < N_FFT:
            seg = np.pad(seg, (0, N_FFT - seg.size))
        mag = np.abs(np.fft.rfft(seg * win))
        mag = np.log1p(mag * 10.0)
        C[i] = mag[mask] @ W
    # ★★ 再减掉「12 个音级普遍存在」的直流分量。实测不去均值时，白噪声的 chroma 恒等于
    #   1/√12＝0.2887 的平坦向量，与**任何**音乐向量的余弦都 >0.96 → 检测器完全失效。
    #   去均值后白噪残差与音乐几乎不相关，指标才有分辨率。
    C -= C.mean(axis=1, keepdims=True)
    C /= (np.linalg.norm(C, axis=1, keepdims=True) + 1e-9)
    return C


def spectral_centroid(y: np.ndarray, sr: int = SR) -> float:
    """谱质心中位数（Hz）—— 判断音区高低。"""
    win = get_window("hann", N_FFT, fftbins=True).astype(np.float32)
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / sr)
    band = (freqs >= 50.0) & (freqs <= 5000.0)
    fk = freqs[band]
    vals = []
    n = 1 + max(0, len(y) - N_FFT) // HOP
    for i in range(max(n, 1)):
        seg = y[i * HOP: i * HOP + N_FFT]
        if seg.size < N_FFT:
            seg = np.pad(seg, (0, N_FFT - seg.size))
        mag = np.abs(np.fft.rfft(seg * win))[band]
        s = float(mag.sum())
        if s > 1e-6:
            vals.append(float((fk * mag).sum() / s))
    return float(np.median(vals)) if vals else 0.0


def estimate_bpm(y: np.ndarray, sr: int = SR) -> float:
    """起始点包络自相关估 BPM（在 45~200 范围取峰）。"""
    win = get_window("hann", N_FFT, fftbins=True).astype(np.float32)
    n = 1 + max(0, len(y) - N_FFT) // ONSET_HOP
    if n < 8:
        return 0.0
    env = np.zeros(n, dtype=np.float32)
    prev = None
    for i in range(n):
        seg = y[i * ONSET_HOP: i * ONSET_HOP + N_FFT]
        if seg.size < N_FFT:
            seg = np.pad(seg, (0, N_FFT - seg.size))
        mag = np.abs(np.fft.rfft(seg * win))
        if prev is not None:
            env[i] = float(np.maximum(mag - prev, 0.0).sum())
        prev = mag
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    fps = sr / float(ONSET_HOP)
    lo = int(fps * 60.0 / 200.0)
    hi = min(int(fps * 60.0 / 45.0), len(ac) - 1)
    if hi <= lo:
        return 0.0
    lag = lo + int(np.argmax(ac[lo:hi + 1]))
    return 60.0 * fps / float(lag) if lag else 0.0


# ---------------------------------------------------------------- DTW

def dtw_align(D: np.ndarray):
    """返回 (归一化平均代价, 路径)。D 为代价矩阵（1 - 余弦相似度）。"""
    n1, n2 = D.shape
    INF = 1e18
    acc = np.full((n1 + 1, n2 + 1), INF, dtype=np.float64)
    bp = np.zeros((n1 + 1, n2 + 1), dtype=np.uint8)
    acc[0, 0] = 0.0
    for i in range(1, n1 + 1):
        prow, crow = acc[i - 1], acc[i]
        drow = D[i - 1]
        for j in range(1, n2 + 1):
            a, b, c = prow[j], prow[j - 1], crow[j - 1]
            if a <= b and a <= c:
                crow[j] = a + drow[j - 1]; bp[i, j] = 0
            elif b <= c:
                crow[j] = b + drow[j - 1]; bp[i, j] = 1
            else:
                crow[j] = c + drow[j - 1]; bp[i, j] = 2
    total = float(acc[n1, n2])
    i, j = n1, n2
    path = []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        k = bp[i, j]
        if k == 0:
            i -= 1; j -= 1
        elif k == 1:
            i -= 1
        else:
            j -= 1
    path.reverse()
    return total / max(len(path), 1), path


def rolling_similarity(D: np.ndarray, path, n1: int, win: int = 25) -> np.ndarray:
    """把路径上的代价摊回主题时间轴（每帧平均），再滑动平滑。"""
    acc = np.zeros(n1); cnt = np.zeros(n1)
    for i, j in path:
        acc[i] += D[i, j]; cnt[i] += 1
    curve = np.where(cnt > 0, 1.0 - acc / np.maximum(cnt, 1), np.nan)
    if not np.any(~np.isnan(curve)):
        return np.ones(n1)
    filled = np.nan_to_num(curve, nan=float(np.nanmean(curve)))
    # ★ 必须先 edge padding 再卷积，否则 np.convolve('same') 的隐式零填充会把首尾拉低
    pad = win // 2
    ext = np.pad(filled, (pad, pad), mode="edge")
    return np.convolve(ext, np.ones(win) / win, mode="same")[pad:pad + n1]


# ---------------------------------------------------------------- 报告 / 图

def _fmt_clock(t: float) -> str:
    return "%d:%04.1f" % (int(t) // 60, t % 60)


def _bar(v: float, lo: float = 0.4, hi: float = 1.0, w: int = 22) -> str:
    x = int(round((v - lo) / (hi - lo) * w))
    x = max(0, min(w, x))
    return "#" * x + "." * (w - x)


def draw_png(curve: np.ndarray, dur: float, title: str, out: str, overall: float) -> bool:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    W, H = 1280, 440
    L, R, T, B = 78, 34, 62, 58
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d = ImageDraw.Draw(img)

    def font(sz, bold=False):
        for p in ([r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"] if bold
                  else [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc"]):
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, sz)
                except Exception:
                    pass
        return ImageFont.load_default()

    f_t, f_a, f_s = font(20, True), font(14), font(13)

    def X(t): return L + (W - L - R) * (t / dur if dur > 0 else 0)
    def Y(v): return T + (H - T - B) * (1.0 - (v - 0.4) / 0.6)

    # 网格
    for v in (0.5, 0.6, 0.7, 0.78, 0.85, 0.9, 1.0):
        y = Y(v)
        dashed = v in (PASS, MAYBE)
        col = "#D9534F" if v == PASS else ("#E8A33D" if v == MAYBE else "#E8E8EE")
        if dashed:
            x = L
            while x < W - R:
                d.line([(x, y), (min(x + 9, W - R), y)], fill=col, width=2); x += 16
            d.text((W - R + 6, y - 8), "%.2f" % v, font=f_s, fill=col)
        else:
            d.line([(L, y), (W - R, y)], fill=col, width=1)
        d.text((L - 46, y - 8), "%.2f" % v, font=f_s, fill="#8A8A99")

    x = L
    while x <= W - R:
        t = (x - L) / (W - L - R) * dur
        d.line([(x, T), (x, H - B)], fill="#F2F2F6", width=1)
        d.text((x - 14, H - B + 8), _fmt_clock(t), font=f_s, fill="#8A8A99")
        x += (W - L - R) / max(int(dur / 30.0), 1)

    pts = [(X(i / max(len(curve) - 1, 1) * dur), Y(float(v))) for i, v in enumerate(curve)]
    if len(pts) > 1:
        d.line(pts, fill="#2F6BFF", width=3, joint="curve")

    # 最低点标记
    imin = int(np.argmin(curve))
    tmin = imin / max(len(curve) - 1, 1) * dur
    d.ellipse([X(tmin) - 5, Y(float(curve[imin])) - 5, X(tmin) + 5, Y(float(curve[imin])) + 5],
              fill="#D9534F", outline="#FFFFFF")
    d.text((X(tmin) + 9, Y(float(curve[imin])) - 22),
           "最低 %s (%.2f)" % (_fmt_clock(tmin), curve[imin]), font=f_s, fill="#D9534F")

    d.text((L, 18), title, font=f_t, fill="#1A1A22")
    d.text((L, 40), "全程相似度曲线（DTW 对齐后）· 综合 %.3f · 红线 0.85 放行 / 橙线 0.78 可疑"
           % overall, font=f_a, fill="#6A6A78")
    d.line([(L, H - B), (W - R, H - B)], fill="#C9C9D2", width=2)
    d.line([(L, T), (L, H - B)], fill="#C9C9D2", width=2)
    img.save(out)
    return True


# ---------------------------------------------------------------- check

def cmd_check(args) -> int:
    th = args.theme
    if os.path.isdir(th):
        cands = sorted(f for f in os.listdir(th)
                       if f.lower().endswith((".ogg", ".wav", ".mp3", ".flac")))
        if not cands:
            print("[x] 目录里没有音频：%s" % th); return 2
        by_name = {os.path.basename(t): t for t in args.test} if args.test else {}
        pick = by_name.get(cands[0], os.path.join(th, cands[0]))
        for c in cands:
            if "完整" in c and not by_name:
                pick = os.path.join(th, c)
        th = pick
        print("[i] --theme 是目录，选用：%s" % os.path.basename(th))

    tests = args.test or []
    if not tests:
        print("[x] 请用 --test 指定至少一个待检文件"); return 2

    print("主题基准：%s" % os.path.basename(th))
    yt = load_mono(th)
    Ct = chroma(yt)
    c_t = spectral_centroid(yt)
    b_t = estimate_bpm(yt)
    print("  主题时长 %.1f s · chroma %d 帧 · 音区质心 %.0f Hz · 估算 %s BPM"
          % (yt.size / SR, Ct.shape[0], c_t, ("%.0f" % b_t) if b_t else "—"))

    def cost_matrix(A, B):
        # 去均值后的 chroma 余弦相似度映射成代价；反向/不相关按 1 封顶
        return np.clip(1.0 - (A @ B.T), 0.0, 1.0)

    sim_self = 1.0 - dtw_align(cost_matrix(Ct, Ct))[0]
    print("  ★ 自比基准 %.3f（标尺，应 ≈1.000；明显低于 1 说明素材太短或近乎无声）" % sim_self)
    print()

    rows, worst = [], 0
    for t in tests:
        if not os.path.exists(t):
            print("[x] 找不到：%s" % t); return 2
        yv = load_mono(t)
        Cv = chroma(yv)
        D = cost_matrix(Ct, Cv)
        cost, path = dtw_align(D)
        sim = 1.0 - cost
        # 调性指纹
        pt = Ct.mean(axis=0); pt = pt / (np.linalg.norm(pt) + 1e-9)
        pv = Cv.mean(axis=0); pv = pv / (np.linalg.norm(pv) + 1e-9)
        key_sim = float(pt @ pv)

        # ★ 移调不变口径 —— 专治「故意降调的变奏」被误判跑偏（chroma 对音级敏感，一移调必然判低）。
        #   先用调性指纹在 12 个整体移调里挑出最可能的那个，再在该移调上量旋律相似度（只多跑 1 次 DTW）。
        best_k, best_key = 0, -9.0
        for k in range(12):
            pk = np.roll(pv, k, axis=0)
            pk = pk / (np.linalg.norm(pk) + 1e-9)
            s = float(pt @ pk)
            if s > best_key:
                best_key, best_k = s, k
        Cv_t = np.roll(Cv, best_k, axis=1) if best_k else Cv
        cost_t, _ = dtw_align(cost_matrix(Ct, Cv_t))
        sim_t = 1.0 - cost_t

        c_v = spectral_centroid(yv)
        b_v = estimate_bpm(yv)
        curve = rolling_similarity(D, path, Ct.shape[0])
        dur_t = yt.size / SR

        if sim >= PASS:
            verdict = "放行 ✓"
        elif sim_t >= PASS:
            verdict = "放行 ✓（整体移调 %+d 半音）" % best_k
        else:
            verdict = "可疑 ⚠ 重抽择优" if max(sim, sim_t) >= MAYBE else "跑偏 ✗ 重出"
        rows.append(dict(test=os.path.basename(t), similar=round(float(sim), 4),
                         similar_inv=round(float(sim_t), 4), shift=int(best_k),
                         key=round(key_sim, 4), centroid=round(c_v, 1), bpm=round(b_v, 1),
                         duration=round(yv.size / SR, 1), verdict=verdict))

        print("—" * 66)
        print("待检：%s   (%.1f s)" % (os.path.basename(t), yv.size / SR))
        print("  综合相似度 %.3f  %s   %s" % (sim, _bar(sim), verdict))
        print("  调性指纹   %.3f  %s   (同调/关系小调应 >0.85)" % (key_sim, _bar(key_sim)))
        print("  移调不变   %.3f  %s   (推定整体移调 %+d 半音；**故意降调的变奏以此为准**)"
              % (sim_t, _bar(sim_t), best_k))
        print("  音区质心   %.0f Hz vs 主题 %.0f Hz  → %+.0f%%  %s"
              % (c_v, c_t, (c_v - c_t) / max(c_t, 1e-6) * 100.0,
                 "低音区 ✓" if c_v < c_t * 0.9 else ("高音区" if c_v > c_t * 1.1 else "音区相近")))
        if b_v and b_t:
            print("  速度       %.0f vs %.0f BPM  → %+.0f%%  %s"
                  % (b_v, b_t, (b_v - b_t) / b_t * 100.0,
                     "更慢 ✓" if b_v < b_t * 0.92 else ("更快" if b_v > b_t * 1.08 else "速度相近")))
        imin = int(np.argmin(curve))
        print("  最不像的一段在 **主题 %s** 处（相似度 %.2f）"
              % (_fmt_clock(imin / max(len(curve) - 1, 1) * dur_t), curve[imin]))

        if args.out:
            out = args.out
            if len(tests) > 1:
                stem, ext = os.path.splitext(args.out)
                out = "%s_%s%s" % (stem, os.path.splitext(os.path.basename(t))[0], ext or ".png")
            ok = draw_png(curve, dur_t,
                          "主题一致性 · %s ← 对比 → %s" % (os.path.basename(th),
                                                       os.path.basename(t)),
                          out, float(sim))
            if ok:
                print("  曲线图 → %s" % out)
            else:
                print("  [!] 画图失败（缺 PIL 字体？），数值结果不受影响")
            worst = max(worst, 1 if not ok else 0)
        print()

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(dict(theme=os.path.basename(th), theme_bpm=b_t,
                           theme_centroid=c_t, results=rows), f,
                      ensure_ascii=False, indent=2)
        print("[ok] JSON → %s" % args.json)
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="主题一致性检测（变奏有没有偏离主题）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s2 = sub.add_parser("check", help="比一比变奏和主题像不像")
    s2.add_argument("--theme", required=True, help="主题基准（成曲文件或目录）")
    s2.add_argument("--test", nargs="*", default=[], help="一个或多个待检变奏")
    s2.add_argument("--out", default=None, help="逐段相似度曲线 PNG 输出路径")
    s2.add_argument("--json", default=None, help="结果另存 JSON")
    s2.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
