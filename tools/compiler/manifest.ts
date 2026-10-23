/**
 * 生成 `src/generated/assets.manifest.json`
 *
 * 从 `script.json` 抽出全部资源 key，映射为 ASCII 文件名。
 * 用途（见 `技术设计.md` §12.1）：中文文件名在 Windows 可用，但打包工具链、
 * 字体子集化、CDN 分发环节容易出编码坑 —— 故文件名一律 ASCII，中文→文件的映射全在本表。
 *
 * **幂等可重跑**：台本一改，`npm run compile && npm run manifest` 即可刷新。
 *
 * 用法：`npm run manifest`
 */

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SCRIPT = resolve(__dirname, '../../src/generated/script.json');
/** 日配语音的部署目录（Vite public/ ⇒ 运行时 URL 前缀 /assets/audio/voice/） */
const VOICE_DIR = resolve(__dirname, '../../public/assets/audio/voice');
const OUT_DIR = resolve(__dirname, '../../src/generated');
const OUT = resolve(OUT_DIR, 'assets.manifest.json');

// ============================================================
// 站位（中文自由文本）→ 舞台锚点枚举
// 见 R-003：台本里的中文方位由引擎侧映射，台本一个字不用改
// ============================================================
const SLOT_MAP: Record<string, string> = {
  长椅中央: 'bench-center',
  长椅左端: 'bench-left',
  长椅右端: 'bench-right',
  长椅: 'bench-center',
  长椅右侧: 'bench-right',
  长椅右外侧: 'bench-far-right',
  操场边线外: 'field-far-left',
  操场对面: 'field-far-right',
  看台左: 'stand-left',
  看台右: 'stand-right',
  走廊左: 'corridor-left',
  走廊右: 'corridor-right',
  左: 'left',
  右: 'right',
  中央: 'center',
  画面左侧: 'left',
  画面右侧: 'right',
  center: 'center',
};

// ============================================================
// 角色（中文名）→ 文件词干
// ============================================================
const CHAR_MAP: Record<string, string> = {
  龙司: 'ryuji',
  樱: 'sakura',
  阳太: 'yota',
  亚里沙: 'arisa',
  九条院: 'kujo',
  泽渡: 'sawtari',
};

// ============================================================
// BGM 备注（台本侧 10.2 的戏剧要求，须传给音频制作与引擎）
// ============================================================
const BGM_NOTE: Record<string, string> = {
  '樱_主题（完整）': '章末收束专用：不循环，弹到结束（L3088 / L4048，均配 ※ 长镜头拉远）',
  '樱_主题': '樱主题主旋律',
  '樱_主题（淡）': '樱主题・轻混音（远景 / 夜晚）',
  '樱_主题（低）': '樱主题・低沉变奏（压抑场景）',
  '静・樱': '樱的空间专用氛围（无旋律底噪）—— 长椅 / 樱树场景',
  静: '空教室 / 夜晚的静',
  悬: '悬疑压迫感（含原「静・悬」—— 教室空旷＋被盯上）',
  日常: '日常基础',
  '日常（暖）': '日常・暖色（含原「日常・收束」）',
  '日常（快）': '日常・快节奏（含原「日常・杂踏 / 轻快」）',
  晚风: '傍晚氛围',
  体育课: '体育课专用',
  无: '停止 BGM',
};

/** 文件名安全化：仅保留 ASCII 字母数字与下划线，其余转下划线 */
function safe(s: string): string {
  return s
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '')
    .toLowerCase();
}

interface Entry {
  file: string;
  /** 在 script.json 中被引用次数 */
  count: number;
  note?: string;
  /** BGM 专用：是否循环 */
  loop?: boolean;
  /**
   * SE 专用：**环境型**（持续铺在场景里的底噪）。
   * ⚠️ 环境型 SE 在**场景切换时必须停掉**，否则会跨场一直响 ——
   *   实测「周围的议论声」（60.8 s）播到长椅戏还在响（主人 2026-09-18 反馈）。
   *   一次性事件音（纸页/脚步/铃声）不需要这个标记：它们播完即止。
   */
  env?: boolean;
}

