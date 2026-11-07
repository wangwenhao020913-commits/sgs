"""自制包·锋·于禁：【定乱】/【节钺】+「【定乱】牌」的标签语义（feng-yujin 票 01）。

判据一律走**外部缝**：询问载荷（``kind`` / ``actor`` / ``spec`` / ``skill_id``）、
``legal_actions`` 的候选集合、``card_reveal`` / ``tag_add`` 记录、技能实例与牌标签状态；
不读回调内部结构。

本文件同时钉住框架票 ``02``（``Timing.ROUND_START`` + 共享技逐座位派发）与 ``03``
（``CardTagType.DINGLUAN`` 的禁令 / 进入弃牌堆即清 / 标签自身效果）的行为面。
"""

from __future__ import annotations

import pytest

from sgs.data import CardTagType, Identity, RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.skills import conversion_offers
from sgs.skills.heroes import CONVERSION_SKILLS
from sgs.state import (
    ActivateSkill,
    CardTag,
    EndPhase,
    MoveReason,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="standard_108")

YUJIN = "feng_yujin"
DINGLUAN = "hero.feng_yujin.dingluan"
DINGLUAN_MARK = "hero.feng_yujin.dingluan_mark"
JIEYUE = "hero.feng_yujin.jieyue"

DINGLUAN_TAG = CardTagType.DINGLUAN


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _tag(engine: Engine, card_id: int, *, owner: int = 0) -> None:
    """给一张牌挂【定乱】标签（走引擎的唯一写入口，与技能同一条路）。"""
    engine.add_card_tag(
        card_id,
        CardTag(tag_type=DINGLUAN_TAG, owner=owner, skill_id=DINGLUAN),
    )


def _picks(query, count: int):
    """从候选里挑"张数 = 期望值"的那一条（候选自带 ``answers_seq``）。"""
    low, high = query.spec.total
    want = max(low, min(high if high is not None else len(query.slots), count))
    choices = [a for a in query.legal_actions if isinstance(a, SelectCards)]
    wanted = [a for a in choices if len(a.picks) == want]
    if wanted:
        return wanted[0]
    return choices[0]


def _pass(query):
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Driver:
    """按脚本应答（座位可配；定乱 / 节钺 / 通用询问各一档）。"""

    def __init__(
        self,
        *,
        activate_dingluan: bool = False,
        target: int | None = None,
        jieyue: bool = False,
        choice: str = "jieyue_mark",
        pick: int = 1,
    ) -> None:
        self.activate_dingluan = activate_dingluan
        self.target = target
        self.jieyue = jieyue
        self.choice = choice
        self.pick = pick
        self.activations: list = []
        self.selections: list = []
        self.options: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == DINGLUAN:
            self.activations.append(query)
            if not self.activate_dingluan:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(
                a for a in query.legal_actions if isinstance(a, ActivateSkill)
            )
        if query.kind == "SELECT_OPTION":
            self.options.append(query)
            return self._option(query)
        if query.kind == "SELECT_CARDS":
            self.selections.append(query)
            return _picks(query, self.pick)
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return _pass(query)

    def _option(self, query):
        choices = [a for a in query.legal_actions if isinstance(a, SelectOption)]
        if not choices:
            return _pass(query)
        skill_id = str(query.skill_id or "")
        if skill_id.endswith("choice"):
            wanted = [a for a in choices if a.choice == self.choice]
            return wanted[0] if wanted else choices[0]
        if self.target is not None:
            wanted = [a for a in choices if a.choice == f"seat:{self.target}"]
            if wanted:
                return wanted[0]
        return choices[0]


def _engine(
    *,
    seats: list[SeatSpec],
    turn_seat: int = 0,
    hand0: tuple[int, ...] = (),
    phase=None,
) -> Engine:
    """构造局面：**所有座位手里的牌都从牌堆里排掉**（否则同一张牌会同时在牌堆与手牌里，
    ``locate`` 只认一个来源区，搬牌时就会复制出来）。"""
    del hand0
    used = {
        int(card_id)
        for spec in seats
        for zone in (spec.hand, spec.equip, spec.judge)
        for card_id in zone
    }
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=_deck_excluding(*sorted(used)),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=len(seats), first_seat=turn_seat),
    )
    return Engine.from_state(state, seed=37)


