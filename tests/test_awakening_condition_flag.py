"""框架票 01/05：觉醒条件的「视为满足」位（``satisfy_awakening_condition``）。

用户 2026-11-14 的裁定：**觉醒技分成「觉醒条件」与「判断是否觉醒的时机」两半**——辉逝的
「令其视为满足其一个觉醒技的觉醒条件」**只满足条件**，觉醒仍要等**它自己的时机**到来才
触发（不是立即结算）。落点 = per-instance 的 ``_CONDITION_SATISFIED`` 位 + 派发环
（``_matching_hooks``）在求值觉醒技的钩子条件时视为成立。

四条钉住的东西：

1. 置位**不结算**（体力 / 上限 / ☆ 授予都不动）；
2. 位只旁路**觉醒条件**那一半：时机一到就触发（条件本来不成立也触发）；
   **时机（归属）恒判**——别人的准备阶段 / 受到伤害都不触发（见第 ④ 组）；
3. 真正觉醒后位被清掉（``_mark_awakened``），此后不再靠位产出候选；
4. 拒绝面：非觉醒技 / 已觉醒 / 目标阵亡 / 技能已失效 ⇒ ``False`` 且零副作用。
"""

from __future__ import annotations

from sgs.data import hero_skill_id, load_content_pack
from sgs.effects import DamageNature
from sgs.engine import Engine
from sgs.skills import awakening_condition_satisfied, awakening_skill_awakened
from sgs.state import FrameKind, Pass, Phase, SelectOption
from sgs.timing import Timing
from support import SeatSpec, build_state, deck_excluding

PACK = load_content_pack(deck_profile="standard_108")

QIANXUE = hero_skill_id("jie_xushu", "qianxue")
JIANYAN = hero_skill_id("jie_xushu", "jianyan")
ZHUHAI = hero_skill_id("jie_xushu", "zhuhai")
QINXUE = hero_skill_id("jie_lvmeng", "qinxue")
GONGXIN = hero_skill_id("jie_lvmeng", "gongxin")


def _engine(seats: list[SeatSpec], *, turn_seat: int = 0) -> Engine:
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK),
        turn_seat=turn_seat,
    )
    engine = Engine.from_state(state, seed=7)
    engine._step()  # L0
    return engine


class _Answer:
    def __init__(self, choice: str) -> None:
        self._choice = choice

    def respond(self, query):
        if query.kind == "SELECT_OPTION":
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == self._choice:
                    return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _drive(engine: Engine, players, *, steps: int = 60) -> None:
    """推进 ``steps`` 步（够走完一个准备阶段与本回合的询问）。"""
    for _ in range(steps):
        if engine.finished:
            return
        query = engine.pending
        if query is None:
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))


# ---------------------------------------------------------------------------
# ① 置位不结算：它只是"条件成立"，不是"现在觉醒"
# ---------------------------------------------------------------------------


def test_satisfying_the_condition_marks_the_instance_but_does_not_resolve():
    engine = _engine([SeatSpec(hero="jie_lvmeng", hp=4, hp_max=4), SeatSpec()])
    state = engine.state

    assert engine.satisfy_awakening_condition(0, QINXUE) is True
    assert awakening_condition_satisfied(state, 0, QINXUE) is True
    assert state.player(0).hp_max == 4, "上限没有立刻 -1"
    assert GONGXIN not in state.player(0).granted_skills, "☆ 技能没有立刻授予"
    assert awakening_skill_awakened(state, 0, QINXUE) is False


# ---------------------------------------------------------------------------
# ② 时机到了才触发（条件本来不成立也触发）
# ---------------------------------------------------------------------------


