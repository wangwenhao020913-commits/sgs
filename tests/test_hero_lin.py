"""神话再临·林包（移动版）：神曹操移动版 —— 【归心】/【飞影】（lin-batch 票 01）。

牌面（用户 `技能.txt` 第 1 版文本，逐字；口径 = **移动版**，取牌动词是「获得」）：

    归心
    　当你受到1点伤害后，你可以获得每名其他角色区域里的一张牌，然后你翻面。

    飞影
    　锁定技，其他角色计算与你的距离+1。

票据（`.scratch/lin-batch/issues/01-shen-caocao.md`）与用户裁定
（`01b` 的 `## Answer`）已定死六件事：

- **版本 / 数据面** = 移动版（`heroes.lin_mobile.json`、`pack_id = "lin_mobile"`、
  `version = "lin-mobile"`、`shen_caocao_mobile`、显示名「神曹操移动版」、真名「神曹操」）；
- **取牌面 = 选择获得**（不是 OL 现行的「随机获得」）⇒ 逐座位未知区域弹窗自选 1 张；
- **「区域里」= 三区**（手牌 + 装备 + 判定），`reveal_hand=False`；
- **Q1 = B**：全场无人有牌也照发问（可只为翻面发动）；
- **Q2 = A**：发动后**不可**逐座少拿（`optional=False`、候选里没有 `Pass`）；
- **Q3 = A**：逐座位顺序 = **从持有者下家起的座位序**。

判据全走**外部缝**：记录流里的时机 / 询问 / delta、选牌面的 `spec.zones` 与槽位、
以及终局状态（牌在哪、谁翻面了）。**不 mock、不旁路候选**。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.effects import distance
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.state import (
    ActivateSkill,
    FrameKind,
    Pass,
    SelectCards,
    SkillSuppression,
    Zone,
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
)

PACK = load_content_pack(deck_profile="standard_108")

HERO = "shen_caocao_mobile"
GUIXIN = "hero.shen_caocao_mobile.guixin"
FEIYING = "hero.shen_caocao_mobile.feiying"

DAMAGE_AFTER_TAKE = Timing.DAMAGE_AFTER_TAKE


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _damage_frame(state, card, *, amount: int, source: int | None, target: int):
    """与引擎同形的 ``DAMAGE`` 子帧载荷（与 `test_hero_damage_after.py` 的同名工厂同形）。"""
    channel = state.card(card) if card is not None else None
    return {
        "source": source,
        "targets": (target,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": target,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _advance(engine, players, stop) -> Engine:
    """逐步推进到 ``stop(engine)`` 成立（同 ``tests/test_hero_damage_after.py`` 的手写推进）。"""
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _minimal(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


class Scripted:
    """座位脚本：``activations`` 逐轮给"发不发动"（长度不足时沿用最后一个）。

    ``pick`` 是 ``(query, round) -> Action`` 的回调，用于"这一问挑哪张牌"；未给时落到
    :func:`_minimal`。**其它技能**（【枭姬】/【连营】）的「是否发动」一律答发动——它们是
    本票借来造"搬牌挂起"的中继，不是被测对象。
    """

    def __init__(self, *, activations=(True,), pick=None) -> None:
        self.activations = tuple(activations) or (True,)
        self._pick = pick
        self.activations_seen: list = []
        self.selections: list = []
        self.options: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            index = len(self.activations_seen)
            self.activations_seen.append(query)
            wanted = self.activations[min(index, len(self.activations) - 1)]
            if wanted:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill):
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]
        if query.kind == "SELECT_CARDS" and self._pick is not None:
            self.selections.append(query)
            return self._pick(query, len(self.selections) - 1)
        if query.kind == "SELECT_CARDS":
            self.selections.append(query)
            return _minimal(query)
        if query.kind == "SELECT_OPTION":
            self.options.append(query)
        return _minimal(query)


def _players(engine, *, activations=(True,), pick=None):
    owner = Scripted(activations=activations, pick=pick)
    others = [Scripted(activations=activations) for _ in range(len(engine.state.players) - 1)]
    return [owner, *others]


def _state(seats: list[SeatSpec], *, processing=(), turn_seat: int | None = 0):
    used = {card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)}
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *used, *processing),
        processing=processing,
        turn_seat=turn_seat,
    )


def _run_damage(
    seats: list[SeatSpec],
    *,
    amount: int = 1,
    source: int | None = 1,
    target: int = 0,
    card: int | None = None,
    turn_seat: int | None = 0,
    pick=None,
    activations=(True,),
    seed: int = 7,
):
    """压一个 ``DAMAGE`` 子帧并推进到 ``DAMAGE_END``；返回 ``(engine, players)``。

    座位 0 = **受伤的神曹操**（技能持有者），座位 1 = 伤害来源，其余座位按 ``seats`` 给。
    """
    if card is None:
        card = _cards("slash")[0]
    state = _state(seats, processing=(card,), turn_seat=turn_seat)
    engine = Engine.from_state(state, seed=seed)
    players = _players(engine, activations=activations, pick=pick)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, card, amount=amount, source=source, target=target),
        timing=Timing.DAMAGE_DEAL,
    )
    _advance(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END))
    return engine, players


def _guixin_seats(seats: int) -> list[SeatSpec]:
    """座位 0 = 神曹操移动版（3 体力），其余无武将（体力 4）。"""
    return [SeatSpec(hero=HERO, hp=3, hp_max=3)] + [SeatSpec() for _ in range(seats - 1)]


# ---- 记录流的只读判据（外部缝） -------------------------------------------


def _activation_queries(engine, skill_id: str = GUIXIN) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and str(record.query.skill_id) == skill_id
    ]


def _flips(engine) -> list[tuple[int, bool]]:
    return [
        (record.seat, record.flipped)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]


def _seat_of(query) -> int:
    """这一问的选择面属于哪个座位（隐藏手牌槽与可见槽都带 ``ref.seat``）。"""
    return query.slots[0].ref.seat


def _pick_slot(query, predicate) -> SelectCards:
    index = next(i for i, slot in enumerate(query.slots) if predicate(slot))
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (index,)
    )


def _pick_first(query, _round):
    return _minimal(query)


# ---------------------------------------------------------------------------
# §1 归心 —— ① 入口（Q1 = B：全场无牌也照发问）
# ---------------------------------------------------------------------------


def test_guixin_is_offered_when_nobody_else_has_a_card():
    """**Q1 = B**（用户裁定）：全场无牌也照发「是否发动」，发动后取 0 张、只翻面。

    票据的原始断言名是 `test_guixin_is_not_offered_without_any_card_elsewhere`（读法 A 的
    方向）；用户 2026-11-08 裁 B ⇒ 方向随之反转，本条就是那一格的新方向。
    """
    engine, players = _run_damage(_guixin_seats(2))

    assert len(_activation_queries(engine)) == 1, "全场无牌也照发问（Q1 = B）"
    assert players[0].selections == [], "没有可拿的牌 ⇒ 一张选牌询问都不发"
    assert engine.state.player(0).hand == [], "取 0 张"
    assert engine.state.player(0).flipped is True, "只翻面"


def test_guixin_can_be_activated_just_to_flip_back_while_back_facing():
    """Q1 = B 的实际收益：背面朝上时，归心可以把神曹操**翻回正面**（L783 toggle）。"""
    seats = _guixin_seats(2)
    state = _state(seats, processing=())
    state.player(0).flipped = True
    engine = Engine.from_state(state, seed=7)
    players = _players(engine)
    card = _cards("slash")[0]
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, card, amount=1, source=1, target=0),
        timing=Timing.DAMAGE_DEAL,
    )
    _advance(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END))

    assert _flips(engine) == [(0, False)], "翻回正面"
    assert engine.state.player(0).flipped is False


# ---------------------------------------------------------------------------
# §1 归心 —— ② 目标池（S1：不含自己；R3：无牌跳过；座位序）
# ---------------------------------------------------------------------------


def test_guixin_targets_every_other_character_with_cards():
    """S1：「每名其他角色」⇒ 除神曹操外**每一名**有牌的存活角色各一轮，**不含自己**。"""
    seats = _guixin_seats(4)
    seats[0] = SeatSpec(hero=HERO, hp=3, hp_max=3, hand=(_cards("peach")[0],))
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    seats[2] = SeatSpec(hand=(_cards("dodge")[0],))
    # 座位 3 三区皆空 ⇒ 不在循环里（R3）。
    engine, players = _run_damage(seats, pick=_pick_first)

    assert [_seat_of(query) for query in players[0].selections] == [1, 2], (
        "有牌的两名其他角色各问一次，且不含自己（座位 0）"
    )
    assert len(engine.state.player(0).hand) == 3, "自己的 1 张 + 拿到的 2 张"


def test_guixin_takes_exactly_one_card_per_seat():
    """G3 / G5：每名角色**恰好 1 张**（不是「任意张」）——三区里四张牌，只少一张。"""
    seats = _guixin_seats(3)
    seats[1] = SeatSpec(
        hand=(_cards("slash")[1], _cards("dodge")[0]),
        equip=(_cards("eight_diagrams")[0],),
        judge=(_cards("indulgence")[0],),
    )
    seats[2] = SeatSpec(hand=(_cards("peach")[0], _cards("peach")[1]))
    engine, players = _run_damage(seats, pick=_pick_first)

    for query in players[0].selections:
        assert query.spec.total == (1, 1), "总张数 = (1, 1)，没有 0 张 / 多张档"
        assert all(
            len(action.picks) == 1
            for action in query.legal_actions
            if isinstance(action, SelectCards)
        ), "每条候选都恰好选 1 张"
    seat1 = engine.state.player(1)
    assert len(seat1.hand) + len(seat1.equip) + len(seat1.judge) == 3, "四张里只少一张"
    assert len(engine.state.player(2).hand) == 1, "两手里只少一张"
    assert len(engine.state.player(0).hand) == 2, "两名座位各拿 1 张"


def test_guixin_skips_a_seat_with_no_cards():
    """G1 / R3：三区皆空的座位**不发询问**（零副作用地跳过）。"""
    seats = _guixin_seats(4)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    engine, players = _run_damage(seats, pick=_pick_first)

    assert [_seat_of(query) for query in players[0].selections] == [1], "空座位不发问"
    assert engine.state.player(0).flipped is True, "跳过座位不影响结算"


def test_guixin_picks_in_seat_order():
    """Q3 = A：逐座位顺序 = **从持有者下家起的座位序**（座位 1 → 2 → 3）。

    这一条同时钉住隐藏槽洗位（``select`` 子流）的**消耗次序**：每次 ``selection_face``
    按座位序调用一次，故询问次序与洗位次序同源。
    """
    seats = _guixin_seats(4)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    seats[2] = SeatSpec(hand=(_cards("dodge")[0],))
    seats[3] = SeatSpec(hand=(_cards("peach")[1],))
    engine, players = _run_damage(seats, pick=_pick_first)

    assert [_seat_of(query) for query in players[0].selections] == [1, 2, 3]


def test_guixin_seat_picks_do_not_share_an_answer():
    """帧内询问键带**点号 + 座位号**：两个座位各问一次、各拿各的那张。"""
    slash = _cards("slash")[1]
    dodge = _cards("dodge")[0]
    seats = _guixin_seats(3)
    seats[1] = SeatSpec(equip=(slash,))
    seats[2] = SeatSpec(equip=(dodge,))
    engine, players = _run_damage(seats, pick=_pick_first)

    assert len(players[0].selections) == 2, "第 2 个座位不得命中第 1 个座位的答案"
    assert slash in engine.state.player(0).hand
    assert dodge in engine.state.player(0).hand
    assert engine.state.player(1).equip == [] and engine.state.player(2).equip == []


# ---------------------------------------------------------------------------
# §1 归心 —— ③ 选取面（R4：三区、手牌隐藏槽、可选一）
# ---------------------------------------------------------------------------


def test_guixin_face_covers_three_zones():
    """R4：「区域里」= **手牌 + 装备区 + 判定区**（三区一起铺成一张选取面）。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(
        hand=(_cards("slash")[1],),
        equip=(_cards("eight_diagrams")[0],),
        judge=(_cards("indulgence")[0],),
    )
    engine, players = _run_damage(seats, pick=_pick_first)

    assert len(players[0].selections) == 1
    query = players[0].selections[0]
    assert [zone.value for zone in query.spec.zones] == ["hand", "equip", "judge"]
    assert query.spec.total == (1, 1), "恰好一张（G3：不是「任意张」）"


