r"""P 族：限制豁免与【咆哮】②（M6 票 02 / 票 05）——【咆哮】/【奇才】/ 界张飞【咆哮】。

规则原文：§九 L979 / L998，§十七 L1640–1695；界张飞的 §十九 L2075–L2081。
三条都是**锁定技**（牌面没有"你可以"），故它们没有"是否发动"询问：

=====================  ========================  ============================
技能                   挂点                      语义
=====================  ========================  ============================
张飞【咆哮】           ``COUNT_LIMIT``           你于出牌阶段内使用【杀】无次数限制
界张飞【咆哮】①        ``COUNT_LIMIT``           同上（共用同一条回调）
界张飞【咆哮】②        ``SLASH_CANCELED``（接缝） 自己被【闪】抵消后挂上"下一次 +1"
                       ``DAMAGE_DEAL_AMOUNT``    本回合第一个【杀】伤害 +1
                       ``DAMAGE_AFTER_TAKE``     造成伤害之后消费该标记
黄月英【奇才】          ``DISTANCE_LIMIT``        你使用锦囊牌无距离限制
=====================  ========================  ============================

两处判据都读**牌面事实**而不是牌名白名单：

- 【咆哮】用 :func:`sgs.effects.is_slash`（【杀】家族：普通 / 火 / 雷 / 转化 / 虚拟），
  与【诸葛连弩】的 ``_unlimited_slash`` 同一判据——「泛指角色能力的无次数限制」
  （§十七 17.2 ④）不按具体牌名分档；
- 【奇才】读数据文件的**牌名大类** :func:`sgs.data.card_category`：`trick` 才豁免距离。
  "无距离限制"是**条件豁免**（§十七 17.3）：完全免除"距离 ≤ 上限"这一条，而不是把距离
  改成某个数——故回调返回 ``None``，由 ``check_targets`` 跳过距离比较；【杀】的攻击范围
  是另一条判据（``needs_distance``），不经过本挂点，因此【奇才】不会顺带放宽【杀】。

**界张飞【咆哮】②**（M6 票 05）的"下一次"语义在这里钉死（`06`（§5「程序化约定与偏离一手」）
的账本行由票 21 写回）：

- **触发**：自己使用的【杀】被【闪】抵消（``SeamKind.SLASH_CANCELED`` 只在"被抵消的
  【杀】"上开，抵消由【闪】的响应窗口判定，engine / 本模块都不认"闪"这个牌名）；
  挂的是**座位级**的一次性状态（``Player.paoxiao_bonus``，作用域 = 本回合），
  不随技能实例存亡撤回（D9 的一次性效果）；
- **消费**：本回合**第一个**由自己使用的【杀】造成的伤害事件，"造成伤害时"而不是
  "使用【杀】时"——被抵消 / 未造成伤害的那张【杀】**不消费**（与【酒】"使用即消费"
  相反）；非【杀】伤害不消费；多目标【杀】只有**第一个目标**的本次伤害 +1；
- **过期**：回合末与下一回合起点各清一次（与【酒】/【裸衣】同一收尾点）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    is_slash,
)
from sgs.resolution import Frame, invalidate_event_frame
from sgs.state import GameState, MoveReason, SlotKind, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import ActivationPlan, Hook, Seam, SeamKind, SkillDef, SkillInstance
from .flow import FlowAnswers, PickCards, SkillFlow
from .move_after import move_event

__all__ = [
    "JIE_PAOXIAO",
    "JIE_QICAI",
    "MENGJIN",
    "MENGJIN_FLOW",
    "PAOXIAO",
    "PAOXIAO_BONUS",
    "QICAI",
    "limits_activation_plans",
    "limits_skill_definitions",
]

PAOXIAO = hero_skill_id("zhangfei", "paoxiao")
"""张飞【咆哮】的全限定技能 id。"""

JIE_PAOXIAO = hero_skill_id("jie_zhangfei", "paoxiao")
"""界张飞【咆哮】的全限定技能 id（§十九 L2075–L2078：①无次数限制 + ②被抵消后的 +1）。"""

QICAI = hero_skill_id("huangyueying", "qicai")
"""黄月英【奇才】的全限定技能 id。"""

JIE_QICAI = hero_skill_id("jie_huangyueying", "qicai")
"""界黄月英【奇才】的全限定技能 id（§十九 L2106–L2107：①无距离限制 + ②取消装备区弃置）。"""

PAOXIAO_BONUS = 1
"""【咆哮】②的伤害增量（"此伤害 +1"）。"""


def _paoxiao_count_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT`` 回调：**这个座位使用【杀】不设上限**（``None`` = 无限）。

    ``ctx.owner == ctx.subject`` 是"这条技能属于使用者本人"的自校（修饰器链遍历的是
    **全体存活实例**，缺这一条会替别人解除次数上限）；牌面判据走 :func:`is_slash`，
    火 / 雷 / 转化 / 虚拟【杀】同族。``state`` 只作链式回调的统一签名，不用。
    标准张飞与界张飞共用同一条回调（①逐字相同），界版只是另一个武将 id。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return None


def _paoxiao_mark_bonus(host: Any, frame: Frame) -> Any:
    """``SLASH_CANCELED`` 接缝：**自己的**【杀】被抵消 ⇒ 挂上"下一次伤害 +1"。

    接缝对所有存活实例派发，故先自校"被抵消的这张【杀】的使用者是本实例持有者"
    （``skill_owner`` 由派发环在回调前写入，是"谁在跑"的帧私有坐标）。写入唯一入口
    :meth:`~sgs.engine.Engine.set_paoxiao_bonus`；**不发 delta**（回放按同一条
    【闪】的应答重建），与【酒】/【裸衣】同姿态。
    """
    source = frame.data.get("source")
    if source is None or frame.data.get("skill_owner") != source:
        return None
    host.set_paoxiao_bonus(source, True)
    return None


def _paoxiao_bonus_damage(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DAMAGE_DEAL_AMOUNT`` 回调：待消费标记在场且本次是**自己使用的【杀】** ⇒ +1。

    只读不写（"造成伤害时 +1"）：消费发生在这次伤害结算之后的
    :func:`_paoxiao_consume_bonus`，故同一张多目标【杀】只有第一个目标的本次伤害吃到 +1，
    而"被抵消 / 伤害被防止"的帧（``amount <= 0``）不会白消费掉它。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not state.player(ctx.owner).paoxiao_bonus:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return current + PAOXIAO_BONUS


def _paoxiao_consume_bonus(host: Any, frame: Frame) -> Any:
    """``DAMAGE_AFTER_TAKE`` 钩子：这次伤害真的发生了 ⇒ 消费掉【咆哮】②的标记。

    进入本钩子时 ``amount`` 已是定稿值；伤害被防止 / 减为 0 时引擎不派发本时机
    （:func:`~sgs.engine.steps.damage_after_take` 的既有语义），故"没造成伤害"不会消费。
    判据与给加伤的回调同源（来源 = 持有者、渠道牌是【杀】族），不认具体牌名。
    """
    source = frame.data.get("source")
    if source is None or frame.data.get("skill_owner") != source:
        return None
    if frame.data.get("amount", 0) <= 0:
        return None
    card = frame.data.get("channel")
    if card is None or not is_slash(behavior_for(card)):
        return None
    host.set_paoxiao_bonus(source, False)
    return None


def _qicai_distance_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE_LIMIT`` 回调：**锦囊牌的距离上限不存在**（``None`` = 豁免距离条件）。

    判据 = 数据文件里的牌名大类为 ``trick``（延时 / 非延时同档：两者都写在同一张大类里，
    【兵粮寸断】这类"距离 1 以内"的延时锦囊因此自动覆盖，不需要第二份名单）。
    ``card_category`` 查不到牌名（虚拟牌漏配等）⇒ ``None`` ⇒ 视为非锦囊、原样返回
    ``current``（fail-closed：不豁免）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if card_category(state.pack_id, ctx.card.type) is not Category.TRICK:
        return current
    return None


def _jie_qicai_cancel(host: Any, frame: Frame) -> Any:
    """``EQUIPMENT_DISCARD`` 接缝：**其他角色弃置你装备区里的防具牌** ⇒ 事件帧无效。

    四段判据都不认牌名：

    1. **是我的装备区**（``move.from_zone.seat == 持有者``）——「你装备区里」；
    2. **是弃置**（去向 = 弃牌堆；接缝本身只为这个形状开，见 ``Engine.move_in_frame``）；
    3. **是其他角色**（``move.causer != 持有者``）——「其他角色弃置」；自己顶替 / 自弃不挡；
    4. **是防具牌**（``CardRef.slot is SlotKind.ARMOR``）。宝物半句**无载体**（D7：宝物槽位
       随木牛流马一起出局），故 `06`（§5「程序化约定与偏离一手」） 登记为偏离，实现只处理防具。

    成立则把**这个事件帧**标成无效（:func:`~sgs.resolution.invalidate_event_frame`）：
    引擎读一次、整个移动被取消。**宝物牌未匹配**不是实现缺口，是裁定（见 `06`）。
    """
    del host
    owner = frame.data.get("skill_owner")
    move = move_event(frame)
    if owner is None or move is None:
        return None
    if move.from_zone.zone is not Zone.EQUIP or move.from_zone.seat != owner:
        return None
    if move.to_zone.zone is not Zone.DISCARD:
        return None
    if move.causer is None or move.causer == owner:
        return None
    if not any(card.slot is SlotKind.ARMOR for card in move.cards):
        return None
    invalidate_event_frame(frame)
    return None


# ---------------------------------------------------------------------------
# 火包·移动版庞德【猛进】（huo-mobile-batch 票 01）：接缝 + 一步选牌 + 弃置
# ---------------------------------------------------------------------------

MENGJIN = hero_skill_id("pangde_mobile", "mengjin")
"""庞德移动版【猛进】的全限定技能 id。

