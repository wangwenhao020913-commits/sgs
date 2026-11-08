"""神话再临·林包（移动版）：神曹操移动版【归心】/【飞影】 与 孙坚移动版【英魂】。

本批（lin-batch）三只武将同住 `heroes.lin_mobile.json`，每票一个实现者 / 一个提交：

- **票 01**：神曹操移动版 —— 【归心】/【飞影】（本文件 §1 / §2）；
- **票 02**：孙坚移动版 —— 【英魂】（本文件 §3）。

票 01 的牌面（用户 `技能.txt` 第 1 版文本，逐字；口径 = **移动版**，取牌动词是「获得」）：

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

票 02 的牌面与裁定见本文件 §3 的段首注释（用户 2026-11-08 签「询问剧本」+ Q1–Q6 全按
建议 A）。

判据全走**外部缝**：记录流里的时机 / 询问 / delta、选牌面的 `spec.zones` 与槽位、
以及终局状态（牌在哪、谁翻面了 / 谁摸了几张、弃了几张）。**不 mock、不旁路候选**。
"""

from __future__ import annotations

from sgs.data import (
    Category,
    Color,
    Identity,
    Kingdom,
    RuleConfig,
    Suit,
    card_category,
    color_of_suit,
    load_content_pack,
)
from sgs.effects import ModifierPoint, check_targets, distance, legal_actions_for
from sgs.engine import Engine
from sgs.resolution import Tag, add_tag
from sgs.engine import steps as engine_steps
from sgs.skills import CATALOG, flow_for, hero_card_zone_key
from sgs.skills.catalog import PASS_STATE_KEY
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    HeroCardZone,
    MoveReason,
    Pass,
    Phase,
    PlayCard,
    QueryKind,
    SelectCards,
    SelectOption,
    SkillSuppression,
    SubstituteJudgeCard,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    ran_timing,
    run_queries,
    run_turns,
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


# ===========================================================================
# §3 孙坚移动版【英魂】（lin-batch 票 02）
#
# 牌面（`…/孙坚/技能.txt`，逐字；口径 = **移动版**，见票面 §0.1）：
#
#     英魂
#     　准备阶段，若你已受伤，你可以选择一名其他角色并选择一项：
#       1.令其摸X张牌，然后其弃置一张牌；2.令其摸一张牌，然后其弃置X张牌。
#       （X为你已损失的体力值）
#
# 用户裁定（票面 `## 用户裁定` ③）：**发动者选 1 项，令目标执行**——两段效果都由目标
# 执行（弃哪几张也由目标自己选）；X = 1 时两项后果相同是**有意的重复**，两项都列、
# 不置灰、不合并。判据全走**外部缝**：询问（`SKILL_ACTIVATION` / `SELECT_OPTION` /
# `SELECT_CARDS`）、`spec`、记录流 delta 与终局状态。**不 mock、不旁路候选**。
# ===========================================================================

SUNJIAN = "sunjian_mobile"
YINGHUN = "hero.sunjian_mobile.yinghun"
YINGHUN_DRAW_X_DISCARD_ONE = "yinghun_draw_x_discard_one"
YINGHUN_DRAW_ONE_DISCARD_X = "yinghun_draw_one_discard_x"
QINGJIAN = "hero.jie_xiahoudun.qingjian"

YINGHUN_REQUIREMENT = (
    "准备阶段，若你已受伤，你可以选择一名其他角色并选择一项："
    "1.令其摸X张牌，然后其弃置一张牌；2.令其摸一张牌，然后其弃置X张牌。"
    "（X为你已损失的体力值）"
)


class Yinghun:
    """座位脚本：预发动 / 选座 / 选一项 / 弃牌各按构造参数应答。

    **别的技能一律「不发动」**：本票借【清俭】造"摸牌内联挂起"的中继，它不是被测对象。
    """

    def __init__(
        self,
        *,
        activate: bool = True,
        seat: int = 1,
        option: str = YINGHUN_DRAW_X_DISCARD_ONE,
        take: int | None = None,
        pick_index: int = 0,
        relay: bool = False,
        pick_action=None,
    ) -> None:
        self.activate = activate
        self.seat = seat
        self.option = option
        self.take = take
        self.pick_index = pick_index
        self.relay = relay
        """``True`` = **中继技能也发动**（本票只借【清俭】造"摸到的牌被当场交出去"的局面）。"""
        self.pick_action = pick_action
        """``(query) -> Action``：需要按**区域**挑牌时用它（缺省按 ``take`` / ``pick_index`` 挑）。"""
        self.activations: list = []
        self.options: list = []
        self.selections: list = []

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION":
            activate = next(
                (a for a in query.legal_actions if isinstance(a, ActivateSkill)), None
            )
            give_up = next((a for a in query.legal_actions if isinstance(a, Pass)), None)
            self.activations.append(query)
            if str(query.skill_id) == YINGHUN:
                if self.activate and activate is not None:
                    return activate
                if give_up is not None:
                    return give_up
                return query.legal_actions[0]
            if self.relay and activate is not None:
                return activate
            # 【清俭】等中继技能：缺省一律不发动（不是被测对象）。
            return give_up if give_up is not None else query.legal_actions[0]
        if kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
            return query.legal_actions[0]
        if kind == "SELECT_OPTION":
            self.options.append(query)
            skill = str(getattr(query, "skill_id", ""))
            wanted = None
            if skill == "yinghun:seat":
                wanted = f"seat:{self.seat}"
            elif skill == "yinghun:option":
                wanted = self.option
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and str(action.choice) == wanted:
                    return action
            return next(
                (a for a in query.legal_actions if isinstance(a, SelectOption)),
                query.legal_actions[0],
            )
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            if self.pick_action is not None:
                return self.pick_action(query)
            usable = [
                action
                for action in query.legal_actions
                if isinstance(action, SelectCards)
                and (self.take is None or len(action.picks) == self.take)
            ]
            if usable:
                return usable[self.pick_index]
            return query.legal_actions[0]
        return query.legal_actions[0]


def _yinghun_state(
    *,
    own_hp: int = 2,
    own_hp_max: int = 4,
    target_hero: str | None = None,
    target_hp: int = 4,
    target_hand: tuple[int, ...] = (),
    target_equip: tuple[int, ...] = (),
    target_judge: tuple[int, ...] = (),
    target_alive: bool = True,
    seats: int = 2,
    first_seat: int = 0,
    deck: tuple[int, ...] | None = None,
):
    """座位 0 = 孙坚移动版；座位 1 = 目标；其余无武将。``own_hp`` ⇒ X = 上限 − 当前。"""
    used = {*target_hand, *target_equip, *target_judge}
    specs = [
        SeatSpec(hero=SUNJIAN, hp=own_hp, hp_max=own_hp_max),
        SeatSpec(
            hero=target_hero,
            hp=target_hp,
            hp_max=4,
            hand=tuple(target_hand),
            equip=tuple(target_equip),
            judge=tuple(target_judge),
            alive=target_alive,
        ),
    ]
    specs.extend(SeatSpec() for _ in range(seats - 2))
    rule_config = (
        None
        if first_seat == 0
        else RuleConfig(player_count=2, first_seat=first_seat)
    )
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK, *used) if deck is None else deck,
        turn_seat=first_seat,
        phase=Phase.PREPARE,
        rule_config=rule_config,
    )


def _run_prepare(engine, **kwargs):
    """推进到**准备阶段跑完**（``PHASE_DRAW`` 已落）——英魂的全部询问都在它之前。"""
    players = [Yinghun(**kwargs) for _ in range(len(engine.state.players))]
    run_queries(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW))
    return players


def _yinghun_engine(**kwargs):
    """``(engine, players)``：座位脚本先摘出来，剩下的参数交给 :func:`_yinghun_state`。"""
    script = {
        key: kwargs.pop(key)
        for key in ("activate", "seat", "option", "take", "pick_index", "relay", "pick_action")
        if key in kwargs
    }
    state = _yinghun_state(**kwargs)
    engine = Engine.from_state(state, seed=7)
    return engine, _run_prepare(engine, **script)


def _step_query(players, step: str):
    """``yinghun:<step>`` 那一问（找不到 ⇒ 断言失败，不是静默 ``None``）。"""
    for query in players[0].options:
        if str(getattr(query, "skill_id", "")) == f"yinghun:{step}":
            return query
    raise AssertionError(
        f"没有发出 yinghun:{step} 询问："
        f"{[str(query.skill_id) for query in players[0].options]}"
    )


def _discard_query(players) -> list:
    """目标那一侧的选牌询问（英魂的弃置段；本票目标只在弃置段被问选牌）。"""
    return list(players[1].selections)


def _discard_moves(engine, seat: int) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
        and record.owner == seat
    ]


def _draw_moves(engine, seat: int) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
        and record.owner == seat
    ]


# ---------------------------------------------------------------------------
# §3 英魂 —— ① 入口（自己的准备阶段 + 已受伤 + 有其他存活角色）
# ---------------------------------------------------------------------------


def test_yinghun_is_offered_only_on_my_own_prepare_phase():
    """H2：「准备阶段」= **自己的**准备阶段；已受伤 ⇒ 恰问一次，且「可以」= 有放弃出口。"""
    engine, players = _yinghun_engine(own_hp=3)

    assert len(players[0].activations) == 1, "每个自己的准备阶段至多问一次"
    query = players[0].activations[0]
    assert query.actor == 0
    kinds = {type(action).__name__ for action in query.legal_actions}
    assert {"ActivateSkill", "Pass"} <= kinds, "「可以」⇒ 提交与放弃两个出口都在候选里"
    assert query.optional is True


def test_yinghun_is_not_offered_when_not_wounded():
    """G2 / L741：满体力（未受伤）⇒ 条件为假，**连问都不发**（零副作用）。"""
    engine, players = _yinghun_engine(own_hp=4, own_hp_max=4)

    assert players[0].activations == []
    assert players[1].selections == []
    assert not any(
        record.kind == "query" and str(getattr(record.query, "skill_id", "")) == YINGHUN
        for record in engine.records
    )


def test_yinghun_with_no_other_alive_seat_offers_nothing():
    """S4：没有其他存活角色 ⇒ 目标池空 ⇒ 不发没有候选的询问。"""
    engine, players = _yinghun_engine(target_alive=False)

    assert players[0].activations == []
    assert players[1].selections == []


def test_yinghun_target_pool_is_other_alive_seats_only():
    """S1 / S4 / O2：座位步的候选 = **其他存活**座位（不含自己、不含死者），且无 `Pass`。"""
    engine, players = _yinghun_engine(seats=3, target_alive=False)

    assert engine.state.player(1).alive is False
    query = _step_query(players, "seat")
    choices = {
        str(action.choice)
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    }
    assert choices == {"seat:2"}, "死者与持有者自己都不进池（只剩座位 2）"
    assert not any(isinstance(action, Pass) for action in query.legal_actions), (
        "O2：发动后必须选一名角色（没有 Pass）"
    )


def test_yinghun_does_not_trigger_in_another_players_prepare_phase():
    """H2 反向：**别人的**准备阶段 ⇒ 持有者的实例连「是否发动」都不问。"""
    engine, players = _yinghun_engine(first_seat=1)

    assert engine.state.turn_seat == 1
    assert players[0].activations == [], "孙坚不是当前回合角色 ⇒ 英魂不该被问到"


# ---------------------------------------------------------------------------
# §3 英魂 —— ② 选项步（两项恒列、无 `Pass`）
# ---------------------------------------------------------------------------


def test_yinghun_lists_both_options_even_when_x_is_one():
    """O1 + 用户裁定③：X = 1 时两项**后果相同**，仍都列、都不置灰、不合并。"""
    engine, players = _yinghun_engine(own_hp=3, own_hp_max=4)  # X = 1

    assert engine.state.player(0).hp_max - engine.state.player(0).hp == 1
    query = _step_query(players, "option")
    assert query.options == (YINGHUN_DRAW_X_DISCARD_ONE, YINGHUN_DRAW_ONE_DISCARD_X)
    assert query.disabled == (), "两项恒可执行 ⇒ 不产出置灰项"
    choices = {
        str(action.choice)
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    }
    assert choices == {YINGHUN_DRAW_X_DISCARD_ONE, YINGHUN_DRAW_ONE_DISCARD_X}


