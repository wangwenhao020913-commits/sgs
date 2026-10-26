"""M6 票 08：**手牌上限双轴** + 界周瑜 / 界夏侯惇 / 界黄月英（spec D11 / D9）。

规则原文：`三国杀详细规则.txt` §四 L334–L336（手牌上限 = 当前体力值）、§十九
L2023–L2029（界夏侯惇【刚烈】/【清俭】）、§十九 L2103–L2110（界黄月英【集智】/【奇才】）、
§十九 L2152–L2158（界周瑜【英姿】/【反间】）。

**双轴**（D11）是两个**互相独立**的查询：

- **上限** = 体力值 → **替换式**（界【英姿】②"手牌上限等于体力上限"，查询期从活着的实例算
  ⇒ 上限变化 / 技能失去立刻反映）→ **加式**（【清俭】作用于**当前回合角色**、【集智】作用于
  自己；带回合作用域、落座位状态、不随来源消失撤回——D9 的一次性效果）；
- **计数排除**（得牌不计入手牌数）是**票 09 的标签轴**，本文件不碰：本文件的所有断言里
  手牌计数恒 = ``len(hand)``。

落点全部是外部缝：:func:`sgs.effects.hand_limit` / :func:`sgs.effects.discard_count` 的
返回值、弃牌阶段真实发出的 ``DISCARD`` 询问张数、询问序列、终局状态（手牌 / 体力 / 手牌上限）。

六条技能（界周瑜【英姿】②/【反间】、界夏侯惇【刚烈】/【清俭】、界黄月英【集智】/【奇才】①②）
的构造局面都在这里；票 07 的四层否定审计（``tests/test_negation_sites.py``）登记【奇才】②
的站点。
"""

from __future__ import annotations

from sgs.data import Identity, RuleConfig, Suit, load_content_pack
from sgs.effects import check_targets, discard_count, hand_limit
from sgs.engine import Engine
from sgs.state import (
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    SelectOption,
    UseCard,
    Zone,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    ran_timing,
    run_turns,
    run_until,
)
from test_skills import _deck_excluding, _use_frame_popped

PACK = load_content_pack(deck_profile="standard_108")

JIE_YINGZI = "hero.jie_zhouyu.yingzi"
JIE_FANJIAN = "hero.jie_zhouyu.fanjian"
JIE_GANGLIE = "hero.jie_xiahoudun.ganglie"
JIE_QINGJIAN = "hero.jie_xiahoudun.qingjian"
JIE_JIZHI = "hero.jie_huangyueying.jizhi"
JIE_QICAI = "hero.jie_huangyueying.qicai"
JIE_QINGJIAN_FLOW = "qingjian"

JIE_JIZHI_LIMIT = "jie_jizhi:limit"
"""【集智】"是否弃置此牌换 +1"的那次选项询问（M6 票 08 的第二个"可以"）。"""


def _deck_with_third(third: int, *, used: tuple[int, ...] = ()) -> tuple[int, ...]:
    """把 ``third`` 放在**第 3 张**：摸牌阶段取走前两张，**集智**摸到的就是它。"""
    fillers = [card_id for card_id in _deck_excluding(third, *used)]
    return (fillers[0], fillers[1], third, *fillers[2:])


class _ScriptPlayer:
    """按指令应答：``skill_id`` 的「是否发动」与「弃置换上限」；其余取最小合法。"""

    def __init__(self, *, skill_id: str, activate: bool = True, discard: bool = True) -> None:
        self.skill_id = skill_id
        self.activate = activate
        self.discard = discard

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == self.skill_id:
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if getattr(action, "kind", None) == "ActivateSkill"
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if getattr(query, "skill_id", None) == JIE_JIZHI_LIMIT:
            if self.discard:
                return query.legal_actions[0]
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if query.kind == QueryKind.PLAY_PHASE:
            return next(
                action for action in query.legal_actions if action.kind == "EndPhase"
            )
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _use_trick(
    trick: int,
    *,
    third: int,
    seat0_skill: str,
    activate: bool = True,
    discard: bool = True,
):
    """构造局面 → 推到座位 0 的出牌阶段 → 使用 ``trick`` → 跑到使用帧出栈。

    返回 ``(engine, deck_before)``：``deck_before[0]`` 就是集智该摸的那张。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(trick,)), SeatSpec()],
        deck=_deck_with_third(third, used=(trick,)),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(seat0_skill)
    engine = Engine.from_state(state, seed=7)
    players = [PassPlayer(), PassPlayer()]
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    deck_before = list(state.deck)
    query = engine.pending
    action = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.card_id == trick
    )
    engine.submit(action)
    players[0] = _ScriptPlayer(
        skill_id=seat0_skill, activate=activate, discard=discard
    )
    run_until(engine, players, _use_frame_popped)
    return engine, deck_before


def _activation_queries(engine, skill_id: str) -> list:
    """记录流里 ``skill_id`` 的「是否发动」询问（非锁定技的可选性判据）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and record.query.skill_id == skill_id
    ]


def _state(
    *,
    seats: int = 2,
    hp: tuple[int, ...] = (),
    hp_max: tuple[int, ...] = (),
    granted: dict[int, tuple[str, ...]] | None = None,
    deck: tuple[int, ...] = (),
    turn_seat: int | None = None,
    phase=None,
):
    """构造局面：``granted`` 给座位直接装上技能（获得来源，免武将数据）。"""
    specs = [
        SeatSpec(
            hp=hp[index] if index < len(hp) else 4,
            hp_max=hp_max[index] if index < len(hp_max) else (hp[index] if index < len(hp) else 4),
        )
        for index in range(seats)
    ]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=deck or tuple(range(len(PACK.deck.cards))),
        turn_seat=turn_seat,
        phase=phase,
    )
    for seat, skill_ids in (granted or {}).items():
        state.player(seat).granted_skills.extend(skill_ids)
    return state


def _engine(**kwargs) -> Engine:
    return Engine.from_state(_state(**kwargs), seed=7)


