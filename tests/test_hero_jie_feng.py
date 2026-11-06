"""界限突破-风包·移动版：界黄忠移动版【烈弓】（jie-feng-batch 票 01）。

牌面（用户 `D:\\SGS_HEROS\\界限突破-风包\\界黄忠\\技能.txt`，逐字；用户 2026-11-06 签字轮：
**口径 = 移动版**、① = **并集**、无点数【杀】= **fail-closed（① 不生效）**）：

    烈弓
    　①你对你计算与其距离不大于你的【杀】点数的其他角色使用此【杀】无距离限制；
    　②当你使用【杀】指定一名角色为目标后，你可以根据下列条件执行相应的效果：
        1.若你的手牌数不小于其手牌数，该角色不能响应此【杀】；
        2.若你的体力值不大于其体力值，此【杀】对其造成的基础伤害+1。

本文件钉住的判据（详见票面 §1.5 / §1.8 / §1.11）：

1. ① 的 `TARGET_POOL` 并集只补"距 ≤ 点数"的座位，**无点数不补**；链上【空城】仍能剔除；
2. ② 的「不能响应」是**逐目标**帧标签 `Tag.UNRESPONSIVE`（【八卦阵】一并封）；
3. ② 的「+1」按**本次使用 + 该目标**记账（`Player.liegong_bonus`），`RESOLVE_AFTER_END`
   清扫；不会漏到本回合后续的【杀】；
4. 界版牌面没有「出牌阶段内」⇒ 借刀杀人逼出的【杀】也触发（标版不触发）；
5. ② 的条件在**触发时**现算并定格（条件 1 落帧标签、条件 2 落记账）。
"""

from __future__ import annotations

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import check_targets
from sgs.engine import Engine
from sgs.skills.virtual import transformed_card
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    Phase,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_queries,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")
MILITARY = load_content_pack(deck_profile="standard_ex_160")

JIE_LIEGONG = "hero.jie_huangzhong_mobile.liegong"
JIE_HUANGZHONG = "jie_huangzhong_mobile"
HUANGZHONG = "huangzhong"
EIGHT_DIAGRAMS = "equip.eight_diagrams"


def _cards(type_id: str) -> list[int]:
    """某牌名的全部 ``card_id``（标准 108 档）。"""
    return cards_of_type(PACK, type_id)


def _military_cards(type_id: str) -> list[int]:
    return cards_of_type(MILITARY, type_id)


def _candidates(query):
    """一条询问的全部候选：``legal_actions`` ∪ 附带指定池（借刀杀人的"a 出【杀】"）。"""
    return (*query.legal_actions, *getattr(query, "designation_actions", ()))


def _find(query, predicate):
    for action in _candidates(query):
        if predicate(action):
            return action
    return None


def _queries(engine, kind: str):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _asks(engine, skill_id: str = JIE_LIEGONG):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == skill_id
    ]


class Script:
    """一个座位的最小脚本：按顺序出牌；【烈弓】按 ``activate`` 应答。"""

    def __init__(
        self,
        *,
        plays=(),
        activate=True,
        dodge=False,
        skill_id: str = JIE_LIEGONG,
    ) -> None:
        self.plays = [tuple(play) for play in plays]
        self.activate = activate
        self.dodge = dodge
        self.skill_id = skill_id
        self.asks = 0

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.plays:
            card, targets, designations = self.plays[0]
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == card
                and tuple(action.targets) == tuple(targets)
                and tuple(action.designations) == tuple(designations),
            )
            if action is not None:
                self.plays.pop(0)
                return action
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == self.skill_id:
            allowed = self.activate
            if isinstance(allowed, (list, tuple)):
                allowed = self.asks < len(allowed) and bool(allowed[self.asks])
            self.asks += 1
            if allowed:
                return _find(query, lambda action: isinstance(action, ActivateSkill)) or (
                    query.legal_actions[0]
                )
            return _find(query, lambda action: isinstance(action, Pass)) or (
                query.legal_actions[0]
            )
        if getattr(query, "cause", None) is WindowCause.BORROWED_SWORD:
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == "slash",
                )
                or _find(query, lambda action: isinstance(action, Pass))
                or query.legal_actions[0]
            )
        if query.kind == "DODGE":
            if self.dodge:
                return _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == "dodge",
                ) or query.legal_actions[0]
            return _find(query, lambda action: isinstance(action, Pass)) or (
                query.legal_actions[0]
            )
        if query.kind == "PLAY_PHASE":
            return (
                _find(query, lambda action: isinstance(action, EndPhase))
                or query.legal_actions[0]
            )
        if query.kind in ("RESPOND", "PEACH"):
            return _find(query, lambda action: isinstance(action, Pass)) or (
                query.legal_actions[0]
            )
        return query.legal_actions[0]


