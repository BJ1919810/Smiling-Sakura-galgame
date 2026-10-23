#!/bin/bash
# ============================================================
# IndexTTS-2.5 云端环境安装（云卡 AutoDL 3080）
# 依据：docs/云卡3080-使用手册.md（Android-tts 工程）
#   · pip 下载极慢 => 用 curl 从 aliyun 直链取 uv wheel
#   · GitHub 主站不通 => uv 的 python 下载走 ghfast.top 镜像
#   · 大件一律落数据盘（UV_CACHE_DIR / UV_PYTHON_INSTALL_DIR / .venv）
# 幂等：可重复执行
# ============================================================
set -u

REPO=/root/autodl-tmp/index-tts
DATA=/root/autodl-tmp
LOG=$DATA/index-tts-install.log

export PATH=/root/miniconda3/bin:$PATH
export UV_CACHE_DIR=$DATA/.uv_cache
export UV_PYTHON_INSTALL_DIR=$DATA/.uv_python
export UV_PYTHON_INSTALL_MIRROR=https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download
export PIP_CACHE_DIR=$DATA/.pip_cache
export HF_ENDPOINT=https://hf-mirror.com

step() { echo; echo "########## $* ##########"; }

step "0. 磁盘基线"
df -h / /root/autodl-tmp

step "1. 安装 uv"
if command -v uv >/dev/null 2>&1; then
  echo "uv 已存在：$(uv --version)"
else
  mkdir -p /tmp/uvdl && cd /tmp/uvdl
  # ⚠️ 坑：aliyun simple 索引里的 href 尾部带 #sha256=... ，
  #    用 grep -E '\.whl$' 永远匹配不到（会退回索引页 URL，下成 1708K 的 HTML）。
  #    所以交给 Python 正规解析 + urljoin。
  URL=$(/root/miniconda3/bin/python -c "
import re, urllib.request, urllib.parse
h = urllib.request.urlopen('https://mirrors.aliyun.com/pypi/simple/uv/', timeout=90).read().decode('utf-8','replace')
c = re.findall(r'href=[\"\x27]([^\"\x27]*\.whl[^\"\x27]*)', h)
c = [u for u in c if 'manylinux' in u and 'x86_64' in u]
c = [urllib.parse.urljoin('https://mirrors.aliyun.com/pypi/simple/uv/', u) for u in c]
print(c[-1] if c else '')
")
  echo "wheel url = $URL"
  if [ -z "$URL" ]; then
    echo "索引解析失败，回退 pip 直装"
    /root/miniconda3/bin/pip install -U uv -i https://mirrors.aliyun.com/pypi/simple/
  else
    # pip 会按「文件名」校验 wheel 合法性，必须用真实 wheel 名（不能叫 uv.whl）
    WHL=$(basename "${URL%%#*}")
    echo "保存为 $WHL"
    curl -L --retry 5 --retry-delay 3 --max-time 900 -o "$WHL" "$URL" \
         -w 'curl: http=%{http_code} size=%{size_download} speed=%{speed_download}B/s\n'
    # 校验：wheel 必须是 zip（PK 魔数），防止把 HTML 索引页当包装上
    if [ "$(head -c 2 "$WHL")" != "PK" ]; then
      echo "下载物不是 zip，回退 pip 直装"
      /root/miniconda3/bin/pip install -U uv -i https://mirrors.aliyun.com/pypi/simple/
    else
      /root/miniconda3/bin/pip install --no-index --no-deps "/tmp/uvdl/$WHL"
    fi
  fi
fi
which uv || export PATH="$HOME/.local/bin:$PATH"
uv --version || { echo "UV_INSTALL_FAILED"; exit 1; }

step "2. 模型目录接线：./checkpoints -> $DATA/index-tts-2.5"
mkdir -p "$DATA/index-tts-2.5/hf_cache"
if [ -f "$REPO/checkpoints/pinyin.vocab" ]; then
  mv -f "$REPO/checkpoints/pinyin.vocab" "$DATA/index-tts-2.5/pinyin.vocab"
fi
if [ -L "$REPO/checkpoints" ]; then
  echo "checkpoints 已是软链：$(readlink $REPO/checkpoints)"
else
  rm -rf "$REPO/checkpoints"
  ln -s "$DATA/index-tts-2.5" "$REPO/checkpoints"
fi
ls -lL "$REPO/checkpoints/gpt.pth" | head -1
ls -l "$REPO/checkpoints" | head -2

step "3. uv sync（.venv 落数据盘，走 aliyun 源）"
cd "$REPO"
uv sync --extra webui --default-index https://mirrors.aliyun.com/pypi/simple
RC=$?
echo "uv sync exit=$RC"
if [ $RC -ne 0 ]; then
  echo "---- 退回 --index-url 形式重试 ----"
  uv sync --extra webui --index-url https://mirrors.aliyun.com/pypi/simple
  RC=$?
  echo "retry exit=$RC"
fi
[ $RC -ne 0 ] && { echo "UV_SYNC_FAILED"; exit 1; }

step "4. 自检"
# ⚠️ 坑：这里**不要用 `uv run`**。`uv run` 会先做一次环境同步校验，
#    实测在本机卡死 735 s（CPU 只用了 2 s，纯阻塞等待，无输出）。
#    直接用 venv 解释器，绕过 uv 的同步逻辑。
cd "$REPO"
timeout 300 .venv/bin/python -c "import torch, torchaudio, indextts; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), '| torchaudio', torchaudio.__version__); print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"

step "5. 磁盘收尾"
du -sh "$REPO/.venv" 2>/dev/null
df -h / /root/autodl-tmp
echo "=== INSTALL DONE $(date) ==="