def test_yinghun_option_step_has_no_pass():
    """O2 / Q5 = A：「可以」的放弃出口只在预发动那一问；发动后必须选一项。"""
    _engine, players = _yinghun_engine()

    for query in (_step_query(players, "seat"), _step_query(players, "option")):
        assert not any(
            isinstance(action, Pass) for action in query.legal_actions
        ), f"{query.skill_id} 不该有 Pass"


# ---------------------------------------------------------------------------
# §3 英魂 —— ③ 效果（先摸、后弃；两段都由目标执行）
# ---------------------------------------------------------------------------


def test_yinghun_option_one_draws_x_then_target_discards_one():
    """选项 1：目标摸 **X** 张，然后弃置 **1** 张（X = 已损失体力值 = 3）。"""
    hand = (_cards("slash")[1], _cards("slash")[2])
    engine, players = _yinghun_engine(
        own_hp=1, target_hand=hand, option=YINGHUN_DRAW_X_DISCARD_ONE
    )

    assert [len(record.cards) for record in _draw_moves(engine, 1)] == [3], "一次摸 X = 3 张"
    discard = _discard_query(players)
    assert len(discard) == 1
    assert discard[0].spec.total == (1, 1), "弃置一段恰好一张"
    assert len(engine.state.player(1).hand) == len(hand) + 3 - 1


def test_yinghun_option_two_draws_one_then_target_discards_x():
    """选项 2：目标摸 **1** 张，然后弃置 **X** 张（X = 3）。"""
    hand = (
        _cards("slash")[1],
        _cards("slash")[2],
        _cards("dodge")[0],
        _cards("dodge")[1],
        _cards("peach")[0],
    )
    engine, players = _yinghun_engine(
        own_hp=1, target_hand=hand, option=YINGHUN_DRAW_ONE_DISCARD_X
    )

    assert [len(record.cards) for record in _draw_moves(engine, 1)] == [1], "一次摸一张"
    discard = _discard_query(players)
    assert discard[0].spec.total == (3, 3), "弃置 X = 3 张（不是「任意张」）"
    assert len(engine.state.player(1).hand) == len(hand) + 1 - 3


def test_yinghun_short_discard_takes_every_card():
    """Q3 = A：目标可用牌数 < X ⇒ **全部弃置**（《官方规则集》逐字），规格恒可满足。"""
    hand = (_cards("slash")[1],)
    engine, players = _yinghun_engine(
        own_hp=1, target_hand=hand, option=YINGHUN_DRAW_ONE_DISCARD_X
    )

    discard = _discard_query(players)
    assert discard[0].spec.total == (2, 2), "摸 1 张后共 2 张 < X = 3 ⇒ 弃光"
    assert engine.state.player(1).hand == [], "全部弃置，不是「整段不发生」"


def test_yinghun_no_discard_step_without_any_card():
    """边界：弃置段的可用牌数 = 0 ⇒ 该段整体不出现（不发空询问；摸牌已经发生）。

    可达路径：目标 = 界夏侯惇，摸到的**那一张**被【清俭】当场交出去 ⇒ 弃置段开始前
    它手里已经空了（X = 1、选项 1 只摸一张）。本票不另造机制，只用既有中继。
    """
    engine, players = _yinghun_engine(
        own_hp=3,
        target_hero="jie_xiahoudun",
        target_hand=(),
        option=YINGHUN_DRAW_X_DISCARD_ONE,
        relay=True,
    )

    assert QINGJIAN in [str(query.skill_id) for query in players[1].activations]
    assert engine.state.player(1).hand == [] and engine.state.player(1).equip == []
    drawn = {ref.card_id for record in _draw_moves(engine, 1) for ref in record.cards}
    assert len(drawn) == 1, "选项 1 在 X = 1 时只摸一张"
    assert drawn <= set(engine.state.player(0).hand), "那一张真的被交到了孙坚手里（清俭生效）"
    assert _discard_moves(engine, 1) == [], "没有可弃的牌 ⇒ 一条弃置 delta 都不该有"


def test_yinghun_discard_face_covers_hand_and_equipment_but_not_judge():
    """C2 / Q2 = A：「一张牌」= 手牌 + 装备区，**不含判定区**；面由目标自己铺。"""
    hand, equip = _cards("slash")[1], _cards("eight_diagrams")[0]

    def _one_from_each(query):
        """挑"一张手牌 + 一张装备牌"那一条（跨区的弃置正是引擎最容易踩空的那一格）。"""
        index = {
            slot.card.card_id: position for position, slot in enumerate(query.slots)
        }
        picks = (index[hand], index[equip])
        return next(
            action
            for action in query.legal_actions
            if isinstance(action, SelectCards) and action.picks == picks
        )

    engine, players = _yinghun_engine(
        own_hp=2,
        target_hand=(hand,),
        target_equip=(equip,),
        target_judge=(_cards("indulgence")[0],),
        option=YINGHUN_DRAW_ONE_DISCARD_X,
        pick_action=_one_from_each,
    )

    query = _discard_query(players)[0]
    assert [zone.value for zone in query.spec.zones] == ["hand", "equip"]
    assert {slot.ref.seat for slot in query.slots} == {1}, "铺的是目标自己的区"
    assert all(
        slot.card is not None for slot in query.slots if slot.ref.zone is not Zone.HAND
    ), "应答者是手牌持有者本人 ⇒ 可见槽"
    assert engine.state.player(1).judge, "判定区的那张牌一张都没动"

    # 跨区的一次弃置（一手牌 + 一装备牌）：两个来源区各一条 delta（``move_card`` 一次**只
    # 接受一个来源区**），两张牌都真的进了弃牌堆、判定区那张没动。
    discarded = {
        ref.card_id for record in _discard_moves(engine, 1) for ref in record.cards
    }
    assert discarded == {hand, equip}
    assert engine.state.player(1).equip == [], "装备区那张被弃掉了"
    assert len(engine.state.player(1).hand) == 1, "手牌区里只剩【2】**摸到的那一张**"
    assert hand not in engine.state.player(1).hand


def test_yinghun_target_chooses_the_discarded_cards():
    """Q2 = A：弃哪几张由**目标**自己选（不是孙坚挑、也不是引擎按顺序弃）。"""
    hand = (_cards("slash")[1], _cards("dodge")[0], _cards("peach")[0])
    state = _yinghun_state(own_hp=2, target_hand=hand)
    engine = Engine.from_state(state, seed=7)
    players = [
        Yinghun(take=2, pick_index=-1, option=YINGHUN_DRAW_ONE_DISCARD_X)
        for _ in range(len(state.players))
    ]

    run_queries(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW))

    query = _discard_query(players)[0]
    chosen = {slot.card.card_id for slot in query.slots}
    assert set(hand) <= chosen and len(chosen) == len(hand) + 1, (
        "面里是目标自己的三张手牌 + 刚摸到的那一张"
    )
    # 引擎按应答搬牌：被弃的正是应答里点的那两张，其余留在手里。
    discarded = {
        ref.card_id for record in _discard_moves(engine, 1) for ref in record.cards
    }
    assert len(discarded) == 2
    assert set(engine.state.player(1).hand) == chosen - discarded


def test_yinghun_discards_in_one_move():
    """G4：一次弃置 = **一次**搬牌（一条 delta 带两张），不逐张派发「失去牌后」。"""
    hand = (_cards("slash")[1], _cards("dodge")[0])
    engine, _players = _yinghun_engine(
        own_hp=2,
        target_hand=hand,
        option=YINGHUN_DRAW_ONE_DISCARD_X,
        take=2,
    )

    moves = _discard_moves(engine, 1)
    assert len(moves) == 1, "两张牌一次搬完（逐张会派发两次「失去牌后」）"
    assert len(moves[0].cards) == 2


def test_yinghun_x_is_snapshotted_from_lost_hp():
    """Q4 = A：X = 上限 − 当前体力，在发动时现算一次、写进帧私有（本次结算内恒为常数）。

    G9 说明：本技能结算里没有任何能改孙坚体力的路径 ⇒ "定格"与"每次现算"在可达局面里
    不可区分。可断言的是**帧私有里确实存着**发动时那一个 X（重入不重算），且两段效果
    都读它（选项 2 的弃置张数 = 快照里的 X）。
    """
    state = _yinghun_state(
        own_hp=1,
        own_hp_max=5,
        target_hand=tuple(_cards("slash")[1:6]),
    )
    engine = Engine.from_state(state, seed=7)
    players = [
        Yinghun(take=4, option=YINGHUN_DRAW_ONE_DISCARD_X)
        for _ in range(len(state.players))
    ]
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == "SELECT_CARDS",
    )

    frame = engine.phase_body_frame()
    assert frame is not None, "准备阶段体帧还在（不压栈的那条）"
    assert frame.data.get("yinghun_x") == 4, "X = 体力上限 − 当前体力 = 5 − 1"
    pending = engine.pending
    assert pending is not None and pending.kind == "SELECT_CARDS"
    assert pending.spec.total == (4, 4), "弃置 4 张 = 快照里的 X"


def test_yinghun_short_draw_ends_in_a_draw():
    """G10：两堆皆空 ⇒ 短摸即平局（既有引擎行为，不新增分支）。

    "平"必须发生在**英魂的摸牌**里（不是摸牌阶段自己摸空）：故断言平局之前已经问过
    「是否发动」、且 ``PHASE_DRAW`` 一次都没落。
    """
    state = _yinghun_state(own_hp=2, deck=())
    engine = Engine.from_state(state, seed=7)
    players = [Yinghun() for _ in range(len(state.players))]
    run_queries(engine, players, lambda e: e.finished)

    assert len(players[0].activations) == 1, "平局发生在英魂的摸牌里，不是摸牌阶段"
    assert not ran_timing(engine, Timing.PHASE_DRAW), "准备阶段就收局了"
    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end" and tail.reason == "both_decks_empty"


def test_yinghun_suspension_resumes_without_replaying_the_draw():
    """G7：摸牌内联派发「获得牌后」（这里用界夏侯惇【清俭】）⇒ 挂起 → 续跑**不重摸**。

    游标 ``yinghun_drawn`` 在搬牌**之前**落位，故续跑从弃置段接着走，不会把 X 张重摸一遍。
    """
    engine, players = _yinghun_engine(
        own_hp=1,
        own_hp_max=4,
        target_hero="jie_xiahoudun",
        target_hand=(),
        option=YINGHUN_DRAW_X_DISCARD_ONE,
    )

    assert [str(query.skill_id) for query in players[1].activations] == [QINGJIAN], (
        "摸牌确实内联派发了【清俭】⇒ 本回复真的挂起过一次（否则这条断言是空转）"
    )
    assert [len(record.cards) for record in _draw_moves(engine, 1)] == [3], (
        "续跑后仍然只有**一次** 3 张的摸牌（重摸会留下第二条 delta）"
    )
    assert len(engine.state.player(1).hand) == 2, "3 张里弃掉 1 张"


# ---------------------------------------------------------------------------
# §3 英魂 —— ④ 形状与数据面
# ---------------------------------------------------------------------------


def test_yinghun_is_a_prepare_phase_hook_with_a_two_step_flow():
    """形状（`shapes.md` §2 第 52 行「手写主体」）：钩子 + 座位步 / 选项步，主体住 `effect`。"""
    definition = CATALOG[YINGHUN]
    assert definition.locked is False, "牌面写「可以」⇒ 非锁定"
    assert definition.preactivated is False, "首步是座位步 ⇒ 直提态 + 逐步问，不翻预发动"
    assert [hook.timing for hook in definition.hooks] == [Timing.PHASE_PREPARE]
    flow = flow_for(YINGHUN)
    assert flow is not None, "有 SkillFlow ⇒ 步骤语言**内**"
    assert [type(step).__name__ for step in flow.steps] == ["PickSeats", "PickOption"]
    assert flow.steps[0].key == "seat" and flow.steps[1].key == "option"
    assert flow.steps[1].options == (
        YINGHUN_DRAW_X_DISCARD_ONE,
        YINGHUN_DRAW_ONE_DISCARD_X,
    )


