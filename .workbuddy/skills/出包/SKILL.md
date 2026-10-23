---
name: 出包
version: "1.1"
display_name: "出包（桌面 exe ＋ Android APK）"
description: "《笑樱》的打包出口。①桌面 exe＝`node tools/build-exe.mjs`（manifest → tsc + vite build → cargo build --release --features custom-protocol → `tools/exe_probe.py` 探针 → 部署根目录 `笑樱-测试版.exe`）；⛔ 本机禁止 `bash` 调用（沙箱拦，fallback wsl.exe 撞黑名单）；②Android APK＝`powershell tools/build-apk.ps1`（**必须从纯 ASCII 路径发起**）。当用户说「打包 / 出包 / 出 exe / 打 exe / 生成 exe / 打 apk / 打包安卓 / 出个包」时使用。"
agent_created: true
user-invocable: true
---

# 出包（《笑樱》）

## 〇、先判断要出哪个包

| 用户说 | 走 |
|---|---|
| 「打包 exe」「出 exe」「桌面版」 | § 一 桌面 exe |
| 「打 apk」「安卓包」「手机版」 | § 二 Android APK |
| 只说「打包」 | 默认 **exe**（桌面是主线；APK 属试验分支） |

## 一、桌面 exe（一键）

```bash
node tools/build-exe.mjs                 # 完整出包 + 探针 + 部署根目录 笑樱-测试版.exe
node tools/build-exe.mjs --no-deploy     # 出包 + 探针，不覆盖根目录
node tools/build-exe.mjs --skip-build    # 沿用现有 dist，只重编 rust（调试用）
```

耗时约 **8–9 分钟**（cargo release 全量 ≈ 8 min，前端 ≈ 20 s）。**后台跑**，别在前台等超时。

### ⛔ 不要用 `bash tools/build-exe.sh`（同链路的旧版）

**本机沙箱把任何显式 `bash` 调用都拦掉**：宿主 shim 先因 PATH 缺 coreutils 报
`dirname: command not found` → `cd: null directory`，随后 fallback 到 **wsl.exe**，正撞程序黑名单：

```
PROGRAM BLOCKED BY SECURITY POLICY - wsl.exe (C:\Program Files\WSL\wsl.exe)
```

表现为**1 秒即退、输出乱码**。`bash -c 'echo hi'` 也同样被拦 —— 不是脚本的问题，是 `bash` 这个入口本身。
→ **一键入口一律用 Node 版**；`build-exe.sh` 只留作链路文档/换机参考。需要 shell 时也别调 `bash`，
直接把命令**逐条**交给 Bash 工具执行（逐条可行，已验证）。

### 前置：台本改了要先编译

脚本**不会**替你跑 `npm run compile` —— 台本编译是有语义的步骤，不该被出包顺手带过。
但脚本会检查 `笑樱_galgame台本.md` 是否比 `src/generated/script.json` 新，新了会**告警**。
→ 见到告警先跑 `npm run compile`，再重新出包。

### 手动链路（脚本坏了/要改链路时的兜底）

```bash
NODE=C:/Users/zbj/.workbuddy/binaries/node/versions/22.22.2-3/node.exe
export PATH="/c/Users/zbj/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin:/c/Windows/System32:$PATH"

"$NODE" --experimental-strip-types tools/compiler/manifest.ts   # 素材有变时
"$NODE" node_modules/typescript/bin/tsc --noEmit
"$NODE" node_modules/vite/bin/vite.js build                     # → dist/
cd src-tauri && CARGO_INCREMENTAL=0 cargo build --release --features custom-protocol
cd .. && python tools/exe_probe.py --deploy
```

### ⚠️ 三条铁律（本机踩出来的，别改回去）

1. **PATH 必须把「rustup 工具链真身 bin」顶到最前**
   `~/.cargo/bin/rustc.exe` 是 **shim**，在 Git Bash 下报 `STATUS_DLL_NOT_FOUND`。
   真身＝`~/.rustup/toolchains/stable-x86_64-pc-windows-msvc/bin/`（cargo 会就近找 rustc，所以顶 cargo 所在目录即可）。
2. **绕过 tauri-cli**：`tauri build`（2.11.4）在本机 panic（`rust.rs` unwrap；`--config`/unset SHELL 均救不了）
   → 直接 `cargo build`。代价：没有 NSIS 安装包，只出裸 exe（当前够用）。
3. **裸跑 cargo 必须显式 `--features custom-protocol`**
   否则 `generate_context!` 不嵌 `dist/` → exe 是**黑屏空壳**（该 feature 已在 `Cargo.toml` 声明）。

### 探针怎么判「真的嵌进去了」（`tools/exe_probe.py`）

release 内嵌资源经 **brotli 压缩**，压缩区里 grep 台词/文件名**必 miss**。有效判据三条：

1. **资源路径表**（明文）里能看到入口 chunk 与素材条目 —— 主判据；
2. **时序链**：`script.json ≤ manifest ≤ dist ≤ exe`（后半必须晚于前半）；
3. **体积增量** ≈ 新增素材体积（例：本次 +22.8 MB ≈ 10 首 BGM）。

**⚠️ 入口 chunk hash 与上一版相同 ≠ 异常**：`src/` 未改时 rollup hash 就是一样的；
`manifest.generatedAt` 会被 tree-shaking 去掉（无人引用），所以重跑 manifest 也不影响 hash。
**判断素材有没有更新，看资源路径表里的条目数，不看 chunk hash。**

## 二、Android APK

```powershell
powershell -File tools\build-apk.ps1
```

- **★★ 铁律：Android 构建必须从纯 ASCII 路径发起**（脚本用 robocopy 副本 `D:\Android\build\xy`）。
  中文路径在这条链上爆过 3 次：①NDK lld 编码坏 ②AGP 拒非 ASCII（`android.overridePathCheck=true`）
  ③Gradle Kotlin DSL accessors 生成 60 s 超时（连带报「compileSdk 未指定」＝假线索）。
- 本机非管理员、未开开发者模式 → **禁止符号链接**：手动 Copy `.so` 与 dist ＋ gradle `-x` 跳过 `rust*` 任务。
  ⚠️ 跳过后**必须自己铺 assets**，否则出的是只有壳没前端的空包（曾出 239 MB 空包，补齐 assets 后 358 MB 才对）。
- 产物：根目录 `笑樱-测试版.apk`（debug universal，arm64-only）。
- 细节见 `tools/build-apk.ps1` 头注释 ＋ `.workbuddy/memory/MEMORY.md` 的 Android 段。

## 三、出完包

- 提醒主人**实机跑一次**：桌面重点验 BGM 播放/循环、F11 全屏、立绘出场动画；APK 验 WebGL/横屏/触控。
- 二进制探针**不能替代目验**：黑屏这类问题探针查不出来（2026-09-13 就是主人实测才发现的）。
