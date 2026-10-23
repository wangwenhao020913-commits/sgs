"""M5-07：启发式 AI 的属性攻防 + 连环 / 酒取舍（spec D13 / §6 A2 A3）。

落点全部是**外部缝**：

- ``HeuristicPlayer.respond(query) -> Action``（唯一接口，不碰私有打分函数）；
- 真局面 + 真候选（``Engine.from_state`` / ``legal_actions_for``），应答只经
  :class:`HeuristicPlayer` 观测；
- 公开信息：只读 ``query.view`` 的**公开事实**——目标穿戴的防具、横置 / 酒状态、
  手牌**张数**、判定区；不读对手手牌牌面（隐藏手牌不变性由
  ``tests/test_heuristic_ai.py`` 的既有断言继续钉住）。

四条构造局面（票面「要做什么」的 1–4）：

1. 火杀 vs 藤甲：同时有火杀与普通杀 ⇒ 选火杀（普通杀无效、火杀 +1）；
2. 普通杀 vs 藤甲：另有合法目标 ⇒ 避开藤甲目标，不把【杀】浪费在无效目标上；
3. 酒 + 杀斩杀：目标在斩杀线内 ⇒ 先【酒】再【杀】；杀不掉时不用【酒】；
4. 铁索 / 火攻有收益时使用、无收益时铁索**重铸**（不是空放）。

160 张档的全 AI 种子判据住 ``tests/test_all_ai_games.py``（本票扩到 ≥8 个固定种子）。
"""

from __future__ import annotations

from sgs.agents import HeuristicPlayer
from sgs.data import Identity, load_content_pack
from sgs.engine import Engine, RandomSource
from sgs.effects import legal_actions_for
from sgs.state import (
    EndPhase,
    Pass,
    PeachQuery,
    PlayPhaseQuery,
    QueryKind,
    Reforge,
    SelectCards,
    UseCard,
    project,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 张档：新牌（火杀 / 藤甲 / 铁索 / 火攻 / 酒）只在显式选档时出现。"""

FIRE_SLASH = cards_of_type(PACK, "fire_slash")
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")
WINE = cards_of_type(PACK, "wine")
IRON_CHAIN = cards_of_type(PACK, "iron_chain")
FIRE_ATTACK = cards_of_type(PACK, "fire_attack")
VINE_ARMOR = cards_of_type(PACK, "vine_armor")
SILVER_LION = cards_of_type(PACK, "silver_lion")
QINGGANG_SWORD = cards_of_type(PACK, "qinggang_sword")

AI_SEED = 7
"""构造局面用的 AI 子流种子（同种子逐字节可复现；平分由它打破）。"""


def _inert_draw(*used: int) -> tuple[int, int]:
    """摸牌阶段的两张「惰性牌」（【闪】），避开 ``used``（手里 / 装备区的实体牌）。

    【闪】在出牌阶段没有使用候选，故摸到它们不改变决策面；避开 ``used`` 保证构造局面
    **不出现重复的实体牌**（牌守恒的局部形态）。
    """
    pair = tuple(card_id for card_id in DODGE if card_id not in used)[:2]
    assert len(pair) == 2, "【闪】不够两张（构造局面写错了）"
    return (pair[0], pair[1])


def _deck_with_top(*top: int, exclude: tuple[int, ...] = ()) -> tuple[int, ...]:
    """牌堆 = 指定的牌顶顺序 + 其余全部牌（牌守恒：``exclude`` 是被搬进手牌 / 装备区的）。"""
    used = set(top) | set(exclude)
    return (*top, *(index for index in range(len(PACK.deck.cards)) if index not in used))


def _minimal(query):
    """最小合法应答：出牌阶段结束、其余能放弃就放弃。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(
            action for action in query.legal_actions if isinstance(action, EndPhase)
        )
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Minimal:
    """非被测座位的脚本参与者：只做最小合法应答。"""

    def respond(self, query):
        return _minimal(query)


def _advance(engine, players, stop, *, limit: int = 40_000) -> Engine:
    """手动推进到 ``stop(engine)`` 成立（与 ``tests/test_m5_wine.py`` 同一驱动姿态）。"""
    for _ in range(limit):
        if stop(engine) or engine.finished:
            return engine
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _at_play_phase(seat: int = 0):
    return lambda engine: (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == seat
    )


def _engine(seats, *, deck: tuple[int, ...], turn_seat: int = 0, seed: int = 1) -> Engine:
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
    )
    return Engine.from_state(state, seed=seed)


