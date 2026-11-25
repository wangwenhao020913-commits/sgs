"""U 族：使用牌时触发（M6 票 02）——黄月英【集智】。

规则原文：§九 L1013（"当你使用一张非延时类锦囊牌时，你可以摸一张牌"）与界版 §十九
L2104–L2105（"当你使用一张**非转化的**锦囊牌时……"）。两版牌面**不同**（M6 评审订正）：

- **标准版**只排除延时类：**转化锦囊照常触发**（票 02 曾把"非转化"写进标准版口径）；
- **界版**只排除转化牌（"非转化的"只在界版文本里）：**延时类非转化锦囊照常触发**
  （界版牌面**没有**"非延时类"；正例在 ``tests/test_hero_jie_hand_limit.py``）。

落点全部是外部缝：

- **询问缝**：``SKILL_ACTIVATION`` 的**条数**（非锁定技：每次使用问一次；不该问的一次不问）
  与 ``skill_id``（标准 / 界各一个）；
- **终局状态**：手牌内容（摸到的**正是**牌堆顶那张，且排在锦囊自身效果摸到的牌之前）；
- 真引擎的整段出牌阶段（含**转化牌**这一正反两例：来源由 ``start_use`` 直接压帧，与
  ``tests/test_m5_attribute_damage.py`` 同一手法）。

标准版排除延时类、界版排除转化牌，两版各有一个反例（界版对延时类的正例另居 Jie 文件）。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.skills import transformed_card
from sgs.state import FrameKind, Pass, QueryKind, UseCard

from support import PassPlayer, SeatSpec, build_state, cards_of_type, run_until
from test_skills import _deck_excluding, _use_frame_popped

PACK = load_content_pack(deck_profile="standard_108")

JIZHI = "hero.huangyueying.jizhi"
JIE_JIZHI = "hero.jie_huangyueying.jizhi"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _jizhi_queries(engine, skill_id: str = JIZHI) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and record.query.skill_id == skill_id
    ]


class _JizhiAnswerer:
    """座位 0 的应答：只答【集智】的「是否发动」，其余一律放弃。"""

    def __init__(self, *, activate: bool = True) -> None:
        self.activate = activate

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIZHI:
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


class _PassThrough:
    """其余座位：响应一律放弃。"""

    def respond(self, query):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _use_in_play_phase(
    trick: int, *, targets: tuple[int, ...] = (), activate: bool = True, seats: int = 2
):
    """推到座位 0 的出牌阶段 → 提交一次使用 → 推进到该使用帧出栈。

    返回 ``(engine, 使用前手牌, 使用前牌堆)``：**摸牌阶段已经过去**，故这两份快照就是
    "集智该摸的那张"与"锦囊自己摸的牌"的独立真源（不读引擎内部随机数）。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(trick,), hero="huangyueying"),
            *(SeatSpec() for _ in range(seats - 1)),
        ],
        deck=_deck_excluding(trick),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    players = [PassPlayer() for _ in range(seats)]
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    hand_before = list(state.player(0).hand)
    deck_before = list(state.deck)
    query = engine.pending
    action = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == trick
        and action.targets == targets
    )
    engine.submit(action)
    answerers = [*players]
    answerers[0] = _JizhiAnswerer(activate=activate)
    run_until(engine, answerers, _use_frame_popped)
    return engine, hand_before, deck_before


def test_jizhi_draws_one_card_when_its_owner_uses_a_non_delayed_trick():
    """正例：无中生有时先摸【集智】那一张，再结算锦囊自己的两张。"""
    trick = _cards("ex_nihilo")[0]

    engine, hand_before, deck_before = _use_in_play_phase(trick, targets=(0,))

    assert len(_jizhi_queries(engine)) == 1, "每次使用问一次「是否发动」"
    expected = [card for card in hand_before if card != trick] + deck_before[:3]
    assert list(engine.state.player(0).hand) == expected, (
        "集智先摸牌堆顶一张，随后【无中生有】再摸两张——次序即手牌序"
    )
    assert engine.state.player(0).card_use_counts.get("ex_nihilo") == 1


def test_declining_jizhi_draws_nothing():
    """反例：同样的使用，答「不发动」⇒ 只剩锦囊自身的两张。"""
    trick = _cards("ex_nihilo")[0]

    engine, hand_before, deck_before = _use_in_play_phase(trick, targets=(0,), activate=False)

    assert len(_jizhi_queries(engine)) == 1, "问过一次，只是答不发动"
    expected = [card for card in hand_before if card != trick] + deck_before[:2]
    assert list(engine.state.player(0).hand) == expected, "少摸集智那一张"


def test_jizhi_does_not_fire_on_a_basic_card():
    """反例：使用【杀】不触发（"非延时类锦囊"才触发）。"""
    slash = _cards("slash")[0]

    engine, hand_before, _deck_before = _use_in_play_phase(slash, targets=(1,))

    assert _jizhi_queries(engine) == [], "【杀】不是锦囊 ⇒ 连「是否发动」都不该问"
    assert list(engine.state.player(0).hand) == [
        card for card in hand_before if card != slash
    ], "没有集智摸牌"


