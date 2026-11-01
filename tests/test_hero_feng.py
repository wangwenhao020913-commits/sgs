"""风包：界曹仁（移动版）【据守】、黄忠【烈弓】。

牌面（`D:\\SGS_HEROS\\神话再临-风包\\曹仁\\技能.txt`，用户录入）：

    据守：结束阶段，你可以摸三张牌，然后翻面。

三条验收（全走外部缝：询问 / 记录流 / 终局状态）：

1. **自己的结束阶段**问一次；发动 ⇒ 摸三张 + 武将牌翻到背面（各一条 ``card_move`` /
   ``flipped_change``）；
2. **不发动** ⇒ 一张不摸、状态不变；
3. **翻面是 toggle**：已经背面朝上时发动 ⇒ 翻回正面（不是"再置为背面"）。

翻面本身（跳过回合 / 不清理"直到你的下个回合开始"类作用域）在 ``tests/test_turn_loop.py``。

黄忠【烈弓】（票 03；牌面 `D:\\SGS_HEROS\\神话再临-风包\\黄忠\\技能.txt`）落在文件后半：
与【铁骑】同形的逐目标帧标签 ``Tag.UNRESPONSIVE``——被封印的目标连【闪】窗口都不出现。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:  # sgs_web 住在 tools/（与 test_web.py 同一条引导）
    sys.path.insert(0, str(_TOOLS))

from sgs.data import Identity, RuleConfig, load_content_pack, load_hero_tables
from sgs.effects import check_targets, distance, legal_actions_for
from sgs.engine import Engine
from sgs.skills.virtual import transformed_card
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
    Zone,
    project,
)
from sgs.timing import Timing
from sgs_web.display import seat_display

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

JUSHSOU = "hero.jie_caoren_mobile.jushou"
QINGJIAN = "hero.jie_xiahoudun.qingjian"
HERO = "jie_caoren_mobile"


class Scripted:
    """是否发动【据守】由标志决定；清俭一律 Pass（本文件只借它制造摸牌挂起）。"""

    def __init__(self, *, activate: bool) -> None:
        self.activate = activate
        self.queries = []
        self.qingjian = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == QINGJIAN:
            self.qingjian.append(query)
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JUSHSOU:
            self.queries.append(query)
            if self.activate:
                return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _run(
    *,
    activate: bool,
    flip_before_finish: bool = False,
    deck=tuple(range(10, 40)),
    grant_qingjian: bool = False,
):
    """造一个"座位 0 是界曹仁移动版"的局面，跑到一个完整回合结束。

    ``flip_before_finish``：先在**出牌阶段开始后**把武将牌翻到背面（此刻"回合开始前"已经
    过去，故不会触发跳过），再看结束阶段的据守怎么把自己翻回来。
    ``grant_qingjian``：给座位 0 授予【清俭】——据守摸三张会触发它并挂起（票 25 的回归）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=(40, 41, 42)), SeatSpec()],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    if grant_qingjian:
        state.player(0).granted_skills.append(QINGJIAN)
    engine = Engine.from_state(state, seed=7)
    player = Scripted(activate=activate)
    players = [player, Scripted(activate=False)]

    def stop(e) -> bool:
        return any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        )

    if flip_before_finish:
        run_queries(
            engine,
            players,
            lambda e: any(
                record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
                for record in e.records
            ),
        )
        engine.set_flipped(None, 0, True)
    run_queries(engine, players, stop)
    return engine, player


def _draws(engine) -> list[int]:
    """座位 0 摸到的牌（按顺序）。"""
    return [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.seat == 0
        for card in record.cards
    ]


def _flips(engine) -> list[tuple[int, bool]]:
    return [
        (record.seat, record.flipped)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]


def test_jushou_draws_three_then_turns_the_hero_card_over():
    """正例：结束阶段问一次；发动 ⇒ 摸三张（牌堆顶三张）+ 翻面。"""
    engine, player = _run(activate=True)

    assert len(player.queries) == 1, "结束阶段问一次「是否发动」"
    assert player.queries[0].skill_id == JUSHSOU
    assert engine.state.player(0).flipped is True, "翻到背面朝上"
    assert _flips(engine) == [(0, True)], "翻面落一条 delta"
    # 摸牌阶段 2 张 + 结束阶段 3 张 = 5 张，且结束阶段那三张就是牌堆顶接下来的三张。
    drawn = _draws(engine)
    assert len(drawn) == 5, f"摸牌阶段 2 张 + 据守 3 张，实得 {drawn}"
    assert drawn[2:] == list(deck_top := list(range(10, 40))[2:5]), "据守摸的是牌堆顶三张"


def test_declining_jushou_leaves_hand_and_hero_card_untouched():
    """反例：不发动 ⇒ 一张不摸、不翻面。"""
    engine, player = _run(activate=False)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False
    assert _flips(engine) == [], "不发动不落翻面 delta"


def test_jushou_toggles_a_back_facing_hero_card_back_to_the_front():
    """翻面是 **toggle**（规则集 13.1）：背面朝上时发动 ⇒ 翻回正面。"""
    engine, player = _run(activate=True, flip_before_finish=True)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False, "背面 ⇒ 正面"
    assert _flips(engine) == [(0, True), (0, False)], "造局一条、据守一条"


def test_jushou_still_flips_after_a_qingjian_ask_suspends_the_draw():
    """票 25 回归：摸三张触发【清俭】挂起，续跑后**仍要翻面**（旧实现只摸不翻）。

    Q1（摸牌算获得）之后，据守的"先摸三张"会内联派发清俭并挂起。挂起时若把"本次据守
    已走完"的游标当成两段都完成，续跑会直接返回——手牌摸到了、武将牌却没翻。判据 =
    清俭真的问了一次，且翻面 delta 照旧存在。
    """
    engine, player = _run(activate=True, grant_qingjian=True)

    assert len(player.qingjian) == 1, "据守的摸牌触发清俭一次（Pass）"
    assert len(player.queries) == 1, "据守自己的发动询问仍只有一次"
    assert engine.state.player(0).flipped is True, "续跑之后翻面仍然发生"
    assert _flips(engine) == [(0, True)], "翻面只落一条 delta（不重翻）"
    assert len(_draws(engine)) == 5, "摸牌阶段 2 张 + 据守 3 张（清俭 Pass 不改变张数）"


