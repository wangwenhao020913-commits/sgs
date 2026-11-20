"""自制包·锋·关羽：【单骑】/【义云】/【心汉】（feng-guanyu 票 01；用户 2026-11-20 逐条签）。

判据一律走**外部缝**：询问种类与载荷（``SKILL_ACTIVATION`` / ``SELECT_OPTION`` /
``SELECT_CARDS`` / ``DODGE`` / ``RESPOND``）、候选集合、``card_reveal`` / ``card_move`` /
``skill_suppress`` 记录、引擎公开读口（``sgs.skills.yiyun_recorded_suits``）与终局状态
（手牌 / 弃牌堆 / 势力 / 失效记录）；不读回调内部结构。

三张框架票（同批落地）的行为面也在这里顺带钉住：

- 票 03 的**全集转化**（来源张数无上界、按局 id、零组合枚举）——【单骑】把整手同色牌压成一张；
- 票 04 的**响应牌面收窄**——单骑第三段"只能被义云记录的花色的牌响应"；
- 票 05 的**失效提前到期**——义云丙"直到有黑色牌进入弃牌堆"。
"""

from __future__ import annotations

from sgs.data import Color, Identity, Kingdom, RuleConfig, Suit, load_content_pack
from sgs.engine import Engine
from sgs.skills import (
    awakening_skill_awakened,
    reset_turn_skill_state,
    yiyun_recorded_suits,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    PlayCard,
    SelectCards,
    SelectOption,
    SelectOptions,
    SkillSuppressionScope,
    UseCard,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_until

PACK = load_content_pack(deck_profile="standard_108")

DANQI = "hero.feng_guanyu.danqi"
YIYUN = "hero.feng_guanyu.yiyun"
XINHAN = "hero.feng_guanyu.xinhan"


# ---------------------------------------------------------------------------
# 局面与驱动
# ---------------------------------------------------------------------------


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _by_color(color: Color, count: int) -> tuple[int, ...]:
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    return tuple(
        card.card_id for card in probe.cards if card.color is color
    )[:count]


_ROSTER = (Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE)


def _engine(*, seats: list[SeatSpec], turn_seat: int = 0, phase=None) -> Engine:
    # 身份局（4 人）必须先发一个主公座位（票 13 §4），而**先手 = 主公**：把主公给
    # ``turn_seat``，本文件就能从"这一座位的这个阶段"起跑（不必先绕一圈）。
    rest = list(_ROSTER)
    seats = [
        spec
        if spec.identity is not None
        else SeatSpec(
            hp=spec.hp,
            hp_max=spec.hp_max,
            hand=spec.hand,
            equip=spec.equip,
            judge=spec.judge,
            alive=spec.alive,
            identity=Identity.LORD if index == turn_seat else rest.pop(0),
            gender=spec.gender,
            hero=spec.hero,
            kingdom=spec.kingdom,
            virtual_equipment=spec.virtual_equipment,
            chained=spec.chained,
        )
        for index, spec in enumerate(seats)
    ]
    used = {
        int(card_id)
        for spec in seats
        for zone in (spec.hand, spec.equip, spec.judge)
        for card_id in zone
    }
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=len(seats)),
    )
    return Engine.from_state(state, seed=11)


