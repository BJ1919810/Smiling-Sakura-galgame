# -*- coding: utf-8 -*-
"""云卡生图 CLI（笑樱项目）—— 通过 SSH 隧道直连 AutoDL RTX3080 上的 ComfyUI。

模型：Qwen-Image 2512 fp8（无水印）＋ Qwen2.5-VL 文本编码器 ＋ qwen_image_vae。
与网页控制台（Android-tts/tools/comfy_web）同一条隧道，但改成命令行，方便脚本化批量出图。

用法：
    # 文生图
    python tools/cloudgen.py t2i --prompt "..." --out 素材基准/out/a.png [--width 1328 --height 784]
    # 图生图（源图自动上传到云卡 input/）
    python tools/cloudgen.py i2i --src a.png --denoise 0.6 --prompt "..." --out b.png
    # 黑白蒙版法合成透明 PNG（在云端 JoinImageWithAlpha）
    python tools/cloudgen.py alpha --color c.png --mask m.png --out t.png
    # 查候选（模型/采样器/节点）
    python tools/cloudgen.py info [--node JoinImageWithAlpha]

口令来源：环境变量 COMFY_SSH_* 或 tools/cloud-comfy.config.json
"""
from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import sys
import time
import uuid

import paramiko

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "cloud-comfy.config.json")

DEFAULTS = {
    "unet": "qwen_image_2512_fp8_e4m3fn.safetensors",
    "clip": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    "vae": "qwen_image_vae.safetensors",
    "width": 1328, "height": 784,
    "steps": 25, "cfg": 2.5, "shift": 3.5,
    "sampler": "euler", "scheduler": "simple",
    "negative": "",
}


# ------------------------------------------------------------------ 配置 / 隧道

def load_cfg() -> dict:
    cfg: dict = {}
    if os.path.exists(CFG_PATH):
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    out = {
        "host": os.environ.get("COMFY_SSH_HOST") or cfg.get("ssh_host", ""),
        "port": int(os.environ.get("COMFY_SSH_PORT") or cfg.get("ssh_port", 22)),
        "user": os.environ.get("COMFY_SSH_USER") or cfg.get("ssh_user", "root"),
        "pass": os.environ.get("COMFY_SSH_PASS") or cfg.get("ssh_password", ""),
        "comfy": int(os.environ.get("COMFY_REMOTE_PORT") or cfg.get("remote_comfy_port", 6006)),
    }
    if not out["host"] or not out["pass"]:
        sys.exit("[配置缺失] 需要 ssh_host / ssh_password（%s 或 COMFY_SSH_* 环境变量）" % CFG_PATH)
    return out


class Tunnel:
    """SSH direct-tcpip → 云卡 127.0.0.1:6006（ComfyUI）。"""

    def __init__(self) -> None:
        self.cfg = load_cfg()
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.ssh.connect(self.cfg["host"], self.cfg["port"], self.cfg["user"], self.cfg["pass"],
                         timeout=25, look_for_keys=False, allow_agent=False)
        self._transport = self.ssh.get_transport()

    def channel(self) -> paramiko.Channel:
        return self._transport.open_channel(
            "direct-tcpip", ("127.0.0.1", self.cfg["comfy"]), ("127.0.0.1", 0))

    def conn(self) -> http.client.HTTPConnection:
        factory = self.channel

        class _Conn(http.client.HTTPConnection):
            def connect(self) -> None:      # noqa: D401
                self.sock = factory()

        return _Conn("127.0.0.1", self.cfg["comfy"], timeout=1800)

    def close(self) -> None:
        try:
            self.ssh.close()
        except Exception:
            pass


# ------------------------------------------------------------------ HTTP 小工具

def api_get(t: Tunnel, path: str) -> bytes:
    c = t.conn()
    try:
        c.request("GET", path)
        r = c.getresponse()
        data = r.read()
        if r.status != 200:
            raise RuntimeError("GET %s → %s %s" % (path, r.status, data[:300]))
        return data
    finally:
        c.close()


