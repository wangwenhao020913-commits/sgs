"""M6 票 09：**牌标签骨架** + 标签类型登记 + 界甄姬【洛神】（spec D12 / §标签）。

规则原文：`三国杀详细规则.txt` §十九 L2061–L2065（界甄姬：

    洛神：准备阶段，你可以进行一次判定，若结果为黑色，你获得此牌并可以重复此流程
         （以此法获得的牌不计入本回合的手牌上限）。
    倾国：你可以将一张黑色手牌当【闪】使用或打出。

**标签 = 挂在*一张牌*上的持久事实**（spec D12）：与牌所在的区域无关——牌在牌堆顶 /
手牌 / 弃牌堆都能带。每个标签记**（类型、来源实例）**；每个类型在登记表里声明四项：

1. **读取方**（``reader``）：哪个查询会读它（M6 只有手牌计数）；
2. **生存期 / 清除触发**（``clear_trigger``）：回合末 / 离开区域 / 被使用或打出；
3. **可见性**（``visibility``）：公开 / 隐藏 / **随牌面可见**（自己看得见牌面才带，M6 三类）；
4. **是否随来源实例消失**（``disappears_with_source``）：失去 / 失效来源技能时标签是否一并清除。

未登记的标签类型**判红**（与 ``tests/test_card_family_sites.py`` 同规格）：登记表与
:class:`~sgs.data.CardTagType` 双向对齐；合成反例证明检查会咬。

M6 登记三类**形状**，只有第一类有载体：

- **本回合不计入**（洛神式，M6 唯一活载体）：界甄姬【洛神】以此法获得的黑牌带它，
  弃牌阶段不计入手牌数；回合末清除；失去 / 失效【洛神】时一并清除；
- **在某区域期间不计入**（黠慧式，M6 无载体）：手牌里带它 ⇒ 不计入；离开手牌即清
  （``LEAVE_ZONE``）；
- **到被使用 / 打出为止**（遗珠式，M6 无载体）：带它 ⇒ 不计入；被使用 / 打出即清
  （``USED_OR_PLAYED``）——它的清除触发**不是**回合末，故回合末清理不碰它。

**公开的"手牌数"不动**：投影里的 ``hand_count`` 恒 = ``len(hand)``；标签只改
"手牌计数"这个**弃牌阶段查询**（``effects.hand_count``），两个查询互不替换（spec D11）。
M6 三类标签声明为 ``FACE_VISIBLE``：网页载荷只在**看得到牌面**的牌上带标签，看不到的人
连标签都收不到；手牌计数/记录流不受影响。
"""

from __future__ import annotations

import json
from enum import Enum

import pytest

