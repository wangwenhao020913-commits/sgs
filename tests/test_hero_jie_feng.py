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

from sgs.data import Identity, Kingdom, RuleConfig, load_content_pack
from sgs.effects import check_targets, legal_actions_for
from sgs.engine import Engine
from sgs.skills.virtual import transformed_card
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    QueryKind,
    SelectCards,
    SelectOption,
    SelectOptions,
    SubstituteJudgeCard,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
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
        options=(),
        option_prefix: str = "",
    ) -> None:
        self.plays = [tuple(play) for play in plays]
        self.activate = activate
        self.dodge = dodge
        self.skill_id = skill_id
        self.option_prefix = option_prefix
        self.options = list(options)
        self.asks = 0
        self.option_asks = 0

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
        if (
            query.kind == "SELECT_OPTION"
            and self.option_prefix
            and str(getattr(query, "skill_id", "")).startswith(self.option_prefix)
        ):
            self.option_asks += 1
            wanted = self.options.pop(0) if self.options else ""
            if wanted:
                return _find(
                    query,
                    lambda action: isinstance(action, SelectOption)
                    and action.choice == wanted,
                ) or query.legal_actions[0]
            return _find(
                query, lambda action: isinstance(action, SelectOption)
            ) or query.legal_actions[0]
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


# ===========================================================================
# jie-feng-batch 票 02：界魏延移动版【狂骨】【奇谋】
# ===========================================================================

JIE_KUANGGU = "hero.jie_weiyan_mobile.kuanggu"
JIE_QIMOU = "hero.jie_weiyan_mobile.qimou"
JIE_WEIYAN = "jie_weiyan_mobile"


def test_jie_weiyan_mobile_is_in_the_default_pool() -> None:
    """界魏延移动版：真名「魏延」、显示名带「移动版」后缀、两条技能。"""
    from sgs.data import hero_by_id

    hero = hero_by_id(JIE_WEIYAN)
    assert hero is not None
    assert hero.name == "界魏延移动版"
    assert hero.true_name == "魏延"
    assert hero.version == "jie-feng-mobile"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["kuanggu", "qimou"]


def _kuanggu_engine(
    *,
    pack=PACK,
    card: int,
    owner_hp: int = 3,
    owner_equip=(),
    target_hp: int = 4,
    target_hand=(),
    target_equip=(),
    target: int = 1,
    seats: int = 2,
    activate=True,
    options=(),
    dodge: bool = False,
):
    """界魏延移动版出【杀】打 ``target``；到出牌阶段后摆好手牌 / 体力，跑到结算栈空。"""
    all_seats = [
        SeatSpec(hero=JIE_WEIYAN),
        *[
            SeatSpec(
                hp=target_hp if index == target else 4,
                hand=tuple(target_hand) if index == target else (),
                equip=tuple(target_equip) if index == target else (),
            )
            for index in range(1, seats)
        ],
    ]
    used = [card, *owner_equip, *target_hand, *target_equip]
    state = build_state(
        pack=pack,
        seats=all_seats,
        deck=deck_excluding(pack, *used),
        turn_seat=0,
    )
    players = [
        Script(
            plays=((card, (target,), ()),),
            activate=activate,
            skill_id=JIE_KUANGGU,
            options=options,
            option_prefix=JIE_KUANGGU + ":opt:",
        ),
        *[Script(dodge=dodge) for _ in range(seats - 1)],
    ]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, _to_play_phase)
    owner = engine.state.player(0)
    owner.hand[:] = [card]
    owner.hp = owner_hp
    owner.equip[:] = list(owner_equip)
    victim = engine.state.player(target)
    victim.hand[:] = list(target_hand)
    victim.hp = target_hp
    run_until(engine, players, lambda e: card in e.state.discard and not e.stack)
    return engine


SLASH = _cards("slash")[0]
DODGE = _cards("dodge")[0]
PEACH = _cards("peach")[0]


def test_jie_kuanggu_can_choose_the_draw() -> None:
    """距离 1 造成 1 点伤害后：选「摸一张牌」⇒ 手牌 +1、体力不变。"""
    engine = _kuanggu_engine(
        card=SLASH, options=("kuanggu_draw",), owner_hp=3, target_hp=4
    )

    assert len(_asks(engine, JIE_KUANGGU)) == 1, "非锁定 ⇒ 问一次「是否发动」"
    assert engine.state.player(0).hp == 3
    assert len(engine.state.player(0).hand) == 1, "出掉【杀】后又摸一张"


def test_jie_kuanggu_can_choose_the_heal() -> None:
    """选「回复1点体力」⇒ 走 ``RECOVER`` 帧、体力 +1。"""
    engine = _kuanggu_engine(
        card=SLASH, options=("kuanggu_heal",), owner_hp=2, target_hp=4
    )

    assert engine.state.player(0).hp == 3
    assert any(
        record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
        for record in engine.records
    ), "回复走回复体力事件"


def test_jie_kuanggu_full_hp_heal_is_a_no_op() -> None:
    """满体力选回复 ⇒ 不压帧、零 delta（规则 §4.15 L426）。"""
    engine = _kuanggu_engine(
        card=SLASH, options=("kuanggu_heal",), owner_hp=4, target_hp=4
    )

    assert engine.state.player(0).hp == 4
    assert not any(
        record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
        for record in engine.records
    ), "满体力不回复 ⇒ 不产生回复事件"