# ---------------------------------------------------------------------------
# 风包·黄忠【烈弓】（票 03）：零步流水线 + 逐目标帧标签
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\黄忠\\技能.txt`）：
#
#     烈弓：当你于出牌阶段内使用【杀】指定一名角色为目标后，若该角色的手牌数不小于你的
#           体力值或不大于你的攻击范围，你可以令其不能使用【闪】响应此【杀】。
#
# 形状 = 【铁骑】同形的逐目标帧标签（``Tag.UNRESPONSIVE``）：被封的目标连【闪】窗口都
# 不出现（【八卦阵】的"视为使用【闪】"一并不出现——账本「视为使用也是使用」）。
# ---------------------------------------------------------------------------

LIEGONG = "hero.huangzhong.liegong"
HUANGZHONG = "huangzhong"
EIGHT_DIAGRAMS = "equip.eight_diagrams"


def _cards(type_id: str) -> list[int]:
    """某牌名的全部 ``card_id``（按数据表顺序）。"""
    return cards_of_type(PACK, type_id)


def _candidates(query):
    """一条询问的全部候选：``legal_actions`` ∪ 附带指定池（借刀杀人的"a 出【杀】"只在这里）。"""
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


def _liegong_asks(engine):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == LIEGONG
    ]


class Liegong:
    """一个座位的最小脚本：出牌阶段按 ``card`` 出牌，其余询问按标志应答。

    ``activate`` = 【烈弓】问到时发动（否则放弃）；``dodge`` = 有【闪】就打出；
    持刀者窗口（借刀杀人）一律选"出【杀】"那一支。
    """

    def __init__(
        self,
        *,
        card: int | None = None,
        targets: tuple[int, ...] = (1,),
        designations: tuple[int, ...] = (),
        activate: bool = False,
        dodge: bool = False,
    ) -> None:
        self.card = card
        self.targets = tuple(targets)
        self.designations = tuple(designations)
        self.activate = activate
        self.dodge = dodge
        self.played = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.card is not None and not self.played:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == self.card
                and tuple(action.targets) == self.targets
                and tuple(action.designations) == self.designations,
            )
            if action is not None:
                self.played = True
                return action
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == LIEGONG:
            if self.activate:
                return _find(
                    query, lambda action: isinstance(action, ActivateSkill)
                ) or query.legal_actions[0]
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
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
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        if query.kind == "PLAY_PHASE":
            return (
                _find(query, lambda action: isinstance(action, EndPhase))
                or query.legal_actions[0]
            )
        if query.kind in ("RESPOND", "PEACH"):
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        return query.legal_actions[0]


def _liegong_state(
    *,
    hp: int = 4,
    equip: tuple[int, ...] = (),
    target_hand: tuple[int, ...] = (),
    card: int,
):
    """座位 0 = 黄忠（手里有 ``card``）、座位 1 = 目标；牌堆去掉已发到座位上的牌。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp, equip=equip, hand=(card,), hero=HUANGZHONG),
            SeatSpec(hand=target_hand),
        ],
        deck=deck_excluding(PACK, card, *equip, *target_hand),
        turn_seat=0,
    )


def _run_slash(state, *, card: int, activate: bool = False, dodge: bool = False):
    """座位 0 对座位 1 使用 ``card``；跑到那张牌进弃牌堆、结算栈清空。"""
    players = [
        Liegong(card=card, activate=activate),
        Liegong(dodge=dodge),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: card in e.state.discard and not e.stack,
    )
    return engine


def test_liegong_seals_the_dodge_window_when_the_hand_count_is_not_less_than_hp():
    """第一分支：手牌数 **不小于当前体力值**（黄忠 2 点体力、目标 2 张手牌）⇒ 无【闪】窗口。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = _liegong_state(hp=2, card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=True)

    assert len(_liegong_asks(engine)) == 1, "自己的出牌阶段里问一次「是否发动」"
    assert _queries(engine, "DODGE") == [], "被封 ⇒ 该目标连【闪】询问都不出现"
    assert engine.state.player(1).hp == 3, "不可响应 ⇒ 照常受伤"


def test_liegong_seals_the_dodge_window_when_the_hand_count_is_within_the_attack_range():
    """第二分支：手牌数 **不大于攻击范围**（贯石斧范围 3、目标 2 张手牌、黄忠满体力 4）。"""
    slash, axe, dodge1, dodge2 = (
        _cards("slash")[0],
        _cards("axe")[0],
        _cards("dodge")[0],
        _cards("dodge")[1],
    )
    state = _liegong_state(hp=4, equip=(axe,), card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=True)

    assert len(_liegong_asks(engine)) == 1
    assert _queries(engine, "DODGE") == [], "2 ≤ 攻击范围 3 ⇒ 封【闪】"
    assert engine.state.player(1).hp == 3


def test_liegong_is_not_offered_when_neither_branch_holds():
    """两个分支都不成立（目标 3 张手牌、黄忠满体力 4 且无武器 ⇒ 范围 1）⇒ **不问**、窗口照常。"""
    slash = _cards("slash")[0]
    target_hand = tuple(_cards("dodge")[:3])
    state = _liegong_state(hp=4, card=slash, target_hand=target_hand)

    engine = _run_slash(state, card=slash, activate=True, dodge=True)

    assert _liegong_asks(engine) == [], "条件不成立 ⇒ 连「是否发动」都不发"
    assert len(_queries(engine, "DODGE")) == 1, "窗口照常出现"
    assert engine.state.player(1).hp == 4, "【闪】照常抵消"


def test_declining_liegong_leaves_the_dodge_window_open():
    """条件成立但答「不发动」⇒ 窗口照常、目标能闪掉（正面对照，断言不空转）。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = _liegong_state(hp=2, card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=False, dodge=True)

    assert len(_liegong_asks(engine)) == 1, "条件成立 ⇒ 仍问一次，只是答了不发动"
    assert len(_queries(engine, "DODGE")) == 1, "不发动 ⇒ 窗口照常"
    assert engine.state.player(1).hp == 4, "【闪】照常抵消"


def test_liegong_is_not_offered_in_another_characters_play_phase():
    """「于出牌阶段内」= **自己的**出牌阶段：借刀杀人逼出的【杀】不触发（此刻是别人的出牌阶段）。

    造局：座位 0（当前回合）打【借刀杀人】指定持刀的座位 1 = 黄忠（2 点体力）、刀下亡魂是
    座位 2（2 张手牌）。若只看"出牌阶段"，黄忠的条件此刻**成立**（2 ≥ 2）；不触发的唯一
    理由是**这不是他自己的出牌阶段**。
    """
    borrowed, crossbow, slash = (
        _cards("borrowed_sword")[0],
        _cards("crossbow")[0],
        _cards("slash")[0],
    )
    dodge1, dodge2 = _cards("dodge")[0], _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hp=2, hand=(slash,), equip=(crossbow,), hero=HUANGZHONG),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=deck_excluding(PACK, borrowed, crossbow, slash, dodge1, dodge2),
        turn_seat=0,
    )
    players = [
        Liegong(card=borrowed, targets=(1,), designations=(2,)),
        Liegong(),
        Liegong(dodge=True),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: slash in e.state.discard and borrowed in e.state.discard and not e.stack,
    )

    assert _liegong_asks(engine) == [], "别人的出牌阶段里被迫使用的【杀】不触发烈弓"
    assert len(_queries(engine, "DODGE")) == 1, "座位 2 的【闪】窗口照常出现"
    assert engine.state.player(2).hp == 4, "【闪】照常抵消"


MILITARY = load_content_pack(deck_profile="standard_ex_160")
"""军争档（160 张）：火【杀】只在这一档里有；标准 108 档没有它。"""


def _military_cards(type_id: str) -> list[int]:
    return cards_of_type(MILITARY, type_id)


def test_liegong_triggers_on_a_fire_slash_but_not_on_a_duel():
    """牌名按【杀】**家族**判据：火【杀】照常触发；【决斗】不是【杀】⇒ 不问。"""
    fire_slash = _military_cards("fire_slash")[0]
    dodge1, dodge2 = _military_cards("dodge")[0], _military_cards("dodge")[1]
    fire_state = build_state(
        pack=MILITARY,
        seats=[
            SeatSpec(hp=2, hand=(fire_slash,), hero=HUANGZHONG),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=deck_excluding(MILITARY, fire_slash, dodge1, dodge2),
        turn_seat=0,
    )
    fire_engine = _run_slash(fire_state, card=fire_slash, activate=True)

    assert len(_liegong_asks(fire_engine)) == 1, "火【杀】同族 ⇒ 照常问"
    assert _queries(fire_engine, "DODGE") == [], "封【闪】"
    assert fire_engine.state.player(1).hp == 3

    duel, filler1, filler2 = (
        _cards("duel")[0],
        _cards("peach")[0],
        _cards("peach")[1],
    )
    duel_state = _liegong_state(hp=2, card=duel, target_hand=(filler1, filler2))
    duel_engine = _run_slash(duel_state, card=duel)

    assert _liegong_asks(duel_engine) == [], "【决斗】不是【杀】⇒ 不问"
    assert duel_engine.state.player(1).hp == 3, "【决斗】照常结算（目标没有【杀】）"


def test_liegong_seals_every_qualified_target_with_one_question():
    """【方天画戟】多目标：两个目标各按自己的手牌数判定，**「是否发动」仍只有一次**（票 23 的答案缓存裁定）。"""
    slash, halberd = _cards("slash")[0], _cards("halberd")[0]
    target_one = tuple(_cards("dodge")[:4])
    target_two = tuple(_cards("peach")[:2])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(halberd,), hero=HUANGZHONG),
            SeatSpec(hand=target_one),
            SeatSpec(hand=target_two),
        ],
        deck=deck_excluding(PACK, slash, halberd, *target_one, *target_two),
        turn_seat=0,
    )

    def to_play_phase(engine) -> bool:
        return any(
            record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
            for record in engine.records
        )

    players = [Liegong(card=slash, targets=(1, 2), activate=True), Liegong(), Liegong()]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, to_play_phase)
    # 方天画戟要"【杀】是最后一张手牌"：摸牌阶段过完后把座位 0 的手牌换成只有这张【杀】。
    hand = engine.state.player(0).hand
    hand.clear()
    hand.append(slash)
    run_until(
        engine,
        players,
        lambda e: slash in e.state.discard and not e.stack,
    )

    assert len(_liegong_asks(engine)) == 1, "多目标只问一次（同一帧第二次触发沿用第一次答案）"
    assert _queries(engine, "DODGE") == [], "两个目标都封【闪】（4 ≥ 4 / 2 ≤ 范围 4）"
    assert engine.state.player(1).hp == 3
    assert engine.state.player(2).hp == 3


def test_liegong_also_seals_the_eight_diagrams_provider():
    """封的是一个**响应窗口**，不只是真牌：目标装了【八卦阵】也拿不到「视为使用【闪】」的机会。"""
    slash, eight = _cards("slash")[0], _cards("eight_diagrams")[0]
    dodge, peach = _cards("dodge")[0], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hand=(slash,), hero=HUANGZHONG),
            SeatSpec(equip=(eight,), hand=(dodge, peach)),
        ],
        deck=deck_excluding(PACK, slash, eight, dodge, peach),
        turn_seat=0,
    )

    engine = _run_slash(state, card=slash, activate=True)

    assert _queries(engine, "DODGE") == [], "窗口根本不出现 ⇒ 【八卦阵】的提供者候选也没有"
    assert engine.state.player(1).hp == 3


# ---------------------------------------------------------------------------
# 票 04 / 票 05 / 票 06 / 票 07：神关羽移动版【武神】【武魂】
# ---------------------------------------------------------------------------

SHEN_GUANYU = "shen_guanyu_mobile"
WUSHEN = "hero.shen_guanyu_mobile.wushen"
WUHUN = "hero.shen_guanyu_mobile.wuhun"


def _heart(type_id: str) -> int:
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit == "heart"
    )


def _not_heart(type_id: str) -> int:
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit != "heart"
    )


def _shen_play_state(hand: tuple[int, ...]):
    """4 人身份局、座位 0 = 神关羽、出牌阶段；其余座位给距离 2 / 3 的对照。"""
    seats = [
        SeatSpec(hero=SHEN_GUANYU, hand=hand, hp=3, hp_max=4, identity=Identity.LORD),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.LOYALIST),
        SeatSpec(identity=Identity.RENEGADE),
    ]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )


def _play_actions(state):
    return legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)


def _as_slash(actions, source: int):
    return [
        action
        for action in actions
        if getattr(action, "card", None) is not None
        and action.card.type == "slash"
        and action.card.is_virtual
        and action.card.virtual_sources == (source,)
    ]


def test_wushen_turns_a_heart_hand_card_into_a_plain_slash_and_forbids_its_old_use():
    """①：红桃手牌只能当【杀】；它原来的用途（这里是【桃】）从候选里消失。"""
    heart_peach = _heart("peach")
    black_peach = _not_heart("peach")
    state = _shen_play_state((heart_peach, black_peach))
    actions = _play_actions(state)

    assert _as_slash(actions, heart_peach), "红桃手牌 ⇒ 一张普通【杀】的转化候选"
    assert not any(
        isinstance(action, UseCard) and action.card.card_id == heart_peach
        for action in actions
    ), "红桃【桃】不能按原用途使用"
    assert any(
        isinstance(action, UseCard) and action.card.card_id == black_peach
        for action in actions
    ), "非红桃【桃】照常"


def test_wushen_heart_card_product_is_a_plain_slash():
    """①的产物是**普通【杀】**（不是原牌名）：红桃【杀】也走"当普通【杀】"。

    移动版 Q&A 的"红桃火杀按普通杀"由同一条声明覆盖（产物 ``type = "slash"``）；
    火【杀】只在 160 档牌堆里，108 档用普通【杀】钉同一条产物声明。
    """
    heart_slash = _heart("slash")
    state = _shen_play_state((heart_slash,))
    products = _as_slash(_play_actions(state), heart_slash)
    assert products and products[0].card.type == "slash"
    assert products[0].card.name == "杀"


def test_wushen_heart_slash_ignores_distance():
    """②：红桃【杀】的目标池豁免距离（座位 2 在 4 人局距离 3 > 攻击范围 1）。"""
    heart_slash = _heart("slash")
    state = _shen_play_state((heart_slash,))
    product = transformed_card(state, (heart_slash,), "slash")
    pool = check_targets(state, 0, product)
    assert 2 in pool and 3 in pool, "无距离限制 ⇒ 所有其他存活角色都在池里"


def test_wushen_heart_dodge_cannot_answer_a_dodge_window():
    """①：红桃【闪】不能当【闪】打出；非红桃【闪】照常。"""
    heart_dodge = _heart("dodge")
    black_dodge = _not_heart("dodge")
    state = _shen_play_state((heart_dodge, black_dodge))
    actions = legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=1)
    used = {
        action.card.card_id
        for action in actions
        if isinstance(action, UseCard)
    }
    assert heart_dodge not in used
    assert black_dodge in used


def test_wushen_heart_peach_cannot_save_the_dying_holder():
    """①：红桃【桃】不能当【桃】自救（濒死窗口）。"""
    heart_peach = _heart("peach")
    black_peach = _not_heart("peach")
    state = _shen_play_state((heart_peach, black_peach))
    actions = legal_actions_for(state, 0, QueryKind.PEACH, answers_seq=1, dying=0)
    used = {
        action.card.card_id
        for action in actions
        if isinstance(action, UseCard)
    }
    assert heart_peach not in used
    assert black_peach in used


def test_wushen_heart_card_can_be_played_as_a_slash_in_response():
    """①：南蛮 / 决斗的响应窗口里，红桃手牌仍能当【杀】**打出**。"""
    heart_peach = _heart("peach")
    state = _shen_play_state((heart_peach,))
    ctx = RespondContext(
        cause=WindowCause.BARBARIAN_INVASION,
        required="slash",
        mode=RespondMode.PLAY,
    )
    actions = legal_actions_for(
        state, 0, QueryKind.RESPOND, answers_seq=1, payload=ctx
    )
    assert any(
        action.card.is_virtual and action.card.virtual_sources == (heart_peach,)
        for action in actions
    ), "红桃手牌当【杀】打出"


# ---- 武魂：标记 / 死亡时 -------------------------------------------------

KINDS = {
    FrameKind.DAMAGE,
    FrameKind.DEATH,
    FrameKind.NEAR_DEATH,
    FrameKind.LIFE_DECREASE,
    FrameKind.JUDGE,
}


class _PassOrFirst:
    def __init__(self):
        self.queries = []

    def respond(self, query):
        self.queries.append(query)
        if query.kind in ("RESPOND", "DODGE", "PEACH"):
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return query.legal_actions[0]


def _four_seat_state(*, shen_hp=1, dead_seats=(), deck=None):
    seats = [
        SeatSpec(identity=Identity.LORD),
        SeatSpec(identity=Identity.REBEL, hp=shen_hp, hero=SHEN_GUANYU),
        SeatSpec(identity=Identity.LOYALIST),
        SeatSpec(identity=Identity.RENEGADE),
    ]
    for seat in dead_seats:
        seats[seat] = SeatSpec(
            identity=seats[seat].identity, alive=False
        )
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck if deck is not None else tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


def _run_damage(state, *, source, target, amount=1, markers=()):
    engine = Engine.from_state(state, seed=7)
    for marker in markers:
        engine.add_marker(None, **marker)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": target,
            "channel": None,
            "nature": "NORMAL",
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    players = [_PassOrFirst() for _ in range(len(state.players))]
    for _ in range(20_000):
        if engine.pending is None and not any(
            frame.kind in KINDS for frame in engine.stack.frames
        ):
            break
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    return engine, players


def _marker(seat: int, *, count: int = 1, source_seat: int = 1) -> dict:
    return {
        "seat": seat,
        "name": "梦魇",
        "source_seat": source_seat,
        "source_skill_id": WUHUN,
        "count": count,
        "clear_on_source_death": True,
    }


def _timings(engine, timing):
    return [
        record
        for record in engine.records
        if getattr(record, "kind", None) == "timing"
        and getattr(record, "timing", None) is timing
    ]


def _judge_targets(engine):
    return [
        record.actor
        for record in _timings(engine, Timing.JUDGE_ON)
    ]


def test_wuhun_gives_one_marker_per_damage_point_from_another_character():
    """①逐点：一次 2 点伤害给来源 2 枚（per_point）。"""
    state = _four_seat_state(shen_hp=4)
    engine, _players = _run_damage(state, source=0, target=1, amount=2)
    markers = engine.state.player(0).markers
    assert len(markers) == 1
    assert (markers[0].name, markers[0].count) == ("梦魇", 2)
    assert markers[0].source_seat == 1 and markers[0].source_skill_id == WUHUN


def test_wuhun_does_not_mark_self_or_sourceless_damage():
    for source in (1, None):
        state = _four_seat_state(shen_hp=4)
        engine, _players = _run_damage(state, source=source, target=1)
        assert all(not player.markers for player in engine.state.players)


def test_wuhun_marker_is_public_with_its_source_label():
    state = _four_seat_state(shen_hp=4)
    engine = Engine.from_state(state, seed=7)
    engine.add_marker(
        None,
        0,
        name="梦魇",
        source_seat=1,
        source_skill_id=WUHUN,
        count=2,
        clear_on_source_death=True,
    )
    view = project(engine.state, 0)
    payload = seat_display(
        view.players[0], heroes=load_hero_tables(), players=view.players
    )
    marker = payload["markers"][0]
    assert (marker["name"], marker["count"]) == ("梦魇", 2)
    assert "神关羽" in marker["source_label"] and "武魂" in marker["source_label"]


def test_wuhun_without_marks_does_nothing_on_death():
    state = _four_seat_state()
    engine, _players = _run_damage(state, source=0, target=1)
    assert engine.state.player(1).alive is False
    assert _judge_targets(engine) == []
    assert engine.state.player(2).alive and engine.state.player(3).alive


def test_wuhun_unique_max_marks_judges_without_a_choice():
    slash = cards_of_type(PACK, "slash")[0]
    deck = (slash,) + tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id != slash
    )
    state = _four_seat_state(deck=deck)
    engine, players = _run_damage(
        state, source=0, target=1, markers=(_marker(2, count=2),)
    )
    assert _judge_targets(engine) == [2]
    assert not any(query.kind == "SELECT_OPTION" for player in players for query in player.queries)
    assert engine.state.player(2).alive is False, "非桃 / 桃园结义 ⇒ 直接死亡"
    assert engine.state.player(3).alive


def test_wuhun_tie_lets_the_dying_holder_pick_one():
    deck = (cards_of_type(PACK, "slash")[0],) + tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id != cards_of_type(PACK, "slash")[0]
    )
    state = _four_seat_state(deck=deck)
    engine, players = _run_damage(
        state,
        source=0,
        target=1,
        markers=(_marker(2, count=1), _marker(3, count=1)),
    )
    tie = [
        query
        for player in players
        for query in player.queries
        if query.kind == "SELECT_OPTION" and query.skill_id == "wuhun:pick"
    ]
    assert len(tie) == 1
    assert tie[0].actor == 1, "平票由已经阵亡的神关羽选"
    assert set(tie[0].options) == {"seat:2", "seat:3"}
    assert _judge_targets(engine) == [2], "选中的座位进行判定"
    assert engine.state.player(2).alive is False


def test_wuhun_peach_or_peach_garden_saves_the_judged_character():
    peach = cards_of_type(PACK, "peach")[0]
    deck = (peach,) + tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id != peach
    )
    state = _four_seat_state(deck=deck)
    engine, _players = _run_damage(
        state, source=0, target=1, markers=(_marker(2),)
    )
    assert _judge_targets(engine) == [2]
    assert engine.state.player(2).alive is True


def test_wuhun_ignores_marks_from_another_source():
    state = _four_seat_state()
    engine, _players = _run_damage(
        state,
        source=0,
        target=1,
        markers=(_marker(2, source_seat=0),),
    )
    assert _judge_targets(engine) == []
    assert engine.state.player(2).alive is True


def test_wuhun_does_not_fire_when_the_game_is_already_decided():
    state = _four_seat_state(dead_seats=(3,))
    engine, _players = _run_damage(
        state, source=0, target=1, markers=(_marker(2),)
    )
    assert engine.pending_outcome is not None, "反贼全灭 ⇒ 神关羽死亡即判胜"
    assert _judge_targets(engine) == [], "已判胜 ⇒ 武魂不触发"
    assert engine.state.player(2).alive is True


# ---------------------------------------------------------------------------
# 神吕蒙移动版【涉猎】【攻心】（用户裁定 2026-10-31）
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\神吕蒙\\技能.txt`，逐字）：
#
#     涉猎：摸牌阶段，你可以改为亮出牌堆顶的五张牌，然后获得其中每种花色的牌各一张。
#     攻心：出牌阶段限一次，你可以观看一名其他角色的手牌，然后你可以展示其中一张♥️牌并
#           选择一项：1.弃置此牌；2.将此牌置于牌堆顶。
#
# 用户补充（同轮命令，是解释）：涉猎可取 0~4 张、每花色至多一张、未获得的置入弃牌堆、
# 亮不满即短摸平局；攻心的"可以展示"包含"直接放弃"。
# ---------------------------------------------------------------------------

