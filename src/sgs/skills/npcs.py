"""NPC 技能（M7 票 27；票 02 §7）：四条**只授敌方座位**的技能。

牌面（票 02 §7 的口径）::

    NPC 技能只授予**敌方座位**，不进玩家池 / 价值表 / 抽取；
    v1 4 条（小怪 2~3、Boss 1~2），通用效果，命中 shapes.md 既有形状。

**id 方案**：``npc.<短 id>``。它**不在** ``hero_skill_id`` 的 ``hero.<武将>.<短 id>`` 派生式上
——NPC 技能没有"默认拥有者"武将（敌人是模板 + 抽到的技能，不是武将表记录），``CATALOG``
只按 id 查表，故前缀只用来说明"这是谁家的技能"。四条 id 与卡池 / 价值表的**不相交**是
结构事实：价值表两段都不收载体 ``NPC`` 的 id（``sgs/data/values.py`` 的语义门 + 学习池入口
排除），故"不进池"不靠稀有度、也不靠前缀约定。

**载体 = ``CarrierKind.NPC``**（第六档，与票 26 的 ``RELIC`` 同一做法）。它回答的是"这条技能
归谁"：不属于任何武将牌、也不属于玩家成长面。优先级档**复用 ``HERO``**（见
``sgs/effects/modifiers.py`` 的 ``_CARRIER_PRIORITY``）：NPC 技能就是敌人在这一场里的
"武将牌技能"（模板 ``initial_skills`` + 随机池），行为上与武将技同档。

**入口**（票 02 §10 / §11）：唯一入口是 ``PveConfig.grants`` 的 ``kind="skill"`` ⇒
``Player.granted_skills``（``SkillSourceKind.GRANTED``：M6 票 04 的原语，不新造来源）。
``PveConfig`` 构造期按**载体**拒绝非 ``ENEMY`` 座位（``sgs/data/rule_config.py`` 的
:meth:`~sgs.data.PveConfig._grants_fit_the_growth_limits` 第 6 条），故"只授敌方"是 header
自证的一部分。

**形状**（`docs/agents/skill-authoring/shapes.md` §2，各选一行）：

| 技能 | 效果 | 形状 / 挂点 | 样板 |
|---|---|---|---|
| ``npc.fury`` | 造成伤害 +1 | 纯修饰器 ``DAMAGE_DEAL_AMOUNT``（来源侧） | 【古锭刀】/ 遗物 ``relic.damage_dealt`` |
| ``npc.guard`` | 受到伤害 -1 | 纯修饰器 ``DAMAGE_TAKE_AMOUNT``（目标侧） | 【藤甲】的同一挂点 |
| ``npc.swift`` | 与其他角色的距离 -1 | 纯修饰器 ``DISTANCE``（**主体侧**） | 【马术】 |
| ``npc.wrath`` | 造成伤害后回复 1 点体力 | 造伤后钩子 ``DAMAGE_AFTER_DEAL`` + ``RECOVER`` 子帧 | 【狂骨】②（去掉距离条件） |

四条都是 ``locked=True``（牌面没有「可以」）/ ``carrier=CarrierKind.NPC`` /
``skill_type=NORMAL`` / 无流水线 / 无接缝 / 无显示组件 ⇒ **零新询问形态、零 DOM**（C3）。
锁定是刻意的：敌人从不"选择"技能（AI 读点因此一条都不新增，票 02 §11）。

**读法（刻意的选择，写在这里免得下一手重新推导）**：

1. 「造成伤害 +1」「受到伤害 -1」「距离 -1」都是**查询期现算**的修饰器（不落 delta、不进
   记录流），与遗物六条同一姿态；只对**持有者自己**生效（修饰器链遍历全体存活实例，回调
   自带 ``ctx.subject`` / ``ctx.target`` 自校）。
2. 「受到伤害 -1」是**逐帧**的（``DAMAGE_TAKE_AMOUNT`` 在每一帧派发，含传导帧），下限 0；
   1 点伤害被减到 0 就是"没受到伤害"——引擎在 ``amount <= 0`` 时整条"伤害后"链不派发
   （规则 §4.14：0 点伤害不算受到伤害）。
3. 「距离 -1」只做**主体侧**（持有者算别人）：牌面写「你与其他角色的距离」，与【马术】同向；
   **不**做客体侧（遗物那条"距离 −1"双向是本票之前的刻意读法，两条不能混）。
4. 「造成伤害后回复 1 点体力」**逐事件、不是逐点**（牌面没有「1 点」限定词，与沮授【矢北】
   同一条默认读法）：一次 2 点伤害只回 1 点。满体力时不压帧（规则 §4.15「满体力时不回复」），
   走**既有回复路径**（``RECOVER`` 子帧）⇒【救援】一类改回复量的技能照样看得到它。
"""

from __future__ import annotations

from typing import Any

from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.state import FrameKind
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef

__all__ = [
    "NPC_FURY",
    "NPC_GUARD",
    "NPC_SKILL_IDS",
    "NPC_SWIFT",
    "NPC_WRATH",
    "npc_skill_definitions",
]

NPC_FURY = "npc.fury"
"""凶暴：锁定技，你造成的伤害 +1（小怪）。"""

NPC_GUARD = "npc.guard"
"""坚甲：锁定技，你受到的伤害 -1（小怪）。"""

NPC_SWIFT = "npc.swift"
"""疾行：锁定技，你与其他角色的距离 -1（小怪）。"""

