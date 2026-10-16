"""B 族：伤害后触发、逐点触发与【奸雄】/【反馈】/【遗计】（票 05）。

落点与票 05 §验收一致，全部走**外部缝**：

- **记录流**：``DAMAGE_AFTER_TAKE`` 的时机事件序列（逐点触发在记录流里就是同一帧里
  **多条** ``DAMAGE_AFTER_TAKE``，故"触发了几轮"读它最直接）；
- **询问缝**：``SKILL_ACTIVATION`` 的**次数**（【遗计】2 次、【奸雄】1 次）、
  ``SELECT_CARDS`` 的选择面（``query.slots`` 的隐藏槽 / 可见槽、``revealed`` 授予）；
- **终局状态**：手牌 / 装备 / 处理区 / 牌堆（"拿到的是哪张牌""牌去哪了"）。

**时点精确的局面这样造**：人为局面 + 直接压一个 ``FrameKind.DAMAGE`` 子帧（与引擎在
【杀】命中时压的形状逐字相同：``amount`` / ``target`` / ``source`` / ``card`` /
``channel``），再推进入栈。这使"受到 2 点伤害"是一个精确构造的事实，不必先跑一遍
【杀】与【闪】的窗口。

**判据怎么读**：''伤害被减为 0''读**帧标签**（:data:`~sgs.resolution.Tag.PREVENTED`，
【寒冰剑】类效果打的）；''逐点''读 ``DAMAGE_AFTER_TAKE`` 记录条数与技能询问条数；
''没泄露''读**别人**座位询问里的 ``view`` 与记录流里有没有带牌面的移动。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.resolution import Tag, add_tag
from sgs.state import (
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    SelectCards,
    SelectCardsQuery,
    UseCard,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_108")

JIANXIONG = "hero.caocao.jianxiong"
FANKUI = "hero.simayi.fankui"
YIJI = "hero.guojia.yiji"

DAMAGE_AFTER_TAKE = Timing.DAMAGE_AFTER_TAKE


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _advance(engine, players, stop) -> Engine:
    """逐步推进到 ``stop(engine)`` 成立（同 ``tests/test_hero_move_after.py`` 的手写推进）。"""
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


def _pass_or_first(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


class Scripted:
    """技能发动一律同意；``select``（``(query, round) -> Action``）应答**选牌与选座**询问。"""

    def __init__(self, select=None, *, activate: bool = True) -> None:
        self._select = select
        self.activate = activate
        self.activation_queries: list = []
        self.selections: list = []
        self.options: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            self.activation_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        if query.kind == "SELECT_CARDS" and self._select is not None:
            self.selections.append(query)
            return self._select(query, len(self.selections) - 1)
        # 「交给谁」走 `SELECT_OPTION`（见 `damage_after._yiji`）：也要能交回给测试的
        # 选座回调，否则它会落到下面的 `_pass_or_first` 上、静默选第一个座位。
        if query.kind == "SELECT_OPTION" and self._select is not None:
            self.options.append(query)
            return self._select(query, len(self.options) - 1)
        return _pass_or_first(query)


def _damage_frame(state, card: int | None, *, amount: int, source: int | None):
    """与引擎同形的 ``DAMAGE`` 子帧载荷。"""
    channel = state.card(card) if card is not None else None
    return {
        "source": source,
        "targets": (1,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": 1,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _damage_engine(
    *,
    target: SeatSpec | None = None,
    source_seat: SeatSpec | None = None,
    seats_after: int = 0,
    amount: int = 1,
    card: int | None = None,
    processing: tuple[int, ...] | None = None,
    source: int | None = 0,
    deck: tuple[int, ...] | None = None,
    prevented: bool = False,
    seed: int = 7,
):
    """压一个 ``DAMAGE`` 子帧并推进到它出栈；返回 ``(engine, players)``。

    座位 0 是伤害来源，座位 1 是被伤害者（``target`` 覆盖它的 ``SeatSpec``）。
    """
    if card is None:
        card = _cards("slash")[0]
    if processing is None:
        processing = (card,)
    specs = (
        [source_seat if source_seat is not None else SeatSpec()]
        + [target if target is not None else SeatSpec()]
        + [SeatSpec() for _ in range(seats_after)]
    )
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=(
            deck
            if deck is not None
            else _deck_excluding(card, *processing)
        ),
        processing=processing,
    )
    engine = Engine.from_state(state, seed=seed)
    players = [Scripted() for _ in range(len(specs))]
    frame = engine.push_frame(
        FrameKind.DAMAGE, _damage_frame(state, card, amount=amount, source=source),
        timing=Timing.DAMAGE_DEAL,
    )
    if prevented:
        add_tag(frame, Tag.PREVENTED)
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )
    return engine, players


def _timings(engine, timing: Timing) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is timing
    ]


def _activation_count(engine, skill_id: str) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == skill_id
    )


def _zones_of(state, card_id: int) -> list[tuple[str, int | None]]:
    """``card_id`` 现在落在哪些区（六区坐标）。"""
    found: list[tuple[str, int | None]] = []
    if card_id in state.deck:
        found.append(("deck", None))
    if card_id in state.processing:
        found.append(("processing", None))
    if card_id in state.discard:
        found.append(("discard", None))
    for player in state.players:
        if card_id in player.hand:
            found.append(("hand", player.seat))
        if card_id in player.equip:
            found.append(("equip", player.seat))
        if card_id in player.judge:
            found.append(("judge", player.seat))
    return found


def _total_cards(state) -> int:
    return (
        len(state.deck)
        + len(state.discard)
        + len(state.processing)
        + sum(len(p.hand) + len(p.equip) + len(p.judge) for p in state.players)
    )


def _pick_slot(query: SelectCardsQuery, predicate) -> SelectCards:
    """选 ``query.slots`` 里第一个满足 ``predicate`` 的槽位（隐藏槽 / 可见槽各一用）。"""
    index = next(i for i, slot in enumerate(query.slots) if predicate(slot))
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (index,)
    )


# ---------------------------------------------------------------------------
# ① 逐点触发（票 05 §验收 1）
# ---------------------------------------------------------------------------


def test_two_points_of_damage_ask_yiji_twice_and_jianxiong_once():
    """受到 2 点伤害 ⇒【遗计】被问 2 次（逐点）；【奸雄】只在第一轮有机会。"""
    card = _cards("slash")[0]
    engine, _players = _damage_engine(target=SeatSpec(hero="guojia"), amount=2, card=card)

    assert len(_timings(engine, DAMAGE_AFTER_TAKE)) == 1, "一条时机记录覆盖逐点派发"
    assert _activation_count(engine, YIJI) == 2, "【遗计】逐点 ⇒ 问 2 次"

    card = _cards("slash")[1]
    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), amount=2, card=card)

    assert len(_timings(engine, DAMAGE_AFTER_TAKE)) == 1
    assert _activation_count(engine, JIANXIONG) == 1, "非逐点 ⇒ 只在第一轮问一次"


def test_one_point_of_damage_asks_both_once():
    """受到 1 点伤害 ⇒ 两者各 1 次（``06`` §1 的关键验证）。"""
    engine, _players = _damage_engine(target=SeatSpec(hero="guojia"), amount=1)
    assert _activation_count(engine, YIJI) == 1

    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), amount=1)
    assert _activation_count(engine, JIANXIONG) == 1


# ---------------------------------------------------------------------------
# ② 伤害减为 0 ⇒ B 族一次都不触发（票 05 §验收 2）
# ---------------------------------------------------------------------------


def test_a_prevented_damage_never_triggers_the_family():
    """【寒冰剑】类"防止此伤害"⇒ 帧标签 ``PREVENTED`` ⇒ B 族一次都不问、牌不搬。

    时机记录本身照落一条（M2 的【寒冰剑】路径就是这样，改它会动既有基准），被挡掉的是
    **派发**——这正是"伤害为 0 即防止"的既有语义不被"受到伤害后"的时点绕开。
    """
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="caocao"), card=slash, prevented=True
    )

    assert _activation_count(engine, JIANXIONG) == 0, "B 族一次都不问"
    assert engine.state.processing == [slash], "防止伤害 ⇒【奸雄】不搬牌"
    assert engine.state.player(1).hand == []


# ---------------------------------------------------------------------------
# ③ 【奸雄】拿到的正是造成伤害的那张牌（票 05 §验收 3）
# ---------------------------------------------------------------------------


def test_jianxiong_takes_the_card_that_dealt_the_damage():
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), card=slash)

    assert _activation_count(engine, JIANXIONG) == 1
    assert slash in engine.state.player(1).hand, "牌从处理区进了曹操的手牌"
    assert engine.state.processing == []
    assert slash not in engine.state.discard, "不是被弃置，而是被获得"
    assert _zones_of(engine.state, slash) == [("hand", 1)]


def test_jianxiong_is_skipped_when_the_card_is_no_longer_in_the_processing_zone():
    """牌已被移走（【寒冰剑】类效果 / 已被别人获得）⇒ 跳过且不报错。"""
    slash = _cards("slash")[0]
    other = _cards("peach")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="caocao"), card=slash, processing=(other,)
    )

    assert engine.state.player(1).hand == []
    assert _activation_count(engine, JIANXIONG) == 0, "找不到那张牌 ⇒ 连问都不问"
    assert engine.state.processing == [other]


# ---------------------------------------------------------------------------
# ③b 【奸雄】要走完**真实使用链**才交付（本轮报障；上方三条只压 DAMAGE 帧，
#     照不到 USE 帧的 L5.6，那正是这个 bug 藏身处）
# ---------------------------------------------------------------------------


class _SlashUser:
    """出牌阶段对 ``target`` 使用手里那张【杀】（只用一次），随后立即结束阶段。

    "随后结束阶段"是刻意的：摸牌阶段已经补了两张牌，若不结束，通用脚本会接着打出它们，
    引入第二次伤害、第二次【奸雄】询问——那测的就不是这条链了。
    """

    def __init__(self, target: int) -> None:
        self._target = target
        self.used = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == "slash"
                        and self._target in (action.targets or ())
                    ):
                        self.used = True
                        return action
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        return _pass_or_first(query)


class _CaoCaoResponder:
    """曹操的应答器：【奸雄】按 ``activate`` 决定发动与否，其余交给通用脚本。"""

    def __init__(self, *, activate: bool) -> None:
        self.activate = activate
        self.asked = 0

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            if getattr(query, "skill_id", None) == JIANXIONG:
                self.asked += 1
                if self.activate:
                    for action in query.legal_actions:
                        if getattr(action, "kind", None) == "ActivateSkill":
                            return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return _pass_or_first(query)


def _jianxiong_full_use(*, activate: bool):
    """座位 0 对座位 1（曹操）使用一张真【杀】，跑完**整条使用链**直到本回合结束。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hero="caocao")],
        deck=_deck_excluding(slash),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=7)
    caocao = _CaoCaoResponder(activate=activate)
    players = [_SlashUser(1), caocao]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        ),
    )
    return engine, caocao, slash


