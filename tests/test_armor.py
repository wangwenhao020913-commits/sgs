"""护甲值：第二条体力轴（yijiang-2014-round2 票 07 / W7；用户 2026-11-09 签 Q-A1..Q-A5）。

口径（票尾「落地口径」表，逐字）：

- ``Player.armor``（缺省 0）+ ``ARMOR_CAP = 5``（可叠加、封顶 5）；
- ``apply_armor`` 是唯一写入口（照 ``apply_hp`` 的姿态：帧先夹住，原语对越界抛错）；
- 伤害**先扣护甲、再扣体力**（只在伤害帧；「失去体力」帧不吸收），伤害值仍是全额；
- 回复体力 / 体力上限变化 / 任何「失去体力」**都不碰护甲**；
- 濒死只看体力（护甲扣光不濒死）；
- 记录流分轴记 ``armor_change`` + ``hp_change``（护甲全吸收时一条 ``hp_change`` 都不发）；
- 投影 ``PlayerPublic.armor``；AI 致死判据 = ``hp + armor``，「已受伤」仍只看 ``hp < hp_max``。

落点 = 库门面：``sgs.effects`` 的叶子原语、``Engine`` 的记录流 / 询问视图、``project``。
时点精确的局面照 ``tests/test_hero_damage_after.py`` 的先例：人为局面 + 直接压一个
``FrameKind.DAMAGE`` 子帧（与引擎在【杀】命中时压的形状逐字相同），再推进入栈。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from sgs.data import load_content_pack
from sgs.effects import EffectContext, apply_armor  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.resolution import Frame, Step  # noqa: E402
from sgs.state import ARMOR_CAP, FrameKind, Pass, PlayerPublic, project  # noqa: E402
from sgs.timing import Timing  # noqa: E402
from sgs_web.narrative import build_narrative  # noqa: E402
from sgs_web.session import Session  # noqa: E402

from support import SeatSpec, build_state, cards_of_type  # noqa: E402

PACK = load_content_pack(deck_profile="standard_108")
SLASH = cards_of_type(PACK, "slash")[0]
"""一张实体【杀】：伤害帧的渠道牌（牌面从处理区取，不必先在手里）。"""


# ---------------------------------------------------------------------------
# 叶子原语：apply_armor（唯一写入口）
# ---------------------------------------------------------------------------


def _state(*, hp=(4, 4), armor=(0, 0)):
    """无伤害帧的人为局面：护甲直接写进状态（原语 / 投影 / AI 三条判据共用）。"""
    specs = [SeatSpec(hp=hp[index], hp_max=4) for index in range(len(hp))]
    state = build_state(pack=PACK, seats=specs)
    for seat, value in enumerate(armor):
        state.players[seat].armor = value
    return state


def test_apply_armor_writes_the_value_and_reports_the_change():
    state = _state(armor=(1, 0))

    delta = apply_armor(state, EffectContext(actor=0), 0, 3)

    assert state.player(0).armor == 3, "护甲是**可叠加**的第二条体力轴，写入口只负责写"
    assert (delta.seat, delta.before, delta.after) == (0, 1, 3)


def test_apply_armor_refuses_a_value_outside_the_signed_cap():
    state = _state(armor=(2, 0))

    with pytest.raises(ValueError):
        apply_armor(state, EffectContext(actor=0), 0, ARMOR_CAP + 1)
    with pytest.raises(ValueError):
        apply_armor(state, EffectContext(actor=0), 0, -1)

    assert state.player(0).armor == 2, "越界写入必须零状态改动"


def test_armor_stacks_up_to_the_cap_and_the_primitive_stops_there():
    state = _state(armor=(0, 0))

    apply_armor(state, EffectContext(actor=0), 0, 3)
    apply_armor(state, EffectContext(actor=0), 0, ARMOR_CAP)

    assert state.player(0).armor == ARMOR_CAP == 5, "可叠加、硬上限 5（Q-A2）"


def test_the_jie_shibei_armor_grant_clamps_to_the_cap_at_the_caller():
    """调用方夹取：护甲 4 再吃【矢北】的 3 点 = **5**（``min(护甲 + 3, ARMOR_CAP)``）。

    原语不替调用方封顶（越界值直接抛，见上面那条范围检查）；唯一会叠加的调用方是【矢北】的
    开局钩子，真对局里开局护甲恒 0、撞不到上限，故这里按它的真实形状（真 ``Engine`` + 那条
    回调）把"4 + 3 ⇒ 5、且原语不抛错"钉住。
    """
    from sgs.skills.damage_after import _jie_shibei_start

    state = _state(hp=(2, 4), armor=(4, 0))
    engine = Engine.from_state(state, seed=7)
    frame = Frame(kind=FrameKind.RESOLVE, data={"skill_owner": 0}, frame_id=0)

    _jie_shibei_start(engine, frame)

    assert state.player(0).armor == ARMOR_CAP == 5, "4 + 3 被调用方夹到封顶 5"
    assert _deltas(engine, "armor_change")[-1].after == 5, "夹取后的值走唯一写入口落记录"


# ---------------------------------------------------------------------------
# 伤害分账：先扣护甲、再扣体力（唯一写点 life_decrease_on）
# ---------------------------------------------------------------------------


def _pass_or_first(query):
    """最小合法应答：响应 / 濒死窗口放弃，其余取候选里第一个。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    return query.legal_actions[0]


