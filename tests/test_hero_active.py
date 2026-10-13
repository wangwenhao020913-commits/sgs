"""A 族：出牌阶段**主动技候选**（票 04）。

落点与票 04 §验收一致，全部走**外部缝**：

- ``legal_actions_for(PLAY_PHASE)`` 的候选集合（形状 / 载荷 / 付不起 / 用过后不再进池）；
- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的记录流与终局状态（效果 / 伤害来源 /
  牌移动 / 回复夹上限）；
- 组合上限闸的**候选形态**与**顺序降级**（``SelectionSpec(sequential=True)``）。

不测 ``ACTIVATIONS`` / 候选回调的内部结构；行为只经候选、询问、delta 流观测。
【苦肉】不在 16 名名单（票 12 的 46 条计数不含它），故"裸发动 + 失去体力"用**注入的
假定义**验形状（``tests/test_hero_skills.py`` 的先例）。
"""

from __future__ import annotations

from functools import partial
from itertools import combinations
from types import MappingProxyType

import pytest

from sgs.data import Gender, load_content_pack
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierPoint,
    check_targets,
    legal_actions_for,
)
from sgs.engine import Engine
from sgs.resolution import Frame
from sgs.skills import (
    CATALOG,
    CANDIDATE_CAP,
    SkillDef,
    SkillType,
    activate_skill,
    pure_virtual_card,
    reset_phase_skill_state,
)
from sgs.skills import active as active_module
from sgs.skills import catalog as catalog_module
from sgs.state import (
    ActivateSkill,
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    SelectOption,
    Zone,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_turns, run_until

PACK = load_content_pack(deck_profile="standard_108")

ZHIHENG = "hero.sunquan.zhiheng"
RENDE = "hero.liubei.rende"
FANJIAN = "hero.zhouyu.fanjian"
QINGNANG = "hero.huatuo.qingnang"
LIJIAN = "hero.diaochan.lijian"
JIEYIN = "hero.sunshangxiang.jieyin"
KUROU = "hero.huanggai.kurou"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _hearts(count: int) -> tuple[int, ...]:
    return tuple(
        card_id for card_id, card in enumerate(PACK.deck.cards) if card.suit == "heart"
    )[:count]


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _offers(actions, skill_id: str) -> list[ActivateSkill]:
    return [
        action
        for action in actions
        if isinstance(action, ActivateSkill) and action.skill_id == skill_id
    ]


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _decline(query):
    """响应窗口一律放弃：随机摸到的【无懈可击】/【闪】不会把被测链改道。"""
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE):
        return _find(query, lambda action: isinstance(action, Pass))
    return None


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _run(state, players, stop, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _skill_frame_popped(engine) -> bool:
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.SKILL
        for record in engine.records
    )


def _play_query_count(engine, skill_id: str) -> int:
    """记录流里出现过该技能候选的 PLAY_PHASE 询问数。"""
    total = 0
    for record in engine.records:
        if record.kind != "query" or record.query.kind != QueryKind.PLAY_PHASE.value:
            continue
        if any(
            isinstance(action, ActivateSkill) and action.skill_id == skill_id
            for action in record.query.legal_actions
        ):
            total += 1
    return total


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# 制衡（孙权）：弃任意张（手牌 + 装备区）→ 摸等量；一维 2ⁿ，不设闸
# ---------------------------------------------------------------------------


def test_zhiheng_offers_every_nonempty_subset_of_hand_and_equipment():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    equip = _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(equip,), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(first, second, equip),
        turn_seat=0,
    )

    picks = {action.picks for action in _offers(_play_candidates(state), ZHIHENG)}

    assert picks == {
        (first,),
        (second,),
        (equip,),
        (first, second),
        (first, equip),
        (second, equip),
        (first, second, equip),
    }, "全组合 2ⁿ（跨手牌与装备区）且**弃 0 张不入池**"


def test_zhiheng_is_not_offered_without_any_card_to_discard():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    assert not _offers(_play_candidates(state), ZHIHENG), "付不起的组合不进池"


def test_zhiheng_discards_by_source_zone_and_draws_the_same_count():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    equip = _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(equip,), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(first, second, equip),
        turn_seat=0,
    )

    def zhiheng(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == ZHIHENG
                and set(action.picks) == {first, equip},
            )
        return _decline(query)

    engine = _run(state, [Scripted(zhiheng), Scripted(_decline)], _skill_frame_popped)

    assert first in engine.state.discard and equip in engine.state.discard
    assert second in engine.state.player(0).hand, "没被选中的牌留在手里"
    assert not engine.state.player(0).equip, "装备区那张按来源区弃掉"
    assert ("hand", "discard", "DISCARD") in _moves_of(engine, first)
    assert ("equip", "discard", "DISCARD") in _moves_of(engine, equip)
    # 起始 2 张 + 摸牌阶段 2 张 − 制衡弃 1 张手牌 + 制衡摸 2 张 = 5。
    assert len(engine.state.player(0).hand) == 5, "摸牌数与弃置数相等（弃 2 摸 2）"


