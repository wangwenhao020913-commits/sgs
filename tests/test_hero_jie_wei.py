"""M6 票 14：界·魏 A（界曹操 / 界司马懿 / 界张辽 / 界许褚）。

六条技能，逐条走**外部缝**（记录流 / 询问缝 / 终局状态），与各族的既有测试同一姿态：

=====================  ==============================================================
技能                     牌面（§十九 L2028–L2055）与落点
=====================  ==============================================================
界曹操【奸雄】           受到伤害后获得造成伤害的牌**并摸一张牌**（标准版只拿牌）
界曹操【护驾】①         与标准【护驾】逐字相同（求助窗口，候选提供者）
界曹操【护驾】②         **每回合限一次**，其他魏势力角色于其回合外使用 / 打出【闪】时，
                        **其**可以令主公摸一张牌 —— 决定者不是受益者
界司马懿【反馈】         "当你受到 **1 点** 伤害后" ⇒ 逐点触发（标准版每次伤害事件一次）
界司马懿【鬼才】         "打出一张牌" ⇒ 改判窗口候选含装备区（标准版"一张手牌"只有手牌）
界张辽【突袭】           "少摸**任意**张，然后获得**等量名**其他角色的各一张手牌"
界许褚【裸衣】           亮出牌堆顶三张 → 可获得其中的基本牌 / 武器牌 /【决斗】；
                        若如此做，放弃摸牌，且**直到你的下个回合开始**基础伤害 +1
=====================  ==============================================================

三条**裁定**（写在代码里，本票不写回文档——写回是票 21 的活）：

1. 【裸衣】的"亮出"走 ``peek_deck`` +  ``card_reveal``：牌**不移动**，没拿走的自然留在
   牌堆顶；"若如此做"的判据 = **真的拿到了至少一张**（一张都没有 ⇒ 照常摸两张、不加伤）。
2. 【护驾】②的"每回合限一次"= **整回合只触发一次**（不是每个盟友各一次；用户 2026-11-10
   裁定），与【激将】② 同一条口径。与【清俭】同姿态：**真的发动了**才落位，决定者放弃
   不算——放弃不消耗这一回合的名额，后续盟友照样会被问。
3. 【护驾】②"于其回合外"这一条在正常牌流里几乎造不出来（闪的响应者通常不是当前回合
   角色），故它的反例用**条件直探**（合成帧）钉住，其余反例都走真实询问流。

**AI 覆盖**（spec §AI / D16 / S7）住 ``tests/test_heuristic_ai.py`` 的本票小节：四条
可被 AI 主动发动的技能各有一条确定性场景；【鬼才】不是"是否发动"询问（改判窗口直接问
"打不打一张牌"），由本文件的构造局面覆盖。
"""

from __future__ import annotations

from sgs.data import Color, Identity, Kingdom, color_of_suit, load_content_pack
from sgs.engine import Engine
from sgs.skills import (
    CATALOG,
    REJUDGE_SKILL_IDS,
    mark_skill_used_this_turn,
    rejudge_capable_seats,
    rejudge_card_zones,
    reset_turn_skill_state,
)
from sgs.skills.catalog import MATCH_OWNER_KEY
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    PlayCard,
    SubstituteJudgeCard,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_JIANXIONG = "hero.jie_caocao.jianxiong"
JIE_HUJIA = "hero.jie_caocao.hujia"
JIE_FANKUI = "hero.jie_simayi.fankui"
JIE_GUICAI = "hero.jie_simayi.guicai"
JIE_TUXI = "hero.jie_zhangliao.tuxi"
JIE_LUOYI = "hero.jie_xuchu.luoyi"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _run(engine, players, stop):
    return run_queries(engine, players, stop)


def _pass_or_first(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


class Scripted:
    """``handler(query) -> Action | None`` 先跑；返回 ``None`` 时再按最小规则应答。

    「是否发动」缺省一律同意（``activate=False`` 时一律放弃），故同一个脚本类既当
    伤害后技能的发动者、又当选牌 / 改判窗口的应答者。
    """

    def __init__(self, handler=None, *, activate: bool = True) -> None:
        self._handler = handler
        self.activate = activate
        self.activation_queries: list = []

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        if query.kind == "SKILL_ACTIVATION":
            self.activation_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return _pass_or_first(query)


def _kind_queries(engine, skill_id: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == skill_id
    ]



# ---------------------------------------------------------------------------
# 伤害后技能共用：与引擎同形的 DAMAGE 子帧 + 手写推进（与 test_hero_damage_after 同姿态）
# ---------------------------------------------------------------------------


def _damage_frame(state, card, *, amount: int, source: int | None):
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
    target: SeatSpec,
    source_seat: SeatSpec | None = None,
    amount: int = 1,
    card: int | None = None,
    processing: tuple[int, ...] | None = None,
    source: int | None = 0,
    deck: tuple[int, ...] | None = None,
    select=None,
):
    """座位 0 是伤害来源、座位 1 是被伤害者（``target`` 覆盖它的声明）。"""
    if card is None:
        card = _cards("slash")[0]
    if processing is None:
        processing = (card,)
    specs = [source_seat if source_seat is not None else SeatSpec(), target]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=deck if deck is not None else deck_excluding(PACK, card, *processing),
        processing=processing,
    )
    engine = Engine.from_state(state, seed=7)
    players = [Scripted(handler=select) for _ in specs]
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, card, amount=amount, source=source),
        timing=Timing.DAMAGE_DEAL,
    )
    _run(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )
    return engine, players


# ---------------------------------------------------------------------------
# ① 界曹操【奸雄】：获得造成伤害的牌**并摸一张牌**（§十九 L2029）
# ---------------------------------------------------------------------------


