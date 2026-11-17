"""一将成名-2014·移动版：曹真【司敌】（yijiang-2014-round3 票 03；用户 2026-11-17 签）。

牌面（移动版 = 用户资产）：

    ①当你使用【闪】时，或当其他角色于你的回合内使用【闪】时，你可以将牌堆顶的一张牌
      置于你的武将牌上称为“司敌”牌；
    ②其他角色的出牌阶段开始时，你可以移去一张“司敌”牌，令其本阶段使用【杀】的次数上限-1。

**用户 2026-11-17 的裁定**：

- **A1 = 移动版**（本将住独立移动版表 `heroes.yijiang_2014_mobile.json`，显示名「曹真移动版」，
  `version = "yijiang-2014-mobile"`；OL 的【司敌】是完全另一条技能，见
  `.scratch/yijiang-2014-round3/issues/04-caozhen-a1-ruling.md`）；
- **牌名 = 「司敌」**（不是移动版官方页的「钤」）；
- **② 的「移去一张」= 玩家自选**（像【七星】那样；那些牌并非完全等价——将来可能有按花色 /
  牌名读它们的效果），故是一步 `PickCards(zones=(Zone.HERO,), hero_zone=…)`，不是实现代挑。

驱动纪律：本文件用真引擎跑"使用【闪】→ 放牌"与"别人的出牌阶段开始 → 移去 → 上限 −1"两条
完整链路；`②` 的目标永远是**当前回合角色**，`①` 的判据走 `USE_ON_USE`（打出不触发）。
"""

from __future__ import annotations

from sgs.data import RuleConfig, load_content_pack, load_heroes
from sgs.effects import check_count
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.catalog import hero_card_zone_key
from sgs.skills.hero_zone import (
    SIDI,
    _sidi_limit_condition,
    _sidi_place_condition,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    SelectCards,
    UseCard,
    Zone,
)
from sgs.state.models import HeroCardZone
from sgs.timing import Timing
from support import SeatSpec, build_state, cards_of_type, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")
HERO = "caozhen_mobile"
DODGE = cards_of_type(PACK, "dodge")
SLASH = cards_of_type(PACK, "slash")
# 「司敌」牌的取样：取一张既不在手里的牌（牌堆里还留着它会让"同一张牌两个区"）
HERO_CARD = DODGE[0]
HERO_CARD_SECOND = DODGE[1]


def _state(*specs, turn_seat=0, phase=Phase.PLAY, hero_card=None, deck_extra=()):
    """四座局面：0 = 曹真移动版；``hero_card`` 预先放进「司敌」区。"""
    seats = [SeatSpec(hero=HERO), *specs]
    while len(seats) < 4:
        seats.append(SeatSpec())
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    if hero_card is not None:
        used.append(hero_card)
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *used, *deck_extra),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(first_seat=0),
    )


def _give_hero_card(state, *card_ids: int) -> str:
    key = hero_card_zone_key(0, SIDI)
    state.player(0).hero_cards[key] = HeroCardZone(
        skill_id=SIDI, owner=0, cards=list(card_ids)
    )
    return key


def _drive(engine, stop, *, pick: int = 0, limit: int = 900):
    """推进到 ``stop(engine)``；应答规则：能发动【司敌】就发动，选牌取第 ``pick`` 个位置。"""
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.pending is not None:
            query = engine.pending
            act = next(
                (a for a in query.legal_actions if isinstance(a, ActivateSkill)), None
            )
            if act is not None:
                engine.submit(act)
                continue
            select = next(
                (a for a in query.legal_actions if isinstance(a, SelectCards)), None
            )
            if select is not None:
                engine.submit(select)
                continue
            end = next((a for a in query.legal_actions if isinstance(a, EndPhase)), None)
            if end is not None:
                engine.submit(end)
                continue
            give_up = next((a for a in query.legal_actions if isinstance(a, Pass)), None)
            engine.submit(give_up if give_up is not None else query.legal_actions[0])
            continue
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("步数上限内没有到达停止条件")


def _to_other_play_phase(state, *, target_seat: int = 1):
    """把引擎推进到 ``target_seat`` 的**出牌阶段开始之后**（座位 0 空过自己的回合）。"""
    engine = Engine(state, seed=1)
    return engine


# ---------------------------------------------------------------------------
# 数据面 / 声明面
# ---------------------------------------------------------------------------


def test_caozhen_mobile_is_in_the_mobile_table_with_sidi():
    hero = load_heroes("yijiang_2014_mobile").by_id(HERO)
    assert hero is not None, "曹真移动版必须落在 heroes.yijiang_2014_mobile.json"
    assert hero.name == "曹真移动版"
    assert hero.true_name == "曹真"
    assert hero.kingdom.value == "wei" and hero.gender.value == "male"
    assert hero.hp_max == 4
    assert hero.version == "yijiang-2014-mobile"
    assert hero.enabled is True
    assert [skill.id for skill in hero.skills] == ["sidi"]


