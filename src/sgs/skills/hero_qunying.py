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
  **打出不算使用**（没有 USE 帧 ⇒ 不兑现）。**已经待生效时不再置位**（票 12）：否则这张
  牌自己的后续手牌数变化（如 ③ 摸了牌）会把这个位推后到**它自己**之后。作用域 = 本游戏
  回合；回合末由 ``Engine._clear_turn_effects`` 兜底复位。
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

from sgs.data import SkillType, card_category, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.resolution import Frame
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import ActivationPlan, MATCH_OWNER_KEY, BadgeKind, Hook, SkillDef
from .flow import FlowAnswers, PickOption, SkillFlow
from .move_after import MoveEvent, move_event

__all__ = [
    "KUANGCAI",
    "KUANGCAI_FLOW",
    "LIANYING",
    "QIANXUN",
    "qunying_activation_plans",
    "qunying_skill_definitions",
]

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
    """给一个待生效位记下**此刻的使用序号**（``-1`` 之外的任何值都表示"待生效"）。

    **已经待生效时不覆盖**：这两个位是"本回合使用的下一张牌"的**一次性座位态**——较早
    那次触发已经锁定了"下一张牌"是哪一张，在它兑现之前再触发只是同一张牌、同一个位。

    不保留更早序号的后果（qunying 票 12 的根因）：使用某张牌时，它自己"手牌 → 处理区"
    也是手牌数变化，可能再触发一次 ②（例如手牌 1 + 装备 1 + 判定 0：用掉手牌后 ③ 摸牌，
    摸到的那张再被用掉时 ③ 又摸一张 ⇒ 手牌数又等于装备区张数）。此刻
    ``used_or_played_ever`` **已经是这张牌自己的序号**（``start_use`` 先 +1），覆盖后
    「指定目标时」的 ``used > serial`` 判据对这张牌本身不成立 ⇒ 位被推到下一张牌，
    这张牌（用户局面里的【酒】/【桃】/装备牌）永远等不到兑现。
    """
    if int(getattr(player, field)) >= 0:
        return
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


# ---------------------------------------------------------------------------
# 群英包·「锋势」祢衡【狂才】（qunying 票 05；用户 2026-11-22 签 Q9–Q14）
#
# 牌面（用户 `技能.txt` 逐字）：
#
#     狂才：结束阶段，你可以声明一项：牌堆顶第三张牌的类别；第四张牌的花色；第十三张牌的点数，
#           然后亮出对应的牌，若相符，你获得此牌及其上所有牌
#
# 读法（票 05 §3.1 / §4；逐条都有账本或规则行）：
#
#   - **阶段**：自己的结束阶段（`instance.owner == state.turn_seat`，与【闭月】/【观星】同一道
#     阶段门）；
#   - **牌堆张数**（用户澄清第 1 条）：牌堆**不足 3 / 4 / 13 张**时，对应选项**不列出**；
#     不足 3 张 ⇒ 整条技能不提供入口。这里只数 `state.deck`，**不**走 `peek_deck` 的
#     "不足就把弃牌堆洗进来"那一档（候选期先判，见 :func:`_kuangcai_declarations`）；
#   - **其上所有牌**（用户澄清第 2 条）= **当前牌堆**里自顶向下的第 1 … N 张，一次性"获得"
#     （`MoveReason.PUT`，与【勤政】/界【裸衣】同一口径；不是"摸"）；
#   - **亮出**（Q11 / Q14）= 公开事实（`card_reveal`，与【火攻】/界【裸衣】/界【涯角】同一条
#     记录面），牌**留在牌堆原位**；只有"所声明那一项对应的第 N 张"被亮出（上面的 N-1 张不亮）；
#   - **类别**（Q9）= 基本 / 锦囊 / 装备三大类（规则 §1.3 L79–L84 + 账本 L1339「「类型」与
#     「类别」同义，取大类」，同 L1117 界【涯角】）；**花色** = 四色；**点数** = A = 1 … K = 13
#     （规则 §12.2 L1325）；
#   - **相符则获得是强制的**（牌面写"若相符，你获得"，没有第二次询问）。
#
# 形状 = `shapes.md` §2 的「选项步（闭集，可动态）」+「帧外时机钩子 + 单步选项」的阶段体落法
# +「手写主体」（`SkillFlow` 只装两步、亮牌 / 获得住在 `effect` 里）。样板 = 界许褚【裸衣】
# （`peek_deck` + `reveal_card` + `move_in_frame` + `open_phase_body_frame`）、界【结姻】
# （选项集按前序答案现算）。
# ---------------------------------------------------------------------------