def test_guixin_face_hides_the_hands():
    """`reveal_hand=False`：别人的手牌是**隐藏槽**（位置在、牌面不在），装备 / 判定可见。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(
        hand=(_cards("slash")[1],),
        equip=(_cards("eight_diagrams")[0],),
        judge=(_cards("indulgence")[0],),
    )
    _engine, players = _run_damage(seats, pick=_pick_first)

    query = players[0].selections[0]
    hidden = [slot for slot in query.slots if slot.ref.zone is Zone.HAND]
    visible = [slot for slot in query.slots if slot.ref.zone is not Zone.HAND]
    assert hidden and all(slot.card is None for slot in hidden), "手牌是隐藏槽"
    assert visible and all(slot.card is not None for slot in visible), "装备 / 判定区是可见槽"


def test_guixin_cannot_skip_a_seat_with_cards():
    """Q2 = A：发动之后**不可**逐座放弃 ⇒ 选牌步 `optional=False`、候选里没有 ``Pass``。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    _engine, players = _run_damage(seats, pick=_pick_first)

    query = players[0].selections[0]
    assert query.spec.optional is False
    assert not any(isinstance(action, Pass) for action in query.legal_actions)


def test_guixin_is_a_legal_settlement_when_only_one_card_is_taken():
    """G2：只拿到 1 张也算**合法结算**（翻面是代价性的，玩家可以浪费）。"""
    seats = _guixin_seats(3)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    engine, _players = _run_damage(seats, pick=_pick_first)

    assert len(engine.state.player(0).hand) == 1
    assert engine.state.player(0).flipped is True
    assert _flips(engine) == [(0, True)]


