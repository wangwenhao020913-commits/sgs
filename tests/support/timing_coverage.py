"""时机覆盖清单（票 25 / 票 12 §5）：节点 → 覆盖它的基准或用例，连同来源的记录流。

清单的键集必须**恰等于** dispatch 白名单（``sgs.timing.DISPATCH_WHITELIST``，
56 个可从记录流观测的节点：M2 定格 53 + M3 解封的 ``MOVE_AFTER`` + wuming 票 04 解封的
加体力上限二时机）。每个值是一组来源 id：

- ``baseline:<slug>``：``tests/replays/<slug>.jsonl`` 这条 Golden Replay 的记录流
  （它的字节已由 ``tests/test_replays.py`` 用当前引擎回放验证）；
- ``position:<name>``：``tests/support/positions.py`` 里一个人为构造局面的记录流。

``tests/test_timing_coverage.py`` 会**真的**跑 / 读这些来源，逐节点断言"清单里声明的
节点确实在来源的记录流里出现"——清单因此不是声明，而是机械判决。

对票 12 §5 预分配表的修订（票 12 §9"实测未命中即回改清单并显式说明原因"）：

- 表里把 L2 全 25 个都记作"必须实现"；实测只有 14 个进记录流——5 个 ``INTERVAL_*``
  与准备 / 判定 / 结束阶段的 ``*_START`` / ``*_END``（共 11 个）结构性地不 dispatch。
  它们已由 ``sgs.timing.EMPTY_LOG`` 单列，**不进本清单**（票 18 的登记）。
- L2 可见的 14 个按表挂"单测 8"，落到 ``position:turn-loop``：它与
  ``tests/test_turn_loop.py`` 的构造局面同形，逐节点断言阶段轮换（含判定阶段空过）。
- L10 按表挂"基准 03 / 04 + 单测 7"，落到 ``position:play-phase-peach``（出牌阶段主动
  用【桃】）+ 基准 03 / 04（濒死自救 / 他人救）：三条互不相同的回复路径都要有来源。
- 票 32 §3：6 个原 enum-only 节点转 dispatch，来源挂在 M2 基准上——
  ``JUDGE_*`` 挂基准 14（闪电判定）、``DEATH_REVEAL_IDENTITY_BEFORE`` 挂基准 23
  （两次阵亡亮明）、``PLAY_DECLARE`` / ``PLAY_ON_PLAY`` 挂基准 31（打出链）。
- wuming 票 04：L11 的**加体力上限**二时机（``MAXLIFE_INCREASE_BEFORE/AFTER``）解封，
  落到 ``position:maxlife``。首个真实载体 = 票 01 的【天成】（`src/sgs/skills/start.py`）；
  这里仍用直接调唯一写入口 ``Engine.raise_hp_max`` 的构造局面（
  `tests/support/positions.py:maxlife_engine`），因为它给出最小帧 + 两条时机记录的定长流
  （【天成】的流还混着展示 / 询问 / 回复）；减侧两个 ``MAXLIFE_DECREASE_*`` **不解封**，
  仍留枚举位。

**不进清单的 27 个节点与理由**（票 12 §5"实战不可达者写明理由"）：

- ``sgs.timing.EMPTY_LOG``（11）：L2 边界 / 阶段间节点，流程结构上不 dispatch
  （票 18 承票 06 §7 的登记），记录流里不出现，故无来源可挂；
- ``sgs.timing.ENUM_ONLY``（16）：触发它们的前提机制（拼点、翻面、失去体力、**减**体力
  上限、牌移动、属性伤害…）仍被排除，只留枚举位——"加"体力上限已于 wuming 票 04 解封。

另：基准 ``06-deck-reshuffle`` 不作为来源——重洗没有专属时机节点，它 dispatch 的与
01–05 相同；它自己的回归断言（重洗可观测）由 ``tests/test_deck_shuffle.py`` 与
``tests/test_replays.py`` 承担。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sgs.engine import read_journal
from sgs.timing import Timing

from .positions import (
    maxlife_engine,
    move_after_engine,
    play_phase_peach_engine,
    turn_loop_engine,
)

__all__ = ["ALL_SOURCES", "COVERAGE", "dispatched", "stream_records"]

_REPLAYS_DIR = Path(__file__).resolve().parent.parent / "replays"

TURN_LOOP = "position:turn-loop"
PLAY_PHASE_PEACH = "position:play-phase-peach"
MOVE_AFTER_SCENE = "position:move-after"
MAXLIFE_SCENE = "position:maxlife"


def _baseline(slug: str) -> str:
    return f"baseline:{slug}"


B01 = _baseline("01-slash-dodge-cancel")
B02 = _baseline("02-slash-hit-damage")
B03 = _baseline("03-near-death-self-peach")
B04 = _baseline("04-near-death-other-peach")
B05 = _baseline("05-near-death-death")
B14 = _baseline("14-lightning-thunder-damage")
B23 = _baseline("23-two-deaths-revealed")
B31 = _baseline("31-play-card-response")
"""M2 基准里用作新节点来源的三条（票 32 §3：6 个 enum-only 转 dispatch）。"""

COVERAGE: dict[Timing, tuple[str, ...]] = {
    # ---- L0 游戏级：发牌只在真实开局（new_game）里发生，故挂任一条基准 ----
    Timing.GAME_INITIAL_HAND_DEALING: (B01,),
    Timing.GAME_START: (TURN_LOOP,),
    # ---- L1 / L2：构造局面单测 8（回合阶段轮换，含判定阶段空过） ----
    Timing.TURN_BEFORE_START: (TURN_LOOP,),
    Timing.TURN_START: (TURN_LOOP,),
    Timing.PHASE_PREPARE: (TURN_LOOP,),
    Timing.PHASE_JUDGE: (TURN_LOOP,),
    Timing.PHASE_DRAW_START: (TURN_LOOP,),
    Timing.PHASE_DRAW: (TURN_LOOP,),
    Timing.PHASE_DRAW_END: (TURN_LOOP,),
    Timing.PHASE_PLAY_START: (TURN_LOOP,),
    Timing.PHASE_PLAY_IDLE: (TURN_LOOP,),
    Timing.PHASE_PLAY_END: (TURN_LOOP,),
    Timing.PHASE_DISCARD_START: (TURN_LOOP,),
    Timing.PHASE_DISCARD: (TURN_LOOP,),
    Timing.PHASE_DISCARD_END: (TURN_LOOP,),
    Timing.PHASE_FINISH: (TURN_LOOP,),
    Timing.TURN_END: (TURN_LOOP,),
    Timing.TURN_AFTER_END: (TURN_LOOP,),
    # ---- L4 使用结算前：基准 01 的【杀】使用链（它同时给出【闪】抵消的互斥证据） ----
    Timing.USE_CHOOSE_TARGET: (B01,),
    Timing.USE_ON_USE: (B01,),
    Timing.USE_DESIGNATE_TARGET: (B01,),
    Timing.USE_BECOME_TARGET: (B01,),
    Timing.USE_AFTER_DESIGNATE_TARGET: (B01,),
    Timing.USE_AFTER_BECOME_TARGET: (B01,),
    # ---- L5 使用结算中/后：抵消路径挂 01，生效路径挂 02 ----
    Timing.RESOLVE_START: (B01,),
    Timing.RESOLVE_BEFORE_EFFECT: (B01,),
    Timing.RESOLVE_EFFECT: (B02,),
    Timing.RESOLVE_AFTER_EFFECT: (B02,),
    Timing.RESOLVE_END: (B01,),
    Timing.RESOLVE_AFTER_END: (B01,),
    # ---- L7 伤害 / L8 扣减体力：基准 02 的一次命中伤害 ----
    Timing.DAMAGE_DEAL: (B02,),
    Timing.DAMAGE_TAKE: (B02,),
    Timing.DAMAGE_AFTER_DEAL: (B02,),
    Timing.DAMAGE_AFTER_TAKE: (B02,),
    Timing.DAMAGE_END: (B02,),
    Timing.LIFE_DECREASE_BEFORE: (B02,),
    Timing.LIFE_DECREASE_ON: (B02, B03),
    Timing.LIFE_DECREASE_AFTER: (B02,),
    # ---- L10 回复体力：出牌阶段主动用桃 + 濒死自救 / 他人救三条路径 ----
    Timing.RECOVER_VALUE: (PLAY_PHASE_PEACH, B03, B04),
    Timing.RECOVER_AFTER: (PLAY_PHASE_PEACH, B03, B04),
    # ---- L12 濒死 / L13 死亡：基准 03 与 05 各自命中一段互斥路径 ----
    Timing.NEAR_DEATH_ENTER: (B03,),
    Timing.NEAR_DEATH_AT: (B03,),
    Timing.NEAR_DEATH_AFTER: (B03,),
    Timing.DEATH_BEFORE_START: (B05,),
    Timing.DEATH_REVEAL_IDENTITY_BEFORE: (B23,),
    Timing.DEATH_REVEAL_IDENTITY_AFTER: (B05,),
    Timing.DEATH_ON: (B05,),
    Timing.DEATH_AFTER: (B05,),
    # ---- L6 打出（票 19）/ L14 判定（票 26）：M2 基准给来源 ----
    Timing.PLAY_DECLARE: (B31,),
    Timing.PLAY_ON_PLAY: (B31,),
    Timing.JUDGE_ON: (B14,),
    Timing.JUDGE_CARD_BEFORE_EFFECT: (B14,),
    Timing.JUDGE_CARD_AFTER_EFFECT: (B14,),
    # ---- L16 牌移动：票 07 解封的 `MOVE_AFTER`，来源是 D 族的构造局面单测 ----
    Timing.MOVE_AFTER: (MOVE_AFTER_SCENE,),
    # ---- L11 体力上限：wuming 票 04 解封**加**侧二时机，来源是构造局面单测 ----
    Timing.MAXLIFE_INCREASE_BEFORE: (MAXLIFE_SCENE,),
    Timing.MAXLIFE_INCREASE_AFTER: (MAXLIFE_SCENE,),
}
"""56 个可从记录流观测的节点 → 覆盖它的基准 / 构造局面用例。"""


@lru_cache(maxsize=None)
def stream_records(source_id: str):
    """一个覆盖来源产出的记录流；同一个来源在一次测试会话里只算一次。"""
    if source_id.startswith("baseline:"):
        slug = source_id.removeprefix("baseline:")
        return read_journal(_REPLAYS_DIR / f"{slug}.jsonl")
    if source_id == TURN_LOOP:
        return turn_loop_engine().records
    if source_id == PLAY_PHASE_PEACH:
        return play_phase_peach_engine().records
    if source_id == MOVE_AFTER_SCENE:
        return move_after_engine().records
    if source_id == MAXLIFE_SCENE:
        return maxlife_engine().records
    raise KeyError(f"未知的覆盖来源：{source_id!r}")


def dispatched(source_id: str) -> frozenset[str]:
    """该来源的记录流里实测 dispatch 的时机节点（字符串取值）。"""
    return frozenset(
        record.timing.value
        for record in stream_records(source_id)
        if record.kind == "timing"
    )


ALL_SOURCES: tuple[str, ...] = tuple(
    sorted({source for sources in COVERAGE.values() for source in sources})
)
"""清单里用到的全部来源 id（去重、定序）。"""