class _Driver:
    """按脚本应答：技能发动 / 选项 / 选座 / 选牌 / 响应各一档。"""

    def __init__(
        self,
        *,
        skill: str | None = None,
        activate: bool = True,
        option: str | None = None,
        seat: int | None = None,
        optional: bool = False,
        respond: str = "pass",
    ) -> None:
        self.skill = skill
        self.activate = activate
        self.option = option
        self.seat = seat
        self.optional = optional
        self.response_mode = respond
        self.activations: list = []
        self.options: list = []
        self.selections: list = []
        self.responses: list = []

    def respond(self, query):
        kind = str(query.kind)
        if kind == "SKILL_ACTIVATION" and (
            self.skill is None or query.skill_id == self.skill
        ):
            self.activations.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill):
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if kind == "SELECT_OPTIONS":  # ask_seats（座位多选；候选是骨架，答案自己组装）
            self.options.append(query)
            if self.optional:
                for action in query.legal_actions:
                    if isinstance(action, Pass):
                        return action
            disabled = set(getattr(query, "disabled", ()) or ())
            pool = [opt for opt in (getattr(query, "options", ()) or ()) if opt not in disabled]
            wanted = None if self.seat is None else f"seat:{self.seat}"
            if wanted in pool:
                return SelectOptions(choices=(wanted,), answers_seq=query.seq)
            count = max(1, int(getattr(query, "min_selected", 1) or 1))
            return SelectOptions(choices=tuple(pool[:count]), answers_seq=query.seq)
        if kind == "SELECT_OPTION":
            self.options.append(query)
            return self._option(query)
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            picks = [a for a in query.legal_actions if isinstance(a, SelectCards)]
            return picks[0] if picks else query.legal_actions[0]
        if kind in ("DODGE", "RESPOND", "PEACH"):
            self.responses.append(query)
            if self.response_mode == "pass":
                for action in query.legal_actions:
                    if isinstance(action, Pass):
                        return action
            if self.response_mode == "card" and isinstance(query.legal_actions[0], (UseCard, PlayCard)):
                return query.legal_actions[0]
        if kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        return query.legal_actions[-1]

    def _option(self, query):
        choices = [a for a in query.legal_actions if isinstance(a, SelectOption)]
        if self.optional:
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if not choices:
            return query.legal_actions[0]
        if self.option is not None:
            wanted = [a for a in choices if a.choice == self.option]
            if wanted:
                return wanted[0]
        if self.seat is not None:
            wanted = [a for a in choices if a.choice == f"seat:{self.seat}"]
            if wanted:
                return wanted[0]
        return choices[0]


def _step_until(engine, cond, limit: int = 200) -> None:
    """推进到 ``cond`` 成立（``_step`` 的**第一件事**就是到期收口，待答询问不会挡住它）。"""
    for _ in range(limit):
        if cond():
            return
        if engine.finished:
            return
        engine._step()


def _danqi_use(engine):
    """单骑开出的那张转化牌：**USE 帧**上的 ``card``（虚拟产物不搬牌，只能从帧上读）。"""
    for frame in engine.stack.frames:
        if frame.kind is FrameKind.USE:
            card = frame.data.get("card")
            if card is not None and getattr(card, "is_virtual", False):
                if card.type in ("duel", "fire_slash"):
                    return card
    return None


# ---------------------------------------------------------------------------
# 【单骑】：别人的准备阶段 → 整手同色手牌当一张【决斗】/ 火【杀】
# ---------------------------------------------------------------------------


def test_danqi_is_offered_in_another_players_prepare_phase():
    """只有**别人**的准备阶段会问（自己的准备阶段不问；其它角色之间互不触发）。"""
    black = _by_color(Color.BLACK, 1)
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu", hand=black),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    driver = _Driver(skill=DANQI, activate=True)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: bool(driver.activations),
    )
    assert driver.activations, "座位 1 的准备阶段应问座位 0 的单骑"
    assert all(query.actor == 0 for query in driver.activations)


def test_danqi_uses_all_black_hand_cards_as_a_duel():
    """选黑色 ⇒ 来源 = **全部**黑色手牌，产物 = 一张【决斗】，目标 = 当前回合角色。"""
    black = _by_color(Color.BLACK, 3)
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu", hand=black),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    driver = _Driver(skill=DANQI, option="danqi:black")
    target_driver = _Driver(respond="pass")
    run_until(
        engine,
        [driver, target_driver, _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: _danqi_use(e) is not None,
    )
    card = _danqi_use(engine)
    assert card is not None and card.type == "duel"
    assert set(card.virtual_sources) == set(black), "该色全部手牌都是来源"
    assert card.suit is None and card.rank is None, "多源转化的花色 / 点数恒无"
    assert card.color is Color.BLACK


def test_danqi_uses_all_red_hand_cards_as_a_fire_slash():
    """选红色 ⇒ 产物是火【杀】（无距离限制由技能侧豁免距离判据）。"""
    red = _by_color(Color.RED, 2)
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu", hand=red),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    driver = _Driver(skill=DANQI, option="danqi:red")
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: _danqi_use(e) is not None,
    )
    card = _danqi_use(engine)
    assert card is not None and card.type == "fire_slash"
    assert set(card.virtual_sources) == set(red)


