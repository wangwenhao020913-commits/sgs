"""H 族：转化牌泛化（票 03）。

落点与票 03 §验收一致，全部走**外部缝**：``build_state`` 构造局面 + ``legal_actions_for``
的候选集合 + 构造器的牌面事实。不测回调的私有分支。

三形态的牌面事实（ADR-003 / ADR-004；`03` §5「效果原语（Effect Primitives）清单」的四列表）：

- **单源转化**（武圣 / 龙胆 / 国色 / 急救）：继承原牌花色 / 点数 / 颜色；
- **多源转化**（丈八蛇矛）：花色恒无、点数恒无、颜色按同色派生；
- **纯虚拟牌**（八卦阵的视为【闪】）：三者皆无。

**属性一律不继承**：转化牌由效果决定属性，未声明即普通伤害——落在两条可断言的事实上：
牌面模型里**根本没有属性字段**（故"武圣转化的【杀】是普通【杀】"是结构事实），
且黑【杀】仍被【仁王盾】挡、红牌转化的【杀】不被挡（颜色照继承）。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sgs.data import Color, Identity, RuleConfig, color_of_suit
from sgs.effects import (
    ModifierContext,
    ModifierPoint,
    apply_modifiers,
    legal_actions_for,
    locate,
    target_is_effective,
)
from sgs.engine import Engine, IllegalActionError
from sgs.skills import (
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    VirtualSourceError,
    preactivation_for,
    transformed_card,
    transformed_slash,
    virtual_card_id,
    virtual_dodge,
)
from sgs.skills.virtual import LEGACY_KIND_COUNT
from sgs.state import (
    ActivateSkill,
    FrameKind,
    PlayCard,
    PlayPhaseQuery,
    QueryKind,
    RespondContext,
    RespondMode,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_until

from test_skills import (
    GREEN_DRAGON,
    PACK,
    SERPENT,
    SLASH,
    Scripted,
    _cards,
    _deck_excluding,
    _find,
    _first_by_color,
    _run,
    _slash_use,
    _use_frame_popped,
)

# 构造局面的四个武将（票 03 §表）：技能 id 走 ``hero.<武将>.<短 id>`` 的既有派生。
GUANYU = "hero.guanyu.wusheng"
ZHAOYUN_SLASH = "hero.zhaoyun.longdan"
DAQIAO = "hero.daqiao.guose"
HUATUO = "hero.huatuo.jijiu"

RED_PEACH = _first_by_color("peach", black=False)
RED_DODGE = [cid for cid in _cards("dodge") if color_of_suit(PACK.deck.cards[cid].suit) is Color.RED][0]
BLACK_SLASH = _first_by_color("slash", black=True)
RED_SLASH = _first_by_color("slash", black=False)
DIAMOND = [cid for cid in range(len(PACK.deck.cards)) if PACK.deck.cards[cid].suit == "diamond"][0]
RENWANG = _cards("renwang_shield")[0]
ARROW_BARRAGE = _cards("arrow_barrage")[0]


def _virtual(actions, type_id: str) -> list[Any]:
    """候选里该牌名的**转化牌**行动（使用与打出两种构造子都算）。"""
    return [
        action
        for action in actions
        if isinstance(action, (UseCard, PlayCard))
        and action.card.card_id < 0
        and action.card.type == type_id
    ]


def _virtual_uses(actions, type_id: str) -> list[UseCard]:
    """候选里该牌名的转化牌**使用**行动（目标是座位的那一类）。"""
    return [action for action in _virtual(actions, type_id) if isinstance(action, UseCard)]


def _sources(actions, type_id: str) -> set[tuple[int, ...]]:
    return {action.card.virtual_sources for action in _virtual(actions, type_id)}


def _respond(required: str, *, mode: RespondMode = RespondMode.PLAY, targets: tuple[int, ...] = ()):
    """一个响应窗口的载荷（票 19 §4 的类型化 ``payload``）。"""
    return RespondContext(
        cause=WindowCause.DUEL,
        required=required,
        mode=mode,
        use_targets=targets,
    )


# ---------------------------------------------------------------------------
# id 派生：单源 / 多源 / 纯虚拟三类不撞（验收 1 / 4；票 03 §要做什么 1）
# ---------------------------------------------------------------------------


def test_virtual_card_ids_are_injective_across_single_multi_and_pure_virtual_forms():
    """单源 / 多源 / 纯虚拟三类**逐对不等**——步长不再假设"最多两张来源"。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    count = len(state.cards)

    single = transformed_card(state, (RED_PEACH,), "slash").card_id
    multi = transformed_card(state, (RED_PEACH, RED_DODGE), "slash").card_id
    triples = transformed_card(state, (RED_PEACH, RED_DODGE, BLACK_SLASH), "slash").card_id
    pure = virtual_dodge(state).card_id
    other_no_sources = virtual_card_id(kind=int(VirtualKind.PEACH), sources=(), card_count=count)

    assert len({single, multi, triples, pure, other_no_sources}) == 5, (
        "单源 / 双源 / 三源 / 纯虚拟 / 另一命名空间的无来源牌，五个 id 必须两两不同"
    )
    assert all(card_id < 0 for card_id in (single, multi, triples, pure, other_no_sources))


def test_the_same_virtual_card_and_sources_are_deterministic_regardless_of_source_order():
    """同一目标牌名 + 同一组来源 ⇒ 同一个 id（来源集合的枚举序不影响）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    forward = transformed_card(state, (RED_PEACH, RED_DODGE), "slash")
    backward = transformed_card(state, (RED_DODGE, RED_PEACH), "slash")

    assert forward.card_id == backward.card_id
    assert forward.virtual_sources == backward.virtual_sources == tuple(
        sorted((RED_PEACH, RED_DODGE))
    )


def test_different_source_sets_get_different_ids_for_the_same_target_name():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    assert (
        transformed_card(state, (RED_PEACH,), "slash").card_id
        != transformed_card(state, (RED_DODGE,), "slash").card_id
    )


def test_the_id_derivation_is_injective_over_every_kind_and_source_count():
    """步长不再假设"最多两张来源"的**穷举判据**（票 03 §要做什么 1 / §验收 4）。

    对 ``kind`` 的全部取值 × ``0..MAX_VIRTUAL_SOURCES`` 张来源 × 若干 ``card_count``
    逐个算 id，断言**两两不同**——旧的 ``(card_count+1)**2`` 步长在第 3 张来源上就会撞。
    来源 id 取 ``1..size``（合法牌 id 的**非零**前缀）；``card_count`` 因此都取
    ``>= MAX_VIRTUAL_SOURCES + 1``，否则 ``1..size`` 会越过 ``base`` 这一位、不再是
    合法来源。兼容段（``kind < 2``）逐字保留旧算式，故它的 id **只由来源集合决定、
    与牌堆张数无关**（``card_count`` 只是那里的进制）：
    跨牌堆的撞因此只查**泛化段**（``kind >= 2``）——本票新引入的那一半。
    """
    count = len(PACK.deck.cards)
    global_seen: set[int] = set()
    for card_count in (MAX_VIRTUAL_SOURCES + 1, 53, count):
        seen: set[int] = set()
        for kind in range(len(VirtualKind)):
            for size in range(MAX_VIRTUAL_SOURCES + 1):
                sources = tuple(range(1, size + 1))
                card_id = virtual_card_id(kind=kind, sources=sources, card_count=card_count)
                assert card_id < 0
                assert card_id not in seen, (
                    f"同一牌堆内撞 id：kind={kind} sources={sources} card_count={card_count}"
                )
                seen.add(card_id)
                if kind < LEGACY_KIND_COUNT:
                    continue
                assert card_id not in global_seen, f"跨牌堆撞 id：{card_id}"
                global_seen.add(card_id)


def test_the_legacy_namespace_ids_are_byte_identical_to_the_frozen_replays():
    """兼容段（``kind < 2``）的 id 与旧算式逐字节相同——34 条基准磁带一行不改。

    ``-11882`` 是【八卦阵】"视为【闪】"在 108 张牌堆下的既有 id（M2 磁带里记着它）；
    这条断言钉住"票 03 的泛化没有挪动既有数字"。
    """
    assert virtual_card_id(kind=0, sources=(0, 1), card_count=108) == -2
    assert virtual_card_id(kind=1, sources=(), card_count=108) == -11882


def test_the_generalized_namespace_does_not_overlap_the_legacy_one():
    """``kind >= 2`` 的区间与兼容段不重叠（两段之间是可证明的界，不是巧合）。"""
    count = len(PACK.deck.cards)
    legacy_max = max(
        virtual_card_id(kind=0, sources=tuple(range(size)), card_count=count)
        for size in range(MAX_VIRTUAL_SOURCES + 1)
    )
    generalized_min = min(
        virtual_card_id(kind=kind, sources=tuple(range(size)), card_count=count)
        for kind in range(LEGACY_KIND_COUNT, len(VirtualKind))
        for size in range(MAX_VIRTUAL_SOURCES + 1)
    )

    assert generalized_min < legacy_max, "两段都在负数区，泛化段必须更负（区间不相交）"


# ---------------------------------------------------------------------------
# 共享构造器：单源继承 / 多源恒无 / 属性不继承（验收 1 / 2 / 3）
# ---------------------------------------------------------------------------


def test_a_single_source_transformation_inherits_suit_rank_and_color():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    origin = state.card(RED_PEACH)

    card = transformed_card(state, (RED_PEACH,), "slash")

    assert card.is_virtual and card.card_id < 0
    assert card.type == "slash" and card.name == "杀"
    assert card.suit == origin.suit, "单源转化继承原牌花色"
    assert card.rank == origin.rank, "单源转化继承原牌点数"
    assert card.color is Color.RED, "单源转化继承原牌颜色"
    assert card.virtual_sources == (RED_PEACH,)


def test_a_single_source_transformation_of_a_black_card_is_black():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    card = transformed_card(state, (BLACK_SLASH,), "slash")

    assert card.color is Color.BLACK
    assert card.suit == state.card(BLACK_SLASH).suit


def test_a_multi_source_transformation_has_no_suit_or_rank_and_derives_color_by_uniformity():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    same_color = transformed_card(state, (RED_PEACH, RED_DODGE), "slash")
    mixed = transformed_card(state, (RED_PEACH, BLACK_SLASH), "slash")

    assert same_color.suit is None and same_color.rank is None
    assert same_color.color is Color.RED, "同色 ⇒ 有该颜色"
    assert mixed.suit is None and mixed.rank is None and mixed.color is None, "异色 ⇒ 无色"


def test_transformed_card_ids_do_not_collide_across_target_card_names():
    """每个目标牌名一段 kind 命名空间：同一来源的【杀】与【闪】不撞 id。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    slash = transformed_card(state, (RED_SLASH,), "slash")
    dodge = transformed_card(state, (RED_SLASH,), "dodge")

    assert slash.card_id != dodge.card_id
    assert dodge.name == "闪" and dodge.suit == state.card(RED_SLASH).suit


def test_the_constructor_rejects_an_empty_source_set():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    with pytest.raises(VirtualSourceError):
        transformed_card(state, (), "slash")


def test_the_constructor_rejects_more_sources_than_the_generalized_upper_bound():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    too_many = tuple(cid for cid in range(len(PACK.deck.cards)) if cid != DIAMOND)
    too_many = too_many[: MAX_VIRTUAL_SOURCES + 1]

    with pytest.raises(VirtualSourceError):
        transformed_card(state, too_many, "slash")


def test_the_shared_constructor_keeps_the_transformed_slash_wrapper_intact():
    """薄包装：丈八蛇矛的【杀】与新构造器逐字等价（既有调用点与测试不破）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    slash = transformed_slash(state, (RED_PEACH, RED_DODGE))
    generic = transformed_card(state, (RED_PEACH, RED_DODGE), "slash")

    assert slash.card_id == generic.card_id
    assert slash.model_dump() == generic.model_dump()
    assert virtual_dodge(state).is_virtual and not virtual_dodge(state).virtual_sources


# ---------------------------------------------------------------------------
# 武圣（关羽）：红色牌 →【杀】（PLAY + RESPOND；验收 1 / 3 / 4）
# ---------------------------------------------------------------------------


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def test_wusheng_offers_a_red_card_as_a_slash_inheriting_suit_rank_and_color():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH, BLACK_SLASH), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH, BLACK_SLASH),
        turn_seat=0,
    )
    origin = state.card(RED_PEACH)

    virtual = _virtual_uses(_play_candidates(state), "slash")

    assert _sources(_play_candidates(state), "slash") == {(RED_PEACH,)}, (
        "红牌进候选；黑牌不进候选（源筛选在候选生成期就闭掉）"
    )
    assert virtual and virtual[0].card.suit == origin.suit
    assert virtual[0].card.rank == origin.rank
    assert virtual[0].card.color is Color.RED
    assert all(action.card.virtual_sources != (BLACK_SLASH,) for action in virtual)


def test_wusheng_source_zone_default_is_hand_plus_equip_and_never_the_judge_zone():
    """区域默认（W4-3 / `GLOSSARY.md`「区域默认」）：牌面没写区域 ⇒ 手牌 ∪ 装备。

    判定区**不默认包含**——武圣的牌面只写"一张红色牌"，故判定区的红牌不是它的源
    （旧声明是"任意区域"＝手牌∪装备∪判定，本票按区域默认收窄）。
    """
    hand_state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )
    equip_state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(RED_DODGE,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    judge_state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(RED_DODGE,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )

    assert _sources(_play_candidates(hand_state), "slash") == {(RED_PEACH,)}
    assert _sources(_play_candidates(equip_state), "slash") == {(RED_DODGE,)}
    assert not _virtual(_play_candidates(judge_state), "slash"), (
        "判定区的红牌不是【武圣】的源：区域默认不包含判定区"
    )


def test_wusheng_needs_at_least_one_source_but_may_use_several_red_cards():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH, RED_DODGE), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH, RED_DODGE),
        turn_seat=0,
    )

    groups = _sources(_play_candidates(state), "slash")

    assert groups == {(RED_PEACH,), (RED_DODGE,)}, "一次转化一张（单源）"


def test_wusheng_respects_the_slash_count_limit_like_a_real_slash():
    """验收 5：使用次数按【杀】的目标牌名计入同一条 ``COUNT_LIMIT`` 链。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 1

    assert not _virtual(_play_candidates(state), "slash"), (
        "本回合【杀】额度已用完 ⇒ 武圣的转化【杀】与真【杀】一样不进候选"
    )