def _advance(engine, stop) -> Engine:
    """无技能的人为局面：一路推进到 ``stop(engine)``；询问一律放弃 / 取第一个。"""
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        engine.submit(_pass_or_first(engine.pending))
    raise AssertionError("步数上限内没有到达停止条件")


def _damage_frame(state, *, amount: int, source: int | None = 0) -> dict:
    """与引擎在【杀】命中时压的 ``DAMAGE`` 子帧逐字同形（渠道牌取处理区那张）。"""
    channel = state.card(SLASH)
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


def _engine(*, hp=(4, 4), armor=(0, 0), amount: int = 1):
    """人为局面：座位 0 = 来源、座位 1 = 目标；压一条 ``amount`` 点的 ``DAMAGE`` 子帧。"""
    specs = [SeatSpec(hp=hp[index], hp_max=4) for index in range(len(hp))]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=tuple(index for index in range(len(PACK.deck.cards)) if index != SLASH),
        processing=(SLASH,),
    )
    for seat, value in enumerate(armor):
        state.players[seat].armor = value
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE, _damage_frame(state, amount=amount), timing=Timing.DAMAGE_DEAL
    )
    return engine


def _damage_until_end(engine) -> Engine:
    return _advance(
        engine,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.DAMAGE_END
            for record in e.records
        ),
    )


def _deltas(engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _timings(engine, timing: Timing) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is timing
    ]


def test_a_two_point_hit_takes_armor_first_and_the_rest_from_hp():
    """用户 Q-A3 的实例：护甲 1 / 体力 2 吃 2 点 ⇒ 扣 1 护甲 + 1 体力（**一次 2 点伤害**）。"""
    engine = _engine(hp=(4, 2), armor=(0, 1), amount=2)

    _damage_until_end(engine)

    assert (engine.state.player(1).hp, engine.state.player(1).armor) == (1, 0), (
        "伤害先扣护甲（1 点）、不足的 1 点再扣体力"
    )
    assert _deltas(engine, "armor_change")[0].before == 1
    assert _deltas(engine, "armor_change")[0].after == 0


def test_a_fully_absorbed_hit_raises_no_hp_change_but_still_fires_the_timing():
    engine = _engine(hp=(4, 3), armor=(0, 2), amount=2)

    _damage_until_end(engine)

    assert (engine.state.player(1).hp, engine.state.player(1).armor) == (3, 0), (
        "2 点全被护甲吸收，体力一点不掉"
    )
    assert _deltas(engine, "armor_change"), "被吸收的护甲变化照样落记录"
    assert not _deltas(engine, "hp_change"), (
        "护甲全吸收 ⇒ 这一帧只有 armor_change，一条 hp_change 都不发"
    )
    assert _timings(engine, Timing.LIFE_DECREASE_AFTER), (
        "LIFE_DECREASE_AFTER 照发——挂在这里的技能要能读到（delta 可能没有）"
    )