def api_json(t: Tunnel, path: str, body: dict | None = None) -> dict:
    c = t.conn()
    try:
        if body is None:
            c.request("GET", path)
        else:
            c.request("POST", path, body=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        if r.status != 200:
            raise RuntimeError("%s → %s %s" % (path, r.status, data[:400]))
        return json.loads(data or b"{}")
    finally:
        c.close()


def upload_image(t: Tunnel, src_path: str, name: str) -> str:
    """上传源图到云卡 ComfyUI/input/，返回可用文件名（ComfyUI /upload/image，无需 SFTP）。"""
    with open(src_path, "rb") as f:
        raw = f.read()
    boundary = "----xy" + uuid.uuid4().hex
    ctype = mimetypes.guess_type(src_path)[0] or "image/png"
    body = b"".join([
        ("--%s\r\n" % boundary).encode(),
        ('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % name).encode(),
        ("Content-Type: %s\r\n\r\n" % ctype).encode(),
        raw,
        ("\r\n--%s--\r\n" % boundary).encode(),
    ])
    c = t.conn()
    try:
        c.request("POST", "/upload/image", body=body,
                  headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        r = c.getresponse()
        data = json.loads(r.read() or b"{}")
        if r.status != 200:
            raise RuntimeError("上传失败 %s %s" % (r.status, data))
        return data.get("name") or name
    finally:
        c.close()


def run_workflow(t: Tunnel, wf: dict, out_path: str, poll_s: float = 2.0,
                 timeout_s: float = 1800) -> tuple[str, int]:
    """提交工作流 → 轮询 → 取回第一张图。返回 (云卡文件名, 实际种子)。"""
    pid = api_json(t, "/prompt", {"prompt": wf, "client_id": uuid.uuid4().hex})["prompt_id"]
    t0 = time.time()
    seen = False
    last_note = 0.0
    while True:
        if time.time() - t0 > timeout_s:
            raise TimeoutError("等待出图超时（%.0fs）" % timeout_s)
        hist = api_json(t, "/history/" + pid)
        if hist:
            entry = hist.get(pid) or {}
            st = (entry.get("status") or {})
            if st.get("status_str") == "error" or st.get("completed") is False and st.get("messages"):
                msgs = st.get("messages") or []
                if any(m[0] == "execution_error" for m in msgs):
                    raise RuntimeError("ComfyUI 执行报错：%s" % json.dumps(msgs, ensure_ascii=False)[:800])
            outputs = entry.get("outputs") or {}
            imgs = [im for node in outputs.values() for im in (node.get("images") or [])]
            if imgs:
                im = imgs[0]
                q = "/view?filename=%s&subfolder=%s&type=%s" % (
                    im["filename"], im.get("subfolder", ""), im.get("type", "output"))
                raw = api_get(t, q)
                os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(raw)
                seed = (entry.get("prompt") or [None, None, {}])[2].get("seed", -1)
                return im["filename"], int(seed)
            seen = True
        else:
            # 排队中（不在 history = 还在跑或排队）
            if not seen and time.time() - last_note > 20:
                last_note = time.time()
                print("  ...生成中 %.0fs" % (time.time() - t0), flush=True)
        time.sleep(poll_s)


# ------------------------------------------------------------------ 工作流

def wf_t2i(a: argparse.Namespace) -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": a.unet, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": a.clip, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": a.vae}},
        "4": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": a.shift}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": a.prompt}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": a.negative}},
        "7": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": a.width, "height": a.height, "batch_size": 1}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0],
                         "latent_image": ["7", 0], "seed": a.seed, "steps": a.steps,
                         "cfg": a.cfg, "sampler_name": a.sampler, "scheduler": a.scheduler,
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"images": ["9", 0], "filename_prefix": a.prefix}},
    }