def test_wusheng_converted_slash_is_an_ordinary_slash_for_the_renwang_shield():
    """验收 3：属性一律不继承 ⇒ 武圣的红牌转化【杀】**不是**黑【杀】，不被仁王盾挡。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec(equip=(RENWANG,))],
        deck=_deck_excluding(RED_PEACH, RENWANG),
        turn_seat=0,
    )

    red_slash = transformed_card(state, (RED_PEACH,), "slash")
    black_slash = transformed_card(state, (BLACK_SLASH,), "slash")

    assert target_is_effective(state, red_slash, 0, 1) is True, "红牌转化 ⇒ 普通【杀】"
    assert target_is_effective(state, black_slash, 0, 1) is False, (
        "黑牌转化仍继承颜色 ⇒ 仁王盾照挡；属性不继承与颜色继承是两条互不干扰的裁定"
    )


def test_a_converted_slash_carries_no_attribute_because_the_card_face_has_no_such_field():
    """"普通【杀】"是**结构事实**：牌面模型里没有属性字段，故属性不可能被继承。

    规则 16.3 的"属性不继承、由效果决定"因此不靠某个构造器自觉，而是"没地方放属性"。
    """
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    fields = set(type(state.card(RED_PEACH)).model_fields)

    assert not {name for name in fields if "attribute" in name or "element" in name}
    assert "type" in fields and "suit" in fields and "color" in fields


def test_wusheng_offers_the_red_card_when_a_slash_is_required_in_response():
    """武圣的打出路径：响应窗口要【杀】时红牌进候选（`PLAY + RESPOND`）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.RESPOND, answers_seq=1, payload=_respond("slash"))

    playable = [
        action
        for action in actions
        if isinstance(action, PlayCard)
        and action.card.card_id < 0
        and action.card.type == "slash"
    ]
    assert [action.card.virtual_sources for action in playable] == [(RED_PEACH,)]


def test_wusheng_is_not_offered_without_a_red_card_in_hand():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(BLACK_SLASH),
        turn_seat=0,
    )

    assert not _virtual(_play_candidates(state), "slash")


# ---------------------------------------------------------------------------
# 整局链上的两条：使用次数按目标牌名计入（验收 5）/ 【闪】窗口进候选
# ---------------------------------------------------------------------------


def _play_transformed(skill_id: str):
    """在 ``PLAY_PHASE`` 按**清单**确认该转化技（W4-fix-2：候选已从 `legal_actions` 收回）。

    来源取来源步的第一张可用牌、目标取目标池的第一项——与界面的"点技能 → 选牌 → 选目标
    → 确认"同形，交出去的是 `ActivateSkill(sources, targets)`。
    """

    def chooser(query):
        if query.kind != QueryKind.PLAY_PHASE:
            return None
        steps = query.transform_preactivations.get(skill_id)
        if not steps:
            return None
        source = int(steps[0].pool[0].options[0])
        choices: tuple[str, ...] = ()
        if len(steps) > 1:
            seat = steps[1].pool[0].options[0].removeprefix("seat:")
            choices = (f"seat:{seat}",)
        return ActivateSkill(
            skill_id=skill_id, picks=(source,), choices=choices, answers_seq=query.seq
        )

    return Scripted(chooser)


def test_using_a_longdan_converted_slash_counts_against_the_slash_use_limit():
    """验收 5：龙胆的"【闪】当【杀】"按**目标牌名**【杀】计入 ``COUNT_LIMIT`` 链。

    与真【杀】同一条链 ⇒ 本回合已用过一张【杀】后，转化【杀】一样进不了候选；
    真的用出去一次后，计数落在 ``card_use_counts["slash"]`` 上（不是 ``"dodge"``）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 1

    assert not _virtual(_play_candidates(state), "slash"), "额度已满 ⇒ 转化【杀】不进候选"
    state.player(0).card_use_counts["slash"] = 0

    engine = _run(
        state,
        [_play_transformed(ZHAOYUN_SLASH), Scripted(lambda q: None)],
        _use_frame_popped,
    )

    assert engine.state.player(0).card_use_counts.get("slash") == 1
    assert "dodge" not in engine.state.player(0).card_use_counts


def test_a_real_slash_opens_a_dodge_window_where_longdan_offers_the_hand_slash():
    """真【杀】打过来时，赵云手里的【杀】在 ``DODGE`` 窗口里作为转化【闪】进候选。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_SLASH,)), SeatSpec(hand=(RED_DODGE,), hero="zhaoyun")],
        deck=_deck_excluding(RED_SLASH, RED_DODGE),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, RED_SLASH, 1)
        return None

    def zhaoyun(query):
        if query.kind == QueryKind.DODGE:
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id < 0
                and action.card.type == "dodge",
            )
        return None

    engine = _run(state, [Scripted(attacker), Scripted(zhaoyun)], _use_frame_popped)

    assert any(
        record.kind == "query" and record.query.kind == QueryKind.DODGE.value
        for record in engine.records
    ), "真【杀】照常开【闪】窗口"
    assert RED_DODGE not in engine.state.player(1).hand, (
        "转化【闪】的来源【闪】走正常的使用路径离手"
    )


# ---------------------------------------------------------------------------
# 龙胆（赵云）：【杀】↔【闪】双向（验收 5）
# ---------------------------------------------------------------------------


def test_longdan_offers_a_hand_dodge_as_a_slash_in_the_play_phase():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )

    virtual = _virtual(_play_candidates(state), "slash")

    assert [action.card.virtual_sources for action in virtual] == [(RED_DODGE,)]
    assert virtual[0].card.suit == state.card(RED_DODGE).suit


def test_longdan_offers_a_hand_slash_as_a_dodge_when_a_dodge_is_needed():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_SLASH,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_SLASH),
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=1)

    virtual = _virtual(actions, "dodge")
    assert [action.card.virtual_sources for action in virtual] == [(RED_SLASH,)]
    assert virtual[0].card.color is Color.RED, "单源转化继承原牌颜色"


def test_longdan_dodge_source_is_the_slash_card_name_not_a_red_card():
    """源筛选谓词是**牌名**（【杀】），不是颜色：红【杀】与黑【杀】都行。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH, RED_PEACH), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(BLACK_SLASH, RED_PEACH),
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=1)

    assert _sources(actions, "dodge") == {(BLACK_SLASH,)}, "红【桃】不能当【闪】"


def test_longdan_source_zone_default_is_hand_plus_equip_in_both_directions():
    """区域默认：龙胆两个方向的源都扩到**手牌 ∪ 装备**（纯统一，无实际判定变化）。

    牌面写的是"一张【杀】/【闪】"，故判定区的牌仍不是源。
    """
    slash_equip = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(RED_SLASH,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_SLASH),
        turn_seat=0,
    )
    dodge_equip = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )

    assert _sources(legal_actions_for(slash_equip, 0, QueryKind.DODGE, answers_seq=1), "dodge") == {
        (RED_SLASH,)
    }, "装备区的【杀】可当【闪】打出"
    assert _sources(_play_candidates(dodge_equip), "slash") == {(RED_DODGE,)}, (
        "装备区的【闪】可当【杀】使用"
    )

    judge_state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    assert not _virtual(_play_candidates(judge_state), "slash"), "判定区的牌不是源"


def test_longdan_offers_the_hand_slash_in_the_play_out_response_window():
    """``RESPOND``（万箭 / 南蛮的"打出"窗口）也要走：龙胆的两个方向都覆盖两条路。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_SLASH,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_SLASH),
        turn_seat=0,
    )

    actions = legal_actions_for(
        state,
        0,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=_respond("dodge"),
    )

    virtual = _virtual(actions, "dodge")
    assert [action.card.virtual_sources for action in virtual] == [(RED_SLASH,)]


# ---------------------------------------------------------------------------
# 国色（大乔）：方块牌 →【乐不思蜀】（验收：源筛选闭集里的**花色**）
# ---------------------------------------------------------------------------


def test_guose_offers_a_diamond_card_as_an_indulgence():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(DIAMOND),
        turn_seat=0,
    )

    virtual = _virtual(_play_candidates(state), "indulgence")

    assert [action.card.virtual_sources for action in virtual] == [(DIAMOND,)]
    assert virtual[0].card.suit == state.card(DIAMOND).suit, "单源转化继承花色"
    assert virtual[0].targets == (1,), "目标池走与真【乐不思蜀】同一套 check_targets"


def test_guose_source_zone_default_includes_the_equipment_zone():
    """区域默认：国色牌面只写"一张方块牌" ⇒ 手牌 ∪ 装备（验收：国色可选装备区方块牌）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,), equip=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(BLACK_SLASH, DIAMOND),
        turn_seat=0,
    )

    assert _sources(_play_candidates(state), "indulgence") == {(DIAMOND,)}

    judge_state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(DIAMOND),
        turn_seat=0,
    )
    assert not _virtual(_play_candidates(judge_state), "indulgence"), (
        "判定区的方块牌同样不是源（区域默认不包含判定区）"
    )


def test_guose_does_not_offer_a_non_diamond_card():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(BLACK_SLASH),
        turn_seat=0,
    )

    assert not _virtual(_play_candidates(state), "indulgence")


class _GuoseUser:
    """座位 0：按清单把方块牌当【乐不思蜀】用掉；其余一律结束阶段 / 放弃。"""

    def __init__(self) -> None:
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and not self.used:
            steps = query.transform_preactivations.get(DAQIAO)
            if steps:
                self.used = True
                return ActivateSkill(
                    skill_id=DAQIAO,
                    picks=(int(steps[0].pool[0].options[0]),),
                    choices=("seat:1",),
                    answers_seq=query.seq,
                )
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "EndPhase":
                return action
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "Pass":
                return action
        return query.legal_actions[0]


class _FrameSpy(Engine):
    """记下每一次压栈的帧类型与载荷：判定帧拿的是哪张牌只能这样观测。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[Any, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, dict(data)))
        return frame


def test_guose_places_its_virtual_indulgence_in_the_judge_zone_and_it_judges():
    """【国色】的方块牌当【乐不思蜀】要**真进判定区、真判定**（顺手修的真 bug）。

    现行 :meth:`Engine.move_used_card_to_judge_zone` 对虚拟牌直接 ``return``：来源方块牌
    卡在处理区，判定区永远为空、``JUDGE_ON`` 一次都不发。修法是把 ``virtual_sources`` 置入
    判定区，并在引擎里记下"这张判定牌其实是哪张虚拟牌"，判定阶段按虚拟牌结算。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(DIAMOND),
        turn_seat=0,
    )
    engine = _FrameSpy.from_state(state, seed=3)
    players = [_GuoseUser(), _GuoseUser()]
    for _ in range(3000):
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))

    judge_on = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.JUDGE_ON
    ]
    assert judge_on, "【国色】的【乐不思蜀】必须走到判定（现在 JUDGE_ON 一次都不发）"
    assert judge_on[0].actor == 1, "判定的是被指定的座位 1"
    resolve_cards = [
        data.get("card") for kind, data in engine.pushed if kind is FrameKind.RESOLVE
    ]
    assert any(getattr(card, "type", None) == "indulgence" for card in resolve_cards), (
        "判定阶段的 RESOLVE 帧必须拿那张**虚拟**乐不思蜀来跑效果，而不是来源方块牌"
    )
    assert DIAMOND in engine.state.discard, "判定收尾后来源方块牌进弃牌堆"
    assert not engine.state.processing, "处理区不该留下卡住的牌"
    assert not engine.state.virtual_judge, "来源牌离开判定区后映射要清掉"


def test_the_guose_virtual_indulgence_still_opens_a_nullify_window_offering_nullify():
    """B1 的**合法**邻例：国色把方块牌当【乐不思蜀】不能被一起修掉。

    判定阶段在**虚拟牌**的结算帧上照常开 ``NULLIFY`` 窗口；视图只投实体来源牌，所以
    从记录流上看很像"无懈在响应杀"，但那是延时锦囊 ⇒ 候选里必须有【无懈可击】。
    """
    nullify = _cards("nullify")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(DIAMOND,), hero="daqiao"), SeatSpec(hand=(nullify,))],
        deck=_deck_excluding(DIAMOND, nullify),
        turn_seat=0,
    )
    engine = _FrameSpy.from_state(state, seed=3)
    players = [_GuoseUser(), _GuoseUser()]
    for _ in range(3000):
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))

    windows = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.NULLIFY
    ]
    assert windows, "虚拟【乐不思蜀】的结算必须开无懈窗口"
    offered = {
        action.card.card_id
        for window in windows
        for action in window.legal_actions
        if isinstance(action, UseCard)
    }
    assert nullify in offered, "国色路径的【无懈可击】候选必须保留"
    assert {
        action.card.type
        for window in windows
        for action in window.legal_actions
        if isinstance(action, (UseCard, PlayCard))
    } == {"nullify"}, "无懈窗口只收【无懈可击】"


# ---------------------------------------------------------------------------
# 急救（华佗）：红色牌 →【桃】，限回合外（验收 6；时机判据用 ``turn_seat``）
# ---------------------------------------------------------------------------


def test_jijiu_is_offered_outside_its_owners_turn():
    """急救限于回合外：座位 1 的回合里，座位 0 的华佗把红牌当【桃】进候选。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hp=3, hero="huatuo"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=1,
    )

    virtual = _virtual_uses(_play_candidates(state, seat=0), "peach")

    assert [action.card.virtual_sources for action in virtual] == [(RED_PEACH,)]
    assert virtual[0].targets == (0,), "【桃】的目标规则照读真牌（只能是自己且已受伤）"


