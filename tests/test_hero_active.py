"""A 族：出牌阶段**主动技候选**（票 04）。

落点与票 04 §验收一致，全部走**外部缝**：

- ``legal_actions_for(PLAY_PHASE)`` 的候选集合（形状 / 载荷 / 付不起 / 用过后不再进池）；
- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的记录流与终局状态（效果 / 伤害来源 /
  牌移动 / 回复夹上限）；
- 组合上限闸的**候选形态**与**顺序降级**（``SelectionSpec(sequential=True)``）。

不测 ``ACTIVATIONS`` / 候选回调的内部结构；行为只经候选、询问、delta 流观测。
【苦肉】不在 16 名名单（票 12 的 46 条计数不含它），故"裸发动 + 失去体力"用**注入的
假定义**验形状（``tests/test_hero_skills.py`` 的先例）。
"""

from __future__ import annotations

from functools import partial
from types import MappingProxyType

import pytest

from sgs.data import Gender, load_content_pack
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierPoint,
    check_targets,
    legal_actions_for,
)
from sgs.engine import Engine, IllegalActionError
from sgs.resolution import Frame
from sgs.skills import (
    CATALOG,
    CANDIDATE_CAP,
    SkillDef,
    SkillType,
    activate_skill,
    pure_virtual_card,
    reset_phase_skill_state,
)
from sgs.skills import active as active_module
from sgs.skills import catalog as catalog_module
from sgs.skills.preativation import preactivation_for
from sgs.state import (
    ActivateSkill,
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    SelectOption,
    Zone,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_turns, run_until

PACK = load_content_pack(deck_profile="standard_108")

ZHIHENG = "hero.sunquan.zhiheng"
RENDE = "hero.liubei.rende"
FANJIAN = "hero.zhouyu.fanjian"
QINGNANG = "hero.huatuo.qingnang"
LIJIAN = "hero.diaochan.lijian"
JIEYIN = "hero.sunshangxiang.jieyin"
KUROU = "hero.huanggai.kurou"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _hearts(count: int) -> tuple[int, ...]:
    return tuple(
        card_id for card_id, card in enumerate(PACK.deck.cards) if card.suit == "heart"
    )[:count]


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _offers(actions, skill_id: str) -> list[ActivateSkill]:
    return [
        action
        for action in actions
        if isinstance(action, ActivateSkill) and action.skill_id == skill_id
    ]


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _decline(query):
    """响应窗口一律放弃：随机摸到的【无懈可击】/【闪】不会把被测链改道。"""
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE):
        return _find(query, lambda action: isinstance(action, Pass))
    return None


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _run(state, players, stop, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _skill_frame_popped(engine) -> bool:
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.SKILL
        for record in engine.records
    )


def _play_query_count(engine, skill_id: str) -> int:
    """记录流里**下发过该技能预发动清单**的 PLAY_PHASE 询问数（W4-2 起可用性只在清单里）。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.PLAY_PHASE.value
        and skill_id in record.query.preactivations
    )


def _composite(query, skill_id: str, *, picks=(), choices=()) -> ActivateSkill:
    """一个**复合动作**：预发动确认时提交的 ``ActivateSkill``（不要求是候选）。"""
    return ActivateSkill(
        skill_id=skill_id,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def _manifest(query, skill_id: str):
    """这条 PLAY_PHASE 询问随附的预发动清单（W4-2 起全部 A 族主动技都走它）。"""
    return tuple(query.preactivations.get(skill_id, ()))


def _pool_options(step) -> tuple[str, ...]:
    """清单一步的候选（所有 ``when`` 行摊平、去重保序）。"""
    seen: list[str] = []
    for row in step.pool:
        for option in row.options:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _row_for(step, *when: str) -> tuple[str, ...]:
    """清单一步里 ``when == when`` 那一行的候选；找不到 ⇒ 空元组。"""
    for row in step.pool:
        if tuple(row.when) == tuple(when):
            return tuple(row.options)
    return ()


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# 制衡（孙权）：弃任意张（手牌 + 装备区）→ 摸等量
#
# 票 04：本技能是【制衡】**只有一步**的实例（``PickCards(HAND ∪ EQUIP, 1, n)``）；
# W4-2：这一步从"发动后的逐步询问"改成**预发动清单**——骨架候选从 ``PLAY_PHASE`` 删掉，
# 可用性只由 ``preactivations`` 表达，确认 = 一个复合动作。
# ---------------------------------------------------------------------------


def _zhiheng_state(hand: tuple[int, ...], equip: tuple[int, ...] = ()):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, equip=equip, hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(*hand, *equip),
        turn_seat=0,
    )


def _park_at_play_phase(state, *, seed: int = 1):
    """把引擎开到第一条出牌阶段询问（清单就在这条询问上）。"""
    engine = _run(
        state,
        [Scripted(_decline), Scripted(_decline)],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE.value,
        seed=seed,
    )
    return engine


def test_zhiheng_manifest_replaces_the_skeleton_however_many_cards():
    """W4-2：8 张手牌时骨架候选消失、清单仍在 —— 询问体量不随张数增长。

    旧断言（票 04）是"候选恒一条骨架"；预发动之后骨架被删，可用性只剩清单：
    区域 / 张数 / 候选都在清单里表达，``legal_actions`` 里不再有它。
    这条取代旧形状的 ``test_zhiheng_offers_every_nonempty_subset_of_hand_and_equipment``
    （票 04 改为骨架候选，W4-2 再改为预发动清单；2ⁿ 枚举两次都没回来）。
    """
    hand = tuple(_cards("slash")[:4]) + tuple(_cards("dodge")[:4])
    assert len(hand) == 8, "手牌 8 张：旧形状 2⁸ − 1 = 255 条候选"
    engine = _park_at_play_phase(_zhiheng_state(hand))
    query = engine.pending

    assert not _offers(query.legal_actions, ZHIHENG), "已翻预发动：骨架候选已从 legal_actions 删掉"
    steps = _manifest(query, ZHIHENG)
    assert [step.name for step in steps] == ["card"], "单步清单"
    assert steps[0].zones == (Zone.HAND, Zone.EQUIP)
    assert (steps[0].min, steps[0].max) == (1, 10), (
        "张数区间由清单表达（上限 = 摸牌阶段后的真实池：起始 8 + 摸 2）"
    )
    assert len(_pool_options(steps[0])) == 10
    assert len(query.legal_actions) <= CANDIDATE_CAP, "整段询问不随张数增长"


def test_zhiheng_manifest_size_is_constant_across_hand_sizes():
    """清单的**步骤数**与手牌 / 装备区张数无关（8 张与 2 张都只有一步）。"""
    hand = tuple(_cards("slash")[:8])
    small = _manifest(_park_at_play_phase(_zhiheng_state(hand[:2])).pending, ZHIHENG)
    large = _manifest(_park_at_play_phase(_zhiheng_state(hand)).pending, ZHIHENG)

    assert len(small) == len(large) == 1


def test_zhiheng_is_not_offered_without_any_card_to_discard():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    assert preactivation_for(state, 0, ZHIHENG) == (), "付不起 ⇒ 清单渲不出来 ⇒ 此刻不可用"


def _zhiheng_driver(*, pick: tuple[int, ...] | None = None, cancel: bool = False):
    """确认预发动：在 ``PLAY_PHASE`` 直接提交复合动作（``picks`` = 要弃的实体牌 id）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if cancel:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, ZHIHENG, picks=pick or ())
        return _decline(query)

    return driver