def test_the_damage_amount_stays_the_full_value_even_when_armor_absorbs_it():
    captured: list[dict] = []

    def probe(host, frame):
        """只取扣减帧的载荷，不改状态：这条用例断言的是"伤害值仍是全额"。"""
        captured.append(dict(frame.data))

    engine = _engine(hp=(4, 2), armor=(0, 1), amount=2)
    engine.steps = {
        **engine.steps,
        # 探针排在 LIFE_DECREASE 的第一步之前，读到的就是伤害帧传下来的原值。
        FrameKind.LIFE_DECREASE: (
            Step(probe, Timing.LIFE_DECREASE_BEFORE),
            *engine.steps[FrameKind.LIFE_DECREASE],
        ),
    }

    _damage_until_end(engine)

    assert captured and captured[0]["amount"] == 2, (
        "被护甲吸收的那 1 点仍算伤害：伤害值 / note_damage_* 都按全额（Q-A3）"
    )
    assert captured[0].get("life_decrease_from_damage") is True, "伤害帧的来源判据"


def test_a_dying_target_is_dying_on_hp_alone_and_armor_never_saves_it():
    engine = _engine(hp=(4, 1), armor=(0, 1), amount=2)

    _advance(
        engine,
        lambda e: any(
            record.kind == "frame"
            and record.frame_kind is FrameKind.NEAR_DEATH
            and record.op == "push"
            for record in e.records
        ),
    )

    assert engine.state.player(1).hp == 0, "濒死只看体力（Q-A4）：护甲不挡濒死（降到 0 就进濒死）"
    assert engine.state.player(1).armor == 0, "护甲照常先被扣（1 点护甲吸掉 1 点，剩下 1 点扣体力）"


def test_armor_absorbing_everything_never_triggers_near_death():
    engine = _engine(hp=(4, 1), armor=(0, ARMOR_CAP), amount=1)

    _damage_until_end(engine)

    assert engine.state.player(1).hp == 1 and engine.state.player(1).armor == ARMOR_CAP - 1
    assert not [
        record
        for record in engine.records
        if record.kind == "frame" and record.frame_kind is FrameKind.NEAR_DEATH
    ], "护甲扣光不濒死：hp 仍 > 0 ⇒ 一个濒死帧都不压"


# ---------------------------------------------------------------------------
# 不碰护甲的三条路径：「失去体力」/ 回复体力 / 体力上限变化（Q-A4）
# ---------------------------------------------------------------------------


def _push(engine, kind: FrameKind, data: dict, timing: Timing) -> Engine:
    engine.push_frame(kind, data, timing=timing)
    return engine


def test_a_lose_hp_frame_is_never_absorbed_by_armor():
    """「失去体力」≠ 伤害：【苦肉】/【反间】/无来源失去都一点护甲都不碰（Q-A4）。"""
    state = _state(armor=(0, 3))
    engine = Engine.from_state(state, seed=7)
    _push(
        engine,
        FrameKind.LIFE_DECREASE,
        {"target": 1, "source": 0, "amount": 2},
        Timing.LIFE_DECREASE_BEFORE,
    )

    _advance(
        engine,
        lambda e: bool(_timings(e, Timing.LIFE_DECREASE_AFTER)),
    )

    assert (engine.state.player(1).hp, engine.state.player(1).armor) == (2, 3), (
        "失去体力照旧全额从体力扣，护甲原样不动"
    )
    assert not _deltas(engine, "armor_change"), "没有护甲变化 ⇒ 一条 armor_change 都不发"