def test_sidi_declares_the_mobile_card_text_and_a_never_returning_zone():
    from sgs.data import hero_placement_decl

    definition = CATALOG[SIDI]
    assert definition.locked is False, "「可以」⇒ 每次问一次是否发动"
    assert definition.places_on_hero_card is True
    assert definition.preactivated is False
    assert [hook.timing for hook in definition.hooks] == [
        Timing.USE_ON_USE,
        Timing.PHASE_PLAY_START,
    ], "① 只挂使用（打出走 PLAY_ON_PLAY，不在这里）；② 挂在别人的出牌阶段开始时"
    assert definition.step_requirements["card"] == "移去一张“司敌”牌（武将牌上的牌）"
    assert "司敌" in definition.requirement and "钤" not in definition.requirement
    decl = hero_placement_decl(SIDI, pack_id=PACK.pack_id)
    assert decl is not None, "放置类技能必须在数据里声明三项"
    assert decl.return_timing is not None
    assert decl.extra_effect is True, "② 是附加流程 ⇒ 引擎不代还"


# ---------------------------------------------------------------------------
# ① 使用【闪】→ 牌堆顶一张置于武将牌上
# ---------------------------------------------------------------------------


def _place_frame(state, *, source: int, card_id: int, owner: int = 0):
    from sgs.resolution import Frame
    from sgs.skills.catalog import MATCH_OWNER_KEY

    frame = Frame(kind=FrameKind.RESOLVE, data={})
    frame.data["source"] = source
    frame.data["card"] = state.card(card_id)
    frame.data[MATCH_OWNER_KEY] = owner
    return frame


class _Host:
    def __init__(self, state):
        self.state = state


def test_sidi_places_a_card_when_the_owner_uses_a_dodge():
    state = _state(SeatSpec(hand=(DODGE[0],)))
    engine = Engine(state, seed=1)
    engine.start_use(state.card(DODGE[0]), source=0, targets=())
    _drive(engine, lambda e: e.pending is not None)
    query = engine.pending
    assert query.kind == "SKILL_ACTIVATION"
    act = next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
    assert act.skill_id == SIDI
    engine.submit(act)
    _drive(engine, lambda e: bool(state.player(0).hero_cards))
    key = hero_card_zone_key(0, SIDI)
    assert len(state.player(0).hero_cards[key].cards) == 1, "牌堆顶一张进了「司敌」区"


def test_sidi_is_not_offered_on_a_played_dodge():
    """【万箭齐发】窗口里的【闪】是**打出** ⇒ ① 不触发（只有 USE_ON_USE 一条钩子）。"""
    assert Timing.PLAY_ON_PLAY not in [hook.timing for hook in CATALOG[SIDI].hooks]


def test_sidi_condition_reads_who_used_the_dodge_and_whose_turn_it_is():
    from sgs.effects import behavior_for

    dodge = _state(SeatSpec(hand=(DODGE[0],)))
    host = _Host(dodge)
    owner_frame = _place_frame(dodge, source=0, card_id=DODGE[0])
    assert _sidi_place_condition(host, owner_frame) is True, "自己使用【闪】（谁的回合都算）"

    others_frame = _place_frame(dodge, source=1, card_id=DODGE[0])
    dodge.turn_seat = 0
    assert _sidi_place_condition(host, others_frame) is True, "别人**在我的回合内**使用【闪】"
    dodge.turn_seat = 2
    assert _sidi_place_condition(host, others_frame) is False, "别人的回合里别人用【闪】⇒ 不触发"

    slash_frame = _place_frame(dodge, source=0, card_id=SLASH[0])
    dodge.turn_seat = 0
    assert _sidi_place_condition(host, slash_frame) is False, "不是【闪】就不触发"


def test_sidi_is_not_offered_when_both_decks_are_empty():
    state = _state()
    state.deck.clear()
    state.discard.clear()
    host = _Host(state)
    frame = _place_frame(state, source=0, card_id=DODGE[0])
    assert _sidi_place_condition(host, frame) is False, "没有牌可放 ⇒ 候选阶段就不产出"


# ---------------------------------------------------------------------------
# ② 别的角色的出牌阶段开始时：移去一张（玩家自选）→ 其本阶段【杀】上限 −1
# ---------------------------------------------------------------------------


def _advance_to(engine, pred, *, limit: int = 900):
    """推进到 ``pred(engine)`` 成立（成立时**不**提交，留给调用方）；其余询问给最小合法应答。"""
    for _ in range(limit):
        if pred(engine):
            return engine
        if engine.pending is not None:
            query = engine.pending
            end = next((a for a in query.legal_actions if isinstance(a, EndPhase)), None)
            if end is not None:
                engine.submit(end)
                continue
            select = next(
                (a for a in query.legal_actions if isinstance(a, SelectCards)), None
            )
            if select is not None:
                engine.submit(select)
                continue
            give_up = next((a for a in query.legal_actions if isinstance(a, Pass)), None)
            engine.submit(give_up if give_up is not None else query.legal_actions[0])
            continue
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("步数上限内没有到达停止条件")


def _sidi_ask(engine) -> bool:
    query = engine.pending
    return query is not None and query.kind == "SKILL_ACTIVATION" and any(
        isinstance(a, ActivateSkill) and a.skill_id == SIDI for a in query.legal_actions
    )