def _slash_moves(engine, slash: int) -> list[tuple[Zone, Zone]]:
    return [
        (record.from_zone.zone, record.to_zone.zone)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and slash in [card.card_id for card in record.cards]
    ]


def test_jianxiong_keeps_the_card_through_the_whole_use_chain():
    """报障原文：「有时候奸雄发动但是无法获得牌」。

    上方三条只压一个 ``DAMAGE`` 子帧，照不到外层 ``USE`` 帧的 L5.6。真实对局里
    【杀】的实体牌在 ``use_after_end`` 才离开处理区，而 :meth:`Engine.move_processing_card_to_discard`
    当时**不核牌还在不在处理区**：牌已被【奸雄】搬进曹操手里，它仍照着"处理区 → 弃牌堆"
    再记一条 ``hand(曹操) → discard`` 的移动——发动了，牌却没了。

    判据：整条使用链走完，那张【杀】在曹操手里、且**没有**一条把它从曹操手里搬走的移动。
    """
    engine, caocao, slash = _jianxiong_full_use(activate=True)

    assert caocao.asked == 1, "【奸雄】被问了一次"
    assert slash in engine.state.player(1).hand, "使用链走完，那张【杀】必须仍在曹操手里"
    assert slash not in engine.state.discard, "L5.6 不得把已被【奸雄】拿走的牌再弃掉"
    assert _slash_moves(engine, slash) == [
        (Zone.HAND, Zone.PROCESSING),
        (Zone.PROCESSING, Zone.HAND),
    ], "牌的轨迹只该是 手牌→处理区→曹操手牌"


