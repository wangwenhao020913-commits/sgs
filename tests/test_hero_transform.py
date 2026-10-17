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

from typing import Any

import pytest

from sgs.data import Color, color_of_suit
from sgs.effects import legal_actions_for, target_is_effective
from sgs.engine import Engine
from sgs.skills import (
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    VirtualSourceError,
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
    来源 id 取 ``1..size``（合法牌 id 的**非零**前缀）。兼容段（``kind < 2``）逐字保留旧
    算式，故它的 id **只由来源集合决定、与牌堆张数无关**（``card_count`` 只是那里的进制）：
    跨牌堆的撞因此只查**泛化段**（``kind >= 2``）——本票新引入的那一半。
    """
    count = len(PACK.deck.cards)
    global_seen: set[int] = set()
    for card_count in (2, 53, count):
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


def _play_transformed(seat: int, card_type: str, *, additional: tuple[int, ...] = ()):
    """在 ``PLAY_PHASE`` 挑该牌名的转化牌使用；其余询问退到最小应答。"""

    def chooser(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id < 0
                and action.card.type == card_type,
            )
        return None

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
        [_play_transformed(0, "slash"), Scripted(lambda q: None)],
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
    """座位 0：把手里的方块牌当【乐不思蜀】用掉；其余一律结束阶段 / 放弃。"""

    def __init__(self) -> None:
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and not self.used:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.type == "indulgence"
                    and action.card.is_virtual
                ):
                    self.used = True
                    return action
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
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id < 0
                and action.card.type == "peach",
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
    offered = [
        action
        for action in rescue[0].legal_actions
        if isinstance(action, UseCard)
        and action.card.is_virtual
        and action.card.type == "peach"
    ]
    assert [action.card.virtual_sources for action in offered] == [(RED_DODGE,)], (
        f"求桃窗口里急救的候选 = 这张红牌转化的【桃】：{rescue[0].legal_actions}"
    )
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


def _manifest(state, actions, skill_id: str, *, fixed_target: bool = False):
    from sgs.skills import conversion_preactivations

    return conversion_preactivations(
        state, 0, actions, fixed_target=fixed_target
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