def test_zhiheng_step_declares_any_number_from_one_up_to_the_whole_pool():
    """票 04："任意张"的上下限由**步骤声明**表达；W4-2 起同一个声明渲染成清单。

    上限是执行器按真实池解析出的 ``hi=None``（不是写死的大哨兵）⇒ 清单里的 ``max``
    与引擎的选择面同源；下限 ``lo=1`` ⇒ 空选不是合法提交。
    """
    hand = tuple(_cards("slash")[:3])
    equip = (_cards("jueying")[0],)
    engine = _park_at_play_phase(_zhiheng_state(hand, equip))

    (step,) = _manifest(engine.pending, ZHIHENG)

    assert step.zones == (Zone.HAND, Zone.EQUIP)
    assert step.min == 1, "下限 1：0 张不是合法提交"
    assert step.max == len(hand) + len(equip) + 2, (
        "上限 = 全部可选牌（起始 3 + 1 张装备 + 摸牌阶段 2）"
    )
    options = set(_pool_options(step))
    assert {str(card_id) for card_id in (*hand, *equip)} <= options, "本人手牌与装备区都在候选里"
    assert len(options) == len(hand) + len(equip) + 2


def test_zhiheng_discards_by_source_zone_and_draws_the_same_count():
    """复合动作一次提交：选出的牌按来源区弃置，再摸**等量**的牌。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    equip = _cards("jueying")[0]
    state = _zhiheng_state((first, second), (equip,))

    engine = _run(
        state,
        [Scripted(_zhiheng_driver(pick=(first, equip))), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert first in engine.state.discard and equip in engine.state.discard
    assert second in engine.state.player(0).hand, "没被选中的牌留在手里"
    assert not engine.state.player(0).equip, "装备区那张按来源区弃掉"
    assert ("hand", "discard", "DISCARD") in _moves_of(engine, first)
    assert ("equip", "discard", "DISCARD") in _moves_of(engine, equip)
    # 起始 2 张 + 摸牌阶段 2 张 − 制衡弃 1 张手牌 + 制衡摸 2 张 = 5。
    assert len(engine.state.player(0).hand) == 5, "摸牌数与弃置数相等（弃 2 摸 2）"


def test_zhiheng_cancel_leaves_the_table_untouched_and_the_skill_available():
    """取消 = **接入层**退出本地预发动，一个 action 都不提交（引擎零记录、零副作用）。

    判据：停在出牌阶段那一刻（= 取消的时点）清单已在（同一阶段随时可再确认）；记录流里
    没有 SKILL 帧、没有牌移动；手牌 / 装备区 / 弃牌堆原样。取消本身由
    ``tests/test_web.py`` 与 ``ui_smoke.mjs`` 钉住"不提交任何 action"。
    """
    card = _cards("slash")[0]
    equip = _cards("jueying")[0]
    state = _zhiheng_state((card,), (equip,))

    engine = _park_at_play_phase(state)
    player = engine.state.player(0)

    assert _manifest(engine.pending, ZHIHENG), "出牌阶段询问上清单一直在（可随时确认）"
    assert not _skill_frame_popped(engine), "没提交 ⇒ 没有 SKILL 帧"
    assert card in player.hand and equip in player.equip, "选择只收集、未提交才生效"
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert len(player.hand) == 3, "没有制衡摸牌（起始 1 + 摸牌阶段 2）"
    assert not [
        record
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and isinstance(record.action, ActivateSkill)
    ], "取消（不提交）⇒ 记录流里没有任何 ActivateSkill"


def test_the_skill_owner_reaches_the_runtime_query_of_a_remaining_step():
    """`SelectCardsQuery.skill_id` 这个本地通道仍有**读取方**：反间的第二步仍是逐步询问。

    票 05 加的字段 ``exclude=True``、不进记录流，故只能读**运行期**的 query 对象。
    W4-2 之后【制衡】/【仁德】的选牌步都进了复合动作（不再发询问），但换应答者的第二段
    （反间的挑牌）照旧逐步问，技能归属必须照旧发出去。
    """
    state, _hearts = _fanjian_state()
    driver = _fanjian_driver(suit="spade")

    engine = _run(state, [Scripted(driver), Scripted(driver)], _skill_frame_popped)

    cards_queries = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_CARDS.value
    ]
    assert cards_queries, "反间的挑牌步发过询问"
    assert cards_queries[0].skill_id == FANJIAN, "选择询问带上了发动它的技能归属"


def test_a_used_active_skill_disappears_from_the_next_play_phase_query():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    def zhiheng(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _composite(query, ZHIHENG, picks=(first,))
        return _decline(query)

    engine = _run(state, [Scripted(zhiheng), Scripted(_decline)], _skill_frame_popped)

    assert _play_query_count(engine, ZHIHENG) == 1, (
        "第一次询问的 preactivations 里有它；发动后重新询问里不再有它（用完即不再进池）"
    )


def test_reset_phase_skill_state_clears_the_counters_at_play_phase_start():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    state.skill_state["0:hero.sunquan.zhiheng:HERO_CARD:hero.sunquan.zhiheng"] = 1

    run_turns(Engine.from_state(state, seed=1), [RecordingPlayer(), RecordingPlayer()], turns=1)

    assert state.skill_state == {}, "本出牌阶段开始时复位（票 04 §要做什么 2）"


def test_reset_phase_skill_state_is_a_public_reset_point():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="sunquan"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    state.skill_state["0:x:HERO_CARD:x"] = 1

    reset_phase_skill_state(state)

    assert state.skill_state == {}


# ---------------------------------------------------------------------------
# 【苦肉】的形状：裸发动（max_picks == 0）+ 失去体力（LIFE_LOSE_* 不解封）
# ---------------------------------------------------------------------------


def test_a_bare_activation_candidate_and_handler_run_the_shape(monkeypatch):
    """裸发动：候选载荷全空，发动后回调直接跑（本例 = 失去 1 点体力）。"""

    def offers(state, ctx, current):
        if ctx.scope is not CandidateScope.PLAY or ctx.owner != ctx.subject:
            return current
        return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))

    def activate(host, frame):
        seat = frame.data["source"]
        host.apply_hp_change(frame, seat, host.state.player(seat).hp - 1)
        return None

    definition = SkillDef(
        skill_id=KUROU,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, offers),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, KUROU: definition}),
    )
    monkeypatch.setattr(
        active_module,
        "ACTIVATIONS",
        MappingProxyType({**active_module.ACTIVATIONS, KUROU: activate}),
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4, hero="huanggai"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    bare = [
        action
        for action in _offers(_play_candidates(state), KUROU)
        if action.picks == () and action.choices == ()
    ]
    assert bare, "max_picks == 0 ⇒ 裸发动候选（无载荷）"

    def kurou(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == KUROU,
            )
        return _decline(query)

    engine = _run(state, [Scripted(kurou), Scripted(_decline)], _skill_frame_popped)

    assert engine.state.player(0).hp == 3, "失去体力走 apply_hp 原语"
    assert not any(
        record.kind == "frame" and record.frame_kind is FrameKind.LIFE_LOSE
        for record in engine.records
    ), "LIFE_LOSE_* 不解封"
    assert not any(
        record.kind == "timing" and record.timing.value.startswith("LIFE_LOSE")
        for record in engine.records
    )


def test_activate_skill_fails_closed_for_an_unregistered_id():
    with pytest.raises(NotImplementedError):
        activate_skill(None, Frame(kind=FrameKind.SKILL, data={"skill_id": "no.such.skill"}))


# ---------------------------------------------------------------------------
# 仁德（刘备）：给任意张手牌 → 一名其他角色；本阶段首次给到第 2 张回复 1 点
#
# 票 05：本技能是 SkillFlow 的第四个消费者（前三个：【青囊】票 02、【结姻】票 03、
# 【制衡】票 04），也是**唯一一条没有"出牌阶段限一次"**的（规则 976）：流水线传
# ``once_per_phase=False``，``_can_offer(..., once_per_phase=False)``，取消后同一阶段
# 仍可再发动。旧形状按用户裁定不设组合闸（非空手牌子集 × 目标全枚举，手牌 8 张约
# 1800 条候选，用来换"点座位即提交"的交互）；票 05 换成骨架候选 + 两步询问，交互随之
# 变成**选任意张手牌 → 点一名其他角色 → 确认**（用户已知情并接受）。
# ---------------------------------------------------------------------------


def _rende_state(
    hand: tuple[int, ...],
    *,
    owner_hp: int = 3,
    owner_hp_max: int = 4,
    targets: int = 1,
):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=owner_hp, hp_max=owner_hp_max, hand=hand, hero="liubei"),
            *[SeatSpec() for _ in range(targets)],
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )


def test_rende_manifest_replaces_the_skeleton():
    """W4-2：票 05 的骨架候选删掉，可用性只由两步清单表达（选任意张 → 一名其他角色）。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second), targets=2)

    engine = _park_at_play_phase(state)
    query = engine.pending

    assert not _offers(query.legal_actions, RENDE), "骨架候选已删"
    steps = _manifest(query, RENDE)
    assert [step.name for step in steps] == ["card", "seat"]
    assert steps[0].zones == (Zone.HAND,)
    assert (steps[0].min, steps[0].max) == (1, 2 + 2), "任意张（摸牌阶段后 4 张）"
    assert set(_pool_options(steps[1])) == {"seat:1", "seat:2"}, "目标 = 一名其他角色"


