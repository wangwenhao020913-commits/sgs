"""技能机制与装备触发效果（施工票 30）。

落点与票 17 §"测试缝"一致：

- **规则函数 / 只读真源**（少量直接单测，M1 先例）：``target_is_effective`` 的青釭剑 ×
  仁王盾、``transformed_slash`` 的转化牌面事实、``legal_actions_for`` 的提供者序列、
  ``resolve_instances`` / ``instance_key`` / ``prune_skill_state`` 的实例模型。
- **库门面**（唯一"真"缝）：``Engine.from_state`` 的人为局面 + 脚本玩家，断言记录流里的
  发动询问、判定帧、伤害扣减 / 防止、牌移动与虚拟牌。

不测 ``CATALOG`` / 修饰器挂点 / ``OPS`` 注册表的内部结构（票 17 §"明确不开的缝"）；
行为全部经候选、询问、时机与 delta 流观测。
"""

from __future__ import annotations

from sgs.data import Color, Gender, RuleConfig, load_content_pack
from sgs.effects import (
    legal_actions_for,
    target_is_effective,
)
from sgs.engine import Engine
from sgs.skills import (
    CATALOG,
    instance_key,
    is_equipped,
    prune_skill_state,
    resolve_instances,
    transformed_slash,
)
from sgs.state import (
    ActivateSkill,
    DodgeContext,
    FrameKind,
    Pass,
    PlayCard,
    QueryKind,
    SelectCards,
    SelectOption,
    SelectOptionContext,
    UseCard,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _first_by_color(type_id: str, *, black: bool) -> int:
    from sgs.data import color_of_suit

    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id
        for card_id in _cards(type_id)
        if color_of_suit(PACK.deck.cards[card_id].suit) is wanted
    )


QINGGANG = _cards("qinggang_sword")[0]
RENWANG = _cards("renwang_shield")[0]
ICE_SWORD = _cards("ice_sword")[0]
QILIN = _cards("qilin_bow")[0]
DOUBLE_SWORD = _cards("double_sword")[0]
GREEN_DRAGON = _cards("green_dragon_blade")[0]
AXE = _cards("axe")[0]
EIGHT_DIAGRAMS = _cards("eight_diagrams")[0]
SERPENT = _cards("serpent_spear")[0]
CROSSBOW = _cards("crossbow")[0]
JUYING = _cards("jueying")[0]
SLASH = _cards("slash")[0]
BLACK_SLASH = _first_by_color("slash", black=True)
RED_SLASH = _first_by_color("slash", black=False)


# ---------------------------------------------------------------------------
# 脚本玩家
# ---------------------------------------------------------------------------


class Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _deck_excluding(*used: int) -> tuple[int, ...]:
    """其余全部实体牌（不够会短摸平局，构造局面必须把牌堆补满）。"""
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _judge_deck(judge_card: int, *, used: tuple[int, ...] = ()) -> tuple[int, ...]:
    """把 ``judge_card`` 放在**第 3 张**：摸牌阶段先取走前两张，判定取到的就是它。"""
    excluded = set(used) | {judge_card}
    fillers = [card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded]
    first, second = fillers[0], fillers[1]
    rest = [card_id for card_id in fillers[2:]]
    return (first, second, judge_card, *rest)


def _run(state, players, stop, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _slash_use(query, card_id: int, target: int):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == (target,),
    )


def _use_frame_popped(engine) -> bool:
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )


def _has_query(engine, kind: str, *, skill_id: str | None = None) -> bool:
    for record in engine.records:
        if record.kind != "query" or record.query.kind != kind:
            continue
        if skill_id is None or getattr(record.query, "skill_id", None) == skill_id:
            return True
    return False


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# 青釭剑 × 仁王盾：EFFECTIVENESS 链上的 ignore_armor（票 07 §3 / 票 30）
# ---------------------------------------------------------------------------


def test_qinggang_sword_makes_a_black_slash_ignore_the_targets_renwang_shield():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(QINGGANG,)), SeatSpec(equip=(RENWANG,))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is True, (
        "青釭剑在 EFFECTIVENESS 链上先置 ignore_armor，仁王盾不再把黑杀判为无效"
    )


def test_qinggang_sword_only_ignores_armor_for_its_holders_own_slash():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(equip=(QINGGANG, RENWANG))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is False, (
        "青釭剑在目标身上：它不压制自己的防具，座位 0 的黑杀照常被仁王盾挡下"
    )


def test_qinggang_sword_does_not_change_effectiveness_for_non_slash_cards():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(QINGGANG,)), SeatSpec(equip=(RENWANG,))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(RED_SLASH), 0, 1) is True


