"""可见性表（ai-view 票 05；spec §2.4）：三值闭集 + 两道断言 + 脚手架。

缝（三条，全部是**声明面 / 真实产出路径**，不读引擎私有状态）：

1. **声明面**：``FLOWS`` 里每条流水线的每个叶子步骤，都要在
   :data:`~sgs.skills.catalog.STEP_VISIBILITY` 里有一行；反过来，表里的每个键都要在
   该技能的某条流水线里真实存在。表是 ``SkillDef.step_visibility`` 的唯一来源。
2. **键空间**：键 = **步骤名**，不是步序——界【离间】与标准【离间】的两次点选次序相反，
   三个键一字不改。
3. **追加要求（用户 2026-11-23 裁定）**：拆的区选择步是 ``PUBLIC``；南蛮 / 万箭的每个
   目标是独立效果 ⇒ 卡牌解析表按**目标**登记，一局 N 个目标 = N 条可归因事实
   （与票 04 的公开面缓冲对得上）。

``HIDDEN`` / ``CLAIM`` 的判据不是「这一步好不好看」，而是**答案值是否进入公开面**：
牌从手牌交到别人手里（牌面仍是隐藏区）、扣置、观星的摆放 ⇒ 不公开；【蛊惑】的声明牌名
只公开**声称值**。
"""

from __future__ import annotations

import sys
from pathlib import Path

from sgs.data import Gender, load_content_pack
from sgs.skills import CATALOG, StepVisibility
from sgs.skills.catalog import CARD_RESOLUTION_VISIBILITY, STEP_VISIBILITY
from sgs.skills.flow import Arrange, flow_steps
from sgs.skills.preactivation import FLOWS
from sgs.state import ActivateSkill, QueryKind, SelectCards, Zone
from sgs.timing import Timing

from support import SeatSpec, build_state, deck_excluding

# 真实局面的夹具与票 04 的公开面测试同源：同一批 `support` 构造器、同一条记录路径，
# 不在这里重造一份"只喂表面的合成局"（`CODING_STANDARDS.md` §6）。
from test_public_surface import (
    _Scripted,
    _barbarian_engine,
    _dismantle_engine,
    _moves_from,
    _run,
)

PACK = load_content_pack(deck_profile="standard_108")

LIJIAN = "hero.diaochan.lijian"
JIE_LIJIAN = "hero.jie_diaochan.lijian"
GUOHUO = "hero.yuji_mobile.guohuo"
GUANXING = "hero.zhugeliang.guanxing"