SHEN_LVMENG = "shen_lvmeng_mobile"
SHELIE = "hero.shen_lvmeng_mobile.shelie"
GONGXIN = "hero.shen_lvmeng_mobile.gongxin"


def _deltas(engine, delta_type: str):
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


def _slot_of(query, card_id: int) -> int:
    for index, slot in enumerate(query.slots):
        if slot.card is not None and slot.card.card_id == card_id:
            return index
    raise AssertionError(f"选择面里没有牌 {card_id}：{query.slots}")


def _pick(query, card_ids: tuple[int, ...]) -> SelectCards:
    return SelectCards(
        picks=tuple(_slot_of(query, card_id) for card_id in card_ids),
        answers_seq=query.seq,
    )


def _first_suit(suit: str, *, excluded=()) -> int:
    return next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in excluded and PACK.deck.cards[card_id].suit == suit
    )


def _shelie_deck(top: tuple[int, ...]) -> tuple[int, ...]:
    """把 ``top`` 依次放在牌堆顶，其余牌接在后面（与界裸衣的测试同形）。"""
    used = set(top)
    return (*top, *(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used))


def _shelie_slots(query) -> tuple[int, ...]:
    return tuple(slot.card.card_id for slot in query.slots if slot.card is not None)


def _shelie_queries(engine):
    return [
        query
        for query in _queries(engine, QueryKind.SELECT_CARDS.value)
        if query.spec.max_per_suit is not None
    ]


