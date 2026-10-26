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

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import ActivateSkill, EndPhase, Pass, Phase, UseCard, WindowCause
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
