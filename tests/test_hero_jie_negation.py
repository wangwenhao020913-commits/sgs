"""M6 票 07：界甘宁 / 界貂蝉与**否定分类四层**的行为面（spec D15 / S8）。

票面的四个否定层**不统一抽象**，各自实现、只共用一张审计清单
（:mod:`tests.test_negation_sites`）：

=============================  ===========================================  ==================
层                              语义                                          本票载体
=============================  ===========================================  ==================
目标产生前取消                  根本不会被选为目标（指定目标与后续发动一并取消）  无（【帷幕】不在本批）
目标级无效                      目标照常指定，但不产生效果                     界甘宁【奋威】
事件帧无效                      某个**事件帧**无效                            票 08【奇才】②
响应窗口不产生                  不可无懈 + 不可打出对应牌                     界貂蝉【离间】
=============================  ===========================================  ==================

本文件覆盖四名技能：

- **界甘宁【奇袭】**：与标准版【甘宁】牌面**逐字相同**（黑色牌当【过河拆桥】），
  故共用同一条转化技声明与实现，只是另一个武将 id（不 fork）；
- **界甘宁【奋威】**（限定技）：一张锦囊牌指定至少两个目标后，可令此牌对其中任意名
  目标角色无效 ⇒ **目标级无效**。未选中的目标照常结算；被无效的目标走到自己的
  "使用结算开始时"就终止（故**没有**"生效前"的无懈窗口）；
- **界貂蝉【离间】**：令后者视为对前者使用一张**不能被响应**的【决斗】⇒
  **响应窗口不产生**：无懈询问不出现、决斗的【杀】响应也不出现（决斗照常结算，
  败方受到伤害——"不可响应"不是"抵消"）。**选择序**：界版第一次点的是【决斗】**目标**
  （前者）、第二次才是**使用者**（后者）；标准版不指定次序（既有口径 = 先使用者）；
- **界貂蝉【闭月】**：结束阶段有手牌摸 1 / 没有手牌摸 2。

三条**裁定**（`06` §5「程序化约定与偏离一手」逐条登记）：

1. 【奋威】可选的目标集合 = 此牌的**全部**目标（含发动者本人，当他是目标之一时）；
   牌面"其中任意名目标角色"没有把发动者排除在外；
2. 【离间】的"不能被响应"只关闭**牌类响应窗口**（无懈 / 决斗的【杀】），**不**挡
   "生效时"类技能（如【谦逊】）对这张虚拟【决斗】的介入——"响应"是用牌响应；
3. 【离间】的选择序：§十九 L2212–L2214 的"前者 / 后者"把**第一次点的人钉成【决斗】
   目标**；标准版 §九 L1068–L1069 的牌面不指定次序，故界版先目标、标准版先使用者。
"""

from __future__ import annotations

from sgs.agents import tape_from_journal
from sgs.data import (
    Color,
    Gender,
    Identity,
    RuleConfig,
    color_of_suit,
    load_content_pack,
)
from sgs.engine import Engine
from sgs.skills import conversion_offers, limited_skill_used
from sgs.skills.preactivation import preactivation_for
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    PlayCard,
    QueryKind,
    SelectOption,
    SelectOptions,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_QIXI = "hero.jie_ganning.qixi"
FENWEI = "hero.jie_ganning.fenwei"
JIE_LIJIAN = "hero.jie_diaochan.lijian"
JIE_BIYUE = "hero.jie_diaochan.biyue"
"""本票四条技能的全限定 id（与 §十九 L2144–L2148 / L2212–L2216 对齐）。"""


# ---------------------------------------------------------------------------
# 脚本参与者与候选 / 询问的小工具（与既有族测试同一姿态）
# ---------------------------------------------------------------------------


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = PassPlayer()
        self.queries = []

    def respond(self, query):
        self.queries.append(query)
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _first(query, action_type):
    return _find(query, lambda action: isinstance(action, action_type))


def _activate(query, skill_id: str):
    return _find(
        query,
        lambda action: isinstance(action, ActivateSkill) and action.skill_id == skill_id,
    )


def _seat_option(query, seat: int):
    return _find(
        query,
        lambda action: isinstance(action, SelectOption)
        and action.choice == f"seat:{seat}",
    )