def _play_phase(engine: Engine, players, seat: int = 0) -> Engine:
    return run_queries(
        engine,
        players,
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and int(current.pending.actor) == seat,
    )


def _use_cards(query) -> list[int]:
    """出牌阶段候选里所有"使用实体牌"的牌 id。"""
    return [
        int(action.card.card_id)
        for action in query.legal_actions
        if action.kind == "UseCard" and not action.card.is_virtual
    ]


# ---------------------------------------------------------------------------
# §标签：禁令 / 进入弃牌堆即清 / 标签自身效果（框架票 03）
# ---------------------------------------------------------------------------


def test_a_tagged_slash_cannot_be_played_but_an_untagged_one_can():
    """R4=A：被标记的牌**本来就是【杀】**也不能使用 / 打出。"""
    slashes = _cards("slash")
    tagged, clean = slashes[0], slashes[1]
    engine = _engine(
        seats=[SeatSpec(hero=YUJIN, hand=(tagged, clean)), SeatSpec()],
        hand0=(tagged, clean),
    )
    _tag(engine, tagged)
    driver = _Driver()
    _play_phase(engine, [driver, _Driver()])
    available = _use_cards(engine.pending)
    assert clean in available
    assert tagged not in available, "【定乱】牌不能作为【杀】使用"


def test_a_tagged_card_cannot_be_a_conversion_source_for_a_slash():
    """R4=A：不能作为转化【杀】的源牌——单来源（武圣）的候选面里也没有它。"""
    reds = [
        card_id
        for card_id in _cards("slash") + _cards("peach") + _cards("dodge")
        if PACK.deck.cards[card_id].suit in ("heart", "diamond")
    ]
    tagged = reds[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="guanyu", hand=(tagged,)), SeatSpec()],
        deck=_deck_excluding(tagged),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=11)
    _tag(engine, tagged)
    assert conversion_offers(engine.state, 0, "slash") == (), (
        "被标记的红牌不能再「当【杀】」用（武圣的源筛选要读到禁令）"
    )
    assert any(skill.target_type_id == "slash" for skill in CONVERSION_SKILLS)


def test_the_mark_clears_when_the_card_enters_the_discard_pile():
    """清除触发 = ENTER_DISCARD（"直到进入弃牌堆"）。"""
    card = _cards("peach")[0]
    engine = _engine(seats=[SeatSpec(hero=YUJIN, hand=(card,)), SeatSpec()], hand0=(card,))
    _tag(engine, card)
    assert engine.state.tags_of(card), "前置：标签已挂上"
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
    )
    assert not engine.state.tags_of(card), "进入弃牌堆后标签必须消失"


def test_a_marked_card_blocks_a_multi_source_slash_conversion():
    """R4=A：多来源转化（丈八蛇矛）的源牌里只要有一张被标记，整条「当【杀】」就不合法。"""
    spear = _cards("serpent_spear")[0]
    hand = (_cards("peach")[0], _cards("dodge")[0])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=YUJIN, hand=hand, equip=(spear,)), SeatSpec()],
        deck=_deck_excluding(spear, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=23)
    _tag(engine, hand[0])
    driver = _Driver()
    _play_phase(engine, [driver, _Driver()])
    virtual_slashes = [
        action
        for action in engine.pending.legal_actions
        if action.kind == "UseCard"
        and action.card.is_virtual
        and action.card.type == "slash"
    ]
    assert virtual_slashes == [], "丈八的源牌里有一张【定乱】牌 ⇒ 产生不出虚拟【杀】"


def test_the_mark_effect_survives_the_writers_death():
    """E2：牌面写「直到进入弃牌堆」——于禁身死也不撤回标记，标签自身效果照旧。"""
    card = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=YUJIN, alive=False, identity=Identity.LORD),
            SeatSpec(hand=(card,), identity=Identity.REBEL),
        ],
        deck=_deck_excluding(card),
        turn_seat=1,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=19)
    _tag(engine, card, owner=0)
    assert engine.state.tags_of(card), "前置：标记还在（不随来源消失）"
    before = len(engine.state.player(1).hand)
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
    )
    assert len(engine.state.player(1).hand) == before - 1 + 1, "于禁已阵亡：标签效果仍摸一张"