def test_jie_jianxiong_takes_the_channel_card_and_draws_one():
    """正例：拿牌 + 多摸一张（标准版只拿牌，摸的那张是牌堆顶）。"""
    slash = _cards("slash")[0]
    deck = deck_excluding(PACK, slash)
    engine, _players = _damage_engine(
        target=SeatSpec(hero="jie_caocao"), card=slash, deck=deck
    )

    assert len(_kind_queries(engine, JIE_JIANXIONG)) == 1
    assert engine.state.player(1).hand == [slash, deck[0]], "获得造成伤害的牌并摸一张牌"
    assert engine.state.processing == []
    assert deck[0] not in engine.state.deck, "摸走的那张已经不在牌堆"


def test_standard_jianxiong_only_takes_the_card():
    """对照：同一局面下标准曹操不多摸（界版多出来的正是"并摸一张牌"这一句）。"""
    slash = _cards("slash")[1]
    engine, _players = _damage_engine(target=SeatSpec(hero="caocao"), card=slash)

    assert engine.state.player(1).hand == [slash]


def test_jie_jianxiong_still_draws_one_when_there_is_no_channel_card():
    """渠道牌拿不到（伤害来自**技能**，或那张牌已不在处理区）⇒ 仍问一次、仍摸一张。

    牌面「获得对你造成伤害的牌**并摸一张牌**」是两段并列效果，"摸一张牌"不以"拿到了牌"
    为前提（用户裁定，票 23；与 `参考资料/技能描述歧义核查.md` B-15 一致）。标准版没有
    "并摸一张"这一句，故它在这种局面下**不问也不摸**（见下一用例）。
    """
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="jie_caocao"), card=slash, processing=()
    )

    assert len(_kind_queries(engine, JIE_JIANXIONG)) == 1, "仍要问一次"
    assert len(engine.state.player(1).hand) == 1, "拿不到牌，但那一张照摸"


def test_standard_jianxiong_is_not_offered_when_the_card_is_no_longer_in_processing():
    """对照：标准版只有"获得此牌"一段 ⇒ 拿不到牌就连问都不问。"""
    slash = _cards("slash")[1]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="caocao"), card=slash, processing=()
    )

    assert _kind_queries(engine, "hero.caocao.jianxiong") == []
    assert engine.state.player(1).hand == []


def test_jie_jianxiong_can_be_declined_and_then_draws_nothing():
    """反例：答"不发动"⇒ 牌留在处理区、不摸牌（界版的两段都发生在同一问之后）。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="jie_caocao")],
        deck=deck_excluding(PACK, slash),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    players = [Scripted(activate=False), Scripted(activate=False)]
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    _run(engine, players, lambda e: ran_timing(e, Timing.DAMAGE_END))

    assert engine.state.processing == [slash], "不发动 ⇒ 牌留在处理区"
    assert engine.state.player(1).hand == []


# ---------------------------------------------------------------------------
# ② 界司马懿【反馈】："当你受到 **1 点** 伤害后" ⇒ 逐点触发（§十九 L2036）
# ---------------------------------------------------------------------------


def _pick_first(query):
    """【反馈】的选牌：手牌是隐藏槽 ⇒ 取面内第一个槽；其余询问交回缺省应答。"""
    if query.kind != "SELECT_CARDS":
        return None
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and len(action.picks) == 1
    )


def test_jie_fankui_triggers_once_per_damage_point():
    """正例：受到 2 点伤害 ⇒ 问两次、拿两张（标准版每次伤害事件只问一次）。"""
    slash = _cards("slash")[0]
    source_cards = (_cards("peach")[0], _cards("peach")[1])
    engine, _players = _damage_engine(
        target=SeatSpec(hero="jie_simayi"),
        source_seat=SeatSpec(hand=source_cards),
        amount=2,
        card=slash,
        deck=deck_excluding(PACK, slash, *source_cards),
        select=_pick_first,
    )

    assert len(_kind_queries(engine, JIE_FANKUI)) == 2, "逐点 ⇒ 受到 2 点问两次"
    assert sorted(engine.state.player(1).hand) == sorted(source_cards), "两次各拿一张"
    assert engine.state.player(0).hand == []


def test_standard_fankui_asks_once_for_two_points():
    """对照：标准【反馈】是"当你受到伤害后"（每次伤害事件一次）。"""
    slash = _cards("slash")[1]
    source_cards = (_cards("peach")[0], _cards("peach")[1])
    engine, _players = _damage_engine(
        target=SeatSpec(hero="simayi"),
        source_seat=SeatSpec(hand=source_cards),
        amount=2,
        card=slash,
        deck=deck_excluding(PACK, slash, *source_cards),
        select=_pick_first,
    )

    assert len(_kind_queries(engine, "hero.simayi.fankui")) == 1
    assert len(engine.state.player(1).hand) == 1


def test_jie_fankui_is_not_offered_when_the_damage_has_no_source():
    """反例：无来源伤害（【闪电】一类）⇒ 不问、不搬牌。"""
    slash = _cards("slash")[0]
    engine, _players = _damage_engine(
        target=SeatSpec(hero="jie_simayi"), card=slash, source=None
    )

    assert _kind_queries(engine, JIE_FANKUI) == []
    assert engine.state.player(1).hand == []


# ---------------------------------------------------------------------------
# ③ 界司马懿【鬼才】："打出一张牌" ⇒ 改判窗口候选含装备区（§十九 L2037）
# ---------------------------------------------------------------------------

INDULGENCE = _cards("indulgence")[0]


def _black_slash() -> int:
    return next(
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    )


def _red_slash(*, excluded: tuple[int, ...] = ()) -> int:
    return next(
        card_id
        for card_id in _cards("slash")
        if card_id not in excluded
        and color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
    )


def test_jie_guicai_is_a_rejudge_capable_skill_and_the_window_asks_its_owner():
    """正例：判黑 ⇒ 界司马懿在改判窗口里打出一张红牌 ⇒ 判定按新牌算。"""
    original = _black_slash()
    replacement = _red_slash(excluded=(original,))
    deck = (original, *(card_id for card_id in range(len(PACK.deck.cards))
                        if card_id not in {original, replacement, INDULGENCE}))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(judge=(INDULGENCE,)),
            SeatSpec(hero="jie_simayi", hand=(replacement,)),
        ],
        deck=deck,
        turn_seat=0,
    )

    assert JIE_GUICAI in REJUDGE_SKILL_IDS, "界【鬼才】带改判能力标记"
    assert rejudge_capable_seats(state) == (1,)

    def rejudger(query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.REJUDGE:
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SubstituteJudgeCard)
                and action.card.card_id == replacement
            )
        return None

    engine = Engine.from_state(state, seed=1)
    _run(
        engine,
        [Scripted(), Scripted(rejudger)],
        lambda e: ran_timing(e, Timing.PHASE_PLAY_START),
    )

    windows = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "RESPOND"
        and getattr(record.query, "cause", None) is WindowCause.REJUDGE
    ]
    assert [query.actor for query in windows] == [1], "改判询问弹给界司马懿"
    assert replacement in engine.state.discard, "替代的牌结算后进弃牌堆"
    assert replacement not in engine.state.player(1).hand


def test_a_standard_simayi_seat_is_not_in_the_jie_capable_seats():
    """反例：标准司马懿的局面里，界【鬼才】的座位集合口径不误判。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="simayi", hand=(_red_slash(),))],
        deck=deck_excluding(PACK, _red_slash(), INDULGENCE),
        turn_seat=0,
    )

    assert rejudge_capable_seats(state) == (1,), "标准【鬼才】同样有改判能力"
    assert JIE_GUICAI in REJUDGE_SKILL_IDS