def test_without_jianxiong_the_slash_still_goes_to_discard():
    """正面对照：不发动【奸雄】时，【杀】照旧在"使用结算结束后"进弃牌堆。"""
    engine, caocao, slash = _jianxiong_full_use(activate=False)

    assert caocao.asked == 1, "问过一次（答了不发动）"
    assert slash in engine.state.discard
    assert slash not in engine.state.player(1).hand
    assert _slash_moves(engine, slash) == [
        (Zone.HAND, Zone.PROCESSING),
        (Zone.PROCESSING, Zone.DISCARD),
    ]


# ---------------------------------------------------------------------------
# ④ 【反馈】的选取面：来源为空不进候选；手牌隐藏槽 + 装备可见槽（票 05 §验收 4）
# ---------------------------------------------------------------------------


def test_fankui_is_not_offered_when_the_damage_has_no_source():
    engine, _players = _damage_engine(
        target=SeatSpec(hero="simayi"), card=_cards("slash")[0], source=None
    )

    assert _activation_count(engine, FANKUI) == 0


def test_fankui_is_not_offered_when_the_source_has_no_cards():
    engine, _players = _damage_engine(target=SeatSpec(hero="simayi"), source=0)

    assert _activation_count(engine, FANKUI) == 0, "来源无牌可拿 ⇒ 不进候选"