def test_jijiu_is_not_offered_inside_its_owners_turn():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hp=3, hero="huatuo"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )

    assert not _virtual(_play_candidates(state), "peach"), (
        "急救限于回合外：自己的回合内不进候选"
    )


# ---------------------------------------------------------------------------
# 急救接求桃窗口（W4-3）：`_peach` 的**转化源候选路径**
#
# 旧形状两处互斥：`scopes=(PLAY,)`（出牌阶段）＋ `outside_turn_only=True`（回合外）——
# 回合外没有出牌阶段，故急救进不了任何候选；求桃窗口又只取真实手牌。本票给求桃窗口
# 加转换化源的候选路径：清单 = 一张红牌（手牌 ∪ 装备），**无目标步**（濒死者由窗口固定）。
# ---------------------------------------------------------------------------


def _peach_candidates(state, seat: int, dying: int):
    return legal_actions_for(state, seat, QueryKind.PEACH, answers_seq=1, dying=dying)


def test_the_peach_window_offers_jijiu_as_a_transformed_peach_targeting_the_dying_seat():
    """验收：急救在求桃窗口**真实出现**——红牌（手牌 / 装备）当【桃】，目标 = 濒死者。"""
    for spec, source in (
        (SeatSpec(hand=(RED_PEACH,), hero="huatuo"), RED_PEACH),
        (SeatSpec(equip=(RED_DODGE,), hero="huatuo"), RED_DODGE),
    ):
        state = build_state(
            pack=PACK,
            seats=[spec, SeatSpec(hp=0)],
            deck=_deck_excluding(source),
            turn_seat=1,  # 华佗在回合外：急救的时机谓词
        )

        virtual = _virtual_uses(_peach_candidates(state, 0, dying=1), "peach")

        assert [action.card.virtual_sources for action in virtual] == [(source,)], (
            f"红牌 {source} 必须能当【桃】：{virtual}"
        )
        assert virtual[0].targets == (1,), "濒死者由窗口固定：转化【桃】的目标就是它"


def test_the_peach_window_never_reads_jijiu_sources_from_the_judge_zone():
    """区域默认：判定区的红牌不是急救的源（手牌 ∪ 装备）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(judge=(RED_DODGE,), hero="huatuo"), SeatSpec(hp=0)],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=1,
    )

    assert not _virtual(_peach_candidates(state, 0, dying=1), "peach")


def test_the_peach_window_does_not_offer_jijiu_during_the_owners_own_turn():
    """时机谓词照旧：急救只在回合外（自己的回合里求桃窗口不给转化【桃】）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="huatuo"), SeatSpec(hp=0)],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )

    assert not _virtual(_peach_candidates(state, 0, dying=1), "peach")


def test_a_real_near_death_session_is_rescued_by_jijiu_out_of_turn():
    """**真濒死会话**钉住急救：座位 2 的华佗在回合外把红牌当【桃】救回座位 1。

    这条走完整引擎（记录流 + 候选 + 移动），不是合成的 ``legal_actions_for`` 调用。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(hand=(RED_DODGE,), hero="huatuo"),
            SeatSpec(),
        ],
        deck=_deck_excluding(SLASH, RED_DODGE),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, SLASH, 1)
        return None

    def huatuo(query):
        if query.kind == QueryKind.PEACH:
            steps = query.transform_preactivations.get(HUATUO)
            if steps:
                return ActivateSkill(
                    skill_id=HUATUO,
                    picks=(int(steps[0].pool[0].options[0]),),
                    answers_seq=query.seq,
                )
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(), Scripted(huatuo), Scripted()],
        lambda engine: any(
            record.kind == "frame"
            and record.op == "pop"
            and record.frame_kind is FrameKind.NEAR_DEATH
            for record in engine.records
        ),
    )

    rescue = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.PEACH
        and record.query.actor == 2
    ]
    assert rescue, "濒死窗口必须问到回合外的座位 2（华佗）"
    assert not [
        action
        for action in rescue[0].legal_actions
        if isinstance(action, UseCard) and action.card.virtual_sources
    ], "转化【桃】不再是候选：急救只经清单进入"
    steps = rescue[0].transform_preactivations[HUATUO]
    assert [step.name for step in steps] == ["card"]
    assert tuple(steps[0].pool[0].options) == (str(RED_DODGE),)
    assert engine.state.player(1).hp == 1, "转化【桃】把濒死者救回 1 点体力"
    assert RED_DODGE not in engine.state.player(2).hand, "来源红牌走正常使用路径离手"
    assert RED_DODGE in engine.state.discard, "来源红牌使用后进弃牌堆"



# ---------------------------------------------------------------------------
# 票 14：「提供一张 B」的询问（【青龙偃月刀】的续杀）—— 候选 = 实体 B ＋ 转化技的虚拟 B
#
# 用户口径（acceptance §B 裁定 1–4）：需要使用或打出一张牌时**不新开区域**、就地高亮，
# 且这一步要能发动别的转化技（【龙胆】/【武圣】）——故询问的候选面必须同时装得下
# 实体【杀】与"别的技能转出来的【杀】"。
# ---------------------------------------------------------------------------


def _provide_query(engine, target: str = "slash"):
    """记录流里【青龙】"提供一张 B"那一步的 ``SELECT_CARDS`` 询问。"""
    for record in engine.records:
        if (
            record.kind == "query"
            and record.query.kind == QueryKind.SELECT_CARDS
            and getattr(record.query, "provide_type", None) == target
        ):
            return record.query
    raise AssertionError(f"记录流里没有 provide_type={target!r} 的选择面询问")


def _provide_face(query):
    """``provide`` 面每个槽的 ``(牌 id, 牌名, 来源实体牌)``（虚拟槽读 ``virtual_sources``）。"""
    return [
        (
            slot.card.card_id,
            slot.card.type,
            tuple(slot.card.virtual_sources),
        )
        for slot in query.slots
        if slot.card is not None
    ]


def _green_dragon_provide_driver(*, activation="activate"):
    """座位 0：用首张【杀】打座位 1；【青龙】发动询问按 ``activation`` 应答。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, SLASH, 1)
        if query.kind == QueryKind.SKILL_ACTIVATION:
            if activation == "activate":
                return _find(query, lambda action: isinstance(action, ActivateSkill))
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    return Scripted(driver)


def _dodge_with_real_card(query):
    if query.kind == QueryKind.DODGE:
        return _find(query, lambda action: isinstance(action, UseCard))
    return None


def _to_provide_step(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.SELECT_CARDS
        and getattr(engine.pending, "provide_type", None) == "slash"
    )


def test_green_dragon_provide_face_offers_the_hand_slash_alongside_longdans_dodge():
    """续杀的询问里**同时**有手牌【杀】与【龙胆】的【闪】转化的虚拟【杀】（票 14 验收 1）。"""
    second = _cards("slash")[1]
    dodge = RED_DODGE
    defender_dodge = _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, second, dodge), equip=(GREEN_DRAGON,), hero="zhaoyun"),
            SeatSpec(hand=(defender_dodge,)),
        ],
        deck=_deck_excluding(SLASH, second, dodge, GREEN_DRAGON, defender_dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [_green_dragon_provide_driver(), Scripted(_dodge_with_real_card)],
        _to_provide_step,
    )

    face = _provide_face(_provide_query(engine))

    assert (second, "slash", ()) in face, f"手牌里的实体【杀】必须进面：{face}"
    assert any(
        type_id == "slash" and sources == (dodge,) for _card_id, type_id, sources in face
    ), f"【龙胆】的【闪】→【杀】必须进面：{face}"
    assert len(face) == 2, f"这一步此刻恰有两张可提供的【杀】：{face}"


def test_green_dragon_provide_face_reads_wusheng_sources_from_any_zone():
    """【武圣】的红牌（**含装备区**）也进"提供一张【杀】"的面（票 14 验收 1）。"""
    horse = _cards("chitu")[0]
    defender_dodge = _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(GREEN_DRAGON, horse), hero="guanyu"),
            SeatSpec(hand=(defender_dodge,)),
        ],
        deck=_deck_excluding(SLASH, GREEN_DRAGON, horse, defender_dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [_green_dragon_provide_driver(), Scripted(_dodge_with_real_card)],
        _to_provide_step,
    )

    face = _provide_face(_provide_query(engine))

    # 虚拟槽的第一项是**虚拟牌** id（负数）；来源实体牌读 ``virtual_sources``——与前一条
    # 用例的 ``sources == (dodge,)`` 同姿态，不能拿来源 id 去比虚拟牌 id。
    assert any(
        type_id == "slash" and sources == (horse,) for _card_id, type_id, sources in face
    ), f"装备区的红牌是【武圣】的合法来源：{face}"
    assert all(
        sources != (GREEN_DRAGON,) for _card_id, _type_id, sources in face
    ), "【青龙偃月刀】本身是黑牌，不能当【武圣】的来源"


def test_green_dragon_continuation_ignores_distance_and_uses_the_original_target():
    """续杀**原目标沿用 / 不计次数 / 不限距离**：目标离开攻击范围也照打（票 14 验收 2）。

    构造：8 个座位，座位 0 到座位 4 的距离是 4；关羽装备【青龙偃月刀】（攻击范围 3）
    与进攻马赤兔（−1）⇒ 距离 3，最初那张【杀】合法。座位 4 用【闪】抵消后，续杀**用赤兔
    本身**作【武圣】的来源 ⇒ 马一离场距离就回到 4，但这一次使用**不重算距离**
    （目标直接沿用原【杀】的目标）。
    """
    horse = _cards("chitu")[0]
    defender_dodge = _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(GREEN_DRAGON, horse), hero="guanyu"),
            *[SeatSpec() for _ in range(3)],
            SeatSpec(hand=(defender_dodge,)),
            *[SeatSpec() for _ in range(3)],
        ],
        deck=_deck_excluding(SLASH, GREEN_DRAGON, horse, defender_dodge),
        turn_seat=0,
    )

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, SLASH, 4)
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS:
            # **必须挑赤兔那张**（虚拟槽的 ``card_id`` 是负数，来源读 ``virtual_sources``）：
            # 摸牌阶段进手的两张红牌也是【武圣】的合法来源，改挑它们马不离场、距离压根不变，
            # 这条用例就证明不了"续杀不重算距离"。
            index = next(
                i
                for i, slot in enumerate(query.slots)
                if slot.card is not None and tuple(slot.card.virtual_sources) == (horse,)
            )
            return _find(
                query,
                lambda action: getattr(action, "kind", None) == "SelectCards"
                and tuple(getattr(action, "picks", ())) == (index,),
            )
        return None

    def defender(query):
        if query.kind == QueryKind.DODGE:
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    # ``participants`` **按座位号索引**（``game.Engine.run``）：8 个座位就得给 8 个参与者，
    # 追问【闪】的是座位 4，只给两个会在那个询问上 IndexError（不是引擎问错 actor）。
    engine = _run(
        state,
        [
            Scripted(driver),
            *[Scripted() for _ in range(3)],
            Scripted(defender),
            *[Scripted() for _ in range(3)],
        ],
        lambda engine: engine.state.player(4).hp < 4,
    )

    assert engine.state.player(4).hp == 3, "续杀命中：目标离开攻击范围也照打（不限距离）"
    assert engine.state.player(0).card_use_counts.get("slash", 0) == 1, (
        "续杀不计次数：本回合【杀】的计数只算最初那张"
    )
    assert horse in engine.state.discard, "【武圣】的来源（赤兔）走正常的使用路径离场"


