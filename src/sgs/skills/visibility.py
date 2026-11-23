"""可见性表：每一步答案在公开面上的处置（ai-view 票 05；spec §2.4）。

**只登记边界，不登记语义**：值回答"答案值进不进公开面、以哪一种形态进"，不回答
"这一步意味着什么"（语义由权重学，不手写；见 spec §2.3）。闭集三值见
:class:`StepVisibility`。

这张表为什么住在这里、而不是塞进 ``catalog.py``：``SkillDef`` 的各族构造器分散在
二十多个模块里，把逐技能的行写回各构造器等于让"边界"跟着"执行结构"走；而
``SkillFlow`` 也不收它（执行结构与事实面结构随不同原因变化，绑死会让每次改动互相牵扯）。
于是：

- **唯一真源** = 本模块的两张表（技能步骤 / 卡牌解析步骤）；
- **合并点** = ``catalog.CATALOG`` 的字典推导（``step_visibility`` 字段的唯一生产者）；
- **将来的读取方** = 票 06 的投影；本票只登记边界并让**未登记的新步骤**判红。

两道断言（``tests/test_visibility_table.py``，票档门禁）：

1. ``FLOWS`` 里每条流水线的每个叶子步骤都必须登记；
2. 登记的每个键都必须在该技能的某条流水线里真实存在——**不静默落默认**。

行的派生与核对由 ``tools/dump_public_surface.py`` 完成：它从各 ``SkillFlow`` / ``Hook``
派生出 ``(技能, 步骤名, kind, 默认可见性)`` 给人确认默认值 / 改例外，而不是让人从零
枚举一百多条技能。**默认值不参与运行期**：表里每一行都是显式登记的。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeAlias

__all__ = [
    "CARD_RESOLUTION_VISIBILITY",
    "STEP_VISIBILITY",
    "StepVisibility",
]


class StepVisibility(str, Enum):
    """一步**答案**在公开面上的处置（闭集三值；spec §2.4）。

    - ``PUBLIC``：这一步的答案公开（选目标 / 选选项 / 弃置一张牌 / 亮出一张牌，如
      【离间】的 ``user`` / ``victim``、制衡的 ``card``）；
    - ``HIDDEN``：不公开（观星的摆放、选牌时的扣置、【蛊惑】扣置的是哪一张、手牌交到
      别人手里，牌面仍在隐藏区）；
    - ``CLAIM``：公开面只装**声称值**，真值永不进视图（【蛊惑】声明的牌名）。

    **键 = 步骤名，不是步序**：界【离间】与标准【离间】的选择次序相反、三个键一字不改；
    按第几步索引会当场错位。
    """

    PUBLIC = "PUBLIC"
    HIDDEN = "HIDDEN"
    CLAIM = "CLAIM"


StepVisibilityTable: TypeAlias = Mapping[str, Mapping[str, StepVisibility]]
"""``(技能 id / card:<牌名>) -> 步骤名 -> 值``（本模块两张表的共同形状）。"""


STEP_VISIBILITY: StepVisibilityTable = {
    "equip.axe": {"card": StepVisibility.PUBLIC},
    "equip.green_dragon_blade": {"card": StepVisibility.PUBLIC},
    "equip.qilin_bow": {"card": StepVisibility.PUBLIC},
    "hero.caopi_mobile.fangzhu": {"seat": StepVisibility.PUBLIC},
    "hero.caozhen_mobile.sidi": {"card": StepVisibility.PUBLIC},
    "hero.chenqun.dingpin": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.chenzhen.shameng": {
        "card1": StepVisibility.PUBLIC,
        "card2": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.daqiao.liuli": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.dianwei_mobile.qiangxi": {"option": StepVisibility.PUBLIC},
    "hero.diaochan.lijian": {
        "card": StepVisibility.PUBLIC,
        "user": StepVisibility.PUBLIC,
        "victim": StepVisibility.PUBLIC,
    },
    "hero.dongzhuo_mobile.bengkuai": {"option": StepVisibility.PUBLIC},
    "hero.feng_guanyu.danqi": {"choice": StepVisibility.PUBLIC},
    "hero.feng_yujin.jieyue": {
        "give": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.guyong.shenxing": {"card": StepVisibility.PUBLIC},
    "hero.haozhao.zhengku": {"seat": StepVisibility.PUBLIC},
    "hero.huatuo.qingnang": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_caozhen_mobile.sidi": {
        # 谁被「司敌」是公开标记；**预测他会打谁**只有曹真自己知道（用户 Q6）。
        "first": StepVisibility.PUBLIC,
        "target": StepVisibility.HIDDEN,
    },
    "hero.jie_chenqun_mobile.dingpin": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_daqiao.guose": {
        "card": StepVisibility.PUBLIC,
        "option": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_daqiao.liuli": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_diaochan.lijian": {
        "card": StepVisibility.PUBLIC,
        "user": StepVisibility.PUBLIC,
        "victim": StepVisibility.PUBLIC,
    },
    "hero.jie_gongsunzan.qiaomeng": {"card": StepVisibility.HIDDEN},
    "hero.jie_guanyu.yijue": {
        "card": StepVisibility.PUBLIC,
        "cost": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_guyong_mobile.shenxing": {"card": StepVisibility.PUBLIC},
    "hero.jie_huanggai.kurou": {"card": StepVisibility.PUBLIC},
    "hero.jie_huatuo.chuli": {"seat": StepVisibility.PUBLIC},
    "hero.jie_huaxiong.shizhan": {"seat": StepVisibility.PUBLIC},
    "hero.jie_jushou_mobile.jianying": {
        "card": StepVisibility.PUBLIC,
        "name": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_lidian.xunxun": {"cards": StepVisibility.HIDDEN},
    "hero.jie_liubei.jijiang": {"seat": StepVisibility.PUBLIC},
    "hero.jie_liubei.rende": {
        "card": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_lvbu.liyu": {"card": StepVisibility.HIDDEN},
    "hero.jie_lvmeng.gongxin": {"seat": StepVisibility.PUBLIC},
    "hero.jie_simayi.fankui": {"card": StepVisibility.HIDDEN},
    "hero.jie_sunquan.zhiheng": {"card": StepVisibility.PUBLIC},
    "hero.jie_sunshangxiang.jieyin": {
        "card": StepVisibility.PUBLIC,
        "option": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_weiyan_mobile.qimou": {"option": StepVisibility.PUBLIC},
    "hero.jie_wuyi_mobile.benxi": {"card": StepVisibility.PUBLIC},
    "hero.jie_xiahoudun.qingjian": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_xushu.jianyan": {"type": StepVisibility.PUBLIC},
    "hero.jie_xushu.zhuhai": {"card": StepVisibility.PUBLIC},
    "hero.jie_zhangjiao_mobile.huangtian": {"card": StepVisibility.HIDDEN},
    "hero.jie_zhangjiao_mobile.leiji": {"seat": StepVisibility.PUBLIC},
    "hero.jie_zhangliao.tuxi": {"seat": StepVisibility.PUBLIC},
    "hero.jie_zhouyu.fanjian": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.jie_zhugeliang.guanxing": {"cards": StepVisibility.HIDDEN},
    "hero.liubei.jijiang": {"seat": StepVisibility.PUBLIC},
    "hero.liubei.rende": {
        "card": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.liuqi.wenji": {
        "card": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.liuyan.limu": {"card": StepVisibility.PUBLIC},
    "hero.lu_su_mobile.dimeng": {
        "first": StepVisibility.PUBLIC,
        "second": StepVisibility.PUBLIC,
    },
    "hero.lukang_mobile.duanyan": {"column": StepVisibility.PUBLIC},
    "hero.lukang_mobile.huairou": {"card": StepVisibility.PUBLIC},
    "hero.maliang.yingyuan": {"seat": StepVisibility.PUBLIC},
    "hero.mayunlu.fengpo": {"option": StepVisibility.PUBLIC},
    "hero.pangde_mobile.mengjin": {"card": StepVisibility.PUBLIC},
    "hero.pangtong_sp.guolun": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.panjun.guanwei": {"card": StepVisibility.PUBLIC},
    "hero.qunying_luxun.qianxun": {
        "card": StepVisibility.PUBLIC,
        "cost": StepVisibility.PUBLIC,
        "effect": StepVisibility.PUBLIC,
        "equip": StepVisibility.PUBLIC,
        "product": StepVisibility.PUBLIC,
        "to": StepVisibility.PUBLIC,
        "type": StepVisibility.PUBLIC,
    },
    "hero.qunying_niheng.kuangcai": {
        "declaration": StepVisibility.PUBLIC,
        "value": StepVisibility.PUBLIC,
    },
    "hero.qunying_niheng.shejian": {"cost": StepVisibility.PUBLIC},
    "hero.shen_ganning.jieying": {"seat": StepVisibility.PUBLIC},
    "hero.shen_ganning.poxi": {"seat": StepVisibility.PUBLIC},
    "hero.shen_lvmeng_mobile.gongxin": {"seat": StepVisibility.PUBLIC},
    "hero.shen_zhouyu_mobile.qinyin": {"option": StepVisibility.PUBLIC},
    "hero.shen_zhugeliang.dawu": {"card": StepVisibility.HIDDEN},
    "hero.shen_zhugeliang.kuangfeng": {
        "card": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.shen_zhugeliang.qixing": {"cards": StepVisibility.HIDDEN},
    "hero.shi_zhaoyun.wuyi": {"seat": StepVisibility.PUBLIC},
    "hero.simayi.fankui": {"card": StepVisibility.HIDDEN},
    "hero.sunjian_mobile.yinghun": {
        "option": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.sunquan.zhiheng": {"card": StepVisibility.PUBLIC},
    "hero.sunshangxiang.jieyin": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.sunshangxiang_sp.liangzhu": {"option": StepVisibility.PUBLIC},
    "hero.taishici.tianyi": {"seat": StepVisibility.PUBLIC},
    "hero.tao_liubei.yiqi": {"type": StepVisibility.PUBLIC},
    "hero.xiahouyuan.shensu": {"items": StepVisibility.PUBLIC},
    "hero.xunyu.jieming": {"seat": StepVisibility.PUBLIC},
    "hero.xunyu.quhu": {"seat": StepVisibility.PUBLIC},
    "hero.yuji_mobile.guohuo": {
        "card": StepVisibility.HIDDEN,
        "name": StepVisibility.CLAIM,
    },
    "hero.zhangjiao_mobile.huangtian": {"card": StepVisibility.HIDDEN},
    "hero.zhangjiao_mobile.leiji": {"seat": StepVisibility.PUBLIC},
    "hero.zhangliao.tuxi": {"seat": StepVisibility.PUBLIC},
    "hero.zhangxiu.congjian": {
        "card": StepVisibility.HIDDEN,
        "seat": StepVisibility.PUBLIC,
    },
    "hero.zhangxiu.xiongluan": {"seat": StepVisibility.PUBLIC},
    "hero.zhouyu.fanjian": {
        "card": StepVisibility.PUBLIC,
        "seat": StepVisibility.PUBLIC,
        "suit": StepVisibility.PUBLIC,
    },
    "hero.zhugeliang.guanxing": {"cards": StepVisibility.HIDDEN},
    "hero.zhugezhan.zuilun": {"cards": StepVisibility.HIDDEN},
    "hero.zuoci.huashen": {"skill": StepVisibility.PUBLIC},
}
"""``skill_id -> 步骤名 -> 值``（票 05 的**技能侧**表；只收 ``SkillFlow`` 的叶子步骤）。