def test_rende_manifest_is_constant_however_many_cards_and_targets():
    """清单的步骤数 / 结构不随张数、目标数增长（旧形状是二维相乘的候选枚举）。

    这条取代旧的 ``test_rende_offers_picks_beyond_the_old_cap_so_the_ui_can_commit_in_one_step``
    （票 05 起载荷烘焙形状连同 `ui.manual` 一起不再有生产者；W4-2 起连骨架也不再出现）。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="liubei"), *[SeatSpec() for _ in range(7)]],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    assert (2 ** len(hand) - 1) * 7 > CANDIDATE_CAP, "旧形状必须超闸（否则本用例证明不了什么）"

    engine = _park_at_play_phase(state)
    steps = _manifest(engine.pending, RENDE)

    assert [step.name for step in steps] == ["card", "seat"], "恒两步"
    assert len(_pool_options(steps[1])) == 7, "目标候选 = 七名其他角色"
    assert len(engine.pending.legal_actions) <= CANDIDATE_CAP, "整段询问不随张数增长"


def _rende_driver(*, pick=None, target: int = 1):
    """确认预发动：复合动作（``picks`` = 交出的手牌 id，``choices`` = 目标座位）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if pick is None:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, RENDE, picks=pick, choices=(f"seat:{target}",))
        return _decline(query)

    return driver


def test_rende_asks_the_cards_then_the_target():
    """两步清单（``card`` → ``seat``）：一个复合动作一次交出去，本人应答的前缀不再发问。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))

    engine = _run(
        state,
        [Scripted(_rende_driver(pick=(first, second))), Scripted(_decline)],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert "SELECT_CARDS" not in asked and "SELECT_OPTION" not in asked, (
        "清单由本人填完 ⇒ 引擎不再逐步发问"
    )
    accepted = [
        record.action
        for record in engine.records
        if record.kind == "action" and record.accepted
    ]
    assert any(
        isinstance(action, ActivateSkill)
        and action.skill_id == RENDE
        and action.picks == (first, second)
        and action.choices == ("seat:1",)
        for action in accepted
    ), "确认 = 一条复合 ActivateSkill"


def test_rende_gives_the_chosen_cards_to_the_target_in_one_step():
    """一次发动 = 一次移动：三张牌在提交点一并交给目标（不再逐张问）。"""
    hand = tuple(_cards("slash")[:4])
    chosen = hand[:3]
    state = _rende_state(hand, owner_hp=4, owner_hp_max=4)

    engine = _run(
        state, [Scripted(_rende_driver(pick=chosen)), Scripted(_decline)], _skill_frame_popped
    )

    assert set(engine.state.player(1).hand) == set(chosen), "三张一次交出"
    assert not set(chosen) & set(engine.state.player(0).hand), "给出的牌离开自己的手牌"
    put_moves = [
        {card.card_id for card in record.cards}
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone.zone.value == "hand"
        and record.to_zone.zone.value == "hand"
        and record.reason.value == "PUT"
    ]
    assert set(chosen) in put_moves, "移动原因是 PUT（交给，不是弃置）"


def test_rende_is_not_offered_without_hand_cards_or_without_a_target():
    no_hand = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="liubei"), SeatSpec()],
        deck=_deck_excluding(),
        turn_seat=0,
    )
    no_target = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="liubei")],
        deck=_deck_excluding(),
        turn_seat=0,
    )

    assert preactivation_for(no_hand, 0, RENDE) == (), "没有手牌 ⇒ 清单渲不出来"
    assert preactivation_for(no_target, 0, RENDE) == (), "没有其他角色 ⇒ 目标步为空 ⇒ 没有清单"


def test_rende_recovers_when_two_cards_are_given_for_the_first_time():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))

    engine = _run(
        state,
        [Scripted(_rende_driver(pick=(first, second))), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert engine.state.player(0).hp == 4, "首次给到第 2 张回复 1 点"
    assert set(engine.state.player(1).hand) == {first, second}, "两张手牌交给目标"
    assert first not in engine.state.player(0).hand


def test_rende_does_not_recover_when_only_one_card_is_given():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))

    engine = _run(
        state, [Scripted(_rende_driver(pick=(first,))), Scripted(_decline)], _skill_frame_popped
    )

    assert engine.state.player(0).hp == 3, "只给 1 张 ⇒ 不回复"
    assert engine.state.player(1).hand == [first]


def _skill_frames_popped(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "pop"
        and record.frame_kind is FrameKind.SKILL
    )


def _rende_sequence_driver(plan: list[tuple[int, ...]]):
    """按 ``plan`` **逐次发动**【仁德】：第 i 次把 ``plan[i]`` 那组手牌交给座位 1。

    用来验"授予量由多次发动**累计**"这条规则（单次发动的驱动器 `_rende_driver` 盖不住）。
    每次确认 = 一条复合动作（``once_per_phase=False`` ⇒ 同一阶段可以再来一次）。
    """
    remaining = list(plan)

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if not remaining:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(
                query, RENDE, picks=remaining.pop(0), choices=("seat:1",)
            )
        return _decline(query)

    return driver


def test_rende_recovers_when_the_running_total_reaches_two_across_activations():
    """**规则 976 的口径是"本阶段累计"**：给 1 张、再给 1 张 ⇒ **第二次发动**时回血。

    旧实现只看**单次发动**的张数（`given < 2` ⇒ 不回），1+1 永远不回血 —— 这正是用户实机
    验收报回来的那处瑕疵（`../acceptance.md` §A）。
    """
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))

    engine = _run(
        state,
        [Scripted(_rende_sequence_driver([(first,), (second,)])), Scripted(_decline)],
        lambda e: _skill_frames_popped(e) >= 2,
    )

    assert set(engine.state.player(1).hand) == {first, second}, "两次各给 1 张"
    assert engine.state.player(0).hp == 4, "累计到第 2 张的那一次发动回 1 点血"


def test_rende_recovers_on_the_activation_that_pushes_the_total_past_two():
    """1 张 + 3 张：**第二次**发动时累计跨过 2 ⇒ 回血（不是第一次、也不是按单次张数）。"""
    hand = tuple((_cards("slash") + _cards("dodge"))[:4])
    state = _rende_state(hand)

    engine = _run(
        state,
        [Scripted(_rende_sequence_driver([hand[:1], hand[1:]])), Scripted(_decline)],
        lambda e: _skill_frames_popped(e) >= 2,
    )

    assert set(engine.state.player(1).hand) == set(hand), "1 + 3 张都交出去了"
    assert engine.state.player(0).hp == 4, "第二次发动把累计推过 2 ⇒ 回血"


def test_rende_recovers_at_most_once_per_phase():
    """累计达到 2 之后，本阶段再给多少都不算"首次"⇒ 只回一次。"""
    hand = tuple((_cards("slash") + _cards("dodge"))[:3])
    state = _rende_state(hand, owner_hp=2)

    engine = _run(
        state,
        [Scripted(_rende_sequence_driver([hand[:1], hand[1:2], hand[2:]])), Scripted(_decline)],
        lambda e: _skill_frames_popped(e) >= 3,
    )

    assert set(engine.state.player(1).hand) == set(hand), "三次都给成了"
    assert engine.state.player(0).hp == 3, "1+1 时回一次（2→3）；第三次再给也不回"


def _rende_offered_after_cancel(engine) -> bool:
    """取消之后**同一出牌阶段**又挂起了带【仁德】清单的 PLAY_PHASE 询问。"""
    if not _skill_frame_popped(engine):
        return False
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == QueryKind.PLAY_PHASE.value
        and RENDE in pending.preactivations
    )


def test_rende_has_no_once_per_phase_limit():
    """**规则 976**：没有"出牌阶段限一次"——同一出牌阶段可以给第二次。"""
    first, second, third, fourth = (_cards("slash") + _cards("dodge"))[:4]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hp_max=4, hand=(first, second, third, fourth), hero="liubei"),
            SeatSpec(),
        ],
        deck=_deck_excluding(first, second, third, fourth),
        turn_seat=0,
    )
    plan = [(first, second), (third, fourth)]

    def give(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if not plan:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, RENDE, picks=plan.pop(0), choices=("seat:1",))
        return _decline(query)

    engine = _run(state, [Scripted(give), Scripted(_decline)], lambda e: _skill_frames_popped(e) >= 2)

    assert set(engine.state.player(1).hand) == {first, second, third, fourth}, "两次都给成了"
    assert engine.state.player(0).hp == 3, "第 2 张回复 1 点（2 → 3），第 4 张不再回复"
    assert _play_query_count(engine, RENDE) >= 2, "发动过之后仍回到池里（没有限一次）"


def test_rende_cancel_leaves_the_table_untouched_and_the_skill_available():
    """取消 = 不提交；非法复合动作 = 被同一校验器拒掉，两者都**零状态改动**。

    预发动下"逐步取消"不存在了：本人填完清单后一条复合动作提交。这里钉住的是它的反面——
    没填完 / 填错时引擎拒绝并原样保留询问（同一阶段还能重来）。
    """
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))
    engine = _park_at_play_phase(state)
    query = engine.pending
    baseline = (
        tuple(engine.state.player(0).hand),
        tuple(engine.state.player(1).hand),
        engine.state.player(0).hp,
        tuple(engine.state.discard),
    )

    assert _manifest(query, RENDE), "清单在（可随时确认）"
    with pytest.raises(IllegalActionError):
        engine.submit(_composite(query, RENDE, picks=(), choices=("seat:1",)))

    assert (
        tuple(engine.state.player(0).hand),
        tuple(engine.state.player(1).hand),
        engine.state.player(0).hp,
        tuple(engine.state.discard),
    ) == baseline, "非法复合动作零状态改动"
    assert engine.pending is query and RENDE in engine.pending.preactivations
    player = engine.state.player(0)
    assert first in player.hand and second in player.hand, "选择只收集、提交才生效"


def test_rende_a_rejected_confirmation_does_not_spend_the_first_recovery():
    """被拒的确认不落 ``_RECOVERED``：紧接着给到第 2 张仍回复（失败零副作用）。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = _rende_state((first, second))

    def liubei(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if RENDE not in query.preactivations:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, RENDE, picks=(first, second), choices=("seat:1",))
        return _decline(query)

    engine = _run(state, [Scripted(liubei), Scripted(_decline)], _skill_frame_popped)

    assert set(engine.state.player(1).hand) == {first, second}, "给成了"
    assert engine.state.player(0).hp == 4, "首次给到第 2 张 ⇒ 回 1 点"