from sgs.data import (
    CARD_TAG_TYPES,
    CardTagClear,
    CardTagReader,
    CardTagType,
    CardTagVisibility,
    Color,
    RuleConfig,
    card_tag_spec,
    color_of_suit,
    load_content_pack,
)
from sgs.effects import discard_count, hand_count, hand_limit
from sgs.engine import Engine
from sgs.skills import CATALOG, conversion_offers
from sgs.state import (
    CardTag,
    DiscardCards,
    EndPhase,
    Pass,
    QueryKind,
    SelectOption,
    UseCard,
    Zone,
    project,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    RecordingPlayer,
    SeatSpec,
    build_state,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")


def _tag(
    tag_type: CardTagType,
    *,
    owner: int = 0,
    skill_id: str = "hero.jie_zhenji.luoshen",
) -> CardTag:
    """一个标签实例：类型 + 来源实例（默认界甄姬【洛神】）。牌 id 是 ``card_tags`` 的键。"""
    return CardTag(tag_type=tag_type, owner=owner, skill_id=skill_id)

TAG_TYPES = frozenset(CardTagType)


def audit_tag_registry(types, registry) -> list[str]:
    """登记表与标签类型集合的**双向**对照；返回全部问题（不 fail-fast）。

    判据与 ``tests/test_card_family_sites.py`` 的 :func:`audit` 同姿态：新增一个
    :class:`~sgs.data.CardTagType` 成员却没有四项声明 ⇒ 红；登记表里躺着已删除的类型 ⇒ 也红。
    """
    problems: list[str] = []
    for tag_type in sorted(types, key=lambda item: str(item)):
        spec = registry.get(tag_type)
        if spec is None:
            problems.append(
                f"未登记的标签类型：{tag_type}——"
                "D12 要求每个类型声明读取方 / 清除触发 / 可见性 / 是否随来源消失四项"
            )
            continue
        for field in ("reader", "clear_trigger", "visibility", "disappears_with_source"):
            if getattr(spec, field, None) is None:
                problems.append(f"{tag_type} 的 {field} 没有声明")
        if not isinstance(spec.disappears_with_source, bool):
            problems.append(f"{tag_type} 的 disappears_with_source 必须是 bool")
    for tag_type in sorted(set(registry) - set(types), key=lambda item: str(item)):
        problems.append(f"登记表里的 {tag_type} 已不是 CardTagType 成员——清单与枚举脱节")
    return problems


class _ProbeTagType(str, Enum):
    """合成反例的类型：**不在**登记表里（证明检查会咬，而不是空转）。"""

    UNREGISTERED = "unregistered_probe"


def test_every_tag_type_declares_the_four_facts() -> None:
    """登记表 = 枚举闭集，且每个类型四项都声明。"""
    assert set(CARD_TAG_TYPES) == TAG_TYPES
    assert audit_tag_registry(TAG_TYPES, CARD_TAG_TYPES) == []

    for spec in CARD_TAG_TYPES.values():
        assert isinstance(spec.reader, CardTagReader)
        assert isinstance(spec.clear_trigger, CardTagClear)
        assert isinstance(spec.visibility, CardTagVisibility)
        assert isinstance(spec.disappears_with_source, bool)


def test_an_unregistered_tag_type_is_reported() -> None:
    """合成反例：类型集合里多一个未登记成员 ⇒ 检查必须报出它。"""
    types = TAG_TYPES | {_ProbeTagType.UNREGISTERED}
    problems = audit_tag_registry(types, CARD_TAG_TYPES)
    assert any("UNREGISTERED" in problem for problem in problems), problems

    # 反面自检：把同一类型补进临时表后必须变绿（否则"红"可能只是格式错）。
    probe = card_tag_spec(CardTagType.LUOSHEN_HAND)
    assert audit_tag_registry(TAG_TYPES | {_ProbeTagType.UNREGISTERED}, {
        **CARD_TAG_TYPES, _ProbeTagType.UNREGISTERED: probe,
    }) == []


def test_reading_an_unregistered_tag_type_fails_closed() -> None:
    """读取未登记类型 ⇒ 显式失败（不静默按"不排除"处理）。"""
    with pytest.raises(KeyError):
        card_tag_spec(_ProbeTagType.UNREGISTERED)  # type: ignore[arg-type]


def test_the_three_m6_shapes_are_registered_with_one_live_carrier() -> None:
    """spec §标签：M6 至少登记三类，只有"本回合不计入"有载体（界甄姬【洛神】）。"""
    specs = {tag_type: CARD_TAG_TYPES[tag_type] for tag_type in TAG_TYPES}
    carriers = {tag_type: spec.carrier for tag_type, spec in specs.items()}
    live = [tag_type for tag_type, carrier in carriers.items() if carrier is not None]

    assert len(specs) == 3, sorted(specs)
    assert live == [CardTagType.LUOSHEN_HAND], "M6 只有一个活载体（界甄姬【洛神】）"
    assert carriers[CardTagType.LUOSHEN_HAND] == "hero.jie_zhenji.luoshen"

    turned = specs[CardTagType.LUOSHEN_HAND]
    assert turned.clear_trigger is CardTagClear.TURN_END
    assert turned.visibility is CardTagVisibility.FACE_VISIBLE
    assert turned.disappears_with_source is True

    zone = specs[CardTagType.ZONE_HAND]
    assert zone.clear_trigger is CardTagClear.LEAVE_ZONE, "黠慧式：离开手牌即清"
    assert zone.visibility is CardTagVisibility.FACE_VISIBLE

    until = specs[CardTagType.UNTIL_PLAYED]
    assert until.clear_trigger is CardTagClear.USED_OR_PLAYED, "遗珠式：到被使用 / 打出为止"
    assert until.visibility is CardTagVisibility.FACE_VISIBLE

    # 载体锚点与 ``CATALOG`` 对账：登记表说"由谁生产"，技能表里就必须真有那一条。
    for tag_type, spec in specs.items():
        if spec.carrier is not None:
            assert spec.carrier in CATALOG, f"{tag_type} 的载体不在 CATALOG 里"


# ---------------------------------------------------------------------------
# 手牌计数读标签（spec D11 的"计数排除"轴；上限是另一个查询）
# ---------------------------------------------------------------------------

HAND = (0, 1, 2, 3, 4)
"""构造局面用的五张手牌（牌 id 直接取自内容包的下标；只为计数，不要求牌守恒）。"""

TAGGED = 2
"""其中被挂上标签的那一张（弃牌阶段不该再数它）。"""


def _count_state(*, hand=HAND, hp: int = 4, turn_seat: int = 0):
    """座位 0 有 ``hand``、体力 ``hp``（⇒ 上限 = hp）的构造局面。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp, hand=hand), SeatSpec()],
        deck=tuple(range(20, 40)),
        turn_seat=turn_seat,
    )


def test_the_hand_count_excludes_tagged_cards_and_the_discard_query_follows() -> None:
    """手牌计数 = 不带"排除计数"标签的张数；上限不变（两个查询互不替换）。"""
    state = _count_state()
    player = state.player(0)

    assert hand_count(state, player) == 5
    assert discard_count(state, player) == 1, "无标签：5 张 − 上限 4 = 弃 1"

    state.card_tags[TAGGED] = (
        _tag(CardTagType.LUOSHEN_HAND),
    )

    assert hand_limit(state, player) == 4, "上限不读标签"
    assert hand_count(state, player) == 4, "带排除计数标签的那张不再数"
    assert discard_count(state, player) == 0, "4 − 4 = 0 ⇒ 弃牌阶段不发询问"


def test_discarding_the_tagged_card_does_not_end_the_discard_phase_early() -> None:
    """弃牌阶段按**计数**收敛：把不计数的那张弃掉不会让计数下降，必须再问一次。

    规则上洛神牌"不计入本回合的手牌上限"，但它仍是手牌、**可以被弃**；弃掉它只是让
    ``len(hand)`` 降 1，``hand_count`` 不变。旧实现只问一次就进 ``PHASE_DISCARD_END``，
    于是出现"计数仍 > 上限"的非法边界（M6 票 12 的混合局由 fuzz 抓到）。判据 = 询问
    次数与最终边界：第一次弃掉带标签的牌之后还要再问，直到计数回落到上限。
    """
    state = _count_state(hand=(0, 1, 2, 3, 4, 5))
    state.card_tags[TAGGED] = (_tag(CardTagType.LUOSHEN_HAND),)
    engine = Engine.from_state(state, seed=7)
    queries = []

    class _Discarder:
        """按 ``picks`` 逐次弃牌（每个询问一组牌 id）；其它询问走最小应答。"""

        def __init__(self, picks) -> None:
            self._picks = list(picks)

        def respond(self, query):
            if query.kind == QueryKind.DISCARD:
                queries.append(query)
                wanted = set(self._picks.pop(0))
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, DiscardCards)
                    and {card.card_id for card in action.cards} == wanted
                )
            if query.kind == QueryKind.PLAY_PHASE:
                return next(
                    action for action in query.legal_actions if isinstance(action, EndPhase)
                )
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]

    # 初始 6 张 + 摸牌阶段 2 张 = 8；其中一张带标签 ⇒ 计数 7、上限 4 ⇒ 弃 3。
    # 第一次弃掉"带标签的 2 号 + 两张计数牌" ⇒ 计数只降 2（7 → 5）⇒ 还要再弃 1 张。
    run_turns(engine, [_Discarder([{2, 0, 1}, {3}]), PassPlayer()], turns=1)

    player = state.player(0)
    assert [query.count for query in queries] == [3, 1], (
        "弃掉带标签的牌之后计数没有降到上限，弃牌阶段必须继续问"
    )
    assert TAGGED not in player.hand
    assert hand_count(state, player) == hand_limit(state, player) == 4, "边界：计数回落到上限"


def test_the_tag_never_changes_the_public_hand_count_or_enters_a_payload() -> None:
    """spec §投影与记录流：标签的增删**不改变**公开的"手牌数"这个数字。"""
    state = _count_state()
    before_self = project(state, 0).model_dump(mode="json")
    before_other = project(state, 1).model_dump(mode="json")

    state.card_tags[TAGGED] = (
        _tag(CardTagType.LUOSHEN_HAND),
    )

    after_self = project(state, 0).model_dump(mode="json")
    after_other = project(state, 1).model_dump(mode="json")
    assert after_self == before_self, "标签不该进本人载荷"
    assert after_other == before_other, "标签不该进他人载荷"
    assert after_self["players"][0]["hand_count"] == len(HAND), "公开手牌数恒 = len(hand)"
    assert "luoshen_hand" not in json.dumps(after_self, ensure_ascii=False)
    assert "luoshen_hand" not in json.dumps(after_other, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 生存期：写入口 / 回合末清除 / 另两类形状的构造测试
# ---------------------------------------------------------------------------


def _count_engine(*, hand=HAND, hp: int = 4):
    """构造局面起跑的引擎（座位 0 的回合；牌 id 直接给，不要求牌守恒）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp, hand=hand), SeatSpec()],
        deck=tuple(range(20, 40)),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Engine.from_state(state, seed=7), state


