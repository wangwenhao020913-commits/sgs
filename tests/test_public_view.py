"""公开面进视角（ai-view 票 06）：``PlayerView`` 上的**有界**公开面切片。

判据（票面 `## 验收`）：

1. **有界**：视图只带**聚合计数** + **最近 K 条公开事件**（K = ``PUBLIC_SURFACE_RECENT_LIMIT``），
   全量事件表不进视图；
2. **不泄露**：隐藏手牌变化 / 身份未亮明 / 牌堆顺序 / 扣置牌真值 / 隐藏步骤答案
   都不进新字段（逐字节不变）；
3. **等价**：``fold(引擎重放) ≡ GameState 增量维护``——同一局磁带重放后逐座位投影相等；
4. **不进记录流**：``exclude=True`` ⇒ ``model_dump`` 无这两个键、journal 无痕迹。

本文件只测**读面**：本票不让打分器读它们（读是票 07），读点表的登记另在
``tests/test_ai_adaptation.py`` 的覆盖判据里咬住。
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import fields, is_dataclass
from enum import Enum
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from sgs.agents import HeuristicPlayer, TapePlayer, tape_from_journal
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, ai_stream
from sgs.state import (
    PUBLIC_SURFACE_RECENT_LIMIT,
    ActivateSkill,
    CardMoveDelta,
    Cause,
    DeckShuffleDelta,
    EndPhase,
    FactKind,
    IdentityDealDelta,
    MoveReason,
    Phase,
    PlayerView,
    PublicFact,
    PublicSurface,
    SelectOption,
    Zone,
    ZoneRef,
    fact_class,
    project,
)
from sgs.state.models import ENGINE_LEVEL_FACT_CLASSES
from sgs.skills.virtual import transformed_card

from support import PassPlayer, SeatSpec, build_state, deck_excluding, run_queries, run_turns
from test_hero_feng import GuohuoScript

PACK = load_content_pack(deck_profile="basic_53")
"""票 06 的构造局只判"公开面进视图"，牌堆档位与金录像无关，取最小档。"""


# ---------------------------------------------------------------------------
# 构造局面（两个座位、放牌堆顺序不同 ⇒ 摸到的牌不同、公开事实完全相同）
# ---------------------------------------------------------------------------


def _quiet_game(deck: tuple[int, ...], *, seats: int = 2) -> Engine:
    """两座位、双方只结束阶段：只产生摸牌 / 回合切换 / 结束阶段三类公开事实。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=()) for _ in range(seats)],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    run_turns(engine, [PassPlayer() for _ in range(seats)], turns=2)
    return engine


def _drawn_ids(engine: Engine) -> tuple[int, ...]:
    """这局摸到的全部牌 id（按事实序）。"""
    return tuple(
        int(card.card_id)
        for fact in engine.state.public_surface.recent
        if fact_class(fact) == "move:DRAW"
        for card in fact.payload.cards
    )


def _guohuo_until_doubt(card_id: int, declared: str) -> tuple[Engine, object]:
    """跑到【蛊惑】逐座位质疑那一问：此刻扣置牌在处理区、牌面不公开。"""
    used = {card_id}
    filler = tuple(
        index for index in range(len(PACK.deck.cards) - 1, -1, -1) if index not in used
    )[-2:]
    used |= set(filler)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(card_id,), hp=2, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=11)
    players = [
        GuohuoScript(activate=True, declare=declared),
        GuohuoScript(),
        GuohuoScript(),
    ]

    def asked_about_doubt(current: Engine) -> bool:
        """停在**第二个**质疑者那一问：此时扣置牌仍在处理区、且已有一步选项答案落账。"""
        pending = current.pending
        return (
            pending is not None
            and str(getattr(pending, "skill_id", "")).endswith(":doubt")
            and int(pending.actor) == 2
        )

    run_queries(engine, players, asked_about_doubt)
    return engine, state


# ---------------------------------------------------------------------------
# §1 有界：视图带的是"聚合计数 + 最近 K 条"，不是全量事件表
# ---------------------------------------------------------------------------


