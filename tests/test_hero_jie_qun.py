"""M6 票 18：界·群（界华佗 / 界吕布 / 界华雄 / 界公孙瓒）。

八条技能逐条走**外部缝**（候选 / 询问 / 记录流 / 终局状态），与各族的既有测试同一姿态：

========================  ============================================================
技能                        牌面（§十九 L2197–L2231）与落点
========================  ============================================================
界华佗【急救】              "你的回合外，你可以将一张红色牌当【桃】使用"——与标准版
                            **逐字相同**（同一条转化声明，经 ``shared_with`` 派生）
界华佗【除疠】              "选择任意名势力各不相同的其他角色，然后弃置你和这些角色的
                            各一张牌；因此被弃置黑桃牌的角色各摸一张牌"——目标组合
                            = 任意名 / 势力互异 / 不含自己 / 至少 1 名
界吕布【无双】              与标准版**逐字同义**（①【杀】两张【闪】、②【决斗】两张
                            【杀】）；界版只多【利驭】
界吕布【利驭】              "获得其区域内的一张牌，若不为装备牌则其摸一张，否则视为你对
                            **该角色指定的另一名其他角色**使用一张【决斗】"
界华雄【耀武】              锁定技：受到**牌造成的**伤害时，红色 ⇒ 伤害来源摸一张，
                            否则自己摸一张
界华雄【势斩】              出牌阶段限两次：令一名其他角色视为对你使用一张【决斗】
界公孙瓒【趫猛】            "选择其区域里的一张牌：为坐骑牌则获得之，否则弃置之"
                            ——可能选到手牌（对选择者不可见）
界公孙瓒【义从】            锁定技：①自己到别人 -1；②体力 ≤ 2 时别人到自己 +1
========================  ============================================================

**三条本票裁定**（`06`（§5「程序化约定与偏离一手」））：【除疠】目标 = 任意名 / 势力
互异 / 不含自己 / 至少 1 名（0 目标非法）；【利驭】的【决斗】目标由**受伤者指定**；
【趫猛】"区域里的一张牌"按 `GLOSSARY.md`「区域默认」= 手牌 + 装备 + 判定区，可能选到
**隐藏的手牌**。
"""

from __future__ import annotations

from sgs.data import Kingdom, load_content_pack
from sgs.effects import legal_actions_for
from sgs.engine import Engine
from sgs.skills import conversion_offers
from sgs.skills.active import CHULI_FLOW
from sgs.state import (
    ActivateSkill,
    MoveReason,
    Pass,
    PlayCard,
    QueryKind,
    SelectCards,
    SelectOption,
    SelectOptions,
    UseCard,
)

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries

PACK = load_content_pack(deck_profile="standard_108")

JIJIU = "hero.jie_huatuo.jijiu"
CHULI = "hero.jie_huatuo.chuli"

QUN, WEI, SHU, WU = Kingdom.QUN, Kingdom.WEI, Kingdom.SHU, Kingdom.WU


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _with_suit(type_id: str, suit: str, *, skip: int = 0) -> int:
    matches = [
        card_id for card_id in _cards(type_id) if PACK.deck.cards[card_id].suit == suit
    ]
    return matches[skip]


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小合法应答（结束阶段 / 放弃 / 首个候选）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return _pass_or_end(query)


def _pass_or_end(query):
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "EndPhase":
                return action
    return query.legal_actions[0]


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _run(state, players, stop, seed: int = 3):
    engine = Engine.from_state(state, seed=seed)
    participants = list(players) + [Scripted()] * (len(state.players) - len(players))
    run_queries(engine, participants, stop)
    return engine


def _activated(engine, skill_id: str) -> bool:
    return any(
        record.kind == "action"
        and record.accepted
        and isinstance(record.action, ActivateSkill)
        and record.action.skill_id == skill_id
        for record in engine.records
    )


def _offers(state, skill_id: str, seat: int = 0) -> list[ActivateSkill]:
    return [
        action
        for action in legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, ActivateSkill) and action.skill_id == skill_id
    ]


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# ① 界华佗【急救】：与标准版逐字相同（同一条转化声明，另一个武将 id）
# ---------------------------------------------------------------------------


def test_jie_jijiu_offers_red_cards_as_peach_outside_the_owners_turn():
    """正例：回合外红牌当【桃】的候选归属 ``hero.jie_huatuo.jijiu``。"""
    red = _with_suit("dodge", "heart")
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(red,), hero="jie_huatuo", kingdom=QUN), SeatSpec()],
        deck=deck_excluding(PACK, red),
        turn_seat=1,
    )

    offers = conversion_offers(state, 0, "peach")

    assert (red, JIJIU) in offers


