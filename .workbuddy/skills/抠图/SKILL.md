---
name: 抠图
version: "5.3"
display_name: "出图与抠图（ImageGen 出图 → 黑白蒙版法 → 透明 PNG；含去水印）"
description: "《笑樱》项目**出图 + 透明化 + 去水印**的唯一入口。①出图＝**ImageGen**（单图输入锚画风 ＋ `input_fidelity`；云卡 Qwen-Image 出图已于 2026-09-15 否决，仅留档）；②透明化＝黑白蒙版法（颜色层纯黑底 ＋ 白剪影蒙版层）→ 本地 mask_compose 合成清边（云端 JoinImageWithAlpha 留档备用）；③去水印＝`tools/dewm.py`（`--smooth` 高质量模式：Laplace/IDW 低频＋颗粒补偿）。当用户说「出图 / 生图 / 生成立绘 / 去背景 / 抠图 / 透明底 / 去水印 / 水印」，或要给《笑樱》美术资产出图、做透明化、去右下水印时使用。"
agent_created: true
user-invocable: true
---

# 出图与抠图（《笑樱》美术生产）

## 〇、出图端（现役＝**ImageGen**；云卡 Qwen-Image 出图已否决）

**2026-09-15 主人拍板：退回 ImageGen 工作流。** 云卡出图否决，主人给的两条原因：

1. **画风锚不住**——「生成的根本不是日式动漫风，甚至它可能都不知道什么是柚子社」。
   Qwen-Image 是中文通用模型，对 galgame／柚子社这类**细分画风标签没有概念**，纯文字锚不住。
2. **没有原生图生图**——本项目立绘配方依赖「喂一张参考图锚定画风 ＋ `input_fidelity`」；
   云卡 i2i 既把输出尺寸锁死为源图尺寸、又只能靠 `denoise` 硬凑，链路太麻烦。

> **顺带纠正一条旧结论**：「ImageGen 带水印 → 弃用」这个理由在**成品管线里其实不成立**（§三 实测）：
> 立绘水印只落在 **alpha=0 的透明区 RGB 残留**（按 alpha 合成看不见）；
> BG/CG 水印只在 `*_源_2560x1536` 底部，**裁底 96px 正好裁掉**。
> → 回到 ImageGen 不需要额外加水印工序。

### 出图配方（＝制作手册.md §4 的 ImageGen 口径，照抄即可）

| 用途 | size | input_fidelity | 说明 |
|---|---|---|---|
| 立绘 · 颜色层 | `1440x2560` | `high`（改动小）/ `medium`（换姿势、比例） | **必须单图输入**（传两张会被忽略 size，掉到 1024×1024） |
| 立绘 · 蒙版层（白剪影） | `1440x2560` | `high` | 以颜色层为 image1 |
| 表情差分 | `1440x2560` | `high` | 只改表情，其余一字不动 |
| 背景 | `2560x1536` | `medium`（画风参考） | 成品裁底 96px → 2560×1440 |
| quality | 一律 `high` | | |

- **角色描写完整写进提示词**（文生图式）＋一张画风锚点图；写明「只参考画风，不参考图中人物」。
  **禁止拿角色自己的旧图反复 img2img**（代代劣化＝主人口中的「糊」）。
- **底色必须纯黑 #000000**（§四 铁律，`mask_compose.py` 靠四边洪泛近黑判背景）。
- ⚠️ **同一 output_dir 一律串行、出一张立刻改名**：ImageGen 产物名＝前缀＋秒级时间戳，并行会互相覆盖。

---

### 云卡存档（AutoDL RTX3080 · ComfyUI ＋ Qwen-Image 2512）——链路可用，能力不匹配

**出图端不用它了**，以下留档：哪天要做「云端 alpha 合成」或「往空白处加东西」再翻。

- 连接：`ssh -p 29301 root@region-41.seetacloud.com`（**端口/口令关机再开就变**，以 AutoDL 控制台为准）；
  ComfyUI 在容器内 `127.0.0.1:6006`，`tools/remote/start_comfy.sh` 幂等启动（10 秒就绪）。
  连接信息在 `tools/cloud-comfy.config.json`（含口令，已 gitignore；可被 `COMFY_SSH_*` 覆盖）。