def test_jizhi_does_not_fire_on_a_delayed_trick():
    """反例：标准版（§九 L1013 的"非延时类"）对真【乐不思蜀】不触发。

    界版牌面**没有**"非延时类"（§十九 L2120–L2121），同一张牌照常触发——正例见
    ``tests/test_hero_jie_hand_limit.py::test_jie_jizhi_fires_on_a_delayed_non_transformed_trick``。
    """
    delayed = _cards("indulgence")[0]

    engine, hand_before, _deck_before = _use_in_play_phase(delayed, targets=(1,))

    assert _jizhi_queries(engine) == [], "标准版：延时类锦囊不触发集智"
    assert list(engine.state.player(0).hand) == [
        card for card in hand_before if card != delayed
    ]


def _use_transformed_trick(hero_id: str):
    """把一张手牌转成【决斗】使用（不经候选面）→ 推进到"集智已应答、下一问未答"。

    与 ``test_m5_attribute_damage`` 同一手法：``start_use`` 直接压使用帧；外部可观测的
    只有"集智问没问"与手牌结果。产物取【决斗】是因为它已在虚拟牌命名空间里登记
    （没有新开空段）。返回 ``(engine, 使用前牌堆)``：牌堆顶就是集智该摸的那张。
    """
    source = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(source,), hero=hero_id), SeatSpec(hp=4)],
        deck=_deck_excluding(source),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    deck_before = list(state.deck)
    virtual = transformed_card(state, (source,), "duel")

    engine.start_use(virtual, source=0, targets=(1,))
    for _ in range(200):
        if engine.pending is not None:
            query = engine.pending
            if (
                query.kind == QueryKind.SKILL_ACTIVATION
                and query.skill_id in (JIZHI, JIE_JIZHI)
            ):
                engine.submit(
                    next(
                        action
                        for action in query.legal_actions
                        if getattr(action, "kind", None) == "ActivateSkill"
                    )
                )
                continue
            break  # 集智之后的第一问（【决斗】的响应窗口）——此时手牌已定型
        if engine.finished:
            break
        engine._step()  # noqa: SLF001 - 与既有构造局面测试同一推进手法
    return engine, deck_before


def test_jizhi_fires_on_a_transformed_trick_card():
    """标准版（§九 L1013 没有"非转化"）：转化【决斗】照样触发，摸牌堆顶一张。

    M6 评审订正：票 02 曾把界版的"非转化"也套到标准版上，导致标准【集智】对转化锦囊
    整段哑火；规则原文只排除**延时类**。
    """
    engine, deck_before = _use_transformed_trick("huangyueying")

    assert len(_jizhi_queries(engine)) == 1, "标准版对转化锦囊照样问一次「是否发动」"
    assert list(engine.state.player(0).hand) == [deck_before[0]], "集智摸到牌堆顶那一张"


def test_jie_jizhi_does_not_fire_on_a_transformed_trick_card():
    """界版（§十九 L2121 的"非转化的"）：同一次转化【决斗】⇒ 不问、不摸。"""
    engine, _deck_before = _use_transformed_trick("jie_huangyueying")

    assert _jizhi_queries(engine, JIE_JIZHI) == [], "界版把转化牌挡在触发条件之外"
    assert list(engine.state.player(0).hand) == [], "来源牌已按使用路径离手，且没有集智摸牌"



def test_jie_jizhi_cancelled_ask_does_not_discard_the_drawn_card():
    """界【集智】的"弃置换 +1"询问被取消 ⇒ 不发动（票 06；``use_after.py:303`` 那个站点）。

    窗口外已阵亡的持有者：``ask_option`` 返回 ``Cancelled``——它不是 ``Pass``，所以旧实现
    ``isinstance(answer, Pass)`` 为假、把取消读成"发动"：弃掉刚摸到的基本牌并加手牌上限。
    正确读法是"这一问没有发生" ⇒ 不弃牌、不加式、整段收口。

    直调 ``_jie_jizhi``（与 01 票借刀用例同一姿态）：钩子只在"使用牌时"派发，而
    ``resolve_instances`` 不会为已阵亡的座位产出实例——"钩子已经跑起来、持有者随后阵亡"
    这一中间态只能这样摆；``Engine`` 与 ``ask_option`` 仍是产品路径。
    """
    from sgs.resolution import Frame
    from sgs.skills.use_after import (
        _JIE_CARD_KEY,
        _JIE_DRAWN_KEY,
        _JIE_BONUS_KEY,
        _jie_jizhi,
    )

    drawn = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(drawn,), hp=0, alive=False), SeatSpec()],
        deck=_deck_excluding(drawn),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=3)
    frame = Frame(
        kind=FrameKind.SKILL,
        data={
            "source": 0,
            "targets": (),
            "skill_id": JIE_JIZHI,
            "skill_owner": 0,
            _JIE_DRAWN_KEY: True,
            _JIE_CARD_KEY: drawn,
        },
    )

    assert _jie_jizhi(engine, frame) is None, "取消 = 整段收口（不返回 'stay'、不再发动）"
    assert drawn in engine.state.player(0).hand, "取消不是'发动'：这张牌不弃置"
    assert drawn not in engine.state.discard, "没有弃牌发生"
    assert engine.state.player(0).hand_limit_bonus == 0, "取消不是'发动'：不加手牌上限"
    assert frame.data.get(_JIE_BONUS_KEY) is True, "整段收口要落'本帧已处理'游标"
    assert [record for record in engine.records if record.kind == "query"] == [], (
        "死者的询问不落 QueryRecord（引擎闸在发问前取消）"
    )