function build() {
  if (!existsSync(SCRIPT)) {
    console.error('找不到 ' + SCRIPT + '。请先 `npm run compile`。');
    process.exit(1);
  }
  const program = JSON.parse(readFileSync(SCRIPT, 'utf-8'));
  const ops = program.ops as Array<Record<string, unknown>>;

  const bg: Record<string, Entry> = {};
  const bgm: Record<string, Entry> = {};
  const se: Record<string, Entry> = {};
  const cg: Record<string, Entry> = {};
  const char: Record<string, { stem: string; poses: Record<string, number> }> = {};
  const slot: Record<string, string> = {};

  // ============================================================
  // ★ BG 部署锁定表（2026-09-12，根修「manifest 键序移位」事故）
  //   磁盘上已部署的 BG（public/assets/bg/）按制作手册.md §2.4 钉死：
  //   bg_001=教室中性底 / bg_002=走廊 / bg_024=校舍间岔路_杂草 /
  //   bg_025=旧长椅_午间 / bg_026=教室（备用副本）。
  //   台本插入新场景导致键序变化时，锁定键的映射**永不移位**；
  //   未部署键的自动编号会跳过锁定编号，防止撞上真实文件。
  //   同地点多标识共用中性底，时段/天气由引擎滤镜负责（待办）。
  // ============================================================
  const BG_PIN: Record<string, string> = {
    // 教室（7+ 键）→ bg_001
    教室_午休_喧闹: 'bg/bg_001.webp',
    教室_早晨: 'bg/bg_001.webp',
    教室_午休: 'bg/bg_001.webp',
    教室_午后: 'bg/bg_001.webp',
    教室_午后斜阳: 'bg/bg_001.webp',
    教室_空旷_傍晚: 'bg/bg_001.webp',
    教室_放学后: 'bg/bg_001.webp',
    // 走廊（2 键）→ bg_002
    走廊_午后: 'bg/bg_002.webp',
    走廊_窗_黄昏: 'bg/bg_002.webp',
    // ★ 操场（4 键）→ bg_006（2026-09-17 新出：晴天、空气通透、看台坐满学生；时段/天气差分待滤镜）
    操场_晴天_大风: 'bg/bg_006.webp',
    操场_大风: 'bg/bg_006.webp',
    操场_午后: 'bg/bg_006.webp',
    操场_放学前的间隙: 'bg/bg_006.webp',
    // ★ 教学楼・中庭水龙头（1 键）→ bg_012（2026-09-17 新出）
    中庭_水龙头: 'bg/bg_012.webp',
    // ★ 龙司的房间（1 键）→ bg_016（2026-09-17 新出）
    房间_书桌_夜: 'bg/bg_016.webp',
    // ★ 教学楼后・拐角（1 键）→ bg_018（2026-09-17 新出；凸角 ＋ 教学楼后门 ＋ 体育仓库铁皮门）
    教学楼后_拐角_傍晚: 'bg/bg_018.webp',
    // ★ 校舍间岔路（1 键）→ bg_024
    校舍间岔路_杂草: 'bg/bg_024.webp',
    // ★ 校舍间小路_紫藤（1 键）→ bg_027 —— 2026-09-17 从 bg_024 上**拆出来**：
    //   台本（Ch2 SC01）里它是**另一个地方**（紫藤老墙＋体育仓库背面＋旧跳箱＋自动贩卖机），
    //   原先与岔路共用 bg_024 是错的。
    校舍间小路_紫藤: 'bg/bg_027.webp',
    // 旧校舍・樱树下长椅（11 键）→ bg_025
    旧长椅_午间: 'bg/bg_025.webp',
    旧长椅_夕阳: 'bg/bg_025.webp',
    旧长椅_暮色: 'bg/bg_025.webp',
    旧长椅_黄昏: 'bg/bg_025.webp',
    旧长椅_午后: 'bg/bg_025.webp',
    旧校舍_樱树_长椅: 'bg/bg_025.webp',
    旧校舍_樱树_正午: 'bg/bg_025.webp',
    旧校舍_樱树_阴天: 'bg/bg_025.webp',
    旧校舍_樱树_清晨: 'bg/bg_025.webp',
    旧校舍_傍晚_樱树: 'bg/bg_025.webp',
    旧校舍_傍晚_长椅: 'bg/bg_025.webp',
  };
  const PINNED_BG_NUMS = new Set(
    Object.values(BG_PIN).map((f) => parseInt(f.match(/bg_(\d+)\./)![1], 10)),
  );

  // ============================================================
  // ★ CG 部署锁定表（2026-09-17，根修「CG 键序移位」事故）
  //   事故：第 1 章「去立绘 CG 化」一次性在章首插入 8 个新键 →
  //   CG 自动编号整体后移 8 位，但 `public/assets/cg/` 里还是旧编号的文件
  //   ⇒ `教室_早晨_背影` 解析到旧 cg_001（＝旧形象的 `长椅默读`），
  //     游戏里画的是粉发旧樱，**引擎零报错**。
  //   根因与 BG 同源：编号按「台本首现顺序」自动发，台本一插键就全体顺移。
  //   ⇒ **已接线键一律在此钉死编号**；未部署键自动取「已发最大号 + 1」并跳过被钉号。
  //   ⚠️ 新增／重排 CG 键的流程：先改这张表 → `npm run compile && npm run manifest`
  //      → `python tools/deploy-cg.py`（它按 manifest 对号，别手抄编号）。
  // ============================================================
  const CG_PIN: Record<string, string> = {
    // ★ 2026-09-18（第二次漂移后）起：**manifest 里全部键都在此钉死**，不再依赖自动发号。
    //   教训：上次只钉了 17 个已接线键 → 本轮在台本里插 24 个新键（顺序上夹在中间）
    //   ⇒ 那 7 个「已部署但没钉」的键（cg_019–024）整体后移 ⇒ 又差点「画错图、零报错」。
    //   ⚠️ 新增键的流程：台本写 `■ CG：键名` → compile+manifest → **把新键补进本表** → deploy-cg。
    教室_早晨_背影: 'cg/cg_001.webp',
    教室_午休_三人: 'cg/cg_002.webp',
    走廊_让路: 'cg/cg_003.webp',
    长椅默读: 'cg/cg_004.webp',
    长椅初识: 'cg/cg_005.webp',
    // 复用：以下 2 个键共用同一张成品（主人 2026-09-18 定「统一用这张」）
    长椅_隔着椅子: 'cg/cg_006.webp',
    夕阳下的长椅_仰视背影: 'cg/cg_006.webp',
    长椅_念笑话: 'cg/cg_007.webp',
    樱_眼睛里的一点变化: 'cg/cg_008.webp',
    操场_远处摔倒: 'cg/cg_009.webp',
    长椅_糖与便条: 'cg/cg_010.webp',
    樱摔倒_俯视跑道: 'cg/cg_011.webp',
    樱摔倒_阴影蜷坐: 'cg/cg_012.webp',
    樱摔倒_白光与手: 'cg/cg_013.webp',
    夕阳下的长椅: 'cg/cg_014.webp',
    // —— 空号 cg_015（保留，不许放开：放开会把后面的已部署键整体前移）——
    樱_眼眶热了: 'cg/cg_016.webp',
    樱的独白: 'cg/cg_017.webp',
    长椅_三人同框: 'cg/cg_018.webp',
    长椅_阳太的赌注: 'cg/cg_019.webp',
    教室_亚里沙给糖: 'cg/cg_020.webp',
    长椅_拧螺丝: 'cg/cg_021.webp',
    长椅_掌心的糖: 'cg/cg_022.webp',
    操场_看台上的他: 'cg/cg_023.webp',
    走廊柱子后_望中庭: 'cg/cg_024.webp',
    九条院_三步之外: 'cg/cg_025.webp',
    九条院_笑容裂开: 'cg/cg_026.webp',
    中庭_我乐意: 'cg/cg_027.webp',
    教室_傍晚_樱的座位: 'cg/cg_028.webp',
    岔路_围堵: 'cg/cg_029.webp',
    岔路_捏住她的下巴: 'cg/cg_030.webp',
    岔路_她挡在他前面: 'cg/cg_031.webp',
    岔路_他咬住那只手: 'cg/cg_032.webp',
    岔路_她哭了: 'cg/cg_033.webp',
    房间_抽屉里的糖: 'cg/cg_034.webp',
    长椅_她翻看透明糖: 'cg/cg_035.webp',
    长椅_我真的这么想: 'cg/cg_036.webp',
    长椅_木片樱花回礼: 'cg/cg_037.webp',
    操场_递糖: 'cg/cg_038.webp',
    教室_他第一次笑: 'cg/cg_039.webp',
    长椅_隔着椅子_叶樱: 'cg/cg_040.webp',
    长椅_念笑话_叶樱: 'cg/cg_041.webp',
    长椅_一人一颗糖: 'cg/cg_042.webp',
    长椅_泽渡登场: 'cg/cg_043.webp',
    长椅_她的眼睛弯了一下: 'cg/cg_044.webp',
    长椅_两颗糖并排: 'cg/cg_045.webp',
    长椅_螺丝: 'cg/cg_046.webp',
    长椅_泽渡摊牌: 'cg/cg_047.webp',
    手机短信: 'cg/cg_048.webp',
  };
  /**
   * ★ 保留空号（2026-09-18）。
   *
   * `夕阳下的长椅_仰视背影` 改为**复用** `长椅_隔着椅子`（cg_006）后，`cg_015` 就没有主人了。
   * ⛔ 但**绝不能**把它从「已钉号」里拿掉 —— 自动发号是「取已发最大号 +1，跳过被钉号」，
   *   015 一旦解开，第一个**未钉**的键（`长椅_三人同框`，它在 Ch2 出现、早于 Ch3 那批钉号）
   *   就会捡走 015 ⇒ 其后 6 个已部署键（`cg_018–024`）**整体前移一位**，
   *   而 `public/assets/cg/` 里还是旧编号的文件 ＝ 昨夜「画成粉发旧樱」那起事故原样重演
   *   （实测：解开 015 后 3 分钟内就复现，见工作日志 2026-09-18）。
   * ⇒ 空号一律在此登记，和正常钉号同等对待。
   */
  const CG_RESERVED_NUMS = [15];
  const PINNED_CG_NUMS = new Set([
    ...Object.values(CG_PIN).map((f) => parseInt(f.match(/cg_(\d+)\./)![1], 10)),
    ...CG_RESERVED_NUMS,
  ]);

  // ============================================================
  // ★★ SE 音效编号钉死表（2026-09-18 建立）
  //
  // 为什么必须钉：SE 与 BG/CG 同属「按台本首现顺序自动发号」体系。
  // 09-18 把「哨声、风声」合并进「哨声、风」时就踩到同一颗雷 ——
  // 删掉中间一个键 ⇒ 其后 14 个键（se_014–se_030）**整体前移一位**，
  // 而 `public/assets/audio/se/` 里文件名会跟着重写 ⇒
  // **文件名的内容和实际音频错位**（如 se_014 变成原 se_015 的心跳），引擎零报错。
  // 与 CG 那次「画成粉发旧樱」是**完全相同的事故类型**。
  //
  // ⚠️ 规矩（与 CG 同）：
  //   1. **manifest 里全部 SE 键都在此钉死**，不依赖自动发号。
  //   2. 新增／重排 SE 键的流程：台本写 `SE：键名` → `npm run compile && npm run manifest`
  //      → **把新键补进本表** → `python tools/deploy-se.py`（若已建）
  //   3. ⛔ **废弃的编号（如合并掉的「哨声、风声」= se_013）必须登记到
  //      SE_RESERVED_NUMS，不许放开** —— 空号会被首个未钉键捡走，后面整体前移。
  // ============================================================
  const SE_PIN: Record<string, string> = {
    '上课预备铃': 'se/se_001.ogg',
    '教室嘈杂': 'se/se_002.ogg',
    '脚步声': 'se/se_003.ogg',
    '风声': 'se/se_004.ogg',
    '书页轻响': 'se/se_005.ogg',
    '哨声、风': 'se/se_006.ogg',
    '广播站音乐（远处）': 'se/se_007.ogg',
    '翻书声': 'se/se_008.ogg',
    '自动贩卖机': 'se/se_009.ogg',
    '踩过落叶的脚步声（由远及近）': 'se/se_010.ogg',
    '午休结束的预备铃': 'se/se_011.ogg',
    '糖纸轻响': 'se/se_012.ogg',
    // —— 空号 se_013（保留）：原「哨声、风声」，2026-09-18 合并进「哨声、风」——
    '发令哨': 'se/se_014.ogg',
    '心跳': 'se/se_015.ogg',
    '椅子向后一撞': 'se/se_016.ogg',
    '周围的议论声': 'se/se_017.ogg',
    '水声': 'se/se_018.ogg',
    '一阵风': 'se/se_019.ogg',
    '脚步（犹豫・拖沓）': 'se/se_020.ogg',
    '风卷枯叶': 'se/se_021.ogg',
    '闷响': 'se/se_022.ogg',
    '台灯开关': 'se/se_023.ogg',
    '脚步（由远及近）': 'se/se_024.ogg',
    '脚步声远去': 'se/se_025.ogg',
    '哨声': 'se/se_026.ogg',
    '放学铃': 'se/se_027.ogg',
    '午休结束铃': 'se/se_028.ogg',
    '粉笔': 'se/se_029.ogg',
    '提示音': 'se/se_030.ogg',
  };
  /** 废弃但**不许回收**的 SE 编号（合并/删除留下的空号） */
  /**
   * ★ 环境型 SE（2026-09-18）—— 「持续铺在场景里的底噪」。
   *   为什么要单独标出来：引擎的 SE 是「播完为止、没人管它」，
   *   而台本里**没有任何 SE 停止语义**（实测 37 处 SE 标记、0 处停止）。
   *   ⇒ 环境型一旦播下就会跨场响到底（议论声 60.8 s 响到长椅戏）。
   *   本表让引擎能在**场景切换时**精准停掉它们，同时不影响一次性事件音。
   *   ★ 新增环境类音效时，除了加进 SE_PIN，**也要加到这里**。
   */
  const SE_ENV = new Set<string>([
    '教室嘈杂',
    '周围的议论声',
    '风声',
    '水声',
    '广播站音乐（远处）',
  ]);
  const SE_RESERVED_NUMS: number[] = [13];
  const PINNED_SE_NUMS = new Set([
    ...Object.values(SE_PIN).map((f) => parseInt(f.match(/se_(\d+)\./)![1], 10)),
    ...SE_RESERVED_NUMS,
  ]);

  /**
   * 登记一个资源键；已有则只累加 count。
   *
   * ★ 发号规则（2026-09-17 改）：
   *   - 在 PIN 表里 → 直接用锁定文件，**不占自动编号**；
   *   - 否则取「**当前已发出的最大编号 + 1**」，并跳过所有被 PIN 占用的编号。
   *   ⚠️ 旧写法是 `Object.keys(m).length + 1` —— 已注册数**不等于**已发号数
   *   （PIN 键也计入长度、但不占号），插两个新键时第二个可能捡回第一个占过的号
   *   ⇒ 两个键指向同一个文件。改成扫 file 字段取最大值，彻底消掉这个坑。
   */
  const bump = (m: Record<string, Entry>, key: string, ext: string, prefix: string) => {
    if (!m[key]) {
      // ★ 三类资源都能钉：bg→BG_PIN / cg→CG_PIN / se→SE_PIN。
      //   ⚠️ 以前只接了 bg 和 cg，se 漏在外面走自动发号 —— 见 SE_PIN 上方的事故说明。
      const pin =
        prefix === 'bg' ? BG_PIN[key]
        : prefix === 'cg' ? CG_PIN[key]
        : prefix === 'se' ? SE_PIN[key]
        : undefined;
      if (pin) {
        m[key] = { file: pin, count: 0 };
      } else {
        const pinnedNums =
          prefix === 'bg' ? PINNED_BG_NUMS
          : prefix === 'cg' ? PINNED_CG_NUMS
          : prefix === 'se' ? PINNED_SE_NUMS
          : null;
        const re = new RegExp(`${prefix}_(\\d+)\\.`);
        let idx = 1;
        for (const e of Object.values(m)) {
          const hit = e.file.match(re);
          if (hit) idx = Math.max(idx, parseInt(hit[1], 10) + 1);
        }
        if (pinnedNums) while (pinnedNums.has(idx)) idx += 1;
        m[key] = { file: `${prefix}/${prefix}_${String(idx).padStart(3, '0')}.${ext}`, count: 0 };
      }
    }
    m[key]!.count += 1;
  };

  for (const op of ops) {
    switch (op.op) {
      case 'bg':
        // ⚠️ 扩展名必须是 webp：钉死的键走 BG_PIN 不受影响，但**未钉的新键**
        //    会按这里的 ext 生成路径 —— 2026-09-18 转 WebP 时漏改过，
        //    会让台本新增的 BG 生成 `.png` 引用而 404。
        bump(bg, op.name as string, 'webp', 'bg');
        break;
      case 'bgm':
        bump(bgm, op.name as string, 'ogg', 'bgm');
        break;
      case 'se': {
        const seName = op.name as string;
        bump(se, seName, 'ogg', 'se');
        if (SE_ENV.has(seName)) se[seName]!.env = true; // 供引擎在场景切换时停掉
        break;
      }
      case 'cg':
        // 「无」＝收起指令（N-014），不是素材 → 不登记（否则会产出 cg_010 幽灵键）
        if (op.name !== '无') bump(cg, op.name as string, 'webp', 'cg'); // 同上：未钉新键也要 webp
        break;
      case 'sprite': {
        const c = op.char as string;
        const stem = CHAR_MAP[c] ?? safe(c);
        if (!char[c]) char[c] = { stem, poses: {} };
        const pose = (op.pose as string) || 'default';
        char[c]!.poses[pose] = (char[c]!.poses[pose] ?? 0) + 1;
        const s = op.slot as string;
        if (s) slot[s] = SLOT_MAP[s] ?? 'center';
        break;
      }
    }
  }

  // ============================================================
  // ★ 语音表（2026-09-18）—— 按台本行号索引的日配素材
  //
  //  与其它资源不同：语音不由台本 op 引用，而是「台词行 → 音频文件」的外挂映射。
  //  ⇒ 扫磁盘而不是扫 ops。文件名 `v_00409.ogg` 的词干就是台本行号。
  //
  //  ⚠️ 无音频＝静默降级（与 BG/SE 一致）：没配到音的角色/旁白不该让游戏卡住。
  //     `AudioEngine.playVoice()` 查不到键就什么都不做。
  // ============================================================
  const voice: Record<string, string> = {};
  let voiceSkipped = 0;
  if (existsSync(VOICE_DIR)) {
    for (const f of readdirSync(VOICE_DIR)) {
      const m = /^v_(\d+)\.(ogg|wav|mp3)$/i.exec(f);
      if (!m) {
        voiceSkipped += 1;
        continue;
      }
      // 键用十进制字符串，去掉前导零（'00409' → '409'）——与 String(line) 对齐
      voice[String(parseInt(m[1]!, 10))] = `voice/${f}`;
    }
  }

  // BGM 备注与循环标记
  for (const [k, v] of Object.entries(bgm)) {
    if (BGM_NOTE[k]) v.note = BGM_NOTE[k];
    // 唯一不循环的：樱_主题（完整）
    v.loop = k !== '樱_主题（完整）' && k !== '无';
  }

  const manifest = {
    version: 1,
    generatedAt: new Date().toISOString(),
    source: 'script.json',
    note:
      '中文 key → ASCII 文件名映射。文件名一律 ASCII 以规避打包 / 分发环节的编码坑；' +
      '美术与音频按本表投放素材。台本改动后重跑 `npm run compile && npm run manifest` 即可刷新。',
    stats: {
      bg: Object.keys(bg).length,
      bgm: Object.keys(bgm).length,
      se: Object.keys(se).length,
      cg: Object.keys(cg).length,
      char: Object.keys(char).length,
      slot: Object.keys(slot).length,
      voice: Object.keys(voice).length,
    },
    bg,
    bgm,
    se,
    cg,
    char,
    slot,
    voice,
  };

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT, JSON.stringify(manifest, null, 2), 'utf-8');

  console.log('产出：' + OUT);
  console.log(
    `  BG ${manifest.stats.bg} / BGM ${manifest.stats.bgm} / SE ${manifest.stats.se} / ` +
      `CG ${manifest.stats.cg} / 角色 ${manifest.stats.char} / 站位 ${manifest.stats.slot} / ` +
      `语音 ${manifest.stats.voice}`,
  );
  if (manifest.stats.voice === 0) {
    console.log('  （语音目录为空或不存在：' + VOICE_DIR + ' —— 游戏将无配音，属正常降级）');
  }
  if (voiceSkipped) {
    console.log(`  ⚠ 语音目录有 ${voiceSkipped} 个文件名不符合 v_<行号>.<ext> 约定，已忽略`);
  }
  const unmappedSlot = Object.keys(slot).filter((k) => !SLOT_MAP[k]);
  if (unmappedSlot.length) {
    console.log('  ⚠ 未映射站位（已落 center）:', unmappedSlot.map(([k]) => k).join(' / '));
  }
}

build();