def _decline(query):
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
        return _find(query, lambda action: isinstance(action, Pass))
    return None


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _first_card(*, type_id: str | None = None, color: Color | None = None):
    for card_id, card in enumerate(PACK.deck.cards):
        if type_id is not None and card.type != type_id:
            continue
        if color is not None and color_of_suit(card.suit) is not color:
            continue
        return card_id
    raise AssertionError("夹具里找不到满足条件的牌")


def _deltas(engine: Engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


def _queries(engine: Engine, kind: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _run(state, players, stop, *, seed: int = 7):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _skill_frame_popped(engine) -> bool:
    return any(
        record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is FrameKind.SKILL
        for record in engine.records
    )


# ---------------------------------------------------------------------------
# 界甘宁【奇袭】：与标准版【甘宁】牌面逐字相同 ⇒ 共用同一条转化声明
#
# §十九 L2145：奇袭：出牌阶段，你可以将一张黑色牌当【过河拆桥】使用。
# 标准版 §九 L1025 同字。**不 fork**：界版只是另一个武将 id，候选 / 目标池 / 使用链
# 全走 heroes.CONVERSION_SKILLS 里同一个平移声明（共用同一条谓词与同一条产物）。
# ---------------------------------------------------------------------------


def _ganning_state(hand: tuple[int, ...]):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="jie_ganning"), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )


def test_jie_qixi_is_offered_for_a_black_card_only():
    """正例：界甘宁的黑牌能当【过河拆桥】；反例：红牌不行（同一份颜色谓词）。"""
    black = _first_card(color=Color.BLACK)
    red = _first_card(color=Color.RED)
    state = _ganning_state((black, red))

    offers = conversion_offers(state, 0, "dismantlement")

    assert (black, JIE_QIXI) in offers, "界甘宁的黑牌应能当【过河拆桥】（§十九 L2145）"
    assert all(card_id != red for card_id, _skill in offers), "红牌不是黑色 ⇒ 不能转化"


def test_jie_qixi_is_the_standard_implementation_not_a_fork():
    """界版与标准版共用同一条声明：两者都能把同一个**牌面**当作【过河拆桥】。

    判据是**候选面同源**而不是"实现文件相同"：标准甘宁与界甘宁在同一构造里各自
    解析实例，两个武将 id 给出的候选来源牌集合逐张相同。
    """
    black = _first_card(color=Color.BLACK)
    standard = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(black,), hero="ganning"), SeatSpec()],
        deck=deck_excluding(PACK, black),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )

    assert conversion_offers(standard, 0, "dismantlement") == (
        (black, "hero.ganning.qixi"),
    )
    assert conversion_offers(_ganning_state((black,)), 0, "dismantlement") == (
        (black, JIE_QIXI),
    )


# ---------------------------------------------------------------------------
# 界貂蝉【闭月】：结束阶段，若你有 / 没有手牌，你可以摸一 / 两张牌
#
# §十九 L2215：闭月：结束阶段，若你有 / 没有手牌，你可以摸一 / 两张牌。
# 与标准版【闭月】（摸一张）的区别只在**张数随手牌数分叉**，故界版另有一条零步
# 流水线；标准版一动不改。
# ---------------------------------------------------------------------------


_EQUIP_SLOTS = (
    "qinggang_sword",
    "double_sword",
    "green_dragon_blade",
    "axe",
    "halberd",
    "qilin_bow",
    "ice_sword",
    "renwang_shield",
)
"""八张**牌堆里有实例**的装备牌名：起始手牌 6 张 + 摸牌阶段摸到的 2 张都能在出牌阶段用掉。"""


def _biyue_seat(hand: tuple[int, ...]) -> SeatSpec:
    return SeatSpec(hand=hand, hero="jie_diaochan", gender=Gender.FEMALE)


def _use_everything_then_end(query):
    """出牌阶段：把候选里的装备牌一张张用掉；没有装备可用就结束阶段。"""
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if isinstance(action, UseCard) and action.card.type in _EQUIP_SLOTS:
                return action
        return _find(query, lambda action: isinstance(action, EndPhase))
    if query.kind == QueryKind.SKILL_ACTIVATION:
        if query.skill_id == JIE_BIYUE:
            return _activate(query, JIE_BIYUE)
        return _find(query, lambda action: isinstance(action, Pass))
    return _decline(query)