def test_jie_kuanggu_both_options_stay_legal_at_full_hp() -> None:
    """满体力时两项都列出、不置灰（用户 2026-11-06 签）。"""
    engine = _kuanggu_engine(
        card=SLASH, options=("kuanggu_draw",), owner_hp=4, target_hp=4
    )

    query = next(
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and str(record.query.skill_id).startswith(JIE_KUANGGU + ":opt:")
    )
    choices = [
        action.choice
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert choices == ["kuanggu_heal", "kuanggu_draw"]
    assert list(getattr(query, "disabled", ())) == [], "不置灰"


def test_jie_kuanggu_asks_the_activation_once_per_point() -> None:
    """「1 点伤害后」⇒ 2 点伤害问两次「是否发动」。"""
    scimitar = cards_of_type(MILITARY, "ancient_scimitar")[0]
    slash = _military_cards("slash")[0]
    engine = _kuanggu_engine(
        pack=MILITARY,
        card=slash,
        owner_equip=(scimitar,),
        owner_hp=2,
        target_hp=4,
        target_hand=(),
        options=("kuanggu_draw", "kuanggu_heal"),
    )

    assert engine.state.player(1).hp == 2, "古锭刀对空手目标 +1 ⇒ 2 点伤害"
    assert len(_asks(engine, JIE_KUANGGU)) == 2, "2 点 ⇒ 两次「是否发动」"
    assert engine.state.player(0).hp == 3, "第 2 点选了回复（2 → 3）"
    assert len(engine.state.player(0).hand) == 1, "第 1 点选了摸牌"


def test_jie_kuanggu_asks_the_choice_again_for_the_second_point() -> None:
    """**本技能最重要的断言**：第 1 点选摸牌、第 2 点选回复 ⇒ 两个效果各自发生。

    把选项询问 `skill_id` 里的点号去掉 ⇒ 第 2 点命中第 1 点的答案 ⇒ 变成两次摸牌
    （手牌 2、体力不变）⇒ 本条红。
    """
    scimitar = cards_of_type(MILITARY, "ancient_scimitar")[0]
    slash = _military_cards("slash")[0]
    engine = _kuanggu_engine(
        pack=MILITARY,
        card=slash,
        owner_equip=(scimitar,),
        owner_hp=2,
        target_hp=4,
        target_hand=(),
        options=("kuanggu_draw", "kuanggu_heal"),
    )

    assert engine.state.player(0).hp == 3, "第 2 点的回复生效"
    assert len(engine.state.player(0).hand) == 1, "第 1 点的摸牌生效（不是第二次回复）"
    option_queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and str(record.query.skill_id).startswith(JIE_KUANGGU + ":opt:")
    ]
    assert len(option_queries) == 2, "每点各问一次选项"
    assert [query.skill_id for query in option_queries] == [
        f"{JIE_KUANGGU}:opt:0",
        f"{JIE_KUANGGU}:opt:1",
    ]


def test_jie_kuanggu_snapshot_hook_never_asks() -> None:
    """定格距离那条钩子是 ``forced=True``：距离 2 的伤害一条询问都不发。"""
    weapon = _cards("axe")[0]
    slash = _cards("slash")[0]
    engine = _kuanggu_engine(
        card=slash,
        owner_equip=(weapon,),
        owner_hp=3,
        target_hp=4,
        target=2,
        seats=4,
    )

    assert engine.state.player(2).hp == 3, "伤害照常结算"
    assert _asks(engine, JIE_KUANGGU) == [], "距离 2 ⇒ 条件假、连问都不问"
    assert engine.state.player(0).hp == 3


def test_jie_kuanggu_uses_the_distance_before_the_hp_deduction() -> None:
    """相邻 +【的卢】且被这一击打死：扣减体力前距离 2 ⇒ 不触发（现算会算成 1）。"""
    weapon = _cards("axe")[0]
    horse = _cards("dilu")[0]
    slash = _cards("slash")[0]
    engine = _kuanggu_engine(
        card=slash,
        owner_equip=(weapon,),
        owner_hp=3,
        target_hp=1,
        target_equip=(horse,),
        seats=4,
    )

    assert not engine.state.player(1).alive, "这一击把目标打死了"
    assert engine.state.player(0).hp == 3, "快照距离 2 ⇒ 狂骨不触发"


# ---------------------------------------------------------------------------
# 界魏延移动版【奇谋】
# ---------------------------------------------------------------------------

from sgs.effects import ModifierContext, ModifierPoint, apply_modifiers, distance  # noqa: E402
from sgs.skills import limited_skill_used  # noqa: E402
from support import PassPlayer  # noqa: E402


class QimouScript:
    """出牌阶段点【奇谋】→ 选 X；其余询问能结束阶段就结束、否则放弃。"""

    def __init__(self, *, choice: str = "", use: bool = True) -> None:
        self.choice = choice
        self.use = use
        self.used = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.use and not self.used:
            action = _find(
                query,
                lambda action: isinstance(action, ActivateSkill)
                and action.skill_id == JIE_QIMOU,
            )
            if action is not None:
                self.used = True
                return action
        if query.kind == "SELECT_OPTION" and str(
            getattr(query, "skill_id", "")
        ).startswith("qimou:"):
            return _find(
                query,
                lambda action: isinstance(action, SelectOption)
                and action.choice == self.choice,
            ) or query.legal_actions[0]
        if query.kind == "PLAY_PHASE":
            return _find(query, lambda action: isinstance(action, EndPhase)) or (
                query.legal_actions[0]
            )
        return _find(query, lambda action: isinstance(action, Pass)) or (
            query.legal_actions[0]
        )


def _qimou_setup(*, hp: int = 4, seats: int = 2, equip=(), pack=PACK, use=True):
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero=JIE_WEIYAN, hp=hp, equip=tuple(equip)),
            *[SeatSpec() for _ in range(seats - 1)],
        ],
        deck=deck_excluding(pack, *equip),
        turn_seat=0,
    )
    script = QimouScript(use=use)
    players = [script, *[PassPlayer() for _ in range(seats - 1)]]
    engine = Engine.from_state(state, seed=1)
    return engine, players, script


