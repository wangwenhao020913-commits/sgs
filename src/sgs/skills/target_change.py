r"""K 族【马术】与 M 族【流离】（票 12 登记）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 等同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

两条技能各落一个**既有**机制（票 12 §落地表第 11/22 行），本票不加任何新机制：

===========  ======  ==========================================================
技能           锁定    落点
===========  ======  ==========================================================
马超【马术】    锁定    ``Modifier(DISTANCE, −1)``（**主体侧**：与 −1 坐骑同一条链）
大乔【流离】    非锁定  ``Hook(USE_BECOME_TARGET)`` 上的两步 ``SkillFlow``：弃一张牌，
                       把【杀】**转移目标**
===========  ======  ==========================================================

【马术】（规则 986）：锁定技，你**计算与其他角色的距离** −1。方向性照坐骑的既有分列
（``ctx.subject`` = 主体、``ctx.target`` = 客体，见 :func:`sgs.effects.distance`）：
只有持有者当**主体**时 −1，别人算到他的距离不变。下限 1 由距离管道在链后夹住，
故本回调不必自己夹。

【流离】（规则 1025）：当你成为【杀】的目标时，你可以弃置一张牌，将此【杀】转移给你
攻击范围内的一名其他角色（该角色不得是此【杀】的使用者）。规则 17.4/17.5 把它归在
**②成为目标时**（早于③【铁骑】），故挂 ``USE_BECOME_TARGET``；转移发生在**目标确定
阶段**，不是"牌已指定后令无效"（17.6）——落点就是把本次使用的 ``targets`` 里那一个目标
换成新目标，再对新目标重发一次"成为目标时"（新目标因此照常进响应窗口）。
"攻击范围内"读**持有者自己**的攻击范围（牌面写"你攻击范围内"），使用者与持有者自己
都被排除；新目标还必须在场（``targets_within_attack_range`` 只给存活者）。

**票 08：从手写钩子迁到两步 ``SkillFlow``**：

============  ==================================================================
步 1（成本）   ``PickCards(HAND ∪ EQUIP, 1..1)``，``face`` = 持有者（可见面）
步 2（新目标） ``PickSeats(pool=_liuli_target_pool)`` ⇒ ``SELECT_OPTION`` 的 ``seat:N``
============  ==================================================================

- 成本搬牌**推迟到提交点**（``effect`` 的第一件事）：这是票 00/01 的 N6"选择只收集、
  提交才生效"。``liuli:cost_moved`` 那道帧内游标原样保留——搬牌可能内联派发"失去牌后"
  并挂起，``move_in_frame`` 返回 ``"stay"`` 时透传。
- 步 2 从**座位槽的 ``SELECT_CARDS``** 改成 ``SELECT_OPTION`` 的 ``seat:N``——这是**有意
  的用户可见交互变更**：与 A 族【离间】/【青囊】/【结姻】同一条约定（``seat:N`` 让前端
  直接高亮场上武将、不铺选项按钮）。含大乔的 Golden Replay 据此**重录**（不是手改基准）。
- 两步都 ``optional=False``：**有意保留今天的取消出口**（只有发动询问那一道；迁移前两步
  也都没有 ``Pass``）。给一个 ``Pass`` 会新增取消出口、改记录流与既有录像。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    TargetCount,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    distance,
    is_slash,
    targets_within_attack_range,
)
from sgs.resolution import Control, Frame, Tag, add_tag
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef, _move_by_source
from .flow import FlowAnswers, PickCards, PickSeats, SkillFlow

__all__ = [
    "BENXI",
    "JIE_LIULI",
    "LIULI_FLOW",
    "JIE_MASHU",
    "JIE_YICONG",
    "FEIYING",
    "JIE_BENXI",
    "LIULI",
    "MASHU",
    "MAYUNLU_MASHU",
    "PANGDE_MASHU",
    "target_change_skill_definitions",
]

MASHU = hero_skill_id("machao", "mashu")
"""马超【马术】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

JIE_YICONG = hero_skill_id("jie_gongsunzan", "yicong")
"""界公孙瓒【义从】的全限定技能 id（M6 票 18；§十九 L2229–L2230）。"""

JIE_MASHU = hero_skill_id("jie_machao", "mashu")
"""界马超【马术】的全限定技能 id（M6 票 04）：§十九 L2107 与标准版逐字相同。

界版的第二条技能是【铁骑】（票 04 的第二个载体，住 :mod:`sgs.skills.response_window`）；
【马术】本身没有版本差异，故两个 id 共用**同一个** ``DISTANCE`` 回调——"一个技能一条定义"
不因武将版本分叉而成两份行为。"""

