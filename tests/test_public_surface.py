"""公开面缓冲（ai-view 票 04；spec §2.3 / §2.5）：起因作用域 + 两个写入点 + 有界性。

缝 = **引擎侧的两个写入点**（``Engine.emit_delta`` / ``Engine.submit``）与
``GameState.public_surface``，全部走外部可观测面：

1. **主动技**：SKILL 帧压栈时压一层起因；帧内（含它压出的子帧）的每条变化事实都带
   ``(owner, skill_id, source, 效果编号)``；
2. **被动技**：钩子跑在别人的帧里——帧不换、起因换；【自书】的 ``tag_add`` 与【反馈】
   的选牌答案都归到**那条技能**；
3. **fail-closed**：技能结算内缺起因 ⇒ 引擎直接 raise；用"把作用域层拿掉"的探针证明它
   真的会咬（不是摆设）；
4. **封闭清单**：无起因只允许出现在 :data:`~sgs.state.ENGINE_LEVEL_FACT_CLASSES` 登记的
   事实类上——未登记即红；``_DELTA_SUBJECT_FIELD`` 与 ``sgs.state.deltas`` 双向对齐；
5. **有界性**：``recent`` 恒 ≤ 32 条；聚合计数的键空间 = 座位对 × 事实类；
   ``project()`` 不读缓冲、耗时不变（票 04 不做视图，那是票 06）；
6. **记录流零改动**：缓冲不进记录流（47 条冻结金录像由 ``tools/check_goldens.sh`` 钉住，
   这里再断一次"记录流里没有这个字段"）。

追加要求（用户 2026-11-23 裁定）的**事实面**两条也在这里：
保护性【过河拆桥】——``card_move`` 载荷原样带着 ``from_zone``（判定区 vs 手牌）；
范围伤害——``UseCard.targets`` 是多目标、每个目标的伤害是**各自一条** ``hp_change`` 事实。
"""

from __future__ import annotations

import json
import statistics
import time

import pytest