def wf_i2i(a: argparse.Namespace, uploaded: str) -> dict:
    wf = wf_t2i(a)
    wf["7"] = {"class_type": "LoadImage", "inputs": {"image": uploaded}}
    wf["7b"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["7", 0], "vae": ["3", 0]}}
    wf["8"]["inputs"]["latent_image"] = ["7b", 0]
    wf["8"]["inputs"]["denoise"] = a.denoise
    return wf


def wf_inpaint(a: argparse.Namespace, image_name: str, mask_name: str) -> dict:
    """局部重绘：mask 白区重画，其余靠 latent 保持。

    ⚠️ mask 语义：**白 = 要重绘**（VAEEncodeForInpaint 的 mask 是「inpaint 区域」）。
    ⚠️ 输出整图，mask 外经过 VAE 往返会有轻微损失 → 调用方务必 `paste_back` 贴回原像素。
    """
    wf = wf_t2i(a)
    wf["7"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    wf["7b"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
    wf["7c"] = {"class_type": "ImageToMask", "inputs": {"image": ["7b", 0], "channel": "red"}}
    wf["7d"] = {"class_type": "VAEEncodeForInpaint",
                "inputs": {"pixels": ["7", 0], "vae": ["3", 0],
                           "mask": ["7c", 0], "grow_mask_by": a.grow}}
    wf["8"]["inputs"]["latent_image"] = ["7d", 0]
    wf["8"]["inputs"]["denoise"] = 1.0
    return wf


def make_rect_mask(src_path: str, rect: list[int], out_path: str, dilate: int = 0) -> str:
    """生成矩形 mask（黑底白块）。rect = [x0, y0, x1, y1]，dilate 再向外扩多少像素。"""
    from PIL import Image, ImageDraw
    with Image.open(src_path) as im:
        w, h = im.size
    x0, y0, x1, y1 = rect
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rectangle(
        [max(0, x0 - dilate), max(0, y0 - dilate),
         min(w, x1 + dilate), min(h, y1 + dilate)], fill=255)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    m.save(out_path)
    return out_path


def paste_back(src_path: str, result_path: str, mask_path: str, out_path: str,
               feather: float = 0.0, tail_pct: float | None = None,
               keep_alpha: bool = True) -> dict:
    """把重绘结果只贴回 mask 区域，mask 外 100% 保留原图像素。

    feather>0 时对 mask 做高斯羽化，避免硬接缝。
    keep_alpha=True（默认）时**保留源图的 alpha 通道**——透明区的 RGB 残留被抹掉、
    但透明度不变；若把 alpha 一起贴回会把透明区弄成不透明（去水印场景的经典事故）。
    tail_pct 用于「右下角去水印」这类场景的自动定量：统计结果图最后 N% 行里
    比原图变化的像素数。
    """
    from PIL import Image, ImageFilter
    import numpy as np
    base = Image.open(src_path)
    mode0 = base.mode
    base_a = base.getchannel("A") if "A" in base.getbands() else None
    base_rgb = base.convert("RGB")
    res = Image.open(result_path).convert("RGB")
    if res.size != base_rgb.size:
        res = res.resize(base_rgb.size, Image.LANCZOS)
    m = Image.open(mask_path).convert("L")
    if m.size != base_rgb.size:
        m = m.resize(base_rgb.size, Image.NEAREST)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))

    merged = base_rgb.copy()
    merged.paste(res, (0, 0), m)

    if keep_alpha and base_a is not None:
        out_im = merged.convert("RGBA")
        out_im.putalpha(base_a)          # alpha 原样保留
        out_im.save(out_path)
    elif mode0 == "RGB":
        merged.save(out_path)
    else:
        merged.convert(mode0).save(out_path)

    st = {}
    if tail_pct:
        a = np.asarray(base_rgb.convert("L"), dtype=np.int16)
        b = np.asarray(merged.convert("L"), dtype=np.int16)
        cut = int(base_rgb.size[1] * (1 - tail_pct / 100.0))
        st["tail_changed_px"] = int((np.abs(a - b)[cut:] > 8).sum())
        st["tail_min"] = int(a[cut:].min())
        st["tail_max"] = int(a[cut:].max())
    return st