def test_the_owner_draws_when_a_marked_card_is_discarded_not_by_use():
    """标签自身效果：非因使用 / 打出进弃牌堆 ⇒ 拥有者摸 1 张（R5：只看手牌 / 装备区）。"""
    card = _cards("peach")[0]
    filler = _cards("dodge")[0]
    engine = _engine(
        seats=[SeatSpec(hero=YUJIN, hand=(card, filler)), SeatSpec()],
        hand0=(card, filler),
    )
    _tag(engine, card)
    before = len(engine.state.player(0).hand)
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
    )
    after = len(engine.state.player(0).hand)
    assert after == before - 1 + 1, "失去一张、又摸一张"
    assert card in [int(c) for c in engine.state.discard]


def test_no_draw_when_the_marked_card_is_used():
    """「非因使用或打出」——使用 / 打出的移动原因都是 ``USE``，不触发。"""
    card = _cards("peach")[0]
    engine = _engine(seats=[SeatSpec(hero=YUJIN, hand=(card,)), SeatSpec()], hand0=(card,))
    _tag(engine, card)
    before = len(engine.state.player(0).hand)
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.USE,
        owner=0,
    )
    assert len(engine.state.player(0).hand) == before - 1, "使用进弃牌堆不摸牌"


def test_no_draw_when_the_marked_card_enters_discard_from_another_zone():
    """R5：拥有者只看手牌 / 装备区——判定区进弃牌堆不摸。"""
    card = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=YUJIN, judge=(card,)), SeatSpec()],
        deck=_deck_excluding(card),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=13)
    _tag(engine, card)
    before = len(engine.state.player(0).hand)
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.JUDGE,
        owner=0,
    )
    assert len(engine.state.player(0).hand) == before, "判定区的牌进弃牌堆：无人可摸"
    assert not engine.state.tags_of(card)


# ---------------------------------------------------------------------------
# §定乱：共享技 + 轮边界逐座位派发（框架票 02）
# ---------------------------------------------------------------------------


def _round_starts(engine: Engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.ROUND_START
    )


def _four_seats() -> list[SeatSpec]:
    """4 人身份局（座位 0 是主公）：``RuleConfig.player_count`` 只收 2/4/5/6/7/8。"""
    slashes = _cards("slash")
    return [
        SeatSpec(
            hero=YUJIN,
            hand=(slashes[0], slashes[1], _cards("peach")[0], _cards("dodge")[0]),
            identity=Identity.LORD,
        ),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.RENEGADE),
    ]


def test_dingluan_asks_every_seat_in_order_at_the_round_start():
    """A1 + P1：每轮开始时按座位序逐座位问一次「是否发动」；共享技不限于持有者。"""
    engine = _engine(seats=_four_seats(), hand0=(_cards("slash")[0],))
    drivers = [_Driver() for _ in range(4)]
    run_queries(engine, drivers, lambda current: _round_starts(current) >= 2)
    asked = [int(query.actor) for driver in drivers for query in driver.activations]
    assert asked[:4] == [0, 1, 2, 3], "第一轮：从本轮起始座位起按座位序各问一次"
    assert all(query.skill_id == DINGLUAN for d in drivers for query in d.activations)


def test_dingluan_markers_are_public_and_the_cards_are_revealed():
    """标记 = 公开事实；展示走 `card_reveal`（不移动牌）。"""
    target_card = _cards("dodge")[0]
    engine = _engine(
        seats=[SeatSpec(hero=YUJIN, hand=(_cards("slash")[0],)), SeatSpec(hand=(target_card,))],
        hand0=(_cards("slash")[0],),
    )
    driver = _Driver(activate_dingluan=True, target=1)
    players = [driver, _Driver(), _Driver()]
    run_queries(
        engine,
        players,
        lambda current: bool(engine.state.tags_of(target_card)),
    )
    tags = engine.state.tags_of(target_card)
    assert [(tag.tag_type, tag.owner) for tag in tags] == [(DINGLUAN_TAG, 0)]
    reveals = [
        record
        for record in engine.records
        if record.kind == "delta" and getattr(record, "delta_type", "") == "card_reveal"
    ]
    assert any(int(record.card.card_id) == target_card for record in reveals), (
        "展示 = card_reveal（不移动牌）"
    )
    assert target_card in [int(c) for c in engine.state.player(1).hand], "展示不移动牌"


