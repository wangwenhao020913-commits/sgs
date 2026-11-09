"""技能注册表：``skills/`` 是技能定义的**唯一入口**。

模块级内容分两层：

- :mod:`sgs.effects.modifiers`（``effects`` 层）装**修饰器管道的词汇与派发**：
  ``ModifierPoint`` 六个挂点 / ``ModifierContext`` / ``Modifier`` / ``apply_modifiers``。
  它们住下层，是因为六个挂点都由 ``effects/actions.py`` 的规则函数直接读；本子包把
  这些名字连同 :class:`~sgs.skills.catalog.SkillDef` / ``CATALOG`` 一起再导出，外部
  仍只认 ``sgs.skills`` 这一个入口。
- :mod:`sgs.skills.catalog` 装**定义与派发**：``SkillDef`` / ``Hook`` / ``Seam`` /
  ``CATALOG`` / ``resolve_instances`` / ``dispatch_timing`` / ``dispatch_seam``。
  票 29 落其**静态面**（诸葛连弩 / 仁王盾 / 六坐骑 / 方天画戟），票 30 在同一张表上补
  时机钩子、帧内接缝与虚拟牌。
- :mod:`sgs.skills.virtual` 装**虚拟牌 / 转化牌**的构造（票 30）。

**族 → 模块**（一条技能一条 :class:`~sgs.skills.catalog.SkillDef`；"某条技能住哪"先查这张表，
别逐个模块翻）：

| 族 | 模块 | 代表技能 |
|---|---|---|
| A 主动技 | :mod:`sgs.skills.active` | 制衡 / 仁德 / 反间 / 青囊 / 离间 / 结姻 / ☆荐言 / ☆攻心 |
| B 伤害后 | :mod:`sgs.skills.damage_after` | 奸雄 / 反馈 / 遗计 / 狂骨（造伤侧锁定技） |
| H 转化（源谓词 → 虚拟牌） | :mod:`sgs.skills.heroes` | 武圣 / 龙胆 / 国色 / 急救 |
| K·M 距离与目标转移 | :mod:`sgs.skills.target_change` | 马术 / 流离 |
| 主公求助 | :mod:`sgs.skills.lord_request` | 护驾 / 激将 / 救援 |
| 改判 | :mod:`sgs.skills.rejudge` | 鬼才 / 天妒 |
| 失去牌后 | :mod:`sgs.skills.move_after` | 连营 / 枭姬 |
| 阶段体 | :mod:`sgs.skills.phase_body` | 英姿 / 闭月 / 替身（**限定技**） |
| 失去体力后（M6 票 17） | :mod:`sgs.skills.life_after` | 诈降（摸三张 + 本阶段红【杀】加成） |
| 排列 | :mod:`sgs.skills.arrange` | 观星 |
| 目标池 / 响应窗口 | :mod:`sgs.skills.target_pool` / :mod:`sgs.skills.response_window` | 空城 / 谦逊；无双 / 铁骑 / 烈弓 |
| P 限制豁免 / 【咆哮】②（锁定技） | :mod:`sgs.skills.limits` | 咆哮（次数 + 被抵消后的 +1）/ 奇才（距离） |
| U 使用牌时 | :mod:`sgs.skills.use_after` | 集智 |
| S **状态位变化后**（`STATE_FLIPPED` / `STATE_CHAINED`，yijiang-2014 票 08） | :mod:`sgs.skills.state_after` | 法恩 |
| J 可重复判定（手写多轮循环） | :mod:`sgs.skills.judgment_loop` | 洛神 |
| 觉醒 / ☆ 派生（M6 票 06） | :mod:`sgs.skills.awakening` | 潜心 / 勤学 / 诛害 |
| 否定（目标级无效，M6 票 07） | :mod:`sgs.skills.negation` | 奋威（目标产生前的取消住 :mod:`sgs.skills.target_pool`，事件帧 / 响应窗口各自住票 08 / 界【离间】） |
| **流水线执行器**（**不**依赖 catalog） | :mod:`sgs.skills.flow` | ``SkillFlow`` / ``PickCards`` / ``PickSeats`` / ``PickOption`` / ``PickProvide`` / ``run_flow`` |
| **注册表 + 装备定义** | :mod:`sgs.skills.catalog` | ``SkillDef`` / ``CATALOG`` / ``resolve_instances`` / ``dispatch_timing`` / ``dispatch_seam`` |
| **武将牌堆的占用面**（票 05） | :mod:`sgs.skills.hero_cards` | ``blocked_true_names``（"被抽走 ∪ 在场"的派生集合） |
| **L0「游戏开始时」**（wuming 票 01） | :mod:`sgs.skills.start` | 天成（锁定技：展示 3 张武将牌，每次选"获得技能 / 加体力上限 + 回复 + 摸 X 张"） |

本文件另装**跨层只读查询**：加载期用它校验"每张装备牌都有对应技能"（票 09 §7 #8），
判定帧用它拿改判能力座位（票 10 §4），主公技门控用它（票 13 §4）。

装备**牌名 id 不带前缀**（``crossbow``），装备**技能 id = ``equip.`` + 牌名 id**
（``equip.crossbow``）——两者不可混（票 09 §1）。
"""