def test_recovering_hp_does_not_restore_armor():
    state = _state(hp=(4, 2), armor=(0, 1))
    engine = Engine.from_state(state, seed=7)
    _push(
        engine,
        FrameKind.RECOVER,
        {"targets": (1,), "amount": 1, "source": 0},
        Timing.RECOVER_VALUE,
    )

    _advance(engine, lambda e: bool(_timings(e, Timing.RECOVER_AFTER)))

    assert (engine.state.player(1).hp, engine.state.player(1).armor) == (3, 1), (
        "回复体力只回体力（【桃】不能刷护甲）"
    )
    assert not _deltas(engine, "armor_change")


def test_hp_max_changes_do_not_touch_armor():
    state = _state(hp=(4, 2), armor=(0, 2))
    engine = Engine.from_state(state, seed=7)
    frame = Frame(kind=FrameKind.MAXLIFE, data={}, frame_id=0)

    engine.apply_hp_max_change(frame, 1, 5)
    engine.reduce_hp_max(frame, 1)

    assert engine.state.player(1).hp_max == 4
    assert engine.state.player(1).armor == 2, "护甲与体力上限是两条轴（Q-A4）"
    assert not _deltas(engine, "armor_change")


# ---------------------------------------------------------------------------
# 记录流：分轴、次序、回放重建
# ---------------------------------------------------------------------------


def test_the_armor_delta_precedes_the_hp_delta_in_the_same_life_decrease_frame():
    engine = _engine(hp=(4, 2), armor=(0, 1), amount=2)

    _damage_until_end(engine)

    armor = _deltas(engine, "armor_change")
    hp = _deltas(engine, "hp_change")
    assert len(armor) == 1 and len(hp) == 1
    assert armor[0].kind == "delta" and armor[0].delta_type == "armor_change"
    assert armor[0].frame_id == hp[0].frame_id, "同一次结算的两条 delta 同帧"
    assert (armor[0].before, armor[0].after) == (1, 0)
    assert (hp[0].before, hp[0].after) == (2, 1)
    assert armor[0].seq < hp[0].seq, "护甲在前、体力在后（回放逐条重建的次序）"


def test_folding_the_delta_stream_rebuilds_armor_and_hp():
    """回放 = 逐条 delta 重建：``armor_change`` / ``hp_change`` 各写各的轴。"""
    engine = _engine(hp=(4, 2), armor=(0, 2), amount=3)
    rebuilt = [{"hp": player.hp, "armor": player.armor} for player in engine.state.players]

    _damage_until_end(engine)

    for record in engine.records:
        if record.kind != "delta":
            continue
        if record.delta_type == "armor_change":
            rebuilt[record.seat]["armor"] = record.after
        elif record.delta_type == "hp_change":
            rebuilt[record.seat]["hp"] = record.after

    assert rebuilt[1] == {"hp": 1, "armor": 0}, "护甲 2 吸掉 2 点，剩下 1 点扣体力"
    assert rebuilt[1] == {
        "hp": engine.state.player(1).hp,
        "armor": engine.state.player(1).armor,
    }, "重建结果必须与引擎状态一致"


# ---------------------------------------------------------------------------
# 投影 / 前端载荷 / AI 判据
# ---------------------------------------------------------------------------


def test_the_projection_carries_armor_for_every_seat():
    state = _state(armor=(3, 0))

    view = project(state, 1)

    assert "armor" in PlayerPublic.model_fields, "护甲是公开的座位事实"
    assert view.players[0].armor == 3, "对手的护甲值可见（座位卡要画出来）"
    assert view.players[1].armor == 0, "默认值照发：没有护甲的座位给 0"