PANGDE_MASHU = hero_skill_id("pangde_mobile", "mashu")
"""火包·移动版庞德【马术】的全限定技能 id（huo-mobile-batch 票 01）。

牌面与两条既有【马术】逐字相同（`D:\\SGS_HEROS\\神话再临-火包\\庞德\\技能.txt`）⇒ 用户
2026-11-02 签"复用马超的马术"：**同一个** ``DISTANCE`` 回调、另立一条 ``SkillDef``（第三条
同形实例，与 :data:`JIE_MASHU` 同一姿态）。"""

MAYUNLU_MASHU = hero_skill_id("mayunlu", "mashu")
"""SP武将组5 马云騄【马术】的全限定技能 id（sp5-mayunlu-panjun 票 01）。

牌面与三条既有【马术】逐字相同（`D:\\SGS_HEROS\\SP武将组5\\马云騄\\技能.txt`）⇒ 同一个
``DISTANCE`` 回调、另立一条 ``SkillDef``（第四条同形实例，与庞德移动版同一姿态）。"""

FEIYING = hero_skill_id("shen_caocao_mobile", "feiying")
"""神曹操移动版【飞影】的全限定技能 id（lin-batch 票 01）。

牌面（用户 `技能.txt`，逐字）：「锁定技，其他角色计算与你的距离+1。」与移动版官网
（`sanguosha.cn` 的 hero-detail-143）逐字相同；一将成名官网写作「其他角色与你的距离+1」
（省了「计算」二字，同义）⇒ **无平台差异**、不构成 A1：本技能没有任何可被平台文本翻转的
结算量（客体侧 +1，无分支、无数值档位）。"""

LIULI = hero_skill_id("daqiao", "liuli")
"""大乔【流离】的全限定技能 id。"""

JIE_LIULI = hero_skill_id("jie_daqiao", "liuli")
"""界大乔【流离】的全限定技能 id（M6 票 17）：§十九 L2179–L2181 与标准版**逐字相同**。

与标准【流离】共用同一条钩子 / 流水线（同一个回调、同一份步骤声明）：界版只是另一个武将
id，行为不因版本分叉。"""

_LIULI_USED = "liuli_used"
"""帧私有键：本次使用里【流离】已经转移过目标（重入不再问、不再转移）。"""


# ---------------------------------------------------------------------------
# 马超【马术】：DISTANCE −1（主体侧）
# ---------------------------------------------------------------------------