NPC_WRATH = "npc.wrath"
"""嗜血：锁定技，当你造成伤害后，你回复 1 点体力（Boss）。"""

NPC_SKILL_IDS: tuple[str, ...] = (NPC_FURY, NPC_GUARD, NPC_SWIFT, NPC_WRATH)
"""四条 NPC 技能的 id（出厂顺序 = 票 02 §7 的 小怪 2~3 + Boss 1~2 口径）。

v1 的分配：小怪三条（``fury`` / ``guard`` / ``swift``）、Boss 一条（``wrath``）——具体哪个
模板带哪条由**剧本内容**决定（票 28 的模板 ``initial_skills`` / ``random`` 池），不是本票的
数据落点：本票只保证"这四条存在、只授敌方、效果正确"。
"""


def _owner_is_source(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``ctx.owner == ctx.subject`` 时把 ``current`` 加 1（伤害来源侧自校）。

    ``DAMAGE_DEAL_AMOUNT`` 的 ``ctx.subject`` = 伤害来源（``ctx.role = SUBJECT``）。修饰器链
    遍历全体存活实例，缺这一句就会"任何人的伤害都加成"。
    """
    del state
    if ctx.owner != ctx.subject:
        return current
    return current + 1


def _owner_is_target(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``ctx.owner == ctx.target`` 时把 ``current`` 减 1（受伤目标侧自校，下限 0）。"""
    del state
    if ctx.owner != ctx.target:
        return current
    return max(0, current - 1)


def _owner_leads_the_distance(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``ctx.subject == ctx.owner`` 时距离 −1（**只做主体侧**：持有者算别人）。"""
    del state
    if ctx.owner != ctx.subject:
        return current
    return current - 1


_WRATH_RECOVERED = "npc.wrath:recovered"
"""帧私有守卫：这次伤害事件已经压过回复子帧。

派发环在别的实例挂起后重入时会从头再跑一遍已完成的钩子（与【狂骨】/【天成】同一个重入
口径），没有它就会回两次。写在压帧**之前**：``RECOVER`` 子帧已经在飞，重入只该跳过。
"""


def _wrath_deals(host: Any, frame: Any) -> bool:
    """**这次伤害是持有者造成的吗**（``DAMAGE_AFTER_DEAL`` 的"关于我"自校）。

    读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值前由派发环写）而不是
    ``skill_owner``（回调执行期才写）：条件必须能按"我是谁"过滤——派发环遍历每个存活玩家的
    实例，漏掉它就会"任何人造成伤害、Boss 都回血"（与【狂骨】的 ``_kuanggu_deals`` 同形）。
    无来源伤害（【闪电】一类）的 ``source`` 是 ``None`` ⇒ 天然排除。
    """
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if frame.data.get("source") != owner:
        return False
    return frame.data.get("target") is not None


def _wrath(host: Any, frame: Any) -> Any:
    """``DAMAGE_AFTER_DEAL`` 回调：**造成伤害后**回复 1 点体力（压 ``RECOVER`` 子帧）。

    三个刻意口径（与【狂骨】② 的差别只在"没有距离条件"）：

    1. **逐事件、不逐点**（``per_point=False``）：牌面没有「1 点」限定词 ⇒ 一次 2 点伤害只回
       1 点（与沮授【矢北】同一条默认读法）；
    2. **走既有回复路径**（``RECOVER`` 子帧、``amount=1``）：【救援】一类改回复量的技能因此
       看得到它；回复量夹在体力上限内由 RECOVER 帧自己负责；
    3. **满体力时不压帧**（规则 §4.15「满体力时不回复」）：锁定技照常"发动"，但什么也不发生
       ——没有 delta、也没有回复事件可供别人挂钩。
    """
    if frame.data.get(_WRATH_RECOVERED):
        return None
    frame.data[_WRATH_RECOVERED] = True
    owner = int(frame.data["skill_owner"])
    player = host.state.player(owner)
    if player.hp >= player.hp_max:
        return None
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
    return "stay"


def npc_skill_definitions() -> tuple[SkillDef, ...]:
    """四条 NPC 技能的 :class:`~sgs.skills.catalog.SkillDef`（票 02 §7 的 2~3 + 1~2 口径）。

    ``carrier = NPC`` ⇒ 优先级档复用武将档（``_CARRIER_PRIORITY``），并让"不进玩家成长面"
    成为两处机械门读得到的结构事实。``requirement`` 是给人看的一句话（提示条 / 描述），
    不是规则判据。
    """
    return (
        SkillDef(
            skill_id=NPC_FURY,
            locked=True,
            carrier=CarrierKind.NPC,
            modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _owner_is_source),),
            requirement="锁定技：你造成的伤害 +1",
        ),
        SkillDef(
            skill_id=NPC_GUARD,
            locked=True,
            carrier=CarrierKind.NPC,
            modifiers=(Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _owner_is_target),),
            requirement="锁定技：你受到的伤害 -1",
        ),
        SkillDef(
            skill_id=NPC_SWIFT,
            locked=True,
            carrier=CarrierKind.NPC,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _owner_leads_the_distance),),
            requirement="锁定技：你与其他角色的距离 -1",
        ),
        SkillDef(
            skill_id=NPC_WRATH,
            locked=True,
            carrier=CarrierKind.NPC,
            hooks=(
                Hook(
                    Timing.DAMAGE_AFTER_DEAL,
                    handler=_wrath,
                    condition=_wrath_deals,
                    per_point=False,
                ),
            ),
            requirement="锁定技：当你造成伤害后，你回复 1 点体力",
        ),
    )
