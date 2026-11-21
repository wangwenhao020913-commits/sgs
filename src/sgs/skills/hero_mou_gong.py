r"""谋攻篇-同包·谋刘赪（mou-gong-tong 票 01）：【掠影】【莺舞】。

数据面住 ``heroes.mou_gong_tong.json``（``pack_id = mou_gong_tong``）。牌面（用户
``技能.txt`` = **OL 现行文本**，逐字；移动版官网把「出牌阶段内」写在整条技能开头，差异
按账本「技能文本以 OL 现行为准」只登记、不实现）：

    掠影
      ①当你使用【杀】结算结束后，若你的“椎”标记数不小于2，你移去2个“椎”标记，你摸一张牌，
        然后可以视为使用一张【过河拆桥】；
      ②出牌阶段限两次，当你使用【杀】指定其他角色为目标时，你获得1个“椎”标记。

    莺舞
      ①当你使用非伤害类普通锦囊牌结算结束后，若你的“椎”标记数不小于2，你移去2个“椎”标记，
        你摸一张牌，然后你可以视为使用一张无次数限制的【杀】；
      ②出牌阶段限两次，当你使用非伤害类普通锦囊牌指定一名角色为目标时，若你拥有技能“掠影”，
        你获得1个“椎”标记。

读法（票 01 §0 / §2.1 / §3.1 的逐条依据）：

- 「出牌阶段限两次」⇒ **只在自己的出牌阶段**、每阶段至多 2 次发动（2022 规则集第二章
  L81：时机 = 出牌阶段的空闲时间点，条件 = 本阶段发动次数 < X；与账本「界【渐营】② 只在
  持有者自己的出牌阶段内」同一口径）。故 ② 的条件自己写 ``is_own_play_phase``——
  ``reset_phase_skill_state`` 是 key-agnostic 的，它**不**区分"谁的出牌阶段"。
- 牌面没有「可」⇒ 触发段**强制**（2022 第二章 L147：「关键是要找是否有『可』这个字」），
  只有「然后可以视为使用…」那一段有放弃出口（用户裁定「可以 = 该句效果必含放弃」）。
- ② 是**逐目标**触发（用户 2026-11-21 裁定：「一名目标=1个标记，两名目标=发动两次=2个标记，
  大于两名目标=发动两次=2个标记」）⇒ 一次使用按**目标数**折算 min(N, 剩余额度) 次发动。
- 时机：② 是「指定目标**时**」（规则 §17.4 的 ①，`USE_DESIGNATE_TARGET`；不是铁骑 / 烈弓
  用的「指定目标后」③）；① 是「使用结算结束后」（`RESOLVE_AFTER_END`，每次使用恰一次）。
- 「非伤害类普通锦囊牌」按 2022 第三章 L66 的**官方牌名名单**判（不是"这张牌实际会不会
  造成伤害"——数据层的锦囊效果是一整个不透明 ``CallOp``，静态推不出来）。
- 「无次数限制」= 2022 合并后的口径（规则 §17.2 L1667–L1692：达到上限后仍可使用、且不消耗
  次数）⇒ 产物【杀】走 ``start_use(..., counts=False)``。
- 「若你拥有技能『掠影』」= **实例还在**（2022 第三章 L27；失效仍算拥有，失去才不算）。

「椎」标记（账本「标记（marker）机制」）：两条技能都以**掠影**为 ``source_skill_id``
⇒ 单一池（票 01 的 Q3 = A）：UI 上是一条 ``椎×N``，失去【掠影】时整池清空、失效不动。
「出牌阶段限两次」的计数复用 ``catalog`` 的每阶段计数位段（bit 32..35，与界顾雍移动版
【慎行】同一段、同一复位点），按**实例键**分桶 ⇒ 两条技能的计数互不串。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    behavior_for,
    check_targets,
    is_own_play_phase,
    is_slash,
)
from sgs.state import GameState, Pass
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    _PHASE_USE_MASK,
    _PHASE_USE_SHIFT,
    instance_key,
    resolve_instances,
)
from .virtual import pure_virtual_card

__all__ = [
    "LVEYING",
    "MARKER",
    "YINGWU",
    "mou_gong_tong_skill_definitions",
]

LVEYING = hero_skill_id("mou_liucheng", "lveying")
"""谋刘赪【掠影】的全限定技能 id（也是「椎」标记的**唯一来源**）。"""

YINGWU = hero_skill_id("mou_liucheng", "yingwu")
"""谋刘赪【莺舞】的全限定技能 id。"""

MARKER = "椎"
"""标记名（两条技能共用的池）。"""

PHASE_LIMIT = 2
"""「出牌阶段限两次」的那个 2。"""

DISMANTLEMENT = "dismantlement"
"""【掠影】① 的产物牌名【过河拆桥】（纯虚拟牌，无来源实体牌）。"""

SLASH = "slash"
"""【莺舞】① 的产物牌名【杀】。"""

_DAMAGE_TRICKS = frozenset(
    {
        # 2022 规则集第三章 L66 的官方名单（伤害类锦囊牌 = 牌名为以下这些牌的牌的集合）。
        # 本仓现有牌面数据只覆盖前四项里的四项；奇正相生 / 火烧连营 / 水淹七军 / 出其不意 /
        # 趁火打劫 尚无牌堆数据 —— 将来加牌时必须把它们登记进本集合（`tests/test_card_family_sites`
        # 的"新牌族必须登记"同一条纪律）。
        "duel",  # 决斗
        "fire_attack",  # 火攻
        "barbarian_invasion",  # 南蛮入侵
        "arrow_barrage",  # 万箭齐发
    }
)
"""**伤害类**普通锦囊的牌名 id 集合（官方名单的落地）。"""

# 帧私有游标（每个"已付 / 已问 / 已发起"一个）：钩子挂起重入时不重做、也不漏做。
_SPENT = "mou_spent"
"""这次发动已经移去 2 个「椎」标记。"""

_DREW = "mou_drew"
"""这次发动已经摸过那一张牌。"""

_OPTION = "mou_option"
"""「是否视为使用」那一问的答案（``""`` = 放弃；续跑不重问）。"""

_TARGET = "mou_target"
"""选中的目标座位（续跑不重问）。"""

_USED = "mou_used"
"""虚拟牌已经发起（重入不重复 ``start_use``）。"""


# ---------------------------------------------------------------------------
# 「椎」标记与每阶段计数
# ---------------------------------------------------------------------------


def _marker_count(state: GameState, seat: int) -> int:
    """``seat`` 身上名字为「椎」的标记**合计**（不按来源过滤：Q3 = A 时只有一条，Q3 = B
    时也是"读合计"——牌面写的是"你的『椎』标记数"，不是"某条技能产生的"）。"""
    return sum(int(marker.count) for marker in state.player(seat).markers if marker.name == MARKER)


def _possesses(state: GameState, seat: int, skill_id: str) -> bool:
    """``seat`` **拥有** ``skill_id`` 这条技能吗：【莺舞】② 的「若你拥有技能『掠影』」。

    ``resolve_instances`` 已经减掉 ``lost_skills``，故这里要的是
    ``include_suppressed=True``（**失效 ≠ 失去**：失效仍算拥有，见 GLOSSARY「技能失效」）。
    """
    return any(
        instance.owner == seat and instance.skill_id == skill_id
        for instance in resolve_instances(state, include_suppressed=True)
    )


def _phase_uses(state: GameState, seat: int, skill_id: str) -> int:
    """本出牌阶段该实例已经发动几次（``catalog._PHASE_USE_MASK`` 的 4 位计数位段）。

    位段与界顾雍移动版【慎行】共用：``reset_phase_skill_state`` 按同一掩码复位，而
    ``skill_state`` 按**实例键**分桶 ⇒ 两条技能各数各的，互不串。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            flags = state.skill_state.get(instance_key(instance), 0)
            return (flags & _PHASE_USE_MASK) >> _PHASE_USE_SHIFT
    return 0