KUANGCAI = hero_skill_id("qunying_niheng", "kuangcai")
"""群英包·「锋势」祢衡【狂才】的全限定技能 id。"""

KUANGCAI_THIRD_TYPE = "kuangcai:third_type"
KUANGCAI_FOURTH_SUIT = "kuangcai:fourth_suit"
KUANGCAI_THIRTEENTH_RANK = "kuangcai:thirteenth_rank"

KUANGCAI_DECLARATIONS: tuple[str, ...] = (
    KUANGCAI_THIRD_TYPE,
    KUANGCAI_FOURTH_SUIT,
    KUANGCAI_THIRTEENTH_RANK,
)
"""三个声明项的选项 id（顺序 = 牌面顺序；`options.js:OPTION_LABELS` 各一条中文）。"""

KUANGCAI_DEPTHS: dict[str, int] = {
    KUANGCAI_THIRD_TYPE: 3,
    KUANGCAI_FOURTH_SUIT: 4,
    KUANGCAI_THIRTEENTH_RANK: 13,
}
"""声明项 → 对应的牌堆**深度**（第 N 张；也是"相符时获得的张数"）。"""

KUANGCAI_RANK_PREFIX = "kuangcai_rank_"
"""点数选项 id 的前缀（``kuangcai_rank_1`` … ``kuangcai_rank_13``；中文由 `OPTION_LABELS` 给）。"""

KUANGCAI_VALUES: dict[str, tuple[str, ...]] = {
    # 类别：三大类（复用【义起】已在 `OPTION_LABELS` 里的三个 id 与中文）。
    KUANGCAI_THIRD_TYPE: ("basic", "trick", "equipment"),
    # 花色：引擎花色值（中文由 `projection.option_labels` 从 `SUIT_LABELS` 派生，不另登记）。
    KUANGCAI_FOURTH_SUIT: ("spade", "heart", "club", "diamond"),
    # 点数：13 档（A = 1 … K = 13）。
    KUANGCAI_THIRTEENTH_RANK: tuple(
        f"{KUANGCAI_RANK_PREFIX}{rank}" for rank in range(1, 14)
    ),
}
"""声明项 → 第二步可选的值闭集（类别 3 / 花色 4 / 点数 13）。"""

_KUANGCAI_REVEALED = "kuangcai_revealed"
"""帧私有：这一次 `peek_deck` 定格下来的 N 张牌 id（自顶向下；续跑沿用它，不重看牌堆）。"""

_KUANGCAI_REVEAL_MOVED = "kuangcai_reveal_moved"
"""帧私有：第 N 张已经**亮进处理区**（亮出只做一次）。"""

_KUANGCAI_VERDICT = "kuangcai_verdict"
"""帧私有：这一次声明相不相符（``hit`` / ``miss``；重入不重判）。"""

_KUANGCAI_GAINED_REVEALED = "kuangcai_gained_revealed"
"""帧私有：亮出的那张已经从处理区进手牌。"""

_KUANGCAI_GAINED_ABOVE = "kuangcai_gained_above"
"""帧私有：它在牌堆里的"其上所有牌"已经进手牌。"""

_KUANGCAI_DISCARDED = "kuangcai_discarded"
"""帧私有：不相符时亮出的那张已经置入弃牌堆。"""


def _kuangcai_rank_of(value: str) -> int | None:
    """``kuangcai_rank_<n>`` → n；不是这个前缀 ⇒ ``None``（不猜）。"""
    text = str(value)
    if not text.startswith(KUANGCAI_RANK_PREFIX):
        return None
    try:
        rank = int(text[len(KUANGCAI_RANK_PREFIX) :])
    except ValueError:  # pragma: no cover - 选项闭集由本模块给出，撞到即声明写错
        return None
    return rank if 1 <= rank <= 13 else None