def _ai(seed: int = AI_SEED) -> HeuristicPlayer:
    return HeuristicPlayer(RandomSource(seed).substream("ai"))


def _play_phase_query(state, seat: int = 0) -> PlayPhaseQuery:
    """与引擎同形的 ``PLAY_PHASE`` 询问（候选由 ``legal_actions_for`` 从真局面生成）。"""
    actions = legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
    return PlayPhaseQuery(
        seq=1,
        actor=seat,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="出牌阶段。",
        legal_actions=actions,
        view=project(state, seat),
    )


# ---------------------------------------------------------------------------
# 局面 1：火杀 vs 藤甲 —— 普通杀无效，火杀还能 +1
# ---------------------------------------------------------------------------


def test_the_ai_picks_the_fire_slash_against_vine_armour():
    """同时有火杀与普通杀、目标穿藤甲 ⇒ 选火杀（不是普通杀）。"""
    fire, normal, vine = FIRE_SLASH[0], SLASH[0], VINE_ARMOR[0]
    engine = _engine(
        [SeatSpec(hand=(fire, normal)), SeatSpec(equip=(vine,))],
        deck=_deck_with_top(*_inert_draw(fire, normal, vine), exclude=(fire, normal, vine)),
    )
    _advance(engine, [_Minimal(), _Minimal()], _at_play_phase(0))

    action = _ai().respond(engine.pending)

    assert isinstance(action, UseCard), "出牌阶段应当真的使用一张牌"
    assert action.card.card_id == fire, "藤甲让普通【杀】无效、火【杀】+1 ⇒ 选火杀"
    assert action.targets == (1,), "目标是穿藤甲的那名角色"


# ---------------------------------------------------------------------------
# 局面 2：普通杀 vs 藤甲 —— 另有合法目标时避开藤甲目标
# ---------------------------------------------------------------------------


def test_the_ai_avoids_a_vine_armour_target_with_a_normal_slash():
    """只有普通杀、场上另有不穿藤甲的合法目标 ⇒ 不把【杀】浪费在无效目标上。"""
    normal, vine = SLASH[0], VINE_ARMOR[0]
    engine = _engine(
        [
            SeatSpec(hand=(normal,)),
            SeatSpec(equip=(vine,)),
            SeatSpec(),
        ],
        deck=_deck_with_top(*_inert_draw(normal, vine), exclude=(normal, vine)),
    )
    _advance(engine, [_Minimal(), _Minimal(), _Minimal()], _at_play_phase(0))

    action = _ai().respond(engine.pending)

    assert isinstance(action, UseCard), "出牌阶段应当真的使用一张牌"
    assert action.card.card_id == normal
    assert action.targets == (2,), "普通【杀】对藤甲无效 ⇒ 打不穿藤甲的目标"


# ---------------------------------------------------------------------------
# 局面 3：【酒】+【杀】能斩杀 —— 先酒再杀；杀不掉时不浪费酒
# ---------------------------------------------------------------------------


def _wine_slash_engine(*, target_hp: int) -> Engine:
    """座位 0 = AI（手握【酒】+【杀】）、座位 1 = 目标（空手，不会【闪】）。

    牌堆顶两张是【闪】（出牌阶段无候选）⇒ 摸牌阶段摸到它们不改变决策面。
    """
    wine, slash = WINE[0], SLASH[0]
    return _engine(
        [SeatSpec(hand=(wine, slash)), SeatSpec(hp=target_hp, hp_max=target_hp)],
        deck=_deck_with_top(*_inert_draw(wine, slash), exclude=(wine, slash)),
    )