键 = 步骤名（``FlowStep.key``，也即 `step_requirements` 的**清单键**）；一行一条**边界**。
注意两者**不是逐条一一对应**：投影的逐步文案对 ``SELECT_CARDS`` 恒读 ``card``、并会为
手写问补键 / 合并键（例：锋·于禁【节钺】的可见性键是 ``seat`` / ``give``，文案表另有
``card`` / ``choice``）。**不要**按文案键反查可见性。

**本表只覆盖 ``SkillFlow`` 步骤**：没有流式步骤的技能不出现。手写段机的答案（【蛊惑】的
``target`` / ``designation``、【佐幸】的 ``name`` / ``target`` / ``designation`` 一类）不是
``FlowStep``，不在本表；它们的公开面边界是票 06 投影时的另一条读法（票面两道断言只约束
``FLOWS`` 派生出来的键，不静默落默认）。

**不与步序绑定**：同一技能的多条流水线（不同时机 / 按状态切换）共用这一行；重复的步骤名
在派生时必须落到同一个键（``jie_zhangjiao_mobile.leiji`` 的 ``seat`` 出现在两条流水线里，
值相同）。
"""


CARD_RESOLUTION_VISIBILITY: StepVisibilityTable = {
    "card:dismantlement": {"pick": StepVisibility.PUBLIC},
    "card:snatch": {"pick": StepVisibility.PUBLIC},
    "card:barbarian_invasion": {"target": StepVisibility.PUBLIC},
    "card:arrow_barrage": {"target": StepVisibility.PUBLIC},
    "card:peach_garden": {"target": StepVisibility.PUBLIC},
    "card:harvest": {"target": StepVisibility.PUBLIC},
    "card:iron_chain": {"target": StepVisibility.PUBLIC},
}
"""``card:<牌名> -> 解析步骤名 -> 值``（**卡牌侧**的表；用户 2026-11-23 裁定的追加要求）。