# ---------------------------------------------------------------------------
# ① 上限 = 体力值（既有公式）+ 替换式（界【英姿】②）
# ---------------------------------------------------------------------------


def test_the_base_hand_limit_is_the_current_hp():
    """基础轴：手牌上限 = 当前体力值（§四 L334–L336）。"""
    state = _state(hp=(2,), hp_max=(4,))

    assert hand_limit(state, state.player(0)) == 2


def test_jie_yingzi_replaces_the_limit_with_the_hp_max():
    """替换式：界【英姿】②在手 ⇒ 手牌上限 = 体力**上限**（不是当前体力值）。"""
    state = _state(hp=(2,), hp_max=(3,), granted={0: (JIE_YINGZI,)})

    assert hand_limit(state, state.player(0)) == 3, "2 点体力、上限 3 ⇒ 上限按上限算"
    assert discard_count(state, state.player(0)) == -3, "手牌 0 张、上限 3 ⇒ 无需弃牌"


def test_the_replacement_reads_the_hp_max_at_query_time():
    """派生量：**体力上限**变化立刻反映到上限（不是写入时快照）。"""
    state = _state(hp=(2,), hp_max=(3,), granted={0: (JIE_YINGZI,)})
    assert hand_limit(state, state.player(0)) == 3

    state.player(0).hp_max = 2

    assert hand_limit(state, state.player(0)) == 2, "上限 3 → 2 后立刻按 2 读"


def test_the_standard_yingzi_does_not_replace_the_limit():
    """反例：标准版【英姿】只有"多摸一张"，没有替换式（手牌上限仍 = 当前体力值）。"""
    state = _state(hp=(2,), hp_max=(3,), granted={0: ("hero.zhouyu.yingzi",)})

    assert hand_limit(state, state.player(0)) == 2


def test_losing_the_replacement_skill_withdraws_it_immediately():
    """连续型效果随实例存亡（D9）：技能**失去**后替换式立刻消失。"""
    engine = _engine(hp=(2,), hp_max=(3,), granted={0: (JIE_YINGZI,)})
    assert hand_limit(engine.state, engine.state.player(0)) == 3

    engine.lose_skill(0, JIE_YINGZI)

    assert hand_limit(engine.state, engine.state.player(0)) == 2, "实例没了 ⇒ 回到体力值"


def test_suppressing_a_non_locked_skill_does_not_touch_the_locked_replacement():
    """【英姿】是**锁定技**：座位级"所有非锁定技失效"挡不住它（D10 的谓词只盖非锁定）。"""
    engine = _engine(hp=(2,), hp_max=(3,), granted={0: (JIE_YINGZI,)})

    engine.suppress_skills(0, "yijue")

    assert hand_limit(engine.state, engine.state.player(0)) == 3, "锁定技不受失效谓词影响"


# ---------------------------------------------------------------------------
# ② 加式：回合作用域 + 一次性效果不随来源撤回（D9）
# ---------------------------------------------------------------------------


def test_a_turn_scoped_bonus_adds_to_the_limit():
    """加式：座位级 +X 落在当前回合里，手牌上限 = 基础 / 替换值 + X。"""
    state = _state(hp=(2,), hp_max=(4,))
    state.player(0).hand_limit_bonus = 2

    assert hand_limit(state, state.player(0)) == 4


def test_the_bonus_stacks_on_top_of_the_replacement():
    """两轴叠加：替换式先落（= 体力上限），加式再往上加。"""
    state = _state(hp=(2,), hp_max=(3,), granted={0: (JIE_YINGZI,)})
    state.player(0).hand_limit_bonus = 1

    assert hand_limit(state, state.player(0)) == 4


def test_the_turn_scoped_bonus_expires_at_turn_end():
    """过期 = 回合末：不是"到某张牌结算完"，也不是"到自己的下一个回合"。"""
    engine = _engine(hp=(2,), hp_max=(4,), turn_seat=0)
    engine.state.player(1).hand_limit_bonus = 2

    run_turns(
        engine,
        [PassPlayer() for _ in range(2)],
        turns=1,
    )

    assert engine.state.player(1).hand_limit_bonus == 0
    assert hand_limit(engine.state, engine.state.player(1)) == 4


def test_a_one_shot_bonus_is_not_withdrawn_when_its_source_skill_is_lost():
    """D9：一次性效果**不随来源消失撤回** —— 清俭的 +X 在技能失去后仍留到回合末。"""
    state = _state(hp=(2,), hp_max=(4,), granted={0: (JIE_QINGJIAN,)})
    state.player(1).hand_limit_bonus = 2
    engine = Engine.from_state(state, seed=7)

    engine.lose_skill(0, JIE_QINGJIAN)

    assert hand_limit(engine.state, engine.state.player(1)) == 6, (
        "+X 住座位状态，不读技能实例"
    )


# ---------------------------------------------------------------------------
# ③ 界黄月英【集智】：摸一张；若**摸到的**那张是基本牌，可弃置它换本回合 +1
#
# 「此牌」= **集智摸的那一张**（spec D20；规则书 L2199 的注记是误读，见 `06`（§5「程序化约定与偏离一手」））。
# ---------------------------------------------------------------------------


def test_jie_jizhi_draws_a_card_on_a_non_transformed_trick():
    """正例（与标准版同句）：非转化、非延时的锦囊 ⇒ 问一次，摸牌堆顶那一张。

    手牌序是**次序事实**：集智那一张先摸，随后才轮到【无中生有】自己的两张。
    """
    trick = cards_of_type(PACK, "ex_nihilo")[0]
    drawn = cards_of_type(PACK, "slash")[0]

    engine, deck_before = _use_trick(
        trick, third=drawn, seat0_skill=JIE_JIZHI, discard=False
    )

    assert engine.state.player(0).hand_limit_bonus == 0, "摸了基本牌但没选弃置 ⇒ 不加"
    assert deck_before[0] == drawn
    assert list(engine.state.player(0).hand) == [0, 1, drawn, *deck_before[1:3]], (
        "集智先摸牌堆顶一张，随后【无中生有】再摸两张"
    )


