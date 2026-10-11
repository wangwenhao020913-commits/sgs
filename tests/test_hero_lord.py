"""F 族：主公技（势力作用域 / 求助窗口 / 回复量修饰）（票 09）。

落点与票 09 §验收一致，全部走**外部缝**：

- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的**记录流**（求助询问的对象与顺序、
  代打的实体牌去向、回复后的体力）；
- 只读小工具 ``same_kingdom``（势力作用域的唯一读法）。

三条技能的逐字语义（`三国杀详细规则.txt` 第九章 933–1054 行）：

- 曹操【护驾】（锁定：非）：需要【闪】时令**其他魏势力角色**打出【闪】（视为由曹操使用或打出）；
- 刘备【激将】（非锁定）：需要【杀】时令**其他蜀势力角色**打出【杀】（视为由刘备使用或打出）；
- 孙权【救援】（锁定）：**其他吴势力角色**对孙权使用【桃】时，回复量 +1。

【护驾】【激将】与【八卦阵】同构：提供者在候选里以 ``ActivateSkill`` 出现、
"每提供者只问一次"由候选生成器统一过滤；选中才开**势力求助窗口**（短路式，非无懈的收集式）。
"""

from __future__ import annotations

from types import MappingProxyType

from sgs.data import Identity, Kingdom, load_content_pack
from sgs.effects import CandidateScope, CarrierKind, Modifier, ModifierPoint
from sgs.engine import Engine
from sgs.skills import CATALOG, Hook, SkillDef, SkillType, resolve_instances, same_kingdom
from sgs.skills import catalog as catalog_module
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    PlayCard,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")

HUJIA = "hero.caocao.hujia"
JIJIANG = "hero.liubei.jijiang"
JIUYUAN = "hero.sunquan.jiuyuan"


# ---------------------------------------------------------------------------
# 通用脚本玩家（与票 08 的 ``Scripted`` 同形）
# ---------------------------------------------------------------------------


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _of_type(query, action_type, *, type_id=None):
    return _find(
        query,
        lambda action: isinstance(action, action_type)
        and (type_id is None or getattr(action, "card", None) is not None and action.card.type == type_id),
    )


def _use(query, card_id: int, target: int):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == (target,),
    )


def _queries(engine, kind):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _respond_queries(engine, cause):
    return [
        query
        for query in _queries(engine, "RESPOND")
        if getattr(query, "cause", None) is cause
    ]


def _fallback(query):
    if query.kind == "PLAY_PHASE":
        return _of_type(query, EndPhase) or query.legal_actions[0]
    if query.kind in ("DODGE", "RESPOND", "PEACH"):
        return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
    return query.legal_actions[0]