from __future__ import annotations

from sgs.data import SkillType
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    ModifierRole,
    PriorityClass,
    apply_modifiers,
)
from sgs.state import GameState, RejudgeOffer, Zone

from .catalog import (
    ACTIVATION_PLANS,
    CATALOG,
    DAMAGE_DEAL_POINT_KEY,
    DAMAGE_DEAL_RECORDED_KEY,
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    MATCH_ACTOR_KEY,
    PASS_STATE_KEY,
    STATE_CHANGE_KEY,
    ActivationCandidate,
    ActivationPlan,
    Hook,
    RUNNING_SKILL_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    RejudgeOriginal,
    Seam,
    SeamKind,
    SkillDef,
    SkillInstance,
    SkillSource,
    SkillSourceKind,
    StateChange,
    activation_plan,
    awakening_skill_awakened,
    damage_point_index,
    dispatch_seam,
    dispatch_timing,
    hero_card_zone_key,
    instance_key,
    is_equipped,
    limited_skill_used,
    lord_skill_allowed,
    lord_skill_ids,
    placement_skill_ids,
    prune_card_tags,
    prune_skill_state,
    rejudge_skill_ids,
    reset_phase_skill_state,
    mark_round_skill_used,
    mark_limited_skill_used,
    mark_skill_used_this_turn,
    reset_round_skill_state,
    reset_turn_skill_state,
    round_limit_present,
    round_skill_uses,
    resolve_instances,
    same_kingdom,
    skill_used_this_turn,
    state_change,
    terminate_activation_pass,
    timing_hooks_present,
)
# 转化技的**来源查询**（票 14）：接入层只允许 import ``sgs.skills`` 这个公开子包入口，
# 不能深导入 ``sgs.skills.heroes``（M4 第一纪律，`tests/test_dod_m4.py` 钉着），故在包入口
# 重导出这一条只读查询。
from .heroes import (
    conversion_offers,
    conversion_offers_by_source,
    conversion_preactivations,
    conversion_skill_ids,
    near_death_conversion_offers,
    is_forced_conversion,
    is_transformed_action,
    owned_conversion_skill_ids,
    resolve_conversion_confirmation,
    transform_candidates,
)
from .arrange import (
    GUANXING,
    GUANXING_MAX,
    arrange_activation_plans,
    arrange_skill_definitions,
)
from .damage_after import (
    DAMAGE_AFTER_SKILLS,
    DamageAfterSkill,
    damage_after_skill_definitions,
)
from .active import (
    ACTIVATIONS,
    ACTIVE_SKILLS,
    CANDIDATE_CAP,
    GONGXIN,
    GONGXIN_DECK_TOP,
    GONGXIN_DISCARD,
    GONGXIN_FLOW,
    GONGXIN_OPTIONS,
    JIANYAN,
    JIANYAN_FLOW,
    JIANYAN_OPTIONS,
    JIE_LIJIAN,
    YIJUE,
    YIJUE_FLOW,
    YIJUE_HEAL,
    YIJUE_HEAL_OPTIONS,
    YIJUE_SKIP_HEAL,
    ActiveSkill,
    Arrange,
    PickCards,
    PickOption,
    PickProvide,
    PickSeats,
    SkillFlow,
    activate_skill,
    register_activation,
    run_flow,
)
from .flow import (
    FREE_SPLIT,
    SEAT_PREFIX,
    card_preactivations,
    manifest,
    validate_flow_activation,
)
from .preactivation import (
    flow_for,
    preactivation_for,
    preactivated_skill_ids,
    preactivations_for,
    validate_activation,
)
from .move_after import (
    MOVE_AFTER_SKILLS,
    MOVE_EVENT_KEY,
    MoveAfterSkill,
    MoveEvent,
    dispatch_move_after,
    move_after_providers,
    move_after_skill_definitions,
    move_event,
)
from .virtual import (
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    VirtualSourceError,
    kind_source_ceiling,
    pure_virtual_card,
    transformed_card,
    transformed_slash,
    virtual_card_id,
    virtual_dodge,
)
from .lord_request import (
    HUJIA,
    JIE_JIUYUAN,
    JIE_JIUYUAN_FLOW,
    JIE_JIUYUAN_REPLACED_KEY,
    JIJIANG,
    JIUYUAN,
    LORD_REQUEST_DELIVER_KEY,
    LORD_REQUEST_KEY,
    LORD_REQUEST_SKILLS,
    PROVIDERS,
    PROVIDER_ACTOR_KEY,
    PROVIDER_CARD_KEY,
    PROVIDER_REQUIRED_KEY,
    PROVIDER_SATISFIED_KEY,
    PROVIDER_SKILL_KEY,
    LordSkill,
    advance_provider,
    dispatch_provider,
    lord_skill_definitions,
    provider_registered,
    start_provider,
)
from .rejudge import (
    GUICAI,
    TIANDU,
    rejudge_activation_plans,
    rejudge_skill_definitions,
    tiandu_handler,
)
from .phase_body import (
    PHASE_BODY_SKILLS,
    XUNXUN,
    XUNXUN_ARRANGE,
    XUNXUN_FLOW,
    PhaseBodySkill,
    phase_body_skill_definitions,
)
from .response_window import (
    JIE_TIEQI,
)
from .target_change import (
    JIE_LIULI,
    JIE_MASHU,
    LIULI,
    MASHU,
    target_change_skill_definitions,
)
from .limits import (
    PAOXIAO,
    QICAI,
    limits_skill_definitions,
)
from .use_after import (
    CONGTAO,
    JIZHI,
    JIZHI_FLOW,
    use_after_skill_definitions,
)
from .judgment_loop import (
    JIE_LUOSHEN,
    LUOSHEN,
    LUOSHEN_OPTIONS,
    LUOSHEN_TAG_TYPE,
    judgment_loop_activation_plans,
    judgment_loop_skill_definitions,
)
from .negation import (
    FENWEI,
    TARGET_INEFFECTIVE_KEY,
    mark_targets_ineffective,
    negation_skill_definitions,
    target_is_ineffective,
)
from .hero_zone import (
    JIE_LIANYING,
    JIE_QIANXUN,
    QIANXUN_FLOW,
    QIANXUN_PLACED_KEY,
    hero_zone_skill_definitions,
)
from .awakening import (
    LVMENG_GONGXIN,
    LVMENG_QINXUE,
    QIANXUE_FLOW,
    QINXUE_DRAW,
    QINXUE_FLOW,
    QINXUE_OPTIONS,
    QINXUE_RECOVER,
    XUSHU_JIANYAN,
    XUSHU_QIANXUE,
    XUSHU_ZHUHAI,
    ZHUHAI_FLOW,
    awakening_activation_plans,
    awakening_skill_definitions,
)
from .life_after import (
    JIE_ZHAXIANG,
    ZHAXIANG_DRAW,
    life_after_skill_definitions,
)
from .hero_cards import blocked_true_names
from .start import (
    TIANCHENG,
    TIANCHENG_MAX_HP,
    TIANCHENG_REQUIREMENT,
    TIANCHENG_REVEALS,
    WUMING,
    start_skill_definitions,
)