# ---------------------------------------------------------------------------
# 预发动清单（W4-3）：五条转化技的入口 = "点技能 → 选源牌 → 选目标 → 确认"
#
# 清单从**这次询问的候选**派生（候选 = 清单：来源组就是候选的 ``virtual_sources``），
# 故张数 / 目标池 / 区域 / 次数链与候选天然同源。确认仍是**普通候选**
# ``UseCard(虚拟牌, 目标)``（本来就一次动作），不产生复合动作。
# ---------------------------------------------------------------------------


def _virtual_actions(state, seat: int = 0):
    """``PLAY_PHASE`` 里本座位全部**虚拟牌**候选（转化技的产物）。"""
    return [
        action
        for action in legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, (UseCard, PlayCard)) and action.card.is_virtual
    ]


def _manifest(state, actions, skill_id: str, *, fixed_target: bool = False, seat: int = 0):
    from sgs.skills import conversion_preactivations

    return conversion_preactivations(
        state, seat, actions, fixed_target=fixed_target
    )[skill_id]


def test_wusheng_preactivation_manifest_orders_the_red_source_then_the_slash_target():
    """武圣（主动使用）：1. 一张红牌（手牌 ∪ 装备）；2. 这张【杀】的目标。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH,), equip=(RED_DODGE,), judge=(RED_SLASH,), hero="guanyu"),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, RED_DODGE, RED_SLASH),
        turn_seat=0,
    )

    steps = _manifest(state, _virtual_actions(state), GUANYU)

    assert [step.name for step in steps] == ["card", "seat"]
    assert [step.order for step in steps] == [0, 1]
    source = steps[0]
    assert source.min == source.max == 1, "一张红牌"
    assert set(source.zones) == {Zone.HAND, Zone.EQUIP}, "区域默认：手牌 ∪ 装备，不含判定区"
    assert source.pool[0].when == ()
    assert set(source.pool[0].options) == {str(RED_PEACH), str(RED_DODGE)}, (
        f"判定区的红牌不是源：{source.pool[0].options}"
    )
    target = steps[1]
    assert target.min == target.max == 1
    assert [option for row in target.pool for option in row.options] == ["seat:1"]


def test_longdan_manifests_have_a_source_step_only_in_both_response_directions():
    """龙胆（响应）：1. 一张【杀】/【闪】；**无目标**（打出的目标由窗口承载）。"""
    dodge_state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(RED_SLASH,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_SLASH),
        turn_seat=0,
    )
    dodge_actions = [
        action
        for action in legal_actions_for(dodge_state, 0, QueryKind.DODGE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.is_virtual
    ]
    slash_direction = _manifest(dodge_state, dodge_actions, ZHAOYUN_SLASH)
    assert [step.name for step in slash_direction] == ["card"], "响应：无目标步"
    assert tuple(slash_direction[0].pool[0].options) == (str(RED_SLASH),)
    assert tuple(slash_direction[0].zones) == (Zone.EQUIP,), "区域默认：装备区的【杀】可当【闪】"

    respond_state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    respond_actions = [
        action
        for action in legal_actions_for(
            respond_state, 0, QueryKind.RESPOND, answers_seq=1, payload=_respond("slash")
        )
        if isinstance(action, PlayCard) and action.card.is_virtual
    ]
    assert respond_actions, "RESPOND（南蛮 / 决斗的打出窗口）必须把【闪】转的【杀】放进候选"
    other_direction = _manifest(respond_state, respond_actions, ZHAOYUN_SLASH)
    assert [step.name for step in other_direction] == ["card"]
    assert tuple(other_direction[0].pool[0].options) == (str(RED_DODGE),)


def test_longdan_active_use_manifest_takes_the_dodge_then_the_slash_target():
    """龙胆（主动使用，当【杀】）：1. 一张【闪】（手牌 ∪ 装备）；2. 这张【杀】的目标。

    与上面两条**响应方向**的清单不同：主动使用带目标步，``seat`` 步的池就是这张虚拟
    【杀】此刻的合法目标（W4 评审处置 B.2；渲染早已如此，缺的是这条钉子）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    actions = [action for action in _virtual_actions(state) if action.card.type == "slash"]
    assert actions, "主动使用方向：手牌【闪】必须能转成【杀】并出现在出牌阶段候选里"

    steps = _manifest(state, actions, ZHAOYUN_SLASH)

    assert [step.name for step in steps] == ["card", "seat"]
    assert [step.order for step in steps] == [0, 1]
    source = steps[0]
    assert source.min == source.max == 1, "一张【闪】"
    assert tuple(source.zones) == (Zone.HAND,), "这张【闪】此刻在手里"
    assert tuple(source.pool[0].options) == (str(RED_DODGE),)
    target = steps[1]
    assert target.min == target.max == 1
    assert [option for row in target.pool for option in row.options] == ["seat:1"], (
        "目标步的池 = 这张【杀】的目标（不是「所有座位」）"
    )


def test_guose_manifest_reads_diamond_sources_from_the_equipment_zone_too():
    """国色（主动使用）：1. 一张方块牌（手牌 ∪ 装备）；2. 这张【乐不思蜀】的目标。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,), equip=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(BLACK_SLASH, DIAMOND),
        turn_seat=0,
    )

    steps = _manifest(state, _virtual_actions(state), DAQIAO)

    assert [step.name for step in steps] == ["card", "seat"]
    assert tuple(steps[0].pool[0].options) == (str(DIAMOND),)
    assert set(steps[0].zones) == {Zone.EQUIP}
    assert [option for row in steps[1].pool for option in row.options] == ["seat:1"]


def test_jijiu_peach_manifest_is_one_red_card_and_has_no_target_step():
    """急救（求桃窗口）：1. 一张红牌（手牌 ∪ 装备）；**无目标步**（濒死者由窗口固定）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(RED_DODGE,), hero="huatuo"), SeatSpec(hp=0)],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=1,
    )

    actions = [
        action
        for action in _peach_candidates(state, 0, dying=1)
        if isinstance(action, UseCard) and action.card.is_virtual
    ]
    steps = _manifest(state, actions, HUATUO, fixed_target=True)

    assert [step.name for step in steps] == ["card"], "无目标步：濒死者由求桃窗口固定"
    assert tuple(steps[0].pool[0].options) == (str(RED_DODGE),)
    assert tuple(steps[0].zones) == (Zone.EQUIP,)


def test_serpent_spear_manifest_takes_exactly_two_hand_cards_then_the_slash_target():
    """丈八蛇矛：1. 恰好两张手牌；2.（使用）这张【杀】的目标。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )

    steps = _manifest(state, _virtual_actions(state), "equip.serpent_spear")

    assert [step.name for step in steps] == ["card", "seat"]
    assert steps[0].min == steps[0].max == 2, "恰好两张手牌"
    assert tuple(steps[0].zones) == (Zone.HAND,)
    assert set(steps[0].pool[0].options) == {str(first), str(second)}
    assert [option for row in steps[1].pool for option in row.options] == ["seat:1"]


def test_serpent_spear_respond_manifest_has_only_the_two_hand_sources():
    """丈八蛇矛（响应·当【杀】打出）：1. 恰好两张手牌；**无目标步**。

    使用侧的清单由上面那条钉（``card`` + ``seat``）；这一条补响应方向：打出的目标由
    窗口承载，清单只有来源步（W4 评审处置 B.3；渲染早已如此，缺的是这条钉子）。
    """
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )
    actions = [
        action
        for action in legal_actions_for(
            state, 0, QueryKind.RESPOND, answers_seq=1, payload=_respond("slash")
        )
        if isinstance(action, PlayCard) and action.card.is_virtual
    ]
    assert actions, "响应方向：两张手牌必须能转成【杀】并出现在 RESPOND 候选里"

    steps = _manifest(state, actions, "equip.serpent_spear")

    assert [step.name for step in steps] == ["card"], "响应：无目标步"
    assert steps[0].min == steps[0].max == 2, "恰好两张手牌"
    assert tuple(steps[0].zones) == (Zone.HAND,)
    assert set(steps[0].pool[0].options) == {str(first), str(second)}


def test_the_play_phase_query_carries_the_transform_preactivation_manifests():
    """跨线写入面：清单落在**询问**上（由接入层下发），不是接入层自己再算一份。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(), Scripted()],
        lambda engine: engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE,
    )
    query = engine.pending

    assert query is not None
    assert GUANYU in query.transform_preactivations, (
        f"出牌阶段的询问必须带转化技清单：{query.transform_preactivations}"
    )
    steps = query.transform_preactivations[GUANYU]
    assert [step.name for step in steps] == ["card", "seat"]
    assert str(RED_PEACH) in steps[0].pool[0].options
    assert "transform_preactivations" not in query.model_dump(mode="json"), (
        "清单是派生数据：不进记录流（既有 Golden Replay 因此逐字节不变）"
    )


def test_green_dragon_provide_face_reads_longdan_equip_slash_and_skips_the_judge_zone():
    """青龙的"1 张【杀】"步骤：实体【杀】∪ 转化【杀】（龙胆 / 武圣），来源含装备、不含判定。

    龙胆要把一张【闪】**转成**【杀】，故"装备区的【杀】"这条读法不成立——放装备区的是一张
    【闪】。判定区的同一张【闪】不合法（区域默认不包含判定区）。
    """
    equip_dodge = _cards("dodge")[1]
    judge_dodge = _cards("dodge")[2]
    defender_dodge = _cards("dodge")[3]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=(SLASH,),
                equip=(GREEN_DRAGON, equip_dodge),
                judge=(judge_dodge,),
                hero="zhaoyun",
            ),
            SeatSpec(hand=(defender_dodge,)),
        ],
        deck=_deck_excluding(SLASH, GREEN_DRAGON, equip_dodge, judge_dodge, defender_dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [_green_dragon_provide_driver(), Scripted(_dodge_with_real_card)],
        _to_provide_step,
    )

    face = _provide_face(_provide_query(engine))

    assert (SLASH, "slash", ()) not in face, "最初那张【杀】已经被用掉，不在手里"
    assert any(
        type_id == "slash" and sources == (equip_dodge,) for _card_id, type_id, sources in face
    ), f"装备区的【闪】经【龙胆】转出的【杀】必须进面：{face}"
    assert all(
        sources != (judge_dodge,) for _card_id, _type_id, sources in face
    ), "判定区的牌不是源（区域默认不包含判定区）"


def test_green_dragon_provide_zones_cover_every_source_zone_in_its_pool():
    """青龙的提供步：``Step.zones`` 必须覆盖池里每个来源此刻所在的区。

    W4-3 的区域默认让【龙胆】/【武圣】的转化源也能来自装备区；因此青龙这一行的
    ``PickProvide.zones``（原来只写 ``(HAND,)``）必须与池同源（手牌 ∪ 装备）。
    ``flow.manifest`` 的语义不动：它照旧把 ``PickProvide.zones`` 摊成 ``Step.zones``
    （W4 评审处置 C.6）。
    """
    equip_dodge = _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(GREEN_DRAGON, equip_dodge), hero="zhaoyun"),
            SeatSpec(),
        ],
        deck=_deck_excluding(SLASH, GREEN_DRAGON, equip_dodge),
        turn_seat=0,
    )

    steps = preactivation_for(state, 0, "equip.green_dragon_blade")

    assert [step.name for step in steps] == ["card"]
    step = steps[0]
    sources = tuple(int(option) for option in step.pool[0].options)
    assert set(sources) == {SLASH, equip_dodge}, (
        "池 = 手牌实体【杀】 ＋ 龙胆把装备区【闪】转出的【杀】"
    )
    source_zones = {locate(state, source).zone for source in sources}
    assert Zone.EQUIP in source_zones, "池里确有装备区来源（否则这条断言不说明问题）"
    assert source_zones <= set(step.zones), (
        f"Step.zones={tuple(zone.value for zone in step.zones)} 必须覆盖池里来源所在的区 "
        f"{sorted(zone.value for zone in source_zones)}"
    )


# ---------------------------------------------------------------------------
# W4-fix-2：转化技只经预发动（用户裁定 A）
#
# 候选收口：四类语境的 `legal_actions` 里**不再有**任何 `virtual_sources` 非空的
# 使用 / 打出动作；转化能力只由 `transform_preactivations`（从引擎**内部**候选池派生）
# 表达。确认 = 按清单交出来源集合（＋目标集合）的 `ActivateSkill`，由**服务端**解析成
# 那条普通 `UseCard(虚拟牌, 目标)`（与候选同源的那批内部动作）；校验失败零状态改动。
# ---------------------------------------------------------------------------

SERPENT_SKILL = "equip.serpent_spear"


def _transformed_uses(actions, type_id: str | None = None):
    """候选里 `virtual_sources` 非空的**使用 / 打出**动作（转化牌）。"""
    return [
        action
        for action in actions
        if isinstance(action, (UseCard, PlayCard))
        and action.card.virtual_sources
        and (type_id is None or action.card.type == type_id)
    ]


def _parked_at(state, kind: QueryKind, *, participants=None):
    """跑到 `state` 上的第一个 `kind` 询问（引擎真的停下，`pending` 即它）。"""
    players = participants or [Scripted(), Scripted()]
    engine = _run(
        state,
        players,
        lambda running: running.pending is not None and running.pending.kind == kind,
    )
    assert engine.pending is not None and engine.pending.kind == kind, (
        f"没有走到 {kind} 询问，停在 {engine.pending}"
    )
    return engine