def _rejudge_query(query) -> bool:
    return query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.REJUDGE


def _stop_at_rejudge(engine) -> bool:
    return engine.pending is not None and _rejudge_query(engine.pending)


def _guicai_equip_state(hero_id: str):
    """判定牌 = 黑【杀】、座位 0 的判定区有【乐不思蜀】；座位 1 只有一张【麒麟弓】。

    【麒麟弓】是**红桃**：改判成它 ⇒ 【乐不思蜀】判定为红桃、不生效，出牌阶段照常开始，
    故"改判之后的账"可以用 ``PHASE_PLAY_START`` 当停止点，不依赖被跳过的阶段。
    """
    original = _black_slash()
    bow = _cards("qilin_bow")[0]
    deck = (
        original,
        *(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in {original, bow, INDULGENCE}
        ),
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(judge=(INDULGENCE,)),
            SeatSpec(hero=hero_id, equip=(bow,)),
        ],
        deck=deck,
        turn_seat=0,
    )
    return state, original, bow


def test_jie_guicai_window_offers_the_players_equipment_cards():
    """正例（§十九 L2037"打出一张牌"）：只有装备牌也能改判，且替代照常搬牌。

    `参考资料/界限突破-界标准包武将资料.md`：「鬼才由'一张手牌'放宽为'一张牌'」。
    """
    state, original, bow = _guicai_equip_state("jie_simayi")

    assert rejudge_card_zones(state, 1) == (Zone.HAND, Zone.EQUIP), (
        "界【鬼才】的候选区 = 手牌 ∪ 装备区"
    )

    def rejudger(query):
        if _rejudge_query(query):
            assert query.actor == 1, "改判询问弹给界司马懿"
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SubstituteJudgeCard) and action.card.card_id == bow
            )
        return None

    engine = Engine.from_state(state, seed=1)
    _run(
        engine,
        [Scripted(), Scripted(rejudger)],
        lambda e: ran_timing(e, Timing.PHASE_PLAY_START),
    )

    assert bow in engine.state.discard, "装备区的牌当改判牌：结算后进弃牌堆"
    assert bow not in engine.state.player(1).equip
    assert original in engine.state.discard, "原判定牌按「代替」进弃牌堆"


def test_the_standard_guicai_window_does_not_offer_equipment_cards():
    """反例（§九 L958"一张**手牌**"）：同局面换成标准司马懿 ⇒ 装备牌不进候选。"""
    state, _original, bow = _guicai_equip_state("simayi")

    assert rejudge_card_zones(state, 1) == (Zone.HAND,), "标准【鬼才】只有手牌"

    engine = Engine.from_state(state, seed=1)
    _run(engine, [Scripted(), Scripted()], _stop_at_rejudge)

    query = engine.pending
    assert query is not None and query.actor == 1, "改判询问仍会发出（只是候选只有手牌）"
    assert all(
        not (isinstance(action, SubstituteJudgeCard) and action.card.card_id == bow)
        for action in query.legal_actions
    ), "标准版只有手牌；装备区的牌不进改判候选（「用一张牌」是界版才有的放宽）"
    engine.submit(next(action for action in query.legal_actions if isinstance(action, Pass)))
    assert bow in engine.state.player(1).equip, "放弃改判 ⇒ 装备牌留在装备区"


# ---------------------------------------------------------------------------
# ④ 界张辽【突袭】：少摸**任意**张，然后获得**等量名**其他角色的各一张手牌
# （§十九 L2046–L2047）
# ---------------------------------------------------------------------------

TUXI_SEAT_PREFIX = "seat:"