def test_the_view_carries_the_bounded_public_surface():
    engine = _quiet_game(tuple(range(30, 50)))
    surface = engine.state.public_surface

    assert surface.total > PUBLIC_SURFACE_RECENT_LIMIT or surface.recent, "前置：确实有公开事实"
    for seat in range(2):
        view = project(engine.state, seat)
        assert isinstance(view, PlayerView)
        assert view.public_counts == tuple(surface.counts), (
            "聚合计数逐格照搬（同一份事实，不另算一份）"
        )
        assert len(view.public_history) == len(surface.recent)
        assert len(view.public_history) <= PUBLIC_SURFACE_RECENT_LIMIT
        assert [(event.seq, event.fact_class) for event in view.public_history] == [
            (fact.seq, fact_class(fact)) for fact in surface.recent
        ], "事件的锚点 / 机械类别与原事实逐条对齐"
        assert all(event.payload is not None for event in view.public_history), (
            "这一局没有可裁剪的载荷（摸牌 + 结束阶段），全部照进视图"
        )


def test_the_public_surface_stays_out_of_the_record_stream():
    """``exclude=True``：两个新字段既不在视图 dump 里，也不在 journal 里。"""
    engine = _quiet_game(tuple(range(30, 50)))
    view = project(engine.state, 0)
    dumped = view.model_dump(mode="json")

    assert "public_history" not in dumped and "public_counts" not in dumped
    assert "public_history" not in engine.journal()
    assert "public_counts" not in engine.journal()


# ---------------------------------------------------------------------------
# §2 不泄露：隐藏信息不进新字段
# ---------------------------------------------------------------------------