def _drive_until_pending(engine, *, limit: int = 4000):
    """提交后把引擎推到下一个询问（不判终局）。"""
    for _ in range(limit):
        if engine.pending is not None or engine.finished:
            return engine
        engine._step()
    raise AssertionError(f"{limit} 步内没有回到询问")


def test_the_play_phase_hides_transform_candidates_but_still_carries_the_manifest():
    """出牌阶段：红牌不再"直接当【杀】"，但清单照旧由此刻的转化候选派生。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )

    query = _parked_at(state, QueryKind.PLAY_PHASE).pending

    assert _transformed_uses(query.legal_actions) == [], (
        f"出牌阶段的候选里不得再有转化牌：{query.legal_actions}"
    )
    steps = query.transform_preactivations[GUANYU]
    assert str(RED_PEACH) in steps[0].pool[0].options, (
        "清单仍从**内部**候选池派生：来源红牌必须在来源步里"
    )
    assert [step.name for step in steps] == ["card", "seat"]


def test_the_dodge_window_hides_the_longdan_candidate_but_still_carries_the_manifest():
    """【闪】窗口：龙胆的虚拟【闪】不再直接可选，入口只剩清单。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(hand=(RED_SLASH,), hero="zhaoyun")],
        deck=_deck_excluding(SLASH, RED_SLASH),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _slash_use(query, SLASH, 1)
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )

    query = _parked_at(
        state, QueryKind.DODGE, participants=[attacker, Scripted()]
    ).pending

    assert _transformed_uses(query.legal_actions, "dodge") == []
    assert [step.name for step in query.transform_preactivations[ZHAOYUN_SLASH]] == ["card"]


def test_the_arrow_barrage_window_keeps_the_longdan_manifest_and_waives_the_damage():
    """【万箭齐发】：转化【闪】仍是**内部**候选，入口只剩龙胆清单，确认后伤害被免除。

    W4-fix-6 的 A 项：这扇窗口的候选由 ``CandidateScope.DODGE`` 产出（``UseCard(闪)``），
    旧判据按 ``QueryKind.RESPOND`` 只收 ``PlayCard``，于是候选被删、清单也没了——
    赵云的【杀】→【闪】能力**整个消失**。修法按 ``RespondContext`` 区分：只有借刀杀人
    的"被迫使用"排除，其余响应窗口的转化候选照旧进内部池。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(ARROW_BARRAGE,)), SeatSpec(hand=(SLASH,), hero="zhaoyun")],
        deck=_deck_excluding(ARROW_BARRAGE, SLASH),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _find(
            query,
            lambda action: isinstance(action, UseCard)
            and action.card.card_id == ARROW_BARRAGE,
        )
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )
    engine = _run(
        state,
        [attacker, Scripted()],
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.RESPOND
        and running.pending.cause == WindowCause.ARROW_BARRAGE,
    )
    query = engine.pending
    assert query is not None and query.actor == 1

    assert _transformed_uses(query.legal_actions, "dodge") == [], (
        f"【万箭】窗口的候选里不得再有转化【闪】：{query.legal_actions}"
    )
    assert _transformed_uses(query.transform_actions, "dodge"), (
        "内部池必须保留那条转化【闪】——清单与确认都从它派生"
    )
    assert ZHAOYUN_SLASH in query.transform_preactivations, (
        f"【万箭】窗口必须下发龙胆清单：{query.transform_preactivations}"
    )
    assert [step.name for step in query.transform_preactivations[ZHAOYUN_SLASH]] == ["card"], (
        "目标由窗口固定 ⇒ 清单只有来源步"
    )

    confirm = ActivateSkill(skill_id=ZHAOYUN_SLASH, picks=(SLASH,), answers_seq=query.seq)
    assert confirm not in query.legal_actions
    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(1).hp == 4, "按清单确认的转化【闪】免除了【万箭】的伤害"
    assert SLASH in engine.state.discard, "来源【杀】走正常使用路径进弃牌堆"


def _borrowed_play_query(engine):
    """引擎停在出牌阶段：返回询问（借刀入口 = 清单 + 内部附带指定池）。"""
    assert engine.pending is not None and engine.pending.kind == QueryKind.PLAY_PHASE
    assert isinstance(engine.pending, PlayPhaseQuery)
    return engine.pending


def _borrowed_window(state, designation: int = 2, *, seats: int = 3):
    """驱动到**持刀者窗口**：先按清单指定 [A=1, Bn=designation]，再由持刀者决定 a / b。"""
    players = [Scripted() for _ in range(seats)]
    engine = _run(
        state,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.PLAY_PHASE,
    )
    play = _borrowed_play_query(engine)
    action = next(
        candidate
        for candidate in play.designation_actions
        if candidate.designations == (designation,)
    )
    engine.submit(action)
    run_until(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.RESPOND
        and running.pending.cause is WindowCause.BORROWED_SWORD,
    )
    return engine, play


def test_the_borrowed_sword_holder_window_carries_the_source_only_transform_manifest():
    """翻转（W5-02b）：借刀窗口的转化清单**下发了**，且只有来源步（目标固定 Bn）。

    W4-fix-6 的 A 项曾把借刀窗口的转化候选从暴露面与内部池一并收回（W5 才做"使用牌"
    的第二段预发动）。02b 把入口改成清单后，这扇窗口与其余响应窗口同形：
    ``transform_preactivations[技能]`` = 来源步，确认解析成 ``UseCard(虚拟【杀】, (Bn,))``。
    """
    borrowed = _cards("borrowed_sword")[0]
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(RED_DODGE,), equip=(weapon,), hero="zhaoyun"),
            SeatSpec(),
        ],
        deck=_deck_excluding(borrowed, weapon, RED_DODGE),
        turn_seat=0,
    )
    engine, play = _borrowed_window(state)

    assert not [
        action
        for action in play.legal_actions
        if isinstance(action, UseCard) and action.card.type == "borrowed_sword"
    ], "裸借刀候选已收回：入口只剩清单"
    assert [(step.name, step.min, step.max) for step in play.card_preactivations["borrowed_sword"]] == [
        ("seat", 1, 1),
        ("seat", 1, 1),
    ], "外层两步清单：持刀者 A → Bn"
    assert play.designation_actions, "内部附带指定池仍服务第 4 条提交路"

    window = engine.pending
    assert window is not None and window.actor == 1
    assert window.cause is WindowCause.BORROWED_SWORD
    assert _transformed_uses(window.legal_actions, "slash") == [], (
        "转化候选仍从暴露面收回（入口 = 清单）"
    )
    assert _transformed_uses(window.transform_actions, "slash"), (
        "内部池必须保留那条转化【杀】——清单与确认都从它派生"
    )
    assert ZHAOYUN_SLASH in window.transform_preactivations, (
        f"借刀窗口必须下发龙胆清单：{window.transform_preactivations}"
    )
    assert [step.name for step in window.transform_preactivations[ZHAOYUN_SLASH]] == ["card"], (
        "目标由窗口固定 ⇒ 清单只有来源步"
    )
    assert {
        action.targets
        for action in window.legal_actions
        if isinstance(action, UseCard) and not action.card.virtual_sources
    } == set(), "这扇窗口里没有真牌候选"

    confirm = ActivateSkill(
        skill_id=ZHAOYUN_SLASH, picks=(RED_DODGE,), answers_seq=window.seq
    )
    assert confirm not in window.legal_actions
    engine.submit(confirm)
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    resolved = accepted.action
    assert isinstance(resolved, UseCard)
    assert resolved.card.type == "slash" and resolved.card.virtual_sources == (RED_DODGE,)
    assert resolved.targets == (2,), "目标固定为使用者指定的 Bn"


_BORROWED_TRANSFORM_CASES = (
    ("武圣", "guanyu", (RED_PEACH,), (RED_PEACH,), GUANYU),
    ("龙胆", "zhaoyun", (RED_DODGE,), (RED_DODGE,), ZHAOYUN_SLASH),
    ("丈八蛇矛", None, (RED_PEACH, RED_DODGE), (RED_PEACH, RED_DODGE), "equip.serpent_spear"),
)
"""``(标签, 武将, 手牌, 来源牌, 技能 id)``：三条转化技在借刀窗口里各确认一次。

丈八蛇矛没有武将，武器就是它自己（``SERPENT``）；来源步要求**两张**手牌。
"""


def test_every_transformed_slash_in_the_borrowed_window_resolves_to_bn():
    """【武圣】/【龙胆】/【丈八】的清单可确认，并解析成 ``UseCard(虚拟【杀】, (Bn,))``。"""
    for label, hero, hand, picks, skill_id in _BORROWED_TRANSFORM_CASES:
        equip = (SERPENT,) if skill_id == "equip.serpent_spear" else (_cards("crossbow")[0],)
        borrowed = _cards("borrowed_sword")[0]
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(borrowed,)),
                SeatSpec(hand=hand, equip=equip, hero=hero),
                SeatSpec(),
            ],
            deck=_deck_excluding(borrowed, *equip, *hand),
            turn_seat=0,
        )
        engine, _play = _borrowed_window(state)
        window = engine.pending
        assert window is not None and window.cause is WindowCause.BORROWED_SWORD

        steps = window.transform_preactivations[skill_id]
        assert [step.name for step in steps] == ["card"], (
            f"{label}：目标由窗口固定 ⇒ 清单只有来源步"
        )
        assert steps[0].min == len(picks), f"{label}：来源步张数 = 转化源的张数"

        confirm = ActivateSkill(skill_id=skill_id, picks=picks, answers_seq=window.seq)
        engine.submit(confirm)
        accepted = engine.records[-1]
        assert accepted.kind == "action" and accepted.accepted is True, f"{label}：确认被拒"
        resolved = accepted.action
        assert isinstance(resolved, UseCard), f"{label}：解析结果不是 UseCard"
        assert resolved.card.type == "slash" and resolved.card.virtual_sources, (
            f"{label}：解析出的不是转化【杀】"
        )
        assert resolved.targets == (2,), f"{label}：目标应固定为 Bn=2"


def test_the_respond_window_hides_the_wusheng_play_but_still_carries_the_manifest():
    """响应窗口（南蛮入侵的**打出**）：红牌不再直接当【杀】打出。"""
    barbarian = _cards("barbarian_invasion")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(RED_PEACH,), hero="guanyu")],
        deck=_deck_excluding(barbarian, RED_PEACH),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _find(
            query,
            lambda action: isinstance(action, UseCard)
            and action.card.card_id == barbarian,
        )
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )

    query = _run(
        state,
        [attacker, Scripted()],
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.RESPOND
        and running.pending.cause == WindowCause.BARBARIAN_INVASION,
    ).pending

    assert query is not None and query.actor == 1
    assert _transformed_uses(query.legal_actions, "slash") == [], (
        f"响应窗口里不得再有转化【杀】：{query.legal_actions}"
    )
    assert str(RED_PEACH) in query.transform_preactivations[GUANYU][0].pool[0].options


def test_the_peach_window_keeps_the_real_peach_and_hides_the_jijiu_candidate():
    """求桃窗口：真【桃】照旧可直接点，急救的转化【桃】只经清单（濒死者由窗口固定）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(hand=(RED_PEACH,), hero="huatuo"),
        ],
        deck=_deck_excluding(SLASH, RED_PEACH),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _slash_use(query, SLASH, 1)
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )

    query = _run(
        state,
        [attacker, Scripted(), Scripted()],
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.PEACH
        and running.pending.actor == 2,
    ).pending

    assert query is not None and query.actor == 2
    real = [
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == RED_PEACH
        and action.targets == (1,)
    ]
    assert real, "真【桃】仍是候选（可直接点）"
    assert _transformed_uses(query.legal_actions, "peach") == [], (
        "急救的转化【桃】不再作为候选出现"
    )
    steps = query.transform_preactivations[HUATUO]
    assert [step.name for step in steps] == ["card"], "无目标步：濒死者由窗口固定"


def test_a_wusheng_confirmation_from_the_manifest_uses_the_slash_normally():
    """武圣：按清单交来源红牌 + 目标座位 ⇒ 服务端解析成普通的转化【杀】使用。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    options = query.transform_preactivations[GUANYU][0].pool[0].options
    assert str(RED_PEACH) in options, "清单的来源步由内部候选派生"

    confirm = ActivateSkill(
        skill_id=GUANYU, picks=(RED_PEACH,), choices=("seat:1",), answers_seq=query.seq
    )
    assert confirm not in query.legal_actions

    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(0).card_use_counts.get("slash") == 1, "解析出的是一次【杀】的使用"
    assert RED_PEACH in engine.state.processing, "来源红牌走正常使用路径离开手牌、进处理区"
    accepted = [record for record in engine.records if record.kind == "action" and record.accepted]
    assert accepted, "确认被接受"


def test_a_longdan_confirmation_from_the_manifest_cancels_the_slash():
    """龙胆（【杀】→【闪】方向）：清单确认 ⇒ 服务端解析成普通的转化【闪】使用。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(hand=(RED_SLASH,), hero="zhaoyun")],
        deck=_deck_excluding(SLASH, RED_SLASH),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _slash_use(query, SLASH, 1)
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )
    engine = _parked_at(state, QueryKind.DODGE, participants=[attacker, Scripted()])
    query = engine.pending
    assert [step.name for step in query.transform_preactivations[ZHAOYUN_SLASH]] == ["card"]

    confirm = ActivateSkill(
        skill_id=ZHAOYUN_SLASH, picks=(RED_SLASH,), answers_seq=query.seq
    )
    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(1).hp == 4, "转化【闪】抵消了这次【杀】"
    assert RED_SLASH in engine.state.discard, "来源【杀】走正常使用路径进弃牌堆"