def _play_one_turn(engine, players):
    """跑完座位 0 的一个完整回合（在下一次询问处停）——结束时闭月已经结算完。"""
    run_turns(engine, players, turns=1)


def test_jie_biyue_draws_one_when_the_hand_is_not_empty():
    """正例：结束阶段手里还有牌 ⇒ 摸 1 张（与标准版【闭月】同数）。"""
    held = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[_biyue_seat((held,)), SeatSpec()],
        deck=deck_excluding(PACK, held),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    players = [Scripted(_use_everything_then_end), PassPlayer()]

    _play_one_turn(engine, players)

    # 起始 1 张 + 摸牌阶段 2 张 = 3（体力 3，弃牌阶段不弃）+ 闭月 1 张 = 4。
    assert len(engine.state.player(0).hand) == 4, "有手牌 ⇒ 摸一张（§十九 L2215）"
    deltas = _deltas(engine, "card_move")
    assert deltas, "摸牌走正常移动入口"


def test_jie_biyue_draws_two_when_the_hand_is_empty():
    """反例（另一半）：结束阶段没有手牌 ⇒ 摸 2 张。"""
    hand = tuple(_cards(type_id)[0] for type_id in _EQUIP_SLOTS[:6])
    drawn_phase = tuple(_cards(type_id)[0] for type_id in _EQUIP_SLOTS[6:])
    finish_draw = (_cards("peach")[0], _cards("dodge")[0])
    deck = (
        *drawn_phase,
        *finish_draw,
        *deck_excluding(PACK, *hand, *drawn_phase, *finish_draw),
    )
    state = build_state(
        pack=PACK,
        seats=[_biyue_seat(hand), SeatSpec()],
        deck=deck,
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    players = [Scripted(_use_everything_then_end), PassPlayer()]

    _play_one_turn(engine, players)

    assert engine.state.player(0).hand == list(finish_draw), (
        "结束阶段没有手牌 ⇒ 摸两张（§十九 L2215；起始与摸牌阶段的 8 张装备已全部用掉）"
    )


# ---------------------------------------------------------------------------
# 界貂蝉【离间】：**不能被响应**的虚拟【决斗】——响应窗口不产生
#
# §十九 L2212–L2214：令后者视为对前者使用一张不能被响应的【决斗】。
#
# "不能被响应"落成帧标签 ``Tag.UNRESPONSIVE`` 的两个既有读取面：
# ① 结算的"生效前"见到它就不开无懈询问；② 决斗效果见到它就不再问【杀】。
# 决斗**照常结算**（受害者直接判负、受到伤害）——"不可响应"不是"抵消"。
# ---------------------------------------------------------------------------


def _lijian_state(hero_id: str):
    cost = _cards("slash")[0]
    victim_slash = _cards("slash")[1]
    user_slash = _cards("slash")[2]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=(cost,), hero=hero_id, gender=Gender.FEMALE,
                identity=Identity.LORD,
            ),
            SeatSpec(
                hand=(user_slash,), gender=Gender.MALE,
                identity=Identity.LOYALIST,
            ),
            SeatSpec(
                hand=(victim_slash,), gender=Gender.MALE,
                identity=Identity.REBEL,
            ),
            SeatSpec(gender=Gender.FEMALE, identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, cost, victim_slash, user_slash),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    return cost, victim_slash, user_slash, state


