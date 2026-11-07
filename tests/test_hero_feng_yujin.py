"""自制包·锋·于禁：【定乱】/【节钺】+「【定乱】牌」的标签语义（feng-yujin 票 01）。

判据一律走**外部缝**：询问载荷（``kind`` / ``actor`` / ``spec`` / ``skill_id``）、
``legal_actions`` 的候选集合、``card_reveal`` / ``tag_add`` 记录、技能实例与牌标签状态；
不读回调内部结构。

本文件同时钉住框架票 ``02``（``Timing.ROUND_START`` 轮边界派发）与 ``03``
（``CardTagType.DINGLUAN`` 的禁令 / 进入弃牌堆即清 / 标签自身效果）的行为面；票 ``07``
起【定乱】是**于禁自己的技能**（B2）——轮开始时**只问持有者**，别的座位零询问；
票 ``08`` 起限次在**目标侧**（D1）——每名角色整局至多被定乱 1 次，目标池排除已被
定乱过的座位（含自己），X = 3 − 已被定乱过的角色数。
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


class _PerRoundDriver(_Driver):
    """按轮次换目标座位的脚本驱动（票 08 主判据要"第 1 轮敌方 / 第 2 轮自己"）。"""

    def __init__(self, targets: list[int]) -> None:
        super().__init__(activate_dingluan=True)
        self._targets = list(targets)

    def respond(self, query):
        if (
            query.kind == "SKILL_ACTIVATION"
            and query.skill_id == DINGLUAN
            and self._targets
        ):
            self.target = self._targets.pop(0)
        return super().respond(query)


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
# §定乱：轮边界派发，只问持有者 + 目标侧限次（框架票 02 + feng-yujin 票 07 / 08）
# ---------------------------------------------------------------------------


def _round_starts(engine: Engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.ROUND_START
    )


def _marks(engine: Engine) -> tuple[int, ...]:
    """已经被定乱过的座位（票 08 的记账位段 = "每名角色整局至多被定乱 1 次"的唯一真源）。"""
    from sgs.skills.hero_yujin import _seat_used

    return tuple(
        seat
        for seat in range(len(engine.state.players))
        if _seat_used(engine.state, seat)
    )


def _x(engine: Engine) -> int:
    """【定乱】的当前 X（= 3 − 已被定乱过的角色数）。"""
    from sgs.skills.hero_yujin import _remaining_x

    return _remaining_x(engine.state)


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


def test_dingluan_is_not_offered_to_a_seat_that_does_not_own_the_skill():
    """B2（票 07）：【定乱】是于禁自己的技能 ⇒ 没有这条技能的座位连候选都没有。

    用户 2026-11-07 的 2 人局实测里，座位 1 是 ``pangtong_mobile`` 却被问了一次并自行发动；
    反向断言（旧读法是"每个座位各问一次"）：座位 1 整轮零询问。
    """
    engine = _engine(
        seats=[
            SeatSpec(hero=YUJIN, hand=(_cards("slash")[0],)),
            SeatSpec(hero="pangtong_mobile", hand=(_cards("dodge")[0],)),
        ],
        hand0=(_cards("slash")[0],),
    )
    owner = _Driver()
    other = _Driver()
    run_queries(engine, [owner, other], lambda current: _round_starts(current) >= 2)
    assert other.activations == [], "座位 1 没有【定乱】⇒ 不该被问"
    assert len(owner.activations) == 1, "对照臂：持有者在本轮开始时确实被问了一次"


def test_the_owner_passing_leaves_no_tag_and_no_reveal():
    """B2（票 07）：「放弃 = 无事发生」的正面断言。

    座位 1 的 driver **愿意**发动（旧读法下它会被问、并自行亮牌）——放弃之后桌上不该
    出现任何【定乱】牌，也不该有任何 ``card_reveal`` 记录。
    """
    target_card = _cards("dodge")[0]
    engine = _engine(
        seats=[
            SeatSpec(hero=YUJIN, hand=(_cards("slash")[0],)),
            SeatSpec(hero="pangtong_mobile", hand=(target_card,)),
        ],
        hand0=(_cards("slash")[0],),
    )
    owner = _Driver()
    other = _Driver(activate_dingluan=True, target=0)
    run_queries(engine, [owner, other], lambda current: _round_starts(current) >= 2)
    assert owner.activations, "对照臂：持有者被问过（它选了放弃）"
    assert engine.state.card_tags == {}, "放弃之后没有任何牌被标记为【定乱】牌"
    reveals = [
        record
        for record in engine.records
        if record.kind == "delta" and getattr(record, "delta_type", "") == "card_reveal"
    ]
    assert reveals == [], "放弃之后没有任何牌被展示"


def test_dingluan_is_asked_only_at_the_owners_seat_every_round_start():
    """B2：轮开始时**只问持有者**；放弃之后下一轮开始时仍会再被问（不是"整局只问一次"）。"""
    engine = _engine(seats=_four_seats(), hand0=(_cards("slash")[0],))
    drivers = [_Driver() for _ in range(4)]
    # 停在第 3 轮的轮边界 ⇒ 第 1、2 轮各问完（持有者两次都放弃）。
    run_queries(engine, drivers, lambda current: _round_starts(current) >= 3)
    assert [int(query.actor) for query in drivers[0].activations] == [0, 0], (
        "只有持有者被问，且每个轮开始时都可能被问一次"
    )
    for driver in drivers[1:]:
        assert driver.activations == [], "没有【定乱】的座位整局零询问"


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


def test_dingluan_can_mark_each_character_once_in_a_two_seat_game():
    """D1（票 08）主判据：2 人局「第 1 轮敌方 / 第 2 轮自己」。

    用户 2026-11-07 的实机报障：首轮对敌方发动之后于禁整局不再被问，而 X 还剩 2。
    限次落到目标侧之后，座位 1 已被定乱 ⇒ 第 2 轮**仍被问**，但目标池只剩于禁自己；
    第 2 次发动因此只能展示 2 张（X = 3 − 1）。
    """
    engine = _engine(
        seats=[
            SeatSpec(hero=YUJIN, hand=(_cards("slash")[0], _cards("peach")[0])),
            SeatSpec(
                hand=(_cards("dodge")[0], _cards("slash")[1], _cards("peach")[1])
            ),
        ],
        hand0=(_cards("slash")[0], _cards("peach")[0]),
    )
    owner = _PerRoundDriver(targets=[1, 0])
    other = _Driver()
    # 停在第 3 轮的轮边界 ⇒ 第 1、2 轮各问完、两次效果都已结算（第 3 轮已无目标可问）。
    run_queries(engine, [owner, other], lambda current: _round_starts(current) >= 3)

    assert [int(query.actor) for query in owner.activations] == [0, 0], (
        "第 2 轮仍被问：限次在目标侧，不在发动者侧"
    )
    assert [query.spec.total for query in owner.selections] == [(1, 3), (1, 2)], (
        "第 1 次 X=3、第 2 次 X=2（牌面原文 3 − 此前次数）"
    )
    round_two_targets = [
        str(action.choice)
        for action in owner.options[1].legal_actions
        if isinstance(action, SelectOption)
    ]
    assert round_two_targets == ["seat:0"], "座位 1 已被定乱 ⇒ 第 2 轮目标池只剩于禁自己"
    assert _marks(engine) == (0, 1), "每名角色整局至多被定乱 1 次（两个座位各一次）"
    assert _x(engine) == 1, "X = 3 − 已被定乱过的角色数"


def test_dingluan_never_offers_a_target_that_was_already_marked():
    """D1（票 08）：目标池排除已经被定乱过的角色（记账位段预置 ⇒ 候选里没有它）。"""
    from sgs.skills.hero_yujin import _mark_dingluan_used

    engine = _engine(
        seats=[
            SeatSpec(
                hero=YUJIN,
                hand=(_cards("slash")[0],),
                identity=Identity.LORD,
            ),
            SeatSpec(hand=(_cards("dodge")[0],), identity=Identity.REBEL),
            SeatSpec(hand=(_cards("peach")[0],), identity=Identity.REBEL),
            SeatSpec(hand=(_cards("slash")[1],), identity=Identity.RENEGADE),
        ],
        hand0=(_cards("slash")[0],),
    )
    _mark_dingluan_used(engine.state, 1)
    driver = _Driver(activate_dingluan=True, target=2)
    others = [_Driver() for _ in range(3)]
    run_queries(engine, [driver, *others], lambda current: bool(driver.options))

    query = driver.options[0]
    choices = [
        str(action.choice)
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert "seat:1" not in choices, "座位 1 已被定乱过 ⇒ 不再是【定乱】的目标"
    assert choices == ["seat:0", "seat:2", "seat:3"]
    assert all(
        str(getattr(action, "choice", "")) != "seat:1"
        for action in query.legal_actions
    ), "整份候选里都没有 seat:1"


def test_dingluan_x_counts_marked_characters_not_activations():
    """D1（票 08）：X = 3 − 已被定乱过的角色数（不是"发动过的座位数"）。

    三次发动分别定乱座位 1 / 2 / 3 ⇒ X 走 3 → 2 → 1 → 0；于禁自己的座位从不被记账
    （A1 的"发动者侧记账"会把 bit 0 置上，X 就只会变成 3 − 2 = 1）。

    每个目标都配 ≥3 张手牌：``spec.total`` 的上界 = min(X, 目标牌数)，目标牌数若比 X 小
    就会把 hi 拉低，X 的走势也就测不出来了（第 3 名目标原本只有 1 张牌 ⇒ hi 恒 1）。
    """
    engine = _engine(
        seats=[
            SeatSpec(
                hero=YUJIN,
                hand=(_cards("slash")[0], _cards("peach")[0]),
                identity=Identity.LORD,
            ),
            SeatSpec(
                hand=(_cards("dodge")[0], _cards("slash")[1], _cards("peach")[1]),
                identity=Identity.REBEL,
            ),
            SeatSpec(
                hand=(_cards("dodge")[1], _cards("slash")[2], _cards("peach")[2]),
                identity=Identity.REBEL,
            ),
            SeatSpec(
                hand=(_cards("dodge")[2], _cards("slash")[3], _cards("peach")[3]),
                identity=Identity.RENEGADE,
            ),
        ],
        hand0=(_cards("slash")[0],),
    )
    driver = _PerRoundDriver(targets=[1, 2, 3])
    others = [_Driver() for _ in range(3)]
    run_queries(engine, [driver, *others], lambda current: _round_starts(current) >= 4)

    assert [query.spec.total[1] for query in driver.selections] == [3, 2, 1], (
        "每定乱一名角色，下一次发动的 X 减 1"
    )
    assert _marks(engine) == (1, 2, 3), "记账记的是目标，不是发动者（座位 0 不在其中）"
    assert _x(engine) == 0, "三名角色已被定乱 ⇒ X=0"
    assert len(driver.activations) == 3, "X=0 之后不再问"


def test_dingluan_x_shrinks_with_every_activation_and_a_used_seat_is_skipped():
    """D1（票 08）：X = 3 − 已被定乱过的角色数；已经被定乱过的座位不再进目标池。

    直接用记账入口把座位 1、2 置成"已经被定乱过"（本局一次都没发动过）⇒ 持有者第一问
    的 X 必须缩到 1、目标池只剩座位 0 / 3；它发动一次（目标 = 座位 3）再记一名 ⇒ X=0，
    此后不再问。记账记的是**目标**，不是发动者（座位 0 不在其中）。
    """
    from sgs.skills.hero_yujin import _mark_dingluan_used

    engine = _engine(
        seats=[
            SeatSpec(hero=YUJIN, hand=(_cards("slash")[0],), identity=Identity.LORD),
            SeatSpec(hand=(_cards("dodge")[0],), identity=Identity.REBEL),
            SeatSpec(hand=(_cards("peach")[0],), identity=Identity.REBEL),
            SeatSpec(hand=(_cards("slash")[1],), identity=Identity.RENEGADE),
        ],
        hand0=(_cards("slash")[0],),
    )
    _mark_dingluan_used(engine.state, 1)
    _mark_dingluan_used(engine.state, 2)
    first = _Driver(activate_dingluan=True, target=3)
    others = [_Driver() for _ in range(3)]
    # 停在第 3 轮的轮边界 ⇒ 第 1、2 轮各问完。
    run_queries(engine, [first, *others], lambda current: _round_starts(current) >= 3)

    target_options = [
        str(action.choice)
        for action in first.options[0].legal_actions
        if isinstance(action, SelectOption)
    ]
    assert target_options == ["seat:0", "seat:3"], "已定乱过的座位 1 / 2 不再进目标池"
    totals = [query.spec.total for query in first.selections]
    assert totals and totals[0][1] == 1, "X = 3 − 已被定乱过的角色数"
    assert _marks(engine) == (1, 2, 3), "发动后记的是**目标**（座位 3），不是发动者"
    assert _x(engine) == 0, "第三名角色被定乱 ⇒ X=0"
    assert len(first.activations) == 1, "X=0 之后不再问"
    for driver in others:
        assert driver.activations == [], "非持有者零询问"


def test_dingluan_is_not_offered_when_x_reaches_zero():
    """D1：X=0 ⇒ 轮开始时连问都不问（持有者自己没被定乱过、目标池非空也不行）。"""
    from sgs.skills.hero_yujin import _mark_dingluan_used

    engine = _engine(seats=_four_seats(), hand0=(_cards("slash")[0],))
    # 三名角色已经被定乱过（座位位段是 X 减数的唯一真源）。
    for seat in (1, 2, 3):
        _mark_dingluan_used(engine.state, seat)
    drivers = [_Driver() for _ in range(4)]
    run_queries(engine, drivers, lambda current: _round_starts(current) >= 2)
    assert all(driver.activations == [] for driver in drivers), "X=0 不再产出询问"


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


def test_jieyue_marking_does_not_consume_the_dingluan_limit():
    """【节钺】的标记**不**占用「被定乱过」的记账（两条技能各记各的，票 08）。

    2 人局：座位 0 = 锋·于禁在出牌阶段发动【节钺】，选项 1 把座位 1 的牌标成【定乱】牌
    并交还；走到**下一轮开始**时，【定乱】的目标选项里**仍有 `seat:1`**。判据只走公开
    询问面（目标选项），不读私有位段——【节钺】若顺手写了【定乱】的那段位，座位 1 会从
    目标池里消失。
    """
    own = (_cards("peach")[0], _cards("dodge")[0])
    hand1 = (_cards("slash")[0],)
    engine = _jieyue_engine(own, hand1)
    # 第 1 个轮边界先放弃（本题只看【节钺】之后的那个轮边界）。
    driver = _Driver(jieyue=True, choice="jieyue_mark", pick=2)
    players = [driver, _Driver(pick=1)]
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
    assert all(
        any(tag.tag_type is DINGLUAN_TAG for tag in engine.state.tags_of(card))
        for card in target_hand
    ), "前置：选项 1 交还的这两张牌都被标成【定乱】牌"

    # 之后才在轮边界发动【定乱】：目标池里应仍有座位 1（记账没被【节钺】占掉）。
    driver.activate_dingluan = True
    run_queries(
        engine,
        players,
        lambda current: any(
            str(query.skill_id or "").startswith("dingluan:seat:")
            for query in driver.options
        ),
    )
    query = next(
        query
        for query in driver.options
        if str(query.skill_id or "").startswith("dingluan:seat:")
    )
    choices = [
        str(action.choice)
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert "seat:1" in choices, (
        "【节钺】的标记不占用「被定乱过」的记账：下一轮开始时座位 1 仍在目标池里"
        f"（实际：{choices}）"
    )


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