def test_yinghun_requirement_is_the_card_text():
    """一句话文案 = 牌面逐字（含 X 的定义）；逐步文案四个键齐（含手写询问的两种键）。"""
    definition = CATALOG[YINGHUN]
    assert definition.requirement == YINGHUN_REQUIREMENT
    assert set(definition.step_requirements) == {"seat", "option", "card", "discard"}
    assert definition.step_requirements["card"] == definition.step_requirements["discard"], (
        "手写询问的 SELECT_CARDS 逐步文案两种键同文（先例 = 【刚烈】）"
    )


def test_sunjian_mobile_is_the_wu_general_of_the_mobile_lin_pack():
    """数据面：孙坚移动版那一行与 `heroes.lin_mobile.json` 同源（`hero.<武将 id>.<短 id>`）。"""
    from sgs.data import hero_by_id

    hero = hero_by_id(SUNJIAN)
    assert hero is not None
    assert hero.name == "孙坚移动版"
    assert hero.true_name == "孙坚"
    assert hero.version == "lin-mobile"
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("wu", "male", 4)
    assert hero.enabled is True
    assert [f"hero.{hero.id}.{skill.id}" for skill in hero.skills] == [YINGHUN]


# ===========================================================================
# §4 徐晃移动版【断粮】（lin-batch 票 03）
#
# 牌面（`…/徐晃/技能.txt`，逐字；口径 = **移动版**，见票面 §0.1）：
#
#     断粮
#     　①你可以将一张黑色非锦囊牌当【兵粮寸断】使用；
#     　②你对距离为2的其他角色使用【兵粮寸断】无距离限制。
#
# 用户 2026-11-08 的裁定（票面 §0.2 / §1 询问剧本）：
#
# - **移动版口径、没有【截辎】**（OL 现行 / OL 官网 / 一将成名才有那一条）；
# - **② 的合法目标 = 距离 ≤ 2**（距离 ≥ 3 仍不合法，**不是**"完全无距离限制"），落点 =
#   `DISTANCE_LIMIT` 的 `max(current, 2)`，`current is None` 时原样返回 `None`；
# - **`standard_108` 档牌堆里没有【兵粮寸断】** ⇒ 整条不产出候选（Q2 = A，账本 L938）。
#
# 判据全走**外部缝**：`legal_actions_for` 的候选面 / `check_targets` / 引擎下发的
# `transform_preactivations` 清单 / 记录流与终局状态。**不 mock、不旁路候选**。
# ===========================================================================

XUHUANG = "xuhuang_mobile"
DUANLIANG = "hero.xuhuang_mobile.duanliang"
QICAI = "hero.huangyueying.qicai"
JIZHI = "hero.huangyueying.jizhi"
JIE_JIZHI = "hero.jie_huangyueying.jizhi"
XIAOJI = "hero.sunshangxiang.xiaoji"