class _TuxiPlayer:
    """【突袭】脚本玩家：按 ``seats`` 依次选人，选完用 ``Pass`` 收口。"""

    def __init__(self, *, seats: tuple[int, ...] = (), activate: bool = True) -> None:
        self._seats = list(seats)
        self.activate = activate
        self.queries: list = []
        self.options: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id in (JIE_TUXI, "hero.zhangliao.tuxi"):
            self.queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "SELECT_OPTION" and str(query.skill_id).startswith(("tuxi:", "jie_tuxi:")):
            self.options.append(query)
            if self._seats:
                seat = self._seats.pop(0)
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectOption)
                    and action.choice == f"{TUXI_SEAT_PREFIX}{seat}"
                )
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "PLAY_PHASE":
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        return query.legal_actions[0]


def _tuxi_state(*, hero: str):
    hand_a, hand_b = _cards("dodge")[0], _cards("dodge")[1]
    deck = deck_excluding(PACK, hand_a, hand_b)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=hero),
            SeatSpec(hand=(hand_a,)),
            SeatSpec(hand=(hand_b,)),
        ],
        deck=deck,
        turn_seat=0,
    )
    return hand_a, hand_b, deck, state


def test_jie_tuxi_one_target_means_drawing_one_fewer():
    """正例：少摸**一**张 ⇒ 拿一个人的一张手牌 + 照常摸一张。"""
    hand_a, hand_b, deck, state = _tuxi_state(hero="jie_zhangliao")
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(seats=(1,))

    _run(
        engine,
        [player, Scripted(), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert len(player.queries) == 1
    assert engine.state.player(0).hand == [hand_a, deck[0]], "少摸一张 ⇒ 只摸到牌堆顶第 1 张"
    assert engine.state.player(1).hand == []
    assert engine.state.player(2).hand == [hand_b], "没被选中的人手牌不动"


def test_jie_tuxi_two_targets_means_drawing_none():
    """正例：少摸**两**张 ⇒ 拿两个人的各一张手牌、本阶段一张不摸。"""
    hand_a, hand_b, deck, state = _tuxi_state(hero="jie_zhangliao")
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(seats=(1, 2))

    _run(
        engine,
        [player, Scripted(), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert sorted(engine.state.player(0).hand) == sorted([hand_a, hand_b])
    assert list(engine.state.deck) == list(deck), "少摸两张 ⇒ 牌堆一张都没少"


def test_declining_jie_tuxi_draws_two_cards():
    """反例：答"不发动"⇒ 正常摸两张、别人手牌不动。"""
    hand_a, hand_b, deck, state = _tuxi_state(hero="jie_zhangliao")
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(activate=False)

    _run(
        engine,
        [player, Scripted(), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert player.queries and player.options == []
    assert list(engine.state.player(0).hand) == list(deck[:2])
    assert engine.state.player(1).hand == [hand_a]


def test_jie_tuxi_is_not_offered_when_no_other_character_has_hand_cards():
    """反例：没有可拿的手牌 ⇒ 连"是否发动"都不问（正常摸两张）。"""
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_zhangliao"), SeatSpec(), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer()

    _run(
        engine,
        [player, Scripted(), Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert player.queries == []
    assert list(engine.state.player(0).hand) == list(deck[:2])


# ---------------------------------------------------------------------------
# ⑤ 界许褚【裸衣】：亮出牌堆顶三张 → 拿基本牌 / 武器牌 /【决斗】→ 放弃摸牌 +
# "直到你的下个回合开始"基础伤害 +1（§十九 L2051–L2055）
# ---------------------------------------------------------------------------

_LUOYI_TOP_TYPES = ("slash", "crossbow", "duel")


class _LuoyiPlayer:
    """【裸衣】脚本玩家：第一次「是否发动」按 ``activate`` 答；第二次按 ``gain`` 答。"""

    def __init__(
        self, *, activate: bool = True, gain: bool = True, card_type: str = "slash"
    ) -> None:
        self.activations_left = 1 if activate else 0
        self.gain = gain
        self.card_type = card_type
        self.queries: list = []
        self.options: list = []
        self.cards_used = 0

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_LUOYI:
            self.queries.append(query)
            if self.activations_left:
                self.activations_left -= 1
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "SELECT_OPTION" and str(query.skill_id).startswith("luoyi:"):
            self.options.append(query)
            wanted = "luoyi_gain" if self.gain else "luoyi_skip"
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectOption) and action.choice == wanted
            )
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.type == self.card_type
                    and action.targets == (1,)
                ):
                    self.cards_used += 1
                    return action
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if query.kind == "DODGE":
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return _pass_or_first(query)


def _luoyi_deck(top) -> tuple[int, ...]:
    """把 ``top`` 的三张放在牌堆顶，其余牌接在后面。"""
    used = set(top)
    return (*top, *(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used))


def _one_of(type_id: str, *, excluded=()) -> int:
    return next(card_id for card_id in _cards(type_id) if card_id not in excluded)


def test_jie_luoyi_takes_the_qualified_revealed_cards_and_gives_up_the_draw():
    """正例：三张全合格（基本 / 武器 / 决斗）⇒ 全拿走、本阶段一张不摸。"""
    top = (_one_of("slash"), _one_of("crossbow"), _one_of("duel"))
    hand_card = _one_of("peach", excluded=set(top))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(hand_card,)), SeatSpec()],
        deck=_luoyi_deck(top),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()

    _run(
        engine,
        [player, Scripted()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert len(player.queries) == 1
    assert len(player.options) == 1, "亮出后必须再问一次「是否获得」"
    assert player.options[0].skill_id == "luoyi:gain"
    assert {
        action.choice
        for action in player.options[0].legal_actions
        if isinstance(action, SelectOption)
    } == {"luoyi_gain", "luoyi_skip"}
    assert not any(isinstance(action, Pass) for action in player.options[0].legal_actions), (
        "「是否获得」是二选一，不是可取消步骤"
    )
    assert engine.state.player(0).hand == [hand_card, *top], "三张全进手牌、没有额外摸牌"
    assert all(card_id not in engine.state.deck for card_id in top)
    assert all(card_id not in engine.state.discard for card_id in top), "获得不是置入弃牌堆"
    assert [PACK.deck.cards[card_id].type for card_id in top] == list(_LUOYI_TOP_TYPES)


def test_jie_luoyi_declining_the_gain_discards_the_revealed_cards_and_draws_two():
    """用户裁定：不获得 ⇒ 三张亮出的牌置入弃牌堆，然后正常摸两张、不加伤。"""
    top = (_one_of("slash"), _one_of("crossbow"), _one_of("duel"))
    slash = _one_of("slash", excluded=set(top))
    deck = tuple(card_id for card_id in _luoyi_deck(top) if card_id != slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(slash,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer(gain=False)
    players = [player, Scripted()]

    _run(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    assert len(player.queries) == 1
    assert len(player.options) == 1
    assert set(top) <= set(engine.state.discard), "不获得的三张必须进弃牌堆"
    discard_moves = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_move"
        and record.to_zone.zone is Zone.DISCARD
        and {card.card_id for card in record.cards} == set(top)
    ]
    assert len(discard_moves) == 1 and discard_moves[0].reason is MoveReason.PUT, (
        "置入弃牌堆是规则性搬牌（PUT），不是弃置（DISCARD）"
    )
    assert not set(top) & set(engine.state.player(0).hand), "不获得的牌不能留 / 摸回手里"
    assert len(engine.state.player(0).hand) == 3, "原有的【杀】+ 正常摸两张"
    assert engine.state.player(0).luoyi_until_next_turn is False, "不获得 ⇒ 不加伤"

    _run(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))
    assert player.cards_used == 1
    assert engine.state.player(1).hp == 3, "【杀】只有基础 1 点"


def test_jie_luoyi_adds_one_to_this_turns_slash_damage():
    """正例：若如此做 ⇒ 本回合【杀】伤害 1 + 1 = 2。"""
    top = (_one_of("slash"), _one_of("crossbow"), _one_of("duel"))
    slash = _one_of("slash", excluded=set(top))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(slash,)), SeatSpec()],
        deck=_luoyi_deck(top),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()

    _run(engine, [player, Scripted()], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert player.cards_used == 1
    assert engine.state.player(1).hp == 2, "【杀】基础 1 点 + 界【裸衣】1 点"


def test_declining_jie_luoyi_draws_two_and_deals_one():
    """反例：不发动 ⇒ 正常摸两张、【杀】只有基础 1 点。"""
    slash = _one_of("slash")
    deck = deck_excluding(PACK, slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(slash,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer(activate=False)

    _run(engine, [player, Scripted()], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(player.queries) == 1
    assert player.options == [], "不发动 ⇒ 连亮牌都不发生，没有第二次询问"
    assert len(engine.state.player(0).hand) >= 1
    assert engine.state.player(1).hp == 3, "不发动 ⇒ 【杀】只有基础 1 点"


def test_jie_luoyi_without_a_qualified_card_skips_the_question_and_discards_the_three():
    """退化档：三张都不是基本 / 武器 / 决斗 ⇒ 不问「是否获得」，直接按不获得走。

    没有可拿的牌时不存在第二个选择；但用户裁定的"不获得 ⇒ 置入弃牌堆"照常生效，
    故亮出的三张进弃牌堆、随后正常摸两张，且不解锁 +1。
    """
    top = (_one_of("dismantlement"), _one_of("snatch"), _one_of("ex_nihilo"))
    slash = _one_of("slash", excluded=set(top))
    deck = tuple(card_id for card_id in _luoyi_deck(top) if card_id != slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(slash,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()
    players = [player, Scripted()]

    _run(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    assert len(player.queries) == 1
    assert player.options == [], "没有可拿的牌 ⇒ 不发没有二选一的询问"
    assert set(top) <= set(engine.state.discard), "不获得 ⇒ 三张置入弃牌堆"
    assert not set(top) & set(engine.state.player(0).hand), "三张不应被摸回手里"
    assert len(engine.state.player(0).hand) == 3, "原有的【杀】+ 正常摸两张"
    assert engine.state.player(0).luoyi_until_next_turn is False

    _run(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))
    assert player.cards_used == 1
    assert engine.state.player(1).hp == 3, "没拿到牌 ⇒ 不解锁 +1"
    revealed = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_reveal"
    ]
    assert len(revealed) == 3, "亮出三张是公开事实（三条 card_reveal）"


def test_jie_luoyi_reveals_wait_for_the_gain_question_to_be_answered():
    """亮出的三张必须挂在**承载这次询问的帧**上，问完才 `pop`。

    阶段体没有结算栈帧（``_dispatch_phase_body`` 的帧 ``frame_id = 0``），而前端
    ``stageLane`` 对 ``frame_id = 0`` 的展示项只给固定 1s 停留。故【裸衣】亮牌若沿用
    那个 0 号帧，三张会在「是否获得」还没答之前淡出。这里钉住修法：亮牌前给阶段体帧
    分配一个真实帧号并落 ``push``，询问挂起期间不落 ``pop``；答完这才落 ``pop``。
    """
    top = (_one_of("slash"), _one_of("crossbow"), _one_of("duel"))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jie_xuchu", hand=(_one_of("peach", excluded=set(top)),)),
            SeatSpec(),
        ],
        deck=_luoyi_deck(top),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()
    players = [player, Scripted()]

    _run(
        engine,
        players,
        lambda e: e.pending is not None and str(e.pending.skill_id) == "luoyi:gain",
    )

    reveals = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_reveal"
    ]
    assert len(reveals) == 3, "亮出三张是公开事实（三条 card_reveal）"
    frame_ids = {record.frame_id for record in reveals}
    assert len(frame_ids) == 1, "三张亮牌必须挂在同一个承载帧上"
    frame_id = frame_ids.pop()
    assert frame_id > 0, "阶段体帧必须拿到真实帧号，否则前端按无帧的 1s 停留淡出"

    pushes = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_id == frame_id
    ]
    pops = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "pop" and record.frame_id == frame_id
    ]
    assert len(pushes) == 1, "承载帧必须有一次 push"
    assert pops == [], "询问还没答，承载帧不得 pop"

    action = next(
        candidate
        for candidate in engine.pending.legal_actions
        if isinstance(candidate, SelectOption) and candidate.choice == "luoyi_gain"
    )
    engine.submit(action)
    _run(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    pops = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "pop" and record.frame_id == frame_id
    ]
    assert [record.frame_id for record in pops] == [frame_id], "答完询问才落 pop"
    assert pops[0].seq > reveals[-1].seq, "pop 必须在三条亮牌之后"


def test_jie_luoyi_bonus_survives_other_players_turns():
    """正例（作用域的**区别**）：效果写到"直到**你的**下个回合开始"，故别人的回合里仍 +1。

    构造方式与 :mod:`tests.test_hero_damage_after` 同姿态：先用真实回合激活【裸衣】，
    等回合轮转到别人（``turn_seat`` 不再是许褚）后，压一个与引擎同形的 DAMAGE 子帧
    （来源 = 界许褚）；+1 必须仍生效。随后继续跑到许褚的下一个 ``TURN_START``，
    座位状态才过期。
    """
    top = (_one_of("duel"), _one_of("peach"), _one_of("ex_nihilo"))
    slash = _one_of("slash", excluded=set(top))
    deck = tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in {slash, *top}
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xuchu", hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=(*top, *deck),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()
    players = [player, Scripted(), Scripted()]

    _run(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))
    assert engine.state.player(0).luoyi_until_next_turn is True, "若如此做 ⇒ 加伤已挂上"

    # 回合轮转到别人（座位 0 的回合结束、座位 1 的回合开始前）。
    _run(engine, players, lambda e: e.state.turn_seat == 1)
    assert engine.state.turn_seat == 1, "现在是别人的回合"

    # 与引擎同形的 DAMAGE 子帧：来源 = 座位 0（界许褚），目标 = 座位 2。
    state = engine.state

    def damage_ends(engine) -> int:
        return sum(
            1
            for record in engine.records
            if record.kind == "timing" and record.timing is Timing.DAMAGE_END
        )

    before = damage_ends(engine)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 0,
            "targets": (2,),
            "card": state.card(slash),
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 2,
            "channel": state.card(slash),
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    _run(engine, players, lambda e: damage_ends(e) > before)
    assert engine.state.player(2).hp == 2, "别人的回合里，界【裸衣】的 +1 仍在"

    # 到了自己的下个回合起点，+1 才过期（第 4 条 TURN_START = 座位 0 的第二个回合）。
    _run(
        engine,
        players,
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "timing" and record.timing is Timing.TURN_START
        )
        >= 4,
    )
    assert engine.state.player(0).luoyi_until_next_turn is False


# ---------------------------------------------------------------------------
# ⑥ 界曹操【护驾】① + ②：求助窗口仍在，②是**决定者不是受益者**的第一条钩子
# （§十九 L2030–L2034）
# ---------------------------------------------------------------------------


def _lord_state(*, lord_hero: str, ally_kingdom, ally_hand: tuple[int, ...], turn_seat: int = 0):
    """座位 0 是攻击者（当前回合角色），座位 1 是曹操（主公），座位 2 是盟友。"""
    slash = _cards("slash")[0]
    used = {slash, *ally_hand}
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(
                hero=lord_hero,
                identity=Identity.LORD,
                kingdom=Kingdom.WEI,
            ),
            SeatSpec(hand=ally_hand, kingdom=ally_kingdom),
        ],
        deck=deck,
        turn_seat=turn_seat,
    )
    return slash, deck, state


def test_jie_hujia_first_window_still_provides_the_ally_dodge_and_second_asks_the_ally():
    """界【护驾】①仍在候选里；盟友代打【闪】后，②把"是否让主公摸牌"问给**盟友**。"""
    dodge = _cards("dodge")[0]
    slash, deck, state = _lord_state(
        lord_hero="jie_caocao", ally_kingdom=Kingdom.WEI, ally_hand=(dodge,)
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                ):
                    return action
        return None

    def lord(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIE_HUJIA:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id == dodge:
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(lord), Scripted(ally)],
        lambda e: e.state.player(1).hand,
    )

    requests = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "RESPOND"
        and getattr(record.query, "cause", None) is WindowCause.LORD_REQUEST
    ]
    assert [query.actor for query in requests] == [2], "①的求助窗口问其他魏势力角色"
    assert engine.state.player(1).hp == 4, "代打的【闪】抵消了这张【杀】"
    assert dodge in engine.state.discard, "代打是真打出（实体牌进弃牌堆）"

    seconds = _kind_queries(engine, JIE_HUJIA)
    assert [query.actor for query in seconds] == [2], "②的询问发给**打出【闪】**的座位"
    # 座位 0 的摸牌阶段先摸走了牌堆顶两张，故②摸到的是 ``deck[2]``。
    assert engine.state.player(1).hand == [deck[2]], "收益归主公（摸一张）"


