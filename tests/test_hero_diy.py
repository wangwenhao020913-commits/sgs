"""diy-heroes 票 04：``HeroSkill.skill_id`` 显式引用**已登记**的技能（数据-only）。

墙 1 的实测形态：技能条目只有短 id，对外技能 id 由 :func:`sgs.data.hero_skill_id`
派生成 ``hero.<武将 id>.<短 id>``；而 ``CATALOG`` 是模块导入期构建的闭集。⇒ 自制武将照
既有形状写 ``{"id": "wusheng"}`` 会派生出一个不存在的 ``hero.<自制武将>.wusheng``，
加载期报 ``[hero.skill_missing]``——想复用【武圣】就必须再写一份 Python 定义。

本票给条目加**可选**的全限定 ``skill_id``（spec 裁定 3：只做"复用已登记技能"的最小拆法）：
给了就不再看短 id 派生，``None``（缺省）的既有数据逐字节走原路。刻意**不**做：不改
:func:`sgs.data.hero_skill_id` 的派生规则、不改代码侧 ``HeroSkillDecl``、不做运行时插件加载。

测试只经公开接缝：``validate_heroes`` / ``hero_card_skill_ids`` / ``hero_skill_ids`` /
``hero_placement_decls``（数据层）与 ``seat_skill_display`` / ``build_skills``（接入层）。
DIY 表**不进官方池**：用 ``monkeypatch`` 把 :func:`sgs.data.loader.load_hero_tables` 换成
"既有武将表 + 这张 DIY 表"，故 ``HERO_SUPPLEMENT_PACK_IDS`` / 池计数一处不动。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    """把 ``tools/`` 与 ``src/`` 放进 ``sys.path``（幂等；``sgs`` 与 ``sgs_web`` 都可导入）。"""
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import (  # noqa: E402
    DataLoadError,
    HeroTable,
    LoadError,
    PlacementReturn,
    SkillType,
    hero_card_skill_ids,
    hero_placement_decls,
    hero_skill_id,
    hero_skill_ids,
    load_content_pack,
    load_hero_tables,
    load_heroes,
    validate_heroes,
)
from sgs.engine import Engine  # noqa: E402
from sgs.state import ActivateSkill  # noqa: E402
from sgs_web.projection import build_skills, seat_skill_display  # noqa: E402
from support.positions import SeatSpec, build_state  # noqa: E402

REUSED_SKILL = "hero.jie_guanyu.wusheng"
"""界关羽【武圣】：``CATALOG`` 里已登记、无 ``placement`` / ``granted_by``，当"复用"的靶子。"""

LIMITED_SKILL = "hero.pangtong_sp.songsang"
"""SP庞统【送丧】：**限定技**（``SkillType.LIMITED``），当"数据声明与定义不一致"的靶子。"""

PLACEMENT_SKILL = "hero.jie_luxun.qianxun"
"""界陆逊【谦逊】：唯一登记在案的**放置类**技能（``SkillDef.places_on_hero_card``）。"""


def _diy_raw(
    *,
    skill_id: str | None = REUSED_SKILL,
    skill_type: str = "NORMAL",
    short_id: str = "wusheng",
    extra_skills: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """一张自制武将的武将表（JSON 形状）：一个武将、一条引用已登记技能的条目。

    ``short_id`` 是条目在数据文件里的**行键**（``granted_by`` / 形状表读它），与显式
    ``skill_id`` 的末段**可以不同**——端到端那条断言要钉的正是这种"短 id 与目标技能末段
    不一致"的情形（旧派生 ``hero.<武将>.<短 id>`` 在这里必然是错的 id）。
    """
    skill: dict[str, Any] = {"id": short_id, "name": "武圣", "skill_type": skill_type}
    if skill_id is not None:
        skill["skill_id"] = skill_id
    return {
        "schema_version": 1,
        "pack_id": "diy",
        "heroes": [
            {
                "id": "myhero",
                "name": "自制武将",
                "true_name": "自制",
                "kingdom": "qun",
                "gender": "male",
                "hp_max": 4,
                "version": "diy",
                "enabled": True,
                "skills": [skill, *extra_skills],
            }
        ],
    }


def _validate(raw: dict[str, Any]) -> HeroTable:
    return validate_heroes(raw, file="heroes.diy.json")


def _errors(raw: dict[str, Any]) -> tuple[LoadError, ...]:
    with pytest.raises(DataLoadError) as excinfo:
        _validate(raw)
    return excinfo.value.errors


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    """清掉进程级缓存：两个键都含 ``pack_id``，而 DIY 表只在**本测试**里存在。"""
    for cached in (hero_card_skill_ids, hero_placement_decls):
        cached.cache_clear()
    yield
    for cached in (hero_card_skill_ids, hero_placement_decls):
        cached.cache_clear()


def _install(
    monkeypatch: pytest.MonkeyPatch,
    raw: dict[str, Any] | None = None,
    *,
    keep_existing: bool = True,
) -> HeroTable:
    """把加载面换成"既有武将表 + 这张 DIY 表"（**不进** ``HERO_SUPPLEMENT_PACK_IDS``）。"""
    table = _validate(raw if raw is not None else _diy_raw())
    existing = load_hero_tables() if keep_existing else ()
    monkeypatch.setattr(
        "sgs.data.loader.load_hero_tables",
        lambda pack_id="standard": (*existing, table),
    )
    for cached in (hero_card_skill_ids, hero_placement_decls):
        cached.cache_clear()
    return table


# ---------------------------------------------------------------------------
# ① 复用：显式 skill_id 指向别的武将名下已登记的技能
# ---------------------------------------------------------------------------


def test_a_diy_hero_can_reuse_an_already_registered_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """给条目写 ``skill_id: hero.jie_guanyu.wusheng`` ⇒ 校验通过、载体解析返回那条 id。"""
    table = _install(monkeypatch)

    assert table.by_id("myhero") is not None
    assert hero_card_skill_ids("myhero") == (REUSED_SKILL,)


# ---------------------------------------------------------------------------
# ② 引用闭合：显式 id 必须命中 CATALOG（fail-closed，不是运行期特性）
# ---------------------------------------------------------------------------


def test_an_unknown_explicit_skill_id_is_rejected() -> None:
    """``skill_id: hero.nobody.wusheng`` ⇒ ``heroes.skill_id_not_found``（核 rule_id 与 loc）。"""
    errors = _errors(_diy_raw(skill_id="hero.nobody.wusheng"))

    assert [error.rule_id for error in errors] == ["heroes.skill_id_not_found"]
    assert errors[0].loc == "/heroes/0/skills/0/skill_id"
    assert errors[0].got == repr("hero.nobody.wusheng")


# ---------------------------------------------------------------------------
# ③ 类型对账：数据声明 ↔ 被引用定义的 SkillType
# ---------------------------------------------------------------------------


def test_an_explicit_skill_id_must_agree_with_the_declared_type() -> None:
    """条目写 ``NORMAL``、被引用的【送丧】是 ``LIMITED`` ⇒ ``heroes.skill_id_type_mismatch``。"""
    errors = _errors(_diy_raw(skill_id=LIMITED_SKILL, skill_type="NORMAL"))

    assert [error.rule_id for error in errors] == ["heroes.skill_id_type_mismatch"]
    assert errors[0].loc == "/heroes/0/skills/0/skill_type"
    assert errors[0].got == SkillType.NORMAL.value
    assert SkillType.LIMITED.value in errors[0].expected


# ---------------------------------------------------------------------------
# ④ 缺省不动：既有数据（不写 skill_id）的派生值逐字相同
# ---------------------------------------------------------------------------


def test_the_default_derivation_is_untouched() -> None:
    """抽两张真实数据表：条目的 ``skill_id`` 都是 ``None``，派生仍是 ``hero.<武将>.<短 id>``。

    期望值用**手写的字面格式**（``f"hero.{hero.id}.{skill.id}"``）而不是再调一次派生函数：
    否则断言与被测实现同源，改坏了也照样绿。
    """
    for pack_id in ("standard", "jie"):
        for hero in load_heroes(pack_id).heroes:
            assert [skill.skill_id for skill in hero.skills] == [None] * len(hero.skills)
            assert hero_skill_ids(hero.id) == tuple(
                f"hero.{hero.id}.{skill.id}" for skill in hero.skills
            )
            assert hero_card_skill_ids(hero.id) == tuple(
                f"hero.{hero.id}.{skill.id}"
                for skill in hero.skills
                if skill.granted_by is None
            )

    assert hero_skill_id("jie_guanyu", "wusheng") == "hero.jie_guanyu.wusheng"
    assert hero_skill_ids("caocao") == ("hero.caocao.jianxiong", "hero.caocao.hujia")


# ---------------------------------------------------------------------------
# ⑤ 放置类技能：三项声明照写，声明表按**被引用**的 id 建键（四个派生点的最后一个）
# ---------------------------------------------------------------------------


def test_a_diy_hero_can_reuse_a_placement_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """只装 DIY 表（``keep_existing=False``）：放置声明表的键是**被引用**的技能 id。"""
    raw = _diy_raw(skill_id=PLACEMENT_SKILL)
    raw["heroes"][0]["skills"][0]["placement"] = {
        "visibility": "public",
        "return_timing": "turn_end",
        "extra_effect": False,
    }
    _install(monkeypatch, raw, keep_existing=False)

    decls = hero_placement_decls()
    assert list(decls) == [PLACEMENT_SKILL]
    assert decls[PLACEMENT_SKILL].return_timing is PlacementReturn.TURN_END


def test_a_reused_placement_skill_still_requires_its_declaration() -> None:
    """显式引用不豁免既有对账：引用放置类技能而不写 ``placement`` ⇒ ``heroes.placement_missing``。"""
    errors = _errors(_diy_raw(skill_id=PLACEMENT_SKILL))

    assert [error.rule_id for error in errors] == ["heroes.placement_missing"]


# ---------------------------------------------------------------------------
# ⑥ 撞车：同一武将内两条条目解析到同一个全限定 id
# ---------------------------------------------------------------------------


def test_two_entries_resolving_to_the_same_skill_id_are_rejected() -> None:
    """两条短 id 不同、解析后同 id 的条目 ⇒ ``heroes.skill_id_duplicate``（照既有那条的姿态）。

    既有 ``hero.skill_duplicate_id`` 比的是**短 id**，显式引用把两条不同的短 id 解析到同一条
    已登记技能上，它管不到——故另开这一条，``loc`` 指向第二个条目的 ``skill_id``。
    """
    alias = {
        "id": "wusheng_alias",
        "skill_id": REUSED_SKILL,
        "name": "武圣",
        "skill_type": "NORMAL",
    }
    errors = _errors(_diy_raw(extra_skills=(alias,)))

    assert [error.rule_id for error in errors] == ["heroes.skill_id_duplicate"]
    assert errors[0].loc == "/heroes/0/skills/1/skill_id"
    assert errors[0].got == repr(REUSED_SKILL)


def test_two_heroes_may_carry_the_same_registered_skill() -> None:
    """**跨武将**复用同一条技能不是撞车：这正是本票要开的门（两张牌各有一份【武圣】）。"""
    raw = _diy_raw()
    raw["heroes"].append(
        {
            "id": "myhero2",
            "name": "自制武将二号",
            "true_name": "自制二号",
            "kingdom": "shu",
            "gender": "female",
            "hp_max": 3,
            "version": "diy",
            "enabled": True,
            "skills": [
                {
                    "id": "wusheng",
                    "skill_id": REUSED_SKILL,
                    "name": "武圣",
                    "skill_type": "NORMAL",
                }
            ],
        }
    )

    assert _validate(raw).by_id("myhero2") is not None


# ---------------------------------------------------------------------------
# ⑦ 端到端：这张 DIY 武将真的进了一局，两条技能清单都列得出、可用性语义没坏
# ---------------------------------------------------------------------------


def test_a_diy_hero_reaches_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_state`` 里坐这张 DIY 武将：详情 / 按钮清单都列出【武圣】，``available`` 正常。

    证明的不是"能加载"，而是"能玩"：载体解析（``resolve_instances`` → ``hero_card_skill_ids``）
    在真实产出路径上认得出这条**显式引用**，接入层的两份清单据同一条 id 渲染。
    """
    # 条目的行键（``my_wusheng``）与目标技能末段（``wusheng``）**故意不同**：旧派生
    # ``hero.myhero.my_wusheng`` 在这里一定是错的 id，故这条断言钉的是"读显式引用"而不是巧合。
    _install(monkeypatch, _diy_raw(short_id="my_wusheng"))
    state = build_state(pack=load_content_pack(), seats=[SeatSpec(hero="myhero")])
    engine = Engine.from_state(state, seed=1)

    listed = {
        entry["skill_id"]: entry
        for entry in seat_skill_display(SimpleNamespace(engine=engine), 0)
    }
    assert REUSED_SKILL in listed, "武将详情清单必须列出显式引用的技能"
    assert listed[REUSED_SKILL]["name"] == "武圣"
    assert listed[REUSED_SKILL]["carrier"] == "HERO"
    assert "hero.myhero.my_wusheng" not in listed, "短 id 派生的幽灵 id 不许出现在清单里"

    idle = SimpleNamespace(engine=engine, pending=None)
    operable = {entry["skill_id"]: entry for entry in build_skills(idle, 0)}
    assert REUSED_SKILL in operable, "按钮清单必须列出显式引用的技能"
    assert operable[REUSED_SKILL]["available"] is False, "没有候选 ⇒ 不可点（可用性不是编的）"

    asked = SimpleNamespace(
        actor=0,
        legal_actions=(ActivateSkill(skill_id=REUSED_SKILL, answers_seq=0),),
    )
    offered = {
        entry["skill_id"]: entry
        for entry in build_skills(SimpleNamespace(engine=engine, pending=asked), 0)
    }
    assert offered[REUSED_SKILL]["available"] is True, "询问给了候选 ⇒ 这条技能就可点"


# ---------------------------------------------------------------------------
# ⑧ 空串不是"没写"：显式引用缺省是 ``None``，``""`` 按结构错误拒收
# ---------------------------------------------------------------------------


def test_an_empty_explicit_skill_id_is_a_structural_error() -> None:
    """``skill_id: ""`` 不许静默退回短 id 派生：空串是"写错了"，不是"没写"（缺省 = ``None``）。"""
    errors = _errors(_diy_raw(skill_id=""))

    assert [error.rule_id for error in errors] == ["structure.string_too_short"]
    assert errors[0].loc == "/heroes/0/skills/0/skill_id"