def _to_play_query(engine, players):
    run_queries(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    return engine.pending


def _qimou_use(*, hp: int = 4, choice: str = "失去2点体力", seats: int = 2, equip=(), pack=PACK):
    engine, players, script = _qimou_setup(hp=hp, seats=seats, equip=equip, pack=pack)
    script.choice = choice
    _to_play_query(engine, players)
    run_queries(
        engine,
        players,
        lambda running: running.state.player(0).qimou_distance_x > 0
        or not running.state.player(0).alive
        or running.finished,
    )
    return engine, players, script


def test_jie_qimou_offers_one_option_per_hp() -> None:
    """体力 3 ⇒ X 的选项 = 1..3（没有 0 档），且按钮文本是中文。"""
    engine, players, _script = _qimou_setup(hp=3)
    query = _to_play_query(engine, players)
    action = _find(
        query,
        lambda action: isinstance(action, ActivateSkill) and action.skill_id == JIE_QIMOU,
    )
    assert action is not None, "出牌阶段应出现【奇谋】候选"
    engine.submit(action)
    while engine.pending is None:
        engine._step()

    option_query = engine.pending
    assert option_query is not None and option_query.kind == "SELECT_OPTION"
    assert option_query.skill_id == "qimou:option"
    assert [
        action.choice
        for action in option_query.legal_actions
        if isinstance(action, SelectOption)
    ] == ["失去1点体力", "失去2点体力", "失去3点体力"]


def test_jie_qimou_has_no_candidate_at_zero_hp() -> None:
    """当前体力为 0（「不屈」类技能造成的存活）⇒ 候选不出现（用户 2026-11-06 签）。"""
    engine, players, _script = _qimou_setup(hp=0)
    query = _to_play_query(engine, players)

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_QIMOU
    ], "0 体力时没有合法的「失去 1 点」⇒ 技能不能发动"


def test_jie_qimou_loses_the_chosen_hp_and_applies_both_bonuses() -> None:
    """X=2：体力 -2；本回合距离 -2（下限 1）；本阶段【杀】上限 +2。"""
    engine, _players, _script = _qimou_use(hp=4, choice="失去2点体力")

    assert engine.state.player(0).hp == 2
    assert engine.state.player(0).qimou_distance_x == 2
    assert engine.state.player(0).qimou_count_x == 2
    assert distance(engine.state, 0, 1) == 1, "距离下限 1"
    assert (
        apply_modifiers(
            ModifierPoint.COUNT_LIMIT,
            engine.state,
            ModifierContext(subject=0, card=engine.state.card(SLASH)),
            1,
        )
        == 3
    ), "基础上限 1 + X 2"


def test_jie_qimou_is_only_offered_once_per_game() -> None:
    """限定技：用完之后不再出现候选，整局位已落。"""
    engine, players, script = _qimou_use(hp=4, choice="失去2点体力")

    assert limited_skill_used(engine.state, 0, JIE_QIMOU) is True
    query = _to_play_query(engine, players)
    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_QIMOU
    ]


def test_jie_qimou_keeps_an_unlimited_slash_cap_unlimited() -> None:
    """装备【诸葛连弩】时次数上限是 ``None``（无限）⇒ +X 仍保持无限。"""
    crossbow = _cards("crossbow")[0]
    engine, _players, _script = _qimou_use(
        hp=4, choice="失去2点体力", equip=(crossbow,)
    )

    assert (
        apply_modifiers(
            ModifierPoint.COUNT_LIMIT,
            engine.state,
            ModifierContext(subject=0, card=engine.state.card(SLASH)),
            1,
        )
        is None
    ), "无限上限 +X 仍是无限"


def test_jie_qimou_distance_never_drops_below_one() -> None:
    """六座环、X=3：距离 3 的座位被压到下限 1。"""
    engine, _players, _script = _qimou_use(hp=4, choice="失去3点体力", seats=6)

    assert distance(engine.state, 0, 3) == 1


def test_jie_qimou_slash_bonus_expires_when_the_play_phase_ends() -> None:
    """「本阶段」的次数 +X 在出牌阶段结束时清；距离那一轴还在。"""
    engine, players, _script = _qimou_use(hp=4, choice="失去2点体力")

    run_queries(
        engine,
        players,
        lambda running: any(
            record.kind == "timing"
            and record.timing is Timing.PHASE_DISCARD_START
            for record in running.records
        ),
    )

    assert engine.state.player(0).qimou_count_x == 0, "本阶段结束 ⇒ 次数加成一并清"
    assert engine.state.player(0).qimou_distance_x == 2, "距离加成活到回合末"


def test_jie_qimou_distance_expires_at_turn_end() -> None:
    """「本回合」的距离 -X 在回合末清。"""
    engine, players, _script = _qimou_use(hp=4, choice="失去2点体力")

    run_queries(
        engine,
        players,
        lambda running: running.state.turn_seat == 1,
    )

    assert engine.state.player(0).qimou_distance_x == 0


def test_jie_qimou_death_from_the_cost_stops_the_skill() -> None:
    """X = 当前体力且没救回 ⇒ 技能就此结束，两段修饰都不落。"""
    engine, _players, _script = _qimou_use(hp=1, choice="失去1点体力")

    assert not engine.state.player(0).alive
    assert engine.state.player(0).qimou_distance_x == 0
    assert engine.state.player(0).qimou_count_x == 0


def test_jie_qimou_hp_loss_does_not_trigger_kuanggu() -> None:
    """「失去体力」≠「受到伤害」：奇谋的代价不触发【狂骨】。"""
    engine, _players, _script = _qimou_use(hp=4, choice="失去2点体力")

    assert _asks(engine, JIE_KUANGGU) == []


# ---------------------------------------------------------------------------
# 界夏侯渊移动版【神速】（jie-feng-batch2 票 01）
#
# 牌面（用户 2026-11-08 改后，逐字）：「回合开始时，你可以选择至多三项：1.跳过判定阶段和
# 摸牌阶段；2.跳过出牌阶段并弃置一张装备牌；3.跳过弃牌阶段并翻面。你每选择一项，便视为你
# 使用一张无距离限制的【杀】。」
#
# 本节的判据（票面 §1.11）：
# 1. 选项面是**多选**（`SELECT_OPTIONS`，任意 0~3 项）；没有可弃的装备牌时【2】置灰；
# 2. 【2】的弃牌是**代价**、立刻先付，付不起这一项作废；
# 3. 结果按牌面 1→2→3：跳阶段（第三项还翻面）→ 它那一张无距离【杀】；
# 4. 三张【杀】不计入出牌阶段的次数；跳过的判定阶段保留判定区的牌。
# ---------------------------------------------------------------------------