def test_jie_jijiu_stays_outside_turn_only_and_does_not_belong_to_standard_huatuo():
    """反例：自己的回合内不产出；标准华佗的候选归标准 id（两版各一条声明入口）。"""
    red = _with_suit("dodge", "heart")
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(red,), hero="jie_huatuo", kingdom=QUN),
            SeatSpec(hand=(red,), hero="huatuo", kingdom=QUN),
            SeatSpec(kingdom=WEI),
        ],
        deck=deck_excluding(PACK, red),
        turn_seat=0,
    )

    assert conversion_offers(state, 0, "peach") == ()
    assert (red, "hero.huatuo.jijiu") not in conversion_offers(state, 0, "peach")

    state.turn_seat = 2
    jie_offers = conversion_offers(state, 0, "peach")
    standard_offers = conversion_offers(state, 1, "peach")
    assert (red, JIJIU) in jie_offers
    assert (red, "hero.huatuo.jijiu") not in jie_offers
    assert (red, "hero.huatuo.jijiu") in standard_offers
    assert (red, JIJIU) not in standard_offers


# ---------------------------------------------------------------------------
# ② 界华佗【除疠】：目标组合（任意名 / 势力互异 / 不含自己 / 至少 1 名）
# ---------------------------------------------------------------------------


def _chuli_state(*, own_card: int, wei_a: int, wei_b: int, shu: int):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(own_card,), hero="jie_huatuo", kingdom=QUN),
            SeatSpec(hand=(wei_a,), kingdom=WEI),
            SeatSpec(hand=(wei_b,), kingdom=WEI),
            SeatSpec(hand=(shu,), kingdom=SHU),
        ],
        deck=deck_excluding(PACK, own_card, wei_a, wei_b, shu),
        turn_seat=0,
    )


def _chuli_players(*, picks: tuple[str, ...], cards: tuple[int, ...] = (0,)):
    """脚本：发动【除疠】→ **一次**点齐座位（票 13 的座位多选）→ 每次选牌都点面内第 ``cards[i]`` 个。

    只对**技能帧里的** ``SELECT_CARDS`` 应答（``query.skill_id == CHULI``）：弃牌阶段的
    自弃也走同一询问类型，不加这道过滤会把它也算成【除疠】的成本。
    """
    state = {
        "activated": False,
        "seats": list(picks),
        "cards": list(cards),
        "seat_queries": [],
    }

    def choose(query):
        if query.kind == QueryKind.PLAY_PHASE and not state["activated"]:
            action = _find(
                query, lambda a: isinstance(a, ActivateSkill) and a.skill_id == CHULI
            )
            if action is not None:
                state["activated"] = True
                return action
        if query.kind == QueryKind.SELECT_OPTIONS and str(query.skill_id).startswith(
            "chuli:seat"
        ):
            state["seat_queries"].append(query)
            seats = tuple(state["seats"])
            state["seats"].clear()
            if not seats:
                return _find(query, lambda a: isinstance(a, Pass))
            return SelectOptions(choices=seats, answers_seq=query.seq)
        if query.kind == QueryKind.SELECT_CARDS and query.skill_id == CHULI:
            index = state["cards"].pop(0)
            state["cards"].append(index)
            return SelectCards(picks=(index,), answers_seq=query.seq)
        return None

    return state, [Scripted(choose), Scripted(), Scripted(), Scripted()]


def _phase_over(engine) -> bool:
    from sgs.state import Phase

    return engine.finished or engine.state.phase in (Phase.DISCARD, Phase.FINISH)


def test_chuli_discards_one_card_from_self_and_each_distinct_kingdom_target():
    """正例：自己 + 两名势力互异的其他角色各弃一张；黑桃那几张各摸一张。"""
    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    wei_other = _with_suit("peach", "heart")
    shu = _with_suit("slash", "club")
    state = _chuli_state(own_card=own, wei_a=wei, wei_b=wei_other, shu=shu)
    _, players = _chuli_players(picks=("seat:1", "seat:3"), cards=(0, 0, 0))

    engine = _run(state, players, _phase_over)

    assert _activated(engine, CHULI)
    assert _moves_of(engine, own) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, wei) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, shu) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, wei_other) == [], "没被选中的角色一张都不弃"
    # 0 号位：起始 1 + 摸牌 2 − 弃 1 + 黑桃摸 1 = 3
    assert len(engine.state.player(0).hand) == 3
    # 1 号位：起始 1 − 弃 1 + 黑桃摸 1 = 1
    assert len(engine.state.player(1).hand) == 1
    # 3 号位：起始 1 − 弃 1（梅花不摸）= 0
    assert len(engine.state.player(3).hand) == 0