# ---------------------------------------------------------------------------
# 反间（周瑜）：目标选花色 + 获得并展示你的一张手牌 + 条件伤害
# ---------------------------------------------------------------------------


def _fanjian_driver(
    *, suit: str, target: int = 1, card_pick: int = 0, cancel_at: str | None = None
):
    """按**预发动流程**应答：座位 0 在出牌阶段确认第一段（选谁）；目标逐步答花色 / 挑牌。

    同一个驱动器给两个座位用：座位 0 只在 PLAY_PHASE 里提交复合动作（``choices = seat:N``），
    座位 1 只在步 2/3 的询问上应答 —— 这正是"选择权在目标"的形状（票 06）。
    ``cancel_at="seat"`` = **本地取消**第一段（不提交任何 action）。
    """

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if cancel_at == "seat":
                return _find(query, lambda action: action.kind == "EndPhase")
            if FANJIAN not in query.preactivations:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, FANJIAN, choices=(f"seat:{target}",))
        if query.kind == QueryKind.SELECT_OPTION:
            if cancel_at == "suit":
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query,
                lambda action: isinstance(action, SelectOption) and action.choice == suit,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            if cancel_at == "card":
                return _find(query, lambda action: isinstance(action, Pass))
            return _find(
                query,
                lambda action: isinstance(action, SelectCards)
                and action.picks == (card_pick,),
            )
        return _decline(query)

    return driver


def _fanjian_state():
    hearts = _hearts(2)
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hearts, hero="zhouyu"), SeatSpec(hp=4, hp_max=4)],
        deck=_deck_excluding(*hearts),
        turn_seat=0,
    ), hearts


def test_fanjian_first_segment_is_the_manifest_only():
    """W4-2：第一段（选谁）进清单；骨架候选删除。第二段（猜花色 / 挑牌）**不进**清单。"""
    heart = _hearts(1)[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(heart,), hero="zhouyu"), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(heart),
        turn_seat=0,
    )

    engine = _park_at_play_phase(state)
    query = engine.pending

    assert not _offers(query.legal_actions, FANJIAN), "第一段已翻预发动：骨架候选已删"
    steps = _manifest(query, FANJIAN)
    assert [step.name for step in steps] == ["seat"], "清单**只到**换应答者的那一步为止"
    assert set(_pool_options(steps[0])) == {"seat:1", "seat:2"}


def test_fanjian_does_not_need_the_target_to_hold_cards():
    heart = _hearts(1)[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(heart,), hero="zhouyu"), SeatSpec()],
        deck=_deck_excluding(heart),
        turn_seat=0,
    )

    assert preactivation_for(state, 0, FANJIAN), (
        "展示的是**发动者的**手牌 ⇒ 目标无手牌不是边界（票 04 §验收 4）"
    )


def test_fanjian_a_different_suit_deals_one_damage_from_the_activator():
    state, hearts = _fanjian_state()
    driver = _fanjian_driver(suit="spade")

    engine = _run(state, [Scripted(driver), Scripted(driver)], _skill_frame_popped)

    assert engine.state.player(1).hp == 3, "花色不同 ⇒ 目标受到周瑜造成的 1 点伤害"
    assert len(engine.state.player(1).hand) == 1, "目标获得了那一张手牌"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 0, "伤害来源是发动者"
    revealed = [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone.zone.value == "hand"
        and record.to_zone.zone.value == "processing"
        for card in record.cards
    ]
    assert len(revealed) == 1
    paths = [(from_zone, to_zone) for from_zone, to_zone, _reason in _moves_of(engine, revealed[0])]
    assert ("hand", "processing") in paths, "展示 = 先亮进公开的处理区"
    assert ("processing", "hand") in paths, "再从处理区进目标手牌（获得）"