def test_the_ai_plays_wine_before_a_lethal_slash_and_conserves_it_otherwise():
    """目标在斩杀线内 ⇒ 先【酒】再【杀】；打不死时先【杀】、不把【酒】白扔。"""
    wine, slash = WINE[0], SLASH[0]

    lethal = _wine_slash_engine(target_hp=2)
    ai = _ai()
    players = [ai, _Minimal()]
    _advance(lethal, players, _at_play_phase(0))

    first = ai.respond(lethal.pending)

    assert isinstance(first, UseCard) and first.card.card_id == wine, (
        "【酒】+【杀】正好斩杀：先【酒】"
    )
    assert first.targets == (0,), "出牌阶段的【酒】只对自己"
    lethal.submit(first)
    _advance(lethal, players, _at_play_phase(0))

    second = ai.respond(lethal.pending)

    assert isinstance(second, UseCard) and second.card.card_id == slash, (
        "【酒】之后补上那张【杀】"
    )
    assert second.targets == (1,)

    wasteful = _wine_slash_engine(target_hp=4)
    conserved_ai = _ai()
    _advance(wasteful, [conserved_ai, _Minimal()], _at_play_phase(0))

    action = conserved_ai.respond(wasteful.pending)

    assert isinstance(action, UseCard) and action.card.card_id == slash, (
        "杀不掉时不浪费【酒】：这一手先出【杀】"
    )


def _silver_lion_wine_engine(*, qinggang: bool) -> Engine:
    """座位 0（AI）握【酒】+【杀】；座位 1 穿白银狮子、2 体力。

    ``qinggang=True`` 时座位 0 另装备【青釭剑】——它越过白银狮子的封顶。
    """
    wine, slash, lion = WINE[0], SLASH[0], SILVER_LION[0]
    equip = (QINGGANG_SWORD[0],) if qinggang else ()
    used = (wine, slash, lion, *equip)
    return _engine(
        [
            SeatSpec(hand=(wine, slash), equip=equip),
            SeatSpec(hp=2, hp_max=4, equip=(lion,)),
        ],
        deck=_deck_with_top(*_inert_draw(*used), exclude=used),
    )


def test_wine_is_not_lethal_through_the_silver_lion_cap_without_qinggang():
    """白银狮子把【酒】的 +1 也封在 1 点：没有青釭剑 ⇒ 不喝酒；有青釭剑 ⇒ 正好斩杀。"""
    wine, slash = WINE[0], SLASH[0]

    capped = _silver_lion_wine_engine(qinggang=False)
    ai = _ai()
    _advance(capped, [ai, _Minimal()], _at_play_phase(0))

    action = ai.respond(capped.pending)

    assert isinstance(action, UseCard) and action.card.card_id == slash, (
        "白银狮子封顶吃掉【酒】的 +1：这一口不是斩杀，AI 先出【杀】"
    )

    piercing = _silver_lion_wine_engine(qinggang=True)
    ai = _ai()
    _advance(piercing, [ai, _Minimal()], _at_play_phase(0))

    action = ai.respond(piercing.pending)

    assert isinstance(action, UseCard) and action.card.card_id == wine, (
        "青釭剑越过白银狮子：【酒】+【杀】= 2 点正好斩杀，先喝酒"
    )


# ---------------------------------------------------------------------------
# 局面 4：铁索 / 火攻有收益时使用、无收益时铁索**重铸**（不是空放）
# ---------------------------------------------------------------------------


def _find_use(query, type_id: str, targets=None):
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            continue
        if targets is None or action.targets == targets:
            return action
    return None