def test_the_tag_writer_is_idempotent_and_rejects_virtual_cards() -> None:
    """写入唯一入口：实体牌才可挂；同一标签幂等。"""
    engine, state = _count_engine()
    tag = _tag(CardTagType.LUOSHEN_HAND)

    assert engine.add_card_tag(TAGGED, tag) is True
    assert engine.add_card_tag(TAGGED, tag) is False, "同一标签重复挂是幂等的"
    assert state.tags_of(TAGGED) == (tag,)
    assert state.has_card_tag(TAGGED, CardTagType.LUOSHEN_HAND) is True

    with pytest.raises(ValueError):
        engine.add_card_tag(-1, tag)


def test_the_turn_end_clears_the_this_turn_tags_and_the_count_restores() -> None:
    """本回合不计入：回合末清除 ⇒ 计数立刻恢复（spec §标签：生存期 = 本回合）。

    判据读两个查询的**关系**而不是写死张数：回合内 ``hand_count == len(hand) - 1``
    （带标签那张不数），回合末 ``hand_count == len(hand)``（标签没了，整手都数）。
    弃牌阶段现在会弃到**计数**回落为止（带标签的牌被弃掉时还要补弃），故"弃完之后的
    手牌张数"不再是旧实现下的那个常量。
    """
    engine, state = _count_engine()
    engine.add_card_tag(TAGGED, _tag(CardTagType.LUOSHEN_HAND))
    player = state.player(0)
    assert discard_count(state, player) == 0
    assert hand_count(state, player) == len(player.hand) - 1, "回合内：带标签那张不数"

    run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=1)

    assert state.card_tags == {}, "回合末清除全部 TURN_END 标签"
    assert hand_count(state, player) == len(player.hand), "计数恢复：整手都数"
    assert hand_count(state, player) <= hand_limit(state, player), "弃牌阶段按计数收敛"