def test_fanjian_a_matching_suit_deals_no_damage():
    state, _hearts_used = _fanjian_state()
    driver = _fanjian_driver(suit="heart")

    engine = _run(state, [Scripted(driver), Scripted(driver)], _skill_frame_popped)

    assert engine.state.player(1).hp == 4, "花色相同 ⇒ 不受伤"
    assert len(engine.state.player(1).hand) == 1, "但牌照常被获得"


def _fanjian_queries(engine):
    return [record.query for record in engine.records if record.kind == "query"]


def test_fanjian_first_segment_in_the_manifest_then_the_target_answers():
    """清单只到第一段；确认后**目标**逐步答花色 / 挑牌（三段序列 + 每问的 ``actor``）。"""
    state, hearts = _fanjian_state()
    driver = _fanjian_driver(suit="spade")

    engine = _run(state, [Scripted(driver), Scripted(driver)], _skill_frame_popped)

    queries = _fanjian_queries(engine)
    # 周瑜的【英姿】在摸牌阶段先发了一条 SKILL_ACTIVATION ⇒ 从"带【反间】清单的那条
    # PLAY_PHASE"开始数三步。
    start = next(
        index
        for index, query in enumerate(queries)
        if query.kind == "PLAY_PHASE" and FANJIAN in query.preactivations
    )
    seat_steps = _manifest(queries[start], FANJIAN)
    suit_q, card_q = queries[start + 1], queries[start + 2]
    assert len(seat_steps) == 1 and seat_steps[0].name == "seat", (
        "第一段（选谁）在清单里，且清单**止于**换应答者的那一步"
    )
    assert [q.kind for q in (queries[start], suit_q, card_q)] == [
        "PLAY_PHASE",
        "SELECT_OPTION",
        "SELECT_CARDS",
    ], "确认复合动作 →（目标）猜花色 →（目标）挑一张"
    assert suit_q.skill_id == "fanjian:suit" and suit_q.actor == 1, "步 2 由**目标**应答"
    assert card_q.actor == 1, "步 3 也由**目标**应答"
    assert {slot.ref.seat for slot in card_q.slots} == {0}, (
        "但选择面铺的是**发动者**的手牌（PickCards.actor 与 .face 第一次分开）"
    )
    assert len(card_q.slots) >= len(hearts), "发动者的手牌（含开局那两张红桃）就是这一问的选择面"
    assert all(slot.card is None for slot in card_q.slots), (
        "**盲挑**：槽位只有位置、没有牌面 —— 官方口径是「获得之后才展示」，"
        "挑的时候不亮牌（迁移前 selection_face 的缺省就是隐藏槽）"
    )


def _fanjian_offered_after_cancel(engine) -> bool:
    """取消之后**同一出牌阶段**又挂起了带【反间】清单的 PLAY_PHASE 询问。"""
    if not _skill_frame_popped(engine):
        return False
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == QueryKind.PLAY_PHASE.value
        and FANJIAN in pending.preactivations
    )


def _run_fanjian_cancel(cancel_at: str):
    state, hearts = _fanjian_state()
    driver = _fanjian_driver(suit="spade", cancel_at=cancel_at)

    if cancel_at == "seat":
        # 第一段是本地预发动：取消 = 一个 action 都不提交 ⇒ 停在出牌阶段那一刻。
        engine = _park_at_play_phase(state)
        return hearts, engine, None
    engine = _run(
        state,
        [Scripted(driver), Scripted(driver)],
        _fanjian_offered_after_cancel,
    )
    return hearts, engine, driver


@pytest.mark.parametrize("cancel_at", ["seat", "suit", "card"])
def test_fanjian_cancel_leaves_the_table_untouched_and_the_skill_available(cancel_at):
    """任何一段取消 ⇒ 没有牌移动、没有伤害、同一阶段还能再发动。

    ``seat`` 那档取消的是**第一段的本地预发动**（不提交、引擎看不到）；``suit`` / ``card``
    仍是第二段逐步询问里的 ``Pass``（换应答者的第二段不套预发动，保留原有取消出口）。
    """
    hearts, engine, _driver = _run_fanjian_cancel(cancel_at)

    assert set(hearts) <= set(engine.state.player(0).hand), "选择只收集：那几张牌仍在手里"
    assert not engine.state.player(1).hand, "一张都没交出去"
    assert engine.state.player(1).hp == 4, "目标没有受伤"
    assert engine.state.processing == [], "处理区为空 —— 没有亮过牌"
    if cancel_at == "seat":
        assert _manifest(engine.pending, FANJIAN), "本地取消后清单仍在（同一阶段可再确认）"
    else:
        assert _fanjian_offered_after_cancel(engine), "取消不消耗次数：同一阶段仍可再发动"


# ---------------------------------------------------------------------------
# 青囊（华佗）：弃 1 张手牌 → 一名已受伤角色回复 1 点（含自己）
#
# 票 02：形状与【离间】同源 —— 候选恒一条骨架，"弃哪张 / 治谁"由发动后的步骤
# 流水线问（``SELECT_CARDS`` → ``SELECT_OPTION``）。旧的"过闸降级"分支已删除，
# 同一个技能不再有两种交互形状。
# ---------------------------------------------------------------------------


def _qingnang_state(*, target_hp: int = 2):
    """发动者 0（华佗，手牌 1 张【杀】）+ 座位 1（默认已受伤）。"""
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="huatuo"), SeatSpec(hp=target_hp, hp_max=4)],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _qingnang_driver(*, target: int = 1, pick: int):
    """确认预发动：复合动作（``picks`` = 弃掉的手牌 id，``choices`` = 目标座位）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if QINGNANG not in query.preactivations:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, QINGNANG, picks=(pick,), choices=(f"seat:{target}",))
        return _decline(query)

    return driver


def test_qingnang_target_pool_is_the_wounded_characters_including_self():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hp_max=4, hand=(card,), hero="huatuo"),
            SeatSpec(hp=4, hp_max=4),
            SeatSpec(hp=2, hp_max=4),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )

    engine = _park_at_play_phase(state)
    steps = _manifest(engine.pending, QINGNANG)

    assert set(_pool_options(steps[1])) == {"seat:0", "seat:2"}, (
        "目标池 = 已受伤角色（含自己），未受伤者出池"
    )


def test_qingnang_asks_the_card_then_the_target():
    """两步清单（``card`` → ``seat``）一次提交：弃哪张 / 治谁都在复合动作里。"""
    card, state = _qingnang_state()

    engine = _park_at_play_phase(state)
    steps = _manifest(engine.pending, QINGNANG)

    assert [step.name for step in steps] == ["card", "seat"]
    assert set(_pool_options(steps[0])) >= {
        str(card_id) for card_id in engine.state.player(0).hand
    }, "步 1 铺的是自己的手牌"
    assert set(_pool_options(steps[1])) == {"seat:1"}, "步 2 的目标 = 一名已受伤角色"


def test_qingnang_discards_one_hand_card_and_heals_the_wounded_target():
    card, state = _qingnang_state()

    engine = _run(
        state,
        [Scripted(_qingnang_driver(target=1, pick=card)), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    assert ("hand", "discard", "DISCARD") in _moves_of(engine, card)


def test_qingnang_cancel_leaves_the_table_untouched_and_the_skill_available():
    """取消 = 不提交；列表里仍挂着带【青囊】清单的同一阶段询问，零状态改动。"""
    card, state = _qingnang_state()

    engine = _park_at_play_phase(state)
    player = engine.state.player(0)

    assert QINGNANG in engine.pending.preactivations, "同一阶段随时可再确认"
    assert card in player.hand, "成本还在手里"
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert engine.state.player(1).hp == 2, "目标没有被治疗"
    assert not _skill_frame_popped(engine), "取消（不提交）⇒ 没有 SKILL 帧"


# ---------------------------------------------------------------------------
# 离间（貂蝉）：弃 1 张牌 + 两名男性 → 视为其中一名对另一名使用【决斗】
# ---------------------------------------------------------------------------


def test_lijian_requires_two_male_characters():
    card = _cards("slash")[0]
    one_male = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    two_males = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )

    only_one = _park_at_play_phase(one_male)
    enough = _park_at_play_phase(two_males)

    assert LIJIAN not in only_one.pending.preactivations, (
        "只有一名男性 / 两名女性 ⇒ 没有合法有序对 ⇒ 清单渲不出来"
    )
    assert LIJIAN in enough.pending.preactivations, "够条件 ⇒ 三步清单"


def test_lijian_manifest_is_constant_however_many_cards_and_males():
    """W4-2：三步清单的**步骤数 / 结构**不随牌数、男性数增长（旧形状是牌 × 有序男性对）。

    这条取代旧的"过闸降级"断言：降级路径对【离间】不再有消费者。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="diaochan", gender=Gender.FEMALE), *_wounded_males(7)],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    assert len(hand) * (7 * 6) > CANDIDATE_CAP, "旧形状必须过闸（否则本用例证明不了什么）"

    steps = preactivation_for(state, 0, LIJIAN)

    assert [step.name for step in steps] == ["card", "user", "victim"], "恒三步"
    assert len(_pool_options(steps[1])) == 7, "使用者候选 = 七名男性"
    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "出牌阶段询问仍不超闸值"