EQUIPMENT_CARD_TYPE_IDS: tuple[str, ...] = tuple(
    skill_id.removeprefix("equip.")
    for skill_id, definition in CATALOG.items()
    if definition.carrier is CarrierKind.EQUIPMENT
)
"""22 张装备的**牌名 id**（11 武器 + 4 防具 + 7 坐骑；M5-01a 起含军争 5 张），不带前缀。"""

EQUIPMENT_SKILL_IDS: frozenset[str] = frozenset(
    definition.skill_id
    for definition in CATALOG.values()
    if definition.carrier is CarrierKind.EQUIPMENT
)
"""装备**技能 id = ``equip.`` + 牌名 id**（票 09 §1）：由 ``CATALOG`` 派生，不另抄一遍。"""

LORD_SKILL_IDS: frozenset[str] = lord_skill_ids()
"""登记在**主公技类型**上的技能 id：:func:`lord_skill_ids` 对 ``CATALOG`` 的 **import 期快照**
（票 01 §载体接入 #8：M2 手抄空集 → M3 由定义字段派生）。

"这是不是主公技"因此只有一个真源（``SkillDef.skill_type``），不再是一张会与定义漂移的
名单；运行期的门控读定义字段（:func:`_identity_gates_out` / :func:`lord_skill_allowed`），
不读这个快照。**这份名单不是"非主公一条都不许有"的过滤器**：身份门控只作用于**武将牌
载体**（wuming 票 06；`SkillSourceKind.HERO_CARD`），运行时**获得**的主公技
（`Player.granted_skills`，来源 ``GRANTED``）任何身份都能发动，投影的获得循环也不按它筛。
"""

