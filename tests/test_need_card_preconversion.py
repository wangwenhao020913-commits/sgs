"""**预转化**（need-card-supply 票 04）：【蛊惑】的逐张手牌供给。

用户 2026-11-20 Q1（逐字，`spec.md` §6）：

    蛊惑不是暗信息特例，而是特殊的转化牌：……他可以把任何一张手牌，转化为其他一张手牌，但是
    在通过'质疑校验'之前，这是一种'预转化'；我们完全可以把'预转化'能 supply 的产物也纳入
    考量，例如被记录方块的锋·关羽使用杀指定为目标时，于吉蛊惑就只能用'方块牌'进行'预转化'，
    其它牌不能进行转化。当计算 supply 时，也要考虑'定乱'不能被当做杀使用/打出、被关羽义绝后
    无法使用/打出手牌等其它限制情况。

判据（票面 §4 的 1–6 + §2b-3 / §2b-4）：

1. **逐张报供给并置灰**（§4-1 / S12 / S14）：每张手牌一行，``ok`` / ``reason`` 由后端按
   这一问的 ``Demand`` 现算（用户 Q4）——换一份需求，高亮的**那一张**就跟着换；
2. **匹配器不另写一套**（§4-2）：同一张来源牌，``kind="transformed"`` 与
   ``kind="preconversion"`` 对同一 ``Demand`` 给出**逐字相同**的判定（§16.3：转化牌继承
   来源牌的花色 / 点数 / 颜色）；
3. **供给 none ⇒ 仍不进面**（§4-3）：一张满足需求的牌都没有 ⇒ 整档不进候选；
4. **上游禁令计入**（§4-4 / S11）：【定乱】的牌不能当杀族产物的来源；被【义绝】封住手牌的
   于吉整档供给 ``none``；
5. **信息卫生**（§4-5 / S13）：本人视图有逐张行、同座他人视图没有、409 裸 ``Query`` 没有；
6. **高亮 / 可点一致**（§2b-4 / S16）+ **一个判据**（§2b-3）：行 ``ok=True`` ⇒ 那一档有合法
   候选；逐张行与"这一档进不进面"读同一个 :func:`~sgs.effects.supplies_of_action`。

**本票不做的**（票面 §2b-5 点名）：没有声明的提供者（【武翊】/ 界【渐营】②）的供给、求助
子窗口的受理面（``_run_lord_request`` 仍只收 ``PlayCard``）、重录任何金录像。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:  # 与 ``test_web.py`` 同一条引导（``sgs`` + ``sgs_web``）
        sys.path.insert(0, str(_entry))

from sgs.data import CardTagType, RuleConfig, load_content_pack  # noqa: E402
from sgs.effects import (  # noqa: E402
    Demand,
    Supply,
    all_actions_for,
    demand_for,
    forbidden_as_slash,
    legal_actions_for,
    need_card_tier,
    supplies_for,
    supplies_of_action,
    supply_is_legal,
    supply_mismatch,
    supply_satisfies,
)
from sgs.engine import Engine  # noqa: E402
from sgs.skills import CATALOG  # noqa: E402
from sgs.state import (  # noqa: E402
    ActivateSkill,
    CardTag,
    DodgeContext,
    Pass,
    QueryKind,
    RespondContext,
    RespondMode,
    WindowCause,
)
from sgs_web import projection as _projection  # noqa: E402
from sgs_web.session import Session  # noqa: E402

from support import (  # noqa: E402
    PassPlayer,
    SeatSpec,
    build_state,
    deck_excluding,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 张档（模块级加载必须写死档位，票 32）。"""

GUOHUO = "hero.yuji_mobile.guohuo"
YIJUE = "hero.jie_guanyu.yijue"
"""界关羽【义绝】的技能 id：写进 ``hand_card_prohibition_sources`` 的那个"施加者"。"""

DINGLUAN = "hero.feng_yujin.dingluan"
"""锋·于禁【定乱】的技能 id（标签来源；读点 = ``effects.actions.forbidden_as_slash``）。"""

HOLDER = 0
ATTACKER = 1


