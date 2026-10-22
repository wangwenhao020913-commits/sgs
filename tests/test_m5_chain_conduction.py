"""M5-03：连环传导（spec §5.1.1 的裁定，逐条钉死）。

落点 = 外部缝：``Engine.from_state`` 的人为局面 + 记录流（``hp_change`` / ``chained_change``
的**顺序**）+ ``_FrameSpy``（帧私有事实 ``nature`` / ``chain`` / ``chain`` 名单）+ 技能钩子
（"钩子读到的是已重置"）。伤害修正用**构造的修饰器**（藤甲 / 白银的数值由 M5-05 提供，
本票只证传导目标侧修正照常生效、来源侧修正不重算）。

钉住的合同（§5.1.1）：

1. 只有**源头**属性伤害（火 / 雷、``chain=False``）命中**横置**角色才触发；普通伤害不触发；
2. 重置与伤害值结算在**同一伤害事件内**、先于 ``DAMAGE_AFTER_TAKE`` / 濒死 / 死亡（钩子读到已重置）；
3. 名单在传导开始时**锁定**（当前回合角色起、座位号递增绕环；初始目标已重置不在名单；
   中途新横置的不进名单；轮到时已重置 / 已死亡跳过；初始目标死亡不中断）；
4. 每个传导目标：来源 = 原来源、属性 = 原属性、数量 = 初始目标**实际受到的伤害值**；
   目标侧修正照常、**来源侧加伤不重算**；结算后立即重置；
5. 传导伤害 ``chain=True``、**不再传导**；渠道牌沿用源伤害的渠道牌。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.effects import CarrierKind, Modifier, ModifierPoint
from sgs.engine import Engine
from sgs.skills import catalog as catalog_module
from sgs.skills.catalog import Hook, SkillDef
from sgs.state import EndPhase, FrameKind, Pass, QueryKind, UseCard
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")

FIRE_SLASH = cards_of_type(PACK, "fire_slash")
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")
HALBERD = cards_of_type(PACK, "halberd")


def _deck_excluding(*cards: int) -> tuple[int, ...]:
    excluded = set(cards)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


class _FrameSpy(Engine):
    """记下每一次压栈的帧（类型 / frame_id / 载荷），帧私有事实只能这样观测。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[FrameKind, int, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, frame.frame_id, dict(data)))
        return frame

    def damage_frames(self) -> list[dict[str, Any]]:
        return [data for kind, _fid, data in self.pushed if kind is FrameKind.DAMAGE]


def _install(monkeypatch, *definitions: SkillDef) -> None:
    """把构造的技能并进 ``catalog.CATALOG``（与 ``tests/test_hero_skills.py`` 同姿态）。"""
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, **{d.skill_id: d for d in definitions}}),
    )


def _damage_taken_plus_one(monkeypatch) -> str:
    """构造的「受到伤害 +1」修正（藤甲的数值代表）：只作用于**被伤害者本人**。"""

    def handler(state, ctx, current):
        del state
        return current + 1 if ctx.owner == ctx.target else current

    fake = SkillDef(
        skill_id="test.damage_taken_plus_one",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        modifiers=(Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, handler),),
    )
    _install(monkeypatch, fake)
    return fake.skill_id


def _damage_dealt_plus_one(monkeypatch) -> str:
    """构造的「造成伤害 +1」修正（酒 / 古锭刀的代表）：只作用于**来源本人**。"""

    def handler(state, ctx, current):
        del state
        return current + 1 if ctx.owner == ctx.subject else current

    fake = SkillDef(
        skill_id="test.damage_dealt_plus_one",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, handler),),
    )
    _install(monkeypatch, fake)
    return fake.skill_id


def _watch_after_take(monkeypatch, action=None) -> tuple[list[tuple[int, bool]], str]:
    """在 ``DAMAGE_AFTER_TAKE`` 上挂一个锁定钩子：记下"钩子读到的横置状态"。

    可选 ``action(host, frame)`` 在同一个钩子里改状态（用构造的局面验"中途新横置不进名单"
    与"轮到时已重置 / 已死亡跳过"）。
    """
    seen: list[tuple[int, bool]] = []

    def handler(host, frame):
        target = frame.data["target"]
        seen.append((target, host.state.player(target).chained))
        if action is not None:
            action(host, frame)

    fake = SkillDef(
        skill_id="test.chain_watch",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        hooks=(Hook(Timing.DAMAGE_AFTER_TAKE, handler=handler),),
    )
    _install(monkeypatch, fake)
    return seen, fake.skill_id