def _mark_phase_use(state: GameState, seat: int, skill_id: str) -> None:
    """给实例的每阶段计数 +1（实例不在 ⇒ 零动作；上限 15 = 位段宽）。"""
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            key = instance_key(instance)
            flags = state.skill_state.get(key, 0)
            used = (flags & _PHASE_USE_MASK) >> _PHASE_USE_SHIFT
            capped = min(used + 1, 0b1111)
            state.skill_state[key] = (flags & ~_PHASE_USE_MASK) | (capped << _PHASE_USE_SHIFT)
            return


# ---------------------------------------------------------------------------
# 牌型判据
# ---------------------------------------------------------------------------


def _is_ordinary_trick(state: GameState, card: Any) -> bool:
    """「普通锦囊牌」= 大类是锦囊 ∧ **非延时**（转化 / 虚拟牌按产物牌型判）。"""
    if card_category(state.pack_id, card.type) is not Category.TRICK:
        return False
    return not behavior_for(card).is_delayed


def _is_non_damage_trick(state: GameState, card: Any) -> bool:
    """「非伤害类普通锦囊牌」= 普通锦囊 ∧ 牌名不在官方伤害类名单里（2022 第三章 L66）。"""
    return _is_ordinary_trick(state, card) and card.type not in _DAMAGE_TRICKS