def test_jie_jizhi_fires_on_a_delayed_non_transformed_trick():
    """界版牌面**没有**"非延时类"（§十九 L2104–L2105）：真【乐不思蜀】照常触发、摸一张。

    与标准版（§九 L997 的"非延时类锦囊牌"）相反；标准版对同一张牌的反例见
    ``tests/test_hero_use_after.py::test_jizhi_does_not_fire_on_a_delayed_trick``。
    "此牌"是**摸到的**那张（D20）：摸到锦囊 ⇒ 不产生"弃置换 +1"的询问，上限不加。
    """
    delayed = cards_of_type(PACK, "indulgence")[0]
    drawn = cards_of_type(PACK, "dismantlement")[0]
    fillers = _deck_excluding(drawn, delayed)

    engine, deck_before = _use_trick(
        delayed, third=drawn, seat0_skill=JIE_JIZHI, discard=True
    )

    assert deck_before[0] == drawn
    assert len(_activation_queries(engine, JIE_JIZHI)) == 1, (
        "界版对延时类非转化锦囊照样问一次「是否发动」"
    )
    assert list(engine.state.player(0).hand) == [*fillers[:2], drawn], (
        "集智摸到牌堆顶那一张；延时锦囊自身去判定区、不再摸牌"
    )
    assert engine.state.player(0).hand_limit_bonus == 0
    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and getattr(record.query, "skill_id", "") == JIE_JIZHI_LIMIT
    ], "摸到的是锦囊 ⇒ 不产生「是否弃置」询问"


def test_jie_jizhi_discards_the_drawn_basic_card_for_plus_one():
    """「若此牌为基本牌」= **摸到的那张**；弃置它 ⇒ 本回合手牌上限 +1。"""
    trick = cards_of_type(PACK, "ex_nihilo")[0]
    drawn = cards_of_type(PACK, "slash")[0]

    engine, _deck_before = _use_trick(
        trick, third=drawn, seat0_skill=JIE_JIZHI, discard=True
    )

    assert drawn not in engine.state.player(0).hand, "摸到的那张被弃置"
    assert drawn in engine.state.discard
    assert engine.state.player(0).hand_limit_bonus == 1
    assert hand_limit(engine.state, engine.state.player(0)) == 5, "4 点体力 + 1"


def test_jie_jizhi_declining_the_discard_keeps_the_card_and_the_limit():
    """反例（可选性）：答"不弃置" ⇒ 牌留在手里、上限不加。"""
    trick = cards_of_type(PACK, "ex_nihilo")[0]
    drawn = cards_of_type(PACK, "slash")[0]

    engine, deck_before = _use_trick(
        trick, third=drawn, seat0_skill=JIE_JIZHI, discard=False
    )

    assert list(engine.state.player(0).hand) == [0, 1, drawn, *deck_before[1:3]]
    assert engine.state.player(0).hand_limit_bonus == 0


def test_jie_jizhi_does_not_offer_the_discard_when_the_drawn_card_is_not_basic():
    """反例（"若此牌为基本牌"）：摸到锦囊 ⇒ 连第二个询问都不发。"""
    trick = cards_of_type(PACK, "ex_nihilo")[0]
    drawn = cards_of_type(PACK, "dismantlement")[0]

    engine, deck_before = _use_trick(
        trick, third=drawn, seat0_skill=JIE_JIZHI, discard=True
    )

    assert list(engine.state.player(0).hand) == [0, 1, drawn, *deck_before[1:3]], (
        "非基本牌不弃"
    )
    assert engine.state.player(0).hand_limit_bonus == 0
    assert not [
        record
        for record in engine.records
        if record.kind == "query" and getattr(record.query, "skill_id", "") == JIE_JIZHI_LIMIT
    ], "非基本牌 ⇒ 不产生「是否弃置」询问"


def test_jie_jizhi_declining_the_activation_draws_and_adds_nothing():
    """反例（可选性）：不发动 ⇒ 少摸集智那一张、上限不加。"""
    trick = cards_of_type(PACK, "ex_nihilo")[0]
    drawn = cards_of_type(PACK, "slash")[0]

    engine, deck_before = _use_trick(
        trick, third=drawn, seat0_skill=JIE_JIZHI, activate=False
    )

    assert engine.state.player(0).hand_limit_bonus == 0
    assert list(engine.state.player(0).hand) == [0, 1, *deck_before[:2]], (
        "只有【无中生有】自己的两张；集智那一张没摸"
    )


# ---------------------------------------------------------------------------
# ④ 界夏侯惇【清俭】：**别人的回合**获得牌 ⇒ 交给另一名角色 ⇒ 当前回合角色 +X
#
# X = 给出的牌**包含的类别数**（基本 / 锦囊 / 装备三档）。作用对象是**当前回合角色**
# （跨座位轴），不是清俭的持有者。获取来源 = 【五谷丰登】的"处理区 → 手牌"（真"获得"，
# 不是摸牌）。
# ---------------------------------------------------------------------------


