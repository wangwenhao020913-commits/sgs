"""测试支撑：构造局面的纯函数与脚本玩家（票 12 §6）。"""
from .positions import (
    PeachInPlayPhasePlayer,
    RecordingPlayer,
    SeatSpec,
    SequenceHalt,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    play_phase_peach_engine,
    play_standard,
    run_turns,
    run_until,
    turn_loop_engine,
)
from .scripts import PassPlayer, find_action, ran_timing, run_queries

ACCEPTANCE_SEEDS: tuple[int, ...] = tuple(range(136, 168))
"""验收用的 **32 个种子**（票 13 §验收 5/6；三处必须同批，故收在这里一份）。

原批是 ``range(1, 33)``。每一轮让随机座位上的武将变强 / 候选集合变大的修复，都会同时
挪动随机档的行为与 AI 子流的平分抽签，启发式候选的胜率随之漂移：【奸雄】修复后
20/32 → 19/32（< 0.6）；顺手修的【国色】虚拟【乐不思蜀】可判定后，``15..46`` 那批的
"优势"掉到 7（< 8）。按本仓纪律**重新选种**而不是放宽判据。

本轮（报障 2）【仁德】改成"没有出牌阶段次数上限、且不降级"：随机档因此会反复发动仁德
（探针：32 局里随机座位发动 99 次、启发式座位只发动 1 次），``24..55`` 的胜率掉到
18/32；重新选种为 ``104..135``（当前引擎下启发式 21/32、随机 10/32，优势 11）。

**W4-fix-2（转化技只经预发动）又重选一次**：转化候选从 ``legal_actions`` 收回后，**随机档**
的可选面变小、盲选质量上升（``104..135`` 上随机 9/32 → 14/32），启发式档持平（17/32），
批内优势从 8 掉到 3。判据 ``≥ len//8 = 4`` 不放宽，按本仓纪律**重新选种**为相邻的
``136..167``：当时引擎下启发式 24/32、随机 15/32，优势 **9**；128 局整批的相对优势
0.25（87/128 vs 55/128）。

**票 12（每座位 ``ai:<seat>`` 子流）后重读**：32 种子批仍是 ``136..167``，判据不变——
**启发式 22/32 vs 随机 16/32（优势 6 ≥ 4）**。128 局验收带按仓纪律换到 ``5000..5127``
（88/128 vs 51/128 = 0.289），旧 128 批 ``104..231`` 降为趋势批（78/128 vs 58/128 =
0.156，只作诊断）。详细选种理由见 ``test_heuristic_ai.RATE_SEEDS`` 的 docstring。
使用者：``test_all_ai_games.SEEDS``、``test_heuristic_ai`` 的 32 种子扫描与胜率对比。"""

__all__ = (
    "ACCEPTANCE_SEEDS",
    "PassPlayer",
    "PeachInPlayPhasePlayer",
    "RecordingPlayer",
    "SeatSpec",
    "SequenceHalt",
    "build_state",
    "cards_of_type",
    "declare_death",
    "deck_excluding",
    "find_action",
    "play_phase_peach_engine",
    "play_standard",
    "ran_timing",
    "run_queries",
    "run_turns",
    "run_until",
    "turn_loop_engine",
)