def _lijian_state():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(card,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
        ],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _lijian_driver(*, user: int, victim: int, pick: int):
    """确认预发动：复合动作（``picks`` = 弃掉的牌、``choices`` = 使用者 → 目标）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if LIJIAN not in query.preactivations:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(
                query,
                LIJIAN,
                picks=(pick,),
                choices=(f"seat:{user}", f"seat:{victim}"),
            )
        return _decline(query)

    return driver


def test_lijian_manifest_orders_the_card_then_the_two_males():
    card, state = _lijian_state()

    steps = preactivation_for(state, 0, LIJIAN)
    assert [step.name for step in steps] == ["card", "user", "victim"]
    assert set(_pool_options(steps[0])) == {str(card)}, "步 1 = 一张牌（手牌 ∪ 装备区）"
    assert set(_pool_options(steps[1])) == {"seat:1", "seat:2"}, "步 2 = 一名男性"
    assert _row_for(steps[2], str(card), "seat:1") == ("seat:2",), (
        "步 3：使用者不能当自己的目标（行键 = 前序两步的答案）"
    )
    assert _row_for(steps[2], str(card), "seat:2") == ("seat:1",)


def test_lijian_composite_runs_the_duel_with_the_chosen_user_as_source():
    card, state = _lijian_state()

    engine = _run(
        state,
        [
            Scripted(_lijian_driver(user=1, victim=2, pick=card)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert "SELECT_CARDS" not in asked and "SELECT_OPTION" not in asked, (
        "三步由本人填完 ⇒ 一条复合动作，不再逐步发问"
    )
    assert card in engine.state.discard, "成本弃掉"
    assert engine.state.player(2).hp == 3, "目标 2 没打出【杀】⇒ 受到 1 点伤害"
    assert engine.state.player(1).hp == 4, "使用者座位 1 不受伤（决斗败方是座位 2）"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 1, "伤害来源随被选中的男性使用者（不是貂蝉）"


def test_lijian_user_is_the_chosen_male_not_the_activator():
    """把两次点选反过来 ⇒ 使用者与败方互换，伤害来源随之（不是固定座位）。"""
    card, state = _lijian_state()

    engine = _run(
        state,
        [
            Scripted(_lijian_driver(user=2, victim=1, pick=card)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    assert card in engine.state.discard
    assert engine.state.player(1).hp == 3, "这次败方是座位 1（座位 2 是使用者）"
    damage = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]
    assert damage and damage[-1].source == 2


def test_lijian_may_pay_with_an_equipment_card():
    """成本可以是**装备区**的牌：清单步 1 的候选 = 手牌 ∪ 装备区。"""
    equip = _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(equip,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(equip),
        turn_seat=0,
    )

    steps = preactivation_for(state, 0, LIJIAN)
    assert str(equip) in _pool_options(steps[0]), "装备区的牌进了清单候选"

    engine = _run(
        state,
        [
            Scripted(_lijian_driver(user=1, victim=2, pick=equip)),
            Scripted(_decline),
            Scripted(_decline),
        ],
        _skill_frame_popped,
    )

    assert equip in engine.state.discard, "装备区的牌被弃置"
    assert engine.state.player(2).hp == 3, "技能照常结算"


# ---------------------------------------------------------------------------
# 取消（票 01 / S2）：中途退出不搬家产、不消耗次数
# ---------------------------------------------------------------------------


def test_lijian_cancel_leaves_the_table_untouched_and_the_skill_available():
    """取消 = 不提交复合动作：手牌 / 装备 / 弃牌堆原样，清单仍在同一阶段询问上。"""
    card, state = _lijian_state()

    engine = _park_at_play_phase(state)
    player = engine.state.player(0)

    assert _manifest(engine.pending, LIJIAN), "清单在 ⇒ 同一阶段还能再发动"
    assert card in player.hand, "成本仍在手里（没提交 ⇒ 不动）"
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert engine.state.player(1).hp == 4 and engine.state.player(2).hp == 4, "没人受伤"
    assert not _skill_frame_popped(engine), "取消（不提交）⇒ 没有 SKILL 帧"


def test_lijian_uses_a_pure_virtual_duel_card():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    card = pure_virtual_card(state, "duel")

    assert card.card_id < 0 and card.type == "duel" and card.name == "决斗"
    assert card.suit is None and card.rank is None and card.color is None


# ---------------------------------------------------------------------------
# 通用步骤流水线（票 02）：提炼出的形状**能表达**【离间】的三步
#
# 本票不迁【离间】（它保持票 01 的手写三步），所以这里把提炼物当作**新技能**注入
# （复用【苦肉】的空槽位，与上面 KUROU 的先例同姿态），只钉【青囊】用不到的那两维：
# 有序两次选座，且第二次的池由第一次的结果（与真牌同源的 ``check_targets``）决定。
# ---------------------------------------------------------------------------


def _inject_flow_skill(monkeypatch, flow) -> None:
    """把一条 :class:`~sgs.skills.active.SkillFlow` 当作【苦肉】槽位上的真技能注入。"""

    def offers(state_, ctx, current):
        if ctx.scope is not CandidateScope.PLAY or ctx.owner != ctx.subject:
            return current
        return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))

    definition = SkillDef(
        skill_id=KUROU,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, offers),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, KUROU: definition}),
    )
    monkeypatch.setattr(
        active_module,
        "ACTIVATIONS",
        MappingProxyType(
            {**active_module.ACTIVATIONS, KUROU: partial(active_module.run_flow, flow=flow)}
        ),
    )


def _flow_two_male_state():
    card = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(card,), hero="huanggai"), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(card),
        turn_seat=0,
    )
    return card, state


def _flow_driver(seats: tuple[int, int]):
    """点技能 → 弃 1 张牌 → 依次点 ``seats`` 那两个座位。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _find(
                query,
                lambda action: isinstance(action, ActivateSkill) and action.skill_id == KUROU,
            )
        if query.kind == QueryKind.SELECT_CARDS:
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            wanted = {
                "kurou:seat:0": f"seat:{seats[0]}",
                "kurou:seat:1": f"seat:{seats[1]}",
            }.get(query.skill_id)
            if wanted is not None:
                return _find(
                    query,
                    lambda action: isinstance(action, SelectOption) and action.choice == wanted,
                )
        return _decline(query)

    return driver


