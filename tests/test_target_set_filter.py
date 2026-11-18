"""框架票 05：``ModifierPoint.TARGET_SET_FILTER``（组合级目标资格谓词）。

票面 = ``.scratch/jie-yijiang-2014-wuyi-caozhen/issues/05-target-set-filter.md``（用户 2026-11-18
裁「吴-C1 = A」）。本文件只钉**框架本身**的四件事（消费者 = 界吴懿移动版【奔袭】②，住在
``tests/test_hero_jie_yijiang.py``）：

1. 挂点在 ``ModifierPoint`` 闭集里；
2. **没有消费者**时逐组原样返回（既有牌型的候选逐字节不变）；
3. 消费者**只许收窄**（返回假 ⇒ 那一组不合法；返回真 ⇒ 保留）；
4. 四个候选生成站点（``_play_phase`` / ``_respond`` 的 USE 趟 / ``virtual_card_use`` 的
   PLAY 趟与 RESPOND 趟）都真的过了这条链——用"注入一个假消费者"的合成局面证明。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.effects import (
    Modifier,
    ModifierContext,
    ModifierPoint,
    filter_target_sets,
    legal_actions_for,
)
from sgs.engine import Engine
from sgs.state import Action, Phase, QueryKind, UseCard

from support import SeatSpec, build_state, cards_of_type, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")

SEAT = 0


def test_the_point_is_registered_in_the_closed_set():
    assert ModifierPoint.TARGET_SET_FILTER.value == "TARGET_SET_FILTER"
    assert {point.value for point in ModifierPoint} >= {"TARGET_SET_FILTER"}


def _state(*, hand: tuple[int, ...] = ()) -> object:
    """四座位、无武将的合成局面（别的修饰器一条都不在 ⇒ 链上只有本文件注入的那条）。"""
    deck = deck_excluding(PACK, *hand)
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hp=4, hp_max=4, identity=Identity.LORD),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )


def _install(monkeypatch, handler):
    """把一条 ``TARGET_SET_FILTER`` 消费者挂进 ``resolve_instances``（合成实例，不入 CATALOG）。"""
    instance = SimpleNamespace(
        owner=SEAT,
        skill_id="test.target_set_filter",
        definition=SimpleNamespace(
            modifiers=(Modifier(ModifierPoint.TARGET_SET_FILTER, handler),)
        ),
    )
    monkeypatch.setattr(
        "sgs.skills.resolve_instances", lambda state, **kwargs: (instance,)
    )
    return instance


# ---------------------------------------------------------------------------
# 2 没有消费者 ⇒ 原样返回
# ---------------------------------------------------------------------------


def test_without_a_consumer_every_combo_survives():
    state = _state()
    card = state.card(cards_of_type(PACK, "slash")[0])
    combos = ((1,), (2,), (3,), (1, 2))
    assert filter_target_sets(state, SEAT, card, combos) == combos
    assert filter_target_sets(state, SEAT, card, ()) == ()


# ---------------------------------------------------------------------------
# 3 消费者只许收窄
# ---------------------------------------------------------------------------


def test_a_consumer_can_only_narrow_the_combo_set(monkeypatch):
    state = _state()
    card = state.card(cards_of_type(PACK, "slash")[0])
    seen: list[tuple[int, ...]] = []

    def only_single(host_state, ctx, current):
        assert current is True, "current 必须从 True 起"
        seen.append(tuple(ctx.use_targets or ()))
        return len(ctx.use_targets or ()) == 1

    _install(monkeypatch, only_single)
    kept = filter_target_sets(state, SEAT, card, ((1,), (2, 3)))
    assert kept == ((1,),), "多目标的组合被收窄掉"
    assert seen == [(1,), (2, 3)], "每一组都过了一次链（逐组派发）"


def test_a_consumer_returning_truthy_keeps_the_combo(monkeypatch):
    state = _state()
    card = state.card(cards_of_type(PACK, "slash")[0])

    _install(monkeypatch, lambda host_state, ctx, current: 1)
    assert filter_target_sets(state, SEAT, card, ((1,), (2,))) == ((1,), (2,))


# ---------------------------------------------------------------------------
# 4 四个站点都真的过了这条链
# ---------------------------------------------------------------------------


def _play_phase_uses(state) -> tuple[tuple[int, ...], ...]:
    actions = legal_actions_for(state, SEAT, QueryKind.PLAY_PHASE, answers_seq=1)
    return tuple(
        tuple(int(seat) for seat in action.targets)
        for action in actions
        if isinstance(action, UseCard)
        and int(action.card.card_id) == int(cards_of_type(PACK, "slash")[0])
    )


def test_the_play_phase_site_filters_the_combos(monkeypatch):
    slash = cards_of_type(PACK, "slash")[0]
    state = _state(hand=(slash,))
    before = _play_phase_uses(state)
    assert before, "合成局面里【杀】至少有一个合法目标"

    _install(monkeypatch, lambda host_state, ctx, current: 1 not in (ctx.use_targets or ()))
    after = _play_phase_uses(state)
    assert after and all(1 not in combo for combo in after)
    assert set(after) < set(before), "只收窄、不新增"


def test_the_respond_site_filters_the_combos(monkeypatch):
    """被迫使用窗口（借刀 / 乱武）走的 `_respond` USE 趟：同一份链值。"""
    from sgs.state import RespondContext, RespondMode, WindowCause

    slash = cards_of_type(PACK, "slash")[0]
    state = _state(hand=(slash,))
    payload = RespondContext(
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        target=1,
        mode=RespondMode.USE,
    )
    before = legal_actions_for(
        state, SEAT, QueryKind.RESPOND, answers_seq=1, payload=payload
    )
    assert any(isinstance(action, UseCard) for action in before), "被迫使用窗口的候选"

    _install(monkeypatch, lambda host_state, ctx, current: False)
    after = legal_actions_for(
        state, SEAT, QueryKind.RESPOND, answers_seq=1, payload=payload
    )
    assert not any(isinstance(action, UseCard) for action in after), (
        "消费者判死每一组 ⇒ 这一档一个使用候选都不剩"
    )