def _harvest_engine(*, second_gain: bool = False):
    """座位 0 的回合：座位 1（清俭）从【五谷丰登】获得一张。

    ``second_gain``：同一回合里再用一张【杀】打座位 1，令其经【反馈】**再获得**一张
    （第二次获得不再触发清俭——每回合限一次）。
    """
    harvest = cards_of_type(PACK, "harvest")[0]
    basic = cards_of_type(PACK, "slash")[0]
    trick = cards_of_type(PACK, "dismantlement")[0]
    slash = cards_of_type(PACK, "slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(harvest, slash), identity=Identity.LORD),
            SeatSpec(hand=(basic, trick)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(harvest, basic, trick, slash),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    state.player(1).granted_skills.extend(
        (JIE_QINGJIAN, "hero.simayi.fankui") if second_gain else (JIE_QINGJIAN,)
    )
    engine = Engine.from_state(state, seed=7)
    players = [
        _QingjianDriver(seat=seat, second_gain=second_gain) for seat in range(4)
    ]
    run_until(
        engine,
        players,
        lambda e: _harvest_settled(e) and (not second_gain or _slash_played(e)),
    )
    return engine


def _harvest_settled(engine) -> bool:
    return any(
        record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.type == "harvest" for card in record.cards)
        for record in engine.records
    )


def _slash_played(engine) -> bool:
    return any(
        record.kind == "timing"
        and record.timing is Timing.DAMAGE_AFTER_TAKE
        for record in engine.records
    )


class _QingjianDriver:
    """按座位应答：清俭的两步 + （可选）反馈一步；其余最小合法。"""

    def __init__(self, *, seat: int, second_gain: bool) -> None:
        self.seat = seat
        self.second_gain = second_gain
        self.played_harvest = False
        self.played_slash = False
        self.qingjian_queries: list = []

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0:
            if not self.played_harvest:
                harvest = self._find_use(query, "harvest")
                if harvest is not None:
                    self.played_harvest = True
                    return harvest
            if self.second_gain and not self.played_slash:
                slash = self._find_use(query, "slash", targets=(1,))
                if slash is not None:
                    self.played_slash = True
                    return slash
            return next(
                action for action in query.legal_actions if action.kind == "EndPhase"
            )
        skill_id = getattr(query, "skill_id", None)
        if query.kind == QueryKind.SKILL_ACTIVATION and skill_id == JIE_QINGJIAN:
            self.qingjian_queries.append(query)
            return next(
                action
                for action in query.legal_actions
                if getattr(action, "kind", None) == "ActivateSkill"
            )
        if query.kind == QueryKind.SKILL_ACTIVATION and skill_id == "hero.simayi.fankui":
            return next(
                action
                for action in query.legal_actions
                if getattr(action, "kind", None) == "ActivateSkill"
            )
        if query.kind == QueryKind.SELECT_CARDS:
            zones = query.spec.zones
            if (
                query.actor == 1
                and zones == (Zone.HAND, Zone.EQUIP)
                and query.spec.total[0] == 1
            ):
                # 清俭的"展示任意张牌"：给**前两张**（基本 + 锦囊 = 两类）。
                for action in query.legal_actions:
                    if isinstance(action, SelectCards) and action.picks == (0, 1):
                        return action
                raise AssertionError("清俭的选牌面里没有前两张的候选")
            return query.legal_actions[0]
        if skill_id == "qingjian:seat":
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "seat:2":
                    return action
            raise AssertionError("清俭的座位池里没有被选座位 2")
        if query.kind == QueryKind.DODGE:
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.RESPOND:
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return query.legal_actions[0]

    @staticmethod
    def _find_use(query, type_id: str, targets=None):
        for action in query.legal_actions:
            if not isinstance(action, UseCard) or action.card.type != type_id:
                continue
            if targets is None or action.targets == targets:
                return action
        return None


def test_qingjian_shows_any_hand_or_equipment_card():
    """默认读法「牌前没写区域 ⇒ 手牌 + 装备区」：清俭的展示步两者都收。"""
    from sgs.skills import CATALOG

    step = CATALOG[JIE_QINGJIAN].hooks[0].flow.steps[0]
    assert step.zones == (Zone.HAND, Zone.EQUIP)
    assert step.lo == 1 and step.hi is None


def test_qingjian_gives_cards_and_boosts_the_turn_player_not_itself():
    """正例（跨座位）：清俭在**别人**的回合获得牌 ⇒ +X 落在**当前回合角色**身上。"""
    engine = _harvest_engine()
    state = engine.state

    assert state.player(0).hand_limit_bonus == 2, "基本 + 锦囊 = 两类 ⇒ 当前回合角色 +2"
    assert state.player(1).hand_limit_bonus == 0, "清俭的持有者自己不加"
    assert hand_limit(state, state.player(0)) == 6
    assert hand_limit(state, state.player(1)) == 4
    assert len(state.player(1).hand) == 1, "两张手牌给出去了（原两张 + 获得一张 - 给两张）"
    given = {
        cards_of_type(PACK, "slash")[0],
        cards_of_type(PACK, "dismantlement")[0],
    }
    assert given <= set(state.player(2).hand), (
        "给出的两张牌进了选定的另一名角色的手牌"
    )


class _PlayExNihiloThenPass:
    """出牌阶段用一张【无中生有】；遇到清俭的发动询问一律 Pass（只验"问了"）。"""

    def __init__(self) -> None:
        self.qingjian: list = []
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and not self.used:
            self.used = True
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type == "ex_nihilo":
                    return action
            return next(action for action in query.legal_actions if action.kind == "EndPhase")
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QINGJIAN:
            self.qingjian.append(query)
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def test_qingjian_triggers_on_a_draw_outside_its_owners_draw_phase():
    """正例（Q1：**摸牌也算获得**，票 25 落地）：出牌阶段用【无中生有】摸两张 ⇒ 清俭发问。

    同一持有者在自己**摸牌阶段**的摸牌不问（"于你的摸牌阶段外"的阶段门，见
    `test_qingjian_does_not_trigger_inside_its_owners_draw_phase`）；本用例把同一动作搬到
    出牌阶段——旧判据（"获得 ≠ 摸牌"）下这里**一次询问都没有**，Q1 之后才有。
    """
    ex_nihilo = cards_of_type(PACK, "ex_nihilo")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec()],
        deck=_deck_excluding(ex_nihilo),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(JIE_QINGJIAN)
    engine = Engine.from_state(state, seed=7)
    players = [_PlayExNihiloThenPass(), _PlayExNihiloThenPass()]

    run_turns(engine, players, turns=1)

    assert len(players[0].qingjian) == 1, "出牌阶段的摸牌也是『获得』 ⇒ 问一次"
    assert engine.state.player(0).hand_limit_bonus == 0, "本例在发动询问处 Pass，不加手牌上限"