def test_the_ai_uses_iron_chain_and_fire_attack_only_when_they_pay_and_reforges_otherwise():
    """有收益 ⇒ 用；无收益 ⇒ 铁索**重铸**、火攻不出（不是空放）。"""
    chain, fire_attack = IRON_CHAIN[0], FIRE_ATTACK[0]

    # ① 铁索有收益：自己与一名敌人都在连环上 ⇒ 重置自己（别等火伤传导过来）。
    paying = _engine(
        [SeatSpec(hand=(chain,), chained=True), SeatSpec(chained=True)],
        deck=_deck_with_top(*_inert_draw(chain), exclude=(chain,)),
    )
    _advance(paying, [_Minimal(), _Minimal()], _at_play_phase(0))

    # M5-bugfix：铁索声明了 preserve_target_order ⇒ 出牌阶段把 (0,1) 与 (1,0) 都发成候选。
    # 未明身份座位的重置在打分里是 0，于是 (0,) / (0,1) / (1,0) 同分、由种子打破——
    # 换一个在**三强并列**下仍落在 (0,) 的种子（平分由它打破，AI_SEED 不变：
    # 其余三条局面仍按各自的平分结果断言）。
    action = _ai(seed=3).respond(paying.pending)

    assert isinstance(action, UseCard), "有收益时铁索应当真的使用"
    assert action.card.card_id == chain
    assert action.targets == (0,), "重置自己（脱离连环）才是这一步的收益"
    paying.submit(action)  # 重放同样的动作必须被引擎接受（合法）

    # ② 无收益：无人横置、手里也没有属性伤害 ⇒ 铁索走重铸，不空放。
    idle = _engine(
        [SeatSpec(hand=(chain,)), SeatSpec()],
        deck=_deck_with_top(*_inert_draw(chain), exclude=(chain,)),
    )
    _advance(idle, [_Minimal(), _Minimal()], _at_play_phase(0))

    action = _ai().respond(idle.pending)

    assert isinstance(action, Reforge), "没有收益时铁索重铸，而不是空放"
    assert action.card.card_id == chain
    idle.submit(action)  # 重铸是独立 Action：引擎接受它（不按使用结算）

    # ③ 火攻有收益：目标有手牌、自己还有别的牌可以弃 ⇒ 使用。
    use = _engine(
        [SeatSpec(hand=(fire_attack,)), SeatSpec(hand=(DODGE[0],))],
        deck=_deck_with_top(
            *_inert_draw(fire_attack, DODGE[0]), exclude=(fire_attack, DODGE[0])
        ),
    )
    _advance(use, [_Minimal(), _Minimal()], _at_play_phase(0))

    action = _ai().respond(use.pending)

    assert isinstance(action, UseCard), "火攻能造成有效伤害时应当使用"
    assert action.card.card_id == fire_attack
    assert action.targets == (1,), "火攻指向有手牌的那名角色"

    # ④ 无收益：手里只剩这张火攻（没有同花色牌可弃）⇒ 不用火攻。
    only = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(fire_attack,)), SeatSpec(hand=(DODGE[0],))],
        deck=_deck_with_top(
            *_inert_draw(fire_attack, DODGE[0]), exclude=(fire_attack, DODGE[0])
        ),
        turn_seat=0,
    )
    action = _ai().respond(_play_phase_query(only))

    assert isinstance(action, EndPhase), "没有同花色牌可弃 ⇒ 火攻不产生伤害，不出"


# ---------------------------------------------------------------------------
# 新询问形状：火攻的"目标亮牌"与"同花色弃牌 / 放弃"
# ---------------------------------------------------------------------------


class _FireAttackUser:
    """座位 0：出牌阶段用【火攻】打 ``target``；其余询问交给启发式 AI 并逐条记下。"""

    def __init__(self, *, target: int = 1) -> None:
        self.ai = _ai()
        self.target = target
        self.seen: list[tuple[object, object]] = []
        self.used = False

    def respond(self, query):
        action = None
        if not self.used and query.kind == QueryKind.PLAY_PHASE:
            action = _find_use(query, "fire_attack", (self.target,))
            self.used = action is not None
        if action is None:
            action = self.ai.respond(query)
        self.seen.append((query, action))
        return action


class _RecordingAI:
    """启发式 AI 的透明包装：本票只读它给每条询问的**应答**（外部缝，不碰打分函数）。"""

    def __init__(self) -> None:
        self.ai = _ai()
        self.seen: list[tuple[object, object]] = []

    def respond(self, query):
        action = self.ai.respond(query)
        self.seen.append((query, action))
        return action


def _select_steps(seen, *, optional: bool):
    return [
        (query, action)
        for query, action in seen
        if query.kind == QueryKind.SELECT_CARDS and query.spec.optional is optional
    ]


def _fire_attack_discard_case(*, spare: int) -> tuple[Engine, _FireAttackUser, _RecordingAI]:
    """座位 0 用火攻打座位 1；``spare`` = 手里那张**黑桃**垫牌（同花色弃牌的唯一来源）。

    目标会亮**最便宜**的那张（亮牌是它自己的选择）：黑桃【闪电】（1.0）。
    """
    fire = FIRE_ATTACK[0]
    spade_lightning, heart_peach = 28, 4
    return (
        _engine(
            [SeatSpec(hand=(fire, spare)), SeatSpec(hand=(spade_lightning, heart_peach))],
            deck=_deck_with_top(
                *_inert_draw(fire, spare, spade_lightning, heart_peach),
                exclude=(fire, spare, spade_lightning, heart_peach),
            ),
        ),
        _FireAttackUser(target=1),
        _RecordingAI(),
    )