# ---------------------------------------------------------------------------
# 局面构造
# ---------------------------------------------------------------------------


def _card(type_id: str, suit: str) -> int:
    """标准档里某牌名某花色的第一张 ``card_id``（牌面事实就取这一张）。"""
    return next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == type_id and card.suit.value == suit
    )


DIAMOND_SLASH = _card("slash", "diamond")
CLUB_SLASH = _card("slash", "club")
HEART_PEACH = _card("peach", "heart")
"""三张**不是【闪】**的手牌：故【闪】窗口里没有真牌候选，逐张行只能是预转化。"""

CLUB_NULLIFY = _card("nullify", "club")
"""杀族窗口里**不是**候选的牌（打出【杀】时它不能响应）——【定乱】那张用它来挂标签。"""


def _state(hand: tuple[int, ...], *, tag: int | None = None):
    """座位 0 = 于吉移动版（给定手牌），座位 1 = 空手牌（窗口由调用方给载荷，不开真询问）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="yuji_mobile", hand=hand, hp=3, hp_max=3), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=HOLDER,
        rule_config=RuleConfig(player_count=2, first_seat=HOLDER),
    )
    if tag is not None:
        state.card_tags[tag] = (
            CardTag(tag_type=CardTagType.DINGLUAN, owner=ATTACKER, skill_id=DINGLUAN),
        )
    return state


def _prohibit(state) -> None:
    """给座位 0 加一条"本回合不能使用或打出手牌"（唯一写入口；施加者 = 【义绝】）。"""
    assert Engine.from_state(state, seed=3).add_hand_card_prohibition(HOLDER, YIJUE) is True


def _dodge_payload(*suits: str) -> DodgeContext:
    return DodgeContext(response_suits=suits or None)


def _slash_payload(*suits: str) -> RespondContext:
    return RespondContext(
        cause=WindowCause.BARBARIAN_INVASION,
        required="slash",
        mode=RespondMode.PLAY,
        response_suits=suits or None,
    )


def _tiers(state, kind: QueryKind, payload):
    return supplies_for(kind, state=state, seat=HOLDER, payload=payload, answers_seq=0)


def _preconversion(tiers) -> list[Supply]:
    """【蛊惑】这一档的逐张供给（只取 ``kind="preconversion"``，按手牌序）。"""
    return [
        supply
        for group in tiers
        if group.provider == GUOHUO
        for supply in group.supplies
        if supply.kind == "preconversion"
    ]


def _rows(state, kind: QueryKind, payload) -> list[dict]:
    demand = demand_for(kind, payload)
    assert demand is not None
    return _projection.need_card_supply_rows(state, demand, _tiers(state, kind, payload))


def _providers(actions) -> list[str]:
    return [action.skill_id for action in actions if isinstance(action, ActivateSkill)]


def _face(state, kind: QueryKind, payload) -> tuple:
    return legal_actions_for(state, HOLDER, kind, answers_seq=0, payload=payload)


def _face_tiers(state, kind: QueryKind, payload, tier: int) -> set[int]:
    """**这一档自己的那张面**里出现过的档（``floor = tier`` ⇒ 引擎在这一档问时看到的面）。"""
    if isinstance(payload, DodgeContext):
        at_tier = DodgeContext(floor=tier, response_suits=payload.response_suits)
    else:
        at_tier = RespondContext(
            cause=payload.cause,
            required=payload.required,
            mode=payload.mode,
            use_targets=payload.use_targets,
            response_suits=payload.response_suits,
            floor=tier,
        )
    face = legal_actions_for(state, HOLDER, kind, answers_seq=0, payload=at_tier)
    return {int(need_card_tier(action)) for action in face if not isinstance(action, Pass)}


# ---------------------------------------------------------------------------
# 1. 逐张报供给并置灰（§4-1 / S12 / S14）
# ---------------------------------------------------------------------------


def test_the_preconversion_declaration_is_judgeable_and_expands_per_hand_card():
    """§1 / §2b-1：声明是**可判**的（``unknown=False``），逐张供给由同一个生成口展开。

    ``SkillDef.supply`` 只说"我是预转化"（``product=None``：产物牌名由牌堆 + 窗口现算）；
    展开点是 :func:`~sgs.effects.supplies_of_action`——**闸门与行表读同一个函数**（§2b-3），
    故这里直接断"``supplies_for`` 的那几条 == ``supplies_of_action`` 的返回值"。
    """
    declared = CATALOG[GUOHUO].supply
    assert declared is not None
    assert (declared.kind, declared.unknown, declared.product) == (
        "preconversion",
        False,
        None,
    )

    hand = (DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH)
    state = _state(hand)
    payload = _dodge_payload("diamond")
    expanded = supplies_of_action(
        ActivateSkill(skill_id=GUOHUO, answers_seq=0),
        state=state,
        seat=HOLDER,
        kind=QueryKind.DODGE,
        payload=payload,
    )
    assert expanded is not None and len(expanded) == len(hand), (
        f"每一张手牌各一条供给：{expanded}"
    )
    assert expanded == tuple(_preconversion(_tiers(state, QueryKind.DODGE, payload)))

    faces = [
        (supply.product, supply.suit, supply.rank, supply.color, supply.kind, supply.source_count)
        for supply in expanded
    ]
    assert faces == [
        (
            "dodge",
            state.card(card_id).suit,
            state.card(card_id).rank,
            state.card(card_id).color,
            "preconversion",
            1,
        )
        for card_id in hand
    ], "牌面事实 = **来源牌**的（§16.3：预转化牌继承来源牌的花色 / 点数 / 颜色）"


def test_only_the_matching_source_card_row_is_highlighted():
    """§4-1：于吉手里 ♦ / ♣ / ♥ 三张牌，需求限 ♦ ⇒ **只有 ♦ 那行** ``ok=True``，其余两行灰。

    后半条钉的是用户 Q4（**后端实时算**）与 S14：同一局面换一份需求（限 ♥），高亮的那一张
    就换成 ♥——不是静态表、也不在前端猜。
    """
    state = _state((DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH))

    diamond_rows = _rows(state, QueryKind.DODGE, _dodge_payload("diamond"))
    assert len(diamond_rows) == 3, f"三张来源牌各一行：{diamond_rows}"
    assert {row["label"] for row in diamond_rows} == {"使用牌档·蛊惑"}
    assert [row["ok"] for row in diamond_rows] == [True, False, False]
    rank = PACK.deck.cards[DIAMOND_SLASH].rank
    assert diamond_rows[0]["supply_text"] == f"♦{rank}预转化【闪】"
    assert "预转化" in diamond_rows[0]["supply_text"]
    assert all(row["reason"] for row in diamond_rows[1:]), (
        f"灰行要给出理由（后端算）：{diamond_rows[1:]}"
    )
    assert all(
        "不满足花色限制" in str(row["reason"]) for row in diamond_rows[1:]
    ), "理由来自匹配器的那一句（不在这里另拼一套判据）"
    assert GUOHUO in _providers(_face(state, QueryKind.DODGE, _dodge_payload("diamond"))), (
        "有一张 ♦ 的牌 ⇒ 这一档进面（票 02 之前它被一刀切禁掉）"
    )

    # 换一份需求 ⇒ 高亮的那一张跟着换（后端实时算；前端只读 ok）。
    heart_rows = _rows(state, QueryKind.DODGE, _dodge_payload("heart"))
    assert [row["ok"] for row in heart_rows] == [False, False, True]
    assert heart_rows[2]["supply_text"].startswith("♥")


# ---------------------------------------------------------------------------
# 2. 匹配器不另写一套（§4-2）
# ---------------------------------------------------------------------------


def test_the_matcher_judges_preconversion_exactly_like_a_single_source_conversion():
    """§4-2：同一张来源牌，``transformed`` 与 ``preconversion`` 对同一 ``Demand`` 判定相同。

    判据来源 = §16.3（转化牌的花色 / 点数 / 颜色**继承来源牌**）：预转化与单源转化在牌面
    维度上是**同一件事**，故匹配器不给它第二套比对。六个维度逐个比；``kinds`` 那一维是
    **例外并且必须例外**——它判的就是 kind 本身（两种供给的种类天然不同）。
    """
    preconversion = Supply(
        product="dodge", suit="diamond", rank=7, color="red", kind="preconversion"
    )
    transformed = replace(preconversion, kind="transformed")

    demands = (
        Demand(),
        Demand(names=("dodge",)),
        Demand(names=("slash",)),
        Demand(suits=("diamond",)),
        Demand(suits=("heart",)),
        Demand(colors=("red",)),
        Demand(colors=("black",)),
        Demand(ranks=(7,)),
        Demand(ranks=(8,)),
        Demand(source_counts=(1,)),
        Demand(source_counts=(2,)),
        Demand(names=("dodge",), suits=("diamond",), colors=("red",), ranks=(7,)),
    )
    for demand in demands:
        assert supply_satisfies(demand, preconversion) == supply_satisfies(
            demand, transformed
        ), demand
        assert supply_mismatch(demand, preconversion) == supply_mismatch(
            demand, transformed
        ), demand

    # ``kinds`` 是**种类判据本身**：预转化不是转化（这一维上它们必须不同）。
    assert supply_satisfies(Demand(kinds=("transformed",)), transformed) is True
    assert supply_satisfies(Demand(kinds=("transformed",)), preconversion) is False
    assert supply_satisfies(Demand(kinds=("preconversion",)), preconversion) is True


# ---------------------------------------------------------------------------
# 3. 供给 none ⇒ 仍不进面（§4-3 / §2b-4）
# ---------------------------------------------------------------------------


def test_the_tier_stays_out_of_the_face_when_no_hand_card_matches():
    """§4-3：手里没有一张能预转化出满足需求的牌 ⇒ **整档不进面**（失败关闭不变）。

    这是"判据从'有需求就全禁'变成'算出来一张都不行'"的两半（票面 §2）：
    ① 面里没有【蛊惑】；② 逐张行仍然画得出来（灰行的来源 = 供给池），每行 ``ok=False``；
    ③ **不存在** ``ok=True`` 的蛊惑行（§2b-4 点名的那条断言）。
    """
    state = _state((CLUB_SLASH, HEART_PEACH))
    payload = _dodge_payload("diamond")

    rows = _rows(state, QueryKind.DODGE, payload)
    assert rows, "灰行要照报（不是凭空消失）"
    assert not any(row["ok"] for row in rows), f"一张都不满足 ⇒ 不许有 ok=True 的行：{rows}"
    assert all(row["reason"] for row in rows)
    assert GUOHUO not in _providers(_face(state, QueryKind.DODGE, payload))

    # 供给池里仍在（票 02 §1b：灰行的唯一来源），只是不进面。
    pool = all_actions_for(
        state, HOLDER, QueryKind.DODGE, answers_seq=0, payload=payload
    )
    assert GUOHUO in _providers(pool), "供给池里应当保留"
    for supply in _preconversion(_tiers(state, QueryKind.DODGE, payload)):
        assert supply_satisfies(demand_for(QueryKind.DODGE, payload), supply) is False


# ---------------------------------------------------------------------------
# 4. 上游禁令计入（§4-4 / S11）
# ---------------------------------------------------------------------------


def test_a_dingluan_tagged_source_card_is_not_a_preconversion_source_for_slash():
    """§4-4 前半：带【定乱】标记的牌不能作为【杀】响应时的**预转化源**。

    判据**复用** :func:`~sgs.effects.forbidden_as_slash`（标签读点只有一处），不在这里另写
    白名单；对照 = 同一个局面**不挂标签** ⇒ 两张来源牌都在。不满足上游禁令的那张牌**不出现
    在供给行里**（票面 §2 允许的两种读法之一：与"产物牌名不匹配"的理由分开，不混成一条）。
    """
    hand = (CLUB_NULLIFY, HEART_PEACH)
    payload = _slash_payload()
    plain = _state(hand)
    tagged = _state(hand, tag=CLUB_NULLIFY)

    plain_supplies = _preconversion(_tiers(plain, QueryKind.RESPOND, payload))
    assert [supply.suit for supply in plain_supplies] == ["club", "heart"], (
        "对照：不挂【定乱】时两张来源牌都报"
    )

    assert forbidden_as_slash(tagged, tagged.card(CLUB_NULLIFY)) is True
    supplies = _preconversion(_tiers(tagged, QueryKind.RESPOND, payload))
    assert [supply.suit for supply in supplies] == ["heart"], (
        f"【定乱】那张不出现在供给行里：{supplies}"
    )
    # 面里只剩【蛊惑】（另一张牌仍能预转化）——不是"整档消失"。
    assert GUOHUO in _providers(_face(tagged, QueryKind.RESPOND, payload))


def test_a_blocked_holder_reports_the_whole_tier_as_none():
    """§4-4 后半：被【义绝】封住手牌的于吉 ⇒ **整档供给 none**，不进面，但行照画 + 理由。

    判据复用座位禁令（``Player.hand_card_prohibited``，唯一存储是
    ``hand_card_prohibition_sources``）；理由是**提供者自己的声明**
    （``SkillDef.supply_none_reason``），投影只拼句子。
    """
    state = _state((DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH))
    _prohibit(state)
    assert state.player(HOLDER).hand_card_prohibited is True
    payload = _dodge_payload("diamond")

    tiers = _tiers(state, QueryKind.DODGE, payload)
    guohuo = [group for group in tiers if group.provider == GUOHUO]
    assert len(guohuo) == 1, f"这一档只该有一组：{tiers}"
    assert guohuo[0].supplies == (), "一张牌也扣置不出去 ⇒ 整档无供给"
    assert guohuo[0].reason, "整档给不出来要有理由（票 03 建立的口子）"

    rows = _rows(state, QueryKind.DODGE, payload)
    assert len(rows) == 1 and rows[0]["ok"] is False
    assert rows[0]["supply_text"] == "供给为 none"
    assert "不能使用或打出手牌" in str(rows[0]["reason"]), rows[0]["reason"]
    assert GUOHUO not in _providers(_face(state, QueryKind.DODGE, payload))


# ---------------------------------------------------------------------------
# 5. 信息卫生（§4-5 / S13）
# ---------------------------------------------------------------------------


def _restricted_dodge_session():
    """一条**真**窗口：【杀】指定了座位 0（于吉），且只许 ♦ 的牌响应。

    ``response_suits`` 是【单骑】第三段那个事实（``Engine.start_use(..., response_suits=...)``
    写进 USE 帧、随结算帧继承到本问）；这里直接按同一入口摆出来。
    """
    hand = (DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH)
    attack = _card("slash", "spade")
    used = (*hand, attack)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=hand, hp=3, hp_max=3),
            SeatSpec(hand=(attack,)),
        ],
        deck=deck_excluding(PACK, *used),
        turn_seat=ATTACKER,
        rule_config=RuleConfig(player_count=2, first_seat=ATTACKER),
    )
    engine = Engine.from_state(state, seed=11)
    engine.start_use(
        state.card(attack),
        source=ATTACKER,
        targets=(HOLDER,),
        response_suits=("diamond",),
    )
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: e.pending is not None)
    assert engine.pending is not None and engine.pending.kind == QueryKind.DODGE, (
        "构造局面没到带花色需求的【闪】窗口"
    )
    session = Session(
        seed=11, ai="heuristic", timeout_seconds=None, player_count=2, human_seat=HOLDER
    )
    session.engine = engine
    return session


def test_the_preconversion_rows_are_only_handed_to_the_holder():
    """§4-5 / S13：逐张手牌的花色 / 点数是**私密事实**——三条路只有本人视图拿得到。

    - 本人视图（查看者 == 询问者）：一句需求 + 逐张行（♦ 高亮、其余灰）；
    - 同座他人视图（``human_seat`` 换成别人）：两个键都是 ``None``；
    - 409 里附带的裸 ``Query``（拿不到引擎）：两个键都是 ``None``（含声明了 ``human_seat``
      却没引擎的那一档——fail-closed）。
    """
    session = _restricted_dodge_session()

    own = _projection.pending_payload(session)
    assert own["need_card_demand"] == "本次要求：【闪】，且花色必须是 ♦"
    rows = own["need_card_supplies"]
    assert len(rows) == 3, f"本人视图要有逐张行：{rows}"
    assert any(row["ok"] for row in rows)
    assert "♦" in "".join(row["supply_text"] for row in rows)
    assert [action["kind"] for action in own["legal_actions"]] == [
        "ActivateSkill",
        "Pass",
    ], "有 ♦ 的牌 ⇒ 【蛊惑】进面（同一份供给给出的答案）"

    # 同座他人视图 ⇒ 看不到（供给里带着手牌档的牌面，私密事实）。
    other_viewer = _projection.pending_payload(session.pending, human_seat=ATTACKER)
    assert other_viewer["need_card_demand"] is None
    assert other_viewer["need_card_supplies"] is None

    # 409 裸 Query（拿不到引擎）⇒ 两个键都是 None（fail-closed）。
    bare = _projection.pending_payload(session.pending)
    assert bare["need_card_demand"] is None and bare["need_card_supplies"] is None
    bare_with_viewer = _projection.pending_payload(session.pending, human_seat=HOLDER)
    assert bare_with_viewer["need_card_supplies"] is None


# ---------------------------------------------------------------------------
# 6. 高亮 / 可点一致（§2b-4 / §2b-5；spec §7 S16）
# ---------------------------------------------------------------------------


def test_a_preconversion_row_is_highlighted_exactly_when_that_card_is_a_legal_candidate():
    """票面 §2b-4（spec §7 **S16**）：行 ``ok=True`` ⇒ 那一档有合法候选。

    **正反两个方向**都断，四个局面各一条（有 ♦ 的牌 / 一张都没有 / 【定乱】挡掉一张 /
    被【义绝】封手牌）：

    - 有 ``ok=True`` 的蛊惑行 ⇔ 【蛊惑】那条 ``ActivateSkill`` 在面里（同一个
      :func:`~sgs.effects.supplies_of_action`、同一个匹配器）；
    - 每一行（不论档）``ok=True`` ⇒ 它那一档在自己的面里至少有一条合法候选。票 01 的行表在
      ``unknown`` 档上曾违反它、票 03 收口 relay、本票收口【蛊惑】——这是最后一道回归网。
    """
    dodge_cases = (
        ("♦ 在手", _state((DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH))),
        ("一张都不匹配", _state((CLUB_SLASH, HEART_PEACH))),
    )
    cases = [
        (label, state, QueryKind.DODGE, _dodge_payload("diamond"))
        for label, state in dodge_cases
    ]
    cases.append(
        (
            "【定乱】挡掉一张",
            _state((CLUB_NULLIFY, HEART_PEACH), tag=CLUB_NULLIFY),
            QueryKind.RESPOND,
            _slash_payload(),
        )
    )
    blocked = _state((DIAMOND_SLASH,))
    _prohibit(blocked)
    cases.append(("义绝封手牌", blocked, QueryKind.DODGE, _dodge_payload("diamond")))

    for label, state, kind, payload in cases:
        demand = demand_for(kind, payload)
        assert demand is not None
        rows = _rows(state, kind, payload)
        face = _face(state, kind, payload)
        provider_in_face = GUOHUO in _providers(face)
        # **一个判据**（§2b-3）的谓词层：两条读取路径（行 `ok` = supply_satisfies、
        # 闸门 = supply_is_legal）对**逐张供给**给出同一个答案。
        for supply in _preconversion(_tiers(state, kind, payload)):
            assert supply_satisfies(demand, supply) == supply_is_legal(demand, supply), (
                f"{label}：同一张来源牌在行表与闸门里的判定必须相同：{supply}"
            )
        assert any(row["ok"] for row in rows) == provider_in_face, (
            f"{label}：行的高亮要与'【蛊惑】这一档在不在面里'同进同退：{rows}"
        )
        for row in rows:
            if not row["ok"]:
                continue
            present = _face_tiers(state, kind, payload, int(row["tier"]))
            assert int(row["tier"]) in present, (
                f"{label}：行 ok=True 但 {row['tier']} 档没有任何合法候选"
                f"（S16 的那道缝）：{row}"
            )