def wf_alpha(color_name: str, mask_name: str, prefix: str,
             threshold: float | None, invert: bool) -> dict:
    """黑白蒙版法：颜色层 + 白剪影蒙版 → (二值化) → JoinImageWithAlpha → RGBA PNG。

    ⚠️ 云卡 ComfyUI 没有 `ImageThreshold`，但有 extra 节点 **`ThresholdMask(mask, value)`**。
    `value` 是 0~1 的阈值（作用在 MASK 上）。
    """
    wf: dict = {
        "1": {"class_type": "LoadImage", "inputs": {"image": color_name}},
        "2": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
    }
    mask_ref: list = ["3", 0]
    if threshold is not None:
        wf["4"] = {"class_type": "ThresholdMask",
                   "inputs": {"mask": mask_ref, "value": threshold}}
        mask_ref = ["4", 0]
    if invert:
        wf["5"] = {"class_type": "InvertMask", "inputs": {"mask": mask_ref}}
        mask_ref = ["5", 0]
    wf["6"] = {"class_type": "JoinImageWithAlpha",
               "inputs": {"image": ["1", 0], "alpha": mask_ref}}
    wf["7"] = {"class_type": "SaveImage",
               "inputs": {"images": ["6", 0], "filename_prefix": prefix}}
    return wf


# ------------------------------------------------------------------ 子命令

def cmd_info(t: Tunnel, a: argparse.Namespace) -> None:
    if a.node:
        d = api_json(t, "/object_info/" + a.node)
        if not d:
            print("节点不存在：%s" % a.node)
            return
        print(json.dumps(d, ensure_ascii=False, indent=2)[:3000])
        return
    oi = api_json(t, "/object_info")
    print("ComfyUI 节点总数：%d" % len(oi))
    for key, label in [("UNETLoader", "unet_name"), ("CLIPLoader", "clip_name"),
                       ("VAELoader", "vae_name")]:
        opts = oi.get(key, {}).get("input", {}).get("required", {}).get(label)
        print("%s.%s: %s" % (key, label, (opts or [[]])[0]))
    for cls in ["KSampler", "ThresholdMask", "JoinImageWithAlpha", "ImageToMask", "InvertMask"]:
        print("节点 %-20s %s" % (cls, "✅" if cls in oi else "❌ 缺失"))
    names = [n for n in oi if "alpha" in n.lower() or "mask" in n.lower()]
    print("含 alpha/mask 的节点：%s" % ", ".join(sorted(names)[:40]))


def _common_generate(t: Tunnel, a: argparse.Namespace) -> None:
    if not a.seed or a.seed < 0:
        a.seed = int(time.time() * 1000) % (2 ** 31)
    print("[提交] %s %dx%d steps=%d cfg=%s seed=%d" % (
        "i2i" if a.cmd == "i2i" else "t2i", a.width, a.height, a.steps, a.cfg, a.seed))
    t0 = time.time()
    if a.cmd == "i2i":
        if not os.path.exists(a.src):
            sys.exit("源图不存在：%s" % a.src)
        name = "xy_" + os.path.basename(a.src).replace(" ", "_")
        # 中文名经 HTTP→JSON 多层转码易坏，统一 ASCII 化
        if not name.isascii():
            name = "xy_" + uuid.uuid4().hex[:10] + (os.path.splitext(a.src)[1] or ".png")
        uploaded = upload_image(t, a.src, name)
        print("[上传] %s → 云卡 input/%s" % (os.path.basename(a.src), uploaded))
        wf = wf_i2i(a, uploaded)
    else:
        wf = wf_t2i(a)
    fn, seed = run_workflow(t, wf, a.out)
    size = os.path.getsize(a.out)
    from PIL import Image
    with Image.open(a.out) as im:
        mode, wh = im.mode, im.size
    print("[完成] %.0fs  %s  %dx%d mode=%s %d 字节  seed=%d" % (
        time.time() - t0, a.out, wh[0], wh[1], mode, size, seed))