DUANLIANG_REQUIREMENT = (
    "选择1张黑色非锦囊牌当【兵粮寸断】使用；"
    "你对距离为2的其他角色使用【兵粮寸断】无距离限制"
)

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：**【兵粮寸断】**（2 张）只在这一档里 ⇒ 断粮的产物在这一档才有实体牌。"""

SHORTAGE_160 = tuple(cards_of_type(PACK_160, "supply_shortage"))
FILLERS = (2, 3)
"""座位 0 摸牌阶段摸到的两张（红【闪】：出牌阶段用不出去 ⇒ 不干扰候选面的读数）。"""


def _find_160(*, category: Category, color: Color, exclude: tuple[int, ...] = ()) -> int:
    """按 (大类, 颜色) 取一张牌（数据表顺序 ⇒ 确定性，不依赖洗牌）。

    大类读数据文件的 :func:`sgs.data.card_category`（唯一真源），颜色读
    :func:`sgs.data.color_of_suit`——"非锦囊"的判据与实现同为"不是 ``trick`` 大类"。
    """
    excluded = set(exclude)
    for card_id, card in enumerate(PACK_160.deck.cards):
        if card_id in excluded:
            continue
        if card_category(PACK_160.pack_id, card.type) is not category:
            continue
        if color_of_suit(card.suit) is not color:
            continue
        return card_id
    raise AssertionError(f"军争 160 张里找不到 {color}/{category} 的牌")


BLACK_BASIC = _find_160(category=Category.BASIC, color=Color.BLACK)
"""黑【杀】（♠）：**基本牌** ⇒ 「非锦囊」的源。"""

BLACK_EQUIP = _find_160(category=Category.EQUIPMENT, color=Color.BLACK)
"""黑装备牌（♠【青釭剑】）：装备牌同样是「非锦囊」的源（装/换装备都不影响它是源）。"""

BLACK_TRICK = _find_160(category=Category.TRICK, color=Color.BLACK)
"""黑锦囊（♣【决斗】）：**不是**源（「非锦囊」把它排除）。"""

RED_BASIC = _find_160(category=Category.BASIC, color=Color.RED)
"""红基本牌（♥【闪】）：颜色不符 ⇒ 不是源。"""


def _duanliang_state(
    *,
    hand: tuple[int, ...] = (),
    equip: tuple[int, ...] = (),
    judge: tuple[int, ...] = (),
    seats: int = 2,
    dead: tuple[int, ...] = (),
    hero: str | None = XUHUANG,
    pack=PACK_160,
):
    """座位 0 = 徐晃移动版（``hero=None`` 换人做对照）；座位 1 可带判定区；其余空座。"""
    specs = [SeatSpec(hero=hero, hand=tuple(hand), equip=tuple(equip))]
    for seat in range(1, seats):
        specs.append(
            SeatSpec(
                judge=tuple(judge) if seat == 1 else (),
                alive=seat not in dead,
            )
        )
    return build_state(
        pack=pack,
        seats=specs,
        deck=deck_excluding(pack, *hand, *equip, *judge),
        turn_seat=0,
    )


def _play_candidates(state, seat: int = 0):
    """出牌阶段的**内部候选池**（与 ``test_hero_transform.py`` 的外缝同一处）。"""
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _virtual_shortage(actions) -> list:
    """候选里【断粮】转出来的**虚拟【兵粮寸断】**（``card_id < 0`` = 虚拟牌）。"""
    return [
        action
        for action in actions
        if isinstance(action, (UseCard, PlayCard))
        and action.card.card_id < 0
        and action.card.type == "supply_shortage"
    ]


def _shortage_targets(actions) -> set[int]:
    """虚拟【兵粮寸断】的可指定目标（每个候选一条目标组合、单目标 ⇒ 取首项）。"""
    return {int(action.targets[0]) for action in _virtual_shortage(actions)}


def _timings(engine, timing: Timing) -> list[int]:
    """某时机 dispatch 过的座位序（记录流的公开面）。"""
    return [
        int(record.actor)
        for record in engine.records
        if record.kind == "timing"
        and record.timing is timing
        and record.actor is not None
    ]


def _moves(engine, *, reason: MoveReason, to_zone: Zone | None = None) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is reason
        and (to_zone is None or record.to_zone.zone is to_zone)
    ]


class _DuanliangUser:
    """座位脚本：出牌阶段用【断粮】把 ``source`` 当【兵粮寸断】指到 ``target``。

    ``accept`` = 允许**真的发动**的技能 id 集合（本票借它让中继技能 ——【枭姬】/【集智】——
    发声，从而看得见"失去装备后"与"使用锦囊时"两条接缝）；``asked`` 记下**全部**被问过的
    技能 id（"没被问"与"问了但放弃"是两件事，前者才是"技能不适用"）。
    不做断粮的座位（``source=None``）只回最小合法应答。
    """

    def __init__(
        self,
        *,
        source: int | None = None,
        target: int = 1,
        accept: tuple[str, ...] = (),
    ) -> None:
        self.source = source
        self.target = target
        self.accept = frozenset(accept)
        self.used = False
        self.asked: list[str] = []
        self.activations: list[str] = []
        self.play_queries: list = []
        """自己收到的 ``PLAY_PHASE`` 询问（清单就在它上面 ⇒ 断言"清单里有没有这条技能"）。"""

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "PLAY_PHASE":
            self.play_queries.append(query)
        if kind == "PLAY_PHASE" and not self.used and self.source is not None:
            steps = (getattr(query, "transform_preactivations", None) or {}).get(DUANLIANG)
            if steps:
                card_step = next(step for step in steps if step.name == "card")
                options = tuple(int(option) for option in card_step.pool[0].options)
                assert self.source in options, f"源牌不在清单的来源步里：{options}"
                self.used = True
                return ActivateSkill(
                    skill_id=DUANLIANG,
                    picks=(self.source,),
                    choices=(f"seat:{self.target}",),
                    answers_seq=query.seq,
                )
        if kind == "SKILL_ACTIVATION":
            skill_id = str(getattr(query, "skill_id", "") or "")
            self.asked.append(skill_id)
            activate = next(
                (action for action in query.legal_actions if isinstance(action, ActivateSkill)),
                None,
            )
            if skill_id in self.accept and activate is not None:
                self.activations.append(skill_id)
                return activate
        for action in query.legal_actions:
            if isinstance(action, (EndPhase, Pass)):
                return action
        return query.legal_actions[0]


def _deck_with_judge(*, judge: int, used: tuple[int, ...] = ()) -> tuple[int, ...]:
    """牌堆：座位 0 的摸牌阶段摸走 :data:`FILLERS`，座位 1 的判定摸到 ``judge``。"""
    rest = tuple(
        card_id
        for card_id in range(len(PACK_160.deck.cards))
        if card_id not in {judge, *FILLERS, *used}
    )
    return (*FILLERS, judge, *rest)


def _play_started(engine, seat: int) -> bool:
    """该座位自己的**出牌阶段**已经开始（``PHASE_PLAY_START`` 已 dispatch 给他）。"""
    return seat in _timings(engine, Timing.PHASE_PLAY_START)


def _run_duanliang(
    *,
    hand: tuple[int, ...] = (),
    equip: tuple[int, ...] = (),
    source: int | None,
    judge: int | None = None,
    grants: tuple[str, ...] = (),
    accept: tuple[str, ...] = (),
    stop=None,
):
    """真引擎：跑到 ``stop``（缺省 = 座位 1 的出牌阶段开始）。

    座位 0 是徐晃移动版、``hand`` / ``equip`` 给源牌；``grants`` 是**获得**的技能
    （``granted_skills``：本票用它把【枭姬】/【集智】装到同一座位上造接缝）。
    """
    judge_card = (
        _find_160(category=Category.BASIC, color=Color.RED, exclude=FILLERS)
        if judge is None
        else judge
    )
    state = build_state(
        pack=PACK_160,
        seats=[
            SeatSpec(hero=XUHUANG, hand=tuple(hand), equip=tuple(equip)),
            SeatSpec(),
        ],
        deck=_deck_with_judge(judge=judge_card, used=(*hand, *equip)),
        turn_seat=0,
    )
    state.player(0).granted_skills.extend(grants)
    engine = Engine.from_state(state, seed=1)
    user = _DuanliangUser(source=source, accept=accept)
    run_queries(
        engine,
        [user, _DuanliangUser()],
        stop if stop is not None else (lambda current: _play_started(current, 1)),
    )
    return engine, user


# ---------------------------------------------------------------------------
# §4 断粮 —— ① 源筛选（黑色 ∧ 非锦囊 ∧ 手牌 ∪ 装备）
# ---------------------------------------------------------------------------


def test_duanliang_is_not_offered_without_a_black_non_trick_card():
    """G1 付不起：手里只有**红**基本牌 ⇒ 候选阶段就不产出（不是发空询问）。"""
    state = _duanliang_state(hand=(RED_BASIC,))

    assert not _virtual_shortage(_play_candidates(state)), (
        "红包不是源（①写的是「黑色」）⇒ 没有候选，清单里连这条技能都不该出现"
    )


def test_duanliang_does_not_convert_a_black_trick():
    """「非锦囊」= 基本 ∪ 装备：黑色**锦囊**不是源（延时类也在锦囊大类里，一并排除）。"""
    state = _duanliang_state(hand=(BLACK_TRICK,))

    assert not _virtual_shortage(_play_candidates(state)), (
        "黑色锦囊不是【断粮】的源（三类牌里只排除 trick 那一类）"
    )


def test_duanliang_offers_a_black_equipment_card_from_the_equip_zone():
    """区域默认（W4-3）：牌面没写区域 ⇒ 手牌 ∪ 装备；判定区**不默认包含**。"""
    equip_state = _duanliang_state(equip=(BLACK_EQUIP,))

    virtual = _virtual_shortage(_play_candidates(equip_state))
    assert virtual, "装备区的黑装备牌是源（与【奇袭】同一档的区域默认）"
    assert {action.card.virtual_sources for action in virtual} == {(BLACK_EQUIP,)}

    judge_state = _duanliang_state()
    judge_state.player(0).judge.append(BLACK_EQUIP)
    assert not _virtual_shortage(_play_candidates(judge_state)), (
        "判定区的牌不是源：区域默认不包含判定区（账本 L674 / L775②）"
    )


# ---------------------------------------------------------------------------
# §4 断粮 —— ② 目标池（距离 ≤ 2 = 用户 2026-11-08 的裁定）
# ---------------------------------------------------------------------------


def test_duanliang_is_not_offered_without_a_legal_target():
    """G2 付得起但没收益：其他座位全死了 ⇒ 目标池空 ⇒ 整条不出现（零副作用）。"""
    state = _duanliang_state(hand=(BLACK_BASIC,), seats=3, dead=(1, 2))

    assert not _virtual_shortage(_play_candidates(state)), (
        "一个合法目标都没有 ⇒ 不产出候选（与 G1 同一条纪律：不发没有候选的询问）"
    )


def test_duanliang_target_pool_is_other_alive_seats_at_distance_at_most_two():
    """合法目标 = 其他 + 存活 + **距离 ≤ 2**；自己与死者永不在池里。"""
    state = _duanliang_state(hand=(BLACK_BASIC,), seats=6, dead=(5,))

    assert _shortage_targets(_play_candidates(state)) == {1, 2, 3, 4}, (
        "座位 2 / 3 都在距离 2 以内（② 放宽的正是这一格）；座位 5 已死、自己不在池里"
    )
    assert distance(state, 0, 2) == 2 and distance(state, 0, 3) == 2


def test_duanliang_keeps_distance_three_illegal():
    """距离 ≥ 3 仍不合法（用户裁的**不是**"完全无距离限制"）。"""
    state = _duanliang_state(hand=(BLACK_BASIC,), seats=7)

    assert distance(state, 0, 3) == 3 and distance(state, 0, 4) == 3, "前提：这两座确实是距离 3"
    assert _shortage_targets(_play_candidates(state)) == {1, 2, 5, 6}, (
        "距离 3 的座位 3 / 4 不可选（② 只放宽到 2）"
    )


def test_duanliang_distance_exemption_covers_real_and_converted_supply_shortage():
    """② 对**真实的**与**转化出的**【兵粮寸断】都生效（判据按牌名，不按来源）。"""
    converted = _duanliang_state(hand=(BLACK_BASIC,), seats=7)
    assert _shortage_targets(_play_candidates(converted)) == {1, 2, 5, 6}

    real = SHORTAGE_160[0]
    real_state = _duanliang_state(hand=(real,), seats=7)
    assert check_targets(real_state, 0, real_state.card(real)) == (1, 2, 5, 6), (
        "真牌的额定距离也从 1 放宽到 2（同一条 DISTANCE_LIMIT 链）"
    )

    plain = _duanliang_state(hero=None, hand=(real,), seats=7)
    assert check_targets(plain, 0, plain.card(real)) == (1, 6), (
        "没有这条技能的人：额定距离仍是 1（② 是**主体侧**的修饰器）"
    )


def test_duanliang_does_not_narrow_a_fully_exempt_distance_chain():
    """M3：链上已有 `None`（完全豁免，如【奇才】）⇒ 原样返回 `None`，不许收窄成 2。"""
    real = SHORTAGE_160[0]
    state = _duanliang_state(hand=(real,), seats=7)
    state.player(0).granted_skills.append(QICAI)

    assert check_targets(state, 0, state.card(real)) == (1, 2, 3, 4, 5, 6), (
        "【奇才】已经把距离条件整条豁免 ⇒ 距离 3 的座位也合法；【断粮】不许把它收窄回 2"
    )

    control = _duanliang_state(hand=(real,), seats=7)
    assert check_targets(control, 0, control.card(real)) == (1, 2, 5, 6), (
        "对照：链上没有 `None` 时 ② 只放宽到 2（证明上一行不是「本来就合法」）"
    )


def test_duanliang_cannot_target_a_character_with_a_supply_shortage_in_the_judge_zone():
    """C5：判定区**已有同名**【兵粮寸断】的目标不是合法目标（候选阶段就剔除）。"""
    real = SHORTAGE_160[0]
    state = _duanliang_state(hand=(BLACK_BASIC,), seats=4, judge=(real,))

    assert _shortage_targets(_play_candidates(state)) == {2, 3}, (
        "座位 1 的判定区已有同名 ⇒ 不是候选（不发「选了没用」的询问）"
    )
    assert check_targets(state, 0, state.card(real)) == (2, 3), (
        "真牌同一条链：同名守卫对转化牌与真牌一视同仁"
    )


# ---------------------------------------------------------------------------
# §4 断粮 —— 产物（单源转化：继承源牌花色 / 点数 / 颜色；产物牌名 = 兵粮寸断）
# ---------------------------------------------------------------------------


def test_duanliang_product_inherits_the_source_suit_and_rank():
    """G6：单源转化**继承源牌的花色 / 点数 / 颜色**（规则 §16.3；属性不继承，与锦囊无关）。"""
    state = _duanliang_state(hand=(BLACK_EQUIP,))
    origin = state.card(BLACK_EQUIP)

    virtual = _virtual_shortage(_play_candidates(state))

    assert virtual and virtual[0].card.suit == origin.suit
    assert virtual[0].card.rank == origin.rank
    assert virtual[0].card.color is Color.BLACK
    assert virtual[0].card.name == state.card(SHORTAGE_160[0]).name, "产物牌名 =【兵粮寸断】"


# ---------------------------------------------------------------------------
# §4 断粮 —— 效果链（置入判定区 → 判定非梅花 ⇒ 跳过摸牌阶段；既有真牌链，不新写）
# ---------------------------------------------------------------------------


def _judge_of(suit: Suit) -> int:
    """按判定花色取一张具体牌（判定花色由测试显式指定，不依赖洗牌）。"""
    for card_id, card in enumerate(PACK_160.deck.cards):
        if card_id in {*SHORTAGE_160, *FILLERS}:
            continue
        if card.suit is suit:
            return card_id
    raise AssertionError(f"军争 160 张里找不到 {suit} 的牌")


def test_duanliang_only_skips_the_draw_phase_on_a_non_club_judgment():
    """判定**非梅花** ⇒ 跳过目标摸牌阶段；**梅花** ⇒ 照常摸两张（真牌既有链）。"""
    skipped, _ = _run_duanliang(hand=(BLACK_BASIC,), source=BLACK_BASIC, judge=_judge_of(Suit.SPADE))
    normal, _ = _run_duanliang(hand=(BLACK_BASIC,), source=BLACK_BASIC, judge=_judge_of(Suit.CLUB))

    assert _timings(skipped, Timing.PHASE_DRAW) == [0], (
        "座位 1 的摸牌阶段整段不存在（连边界也不发）：只留下座位 0 自己那一次"
    )
    assert _timings(normal, Timing.PHASE_DRAW) == [0, 1], "梅花 ⇒ 座位 1 照常摸牌"
    assert skipped.state.player(1).hand == [], "非梅花 ⇒ 一张牌都没摸"
    assert len(normal.state.player(1).hand) == 2, "梅花 ⇒ 照常摸两张"

    put = [record for record in _moves(skipped, reason=MoveReason.PUT) if record.to_zone.zone is Zone.JUDGE]
    assert [(record.to_zone.seat, record.owner) for record in put] == [(1, 0)], (
        "使用当时就置入目标判定区（L4；判定推迟到目标自己的判定阶段）"
    )
    assert BLACK_BASIC in skipped.state.discard, "判定结算完，来源牌进弃牌堆"


def test_duanliang_does_not_trigger_jizhi():
    """两版【集智】都不触发：标准版只排除**延时类**、界版只排除**转化**——断粮两样都占。"""
    for skill_id in (JIZHI, JIE_JIZHI):
        engine, user = _run_duanliang(
            hand=(BLACK_BASIC,), source=BLACK_BASIC, grants=(skill_id,)
        )

        assert skill_id not in user.asked, (
            f"{skill_id} 不该被问：产物是**延时类锦囊**（标准版排除它）且是**转化牌**"
            "（界版排除它）"
        )
        assert len(engine.state.player(0).hand) == len(FILLERS), "没有额外的集智摸牌"


def test_duanliang_resumes_after_an_equipment_loss_hook():
    """G7：源牌从装备区搬出处理区 ⇒ 内联派发"失去装备后"（【枭姬】）⇒ 续跑不重搬。"""
    engine, user = _run_duanliang(
        equip=(BLACK_EQUIP,),
        source=BLACK_EQUIP,
        grants=(XIAOJI,),
        accept=(XIAOJI,),
        stop=lambda current: BLACK_EQUIP in current.state.player(1).judge,
    )

    assert XIAOJI in user.asked and XIAOJI in user.activations, (
        "源牌离开装备区就是一次「失去牌」（账本 L795）⇒【枭姬】应当被问、且这次发动了"
    )
    assert len(engine.state.player(0).hand) == len(FILLERS) + 2, (
        "枭姬摸两张：摸牌阶段的 2 张 + 枭姬的 2 张"
    )
    assert BLACK_EQUIP not in engine.state.player(0).equip, "源牌已离开装备区"
    assert engine.state.virtual_judge.get(BLACK_EQUIP) is not None, (
        "判定区里那张牌代表的是**虚拟**【兵粮寸断】（virtual_judge 记着映射）"
    )
    assert not engine.state.processing, "处理区不留卡住的牌（续跑把同一次移动收尾了）"


def test_duanliang_is_offered_on_the_160_deck_and_not_on_the_108_deck():
    """Q2 = A（账本 L938）：`standard_108` 档没有【兵粮寸断】实体牌 ⇒ 整条不产出。"""
    offered, user = _run_duanliang(hand=(BLACK_BASIC,), source=BLACK_BASIC)
    assert user.play_queries, "座位 0 的出牌阶段应当被问过"
    first = user.play_queries[0]
    assert DUANLIANG in (first.transform_preactivations or {}), (
        "军争 160 档：清单里有断粮"
    )

    state_108 = _duanliang_state(hand=(BLACK_BASIC,), pack=PACK)
    assert not _virtual_shortage(_play_candidates(state_108)), (
        "标准 108 档的牌堆里一张【兵粮寸断】都没有 ⇒ 不产出该方向（`_card_name` 会回落到"
        " 英文 id，那样的虚拟牌不是本仓的语义）"
    )


# ---------------------------------------------------------------------------
# §4 断粮 —— 形状 / 数据面
# ---------------------------------------------------------------------------


def test_duanliang_is_a_single_source_conversion_with_a_distance_limit_modifier():
    """形状：一条**转化声明**（`CANDIDATES`）＋ 一个 `DISTANCE_LIMIT` 修饰器，无钩子 / 流水线。"""
    definition = CATALOG[DUANLIANG]

    assert definition.virtual_type_ids == ("supply_shortage",), "产物牌名 =【兵粮寸断】"
    assert definition.virtual_source_count == 1, "单源（恒 1 张）"
    assert definition.locked is False and definition.preactivated is False
    assert {modifier.point for modifier in definition.modifiers} == {
        ModifierPoint.CANDIDATES,
        ModifierPoint.DISTANCE_LIMIT,
    }, "① 走 CANDIDATES（转化入口）、② 走 DISTANCE_LIMIT（额定距离放宽）"
    assert not definition.hooks and not definition.seams, "本技能不挂钩子 / 接缝"
    assert flow_for(DUANLIANG) is None, "转化声明没有流水线 ⇒ 步骤语言**外**"


def test_duanliang_requirement_names_both_halves():
    """一句话文案 = ①「选择1张…当…使用」＋ ②「…无距离限制」（同一句，转化技没有逐步文案）。"""
    definition = CATALOG[DUANLIANG]

    assert definition.requirement == DUANLIANG_REQUIREMENT
    assert definition.step_requirements is None, (
        "转化技只透 requirement（conversion_skill_definitions 不写 step_requirements）"
    )


def test_the_xuhuang_mobile_hero_is_in_the_default_pool():
    """数据面：徐晃移动版那一行与 `heroes.lin_mobile.json` 同源（`hero.<武将 id>.<短 id>`）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    hero = hero_by_id(XUHUANG)
    assert hero is not None
    assert hero.name == "徐晃移动版"
    assert hero.true_name == "徐晃"
    assert hero.version == "lin-mobile"
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("wei", "male", 4)
    assert hero.enabled is True
    assert [f"hero.{hero.id}.{skill.id}" for skill in hero.skills] == [DUANLIANG]
    assert XUHUANG in enabled_hero_ids(), "启用 ⇒ 进默认池（林包移动版按独立版本并存）"