def test_a_zone_tag_keeps_excluding_while_in_hand_and_is_cleared_on_leaving_the_zone() -> None:
    """黠慧式（无载体，构造测试）：在手牌期间不计入；离开手牌即清（LEAVE_ZONE）。"""
    engine, state = _count_engine()
    tag = _tag(CardTagType.ZONE_HAND, skill_id="hero.future.xiahui")
    engine.add_card_tag(TAGGED, tag)
    assert hand_count(state, state.player(0)) == 4, "在手牌里 ⇒ 不计入"

    # 牌离开手牌：读取方自然读不到它（计数不再受影响）；清除触发再把标签本身撤掉。
    state.player(0).hand.remove(TAGGED)
    state.discard.append(TAGGED)
    assert hand_count(state, state.player(0)) == 4, "剩下四张照常计数"
    assert engine.clear_card_tags(CardTagClear.LEAVE_ZONE) == 1
    assert state.tags_of(TAGGED) == (), "离开声明区域即清"


def test_an_until_played_tag_lasts_until_the_card_is_used_or_played() -> None:
    """遗珠式（无载体，构造测试）：到被使用 / 打出为止；回合末清理**不**碰它。"""
    engine, state = _count_engine()
    tag = _tag(CardTagType.UNTIL_PLAYED, skill_id="hero.future.yizhu")
    engine.add_card_tag(TAGGED, tag)
    assert hand_count(state, state.player(0)) == 4

    assert engine.clear_card_tags(CardTagClear.TURN_END) == 0, "它的清除触发不是回合末"
    assert state.tags_of(TAGGED) == (tag,)

    assert engine.clear_card_tags(CardTagClear.USED_OR_PLAYED) == 1
    assert hand_count(state, state.player(0)) == 5, "用掉之后不再排除"