def _minimal(query):
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return query.legal_actions[0]


class _Passive:
    def respond(self, query):
        return _minimal(query)


class _PlayAt:
    """出牌阶段用 ``card_id`` 打 ``target``（只打一次）；其余最小应答。"""

    def __init__(self, card_id: int, target: int) -> None:
        self.card_id = card_id
        self.target = target
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and not self.used:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == self.card_id
                    and action.targets == (self.target,)
                ):
                    self.used = True
                    return action
        return _minimal(query)


def _advance(engine, players, stop) -> Engine:
    for _ in range(40_000):
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


def _eight_state(*, chained=(), hand0=(FIRE_SLASH[0],), equip0=(), grants=None, hp=None, dead=(), turn_seat=0):
    """8 座身份局：0 号主公、其余反贼；``grants`` = 座位 → 虚拟授予的技能 id。"""
    grants = grants or {}
    hp = hp or {}
    seats = []
    for index in range(8):
        hand = hand0 if index == turn_seat else ()
        equip = equip0 if index == turn_seat else ()
        seats.append(
            SeatSpec(
                hp=hp.get(index, 4),
                hand=hand,
                equip=equip,
                identity=Identity.LORD if index == 0 else Identity.REBEL,
                alive=index not in dead,
                chained=index in chained,
                virtual_equipment=tuple(grants.get(index, ())),
            )
        )
    used = tuple(hand0) + tuple(equip0)
    return build_state(
        pack=PACK,
        seats=seats,
        deck=_deck_excluding(*used),
        turn_seat=turn_seat,
        rule_config=RuleConfig(player_count=8),
    )


def _run_one_attack(engine, seat: int, card_id: int, target: int) -> Engine:
    attacker = _PlayAt(card_id, target)
    players = [_Passive() for _ in range(8)]
    players[seat] = attacker
    _advance(
        engine,
        players,
        lambda engine: attacker.used
        and not engine.stack
        and engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE,
    )
    return engine


def _hp_changes(engine):
    return [
        (record.seat, record.before, record.after)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "hp_change"
    ]


def _chained_changes(engine):
    return [
        (record.seat, record.chained)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "chained_change"
    ]


# ---------------------------------------------------------------------------
# §5.1.1 的 8 人例子逐座位钉死（含传导基数与目标侧修正）
# ---------------------------------------------------------------------------