def cmd_inpaint(t: Tunnel, a: argparse.Namespace) -> None:
    if not os.path.exists(a.src):
        sys.exit("源图不存在：%s" % a.src)
    if not a.seed or a.seed < 0:
        a.seed = int(time.time() * 1000) % (2 ** 31)
    # mask：文件 或 --mask-rect 自动生成
    if a.mask:
        if not os.path.exists(a.mask):
            sys.exit("mask 不存在：%s" % a.mask)
        mask_path = a.mask
    else:
        from PIL import Image
        with Image.open(a.src) as im:
            w, h = im.size
        r = [int(v) for v in a.mask_rect.split(",")]
        if len(r) != 4:
            sys.exit("--mask-rect 需要 x0,y0,x1,y1 四个整数")
        # 支持负值：负 = 从右/下边缘回数（-240,-130,-1,-1）
        def fix(v, n):
            return v if v >= 0 else n + v
        r = [fix(r[0], w), fix(r[1], h), fix(r[2], w), fix(r[3], h)]
        mask_path = os.path.join(os.path.dirname(os.path.abspath(a.out)), "_automsk.png")
        make_rect_mask(a.src, r, mask_path, dilate=a.dilate)
        print("[mask] 矩形 %s（原始尺寸 %dx%d）→ %s" % (r, w, h, mask_path))

    def ascii_name(p, pre):
        n = pre + os.path.basename(p).replace(" ", "_")
        if not n.isascii():
            n = pre + uuid.uuid4().hex[:10] + (os.path.splitext(p)[1] or ".png")
        return n

    iname = upload_image(t, a.src, ascii_name(a.src, "xy_inp_src"))
    mname = upload_image(t, mask_path, ascii_name(mask_path, "xy_inp_msk"))
    from PIL import Image
    with Image.open(a.src) as im:
        sw, sh = im.size
    print("[提交] inpaint %dx%d steps=%d cfg=%s grow=%d denoise=1.0" % (
        sw, sh, a.steps, a.cfg, a.grow))
    t0 = time.time()
    wf = wf_inpaint(a, iname, mname)
    fn, seed = run_workflow(t, wf, a.out)
    t_q = time.time() - t0

    if a.paste and os.path.exists(a.out):
        st = paste_back(a.src, a.out, mask_path, a.out, feather=a.feather, tail_pct=8)
        print("[贴回] 羽化 %.1fpx，mask 外保留原始像素" % a.feather)
        print("       右下 8%% 行内变动像素 %d，原图该区亮度 min/max=%s/%s" % (
            st.get("tail_changed_px", -1), st.get("tail_min"), st.get("tail_max")))
    with Image.open(a.out) as im:
        mode, wh = im.mode, im.size
    print("[完成] %.0fs（含上传/轮询/回传）  %s  %dx%d mode=%s %.1fMB  seed=%d" % (
        t_q, a.out, wh[0], wh[1], mode, os.path.getsize(a.out) / 1048576, seed))


def cmd_alpha(t: Tunnel, a: argparse.Namespace) -> None:
    for p in (a.color, a.mask):
        if not os.path.exists(p):
            sys.exit("缺文件：%s" % p)
    cname = upload_image(t, a.color, "xy_color" + os.path.splitext(a.color)[1].lower())
    mname = upload_image(t, a.mask, "xy_mask" + os.path.splitext(a.mask)[1].lower())
    wf = wf_alpha(cname, mname, a.prefix, a.threshold, a.invert)
    print("[提交] alpha 合成 threshold=%s invert=%s" % (a.threshold, a.invert))
    fn, _ = run_workflow(t, wf, a.out)
    from PIL import Image, ImageStat
    with Image.open(a.out) as im:
        print("[完成] %s  mode=%s size=%s" % (a.out, im.mode, im.size))
        if im.mode == "RGBA":
            alpha = im.getchannel("A")
            hist = alpha.histogram()
            total = im.size[0] * im.size[1]
            print("       alpha 直方图：透明 %d (%.1f%%) / 不透明 %d (%.1f%%) / 中间灰 %d (%.1f%%)" % (
                hist[0], 100 * hist[0] / total, hist[255], 100 * hist[255] / total,
                total - hist[0] - hist[255],
                100 * (total - hist[0] - hist[255]) / total))
            print("       不透明区平均亮度：%.1f" % ImageStat.Stat(im.convert("RGB")).mean[0])