def test_the_flagged_condition_triggers_at_its_own_timing():
    # 界吕蒙【勤学】的正常条件是"手牌数 - 体力值 ≥ 2"，这里手牌 0 / 体力 4 ⇒ 不成立。
    engine = _engine([SeatSpec(hero="jie_lvmeng", hp=4, hp_max=4), SeatSpec()])
    state = engine.state
    _drive(engine, [_Answer("draw_two"), _Answer("draw_two")])
    assert awakening_skill_awakened(state, 0, QINXUE) is False, "条件不成立时不该触发"

    assert engine.satisfy_awakening_condition(0, QINXUE) is True
    _drive(engine, [_Answer("draw_two"), _Answer("draw_two")])

    assert awakening_skill_awakened(state, 0, QINXUE) is True, "时机到了就触发"
    assert state.player(0).hp_max == 3, "【勤学】的上限 -1"
    assert GONGXIN in state.player(0).granted_skills
    assert awakening_condition_satisfied(state, 0, QINXUE) is False, "觉醒后位被清掉"


# ---------------------------------------------------------------------------
# ③ 拒绝面
# ---------------------------------------------------------------------------


def test_refuses_a_non_awakening_skill():
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])

    assert engine.satisfy_awakening_condition(0, ZHUHAI) is False, "【诛害】不是觉醒技"


def test_refuses_a_dead_target():
    engine = _engine([SeatSpec(hero="jie_xushu", alive=False), SeatSpec()])

    assert engine.satisfy_awakening_condition(0, QIANXUE) is False


def test_refuses_a_suppressed_skill():
    """失效 ≠ 未触发：被挡住的觉醒技不可被"视为满足条件"（fail-closed）。"""
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])
    engine.suppress_skills(0, "hero.some_attacker.yijue")

    assert engine.satisfy_awakening_condition(0, QIANXUE) is False


def test_refuses_an_already_awakened_skill():
    engine = _engine([SeatSpec(hero="jie_lvmeng", hp=4, hp_max=4), SeatSpec()])
    assert engine.satisfy_awakening_condition(0, QINXUE) is True
    _drive(engine, [_Answer("draw_two"), _Answer("draw_two")])
    assert awakening_skill_awakened(engine.state, 0, QINXUE) is True

    assert engine.satisfy_awakening_condition(0, QINXUE) is False


# ---------------------------------------------------------------------------
# ④ 时机（归属）恒判：置位只满足条件，不改变"谁的时机"
# ---------------------------------------------------------------------------


def test_a_satisfied_qianxue_does_not_fire_when_its_owner_takes_damage():
    """【潜心】的时机 = **你造成**伤害后。辉逝置位后【界徐庶】**受到**伤害也不触发
    （用户 2026-11-14 报障：「潜心的时机应该是自身造成伤害（目前受到伤害也能触发）」）。"""
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])
    state = engine.state
    assert engine.satisfy_awakening_condition(0, QIANXUE) is True
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": None,
            "use_timing": None,
            "target": 0,
            "channel": None,
            "nature": DamageNature.NORMAL,
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    _drive(engine, [_Answer("draw_two"), _Answer("draw_two")])

    assert awakening_skill_awakened(state, 0, QIANXUE) is False, "受伤不是「你造成伤害」"
    assert state.player(0).hp_max == 4, "上限 -1 没有发生"
    assert JIANYAN not in state.player(0).granted_skills


def test_a_satisfied_qinxue_ignores_another_seats_phase():
    """【勤学】的时机 = **你的**准备 / 结束阶段。置位后别人的准备阶段也不触发。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_lvmeng", hp=4, hp_max=4), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    assert engine.satisfy_awakening_condition(0, QINXUE) is True
    players = [_Answer("draw_two"), _Answer("draw_two")]
    for _ in range(200):
        if state.turn_seat != 1 or state.phase is not Phase.PREPARE:
            break  # 已经跑完座位 1 的准备阶段
        query = engine.pending
        if query is None:
            if engine.finished:
                break
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))

    assert awakening_skill_awakened(state, 0, QINXUE) is False, "别人的阶段不是它的时机"
    assert state.player(0).hp_max == 4
    assert GONGXIN not in state.player(0).granted_skills