def _mashu_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE`` 回调：持有者当**主体**时 −1（客体侧不改；下限由管道夹住）。"""
    del state
    if ctx.owner != ctx.subject:
        return current
    return current - 1


def _yicong_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """界公孙瓒【义从】的两条距离修正（§十九 L2229–L2230）。

    ①"你计算与其他角色的距离 -1" ⇒ 持有者是**主体**时 −1（与【马术】同一条）；
    ②"若你的体力值不大于 2，其他角色计算与你的距离 +1" ⇒ 持有者是**客体**且**当前
    体力值**（不是体力上限）≤ 2 时 +1。两条互斥（主体与客体不可能同时是同一人）。
    """
    if ctx.owner == ctx.subject:
        return current - 1
    if ctx.owner == ctx.target and state.player(ctx.owner).hp <= 2:
        return current + 1
    return current


# ---------------------------------------------------------------------------
# 一将成名-2014 吴懿【奔袭】：本回合使用过的牌数 ⇒ 距离 −X；条件成立时无视防具 / 【杀】多一个目标
# ---------------------------------------------------------------------------

BENXI = hero_skill_id("wuyi", "benxi")
"""一将成名-2014 吴懿【奔袭】的全限定技能 id（yijiang-2014-round3 票 01）。"""


def _benxi_cards_used_this_turn(state: GameState, seat: int) -> int:
    """① 的 X = 该座位**本回合使用过**的牌数。

    **不新增字段**：``Player.turn_card_suits`` 是引擎侧既有的"本回合使用过的牌"唯一真源
    （写点 :meth:`~sgs.engine.Engine._note_turn_card_suit`，``start_use`` 入口**逐次 append**、
    不去重、无花色的牌也记 ``None``、**打出不算**；作用域 = 本回合，回合末
    :meth:`~sgs.engine.Engine._clear_turn_effects` 遍历**所有座位**清空）。

    "于回合内"由 ``state.turn_seat == seat`` 表达（账本 L966 ④「回合没写谁的 ⇒ 仅自己的
    回合」）：别人回合里我响应【无懈可击】也会记进这个列表，但那不是"我的回合"。
    """
    if state.turn_seat != seat:
        return 0
    return len(state.player(seat).turn_card_suits)


def _benxi_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """① ``DISTANCE``：持有者当主体时 −X（X = 本回合已用牌数）。

    只读座位事实、**不调** :func:`~sgs.effects.distance`——否则这条回调会重入自己
    （② 的条件才调 ``distance()``，它在别的挂点上）。下限 1 由距离管道在链后夹住。
    """
    if ctx.owner != ctx.subject:
        return current
    used = _benxi_cards_used_this_turn(state, ctx.owner)
    if not used:
        return current
    return current - used


def _benxi_all_other_distances_are_one(state: GameState, owner: int) -> bool:
    """② 的条件：「你的回合内，若你计算与所有其他角色的距离均为 1」（现算、连续型）。

    逐座位现调 :func:`~sgs.effects.distance`（它含 ① 的 −X 与链尾的「下限 1」）；
    没有其他存活角色时条件不成立（无事可无视 / 无目标可加）。
    """
    if state.turn_seat != owner:
        return False
    others = [player.seat for player in state.players if player.alive and player.seat != owner]
    if not others:
        return False
    return all(distance(state, owner, seat) == 1 for seat in others)


def _benxi_target_count(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """② ``TARGET_COUNT``：【杀】的额定目标数 1 → 2（上限，不是强制）。

    只对【杀】家族（含火【杀】 / 雷【杀】 / 转化 / 虚拟）生效——牌面写的是「使用【杀】时」；
    这是**额度**修正，不是补池（详见账本 L884 / `shapes.md` §3.3 的 `TARGET_COUNT`）。
    """
    if ctx.owner != ctx.subject:
        return current
    if ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    if not _benxi_all_other_distances_are_one(state, ctx.owner):
        return current
    return int(current) + 1


def _benxi_ignore_armor(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """② ``EFFECTIVENESS`` / ``DAMAGE_TAKE_AMOUNT`` / ``DAMAGE_CAP``：置链内旁路标志。

    牌面「你无视其他角色的防具」**没有**限定【杀】（用户 2026-11-17 签 Q2b = 对所有牌）⇒
    与本模块外【青釭剑】的同形回调相比，少一句 ``_own_slash``；覆盖范围按一手
    （OL 现行【青釭剑】「令其防具的**效果**无效」）= 防具的**整体效果**（仁王盾式"无效"、
    藤甲的火焰 +1、白银狮子的封顶；【八卦阵】的"视为使用【闪】"属提供者面，由同一条技能的
    :func:`_benxi_ignore_armor_tag` 钩子写帧标签承接——那条不在这条链上）。
    """
    if ctx.owner != ctx.subject:
        return current
    if not _benxi_all_other_distances_are_one(state, ctx.owner):
        return current
    ctx.flags["ignore_armor"] = True
    return current


def _benxi_ignore_armor_condition(host: Any, frame: Frame) -> bool:
    """② 的 ``USE_AFTER_DESIGNATE_TARGET`` 钩子条件：**我用的、我的回合、所有距离均为 1**。

    修饰器版本用 ``ctx.owner`` 自校，钩子的条件拿不到实例，故读派发环在求值前写进
    :data:`MATCH_OWNER_KEY` 的持有者，再与帧的 ``source`` 比对（与【青釭剑】/【铁骑】同一条
    "关于我"判据）。距离判据复用 :func:`_benxi_all_other_distances_are_one`（连续型、查询期
    现算，含 ① 的 −X 与地板 1）——与三条伤害链上的修饰器**同一个条件函数**，不会两处漂移。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    return _benxi_all_other_distances_are_one(host.state, owner)


def _benxi_ignore_armor_tag(host: Any, frame: Frame) -> None:
    """② 给本次使用的**每个目标**打 ``Tag.ARMOR_IGNORED``（候选面那一半，round2 票 25）。

    与【青釭剑】的 ``_qinggang_ignore_armor_tag`` 同形、同一个标签；差别只在条件（本技能
    Q2b = **对所有牌**，故不判【杀】家族）。帧标签是**这一次使用**的属性：随本次使用
    （帧出栈）结构性消失，不进记录流；【闪】窗口（``steps._dodge_window``）据此把目标身上
    【八卦阵】/ 卧龙【八阵】的"视为使用【闪】"提供者收掉。
    """
    del host
    for target in tuple(frame.data.get("targets", ())):
        add_tag(frame, Tag.ARMOR_IGNORED, target=target)


def _feiying_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """神曹操移动版【飞影】的 ``DISTANCE`` 回调：持有者当**客体**时 +1（lin-batch 票 01）。

    逐字同义于界【义从】②的那三行，**去掉** ``hp <= 2`` 判据——飞影是**无条件常驻**的
    锁定技（牌面只有一个分句，没有"若…"前提）。方向是**客体侧**（「其他角色**计算与你的**
    距离 +1」⇒ 被算的是持有者）：抄【马术】的 ``ctx.subject`` 判据就等于"我算别人 −1"，
    方向正好相反。下限 1 由距离管道在链后夹住（:func:`~sgs.effects.distance`），本回调不自己夹。
    """
    del state
    if ctx.owner != ctx.target:
        return current
    return current + 1