# ---------------------------------------------------------------------------
# ② 的半边：逐目标折算的「获得 1 个椎标记」（强制段，无询问）
# ---------------------------------------------------------------------------


def _owned_marker_targets(frame: Any, owner: int, *, others_only: bool) -> tuple[int, ...]:
    """这一次使用的目标里**算数**的那几个座位（座位序即派发序）。

    【掠影】② 写「指定**其他角色**为目标」⇒ 排除自己；【莺舞】② 写「指定一名角色」⇒
    含自己（默认读法（二）①）。
    """
    targets = tuple(int(seat) for seat in frame.data.get("targets", ()))
    if not others_only:
        return targets
    return tuple(seat for seat in targets if seat != owner)


def _gain_marker_condition(host: Any, frame: Any, skill_id: str, *, others_only: bool) -> bool:
    """② 的共同条件：**自己的**出牌阶段 ∧ 额度未满 ∧ 是自己的这张牌 ∧ 有算数的目标。

    目标侧的牌型判据由 :func:`_lveying_target_condition` / :func:`_yingwu_target_condition`
    补（【杀】/ 非伤害类普通锦囊 + 后者的"拥有掠影"）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if frame.data.get("source") != owner:
        return False
    if not is_own_play_phase(host.state, owner):
        return False
    if _phase_uses(host.state, owner, skill_id) >= PHASE_LIMIT:
        return False
    return bool(_owned_marker_targets(frame, owner, others_only=others_only))


def _lveying_target_condition(host: Any, frame: Any) -> bool:
    """【掠影】②：自己使用【杀】指定其他角色为目标时（指定目标**时**，规则 §17.4 的 ①）。"""
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        return False
    return _gain_marker_condition(host, frame, LVEYING, others_only=True)


def _yingwu_target_condition(host: Any, frame: Any) -> bool:
    """【莺舞】②：自己使用**非伤害类普通锦囊牌**指定一名角色为目标时，且**拥有**「掠影」。"""
    card = frame.data.get("card")
    if card is None or not _is_non_damage_trick(host.state, card):
        return False
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or not _possesses(host.state, int(owner), LVEYING):
        return False
    return _gain_marker_condition(host, frame, YINGWU, others_only=False)


def _gain_markers(host: Any, frame: Any, skill_id: str, *, others_only: bool) -> Any:
    """提交点：按目标数折算，**逐个** ``add_marker(count=1)``（每次发动一条 delta）。

    折算 = ``min(目标数, 2 − 本阶段已发动次数)``（用户裁定：一名目标 = 1 次、两名 = 2 次、
    > 两名也只结算 2 次）。**不发询问**（牌面无「可」）⇒ 声明成「簿记段」：不撑"先结哪
    一条"的面。帧私有游标记住"这一帧已经加过"，挂起重入不重复加。
    """
    done_key = f"mou_gained:{skill_id}"
    if frame.data.get(done_key):
        return None
    state = host.state
    owner = int(frame.data[MATCH_OWNER_KEY])
    remaining = PHASE_LIMIT - _phase_uses(state, owner, skill_id)
    targets = _owned_marker_targets(frame, owner, others_only=others_only)
    frame.data[done_key] = True
    for _seat in targets[: max(0, remaining)]:
        host.add_marker(
            frame,
            owner,
            name=MARKER,
            source_seat=owner,
            source_skill_id=LVEYING,
            description="移去2个，摸一张牌，然后可以视为使用一张牌",
        )
        _mark_phase_use(state, owner, skill_id)
    return None


def _lveying_gain(host: Any, frame: Any) -> Any:
    return _gain_markers(host, frame, LVEYING, others_only=True)


def _yingwu_gain(host: Any, frame: Any) -> Any:
    return _gain_markers(host, frame, YINGWU, others_only=False)


# ---------------------------------------------------------------------------
# ① 的半边：移去 2 个 → 摸 1 张 →（可以）视为使用一张牌（强制段 + 一个放弃出口）
# ---------------------------------------------------------------------------


def _spent_marker_condition(host: Any, frame: Any, skill_id: str) -> bool:
    """① 的共同条件：自己的这张牌结算结束 ∧ 「椎」≥ 2。

    阶段**不锁**（OL 文本的 ① 没有「出牌阶段内」；移动版的差异见模块头注）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if frame.data.get("source") != owner:
        return False
    if not _possesses(host.state, owner, skill_id):
        return False
    return _marker_count(host.state, owner) >= PHASE_LIMIT