def _flow_seat_queries(engine):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_OPTION.value
    ]


def test_the_flow_shape_can_express_a_dependent_two_seat_pick(monkeypatch):
    """``pool(state, frame, owner, chosen)`` 拿得到第一次的结果 ⇒ 能照真牌 ``check_targets`` 收窄。"""
    _card, state = _flow_two_male_state()
    duel = pure_virtual_card(state, "duel")
    seen: list[tuple[int, ...]] = []

    def pool(state_, frame, owner, chosen):
        """与 ``_lijian_partners`` 同形：使用者一侧不设限，受害者一侧走真牌同一条链。"""
        del frame, owner
        seen.append(tuple(chosen))
        if not chosen:
            return (1, 2)
        return tuple(seat for seat in (1, 2) if seat in check_targets(state_, chosen[0], duel))

    _inject_flow_skill(
        monkeypatch,
        active_module.SkillFlow(
            name="probe_dependent_seats",
            steps=(
                active_module.PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="card"),
                active_module.PickSeats(lo=2, hi=2, pool=pool, key="seat"),
            ),
            effect=lambda host, frame, answers: None,
        ),
    )

    engine = _run(
        state,
        [Scripted(_flow_driver((1, 2))), Scripted(_decline), Scripted(_decline)],
        _skill_frame_popped,
    )

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert asked[:4] == ["PLAY_PHASE", "SELECT_CARDS", "SELECT_OPTION", "SELECT_OPTION"], (
        "①选牌 → ②选第一次座位 → ③选第二次座位（有序两次都是同一个 PickSeats 步骤）"
    )
    seats = _flow_seat_queries(engine)
    assert [query.actor for query in seats] == [0, 0], "两步都由发动者应答（actor 缺省）"
    assert (1,) in seen, "第二次的池建在第一次的结果之上（check_targets 据此收窄）"
    assert seats[0].options == ("seat:1", "seat:2"), "第一次：全部合法男性"
    assert seats[1].options == ("seat:2",), "第二次：check_targets 只留下座位 2"


def test_the_flow_shape_ordered_pick_never_re_offers_the_chosen_seat(monkeypatch):
    """``ordered=True``：同一步里第二问的池自动排除第一问已选的座位（design §3 N3）。"""
    _card, state = _flow_two_male_state()

    def pool(state_, frame, owner, chosen):
        del state_, frame, owner, chosen
        return (1, 2)  # 刻意不做任何收窄：排除只能来自 ordered

    _inject_flow_skill(
        monkeypatch,
        active_module.SkillFlow(
            name="probe_ordered_seats",
            steps=(active_module.PickSeats(lo=2, hi=2, ordered=True, pool=pool, key="seat"),),
            effect=lambda host, frame, answers: None,
        ),
    )

    engine = _run(
        state,
        [Scripted(_flow_driver((1, 2))), Scripted(_decline), Scripted(_decline)],
        _skill_frame_popped,
    )

    seats = _flow_seat_queries(engine)
    assert [query.options for query in seats] == [("seat:1", "seat:2"), ("seat:2",)], (
        "第一问全池，第二问刨掉已选的座位 1"
    )


# ---------------------------------------------------------------------------
# 结姻（孙尚香）：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
#
# 票 03：形状与【青囊】逐字同源 —— 候选恒一条骨架，"弃哪两张 / 救谁"由发动后的
# 步骤流水线问（``SELECT_CARDS`` **恰好 2 张** → ``SELECT_OPTION``）。旧的
# "两维过闸降级为顺序多轮"分支已删除，同一个技能不再有两种交互形状。
# ---------------------------------------------------------------------------


def _jieyin_state(*, owner_hp: int = 3, target_hp: int = 2, target_hp_max: int = 4):
    """发动者 0（孙尚香，手牌 2 张）+ 座位 1（默认已受伤男性）。"""
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=owner_hp,
                hp_max=4,
                hand=(first, second),
                hero="sunshangxiang",
                gender=Gender.FEMALE,
            ),
            SeatSpec(hp=target_hp, hp_max=target_hp_max, gender=Gender.MALE),
        ],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    return first, second, state