牌面权威 = 用户 `D:\\SGS_HEROS\\神话再临-火包\\庞德\\技能.txt`；OL 庞德页的第二个技能是
【鞬出】（另一条技能）⇒ **A1** 裁定票 04，用户 2026-11-02 签**读法 A：按移动版实现**。
"""

_MENGJIN_MATCHED = "mengjin_matched"
"""帧私有：本帧上【猛进】已经认过的 ``(持有者, 目标)``。

选牌步的**面**每次重入都重新求值，而提交点会把那张牌搬走——不认这个标记，挂起续跑会
看到空面、把整条流水线判成取消（与【趫猛】/【利驭】同一条理由）。记的是**目标座号**而
不是布尔：同一张多目标【杀】里每个被【闪】抵消的目标各触发一次，而 RESOLVE 帧跨目标
复用，布尔标记会串到下一个目标身上。
"""


def _mengjin_target(state: GameState, frame: Frame) -> int | None:
    """当前【杀】被【闪】抵消的那个**目标**（C 段的 ``targets[cursor]``）；无目标 ⇒ ``None``。"""
    del state
    targets = tuple(frame.data.get("targets", ()))
    if not targets:
        return None
    return int(targets[frame.data.get("cursor", 0)])


def _mengjin_face(state: GameState, frame: Frame, owner: int, answers: FlowAnswers) -> Any:
    """选牌步的 ``face``：**被抵消的目标**的手牌 + 装备区。

    「其」的先行词 = 句子里那个用【闪】抵消的目标角色（账本默认读法（二）②已裁「其他
    角色」不含自己）；「一张牌」没有区域限定 ⇒ 手牌 + 装备区，**不含判定区**（默认读法
    （一）②）。重入时那张牌可能已被搬走 ⇒ 认 :data:`_MENGJIN_MATCHED` 仍返回目标；
    真正没有牌可弃 ⇒ 空元组 = 无此步（候选阶段已由 plan 挡住，这是防御分支）。
    """
    del answers
    target = _mengjin_target(state, frame)
    if target is None:
        return ()
    if frame.data.get(_MENGJIN_MATCHED) == (owner, target):
        return target
    player = state.player(target)
    if not (player.hand or player.equip):
        return ()
    frame.data[_MENGJIN_MATCHED] = (owner, target)
    return target


def _mengjin_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """【猛进】的发动前提：**自己的**【杀】被抵消，且那个目标有牌可弃。

    接缝对所有存活实例派发，故先自校 ``frame.data["source"] == instance.owner``（与
    【贯石斧】的 ``_axe_plan`` 同一姿态）。"关于我"这条判据住在 plan 里而不是
    ``Hook.condition``：接缝没有钩子面的条件字段，plan 就是它的"付不付得起 + 关于谁"。
    """
    if frame.data.get("source") != instance.owner:
        return ActivationPlan(optional=True, available=False)
    target = _mengjin_target(state, frame)
    if target is None:
        return ActivationPlan(optional=True, available=False)
    if frame.data.get(_MENGJIN_MATCHED) == (instance.owner, target):
        return ActivationPlan(optional=True, available=True)
    player = state.player(target)
    return ActivationPlan(optional=True, available=bool(player.hand or player.equip))


def _mengjin_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：把选中的那张牌**弃置**（弃置者是庞德，牌主是被抵消的目标）。

    搬牌会内联派发「失去牌后」（目标失去最后一张手牌 ⇒ 【连营】一类）⇒ ``"stay"`` 原样
    透传，续跑由 ``move_in_frame`` 的重入守卫与 :data:`_MENGJIN_MATCHED` 共同保证"不重弃"。
    """
    owner = frame.data.get("skill_owner")
    picks = tuple(answers["card"])
    target = _mengjin_target(host.state, frame)
    if owner is None or target is None or not picks:
        return None
    card = host.state.card(int(picks[0]))
    control = host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=target,
        actor=owner,
    )
    return "stay" if control == "stay" else None


