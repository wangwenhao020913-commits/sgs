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
from sgs.skills import CATALOG, AppearanceKind, zuoci as zuoci_module  # noqa: E402
from sgs.skills.zuoci import avatar_skill_rows, eligible_skill_ids  # noqa: E402
from sgs.state import ActivateSkill, Marker, PlayerPublic  # noqa: E402
from sgs_web.descriptions import skill_description  # noqa: E402
from sgs_web.display import option_details, seat_display  # noqa: E402
from sgs_web.narrative import _name_tables  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    APPEARANCE_BUILDERS,
    build_skills,
    seat_skill_display,
)
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
    for cached in (hero_card_skill_ids, hero_placement_decls, _name_tables):
        cached.cache_clear()
    yield
    for cached in (hero_card_skill_ids, hero_placement_decls, _name_tables):
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
    tables = (*existing, table)
    monkeypatch.setattr(
        "sgs.data.loader.load_hero_tables",
        lambda pack_id="standard": tables,
    )
    # 叙事模块在导入期直接绑定了这个函数，按名补丁才算数（`sgs.data.loader` 那份读不到）。
    monkeypatch.setattr(
        "sgs_web.narrative.load_hero_tables",
        lambda pack_id="standard": tables,
    )
    for cached in (hero_card_skill_ids, hero_placement_decls, _name_tables):
        cached.cache_clear()
    return table


def _installed(
    monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any] | None = None
) -> tuple[HeroTable, ...]:
    """装好的**加载面**本身（`option_details` / `seat_display` 读 `heroes=` 的那一份）。"""
    return (*load_hero_tables(), _install(monkeypatch, raw))


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


def _placement(*, return_timing: str = "turn_end") -> dict[str, Any]:
    """一项放置声明（缺省 = 界陆逊【谦逊】那份：public / turn_end / 无附加效果）。"""
    return {
        "visibility": "public",
        "return_timing": return_timing,
        "extra_effect": False,
    }


def _second_diy_hero(*, short_id: str, placement: dict[str, Any]) -> dict[str, Any]:
    """同一张 DIY 表里的第二个武将：也引用同一个**放置类**技能。"""
    return {
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
                "id": short_id,
                "skill_id": PLACEMENT_SKILL,
                "name": "谦逊",
                "skill_type": "NORMAL",
                "placement": placement,
            }
        ],
    }


def test_two_heroes_with_conflicting_placement_decls_are_rejected() -> None:
    """两个武将复用同一条放置技能、**声明不同** ⇒ ``heroes.placement_conflict``（票 08 B）。

    ``hero_placement_decls`` 按解析后的 id 建全局表：后写会静默覆盖先写，引擎就用 A 的归还
    时机返回 B 的牌。既有的 ``heroes.skill_id_duplicate`` 只管**同一武将内**，管不到这里。
    """
    raw = _diy_raw(skill_id=PLACEMENT_SKILL, short_id="qianxun")
    raw["heroes"][0]["skills"][0]["placement"] = _placement()
    raw["heroes"].append(
        _second_diy_hero(short_id="qianxun_alias", placement=_placement(return_timing="never"))
    )

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["heroes.placement_conflict"]
    assert errors[0].loc == "/heroes/1/skills/0/placement"
    assert "myhero" in errors[0].expected and "qianxun" in errors[0].expected, (
        f"expected 要点名首次声明者；实得 {errors[0].expected!r}"
    )
    assert "myhero2" in errors[0].got and "qianxun_alias" in errors[0].got, (
        f"got 要点名这次的声明者；实得 {errors[0].got!r}"
    )
    assert "turn_end" in errors[0].expected and "never" in errors[0].got


def test_the_same_placement_declaration_on_two_heroes_is_allowed() -> None:
    """两份**一模一样**的声明不算冲突：跨武将复用同一条技能正是票 04 要开的门。"""
    raw = _diy_raw(skill_id=PLACEMENT_SKILL, short_id="qianxun")
    raw["heroes"][0]["skills"][0]["placement"] = _placement()
    raw["heroes"].append(_second_diy_hero(short_id="qianxun", placement=_placement()))

    assert _validate(raw).by_id("myhero2") is not None