# ---------------------------------------------------------------------------
# §1 归心 —— ④ 逐点（G4 / G8）与激活答案
# ---------------------------------------------------------------------------


def test_guixin_fires_once_per_damage_point():
    """G4：「受到**1 点**伤害后」⇒ 逐点。2 点伤害 = **两次完整发动**（各取一圈 + 各翻一次）。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1], _cards("dodge")[0]))
    engine, players = _run_damage(seats, amount=2, pick=_pick_first)

    assert len(players[0].selections) == 2, "每点各拿一圈"
    assert len(engine.state.player(0).hand) == 2
    assert engine.state.player(1).hand == []
    assert _flips(engine) == [(0, True), (0, False)], "两次 toggle ⇒ 回到正面"


def test_guixin_asks_the_activation_once_per_damage_point():
    """G8 / L899：逐点 ⇒ **每点各问一次**「是否发动」，第 2 点不得复用第 1 点的答案。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1], _cards("dodge")[0]))
    engine, players = _run_damage(
        seats, amount=2, activations=(True, False), pick=_pick_first
    )

    assert len(_activation_queries(engine)) == 2, "两点各问一次"
    assert len(players[0].selections) == 1, "第 2 点答了「不发动」⇒ 只有第 1 点取牌"
    assert _flips(engine) == [(0, True)], "第 2 点没发动 ⇒ 只翻一次"