def test_dingluan_x_shrinks_with_every_activation_and_a_used_seat_is_skipped():
    """X = 3 − 全局发动次数；已发动过的座位整局不再问（A1 每人整局 1 次）。"""
    engine = _engine(seats=_four_seats(), hand0=(_cards("slash")[0],))
    first = _Driver(activate_dingluan=True, target=0)
    others = [_Driver() for _ in range(3)]
    players = [first, *others]
    # 第 1 轮：座位 0 发动（X=3）；其余座位放弃。停在第 3 轮的轮边界 ⇒ 第 1、2 轮各问完。
    run_queries(engine, players, lambda current: _round_starts(current) >= 3)
    totals = [query.spec.total for query in first.selections]
    assert totals and totals[0][1] == 3, "第一次发动 X=3"
    assert len(first.activations) == 1, "A1：座位 0 整局只被问一次（发动后不再问）"
    for driver in others:
        assert len(driver.activations) == 2, "其余座位每轮各被问一次"


def test_dingluan_is_not_offered_when_x_reaches_zero():
    """三次发动之后 X=0 ⇒ 轮开始时连问都不问（共享技的预算耗尽）。"""
    engine = _engine(seats=_four_seats(), hand0=(_cards("slash")[0],))
    # 直接落位三次"已发动"（座位位段是唯一真源）。
    from sgs.skills.hero_yujin import _mark_dingluan_used

    for seat in range(3):
        _mark_dingluan_used(engine.state, seat)
    drivers = [_Driver() for _ in range(4)]
    run_queries(engine, drivers, lambda current: _round_starts(current) >= 2)
    assert all(len(driver.activations) == 0 for driver in drivers), "X=0 不再产出询问"


# ---------------------------------------------------------------------------
# §节钺：出牌阶段限一次 + 两条分支（R8=A / R9=1..2 / R10 兜底 Pass）
# ---------------------------------------------------------------------------


def _jieyue_engine(hand0: tuple[int, ...], hand1: tuple[int, ...]) -> Engine:
    return _engine(
        seats=[SeatSpec(hero=YUJIN, hand=hand0), SeatSpec(hand=hand1)],
        hand0=hand0,
        phase=Phase.PLAY,
    )


def test_jieyue_is_once_per_play_phase():
    """出牌阶段限一次：发过一次之后，候选里不再出现。"""
    hand0 = (_cards("peach")[0], _cards("dodge")[0])
    hand1 = (_cards("peach")[1],)
    engine = _jieyue_engine(hand0, hand1)
    driver = _Driver(jieyue=True, choice="jieyue_damage", pick=1)
    _play_phase(engine, [driver, _Driver()])
    ids = [action.skill_id for action in engine.pending.legal_actions if action.kind == "ActivateSkill"]
    assert JIEYUE in ids
    engine.submit(
        next(
            action
            for action in engine.pending.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIEYUE
        )
    )
    # 二选一：选"弃置并造成伤害"，把这一轮发动走完。
    run_queries(engine, [driver, _Driver()], lambda current: current.pending is None or current.pending.kind != "SELECT_OPTION")
    # 回到出牌阶段再问一次：JIEYUE 不再候选。
    run_queries(
        engine,
        [driver, _Driver()],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and int(current.pending.actor) == 0,
    )
    ids = [
        action.skill_id
        for action in engine.pending.legal_actions
        if action.kind == "ActivateSkill"
    ]
    assert JIEYUE not in ids, "出牌阶段限一次"