def test_a_used_active_skill_disappears_from_the_next_play_phase_query():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    def zhiheng(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == ZHIHENG,
            )
        return _decline(query)

    engine = _run(state, [Scripted(zhiheng), Scripted(_decline)], _skill_frame_popped)

    assert _play_query_count(engine, ZHIHENG) == 1, (
        "第一次询问里有它；发动后重新询问里不再有它（用完即不再进池）"
    )


def test_reset_phase_skill_state_clears_the_counters_at_play_phase_start():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    state.skill_state["0:hero.sunquan.zhiheng:HERO_CARD:hero.sunquan.zhiheng"] = 1

    run_turns(Engine.from_state(state, seed=1), [RecordingPlayer(), RecordingPlayer()], turns=1)

    assert state.skill_state == {}, "本出牌阶段开始时复位（票 04 §要做什么 2）"


def test_reset_phase_skill_state_is_a_public_reset_point():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    state.skill_state["0:x:HERO_CARD:x"] = 1

    reset_phase_skill_state(state)

    assert state.skill_state == {}


# ---------------------------------------------------------------------------
# 【苦肉】的形状：裸发动（max_picks == 0）+ 失去体力（LIFE_LOSE_* 不解封）
# ---------------------------------------------------------------------------


def test_a_bare_activation_candidate_and_handler_run_the_shape(monkeypatch):
    """裸发动：候选载荷全空，发动后回调直接跑（本例 = 失去 1 点体力）。"""

    def offers(state, ctx, current):
        if ctx.scope is not CandidateScope.PLAY or ctx.owner != ctx.subject:
            return current
        return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))

    def activate(host, frame):
        seat = frame.data["source"]
        host.apply_hp_change(frame, seat, host.state.player(seat).hp - 1)
        return None

    definition = SkillDef(
        skill_id=KUROU,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, offers),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, KUROU: definition}),
    )
    monkeypatch.setattr(
        active_module,
        "ACTIVATIONS",
        MappingProxyType({**active_module.ACTIVATIONS, KUROU: activate}),
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4, hero="huanggai"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    bare = [
        action
        for action in _offers(_play_candidates(state), KUROU)
        if action.picks == () and action.choices == ()
    ]
    assert bare, "max_picks == 0 ⇒ 裸发动候选（无载荷）"

    def kurou(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == KUROU,
            )
        return _decline(query)

    engine = _run(state, [Scripted(kurou), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(0).hp == 3, "失去体力走 apply_hp 原语"
    assert not any(
        record.kind == "frame" and record.frame_kind is FrameKind.LIFE_LOSE
        for record in engine.records
    ), "LIFE_LOSE_* 不解封"
    assert not any(
        record.kind == "timing" and record.timing.value.startswith("LIFE_LOSE")
        for record in engine.records
    )


def test_activate_skill_fails_closed_for_an_unregistered_id():
    with pytest.raises(NotImplementedError):
        activate_skill(None, Frame(kind=FrameKind.SKILL, data={"skill_id": "no.such.skill"}))


# ---------------------------------------------------------------------------
# 仁德（刘备）：给任意张手牌；本阶段首次给到第 2 张回复 1 点；两维 ⇒ 闸 + 顺序降级
# ---------------------------------------------------------------------------


def test_rende_offers_a_subset_of_the_hand_for_each_other_character():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero="liubei"), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    offers = _offers(_play_candidates(state), RENDE)

    assert {(action.picks, action.choices) for action in offers} == {
        ((first,), ("seat:1",)),
        ((second,), ("seat:1",)),
        ((first, second), ("seat:1",)),
        ((first,), ("seat:2",)),
        ((second,), ("seat:2",)),
        ((first, second), ("seat:2",)),
    }, "两维相乘：张数 × 目标；空组合（给 0 张）不进池"


def test_rende_is_not_offered_without_hand_cards_or_without_a_target():
    no_hand = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="liubei"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    no_target = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="liubei")],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    assert not _offers(_play_candidates(no_hand), RENDE)
    assert not _offers(_play_candidates(no_target), RENDE)