JIE_SHENSU = "hero.jie_xiahouyuan_mobile.shensu"
"""界夏侯渊【神速】的全限定技能 id。"""

JIE_XIAHOUYUAN = "jie_xiahouyuan_mobile"

_JIE_SHENSU_ARMOR = cards_of_type(PACK, "eight_diagrams")[0]
"""八卦阵（防具）：【2】的成本——**手牌**里的装备牌。"""

_JIE_SHENSU_HORSE = cards_of_type(PACK, "dilu")[0]
"""的卢（+1 防御马）：【2】的成本——**装备区**里的装备牌（"装备牌"含两个区）。"""

_JIE_SHENSU_HAND = tuple(cards_of_type(PACK, "dodge")[:2])
"""手牌里的两张【闪】：撑手牌数、不给任何判据。"""

_JIE_SHENSU_INDULGENCE = cards_of_type(PACK, "indulgence")[0]
"""【乐不思蜀】：放在判定区，用来钉"跳过的判定阶段不消费判定区的牌"。"""


def _jie_shensu_fallback(query):
    """其余询问取最小合法应答：有 ``Pass`` 就 ``Pass``，否则第一条。"""
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _jie_shensu_items(query, wanted):
    """这条 ``SELECT_OPTIONS`` 询问里"恰好勾中 ``wanted``"的那条候选。"""
    for action in query.legal_actions:
        if isinstance(action, SelectOptions) and tuple(action.choices) == tuple(wanted):
            return action
    raise AssertionError(
        f"多选面里勾不出 {wanted!r}："
        f"{[a.choices for a in query.legal_actions if isinstance(a, SelectOptions)]}"
    )


def _jie_shensu_seat(query, seat: int):
    """这条选座询问里 ``seat`` 对应的候选。"""
    return _find(
        query,
        lambda action: isinstance(action, SelectOption) and action.choice == f"seat:{seat}",
    )


class _JieShensuActor:
    """座位 0（界夏侯渊）的脚本：答【神速】的每一问；可选在出牌阶段再用一张真【杀】。"""

    def __init__(
        self,
        *,
        activate: bool = True,
        items=(),
        targets=(),
        picks=(),
        play_slash=(),
    ) -> None:
        self.activate = activate
        self.items = tuple(items)
        self.targets = list(targets)
        self.picks = tuple(picks)
        self.play_slash = list(play_slash)
        self.items_query = None
        self.target_queries = []
        self.cards_queries = []

    def respond(self, query):
        skill = str(getattr(query, "skill_id", "") or "")
        if query.kind == "SKILL_ACTIVATION" and skill == JIE_SHENSU:
            if self.activate:
                return _find(query, lambda action: isinstance(action, ActivateSkill))
            return _jie_shensu_fallback(query)
        if query.kind == "SELECT_OPTIONS" and skill.endswith(":items"):
            self.items_query = query
            return _jie_shensu_items(query, self.items)
        if query.kind == "SELECT_OPTION" and ":target:" in skill:
            self.target_queries.append(query)
            return _jie_shensu_seat(query, self.targets.pop(0))
        if query.kind == "SELECT_CARDS":
            self.cards_queries.append(query)
            wanted = set(self.picks)
            for action in query.legal_actions:
                if not isinstance(action, SelectCards):
                    continue
                picked = {
                    query.slots[index].card.card_id
                    for index in action.picks
                    if query.slots[index].card is not None
                }
                if picked == wanted:
                    return action
            raise AssertionError(f"弃牌面里选不出 {sorted(wanted)}")
        if query.kind == "PLAY_PHASE":
            if self.play_slash:
                target = self.play_slash.pop(0)
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == "slash"
                        and action.targets == (target,)
                    ):
                        return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return _jie_shensu_fallback(query)


def _jie_shensu_engine(seats, *, used=(), players=None, turn_seat=0, seed=29, stop=None):
    """造「座位 0 是界夏侯渊、从自己的回合起点开跑」的局面，默认跑到弃牌阶段开始。"""
    deck = tuple(card_id for card_id in deck_excluding(PACK, *used))
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=seed)
    actors = list(players) if players is not None else []
    while len(actors) < len(seats):
        actors.append(PassPlayer())
    run_queries(
        engine,
        actors,
        stop or (lambda e: ran_timing(e, Timing.PHASE_DISCARD_START)),
    )
    return engine, state


def _jie_shensu_uses(engine) -> int:
    """本局压过几个 ``USE`` 帧。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.USE
    )


def _jie_shensu_activation_queries(engine):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == JIE_SHENSU
    ]


def test_jie_xiahouyuan_mobile_is_in_the_default_pool() -> None:
    """界夏侯渊移动版进了默认池（`jie_feng_mobile` 表 + 显示名后缀）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    assert JIE_XIAHOUYUAN in enabled_hero_ids()
    hero = hero_by_id(JIE_XIAHOUYUAN)
    assert hero.name == "界夏侯渊移动版"
    assert hero.true_name == "夏侯渊"


def test_jie_shensu_asks_once_at_own_turn_start_and_declines_cleanly() -> None:
    """自己的回合开始时问一次；放弃 ⇒ 什么都不跳、一张【杀】都没有。"""
    actor = _JieShensuActor(activate=False)
    engine, state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=_JIE_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_JIE_SHENSU_HAND,
        players=[actor],
    )

    assert len(_jie_shensu_activation_queries(engine)) == 1
    assert ran_timing(engine, Timing.PHASE_JUDGE), "没发动 ⇒ 判定阶段照常"
    assert ran_timing(engine, Timing.PHASE_DRAW), "没发动 ⇒ 摸牌阶段照常"
    assert _jie_shensu_uses(engine) == 0
    assert actor.items_query is None
    assert len(state.player(0).hand) == 4, "没发动 ⇒ 正常摸两张"