MENGJIN_FLOW = SkillFlow(
    name="mengjin",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            # 「弃置其一张牌」不是"可以不选"（"可以"管整条技能 ⇒ 放弃出口在发动询问），
            # 故这一步没有 Pass；中途面为空 = 整条取消、零副作用。
            optional=False,
            face=_mengjin_face,
            # 目标的手牌是**隐藏槽**（只有位置、没有牌面），装备区照旧可见——与【反馈】
            # /【趫猛】同一姿态（UI = 未知区域弹窗）。
            reveal_hand=False,
        ),
    ),
    effect=_mengjin_effect,
)
"""【猛进】的一步流水线：[是否发动] → 弃置被抵消目标的一张牌（手牌 / 装备区）。"""


def limits_activation_plans() -> Mapping[
    str, Callable[[GameState, Frame, SkillInstance], ActivationPlan]
]:
    """本模块技能的**发动形态**注册表（并入 :data:`sgs.skills.catalog.ACTIVATION_PLANS`）。

    【猛进】不是"裸问"：它的 ``available`` 要读帧（谁用的【杀】/ 目标有没有牌），故必须
    有一条 plan；三条锁定技（【咆哮】/【奇才】）不问"是否发动"，没有 plan。
    """
    return {MENGJIN: _mengjin_plan}