def _to_play_phase(engine) -> bool:
    return any(
        record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
        for record in engine.records
    )


def _controlled_use(
    *,
    pack=PACK,
    card: int,
    owner_hand=(),
    owner_hp: int = 4,
    target_hand=(),
    target_hp: int = 4,
    target_equip=(),
    activate=True,
    target_dodge=False,
    extra_seats=(),
    used=(),
    wine: bool = False,
):
    """座位 0 = 界黄忠（自己的回合）、座位 1 = 目标；**到出牌阶段后再摆好两边的手牌 / 体力**。

    从开局跑会经过摸牌阶段（摸两张），手牌数因此不可预知 —— 与真局一致，故先跑到
    ``PHASE_PLAY_START``，再把两边的手牌 / 体力改成测试要的精确值，然后出这一张【杀】。
    """
    seats = [
        SeatSpec(hero=JIE_HUANGZHONG),
        SeatSpec(equip=tuple(target_equip)),
        *extra_seats,
    ]
    used_cards = [card, *owner_hand, *target_hand, *target_equip, *used]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck_excluding(pack, *used_cards),
        turn_seat=0,
    )
    players = [
        Script(plays=((card, (1,), ()),), activate=activate),
        Script(dodge=target_dodge),
        *[Script() for _ in extra_seats],
    ]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, _to_play_phase)
    owner = engine.state.player(0)
    owner.hand[:] = list(owner_hand)
    owner.hp = owner_hp
    target = engine.state.player(1)
    target.hand[:] = list(target_hand)
    target.hp = target_hp
    if wine:
        owner.wine_active = True
    run_until(engine, players, lambda e: card in e.state.discard and not e.stack)
    return engine


# ---------------------------------------------------------------------------
# 数据面
# ---------------------------------------------------------------------------


def test_jie_huangzhong_mobile_is_in_the_default_pool() -> None:
    """界黄忠移动版：真名「黄忠」、显示名带「移动版」后缀、蜀 / 男 / 4。"""
    from sgs.data import hero_by_id

    hero = hero_by_id(JIE_HUANGZHONG)
    assert hero is not None
    assert hero.name == "界黄忠移动版"
    assert hero.true_name == "黄忠"
    assert hero.version == "jie-feng-mobile"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["liegong"]


# ---------------------------------------------------------------------------
# ①：距离豁免（并集；无点数不补；空城仍生效）
# ---------------------------------------------------------------------------