# ===========================================================================
# §5 曹丕移动版【行殇】【放逐】【颂威】（lin-batch2 票 01）
#
# 牌面（`…/曹丕/技能.txt`，逐字；口径 = **移动版**，见票面 §0.1）：
#
#     行殇
#     　当其他角色死亡时，你可以获得其所有牌。
#
#     放逐
#     　当你受到伤害后，你可以令一名其他角色翻面，然后该角色摸X张牌（X为你已损失的体力值）。
#
#     颂威
#     　主公技，当其他魏势力角色的黑色判定牌生效后，其可以令你摸一张牌。
#
# 用户 2026-11-08 签 **Q1–Q7 全按建议 A**（票头 `规范确认:`）：
#   Q1 行殇取「手牌 + 装备区」；Q2 搬死者牌不派发「失去牌后」；Q3 死者无牌 / 已判胜连问
#   都不发；Q4 放逐先翻面后摸牌；Q5 颂威每次黑色判定一次（不限每回合）；Q6 行殇进
#   `tests/test_hero_catalog.py` 的豁免表；Q7 放逐的座位步登记为敌对。
# 判据全走**外部缝**：询问（含跨座位的 actor）、记录流 delta、终局状态。**不 mock**。
# ===========================================================================

CAOPI = "caopi_mobile"
XINGSHANG = "hero.caopi_mobile.xingshang"
FANGZHU = "hero.caopi_mobile.fangzhu"
SONGWEI = "hero.caopi_mobile.songwei"
JIE_QINGJIAN = "hero.jie_xiahoudun.qingjian"
JIE_LIANYING = "hero.jie_luxun.lianying"
INDULGENCE = _cards("indulgence")[0]
EIGHT_DIAGRAMS = _cards("eight_diagrams")[0]

XINGSHANG_REQUIREMENT = "当其他角色死亡时，你可以获得其所有的牌"
FANGZHU_REQUIREMENT = (
    "当你受到伤害后，你可以令一名其他角色翻面，然后该角色摸X张牌（X为你已损失的体力值）。"
)
SONGWEI_REQUIREMENT = "主公技，当其他魏势力角色的黑色判定牌生效后，其可以令你摸一张牌。"


def _first_card_of_color(color, *, exclude=()):
    """一张指定颜色的实体牌（判定牌只看颜色；避开已占用的 id）。"""
    used = {INDULGENCE, *exclude}
    return next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in used and color_of_suit(PACK.deck.cards[card_id].suit) is color
    )


def _black_judge(*, exclude=()) -> int:
    return _first_card_of_color(Color.BLACK, exclude=exclude)


def _red_judge(*, exclude=()) -> int:
    return _first_card_of_color(Color.RED, exclude=exclude)


class Caopi:
    """座位脚本：按**技能 id 前缀**白名单发「是否发动」，其余技能一律放弃。

    ``activate`` = 前缀元组（空 = 全放弃）；``seat`` = 座位步选谁；``pick`` = 选牌步应答；
    ``bagua`` = 在【闪】窗口里点【八卦阵】；``slash_at`` = 出牌阶段对谁用【杀】。
    其它询问取最小合法应答（响应 / 弃牌等）。**别的技能不是被测对象**，故缺省放弃。
    """

    def __init__(
        self,
        *,
        activate=(),
        seat=None,
        pick=None,
        bagua=False,
        slash_at=None,
        rejudge_with=None,
    ) -> None:
        self.activate = tuple(activate)
        self.seat = seat
        self.pick = pick
        self.bagua = bagua
        self.slash_at = slash_at
        self.rejudge_with = rejudge_with
        self.activations: list = []
        self.options: list = []
        self.selections: list = []

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        skill = str(getattr(query, "skill_id", "") or "")
        if kind == "SKILL_ACTIVATION":
            self.activations.append(query)
            if self.activate and skill.startswith(self.activate):
                action = next(
                    (a for a in query.legal_actions if isinstance(a, ActivateSkill)), None
                )
                if action is not None:
                    return action
            return next(
                (a for a in query.legal_actions if isinstance(a, Pass)),
                query.legal_actions[0],
            )
        if kind == "SELECT_OPTION":
            self.options.append(query)
            if self.seat is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, SelectOption)
                        and str(action.choice) == f"seat:{self.seat}"
                    ):
                        return action
            return next(
                (a for a in query.legal_actions if isinstance(a, SelectOption)),
                query.legal_actions[0],
            )
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            if self.pick is not None:
                return self.pick(query)
            return next(
                (a for a in query.legal_actions if isinstance(a, SelectCards)),
                query.legal_actions[0],
            )
        if kind == "PLAY_PHASE":
            if self.slash_at is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == "slash"
                        and action.targets == (self.slash_at,)
                    ):
                        return action
            return next(
                (a for a in query.legal_actions if isinstance(a, EndPhase)),
                query.legal_actions[0],
            )
        if kind in ("DODGE", "RESPOND") and self.bagua:
            for action in query.legal_actions:
                if (
                    isinstance(action, ActivateSkill)
                    and action.skill_id == "equip.eight_diagrams"
                ):
                    return action
        if kind in ("DODGE", "RESPOND") and self.rejudge_with is not None:
            for action in query.legal_actions:
                if (
                    isinstance(action, SubstituteJudgeCard)
                    and action.card.card_id == self.rejudge_with
                ):
                    return action
        return next(
            (a for a in query.legal_actions if isinstance(a, Pass)), query.legal_actions[0]
        )


def _caopi_scripts(engine, *, activate=(), **kwargs) -> list:
    """每座一个 :class:`Caopi`（``activate`` 对全部座位生效；别的座位也要能应答）。"""
    return [
        Caopi(activate=activate, **kwargs) for _ in range(len(engine.state.players))
    ]


def _moves_to(engine, *, to_zone: ZoneRef, reason: MoveReason | None = None) -> list:
    """记录流里"搬到 ``to_zone``"的 ``card_move``（可按 reason 过滤）。"""
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone == to_zone
        and (reason is None or record.reason is reason)
    ]


def _death_pops(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is FrameKind.DEATH
    )


def _judge_pops(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is FrameKind.JUDGE
    )


# ---------------------------------------------------------------------------
# §5 行殇 —— ① 入口（Q1 两区有牌 / Q3 无牌与已判胜不问 / 死的不是自己）
# ---------------------------------------------------------------------------


def _xingshang_state(
    *,
    seats: int = 3,
    owner: int = 0,
    dead: int = 1,
    hand: tuple[int, ...] = (),
    equip: tuple[int, ...] = (),
    judge: tuple[int, ...] = (),
    dead_hero: str | None = None,
    deck: tuple[int, ...] | None = None,
):
    """座位 ``owner`` = 曹丕移动版（3 体力），座位 ``dead`` = 死者（按参数铺牌）。"""
    used = {*hand, *equip, *judge}
    specs = [SeatSpec() for _ in range(seats)]
    specs[owner] = SeatSpec(hero=CAOPI, hp=3, hp_max=3)
    specs[dead] = SeatSpec(
        hero=dead_hero,
        hand=tuple(hand),
        equip=tuple(equip),
        judge=tuple(judge),
    )
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck if deck is not None else deck_excluding(PACK, *used),
        turn_seat=0,
    )


def _run_xingshang(state, *, dead: int, activate: bool = True, stop=None) -> tuple:
    """把局面装进引擎、压一次死亡并推进到 ``DEATH_AFTER`` 之后；返回 ``(engine, players)``。"""
    engine = Engine.from_state(state, seed=7)
    players = [
        Caopi(activate=(XINGSHANG,) if activate else ())
        for _ in range(len(engine.state.players))
    ]
    declare_death(engine, dead)
    _advance(
        engine,
        players,
        stop or (lambda e: ran_timing(e, Timing.DEATH_AFTER) or e.finished),
    )
    return engine, players


def test_xingshang_is_offered_when_another_character_dies_with_cards():
    """Q1 = A：死者手牌 2 张 ⇒ 问一次「是否发动」，发动后两张都进曹丕手牌。"""
    engine, players = _run_xingshang(_xingshang_state(hand=(0, 1)), dead=1)

    assert len(_activation_queries(engine, XINGSHANG)) == 1, "一次死亡只问一次"
    assert engine.state.player(0).hand == [0, 1], "死者的两张手牌进了曹丕手牌"
    assert players[0].activations, "这一问发给曹丕（座位 0）"
    assert all(q.actor == 0 for q in _activation_queries(engine, XINGSHANG)), (
        "问的是技能持有者本人（「你可以获得」，不是死者）"
    )


def test_xingshang_is_not_offered_when_the_dead_has_no_cards():
    """Q3 = A：死者两区皆空（只有判定区的牌）⇒ 条件假、连问都不发。

    判定区的牌不算「其所有牌」的一部分（Q1），故"只有判定区的牌"也不构成候选。
    """
    engine, players = _run_xingshang(
        _xingshang_state(hand=(), judge=(INDULGENCE,)), dead=1
    )

    assert _activation_queries(engine, XINGSHANG) == [], "没有可拿的牌 ⇒ 不发空询问"
    assert players[0].activations == []
    assert engine.state.player(0).hand == []
    assert INDULGENCE in engine.state.discard, "判定区的牌照旧由系统弃掉"


def test_xingshang_is_not_offered_when_the_owner_is_the_one_dying():
    """H2：「其他角色」不含自己 ⇒ 曹丕自己死亡时这条技能不触发。"""
    state = _xingshang_state(seats=3, owner=1, dead=1, hand=(0, 1))
    engine, players = _run_xingshang(state, dead=1)

    assert _activation_queries(engine, XINGSHANG) == [], "死的是持有者 ⇒ 不问"
    assert engine.state.player(1).hand == [] and engine.state.player(1).equip == []
    assert 0 in engine.state.discard and 1 in engine.state.discard, "照常进弃牌堆"


def test_xingshang_is_not_offered_when_the_game_is_already_decided():
    """Q3 = A：终局已判定 ⇒ 条件读 `host.outcome_decided()` 为真 ⇒ 不问、也不拿牌。

    两座局里最后一名对手死亡 ⇒ 检测点（`DEATH_REVEAL_IDENTITY_AFTER`）先判胜，
    `DEATH_ON` 才派发 ⇒ 这正是"已判胜"的那一格。
    """
    state = _xingshang_state(seats=2, hand=(0, 1))
    engine, players = _run_xingshang(state, dead=1)

    assert engine.outcome_decided(), "两座局最后一名对手死亡 ⇒ 判定点先判胜"
    assert _activation_queries(engine, XINGSHANG) == [], "已判胜 ⇒ 连问都不发"
    assert engine.state.player(0).hand == [], "也不拿牌"
    assert 0 in engine.state.discard and 1 in engine.state.discard