class _SheliePlayer:
    """摸牌阶段发动涉猎；在选牌面里每选中的花色各拿第一张（``suits`` 为空 = 一张不拿）。"""

    def __init__(self, suits: tuple[str, ...] = ()) -> None:
        self.suits = tuple(suits)
        self.activations = []
        self.picks = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION.value and query.skill_id == SHELIE:
            self.activations.append(query)
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS.value and query.spec.max_per_suit is not None:
            self.picks.append(query)
            seen: set[str] = set()
            slots = []
            for index, slot in enumerate(query.slots):
                suit = slot.card.suit if slot.card is not None else None
                if suit is None or suit not in self.suits or suit in seen:
                    continue
                seen.add(suit)
                slots.append(index)
            return SelectCards(picks=tuple(sorted(slots)), answers_seq=query.seq)
        if query.kind == QueryKind.PLAY_PHASE.value:
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _shelie_run(
    *,
    top: tuple[int, ...],
    suits: tuple[str, ...],
    discard: tuple[int, ...] = (),
):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=_shelie_deck(top),
        discard=discard,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer(suits=suits)
    run_until(
        engine,
        [player, PassPlayer()],
        lambda e: e.finished or ran_timing(e, Timing.PHASE_PLAY_START),
    )
    return engine, player


def test_shelie_reveals_five_and_takes_one_per_chosen_suit():
    """亮出五张（公开处理区）⇒ 每花色各取一张；未获得的置入弃牌堆；本阶段不摸牌。"""
    heart = _first_suit("heart")
    diamond = _first_suit("diamond", excluded={heart})
    club = _first_suit("club", excluded={heart, diamond})
    spade = _first_suit("spade", excluded={heart, diamond, club})
    heart2 = _first_suit("heart", excluded={heart, diamond, club, spade})
    top = (heart, diamond, club, spade, heart2)
    engine, player = _shelie_run(top=top, suits=("heart", "spade"))

    assert len(player.activations) == 1, "摸牌阶段问一次「是否发动」"
    asked = player.picks
    assert len(asked) == 1
    query = asked[0]
    assert query.actor == 0
    assert query.spec.total == (0, 4)
    assert query.spec.max_per_suit == 1
    assert [slot.ref.zone for slot in query.slots] == [Zone.PROCESSING] * 5
    assert set(_shelie_slots(query)) == set(top), "五张都在处理区、牌面公开"
    # 候选生成期已按花色收窄：任何候选都不能同时拿两张红桃。
    for action in query.legal_actions:
        if not isinstance(action, SelectCards):
            continue
        suits = [PACK.deck.cards[query.slots[index].card.card_id].suit for index in action.picks]
        assert len(suits) == len(set(suits)), f"候选 {action.picks} 同花色出现两次"
    assert any(isinstance(a, SelectCards) and len(a.picks) == 0 for a in query.legal_actions), (
        "0 张也是一个合法候选（放弃 = 提交 0 张）"
    )

    hand = engine.state.player(0).hand
    assert heart in hand and spade in hand
    assert not ({diamond, club, heart2} & set(hand))
    assert {diamond, club, heart2} <= set(engine.state.discard), "未获得的置入弃牌堆"
    assert len(hand) == 2, "改为亮牌 ⇒ 本阶段一张都不摸（不是 2 + 2）"
    assert engine.state.processing == [], "亮牌结算完离开处理区"