def test_the_ai_reveals_cheaply_and_only_discards_a_cheap_same_suit_card_for_fire_attack():
    """亮牌挑**便宜**的那张；同花色弃牌只在"1 点伤害值得"时弃，贵的牌宁可放弃。"""
    cheap_spare, dear_spare = 27, 33  # 黑桃【决斗】2.5 / 黑桃【顺手牵羊】3.2

    engine, user, target = _fire_attack_discard_case(spare=cheap_spare)
    fire = FIRE_ATTACK[0]
    _advance(engine, [user, target], lambda e: fire in e.state.discard)

    reveal = _select_steps(target.seen, optional=False)
    assert len(reveal) == 1, "目标被问一次：亮哪一张"
    reveal_query, reveal_action = reveal[0]
    assert reveal_action.picks, "亮牌必须真的选一张"
    revealed = reveal_query.slots[reveal_action.picks[0]].card
    assert revealed.card_id == 28, "亮牌挑便宜的【闪电】，不亮【桃】"

    discard = _select_steps(user.seen, optional=True)
    assert len(discard) == 1, "使用者被问一次：弃不弃同花色"
    discard_query, discard_action = discard[0]
    assert isinstance(discard_action, SelectCards), "1 点伤害值一张【决斗】⇒ 弃"
    discarded = discard_query.slots[discard_action.picks[0]].card
    assert discarded.card_id == cheap_spare
    assert engine.state.player(1).hp == 3, "弃了同花色 ⇒ 火攻造成 1 点火焰伤害"

    engine, user, target = _fire_attack_discard_case(spare=dear_spare)
    _advance(engine, [user, target], lambda e: fire in e.state.discard)

    discard = _select_steps(user.seen, optional=True)
    assert len(discard) == 1
    assert isinstance(discard[0][1], Pass), "1 点伤害不值一张【顺手牵羊】⇒ 放弃"
    assert engine.state.player(1).hp == 4, "放弃同花色 ⇒ 火攻不造成伤害（成对反例）"


def test_the_ai_uses_wine_to_rescue_itself_at_near_death():
    """濒死自救：没有【桃】时【酒】也是"回复 1 点"，不能被当成 0 分与「放弃」打平。"""
    wine = WINE[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=0, hp_max=4, hand=(wine,)), SeatSpec()],
        deck=_deck_with_top(exclude=(wine,)),
        turn_seat=0,
    )
    actions = legal_actions_for(
        state, 0, QueryKind.PEACH, answers_seq=1, dying=0
    )
    query = PeachQuery(
        seq=1,
        actor=0,
        timing=Timing.NEAR_DEATH_AT,
        prompt="濒死求桃。",
        legal_actions=actions,
        view=project(state, 0),
        dying=0,
    )

    action = _ai().respond(query)

    assert isinstance(action, UseCard) and action.card.card_id == wine, "【酒】自救"
    assert action.targets == (0,)


def test_the_ai_scores_a_chained_revealed_enemy_without_using_the_chain():
    """回归（M6 P0 暴露）：横置的**已明敌人**走进【铁索】打分器的敌人分支。

    该分支原来引用一个不存在的常量（``_IRON_CHAIN_RESET_ENEMY_VALUE``，真名是
    ``_IRON_CHAIN_RESET_ENEMY_PENALTY``）；启用池 16 名时随机对局没走到它，M6 P0
    扩到 19 名后第一次命中就 ``NameError``（160 档 8 种子里的 seed 142）。
    判据 = AI 照常应答，且**不**替敌人拆引线（重置敌人是 -1.5，比重铸的 0.6 差）。
    """
    chain = IRON_CHAIN[0]
    enemy_chained = _engine(
        [
            SeatSpec(hand=(chain,), identity=Identity.REBEL),
            SeatSpec(identity=Identity.LORD, chained=True),
        ],
        deck=_deck_with_top(*_inert_draw(chain), exclude=(chain,)),
    )
    _advance(enemy_chained, [_Minimal(), _Minimal()], _at_play_phase(0))

    action = _ai().respond(enemy_chained.pending)

    assert isinstance(action, Reforge), "横置的已明敌人是负收益（替他拆引线）⇒ 重铸"
    assert action.card.card_id == chain
