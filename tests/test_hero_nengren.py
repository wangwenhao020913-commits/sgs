"""剧本主角「能人志士」（``nengren``）的数据面（M7 票 11；spec §5 第 9 条 / 票 03 §6）。

剧本的初始角色里有「能人志士」：零技能的合成主角（群 / 男 / 4 血）。加载器原先要求"每名
武将至少一个技能"，本票不放松这条纪律，而是给它一条**显式数据形状**：武将表升 schema v2、
新增 ``bare: true``（= "我故意没有技能"）。三个面在这里钉住：

- **数据**：``nengren`` 在 ``heroes.homebrew.json``（v2 表）里、零技能、``bare: true``、
  ``enabled: false``；技能读取面取它也不炸（``hero_skill_ids`` 是空元组）；
- **点名**：剧本 roster 的点名按 id 跨表查找（``hero_by_id``，**不看** ``enabled``）；
- **随机**：随机配将的池是 ``enabled_hero_ids`` 的启用位投影，``nengren`` 不在其中。

两条例外（零技能且无 ``bare`` 判红 / ``bare`` 与技能并存判红）走合成记录，落在
``tests/data/test_heroes_validation.py`` 的三段校验单测里——数据文件里的真实记录只覆盖
正向那一半。
"""

from __future__ import annotations

import pytest

from sgs.data import (
    Gender,
    Kingdom,
    enabled_hero_ids,
    hero_by_id,
    hero_skill_ids,
    load_heroes,
)

NENGREN = "nengren"
"""剧本主角「能人志士」的武将 id（票 03 §6：合成主角、零技能、``enabled: false``）。"""


def test_the_nengren_sheet_is_a_bare_zero_skill_hero() -> None:
    """``nengren`` 从自制包表加载：群 / 男 / 4 血 / 零技能 / ``bare: true`` / 未启用。"""
    table = load_heroes("homebrew")
    hero = table.by_id(NENGREN)

    assert hero is not None, "能人志士必须能从 `heroes.homebrew.json` 加载"
    assert (hero.name, hero.true_name) == ("能人志士", "能人志士")
    assert (hero.kingdom, hero.gender, hero.hp_max) == (Kingdom.QUN, Gender.MALE, 4)
    assert (hero.version, hero.enabled, hero.bare) == ("homebrew", False, True)
    assert hero.skills == (), "能人志士是零技能的合成主角"
    assert hero_skill_ids(NENGREN) == (), "零技能在载体解析这一面是空清单，不是错误"
    assert table.schema_version == 2, "带 `bare` 的表走 schema v2（v1 表继续可读）"


def test_a_script_roster_may_name_nengren_while_the_random_deal_pool_may_not() -> None:
    """同一断言里的两面：点名走按 id 的跨表查找，随机配将走启用池。"""
    named = hero_by_id(NENGREN)

    assert named is not None, "剧本 roster 点名按 id 查（不看 `enabled`）"
    assert named.enabled is False, "它确实是未启用武将——不是靠启用位才被找到的"
    assert NENGREN not in enabled_hero_ids(), "随机配将池仍只读 `enabled`"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
