r"""兵势篇-节包武将技能（``bingshi_jie`` 包；01 票起 = 势赵云【摧锋】）。

本模块只装**兵势篇-节包**的武将技能声明；族级原语（钩子 / 步骤流水线 / 修饰器 / 纯虚拟牌）
一律复用既有实现，不新增机制。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 势赵云【武翊】= A 族，住 :mod:`sgs.skills.active`（出牌阶段主动技的候选 / 发动主体 /
  提供者都归那一族；它的 ④ 也是同一条声明上的钩子）；
- 势赵云【摧锋】= 本模块（``TURN_END`` 钩子 + 零步流水线 + 卡牌级加伤 + 本回合计数）。

【摧锋】（牌面权威 = 移动版官网 ``hero-detail-672``，逐字）：

    每轮限两次，每回合结束时，你可视为对当前回合角色使用一张伤害值+X的【决斗】
    （X为本回合所有人打出【杀】的次数）。

用户 2026-11-18 签的口径（票面「用户裁定」表）：

- **Q6 = B**（逐字「但看每回合（可以），但是决斗不能指定自己为目标，所以门槛在这里，
  不能发动」）：触发面仍是**每一个**回合末，但当前回合角色是赵云自己时**不提供候选、
  不扣次数**——门槛是【决斗】的目标规则（规则 §6.4：对一名**其他**角色使用）；
- **Q7 / Q8 = 对**：X **动态**（每次伤害现算，含决斗拼杀过程中打出的【杀】）、+X 是
  **卡牌级**（这张【决斗】两个方向的伤害都吃；赵云输掉时对方造成的那点同样加）。

三段各只有一处真源：

- **X** = ``Player.cui_feng_plays``（本回合全场打出【杀】的次数）：``PLAY_ON_PLAY`` 的簿记
  钩子累加（**不**自校持有者——派发环逐座位各问一次，故这一条看得见全场），
  ``TURN_START`` 的簿记钩子清 0；
- **渠道牌** = ``Player.cui_feng_duel``（那张纯虚拟【决斗】的 card_id）：提交点写、
  ``RESOLVE_AFTER_END`` 清扫钩子清；
- **+X** = 同一条声明上的 ``DAMAGE_DEAL_AMOUNT`` 修饰器——卡牌级：只认 ``ctx.card.card_id``
  与记录一致，**不**要求 ``ctx.owner == ctx.subject``（样板 = 马云騄【凤魄】②）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierPoint, behavior_for, is_slash
from sgs.resolution import Frame
from sgs.state import GameState
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    mark_round_skill_used,
    round_skill_uses,
)
from .flow import SkillFlow
from .virtual import pure_virtual_card

__all__ = [
    "CUIFENG",
    "CUIFENG_FLOW",
    "CUIFENG_ROUND_LIMIT",
    "bingshi_jie_skill_definitions",
]

CUIFENG = hero_skill_id("shi_zhaoyun", "cuifeng")
"""势赵云【摧锋】的全限定技能 id。"""

CUIFENG_ROUND_LIMIT = 2
"""「每轮限两次」的那个 2。"""

CUIFENG_DUEL = "duel"
"""产物牌名：【决斗】（纯虚拟牌，无来源实体牌）。"""

_CUIFENG_STARTED = "cuifeng_started"
"""帧私有：这次发动已经开出虚拟【决斗】（重入不重复 ``start_use``）。"""


def _cuifeng_condition(host: Any, frame: Any) -> bool:
    """触发条件：**别人的**回合末 ∧ 本轮额度未满 ∧ 那名角色还活着。

    ``TURN_END`` 是**每个座位**的回合末都派发的帧外时机（界吕蒙【博图】是阶段体、有
    own-turn 闸；郝昭【镇骨】用普通钩子 + 自校）。这里读帧上的 ``source``（= 当前回合
    座位），**自己**那一份直接判假：用户 Q6 = B 的门槛（【决斗】不能指定自己为目标），
    它挡在**候选**这一侧，故连"是否发动"都不问、也不扣次数。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    turn = frame.data.get("source")
    if owner is None or turn is None:
        return False
    if int(turn) == int(owner):
        return False
    if not host.state.player(int(turn)).alive:
        return False
    return round_skill_uses(host.state, int(owner), CUIFENG) < CUIFENG_ROUND_LIMIT