def test_jie_shensu_does_not_trigger_on_another_turn_start() -> None:
    """别人的回合开始时**不问**（自校：只有当前回合角色的那一份实例被问）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_XIAHOUYUAN), SeatSpec(hand=_JIE_SHENSU_HAND)],
        deck=deck_excluding(PACK, *_JIE_SHENSU_HAND),
        turn_seat=1,
        phase=Phase.PREPARE,
        rule_config=RuleConfig(player_count=2, starting_hand_size=4, first_seat=1),
    )
    engine = Engine.from_state(state, seed=29)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DISCARD_START),
    )

    assert not _jie_shensu_activation_queries(engine)
    assert ran_timing(engine, Timing.TURN_START), "回合确实开始了，只是不是他的"


def test_jie_shensu_items_step_is_a_multi_select_with_three_options() -> None:
    """选项面是**多选**（不是 7 档单选）：三项各自一行、可勾任意组合。"""
    actor = _JieShensuActor(items=())
    _engine, _state = _jie_shensu_engine(
        [
            SeatSpec(
                hero=JIE_XIAHOUYUAN,
                hand=(_JIE_SHENSU_ARMOR, *_JIE_SHENSU_HAND),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_JIE_SHENSU_ARMOR, *_JIE_SHENSU_HAND),
        players=[actor],
    )

    query = actor.items_query
    assert query is not None and query.kind == "SELECT_OPTIONS"
    assert tuple(query.options) == ("jie_shensu_1", "jie_shensu_2", "jie_shensu_3")
    assert query.max_selected == 3
    subsets = {action.choices for action in query.legal_actions if isinstance(action, SelectOptions)}
    assert subsets == {
        (),
        ("jie_shensu_1",),
        ("jie_shensu_2",),
        ("jie_shensu_3",),
        ("jie_shensu_1", "jie_shensu_2"),
        ("jie_shensu_1", "jie_shensu_3"),
        ("jie_shensu_2", "jie_shensu_3"),
        ("jie_shensu_1", "jie_shensu_2", "jie_shensu_3"),
    }, "有装备牌时 8 个子集都合法（0~3 项）"


def test_jie_shensu_option_two_is_greyed_without_equipment() -> None:
    """没有可弃的装备牌 ⇒ 第 2 项**列出来但置灰**，且任何候选都不含它。"""
    actor = _JieShensuActor(items=("jie_shensu_1",), targets=(1,))
    _engine, _state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=_JIE_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_JIE_SHENSU_HAND,
        players=[actor],
    )

    query = actor.items_query
    assert query is not None
    assert tuple(query.disabled) == ("jie_shensu_2",), "第 2 项置灰（界面画灰、点不动）"
    assert tuple(query.options) == ("jie_shensu_1", "jie_shensu_2", "jie_shensu_3"), (
        "置灰项仍留在 options 里（要画出来）"
    )
    allowed = {
        action.choices for action in query.legal_actions if isinstance(action, SelectOptions)
    }
    assert allowed == {
        (),
        ("jie_shensu_1",),
        ("jie_shensu_3",),
        ("jie_shensu_1", "jie_shensu_3"),
    }, "含【2】的组合全部不合法"


def test_jie_shensu_option_two_pays_the_equipment_before_any_slash() -> None:
    """【2】的弃牌是**代价**：选完立刻付，付清之前不问目标、不发起【杀】。"""
    actor = _JieShensuActor(
        items=("jie_shensu_2",), targets=(2,), picks=(_JIE_SHENSU_HORSE,)
    )
    engine, state = _jie_shensu_engine(
        [
            SeatSpec(
                hero=JIE_XIAHOUYUAN,
                hand=(_JIE_SHENSU_ARMOR, *_JIE_SHENSU_HAND),
                equip=(_JIE_SHENSU_HORSE,),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_JIE_SHENSU_ARMOR, _JIE_SHENSU_HORSE, *_JIE_SHENSU_HAND),
        players=[actor],
    )

    assert actor.cards_queries, "弃装备是一问"
    face = {
        slot.card.card_id
        for slot in actor.cards_queries[0].slots
        if slot.card is not None
    }
    assert face == {_JIE_SHENSU_ARMOR, _JIE_SHENSU_HORSE}, "手牌 + 装备区的装备牌都在面上"
    assert _JIE_SHENSU_HORSE in state.discard, "选中的装备区牌进了弃牌堆"
    assert not ran_timing(engine, Timing.PHASE_PLAY_START), "出牌阶段被跳过"
    asks = [
        (record.seq, record.query.kind, str(getattr(record.query, "skill_id", "") or ""))
        for record in engine.records
        if record.kind == "query"
    ]
    pay_at = next(i for i, (_s, kind, _sid) in enumerate(asks) if kind == "SELECT_CARDS")
    target_at = next(i for i, (_s, _k, sid) in enumerate(asks) if ":target:" in sid)
    assert pay_at < target_at, f"弃装备必须早于选目标：{asks}"


def test_jie_shensu_three_items_use_three_slashes_in_order() -> None:
    """三项全选：弃一件装备 → 跳判定/摸牌 → 杀 → 跳出牌 → 杀 → 跳弃牌 + 翻面 → 杀。"""
    actor = _JieShensuActor(
        items=("jie_shensu_1", "jie_shensu_2", "jie_shensu_3"),
        targets=(1, 2, 3),
        picks=(_JIE_SHENSU_ARMOR,),
    )
    engine, state = _jie_shensu_engine(
        [
            SeatSpec(
                hero=JIE_XIAHOUYUAN,
                hand=(_JIE_SHENSU_ARMOR, *_JIE_SHENSU_HAND),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_JIE_SHENSU_ARMOR, *_JIE_SHENSU_HAND),
        players=[actor],
    )

    assert _jie_shensu_uses(engine) == 3, "每选一项一张【杀】"
    assert len(actor.target_queries) == 3, "三张各自选目标"
    assert state.player(0).flipped is True, "第三项翻面"
    assert not ran_timing(engine, Timing.PHASE_PLAY_START), "第二项跳出牌阶段"
    assert state.player(1).hp == 3 and state.player(2).hp == 3 and state.player(3).hp == 3


def test_jie_shensu_option_three_skips_the_discard_phase() -> None:
    """第三项跳过弃牌阶段：手牌超上限也不进 DISCARD 询问。"""
    actor = _JieShensuActor(items=("jie_shensu_3",), targets=(1,))
    engine, state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=_JIE_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_JIE_SHENSU_HAND,
        players=[actor],
    )

    assert state.player(0).flipped is True
    assert not any(
        record.kind == "query" and record.query.kind == "DISCARD"
        for record in engine.records
    ), "弃牌阶段被跳过 ⇒ 不发 DISCARD 询问"


def test_jie_shensu_flip_skips_the_next_turn_but_not_this_one() -> None:
    """翻面：他**下一个**回合整个被跳过（回合开始前翻回、不发 `TURN_START`）。"""
    actor = _JieShensuActor(items=("jie_shensu_3",), targets=(1,))
    engine, state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=_JIE_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_JIE_SHENSU_HAND,
        players=[actor],
        stop=lambda e: ran_timing(e, Timing.TURN_START) and e.state.turn_seat == 1,
    )

    assert state.player(0).flipped is True, "本回合结束时他仍是背面"
    assert len(_jie_shensu_activation_queries(engine)) == 1, "只在自己的那个回合问过"


def test_jie_shensu_skipped_judge_phase_keeps_the_judge_zone_cards() -> None:
    """跳过判定阶段 ≠ 弃置判定区的牌（规则 §11.5）。"""
    actor = _JieShensuActor(items=("jie_shensu_1",), targets=(1,))
    _engine, state = _jie_shensu_engine(
        [
            SeatSpec(
                hero=JIE_XIAHOUYUAN,
                hand=_JIE_SHENSU_HAND,
                judge=(_JIE_SHENSU_INDULGENCE,),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_JIE_SHENSU_INDULGENCE, *_JIE_SHENSU_HAND),
        players=[actor],
    )

    assert _JIE_SHENSU_INDULGENCE in state.player(0).judge, "判定区里的牌保留"
    assert _JIE_SHENSU_INDULGENCE not in state.discard, "没被判、也没被弃"


def test_jie_shensu_slashes_do_not_consume_the_play_phase_limit() -> None:
    """回合开始的那三张【杀】不计入出牌阶段的次数（``counts=False``）。"""
    slash = cards_of_type(PACK, "slash")[0]
    actor = _JieShensuActor(
        items=("jie_shensu_1",),
        targets=(1,),
        # 出牌阶段再用一张真【杀】：打相邻座位（距离 1，裸手也够得着）。
        play_slash=(1,),
    )
    engine, _state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=(slash, *_JIE_SHENSU_HAND)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(slash, *_JIE_SHENSU_HAND),
        players=[actor],
    )

    assert _jie_shensu_uses(engine) == 2, "回合开始一张 + 出牌阶段一张（后者没被前者的次数挡住）"


def test_jie_shensu_without_a_legal_target_skips_only_that_slash() -> None:
    """没有任何合法目标 ⇒ 这一张【杀】不发生，其余项照旧（不发空询问）。"""
    actor = _JieShensuActor(items=("jie_shensu_1",))
    engine, _state = _jie_shensu_engine(
        [
            SeatSpec(hero=JIE_XIAHOUYUAN, hand=_JIE_SHENSU_HAND),
            SeatSpec(hero="zhugeliang", hand=()),
            SeatSpec(hero="zhugeliang", hand=()),
            SeatSpec(hero="zhugeliang", hand=()),
        ],
        used=_JIE_SHENSU_HAND,
        players=[actor],
    )

    assert actor.target_queries == [], "空池不发询问"
    assert _jie_shensu_uses(engine) == 0, "一张【杀】都没发生"
    assert not ran_timing(engine, Timing.PHASE_JUDGE), "但【1】的跳阶段照旧生效"


# ---------------------------------------------------------------------------
# 界张角移动版【雷击】【鬼道】【黄天】（jie-feng-batch2 票 02）
#
# 牌面（用户 2026-11-08 改后，逐字）：
#   雷击：当你使用或打出【闪】时，你可以令一名其他角色进行一次判定，若结果为♠️，你对其造成
#         2点雷电伤害；♣️，你回复1点体力并对其造成1点雷电伤害。
#   鬼道：当一名角色的判定牌生效前，你可以用一张黑色牌替换之。
#   黄天：主公技，其他群势力角色的出牌阶段限一次，该角色可以交给你一张【闪】或【闪电】。
#
# 本节钉住（票面 §1.9 / §2.9 / §3.9）：
# 1. 雷击的目标池**不含自己**；♠ = 一次 2 点、♣ = 先回复 1 点再 1 点、♥♦ 零副作用；
# 2. 鬼道的候选只收黑色牌（手牌 ∪ 装备区），**替换**= 原判定牌进张角手牌；
# 3. 黄天与移动版**共用** flow、但声明 / 技能 id 各一份，且能真的把牌交给界张角。
# ---------------------------------------------------------------------------

JIE_LEIJI = "hero.jie_zhangjiao_mobile.leiji"
JIE_GUIDAO = "hero.jie_zhangjiao_mobile.guidao"
JIE_HUANGTIAN = "hero.jie_zhangjiao_mobile.huangtian"
JIE_ZHANGJIAO = "jie_zhangjiao_mobile"
ZHANGJIAO_MOBILE = "zhangjiao_mobile"


def _zj_suit_card(*, suit: str, exclude=()) -> int:
    """牌堆里第一张指定花色的牌（避开已发到座位上的）。"""
    for index, card in enumerate(PACK.deck.cards):
        if index in exclude or card.suit != suit:
            continue
        return index
    raise AssertionError(f"牌堆里没有 {suit}")


def _zj_pick(query, predicate):
    action = next((item for item in query.legal_actions if predicate(item)), None)
    assert action is not None, (
        f"候选里没有预期动作：{[(a.kind, getattr(a, 'choice', None)) for a in query.legal_actions]}"
    )
    return action


def _zj_default(query):
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _zj_step_until(engine, predicate, *, limit: int = 4000) -> bool:
    """推进到 ``predicate`` 成立；中途其它询问按"缺省放弃 / 结束阶段"应答。"""
    for _ in range(limit):
        if predicate(engine):
            return True
        if engine.pending is not None:
            engine.submit(_zj_default(engine.pending))
            continue
        engine._step()
    return False


def _zj_run_to_quiet(engine, *, limit: int = 4000) -> None:
    for _ in range(limit):
        if engine.finished or not engine.stack:
            return
        if engine.pending is None:
            engine._step()
            continue
        engine.submit(_zj_default(engine.pending))


def test_jie_zhangjiao_mobile_is_in_the_default_pool() -> None:
    """数据面：界张角移动版进默认池（真名「张角」、群 / 男 / 3 体力）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    assert JIE_ZHANGJIAO in enabled_hero_ids()
    hero = hero_by_id(JIE_ZHANGJIAO)
    assert hero.name == "界张角移动版"
    assert hero.true_name == "张角"
    assert hero.kingdom == "qun"
    assert hero.hp_max == 3