# ---------------------------------------------------------------------------
# 寒冰剑：防止伤害 + 依次弃两张（帧级 prevented）
# ---------------------------------------------------------------------------


def test_ice_sword_prevents_the_damage_and_discards_two_of_the_targets_cards():
    first, second = _cards("slash")[1], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)),
            SeatSpec(hand=(first, second)),
        ],
        deck=_deck_excluding(SLASH, ICE_SWORD, first, second),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert engine.state.player(1).hp == 4, "防止伤害 ⇒ 不扣体力"
    assert not any(
        record.kind == "delta" and record.delta_type == "hp_change" for record in engine.records
    ), "防止伤害不新增 delta"
    assert not any(
        record.kind == "frame" and record.frame_kind is FrameKind.LIFE_DECREASE
        for record in engine.records
    ), "防止伤害不新增时机 / 帧"
    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.ice_sword")
    assert _moves_of(engine, first) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, second) == [("hand", "discard", "DISCARD")]


def test_ice_sword_clamps_to_the_targets_available_cards():
    """池不足退化：目标只有一张牌 ⇒ 只弃一张，不卡死（票 19 §6）。"""
    only = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)), SeatSpec(hand=(only,))],
        deck=_deck_excluding(SLASH, ICE_SWORD, only),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _moves_of(engine, only) == [("hand", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 4


# ---------------------------------------------------------------------------
# 麒麟弓：造成伤害时可弃目标一张坐骑
# ---------------------------------------------------------------------------


def test_qilin_bow_discards_a_mount_of_the_damage_target():
    mount = JUYING
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(QILIN,)), SeatSpec(equip=(mount,))],
        deck=_deck_excluding(SLASH, QILIN, mount),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.qilin_bow")
    assert _moves_of(engine, mount) == [("equip", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 3, "麒麟弓只弃坐骑，不防止伤害"


def test_declining_a_non_locked_activation_skips_the_hook():
    """答 ``Pass``（不发动）⇒ 技能不生效（`GLOSSARY.md` 的**发动**）。"""
    mount = JUYING
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(QILIN,)), SeatSpec(equip=(mount,))],
        deck=_deck_excluding(SLASH, QILIN, mount),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.qilin_bow")
    assert not _has_query(engine, "SELECT_CARDS"), "不发动 ⇒ 不构造弃坐骑的选取询问"
    assert mount in engine.state.player(1).equip
    assert engine.state.player(1).hp == 3, "不发动麒麟弓 ⇒ 照常受伤"


def test_the_slash_canceled_seam_only_fires_for_the_slash_owner():
    """【青龙偃月刀】只对**持有者自己**的【杀】被抵消时生效（票 12 §4）。"""
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hand=(dodge,), equip=(GREEN_DRAGON,)),
        ],
        deck=_deck_excluding(SLASH, dodge, GREEN_DRAGON),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade"), (
        "被抵消的是座位 0 的【杀】，座位 1 的青龙偃月刀不得被问"
    )
    slash_actions = [
        record
        for record in engine.records
        if record.kind == "action"
        and getattr(record.action, "kind", None) == "UseCard"
        and record.action.card.type == "slash"
    ]
    assert len(slash_actions) == 1, "没有续杀"
    assert engine.state.player(1).hp == 4, "闪抵消，未受伤"


def test_renwang_shield_blocks_a_transformed_black_slash():
    """两张黑牌的丈八蛇矛【杀】仍有黑色，被【仁王盾】挡（U-4 的裁定）。"""
    from sgs.data import color_of_suit

    blacks = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    ]
    state = build_state(
        pack=PACK, seats=[SeatSpec(equip=(RENWANG,)), SeatSpec()], turn_seat=0
    )
    card = transformed_slash(state, (blacks[0], blacks[1]))

    assert card.color is Color.BLACK
    assert target_is_effective(state, card, 1, 0) is False, "转化黑杀照常被仁王盾挡"


# ---------------------------------------------------------------------------
# 雌雄双股剑：SELECT_OPTION（选择权在目标）+ 性别判据
# ---------------------------------------------------------------------------


def test_double_sword_offers_the_choice_to_the_target_of_opposite_gender():
    other = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(other,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, other),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == "SELECT_OPTION":
            return _find(query, lambda action: isinstance(action, SelectOption) and action.choice == "draw_one")
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    option_query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_OPTION"
    )
    assert option_query.options == ("discard_one", "draw_one")
    assert option_query.actor == 1, "选择权在目标"
    assert any(
        record.kind == "action"
        and getattr(record.action, "kind", None) == "SelectOption"
        and record.action.choice == "draw_one"
        for record in engine.records
    )


