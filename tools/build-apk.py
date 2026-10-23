# -*- coding: utf-8 -*-
"""《笑樱》Android APK 打包（Python 版，替代 tools/build-apk.ps1 —— 主人要求不用 PowerShell）。

与 ps1 版的三条同源约束：
  1. **中文项目路径会让 Gradle Kotlin DSL 卡死** ⇒ 先同步到纯 ASCII 副本（默认 D:/Android/build/xy）再构建。
  2. 本机非管理员 → 不能建符号链接 ⇒ 用 junction 挂 node_modules；.so 与 dist **手动真拷贝**进 jniLibs/assets，
     并让 gradle `-x` 跳过 rust* 任务。
  3. vite build 会清空 dist（触发批量删除保护）⇒ 先把已构建好的 dist 同步进副本。

★ 2026-09-19 新增「复用 .so」快路径：若 `CARGO_TARGET_DIR/.../libxiaoying_lib.so` 存在
   且比 `src-tauri`（不含 gen/target）的所有源文件都新 ⇒ 跳过 Rust 交叉编译（省 10+ 分钟）。
   要强制重编：`--rebuild-rust`。

用法：python tools/build-apk.py [--rebuild-rust] [--skip-sync]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLONE = r'D:\Android\build\xy'
JAVA_HOME = r'D:\Android\jdk-17.0.20.1+1'
ANDROID_HOME = r'D:\Android\Sdk'
NDK_HOME = r'D:\Android\Sdk\ndk\27.0.12077973'
GRADLE_HOME = r'D:\Android\gradle-home'
CARGO_TARGET = r'D:\Android\build\target-xiaoying'
RUST_BIN = r'C:\Users\zbj\.rustup\toolchains\stable-x86_64-pc-windows-msvc\bin'


def _find_node() -> str:
    """定位 node.exe（★ 2026-10-23：曾硬编码 `node\versions\22.22.2-3\node.exe`，
    而 managed 目录实际是 `22.22.2-6` —— 后缀随下载批次变，硬编码必炸，
    症状是 subprocess 抛 `FileNotFoundError: [WinError 2]`，看着像缺文件其实是 exec 路径失效）。

    优先级：环境变量 NODE_EXE ＞ managed versions 下最新一版 ＞ PATH 里的 node。
    """
    env_node = os.environ.get('NODE_EXE')
    if env_node and os.path.exists(env_node):
        return env_node
    base = r'C:\Users\zbj\.workbuddy\binaries\node\versions'
    if os.path.isdir(base):
        for d in sorted(os.listdir(base), reverse=True):
            exe = os.path.join(base, d, 'node.exe')
            if os.path.exists(exe):
                return exe
    return shutil.which('node') or 'node'


NODE = _find_node()

SO = os.path.join(CARGO_TARGET, 'aarch64-linux-android', 'debug', 'libxiaoying_lib.so')
LOG_DIR = os.path.join(ROOT, '.workbuddy', 'tmp')
SKIP_DIRS = {'node_modules', '.workbuddy', '.git', 'dist', 'target', 'gen-android-build',
             'downloads', '素材基准', '.shots'}
SKIP_FILES = {'笑樱-测试版.exe', '笑樱-测试版.apk'}


def log(msg: str) -> None:
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def make_env() -> dict:
    env = dict(os.environ)
    env['JAVA_HOME'] = JAVA_HOME
    env['ANDROID_HOME'] = ANDROID_HOME
    env['NDK_HOME'] = NDK_HOME
    env['GRADLE_USER_HOME'] = GRADLE_HOME
    env['CARGO_TARGET_DIR'] = CARGO_TARGET
    env['CARGO_INCREMENTAL'] = '0'
    env['PATH'] = ';'.join([
        r'C:\Windows\System32', RUST_BIN, r'C:\Users\zbj\.cargo\bin',
        os.path.join(JAVA_HOME, 'bin'), env.get('PATH', ''),
    ])
    return env


def ignore(dirname: str, names: list) -> set:
    out = set()
    for n in names:
        if n in SKIP_DIRS or n in SKIP_FILES:
            out.add(n)
        elif n.endswith('_试听台.html') or n.endswith('对照台.html'):
            out.add(n)
    return out


def sync(dst_root: str, src_root: str) -> None:
    """只覆盖/新增，不删除目标多余项（避免触发批量删除保护）。"""
    for r, ds, fs in os.walk(src_root):
        ds[:] = [d for d in ds if d not in ignore(r, ds)]
        rel = os.path.relpath(r, src_root)
        target_dir = os.path.join(dst_root, rel) if rel != '.' else dst_root
        os.makedirs(target_dir, exist_ok=True)
        for f in fs:
            if f in ignore(r, fs):
                continue
            s = os.path.join(r, f)
            d = os.path.join(target_dir, f)
            try:
                if not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d) \
                        or os.path.getsize(s) != os.path.getsize(d):
                    shutil.copy2(s, d)
            except OSError as e:
                log(f'  跳过 {f}: {e}')


def run(cmd: list, cwd: str, env: dict, label: str) -> None:
    """跑一步外部命令，输出**实时写日志文件**。

    ★ 2026-10-23：原实现用 `capture_output=True` 把输出全憋在内存里 —— 首次 Android
    交叉编译一跑就是十几分钟，中途既看不到进度、也没法判断「在编」还是「卡死」
    （实测卡死 31 分钟，靠「target 里最后一个产物时间不动」才发现）。
    改成 stdout/stderr 直接落盘：全程可追，卡住时能看最后一行，失败时打尾部。
    """
    log(f'▶ {label}')
    os.makedirs(LOG_DIR, exist_ok=True)
    safe = re.sub(r'[^\w\u4e00-\u9fff-]', '-', label)
    logpath = os.path.join(LOG_DIR, f'apk-{safe}.log')
    with open(logpath, 'w', encoding='utf-8', errors='replace') as lf:
        p = subprocess.run(cmd, cwd=cwd, env=env,
                           stdout=lf, stderr=subprocess.STDOUT)
    with open(logpath, encoding='utf-8', errors='replace') as lf:
        lines = lf.read().strip().splitlines()
    for line in lines[-12:]:
        print('   ' + line, flush=True)
    if p.returncode != 0:
        for line in lines[-25:]:
            print('   ! ' + line, flush=True)
        raise SystemExit(f'{label} 失败 exit={p.returncode}（完整日志：{logpath}）')
    log(f'   日志：{logpath}')


def newest_source_mtime() -> float:
    newest = 0.0
    base = os.path.join(ROOT, 'src-tauri')
    for r, ds, fs in os.walk(base):
        parts = r.split(os.sep)
        if 'gen' in parts or 'target' in parts:
            continue
        for f in fs:
            newest = max(newest, os.path.getmtime(os.path.join(r, f)))
    return newest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild-rust', action='store_true')
    ap.add_argument('--skip-sync', action='store_true')
    ap.add_argument('--no-strip', action='store_true', help='不 strip .so（调试符号保留，包更大）')
    ap.add_argument('--keep-stale', action='store_true', help='不清空副本 assets 的旧内容')
    ap.add_argument('--drop-char', action='store_true', default=True,
                    help='把未使用的立绘 char/*.png 排除出包（默认开）')
    ap.add_argument('--keep-char', dest='drop_char', action='store_false',
                    help='保留立绘（包大 ~54 MB）')
    args = ap.parse_args()

    env = make_env()
    android_dir = os.path.join(CLONE, 'src-tauri', 'gen', 'android')
    assets_dir = os.path.join(android_dir, 'app', 'src', 'main', 'assets')
    jni_dir = os.path.join(android_dir, 'app', 'src', 'main', 'jniLibs', 'arm64-v8a')

    log(f'项目 {ROOT}')
    log(f'ASCII 副本 {CLONE}')

    # 1. 同步源码 → 副本
    if not args.skip_sync:
        log('[1/5] 同步源码（排除 node_modules/素材基准/dist/target…）')
        os.makedirs(CLONE, exist_ok=True)
        sync(CLONE, ROOT)
        print('   OK', flush=True)

        # ★★ 2026-09-20 补：`public/` 必须**先删后拷**，不能只靠上面的 sync。
        #   原因（实测踩过）：sync 只增不删 ⇒ 副本 public 会积累历次构建的旧文件。
        #   而 `.so` 需重编时 `tauri android build` 会用**副本的 public** 重跑 vite build
        #   ⇒ 旧文件被打进 APK：语音重编号那次多出 425 个旧 `v_*.ogg`（+11.8 MB），
        #     CG 多 9 个、BG 多 5 个。
        #   ⚠️ 更危险的是 sync 的比较条件含 `mtime(src) > mtime(dst)`，而 rename/copy2
        #      都保留 mtime ⇒ 可能**不覆盖**同名文件，留下「名字对、内容错」的音频。
        #   ⇒ 整体镜像，保证副本 == 本机。
        log('[1c] 镜像 public/（先删后拷）')
        src_pub = os.path.join(ROOT, 'public')
        clone_pub = os.path.join(CLONE, 'public')
        if os.path.isdir(src_pub):
            if os.path.isdir(clone_pub):
                shutil.rmtree(clone_pub)
            shutil.copytree(src_pub, clone_pub)
            n = sum(len(fs) for _, _, fs in os.walk(clone_pub))
            print(f'   OK（{n} 个文件）', flush=True)

    # node_modules junction
    nm = os.path.join(CLONE, 'node_modules')
    if not os.path.exists(nm):
        log('[1b] 建立 node_modules junction')
        subprocess.run(['cmd', '/c', 'mklink', '/J', nm, os.path.join(ROOT, 'node_modules')],
                       capture_output=True)

    # 2. dist → 副本
    # ★ 2026-09-19 瘦身：**必须先删掉副本里的旧 dist**再拷。
    #   原先用 copytree(dirs_exist_ok=True) 只覆盖不删除 ⇒ 历次构建的旧资源（cg/bg 的 PNG，
    #   转 webp 之前的残留，58 MB）会一直躺在副本 dist 里，再被整包搬进 APK。
    log('[2/5] 同步 dist → 副本（先清旧 dist）')
    clone_dist = os.path.join(CLONE, 'dist')
    if os.path.isdir(clone_dist):
        shutil.rmtree(clone_dist)
    shutil.copytree(os.path.join(ROOT, 'dist'), clone_dist)
    # gradle 的 assets 合并中间产物同样会留旧的，一并清掉（否则源码清了包里还在）
    for stale in ('assets', 'compressed_assets'):
        p = os.path.join(CLONE, 'src-tauri', 'gen', 'android', 'app', 'build', 'intermediates', stale)
        if os.path.isdir(p):
            shutil.rmtree(p)
            log(f'  清掉 gradle 中间产物 intermediates/{stale}')
    print('   OK', flush=True)

    # 3. BuildTask.kt 修正（写死的 node 路径 / tauri 脚本入口 / Windows fallback 拼错后缀）
    log('[3/5] 校正 BuildTask.kt')
    bt = os.path.join(android_dir, 'buildSrc', 'src', 'main', 'java', 'com', 'qingyu',
                      'xiaoying', 'kotlin', 'BuildTask.kt')
    if os.path.exists(bt):
        with open(bt, encoding='utf-8') as f:
            c = f.read()
        c = c.replace('\\node""""', '\\node.exe""""')
        c = re.sub(r'listOf\("tauri", "android", "android-studio-script"\)',
                   'mutableListOf("node_modules/@tauri-apps/cli/tauri.js", "android", "android-studio-script")', c)
        # ★ 2026-09-20：模板的 Windows fallback 会给已经带 `.exe` 的路径**再拼后缀**
        #   （$executable.exe / .cmd / .bat）⇒ Gradle 报
        #   「A problem occurred starting process 'command '...\node.exe.bat''」，
        #   整个 assemble 直接失败。可执行文件是**绝对路径且已含 .exe**，
        #   完全不需要猜后缀 ⇒ 把 fallback 列表清空，只跑一次原始路径。
        #   ⛔ 正则不好写（含 Kotlin 的 `$` 插值），用精确字符串替换。
        c = c.replace(
            '                val fallbacks = listOf(\n'
            '                    "$executable.exe",\n'
            '                    "$executable.cmd",\n'
            '                    "$executable.bat",\n'
            '                )\n',
            '                val fallbacks = listOf<String>()\n',
        )
        # ★ 2026-10-23：模板把 **node.exe 的绝对路径**也写死在 BuildTask.kt 里
        #   （`val executable = """…\node\versions\22.22.2-3\node.exe"""`）。managed node
        #   目录后缀会随下载批次变成 `22.22.2-6`，路径一失效 Gradle 就报
        #   「A problem occurred starting process '…node.exe'」⇒ assemble 直接失败。
        #   ⇒ 每次同步后把它校正成当前真实路径（NODE 由 `_find_node()` 解析）。
        #   ⚠️ repl 必须用 lambda：直接传字符串的话，`C:\Users\...` 里的 `\U` 会被
        #      re 当成模板转义 ⇒ `re.PatternError: bad escape \U`（已实测踩过一次）。
        c = re.sub(r'val executable = """[^"]*"""',
                   lambda m: 'val executable = """%s"""' % NODE, c)
        with open(bt, 'w', encoding='utf-8') as f:
            f.write(c)
        print('   OK', flush=True)

    # 4. Rust .so：能复用就复用
    reuse = False
    if not args.rebuild_rust and os.path.exists(SO):
        so_mt = os.path.getmtime(SO)
        src_mt = newest_source_mtime()
        reuse = so_mt > src_mt
        log(f'[4/5] .so 检查：so={time.strftime("%m-%d %H:%M", time.localtime(so_mt))} '
            f'src={time.strftime("%m-%d %H:%M", time.localtime(src_mt))} ⇒ '
            f'{"复用（跳过交叉编译）" if reuse else "需重编"}')
    if not reuse:
        log('[4/5] Rust 交叉编译（aarch64，约 10+ 分钟）')
        # ★ 2026-09-20：这条路有两个已知失败点，都不代表「.so 没编出来」：
        #   ① Gradle 的 rustBuildArm64Debug → android-studio-script 需 IDE 握手，
        #      命令行下必然 ConnectionRefused；Tauri CLI 会在编完 .so 之后才走到它。
        #   ② BuildTask.kt 的 Windows fallback 会拼出 `node.exe.bat`（见该文件 25-29 行），
        #      Gradle 报「command '...node.exe.bat' 启动失败」。
        #   ⇒ 失败后只要 .so **比开始前更新**，就说明交叉编译已完成，继续往下走。
        so_before = os.path.getmtime(SO) if os.path.exists(SO) else 0.0
        try:
            run([NODE, 'node_modules/@tauri-apps/cli/tauri.js', 'android', 'build', '--apk',
                 '--debug', '--target', 'aarch64'], cwd=CLONE, env=env, label='tauri android build')
        except SystemExit:
            so_after = os.path.getmtime(SO) if os.path.exists(SO) else 0.0
            if so_after > so_before:
                log(f'  ⚠ tauri android build 非零退出，但 .so 已更新'
                    f'（{time.strftime("%H:%M:%S", time.localtime(so_after))}）⇒ 视为编译成功，继续')
            else:
                raise
    if not os.path.exists(SO):
        raise SystemExit(f'缺少 {SO}')
    log(f'  .so {os.path.getsize(SO) / 1048576:.1f} MB')

    # 5. 铺 jniLibs / assets
    log('[5/5] 铺 jniLibs + assets')
    os.makedirs(jni_dir, exist_ok=True)
    # ★ 2026-09-19 瘦身：先把 .so strip 掉再拷（221 MB → ~121 MB）
    so_to_copy = SO
    if not args.no_strip:
        strip = os.path.join(NDK_HOME, 'toolchains', 'llvm', 'prebuilt', 'windows-x86_64',
                             'bin', 'llvm-strip.exe')
        if os.path.exists(strip):
            stripped = os.path.join(ROOT, '.workbuddy', 'tmp', 'libxiaoying_lib_stripped.so')
            os.makedirs(os.path.dirname(stripped), exist_ok=True)
            p = subprocess.run([strip, '--strip-all', '-o', stripped, SO],
                               capture_output=True, text=True)
            if p.returncode == 0 and os.path.exists(stripped):
                before = os.path.getsize(SO) / 1048576
                after = os.path.getsize(stripped) / 1048576
                log(f'  strip --strip-all: {before:.1f} MB → {after:.1f} MB')
                so_to_copy = stripped
            else:
                log(f'  ⚠ strip 失败（rc={p.returncode}），用未 strip 的 .so')
        else:
            log('  ⚠ 未找到 llvm-strip，跳过 strip')
    shutil.copy2(so_to_copy, os.path.join(jni_dir, 'libxiaoying_lib.so'))

    # ★ 2026-09-19 瘦身：**先清空副本 assets**（保留 tauri CLI 生成的 tauri.conf.json），
    #   否则历次构建的陈旧资源会越积越多（本次实测有 58 MB 旧 PNG：cg/bg 转 webp 前的残留）。
    if not args.keep_stale:
        purged = 0
        for entry in os.listdir(assets_dir):
            if entry == 'tauri.conf.json':
                continue
            p = os.path.join(assets_dir, entry)
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            purged += 1
        log(f'  已清理副本 assets 旧内容 {purged} 项')
    dist_dir = os.path.join(CLONE, 'dist')
    skipped_char = 0
    for r, ds, fs in os.walk(dist_dir):
        rel = os.path.relpath(r, dist_dir)
        # ★ 立绘（char/*.png, 53.8 MB）引擎已不使用（manifest char/slot 均为 0 键）⇒ 不进包；
        #   母版仍保留在 public/assets/char，桌面端要恢复只需去掉 --drop-char。
        if args.drop_char and (rel == os.path.join('assets', 'char')
                               or rel.startswith(os.path.join('assets', 'char') + os.sep)):
            skipped_char += len(fs)
            continue
        td = os.path.join(assets_dir, rel) if rel != '.' else assets_dir
        os.makedirs(td, exist_ok=True)
        for f in fs:
            # ★ 瘦身：sourcemap（*.map，约 3-4 MB）只在开发期有用，不进 APK
            if f.endswith('.map'):
                continue
            shutil.copy2(os.path.join(r, f), os.path.join(td, f))
    if skipped_char:
        log(f'  已排除未使用的立绘 {skipped_char} 个文件（char/）')
    print('   OK', flush=True)

    # 6. Gradle 打包（跳过 rust* 任务）
    # ★★ 2026-09-20 补：**先删上次的 APK 与打包中间产物**。
    #   实测不删会导致文件里残留一整份旧 zip 数据：成品 350.4 MB，而中央目录只引用
    #   224.9 MB ⇒ **125.2 MB 纯垃圾**（本地文件头出现 3937 次 vs 条目 1868 个）。
    #   根因＝增量打包复用了上次的输出文件，条目被重写但旧数据没被截断
    #   （上一版 .so 200.5 MB → 本版 145.7 MB，差值正是残留主要来源）。
    #   功能无害（读取以中央目录为准），但白胖 125 MB，装机与传输都翻倍。
    stale_apk = os.path.join(android_dir, 'app', 'build', 'outputs', 'apk', 'universal',
                             'debug', 'app-universal-debug.apk')
    if os.path.exists(stale_apk):
        os.remove(stale_apk)
        log('  已删除上次的成品 APK')
    stale_pkg = os.path.join(android_dir, 'app', 'build', 'intermediates', 'apk')
    if os.path.isdir(stale_pkg):
        shutil.rmtree(stale_pkg)
        log('  已清掉 intermediates/apk')

    log('[6/6] Gradle assembleUniversalDebug')
    gradlew = os.path.join(android_dir, 'gradlew.bat')
    run([gradlew, '--project-dir', android_dir, 'assembleUniversalDebug',
         '-x', ':app:rustBuildUniversalDebug', '-x', ':app:rustBuildArm64Debug',
         '-x', ':app:rustBuildArmDebug', '-x', ':app:rustBuildX86Debug',
         '-x', ':app:rustBuildX86_64Debug'], cwd=android_dir, env=env, label='gradle assemble')

    out_apk = os.path.join(android_dir, 'app', 'build', 'outputs', 'apk', 'universal',
                           'debug', 'app-universal-debug.apk')
    if not os.path.exists(out_apk):
        raise SystemExit(f'未找到 APK：{out_apk}')
    final = os.path.join(ROOT, '笑樱-测试版.apk')
    shutil.copy2(out_apk, final)
    log(f'完成 -> {final}  {os.path.getsize(final) / 1048576:.1f} MB')

    # ★★ 2026-09-20 补（第 7 步）：出包后核对包内素材数量 == 本机。
    #   专防「副本旧文件被打进包」——那次语音多 425 个、CG 多 9 个、BG 多 5 个，
    #   包体虚胖 11.8 MB，而构建日志里**一点异常都没有**。
    log('[7/7] 包内素材数量核对')
    import zipfile
    checked = 0
    bad = []
    for sub in ('audio/voice', 'audio/bgm', 'audio/se', 'cg', 'bg'):
        src_dir = os.path.join(ROOT, 'public', 'assets', sub)
        if not os.path.isdir(src_dir):
            continue
        # ⚠️ Gradle 打包 assets 时会**跳过点开头的文件**（如 .gitkeep）
        #    ⇒ 本机计数必须一并排除，否则永远差 1、误报「副本没清干净」
        want = len([f for f in os.listdir(src_dir)
                    if os.path.isfile(os.path.join(src_dir, f)) and not f.startswith('.')])
        prefix = f'assets/assets/{sub}/'
        with zipfile.ZipFile(final) as z:
            got = len([n for n in z.namelist()
                       if n.startswith(prefix) and not n.endswith('/')])
        checked += 1
        mark = 'ok' if got == want else '!'
        if got != want:
            bad.append((sub, want, got))
        print(f'   [{mark}] {sub:14s} 本机 {want:5d} ｜ 包内 {got:5d}', flush=True)
    if bad:
        log('⚠️ 包内素材数量与本机不一致（副本旧文件未清理干净）：'
            + '、'.join(f'{a} 本机{b} 包内{c}' for a, b, c in bad))
    else:
        log(f'  ✓ {checked} 类素材数量全部一致')

    # ★★ zip 自洽性：条目压缩后总量 vs 文件实际大小。
    #   差额大 ⇒ 文件里残留了不被引用的旧数据（增量打包未截断），见第 6 步注释。
    with zipfile.ZipFile(final) as z:
        comp = sum(i.compress_size for i in z.infolist())
        n_entry = len(z.infolist())
    real = os.path.getsize(final)
    waste = (real - comp) / 1048576
    log(f'  zip 自洽：条目压缩后 {comp / 1048576:.1f} MB ｜ 文件 {real / 1048576:.1f} MB '
        f'｜ 差额 {waste:.1f} MB（{n_entry} 条目）')
    if waste > 5:
        log(f'  ⚠️ 残留 {waste:.1f} MB 不被引用的旧数据 —— 检查第 6 步的删旧产物是否生效')
    return 0


if __name__ == '__main__':
    sys.exit(main())