def test_jie_hujia_second_declining_leaves_the_lord_without_a_card():
    """反例：决定者（盟友）答"不发动"⇒ 主公不摸牌；放弃不占"每回合一次"。"""
    dodge = _cards("dodge")[0]
    slash, deck, state = _lord_state(
        lord_hero="jie_caocao", ally_kingdom=Kingdom.WEI, ally_hand=(dodge,)
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (2,)
                ):
                    return action
        return None

    def ally(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == dodge:
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(), Scripted(ally, activate=False)],
        lambda e: slash in e.state.discard and dodge in e.state.discard,
    )

    assert [query.actor for query in _kind_queries(engine, JIE_HUJIA)] == [2]
    assert engine.state.player(1).hand == []
    assert engine.state.player(2).hp == 4, "【闪】抵消了这张【杀】"


def test_jie_hujia_second_does_not_ask_a_non_wei_ally():
    """反例：非同势力角色使用【闪】⇒ ②不问（"其他**魏势力**角色"）。"""
    dodge = _cards("dodge")[0]
    slash, _deck, state = _lord_state(
        lord_hero="jie_caocao", ally_kingdom=Kingdom.SHU, ally_hand=(dodge,)
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (2,)
                ):
                    return action
        return None

    def ally(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == dodge:
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(), Scripted(ally)],
        lambda e: slash in e.state.discard and dodge in e.state.discard,
    )

    assert _kind_queries(engine, JIE_HUJIA) == [], "异势力角色不触发②"
    assert engine.state.player(1).hand == []