def test_double_sword_hides_the_discard_option_when_the_target_has_no_hand_cards():
    """付不起的组合不进候选（票 19 §6）：目标无手牌 ⇒ 只剩"令你摸一张"。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    option_query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_OPTION"
    )
    assert option_query.options == ("draw_one",)


def test_double_sword_does_not_trigger_for_same_gender_targets():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD),
        turn_seat=0,
    )

    engine = _run(
        state,
        [
            Scripted(lambda q: _slash_use(q, SLASH, 1) if q.kind == "PLAY_PHASE" else None),
            Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert not _has_query(engine, "SELECT_OPTION")
    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.double_sword")


# ---------------------------------------------------------------------------
# 八卦阵：需要【闪】的提供者序列 + 惰性判定（帧内接缝）
# ---------------------------------------------------------------------------


def test_eight_diagrams_is_offered_once_in_the_dodge_window():
    state = build_state(
        pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(EIGHT_DIAGRAMS,))], turn_seat=0
    )

    offered = legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)
    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == "equip.eight_diagrams"
        for action in offered
    ), "提供者序列把八卦阵追加进需要【闪】的候选"

    used = legal_actions_for(
        state,
        1,
        QueryKind.DODGE,
        answers_seq=1,
        payload=DodgeContext(providers_used=("equip.eight_diagrams",)),
    )
    assert not any(isinstance(action, ActivateSkill) for action in used), (
        "每提供者只问一次：问过之后不再提供"
    )


def test_a_red_eight_diagrams_judgment_satisfies_the_dodge():
    red = _first_by_color("slash", black=False)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(equip=(EIGHT_DIAGRAMS,))],
        deck=_judge_deck(red, used=(SLASH, EIGHT_DIAGRAMS)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert engine.state.player(1).hp == 4, "红色判定满足需要 ⇒ 【杀】被抵消"
    assert any(
        record.kind == "timing" and record.timing is Timing.JUDGE_CARD_AFTER_EFFECT
        for record in engine.records
    ), "选中才惰性压判定帧"


def test_a_black_eight_diagrams_judgment_falls_through_to_the_real_dodge_window():
    black = _first_by_color("slash", black=True)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(equip=(EIGHT_DIAGRAMS,))],
        deck=_judge_deck(black, used=(SLASH, EIGHT_DIAGRAMS)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    dodges = {"count": 0}

    def defender(query):
        if query.kind == "DODGE":
            dodges["count"] += 1
            if dodges["count"] == 1:
                return _find(query, lambda action: isinstance(action, ActivateSkill))
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert dodges["count"] == 2, "黑色判定 ⇒ 该提供者机会用尽、重问真牌窗口"
    second = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "DODGE"
    ][1]
    assert not any(isinstance(action, ActivateSkill) for action in second.legal_actions)
    assert engine.state.player(1).hp == 3, "没闪 ⇒ 照常受伤"


# ---------------------------------------------------------------------------
# 青龙偃月刀 / 贯石斧：【杀】被抵消时的帧内接缝
# ---------------------------------------------------------------------------


def test_green_dragon_blade_uses_another_slash_on_the_same_target():
    second_slash = _cards("slash")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, second_slash), equip=(GREEN_DRAGON,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, second_slash, GREEN_DRAGON, dodge),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1) or _slash_use(query, second_slash, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    dodges = {"used": False}

    def defender(query):
        if query.kind == "DODGE" and not dodges["used"]:
            dodges["used"] = True
            return _find(query, lambda action: isinstance(action, UseCard))
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade")
    assert engine.state.player(1).hp == 3, "续杀无距离限制、命中造成 1 点伤害"
    assert engine.state.player(0).card_use_counts.get("slash", 0) == 1, (
        "续杀不计次数（票 12）：本回合【杀】的计数只算最初那张"
    )


def test_axe_discards_two_cards_to_keep_the_damage_after_a_dodge():
    extra = _cards("peach")[0]
    extra2 = _cards("peach")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra, extra2), equip=(AXE,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, extra, extra2, AXE, dodge),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill) and len(action.picks) == 2)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.axe")
    assert engine.state.player(1).hp == 3, "弃两张后此【杀】依然造成伤害"
    discarded = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DISCARD"
        and record.from_zone.seat == 0
    ]
    assert sum(len(record.cards) for record in discarded) == 2, "成本恰是两张牌"
    assert AXE in engine.state.player(0).equip, "武器本身不能当成本"

    resolve_id = next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.RESOLVE
    )
    timings = [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.frame_id == resolve_id
    ]
    assert Timing.RESOLVE_EFFECT not in timings, "贯石斧跳『生效时』"
    assert Timing.RESOLVE_AFTER_EFFECT in timings, "直进『生效后』"


def test_serpent_spear_transformed_play_ignores_the_slash_limit():
    """打出不占使用次数（票 19 §2）：次数用尽仍可从候选里打出转化【杀】。"""
    from sgs.state import RespondContext, RespondMode, WindowCause

    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 99  # 次数已用尽

    played = legal_actions_for(
        state,
        0,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BARBARIAN_INVASION, required="slash", mode=RespondMode.PLAY
        ),
    )
    used = legal_actions_for(
        state,
        0,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BORROWED_SWORD,
            required="slash",
            mode=RespondMode.USE,
            use_targets=(1,),
        ),
    )

    assert any(
        isinstance(action, PlayCard) and action.card.card_id < 0 for action in played
    ), "打出不计次数 ⇒ 转化【杀】仍在候选中"
    assert not any(
        isinstance(action, UseCard) and action.card.card_id < 0 for action in used
    ), "使用仍受次数限制"


def test_eight_diagrams_is_offered_in_the_arrow_barrage_dodge_window():
    """票 10 §5.5：两个"需要【闪】"seam 共用同一套嵌法（``DODGE`` 与【万箭齐发】）。"""
    from sgs.state import RespondContext, RespondMode, WindowCause

    state = build_state(
        pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(EIGHT_DIAGRAMS,))], turn_seat=0
    )

    actions = legal_actions_for(
        state,
        1,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.ARROW_BARRAGE, required="dodge", mode=RespondMode.PLAY
        ),
    )

    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == "equip.eight_diagrams"
        for action in actions
    )


def test_a_cost_that_cannot_be_paid_produces_no_activation_candidate():
    """付不起 ⇒ 候选为空、不构造询问（票 19 §6 的候选生成契约）。"""
    from sgs.effects import legal_actions_for
    from sgs.state import SkillActivationContext

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    empty_face = SkillActivationContext(
        skill_id="equip.axe", optional=True, min_picks=2, max_picks=2, slots=()
    )

    actions = legal_actions_for(
        state, 0, QueryKind.SKILL_ACTIVATION, answers_seq=1, payload=empty_face
    )

    assert not any(isinstance(action, ActivateSkill) for action in actions)
    assert all(isinstance(action, Pass) for action in actions)


# ---------------------------------------------------------------------------
# 丈八蛇矛：转化牌 / 虚拟牌
# ---------------------------------------------------------------------------


def test_a_transformed_slash_is_virtual_has_no_rank_and_inherits_a_common_color():
    from sgs.data import color_of_suit

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    blacks = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    ]
    reds = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
    ]
    black_one, black_two = blacks[0], blacks[1]
    red_one = reds[0]

    same_color = transformed_slash(state, (black_one, black_two))
    mixed = transformed_slash(state, (black_one, red_one))

    assert same_color.is_virtual and same_color.card_id < 0
    assert same_color.rank is None and same_color.suit is None
    assert same_color.color is Color.BLACK, "同色 ⇒ 有颜色但无花色"
    assert same_color.virtual_sources == tuple(sorted((black_one, black_two)))
    assert mixed.color is None, "异色 ⇒ 无色"
    assert transformed_slash(state, (black_one, black_two)).card_id == same_color.card_id, (
        "同一组来源恒得同一个负 id（确定性）"
    )


def test_virtual_card_id_namespaces_do_not_collide():
    from sgs.skills.virtual import virtual_card_id, virtual_dodge

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    assert virtual_dodge(state).card_id != transformed_slash(state, (0, 1)).card_id, (
        "纯虚拟牌与来源 (0,1) 的转化牌不得撞 id"
    )
    assert virtual_card_id(kind=0, sources=(0, 1), card_count=108) != virtual_card_id(
        kind=1, sources=(), card_count=108
    )


def test_serpent_spear_offers_a_transformed_slash_candidate():
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    virtual = [
        action
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id < 0
    ]

    assert virtual, "装备丈八蛇矛且有两张手牌 ⇒ 追加转化【杀】候选"
    assert virtual[0].card.virtual_sources == (first, second)


def test_serpent_spear_consumes_both_source_cards_and_stays_out_of_conservation():
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.virtual_sources == (first, second),
            )
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    both = [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [first, second]
    ]
    assert both == [
        ("hand", "processing", "USE"),
        ("processing", "discard", "USE"),
    ], "一条 delta 带两张来源牌（票 12 §Q21）"
    assert engine.state.player(1).hp == 3

    everywhere = [
        *engine.state.deck,
        *engine.state.discard,
        *engine.state.processing,
        *(card_id for player in engine.state.players for card_id in player.hand),
        *(card_id for player in engine.state.players for card_id in player.equip),
        *(card_id for player in engine.state.players for card_id in player.judge),
    ]
    assert sorted(everywhere) == list(range(len(PACK.deck.cards))), (
        "虚拟牌不参与牌守恒：两张来源实体牌仍恰在一处"
    )


# ---------------------------------------------------------------------------
# 技能实例模型（票 07 §6）：键 / 去重 / 来源清理 / 虚拟装备并集
# ---------------------------------------------------------------------------


def test_skill_instances_are_keyed_by_owner_skill_and_source():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,)), SeatSpec(equip=(CROSSBOW,))],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert len(instances) == 2, "两个人各拿同一个技能 ⇒ 两个实例，互不串状态"
    keys = sorted(instance_key(instance) for instance in instances)
    assert keys == [
        f"0:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
        f"1:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
    ]


def test_a_virtual_grant_is_deduped_behind_the_earliest_surviving_source():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(CROSSBOW,), virtual_equipment=("equip.crossbow",)),
            SeatSpec(),
        ],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert [instance.owner for instance in instances] == [0]
    assert instances[0].source.card_id == CROSSBOW, "实体装备来源在虚拟授予之前 ⇒ 胜出"


def test_prune_skill_state_drops_state_whose_source_is_gone():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    state.skill_state["0:equip.crossbow:EQUIPMENT_CARD:0"] = 3

    prune_skill_state(state)

    assert state.skill_state == {}, "来源（装备）已不在 ⇒ per-instance 状态随 source 清理"


def test_is_equipped_reads_the_physical_equipment_zone():
    state = build_state(
        pack=PACK, seats=[SeatSpec(equip=(SERPENT,)), SeatSpec()], turn_seat=0
    )

    from sgs.state import SlotKind

    assert is_equipped(state, 0, SlotKind.WEAPON) is True
    assert is_equipped(state, 0, SlotKind.ARMOR) is False
    assert is_equipped(state, 1, SlotKind.WEAPON) is False


def test_virtual_equipment_unions_with_physical_equipment_without_occupying_a_zone(monkeypatch):
    """虚拟装备不占牌守恒、与实体装备并集决定"已装备"（票 07 §6 / 票 30）。

    M2 没有虚拟装备的生产者，故用 monkeypatch 装一个带 ``virtual_slot`` 的假技能，
    只验证**并集判定**与"不进装备区"两条结构事实。
    """
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import catalog as catalog_module
    from sgs.skills.catalog import SkillDef
    from sgs.state import SlotKind

    fake = SkillDef(
        skill_id="test.virtual_armor",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        virtual_slot=SlotKind.ARMOR,
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, fake.skill_id: fake}),
    )
    state = build_state(
        pack=PACK, seats=[SeatSpec(virtual_equipment=(fake.skill_id,))], turn_seat=0
    )

    assert is_equipped(state, 0, SlotKind.ARMOR) is True, "虚拟授予也算已装备"
    assert is_equipped(state, 0, SlotKind.WEAPON) is False
    assert state.player(0).equip == [], "虚拟装备不是牌、不进装备区"
    assert all(
        instance.source.kind.value == "VIRTUAL_GRANT"
        for instance in resolve_instances(state)
    )


def test_every_equipment_card_has_a_skill_and_vice_versa():
    from sgs.skills import EQUIPMENT_CARD_TYPE_IDS

    assert set(CATALOG) == {f"equip.{card_type}" for card_type in EQUIPMENT_CARD_TYPE_IDS}


def test_the_new_query_and_action_kinds_round_trip_through_the_journal(tmp_path):
    """v2 报文白名单：``SKILL_ACTIVATION`` / ``SELECT_OPTION`` 随询问与行动落盘、可反序列化。"""
    from sgs.engine import dump_journal, read_journal

    other = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(other,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, other),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == "SELECT_OPTION":
            return _find(query, lambda action: isinstance(action, SelectOption))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )
    text = engine.journal()
    path = tmp_path / "game.jsonl"
    path.write_text(text, encoding="utf-8")

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.double_sword")
    assert _has_query(engine, "SELECT_OPTION")
    assert dump_journal(read_journal(path)) == text