def test_the_lethal_check_counts_armor_while_the_wounded_check_stays_on_hp():
    """AI 的斩杀判据 ``hp + armor``；「已受伤」仍只看 ``hp < hp_max``（W7 落地口径）。"""
    from sgs.agents import heuristic as scoring

    state = _state(hp=(4, 1), armor=(0, 3))
    state.players[1].hp_max = 4
    slash = state.card(SLASH)

    view = project(state, 0)
    assert scoring._lethal_to(slash, view, 1) is False, (
        "体力 1 + 护甲 3 = 4 点要扛：1 点伤害这刀打不死（忽略护甲会误判成斩杀）"
    )

    state.players[1].armor = 0
    assert scoring._lethal_to(slash, project(state, 0), 1) is True, (
        "同一份伤害模型下，护甲清零后 1 点体力就是斩杀线"
    )

    state.players[1].hp = 4
    state.players[1].armor = ARMOR_CAP
    wounded = project(state, 0)
    assert scoring._wounded(wounded) is False, "满血 + 有护甲 ≠ 已受伤"
    state.players[0].hp = 3
    assert scoring._wounded(project(state, 0)) is True, "已受伤仍按 hp < hp_max 判"


def _real_armor_session_lines() -> str:
    """真对局 → 生产叙事入口（牌桌 / 回放页共用同一份 ``build_narrative``）。

    4 人局把座位 0 点成**界沮授移动版**（W3 的【矢北】= 全树唯一的护甲载体：开局 3 点），
    其余三个座位也显式钉死 ⇒ 默认池以后再加将不会换掉这一局。``ai="random"`` + 挂起驱动
    把整局走完（与 ``tests/test_web.py`` 的护甲载荷用例同一条真会话构造面）。
    """
    session = Session(
        seed=3,
        ai="random",
        timeout_seconds=None,
        player_count=4,
        heroes=("jie_jushou_mobile", "xuchu", "shen_zhugeliang", "jiaxu_mobile"),
    )
    answers = 0
    steps = 0
    while not session.finished:
        steps += 1
        assert steps < 10_000, "挂起驱动没有推进（活锁）"
        pending = session.pending
        if pending is None:
            session.drive()  # 五谷逐目标暂停（table-ux-overhaul 票 19）：没有挂起也要接着驱动
            continue
        session.submit_answer(pending.legal_actions[0])
        answers += 1
    assert answers and session.finished, "真对局必须真的跑完"
    entries = build_narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    return "\n".join(entry["text"] for entry in entries if entry["kind"] == "line")


def _gain_armor_lines() -> dict[int, str]:
    """开局「获得护甲」那一行**只能**用合成记录驱动：``GAME_START`` 落在首条
    ``TURN_START`` **之前**，而叙事刻意从首回合开始（``_Narrator.started``）⇒
    生产叙事流里读不到这条 delta（读不到**不是**"全树零载体"：W3 的【矢北】就是载体）。"""
    entries = build_narrative(
        [
            {"kind": "timing", "seq": 1, "timing": "TURN_START", "actor": 0},
            {
                "kind": "delta",
                "seq": 2,
                "delta_type": "armor_change",
                "seat": 1,
                "before": 0,
                "after": 3,
            },
        ],
        viewer=0,
        heroes=(),
        pack_id="standard",
    )
    return {entry["seq"]: entry["text"] for entry in entries if entry["kind"] == "line"}


def test_the_simple_narrative_writes_armor_changes_in_chinese():
    """简易档事件流（牌桌与回放页共用）：护甲变化有中文行，不落 ``armor_change`` 英文枚举。

    **吸收**那半段走**真对局**（界沮授移动版 + ``build_narrative`` 生产入口，见
    :func:`_real_armor_session_lines`）；**获得**那半段只能用合成记录，原因写在
    :func:`_gain_armor_lines`（开局 3 点是 ``GAME_START`` 钩子，叙事从首回合才开始）。
    """
    absorbed = _real_armor_session_lines()
    assert "座位0 界沮授移动版 护甲吸收伤害 1 点" in absorbed, absorbed[-2000:]
    assert "armor_change" not in absorbed, "真对局的叙事不该漏出英文枚举"

    gained = _gain_armor_lines()
    assert "获得护甲 3 点" in gained[2], gained
    assert all("armor_change" not in text for text in gained.values()), gained