def test_qingjian_does_not_trigger_inside_its_owners_draw_phase():
    """反例（"于你的摸牌阶段外"）：自己的摸牌阶段里获得牌不触发。"""
    # 【突袭】在自己的摸牌阶段"获得"其他角色的手牌 ⇒ 时点在摸牌阶段内。
    tuxi = "hero.zhangliao.tuxi"
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(cards_of_type(PACK, "peach")[0],)), SeatSpec()],
        deck=_deck_excluding(cards_of_type(PACK, "peach")[0]),
        turn_seat=1,
    )
    state.player(1).granted_skills.extend((JIE_QINGJIAN, tuxi))
    engine = Engine.from_state(state, seed=7)
    players = [_QingjianDriver(seat=0, second_gain=False), _QingjianDriver(seat=1, second_gain=False)]

    run_until(engine, players, lambda e: ran_timing(e, Timing.PHASE_PLAY_START))

    assert players[1].qingjian_queries == [], "自己的摸牌阶段内获得 ⇒ 不触发"
    assert engine.state.player(1).hand_limit_bonus == 0


def test_lianying_triggers_when_the_last_hand_cards_are_given_away():
    """Q2（票 24，用户裁定）：**交给别人的手牌也算失去**。

    清俭把手里最后两张牌交给另一名角色（手牌 → 别人的手牌）：对交出方是"失去手牌"，
    【连营】因此该问。旧判据把"去向是手牌"一律排除（不分是谁的手牌），这条会静默不触发。
    """
    liianying = "hero.luxun.lianying"
    harvest = cards_of_type(PACK, "harvest")[0]
    basic = cards_of_type(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(harvest,), identity=Identity.LORD),
            SeatSpec(hand=(basic,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(harvest, basic),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    state.player(1).granted_skills.extend((JIE_QINGJIAN, liianying))
    engine = Engine.from_state(state, seed=7)
    players = [_QingjianDriver(seat=seat, second_gain=False) for seat in range(4)]

    run_until(
        engine,
        players,
        lambda e: getattr(e.pending, "skill_id", None) == liianying or _harvest_settled(e),
    )

    assert getattr(engine.pending, "skill_id", None) == liianying, (
        f"把最后的手牌交给别人 ⇒ 失去手牌 ⇒ 连营该问（停在 {type(engine.pending).__name__}）"
    )


def test_qingjian_is_once_per_turn():
    """每回合限一次：同一回合第二次获得牌不再问、不再加。"""
    engine = _harvest_engine(second_gain=True)

    queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and record.query.skill_id == JIE_QINGJIAN
    ]
    assert len(queries) == 1, "同一回合两次获得 ⇒ 只问一次"
    assert engine.state.player(0).hand_limit_bonus == 2, "第二次不再叠加"


def test_qingjian_bonus_expires_when_the_turn_ends():
    """+X 的过期 = 回合末（与其它一次性效果同一收尾点）。"""
    engine = _harvest_engine()
    assert engine.state.player(0).hand_limit_bonus == 2

    run_turns(engine, [PassPlayer() for _ in range(4)], turns=1)

    assert engine.state.player(0).hand_limit_bonus == 0
    assert hand_limit(engine.state, engine.state.player(0)) == 4


def test_the_bonus_of_a_dead_turn_player_is_not_retargeted_and_expires_at_turn_end():
    """裁定（`06`（§5「程序化约定与偏离一手」））：作用对象**中途阵亡** ⇒ 增量仍记在该座位名下、不重定向，
    只是死者不再弃牌（读不到它）；回合末与其它 ``TURN`` 作用域的状态一起清掉。"""
    engine = _engine(hp=(4,), hp_max=(4,), turn_seat=0)
    engine.add_hand_limit_bonus(0, 2)
    engine.state.player(0).alive = False

    assert engine.state.player(0).hand_limit_bonus == 2, "阵亡不撤回已落的一次性效果"
    assert engine.state.player(1).hand_limit_bonus == 0, "不重定向到别人"
    assert hand_limit(engine.state, engine.state.player(0)) == 6

    run_turns(engine, [PassPlayer(), PassPlayer()], turns=1)

    assert engine.state.player(0).hand_limit_bonus == 0


# ---------------------------------------------------------------------------
# ⑤ 界夏侯惇【刚烈】（较界版重写）：判定红色 ⇒ 你对来源造成 1 点伤害；
#    黑色 ⇒ 你弃置来源的一张牌（**不是**标准版的"来源二选一"）
#
# 规则原文 §十九 L2023–L2025。与标准【刚烈】的差别是整句重写（票 01 的"判定 → 来源
# 二选一"不在这里复用），故条件同形（受伤者是自己 ∧ 有可结算来源）、结算段全新。
# ---------------------------------------------------------------------------


def _suit_card(suit: Suit) -> int:
    for index, card in enumerate(PACK.deck.cards):
        if card.suit is suit:
            return index
    raise AssertionError(f"标准 108 张里没有 {suit} 花色的牌")


class _JieGanglieDriver:
    """应答界【刚烈】：是否发动 + （黑色时）由**夏侯惇**选来源的一张牌。"""

    def __init__(self, *, activate: bool = True) -> None:
        self.activate = activate
        self.activations: list = []
        self.selections: list = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_GANGLIE:
            self.activations.append(query)
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if getattr(action, "kind", None) == "ActivateSkill"
                )
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.SELECT_CARDS:
            self.selections.append(query)
            return query.legal_actions[0]
        return _pass_or_first(query)