def test_danqi_disables_the_color_without_cards():
    """只有黑色手牌时，红色那一项**置灰**（值不值得选另说，先要看得出用不了）。"""
    black = _by_color(Color.BLACK, 1)
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu", hand=black),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    driver = _Driver(skill=DANQI, option="danqi:black")
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: bool(driver.options),
    )
    query = driver.options[0]
    assert query.skill_id.endswith("choice")
    assert [action.choice for action in query.legal_actions if isinstance(action, SelectOption)] == [
        "danqi:black"
    ], "红色项这一刻没有候选（置灰 = 不进 legal_actions）"


def test_danqi_restricts_responses_to_the_suits_recorded_by_yiyun():
    """有义云 ∧ 来源 ≥2 ⇒ 这次使用只许记录花色的牌响应；记录为空 ⇒ 无法响应。"""
    black = _by_color(Color.BLACK, 2)
    # 目标手里：黑桃【杀】（决斗要用【杀】响应）+ 红桃【杀】。记录为空 ⇒ 两张都不能响应。
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    slash_by_suit: dict[str, int] = {}
    for card in probe.cards:
        if card.type == "slash":
            slash_by_suit.setdefault(card.suit, card.card_id)
    spade_slash = slash_by_suit["spade"]
    heart_slash = slash_by_suit["heart"]
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu", hand=black),
            SeatSpec(hero="machao", hand=(spade_slash, heart_slash)),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    target = _Driver(respond="card")
    driver = _Driver(skill=DANQI, option="danqi:black")
    run_until(
        engine,
        [driver, target, _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: bool(target.responses),
    )
    assert target.responses, "决斗应开出【杀】响应窗口"
    query = target.responses[0]
    playable = [a for a in query.legal_actions if isinstance(a, (UseCard, PlayCard))]
    assert playable == [], "义云记录为空 ⇒ 没有任何牌能响应（含记录以外的花色）"


# ---------------------------------------------------------------------------
# 【义云】：展示 / 记录 / 甲~丁 / 摸清
# ---------------------------------------------------------------------------


def _damage(
    engine: Engine, *, source: int, target: int = 1, amount: int = 1, card: int | None = None
) -> None:
    """压一个与引擎同形的伤害子帧（伤害后技能的测试惯例）。"""
    channel = engine.state.card(card) if card is not None else None
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": channel,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": target,
            "channel": channel,
            "nature": "NORMAL",
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _yiyun_engine(hand: tuple[int, ...], *, heroes=("feng_guanyu", "machao")):
    return _engine(
        seats=[
            SeatSpec(hero=heroes[0], hand=hand),
            SeatSpec(hero=heroes[1]),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=0,
        phase=Phase.PLAY,
    )


def test_yiyun_reveals_and_records_suits_and_asks_the_red_branch():
    """全红手牌：展示全部手牌、记录花色集合，然后问"甲 / 乙"两项。"""
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    red_hand = tuple(
        card.card_id for card in probe.cards if card.color is Color.RED
    )[:1]
    assert red_hand
    engine = _yiyun_engine(red_hand)
    driver = _Driver(option="yiyun:recover_one", seat=0)
    _damage(engine, source=0)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        # 先问座位（SELECT_OPTIONS）、再问选项（SELECT_OPTION）⇒ 停在后者的待答处。
        lambda e: e.pending is not None
        and str(e.pending.kind) == "SELECT_OPTION",
    )

    assert yiyun_recorded_suits(engine.state, 0) == (
        engine.state.card(red_hand[0]).suit,
    ), "记录 = 本次展示的牌的花色（集合）"
    assert any(
        getattr(record, "delta_type", None) == "card_reveal"
        for record in engine.records
    ), "展示走 card_reveal（不移动牌）"
    query = engine.pending
    assert [a.choice for a in query.legal_actions if isinstance(a, SelectOption)] == [
        "yiyun:draw_two",
        "yiyun:recover_one",
    ]
    assert query.option_texts == {
        "yiyun:draw_two": "甲：摸两张牌",
        "yiyun:recover_one": "乙：回复1点体力",
    }, "按钮文案是中文（用户 2026-11-20 点名；不是选项 id 原文）"


def test_yiyun_mixed_record_draws_the_number_of_recorded_suits_and_clears():
    """记录混色 ⇒ 摸「记录的花色数」张并清空记录（先摸后清）。"""
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    black = [c.card_id for c in probe.cards if c.color is Color.BLACK][:1]
    red = [c.card_id for c in probe.cards if c.color is Color.RED][:1]
    engine = _yiyun_engine((black[0], red[0]))
    _damage(engine, source=0)
    driver = _Driver()
    hand_before = len(engine.state.player(0).hand)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        # 混色 ⇒ 摸「记录的花色数」张（2 张）后再清空；摸完就是判定点。
        lambda e: len(e.state.player(0).hand) > hand_before,
    )
    assert yiyun_recorded_suits(engine.state, 0) == (), "混色 ⇒ 记录已清空"
    assert not driver.options, "混色 ⇒ 不触发 ①②（没有选项询问）"