# ---------------------------------------------------------------------------
# §1 归心 —— ⑤ 翻面（toggle / 次序 / 放弃出口）
# ---------------------------------------------------------------------------


def test_guixin_flips_after_the_gains():
    """效果末段：**先取牌、后翻面**（记录流里的次序断言）。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    engine, _players = _run_damage(seats, pick=_pick_first)

    indices = [
        index
        for index, record in enumerate(engine.records)
        if record.kind == "delta" and record.delta_type in ("card_move", "flipped_change")
    ]
    assert len(indices) >= 2
    move_index = next(
        index
        for index, record in enumerate(engine.records)
        if record.kind == "delta" and record.delta_type == "card_move"
    )
    flip_index = next(
        index
        for index, record in enumerate(engine.records)
        if record.kind == "delta" and record.delta_type == "flipped_change"
    )
    assert move_index < flip_index, "取牌在前、翻面在后"


def test_guixin_flips_while_already_back_facing():
    """L783：翻面是 **toggle** —— 已背面朝上 ⇒ 这一手把他翻回**正面**（不是再翻一次背）。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    state = _state(seats, processing=())
    state.player(0).flipped = True
    card = _cards("slash")[0]
    engine = Engine.from_state(state, seed=7)
    players = _players(engine, pick=_pick_first)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, card, amount=1, source=1, target=0),
        timing=Timing.DAMAGE_DEAL,
    )
    _advance(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END))

    assert engine.state.player(0).flipped is False
    assert _flips(engine) == [(0, False)]
    assert len(engine.state.player(0).hand) == 1, "照样取牌"


