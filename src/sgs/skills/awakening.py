r"""觉醒技（M6 票 06；§四 4.12 L357–L370 / §十九 L2127–L2161）。

觉醒技的机制面只有三条（票面「要做什么」）：

1. **强制发动**：条件满足时不发"是否发动"询问，直接生效。落点在
   :func:`sgs.skills.catalog._dispatch` 的 ``forced = locked or AWAKENING``——觉醒技
   **不并进 ``locked``**（那是"锁定技"这一规则类型，同时被"所有非锁定技失效"读取），
   故它仍可被座位级失效挡住；"不问"这一条按 :class:`~sgs.data.SkillType` 判。
2. **减 1 点体力上限**：走 :meth:`~sgs.engine.Engine.reduce_hp_max` 这个唯一写入口
   （规则 §四 4.15：不自动回血；满体力时体力值同步下调到新上限）。
3. **通过"获得"来源授予 ☆ 技能**：走 :meth:`~sgs.engine.Engine.grant_skill`；☆ 技能的
   实例只来自 ``Player.granted_skills``（武将牌载体在 :func:`~sgs.skills.catalog.resolve_instances`
   里被 ``hero_card_skill_ids`` 排除），故未觉醒时不存在。

**"已觉醒"位**落 per-instance 的 ``GameState.skill_state``（:data:`sgs.skills.catalog._AWAKENED`），
与 A 族阶段位 / 限定技"已用"位互不重叠；出牌阶段复位不清它。位在 effect 执行完之后才落，
所以 effect 挂起（【勤学】的选项）时重入还能续跑同一个实例。

本模块同时承载**界徐庶的【诛害】**：它是觉醒载体的另一半（票面"载体：界徐庶【诛害】
【潜心】→☆【荐言】"），形态是"其他角色的结束阶段"的触发技——挂在
``Timing.PHASE_FINISH``（由引擎的阶段体派发为**当前回合角色**的帧），条件自校
"持有者不是当前回合角色"，然后由持有者对其使用一张【杀】。

=========================  ==========  ============  ==============================
技能                       类型        挂点          效果
=========================  ==========  ============  ==============================
界徐庶【潜心】             觉醒技      DAMAGE_AFTER_DEAL  上限 -1 → 获得 ☆【荐言】
界吕蒙【勤学】             觉醒技      PHASE_PREPARE / PHASE_FINISH（同一技能两条）
                                                   上限 -1 → 摸两张 / 回复 1 点 → 获得 ☆【攻心】
界徐庶【诛害】             普通技      PHASE_FINISH  对其使用一张【杀】
=========================  ==========  ============  ==============================
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import (
    CarrierKind,
    check_count,
    check_targets,
    satisfies_required,
)
from sgs.state import (
    FrameKind,
    GameState,
    MoveReason,
    Phase,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    ActivationPlan,
    Hook,
    MATCH_OWNER_KEY,
    SkillDef,
)
from .flow import PickCards, SkillFlow

__all__ = [
    "LVMENG_GONGXIN",
    "LVMENG_QINXUE",
    "QIANXUE_FLOW",
    "QINXUE_FLOW",
    "XUSHU_JIANYAN",
    "XUSHU_QIANXUE",
    "XUSHU_ZHUHAI",
    "ZHUHAI_FLOW",
    "awakening_activation_plans",
    "awakening_skill_definitions",
]

XUSHU_QIANXUE = hero_skill_id("jie_xushu", "qianxue")
"""界徐庶【潜心】的全限定技能 id（§十九 L2130）。"""

XUSHU_JIANYAN = hero_skill_id("jie_xushu", "jianyan")
"""界徐庶 ☆【荐言】的全限定技能 id（§十九 L2132；由【潜心】觉醒后获得）。"""

LVMENG_QINXUE = hero_skill_id("jie_lvmeng", "qinxue")
"""界吕蒙【勤学】的全限定技能 id（§十九 L2152）。"""

LVMENG_GONGXIN = hero_skill_id("jie_lvmeng", "gongxin")
"""界吕蒙 ☆【攻心】的全限定技能 id（§十九 L2158；由【勤学】觉醒后获得）。"""

XUSHU_ZHUHAI = hero_skill_id("jie_xushu", "zhuhai")
"""界徐庶【诛害】的全限定技能 id（§十九 L2127–L2129）。"""

_AWAKENING_COMMITTED = "awakening_committed"
"""帧私有：**在飞**的觉醒技的持有者（键 = 座位号，每个觉醒技自己的帧里只写一次）。