def test_shelie_can_take_zero_and_discards_all_five():
    heart = _first_suit("heart")
    diamond = _first_suit("diamond", excluded={heart})
    club = _first_suit("club", excluded={heart, diamond})
    spade = _first_suit("spade", excluded={heart, diamond, club})
    heart2 = _first_suit("heart", excluded={heart, diamond, club, spade})
    top = (heart, diamond, club, spade, heart2)
    engine, _player = _shelie_run(top=top, suits=())

    assert engine.state.player(0).hand == []
    assert set(top) <= set(engine.state.discard), "一张不拿 ⇒ 五张全部置入弃牌堆"


def test_shelie_discards_and_gains_use_put_not_discard():
    heart = _first_suit("heart")
    spade = _first_suit("spade", excluded={heart})
    club = _first_suit("club", excluded={heart, spade})
    diamond = _first_suit("diamond", excluded={heart, spade, club})
    heart2 = _first_suit("heart", excluded={heart, spade, club, diamond})
    top = (heart, spade, club, diamond, heart2)
    engine, _player = _shelie_run(top=top, suits=("club",))

    moves = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_move"
        and record.from_zone.zone is Zone.PROCESSING
    ]
    gained = [
        record
        for record in moves
        if record.to_zone.zone is Zone.HAND
        and {card.card_id for card in record.cards} == {club}
    ]
    leftover = [
        record
        for record in moves
        if record.to_zone.zone is Zone.DISCARD
        and {card.card_id for card in record.cards} == {heart, spade, diamond, heart2}
    ]
    assert len(gained) == 1 and gained[0].reason is MoveReason.PUT
    assert len(leftover) == 1 and leftover[0].reason is MoveReason.PUT, (
        "未获得的牌是规则性置入弃牌堆（PUT），不是弃置（DISCARD）"
    )


def test_shelie_short_reveal_ends_the_game_in_a_draw():
    """短摸（两堆合计不足 5 张）⇒ 游戏直接结束、判平局（用户裁定）。"""
    top = (
        _first_suit("heart"),
        _first_suit("diamond"),
        _first_suit("club"),
        _first_suit("spade"),
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=top,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer()
    run_until(engine, [player, PassPlayer()], lambda e: e.finished)

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"
    assert player.picks == [], "短摸不再发选牌询问"


def test_shelie_offers_nothing_without_a_deck():
    """没有可亮的牌时同样按短摸结束（0 < 5），不进入选牌步。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=(),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer()
    run_until(engine, [player, PassPlayer()], lambda e: e.finished)
    assert engine.finished and engine.records[-1].reason == "both_decks_empty"
    assert player.picks == []


# ---- 攻心（与界吕蒙 ☆攻心共用同一条流水线）-------------------------------


def _gongxin_engine(target_hand: tuple[int, ...]):
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=SHEN_LVMENG, hand=()),
            SeatSpec(hand=target_hand),
        ],
        deck=deck_excluding(PACK, *target_hand),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=7)


def _heart_and_black() -> tuple[int, int]:
    heart = _first_suit("heart")
    black = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit in {"spade", "club"}
    )
    return heart, black


class _GongxinPlayer:
    """发动攻心 → 选座位 1 →（可选）选红桃 → 选处置。"""

    def __init__(self, *, heart: int | None, option: str | None) -> None:
        self.heart = heart
        self.option = option
        self.watch = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION.value:
            # 神吕蒙同时有【涉猎】：本组只测【攻心】，摸牌阶段一律不发动涉猎。
            return next(
                (a for a in query.legal_actions if isinstance(a, Pass)),
                query.legal_actions[0],
            )
        if query.kind == QueryKind.PLAY_PHASE.value:
            return next(
                (a for a in query.legal_actions
                 if isinstance(a, ActivateSkill) and a.skill_id == GONGXIN),
                None,
            ) or next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == QueryKind.SELECT_OPTION.value and query.skill_id == "gongxin:seat":
            return next(
                a for a in query.legal_actions
                if isinstance(a, SelectOption) and a.choice == "seat:1"
            )
        if query.kind == QueryKind.SELECT_CARDS.value:
            self.watch.append(query)
            if self.heart is None:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return _pick(query, (self.heart,))
        if query.kind == QueryKind.SELECT_OPTION.value and query.skill_id == "gongxin:option":
            if self.option is None:
                return query.legal_actions[0]
            return next(
                a for a in query.legal_actions
                if isinstance(a, SelectOption) and a.choice == self.option
            )
        return query.legal_actions[0]


def test_gongxin_mobile_watches_the_whole_hand_and_discards_the_shown_heart():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="discard")
    run_until(engine, [player, PassPlayer()], lambda e: heart in e.state.discard)

    assert len(player.watch) == 1
    assert {card.card_id for card in player.watch[0].revealed} == {black, heart}
    assert [slot.card.card_id for slot in player.watch[0].slots if slot.card] == [heart]
    assert [
        (delta.seat, delta.card.card_id)
        for delta in _deltas(engine, "card_reveal")
    ] == [(1, heart)], "展示是公开事实"
    assert heart in engine.state.discard
    assert heart not in engine.state.player(1).hand


def test_gongxin_mobile_can_put_the_heart_on_top_of_the_deck():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="deck_top")
    run_until(engine, [player, PassPlayer()], lambda e: e.state.deck and e.state.deck[0] == heart)
    assert engine.state.deck[0] == heart
    assert heart not in engine.state.player(1).hand


def test_gongxin_mobile_views_the_hand_even_without_a_heart():
    """用户裁定：目标有手牌就必须能观看——没有红桃也发空面询问，出口是「放弃」。"""
    black_cards = tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit in {"spade", "club"}
    )[:2]
    engine = _gongxin_engine(black_cards)
    player = _GongxinPlayer(heart=None, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)

    assert len(player.watch) == 1, "观看这一步无条件发生"
    query = player.watch[0]
    assert query.slots == (), "没有红桃 ⇒ 可选槽为空"
    assert {card.card_id for card in query.revealed} == set(black_cards), "整副手牌仍授予"
    assert [a.kind for a in query.legal_actions] == ["Pass"], "唯一出口 = 放弃"
    assert _deltas(engine, "card_reveal") == []


def test_gongxin_mobile_may_pass_with_a_heart_and_keeps_the_card():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=None, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    assert heart in engine.state.player(1).hand, "放弃 ⇒ 不展示、不动目标的牌"
    assert _deltas(engine, "card_reveal") == []


def test_gongxin_mobile_timeout_defaults_to_the_first_option():
    """二选一不可放弃、超时取第一个合法项 = 弃置此牌（服务端 ``fallback_action`` 同一判据）。"""
    heart, black = _heart_and_black()
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec(hand=(black, heart))],
        deck=deck_excluding(PACK, black, heart),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    player = _GongxinPlayer(heart=heart, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    # 脚本里 option=None 会去候选里找 choice=None ⇒ 落回第一个候选（= discard）。
    assert heart in engine.state.discard


def test_gongxin_mobile_is_once_per_play_phase():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="discard")
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    play_queries = [
        query
        for query in _queries(engine, QueryKind.PLAY_PHASE.value)
        if query.actor == 0
    ]
    assert len(play_queries) >= 1
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == GONGXIN
        for action in play_queries[-1].legal_actions
    ), "本阶段已经用过一次 ⇒ 不再进候选"



# ---------------------------------------------------------------------------
# 魏延【狂骨】（`feng-weiyan` 票 01）：锁定技，造伤后按「扣减体力前」的距离回复 1 点体力
#
# 牌面（`D:\SGS_HEROS\神话再临-风包\魏延\技能.txt`，逐字）：
#
#     狂骨：锁定技，当你对一名角色造成1点伤害后，若其扣减体力前你计算与其的距离不大于 1，
#           你回复 1 点体力。
#
# 全部走**外部缝**（记录流 / 终局状态）：一条【杀】或一张【南蛮入侵】打出伤害，看魏延的体力、
# `RECOVER` 帧与 `hp_change`。距离这一侧的关键用例是
# `test_kuanggu_uses_the_distance_before_the_hp_deduction`——目标被这一击打死、又带着 +1
# 防御马时，只有"扣减体力前定格"的实现才会给出"不触发"（现算会算成 1 而误回血）。
#
# 两条判据（`edge-cases.md` 的 H2 / H3）：**持有者自校**（伤害必须由魏延造成：别人打人、
# 魏延自己挨打都不触发）与**锁定技不问**（记录流里一条 `SKILL_ACTIVATION` 都没有）。
# "无来源伤害（【闪电】）"与"失去体力（【苦肉】）"是结构排除：前者 `source` 为 `None`、
# 与持有者永不相等；后者走 `LIFE_DECREASE` 帧、根本不到伤害帧的这两个时机。
# ---------------------------------------------------------------------------

KUANGGU = "hero.weiyan.kuanggu"
WEIYAN = "weiyan"

_KUANGGU_SLASH = cards_of_type(PACK, "slash")[0]

_KUANGGU_PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：只有它带【古锭刀】（对无手牌目标 +1）——"2 点伤害 ⇒ 逐点两次"的载体。"""

_KUANGGU_QINGLONG = cards_of_type(PACK, "green_dragon_blade")[0]
"""青龙偃月刀：攻击范围 3——用来把"够不着"的目标变成能使用【杀】的目标（距离 2 仍合法）。"""

_KUANGGU_CHITU = cards_of_type(PACK, "chitu")[0]
"""赤兔（−1 进攻马）：**自己**计算与其他角色的距离 −1。"""

_KUANGGU_DILU = cards_of_type(PACK, "dilu")[0]
"""的卢（+1 防御马）：**其他角色**计算与你的距离 +1。"""


def _kuanggu_use_chain_done(engine) -> bool:
    """这张牌的使用链整个跑完（USE 帧已出栈 + 结算栈空）。

    与 `tests/test_use_and_resolve.py::_use_chain_done` 同姿态：被动局面不会自然收局，故只跑到
    被测链结束为止——死亡结算在链内，濒死 / 死亡 / 造成伤害后都已在记录里。
    """
    popped = any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )
    return popped and not engine.stack