def test_guixin_declining_leaves_cards_and_flip_untouched():
    """L816 的放弃出口：答「不发动」⇒ 一张牌都不动、也不翻面。"""
    seats = _guixin_seats(2)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    engine, players = _run_damage(seats, activations=(False,), pick=_pick_first)

    assert len(_activation_queries(engine)) == 1
    assert players[0].selections == []
    assert engine.state.player(0).hand == []
    assert engine.state.player(0).flipped is False
    assert _flips(engine) == []


# ---------------------------------------------------------------------------
# §1 归心 —— ⑥ 边界（G6 判定区 / G7 挂起续跑 / G9 现算座位池）
# ---------------------------------------------------------------------------


def test_guixin_takes_a_judge_zone_card_and_the_delayed_trick_stops():
    """G6：判定区的牌被**获得**后，该延时锦囊的使用流程终止（不再判定、不入弃牌堆）。

    构造：座位 2 的判定区挂着【乐不思蜀】，``turn_seat = 2``；神曹操挨 1 点伤害后把那张
    牌拿走 ⇒ 座位 2 的判定阶段轮到时，判定区已经空了（``_phase_judge`` 每轮现读）。
    """
    indulgence = _cards("indulgence")[0]
    seats = _guixin_seats(3)
    seats[1] = SeatSpec(hand=(_cards("slash")[1],))
    seats[2] = SeatSpec(judge=(indulgence,))
    engine, players = _run_damage(
        seats,
        pick=lambda query, _round: _pick_slot(
            query, lambda slot: slot.ref.zone is Zone.JUDGE
        )
        if any(slot.ref.zone is Zone.JUDGE for slot in query.slots)
        else _minimal(query),
        turn_seat=2,
    )

    assert indulgence in engine.state.player(0).hand, "判定区的牌进了神曹操的手牌"
    assert engine.state.player(2).judge == [], "离开了判定区"

    run_queries(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW))
    assert not any(
        record.kind == "timing" and record.timing is Timing.JUDGE_ON
        for record in engine.records
    ), "延时锦囊没有被判定"
    assert indulgence in engine.state.player(0).hand, "也没有被结算进弃牌堆"


def test_guixin_resumes_without_taking_twice():
    """G7：搬牌内联派发「失去牌后」（这里用界【枭姬】）⇒ 挂起 → 续跑**不重复取同一座**。

    座位 1 = 界孙尚香（只有一张装备牌 + 备用一张手牌）：拿走装备牌会让【枭姬】问一次，
    引擎因此挂起；续跑时座位 1 必须已经在"已取"游标里，不得再问一次、也不得再拿走一张。
    """
    equip = _cards("eight_diagrams")[0]
    spare = _cards("slash")[1]
    seats = _guixin_seats(3)
    seats[1] = SeatSpec(hero="jie_sunshangxiang", equip=(equip,), hand=(spare,))
    seats[2] = SeatSpec(hand=(_cards("dodge")[0],))
    engine, players = _run_damage(
        seats,
        pick=lambda query, _round: _pick_slot(
            query, lambda slot: slot.ref.zone is Zone.EQUIP
        )
        if any(slot.ref.zone is Zone.EQUIP for slot in query.slots)
        else _minimal(query),
    )

    assert [(_seat_of(query)) for query in players[0].selections] == [1, 2], (
        "座位 1 只问一次（挂起续跑不重问），随后推进到座位 2"
    )
    assert [str(query.skill_id) for query in players[1].activations_seen] == [
        "hero.jie_sunshangxiang.xiaoji"
    ], "搬牌内联派发了【枭姬】⇒ 本回复确实挂起过一次（否则这条断言是空转）"
    assert equip in engine.state.player(0).hand
    assert equip not in engine.state.player(0).hand[1:], "同一张牌没有被搬两次"
    assert spare in engine.state.player(1).hand, "座位 1 的另一张牌没被拿走"