def test_fankui_offers_the_sources_hand_hidden_and_equip_visible():
    """来源有手牌 + 装备 ⇒ 选取面覆盖两区；手牌槽 ``card is None``、装备槽可见。"""
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0], _cards("peach")[1])
    source_equip = (_cards("crossbow")[0],)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand, equip=source_equip),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand, *source_equip),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        return _pick_slot(query, lambda slot: slot.card is None)

    players = [Scripted(), Scripted(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert _activation_count(engine, FANKUI) == 1
    assert len(players[1].selections) == 1, "【反馈】只问一次选牌"
    query = players[1].selections[0]
    hidden = [slot for slot in query.slots if slot.card is None]
    visible = [slot for slot in query.slots if slot.card is not None]

    assert len(hidden) == 2, "来源的两张手牌是隐藏槽（只有位置）"
    assert all(slot.ref.zone is Zone.HAND for slot in hidden)
    assert len(visible) == 1, "来源的装备是可见槽"
    assert visible[0].card.card_id == source_equip[0]
    assert visible[0].ref == ZoneRef(Zone.EQUIP, 0)
    owner_hand = list(engine.state.player(1).hand)
    assert len(owner_hand) == 1, "选中隐藏槽并结算后才揭晓：那张手牌进了司马懿手里"
    assert owner_hand[0] in source_hand
    remaining = [card_id for card_id in source_hand if card_id != owner_hand[0]]
    assert sorted(engine.state.player(0).hand) == sorted(remaining)
    assert engine.state.player(0).equip == list(source_equip), "装备没被动过"


def test_fankui_can_take_a_visible_equipment_card():
    """选取面覆盖两区：也能拿走**装备区**那张可见牌。"""
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0],)
    source_equip = (_cards("crossbow")[0],)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand, equip=source_equip),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand, *source_equip),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        return _pick_slot(query, lambda slot: slot.card is not None)

    players = [Scripted(), Scripted(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert source_equip[0] in engine.state.player(1).hand
    assert engine.state.player(0).equip == []


# ---------------------------------------------------------------------------
# ④b 【反馈】必须自校持有者（票 13；用户实机报障）
#
# 夹具约定：座位 0 = 伤害来源、座位 1 = 挨打者、**座位 2 = 司马懿**——
# 受伤的那个人不是持有者。旧条件读 ``frame.data.get("skill_owner")``（条件求值期还没写）
# ⇒ 判据退化成"来源有牌"，于是座位 2 的实例也会被问"是否发动"，答发动后**真去拿来源的牌**。
# ---------------------------------------------------------------------------


def _foreign_damage_with_simayi(*, equip: tuple[int, ...] = ()):
    """座位 0 打座位 1；司马懿（持【反馈】）在座位 2，手里有牌的是**来源**。

    返回 ``(engine, players, source_hand)``：来源的手牌固定两张
    （覆盖"隐藏槽"与"装备可见槽"两种选取面），装备由 ``equip`` 给。
    """
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0], _cards("peach")[1])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand, equip=equip),
            SeatSpec(),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand, *equip),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    players = [Scripted(), Scripted(), Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )
    return engine, players, source_hand


def test_foreign_damage_does_not_offer_fankui_to_the_unhurt_owner():
    """**别人受伤 ⇒ 司马懿不该被问【反馈】**（票 13；用户原话「任何角色受到伤害都会触发」）。

    判据：受伤的是座位 1、装【反馈】的是座位 2（司马懿），且**来源手里有牌**
    ⇒ 一次"是否发动"都不该问。旧条件在条件求值期读 ``skill_owner``（那时还没写），
    退化成"伤害有来源且有牌"，这条就会变红。
    """
    engine, _players, source_hand = _foreign_damage_with_simayi()

    assert _activation_count(engine, FANKUI) == 0, (
        "受伤的是座位 1、装【反馈】的是座位 2 ⇒ 不该问；问了就说明触发条件没自校持有者"
    )
    # 那 4 条既有【反馈】用例断言的事一件也没发生（见下一条）。
    assert source_hand and all(card in engine.state.player(0).hand for card in source_hand)


def test_foreign_damage_moves_no_card_for_fankui():
    """**别人受伤时，那 4 条既有【反馈】用例断言的事一件也不发生**（票 13）。

    四条既有用例分别钉：无来源不询问 / 来源无牌不询问 / 隐藏手牌槽 + 结算后进司马懿手 /
    装备可见槽可拿。这里把"来源有手牌 + 有装备"的最好条件摆出来，但受伤的不是司马懿：

    - 没有 SKILL_ACTIVATION 询问（对应前两条"不进候选"）；
    - 司马懿一张选牌询问都不发（对应后两条的选取面）；
    - 来源的手牌与装备**原封不动**（旧行为会把牌真搬进司马懿手里）。
    """
    equip = (_cards("crossbow")[0],)
    engine, players, source_hand = _foreign_damage_with_simayi(equip=equip)

    assert _activation_count(engine, FANKUI) == 0
    assert players[2].selections == [], "没有『是否发动』就没有后续选牌询问"
    assert sorted(engine.state.player(0).hand) == sorted(source_hand), "来源的手牌一张不少"
    assert engine.state.player(0).equip == list(equip), "来源的装备一件不少"
    assert engine.state.player(2).hand == [], "司马懿没有拿到任何牌"


# ---------------------------------------------------------------------------
# ⑤ 【遗计】看牌堆顶 2 张：不泄露、交给后牌守恒（票 05 §验收 5）
# ---------------------------------------------------------------------------