def _kuanggu_find_play(player, query):
    """该座位要打的那张牌（【杀】要求目标正是 ``target``；锦囊只看牌名）。"""
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != player.type_id:
            continue
        if player.type_id == "slash" and action.targets != (player.target,):
            continue
        return action
    return None


class _KuangguActor:
    """座位 0（或指定座位）：打一次指定牌名的牌，其余询问取最小合法应答。"""

    def __init__(self, *, target: int, type_id: str = "slash") -> None:
        self.target = target
        self.type_id = type_id
        self.used = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                action = _kuanggu_find_play(self, query)
                if action is not None:
                    self.used = True
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _kuanggu_engine(
    seats, *, used=(), target=1, type_id="slash", turn_seat=0, seed=17, pack=PACK
):
    """造局面并跑到那一张牌的使用链结束；返回 ``(engine, state)``。"""
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck_excluding(pack, *used),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=seed)
    players = [PassPlayer() for _ in seats]
    players[turn_seat] = _KuangguActor(target=target, type_id=type_id)
    run_queries(engine, players, _kuanggu_use_chain_done)
    return engine, state


def _kuanggu_hp_changes(engine, seat: int):
    """某座位的 ``hp_change`` delta 序列（回复 = before < after）。"""
    return [record for record in _deltas(engine, "hp_change") if record.seat == seat]


def _kuanggu_timings(engine) -> list:
    return [record.timing for record in engine.records if record.kind == "timing"]


def test_kuanggu_recovers_one_after_dealing_damage_within_distance():
    """距离 1 造成 1 点伤害 ⇒ 回复 1 点；锁定技不问（一条 `SKILL_ACTIVATION` 都没有）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(1).hp == 3, "【杀】照常造成 1 点伤害"
    assert state.player(0).hp == 4, "距离 1 ⇒ 狂骨回复 1 点体力"
    assert not [
        query
        for query in _queries(engine, QueryKind.SKILL_ACTIVATION.value)
        if query.skill_id == KUANGGU
    ], "锁定技不问是否发动"


def test_kuanggu_recovery_goes_through_a_recover_frame():
    """回复走**回复体力事件**（`RECOVER_VALUE` / `RECOVER_AFTER`）+ 一条 `hp_change`。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    timings = _kuanggu_timings(engine)
    assert Timing.RECOVER_VALUE in timings and Timing.RECOVER_AFTER in timings
    assert [(change.before, change.after) for change in _kuanggu_hp_changes(engine, 0)] == [
        (3, 4)
    ]


def test_kuanggu_does_not_fire_outside_distance_one():
    """距离 2 ⇒ 不回复；伤害照常结算（条件只管狂骨，不改伤害）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_QINGLONG,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(),
            SeatSpec(hp=4),
            SeatSpec(),
        ],
        target=2,
        used=(_KUANGGU_SLASH, _KUANGGU_QINGLONG),
    )

    assert distance(state, 0, 2) == 2, "四座环上 0 与 2 相距 2"
    assert state.player(2).hp == 3, "伤害照常结算"
    assert state.player(0).hp == 3, "距离 2 ⇒ 狂骨不回复"


def test_kuanggu_ignores_damage_it_did_not_deal():
    """别人造成的伤害不触发——**魏延自己挨打也不回血**（条件必须自校持有者是伤害来源）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3),
            SeatSpec(hand=(_KUANGGU_SLASH,)),
            SeatSpec(),
            SeatSpec(),
        ],
        target=0,
        turn_seat=1,
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(0).hp == 2, "吃 1 点伤害但没回复（伤害不是我造成的）"
    assert Timing.RECOVER_VALUE not in _kuanggu_timings(engine)


def test_kuanggu_triggers_once_per_damage_point():
    """「1 点伤害」⇒ 逐点：古锭刀造 2 点 ⇒ 两次触发、共回复 2 点（两帧）。"""
    pack = _KUANGGU_PACK_160
    scimitar = cards_of_type(pack, "ancient_scimitar")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=2, equip=(scimitar,), hand=(slash,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        pack=pack,
        used=(slash, scimitar),
    )

    assert state.player(1).hp == 2, "古锭刀对**没有手牌**的目标 +1 ⇒ 2 点伤害"
    assert state.player(0).hp == 4, "两点各回复 1 点"
    assert [
        (change.before, change.after) for change in _kuanggu_hp_changes(engine, 0)
    ] == [(2, 3), (3, 4)], "两帧逐点各一次，不是一次回复 2 点"


def test_kuanggu_still_recovers_when_the_lethal_damage_kills_a_character_within_distance():
    """致死也在触发面上：造成伤害后在濒死 / 死亡**之后**跑，距离按扣减体力前照样是 1。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert not state.player(1).alive, "1 点伤害打死 1 体力的目标"
    assert state.player(0).hp == 4, "目标死了也照常回复（距离在扣减体力前定格）"


def test_kuanggu_uses_the_distance_before_the_hp_deduction():
    """**本技能的核心边界**：相邻 + 【的卢】⇒ 扣减体力前距离 2 ⇒ 打死了也不触发。

    这一条能红：若在"造成伤害后"现算距离，死者（装备已弃、装备技不再解析）会算成 1 ⇒ 误回血。
    下面同时断言"现算值确实是 1"，把"为什么必须在扣减体力前定格"留在测试里。
    """
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_QINGLONG,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(hp=1, equip=(_KUANGGU_DILU,)),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH, _KUANGGU_QINGLONG, _KUANGGU_DILU),
    )

    assert not state.player(1).alive, "这一击把目标打死了"
    assert state.player(0).hp == 3, "扣减体力前距离 2（相邻 + 的卢）⇒ 狂骨不回复"
    assert state.player(1).equip == [], "死亡结算已弃置的卢"
    assert distance(state, 0, 1) == 1, "死者现算反而够得着——正是必须定格的原因"


def test_kuanggu_judges_each_damaged_target_separately():
    """一次【南蛮入侵】打三个目标：逐目标各判距离（相邻的两个触发、相距 2 的不触发）。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=1, hand=(barbarian,)),
            SeatSpec(hp=4),
            SeatSpec(hp=4),
            SeatSpec(hp=4),
        ],
        type_id="barbarian_invasion",
        used=(barbarian,),
    )

    assert [state.player(seat).hp for seat in (1, 2, 3)] == [3, 3, 3], "三个目标各 1 点伤害"
    assert state.player(0).hp == 3, "相邻的 1 / 3 号各触发一次（1 → 3），2 号距离 2 不触发"


