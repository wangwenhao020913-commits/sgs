"""**预转化**（need-card-supply 票 04）：【蛊惑】的逐张手牌供给。

用户 2026-11-20 Q1（逐字，`spec.md` §6）：

    蛊惑不是暗信息特例，而是特殊的转化牌：……他可以把任何一张手牌，转化为其他一张手牌，但是
    在通过'质疑校验'之前，这是一种'预转化'；我们完全可以把'预转化'能 supply 的产物也纳入
    考量，例如被记录方块的锋·关羽使用杀指定为目标时，于吉蛊惑就只能用'方块牌'进行'预转化'，
    其它牌不能进行转化。当计算 supply 时，也要考虑'定乱'不能被当做杀使用/打出、被关羽义绝后
    无法使用/打出手牌等其它限制情况。

判据（票面 §4 的 1–6 + §2b-3 / §2b-4）：

1. **逐张报供给**（§4-1 / S12）：每张手牌一条供给、由后端按这一问的 ``Demand`` 现算
   （用户 Q4）——票 05 起不再有逐行渲染与载荷键，供给模型本身照旧；
2. **匹配器不另写一套**（§4-2）：同一张来源牌，``kind="transformed"`` 与
   ``kind="preconversion"`` 对同一 ``Demand`` 给出**逐字相同**的判定（§16.3：转化牌继承
   来源牌的花色 / 点数 / 颜色）；
3. **供给 none ⇒ 仍不进面**（§4-3）：一张满足需求的牌都没有 ⇒ 整档不进候选；
4. **上游禁令计入**（§4-4 / S11）：【定乱】的牌不能当杀族产物的来源；被【义绝】封住手牌的
   于吉整档供给 ``none``；
5. **信息卫生**（§4-5 / S13）：**已随展示面一并删除**（票 05，spec §0.1b）——私密的两个载荷
   键不存在了，本文件只留"这一档进不进面"的行为断言；
6. **一个判据**（§2b-3）：逐张供给与"这一档进不进面"读同一个
   :func:`~sgs.effects.supplies_of_action`，匹配器与闸门给出同一个答案（S16 的展示不变量
   已按 spec §0.1b 作废）。

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
    QueryKind,
    RespondContext,
    RespondMode,
    WindowCause,
)

from support import (  # noqa: E402
    SeatSpec,
    build_state,
    deck_excluding,
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


def _providers(actions) -> list[str]:
    return [action.skill_id for action in actions if isinstance(action, ActivateSkill)]


def _face(state, kind: QueryKind, payload) -> tuple:
    return legal_actions_for(state, HOLDER, kind, answers_seq=0, payload=payload)


# ---------------------------------------------------------------------------
# 1. 供给模型的形状（§4-1 / S12；展示行已按票 05 删除）
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


def test_a_matching_source_card_lets_the_preconversion_tier_into_the_face():
    """§4-1 的行为半：手里有一张花色匹配的来源牌 ⇒【蛊惑】这一档**进面**。

    展示行已按用户 2026-11-21 的裁定删除（need-card-supply 票 05，spec §0.1b）：原来"只有 ♦
    那行高亮 / 换需求高亮换行"的逐行断言随之作废。留下的是供给模型的结论落到面上的那一半——
    票 02 之前这一档被一刀切禁掉，这条断言就是"多禁被放开"。
    """
    state = _state((DIAMOND_SLASH, CLUB_SLASH, HEART_PEACH))

    assert GUOHUO in _providers(_face(state, QueryKind.DODGE, _dodge_payload("diamond"))), (
        "有一张 ♦ 的牌 ⇒ 这一档进面（票 02 之前它被一刀切禁掉）"
    )
    assert GUOHUO in _providers(_face(state, QueryKind.DODGE, _dodge_payload("heart"))), (
        "换一份需求（限 ♥）⇒ ♥ 那张来源牌同样让这一档进面（不是静态表）"
    )


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
    ① 面里没有【蛊惑】；② 供给池里仍在、只是逐张都不过匹配器（展示行已按票 05 删除，
    灰行那一半作废）。
    """
    state = _state((CLUB_SLASH, HEART_PEACH))
    payload = _dodge_payload("diamond")

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
    """§4-4 后半：被【义绝】封住手牌的于吉 ⇒ **整档供给 none**，不进面，但这一组仍在 + 理由。

    判据复用座位禁令（``Player.hand_card_prohibited``，唯一存储是
    ``hand_card_prohibition_sources``）；理由是**提供者自己的声明**
    （``SkillDef.supply_none_reason``）。展示行已按票 05 删除（spec §0.1b），本断言只到
    供给模型为止。
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
    assert GUOHUO not in _providers(_face(state, QueryKind.DODGE, payload))


# ---------------------------------------------------------------------------
# 5. 一个判据（§2b-3）：匹配器与闸门对逐张供给给出同一个答案
# ---------------------------------------------------------------------------


def test_the_matcher_and_the_gate_agree_on_every_source_card():
    """票面 §2b-3 / §2b-4 的**判据**半（S16 的展示不变量已按 spec §0.1b 作废）。

    四个局面各一条（有 ♦ 的牌 / 一张都没有 / 【定乱】挡掉一张 / 被【义绝】封手牌）：

    - 同一张来源牌上，``supply_satisfies``（行表曾读的匹配器）与 ``supply_is_legal``
      （闸门读的判定）必须给出同一个答案——展示行删了，这条"一个判据"仍要成立；
    - "这一档进不进面" ⇔ "至少有一张来源牌过闸门"——逐张供给与面读同一个
      :func:`~sgs.effects.supplies_of_action`。
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
        supplies = _preconversion(_tiers(state, kind, payload))
        face = _face(state, kind, payload)
        for supply in supplies:
            assert supply_satisfies(demand, supply) == supply_is_legal(demand, supply), (
                f"{label}：同一张来源牌在匹配器与闸门里的判定必须相同：{supply}"
            )
        assert any(supply_is_legal(demand, supply) for supply in supplies) == (
            GUOHUO in _providers(face)
        ), f"{label}：这一档进不进面要与'至少一张来源牌过闸门'同进同退"