def test_a_cross_table_placement_conflict_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**跨表**冲突也要红：单表校验看不见，全局声明表必须在建表时 fail-closed。

    既有表的界陆逊【谦逊】声明是 ``turn_end``；DIY 包把它改成 ``never`` —— 引擎的归还时机
    会因此取决于声明表的遍历顺序。这正是票 08 B 说的"静默后写覆盖先写"。
    """
    raw = _diy_raw(skill_id=PLACEMENT_SKILL, short_id="qianxun")
    raw["heroes"][0]["skills"][0]["placement"] = _placement(return_timing="never")
    _install(monkeypatch, raw)  # 既有表 + DIY 表：单表校验都通过

    with pytest.raises(DataLoadError) as excinfo:
        hero_placement_decls()

    errors = excinfo.value.errors
    assert [error.rule_id for error in errors] == ["heroes.placement_conflict"]
    assert "jie_luxun" in errors[0].expected and "myhero" in errors[0].got
    assert "turn_end" in errors[0].expected and "never" in errors[0].got


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


# ---------------------------------------------------------------------------
# ⑨ 票 07：展示层名字映射 / 化身候选面读 hero_card_skill_id
# ---------------------------------------------------------------------------


def test_a_diy_entries_option_details_carry_the_referenced_skill_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """天成技能行：``text`` 是「武圣」、``skill_id`` 是被引用的全限定 id，不是 DIY 行键。

    选项 **id** 本来就由 :func:`sgs.data.hero_card_skill_ids` 给出（正确）；坏掉的是
    ``option_details`` 自己按短 id 建的**名字表**——于是整行退回 id 当文本、描述丢失。
    """
    tables = _installed(monkeypatch, _diy_raw(short_id="my_wusheng"))
    query = SimpleNamespace(
        options=(REUSED_SKILL,), hero_card=SimpleNamespace(hero_id="myhero")
    )

    rows = option_details(query, heroes=tables)

    assert [row["text"] for row in rows] == ["武圣"]
    assert [row["skill_id"] for row in rows] == [REUSED_SKILL]
    assert rows[0]["description"] == skill_description(REUSED_SKILL)
    assert "my_wusheng" not in repr(rows), "DIY 行键不是技能 id，不许出现在展示行里"
    assert "hero.myhero.my_wusheng" not in repr(rows), "短 id 派生的幽灵 id 不许出现"


def _player_with_marker() -> PlayerPublic:
    """一个**只挂一条标记**的座位（``seat_display`` 的 ``players`` 也要它自己）。"""
    return PlayerPublic(
        seat=0,
        hp=4,
        hp_max=4,
        hand_count=0,
        alive=True,
        equip=(),
        judge=(),
        hero="myhero",
        markers=(
            Marker(
                name="测试标记",
                source_seat=0,
                source_skill_id=REUSED_SKILL,
                count=1,
            ),
        ),
    )


def test_a_marker_from_a_diy_hero_names_the_referenced_skill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """标记悬停的来源名：按**解析后**的全限定 id 反查「武圣」，不是末段短 id。"""
    tables = _installed(monkeypatch, _diy_raw(short_id="my_wusheng"))
    player = _player_with_marker()

    payload = seat_display(player, heroes=tables, players=(player,))

    assert [item["source_label"] for item in payload["markers"]] == [
        "座位 1 自制武将·武圣"
    ]


def test_a_diy_hero_has_avatar_candidates_that_exist_in_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """化身候选面：这张牌给出的每个 id 都必须是 ``CATALOG`` 里的真技能（不是短 id 派生的）。"""
    _install(monkeypatch, _diy_raw(short_id="my_wusheng"))

    rows = avatar_skill_rows("myhero", pack_id="standard")

    assert rows == ((REUSED_SKILL, True),)
    assert all(skill_id in CATALOG for skill_id, _ in rows), "化身候选必须是可以解析的技能 id"
    assert eligible_skill_ids("myhero", pack_id="standard") == (REUSED_SKILL,)


def test_the_avatar_face_names_the_referenced_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    """化身面（票 08 A）：**选项文本**与**外观行第二行**都给「武圣」，不是行键 / 末段 / 整串 id。

    旧 ``skill_name_on`` 拿 ``skill_id.rsplit(".", 1)[-1]``（= ``wusheng``）去撞
    ``HeroSkill.id``；这张牌的条目行键是 ``my_wusheng`` ⇒ 撞不到，于是选项写成
    「自制武将·wusheng」、外观行第二行也退回行键。下面四样都钉住"名字"而非"id"。
    """
    _install(monkeypatch, _diy_raw(short_id="my_wusheng"))
    state = build_state(pack=load_content_pack(), seats=[SeatSpec(hero="myhero")])
    state.player(0).avatar_cards.append("myhero")

    appearance = APPEARANCE_BUILDERS[AppearanceKind.AVATAR](state, 0)
    row = appearance["cards"][0]["skills"][0]
    assert row["skill_id"] == REUSED_SKILL
    assert row["name"] == "武圣", "外观行第二行必须是条目中文名（不是数据行键 / 末段 / 整串 id）"

    texts = zuoci_module._option_texts(state, (("myhero", REUSED_SKILL),))
    assert texts == {REUSED_SKILL: "自制武将·武圣"}, (
        "化身选项文本 = 武将名·技能名；旧口径会给「自制武将·wusheng」"
    )


def test_the_narrative_name_table_keys_the_referenced_skill_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """叙事名字表：显式引用按**解析后**的 id 建键，不留短 id 派生的幽灵键。"""
    _install(monkeypatch, _diy_raw(short_id="my_wusheng"))

    _, skill_names = _name_tables("standard")

    assert skill_names[REUSED_SKILL] == "武圣"
    assert "hero.myhero.my_wusheng" not in skill_names, (
        "显式引用不该在名字表里多出一个短 id 派生的幽灵键（名字表的两半必须同一口径）"
    )


def _short_id_derivation(hero: Any, skill: Any) -> str:
    """**旧口径**：``hero.<武将>.<短 id>``（票 04 前的派生；反向检查用）。"""
    return hero_skill_id(hero.id, skill.id)


def _restore_short_id_derivation(monkeypatch: pytest.MonkeyPatch) -> None:
    """把派生口临时换回短 id 重拼，并清掉名字表缓存。

    票 08 A 之后名字反查只剩**一个**实现（``sgs.data.hero_skill_name(s)``），故这里只需换
    数据层 loader 的那一处派生 + 化身候选面自己绑定的那一份；展示层 / 叙事不再各持一份。
    """
    for target in (
        "sgs.data.loader.hero_card_skill_id",
        "sgs.skills.zuoci.hero_card_skill_id",
    ):
        monkeypatch.setattr(target, _short_id_derivation)
    _name_tables.cache_clear()


def test_the_sweep_assertions_discriminate_the_old_short_id_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向①：派生口换回短 id ⇒ 三处观测回到**旧坏值**（证明上面的断言不空转）。

    同一条读取路径，只要派生口退回 ``hero.<武将>.<短 id>``，名字 / 说明 / 化身候选 / 叙事
    键就一起坏掉。它守的是**反例的判别力**：若夹具退回 ``short_id="wusheng"``（短 id 恰好
    等于被引用技能的末段），短 id 派生就不再出错，本测试随即变红，提醒"这张表不再是反例"。
    （修复本身被改回就地重拼时，红的是那三条真断言，不是这一条。）
    """
    tables = _installed(monkeypatch, _diy_raw(short_id="my_wusheng"))
    _restore_short_id_derivation(monkeypatch)

    query = SimpleNamespace(
        options=(REUSED_SKILL,), hero_card=SimpleNamespace(hero_id="myhero")
    )
    row = option_details(query, heroes=tables)[0]
    assert row["text"] == REUSED_SKILL, "旧口径：整串 id 当名字"
    assert row["description"] is None
    assert row["skill_id"] is None

    player = _player_with_marker()
    payload = seat_display(player, heroes=tables, players=(player,))
    assert [item["source_label"] for item in payload["markers"]] == [
        "座位 1 自制武将·wusheng"
    ], "旧口径：短 id 当名字"

    assert avatar_skill_rows("myhero", pack_id="standard") == (
        ("hero.myhero.my_wusheng", True),
    ), "旧口径：化身候选指向不存在的 id"
    assert "hero.myhero.my_wusheng" in _name_tables("standard")[1], (
        "旧口径：叙事名字表多一个幽灵键"
    )


