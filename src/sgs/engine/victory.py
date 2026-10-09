"""身份局的胜负求解（票 25 / 决策回票 14 §2）。

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
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sgs.data import Identity

__all__ = ["DeathFact", "Side", "Victory", "solve_identity_outcome"]

Side = Literal["lord", "rebel", "renegade"]


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
    """身份局的一次终局：获胜方 + 该侧全部座位（含阵亡者，按座位升序）+ 胜因。"""

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