- 工具：`cloudgen.py`（t2i / i2i / alpha / inpaint）、`cloud_sh.py`（远端 shell）、
  `cloud_alpha_check.py`（alpha 验收）、`png_prompt.py`（**回捞出图时的工作流/提示词**，与生成端无关，通用）。
- 出图实测：1440×2560 / 20 步 = **281 s**（比 ImageGen 慢）；**无水印但画风是通用动漫**（不合本项目口味）。
  唯一出图提示词留档：`.workbuddy/backup/文档整理前_20260917/云卡出图提示词.md`（已否决，仅留档）。
- 仍有价值的一环＝**§二 云端 alpha 合成**（IoU 0.9939 / 0.9928、< 2 s、颜色层零改动）；
  但本地 `mask_compose + edge_clean` 已够用 → **默认仍走本地**。
- 云卡**跨项目共用**（上面还跑 GPT-SoVITS / IndexTTS），别狂刷；数据盘剩 8.4 G，别传大文件。

```bash
G="C:/Users/zbj/.workbuddy/binaries/python/envs/default/Scripts/python.exe"   # paramiko 在隔离 venv
"$G" tools/cloud_sh.py --file tools/remote/start_comfy.sh     # 起 ComfyUI（幂等）
"$G" tools/cloudgen.py alpha --color 颜色层.png --mask 蒙版层.png --threshold 0.5 --invert --out 透明.png
"$G" tools/png_prompt.py 图.png                               # 回捞 PNG 内嵌的提示词/工作流
```

---

## 一、★ 唯一透明化管线：黑白蒙版法（2026-09-12 主人拍板，未变）

绿幕（色度键）、浅灰底（配 AI matting）、AI matting、rembg **全部否决**。
**每资产就是 2 次生成**（颜色层 + 蒙版层），不要再试图省掉蒙版层那次。

```bash
# ① 颜色层：纯黑底（prompt 见 制作手册.md §4.1）
# ② 蒙版层：以颜色层为 image1 出纯白剪影（prompt 见 制作手册.md §4.2）
# ③④⑤ 二选一：本地管线（默认）或云端 alpha（§二）
# 本地：
"$G" 素材基准/_工具/mask_compose.py <颜色层.png> <蒙版层.png> <输出.png>
"$G" 素材基准/_工具/edge_clean.py   <上一步.png>   <输出.png>
# 验收（必做）
"$G" 素材基准/_工具/matting_check.py <输出.png>
"$G" 素材基准/_工具/mat_preview.py preview <输出.png>
```

---

## 二、★★ 透明通道结论（2026-09-15 实测）

> **默认仍走 §一 的本地管线**（`mask_compose + edge_clean`，产物口径稳定）。
> 本节是「云端 JoinImageWithAlpha」的留档能力——它本身等效且更快，但要用它就得开云卡链路，
> 而云卡**出图**已否决（§〇）。独立需要（例如换台机器、本地没环境）时可整节照搬。

**能不能直接生成透明通道的图？——模型本身不能，云端管线能。**

| 问题 | 结论 |
|---|---|
| Qwen-Image 直出 alpha？ | ❌ **不行**。VAE 是 3 通道 RGB，ComfyUI 没有「透明输出」开关；`VAEDecode` 出来就是 RGB |
| 有抠图模型可兜底？ | ❌ 机器上没有。扫了全部 947 个节点：**无 rembg / BiRefNet / RMBG / LayerDiffuse**；Bria 系需 API key；SAM3 权重不在 |
| 那怎么办？ | ✅ **黑白蒙版法搬上云**：颜色层 + 白剪影蒙版 → `JoinImageWithAlpha` → 直接落 RGBA PNG |

### 云端 alpha 工作流（`cloudgen.py alpha` 已封装）

```
LoadImage(颜色层) ───────────────────────────────┐
LoadImage(蒙版层) → ImageToMask(red) → ThresholdMask(0.5) → InvertMask ─→ JoinImageWithAlpha → SaveImage
```