# 脚手架是只读工具、不是包：按目录导入（同 `test_skill_authoring_docs`）。
_TOOLS_DIR = str(Path(__file__).resolve().parents[1] / "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _scaffold():
    import dump_public_surface

    return dump_public_surface


# ---------------------------------------------------------------------------
# 1) 两道断言：漏一条 / 多一条，都判红
# ---------------------------------------------------------------------------


def test_every_flow_step_has_a_registered_visibility():
    """流程里出现的每个步骤键都必须登记（漏一条即红，不静默落默认）。"""
    scaffold = _scaffold()
    assert scaffold.missing_steps(scaffold.skill_step_keys(), STEP_VISIBILITY) == ()


def test_every_registered_visibility_is_a_flow_step():
    """表里的每个键都必须在**该技能**的某条流水线里真实存在（多一条即红）。"""
    scaffold = _scaffold()
    assert scaffold.ghost_steps(scaffold.skill_step_keys(), STEP_VISIBILITY) == ()


def test_a_missing_registration_is_rejected():
    """故意漏一条 ⇒ 判红（合成的最小流水线，验的是断言本身）。"""
    scaffold = _scaffold()
    problems = scaffold.missing_steps(
        {"hero.synthetic": {"card", "seat"}},
        {"hero.synthetic": {"card": StepVisibility.PUBLIC}},
    )
    assert problems == ("hero.synthetic: 步骤 seat 未登记可见性",), problems


def test_a_ghost_registration_is_rejected():
    """故意多一条 ⇒ 判红（表里的键在流水线里不存在）。"""
    scaffold = _scaffold()
    problems = scaffold.ghost_steps(
        {"hero.synthetic": {"card"}},
        {"hero.synthetic": {"card": StepVisibility.PUBLIC, "ghost": StepVisibility.HIDDEN}},
    )
    assert problems == ("hero.synthetic: 登记的步骤 ghost 不在派生集合里",), problems


def test_the_registry_reaches_the_definitions():
    """表的唯一读取方是 ``SkillDef.step_visibility``：有步骤的拿同一行，没步骤的是 ``None``。"""
    keys = _scaffold().skill_step_keys()
    assert set(keys) == set(STEP_VISIBILITY), "键集必须与有步骤的技能一致"
    for skill_id, expected in STEP_VISIBILITY.items():
        assert CATALOG[skill_id].step_visibility == expected, skill_id
    for skill_id in FLOWS:
        if skill_id not in keys:
            assert CATALOG[skill_id].step_visibility is None, (
                f"{skill_id} 的流水线没有叶子步骤，不应有可见性行"
            )


def test_the_three_values_are_a_closed_set():
    """值 = 闭集三值；表里出现别的类型即红。"""
    assert {member.value for member in StepVisibility} == {"PUBLIC", "HIDDEN", "CLAIM"}
    for skill_id, row in STEP_VISIBILITY.items():
        for step, value in row.items():
            assert isinstance(value, StepVisibility), (skill_id, step, value)


# ---------------------------------------------------------------------------
# 2) 键 = 步骤名（不是步序）
# ---------------------------------------------------------------------------


def test_lijian_keeps_its_step_keys_when_the_pick_order_flips():
    """界【离间】与标准【离间】的选择次序相反，**步骤键一字不改**（票面 §键必须是步骤名）。"""
    standard = STEP_VISIBILITY[LIJIAN]
    jie = STEP_VISIBILITY[JIE_LIJIAN]
    assert set(standard) == {"card", "user", "victim"}
    assert set(jie) == set(standard), "两版的键集必须相同（次序不同，键不改名）"
    assert standard == jie, "两版的可见性登记也必须相同"


def test_lijian_user_and_victim_are_independent_answers():
    """``user`` / ``victim`` 是两行 ⇒ 两个答案值可以不同（目标不等价），不被同一个键吃掉。"""
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=deck_excluding(PACK, card),
        turn_seat=0,
    )
    engine = _run(
        state,
        [
            _Scripted(_lijian_driver(pick=card, choices=("seat:1", "seat:2"))),
            _Scripted(),
            _Scripted(),
        ],
        lambda e: _settled(e, Timing.RESOLVE_AFTER_END),
    )

    answers = [
        fact
        for fact in engine.state.public_surface.recent
        if isinstance(fact.payload, ActivateSkill) and fact.payload.skill_id == LIJIAN
    ]
    assert answers, "离间的复合动作必须有答案事实"
    choices = answers[0].payload.choices
    assert len(set(choices)) == 2, f"user / victim 是两个不同的座位：{choices}"
    assert STEP_VISIBILITY[LIJIAN] == {
        "card": StepVisibility.PUBLIC,
        "user": StepVisibility.PUBLIC,
        "victim": StepVisibility.PUBLIC,
    }, "两个座位答案各有一行（删掉其中一行这条用例会红）"


# ---------------------------------------------------------------------------
# 3) 三值的边界
# ---------------------------------------------------------------------------


def test_guohuo_declares_a_claim_and_conceals_the_card():
    """【蛊惑】的声明步 = ``CLAIM``；扣置那一步 = ``HIDDEN``（真值不进视图）。"""
    row = STEP_VISIBILITY[GUOHUO]
    assert row == {
        "card": StepVisibility.HIDDEN,
        "name": StepVisibility.CLAIM,
    }, row


def test_a_secret_prediction_target_is_hidden():
    """界【司敌】：谁被标记是公开的（``first``），**预测他会打谁**只有曹真知道（``target``）。"""
    row = STEP_VISIBILITY["hero.jie_caozhen_mobile.sidi"]
    assert row["first"] is StepVisibility.PUBLIC
    assert row["target"] is StepVisibility.HIDDEN


def test_an_arrangement_is_hidden():
    """观星的摆放**只给排列者看**：所有 ``Arrange`` 步都登记为 ``HIDDEN``。"""
    arranged: list[tuple[str, str]] = []
    for skill_id, row in STEP_VISIBILITY.items():
        for _timing, flow in FLOWS[skill_id]:
            for step in flow_steps(flow):
                if isinstance(step, Arrange):
                    arranged.append((skill_id, step.key))
                    assert row[step.key] is StepVisibility.HIDDEN, (skill_id, step.key)
    assert arranged, "至少要枚举到观星那一类排列步（否则这条断言是空转）"