def _jie_leiji_state(*, judge: int, hp: int = 4):
    """座位 1 先用【杀】打座位 0（界张角）；座位 0 手里有【闪】。"""
    slash = cards_of_type(PACK, "slash")[0]
    dodge = cards_of_type(PACK, "dodge")[0]
    used = {slash, dodge, judge}
    rest = tuple(index for index in range(len(PACK.deck.cards)) if index not in used)
    deck = (rest[0], rest[1], judge, *rest[2:])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp, hp_max=4, hero=JIE_ZHANGJIAO, hand=(dodge,)),
            SeatSpec(hp=4, hp_max=4, hand=(slash,)),
        ],
        deck=deck,
        rule_config=RuleConfig(player_count=2, starting_hand_size=1, first_seat=1),
    )
    return slash, dodge, state


def _run_jie_leiji(engine, *, slash: int, dodge: int, target_choice: str = "seat:1"):
    """走完"出【杀】→ 出【闪】→ 是否发动雷击 → 选判定角色"这四问。"""
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    ), "没到座位 1 的出牌阶段"
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == slash,
        )
    )
    assert _zj_step_until(
        engine, lambda e: e.pending is not None and e.pending.kind == QueryKind.DODGE
    ), "没到【闪】窗口"
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == dodge,
        )
    )
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(e.pending, "skill_id", None) == JIE_LEIJI,
    ), "没问【雷击】是否发动"
    engine.submit(_zj_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_OPTION,
    ), "没到选判定角色的询问"
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, SelectOption)
            and str(action.choice) == target_choice,
        )
    )