def test_rende_recovers_when_two_cards_are_given_for_the_first_time():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hp_max=4, hand=(first, second), hero="liubei"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    def rende(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == RENDE
                and set(action.picks) == {first, second},
            )
        return _decline(query)

    engine = _run(state, [Scripted(rende), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(0).hp == 4, "首次给到第 2 张回复 1 点"
    assert set(engine.state.player(1).hand) == {first, second}, "两张手牌交给目标"
    assert first not in engine.state.player(0).hand


def test_rende_does_not_recover_when_only_one_card_is_given():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hp_max=4, hand=(first, second), hero="liubei"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    def rende(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == RENDE
                and action.picks == (first,),
            )
        return _decline(query)

    engine = _run(state, [Scripted(rende), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(0).hp == 3, "只给 1 张 ⇒ 不回复"


def _small_and_big_rende():
    hand = tuple(_cards("slash") + _cards("dodge"))[:8]
    big = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hp_max=4, hand=hand, hero="liubei"), SeatSpec()],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    small = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hp_max=4, hand=hand[:2], hero="liubei"), SeatSpec()],
        deck=_deck_excluding(*hand[:2]),
        turn_seat=0,
    )
    return big, small


def test_rende_offers_picks_beyond_the_old_cap_so_the_ui_can_commit_in_one_step():
    """**报障 2**：【仁德】不降级——超过旧闸值时仍带 ``picks``。

    前端要"直接多选手牌 + 直接点座位、一次交出去"，所以每一组非空子集 × 目标都必须
    能原样找到一条候选；若降级成"只有目标"，界面就拼不出这一组。
    """
    big, _small = _small_and_big_rende()
    hand = tuple(big.player(0).hand)
    assert (2 ** (len(hand) + 2) - 1) > CANDIDATE_CAP, "这局必须超过旧闸值（否则本用例空转）"

    offers = _offers(_play_candidates(big), RENDE)

    assert {action.picks for action in offers} == {
        subset
        for size in range(1, len(hand) + 1)
        for subset in combinations(hand, size)
    }, "非空子集全在池（不降级），界面才能一步提交"


def test_rende_gives_several_cards_without_an_intermediate_selection():
    """一次发动 = 一次移动：不再有"逐张选牌"的 ``SELECT_CARDS`` 中间询问。"""
    big, _small = _small_and_big_rende()
    hand = tuple(big.player(0).hand)
    chosen = hand[:3]

    def rende(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == RENDE
                and action.picks == chosen,
            )
        return _decline(query)

    engine = _run(big, [Scripted(rende), Scripted(_decline)], _skill_frame_popped)

    assert set(engine.state.player(1).hand) == set(chosen), "三张一次交出"
    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_CARDS.value
    ], "仁德不再走顺序选牌"


def test_rende_has_no_once_per_phase_limit():
    """**报障 2**：规则 976 没有"限一次"——同一出牌阶段可以给第二次。"""
    first, second, third, fourth = (_cards("slash") + _cards("dodge"))[:4]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=2, hp_max=4, hand=(first, second, third, fourth), hero="liubei"), SeatSpec()],
        deck=_deck_excluding(first, second, third, fourth),
        turn_seat=0,
    )
    plan = [(first, second), (third, fourth)]

    def give(query):
        if query.kind == QueryKind.PLAY_PHASE:
            candidates = _offers(query.legal_actions, RENDE)
            for want in list(plan):
                chosen = [action for action in candidates if action.picks == want]
                if chosen:
                    plan.remove(want)
                    return chosen[0]
        return _decline(query)

    def two_skill_frames(engine) -> bool:
        return (
            sum(
                1
                for record in engine.records
                if record.kind == "frame"
                and record.op == "pop"
                and record.frame_kind is FrameKind.SKILL
            )
            >= 2
        )

    engine = _run(state, [Scripted(give), Scripted(_decline)], two_skill_frames)

    assert set(engine.state.player(1).hand) == {first, second, third, fourth}, "两次都给成了"
    assert engine.state.player(0).hp == 3, "第 2 张回复 1 点（2 → 3），第 4 张不再回复"
    assert _play_query_count(engine, RENDE) >= 2, "发动过之后仍回到池里（没有限一次）"