PLACEMENT_SKILL_IDS: frozenset[str] = placement_skill_ids()
"""**放置类技能**（往武将牌上放牌）的技能 id：:func:`placement_skill_ids` 对 ``CATALOG``
的 **import 期快照**（M6 票 10）。

"这是不是放置类技能"的唯一真源是 ``SkillDef.places_on_hero_card``；数据侧的三项声明由
加载期与它双向核对（``heroes.placement_missing`` / ``heroes.placement_not_registered``），
引擎在放置入口按定义字段 fail-closed。"""

REJUDGE_SKILL_IDS: frozenset[str] = rejudge_skill_ids()
"""有**改判**能力的技能 id（【鬼才】/【鬼道】一类）：:func:`rejudge_skill_ids` 对
``CATALOG`` 的 **import 期快照**（票 11 §①：M2 手抄空集 → M3 由定义字段派生）。

"这是不是改判技"因此只有一个真源（``SkillDef.rejudge_capable``），不再是一张会与定义
漂移的名单；运行期的座位解析读**存活实例**的定义字段（:func:`rejudge_capable_seats`），
不读这个快照。
"""

__all__ = (
    "ACTIVATIONS",
    "ACTIVATION_PLANS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "CATALOG",
    "DAMAGE_AFTER_SKILLS",
    "DAMAGE_POINT_KEY",
    "DAMAGE_RECORDED_KEY",
    "DAMAGE_DEAL_POINT_KEY",
    "DAMAGE_DEAL_RECORDED_KEY",
    "EQUIPMENT_CARD_TYPE_IDS",
    "EQUIPMENT_SKILL_IDS",
    "FENWEI",
    "FREE_SPLIT",
    "GUANXING",
    "GUANXING_MAX",
    "GUICAI",
    "HUJIA",
    "JIE_JIUYUAN",
    "CONGTAO",
    "JIE_JIUYUAN_FLOW",
    "JIE_JIUYUAN_REPLACED_KEY",
    "JIE_LIANYING",
    "JIE_LIJIAN",
    "JIE_LIULI",
    "JIE_QIANXUN",
    "JIE_ZHAXIANG",
    "JIJIANG",
    "JIE_MASHU",
    "JIE_TIEQI",
    "JIZHI",
    "JIZHI_FLOW",
    "JIUYUAN",
    "LIULI",
    "LORD_REQUEST_DELIVER_KEY",
    "LORD_REQUEST_KEY",
    "LORD_REQUEST_SKILLS",
    "LORD_SKILL_IDS",
    "JIE_LUOSHEN",
    "LUOSHEN",
    "LUOSHEN_OPTIONS",
    "LUOSHEN_TAG_TYPE",
    "MASHU",
    "MATCH_ACTOR_KEY",
    "MAX_VIRTUAL_SOURCES",
    "MOVE_AFTER_SKILLS",
    "MOVE_EVENT_KEY",
    "PAOXIAO",
    "PLACEMENT_SKILL_IDS",
    "PHASE_BODY_SKILLS",
    "XUNXUN",
    "XUNXUN_ARRANGE",
    "XUNXUN_FLOW",
    "PROVIDERS",
    "PROVIDER_ACTOR_KEY",
    "PROVIDER_CARD_KEY",
    "PROVIDER_REQUIRED_KEY",
    "PROVIDER_SATISFIED_KEY",
    "PROVIDER_SKILL_KEY",
    "QIANXUN_FLOW",
    "QIANXUN_PLACED_KEY",
    "QICAI",
    "REJUDGE_SKILL_IDS",
    "RejudgeOriginal",
    "RUNNING_SKILL_KEY",
    "SKILL_JUDGMENT_CARD_KEY",
    "SEAT_PREFIX",
    "STATE_CHANGE_KEY",
    "ActivationCandidate",
    "ActivationPlan",
    "PASS_STATE_KEY",
    "ActiveSkill",
    "Arrange",
    "CarrierKind",
    "DamageAfterSkill",
    "Hook",
    "LordSkill",
    "Modifier",
    "ModifierContext",
    "ModifierPoint",
    "ModifierRole",
    "MoveAfterSkill",
    "MoveEvent",
    "PhaseBodySkill",
    "PickCards",
    "PickOption",
    "PickProvide",
    "PickSeats",
    "PriorityClass",
    "Seam",
    "SeamKind",
    "SkillDef",
    "SkillFlow",
    "SkillInstance",
    "SkillSource",
    "SkillSourceKind",
    "StateChange",
    "SkillType",
    "TARGET_INEFFECTIVE_KEY",
    "TIANDU",
    "VirtualKind",
    "VirtualSourceError",
    "GONGXIN",
    "GONGXIN_DECK_TOP",
    "GONGXIN_DISCARD",
    "GONGXIN_FLOW",
    "GONGXIN_OPTIONS",
    "JIANYAN",
    "JIANYAN_FLOW",
    "JIANYAN_OPTIONS",
    "LVMENG_GONGXIN",
    "LVMENG_QINXUE",
    "QIANXUE_FLOW",
    "QINXUE_DRAW",
    "QINXUE_FLOW",
    "QINXUE_OPTIONS",
    "QINXUE_RECOVER",
    "XUSHU_JIANYAN",
    "XUSHU_QIANXUE",
    "XUSHU_ZHUHAI",
    "YIJUE",
    "YIJUE_FLOW",
    "YIJUE_HEAL",
    "YIJUE_HEAL_OPTIONS",
    "YIJUE_SKIP_HEAL",
    "ZHAXIANG_DRAW",
    "life_after_skill_definitions",
    "activate_skill",
    "activation_plan",
    "advance_provider",
    "apply_modifiers",
    "arrange_activation_plans",
    "arrange_skill_definitions",
    "blocked_true_names",
    "start_skill_definitions",
    "TIANCHENG",
    "TIANCHENG_MAX_HP",
    "TIANCHENG_REQUIREMENT",
    "TIANCHENG_REVEALS",
    "WUMING",
    "conversion_offers",
    "conversion_offers_by_source",
    "conversion_preactivations",
    "conversion_skill_ids",
    "near_death_conversion_offers",
    "damage_after_skill_definitions",
    "damage_point_index",
    "dispatch_move_after",
    "dispatch_provider",
    "dispatch_seam",
    "dispatch_timing",
    "equipment_skill_ids",
    "flow_for",
    "hero_card_zone_key",
    "hero_zone_skill_definitions",
    "instance_key",
    "is_equipped",
    "is_forced_conversion",
    "is_transformed_action",
    "judgment_loop_activation_plans",
    "judgment_loop_skill_definitions",
    "kind_source_ceiling",
    "limits_skill_definitions",
    "limited_skill_used",
    "lord_skill_allowed",
    "lord_skill_definitions",
    "lord_skill_ids",
    "mark_limited_skill_used",
    "mark_round_skill_used",
    "mark_skill_used_this_turn",
    "move_after_providers",
    "move_after_skill_definitions",
    "mark_targets_ineffective",
    "move_event",
    "negation_skill_definitions",
    "owned_conversion_skill_ids",
    "phase_body_skill_definitions",
    "placement_skill_ids",
    "prune_card_tags",
    "prune_skill_state",
    "provider_registered",
    "register_activation",
    "pure_virtual_card",
    "rejudge_activation_plans",
    "rejudge_capable_seats",
    "rejudge_offers",
    "rejudge_original_to",
    "rejudge_card_zones",
    "rejudge_skill_definitions",
    "rejudge_skill_ids",
    "reset_phase_skill_state",
    "reset_round_skill_state",
    "resolve_conversion_confirmation",
    "reset_turn_skill_state",
    "round_limit_present",
    "round_skill_uses",
    "resolve_instances",
    "run_flow",
    "same_kingdom",
    "skill_used_this_turn",
    "state_change",
    "start_provider",
    "target_change_skill_definitions",
    "target_is_ineffective",
    "tiandu_handler",
    "terminate_activation_pass",
    "timing_hooks_present",
    "transform_candidates",
    "transformed_card",
    "transformed_slash",
    "use_after_skill_definitions",
    "virtual_card_id",
    "virtual_dodge",
)