def test_a_longdan_confirmation_from_the_manifest_uses_the_slash_normally():
    """龙胆（【闪】→【杀】方向）：出牌阶段的清单确认解析成普通的转化【杀】使用。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), hero="zhaoyun"), SeatSpec()],
        deck=_deck_excluding(RED_DODGE),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    options = query.transform_preactivations[ZHAOYUN_SLASH][0].pool[0].options
    assert str(RED_DODGE) in options

    confirm = ActivateSkill(
        skill_id=ZHAOYUN_SLASH, picks=(RED_DODGE,), choices=("seat:1",), answers_seq=query.seq
    )
    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(0).card_use_counts.get("slash") == 1
    assert RED_DODGE in engine.state.processing


def test_a_guose_confirmation_from_the_manifest_places_the_indulgence():
    """国色：清单确认解析成普通的转化【乐不思蜀】使用（进判定区、来源方块牌离手）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(DIAMOND,), hero="daqiao"), SeatSpec()],
        deck=_deck_excluding(DIAMOND),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    assert str(DIAMOND) in query.transform_preactivations[DAQIAO][0].pool[0].options

    confirm = ActivateSkill(
        skill_id=DAQIAO, picks=(DIAMOND,), choices=("seat:1",), answers_seq=query.seq
    )
    engine.submit(confirm)
    _drive_until_pending(engine)

    judge = tuple(engine.state.player(1).judge)
    assert judge == (DIAMOND,), "【乐不思蜀】的来源方块牌进了被指定座位的判定区"
    assert engine.state.virtual_judge[DIAMOND].type == "indulgence", (
        "判定区里摆的是来源牌，映射记着它代表的那张虚拟【乐不思蜀】"
    )


def test_a_jijiu_confirmation_from_the_manifest_rescues_the_dying_seat():
    """急救：求桃窗口的清单确认（一张红牌、无目标步）解析成目标固定的转化【桃】。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(hand=(RED_DODGE,), hero="huatuo"),
        ],
        deck=_deck_excluding(SLASH, RED_DODGE),
        turn_seat=0,
    )
    attacker = Scripted(
        lambda query: _slash_use(query, SLASH, 1)
        if query.kind == QueryKind.PLAY_PHASE
        else None
    )
    engine = _run(
        state,
        [attacker, Scripted(), Scripted()],
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.PEACH
        and running.pending.actor == 2,
    )
    query = engine.pending
    assert [step.name for step in query.transform_preactivations[HUATUO]] == ["card"]

    confirm = ActivateSkill(skill_id=HUATUO, picks=(RED_DODGE,), answers_seq=query.seq)
    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(1).hp == 1, "转化【桃】把濒死者救回 1 点体力"
    assert RED_DODGE not in engine.state.player(2).hand
    assert RED_DODGE in engine.state.discard


def test_a_serpent_spear_confirmation_from_the_manifest_takes_two_hand_cards():
    """丈八蛇矛：清单确认 = 恰好两张手牌 + 目标，解析成普通的转化【杀】使用。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    step = query.transform_preactivations[SERPENT_SKILL][0]
    assert step.min == step.max == 2

    confirm = ActivateSkill(
        skill_id=SERPENT_SKILL,
        picks=(first, second),
        choices=("seat:1",),
        answers_seq=query.seq,
    )
    engine.submit(confirm)
    _drive_until_pending(engine)

    assert engine.state.player(0).card_use_counts.get("slash") == 1
    assert first in engine.state.processing and second in engine.state.processing


def test_a_transform_confirmation_with_a_bad_source_changes_nothing():
    """校验失败：零状态改动、`accepted=false`、首因写进拒绝记录。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    baseline = json.dumps(engine.state.model_dump(mode="json"), sort_keys=True)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            ActivateSkill(
                skill_id=GUANYU, picks=(BLACK_SLASH,), choices=("seat:1",), answers_seq=query.seq
            )
        )

    assert "来源牌不合法" in caught.value.message
    assert json.dumps(engine.state.model_dump(mode="json"), sort_keys=True) == baseline
    assert engine.pending is query
    rejected = engine.records[-1]
    assert rejected.accepted is False and "转化确认失败" in rejected.reason


def test_a_transform_confirmation_with_a_target_outside_the_pool_changes_nothing():
    """目标的合法性同样由候选池回答：不在池里的座位 ⇒ 目标不合法、零状态改动。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), hero="guanyu"), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )
    engine = _parked_at(state, QueryKind.PLAY_PHASE)
    query = engine.pending
    baseline = json.dumps(engine.state.model_dump(mode="json"), sort_keys=True)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            ActivateSkill(
                skill_id=GUANYU, picks=(RED_PEACH,), choices=("seat:0",), answers_seq=query.seq
            )
        )

    assert "目标不合法" in caught.value.message
    assert json.dumps(engine.state.model_dump(mode="json"), sort_keys=True) == baseline
    assert engine.pending is query


# ---------------------------------------------------------------------------
# W7-01b：转化清单多目标（合成候选，1..max；**不含方天专属规则**）
# ---------------------------------------------------------------------------
#
# 方天的候选生成在 W7-02，本票只证明"清单 / 确认能表达 1..max"：多目标候选是**手写**的
# ``UseCard(transformed_card(来源), targets=(…))``（合成局面），不是任何技能的产物。
# 目标步的来源作用域 / 固定窗口收窄 / 完整目标集确认都在这份合成候选上钉住。


SECOND_RED_DODGE = [
    card_id
    for card_id in _cards("dodge")
    if card_id != RED_DODGE
    and color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
][0]
"""第二张红【闪】：与 ``RED_DODGE`` 同为【龙胆】来源，让清单出现两个来源组。"""


def _synthetic_multi_target_state():
    """赵云手里两张红【闪】+ 三名其它座位（座位 1 / 2 / 3 都是合成目标）。"""
    return (
        build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(RED_DODGE, SECOND_RED_DODGE), hero="zhaoyun"),
                SeatSpec(),
                SeatSpec(),
                SeatSpec(),
            ],
            deck=_deck_excluding(RED_DODGE, SECOND_RED_DODGE),
            turn_seat=0,
        ),
        RED_DODGE,
        SECOND_RED_DODGE,
    )


def _synthetic_multi_target_actions(state, first: int, second: int) -> list[UseCard]:
    """手写 1 / 2 目标组合：两张来源各有**不同**的目标集合（来源作用域可观测）。

    ``first`` 的组合 = ``(1,)`` / ``(1, 2)``；``second`` 的组合 = ``(3,)`` / ``(1, 3)``
    ——两个来源组的目标池不同，清单因此必须逐行分列，不能合并成一份。
    """
    actions: list[UseCard] = []
    for source, combos in ((first, ((1,), (1, 2))), (second, ((3,), (1, 3)))):
        card = transformed_card(state, (source,), "slash")
        actions.extend(
            UseCard(card=card, targets=combo, answers_seq=1) for combo in combos
        )
    return actions


def _borrow_like_multi_target_actions(state, source: int) -> list[UseCard]:
    """借刀窗口形状的合成候选：Bn = 2，组合恒含 2（``(2,)`` / ``(2,1)`` / ``(2,1,3)``）。"""
    card = transformed_card(state, (source,), "slash")
    return [
        UseCard(card=card, targets=combo, answers_seq=1)
        for combo in ((2,), (2, 1), (2, 1, 3))
    ]


def test_a_synthetic_multi_target_manifest_lists_every_target_per_source():
    """目标步 = 1..max；每行 options = 该来源组**全部组合的全部目标**（按候选顺序去重）。

    旧实现把每个组合压成首目标（``targets[0]``），额外目标在清单里永远点不到；W7-01b 起
    目标步表达"最多可选几个"，来源作用域仍由 ``when``（来源答案）分列。
    """
    state, first, second = _synthetic_multi_target_state()
    actions = _synthetic_multi_target_actions(state, first, second)

    steps = _manifest(state, actions, ZHAOYUN_SLASH)

    assert [step.name for step in steps] == ["card", "seat"]
    assert [step.order for step in steps] == [0, 1]
    source = steps[0]
    assert source.min == source.max == 1, "一张【闪】当【杀】"
    assert tuple(source.pool[0].options) == (str(first), str(second))
    target = steps[1]
    assert (target.min, target.max) == (1, 2), "1..最长组合目标数"
    rows = {row.when: row.options for row in target.pool}
    assert rows == {
        (str(first),): ("seat:1", "seat:2"),
        (str(second),): ("seat:3", "seat:1"),
    }, f"逐来源组的全部目标（不再只取首目标）：{rows}"


def test_a_synthetic_multi_target_manifest_keeps_the_fixed_window_shape_when_targets_are_unique():
    """``fixed_target=True`` 只在目标**完全唯一**时省掉目标步（既有响应 / 求桃形状）。

    唯一的单目标 ⇒ 清单只有来源步（W5 借刀 / 【万箭】/ 求桃一字不改）；
    出现多目标组合 ⇒ 仍下发目标步（W7-01b 的借刀窗口多目标）。
    """
    state, first, _second = _synthetic_multi_target_state()
    card = transformed_card(state, (first,), "slash")

    unique = [
        UseCard(card=card, targets=(2,), answers_seq=1),
        UseCard(card=card, targets=(2,), answers_seq=1),
    ]
    unique_steps = _manifest(state, unique, ZHAOYUN_SLASH, fixed_target=True)
    assert [step.name for step in unique_steps] == ["card"], (
        "目标无信息量（同一个单目标）⇒ 不下发目标步"
    )

    multi = _borrow_like_multi_target_actions(state, first)
    multi_steps = _manifest(state, multi, ZHAOYUN_SLASH, fixed_target=True)
    assert [step.name for step in multi_steps] == ["card", "seat"], (
        "多目标候选在固定目标窗口里仍下发目标步"
    )
    target = multi_steps[1]
    assert (target.min, target.max) == (1, 3)
    assert tuple(target.pool[0].options) == ("seat:2", "seat:1", "seat:3"), (
        "候选恒含 Bn ⇒ 逐来源组的目标并集首项就是 Bn（借刀指定的目标）"
    )


def test_a_synthetic_multi_target_confirmation_resolves_by_the_full_target_set():
    """确认按**完整目标集**（顺序无关）：1 个 / 2 个 / 3 个目标各解析成那条 ``UseCard``。"""
    from sgs.skills import resolve_conversion_confirmation

    state, first, _second = _synthetic_multi_target_state()
    actions = _borrow_like_multi_target_actions(state, first)
    steps = _manifest(state, actions, ZHAOYUN_SLASH, fixed_target=True)

    def resolve(choices):
        return resolve_conversion_confirmation(
            state, 0, ZHAOYUN_SLASH, (first,), choices, actions, steps=steps
        )

    single = resolve(("seat:2",))
    assert isinstance(single, UseCard) and single.targets == (2,)
    assert single.card.virtual_sources == (first,)

    pair = resolve(("seat:1", "seat:2"))  # 点击顺序与候选目标次序相反
    assert isinstance(pair, UseCard) and pair.targets == (2, 1)

    triple = resolve(("seat:3", "seat:2", "seat:1"))
    assert isinstance(triple, UseCard), triple
    assert triple.targets == (2, 1, 3), "顺序无关：解析成候选池里那条 3 目标使用"
    assert triple.card.virtual_sources == (first,)


@pytest.mark.parametrize(
    ("choices", "why"),
    [
        (("seat:2", "seat:1", "seat:2"), "重复选择（按集合看恰是 2 目标组合）"),
        (("seat:1", "seat:3"), "不含 Bn 的组合不在候选池里"),
        (("seat:2", "seat:1", "seat:3", "seat:0"), "多一个目标"),
        (("seat:2", "seat:2"), "只剩重复的 Bn"),
    ],
)
def test_a_synthetic_multi_target_confirmation_rejects_non_candidate_target_sets(
    choices, why
):
    """缺 / 多 / 重复 / 不含 Bn 均拒，首个失败原因是**目标不合法**（不改任何状态）。"""
    from sgs.skills import resolve_conversion_confirmation

    state, first, _second = _synthetic_multi_target_state()
    actions = _borrow_like_multi_target_actions(state, first)
    steps = _manifest(state, actions, ZHAOYUN_SLASH, fixed_target=True)

    resolved = resolve_conversion_confirmation(
        state, 0, ZHAOYUN_SLASH, (first,), choices, actions, steps=steps
    )

    assert isinstance(resolved, str), f"{why}：不该被接受（{resolved!r}）"
    assert "目标不合法" in resolved, why


def test_a_synthetic_multi_target_confirmation_rejects_a_choice_outside_the_manifest():
    """清单外的选项文本仍是第三档失败原因（错误分档不变）。"""
    from sgs.skills import resolve_conversion_confirmation

    state, first, _second = _synthetic_multi_target_state()
    actions = _borrow_like_multi_target_actions(state, first)
    steps = _manifest(state, actions, ZHAOYUN_SLASH, fixed_target=True)

    resolved = resolve_conversion_confirmation(
        state, 0, ZHAOYUN_SLASH, (first,), ("card:1",), actions, steps=steps
    )

    assert isinstance(resolved, str) and "清单外的选项" in resolved