**三个必须，少一个就翻车**：

1. ⚠️ **必须 `InvertMask`**：`JoinImageWithAlpha` 的 alpha 语义与「白＝人物」相反，
   不加反相 → 人物透明、背景不透明（实测不透明 71.7% ↔ 应为 28.3%，正好互换）。
2. ⚠️ **必须 `ThresholdMask(value=0.5)`**：生成的蒙版层不是纯黑白（实测只有 20% 纯白、**59% 中间灰**），
   不二值化 → alpha 54.6% 落在半透明灰上，整张糊边。
3. ⚠️ 云卡没有 `ImageThreshold` 节点，**用 extra 节点 `ThresholdMask`**（输入 `mask` + `value` 0~1）。

### 实测数据（对本地 `mask_compose + edge_clean` 成品）

| 角色 | 云端 alpha 不透明占比 | IoU | 半透明像素 | 颜色层是否被改 |
|---|---|---|---|---|
| 樱（浅发） | 28.3%（本地 28.2%） | **0.9939** | **0 px** | 否（maxdiff=0） |
| 九条院（深发） | 29.9%（本地 29.9%） | **0.9928** | **0 px** | 否（maxdiff=0） |

**结论**：云端 alpha 与本地管线**等效（IoU ≈ 0.99）**，同样是**硬边**（0 中间灰，与主人「硬边优于软边」的口径一致），
合成仅需 **< 2 秒**（不做扩散采样）。**颜色层零改动**，纯 alpha 合成。

### 但仍有两处差异 → 云端出 alpha 后建议补一道本地清理

- **水印残影** 3561 px：旧 ImageGen 资产的水印被蒙版一起判成人物。**云卡出图无水印 → 新资产不存在此问题**。
- **亮边 / 背景口袋**：本地 `mask_compose` 有四边洪泛判据、`edge_clean` 有亮边替换（深发色靠它清），
  云端只做阈值 → 差异约 4k px。**深发色角色建议照样跑一遍 `edge_clean.py` 兜底**。

### 验收命令

```bash
"$G" tools/cloud_alpha_check.py <云端alpha.png> <本地成品.png> <颜色层.png>   # IoU / 半透明量 / 颜色层是否被改
"$G" 素材基准/_工具/mat_preview.py preview <云端alpha.png>                  # 棋盘格目视（必做）
```

> **透明 PNG 在 Read 工具里渲染成全黑 —— 验收必须走棋盘格预览**，交付时同时给透明成品＋预览图。

---

## 三、去水印（ImageGen 存量素材，2026-09-15 实测定案）

### 先问：真的需要去吗？——**现有素材都不用去**

| 素材 | 水印落在哪 | 结论 |
|---|---|---|
| 立绘 `_raw_*` | **alpha=0 的透明区**（只有 RGB 残留） | 按 alpha 合成**看不见**；只有用「忽略 alpha 看图」的软件打开才会看到 |
| BG/CG `*_源_2560x1536` | 源图右下角**实打实压在画面上** | 但成品流程**裁底 96px 正好把它裁掉** → `*_2560x1440` 干净 |
| 颜色层/蒙版层 | 黑底空白处 | 抠图时被蒙版排除，不进成品 |

→ 交付前别急着加一道去水印工序。**只有要重新构图源图（裁出别的画面）时才需要处理。**

### 水印定位铁律：右下角固定尺寸，**不随图缩放**

两条独立实测证据：

| 图 | 水印 bbox | 右边距 | 下边距 |
|---|---|---|---|
| 1440×2560 立绘 | x[1269,1425] y[2474,2545] | 15 | 15 |
| 2560×1536 源图（走廊） | x[2379,2545] y[1445,1521] | 15 | 15 |
| 2560×1536 源图（旧长椅） | x[2380,2545] y[1445,1521] | 15 | 15 |

**所以框「右下角 200×115」必然盖得住。禁止按比例外推**——对 1536 高的图会偏 100+px，
mask 盖不全 → 残留半截「AI生成」，还容易误判成「算法不行」。