def _composite(query, skill_id: str, *, picks=(), choices=()) -> ActivateSkill:
    """预发动确认时提交的**复合动作**（不要求是候选）。"""
    return ActivateSkill(
        skill_id=skill_id,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def _lijian_driver(skill_id: str, *, pick: int, seats: tuple[int, int]):
    """``seats`` = **两次座位选择的次序**：标准版 =（使用者, 目标）；界版 =（目标, 使用者）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE and skill_id in query.preactivations:
            return _composite(
                query,
                skill_id,
                picks=(pick,),
                choices=tuple(f"seat:{seat}" for seat in seats),
            )
        return None

    return Scripted(driver)


def test_jie_lijian_selects_the_duel_target_before_the_user():
    """选择序（§十九 L2212–L2214）：第一次点**目标**（前者），第二次点**使用者**（后者）。

    标准版 §九 的牌面不指定次序（本仓既有口径 = 先使用者、后目标），故两版选择序相反是
    **牌面差异**而不是实现细节：本测试钉住步骤键序、逐前序子池与分步文案。
    """
    cost, _victim, _user, state = _lijian_state("jie_diaochan")

    steps = preactivation_for(state, 0, JIE_LIJIAN)

    assert [step.name for step in steps] == ["card", "victim", "user"], (
        "界版先选目标、再选使用者（标准版相反）"
    )
    assert {option for row in steps[1].pool for option in row.options} == {"seat:1", "seat:2"}, (
        "步 2 = 一名男性（可作【决斗】目标）"
    )
    rows = {row.when: row.options for row in steps[2].pool}
    assert rows[(str(cost), "seat:1")] == ("seat:2",), "目标选了座位 1 ⇒ 使用者只能是座位 2"
    assert rows[(str(cost), "seat:2")] == ("seat:1",)
    assert "目标" in steps[1].wording and "使用者" in steps[2].wording, (
        "分步文案随界版次序下发（界面画得出先点谁）"
    )


def test_jie_lijian_produces_no_nullify_and_no_slash_response_window():
    """正例：无懈询问**不出现**、决斗的【杀】询问**不出现**，伤害照常结算。

    两次点人按**界版次序**传入：第一次点目标（座位 2）、第二次点使用者（座位 1）。
    """
    cost, victim_slash, _user_slash, state = _lijian_state("jie_diaochan")
    engine = _run(
        state,
        [
            _lijian_driver(JIE_LIJIAN, pick=cost, seats=(2, 1)),
            Scripted(_decline),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    responses = _queries(engine, QueryKind.RESPOND.value)
    assert not [q for q in responses if q.cause is WindowCause.NULLIFY], (
        "不能被响应 ⇒ 无懈询问不出现"
    )
    assert not [q for q in responses if q.cause is WindowCause.DUEL], (
        "决斗双方都不能打出【杀】⇒ 决斗的响应询问不出现"
    )
    assert victim_slash in engine.state.player(2).hand, "受害者没有机会打出【杀】"
    assert engine.state.player(2).hp == 3, "决斗照常结算：受害者受到使用者 1 点伤害"
    assert engine.state.player(1).hp == 4, "伤害来源是使用者座位 1，不是貂蝉"
    assert cost in engine.state.discard, "成本照常弃置"


def test_jie_lijian_treats_the_first_pick_as_the_duel_target():
    """反例（对照）：把第一次点人选中座位 1 ⇒ 受伤的是**座位 1**（它不是使用者）。

    标准版同样的两次点人（1, 2）意味着"座位 1 是使用者"；界版次序反过来，故这条钉住
    "第一次点的是【决斗】目标"这一面，而不只是清单的键序。
    """
    cost, _victim, _user_slash, state = _lijian_state("jie_diaochan")
    engine = _run(
        state,
        [
            _lijian_driver(JIE_LIJIAN, pick=cost, seats=(1, 2)),
            Scripted(_decline),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    assert engine.state.player(1).hp == 3, "第一次点的是目标 ⇒ 座位 1 受到【决斗】伤害"
    assert engine.state.player(2).hp == 4, "座位 2 是使用者（伤害来源），不掉血"


def test_the_standard_lijian_does_open_both_windows():
    """反例（对照）：标准【貂蝉】的同一张剧本里，无懈窗口与【杀】响应窗口都在。

    标准版次序 =（使用者, 目标）：座位 1 使用【决斗】打向座位 2。
    """
    cost, victim_slash, _user_slash, state = _lijian_state("diaochan")

    def victim(query):
        if query.kind == QueryKind.RESPOND and query.cause is WindowCause.DUEL:
            return _first(query, PlayCard)
        return _decline(query)

    engine = _run(
        state,
        [
            _lijian_driver("hero.diaochan.lijian", pick=cost, seats=(1, 2)),
            Scripted(_decline),
            Scripted(victim),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    responses = _queries(engine, QueryKind.RESPOND.value)
    assert [q for q in responses if q.cause is WindowCause.NULLIFY], (
        "标准版的【决斗】有「生效前」的无懈窗口"
    )
    duel_asks = [q for q in responses if q.cause is WindowCause.DUEL]
    assert duel_asks and duel_asks[0].actor == 2, "由目标先响应【杀】"
    assert victim_slash in engine.state.discard, "受害者打出了那张【杀】"
    assert engine.state.player(1).hp == 3, "只出一张就放弃的一方（座位 1）受伤"


# ---------------------------------------------------------------------------
# 界甘宁【奋威】（限定技）：目标级无效
#
# §十九 L2146–L2147：当一张锦囊牌指定至少两个目标后，你可以令此牌对其中任意名
# 目标角色无效。
#
# 落点 = 本次使用帧上的"无效目标集合" + 结算 C1 的终止点：被无效的目标**照常被指定**
# （仍是目标、仍参与目标数），但轮到它自己的"使用结算开始时"就终止——不产生效果，
# 且没有"生效前"的无懈窗口。
# ---------------------------------------------------------------------------

_UNTARGETED = _cards("slash")[0]
"""未用到的牌（从牌堆里排除，保证摸牌固定）。"""

BARBARIAN = "barbarian_invasion"
PEACH_GARDEN = "peach_garden"


def _fenwei_script(*, targets=(), decline=False, log=None):
    """座位 1（界甘宁）的脚本：答【奋威】的"是否发动"、**一次**点齐 ``targets``。

    票 13 起选无效目标是一问的座位多选（``SELECT_OPTIONS``），故 ``targets`` 一次性
    提交成一个集合；``log`` 收下两类询问供断言（"是否发动" / 选无效目标的那一问）。
    """

    def driver(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == FENWEI:
            if log is not None:
                log.append(query)
            if decline:
                return _find(query, lambda action: isinstance(action, Pass))
            return _activate(query, FENWEI)
        if query.kind == QueryKind.SELECT_OPTIONS and query.skill_id.startswith("fenwei:"):
            if log is not None:
                log.append(query)
            choices = tuple(
                f"seat:{seat}" for seat in targets if f"seat:{seat}" in query.options
            )
            if choices:
                return SelectOptions(choices=choices, answers_seq=query.seq)
            return _find(query, lambda action: isinstance(action, Pass))
        return _decline(query)

    return Scripted(driver)


def _trick_user_script(card_id: int, card_type: str):
    """座位 0 的脚本：出牌阶段使用 `card_id`（第一张该牌名），其余最小应答。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.type == card_type
                and action.card.card_id == card_id,
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return _decline(query)

    return Scripted(driver)


def test_fenwei_invalidates_only_the_chosen_subset():
    """正例：只令座位 2 对这张【南蛮入侵】无效 ⇒ 座位 2 掉血被跳过，其余照常结算。"""
    barbarian = _cards(BARBARIAN)[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,), identity=Identity.LORD),
            SeatSpec(hero="jie_ganning", identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, barbarian),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    engine = _run(
        state,
        [
            _trick_user_script(barbarian, BARBARIAN),
            _fenwei_script(targets=(2,)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        lambda e: e.state.player(1).hp < 4 and e.state.player(3).hp < 4,
    )

    assert engine.state.player(2).hp == 4, "被【奋威】无效的目标不产生效果（没掉血）"
    assert engine.state.player(1).hp == 3, "未选中的目标照常结算（没有【杀】⇒ 掉 1 点）"
    assert engine.state.player(3).hp == 3, "其余未选中的目标同样照常结算"


def test_fenwei_declining_lets_every_target_take_damage():
    """反例：答"不发动"⇒ 两个目标都照常结算（都掉血）。"""
    barbarian = _cards(BARBARIAN)[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,), identity=Identity.LORD),
            SeatSpec(hero="jie_ganning", identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, barbarian),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    engine = _run(
        state,
        [
            _trick_user_script(barbarian, BARBARIAN),
            _fenwei_script(decline=True),
            Scripted(_decline),
            Scripted(_decline),
        ],
        lambda e: e.state.player(1).hp < 4 and e.state.player(2).hp < 4,
    )

    assert (engine.state.player(1).hp, engine.state.player(2).hp) == (3, 3)
    assert limited_skill_used(engine.state, 1, FENWEI) is False, "不发动不消耗限定技"


def test_fenwei_may_invalidate_the_user_when_the_card_targets_him():
    """裁定①：【桃园结义】含全部角色 ⇒ 目标集合含界甘宁本人，可以把自己选为无效目标。"""
    garden = _cards(PEACH_GARDEN)[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hp_max=4, hand=(garden,), identity=Identity.LORD),
            SeatSpec(
                hero="jie_ganning", hp=2, hp_max=4, identity=Identity.LOYALIST
            ),
            SeatSpec(hp=2, hp_max=4, identity=Identity.REBEL),
            SeatSpec(hp=2, hp_max=4, identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, garden),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    log: list = []
    engine = _run(
        state,
        [
            _trick_user_script(garden, PEACH_GARDEN),
            _fenwei_script(targets=(1,), log=log),
            Scripted(_decline),
            Scripted(_decline),
        ],
        lambda e: e.state.player(0).hp == 3 and e.state.player(3).hp == 3,
    )

    option_queries = [
        query
        for query in log
        if query.kind == QueryKind.SELECT_OPTIONS.value
        and query.skill_id.startswith("fenwei:")
    ]
    assert option_queries, "选无效目标的询问确实发生了"
    assert "seat:1" in option_queries[0].options, (
        "界甘宁本人是这张【桃园结义】的目标之一 ⇒ 也在可选集合里"
    )
    assert engine.state.player(1).hp == 2, "把自己选为无效目标 ⇒ 不回复"
    assert engine.state.player(0).hp == 3, "未选中的目标（含锦囊的使用者）照常回复 1 点"
    assert engine.state.player(3).hp == 3


def test_fenwei_needs_a_trick_with_at_least_two_targets():
    """反例（触发条件）：只指定一个目标的锦囊不触发（连"是否发动"都不问）。"""
    dismantlement = _cards("dismantlement")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(dismantlement,), identity=Identity.LORD),
            SeatSpec(hero="jie_ganning", identity=Identity.LOYALIST),
            SeatSpec(hand=(_UNTARGETED,), identity=Identity.REBEL),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, dismantlement, _UNTARGETED),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    log: list = []
    engine = _run(
        state,
        [
            _trick_user_script(dismantlement, "dismantlement"),
            _fenwei_script(log=log),
            Scripted(_decline),
            Scripted(_decline),
        ],
        lambda e: e.state.player(1).hp == 4 and e.state.player(0).hp == 4,
    )

    assert not [q for q in log if q.skill_id == FENWEI], "不足两个目标 ⇒ 不问【奋威】"


