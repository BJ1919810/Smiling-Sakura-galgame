# -*- coding: utf-8 -*-
"""ASR 语音识别（云卡侧执行）——移植 Movie-trans 的 tools/asr.py 识别逻辑。

对齐 Movie-trans 的算法约定（tools/asr.py:139-179）
  · language == "zh"          → FunASR Paraformer（中文专用）
  · language == None          → faster-whisper 自动检测；若检测出中文则转 FunASR
  · language == 其它（如 ja）  → faster-whisper
  · whisper 参数：beam_size=5, vad_filter=True, min_silence_duration_ms=700
  · 默认模型 large-v3，GPU 上 float16

本脚本做的事（日语参考音频场景）
  1) 对整条音频识别，输出「带时间戳的分段」
  2) 额外输出一个纯文本（拼好的全文）
  3) 如果给了 --clip-start/--clip-end，再单独对这一段识别一次
     （用来给「樱2.wav」这种切片配精确译文）

输出 JSON：
  { "language": "ja", "duration": 12.3, "text": "...", "segments": [...], "clips": {...} }

用法：
    /root/miniconda3/envs/GPTSoVits/bin/python whisper-asr.py \
        --input /root/autodl-tmp/sakura-out/denoise/vocal_1_16000.wav \
        --language ja --out /root/autodl-tmp/sakura-out/asr.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# 云卡连不上 huggingface.co（实测 [Errno 101] Network is unreachable），
# 但 hf-mirror.com 可达（HTTP/2 200）。faster-whisper 首次要下 large-v3 权重，
# 必须把 endpoint 指到镜像。**要在 import huggingface_hub / faster_whisper 之前设**。
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--language", default="ja", help="ja / zh / en / auto")
    ap.add_argument("--model-size", default="large-v3")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default=None, help="默认 cuda=float16 / cpu=int8")
    ap.add_argument("--clip", action="append", default=[],
                    help="额外识别的片段，格式 名称:起点:终点（秒），可多次")
    ap.add_argument("--model-dir", default="/root/autodl-tmp/whisper-models",
                    help="faster-whisper 权重目录（本地没有会去下）")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"[错误] 输入不存在：{args.input}")

    import torch
    cuda = torch.cuda.is_available() and args.device == "cuda"
    compute_type = args.compute_type or ("float16" if cuda else "int8")
    device = "cuda" if cuda else "cpu"

    lang = None if args.language in ("auto", "", "none") else args.language

    print(f">>> 载入 faster-whisper {args.model_size} device={device} compute={compute_type}",
          flush=True)
    t0 = time.perf_counter()
    from faster_whisper import WhisperModel
    model = WhisperModel(
        args.model_size,
        device=device,
        compute_type=compute_type,
        download_root=args.model_dir,
    )
    print(f">>> 模型就绪 {time.perf_counter() - t0:.1f}s", flush=True)

    def transcribe(path: str, language):
        segments, info = model.transcribe(
            audio=path,
            beam_size=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=700),
            language=language,
            word_timestamps=False,
        )
        segs = []
        text = ""
        for s in segments:
            segs.append({
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "text": s.text.strip(),
                "avg_logprob": round(getattr(s, "avg_logprob", 0.0), 4),
                "no_speech_prob": round(getattr(s, "no_speech_prob", 0.0), 4),
            })
            text += s.text
        return text, segs, info

    t1 = time.perf_counter()
    text, segs, info = transcribe(args.input, lang)
    dt = time.perf_counter() - t1
    audio_dur = getattr(info, "duration", None)
    print(f">>> 识别完成 {dt:.1f}s  duration={audio_dur}  detected={info.language}"
          f"(p={getattr(info, 'language_probability', 0):.2f})  段数={len(segs)}", flush=True)
    for s in segs:
        print(f"    [{s['start']:>7.2f} → {s['end']:>7.2f}] {s['text']}", flush=True)
    print(f">>> 全文：{text.strip()}", flush=True)

    result = {
        "input": args.input,
        "requested_language": args.language,
        "detected_language": info.language,
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 4),
        "duration": round(float(audio_dur), 3) if audio_dur else None,
        "elapsed_s": round(dt, 2),
        "model": args.model_size,
        "device": device,
        "compute_type": compute_type,
        "text": text.strip(),
        "segments": segs,
        "clips": {},
    }

    # 额外片段（用 ffmpeg 抽出临时 wav 再识别，避免动到原始文件）
    if args.clip:
        import subprocess
        import tempfile
        for spec in args.clip:
            parts = spec.split(":")
            if len(parts) != 3:
                print(f"[跳过] 片段格式应为 名称:起点:终点，收到 {spec}", flush=True)
                continue
            name, a, b = parts[0], float(parts[1]), float(parts[2])
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                tmp = tf.name
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-i", args.input,
                     "-ss", str(a), "-to", str(b), "-ac", "1", "-ar", "16000", tmp],
                    check=True,
                )
                ctext, csegs, cinfo = transcribe(tmp, lang)
                print(f">>> 片段 {name} [{a}-{b}]：{ctext.strip()}", flush=True)
                result["clips"][name] = {
                    "start": a, "end": b,
                    "text": ctext.strip(),
                    "segments": csegs,
                }
            except Exception as e:  # noqa: BLE001
                print(f"[片段 {name} 失败] {type(e).__name__}: {e}", flush=True)
                result["clips"][name] = {"start": a, "end": b,
                                         "error": f"{type(e).__name__}: {e}"}
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f">>> 写入 {args.out}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[异常] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
