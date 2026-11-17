"""SP武将组8：刘焉【图射】/【立牧】（sp8-liuyan 票 01；用户 2026-11-17 签口径）。

**【图射】一组**（零步流水线：``USE_AFTER_DESIGNATE_TARGET`` 钩子 + 摸 X 张）：

- 闸门是**产物大类不是装备牌**，不是"有没有目标"——用户逐字口径：「装备牌也是指定目标的，
  但是该技能限制了『非装备牌』」⇒ 装备牌照样把自己指定为目标、仍**不触发**；反过来
  自用的非装备牌（桃 / 无中生有 / 酒 / 闪电 / 转化【乐不思蜀】）照样触发、X = 1；
- X = 本次使用的**座位目标数**（含自己），一次使用只问一次；
- 「若你没有基本牌」读**触发时**的手牌（此时这张牌已进处理区）；
- Q-A：0 个座位目标的使用（无懈可击）**不发询问**。

**【立牧】一组**（A 族 + 零步流水线：① 的选牌 / 使用手写在提交点；② 是两条常驻修饰器）：

- ① 方片牌可来自**手牌或装备区**，转化【乐不思蜀】落**自己**的判定区，**然后**回复 1 点
  （回复严格排在使用结算完之后 —— 次序断言 `card_move → hp_change`）；
- ① 的两个闸门（用户逐字）：**自身判定区已有【乐不思蜀】** / **判定区被废除** ⇒ 不能发动
  （落在**候选阶段**，不是发动后取消）；同一阶段可再发动（牌面无"限一次"），真正挡住第二次
  的是判定区同名；
- ② 读法 **R1**（用户 2026-11-17 签）：「没有距离限制」只豁免**牌自身的额定距离**，
  【杀】的攻击范围照旧；超出攻击范围的目标（杀 / 顺）一律不可选。

驱动纪律：脚本玩家只应答**被测那一步**，其余走最小合法应答（放弃 / 结束出牌阶段）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import (
    Category,
    Identity,
    RuleConfig,
    Suit,
    card_category,
    hero_skill_id,
    load_content_pack,
)
from sgs.effects import (
    ModifierPoint,
    attack_range,
    check_count,
    check_targets,
    distance,
)
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.catalog import MATCH_OWNER_KEY
from sgs.skills.hero_sp8 import _tushe_condition
from sgs.state import (
    SEALED_SLOT_IDS,
    ActivateSkill,
    EndPhase,
    GameState,
    Pass,
    Phase,
    SelectCards,
    UseCard,
    Zone,
)
from sgs.timing import Timing
from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries

PACK = load_content_pack(deck_profile="standard_108")

LIUYAN = "liuyan"
TUSHE = hero_skill_id("liuyan", "tushe")
LIMU = hero_skill_id("liuyan", "limu")


# ---------------------------------------------------------------------------
# 构造与驱动助手
# ---------------------------------------------------------------------------


def _is_basic(card_id: int) -> bool:
    return card_category(PACK.pack_id, PACK.deck.cards[card_id].type) is Category.BASIC


def _diamond(*, basic: bool) -> int:
    """一张方片牌（``basic`` 选基本牌 / 非基本牌）。"""
    for card_id, card in enumerate(PACK.deck.cards):
        if card.suit is Suit.DIAMOND and _is_basic(card_id) is basic:
            return card_id
    raise AssertionError("标准 108 里找不到对应方片牌")


def _non_basic(*, exclude: tuple[int, ...] = ()) -> int:
    """一张非基本牌（当填充牌 / 上手牌用）。"""
    for card_id, card in enumerate(PACK.deck.cards):
        if card_id not in exclude and not _is_basic(card_id):
            return card_id
    raise AssertionError("标准 108 里找不到非基本牌")


def _event_id(card_type: str) -> int:
    return cards_of_type(PACK, card_type)[0]


class Script:
    """座位 0 的脚本：可指定"打哪张牌 / 发动哪条技能 / 选哪张牌"，其余最小合法应答。"""

    def __init__(
        self,
        state: GameState,
        *,
        use_card: int | None = None,
        targets: tuple[int, ...] = (),
        activate: tuple[str, ...] = (),
        answer: tuple[str, ...] = ("tushe",),
        pick_card: int | None = None,
        pick_diamond: bool = False,
    ) -> None:
        self._state = state
        self.use_card = use_card
        self.targets = targets
        self.activate = list(activate)
        self.answer = answer
        self.pick_card = pick_card
        self.pick_diamond = pick_diamond
        self.used = False
        self.activation_queries = 0
        self.select_queries = 0

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if self.use_card is not None and not self.used:
                self.used = True
                return UseCard(
                    card=self._state.card(self.use_card),
                    targets=self.targets,
                    answers_seq=query.seq,
                )
            for skill_id in self.activate:
                action = find_action(
                    query,
                    lambda a: isinstance(a, ActivateSkill) and a.skill_id == skill_id,
                )
                if action is not None:
                    self.activate.remove(skill_id)
                    return action
            return _end_phase(query)
        if query.kind == "SKILL_ACTIVATION":
            for skill_id in self.answer:
                action = find_action(
                    query,
                    lambda a: isinstance(a, ActivateSkill)
                    and a.skill_id == hero_skill_id(LIUYAN, skill_id),
                )
                if action is not None:
                    self.activation_queries += 1
                    return action
            return _pass(query)
        if query.kind == "SELECT_CARDS":
            self.select_queries += 1
            index = self._pick_index(query)
            return SelectCards(picks=(index,), answers_seq=query.seq)
        return _pass(query)

    def _pick_index(self, query) -> int:
        for index, slot in enumerate(query.slots):
            card = getattr(slot, "card", None)
            if card is None:
                continue
            if self.pick_diamond and card.suit == Suit.DIAMOND.value:
                return index
            if self.pick_card is not None and card.card_id == self.pick_card:
                return index
        raise AssertionError("选择面里没有要找的那张牌（局面构造坏了）")


def _end_phase(query):
    return next(a for a in query.legal_actions if isinstance(a, EndPhase))


def _pass(query):
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def find_action(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _seats(
    *,
    hand: tuple[int, ...] = (),
    equip: tuple[int, ...] = (),
    judge: tuple[int, ...] = (),
    hp: int = 3,
    hp_max: int = 3,
    others: int = 1,
) -> list[SeatSpec]:
    identity = Identity.LORD if others > 1 else None
    seats = [
        SeatSpec(
            hero=LIUYAN,
            hand=hand,
            equip=equip,
            judge=judge,
            hp=hp,
            hp_max=hp_max,
            identity=identity,
        )
    ]
    seats.extend(SeatSpec() for _ in range(others))
    if others > 1:
        # 身份局必须先发出一个主公座位（票 13 §4）；其余座位给最小合法身份。
        seats[2] = SeatSpec(identity=Identity.REBEL)
        for index in range(3, len(seats)):
            seats[index] = SeatSpec(identity=Identity.RENEGADE)
    return seats


def _state(
    seats: list[SeatSpec],
    *,
    deck: tuple[int, ...],
    phase: Phase = Phase.PLAY,
    turn_seat: int = 0,
) -> GameState:
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=len(seats), first_seat=turn_seat),
    )


def _stop_when(predicate):
    """停止判据工厂：引擎**空闲**（无待答询问、结算栈已空）且 ``predicate`` 成立。

    注意不能只写"栈空"——刚构造的引擎也是栈空 + 无询问，会立刻停（本票实测踩过）。
    """

    def stop(engine: Engine) -> bool:
        return (
            engine.pending is None
            and not engine.stack.frames
            and predicate(engine)
        )

    return stop


def _run(engine: Engine, players: list[Any], stop) -> Engine:
    """推进到 ``stop`` 成立（每位询问按脚本应答）。"""
    return run_queries(engine, players, stop)


def _hand(engine: Engine, seat: int = 0) -> tuple[int, ...]:
    return tuple(engine.state.player(seat).hand)


def _first_index(engine: Engine, predicate) -> int | None:
    for index, record in enumerate(engine.records):
        if predicate(record):
            return index
    return None


# ---------------------------------------------------------------------------
# 数据面
# ---------------------------------------------------------------------------


def test_liuyan_is_in_the_sp8_table_with_two_skills():
    """数据面：``sp8`` 表里一条刘焉（群 / 男 / 3 体力），技能恰两条且都在 CATALOG 里。"""
    from sgs.data import load_heroes

    table = load_heroes("sp8")
    heroes = [hero for hero in table.heroes if hero.id == LIUYAN]
    assert len(heroes) == 1
    hero = heroes[0]
    assert hero.true_name == "刘焉"
    assert hero.kingdom.value == "qun"
    assert hero.gender.value == "male"
    assert hero.hp_max == 3
    assert hero.version == "sp8"
    assert hero.enabled
    assert {skill.id for skill in hero.skills} == {"tushe", "limu"}
    assert TUSHE in CATALOG and LIMU in CATALOG


def test_tushe_declares_the_card_text_verbatim_and_never_preactivates():
    """【图射】牌面逐字（OL 现行）+ ``preactivated=False``（零步 ⇒ 清单渲不出，不能翻）。"""
    definition = CATALOG[TUSHE]
    assert definition.locked is False
    assert definition.preactivated is False
    assert definition.requirement == (
        "当你使用非装备牌指定目标后，若你没有基本牌，则你可以摸X张牌"
        "（X为此牌指定的目标数）。"
    )


def test_limu_declares_the_card_text_verbatim_and_has_both_limit_modifiers():
    """【立牧】牌面逐字 + ② 的两个修饰器（距离 / 次数）都挂在这条声明上。"""
    definition = CATALOG[LIMU]
    assert definition.locked is False
    assert definition.requirement.startswith("出牌阶段，你可以将一张方片牌当【乐不思蜀】")
    points = {modifier.point for modifier in definition.modifiers}
    assert ModifierPoint.TARGET_RANGE_EXEMPT in points
    assert ModifierPoint.COUNT_EXEMPT in points


# ---------------------------------------------------------------------------
# 图射：触发条件
# ---------------------------------------------------------------------------


class _Host:
    """只带 ``state`` 的最小宿主（条件只读这一面）。"""

    def __init__(self, state: GameState) -> None:
        self.state = state


class _Frame:
    def __init__(self, **data: Any) -> None:
        self.data = data


def test_tushe_is_not_offered_when_the_use_has_no_seat_targets():
    """Q-A（用户 2026-11-17 签）：0 个座位目标的使用（无懈可击）不触发。

    正面对照一起断：同一张牌面、把目标补成 1 个 ⇒ 条件成立（防"恒假的假绿"）。
    """
    trick = _event_id("dismantlement")
    state = _state(_seats(hand=()), deck=deck_excluding(PACK, trick))
    host = _Host(state)
    card = state.card(trick)
    assert (
        _tushe_condition(
            host,
            _Frame(**{MATCH_OWNER_KEY: 0, "source": 0, "targets": (), "card": card}),
        )
        is False
    ), "0 个座位目标 ⇒ 不触发（X = 0 不摸）"
    assert (
        _tushe_condition(
            host,
            _Frame(**{MATCH_OWNER_KEY: 0, "source": 0, "targets": (1,), "card": card}),
        )
        is True
    ), "同一张牌面、1 个目标 ⇒ 条件成立"


def test_tushe_draws_one_after_using_a_self_targeted_trick():
    """自用的非装备牌（【无中生有】目标是自己）照样触发，X = 1（含自己）。"""
    card = _event_id("ex_nihilo")
    state = _state(_seats(hand=(card,)), deck=deck_excluding(PACK, card))
    script = Script(state, use_card=card, targets=(0,), answer=("tushe",))
    expected = 1 + 2 - 1 + 2 + 1  # 起始 1 → 摸牌 +2 → 用掉 −1 → 它自己摸 2 → 图射摸 1
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: len(_hand(e)) == expected),
    )

    assert script.activation_queries == 1, "图射该问一次"
    assert len(_hand(engine)) == expected


def test_tushe_draws_one_per_target_and_asks_once_for_a_three_target_card():
    """三目标锦囊：【图射】只问**一次**、X = 3（不是逐目标触发）。"""
    card = _event_id("barbarian_invasion")
    state = _state(
        _seats(hand=(card,), others=3), deck=deck_excluding(PACK, card)
    )
    script = Script(state, use_card=card, targets=(1, 2, 3), answer=("tushe",))
    players = [script] + [Script(state) for _ in range(3)]
    expected = 1 + 2 - 1 + 3  # 起始 1 + 摸牌 2 − 用掉 1 + 图射 3（三个目标）
    engine = _run(
        Engine.from_state(state, seed=7),
        players,
        _stop_when(lambda e: len(_hand(e)) == expected),
    )

    assert script.activation_queries == 1, "一张多目标牌只问一次"
    assert len(_hand(engine)) == expected


def test_tushe_is_not_offered_on_an_equipment_card_even_though_it_targets_itself():
    """用户口径：装备牌**照样指定自己为目标**，挡它的是牌面的「非装备牌」。

    反例对照：换成一张非装备牌（同一次使用、同一个目标）⇒ 会问。
    """
    equip = _event_id("crossbow")
    state = _state(_seats(hand=(equip,)), deck=deck_excluding(PACK, equip))
    script = Script(state, use_card=equip, targets=(0,), answer=("tushe",))
    expected = 1 + 2 - 1  # 起始 1 + 摸牌 2 − 用掉 1（不摸）
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: equip in tuple(e.state.player(0).equip)),
    )

    assert script.activation_queries == 0, "装备牌不触发【图射】"
    assert list(engine.state.player(0).equip) == [equip], "先确认这张装备真的用出去了"
    assert len(_hand(engine)) == expected


def test_tushe_is_not_offered_while_a_basic_card_remains_in_hand():
    """「若你没有基本牌」：手里还留着基本牌 ⇒ 不触发、不摸。"""
    duel = _event_id("duel")
    slash = _event_id("slash")
    state = _state(
        _seats(hand=(duel, slash)), deck=deck_excluding(PACK, duel, slash)
    )
    script = Script(state, use_card=duel, targets=(1,), answer=("tushe",))
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: duel not in _hand(e)),
    )

    assert script.activation_queries == 0, "手里还有【杀】⇒ 条件假"
    assert slash in _hand(engine), "基本牌还在手里（只是不许发动）"


def test_tushe_reads_the_hand_after_the_used_card_left_it():
    """触发时那张牌**已经**离开手牌（进处理区）——它就是"最后一张基本牌"时条件成立。

    【无中生有】是锦囊，故这里用一张方片【闪】：用立牧把它当【乐不思蜀】用掉，手牌随即
    没有基本牌 ⇒ 图射触发（见立牧组的交互用例）；本用例先断"用它之后手牌无基本牌"这个事实。
    """
    dodge = None
    for card_id, card in enumerate(PACK.deck.cards):
        if card.type == "dodge":
            dodge = card_id
            break
    assert dodge is not None
    state = _state(_seats(hand=(dodge,)), deck=deck_excluding(PACK, dodge))
    # 直接构造"这张牌已离开手牌"的局面：把这张【闪】当作刚使用的牌，手牌里只剩一张锦囊。
    filler = _non_basic(exclude=(dodge,))
    state_with_filler = _state(
        _seats(hand=(filler,)), deck=deck_excluding(PACK, filler)
    )
    assert (
        _tushe_condition(
            _Host(state_with_filler),
            _Frame(
                **{
                    MATCH_OWNER_KEY: 0,
                    "source": 0,
                    "targets": (0,),
                    "card": state.card(dodge),
                }
            ),
        )
        is True
    )
    assert (
        _tushe_condition(
            _Host(state),
            _Frame(
                **{
                    MATCH_OWNER_KEY: 0,
                    "source": 0,
                    "targets": (0,),
                    "card": state.card(filler),
                }
            ),
        )
        is False
    ), "同一局面里手牌还有基本牌 ⇒ 假（条件读的是当时的手牌）"


# ---------------------------------------------------------------------------
# 立牧 ①：落判定区 + 回复
# ---------------------------------------------------------------------------


def test_limu_puts_the_converted_indulgence_into_own_judge_zone_then_recovers():
    """① 方片牌 → 转化【乐不思蜀】落**自己**的判定区，**然后**回复 1 点体力。"""
    diamond = _diamond(basic=False)
    filler = _non_basic(exclude=(diamond,))
    state = _state(
        _seats(hand=(diamond, filler), hp=2, hp_max=3),
        deck=deck_excluding(PACK, diamond, filler),
    )
    script = Script(state, activate=(LIMU,), pick_card=diamond, answer=())
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: bool(e.state.player(0).judge) and e.state.player(0).hp == 3),
    )

    player = engine.state.player(0)
    assert list(player.judge) == [diamond], "方片牌（实体来源牌）落在自己的判定区"
    assert engine.state.virtual_judge[diamond].type == "indulgence", "它在判定阶段按【乐不思蜀】结算"
    assert player.hp == 3, "然后回复 1 点体力"
    assert diamond not in player.hand
    # 「然后」的次序：先落判定区，后回复（两个 delta 的先后）
    judge_at = _first_index(
        engine, lambda record: record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.JUDGE
        and any(card.card_id == diamond for card in record.cards)
    )
    hp_at = _first_index(
        engine, lambda record: record.kind == "delta" and record.delta_type == "hp_change"
    )
    assert judge_at is not None and hp_at is not None
    assert judge_at < hp_at, "回复必须排在使用结算（判定区落位）之后"


def test_limu_may_take_the_diamond_card_from_the_equip_zone():
    """① 的区域默认：手牌 **或** 装备区的方片牌（官方攻略同口径）。"""
    weapon = _diamond_equipment()
    state = _state(
        _seats(equip=(weapon,), hp=3, hp_max=3),
        deck=deck_excluding(PACK, weapon),
    )
    script = Script(state, activate=(LIMU,), pick_card=weapon, answer=())
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: bool(e.state.player(0).judge)),
    )

    player = engine.state.player(0)
    assert weapon not in player.equip
    assert weapon in player.judge, "装备区的方片牌也能当【乐不思蜀】"
    assert engine.state.virtual_judge[weapon].type == "indulgence"


def _diamond_equipment() -> int:
    """一张方片**装备牌**（牌名大类读内容包，不抄牌名表）。"""
    for card_id, card in enumerate(PACK.deck.cards):
        if card.suit is not Suit.DIAMOND:
            continue
        if card_category(PACK.pack_id, card.type) is Category.EQUIPMENT:
            return card_id
    raise AssertionError("标准 108 里找不到方片装备牌")


def test_limu_is_not_offered_when_own_judge_zone_already_holds_an_indulgence():
    """用户口径：自身有【乐不思蜀】时不能发动（判定区不能放两张同名延时锦囊）。

    直测**候选面**：引擎起跑会先跑判定阶段、把判定区那张【乐不思蜀】判掉，故这里不经过
    整局驱动，直接调候选回调（``ctx`` 只给候选期那几格）。
    """
    from sgs.effects import CandidateScope, ModifierContext
    from sgs.skills.active import _limu_offers

    diamond = _diamond(basic=False)
    indulgence = _event_id("indulgence")
    state = _state(
        _seats(hand=(diamond,), judge=(indulgence,)),
        deck=deck_excluding(PACK, diamond, indulgence),
    )
    ctx = ModifierContext(
        subject=0, owner=0, skill_id=LIMU, scope=CandidateScope.PLAY
    )
    assert _limu_offers(state, ctx, ()) == (), "判定区已有【乐不思蜀】⇒ 不产出候选"
    state.player(0).judge = []
    assert _limu_offers(state, ctx, ()) != (), "判定区一空 ⇒ 候选回来（正面对照）"


def test_limu_is_not_offered_when_the_judge_zone_is_abolished():
    """用户口径：判定区被废除时不能发动。"""
    diamond = _diamond(basic=False)
    state = _state(_seats(hand=(diamond,)), deck=deck_excluding(PACK, diamond))
    state.player(0).sealed_zones = tuple(SEALED_SLOT_IDS)
    engine = Engine.from_state(state, seed=7)
    query = _first_play_phase(engine, [Script(state), Script(state)])

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == LIMU
    ], "判定区被废除 ⇒ 立牧不该出现在出牌阶段候选里"


def _first_play_phase(engine: Engine, players: list[Any]):
    """跑到第一个出牌阶段询问（不提交），返回那条询问。"""
    for _ in range(2_000):
        query = engine.pending
        if query is not None and query.kind == "PLAY_PHASE":
            return query
        if query is None:
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("没有走到出牌阶段询问")


def test_limu_second_use_is_not_offered_while_the_judge_zone_holds_one():
    """同一阶段可再发动（无「限一次」），但判定区同名把第二次挡在**候选阶段**。"""
    diamond = _diamond(basic=False)
    filler = _non_basic(exclude=(diamond,))
    state = _state(
        _seats(hand=(diamond, filler, filler), hp=3, hp_max=3),
        deck=deck_excluding(PACK, diamond, filler),
    )
    script = Script(state, activate=(LIMU,), pick_card=diamond, answer=())
    engine = Engine.from_state(state, seed=7)
    _run(engine, [script, Script(state)], lambda e: bool(e.state.player(0).judge))
    # 第一次发动收尾后仍在同一出牌阶段：候选里不该再有立牧。
    query = _first_play_phase(engine, [Script(state), Script(state)])
    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == LIMU
    ]


def test_limu_converted_indulgence_triggers_tushe():
    """交互：立牧① 的转化【乐不思蜀】是"非装备牌 + 1 个目标（自己）" ⇒ 图射触发、摸 1 张。"""
    diamond = _diamond(basic=True)  # 方片【闪】/【杀】/【桃】：手里唯一的基本牌
    filler = _non_basic(exclude=(diamond,))
    top = (_non_basic(exclude=(diamond, filler)), _non_basic(exclude=(diamond, filler)))
    state = _state(
        _seats(hand=(diamond, filler), hp=3, hp_max=3),
        deck=(top[0], top[1], *deck_excluding(PACK, diamond, filler, *top)),
    )
    script = Script(
        state, activate=(LIMU,), pick_card=diamond, answer=("tushe",)
    )
    expected = 2 + 2 - 1 + 1  # 起始 2 + 摸牌 2（两张非基本）− 用掉 1 + 图射 1
    engine = _run(
        Engine.from_state(state, seed=7),
        [script, Script(state)],
        _stop_when(lambda e: len(_hand(e)) == expected and bool(e.state.player(0).judge)),
    )

    assert script.activation_queries == 1, "转化【乐不思蜀】指定自己为目标 ⇒ 图射问一次"
    player = engine.state.player(0)
    assert player.hp == 3
    assert diamond in player.judge
    # 起始 2 + 摸牌阶段 2（两张非基本）− 用掉 1 + 图射 1 = 4
    assert len(player.hand) == 2 + 2 - 1 + 1


# ---------------------------------------------------------------------------
# 立牧 ②：读法 R1（攻击范围内；只豁免牌自身的额定距离 + 次数）
# ---------------------------------------------------------------------------


def _four_seats(*, hand=(), equip=(), judge=(), hp=4, hp_max=4) -> GameState:
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero=LIUYAN,
                hand=hand,
                equip=equip,
                judge=judge,
                hp=hp,
                hp_max=hp_max,
                identity=Identity.LORD,
            ),
            SeatSpec(identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL, hand=(_non_basic(),)),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, *hand, *equip, *judge),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )
    return state


def test_distance_between_seat_zero_and_two_is_two():
    """先确认局面：4 人环上座位 0 → 座位 2 的距离是 2（R1 的分歧点靠它）。"""
    state = _four_seats()
    assert distance(state, 0, 2) == 2
    assert distance(state, 0, 1) == 1
    assert attack_range(state, 0) == 1


def test_slash_still_cannot_reach_outside_attack_range_while_limu_is_active():
    """R1：② 不豁免【杀】的攻击范围（与 R2 的分歧点）。"""
    indulgence = _event_id("indulgence")
    slash = _event_id("slash")
    banned = _four_seats(hand=(slash,), judge=(indulgence,))
    plain = _four_seats(hand=(slash,))
    card = banned.card(slash)
    assert 2 not in check_targets(banned, 0, card), "立牧生效时仍打不到攻击范围外"
    assert check_targets(banned, 0, card) == check_targets(plain, 0, card)


def test_snatch_reaches_a_target_in_attack_range_but_beyond_rated_distance():
    """R1：② 豁免顺 / 兵的**额定距离** ⇒ 装备 2 距离武器后，能顺到距离 2 的角色。"""
    indulgence = _event_id("indulgence")
    snatch = _event_id("snatch")
    weapon = _event_id("qinggang_sword")  # 攻击范围 2
    active = _four_seats(hand=(snatch,), equip=(weapon,), judge=(indulgence,))
    idle = _four_seats(hand=(snatch,), equip=(weapon,))
    card = active.card(snatch)
    assert 2 in check_targets(active, 0, card), "攻击范围内、距离 2 ⇒ 顺手牵羊可用"
    assert 2 not in check_targets(idle, 0, card), "判定区为空 ⇒ 回到额定距离 1"


def test_snatch_cannot_reach_outside_attack_range_even_while_limu_is_active():
    """R1 与 R3 的唯一分歧：目标在**攻击范围外** ⇒ 顺也不可用（先筛人、再豁免额定距离）。"""
    indulgence = _event_id("indulgence")
    snatch = _event_id("snatch")
    state = _four_seats(hand=(snatch,), judge=(indulgence,))
    assert attack_range(state, 0) == 1
    assert 2 not in check_targets(state, 0, state.card(snatch))


def test_slash_count_is_exempt_while_the_judge_zone_has_a_card():
    """② 的「没有次数限制」：判定区有牌时，本阶段第 2 张【杀】不再超额。"""
    indulgence = _event_id("indulgence")
    slash = _event_id("slash")
    active = _four_seats(hand=(slash,), judge=(indulgence,))
    idle = _four_seats(hand=(slash,))
    card = active.card(slash)
    # 先把"本回合已用 1 张【杀】"落进座位事实（出牌阶段计数键）。
    for state in (active, idle):
        state.player(0).card_use_counts["slash"] = 1
    assert check_count(active, 0, card, targets=(1,)) is False, "立牧生效 ⇒ 不超额"
    assert check_count(idle, 0, card, targets=(1,)) is True, "判定区为空 ⇒ 照常超额"


def test_limu_effects_stop_when_the_judge_zone_is_empty():
    """② 是**连续**的：判定区一空，距离 / 次数立刻回到常规。"""
    indulgence = _event_id("indulgence")
    snatch = _event_id("snatch")
    weapon = _event_id("qinggang_sword")
    state = _four_seats(hand=(snatch,), equip=(weapon,), judge=())
    state.player(0).judge = [indulgence]
    card = state.card(snatch)
    assert 2 in check_targets(state, 0, card)
    state.player(0).judge = []
    assert 2 not in check_targets(state, 0, card)