class Scripted:
    """一个座位：先跑 ``handler``；返回 ``None`` 时退到最小合法应答。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return _fallback(query)


# ---------------------------------------------------------------------------
# ① 势力作用域：只读小工具 same_kingdom（不建字段）
# ---------------------------------------------------------------------------


def test_same_kingdom_reads_the_public_kingdom_field():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="caocao", kingdom=Kingdom.WEI),
            SeatSpec(hero="simayi", kingdom=Kingdom.WEI),
            SeatSpec(hero="liubei", kingdom=Kingdom.SHU),
            SeatSpec(),
        ],
    )

    assert same_kingdom(state, 0, 1) is True, "同势力 ⇒ True"
    assert same_kingdom(state, 0, 2) is False, "异势力 ⇒ False"
    assert same_kingdom(state, 0, 3) is False, "无武将（势力未知）⇒ False"
    assert same_kingdom(state, 3, 3) is False, "势力未知的座位与自己也不算同势力"


def test_window_cause_has_the_lord_request_value():
    """票 09 §要做什么 5：``WindowCause`` 闭集只许增，M3 加 ``LORD_REQUEST``。"""
    assert WindowCause.LORD_REQUEST.value == "LORD_REQUEST"


def test_the_three_lord_skills_produce_no_instance_in_an_identity_free_game():
    """票 09 §验收 6：无身份局（2 人）里三个主公技全不产出实例。"""
    for hero, skill_id in (("caocao", HUJIA), ("liubei", JIJIANG), ("sunquan", JIUYUAN)):
        state = build_state(
            pack=PACK,
            seats=[SeatSpec(hero=hero), SeatSpec()],
            turn_seat=0,
        )

        assert skill_id not in [
            instance.skill_id for instance in resolve_instances(state)
        ], f"无身份局里 {skill_id} 不应产出实例"


# ---------------------------------------------------------------------------
# ⑤ 孙权【救援】：回复量修饰（锁定技，其他吴势力角色对孙权用【桃】时 +1）
# ---------------------------------------------------------------------------


def _rescue_state(*, rescuer_kingdom, self_rescue: bool):
    """座位 0 用一张【杀】把孙权（座位 1）打到 0；座位 2 持【桃】。

    ``self_rescue`` 时【桃】在孙权自己手里（验"自己对自己用桃不受影响"）。
    """
    slash = _cards("slash")[0]
    peach = _cards("peach")[0]
    sunquan_hand = (peach,) if self_rescue else ()
    seat0_hand = (slash,)
    seat2_hand = () if self_rescue else (peach,)
    return slash, peach, build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=seat0_hand),
            SeatSpec(hp=1, hp_max=4, hand=sunquan_hand, hero="sunquan",
                     identity=Identity.LORD, kingdom=Kingdom.WU),
            SeatSpec(hand=seat2_hand, kingdom=rescuer_kingdom),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(PACK.deck.cards))
            if card_id not in (slash, peach)
        ),
        turn_seat=0,
    )


def _run_rescue(*, rescuer_kingdom, self_rescue: bool):
    slash, peach, state = _rescue_state(rescuer_kingdom=rescuer_kingdom, self_rescue=self_rescue)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, 1)
        return None

    def lord(query):
        if query.kind == "DODGE":
            return _of_type(query, Pass)
        if query.kind == "PEACH" and self_rescue:
            return _of_type(query, UseCard, type_id="peach")
        return None

    def rescuer(query):
        if query.kind == "PEACH" and not self_rescue:
            return _of_type(query, UseCard, type_id="peach")
        return None

    return run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(lord), Scripted(rescuer)],
        lambda e: peach in e.state.discard,
    )


def test_jiuyuan_adds_one_when_a_wu_character_rescues_sunquan_with_a_peach():
    """票 09 §验收 5：吴势力角色对孙权的【桃】回复 2 点（基础 1 + 1）。"""
    engine = _run_rescue(rescuer_kingdom=Kingdom.WU, self_rescue=False)

    assert engine.state.player(1).hp == 2, "【救援】锁定技：其他吴势力角色的【桃】回复量 +1"


def test_jiuyuan_does_not_boost_a_non_wu_rescuer():
    """票 09 §验收 5：非吴势力角色回复 1 点。"""
    engine = _run_rescue(rescuer_kingdom=Kingdom.SHU, self_rescue=False)

    assert engine.state.player(1).hp == 1, "非吴势力角色不受【救援】影响"


def test_jiuyuan_does_not_boost_sunquans_own_peach():
    """票 09 §验收 5：孙权自己对自己用桃不受影响（"其他吴势力角色"的"其他"）。"""
    engine = _run_rescue(rescuer_kingdom=Kingdom.WU, self_rescue=True)

    assert engine.state.player(1).hp == 1, "孙权自救不受【救援】影响"


# ---------------------------------------------------------------------------
# ①②③④ 曹操【护驾】：势力求助窗口（需要【闪】）
# ---------------------------------------------------------------------------


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _specs_state(specs, *, turn_seat):
    """按座位声明构造局面；牌堆剔除所有已发出去的牌（``locate`` 先查牌堆，不能撞）。"""
    used: set[int] = set()
    for spec in specs:
        used.update(spec.hand)
        used.update(spec.equip)
        used.update(spec.judge)
    return build_state(
        pack=PACK,
        seats=specs,
        deck=tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used),
        turn_seat=turn_seat,
    )


def _slash_attacker(slash: int, target: int):
    def handler(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, slash, target)
        return None

    return handler


def _hujia_lord():
    def handler(query):
        if query.kind == "DODGE":
            return _of_type(query, ActivateSkill, type_id=None)
        return None

    return handler


def _lord_identity_gate_state(*, identity):
    slash = _cards("slash")[0]
    specs = [
        SeatSpec(hand=(slash,)),
        SeatSpec(hero="caocao", identity=identity, kingdom=Kingdom.WEI),
        SeatSpec(hand=(_cards("dodge")[0],), kingdom=Kingdom.WEI),
    ]
    return slash, _specs_state(specs, turn_seat=0)


def _run_gate(*, identity):
    slash, state = _lord_identity_gate_state(identity=identity)
    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(_slash_attacker(slash, 1)), Scripted(_hujia_lord()), Scripted()],
        lambda e: slash in e.state.discard and not e.stack,
    )
    return engine


def test_a_non_lord_hujia_is_absent_from_the_dodge_candidates():
    """票 09 §验收 1：非主公持有曹操 ⇒ 护驾实例不存在、候选里也没有它。"""
    engine = _run_gate(identity=Identity.REBEL)
    dodge_query = _queries(engine, "DODGE")[0]

    assert not [
        action
        for action in dodge_query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == HUJIA
    ], "非主公的武将牌上根本没有护驾"


def test_a_lord_hujia_appears_as_a_dodge_candidate():
    """票 09 §验收 1：主公持有曹操 ⇒ 在需要【闪】时出现"视为发动"候选。"""
    engine = _run_gate(identity=Identity.LORD)
    dodge_query = _queries(engine, "DODGE")[0]

    assert [
        action
        for action in dodge_query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == HUJIA
    ], "主公的护驾在需要【闪】时进候选"


def _hujia_state(*, ally_specs, turn_seat=0, attack_seat=0, target=1):
    """曹操（座位 1，主公）被座位 ``attack_seat`` 用【杀】打；``ally_specs`` 是其余座位。

    返回 ``(slash, state)``；座位 2..n 按 ``ally_specs`` 展开（座位 0 是攻击者）。
    """
    slash = _cards("slash")[0]
    specs: list[SeatSpec] = [
        SeatSpec(hand=(slash,)),
        SeatSpec(hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
    ]
    specs.extend(ally_specs)
    return slash, _specs_state(specs, turn_seat=turn_seat)


def _run_hujia(*, ally_specs, ally_policy, turn_seat=0, stop=None):
    """驱动一次【护驾】求助：曹操答"视为发动"，同势力角色按 ``ally_policy`` 应答。"""
    slash, state = _hujia_state(ally_specs=ally_specs, turn_seat=turn_seat)
    dodge_ids = {
        card_id
        for spec in ally_specs
        for card_id in spec.hand
        if PACK.deck.cards[card_id].type == "dodge"
    }

    players = [Scripted(_slash_attacker(slash, 1)), Scripted(_hujia_lord())]
    for offset, _spec in enumerate(ally_specs):
        seat = 2 + offset
        players.append(Scripted(lambda query, s=seat: ally_policy(s, query)))

    def default_stop(engine):
        return bool(dodge_ids) and all(
            card_id in engine.state.discard for card_id in sorted(dodge_ids)
        )

    return run_until(
        Engine.from_state(state, seed=1),
        players,
        stop if stop is not None else default_stop,
    )


def _plays_dodge(query):
    return _of_type(query, PlayCard, type_id="dodge")


def test_hujia_asks_the_same_kingdom_ally_and_the_ally_plays_a_real_dodge():
    """票 09 §验收 2/3：同势力角色被打出【闪】⇒ 视为曹操打出；实体牌进弃牌堆、不占次数。"""
    dodge = _cards("dodge")[0]
    engine = _run_hujia(
        ally_specs=[SeatSpec(hand=(dodge,), kingdom=Kingdom.WEI)],
        ally_policy=lambda seat, query: _of_type(query, PlayCard, type_id="dodge")
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        else None,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert [query.actor for query in requests] == [2], "只问同势力的其他角色"
    assert all(query.required == "dodge" for query in requests)
    assert dodge in engine.state.discard, "代打消耗响应者的实体牌"
    assert engine.state.player(2).card_use_counts == {}, "代打不占响应者自己的使用次数"
    assert engine.state.player(1).hp == 4, "需要被满足 ⇒ 曹操不受【杀】伤害"
    assert engine.state.card(dodge).type == "dodge"


def test_hujia_asks_counterclockwise_from_the_turn_seat_and_stops_at_the_first_success():
    """票 09 §验收 2：从当前回合角色起逆时针；首个打出即停，后面的同势力角色不被问。"""
    dodge_a, dodge_b = _cards("dodge")[0], _cards("dodge")[1]

    def policy(seat, query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            if seat == 4:
                return _of_type(query, PlayCard, type_id="dodge")
            return _of_type(query, Pass)
        return None

    # 5 座位：座位 2 是攻击者（= 当前回合角色），曹操在座位 1，其他魏势力在座位 0 / 4。
    # 逆时针从座位 2 起 ⇒ 座位 4 先于座位 0 被问（若按座位升序就会反）。
    slash = _cards("slash")[0]
    specs = [
        SeatSpec(hand=(dodge_a,), kingdom=Kingdom.WEI),
        SeatSpec(hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
        SeatSpec(hand=(slash,)),
        SeatSpec(),
        SeatSpec(hand=(dodge_b,), kingdom=Kingdom.WEI),
    ]
    state = _specs_state(specs, turn_seat=2)

    players = [
        Scripted(lambda query: policy(0, query)),
        Scripted(_hujia_lord()),
        Scripted(_slash_attacker(slash, 1)),
        Scripted(),
        Scripted(lambda query: policy(4, query)),
    ]
    engine = run_until(Engine.from_state(state, seed=1), players, lambda e: dodge_b in e.state.discard)

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert [query.actor for query in requests] == [4], (
        "逆时针从当前回合角色（座位 2）起：座位 4 先于座位 0 被问；首个成功即止"
    )
    assert dodge_a not in engine.state.discard, "座位 0 根本没收到询问"


def test_hujia_continues_counterclockwise_after_a_pass():
    """票 09 §验收 2：某人 Pass ⇒ 轮到下一人；已 Pass 者不被问第二次。"""
    dodge_a, dodge_b = _cards("dodge")[0], _cards("dodge")[1]

    def policy(seat, query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            if seat == 3:
                return _of_type(query, PlayCard, type_id="dodge")
            return _of_type(query, Pass)
        return None

    specs = [
        SeatSpec(hand=(_cards("slash")[0],)),
        SeatSpec(hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
        SeatSpec(hand=(dodge_a,), kingdom=Kingdom.WEI),
        SeatSpec(hand=(dodge_b,), kingdom=Kingdom.WEI),
    ]
    state = _specs_state(specs, turn_seat=0)

    players = [
        Scripted(_slash_attacker(_cards("slash")[0], 1)),
        Scripted(_hujia_lord()),
        Scripted(lambda query: policy(2, query)),
        Scripted(lambda query: policy(3, query)),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: dodge_b in e.state.discard,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert [query.actor for query in requests] == [2, 3], "座位 2 Pass ⇒ 轮到座位 3；每人一次"
    assert dodge_b in engine.state.discard
    assert engine.state.player(1).hp == 4


def test_hujia_never_asks_a_non_wei_character():
    """票 09 §验收 4：非魏势力角色不收到护驾求助询问。"""
    wei_dodge, shu_dodge = _cards("dodge")[0], _cards("dodge")[1]

    def policy(seat, query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            return _of_type(query, PlayCard, type_id="dodge")
        return None

    specs = [
        SeatSpec(hand=(_cards("slash")[0],)),
        SeatSpec(hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
        SeatSpec(hand=(shu_dodge,), kingdom=Kingdom.SHU),
        SeatSpec(hand=(wei_dodge,), kingdom=Kingdom.WEI),
    ]
    state = _specs_state(specs, turn_seat=0)

    players = [
        Scripted(_slash_attacker(_cards("slash")[0], 1)),
        Scripted(_hujia_lord()),
        Scripted(lambda query: policy(2, query)),
        Scripted(lambda query: policy(3, query)),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: wei_dodge in e.state.discard,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert [query.actor for query in requests] == [3], "只有魏势力角色被问"
    assert shu_dodge not in engine.state.discard, "蜀势力角色的牌没有被动过"


def test_the_lord_request_is_short_circuit_unlike_the_collected_nullify_window():
    """票 09 §验收 2 的**对照断言**：无懈是收集式（问遍全体、收齐后取最高优先级），

    求助是短路式（首个成功即止、后面的同势力角色根本不收到询问）。
    """
    ex_nihilo = _cards("ex_nihilo")[0]
    nullify = _cards("nullify")[0]
    specs = [
        SeatSpec(hand=(ex_nihilo,)),
        SeatSpec(),
        SeatSpec(hand=(nullify,)),
    ]
    state = _specs_state(specs, turn_seat=0)

    def caster(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="ex_nihilo")
        return None

    def nullifier(query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.NULLIFY:
            return _of_type(query, UseCard, type_id="nullify")
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(caster), Scripted(), Scripted(nullifier)],
        lambda e: nullify in e.state.discard,
    )
    nullify_queries = _respond_queries(engine, WindowCause.NULLIFY)
    outer = nullify_queries[0].window_frame

    assert [query.actor for query in nullify_queries if query.window_frame == outer] == [0, 1, 2], (
        "收集式无懈窗口：座位 2 打出前，座位 0 / 1 也各拿到一次询问（与求助的短路式相反）"
    )


# ---------------------------------------------------------------------------
# ②⑦ 刘备【激将】：势力求助窗口（需要【杀】，南蛮 / 决斗，可在别人的回合）
# ---------------------------------------------------------------------------


def _jijiang_lord():
    def handler(query):
        if query.kind == "RESPOND":
            return _of_type(query, ActivateSkill, type_id=None)
        return None

    return handler


def test_jijiang_works_in_another_characters_turn_and_the_ally_plays_a_real_slash():
    """票 09 §验收 2/4/7：南蛮窗口里刘备令其他蜀势力角色打出【杀】（发生在座位 0 的回合）。"""
    barbarian, slash = _cards("barbarian_invasion")[0], _cards("slash")[0]
    specs = [
        SeatSpec(hand=(barbarian,)),
        SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
        SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
        SeatSpec(kingdom=Kingdom.WEI),
    ]
    state = _specs_state(specs, turn_seat=0)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(_jijiang_lord()), Scripted(ally), Scripted()],
        lambda e: slash in e.state.discard,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert engine.state.turn_seat == 0, "求助发生在别人（座位 0）的回合里"
    assert [query.actor for query in requests] == [2], "只问其他蜀势力角色"
    assert all(query.required == "slash" for query in requests)
    assert slash in engine.state.discard, "代打消耗响应者的实体牌"
    assert engine.state.player(2).card_use_counts == {}, "代打不占响应者的使用次数"
    assert engine.state.player(1).hp == 4, "需要被满足 ⇒ 刘备不受【南蛮入侵】伤害"


def test_jijiang_never_asks_a_non_shu_character():
    """票 09 §验收 4：非蜀势力角色不收到激将求助询问。"""
    barbarian, slash = _cards("barbarian_invasion")[0], _cards("slash")[0]
    wei_slash = _cards("slash")[1]
    specs = [
        SeatSpec(hand=(barbarian,)),
        SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
        SeatSpec(hand=(wei_slash,), kingdom=Kingdom.WEI),
        SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
    ]
    state = _specs_state(specs, turn_seat=0)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(_jijiang_lord()), Scripted(lambda q: ally(q)), Scripted(lambda q: ally(q))],
        lambda e: slash in e.state.discard,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)

    assert [query.actor for query in requests] == [3], "只有蜀势力角色被问"
    assert wei_slash not in engine.state.discard


def test_jijiang_answers_a_duel_and_hands_the_response_back():
    """票 09 §验收 7：【决斗】里刘备令其他蜀势力角色打出【杀】，响应权交回对方。"""
    duel, slash = _cards("duel")[0], _cards("slash")[0]
    specs = [
        SeatSpec(hand=(duel,)),
        SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
        SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
    ]
    state = _specs_state(specs, turn_seat=0)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _use(query, duel, 1)
        return None

    def ally(query):
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.LORD_REQUEST:
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = run_until(
        Engine.from_state(state, seed=1),
        [Scripted(attacker), Scripted(_jijiang_lord()), Scripted(ally)],
        lambda e: e.state.player(0).hp < 4,
    )

    duel_queries = _respond_queries(engine, WindowCause.DUEL)

    assert [query.actor for query in duel_queries] == [1, 0], (
        "刘备借【杀】满足了这一次响应 ⇒ 响应权交给对方（座位 0）"
    )
    assert engine.state.player(1).hp == 4, "刘备没有受伤"
    assert engine.state.player(0).hp == 3, "对方放弃 ⇒ 对方受到 1 点伤害"


# ---------------------------------------------------------------------------
# ① 跨座位注入是合法用法（只验证、不实现：`03` §4「技能描述模型」的冻结面约定）
# ---------------------------------------------------------------------------

INJECTED = "hero.caocao.jianxiong"
"""票 05 尚未登记的技能 id（数据文件里有、``CATALOG`` 里没有）——注入假定义用。"""


def _install(monkeypatch, *definitions: SkillDef) -> None:
    """把假武将定义并进 ``catalog.CATALOG``（与 ``test_hero_skills.py`` 同姿态）。"""
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, **{d.skill_id: d for d in definitions}}),
    )


def _cross_seat_offer(state, ctx, current):
    """【黄天】式的形状：**持有者**给**别的座位**的 ``PLAY_PHASE`` 注入候选（票 09 §要做什么 2）。

    这是"回调自己比座位"的直接兑现——``ctx.owner != ctx.subject`` 是**合法**的注入，
    不是引擎约束；本票只验证机制不堵死，不实现张角。
    """
    del state
    if ctx.scope is not CandidateScope.PLAY or ctx.owner == ctx.subject:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def test_candidates_can_be_injected_into_another_seats_play_phase(monkeypatch):
    """【黄天】式：``ctx.owner`` = 持有者、``ctx.subject`` = 别的角色 ⇒ 候选照样注入。"""
    from sgs.effects import legal_actions_for
    from sgs.state import QueryKind

    _install(
        monkeypatch,
        SkillDef(
            skill_id=INJECTED,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.CANDIDATES, _cross_seat_offer),),
        ),
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao"), SeatSpec()],
        turn_seat=1,
    )

    other = legal_actions_for(state, 1, QueryKind.PLAY_PHASE, answers_seq=0)
    own = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=0)

    assert [
        action
        for action in other
        if isinstance(action, ActivateSkill) and action.skill_id == INJECTED
    ], "持有者可以把候选注入别人的出牌阶段询问"
    assert not [
        action
        for action in own
        if isinstance(action, ActivateSkill) and action.skill_id == INJECTED
    ], "注入条件是回调自己写的（示例里持有者自己的询问不注入）"


def test_a_hook_can_fire_on_another_seats_event_and_compare_the_source_kingdom(monkeypatch):
    """【暴虐】式：钩子挂在别人的事件上，回调比 ``frame.data["source"]`` 判势力。"""
    seen: list[tuple[int | None, bool]] = []

    def betray_hook(host, frame):
        owner = frame.data.get("skill_owner")
        source = frame.data.get("source")
        seen.append((source, source is not None and same_kingdom(host.state, owner, source)))
        return None

    _install(
        monkeypatch,
        SkillDef(
            skill_id=INJECTED,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.DAMAGE_AFTER_TAKE, betray_hook),),
        ),
    )
    slash = _cards("slash")[0]
    for source_kingdom, expected in ((Kingdom.WEI, True), (Kingdom.SHU, False)):
        seen.clear()
        specs = [
            SeatSpec(hero="caocao", kingdom=Kingdom.WEI),
            SeatSpec(hand=(slash,), kingdom=source_kingdom),
            SeatSpec(kingdom=Kingdom.WEI),
        ]
        state = _specs_state(specs, turn_seat=1)
        run_until(
            Engine.from_state(state, seed=1),
            [Scripted(), Scripted(_slash_attacker(slash, 2)), Scripted()],
            lambda e: e.state.player(2).hp < 4,
        )

        assert seen == [(1, expected)], (
            "座位 0 的钩子在**座位 1 造成伤害**的事件上触发，并按事件来源判势力"
        )


