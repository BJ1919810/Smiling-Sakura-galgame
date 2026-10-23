# -*- coding: utf-8 -*-
"""IndexTTS-2.5 批量语音合成（云卡侧执行）。

设计要点（都来自源码，见 .workbuddy/memory 记录）：
  · 8 维情绪向量顺序固定：高兴/愤怒/悲伤/恐惧/反感/低落/惊讶/自然
  · `normalize_emo_vec` **只有官方 webui 调**，`infer()` 内部不调 →
    本脚本显式调用，与官方 WebUI 行为一致（偏置 + 总和压到 0.8）
  · 一旦给 emo_vector，`emo_audio_prompt` 会被强制清空（情绪参考音频与向量互斥）
  · 模型只加载一次，逐条合成（避免每条都重新载权重）

用法：
    uv run python indextts-gen.py --jobs jobs.jsonl --out-dir out \
        --ref /root/autodl-tmp/index-tts-ref/sakura_ref.wav

⚠️ 与 GPT-SoVITS 的区别：IndexTTS-2.5 **没有 `prompt_text` / `prompt_lang`**，
   参考音频是纯 zero-shot（源码里 grep 不到这两个参数）。参考音频上限 15 s（`infer_v2_5.py:627`）。
   `lang` 合法值：zh / zhen / en / ja（`infer_v2_5.py:705`）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EMO_ORDER = ["高兴", "愤怒", "悲伤", "恐惧", "反感", "低落", "惊讶", "自然"]


def to_vec(emo: dict) -> list[float]:
    """把「中文键 → 强度」的 dict 转成固定顺序的 8 维向量。"""
    unknown = [k for k in emo if k not in EMO_ORDER]
    if unknown:
        raise ValueError(f"未知情绪键 {unknown}，合法键：{EMO_ORDER}")
    return [float(emo.get(k, 0.0)) for k in EMO_ORDER]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="/root/autodl-tmp/index-tts-2.5")
    ap.add_argument("--ref", required=True, help="参考音频（服务端绝对路径，≤15s，不配套参考文本）")
    ap.add_argument("--jobs", required=True, help="JSONL：{id,text,emo,emo_alpha,lang,speed}")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--bf16", action="store_true", help="bf16 推理（省显存）")
    ap.add_argument("--qwen-emo", action="store_true", help="加载 QwenEmotion（我们用向量，默认不加载）")
    ap.add_argument("--warmup", action="store_true", default=True)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    print(f">>> 加载模型 model_dir={args.model_dir} bf16={args.bf16}", flush=True)
    t0 = time.perf_counter()
    from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

    tts = IndexTTS2(
        cfg_path=os.path.join(args.model_dir, "config.yaml"),
        model_dir=args.model_dir,
        use_bf16=args.bf16,
        use_qwen_emo=args.qwen_emo,
    )
    print(f">>> 模型就绪 {time.perf_counter() - t0:.1f}s device={tts.device} "
          f"low_vram={getattr(tts, 'low_vram', None)}", flush=True)

    jobs = []
    with open(args.jobs, encoding="utf-8") as f:
        for ln, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw or raw.startswith("//"):
                continue
            try:
                j = json.loads(raw)
            except json.JSONDecodeError as e:
                print(f"[跳过] 第 {ln} 行不是合法 JSON：{e}", flush=True)
                continue
            jobs.append(j)
    print(f">>> 共 {len(jobs)} 条待合成", flush=True)

    # 预热：首条含 CUDA 图编译，不预热会误判性能
    if args.warmup and jobs:
        w = jobs[0]
        print(">>> 预热（首条含 CUDA 图编译，慢是正常的）", flush=True)
        t = time.perf_counter()
        try:
            tts.infer(
                spk_audio_prompt=args.ref, text=w["text"],
                output_path=os.path.join(args.out_dir, "_warmup.wav"),
                lang=w.get("lang", "zh"),
                emo_vector=tts.normalize_emo_vec(to_vec(w.get("emo", {})), apply_bias=True),
            )
            print(f">>> 预热完成 {time.perf_counter() - t:.1f}s", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f">>> 预热失败（继续跑正式批次）：{type(e).__name__}: {e}", flush=True)

    results = []
    for n, j in enumerate(jobs, 1):
        jid = str(j.get("id", f"line{n}"))
        out = os.path.join(args.out_dir, f"{jid}.wav")
        raw_vec = to_vec(j.get("emo", {}))
        vec = tts.normalize_emo_vec(raw_vec, apply_bias=True)
        t = time.perf_counter()
        try:
            tts.infer(
                spk_audio_prompt=args.ref,
                text=j["text"],
                output_path=out,
                lang=j.get("lang", "zh"),
                emo_vector=vec,
                emo_alpha=float(j.get("emo_alpha", 1.0)),
                duration_factor=float(j.get("speed", 1.0)),
            )
            dt = time.perf_counter() - t
            size = os.path.getsize(out) if os.path.exists(out) else 0
            print(f"[{n}/{len(jobs)}] {jid}  原始{raw_vec} -> 归一{['%.4f' % v for v in vec]}"
                  f"  {dt:.1f}s  {size}B", flush=True)
            results.append({"id": jid, "ok": True, "seconds": round(dt, 2),
                            "raw_vec": raw_vec, "vec": vec})
        except Exception as e:  # noqa: BLE001
            print(f"[{n}/{len(jobs)}] {jid}  失败：{type(e).__name__}: {e}", flush=True)
            results.append({"id": jid, "ok": False, "error": f"{type(e).__name__}: {e}"})

    with open(os.path.join(args.out_dir, "_report.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    ok = sum(1 for r in results if r["ok"])
    print(f">>> 完成 {ok}/{len(results)} 条，输出目录 {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