def test_the_still_gated_skills_keep_the_play_query_under_the_cap():
    """闸门仍在：会降级的技能（【结姻】两维相乘）单次询问候选数不超闸值。

    【离间】迁到步骤流水线后（票 01）、【青囊】迁走后（票 02）都不再是样本——它们的候选
    恒为一条，与闸门无关；本票仍留【结姻】当样本（它到票 03 才迁）。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hero="sunshangxiang", gender=Gender.FEMALE),
            *_wounded_males(7),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    pair_count = len(hand) * (len(hand) - 1) // 2
    assert pair_count * 7 > CANDIDATE_CAP, "两维相乘必须过闸（否则本用例空转）"

    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "降级后单次询问候选数不超过闸值"


# ---------------------------------------------------------------------------
# 反间（周瑜）：目标选花色 + 获得并展示你的一张手牌 + 条件伤害
# ---------------------------------------------------------------------------


def _fanjian_plugin(*, suit: str):
    def activator(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == FANJIAN,
            )
        return _decline(query)

    def target(query):
        if query.kind == QueryKind.SELECT_OPTION:
            return _find(
                query,
                lambda action: isinstance(action, SelectOption) and action.choice == suit,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        return _decline(query)

    return activator, target


def _fanjian_state():
    hearts = _hearts(2)
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hearts, hero="zhouyu"), SeatSpec(hp=4, hp_max=4)],
        deck=_deck_excluding(*hearts),
        turn_seat=0,
    ), hearts


def test_fanjian_offers_one_candidate_per_other_character():
    heart = _hearts(1)[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(heart,), hero="zhouyu"), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(heart),
        turn_seat=0,
    )

    assert {action.choices for action in _offers(_play_candidates(state), FANJIAN)} == {
        ("seat:1",),
        ("seat:2",),
    }


def test_fanjian_does_not_need_the_target_to_hold_cards():
    heart = _hearts(1)[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(heart,), hero="zhouyu"), SeatSpec()],
        deck=_deck_excluding(heart),
        turn_seat=0,
    )

    assert _offers(_play_candidates(state), FANJIAN), (
        "展示的是**发动者的**手牌 ⇒ 目标无手牌不是边界（票 04 §验收 4）"
    )


def test_fanjian_a_different_suit_deals_one_damage_from_the_activator():
    state, hearts = _fanjian_state()
    activator, target = _fanjian_plugin(suit="spade")

    engine = _run(state, [Scripted(activator), Scripted(target)], _skill_frame_popped)

    assert engine.state.player(1).hp == 3, "花色不同 ⇒ 目标受到周瑜造成的 1 点伤害"
    assert len(engine.state.player(1).hand) == 1, "目标获得了那一张手牌"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 0, "伤害来源是发动者"
    revealed = [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone.zone.value == "hand"
        and record.to_zone.zone.value == "processing"
        for card in record.cards
    ]
    assert len(revealed) == 1
    paths = [(from_zone, to_zone) for from_zone, to_zone, _reason in _moves_of(engine, revealed[0])]
    assert ("hand", "processing") in paths, "展示 = 先亮进公开的处理区"
    assert ("processing", "hand") in paths, "再从处理区进目标手牌（获得）"


def test_fanjian_a_matching_suit_deals_no_damage():
    state, _hearts_used = _fanjian_state()
    activator, target = _fanjian_plugin(suit="heart")

    engine = _run(state, [Scripted(activator), Scripted(target)], _skill_frame_popped)

    assert engine.state.player(1).hp == 4, "花色相同 ⇒ 不受伤"
    assert len(engine.state.player(1).hand) == 1, "但牌照常被获得"


# ---------------------------------------------------------------------------
# 青囊（华佗）：弃 1 张手牌 → 一名已受伤角色回复 1 点（含自己）
#
# 票 02：形状与【离间】同源 —— 候选恒一条骨架，"弃哪张 / 治谁"由发动后的步骤
# 流水线问（``SELECT_CARDS`` → ``SELECT_OPTION``）。旧的"过闸降级"分支已删除，
# 同一个技能不再有两种交互形状。
# ---------------------------------------------------------------------------


def _qingnang_state(*, target_hp: int = 2):
    """发动者 0（华佗，手牌 1 张【杀】）+ 座位 1（默认已受伤）。"""
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="huatuo"), SeatSpec(hp=target_hp, hp_max=4)],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _qingnang_driver(*, target: int = 1, cancel_at: str | None = None):
    """按**新流程**应答：点技能 → 弃置 1 张手牌 → 选一名已受伤角色。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == QINGNANG,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            if cancel_at == "card":
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            if cancel_at == "seat":
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query,
                lambda action: isinstance(action, SelectOption)
                and action.choice == f"seat:{target}",
            )
        return _decline(query)

    return driver


def _qingnang_option_query(engine):
    return next(
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION.value
        and str(record.query.skill_id).startswith("qingnang:")
    )


def test_qingnang_target_pool_is_the_wounded_characters_including_self():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hp_max=4, hand=(card,), hero="huatuo"),
            SeatSpec(hp=4, hp_max=4),
            SeatSpec(hp=2, hp_max=4),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(_qingnang_driver(cancel_at="seat")), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert _qingnang_option_query(engine).options == ("seat:0", "seat:2"), (
        "目标池 = 已受伤角色（含自己），未受伤者出池"
    )