def _dump(value) -> object:
    """把一个公开面字段值摊成 JSON 可判定的形态（冻结值类型 / pydantic 模型混合）。"""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _dump(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (tuple, list)):
        return [_dump(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _field_dump(view: PlayerView) -> str:
    """两个新字段的 canonical JSON（判"逐字节不变"用；``exclude`` 字段不进 view dump）。"""
    return json.dumps(
        {
            "counts": [_dump(entry) for entry in view.public_counts],
            "history": [_dump(event) for event in view.public_history],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _json(value: str) -> str:
    """一个字符串值的 JSON 形态（判"这个值有没有出现在载荷里"）。"""
    return json.dumps(value, ensure_ascii=False)


def _record(
    state,
    *,
    seq,
    payload,
    actor=None,
    targets=(),
    kind=FactKind.DELTA,
    cause=None,
    window=None,
    step=None,
    answer_hidden=False,
):
    """按**生产写入口**（``PublicSurface.record``）记一条事实（构造用例的注入点）。"""
    state.public_surface.record(
        PublicFact(
            seq=seq,
            kind=kind,
            actor=actor,
            targets=targets,
            window=window,
            payload=payload,
            cause=cause,
            step=step,
            answer_hidden=answer_hidden,
        )
    )


def test_a_hidden_hand_never_moves_the_new_view_fields():
    """两局**只差隐藏手牌**（牌堆顺序不同 ⇒ 摸到的牌不同）⇒ 新字段逐字节不变。

    这是票面「不泄露」判据的第一半（``test_m5_no_leak`` 是 delta-generic 的，抓不到
    新增的视图字段）：``move:DRAW`` 的原始载荷带牌面，投影必须把它裁成"摸了几张"。
    """
    hot = _quiet_game(tuple(range(30, 50)))
    cold = _quiet_game(tuple(reversed(range(30, 50))))

    assert _drawn_ids(hot) != _drawn_ids(cold), "前置：两局摸到的牌确实不同（否则空转）"
    assert hot.journal() != cold.journal(), "前置：记录流的牌面确实不同"

    for seat in range(2):
        assert _field_dump(project(hot.state, seat)) == _field_dump(
            project(cold.state, seat)
        ), f"座位 {seat} 的新字段随隐藏手牌变了"


def test_a_hidden_identity_never_moves_the_new_view_fields_for_others():
    """两局**只差未亮明的身份** ⇒ 旁观座位的新字段逐字节不变（本人 / 主公照旧可见）。"""
    lord = Identity.LORD
    rebel = Identity.REBEL
    renegade = Identity.RENEGADE
    assert lord is not rebel and rebel is not renegade

    def build(other_identity: Identity) -> Engine:
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hp=4, identity=lord),
                SeatSpec(hp=4, identity=other_identity),
            ],
            deck=(30, 31, 32, 33),
            turn_seat=0,
            phase=Phase.PLAY,
        )
        engine = Engine.from_state(state, seed=3)
        for seat, identity in ((0, lord), (1, other_identity)):
            _record(
                engine.state,
                seq=seat + 1,
                actor=None,
                payload=IdentityDealDelta(seat=seat, identity=identity),
            )
        return engine

    renegade_game = build(renegade)
    rebel_game = build(rebel)

    assert _field_dump(project(renegade_game.state, 0)) == _field_dump(
        project(rebel_game.state, 0)
    ), "未亮明的身份不该出现在旁观座位的新字段里"

    # 本人看得到自己的那张身份牌（发放事实对本人公开）。
    own = project(rebel_game.state, 1)
    assert any(
        getattr(event.payload, "delta_type", None) == "identity_deal"
        and event.payload.seat == 1
        and event.payload.identity is rebel
        for event in own.public_history
    ), "本人应当看得到自己那张身份牌的发放事实"
    assert all(
        getattr(event.payload, "identity", None) is not rebel
        for event in project(rebel_game.state, 0).public_history
    ), "座位 0 不该看到座位 1 未亮明的身份"


def test_the_deck_order_never_reaches_the_view():
    """洗牌事实只回答"洗过了"，**牌堆顺序**（``deck_shuffle.cards``）不进视图。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(30, 31, 32),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    shuffled = tuple(state.cards[card_id] for card_id in (30, 31, 32))
    _record(
        state,
        seq=1,
        payload=DeckShuffleDelta(
            cards=shuffled,
            from_zone=None,
            to_zone=ZoneRef(zone=Zone.DECK),
        ),
    )

    event = project(state, 0).public_history[-1]

    assert event.fact_class == "delta:deck_shuffle", "事实本身仍公开（洗过一次牌）"
    assert event.payload is None, "牌堆顺序不下发"


def test_a_public_move_keeps_its_face_and_a_private_one_only_counts():
    """牌面按**区域**公开性裁剪：弃牌堆 / 装备区 / 判定区公开，手牌 / 牌堆不公开。"""
    public_card = 30
    private_card = 31
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(private_card,)), SeatSpec(hp=4)],
        deck=(30, 31, 32, 33),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    _record(
        state,
        seq=1,
        payload=CardMoveDelta(
            cards=(state.cards[public_card],),
            from_zone=ZoneRef(zone=Zone.HAND, seat=0),
            to_zone=ZoneRef(zone=Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=0,
        ),
    )
    _record(
        state,
        seq=2,
        payload=CardMoveDelta(
            cards=(state.cards[private_card],),
            from_zone=ZoneRef(zone=Zone.HAND, seat=0),
            to_zone=ZoneRef(zone=Zone.HAND, seat=1),
            reason=MoveReason.PUT,
            owner=0,
        ),
    )

    events = project(state, 0).public_history

    discard = next(event for event in events if event.fact_class == "move:DISCARD")
    hand_over = next(event for event in events if event.fact_class == "move:PUT")
    assert [card.card_id for card in discard.payload.cards] == [public_card], (
        "弃牌堆是公开区 ⇒ 牌面进视图"
    )
    assert discard.payload.hidden_count == 0
    assert hand_over.payload.cards == (), "手牌到手牌不公开 ⇒ 一张牌面都不给"
    assert hand_over.payload.hidden_count == 1, "但张数是公开事实"


def test_a_virtual_card_is_shown_only_when_its_sources_are_public():
    """转化 / 声称的**虚拟牌**按来源实体牌判，且来源本身**不进**视图。

    虚拟牌的花色 / 点数是单源继承来的（``transformed_card``）⇒ 来源不公开时整张不给；
    来源公开时给产物、但剥掉 ``virtual_sources``（"由哪几张实体牌变来"不是公开面）。

    构造 = 使用腿之后的状态：来源实体牌在**处理区**里（``USE`` 移动把它搬了过去），
    差别只在它是不是**扣置**进去的那几张（引擎按移动形状维护的集合）。
    """
    source = 30
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(31, 32, 33, 34),
        processing=(source,),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    product = transformed_card(state, (source,), "slash")
    _record(
        state,
        seq=1,
        payload=CardMoveDelta(
            cards=(product,),
            from_zone=ZoneRef(zone=Zone.HAND, seat=0),
            to_zone=ZoneRef(zone=Zone.PROCESSING),
            reason=MoveReason.USE,
            owner=0,
        ),
    )

    hidden = project(state, 0, concealed_processing=(source,)).public_history[-1]
    assert hidden.payload.cards == (), "来源不公开 ⇒ 产物不给"
    assert hidden.payload.hidden_count == 1

    shown = project(state, 0).public_history[-1]
    assert len(shown.payload.cards) == 1
    assert shown.payload.cards[0].type == product.type, "产物牌面（声称值）照发"
    assert shown.payload.cards[0].virtual_sources == (), "来源实体牌不进视图"


def test_a_concealed_claim_card_keeps_its_true_face_out_of_the_view():
    """【蛊惑】扣置期：视图里**找不到**那张扣置牌的真值（票面验收的最后一条）。

    真值的三条泄露路径各钉一处：``answer:ActivateSkill`` 的 ``picks``（实体牌 id）、
    ``move:LOSE`` 的牌面、以及随后按**声称牌名**转化出来的虚拟牌的来源牌。

    构造：真牌 = 一张【闪】，**声称**成【杀】——两个名字不同，故"真值在不在"可判。
    """
    declared = "杀"
    true_card = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "dodge"
    )
    engine, state = _guohuo_until_doubt(true_card, declared)
    true_ref = state.card(true_card)

    blob = _field_dump(
        project(engine.state, 0, concealed_processing=engine.processing_concealed)
    )
    assert f'"card_id": {true_card}' not in blob, "扣置牌的 id 不该进视图"
    assert f'"name": {_json(true_ref.name)}' not in blob, "扣置牌的真名不该进视图"
    assert f'"suit": {_json(str(true_ref.suit))}' not in blob, (
        "扣置牌的花色不该进视图"
    )
    assert f'"type": {_json(true_ref.type)}' not in blob, (
        "扣置牌的真牌型不该进视图"
    )
    assert declared in blob, "**声称值**是公开面（CLAIM 步骤只装声称值）"

    raw = json.dumps(
        [fact.model_dump(mode="json") for fact in engine.state.public_surface.recent],
        ensure_ascii=False,
    )
    assert str(true_card) in raw, "前置：公开面缓冲**确实**带着真值（否则这条判据空转）"


def test_a_hidden_step_answer_is_withheld():
    """隐藏步骤的答案**不进视图**（票 05 的三值表：``HIDDEN`` ⇒ 载荷不公开）。

    两条同类事实按**已声明的边界**造：``HIDDEN`` 的一条载荷被裁掉（写点解析出的
    ``answer_hidden``），``CLAIM`` 的一条保留（声称值是公开面）。真实引擎路径另有一条
    `test_a_hidden_step_answer_is_withheld_on_the_product_path`（界曹真【司敌】）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(30, 31, 32),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    hidden_cause = Cause(
        owner=0,
        skill_id="hero.jie_caozhen_mobile.sidi",
        source="HERO_CARD:hero.jie_caozhen_mobile.sidi",
        trigger="jie_caozhen_sidi_predict",
        order=0,
    )
    claim_cause = Cause(
        owner=0,
        skill_id="hero.yuji_mobile.guohuo",
        source="HERO_CARD:hero.yuji_mobile.guohuo",
        trigger="guohuo",
        order=0,
    )
    _record(
        state,
        seq=1,
        actor=0,
        kind=FactKind.ANSWER,
        window="SELECT_OPTION",
        payload=SelectOption(answers_seq=1, choice="seat:1"),
        cause=hidden_cause,
        step="sidi:target",
        answer_hidden=True,
    )
    _record(
        state,
        seq=2,
        actor=0,
        kind=FactKind.ANSWER,
        window="SELECT_OPTION",
        payload=SelectOption(answers_seq=2, choice="过河拆桥"),
        cause=claim_cause,
        step="hero.yuji_mobile.guohuo:name",
    )

    events = project(state, 0).public_history

    hidden, claim = events[0], events[1]
    assert hidden.payload is None, "HIDDEN 步骤的答案值不进视图"
    assert hidden.fact_class == "answer:SelectOption", "事实本身仍可读（谁答了哪一类）"
    assert claim.payload is not None, "CLAIM 步骤的声称值是公开面"
    assert claim.payload.choice == "过河拆桥"


def test_a_hidden_step_answer_is_withheld_on_the_product_path():
    """同一条边界走**真实引擎路径**：界曹真【司敌】的预测目标（用户 Q6 = 只有自己知道）。

    真实局里两条座位步各一条答案事实：``first``（谁被司敌）登记 ``PUBLIC``、``target``
    （预测他会打谁）登记 ``HIDDEN``。故视图里应当**留着前者的座位、丢掉后者的座位**。
    """
    from test_hero_jie_yijiang import (
        _SidiTable,
        _non_delayed_trick,
        _run_sidi,
        _sidi_state,
    )
    from test_hero_jie_yijiang import PACK as SIDI_PACK
    from support import cards_of_type as sidi_cards_of_type

    trick = _non_delayed_trick()
    slash = sidi_cards_of_type(SIDI_PACK, "slash")[0]
    engine = Engine.from_state(_sidi_state(mine=trick, user=slash), seed=21)
    table = _SidiTable(card=trick, predicted=1, predicted_target=2)
    _run_sidi(engine, table, lambda current: bool(current.state.player(0).sidi_marks))

    hidden = [
        fact
        for fact in engine.state.public_surface.recent
        if fact.kind is FactKind.ANSWER and fact.answer_hidden
    ]
    assert [fact.step for fact in hidden] == ["sidi:target"], "写点解析出的边界 = 预测目标步"

    blob = _field_dump(
        project(engine.state, 0, concealed_processing=engine.processing_concealed)
    )
    assert '"choice": "seat:1"' in blob, "「谁被司敌」是公开标记 ⇒ 座位留在公开面"
    assert '"choice": "seat:2"' not in blob, "「预测他会打谁」是 HIDDEN ⇒ 座位不进视图"


def test_a_composite_activation_never_carries_physical_card_ids():
    """预发动的复合答案是**一条** ``ActivateSkill``：``picks`` 一律不下发。

    ``picks`` 在普通发动里是面内位置、在预发动（W4-1 的复合动作）里是**实体牌 id**——
    两者在载荷上不可分，故一律裁掉；``choices`` 是选项 id，照发（声称值走这一格）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(30, 31, 32),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    _record(
        state,
        seq=1,
        actor=0,
        kind=FactKind.ANSWER,
        window="PLAY_PHASE",
        payload=ActivateSkill(
            answers_seq=1,
            skill_id="hero.yuji_mobile.guohuo",
            choices=("过河拆桥",),
            picks=(30,),
        ),
    )

    event = project(state, 0).public_history[-1]

    assert event.payload.picks == (), "实体牌 id 不下发"
    assert event.payload.choices == ("过河拆桥",), "选项 id（声称值）照发"


def test_the_engine_records_the_answered_step_for_option_queries():
    """写点：选项 / 座位询问的答案事实带 ``step``（票 05 的表按它裁视图）。

    真实路径 = 【蛊惑】的目标步（``hero.yuji_mobile.guohuo:target`` 这一问）与质疑步。
    """
    declared = "杀"
    true_card = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "dodge"
    )
    engine, _ = _guohuo_until_doubt(true_card, declared)

    answers = [
        fact
        for fact in engine.state.public_surface.recent
        if fact.kind is FactKind.ANSWER and fact.step is not None
    ]

    assert answers, "前置：这一局确实问过选项步"
    assert {fact.step for fact in answers} >= {
        "hero.yuji_mobile.guohuo:target",
    }, [fact.step for fact in answers]
    assert all(fact.window == "SELECT_OPTION" for fact in answers)


def test_every_hidden_step_is_covered_by_the_two_trimming_paths():
    """边界闭合：票 05 的 ``HIDDEN`` 行**每一条都被本票的两条路径覆盖**。

    答案进视图只有两条路，按步骤形状分：

    - **位置类**（``PickCards`` / ``Arrange``）：答案是面内位置，不携带牌面（映射留在
      引擎里）⇒ 即使照发也不泄露，**不需要裁**；
    - **选项 / 座位类**（``PickOption`` / ``PickSeats``）：答案是座位号 / 选项 id ⇒ 由写点
      解析出的 ``answer_hidden`` 把整个载荷裁掉（它们都经 ``SELECT_OPTION(S)`` 询问，
      ``_answer_step`` 记得下步骤）。

    将来出现第三种形状的 HIDDEN 步（例如 ``PickProvide`` 或手写段机）时这条会红：那时要么
    扩展裁剪面，要么证明它的答案值不装秘密。卡牌侧（``CARD_RESOLUTION_VISIBILITY``）也一并
    钉住：7 行全是 ``PUBLIC``。
    """
    from sgs.skills import CATALOG
    from sgs.skills.flow import (
        Arrange,
        PickCards,
        PickOption,
        PickSeats,
        flow_steps,
    )
    from sgs.skills.preactivation import FLOWS
    from sgs.skills.visibility import CARD_RESOLUTION_VISIBILITY, StepVisibility

    covered = (PickCards, Arrange, PickOption, PickSeats)
    offenders: list[str] = []
    checked = 0
    for skill_id, definition in sorted(CATALOG.items()):
        rows = definition.step_visibility or {}
        hidden = [step for step, value in rows.items() if value is StepVisibility.HIDDEN]
        if not hidden:
            continue
        kinds = {
            step.key: step
            for _timing, flow in FLOWS.get(skill_id, ())
            for step in flow_steps(flow)
        }
        for step in sorted(hidden):
            checked += 1
            found = kinds.get(step)
            if found is None:
                offenders.append(f"{skill_id}:{step}（流水线里找不到这一步）")
            elif not isinstance(found, covered):
                offenders.append(f"{skill_id}:{step}（{type(found).__name__}）")

    assert checked >= 20, f"只查到 {checked} 条 HIDDEN 行；票 05 的表里应有 20 条"
    assert not offenders, (
        "出现了两条裁剪路径都不管的隐藏步骤——它的答案值可能装秘密：\n"
        + "\n".join(offenders)
    )
    assert all(
        value is not StepVisibility.HIDDEN
        for row in CARD_RESOLUTION_VISIBILITY.values()
        for value in row.values()
    ), "卡牌侧的步骤没有 HIDDEN 档；出现即要重新裁这一步的答案面"


def test_every_preactivated_skill_hides_only_card_steps():
    """边界闭合：**预发动**技能的隐藏步骤目前全是 ``PickCards``。

    复合动作把每一步的答案烘进**一条** ``ActivateSkill``：``picks`` 装实体牌 id（裁掉）、
    ``choices`` 装选项 id（照发）。故"隐藏步骤只可能是选牌步"是 ``choices`` 可以照发的
    **前提**。将来新增一条预发动的隐藏**选项 / 座位**步时这条会红——那时要连 ``choices``
    一起裁（或在写点按步骤把答案拆开），不能让它静默流进视图。
    """
    from sgs.skills import CATALOG
    from sgs.skills.flow import PickCards, flow_steps
    from sgs.skills.preactivation import FLOWS, preactivated_skill_ids
    from sgs.skills.visibility import StepVisibility

    offenders: list[str] = []
    for skill_id in sorted(preactivated_skill_ids()):
        definition = CATALOG[skill_id]
        rows = definition.step_visibility or {}
        hidden = {step for step, value in rows.items() if value is StepVisibility.HIDDEN}
        if not hidden:
            continue
        kinds = {
            step.key: step
            for _timing, flow in FLOWS.get(skill_id, ())
            for step in flow_steps(flow)
        }
        for step in sorted(hidden):
            found = kinds.get(step)
            if found is None:
                offenders.append(f"{skill_id}:{step}（流水线里找不到这一步）")
            elif not isinstance(found, PickCards):
                offenders.append(f"{skill_id}:{step}（{type(found).__name__}）")

    assert not offenders, (
        "预发动技能出现了非选牌类的隐藏步骤——复合答案的 choices 可能装秘密：\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# §3 等价 / 有界 / 读点：fold(引擎重放) ≡ 增量维护
# ---------------------------------------------------------------------------

REPLAY_PACK = load_content_pack(deck_profile="standard_ex_160")
REPLAY_SEED = 20261013
REPLAY_PLAYERS = 5


def _play_ai_game() -> Engine:
    engine = Engine.new_game(
        seed=REPLAY_SEED,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=REPLAY_PACK,
        hero_pool=(),
    )
    engine.run(
        [HeuristicPlayer(ai_stream(REPLAY_SEED, seat)) for seat in range(REPLAY_PLAYERS)]
    )
    return engine


def test_the_new_view_fields_are_rebuilt_by_an_engine_replay():
    """``fold(引擎重放) ≡ GameState 增量维护``（同源先例：技能实例 增量 vs ``resolve_instances``）。

    跑一局 → 从记录流重放 → 逐座位投影**整份视图**相等：公开面的两份切片是投影期的纯函数，
    重放重建缓冲后逐字节相同（不是"只有 recent 相等"）。
    """
    engine = _play_ai_game()
    assert engine.finished, "前置：这一局必达终局"

    seed, tape = tape_from_journal(engine.records)
    replay = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=REPLAY_PACK,
        hero_pool=(),
    )
    replay.run([TapePlayer(tape)] * REPLAY_PLAYERS)

    assert replay.state.public_surface == engine.state.public_surface, (
        "前置：缓冲本身逐字段相等（票 04 的判据）"
    )
    for seat in range(REPLAY_PLAYERS):
        hot = project(
            engine.state, seat, concealed_processing=engine.processing_concealed
        )
        cold = project(
            replay.state, seat, concealed_processing=replay.processing_concealed
        )
        assert hot == cold, f"座位 {seat} 的视图没有从重放重建出来"


def test_the_view_history_stays_bounded_and_cheap():
    """有界性：``recent`` 恒 ≤ 32、计数键空间 = 座位对 × 事实类；投影耗时不随局长增长。

    票面要的**实测数字**记在票的 ``## Answer``；这里钉住"有界"与"低价"两条机械判据。
    """
    engine = _play_ai_game()
    state = engine.state
    surface = state.public_surface
    seats = len(state.players)

    assert surface.total > PUBLIC_SURFACE_RECENT_LIMIT * 5, "前置：确实淘汰过"
    view = project(state, 0, concealed_processing=engine.processing_concealed)
    assert len(view.public_history) == PUBLIC_SURFACE_RECENT_LIMIT
    assert len(view.public_counts) <= (seats + 1) ** 2 * len(
        ENGINE_LEVEL_FACT_CLASSES
    ), "计数键空间 = 座位对 × 事实类，与局长无关"

    # 差分判据（机器快慢无关）：新字段的**增量**必须远小于"整次投影"的量级——它是
    # O(K=32) 的常量活，不是随局长增长的活。空缓冲 = 票 04 的基线成本。
    def _median(*, n: int = 200) -> float:
        samples = []
        for _ in range(n):
            started = time.perf_counter()
            project(state, 0, concealed_processing=engine.processing_concealed)
            samples.append(time.perf_counter() - started)
        return statistics.median(samples)

    full = _median()
    saved = state.public_surface
    state.public_surface = PublicSurface()
    baseline = _median()
    state.public_surface = saved

    assert full < 0.005, f"单次投影中位 {full * 1000:.3f} ms（与票 04 同一上界）"
    assert full - baseline < 0.001, (
        f"新字段的增量 {1000 * (full - baseline):.3f} ms 超过 1 ms："
        "投影可能被做成了随局长增长的全量搬运"
    )


def test_the_new_view_fields_only_keep_the_recent_window():
    """有界性的另一半：**堆多少条事实都只留最近 K 条**（不是"这一局恰好够短"）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(30, 31, 32),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    for seq in range(1, 101):
        _record(
            state,
            seq=seq,
            actor=seq % 2,
            targets=(seq % 2,),
            kind=FactKind.ANSWER,
            window="PLAY_PHASE",
            payload=EndPhase(answers_seq=seq),
        )

    view = project(state, 0)

    assert state.public_surface.total == 100
    assert len(view.public_history) == PUBLIC_SURFACE_RECENT_LIMIT
    assert [event.seq for event in view.public_history] == list(
        range(100 - PUBLIC_SURFACE_RECENT_LIMIT + 1, 101)
    ), "留下的必须是**最近** K 条（先入的 68 条已被淘汰，不会进视图）"


def test_every_delta_type_registers_a_public_payload_rule():
    """变化事实的载荷处置是**闭集登记**：新增 delta 不登记即红（与票 04 同一姿态）。"""
    from sgs.state import projection as view
    from test_public_surface import _delta_schema_types

    schema = _delta_schema_types()
    assert len(schema) >= 19, "只枚举到少量 delta 类型；扫描目标可能指错了"
    assert set(view._DELTA_PAYLOAD_RULES) == set(schema), (
        "公开面载荷登记与 delta 类型不同步：\n"
        f"  未登记：{sorted(set(schema) - set(view._DELTA_PAYLOAD_RULES))}\n"
        f"  幽灵条目：{sorted(set(view._DELTA_PAYLOAD_RULES) - set(schema))}"
    )


def test_an_unregistered_delta_type_fails_closed():
    """未登记的类型**直接炸**，不许按"默认照发"静默流进视图。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=(30, 31, 32),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    _record(state, seq=1, payload=SimpleNamespace(delta_type="no_such_delta"))

    with pytest.raises(KeyError):
        project(state, 0)


def test_the_read_point_table_registers_the_new_fields_as_read_by_ticket_07():
    """读点表覆盖：两格都在表里、理由非空、`ai-view` 票 07 起登记为**读**。

    票 06 只让视图能看见（两格登记为不读）；票 07 的身份行为后验是它们的第一读方
    （`public_history` → 逐事件行为类，`public_counts` → 长期拿牌 / 弃牌压力）。
    """
    from sgs.agents import heuristic, identity

    rows = heuristic._AI_READ_POINTS["PlayerView"]
    for field in ("public_counts", "public_history"):
        assert field in rows, f"{field} 没有登记读点"
        read, reason = rows[field]
        assert read is True, f"{field} 票 07 起必须登记为读"
        assert reason.strip(), f"{field} 的理由为空"
    assert "behavior_observations" in rows["public_history"][1]
    assert "pressure_observations" in rows["public_counts"][1]
    # 登记的理由必须点名**真的被启发式接上的那个函数**（不是同名的另一份实现）：
    # 启发式经 `from . import identity` 读它，故模块对象必须是同一个。
    assert heuristic.identity_model is identity
    assert identity.behavior_observations is heuristic.identity_model.behavior_observations
    assert identity.pressure_observations is heuristic.identity_model.pressure_observations


