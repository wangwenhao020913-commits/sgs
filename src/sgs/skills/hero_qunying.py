r"""群英包·「锋势」陆逊（qunying 票 01）：【连营】。

数据面住 ``heroes.qunying.json``（``pack_id = qunying``；显示名「锋势」陆逊、真名陆逊、
吴 / 男 / 4 体力）。牌面（用户 ``技能.txt`` 逐字）：

    连营
      锁定技，你的手牌数变化后若与你：
      体力值相同，你本回合使用的下一张牌无距离与次数限制
      装备区的牌数量相同：你本回合使用的下一张牌指定目标时，令所有目标进入连环状态
      判定区的牌数量相等：你摸一张牌

读法（票 01 §2.1 / §4；逐条都有账本行）：

- **触发**：「手牌数变化后」= 手牌区张数真的变了的**移动事件之后**
  （``MOVE_AFTER``；进手牌 / 手牌离开都算，手牌 → **自己的**手牌即重排不算）。
  牌面没有「一张」这类限定词 ⇒ **逐事件**（默认读法（三））：一次弃两张手牌只触发一次。
- **三条分支各自判**「手牌数 == 体力值 / 装备区牌数 / 判定区牌数」，比较的是**移动之后**
  的三组量（动态量现算，默认读法（二）⑤）。锁定技 ⇒ 一个"是否发动"的询问都不发。
- **①② 的「本回合使用的下一张牌」** = 一个**一次性座位态**：置位时记下触发那一刻的
  :attr:`~sgs.state.Player.used_or_played_ever`，兑现点 = **下一次使用**的「指定目标时」
  （``USE_DESIGNATE_TARGET``，L4.3）。判据 = "这一次使用的序号**大于**置位时的序号"——
  触发它的那张牌自己不算"下一张"（手牌 → 处理区的移动就发生在它自己的 USE 帧里），
  **打出不算使用**（没有 USE 帧 ⇒ 不兑现）。作用域 = 本游戏回合；回合末由
  ``Engine._clear_turn_effects`` 兜底复位。
- **①「无距离与次数限制」**：
  - 「无距离限制」= **条件豁免**（距离 + 攻击范围两条都不查），落 ``TARGET_RANGE_EXEMPT``
    ——规则 §17.3 L1706–L1707 明文「对【杀】…即可指定任意其他合法角色」；目标池随后照常过
    ``TARGET_POOL`` 链（【空城】一类仍能删目标），与账本「风包夏侯渊【神速】」行同口径。
  - 「无次数限制」= 规则 §17.2 的 **② 档**：**越过上限仍可使用、但照常计入次数**
    （用户 2026-11-21 裁定，账本「『无次数限制』= ② 档」行）。落 ``COUNT_LIMIT`` 返回
    ``None``（这一次使用没有上限），而 ``start_use`` 走缺省 ``counts=True`` ⇒ 用了就记一次。
- **②「令所有目标进入连环状态」** = 指定目标时**逐目标** ``set_chained(frame, seat, True)``
  （账本：一趟横置 2 名角色 = 2 个事件；已横置的那一位是幂等写、不算事件）。
- **③「你摸一张牌」** = 强制段的真效果（不是簿记），摸牌可能内联触发「获得牌后」而挂起 ⇒
  帧内游标按**这一次移动事件**分桶（同一帧里可以有多次手牌数变化，各自都要摸）。

【谦逊】不是本模块的技能（它是 A 族，声明住 :mod:`sgs.skills.active` 的注册表）；
本模块只放【连营】与两处共享常量（技能 id / 座位标签）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.state import GameState, Zone
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, BadgeKind, Hook, SkillDef
from .move_after import MoveEvent, move_event

__all__ = ["LIANYING", "QIANXUN", "qunying_skill_definitions"]

QIANXUN = hero_skill_id("qunying_luxun", "qianxun")
"""群英包·「锋势」陆逊【谦逊】的全限定技能 id（声明住 ``sgs.skills.active``）。"""

LIANYING = hero_skill_id("qunying_luxun", "lianying")
"""群英包·「锋势」陆逊【连营】的全限定技能 id。"""

_DRAWN_EVENT = "lianying_drawn_event"
"""帧私有：**这一次移动事件**已经摸过牌（值 = 事件签名）。

