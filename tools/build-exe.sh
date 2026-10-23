#!/usr/bin/env bash
# ============================================================================
# 笑樱 · 桌面 exe 一键出包（bash 版 —— 已降级为链路文档/换机参考）
#
# ★★★ 本机首选入口是 Node 版：`node tools/build-exe.mjs` ★★★
#     本机沙箱会拦一切显式 bash 调用（shim fallback 到 wsl.exe 撞程序黑名单），
#     `bash xxx.sh` 1 秒即退、输出乱码。本文件保留仅作链路说明。
##   链路：manifest → tsc --noEmit → vite build(dist) → cargo build --release
#         → 探针（tools/exe_probe.py）→ 部署根目录 笑樱-测试版.exe
#
# ⚠️ 三条本机铁律（都是踩出来的，别改回去）：
#   ① rustup 的 cargo/rustc **shim** 在 Git Bash 下报 STATUS_DLL_NOT_FOUND
#      → PATH 必须把「工具链真身 bin」顶到最前（cargo 会就近找 rustc）
#   ② tauri-cli 2.11.4 的 `build` 子命令在本机 panic（rust.rs unwrap）
#      → 绕过 CLI，直接跑 cargo build；NSIS 安装包略过，只出裸 exe（够用）
#   ③ 裸跑 cargo **必须显式** --features custom-protocol
#      → 否则 generate_context! 不嵌 dist，出的是黑屏空壳
#
# 前置：台本若改过，先 `npm run compile` 重编 script.json（本脚本不代劳，
#       因为台本编译是有语义的步骤，不该被出包顺手带过）。
#
# 用法：
#   bash tools/build-exe.sh              # 完整出包 + 探针 + 部署
#   bash tools/build-exe.sh --no-deploy  # 出包 + 探针，但不覆盖根目录 exe
#   bash tools/build-exe.sh --skip-build # 只用现有 dist 重编 rust（调试用）
# ============================================================================
set -euo pipefail

TOOLCHAIN="/c/Users/zbj/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin"
NODE="C:/Users/zbj/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
PY="C:/Users/zbj/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
export PATH="$TOOLCHAIN:/c/Windows/System32:$PATH"

DEPLOY=1
SKIP_BUILD=0
for a in "$@"; do
  case "$a" in
    --no-deploy)  DEPLOY=0 ;;
    --skip-build) SKIP_BUILD=1 ;;
    *) echo "未知参数：$a"; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."
ROOT="$PWD"
T0=$(date +%s)

echo "==========================================================================="
echo "笑樱 · exe 出包   $(date +'%Y-%m-%d %H:%M:%S')"
echo "==========================================================================="

# --- 0. 工具链自检（shim 坑：必须指向真身） ---
echo
echo "[0/5] 工具链自检"
"$TOOLCHAIN/cargo.exe" --version || { echo "  [x] 工具链真身不可用：$TOOLCHAIN"; exit 1; }
"$NODE" --version

# --- 1. 台本新鲜度提醒 ---
if [ -f "笑樱_galgame台本.md" ] && [ -f "src/generated/script.json" ]; then
  if [ "笑樱_galgame台本.md" -nt "src/generated/script.json" ]; then
    echo
    echo "  ⚠️ 台本比 script.json 新！先跑： npm run compile   （本次仍按旧脚本出包）"
  fi
fi

if [ "$SKIP_BUILD" = "0" ]; then
  echo
  echo "[1/5] manifest（素材有变时刷新）"
  "$NODE" --experimental-strip-types tools/compiler/manifest.ts

  echo
  echo "[2/5] tsc --noEmit"
  "$NODE" node_modules/typescript/bin/tsc --noEmit
  echo "  类型检查通过"

  echo
  echo "[3/5] vite build → dist/"
  "$NODE" node_modules/vite/bin/vite.js build
else
  echo
  echo "[1-3/5] 已跳过前端构建（--skip-build），沿用现有 dist"
fi

echo
echo "[4/5] cargo build --release --features custom-protocol"
cd src-tauri
CARGO_INCREMENTAL=0 cargo build --release --features custom-protocol
cd "$ROOT"

echo
echo "[5/5] 探针"
if [ "$DEPLOY" = "1" ]; then
  "$PY" tools/exe_probe.py --deploy
else
  "$PY" tools/exe_probe.py
fi

T1=$(date +%s)
echo
echo "==========================================================================="
echo "完成，耗时 $(( T1 - T0 )) s"
echo "==========================================================================="