def test_a_synthetic_multi_target_engine_confirmation_uses_the_full_targets():
    """引擎确认路径：注入合成多目标候选 + 清单 ⇒ 落账的正是解析出的 2 目标 ``UseCard``。

    不新建引擎入口：只把本次询问的 ``transform_actions`` / ``transform_preactivations``
    换成合成的那一份（与 W7-02 的候选生成无关），走的就是 ``Engine.submit`` 的转化确认路。
    """
    state, first, _second = _synthetic_multi_target_state()
    engine = _parked_at(
        state,
        QueryKind.PLAY_PHASE,
        participants=[Scripted(), Scripted(), Scripted(), Scripted()],
    )
    query = engine.pending
    actions = _borrow_like_multi_target_actions(state, first)
    steps = _manifest(state, actions, ZHAOYUN_SLASH, fixed_target=True)
    engine.pending = query.model_copy(
        update={
            "transform_actions": tuple(actions),
            "transform_preactivations": {ZHAOYUN_SLASH: steps},
        }
    )

    engine.submit(
        ActivateSkill(
            skill_id=ZHAOYUN_SLASH,
            picks=(first,),
            choices=("seat:1", "seat:2"),
            answers_seq=query.seq,
        )
    )

    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    resolved = accepted.action
    assert isinstance(resolved, UseCard)
    assert resolved.card.virtual_sources == (first,)
    assert resolved.targets == (2, 1), "按完整目标集解析成候选池里那条 2 目标使用"
    _drive_until_pending(engine)


# ---------------------------------------------------------------------------
# W7-01：方天的"最后一张手牌"谓词（真 / 转化共用一份）
# ---------------------------------------------------------------------------
#
# 转化【杀】的候选接线归 W7-02；本票只测**谓词层**——用公开的 ``apply_modifiers`` +
# ``ModifierContext`` + ``transformed_card`` 构造合成局面，**不生成**转化【杀】的候选
# （W7 spec §3.2 / §3.7）。三条 false 分支的**可达性不同**（W7-03 / H2）：【武圣】的装备区
# 红牌是自然局面（如已装备的【赤兔】），谓词 false 分支可达；只有**丈八两来源 + 方天**
# （同抢武器槽）与**龙胆装备区【闪】**（【闪】不可能合法进装备区）不可达自然局，故后两者
# 只在这里的合成局面里钉住。

HALBERD = _cards("halberd")[0]


def _halberd_target_count(state, card, *, seat: int = 0) -> int:
    """方天在 ``TARGET_COUNT`` 链上给这张牌算出的最大目标数（从额定 1 起算）。"""
    return apply_modifiers(
        ModifierPoint.TARGET_COUNT,
        state,
        ModifierContext(subject=seat, card=card),
        1,
    )


def test_the_last_hand_card_predicate_accepts_a_single_source_in_hand():
    """单源转化【杀】的来源在手里且是**最后一张手牌** ⇒ 方天 +2（1 → 3）。

    同一份谓词的真牌分支（`hand == (card_id,)`）也在这里复核：真 / 转化不该各写一份。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,), equip=(HALBERD,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(RED_PEACH, HALBERD),
        turn_seat=0,
    )
    transformed = transformed_card(state, (RED_PEACH,), "slash")

    assert _halberd_target_count(state, transformed) == 3, "单源、在手、最后一张 ⇒ +2"

    last_real = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(HALBERD,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(SLASH, HALBERD),
        turn_seat=0,
    )
    assert _halberd_target_count(last_real, last_real.card(SLASH)) == 3, (
        "真【杀】是最后一张手牌 ⇒ 同一份谓词返回真"
    )


def test_the_last_hand_card_predicate_needs_the_source_to_be_the_only_card():
    """来源在手里但**还剩别的牌** ⇒ 不是最后一张手牌，方天不追加。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH, BLACK_SLASH), equip=(HALBERD,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, BLACK_SLASH, HALBERD),
        turn_seat=0,
    )
    transformed = transformed_card(state, (RED_PEACH,), "slash")

    assert _halberd_target_count(state, transformed) == 1


def test_the_last_hand_card_predicate_rejects_a_multi_source_transformed_slash():
    """【丈八蛇矛】两张手牌转化的【杀】：多来源 ⇒ 不满足"来源恰好一张"，方天不追加。

    自然局不可达（丈八与方天抢武器槽），故只用合成局面钉住谓词的 false 分支。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH,), equip=(HALBERD,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, RED_DODGE, HALBERD),
        turn_seat=0,
    )
    multi = transformed_card(state, (RED_PEACH, RED_DODGE), "slash")

    assert len(multi.virtual_sources) == 2
    assert _halberd_target_count(state, multi) == 1


def test_the_last_hand_card_predicate_rejects_a_source_outside_the_hand():
    """来源在**装备区**（武圣红牌 / 龙胆装备区【闪】）⇒ 手牌里没有它，方天不追加。

    **武圣的装备区红牌是自然局面**（W7-03 / H2：如已装备的【赤兔】；W7-02 的
    ``test_the_halberd_does_not_widen_a_wusheng_slash_from_the_equipment_zone`` 建在同一种
    局面上）；龙胆的装备区【闪】则不可达自然局（【闪】不可能合法进入装备区），只能合成。
    谓词只认"手牌恰是那一张来源"，区域不是手牌 ⇒ false。
    """
    filler = BLACK_SLASH
    chitu = _cards("chitu")[0]
    with_wusheng_equip = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(filler,), equip=(HALBERD, chitu)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(filler, chitu, HALBERD),
        turn_seat=0,
    )
    from_equip = transformed_card(with_wusheng_equip, (chitu,), "slash")
    assert _halberd_target_count(with_wusheng_equip, from_equip) == 1

    with_longdan_equip = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(), equip=(HALBERD, RED_DODGE)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_DODGE, HALBERD),
        turn_seat=0,
    )
    dodge_from_equip = transformed_card(with_longdan_equip, (RED_DODGE,), "slash")
    assert _halberd_target_count(with_longdan_equip, dodge_from_equip) == 1

    # 对照：同一张来源**在手里**且是最后一张 ⇒ +2（证明上面两条的 false 来自区域，不是别的）
    in_hand = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(RED_DODGE, HALBERD),
        turn_seat=0,
    )
    assert _halberd_target_count(in_hand, transformed_card(in_hand, (RED_DODGE,), "slash")) == 3


def test_the_last_hand_card_predicate_needs_the_halberd():
    """没有【方天画戟】就没有这条修饰器：同一张"最后一张手牌"转化【杀】仍是 1。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(RED_PEACH,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(RED_PEACH),
        turn_seat=0,
    )

    assert _halberd_target_count(state, transformed_card(state, (RED_PEACH,), "slash")) == 1


# ---------------------------------------------------------------------------
# W7-02：方天 × 转化【杀】（出牌阶段 + 借刀窗口）
# ---------------------------------------------------------------------------
#
# 读点 = `virtual.py:virtual_card_use`：与出牌阶段真牌（`_play_phase`）和借刀 USE 趟
# （`_respond`）读**同一份** `TARGET_COUNT` 链、用**同一个** `target_combos`。方天的
# "最后一张手牌"谓词是 W7-01 的同一份（单源 / 实体 / 在手 / `hand == (source,)`），
# 故这里只验证"虚拟牌被送进那条链"与"清单 / 确认 / 结算"三面。
#
# 装备区来源（武圣把已装备的红牌当【杀】）在自然局可达；**丈八两来源 + 方天**（同抢武器槽）
# 与**龙胆装备区【闪】**（【闪】不可能合法进装备区）自然局不可达，只在合成局面里钉 false
# 分支（W7 spec §0 第 9 条 / §7）。

BORROWED_SWORD = _cards("borrowed_sword")[0]
CHITU = _cards("chitu")[0]
"""赤兔（红桃 5，进攻马）：武圣的**装备区**红色来源，自然局可达。"""


def _combos_by_source(actions) -> dict[tuple[int, ...], set[tuple[int, ...]]]:
    """``{来源组: {目标组合}}``：只收**转化【杀】**的使用候选。"""
    out: dict[tuple[int, ...], set[tuple[int, ...]]] = {}
    for action in actions:
        if (
            isinstance(action, UseCard)
            and action.card.is_virtual
            and action.card.type == "slash"
        ):
            out.setdefault(tuple(action.card.virtual_sources), set()).add(action.targets)
    return out


def _transform_slash_combos(state, seat: int = 0):
    """出牌阶段候选里转化【杀】的全部 ``(来源组, 目标组合)``（`virtual_card_use` 的产物）。"""
    actions = legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
    virtual = [
        action
        for action in actions
        if isinstance(action, UseCard)
        and action.card.is_virtual
        and action.card.type == "slash"
    ]
    return _combos_by_source(virtual), virtual


def _transform_borrow_actions(state, holder: int, bn: int):
    """持刀者窗口里**转化【杀】**的候选（借刀的固定目标是 ``bn``）。"""
    actions = legal_actions_for(
        state,
        holder,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BORROWED_SWORD,
            required="slash",
            mode=RespondMode.USE,
            use_targets=(bn,),
        ),
    )
    return [
        action
        for action in actions
        if isinstance(action, UseCard)
        and action.card.is_virtual
        and action.card.type == "slash"
    ]


def test_the_halberd_widens_a_wusheng_transformed_slash_to_three_targets():
    """武圣：手牌红牌当【杀】、它是**最后一张手牌** ⇒ 1..3 目标（与真【杀】同一条链）。

    同一局面里**方天画戟自己**（方块 = 红色，装备区）也是武圣的合法来源——它在装备区、
    不是"最后一张手牌"，故只产出单目标。两条判据因此在一个**自然局面**里并排可观测：
    手牌来源 1..3，装备区来源 1。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH,), equip=(HALBERD,), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, HALBERD),
        turn_seat=0,
    )

    combos, actions = _transform_slash_combos(state)

    assert combos[(RED_PEACH,)] == {
        (1,),
        (2,),
        (3,),
        (1, 2),
        (1, 3),
        (2, 3),
        (1, 2, 3),
    }, "最后一张手牌的转化来源 ⇒ 额定 1 放宽到 1..3"
    assert combos[(HALBERD,)] == {(1,), (2,), (3,)}, (
        "装备区来源（方天自己也是红色）不是最后一张手牌 ⇒ 只有单目标"
    )
    assert all(action.designations == () for action in actions), (
        "转化【杀】的 UseCard 不带附带指定：目标全在 targets 里"
    )

    steps = _manifest(state, _virtual_actions(state), GUANYU)
    assert [step.name for step in steps] == ["card", "seat"]
    target = steps[1]
    assert (target.min, target.max) == (1, 3), "清单目标步 = 1..3（W7-01b 的 1..max）"
    # W7-03 / D4：目标步**逐来源**一行，行上带该来源的上限——两行的 options 一模一样
    # （可达目标与来源无关），故 `options` 说不出"该来源最多几个"，上限必须是单独一格。
    rows = {row.when: row for row in target.pool}
    assert set(rows) == {(str(HALBERD),), (str(RED_PEACH),)}
    assert rows[(str(HALBERD),)].max == 1, "装备区来源（方天自己也是红色）只允许 1 个目标"
    assert rows[(str(RED_PEACH),)].max == 3, "最后一张手牌来源放宽到 1..3"
    assert all(
        set(row.options) == {"seat:1", "seat:2", "seat:3"} for row in target.pool
    ), "两行的可达目标相同 ⇒ 逐来源上限不能从 options 推出"
    assert {str(RED_PEACH), str(HALBERD)} <= set(steps[0].pool[0].options), (
        "来源步同时列出两张红牌：手牌的最后一张 + 装备区的方天"
    )


def test_the_manifest_caps_each_source_at_its_own_target_count():
    """W7-03（D4）：目标步**逐来源一行**，行上带该来源的真实上限（界面据此收住可选数）。

    自然局（关羽 + 方天 + 唯一红手牌）：最后一张手牌的来源 1..3、装备区来源（方天自己
    也是红色）1；两行的 ``options`` 完全相同（可达目标与来源无关）⇒ "该来源最多几个"只能
    由行上的 ``max`` 回答。这是界面修复（D4）的数据前提：全局 ``Step.max`` = 3，选错来源
    时按全局值填满就会拼出引擎会拒绝的确认。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH,), equip=(HALBERD,), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, HALBERD),
        turn_seat=0,
    )

    steps = _manifest(state, _virtual_actions(state), GUANYU)

    target = steps[1]
    assert target.max == 3, "步骤上的 max 仍是全局最大（1..3）"
    rows = {row.when: row for row in target.pool}
    assert set(rows) == {(str(HALBERD),), (str(RED_PEACH),)}, (
        f"逐来源各一行（不因 options 相同而合并）：{list(rows)}"
    )
    assert rows[(str(HALBERD),)].max == 1, "装备区来源：上限 1"
    assert rows[(str(RED_PEACH),)].max == 3, "最后一张手牌来源：上限 3"
    assert all(
        set(row.options) == {"seat:1", "seat:2", "seat:3"} for row in target.pool
    ), "两行可达目标相同 ⇒ 上限不能从 options 推出，必须由行上的 max 带出"