def _wide_state(*, hand: tuple[int, ...]):
    """六座环：0 号是界黄忠（其余五座距离 1/2/3/2/1）。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_HUANGZHONG, hand=hand), *[SeatSpec() for _ in range(5)]],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=6),
    )


def test_jie_liegong_no_distance_exempts_a_target_beyond_the_attack_range() -> None:
    """♣3【杀】（点数 3）：距离 3 的座位进池（正常攻击范围只有 1）。"""
    slash = next(card for card in _cards("slash") if PACK.deck.cards[card].rank == 3)
    state = _wide_state(hand=(slash,))

    pool = check_targets(state, 0, state.card(slash))

    assert 3 in pool, "距 3 ≤ 点数 3 ⇒ 并集把座位 3 补进池"
    assert set(pool) == {1, 2, 3, 4, 5}, "其余座位照常"


def test_jie_liegong_no_distance_still_needs_the_rank() -> None:
    """♣2【杀】（点数 2）：距离 3 的座位**不进池**（并集只补距 ≤ 点数）。"""
    slash = next(card for card in _cards("slash") if PACK.deck.cards[card].rank == 2)
    state = _wide_state(hand=(slash,))

    pool = check_targets(state, 0, state.card(slash))

    assert 3 not in pool, "距 3 > 点数 2 ⇒ ① 不补"
    assert set(pool) == {1, 2, 4, 5}


def test_jie_liegong_does_not_readd_the_kongcheng_target() -> None:
    """并集先跑、【空城】（`hero.jie_zhugeliang.kongcheng`）后跑 ⇒ 0 手牌的界诸葛亮仍不可选。"""
    slash = next(card for card in _cards("slash") if PACK.deck.cards[card].rank == 3)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_HUANGZHONG, hand=(slash,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(hero="jie_zhugeliang"),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        rule_config=RuleConfig(player_count=6),
    )

    pool = check_targets(state, 0, state.card(slash))

    assert 3 not in pool, "【空城】在并集之后删除目标（并集写法不能把它加回来）"


def test_jie_liegong_no_distance_does_not_leak_to_other_seats() -> None:
    """别人使用【杀】时 ① 不生效（`ctx.owner != ctx.subject`）。"""
    slash = next(card for card in _cards("slash") if PACK.deck.cards[card].rank == 3)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_HUANGZHONG),
            SeatSpec(hand=(slash,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        rule_config=RuleConfig(player_count=6),
    )

    pool = check_targets(state, 1, state.card(slash))

    assert 3 not in pool, "座位 1 的池不因界黄忠的 ① 放宽"


def test_jie_liegong_leaves_the_pool_alone_without_a_rank() -> None:
    """丈八两张手牌转化【杀】：点数恒无 ⇒ ① 不生效（`01a` 裁定 A，fail-closed）。"""
    first, second = _cards("slash")[0], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_HUANGZHONG, hand=(first, second)),
            *[SeatSpec() for _ in range(5)],
        ],
        deck=deck_excluding(PACK, first, second),
        turn_seat=0,
        rule_config=RuleConfig(player_count=6),
    )
    virtual = transformed_card(state, (first, second), "slash")

    pool = check_targets(state, 0, virtual)

    assert virtual.rank is None
    assert set(pool) == {1, 5}, "无点数 ⇒ 只留攻击范围 1 内的座位"


# ---------------------------------------------------------------------------
# ②：逐目标封【闪】/ 逐目标 +1（记账；本次使用）
# ---------------------------------------------------------------------------


def test_jie_liegong_seals_the_dodge_window_when_the_hand_count_is_not_less() -> None:
    """条件 1：你的手牌数 ≥ 其手牌数 ⇒ 该目标没有【闪】窗口。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash, peach),
        owner_hp=4,
        target_hand=(dodge,),
        target_hp=3,
        activate=True,
        target_dodge=True,
    )

    assert len(_asks(engine)) == 1, "条件成立 ⇒ 问一次"
    assert _queries(engine, "DODGE") == [], "条件 1 ⇒ 连【闪】窗口都不出现"
    assert engine.state.player(1).hp == 2


def test_jie_liegong_adds_one_damage_when_the_hp_is_not_greater() -> None:
    """条件 2：你的体力值 ≤ 其体力值 ⇒ 此【杀】对其伤害 +1（1 → 2）。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash,),
        owner_hp=2,
        target_hand=(dodge, peach, _cards("dodge")[1]),
        target_hp=3,
        activate=True,
        target_dodge=False,
    )

    assert len(_asks(engine)) == 1
    assert len(_queries(engine, "DODGE")) == 1, "条件 1 不成立 ⇒ 窗口照常"
    assert engine.state.player(1).hp == 1, "3 - (1 + 1) = 1"


def test_declining_jie_liegong_leaves_both_effects_off() -> None:
    """放弃 ⇒ 窗口照常、伤害仍为 1。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash,),
        owner_hp=2,
        target_hand=(dodge, peach, _cards("dodge")[1]),
        target_hp=3,
        activate=False,
        target_dodge=False,
    )

    assert len(_asks(engine)) == 1, "条件成立 ⇒ 仍问一次"
    assert len(_queries(engine, "DODGE")) == 1
    assert engine.state.player(1).hp == 2, "不发动 ⇒ 只受 1 点"