def main() -> None:
    ap = argparse.ArgumentParser(description="云卡生图 CLI（Qwen-Image 2512）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_gen(p: argparse.ArgumentParser) -> None:
        p.add_argument("--prompt", required=True)
        p.add_argument("--negative", default=DEFAULTS["negative"])
        p.add_argument("--out", required=True)
        p.add_argument("--width", type=int, default=DEFAULTS["width"])
        p.add_argument("--height", type=int, default=DEFAULTS["height"])
        p.add_argument("--steps", type=int, default=DEFAULTS["steps"])
        p.add_argument("--cfg", type=float, default=DEFAULTS["cfg"])
        p.add_argument("--shift", type=float, default=DEFAULTS["shift"])
        p.add_argument("--sampler", default=DEFAULTS["sampler"])
        p.add_argument("--scheduler", default=DEFAULTS["scheduler"])
        p.add_argument("--seed", type=int, default=-1)
        p.add_argument("--prefix", default="xy")
        p.add_argument("--unet", default=DEFAULTS["unet"])
        p.add_argument("--clip", default=DEFAULTS["clip"])
        p.add_argument("--vae", default=DEFAULTS["vae"])

    p1 = sub.add_parser("t2i"); add_gen(p1)
    p2 = sub.add_parser("i2i"); add_gen(p2)
    p2.add_argument("--src", required=True)
    p2.add_argument("--denoise", type=float, default=0.6)

    p3 = sub.add_parser("alpha")
    p3.add_argument("--color", required=True)
    p3.add_argument("--mask", required=True)
    p3.add_argument("--out", required=True)
    p3.add_argument("--prefix", default="xy_alpha")
    p3.add_argument("--threshold", type=float, default=None,
                    help="先做二值化再取 alpha（0~1），默认不二值化")
    p3.add_argument("--invert", action="store_true", help="蒙版黑白反相")

    p4 = sub.add_parser("info")
    p4.add_argument("--node", default=None)

    p5 = sub.add_parser("inpaint", help="局部重绘（去水印/补件）：mask 白区重画，其余原样贴回")
    add_gen(p5)
    p5.add_argument("--src", required=True)
    p5.add_argument("--mask", default=None, help="mask 文件（白=要重画）")
    p5.add_argument("--mask-rect", default=None,
                    help="不提供 mask 时自动生成矩形，格式 x0,y0,x1,y1；负值＝从右/下边缘回数")
    p5.add_argument("--dilate", type=int, default=0, help="矩形 mask 再外扩像素")
    p5.add_argument("--grow", type=int, default=16, help="VAEEncodeForInpaint 的 grow_mask_by")
    p5.add_argument("--feather", type=float, default=4.0, help="贴回时的羽化半径")
    p5.add_argument("--no-paste", dest="paste", action="store_false",
                    help="不贴回，直接用云端整图输出（默认贴回）")

    a = ap.parse_args()
    t = Tunnel()
    try:
        if a.cmd == "info":
            cmd_info(t, a)
        elif a.cmd == "alpha":
            cmd_alpha(t, a)
        elif a.cmd == "inpaint":
            cmd_inpaint(t, a)
        else:
            _common_generate(t, a)
    finally:
        t.close()


if __name__ == "__main__":
    main()