def _kuangcai_declarations(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """第 1 步的闭集：**牌堆张数够**的那几项（用户澄清第 1 条）。

    只数 ``state.deck``（不洗弃牌堆）：`peek_deck` 在张数不足时会把弃牌堆洗进来，
    那是**摸牌**的语义，不是"牌堆顶第 13 张"的语义 ⇒ 这一问必须在搬任何牌之前按原始牌堆判。
    """
    del frame, owner, answers
    depth = len(state.deck)
    return tuple(
        option for option in KUANGCAI_DECLARATIONS if depth >= KUANGCAI_DEPTHS[option]
    )


def _kuangcai_values(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """第 2 步的闭集：按第 1 步的答案现算（类别 3 / 花色 4 / 点数 13）。"""
    del state, frame, owner
    return KUANGCAI_VALUES.get(str(answers.get("declaration", "")), ())


def _kuangcai_matches(state: GameState, card: Any, declaration: str, value: str) -> bool:
    """亮出的那张牌是否与所声明的值**相符**（类别 / 花色 / 点数三种比法，各读数据层事实）。"""
    if declaration == KUANGCAI_THIRD_TYPE:
        return card_category(state.pack_id, card.type).value == str(value)
    if declaration == KUANGCAI_FOURTH_SUIT:
        return (card.suit or "") == str(value)
    if declaration == KUANGCAI_THIRTEENTH_RANK:
        rank = _kuangcai_rank_of(value)
        return rank is not None and card.rank is not None and int(card.rank) == rank
    return False


def _kuangcai_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：**亮出**第 N 张（进处理区）→ 相符则获得"此牌及其上所有牌"；不相符则置入弃牌堆。

    「亮出」的口径（用户 2026-11-22 第三轮 + 账本同形先例 = 五谷 / 荐言 / 裸衣 / 涉猎）：
    **亮出的牌离开牌堆、进入处理区**（公开可见），结算完若没有被人拿走 ⇒ **置入弃牌堆**。
    它**不是**「展示」——展示不移动、只在舞台画牌面（`card_reveal`）。故这里：
    ``peek_deck`` 只为**定格**那 N 张 id（牌不动），随后把**第 N 张** `PUT` 进处理区；
    上面 N-1 张不亮、也不动。

    三段各一个帧私有游标（G7）：亮牌（`peek_deck` + 搬进处理区）只做一次；判定结果定格
    在帧私有；获得是**两次**搬牌（处理区 → 手牌 = "此牌"；牌堆 → 手牌 = "其上所有牌"——
    `move_card` 一次只能有一个来源区），不相符则一次搬牌（处理区 → 弃牌堆）。每次搬牌都可能
    内联派发「获得牌后」而挂起，故各段先做、再做游标。
    """
    state = host.state
    owner = int(frame.data["skill_owner"])
    declaration = str(answers["declaration"])
    depth = KUANGCAI_DEPTHS.get(declaration)
    if depth is None:  # pragma: no cover - 闭集由本模块给出
        return None

    # ---- 段 1：定格牌堆顶 N 张，再把**第 N 张**亮进处理区（公开事实） --------------
    if _KUANGCAI_REVEALED not in frame.data:
        if len(state.deck) < depth:
            # 两次询问之间牌堆变短（理论上不可达：这两问之间没有任何搬牌）⇒ fail-closed。
            return None
        peeked = host.peek_deck(frame, depth)
        frame.data[_KUANGCAI_REVEALED] = tuple(int(card.card_id) for card in peeked)
        # 阶段体帧缺省 frame_id = 0（展示项只停 1s），而后面还要结算获益 ⇒ 给它一个真实帧号。
        host.open_phase_body_frame(frame)
    ids = tuple(int(card_id) for card_id in frame.data[_KUANGCAI_REVEALED])
    if len(ids) < depth:
        return None
    above = ids[: depth - 1]
    revealed = ids[depth - 1]
    if not frame.data.get(_KUANGCAI_REVEAL_MOVED):
        control = host.move_in_frame(
            frame,
            state.refs((revealed,)),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data[_KUANGCAI_REVEAL_MOVED] = True

    # ---- 段 2：判定（定格在帧私有，重入不重判） -----------------------------------
    verdict = frame.data.get(_KUANGCAI_VERDICT)
    if verdict is None:
        matched = _kuangcai_matches(state, state.card(revealed), declaration, str(answers["value"]))
        verdict = "hit" if matched else "miss"
        frame.data[_KUANGCAI_VERDICT] = verdict

    # ---- 段 3：相符 ⇒ 获得「此牌」+「其上所有牌」；不相符 ⇒ 亮出的那张置入弃牌堆 ------
    if verdict == "miss":
        if not frame.data.get(_KUANGCAI_DISCARDED):
            control = host.move_in_frame(
                frame,
                state.refs((revealed,)),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
            frame.data[_KUANGCAI_DISCARDED] = True
        return None

    # 先搬"其上所有牌"、再搬"此牌"：手牌里的次序因此仍是**牌堆自顶向下**的次序（与
    # 【裸衣】/【涉猎】"按亮出顺序进手牌"同形）；两次搬牌的来源区不同，不能合成一次。
    if above and not frame.data.get(_KUANGCAI_GAINED_ABOVE):
        control = host.move_in_frame(
            frame,
            state.refs(above),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data[_KUANGCAI_GAINED_ABOVE] = True
    if not frame.data.get(_KUANGCAI_GAINED_REVEALED):
        control = host.move_in_frame(
            frame,
            state.refs((revealed,)),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data[_KUANGCAI_GAINED_REVEALED] = True
    return None


KUANGCAI_FLOW = SkillFlow(
    name="kuangcai",
    steps=(
        # 步 1：声明哪一项（牌堆张数不够的那几项不列出）。
        PickOption(options=_kuangcai_declarations, optional=False, key="declaration"),
        # 步 2：声明具体值（选项集按第 1 步的答案现算）。
        PickOption(options=_kuangcai_values, optional=False, key="value"),
    ),
    effect=_kuangcai_effect,
)
"""【狂才】的两步流水线：声明项 → 声明值 → 亮牌 / 获得（手写主体）。"""


def _kuangcai_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """【狂才】的发动前提：**自己的**结束阶段 ∧ 牌堆至少 3 张（否则一个选项都没有）。

    与【闭月】/【观星】同一条阶段门（``owner == turn_seat``）；"牌堆 < 3"是"付不起"的
    等价物 ⇒ ``available=False``：连"是否发动"都不问（不是发一条没有选项的询问）。
    """
    del frame
    if int(instance.owner) != int(state.turn_seat):
        return ActivationPlan(available=False)
    if len(state.deck) < KUANGCAI_DEPTHS[KUANGCAI_THIRD_TYPE]:
        return ActivationPlan(available=False)
    return ActivationPlan()


def qunying_activation_plans() -> dict[str, Any]:
    """群英包的发动形态表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。

    与 :func:`~sgs.skills.arrange.arrange_activation_plans` 同一姿态：注册表按 skill id
    索引，派发逻辑里不出现技能 id 名单。
    """
    return {KUANGCAI: _kuangcai_plan}


def qunying_skill_definitions() -> tuple[SkillDef, ...]:
    """群英包的被动侧声明：【连营】（陆逊）+【狂才】（祢衡）。

    【谦逊】（陆逊）与【舌剑】（祢衡）是 A 族，声明住 :mod:`sgs.skills.active`。
    """
    return (
        SkillDef(
            skill_id=KUANGCAI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.PHASE_FINISH, flow=KUANGCAI_FLOW),),
            requirement=(
                "结束阶段，你可以声明一项：牌堆顶第三张牌的类别；第四张牌的花色；第十三张牌"
                "的点数，然后亮出对应的牌，若相符，你获得此牌及其上所有牌"
            ),
            step_requirements={
                "declaration": (
                    "声明一项：牌堆顶第三张牌的类别 / 第四张牌的花色 / 第十三张牌的点数"
                ),
                "value": "声明上一步所选项的具体值（类别 / 花色 / 点数）",
            },
        ),
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