def _cuifeng_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：扣一次本轮额度 → 对当前回合角色视为使用一张伤害值 +X 的【决斗】。

    产物是**纯虚拟牌**（``pure_virtual_card``：花色 / 点数 / 颜色皆无，规则 §16.1），与
    【离间】/【势斩】共用同一条构造与同一条真使用链。目标由 ``state.turn_seat`` **定死**
    （没有选择面），故 ``start_use`` 直接给目标、不经 ``check_targets``——这是 Q6 = B 之后
    唯一走得到的那一支（目标恒为别人）。

    记账顺序：额度 → 渠道牌 id → ``start_use``。若 ``start_use`` 期间挂起续跑，帧私有
    ``_CUIFENG_STARTED`` 保证只开一次决斗。
    """
    del answers
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_CUIFENG_STARTED):
        return None
    turn = host.state.turn_seat
    if turn is None or int(turn) == owner:
        return None
    frame.data[_CUIFENG_STARTED] = True
    mark_round_skill_used(host.state, owner, CUIFENG)
    card = pure_virtual_card(host.state, CUIFENG_DUEL)
    host.state.player(owner).cui_feng_duel = int(card.card_id)
    host.start_use(
        card,
        source=owner,
        targets=(int(turn),),
        use_timing=Timing.TURN_END,
    )
    return None


CUIFENG_FLOW = SkillFlow(name="cuifeng", steps=(), effect=_cuifeng_effect)
"""【摧锋】的零步流水线：[是否发动] → 视为使用【决斗】（目标由回合角色定死，无步骤）。"""


def _cuifeng_play_condition(host: Any, frame: Any) -> bool:
    """计数条件：**任何人**打出的牌是【杀】**族**（属性 / 转化 / 虚拟产物同族）。

    **刻意不**自校 ``source == owner``：X 的定义是「本回合**所有人**打出【杀】的次数」，
    而 ``PLAY_ON_PLAY`` 的派发环是逐座位的（`catalog.py:_dispatch` 对每个座位各问一次，
    ``MATCH_OWNER_KEY`` = 该座位）——故持有者自己的这条钩子因此看得见全场每一张打出的
    【杀】。这正是它不必动 :meth:`Engine.play_card`（全牌共享入口，E2）的原因。
    """
    del host
    card = frame.data.get("card")
    return card is not None and is_slash(behavior_for(card))


def _cuifeng_note_play(host: Any, frame: Any) -> None:
    """簿记：全场每打出一张【杀】，持有者的本回合计数 +1。"""
    owner = int(frame.data["skill_owner"])
    host.state.player(owner).cui_feng_plays += 1
    return None


def _cuifeng_reset_turn(host: Any, frame: Any) -> None:
    """簿记：新回合开始 ⇒ 本回合计数清零（作用域 = 一个回合，额外回合各有一份）。

    复位点必须落在 ``TURN_START`` 而不是 ``_clear_turn_effects``：后者在回合末跑，会把
    **本回合末**那次【摧锋】要读的 X 提前抹掉。
    """
    owner = int(frame.data["skill_owner"])
    host.state.player(owner).cui_feng_plays = 0
    return None


def _cuifeng_clear_condition(host: Any, frame: Any) -> bool:
    """清扫钩子的条件：这次使用就是我记过账的那张【决斗】。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    record = host.state.player(int(owner)).cui_feng_duel
    if record is None:
        return False
    card = frame.data.get("card")
    return card is not None and int(card.card_id) == int(record)


def _cuifeng_clear(host: Any, frame: Any) -> None:
    """一次使用结算完（被【无懈可击】抵消 / 伤害被防止也要清）：清掉渠道牌记账。"""
    owner = frame.data.get("skill_owner")
    if owner is not None:
        host.state.player(int(owner)).cui_feng_duel = None
    return None


def _cuifeng_damage_bonus(
    state: GameState, ctx: Any, current: Any
) -> Any:
    """``DAMAGE_DEAL_AMOUNT``：这张【决斗】的伤害值 +X（**卡牌级**、X **动态**）。

    只认 ``(持有者的记账, ctx.card.card_id)`` 这一对：**不要求** ``ctx.owner == ctx.subject``
    ——赵云输掉这场决斗时，那一点伤害是对方造成的，牌面写的却是「这张【决斗】的伤害值
    +X」（用户 Q8 = 对；对照【裸衣】写的是「你为伤害来源」，故它有 ``ctx.owner == ctx.subject``
    这一条）。X 每次伤害**现读** ``Player.cui_feng_plays``（用户 Q7），故决斗拼杀过程中
    刚打出的【杀】也算进来（票面注意 2 的 ``1+2+1+1=5``）。传导帧不派发本挂点
    （``damage_amount`` 的既有口径），不会重复加。
    """
    if ctx.card is None:
        return current
    record = state.player(ctx.owner).cui_feng_duel
    if record is None or int(ctx.card.card_id) != int(record):
        return current
    return current + state.player(ctx.owner).cui_feng_plays


_CUIFENG = SkillDef(
    skill_id=CUIFENG,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # 发动：每个（别人的）回合末问一次（「你可以」⇒ 非锁定）。
        Hook(Timing.TURN_END, flow=CUIFENG_FLOW, condition=_cuifeng_condition),
        # 计数：全场打出的【杀】（簿记段，静默跑、不进任何面）。
        Hook(
            Timing.PLAY_ON_PLAY,
            handler=_cuifeng_note_play,
            condition=_cuifeng_play_condition,
            forced=True,
            bookkeeping=True,
        ),
        # 复位：新回合开始清 0。
        Hook(
            Timing.TURN_START,
            handler=_cuifeng_reset_turn,
            forced=True,
            bookkeeping=True,
        ),
        # 清账：那张【决斗】结算完就清掉渠道牌 id。
        Hook(
            Timing.RESOLVE_AFTER_END,
            handler=_cuifeng_clear,
            condition=_cuifeng_clear_condition,
            forced=True,
            bookkeeping=True,
        ),
    ),
    modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _cuifeng_damage_bonus),),
    # 每轮限两次：`round_limit_present()` 读这一位才会把 `turn_change` 落进记录流。
    per_round_limit=True,
    requirement=(
        "每轮限两次，每回合结束时，你可视为对当前回合角色使用一张伤害值+X的【决斗】"
        "（X为本回合所有人打出【杀】的次数）"
    ),
)
"""势赵云【摧锋】的完整声明（一条技能、四条钩子、一个卡牌级加伤修饰器）。"""


def bingshi_jie_skill_definitions() -> tuple[SkillDef, ...]:
    """兵势篇-节包的非 A 族技能定义（当前只有【摧锋】一条）。"""
    return (_CUIFENG,)