# ---------------------------------------------------------------------------
# 载体：界甄姬【洛神】（规则 §十九 L2061–L2065）
# ---------------------------------------------------------------------------

JIE_LUOSHEN = "hero.jie_zhenji.luoshen"
JIE_QINGGUO = "hero.jie_zhenji.qingguo"
REPEAT_PREFIX = "luoshen:repeat:"

BLACK_SLASH = 40
"""黑色【杀】（标准 108 档的牌 id；对相邻座位 1 合法：距离 1 / 范围 1）。"""

HAND_LIMIT = 3
"""界甄姬体力上限 3 ⇒ 体力 3 时手牌上限 = 3。"""


def _fillers(*used: int) -> list[int]:
    """牌堆填充：取内容包里没被本用例占用的牌 id（构造局面不要求牌守恒）。"""
    return [index for index in range(len(PACK.deck.cards)) if index not in used]


class _JieZhenjiPlayer:
    """座位 0 的脚本：发动【洛神】→ 每轮答"停止"→ 结束出牌阶段；记录弃牌询问。"""

    def __init__(self, *, activate: bool = True, repeats: int = 0) -> None:
        self.activate = activate
        self.repeats = repeats
        self.queries: list = []
        self.repeat_queries: list = []
        self.discard_queries: list = []

    def respond(self, query):
        self.queries.append(query)
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LUOSHEN:
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id).startswith(
            REPEAT_PREFIX
        ):
            self.repeat_queries.append(query)
            if len(self.repeat_queries) > self.repeats:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "repeat":
                    return action
            raise AssertionError(f"「是否继续」里没有 repeat：{query.legal_actions}")
        if query.kind == QueryKind.DISCARD:
            self.discard_queries.append(query)
            return query.legal_actions[0]
        if query.kind == QueryKind.PLAY_PHASE:
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _jie_zhenji_engine(*, hand=(10,), deck=None, repeats: int = 0):
    """界甄姬在座位 0 的构造局面；牌堆缺省 = 黑（【洛神】获得）→ 摸牌阶段的两张。

    手牌 1 + 获得 1 + 摸 2 = 4 张：上限 3 ⇒ 不带标签本应弃 1 张，带标签则恰好 0 张。
    """
    fillers = _fillers(BLACK_SLASH, *hand)
    resolved_deck = tuple(deck) if deck is not None else (
        BLACK_SLASH, *fillers[:4]
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hp_max=3, hand=hand, hero="jie_zhenji"),
            SeatSpec(),
        ],
        deck=resolved_deck,
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=7)
    return engine, state, _JieZhenjiPlayer(repeats=repeats)