# ---------------------------------------------------------------------------
# 4) 追加要求：拆的区选择步 + 多目标牌的逐目标效果
# ---------------------------------------------------------------------------


def test_the_dismantlement_zone_pick_is_public():
    """拆「拿走哪一张 / 哪个区」那一步 = ``PUBLIC``；一局**真实拆判定区**留下公开答案。"""
    assert CARD_RESOLUTION_VISIBILITY["card:dismantlement"] == {
        "pick": StepVisibility.PUBLIC
    }
    engine = _dismantle_engine(judge=True)
    answers = [
        fact
        for fact in engine.state.public_surface.recent
        if isinstance(fact.payload, SelectCards)
    ]
    assert answers, "拆的选牌答案必须进公开面"
    assert answers[0].window == "SELECT_CARDS", "答案事实带着它所属的询问种类"
    moves = _moves_from(engine, Zone.JUDGE)
    assert moves and moves[0].payload.from_zone.seat == 1, (
        "拆的是判定区：区坐标里带着主人（票 04 的事实面）"
    )


def test_a_multi_target_card_registers_one_effect_per_target():
    """南蛮 / 万箭的每个目标是一条效果 ⇒ 卡牌解析表按 ``target`` 登记，一局 N 个目标 N 条事实。"""
    for card_type in ("barbarian_invasion", "arrow_barrage"):
        assert CARD_RESOLUTION_VISIBILITY[f"card:{card_type}"] == {
            "target": StepVisibility.PUBLIC
        }, card_type

    engine = _barbarian_engine()
    damage = [
        fact
        for fact in engine.state.public_surface.recent
        if getattr(fact.payload, "delta_type", None) == "hp_change"
        and fact.payload.after < fact.payload.before
    ]
    assert [fact.targets for fact in damage] == [(1,), (2,)], (
        "同一张【南蛮入侵】对两个目标各一条伤害事实（不是一条聚合的 AoE）"
    )
    assert [fact.actor for fact in damage] == [0, 0], "每条事实都归因到这一发的使用者"


# ---------------------------------------------------------------------------
# 5) 脚手架
# ---------------------------------------------------------------------------


def test_the_scaffold_lists_every_row_without_semantics():
    """脚手架列出全部行，且**不输出**语义字段（``meaning`` / ``hint`` / 提示文案）。"""
    text = _scaffold().render()
    for skill_id in STEP_VISIBILITY:
        assert skill_id in text, f"脚手架漏了一行：{skill_id}"
    for column in ("step", "kind", "default", "registered"):
        assert column in text
    for forbidden in ("meaning", "hint", "wording", "提示", "语义"):
        assert forbidden not in text, f"脚手架输出了语义字段：{forbidden}"


def test_the_scaffold_shows_the_kind_and_default_for_each_step():
    """每一行带 ``kind`` 与**默认值**：供人确认默认 / 改例外（不是让人从零枚举）。"""
    rows = _scaffold().skill_rows()
    by_key = {(row.owner, row.step): row for row in rows}
    assert by_key[(LIJIAN, "card")].kind == "PickCards"
    assert by_key[(LIJIAN, "user")].kind == "PickSeats"
    assert by_key[(GUANXING, "cards")].kind == "Arrange"
    assert by_key[(GUOHUO, "name")].default is StepVisibility.PUBLIC
    assert by_key[(GUOHUO, "name")].registered is StepVisibility.CLAIM
    assert by_key[(GUANXING, "cards")].default is StepVisibility.HIDDEN
    assert by_key[(GUANXING, "cards")].registered is StepVisibility.HIDDEN


def test_the_card_table_covers_every_derived_card_step():
    """卡牌侧也走同一份两道断言：派生集合与登记表双向相等（漏 / 多都判红）。"""
    scaffold = _scaffold()
    derived = {
        owner: set(steps) for owner, steps in scaffold.card_step_keys().items()
    }
    assert scaffold.missing_steps(derived, CARD_RESOLUTION_VISIBILITY) == ()
    assert scaffold.ghost_steps(derived, CARD_RESOLUTION_VISIBILITY) == ()


def test_the_check_entry_point_sees_no_problems():
    """``--check`` 的那一条路径（漏 / 多 + 卡牌侧 + kind 冲突）全绿。"""
    assert _scaffold().problems() == ()


