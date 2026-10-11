r"""失去牌后（D 族，票 07）：内联时机 ``MOVE_AFTER`` 的载荷、惰性守卫与两条技能。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool`
同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

D 族的形态差别只有一处：它**不建帧、也不产候选**，而是挂在牌移动的**唯一入口**
（``Engine.move_in_frame``）在 ``move_card`` 之后**内联派发**的 ``MOVE_AFTER`` 上
（票 07 §要做什么 1 / R1-Q2 / ADR-010）。故：

- **载荷**：一次移动的七要素（``cards`` / ``from_zone`` / ``to_zone`` / ``reason`` /
  ``owner`` / ``after_holder`` / ``actor``）装进 :class:`MoveEvent`，由引擎写进当前帧的
  ``frame.data`` 私有槽 :data:`MOVE_EVENT_KEY`——帧私有数据**不进记录流**（票 07 的
  两条代码事实之一），故这一步不动任何既有基准；
- **惰性守卫**：只有当前局面**存在注册在 ``MOVE_AFTER`` 上的实例**时才派发
  （:func:`move_after_providers`）。无武将模式 ⇒ 零派发 ⇒ M2 的 34 条基准一行不变
  （与【八卦阵】"选中才惰性压判定帧"同款先例）；
- **"失去"与移动原因无关**（R5-Q3）：判定只看 ``from_zone`` / ``to_zone`` / 移动后的持有，
  不看 :class:`~sgs.state.MoveReason`——使用 / 打出 / 弃置 / 交给 / 被获得 / 重铸 / 顶替
  全算，**摸牌不算**（牌堆 → 手牌）。

两条技能的逐字语义（票 07 §要做什么 6 的表）：

=========================  ======  ==================================================
技能                        锁定    触发条件
=========================  ======  ==================================================
陆逊【连营】                非锁定   ``from_zone == HAND ∧ to_zone != HAND ∧`` 移动后手牌为空
孙尚香【枭姬】              非锁定   ``from_zone == EQUIP``（含顶替：先旧后新的旧装备那条）
=========================  ======  ==================================================

两条都是**非锁定技**（技能面写了"你可以"）⇒ 发动是询问出来的，故 :func:`_dispatch` 会
先发一次 ``SKILL_ACTIVATION`` 询问。**这一族的钩子不得暂停**（不调 ``ask``、不压提问帧）：
``Engine.move_in_frame`` 的返回形状是 ``None``，钩子返回 ``"stay"`` 无处安放——本票的两条
技能都只做"摸 N 张"这一件同步事（牌堆重洗也是一次同步 delta），故约束天然成立。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.state import CardRef, GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef, dispatch_timing, resolve_instances

__all__ = [
    "MOVE_AFTER_SKILLS",
    "MOVE_EVENT_KEY",
    "MoveAfterSkill",
    "MoveEvent",
    "dispatch_move_after",
    "move_after_providers",
    "move_after_skill_definitions",
    "move_event",
]

MOVE_EVENT_KEY = "move_event"
"""帧私有槽名：本次 ``MOVE_AFTER`` 的载荷（:class:`MoveEvent`）。

