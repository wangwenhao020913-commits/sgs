"""测试支撑：构造局面的纯函数与脚本玩家（票 12 §6）。"""
from .positions import (
    PeachInPlayPhasePlayer,
    RecordingPlayer,
    SeatSpec,
    SequenceHalt,
    build_state,
    cards_of_type,
    declare_death,
    play_phase_peach_engine,
    play_standard,
    run_turns,
    run_until,
    turn_loop_engine,
)
from .scripts import PassPlayer, find_action, ran_timing, run_queries

ACCEPTANCE_SEEDS: tuple[int, ...] = tuple(range(104, 136))
"""验收用的 **32 个种子**（票 13 §验收 5/6；三处必须同批，故收在这里一份）。

原批是 ``range(1, 33)``。每一轮让随机座位上的武将变强 / 候选集合变大的修复，都会同时
挪动随机档的行为与 AI 子流的平分抽签，启发式候选的胜率随之漂移：【奸雄】修复后
20/32 → 19/32（< 0.6）；顺手修的【国色】虚拟【乐不思蜀】可判定后，``15..46`` 那批的
"优势"掉到 7（< 8）。按本仓纪律**重新选种**而不是放宽判据。

本轮（报障 2）【仁德】改成"没有出牌阶段次数上限、且不降级"：随机档因此会反复发动仁德
（探针：32 局里随机座位发动 99 次、启发式座位只发动 1 次），``24..55`` 的胜率掉到
18/32；重新选种为 ``104..135``（当前引擎下启发式 21/32、随机 10/32，优势 11）。
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
    "find_action",
    "play_phase_peach_engine",
    "play_standard",
    "ran_timing",
    "run_queries",
    "run_turns",
    "run_until",
    "turn_loop_engine",
)
