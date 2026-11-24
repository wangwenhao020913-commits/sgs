"""规则启发式参与者（M3 票 13；`04` 的规则启发式小节 / ADR-011）。

本模块只交出 `04` 的第二层（**规则启发式**），与既有的
:class:`~sgs.agents.participants.RandomPlayer` 并存、由命令行显式选择。
它只满足 :class:`~sgs.agents.participants.Participant` 的
``respond(query) -> Action``，**不引入第二套接口**。

结构 = **选路器**（按 :class:`~sgs.state.QueryKind` 分派）→ 每类一个
``scorer(query, action) -> float`` → 取最高分；**平分由 ``"ai"`` 子流打破**
（同种子逐字节可复现，不另造随机源）。

**硬约束**（票 13 §硬约束 4 / `04` 的 AI 工程约束）：只读**询问自带的面**
（:attr:`Query.view`、:attr:`Query.legal_actions` 与询问自己的派生态：
``skill_id`` / ``spec`` / ``slots`` / ``preactivations`` / ``preactivation``）。本模块
**不直接** import ``sgs.engine`` / ``sgs.skills`` / ``sgs.data`` / ``sgs.resolution`` /
``sgs.timing``，也不读 ``GameState``——"信息隐藏不是约定"这条因此是可断言事实
（静态扫描住 ``tests/test_source_discipline.py``）。`ai-view` 票 07 起本模块经
``from . import identity`` 读身份信念；``identity`` 自己只 import ``sgs.data`` 的
配比表与 ``sgs.state`` 的视图类型（同一可见面的子集），它的 import 白名单在同一份
扫描里（``IDENTITY_IMPORT_ALLOWLIST``），不靠"相对导入天然安全"放行。
**行为推断（`ai-view` 票 07 起）与决策时限**：ADR-011 原文的"不做行为 / 历史推断"已被
推翻——历史经 ``PlayerView.public_history`` / ``public_counts`` 进视图，读点表由"不读"改
"读"；"不建决策时限 / 不读全知状态 / 同步纯函数"三条不变。

**身份信念（`ai-view` 票 07；spec §2.6）**：未知座位的 ``(P(敌), P(友))`` 从"配比均匀
后验"换成 :mod:`sgs.agents.identity` 的**精确联合后验**边缘——不矛盾的联合身份分配
（本人已知 / 主公已明 / 阵亡者已亮明）乘各公开行为事件的似然因子、归一。证据只有
``PlayerView.public_history`` 的**公开事实**（敌意 / 有利 / 濒死未救，按"行为类 × 身份 ×
先手方"的似然表）与 ``public_counts`` 的长期压力，加上两道反自我实现的护栏（干预折扣 +
先手方条件化）。改动**只发生在 :func:`_identity_relation` 的未知分支**：返回值契约、读点、
14 个调用点一字不改，``P(敌)`` 仍只作连续权重、不升格成硬判；判定层另导出
``(标签, 置信度)``（:func:`~sgs.agents.identity.identity_judgment`），解释面
（`ai_eval replay --explain`）读它。
无公开行为证据 / 配比算不出 ⇒ 逐值退回 I1 的均匀后验与中性 ``(0, 0)``。

**身份保守档（I1，票 21）**：敌友不再只有"主公 ↔ 反贼"这一对 0/1——已亮明的身份
（本人 / 主公 / 已阵亡 / ``identity_visible``）从 :data:`_IDENTITY_ROSTER` 里减掉，存活
未知座位按人头均分得到 ``(P(敌), P(友))``（:func:`_identity_relation`），替换约 8 个用点
的 0/1（伤害目标、座位步、连环、火攻、无懈、濒死救援）。``P(敌)`` **只作权重**、不升格成
"必须打谁"的硬判；内奸不把主公当优先敌人（主公留到最后）。算不出（人数不在 4~8 /
配比对不上）⇒ 退回中性的 0/0，与 I1 之前的行为一致。

**内奸行的局面函数（票 32）**：:data:`_HOSTILITY` 的其余五行仍是静态常量，内奸那一行改成
按**存活力量均衡**现算的标量 ``H``（:func:`_renegade_hostility`）：``(E[主公侧] − E[反贼侧]) / D``
再给主公加一道护主护栏（反贼侧还有人活着就不许把主公当敌人）。返回值仍是
``(max(H, 0), max(−H, 0))`` ⇒ 14 个调用点一行不改；未知座位取该行对剩余多重集的期望
（:func:`_renegade_unknown_relation`）。"只剩主公 ⇒ +1" 是 ``D = 1、E[反贼] = 0`` 算出来的，
**没有**特例分支。

**无懈目标化（D1，票 18）**：``RESPOND`` 不再"手里有牌就宣"——``cause = NULLIFY`` 时按
:func:`_nullify_declares` 读处理区顶牌与 ``query.target``，只在"敌人拿到利 / 自己或已明
盟友挨害"时宣；嵌套无懈与未登记的牌名一律 ``Pass``（不做链求解，spec §6.1 D1）。

**斩杀优先（D2，票 19）**：``_score_attack`` 多一条斩杀项——目标**这一下就死**时加
:data:`_LETHAL_STRIKE_VALUE`，残局不再把能一刀斩的目标放跑。判据收成
:func:`_lethal_to`（``_damage_to >= 目标体力``）**一处**：伤害牌打分问"现在就够不够"，
:func:`_wine_enables_kill` 问"加【酒】才够"，两者读同一份实际伤害（藤甲 / 白银狮子 /
【酒】/ 古锭刀），不会各说各话。D2 本身不读目标手牌。

**记牌 P(闪)（C1，票 20）**：【杀】族 / 【万箭齐发】这类**靠【闪】抵消**的伤害牌，其
"每张手牌 0.25 防御贴现"换成由**目标手里有【闪】的概率**导出的期望伤害
（:func:`_dodge_probability` / :func:`_expected_attack_score`）。输入只有 ``view`` 的
公开面（弃牌堆 / 处理区 / 装备 / 判定 / 自己手牌 / 他家的手牌**数** / 牌堆**数** /
武将牌上的**张数**与**本人**区的牌面）＋ :data:`_DECK_TABLE` 的静态档位表；档位靠守恒量
反推，**对不上就关闭记牌**、回落到常量贴现（不猜）。票 27 起
"武将牌上的牌"按张数补进守恒量、本人区里的牌面进可见面，故放置期间记牌**继续可用**
（他人的区只给张数、牌面留在未知池）。

**记牌批 2（C2，票 23）**：同一台记牌机器铺到另外两件"能把打出去的牌作废"的牌上——
【桃】（濒死 / 救援，:func:`_rescue_probability` × 斩杀分）与【无懈可击】（锦囊目标，
:func:`_nullification_probability` × 锦囊分，**两个都按"目标那一侧 × 关系权重"算**）；同时把
"未知牌一律按值 1.0"换成未知池里一张牌的**期望牌价**（:func:`_unknown_card_value`，装备按
槽位价）。未知池为空 ⇒ 期望 ``0.0``（不是 1.0 的魔数）；记牌关闭 ⇒ 三条判据一律 ``None`` ⇒
逐值回到 C2 之前的行为。收窄口径（无懈只数目标本人 / 逐座位独立超几何 / 多目标与延时锦囊
不打折 / 零收益锦囊不打折）登记在票 23 的 Answer 与台账。

**装备先手（D4，票 25）**：装备分不再恒为 :data:`_EQUIP_PLAY_VALUE`——先问
:func:`_equip_setup_score`"装上之后**候选面里**那张【杀】是不是**才**打得死"，是则把这一手抬到
那条【杀】**之前**（古锭刀对 0 手目标、青釭剑越过藤甲 / 白银狮子）。判据只看**真实候选**
与**真实伤害模型**（"假如装上"的局面副本走同一份 :func:`_damage_to`，同槽位替换也在里面），
且**够得着才算**（射程变短的替换一律不追加分，见 :func:`_keeps_reach`）；装上不改斩杀 /
射程可能变短 / 非武器 / 已经装着同一件 ⇒ 逐值不变。不做装备的一般优先级，也不做多步规划。

**盟友负分与主公 veto（`ai-view` 票 01）**：I1 那句"打盟友没有额外惩罚"被推翻——伤害牌
（:func:`_score_attack`，含 AoE 的逐目标）与单体有害锦囊（:func:`_harmful_trick_relation`）
都过同一个 :func:`_relation_term`（``_RELATION_FOCUS × (P敌 − P友)``），幅度使**确认
盟友**的总分低于过牌。主忠方在**出牌阶段挑目标**时主公不可作为目标
（:func:`_lord_veto_applies` 的规则级 veto）：单靠负分压不住斩杀项（``4.0 + 6.5 − 5.0``），
故它是"候选不可选"而不是又一项价格。边界：**不碰响应窗口**（借刀逼你砍主公是规则）、
**全体存活的 AoE 不 veto**（目标挑不了，只按逐目标期望差分）。实机依据 = seed 7032 里
忠臣三次回头打主公（当时主公是唯一合法【杀】目标）＋同一局拆主公的【过河拆桥】。

**内奸护主硬 veto（`ai-view` 票 11）**：挂在**同一格**、共用同一个
:data:`_LORD_VETO_PENALTY`——内奸在**还有别人活着**（存活人数 > 2）时也不能把主公当目标：
主公一死，只要内奸之外还有别人活着（反贼 / 忠臣都算），立刻由反贼获胜
（``victory.py`` 的主公死亡分支）。只有 1v1（只剩主公与内奸）放开，那是内奸"杀主公即胜"
的唯一赢法。原因同票 01：关系项压不住斩杀项（实机 seed 1066672543218525911：
``3 + 1 + 6.5 − 1.6667 = +8.83``）。同样不碰响应窗口、不 veto 全体强制目标
（:func:`_renegade_lord_veto_applies` 本体）。

**M5-07 的属性 / 连环 / 酒**：军争的取舍只接进**既有打分器**，不另造策略框架。用到的
公开事实 = 目标的装备区（藤甲 / 白银狮子 / 古锭刀）、自己的装备（青釭剑 / 朱雀羽扇）、
横置与 ``wine_active``、手牌**张数**、体力与已明身份；【杀】族判据走行为表
（:func:`_is_slash_card`），火【杀】/ 雷【杀】与转化、虚拟产物因此自动同权。四条取舍：
`_damage_to`（藤甲 / 酒 / 古锭刀 / 白银狮子的实际伤害）、`_score_wine`（只为一击必杀
才喝）、`_score_iron_chain` / `_score_reforge`（有收益才用、没收益重铸）、
`_score_fire_attack` 与 `_score_select_cards` 的火攻分支（无同花色来源就不开、
同花色弃牌换 1 点伤害，贵的牌宁可放弃）。

**预发动填表**（W4-4）：预发动后复合动作**不在** ``legal_actions`` 里，AI 读询问随附的
清单（主动 ``preactivations`` / 被动 ``preactivation``），按**现有打分器**填一个
``ActivateSkill(picks, choices)``：代价取 :func:`_card_value` 最低、目标取
:func:`_score_seat_option` 最高、转化源取旧 :func:`_score_select_cards` 会挑的那张；
提交后由引擎的**同一个校验器**（``sgs.skills.validate_activation``）放行。
**转化技也走同一条路**（W4-fix-2）：候选已从 ``legal_actions`` 收回，AI 读
``transform_preactivations`` 填 ``ActivateSkill(来源, 目标)``——目标只填 ``choices``，且
**只在清单带目标步时**填；没有目标步（目标由牌本身决定，huo-mobile-batch 票 07）时只
交来源，目标集由引擎取自**候选池**，再由同一份内部候选池解析成普通使用 / 打出
（`sgs.skills.resolve_conversion_confirmation`）。

**技能语义表** :data:`SKILL_VALUES`：键 = 技能 id（含 ``hero.`` / ``equip.`` 前缀），值 =
``(打分函数, 默认策略说明)``；打分函数签名统一 ``(query, action, view) -> float``
（`map.md` R4-Q6）。表里没有的技能**缺省 Pass**（"拿不准就不发动"）。三向同步纪律
（`03` 的「技能分类与过滤」：加武将 / 装备忘了教 AI 必须立刻红）住
``tests/test_heuristic_ai.py``：① 启用的每名武将的每个**非锁定**技能都有条目；
② 会问"是否发动"的每条非锁定装备技都有条目；③ 表里每个 id 都在数据文件里存在。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from types import MappingProxyType

from sgs.effects import (
    Randomness,
    TargetCount,
    behavior_for,
    cap_of,
    is_slash,
    target_sets_match,
    usable_options,
)
from sgs.state import (
    Action,
    ActivateSkill,
    ArrangeCards,
    ArrangeQuery,
    Distribute,
    DistributeQuery,
    CardRef,
    DiscardCards,
    DiscardQuery,
    DodgeQuery,
    EndPhase,
    Pass,
    PeachQuery,
    PlayCard,
    SubstituteJudgeCard,
    PlayPhaseQuery,
    PlayerPublic,
    PlayerView,
    Query,
    QueryKind,
    Reforge,
    RespondQuery,
    SelectCards,
    SelectCardsQuery,
    SelectOption,
    SelectOptionQuery,
    SelectOptions,
    SelectOptionsQuery,
    SkillActivationQuery,
    SlotKind,
    UseCard,
    WindowCause,
    step_option_key,
)

from . import identity as identity_model

__all__ = ["SKILL_VALUES", "HeuristicPlayer", "Score"]

# ---------------------------------------------------------------------------
# 打分要素：只取可从 ``query.view`` 与 action **局部算出**的量（票 13 §打分要素）
# ---------------------------------------------------------------------------

_DAMAGE_CARD_TYPES = frozenset({"duel", "barbarian_invasion", "arrow_barrage"})
"""**非【杀】族**的伤害牌名：出牌阶段优先于装备与过牌。

【杀】族（普通 / 火 / 雷与转化、虚拟产物）**不在**这张表里：它的判据是行为表的共享
计数键（:func:`~sgs.effects.is_slash`），见 :func:`_is_slash_card`。只往集合里补
``fire_slash`` / ``thunder_slash`` 两个牌名会绕过家族判据（M5-04 / M5-05b），
日后第五条【杀】又得再补一次。

.. note:: ``frozenset`` 只作**成员判定**（``card.type in ...``），从不迭代——
   源码纪律禁止的是集合**迭代**（逐进程序变化），不是集合本身。
"""

_VINE_ARMOR = "vine_armor"
_SILVER_LION = "silver_lion"
_ANCIENT_SCIMITAR = "ancient_scimitar"
_QINGGANG_SWORD = "qinggang_sword"
_VERMILION_FAN = "vermilion_feather_fan"
"""军争装备 / 既有武器的牌名**字面量**（判据只读公开装备区的牌面）。

同 :data:`_PLAY_PHASE_IDLE` 的理由：牌名是数据层的值，``sgs.data`` 不在本模块的可见面
白名单里；这里只拿它们做**相等比较**，不复制任何规则。
"""

_ELEMENTAL_SLASH_TYPES = frozenset({"fire_slash", "thunder_slash"})
"""**属性**伤害的【杀】（火焰 / 雷电）：连环传导只认属性伤害，普通【杀】不传导。

``frozenset`` 只作成员判定、不迭代；【朱雀羽扇】把普通【杀】转成火【杀】的能力由
:func:`_elemental_ready` 单独读（装备是公开信息）。
"""

_RESCUE_CARD_TYPES = frozenset({"peach", "wine"})
"""求桃窗口里能**回复 1 点**的牌：真【桃】与【酒】的濒死自救（M5-04）。

两者规则上同价，故共用 :func:`_peach_save_value`；``frozenset`` 只作成员判定、不迭代。
"""

_SLOT_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "armor": 2.2,
        "weapon": 2.0,
        "horse_defense": 1.2,
        "horse_offense": 1.0,
        "treasure": 1.0,
    }
)
"""装备按**槽位**的价值序（票 13 §DISCARD「装备按槽位价值排序」）。"""

_TRICK_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "ex_nihilo": 3.5,
        "snatch": 3.2,
        "indulgence": 3.2,
        "dismantlement": 3.0,
        "peach_garden": 2.5,
        "harvest": 2.0,
        "lightning": 1.5,
        "nullify": 0.0,
        "dodge": 0.0,
        "peach": 0.0,
    }
)
"""锦囊（含延时锦囊）的使用价值；【无懈可击】/【闪】不能在出牌阶段主动使用 ⇒ 0。"""

_UNREGISTERED_TRICK_VALUE = 1.0
"""表里没有牌名的锦囊：按"一张普通牌的保守价"估（票 13 §DISCARD 的兜底口径）。

**不是**未知牌的价（那是 :func:`_unknown_card_value`）：这里牌面是**知道的**，只是还没教
AI 它值多少，故保持常量、不随牌局漂 —— 拿不着模型的牌不该因为牌堆变薄而涨价。
"""

_CARD_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "peach": 6.0,
        "dodge": 4.0,
        "nullify": 3.5,
        "ex_nihilo": 3.5,
        "snatch": 3.2,
        "indulgence": 3.2,
        "dismantlement": 3.0,
        "slash": 3.0,
        "duel": 2.5,
        "barbarian_invasion": 2.5,
        "arrow_barrage": 2.5,
        "wine": 2.4,
        "fire_attack": 2.4,
        "supply_shortage": 2.6,
        "harvest": 2.2,
        "peach_garden": 2.0,
        "borrowed_sword": 2.0,
        "iron_chain": 1.6,
        "lightning": 1.0,
    }
)
"""牌名价值表：**弃牌**与**排列**共用的"这张牌多有用"（票 13 §DISCARD / §ARRANGE）。

火【杀】/ 雷【杀】与转化、虚拟【杀】**不逐名入表**：:func:`_card_value` 按家族判据
回落到普通【杀】的同价（见 :func:`_is_slash_card`）。
"""

_EQUIP_VALUE = 1.5
"""没有登记槽位价值的装备：按"一件普通装备"计（:func:`_card_value` 的兜底）。"""

_HIDDEN_CARD_VALUE = 1.0
"""牌面不可见时的**兜底**牌价：隐藏槽（只有位置、没有牌面）按"一张普通的牌"计。

票 23（C2）起它只作**记牌关闭**时的兜底——记牌可用时，一张看不见牌面的牌按未知池里
一张牌的期望价算（:func:`_unknown_card_value`），不再一律回这个魔数。
"""

_DAMAGE_BASE = 3.0
_TARGET_BONUS = 1.0
_HAND_DEFENSE_WEIGHT = 0.25
_HAND_DEFENSE_CAP = 4
_SAVE_VALUE = 6.0
_CONSERVE_VALUE = 0.5
_HEAL_VALUE = 3.5
_EQUIP_PLAY_VALUE = 2.5
_EQUIP_SETUP_MARGIN = 0.1
"""「先装后杀」要抢在那条【杀】前面所需的**一格**分（D4，票 25）。

**不是**"装备比【杀】值钱"的估价：装上之后这一刀的伤害会变，那份收益在**下一步**的
斩打分里才出现（:func:`_score_attack` 的斩杀项读真实伤害）。这里只补"同一次询问里两个
候选的先后"所必需的那点差，故取一个只为"严格大于"的常数，不参与任何比例。"""
_DODGE_VALUE = 5.0
_DODGE_SKILL_VALUE = 4.5
_RESPOND_VALUE = 5.0
"""响应窗口的分值（真牌略高于提供者）。

**同一张面 = 同一档**（table-ux-overhaul 票 04 / Q13 / Q17）：引擎把"需要（使用 / 打出）牌"
的窗口按档拆成多张面（武将档 → 装备档 → 牌档），一次询问里**只有一档**的候选。这几个常量的
相对高低因此只在**同档内**比较（真正会撞上的只有牌档那一张面：真牌 vs 转化类提供者，
例如【蛊惑】）；跨档的"真牌比【八卦阵】高 0.5"不构成决策——档序由引擎给，拒绝低档才会被
问到下一档，而每一档的 ``Pass`` 都在面上。打分器因此不需要按档重标定：它只在"这一档里选
最好的 / 都不发动"之间比较，也不会因为分面漏填（每一档都有一张完整的合法面）。
"""

_ILLEGAL_PENALTY = -100.0
_UNKNOWN_SKILL_VALUE = -1.0
"""表里没有的技能：**低于 Pass**（0.0）⇒ 缺省保守，拿不准就不发动。"""
_ABORT_SKILL_PENALTY = -100.0
"""撤销一次**已经发动**的技能（发动后流水线里的「取消」）：比任何成本都差。

与 :data:`_ILLEGAL_PENALTY` 同值但**语义不同**（那条是"这条应答本身不合法"），
故各留一个常量，免得日后调一处误伤另一处。理由见 :func:`_is_committed_skill_step`。
"""
_CYCLE_VALUE = 1.5
"""「弃一张换一张」的每张期望增益（【制衡】的骨架候选与它的选牌步共用同一估值）。

牌面价值低于它的牌才值得换掉；两处共用一个常量，保证"发不发动"与"弃哪几张"的
模型一致（否则发动决策与随后的选牌步会各说各话）。
"""
_RENDE_RECOVER_VALUE = 4.0
"""【仁德】本阶段首次给到 ≥2 张时那 1 点体力对 AI 的价值。

发动决策（:func:`_score_rende`）与选牌步（:func:`_score_select_cards` 的给牌分支）共用，
保证"给 2 张还是 1 张"两处算的是同一笔账。
"""
_PLAY_PHASE_IDLE = "PHASE_PLAY_IDLE"
"""时机标识的**字面量**（:class:`~sgs.timing.Timing` 是 ``str`` 枚举，故可直接比）。

为什么抄一个字面量而不是 import：本模块**不许碰全知面**，而
``tests/test_source_discipline.py`` 的可见面白名单把 ``sgs.timing`` 判红
（`HEURISTIC_ALLOWED_SGS_MODULES` 只有 ``sgs.effects`` 与 ``sgs.state``）。
"""

_SEAT_PREFIX = "seat:"
"""座位选项的规范文本前缀；**规范声明** = `sgs.skills.flow.SEAT_PREFIX`（本处是被边界允许的副本）。

为什么抄一个**字面量**而不 import：``sgs.skills`` 不在
``tests/test_source_discipline.py`` 的可见面白名单里（`HEURISTIC_ALLOWED_SGS_MODULES`
只有 ``sgs.effects`` / ``sgs.state``），本模块连公开入口都不许碰。改前缀时必须同步
`flow.SEAT_PREFIX`、本副本与 ``tools/sgs_web/static/app.js`` 的跨语言副本（W6-06b）；
边界不是"全场单一真源"，而是"接入层导规范值 + 两个被边界允许的带注释副本"。
"""

_LORD = "lord"
_LOYALIST = "loyalist"
_REBEL = "rebel"
_RENEGADE = "renegade"
_ALLY = "ally"
_ENEMY = "enemy"
"""身份的**字面量**（:class:`~sgs.data.Identity` 是 ``str`` 枚举，故可直接比）。

同 :data:`_PLAY_PHASE_IDLE` 的理由：``Identity`` 住在 ``sgs.data``，不在本模块的可见面
白名单里。派生出来的 ``frozenset``（:func:`_enemy_identities` / :func:`_ally_identities`）
只作成员判定、从不迭代（源码纪律禁止的是迭代）。

``_ALLY`` / ``_ENEMY``（M7 票 08）是 PvE 的两个阵营值：它们**同时**是"某人是我的友 / 敌"
这条关系的值（见 :data:`_HOSTILITY`）——同一组字面量在两侧出现是刻意的（阵营名 = 关系名）。
"""

_NEUTRAL = "neutral"
"""敌对值表的第三值「中立」。**它不是身份**，只在这张表的值域里出现。"""

_HOSTILITY: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        # 我 ↓ ／ 对方 → 关系
        # 主公侧两行**逐格相同**（忠臣与主公同阵营）——分别写出来是为了将来能各自改，
        # 不是漏抄了公共行。
        _LORD: MappingProxyType(
            {
                _LORD: _ALLY,
                _LOYALIST: _ALLY,
                _REBEL: _ENEMY,
                _RENEGADE: _ENEMY,
                _ALLY: _NEUTRAL,
                _ENEMY: _NEUTRAL,
            }
        ),
        _LOYALIST: MappingProxyType(
            {
                _LORD: _ALLY,
                _LOYALIST: _ALLY,
                _REBEL: _ENEMY,
                _RENEGADE: _ENEMY,
                _ALLY: _NEUTRAL,
                _ENEMY: _NEUTRAL,
            }
        ),
        _REBEL: MappingProxyType(
            {
                _LORD: _ENEMY,
                _LOYALIST: _ENEMY,
                _REBEL: _ALLY,
                _RENEGADE: _NEUTRAL,
                _ALLY: _NEUTRAL,
                _ENEMY: _NEUTRAL,
            }
        ),
        _RENEGADE: MappingProxyType(
            {
                # 内奸**不把主公当敌人**：内奸要最后一个人活着，主公早死反贼就赢。
                # 票 32 起 :func:`_identity_relation` 在 ``mine == _RENEGADE`` 时**不查这一行**
                # （走局面函数 :func:`_renegade_hostility`，按存活力量均衡 + 护主护栏）；
                # 这一行留着是给 :func:`_enemy_identities` / :func:`_ally_identities` 的派生
                # 视图（第二读取方：``_score_baonue`` 的 ``"lord" in _ally_identities``）。
                _LORD: _NEUTRAL,
                _LOYALIST: _ENEMY,
                _REBEL: _ENEMY,
                _RENEGADE: _NEUTRAL,
                _ALLY: _NEUTRAL,
                _ENEMY: _NEUTRAL,
            }
        ),
        _ALLY: MappingProxyType(
            {
                _LORD: _NEUTRAL,
                _LOYALIST: _NEUTRAL,
                _REBEL: _NEUTRAL,
                _RENEGADE: _NEUTRAL,
                _ALLY: _ALLY,
                _ENEMY: _ENEMY,
            }
        ),
        _ENEMY: MappingProxyType(
            {
                _LORD: _NEUTRAL,
                _LOYALIST: _NEUTRAL,
                _REBEL: _NEUTRAL,
                _RENEGADE: _NEUTRAL,
                _ALLY: _ENEMY,
                _ENEMY: _ALLY,
            }
        ),
    }
)
"""**敌对值表**：``{我: {对方: 友 / 中立 / 敌}}``——"谁恨谁"这一层的唯一真源（票 08）。

值域是**三值**（友 / 中立 / 敌），不是数值：这一层只回答关系，幅度留在
:func:`_identity_relation` 的 ``(P(敌), P(友))`` 上（权重不进这张表、也不和数据混层）。
:func:`_enemy_identities` / :func:`_ally_identities` 是它的两个派生视图，
:func:`_identity_relation` 的"已亮明"分支直接查它（不再是 if-chain）。

身份局四行逐格等于票 21 的 if-chain（票 08 是**等价重构**，16 个身份对的输出逐对相同）：
主公侧认忠臣为友、反贼 / 内奸为敌；反贼认主公侧为敌、反贼为友、内奸中立；内奸认忠臣 /
反贼为敌、主公中立（**不把主公当优先敌人**）、且没有盟友。

**内奸那一行是历史值，不是今天的取值**（票 32）：:func:`_identity_relation` 在
``mine == _RENEGADE`` 时走 :func:`_renegade_hostility`（按存活力量均衡 + 护主护栏），
本表这一行只服务 :func:`_enemy_identities` / :func:`_ally_identities` 的派生视图。

PvE 两行（盟 → 盟 = 友、敌 → 敌 = 友、盟 → 敌 = 敌、敌 → 盟 = 敌）只是把
``PveConfig`` 的阵营名读成关系名：PvE 开局全部 ``identity_visible``（票 01 §3）⇒ 读到的是
**确定的**友 / 敌，不走概率档。**横跨两个模式的格子恒「中立」**（主公 × 友这类永不共局）：
填中立是"永不读到"的结构事实，不是语义选择。

为什么是字面表：本模块的可见面白名单不许 import ``sgs.data``（同 :data:`_IDENTITY_ROSTER`），
而关系是静态内容表。真源仍是数据层——``tests/test_heuristic_ai.py`` 双向核：表的行 / 列
逐项等于 ``sgs.data.identities.Identity`` 的六个值，值域恰是这三值，且 16 个身份对的输出
与 if-chain 版逐对相同。
"""


def _hostility(mine: str, theirs: str) -> str:
    """``mine`` 眼里的 ``theirs``：友 / 中立 / 敌（查 :data:`_HOSTILITY`）。

    行或列不在表里（人为局面写了个不存在的身份）⇒ 中立，**不猜**——与
    :func:`_identity_relation`"算不出就退回 (0, 0)"同一条 fail-closed 口径。
    """
    row = _HOSTILITY.get(mine)
    return _NEUTRAL if row is None else row.get(theirs, _NEUTRAL)


_IDENTITY_ROSTER: Mapping[int, tuple[str, ...]] = MappingProxyType(
    {
        4: (_LORD, _LOYALIST, _REBEL, _RENEGADE),
        5: (_LORD, _LOYALIST, _REBEL, _REBEL, _RENEGADE),
        6: (_LORD, _LOYALIST, _REBEL, _REBEL, _REBEL, _RENEGADE),
        7: (_LORD, _LOYALIST, _LOYALIST, _REBEL, _REBEL, _REBEL, _RENEGADE),
        8: (
            _LORD,
            _LOYALIST,
            _LOYALIST,
            _REBEL,
            _REBEL,
            _REBEL,
            _REBEL,
            _RENEGADE,
        ),
    }
)
"""4~8 人的身份配比**字面表**（I1 身份保守档，spec §6.4；票 21）。

**为什么是字面表**：同 :data:`_DECK_TABLE`——本模块的可见面白名单不许 import ``sgs.data``，
而配比是静态内容表、不是询问面。**真源仍是数据层**：
``tests/test_heuristic_ai.py::test_the_identity_roster_table_matches_the_content_pack``
把这张表与 ``sgs.data.identities.IDENTITY_ROSTERS`` 逐人数反查（多重集相等），内容包 /
配比漂移而表没跟上即红。

键 = 座位数（= ``len(view.players)``）；值 = 该人数的身份多重集。**2 人无身份局不在表里**：
查不到就退回"关系未知"的 ``(0, 0)``（= 票 21 之前的行为），不猜。
"""

_SEAT_TARGET_VALUE = 2.0
"""座位步里敌 / 友的分值幅度：按 ``P(敌)`` / ``P(友)`` 加权（I1），中性是 ``(0, 0)``。"""

_HERO_CARD_SKILL_VALUE = 2.0
"""展示武将牌时"获得该武将牌上的一个技能"一行的通用估值（票 07；票 02 §4.2 B5）。

一次性获得一条**持久**能力：不比单张牌便宜（一张牌约 1.0~1.5），又低于"回血救人"
（4.0~6.0）。具体武将若在 :data:`_OPTION_VALUES` 里登记了自己的选项 id，那条优先；
这里是**没有登记时**的缺省，只保证"不与放弃打平、不被效果行淹没"。
"""

_HERO_CARD_EFFECT_VALUE = 2.5
"""展示武将牌时**技能自己的效果行**（选项 2）的通用估值（票 07）。

这一行的语义由技能自己定（【天成】= +1 上限 / 回复 1 / 摸 X 张），引擎给的文本就是它。
按"一次性但立即兑现"估：略高于"拿一个技能"，因为 X 随次数增长、且含回血。
一将一票可以改自己的选项 id 的登记值来覆盖这条缺省。
"""

_RELATION_FOCUS = _RESPOND_VALUE
""""这个目标值不值得打"的关系项幅度：``_RELATION_FOCUS × (P(敌) − P(友))``（`ai-view` 票 01）。

**为什么是这一个式子**：票面要求把 `_score_attack` / `_score_card_use` 从"只按 `P(敌)`
加权、打盟友不加不减"改成**对称**的关系项（Q4："主公对主忠方硬 veto；其余已明盟友定价
负分"）。票面点的四处先例里，**逐值同形**的是两处：:func:`_luanwu_respond_value` 的
``_RESPOND_VALUE × (enemy − ally)`` 与 :func:`_score_distribute` 的
``_SEAT_TARGET_VALUE × (enemy − ally)``；另两处是同**族**而不是同式——
:func:`_peach_save_value` 是 ``_SAVE_VALUE × (1 − 2·P敌)``（把"两边都不是"也记成盟友），
:func:`_score_iron_chain` 的每一项按**分支**各自取常量（横置 / 重置 × 敌 / 友）。
故本票的口径是"把 ``k × (敌 − 友)`` 这一个**同形**式子推广到两条主路径"，
系数直接取 :data:`_RESPOND_VALUE`（见下），敌 / 友用**同一个**系数才叫"对称"。

**为什么是 5.0**：票面的判决信号是"确认盟友的总分低于过牌"，而伤害牌有
:data:`_DAMAGE_BASE` + :data:`_TARGET_BONUS` 的底价 4.0（单体锦囊最高 3.5）⇒ 系数必须
**盖过底价**：``4.0 − 5.0 = −1.0 < 0``。5.0 就是 :data:`_RESPOND_VALUE`——与
:func:`_luanwu_respond_value` 的"友 = −5 / 敌 = +5"同价，不另造一个魔数。

取代的是 I1 的 ``_ENEMY_FOCUS``（2.0）：那一档只给敌人加分、盟友一分不扣，正是实机
seed 7032 里忠臣回头打主公（主公是当时唯一合法【杀】目标）的根因。**敌人一侧因此从 +2
涨到 +5**：关系项对 P(敌) = 1 的已明敌人更"值得打"，对未知座位按 ``P(敌) − P(友)`` 的
**正负**取舍（先验偏敌 ⇒ 仍是正项，不会把 AI 变成不出牌的绵羊）。

**它压不住斩杀项**（``4.0 + 6.5 − 5.0 = +5.5``）：主公那一格由
:func:`_lord_veto_applies` 的**规则级** veto 兜住，其余已明盟友没有这条兜底——两笔账
分开记，别把 veto 写成"再大一点的负分"。

**已知边界（不是"逐值不变"）**：系数只盖过**底价** 4.0，没盖过伤害加成
（:data:`_DAMAGE_POINT_VALUE` × 多点伤害）——确认盟友身上一张**两点以上**的伤害牌
（藤甲火【杀】/ 【酒】/ 古锭刀）仍可能算到 0 以上（``4.0 + 1.4 − 5.0 = +0.4``）。
身份局里"已明的非主公盟友"只有阵亡者与 `identity_visible` 的 PvE 队友 ⇒ 实机只落在 PvE；
票面的判决信号（确认盟友的**普通**【杀】/ 单体锦囊低于过牌）与主公那一格（veto）都成立。
要连多点伤害一起压住得让惩罚随伤害放大，那是"按效果计价"的活，归 `SKILL_VALUES` / 后续票。
"""

_LORD_VETO_PENALTY = -50.0
"""把**主公**当目标时的硬 veto 分（Q4）：**不是价格**，是"这个候选不可选"。

主忠方恒生效；内奸在**还有别人活着**时共用同一个常量（`ai-view` 票 11）——两者都是
"这条候选不可选"，语义相同，故不另立一个数（若日后要区分，先拆成两个字面量并同步两处
docstring）。

只需严格低于任何可选的合法候选（``EndPhase`` = 0、最贵的一张牌的负分也只有 −6 上下），
不必与 :data:`_ILLEGAL_PENALTY` 同值——那条是"这条应答本身不合法"，语义不同，各有各的
常量（同 :data:`_ABORT_SKILL_PENALTY` 与 :data:`_ILLEGAL_PENALTY` 分列的理由）。
"""

_DAMAGE_POINT_VALUE = 1.4
"""每**多一点**实际伤害的加权（藤甲的火焰 +1、【酒】的 +1、古锭刀的 +1）。

只作同一张牌内部的比较：它让"火【杀】打藤甲"严格高于"普通【杀】打藤甲"，
而不把伤害牌的整体序（伤害 ＞ 装备 ＞ 过牌）翻过来。
"""

_WASTED_ATTACK_PENALTY = 5.0
"""目标**实际不会受伤**时扣的分（普通【杀】/【南蛮】/【万箭】打藤甲）。

扣到伤害牌低于「结束阶段」（0）⇒ AI 宁可不出这一张，也不把【杀】浪费在无效目标上；
另一个合法目标仍按正分参评，故"另有目标时避开藤甲"不是靠拒绝出牌做到的。
"""

_WINE_LETHAL_VALUE = 6.5
"""【酒】+【杀】正好斩杀时，那口【酒】的价值（高于任何单张伤害牌的裸分）。

故意高：这一手是**击杀**，比"再打一刀不死的【杀】"值钱；且必须高过同一询问里
那张【杀】自己的分数（否则贪心会先出【杀】、把【酒】留成废牌）。
"""

_LETHAL_STRIKE_VALUE = _WINE_LETHAL_VALUE
"""这一下**当场打死**（``_damage_to >= 目标体力``）时给伤害牌加的斩杀分（D2，票 19）。

**直接引用** :data:`_WINE_LETHAL_VALUE`：两者是同一笔"击杀"的价，只是加在不同东西上
（一口【酒】/ 一张伤害牌）；各写一个 6.5 会在改价时漂开。真要分头调，先拆成两个字面量
并同步两处 docstring。

击杀是残局里最值钱的一步，不该被手牌贴现或已明敌人加权翻过去；判据与【酒】那条共用
:func:`_lethal_to`（"现在就够" vs "加【酒】才够"）。**藤甲**把普通【杀】挡成 0 时一分
不加（【酒】/ 古锭刀的加伤照常算进来；白银狮子只是把伤害封在 1，够不够仍看目标体力；
非【杀】家族的伤害牌按 1 点算，与 :func:`_damage_to` 同口径）。**不看目标手牌数**：
spec §6.1 D2 的"改什么"只要求 ``_damage_to >= 体力``，用户故事里的"0 手牌"是**残局
场景**而不是判据——概率版 P(闪) 归 C1（记牌那票）。

取值只需盖过"两个**非斩杀**目标之间"的常见分差——已明敌人（:data:`_RELATION_FOCUS`
5.0）与手牌贴现（:data:`_HAND_DEFENSE_WEIGHT` × :data:`_HAND_DEFENSE_CAP` = 1.0）之和 6.0——
就能让"一刀能斩的残局目标"稳过"更健康的敌人"，同时不动伤害牌的整体序（伤害 ＞ 装备 ＞
过牌）。它不建模"这一刀可能被【闪】抵消"：那是概率贴现，归 C1，本票只做公开可算版。
"""

_WINE_WASTE_PENALTY = -1.0
"""杀不掉 / 手里没有【杀】时喝【酒】：低于「结束阶段」（0）⇒ 一口都不浪费。"""

_REFORGE_VALUE = 0.6
"""【铁索连环】重铸的固定收益：**换一张牌**（弃置 + 摸一张）而不是使用。

高于「结束阶段」（0）⇒ 没有收益时 AI 会把铁索换掉、不空放；低于任何有产出的使用
（伤害 / 装备 / 过牌）⇒ 有收益时永远先选"用"。重铸是独立行动，不算使用、不触发
使用锦囊类技能（M5-03），故它不会被当成"用了一张锦囊"来估值。
"""

_IRON_CHAIN_RESET_FRIENDLY_VALUE = 2.0
"""铁索对**已在连环上的自己 / 已明盟友**的收益：重置 = 脱离连环，躲开下一次火伤传导。"""

_IRON_CHAIN_RESET_ENEMY_PENALTY = 1.5
"""把**已在连环上的敌人**重置掉是亏的：等于替他拆掉传导的引线（扣分的大小）。"""

_IRON_CHAIN_EXPOSE_FRIENDLY_PENALTY = 2.0
"""把**未横置的自己 / 盟友**横置是纯亏：替对手把传导接上（扣分的大小）。"""

_IRON_CHAIN_CHAIN_ENEMY_VALUE = 2.0
"""横置敌人：只有在**手里有属性伤害**（火 / 雷【杀】或朱雀羽扇）时才值得——先串起来，
下一刀火【杀】就能顺着连环烧过去。"""

_FIRE_ATTACK_BASE = 1.0
_FIRE_ATTACK_SPARE_WEIGHT = 0.4
_FIRE_ATTACK_SPARE_CAP = 2
"""【火攻】的基线价值与"手上还有几张别的牌"的加权（每张 +0.4，最多算 2 张）。

同花色弃牌是**可选**的：手里一张别的牌都没有时，连"可能同花色"都没有 ⇒ 火攻不产生
伤害（见 :data:`_FIRE_ATTACK_NO_SPARE_PENALTY`）。加权封顶是为了让火攻始终低于
"出一张【杀】"（伤害牌优先），不把出牌序翻过来。
"""

_FIRE_ATTACK_NO_SPARE_PENALTY = -1.0
"""手里只剩这张【火攻】（没有同花色牌可弃）/ 只指向自己 ⇒ 低于「结束阶段」，不出。"""

_FIRE_ATTACK_VINE_BONUS = 1.0
"""火攻是**火焰**伤害：目标穿藤甲时 +1（公开装备）。"""

_FIRE_ATTACK_DISCARD_VALUE = 3.0
"""火攻同花色弃牌换来的 1 点火焰伤害值多少：只弃**不高于它**的牌，贵的牌宁可放弃。

"1 点伤害 ≈ 3.0 牌价"与 :data:`_CARD_VALUES` 的【杀】（3.0）同量级：比它贵的
（【桃】6 / 【闪】4 / 【无懈】3.5）留着更值；比它便宜的（【杀】、装备、普通锦囊）弃掉。
"""

_DECK_TABLE: Mapping[str, tuple[int, Mapping[str, int]]] = MappingProxyType(
    {
        "basic_53": (
            53,
            MappingProxyType(
                {
                    "dodge": 15,
                    "peach": 8,
                    "slash": 30,
                }
            ),
        ),
        "standard_108": (
            108,
            MappingProxyType(
                {
                    "dodge": 15,
                    "peach": 8,
                    "slash": 30,
                    "dismantlement": 6,
                    "snatch": 5,
                    "ex_nihilo": 4,
                    "nullify": 4,
                    "duel": 3,
                    "barbarian_invasion": 3,
                    "arrow_barrage": 1,
                    "peach_garden": 1,
                    "borrowed_sword": 2,
                    "harvest": 2,
                    "indulgence": 3,
                    "lightning": 2,
                    "crossbow": 2,
                    "qinggang_sword": 1,
                    "double_sword": 1,
                    "green_dragon_blade": 1,
                    "serpent_spear": 1,
                    "axe": 1,
                    "halberd": 1,
                    "qilin_bow": 1,
                    "ice_sword": 1,
                    "eight_diagrams": 2,
                    "renwang_shield": 1,
                    "jueying": 1,
                    "dilu": 1,
                    "zhuahuang_feidian": 1,
                    "chitu": 1,
                    "dawan": 1,
                    "zixing": 1,
                }
            ),
        ),
        "standard_ex_160": (
            160,
            MappingProxyType(
                {
                    "dodge": 24,
                    "peach": 12,
                    "slash": 30,
                    "fire_slash": 5,
                    "thunder_slash": 9,
                    "wine": 5,
                    "dismantlement": 6,
                    "snatch": 5,
                    "ex_nihilo": 4,
                    "nullify": 7,
                    "duel": 3,
                    "barbarian_invasion": 3,
                    "arrow_barrage": 1,
                    "peach_garden": 1,
                    "borrowed_sword": 2,
                    "harvest": 2,
                    "supply_shortage": 2,
                    "fire_attack": 3,
                    "iron_chain": 6,
                    "indulgence": 3,
                    "lightning": 2,
                    "crossbow": 2,
                    "qinggang_sword": 1,
                    "double_sword": 1,
                    "green_dragon_blade": 1,
                    "serpent_spear": 1,
                    "axe": 1,
                    "halberd": 1,
                    "qilin_bow": 1,
                    "ice_sword": 1,
                    "eight_diagrams": 2,
                    "renwang_shield": 1,
                    "silver_lion": 1,
                    "vine_armor": 2,
                    "ancient_scimitar": 1,
                    "vermilion_feather_fan": 1,
                    "jueying": 1,
                    "dilu": 1,
                    "zhuahuang_feidian": 1,
                    "chitu": 1,
                    "dawan": 1,
                    "zixing": 1,
                    "hualiu": 1,
                }
            ),
        ),
    }
)
"""记牌的牌堆档位**字面表**（票 05 §2 / spec §6.3；C1，票 20）。

**为什么是字面表**：本模块的可见面白名单不许 import ``sgs.data``
（``tests/test_source_discipline.py`` 的 ``HEURISTIC_ALLOWED_SGS_MODULES``），而牌堆分布是
静态内容表、不是询问面。**真源仍是内容包**：``tests/test_heuristic_ai.py`` 把这张表与
``load_content_pack(...).decks[*].expect.by_type`` 逐项反查，内容包漂移而表没跟上即红。

键 = 牌堆档位；值 = ``(总张数, {牌名: 张数})``。总张数**独立成项**而不是现算 ``sum``：
字面表被改坏（改了逐项没改总数）时，:func:`_deck_profile` 的运行期 self-check 能看见并
关闭记牌。三档总张数互不相同（53 / 108 / 160）⇒ 守恒量能唯一定档。
"""

_DODGE_DEFENDED_TYPES = frozenset({"arrow_barrage"})
"""**用【闪】抵消**的伤害牌里不在【杀】族的那几张（【万箭齐发】）。

【杀】族走 :func:`_is_slash_card`（行为表的族键）；【决斗】/【南蛮入侵】要的是【杀】，
不吃 P(闪)，仍走常量手牌贴现。
"""

_DODGE_CONSUMED_VALUE = 1.0
"""躲掉也消耗对方一张【闪】的收益，用**分数**计（≈ 一张普通牌的价值）。

与 :data:`_DAMAGE_POINT_VALUE` 是两把尺：伤害按"每点 1.4 分"计价，而"对方少一张【闪】"
按一张普通牌 ≈ 1.0 分计价。**不能把收益写成 1.0 个伤害点**：那样 1 点伤害的期望
``1×(1−p) + 1×p`` 恒等于 1，P(闪) 就完全不进普通【杀】的分数——那不是"期望伤害"，而是
把常量贴现删掉了。收益项只把"这一刀被躲掉"的损失**部分**抵回来（否则会系统性低估进攻，
spec §6.3），所以 P(闪) 始终影响分数：1 点伤害的期望增量从 ``0``（p=0）降到 ``−0.4``
（p=1，= 白扔一张牌换对方一张【闪】）。

数值上它恰好等于旧贴现的封顶 ``_HAND_DEFENSE_WEIGHT × _HAND_DEFENSE_CAP``（0.25 × 4），
但**故意各留一个字面量**：那条是"旧模型最多打这么多折"，这条是"一张牌值多少分"；把一条
绑到另一条上，调旧贴现的封顶就会偷偷改掉"消耗一张牌"的价。改一处时先想清楚是不是也要改
另一处（与 :data:`_ILLEGAL_PENALTY` / :data:`_ABORT_SKILL_PENALTY` 同一条处置：同值不同义）。
"""


def _is_slash_card(card: CardRef | None) -> bool:
    """这张牌的**产物牌型**是不是【杀】族（普通 / 火 / 雷与转化、虚拟产物）。

    判据 = 行为表的共享计数键（:func:`~sgs.effects.is_slash`，M5-02 / M5-05b）：火【杀】/
    雷【杀】不是"往牌名集合里补的两个名字"，而是家族的一员；转化 / 虚拟【杀】按**产物
    牌型**查行为表，故同一个判据也覆盖它们。表外牌名（不该出现）fail-open 成"不是【杀】"。
    """
    if card is None:
        return False
    try:
        return is_slash(behavior_for(card))
    except KeyError:  # pragma: no cover - 数据表外的牌名（不该出现）
        return False


def _damage_card(card: CardRef) -> bool:
    """这张牌能不能造成伤害：:func:`_is_slash_card` 或 :data:`_DAMAGE_CARD_TYPES`。"""
    return _is_slash_card(card) or card.type in _DAMAGE_CARD_TYPES


def _card_value(card: CardRef | None, *, view: PlayerView | None = None) -> float:
    """一张牌的局部估值：牌名表 → 装备槽位 → 未知兜底（不读任何隐藏信息）。

    ``card is None``（只有位置、没有牌面：**别人的手牌**）时，C2（票 23）起按
    **未知池的期望牌价**算（:func:`_unknown_card_value`）——票 23 之前恒为 1.0。
    ``view`` 不给（没有视角可读）或记牌关闭 ⇒ :data:`_HIDDEN_CARD_VALUE`。
    """
    if card is None:
        return _HIDDEN_CARD_VALUE if view is None else _unknown_card_value(view)
    known = _CARD_VALUES.get(card.type)
    if known is not None:
        return known
    if _is_slash_card(card):
        # 火【杀】/ 雷【杀】/ 转化与虚拟【杀】与普通【杀】同价：家族判据，不逐名抄表。
        return _CARD_VALUES["slash"]
    if card.slot is not None:
        return _SLOT_VALUES.get(card.slot.value, _EQUIP_VALUE)
    return _UNREGISTERED_TRICK_VALUE


def _me(view: PlayerView) -> PlayerPublic:
    return view.players[view.seat]


def _wears(player: PlayerPublic, type_id: str) -> bool:
    """``player`` 的装备区里有没有这张装备（公开信息；只比牌名）。"""
    return any(card.type == type_id for card in player.equip)


def _ignores_armour(me: PlayerPublic) -> bool:
    """我装备区的武器是否**无视防具**（【青釭剑】）：藤甲对它不设防（M5-05a）。"""
    return _wears(me, _QINGGANG_SWORD)


def _damage_to(
    card: CardRef | None, view: PlayerView, seat: int, *, wine: bool | None = None
) -> int:
    """这张伤害牌落到 ``seat`` 身上大约造成几点伤害（只读公开的装备与状态）。

    只建模**公开可读**的修正，不做概率：

    - **藤甲**：普通【杀】无效（0）；火【杀】+1（火伤 +1）；雷【杀】照常。我装备
      【青釭剑】时整条让开（"无视防具"）；
    - **【酒】**：``me.wine_active`` 的下一张【杀】+1（公开状态）；``wine=True`` 表示
      "假设这一口已经喝下"（``_wine_enables_kill`` 推演斩杀线时用），缺省读当前状态；
    - **古锭刀**：目标**没有手牌**时 +1（手牌张数是公开信息）；
    - **白银狮子**：每次伤害至多 1 点（封顶在最后一步），**青釭剑**越过它——与
      ``catalog._silver_lion_damage_cap`` 同一条口径；【酒】的 +1 也吃这道封顶。

    非【杀】的伤害牌（【决斗】/【南蛮】/【万箭】）不参与装备交互，按 1 点计。
    """
    if card is None:
        return 1
    target = view.players[seat]
    me = _me(view)
    damage = 1
    if _is_slash_card(card):
        vine = _wears(target, _VINE_ARMOR) and not _ignores_armour(me)
        if vine:
            if card.type == "slash":
                return 0
            if card.type == "fire_slash":
                damage += 1
        add_wine = me.wine_active if wine is None else wine
        if add_wine:
            damage += 1
        if _wears(me, _ANCIENT_SCIMITAR) and target.hand_count == 0:
            damage += 1
    if _wears(target, _SILVER_LION) and not _ignores_armour(me):
        damage = min(damage, 1)
    return damage


def _wounded(view: PlayerView) -> bool:
    me = _me(view)
    return me.hp < me.hp_max


def _lethal_to(
    card: CardRef | None, view: PlayerView, seat: int, *, wine: bool | None = None
) -> bool:
    """这一下落到 ``seat`` 身上是不是**当场打死**（``_damage_to >= hp + armor``）。

    这是**唯一的斩杀判据**（D2，票 19）：:func:`_score_attack` 的斩杀项问"现在就够不够"，
    :func:`_wine_enables_kill` 问"加【酒】才够、现在不够"——两条都读这里，不各写一份
    ``_damage_to`` 比较（改伤害模型时只有一处会漂）。``wine`` 语义同 :func:`_damage_to`：
    ``None`` 读当前状态，``True`` 推演"这一口已经喝下"。

    只读公开面：目标体力 / **护甲**（两条都在投影里）与 :func:`_damage_to`（藤甲 / 白银狮子 /
    【酒】/ 古锭刀都在它里面）。**不**读目标手牌牌面：这一刀会不会被【闪】抵消是概率问题，
    归 C1（记牌那票），本票的"斩杀"只回答"实际伤害够不够"。
    """
    target = view.players[seat]
    # 护甲是**第二条体力轴**（yijiang-2014-round2 票 07 / W7）：这一刀要先把护甲打光才轮到
    # 体力，"还能扛几下"因此是 ``hp + armor``。**只**进这条斩杀判据——「已受伤」
    # （:func:`_wounded`）仍只看 ``hp < hp_max``（护甲不让任何人看起来受伤）。
    return _damage_to(card, view, seat, wine=wine) >= target.hp + target.armor


# ---------------------------------------------------------------------------
# 记牌 P(闪)（C1，票 20）：只读 view 的公开面 + 静态档位表
# ---------------------------------------------------------------------------


def _conserved_card_count(view: PlayerView) -> int:
    """整局实体牌的**守恒量**：牌堆 + 全部手牌数 + 弃牌堆 + 处理区 + 全部装备 / 判定区 +
    **全部武将牌上的牌**。

    每一项都在 :class:`~sgs.state.PlayerView` 的公开面里（牌堆与他家手牌只有张数，自己的
    手牌是牌面；武将牌上的牌按座位给公开张数 ``hero_zone_count``——票 27）。跨座位的区算
    宿主那一行，与投影口径一致。
    """
    total = view.deck_count + len(view.discard) + len(view.processing)
    for player in view.players:
        total += (
            player.hand_count
            + player.hero_zone_count
            + len(player.equip)
            + len(player.judge)
        )
    return total


def _visible_card_faces(view: PlayerView) -> list[CardRef]:
    """本视角里**看得到牌面**的牌：自己手牌 + **本人武将牌上的牌** + 弃牌堆 + 处理区 +
    全部装备 / 判定区。

    与 :func:`_visible_cards` 的区别：那张表是选牌步的"可指认集合"（不含两个共享区），
    这张表是记牌的"已出现的牌"（共享区恰恰是最主要的来源）。他人武将牌上的牌**不在**这里：
    投影只给张数（票 27 与 M6 的 2026-10 口径一致——牌面只给宿主）。
    """
    cards = [*view.self_hand, *view.hero_zone_cards, *view.discard, *view.processing]
    for player in view.players:
        cards.extend(player.equip)
        cards.extend(player.judge)
    return cards


def _deck_table_is_consistent() -> bool:
    """字面表自身是否自洽：每一档 ``sum(by_type) == total``。

    这是**运行期的 fail-closed 自检**（票 20 D2 的"档位表被改动 ⇒ 记牌关闭"）：改了逐项没
    改总数时，不必等测试就关闭记牌。**一致性漂移**（total 与逐项一起改）运行期看不出来——
    那条由 ``tests/test_heuristic_ai.py`` 的反向对齐测试兜（对准内容包的 `by_type`，漂移即红）。
    三档共 ≤ 90 个整数相加，放在每次询问的评估路径上也不值得缓存。
    """
    return all(
        sum(by_type.values()) == declared_total
        for declared_total, by_type in _DECK_TABLE.values()
    )


def _verified_deck_profile(view: PlayerView) -> tuple[int, Mapping[str, int]] | None:
    """按守恒量反推牌堆档位，返回 ``(总张数, 逐项张数)``；**验不过就答 None**（关闭记牌，不猜）。

    档位**名字**故意不返回：没有读取方（记牌要的是总张数与**整张逐牌名表**——C1 只用
    【闪】那一项，C2 起未知牌期望 / P(【无懈】) / P(【桃】) 都读同一份剩余张数），
    少一个字段就少一处漂移。

    三条判据（spec §6.3 的"不猜"）：

    - 字面表自身自洽（:func:`_deck_table_is_consistent`）；
    - 守恒量必须**恰好等于**某一档的总张数（三档互不相同）——人为少一张（例如构造局面
      少放一张实体牌）也对不上 ⇒ 关闭；票 27 起武将牌上的牌按张数补回守恒量，**不再**因为
      【谦逊】放置而误关；
    - 可见牌面的**同类张数不得超过**该档声明（构造局面 / 串档的机械保护）。
    """
    if not _deck_table_is_consistent():
        return None
    count = _conserved_card_count(view)
    for declared_total, by_type in _DECK_TABLE.values():
        if declared_total != count:
            continue
        seen: dict[str, int] = {}
        for card in _visible_card_faces(view):
            seen[card.type] = seen.get(card.type, 0) + 1
            if seen[card.type] > by_type.get(card.type, 0):
                return None
        return declared_total, by_type
    return None


def _unknown_pool(view: PlayerView) -> tuple[int, Mapping[str, int]] | None:
    """未知池的 ``(张数, 逐牌名剩余张数)``：档位总牌面 − 可见牌面；记牌关闭时答 ``None``。

    未知池 = 牌堆 + 他家手牌 + **他人武将牌上不可见的牌**（它们移出了可数区域、但张数在
    守恒量里；本人武将牌上的牌面已经算进可见面，不会被重复计入）。记牌关闭时（档位 /
    守恒对不上）返回 ``None``，调用方回落到常量贴现。

    C1（票 20）只取【闪】一项；C2（票 23）按**整张逐牌名表**读它：未知牌的期望牌价、
    【无懈可击】与【桃】的概率都从这份剩余张数里算（不再各自从档位表重扣一遍）。
    """
    profile = _verified_deck_profile(view)
    if profile is None:
        return None
    total, by_type = profile
    visible = _visible_card_faces(view)
    remaining = dict(by_type)
    for card in visible:
        left = remaining.get(card.type, 0) - 1
        if left < 0:
            return None
        remaining[card.type] = left
    unknown = total - len(visible)
    if unknown < 0:
        return None
    return unknown, remaining


def _has_card_probability_in_pool(
    pool: tuple[int, Mapping[str, int]], hand: int, types: tuple[str, ...]
) -> float | None:
    """在一份**已知的未知池**里，``hand`` 张手牌至少有一张 ``types`` 里某牌的概率。

    未知池按**无放回**算超几何 ``1 − C(未知 − 该类, h) / C(未知, h)``（spec §6.3 的
    "4 张手牌约 47%"就是 4 张手牌在 standard_108 开局时抽到【闪】的量级）；``None`` =
    这份池子算不出（未知池比手牌还少）。

    **为什么单独一层**：C2 起"目标那一侧"要对**多个座位**各算一次（:func:`_side_holds_probability`），
    未知池只该算一次（``_unknown_pool`` 会走守恒量 + 可见面），故乘除这部分抽出来。
    """
    unknown, remaining = pool
    if hand <= 0:
        return 0.0
    if unknown < hand:
        return None
    matches = sum(remaining.get(type_id, 0) for type_id in types)
    if matches <= 0:
        return 0.0
    if matches >= unknown:
        return 1.0
    # C(未知 − 该类, h) / C(未知, h) = ∏ (未知 − 该类 − i) / (未知 − i)：整数分子分母各乘一次，
    # 最后一次除法——比逐项浮点乘更稳，也不引 math（可见面白名单里没有它）。
    safe = 1
    total = 1
    for index in range(hand):
        safe *= unknown - matches - index
        total *= unknown - index
    return 1.0 - safe / total


def _has_card_probability(
    view: PlayerView, seat: int, types: tuple[str, ...]
) -> float | None:
    """目标 ``seat`` **手里至少有一张** ``types`` 里某牌的概率；记牌关闭时答 ``None``。

    C1 的 P(闪)、C2 的 P(【桃】)、P(【无懈可击】) 共用这一台机器（乘除在
    :func:`_has_card_probability_in_pool`）。

    ``types`` 用**元组**而不是集合：这里要逐项求和，而集合的迭代序逐进程变化
    （``tests/test_source_discipline.py`` ③）；调用方按固定字面量给。

    **武将牌上的牌（票 27）**：本人武将牌上的牌面在 ``view.hero_zone_cards`` 里（已出现），
    每个座位的张数在 ``hero_zone_count`` 里（守恒量）；**他人**武将牌上的牌只给张数、牌面
    留在未知池里——与投影的宿主可见口径一致，不是全知读取。
    """
    if not 0 <= seat < len(view.players):
        return None
    # 先判"记牌能不能用"，再看张数：0 手目标的答案恒为 0，但"记牌关闭"必须仍然可见
    # （``None`` = 不知道 / 不可用），否则调用方分不清"没有这张牌"和"没在记牌"。
    pool = _unknown_pool(view)
    if pool is None:
        return None
    return _has_card_probability_in_pool(pool, view.players[seat].hand_count, types)


def _dodge_probability(view: PlayerView, seat: int) -> float | None:
    """目标 ``seat`` **手里至少有一张【闪】**的概率；记牌关闭时答 ``None``。

    机器在 :func:`_has_card_probability`（C2 起三条概率共用一份乘除）；本函数只是把
    "哪一类牌"钉成【闪】——C1 的伤害贴现读它。
    """
    return _has_card_probability(view, seat, _DODGE_TYPES)


def _defended_by_dodge(card: CardRef | None) -> bool:
    """这一张伤害牌是不是靠目标打出【闪】来抵消（【杀】族与【万箭齐发】）。"""
    if card is None:
        return False
    return card.type in _DODGE_DEFENDED_TYPES or _is_slash_card(card)


def _expected_attack_score(damage: int, dodge_probability: float) -> float:
    """这张伤害牌的**期望增量分**（不含 :data:`_DAMAGE_BASE` / 目标数 / 关系项）。

    ``_DAMAGE_POINT_VALUE × (damage × (1 − p) − 1) + _DODGE_CONSUMED_VALUE × p``

    - 第一项 = 期望伤害相对"1 点保底"的增量：p=0 时就是 ``_DAMAGE_POINT_VALUE × (damage − 1)``，
      与旧公式逐字节一致；p=1 时是 ``−_DAMAGE_POINT_VALUE × damage``（这一刀白打）。
    - 第二项 = "躲掉也消耗一张【闪】"的收益（分数单位，见 :data:`_DODGE_CONSUMED_VALUE`）。
      它让普通【杀】在 p 升高时只是**小幅**变差（1 点伤害：0 → −0.4），而不是被全价打折。
    """
    return (
        _DAMAGE_POINT_VALUE * (damage * (1.0 - dodge_probability) - 1.0)
        + _DODGE_CONSUMED_VALUE * dodge_probability
    )


# ---------------------------------------------------------------------------
# 记牌批 2（C2，票 23）：未知牌期望 + 桃 / 无懈的期望
#
# C1 只把【闪】的占比接进了伤害打分（"这一刀会不会被躲掉"）。C2 把同一台记牌机器铺到
# 另外两件**能把已经打出去的牌作废**的牌上——【桃】（濒死 / 救援）与【无懈可击】（锦囊
# 目标）——并把"未知牌一律按 1.0"的魔数换成未知池里一张牌的**期望牌价**。
#
# 四条判据同源：都只读 `_unknown_pool` 的剩余张数 + `_DECK_TABLE`（与 C1 一份档位表），
# 记牌关闭时一律 `None` ⇒ 回落到票 23 之前的行为（"关掉记牌 = 今天"这条仍然成立）。
# ---------------------------------------------------------------------------

_DODGE_TYPES = ("dodge",)
"""P(有【闪】) 的牌名（C1）。元组而不是集合：概率函数要逐项求和，集合迭代序逐进程变化。"""

_NULLIFY_TYPES = ("nullify",)
"""P(有【无懈可击】) 的牌名（C2：锦囊目标能不能整张取消）。"""

_RESCUE_OWN_TYPES = tuple(sorted(_RESCUE_CARD_TYPES))
"""濒死**自救**的牌名（【桃】/【酒】；C2）：元组而不是集合，内容仍只声明一份。"""

_RESCUE_OTHER_TYPES = ("peach",)
"""**别人**救你的牌名：只有【桃】（【酒】只能自救，规则上不能替人回复）。"""

_POOL_SLASH_TYPES = frozenset({"slash", "fire_slash", "thunder_slash"})
"""档位表里按【杀】族计价的牌名（:func:`_pool_card_value`；只作成员判定、不迭代）。

"火【杀】/ 雷【杀】与转化、虚拟【杀】同价"这条在**牌面**那一侧走 :func:`_is_slash_card`；
档位表的键是**牌名字符串**、没有 ``CardRef``，故这里按牌名登记一张小表。

**漂移由测试兜**：``tests/test_heuristic_ai.py::test_the_card_counting_tier_table_matches_the_content_pack``
要求 ``_DECK_TABLE`` 的**每一个**牌名都落在三张字面表之一（本表 / :data:`_CARD_VALUES` /
:data:`_POOL_EQUIPMENT_SLOTS`）——新加第五条【杀】而没登记，它会红（不是运行期静默按兜底价）。
"""

_POOL_EQUIPMENT_SLOTS: Mapping[str, str] = MappingProxyType(
    {
        "ancient_scimitar": "weapon",
        "axe": "weapon",
        "crossbow": "weapon",
        "double_sword": "weapon",
        "green_dragon_blade": "weapon",
        "halberd": "weapon",
        "ice_sword": "weapon",
        "qilin_bow": "weapon",
        "qinggang_sword": "weapon",
        "serpent_spear": "weapon",
        "vermilion_feather_fan": "weapon",
        "eight_diagrams": "armor",
        "renwang_shield": "armor",
        "silver_lion": "armor",
        "vine_armor": "armor",
        "dilu": "horse_defense",
        "hualiu": "horse_defense",
        "jueying": "horse_defense",
        "zhuahuang_feidian": "horse_defense",
        "chitu": "horse_offense",
        "dawan": "horse_offense",
        "zixing": "horse_offense",
    }
)
"""档位表里**装备牌名 → 槽位**的字面表（C2 的未知牌期望按槽位价计，不是一律一件价）。

档位表逐**牌名**记数、装备没有 ``equip`` 这一类（``crossbow`` / ``eight_diagrams`` /
``jueying`` …），故要按 :data:`_SLOT_VALUES` 计价就得知道每个牌名是哪个槽——AI 模块不能
import ``sgs.data``，这张表只能手抄。**漂移由测试兜**：反向对齐测试逐条核「内容包里这个牌名
的 ``subcategory`` 就是这里写的槽位」，改内容包 / 漏登记即红。
"""


def _pool_card_value(type_id: str) -> float:
    """档位表的一个**牌名**值多少分：牌名表 → 【杀】族 → 装备槽位 → 未登记的锦囊兜底。

    与 :func:`_card_value` 同一套价（装备按槽位、未登记的锦囊按一张普通牌），只是入口是
    **牌名字符串**（档位表的键）而不是 ``CardRef``——两处读到同一个概念时价必须一致，
    否则"弃牌时值多少"与"期望里值多少"会各说各话。
    """
    known = _CARD_VALUES.get(type_id)
    if known is not None:
        return known
    if type_id in _POOL_SLASH_TYPES:
        return _CARD_VALUES["slash"]
    slot = _POOL_EQUIPMENT_SLOTS.get(type_id)
    if slot is not None:
        return _SLOT_VALUES.get(slot, _EQUIP_VALUE)
    return _UNREGISTERED_TRICK_VALUE


def _unknown_card_expectation(view: PlayerView) -> float | None:
    """未知池里**一张牌的期望牌价**：``Σ 剩余张数 × 牌价 ÷ 剩余总张数``；记牌关闭 ⇒ ``None``。

    期望牌价随牌局变化：牌堆里【桃】/【杀】被看见得越多、剩下的多为装备时，一张未知牌就
    不值钱（票面"把未知牌一律 1.0 换成未知池期望"）。

    **退化必须干净**（票面验收）：未知池**为空**（全部牌面都看得见）⇒ ``0.0``——没有牌可抽，
    期望就是 0，**不是** :data:`_HIDDEN_CARD_VALUE` 那个 1.0 的魔数。记牌关闭（档位 / 守恒
    对不上）⇒ ``None``，调用方走兜底（票 23 之前的行为）。
    """
    pool = _unknown_pool(view)
    if pool is None:
        return None
    unknown, remaining = pool
    if unknown <= 0:
        return 0.0
    total = sum(
        count * _pool_card_value(type_id) for type_id, count in remaining.items()
    )
    return total / unknown


def _unknown_card_value(view: PlayerView) -> float:
    """一张**看不见牌面的牌**值多少分：记牌可用 ⇒ 未知池期望；关闭 ⇒ :data:`_HIDDEN_CARD_VALUE`。

    票 23 之前这里恒是 1.0。现在只有"记牌关闭"才走那个兜底——与 C1 的"对不上就关闭记牌、
    回到今天的行为"同一条口径（``None`` = 不猜，不是 0）。
    """
    expectation = _unknown_card_expectation(view)
    return _HIDDEN_CARD_VALUE if expectation is None else expectation


_NULLIFY_CONSUMED_VALUE = 1.0
"""锦囊被【无懈可击】取消时对方的代价，用**分数**计（≈ 一张普通牌的价值）。

与 :data:`_DODGE_CONSUMED_VALUE` 同一姿态、**故意各留一个字面量**：那条是"这一刀被躲掉也
消耗一张【闪】"，这条是"这张锦囊被取消也消耗一张【无懈】"；把一条绑到另一条上，调其中
一条的价就会偷偷改掉另一条（与 :data:`_ILLEGAL_PENALTY` / :data:`_ABORT_SKILL_PENALTY`
同一条处置：同值不同义）。
"""


def _expected_with_consumed(value: float, probability: float, consumed: float) -> float:
    """``value × (1 − p) + consumed × p``：一次"可能被作废"的落点的期望分（C2 的共用算式）。

    :func:`_expected_trick_score`（锦囊被无懈取消）与 :func:`_expected_lethal_score`（斩杀被
    桃救回）共用这一行；:func:`_expected_attack_score` **不**共用——那条按"伤害点"计价、
    正负项的形状不同，硬套会把两把尺子混成一把。
    """
    return value * (1.0 - probability) + consumed * probability


def _expected_trick_score(
    value: float, nullify_probability: float | None
) -> float:
    """锦囊的**期望分**：``value × (1 − p) + 一张牌的价 × p``（C2）。

    - ``p`` 为 ``None``（记牌关闭）⇒ 逐值返回 ``value``（票 23 之前的行为）；
    - ``p = 0`` ⇒ 也逐值返回（未知池里没有【无懈】可宣）；
    - ``p = 1`` ⇒ 只剩"对方消耗一张【无懈】"的价——与 :func:`_expected_attack_score` 的
      收益项同一思路；否则"必然被取消"的锦囊与"白扔"同价，AI 分不出好坏。

    **``value <= 0`` 一律不打折**（评审自纠）：补偿项说的是"这张打出去的牌**即使被取消**
    也让对方付出了一张牌"，前提是这一手本来**值得打**。给零收益的【铁索连环】加上一个正的
    补偿价，等于教 AI 为了钓一张【无懈】白打一张牌（还会压过 0.6 的重铸）；负收益的更不该
    被抬高。
    """
    if nullify_probability is None or value <= 0.0:
        return value
    return _expected_with_consumed(value, nullify_probability, _NULLIFY_CONSUMED_VALUE)


_NULLIFIABLE_TRICKS = (
    "dismantlement",
    "snatch",
    "duel",
    "borrowed_sword",
    "fire_attack",
    "iron_chain",
)
"""**单目标、当回合结算**的锦囊：目标手里的【无懈可击】能把整张取消（C2 的登记表）。

两条"不打折"的边界（登记在票 23 的 Answer）：

- **多目标锦囊**（【南蛮入侵】/【万箭齐发】/【五谷丰登】/【桃园结义】）：取消只覆盖**一个**
  目标，整张按一个目标的概率打折会系统性低估；
- **延时锦囊**（【乐不思蜀】/【兵粮寸断】/【闪电】）：它**之后**才结算，取消概率不再由
  "目标此刻的手牌"决定（是整桌的未知池）。

宁可少算（少一次打折）也不算错——这两类保持票 23 之前的常量分。
"""


def _trick_expectation(query: Query, action: Action, total: Score) -> float:
    """把"目标可能用【无懈可击】取消"折进这张锦囊的分（C2 的**唯一**落点）。

    只在 :func:`_score_card_use` 调用：``_score_attack`` 也被本模块别处直接调（测试 / 将来
    的单卡估值），锦囊的取消期望属于"出牌阶段用这张牌"这一步，不掺进纯伤害模型。
    """
    if not isinstance(action, UseCard) or action.card.type not in _NULLIFIABLE_TRICKS:
        return total.finish()
    view = query.view
    others = [
        seat
        for seat in action.targets
        if seat != view.seat and 0 <= seat < len(view.players)
    ]
    if len(others) != 1:
        return total.finish()
    probability = _nullification_probability(view, others[0])
    if probability is not None:
        # ``_expected_trick_score`` 自己含两条"逐值不变"边界（``p`` 缺失 / 总分 ≤ 0）；
        # 不改分时 ``transform`` 不记 0 增量项（见 :meth:`Score.transform`）。
        total.transform(
            "无懈可击的期望",
            lambda value: _expected_trick_score(value, probability),
        )
    return total.finish()


_RESCUE_CONSUMED_VALUE = 1.0
"""斩杀被【桃】救回来时对方的代价，用**分数**计（≈ 一张普通牌的价值）。

与 :data:`_DODGE_CONSUMED_VALUE` / :data:`_NULLIFY_CONSUMED_VALUE` 同一条思路、各留一个字面量：
"这一下没打死"也不是白打——对方的【桃】被消耗掉了；但只抵掉一部分，不把斩杀价抹平。
"""


def _side_willingness(view: PlayerView, target: int) -> tuple[float, ...]:
    """逐座位"**替 ``target`` 出头**"的权重（0..1）：``target`` 自己恒 1，其余按是否与它同阵营。

    第三方之间的阵营不是公开事实，只能用我对这两席的关系权重推：``target`` 是**我的敌人** ⇒
    只有"我的敌人"才是它的盟友（``P(敌)``）；反之用 ``P(友)``（I1 的关系权重，票 21）。
    两边都不是（含配比算不出的中性 ``(0, 0)``）⇒ 0——中立不替任何人出头。

    落点是**桃**那一侧（谁愿意救这个濒死者）；**无懈**那一侧按目标本人收窄，不走这里
    （宽口径没过不劣化闸，见 :func:`_nullification_probability`）。
    """
    enemy_of_target, ally_of_target = _identity_relation(view, target)
    enemy_side = enemy_of_target > ally_of_target
    weights: list[float] = []
    for seat, player in enumerate(view.players):
        if seat == target:
            weights.append(1.0)
        elif seat == view.seat or not player.alive:
            # **我自己不算**：决策是我在做——我不会宣无懈取消自己打出的锦囊，
            # 也还没轮到（或已经错过）用我的【桃】去救那个濒死者。
            weights.append(0.0)
        else:
            enemy, ally = _identity_relation(view, seat)
            weights.append(enemy if enemy_side else ally)
    return tuple(weights)


def _side_holds_probability(
    view: PlayerView,
    target: int,
    own_types: tuple[str, ...],
    other_types: tuple[str, ...],
) -> float | None:
    """``target`` 那一侧**至少有一张**该牌的概率；记牌关闭 / 非法座位 ⇒ ``None``。

    :func:`_side_willingness` 逐座位加权后按 ``1 − Π (1 − p_s × w_s)`` 合并：
    ``target`` 自己用 ``own_types``（自救 / 无懈），别人用 ``other_types``（替它出手的牌）。
    未知池只算一次（:func:`_has_card_probability_in_pool`），不逐座位重跑去守恒量。

    **近似（登记在票 23 的 Answer）**：逐座位独立按超几何估，而同一张牌不可能落在两处——
    严格口径要多元超几何。独立相乘会**略微高估**"多处出手"，对出手的一方保守。
    """
    if not 0 <= target < len(view.players) or not view.players[target].alive:
        return None
    pool = _unknown_pool(view)
    if pool is None:
        return None
    missed = 1.0
    for seat, weight in enumerate(_side_willingness(view, target)):
        if weight <= 0.0:
            continue
        types = own_types if seat == target else other_types
        probability = _has_card_probability_in_pool(
            pool, view.players[seat].hand_count, types
        )
        if probability is None:
            return None
        missed *= 1.0 - probability * weight
    return 1.0 - missed


def _rescue_probability(view: PlayerView, seat: int) -> float | None:
    """``seat`` 被打进濒死后**被救回来**的概率（C2 的"桃期望"）；记牌关闭 / 非法座位 ⇒ ``None``。

    两个来源（都只读公开面 + :data:`_DECK_TABLE`）：

    1. **他自己手里的【桃】/【酒】**（濒死自救：酒只对自己生效）；
    2. **他那一侧的其他人手里的【桃】× 救援意愿**（意愿见 :func:`_side_willingness`）。

    **一局只算一次**：不建模"救回来又被打死"的连锁（登记在票 23 的 Answer）。
    """
    return _side_holds_probability(view, seat, _RESCUE_OWN_TYPES, _RESCUE_OTHER_TYPES)


def _nullification_probability(view: PlayerView, seat: int) -> float | None:
    """这张锦囊在 ``seat`` 身上**被宣【无懈可击】取消**的概率；记牌关闭 ⇒ ``None``。

    **收窄到目标本人（有实测依据，不是漏做）**：只数 ``seat`` **自己**手里的【无懈】，不数
    它那一侧的盟友。宽口径（盟友 × 关系权重，与 :func:`_rescue_probability` 同一台机器）在
    本票的第二轮曾整版落地，``promote`` 768 副**没过不劣化闸**（对父版本 ``-0.0143``、
    CI ``[-0.0456, 0.0143]``、CI 下界低于 δ=0.0319）⇒ 按 spec §6.7 的停手线回退到收窄档。
    语义上的收窄也站得住：无懈是**留住的一张牌**，盟友替一个单目标锦囊宣它的意愿本来就低，
    "手里有无懈 ⇒ 一定宣"只在**目标自己**身上近似成立。逐条读数见台账 `ai-v7` 口径说明。
    """
    return _has_card_probability(view, seat, _NULLIFY_TYPES)


def _expected_lethal_score(view: PlayerView, seat: int) -> float:
    """斩杀分的**期望**：``_LETHAL_STRIKE_VALUE × (1 − p) + 一张牌的价 × p``（C2）。

    ``p`` = :func:`_rescue_probability`；记牌关闭（``p = None``）⇒ 逐值返回
    :data:`_LETHAL_STRIKE_VALUE`（票 23 之前的行为）。
    """
    probability = _rescue_probability(view, seat)
    if probability is None:
        return _LETHAL_STRIKE_VALUE
    return _expected_with_consumed(
        _LETHAL_STRIKE_VALUE, probability, _RESCUE_CONSUMED_VALUE
    )


# ---------------------------------------------------------------------------
# 分项打分（`ai-view` 票 02）：``Score`` 累加器
#
# 改写前每个 ``_score_*`` 把各项内联成 ``score += …``，总分是一个 float——"这一步加到了
# 什么"只能回去读代码。``Score`` 把"算分"与"记分"收进同一份代码：**默认只累加**
# （与内联加法逐浮点相同），**开 trace 才记明细**（``((项名, 值), …)``），供回放台
# （票 03）直接展示。工具侧不复算分项——复算就是第二份真源，必然漂。
# ---------------------------------------------------------------------------


class Scored(float):
    """一次打分的**结果**：float 值 + trace 打开时记下的分项（`ai-view` 票 02）。

    ``float`` 子类 ⇒ 既有的比较 / 四则 / ``pytest.approx`` 全部照旧，``_score_*`` 的
    "返回 float"契约不动。:meth:`Score.add` 认出它时**接住分项**（而不是再记一行聚合项），
    于是嵌套打分器的明细在外层保留下来——``_score_play_phase → _score_card_use →
    _score_attack`` 这条主路径上，玩家看到的仍是"伤害牌底价 / 目标数 / 期望伤害 / 敌我
    关系"，不是一行"伤害牌 3.75"。
    """

    items: tuple[tuple[str, float], ...]

    def __new__(
        cls, value: float, items: tuple[tuple[str, float], ...]
    ) -> "Scored":
        result = super().__new__(cls, value)
        result.items = items
        return result


class Score:
    """候选分数的累加器：分项与总分由**同一份代码**产出（`ai-view` 票 02 / spec §2.1）。

    **默认只累加**：:meth:`add` 就是一次 float 加法，与改写前内联的 ``score += …`` 逐
    浮点相同，且不分配明细。**开启 trace**（:meth:`tracing`）后 :meth:`finish` 返回
    :class:`Scored`（仍是 float），它的 ``items`` 就是这一层的分项。票 03 的用法：

    ``with Score.tracing(): value = scorer(query, action); value.items``

    分项是**扁平**的：被调用的打分器返回 :class:`Scored` 时，它的分项被接进外层，故
    "分项之和 == 返回值"在每一层都成立（``transform`` 的增量项允许 1 ulp 误差）。不把
    树形结构塞进 Score：票 03 要的是"这一步为什么这么打"的加法明细，不是另一套嵌套模型。

    项名是**人话短语**（"伤害牌底价" / "斩杀"），不是变量名；一个打分器的项名集合是它的
    解释契约——**改名 = 历史解释不可比**，只在改解释口径的票里动。

    单线程：AI 决策是纯函数、引擎不并发，故 trace 用一个类级开关而不是 contextvar / 锁。
    """

    _tracing: bool = False

    def __init__(self) -> None:
        self._total = 0.0
        self._items: list[tuple[str, float]] | None = (
            [] if Score._tracing else None
        )

    def add(self, name: str, value: float) -> None:
        """累加一项：``total += value``；trace 打开时记 ``(项名, 值)``。

        ``value`` 若是另一个打分器刚返回的 :class:`Scored`，**接住它的分项**而不是再记一
        行聚合项——这一项就是它那些分项的和，分项之和与总分因此不会差两份。
        """
        self._total += value
        if self._items is None:
            return
        if isinstance(value, Scored):
            self._items.extend(value.items)
        else:
            self._items.append((name, value))

    def transform(self, name: str, change: Callable[[float], float]) -> None:
        """对**已累加的总分**做一次变换（"被取消 / 被救回的期望"），记成一项**增量**分。

        增量而不是"改写后每项"：分项之和仍等于总分（浮点允许 1 ulp 误差），且变换只写在
        这一处（:func:`_trick_expectation` 是唯一使用者），不必把同一个算式抄进每一项。
        **没改分**（``_expected_trick_score`` 的逐值不变边界）就不记 0 增量项——但分支
        本身该有的 0 分项仍由 ``add`` 记，两者不是一回事。
        """
        before = self._total
        after = change(before)
        self._total = after
        if self._items is not None and after != before:
            self._items.append((name, after - before))

    def finish(self) -> float:
        """结束一层打分：trace 打开时返回带分项的 :class:`Scored`，否则就是 float。"""
        if self._items is None:
            return self._total
        return Scored(self._total, tuple(self._items))

    @classmethod
    @contextmanager
    def tracing(cls) -> Iterator[None]:
        """打开 trace：``with Score.tracing(): value = scorer(query, action)``。"""
        previous = cls._tracing
        cls._tracing = True
        try:
            yield
        finally:
            cls._tracing = previous


def _item_score(name: str, value: float) -> float:
    """**单分项**分数的快捷写法（常量分支 / 委托分支用）。

    与多分项打分器同一条 trace 契约：分支里也得有一个项名，否则票 03 只能对一条分支显示
    空白解释。
    """
    total = Score()
    total.add(name, value)
    return total.finish()


def _net_score(gain_name: str, gain: float, cost: float) -> float:
    """**一收一付**两项的分数：``gain − cost``（"收益 − 牌价"的共用写法）。

    技能打分器里重复出现的形状（回复 − 牌价 / 换牌收益 − 牌价 / 反间收益 − 牌价…）。项名
    统一：收益用本技能的短语，代价一律叫 :data:`_PRICE_ITEM`（"牌价"是同一个概念：一张牌
    离开我的手）。
    """
    total = Score()
    total.add(gain_name, gain)
    total.add(_PRICE_ITEM, -cost)
    return total.finish()


_PRICE_ITEM = "牌价"
"""牌类代价的**唯一**项名（弃 / 给 / 付 出去的都是同一个概念：一张牌的价）。"""


# ---------------------------------------------------------------------------
# 每类询问一个 scorer：``(query, action) -> float``
# ---------------------------------------------------------------------------


def _score_attack(action: Action, view: PlayerView) -> float:
    """伤害期望：目标非己方**基本为正**（确认盟友会被关系项压到过牌之下）；每一点
    **预期**伤害与"躲掉也消耗一张牌"各自计价。

    多目标（【万箭】/【南蛮】/方天三杀）逐目标累加，关系项也逐目标累加 ⇒ "敌方收益 −
    盟友损失"的期望差分天然成立，不需要第二套 AoE 判据（`ai-view` 票 01）。

    ``targets`` 只出现在 :class:`~sgs.state.UseCard` 上；``PlayCard``（打出）无目标，
    故按"无目标"读——打出伤害牌在出牌阶段本就不产生伤害。

    目标穿戴（公开信息）会修正实际伤害（:func:`_damage_to`）：普通【杀】打藤甲 = 0 ⇒
    扣 :data:`_WASTED_ATTACK_PENALTY`；火【杀】打藤甲 = 2 ⇒ 每多一点加
    :data:`_DAMAGE_POINT_VALUE`。于是"火杀 vs 藤甲选火杀"与"普通杀避开藤甲"都不需要
    专门的牌名分支，只读同一张公开装备表。

    **斩杀项（D2，票 19）**：目标**这一下就死**（判据见 :func:`_lethal_to`）时再加
    :data:`_LETHAL_STRIKE_VALUE`——残局里"一刀能斩的 1 体力目标"因此压过"更健康的已明
    敌人"，而伤害不够时一分不加（藤甲把这一刀挡成 0 ⇒ 照样是浪费）。

    **记牌 P(闪)（C1，票 20）**：**靠【闪】抵消**的伤害牌（【杀】族 / 【万箭齐发】）且
    记牌可用时，把"每张手牌 0.25 的常量贴现"换成
    :func:`_expected_attack_score`——未知池里【闪】的占比导出目标有闪的概率，期望伤害
    （负项）与"躲掉也消耗一张牌"（正项）一起算。记牌关闭（档位 / 守恒对不上）或不靠
    【闪】抵消的伤害牌（【决斗】/【南蛮】要求【杀】）**仍走常量贴现**，与票 20 之前一致。
    伤害为 0 的无效目标不进概率模型：那本来就不该出。

    **记牌桃期望（C2，票 23）**：目标**这一下就死**时，斩杀项不再是满价——按
    :func:`_expected_lethal_score` 折成期望（濒死者自己 / 同阵营座位的【桃】可能把他救回来；
    救回来也消耗对方一张【桃】）。记牌关闭 ⇒ 逐值回到 :data:`_LETHAL_STRIKE_VALUE`。
    """
    targets = tuple(action.targets) if isinstance(action, UseCard) else ()
    total = Score()
    if not targets:
        total.add("无目标", _DAMAGE_BASE)
        return total.finish()
    me = view.seat
    others = [
        seat
        for seat in targets
        if seat != me and 0 <= seat < len(view.players) and view.players[seat].alive
    ]
    if not others:
        total.add("只指向自己", -_SAVE_VALUE)
        return total.finish()
    card = getattr(action, "card", None)
    total.add("伤害牌底价", _DAMAGE_BASE)
    total.add("目标数", _TARGET_BONUS * len(others))
    for seat in others:
        hand_defense = _HAND_DEFENSE_WEIGHT * min(
            view.players[seat].hand_count, _HAND_DEFENSE_CAP
        )
        damage = _damage_to(card, view, seat)
        if damage <= 0:
            total.add("打不动（被防具挡下）", -_WASTED_ATTACK_PENALTY)
            total.add("目标手牌防御", -hand_defense)
        else:
            dodge_probability = (
                _dodge_probability(view, seat) if _defended_by_dodge(card) else None
            )
            if dodge_probability is None:
                total.add("伤害点数", _DAMAGE_POINT_VALUE * (damage - 1))
                total.add("目标手牌防御", -hand_defense)
            else:
                total.add(
                    "期望伤害",
                    _expected_attack_score(damage, dodge_probability),
                )
        # 关系项（`ai-view` 票 01）：``_RELATION_FOCUS × (P敌 − P友)``。票 21 的"打盟友
        # 没有额外惩罚"正是实机 seed 7032 里忠臣回头打主公的根因：主公是唯一合法目标
        # 时这张【杀】拿满底价 4.0，打得到就打。"主公"那一格另有规则级 veto
        # （:func:`_lord_veto_applies`），因为负分压不住斩杀项。
        total.add("敌我关系", _relation_term(view, seat))
        if _lethal_to(card, view, seat):
            total.add("斩杀", _expected_lethal_score(view, seat))
    return total.finish()


def _slash_target_pairs(query: Query) -> list[tuple[UseCard, int]]:
    """候选面里 **【杀】族 × 合法目标** 的成对清单（自己 / 越界 / 已阵亡都跳过）。

    【酒】的"加这一口正好斩杀"（:func:`_wine_enables_kill`）与 D4 的"先装这一手才斩得掉"
    （:func:`_equip_setup_score`）问的是**同一批**候选与目标，故扫描只留这一份：两边各抄
    一遍的话，一边补了"存活 / 不可达"之类的过滤，另一边会悄悄漂开（评审 Standards 轴
    2026-10-31 指出的 Duplicated Code）。**只产出真实候选**，不替引擎补候选面；候选面本来
    就只是几张牌，收成 list 比再引一个迭代器类型便宜。
    """
    view = query.view
    pairs: list[tuple[UseCard, int]] = []
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or not _is_slash_card(action.card):
            continue
        for seat in action.targets:
            if seat == view.seat or not 0 <= seat < len(view.players):
                continue
            if not view.players[seat].alive:
                continue
            pairs.append((action, seat))
    return pairs


def _wine_enables_kill(query: Query) -> bool:
    """【酒】用下去**真的能造成击杀**吗：某个合法【杀】目标正好差这 1 点。

    只读公开面：目标体力、目标装备（藤甲 / 白银狮子 / 古锭刀）、自己的 ``wine_active``
    与**手里的【杀】候选**（候选由引擎从真局面生成）。判据是"这一刀现在打不死、加【酒】
    正好打死"——两条都走 :func:`_lethal_to` 同一份斩杀判据（D2 的斩杀项也读它，故"酒是
    斩杀"与"这一刀是斩杀"不会各说各话）：目标现在不是斩杀、且 ``wine=True`` 的推演是斩杀。

    白银狮子的封顶把【酒】的 +1 也封在 1 点里（除非我装备青釭剑越过防具），故那种局面下
    ``_lethal_to(wine=True)`` 与现在同值 ⇒ 不是斩杀。已经能一刀打死时【酒】是多余的——
    那条不算"能击杀"，故 AI 不会为补刀白白扔掉【酒】。
    """
    view = query.view
    for action, seat in _slash_target_pairs(query):
        if _lord_is_vetoed_target(view, seat):
            # 主公 veto 是**规则**：这一刀到时候会被摘掉 ⇒ 为它喝酒是白扔（`ai-view` 票 01）。
            continue
        # ``_lethal_to`` 已经吸收了两条旧边界：伤害 0（普通【杀】打藤甲）时加酒也无效、
        # 白银狮子把 +1 封掉时"加酒正好打死"不成立——两者都落在"现在与酒后同为不斩杀"。
        if not _lethal_to(action.card, view, seat) and _lethal_to(
            action.card, view, seat, wine=True
        ):
            return True
    return False


def _score_wine(query: Query, view: PlayerView) -> float:
    """【酒】的出牌阶段用法：只在"下一张【杀】+1 正好斩杀"时值得，否则白扔。

    两种用法共用一个每回合一次的计数键，故出牌阶段把【酒】喝掉就再也救不了自己；
    "杀不掉也先来一口"在 AI 的估值里是**负分**（低于结束阶段），一条也不浪费。
    """
    del view
    if _wine_enables_kill(query):
        return _item_score("补刀酒", _WINE_LETHAL_VALUE)
    return _item_score("白喝（这一口救不了自己）", _WINE_WASTE_PENALTY)


def _elemental_ready(view: PlayerView) -> bool:
    """我手里 / 装备上有没有**属性伤害**的引线（横置敌人才有传导的下一棒）。

    只读公开面：自己的手牌牌面（对自己是已知的）与装备区的【朱雀羽扇】（把普通【杀】
    变火【杀】）。【火攻】不算：它的伤害要再来一张同花色手牌才成立，链式"先串再烧"的
    估值不把两步赌在一张牌上。
    """
    me = _me(view)
    if _wears(me, _VERMILION_FAN):
        return True
    return any(card.type in _ELEMENTAL_SLASH_TYPES for card in view.self_hand)


def _score_iron_chain(action: Action, view: PlayerView) -> float:
    """【铁索连环】：每个目标**独立 toggle**，收益 = 各目标横置 / 重置的收益之和。

    每条分支按 :func:`_identity_relation` 的 ``P(敌)`` / ``P(友)`` **加权**（I1，票 21）：

    - 已在连环上的**自己 / 盟友** ⇒ 重置是好的（脱离传导名单）；
    - 已在连环上的**敌人** ⇒ 重置是亏的（替他拆引线）；
    - 未横置的**自己 / 盟友** ⇒ 纯亏（替对手接上引线）；
    - 未横置的**敌人** ⇒ 只有手里有属性引线时才值得（先串起来，下一刀火【杀】烧过去）。

    中性的座位（``(0, 0)``，如无身份局、配比算不出）一分不加——与 I1 之前一致。别退回
    ``if/elif`` 的两档写法：0/1 时结果虽然相同，但未知座位会被当成"无收益的中性"，那正是
    概率档要修的那一格（7 人局起，主公眼里的未知座位 P(敌) 已经明显大于 P(友)）。

    没有收益时分数为 0：低于「重铸」（:data:`_REFORGE_VALUE`）⇒ 走重铸而不是空放。
    """
    targets = tuple(action.targets) if isinstance(action, UseCard) else ()
    if not targets:
        return _item_score("无目标", 0.0)
    elemental = _elemental_ready(view)
    total = Score()
    for seat in targets:
        if not 0 <= seat < len(view.players):
            continue
        enemy, ally = _identity_relation(view, seat)
        if view.players[seat].chained:
            total.add("重置盟友（脱离传导）", _IRON_CHAIN_RESET_FRIENDLY_VALUE * ally)
            # 常量名是 ``..._PENALTY``（M6 P0 之前这一行写成了不存在的 ``..._VALUE``：该分支
            # 在启用池 16 名时没被随机对局走到，扩到 19 名后第一次命中就 NameError；回归
            # 测试见 `tests/test_m5_ai_military.py`）。
            total.add(
                "重置敌人（替他拆引线）", -_IRON_CHAIN_RESET_ENEMY_PENALTY * enemy
            )
        else:
            total.add(
                "串上盟友（替对手接引线）",
                -_IRON_CHAIN_EXPOSE_FRIENDLY_PENALTY * ally,
            )
            if elemental:
                total.add(
                    "串上敌人（下一棒烧过去）",
                    _IRON_CHAIN_CHAIN_ENEMY_VALUE * enemy,
                )
    return total.finish()


def _score_fire_attack(action: Action, view: PlayerView) -> float:
    """【火攻】：目标有手牌 + 自己**还有别的牌**可弃才可能造成伤害，否则不出。

    同花色弃牌是可选步（目标先亮一张，AI 再决定弃不弃），故"值不值"在弃牌那一步
    （:data:`_FIRE_ATTACK_DISCARD_VALUE`）再算一次；这里只回答"这一步值不值得开"：
    手里只剩这张火攻（没有同花色牌可弃）或只点自己 ⇒ 负分，宁可结束阶段。
    """
    targets = tuple(action.targets) if isinstance(action, UseCard) else ()
    me = view.seat
    others = [
        seat
        for seat in targets
        if seat != me and 0 <= seat < len(view.players) and view.players[seat].alive
    ]
    spare = len(view.self_hand) - 1
    if not others or spare <= 0:
        return _item_score("没有同花色牌可弃", _FIRE_ATTACK_NO_SPARE_PENALTY)
    total = Score()
    total.add("火攻底价", _FIRE_ATTACK_BASE)
    total.add(
        "多余的手牌",
        _FIRE_ATTACK_SPARE_WEIGHT * min(spare, _FIRE_ATTACK_SPARE_CAP),
    )
    for seat in others:
        if _wears(view.players[seat], _VINE_ARMOR):
            total.add("藤甲（2 点伤害）", _FIRE_ATTACK_VINE_BONUS)
        total.add("敌我关系", _relation_term(view, seat))
    return total.finish()


def _score_reforge(action: Action, view: PlayerView) -> float:
    """重铸：换一张牌（不是使用、不占次数、不触发使用锦囊类技能）——代价是**这张牌本身**。

    基线取【铁索连环】的牌价（:data:`_CARD_VALUES` 的 ``iron_chain``）：重铸这张牌换一张
    摸牌，净收益 = :data:`_REFORGE_VALUE` 减去"这张牌比【铁索连环】贵多少"。于是

    - 【铁索连环】自己恒得 :data:`_REFORGE_VALUE`（既有口径：没收益时重铸它，见
      :func:`_score_iron_chain` 的"没有收益时分数为 0"）；
    - 更贵的牌（【桃】/【闪】/【杀】/【无懈可击】）得**负分** ⇒ 不会为了重铸白扔好牌；
    - 更便宜的牌（【闪电】一类）仍按重铸的收益拿正分。

    huo-mobile-batch 票 02 之前恒 0.6——那时只有【铁索连环】自己可重铸（它本来就是
    "不想要才重铸"的牌），庞统【连环】把重铸能力扩到**任意♣手牌**之后，恒 0.6 会让 AI
    为 0.6 分扔掉梅花【桃】/【无懈可击】/【杀】。代价不夹下限：负分才能输给「什么都不做」。
    """
    assert isinstance(action, Reforge)
    baseline = _CARD_VALUES["iron_chain"]
    total = Score()
    total.add("重铸收益", _REFORGE_VALUE)
    total.add("这张牌的代价", baseline - _card_value(action.card, view=view))
    return total.finish()


# ---------------------------------------------------------------------------
# 装备先手（D4，票 25）：装上之后这一刀才"打得死" ⇒ 先装，再杀
# ---------------------------------------------------------------------------


def _equipment_after(me: PlayerPublic, card: CardRef) -> tuple[CardRef, ...]:
    """把我装上 ``card`` 之后的**装备区**写出来：同槽位替换，其余槽位原样。

    "换刀之后旧刀的效果不再生效"是规则事实，故这里不能把新旧两把武器叠加——古锭刀替下
    青釭剑之后，普通【杀】打【藤甲】重新变成 0 点（:func:`_damage_to` 只读装备区的牌面）。
    """
    return (*(equipped for equipped in me.equip if equipped.slot != card.slot), card)


def _view_wearing(view: PlayerView, card: CardRef) -> PlayerView:
    """"假如我把这张装备装上"的视角副本：只改自己的装备区，其余逐字段原样。

    这个副本的唯一用途是把"装上之后伤害是多少"交回 :func:`_damage_to`——**同一份**伤害
    模型（藤甲 / 白银狮子的封顶、青釭剑的无视防具、古锭刀的手牌数判据都在里面）。另抄
    一份"假如"算式就是第二处会漂的伤害模型，故这里只造局面、不算规则。
    """
    me = view.players[view.seat]
    players = list(view.players)
    players[view.seat] = me.model_copy(update={"equip": _equipment_after(me, card)})
    return view.model_copy(update={"players": tuple(players)})


def _keeps_reach(me: PlayerPublic, card: CardRef) -> bool:
    """换上 ``card`` 之后，**现在合法的【杀】目标会不会因为射程变短掉出候选面**。

    装上装备是**同槽位替换**：把【麒麟弓】(射程 5) 换成【古锭刀】(射程 2) 之后，距离 3 的
    目标不再是【杀】的合法目标——"先装后杀"在这一格根本不成立（装完反而够不着，白装一手）。
    只比两件装备的**牌面事实** ``attack_range``：新射程 ≥ 现射程 ⇒ 现在够得着的都还够得着；
    该槽位本来没装武器 ⇒ 只会变长或不变；这件装备本来就不带射程（防具 / 坐骑 / 宝物）
    ⇒ 不影响【杀】的合法性。

    这里**不预测**"具体掉哪些目标"：``PlayerView`` 里没有距离，自己算一遍就是把距离规则
    抄第二份。宁可少赚一手（射程可能变短时一律不追加分），不可白装一手。
    """
    if card.attack_range is None:
        return True
    worn = next((equipped for equipped in me.equip if equipped.slot == card.slot), None)
    if worn is None or worn.attack_range is None:
        return True
    return card.attack_range >= worn.attack_range


def _equip_setup_score(query: Query, card: CardRef) -> float:
    """装这件装备这一步的分（D4，票 25）：**只在"先装后杀能把打不死变成打死"时**抬高。

    窄面判据（票面原文）：装上 ``card`` 之后，**真实候选面**里某张【杀】对某个目标的实际
    伤害从"打不死"（:func:`_lethal_to` 假）变成"打得死"（真）⇒ 这一手必须排在**那条
    【杀】之前**。否则 AI 会先把那 1 点伤害打出去、把残局放跑——古锭刀（目标 0 手时 +1）
    与青釭剑（越过藤甲 / 白银狮子的封顶）就是这一格。

    **够得着才算**（评审 B2）：伤害够但**射程变短**时，那个目标装完就不再是合法目标，
    "先装后杀"整条链不成立 ⇒ 走 :func:`_keeps_reach` 一律不追加分。

    其余一切情形（含"装上也不改斩杀"、非武器 / 已经装着同一件 / 射程可能变短）
    **逐值不变**，仍是 :data:`_EQUIP_PLAY_VALUE`：本票不给装备的通用优先级加权重，
    也不做多步规划。

    比较用**那条【杀】现在的分**（同一询问、同一视角），故抬高的幅度只买"先后"这一格；
    装完之后那条【杀】的伤害变了，斩打分（:func:`_score_attack` 的斩杀项）下一步自然跟着变。
    """
    view = query.view
    if not _keeps_reach(view.players[view.seat], card):
        return _EQUIP_PLAY_VALUE
    after = _view_wearing(view, card)
    unlocked: float | None = None
    for action, seat in _slash_target_pairs(query):
        if _lord_is_vetoed_target(view, seat):
            continue  # 主公 veto（票 01）：这一刀到时候会被摘掉 ⇒ 不为它先装
        if _lethal_to(action.card, view, seat):
            continue
        if not _lethal_to(action.card, after, seat):
            continue
        score = _score_attack(action, view)
        if unlocked is None or score > unlocked:
            unlocked = score
    if unlocked is None:
        return _EQUIP_PLAY_VALUE
    return max(_EQUIP_PLAY_VALUE, unlocked + _EQUIP_SETUP_MARGIN)


def _score_card_use(query: Query, action: Action) -> float:
    """出牌阶段使用一张牌的局部收益（伤害 ＞ 装备 ＞ 过牌；【桃】只在受伤时用）。

    **记牌无懈期望（C2，票 23）**：单目标、当回合结算的锦囊（:data:`_NULLIFIABLE_TRICKS`）
    再乘一道"目标手里有【无懈可击】吗"的期望（:func:`_trick_expectation`）——被取消也不是
    白打，对方的【无懈】被消耗掉。记牌关闭 / 多目标 / 延时锦囊 ⇒ 逐值不变。

    **装备先手（D4，票 25）**：装备分不再恒为 :data:`_EQUIP_PLAY_VALUE`，先问
    :func:`_equip_setup_score`"装上之后**候选面里**那张【杀】是不是才打得死"；
    不是 ⇒ 逐值不变。**只看 ``legal_actions``**：转化技的【杀】产物住在
    ``transform_preactivations``（W4-fix-2 从候选面收回），本判据看不到它们。

    **单体有害锦囊按目标过关系项（`ai-view` 票 01）**：非伤害分支不再一律走
    :data:`_TRICK_VALUES` 的常量分——拆 / 顺 / 乐 / 兵粮 / 借刀这类牌多一个
    :func:`_harmful_trick_relation`（逐 ``targets + designations``）。伤害牌与【火攻】
    各自在 :func:`_score_attack` / :func:`_score_fire_attack` 里已经算过，不在这里重算。
    """
    assert isinstance(action, (UseCard, PlayCard))
    view = query.view
    card = action.card
    total = Score()
    if _damage_card(card):
        total.add("伤害牌", _score_attack(action, view))
    elif card.type == "wine":
        total.add("酒", _score_wine(query, view))
    elif card.type == "peach":
        total.add("回复", _HEAL_VALUE if _wounded(view) else 0.0)
    elif card.type == "iron_chain":
        total.add("铁索连环", _score_iron_chain(action, view))
    elif card.type == "fire_attack":
        total.add("火攻", _score_fire_attack(action, view))
    elif card.slot is not None:
        total.add("装备先手", _equip_setup_score(query, card))
    else:
        total.add("锦囊底价", _TRICK_VALUES.get(card.type, _UNREGISTERED_TRICK_VALUE))
        # 单体有害锦囊按**目标**过关系项（`ai-view` 票 01）：票面那条实机同样的缺口——
        # 忠臣手里有【过河拆桥】就拆主公。有益 / 自己 / 全体锦囊逐值不变。
        total.add("敌我关系", _harmful_trick_relation(action, view))
    return _trick_expectation(query, action, total)


def _score_play_phase(query: Query, action: Action) -> float:
    """``PLAY_PHASE``：能造成伤害且关系项为正 ＞ 装备 ＞ 过牌；技能查语义表。

    后半句的"关系项为正"是 `ai-view` 票 01 之后的口径：确认盟友的目标会被
    :func:`_relation_term` 压到过牌（0.0）之下，不再是"目标非己方恒正"。

    次序不是死的：装备在"先装后杀才斩得掉"时会被抬到那条【杀】之前（D4，票 25），
    见 :func:`_equip_setup_score`。

    **主公 veto（`ai-view` 票 01 / Q4 / 票 11）**作用在**挑目标**这一层、而不是某项价格里：
    主忠方、以及**还有别人活着时的内奸**（票 11），把主公当目标的有害候选直接拿
    :data:`_LORD_VETO_PENALTY`（见 :func:`_lord_veto_applies`）。写成"再大一点的负分"在
    斩杀那一格会被 :data:`_LETHAL_STRIKE_VALUE` 翻回去——忠臣照样一刀砍死 1 体力的主公，
    内奸同样（票 11 的实机：``4.0 + 6.5 − 1.6667 = +8.83``）。
    """
    view = query.view
    if isinstance(action, EndPhase):
        return _item_score("结束阶段", 0.0)
    if _lord_veto_applies(view, action):
        return _item_score("主公 veto（规则级不可选）", _LORD_VETO_PENALTY)
    if isinstance(action, ActivateSkill):
        return _skill_value(query, action)
    if isinstance(action, Reforge):
        return _score_reforge(action, view)
    if isinstance(action, (UseCard, PlayCard)):
        return _score_card_use(query, action)
    return _item_score("未知动作", 0.0)


def _score_dodge(query: Query, action: Action) -> float:
    """``DODGE``：能抵消就抵消（响应牌不占使用次数）。"""
    if isinstance(action, Pass):
        return _item_score("放弃", 0.0)
    if isinstance(action, ActivateSkill):
        return _item_score("技能抵消", _DODGE_SKILL_VALUE)
    if isinstance(action, (UseCard, PlayCard)) and action.card.type == "dodge":
        return _item_score("抵消", _DODGE_VALUE)
    return _item_score("不抵消", 0.0)


_NULLIFY = "nullify"
"""无懈窗口的响应牌名；也是**嵌套无懈**的判据（处理区顶牌是它 ⇒ 这层在"无懈无懈"）。"""

_NULLIFY_HELPFUL_TRICKS = frozenset({"ex_nihilo", "harvest", "peach_garden"})
"""被无懈取消时对**目标**是净收益的锦囊：过牌 / 拿牌 / 回复。

取消它们 = 从目标手里拿走一份收益，故只有目标是**已明敌人**时才值得宣。
"""

_NULLIFY_HARMFUL_TRICKS = frozenset(
    {
        "arrow_barrage",
        "barbarian_invasion",
        "borrowed_sword",
        "dismantlement",
        "duel",
        "fire_attack",
        "indulgence",
        "lightning",
        "snatch",
        "supply_shortage",
    }
)
"""被无懈取消时对**目标**是净损失的锦囊：伤害 / 拆顺 / 跳过阶段 / 判定区。

取消它们 = 替目标挡下损失，故只有目标是自己 / **已明盟友**时才值得宣；打敌人的那些
（例：乐不思蜀贴在主公头上）反而**不能**宣（"无懈的无效化目标是敌人 ⇒ 不宣"）。
【闪电】按"炸到判定者"的有害面归类：自己 / 盟友头上要拆，敌人头上让他自己扛。
"""

_NULLIFY_ABSTAIN_VALUE = -_RESPOND_VALUE
"""无懈窗口里**不该宣**时给无懈候选的分值：**严格低于** ``Pass``（0）。

不能给 0：那样"宣"与"不宣"平分，由 ``ai:<seat>`` 子流抽签决定（【遗计】收牌人的老问题），
"不该宣"的 D 用例只会在部分种子上红。严格为负 ⇒ 判据是确定性的，不靠运气。
"""


def _nullified_target_is_harmed(
    card: CardRef, view: PlayerView, target: int
) -> bool | None:
    """这张锦囊对 ``target`` 是**害**（``True``）/ **利**（``False``）/ **拿不准**（``None``）。

    - 未登记的牌名（新增锦囊 / 未知虚拟产物）⇒ ``None``：拿不准就不宣，别用"猜"代替规则；
    - 【铁索连环】的利 / 害按目标**当前是否横置**读：已横置 ⇒ 这一步是重置（解除传导，
      是利），未横置 ⇒ 串上引线（是害）。横置是公开信息，不是猜测。
    """
    if card.type in _NULLIFY_HARMFUL_TRICKS:
        return True
    if card.type in _NULLIFY_HELPFUL_TRICKS:
        return False
    if card.type == "iron_chain" and 0 <= target < len(view.players):
        return not view.players[target].chained
    return None


def _nullify_declares(query: Query) -> bool | None:
    """无懈窗口里**该不该宣**【无懈可击】；不是无懈窗口 ⇒ ``None``。

    判据（spec §6.1 D1）= **被取消的那张牌对目标有利 / 有害 × 目标与我的关系**：
    ``view.processing`` 顶牌是本次结算的牌（置入序，先入在前），``query.target`` 是这次
    结算针对的座位。

    | 牌对目标 | 宣的条件 | 为什么 |
    |---|---|---|
    | 利 | ``P(敌) > P(友)`` | 少给敌人一份；拆盟友的收益是亏 |
    | 害 | ``P(友) > P(敌)`` | 替朋友挡下；替敌人挡刀是亏 |

    - **嵌套无懈**（处理区顶牌是无懈）：不做链求解 ⇒ 不宣（D1 的范围，spec §6.1）；
    - 处理区为空 / ``target`` 缺失（协议上无懈窗口恒有目标，这里只作防御）：不宣；
    - 关系按 :func:`_identity_relation` 的 ``(P(敌), P(友))`` 加权（I1，票 21）：这是
      "利 / 害"两笔对称价下的期望值判据——宣与不宣的收益是一对相反数，故**比较两个概率**
      就够了；**平**（含算不出的 ``(0, 0)``）⇒ 不宣，与票 18 的中性口径一致。
    """
    if getattr(query, "cause", None) is not WindowCause.NULLIFY:
        return None
    view = query.view
    target = getattr(query, "target", None)
    if target is None or not view.processing:
        return False
    if view.processing[-1].type == _NULLIFY:
        return False
    harmed = _nullified_target_is_harmed(view.processing[-1], view, target)
    if harmed is None:
        return False
    enemy, ally = _identity_relation(view, target)
    if harmed:
        return ally > enemy
    return enemy > ally


def _luanwu_respond_value(query: Query, action: Action) -> float | None:
    """贾诩移动版【乱武】窗口的"用【杀】还是失体力"（lin-batch2 票 03，Q9 = B）。

    这扇窗口的两条出口**不对称**：选项 1 = 对距离最近的另一名角色使用一张【杀】（代价
    一张牌、收益看打的是谁），选项 2 = ``Pass`` = 失去 1 点体力（代价固定 1 点）。既有
    的"能抵消就抵消"常量分（:data:`_RESPOND_VALUE`）在这里会让 AI **稳定打最近的队友**
    （4 人环里最近的两人常有盟友）——用户 2026-11-08 签 Q9 = B：按**目标敌我**给分。

    判据只用两条既有事实，**不新增通道**：``action.targets``（真牌 / 转化牌直接带；提供者
    产物不带目标时回落到窗口里任一候选的目标——乱武的窗口只有**一个**固定目标）与
    :func:`_identity_relation` 的 ``(P(敌), P(友))``。敌 ⇒ ``+_RESPOND_VALUE``、友 ⇒
    ``-_RESPOND_VALUE``（**低于** ``Pass`` 的 0）、算不出 ⇒ 0（与 ``Pass`` 打平，由候选
    次序决定，不硬判）。

    不是乱武窗口 ⇒ ``None``（调用方走既有分支）。
    """
    if getattr(query, "cause", None) is not WindowCause.LUANWU:
        return None
    if isinstance(action, Pass):
        return 0.0
    if not isinstance(action, (ActivateSkill, UseCard, PlayCard)):
        return 0.0
    targets = tuple(getattr(action, "targets", None) or ())
    if not targets:
        # 提供者（【蛊惑】）的产物没有目标步：目标由窗口固定，读候选里的那一个。
        targets = tuple(
            int(target)
            for candidate in query.legal_actions
            for target in (getattr(candidate, "targets", None) or ())
        )
    if not targets:
        return _RESPOND_VALUE
    enemy, ally = _identity_relation(query.view, int(targets[0]))
    return _RESPOND_VALUE * (enemy - ally)


def _score_respond(query: Query, action: Action) -> float:
    """``RESPOND``：能抵消就抵消；技能类提供者（【护驾】/【激将】/【蛊惑】）同价。

    **无懈窗口是唯一的例外**（票 18 / spec §6.1 D1）：先按 :func:`_nullify_declares`
    判"该不该宣"；不该宣时给无懈候选 :data:`_NULLIFY_ABSTAIN_VALUE`（**严格低于**
    ``Pass``），由最大值直接选出 ``Pass``。其余窗口（【南蛮】/【万箭】/【决斗】/借刀 /
    势力求助 / 改判）仍是"有牌就抵消"的常量分——它们没有"取消对谁有利"的二义性。

    **乱武窗口是第二条例外**（lin-batch2 票 03，Q9 = B）：它的两条出口不是"抵消 / 不抵消"，
    而是"打最近的人 / 失去 1 点体力"⇒ 按目标敌我给分，见 :func:`_luanwu_respond_value`。

    **提供者也是响应牌**（票 03）：``ActivateSkill`` 在无懈窗口里同样过 :func:`_nullify_declares`
    ——【蛊惑】的产物是一张【无懈可击】，它的"该不该宣"与真牌是同一件事。不这么写的话，
    提供者恒拿 :data:`_DODGE_SKILL_VALUE`（正分）⇒ AI 会用它去宣一张**不该宣**的无懈，
    而换成真牌那条路它是会 ``Pass`` 的。
    """
    luanwu = _luanwu_respond_value(query, action)
    if luanwu is not None:
        return _item_score("乱武：按目标敌我给分", luanwu)
    if isinstance(action, Pass):
        return _item_score("放弃响应", 0.0)
    if isinstance(action, (ActivateSkill, UseCard, PlayCard, SubstituteJudgeCard)):
        if _nullify_declares(query) is False:
            return _item_score("无懈：这一宣对己方不利", _NULLIFY_ABSTAIN_VALUE)
        if isinstance(action, ActivateSkill):
            return _item_score("技能抵消", _DODGE_SKILL_VALUE)
        return _item_score("响应", _RESPOND_VALUE)
    return _item_score("无响应", 0.0)


def _peach_save_value(query: PeachQuery) -> float:
    """在求桃窗口里救 ``query.dying`` 的价值（真【桃】与急救的转化【桃】共用）。

    关系按 ``P(敌)`` **加权**（I1，票 21）：原来"已明敌人 ⇒ −6、其余 ⇒ +6"换成
    ``_SAVE_VALUE × (1 − 2·P(敌))``——三个质量各自计价（救盟友 +6、救敌人 −6、两边都不是
    +6）化简后就是这个式子，故 P(敌)=0 / 1 时与票 21 之前**同值**。5 人局主公眼里的未知
    座位 P(敌)=3/4 ⇒ −3：**大概率是敌人就不救**（仍不是硬判，见 spec §6.4）。
    """
    if query.dying == query.view.seat:
        return _SAVE_VALUE
    me = _me(query.view)
    if me.hp <= 1 and me.hand_count <= 1:
        return _CONSERVE_VALUE
    enemy, _ = _identity_relation(query.view, query.dying)
    return _SAVE_VALUE * (1.0 - 2.0 * enemy)


def _score_peach(query: Query, action: Action) -> float:
    """``PEACH``：救（桃用于救命几乎恒正收益），除非自己是濒死链的下一个。

    W4-fix-2：急救的转化【桃】不再是候选，AI 从清单填出的 ``ActivateSkill`` 与真【桃】
    按同一个救命价值打分——否则它会是 0 分、与「放弃」打平。

    M5-04：【酒】的濒死自救与【桃】同价（规则上都是"回复 1 点"）——它只在本座位濒死时
    出现在候选里，且**不产生**加伤，故与真【桃】共用 :func:`_peach_save_value`，
    不能因为牌名不是 ``peach`` 就 0 分、与「放弃」打平。
    """
    assert isinstance(query, PeachQuery)
    if isinstance(action, Pass):
        return _item_score("放弃救援", 0.0)
    if isinstance(action, ActivateSkill):
        return _item_score("救人", _peach_save_value(query))
    if not isinstance(action, (UseCard, PlayCard)) or action.card.type not in _RESCUE_CARD_TYPES:
        return _item_score("不是救援牌", 0.0)
    return _item_score("救人", _peach_save_value(query))


def _score_discard(query: Query, action: Action) -> float:
    """``DISCARD``：弃价值最低（【桃】/【闪】最后弃，装备按槽位价值排序）。"""
    del query
    if not isinstance(action, DiscardCards):
        return _item_score("非法弃牌", 0.0)
    total = Score()
    # 保留内置 ``sum``：CPython 3.12 起它对 float 走**补偿求和**，逐张 ``+=`` 会在
    # 末位差 1 ulp（票 02 的 parity 就是这样抓到的）——分项是一笔"弃牌成本之和"，
    # 不是一个假的逐张明细。
    total.add(_PRICE_ITEM, -sum(_card_value(card) for card in action.cards))
    return total.finish()


def _skill_value(query: Query, action: ActivateSkill) -> float:
    """技能收益查 :data:`SKILL_VALUES`；表里没有 ⇒ 保守（低于 Pass）。"""
    entry = SKILL_VALUES.get(action.skill_id)
    if entry is None:
        return _item_score("未登记技能（保守）", _UNKNOWN_SKILL_VALUE)
    scorer, _note = entry
    return scorer(query, action, query.view)


def _score_skill_activation(query: Query, action: Action) -> float:
    """``SKILL_ACTIVATION``：查技能语义表，缺省 ``Pass``（拿不准就不发动）。"""
    if isinstance(action, Pass) or not isinstance(action, ActivateSkill):
        return _item_score("放弃或不发动", 0.0)
    return _skill_value(query, action)


def _is_committed_skill_step(query: Query) -> bool:
    """这一步的「放弃」等于**撤销一次已经发动的技能**（而不是放弃一桩好事）。

    判据 = 出牌阶段空闲时机（``PHASE_PLAY_IDLE``）**且这一步可取消**。这个组合只由主动技
    发动后的步骤流水线产出（【离间】票 01）：玩家点了技能按钮就是"要发动"，中途放弃 =
    撤销；而引擎的"取消不消耗使用次数"意味着**技能会立刻重新可发动**。

    AI 若在这里挑「放弃」，就会"发动 → 放弃 → 又发动"地空转、永远走不完出牌阶段
    （32 种子基准组的 seed=107 就是这么自旋的）。故 AI 的纪律是：
    **既然选了发动，就得把成本付完**；要放弃就该在上一层（不点那个技能）放弃。
    """
    if getattr(query, "timing", None) != _PLAY_PHASE_IDLE:
        return False
    spec = getattr(query, "spec", None)
    if spec is not None:
        return bool(getattr(spec, "optional", False))
    # ``SELECT_OPTION`` 没有 ``spec``：此刻**候选里有 Pass** 本身就说明这一步可取消，
    # 而可取消的选项询问只有发动后流水线会发（取消 = 撤销一次已发动的技能）。
    return any(getattr(action, "kind", None) == "Pass" for action in query.legal_actions)


_GIVE_STEPS = frozenset({"rende"})
"""把**自己的牌交出去**的技能短 id（选牌步的成本语义与"弃一张换一张"完全不同）。

【仁德】给牌是纯成本（没有回抽），但给到 ≥2 张且自己受伤时换回 1 点体力
（:data:`_RENDE_RECOVER_VALUE`）。判据读询问自带的 ``skill_id``（票 05 加的本地通道，
不进记录流）——**不能只看 ``total`` 的 ``lo != hi``**：【制衡】与【仁德】都是
``(1, n)``，只看形状会把给牌误当成换牌（票 04 的 Answer 点名过这个坑）。
"""

_CYCLE_STEPS = frozenset({"zhiheng"})
"""每弃一张就回抽一张的技能短 id（【制衡】）：按 :data:`_CYCLE_VALUE` 逐张估值。"""


def _step_skill_short(query: Query) -> str:
    """这一步属于哪条技能（询问自带 ``skill_id`` 的短 id）；不是技能步 ⇒ 空串。"""
    raw = getattr(query, "skill_id", None)
    return str(raw).rsplit(".", 1)[-1] if raw else ""


def _step_card_score(
    card: CardRef | None, owner: int, me: int, short: str, *, view: PlayerView
) -> float:
    """一步选牌的**单张**分值（``_score_select_cards`` 与预发动填表共用的唯一口径）。

    - **别人的牌**（【麒麟弓】弃目标坐骑 / 【反间】挑发动者的牌）：取高价值 ⇒ ``+牌价``；
    - **自己的牌（成本）**：按技能语义取负——【制衡】每弃一张回抽一张，记
      ``_CYCLE_VALUE - 牌价``；其余（含【仁德】的给牌）记 ``-牌价``。

    ``view`` 只服务**没有牌面**的槽（别人的手牌）：C2（票 23）起它按未知池的期望牌价算；
    有牌面的槽逐值不受影响。

    两处读它（选牌步的打分、预发动填表的"选哪几张"），故两份模型不会漂移。
    """
    value = _card_value(card, view=view)
    if owner != me:
        return value
    if short in _CYCLE_STEPS:
        return _CYCLE_VALUE - value
    return -value


def _is_fire_attack_discard(query: SelectCardsQuery) -> bool:
    """这一步是不是【火攻】的**同花色弃牌**（可选弃一张，换 1 点火焰伤害）。

    判据只读公开面：询问可选（目标亮牌那一步**必选**一张）且处理区里躺着那张【火攻】
    （处理区是公开信息，火攻此刻正在结算）。不猜目标是谁——那 1 点伤害值多少按
    :data:`_FIRE_ATTACK_DISCARD_VALUE` 一刀切。
    """
    return query.spec.optional and any(
        card.type == "fire_attack" for card in query.view.processing
    )


_PINDIAN_SKILLS: frozenset[str] = frozenset(
    {"hero.taishici.tianyi", "hero.xunyu.quhu"}
)
"""拼点选牌的技能 id（huo-taishici-xunyu 票 01 / 02）：两张手牌比点数。

与普通选牌的**打分方向相反**：普通选自己的牌 = 付成本（越便宜越好），拼点要**赢**
（点数越大越好，平点 = 没赢）。`docs/agents/ai-adaptation.md` §1 把拼点列为"复用
SELECT_CARDS 会语义反转"的反例，这一条就是它要求的独立分支。
"""


def _is_pindian_pick(query: Query) -> bool:
    """这一步是不是"为拼点选一张手牌"。"""
    return isinstance(query, SelectCardsQuery) and (query.skill_id or "") in _PINDIAN_SKILLS


def _score_select_cards(query: Query, action: Action) -> float:
    """``SELECT_CARDS``：选**别人的**牌取高价值、选**自己的**牌（成本）取低价值。

    两种"任意张自己的牌"各有各的成本语义（按询问自带的技能 id 分派，不看 ``total`` 形状）：

    - 【制衡】:data:`_CYCLE_STEPS`：每弃一张回抽一张 ⇒ 逐张按 ``_CYCLE_VALUE - 牌价`` 估值；
    - 【仁德】:data:`_GIVE_STEPS`：给出去没有回抽，但给到 ≥2 张且自己受伤换回 1 点体力 ⇒
      按 ``-牌价`` 估值并给"≥2 张"补上 :data:`_RENDE_RECOVER_VALUE`。

    单张口径的唯一实现是 :func:`_step_card_score`——W4 评审处置 D.7 起，预发动填表的
    "选哪几张"也读它（不再另写一套）。

    M5-06 的例外是【火攻】的**可选同花色弃牌**（:func:`_is_fire_attack_discard`）：
    "弃一张换 1 点伤害"是一笔正收益，故按 :data:`_FIRE_ATTACK_DISCARD_VALUE - 牌价`
    估值——便宜的同花色牌该弃、贵的（【桃】/【闪】/【无懈】）宁可放弃（``Pass`` = 0）。
    目标**亮哪一张**仍是普通的"选自己的牌取低价值"（同一段代码的缺省分支）。
    """
    assert isinstance(query, SelectCardsQuery)
    if not isinstance(action, SelectCards):
        penalty = _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
        return _item_score("不选（可能是撤销已发动的技能）", penalty)
    committed = _is_committed_skill_step(query)
    short = _step_skill_short(query) if committed else ""
    pindian = _is_pindian_pick(query)
    fire_attack_discard = not committed and _is_fire_attack_discard(query)
    total = Score()
    if not action.picks:
        total.add("什么都不选", 0.0)
    for index in action.picks:
        if not 0 <= index < len(query.slots):
            return _item_score("非法选牌", _ILLEGAL_PENALTY)
        slot = query.slots[index]
        if pindian:
            # 拼点：按**点数**给分（A=1 … K=13），赢的那一侧收益远大于省一张贵牌。
            total.add("拼点点数", float(slot.card.rank or 0))
            continue
        if fire_attack_discard:
            total.add(
                "火攻弃牌换 1 点伤害",
                _FIRE_ATTACK_DISCARD_VALUE - _card_value(slot.card),
            )
            continue
        total.add(
            "选牌",
            _step_card_score(
                slot.card, slot.ref.seat, query.view.seat, short, view=query.view
            ),
        )
    if short in _GIVE_STEPS and len(action.picks) >= 2 and _wounded(query.view):
        total.add("仁德回复", _RENDE_RECOVER_VALUE)
    return total.finish()


_OPTION_VALUES: Mapping[str, float] = MappingProxyType(
    {
        # SP武将组1·SP孙尚香【良助】（sp1-sunshangxiang-maliang 票 02）：回复体力后的二选一。
        # 「令其摸两张牌」给的是回复者（常常是队友）两张、还会推进【返乡】的觉醒条件，
        # 比「摸一张牌」明显更值；两项都无代价，故都为正分。
        "liangzhu:draw_one": 1.0,
        # jie-yijiang-2014-wuyi-caozhen 票 03：界曹真【司敌】② 的二选一（两项都是净收益）。
        # 「取消之 + 对其 1 点伤害」= 一张对手的牌 + 1 点伤害（≈ 伤害牌本身的价值），
        # 「摸两张牌」= 两张牌 ≈ 2.0 ⇒ 伤害那一档略高（还能顺带废掉对手那一次使用）。
        "sidi:cancel": 2.2,
        "sidi:draw_two": 1.9,
        "liangzhu:draw_two": 1.8,
        # SP武将组5 马云騄【凤魄】（票 01）：X 未知时按"伤害一点 ≈ 2.8"估——对单目标【杀】，
        # +X 伤害通常比摸 X 张更值（摸牌还要再转化），但 X 小时两者接近。
        "fengpo:draw": 1.0,
        "fengpo:damage": 1.6,
        # feng-guanyu 票 01：锋·关羽【单骑】的颜色二选一（两项都是"把整手同色牌压成一张牌"）。
        # 火【杀】带属性、还可能逼掉【闪】；【决斗】逼的是【杀】且双方轮流出——都略低于
        # 一张真牌（1.5）的"立刻兑现"档，按 1.8 / 2.0 估。
        "danqi:black": 1.8,
        "danqi:red": 2.0,
        # feng-guanyu 票 01：锋·关羽【义云】/【心汉】的甲乙丙丁（两条技能共用同一组 id）。
        # 甲摸两张 ≈ 两张牌（1.6）；乙回复 1 点（≈2.0）；丙压低对手（失效到黑牌入弃牌堆）
        # 价值最高（2.4）；丁 1 点伤害（2.2）。
        "yiyun:draw_two": 1.6,
        "yiyun:recover_one": 2.0,
        "yiyun:suppress": 2.4,
        "yiyun:damage_one": 2.2,
        # 【雌雄双股剑】目标侧的两种损失：弃一张手牌 vs 令使用者摸一张。
        "discard_one": -1.0,
        "draw_one": -1.5,
        # 【刚烈】（M6 票 01）伤害来源的二选一：弃两张手牌 vs 受 1 点伤害。
        # 两点伤害按"1 点 ≈ 3.5、两张最便宜的牌 ≈ 2"估计 ⇒ 能弃牌就弃牌。
        "discard_two": -2.0,
        "take_damage": -3.5,
        # 【洛神】（M6 票 02 / B1）每个黑色判定结果后的「是否继续」：唯一选项「继续」=
        # 再来一轮=白拿一张黑牌；「放弃」= `Pass`，走通用 0 分。判据是"红色才停"，
        # 故每轮继续恒为正（不做概率模型）。
        "repeat": 2.0,
        # 自制包·锋·于禁【节钺】（feng-yujin 票 01）的两支：弃至多两张换 1 点伤害（≈1.4）
        # 对己方更直接；「标记并交给该角色」是送牌 + 封住对方那些牌当【杀】（对方弃置时
        # 还能摸一张），换来的压制弱一些 ⇒ 略低于伤害的分。
        "jieyue_damage": 1.4,
        "jieyue_mark": 0.8,
        # 陆抗移动版【决堰】（lukang-mobile 票 01）：三栏都是"永久废槽换一条本阶段 / 本回合
        # 收益"，故按即时变现强弱给分——摸三张 + 手牌上限最直接（2.5）；+3 次【杀】要求手里
        # 真有【杀】（2.0）；"无距离限制"要看场上距离，最容易白拿（1.5）。
        "duanyan_armor": 2.5,
        "duanyan_weapon": 2.0,
        "duanyan_horses": 1.5,
        # 界刘备【仁德】（M6 票 16）：本阶段第二张换来的"视为使用一张基本牌"。
        # 【桃】最稳（回血），【杀】次之（要目标），【酒】只有在能补刀时才值（保守给低）。
        "basic_slash": 2.0,
        "basic_peach": 3.5,
        "basic_wine": 1.0,
        # 界赵云【涯角】（M6 票 16）：类别相同 → 把亮出的牌交给一名角色（白赚一张）；
        # 类别不同 → 弃对手一张（拆牌的收益）或放弃弃牌。
        "yajiao_give": 1.5,
        "yajiao_top": 0.0,
        "yajiao_discard": 1.5,
        # 界孙尚香【结姻】（M6 票 17）的二选一：两种成本都要付一张牌；置装备还让目标
        # 多一件装备，故略优于纯弃置（下一步的选牌步仍按"自己的牌 = 成本"挑最便宜的）。
        "jieyin_discard_hand": 0.5,
        "jieyin_place_equipment": 1.0,
        # 界大乔【国色】（M6 票 17）的二选一：把方块牌当【乐不思蜀】是限制对手（正收益）；
        # 弃场上的【乐不思蜀】是解队友的控制（收益略低但同样为正）。
        "guose_use_indulgence": 2.0,
        "guose_discard_indulgence": 1.5,
        # 界许褚【裸衣】（用户裁定）：亮三张后的「获得 / 不获得」。获得 = 拿合格牌（最多
        # 三张）+ 放弃摸牌 + 跨回合【杀】/【决斗】基础伤害 +1；不获得 = 三张置入弃牌堆 +
        # 正常摸两张。获得在牌数与伤害两端都占优（最差也是用两张摸牌换一张合格牌 + 1 点
        # 伤害），故给正分；不获得只作为"没有合格牌"的退化档，不由 AI 主动选择。
        "luoyi_gain": 2.5,
        "luoyi_skip": 0.0,
        # 风包夏侯渊【神速】（票 01）的选项步：三档单选项。"【1】"只有代价（跳判定 / 摸牌）
        # 换来一张无距离【杀】，是该技能值得发动的那一档；"【2】"要多付一张装备牌 + 跳出牌
        # 阶段，贪心不选；"两项都选"= 两档之和、略低于【1】（多付一张装备）。
        "shensu_skip_judge_draw": 2.0,
        "shensu_skip_play_equip": -1.0,
        "shensu_both": 1.0,
        # 界夏侯渊【神速】（jie-feng-batch2 票 01）的多选面：三项各自估值、AI 取和最大
        # 的那一组。【1】与标版同档；【2】多付一张装备 + 让出整个出牌阶段（比标版更亏，
        # 因为牌面不再允许"两项都选"这种打包优惠）；【3】翻面 = 白丢一个回合。
        "jie_shensu_1": 2.0,
        "jie_shensu_2": -1.0,
        "jie_shensu_3": -3.0,
        # 典韦移动版【强袭】（huo-dianwei-mobile 票 01）的代价二选一：两档都是成本，
        # 由"哪一档更便宜"决定——1 点体力 ≈ 3.5、一张武器牌 ≈ 2.0 ⇒ 有牌就弃牌
        # （已发动的技能不能再放弃：`_is_committed_skill_step` 给 Pass 惩罚）。
        "qiangxi_lose_hp": -3.5,
        "qiangxi_discard_weapon": -2.0,
        # 神周瑜移动版【琴音】（huo-shenzhouyu-mobile 票 01）：发动之后**必选一项**。
        # 「各失去 1 点体力」是全场伤害（残局更主动）、「各回复 1 点体力」是全场治疗；
        # 两项各有得失，差值只体现既有姿态"更愿意进攻"。
        "qinyin_lose": 1.6,
        "qinyin_recover": 1.2,
        # 自制包·韬·曹操【倡义】（tao-caocao 票 01）。
        # - 段 2「点数 +X」：没有代价、直接把这一次拼点的点数抬高 ⇒ 恒选（高于放弃）。
        "changyi:boost": 3.0,
        # - 段 3「令其获得【倡义】」：复制一份给对方、自己保留。它是一条"输了要付代价"
        #   的技能，给出没有成本 ⇒ 保守给小的正值（不抵消拼点本身的收益）。
        "changyi:grant": 0.5,
        # - 段 4（须二选一，两格都是成本）：「摸 X 张并失去此技能」= 手牌 +X（约 0.8/张 × 2~3）
        #   减掉失去技能；「弃至少 X 张 + 1 点伤害」= 弃牌（约 1/张 × 2~3）换 1 点伤害
        #   （约 1.4）。两者接近，摸牌那格分略高：X 越大手牌收益越大，而弃牌那格的分随 X 走低。
        "changyi:draw": 1.2,
        "changyi:punish": 0.8,
        # shiji-zhi 票 01：神郭嘉【慧识】的两处单选项——「是否继续判定」= 白拿一张判定牌
        # （**加 1 上限已在问之前结算**，见用户 2026-11-14 订正），恒为正；「是否把判定牌交给
        # 一名角色」= 送牌收益，保守给正。
        "huishi_continue": 2.0,
        "huishi_give": 1.5,
        # 界魏延移动版【狂骨】（jie-feng-batch 票 02）：每点二选一，必答。
        # 「摸一张牌」永不空转（满体力时回复是零收益），故略高于回复。
        "kuanggu_heal": 1.5,
        "kuanggu_draw": 2.0,
        # 孙坚移动版【英魂】（lin-batch 票 02，Q6 = A）：座位步已登记为**敌对**（见
        # ``_HOSTILE_SEAT_STEPS``），故这里按"控场"给分——选项 2「摸一张牌，然后其弃置 X 张」
        # 是净拆牌（X ≥ 1，越到残局越强）；选项 1「摸 X 张，然后其弃置一张牌」是净送牌
        # （X ≥ 2 时等于给对手补牌），故只给一点"至少让它丢一张"的残值。
        "yinghun_draw_one_discard_x": 1.5,
        "yinghun_draw_x_discard_one": 0.2,
        # 界魏延移动版【奇谋】（jie-feng-batch 票 02）：动态 X 的中文选项值。
        # X 越大自伤越重（体力 ≤ 1 时技能本身就不发动）；登记 1..4 四档，X=1 最高 ⇒
        # AI 取最小自伤；> 4 的档位今天不可达（体力上限 4），未登记也仍是 0 < 1.0。
        "失去1点体力": 1.0,
        "失去2点体力": -0.6,
        "失去3点体力": -1.2,
        "失去4点体力": -1.8,
        # 林包·移动版董卓【崩坏】（lin-batch2 票 02，Q7 = A）：锁定技的**必答二选一**，两项
        # 都是成本。失 1 点体力可回复、减 1 点上限是永久（还会让后续每个结束阶段更难）⇒
        # 失体力那格略高。已知限制：1 体力时「失体力」= 进濒死，静态常量读不出这个
        # （要按"当前体力"细算得另开 AI 票），故这里把现状钉住、不假装它是对的。
        "bengkuai_lose_hp": -1.2,
        "bengkuai_reduce_max": -1.6,
        # 谋攻篇-同包·谋刘赪（mou-gong-tong 票 01）：「然后可以视为使用一张牌」的放弃出口。
        # 两个产物都不耗手牌、没有代价（【过河拆桥】拆对手一张 / 无次数限制的【杀】打一刀）
        # ⇒ 正分、高于"放弃"（0）；座位步另由 `_HOSTILE_SEAT_STEPS` 登记。
        "lveying:use": 1.2,
        "yingwu:use": 1.4,
        # 群英包·「锋势」陆逊【谦逊】（qunying 票 01）：三个代价与三个效果各自的估值。
        # 代价：② 掉 1 点无来源火焰伤害最便宜、③ 把一张装备牌当【闪电】放自己判定区是负收益、
        # ① 弃光手牌最贵。效果：① 免费使用一张牌最强、③ 按类型检索稳、② 换位置次之。
        "qianxun_cost_damage": 0.6,
        "qianxun_cost_equip": -0.4,
        "qianxun_cost_discard": -1.0,
        "qianxun_effect_card": 1.2,
        "qianxun_effect_gain": 0.9,
        "qianxun_effect_move": 0.7,
        # 群英包·「锋势」祢衡【狂才】（qunying 票 05）：三个**声明项**的估值 = 命中概率 ×
        # 命中时的收益（类别 1/3×3 张、花色 1/4×4 张、点数 1/13×13 张，期望都约 1 张）——
        # 三档同分，差异只是方差；登记成同一档是**刻意的**（盲猜，AI 看不见牌堆，所有
        # 具体值等概率，`_OPTION_VALUES` 里不给具体值分档）。
        "kuangcai:third_type": 1.0,
        "kuangcai:fourth_suit": 1.0,
        "kuangcai:thirteenth_rank": 1.0,
    }
)


def _enemy_identities(mine: str) -> frozenset[str]:
    """``mine`` 这个身份眼里的**敌人身份集合**（:data:`_HOSTILITY` 里标「敌」的那几列）。

    逐项等于票 21 之前的 :func:`_is_revealed_enemy`：主公侧 = 反贼 / 内奸、反贼 = 主公侧、
    内奸 = 忠臣 / 反贼（**不含主公**）；PvE 侧 = 另一个阵营（票 08 起）。
    """
    row = _HOSTILITY.get(mine)
    if row is None:
        return frozenset()
    return frozenset(theirs for theirs, relation in row.items() if relation == _ENEMY)


def _ally_identities(mine: str) -> frozenset[str]:
    """``mine`` 这个身份眼里的**盟友身份集合**（:data:`_HOSTILITY` 里标「友」的那几列）。

    按**阵营**算、不是按人（内奸没有盟友）。比票 21 之前的 :func:`_is_revealed_ally`
    **宽一档**：旧函数只认"主公与我同在主公阵营"，这里认整个同阵营（主公侧的忠臣 /
    反贼一方的反贼）。今天只有主公与 ``identity_visible`` 会亮明身份，故扩宽是**潜伏**的；
    一旦 M5 的亮明效果让忠臣 / 反贼露牌，按阵营记盟友才是对的——那条语义由
    :func:`_identity_relation` 的"已亮明 ⇒ 精确 0/1"分支承载。

    **第二个读取方**：:func:`_score_baonue`（``"lord" in _ally_identities(mine)``；票 08 的
    票面按旧名 ``_score_huangtian`` 写，那个位置现在是【暴虐】的打分器）。两个视图都从同一张
    表派生，故改写这张表时它自动跟上——别只改 :func:`_identity_relation` 的函数体（那会让
    那一处继续吃空集）。
    """
    row = _HOSTILITY.get(mine)
    if row is None:
        return frozenset()
    return frozenset(theirs for theirs, relation in row.items() if relation == _ALLY)


def _unknown_identity_remainder(
    view: PlayerView,
) -> tuple[tuple[str, ...], int] | None:
    """配比减掉**已亮明身份**后的剩余多重集 + 存活未知座位数（I1 身份保守档）。

    已亮明 = ``PlayerPublic.identity`` 非空（本人 / 主公 / 已阵亡 / ``identity_visible``）。
    三条"算不出"一律 ``None``（**不猜**，调用方退回 ``(0, 0)``＝票 21 之前的行为）：

    - 人数不在 :data:`_IDENTITY_ROSTER` 里（2 人无身份局、3 人以下的构造局面）；
    - 亮明身份不在配比里（人为局面写错了阵营，如两个主公）；
    - 阵亡却身份不可见（投影上不该发生），或剩余人数与存活未知座位数对不上。
    """
    roster = _IDENTITY_ROSTER.get(len(view.players))
    if roster is None:
        return None
    remaining = list(roster)
    unknown = 0
    for player in view.players:
        if player.identity is not None:
            try:
                remaining.remove(player.identity)
            except ValueError:
                return None
        elif player.alive:
            unknown += 1
        else:
            return None
    if unknown != len(remaining):
        return None
    return tuple(remaining), unknown


_LORD_SIDE = frozenset({_LORD, _LOYALIST})
"""内奸眼里的**主公侧**（票 32）：均衡式子里的 ``E[主公侧]`` 数这一侧。"""

_REBEL_SIDE = frozenset({_REBEL})
"""内奸眼里的**反贼侧**：``E[反贼侧]`` 只数反贼（内奸自己两边都不算）。"""

_LORD_ONLY = frozenset({_LORD})
"""``_LORD_SIDE`` 里只数主公的那一格：护主护栏的判据（``theirs in _LORD_ONLY``）。

与 :data:`_REBEL_SIDE` 同一种写法（单元素 frozenset + 成员判定）：内奸行里"是不是主公 /
反贼"的两种问法只有一张表，不混用 ``==`` 与 ``in``——两处都读集合，改配比时一起看。
"""


def _alive_count(view: PlayerView) -> int:
    """存活座位数（公开面 ``PlayerPublic.alive``）。

    两个读方共用：内奸行的分母 ``D = 存活人数 − 1``（:func:`_renegade_balance`）与
    "还有别人活着吗"的硬 veto 阈值（:func:`_renegade_lord_off_limits`，`ai-view` 票 11）。
    """
    return sum(1 for player in view.players if player.alive)


def _renegade_balance(view: PlayerView) -> tuple[int, int, int] | None:
    """内奸行的局面读数 ``(D, E[主公侧], E[反贼侧])``；算不出 ⇒ ``None``（票 32）。

    - ``D = 存活座位数 − 1``：分母是**别的**存活座位数（内奸自己占一席、又不属于任何一侧）；
    - ``E[侧] = 已亮明且存活的该侧人数 + :func:`_unknown_identity_remainder` 剩余多重集里
      该侧的份数``。**两半都不能省**：主公是亮明的、他已被从剩余多重集里减掉，只数后半
      （``count(lord in 剩余)``）会得 0 并把整条式子算歪。两半相加 = 该侧存活人数的**期望**
      （配比固定 ⇒ 这条其实是确定值，逐座位的不确定性只影响"谁是哪一侧"）。

    读的全是**公开面**：``view.players`` 的 ``identity`` / ``alive`` 与配比字面表。算不出
    （人数不在配比表 / 亮明身份不在配比里 / 阵亡却不可见）⇒ ``None``，调用方按"中性"处理
    ——与 :func:`_unknown_identity_remainder` 同一条 fail-closed 纪律，**不猜**。

    这笔读数**与"对谁"无关**：一次询问里对多个身份求 ``H`` 时只该算一次
    （:func:`_renegade_unknown_relation` 已把它提到循环外，票 35）。
    """
    remainder = _unknown_identity_remainder(view)
    if remainder is None:
        return None
    remaining, _unknown = remainder
    lord_side = sum(
        1
        for player in view.players
        if player.alive and player.identity in _LORD_SIDE
    )
    rebel_side = sum(
        1
        for player in view.players
        if player.alive and player.identity in _REBEL_SIDE
    )
    lord_side += sum(1 for identity in remaining if identity in _LORD_SIDE)
    rebel_side += sum(1 for identity in remaining if identity in _REBEL_SIDE)
    return _alive_count(view) - 1, lord_side, rebel_side


def _renegade_hostility_from_balance(
    theirs: str, balance: tuple[int, int, int]
) -> float:
    """在**已算好**的 :func:`_renegade_balance` 上取 ``H(内奸 → theirs)``（票 35 的纯函数半边）。

    ``_renegade_unknown_relation`` 要对剩余多重集里的每个身份各求一次 ``H``，而局面读数与
    "对谁"无关 ⇒ 提出来只算一次，座位数不再平方级（原实现每个身份都重算一次 balance，
    而 balance 自己又要遍历全座位并 ``list.remove``）。逐值等于逐身份调
    :func:`_renegade_hostility`。
    """
    if theirs not in _LORD_SIDE and theirs not in _REBEL_SIDE:
        return 0.0
    divisor, lord_side, rebel_side = balance
    if divisor <= 0:
        return 0.0
    spread = (lord_side - rebel_side) / divisor
    if theirs in _REBEL_SIDE:
        return -spread
    if theirs in _LORD_ONLY and rebel_side > 0:
        return min(spread, 0.0)
    return spread


def _renegade_hostility(view: PlayerView, theirs: str) -> float:
    """``H(内奸 → theirs)``：按**存活力量均衡**现算的局面函数（M7 票 32）。

    票 08 把敌对值表整张表化成常量；这是**唯一**一行不是常量的：内奸希望两阵营势均力敌，
    于是以"主公侧 − 反贼侧"为轴——

    - ``H(→主公) = min(spread, 0)`` **只要反贼侧还有人活着**（护主护栏：主公早死反贼就赢，
      这一刀不能打）；反贼侧清空时才放开（只剩主公 ⇒ ``+1``，:func:`_identity_relation`
      按 ``(max(H,0), max(−H,0))`` 给到 ``(1, 0)``）。**护栏只压权重、压不住斩杀项**
      （``4.0 + 6.5 − 5.0``）：真正的"不许打"是 :func:`_renegade_lord_veto_applies` 的
      规则级 veto（`ai-view` 票 11），别以为把 H 调到 0 就够了；
    - ``H(→忠臣) = spread``：主公侧领先就压忠臣（4 人局 ``+1/3`` 是**正确行为**：反贼一死
      内奸就要 1v2），落后就帮忠臣；
    - ``H(→反贼) = −spread``（与忠臣互为反号）。``H(→内奸) = 0``；跨模式的友 / 敌两格
      恒 0（表里也是中立——那两个身份永不与身份局共局）。

    ``spread = (E[主公侧] − E[反贼侧]) / D``（见 :func:`_renegade_balance`），落在 ``[-1, 1]``。
    **「只剩主公 ⇒ +1」不写特例**：那时 ``D = 1、E[反贼侧] = 0``，公式自己给出 ``+1``——
    写特例就是给同一语义留两个真源。算不出（配比 / 亮明面对不上）⇒ ``0.0``（中性，不猜）。
    """
    if theirs not in _LORD_SIDE and theirs not in _REBEL_SIDE:
        return 0.0
    balance = _renegade_balance(view)
    if balance is None:
        return 0.0
    return _renegade_hostility_from_balance(theirs, balance)


def _renegade_unknown_relation(
    view: PlayerView, remaining: tuple[str, ...]
) -> tuple[float, float]:
    """内奸面对**未知座位**的关系权重：``(P敌, P友)`` 对该行在剩余多重集上求期望（票 32）。

    逐身份取 ``(max(H(c), 0), max(−H(c), 0))`` 再按份数平均——``H(座位) = P敌 − P友`` 因此
    逐值等于 ``Σ H(c) / |剩余|``（票面那条式子），两侧各自保留"这份可能是敌人 / 是盟友"的
    信息。与同一函数对另外五行未知座位的"敌人 / 盟友份数 ÷ 未知座位数"是同一条口径：
    0/1 行在这个式子上恰好退化成"份数 ÷ 人数"。

    ``|剩余| = 0`` ⇒ ``(0, 0)``：与 :func:`_identity_relation` 的"算不出就中性"同一条纪律，
    也与 ``unknown <= 0`` 的防御分支一致。

    **局面读数只算一次**（票 35）：:func:`_renegade_balance` 与"对谁"无关，而它自己又要
    遍历全座位并 ``list.remove``——逐身份调 :func:`_renegade_hostility` 会让一次询问变成
    座位数个平方级重算。这里算一次 ``balance`` 再逐身份走
    :func:`_renegade_hostility_from_balance`（纯等价：逐值等于原写法）。
    """
    if not remaining:
        return 0.0, 0.0
    balance = _renegade_balance(view)
    if balance is None:
        return 0.0, 0.0
    enemy = 0.0
    ally = 0.0
    for identity in remaining:
        hostility = _renegade_hostility_from_balance(identity, balance)
        enemy += max(hostility, 0.0)
        ally += max(-hostility, 0.0)
    return enemy / len(remaining), ally / len(remaining)


def _identity_relation(view: PlayerView, seat: int) -> tuple[float, float]:
    """``seat`` 与我的关系权重 ``(P(敌), P(友))``——只读公开面（I1 身份保守档，票 21）。

    三档，替换的是原来"已明敌人 ⇒ 1、其余 ⇒ 0"的 0/1：

    - **已亮明**（``identity`` 非空）⇒ 精确的 0/1（配比表不参与这一步：亮明的身份是**确定**
      信息，不因表对不对而变）。敌人一侧逐项等于票 21 之前；**盟友一侧按阵营算**、比旧的
      "只认主公"宽一档（见 :func:`_ally_identities`，今天只在 ``identity_visible`` 露牌时
      才可能走到）；
    - **未知** ⇒ :func:`_unknown_identity_remainder` 的**均匀后验**：剩余多重集按人头均分，
      ``P = 该集合里"敌人 / 盟友身份"的份数 ÷ 存活未知座位数``；
    - **算不出**（见上）⇒ ``(0.0, 0.0)``，即今天的中性（不猜）。

    **`ai-view` 票 07 换掉了"未知"那一档的来源**：先问
    :func:`~sgs.agents.identity.joint_posterior`——有公开行为证据时按**精确联合后验**的
    边缘取 ``P(敌) / P(友)``（内奸行仍按局面函数 ``H`` 加权）；没有证据 / 算不出 ⇒
    逐值落回上面的均匀后验。调用面（本函数签名 / 返回值 / 14 个调用点）一字未改，
    ``P(敌)`` 仍只作连续权重。

    自己恒 ``(0.0, 1.0)``；``P(敌) + P(友) ≤ 1``，剩下的质量是"两边都不是"（反贼眼里的
    内奸、内奸眼里的主公）。**``P(敌)`` 只作权重**：调用方乘在原 0/1 的幅度上，不把它
    升格成"必须打谁"的硬判（spec §6.4）。

    **PvE（M7 票 08）**：友 / 敌两个身份走**已经亮明**那一档、**不走概率档**——PvE 开局全部
    ``identity_visible``（票 01 §3），且 ``PveConfig`` 的阵营就是关系本身（:data:`_HOSTILITY`
    的 ALLY / ENEMY 两行）：``ALLY → (0, 1)``、``ENEMY → (1, 0)``，两侧都读到硬 ``±1``。
    读点仍是同一个函数、同一个 ``PlayerPublic.identity`` 字段（不新增读点）。

    **内奸行（M7 票 32）**：``mine == _RENEGADE`` 时**不查表**，走局面函数
    :func:`_renegade_hostility`（按存活力量均衡 + 护主护栏）——敌对值表里唯一一行不是常量
    的。返回值契约一字不变（``(max(H,0), max(−H,0))``、非负、和 ≤ 1），故 14 个调用点
    一行不改；未知座位走 :func:`_renegade_unknown_relation`（该行对剩余多重集求期望）。
    """
    if seat == view.seat:
        return 0.0, 1.0
    if not 0 <= seat < len(view.players):
        return 0.0, 0.0
    mine = view.players[view.seat].identity
    if mine is None:
        return 0.0, 0.0
    theirs = view.players[seat].identity
    if theirs is not None:
        if mine == _RENEGADE:
            # 内奸行（票 32）：局面函数，不查表。跨模式格子（友 / 敌）由它返回 0（中立）。
            hostility = _renegade_hostility(view, theirs)
            return max(hostility, 0.0), max(-hostility, 0.0)
        # 已亮明 ⇒ **查敌对值表**（表是"谁恨谁"的唯一真源，见 :data:`_HOSTILITY`）。
        relation = _hostility(mine, theirs)
        return (
            1.0 if relation == _ENEMY else 0.0,
            1.0 if relation == _ALLY else 0.0,
        )
    remainder = _unknown_identity_remainder(view)
    if remainder is None:
        return 0.0, 0.0
    remaining, unknown = remainder
    posterior = identity_model.joint_posterior(view)
    if posterior is not None:
        row = posterior.marginal(seat)
        order = identity_model.IDENTITY_ORDER
        if mine == _RENEGADE:
            # 内奸行仍是**局面函数**：边缘后验只换掉"剩余多重集均分"这一层权重，
            # ``H(→c)`` 的式子与护主护栏一字不动（逐值等于 ``_renegade_unknown_relation``
            # 在均匀权重下的读数）。
            balance = _renegade_balance(view)
            if balance is not None:
                enemy = 0.0
                ally = 0.0
                for index, candidate in enumerate(order):
                    hostility = _renegade_hostility_from_balance(
                        candidate.value, balance
                    )
                    enemy += row[index] * max(hostility, 0.0)
                    ally += row[index] * max(-hostility, 0.0)
                return enemy, ally
        else:
            enemies = _enemy_identities(mine)
            allies = _ally_identities(mine)
            # 迭代身份序（tuple）而不是 frozenset：源码纪律禁无序容器迭代。
            return (
                sum(
                    row[index]
                    for index, candidate in enumerate(order)
                    if candidate.value in enemies
                ),
                sum(
                    row[index]
                    for index, candidate in enumerate(order)
                    if candidate.value in allies
                ),
            )
    if mine == _RENEGADE:
        # 内奸行：该行对剩余多重集求期望（`|剩余| = 0` ⇒ (0, 0)，见函数本体）。
        return _renegade_unknown_relation(view, remaining)
    if unknown <= 0:  # pragma: no cover - 未知座位自己就是其中一个，防御分支
        return 0.0, 0.0
    enemies = _enemy_identities(mine)
    allies = _ally_identities(mine)
    return (
        sum(1 for identity in remaining if identity in enemies) / unknown,
        sum(1 for identity in remaining if identity in allies) / unknown,
    )


def _chosen_seats(action: Action) -> tuple[int, ...]:
    """这一次使用**被挑出来**的座位：``targets`` + 附带指定（借刀的 Bn）。

    借刀的"害"落在 Bn 上（逼谁去打谁），只读 ``targets``（持刀者 A）会把"逼敌人去打主公"
    评成正收益；关系项与主公 veto 因此都按这一份座位看。
    """
    return (
        *getattr(action, "targets", ()),
        *getattr(action, "designations", ()),
    )


def _hostile_card(card: CardRef) -> bool:
    """这张牌对目标是不是**有害**：伤害牌（:func:`_damage_card`）或有害锦囊。

    有害锦囊的牌名真源是 :data:`_NULLIFY_HARMFUL_TRICKS`（无懈那一族）：关系项与主公
    veto 都只按它分"有害 / 有益"，不另立第二张牌名表。
    """
    return _damage_card(card) or card.type in _NULLIFY_HARMFUL_TRICKS


def _is_lord_seat(view: PlayerView, seat: int) -> bool:
    """``seat`` 是不是**本人以外**的已亮明主公（座位层的事实，不含"谁不能打"）。

    读 ``PlayerPublic.identity``（投影已按"本人 / 主公 / 亮明 / 阵亡"裁过）。``==`` 不是
    ``is``：``PlayerPublic.identity`` 是 ``Identity`` 枚举成员（str 子类），而 :data:`_LORD`
    是同一个字面量的 ``str``；``is`` 在两者之间恒 False。
    """
    return (
        seat != view.seat
        and 0 <= seat < len(view.players)
        and view.players[seat].identity == _LORD
    )


def _renegade_lord_off_limits(view: PlayerView) -> bool:
    """内奸视角里"主公暂时不能打"的身份前提（`ai-view` 票 11）。

    本人坐内奸位**且存活人数 > 2**（主公与内奸之外还有别人）⇒ 主公一死立刻由反贼获胜：
    ``sgs.engine.victory`` 的主公死亡分支只在"存活集合恰为 ``{唯一内奸}``"时判内奸胜
    （同批内奸也死 / 无人存活这两格判主忠方）；**只要内奸之外还有别人活着就一律反贼胜**
    ——忠臣还活着也一样，反贼全灭也一样。故这一刀对内奸是自杀。

    存活人数 = 2（只剩主公与内奸）⇒ **不拦**：主公一死内奸独活即胜
    （``lord_dead_sole_renegade_survives``）——内奸必须留着这唯一一条赢法。

    牌层（:func:`_renegade_lord_veto_applies`）与座位层（:func:`_lord_is_vetoed_target`）
    共读这一处，别在两边各写一个 ``> 2``。
    """
    return (
        view.players[view.seat].identity == _RENEGADE
        and _alive_count(view) > 2
    )


def _lord_is_vetoed_target(view: PlayerView, seat: int) -> bool:
    """这次询问里，主公 ``seat`` 能不能被我当作目标（座位层，Q4 + 票 11）。

    - **主忠方**恒不能（Q4 的硬 veto）；
    - **内奸**只在还有别人活着时不能（:func:`_renegade_lord_off_limits`）；1v1 放开；
    - 反贼 / PvE（没有 ``lord`` 身份）不受约束。

    座位层的这一份给"计划"用：:func:`_wine_enables_kill` / :func:`_equip_setup_score`
    是在为**以后那一刀**做铺垫，若那一刀的目标会被 veto 摘掉，这口【酒】/ 这件装备就白出。
    牌层的那两份（:func:`_lord_veto_applies` / :func:`_renegade_lord_veto_applies`）还要看
    牌名与目标数档位，见它们本体。
    """
    if not _is_lord_seat(view, seat):
        return False
    return (
        view.players[view.seat].identity in _LORD_SIDE
        or _renegade_lord_off_limits(view)
    )


def _relation_term(view: PlayerView, seat: int) -> float:
    """``seat`` 当**我打出去的那张牌的目标**值多少：``_RELATION_FOCUS × (P敌 − P友)``。

    伤害牌（:func:`_score_attack`）与单体有害锦囊（:func:`_score_card_use`）**共用这一个
    函数**——同一个"打谁"的语义只留一处实现，别在两处各写一遍（票面的调用面收敛）。式子
    与 :func:`_luanwu_respond_value` / :func:`_score_distribute` 同形（``k × (敌 − 友)``），
    系数见 :data:`_RELATION_FOCUS`。

    自己 / 越界座位恒 ``0.0``：牌打在自己身上（【桃】/ 装备 / 判定区 / 闪电这类只对自己的
    牌）没有"对谁"这一说，别把自伤当成"打盟友"。

    **已明身份与未知座位走同一式子**：``(P(敌), P(友))`` 由 :func:`_identity_relation` 给，
    算不出时它返回 ``(0, 0)`` ⇒ 本函数也退回 0（与 I1 之前的行为一致）。未知座位的正负号
    由先验决定（P敌 > P友 ⇒ 正）——扣分不会把"不确定的席位"一起压死。
    """
    if seat == view.seat or not 0 <= seat < len(view.players):
        return 0.0
    enemy, ally = _identity_relation(view, seat)
    return _RELATION_FOCUS * (enemy - ally)


def _harmful_trick_relation(action: Action, view: PlayerView) -> float:
    """单体**有害**锦囊的关系项之和；其余锦囊（有益 / 自己 / 全体）恒 ``0.0``。

    修复的那一格：``_score_card_use`` 的非伤害分支原来一律走 :data:`_TRICK_VALUES` 的
    **常量分、完全不读身份** ⇒ 忠臣手里有【过河拆桥】就拆主公（实机 seed 7032）。

    牌名分类**复用无懈那一族**（:data:`_NULLIFY_HARMFUL_TRICKS`：拆 / 顺 / 乐 / 兵粮 /
    借刀 …）——"这张牌对目标是不是害"已经有唯一真源，此处不另立一张表。

    已经算过关系项的牌不在这里重算：伤害牌（含【决斗】/【南蛮】/【万箭】）由
    :func:`_score_attack` 逐目标接走，【火攻】有自己的 :func:`_score_fire_attack`；否则
    同一笔关系会被记两次。**附带指定**（借刀杀人的 Bn）也算被指定的座位：借刀"害"的是
    Bn，只读 ``targets``（持刀者 A）会把"逼敌人去打主公"评成正收益。
    """
    if not isinstance(action, UseCard):
        return 0.0
    card = action.card
    if card.type not in _NULLIFY_HARMFUL_TRICKS:
        return 0.0
    if _damage_card(card) or card.type == "fire_attack":
        return 0.0
    return sum(_relation_term(view, seat) for seat in _chosen_seats(action))


def _hostile_card_vetoes_the_lord(
    view: PlayerView, card: CardRef, targets: Sequence[int]
) -> bool:
    """有害牌 × **挑出来的**目标 × 目标含主公：两条主公 veto 共用的牌层判据（票 01 / 票 11）。

    - **有害牌**：:func:`_hostile_card`（伤害牌或有害锦囊）；
    - **挑目标**：目标集合由牌面**强制**的全体牌（万箭 / 南蛮 / 桃园 / 五谷，``ALL_ALIVE*``
      档）不算——使用者挑不了目标，取舍交给 :func:`_relation_term` 的逐目标期望差分；
    - **目标含主公**：``targets`` 是 ``_chosen_seats`` 的结果（含借刀的 Bn），逐座位问
      :func:`_is_lord_seat`——本函数只回答"打到的是不是主公"，**谁不能打**由两个调用方
      各自的身份前提给出（主忠方恒是 / 内奸还有他人存活时，见 :func:`_lord_veto_applies`）。
    """
    if not _hostile_card(card):
        return False
    if behavior_for(card).target_rule.count in (
        TargetCount.ALL_ALIVE,
        TargetCount.ALL_ALIVE_WITH_SELF,
    ):
        return False
    return any(_is_lord_seat(view, seat) for seat in targets)


def _renegade_lord_veto_applies(
    view: PlayerView, card: CardRef, targets: Sequence[int]
) -> bool:
    """内奸版主公 veto（`ai-view` 票 11）：还有他人存活时，敌意牌不许落在主公身上。

    实机（seed 1066672543218525911）：内奸在有反贼存活时对主公出【杀】，把主公打到 0 体力
    ⇒ 主公一死反贼立刻获胜，内奸自己什么都没赢到。根因是关系项压不住斩杀项：
    ``伤害底价 3 + 目标数 1 + 斩杀 6.5 − 关系 1.6667 = +8.83`` ⇒ 打得到就打。故与票 01 的
    主公 veto 同一形状：在**规则层**把这条候选摘到 :data:`_LORD_VETO_PENALTY`，不是再加权重。

    只多一条身份前提（本人是内奸、且还有别人活着，:func:`_renegade_lord_off_limits`）；
    牌名 / 目标档位 / 主公座位三份判据与主忠方共用 :func:`_hostile_card_vetoes_the_lord`。
    **不碰响应窗口**（借刀 / 决斗里被逼出牌是规则），也**不 veto 全体强制目标**（票 01 同一条）。
    """
    if not _renegade_lord_off_limits(view):
        return False
    return _hostile_card_vetoes_the_lord(view, card, targets)


def _lord_veto_applies(view: PlayerView, action: Action) -> bool:
    """主忠方 / 内奸在**出牌阶段挑目标**时，主公不可作为目标（Q4 / `ai-view` 票 11 的硬 veto）。

    四条边界（票面原文逐条落地）：

    - **主忠方**：本人是主公或忠臣（:data:`_LORD_SIDE`）⇒ 主公恒不可选。PvE 没有 ``lord``
      这个身份 ⇒ 恒不生效。
    - **内奸**：本人是内奸时挂在**同一格**，但只在还有别人活着时生效
      （:func:`_renegade_lord_veto_applies`）——1v1 要留给内奸"杀主公即胜"的唯一赢法。
      反贼不受此约束（杀掉主公正合其胜法）。
    - **有害牌**：伤害牌（:func:`_damage_card`）与有害锦囊（:data:`_NULLIFY_HARMFUL_TRICKS`）。
      给主公递牌 / 回血的**有益**牌不在此列（无中 / 桃 / 桃园 / 五谷 …），它们本来就该打在
      主公身上。**已知代价**：牌名层的"有害"分不出**效果**层的善意——【过河拆桥】拆掉主公
      判定区的【乐不思蜀】是保护，但它与拆主公手牌的**牌名、目标完全相同**，故一并被 veto
      与关系项压掉（票面第二条 D 判据要的正是"主公是唯一合法目标时不出【过河拆桥】"）。
      这一格的口径冲突登记在票 01 的 `## Answer`（台账 `ai-versions.md` 的 7004 那条把
      "忠臣拆主公的【乐】"记成可观察的好行为）；按效果拆细是 spec §2.3 给票 04/05 的事。
    - **挑目标**：目标集合由牌面**强制**的全体牌（万箭 / 南蛮 / 桃园 / 五谷，``ALL_ALIVE*``
      档）不在 veto 范围内——它们的目标使用者挑不了，取舍由 :func:`_relation_term` 的逐
      目标期望差分给出（票面："AoE 不需要新机制"）。单体 / 区间 / 附带指定的牌才是"挑"。

    只在 ``PLAY_PHASE`` 的打分器里调用（:func:`_score_play_phase`）：**不碰响应窗口**——
    【借刀杀人】逼你砍主公是规则，禁不掉（``_score_respond`` 一行不改）。
    """
    card = getattr(action, "card", None)
    if card is None:
        return False
    targets = _chosen_seats(action)
    if view.players[view.seat].identity in _LORD_SIDE:
        return _hostile_card_vetoes_the_lord(view, card, targets)
    return _renegade_lord_veto_applies(view, card, targets)


_HOSTILE_SEAT_STEPS = (
    "lijian:",
    "fanjian:",
    "liuli:",
    # M6 票 18：界华佗【除疠】（弃敌方的牌）与界华雄【势斩】（让敌方来决斗）的座位步；
    # 界吕布【利驭】的"受伤者指定决斗目标"也用全限定前缀（决定者是受伤者，他自己选）。
    "chuli:",
    "shizhan:",
    "hero.jie_lvbu.liyu:",
    # 界大乔【国色】（M6 票 17）的【乐不思蜀】目标：手写询问用**全限定** skill_id
    # （``hero.jie_daqiao.guose:seat``），故这里登记整段前缀，不另造第二套短 id 约定。
    "hero.jie_daqiao.guose:",
    # jie-yijiang-2014-wuyi-caozhen 票 03：界曹真【司敌】① 第 1 步"预测谁"——选一个**敌人**
    # 更可能出手；第 2 步（预测目标）不登记：缺省的"盟友 − 敌人"恰好合理。
    "jie_caozhen_sidi_predict:seat:0",
    # 攻心（票 01；界吕蒙 ☆ / 神吕蒙移动版共用）：看谁的牌 + 可能弃 / 置顶他的牌 ⇒ 对手。
    # 不登记会落进"帮盟友"的缺省分支（`docs/agents/ai-adaptation.md` §1 第 2 条点名的坑）。
    "gongxin:",
    # 神甘宁【魄袭】（lei-shenganning 票 01）：看谁的牌 + 可能弃他四色各一 ⇒ 对手
    # （与【攻心】同一档；缺这条 AI 会挑盟友的牌来弃）。
    "poxi:",
    # 神甘宁【劫营】②（同票）：座位是**接下"营"的人**——他的回合末我要拿走其整副手牌，
    # 故这是"被指向的对手"那一档；落进帮助类会让 AI 把营送给盟友、回合末去抢盟友。
    "jieying:",
    # 谋攻篇-同包·谋刘赪（mou-gong-tong 票 01）：① 的两问都指向**对手**——虚拟【过河拆桥】
    # 拆敌方一张牌、虚拟【杀】砍敌方一刀；手写询问用全限定 skill_id
    # （``hero.mou_liucheng.lveying:target`` / ``...yingwu:target``），不登记会落进"帮盟友"。
    "hero.mou_liucheng.lveying:",
    "hero.mou_liucheng.yingwu:",
    # 群英包·「锋势」陆逊【谦逊】（qunying 票 01）：座位步都指向**别人**——
    # 「②移动场上一张牌」的**目的地**放到另一名角色那里；「①视为使用」的目标同样是敌人。
    # 不登记会落进"帮盟友"的缺省分支。
    # 票 03b 起效果② 的"目的地"是流水线的 ``to`` 步（询问名 ``qianxun:to``，短 id 前缀），
    # 手写的 ``:target`` / ``:targets`` 仍是全限定 id ⇒ **两条前缀都登记**（只留全限定会
    # 让流水线那一步落回"帮盟友"）。
    "qianxun:",
    "hero.qunying_luxun.qianxun:",
    # 群英包·「锋势」祢衡【舌剑】（qunying 票 05）：手写询问的**目标步**（虚拟雷【杀】打谁）——
    # 全限定 id ``hero.qunying_niheng.shejian:target``，不登记会落进"帮盟友"的缺省分支。
    "hero.qunying_niheng.shejian:",
    # 风包夏侯渊【神速】（票 01）：每张虚拟【杀】的**目标**——手写询问用全限定 skill_id
    # （``hero.xiahouyuan.shensu:target:0/1``），不登记 AI 会拿【杀】打队友。
    # 界沮授移动版【渐营】②（yijiang-2014-round2 票 01 / W3；票 18 迁进流水线）：产物是
    # 【杀】家族时，第 3 步选的是**这张【杀】打谁** ⇒ 敌人。不登记会落进"帮盟友"的缺省
    # 分支（AI 拿【杀】打队友）。**登记短 id 前缀**：票 18 起这一问是流水线第 3 步，AI 按
    # ``<短技能 id>:<步骤键>``（:func:`_fill_activation` 的拼法）估值，引擎发的询问名也是
    # 同一口径（``flow._flow_option_id`` 从全限定 id 派生短 id）。此前那条全限定
    # ``hero.jie_jushou_mobile.jianying.target`` 是"提交点后手写一问"的 id，已随票 18 删除。
    "jianying:",
    "hero.xiahouyuan.shensu:",
    # 界夏侯渊【神速】（jie-feng-batch2 票 01）：同一手写询问、同一前缀陷阱
    # （``hero.jie_xiahouyuan_mobile.shensu:target:<项号>``）。
    "hero.jie_xiahouyuan_mobile.shensu:",
    # 张角移动版【雷击】（feng-zhangjiao 票 01）：座位步选的是**被判定者**（也就是挨 2 点
    # 雷电伤害的人）——不登记会落进"帮盟友"的缺省分支，AI 会给队友判一次、白打队友。
    "hero.zhangjiao_mobile.leiji:",
    # 界张角移动版【雷击】（jie-feng-batch2 票 02）：同一手写座位步，同一档。
    "hero.jie_zhangjiao_mobile.leiji:",
    # 典韦移动版【强袭】（huo-dianwei-mobile 票 01）：选的是**挨 1 点伤害的人**——不登记
    # AI 会把【强袭】的伤害砸在队友身上。
    "hero.dianwei_mobile.qiangxi:",
    # 荀彧【驱虎】（huo-taishici-xunyu 票 02）：赢段的座位步选的是**挨 A 的 1 点伤害的人**
    # （全限定 skill_id `hero.xunyu.quhu:receiver`）——不登记会落进"帮盟友"的缺省分支。
    "hero.xunyu.quhu:",
    # 鲁肃【缔盟】（jushou-lusu 票 02，Q6 = A）：两步座位步选的是**被交换手牌的两名角色**
    # ——收益方向是"把敌人的手牌换给队友"，落进帮助类会让 AI 帮敌人换出一手好牌。
    "hero.lu_su_mobile.dimeng:",
    # 兵势篇-节包·势赵云【武翊】①（bingshi-jie 票 01）：清单唯一一步选的是**这张虚拟【杀】
    # 打谁** ⇒ 对手。不登记会落进"帮盟友"的缺省分支（AI 拿【杀】打队友）。
    "wuyi:",
    # 自制包·韬·曹操【倡义】（tao-caocao 票 01）：段 4-2 选的是**挨 1 点伤害的人**
    # （全限定 skill_id `hero.tao_caocao.changyi:target`）——不登记 AI 会把伤害砸在队友身上。
    "hero.tao_caocao.changyi:",
    # 自制包·锋·于禁（feng-yujin 票 01）：【节钺】的座位步 = "让谁交牌给你"（对手）；
    # 【定乱】的座位步 = "展示谁的牌"（标记让那些牌不能当【杀】⇒ 对手）。
    "jieyue:seat",
    "dingluan:seat:",
    # 孙坚移动版【英魂】（lin-batch 票 02，Q6 = A）：座位步选中的是"被令摸牌再弃牌"的人
    # ——摸 X 弃 1 是**送牌**，故这条技能只能固定一个方向：不登记就落进"帮盟友"分支，
    # 1V1 里会把牌塞给敌人（资敌）。登记为对手 + 选项 2 正分 = 至少永不资敌。
    "yinghun:",
    # 曹丕移动版【放逐】（lin-batch2 票 01，Q7 = A，`docs/agents/ai-adaptation.md` §1 已知坑二）：
    # 座位步选中的是"被翻面再摸 X 张"的人 —— 翻面 = **跳过其整个下个回合**（控场收益），
    # X 张是给它的补偿。不登记就落进"帮盟友"分支：AI 会把队友翻面、白送它一个回合的牌，
    # 纯自伤。1V1 下唯一其他座位就是敌人，登记为对手即正确方向。
    "fangzhu:",
    # 【激将】的「出牌阶段主动使用【杀】」侧（yijiang-2014-round2 票 20）：清单唯一一步
    # 选的是**这张【杀】的目标** ⇒ 对手。不登记就落进"帮盟友"分支：AI 会拿盟友代打的
    # 【杀】去打另一个盟友。前缀 = 短技能 id（票 18 起的拼法 ``<短 id>:<步骤键>``）。
    "jijiang:",
    # 贾诩移动版【乱武】（lin-batch2 票 03，Q9 = B）：并列最近时**先选**的座位就是那张
    # 【杀】的额定目标（挨打的人）。不登记就落进"帮盟友"分支：AI 会先把队友选成目标，
    # 再在下一问按敌我给分放弃 ⇒ 白失 1 点体力（比"能打敌人却失血"更差）。
    # 全限定前缀，与手写的 ``hero.jiaxu_mobile.luanwu:target`` 同源。
    "hero.jiaxu_mobile.luanwu:",
)
"""座位步的**语义分组**：这些步骤里的座位是"被指向的对手"，其余（治疗 / 给牌）是"被帮助的人"。

键 = 执行器发的 ``skill_id`` 前缀（``lijian:victim`` / ``fanjian:seat`` / ``liuli:seat`` …）。
【流离】（票 08 起走 ``PickSeats``）的座位是"把【杀】转嫁给谁"，属被指向的对手——**必须**
登记在这里：不登记就落进"帮助类"，AI 会系统性地把【杀】转给盟友（迁移前是均匀分值的
隐藏槽，至少是随机挑），比随机还差。

**转化技的目标步不在这里手抄**（W4-fix-6，D.1）：它们由**清单来源**派生——询问把
``transform_preactivations`` 的键交给 :func:`_fill_activation`（``conversion=True``），
而清单本身由技能声明渲染。于是第五条转化技自动落进"被指向的对手"分支，不会静默变成
"帮盟友"（旧实现用四条字面 id：``wusheng`` / ``longdan`` / ``guose`` / ``serpent_spear``）。
"""

_STOLEN_CARD_SEAT_STEPS = ("tuxi:",)
"""座位步的第三类：**从对方手里拿走一张牌**（M6 票 01 的【突袭】）。

与"被指向的对手"不同：拿走一张牌对**任何人**都是可得收益（敌人的代价更高、盟友只是
队内转移），故每个非自己座位都给正分、敌人更高。不把 ``Pass`` 当更优——发动了却一张
不拿是纯亏（还放弃了摸牌），所以 ``"tuxi:"`` 不能落进"帮助类"的默认 0 分里。
"""

_STOLEN_CARD_SEAT_VALUE = 1.0
"""【突袭】座位步：非敌人也值一张牌（估算，不做收益模型调参）。"""

_YIJI_GIVE_STEP = "yiji:give:"
"""【遗计】"把这些牌交给谁"的座位步（票 24 / D3）。

与上面两类都不同：**给自己 = no-op**——这批牌是从牌堆顶亮出来、直接交出去的，交给自己
等于没有交出（``_yiji`` 的移动只是把亮牌移进自己的手牌），故记 :data:`_YIJI_SELF_GIVE_VALUE`
而不是帮助类的 ``+2``。其余座位仍按帮助类 ``2·(P(友) − P(敌))``：盟友正、敌人负。
前缀与 `damage_after._yiji` 的询问名 ``yiji:give:<点号>:<轮号>`` 同源（票 24 不另造短 id）。
"""

_YIJI_SELF_GIVE_VALUE = 0.0
"""【遗计】交给自己的估值：0（no-op）；低于任何正分的盟友、高于任何负分的敌人。"""


def _score_seat_option(
    query: Query,
    choice: str,
    *,
    skill_id: str | None = None,
    conversion: bool = False,
) -> float:
    """座位选项的敌我估值：帮助类避开敌人、决斗类瞄准敌人，``self`` 恒是好的帮助对象。

    票 03 起【结姻】/【青囊】/【离间】的座位改由步骤询问；若一律 0 分，AI 会在友敌之间
    掷硬币（候选次序修复后胜率掉到 50% 的正是这个短板）。

    ``skill_id``（W4-4）：预发动填表时答案不在 ``SELECT_OPTION`` 询问上（复合动作一次
    提交），故由调用方把"这一步属于哪条技能"（``<短 id>:<步骤键>``）显式传进来；逐步
    应答仍缺省读询问自带的 ``skill_id``。

    ``conversion``（W4-fix-6，D.1）：这一步来自**转化技清单**（``transform_preactivations``）
    ⇒ 座位是转化牌的目标（【杀】/【乐不思蜀】都指向对手）。判据由**清单来源**派生，
    不手抄技能 id，第五条转化技因此不会再静默落进"帮盟友"分支。

    **敌友按 ``(P(敌), P(友))`` 加权**（I1，票 21）——三类座位步各自是一条线性插值，故已明
    身份的 0/1 与票 21 之前逐字节一致，未知身份才从"中性 0"变成配比后验：
    指向对手 ``2·P(敌)``；偷牌 ``1 + 1·P(敌)``（对任何人都有收益，敌人更高）；帮助类
    ``2·(P(友) − P(敌))``（自己恒 +2，已明敌人 −2）。**不硬判**：P 只做幅度。

    【遗计】的"交给谁"（票 24，D3）是帮助类的一个**例外**：交给自己 = no-op（0 分），
    其余仍按帮助类——见 :data:`_YIJI_GIVE_STEP`。
    """
    view = query.view
    try:
        seat = int(choice.removeprefix(_SEAT_PREFIX))
    except ValueError:  # pragma: no cover - 座位步的选项恒是 seat:N
        return _item_score("不是座位选项", 0.0)
    if not 0 <= seat < len(view.players):
        return _item_score("座位越界", 0.0)
    enemy, ally = _identity_relation(view, seat)
    skill = (
        str(skill_id)
        if skill_id is not None
        else str(getattr(query, "skill_id", "") or "")
    )
    if conversion or skill.startswith(_HOSTILE_SEAT_STEPS):
        return _item_score("敌对座位", _SEAT_TARGET_VALUE * enemy)
    if skill.startswith(_STOLEN_CARD_SEAT_STEPS):
        return _item_score(
            "偷牌座位",
            _STOLEN_CARD_SEAT_VALUE
            + (_SEAT_TARGET_VALUE - _STOLEN_CARD_SEAT_VALUE) * enemy,
        )
    if skill.startswith(_YIJI_GIVE_STEP) and seat == view.seat:
        return _item_score("遗计交给自己", _YIJI_SELF_GIVE_VALUE)
    return _item_score("帮助座位", _SEAT_TARGET_VALUE * (ally - enemy))


def _score_hero_card_option(query: SelectOptionQuery, choice: str) -> float:
    """展示**武将牌**的一次选项询问里，一行选项的分值（票 07；票 02 §4.2 B5）。

    判据只读询问自带面，**不碰数据表 / 技能表**（本模块的可见性硬约束）：

    - ``choice`` 在 :attr:`SelectOptionQuery.disabled` 里 ⇒ :data:`_ILLEGAL_PENALTY`。
      候选里本来就没有置灰项（引擎已剔除），这条是**防御性分支**：真被硬塞进来也绝不选。
    - 在 :attr:`SelectOptionQuery.option_texts` 里有文本 ⇒ **技能自己的效果行**（选项 2
      那种"摸 X 张"的动态文案由引擎算）⇒ :data:`_HERO_CARD_EFFECT_VALUE`。
    - 没有文本 ⇒ **武将牌上的一条技能**（名字 / 描述由投影从数据表派生）⇒
      :data:`_HERO_CARD_SKILL_VALUE`。

    具体技能的选项 id 若在 :data:`_OPTION_VALUES` 里登记过，那条**先**命中（调用方按
    先查表、再回落到本函数），故一将一票可以改自己那几个 id 的相对次序。
    """
    if choice in query.disabled:
        return _item_score("置灰（非法）选项", _ILLEGAL_PENALTY)
    if choice in query.option_texts:
        return _item_score("技能效果行", _HERO_CARD_EFFECT_VALUE)
    return _item_score("武将牌上的一条技能", _HERO_CARD_SKILL_VALUE)


def _score_select_option(query: Query, action: Action) -> float:
    """``SELECT_OPTION``：座位步按敌我估值、其余按特征表贪心；未知选项一律 0。

    「取消一次已发动的技能」（发动后流水线里的 ``Pass``）是例外：给它一个惩罚，
    否则 AI 会在座位步挑「取消」，再回头重新发动同一条技能 —— 空转（见
    :func:`_is_committed_skill_step`）。

    **展示武将牌**的询问（票 07：``hero_card`` 非 None）多一档：两行都是正收益、
    但"获得一个技能"与"技能自己的效果"各有估值（:func:`_score_hero_card_option`）。
    与既有口径同一个优先序：``_OPTION_VALUES`` 的显式登记**先**命中，没登记才回落到
    武将牌的通用估值——一将一票因此不需要改共享打分器。
    """
    assert isinstance(query, SelectOptionQuery)
    if not isinstance(action, SelectOption):
        penalty = _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
        return _item_score("不选（可能是撤销已发动的技能）", penalty)
    if action.choice.startswith(_SEAT_PREFIX):
        return _score_seat_option(query, action.choice)
    registered = _OPTION_VALUES.get(action.choice)
    if registered is not None:
        return _item_score("登记过的选项", registered)
    if query.hero_card is not None:
        return _score_hero_card_option(query, action.choice)
    return _item_score("未登记选项（保守 0 分）", 0.0)


def _option_choice_value(query: Query, choice: str) -> float:
    """多选面里**单个选项**的分值（``_score_select_options`` 与自建集合共用的唯一口径）。

    座位选项（``seat:N``）走 :func:`_score_seat_option` 的敌我估值——【秉壹】这类"令若干
    角色各摸一张"的座位多选面因此知道该帮谁；其余选项查 :data:`_OPTION_VALUES`（技能票
    自己登记；没登记 = 0 分，保守方向）。
    """
    if choice.startswith(_SEAT_PREFIX):
        return _score_seat_option(query, choice)
    return _OPTION_VALUES.get(choice, 0.0)


def _score_select_options(query: Query, action: Action) -> float:
    """``SELECT_OPTIONS``（多选选项；jie-feng-batch2 票 01a）：按**勾中的项逐个**求和。

    与 :func:`_score_select_option` 同一条口径（单选项的估值只有 :func:`_option_choice_value`
    一处），``Pass`` 按"取消已发动的技能"罚分。空集是一条合法候选（= 什么都不做），它的
    分数恒为 0 ⇒ 与"放弃"同价时按候选序取前面的那条，也就是空集——这是**保守**方向
    （不做额外的事），符合多选面的超时兜底。
    """
    assert isinstance(query, SelectOptionsQuery)
    if not isinstance(action, SelectOptions):
        penalty = _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
        return _item_score("不选（可能是撤销已发动的技能）", penalty)
    total = Score()
    # 见 `_score_discard`：``sum`` 的补偿求和与逐项累加在末位会差 1 ulp，parity 要求保留。
    if action.choices:
        total.add(
            "选项",
            sum(_option_choice_value(query, choice) for choice in action.choices),
        )
    else:
        total.add("空集（什么都不做）", 0.0)
    return total.finish()


_DYNAMIC_POOL_SEAT_STEPS: frozenset[str] = frozenset(
    {
        # 【除疠】"势力各不相同的其他角色"：池按**已选**收缩（`skills.active._chuli_targets`）。
        "chuli:seat",
    }
)
"""池**按已选收缩**（除"排除已选"之外还有别的约束）的座位步（键 = ``<短 id>:<步骤键>``，
由 :func:`~sgs.state.step_option_key` 现拼，与 ``SELECT_OPTION`` 的 ``skill_id`` 同形）。

清单 / 不枚举面给的是**首问快照**：引擎的 ``pool(chosen)`` 在选人过程中会变窄（除疠的
势力互异），而 AI 手里只有"第一问能选谁"。故这些步骤 AI 必须自己按**公开事实**再收窄
（势力是公开信息，见 :func:`_distinct_kingdom_options`），否则它会把一条服务端不会铺出来的
集合交上去——引擎按前缀池复核后整步取消（零副作用），人类看起来就是"发了技能什么都没发生"。

**登记表**（`docs/agents/ai-adaptation.md` §4「维护责任表」的那一行）：列在这里的键必须是
**真实存在、且池真的随已选收窄**的座位步——由
``tests/test_ai_adaptation.py::test_every_dynamic_pool_seat_step_is_a_real_contracting_step``
机械核（新技能出现这种池而不登记 ⇒ 上面那句"发了技能什么都没发生"会静默回来）。

与 :data:`_HAND_REQUIRED_SEAT_STEPS` 同一姿态：判据只读公开的 ``PlayerView``，不读暗牌、
不查状态。
"""


def _distinct_kingdom_options(
    view: PlayerView, options: tuple[str, ...]
) -> tuple[str, ...]:
    """**每个势力只留第一个**座位（按选项原序）：动态池座位步的公开事实收窄。

    名单按座位序保留 ⇒ 这份名单的**任意子集**都还能按原序从真池里一步步拼出来（与引擎
    执行器的前缀复核同一条判据）；势力未知（``None``）的座位自成一组，不猜。
    """
    seen: set[object] = set()
    kept: list[str] = []
    for option in options:
        seat = int(str(option).removeprefix(_SEAT_PREFIX))
        kingdom = view.players[seat].kingdom if 0 <= seat < len(view.players) else None
        if kingdom in seen:
            continue
        seen.add(kingdom)
        kept.append(option)
    return tuple(kept)


def _assembled_option_set(query: SelectOptionsQuery) -> SelectOptions:
    """**不枚举档**多选面：AI 自己拼一个合法集合（yijiang-2014-round2 票 21）。

    这一档的 ``legal_actions`` 只有一条**骨架**（``options`` 序的前 ``min_selected`` 个），
    候选里**没有**真正的答案集合 ⇒ AI 不能"挑候选"，得按同一个谓词自己构造：

    - **池** = ``options`` 去掉 ``disabled``（引擎只把可选项当合法成员）；
    - **估值** = 逐项 :func:`_option_choice_value`（座位走敌我 / 帮盟友那一套），降序取
      **正分**项、至多 ``max_selected`` 个——【秉壹】"令至多 N 名角色各摸一张"因此给自己与
      盟友、不资敌；
    - **下限**：不足 ``min_selected`` 时按同一排名补（副作用最小的）项，绝不交一条越界集合；
    - **次序** = 询问 ``options`` 的**原序**（常规结算顺序；用户 2026-11-09 的裁定：默认按
      常规结算顺序，"依次"要有技能明说才另开一档）——打分排名只决定**选谁**，不决定次序。
    - **池按已选收缩的步骤**（票 13 的【除疠】）先按 :func:`_distinct_kingdom_options` 收窄：
      询问只带**首问**那一份池，真正的约束（势力互异）不在 ``options`` 里。

    池与上限读引擎的**同一份派生**（``sgs.effects.usable_options`` / ``cap_of``，谓词的另一
    半）：构造器的 ``cap`` / ``floor`` 因此天然落在 ``is_legal_option_set`` 的区间内——构造器
    不该产出非法集合，越界即打分 / 构造的编程错误（这里**不**再拿 assert 兜一遍：``-O`` 下
    断言会被剥掉，兜不住任何东西）。上限的第二次夹取**只**发生在收窄过的那一档（见下）。
    """
    options = usable_options(query)
    cap = cap_of(query)
    if str(query.skill_id) in _DYNAMIC_POOL_SEAT_STEPS:
        options = _distinct_kingdom_options(query.view, options)
        # 上限跟着**收窄后**的池：``cap_of`` 已按"可选项数"封顶，但那一份是收窄**前**的
        # 池 ⇒ 这里再按实际构造的池夹一次（只有这一档会真的变窄；别的询问上它是恒等，
        # 故不把全局口径改掉——票 13 的裁定只覆盖"池按已选收缩"的步骤）。
        cap = min(cap, len(options))
    ranked = sorted(
        (-_option_choice_value(query, option), index, option)
        for index, option in enumerate(options)
    )
    picked = [option for negated, _index, option in ranked if negated < 0][:cap]
    floor = min(int(query.min_selected), cap)
    if len(picked) < floor:
        chosen = set(picked)
        for _negated, _index, option in ranked:
            if len(picked) >= floor:
                break
            if option not in chosen:
                picked.append(option)
                chosen.add(option)
    chosen = set(picked)
    choices = tuple(option for option in options if option in chosen)
    return SelectOptions(choices=choices, answers_seq=query.seq)


# ---------------------------------------------------------------------------
# 预发动填表（W4-4）：读清单、复用现有打分器填一个复合动作
# ---------------------------------------------------------------------------


def _visible_cards(view: PlayerView) -> dict[int, tuple[CardRef, int]]:
    """本视角里**看得到牌面**的实体牌 → ``(牌面, 归属座位)``。

    自己的手牌 ＋ 全部座位的装备 / 判定区（后两者是公开信息，票 08 §6）。清单下发的
    选牌候选只可能落在这些牌上——他人手牌那一步在清单里**退化**（不下发候选），故这里
    也不会读到隐藏牌面。
    """
    out: dict[int, tuple[CardRef, int]] = {
        card.card_id: (card, view.seat) for card in view.self_hand
    }
    for player in view.players:
        for card in (*player.equip, *player.judge):
            out.setdefault(card.card_id, (card, player.seat))
    return out


def _pool_row_for(step, prior: tuple[str, ...]):
    """找出与前序答案文本匹配的那一行子池；``when=()`` 行 = 池与前序无关。

    ``step`` 是 ``sgs.state.Step``（清单的一步）。本模块的可见面白名单**不许 import**
    它的类型（``Step`` / ``StepPool`` 不在 ``HEURISTIC_IMPORT_ALLOWLIST`` 里），故这里
    按询问自带的形状鸭子类型读——数据本身由询问下发，不是全知面。
    """
    rows = step.pool
    if not rows:
        return None
    for row in rows:
        if tuple(row.when) == prior:
            return row
    for row in rows:
        if not row.when:
            return row
    return None


def _model_card_picks(
    entries: list[tuple[int, float]],
    step,
    short: str,
    view: PlayerView,
) -> tuple[int, ...] | None:
    """按 :func:`_step_card_score` 的口径选出本步的实体 id（**同一个**打分模型）。

    打分器对一组 picks 的分数 = 各张单张分值之和（【仁德】另给 ≥2 张的回复奖励），
    故"最优子集"= 按单张分值降序取前 ``count`` 张，再在 ``min..max`` 内取总和最高的
    ``count``；同分取更小的 ``count``（多加一张零分牌不改变模型的选择）。
    """
    lower = max(0, step.min)
    upper = len(entries) if step.max is None else min(step.max, len(entries))
    if len(entries) < lower or upper < lower:
        return None
    ordered = sorted(entries, key=lambda item: (-item[1], item[0]))
    give = short in _GIVE_STEPS
    best_score = 0.0
    best_count = lower
    for count in range(lower, upper + 1):
        score = sum(value for _card_id, value in ordered[:count])
        if give and count >= 2 and _wounded(view):
            score += _RENDE_RECOVER_VALUE
        if count == lower or score > best_score:
            best_score = score
            best_count = count
    return tuple(card_id for card_id, _value in ordered[:best_count])


def _pick_card_ids(
    step,
    options: tuple[str, ...],
    cards: Mapping[int, tuple[CardRef, int]],
    view: PlayerView,
    short: str,
) -> tuple[int, ...] | None:
    """把**模型选出的实体 id** 映回清单的选项文本（``None`` = 填不出来）。

    "选哪几张"不再是这里另写的一套：单张口径读 :func:`_step_card_score`，
    子集选择读 :func:`_model_card_picks`——与 ``_score_select_cards`` 逐字同一模型
    （W4 评审处置 D.7；原先按技能分支手写的三档已删除）。
    """
    entries: list[tuple[int, float]] = []
    for text in options:
        try:
            card_id = int(text)
        except (TypeError, ValueError):
            return None
        card, owner = cards.get(card_id, (None, view.seat))
        entries.append(
            (card_id, _step_card_score(card, owner, view.seat, short, view=view))
        )
    if not entries:
        return None
    return _model_card_picks(entries, step, short, view)


def _pick_options(
    step,
    options: tuple[str, ...],
    query: Query,
    short: str,
    *,
    conversion: bool = False,
) -> tuple[str, ...] | None:
    """座位 / 选项步：取 :func:`_score_seat_option` 最高（座位）或特征表最高（选项）。

    **多选座位步**（W7-01b：`step.max` 是整数且 `> step.min`，如转化【杀】的 `1..max`
    目标步）按 ``min(step.max, len(options))`` 填满；**首项必取**——多目标清单的首项是
    候选排序的自然结果（借刀窗口就是 Bn），不选它拼出的组合必然被引擎拒绝；其余按
    :func:`_score_seat_option` 降序补（同分保持清单顺序，与既有单选的 tie-break 一致）。
    ``max is None``、``min == max`` 的既有步与**非座位步**（选项步）一律走旧填法，
    故既有 47 份金录像的动作序列不变。
    """
    seat_options = bool(options) and all(
        text.startswith(_SEAT_PREFIX) for text in options
    )
    if (
        seat_options
        and isinstance(step.max, int)
        and step.max > step.min
    ):
        wanted = min(step.max, len(options))
        if wanted < step.min:
            return None
        first = options[0]
        rest = sorted(
            options[1:],
            key=lambda text: -_score_seat_option(
                query,
                text,
                skill_id=f"{short}:{step.name}",
                conversion=conversion,
            ),
        )
        return (first, *rest[: max(0, wanted - 1)])
    remaining = list(options)
    wanted = max(step.min, 1) if step.min else 1
    if step.max is not None:
        wanted = min(wanted, step.max)
    wanted = min(wanted, len(remaining))
    if wanted < step.min:
        return None
    chosen: list[str] = []
    for _ in range(wanted):
        best: str | None = None
        best_score = 0.0
        for text in remaining:
            score = (
                _score_seat_option(
                    query,
                    text,
                    skill_id=f"{short}:{step.name}",
                    conversion=conversion,
                )
                if text.startswith(_SEAT_PREFIX)
                else _OPTION_VALUES.get(text, 0.0)
            )
            if best is None or score > best_score:
                best, best_score = text, score
        assert best is not None
        remaining.remove(best)
        chosen.append(best)
    return tuple(chosen)


def _conversion_choices_within_pool(
    query: Query,
    picks: Sequence[int],
    choices: Sequence[str],
    *,
    step: object,
    options: Sequence[str],
) -> tuple[str, ...] | None:
    """把转化清单填出的目标集收进**内部候选池**（W7-02 发现 / 01b 修复）。

    一个转化技的**来源组可以有不同目标数上限**：方天画戟只对"最后一张手牌"的转化源 +2，
    装备区来源（武圣把已装备的红牌当【杀】）不追加——而清单目标步的 ``max`` 是**全局**
    最大值。按全局 ``max`` 填满、却用了上限更低的来源，拼出的组合就不在候选池里：引擎的
    确认会拒绝它，而 AI 没有重试的机会（那会是一条非法应答）。故提交前做一次**只读的池
    成员校验**：

    1. 命中内部池（``query.transform_actions``，与清单同源的候选）⇒ 原样返回；
    2. 否则把目标集缩到该来源在池里**真实存在的最多张**（保留清单首项 / 借刀的 Bn，且不
       少于 ``step.min``）；
    3. 池里没有可用组合 ⇒ ``None``（不交半成品，回退到候选 / 骨架路径）。

    它只比"在不在池里"，**不重算任何规则**；上限的真源只能是候选池本身（清单的逐来源
    ``pool`` 行只表达"该来源能选哪些目标"，协议里没有"每个来源最多几张"这一格）。判定
    与 :func:`sgs.agents.participants.is_transform_use` 同姿态：成员校验读的是询问自带的
    公开候选面，引擎的确认仍是最终裁判。

    **清单没有目标步时**（huo-mobile-batch 票 07：目标由牌本身决定，如【乱击】的
    【万箭齐发】）没有目标文本要收，本函数不参与——那一形状的池成员校验是"来源组在不在
    池里"，由 :func:`_conversion_sources_within_pool` 回答。
    """
    pool = tuple(getattr(query, "transform_actions", ()) or ())
    if not pool:
        return tuple(choices)
    seat_choices = tuple(str(text) for text in choices if str(text).startswith(_SEAT_PREFIX))
    if not seat_choices:
        return tuple(choices)
    wanted_sources = tuple(sorted(int(card_id) for card_id in picks))
    prefix = tuple(choices[: len(choices) - len(seat_choices)])

    def targets_of(action: object) -> tuple[int, ...]:
        return tuple(int(target) for target in (getattr(action, "targets", None) or ()))

    def sources_of(action: object) -> tuple[int, ...]:
        card = getattr(action, "card", None)
        return tuple(
            sorted(int(source) for source in (getattr(card, "virtual_sources", None) or ()))
        )

    def matches(chosen: Sequence[str], targets: Sequence[int]) -> bool:
        # 与确认解析器**同一个**比较（D5）：重复选择被拒、与点击顺序无关。
        return target_sets_match(
            (int(text.removeprefix(_SEAT_PREFIX)) for text in chosen), targets
        )

    available = [
        targets_of(action) for action in pool if sources_of(action) == wanted_sources
    ]
    if not available:
        return None
    if any(matches(seat_choices, targets) for targets in available):
        return tuple(choices)

    order = {str(text): index for index, text in enumerate(options)}
    shrinkable = [
        targets
        for targets in available
        if step.min <= len(targets) <= len(seat_choices)
        and all(f"{_SEAT_PREFIX}{target}" in order for target in targets)
    ]
    shrinkable.sort(
        key=lambda targets: (
            -len(targets),
            min(order[f"{_SEAT_PREFIX}{target}"] for target in targets),
        )
    )
    for targets in shrinkable:
        shrunk = tuple(
            text
            for text in options
            if int(str(text).removeprefix(_SEAT_PREFIX)) in set(targets)
        )
        if matches(shrunk, targets):
            return (*prefix, *shrunk)
    return None


def _conversion_sources_within_pool(
    query: Query,
    picks: Sequence[int],
    choices: Sequence[str],
    *,
    step: object,
    options: Sequence[str],
    cards: Mapping[int, tuple[CardRef, int]],
    short: str,
    view: PlayerView,
) -> tuple[int, ...] | None:
    """把转化清单填出的**多源来源组**收进内部候选池（huo-mobile-batch 票 03 / 票 07）。

    【乱击】的"两张花色相同"是**配对谓词**：清单来源步只列"至少有同花色搭档"的牌，但任意
    两张开得出来的牌都点得动——按牌值挑出的两张可能不同花色，拼出的组合就不在候选池里
    （引擎的确认会拒绝，AI 没有重试机会 = 一条非法应答）。故与
    :func:`_conversion_choices_within_pool` 同姿态做一次**只读的池成员校验**：

    1. 当前（来源组, 目标集）在池里 ⇒ 原样返回；
    2. 否则在**同一条来源步的选项**里，按同一打分模型（:func:`_step_card_score`）挑一个
       "池里存在、且目标集也对得上"的来源组；
    3. 一个都凑不出 ⇒ ``None``（不交半成品）。

    **清单没有目标步时**（huo-mobile-batch 票 07：目标由牌本身决定，确认只提交来源——
    如【乱击】的【万箭齐发】）没有目标集可比：池里的来源组**就是**判据，同一组来源的
    目标集由池唯一给出。判据因此退化成"来源组在不在池里"——两张不同花色的牌照样被换掉。

    判据仍是询问自带的公开候选面（``transform_actions``），引擎的确认仍是最终裁判。
    """
    pool = tuple(getattr(query, "transform_actions", ()) or ())
    if not pool:
        return tuple(picks)
    wanted = tuple(sorted(int(card_id) for card_id in picks))
    targets = tuple(
        int(str(text).removeprefix(_SEAT_PREFIX))
        for text in choices
        if str(text).startswith(_SEAT_PREFIX)
    )

    def sources_of(action: object) -> tuple[int, ...]:
        card = getattr(action, "card", None)
        return tuple(
            sorted(int(source) for source in (getattr(card, "virtual_sources", None) or ()))
        )

    def targets_of(action: object) -> tuple[int, ...]:
        return tuple(int(target) for target in (getattr(action, "targets", None) or ()))

    groups = {
        sources_of(action)
        for action in pool
        if sources_of(action)
        and len(sources_of(action)) == len(wanted)
        # 无目标步（``targets`` 为空）⇒ 不比目标：池里的来源组自己就是判据。
        and (not targets or target_sets_match(targets, targets_of(action)))
    }
    if not groups:
        return tuple(picks)
    if wanted in groups:
        return tuple(picks)
    option_ids = set()
    for text in options:
        try:
            option_ids.add(int(text))
        except (TypeError, ValueError):
            return None
    allowed = sorted(group for group in groups if set(group) <= option_ids)
    if not allowed:
        return None
    scored: dict[int, float] = {}
    for card_id in option_ids:
        card, owner = cards.get(card_id, (None, view.seat))
        scored[card_id] = _step_card_score(card, owner, view.seat, short, view=view)
    best = max(allowed, key=lambda group: (sum(scored[cid] for cid in group), tuple(-cid for cid in group)))
    return best


def _fill_activation(
    query: Query,
    skill_id: str,
    steps: tuple[object, ...],
    cards: Mapping[int, tuple[CardRef, int]],
    *,
    conversion: bool = False,
) -> ActivateSkill | None:
    """按清单填一个复合动作；任何一步填不出来 ⇒ ``None``（不提交半成品）。

    ``conversion``（W4-fix-6，D.1）：这份清单来自 ``transform_preactivations`` ⇒ 座位步的
    目标是转化牌指向的对手，由 :func:`_score_seat_option` 的对敌分支估值。

    **多选目标步 + 逐来源上限**（W7-02）：``conversion`` 清单里出现 ``max > min`` 的座位步
    时，填出的目标集还要过 :func:`_conversion_choices_within_pool`——来源组上限不同（方天
    只放宽"最后一张手牌"的来源）时，按全局 ``max`` 填满可能越界；越界即缩到池里真实的
    最多张，仍填不出 ⇒ ``None``。

    **置灰项不填**（票 04 §3）：``StepPool.disabled`` 里的选项在闭集里但**此刻不可选**，
    服务端会拒收（`flow._assign_activation`），故这里先按它收窄候选；全部被置灰 ⇒ ``None``
    （这一步填不出来，不交半成品）。

    **没有目标步的转化清单**（huo-mobile-batch 票 07）：目标由牌本身决定（【乱击】的
    【万箭齐发】= 全体其他存活），清单只有来源步，``choices`` 因此为空；多源转化填出的
    来源组仍要过 :func:`_conversion_sources_within_pool` 的池成员校验（那一形状下判据退化成
    "来源组在不在池里"）。
    """
    picks: list[int] = []
    choices: list[str] = []
    prior: list[str] = []
    short = str(skill_id).rsplit(".", 1)[-1]
    multi_target: tuple[object, tuple[str, ...]] | None = None
    source_step: object | None = None
    source_options: tuple[str, ...] = ()
    for step in sorted(steps, key=lambda item: item.order):
        row = _pool_row_for(step, tuple(prior))
        if row is None:
            return None
        options = tuple(row.options)
        if not options:
            return None
        # 置灰项（票 04 §3）**不是候选**：它们在闭集里只为"列出来给玩家看"，服务端照样
        # 拒收。AI 填表必须按同一份口径收窄——否则它会把一个"此刻用不了"的名字写进复合
        # 动作，被引擎拒成非法应答（`test_all_ai_games.py` 的"零非法应答"当场红）。
        blocked = frozenset(row.disabled)
        if blocked:
            options = tuple(text for text in options if text not in blocked)
            if not options:
                return None
        if all(text.startswith(_SEAT_PREFIX) for text in options):
            if (str(skill_id), str(step.name)) in _HAND_REQUIRED_SEAT_STEPS:
                # 只用**公开**的手牌数筛：空手目标会在展示 / 选牌步整步取消，AI 反复选它
                # 就是空操作死循环（见 :data:`_HAND_REQUIRED_SEAT_STEPS`）。
                options = tuple(
                    text
                    for text in options
                    if query.view.players[int(text.removeprefix(_SEAT_PREFIX))].hand_count > 0
                )
                if not options:
                    return None
            if step_option_key(skill_id, step.name) in _DYNAMIC_POOL_SEAT_STEPS:
                # 清单只下发**首问**快照（除疠的势力互异在 `pool(chosen)` 里）⇒ 按公开的
                # 势力收窄，否则复合动作会被引擎的前缀复核拒成非法应答。
                options = _distinct_kingdom_options(query.view, options)
                if not options:
                    return None
            chosen = _pick_options(
                step, options, query, short, conversion=conversion
            )
            if chosen is None:
                return None
            if isinstance(step.max, int) and step.max > step.min:
                multi_target = (step, options)
            choices.extend(chosen)
            prior.extend(chosen)
        elif all(text.isdigit() for text in options):
            picked = _pick_card_ids(step, options, cards, query.view, short)
            if picked is None:
                return None
            if conversion and len(picked) > 1:
                source_step, source_options = step, options
            picks.extend(picked)
            prior.extend(str(card_id) for card_id in picked)
        else:
            chosen = _pick_options(
                step, options, query, short, conversion=conversion
            )
            if chosen is None:
                return None
            choices.extend(chosen)
            prior.extend(chosen)
    if conversion and multi_target is not None:
        step, options = multi_target
        shrunk = _conversion_choices_within_pool(
            query, picks, choices, step=step, options=options
        )
        if shrunk is None:
            return None
        choices = list(shrunk)
    if conversion and source_step is not None:
        fixed = _conversion_sources_within_pool(
            query,
            picks,
            choices,
            step=source_step,
            options=source_options,
            cards=cards,
            short=short,
            view=query.view,
        )
        if fixed is None:
            return None
        picks = list(fixed)
    return ActivateSkill(
        skill_id=skill_id,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def _preactivation_candidates(query: Query) -> tuple[ActivateSkill, ...]:
    """本次询问可填的全部复合动作（主动读 ``preactivations``、被动读 ``preactivation``）。

    W4-fix-2：**转化技也在这里**——它们的候选已从 ``legal_actions`` 收回（用户裁定 A），
    清单住 ``transform_preactivations``，确认同样是 ``ActivateSkill(来源, 目标)``，由引擎
    按同一份内部候选池解析成普通使用 / 打出。四类语境（出牌 / 【闪】/ 响应 / 求桃）都读它。

    W4-fix-6（D.1）：转化清单的**来源**也是"这些座位步指向对手"的声明——填表时把它传给
    :func:`_fill_activation`（``conversion=True``），不再从技能 id 字面表判断。
    """
    kind = QueryKind(query.kind)
    transforms: dict[str, object] = {}
    if kind is QueryKind.PLAY_PHASE:
        manifests = dict(getattr(query, "preactivations", None) or {})
        transforms = dict(getattr(query, "transform_preactivations", None) or {})
        manifests.update(transforms)
    elif kind is QueryKind.SKILL_ACTIVATION:
        skill_id = getattr(query, "skill_id", None)
        steps = tuple(getattr(query, "preactivation", None) or ())
        # 票 01：一条面可以有多条候选，逐候选的清单住 ``activation_preactivations``；
        # 单候选的旧面两个字段同源（``preactivation`` 兜底），故填表结果逐字不变。
        manifests = dict(getattr(query, "activation_preactivations", None) or {})
        if skill_id and steps:
            manifests.setdefault(str(skill_id), steps)
    elif kind in (QueryKind.DODGE, QueryKind.RESPOND, QueryKind.PEACH):
        transforms = dict(getattr(query, "transform_preactivations", None) or {})
        manifests = transforms
    else:
        return ()
    if not manifests:
        return ()
    cards = _visible_cards(query.view)
    conversion_skills = frozenset(transforms)
    return tuple(
        action
        for skill_id in sorted(manifests)
        if (
            action := _fill_activation(
                query,
                skill_id,
                manifests[skill_id],
                cards,
                conversion=skill_id in conversion_skills,
            )
        )
        is not None
    )


def _pick_designate(query: Query, options: tuple[str, ...]) -> str | None:
    """借刀的 Bn：**已明敌人优先，其次体力最低**；同分取座位号小的（确定性）。

    只读 ``query.view`` 的公开面（体力 / 已明身份），不猜隐藏身份。排序键的前半段复用
    :func:`_score_seat_option` 的``conversion``口径（敌我估值），避免第二份"谁是敌人"的
    判据；后半段再按体力 / 座位号决胜。
    """
    view = query.view

    def rank(text: str) -> tuple[float, int, int]:
        seat = int(str(text).removeprefix(_SEAT_PREFIX))
        player = view.players[seat]
        target_value = _score_seat_option(
            query, text, skill_id="borrowed_sword:designate", conversion=True
        )
        return (target_value, -player.hp, -seat)

    usable = [text for text in options if str(text).startswith(_SEAT_PREFIX)]
    if not usable:
        return None
    return max(usable, key=rank)


def _fill_card_manifest(query: Query, steps) -> tuple[int, int] | None:
    """按**牌名清单**填外层两步：持刀者 A（``_score_seat_option``）→ Bn（敌人 / 低体力）。

    清单形状由服务端下发（``card_preactivations[card.type]``，两步 ``seat``）；这一步
    **不重算**合法性——A / Bn 都是服务端给出的候选，AI 只从中挑。任何一步填不出来 ⇒
    ``None``（不交半成品）。
    """
    ordered = sorted(steps, key=lambda item: item.order)
    if len(ordered) != 2:
        return None
    holder_row = _pool_row_for(ordered[0], ())
    if holder_row is None or not holder_row.options:
        return None
    holder = _pick_options(
        ordered[0], tuple(holder_row.options), query, "borrowed_sword", conversion=True
    )
    if not holder:
        return None
    designate_row = _pool_row_for(ordered[1], tuple(holder))
    if designate_row is None or not designate_row.options:
        return None
    designate = _pick_designate(query, tuple(str(text) for text in designate_row.options))
    if designate is None:
        return None
    return (
        int(str(holder[0]).removeprefix(_SEAT_PREFIX)),
        int(designate.removeprefix(_SEAT_PREFIX)),
    )


def _card_preactivation_candidates(query: Query) -> tuple[UseCard, ...]:
    """本次出牌阶段可填的**附带指定**使用（W5-02b 借刀）。

    键 = ``card.type``：手牌里每张同型牌 × 按清单填出的 ``(A, Bn)`` 各造一条
    ``UseCard(card, targets=(A,), designations=(Bn,))``。提交后由引擎的第四条路按
    **同一份附带指定池**校验（AI 不另写合法性）；持刀者窗口的 a / b 由它自己那一步
    应答，不在这里。
    """
    if QueryKind(query.kind) is not QueryKind.PLAY_PHASE:
        return ()
    manifests = dict(getattr(query, "card_preactivations", None) or {})
    if not manifests:
        return ()
    by_type: dict[str, list[CardRef]] = {}
    for card in query.view.self_hand:
        by_type.setdefault(card.type, []).append(card)
    out: list[UseCard] = []
    for card_type in sorted(manifests):
        filled = _fill_card_manifest(query, manifests[card_type])
        if filled is None:
            continue
        holder, designate = filled
        for card in by_type.get(card_type, ()):
            out.append(
                UseCard(
                    card=card,
                    targets=(holder,),
                    designations=(designate,),
                    answers_seq=query.seq,
                )
            )
    return tuple(out)


def _score_arrange(query: Query, action: Action) -> float:
    """``ARRANGE``：最有用的牌留顶（按牌名价值表；越靠顶权重越高）。"""
    assert isinstance(query, ArrangeQuery)
    if not isinstance(action, ArrangeCards):
        return _item_score("不是排版动作", 0.0)
    order = tuple(action.order)
    if sorted(order) != list(range(len(query.revealed))):
        return _item_score("非法排版顺序", _ILLEGAL_PENALTY)
    if not 0 <= action.split <= len(order):
        return _item_score("非法分界", _ILLEGAL_PENALTY)
    size = len(order)
    total = Score()
    # 同上：保留 ``sum``（补偿求和）才能逐 float 不变。
    total.add(
        "置顶牌的用处",
        sum(
            _card_value(query.revealed[order[index]]) * (size - index)
            for index in range(action.split)
        ),
    )
    return total.finish()


def _score_distribute(query: Query, action: Action) -> float:
    """``DISTRIBUTE``：计数式分配——点数给敌人值钱、给自己 / 盟友是负收益。

    与座位步同一套 ``(P(敌), P(友))`` 加权（I1）：每点按 :data:`_SEAT_TARGET_VALUE` 折算，
    指向敌人的点为正、指向自己或盟友的点为负。全 0 的 ``counts=()`` 因此恰好 0 分——
    "不分配"是一个中性落点，不是被惩罚的非法动作（它在候选里、也是超时兜底）。
    """
    assert isinstance(query, DistributeQuery)
    if not isinstance(action, Distribute):
        return _item_score("不是分配动作", _ILLEGAL_PENALTY)
    view = query.view
    total = Score()
    if not action.counts:
        total.add("不分配（中性落点）", 0.0)
    for seat, count in action.counts:
        if not 0 <= seat < len(view.players) or count <= 0:
            return _item_score("非法座位或张数", _ILLEGAL_PENALTY)
        if count > query.per_seat_max:
            return _item_score("超过每座上限", _ILLEGAL_PENALTY)
        enemy, ally = _identity_relation(view, seat)
        total.add("分配点数", count * _SEAT_TARGET_VALUE * (enemy - ally))
    if sum(count for _, count in action.counts) > query.max_total:
        return _item_score("超过总上限", _ILLEGAL_PENALTY)
    return total.finish()


_SCORERS: Mapping[QueryKind, Callable[[Query, Action], float]] = MappingProxyType(
    {
        QueryKind.PLAY_PHASE: _score_play_phase,
        QueryKind.DODGE: _score_dodge,
        QueryKind.RESPOND: _score_respond,
        QueryKind.PEACH: _score_peach,
        QueryKind.DISCARD: _score_discard,
        QueryKind.SKILL_ACTIVATION: _score_skill_activation,
        QueryKind.SELECT_CARDS: _score_select_cards,
        QueryKind.SELECT_OPTION: _score_select_option,
        QueryKind.SELECT_OPTIONS: _score_select_options,
        QueryKind.ARRANGE: _score_arrange,
        QueryKind.DISTRIBUTE: _score_distribute,
    }
)
"""选路表：**十类询问各一个 scorer**（键集恰等于 ``QueryKind``，由测试钉住）。"""


# ---------------------------------------------------------------------------
# AI 适配契约（票 17 / spec §4.5）：读点表
# ---------------------------------------------------------------------------

_AI_READ_POINTS: Mapping[str, Mapping[str, tuple[bool, str]]] = MappingProxyType(
    {
        "PlayerView": MappingProxyType(
            {
                "seat": (True, "自己的座位：判'是否自己'与座位距离"),
                "players": (True, "逐座位的公开面（体力 / 手牌数 / 装备 / 判定 / 横置 / 酒 / 已明身份）"),
                "self_hand": (
                    True,
                    "自己的手牌牌面：成本、响应与转化源都从这里挑；记牌也数它——"
                    "_visible_card_faces 把它算进'已出现的牌'；【贯石斧】的成本牌价与"
                    "【谦逊】的续命牌判据（1 体力时手里有没有【闪】/【桃】/【酒】）也读它"
                    "（票 22）",
                ),
                "hero_zone_cards": (
                    True,
                    "本人武将牌上的牌面（票 27）：_visible_card_faces 把它算进'已出现的牌'，"
                    "守恒量因此完整、记牌在【谦逊】放置期间仍可用；他人区不在本字段里"
                    "（张数走 PlayerPublic.hero_zone_count）",
                ),
                "deck_count": (
                    True,
                    "记牌守恒量的一项（C1）：_conserved_card_count 与四区 / 手牌数相加后由"
                    "_verified_deck_profile 反推牌堆档位；C2（票 23）起未知池的**逐牌名剩余"
                    "张数**同时喂四条判据——P(【闪】) / P(【无懈可击】) / P(【桃】) 与"
                    "未知牌期望（_has_card_probability / _unknown_card_expectation）。"
                    "只有张数、没有顺序",
                ),
                "discard": (
                    True,
                    "记牌的**可见牌面**（C1）：_visible_card_faces 把弃牌堆的牌算进'已出现'，"
                    "_unknown_pool 从档位总牌面里扣（C2 起扣出来的是**逐牌名**剩余张数，"
                    "三条概率与未知牌期望共用）；顺序对现有打分无影响",
                ),
                "processing": (
                    True,
                    "处理区顶牌 = 本次结算的牌：【无懈可击】按它对目标的利 / 害决定宣不宣"
                    "（D1），顶牌是无懈即嵌套层；【火攻】也靠它给'弃一张换 1 点伤害'的价；"
                    "记牌也数它（_visible_card_faces 的可见牌面，C1）",
                ),
                "processing_concealed": (
                    False,
                    "ai-view 票 12 的处理区扣置牌（交换 / 蛊惑）——本票**不改打分**："
                    "`processing` 仍是全量（区域事实 / 守恒量 / 顶牌判据一字未动），这一格"
                    "只给网页展示画牌背。要把扣置牌从定价 / 记牌里摘出去（它算不算'已出现'）"
                    "是另票的定价问题；在那之前读它就是悄悄的读法变更",
                ),
                "public_counts": (
                    True,
                    "ai-view 票 07 的**长期证据**读它（identity.pressure_observations）："
                    "聚合里的 move:LOSE / move:DISCARD 是**全局长**的拿牌 / 弃牌压力"
                    "（拆 / 顺一类，对目标有害的方向），补上窗口只有 32 条事实时"
                    "早年 / 平静期读不到的样本；伤害 / 回复仍因无符号不进这张表",
                ),
                "public_history": (
                    True,
                    "ai-view 票 07 的**行为推断**读它（唯一读方）：identity.behavior_observations "
                    "把公开事实摊成行为类 × 先手方，喂精确联合后验；只读 actor / targets / "
                    "fact_class / window / 公开载荷，不读隐藏手牌或未亮明身份",
                ),
                "turn_seat": (False, "回合归属不参与打分：没有'我的回合'加权"),
                "phase": (False, "阶段不参与打分：是不是出牌阶段由询问类型本身表达"),
            }
        ),
        "PlayerPublic": MappingProxyType(
            {
                "seat": (True, "座位号：目标 / 敌友判定与座位步排序"),
                "hp": (
                    True,
                    "濒死与斩杀线：伤害够不够、要不要救、内奸估分；【据守】的 1 体力风险"
                    "（正面发动要跳过下个回合）与【谦逊】的续命判据也读它（票 22）",
                ),
                "hp_max": (True, "``hp < hp_max`` 判'已受伤'（【仁德】回体力的额外价值）"),
                "armor": (
                    True,
                    "护甲是第二条体力轴（W7）：``_lethal_to`` 的'这一下打不打得死'读 "
                    "``hp + armor``（有护甲的人得多挨几下）；``_wounded`` 仍只看 "
                    "``hp < hp_max``——护甲不让任何人看起来受伤，所以【仁德】类判据不读它",
                ),
                "hand_count": (
                    True,
                    "公开手牌数：【古锭刀】加伤、防守成本、够不够弃；记牌里它是"
                    "_has_card_probability 的抽样张数（P(【闪】) / P(【无懈】) / P(【桃】)，"
                    "票 20 / 23）——只给张数、不给牌面",
                ),
                "hero_zone_count": (
                    True,
                    "武将牌上的公开张数（票 27）：_conserved_card_count 按座位求和，是记牌"
                    "守恒量的一项；本人区里的牌面另由 view.hero_zone_cards 进可见面",
                ),
                "alive": (
                    True,
                    "只对存活座位出手（不把伤害算给已阵亡者）；也是身份后验的**分母**——"
                    "_unknown_identity_remainder 只把存活座位算进未知席位，阵亡却身份不可见时"
                    "直接 fail-closed（票 21）；全局存活人数另有两个读方（_alive_count）："
                    "内奸局面读数的分母 D = 存活人数 − 1（票 32）与内奸护主硬 veto 的阈值"
                    "『还有别人活着』（存活人数 > 2，`ai-view` 票 11）",
                ),
                "equip": (True, "装备区牌面：防具 / 武器 / 白银狮子的实际伤害；也进 ``_visible_cards``"),
                "judge": (True, "判定区是公开信息，进 ``_visible_cards``（选牌步的可见面）"),
                "sealed_zones": (
                    False,
                    "lei 票 05：被废除的槽不参与 v1 打分——目标合法性 / 装备可用已由引擎"
                    "（``check_targets`` / ``put_equipment_card``）裁掉，AI 拿到的候选里没有"
                    "无效动作；lukang-mobile 票 03 起值域是 7 个槽 id（不是 judge / equip 整区名）；"
                    "将来要给'废掉对方装备区'估值时再翻转这一行",
                ),
                "chained": (True, "【铁索连环】打分读横置状态（``exclude=True``，但询问自带）"),
                "flipped": (
                    True,
                    "【据守】读它判'这一次翻面是翻回正面还是跳过下个回合'（票 22）：已翻面 ⇒"
                    " 三张牌纯赚、恒发动；正面才要付'跳过下个回合'（1 体力时贵过收益）。"
                    "其余打分分支仍不读它",
                ),
                "wine_active": (True, "【酒】：下一张【杀】+1 的实际伤害"),
                "markers": (
                    False,
                    "票 06：标记（「梦魇」等）不参与 v1 打分——武魂②的座位候选已由服务端"
                    "裁到'来源为自身、标记最多'的那几名，AI 只按敌我选；标记也不是牌，"
                    "不进记牌守恒量。将来要按标记做威胁估值时再翻转这一行",
                ),
                "identity": (
                    True,
                    "敌友的唯一信号（I1，票 21）：本人 / 主公 / 已阵亡可见，_identity_relation "
                    "把已亮明的身份从配比字面表里减掉，存活未知座位按人头均分得 "
                    "(P(敌), P(友))——伤害目标 / 座位步 / 连环 / 火攻 / 无懈 / 濒死救援都读它；"
                    "`ai-view` 票 01 起多一个**对称**读法：`_relation_term` 把它折成 "
                    "`_RELATION_FOCUS × (P敌 − P友)`，当'打这个目标值多少'；主公那一格再由 "
                    "_lord_veto_applies 在出牌阶段摘掉；"
                    "（救援一侧：C2 起 _rescue_probability 用它算「谁愿意救濒死者」）；"
                    "算不出就退回中性的 (0, 0)，不做行为 / 历史推断。**PvE 口径（M7 票 08）**："
                    "PvE 开局全部 identity_visible ⇒ 这个字段对全桌公开（友 / 敌），"
                    "_identity_relation 走已亮明的 0/1 档直接读出，配比后验不参与；"
                    "读点与字段都没变，只有读法多认两个值",
                ),
                "hero": (False, "武将 id 不参与打分：技能语义走 ``SKILL_VALUES``，不按武将近路"),
                "kingdom": (False, "势力不参与打分：敌友只按 identity，不看势力同异"),
            }
        ),
        # 每个询问子类都有的公共面（``_QueryBase`` 的字段 + 各自声明的 ``kind``）：
        # 同一份语义只登记一次，否则九类各抄一遍必然漂移。
        "Query": MappingProxyType(
            {
                "seq": (True, "填 ``answers_seq``：应答与询问的配对凭据（陈旧应答由引擎判）"),
                "actor": (False, "应答者恒是 ``view.seat``（AI 只替自己答）；再读一份会多出第二个'我是谁'真源"),
                "timing": (True, "判'出牌阶段空闲时机'：可取消的步骤 = 撤销一次已经发动的技能"),
                "prompt": (False, "人话文案，只给界面 / 人读"),
                "legal_actions": (True, "候选枚举的唯一来源（骨架候选，转化 / 预发动另有清单）"),
                "view": (True, "全部读点的入口：本座位视角"),
                "revealed": (True, "``ARRANGE`` 按授予的牌面把最有用的那张排在顶"),
                "transform_preactivations": (
                    True,
                    "转化技清单（候选已收回）：填 ``ActivateSkill(来源, 目标)``——目标只在清单"
                    "带目标步时填 ``choices``；没有目标步（目标由牌本身决定，huo-mobile-batch"
                    " 票 07）就只交来源，目标集由引擎取自候选池",
                ),
                "transform_actions": (True, "被收回的转化候选：确认路径的成员判定（清单从它派生）"),
                "kind": (True, "选路键：``_SCORERS[QueryKind(query.kind)]`` 与候选生成的派发"),
            }
        ),
        "ArrangeQuery": MappingProxyType(
            {
                "structural": (
                    False,
                    "不枚举档 / 结构校验档的标志（huo-shenzhugeliang 票 02）：候选只有恒等兜底，"
                    "AI 因此只会'不换'。不读它的理由 = 这是**界面**的提交形态，不是打分输入；"
                    "真要按两行内容挑牌，得另有一条从 slots 派生的策略（本批不做）",
                ),
            }
        ),
        "DistributeQuery": MappingProxyType(
            {
                "skill_id": (True, "这一步属于哪条技能（文案归属；打分本身不按技能名分支）"),
                "seats": (
                    True,
                    "可分配的座位（座位升序）：逐个查 `_identity_relation` 决定这点数给谁",
                ),
                "max_total": (True, "合计上限：超过即非法（与候选同一份声明）"),
                "per_seat_max": (True, "单座上限：超过即非法（与候选同一份声明）"),
            }
        ),
        "PlayPhaseQuery": MappingProxyType(
            {
                "preactivations": (True, "主动技预发动清单：复合候选不在 ``legal_actions`` 里，靠它填表"),
                "designation_actions": (
                    False,
                    "借刀附带指定的**原始**候选池；AI 读派生的 ``card_preactivations``（已按牌名分组）",
                ),
                "card_preactivations": (
                    True,
                    "牌名 → 清单：填 ``UseCard(card, targets=(A,), designations=(Bn,))``",
                ),
                "auto_start": (
                    False,
                    "界面自动展开标记（qunying 票 11）：AI 不用它——复合候选仍从同一份"
                    "``preactivations`` 填表，自动展开只改界面的进入方式",
                ),
            }
        ),
        "PeachQuery": MappingProxyType(
            {
                "dying": (True, "救谁：濒死者是不是自己 / 是不是敌人，决定救命价值"),
            }
        ),
        "DiscardQuery": MappingProxyType(
            {
                "count": (False, "弃牌张数由候选自带（``action.cards``），按牌价取负即可"),
            }
        ),
        "RespondQuery": MappingProxyType(
            {
                "cause": (
                    True,
                    "无懈窗口（``NULLIFY``）按'被取消的牌对目标利 / 害 × 关系'判宣不宣"
                    "（D1，``_nullify_declares``）；乱武窗口（``LUANWU``）按'这张【杀】打向"
                    "谁'给分（`_luanwu_respond_value`，Q9 = B）；其余窗口仍是常量'能抵消就抵消'",
                ),
                "required": (False, "必需牌名不读：候选里只有合法的"),
                "target": (
                    True,
                    "无懈窗口里 = 本次结算针对的座位：它与我敌 / 友 / 未知决定宣不宣（D1）",
                ),
                "window_frame": (
                    False,
                    "嵌套层号只用于记录流区分：嵌套靠处理区顶牌是无懈判，不读层号",
                ),
            }
        ),
        "SelectCardsQuery": MappingProxyType(
            {
                "spec": (True, "``spec.optional`` 判'可取消步骤'（火攻的可选弃牌那一步）"),
                "slots": (True, "每个槽的牌面与归属：自己的牌按成本、别人的牌按收益"),
                "skill_id": (True, "步骤的成本语义（【制衡】逐张回抽 / 【仁德】给出）"),
                "provide_type": (False, "'提供一张 B'的牌名不读：合法性由引擎的校验器判"),
            }
        ),
        "SkillActivationQuery": MappingProxyType(
            {
                "skill_id": (True, "查 ``SKILL_VALUES``；也是被动清单的键"),
                "optional": (False, "不读：候选里有 ``Pass`` 本身就够判'这一步可取消'"),
                "min_picks": (False, "冻结报文：生产者恒给 0（A-4 待清理），读了也是常量"),
                "max_picks": (False, "同上：恒 0 的冻结报文"),
                "slots": (False, "冻结报文：生产者恒给 ``()``；真要选的牌在随后的 ``SELECT_CARDS`` 里"),
                "preactivation": (True, "被动入口的预发动清单（单条，不按技能分组）"),
                "skill_ids": (
                    False,
                    "本轮候选 id 清单不读：候选由 ``legal_actions`` 承载，打分仍按 "
                    "``action.skill_id`` 查 ``SKILL_VALUES``（票 01 的 N 候选面同一口径）",
                ),
                "activation_preactivations": (
                    True,
                    "逐候选的预发动清单：N 候选面靠它给**每一条**候选填复合动作"
                    "（``_preactivation_candidates``）；单候选面与 ``preactivation`` 同源",
                ),
            }
        ),
        "SelectOptionQuery": MappingProxyType(
            {
                "skill_id": (
                    True,
                    "座位步（``seat:`` 选项）的敌我估值按它查 ``_HOSTILE_SEAT_STEPS`` / "
                    "``_STOLEN_CARD_SEAT_STEPS`` 两张前缀表（``_score_seat_option`` 缺省读"
                    "询问自带的 id）",
                ),
                "options": (
                    False,
                    "选项面不枚举：非座位选项按 ``action.choice`` 查 ``_OPTION_VALUES``——"
                    "未登记的新选项落 0 分（新增选项必须同票登记条目）",
                ),
                "disabled": (
                    True,
                    "置灰项绝不选：``_score_hero_card_option`` 把它们压到 "
                    "``_ILLEGAL_PENALTY``（防御性分支——候选里引擎已把它们剔除）",
                ),
                "option_texts": (
                    True,
                    "有引擎给文本的行 = 技能自己的效果行（按一次性收益估）；没有文本的行 = "
                    "『获得该武将牌上的一个技能』——``_score_hero_card_option`` 的判据",
                ),
                "hero_card": (
                    True,
                    "非 None = 这一问是『展示武将牌』：``_score_select_option`` 据此进入 "
                    "``_score_hero_card_option``（技能行 / 效果行两档估值）",
                ),
            }
        ),
        "SelectOptionsQuery": MappingProxyType(
            {
                "skill_id": (
                    True,
                    "多选面的技能归属：与 ``SelectOptionQuery`` 同一条（开关 = 界神速的三项、"
                    "座位 = 【秉壹】等座位步）——座位选项的敌我估值按它查那两张前缀表",
                ),
                "options": (
                    True,
                    "选项池（含置灰）：``_score_select_options`` 逐项查 ``_option_choice_value``"
                    "（未登记落 0 分）；**不枚举档**（``structural``）下它同时是 AI 自建集合的"
                    "可选池，且集合按它的**原序**上行（常规结算顺序）",
                ),
                "disabled": (
                    True,
                    "置灰项不进任何候选 / 自建集合（不枚举档由 AI 自己在池里滤掉）——"
                    "『这一项此刻不可选』，不据此改分数",
                ),
                "min_selected": (
                    True,
                    "下限：不枚举档下是**自建集合的地板**（不足则按排名补），枚举档下由候选承担",
                ),
                "max_selected": (
                    True,
                    "上限：不枚举档下是**自建集合的天花板**（只取正分项、至多这么多），"
                    "枚举档下由候选承担",
                ),
                "option_texts": (
                    False,
                    "显示文本只给界面看；AI 的估值读选项 id（``_OPTION_VALUES`` / 座位前缀）",
                ),
                "structural": (
                    True,
                    "**不枚举档**（yijiang-2014-round2 票 21）：``True`` ⇒ 候选里只有骨架，"
                    "AI 必须自己按 ``_option_choice_value`` 拼一个合法集合（谓词 = 引擎收答案的"
                    "``is_legal_option_set``）；读它是『该自己拼』的唯一判据",
                ),
            }
        ),
    }
)
"""**AI 读点表**：``PlayerView`` / ``PlayerPublic`` / 每类询问的每个字段 → 读不读 + 一句理由。

桶 = 字段**所属的那一类**；询问的**公共面**（``_QueryBase`` 的字段与各类声明的 ``kind``）
登记在 ``"Query"`` 桶。格式、四条机械判据、``exclude=True`` 面为什么也要登记、以及"把读过
的字段写成不读"这个高频错误，都在 ``docs/agents/ai-adaptation.md`` §3.2（正文只此一份）；
断言住 ``tests/test_ai_adaptation.py``。
"""


# ---------------------------------------------------------------------------
# 技能语义表：键 = 技能 id，值 = (打分函数, 默认策略说明)
# ---------------------------------------------------------------------------


def _constant(value: float) -> Callable[[Query, Action, PlayerView], float]:
    """常量收益的语义条目（大多数"发动即净赚"的技能用它）。"""

    def scorer(query: Query, action: Action, view: PlayerView) -> float:
        del query, action, view
        return _item_score("常量收益", value)

    return scorer


def _picked_value(view: PlayerView, action: ActivateSkill) -> float:
    """``ActivateSkill.picks``（出牌阶段主动技的**实体牌 id**）的牌面价值之和。

    主动技的载荷在候选构造期就烘进 ``picks``（票 04）：制衡 / 贯石斧一类可跨**手牌与
    装备区**，故照 ``view.self_hand`` ∪ 自己装备区查牌面；查不到（不该发生）按未知牌
    估值，不读任何隐藏信息。
    """
    mine = view.players[view.seat]
    by_id = {card.card_id: card for card in (*view.self_hand, *mine.equip)}
    return sum(_card_value(by_id.get(card_id), view=view) for card_id in action.picks)


def _skeleton_cost(view: PlayerView, count: int) -> float:
    """**骨架候选**的成本预估：发动后那一步会让 AI 挑最便宜的成本张。

    主动技迁到步骤流水线后（票 02 起），候选不再烘 ``picks``（付不付得起改判在步骤里），
    于是 ``_picked_value`` 恒 0、技能看起来免费。``_score_select_cards`` 之后会挑自己
    价值最低的牌当成本，故这里按"手里最便宜的 ``count`` 张"预估，让发动决策重新看得见成本。
    """
    values = sorted(_card_value(card) for card in view.self_hand)
    return sum(values[:count])


def _skill_cost(view: PlayerView, action: ActivateSkill, skeleton: int) -> float:
    """技能成本：候选带 ``picks`` ⇒ 实际牌价；骨架候选 ⇒ 按 ``skeleton`` 张预估。"""
    if action.picks:
        return _picked_value(view, action)
    return _skeleton_cost(view, skeleton)


def _score_rende(query: Query, action: Action, view: PlayerView) -> float:
    """【仁德】：给牌是成本；本阶段首次给到 ≥2 张回复 1 点（只在受伤时值得）。

    票 05 起候选是**骨架**（``picks`` 为空，给哪几张改由发动后的 ``SELECT_CARDS`` 问）；
    旧式 ``len(action.picks) >= 2`` 对骨架恒假、AI 会干脆不用【仁德】。这里按下游那一步
    的实际选择预估（与 :func:`_score_select_cards` 的给牌分支同一模型）：受伤且手牌 ≥2 张
    时给最便宜的两张换 1 点体力，否则给最便宜的一张（净亏，通常不会发动）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if action.picks:
        bonus = _RENDE_RECOVER_VALUE if len(action.picks) >= 2 and _wounded(view) else 0.0
        return _net_score("回复", bonus, _picked_value(view, action))
    if not view.self_hand:
        return _item_score("空手", 0.0)
    if _wounded(view) and len(view.self_hand) >= 2:
        return _net_score("回复", _RENDE_RECOVER_VALUE, _skeleton_cost(view, 2))
    return _item_score(_PRICE_ITEM, -_skeleton_cost(view, 1))


_JIE_RENDE_BASIC_VALUE = 6.0
"""界【仁德】"视为使用一张基本牌"对 AI 的估值。

一张免费的基本牌 ≈ 一次额外的进攻 / 回复（【桃】/【杀】都在 3.5–6 的价值区间），且它不占
手牌、不另付代价；取略高于"一张普通牌"的值，好让"给两张换一次使用"在 AI 的同一问里
排到"先出一张同价牌"之前——否则界【仁德】这个**双收益**机会会被逐张出牌挤掉。
"""


def _score_jie_rende(query: Query, action: Action, view: PlayerView) -> float:
    """界【仁德】：给牌是成本；本阶段第二张换一次"视为使用一张基本牌"。

    与标准【仁德】的差别是**收益不同**（不回复、改为额外使用一张基本牌）；AI 也无法从
    公开面读到"本阶段已经给过几张"，故按下游选牌步的同一模型估：给 ≥2 张就按换到一张
    基本牌计。给 1 张只在"下一张就凑满 2"时才有收益，AI 不建模这一层（保守）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if action.picks:
        bonus = _JIE_RENDE_BASIC_VALUE if len(action.picks) >= 2 else 0.0
        return _net_score("视为使用一张基本牌", bonus, _picked_value(view, action))
    if not view.self_hand:
        return _item_score("空手", 0.0)
    if len(view.self_hand) >= 2:
        return _net_score(
            "视为使用一张基本牌", _JIE_RENDE_BASIC_VALUE, _skeleton_cost(view, 2)
        )
    return _item_score(_PRICE_ITEM, -_skeleton_cost(view, 1))


def _score_zhiheng(query: Query, action: Action, view: PlayerView) -> float:
    """【制衡】：弃任意张摸等量——净牌数不变、换质量；弃【桃】/【闪】按价值扣分。

    票 04 起候选是**骨架**（``picks`` 为空，弃几张改由发动后的 ``SELECT_CARDS`` 问）。
    按下游那一步的实际选择预估：:func:`_score_select_cards` 对"任意张自己的牌"逐张按
    :data:`_CYCLE_VALUE - 牌价` 估值 ⇒ 它会弃掉所有**低于换牌期望**的牌（手牌 ∪ 装备区），
    这里就用同一组牌算同一笔账。旧公式 ``1.5 * len(picks) - picked_value`` 对骨架恒 0，
    会让 AI 干脆不用【制衡】（与【结姻】票 03 同一类退化）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if action.picks:
        return _net_score(
            "换牌收益", _CYCLE_VALUE * len(action.picks), _picked_value(view, action)
        )
    mine = view.players[view.seat]
    values = sorted(_card_value(card) for card in (*view.self_hand, *mine.equip))
    count = sum(1 for value in values if value < _CYCLE_VALUE)
    if count == 0:
        # 没有值得换的牌，但 ``lo=1`` 仍逼着弃一张 ⇒ 发动只会白丢最便宜的那张。
        # 给**负分**（而不是 0.0）：否则与"结束阶段"打平，AI 会随机把限一次的机会浪费掉。
        if not values:
            return _item_score("空手", 0.0)
        return _net_score("换牌收益", _CYCLE_VALUE, values[0])
    return _net_score("换牌收益", _CYCLE_VALUE * count, sum(values[:count]))


def _score_keji(query: Query, action: Action, view: PlayerView) -> float:
    """【克己】（M6 票 03）：跳过弃牌阶段 ⇒ 保住"超出体力值"的那几张手牌。

    收益按保住的张数估（每张约一张未知牌的价值）：手牌数 − 当前体力值。
    不超出上限时不发动（0 分 ⇒ 与 Pass 打平，由下一层的"放弃"胜出）。
    """
    del query, action
    me = view.players[view.seat]
    return _item_score(
        "保住的超上限手牌",
        float(max(0, me.hand_count - me.hp)) * _unknown_card_value(view),
    )


def _score_kurou(query: Query, action: Action, view: PlayerView) -> float:
    """【苦肉】（M6 票 03）：失去 1 点体力换两张牌；**1 点体力时不换**（再失去即濒死）。

    标准版没有"每阶段一次"⇒ 这个打分会被反复问：体力 > 1 时每次都是正分，
    掉到 1 点后转为负分，AI 因此不会把自己苦肉死。
    """
    del query, action
    if view.players[view.seat].hp <= 1:
        return _item_score("1 体力再掉即濒死", -_unknown_card_value(view))
    return _item_score("两张牌", 2.0 * _unknown_card_value(view))


def _score_qingnang(query: Query, action: Action, view: PlayerView) -> float:
    """【青囊】：弃一张手牌令**已受伤**角色回复 1 点（= 3 点收益 − 牌价）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return _net_score("回复", 3.0, _picked_value(view, action))


def _score_fanjian(query: Query, action: Action, view: PlayerView) -> float:
    """【反间】：交付一张牌，目标猜错花色则受伤（期望为正）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return _net_score("反间收益", 2.5, _picked_value(view, action))


def _score_lijian(query: Query, action: Action, view: PlayerView) -> float:
    """【离间】：弃一张牌令两名男性角色决斗（把伤害转给别人）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return _net_score("让两人决斗", 3.0, _picked_value(view, action))


def _score_jieyin(query: Query, action: Action, view: PlayerView) -> float:
    """【结姻】：弃两张手牌，双方各回复 1 点（自己回血是主要收益）。

    票 03 起候选是**骨架**（``picks`` 为空，成本改由发动后的 ``SELECT_CARDS`` 挑）⇒
    按"手里最便宜的两张"预估成本，否则裸候选看起来免费、AI 无脑发动（胜率回归）。
    自己没受伤时收益只剩"替目标回血"（目标还可能是敌人），故大幅降值。
    """
    assert isinstance(action, ActivateSkill)
    del query
    bonus = 4.0 if _wounded(view) else 1.0
    return _net_score("回复", bonus, _skill_cost(view, action, 2))


def _score_liuli(query: Query, action: Action, view: PlayerView) -> float:
    """【流离】：弃一张牌把【杀】转移给合法新目标（候选存在即意味着有目标）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return _net_score(
        "转移【杀】", 3.0, 0.5 * _picked_value(view, action)
    )


def _score_jie_kurou(query: Query, action: Action, view: PlayerView) -> float:
    """界【苦肉】（M6 票 17）：弃一张牌 + 失去 1 点体力，换来【诈降】**摸三张**。

    净牌数 = +2（摸三张、弃一张），另有"本阶段红【杀】无距离 / 不可闪 / 次数 +1"；
    1 点体力时不换（再失去即濒死）。成本按骨架候选预估一张最便宜的牌。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if view.players[view.seat].hp <= 1:
        return _item_score("1 体力再掉即濒死", -_unknown_card_value(view))
    return _net_score(
        "两张牌 + 诈降",
        3.0 * _unknown_card_value(view),
        _skill_cost(view, action, 1),
    )


def _score_jie_guose(query: Query, action: Action, view: PlayerView) -> float:
    """界【国色】（M6 票 17）：二选一 + 稳定摸一张。

    选项 1 是把方块牌变成【乐不思蜀】（控制），选项 2 是弃两张牌解掉场上的控制；
    两者之外**恒摸一张**，故即使方块牌本身是成本，净收益仍为正。
    """
    assert isinstance(action, ActivateSkill)
    del query, view
    return _item_score("二选一 + 摸一张", 2.0)


def _score_jie_jieyin(query: Query, action: Action, view: PlayerView) -> float:
    """界【结姻】（M6 票 17）：成本 1 张牌，较高的摸一张、较低的回复 1 点。

    自己受伤时回复归自己（主收益）；都不受伤且体力相同 ⇒ 两种效果都不执行，纯亏成本，
    故降值。成本按骨架候选预估一张最便宜的牌。
    """
    assert isinstance(action, ActivateSkill)
    del query
    bonus = 4.0 if _wounded(view) else 1.0
    return _net_score("回复", bonus, _skill_cost(view, action, 1))


def _score_jie_jiuyuan(query: Query, action: Action, view: PlayerView) -> float:
    """界【救援】（M6 票 17）：决定者是**正在回复体力的那个吴势力角色**。

    收益 = 自己**确定摸一张**，外加把这次回复转给主公（对主公阵营是好事；不做敌我模型，
    按 D16"平衡读数只记录、不设阈值"给一个常量为正）。不发动则 0 分。
    """
    assert isinstance(action, ActivateSkill)
    del query, view
    return _item_score("摸一张 + 回复转给主公", 2.0)


def _score_chuli(query: Query, action: Action, view: PlayerView) -> float:
    """界华佗【除疠】（M6 票 18）：自己弃一张、每名目标各弃一张；黑桃还有摸牌。

    净收益 = 拆掉对手若干张 − 自己的成本一张；黑桃摸牌不打折（不明牌面，按保守常数）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    return _net_score("拆掉对手若干张", 2.0, 0.5 * _skeleton_cost(view, 1))


def _score_shizhan(query: Query, action: Action, view: PlayerView) -> float:
    """界华雄【势斩】（M6 票 18）：令其他角色视为对我使用【决斗】。

    手里有【杀】才有得打：一张也能赢"对方打不出【杀】"的局面，两张以上才扛得住一次
    来回。1 点体力时不做这笔冒险（决斗输掉即濒死）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = view.players[view.seat]
    if me.hp <= 1:
        return _item_score("1 体力不赌决斗", -_unknown_card_value(view))
    slashes = sum(
        1
        for card in view.self_hand
        if _is_slash_card(card)
    )
    if slashes:
        return _item_score("手里有【杀】", 1.0)
    return _item_score("手里没【杀】", -_unknown_card_value(view))


def _score_shensu(query: Query, action: Action, view: PlayerView) -> float:
    """风包夏侯渊【神速】（票 01）：跳判定 + 摸牌换一张无距离【杀】。

    常态不划算——跳过摸牌阶段少摸两张，只换一张【杀】；**能补刀**时才值得：敌方有人
    ``hp <= 1``（一张【杀】就能打死，且这个技能无距离限制、够得着任何位置）。读的都是
    公开面（体力 + 身份关系），不碰隐藏信息。
    """
    assert isinstance(action, ActivateSkill)
    del query
    for player in view.players:
        if player.seat == view.seat or not player.alive:
            continue
        enemy, _ally = _identity_relation(view, player.seat)
        if player.hp <= 1 and enemy >= 0.5:
            return _item_score("能补刀", 3.0)
    return _item_score("常态不划算", -1.0)


def _score_qimou(query: Query, action: Action, view: PlayerView) -> float:
    """界魏延移动版【奇谋】（jie-feng-batch 票 02）：限定技，按"值不值得用"判。

    - **体力 ≤ 1** ⇒ 失去任意点体力都会进濒死 ⇒ −1.0（低于 Pass = 0.0）；
    - 否则正分（限定技值得用；具体 X 由 `_OPTION_VALUES` 挑最小的那一档）。

    读的都是公开面（自己的体力）；不碰隐藏信息。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = next((player for player in view.players if player.seat == view.seat), None)
    if me is None or not me.alive:
        return _item_score("读不到自己（保守）", _UNKNOWN_SKILL_VALUE)
    if me.hp <= 1:
        return _item_score("1 体力再用即濒死", -1.0)
    return _item_score("限定技值得用", 1.0)


def _score_qiangxi(query: Query, action: Action, view: PlayerView) -> float:
    """移动版典韦【强袭】（huo-dianwei-mobile 票 01）：1 点伤害，代价在下一步里挑。

    代价二选一 ⇒ 这里只回答"值不值得发动"，挑哪一档交给 ``_OPTION_VALUES``
    （武器牌 −2.0 比失去 1 点体力 −3.5 便宜，有牌就弃牌）。读的都是公开面（体力）
    与自己可见的牌（手牌 / 装备区的槽位种类），不碰隐藏信息。

    - **有武器牌可弃** ⇒ 3.5 − 2.0 ≈ +1.5：恒值得；
    - **只能失去体力** ⇒ **能补刀**（任一存活敌方 ``hp <= 1``）时 +2.0，否则保守给 +0.5；
    - **自己只剩 1 体力** ⇒ 自伤会直接进濒死 ⇒ −1.0（低于 Pass = 0.0）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = next((player for player in view.players if player.seat == view.seat), None)
    if me is None or not me.alive:
        return _item_score("读不到自己（保守）", _UNKNOWN_SKILL_VALUE)
    if me.hp <= 1:
        return _item_score("1 体力自伤即濒死", -1.0)
    weapons = [card for card in view.self_hand if card.slot is SlotKind.WEAPON]
    weapons.extend(card for card in me.equip if card.slot is SlotKind.WEAPON)
    if weapons:
        return _item_score("有武器牌可弃", 1.5)
    for player in view.players:
        if player.seat == view.seat or not player.alive:
            continue
        enemy, _ally = _identity_relation(view, player.seat)
        if player.hp <= 1 and enemy >= 0.5:
            return _item_score("能补刀", 2.0)
    return _item_score("只能失去体力（保守）", 0.5)


# ---------------------------------------------------------------------------
# S1 技能表 top3（票 22）：据守 / 贯石斧 / 谦逊
#
# 这三条在盘点（spec 附录 A）里是「有条目但太粗」：正分常量 ⇒ 无条件发动，而牌面各有
# **代价**（翻面 / 弃两张 / 扣光手牌）。三个打分器只读询问自带的 ``view``：体力、公开的
# 翻面状态、自己的手牌牌面（成本与续命牌）——不读任何隐藏信息，也不改技能 / 规则语义。
# ---------------------------------------------------------------------------

_JUSHSOU_FACE_UP_VALUE = 2.0
"""【据守】正面发动：三张牌 vs 跳过下个回合——票 22 之前的中等常数（口径不变）。"""

_JUSHSOU_FLIPPED_VALUE = 3.0
"""【据守】已翻面时发动：翻面是 **toggle**，这一次是翻回正面（下个回合不再被跳过）⇒
三张牌纯赚，故高于正面档，且永远压过 Pass（0.0）。"""

_JUSHSOU_LAST_STAND_VALUE = -1.0
"""【据守】正面且只剩 1 体力（低于 Pass = 0.0）：跳过下个回合等于把命交给对手，
三张牌换不起。

量级只要求"低于 Pass"——它和 :data:`_UNKNOWN_SKILL_VALUE` 同值不同义（那条是"没教过
这条技能"），故各留一个常量。
"""

_AXE_SALVAGE_VALUE = 3.0
"""【贯石斧】把被抵消的【杀】捞回来的那 1 点**非致命**伤害的价 = 一张伤害牌的基础分
（:data:`_DAMAGE_BASE`）。

两张牌的常见牌价（【闪】4.0 / 【杀】3.0 / 【桃】6.0）都高于它 ⇒「两张高价值牌换 1 点
非致命伤害」的净收益为负；只有两张废牌（【闪电】1.0 + 一匹 1.0 的坐骑）才付得起。
"""

_AXE_KILL_VALUE = _LETHAL_STRIKE_VALUE
"""【贯石斧】这一下直接**打死**目标时的价：与 D2 斩杀项同一笔"击杀"的价，直接引用
:data:`_LETHAL_STRIKE_VALUE`，不另写一个数（改价时两处不会漂开）。

击杀**不扣牌价**：票面验收写的是「目标 1 体力 ⇒ 发动」，没有给斩杀加成本条件。
**非致命**那 1 点伤害才走"收益 − 成本"。

**票 23（C2）的边界**：``_score_attack`` 的斩杀项现在按 :func:`_expected_lethal_score`
折【桃】的期望，**斧子不折**——ai-v6 已裁定"击杀恒发动、不扣牌价"，本票"一次只改一个量"，
不把那条裁定的行为面一起动（登记在票 23 的 Answer 与台账 `ai-v7` 口径 3①）。"""

_QIANXUN_VALUE = 2.0
"""【谦逊】照常发动：躲开拆 / 顺、回合末拿回手牌，等于净赚（票 22 之前的常量）。"""

_QIANXUN_LAST_STAND_VALUE = -1.0
"""【谦逊】1 体力且手里有续命牌时的价（低于 Pass = 0.0）：把保命牌扣在武将牌上，
这一回合就用不出来了，等于等死。"""

_SELF_PRESERVATION_CARD_TYPES = frozenset({"dodge", "peach", "wine"})
"""1 体力时要留在手里**续命**的牌：能抵消伤害（【闪】）或把自己从濒死拉回来（【桃】/【酒】）。

``frozenset`` 只作成员判定、不迭代（源码纪律禁止的是集合**迭代**）。票面只点名【闪】，
另外两张是同一条语义的闭集成员（1 体力时扣上武将牌同样用不出来），故一并算进来；
杀 / 无懈一类"响应窗口用"的牌不算——它们救不了 1 体力时的命。
"""


def _score_jushou(query: Query, action: Action, view: PlayerView) -> float:
    """【据守】（移动版界曹仁）：结束阶段摸三张，然后把自己的武将牌**翻面**。

    翻面是 toggle（规则集 13.1）：已翻面时这一次翻面是**翻回正面**，下个回合不再被跳过
    ⇒ 三张牌纯赚；正面发动要付"跳过下个回合"，而 1 体力时那笔代价最贵（对手有整整一个
    回合动手），故低于 Pass。2 体力以上仍按既有的中等常数。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = view.players[view.seat]
    if me.flipped:
        return _item_score("翻回正面 + 三张牌", _JUSHSOU_FLIPPED_VALUE)
    if me.hp <= 1:
        return _item_score("1 体力跳回合等死", _JUSHSOU_LAST_STAND_VALUE)
    return _item_score("三张牌 vs 跳过下回合", _JUSHSOU_FACE_UP_VALUE)


def _salvage_targets(view: PlayerView) -> tuple[int, ...]:
    """【贯石斧】的发动询问**不带目标**（``SkillActivationQuery`` 只有 ``skill_id``）。

    这次【杀】是我自己打出去、刚被【闪】抵消的那一张，故目标只会是"我会打的人"，按这条
    取集合：存活的非自己座位里**不是已亮明盟友**的那些（``P(友) < 1``）。判据只排除"确定是
    盟友"的座位——未知座位（``P(友)`` 是配比里的比例）、中立座位（``(0, 0)``：2 人无身份局、
    内奸眼中的主公、反贼眼中的内奸）都算**可能的目标**。与身份后验的 fail-closed 同一条口径：
    算不出就不猜，但也不要因此把斧子判成永远不发动；反过来，已亮明盟友（``P(友) = 1``）连
    "可能被打"都不该算，否则斧子会为"打死盟友"计价。

    读法上的已知误差：真实目标不可见，故任一"可能的目标"是 1 体力就按斩杀计价。这比"永不
    发动"接近牌面（这一刀已经打出去了），比"任意局面都发动"保守；不要在打分器里补历史。
    """
    return tuple(
        player.seat
        for player in view.players
        if player.alive
        and player.seat != view.seat
        and _identity_relation(view, player.seat)[1] < 1.0
    )


def _score_axe(query: Query, action: Action, view: PlayerView) -> float:
    """【贯石斧】：弃两张牌，让**被【闪】抵消的**自己的【杀】依然造成伤害。

    两条分支（票面验收的两句话）：

    - **可能的斩杀**（目标集合里有人 1 体力，见 :func:`_salvage_targets`）：按
      :data:`_AXE_KILL_VALUE` 计分、**不扣牌价**——"能打死就不为两张牌放弃"，与
      ``_score_attack`` 的斩杀项同口径；
    - **1 点非致命伤害**：按 :data:`_AXE_SALVAGE_VALUE` − 成本；成本 = 这一问要付的两张牌
      （复合候选已在 ``picks`` 里填好 ⇒ 实际牌价；裸候选按"手里最便宜的两张"预估——与成本步
      ``_score_select_cards`` 挑牌共用 :func:`_skill_cost`，发不发动与挑哪几张算同一笔账）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if any(view.players[seat].hp <= 1 for seat in _salvage_targets(view)):
        return _item_score("可能的斩杀（不扣牌价）", _AXE_KILL_VALUE)
    return _net_score(
        "捞回 1 点非致命伤害", _AXE_SALVAGE_VALUE, _skill_cost(view, action, 2)
    )


def _score_qianxun(query: Query, action: Action, view: PlayerView) -> float:
    """【谦逊】（界陆逊）：别的角色对自己生效的锦囊把**全部手牌**扣上武将牌，回合末才拿回。

    代价是"这一回合手牌都用不出来"。1 体力时那笔代价最贵——手里的【闪】/【桃】/【酒】
    正是保命的牌，扣上去就等于等死，故 Pass；其余情况照常发动（躲开拆 / 顺是净赚）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = view.players[view.seat]
    if me.hp <= 1 and any(
        card.type in _SELF_PRESERVATION_CARD_TYPES for card in view.self_hand
    ):
        return _item_score("1 体力扣住保命牌", _QIANXUN_LAST_STAND_VALUE)
    return _item_score("躲开拆 / 顺", _QIANXUN_VALUE)


_HAND_REQUIRED_SEAT_STEPS: frozenset[tuple[str, str]] = frozenset(
    {
        # 【义绝】的目标必须"有手牌"（规则 §十九 L2122：选择一名有手牌的其他角色）。
        ("hero.jie_guanyu.yijue", "seat"),
    }
)
"""座位步要求"目标有手牌"的技能（**公开的** ``hand_count`` 可判，不读暗牌）。

引擎的候选池**故意不按手牌收缩**（【义绝】的池只按"存活 / 不是自己"定：展示步为空时
整步取消、提交点之前就停，重入不会因目标已被拿走手牌而误取消）。代价是 AI 可能选中
一个空手目标：展示步空面 ⇒ 取消 ⇒ 不落「阶段已用」位 ⇒ 同一个出牌阶段里反复发动同一
个空操作（32 种子混合池扫里真出现过）。故 AI 用**公开**的手牌数避开空手目标。
"""


def _huangtian_lord_seat(view: PlayerView) -> int | None:
    """公开面上**主公的座位**（``identity`` 非空且是 ``lord``）——主公技的接收方。

    主公的身份是公开信息（`06`（§5「程序化约定与偏离一手」）U1-04 行 / 投影的
    ``_visible_identity``），故 AI 只读公开面就能找到他，不碰全知状态。
    """
    for player in view.players:
        if player.identity == "lord":
            return player.seat
    return None


def _score_huangtian(query: Query, action: Action, view: PlayerView) -> float:
    """主公技【黄天】：把一张【闪】/【闪电】交给张角（收益归他、成本归我）。

    候选侧的四个门之一就是"同势力"（群），故把主公按**未明敌人**处理：只有他**已亮明
    是敌人**时才不给（−1），明盟友 / 未知给正分（一张牌换主公的防御资源）。不按势力
    直接判"友"——身份局的群势力里既有忠臣也有反贼。
    """
    del query, action
    lord = _huangtian_lord_seat(view)
    if lord is None:
        return _item_score("没有主公", 0.0)
    enemy, _ally = _identity_relation(view, lord)
    if enemy >= 1.0:
        return _item_score("主公是已明敌人", -1.0)
    return _item_score("把牌交给主公", 2.0 * (1.0 - enemy))


def _score_jiuchi(query: Query, action: Action, view: PlayerView) -> float:
    """林包·移动版董卓【酒池】（lin-batch2 票 02，Q7 = A）：黑桃手牌当【酒】。

    **与真【酒】同一条估值**（三参包装 :func:`_score_wine`）：这一口酒只在"下一张【杀】
    正好凑成斩杀"时给正分，否则 :data:`_WINE_WASTE_PENALTY`（【酒】每回合限一次，白喝
    就再也救不了自己）。真正的候选打分仍走 ``_score_card_use`` 的 ``wine`` 分支（与真
    【酒】同一个函数），本条只满足语义表的双向同步纪律与"清单 / 被动入口"的登记口径；
    濒死那一侧由 :func:`_score_peach` 按"回复 1 点"计价（``_RESCUE_CARD_TYPES`` 含 wine）。
    """
    del action
    total = Score()
    total.add("酒（与真【酒】同一条估值）", _score_wine(query, view))
    return total.finish()


def _score_baonue(query: Query, action: Action, view: PlayerView) -> float:
    """林包·移动版董卓【暴虐】（lin-batch2 票 02，Q7 = A）：**关系感知**的主公技。

    这一问的应答者是**造成伤害的那个群势力角色**（决定者 ≠ 受益者），收益归主公：
    主公回复 1 点体力。``SkillActivationQuery`` 没有"受益者是谁"的字段，故这里按
    **主公身份公开**这一条事实自己找（:func:`_huangtian_lord_seat`，与【黄天】同一姿态）：
    主公在**我的盟友集**里（:func:`_ally_identities`，按身份阵营算）⇒ ``+1.5``
    （1 点回复 ≈ 半个 :data:`_HEAL_VALUE`，而且是一次"白拿"的机会）；否则 ``-1.0``
    （帮敌人回血是负收益）。主公 / 自己的身份读不到（无身份局）⇒ ``0.0``（不表态）。
    """
    del query, action
    lord = _huangtian_lord_seat(view)
    if lord is None:
        return _item_score("没有主公", 0.0)
    mine = _me(view).identity
    if mine is None:
        return _item_score("读不到自己的身份", 0.0)
    if "lord" in _ally_identities(mine):
        return _item_score("帮主公回血", 1.5)
    return _item_score("帮敌人回血", -1.0)


def _score_guixin(query: Query, action: Action, view: PlayerView) -> float:
    """神曹操移动版【归心】（lin-batch 票 01）：逐座位各拿一张 + **翻面**。

    **有代价**：翻面是 toggle（L783）——正面朝上时这一手会把自己翻过去，下个回合开始前
    翻回正面并**跳过整个回合**（规则 13.2）；已经背面朝上时，这一手反而是**翻回正面**的
    收益。故 ``SKILL_VALUES`` 只回答"值不值得发动"：取几张读**公开面**
    （``view.players[*]`` 的 ``alive`` / ``hand_count`` / ``equip`` / ``judge`` 判"这一座
    有没有牌可拿"），翻过去还是翻回来读 ``view.players[self].flipped``；**挑哪一张**由
    通用的 ``SELECT_CARDS`` 打分器按牌面处理（``action.picks`` 不进这一问）。

    数值：``gains`` = 有牌的其他存活角色数。背面朝上 ⇒ ``gains + 1.0``；正面朝上 ⇒
    ``gains − 1.5``（跳过自己下个回合的代价）；全场无牌（Q1 = B 的"只为翻面"）⇒
    背面 +1.0 / 正面 −1.0。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = next((player for player in view.players if player.seat == view.seat), None)
    if me is None or not me.alive:
        return _item_score("读不到自己（保守）", _UNKNOWN_SKILL_VALUE)
    gains = sum(
        1
        for player in view.players
        if player.seat != view.seat
        and player.alive
        and (player.hand_count or player.equip or player.judge)
    )
    if me.flipped:
        total = Score()
        total.add("各拿一张", float(gains))
        total.add("翻回正面", 1.0)
        return total.finish()
    if not gains:
        return _item_score("翻面没有收益", -1.0)
    total = Score()
    total.add("各拿一张", float(gains))
    total.add("翻面（跳过下个回合）", -1.5)
    return total.finish()


SKILL_VALUES: dict[str, tuple[Callable[[Query, Action, PlayerView], float], str]] = {
    # SP武将组5（sp5-mayunlu-panjun 票 01 / 02）：马术 / 公清是锁定技（没有「是否发动」，
    # 按纪律写 0.0 + 说明）；凤魄 / 观微按收益给正分。
    "hero.mayunlu.mashu": (
        _constant(0.0),
        "锁定技：距离 -1，恒生效、没有询问（不入表就是漏登记）",
    ),
    "hero.panjun.gongqing": (
        _constant(0.0),
        "锁定技：按伤害来源的攻击范围封顶 / 加一，恒生效、没有询问",
    ),
    "hero.mayunlu.fengpo": (
        _constant(2.4),
        "单目标【杀】/【决斗】后二选一：摸 X 张或 +X 伤害，X = 双方手牌 + 装备区的 ♦ 数；"
        "通常净赚 1~2 张 / 1~2 点伤害，恒高于放弃（两个选项的分差由 _OPTION_VALUES 定）",
    ),
    "hero.panjun.guanwei": (
        _constant(2.0),
        "弃一张换其摸两张 + 一个额外出牌阶段：多一段用牌窗口，通常净正（代价是自己的一张牌）",
    ),
    # sp5-pangtong-liuqi：SP庞统 / 刘琦（用户 2026-11-12 一次签）。
    "hero.pangtong_sp.guolun": (
        _constant(2.2),
        "展示对方一张手牌 → 可换一张 → 点数小的一方摸一张：换牌本身是等价交换、"
        "额外的收益是自己的牌点数更小（摸一张）或破坏对方手牌结构；通常净正",
    ),
    "hero.pangtong_sp.songsang": (
        _constant(2.6),
        "限定技：别的角色死亡时回 1 点体力或 +1 体力上限，并获得 ☆【展骥】"
        "（之后出牌阶段每次摸牌都多摸一张）——整局一次的永久收益，恒发动",
    ),
    "hero.liuqi.wenji": (
        _constant(2.0),
        "出牌阶段开始时收一张 + 本回合同名牌不能被响应：净赚一张牌，且给自己一张"
        "『穿透』的牌（对方只能挨打），通常净正",
    ),
    "hero.liuqi.tunjiang": (
        _constant(2.0),
        "结束阶段摸 X 张（X = 存活角色的势力种数）：无代价的纯收益，条件成立就发动",
    ),
    # 魏
    "hero.caocao.jianxiong": (_constant(4.0), "受伤后获得造成伤害的牌：净赚一张，恒发动"),
    # 神周瑜移动版（huo-shenzhouyu-mobile 票 01）：【琴音】是"全体 1 点"的收益（净收益按
    # 场上多寡变号，但恒有权衡⇒保守给正）；【业炎】是限定技 + 最多 3 点火焰伤害（分多少、
    # 给谁由分配弹窗的打分逐点决定）。
    "hero.shen_zhouyu_mobile.qinyin": (
        _constant(2.0),
        "弃牌阶段结束时无条件问一次：全体回复 / 全体失去 1 点，收益恒非负（选哪项由 "
        "_OPTION_VALUES 定），故给正分",
    ),
    # 神诸葛亮（huo-shenzhugeliang 票 01）：三条都是"星是稀缺资源"的正分项。
    # 七星的两行交换面候选只有恒等兜底（不枚举档）⇒ AI 实际只选"不换"，给 0 分是
    # 诚实的（不做没被要求的挑牌策略）；狂风 / 大雾 各按收益给正分。
    "hero.shen_zhugeliang.qixing": (
        _constant(0.0),
        "两行交换：候选只有恒等兜底（界面才构造答案）⇒ AI 只'不换'，中性分",
    ),
    "hero.shen_zhugeliang.kuangfeng": (
        _constant(1.2),
        "结束阶段花一张星：己方受到火焰伤害时 +1（通常是配合火杀 / 火攻），略高于放弃",
    ),
    "hero.shen_zhugeliang.dawu": (
        _constant(2.0),
        "结束阶段花星护核：非雷电伤害全免一轮，防御收益通常高于狂风",
    ),
    "hero.shen_zhouyu_mobile.yeyan": (
        _constant(4.5),
        "限定技：最多 3 点火焰伤害（整局一次）——伤害是货真价实的收益，按限定技给高分；"
        "代价（四色弃牌 + 失 3 体力）由分配弹窗的单座上限与逐点打分间接约束",
    ),
    "hero.caocao.hujia": (
        _constant(_DODGE_SKILL_VALUE),
        "主公技：视为打出一张【闪】，响应窗口里能抵消就抵消",
    ),
    "hero.simayi.fankui": (_constant(3.5), "受伤后获得来源一张牌：净赚一张，恒发动"),
    "hero.simayi.guicai": (_constant(1.0), "改判窗口：有改判能力即参与（保守给正但不高）"),
    # M6 票 01：
    "hero.xiahoudun.ganglie": (
        _constant(4.0),
        "受伤后判定：非红桃则伤害来源弃两张手牌或掉 1 点，收益恒为正",
    ),
    "hero.zhangliao.tuxi": (
        _constant(3.5),
        "放弃摸牌改为至多拿两张：净赚（拿到 ≤2 张、代价是少摸 2 张）",
    ),
    # feng-zhangjiao 票 01：张角移动版三条
    "hero.zhangjiao_mobile.leiji": (
        _constant(2.0),
        "使用 / 打出【闪】后免费判定（♠ 1/4 概率造成 2 点雷电）：无成本，恒发动",
    ),
    # jie-feng-batch2 票 02：界张角移动版三条。
    "hero.jie_zhangjiao_mobile.leiji": (
        _constant(2.5),
        "使用 / 打出【闪】后免费判定：♠ 造 2 点、♣ 回复 1 点 + 造 1 点（两个分支都对自己有利），"
        "无成本 ⇒ 恒发动",
    ),
    "hero.jie_zhangjiao_mobile.guidao": (
        _constant(1.0),
        "改判窗口：与两版【鬼才】同口径（有改判能力即参与，保守给正但不高）",
    ),
    "hero.jie_zhangjiao_mobile.huangtian": (
        _score_huangtian,
        "主公技：发动者是**别的座位**——交出一张【闪】/【闪电】的收益归张角、成本归自己；"
        "按主公的身份关系打分（明敌人不给、其余给正分）",
    ),
    "hero.zhangjiao_mobile.guidao": (
        _constant(1.0),
        "改判窗口：与两版【鬼才】同口径（有改判能力即参与，保守给正但不高）",
    ),
    "hero.zhangjiao_mobile.huangtian": (
        _score_huangtian,
        "主公技：发动者是**别的座位**——交出一张【闪】/【闪电】的收益归张角、成本归自己；"
        "按主公的身份关系打分（明敌人不给、其余给正分）",
    ),
    # huo-taishici-xunyu 票 02：火包移动版荀彧的两条
    "hero.xunyu.quhu": (
        _constant(1.5),
        "拼点：赢则让 A 打 B 1 点（B 的座位由 _HOSTILE_SEAT_STEPS 挑敌人），"
        "没赢自己挨 1 点——按期望给略正",
    ),
    "hero.xunyu.jieming": (
        _constant(2.5),
        "受伤后免费给一名角色把手牌补到 min(体力上限, 5)：净赚若干张，恒发动",
    ),
    # huo-taishici-xunyu 票 01：火包移动版太史慈【天义】——拼点赢则本阶段三条加成
    # （次数上限 +1 / 无距离 / 目标数 +1），没赢只是不能使用【杀】；净收益为正。
    "hero.taishici.tianyi": (
        _constant(2.0),
        "拼点：赢则本阶段三条【杀】加成（高收益），没赢只禁使用【杀】⇒ 恒该发动；"
        "选哪张牌由 _score_select_cards 的拼点分支按点数打分",
    ),
    # huo-dianwei-mobile 票 01：火包移动版典韦【强袭】
    "hero.dianwei_mobile.qiangxi": (
        _score_qiangxi,
        "1 点伤害换一张武器牌（净赚）或 1 点体力：有牌就弃牌；只能失血时只在能补刀 / "
        "体力充裕时发动，1 体力时自伤进濒死 ⇒ 不发动",
    ),
    # huo-mobile-batch 票 01：火包移动版庞德【猛进】
    "hero.pangde_mobile.mengjin": (
        _constant(2.0),
        "自己的【杀】被【闪】抵消后弃对方一张牌：代价 = 零（弃的是对方的牌），"
        "发动前提由 ACTIVATION_PLANS 保证（本方【杀】被抵消 ∧ 对方有牌）",
    ),
    # huo-mobile-batch 票 03：火包移动版袁绍【乱击】（血裔是锁定技，按纪律**不入表**）
    "hero.yuanshao_mobile.luanji": (
        _constant(2.0),
        "两张同花色手牌当【万箭齐发】：一次群体伤害，代价是两张手牌（价值表拉平后仍为正）",
    ),
    # huo-mobile-batch 票 02：火包移动版庞统【连环】/【涅槃】
    "hero.pangtong_mobile.lianhuan": (
        _constant(1.0),
        "一张♣手牌当【铁索连环】使用或重铸：多一条使用路径 + 一条重铸出口，保守为正；"
        "重铸分支的打分由 _score_reforge 按牌值扣项",
    ),
    # huo-wolong-yanliang 票 01：火包·OL 卧龙诸葛【火计】【看破】。
    "hero.wolong_zhugeliang.huoji": (
        _constant(2.0),
        "一张红色手牌当【火攻】：多一条**伤害**路径（1 点火焰），按牌价保守为正；"
        "代价是那张红手牌本身（转化来源进弃牌堆）",
    ),
    "hero.wolong_zhugeliang.kanpo": (
        _constant(2.0),
        "一张黑色手牌当【无懈可击】：只在无懈的收集式窗口出现（问到的都是锦囊要被抵消"
        "的时点），代价是那张黑手牌，恒为正",
    ),
    "hero.yanliang_wenchou.shuangxiong": (
        _constant(2.0),
        "摸牌阶段把 2 张额定摸牌换成 1 次判定 + 判定牌 + 本回合的颜色转化：代价是少摸"
        "一张，收益是**确定**拿到 1 张牌加一条【决斗】路径（判色事前未知 ⇒ 不按颜色"
        "条件化），保守为正",
    ),
    "hero.pangtong_mobile.niepan": (
        _constant(9.0),
        "限定技：濒死时弃区域所有牌、复原、摸三张、回复至 3 点——一局一条命，问到就发动",
    ),
    "hero.yuji_mobile.guohuo": (
        _constant(1.5),
        "扣置一张手牌当任意基本牌 / 普通锦囊：多一条使用路径，但要赌其他角色的质疑（"
        "假牌会被作废，保守为正）",
    ),
    "hero.xiahouyuan.shensu": (
        _score_shensu,
        "跳判定 + 摸牌换一张无距离【杀】：常态少摸两张不划算，只在敌方 1 血能补刀时发动",
    ),
    # jie-feng-batch2 票 01：界夏侯渊【神速】（同一条判据 + 多选面）。
    "hero.jie_xiahouyuan_mobile.shensu": (
        _score_shensu,
        "同标版：敌方 1 血能补刀才发动；发动后在多选面里只勾【1】（【2】多付装备+让出"
        "出牌阶段、【3】翻面白丢一个回合，都是负分）",
    ),
    "hero.xuchu.luoyi": (
        _constant(2.0),
        "少摸一张换本回合【杀】/【决斗】+1：进攻回合值得",
    ),
    "hero.zhenji.luoshen": (
        _constant(3.0),
        "准备阶段判定：黑色则白拿一张并可继续，红色才停——恒为正",
    ),
    "hero.zhenji.qingguo": (_constant(2.0), "黑色手牌当【闪】：多一条防御路径"),
    "hero.guojia.tiandu": (_constant(2.0), "获得此判定牌：额外一张手牌"),
    "hero.guojia.yiji": (_constant(4.5), "受伤后摸两张并可分配：净赚两张"),
    # 蜀
    "hero.liubei.rende": (_score_rende, "给牌是成本；首次给到两张以上回复 1 点 ⇒ 受伤时值得"),
    "hero.liubei.jijiang": (
        _constant(_RESPOND_VALUE),
        "主公技：①求助窗口视为打出【杀】（南蛮 / 决斗，免费）；②出牌阶段主动**使用**【杀】"
        "（票 20：目标由自己选、盟友代付那张牌，占自己的使用次数）——**免费**、只读"
        "`view` 的敌我估值（目标步登记为对手）；唯一的频率闸门是机械锁（同一出牌阶段里"
        "发动后直到出过【杀】或新阶段开始前不再出现），故常量=始终愿意",
    ),
    "hero.guanyu.wusheng": (_constant(2.0), "红色牌当【杀】：多一条进攻路径，按牌价保守为正"),
    "hero.zhaoyun.longdan": (_constant(2.0), "【杀】⇄【闪】互转：攻防两便"),
    "hero.machao.tieqi": (_constant(2.5), "使用【杀】后判定，通过则目标不可响应：助此【杀】命中"),
    "hero.zhugeliang.guanxing": (_constant(3.0), "准备阶段观看牌堆顶并按任意顺序放回：改善摸牌"),
    "hero.huangyueying.jizhi": (_constant(3.0), "使用非延时锦囊时摸一张：净赚"),
    # yijiang-2014-round3 票 03：曹真移动版【司敌】——两个自动预发动入口（①②各一问）。
    # jie-yijiang-2014-wuyi-caozhen 票 01：界吴懿移动版【奔袭】（用户 2026-11-18 签）。
    # 代价 = 弃置任意张（≥1），收益 = 本阶段距离 −X + 下一张牌多指定 + 一次摸五张的机会；
    # 收益通常远大于一张最便宜的牌 ⇒ 净正。
    "hero.jie_wuyi_mobile.benxi": (
        _constant(2.2),
        "出牌阶段开始时弃任意张（≥1）：本阶段距离 −X、下一张基本牌/普通锦囊可额外指定至多 X 名"
        "距离 1 的角色、那张牌造成过伤害再摸五张——有代价（弃置）但通常净赚",
    ),
    # jie-yijiang-2014-wuyi-caozhen 票 03：界曹真【司敌】（预测型；用户 2026-11-18 签）。
    # 无代价、② 是**强制兑现**（中了一律有收益：摸一张 / 二选一），故给正分；"预测得准不准"
    # 打分器读不到（预测目标不在任何载荷里），只按"这条技能还能不能发动"定价。
    "hero.jie_caozhen_mobile.sidi": (
        _constant(1.8),
        "① 使用非延时锦囊后免费指定一名敌人 + 预测其下一张牌的唯一目标（无代价、可反复攒）；"
        "② 命中即强制兑现：目标是自己 ⇒ 摸一张；否则「取消 + 对其 1 点伤害」或「摸两张」"
        "——两项都是净收益，故发动分永远为正（预测准确性不在打分器可见范围内）",
    ),
    "hero.caozhen_mobile.sidi": (
        _constant(2.0),
        "① 使用【闪】后把牌堆顶一张扣上武将牌（免费攒牌，通常净正）；② 别人的出牌阶段开始时"
        "移去一张令其本阶段【杀】上限 −1（控制对手输出，一张牌换一次限制）",
    ),
    # yijiang-2014-round3 票 01：一将成名-2014 吴懿【奔袭】——锁定技、零询问 ⇒ 这一条只服务
    # "这条技能值多少"的语义表（锁定技不入 `_COVERAGE_CASES`：没有可发动的一问）。
    "hero.wuyi.benxi": (
        _constant(0.0),
        "锁定技：本回合每用一张牌距离 −1；与所有其他角色距离均为 1 时无视防具、"
        "【杀】目标数 +1——恒生效、没有询问、没有代价（价值已在候选面里体现，不额外加分）",
    ),
    # 吴
    "hero.sunquan.zhiheng": (_score_zhiheng, "弃任意张摸等量：换牌质量（弃【桃】/【闪】扣分）"),
    "hero.ganning.qixi": (_constant(2.0), "黑色牌当【过河拆桥】：多一条拆牌路径"),
    "hero.lvmeng.keji": (_score_keji, "未出过【杀】就跳过弃牌阶段：保住超出上限的手牌"),
    "hero.huanggai.kurou": (_score_kurou, "失去 1 点体力换两张牌；1 点体力时不换（会濒死）"),
    "hero.zhouyu.yingzi": (_constant(2.5), "摸牌阶段多摸一张：净赚一张"),
    "hero.zhouyu.fanjian": (_score_fanjian, "交付一张牌令目标猜花色：猜错即受伤"),
    "hero.daqiao.guose": (_constant(2.0), "方块牌当【乐不思蜀】：多一条控制路径"),
    "hero.daqiao.liuli": (_score_liuli, "弃一张牌转移【杀】的目标：避免自己受击"),
    "hero.luxun.lianying": (_constant(3.0), "失去最后一张手牌后摸一张：补牌"),
    "hero.sunshangxiang.jieyin": (_score_jieyin, "弃两张手牌令双方各回复 1 点：自己回血"),
    "hero.sunshangxiang.xiaoji": (_constant(3.0), "失去装备后摸两张：净赚两张"),
    # 群
    "hero.huatuo.jijiu": (_constant(2.0), "红色牌当【桃】（限回合外）：多一条救命路径"),
    "hero.huatuo.qingnang": (_score_qingnang, "弃一张手牌令已受伤角色回复 1 点"),
    "hero.diaochan.lijian": (_score_lijian, "弃一张牌令两名男性决斗：伤害转嫁"),
    "hero.diaochan.biyue": (_constant(2.5), "结束阶段摸一张：净赚一张"),
    # M6 票 07：界甘宁 / 界貂蝉（否定分类四层的第一批真实载体）
    "hero.jie_ganning.qixi": (_constant(2.0), "黑色牌当【过河拆桥】：与标准【奇袭】同一条转化"),
    "hero.jie_ganning.fenwei": (
        _constant(2.5),
        "限定技：让多目标锦囊对若干目标无效——保护己方 / 削弱敌方的群体锦囊",
    ),
    "hero.jie_diaochan.lijian": (
        _score_lijian,
        "与标准【离间】同一条三步清单；虚拟【决斗】不可响应，收益更高",
    ),
    "hero.jie_diaochan.biyue": (_constant(2.5), "结束阶段摸一 / 两张：净赚"),
    # M6 票 08：手牌上限双轴 + 界周瑜 / 界夏侯惇 / 界黄月英
    "hero.jie_zhouyu.fanjian": (
        _constant(2.5),
        "交出一张手牌换取目标二选一：弃同花色牌或失去 1 点体力——多数时候是净赚",
    ),
    "hero.jie_xiahoudun.ganglie": (
        _constant(3.5),
        "受伤后判定：红打来源 1 点 / 黑拆来源一张——两条分支都为正",
    ),
    "hero.jie_xiahoudun.qingjian": (
        _constant(2.0),
        "别人回合获得牌后给牌换当前回合角色 +X 手牌上限：给低价值牌即可",
    ),
    "hero.jie_huangyueying.jizhi": (
        _constant(3.0),
        "使用非转化锦囊摸一张（延时类也触发）；摸到基本牌还能弃它换本回合 +1 手牌上限",
    ),
    # M6 票 09：牌标签的首个载体——界甄姬【洛神】（与标准版同一条循环 + 标签）
    "hero.jie_zhenji.luoshen": (
        _constant(3.0),
        "准备阶段判定：黑色则白拿一张并可继续，且获得的牌本回合不占手牌数",
    ),
    "hero.jie_zhenji.qingguo": (_constant(2.0), "黑色手牌当【闪】：与标准【倾国】同一条转化"),
    # M6 票 10：武将牌上的牌区的首个载体——界陆逊【谦逊】/【连营】
    "hero.jie_luxun.qianxun": (
        _score_qianxun,
        "别的角色的普通锦囊 / 延时锦囊对自己生效时把全部手牌扣上武将牌：躲开拆顺，回合末拿回。"
        "代价 = 这一回合手牌用不出来，1 体力且手里有续命牌（【闪】/【桃】/【酒】）时不发动"
        "（票 22；读 view.players[self].hp 与 view.self_hand）",
    ),
    "hero.jie_luxun.lianying": (
        _constant(2.5),
        "失去最后的手牌后令至多 X 名角色各摸一张：【谦逊】扣光手牌时白赚 X 张",
    ),
    # M6 票 11：额外回合 / 轮次的首个载体——界吕蒙【博图】
    "hero.jie_lvmeng.botu": (
        _constant(5.0),
        "执行一个额外的回合（完整六阶段）：一次免费的整回合，收益远高于一次用牌；"
        "「每轮限 X 次」由技能前提自己收住",
    ),
    # M6 票 20：票 04–11 的界将补齐语义条目（此前只有表 05–18 的批次，正向检查只看标准表
    # 时看不出来）。觉醒技（潜心 / 勤学）**强制发动**、引擎从不出示"是否发动"询问，条目取
    # 0.0 并在说明里点名；它们由构造测试与 M6 金录像覆盖，不是"AI 主动发动"。
    "hero.jie_guanyu.wusheng": (
        _constant(2.0),
        "红色牌当【杀】：与标准【武圣】同一条转化，多一条进攻路径",
    ),
    "hero.jie_guanyu.yijue": (
        _constant(2.5),
        "弃一张牌令目标展示一张手牌：红则拿走并可回复、黑则封住其技能与手牌——两条都为正",
    ),
    "hero.jie_machao.tieqi": (
        _constant(2.5),
        "使用【杀】后判定，通过则目标不可响应：与标准【铁骑】同一条助命中",
    ),
    "hero.jie_zhangfei.tishen": (
        _constant(3.5),
        "限定技：准备阶段回复至体力上限并摸『实际回复值』张——一次大回复 + 补牌",
    ),
    "hero.jie_xushu.zhuhai": (
        _constant(2.0),
        "其他角色的结束阶段对其使用一张【杀】：回合外的一刀，条件由距离 / 次数收住",
    ),
    "hero.jie_xushu.qianxue": (
        _constant(0.0),
        "觉醒技**强制发动**（引擎不出示『是否发动』询问）：条目只为满足正向检查，"
        "由构造测试与 M6 金录像覆盖",
    ),
    "hero.jie_xushu.jianyan": (
        _constant(2.5),
        "☆ 出牌阶段：看牌堆顶一张按类别 / 颜色亮出并令一名男性角色获得——净赚一张",
    ),
    "hero.jie_lvmeng.keji": (
        _score_keji,
        "未出过【杀】就跳过弃牌阶段：与标准【克己】同口径，保住超出上限的手牌",
    ),
    "hero.jie_lvmeng.qinxue": (
        _constant(0.0),
        "觉醒技**强制发动**（引擎不出示『是否发动』询问）：减上限后由选项步问摸两张 / 回复",
    ),
    "hero.jie_lvmeng.gongxin": (
        _constant(2.5),
        "☆ 出牌阶段：观看一名其他角色的手牌并处理其中一张红桃牌——信息 + 一张牌",
    ),
    # 票 01：神吕蒙移动版（与界吕蒙 ☆攻心共用同一条流水线；涉猎是阶段体 + 手写选牌）
    "hero.shen_lvmeng_mobile.gongxin": (
        _constant(2.5),
        "出牌阶段限一次：观看一名其他角色的手牌并处理其中一张红桃牌——信息 + 一张牌"
        "（与界吕蒙 ☆攻心同一条流水线、同一语义；座位步的敌我估值走 'gongxin:' 前缀）",
    ),
    "hero.shen_lvmeng_mobile.shelie": (
        _constant(2.0),
        "摸牌阶段放弃摸牌、改为亮五张并按每种花色至多取一张（0~4 张）：换掉固定的两张摸牌，"
        "取到 2 张以上就是净赚；亮牌公开、不额外读隐藏信息",
    ),
    # 本票：移动版界曹仁【据守】——结束阶段摸三张，然后翻面
    "hero.jie_caoren_mobile.jushou": (
        _score_jushou,
        "结束阶段摸三张、然后把武将牌翻面：三张牌是净赚，代价是下个回合被跳过。"
        "已翻面时这一次是翻回正面（不跳回合）⇒ 恒发动；正面且 1 体力 ⇒ Pass（票 22；"
        "读 view.players[self].flipped 与 hp）",
    ),
    # M6 票 14：界·魏 A（曹操 / 司马懿 / 张辽 / 许褚）
    "hero.jie_caocao.jianxiong": (
        _constant(4.0),
        "受伤后获得造成伤害的牌**并摸一张牌**：净赚两张，恒发动",
    ),
    "hero.jie_caocao.hujia": (
        _constant(_DODGE_SKILL_VALUE),
        "主公技：①求助窗口视为打出【闪】；②决定者（盟友）让主公白摸一张——两条都为正",
    ),
    "hero.jie_simayi.fankui": (
        _constant(3.5),
        "受到 1 点伤害后获得伤害来源的一张牌：逐点触发 ⇒ 每次伤害赚得更多",
    ),
    "hero.jie_simayi.guicai": (
        _constant(1.0),
        "改判窗口：与标准【鬼才】同口径（有改判能力即参与，保守给正但不高）",
    ),
    "hero.jie_zhangliao.tuxi": (
        _constant(3.5),
        "少摸任意张换等量张他人手牌：至多 2 张，牌数不变但换牌质量更高",
    ),
    "hero.jie_xuchu.luoyi": (
        _constant(2.5),
        "亮三张拿基本牌 / 武器牌 /【决斗】并放弃摸牌，换**跨回合**的【杀】/【决斗】+1",
    ),
    # M6 票 15：界·魏 B（郭嘉 / 李典）
    "hero.jie_guojia.tiandu": (_constant(2.0), "获得此判定牌：与标准【天妒】同一条收益"),
    "hero.jie_guojia.yiji": (
        _constant(4.5),
        "受伤后摸两张（分配是「至多」，AI 不在受伤时机白送手牌，见 Answer）",
    ),
    "hero.jie_lidian.xunxun": (
        _constant(2.5),
        "摸牌阶段看四张并把有用的两张置顶：改善自己接下来摸到的两张",
    ),
    "hero.jie_lidian.wangxi": (
        _constant(2.5),
        "造成 / 受到 1 点伤害后摸两张、交出其中一张：净赚一张且能补队友",
    ),
    # M6 票 16：界·蜀（刘备 / 赵云 / 诸葛亮）
    "hero.jie_liubei.rende": (
        _score_jie_rende,
        "给牌是成本；本阶段第二张换一次「视为使用一张基本牌」（AI 按给两张估）",
    ),
    "hero.jie_liubei.jijiang": (
        _constant(_RESPOND_VALUE),
        "主公技：①求助窗口视为使用 / 打出【杀】；②盟友回合外打出【杀】时令主公摸一张；"
        "③与标准版同一条出牌阶段主动**使用**【杀】的入口（票 20：免费，只读 `view` 的"
        "敌我估值，频率由机械锁兜住）",
    ),
    "hero.jie_zhaoyun.longdan": (
        _constant(2.0),
        "【杀】⇄【闪】与 酒⇄桃 四向互转：攻防与自救都多一条路径",
    ),
    "hero.jie_zhaoyun.yajiao": (
        _constant(2.5),
        "回合外使用 / 打出手牌时亮一张：类别相同则白拿一张，否则可拆对手一张",
    ),
    "hero.jie_zhugeliang.guanxing": (
        _constant(3.0),
        "准备阶段看牌堆顶五张（存活 < 4 时三张）；全置底还能在结束阶段再看一次",
    ),
    # M6 票 17：界·吴（孙权 / 黄盖 / 大乔 / 孙尚香）。【诈降】是锁定技（没有「是否发动」
    # 询问），由构造测试与"苦肉真的触发它"的 AI 场景覆盖，不在此表。
    "hero.jie_sunquan.zhiheng": (
        _score_zhiheng,
        "与标准【制衡】同一条单步清单；弃光所有手牌多摸一张",
    ),
    "hero.jie_sunquan.jiuyuan": (
        _score_jie_jiuyuan,
        "决定者 = 正在回复体力的吴势力角色：自己摸一张，把这次回复转给主公",
    ),
    "hero.jie_huanggai.kurou": (
        _score_jie_kurou,
        "弃一张牌 + 失去 1 点体力换【诈降】摸三张；1 点体力时不换",
    ),
    "hero.jie_daqiao.guose": (
        _score_jie_guose,
        "二选一（方块牌当【乐不思蜀】/ 弃方块并解掉场上一张），选择结束后摸一张",
    ),
    "hero.jie_daqiao.liuli": (
        _score_liuli,
        "与标准【流离】同一条两步清单：弃一张牌把【杀】转移给合法新目标",
    ),
    "hero.jie_sunshangxiang.jieyin": (
        _score_jie_jieyin,
        "成本 1 张牌：较高的摸一张、较低的回复 1 点；体力相同则两种效果都不执行",
    ),
    "hero.jie_sunshangxiang.xiaoji": (
        _constant(3.0),
        "与标准【枭姬】同一条零步钩子：失去装备区里的一张牌后摸两张",
    ),
    # 装备技（W4-4）：会问"是否发动"的 5 条非锁定装备都要有正分条目——缺省
    # ``_UNKNOWN_SKILL_VALUE = -1.0 < Pass(0)`` ⇒ 不入表就永不发动（雌雄无法复现的根因）。
    # 票 22 之前一律是常量正分（"正分 ⇒ 永远发动"，成本 / 收益不建模，见票面"不做收益模型
    # 调参"）；票 22 起【贯石斧】换成 `_score_axe`（成本 = 两张牌，净收益低于 Pass 不发动），
    # 其余四条仍是常量。
    "equip.double_sword": (_constant(2.0), "雌雄双股剑：令异性目标弃一张或你摸一张"),
    "equip.green_dragon_blade": (_constant(2.0), "青龙偃月刀：被抵消后再补一张【杀】"),
    "equip.axe": (
        _score_axe,
        "贯石斧：弃两张令被抵消的【杀】依然造成伤害。可能的斩杀 ⇒ 恒发动（击杀不扣牌价）；"
        "否则 1 点非致命伤害 − 这一问要付的两张牌价，净收益低于 Pass 就不发动（票 22；读"
        "action.picks / view.self_hand 的牌价与 PlayerPublic.hp 与 identity）。裸候选没有"
        "picks 时成本按'手里最便宜的两张'预估（装备区的牌不进预估），AI 自己填表时 picks 在",
    ),
    # 票 09：朱雀羽扇从「转化候选」（AI 在普通【杀】/ 火【杀】之间挑）改成**询问式**
    # （"当你使用普通【杀】时，可以将其转化为火【杀】"）⇒ 它变成会问"是否发动"的非锁定
    # 装备技，必须有自己的正分条目，否则 `_UNKNOWN_SKILL_VALUE` 让 AI 永不转化。
    # 恒发动：换的是**同一张牌**（不额外付成本），而火伤在【藤甲】/【铁索连环】上只增不减。
    "equip.vermilion_feather_fan": (
        _constant(2.0),
        "朱雀羽扇：把手里的普通【杀】转成火【杀】。不付额外成本，火伤只增不减 ⇒ 恒发动",
    ),
    "equip.qilin_bow": (_constant(2.0), "麒麟弓：造成伤害时弃目标的一匹坐骑"),
    "equip.ice_sword": (_constant(2.0), "寒冰剑：防止伤害并依次弃目标两张牌"),
    # W4-fix-2：丈八蛇矛的转化【杀】候选已收回，AI 改从清单填 —— 没有条目就落在
    # `_UNKNOWN_SKILL_VALUE = -1.0` 上、永不发动（五条转化技的覆盖判据会红）。
    "equip.serpent_spear": (_constant(2.0), "丈八蛇矛：两张手牌当【杀】使用或打出"),
    # M6 票 18：界·群（华佗 / 吕布 / 华雄 / 公孙瓒）。【无双】【耀武】【义从】是锁定技，
    # 没有「是否发动」询问，故不进本表；【急救】与标准版共用同一条转化声明，但仍需要
    # 自己的语义条目（三向同步检查按技能 id 逐条核）。
    "hero.jie_huatuo.jijiu": (_constant(2.0), "红色牌当【桃】（限回合外）：与标准【急救】同一条转化"),
    "hero.jie_huatuo.chuli": (
        _score_chuli,
        "弃自己与若干名势力互异角色各一张牌；黑桃牌的角色各摸一张（拆牌为主）",
    ),
    "hero.jie_lvbu.liyu": (
        _constant(3.0),
        "使用【杀】造成伤害后获得其区域内一张牌：非装备则其摸一张，装备则开一次决斗",
    ),
    "hero.jie_huaxiong.shizhan": (
        _score_shizhan,
        "令一名其他角色视为对我使用【决斗】：手里有【杀】才赌，1 点体力不赌",
    ),
    "hero.jie_gongsunzan.qiaomeng": (
        _constant(3.0),
        "使用【杀】造成伤害后选其区域里一张牌：坐骑获得、其余弃置（净赚）",
    ),
    # 票 03：风包黄忠【烈弓】——自己的出牌阶段里，条件成立的目标不能闪
    "hero.huangzhong.liegong": (
        _constant(3.0),
        "使用【杀】指定目标后封【闪】：条件多由当前体力 / 攻击范围自动满足，助这一刀命中",
    ),
    # 界限突破-风包·移动版（jie-feng-batch 票 01）
    "hero.jie_huangzhong_mobile.liegong": (
        _constant(3.0),
        "使用【杀】指定目标后：满足条件 1 封【闪】、满足条件 2 该目标受 +1 伤害；"
        "**没有成本**（两个判据都由引擎现算，AI 只答是否发动）⇒ 恒发动",
    ),
    # 界限突破-风包·移动版·界魏延（jie-feng-batch 票 02）
    "hero.jie_weiyan_mobile.kuanggu": (
        _constant(2.0),
        "造成 1 点伤害后（距离 ≤ 1）**每点各问一次**：发动后二选一（选项分值见 "
        "`_OPTION_VALUES`）；没有成本 ⇒ 恒发动",
    ),
    "hero.jie_weiyan_mobile.qimou": (
        _score_qimou,
        "限定技：体力 ≤ 1 时不发动（自伤会进濒死），否则正分；X 取最小（选项分值见 "
        "`_OPTION_VALUES`），距离 -X / 【杀】上限 +X 本回合有效",
    ),
    # 自制包·韬·刘备自制版（2026-10-31 票 01）
    "hero.tao_liubei.congtao": (
        _constant(4.0),
        "他人（或自己）的【杀】落到攻击范围内的其他角色时：观看牌堆顶两张、一张交给"
        "【杀】的使用者、另一张置入弃牌堆或放回牌堆顶——**没有成本**，最差也是把一张牌"
        "送给自己人，故恒发动",
    ),
    "hero.tao_liubei.yiqi": (
        _constant(5.0),
        "限定技：看弃牌堆顶 x 张、拿走其中一种类型的全部，并把武将牌换成【武圣】+【咆哮】"
        "——一次性转变，x ≥ 1 时发动到底（候选只在 x ≥ 1 时出现）",
    ),
    # lei-batch 票 01：郝昭【镇骨】——结束阶段选一名其他角色，两次结算都把手牌调成与
    # 我相同（我更多 ⇒ 他摸、我少 ⇒ 他弃）。代价为零、收益随局面走，读不出隐藏信息，
    # 故保守给正：结算方向按"我的手牌数 − 他的"能不能朝他有利可图来判（这里只做
    # "发起不亏"这一层，具体调多少由引擎在回合末现算）。
    "hero.haozhao.zhengku": (
        _constant(2.0),
        "结束阶段选一名其他角色，你的回合末与他的下个回合末各把手牌调成与你相同"
        "（摸牌终值至多 5）：没有代价、只调整对手手牌数，恒发动为正",
    ),
    # lei-batch 票 02：张绣【雄乱】/【从谏】。
    "hero.zhangxiu.xiongluan": (
        _constant(6.0),
        "限定技 + 代价 = 废除自己的判定区与装备区（永久，一次性）：换来本回合对其无距离"
        "与次数限制 + 其不能使用或打出手牌；限定技整局一次、且废除后发不动 ⇒ 恒发动到底"
        "（读 view 只判「还有区域可废」，候选不出现时根本不到这里）",
    ),
    # lei-shenganning 票 01：神甘宁【魄袭】/【劫营】。
    # 【魄袭】的收益全在"四色选牌"那一问（档位 0/1/3/4 都可能），而 `SKILL_VALUES` 只管
    # "要不要发动"——付不起也照发（弃置是**效果**、不是代价），四色凑不齐时出口是放弃，
    # 故这里恒给正值：不发动就一张都看不到。
    "hero.shen_ganning.poxi": (
        _constant(2.5),
        "出牌阶段限一次：看一名其他角色的手牌、再（可选）弃四色各一换 0/1/3/4 档效果。"
        "**没有代价**——弃置是效果不是成本，凑不出四色时出口是「放弃」 ⇒ 恒发动为正；"
        "读 `view.players`（座位步的敌我估值走 'poxi:' 前缀）与 `view.self_hand`"
        "（`legal_actions` 里的 ActivateSkill 骨架），四色选牌那一问由通用 SELECT_CARDS"
        "打分器按牌面处理",
    ),
    # 【劫营】只在**结束阶段**问（交出营），而营留在自己身上同样享受三条加成
    # （摸牌 +1 / 【杀】次数 +1 / 手牌上限 +1）：交给别人只是"到点拿走他的手牌"，
    # 收益取决于场面，故给一个保守的正值（交给别人后 ③ 至少能拿他一张手牌）。
    "hero.shen_ganning.jieying": (
        _constant(1.5),
        "结束阶段可以把营交给一名其他角色；他回合结束时我拿走其所有手牌"
        "（营的三条加成在我身上也照享）。**没有代价**（①③ 是强制段、不问）⇒ 保守给正；"
        "读 `view.players`（座位步走 'jieying:' 前缀：接下营的人是我要抢的对象）",
    ),
    # lei-batch 票 03：诸葛瞻【罪论】（非锁定；【父荫】是锁定技，不需要条目）。
    "hero.zhugezhan.zuilun": (
        _constant(3.0),
        "结束阶段看牌堆顶三张、按满足的选项数拿走 0~3 张（其余按序放回牌堆顶）："
        "拿到的张数只增不减，X=0 时还要与一名其他角色各失 1 点体力 ⇒ 恒发动为正",
    ),
    "hero.zhangxiu.congjian": (
        _constant(2.5),
        "成为多目标普通锦囊的目标时，交出一张牌换摸 1 张（装备牌换 2 张）：净赚 0 / 1 张、"
        "且给的对象是这张牌的其他目标（常为盟友），保守给正",
    ),
    # 自制包·韬·曹操（tao-caocao 票 01）：【鸿志】是锁定技（不问发动 ⇒ 不入表），
    # 【倡义】是"别人出牌阶段结束时主动问"的非锁定技。
    # 自制包·锋·于禁（feng-yujin 票 01 / 07 / 08）：两条都是"没有代价"的技能 ⇒ 恒发动为正；
    # 【定乱】的限次在目标侧（每名角色整局至多被定乱 1 次），于禁可以反复发动。
    "hero.feng_yujin.dingluan": (
        _constant(1.5),
        "每轮开始时（非共享：只问于禁）：展示一名角色的至多 X 张牌并标记"
        "【定乱】——不搬牌、不弃牌，没有代价；标记让那些牌不能当【杀】、进弃牌堆时其拥有者"
        "摸一张。候选出现即说明 X>0 且还有没被定乱过的角色 ⇒ 恒发动为正",
    ),
    # feng-guanyu 票 01：锋·关羽【单骑】——别人的准备阶段把**整手同色牌**压成一张【决斗】/ 火【杀】
    # 打向当前回合角色。牌越打越多，但每张都是"本来未必用得上"的牌；估值按"一张可用牌 + 一次
    # 消耗对手响应"给 1.5（颜色二选一的分值见 `_OPTION_VALUES` 的 `danqi:*`）。
    "hero.feng_guanyu.danqi": (
        _constant(1.5),
        "别人的准备阶段：把该色全部手牌当一张【决斗】/ 无距离限制的火【杀】对其使用；"
        "转化来源是整手同色牌，等于一次性兑现 ⇒ 恒为正（1.5）；颜色二选一的分值见"
        "`_OPTION_VALUES` 的 `danqi:black` / `danqi:red`",
    ),
    # feng-guanyu 票 01：锋·关羽【心汉】——觉醒技（**强制发动，不问**），这一条只是机械登记；
    # 第二段的甲乙丙丁 / 选座步各由 `_OPTION_VALUES` 与 `_SEAT_TARGET_VALUE` 打分。
    "hero.feng_guanyu.xinhan": (
        _constant(3.0),
        "觉醒技：累计造伤 + 回复 ≥ 6 时强制发动，改势力为蜀并可依次对不同角色执行四项"
        "（每项一次）。『是否发动』永远不问（`SkillType.AWAKENING`）⇒ 本条目只做机械登记；"
        "四项的分值见 `_OPTION_VALUES` 的 `yiyun:*`",
    ),
    "hero.feng_yujin.jieyue": (
        _constant(2.0),
        "出牌阶段限一次：令攻击范围内一名有牌的其他角色交给你一张牌（净赚一张），然后二选一"
        "（弃牌打 1 点伤害 / 标记并送还封杀）。发动本身无代价 ⇒ 恒为正；两条出口的分值见"
        "`_OPTION_VALUES` 的 `jieyue_damage` / `jieyue_mark`",
    ),
    # 陆抗移动版（lukang-mobile 票 01）：【决堰】用**永久**废一个装备槽换一条本阶段 / 本回合
    # 收益（恒有权衡 ⇒ 保守给正）；【破势】是觉醒技（强制发动、没有候选可挑）；☆【怀柔】是
    # 1 换 1 的牌质过滤（弃一张装备牌摸一张），略高于放弃。
    "hero.lukang_mobile.duanyan": (
        _constant(1.8),
        "永久废一个装备槽换本阶段 / 本回合一条收益：代价与收益都在明面上，保守给正；"
        "三栏的相对分见 `_OPTION_VALUES` 的 `duanyan_*`",
    ),
    "hero.lukang_mobile.poshi": (
        _constant(0.0),
        "觉醒技：条件满足即强制发动，没有候选可挑（0 分只表示'不参与选择'）",
    ),
    "hero.lukang_mobile.huairou": (
        _constant(0.6),
        "弃一张装备牌摸一张（牌质过滤，1 换 1）：略高于放弃，但不如直接出牌",
    ),
    # 山包·左慈（shan-baozuoci 票 04）：【化身】没有 SKILL_ACTIVATION 询问（① 是强制段、
    # ② 是强制钩子 + 选项流水线），条目只为三向同步纪律而存在；【新生】是"受伤后免费多一张
    # 化身牌"，候选出现即无代价 ⇒ 恒为正。
    "hero.zuoci.huashen": (
        _constant(0.0),
        "换化身没有直接牌收益（只改‘视为拥有’的技能与性别 / 势力）；① 强制、② 的候选由"
        "玩家在弹窗里挑，AI 这里只表示'不参与选择'（没有 是否发动 询问）",
    ),
    "hero.zuoci.xinsheng": (
        _constant(2.0),
        "受伤后随机获得一张新的化身牌（② 的候选池变大）：没有代价 ⇒ 恒发动为正",
    ),
    # shiji-zhi 票 01：始计篇-智包·神郭嘉（四条；A 族主动技不产生"是否发动"询问，
    # 这里表示"值得点"的常量分，并写清代价 / 读哪些字段）。
    "hero.shen_guojia.huishi": (
        _constant(3.0),
        "出牌阶段限一次：判定 + 每张花色不重复的判定都直接 +1 体力上限（含第一张，"
        "与是否继续无关），最后把全部判定牌交人；"
        "只有'收牌人手牌全场最多'才反扣 1 上限。代价是白送判定牌——AI 这一档不细算"
        "收牌人，按'多摸 1~4 张且涨上限'记正分（读 view 只用公开面：上限 < 10 才出现）",
    ),
    "hero.shen_guojia.hui_shi": (
        _constant(2.4),
        "限定技：把有未触发觉醒技的角色那条觉醒条件**标成满足**（它自己的时机到了才觉醒），"
        "否则其摸四张；两支都自减 2 上限。整局一次、收益 = 一次觉醒或 4 张牌 ⇒ 恒发动为正"
        "（读 view 只用公开面）",
    ),
    "hero.shen_guojia.tianyi": (
        _constant(0.0),
        "觉醒技：条件满足时强制发动（不问），效果纯收益（+2 上限 / 回复 1 / 授予 ☆佐幸）",
    ),
    "hero.shen_guojia.zuoxing": (
        _constant(2.8),
        "出牌阶段限一次：花神郭嘉 1 点体力上限换一张普通锦囊的'视为使用'——"
        "白得的锦囊（神郭嘉上限 > 1 才出现）⇒ 恒发动为正（读 view 只用公开面）",
    ),
    # shiji-zhi-luotong-chenzhen 票 02：始计篇-智包·陈震【歃盟】。
    "hero.chenzhen.shameng": (
        _constant(2.6),
        "出牌阶段限一次：弃两张同色手牌 ⇒ 目标摸 2、自己摸 3（净 +1 手牌、盟友 +2）⇒ "
        "没有代价、恒发动为正（读 view 只用公开面：候选出现即说明手里有两张同色、"
        "场上有别的存活角色）",
    ),
    "hero.tao_caocao.changyi": (
        _constant(2.0),
        "别人的出牌阶段结束时与其拼点：赢或平点没有代价（还能把【倡义】复制一份给对方），"
        "输了必须二选一——弃至少 X 张换 1 点伤害、或摸 X 张并失去此技能。两条出口都不致命，"
        "且每轮只有一次、拼点点数还能 +X（段 2 恒选）⇒ 恒发动为正（读 view 只用公开面："
        "候选出现即说明双方都有手牌、本轮还没用过）",
    ),
    # 林包·移动版·神曹操（lin-batch 票 01）：【归心】有一次发动 = 逐座位各拿一张 + 翻面
    # （toggle），故按"能拿几张 vs 跳过下个回合"权衡；【飞影】是锁定技 ⇒ 不要求条目
    # （`_missing_entries` 跳过 `definition.locked`，与马术 / 义从同一姿态）。
    # 林包·移动版·孙坚（lin-batch 票 02）：【英魂】没有代价（不弃牌 / 不失体力 / 无次数
    # 成本），候选只在"已受伤 + 有其他存活角色"时出现 ⇒ 常量即可；两段效果都由目标执行，
    # 收益的符号由座位步的敌对口径与 `_OPTION_VALUES` 承担（`_constant` 不读任何字段）。
    "hero.sunjian_mobile.yinghun": (
        _constant(2.5),
        "准备阶段且已受伤时选一名其他角色、选一项：①令其摸X张再弃1 ②令其摸1再弃X"
        "（X = 已损失体力值，牌数不足则弃光）。**没有代价**（不弃牌 / 不失体力 / 无次数"
        "成本），候选只在「已受伤 + 有其他存活角色」时出现 ⇒ 常量即正",
    ),
    # 一将成名-2014·沮授（jushou-lusu 票 01）：【渐营】是"使用牌与上一张同点数/同花色 ⇒
    # 摸一张"，**没有代价**（不弃牌 / 不失体力 / 不占次数），条件成立时净赚一张 ⇒ 常量即正。
    # 【矢北】是锁定技（照常"发动"、不问），按 `_missing_entries` 的既有口径**不必**登记。
    "hero.jushou.jianying": (
        _constant(2.5),
        "出牌阶段使用与上一张点数或花色相同的牌时摸一张：**没有代价**（不弃牌 / 不失体力 /"
        "无次数成本），候选只在条件成立时出现 ⇒ 常量即正",
    ),
    # 界一将成名-2014·移动版·界沮授（yijiang-2014-round2 票 01 / W3）：同一个技能 id 的两半
    # 共用这一条估值——① 与 OL 版逐字同句（摸一张、无代价 ⇒ 常量即正，抄上面那条）；
    # ② 是"消耗一张牌换一张基本牌的使用权 + ① 的一次摸牌"，`_constant` 读不到那张牌的牌价
    # （已知限制，与上面那条同档），故沿用同一个正值。锁定的【矢北】按 `_missing_entries`
    # 的既有口径**不必**登记。
    "hero.jie_jushou_mobile.jianying": (
        _constant(2.5),
        "① 使用与上一张点数或花色相同的牌时摸一张（无代价）；② 出一张牌当任意基本牌使用、"
        "产物被使用时 ① 必然再摸一张 ⇒ 两半都是正收益，常量即正",
    ),
    # 林包·移动版·鲁肃（jushou-lusu 票 02）：【好施】多摸两张、再按牌面把一半手牌交给
    # 「其他角色里手牌数最少」的那一名——交给谁由座位步（或并列时的手写座位问）决定，
    # 收益方向交给牌面的"最少"判据 ⇒ 本身没有代价，常量即正。
    # 【缔盟】是 A 族主动技：弃 X 张（X = 两人手牌数之差）换双方手牌互换——收益全看谁换给谁，
    # 由两步座位问的敌对口径承担（`_HOSTILE_SEAT_STEPS` 已登记该技能），弃置成本恒 ≤ 弃光，
    # 故常量给一个略高于"换牌"的正值。
    "hero.lu_su_mobile.haoshi": (
        _constant(2.5),
        "摸牌阶段多摸两张、再把一半手牌交给其他角色里手牌最少的那一名：**没有代价**"
        "（不弃牌 / 不失体力 / 无次数成本），交出后手牌仍 ≥ 3 ⇒ 常量即正",
    ),
    "hero.lu_su_mobile.dimeng": (
        _constant(1.5),
        "出牌阶段限一次：选两名其他角色、弃 X 张（X = 两人手牌数之差，可为 0、不足则弃光）"
        "令他们交换手牌。成本 ≤ 弃光自己的牌，收益 = 把敌人的手牌换给队友（座位步按敌对"
        "口径估）⇒ 略高于「不动」的正值",
    ),
    # 林包·移动版·徐晃（lin-batch 票 03）：【断粮】是**转化声明**（没有"是否发动"询问），
    # 这里登记的是它在出牌阶段清单里的"填表值"：源牌就是这次使用的牌，结算后进目标判定区，
    # 没有额外代价（不弃牌 / 不失体力 / 不占次数）⇒ 常量即正；目标由清单的座位步给
    # （`conversion=True` ⇒ 按敌我估值指向对手），故打分器不读任何字段。`standard_108` 档
    # 产物牌名不在牌堆 ⇒ 该档恒 0 条候选（本条目在那档根本不会被问到）。
    "hero.xuhuang_mobile.duanliang": (
        _constant(2.0),
        "出牌阶段把一张**黑色非锦囊牌**（手牌 / 装备区）当【兵粮寸断】使用，指定距离 ≤ 2 的"
        "一名其他角色。**没有代价**（源牌就是这次使用的牌，不额外弃牌 / 不失去体力 / 不占"
        "次数）⇒ 常量即正；可转的源牌由清单的来源步铺出（不读 `view.self_hand`），目标由"
        "座位步的敌我估值（`conversion` 标志）挑",
    ),
    "hero.shen_caocao_mobile.guixin": (
        _score_guixin,
        "受到 1 点伤害后**每点各问一次**：发动后按座位序从每名有牌的其他角色区域里各拿"
        "一张，然后**翻面**（toggle）。有代价：正面朝上时下个回合开始前翻回并**跳过整个"
        "回合**，背面朝上时反而是翻回正面的收益。读 `view.players` 的存活 / `hand_count` /"
        "`equip` / `judge` 判每座有没有牌可拿（`gains`），读 `view.players[self].flipped`"
        "判翻过去还是翻回来；挑哪张由 SELECT_CARDS 打分器按牌面处理（`action.picks` 不进"
        "这一问）。gains = 0 且正面朝上 ⇒ 不发（跳过回合不划算）",
    ),
    # 林包·移动版·曹丕（lin-batch2 票 01）：三条各自的口径见票面 `## AI 适配` 第 3 格。
    "hero.caopi_mobile.xingshang": (
        _constant(3.0),
        "当**其他角色**死亡时获得死者的手牌 + 装备区**全部**牌：**没有代价**（不弃牌 / 不失"
        "体力 / 无次数成本），候选只在「死者两区有牌 ∧ 终局未判定 ∧ 死者不是自己」时出现"
        "（条件挡掉空 / 已判胜）⇒ 常量即正。不读任何字段（拿几张由死者的牌数定，不在候选面上）",
    ),
    "hero.caopi_mobile.fangzhu": (
        _constant(2.5),
        "受到伤害后选一名其他角色：**先**翻面（toggle）**再**令其摸 X 张（X = 已损失体力值）。"
        "**没有代价**（不弃牌 / 不失体力 / 无次数成本），但收益**随座位是敌是友翻转** ——"
        "翻面 = 跳过其整个下个回合（控场收益），X 张是给它的补偿。故这里只给常量正分，"
        "**方向交给座位步**：`_HOSTILE_SEAT_STEPS` 已登记 `fangzhu:` ⇒ 指向敌方",
    ),
    "hero.caopi_mobile.songwei": (
        _constant(2.0),
        "主公技：其他魏势力角色的**黑色**判定牌生效后，判定者可以令**主公**摸一张。决定者"
        "零代价、受益者是主公本人（与界【护驾】② 同口径：它是 `_constant`，不读任何字段）。"
        "三个条件（判定者不是主公 / 同魏 / 黑色）全在 `_songwei_plan.available` 里，候选出现"
        "即已成立 ⇒ 常量即正",
    ),
    # 林包·移动版·董卓（lin-batch2 票 02）：只装两条**非锁定**技（肉林 / 崩坏是锁定技 ⇒
    # 没有「是否发动」询问，按纪律不入表）。口径见票面 `## AI 适配` 第 3 格 / Q7。
    "hero.dongzhuo_mobile.jiuchi": (
        _score_jiuchi,
        "出牌阶段把一张**黑桃手牌**当【酒】使用（濒死时同一条转化还能自救）。与真【酒】"
        "同一笔账（`_score_wine`）：只在『这一口酒正好凑成斩杀』时正分，否则负分（【酒】"
        "每回合限一次）；真正的候选打分走 `_score_card_use` 的 `wine` 分支，本条按语义表"
        "登记。默认 `standard_108` 档没有【酒】实体牌 ⇒ 该档恒 0 条候选",
    ),
    "hero.dongzhuo_mobile.baonue": (
        _score_baonue,
        "主公技：其他群势力角色造成伤害后**由它**决定是否判定（决定者 ≠ 受益者），黑桃 ⇒ "
        "主公回复 1 点。**关系感知**：读公开的主公身份（`_huangtian_lord_seat`）与自己的"
        "身份（`_me(view).identity`），主公在我的盟友集（`_ally_identities`）⇒ +1.5，"
        "否则 -1.0；主公 / 自己的身份读不到 ⇒ 0.0。不读任何隐藏信息",
    ),
    # 林包·移动版·贾诩（lin-batch2 票 03）：只装**限定主动技**【乱武】（完杀 / 帷幕是
    # 锁定技、没有「是否发动」询问，按纪律不入表）。口径见票面 `## AI 适配` 第 3 格 / Q9。
    "hero.jiaxu_mobile.luanwu": (
        _constant(2.5),
        "限定技，出牌阶段无代价发动：所有其他角色各一次「对最近者使用【杀】/ 失去 1 点"
        "体力」——全场互砍 / 掉血，净收益恒为正（整局一次 ⇒ 给得比普通主动技高）。"
        "那一问里'用【杀】还是失体力'按目标敌我另算（`_luanwu_respond_value`，Q9 = B）",
    ),
    # 界一将成名-2014·移动版·界陈群（jie-yijiang-2014-mobile 票 01）：【定品】弃一张牌
    # （大类没用过）→ 一名角色判定：黑 ⇒ 其摸 min(体力值,3) 张，♥ ⇒ 退还成本，♦ ⇒ 自己
    # 翻面。**有代价**：弃一张 + 四分之一的翻面风险；收益是四分之一的退款与二分之一的大摸。
    # 座位步选的是**受益者**（任意存活角色、含自己）⇒ 走 `_score_seat_option` 的"帮盟友"
    # 缺省分支，不登记 `_HOSTILE_SEAT_STEPS`。保守给略高于放弃的正分。
    "hero.jie_chenqun_mobile.dingpin": (
        _constant(1.2),
        "弃一张牌换一次判定：黑（1/2）让目标摸 min(体力值,3) 张、♥（1/4）退还成本、"
        "♦（1/4）自己翻面。代价与风险都在明面上，且座位步会挑盟友 ⇒ 保守给正；"
        "`_constant` 不读任何字段（牌面收益随判定走，AI 侧无法预判）",
    ),
    # 一将成名-2014·陈群（yijiang-2014 票 02）：【定品】弃一张**手牌**（大类没用过）→
    # 一名**已受伤**角色判定：黑（1/2）⇒ 其摸 **已损失体力值** 张（本版无"至多 3"的钳制、
    # 也不退成本），红（1/2）⇒ 自己翻面。与界版同形、收益措辞不同（X 更大、没有 ♥ 退款）。
    # 座位步选的仍是**受益者**（已受伤角色、含自己）⇒ 走 `_score_seat_option` 的"帮盟友"
    # 缺省分支，**不登记** `_HOSTILE_SEAT_STEPS`（登记成 "dingpin:" 前缀还会连带改掉界版，
    # 因为两版的 SELECT_OPTION 短 id 都是 `dingpin:seat`）。保守给略高于放弃的正分。
    "hero.chenqun.dingpin": (
        _constant(1.2),
        "弃一张手牌换一次判定：黑（1/2）让目标摸「已损失体力值」张（可 >3）、"
        "红（1/2）自己翻面（本版不退成本）。代价与风险都在明面上，且座位步会挑盟友 ⇒ "
        "保守给正；`_constant` 不读任何字段（牌面收益随判定走，AI 侧无法预判）",
    ),
    # 一将成名-2014·陈群（yijiang-2014 票 08 / W4b）：【法恩】**没有代价**（不弃牌 / 不失
    # 体力 / 不占次数）——有人在翻面 / 横置后摸一张，受益者是"刚变状态的那个人"（本技能
    # **不选座位**：目标由事件决定）⇒ 走缺省常量即可，**不登记** `_HOSTILE_SEAT_STEPS`
    # （那条表只收"被指向的对手"，本技能没有座位步可登记）。给得比自摸类低：受益者可能
    # 是敌方，而 AI 无从选择。
    "hero.chenqun.faen": (
        _constant(1.0),
        "一名角色翻面 / 横置后（无代价）令**其**摸一张：受益者由事件决定、AI 不选座位 ⇒ "
        "常量正分；`_constant` 不读任何字段",
    ),
    # 界一将成名-2014·移动版·界陈群（yijiang-2014-round2 票 11 / P2）：界版【法恩】——
    # 牌面与 OL 陈群【法恩】**逐字相同**、复用同一组回调 ⇒ 打分口径也逐字沿用上面那条：
    # **没有代价**、目标由事件决定、**不选座位** ⇒ **不登记** `_HOSTILE_SEAT_STEPS`
    # （那条表只收"被指向的对手"，本技能没有座位步可登记）。
    "hero.jie_chenqun_mobile.faen": (
        _constant(1.0),
        "一名角色翻面 / 横置后（无代价）令**其**摸一张：受益者由事件决定、AI 不选座位 ⇒ "
        "常量正分（与 OL 陈群【法恩】逐字同口径）；`_constant` 不读任何字段",
    ),
    # 界一将成名-2014·移动版·界顾雍（jie-yijiang-2014-mobile 票 02）：【慎行】弃两张换
    # 摸 1（同色）/ 摸 2（异色）——**牌质过滤**，净手牌 −1 或 0。弃哪两张由 `action.picks`
    # 的打分挑（AI 会丢低价值牌），故发动本身略高于放弃即可。
    "hero.jie_guyong_mobile.shenxing": (
        _constant(0.6),
        "弃两张摸一或两张（牌质过滤，净 −1 / 0）：弃哪两张由 picks 的打分挑低价值牌，"
        "故恒可发动但不宜给高；`_constant` 不读任何字段",
    ),
    # 界一将成名-2014·移动版·界顾雍（yijiang-2014-round2 票 13）：【秉壹】结束阶段展示全部
    # 手牌，颜色**或类别**全同则令至多 N（= 手牌数）名角色**各摸一张**——**没有代价**
    # （不弃牌 / 不失体力 / 不占次数），候选只在"手上有牌"时出现 ⇒ 常量即正（与 OL 顾雍
    # 【秉壹】同一口径：同一张座位面、同一份主体，界版只多"或类别"那一支）。座位步走缺省的
    # 「帮盟友」口径（选中的是被送牌的人）⇒ **不登记** `_HOSTILE_SEAT_STEPS`。
    "hero.jie_guyong_mobile.bingyi": (
        _constant(1.5),
        "结束阶段展示所有手牌：颜色或类别全同则令至多 N 名角色（N = 展示时的手牌数、含自己）"
        "各摸一张。**没有代价**，座位步走缺省的「帮盟友」口径 ⇒ 常量即正；"
        "`_constant` 不读任何字段",
    ),
    # 一将成名-2014·顾雍（yijiang-2014 票 03）：【慎行】弃两张**恒**摸一张——同样是
    # **牌质过滤**，但净手牌恒 −1（与界版的"同色摸 1 / 异色摸 2"不同，**没有**颜色分支），
    # 且**没有次数上限**（Q-G1 = A）。弃哪两张仍由 `action.picks` 的打分挑低价值牌。
    "hero.guyong.shenxing": (
        _constant(0.5),
        "弃两张恒摸一张（牌质过滤，净 −1；无颜色分支、无次数上限）：弃哪两张由 picks 的"
        "打分挑低价值牌，故恒可发动但不宜给高（净亏一张）；`_constant` 不读任何字段",
    ),
    # 一将成名-2014·顾雍（yijiang-2014 票 09 / W5b）：【秉壹】结束阶段展示全部手牌，颜色
    # 全同则令至多 N（= 手牌数）名角色**各摸一张**——**没有代价**（不弃牌 / 不失体力 /
    # 不占次数），且座位池**含自己**（Q-G2 = A），候选只在"手上有牌"时出现 ⇒ 常量即正。
    # 座位步**走缺省的「帮盟友」口径**（本技能对己方有益）：**不登记**
    # `_HOSTILE_SEAT_STEPS`——那条表收的是"被指向的对手"（伤害 / 拆牌 / 翻面类），
    # 这里选中的是被送一张牌的人。送的张数（≤ 手牌数）由座位多选面的勾选决定，
    # 打分器不读任何字段（`_constant`）。
    "hero.guyong.bingyi": (
        _constant(1.5),
        "结束阶段展示所有手牌：颜色全同则令至多 N 名角色（N = 展示时的手牌数、含自己）"
        "各摸一张。**没有代价**（不弃牌 / 不失体力 / 无次数成本），座位步走缺省的"
        "「帮盟友」口径（选中的是被送牌的人，不是对手）⇒ 常量即正；`_constant` 不读任何字段",
    ),
    # SP武将组8·刘焉（sp8-liuyan 票 01）：【图射】= 使用非装备牌指定目标后摸 X 张
    # （X = 本次使用的座位目标数，含自己）——**没有代价**、条件由引擎在候选阶段判过，
    # 故常量即正；【立牧】= 一张方片牌当【乐不思蜀】落自己判定区 + 回复 1 点，换来本回合
    # 无距离 / 无次数限制——回复与爆发窗口是即期收益、判定区那张是延时代价（判定不为红桃
    # 就跳过下一个出牌阶段），通常净正。
    # yijiang-2011-xusheng-chunhua 票 01：徐盛移动版【破军】（非锁定；归还段是标记载体的
    # 效果定义，不在武将技能清单里 ⇒ 不需要条目）。扣目标的牌到其武将牌上、回合末连本带利
    # 还回去——净收益 = "暂时少几张牌可用"（打空他的防御资源），没有自己的代价 ⇒ 常量即正。
    "hero.xusheng_mobile.pojun": (
        _constant(2.0),
        "使用【杀】指定目标后，可把该目标至多 X 张牌扣到它自己的武将牌上（X = 它的体力值），"
        "当前回合结束时再还给它：**没有代价**，条件已由引擎在候选阶段判过 ⇒ 常量即正；"
        "`_constant` 不读任何字段",
    ),
    # yijiang-2011-xusheng-chunhua 票 03：界徐盛移动版【破军】（① 扣牌 + ② 造成伤害 +1）：
    # 没有自己的代价，① 的条件由引擎在候选阶段判过、② 是自动的数值加成 ⇒ 常量即正，
    # 比徐盛版略高（多一段无条件的 +1）。
    "hero.jie_xusheng_mobile.pojun": (
        _constant(2.4),
        "使用【杀】指定目标后可扣其至多 X 张牌（X = 其体力值）到它自己武将牌上、当前回合末"
        "再还；② 对张数均不大于我的目标造成伤害 +1：**没有代价** ⇒ 常量即正",
    ),
    # yijiang-2011-xusheng-chunhua 票 02：张春华移动版【伤逝】（手牌数小于已损失体力值时
    # 补牌）。**没有代价**、条件由引擎在候选阶段判过（`_shangshi_needs_cards`），收益随
    # "缺口"变化但恒为正 ⇒ 常量即正。同批的【绝情】是锁定技（不问"是否发动"、没有候选），
    # 故不在本表（本表只收**非锁定**技能）。
    "hero.zhangchunhua_mobile.shangshi": (
        _constant(2.2),
        "手牌数小于已损失体力值时摸至该值：**没有代价**，条件已由引擎在候选阶段判过 ⇒ "
        "常量即正；`_constant` 不读任何字段",
    ),
    "hero.liuyan.tushe": (
        _constant(2.4),
        "使用非装备牌指定目标后摸 X 张（X = 本次使用的座位目标数，含自己）：**没有代价**，"
        "条件（非装备牌 ∧ 手牌无基本牌 ∧ 目标数 ≥ 1）已由引擎在候选阶段判过 ⇒ 常量即正；"
        "`_constant` 不读任何字段",
    ),
    "hero.liuyan.limu": (
        _constant(2.0),
        "把一张方片牌当【乐不思蜀】放到自己判定区、回复 1 点体力，换来本回合无距离 / 无次数"
        "限制（攻击范围内的其他角色）。代价 = 那张方片牌与下个判定阶段的延时风险，收益 = "
        "回血 + 不限次数的【杀】；通常净正，故给正分（`_constant` 不读任何字段）",
    ),
    # SP武将组1·SP孙尚香（sp1-sunshangxiang-maliang 票 02）：【良助】= 回复体力后二选一，
    # **没有代价**（不弃牌 / 不失体力 / 无次数成本），选项侧按上面两条 `_OPTION_VALUES` 定；
    # 【返乡】是觉醒技（强制发动、没有候选可挑）；☆【枭姬】与标准 / 界同一条零步流水线。
    "hero.sunshangxiang_sp.liangzhu": (
        _constant(1.5),
        "一名角色于其出牌阶段内回复体力后二选一（我摸一 / 令其摸两张）：**没有代价**，"
        "选项的相对价值由 `_OPTION_VALUES` 的 `liangzhu:*` 定 ⇒ 常量即正；"
        "`_constant` 不读任何字段",
    ),
    "hero.sunshangxiang_sp.fanxiang": (
        _constant(0.0),
        "觉醒技：条件满足即强制发动，没有候选可挑（加 1 上限 + 回复 1 点 + 失去【良助】+ "
        "获得 ☆【枭姬】）；`_constant` 不读任何字段",
    ),
    "hero.sunshangxiang_sp.xiaoji": (
        _constant(3.0),
        "失去装备区里的一张牌后摸两张（逐张触发）：**没有代价**，与标准 / 界【枭姬】"
        "同一份牌面与同一个回调 ⇒ 同一估值；`_constant` 不读任何字段",
    ),
    # SP武将组1·马良（sp1-sunshangxiang-maliang 票 01）：【自书】是锁定技（照常"发动"、
    # 不问），按 `_missing_entries` 的既有口径**不必**登记；这里登记是为了把两段语义写在
    # 一处（census 只强制非锁定条目）。【应援】= 把刚用过的那张牌交给一名其他角色
    # （每种牌名每回合一次）——**没有代价**（那张牌本来就要进弃牌堆），座位步走缺省的
    # 「帮盟友」口径（选中的是收牌的人）⇒ 常量即正。
    "hero.maliang.zishu": (
        _constant(0.0),
        "锁定技：没有「是否发动」询问，AI 无从选择。两段都是规则强制的：回合外获得的牌"
        "打标记、回合结束时弃置；自己的回合内获得牌就摸一张（一次获得多张只多摸一张）。"
        "`_constant` 不读任何字段",
    ),
    "hero.maliang.yingyuan": (
        _constant(1.5),
        "把刚使用并已进弃牌堆的那张牌交给一名其他角色（每种牌名每回合一次）：**没有代价**"
        "（那张牌本来就要弃），座位步走缺省的「帮盟友」口径（选中的是收牌的人，不是对手）"
        "⇒ 常量即正；`_constant` 不读任何字段",
    ),
    # 兵势篇-节包·势赵云（bingshi-jie 票 01）。
    # 【武翊】的「是否发动」有两处：出牌阶段那一问（A 族候选 / 清单）与【闪】窗口那一问
    # （提供者）。两处都是**不耗手牌**的虚拟牌 ⇒ 恒为正；座位步登记在
    # `_HOSTILE_SEAT_STEPS`（"wuyi:"），故 AI 拿这张虚拟【杀】打敌人、不落"帮盟友"缺省。
    "hero.shi_zhaoyun.wuyi": (
        _constant(1.8),
        "不耗手牌的虚拟【杀】/【闪】（每轮限一次）：白嫖一次攻击或一次保命，恒为正；"
        "`_constant` 不读任何字段",
    ),
    # 【摧锋】的目标由"当前回合角色"定死、AI 没有选择面；决斗有反伤风险（自己没【杀】时
    # 输掉就挨 1+X 点），故给一个**温和**的正分：宁可偶尔白发一次，也好过永远不发动。
    # 已知局限（与【崩坏】静态常量同一条纪律）：静态常量读不出"当前回合角色是队友"⇒
    # 1v1 下唯一其他座位就是敌人，多人局里可能在队友的回合末误发；要按敌我细算得另开 AI 票。
    "hero.shi_zhaoyun.cuifeng": (
        _constant(1.0),
        "每回合末无代价的虚拟【决斗】（伤害 1+X）：X 高时是收割手段，X 低时略亏（反伤）"
        "⇒ 温和正分；`_constant` 不读任何字段",
    ),
    # 谋攻篇-同包·谋刘赪（mou-gong-tong 票 01）。两条技能的**触发段都是强制段**
    # （牌面无「可以」⇒ `Hook.forced`，从不进 `SKILL_ACTIVATION` 的面）⇒ 本表这两条只是
    # 同步纪律要求的登记，打分器不会被问到。AI 真正要答的是 ① 末尾那两问：
    # 「是否视为使用」的选项值登记在 `_OPTION_VALUES`（`lveying:use` / `yingwu:use`），
    # 座位步登记在 `_HOSTILE_SEAT_STEPS`（全限定前缀）。
    "hero.mou_liucheng.lveying": (
        _constant(1.0),
        "强制触发（不问「是否发动」）：攒「椎」换成摸一张 + 虚拟【过河拆桥】，没有代价 ⇒ "
        "温和正分；`_constant` 不读任何字段",
    ),
    "hero.mou_liucheng.yingwu": (
        _constant(1.0),
        "强制触发（不问「是否发动」）：攒「椎」换成摸一张 + 无次数限制的虚拟【杀】，没有代价 "
        "⇒ 温和正分；`_constant` 不读任何字段",
    ),
    # 群英包·「锋势」陆逊（qunying 票 01）【谦逊】：A 族主动技（一步选代价 + 手写段机）。
    # 代价轻重由 `_OPTION_VALUES` 分档（弃光手牌最贵、掉 1 点血最便宜）；这里只回答
    # "要不要点这个技能"——有代价但换一张免费的牌 / 一次检索，温和正分。
    "hero.qunying_luxun.qianxun": (
        _constant(1.0),
        "代价换一次免费效果（视为使用一张非装备牌 / 移动场上一张牌 / 检索一张牌）；"
        "代价的贵贱在 `_OPTION_VALUES` 里分档 ⇒ 温和正分",
    ),
    # 群英包·「锋势」祢衡（qunying 票 05）【舌剑】：**有代价**——要付一张"点数大于牌堆顶"的
    # 手牌（置于牌堆顶，之后还能被摸到）；换一张不计次数、无视防具的雷【杀】。
    # 已知局限（AI 适配契约 §1 第 5 条）：每帧现算的**牌堆顶**不在 `PlayerView` 里（它只
    # 有 `deck_count` / `discard`），故 AI 看不见"该挑哪一张"，只能按 `_score_select_cards`
    # 的牌价挑一张合格牌；合格性由引擎的**过滤后选择面**保证（不合格的牌不进面）⇒ 合法性
    # 不受影响。`_constant` 不读任何字段。
    "hero.qunying_niheng.shejian": (
        _constant(1.3),
        "代价 = 一张点数大于牌堆顶的手牌（置顶后仍会被摸到）⇒ 付一张牌换一张无次数限制、"
        "无视防具的雷【杀】，温和正分；牌堆顶点数不在 `PlayerView` 里 ⇒ 挑哪张牌交给"
        "`_score_select_cards`（合格性由过滤后的选择面保证）；`_constant` 不读任何字段",
    ),
    # 【狂才】= 结束阶段的**盲猜**（类别 1/3、花色 1/4、点数 1/13 命中，命中则获得 3/4/13 张）：
    # 没有代价、期望收益约等于一张牌 ⇒ 温和正分；声明项的分档在 `_OPTION_VALUES` 里。
    "hero.qunying_niheng.kuangcai": (
        _constant(1.0),
        "没有代价的盲猜（类别 1/3、花色 1/4、点数 1/13），命中即获得 3/4/13 张牌 ⇒ "
        "期望约一张牌、温和正分；声明项分档在 `_OPTION_VALUES`；`_constant` 不读任何字段",
    ),
}
"""技能 id → ``(打分函数, 默认策略说明)``（`map.md` R4-Q6 的统一签名）。

**英雄侧只装非锁定武将技能**：锁定技没有 ``SKILL_ACTIVATION`` 询问（派发器直接跑），
故不要求条目。**装备侧**装那 5 条会问"是否发动"的非锁定装备技（雌雄 / 青龙 / 贯石 /
麒麟 / 寒冰）；【八卦阵】/【丈八蛇矛】/【方天画戟】没有"是否发动"询问（候选注入 /
修饰器），故仍走缺省保守 ``Pass``。三向同步断言住 ``tests/test_heuristic_ai.py``：
① 启用的非锁定武将技都在；② 那 5 条会问"是否发动"的非锁定装备技都在；③ 表里每个 id
都在数据文件里（武将技 ∪ 装备技）。加武将 / 装备忘了教 AI 会立刻判红。

**说明（第二列）必须写清两件事**（票 22，spec §6.5）：这条技能的发动**有没有代价**
（翻面 / 弃牌 / 扣光手牌 / 限定次数…）与打分器**读了哪些字段**（``view`` 的哪一项、
``action.picks`` 之类）。71 条常量条目的逐行盘点在 spec 附录 A——保持常量也是**裁定过的**
结果（"免费 / 无条件"），不是没看。
"""


# ---------------------------------------------------------------------------
# 参与者
# ---------------------------------------------------------------------------


class HeuristicPlayer:
    """规则启发式参与者：打分取最高，平分由 ``"ai"`` 子流打破（票 13 §结构）。

    随机面只走调用方交进来的 :class:`~sgs.effects.Randomness`（唯一随机源的 ``ai``
    子流），故同种子逐字节可复现、跨 ``PYTHONHASHSEED`` 稳定，且**不另造随机源**。
    """

    def __init__(self, rng: Randomness) -> None:
        self._rng = rng

    def respond(self, query: Query) -> Action:
        actions: list[Action] = list(query.legal_actions)
        # W4-4：预发动后复合动作**不在候选里**，AI 自己按清单填；同一个技能若也给了
        # 骨架候选（未删的被动入口），用填好的那份替换它——"确认 = 填表"是这一档的语义。
        composites = _preactivation_candidates(query)
        if composites:
            filled = {action.skill_id for action in composites}
            actions = [
                action
                for action in actions
                if not (
                    isinstance(action, ActivateSkill) and action.skill_id in filled
                )
            ]
            actions.extend(composites)
        # W5-02b：借刀的**外层两步清单**（附带指定）——复合使用同样不在候选里，
        # AI 读 ``card_preactivations`` 自己填 A / Bn；持刀者窗口的 a / b 由后续询问应答。
        actions.extend(_card_preactivation_candidates(query))
        # **不枚举档**多选（yijiang-2014-round2 票 21）：候选里只有一条骨架，AI 按
        # `_assembled_option_set` 自己拼合法集合来取代它（骨架不是答案集）；「至多」的
        # 放弃出口 ``Pass`` 仍是候选、照旧参评。
        if getattr(query, "structural", False) and isinstance(query, SelectOptionsQuery):
            assembled = _assembled_option_set(query)
            actions = [
                action for action in actions if not isinstance(action, SelectOptions)
            ]
            actions.append(assembled)
        if not actions:
            raise ValueError(f"{query.kind} 询问没有候选行动，参与者无法应答")
        scorer = _SCORERS[QueryKind(query.kind)]
        scores = [scorer(query, action) for action in actions]
        best = max(scores)
        winners = [action for score, action in zip(scores, actions) if score == best]
        return winners[self._rng.randbelow(len(winners))]