def test_the_pinned_eight_player_example_conducts_counter_clockwise(monkeypatch):
    """0 对 3 造成 1 点火焰伤害：3 受 2、名单 0→4→5→6→7；5/6/7 恒为 2（不随 4 的 3 漂移）。"""
    taken = _damage_taken_plus_one(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),  # 攻击范围 4：0 → 3 的距离是 3
        grants={3: (taken,), 4: (taken,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert _hp_changes(engine) == [
        (3, 4, 2),  # 1 火伤 + 藤甲（构造 +1）= 2，传导基数是 2
        (0, 4, 2),
        (4, 4, 1),  # 2 + 自己的 +1 = 3
        (5, 4, 2),
        (6, 4, 2),
        (7, 4, 2),  # 基数固定为 2：不因 4 受了 3 而变 3
    ], "传导顺序 = 当前回合角色起、座位号递增（逆时针、绕环）"
    assert _chained_changes(engine) == [
        (3, False),
        (0, False),
        (4, False),
        (5, False),
        (6, False),
        (7, False),
    ], "每个目标结算完立即重置"


def test_the_reset_happens_inside_the_damage_event_before_after_take(monkeypatch):
    """钩子在 ``DAMAGE_AFTER_TAKE`` 读到的是**已重置**；重置与 hp_change 同帧、不另开事件帧。"""
    taken = _damage_taken_plus_one(monkeypatch)
    seen, watcher = _watch_after_take(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={0: (watcher,), 3: (taken,), 4: (taken,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert [target for target, _chained in seen] == [3, 0, 4, 5, 6, 7]
    assert [chained for _target, chained in seen] == [False] * 6, (
        "「受到伤害后」钩子读到的必须是已重置的状态"
    )

    life_decrease_frames = {
        frame_id: data
        for kind, frame_id, data in engine.pushed
        if kind is FrameKind.LIFE_DECREASE
    }
    chained_records = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "chained_change"
    ]
    assert all(record.frame_id in life_decrease_frames for record in chained_records), (
        "重置落在扣减体力帧内（同一伤害事件），不产生第二个事件帧"
    )
    assert [
        (life_decrease_frames[record.frame_id]["target"], record.chained)
        for record in chained_records
    ] == [(3, False), (0, False), (4, False), (5, False), (6, False), (7, False)]

    # 记录流顺序：同目标的 hp_change → chained_change → DAMAGE_AFTER_TAKE。
    sequence = [
        (record.delta_type, getattr(record, "seat", None))
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type in ("hp_change", "chained_change")
    ]
    assert sequence[:4] == [
        ("hp_change", 3),
        ("chained_change", 3),
        ("hp_change", 0),
        ("chained_change", 0),
    ], "先伤害值落地、再重置；每一对都在该目标自己的 after-take 之前"


def test_conduction_frames_keep_source_nature_and_channel_and_never_reconduct(monkeypatch):
    """传导帧 ``chain=True``、来源 / 属性 / 渠道沿用源伤害；``chain=True`` 不再开传导。"""
    taken = _damage_taken_plus_one(monkeypatch)
    state = _eight_state(
        chained=tuple(range(8)),  # 8 人全横置：名单 = 0,1,2,4,5,6,7（跳过已重置的 3）
        equip0=(HALBERD[0],),
        grants={3: (taken,), 4: (taken,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)
    damage = engine.damage_frames()

    assert len(damage) == 8, "1 次源头 + 7 个名单成员；chain=True 不再触发第二轮传导"
    assert [frame["chain"] for frame in damage] == [False] + [True] * 7
    assert [frame["nature"] for frame in damage] == [damage[0]["nature"]] * 8, (
        "传导伤害沿用原属性（火）"
    )
    assert all(frame["source"] == 0 for frame in damage), "来源 = 原伤害来源"
    assert all(
        frame["channel"].card_id == FIRE_SLASH[0] for frame in damage
    ), "渠道牌沿用源伤害的渠道（事件流 / 伤害飘字因此仍显示源属性）"
    assert [frame["target"] for frame in damage] == [3, 0, 1, 2, 4, 5, 6, 7]
    assert [frame["amount"] for frame in damage] == [1, 2, 2, 2, 2, 2, 2, 2], (
        "传导帧的伤害基数是初始目标实际受到的 2"
    )


def test_the_conduction_base_fixes_the_initial_targets_taken_amount(monkeypatch):
    """基数 = 初始目标**实际受到**的伤害值；来源侧加伤只在初始目标那次算一次。"""
    taken = _damage_taken_plus_one(monkeypatch)
    dealt = _damage_dealt_plus_one(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={0: (dealt,), 3: (taken,), 4: (taken,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert _hp_changes(engine) == [
        (3, 4, 1),  # 1 + 来源侧 +1 + 自己的 +1 = 3
        (0, 4, 1),  # 基数 3；0 没有目标侧修正
        (4, 4, 0),  # 3 + 自己的 +1 = 4
        (5, 4, 1),
        (6, 4, 1),
        (7, 4, 1),
    ], "来源侧加伤已烘进基数；传导目标只再吃自己的受到伤害修正"


# ---------------------------------------------------------------------------
# 触发条件：普通伤害不传导；名单锁定 / 跳过规则
# ---------------------------------------------------------------------------


def test_plain_damage_never_conducts_and_keeps_everyone_chained(monkeypatch):
    """普通【杀】命中横置角色：不传导、也不重置横置（只有属性伤害才重置）。"""
    seen, watcher = _watch_after_take(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        hand0=(SLASH[0],),
        equip0=(HALBERD[0],),
        grants={0: (watcher,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, SLASH[0], 3)

    assert _hp_changes(engine) == [(3, 4, 3)], "只有初始目标掉 1 点"
    assert _chained_changes(engine) == [], "普通伤害不重置横置"
    assert all(engine.state.player(seat).chained for seat in (0, 3, 4, 5, 6, 7))
    assert [frame["chain"] for frame in engine.damage_frames()] == [False]


def test_a_target_chained_mid_conduction_does_not_enter_the_locked_list(monkeypatch):
    """名单在传导开始时锁定：传导途中（0 号的 after-take）才横置的 1 号不挨传导伤害。"""

    def chain_one(host, frame):
        if frame.data["target"] == 0 and frame.data.get("chain"):
            host.set_chained(frame, 1, True)

    taken = _damage_taken_plus_one(monkeypatch)
    _seen, watcher = _watch_after_take(monkeypatch, chain_one)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={0: (watcher,), 3: (taken,), 4: (taken,)},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert engine.state.player(1).chained is True, "1 号确实在传导途中被横置"
    assert 1 not in [seat for seat, _before, _after in _hp_changes(engine)], (
        "中途新横置的不进已锁定的名单"
    )
    assert len(engine.damage_frames()) == 6, "名单仍是 0/4/5/6/7 + 初始目标"


def test_listed_targets_unchained_or_dead_before_their_turn_are_skipped(monkeypatch):
    """轮到时已重置（5 号被钩子重置）/ 已死亡（6 号一开始就阵亡）⇒ 跳过，不挨伤害。"""

    def reset_five(host, frame):
        if frame.data["target"] == 0 and frame.data.get("chain"):
            host.set_chained(frame, 5, False)

    taken = _damage_taken_plus_one(monkeypatch)
    _seen, watcher = _watch_after_take(monkeypatch, reset_five)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={0: (watcher,), 3: (taken,), 4: (taken,)},
        dead=(6,),
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    seats_hit = [seat for seat, _before, _after in _hp_changes(engine)]
    assert seats_hit == [3, 0, 4, 7], "5 号已重置、6 号已阵亡 ⇒ 轮到时跳过"
    assert engine.state.player(6).alive is False


def test_the_initial_target_dying_does_not_abort_conduction(monkeypatch):
    """初始目标 3 号被这次伤害打死：名单照常结算（重置先于死亡）。"""
    taken = _damage_taken_plus_one(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={3: (taken,), 4: (taken,)},
        hp={3: 1},
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert engine.state.player(3).alive is False
    assert _chained_changes(engine)[0] == (3, False), "死亡前先重置"
    assert _hp_changes(engine)[1:] == [(0, 4, 2), (4, 4, 1), (5, 4, 2), (6, 4, 2), (7, 4, 2)], (
        "初始目标死亡不中断传导"
    )
    assert (
        sum(1 for kind, _fid, _data in engine.pushed if kind is FrameKind.DEATH) == 1
    )


def test_a_conduction_target_dying_does_not_abort_the_rest_of_the_list(monkeypatch):
    """4 号在自己的传导伤害里死亡：5/6/7 照常结算，且 4 号重置先于死亡。"""
    taken = _damage_taken_plus_one(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        equip0=(HALBERD[0],),
        grants={3: (taken,), 4: (taken,)},
        hp={4: 3},  # 传导基数 2 + 自己的 +1 = 3 ⇒ 正好倒下
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 0, FIRE_SLASH[0], 3)

    assert engine.state.player(4).alive is False
    changes = _chained_changes(engine)
    assert changes.index((4, False)) < len(changes), "4 号被重置"
    assert _hp_changes(engine) == [
        (3, 4, 2),
        (0, 4, 2),
        (4, 3, 0),
        (5, 4, 2),
        (6, 4, 2),
        (7, 4, 2),
    ], "名单继续走完"


def test_the_list_starts_at_the_current_turn_player_only_when_still_chained(monkeypatch):
    """当前回合角色（2 号）未横置：名单从下一个横置角色开始（4 → 5 → 6 → 7 → 0）。"""
    taken = _damage_taken_plus_one(monkeypatch)
    state = _eight_state(
        chained=(0, 3, 4, 5, 6, 7),
        hand0=(FIRE_SLASH[0],),
        grants={3: (taken,), 4: (taken,)},
        turn_seat=2,
    )

    engine = _run_one_attack(_FrameSpy.from_state(state, seed=1), 2, FIRE_SLASH[0], 3)

    assert [seat for seat, _b, _a in _hp_changes(engine)] == [3, 4, 5, 6, 7, 0]
    assert [frame["target"] for frame in engine.damage_frames()] == [3, 4, 5, 6, 7, 0]
    assert _hp_changes(engine) == [
        (3, 4, 2),
        (4, 4, 1),
        (5, 4, 2),
        (6, 4, 2),
        (7, 4, 2),
        (0, 4, 2),
    ]