def test_jie_leiji_spade_deals_two_thunder_damage() -> None:
    """♠ ⇒ 对**被判定者**造成一次 2 点雷电伤害（不是两次 1 点）。"""
    spade = _zj_suit_card(suit="spade")
    slash, dodge, state = _jie_leiji_state(judge=spade)
    engine = Engine.from_state(state, seed=5)

    _run_jie_leiji(engine, slash=slash, dodge=dodge)
    _zj_run_to_quiet(engine)

    assert engine.state.player(0).hp == 4, "伤害打的是被判定者，不是张角"
    assert engine.state.player(1).hp == 2
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert len(damage) == 1, "一次伤害事件（2 点）"


def test_jie_leiji_club_recovers_one_then_deals_one() -> None:
    """♣ ⇒ 张角**先**回复 1 点，然后对被判定者造成 1 点雷电伤害。"""
    club = _zj_suit_card(suit="club")
    slash, dodge, state = _jie_leiji_state(judge=club, hp=3)
    engine = Engine.from_state(state, seed=5)

    _run_jie_leiji(engine, slash=slash, dodge=dodge)
    _zj_run_to_quiet(engine)

    assert engine.state.player(0).hp == 4, "♣ 的分支里张角回复 1 点"
    assert engine.state.player(1).hp == 3, "同时对被判定者造成 1 点雷电伤害"


def test_jie_leiji_club_at_full_hp_still_deals_one() -> None:
    """满体力时 ♣ 的回复不发生（规则 §4.15），但 1 点伤害照常。"""
    club = _zj_suit_card(suit="club")
    slash, dodge, state = _jie_leiji_state(judge=club, hp=4)
    engine = Engine.from_state(state, seed=5)

    _run_jie_leiji(engine, slash=slash, dodge=dodge)
    _zj_run_to_quiet(engine)

    assert engine.state.player(0).hp == 4, "满体力不回复"
    assert engine.state.player(1).hp == 3, "伤害段仍发生"


def test_jie_leiji_never_targets_the_owner() -> None:
    """「一名**其他**角色」⇒ 座位步的候选里没有张角自己（与移动版的分界）。"""
    club = _zj_suit_card(suit="club")
    slash, dodge, state = _jie_leiji_state(judge=club)
    engine = Engine.from_state(state, seed=5)

    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    )
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == slash,
        )
    )
    assert _zj_step_until(
        engine, lambda e: e.pending is not None and e.pending.kind == QueryKind.DODGE
    )
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == dodge,
        )
    )
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(e.pending, "skill_id", None) == JIE_LEIJI,
    )
    engine.submit(_zj_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_OPTION,
    )
    choices = [
        action.choice
        for action in engine.pending.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert choices == ["seat:1"], "2 人局里唯一合法的判定对象是对手"


def test_jie_leiji_heart_and_diamond_leave_no_side_effect() -> None:
    """♥ / ♦ ⇒ 什么也不发生（判定牌照常结算）。"""
    heart = _zj_suit_card(suit="heart")
    slash, dodge, state = _jie_leiji_state(judge=heart, hp=3)
    engine = Engine.from_state(state, seed=5)

    _run_jie_leiji(engine, slash=slash, dodge=dodge)
    _zj_run_to_quiet(engine)

    assert engine.state.player(0).hp == 3 and engine.state.player(1).hp == 4
    assert not [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing in (Timing.DAMAGE_DEAL, Timing.RECOVER_VALUE)
    ]


def _jie_guidao_state(*, original: int, hand=(), equip=()):
    """座位 0（界张角）的判定阶段有一张【乐不思蜀】；判定牌 = ``original``。"""
    indulgence = cards_of_type(PACK, "indulgence")[0]
    used = {indulgence, original, *hand, *equip}
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=4,
                hp_max=4,
                hero=JIE_ZHANGJIAO,
                judge=(indulgence,),
                hand=tuple(hand),
                equip=tuple(equip),
            ),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=(original, *deck_excluding(PACK, *used)),
        rule_config=RuleConfig(player_count=2, starting_hand_size=1, first_seat=0),
    )