def test_jieyue_target_hands_over_one_card_of_its_own_choice():
    """R8=A：交出者挑——第 2 步的应答者是目标，选择面铺他自己的手牌（可见）。"""
    hand0 = (_cards("peach")[0],)
    hand1 = (_cards("dodge")[0], _cards("slash")[0])
    engine = _jieyue_engine(hand0, hand1)
    target_driver = _Driver(jieyue=True, pick=1)
    players = [_Driver(jieyue=True, choice="jieyue_damage", pick=1), target_driver]
    _play_phase(engine, players)
    engine.submit(
        next(
            action
            for action in engine.pending.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIEYUE
        )
    )
    run_queries(
        engine,
        players,
        lambda current: bool(target_driver.selections),
    )
    give = target_driver.selections[0]
    assert int(give.actor) == 1, "交出牌的是目标自己"
    assert all(slot.card is not None for slot in give.slots), "自己的手牌可见"


def test_jieyue_mark_branch_marks_shows_and_gives():
    """选项 1：选自己的牌 → 标记【定乱】→ 展示 → 交给目标（牌上的标记保留）。"""
    own = (_cards("peach")[0], _cards("dodge")[0])
    hand1 = (_cards("slash")[0],)
    engine = _jieyue_engine(own, hand1)
    driver = _Driver(jieyue=True, choice="jieyue_mark", pick=2)
    players = [driver, _Driver()]
    _play_phase(engine, players)
    engine.submit(
        next(
            action
            for action in engine.pending.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIEYUE
        )
    )
    run_queries(
        engine,
        players,
        lambda current: len(engine.state.player(1).hand) == 2,
    )
    target_hand = [int(card) for card in engine.state.player(1).hand]
    assert own[0] in target_hand and own[1] in target_hand, "选项 1 把这些牌交给目标"
    for card_id in own:
        assert engine.state.tags_of(card_id), "交出去时仍带着【定乱】标记"


def test_jieyue_damage_branch_discards_then_hits():
    """选项 2：弃置至多 2 张自己的牌 → 对目标造成 1 点伤害。"""
    own = (_cards("peach")[0], _cards("dodge")[0])
    hand1 = (_cards("slash")[0],)
    engine = _jieyue_engine(own, hand1)
    driver = _Driver(jieyue=True, choice="jieyue_damage", pick=1)
    players = [driver, _Driver()]
    _play_phase(engine, players)
    engine.submit(
        next(
            action
            for action in engine.pending.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIEYUE
        )
    )
    before_hp = engine.state.player(1).hp
    run_queries(
        engine,
        players,
        lambda current: engine.state.player(1).hp < before_hp,
    )
    assert engine.state.player(1).hp == before_hp - 1
    assert hand1[0] not in [int(card) for card in engine.state.player(1).hand], (
        "目标交出的牌归发动者（选项 2 不会把它交回去）"
    )


def test_jieyue_offers_a_fallback_pass_when_neither_branch_can_run():
    """R10：正常无 Pass；自身没有手牌也没有装备时给一条**兜底 Pass**（避免卡死）。

    构造：构造局面里发动者 0 张牌、目标 1 张牌——交完那一张后发动者恰好 1 张牌，
    故这一档**走不到**；这里直接以"发动者 0 张牌 + 已交出"的帧进效果体，验证
    选项询问退化成了只有 `Pass` 的面（用户 2026-11-07 的兜底要求）。
    """
    from sgs.skills import active as active_module
    from sgs.resolution import Frame
    from sgs.state import FrameKind

    hand1 = (_cards("slash")[0],)
    engine = _jieyue_engine((), hand1)
    frame = Frame(
        kind=FrameKind.SKILL,
        data={"source": 0, "targets": (), "card": None, "skill_id": JIEYUE},
        frame_id=1,
    )
    # 段 1 已搬过（`_JIEYUE_GIVEN` 置真）+ 发动者手牌 / 装备都为 0 ⇒ 两支都不可执行。
    frame.data[active_module._JIEYUE_GIVEN] = True
    active_module._jieyue_effect(engine, frame, {"seat": (1,), "give": (hand1[0],)})
    assert engine.pending is not None and engine.pending.kind == "SELECT_OPTION"
    choices = [
        action for action in engine.pending.legal_actions if isinstance(action, SelectOption)
    ]
    assert choices == [], "两支都不可执行：面上没有可选项"
    assert any(isinstance(action, Pass) for action in engine.pending.legal_actions), (
        "留一条兜底 Pass"
    )