卡牌不是技能、没有 ``SkillFlow``，但它们的结算同样有"要玩家作答 / 逐目标生效"的步骤：

- ``pick``：区域选牌（【过河拆桥】"拿走哪一张 / 哪个区"、【顺手牵羊】同形）——答案公开；
- ``target``：**每个目标一条效果**（南蛮 / 万箭 / 桃园 / 五谷 / 铁索）。表里的一行是
  **模式**，不是一条聚合的 AoE：脚手架按 ``--players N`` 把它展开成 **N 行**（每个座位
  一格，主公那一格也是其中一行），一局 N 个目标因此有 N 条可归因效果（票 04 的公开面
  缓冲按目标记事实）。实际哪几格是目标由目标规则决定，本表只管"这些格里的效果公开"。

**拆分依据是行为表**（``CARD_BEHAVIORS``）：逐目标 = 目标规则可给出 >1 个目标；``pick`` =
效果里调了脚手架里 ``CARD_ZONE_PICK_OPS`` 登记的选牌原语。脚手架按同一份派生打印，登记与
派生双向核对（未登记即红、幽灵登记即红）；**新增区选原语时要在那张表登记**
（与 ``ENGINE_LEVEL_FACT_CLASSES`` 同一纪律），否则它不会出现在派生里。其它卡牌提问
（火攻的亮牌 / 弃牌、借刀的响应…）不在本表——本表只覆盖追加要求点名的两类。

这**不违反**票面"不登记引擎级事件"那一条：那条约束的是 ``STEP_VISIBILITY``（技能步骤
表）——卡牌解析事实无起因、由票 04 的封闭清单管，不会混进技能表；本表是用户追加要求
点名要的**卡牌解析**边界，单独一张、单独核对。
"""