from sgs.agents import HeuristicPlayer, TapePlayer, tape_from_journal
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, ai_stream
from sgs.skills import (
    ACTIVATE_TRIGGER,
    ACTIVATIONS,
    CATALOG,
    RUNNING_SKILL_KEY,
    instance_key,
    resolve_instances,
)
from sgs.skills.preactivation import FLOWS
from sgs.state import (
    ENGINE_LEVEL_FACT_CLASSES,
    PUBLIC_SURFACE_RECENT_LIMIT,
    ActivateSkill,
    Cause,
    FactKind,
    FrameKind,
    HpChangeDelta,
    MoveReason,
    Pass,
    PublicFact,
    QueryKind,
    SelectCards,
    UseCard,
    Zone,
    ZoneRef,
    delta_subject_seats,
    fact_class,
    project,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")

ZHIHENG = "hero.sunquan.zhiheng"
KUROU = "hero.huanggai.kurou"
ZISHU = "hero.maliang.zishu"
FANKUI = "hero.simayi.fankui"

ME = 1
"""【自书】/【反馈】的持有者座位（与 ``tests/test_hero_sp1.py`` 的座位约定同源）。"""

def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


class _Scripted:
    """最小脚本玩家：``handler`` 先答；答不出时退到"结束阶段 / 放弃 / 第一个候选"。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _run(state, participants, stop, *, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, participants, stop)
    return engine


def _collect_facts(monkeypatch) -> list:
    """把缓冲的**全部**事实收进一个列表（``recent`` 只有 32 条，覆盖测试要看全量）。"""
    import sgs.state as state_module

    facts: list = []
    original = state_module.PublicSurface.record

    def record(self, fact):
        facts.append(fact)
        return original(self, fact)

    monkeypatch.setattr(state_module.PublicSurface, "record", record)
    return facts


# ---------------------------------------------------------------------------
# ① 结构登记：每种 delta 都必须回答"它落到哪个座位"
# ---------------------------------------------------------------------------


def _delta_schema_types() -> dict[str, type]:
    """``sgs.state.deltas`` 里全部 delta 模型：``delta_type`` → 类（与不泄露判据同源的枚举法）。"""
    import sgs.state.deltas as schema
    from pydantic import BaseModel

    out: dict[str, type] = {}
    for name in schema.__all__:
        cls = getattr(schema, name)
        if name == "Delta" or not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        out[cls.model_fields["delta_type"].default] = cls
    return out


def test_every_delta_type_registers_the_field_that_names_its_subject_seat() -> None:
    """``_DELTA_SUBJECT_FIELD`` 与 delta 模型集**双向**对齐（新增 delta 必须先登记）。"""
    from sgs.state.models import _DELTA_SUBJECT_FIELD

    schema_types = _delta_schema_types()
    assert set(_DELTA_SUBJECT_FIELD) == set(schema_types), (
        "公开面的事实落点登记与 delta 类型不同步：\n"
        f"  未登记：{sorted(set(schema_types) - set(_DELTA_SUBJECT_FIELD))}\n"
        f"  幽灵条目：{sorted(set(_DELTA_SUBJECT_FIELD) - set(schema_types))}\n"
        "新增 delta 类型时在 ``sgs.state.models._DELTA_SUBJECT_FIELD`` 登记"
        "『载荷里哪个字段是座位』（没有单一落点写 ``None``），并写明理由。"
    )


def test_an_unregistered_delta_type_fails_closed_instead_of_having_no_landing() -> None:
    """合成一条未登记的 delta ⇒ ``KeyError``（不是静默记一条无落点的事实）。"""
    from pydantic import BaseModel, ConfigDict

    class FakeDelta(BaseModel):
        model_config = ConfigDict(frozen=True)
        delta_type: str = "a_brand_new_delta"

    with pytest.raises(KeyError):
        delta_subject_seats(FakeDelta())


def test_the_subject_seat_is_read_from_the_deltas_own_landing_field() -> None:
    """落点取载荷自己的字段：搬牌读**区坐标的座位**、体力变化读 ``seat``、洗牌没有单一落点。"""
    from sgs.state import DeckShuffleDelta

    assert delta_subject_seats(HpChangeDelta(seat=2, before=4, after=3, amount=1)) == (2,)
    assert delta_subject_seats(_card_move_delta()) == (ME,)
    shuffled = DeckShuffleDelta(cards=(), from_zone=None, to_zone=ZoneRef(Zone.DECK))
    assert delta_subject_seats(shuffled) == (), "洗牌没有单一承受座位"


def _card_move_delta():
    """一条最小 ``card_move``：座位 ``ME`` 摸到一张牌（落点 = 目标区坐标里的座位）。"""
    from sgs.engine import instantiate_cards
    from sgs.state import CardMoveDelta

    card = instantiate_cards(PACK)[_cards("peach")[0]]
    return CardMoveDelta(
        cards=(card,),
        from_zone=ZoneRef(Zone.DECK),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=MoveReason.DRAW,
        owner=ME,
    )


# ---------------------------------------------------------------------------
# ② 主动技：SKILL 帧自带起因，帧内（含它压出的子帧）的事实都归它
# ---------------------------------------------------------------------------


def _kurou_state():
    """座位 0 = 黄盖（【苦肉】），出牌阶段空闲。"""
    slash = _cards("slash")[0]
    return (
        build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(slash,), hero="huanggai"),
                SeatSpec(),
            ],
            deck=_deck_excluding(slash),
            turn_seat=0,
        ),
        slash,
    )


def _activate_kurou(query):
    for action in query.legal_actions:
        if isinstance(action, ActivateSkill) and action.skill_id == KUROU:
            return action
    return None


def test_a_skill_frames_deltas_carry_the_skill_cause_and_its_effect_id(monkeypatch) -> None:
    """【苦肉】：失去体力（子帧）与摸两张（同帧）都带 ``(黄盖, 苦肉, 流水线名, 0)``。

    这条同时钉住"**子帧继承起因**"：``LIFE_DECREASE`` 帧是效果体压出、效果体随即
    ``return "stay"`` 退出的——若起因只挂在函数调用期，这条 ``hp_change`` 就会丢起因。
    """
    facts = _collect_facts(monkeypatch)
    state, _slash = _kurou_state()
    engine = _run(
        state,
        [_Scripted(_activate_kurou), _Scripted()],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    query = engine.pending
    action = _activate_kurou(query)
    assert action is not None, "【苦肉】应在出牌阶段候选里"
    engine.submit(action)
    engine.run([_Scripted(), _Scripted()])

    facts = [fact for fact in facts if fact.cause is not None]
    assert facts, "【苦肉】的效果事实必须带起因"

    hp_loss = next(
        fact
        for fact in facts
        if getattr(fact.payload, "delta_type", None) == "hp_change"
    )
    assert hp_loss.targets == (0,)
    assert hp_loss.cause is not None
    assert (hp_loss.cause.owner, hp_loss.cause.skill_id) == (0, KUROU)
    assert hp_loss.cause.trigger == "kurou", "触发点 = 该技能的流水线名（SkillFlow.name）"
    assert hp_loss.cause.order == 0
    assert hp_loss.cause.instance_key.startswith("0:hero.huanggai.kurou:")

    draws = [
        fact
        for fact in facts
        if fact_class(fact) == "move:DRAW" and fact.targets == (0,)
    ]
    assert draws, "摸两张也是这条技能的效果（同帧事实）"
    assert all(fact.cause.skill_id == KUROU for fact in draws)


# ---------------------------------------------------------------------------
# ③ 被动技：钩子跑在别人的帧里，帧不换、起因换
# ---------------------------------------------------------------------------


def _maliang_state():
    """座位 1 = 马良（【自书】），座位 0 = 当前回合角色（别人的回合）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero="maliang"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    return Engine.from_state(state, seed=7)


def test_a_passive_hook_effect_is_attributed_to_its_skill_instance() -> None:
    """回合外获得牌 ⇒ 【自书】打标签：``tag_add`` 归到 ``(马良, 自书, MOVE_AFTER, 0)``。"""
    engine = _maliang_state()
    peach = _cards("peach")[0]
    engine.move_in_frame(
        None,
        engine.state.refs((peach,)),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=MoveReason.LOSE,
        owner=ME,
        actor=0,
    )

    tags = [
        fact
        for fact in engine.state.public_surface.recent
        if getattr(fact.payload, "delta_type", None) == "tag_add"
    ]
    assert len(tags) == 1, "【自书】① 给这张牌打了一枚标签"
    cause = tags[0].cause
    assert cause is not None
    assert (cause.owner, cause.skill_id) == (ME, ZISHU)
    assert cause.trigger == Timing.MOVE_AFTER.value, "被动技的触发点 = 它挂的时机"
    assert cause.order == 0, "该时机上的第一条钩子"
    instance = next(
        instance
        for instance in resolve_instances(engine.state, include_suppressed=True)
        if instance.owner == ME and instance.skill_id == ZISHU
    )
    assert cause.instance_key == instance_key(instance), (
        "``Cause.instance_key`` 必须与 ``skills.instance_key`` 逐字同形（不是第二份格式）"
    )


class _SkillScripted(_Scripted):
    """技能发动一律同意；选牌询问交给 ``select``（照 ``tests/test_hero_damage_after.py``）。"""

    def __init__(self, select=None, *, activate: bool = True) -> None:
        self._select = select
        self._activate = activate
        super().__init__(self._respond)

    def _respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            if self._activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if query.kind == "SELECT_CARDS" and self._select is not None:
            return self._select(query)
        if query.kind in ("RESPOND", "DODGE", "PEACH"):
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return query.legal_actions[0]


def _damage_frame(state, card: int, *, amount: int, source: int | None):
    channel = state.card(card)
    return {
        "source": source,
        "targets": (1,),
        "card": channel,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": 1,
        "channel": channel,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }


def _fankui_engine():
    """座位 0 打座位 1（司马懿）；来源手里有牌 ⇒ 【反馈】会问"是否发动"再问"拿哪张"。"""
    slash = _cards("slash")[0]
    source_hand = (_cards("peach")[0], _cards("peach")[1])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=source_hand),
            SeatSpec(hero="simayi"),
        ],
        deck=_deck_excluding(slash, *source_hand),
        processing=(slash,),
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(state, slash, amount=1, source=0),
        timing=Timing.DAMAGE_DEAL,
    )
    return engine, slash