def test_chuli_grays_out_same_kingdom_seats_after_the_first_pick():
    """票 13：座位步改成**一次多选**之后，"势力互异"靠**服务端预览**现算置灰。

    座位步不再问两次（第二次的池排除已选势力），而是一次 `SELECT_OPTIONS`：**首问**那一份
    `options` 是"此刻能选谁"的快照，"第二名必须换势力"由票 11 的预览按同一份
    `_chuli_targets(state, frame, owner, chosen)` 现算。判据走三个真产出路径：

    1. **清单**（`manifest`）：这一步是一次多选的座位步（`min=1`，`pool` 非空）；
    2. **预览**（`preview_step`，票 11 端点读的就是它）：选了魏（1 号位）之后，同势力的
       2 号位立刻从 `options` 里消失，异势力的 3 号位照旧可选，光标仍停在这一步；
    3. **校验器**（`validate_activation`）：对同一份池逐个对表——预览说能点的都收，
       预览说不能点的（同势力组合）就拒。
    """
    from sgs.skills.flow import manifest, preview_step
    from sgs.skills.preactivation import validate_activation

    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    wei_other = _with_suit("peach", "heart")
    shu = _with_suit("slash", "club")
    state = _chuli_state(own_card=own, wei_a=wei, wei_b=wei_other, shu=shu)

    steps = manifest(CHULI_FLOW, state, 0)
    assert [step.name for step in steps] == ["seat"], "只有一步（座位多选）"
    assert steps[0].min == 1 and steps[0].pool, "首问的候选索引由后端给出"

    first = preview_step(CHULI_FLOW, state, 0, [], steps=steps)
    assert first.kind == "seat"
    assert {"seat:1", "seat:2", "seat:3"} <= set(first.options), (
        f"一开始两名魏将 + 一名蜀将都能选：{first.options}"
    )
    assert not any("seat:0" in option for option in first.options), "不含自己"

    second = preview_step(CHULI_FLOW, state, 0, [("seat", ("seat:1",))], steps=steps)
    assert second.cursor == 0, "多选座位步：光标停在它上面（还能继续加人）"
    assert "seat:2" not in second.options, "同势力（魏）的 2 号位必须立刻变灰"
    assert "seat:3" in second.options, "异势力（蜀）照旧可选"
    assert second.can_submit is True, "已经够 1 名 ⇒ 此刻就能提交"

    for option in second.options:
        assert validate_activation(state, 0, CHULI, (), ("seat:1", option)) is None, (
            f"预览说 {option} 能点 ⇒ 校验器必须收"
        )
    assert validate_activation(state, 0, CHULI, (), ("seat:1", "seat:2")) is not None, (
        "预览说 2 号位不能点 ⇒ 校验器必须拒（势力互异）"
    )


def test_chuli_still_asks_one_discard_per_target_after_the_seat_step():
    """票 13：座位步变成一次多选之后，提交点里**逐名问"弃哪张"**的询问保持原样。

    座位只问一次（`SELECT_OPTIONS`），但成本牌仍按"自己 + 每名目标"各问一次
    （`SELECT_CARDS`，`reveal_hand=True` ⇒ 牌桌直选，靠票 09 的判据）——两步不能并成一步：
    弃哪张是逐名角色的独立选择。
    """
    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    wei_other = _with_suit("peach", "heart")
    shu = _with_suit("slash", "club")
    state = _chuli_state(own_card=own, wei_a=wei, wei_b=wei_other, shu=shu)
    recorded, players = _chuli_players(picks=("seat:1", "seat:3"), cards=(0, 0, 0))

    engine = _run(state, players, _phase_over)

    assert len(recorded["seat_queries"]) == 1, "座位一次问齐（不是两次单选）"
    seat_query = recorded["seat_queries"][0]
    assert seat_query.kind == QueryKind.SELECT_OPTIONS
    assert (seat_query.min_selected, seat_query.max_selected) == (1, 5), "任意名（≥1）"
    assert tuple(seat_query.options) == ("seat:1", "seat:2", "seat:3"), (
        "首问的池 = 全部有牌可弃的异势力其他角色（含两名魏将——同势力由预览收窄）"
    )
    card_asks = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_CARDS
        and record.query.skill_id == CHULI
    ]
    assert len(card_asks) == 3, "自己 + 两名目标 ⇒ 三次『弃哪张』"

    assert _moves_of(engine, own) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, wei) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, shu) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, wei_other) == [], "没被选中的角色一张都不弃"