# ---------------------------------------------------------------------------
# §5 行殇 —— ② 取牌范围（Q1：手牌 + 装备区；判定区 / 武将牌上的牌不取）
# ---------------------------------------------------------------------------


def test_xingshang_takes_hand_and_equipment_but_not_the_judge_zone():
    """Q1 = A：手牌 + 装备区进曹丕手牌；判定区的牌留在原地、由系统弃掉。"""
    equip = _cards("eight_diagrams")[0]
    engine, _players = _run_xingshang(
        _xingshang_state(hand=(0, 1), equip=(equip,), judge=(INDULGENCE,)), dead=1
    )

    assert engine.state.player(0).hand == [0, 1, equip], "手牌两张 + 装备一张"
    assert INDULGENCE not in engine.state.player(0).hand, "判定区不取（Q1）"
    assert INDULGENCE in engine.state.discard, "剩下的判定区由系统清区"
    assert engine.state.player(1).hand == [] and engine.state.player(1).equip == []


def test_xingshang_does_not_take_the_hero_card_cards():
    """Q1 的否证：武将牌上 / 旁的牌「移出游戏、不为任何角色所拥有」⇒ 不取。"""
    hero_card = _cards("peach")[0]
    state = _xingshang_state(hand=(0,), dead=1)
    zone = hero_card_zone_key(1, "hero.jie_xusheng.pojun")
    state.player(1).hero_cards[zone] = HeroCardZone(
        skill_id="hero.jie_xusheng.pojun", owner=1, cards=[hero_card]
    )
    engine, _players = _run_xingshang(state, dead=1)

    assert hero_card not in engine.state.player(0).hand, "武将牌上的牌不进手牌"
    assert hero_card in engine.state.discard, "由死亡结算的 3.b 单独置入弃牌堆"


# ---------------------------------------------------------------------------
# §5 行殇 —— ③ 时段与次序（DEATH_ON 早于系统清区；手牌先、装备区后）
# ---------------------------------------------------------------------------


def test_xingshang_gains_before_the_death_settlement_discards_the_rest():
    """时段：`DEATH_ON` 的取牌 delta 落在系统清区（判定区那条 `card_move`）**之前**。"""
    engine, _players = _run_xingshang(
        _xingshang_state(hand=(0,), judge=(INDULGENCE,)), dead=1
    )
    gains = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 0))
    clears = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone == ZoneRef(Zone.JUDGE, 1)
    ]
    assert gains and clears, "两条 delta 都要有（取牌 / 系统清区）"
    assert gains[0].seq < clears[0].seq, "行殇在系统的『弃置死亡角色区域里所有的牌』之前"


def test_xingshang_gains_hand_and_equipment_in_two_moves():
    """Q1 + 区序：逐来源区各一条 `card_move`，手牌先、装备区后（同 `discard_dead_cards`）。"""
    equip = _cards("eight_diagrams")[0]
    engine, _players = _run_xingshang(
        _xingshang_state(hand=(0, 1), equip=(equip,)), dead=1
    )
    gains = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone == ZoneRef(Zone.HAND, 0)
    ]
    assert [record.from_zone.zone for record in gains] == [Zone.HAND, Zone.EQUIP], (
        "两条：手牌区 → 手牌、装备区 → 手牌"
    )
    assert [record.reason for record in gains] == [MoveReason.PUT, MoveReason.PUT], (
        "「获得」= PUT（与【归心】/【天妒】同一条记录口径）"
    )
    assert [record.owner for record in gains] == [0, 0], "归属 = 获得者（曹丕）"


def test_xingshang_does_not_fire_move_after_for_the_dead_card_owner():
    """**Q2 = A**：搬死者的牌**不派发**「失去牌后」——死者的【连营】一问都不发。

    死者带【连营】（界陆逊）：若走移动入口 `move_in_frame`，`MOVE_AFTER` 会在死亡窗口内
    派发（死者实例还在窗口里）、问它"是否发动"。按 Q2 走 `move_card` + `emit_frame_delta`
    ⇒ 整段静默，连 `MOVE_AFTER` 时机记录都没有。
    """
    engine, _players = _run_xingshang(
        _xingshang_state(hand=(0, 1), dead_hero="jie_luxun"), dead=1
    )

    assert engine.state.player(0).hand == [0, 1], "牌照样拿到（只是不派发失去牌后）"
    assert not ran_timing(engine, Timing.MOVE_AFTER), "一条 MOVE_AFTER 都不该派发（Q2）"
    assert [q for q in _activation_queries(engine, JIE_LIANYING)] == [], (
        "死者的【连营】不该被问（L625：失去牌的是死者）"
    )


# ---------------------------------------------------------------------------
# §5 行殇 —— ④ 放弃出口 / 次数 / 重入 / 同批（G4 / G7 / G8）
# ---------------------------------------------------------------------------


def test_xingshang_declining_leaves_the_dead_cards_in_place():
    """「可以」= 整段发动的放弃出口（L816）：放弃 ⇒ 曹丕一张不拿、牌照常被系统弃掉。"""
    engine, players = _run_xingshang(
        _xingshang_state(hand=(0, 1)), dead=1, activate=False
    )

    assert len(_activation_queries(engine, XINGSHANG)) == 1, "照问"
    assert engine.state.player(0).hand == [], "放弃 ⇒ 一张不拿"
    assert 0 in engine.state.discard and 1 in engine.state.discard, "两张都进弃牌堆"


def test_xingshang_is_offered_once_per_death_frame():
    """G4：无限定词 ⇒ **逐事件一次**（一次死亡只问一次，不是逐张）。"""
    engine, _players = _run_xingshang(_xingshang_state(hand=(0, 1, 2)), dead=1)

    assert len(_activation_queries(engine, XINGSHANG)) == 1, "三张牌也只问一次"
    assert engine.state.player(0).hand == [0, 1, 2]


def test_xingshang_resumes_without_gaining_twice():
    """G8：同一帧被**再派发一轮** `DEATH_ON` ⇒ 帧私有 `_DONE` 位挡住第二次取牌。

    正常路径上不会重入（Q2 不派发"失去牌后"⇒ 没有挂起），这个防御位就是为"宿主级直调"
    准备的。判据：把帧内的 pass 进度清掉、逼引擎重新走一遍派发环 ⇒ 技能**重新被问到**
    （条件此刻确实成立），但答"发动"之后一张也不搬（`xingshang_done` 已落位）。
    """
    engine = Engine.from_state(
        _xingshang_state(hand=(0, 1), equip=(EIGHT_DIAGRAMS,)), seed=7
    )
    players = [Caopi(activate=(XINGSHANG,)) for _ in range(3)]
    declare_death(engine, 1)
    _advance(engine, players, lambda e: ran_timing(e, Timing.DEATH_AFTER) or e.finished)
    frame = next(
        candidate
        for candidate in reversed(engine.stack.frames)
        if candidate.kind is FrameKind.DEATH
    )
    assert engine.state.player(0).hand == [0, 1, EIGHT_DIAGRAMS], "第一次已拿完"

    # 宿主级直调：把 pass 进度与答案缓存清掉（= "这一帧的派发从头再来"），并放一张牌回
    # 死者手里，使条件再次成立。帧私有 `xingshang_done` **不**清——它才是被检验的防御位。
    frame.data.pop(PASS_STATE_KEY, None)
    frame.data["ask_answers"] = {}
    engine.state.player(1).hand.append(0)
    assert engine_steps.death_on(engine, frame) == "stay", "重新走到「是否发动」"
    reask = engine.pending
    assert reask is not None and str(reask.skill_id) == XINGSHANG, "第二问确实是行殇"
    engine.submit(players[0].respond(reask))

    assert engine.state.player(0).hand == [0, 1, EIGHT_DIAGRAMS], (
        "第二次一张都不再搬（`_DONE` 位）"
    )
    assert engine.state.player(1).hand == [0], "放回去的那张还留在死者手里"
    assert len(
        [
            record
            for record in engine.records
            if record.kind == "delta"
            and record.delta_type == "card_move"
            and record.to_zone == ZoneRef(Zone.HAND, 0)
        ]
    ) == 2, "整局只有第一次那两条取牌 delta（手牌 / 装备区）"


def test_xingshang_is_offered_for_each_death_of_a_simultaneous_batch():
    """同批多人同时死亡 ⇒ **每个死亡帧各问一次**（票面读法表「次数」行）。

    两座局会一次判胜（Q3 挡住），故用四座：先声明座位 1 的死亡、推进到它的 `DEATH_ON`
    结算完（帧还压着），再声明座位 2 的死亡（同批嵌套），两次死亡各拿各的牌。
    """
    state = _xingshang_state(
        seats=4, hand=(0, 1), deck=deck_excluding(PACK, 0, 1, 2, 3)
    )
    state.player(2).hand = [2, 3]
    engine = Engine.from_state(state, seed=7)
    players = [Caopi(activate=(XINGSHANG,)) for _ in range(4)]

    declare_death(engine, 1)
    for _ in range(20_000):
        # 停在"第一次死亡的行殇效果已经跑完、死亡帧还压着"的那一刻（同批嵌套的前提）。
        if engine.state.player(0).hand == [0, 1] and engine.pending is None:
            break
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    assert engine.state.player(0).hand == [0, 1], "第一次死亡已结算"

    declare_death(engine, 2)  # 同批嵌套：座位 1 的死亡帧还没出栈
    _advance(
        engine,
        players,
        lambda e: _death_pops(e) >= 2 or e.finished,
    )

    assert len(_activation_queries(engine, XINGSHANG)) == 2, "两个死亡事件各问一次"
    assert engine.state.player(0).hand == [0, 1, 2, 3], "两人的牌都拿到"
    assert engine.state.player(1).hand == [] and engine.state.player(2).hand == []


# ---------------------------------------------------------------------------
# §5 放逐 —— ① 入口（自己的伤害 / 逐事件一次 / 防止 / 死亡 / 空池）
# ---------------------------------------------------------------------------


def _fangzhu_run(
    seats: list[SeatSpec],
    *,
    amount: int = 1,
    source: int | None = 1,
    target: int = 0,
    channel: int | None = None,
    deck: tuple[int, ...] | None = None,
    discard: tuple[int, ...] = (),
    scripts: list | None = None,
    flipped: tuple[int, ...] = (),
    prevented: bool = False,
) -> tuple:
    """压一个 ``DAMAGE`` 子帧并推进到 ``DAMAGE_END``；座位 0 = 曹丕移动版。

    ``channel`` 缺省取第一张【杀】（放处理区）；``deck`` 缺省补上除已占用牌以外的全部实体牌。
    """
    used = {card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)}
    if channel is None:
        channel = next(
            card_id for card_id in _cards("slash") if card_id not in used
        )
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck
        if deck is not None
        else deck_excluding(PACK, *used, channel),
        processing=(channel,),
        discard=discard,
        turn_seat=0,
    )
    for seat in flipped:
        state.player(seat).flipped = True
    engine = Engine.from_state(state, seed=7)
    players = scripts or [
        Caopi(activate=(FANGZHU,)) for _ in range(len(state.players))
    ]
    frame = engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, channel, amount=amount, source=source, target=target),
        timing=Timing.DAMAGE_DEAL,
    )
    if prevented:
        # 「防止此伤害」= 帧标签 `PREVENTED`（【寒冰剑】/【藤甲】一类写入口）：
        # `emit_frame_timing` 只落一条时机记录、一个钩子都不派发。
        add_tag(frame, Tag.PREVENTED)
    _advance(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END) or e.finished)
    return engine, players


def test_fangzhu_is_offered_after_the_owner_takes_damage():
    """入口：曹丕受到伤害后问一次「是否发动」，发动 ⇒ 目标翻面 + 摸 X 张。"""
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)]
    )

    assert len(_activation_queries(engine, FANGZHU)) == 1
    assert _flips(engine) == [(1, True)], "目标翻面（默认指向座位 1）"
    assert engine.state.player(1).hand, "目标摸到牌（X = 2）"
    assert players[0].options and str(players[0].options[0].skill_id) == "fangzhu:seat"