def test_a_conflicting_step_kind_is_reported():
    """同一技能的两条流水线用了同一个步骤键、但类型不同 ⇒ 判红。"""
    from sgs.skills.flow import PickOption, PickSeats, SkillFlow

    def flow(name, step):
        return SkillFlow(name=name, steps=(step,), effect=lambda *_: None)

    flows = {
        "hero.synthetic": (
            (None, flow("a", PickSeats(lo=1, hi=1, pool=lambda *_: (), key="x"))),
            (None, flow("b", PickOption(options=("one",), key="x"))),
        )
    }
    conflicts = _scaffold().kind_conflicts(flows)
    assert conflicts and "类型不同" in conflicts[0], conflicts


def test_the_scaffold_expands_a_multi_target_card_to_one_row_per_seat():
    """``--players N``：多目标牌的 ``target`` 展开成 N 行，主公那一格是其中一行。"""
    scaffold = _scaffold()
    rows = scaffold.card_rows(seats=5)
    barbarian = [row for row in rows if row.owner == "card:barbarian_invasion"]
    assert [row.step for row in barbarian] == [f"target[{seat}]" for seat in range(5)]
    assert "target[0]" in {row.step for row in barbarian}, "主公（座位 0）是一格目标"
    assert all(row.registered is StepVisibility.PUBLIC for row in barbarian)
    assert "target" not in {row.step for row in rows}, "展开后不再有聚合的 target 行"


def test_the_visibility_keys_are_flow_step_names_not_wording_keys():
    """键 = 步骤名，**不是**投影文案键：文案对 `SELECT_CARDS` 合并成 `card`。"""
    definition = CATALOG["hero.feng_yujin.jieyue"]
    row = STEP_VISIBILITY["hero.feng_yujin.jieyue"]
    assert set(row) == {"seat", "give"}, "可见性表按步骤名逐步一行"
    wording = definition.step_requirements or {}
    assert "give" not in wording, "文案表不认这个步骤名（它把两次选牌合并了）"
    assert {"card", "choice"} <= set(wording)


def test_hand_written_skill_steps_are_outside_this_table():
    """本表只收 ``SkillFlow`` 叶子步骤；手写段机的答案不在这里（票面两道断言的边界）。"""
    definition = CATALOG["hero.shen_guojia.zuoxing"]
    assert {"name", "target", "designation"} <= set(definition.step_requirements or {})
    assert definition.step_visibility is None, "零叶子流水线的技能没有可见性行"
    assert {"target", "designation"} & set(STEP_VISIBILITY[GUOHUO]) == set()


def test_the_scaffold_exposes_the_card_effects_too():
    """卡牌解析的逐目标效果也在脚手架里（追加要求 2），且与行为表同源。"""
    scaffold = _scaffold()
    rows = scaffold.card_rows()
    by_key = {(row.owner, row.step): row for row in rows}
    assert by_key[("card:dismantlement", "pick")].kind == "PickCards"
    assert by_key[("card:barbarian_invasion", "target")].kind == scaffold.CARD_PER_TARGET_KIND
    assert by_key[("card:arrow_barrage", "target")].kind == scaffold.CARD_PER_TARGET_KIND
    assert by_key[("card:barbarian_invasion", "target")].default is StepVisibility.PUBLIC
    assert all(row.registered is not None for row in rows), "派生出来的卡牌步骤必须全部登记"


# ---------------------------------------------------------------------------
# 真实局面的驱动（夹具本身来自票 04 的 `test_public_surface`）
# ---------------------------------------------------------------------------


def _cards(type_id: str) -> list[int]:
    return [
        index for index, card in enumerate(PACK.deck.cards) if card.type == type_id
    ]


def _settled(engine, timing: Timing) -> bool:
    return any(
        record.kind == "timing" and record.timing == timing for record in engine.records
    )


def _end_phase_or_none(query):
    if query.kind == QueryKind.PLAY_PHASE:
        return next(
            (action for action in query.legal_actions if action.kind == "EndPhase"), None
        )
    return None


def _lijian_driver(*, pick: int, choices: tuple[str, str]):
    def driver(query):
        if query.kind != QueryKind.PLAY_PHASE:
            return None
        if LIJIAN not in query.preactivations:
            return _end_phase_or_none(query)
        return ActivateSkill(
            skill_id=LIJIAN,
            picks=(pick,),
            choices=choices,
            answers_seq=query.seq,
        )

    return driver