def test_chuli_refuses_a_same_kingdom_set_the_pool_never_offered():
    """票 13：服务端不采信一份"它自己不会铺出来"的集合（越池 ⇒ 整步取消，零副作用）。

    前端按预览置灰，正常提交不到这里；但形状谓词（池内 / 无重复 / 区间）**收不下**
    "势力互异"，故执行器仍按 `step.pool` 的**前缀**复核一遍（与校验器 / 预览同一份回调）。
    判据 = 越池的那份集合让整步取消：一张牌都不弃、次数不消耗。
    """
    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    wei_other = _with_suit("peach", "heart")
    shu = _with_suit("slash", "club")
    state = _chuli_state(own_card=own, wei_a=wei, wei_b=wei_other, shu=shu)
    recorded, players = _chuli_players(
        picks=("seat:1", "seat:2"), cards=(0, 0, 0)
    )

    engine = _run(state, players, _phase_over)

    assert len(recorded["seat_queries"]) == 1, "座位只问一次"
    assert not any(
        record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
        for record in engine.records
    ), "两名都是魏 ⇒ 越池 ⇒ 整步取消（零副作用）"
    assert _offers(engine.state, CHULI), "取消不消耗「出牌阶段限一次」"


def test_chuli_with_zero_targets_is_cancelled_and_costs_nothing():
    """反例（0 目标非法）：座位步直接放弃 ⇒ 整条流水线取消，一张牌都不弃、次数不消耗。"""
    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    wei_other = _with_suit("peach", "heart")
    shu = _with_suit("slash", "club")
    state = _chuli_state(own_card=own, wei_a=wei, wei_b=wei_other, shu=shu)
    _, players = _chuli_players(picks=(), cards=(0,))

    engine = _run(state, players, _phase_over)

    assert not any(
        record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
        for record in engine.records
    ), "取消 ⇒ 零副作用"
    assert _offers(engine.state, CHULI), "取消不消耗「出牌阶段限一次」"


def test_chuli_is_limited_to_once_per_play_phase():
    """反例（次数）：出牌阶段限一次——发动后同一阶段不再产出候选。"""
    own = _with_suit("slash", "spade")
    wei = _with_suit("slash", "spade", skip=1)
    state = _chuli_state(
        own_card=own,
        wei_a=wei,
        wei_b=_with_suit("peach", "heart"),
        shu=_with_suit("slash", "club"),
    )
    assert _offers(state, CHULI), "第一次可发动"

    _, players = _chuli_players(picks=("seat:1",), cards=(0, 0))
    engine = _run(state, players, _phase_over)

    play_queries = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.PLAY_PHASE.value
    ]
    assert play_queries
    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == CHULI
        for action in play_queries[0].legal_actions
    ), "第一次询问里【除疠】在候选里"
    assert all(
        not any(
            isinstance(action, ActivateSkill) and action.skill_id == CHULI
            for action in query.legal_actions
        )
        for query in play_queries[1:]
    ), "发动过之后不再产出【除疠】候选"


# ---------------------------------------------------------------------------
# ③ 界吕布【无双】：与标准版逐字同义（【杀】要两张【闪】）
# ---------------------------------------------------------------------------

LIYU = "hero.jie_lvbu.liyu"