def _run_jie_guidao(engine, *, pick_card: int | None):
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.RESPOND
        and getattr(e.pending, "cause", None) is WindowCause.REJUDGE,
    ), "没开改判窗口"
    if pick_card is None:
        engine.submit(_zj_pick(engine.pending, lambda action: isinstance(action, Pass)))
        return
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, SubstituteJudgeCard)
            and action.card.card_id == pick_card,
        )
    )


def test_jie_guidao_replaces_the_judge_card_and_the_owner_gains_the_original() -> None:
    """**替换**：新牌成为判定牌，原判定牌由界张角获得（不进弃牌堆）。"""
    original = _zj_suit_card(suit="heart")
    black = _zj_suit_card(suit="club", exclude=(original,))
    state = _jie_guidao_state(original=original, hand=(black,))
    engine = Engine.from_state(state, seed=7)

    _run_jie_guidao(engine, pick_card=black)
    _zj_run_to_quiet(engine)

    assert original in engine.state.player(0).hand, "替换 ⇒ 原判定牌由张角获得"
    assert black in engine.state.discard


def test_jie_guidao_offers_only_black_cards() -> None:
    """候选区 = 手牌 ∪ 装备区，只收黑色牌。"""
    original = _zj_suit_card(suit="heart")
    red_hand = _zj_suit_card(suit="diamond", exclude=(original,))
    black_equip = cards_of_type(PACK, "double_sword")[0]
    assert PACK.deck.cards[black_equip].suit == "spade"
    state = _jie_guidao_state(original=original, hand=(red_hand,), equip=(black_equip,))
    engine = Engine.from_state(state, seed=7)

    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.RESPOND
        and getattr(e.pending, "cause", None) is WindowCause.REJUDGE,
    )
    offered = [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, SubstituteJudgeCard)
    ]
    assert [action.card.card_id for action in offered] == [black_equip]
    assert all(action.skill_id == JIE_GUIDAO for action in offered)


def test_jie_guidao_is_not_asked_without_a_black_card() -> None:
    """没有黑色牌 ⇒ 改判窗口不发这一问（判定直接生效）。"""
    original = _zj_suit_card(suit="heart")
    red_hand = _zj_suit_card(suit="diamond", exclude=(original,))
    state = _jie_guidao_state(original=original, hand=(red_hand,))
    engine = Engine.from_state(state, seed=7)

    _run_jie_guidao(engine, pick_card=None)
    _zj_run_to_quiet(engine)

    assert original in engine.state.discard, "没人改判 ⇒ 原判定牌照常进弃牌堆"


def _jie_huangtian_state(*, lord_identity=Identity.LORD, hand=(None,), kingdom=Kingdom.QUN):
    dodge = cards_of_type(PACK, "dodge")[0]
    actual = dodge if hand[0] is None else hand[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=4,
                hp_max=4,
                hero=JIE_ZHANGJIAO,
                identity=lord_identity,
                kingdom=Kingdom.QUN,
            ),
            SeatSpec(
                hp=4,
                hp_max=4,
                hero="yuji_mobile",
                hand=(actual,) if actual is not None else (),
                kingdom=kingdom,
            ),
            SeatSpec(hp=4, hp_max=4, hero="simayi", kingdom=Kingdom.WEI),
            SeatSpec(hp=4, hp_max=4, kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, dodge),
        rule_config=RuleConfig(player_count=4, starting_hand_size=1),
    )
    return state, actual


def test_jie_huangtian_is_offered_only_to_other_qun_seats() -> None:
    """候选进**别人**的出牌阶段：界张角自己与魏势力角色都没有它。"""
    state, _dodge = _jie_huangtian_state()
    for seat, expected in ((0, False), (1, True), (2, False)):
        actions = legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=0)
        found = [
            action
            for action in actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIE_HUANGTIAN
        ]
        assert bool(found) is expected, f"座位 {seat} 的黄天候选应当是 {expected}"


def test_jie_huangtian_moves_the_card_to_the_jie_zhangjiao() -> None:
    """交出一张【闪】⇒ 进**界张角**的手牌（不是移动版那个张角的）。"""
    state, dodge = _jie_huangtian_state()
    engine = Engine.from_state(state, seed=3)

    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    ), "没到座位 1 的出牌阶段"
    engine.submit(
        _zj_pick(
            engine.pending,
            lambda action: isinstance(action, ActivateSkill)
            and action.skill_id == JIE_HUANGTIAN,
        )
    )
    assert _zj_step_until(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_CARDS,
    ), "没到选牌步"
    engine.submit(SelectCards(answers_seq=engine.pending.seq, picks=(0,)))
    assert _zj_step_until(engine, lambda e: dodge in e.state.player(0).hand), (
        "那张【闪】要进界张角手牌"
    )
    assert dodge not in engine.state.player(1).hand


def test_jie_huangtian_shares_the_flow_but_not_the_identifier() -> None:
    """与移动版**逐字相同** ⇒ 共用 flow 对象，但声明 / 技能 id 各一份（界【天妒】先例）。"""
    from sgs.skills import CATALOG
    from sgs.skills.active import HUANGTIAN_FLOW

    assert CATALOG[JIE_HUANGTIAN].skill_id != CATALOG["hero.zhangjiao_mobile.huangtian"].skill_id
    assert JIE_HUANGTIAN in CATALOG and "hero.zhangjiao_mobile.huangtian" in CATALOG
    assert HUANGTIAN_FLOW is not None