# ---------------------------------------------------------------------------
# 大乔【流离】：转移目标
# ---------------------------------------------------------------------------


def _liuli_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**持有者是这次【杀】的目标**、使用者是别人、且本人还没转移过。

    ``condition`` 在派发器写 ``frame.data["skill_owner"]`` **之前**求值，故"持有者是谁"只能读
    派发器在求值前写好的 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（票 13）。
    漏掉这一判据，条件对**每个存活玩家**的实例都成立 ⇒ 任何角色成为【杀】的目标都会弹
    【流离】的"是否发动"，答发动后又被 :func:`_liuli_applies` 挡回 ⇒ 静默取消
    （票 13；用户实机报障的"无事发生"）。
    """
    del host
    if frame.data.get(_LIULI_USED):
        return False
    if frame.data.get(MATCH_OWNER_KEY) not in tuple(frame.data.get("targets", ())):
        return False
    if frame.data.get("source") == frame.data.get("actor"):
        return False
    card = frame.data.get("card")
    # "渠道牌仍是【杀】"按 M5-04 的家族判据（M5-05b-fix：火【杀】/ 雷【杀】同样可转移）。
    if card is None or not is_slash(behavior_for(card)):
        return False
    return True


def _liuli_target_pool(
    state: GameState, frame: Frame, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """可选的新目标：持有者攻击范围内、**不是**此【杀】的使用者、也不是持有者自己。

    ``chosen``（票 07 给 :data:`~sgs.skills.flow.SeatPool` 的签名）在本步恒为空——【流离】
    只选一名新目标，没有"第二问据此排除第一问"的多选语义。

    **攻击范围按"成本已付"重算**（W4-2，票面 §2 的同一条判据）：步骤只收集、成本在提交点
    才搬走，但规则读的是**弃掉那张牌之后**的场况——弃自己的武器时范围回落。已选的成本牌
    从前序答案读（清单渲染与校验器共用的 ``preactivation_answers`` 通道），交给
    :func:`~sgs.effects.targets_within_attack_range` 的 ``excluding`` 用同一公式重算。
    """
    del chosen
    user = frame.data.get("source")
    answers = frame.data.get("preactivation_answers") or {}
    cost = tuple(int(card_id) for card_id in (answers.get("card") or ()))
    return tuple(
        seat
        for seat in targets_within_attack_range(state, owner, excluding=cost)
        if seat != owner and seat != user
    )


def _liuli_applies(state: GameState, frame: Frame, owner: int) -> bool:
    """**这一步此刻存在吗**：迁移前手写回调里的全部提前返回，收在一处。

    票 13 之前，``condition`` 在求值期读不到持有者，故"是不是这次【杀】的目标""使用者是不是
    自己""有没有合法新目标"都必须在这里再判一次（否则每个实例都会走到效果体）。票 13 让
    :func:`_liuli_condition` 读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY` 自校后，过闸的
    实例已经只剩"本次【杀】的目标"；这一档作为**独立的第二道闸**原样保留（与
    :func:`_liuli_effect` 的复核同姿态），并继续承担"没有合法新目标 ⇒ 连成本都不问"：
    返回 ``False`` ⇒ 成本步的 ``face`` 给出**空元组**，执行器据此**一个询问都不发**
    （与迁移前 ``_liuli`` 的提前返回同形）。
    """
    if frame.data.get(_LIULI_USED):
        return False
    if owner not in tuple(frame.data.get("targets", ())):
        return False
    if frame.data.get("source") == owner:
        return False
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        return False
    return bool(_liuli_target_pool(state, frame, owner, ()))


def _liuli_cost_face(state: GameState, frame: Frame, owner: int, answers: FlowAnswers) -> Any:
    """步 1 的选取面：条件成立时是持有者本人，否则**空元组** = 这一步不存在（票 07 的形状）。

    .. note::

       这里用 ``face`` 而不是 ``condition`` 承载守卫：``face`` 的空元组是执行器里
       "**退化面不询问**"的唯一入口，且它在两步流水线里**先于**步 2 的池求值发生——
       于是"没有合法新目标 ⇒ 连成本都不问"与迁移前逐字同形。
    """
    del answers
    return owner if _liuli_applies(state, frame, owner) else ()


def _liuli_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """提交点：弃成本（步 1 选中的牌）→ 转移目标 → 给新目标补发"成为目标时"。

    **成本搬牌在这里才发生**（N6"选择只收集、提交才生效"）：步 1 只收答案、不搬牌。
    ``liuli:cost_moved`` 帧内游标原样保留——搬牌可能内联派发"失去牌后"并挂起，
    ``move_in_frame`` 返回 ``"stay"`` 时**原样透传**；重入时跳过搬牌、直接续跑转移与补派发
    （迁移前的注释与语义一字不差）。

    与【反馈】的 :func:`~sgs.skills.damage_after._fankui_effect` 同姿态：效果体**独立复核**
    "持有者仍是本次使用的目标、使用者不是自己、渠道牌仍是【杀】"——守卫住"无论怎么走到
    这里都不会转移错"，不依赖步骤声明这一个前提。
    """
    owner = frame.data["skill_owner"]
    targets = tuple(frame.data.get("targets", ()))
    if owner not in targets or frame.data.get("source") == owner:
        return None
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        return None
    new_target = int(tuple(answers["seat"])[0])

    moved_key = "liuli:cost_moved"
    if not frame.data.get(moved_key):
        frame.data[moved_key] = True
        if (
            host.move_in_frame(
                frame,
                host.state.refs(answers["card"]),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=owner,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    frame.data[_LIULI_USED] = True
    frame.data["targets"] = tuple(new_target if seat == owner else seat for seat in targets)
    # 新目标也是"成为目标时"：对它补一条派发（它因此照常进响应窗口）。**载荷显式给
    # ``targets=(new_target,)``**，使这次记录与 `use_become_target` 重入时那一轮的键一致——
    # 于是那一步若再扫到新目标会被记录幂等吃掉（不会重复一条时机）。
    # 被转移掉的那一个由 `use_become_target` 的"已派发名单"保证不再派发。
    host.emit_timing(
        Timing.USE_BECOME_TARGET,
        actor=new_target,
        source=frame.data.get("source"),
        targets=(new_target,),
        frame_id=frame.frame_id,
        depth=host.stack.depth,
    )
    return "stay"


LIULI_FLOW = SkillFlow(
    name="liuli",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            # **有意保留现状**（票 08 §要做什么）：迁移前的两步都没有 `Pass`，取消的唯一
            # 出口是发动询问那一道。改成 True 会新增取消出口、改记录流与既有录像。
            optional=False,
            key="card",
            # 面 = 持有者本人（可见手牌 + 装备），与前手写的
            # ``selection_face(owner, (HAND, EQUIP), reveal_hand=True)`` 同源；空元组则
            # 表示"这一步此刻不存在"（不是这次【杀】的目标 / 没有合法新目标）。
            face=_liuli_cost_face,
        ),
        PickSeats(
            lo=1,
            hi=1,
            pool=_liuli_target_pool,
            optional=False,
            key="seat",
        ),
    ),
    effect=_liuli_effect,
)
"""【流离】的两步流水线（票 08）：弃一张牌（手牌 / 装备区）→ 选新目标（``seat:N``）。

成本搬牌、目标替换与补派发都在 :func:`_liuli_effect`（提交点）里做；步 2 的
``PickSeats`` 让新目标以 ``SELECT_OPTION`` 的 ``seat:N`` 表达（票 08 的用户可见交互变更）。
"""


def _liuli_definition(skill_id: str) -> SkillDef:
    """【流离】的 :class:`SkillDef`；标准 / 界两个武将 id 各取一份（同一钩子 + 同一条流水线）。"""
    return SkillDef(
        skill_id=skill_id,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        hooks=(
            Hook(
                Timing.USE_BECOME_TARGET,
                condition=_liuli_condition,
                flow=LIULI_FLOW,
            ),
        ),
        requirement="弃一张牌，把【杀】转移给另一名角色",
        step_requirements={
            "card": "弃置一张牌（手牌 / 装备区）",
            "seat": "选择将此【杀】转移给哪名其他角色",
        },
        preactivated=True,
    )


# ---------------------------------------------------------------------------
# 界一将成名-2014·移动版 界吴懿【奔袭】（jie-yijiang-2014-wuyi-caozhen 票 01；用户 2026-11-18 签）
#
#     出牌阶段开始时，你可以弃置任意张牌，令你本阶段：计算与其他角色的距离-X、使用的
#     下一张基本牌或普通锦囊牌可以额外指定至多X名你计算与其距离为1的角色为目标
#     （X为你以此法弃置的牌数），然后此牌结算结束后，若此牌造成过伤害，你摸五张牌。
#
# 三段落点（`shapes.md` §2「单步选牌 → 效果」+「钩子 + 修饰器」）：
#
# - **发动 / 代价**：`PHASE_PLAY_START` 上的「钩子 + 单步 SkillFlow」（弃置任意张，同
#   【制衡】的选牌步）；提交点里先搬牌（游标）、再 `Engine.arm_jie_benxi` 落位。
# - **本阶段两条修饰**：`DISTANCE`（主体侧 −X）与「下一张牌多指定」——后者 = `TARGET_COUNT`
#   （额度 +X）+ `TARGET_SET_FILTER`（**额外目标必须是你计算距离 1 的角色**；框架票 05 的
#   首个消费者）。两条都只在 `jie_benxi_armed` 且这张牌是「基本牌或普通锦囊牌」时生效。
# - **结算后摸五张**：`DAMAGE_AFTER_DEAL` 记"那张牌造成过伤害"（卡牌级，不逐点），
#   `RESOLVE_AFTER_END` 上一次使用收尾时兑现（**不问**：牌面没有「可以」）。
#
# 消费语义（用户 2026-11-18 签 Q1）：「下一张」= 紧接的那一次使用，用过即失效（不论是否
# 真的加了额外目标）；本阶段没再用这类牌 ⇒ 阶段末失效（`Engine.clear_jie_benxi`
# 与其它"本阶段"字段同一收尾点，距离那一半靠 `phase_seq` 惰性失效）。
# ---------------------------------------------------------------------------

JIE_BENXI = hero_skill_id("jie_wuyi_mobile", "benxi")
"""界吴懿移动版【奔袭】的全限定技能 id（与标准吴懿的 ``hero.wuyi.benxi`` **不同 id**）。"""

_JIE_BENXI_DISCARDED = "jie_benxi_discarded"
"""帧私有：那一步弃牌已经搬完（重入不重搬）。"""

_JIE_BENXI_FIVE = "jie_benxi_drawn"
"""帧私有：这次使用收尾时已经摸过五张（重入不重摸）。"""


def _jie_benxi_target_card(state: GameState, card: Any) -> bool:
    """这张牌是不是「**基本牌或普通锦囊牌**」（牌面这一句是官方用语 ⇒ 不含装备 / 延时锦囊）。

    读**产物**牌型（转化 / 视为使用同样算：账本 L948）。
    """
    if card is None:
        return False
    if behavior_for(card).is_delayed:
        return False
    return card_category(state.pack_id, card.type) in (Category.BASIC, Category.TRICK)


def _jie_benxi_active(state: GameState, seat: int) -> bool:
    """这个座位的【奔袭】本阶段落位还有效吗（``phase_seq`` 惰性复位）。"""
    player = state.player(seat)
    return player.jie_benxi_x > 0 and player.jie_benxi_seq == state.phase_seq


def _jie_benxi_condition(host: Any, frame: Frame) -> bool:
    """① 的条件：**我的出牌阶段刚开始** ∧ 我至少有一张牌可弃。

    自校两件：``MATCH_OWNER_KEY`` 指向我、``frame.data["source"]``（= 当前回合角色）是我。
    一张牌都没有（手牌 ∪ 装备区皆空）⇒ 候选阶段就不产出（不发没有候选的询问）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if frame.data.get("source") != owner:
        return False
    player = host.state.player(owner)
    return bool(player.hand) or bool(player.equip)


def _jie_benxi_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：先弃置选中的牌（游标 + 按来源区分组），再落位本阶段的两条加成。"""
    owner = frame.data.get("skill_owner")
    if owner is None:
        owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return None
    owner = int(owner)
    cards = tuple(int(card_id) for card_id in answers.get("card", ()))
    if not cards:
        return None
    if not frame.data.get(_JIE_BENXI_DISCARDED):
        control = _move_by_source(
            host,
            frame,
            cards,
            to_zone=ZoneRef(Zone.DISCARD),
            owner=owner,
            key="jie_benxi:cost",
        )
        if control == "stay":
            return "stay"
        frame.data[_JIE_BENXI_DISCARDED] = True
    host.arm_jie_benxi(owner, len(cards))
    return None


JIE_BENXI_FLOW = SkillFlow(
    name="jie_benxi",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=None,
            key="card",
            optional=False,
        ),
    ),
    effect=_jie_benxi_effect,
)
"""【奔袭】的单步流水线：弃置任意张（手牌 ∪ 装备区、至少 1 张）→ 落位。"""


def _jie_benxi_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """「计算与其他角色的距离 -X」（主体侧、加式；下限 1 由距离管道在链后夹住）。"""
    if ctx.owner != ctx.subject or not _jie_benxi_active(state, ctx.owner):
        return current
    return current - state.player(ctx.owner).jie_benxi_x


def _jie_benxi_extra_count(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """「下一张基本牌 / 普通锦囊牌**可以额外指定至多 X 名**」= 目标数上限 +X。

    只对 ``TargetCount.RATED`` 的牌生效（全体牌本来就没有"额外目标"这个位置，与
    ``TARGET_COUNT`` 的既有口径一致）；加成只在**还没被消费**时生效。
    """
    if ctx.owner != ctx.subject:
        return current
    player = state.player(ctx.owner)
    if not player.jie_benxi_armed or player.jie_benxi_seq != state.phase_seq:
        return current
    if not _jie_benxi_target_card(state, ctx.card):
        return current
    return current + player.jie_benxi_x


def _jie_benxi_target_set(state: GameState, ctx: ModifierContext, current: Any) -> bool:
    """「额外指定至多 X 名**你计算与其距离为 1** 的角色」= 组合级资格（框架票 05）。

    判据：这一组里**距离 > 1** 的座位数不超过**额定目标数**（多出来的那些就是"额外目标"，
    必须都在距离 1 上）。全体牌（``ALL_ALIVE*``）不查这条链（没有额外目标的位置）⇒ 直接放行。
    """
    if not current:
        return current
    if ctx.owner != ctx.subject:
        return current
    player = state.player(ctx.owner)
    if not player.jie_benxi_armed or player.jie_benxi_seq != state.phase_seq:
        return current
    if not _jie_benxi_target_card(state, ctx.card):
        return current
    behavior = behavior_for(ctx.card)
    rule = behavior.target_rule
    if rule.count is not TargetCount.RATED:
        return current
    combo = tuple(ctx.use_targets or ())
    far = sum(1 for seat in combo if distance(state, ctx.owner, int(seat)) > 1)
    return far <= int(rule.rated_targets)


def _jie_benxi_consume(host: Any, frame: Frame) -> Any:
    """``USE_ON_USE``（强制段）：这一次使用就是"下一张"⇒ 消费掉加成（用过即失效）。"""
    owner = frame.data.get("skill_owner")
    if owner is None:
        return None
    owner = int(owner)
    source = frame.data.get("source")
    card = frame.data.get("card")
    if source is None or int(source) != owner or card is None:
        return None
    if not _jie_benxi_active(host.state, owner):
        return None
    if not _jie_benxi_target_card(host.state, card):
        return None
    host.consume_jie_benxi(owner, int(card.card_id))
    return None


def _jie_benxi_note_damage(host: Any, frame: Frame) -> Any:
    """``DAMAGE_AFTER_DEAL``（强制段）：消费那张牌**造成过伤害**（卡牌级、不逐点）。"""
    owner = frame.data.get("skill_owner")
    card = frame.data.get("card")
    if owner is None or card is None:
        return None
    owner = int(owner)
    player = host.state.player(owner)
    if not player.jie_benxi_card:
        return None
    if int(card.card_id) != int(player.jie_benxi_card):
        return None
    host.note_jie_benxi_dealt(owner)
    return None


def _jie_benxi_done(host: Any, frame: Frame) -> Any:
    """``RESOLVE_AFTER_END``（强制段）：那张牌结算完了 ⇒ 造成过伤害就摸五张。"""
    owner = frame.data.get("skill_owner")
    card = frame.data.get("card")
    if owner is None or card is None:
        return None
    owner = int(owner)
    player = host.state.player(owner)
    if not player.jie_benxi_card or int(card.card_id) != int(player.jie_benxi_card):
        return None
    if not player.jie_benxi_dealt:
        return None
    if frame.data.get(_JIE_BENXI_FIVE):
        return None
    frame.data[_JIE_BENXI_FIVE] = True
    return host.draw_for_hand(frame, owner, 5)


def target_change_skill_definitions() -> tuple[SkillDef, ...]:
    """K 族【马术】（标准 / 界两条）与 M 族【流离】（标准 / 界两条）的 :class:`SkillDef`。

    【马术】锁定（技能面写"锁定技"）⇒ ``locked=True``、落 ``DISTANCE`` 修饰器；
    【流离】非锁定（"你可以"）⇒ ``locked=False``、落 ``USE_BECOME_TARGET`` 钩子（票 08 起
    该钩子声明 ``flow=LIULI_FLOW``）。四条 ``carrier = HERO``（优先级档由载体派生）、
    ``skill_type = NORMAL``。界版两条都是"同一份声明、另一个武将 id"（票 04 / 票 17）。
    """
    return (
        SkillDef(
            skill_id=MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
            requirement="计算与其他角色的距离时 -1",
        ),
        # 界马超【马术】（M6 票 04）：牌面与标准版逐字相同 ⇒ 同一个回调、同一条链。
        SkillDef(
            skill_id=JIE_MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
            requirement="计算与其他角色的距离时 -1",
        ),
        # 火包·移动版庞德【马术】（huo-mobile-batch 票 01）：同一份牌面、同一个回调。
        SkillDef(
            skill_id=PANGDE_MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
            requirement="计算与其他角色的距离时 -1",
        ),
        # SP武将组5 马云騄【马术】（sp5-mayunlu-panjun 票 01）：同一份牌面、同一个回调。
        SkillDef(
            skill_id=MAYUNLU_MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
            requirement="计算与其他角色的距离时 -1",
        ),
        _liuli_definition(LIULI),
        # 界大乔【流离】（M6 票 17）：牌面与标准版逐字相同 ⇒ 同一条钩子 / 流水线。
        _liuli_definition(JIE_LIULI),
        # 界公孙瓒【义从】（M6 票 18）：①主体侧 -1；②客体侧在体力 ≤ 2 时 +1。
        SkillDef(
            skill_id=JIE_YICONG,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _yicong_distance),),
            requirement="①你计算与其他角色的距离 -1；②若你的体力值不大于 2，其他角色计算与你的距离 +1",
        ),
        # 一将成名-2014 吴懿【奔袭】（yijiang-2014-round3 票 01）：一条锁定技、五个修饰点——
        # ① DISTANCE（−X，X = 本回合已用牌数）；② TARGET_COUNT（【杀】额度 1 → 2）与
        # EFFECTIVENESS / DAMAGE_TAKE_AMOUNT / DAMAGE_CAP（无视防具，照【青釭剑】的三挂点）。
        # 零询问；一条强制钩子（锁定技 ⇒ 不问"是否发动"）：② 的「无视防具」在**候选面**上的
        # 那一半（【八卦阵】/【八阵】的提供者）与【青釭剑】走同一个帧标签。
        SkillDef(
            skill_id=BENXI,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_AFTER_DESIGNATE_TARGET,
                    handler=_benxi_ignore_armor_tag,
                    condition=_benxi_ignore_armor_condition,
                ),
            ),
            modifiers=(
                Modifier(ModifierPoint.DISTANCE, _benxi_distance),
                Modifier(ModifierPoint.TARGET_COUNT, _benxi_target_count),
                Modifier(ModifierPoint.EFFECTIVENESS, _benxi_ignore_armor),
                Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _benxi_ignore_armor),
                Modifier(ModifierPoint.DAMAGE_CAP, _benxi_ignore_armor),
            ),
            requirement=(
                "锁定技，①当你于回合内使用牌时，本回合你计算与其他角色的距离-1；"
                "②你的回合内，若你计算与所有其他角色的距离均为1，你无视其他角色的防具且"
                "使用【杀】时可以多选择一个目标。"
            ),
        ),
        # 界一将成名-2014·移动版 界吴懿【奔袭】（jie-yijiang-2014-wuyi-caozhen 票 01；用户
        # 2026-11-18 签）：出牌阶段开始时的单步流水线（弃任意张）+ 三条修饰器（距离 −X /
        # 目标数 +X / 组合级资格 = 额外目标必须距离 1，框架票 05 的首个消费者）+ 三条强制段
        # （消费加成 / 记"造成过伤害" / 结算后摸五张）。非锁定：① 牌面写「可以」。
        SkillDef(
            skill_id=JIE_BENXI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.PHASE_PLAY_START,
                    flow=JIE_BENXI_FLOW,
                    condition=_jie_benxi_condition,
                ),
                Hook(
                    Timing.USE_ON_USE,
                    handler=_jie_benxi_consume,
                    forced=True,
                ),
                Hook(
                    Timing.DAMAGE_AFTER_DEAL,
                    handler=_jie_benxi_note_damage,
                    forced=True,
                ),
                Hook(
                    Timing.RESOLVE_AFTER_END,
                    handler=_jie_benxi_done,
                    forced=True,
                ),
            ),
            modifiers=(
                Modifier(ModifierPoint.DISTANCE, _jie_benxi_distance),
                Modifier(ModifierPoint.TARGET_COUNT, _jie_benxi_extra_count),
                Modifier(ModifierPoint.TARGET_SET_FILTER, _jie_benxi_target_set),
            ),
            preactivated=True,
            requirement=(
                "出牌阶段开始时，你可以弃置任意张牌，令你本阶段：计算与其他角色的距离-X、"
                "使用的下一张基本牌或普通锦囊牌可以额外指定至多X名你计算与其距离为1的角色"
                "为目标（X为你以此法弃置的牌数），然后此牌结算结束后，若此牌造成过伤害，"
                "你摸五张牌。"
            ),
            step_requirements={"card": "弃置任意张牌"},
        ),
        # 神曹操移动版【飞影】（lin-batch 票 01）：纯修饰器 + 锁定 ⇒ 客体侧无条件 +1。
        # 声明形状抄【马术】，**判据方向抄【义从】②**（`ctx.owner == ctx.target`）。
        SkillDef(
            skill_id=FEIYING,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _feiying_distance),),
            requirement="锁定技，其他角色计算与你的距离+1。",
        ),
    )