def equipment_skill_ids() -> frozenset[str]:
    """全部装备技能的 id 集合（只读；加载期跨层校验用）。"""
    return EQUIPMENT_SKILL_IDS


def rejudge_capable_seats(state: GameState) -> tuple[int, ...]:
    """当前**有改判能力**的座位（票 11 §①；判定帧的改判响应队列左项）。

    解析**存活实例**（``resolve_instances``：装备区 ∪ 虚拟授予 ∪ 武将牌，武将阵亡 /
    来源消失即自动出队），取其中定义带 :attr:`~sgs.skills.catalog.SkillDef.rejudge_capable`
    的实例的持有者。读定义字段而不是 :data:`REJUDGE_SKILL_IDS` 快照，与主公技门控读
    ``skill_type`` 同一姿态："能力"只有一个真源。

    场上没有改判能力者 ⇒ 空元组 ⇒ 判定帧不构造任何询问、无额外日志节点，
    "队列恒空即终态正确"的既有口径不破（M2 的 34 条基准一行不改）。
    """
    seats: list[int] = []
    for instance in resolve_instances(state):
        if instance.definition.rejudge_capable and instance.owner not in seats:
            seats.append(instance.owner)
    return tuple(seats)


def rejudge_offers(state: GameState, seat: int) -> tuple[RejudgeOffer, ...]:
    """``seat`` 的改判窗口**逐技能**的候选声明（feng-zhangjiao 票 02；窗口载荷的唯一真源）。

    与 :func:`rejudge_capable_seats` 读**同一批存活实例**、同一批定义字段，只是这里把
    每条改判技能的三个声明字段（``rejudge_zones`` / ``rejudge_card_color``）摊成一条
    :class:`~sgs.state.RejudgeOffer`：候选生成器按它逐条枚举，一条技能一个构造子
    （``SubstituteJudgeCard(skill_id=…)``），故"用哪条技能改判"在记录流里读得出。
    原判定牌的去向（``rejudge_original_to``）不在这里——它由引擎按 ``skill_id`` 现查，
    与"这条技能的候选"分列。实例序 = ``resolve_instances`` 的**解析序**（确定性；定义见
    `03` §4「技能描述模型」的「技能定义 / 实例 / 派发的冻结面」表「链序 / 优先级」行）。
    """
    offers: list[RejudgeOffer] = []
    for instance in resolve_instances(state):
        definition = instance.definition
        if instance.owner != seat or not definition.rejudge_capable:
            continue
        offers.append(
            RejudgeOffer(
                skill_id=instance.skill_id,
                zones=definition.rejudge_zones,
                color=definition.rejudge_card_color,
            )
        )
    return tuple(offers)