def test_guixin_recomputes_the_seat_pool_between_picks():
    """G9：座位池**每轮现算**（重读存活 + 有牌）——死者的牌不拿、续跑后的池照旧现算。"""
    equip = _cards("eight_diagrams")[0]
    seats = _guixin_seats(4)
    seats[1] = SeatSpec(hero="jie_sunshangxiang", equip=(equip,))
    seats[2] = SeatSpec(hand=(_cards("peach")[0],), alive=False)
    seats[3] = SeatSpec(hand=(_cards("dodge")[0],))
    engine, players = _run_damage(
        seats,
        pick=lambda query, _round: _pick_slot(
            query, lambda slot: slot.ref.zone is Zone.EQUIP
        )
        if any(slot.ref.zone is Zone.EQUIP for slot in query.slots)
        else _minimal(query),
    )

    assert [(_seat_of(query)) for query in players[0].selections] == [1, 3], (
        "座位 2 已死（区域里还有牌）⇒ 现算的池把它排除"
    )
    assert [str(query.skill_id) for query in players[1].activations_seen] == [
        "hero.jie_sunshangxiang.xiaoji"
    ], "第 1 座位的搬牌挂起过一次 ⇒ 座位 3 是在**续跑现算**的池里才被问到的"
    assert engine.state.player(3).hand == [], "座位 3 的牌被拿走"
    assert engine.state.player(2).hand, "死者的牌一张都没动"


# ---------------------------------------------------------------------------
# §2 飞影 —— 客体侧 +1（M1 / M4 / 下限 / 连续型 / 零询问）
# ---------------------------------------------------------------------------


def _ring(hero: str | None, *, seats: int = 5, equip=()):
    specs = [SeatSpec(hero=hero, equip=tuple(equip))] + [
        SeatSpec() for _ in range(seats - 1)
    ]
    return build_state(pack=PACK, seats=specs)


def test_feiying_adds_one_to_others_distance_to_me():
    """M1：客体系数 +1 —— 座位距离 2 的两家，别人算神曹操是 3。"""
    without = _ring(None)
    with_hero = _ring(HERO)

    assert distance(without, 2, 0) == 2
    assert distance(with_hero, 2, 0) == 3


def test_feiying_does_not_change_my_distance_to_others():
    """M1 反向（抓"抄错方向"）：神曹操算别人**不变**（马术是主体侧，飞影是客体侧）。"""
    state = _ring(HERO)

    assert distance(state, 0, 2) == 2
    assert distance(state, 2, 0) == 3


def test_feiying_stacks_with_horses_and_other_distance_modifiers():
    """M4：加式叠加、顺序无关（＋1 防御马与飞影同轴）。"""
    jueying = _cards("jueying")[0]
    chitu = _cards("chitu")[0]
    plain = _ring(HERO)
    mounted = _ring(HERO, equip=(jueying, chitu))

    assert distance(plain, 2, 0) == 3
    assert distance(mounted, 2, 0) == 4, "飞影 +1 与 +1 防御马叠加"
    assert distance(mounted, 0, 2) == 1, "−1 进攻马只作用于主体侧（2 − 1）"


def test_feiying_distance_floor_stays_at_one():
    """规则 §4.1 L271：算出来的距离**最小为 1**（管道在链后夹住，回调不自己夹）。"""
    chitu = _cards("chitu")[0]
    state = _ring(HERO, equip=(chitu,))

    # 座位 0（神曹操，装备 −1 进攻马）算相邻座位 1：1 − 1 = 0 ⇒ 夹回 1。
    assert distance(state, 0, 1) == 1
    # 反方向：1（座位距离）+ 1（飞影）= 2，永远不低于 1。
    assert distance(state, 1, 0) == 2