def test_jie_hujia_second_does_not_ask_when_the_lord_dodges_himself():
    """反例：曹操自己使用【闪】⇒ 不是"其他魏势力角色"，②不问。"""
    dodge = _cards("dodge")[0]
    slash = _cards("slash")[0]
    used = {slash, dodge}
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(
                hero="jie_caocao",
                identity=Identity.LORD,
                kingdom=Kingdom.WEI,
                hand=(dodge,),
            ),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                ):
                    return action
        return None

    def lord(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == dodge:
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(lord)],
        lambda e: slash in e.state.discard and dodge in e.state.discard,
    )

    assert _kind_queries(engine, JIE_HUJIA) == []
    assert engine.state.player(1).hp == 4


def test_standard_caocao_has_no_second_hook():
    """反例（对照）：标准曹操的【护驾】只有①，盟友代打【闪】不会问"令主公摸牌"。"""
    dodge = _cards("dodge")[0]
    slash, _deck, state = _lord_state(
        lord_hero="caocao", ally_kingdom=Kingdom.WEI, ally_hand=(dodge,)
    )
    engine = Engine.from_state(state, seed=1)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                ):
                    return action
        return None

    def lord(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == "hero.caocao.hujia":
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id == dodge:
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(lord), Scripted(ally)],
        lambda e: e.state.player(1).hp == 4 and dodge in e.state.discard,
    )

    assert _kind_queries(engine, JIE_HUJIA) == []
    assert _kind_queries(engine, "hero.caocao.hujia") == []