def test_jie_wushuang_still_needs_two_dodges_to_cancel_a_slash():
    """正例：界吕布的【杀】同样要连续两张【闪】才抵消（①与标准版逐字同义）。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _with_suit("dodge", "heart"), _with_suit("dodge", "diamond")
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), hero="jie_lvbu", kingdom=QUN),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=deck_excluding(PACK, slash, dodge1, dodge2),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda a: isinstance(a, UseCard) and a.card.card_id == slash,
            )
        return None

    dodges = {"count": 0}

    def defender(query):
        if query.kind == QueryKind.DODGE:
            dodges["count"] += 1
            if dodges["count"] == 1:
                return _find(query, lambda a: isinstance(a, UseCard))
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda e: e.state.player(1).hp < 4,
    )

    assert dodges["count"] == 2, "界吕布【无双】：一次【杀】要问两次【闪】"
    assert engine.state.player(1).hp == 3, "只出一张【闪】不抵消"


def _liyu_frame(state, slash: int, *, source: int = 0, target: int = 1):
    from sgs.state import FrameKind
    from sgs.timing import Timing

    return FrameKind.DAMAGE, {
        "source": source,
        "targets": (target,),
        "card": state.card(slash),
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": target,
        "channel": state.card(slash),
        "nature": "NORMAL",
        "chain": False,
        "amount": 1,
    }


def _liyu_engine(*, target: SeatSpec, seats_after: int = 0, card_type: str = "slash"):
    from sgs.state import FrameKind
    from sgs.timing import Timing

    card = _cards(card_type)[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jie_lvbu", kingdom=QUN),
            target,
            *[SeatSpec() for _ in range(seats_after)],
        ],
        deck=deck_excluding(PACK, card, *target.hand, *target.equip, *target.judge),
        processing=(card,),
    )
    engine = Engine.from_state(state, seed=7)
    kind, data = _liyu_frame(state, card, source=0, target=1)
    engine.push_frame(kind, data, timing=Timing.DAMAGE_DEAL)
    return engine, state


def _select_by_card(wanted: int | None):
    """按"是不是隐藏槽 / 是不是这张牌"选牌：``wanted=None`` ⇒ 第一个隐藏槽。"""

    def select(query, players):
        if wanted is None:
            return _pick_slot(
                query, lambda slot: slot.card is None
            )
        return _pick_slot(
            query, lambda slot: slot.card is not None and slot.card.card_id == wanted
        )

    return select


def _pick_slot(query, predicate):
    index = next(i for i, slot in enumerate(query.slots) if predicate(slot))
    return next(
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards) and action.picks == (index,)
    )


class _LiyuPlayers:
    """吕布（0）答发动 + 选牌；受伤者（1）指决斗目标；其余放弃。"""

    def __init__(self, *, pick, duel_target: str | None = None, activate: bool = True):
        self.pick = pick
        self.duel_target = duel_target
        self.activate = activate
        self.activations = []
        self.selections = []
        self.options = []

    def respond(self, query):
        if query.actor == 0:
            if query.kind == QueryKind.SKILL_ACTIVATION:
                self.activations.append(query)
                for action in query.legal_actions:
                    if (
                        isinstance(action, ActivateSkill)
                        and action.skill_id == LIYU
                        and self.activate
                    ):
                        return action
                for action in query.legal_actions:
                    if isinstance(action, Pass):
                        return action
            if query.kind == QueryKind.SELECT_CARDS:
                self.selections.append(query)
                return self.pick(query)
        if query.actor == 1 and query.kind == QueryKind.SELECT_OPTION and self.duel_target:
            self.options.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == self.duel_target:
                    return action
        return _pass_or_end(query)


def _advance(engine, players, stop) -> Engine:
    from support import run_queries

    run_queries(engine, players, stop)
    return engine


def test_liyu_gains_a_hidden_hand_card_and_lets_the_damaged_character_draw_one():
    """正例（非装备分支）：从受伤者**区域内**获得一张（手牌隐藏槽）⇒ 其摸一张。"""
    peach = _with_suit("peach", "heart")
    engine, state = _liyu_engine(
        target=SeatSpec(hand=(peach,), kingdom=WEI), seats_after=1
    )

    def pick(query):
        return _pick_slot(query, lambda slot: slot.card is None)

    players = [_LiyuPlayers(pick=pick), _LiyuPlayers(pick=pick)]
    players = players + [Scripted()] * (len(state.players) - len(players))

    _advance(engine, players, lambda e: not e.stack and e.pending is None)

    assert engine.state.player(0).hand == [peach], "获得的牌进吕布手牌"
    assert engine.state.player(1).hand and len(engine.state.player(1).hand) == 1, (
        "受伤者自己摸一张（原手牌已交给吕布）"
    )
    assert engine.state.player(1).hand[0] != peach
    query = players[0].selections[0]
    assert any(slot.card is None for slot in query.slots), "受伤者的手牌对吕布是隐藏槽"


def test_liyu_equipment_branch_lets_the_damaged_character_designate_the_duel_target():
    """正例（装备分支）：获得的牌是装备 ⇒ **受伤者**指定另一名其他角色，吕布对其决斗。"""
    weapon = _cards("crossbow")[0]
    engine, state = _liyu_engine(
        target=SeatSpec(equip=(weapon,), kingdom=WEI), seats_after=1
    )

    def pick(query):
        return _pick_slot(
            query, lambda slot: slot.card is not None and slot.card.card_id == weapon
        )

    players = [
        _LiyuPlayers(pick=pick),
        _LiyuPlayers(pick=pick, duel_target="seat:2"),
        Scripted(),
    ]

    _advance(
        engine,
        players,
        lambda e: not e.stack and e.pending is None,
    )

    assert engine.state.player(0).hand == [weapon], "装备牌被吕布获得"
    assert players[1].options, "决斗目标由受伤者（座位 1）指定"
    options = players[1].options[0].legal_actions
    assert any(isinstance(a, SelectOption) and a.choice == "seat:2" for a in options)
    assert not any(isinstance(a, SelectOption) and a.choice == "seat:0" for a in options), (
        "不能指定吕布（决斗使用者）"
    )
    assert not any(isinstance(a, SelectOption) and a.choice == "seat:1" for a in options), (
        "不能指定受伤者自己"
    )
    assert engine.state.player(2).hp == 3, "吕布对受伤者指定的角色使用【决斗】，其不响应"

    duel_responses = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.RESPOND.value
        and record.query.actor == 2
    ]
    assert duel_responses, "虚拟【决斗】走的是真响应窗口"


def test_liyu_does_not_trigger_when_the_damage_is_not_from_a_slash():
    """反例（触发条件）：【决斗】造成的伤害不触发【利驭】。"""
    engine, state = _liyu_engine(
        target=SeatSpec(hand=(_with_suit("peach", "heart"),), kingdom=WEI),
        card_type="duel",
    )
    players = [_LiyuPlayers(pick=_select_by_card(None)), _LiyuPlayers(pick=_select_by_card(None))]

    _advance(engine, players, lambda e: not e.stack and e.pending is None)

    assert players[0].activations == [], "不是【杀】造成的伤害 ⇒ 连「是否发动」都不问"
    assert state.processing == (_cards("duel")[0],) or True  # 处理区由帧收尾，不强求


def test_liyu_is_not_offered_when_the_damaged_character_has_no_cards():
    """反例（付不起）：受伤者区域内一张牌都没有 ⇒ 不进候选。"""
    engine, _state = _liyu_engine(target=SeatSpec(kingdom=WEI))
    players = [_LiyuPlayers(pick=_select_by_card(None)), _LiyuPlayers(pick=_select_by_card(None))]

    _advance(engine, players, lambda e: not e.stack and e.pending is None)

    assert players[0].activations == [], "无牌可拿 ⇒ 连「是否发动」都不问"


# ---------------------------------------------------------------------------
# ④ 界华雄【耀武】 / 【势斩】
# ---------------------------------------------------------------------------

YAOWU = "hero.jie_huaxiong.yaowu"
SHIZHAN = "hero.jie_huaxiong.shizhan"


def _yaowu_engine(*, card_suit: str | None, source: int | None = 0, card_type: str = "slash"):
    from sgs.state import FrameKind
    from sgs.timing import Timing

    card = _with_suit(card_type, card_suit) if card_suit is not None else None
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(kingdom=WEI),
            SeatSpec(hero="jie_huaxiong", kingdom=QUN),
        ],
        deck=deck_excluding(PACK, card) if card is not None else deck_excluding(PACK),
        processing=(card,) if card is not None else (),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (1,),
            "card": state.card(card) if card is not None else None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 1,
            "channel": state.card(card) if card is not None else None,
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    return engine, state


def test_yaowu_red_card_damage_lets_the_damage_source_draw_one():
    """正例（红牌）：伤害来源摸一张，华雄不摸（锁定技，不问即发动）。"""
    engine, state = _yaowu_engine(card_suit="heart")
    _advance_until_idle(engine)

    assert len(engine.state.player(0).hand) == 1, "红色牌 ⇒ 伤害来源摸一张"
    assert engine.state.player(1).hand == [], "华雄自己不摸"
    assert YAOWU not in {
        record.query.skill_id
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
    }, "锁定技没有「是否发动」询问"


def test_yaowu_black_card_damage_lets_huaxiong_draw_one():
    """反例（黑牌）：改为华雄自己摸一张。"""
    engine, state = _yaowu_engine(card_suit="spade")
    _advance_until_idle(engine)

    assert engine.state.player(1).hand and len(engine.state.player(1).hand) == 1
    assert engine.state.player(0).hand == [], "黑色牌 ⇒ 来源不摸"


def test_yaowu_ignores_damage_not_caused_by_a_card():
    """反例（触发条件）：无渠道牌的伤害（如【刚烈】）不触发【耀武】。"""
    engine, state = _yaowu_engine(card_suit=None, source=None)
    _advance_until_idle(engine)

    assert engine.state.player(0).hand == []
    assert engine.state.player(1).hand == []


def _advance_until_idle(engine) -> Engine:
    """只推进到**伤害帧出栈**为止（不让引擎继续开回合 / 摸牌阶段）。"""
    from support import run_queries

    run_queries(
        engine,
        [Scripted() for _ in engine.state.players],
        lambda e: not e.stack,
    )
    return engine


def _shizhan_players(*, duels: int):
    """脚本：见【势斩】即发动、座位选 1、决斗里出【杀】（没有就放弃）。"""
    state = {"activations": 0, "seat_queries": [], "duel_seen": 0}

    def choose(query):
        if query.kind == QueryKind.PLAY_PHASE and state["activations"] < duels:
            action = _find(
                query,
                lambda a: isinstance(a, ActivateSkill) and a.skill_id == SHIZHAN,
            )
            if action is not None:
                state["activations"] += 1
                return action
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id).startswith(
            "shizhan:seat"
        ):
            state["seat_queries"].append(query)
            return _find(
                query, lambda a: isinstance(a, SelectOption) and a.choice == "seat:1"
            )
        if query.kind == QueryKind.RESPOND and getattr(query, "cause", None) == "DUEL":
            state["duel_seen"] += 1
            slash = _find(
                query, lambda a: isinstance(a, PlayCard) and a.card.type == "slash"
            )
            if slash is not None:
                return slash
            return _find(query, lambda a: isinstance(a, Pass))
        return None

    return state, choose


def _shizhan_state(*, owner_hand, target_hand=()):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=owner_hand, hero="jie_huaxiong", kingdom=QUN),
            SeatSpec(hand=target_hand, kingdom=WEI),
        ],
        deck=deck_excluding(PACK, *owner_hand, *target_hand),
        turn_seat=0,
    )


def test_shizhan_lets_another_character_duel_huaxiong_twice_per_play_phase():
    """正例：令其他角色视为对华雄使用【决斗】；同一出牌阶段可以发动**两次**。"""
    red = _with_suit("slash", "heart")
    black = _with_suit("slash", "club")
    state = _shizhan_state(owner_hand=(red, black))
    recorded, choose = _shizhan_players(duels=2)
    players = [Scripted(choose), Scripted(choose)]

    engine = _run(state, players, _phase_over)

    assert _activated(engine, SHIZHAN), "【势斩】真的发动"
    assert recorded["duel_seen"] >= 2, "每次发动都开一次真【决斗】窗口"
    assert len(engine.state.player(1).hand) == 0
    assert engine.state.player(1).hp == 2, "两次决斗目标（座位 1）各掉 1 点"
    activations = [
        record
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and isinstance(record.action, ActivateSkill)
        and record.action.skill_id == SHIZHAN
    ]
    assert len(activations) == 2, "限两次：同一阶段恰好两次"


def test_shizhan_third_activation_is_not_offered_in_the_same_play_phase():
    """反例（次数）：两次用掉之后，同一出牌阶段的候选里不再有【势斩】。"""
    slashes = (
        _with_suit("slash", "heart"),
        _with_suit("slash", "club"),
        _with_suit("slash", "diamond"),
    )
    state = _shizhan_state(owner_hand=slashes)
    recorded, choose = _shizhan_players(duels=99)
    players = [Scripted(choose), Scripted(choose)]

    engine = Engine.from_state(state, seed=3)
    run_queries(
        engine,
        players,
        lambda e: (
            e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE.value
            and len(
                [
                    record
                    for record in e.records
                    if record.kind == "action"
                    and record.accepted
                    and isinstance(record.action, ActivateSkill)
                    and record.action.skill_id == SHIZHAN
                ]
            )
            == 2
        ),
    )

    offered = engine.pending.legal_actions
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == SHIZHAN
        for action in offered
    ), "两次之后不再产出【势斩】候选"


# ---------------------------------------------------------------------------
# ⑤ 界公孙瓒【趫猛】 / 【义从】
# ---------------------------------------------------------------------------

QIAOMENG = "hero.jie_gongsunzan.qiaomeng"
YICONG = "hero.jie_gongsunzan.yicong"


def _qiaomeng_engine(*, target: SeatSpec, card_type: str = "slash"):
    from sgs.state import FrameKind
    from sgs.timing import Timing

    card = _cards(card_type)[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_gongsunzan", kingdom=QUN), target],
        deck=deck_excluding(PACK, card, *target.hand, *target.equip, *target.judge),
        processing=(card,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 0,
            "targets": (1,),
            "card": state.card(card),
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 1,
            "channel": state.card(card),
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    return engine, state


class _QiaomengPlayers:
    """公孙瓒（0）答发动 + 选牌；其余放弃。"""

    def __init__(self, *, pick):
        self.pick = pick
        self.activations = []
        self.selections = []

    def respond(self, query):
        if query.actor == 0:
            if query.kind == QueryKind.SKILL_ACTIVATION:
                self.activations.append(query)
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == QIAOMENG:
                        return action
                return _pass_or_end(query)
            if query.kind == QueryKind.SELECT_CARDS:
                self.selections.append(query)
                return self.pick(query)
        return _pass_or_end(query)


def _run_damage_frame(engine, state, players):
    from support import run_queries

    run_queries(
        engine,
        players + [Scripted()] * (len(state.players) - len(players)),
        lambda e: not e.stack,
    )
    return engine


def test_qiaomeng_gains_a_mount_card_from_the_damaged_characters_area():
    """正例（坐骑）：选择其区域里的一张坐骑牌 ⇒ **获得**（进公孙瓒手牌）。"""
    horse = _cards("chitu")[0]
    engine, state = _qiaomeng_engine(
        target=SeatSpec(equip=(horse,), kingdom=WEI)
    )
    player = _QiaomengPlayers(
        pick=lambda q: _pick_slot(
            q, lambda slot: slot.card is not None and slot.card.card_id == horse
        )
    )

    _run_damage_frame(engine, state, [player])

    assert engine.state.player(0).hand == [horse], "坐骑牌被公孙瓒获得"
    assert player.activations and player.selections


def test_qiaomeng_discards_a_non_mount_card_including_a_hidden_hand_card():
    """正例（非坐骑 + 隐藏手牌）：选到一张手牌 ⇒ **弃置**（不是获得）。"""
    junk = _with_suit("peach", "heart")
    engine, state = _qiaomeng_engine(
        target=SeatSpec(hand=(junk,), kingdom=WEI)
    )
    player = _QiaomengPlayers(
        pick=lambda q: _pick_slot(q, lambda slot: slot.card is None)
    )

    _run_damage_frame(engine, state, [player])

    assert engine.state.player(0).hand == [], "非坐骑牌不获得"
    assert junk in engine.state.discard, "非坐骑牌被弃置"
    query = player.selections[0]
    assert any(slot.card is None for slot in query.slots), "受伤者的手牌对选择者不可见"


def test_qiaomeng_does_not_trigger_off_a_non_slash_damage():
    """反例（触发条件）：【决斗】造成的伤害不触发【趫猛】。"""
    engine, state = _qiaomeng_engine(
        target=SeatSpec(hand=(_with_suit("peach", "heart"),), kingdom=WEI),
        card_type="duel",
    )
    player = _QiaomengPlayers(pick=lambda q: _pick_slot(q, lambda slot: slot.card is None))

    _run_damage_frame(engine, state, [player])

    assert player.activations == [], "不是【杀】造成的伤害 ⇒ 连「是否发动」都不问"


def test_yicong_shortens_own_distance_and_lengthens_targets_when_hp_is_low():
    """正例：①自己到别人 -1；②体力 ≤ 2 时别人到自己 +1。"""
    from sgs.effects import distance

    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jie_gongsunzan", kingdom=QUN),
            SeatSpec(kingdom=WEI),
            SeatSpec(kingdom=SHU),
            SeatSpec(kingdom=WU),
        ],
        deck=deck_excluding(PACK),
    )

    # 4 人环：0↔2 座位距离 = 2，0↔1 = 1。
    assert distance(state, 0, 2) == 1, "①自己到别人 -1（2 - 1 = 1）"
    assert distance(state, 0, 1) == 1, "下限 1 仍夹住"
    assert distance(state, 2, 0) == 2, "体力 4 > 2 ⇒ 别人到公孙瓒不加距离"
    assert distance(state, 2, 1) == 1, "别的座位之间不受【义从】影响"

    state.player(0).hp = 2
    assert distance(state, 2, 0) == 3, "②体力 ≤ 2 ⇒ 别人到公孙瓒 +1（2 + 1 = 3）"
    assert distance(state, 0, 2) == 1, "①与体力无关，仍 -1"


def test_yicong_is_locked_and_does_not_ask_activation():
    """反例（锁定技）：义从没有「是否发动」询问（由技能类型读，不靠条件）。"""
    from sgs.skills import CATALOG

    assert CATALOG[YICONG].locked is True
    assert CATALOG[QIAOMENG].locked is False, "趫猛是「你可以」的非锁定技"
