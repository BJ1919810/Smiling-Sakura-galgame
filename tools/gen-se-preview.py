# -*- coding: utf-8 -*-
"""
《笑樱》SE 试听台生成器 —— 把 30 个 ogg **内联进单文件 HTML**（base64 data URI）。

★ 为什么要内联（2026-09-18 踩坑）
  WorkBuddy 的内置预览面板是把 HTML **内联注入** 到一个 wrapper 里的，
  ⇒ **相对路径的基准不再是文件所在目录**，`public/assets/audio/se/se_001.ogg`
  会解析到 `127.0.0.1:8498/static-html/<hash>/...` 之外 → 全部 404。
  （现象：点行有反应、布局会变，但永远不响。）
  同理 `file://` 直接双击也会因为 Chrome 的本地文件 CORS 限制播不了 ogg。

  ⇒ 唯一稳的做法：**把 ogg 以 data URI 内联**。单文件、离线可听、双击即可、丢给谁都能听。

★ 代价
  30 个 ogg 共 576 KB → base64 后约 **770 KB**，最终 HTML ≈ 780 KB。
  对本地试听台完全无所谓（不进游戏包）。

用法：
  python tools/gen-se-preview.py                 # 输出到项目根 SE试听台.html
  python tools/gen-se-preview.py --out a.html    # 自定义输出路径
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "src" / "generated" / "assets.manifest.json"
SE_DIR = ROOT / "public" / "assets" / "audio" / "se"
DEFAULT_OUT = ROOT / "SE试听台.html"

# 分类（人工标注，用于试听台的筛选按钮）
# ⚠️ 这里必须与 manifest 的 SE 键**完全对应**：多写一个不存在的键不会报错，
#    但会让分类计数对不上（2026-09-18「哨声、风声」合并后差点漏删）。
CAT = {
    "环境": ["教室嘈杂", "风声", "哨声、风", "广播站音乐（远处）",
             "周围的议论声", "水声", "一阵风", "风卷枯叶"],
    "动作": ["脚步声", "书页轻响", "翻书声", "自动贩卖机",
             "踩过落叶的脚步声（由远及近）", "糖纸轻响", "椅子向后一撞",
             "脚步（犹豫・拖沓）", "闷响", "台灯开关", "脚步（由远及近）",
             "脚步声远去", "粉笔"],
    "提示": ["上课预备铃", "午休结束的预备铃", "发令哨", "心跳",
             "哨声", "放学铃", "午休结束铃", "提示音"],
}
KEY2CAT = {k: c for c, ks in CAT.items() for k in ks}

# ★ 健壮性自检：分类表键集必须与 manifest 的 SE 键集完全一致（在 main 里断言）。

# ============================================================
# 主人听感验收状态（2026-09-18）
# ------------------------------------------------------------
# PASS = 主人明确说「可以」的，**默认从试听台隐藏**（只听没过的，
#        避免每次试听都要在 30 行里挑）。用 --all 可强制显示全部。
# ★ 过审后请把键从 PASS 移出（或留在 PASS），别两边都写。
#
# ★★ 2026-09-18 晚（第二轮验收）：
#   本轮新增过审 6 项：哨声、风／发令哨／心跳／水声／闷响／哨声
#   ⛔「放学铃」虽已过审，但主人要求**换音阶**（威斯敏斯特四音）⇒ 必须重新验收，
#     **不进 PASS**，等主人听完新版再定。
#   ⛔「哨声、风声」已**合并进「哨声、风」**（键已从 manifest 移除），
#     这里不再列，删了才对（列一个不存在的键不会报错、但会让人误以为它还在）。
# ============================================================
PASS_KEYS: set[str] = {
    # —— 第一轮过审（12 项）——
    "上课预备铃",
    "广播站音乐（远处）",
    "自动贩卖机",
    "午休结束的预备铃",
    "一阵风",
    "脚步（犹豫・拖沓）",
    "台灯开关",
    "脚步（由远及近）",
    "脚步声远去",
    "午休结束铃",
    "粉笔",
    "提示音",
    # —— 第二轮过审（6 项）——
    "哨声、风",
    "发令哨",
    "心跳",
    "水声",
    "闷响",
    "哨声",
    # —— 第三轮过审（2 项）——
    "糖纸轻响",
    "风卷枯叶",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="生成内联版 SE 试听台")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出 HTML 路径")
    ap.add_argument("--all", action="store_true",
                    help="显示全部 30 个（默认只显示未通过验收的）")
    args = ap.parse_args()

    se = json.loads(MANIFEST.read_text(encoding="utf-8"))["se"]
    show_all = args.all

    # ★ 健壮性校验：PASS_KEYS 里的键必须真实存在于 manifest。
    #   否则会出现「这个键我没隐藏啊？」的静默失效 —— 典型场景是某键被合并/删除后
    #   忘了从 PASS_KEYS 清掉（2026-09-18「哨声、风声」合并事件就是这么差点漏的）。
    stale = sorted(PASS_KEYS - set(se))
    if stale:
        print(f"⚠️ PASS_KEYS 里有 {len(stale)} 个键不在 manifest（已废弃或拼写错）：")
        for s in stale:
            print(f"    · {s}")
        print("   ⇒ 不会隐藏任何东西，请确认后从 PASS_KEYS 删除。")

    # ★ 分类表自检：CAT 必须覆盖且仅覆盖 manifest 的全部 SE 键。
    #   这套表是手写的，很容易在键增删后忘记同步（合并/重命名时尤其容易漏）。
    cat_keys = set(KEY2CAT)
    man_keys = set(se)
    if cat_keys != man_keys:
        print("⚠️ CAT 分类表与 manifest 不一致：")
        if man_keys - cat_keys:
            print(f"    · manifest 有但 CAT 缺（会显示为「其他」类）：{sorted(man_keys - cat_keys)}")
        if cat_keys - man_keys:
            print(f"    · CAT 有但 manifest 无（已废弃键，请删）：{sorted(cat_keys - man_keys)}")
        print("   ⇒ 试听台的分类计数会不对，请修正 CAT。")
        if cat_keys - man_keys:
            return 1        # 有机键残留属于真错误，直接拦下

    # ---- 组装数据：编号 / 中文名 / 分类 / 时长 / data URI ----
    items = []
    total = 0
    skipped = []
    for i, (key, ent) in enumerate(se.items(), 1):
        if not show_all and key in PASS_KEYS:
            skipped.append(key)
            continue                      # 已过验收 → 不进试听台（省体积也省眼力）
        path = SE_DIR / Path(ent["file"]).name
        if not path.exists():
            print(f"✗ 缺文件：{path}")
            return 1
        raw = path.read_bytes()
        total += len(raw)
        b64 = base64.b64encode(raw).decode("ascii")
        items.append({
            # ★ 显示**真实文件编号**（se_016 → "016"），不是 enumerate 序号：
            #   SE_PIN 留了空号 13 ⇒ 序号从第 13 个键起恒比编号小 1
            #   （「椅子向后一撞」是 se_016，序号却显示成 015）⇒ 按显示的号去报问题会张冠李戴。
            "no": Path(ent["file"]).stem.split("_")[-1],
            "name": key,
            "cat": KEY2CAT.get(key) or "其他",
            "src": f"data:audio/ogg;base64,{b64}",
        })

    data_json = json.dumps(items, ensure_ascii=False)
    # 不排序：保持 manifest 里的首现顺序，读起来跟台本一致（sorted 会按码点排，
    # 中文顺序看着是乱的，且与列表里的编号对不上）。
    hidden_json = json.dumps(skipped, ensure_ascii=False)

    html = (HTML_TEMPLATE
            .replace("__DATA__", data_json)
            .replace("__HIDDEN__", hidden_json)
            .replace("__HIDDEN_N__", str(len(skipped)))
            .replace("__COUNT__", str(len(items))))

    out = Path(args.out)
    out.write_text(html, encoding="utf-8")

    kb = total / 1024
    print(f"✓ 待验收 {len(items)} 个音效（{kb:.0f} KB）已内联 → {out}")
    if skipped:
        print(f"  已隐藏 {len(skipped)} 个过审项（按台本首现顺序）：")
        print("    " + "、".join(skipped))
        print(f"  （要看全部：python tools/gen-se-preview.py --all）")
    print(f"  文件大小：{out.stat().st_size / 1024:.0f} KB")
    return 0


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>《笑樱》SE 音效试听台</title>
<style>
  :root {
    --bg: #f7f7f5;
    --card: #ffffff;
    --border: #e3e2de;
    --text: #26251f;
    --sub: #7c7a72;
    --accent: #534AB7;
    --accent-soft: #EEEDFE;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 28px 32px 60px;
    font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: var(--bg); color: var(--text);
    font-size: 13px; line-height: 1.6;
  }
  h1 { font-size: 17px; font-weight: 500; margin: 0 0 4px; }
  .lede { color: var(--sub); margin: 0 0 20px; }
  .toolbar {
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    margin-bottom: 18px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  button {
    font: inherit; padding: 6px 13px; border-radius: 8px; cursor: pointer;
    border: 1px solid var(--border); background: var(--card); color: var(--text);
    transition: background .12s, border-color .12s;
  }
  button:hover { background: var(--accent-soft); border-color: var(--accent); }
  button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  .vol { display: flex; align-items: center; gap: 8px; margin-left: auto; color: var(--sub); }
  input[type=range] { width: 130px; accent-color: var(--accent); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(268px, 1fr)); gap: 10px; }
  .row {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 11px 13px; cursor: pointer; display: flex; align-items: center; gap: 11px;
    transition: border-color .12s, background .12s;
  }
  .row:hover { border-color: var(--accent); }
  .row.playing { border-color: var(--accent); background: var(--accent-soft); }
  .no { font-variant-numeric: tabular-nums; color: var(--sub); font-size: 11px; min-width: 22px; }
  .name { font-weight: 500; }
  .meta { color: var(--sub); font-size: 11px; font-variant-numeric: tabular-nums; }
  .meta.err { color: #b3261e; }
  .body { flex: 1; min-width: 0; }
  .bar { height: 3px; background: var(--border); border-radius: 2px; margin-top: 5px; overflow: hidden; }
  .bar > i { display: block; height: 100%; width: 0; background: var(--accent); }
  .badge {
    font-size: 11px; font-weight: 400; color: var(--accent);
    background: var(--accent-soft); border: 1px solid var(--accent);
    border-radius: 999px; padding: 1px 9px; margin-left: 6px; vertical-align: 2px;
  }
  .hidden-list { color: var(--sub); }
  .empty { color: var(--sub); padding: 20px 0; }
</style>
</head>
<body>
<h1>《笑樱》SE 音效试听台 <span class="badge">待验收 __COUNT__ 项</span></h1>
<p class="lede">全部为代码合成（<code>tools/gen-se.py</code>），峰值统一 −3 dBFS。音频已内联，离线也能听。<br>
已过验收的 <b>__HIDDEN_N__</b> 项默认隐藏（<span id="hiddenList" class="hidden-list"></span>）。
想看全部：<code>python tools/gen-se-preview.py --all</code></p>

<div class="toolbar">
  <button id="stopAll">全部停止</button>
  <button data-cat="环境">环境类</button>
  <button data-cat="动作">动作类</button>
  <button data-cat="提示">提示类</button>
  <label class="vol">音量 <input type="range" id="vol" min="0" max="100" value="70"><span id="volv">70%</span></label>
</div>

<div class="grid" id="grid"></div>

<script>
const SE = __DATA__;
const HIDDEN = __HIDDEN__;
document.getElementById("hiddenList").textContent =
  HIDDEN.length ? HIDDEN.join("、") : "（无）";
const grid = document.getElementById("grid");
let current = null, currentAudio = null, filter = null;

SE.forEach(item => {
  const row = document.createElement("div");
  row.className = "row";
  // ⛔ 用 data-secat 而不是 data-cat：data-cat 是工具栏筛选按钮的专属属性。
  //    若行上也叫 data-cat，`querySelectorAll("[data-cat]")` 会把行一起选中
  //    （见下方筛选绑定的注释）。
  row.dataset.secat = item.cat;
  row.innerHTML = `
    <span class="no">${item.no}</span>
    <div class="body">
      <div class="name"></div>
      <div class="meta"></div>
      <div class="bar"><i></i></div>
    </div>`;
  row.querySelector(".name").textContent = item.name;
  const meta = row.querySelector(".meta");
  const setMeta = (extra) => {
    meta.classList.toggle("err", !!extra);
    meta.textContent = extra || `${item.cat}类`;
  };
  setMeta(null);
  row.onclick = () => play(item, row, setMeta);
  grid.appendChild(row);
});

const vol = document.getElementById("vol");
const volv = document.getElementById("volv");
vol.oninput = () => {
  volv.textContent = vol.value + "%";
  if (currentAudio) currentAudio.volume = vol.value / 100;
};

function stopAll() {
  if (currentAudio) { currentAudio.pause(); currentAudio = null; }
  if (current) { current.classList.remove("playing"); current.querySelector(".bar > i").style.width = "0"; current = null; }
}

function play(item, row, setMeta) {
  stopAll();
  const a = new Audio(item.src);
  a.volume = vol.value / 100;
  a.onerror = () => setMeta("❌ 解码失败");
  a.onloadedmetadata = () => {
    if (isFinite(a.duration)) setMeta(`${item.cat}类 · ${a.duration.toFixed(2)}s`);
  };
  currentAudio = a; current = row; row.classList.add("playing");
  const bar = row.querySelector(".bar > i");
  a.ontimeupdate = () => {
    if (a.duration) bar.style.width = (a.currentTime / a.duration * 100) + "%";
  };
  a.onended = () => { bar.style.width = "0"; row.classList.remove("playing"); currentAudio = null; current = null; };
  a.play().catch(e => setMeta("❌ " + e.message));
}

document.getElementById("stopAll").onclick = stopAll;
// 筛选用 button[data-cat]（工具栏专属）；行上是 data-secat，两者不会互相干扰。
document.querySelectorAll("button[data-cat]").forEach(b => {
  b.onclick = () => {
    if (filter === b.dataset.cat) { filter = null; b.classList.remove("on"); }
    else {
      filter = b.dataset.cat;
      document.querySelectorAll("button[data-cat]").forEach(x => x.classList.remove("on"));
      b.classList.add("on");
    }
    document.querySelectorAll(".row").forEach(r => {
      r.style.display = (!filter || r.dataset.secat === filter) ? "" : "none";
    });
  };
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