def _hujia_condition(timing: Timing):
    return next(
        hook.condition
        for hook in CATALOG[JIE_HUJIA].hooks
        if hook.timing is timing
    )


class _Frame:
    """只带 ``data`` 的最小帧（``Hook.condition`` 的输入面）。"""

    def __init__(self, **data) -> None:
        self.data = data


class _Host:
    """只带 ``state`` 的最小宿主。"""

    def __init__(self, state) -> None:
        self.state = state


def test_jie_hujia_second_condition_has_four_guards():
    """反例（条件直探）："其他魏势力角色于其回合外"的四段守卫逐段验一遍。

    这一档在正常牌流里造不出来（【闪】的响应者通常不是当前回合角色），故按
    ``tests/test_hero_catalog.py`` 的持有者自校探针同姿态，直接对条件求值。
    """
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(kingdom=Kingdom.WEI),
            SeatSpec(hero="jie_caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, dodge),
        turn_seat=2,
    )
    frame = _Frame(card=state.card(dodge), source=0)
    host = _Host(state)
    frame.data[MATCH_OWNER_KEY] = 1

    for timing in (Timing.USE_ON_USE, Timing.PLAY_ON_PLAY):
        reset_turn_skill_state(state)
        condition = _hujia_condition(timing)
        assert condition(host, frame) is True, "其他魏势力角色于其回合外使用【闪】 ⇒ 触发"

        state.turn_seat = 0
        assert condition(host, frame) is False, "使用者的**自己的**回合 ⇒ 不触发"
        state.turn_seat = 2

        frame.data["source"] = 1
        assert condition(host, frame) is False, "持有者（主公）自己使用 ⇒ 不触发"
        frame.data["source"] = 0

        state.players[0].kingdom = Kingdom.SHU
        assert condition(host, frame) is False, "非同势力 ⇒ 不触发"
        state.players[0].kingdom = Kingdom.WEI

        frame.data["card"] = state.card(_cards("slash")[3])
        assert condition(host, frame) is False, "不是【闪】⇒ 不触发"
        frame.data["card"] = state.card(dodge)

        # 【护驾】②的「每回合限一次」计在**主公 / 实例**头上（用户 2026-11-10 裁定：
        # 「每个回合只触发一次，而非每个盟友每回合触发一次」）⇒ 落位走**实例级**位图。
        # 这一格是两版 ② 的分界：换成 `mark_skill_decider_used_this_turn` 会让这条变红。
        mark_skill_used_this_turn(state, 1, JIE_HUJIA)
        assert condition(host, frame) is False, "本回合已发动过 ⇒ 不再触发（整回合一次）"


