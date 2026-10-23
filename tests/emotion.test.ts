/**
 * 情绪判别（雀跃演出触发器）· 单测
 *
 * 词表写在 src/engine/text/emotion.ts；这里的用例**全部来自真实台本**，
 * 尤其是那些「词面像、心情不像」的陷阱 —— 改词表时先跑这组，别让误判回潮。
 */

import { describe, it, expect } from 'vitest';

import { isPositiveHighEmotion } from '../src/engine/text/emotion.ts';

describe('isPositiveHighEmotion（雀跃触发器）', () => {
  it('命中：全篇 6 处真实正向括注（震惊 / 欢愉）', () => {
    const hits = [
      '阳太｜嬉笑',
      '樱｜抬头・眼睛微微睁大', // 台本原文：「那是她表达惊讶的」
      '樱｜偏头看我・眼睛很亮',
      '樱｜安静地看着我・眼睛亮了一下',
      '樱｜眼睛微微弯了一下',
      '樱｜眼神里有一丝意外・点头',
    ];
    for (const pose of hits) expect(isPositiveHighEmotion(pose), pose).toBe(true);
  });

  it('排除：愤怒 / 生气 / 敌意（主人指定不算）', () => {
    const nope = ['龙司｜脸色瞬间沉下', '怒目', '生气地说', '沉着脸', '咬牙', '铁青着脸'];
    for (const pose of nope) expect(isPositiveHighEmotion(pose), pose).toBe(false);
  });

  it('排除：词面像、心情不像的陷阱（改词表时的回归线）', () => {
    const traps = [
      '阳太｜喘・弯腰撑膝', // 「弯」≠「眼睛弯」
      '九条院｜完美微笑', // 伪装面，在反派脸上蹦一下会毁人设
      '九条院｜捧文件・完美微笑',
      '亚里沙｜盯着他・似笑非笑', // 玩味 ≠ 雀跃
      '龙司｜抬头看天・极淡的松', // 「极淡」＝低情绪
      '樱｜平静',
      '龙司｜面无表情',
      '樱｜默读・坐姿',
      '', // 空串
    ];
    for (const pose of traps) expect(isPositiveHighEmotion(pose), pose).toBe(false);
  });

  it('否定优先：同一条里既有正向词又有怒意 → 以否定为准', () => {
    expect(isPositiveHighEmotion('眼睛亮了起来・沉下脸')).toBe(false);
    expect(isPositiveHighEmotion('惊喜地睁大眼・怒气未消')).toBe(false);
  });

  it('留扩展位：台本侧将来写的新括注也能命中', () => {
    const future = ['樱｜眼睛亮亮的・雀跃', '阳太｜兴奋地挥手', '亚里沙｜睁大眼睛', '樱｜欢呼出声'];
    for (const pose of future) expect(isPositiveHighEmotion(pose), pose).toBe(true);
  });
});
