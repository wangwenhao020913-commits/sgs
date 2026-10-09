"""技能注册表：``skills/`` 是技能定义的**唯一入口**（M1 只留接口空壳，技能本体推 M3）。

M2 最小技能形态的完整落地在票 29 / 票 30；本票只先立起**装备技能的命名空间**，
供加载期跨层校验"每张装备牌都有对应技能"（票 09 §7 #8）：

- 装备**牌名 id 不带前缀**（``crossbow``），装备**技能 id = ``equip.`` + 牌名 id**
  （``equip.crossbow``）——两者不可混（票 09 §1）。
- ``equipment_skill_ids()`` 是只读查询；``data`` 侧加载器调用它做
  ``card_type.equipment_skill_missing``。票 29 / 票 30 把 ``SkillDef`` 与 ``CATALOG``
  立起来后，本函数改为从 ``CATALOG`` 派生同名技能 id 集合，签名不变。

主公技在 M2**只留接口位**（票 13 §4）：标准版确有【护驾】/【激将】/【救援】，但它们
全部寄宿在武将技能上，而武将技能整类 out of scope ⇒ M2 一个都不可发动。这里保留
`(身份==主公) ∧ (类型==主公技)` 的合取式作为可断言的门控，**不新增 ``SkillDef`` 字段**
（那会翻票 07 的冻结面）。
"""

from __future__ import annotations

from sgs.data import Identity

EQUIPMENT_CARD_TYPE_IDS: tuple[str, ...] = (
    "crossbow",
    "qinggang_sword",
    "double_sword",
    "green_dragon_blade",
    "serpent_spear",
    "axe",
    "halberd",
    "qilin_bow",
    "ice_sword",
    "eight_diagrams",
    "renwang_shield",
    "chitu",
    "jueying",
    "dilu",
    "zhuahuang_feidian",
    "dawan",
    "zixing",
)
"""标准版 17 张装备的**牌名 id**（9 武器 + 2 防具 + 6 坐骑），不带前缀。"""

EQUIPMENT_SKILL_IDS: frozenset[str] = frozenset(
    f"equip.{card_type_id}" for card_type_id in EQUIPMENT_CARD_TYPE_IDS
)
"""装备**技能 id = ``equip.`` + 牌名 id**（票 09 §1）：由牌名 id 派生，不另抄一遍。"""

LORD_SKILL_IDS: frozenset[str] = frozenset()
"""登记在**主公技类型**上的技能 id（票 07 §4 允许清单的右项）。

M2 **一个都没有**：武将技能整类 out of scope（票 13 §4）⇒ 主公技门控恒为假。
M3 加武将时把主公技 id 填进来即可，门控与查询面都不改形。
"""

REJUDGE_SKILL_IDS: frozenset[str] = frozenset()
"""有**改判**能力的技能 id（【鬼才】/【鬼道】一类）。

标准版里改判全在武将技能上，而武将技能整类 out of scope ⇒ M2 恒空。
判定帧的改判窗口因此**真 dispatch 但队列恒空**（票 10 §4）。
"""

__all__ = (
    "EQUIPMENT_CARD_TYPE_IDS",
    "EQUIPMENT_SKILL_IDS",
    "LORD_SKILL_IDS",
    "REJUDGE_SKILL_IDS",
    "equipment_skill_ids",
    "lord_skill_allowed",
    "rejudge_capable_seats",
)


def equipment_skill_ids() -> frozenset[str]:
    """全部装备技能的 id 集合（只读；加载期跨层校验用）。"""
    return EQUIPMENT_SKILL_IDS


def rejudge_capable_seats() -> tuple[int, ...]:
    """当前**有改判能力**的座位（票 10 §4 的响应队列左项）。

    M2 没有改判技能（``REJUDGE_SKILL_IDS`` 为空），故恒为空元组——判定帧的改判响应
    队列因此恒空、无询问、无日志节点，"队列恒空即终态正确"。M3 立起技能实例
    （``(owner, skill_id, source)``）后在此把 ``REJUDGE_SKILL_IDS`` 解析成座位集合。
    """
    if REJUDGE_SKILL_IDS:
        raise NotImplementedError("M3 才有改判技能：座位的技能实例解析尚未建立")
    return ()


def lord_skill_allowed(identity: Identity | None, skill_id: str) -> bool:
    """主公技门控：`(身份 == 主公) ∧ (技能属于主公技类型)`（票 07 §4 的允许清单）。

    M2 还没有 ``SkillDef`` 的类型字段（票 07 冻结面不新增），故"属于主公技类型"
    由 ``LORD_SKILL_IDS`` 的**成员资格**表达；M3 立起 ``skill_type`` 后这里改为读字段，
    调用方与门控形状都不变。``LORD_SKILL_IDS`` 在 M2 为空，故本函数对任何输入都返回
    ``False``——"M2 一个都不可发动"因此是可断言的系统事实，而不只是一句约定
    （U-13-1：真实门控要等 M3 的武将技能登记进来才可测）。
    """
    return identity is Identity.LORD and skill_id in LORD_SKILL_IDS