def test_fenwei_is_a_limited_skill_that_fires_once():
    """限定技：第一次多目标锦囊问一次；第二次**不再问**（整局一次）。"""
    first = _cards(BARBARIAN)[0]
    second = _cards(BARBARIAN)[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(first, second), identity=Identity.LORD),
            SeatSpec(hero="jie_ganning", hp=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, first, second),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    log: list = []

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.type == BARBARIAN,
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return _decline(query)

    # 第一张【南蛮】：界甘宁把自己选为无效目标（不掉血）；第二张：照常掉 1 点。
    # 跑完座位 0 的整个回合即可（两张都用掉后不再有【奋威】询问）。
    engine = Engine.from_state(state, seed=7)
    run_turns(
        engine,
        [
            Scripted(user),
            _fenwei_script(targets=(1,), log=log),
            Scripted(_decline),
            Scripted(_decline),
        ],
        turns=1,
    )

    assert len([q for q in log if q.skill_id == FENWEI]) == 1, (
        "限定技整局一次：第二次多目标锦囊不再发「是否发动」"
    )
    assert limited_skill_used(engine.state, 1, FENWEI) is True
    assert engine.state.player(1).hp == 3, (
        "第一次【南蛮】对界甘宁无效（没掉血），第二次照常结算（掉 1 点）"
    )