def _advance(engine, players, stop):
    """逐步推进到 ``stop(engine)`` 成立（与 ``tests/test_hero_damage_after.py`` 同一手法：
    每步都查停止条件，而不是只在询问时查——「帧出栈」这一刻没有询问）。"""
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
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _jie_ganglie_engine(
    *,
    judge_suits: tuple[Suit, ...] = (Suit.SPADE,),
    source_hand: tuple[int, ...] = (),
    source_equip: tuple[int, ...] = (),
    source_judge: tuple[int, ...] = (),
    target: int = 1,
    holder: int = 1,
    source: int | None = 0,
    seats: int = 2,
    amount: int = 1,
    activate: bool = True,
):
    """压一个 DAMAGE 子帧（打向 ``target``），推进到该帧出栈。

    牌堆顶 = ``judge_suits`` ⇒ 界【刚烈】的逐点判定结果可控。
    """
    judges = tuple(_suit_card(suit) for suit in judge_suits)
    channel = cards_of_type(PACK, "slash")[0]
    specs = [SeatSpec() for _ in range(seats)]
    if source is not None:
        specs[source] = SeatSpec(
            hand=tuple(source_hand),
            equip=tuple(source_equip),
            judge=tuple(source_judge),
        )
    used = {*judges, channel, *source_hand, *source_equip, *source_judge}
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index not in used)
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=(*judges, *deck),
        processing=(channel,),
    )
    state.player(holder).granted_skills.append(JIE_GANGLIE)
    engine = Engine.from_state(state, seed=7)
    driver = _JieGanglieDriver(activate=activate)
    players = [PassPlayer() for _ in range(seats)]
    players[holder] = driver
    frame = engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": state.card(channel),
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": target,
            "channel": state.card(channel),
            "nature": "NORMAL",
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    _advance(
        engine,
        players,
        lambda e: any(
            record.kind == "frame" and record.op == "pop" and record.frame_id == frame.frame_id
            for record in e.records
        ),
    )
    return engine, driver


def test_jie_ganglie_a_red_judgment_deals_one_damage_to_the_source():
    """红色（方块是红色）⇒ 你对伤害来源造成 1 点伤害；不问选牌。"""
    hand = tuple(cards_of_type(PACK, "dodge")[:2])

    engine, driver = _jie_ganglie_engine(
        judge_suits=(Suit.DIAMOND,), source_hand=hand
    )

    assert len(driver.activations) == 1, "受伤后问一次「是否发动」"
    assert driver.selections == [], "红色分支不问选牌"
    assert engine.state.player(0).hp == 3, "伤害来源掉 1 点"
    assert engine.state.player(0).hand == list(hand), "不弃牌"


def test_jie_ganglie_a_black_judgment_makes_you_discard_one_card_of_the_source():
    """黑色 ⇒ **你**（夏侯惇）弃置来源的一张牌；来源不掉血、弃几张由你选。"""
    hand = tuple(cards_of_type(PACK, "dodge")[:2])

    engine, driver = _jie_ganglie_engine(judge_suits=(Suit.SPADE,), source_hand=hand)

    assert len(driver.activations) == 1
    assert len(driver.selections) == 1, "黑色 ⇒ 一次选牌"
    assert driver.selections[0].actor == 1, "选牌的是**夏侯惇**（『你弃置』），不是来源"
    assert len(engine.state.player(0).hand) == 1, "来源掉一张牌"
    assert engine.state.player(0).hp == 4, "黑色不造成伤害"


def test_jie_ganglie_does_not_take_a_judgment_card():
    """默认读法「牌前没写区域 ⇒ 手牌 + 装备区」：判定区不在「弃置一张牌」的范围内。"""
    source_judge = (cards_of_type(PACK, "lightning")[0],)

    engine, driver = _jie_ganglie_engine(
        judge_suits=(Suit.SPADE,), source_judge=source_judge
    )

    assert driver.selections == [], "来源只有判定牌 ⇒ 没有可弃的合法候选，不发询问"
    assert source_judge[0] in engine.state.player(0).judge, "判定牌不动"


def test_jie_ganglie_can_take_an_equipment_card():
    """「一张牌」= 来源区域里的一张（含装备区），不是"一张手牌"。"""
    source_equip = (cards_of_type(PACK, "eight_diagrams")[0],)

    engine, driver = _jie_ganglie_engine(
        judge_suits=(Suit.CLUB,), source_equip=source_equip
    )

    assert len(driver.selections) == 1
    assert source_equip[0] not in engine.state.player(0).equip, "装备被弃置"
    assert source_equip[0] in engine.state.discard


def test_jie_ganglie_triggers_once_per_point_of_damage():
    """「受到 **1 点**伤害后」：一次 2 点伤害 ⇒ 两次发动询问（与标准版的一次不同）。"""
    hand = tuple(cards_of_type(PACK, "dodge")[:3])

    engine, driver = _jie_ganglie_engine(
        judge_suits=(Suit.HEART, Suit.DIAMOND), source_hand=hand, amount=2
    )

    assert len(driver.activations) == 2, "逐点触发"
    assert engine.state.player(1).hp == 2, "2 点伤害照常扣"
    assert engine.state.player(0).hp == 2, "两次红色判定各造成 1 点"


def test_jie_ganglie_is_not_offered_without_a_damage_source():
    """反例：无来源伤害（【闪电】一类）⇒ 没有"伤害来源"可结算，连询问都不发。"""
    engine, driver = _jie_ganglie_engine(source=None)

    assert driver.activations == []
    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_CARDS
    ]


def test_jie_ganglie_is_not_offered_when_someone_else_takes_the_damage():
    """反例：别人挨打 ⇒ 界夏侯惇不被问（条件自校持有者）。"""
    engine, driver = _jie_ganglie_engine(target=2, holder=1, seats=3)

    assert driver.activations == []
    assert engine.state.player(2).hp == 3


# ---------------------------------------------------------------------------
# ⑥ 界周瑜【反间】：展示一张手牌交给一名其他角色，然后令其二选一
#    —— ①展示所有手牌，弃置与此牌花色相同的所有牌；②失去 1 点体力
#
# 规则原文 §十九 L2155–L2158。与标准【反间】（选花色 → 猜错受伤）是**整句重写**。
# ---------------------------------------------------------------------------


def _heart_card(*, exclude: set[int] | None = None) -> int:
    exclude = exclude or set()
    for index, card in enumerate(PACK.deck.cards):
        if card.suit is Suit.HEART and index not in exclude:
            return index
    raise AssertionError("标准 108 张里没有红桃")


def _spade_card(*, exclude: set[int] | None = None) -> int:
    exclude = exclude or set()
    for index, card in enumerate(PACK.deck.cards):
        if card.suit is Suit.SPADE and index not in exclude:
            return index
    raise AssertionError("标准 108 张里没有黑桃")