def rejudge_original_to(state: GameState, seat: int, skill_id: str) -> RejudgeOriginal:
    """``seat`` 用 ``skill_id`` 改判时，被换下的原判定牌去哪（票 02；缺省 = 弃牌堆）。

    判据只在技能声明上（:attr:`~sgs.skills.catalog.SkillDef.rejudge_original_to`）：引擎
    按改判动作自带的 ``skill_id`` 现查，不写死任何技能 id，也不按"哪个座位"特判。
    查不到（构造局面 / 已失去技能）时退回 ``DISCARD``——那是规则 §11.3 的缺省一档（代替）。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            return instance.definition.rejudge_original_to
    return RejudgeOriginal.DISCARD


def rejudge_card_zones(state: GameState, seat: int) -> tuple[Zone, ...]:
    """``seat`` 的改判窗口**可以打出哪些区的牌**（M6 票 14；缺省一张手牌）。

    与 :func:`rejudge_capable_seats` 读**同一批存活实例**、同一批定义字段，只是这里取
    :attr:`~sgs.skills.catalog.SkillDef.rejudge_zones` 的并集：标准【鬼才】（§九 L958）
    "打出一张**手牌**" ⇒ ``(HAND,)``；界【鬼才】（§十九 L2037）"打出**一张牌**" ⇒
    ``(HAND, EQUIP)``。多个改判实例并存时取并集（能力只增不减的一个座位不会因此变窄）。

    该座位没有改判能力时不构造窗口（:func:`rejudge_capable_seats` 已先过滤），此处的
    缺省 ``(HAND,)`` 只是"没有定义可读"的安全港，不是一条可用性判断。
    """
    zones: list[Zone] = []
    for instance in resolve_instances(state):
        if instance.owner != seat or not instance.definition.rejudge_capable:
            continue
        for zone in instance.definition.rejudge_zones:
            if zone not in zones:
                zones.append(zone)
    return tuple(zones) or (Zone.HAND,)