def _advance_to_sidi_limit(state):
    """推进到「座位 1 的出牌阶段开始」那一问（曹真在座位 0）。"""
    engine = Engine(state, seed=1)
    _advance_to(engine, _sidi_ask)
    assert engine.pending is not None, "② 应该在别人的出牌阶段开始时问一次"
    assert state.turn_seat == 1
    return engine


def _submit_sidi_ask(engine):
    query = engine.pending
    act = next(
        a for a in query.legal_actions if isinstance(a, ActivateSkill) and a.skill_id == SIDI
    )
    engine.submit(act)


def test_sidi_is_not_offered_without_a_card_on_the_hero_card():
    state = _state()
    engine = Engine(state, seed=1)
    _advance_to(engine, lambda e: e.pending is not None)
    assert engine.pending.kind == "PLAY_PHASE", "没有「司敌」牌 ⇒ ② 连问都不发"


def test_sidi_limit_lowers_the_slash_count_of_the_turn_player():
    state = _state(hero_card=HERO_CARD)
    key = _give_hero_card(state, HERO_CARD)
    engine = _advance_to_sidi_limit(state)
    _submit_sidi_ask(engine)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "SELECT_CARDS")
    select = next(a for a in engine.pending.legal_actions if isinstance(a, SelectCards))
    engine.submit(select)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "PLAY_PHASE")
    assert state.player(1).sidi_slash_penalty == 1, "移去之后：被限者本阶段上限 −1"
    assert state.player(0).hero_cards[key].cards == [], "那张「司敌」牌已移去"
    assert HERO_CARD in state.discard, "移去 = 置入弃牌堆（规则 §15.2）"
    assert check_count(state, 1, state.card(SLASH[0])) is True, (
        "上限 1 − 1 = 0 ⇒ 这张【杀】已经超限"
    )
    assert check_count(state, 2, state.card(SLASH[0])) is False, "只限这一个座位"


def test_sidi_limit_removes_the_card_the_player_picked():
    """用户 2026-11-17 的口径：② 的牌由**玩家自选**（不是实现代挑区内序第一张）。"""
    state = _state(hero_card=HERO_CARD, deck_extra=(HERO_CARD_SECOND,))
    key = _give_hero_card(state, HERO_CARD, HERO_CARD_SECOND)
    engine = _advance_to_sidi_limit(state)
    _submit_sidi_ask(engine)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "SELECT_CARDS")
    query = engine.pending
    picks = {
        action.picks for action in query.legal_actions if isinstance(action, SelectCards)
    }
    assert picks == {(0,), (1,)}, "两张「司敌」牌 ⇒ 两个候选（玩家自己挑）"
    second = next(
        a for a in query.legal_actions if isinstance(a, SelectCards) and a.picks == (1,)
    )
    engine.submit(second)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "PLAY_PHASE")
    assert HERO_CARD_SECOND in state.discard, "移去的是**玩家选的那一张**"
    assert state.player(0).hero_cards[key].cards == [HERO_CARD], "另一张留在武将牌上"


def test_sidi_limit_keeps_an_unlimited_slash_unlimited():
    crossbow = cards_of_type(PACK, "crossbow")[0]
    state = _state(SeatSpec(equip=(crossbow,)), hero_card=HERO_CARD)
    _give_hero_card(state, HERO_CARD)
    engine = _advance_to_sidi_limit(state)
    _submit_sidi_ask(engine)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "SELECT_CARDS")
    select = next(a for a in engine.pending.legal_actions if isinstance(a, SelectCards))
    engine.submit(select)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "PLAY_PHASE")
    assert state.player(1).sidi_slash_penalty == 1
    assert check_count(state, 1, state.card(SLASH[0])) is False, (
        "「无次数限制」没有有限上限可减（规则 §17.2 ④）"
    )


def test_sidi_limit_expires_at_the_play_phase_end():
    state = _state(hero_card=HERO_CARD)
    _give_hero_card(state, HERO_CARD)
    engine = _advance_to_sidi_limit(state)
    _submit_sidi_ask(engine)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "SELECT_CARDS")
    select = next(a for a in engine.pending.legal_actions if isinstance(a, SelectCards))
    engine.submit(select)
    _advance_to(engine, lambda e: bool(e.pending) and e.pending.kind == "PLAY_PHASE")
    assert state.player(1).sidi_slash_penalty == 1
    _advance_to(engine, lambda e: state.phase is Phase.DISCARD)
    assert state.player(1).sidi_slash_penalty == 0, (
        "「本阶段」的作用域在该出牌阶段结束时清掉"
    )


def test_sidi_limit_condition_needs_another_seats_play_phase():
    state = _state(turn_seat=0, hero_card=HERO_CARD)
    _give_hero_card(state, HERO_CARD)
    host = _Host(state)
    frame = _place_frame(state, source=0, card_id=DODGE[0])
    assert _sidi_limit_condition(host, frame) is False, (
        "自己的出牌阶段开始 ⇒ 不触发（牌面写「其他角色」）"
    )