觉醒技的"已觉醒"位要等 effect 执行完才落（`_dispatch`），但 effect 可以挂起
（【勤学】的回复帧）；而 effect 自己会改变条件里的状态（体力 / 上限 / 手牌），
重入时**不能再按新状态重算条件**——否则"回复 1 点体力"之后手牌数减体力值不再 ≥ 2，
条件翻假、effect 永远回不来（`已觉醒` 位也就永远不落）。故 effect 一进来就把本座位
记在这里，:func:`_qianxue_condition` / :func:`_qinxue_condition` 见到它就直接放行
（"在飞帧继续"的局部落点，见 spec D10）。帧私有数据不进记录流。"""

_QIANXUE_DONE = "qianxue_done"
"""帧私有：本次觉醒的两步（上限 / 授予）已经走过（重入不重复）。"""

_QINXUE_HP_MAX_DONE = "qinxue_hp_max_done"
"""帧私有：【勤学】的"减 1 点体力上限"已经做过（重入不重复）。"""

_QINXUE_EFFECT_DONE = "qinxue_effect_done"
"""帧私有：【勤学】的选项效果（摸两张 / 压回复帧）已经启动（重入不重复）。"""

_QINXUE_GRANTED = "qinxue_granted"
"""帧私有：【勤学】已经授予 ☆【攻心】（重入不重复）。"""

_ZHUHAI_STARTED = "zhuhai_started"
"""帧私有：这次【诛害】已经开出了那次使用（重入不再开第二次）。"""


# ---------------------------------------------------------------------------
# 界徐庶【潜心】：当你造成伤害后，若你已受伤 ⇒ 上限 -1，获得【荐言】
# ---------------------------------------------------------------------------


def _qianxue_condition(host: Any, frame: Any) -> bool:
    """【潜心】的触发条件（§十九 L2130）。

    两个条件在**同一时机**上是**合取**，先判归属、再判状态（`06` §5「程序化约定与偏离一手」裁定 1）：

    - **归属**（"当你造成伤害后"）：本次伤害的来源是持有者本人。判据读派发器在条件求值
      期写好的 :data:`MATCH_OWNER_KEY`（伤害帧的 ``source`` 是来源座位，不是实例持有者）；
    - **状态**（"若你已受伤"）：``hp < hp_max``。

    任一不成立就整条不触发——没有"只减上限 / 只给技能"的部分效果。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    if frame.data.get(_AWAKENING_COMMITTED) == owner:
        return True  # 在飞：effect 已经提交，重入不再重算"已受伤"
    player = host.state.player(int(owner))
    return player.hp < player.hp_max