def test_feiying_stops_after_the_owner_dies():
    """G9 / 规则 §4.1 L272：死者不再参与距离计算 ⇒ 修饰器随之停摆。"""
    state = _ring(HERO, seats=5)
    assert distance(state, 2, 0) == 3, "别人算神曹操 = 座位距离 2 + 飞影 1"

    state.player(0).alive = False
    assert distance(state, 2, 0) == 2, "神曹操阵亡 ⇒ 不再 +1（修饰器随实例消失）"


def test_feiying_never_asks_anything():
    """零询问：锁定技 + 纯修饰器 ⇒ 一发询问都不产生（也不是主动技入口）。"""
    definition = CATALOG[FEIYING]
    assert definition.locked is True
    assert definition.hooks == ()
    assert [modifier.point.value for modifier in definition.modifiers] == ["DISTANCE"]

    state = _ring(HERO, seats=2)
    engine = Engine.from_state(state, seed=7)
    players = [PassPlayer() for _ in range(2)]
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == "PLAY_PHASE",
    )
    assert _activation_queries(engine, FEIYING) == []
    assert not any(
        record.kind == "query" and str(record.query.skill_id) == FEIYING
        for record in engine.records
    )


def test_feiying_disappears_when_the_skill_is_suppressed():
    """`locked` 与"失效"是两条轴：`ALL_NON_LOCKED` 压不住锁定技，但实例消失就一并停摆。"""
    state = _ring(HERO, seats=2)
    assert distance(state, 1, 0) == 2

    state.player(0).suppressed_skills.append(
        SkillSuppression(skill_id="hero.jie_guanyu.yijue")
    )
    assert distance(state, 1, 0) == 2, "锁定技不受「所有非锁定技失效」影响"

    state.player(0).lost_skills.append(FEIYING)
    assert distance(state, 1, 0) == 1, "失去技能 ⇒ 修饰器停摆"


# ---------------------------------------------------------------------------
# 形状 / 数据面（一条即可：形态表与数据面另有 test_hero_catalog.py 的机械核对）
# ---------------------------------------------------------------------------


def test_guixin_is_a_handwritten_per_point_hook_without_a_flow():
    """票面 §1「形状」：手写钩子（逐点、无流水线、无 `preactivated`）⇒「多轮技能」格。"""
    from sgs.skills import flow_for

    definition = CATALOG[GUIXIN]
    assert definition.locked is False
    assert definition.preactivated is False, "首步读别人的手牌 ⇒ 不翻预发动"
    assert flow_for(GUIXIN) is None, "手写循环，不走步骤语言"
    hooks = [hook for hook in definition.hooks if hook.timing is DAMAGE_AFTER_TAKE]
    assert len(hooks) == 1
    assert hooks[0].per_point is True
    assert hooks[0].handler is not None
    assert definition.requirement == (
        "当你受到1点伤害后，你可以获得每名其他角色区域里的一张牌，然后你翻面。"
    )
    assert definition.step_requirements == {"card": "获得一名其他角色区域里的一张牌"}


def test_feiying_requirement_is_the_card_text():
    """一句话文案 = 牌面原文（武将详情的技能行读它）。"""
    assert CATALOG[FEIYING].requirement == "锁定技，其他角色计算与你的距离+1。"


def test_guixin_and_feiying_are_the_two_skills_of_the_mobile_hero():
    """数据面：两条技能的全限定 id 与数据表同源（`hero.<武将 id>.<短 id>`）。"""
    from sgs.data import hero_by_id

    hero = hero_by_id(HERO)
    assert hero is not None
    assert hero.name == "神曹操移动版"
    assert hero.true_name == "神曹操"
    assert hero.version == "lin-mobile"
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("shen", "male", 3)
    assert [f"hero.{hero.id}.{skill.id}" for skill in hero.skills] == [GUIXIN, FEIYING]