def _jieyin_driver(*, target: int = 1, pick: tuple[int, int]):
    """确认预发动：复合动作（``picks`` = 一并弃的两张手牌，``choices`` = 目标座位）。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if JIEYIN not in query.preactivations:
                return _find(query, lambda action: action.kind == "EndPhase")
            return _composite(query, JIEYIN, picks=pick, choices=(f"seat:{target}",))
        return _decline(query)

    return driver


def test_jieyin_target_pool_is_wounded_males_excluding_self():
    first, second = _cards("slash")[0], _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hp=3,
                hp_max=4,
                hand=(first, second),
                hero="sunshangxiang",
                gender=Gender.FEMALE,
            ),
            SeatSpec(hp=4, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.MALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )

    engine = _park_at_play_phase(state)
    steps = _manifest(engine.pending, JIEYIN)

    assert set(_pool_options(steps[1])) == {"seat:2"}, (
        "只含已受伤男性：满血男性、受伤女性与发动者自己都出池"
    )


def test_jieyin_asks_the_two_cards_then_the_target():
    first, second, state = _jieyin_state()

    engine = _park_at_play_phase(state)
    steps = _manifest(engine.pending, JIEYIN)

    assert [step.name for step in steps] == ["card", "seat"]
    assert (steps[0].min, steps[0].max) == (2, 2), "恰好两张手牌（一并选）"
    assert set(_pool_options(steps[0])) >= {str(first), str(second)}


def test_jieyin_heals_both_and_clamps_at_the_maximum():
    first, second, state = _jieyin_state(owner_hp=4, target_hp=2, target_hp_max=3)

    engine = _run(
        state,
        [Scripted(_jieyin_driver(target=1, pick=(first, second))), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    assert engine.state.player(0).hp == 4, "自己已满 ⇒ 不再回复、不越上限"
    assert first in engine.state.discard and second in engine.state.discard


def test_jieyin_heals_the_wounded_activator_as_well():
    first, second, state = _jieyin_state(owner_hp=2, target_hp=2)

    engine = _run(
        state,
        [Scripted(_jieyin_driver(target=1, pick=(first, second))), Scripted(_decline)],
        _skill_frame_popped,
    )

    assert engine.state.player(0).hp == 3, "发动者自己也回复 1 点"
    assert engine.state.player(1).hp == 3, "目标回复 1 点"


def test_jieyin_cancel_leaves_the_table_untouched_and_the_skill_available():
    """取消 = 不提交；清单仍在同一阶段询问上，双方未治疗、成本未动。"""
    first, second, state = _jieyin_state(owner_hp=2, target_hp=2)

    engine = _park_at_play_phase(state)
    player = engine.state.player(0)

    assert _manifest(engine.pending, JIEYIN), "同一阶段随时可再确认"
    assert {first, second} <= set(player.hand), "两张成本都还在手里"
    assert not engine.state.discard, "弃牌堆为空 —— 成本没有被执行"
    assert engine.state.player(0).hp == 2 and engine.state.player(1).hp == 2, "双方没有被治疗"
    assert not _skill_frame_popped(engine), "取消（不提交）⇒ 没有 SKILL 帧"


def test_a_cancelable_option_step_offers_pass_last_so_a_first_choice_actor_commits():
    """**活锁**回归（票 03 扫出）：``Pass`` 是"放弃"，必须排在真候选**之后**。

    票 01 的 AI 纪律（``heuristic._is_committed_skill_step``）只护住了启发式 AI；取
    ``legal_actions[0]`` 的贪心参与者（``tests/test_web.py::_play_to_end`` 就是一例）
    一旦撞上"Pass 排在首位"，就会 发动→取消→再发动 地空转——因为取消不消耗次数，
    技能立刻重新进池。候选次序的真源是 ``effects.actions._sort_key``：``SelectOption``
    原先落进与 ``Pass`` 相同的兜底键 ``(1, (), kind)``，而 ``"Pass" < "SelectOption"``。

    W4-2 之后 A 族主动技第一段进了预发动（不再发 ``SELECT_OPTION``）；反间的**第二段**
    （目标猜花色）仍是逐步询问，故活锁面在那里仍然真实存在。
    """
    state, _hearts = _fanjian_state()
    order: list[list[str]] = []

    def driver(query):
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id) == "fanjian:suit":
            order.append([action.kind for action in query.legal_actions])
        return _fanjian_driver(suit="spade")(query)

    _run(state, [Scripted(driver), Scripted(driver)], _skill_frame_popped)

    assert order, "花色步确实发过一次询问"
    assert order[0][-1] == "Pass", f"放弃候选恒在最后（实际 {order[0]}）"
    assert "SelectOption" in order[0], "真候选仍要在（不能只剩 Pass）"


# ---------------------------------------------------------------------------
# 登记面：声明与 CATALOG 同步（票 12 照表登记的前置）
# ---------------------------------------------------------------------------


def test_every_declared_active_skill_has_a_handler_and_a_catalog_definition():
    assert set(active_module.ACTIVATIONS) == {
        skill.skill_id for skill in active_module.ACTIVE_SKILLS
    }
    assert all(skill_id in CATALOG for skill_id in active_module.ACTIVATIONS)


# ---------------------------------------------------------------------------
# 骨架候选：迁到步骤流水线的技能恒一条候选，与手牌数 / 目标数无关
#
# 票 01 的【离间】、票 02 的【青囊】、票 03 的【结姻】都改走"骨架候选 + 步骤流水线"，
# 两维相乘的枚举与 `CANDIDATE_CAP` 降级路径对本族**再无消费者**（旧原语按 §7 Q4 先留、
# 之后再清）。这里并排钉【青囊】与【结姻】两条"候选恒一条"的对照面。
# ---------------------------------------------------------------------------


def _wounded_males(count: int) -> list[SeatSpec]:
    return [SeatSpec(hp=2, hp_max=4, gender=Gender.MALE) for _ in range(count)]


def test_qingnang_manifest_is_constant_however_many_cards_and_targets():
    """W4-2：两步清单的步骤数 / 结构不随牌数、目标数增长（旧形状是牌 × 目标的候选枚举）。

    这条取代旧的"过闸降级"断言（`test_qingnang_degrades_to_target_only_candidates_beyond_the_cap`）：
    【青囊】的 `CANDIDATE_CAP` 降级分支已删除，候选也不再出现。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="huatuo"), *_wounded_males(7)],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    assert len(hand) * 7 > CANDIDATE_CAP, "旧形状必须过闸（否则本用例证明不了什么）"

    steps = preactivation_for(state, 0, QINGNANG)

    assert [step.name for step in steps] == ["card", "seat"], "恒两步"
    assert len(_pool_options(steps[1])) == 7, "目标候选 = 七名已受伤角色"
    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "整段询问仍不超闸值"


def test_jieyin_manifest_is_constant_however_many_cards_and_targets():
    """W4-2：两步清单（恰好两张 → 一名已受伤男性）不随牌数、目标数增长。

    这条取代旧的"过闸降级"断言（`test_jieyin_degrades_to_target_only_candidates_beyond_the_cap`）：
    【结姻】的 `CANDIDATE_CAP` 降级分支已删除，候选也不再出现。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand, hero="sunshangxiang", gender=Gender.FEMALE),
            *_wounded_males(7),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    pair_count = len(hand) * (len(hand) - 1) // 2
    assert pair_count * 7 > CANDIDATE_CAP, "旧形状必须过闸（否则本用例证明不了什么）"

    steps = preactivation_for(state, 0, JIEYIN)

    assert [step.name for step in steps] == ["card", "seat"], "恒两步"
    assert (steps[0].min, steps[0].max) == (2, 2), "恰好两张"
    assert len(_pool_options(steps[1])) == 7, "目标候选 = 七名已受伤男性"
    assert len(_play_candidates(state)) <= CANDIDATE_CAP, "整段询问仍不超闸值"


def test_jieyin_discards_exactly_two_cards_in_one_step_and_heals_both():
    """复合动作一次弃**恰好 2 张**手牌 → 双方各回复 1（不再逐张多轮）。

    这条取代旧的"顺序多轮逐张付成本"断言（`test_jieyin_sequential_mode_discards_two_cards_asked_one_at_a_time`）：
    降级路径删除后，成本由清单的 ``(2, 2)`` 表达；W4-2 起确认 = 一个复合动作。
    """
    hand = tuple(range(20))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hp_max=4, hand=hand, hero="sunshangxiang", gender=Gender.FEMALE),
            SeatSpec(hp=2, hp_max=4, gender=Gender.MALE),
        ],
        deck=_deck_excluding(*hand),
        turn_seat=0,
    )
    pair = (hand[0], hand[1])
    asked_picks: list[tuple[int, ...]] = []

    def jieyin(query):
        if query.kind == QueryKind.PLAY_PHASE:
            assert JIEYIN in query.preactivations
            action = _composite(query, JIEYIN, picks=pair, choices=("seat:1",))
            asked_picks.append(action.picks)
            return action
        return _decline(query)

    engine = _run(state, [Scripted(jieyin), Scripted(_decline)], _skill_frame_popped)

    asked = [record.query.kind for record in engine.records if record.kind == "query"]
    assert "SELECT_CARDS" not in asked and "SELECT_OPTION" not in asked, (
        "清单由本人填完 ⇒ 不再逐步询问"
    )
    assert len(asked_picks) == 1 and len(asked_picks[0]) == 2, "复合动作恰带两张"
    assert engine.state.player(0).hp == 3, "自己回复 1 点"
    assert engine.state.player(1).hp == 3, "目标回复 1 点"
    discarded = [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone is not None
        and record.from_zone.zone.value == "hand"
        and record.to_zone is not None
        and record.to_zone.zone.value == "discard"
        for card in record.cards
    ]
    assert discarded == list(pair), "恰好两张手牌被弃（不是一张、也不是三张）"


def test_lijian_manifest_steps_carry_their_own_wording():
    """W4-fix-3：清单步骤承载**各自的文案**（取自声明的 ``step_requirements``）。

    旧逐步流程里那句"先点选的是决斗使用者"住在 ``SkillDef.step_requirements``，而清单的
    ``Step`` 没有承载文案的字段 ⇒ 界面只能画通用的"选目标"，看不出谁是使用者。现在
    第 2/3 步分别写明"决斗使用者 / 决斗目标"。
    """
    _card, state = _lijian_state()

    steps = preactivation_for(state, 0, LIJIAN)

    assert "使用者" in steps[1].wording, f"步 2 要写明使用者：{steps[1].wording!r}"
    assert "目标" in steps[2].wording, f"步 3 要写明目标：{steps[2].wording!r}"
    assert steps[1].wording != steps[2].wording, "两问的文案必须能分辨（旧文案逐字相同）"
    assert steps[0].wording, "步 1 的弃牌文案也随清单下发"