为什么不是布尔：``MOVE_AFTER`` 的承载帧可以在一帧里派发**多次**手牌数变化（一次弃两张、
一次顶替、一次使用的离开 + 结算里的摸牌…），而挂起续跑会重入**同一个** handler。用
"事件签名"当游标同时满足两件事：续跑不重摸（同一事件的签名不变），同一帧里的第二次
变化照摸（签名不同）。签名只由实体的牌 id + 两个区域坐标组成，不含技能 / 顺序。
"""


def _hand_count_changed(event: MoveEvent | None, seat: int) -> bool:
    """这一次移动有没有改变 ``seat`` 的**手牌张数**（票 01 §2.1 的唯一判据）。

    - 进自己的手牌 ⇒ 变了（摸牌 / 获得 / 交给自己都算，来源不限）；
    - 离开自己的手牌 ⇒ 变了；**唯一但书** = 去自己的手牌（重排一类，张数没变）。
    """
    if event is None:
        return False
    to_zone = event.to_zone
    from_zone = event.from_zone
    mine_to = to_zone.zone is Zone.HAND and to_zone.seat == seat
    mine_from = from_zone.zone is Zone.HAND and from_zone.seat == seat
    if mine_to and mine_from:
        # 自己的手牌内部重排（"手牌 → 自己的手牌"）：张数没变，不算变化。
        return False
    return mine_to or mine_from


def _event_signature(event: MoveEvent | None) -> str:
    """这一次移动的身份（只用于帧内游标，不进记录流）。"""
    if event is None:
        return ""
    cards = "-".join(str(card.card_id) for card in event.cards)
    return f"{cards}|{event.from_zone.zone.value}|{event.to_zone.zone.value}"


def _changed_owner(frame: Any, host: Any) -> int | None:
    """触发者 = 技能持有者（派发环写进 ``MATCH_OWNER_KEY``）；条件不成立 ⇒ ``None``。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return None
    owner = int(owner)
    if not _hand_count_changed(move_event(frame), owner):
        return None
    return owner


def _matches(host: Any, frame: Any, field: str) -> bool:
    """三段的共同闸：手牌数变化 ∧ 手牌数 == ``field`` 指的那个量（移动后现算）。"""
    owner = _changed_owner(frame, host)
    if owner is None:
        return False
    player = host.state.player(owner)
    if field == "hp":
        compare = int(player.hp)
    elif field == "equip":
        compare = len(player.equip)
    else:  # judge
        compare = len(player.judge)
    return len(player.hand) == compare


def _unlimited_condition(host: Any, frame: Any) -> bool:
    """① 手牌数 == **体力值**（移动之后）。"""
    return _matches(host, frame, "hp")


def _chain_condition(host: Any, frame: Any) -> bool:
    """② 手牌数 == **装备区的牌数量**（移动之后）。"""
    return _matches(host, frame, "equip")


def _draw_condition(host: Any, frame: Any) -> bool:
    """③ 手牌数 == **判定区的牌数量**（移动之后）。"""
    return _matches(host, frame, "judge")


def _arm(player: Any, field: str) -> None:
    """给一个待生效位记下**此刻的使用序号**（``-1`` 之外的任何值都表示"待生效"）。"""
    setattr(player, field, int(player.used_or_played_ever))


def _unlimited(host: Any, frame: Any) -> None:
    """① 置「下一张牌无距离与次数限制」位（纯写座位态，没有牌移动 ⇒ 不会挂起）。"""
    _arm(host.state.player(int(frame.data[MATCH_OWNER_KEY])), "lianying_unlimited_after")


def _chain_flag(host: Any, frame: Any) -> None:
    """② 置「下一张牌指定目标时连环」位。"""
    _arm(host.state.player(int(frame.data[MATCH_OWNER_KEY])), "lianying_chain_after")


def _draw(host: Any, frame: Any) -> Any:
    """③ 摸一张牌（挂起续跑按**这一次移动事件**幂等）。"""
    owner = int(frame.data[MATCH_OWNER_KEY])
    signature = _event_signature(move_event(frame))
    if frame.data.get(_DRAWN_EVENT) == signature:
        return None
    control = host.draw_for_hand(frame, owner, 1)
    if control == "stay":
        return "stay"
    frame.data[_DRAWN_EVENT] = signature
    return None


def _pending_serial(player: Any, field: str) -> int:
    """``field`` 上的待生效序号；``-1`` = 没有。"""
    return int(getattr(player, field))


