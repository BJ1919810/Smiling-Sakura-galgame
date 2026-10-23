# -*- coding: utf-8 -*-
"""BS-Roformer 人声分离 —— **本地版**（RTX 3070）。

与 `tools/remote/roformer-denoise.py`（云卡版）算法完全一致，差别只在路径：
  · 代码：**只读引用** Movie-trans 的 `uvr5/` 包（⛔ 不改动该工程任何文件）
  · 权重：Movie-trans/uvr5/uvr5_weights/model_bs_roformer_ep_317_sdr_12.9755.{ckpt,yaml}
  · 设备：本机 cuda

★ 为什么可以本地跑：主人定的「降噪在本地、TTS 在云卡」。
  本机有 RTX 3070 8G + torch 2.6.0+cu126，且**省掉上传下载**。
  ⚠️ 本机 python 必须用 `D:\\0Coding\\Python312\\python.exe`（managed venv 没有 torch）。

★ 为什么一定要带 .yaml：不带就走 `get_default_config()`，其 `num_overlap=2`、`batch_size=2`；
  官方 yaml 是 `num_overlap=4`、`batch_size=1` —— 重叠越多拼接缝越平滑（实打实的质量差异）。

⛔ 不要在分离后加「去噪 / 去回响 / 清高频」后处理（2026-09-14 定论：会让声音变干）。

用法：
    python tools/local-denoise.py --inputs a.wav b.wav --out-dir .workbuddy/tmp/ref/out
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Movie-trans 工程：代码与权重都从这里**只读**取用
MT_ROOT = r'D:\0Coding\pythonworkspace\My_program\Movie-trans'
UVR5_ROOT = os.path.join(MT_ROOT, 'uvr5')
WEIGHTS = os.path.join(UVR5_ROOT, 'uvr5_weights')
MODEL_NAME = 'model_bs_roformer_ep_317_sdr_12.9755'

# ⛔ 不要往 Movie-trans 里写 __pycache__
sys.dont_write_bytecode = True
sys.path.insert(0, MT_ROOT)
sys.path.insert(0, UVR5_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--inputs', nargs='+', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--model-name', default=MODEL_NAME)
    ap.add_argument('--fp32', action='store_true', help='关掉半精度（默认半精度）')
    ap.add_argument('--keep-raw', action='store_true', help='保留 _raw_vocal/_raw_other')
    args = ap.parse_args()

    for p in args.inputs:
        if not os.path.isfile(p):
            print(f'✗ 输入不存在：{p}')
            return 1

    import torch
    print(f'>>> torch {torch.__version__}  cuda={torch.cuda.is_available()}  '
          f'device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"}',
          flush=True)
    if not torch.cuda.is_available():
        print('✗ 需要 CUDA（attend 用 SDPA，CPU 会极慢）')
        return 1

    # PyTorch 2.6+ 的 weights_only 默认翻转会把 ckpt 反序列化卡住；显式关掉。
    _orig_load = torch.load

    def _patched_load(f, *a, **kw):
        kw['weights_only'] = False
        return _orig_load(f, *a, **kw)

    torch.load = _patched_load

    sys.path.insert(0, UVR5_ROOT)
    from uvr5.bsroformer import Roformer_Loader  # noqa: E402

    ckpt = os.path.join(WEIGHTS, args.model_name + '.ckpt')
    yaml = os.path.join(WEIGHTS, args.model_name + '.yaml')
    if not os.path.isfile(ckpt):
        print(f'✗ 找不到权重：{ckpt}')
        return 1
    if not os.path.isfile(yaml):
        print(f'!! 没有配置 {yaml}，将退回内置默认（num_overlap=2，质量略差）', flush=True)

    os.makedirs(args.out_dir, exist_ok=True)
    tmp_vocal = os.path.join(args.out_dir, '_raw_vocal')
    tmp_other = os.path.join(args.out_dir, '_raw_other')
    os.makedirs(tmp_vocal, exist_ok=True)
    os.makedirs(tmp_other, exist_ok=True)

    is_half = not args.fp32
    t0 = time.perf_counter()
    print(f'>>> 加载模型 {args.model_name}  half={is_half}', flush=True)
    loader = Roformer_Loader(model_path=ckpt, config_path=yaml,
                             device='cuda', is_half=is_half)
    print(f'>>> 模型就绪 {time.perf_counter() - t0:.1f}s', flush=True)
    print(f'>>> config: type={loader.model_type} '
          f'num_overlap={loader.config["inference"]["num_overlap"]} '
          f'batch_size={loader.config["inference"]["batch_size"]} '
          f'chunk_size={loader.config["audio"]["chunk_size"]} '
          f'sr={loader.config["audio"]["sample_rate"]}', flush=True)
    print(f'>>> target_instrument={loader.config["training"]["target_instrument"]}', flush=True)

    import librosa
    import numpy as np
    import soundfile as sf

    target = loader.config['training']['target_instrument']
    others = [i for i in loader.config['training']['instruments'] if i != target]

    def rms_db(x: np.ndarray) -> float:
        return 20 * np.log10(max(float(np.sqrt((x ** 2).mean())), 1e-10))

    for inp in args.inputs:
        base = os.path.splitext(os.path.basename(inp))[0]
        t1 = time.perf_counter()
        dur = sf.info(inp).duration
        print(f'\n>>> 分离 {base}（{dur:.2f}s）', flush=True)
        for d in (tmp_vocal, tmp_other):
            shutil.rmtree(d, ignore_errors=True)
            os.makedirs(d, exist_ok=True)
        loader._path_audio_(inp, tmp_other, tmp_vocal, 'wav')

        produced_vocal = os.path.join(tmp_vocal, f'{base}_{target}.wav')
        produced_inst = os.path.join(tmp_other, f'{base}_{others[0]}.wav')
        if not os.path.isfile(produced_vocal):
            print(f'✗ 没产出人声：{produced_vocal}\n  目录：{os.listdir(tmp_vocal)}')
            continue

        out_vocal = os.path.join(args.out_dir, f'{base}_vocal.wav')
        shutil.copy2(produced_vocal, out_vocal)
        if os.path.isfile(produced_inst):
            shutil.copy2(produced_inst, os.path.join(args.out_dir, f'{base}_bg.wav'))

        y, _ = librosa.load(produced_vocal, sr=None, mono=True)
        sf.write(os.path.join(args.out_dir, f'{base}_vocal_16k.wav'),
                 librosa.resample(y, orig_sr=44100, target_sr=16000), 16000,
                 subtype='PCM_16')

        mix, _ = librosa.load(inp, sr=44100, mono=True)
        v, _ = librosa.load(out_vocal, sr=44100, mono=True)
        print(f'    用时 {time.perf_counter() - t1:.1f}s | '
              f'分离前 RMS {rms_db(mix):.1f} → 人声 {rms_db(v):.1f} dBFS | '
              f'人声峰值 {20 * np.log10(max(float(np.abs(v).max()), 1e-10)):.1f} dBFS',
              flush=True)

    if not args.keep_raw:
        for d in (tmp_vocal, tmp_other):
            shutil.rmtree(d, ignore_errors=True)
    print('\n>>> 全部完成')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        print(f'[异常] {type(e).__name__}: {e}', flush=True)
        traceback.print_exc()
        sys.exit(1)