class _JieFanjianDriver:
    """应答界【反间】：发动（出牌阶段骨架）→ 选牌 → 选目标 → 目标二选一。"""

    def __init__(self, *, seat: int, choice: str) -> None:
        self.seat = seat
        self.choice = choice
        self.target_options: list = []

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0:
            for action in query.legal_actions:
                if (
                    getattr(action, "kind", None) == "ActivateSkill"
                    and getattr(action, "skill_id", None) == JIE_FANJIAN
                ):
                    return action
            for action in query.legal_actions:
                if action.kind == "EndPhase":
                    return action
        if query.kind == QueryKind.SELECT_CARDS and query.actor == 0:
            return query.legal_actions[0]
        if getattr(query, "skill_id", None) == "fanjian:seat":
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "seat:1":
                    return action
        if getattr(query, "skill_id", None) == "fanjian:choice":
            self.target_options.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == self.choice:
                    return action
        return _pass_or_first(query)


def _jie_fanjian_engine(*, choice: str):
    """座位 0 = 界周瑜（反间），座位 1 = 目标（手里有同花色 + 异花色各一张）。

    座位 0 的出牌阶段里发动反间：交出一张**红桃**给座位 1。
    """
    given = _heart_card()
    other = _spade_card(exclude={given})
    same_suit = _heart_card(exclude={given})
    different_suit = _spade_card(exclude={given, other})
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(given, other)),
            SeatSpec(hand=(same_suit, different_suit)),
        ],
        deck=_deck_excluding(given, other, same_suit, different_suit),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(JIE_FANJIAN)
    engine = Engine.from_state(state, seed=7)
    driver = _JieFanjianDriver(seat=1, choice=choice)
    players = [driver, driver]
    # 推到"目标二选一"这一问，直接作答（这一问是效果里的 ask，不是流水线步骤），
    # 再推到下一次出牌阶段询问（效果已经结算完）。
    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and getattr(e.pending, "skill_id", None) == "fanjian:choice",
    )
    engine.submit(driver.respond(engine.pending))
    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0
        and sum(
            1
            for record in e.records
            if record.kind == "query"
            and record.query.kind == QueryKind.PLAY_PHASE
            and record.query.actor == 0
        )
        >= 2,
    )
    return engine, driver, given, same_suit, different_suit


def test_jie_fanjian_choice_one_discards_every_card_of_the_same_suit():
    """正例①：目标选"展示所有手牌、弃置同花色"⇒ 同花色（含刚拿到的那张）进弃牌堆。"""
    engine, driver, given, same_suit, different_suit = _jie_fanjian_engine(
        choice="discard_suit"
    )

    assert len(driver.target_options) == 1, "二选一由**目标**应答一次"
    assert driver.target_options[0].actor == 1
    assert given in engine.state.discard, "交出去的那张也同花色 ⇒ 一并弃置"
    assert same_suit in engine.state.discard
    assert different_suit in engine.state.player(1).hand, "异花色留下"
    assert engine.state.player(1).hp == 4, "选①不掉体力"
    assert engine.state.player(1).hand == [different_suit]


def test_jie_fanjian_choice_two_loses_one_hp_and_keeps_the_cards():
    """反例（另一分支）：目标选"失去 1 点体力"⇒ 不掉牌、体力 -1。"""
    engine, _driver, given, same_suit, different_suit = _jie_fanjian_engine(choice="lose_hp")

    assert engine.state.player(1).hp == 3
    assert given in engine.state.player(1).hand, "选②不弃牌（刚拿到的牌留下）"
    assert same_suit in engine.state.player(1).hand
    assert different_suit in engine.state.player(1).hand
    assert given not in engine.state.discard


def test_jie_fanjian_reveals_the_given_card_and_the_targets_hand():
    """"展示一张手牌" + 选①时"展示所有手牌"：都用公开的 ``card_reveal`` 落账。"""
    engine, _driver, given, same_suit, _different = _jie_fanjian_engine(
        choice="discard_suit"
    )

    revealed = [
        record.card.card_id
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_reveal"
    ]
    assert given in revealed, "交出的那张是展示的"
    assert same_suit in revealed, "目标展示所有手牌"


def test_jie_fanjian_is_once_per_play_phase():
    """反例（可选性 / 次数）：本出牌阶段发动过 ⇒ 下一次出牌阶段询问里不再给候选。"""
    engine, _driver, _given, _same, _different = _jie_fanjian_engine(choice="lose_hp")

    assert engine.pending is not None and engine.pending.kind == QueryKind.PLAY_PHASE
    assert not any(
        getattr(action, "skill_id", None) == JIE_FANJIAN
        for action in engine.pending.legal_actions
    ), "出牌阶段限一次 ⇒ 用过就不再进候选"


# ---------------------------------------------------------------------------
# ⑦ 界黄月英【奇才】：①锦囊无距离限制（与标准版同句）；②**事件帧无效**
#    —— 其他角色弃置你装备区里的防具牌时，取消之（宝物半句无载体，见 `06`（§5「程序化约定与偏离一手」））
#
# 否定分类四层里的第三层（票 07 的登记表）：事件已经发生、在它自己的帧里被取消。
# ---------------------------------------------------------------------------