def test_the_halberd_widens_a_longdan_transformed_slash_to_three_targets():
    """龙胆：手牌【闪】当【杀】、它是最后一张手牌 ⇒ 1..3（只有这一个转化来源）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_DODGE, HALBERD),
        turn_seat=0,
    )

    combos, _actions = _transform_slash_combos(state)

    assert combos == {
        (RED_DODGE,): {
            (1,),
            (2,),
            (3,),
            (1, 2),
            (1, 3),
            (2, 3),
            (1, 2, 3),
        }
    }
    steps = _manifest(state, _virtual_actions(state), ZHAOYUN_SLASH)
    assert (steps[1].min, steps[1].max) == (1, 3)


def test_the_halberd_does_not_widen_a_transformed_slash_when_another_hand_card_remains():
    """来源在手里但还有别的牌 ⇒ 不是最后一张手牌：转化【杀】只有单目标（来源同源）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH, BLACK_SLASH), equip=(HALBERD,), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, BLACK_SLASH, HALBERD),
        turn_seat=0,
    )

    combos, _actions = _transform_slash_combos(state)

    assert combos[(RED_PEACH,)] == {(1,), (2,), (3,)}
    steps = _manifest(state, _virtual_actions(state), GUANYU)
    assert (steps[1].min, steps[1].max) == (1, 1), "不是最后一张 ⇒ 清单也只有单目标"


def test_the_halberd_does_not_widen_a_wusheng_slash_from_the_equipment_zone():
    """武圣用**装备区**的红牌（赤兔 / 方天）当【杀】：来源不在手牌 ⇒ 不满足最后一张手牌。

    自然局面（手牌为空、两件红装备）；同时用谓词层复核——候选与谓词读的是同一份判据。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(), equip=(HALBERD, CHITU), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(HALBERD, CHITU),
        turn_seat=0,
    )

    combos, _actions = _transform_slash_combos(state)

    assert set(combos) == {(HALBERD,), (CHITU,)}, "两张红色装备都是武圣的来源"
    assert all(targets == {(1,), (2,), (3,)} for targets in combos.values()), (
        "装备区来源 ⇒ 不追加目标（1 目标）"
    )
    assert _halberd_target_count(state, transformed_card(state, (CHITU,), "slash")) == 1
    steps = _manifest(state, _virtual_actions(state), GUANYU)
    assert (steps[1].min, steps[1].max) == (1, 1)


def test_the_halberd_does_not_widen_a_multi_source_transformed_slash():
    """【丈八蛇矛】两张手牌转化的【杀】：多来源 ⇒ 不满足"来源恰好一张"，方天不追加。

    **合成局面**（自然局不可达：丈八与方天同抢武器槽，W7 spec §0 第 9 条）。谓词与
    候选两处都复核，证明"不追加"来自那一条谓词，不是候选生成漏看。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH, RED_DODGE), equip=(SERPENT, HALBERD)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_PEACH, RED_DODGE, SERPENT, HALBERD),
        turn_seat=0,
    )
    multi = transformed_card(state, (RED_PEACH, RED_DODGE), "slash")

    assert len(multi.virtual_sources) == 2
    assert _halberd_target_count(state, multi) == 1
    combos, _actions = _transform_slash_combos(state)
    assert combos[tuple(sorted((RED_PEACH, RED_DODGE)))] == {(1,), (2,), (3,)}, (
        "多来源只有单目标"
    )


def test_the_halberd_does_not_widen_a_longdan_slash_from_the_equipment_zone():
    """【龙胆】的【闪】若在**装备区**（合成局面，自然局不可达）⇒ 方天同样不追加。

    W7-01 已做谓词级；这里复核 **`virtual_card_use` 的端到端**（spec §5.2 的"02 复核
    端到端不追加"）——候选生成读的是同一份谓词。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(), equip=(HALBERD, RED_DODGE), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(RED_DODGE, HALBERD),
        turn_seat=0,
    )
    from_equip = transformed_card(state, (RED_DODGE,), "slash")

    assert _halberd_target_count(state, from_equip) == 1
    combos, _actions = _transform_slash_combos(state)
    assert combos == {(RED_DODGE,): {(1,), (2,), (3,)}}


def test_a_halberd_holder_can_add_zero_one_or_two_targets_to_a_transformed_slash():
    """借刀窗口：龙胆转化【杀】+ 方天 + 最后一张手牌 ⇒ ``(Bn,)`` + 0..2 个其它合法目标。

    三档用**池大小**区分（与 W7-01 真【杀】用例同形）：2 人局 0 个额外 / 空城把座位 3
    滤掉 ⇒ 1 个 / 4 人全池 ⇒ 2 个。每条候选恒含 Bn，且 `designations` 为空。
    """
    two_player = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BORROWED_SWORD,)),
            SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,), hero="zhaoyun"),
        ],
        deck=_deck_excluding(BORROWED_SWORD, RED_DODGE, HALBERD),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    assert _combos_by_source(_transform_borrow_actions(two_player, 1, 0)) == {
        (RED_DODGE,): {(0,)}
    }, "只有 Bn：0 个额外目标"

    one_extra = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BORROWED_SWORD,)),
            SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(hero="zhugeliang"),
        ],
        deck=_deck_excluding(BORROWED_SWORD, RED_DODGE, HALBERD),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    assert _combos_by_source(_transform_borrow_actions(one_extra, 1, 0)) == {
        (RED_DODGE,): {(0,), (0, 2)}
    }, "空城把座位 3 从池里去掉 ⇒ 只能加 1 个额外目标"

    two_extra = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BORROWED_SWORD,)),
            SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(BORROWED_SWORD, RED_DODGE, HALBERD),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    actions = _transform_borrow_actions(two_extra, 1, 0)
    combos = _combos_by_source(actions)
    assert combos == {(RED_DODGE,): {(0,), (0, 2), (0, 3), (0, 2, 3)}}
    assert all(0 in action.targets for action in actions), "每条候选都恒含 Bn"
    assert all(action.designations == () for action in actions), (
        "持刀者【杀】的 designations 为空：Bn 与额外目标都在 targets 里"
    )


def test_the_borrowed_transform_manifest_puts_bn_first_and_lists_one_to_three_targets():
    """借刀窗口的转化清单：来源步 + 目标步 ``1..3``，首项 = Bn（候选排序的自然结果）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BORROWED_SWORD,)),
            SeatSpec(hand=(RED_DODGE,), equip=(HALBERD,), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(BORROWED_SWORD, RED_DODGE, HALBERD),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    actions = _transform_borrow_actions(state, 1, 0)

    steps = _manifest(state, actions, ZHAOYUN_SLASH, fixed_target=True, seat=1)

    assert [step.name for step in steps] == ["card", "seat"]
    target = steps[1]
    assert (target.min, target.max) == (1, 3)
    assert target.pool[0].options[0] == "seat:0", "首项 = Bn（候选排序的自然结果）"
    assert set(target.pool[0].options) == {"seat:0", "seat:2", "seat:3"}


class _UseTheDrawnEquipment:
    """脚本玩家：按次序用掉指定的装备牌（把出牌阶段多余的手牌清空），其余走最小应答。"""

    def __init__(self, card_ids: tuple[int, ...]) -> None:
        self._pending = list(card_ids)
        self._fallback = Scripted()

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and self._pending:
            wanted = self._pending[0]
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == wanted:
                    self._pending.pop(0)
                    return action
        return self._fallback.respond(query)


def _play_phase_with_the_last_card(state, drawn: tuple[int, ...], *, seat: int = 0):
    """跑到"手牌恰剩最后一张（转化源）"的出牌阶段询问。

    摸牌阶段会摸两张（``drawn`` 指定为装备牌，由 :class:`_UseTheDrawnEquipment` 用掉），
    故 ``state`` 的牌堆必须以它们开头；其余座位走最小应答。
    """
    players = [_UseTheDrawnEquipment(drawn)] + [Scripted() for _ in range(3)]

    def stop(running) -> bool:
        pending = running.pending
        if pending is None or pending.kind != QueryKind.PLAY_PHASE:
            return False
        steps = pending.transform_preactivations.get(GUANYU, ())
        return any(step.name == "seat" and step.max == 3 for step in steps)

    return _run(state, players, stop)


def test_a_wusheng_three_target_confirmation_hits_three_seats():
    """出牌阶段：清单确认 3 目标组合 ⇒ 三名目标各结算一次【杀】（引擎真结算）。

    摸牌摸到两件装备（赤兔 / 八卦阵），先由脚本玩家装上，手牌只剩最后一张红牌；此时
    方天的 1..3 才成立。确认的目标顺序与点击顺序无关，落账的 ``UseCard.targets`` 取自
    候选（按候选目标次序）。
    """
    rest = tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in {RED_PEACH, HALBERD, CHITU, _cards("eight_diagrams")[0]}
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(RED_PEACH,), equip=(HALBERD,), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=(CHITU, _cards("eight_diagrams")[0], *rest),
        turn_seat=0,
    )
    engine = _play_phase_with_the_last_card(state, (CHITU, _cards("eight_diagrams")[0]))
    query = engine.pending
    assert query is not None and query.kind == QueryKind.PLAY_PHASE
    assert engine.state.player(0).hand == [RED_PEACH], "前提：转化源就是最后一张手牌"

    confirm = ActivateSkill(
        skill_id=GUANYU,
        picks=(RED_PEACH,),
        choices=("seat:2", "seat:1", "seat:3"),
        answers_seq=query.seq,
    )
    engine.submit(confirm)
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    resolved = accepted.action
    assert isinstance(resolved, UseCard)
    assert resolved.card.type == "slash" and resolved.card.virtual_sources == (RED_PEACH,)
    assert resolved.targets == (1, 2, 3), "三名目标一次使用（1..3 的 3 目标组合）"

    run_until(
        engine,
        [Scripted() for _ in range(4)],
        lambda running: all(running.state.player(seat).hp == 3 for seat in (1, 2, 3)),
    )
    assert [player.hp for player in engine.state.players] == [4, 3, 3, 3], (
        "三名目标各结算一次【杀】的 1 点伤害"
    )
    assert engine.state.player(0).card_use_counts.get("slash") == 1
    assert RED_PEACH in engine.state.discard, "来源红牌走正常使用路径进弃牌堆"


def _borrowed_transform_state() -> object:
    """4 人局：座位 0 借刀、座位 1 赵云（手牌仅一张红【闪】+ 方天）——持刀者窗口的自然局面。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BORROWED_SWORD,), identity=Identity.LORD),
            SeatSpec(
                hand=(RED_DODGE,),
                equip=(HALBERD,),
                hero="zhaoyun",
                identity=Identity.REBEL,
            ),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
        ],
        deck=_deck_excluding(BORROWED_SWORD, RED_DODGE, HALBERD),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


def test_a_transformed_borrowed_confirmation_without_bn_is_rejected():
    """借刀窗口的转化确认不含 Bn ⇒ "目标不合法"、零状态改动（引擎的成员判定是最终裁判）。"""
    state = _borrowed_transform_state()
    engine, _play = _borrowed_window(state, designation=0, seats=4)
    window = engine.pending
    assert window is not None and window.cause is WindowCause.BORROWED_SWORD
    baseline = json.dumps(engine.state.model_dump(mode="json"), sort_keys=True)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            ActivateSkill(
                skill_id=ZHAOYUN_SLASH,
                picks=(RED_DODGE,),
                choices=("seat:2", "seat:3"),
                answers_seq=window.seq,
            )
        )

    assert "目标不合法" in caught.value.message
    assert json.dumps(engine.state.model_dump(mode="json"), sort_keys=True) == baseline
    assert engine.pending is window
    rejected = engine.records[-1]
    assert rejected.accepted is False and "转化确认失败" in rejected.reason


def test_a_transformed_borrowed_confirmation_resolves_bn_plus_two_extra_targets():
    """借刀窗口：确认 Bn + 2 个额外目标 ⇒ 解析成 3 目标转化【杀】，三名目标各掉 1 点体力。

    主体分列复核：持刀者【杀】的 ``designations == ()``，Bn 与额外目标都在 ``targets``。
    """
    state = _borrowed_transform_state()
    engine, _play = _borrowed_window(state, designation=0, seats=4)
    window = engine.pending
    assert window is not None and window.cause is WindowCause.BORROWED_SWORD

    engine.submit(
        ActivateSkill(
            skill_id=ZHAOYUN_SLASH,
            picks=(RED_DODGE,),
            choices=("seat:0", "seat:2", "seat:3"),
            answers_seq=window.seq,
        )
    )
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    resolved = accepted.action
    assert isinstance(resolved, UseCard)
    assert resolved.targets == (0, 2, 3)
    assert resolved.designations == (), "Bn 与额外目标都在 targets 里"

    run_until(
        engine,
        [Scripted() for _ in range(4)],
        lambda running: all(running.state.player(seat).hp == 3 for seat in (0, 2, 3)),
    )
    assert [player.hp for player in engine.state.players] == [3, 4, 3, 3], (
        "Bn（座位 0）+ 两个额外目标（2 / 3）各结算一次【杀】"
    )