def test_a_passive_hooks_step_answer_is_attributed_to_the_asking_skill() -> None:
    """【反馈】的"拿哪张牌"是**钩子体内**问出的步骤答案：起因在提问那一刻定格。

    应答发生在钩子返回、作用域退出**之后**（引擎挂起等输入）；若在 ``submit`` 时现读栈，
    这条答案会缺起因。判据 = 选牌答案的 ``cause`` 是【反馈】、窗口是 ``SELECT_CARDS``。
    """
    engine, _slash = _fankui_engine()
    taken: list[int] = []

    def select(query):
        action = query.legal_actions[0]
        assert isinstance(action, SelectCards)
        taken.append(action.picks[0])
        return action

    participants = [_SkillScripted(), _SkillScripted(select)]
    run_until(
        engine,
        participants,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )

    answers = [
        fact
        for fact in engine.state.public_surface.recent
        if fact.kind is FactKind.ANSWER
    ]
    select_answers = [fact for fact in answers if fact.window == QueryKind.SELECT_CARDS.value]
    assert len(select_answers) == 1, "【反馈】只问一次选牌"
    answer = select_answers[0]
    assert isinstance(answer.payload, SelectCards)
    assert answer.actor == 1
    assert answer.cause is not None
    assert (answer.cause.owner, answer.cause.skill_id) == (1, FANKUI)
    assert answer.cause.trigger == Timing.DAMAGE_AFTER_TAKE.value
    assert answer.cause.order == 0


# ---------------------------------------------------------------------------
# ④ 步骤答案 = 真实动作（不是按 legal_actions 统计）
# ---------------------------------------------------------------------------