### 方案选择：**平滑背景 cv2 秒杀；有颗粒/渐变用 `--smooth`；纹理背景仍无解**

| 方案 | 纯色背景（墙面 std≈0.3） | 颗粒/渐变背景（水泥地 std≈18） | 高频纹理（草地/花瓣/跑道） | 速度 |
|---|---|---|---|---|
| `dewm.py`（默认 cv2 NS r=3） | ✅ **完美** | ⚠️ **遗留矩形糊块 + V 形折角**（肉眼可见） | ❌ 横向条纹拖影、抹掉画面结构 | **0.03 s** |
| **`dewm.py --smooth`（推荐）** | ✅ | ✅ **1x 视距看不出**（旧长椅源图实测） | ⚠️ 未实测，理论上仍不行 | **0.2 s** |
| `cloudgen.py inpaint`（Qwen-Image 生成式） | ❌ 生成怪纹理（绒毛/横纹） | ❌ | ❌ | 44 s（裁块）/ 278 s（整图） |

**为什么 cv2 在颗粒背景上会留糊块**：它把边界梯度沿等照度线推进，在大面积平滑区产生
**方向性条纹/V 形折角**，且 mask 是硬接缝 → 一块与周围纹理不匹配的「平滑贴片」。

**`--smooth` 怎么解决**（2026-09-15 旧长椅源图调参定案，试了 10 种组合）：

1. **低频**＝四边逆距离加权（IDW）+ **Laplace 方程数值解**（Gauss-Seidel 迭代 1500 次）各半。
   - Laplace 全局最平滑、**无方向性伪影**（实测低频梯度 1.94/2.65，cv2 是 5.21/5.14）；
   - IDW 保证**边缘颜色准**（掩膜区与邻域色差 0.64，Laplace 单用是 4.33）；
   - 各半混合两头兼顾（色差 3.67、梯度 2.61）。
2. **高频**＝从左右邻域统计颗粒 std（剔除花瓣/暗线污染像素），生成**同幅度灰度**相关噪声叠上去
   —— 补回 inpaint 丢掉的颗粒感。⚠️ 噪声必须**单通道复制到 RGB**，三通道独立会出**彩色噪点**。
3. **羽化 2px** 贴回，掩膜外像素零改动（实测 maxdiff 1、仅 498 px 在过渡带）。

**★ 必须用紧致 bbox，不要外扩**：实测外扩 8px 反而让邻域色差从 0.84 涨到 8.40
（把周围真实渐变吞进了插值）。`--smooth` 默认 `tight_rect`（右下角 180×90 @ 边距 15），
实测白字残留 **1 px**（精确 bbox 反而剩 14 px，因为少了边缘余量）。

```bash
# 高质量（推荐）：自动取右下角紧致 bbox
"$G" tools/dewm.py --src a.png --out b.png --smooth
# 精确指定水印 bbox / 批量
"$G" tools/dewm.py --src a.png --out b.png --smooth --rect 2380,1445,2545,1521
"$G" tools/dewm.py --src-dir 素材基准/背景 --suffix _源 --out-dir 输出目录 --smooth
# 快速（纯色背景够用）
"$G" tools/dewm.py --src a.png --out b.png
# 自动检测（--auto 仅背景干净时可靠，画面有地砖缝/阴影等暗线会被带偏）
```

**为什么云卡会翻车**（勿重试）：Qwen-Image 2512 是 **T2I 基座，不是 inpaint 模型**——
它在 mask 区「自由创作」而不「延续周围像素」。官方 blueprint
`Image Inpainting (Qwen-image).json` 的正确接法是
`VAEEncode + SetLatentNoiseMask + ControlNetInpaintingAliMamaApply`（**不是** `VAEEncodeForInpaint`），
但**云卡的 `models/controlnet`、`models/loras` 都是空的**，也没有 LaMa/MAT/Flux-Fill
→ 生成式去水印目前跑不起来。**裁块法**（只把右下角小块送云端）能把 278s 压到 44s
（inpaint 耗时由整图分辨率决定、与 mask 面积无关），方向对但质量不过关。