def test_fangzhu_asks_only_on_the_owners_own_damage():
    """H2：**别人的**伤害 ⇒ 条件假（`_hurt_owner`），连问都不发。"""
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=4, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        target=1,
        source=2,
    )

    assert _activation_queries(engine, FANGZHU) == [], "受伤的不是曹丕 ⇒ 不问"
    assert _flips(engine) == []
    assert players[0].activations == []


def test_fangzhu_triggers_once_per_damage_event():
    """G4：牌面没有「1 点」⇒ `per_point=False`：**2 点伤害也只问一次**、只摸一次 X 张。"""
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=4, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        amount=2,
    )

    assert len(_activation_queries(engine, FANGZHU)) == 1, "逐事件一次（不是逐点）"
    assert len(players[0].options) == 1, "座位步只问一次"
    draws = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    assert [len(record.cards) for record in draws] == [2], "X = 2（4 − 2），一次摸完"
    assert _flips(engine) == [(1, True)]


def test_fangzhu_does_not_fire_when_the_damage_is_prevented():
    """「防止此伤害」⇒ 帧标签 ``PREVENTED`` ⇒ B 族一个钩子都不派发（只落一条时机记录）。"""
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=4, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        prevented=True,
    )

    assert _activation_queries(engine, FANGZHU) == []
    assert _flips(engine) == []
    assert players[0].activations == []


def test_fangzhu_is_not_offered_when_the_owner_dies_from_the_damage():
    """曹丕被这次伤害打死 ⇒ `DAMAGE_AFTER_TAKE` 时他已无实例（死亡结算在子帧里跑完）。"""
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=1, hp_max=3), SeatSpec(hp=4), SeatSpec(hp=4)],
        amount=2,
    )

    assert engine.state.player(0).alive is False
    assert _activation_queries(engine, FANGZHU) == [], "持有者已阵亡 ⇒ 不问"
    assert _flips(engine) == []


def test_fangzhu_seat_pool_is_other_alive_seats_only():
    """S1 / S4：座位池 = 除自己以外的**存活**座位（死者不进池），且没有 ``Pass``。"""
    engine, players = _fangzhu_run(
        [
            SeatSpec(hero=CAOPI, hp=3, hp_max=4),
            SeatSpec(hp=4),
            SeatSpec(hp=4, alive=False),
            SeatSpec(hp=4),
        ]
    )

    query = players[0].options[0]
    choices = {
        str(action.choice)
        for action in query.legal_actions
        if isinstance(action, SelectOption)
    }
    assert choices == {"seat:1", "seat:3"}, "自己（0）与死者（2）都不进池"
    assert not any(
        isinstance(action, Pass) for action in query.legal_actions
    ), "发动后必须选一名角色（没有 Pass）"
    assert _flips(engine) == [(1, True)], "默认选座位 1"


def test_fangzhu_with_no_other_alive_seat_offers_nothing():
    """S4：没有其他存活角色 ⇒ 条件假、整条流水线取消、**不发生任何询问**。"""
    engine, players = _fangzhu_run(
        [
            SeatSpec(hero=CAOPI, hp=3, hp_max=4),
            SeatSpec(hp=4, alive=False),
            SeatSpec(hp=4, alive=False),
        ],
        source=None,
    )

    assert _activation_queries(engine, FANGZHU) == [], "池空 ⇒ 连预发动都不问"
    assert players[0].activations == [] and players[0].options == []
    assert _flips(engine) == []


# ---------------------------------------------------------------------------
# §5 放逐 —— ② 效果（Q4：先翻面后摸牌；X = 持有者已损失体力值；放弃出口）
# ---------------------------------------------------------------------------


def test_fangzhu_flips_the_target_before_it_draws():
    """**Q4 = A**：翻面的 `flipped_change` 落在摸牌的 `card_move` **之前**（一手次序）。"""
    engine, _players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)]
    )

    flips = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]
    draws = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    assert len(flips) == 1 and len(draws) == 1
    assert flips[0].seq < draws[0].seq, (
        "先翻面、后摸牌（Q4：仓内 §13.4 的转写次序相反，按一手）"
    )


def test_fangzhu_flips_a_back_facing_target_to_the_front():
    """翻面是 **toggle**（L783）：已背面朝上的目标这一手把它翻回正面。"""
    engine, _players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        flipped=(1,),
    )

    assert _flips(engine) == [(1, False)], "背面 → 正面"
    assert engine.state.player(1).flipped is False
    assert engine.state.player(1).hand, "摸牌照旧（翻回正面也是合法收益）"


def test_fangzhu_x_is_the_owners_lost_hp():
    """X = **曹丕的**已损失体力值（不是目标的），在发动时定格。

    曹丕 3/4 挨 1 点 ⇒ X = 2；目标自己 1/4（已损失 3）⇒ 若错读成目标的，会摸 3 张。
    """
    engine, _players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=1), SeatSpec(hp=4)]
    )

    draws = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    assert [len(record.cards) for record in draws] == [2], "X = 4 − 2 = 2（不是目标的 3）"


def test_fangzhu_declining_leaves_flip_and_hand_untouched():
    """「可以」= 放弃出口（L816）：放弃 ⇒ 不翻面、不摸牌、座位步一问都不发。"""
    scripts = [Caopi(activate=()) for _ in range(3)]
    engine, players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        scripts=scripts,
    )

    assert len(_activation_queries(engine, FANGZHU)) == 1
    assert _flips(engine) == []
    assert engine.state.player(1).hand == []
    assert players[0].options == [], "放弃 ⇒ 座位步不发生"


def test_fangzhu_short_draw_ends_in_a_draw():
    """G10：摸牌时两堆皆空 ⇒ 短摸即平局（既有 L613 的口径）。"""
    engine, _players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(),
        discard=(),
    )

    assert engine.finished is True
    assert engine.pending_outcome is not None
    assert engine.pending_outcome.result == "draw", "短摸 ⇒ 平局"


def test_fangzhu_resumes_without_flipping_twice():
    """G7：摸牌内联触发「获得牌后」（【清俭】）并挂起 ⇒ 续跑不重翻、不重摸。

    目标 = 界夏侯惇（带【清俭】）：摸牌 → 派发 `MOVE_AFTER` →【清俭】问"是否发动" ⇒
    `draw_for_hand` 返回 ``"stay"`` ⇒ 芳逐的 `effect` 重入。判据 = 记录流里只有
    **一条** `flipped_change` 与 **一条** 摸牌 delta（各 X 张）。
    """
    scripts = [
        Caopi(activate=(FANGZHU,)),
        Caopi(activate=(JIE_QINGJIAN,), seat=0),
        Caopi(),
    ]
    engine, _players = _fangzhu_run(
        [
            SeatSpec(hero=CAOPI, hp=3, hp_max=4),
            SeatSpec(hero="jie_xiahoudun", hp=4),
            SeatSpec(hp=4),
        ],
        scripts=scripts,
    )

    assert len(_activation_queries(engine, JIE_QINGJIAN)) == 1, "摸牌确实内联触发了清俭"
    assert _flips(engine) == [(1, True)], "只翻一次"
    draws = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    assert [len(record.cards) for record in draws] == [2], "只摸一次 X = 2 张"
    assert engine.state.player(1).hand, "牌确实进了目标手牌"


def test_fangzhu_flip_skips_the_targets_next_turn():
    """L783 的联动：被翻面的角色在下个回合开始前翻回正面、并**跳过整个回合**。"""
    engine, _players = _fangzhu_run(
        [SeatSpec(hero=CAOPI, hp=3, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4)]
    )
    assert engine.state.player(1).flipped is True, "放逐先翻过去"

    turns_player = [Caopi(activate=()) for _ in range(3)]
    run_turns(engine, turns_player, turns=3)

    before_start = [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_BEFORE_START
    ]
    assert len(before_start) >= 3, "至少跑到座位 1 的下一次『回合开始前』"
    skipped = [
        record
        for record in engine.records
        if record.kind == "timing"
        and before_start[1] < record.seq < before_start[2]
    ]
    skipped_timings = {record.timing for record in skipped}
    for absent in (
        Timing.PHASE_PREPARE,
        Timing.PHASE_JUDGE,
        Timing.PHASE_DRAW,
        Timing.PHASE_PLAY_START,
        Timing.PHASE_DISCARD_START,
        Timing.PHASE_FINISH,
    ):
        assert absent not in skipped_timings, f"被跳过的回合不该发 {absent}"
    assert (1, False) in _flips(engine), "回合开始前把座位 1 翻回正面"
    assert engine.state.player(1).flipped is False


# ---------------------------------------------------------------------------
# §5 颂威 —— ① 入口（判定者 ≠ 主公 ∧ 同魏 ∧ 黑色；主公技的身份门）
# ---------------------------------------------------------------------------


def _songwei_run(
    *,
    judge_card: int,
    caopi_seat: int = 1,
    lord: bool = True,
    judged_kingdom: Kingdom = Kingdom.WEI,
    judge_cards: tuple[int, ...] = (INDULGENCE,),
    caopi_hand: tuple[int, ...] = (),
    extra: tuple[int, str, tuple[int, ...]] | None = None,
    deck: tuple[int, ...] | None = None,
    discard: tuple[int, ...] = (),
    scripts: list | None = None,
    stop=None,
) -> tuple:
    """座位 0 的判定阶段带延时锦囊（判定牌 = ``deck[0]``），``caopi_seat`` = 曹丕移动版。

    ``caopi_seat == 0`` 时曹丕**自己**就是被判定者（"主公自己"的负向格）。
    ``extra`` = ``(座位, 武将 id, 手牌)``，用来插一个改判者（【鬼才】）。
    """
    base = {"kingdom": judged_kingdom, "identity": Identity.REBEL}
    specs = [SeatSpec(**base), SeatSpec(**base), SeatSpec(**base)]
    specs[0] = SeatSpec(**base, judge=tuple(judge_cards))
    specs[caopi_seat] = SeatSpec(
        hero=CAOPI,
        kingdom=Kingdom.WEI,
        identity=Identity.LORD if lord else Identity.REBEL,
        hand=tuple(caopi_hand),
        judge=tuple(judge_cards) if caopi_seat == 0 else (),
    )
    if extra is not None:
        seat, hero, hand = extra
        specs[seat] = SeatSpec(**base, hero=hero, hand=tuple(hand))
    used = {card for spec in specs for card in (*spec.hand, *spec.equip, *spec.judge)}
    if deck is None:
        deck = (
            judge_card,
            *(
                card_id
                for card_id in range(len(PACK.deck.cards))
                if card_id not in used and card_id != judge_card
            ),
        )
    state = build_state(
        pack=PACK, seats=specs, deck=deck, discard=discard, turn_seat=0
    )
    engine = Engine.from_state(state, seed=1)
    players = scripts or [Caopi(activate=(SONGWEI,)) for _ in range(len(specs))]
    _advance(
        engine,
        players,
        stop or (lambda e: _judge_pops(e) >= 1 or e.finished),
    )
    return engine, players


def test_songwei_is_offered_to_the_judged_wei_character():
    """入口：魏势力角色判定出黑牌 ⇒ 问**判定者**「是否发动」，发动后**主公**摸一张。"""
    engine, players = _songwei_run(judge_card=_black_judge())

    queries = _activation_queries(engine, SONGWEI)
    assert len(queries) == 1, "黑色判定 ⇒ 问一次"
    assert queries[0].actor == 0, "这一问发给被判定者（决定者），不是曹丕（座位 1）"
    assert len(engine.state.player(1).hand) == 1, "受益者是主公（座位 1）"
    assert engine.state.player(0).hand == [], "答话的判定者不摸牌"
    assert players[0].activations, "判定者座位上确实收到这一问"