def _yiji_give_to(owner_seat: int):
    """在【遗计】"交给谁"的 ``SELECT_OPTION`` 询问里选 ``owner_seat``。"""

    def choose(query):
        wanted = f"seat:{owner_seat}"
        for action in query.legal_actions:
            if getattr(action, "choice", None) == wanted:
                return action
        raise AssertionError(
            f"候选里没有 {wanted}：{[getattr(a, 'choice', None) for a in query.legal_actions]}"
        )

    return choose


def _yiji_select(query, _round_index):
    """【遗计】的应答：每轮把面内**第一张**交给郭嘉自己（一轮一张）。

    一轮有**两问**：先选牌（``SELECT_CARDS``）、再选"交给谁"（``SELECT_OPTION``）。
    """
    if query.kind == "SELECT_OPTION":
        return _yiji_give_to(1)(query)   # 座位 1 = 郭嘉（本文件的遗计夹具）
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (0,)
    )


def test_yiji_sees_the_top_two_cards_and_gives_them_away_without_leaking_them():
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    players = [Scripted(), Scripted(_yiji_select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert _activation_count(engine, YIJI) == 1, "1 点伤害 ⇒ 只问一次"
    assert len(players[1].selections) == 2, "一次触发里两张亮牌**各选一次**"
    first, second = players[1].selections

    assert tuple(card.card_id for card in first.revealed) == top, "授予牌堆顶 2 张（自顶向下）"
    assert first.view.deck_count == len(deck), "授予不改牌堆张数"
    assert all(slot.card is not None for slot in first.slots), "观看的牌面在面内可见"
    assert tuple(card.card_id for card in second.revealed) == (top[1],), (
        "第二轮铺的是**本点剩余的那张亮牌**，不重新观看牌堆顶"
    )
    revealed_queries = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.revealed
    ]
    assert revealed_queries == [first, second], "牌面只出现在被授予的那两次选牌询问里"
    # **观看本身不产 delta**：授予的牌面只在上面那两次询问的 ``revealed`` 里；带牌面的
    # ``card_move`` 只有"交给"那两条（观看不搬牌）。
    moved = [
        tuple(card.card_id for card in record.cards)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and set(card.card_id for card in record.cards) & set(top)
    ]
    assert moved == [(top[0],), (top[1],)], "观看不搬牌；两张各有一条'交给'移动"

    assert engine.state.player(1).hand == [top[0], top[1]], "两张都进了郭嘉手里（结算后揭晓）"
    assert engine.state.deck == list(deck[2:]), "被观看的牌离开牌堆顶，其余顺序不变"
    assert _total_cards(engine.state) == len(PACK.deck.cards), "牌守恒仍成立"


def test_declining_yiji_leaves_the_deck_untouched():
    """``Pass`` = 这张不交给任何人：一个钩子都不搬牌、牌堆不动。"""
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def decline(query, _round_index):
        return next(
            action for action in query.legal_actions if isinstance(action, Pass)
        )

    players = [Scripted(), Scripted(decline)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(1).hand == []
    assert engine.state.deck == list(deck), "答 Pass ⇒ 牌堆一张不动"
    assert _total_cards(engine.state) == len(PACK.deck.cards)


def test_yiji_sees_a_fresh_top_two_on_each_damage_point():
    """逐点 ⇒ 每一轮各看一次牌堆顶（上一轮拿走的牌不再出现），且每点两张都发得出去。"""
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=2, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    players = [Scripted(), Scripted(_yiji_select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert len(players[1].selections) == 4, "2 点伤害 × 每点两张 ⇒ 四次选牌"
    first_a, first_b, second_a, second_b = players[1].selections
    assert tuple(card.card_id for card in first_a.revealed) == deck[:2]
    assert tuple(card.card_id for card in first_b.revealed) == (deck[1],), "第一点的第二张"
    assert tuple(card.card_id for card in second_a.revealed) == deck[2:4], "第二点看新的牌堆顶"
    assert tuple(card.card_id for card in second_b.revealed) == (deck[3],), "第二点的第二张"
    assert engine.state.player(1).hand == [deck[0], deck[1], deck[2], deck[3]]
    assert _total_cards(engine.state) == len(PACK.deck.cards)


# ---------------------------------------------------------------------------
# ⑥ 濒死优先于"受到伤害后"（票 05 §验收 6）
# ---------------------------------------------------------------------------


def test_near_death_timings_precede_damage_after_take_in_the_record_stream():
    """一次打到 0 体力的伤害：``NEAR_DEATH_*`` 三个时机都排在 ``DAMAGE_AFTER_TAKE`` 之前。

    濒死窗口里由**受伤者自己出【桃】**救回 1 点体力——这样伤害链才走得到"受到伤害后"，
    否则整条链在终局清栈时被丢弃（死亡路径，见 ``test_death.py``），时序无从观察。
    """
    slash = _cards("slash")[0]
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(),
            SeatSpec(hp=1, hp_max=4, hero="caocao", hand=(peach,)),
        ],
        deck=_deck_excluding(slash, peach),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )

    def select(query, _round):
        del query
        raise AssertionError("本局不该有选牌询问")

    class Rescuer(Scripted):
        def respond(self, query):
            if query.kind == "PEACH":
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "UseCard":
                        return action
            return super().respond(query)

    players = [Scripted(), Rescuer(select)]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(1).hp == 1, "被救回 1 点，伤害链走完"
    order = [
        record.timing
        for record in engine.records
        if record.kind == "timing"
        and record.timing
        in (
            Timing.NEAR_DEATH_ENTER,
            Timing.NEAR_DEATH_AT,
            Timing.NEAR_DEATH_AFTER,
            DAMAGE_AFTER_TAKE,
        )
    ]

    assert order[-1] is DAMAGE_AFTER_TAKE, "受到伤害后排在濒死之后"
    assert order[:3] == [
        Timing.NEAR_DEATH_ENTER,
        Timing.NEAR_DEATH_AT,
        Timing.NEAR_DEATH_AFTER,
    ]
    assert _activation_count(engine, JIANXIONG) == 1


# ---------------------------------------------------------------------------
# ①b 伤害后技能必须**自校持有者**（本轮用户报障；`CODING_STANDARDS.md` 规则 1 的落点）
# ---------------------------------------------------------------------------


def test_foreign_damage_does_not_offer_yiji_to_the_unhurt_owner():
    """**别人受伤 ⇒ 郭嘉不该被问【遗计】**。

    报障原文：「遗计的发动条件似乎是场上任一角色受到伤害」。核实为真：``_dispatch``
    遍历**每个存活玩家**的实例，只按 ``timing`` / ``per_point`` / ``condition`` 过滤
    （`catalog.py` 的派发环），而 ``_yiji_condition`` 当时恒返回 ``True``——
    于是任何人挨打，郭嘉都会被问一次，而【遗计】的回调会把牌堆顶的牌**移动给郭嘉**：
    等于"别人挨打、郭嘉白拿牌"。

    判据：手里装【遗计】的是**座位 0**，挨打的是**座位 1** ⇒ 一次都不该问。
    这条判据对**所有**伤害后技能成立（【奸雄】【反馈】同缺口，另案处理）。
    """
    # 夹具的座位约定：座位 0 = 伤害来源，座位 1 = 被伤害者（`target` 覆盖**座位 1**）。
    # 故郭嘉装在**座位 1 = 挨打的那个**，另需一个"别人挨打"的局面 ⇒ 让郭嘉装在来源座位 0。
    # 注意：武将必须在**建局时**装上；跑完再写 `hero` 不会解析出技能实例，那样的断言会假绿。
    engine, _players = _damage_engine(target=SeatSpec(), source_seat=SeatSpec(hero="guojia"), amount=1)

    assert _activation_count(engine, YIJI) == 0, (
        "受伤的是座位 1、装【遗计】的是座位 0（郭嘉）⇒ 不该问；"
        "问了就说明触发条件没自校持有者"
    )


def _yiji_select_then_seat(seat: int):
    """【遗计】的两问应答：先选面内第一张，再选把这张交出去的座位。"""

    def respond(query, round_index):
        if query.kind == "SELECT_OPTION":
            return _yiji_give_to(seat)(query)
        return next(
            action
            for action in query.legal_actions
            if isinstance(action, SelectCards) and action.picks == (0,)
        )

    return respond


def test_yiji_gives_the_card_to_the_chosen_character_not_always_the_owner():
    """【遗计】"将这些牌**交给任意角色**"（规则 993 / `三国杀详细规则.txt:958`）。

    报障原文：「也没法选择座次交给别人」。核实为真：现行实现一轮只问"选面内哪一张"，
    然后 ``move_in_frame(..., to_zone=ZoneRef(Zone.HAND, owner))``——**去向被写死成郭嘉自己**，
    "交给谁"这一步根本不存在。

    判据：把牌交给**另一个角色**，那张牌必须落进**他的**手牌、而不是郭嘉的。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    giver = 1      # 郭嘉（持有者）
    receiver = 2   # 另一个角色：牌该交给他
    players = [Scripted(), Scripted(_yiji_select_then_seat(receiver)), Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert top[0] in engine.state.player(receiver).hand, (
        "交出去的牌必须落进被选中的角色手里"
    )
    assert top[0] not in engine.state.player(giver).hand, "不该还给持有者"


def _yiji_plan(picks: list[int], seats: list[int]):
    """按计划应答【遗计】的逐轮两问：``picks[i]`` 选面内第几张，``seats[i]`` 交给谁。

    ``Scripted`` 传进来的 ``round_index`` 是**每类询问各自**的出现序号（选牌与选座各数各的），
    故两个计划各按自己的下标取。
    """

    def respond(query, round_index):
        if query.kind == "SELECT_OPTION":
            return _yiji_give_to(seats[round_index])(query)
        wanted = picks[round_index]
        return next(
            action
            for action in query.legal_actions
            if isinstance(action, SelectCards) and action.picks == (wanted,)
        )

    return respond


def test_yiji_distributes_both_revealed_cards_within_one_damage_point():
    """一次受伤触发亮 2 张 ⇒ **两张都要能各自选座交出**（本轮报障）。

    报障原文：「展示的两张牌只能选择1张获得」。核实为真：``_yiji`` 一轮只处理一张，
    下一轮的"观看"由**下一个伤害点**驱动——1 点伤害只有一轮，故第二张永远发不出去；
    2 点伤害则第二轮**重新观看牌堆顶**（第一张已被移走）⇒ 两张亮牌被换成另一对。

    判据：1 点伤害、亮出 ``top[0]`` / ``top[1]``；第一张交给座位 0，第二张交给座位 2。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    yiji_player = Scripted(_yiji_plan(picks=[0, 0], seats=[0, 2]))
    players = [Scripted(), yiji_player, Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert top[0] in engine.state.player(0).hand, "第一张应交给座位 0"
    assert top[1] in engine.state.player(2).hand, (
        "第二张（仍是这次亮出的那张）必须也能交出——只能交一张说明一轮只处理一张"
    )
    assert top[0] not in engine.state.player(1).hand
    assert top[1] not in engine.state.player(1).hand

    faces = [[slot.card.name for slot in query.slots] for query in yiji_player.selections]
    assert len(faces) == 2, f"一次 1 点伤害应有**两轮**选牌（两张各一次），实得 {faces}"
    assert faces[1] == [engine.state.card(top[1]).name], (
        f"第二轮的选择面必须是**剩余的那张亮牌**，而不是重新观看牌堆顶：{faces[1]}"
    )


def test_yiji_rounds_in_one_damage_point_do_not_share_the_seat_answer():
    """同一点内的两轮"交给谁"各有各的缓存键 ⇒ 第 2 轮不会读回第 1 轮的座位答案（票 11）。

    ``ask_option`` 的答案缓存键是 ``(skill_id, actor)``：两轮若同名，第 2 轮会**直接命中**
    第 1 轮那条 ``SelectOption``（不重发询问）⇒ 两张牌都交给第一个人。故手写回调把
    ``skill_id`` 写成 ``yiji:give:<点号>:<轮号>``（票 05 的逐轮缓存键）。这里把**真实询问
    里的键形状**与"两张牌各自去向"绑在同一处断言里。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    yiji_player = Scripted(_yiji_plan(picks=[0, 0], seats=[0, 2]))
    players = [Scripted(), yiji_player, Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert [query.skill_id for query in yiji_player.options] == ["yiji:give:0:0", "yiji:give:0:1"], (
        "同一点的两轮'交给谁'必须各有各的缓存键（点号 + 轮号），否则第 2 轮读回第 1 轮的答案"
    )
    assert top[0] in engine.state.player(0).hand, "第 1 轮：交给座位 0"
    assert top[1] in engine.state.player(2).hand, (
        "第 2 轮的座位答案（2）必须被采纳，而不是读回第 1 轮的答案（0）"
    )


# ---------------------------------------------------------------------------
# ⑦ 【遗计】一次交出两张 / 一张一交（票 15：用户口径"允许同时选择两张牌"）
# ---------------------------------------------------------------------------


def _yiji_pick_slots(count: int, receiver: int = 2):
    """一次在面里选中前 ``count`` 个槽位，并把这一批**一起**交给 ``receiver``。

    两个问法各应答一次（选牌 → 给谁），正是"一次交出两张"的形状：若实现退回"一轮一张"，
    选牌面里就**没有** ``picks=(0, 1)`` 这条候选，本应答器会当场炸掉（红）。
    """

    def respond(query, _round_index):
        if query.kind == "SELECT_OPTION":
            return _yiji_give_to(receiver)(query)
        picks = tuple(range(count))
        return next(
            action
            for action in query.legal_actions
            if isinstance(action, SelectCards) and action.picks == picks
        )

    return respond


def test_yiji_two_cards_can_be_picked_together_and_given_to_one_character():
    """**一次交出两张**：选牌面允许 ``picks=(0, 1)``，两张**一次移动**给同一名角色。

    用户口径（`acceptance.md` §C）："一次给出这两张牌时就需要**允许同时选择两张牌**
    （目前不行）"。故判据是三条：面内有那条双选候选、两张各只有**一条**移动记录
    （分两次给是**操作路径**，不是规则手续）、且两张都落进**同一名**角色手里。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    giver, receiver = 1, 2
    players = [Scripted(), Scripted(_yiji_pick_slots(2)), Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(receiver).hand == [top[0], top[1]], (
        "一次交出的两张必须**一起**落进被选中的那一座"
    )
    assert engine.state.player(giver).hand == [], "交出去就不留在持有者手里"
    assert engine.state.deck == list(deck[2:]), "两张亮牌都离开牌堆顶"
    moves = [
        tuple(card.card_id for card in record.cards)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and set(card.card_id for card in record.cards) & set(top)
    ]
    assert moves == [(top[0], top[1])], (
        f"两张只该有一条'交给'移动（同一名角色一次给）：{moves}"
    )


def test_yiji_one_card_at_a_time_can_choose_a_different_character_each_round():
    """**一张一交**：第一轮只选一张交给座位 0，第二轮把剩下的那张交给座位 2。

    与上一条共用同一段实现，故这里额外钉两件"别弄丢"的旧行为：两轮的面各是
    "本点剩余亮牌"，且两轮的"交给谁"答案不串味（票 11 的逐轮缓存键）。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    yiji_player = Scripted(_yiji_plan(picks=[0, 0], seats=[0, 2]))
    players = [Scripted(), yiji_player, Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert top[0] in engine.state.player(0).hand, "第一轮：交给座位 0"
    assert top[1] in engine.state.player(2).hand, "第二轮：剩下的那张交给**另一名**角色（座位 2）"
    assert engine.state.player(1).hand == [], "两张都交出去了"
    assert [tuple(card.card_id for card in query.revealed) for query in yiji_player.selections] == [
        top,
        (top[1],),
    ], "第一轮看两张、第二轮只看剩余的那张（不重新观看牌堆顶）"


# ---------------------------------------------------------------------------
# ⑧ 【遗计】私密观看：牌面只授予**技能拥有者**（票 15：其它视角 / 公开面都不得出现）
# ---------------------------------------------------------------------------


def test_yiji_watched_cards_reach_only_the_owner_view_and_stay_out_of_the_public_surface():
    """**私密观看**：观看不写任何视角快照 —— 其它座位读不出这两张，持有者拿在手里。

    用户口径（`acceptance.md` §C）："仅此技能拥有者观看，其他人不可见"。引擎侧的通道是
    询问的 ``revealed`` 授予（不写 ``PlayerView``、不落带牌面的 delta），故两条判据：
    其它座位的视角快照里**读不出**这两张（它们在牌堆顶，只有数量）；带牌面的移动只有
    "交给"那一条，且"观看"本身零搬牌。**"授予"这一半由谁看得见**（别人的载荷必须抹掉
    牌面）由接入层的 `tests/test_web.py::test_the_watched_cards_are_private_to_the_pending_seat`
    钉。
    """
    slash = _cards("slash")[0]
    deck = _deck_excluding(slash)
    top = deck[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        deck=deck,
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    giver = 1
    players = [Scripted(), Scripted(_yiji_select), Scripted()]
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    assert engine.state.player(giver).hand == [top[0], top[1]], "持有者拿在手里（观看后揭晓）"
    # ① 其它座位的视角快照：只读得出牌堆**数量**，读不出这两张亮牌。
    for seat in (0, 2):
        view = project(engine.state, seat)
        visible = _view_card_ids(view)
        assert not (visible & set(top)), (
            f"座位 {seat} 的视角里不该出现观看的牌面："
            f"{[engine.state.card(c).name for c in visible & set(top)]}"
        )
    # ② 公开记录流：观看**零搬牌**——带这两张牌面的移动只有"交给"那两条。
    watched_moves = [
        tuple(card.card_id for card in record.cards)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and set(card.card_id for card in record.cards) & set(top)
    ]
    assert watched_moves == [(top[0],), (top[1],)], (
        f"观看不搬牌；两张各有一条'交给'移动：{watched_moves}"
    )


def _view_card_ids(view) -> set[int]:
    """``PlayerView`` 里所有**带牌面**的 ``CardRef`` 的 id（自己手牌 + 公开区）。"""
    refs = list(view.self_hand) + list(view.discard) + list(view.processing)
    for player in view.players:
        refs += list(player.equip) + list(player.judge)
    return {int(ref.card_id) for ref in refs}


def test_foreign_damage_does_not_offer_jianxiong_to_the_unhurt_owner():
    """**别人受伤 ⇒ 曹操不该被问【奸雄】**（与【遗计】同一个缺口）。

    派发环会给**每个存活玩家**的实例求条件，而 ``_jianxiong_condition`` 只判"渠道牌还在
    处理区"，没判"受伤的是不是曹操"——于是任何人挨打，曹操都能顺手把那张牌拿走。
    同族的【反馈】没有这个缺口：它自带"来源不是自己"的守卫（``_fankui_source``）。
    """
    engine, _players = _damage_engine(target=SeatSpec(), source_seat=SeatSpec(hero="caocao"), amount=1)

    assert _activation_count(engine, JIANXIONG) == 0, (
        "受伤的是座位 1、装【奸雄】的是座位 0（曹操）⇒ 不该问"
    )
