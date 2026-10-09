"""技能注册表：``skills/`` 是技能定义的**唯一入口**（M1 只留接口空壳，技能本体推 M3）。

M2 最小技能形态的完整落地在票 29 / 票 30；本票只先立起**装备技能的命名空间**，
供加载期跨层校验"每张装备牌都有对应技能"（票 09 §7 #8）：

- 装备**牌名 id 不带前缀**（``crossbow``），装备**技能 id = ``equip.`` + 牌名 id**
  （``equip.crossbow``）——两者不可混（票 09 §1）。
- ``equipment_skill_ids()`` 是只读查询；``data`` 侧加载器调用它做
  ``card_type.equipment_skill_missing``。票 29 / 票 30 把 ``SkillDef`` 与 ``CATALOG``
  立起来后，本函数改为从 ``CATALOG`` 派生同名技能 id 集合，签名不变。
"""

from __future__ import annotations

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

__all__ = ("EQUIPMENT_CARD_TYPE_IDS", "EQUIPMENT_SKILL_IDS", "equipment_skill_ids")


def equipment_skill_ids() -> frozenset[str]:
    """全部装备技能的 id 集合（只读；加载期跨层校验用）。"""
    return EQUIPMENT_SKILL_IDS