def _to_play_phase(engine, zhenji):
    """推进到座位 0 的出牌阶段（准备阶段的【洛神】已结算完）。"""
    run_until(
        engine,
        [zhenji, PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    return engine


def _to_next_play_phase(engine, zhenji):
    """结束座位 0 的出牌阶段，推进到**座位 1 的出牌阶段**（座位 0 的回合已走完）。"""
    query = engine.pending
    assert query is not None and query.kind == QueryKind.PLAY_PHASE
    engine.submit(next(a for a in query.legal_actions if isinstance(a, EndPhase)))
    run_until(
        engine,
        [zhenji, PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    )
    return engine


def test_jie_zhenji_tags_the_black_card_gained_by_luoshen_for_the_turn() -> None:
    """【洛神】的黑牌进手牌并带上"本回合不计入"标签；弃牌阶段因此不问弃牌。"""
    engine, state, zhenji = _jie_zhenji_engine()
    _to_play_phase(engine, zhenji)
    player = state.player(0)

    assert BLACK_SLASH in player.hand, "黑色判定牌被【洛神】获得"
    assert state.has_card_tag(BLACK_SLASH, CardTagType.LUOSHEN_HAND)
    assert state.tags_of(BLACK_SLASH)[0].skill_id == JIE_LUOSHEN
    assert state.tags_of(BLACK_SLASH)[0].owner == 0

    assert len(player.hand) == 4
    assert hand_limit(state, player) == HAND_LIMIT
    assert hand_count(state, player) == 3, "带标签的那张不数"
    assert discard_count(state, player) == 0
    assert len(player.hand) - hand_limit(state, player) == 1, (
        "对照：不带标签本应弃 1 张——所以『弃牌阶段没有询问』不是空转"
    )

    _to_next_play_phase(engine, zhenji)
    assert zhenji.discard_queries == [], "计数排除生效 ⇒ 弃牌阶段不发询问"
    assert state.card_tags == {}, "回合末清除标签"
    assert discard_count(state, player) == 1, "计数随回合末恢复"


def _tag_events(engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


def test_tag_additions_and_removals_reach_the_record_stream() -> None:
    """spec §投影与记录流：标签增删**进记录流**（M6 票 20 补的 delta）。

    判据只读记录流：① 【洛神】发动后落一条 ``tag_add``，载荷 = 那张牌 + 标签类型 +
    来源实例；② 它落在**这次技能发动**之后（不是开局的计划）；③ 回合末清理落一条同形的
    ``tag_remove``。标签不是牌：两条都不进 ``card_move``，也不改牌的位置——牌在判定后从
    处理区进弃牌堆、再被【洛神】拿进手牌，标签在这之间挂上（标签与区域无关）。
    """
    engine, state, zhenji = _jie_zhenji_engine()
    _to_play_phase(engine, zhenji)

    added = _tag_events(engine, "tag_add")
    assert len(added) == 1, f"【洛神】获得黑牌应落一条 tag_add：{added}"
    event = added[0]
    assert event.card.card_id == BLACK_SLASH, "载荷 = 被挂标签的那张实体牌"
    assert event.tag_type is CardTagType.LUOSHEN_HAND
    assert event.owner == 0 and event.skill_id == JIE_LUOSHEN, "来源实例 = 哪个技能挂的"

    activation = next(
        record
        for record in engine.records
        if record.kind == "action" and getattr(record.action, "skill_id", None) == JIE_LUOSHEN
    )
    assert activation.seq < event.seq, "标签由这次【洛神】发动挂上（顺序可读）"

    _to_next_play_phase(engine, zhenji)
    removed = _tag_events(engine, "tag_remove")
    assert len(removed) == 1, f"回合末清除应落一条 tag_remove：{removed}"
    assert removed[0].card.card_id == BLACK_SLASH
    assert removed[0].tag_type is CardTagType.LUOSHEN_HAND
    assert removed[0].owner == 0 and removed[0].skill_id == JIE_LUOSHEN


def test_losing_or_suppressing_the_source_reports_the_tag_removal() -> None:
    """随来源实例消失的清除（失去 / 失效）同样进记录流：一条 ``tag_remove``。"""
    engine, state, zhenji = _jie_zhenji_engine()
    _to_play_phase(engine, zhenji)
    assert len(_tag_events(engine, "tag_add")) == 1

    assert engine.lose_skill(0, JIE_LUOSHEN) is True
    removed = _tag_events(engine, "tag_remove")
    assert len(removed) == 1, "失去【洛神】⇒ 一条 tag_remove（不是静默清状态）"
    assert removed[0].card.card_id == BLACK_SLASH

    engine2, state2, zhenji2 = _jie_zhenji_engine()
    _to_play_phase(engine2, zhenji2)
    assert engine2.suppress_skills(0, "hero.jie_guanyu.yijue") is True
    removed2 = _tag_events(engine2, "tag_remove")
    assert len(removed2) == 1, "失效【洛神】⇒ 同样落一条 tag_remove"


def test_using_the_tagged_card_does_not_disturb_the_count() -> None:
    """牌被打出 / 用掉后不影响计数：标签留在牌上（与区域无关），手牌数照常。"""
    engine, state, zhenji = _jie_zhenji_engine()
    _to_play_phase(engine, zhenji)
    player = state.player(0)
    assert discard_count(state, player) == 0

    query = engine.pending
    assert query is not None and query.kind == QueryKind.PLAY_PHASE
    use = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == BLACK_SLASH
        and action.targets == (1,)
    )
    engine.submit(use)
    run_until(
        engine,
        [zhenji, PassPlayer()],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE,
    )

    assert BLACK_SLASH not in player.hand, "【杀】已离开手牌"
    assert state.tags_of(BLACK_SLASH), "标签跟着牌走（与区域无关），不因离开手牌被清"
    assert hand_count(state, player) == 3
    assert discard_count(state, player) == 0, "3 张 − 上限 3 = 0，与那张已用掉的牌无关"


def test_losing_or_suppressing_luoshen_clears_its_tags_at_once() -> None:
    """失去 / 失效【洛神】⇒ 自家手牌的该类标签一并清除，计数立刻恢复（票 09 验收）。"""
    engine, state, zhenji = _jie_zhenji_engine()
    _to_play_phase(engine, zhenji)
    player = state.player(0)
    assert discard_count(state, player) == 0

    assert engine.lose_skill(0, JIE_LUOSHEN) is True
    assert state.tags_of(BLACK_SLASH) == (), "失去来源 ⇒ 标签清除"
    assert discard_count(state, player) == 1, "计数立刻恢复"

    # 失效走同一条清理（来源实例不再解析）；新建一局避免失去与失效互相干扰。
    engine2, state2, zhenji2 = _jie_zhenji_engine()
    _to_play_phase(engine2, zhenji2)
    player2 = state2.player(0)
    assert discard_count(state2, player2) == 0

    assert engine2.suppress_skills(0, "hero.jie_guanyu.yijue") is True
    assert state2.tags_of(BLACK_SLASH) == (), "失效来源 ⇒ 标签同样清除"
    assert discard_count(state2, player2) == 1


def test_jie_qingguo_is_the_standard_text_on_another_hero_id() -> None:
    """【倾国】与标准版逐字相同：黑色**手牌**当【闪】；界版只是另一个武将 id。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(BLACK_SLASH,), hero="jie_zhenji"),
            SeatSpec(),
        ],
        deck=tuple(range(20, 40)),
        turn_seat=0,
    )

    assert (BLACK_SLASH, JIE_QINGGUO) in conversion_offers(state, 0, "dodge")

    standard = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,), hero="zhenji"), SeatSpec()],
        deck=tuple(range(20, 40)),
        turn_seat=0,
    )
    assert (BLACK_SLASH, "hero.zhenji.qingguo") in conversion_offers(standard, 0, "dodge")
    assert (BLACK_SLASH, JIE_QINGGUO) not in conversion_offers(standard, 0, "dodge")


def test_the_jie_zhenji_data_entry_lands_in_the_catalog() -> None:
    """数据文件追加界甄姬（洛神 + 倾国，均启用）；两条技能都在 ``CATALOG`` 里闭合。"""
    from sgs.data import hero_skill_ids, load_heroes
    from sgs.skills import CATALOG

    hero = load_heroes("jie").by_id("jie_zhenji")
    assert hero is not None
    assert (hero.name, hero.true_name, hero.version) == ("界甄姬", "甄姬", "jie-ol")
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("wei", "female", 3)
    assert [skill.id for skill in hero.skills] == ["luoshen", "qingguo"]
    assert hero.enabled is True
    assert hero_skill_ids("jie_zhenji") == (JIE_LUOSHEN, JIE_QINGGUO)

    for skill_id in (JIE_LUOSHEN, JIE_QINGGUO):
        assert skill_id in CATALOG
    assert CATALOG[JIE_LUOSHEN].hooks[0].timing is Timing.PHASE_PREPARE


def test_every_black_card_gained_by_jie_luoshen_carries_its_own_tag() -> None:
    """每轮判定各得一张：**每张**以此法获得的牌都带标签（不是"整局只挂一次"）。"""
    second_black = 42
    assert color_of_suit(PACK.deck.cards[second_black].suit) is Color.BLACK
    engine, state, zhenji = _jie_zhenji_engine(
        deck=(BLACK_SLASH, second_black, *_fillers(BLACK_SLASH, second_black, 10)[:4]),
        repeats=1,
    )
    _to_play_phase(engine, zhenji)

    for card_id in (BLACK_SLASH, second_black):
        assert card_id in state.player(0).hand
        assert state.has_card_tag(card_id, CardTagType.LUOSHEN_HAND), card_id
        assert state.tags_of(card_id)[0].skill_id == JIE_LUOSHEN


def test_the_standard_luoshen_gains_the_same_card_without_a_tag() -> None:
    """标准版【洛神】逐字没有"不计入手牌上限"半句 ⇒ 同一张黑牌不带标签。"""
    fillers = _fillers(BLACK_SLASH, 10)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=3, hp_max=3, hand=(10,), hero="zhenji"), SeatSpec()],
        deck=(BLACK_SLASH, *fillers[:4]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=7)

    class _StandardPlayer(_JieZhenjiPlayer):
        def respond(self, query):
            if (
                query.kind == QueryKind.SKILL_ACTIVATION
                and query.skill_id == "hero.zhenji.luoshen"
            ):
                return next(
                    action
                    for action in query.legal_actions
                    if getattr(action, "kind", None) == "ActivateSkill"
                )
            return super().respond(query)

    player = _StandardPlayer()
    _to_play_phase(engine, player)
    assert BLACK_SLASH in state.player(0).hand, "标准版照样获得黑色判定牌"
    assert state.card_tags == {}, "标准版没有标签轴"