def _lveying_finish_condition(host: Any, frame: Any) -> bool:
    """【掠影】①：自己使用的【杀】结算结束后。"""
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        return False
    return _spent_marker_condition(host, frame, LVEYING)


def _yingwu_finish_condition(host: Any, frame: Any) -> bool:
    """【莺舞】①：自己使用的非伤害类普通锦囊牌结算结束后。"""
    card = frame.data.get("card")
    if card is None or not _is_non_damage_trick(host.state, card):
        return False
    return _spent_marker_condition(host, frame, YINGWU)


def _finish_effect(
    host: Any, frame: Any, *, product: str, counts: bool, ask_id: str, option_id: str
) -> Any:
    """① 的段机：移去 2 个「椎」→ 摸一张牌 → 可以视为使用一张 ``product``。

    段序是牌面的"然后"：先付（强制、不可放弃）、再摸、最后才问"可以视为使用"。四个帧私有
    游标各自守住一段——``draw_for_hand`` 会内联派发"获得牌后"的钩子而挂起，``start_use``
    会开出整条使用链（含无懈窗口），两处都必须 ``"stay"`` 原样透传。
    """
    state = host.state
    owner = int(frame.data[MATCH_OWNER_KEY])
    if frame.data.get(_USED):
        return None
    if not frame.data.get(_SPENT):
        if _marker_count(state, owner) < PHASE_LIMIT:
            return None
        host.remove_marker(
            frame,
            owner,
            name=MARKER,
            source_seat=owner,
            source_skill_id=LVEYING,
            count=PHASE_LIMIT,
        )
        frame.data[_SPENT] = True
    if not frame.data.get(_DREW):
        control = host.draw_for_hand(frame, owner, 1)
        if control == "stay":
            return "stay"
        frame.data[_DREW] = True
    card = pure_virtual_card(state, product)
    option = frame.data.get(_OPTION)
    if option is None:
        # 没有合法目标 ⇒ 这一问整段不出现（强制段照做：标记已移去、牌已摸）。
        if not check_targets(state, owner, card):
            frame.data[_USED] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.RESOLVE_AFTER_END,
            skill_id=f"{ask_id}:use",
            options=(option_id,),
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        option = "" if isinstance(answer, Pass) else str(answer.choice)
        frame.data[_OPTION] = option
    if not option:
        frame.data[_USED] = True
        return None
    pool = check_targets(state, owner, card)
    seat = frame.data.get(_TARGET)
    if seat is None:
        if not pool:
            frame.data[_USED] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.RESOLVE_AFTER_END,
            skill_id=f"{ask_id}:target",
            options=tuple(f"seat:{item}" for item in pool),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        seat = int(str(answer.choice).split(":", 1)[1])
        frame.data[_TARGET] = seat
    frame.data[_USED] = True
    host.start_use(
        card,
        source=owner,
        targets=(int(seat),),
        use_timing=Timing.RESOLVE_AFTER_END,
        counts=counts,
    )
    return None