def test_jie_liegong_is_not_offered_when_no_branch_holds() -> None:
    """两个条件都不成立 ⇒ 一条询问都不发。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash,),
        owner_hp=4,
        target_hand=(dodge, peach, _cards("dodge")[1]),
        target_hp=2,
        activate=True,
        target_dodge=True,
    )

    assert _asks(engine) == [], "条件全不成立 ⇒ 连问都不问"
    assert len(_queries(engine, "DODGE")) == 1
    assert engine.state.player(1).hp == 2, "【闪】照常抵消"


def test_jie_liegong_seals_only_the_qualified_targets() -> None:
    """【方天画戟】多目标：只封满足条件 1 的那个；条件 2 的 +1 逐目标各自生效。"""
    slash, halberd = _cards("slash")[0], _cards("halberd")[0]
    dodges = tuple(_cards("dodge")[:3])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_HUANGZHONG, hp=2, equip=(halberd,)),
            SeatSpec(hp=4, hand=dodges),
            SeatSpec(hp=4),
        ],
        deck=deck_excluding(PACK, slash, halberd, *dodges),
        turn_seat=0,
    )
    players = [
        Script(plays=((slash, (1, 2), ()),)),
        Script(dodge=True),
        Script(),
    ]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, _to_play_phase)
    hand = engine.state.player(0).hand
    hand.clear()
    hand.append(slash)
    run_until(engine, players, lambda e: slash in e.state.discard and not e.stack)

    assert len(_asks(engine)) == 1, "多目标只问一次"
    assert len(_queries(engine, "DODGE")) == 1, "只封条件 1 成立的目标 2"
    assert engine.state.player(1).hp == 4, "目标 1 闪掉（条件 1 不成立）"
    assert engine.state.player(2).hp == 2, "目标 2 被 2 点伤害（1 + 条件 2 的 +1）"


def test_jie_liegong_seals_the_eight_diagrams_provider() -> None:
    """封的是响应窗口：【八卦阵】的「视为使用【闪】」也不出现。"""
    slash, eight = _cards("slash")[0], _cards("eight_diagrams")[0]
    peach, dodge = _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash, peach),
        owner_hp=4,
        target_hand=(dodge,),
        target_hp=3,
        target_equip=(eight,),
        activate=True,
        target_dodge=True,
    )

    assert _queries(engine, "DODGE") == []
    assert engine.state.player(1).hp == 2


def test_jie_liegong_damage_bonus_does_not_leak_to_a_later_slash() -> None:
    """本次使用结束就清账：第 2 张【杀】（不发动）对同一目标只造成 1 点。"""
    slash_one, slash_two = _cards("slash")[0], _cards("slash")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_HUANGZHONG), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, slash_one, slash_two),
        turn_seat=0,
    )
    players = [
        Script(
            plays=((slash_one, (1,), ()), (slash_two, (1,), ())),
            activate=(True, False),
        ),
        Script(),
    ]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, _to_play_phase)
    owner = engine.state.player(0)
    owner.hand[:] = [slash_one, slash_two]
    owner.hp = 2
    target = engine.state.player(1)
    target.hand.clear()
    target.hp = 4
    run_until(engine, players, lambda e: slash_two in e.state.discard and not e.stack)

    assert len(_asks(engine)) == 2, "两次使用各问一次"
    assert engine.state.player(1).hp == 1, "4 - (1+1) - 1 = 1（第 2 张不发动）"


def test_jie_liegong_clears_the_bonus_after_a_dodged_slash() -> None:
    """被【闪】抵消 ⇒ 本次使用结束（`RESOLVE_AFTER_END`）仍清账。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash, peach),
        owner_hp=2,
        target_hand=(dodge, _cards("dodge")[1], _cards("dodge")[2]),
        target_hp=3,
        activate=True,
        target_dodge=True,
    )

    assert len(_queries(engine, "DODGE")) == 1
    assert engine.state.player(1).hp == 3, "【闪】抵消"
    assert engine.state.player(0).liegong_bonus is None, "记账已清（不滞留到下一次使用）"


