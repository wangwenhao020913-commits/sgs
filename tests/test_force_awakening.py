"""框架票 05：跨技能**强制觉醒**（``Engine.force_awakening``）。

规范来源 = 用户 2026-11-14：辉逝读法 **A（立即结算）**——「令其视为满足其一个觉醒技的觉醒
条件」当场跑那条觉醒技的效果，随后落"已觉醒"位。首个消费者 = 神郭嘉【辉逝】。

四条钉住的东西：

1. 效果**立即**结算（不等目标自己的时机）、☆ 技能照常授予、已觉醒位照常落；
2. 拒绝面：非觉醒技 / 已觉醒 / 目标阵亡 / 技能已失效 ⇒ ``False`` 且零副作用；
3. 效果里挂起（【勤学】的选项）时续跑正确，跑完才落已觉醒位；
4. 觉醒效果把目标**减死**（【潜心】上限 1 ⇒ 0）时"减死即收尾"，不再授予 ☆。
"""

from __future__ import annotations

from sgs.data import hero_skill_id, load_content_pack
from sgs.engine import Engine
from sgs.state import GameState, Pass, SelectOption
from sgs.timing import Timing

from support import SeatSpec, build_state, deck_excluding, run_queries

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


class _AnswerOptions:
    """只应答 ``SELECT_OPTION``（取指定 choice）；其余询问走最小合法应答。"""

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


def _drive(engine: Engine, players) -> Engine:
    """把引擎推到结算栈出空为止（询问按座位脚本应答）。"""
    return run_queries(
        engine,
        players,
        lambda e: not e.stack and e.pending is None,
    )


# ---------------------------------------------------------------------------
# ① 立即结算：上限 / ☆ 授予 / 已觉醒位
# ---------------------------------------------------------------------------


def test_force_awakening_runs_an_awakening_effect_out_of_its_timing():
    engine = _engine([SeatSpec(hero="jie_xushu", hp=4, hp_max=4), SeatSpec()])
    state = engine.state

    assert state.player(0).hp_max == 4
    assert engine.force_awakening(0, QIANXUE) is True, "应压出强制觉醒帧"
    _drive(engine, [_AnswerOptions("draw_two"), _AnswerOptions("draw_two")])

    assert state.player(0).hp_max == 3, "【潜心】的上限 -1 立即结算"
    assert JIANYAN in state.player(0).granted_skills, "☆【荐言】照常授予"
    from sgs.skills import awakening_skill_awakened

    assert awakening_skill_awakened(state, 0, QIANXUE), "已觉醒位落账"


def test_force_awakening_is_idempotent_and_refuses_an_already_awakened_skill():
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])

    assert engine.force_awakening(0, QIANXUE) is True
    _drive(engine, [_AnswerOptions("draw_two"), _AnswerOptions("draw_two")])

    assert engine.force_awakening(0, QIANXUE) is False, "已觉醒的实例不再压帧"


# ---------------------------------------------------------------------------
# ② 拒绝面：非觉醒技 / 阵亡 / 失效
# ---------------------------------------------------------------------------


def test_force_awakening_refuses_a_non_awakening_skill():
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])

    assert engine.force_awakening(0, ZHUHAI) is False, "【诛害】不是觉醒技"
    assert not engine.stack, "零副作用：一帧都不压"


def test_force_awakening_refuses_a_dead_target():
    engine = _engine([SeatSpec(hero="jie_xushu", alive=False), SeatSpec()])

    assert engine.force_awakening(0, QIANXUE) is False


def test_force_awakening_refuses_a_suppressed_skill():
    """失效 ≠ 未触发：被挡住的觉醒技不可被强制觉醒（fail-closed）。"""
    engine = _engine([SeatSpec(hero="jie_xushu"), SeatSpec()])
    engine.suppress_skills(0, "hero.some_attacker.yijue")

    assert engine.force_awakening(0, QIANXUE) is False
    assert not engine.stack


# ---------------------------------------------------------------------------
# ③ 效果里挂起：先问选项、再摸牌 / 授予
# ---------------------------------------------------------------------------


def test_force_awakening_resumes_after_a_suspended_effect():
    engine = _engine([SeatSpec(hero="jie_lvmeng", hp=4, hp_max=4), SeatSpec()])
    state = engine.state
    hand_before = len(state.player(0).hand)

    assert engine.force_awakening(0, QINXUE) is True
    _drive(engine, [_AnswerOptions("draw_two"), _AnswerOptions("draw_two")])

    assert state.player(0).hp_max == 3, "【勤学】的上限 -1"
    assert len(state.player(0).hand) == hand_before + 2, "选项「摸两张」真的摸了"
    assert GONGXIN in state.player(0).granted_skills, "挂起续跑后仍授予 ☆【攻心】"
    from sgs.skills import awakening_skill_awakened

    assert awakening_skill_awakened(state, 0, QINXUE)


# ---------------------------------------------------------------------------
# ④ 减死即收尾：觉醒效果把目标减到 0 上限
# ---------------------------------------------------------------------------


def test_force_awakening_stops_when_the_effect_kills_the_target():
    engine = _engine([SeatSpec(hero="jie_xushu", hp=1, hp_max=1), SeatSpec()])
    state = engine.state

    assert engine.force_awakening(0, QIANXUE) is True
    _drive(engine, [_AnswerOptions("draw_two"), _AnswerOptions("draw_two")])

    assert state.player(0).alive is False, "【潜心】的上限 -1 减到 0 ⇒ 直接死亡"
    assert JIANYAN not in state.player(0).granted_skills, "减死即收尾：不再授予 ☆"