def _qianxue_effect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：上限 -1 → 获得 ☆【荐言】（"然后"的次序；两件事都只做一次）。"""
    del answers
    owner = frame.data["skill_owner"]
    frame.data[_AWAKENING_COMMITTED] = owner
    if not frame.data.get(_QIANXUE_DONE):
        host.reduce_hp_max(frame, owner, 1)
        host.grant_skill(owner, XUSHU_JIANYAN, frame=frame)
        frame.data[_QIANXUE_DONE] = True
    return None


QIANXUE_FLOW = SkillFlow(name="qianxue", steps=(), effect=_qianxue_effect)
"""【潜心】的零步流水线：强制派发 ⇒ 上限 -1 ⇒ 授予 ☆【荐言】。"""


# ---------------------------------------------------------------------------
# 界吕蒙【勤学】：准备阶段或结束阶段，手牌数 - 体力值 ≥ 2 ⇒ 上限 -1 + 二选一 + ☆【攻心】
# ---------------------------------------------------------------------------

QINXUE_DRAW = "draw_two"
QINXUE_RECOVER = "recover_one"
"""【勤学】的两项：1. 摸两张牌；2. 回复 1 点体力（§十九 L2153–L2154）。"""

QINXUE_OPTIONS: tuple[str, ...] = (QINXUE_DRAW, QINXUE_RECOVER)


def _qinxue_condition(host: Any, frame: Any) -> bool:
    """【勤学】的触发条件：**你的**准备 / 结束阶段 ∧ 手牌数减体力值 ≥ 2。

    "准备阶段**或**结束阶段"是**同一条技能的两条挂接**（`06` §5「程序化约定与偏离一手」裁定 2）：两个
    ``Hook`` 共用本条件与同一条流水线，不是两条技能。持有者自校读
    :data:`MATCH_OWNER_KEY`；阶段体帧的 ``source`` 是当前回合角色，故"你的阶段"=
    ``source == 持有者``。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    if frame.data.get(_AWAKENING_COMMITTED) == owner:
        return True  # 在飞：effect 已经提交，重入不再重算"手牌数减体力值"
    player = host.state.player(int(owner))
    return len(player.hand) - player.hp >= 2