def test_jie_liegong_bonus_stacks_with_wine() -> None:
    """【酒】的 +1 与 ② 的 +1 相加（数值只有加减）：1 + 1 + 1 = 3。"""
    slash, peach, dodge = _cards("slash")[0], _cards("peach")[0], _cards("dodge")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash,),
        owner_hp=2,
        target_hand=(dodge, peach, _cards("dodge")[1]),
        target_hp=4,
        activate=True,
        target_dodge=False,
        wine=True,
    )

    assert engine.state.player(1).hp == 1, "4 - 3 = 1"


def test_jie_liegong_is_still_offered_without_visible_benefit() -> None:
    """条件成立但目标没有【闪】⇒ 仍然问（玩家可以浪费）。"""
    slash, peach = _cards("slash")[0], _cards("peach")[0]
    engine = _controlled_use(
        card=slash,
        owner_hand=(slash, peach),
        owner_hp=4,
        target_hand=(),
        target_hp=3,
        activate=True,
    )

    assert len(_asks(engine)) == 1


def test_jie_liegong_applies_to_fire_slash_but_not_to_duel() -> None:
    """【杀】族判据：火【杀】触发；【决斗】不是【杀】⇒ 不问。"""
    fire_slash = _military_cards("fire_slash")[0]
    dodge_one, dodge_two = _military_cards("dodge")[0], _military_cards("dodge")[1]
    dodge_three = _military_cards("dodge")[2]
    fire_engine = _controlled_use(
        pack=MILITARY,
        card=fire_slash,
        owner_hand=(fire_slash,),
        owner_hp=2,
        target_hand=(dodge_one, dodge_two, dodge_three),
        target_hp=3,
        activate=True,
        target_dodge=False,
    )

    assert len(_asks(fire_engine)) == 1, "火【杀】同族 ⇒ 照常问"
    assert fire_engine.state.player(1).hp == 1, "3 - (1 + 1) = 1"

    duel = _cards("duel")[0]
    duel_engine = _controlled_use(
        card=duel,
        owner_hand=(duel,),
        owner_hp=2,
        target_hand=(_cards("peach")[0], _cards("peach")[1], _cards("peach")[2]),
        target_hp=3,
        activate=True,
    )

    assert _asks(duel_engine) == [], "【决斗】不是【杀】⇒ 不问"


def test_jie_liegong_fires_in_another_turn_where_the_standard_one_does_not() -> None:
    """界版没有「出牌阶段内」⇒ 借刀杀人逼出的【杀】也触发；标版不触发。"""
    borrowed, crossbow, slash = (
        _cards("borrowed_sword")[0],
        _cards("crossbow")[0],
        _cards("slash")[0],
    )
    dodge_one, dodge_two = _cards("dodge")[0], _cards("dodge")[1]

    def run(hero: str):
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(borrowed,)),
                SeatSpec(hp=2, hand=(slash,), equip=(crossbow,), hero=hero),
                SeatSpec(hp=4, hand=(dodge_one, dodge_two)),
            ],
            deck=deck_excluding(
                PACK, borrowed, crossbow, slash, dodge_one, dodge_two
            ),
            turn_seat=0,
        )
        players = [
            Script(plays=((borrowed, (1,), (2,)),)),
            Script(
                skill_id=(
                    JIE_LIEGONG
                    if hero == JIE_HUANGZHONG
                    else "hero.huangzhong.liegong"
                )
            ),
            Script(dodge=True),
        ]
        engine = Engine.from_state(state, seed=1)
        run_until(
            engine,
            players,
            lambda e: slash in e.state.discard
            and borrowed in e.state.discard
            and not e.stack,
        )
        return engine

    jie_engine = run(JIE_HUANGZHONG)
    standard_engine = run(HUANGZHONG)

    assert len(_asks(jie_engine)) == 1, "界版：别人回合里的被迫【杀】也触发"
    assert _asks(standard_engine, "hero.huangzhong.liegong") == [], "标版：不触发"
