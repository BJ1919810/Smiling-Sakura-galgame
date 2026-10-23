# -*- coding: utf-8 -*-
"""BS-Roformer 人声分离（云卡侧执行）——移植 Movie-trans 的 denoise.py 逻辑。

为什么移植而不是调用 Movie-trans：
  主人的要求是「不要改 Movie-trans 工程里的任何文件」，且云卡是 Linux、
  Movie-trans 是 Windows 本地工程（路径/依赖都不通用）。
  这里只**复用它的算法约定**，代码独立。

关键约定（都对齐 Movie-trans/tools/denoise.py）
  · 引擎 roformer（BS-Roformer），权重 model_bs_roformer_ep_317_sdr_12.9755
  · 输入用 librosa 以 44100 重采样载入（模型 config 的 audio.sample_rate=44100）
  · 输出三件套，命名沿用它的习惯：
        vocal_1_44100.wav   人声，44100 立体声（TTS 参考切片从这切）
        vocal_1_16000.wav   人声，16k 单声道（喂 ASR）
        bg_1_44100.wav      伴奏
  · ⛔ 不要在分离后加"去噪/去回响/清高频"后处理（2026-09-14 定论：会让声音变干）

与 Movie-trans 的差异（有意为之）
  · **一定要带 .yaml 配置**。Movie-trans 没带 yaml 就走 get_default_config()，
    内置默认的 inference.num_overlap=2、batch_size=2，而官方 yaml 是
    num_overlap=4、batch_size=1 —— 重叠次数越多拼接缝越平滑，这是实打实的质量差异。
  · torch.load 显式 weights_only=False（云卡 torch 2.1.2 默认就是 False，
    写出来是为了换环境时不踩 PyTorch 2.6+ 的坑）。

用法：
    /root/miniconda3/envs/GPTSoVits/bin/python roformer-denoise.py \
        --input /root/autodl-tmp/sakura-in/merged.wav \
        --out-dir /root/autodl-tmp/sakura-out/denoise
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import traceback

UVR5_ROOT = "/root/GPT-SoVITS/tools/uvr5"
WEIGHTS = os.path.join(UVR5_ROOT, "uvr5_weights")
MODEL_NAME = "model_bs_roformer_ep_317_sdr_12.9755"

sys.path.insert(0, UVR5_ROOT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="输入音频（任意采样率/声道）")
    ap.add_argument("--out-dir", required=True, help="输出目录")
    ap.add_argument("--model-name", default=MODEL_NAME)
    ap.add_argument("--fp32", action="store_true", help="关掉半精度（默认半精度）")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"[错误] 输入不存在：{args.input}")

    import torch
    print(f">>> torch {torch.__version__}  cuda={torch.cuda.is_available()}", flush=True)
    if not torch.cuda.is_available():
        sys.exit("[错误] roformer 必须跑在 CUDA 上（attend 用 SDPA，CPU 会极慢）")

    # PyTorch 2.6+ 的 weights_only 默认翻转会把 ckpt 反序列化卡住；显式关掉。
    _orig_load = torch.load
    def _patched_load(f, *a, **kw):
        kw["weights_only"] = False
        return _orig_load(f, *a, **kw)
    torch.load = _patched_load

    from uvr5.bsroformer import Roformer_Loader  # noqa: E402

    ckpt = os.path.join(WEIGHTS, args.model_name + ".ckpt")
    yaml = os.path.join(WEIGHTS, args.model_name + ".yaml")
    if not os.path.isfile(ckpt):
        sys.exit(f"[错误] 找不到权重：{ckpt}")
    if not os.path.isfile(yaml):
        print(f"!! 没有配置 {yaml}，将退回内置默认（num_overlap=2，质量略差）", flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    # Roformer_Loader 把产物写到 vocal_root/<basename>_<target>.wav
    tmp_vocal = os.path.join(args.out_dir, "_raw_vocal")
    tmp_other = os.path.join(args.out_dir, "_raw_other")
    os.makedirs(tmp_vocal, exist_ok=True)
    os.makedirs(tmp_other, exist_ok=True)

    is_half = not args.fp32
    t0 = time.perf_counter()
    print(f">>> 加载模型 {args.model_name}  half={is_half}", flush=True)
    loader = Roformer_Loader(model_path=ckpt, config_path=yaml, device="cuda", is_half=is_half)
    print(f">>> 模型就绪 {time.perf_counter() - t0:.1f}s", flush=True)
    print(f">>> config: type={loader.model_type} "
          f"num_overlap={loader.config['inference']['num_overlap']} "
          f"batch_size={loader.config['inference']['batch_size']} "
          f"sr={loader.config['audio']['sample_rate']}", flush=True)
    print(f">>> target_instrument={loader.config['training']['target_instrument']}", flush=True)

    t1 = time.perf_counter()
    print(f">>> 开始分离 {args.input}", flush=True)
    loader._path_audio_(args.input, tmp_other, tmp_vocal, "wav")
    print(f">>> 分离完成 {time.perf_counter() - t1:.1f}s", flush=True)

    # 抹平命名差异（yaml 里是 Vocals/Instrumental，内置默认是 vocals/other）
    base = os.path.splitext(os.path.basename(args.input))[0]
    target = loader.config["training"]["target_instrument"]
    others = [i for i in loader.config["training"]["instruments"] if i != target]
    produced_vocal = os.path.join(tmp_vocal, f"{base}_{target}.wav")
    produced_inst = os.path.join(tmp_other, f"{base}_{others[0]}.wav")
    if not os.path.isfile(produced_vocal):
        sys.exit(f"[错误] 没产出人声：{produced_vocal}\n"
                 f"目录内容：{os.listdir(tmp_vocal)}")

    import librosa
    import numpy as np
    import soundfile as sf

    vocal_44k = os.path.join(args.out_dir, "vocal_1_44100.wav")
    shutil.copy2(produced_vocal, vocal_44k)
    print(f"[输出] {vocal_44k}", flush=True)

    if os.path.isfile(produced_inst):
        bg_44k = os.path.join(args.out_dir, "bg_1_44100.wav")
        shutil.copy2(produced_inst, bg_44k)
        print(f"[输出] {bg_44k}", flush=True)

    # 16k 单声道（喂 ASR / 说话人分离）
    y, sr = librosa.load(produced_vocal, sr=None, mono=True)
    y16 = librosa.resample(y, orig_sr=sr, target_sr=16000)
    vocal_16k = os.path.join(args.out_dir, "vocal_1_16000.wav")
    sf.write(vocal_16k, y16, 16000, subtype="PCM_16")
    print(f"[输出] {vocal_16k}", flush=True)

    # 顺手报告一下分离前后的电平（便于判断有没有削顶/变小声）
    def _rms_db(x):
        return 20 * np.log10(max(float(np.sqrt((x ** 2).mean())), 1e-10))
    mix, _ = librosa.load(args.input, sr=44100, mono=True)
    v, _ = librosa.load(vocal_44k, sr=44100, mono=True)
    print(f">>> 电平：mix {_rms_db(mix):.1f} dBFS -> vocal {_rms_db(v):.1f} dBFS", flush=True)
    print(f">>> 峰值：vocal {20*np.log10(max(float(np.abs(v).max()),1e-10)):.1f} dBFS", flush=True)

    for d in (tmp_vocal, tmp_other):
        shutil.rmtree(d, ignore_errors=True)

    print(">>> 全部完成", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[异常] {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