def test_songwei_plan_actor_is_the_judged_character():
    """票面 §3 的落点：`ActivationPlan.actor = frame.data["subject"]`（判定者）。"""
    engine, _players = _songwei_run(judge_card=_black_judge())

    query = _activation_queries(engine, SONGWEI)[0]
    assert query.actor == 0 and engine.state.turn_seat == 0, (
        "actor = 判定者 = 本回合角色（座位 0），不是技能持有者"
    )


def test_songwei_is_not_offered_for_a_red_judge_card():
    """颜色：红色判定 ⇒ `available=False`、连问都不发，主公不摸牌。"""
    engine, players = _songwei_run(judge_card=_red_judge())

    assert _activation_queries(engine, SONGWEI) == []
    assert engine.state.player(1).hand == []
    assert players[0].activations == []


def test_songwei_is_not_offered_for_another_kingdom():
    """势力：判定者不是魏 ⇒ 不问。"""
    engine, players = _songwei_run(
        judge_card=_black_judge(), judged_kingdom=Kingdom.WU
    )

    assert _activation_queries(engine, SONGWEI) == []
    assert engine.state.player(1).hand == []


def test_songwei_is_not_offered_for_the_lord_himself():
    """「**其他**魏势力角色」：主公自己判定 ⇒ 不问（L776②）。"""
    engine, players = _songwei_run(judge_card=_black_judge(), caopi_seat=0)

    assert _activation_queries(engine, SONGWEI) == [], "判定者是曹丕自己 ⇒ 不问"
    assert engine.state.player(0).hand == [], "也不摸牌"


def test_songwei_does_not_exist_without_the_lord_identity():
    """主公技的身份门（`_identity_gates_out`）：曹丕不是主公 ⇒ 实例根本不产出。"""
    engine, players = _songwei_run(judge_card=_black_judge(), lord=False)

    assert _activation_queries(engine, SONGWEI) == []
    assert engine.state.player(1).hand == []


# ---------------------------------------------------------------------------
# §5 颂威 —— ② 效果（收益归主公 / 放弃出口 / Q5 不限每回合）
# ---------------------------------------------------------------------------


def test_songwei_draws_for_the_lord_not_the_decider():
    """收益归 `skill_owner`（主公），不是答话的判定者（照界【护驾】②）。"""
    engine, _players = _songwei_run(judge_card=_black_judge(), caopi_hand=())

    assert len(engine.state.player(1).hand) == 1, "主公 +1"
    assert engine.state.player(0).hand == [], "决定者 +0"
    draws = _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    assert [len(record.cards) for record in draws] == [1], "恰好一张（恒 1，不是 X）"


def test_songwei_declining_draws_nothing():
    """「可以」= 每次都给放弃出口（L816）：判定者放弃 ⇒ 主公不摸牌。"""
    scripts = [Caopi(activate=()) for _ in range(3)]
    engine, _players = _songwei_run(judge_card=_black_judge(), scripts=scripts)

    assert len(_activation_queries(engine, SONGWEI)) == 1, "照问"
    assert engine.state.player(1).hand == []
    assert engine.state.player(0).hand == []


def test_songwei_is_offered_on_every_black_judge_not_once_per_turn():
    """**Q5 = A**：牌面没有「每回合限一次」⇒ 同一回合的两次黑色判定各问一次。

    座位 0 的判定区同时有【乐不思蜀】与【闪电】：判定阶段连续两次判定，牌堆顶两张都是
    黑牌 ⇒ 两次「是否发动」、主公摸两张。
    """
    lightning = _cards("lightning")[1]
    first = _black_judge(exclude=(lightning,))
    # 第二张判定牌取**梅花**：闪电只在"黑桃 2–9"时造成伤害，梅花恒"传给下家"⇒ 两次判定
    # 都只走 `JUDGE_CARD_AFTER_EFFECT`，不夹带伤害结算。
    second = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit == "club"
        and card_id not in {first, INDULGENCE, lightning}
    )
    # 牌堆序 = (第一张判定牌, 颂威第一次摸到的那张, 第二张判定牌, …)：第一次颂威的摸牌
    # 会消耗第二张判定牌，故中间必须垫一张 filler，否则第二次判定的颜色不可控。
    filler = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in {first, second, INDULGENCE, lightning}
    )
    engine, _players = _songwei_run(
        judge_card=first,
        judge_cards=(INDULGENCE, lightning),
        deck=(
            first,
            filler,
            second,
            *(
                card_id
                for card_id in range(len(PACK.deck.cards))
                if card_id not in {first, filler, second, INDULGENCE, lightning}
            ),
        ),
        stop=lambda e: _judge_pops(e) >= 2 or e.finished,
    )

    assert len(_activation_queries(engine, SONGWEI)) == 2, (
        "两次黑色判定 = 两次触发（不限每回合）"
    )
    assert len(engine.state.player(1).hand) == 2, "主公每次各摸一张"


# ---------------------------------------------------------------------------
# §5 颂威 —— ③ 改判 / 装备判定 / 重入 / 短摸（G7 / G10）
# ---------------------------------------------------------------------------


def test_songwei_reads_the_card_after_a_rejudge():
    """改判之后按**新牌**判颜色：原判红 ⇒ 改判成黑 ⇒ 颂威成立。"""
    red = _red_judge()
    black = _black_judge(exclude=(red,))
    scripts = [
        Caopi(activate=(SONGWEI,)),
        Caopi(activate=(SONGWEI,)),
        Caopi(rejudge_with=black),
    ]
    engine, _players = _songwei_run(
        judge_card=red,
        extra=(2, "simayi", (black,)),
        scripts=scripts,
    )

    assert _activation_queries(engine, SONGWEI), "改判成黑之后要问（按新牌判色）"
    assert len(engine.state.player(1).hand) == 1, "主公摸到一张"
    assert red in engine.state.discard, "被替代掉的原判红牌进弃牌堆"


def test_songwei_fires_on_an_equipment_judgement():
    """【八卦阵】的判定走同一条 `JUDGE_CARD_AFTER_EFFECT` ⇒ 颂威照常触发。"""
    slash = _cards("slash")[0]
    black = _black_judge(exclude=(slash, EIGHT_DIAGRAMS))
    seats = [
        SeatSpec(kingdom=Kingdom.WU, identity=Identity.REBEL, hand=(slash,)),
        SeatSpec(kingdom=Kingdom.WEI, identity=Identity.REBEL, equip=(EIGHT_DIAGRAMS,)),
        SeatSpec(hero=CAOPI, kingdom=Kingdom.WEI, identity=Identity.LORD),
    ]
    # 座位 0 的回合先跑**摸牌阶段**（额定 2 张）⇒ 前两张被摸走，第三张才是八卦阵的判定牌。
    fillers = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in {slash, EIGHT_DIAGRAMS, black}
    ][:2]
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=(
            *fillers,
            black,
            *(
                card_id
                for card_id in range(len(PACK.deck.cards))
                if card_id not in {slash, EIGHT_DIAGRAMS, black, *fillers}
            ),
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    scripts = [
        Caopi(slash_at=1),
        Caopi(bagua=True, activate=(SONGWEI,)),
        Caopi(activate=(SONGWEI,)),
    ]
    _advance(
        engine,
        scripts,
        lambda e: _judge_pops(e) >= 1 or e.finished,
    )

    assert ran_timing(engine, Timing.JUDGE_CARD_AFTER_EFFECT), "八卦阵的判定确实发生"
    assert _activation_queries(engine, SONGWEI), "装备判定同样触发颂威"
    assert len(engine.state.player(2).hand) == 1, "主公摸到一张"


def test_songwei_resumes_without_drawing_twice():
    """G7：同一判定帧被**再派发一轮** ⇒ 帧私有 `_SONGWEI_DRAWN_KEY` 挡住第二次摸牌。

    正常路径上摸牌不会挂起（主公没有「获得牌后」类技能），这个防御位就是为"宿主级直调"
    准备的。判据：清掉帧内的 pass 进度、逼引擎重新走一遍派发环 ⇒ 技能**重新被问到**，
    但答"发动"之后一张也不摸（游标已落位）。
    """
    scripts = [Caopi(activate=(SONGWEI,)) for _ in range(3)]
    # 停在"第一次颂威已经摸完牌、判定帧还压着"的那一刻（重入的前提）。
    engine, _players = _songwei_run(
        judge_card=_black_judge(),
        scripts=scripts,
        stop=lambda e: (bool(e.state.player(1).hand) and e.pending is None)
        or e.finished,
    )
    frame = next(
        candidate
        for candidate in reversed(engine.stack.frames)
        if candidate.kind is FrameKind.JUDGE
    )
    hand_before = list(engine.state.player(1).hand)
    assert len(hand_before) == 1, "第一次颂威已摸到一张"
    frame.data.pop(PASS_STATE_KEY, None)
    frame.data["ask_answers"] = {}
    assert engine_steps.judge_after_effect(engine, frame) == "stay", "重新走到「是否发动」"
    reask = engine.pending
    assert reask is not None and str(reask.skill_id) == SONGWEI
    engine.submit(
        next(
            action
            for action in reask.legal_actions
            if isinstance(action, ActivateSkill)
        )
    )

    assert engine.state.player(1).hand == hand_before, "第二次一张都不再摸"
    assert len(
        _moves_to(engine, to_zone=ZoneRef(Zone.HAND, 1), reason=MoveReason.DRAW)
    ) == 1, "整局只有第一次那一条摸牌 delta"


def test_songwei_short_draw_ends_in_a_draw():
    """G10：主公摸牌时两堆皆空 ⇒ 短摸即平局（判定牌还在处理区，不进任何一堆）。"""
    engine, _players = _songwei_run(
        judge_card=_black_judge(),
        deck=(_black_judge(),),
        discard=(),
        stop=lambda e: e.finished,
    )

    assert engine.finished is True
    assert engine.pending_outcome is not None
    assert engine.pending_outcome.result == "draw"


# ---------------------------------------------------------------------------
# §5 曹丕移动版 —— 形状 / 数据面
# ---------------------------------------------------------------------------


def test_caopi_mobile_is_the_wei_lord_of_the_mobile_lin_pack():
    """数据面：`heroes.lin_mobile.json` 那一行（3 体力 / 魏 / 男 / 颂威 = 主公技）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    hero = hero_by_id(CAOPI)
    assert hero is not None
    assert hero.name == "曹丕移动版"
    assert hero.true_name == "曹丕"
    assert hero.version == "lin-mobile"
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("wei", "male", 3)
    assert hero.enabled is True
    assert [(f"hero.{hero.id}.{skill.id}", skill.skill_type.value) for skill in hero.skills] == [
        (XINGSHANG, "NORMAL"),
        (FANGZHU, "NORMAL"),
        (SONGWEI, "LORD"),
    ], "颂威的数据侧声明 = 主公技（与代码侧 SkillDef 对账）"
    assert CAOPI in enabled_hero_ids(), "启用 ⇒ 进默认池（林包移动版按独立版本并存）"


def test_the_caopi_skills_have_their_declared_shapes():
    """形状：行殇 / 颂威 = 零步流水线（只问"是否发动"），放逐 = 单步选座流水线。"""
    for skill_id, requirement in (
        (XINGSHANG, XINGSHANG_REQUIREMENT),
        (FANGZHU, FANGZHU_REQUIREMENT),
        (SONGWEI, SONGWEI_REQUIREMENT),
    ):
        definition = CATALOG[skill_id]
        assert definition.locked is False, f"{skill_id}：牌面写「可以」⇒ 非锁定"
        assert definition.preactivated is False, "三条都不翻预发动（`shapes.md` §4 准入表）"
        assert definition.requirement == requirement
        assert flow_for(skill_id) is not None, f"{skill_id}：有 SkillFlow ⇒ 步骤语言内"
    assert flow_for(XINGSHANG).steps == (), "行殇：零步"
    assert flow_for(SONGWEI).steps == (), "颂威：零步"
    steps = flow_for(FANGZHU).steps
    assert [type(step).__name__ for step in steps] == ["PickSeats"] and steps[0].key == "seat"
    assert CATALOG[FANGZHU].step_requirements == {"seat": "选择一名其他角色"}
    assert CATALOG[SONGWEI].skill_type.value == "LORD"