def test_yiyun_black_branch_suppresses_until_a_black_card_hits_discard():
    """丙：目标获得 `UNTIL_BLACK_DISCARD` 的失效记录；黑牌入弃牌堆后解除。"""
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    black_hand = tuple(c.card_id for c in probe.cards if c.color is Color.BLACK)[:1]
    engine = _yiyun_engine(black_hand)
    driver = _Driver(option="yiyun:suppress", seat=1)
    _damage(engine, source=0)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: any(
            record.skill_id == YIYUN for record in e.state.player(1).suppressed_skills
        ),
    )
    records = engine.state.player(1).suppressed_skills
    assert [(record.skill_id, record.scope) for record in records] == [
        (YIYUN, SkillSuppressionScope.UNTIL_BLACK_DISCARD)
    ], "丙 = 目标座位上的提前到期失效记录"

    # 一张黑牌进弃牌堆 ⇒ 下一条 _step 开头解除（票 05 的到期监听）。
    engine.state.note_discard_suits(
        [engine.state.card(next(c.card_id for c in engine.state.cards if c.color is Color.BLACK))]
    )
    _step_until(
        engine,
        lambda: not [
            record
            for record in engine.state.player(1).suppressed_skills
            if record.scope is SkillSuppressionScope.UNTIL_BLACK_DISCARD
        ],
    )
    assert not [
        record
        for record in engine.state.player(1).suppressed_skills
        if record.scope is SkillSuppressionScope.UNTIL_BLACK_DISCARD
    ], "黑牌入弃牌堆 ⇒ 提前解除"


def _card_of(color: Color, suit: Suit) -> int:
    """该颜色的第一张该花色的牌（用来逐次只展示一种花色）。"""
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    for card in probe.cards:
        if card.color is color and card.suit is suit.value:
            return card.card_id
    raise AssertionError(f"牌堆里没有 {color} {suit}")


def _yiyun_drivers(option: str = "yiyun:draw_two"):
    """义云 ①② 的应答：选项选 ``option``、座位选 1、第二段的"另一名角色"放弃。"""
    driver = _Driver(option=option, seat=1, optional=True)
    return [driver, _Driver(), _Driver(), _Driver()], driver


