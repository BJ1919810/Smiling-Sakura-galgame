// ============================================================================
// 笑樱 · 桌面 exe 一键出包（Node 版 —— 本机首选入口）
//
//   node tools/build-exe.mjs                 # 完整出包 + 探针 + 部署根目录
//   node tools/build-exe.mjs --no-deploy     # 出包 + 探针，不覆盖根目录
//   node tools/build-exe.mjs --skip-build    # 沿用现有 dist，只重编 rust（调试用）
//
// ★ 为什么是 Node 版，而不是 bash 版（tools/build-exe.sh 仍保留作参考）
//   本机沙箱把 **任何显式 `bash` 调用**都拦掉：宿主 shim 先因 PATH 缺 coreutils
//   报 `dirname: command not found`，随后 fallback 到 wsl.exe，正撞程序黑名单
//   → `bash tools/build-exe.sh` 必失败（1 秒即退，输出乱码）。
//   node / python 不受影响，所以一键入口改用 Node 串 spawnSync。
//
// 链路：manifest → tsc --noEmit → vite build(dist) → cargo build --release
//       → 探针（tools/exe_probe.py）→ 部署根目录 笑樱-测试版.exe
//
// ⚠️ 三条铁律（都是踩出来的，别改回去）：
//   ① rustup 的 cargo/rustc **shim** 在 Git Bash 下报 STATUS_DLL_NOT_FOUND
//      → PATH 必须把「工具链真身 bin」顶到最前（cargo 会就近找 rustc）
//   ② tauri-cli 2.11.4 的 `build` 子命令在本机 panic（rust.rs unwrap）
//      → 绕过 CLI，直接跑 cargo build；NSIS 安装包略过，只出裸 exe（够用）
//   ③ 裸跑 cargo **必须显式** --features custom-protocol
//      → 否则 generate_context! 不嵌 dist，出的是黑屏空壳
//
// 前置：台本若改过，先 `npm run compile` 重编 script.json（本脚本不代劳）。
// ============================================================================

import { spawnSync } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const TOOLCHAIN = 'C:\\Users\\zbj\\.rustup\\toolchains\\stable-x86_64-pc-windows-msvc\\bin';
const CARGO = path.join(TOOLCHAIN, 'cargo.exe');
const NODE = process.execPath;
const PY = 'C:\\Users\\zbj\\.workbuddy\\binaries\\python\\versions\\3.13.12\\python.exe';

const argv = process.argv.slice(2);
const DEPLOY = !argv.includes('--no-deploy');
const SKIP_BUILD = argv.includes('--skip-build');
for (const a of argv) {
  if (!['--no-deploy', '--skip-build'].includes(a)) {
    console.error(`未知参数：${a}`);
    process.exit(2);
  }
}

// PATH：工具链真身顶到最前（铁律①），System32 兜底，其余原样
const env = {
  ...process.env,
  PATH: [TOOLCHAIN, 'C:\\Windows\\System32', process.env.PATH || ''].join(';'),
  CARGO_INCREMENTAL: '0',
};

const T0 = Date.now();
const line = '='.repeat(75);
console.log(line);
console.log(`笑樱 · exe 出包   ${new Date().toLocaleString('sv-SE')}`);
console.log(line);

/** 跑一步，失败即整体退出 */
function run(label, cmd, args, cwd = ROOT) {
  console.log(`\n${label}`);
  const r = spawnSync(cmd, args, { cwd, env, stdio: 'inherit', shell: false });
  if (r.error) {
    console.error(`  [x] 启动失败：${r.error.message}`);
    process.exit(1);
  }
  if (r.status !== 0) {
    console.error(`  [x] 退出码 ${r.status}`);
    process.exit(r.status ?? 1);
  }
}

// --- 0. 工具链自检（shim 坑：必须指向真身） ---
for (const exe of [CARGO, PY]) {
  if (!existsSync(exe)) {
    console.error(`[x] 找不到：${exe}`);
    process.exit(1);
  }
}
run('[0/5] 工具链自检', CARGO, ['--version']);
console.log(`  node ${process.version}`);

// --- 1. 台本新鲜度提醒 ---
const scenario = path.join(ROOT, '笑樱_galgame台本.md');
const script = path.join(ROOT, 'src', 'generated', 'script.json');
if (existsSync(scenario) && existsSync(script) &&
    statSync(scenario).mtimeMs > statSync(script).mtimeMs) {
  console.log('\n  ⚠️ 台本比 script.json 新！先跑： npm run compile   （本次仍按旧脚本出包）');
}

if (!SKIP_BUILD) {
  run('[1/5] manifest（素材有变时刷新）',
      NODE, ['--experimental-strip-types', 'tools/compiler/manifest.ts']);
  run('[2/5] tsc --noEmit', NODE, ['node_modules/typescript/bin/tsc', '--noEmit']);
  run('[3/5] vite build → dist/', NODE, ['node_modules/vite/bin/vite.js', 'build']);
} else {
  console.log('\n[1-3/5] 已跳过前端构建（--skip-build），沿用现有 dist');
}

run('[4/5] cargo build --release --features custom-protocol',
    CARGO, ['build', '--release', '--features', 'custom-protocol'],
    path.join(ROOT, 'src-tauri'));

run('[5/5] 探针', PY,
    ['tools/exe_probe.py', ...(DEPLOY ? ['--deploy'] : [])]);

console.log(`\n${line}`);
console.log(`完成，耗时 ${Math.round((Date.now() - T0) / 1000)} s`);
console.log(line);
