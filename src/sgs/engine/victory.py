"""胜负求解：身份局（票 25 / 决策回票 14 §2）与 PvE（票 01 §4 / M7 票 07）。

求解只读三样东西：**全座位的身份**、**本批死亡事实**、**批次末存活座位集合**。
它不碰结算栈、不改状态，因此可以直接单测（构造事实表即判决三条分支）；
引擎在**检测点**与**批次关闭点**各调一次（票 14 §3）：

- 检测点（``DEATH_REVEAL_IDENTITY_AFTER``）记入事实、求出一个**暂定**结果，
  只为压栈守卫与"不再插入新结算"提供依据；
- 批次关闭点（同批最后一次 ``DEATH_AFTER`` 之后）用**批次末存活集合**重解一次，
  那才是宣布的终局。

主公死亡是锚点：一死就判（先于"主忠方全灭"）。除主公外的存活集合恰为
``{唯一内奸}`` 且该内奸批次末仍存活 ⇒ 内奸胜；该内奸同批也死 ⇒ 主忠方胜；
其余一律反贼胜（反贼全灭也胜、只剩忠臣也胜）。本批无主公死亡时才判
"所有反贼与内奸已死 ⇒ 主忠方胜"。官方对"同时死亡"零命中（U-10），
批次规程是**程序化约定**。

PvE 另起一条纯函数（:func:`solve_pve_outcome`）：它读的是**配置 + 存活集合 + 当前轮次**，
没有死亡事实——"主角 / 受保护队友阵亡"与"轮次推进"是两个互不相干的时点，各自调一次即可，
不需要按批汇总。四类逐场条件与两侧判据见票 01 §4 的表。**接进引擎的两个调用点已由 M7 票 08
落下**（``sgs.engine.game:Engine._solve_pve`` 的调用方：死亡检测点 + 轮次 +1 之后）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sgs.data import Identity, PveConfig, PveVictoryKind

__all__ = [
    "DeathFact",
    "Side",
    "Victory",
    "solve_identity_outcome",
    "solve_pve_outcome",
]

Side = Literal["lord", "rebel", "renegade", "ally", "enemy"]
"""获胜一侧。身份局取前三值（决策回票 14 §1），PvE 取后两值（票 01 §4）。"""


@dataclass(frozen=True, slots=True)
class DeathFact:
    """一次死亡在**检测点**定格的事实（票 14 §2）。

    ``snapshot`` = 该死亡瞬间、**除死者外**的存活座位集合：反贼的"无论是否存活"、
    内奸的"主公死亡瞬间唯一存活"两条条件都读它，而不是读批次末的存活集合。

    票 14 §2 的输入表还列出 ``seat`` 与 ``killer``；求解一个都不读——内奸条件已重述为
    "批次末仍独活"（票 25 第 3 条，不再要求"亲手杀死主公"），奖惩另读帧上的 ``killer``。
    故这里只携带**被消费**的字段（YAGNI；登记为对票 14 §2 形状的就地收窄）。
    """

    identity: Identity | None
    snapshot: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Victory:
    """一次终局：获胜方 + 该侧全部座位（含阵亡者，按座位升序）+ 胜因。

    身份局只产 ``lord`` / ``rebel`` / ``renegade`` 三侧，PvE 只产 ``ally`` / ``enemy``。
    """

    side: Side
    winners: tuple[int, ...]
    reason: str


def _seats_with(identities: Sequence[Identity | None], wanted: frozenset[Identity]) -> tuple[int, ...]:
    """拥有 ``wanted`` 中任一身份的座位（座位升序）。"""
    return tuple(seat for seat, identity in enumerate(identities) if identity in wanted)


def solve_identity_outcome(
    *,
    identities: Sequence[Identity | None],
    facts: Sequence[DeathFact],
    alive: Sequence[int],
) -> Victory | None:
    """按票 14 §2 的确定性规程求解；未达终局返回 ``None``。

    ``identities`` 是**全座位**的身份（胜负的 ``winners`` 要列出全侧座位、含阵亡者），
    ``alive`` 是**批次末**存活座位（宣布时的一致快照）。
    """
    alive_set = frozenset(alive)
    lord_side = _seats_with(identities, frozenset({Identity.LORD, Identity.LOYALIST}))
    rebels = _seats_with(identities, frozenset({Identity.REBEL}))
    renegades = _seats_with(identities, frozenset({Identity.RENEGADE}))

    lord_fact = next((fact for fact in facts if fact.identity is Identity.LORD), None)
    if lord_fact is not None:
        snapshot = lord_fact.snapshot
        if len(snapshot) == 1 and snapshot[0] in renegades:
            if snapshot[0] in alive_set:
                return Victory(
                    side="renegade",
                    winners=(snapshot[0],),
                    reason="lord_dead_sole_renegade_survives",
                )
            return Victory(
                side="lord",
                winners=lord_side,
                reason="lord_dead_renegade_died_too",
            )
        if not snapshot:
            # 主公最后死：R:510 的主忠方条件直接成立（票 14 §2 的裁定）。
            return Victory(side="lord", winners=lord_side, reason="lord_dead_no_survivors")
        return Victory(side="rebel", winners=rebels, reason="lord_dead")

    if alive_set.isdisjoint(rebels) and alive_set.isdisjoint(renegades):
        return Victory(side="lord", winners=lord_side, reason="rebels_and_renegade_dead")
    return None


def solve_pve_outcome(
    *, config: PveConfig, alive: frozenset[int], round_no: int
) -> Victory | None:
    """PvE 的逐场胜负：只读**配置 + 存活集合 + 当前轮次**（票 01 §4）。

    判据表（``victory.kind`` = 逐场条件，侧别 = 本场的胜 / 败）：

    | 条件 | 胜 | 败 |
    |---|---|---|
    | ``ROUT`` | 所有 ``ENEMY`` 阵亡 | 主角 / 受保护队友阵亡 |
    | ``SURVIVE_ROUNDS(N)`` | 轮次推进到 N+1 | 同上 |
    | ``ROUT_WITHIN_ROUNDS(N)`` | N 轮内 ``ENEMY`` 全灭 | 轮次推进到 N+1 仍有 ``ENEMY``；同上 |
    | ``SLAY_BOSS`` | ``boss`` 座位阵亡 | 同上 |

    三条**确定性细则**（官方对这几格零命中，这里是程序化约定）：

    1. **败优先于胜**：主角 / 受保护队友阵亡与任何胜利条件同批发生时判败——"玩家阵亡 =
       本场失败"是用户原话（票 01 §1），不拿"敌人也死了"去把它翻成胜；
    2. **"N 轮内"是硬边界**：两类轮次条件都**先判轮次、后判全歼**，于是跨过 N 之后即使
       敌人全灭也不记这一场的胜。票 01 §4 的表没写这一格（胜列要求"N 轮内"、败列要求
       "仍有 ENEMY"），这里落到**败**而不是"不判"：返回 ``None`` 会让"全灭之后"的局
       永远跑下去，而判败与胜列的措辞不冲突；
    3. ``round_no`` 是 ``GameState.round`` 这个唯一计数源（M6 票 11），**不另建计数器**：
       "推进到 N+1" = ``round_no > N``。
    """
    allied = config.seats_of(Identity.ALLY)
    hostile = config.seats_of(Identity.ENEMY)

    if config.principal not in alive:
        return Victory(side="enemy", winners=hostile, reason="principal_dead")
    if config.protected_ally is not None and config.protected_ally not in alive:
        return Victory(side="enemy", winners=hostile, reason="protected_ally_dead")

    kind = config.victory.kind
    rounds = config.victory.rounds
    routed = not alive.intersection(hostile)

    if kind is PveVictoryKind.ROUT:
        if routed:
            return Victory(side="ally", winners=allied, reason="enemies_routed")
        return None

    if kind is PveVictoryKind.SLAY_BOSS:
        if config.boss not in alive:
            return Victory(side="ally", winners=allied, reason="boss_slain")
        return None

    # 剩下两类都带轮次上限（构造期保证 ``rounds`` 非 None）：**先判轮次、再判全歼**，
    # 于是"N 轮内"是硬边界——跨过 N 之后敌人再全灭也不记这一场的胜。
    if rounds is not None and round_no > rounds:
        if kind is PveVictoryKind.SURVIVE_ROUNDS:
            return Victory(side="ally", winners=allied, reason="rounds_survived")
        return Victory(side="enemy", winners=hostile, reason="time_limit_exceeded")
    if kind is PveVictoryKind.ROUT_WITHIN_ROUNDS and routed:
        return Victory(side="ally", winners=allied, reason="enemies_routed_in_time")
    return None