def test_kuanggu_at_full_hp_recovers_nothing():
    """满体力 ⇒ 不回复（规则 §4.15 L426）：连回复事件都不产生，零 delta。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=4, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(1).hp == 3
    assert state.player(0).hp == 4
    assert _kuanggu_hp_changes(engine, 0) == [], "满体力不写体力"
    assert Timing.RECOVER_VALUE not in _kuanggu_timings(engine), "也不该压回复帧"


def test_kuanggu_reads_the_mount_chain():
    """距离读既有的 `DISTANCE` 链：**赤兔（−1）**让座位距离 2 变 1 ⇒ 触发。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_CHITU,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(),
            SeatSpec(hp=4),
            SeatSpec(),
        ],
        target=2,
        used=(_KUANGGU_SLASH, _KUANGGU_CHITU),
    )

    assert distance(state, 0, 2) == 1, "赤兔把座位距离 2 压成 1（也是【杀】能指定它的原因）"
    assert state.player(2).hp == 3
    assert state.player(0).hp == 4, "计算距离 1 ⇒ 狂骨回复 1 点"


# ---------------------------------------------------------------------------
# 夏侯渊【神速】（`feng-xiahouyuan` 票 01）：回合开始时一至两项 → 跳阶段 + 无距离【杀】
#
# 牌面（`D:\SGS_HEROS\神话再临-风包\夏侯渊\技能.txt`，逐字）：
#
#     神速：回合开始时，你可以选择一至两项：
#           【1】跳过判定阶段和摸牌阶段；
#           【2】跳过出牌阶段并弃置一张装备牌。
#           你每以此法选择一项，便视为你使用一张无距离限制的普通【杀】。
#
# 全部走**外部缝**（询问 / 记录流 / 终局状态）：
#
# - **时点**：「回合开始时」是帧外时机，本技能是它补上派发点后的第一个消费者
#   （`tests/test_hero_catalog.py` 的 `_owner_probes` 因此不必给探针——钩子无条件，
#   "是不是我的回合"由激活计划按帧的 ``source`` 判）；
# - **跳阶段**：`PHASE_JUDGE` / `PHASE_DRAW` / `PHASE_PLAY_START` 记录在场与否 + 判定区的牌保留；
# - **虚拟【杀】**：`USE_DESIGNATE_TARGET` 记录的目标（打得到距离 2 的座位 = 无距离限制）。
#
# 三条容易写错的边界各有一条断言：【2】付不起时**取消整项**（R4）、
# 回合开始时的【杀】**不占**出牌阶段次数（W5-04）、空城目标**不进**池。
# ---------------------------------------------------------------------------

SHENSU = "hero.xiahouyuan.shensu"
XIAHOUYUAN = "xiahouyuan"
XIAHOUDUN = "xiahoudun"
GANGLIE = "hero.xiahoudun.ganglie"

_SHENSU_ARMOR = cards_of_type(PACK, "eight_diagrams")[0]
"""八卦阵（防具）：【2】的成本——**手牌**里的装备牌。"""

_SHENSU_HORSE = cards_of_type(PACK, "dilu")[0]
"""的卢（+1 防御马）：【2】的成本——**装备区**里的装备牌（"装备牌"含两个区）。"""

_SHENSU_INDULGENCE = cards_of_type(PACK, "indulgence")[0]
"""【乐不思蜀】：判定区里的牌（"跳过判定阶段时保留"的载体）。"""

_SHENSU_HAND = tuple(cards_of_type(PACK, "dodge")[:2])
"""手牌里的两张【闪】：不参与任何判据，只用来撑手牌数 / 给出牌阶段用。"""

_SHENSU_SPADE = next(
    card_id
    for card_id, card in enumerate(PACK.deck.cards)
    if card.suit == "spade"
)


def _shensu_option(query, choice: str):
    """这条 ``SELECT_OPTION`` 询问里 ``choice`` 对应的候选。"""
    for action in query.legal_actions:
        if isinstance(action, SelectOption) and action.choice == choice:
            return action
    raise AssertionError(
        f"没有选项 {choice!r}："
        f"{[a.choice for a in query.legal_actions if isinstance(a, SelectOption)]}"
    )


def _shensu_cards_action(query, card_ids):
    """这条 ``SELECT_CARDS`` 询问里"恰好选中 ``card_ids``"的那条候选。"""
    wanted = set(card_ids)
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
    raise AssertionError(f"选择面里选不出 {sorted(wanted)}：{[s.card for s in query.slots]}")


def _shensu_fallback(query):
    """其余询问取最小合法应答：有 Pass 就 Pass，否则第一条。"""
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _ShensuActor:
    """座位 0（夏侯渊）的脚本：答【神速】的每一问；可选在出牌阶段再出一张真【杀】。"""

    def __init__(
        self,
        *,
        activate: bool = True,
        items: str | None = None,
        targets=(),
        picks=(),
        play_slash=(),
        ganglie_choice: str | None = None,
    ) -> None:
        self.activate = activate
        self.items = items
        self.targets = list(targets)
        self.picks = tuple(picks)
        self.play_slash = list(play_slash)
        self.ganglie_choice = ganglie_choice
        self.items_query = None
        self.target_queries = []
        self.cards_queries = []

    def respond(self, query):
        skill = str(getattr(query, "skill_id", "") or "")
        if query.kind == "SKILL_ACTIVATION" and skill == SHENSU:
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return _shensu_fallback(query)
        if query.kind == "SELECT_OPTION" and skill.endswith(":items"):
            self.items_query = query
            return _shensu_option(query, self.items)
        if query.kind == "SELECT_OPTION" and ":target:" in skill:
            self.target_queries.append(query)
            return _shensu_option(query, f"seat:{self.targets.pop(0)}")
        if query.kind == "SELECT_OPTION" and self.ganglie_choice is not None and skill == "ganglie:choice":
            return _shensu_option(query, self.ganglie_choice)
        if query.kind == "SELECT_CARDS":
            self.cards_queries.append(query)
            return _shensu_cards_action(query, self.picks)
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
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return _shensu_fallback(query)


class _GanglieActor:
    """座位 1（夏侯惇）：受伤害后发动【刚烈】，其余取最小合法应答。"""

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == GANGLIE:
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        return _shensu_fallback(query)


def _shensu_engine(seats, *, used=(), top=None, players=None, turn_seat=0, seed=29):
    """造「座位 0 是夏侯渊、从自己的回合起点开跑」的局面，跑到弃牌阶段开始。"""
    deck = tuple(card_id for card_id in deck_excluding(PACK, *used) if card_id != top)
    if top is not None:
        deck = (top, *deck)
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
    run_queries(engine, actors, lambda e: ran_timing(e, Timing.PHASE_DISCARD_START))
    return engine, state


def _shensu_targets(engine):
    """本局每一次"使用并指定目标"的目标元组（按记录序）。"""
    return [
        record.targets
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_DESIGNATE_TARGET
    ]


def _shensu_uses(engine) -> int:
    """本局压过几个 ``USE`` 帧。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.USE
    )


def _shensu_activation_queries(engine):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == SHENSU
    ]


def test_shensu_asks_once_at_own_turn_start_and_declines_cleanly():
    """自己的回合开始时问一次；放弃 ⇒ 什么都不跳、一张【杀】都没有。"""
    actor = _ShensuActor(activate=False)
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert len(_shensu_activation_queries(engine)) == 1
    assert ran_timing(engine, Timing.PHASE_JUDGE), "没发动 ⇒ 判定阶段照常"
    assert ran_timing(engine, Timing.PHASE_DRAW), "没发动 ⇒ 摸牌阶段照常"
    assert _shensu_uses(engine) == 0
    assert len(state.player(0).hand) == 4, "没发动 ⇒ 正常摸两张"


def test_shensu_does_not_trigger_on_another_turn_start():
    """别人的回合开始时**不问**（自校：只有当前回合角色的那一份实例被问）。

    先手座位只能在**无身份局**（2 人）里配置（身份局的先手 = 主公座位），故这条用例
    单独造一个 2 人局：座位 1 先手，夏侯渊在座位 0。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=XIAHOUYUAN), SeatSpec(hand=_SHENSU_HAND)],
        deck=deck_excluding(PACK, *_SHENSU_HAND),
        turn_seat=1,
        phase=Phase.PREPARE,
        rule_config=RuleConfig(
            player_count=2, starting_hand_size=4, first_seat=1
        ),
    )
    engine = Engine.from_state(state, seed=29)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DISCARD_START),
    )

    assert not _shensu_activation_queries(engine)
    assert ran_timing(engine, Timing.TURN_START), "回合确实开始了，只是不是他的"