def test_yiyun_record_is_a_union_that_survives_a_non_triggering_activation():
    """**报障修复**（用户 2026-11-20）：发动本身不清记录，记录跨发动取并集。

    单次发动只展示一种花色、手里还有牌 ⇒ 末段条件（有红有黑 ∨ 没有手牌）不成立 ⇒
    既不摸牌也不清空；两次发动（♦ 然后 ♥）之后记录是**两色的并集**，仍全红 ⇒ 仍不清。
    """
    diamond = _card_of(Color.RED, Suit.DIAMOND)
    heart = _card_of(Color.RED, Suit.HEART)
    engine = _yiyun_engine((diamond,))
    drivers, _ = _yiyun_drivers()
    _damage(engine, source=0)
    _run_frame(engine, drivers)
    assert yiyun_recorded_suits(engine.state, 0) == (Suit.DIAMOND.value,), (
        "本次展示的花色记进记录"
    )
    assert len(engine.state.player(0).hand) == 1, "全红且单色 ⇒ 末段不摸牌"

    # 下一回合：换上另一色的手牌再发动一次（记录**并进**旧记录，不清旧位）。
    reset_turn_skill_state(engine.state)
    engine.state.player(0).hand = [heart]
    _damage(engine, source=0)
    _run_frame(engine, drivers)
    assert yiyun_recorded_suits(engine.state, 0) == (
        Suit.HEART.value,
        Suit.DIAMOND.value,
    ), "记录 = 两次展示花色的并集（规范序），不是「最后一次」"
    assert len(engine.state.player(0).hand) == 1, "仍全红 ⇒ 末段仍不摸牌、不清空"


def test_yiyun_empty_hand_triggers_the_draw_and_the_clear():
    """「你没有手牌」在 ①② 之后现算：空手牌 ⇒ 摸「记录的花色数」张、再清空记录。"""
    diamond = _card_of(Color.RED, Suit.DIAMOND)
    engine = _yiyun_engine((diamond,))
    drivers, _ = _yiyun_drivers()
    _damage(engine, source=0)
    _run_frame(engine, drivers)
    assert yiyun_recorded_suits(engine.state, 0) == (Suit.DIAMOND.value,)

    reset_turn_skill_state(engine.state)
    engine.state.player(0).hand = []  # 空手牌 ⇒ ①② 都不触发、末段条件成立
    _damage(engine, source=0)
    _run_frame(engine, drivers)
    assert yiyun_recorded_suits(engine.state, 0) == (), "空手牌 ⇒ 摸完清空记录"
    assert len(engine.state.player(0).hand) == 1, "摸「记录的花色数」= 1 张"