def limits_skill_definitions() -> tuple[SkillDef, ...]:
    """P 族的锁定技 + 火包移动版【猛进】的 :class:`~sgs.skills.catalog.SkillDef`。

    三条锁定技都 ``locked=True``（牌面写"锁定技"）⇒ 不进"是否发动"候选；``carrier = HERO``
    （优先级档由载体派生）、``skill_type = NORMAL``。界张飞【咆哮】①与标准版共用
    ``COUNT_LIMIT`` 回调，②另挂一条接缝 + 两条回调（加伤 / 消费）。
    【猛进】是**非锁定**的接缝技（"你可以"）⇒ 有 plan、发"是否发动"，接缝上挂 ``flow``
    （huo-mobile-batch 票 01；本仓第一条"武将底盘 + 接缝 + flow"）。
    """
    return (
        SkillDef(
            skill_id=PAOXIAO,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _paoxiao_count_limit),),
            requirement="你于出牌阶段内使用【杀】无次数限制",
        ),
        SkillDef(
            skill_id=JIE_PAOXIAO,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.DAMAGE_AFTER_TAKE, handler=_paoxiao_consume_bonus),),
            seams=(Seam(SeamKind.SLASH_CANCELED, handler=_paoxiao_mark_bonus),),
            modifiers=(
                Modifier(ModifierPoint.COUNT_LIMIT, _paoxiao_count_limit),
                Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _paoxiao_bonus_damage),
            ),
            requirement=(
                "你使用【杀】无次数限制；当你使用的【杀】被【闪】抵消后，"
                "本回合下一次【杀】造成伤害时，此伤害 +1"
            ),
        ),
        SkillDef(
            skill_id=QICAI,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE_LIMIT, _qicai_distance_limit),),
            requirement="你使用锦囊牌无距离限制",
        ),
        SkillDef(
            skill_id=JIE_QICAI,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE_LIMIT, _qicai_distance_limit),),
            seams=(Seam(SeamKind.EQUIPMENT_DISCARD, handler=_jie_qicai_cancel),),
            requirement=(
                "你使用锦囊牌无距离限制；当其他角色弃置你装备区里的防具牌时，取消之"
            ),
        ),
        # 火包·移动版庞德【猛进】（huo-mobile-batch 票 01）：接缝 + 一步选牌 → 弃置。
        # 牌面「当你使用的【杀】被目标角色使用【闪】抵消时，你可以弃置其一张牌」。
        SkillDef(
            skill_id=MENGJIN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            seams=(Seam(SeamKind.SLASH_CANCELED, flow=MENGJIN_FLOW),),
            requirement="当你使用的【杀】被目标角色使用【闪】抵消时，你可以弃置其一张牌",
            step_requirements={"card": "弃置其一张牌（手牌 / 装备区）"},
        ),
    )
