# -*- coding: utf-8 -*-
"""APK 包内前端核验（2026-09-20 修：路径应为 assets/assets/…）。

Tauri Android 的资源会多套一层：`assets/assets/index-*.js`（不是 `assets/index-*`）。
上一版探针写成 `assets/index-...` ⇒ 什么都没匹配到，**误报「包是旧的」**。
★ 教训：探针「一个都没命中」时，先怀疑**路径/正则**，别急着下结论说包不对。

同时解释「包内 bundle 体积 ≠ 本地 dist」：`.so` 需要重编时，`tauri android build`
会在 ASCII 副本里**重跑一次 `npm run build`**，构建号时间戳变了 ⇒ hash 与新体积都不同。
⇒ 所以**不能靠 md5/体积比**，要比的是「本轮新增代码特征在不在」。
"""
import hashlib
import os
import re
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK = os.path.join(ROOT, '笑樱-测试版.apk')
DIST = os.path.join(ROOT, 'dist')

# 本轮（2026-09-20）必须出现在包内的特征
MUST_HAVE = {
    '长按手势 touchstart': 'touchstart',
    '长按禁区选择器': 'button, a, input, textarea, select',
    '鉴赏模式 contextmenu': 'contextmenu',
    '全局禁选 -webkit-user-select': '-webkit-user-select',
    '禁长按弹菜单 touch-callout': 'touch-callout',
    '禁高亮 tap-highlight': 'tap-highlight-color',
    '禁拖拽 user-drag': 'user-drag',
    # ★ UI 缩放第二轮（自动因子 + 玩家倍率）—— 少了这几条说明打的还是「纯 px 偏大」那一版
    'UI 缩放 --ui-zoom': '--ui-zoom',
    '自动因子 --ui-auto': '--ui-auto',
    '玩家倍率 --ui-user': '--ui-user',
    '设置项「界面大小」': '界面大小',
}
# 必须**不在**包内的旧痕迹
MUST_NOT = {
    '旧措辞「正式美术制作中」': '正式美术制作中',
    '旧 UI 缩放变量 --ui-rem': '--ui-rem',
    '旧设计画布 transform': 'scale(var(--ui-scale',
}


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    print(f'APK {os.path.getsize(APK) / 1048576:.1f} MB')
    with zipfile.ZipFile(APK) as z:
        names = z.namelist()
        js_names = [n for n in names if re.search(r'(^|/)assets/index-.*\.js$', n)]
        css_names = [n for n in names if re.search(r'(^|/)assets/index-.*\.css$', n)]
        print(f'包内 index JS : {js_names}')
        print(f'包内 index CSS: {css_names}')
        assert js_names, '包内找不到 index JS —— 先怀疑路径正则'
        assert css_names, '包内找不到 index CSS'

        js_blob = b''.join(z.read(n) for n in js_names)
        css_blob = b''.join(z.read(n) for n in css_names)
        text = (js_blob + b'\n' + css_blob).decode('utf-8', errors='replace')

        local_js = [os.path.join(DIST, 'assets', os.path.basename(n)) for n in js_names]
        print()
        print('--- 包内 vs 本地 dist（仅供对照，hash 本就可能不同）---')
        for n, lp in zip(js_names, local_js):
            packed = z.read(n)
            print(f'  包内 {os.path.basename(n)}  {len(packed)} B  md5 {md5(packed)[:12]}')
            if os.path.exists(lp):
                lb = open(lp, 'rb').read()
                print(f'  本地 {os.path.basename(lp)}  {len(lb)} B  md5 {md5(lb)[:12]}'
                      f'  ⇒ {"一致" if packed == lb else "不同（预期，副本重跑过 build）"}')
            else:
                print(f'  本地缺失 {os.path.basename(lp)}（dist 又构建过，属正常）')

        ok = True
        print()
        print('--- 必须存在 ---')
        for label, needle in MUST_HAVE.items():
            hit = needle in text
            ok &= hit
            print(f'  {"✓" if hit else "✗"} {label}')

        print('--- 必须不存在 ---')
        for label, needle in MUST_NOT.items():
            hit = needle in text
            ok &= not hit
            print(f'  {"✓" if not hit else "✗"} {label}')

        # CSS 里的禁选规则要成组出现（光有字符串不够，看是否有 none 值）
        css_text = css_blob.decode('utf-8', errors='replace')
        has_rule = '-webkit-user-select:none' in css_text.replace(' ', '')
        print()
        print(f'  {"✓" if has_rule else "✗"} CSS 里有实际生效的 user-select:none 规则')
        ok &= has_rule

        char_n = sum(1 for n in names if n.startswith('assets/assets/char/'))
        print(f'  {"✓" if char_n == 0 else "✗"} 包内立绘 char/ 文件数 = {char_n}（应为 0）')
        ok &= char_n == 0

    print()
    print('★ 结论：' + ('✓ 包内就是本轮最新前端，可以装' if ok else '✗ 有项不符，先别装！'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