用**私有别键**而不是去覆盖 ``source`` / ``targets`` 等帧字段：那些键是帧自己的语义
（外层钩子与 ``_pending_timing`` 都读它们），就地覆写会让"一次移动"污染"这次使用"。
"""


@dataclass(frozen=True, slots=True)
class MoveEvent:
    """**一次移动**的帧私有载荷（票 07 §要做什么 4）。

    ``from_zone`` / ``to_zone`` 是 :class:`~sgs.state.ZoneRef`（区域坐标），
    ``owner`` = 这次移动的**归属角色**（判定牌 = 被判定者、弃置 / 使用 = 弃置者 / 使用者、
    摸牌 = 摸牌者），``actor`` = **操使者**（效果的执行人，可能不是归属者）。
    ``after_holder`` = **移动后的持有者座位**：``to_zone.seat`` 就是它（共享区为 ``None``）。
    """

    cards: tuple[CardRef, ...]
    from_zone: ZoneRef
    to_zone: ZoneRef
    reason: MoveReason
    owner: int | None
    after_holder: int | None
    actor: int | None

    @property
    def card_ids(self) -> tuple[int, ...]:
        """本次移动的实体牌 id（"这批牌"的一切判定都读它）。"""
        return tuple(card.card_id for card in self.cards)


def move_event(frame: Any) -> MoveEvent | None:
    """当前帧上的移动载荷；不在 ``MOVE_AFTER`` 派发中时是 ``None``。"""
    return frame.data.get(MOVE_EVENT_KEY)


def move_after_providers(state: GameState, *, timing: Timing = Timing.MOVE_AFTER) -> bool:
    """**惰性守卫**：当前局面有没有注册在 ``timing`` 上的实例（票 07 §要做什么 2）。

    ``False`` ⇒ ``move_in_frame`` 一个时机事件都不发、一个钩子都不跑——无武将模式的
    记录流因此逐字节不变（M2 的 34 条基准不重录）。判据用 ``resolve_instances``
    （载体并集 ∪ 身份门控的唯一真源），故"武将牌上根本没有这个技能"的角色不会把守卫点亮。
    """
    return any(
        hook.timing is timing
        for instance in resolve_instances(state)
        for hook in instance.definition.hooks
    )


def dispatch_move_after(host: Any, frame: Any) -> None:
    """在当前帧上派发 ``MOVE_AFTER``（引擎的 ``move_in_frame`` 在 ``move_card`` 之后调它）。

    守卫在 ``Engine.move_in_frame`` 一侧已经判过，这里只做通用派发；返回值被刻意丢弃——
    本族的钩子不得暂停（见模块 docstring）。
    """
    return dispatch_timing(host, frame, Timing.MOVE_AFTER)


# ---------------------------------------------------------------------------
# 逐技能落位（票 07 §要做什么 6 的表）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MoveAfterSkill:
    """D 族一条技能的**声明**：触发条件 + 摸牌数（票 07 §要做什么 6 一行）。"""

    hero_id: str
    short_id: str
    draws: int
    trigger: Any

    @property
    def skill_id(self) -> str:
        """全限定技能 id（与数据侧共用 :func:`sgs.data.hero_skill_id` 这一处派生）。"""
        return hero_skill_id(self.hero_id, self.short_id)

    def fires(self, state: GameState, move: MoveEvent, owner: int | None) -> bool:
        """本次移动对这条技能（持有者 ``owner``）算不算"失去"（只看区域与**移动后**的持有）。"""
        return owner is not None and bool(self.trigger(state, move, owner))

    def condition(self, host: Any, frame: Any) -> bool:
        """钩子触发条件（``Hook.condition``）：不成立就连"是否发动"询问都不发。

        条件读写 :func:`move_event` 给出的本次移动载荷——没有载荷（不在``MOVE_AFTER``
        派发中）即不成立；**持有者**读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`
        （条件求值期由派发器写，票 14 §2）——``skill_owner`` 要到回调前才写。
        """
        move = move_event(frame)
        if move is None:
            return False
        return self.fires(host.state, move, frame.data.get(MATCH_OWNER_KEY))

    def handle(self, host: Any, frame: Any) -> None:
        """钩子回调：条件成立则摸 ``draws`` 张（短摸 ⇒ 平局，与摸牌阶段同口径）。"""
        host.draw_for_hand(frame, frame.data["skill_owner"], self.draws)


def _emptied_hand(state: GameState, move: MoveEvent, owner: int) -> bool:
    """陆逊【连营】的触发条件（票 07 §要做什么 6 的表，第一行）。

    ``from_zone == HAND ∧ to_zone != HAND ∧`` **移动后** ``hand`` 为空。判定读移动后的
    状态：``move_card`` 已经在派发之前跑完，故"手牌为空"就是移动后的手牌。

    **只认技能持有者本人**（票 14 §2）：失去牌的那个座位取 ``move.from_zone.seat``
    （"归属" ``move.owner`` 在【顺手牵羊】一类里写的是**拿走牌的人**），它必须等于
    ``owner``——否则别人掉光手牌会把陆逊的【连营】点亮。
    """
    if move.from_zone.zone is not Zone.HAND or move.to_zone.zone is Zone.HAND:
        return False
    if move.from_zone.seat != owner:
        return False
    return not state.player(owner).hand


def _lost_equipment(state: GameState, move: MoveEvent, owner: int) -> bool:
    """孙尚香【枭姬】的触发条件（票 07 §要做什么 6 的表，第二行）。

    只看 ``from_zone == EQUIP``：与去向、与 :class:`~sgs.state.MoveReason` 都无关——
    弃置 / 被【顺手牵羊】拿走 / **顶替**（同槽先旧后新的旧装备那条移动）都算失去。
    "装备从牌堆进入装备区"是 ``from_zone != EQUIP``，天然不触发。

    **只认技能持有者本人**（票 14 §2）：``from_zone.seat == owner``（同【连营】，
    不用"归属" ``move.owner``——拆 / 顺写的是操使者）。
    """
    del state
    return move.from_zone.zone is Zone.EQUIP and move.from_zone.seat == owner


MOVE_AFTER_SKILLS: tuple[MoveAfterSkill, ...] = (
    # 陆逊【连营】：当你失去最后的手牌时，你可以摸一张牌（规则 1030）。
    MoveAfterSkill(hero_id="luxun", short_id="lianying", draws=1, trigger=_emptied_hand),
    # 孙尚香【枭姬】：当你失去装备区里的一张牌后，你可以摸两张牌（规则 1054）。
    MoveAfterSkill(
        hero_id="sunshangxiang", short_id="xiaoji", draws=2, trigger=_lost_equipment
    ),
)
"""D 族两条技能的逐字声明（票 07 §要做什么 6 的表）。"""


def move_after_skill_definitions() -> tuple[SkillDef, ...]:
    """D 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    两条都是**非锁定技**（技能面写了"你可以"，故 ``locked=False``：发动是询问出来的）；
    ``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``（都不是主公技）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.MOVE_AFTER, skill.handle, skill.condition),),
        )
        for skill in MOVE_AFTER_SKILLS
    )