def test_jie_hujia_second_is_offered_once_per_turn():
    """正例 + 反例：**每回合限一次**——同一回合里盟友第二张【闪】不再触发②。

    同一回合两次"需要【闪】"：一次【杀】对盟友（**使用**）、一次【万箭齐发】对全体
    （**打出**）。盟友两次都打出【闪】，但②只在第一次被问到；主公只摸一张（主公自己也
    用一张【闪】抵消【万箭】，故不再吃伤害、不触发自己的【奸雄】）。
    """
    slash = _cards("slash")[0]
    barrage = _cards("arrow_barrage")[0]
    dodge_a, dodge_b = _cards("dodge")[0], _cards("dodge")[1]
    lord_dodge = _cards("dodge")[2]
    used = {slash, barrage, dodge_a, dodge_b, lord_dodge}
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash, barrage)),
            SeatSpec(
                hero="jie_caocao",
                identity=Identity.LORD,
                kingdom=Kingdom.WEI,
                hand=(lord_dodge,),
            ),
            SeatSpec(hand=(dodge_a, dodge_b), kingdom=Kingdom.WEI),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    played: list[int] = []

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (2,)
                ):
                    return action
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == barrage:
                    return action
        return None

    def lord(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.ARROW_BARRAGE
        ):
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id == lord_dodge:
                    return action
        return None

    def ally(query):
        # 第一张是【杀】的 DODGE 窗口（使用），第二张是【万箭齐发】的 RESPOND 窗口（打出）。
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id in (dodge_a, dodge_b):
                    played.append(action.card.card_id)
                    return action
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.ARROW_BARRAGE
        ):
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id in (
                    dodge_a,
                    dodge_b,
                ):
                    played.append(action.card.card_id)
                    return action
        return None

    _run(
        engine,
        [Scripted(attacker), Scripted(lord), Scripted(ally)],
        lambda e: len(played) >= 2,
    )

    assert sorted(played) == sorted((dodge_a, dodge_b)), "盟友两次都打出了【闪】"
    assert len(_kind_queries(engine, JIE_HUJIA)) == 1, "每回合限一次：第二张【闪】不再问"
    assert len(engine.state.player(1).hand) == 1, "主公只摸一张（收益仍归主公）"
    assert engine.state.player(1).hp == 4, "主公自己用【闪】抵消了【万箭齐发】"


def test_jie_hujia_second_is_once_per_turn_not_once_per_ally():
    """「每回合限一次」**整回合一次**，不是"每个盟友各一次"（用户 2026-11-10 裁定）。

    与【激将】② 的分界就在这一条（那一条按**决定者**计，见
    ``tests/test_hero_jie_shu.py::test_jie_jijiang_second_is_once_per_turn_for_the_substitute``）：

    - 座位 1、3 是**两名不同的魏势力盟友**，同一回合各使用 / 打出一次【闪】；
    - 座位 1 的【闪】（【杀】的 DODGE 窗口，距离 1）让 ② **真的发动** ⇒ 这一回合的名额用掉；
    - 座位 3 的【闪】（【万箭齐发】的响应）**不再被问**（若按"每个盟友一次"，这里会发第二问）；
    - 主公因此只摸**一张**。

    计的是"真的发动"：如果座位 1 只是**放弃**，名额不动、座位 3 照样会被问——那半由
    ``test_jie_hujia_second_is_offered_once_per_turn`` 与条件直探那两条守（放弃不落位）。
    座位序 = 攻方 0 / 盟友 1、3 / 主公 2（4 人环里 0↔1 与 0↔3 都是距离 1，故【杀】够得着）。
    """
    slash = _cards("slash")[0]
    barrage = _cards("arrow_barrage")[0]
    dodge_a, dodge_b = _cards("dodge")[0], _cards("dodge")[1]
    lord_dodge = _cards("dodge")[2]
    used = {slash, barrage, dodge_a, dodge_b, lord_dodge}
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash, barrage)),
            SeatSpec(hand=(dodge_a,), kingdom=Kingdom.WEI),
            SeatSpec(
                hero="jie_caocao",
                identity=Identity.LORD,
                kingdom=Kingdom.WEI,
                hand=(lord_dodge,),
            ),
            SeatSpec(hand=(dodge_b,), kingdom=Kingdom.WEI),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    played: list[int] = []

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == slash
                    and action.targets == (1,)
                ):
                    return action
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == barrage:
                    return action
        return None

    def lord(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.ARROW_BARRAGE
        ):
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id == lord_dodge:
                    played.append(action.card.card_id)
                    return action
        return None

    def ally(query):
        kind = getattr(query, "kind", None)
        if kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == dodge_a:
                    played.append(action.card.card_id)
                    return action
        if kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.ARROW_BARRAGE:
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.card_id == dodge_b:
                    played.append(action.card.card_id)
                    return action
        return None

    _run(
        engine,
        [
            Scripted(attacker),
            Scripted(ally),
            Scripted(lord),
            # 座位 3：第二张【闪】照打（它拿不到 ② 的询问，但行动本身必须发生）。
            Scripted(ally),
        ],
        lambda e: len(played) >= 3,
    )

    assert sorted(played) == sorted((dodge_a, dodge_b, lord_dodge)), "三张【闪】都打了出来"
    assert len(_kind_queries(engine, JIE_HUJIA)) == 1, (
        "第二个**不同的**魏势力盟友不再被问（整回合只触发一次，不是每个盟友一次）"
    )
    assert len(engine.state.player(2).hand) == 1, "主公只摸一张"
    assert engine.state.player(2).hp == 4, "主公自己用【闪】抵消了【万箭齐发】"