def test_shensu_option_one_skips_judge_and_draw_and_uses_one_slash():
    """选【1】：判定 + 摸牌阶段整体越过、准备阶段照常，换来一张无距离【杀】。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert not ran_timing(engine, Timing.PHASE_JUDGE), "判定阶段被跳过"
    assert not ran_timing(engine, Timing.PHASE_DRAW), "摸牌阶段被跳过"
    assert ran_timing(engine, Timing.PHASE_PREPARE), "只跳两个阶段，准备阶段照常"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3, "【杀】造成 1 点伤害"
    assert len(state.player(0).hand) == 2, "摸牌阶段被跳过 ⇒ 一张都没摸"


def test_shensu_skipped_judge_phase_keeps_the_judge_zone_cards():
    """跳过的判定阶段**不消费**判定区里的牌（账本行；规则 §11.5）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(1,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, judge=(_SHENSU_INDULGENCE,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_INDULGENCE,),
        players=[actor],
    )

    assert state.player(0).judge == [_SHENSU_INDULGENCE], "延时锦囊留在判定区"
    assert not ran_timing(engine, Timing.PHASE_JUDGE)
    assert not [
        record
        for record in engine.records
        if record.kind == "frame" and record.frame_kind is FrameKind.JUDGE
    ], "一张判定都没开"


def test_shensu_slash_ignores_distance():
    """【杀】的目标**不受攻击范围限制**：座位 2 在距离 2、攻击范围 1 之外，仍可指定。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        players=[actor],
    )

    assert distance(state, 0, 2) == 2, "座位 2 与座位 0 的距离是 2（攻击范围 1 够不着）"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3


def test_shensu_slash_respects_cannot_be_targeted():
    """「无距离限制」只豁免距离：**不能成为目标**的锁定技照常生效（空城）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    _engine, _state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(hero="jie_zhugeliang", hand=()),
            SeatSpec(),
        ],
        players=[actor],
    )

    assert actor.target_queries, "有合法目标 ⇒ 照常发问"
    choices = [
        action.choice
        for action in actor.target_queries[0].legal_actions
        if isinstance(action, SelectOption)
    ]
    assert "seat:1" not in choices, "空城的界诸葛亮不能成为【杀】的目标"
    assert "seat:2" in choices


def test_shensu_without_a_legal_target_skips_only_the_slash():
    """一个合法目标都没有 ⇒ 不发空询问：这一张【杀】不发生，跳过阶段照旧生效。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw")
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(hero="jie_zhugeliang", hand=()),
        ],
        players=[actor],
    )

    assert not actor.target_queries, "不发没有候选的询问"
    assert _shensu_uses(engine) == 0
    assert not ran_timing(engine, Timing.PHASE_DRAW), "跳过阶段照旧"
    assert ran_timing(engine, Timing.PHASE_PLAY_START)


def test_shensu_option_two_disappears_without_equipment():
    """没有装备牌 ⇒ 选项步只剩【1】（不能执行的项不出现）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(1,))
    _engine, _state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert actor.items_query is not None
    choices = [
        action.choice
        for action in actor.items_query.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert choices == ["shensu_skip_judge_draw"]


def test_shensu_option_two_discards_one_equipment_and_skips_the_play_phase():
    """选【2】：弃一张**装备牌**（手牌 / 装备区都铺）→ 跳过出牌阶段 → 一张【杀】。"""
    actor = _ShensuActor(
        items="shensu_skip_play_equip", targets=(2,), picks=(_SHENSU_HORSE,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(
                hero=XIAHOUYUAN,
                hand=(_SHENSU_ARMOR, *_SHENSU_HAND),
                equip=(_SHENSU_HORSE,),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR, _SHENSU_HORSE),
        players=[actor],
    )

    assert actor.cards_queries, "弃装备是一问"
    face = {
        slot.card.card_id
        for slot in actor.cards_queries[0].slots
        if slot.card is not None
    }
    assert face == {_SHENSU_ARMOR, _SHENSU_HORSE}, "手牌 + 装备区的装备牌都在面上"
    assert _SHENSU_HORSE in state.discard, "选中的装备区牌进了弃牌堆"
    assert not ran_timing(engine, Timing.PHASE_PLAY_START), "出牌阶段被跳过"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3


def test_shensu_both_options_use_two_slashes_in_order():
    """两项都选：先【1】的一张【杀】、再弃装备、【2】的一张【杀】（固定次序）。"""
    actor = _ShensuActor(
        items="shensu_both", targets=(2, 3), picks=(_SHENSU_ARMOR,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(_SHENSU_ARMOR, *_SHENSU_HAND)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR,),
        players=[actor],
    )

    assert not ran_timing(engine, Timing.PHASE_JUDGE)
    assert not ran_timing(engine, Timing.PHASE_DRAW)
    assert not ran_timing(engine, Timing.PHASE_PLAY_START)
    assert _shensu_uses(engine) == 2
    assert _shensu_targets(engine) == [(2,), (3,)]
    assert state.player(2).hp == 3 and state.player(3).hp == 3

    # 次序：第一张【杀】结算完才弃装备，弃完才用第二张。
    uses = [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_DESIGNATE_TARGET
    ]
    moves = [
        record.seq
        for record in engine.records
        if record.kind == "delta"
        and getattr(record, "delta_type", None) == "card_move"
        and record.to_zone.zone is Zone.DISCARD
        and any(card.card_id == _SHENSU_ARMOR for card in record.cards)
    ]
    assert len(uses) == 2 and len(moves) == 1
    assert uses[0] < moves[0] < uses[1]


def test_shensu_cancels_option_two_when_the_equipment_is_stolen_mid_flow():
    """【2】付不起时**取消整项**（R4）：第一张【杀】的结算里装备被【刚烈】弃光。

    结算顺序 = 【1】的【杀】→（夏侯惇【刚烈】让夏侯渊弃两张手牌）→【2】的成本步。
    此刻一张装备牌都没有 ⇒ 不跳出牌阶段、不出第二张【杀】，已进行的【1】不回退。
    """
    actor = _ShensuActor(
        items="shensu_both",
        targets=(1,),
        picks=(_SHENSU_ARMOR, _SHENSU_HORSE),
        ganglie_choice="discard_two",
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(_SHENSU_ARMOR, _SHENSU_HORSE)),
            SeatSpec(hero=XIAHOUDUN),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR, _SHENSU_HORSE),
        top=_SHENSU_SPADE,
        players=[actor, _GanglieActor()],
    )

    assert state.player(0).hand == [], "【刚烈】让夏侯渊弃掉了那两张装备牌"
    assert _shensu_uses(engine) == 1, "只有【1】的一张【杀】"
    assert _shensu_targets(engine) == [(1,)]
    assert not ran_timing(engine, Timing.PHASE_DRAW), "【1】的跳过照旧"
    assert ran_timing(engine, Timing.PHASE_PLAY_START), "【2】取消 ⇒ 出牌阶段没被跳过"


def test_shensu_slash_does_not_consume_the_play_phase_slash_limit():
    """回合开始时的那张【杀】**不占**出牌阶段的次数（规则 §17.10 A2 / 账本 W5-04）。"""
    actor = _ShensuActor(
        items="shensu_skip_judge_draw", targets=(1,), play_slash=(1,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(cards_of_type(PACK, "slash")[0],)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(cards_of_type(PACK, "slash")[0],),
        players=[actor],
    )

    assert _shensu_uses(engine) == 2, "出牌阶段还能出第二张【杀】（额度没被回合开始时那张占掉）"
    assert state.player(1).hp == 2, "两张【杀】各造成 1 点伤害"
    assert state.player(0).card_use_counts.get("slash", 0) == 1, (
        "计数只数到出牌阶段的那一张：回合开始时的虚拟【杀】用 counts=False"
    )


def test_shensu_hero_is_in_the_default_pool():
    """夏侯渊进默认启用池（风包、魏、4 体力、神速）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    assert XIAHOUYUAN in enabled_hero_ids()
    hero = hero_by_id(XIAHOUYUAN)
    assert hero is not None
    assert hero.true_name == "夏侯渊"
    assert hero.kingdom.value == "wei"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["shensu"]