def test_the_answer_facts_are_exactly_the_actions_the_engine_accepted(monkeypatch) -> None:
    """答案事实与记录流里 ``accepted=true`` 的动作**一一对应**（转化 / 复合动作也在内）。"""
    facts = _collect_facts(monkeypatch)
    seed = REPLAY_SEED
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=PACK,
        hero_pool=(),
    )
    engine.run(
        [HeuristicPlayer(ai_stream(seed, seat)) for seat in range(REPLAY_PLAYERS)]
    )

    accepted = [
        record.action
        for record in engine.records
        if record.kind == "action" and record.accepted
    ]
    answers = [fact for fact in facts if fact.kind is FactKind.ANSWER]
    assert accepted, "前置：这一局真的接受过动作"
    assert [fact.payload for fact in answers] == accepted, (
        "答案事实必须逐条等于**真实接受的动作**（不是 legal_actions 的统计）"
    )
    assert len(answers) > PUBLIC_SURFACE_RECENT_LIMIT, "前置：全量答案越过最近窗口"


def _zhiheng_engine():
    """座位 0 = 孙权（【制衡】预发动），停在出牌阶段询问。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine = _run(
        state,
        [_Scripted(), _Scripted()],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    return engine, first


def test_a_composite_activation_is_recorded_as_the_real_action_not_a_legal_candidate() -> None:
    """预发动确认提交的是**复合动作**（不在 ``legal_actions`` 里），答案事实原样记它。"""
    engine, card = _zhiheng_engine()
    query = engine.pending
    assert query is not None
    assert ZHIHENG in query.preactivations, "【制衡】已翻预发动：可用性只在清单里"
    action = ActivateSkill(
        skill_id=ZHIHENG, picks=(card,), answers_seq=query.seq
    )
    assert action not in query.legal_actions, "前置：复合动作确实不在候选集合里"

    engine.submit(action)
    answers = [
        fact
        for fact in engine.state.public_surface.recent
        if fact.kind is FactKind.ANSWER
    ]
    assert answers, "提交本身是一条公开事实"
    assert answers[-1].payload == action, "记的是**提交的那条真实动作**，不是候选"
    assert answers[-1].window == QueryKind.PLAY_PHASE.value


# ---------------------------------------------------------------------------
# ⑤ fail-closed：技能结算内缺起因 ⇒ 直接 raise
# ---------------------------------------------------------------------------


def test_a_delta_inside_a_skill_frame_without_a_cause_scope_is_rejected() -> None:
    """把起因层拿掉（模拟归因层坏掉）⇒ 技能帧内发 delta 直接 raise，不是静默无主事实。"""
    state, _slash = _kurou_state()
    engine = Engine.from_state(state, seed=1)
    frame = engine.start_skill(
        ActivateSkill(skill_id=KUROU, answers_seq=1), source=0
    )
    assert frame.kind is FrameKind.SKILL
    assert engine._frame_causes, "前置：压 SKILL 帧时压了一层起因"

    engine._frame_causes.pop()  # 把作用域层拿掉：等价于"该压没压上"

    with pytest.raises(RuntimeError, match="缺起因"):
        engine.emit_delta(HpChangeDelta(seat=0, before=4, after=3, amount=1))


def test_a_hook_body_without_a_cause_scope_is_rejected_too() -> None:
    """钩子回调体（没有 SKILL 帧）同样不许缺起因：``_hook_bodies`` 是独立于起因栈的判据。"""
    engine = _maliang_state()
    engine._hook_bodies = 1  # 模拟"回调正在跑、但起因层没压上"

    with pytest.raises(RuntimeError, match="缺起因"):
        engine.emit_delta(HpChangeDelta(seat=0, before=4, after=3, amount=1))


# ---------------------------------------------------------------------------
# ⑥ 封闭清单：无起因只允许登记过的事实类
# ---------------------------------------------------------------------------


def _effect_is_declared(cause: Cause) -> bool:
    """``(技能, 触发点, 序)`` 必须能在声明面找到同一条（不手写效果名表的反向核）。"""
    definition = CATALOG.get(cause.skill_id)
    if definition is None:
        return False
    if cause.trigger == ACTIVATE_TRIGGER:
        return cause.skill_id in ACTIVATIONS and cause.order == 0
    if cause.trigger.startswith("seam:"):
        kind = cause.trigger.split(":", 1)[1]
        peers = tuple(seam for seam in definition.seams if seam.kind.value == kind)
        return 0 <= cause.order < len(peers)
    if any(flow.name == cause.trigger for _timing, flow in FLOWS.get(cause.skill_id, ())):
        return cause.order == 0
    peers = tuple(hook for hook in definition.hooks if hook.timing.value == cause.trigger)
    return 0 <= cause.order < len(peers)


REPLAY_SEED = 20261013
"""5 人局、武将开启、启发式对局（实机录像同形；本文件里既跑全 AI 也跑磁带重放）。"""
REPLAY_PLAYERS = 5


def _play_ai_game() -> Engine:
    engine = Engine.new_game(
        seed=REPLAY_SEED,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=PACK,
        hero_pool=(),
    )
    engine.run(
        [HeuristicPlayer(ai_stream(REPLAY_SEED, seat)) for seat in range(REPLAY_PLAYERS)]
    )
    return engine


def test_a_full_ai_game_replays_to_the_same_public_surface() -> None:
    """跑一局 → 从记录流重放：公开面缓冲**逐字段相等**（回放可重建，spec §2.2 的判据）。"""
    engine = _play_ai_game()
    assert engine.finished, "前置：这一局必达终局"

    seed, tape = tape_from_journal(engine.records)
    replay = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=PACK,
        hero_pool=(),
    )
    replay.run([TapePlayer(tape)] * REPLAY_PLAYERS)

    assert replay.journal() == engine.journal(), "记录流逐字节相等"
    assert replay.state.public_surface == engine.state.public_surface, (
        "公开面缓冲必须能从引擎重放重建"
    )
    assert replay.state.public_surface.total > 0


def test_every_public_fact_of_a_replayed_recording_has_a_declared_cause_or_a_registered_class(
    monkeypatch,
) -> None:
    """归因覆盖：实机录像重放后，每条事实要么有**已声明**的起因，要么是登记的引擎级事实类。

    同时用记录流里的真实录像作输入：``tape_from_journal`` → 磁带重放（ticket 03 的同一
    条路），而不是"只在内存里跑一局"。
    """
    engine = _play_ai_game()
    seed, tape = tape_from_journal(engine.records)
    replay = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=REPLAY_PLAYERS),
        pack=PACK,
        hero_pool=(),
    )
    facts = _collect_facts(monkeypatch)
    replay.run([TapePlayer(tape)] * REPLAY_PLAYERS)

    assert facts, "前置：这一局真的产生了公开事实"
    unregistered = sorted(
        {
            fact_class(fact)
            for fact in facts
            if fact.cause is None and fact_class(fact) not in ENGINE_LEVEL_FACT_CLASSES
        }
    )
    assert not unregistered, (
        "无起因的事实类未登记进 ``ENGINE_LEVEL_FACT_CLASSES``：\n"
        f"  {unregistered}\n"
        "技能结算内的事实本该带起因；若这是新的引擎级事件，登记它并写清理由。"
    )
    undeclared = sorted(
        {
            fact.cause.skill_id
            for fact in facts
            if fact.cause is not None and not _effect_is_declared(fact.cause)
        }
    )
    assert not undeclared, (
        f"起因的效果编号在声明面找不到同一条（未声明的技能 / 触发点 / 序）：{undeclared}"
    )


def test_the_in_skill_guard_is_independent_of_the_cause_stack(monkeypatch) -> None:
    """技能结算内的每条 delta 都带起因；且 SKILL 帧在场时起因的 ``skill_id`` 就是它。

    探针在读**结算栈**（与起因栈互相独立）：这条与引擎里的 fail-closed 守卫是同一条判据的
    外部读法，防止守卫被改坏而测试仍是绿的。
    """
    problems: list[str] = []
    original = Engine.emit_delta

    def emit(self, delta, *, frame_id=0, depth=0):
        if self._skill_in_flight():
            cause = self._active_cause()
            if cause is None:
                problems.append(f"技能结算内缺起因：{getattr(delta, 'delta_type', delta)}")
            elif self._hook_causes:
                running = self.stack.top.data.get(RUNNING_SKILL_KEY)
                if running is not None and cause.skill_id != running:
                    problems.append(
                        f"钩子起因与正在跑的技能不符：{cause.skill_id} vs {running}"
                    )
            else:
                skills = [
                    frame.data.get("skill_id")
                    for frame in self.stack.frames
                    if frame.kind is FrameKind.SKILL
                ]
                if skills and cause.skill_id not in skills:
                    problems.append(
                        f"帧起因与 SKILL 帧不符：{cause.skill_id} vs {skills}"
                    )
        return original(self, delta, frame_id=frame_id, depth=depth)

    monkeypatch.setattr(Engine, "emit_delta", emit)
    engine = _play_ai_game()
    assert engine.finished
    assert not problems, "归因覆盖失败：\n" + "\n".join(problems[:10])


# ---------------------------------------------------------------------------
# ⑦ 座位变动：归因跟**发出时的所有者**，不是事后位置
# ---------------------------------------------------------------------------


def test_attribution_is_captured_when_the_scope_opens_not_recomputed_later() -> None:
    """起因在**进作用域那一刻**定格：此后"座位 / 来源"怎么变，已经发出的事实都不跟着漂。

    今天没有"技能改座位"的效果（牌面里不存在座位对调），故按票面要求构造等价局面：
    压 SKILL 帧后 ① 改当前回合角色 / 自然指针（`effect` 类实现可能读的"事后位置"），
    ② 把该座位的武将撤掉（让 `resolve_instances` 事后**查不到**这条实例）。
    若归因是事后现查的，这两步会让它漂成别人 / 漂成回落值；捕获式实现不变。
    """
    state, _slash = _kurou_state()
    engine = Engine.from_state(state, seed=1)
    engine.start_skill(ActivateSkill(skill_id=KUROU, answers_seq=1), source=0)

    engine.state.turn_seat = 1  # ① 事后位置变了
    engine.state.next_seat = 1
    engine.state.player(0).hero = None  # ② 事后查实例查不到了
    engine.emit_delta(HpChangeDelta(seat=1, before=4, after=3, amount=1))

    fact = engine.state.public_surface.recent[-1]
    assert fact.cause is not None
    assert fact.cause.owner == 0, "归因跟发出那一刻的所有者，不跟事后位置"
    assert fact.cause.instance_key.startswith("0:hero.huanggai.kurou:"), (
        "实例身份也是压栈时定格的，不依事后仍能解析出实例"
    )
    assert fact.targets == (1,), "涉及座位仍是载荷里那个"


# ---------------------------------------------------------------------------
# ⑧ 有界性：recent 恒 ≤ 32；计数的键空间 = 座位对 × 事实类
# ---------------------------------------------------------------------------


def test_the_surface_stays_bounded_over_a_whole_game() -> None:
    """一局下来 ``recent`` 恒 32 条、``total`` 继续涨，聚合计数不随局长增长。"""
    engine = _play_ai_game()
    surface = engine.state.public_surface
    seats = len(engine.state.players)

    assert len(surface.recent) == PUBLIC_SURFACE_RECENT_LIMIT
    assert surface.total > PUBLIC_SURFACE_RECENT_LIMIT * 5, "前置：确实淘汰过"
    # 键空间上限：座位对 × 事实类。事实类 = delta 类 + 8 种搬牌原因 + 动作构造子。
    kinds = len(_delta_schema_types()) + len(MoveReason) + len(_action_kinds())
    assert len(surface.counts) <= (seats + 1) ** 2 * kinds
    assert all(entry.count >= 1 for entry in surface.counts)
    assert sum(entry.count for entry in surface.counts) >= surface.total


def test_the_counter_keeps_one_row_per_seat_pair_and_fact_class() -> None:
    """聚合计数按 ``(发起座位, 涉及座位, 事实类)`` 合并：同一键只留一行、次数累加。"""
    from sgs.state import PublicSurface

    surface = PublicSurface()
    cause = Cause(
        owner=1,
        skill_id=KUROU,
        source="HERO_CARD:hero.huanggai.kurou",
        trigger="kurou",
        order=0,
    )
    for seq in (1, 2):
        surface.record(
            PublicFact(
                seq=seq,
                kind=FactKind.DELTA,
                actor=1,
                targets=(2,),
                payload=HpChangeDelta(seat=2, before=4, after=3, amount=1),
                cause=cause,
            )
        )
    surface.record(
        PublicFact(
            seq=3,
            kind=FactKind.DELTA,
            actor=2,
            targets=(2,),
            payload=HpChangeDelta(seat=2, before=3, after=2, amount=1),
            cause=None,
        )
    )

    assert surface.total == 3
    assert [
        (entry.owner, entry.seat, entry.kind, entry.count) for entry in surface.counts
    ] == [
        (1, 2, "delta:hp_change", 2),
        (2, 2, "delta:hp_change", 1),
    ], "同一键只留一行（无起因的按发起座位记账）"


def _action_kinds() -> dict[str, type]:
    """``sgs.state.protocol`` 里全部**行动构造子**：``kind`` → 类（新增构造子会进这里）。

    按**模块成员**扫而不按 ``__all__``：几个构造子（``Reforge`` / ``Distribute`` /
    ``SelectOptions``）是经 ``sgs.state`` 再导出、不在 ``protocol.__all__`` 里，按导出表扫
    会漏。判据 = 有 ``answers_seq`` 字段（四个行动构造子基类的共同字段）。
    """
    import sgs.state.protocol as protocol
    from pydantic import BaseModel

    out: dict[str, type] = {}
    for value in vars(protocol).values():
        if not (isinstance(value, type) and issubclass(value, BaseModel)):
            continue
        if "answers_seq" not in value.model_fields or "kind" not in value.model_fields:
            continue
        out[value.model_fields["kind"].default] = value
    return out


def test_the_engine_level_registry_names_every_delta_and_move_class() -> None:
    """``ENGINE_LEVEL_FACT_CLASSES`` 的 ``delta:*`` / ``move:*`` 半边与 delta / 搬牌原因对齐。"""
    delta_classes = {
        f"delta:{delta_type}"
        for delta_type in _delta_schema_types()
        if delta_type != "card_move"
    }
    move_classes = {f"move:{reason.value}" for reason in MoveReason}
    registered = {
        entry for entry in ENGINE_LEVEL_FACT_CLASSES if not entry.startswith("answer:")
    }
    assert registered == delta_classes | move_classes, (
        "无起因的变化事实清单与 delta / MoveReason 不同步：\n"
        f"  未登记：{sorted((delta_classes | move_classes) - registered)}\n"
        f"  幽灵条目：{sorted(registered - (delta_classes | move_classes))}"
    )


def test_the_engine_level_registry_names_every_action_constructor() -> None:
    """``ENGINE_LEVEL_FACT_CLASSES`` 的 ``answer:*`` 半边与行动构造子集**双向**对齐。"""
    action_kinds = _action_kinds()
    assert len(action_kinds) >= 10, "只枚举到少量行动构造子；扫描目标可能指错了"
    registered = {
        entry.split(":", 1)[1]
        for entry in ENGINE_LEVEL_FACT_CLASSES
        if entry.startswith("answer:")
    }
    assert registered == set(action_kinds), (
        "无起因的步骤答案清单与行动构造子不同步：\n"
        f"  未登记：{sorted(set(action_kinds) - registered)}\n"
        f"  幽灵条目：{sorted(registered - set(action_kinds))}"
    )


def test_the_raw_surface_stays_out_of_the_view_and_the_projection_stays_cheap() -> None:
    """票 04 **不进视图**：``PlayerView`` 没有"原始缓冲"字段、``project()`` 保持低价。

    票 06 之后 ``project()`` 会读缓冲（两份**有界**切片进 ``public_counts`` /
    ``public_history``，见 ``tests/test_public_view.py``），但**缓冲本身**仍不是视图字段：
    ``model_dump`` 里没有 ``public_surface``（记录流 / 网页载荷因此逐字节不变）。
    这里的耗时判据保留为**上界**（< 5 ms），票 06 的实测数字记在那张票的 ``## Answer``。
    """
    engine = _play_ai_game()
    state = engine.state
    views = [project(state, seat) for seat in range(len(state.players))]
    assert all("public_surface" not in view.model_dump() for view in views)

    samples = []
    for _ in range(200):
        started = time.perf_counter()
        project(state, 0)
        samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    assert median < 0.005, f"单次投影中位 {median * 1000:.3f} ms（票面口径：不显著变长）"


def test_the_surface_never_reaches_the_record_stream() -> None:
    """缓冲**不进记录流**：journal 里没有这个字段（47 条冻结金录像由金录像检查单独钉住）。"""
    engine = _play_ai_game()
    assert "public_surface" not in engine.journal()
    assert "public_surface" not in json.dumps(
        [record.model_dump(mode="json") for record in engine.records],
        ensure_ascii=False,
        sort_keys=True,
    )


def test_the_endgame_teardown_is_never_stamped_with_a_suspended_hook_cause() -> None:
    """``finish()`` 可以从钩子体内被调用（短摸直接判平局）⇒ 收尾搬牌**不许**盖钩子的起因。

    收尾是"结算结束之后"的引擎动作（``_teardown_processing`` 的文档写"归属为 ``None``"）；
    只清帧起因是不够的——钩子那一层还开着。
    """
    from sgs.engine.game import Outcome

    engine = _maliang_state()
    card = _cards("peach")[0]
    engine.state.deck.remove(card)  # 让这张牌**只**在处理区（牌守恒：一张牌一个区）
    engine.state.processing.append(card)
    engine.enter_hook_cause(
        Cause(
            owner=ME,
            skill_id=ZISHU,
            source=f"HERO_CARD:{ZISHU}",
            trigger=Timing.MOVE_AFTER.value,
            order=0,
        )
    )
    engine.pending_outcome = Outcome(result="draw", winner=None, reason="both_decks_empty")
    engine._check_seq = engine._last_seq

    engine.finish()

    teardown = [
        fact
        for fact in engine.state.public_surface.recent
        if getattr(fact.payload, "delta_type", None) == "card_move"
        and fact.payload.from_zone.zone is Zone.PROCESSING
    ]
    assert teardown, "终局收尾把处理区的牌置入弃牌堆（有事实）"
    assert teardown[-1].cause is None, "收尾搬牌不属于任何技能"
    assert teardown[-1].actor is None, "收尾时栈已清空（没有帧来源）"
    engine.leave_hook_cause()  # 与 enter 对账（钩子的 finally 会做同一件事）


# ---------------------------------------------------------------------------
# ⑨ 追加要求的事实面：拆掉的区 / 多目标逐点伤害
# ---------------------------------------------------------------------------


def _dismantle_engine(*, judge: bool):
    """座位 0 拆座位 1 的判定区 / 手牌；其余窗口一律放弃。"""
    dismantle = _cards("dismantlement")[0]
    hand_card = _cards("slash")[0]
    judge_card = _cards("indulgence")[0]
    seats = [SeatSpec(hand=(dismantle,)), SeatSpec(hand=(hand_card,)), SeatSpec()]
    if judge:
        seats[1] = SeatSpec(hand=(hand_card,), judge=(judge_card,))
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=_deck_excluding(dismantle, hand_card, judge_card),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=11)

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type == "dismantlement":
                    return action
            return None
        if query.kind == QueryKind.SELECT_CARDS:
            # 有判定区那张时**选它**：面内位置 = 槽位下标（隐藏槽只有位置、没有牌面）。
            index = next(
                (
                    position
                    for position, slot in enumerate(query.slots)
                    if slot.ref.zone is Zone.JUDGE
                ),
                0,
            )
            for action in query.legal_actions:
                if isinstance(action, SelectCards) and action.picks == (index,):
                    return action
            return None
        if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return None

    run_until(
        engine,
        [_Scripted(driver) for _ in seats],
        lambda e: _use_settled(e, "dismantlement"),
    )
    return engine


def _use_settled(engine: Engine, card_type: str) -> bool:
    return any(
        record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.type == card_type for card in record.cards)
        for record in engine.records
    )


def _moves_from(engine: Engine, zone: Zone) -> list:
    return [
        fact
        for fact in engine.state.public_surface.recent
        if getattr(fact.payload, "delta_type", None) == "card_move"
        and fact.payload.from_zone.zone is zone
    ]


def test_the_fact_surface_tells_a_judge_zone_dismantle_from_a_hand_dismantle() -> None:
    """拆判定区 vs 拆手牌：``card_move`` 载荷的 ``from_zone`` 不同（票 04 追加要求 1）。"""
    judge_engine = _dismantle_engine(judge=True)
    judge_moves = _moves_from(judge_engine, Zone.JUDGE)
    assert judge_moves, "拆判定区的移动事实必须带着 from_zone=JUDGE"
    judge_move = judge_moves[0].payload
    assert judge_move.to_zone.zone is Zone.DISCARD
    assert judge_move.from_zone.seat == 1, "判定区的主人在区坐标里（不是按牌名 + 目标猜的）"
    assert judge_moves[0].targets == (1,), "事实落点 = 被拆的那个座位"

    hand_engine = _dismantle_engine(judge=False)
    hand_moves = [
        fact for fact in _moves_from(hand_engine, Zone.HAND) if fact.payload.from_zone.seat == 1
    ]
    assert [fact.payload.reason for fact in hand_moves] == [MoveReason.LOSE]
    assert hand_moves[0].targets == (1,)
    assert not _moves_from(hand_engine, Zone.JUDGE), "没有判定区的局不产生 JUDGE 移动"


def _barbarian_engine():
    """座位 0 用【南蛮入侵】指定所有人；两个目标都交不出【杀】⇒ 各受 1 点伤害。"""
    barbarian = _cards("barbarian_invasion")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(barbarian),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=13)

    def play(query):
        for action in query.legal_actions:
            if isinstance(action, UseCard) and action.card.type == "barbarian_invasion":
                return action
        return None

    def respond(query):
        if query.kind in ("RESPOND", "DODGE", "PEACH"):
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return None

    run_until(
        engine,
        [_Scripted(play), _Scripted(respond), _Scripted(respond)],
        lambda e: _use_settled(e, "barbarian_invasion"),
    )
    return engine


def test_each_target_of_an_aoe_is_its_own_fact_with_its_own_damage() -> None:
    """【南蛮入侵】的每个目标各是一条 ``hp_change`` 事实；使用动作本身带全部目标（追加要求 2）。"""
    engine = _barbarian_engine()
    surface = engine.state.public_surface

    use_answers = [
        fact
        for fact in surface.recent
        if fact.kind is FactKind.ANSWER
        and isinstance(fact.payload, UseCard)
        and fact.payload.card.type == "barbarian_invasion"
    ]
    assert len(use_answers) == 1
    assert use_answers[0].targets == (1, 2), "使用动作带着这一发的全部目标"

    damage = [
        fact
        for fact in surface.recent
        if getattr(fact.payload, "delta_type", None) == "hp_change"
        and fact.payload.after < fact.payload.before
    ]
    assert [fact.targets for fact in damage] == [(1,), (2,)], (
        "每个目标各一条伤害事实（不是一条聚合的 AoE），且按目标结算序"
    )
    assert [fact.actor for fact in damage] == [0, 0], (
        "伤害事实的发起座位 = 所在帧的使用者（这一发可归因到谁）"
    )
    assert [
        (entry.owner, entry.seat, entry.kind) for entry in surface.counts
        if entry.kind == "delta:hp_change"
    ] == [(0, 1, "delta:hp_change"), (0, 2, "delta:hp_change")]
    assert [engine.state.player(seat).hp for seat in (1, 2)] == [3, 3]