def _old_skill_name_on(hero: Any, skill_id: str) -> str:
    """**旧口径**（票 08 A 前）：``skill_id`` 末段撞 ``HeroSkill.id``，撞不到退回末段。"""
    short_id = skill_id.rsplit(".", 1)[-1]
    return next((item.name for item in hero.skills if item.id == short_id), short_id)


def test_the_avatar_name_assertions_discriminate_the_old_short_id_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向②：名字读取口退回"末段撞短 id" ⇒ 化身面的两个观测点回到旧坏值。

    证明 `test_the_avatar_face_names_the_referenced_skill` 不空转：反例正是这张
    ``short_id="my_wusheng"`` 的牌（行键 ≠ 目标技能末段）。
    """
    _install(monkeypatch, _diy_raw(short_id="my_wusheng"))
    state = build_state(pack=load_content_pack(), seats=[SeatSpec(hero="myhero")])
    state.player(0).avatar_cards.append("myhero")
    monkeypatch.setattr("sgs.skills.zuoci.hero_skill_name", _old_skill_name_on)

    row = APPEARANCE_BUILDERS[AppearanceKind.AVATAR](state, 0)["cards"][0]["skills"][0]
    assert row["name"] == "wusheng", "旧口径：末段撞行键撞不到 ⇒ 短 id 当名字"
    assert zuoci_module._option_texts(state, (("myhero", REUSED_SKILL),)) == {
        REUSED_SKILL: "自制武将·wusheng"
    }, "旧口径：化身选项文本写成短 id"