def test_qingnang_asks_the_card_then_the_target():
    _card, state = _qingnang_state()

    engine = _run(
        state,
        [Scripted(_qingnang_driver(target=1)), Scripted(_decline)],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert asked[:3] == ["PLAY_PHASE", "SELECT_CARDS", "SELECT_OPTION"], (
        "先问「弃置1张手牌」，再问治谁（两步流水线）"
    )


def test_qingnang_discards_one_hand_card_and_heals_the_wounded_target():
    card, state = _qingnang_state()

    engine = _run(
        state,
        [Scripted(_qingnang_driver(target=1)), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    assert ("hand", "discard", "DISCARD") in _moves_of(engine, card)


def _qingnang_offered_after_cancel(engine) -> bool:
    """取消之后**同一出牌阶段**又挂起了含【青囊】候选的 PLAY_PHASE 询问。"""
    if not _skill_frame_popped(engine):
        return False
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == QueryKind.PLAY_PHASE.value
        and any(
            isinstance(action, ActivateSkill) and action.skill_id == QINGNANG
            for action in pending.legal_actions
        )
    )


def _run_qingnang_cancel(cancel_at: str):
    """在流水线的 ``cancel_at`` 步取消（``card`` / ``seat``）。

    返回 ``(成本牌, engine, 选择面上出现过的牌面集合)``；停止条件同【离间】：
    "取消后又挂起了一次含【青囊】候选的出牌阶段询问"。
    """
    card, state = _qingnang_state()
    faces: list[set[int]] = []

    def huatuo(query):
        if query.kind == QueryKind.SELECT_CARDS:
            faces.append({slot.card.card_id for slot in query.slots})
        return _qingnang_driver(cancel_at=cancel_at)(query)

    engine = _run(state, [Scripted(huatuo), Scripted(_decline)], _qingnang_offered_after_cancel)
    return card, engine, faces


@pytest.mark.parametrize("cancel_at", ["card", "seat"])
def test_qingnang_cancel_leaves_the_table_untouched_and_the_skill_available(cancel_at):
    """两步中任何一步取消 ⇒ 手牌原样、弃牌堆不变、目标未治疗、同一阶段还能再发动。"""
    card, engine, faces = _run_qingnang_cancel(cancel_at)
    player = engine.state.player(0)

    assert faces, "步 1 的问一定要先发生"
    assert set(player.hand) | set(player.equip) == faces[-1], "选择只收集、提交才生效"
    assert card in player.hand, "成本还在手里"
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert engine.state.player(1).hp == 2, "目标没有被治疗"
    assert _qingnang_offered_after_cancel(engine), "取消不消耗次数：同一阶段仍可再发动"


# ---------------------------------------------------------------------------
# 离间（貂蝉）：弃 1 张牌 + 两名男性 → 视为其中一名对另一名使用【决斗】
# ---------------------------------------------------------------------------


def test_lijian_requires_two_male_characters():
    card = _cards("slash")[0]
    one_male = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    two_males = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )

    assert not _offers(_play_candidates(one_male), LIJIAN), (
        "只有一名男性 / 两名女性 ⇒ 不进候选"
    )
    assert len(_offers(_play_candidates(two_males), LIJIAN)) == 1, "够条件 ⇒ 一条骨架候选"


def test_lijian_offer_is_a_single_skeleton_candidate_however_many_cards_and_males():
    """票 01：候选不再烘"牌 × 有序男性对"，恒为一条骨架 —— 手牌 20 × 男性 7 也不爆。

    这条取代旧的"过闸降级"断言：降级路径对【离间】不再有消费者。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="diaochan", gender=Gender.FEMALE), *_wounded_males(7)],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    assert len(hand) * (7 * 6) > CANDIDATE_CAP, "旧形状必须过闸（否则本用例证明不了什么）"

    offers = _offers(_play_candidates(state), LIJIAN)

    assert len(offers) == 1, "两维相乘的枚举消失：候选恒一条"
    assert offers[0].picks == () and offers[0].choices == (), "载荷为空，改由发动后的步骤询问"
    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "整段询问仍不超闸值"


def _lijian_state():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _lijian_driver(*, user: int, victim: int):
    """按**新流程**应答：点技能 → 弃置 1 张牌 → 选使用者 → 选目标。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == LIJIAN,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            wanted = {
                "lijian:user": f"seat:{user}",
                "lijian:victim": f"seat:{victim}",
            }.get(query.skill_id)
            if wanted is not None:
                return _find(
                    query,
                    lambda action: isinstance(action, SelectOption) and action.choice == wanted,
                )
        return _decline(query)

    return driver


def _lijian_victim_query(engine):
    return next(
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION.value
        and record.query.skill_id == "lijian:victim"
    )


def test_lijian_asks_the_card_then_the_two_males_in_order():
    card, state = _lijian_state()

    engine = _run(
        state,
        [
            Scripted(_lijian_driver(user=1, victim=2)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert asked[:4] == ["PLAY_PHASE", "SELECT_CARDS", "SELECT_OPTION", "SELECT_OPTION"], (
        "先问「弃置1张牌」，再先后问两名男性（第一次点的是使用者）"
    )
    assert _lijian_victim_query(engine).options == ("seat:2",), (
        "使用者不能当自己的目标：第二次的池排除已选的那名男性"
    )
    assert card in engine.state.discard, "成本弃掉"
    assert engine.state.player(2).hp == 3, "目标 2 没打出【杀】⇒ 受到 1 点伤害"
    assert engine.state.player(1).hp == 4, "使用者座位 1 不受伤（决斗败方是座位 2）"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 1, "伤害来源随被选中的男性使用者（不是貂蝉）"


def test_lijian_user_is_the_chosen_male_not_the_activator():
    """把两次点选反过来 ⇒ 使用者与败方互换，伤害来源随之（不是固定座位）。"""
    card, state = _lijian_state()

    engine = _run(
        state,
        [
            Scripted(_lijian_driver(user=2, victim=1)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    assert card in engine.state.discard
    assert engine.state.player(1).hp == 3, "这次败方是座位 1（座位 2 是使用者）"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 2


def test_lijian_may_pay_with_an_equipment_card():
    """成本可以是**装备区**的牌：步 1 的选择面 = 手牌 ∪ 装备区。"""
    equip = _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(equip,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(equip),
        turn_seat=0,
    )
    face: list[tuple[str, int]] = []

    def diaochan(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == LIJIAN,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            slots = list(query.slots)
            face.extend((slot.ref.zone.value, slot.card.card_id) for slot in slots)
            index = next(
                position
                for position, slot in enumerate(slots)
                if slot.card.card_id == equip
            )
            return _find(
                query,
                lambda action: isinstance(action, SelectCards)
                and action.picks == (index,),
            )
        if query.kind == QueryKind.SELECT_OPTION:
            wanted = {"lijian:user": "seat:1", "lijian:victim": "seat:2"}.get(query.skill_id)
            if wanted is not None:
                return _find(
                    query,
                    lambda action: isinstance(action, SelectOption) and action.choice == wanted,
                )
        return _decline(query)

    engine = _run(
        state,
        [Scripted(diaochan), Scripted(_decline), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert ("equip", equip) in face, "装备区的牌进了选择面"
    assert equip in engine.state.discard, "装备区的牌被弃置"
    assert engine.state.player(2).hp == 3, "技能照常结算"


# ---------------------------------------------------------------------------
# 取消（票 01 / S2）：中途退出不搬家产、不消耗次数
# ---------------------------------------------------------------------------


def _lijian_offered_after_cancel(engine) -> bool:
    """取消之后**同一出牌阶段**又挂起了含【离间】候选的 PLAY_PHASE 询问。"""
    if not _skill_frame_popped(engine):
        return False
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == QueryKind.PLAY_PHASE.value
        and any(
            isinstance(action, ActivateSkill) and action.skill_id == LIJIAN
            for action in pending.legal_actions
        )
    )


def _run_lijian_cancel(cancel_at: str):
    """在流水线的 ``cancel_at`` 步取消（``card`` / ``user`` / ``victim``）。

    返回 ``(engine, 选择面上出现过的牌面集合)``；停止条件是"取消后又挂起了一次含【离间】
    候选的出牌阶段询问"——这正是"取消不消耗次数"的可观测判据。
    """
    _card, state = _lijian_state()
    faces: list[set[int]] = []

    def diaochan(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == LIJIAN,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            faces.append({slot.card.card_id for slot in query.slots})
            if cancel_at == "card":
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            if cancel_at == query.skill_id.removeprefix("lijian:"):
                return _find(query, lambda action: isinstance(action, Pass))
            wanted = {"lijian:user": "seat:1", "lijian:victim": "seat:2"}[query.skill_id]
            return _find(
                query,
                lambda action: isinstance(action, SelectOption) and action.choice == wanted,
            )
        return _decline(query)

    engine = _run(
        state,
        [Scripted(diaochan), Scripted(_decline), Scripted(_decline)],
        _lijian_offered_after_cancel,
    )
    return engine, faces


@pytest.mark.parametrize("cancel_at", ["card", "user", "victim"])
def test_lijian_cancel_leaves_the_table_untouched_and_the_skill_available(cancel_at):
    """三步中任何一步取消 ⇒ 手牌 / 装备 / 弃牌堆原样，且同一阶段还能再发动一次。"""
    engine, faces = _run_lijian_cancel(cancel_at)
    player = engine.state.player(0)

    assert faces, "取消发生在选牌之后，故选择面一定构造过"
    assert set(player.hand) | set(player.equip) == faces[-1], (
        "选择只收集、提交才生效：取消不搬任何一张牌"
    )
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert _lijian_offered_after_cancel(engine), "取消不消耗发动次数：同一阶段仍可再发动"


def test_lijian_uses_a_pure_virtual_duel_card():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    card = pure_virtual_card(state, "duel")

    assert card.card_id < 0 and card.type == "duel" and card.name == "决斗"
    assert card.suit is None and card.rank is None and card.color is None


# ---------------------------------------------------------------------------
# 通用步骤流水线（票 02）：提炼出的形状**能表达**【离间】的三步
#
# 本票不迁【离间】（它保持票 01 的手写三步），所以这里把提炼物当作**新技能**注入
# （复用【苦肉】的空槽位，与上面 KUROU 的先例同姿态），只钉【青囊】用不到的那两维：
# 有序两次选座，且第二次的池由第一次的结果（与真牌同源的 ``check_targets``）决定。
# ---------------------------------------------------------------------------


def _inject_flow_skill(monkeypatch, flow) -> None:
    """把一条 :class:`~sgs.skills.active.SkillFlow` 当作【苦肉】槽位上的真技能注入。"""

    def offers(state_, ctx, current):
        if ctx.scope is not CandidateScope.PLAY or ctx.owner != ctx.subject:
            return current
        return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))

    definition = SkillDef(
        skill_id=KUROU,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, offers),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, KUROU: definition}),
    )
    monkeypatch.setattr(
        active_module,
        "ACTIVATIONS",
        MappingProxyType(
            {**active_module.ACTIVATIONS, KUROU: partial(active_module.run_flow, flow=flow)}
        ),
    )


def _flow_two_male_state():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="huanggai"), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _flow_driver(seats: tuple[int, int]):
    """点技能 → 弃 1 张牌 → 依次点 ``seats`` 那两个座位。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == KUROU,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            wanted = {
                "kurou:seat:0": f"seat:{seats[0]}",
                "kurou:seat:1": f"seat:{seats[1]}",
            }.get(query.skill_id)
            if wanted is not None:
                return _find(
                    query,
                    lambda action: isinstance(action, SelectOption) and action.choice == wanted,
                )
        return _decline(query)

    return driver


def _flow_seat_queries(engine):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_OPTION.value
    ]


def test_the_flow_shape_can_express_a_dependent_two_seat_pick(monkeypatch):
    """``pool(state, owner, chosen)`` 拿得到第一次的结果 ⇒ 能照真牌 ``check_targets`` 收窄。"""
    _card, state = _flow_two_male_state()
    duel = pure_virtual_card(state, "duel")
    seen: list[tuple[int, ...]] = []

    def pool(state_, owner, chosen):
        """与 ``_lijian_partners`` 同形：使用者一侧不设限，受害者一侧走真牌同一条链。"""
        del owner
        seen.append(tuple(chosen))
        if not chosen:
            return (1, 2)
        return tuple(seat for seat in (1, 2) if seat in check_targets(state_, chosen[0], duel))

    _inject_flow_skill(
        monkeypatch,
        active_module.SkillFlow(
            steps=(
                active_module.PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="card"),
                active_module.PickSeats(lo=2, hi=2, pool=pool, key="seat"),
            ),
            effect=lambda host, frame, answers: None,
        ),
    )

    engine = _run(
        state,
        [Scripted(_flow_driver((1, 2))), Scripted(_decline), Scripted(_decline)],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert asked[:4] == ["PLAY_PHASE", "SELECT_CARDS", "SELECT_OPTION", "SELECT_OPTION"], (
        "①选牌 → ②选第一次座位 → ③选第二次座位（有序两次都是同一个 PickSeats 步骤）"
    )
    seats = _flow_seat_queries(engine)
    assert [query.actor for query in seats] == [0, 0], "两步都由发动者应答（actor 缺省）"
    assert (1,) in seen, "第二次的池建在第一次的结果之上（check_targets 据此收窄）"
    assert seats[0].options == ("seat:1", "seat:2"), "第一次：全部合法男性"
    assert seats[1].options == ("seat:2",), "第二次：check_targets 只留下座位 2"


def test_the_flow_shape_ordered_pick_never_re_offers_the_chosen_seat(monkeypatch):
    """``ordered=True``：同一步里第二问的池自动排除第一问已选的座位（design §3 N3）。"""
    _card, state = _flow_two_male_state()

    def pool(state_, owner, chosen):
        del state_, owner, chosen
        return (1, 2)  # 刻意不做任何收窄：排除只能来自 ordered

    _inject_flow_skill(
        monkeypatch,
        active_module.SkillFlow(
            steps=(active_module.PickSeats(lo=2, hi=2, ordered=True, pool=pool, key="seat"),),
            effect=lambda host, frame, answers: None,
        ),
    )

    engine = _run(
        state,
        [Scripted(_flow_driver((1, 2))), Scripted(_decline), Scripted(_decline)],
        _skill_frame_popped,
    )

    seats = _flow_seat_queries(engine)
    assert [query.options for query in seats] == [("seat:1", "seat:2"), ("seat:2",)], (
        "第一问全池，第二问刨掉已选的座位 1"
    )


# ---------------------------------------------------------------------------
# 结姻（孙尚香）：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
# ---------------------------------------------------------------------------


def test_jieyin_target_pool_is_wounded_males_excluding_self():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=3,
                hp_max=4,
                hand=(first, second),
                hero="sunshangxiang",
                gender=Gender.FEMALE,
            ),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    assert {action.choices for action in _offers(_play_candidates(state), JIEYIN)} == {
        ("seat:2",),
    }, "只含已受伤男性：满血男性与受伤女性都出池"


def test_jieyin_heals_both_and_clamps_at_the_maximum():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=4,
                hp_max=4,
                hand=(first, second),
                hero="sunshangxiang",
                gender=Gender.FEMALE,
            ),
            SeatSpec(hp=2, hp_max=3, gender=Gender.MALE),
        ],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    def jieyin(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == JIEYIN
                and set(action.picks) == {first, second},
            )
        return _decline(query)

    engine = _run(state, [Scripted(jieyin), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    assert engine.state.player(0).hp == 4, "自己已满 ⇒ 不再回复、不越上限"
    assert first in engine.state.discard and second in engine.state.discard


# ---------------------------------------------------------------------------
# 登记面：声明与 CATALOG 同步（票 12 照表登记的前置）
# ---------------------------------------------------------------------------


def test_every_declared_active_skill_has_a_handler_and_a_catalog_definition():
    assert set(active_module.ACTIVATIONS) == {
        skill.skill_id for skill in active_module.ACTIVE_SKILLS
    }
    assert all(skill_id in CATALOG for skill_id in active_module.ACTIVATIONS)


# ---------------------------------------------------------------------------
# 两维相乘的技能：过闸后降级为"只留目标维"（票 04 §要做什么 3）
#
# 票 01 的【离间】、票 02 的【青囊】迁到骨架候选后不再过闸；这里只剩【结姻】
# （票 03 才迁）仍在验降级路径，外加【青囊】"候选恒一条"的对照面。
# ---------------------------------------------------------------------------


def _wounded_males(count: int) -> list[SeatSpec]:
    return [SeatSpec(hp=2, hp_max=4, gender=Gender.MALE) for _ in range(count)]


def test_qingnang_offer_is_a_single_skeleton_candidate_however_many_cards_and_targets():
    """票 02：候选不再烘"牌 × 目标"，恒一条骨架 —— 手牌 20 × 7 受伤目标也不爆。

    这条取代旧的"过闸降级"断言（`test_qingnang_degrades_to_target_only_candidates_beyond_the_cap`）：
    【青囊】的 `CANDIDATE_CAP` 降级分支已删除，同一个技能不再有两种交互形状。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="huatuo"), *_wounded_males(7)],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    assert len(hand) * 7 > CANDIDATE_CAP, "旧形状必须过闸（否则本用例证明不了什么）"

    offers = _offers(_play_candidates(state), QINGNANG)

    assert len(offers) == 1, "两维相乘的枚举消失：候选恒一条"
    assert offers[0].picks == () and offers[0].choices == (), "载荷为空，改由发动后的步骤询问"
    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "整段询问仍不超闸值"


def test_jieyin_degrades_to_target_only_candidates_beyond_the_cap():
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hero="sunshangxiang", gender=Gender.FEMALE),
            *_wounded_males(2),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    pair_count = len(hand) * (len(hand) - 1) // 2
    assert pair_count * 2 > CANDIDATE_CAP, "这局必须过闸（否则本用例空转）"

    offers = _offers(_play_candidates(state), JIEYIN)

    assert [(action.picks, action.choices) for action in offers] == [
        ((), ("seat:1",)),
        ((), ("seat:2",)),
    ]


def test_jieyin_sequential_mode_discards_two_cards_asked_one_at_a_time():
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hp_max=4, hand=hand, hero="sunshangxiang", gender=Gender.FEMALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.MALE),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    picks_made: list[int] = []

    def jieyin(query):
        if query.kind == QueryKind.PLAY_PHASE:
            candidates = _offers(query.legal_actions, JIEYIN)
            assert candidates and all(action.picks == () for action in candidates)
            return candidates[0]
        if query.kind == QueryKind.SELECT_CARDS:
            if len(picks_made) < 2:
                picks_made.append(1)
                return _find(
                    query,
                    lambda action: isinstance(action, SelectCards) and action.picks == (0,),
                )
            return _find(query, lambda action: isinstance(action, Pass))
        return _decline(query)

    engine = _run(state, [Scripted(jieyin), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    assert len(picks_made) == 2, "两张手牌逐张问出"
    assert len(engine.state.player(0).hand) == len(hand) + 2 - 2, "两张手牌被弃"