def _discard_armor_engine(*, armor: int, trick: str):
    """座位 0 用 ``trick`` 处理座位 1（界黄月英）装备区里的 ``armor``。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(trick,)),
            SeatSpec(equip=(armor,)),
        ],
        deck=_deck_excluding(trick, armor),
        turn_seat=0,
    )
    state.player(1).granted_skills.append(JIE_QICAI)
    engine = Engine.from_state(state, seed=7)
    driver = _TakeZoneCardDriver(seat=0, card_id=armor)
    players = [driver, PassPlayer()]
    _advance(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    action = next(
        action
        for action in engine.pending.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == trick
        and action.targets == (1,)
    )
    engine.submit(action)
    _advance(engine, players, _use_settled_trick(engine, trick))
    return engine


def _use_settled_trick(engine, trick: int):
    def settled(e) -> bool:
        return any(
            record.kind == "timing"
            and record.timing is Timing.RESOLVE_AFTER_END
            and any(card.card_id == trick for card in record.cards)
            for record in e.records
        )

    return settled


class _TakeZoneCardDriver:
    """应答"从目标区域里选一张牌"：选中指定的那张牌（按选择面的牌 id 找位置）。"""

    def __init__(self, *, seat: int, card_id: int | None = None) -> None:
        self.seat = seat
        self.card_id = card_id

    def respond(self, query):
        if query.kind == QueryKind.SELECT_CARDS:
            if self.card_id is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, SelectCards)
                        and len(action.picks) == 1
                        and query.slots[action.picks[0]].card.card_id == self.card_id
                    ):
                        return action
            return query.legal_actions[0]
        if query.kind == QueryKind.PLAY_PHASE and query.actor == self.seat:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type in (
                    "dismantlement",
                    "snatch",
                    "armor",
                ):
                    return action
            for action in query.legal_actions:
                if action.kind == "EndPhase":
                    return action
        return _pass_or_first(query)


def test_jie_qicai_cancels_another_characters_discard_of_your_armor():
    """正例：别人【过河拆桥】你的防具 ⇒ 弃置被取消（牌仍在装备区、没进弃牌堆）。"""
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    trick = cards_of_type(PACK, "dismantlement")[0]

    engine = _discard_armor_engine(armor=armor, trick=trick)

    assert armor in engine.state.player(1).equip, "防具留在装备区"
    assert armor not in engine.state.discard
    assert not [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone.zone is Zone.EQUIP
    ], "整个弃置事件被取消（连一条装备区移动都没有）"


def test_jie_qicai_does_not_cancel_a_weapon_discard():
    """反例（只挡防具）：同样一次拆，目标是武器 ⇒ 照常进弃牌堆。"""
    weapon = cards_of_type(PACK, "green_dragon_blade")[0]
    trick = cards_of_type(PACK, "dismantlement")[0]

    engine = _discard_armor_engine(armor=weapon, trick=trick)

    assert weapon not in engine.state.player(1).equip
    assert weapon in engine.state.discard


def test_jie_qicai_does_not_cancel_a_gain_of_the_armor():
    """反例（弃置≠获得）：【顺手牵羊】把防具**拿走**，不是弃置 ⇒ 不取消。"""
    armor = cards_of_type(PACK, "renwang_shield")[0]
    trick = cards_of_type(PACK, "snatch")[0]

    engine = _discard_armor_engine(armor=armor, trick=trick)

    assert armor not in engine.state.player(1).equip
    assert armor in engine.state.player(0).hand, "顺走成功（进使用者手牌）"


def test_jie_qicai_does_not_cancel_your_own_equipment_replacement():
    """反例（"其他角色"）：自己**顶替**防具（旧防具进弃牌堆）照常发生。"""
    old_armor = cards_of_type(PACK, "eight_diagrams")[0]
    new_armor = cards_of_type(PACK, "renwang_shield")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(old_armor,), hand=(new_armor,)), SeatSpec()],
        deck=_deck_excluding(old_armor, new_armor),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(JIE_QICAI)
    engine = Engine.from_state(state, seed=7)
    driver = _TakeZoneCardDriver(seat=0)
    _advance(
        engine,
        [driver, PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    action = next(
        action
        for action in engine.pending.legal_actions
        if isinstance(action, UseCard) and action.card.card_id == new_armor
    )
    engine.submit(action)
    _advance(engine, [driver, PassPlayer()], _use_settled_trick(engine, new_armor))

    assert old_armor in engine.state.discard, "自己顶替的旧防具照常弃置"
    assert new_armor in engine.state.player(0).equip


def test_jie_qicai_still_lifts_the_distance_limit_of_tricks():
    """①：锦囊无距离限制（与标准版同句、同一个 ``DISTANCE_LIMIT`` 回调）。"""
    snatch = cards_of_type(PACK, "snatch")[0]
    hand = cards_of_type(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(snatch,)),
            SeatSpec(hand=(hand,)),
            SeatSpec(hand=(hand + 1,)),
            SeatSpec(hand=(hand + 2,)),
        ],
        deck=_deck_excluding(snatch, hand, hand + 1, hand + 2),
        turn_seat=0,
    )
    state.player(0).granted_skills.append(JIE_QICAI)

    assert check_targets(state, 0, state.card(snatch)) == (1, 2, 3), (
        "奇才①：距离不再是锦囊的目标条件"
    )


# ---------------------------------------------------------------------------
# ⑧ 界周瑜【英姿】①：锁定技 ⇒ 摸牌阶段**不问**直接多摸一张
#
# 与标准版【英姿】（非锁定、"你可以"）的差别只有锁定性；②（手牌上限 = 体力上限）已在
# 第 ① 节用替换式覆盖。这里用**真武将数据**（``hero="jie_zhouyu"``）走发放段。
# ---------------------------------------------------------------------------


def test_jie_yingzi_draws_the_extra_card_without_asking():
    """锁定技：不产生 ``SKILL_ACTIVATION`` 询问，摸牌阶段直接 3 张。"""
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_zhouyu"), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    run_until(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert list(engine.state.player(0).hand) == list(deck[:3]), "多摸一张"
    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and record.query.skill_id == JIE_YINGZI
    ], "锁定技不问「是否发动」"


def test_jie_yingzi_does_not_trigger_in_another_players_draw_phase():
    """默认读法「没写谁的阶段 ⇒ 仅自己的」：界【英姿】只在周瑜自己的摸牌阶段生效。

    锁定技不走 `SKILL_ACTIVATION` 询问，故 `available=False` 必须在派发环里也生效；
    否则别人的摸牌阶段会替周瑜多摸一张。
    """
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="jie_zhouyu")],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    run_until(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DRAW_END),
    )

    assert engine.state.player(1).hand == [], "周瑜不在自己的摸牌阶段 ⇒ 不能替自己摸牌"
    assert list(engine.state.player(0).hand) == list(deck[:2]), "只有当前回合角色摸两张"