def _qinxue_push_recover(host: Any, frame: Any, *, owner: int) -> None:
    """压一个 ``RECOVER`` 子帧（回复 1 点；超过上限由帧夹住）。"""
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": owner,
            "targets": (owner,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _qinxue_timing(state: GameState) -> Timing:
    """【勤学】的选项询问该挂哪个时机：由**当前阶段**派生（本技能挂在准备 / 结束两个阶段体上）。

    询问的 ``timing`` 只进记录流 / 界面上下文，不参与结算；两个挂接各在两个阶段体里派发，
    故 ``state.phase`` 是这两者之一的确定事实（``_phase_prepare`` / ``_phase_finish`` 都先
    写 phase 再派发）。
    """
    return Timing.PHASE_PREPARE if state.phase is Phase.PREPARE else Timing.PHASE_FINISH


def _qinxue_effect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：上限 -1 → **再问**二选一 → 摸两张 / 回复 1 点 → 获得 ☆【攻心】。

    规则次序是"你减 1 点体力上限**并**选择一项"（§十九 L2152–L2154），且选项的信息面受
    上限变化影响（满体力减上限会同步下调体力值），故选项必须在**减上限之后**问——这不是
    ``SkillFlow`` 的步骤能表达的（步骤只收集、effect 之后才结算），故本技能走**零步流水线**
    + 在 effect 里手写这次 ``ask_option``（与【刚烈】的"判定后问来源"同一形状）。

    重入纪律（帧私有键）：上限只减一次；选项答案由 ``ask_option`` 的帧内缓存给出，重入不再
    问；回复帧出栈后跳过选项效果、只补授予；授予只做一次。
    """
    del answers  # 零步流水线：答案全部来自 effect 里的手写询问
    owner = frame.data["skill_owner"]
    frame.data[_AWAKENING_COMMITTED] = owner
    if not frame.data.get(_QINXUE_HP_MAX_DONE):
        host.reduce_hp_max(frame, owner, 1)
        frame.data[_QINXUE_HP_MAX_DONE] = True
    answer = host.ask_option(
        frame,
        owner,
        _qinxue_timing(host.state),
        skill_id="qinxue:choice",
        options=QINXUE_OPTIONS,
    )
    if host.suspended(answer):
        return "stay"
    if not frame.data.get(_QINXUE_EFFECT_DONE):
        if answer.choice == QINXUE_RECOVER:
            frame.data[_QINXUE_EFFECT_DONE] = True
            _qinxue_push_recover(host, frame, owner=owner)
            return "stay"
        host.draw_for_hand(frame, owner, 2)
        frame.data[_QINXUE_EFFECT_DONE] = True
    if not frame.data.get(_QINXUE_GRANTED):
        host.grant_skill(owner, LVMENG_GONGXIN, frame=frame)
        frame.data[_QINXUE_GRANTED] = True
    return None


QINXUE_FLOW = SkillFlow(name="qinxue", steps=(), effect=_qinxue_effect)
"""【勤学】的零步流水线：上限 -1 → 选项询问 → 摸两张 / 回复 1 点 → 授予 ☆【攻心】。

零步是**有意的**：选项必须在减上限之后问，而 ``SkillFlow`` 的步骤一律先于 effect 收集，
表达不了"先变更状态、再问"。宣告面仍是一条 ``Hook`` / 一条流水线（两条挂接共用）。
"""


# ---------------------------------------------------------------------------
# 界徐庶【诛害】：其他角色的结束阶段，若其本回合造成过伤害 ⇒ 你可以对其使用一张【杀】
# ---------------------------------------------------------------------------


def _zhuhai_target(frame: Any) -> int | None:
    """这次【诛害】的**目标** = 当前回合角色（"其他角色的结束阶段" ⇒ 帧的 ``source``）。"""
    source = frame.data.get("source")
    return None if source is None else int(source)


def _zhuhai_condition(host: Any, frame: Any) -> bool:
    """【诛害】的触发条件：**别人**的结束阶段 ∧ 其本回合造成过伤害。

    持有者自校：``MATCH_OWNER_KEY != source``（自己的结束阶段不触发，也挡住"每个存活
    玩家的实例都被问一次"）。"造成过伤害"读 :meth:`Engine.note_damage_dealt` 记下的
    本回合座位级事实（回合末清）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    target = _zhuhai_target(frame)
    if owner is None or target is None or target == owner:
        return False
    return host.state.player(target).damage_dealt_this_turn


def _zhuhai_illegal_cards(
    state: GameState, frame: Any, owner: int, answers: Any
) -> tuple[int, ...]:
    """【诛害】候选里的**不可用**【杀】：距离照常判；次数闸门随 ``check_count`` 的适用范围走。

    ``PickCards`` 的 ``exclude_cards`` 与选择面同源：从**手牌**里排除掉"目标不在合法目标池
    里"的那些。判据直接调引擎既有的 :func:`~sgs.effects.check_count` /
    :func:`~sgs.effects.check_targets`，不另写一套——故【杀】的次数限制只在**持有者自己的
    出牌阶段**读计数（W5-04 用户裁定 2026-10-30）。本技能的使用发生在**别人的**结束阶段，
    那里 ``check_count`` 恒为 ``False``，不构成闸门。
    """
    del answers
    target = _zhuhai_target(frame)
    if target is None:
        return tuple(state.player(owner).hand)
    excluded: list[int] = []
    for card_id in state.player(owner).hand:
        card = state.card(card_id)
        if not satisfies_required(card, "slash"):
            excluded.append(card_id)
            continue
        if check_count(state, owner, card) or target not in check_targets(state, owner, card):
            excluded.append(card_id)
    return tuple(excluded)


def _zhuhai_plan(state: GameState, frame: Any, instance: Any) -> ActivationPlan:
    """发动形态："手里至少有一张**合法**的【杀】"才问（付不起 / 够不着就不问）。"""
    illegal = set(_zhuhai_illegal_cards(state, frame, instance.owner, {}))
    available = any(
        card_id not in illegal
        and satisfies_required(state.card(card_id), "slash")
        for card_id in state.player(instance.owner).hand
    )
    return ActivationPlan(optional=True, available=available)


def _zhuhai_effect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：把选中的【杀】**对其使用**（目标 = 当前回合角色，距离 / 次数已过闸）。

    "对其使用"是一次真正的使用（开响应窗口、计次数、进记录流），故走
    ``Engine.start_use``；目标只有当前回合角色一个人，不是玩家再选。
    """
    owner = frame.data["skill_owner"]
    target = _zhuhai_target(frame)
    picks = tuple(answers["card"])
    if not picks or target is None or frame.data.get(_ZHUHAI_STARTED):
        return None
    frame.data[_ZHUHAI_STARTED] = True
    host.start_use(
        host.state.card(int(picks[0])),
        source=owner,
        targets=(target,),
        use_timing=Timing.PHASE_FINISH,
    )
    return "stay"


ZHUHAI_FLOW = SkillFlow(
    name="zhuhai",
    steps=(
        PickCards(
            zones=(Zone.HAND,),
            lo=1,
            hi=1,
            optional=False,
            card_types=("slash",),
            exclude_cards=_zhuhai_illegal_cards,
        ),
    ),
    effect=_zhuhai_effect,
)
"""【诛害】的一步流水线：选一张合法的【杀】（手牌）→ 对其使用。"""


def awakening_activation_plans() -> dict[str, Any]:
    """觉醒族并入 ``ACTIVATION_PLANS`` 的条目（只有【诛害】需要"付得起才问"）。"""
    return {XUSHU_ZHUHAI: _zhuhai_plan}


_REQUIREMENTS: dict[str, str] = {
    "qianxue": "减 1 点体力上限，然后获得技能「荐言」（觉醒技：造成伤害后若你已受伤，强制发动）",
    "qinxue": "减 1 点体力上限并选择：摸两张牌 / 回复 1 点体力，然后获得技能「攻心」（觉醒技：准备或结束阶段手牌数减体力值至少为 2 时强制发动）",
    "zhuhai": "对其使用一张【杀】（距离限制照常适用）",
}
"""觉醒族三条技能的"需要做什么"文案（item 4）。"""

_STEP_REQUIREMENTS: dict[str, dict[str, str]] = {
    "qinxue": {"choice": "选择一项：1. 摸两张牌；2. 回复 1 点体力"},
}
"""多步觉醒技的逐步文案。"""


def awakening_skill_definitions() -> tuple[SkillDef, ...]:
    """觉醒族的 :class:`~sgs.skills.catalog.SkillDef`：两条觉醒技 + 界徐庶【诛害】。

    ``skill_type = AWAKENING`` 让派发环按"强制发动"处理（先于 ``locked`` 判据），
    ``locked`` 保持 ``False``——觉醒技不是锁定技，仍可被"所有非锁定技失效"覆盖。
    """
    return (
        SkillDef(
            skill_id=XUSHU_QIANXUE,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.AWAKENING,
            hooks=(
                Hook(
                    Timing.DAMAGE_AFTER_DEAL,
                    flow=QIANXUE_FLOW,
                    condition=_qianxue_condition,
                ),
            ),
            requirement=_REQUIREMENTS["qianxue"],
        ),
        SkillDef(
            skill_id=LVMENG_QINXUE,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.AWAKENING,
            # 同一条技能的**两条挂接**（准备阶段 / 结束阶段）：一个 SkillDef、两条 Hook，
            # 共用条件与流水线（`06` §5「程序化约定与偏离一手」裁定 2）。
            hooks=(
                Hook(
                    Timing.PHASE_PREPARE,
                    flow=QINXUE_FLOW,
                    condition=_qinxue_condition,
                ),
                Hook(
                    Timing.PHASE_FINISH,
                    flow=QINXUE_FLOW,
                    condition=_qinxue_condition,
                ),
            ),
            requirement=_REQUIREMENTS["qinxue"],
            step_requirements=_STEP_REQUIREMENTS["qinxue"],
        ),
        SkillDef(
            skill_id=XUSHU_ZHUHAI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.PHASE_FINISH,
                    flow=ZHUHAI_FLOW,
                    condition=_zhuhai_condition,
                ),
            ),
            requirement=_REQUIREMENTS["zhuhai"],
        ),
    )