def test_yiyun_triggers_once_per_turn():
    """同一回合第二次造成伤害不再触发（锁定技 + 每回合限一次）。"""
    probe = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    black_hand = tuple(c.card_id for c in probe.cards if c.color is Color.BLACK)[:1]
    engine = _yiyun_engine(black_hand)
    driver = _Driver(option="yiyun:suppress", seat=1)
    _damage(engine, source=0)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: bool(driver.options),
    )
    reveals_before = sum(
        1
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_reveal"
    )
    _damage(engine, source=0)
    run_until(
        engine,
        [_Driver(), _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: e.pending is not None,
    )
    reveals_after = sum(
        1
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_reveal"
    )
    assert reveals_after == reveals_before, "本回合第二次伤害不再展示 / 记录"


# ---------------------------------------------------------------------------
# 【心汉】：技能侧累计 → 觉醒 → 势力改蜀 → 四选一
# ---------------------------------------------------------------------------


def _recover(engine: Engine, *, target: int, amount: int = 1) -> None:
    """压一个与本局同形的回复子帧（回复量由帧算、超过上限由帧夹住）。"""
    engine.push_frame(
        FrameKind.RECOVER,
        {
            "source": target,
            "targets": (target,),
            "card": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "amount": amount,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _run_frame(engine: Engine, drivers: list) -> None:
    """只把**当前栈上的帧**跑完（不推进回合；用于人为压的伤害 / 回复子帧）。

    与 ``run_until`` 的差别：``run_until`` 走 ``engine.run``，帧跑完还会继续回合环——
    本用例只想要"这一帧的后果"，回合环会让别的座位把局面推到终局、再压帧就被拒绝。
    """
    for _ in range(200):
        if engine.pending is not None:
            engine.submit(drivers[engine.pending.actor].respond(engine.pending))
            continue
        if not engine.stack.frames:
            return
        engine._step()


def test_xinhan_counts_damage_dealt_points_and_recovery_points():
    """两个计数由心汉自己的簿记钩子写：**造成**伤害按点数、回复按实际回复量。

    用户 2026-11-20 澄清：「受到」是笔误，数的是牌面写的**造成**伤害 —— 只有自己是伤害
    来源时才计（无来源伤害 / 别人打的都不算）；被防止的伤害不算。
    """
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu"),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=0,
        phase=Phase.PLAY,
    )
    players = [_Driver(), _Driver(), _Driver(), _Driver()][: len(engine.state.players)]

    _damage(engine, source=1, target=0, amount=2)  # 别人打的：不计
    _run_frame(engine, players)
    assert engine.state.player(0).xinhan_damage_points == 0, "别人造成的伤害不计入"

    _damage(engine, source=0, target=1, amount=3)  # 我**造成** 3 点
    _run_frame(engine, players)
    assert engine.state.player(0).xinhan_damage_points == 3, "造成伤害点数 +3"

    engine.state.player(0).hp = 2
    _recover(engine, target=0, amount=1)
    _run_frame(engine, players)
    assert engine.state.player(0).xinhan_recover_points == 1, "回复体力点数 +1"


def test_xinhan_bookkeeping_hooks_do_not_mark_the_skill_awakened():
    """**报障修复**（用户 2026-11-20，票 08）：簿记段不是一次发动。

    觉醒技上挂着两条纯计数钩子；它们一跑就落"已觉醒"位的话，同一条技能的其余钩子
    （回复计数、真正的准备阶段觉醒）会被"已觉醒 ⇒ 不再派发"整片挡掉 —— 实机表现就是
    「心汉永远不觉醒」。这里钉住"跑过计数之后仍是未觉醒"。
    """
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu"),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=0,
        phase=Phase.PLAY,
    )
    players = [_Driver(), _Driver(), _Driver(), _Driver()][: len(engine.state.players)]
    assert not awakening_skill_awakened(engine.state, 0, XINHAN), "开局未觉醒"

    _damage(engine, source=0, target=1, amount=3)
    _run_frame(engine, players)
    assert engine.state.player(0).xinhan_damage_points == 3, "簿记钩子确实跑了"
    assert not awakening_skill_awakened(engine.state, 0, XINHAN), (
        "簿记段（纯计数）不是觉醒：跑过也不落「已觉醒」位"
    )


def test_xinhan_awakens_at_six_points_and_changes_the_kingdom_to_shu():
    """累计 ≥ 6 的准备阶段觉醒：势力改蜀 + 开四选一；每项各选一名**不同**角色。"""
    engine = _engine(
        seats=[
            SeatSpec(hero="feng_guanyu"),
            SeatSpec(hero="machao"),
            SeatSpec(hero="guanyu"),
            SeatSpec(hero="caocao"),
        ],
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine.state.player(0).xinhan_damage_points = 6
    driver = _Driver(option="yiyun:draw_two", seat=1)
    run_until(
        engine,
        [driver, _Driver(), _Driver(), _Driver()][: len(engine.state.players)],
        lambda e: bool(driver.options),
    )
    assert engine.state.player(0).kingdom is Kingdom.SHU, "觉醒的第一段：势力改为蜀"
    assert engine.state.turn_seat == 0 and engine.state.phase is Phase.PREPARE, (
        "觉醒时机 = **自己的**准备阶段（用户 2026-11-20 追加口径）"
    )
    assert driver.options, "第二段：可依次对不同角色执行四项之一"
    first = driver.options[0]
    assert [
        a.choice for a in first.legal_actions if isinstance(a, SelectOption)
    ] == ["yiyun:draw_two", "yiyun:recover_one", "yiyun:suppress", "yiyun:damage_one"]
