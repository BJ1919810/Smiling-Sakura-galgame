# -*- coding: utf-8 -*-
"""本地 BS-Roformer 人声分离 + faster-whisper ASR（笑樱项目侧）。

⚠️ 本脚本**只读** Movie-trans 工程：以 sys.path 引入它的 uvr5 代码、
   从它的 uvr5_weights 读权重。**不写入、不修改那边的任何文件。**

环境（实测）
  · 解释器：D:\\0Coding\\Python312\\python.exe（Movie-trans 的原生环境）
    torch 2.6.0+cu126 / librosa / soundfile / faster_whisper / funasr 全齐
  · GPU：本机 RTX 3070 8GB，算力 8.6

坑（都已处理）
  1. torch 2.6+ 的 `torch.load` 默认 weights_only=True，
     而 .ckpt 里是完整 pickle 对象 → 必须 patch 成 False，否则反序列化直接抛错。
  2. roformer 的 Attend 走 `F.scaled_dot_product_attention`（不是 flash_attn 包），
     torch >= 2.0 都支持，本机 2.6.0 没问题。
  3. 必须带 .yaml。不带会走 get_default_config()，其 num_overlap=2，
     而官方 yaml 是 4 —— 重叠越多拼接缝越平滑，是实打实的质量差异。
  4. 本地产物落到 .workbuddy/out/（不进 public/，不进 git）。

用法：
    D:\\0Coding\\Python312\\python.exe tools/local_voice_process.py denoise \
        --input <音频> --out-dir <目录>
    D:\\0Coding\\Python312\\python.exe tools/local_voice_process.py asr \
        --input <音频> --language ja --out <json>
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback

PROJ = r"D:\0Coding\pythonworkspace\My_program\笑樱-galgame"
MT = r"D:\0Coding\pythonworkspace\My_program\Movie-trans"
UVR5 = os.path.join(MT, "uvr5")
WEIGHTS = os.path.join(UVR5, "uvr5_weights")
MODEL = "model_bs_roformer_ep_317_sdr_12.9755"


def _patch_torch_load():
    """torch 2.6+ 默认 weights_only=True，.ckpt 是完整 pickle → 必须关掉。"""
    import torch
    if getattr(_patch_torch_load, "_done", False):
        return
    orig = torch.load

    def patched(f, *a, **kw):
        kw["weights_only"] = False
        return orig(f, *a, **kw)

    torch.load = patched
    _patch_torch_load._done = True


def do_denoise(args):
    _patch_torch_load()
    sys.path.insert(0, UVR5)
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from bsroformer import Roformer_Loader

    if not torch.cuda.is_available():
        sys.exit("[错误] 本机 CUDA 不可用")
    print(f">>> torch {torch.__version__}  {torch.cuda.get_device_name(0)}", flush=True)

    ckpt = os.path.join(WEIGHTS, MODEL + ".ckpt")
    cfg = os.path.join(WEIGHTS, MODEL + ".yaml")
    for p in (ckpt, cfg):
        if not os.path.isfile(p):
            sys.exit(f"[错误] 缺文件：{p}")

    out = os.path.abspath(args.out_dir)
    os.makedirs(out, exist_ok=True)
    tv = os.path.join(out, "_raw_v")
    to = os.path.join(out, "_raw_o")
    os.makedirs(tv, exist_ok=True)
    os.makedirs(to, exist_ok=True)

    t0 = time.perf_counter()
    print(f">>> 加载模型 half={not args.fp32}", flush=True)
    loader = Roformer_Loader(model_path=ckpt, config_path=cfg, device="cuda",
                             is_half=not args.fp32)
    c = loader.config
    print(f">>> 就绪 {time.perf_counter()-t0:.1f}s  type={loader.model_type} "
          f"num_overlap={c['inference']['num_overlap']} "
          f"batch={c['inference']['batch_size']} sr={c['audio']['sample_rate']}", flush=True)
    print(f">>> target={c['training']['target_instrument']} "
          f"instruments={c['training']['instruments']}", flush=True)

    inp = os.path.abspath(args.input)
    t1 = time.perf_counter()
    print(f">>> 分离 {inp}", flush=True)
    loader._path_audio_(inp, to, tv, "wav")
    print(f">>> 分离耗时 {time.perf_counter()-t1:.1f}s", flush=True)

    base = os.path.splitext(os.path.basename(inp))[0]
    tgt = c["training"]["target_instrument"]
    oth = [i for i in c["training"]["instruments"] if i != tgt]
    pv = os.path.join(tv, f"{base}_{tgt}.wav")
    pi = os.path.join(to, f"{base}_{oth[0]}.wav")
    if not os.path.isfile(pv):
        sys.exit(f"[错误] 无产出：{pv}\n目录：{os.listdir(tv)}")

    v44 = os.path.join(out, "vocal_1_44100.wav")
    shutil.copy2(pv, v44)
    print(f"[输出] {v44}", flush=True)
    if os.path.isfile(pi):
        bg = os.path.join(out, "bg_1_44100.wav")
        shutil.copy2(pi, bg)
        print(f"[输出] {bg}", flush=True)

    y, sr = librosa.load(pv, sr=None, mono=True)
    y16 = librosa.resample(y, orig_sr=sr, target_sr=16000)
    v16 = os.path.join(out, "vocal_1_16000.wav")
    sf.write(v16, y16, 16000, subtype="PCM_16")
    print(f"[输出] {v16}", flush=True)

    db = lambda v: 20 * np.log10(max(float(v), 1e-10))
    mix, _ = librosa.load(inp, sr=44100, mono=True)
    v, _ = librosa.load(v44, sr=44100, mono=True)
    print(f">>> 电平 mix {db(np.sqrt((mix**2).mean())):.1f} -> "
          f"vocal {db(np.sqrt((v**2).mean())):.1f} dBFS", flush=True)
    print(f">>> 峰值 vocal {db(np.abs(v).max()):.1f} dBFS", flush=True)

    for d in (tv, to):
        shutil.rmtree(d, ignore_errors=True)
    print(">>> 完成", flush=True)


def do_asr(args):
    import numpy as np  # noqa: F401

    # ⚠️ 权重已在本地 HF 缓存里（asr/models），用 download_root 指过去，
    #    但先试 local_files_only 免得它去连 huggingface.co（本机可能也连不上）
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    model_dir = args.model_dir or os.path.join(MT, "asr", "models")

    from faster_whisper import WhisperModel
    import torch

    cuda = torch.cuda.is_available()
    device = "cuda" if (cuda and args.device == "cuda") else "cpu"
    ct = args.compute_type or ("float16" if device == "cuda" else "int8")

    lang = None if args.language in ("auto", "", "none") else args.language

    print(f">>> faster-whisper {args.model_size} device={device} compute={ct}", flush=True)
    print(f">>> download_root={model_dir}", flush=True)
    t0 = time.perf_counter()
    try:
        model = WhisperModel(args.model_size, device=device, compute_type=ct,
                             download_root=model_dir, local_files_only=True)
    except Exception as e:
        print(f"!! 本地缓存未命中（{type(e).__name__}），改为允许下载：{e}", flush=True)
        model = WhisperModel(args.model_size, device=device, compute_type=ct,
                             download_root=model_dir)
    print(f">>> 就绪 {time.perf_counter()-t0:.1f}s", flush=True)

    inp = os.path.abspath(args.input)
    t1 = time.perf_counter()
    segments, info = model.transcribe(
        audio=inp, beam_size=5, vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=700),
        language=lang, word_timestamps=False,
    )
    segs, text = [], ""
    for s in segments:
        segs.append({
            "start": round(s.start, 3), "end": round(s.end, 3),
            "text": s.text.strip(),
            "avg_logprob": round(getattr(s, "avg_logprob", 0.0), 4),
            "no_speech_prob": round(getattr(s, "no_speech_prob", 0.0), 4),
        })
        text += s.text
    dt = time.perf_counter() - t1

    print(f">>> 完成 {dt:.1f}s  detected={info.language} "
          f"(p={getattr(info,'language_probability',0):.2f})  段数={len(segs)}", flush=True)
    for s in segs:
        print(f"    [{s['start']:>7.2f} → {s['end']:>7.2f}] {s['text']}", flush=True)
    print(f">>> 全文：{text.strip()}", flush=True)

    res = {
        "input": inp, "requested_language": args.language,
        "detected_language": info.language,
        "language_probability": round(float(getattr(info, "language_probability", 0)), 4),
        "duration": round(float(info.duration), 3) if getattr(info, "duration", None) else None,
        "elapsed_s": round(dt, 2), "model": args.model_size,
        "device": device, "compute_type": ct,
        "text": text.strip(), "segments": segs, "clips": {},
    }

    if args.clip:
        import subprocess
        import tempfile
        for spec in args.clip:
            parts = spec.split(":")
            if len(parts) != 3:
                print(f"[跳过] 格式应为 名称:起点:终点 —— {spec}", flush=True)
                continue
            name, a, b = parts[0], float(parts[1]), float(parts[2])
            fd, tmp = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", inp,
                                "-ss", str(a), "-to", str(b),
                                "-ac", "1", "-ar", "16000", tmp], check=True)
                cs, ci = model.transcribe(audio=tmp, beam_size=5, vad_filter=True,
                                          vad_parameters=dict(min_silence_duration_ms=700),
                                          language=lang)
                ct2, cseg = "", []
                for s in cs:
                    cseg.append({"start": round(s.start, 3), "end": round(s.end, 3),
                                 "text": s.text.strip()})
                    ct2 += s.text
                print(f">>> 片段 {name} [{a}-{b}]：{ct2.strip()}", flush=True)
                res["clips"][name] = {"start": a, "end": b,
                                      "text": ct2.strip(), "segments": cseg}
            except Exception as e:  # noqa: BLE001
                print(f"[片段 {name} 失败] {type(e).__name__}: {e}", flush=True)
                res["clips"][name] = {"start": a, "end": b,
                                      "error": f"{type(e).__name__}: {e}"}
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f">>> 写入 {args.out}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("denoise")
    d.add_argument("--input", required=True)
    d.add_argument("--out-dir", required=True)
    d.add_argument("--fp32", action="store_true")
    d.set_defaults(func=do_denoise)

    a = sub.add_parser("asr")
    a.add_argument("--input", required=True)
    a.add_argument("--language", default="ja")
    a.add_argument("--model-size", default="large-v3")
    a.add_argument("--model-dir", default=None)
    a.add_argument("--out", required=True)
    a.add_argument("--device", default="cuda")
    a.add_argument("--compute-type", default=None)
    a.add_argument("--clip", action="append", default=[])
    a.set_defaults(func=do_asr)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[异常] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