**已否决的旁路**（别重试）：镜像补丁 + `seamlessClone`（`MIXED_CLONE` 色差 13.5；
`NORMAL_CLONE` 还会**残留半截水印**，因为边界条件压不住）；19×19 硬拼块采样（方格感明显）；
汉宁窗重叠块（仍有块感）。

### 云卡 inpaint 子命令的适用面（留作备用）

`cloudgen.py inpaint` 本身写得没错（`--mask-rect` 支持负值＝从右/下回数、`--grow`、
`--feather`、默认 `paste_back` 贴回）。它适合「**往空白处加东西**」而不是「抹掉东西」。
两个必须遵守的点：

- **贴回一定要走 mask 混合**：inpaint 输出是整块经 VAE 往返的图，整块贴回会把未 mask 区
  也劣化（实测墙面纹理 maxdiff 186 → 用 `paste_back` 后降到 3）。
- **`paste_back` 会保留源图 alpha**（`keep_alpha=True` 默认）：透明图的水印只存在于 RGB，
  若把 alpha 一起贴回会把透明区弄成不透明。

## 四、关键规矩（沿用 v4）

- **颜色层底色必须纯黑**：`mask_compose.py` 靠「四边洪泛近黑」判外部背景，灰底/绿底直接破坏判据。
- **蒙版复用**（差分/表情组）：只改表情、轮廓不变 → 蒙版可复用（省 1 次生成）；
  **头部姿态/发型变了 → 必须重出蒙版**。复用前先跑验收确认。
- **blur 陷阱**：清边后**绝对不要**再 `GaussianBlur`「保抗锯齿」——会把硬化糊回去（实测弱带 0 → 19,823px）。
- **硬边是特性不是缺陷**：主人目视判定硬 alpha 观感优于软边。
  已知取舍：深色线稿可能被误判「背景口袋」打洞 ＋ 无抗锯齿——主人知悉并接受。
- **深发色角色的黑底边缘光**：靠 `edge_clean.py` 亮差替换清（亮边必须在线稿**外侧**才可去）。
- **蒙版层提示词模板**：保持人物轮廓、姿势、构图与原图完全一致，把画面转换成纯黑白剪影：
  人物整体填充为纯白色（无内部细节、无灰阶过渡），背景填充为纯黑色。边缘清晰利落。无文字，无水印。

## 五、存档（已否决路线，勿再用，仅供查账）

| 路线 | 工具 | 否决原因 |
|---|---|---|
| AI matting 云服务 | `buddy-image-processing.py image-edit --operation matting` ＋ `mat_tighten.py` | 软边压浅底显糊（主人目视）；半透明带带底色 |
| 绿幕＋自研色度键 | `boundary_cutout.py --mode chroma` | 2026-09-12 被主人整体否决 |
| 浅灰底 | （配 AI matting） | 发丝缝暗灰残留无解；灰底破坏 mask_compose 洪泛判据 |
| rembg(isnet-anime) | — | 边缘硬、发丝细节流失（更早轮次否决，模型已删） |
| ImageGen `background: transparent` | ImageGen | 参数不生效，输出 RGB 无 alpha |
| **云卡 Qwen-Image 出图** | `cloudgen.py t2i` / `i2i` | **画风锚不住**（中文通用模型，不认识柚子社/galgame 细分画风）＋**没有原生图生图**（i2i 锁死源图尺寸，只能靠 denoise 硬凑，链路麻烦）→ 2026-09-15 主人驳回，**退回 ImageGen**。⚠️ 注意：当初「换掉 ImageGen」的理由是水印，而水印在成品管线里其实被消化掉了（见 §〇）——别再拿水印当选型理由 |
| 云卡去水印 | `cloudgen.py inpaint`（Qwen-Image） | 非 inpaint 专用模型，mask 区自由创作出怪纹理；两次实测（纯色区 278s／裁块 44s）均失败 → 改用 `tools/dewm.py`（cv2 / `--smooth`） |
| 去水印其它旁路 | 镜像补丁+seamlessClone、硬拼块采样、汉宁窗重叠块 | 色差 13.5／残留水印／方格感／块感——均不如 `dewm.py --smooth`（详见 §三） |