def _lveying_finish(host: Any, frame: Any) -> Any:
    return _finish_effect(
        host,
        frame,
        product=DISMANTLEMENT,
        counts=True,
        ask_id=LVEYING,
        option_id="lveying:use",
    )


def _yingwu_finish(host: Any, frame: Any) -> Any:
    # 「无次数限制」= 达到上限后仍可使用、且不消耗次数（规则 §17.2）⇒ `counts=False`。
    return _finish_effect(
        host,
        frame,
        product=SLASH,
        counts=False,
        ask_id=YINGWU,
        option_id="yingwu:use",
    )


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def mou_gong_tong_skill_definitions() -> tuple[SkillDef, ...]:
    """谋攻篇-同包·谋刘赪的两条技能（每条 = 一条声明 + 两个钩子）。

    ② 的钩子声明 ``forced=True``（牌面无「可」）**且** ``bookkeeping=True``：它是"加一个
    标记"的纯记账段，没有可选的发动形态、先后也无意义 ⇒ 不撑"先结哪一条"的面。
    ① 的钩子是 ``forced`` 但**不是**簿记：它有真效果（移去 / 摸牌 / 可选视为使用）。
    """
    return (
        SkillDef(
            skill_id=LVEYING,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_DESIGNATE_TARGET,
                    condition=_lveying_target_condition,
                    handler=_lveying_gain,
                    forced=True,
                    bookkeeping=True,
                ),
                Hook(
                    Timing.RESOLVE_AFTER_END,
                    condition=_lveying_finish_condition,
                    handler=_lveying_finish,
                    forced=True,
                ),
            ),
            requirement=(
                "①当你使用【杀】结算结束后，若你的“椎”标记数不小于2，你移去2个“椎”标记，"
                "你摸一张牌，然后可以视为使用一张【过河拆桥】；②出牌阶段限两次，当你使用"
                "【杀】指定其他角色为目标时，你获得1个“椎”标记。"
            ),
            step_requirements={
                "use": "是否视为使用一张【过河拆桥】",
                "target": "选择视为使用【过河拆桥】的目标",
            },
        ),
        SkillDef(
            skill_id=YINGWU,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_DESIGNATE_TARGET,
                    condition=_yingwu_target_condition,
                    handler=_yingwu_gain,
                    forced=True,
                    bookkeeping=True,
                ),
                Hook(
                    Timing.RESOLVE_AFTER_END,
                    condition=_yingwu_finish_condition,
                    handler=_yingwu_finish,
                    forced=True,
                ),
            ),
            requirement=(
                "①当你使用非伤害类普通锦囊牌结算结束后，若你的“椎”标记数不小于2，你移去2个"
                "“椎”标记，你摸一张牌，然后你可以视为使用一张无次数限制的【杀】；②出牌阶段"
                "限两次，当你使用非伤害类普通锦囊牌指定一名角色为目标时，若你拥有技能“掠影”，"
                "你获得1个“椎”标记。"
            ),
            step_requirements={
                "use": "是否视为使用一张【杀】",
                "target": "选择视为使用【杀】的目标",
            },
        ),
    )