def _owed(frame: Any, host: Any, field: str) -> bool:
    """这一次使用是不是"下一张牌"（序号已经越过置位时那一刻）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if int(frame.data.get("source", -1)) != int(owner):
        return False
    player = host.state.player(int(owner))
    serial = _pending_serial(player, field)
    return serial >= 0 and int(player.used_or_played_ever) > serial


def _settle_condition(host: Any, frame: Any) -> bool:
    """「指定目标时」：这是持有者自己的一次使用，且至少有一个待生效位到了兑现时刻。

    **使用装备牌 / 【桃】/ 【酒】同样算**（用户 2026-11-21，引移动版势孙綝【戮连】先例）：
    这些牌"对自己使用"就是**指定自己为目标**，故它们照常兑现、照常消费两个待生效位
    （届时"所有目标" = 自己 ⇒ 把自己横置）。``_equipment_use`` 那条排除是错的，已删。
    """
    return _owed(frame, host, "lianying_chain_after") or _owed(
        frame, host, "lianying_unlimited_after"
    )


def _settle(host: Any, frame: Any) -> None:
    """兑现：先连环**这张牌的全部目标**，再把两个位清掉（①② 共用这一次使用）。

    「所有目标」读的是「指定目标时」已经定下的目标集合（``frame.data["targets"]``）——
    规则 §17.4 ①：这个时机在「成为目标时」之前，目标表此时已确定。
    """
    owner = int(frame.data[MATCH_OWNER_KEY])
    player = host.state.player(owner)
    if _owed(frame, host, "lianying_chain_after"):
        player.lianying_chain_after = -1
        for target in tuple(frame.data.get("targets", ())):
            host.set_chained(frame, int(target), True)
    if _owed(frame, host, "lianying_unlimited_after"):
        player.lianying_unlimited_after = -1


def _unlimited_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``TARGET_RANGE_EXEMPT``：待生效位的这一次使用**不查距离 / 攻击范围**。

    ``current`` 从空元组起、回调**只许加**（集合语义）；被放行的座位只是跳过 ② 的两条
    距离判据，随后照常走延时封印、附加谓词与 ``TARGET_POOL`` 链 —— 与界【武圣】② /
    风包【神速】同一条口径，**不**在 ``TARGET_POOL`` 里并集（那会把被判非法的座位加回来）。
    """
    if ctx.owner != ctx.subject:
        return current
    player = state.player(ctx.subject)
    if player.lianying_unlimited_after < 0:
        return current
    seats = tuple(int(other.seat) for other in state.players if other.alive)
    return tuple(dict.fromkeys((*current, *seats)))


def _unlimited_count(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT``：待生效位的这一次使用**没有次数上限**（``None`` = 无限制）。

    只豁免"这一次使用"：位到兑现点就被清掉，故后面再出第二张照常按额度算。而这次使用
    **照常计入次数**（``start_use`` 缺省 ``counts=True``）——规则 §17.2 的 ② 档
    （用户 2026-11-21 裁定）：越上限能用、但把额度用掉。
    """
    if ctx.owner != ctx.subject:
        return current
    if state.player(ctx.subject).lianying_unlimited_after < 0:
        return current
    return None


def qunying_skill_definitions() -> tuple[SkillDef, ...]:
    """群英包·「锋势」陆逊的【连营】声明（【谦逊】在 ``sgs.skills.active`` 里）。"""
    return (
        SkillDef(
            skill_id=LIANYING,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                # 三条 ``MOVE_AFTER`` 段：①② 只写座位态（纯簿记，按链序静默跑）；③ 是
                # 真效果（会挂起），故不声明 bookkeeping。
                Hook(
                    Timing.MOVE_AFTER,
                    condition=_unlimited_condition,
                    handler=_unlimited,
                    forced=True,
                    bookkeeping=True,
                ),
                Hook(
                    Timing.MOVE_AFTER,
                    condition=_chain_condition,
                    handler=_chain_flag,
                    forced=True,
                    bookkeeping=True,
                ),
                Hook(
                    Timing.MOVE_AFTER,
                    condition=_draw_condition,
                    handler=_draw,
                    forced=True,
                ),
                # 兑现（同时也是"打出不算使用"的落点：打出没有 USE 帧）。
                Hook(
                    Timing.USE_DESIGNATE_TARGET,
                    condition=_settle_condition,
                    handler=_settle,
                    forced=True,
                ),
            ),
            modifiers=(
                Modifier(ModifierPoint.TARGET_RANGE_EXEMPT, _unlimited_distance),
                Modifier(ModifierPoint.COUNT_LIMIT, _unlimited_count),
            ),
            # 座位常驻标签（用户 2026-11-21 签 Q16）：两条待生效位 + 都没有时的「无」。
            badge=BadgeKind.WINDOW,
            requirement=(
                "锁定技，你的手牌数变化后若与你：体力值相同，你本回合使用的下一张牌无距离与"
                "次数限制；装备区的牌数量相同，你本回合使用的下一张牌指定目标时，令所有目标"
                "进入连环状态；判定区的牌数量相等，你摸一张牌。"
            ),
        ),
    )
