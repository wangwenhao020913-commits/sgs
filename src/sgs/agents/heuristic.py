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
**不** import ``sgs.engine`` / ``sgs.skills`` / ``sgs.data`` / ``sgs.resolution`` /
``sgs.timing``，也不读 ``GameState``——"信息隐藏不是约定"这条因此是可断言事实
（静态扫描住 ``tests/test_source_discipline.py``）。
**不做行为 / 历史推断、不建决策时限**（ADR-011）：身份只用**公开信号**做配比后验
（I1，票 21，档 1）；拿不准时缺省保守，而不是猜。

**身份保守档（I1，票 21）**：敌友不再只有"主公 ↔ 反贼"这一对 0/1——已亮明的身份
（本人 / 主公 / 已阵亡 / ``identity_visible``）从 :data:`_IDENTITY_ROSTER` 里减掉，存活
未知座位按人头均分得到 ``(P(敌), P(友))``（:func:`_identity_relation`），替换约 8 个用点
的 0/1（伤害目标、座位步、连环、火攻、无懈、濒死救援）。``P(敌)`` **只作权重**、不升格成
"必须打谁"的硬判；内奸仍不把主公当优先敌人（主公留到最后）。算不出（人数不在 4~8 /
配比对不上）⇒ 退回中性的 0/0，与 I1 之前的行为一致。

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
``transform_preactivations`` 填 ``ActivateSkill(来源, 目标)``，由引擎按同一份内部候选池
解析成普通使用 / 打出（`sgs.skills.resolve_conversion_confirmation`）。

**技能语义表** :data:`SKILL_VALUES`：键 = 技能 id（含 ``hero.`` / ``equip.`` 前缀），值 =
``(打分函数, 默认策略说明)``；打分函数签名统一 ``(query, action, view) -> float``
（`map.md` R4-Q6）。表里没有的技能**缺省 Pass**（"拿不准就不发动"）。三向同步纪律
（`03` 的「技能分类与过滤」：加武将 / 装备忘了教 AI 必须立刻红）住
``tests/test_heuristic_ai.py``：① 启用的每名武将的每个**非锁定**技能都有条目；
② 会问"是否发动"的每条非锁定装备技都有条目；③ 表里每个 id 都在数据文件里存在。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sgs.effects import Randomness, behavior_for, is_slash, target_sets_match
from sgs.state import (
    Action,
    ActivateSkill,
    ArrangeCards,
    ArrangeQuery,
    CardRef,
    DiscardCards,
    DiscardQuery,
    DodgeQuery,
    EndPhase,
    Pass,
    PeachQuery,
    PlayCard,
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
    SkillActivationQuery,
    UseCard,
    WindowCause,
)

__all__ = ["SKILL_VALUES", "HeuristicPlayer"]

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

_LORD_SIDE = frozenset({"lord", "loyalist"})
_REBEL_SIDE = frozenset({"rebel", "renegade"})
_LORD = "lord"
_LOYALIST = "loyalist"
_REBEL = "rebel"
_RENEGADE = "renegade"
"""身份的**字面量**（:class:`~sgs.data.Identity` 是 ``str`` 枚举，故可直接比）。

同 :data:`_PLAY_PHASE_IDLE` 的理由：``Identity`` 住在 ``sgs.data``，不在本模块的可见面
白名单里。``frozenset`` 只作成员判定、从不迭代（源码纪律禁止的是迭代）。
"""

_RENEGADE_ENEMIES = frozenset({_LOYALIST, _REBEL})
"""内奸的对手：忠臣与反贼。**主公不在里面**——内奸要最后一个人活着，主公早死反贼就赢。

这是"不把主公当优先敌人"的唯一落点：敌友概率（:func:`_identity_relation`）按这张集合
算，故主公对内奸恒是 ``(0, 0)``（既不是敌人也不是盟友），与票 21 之前的内奸分支同义。
"""

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

_ENEMY_FOCUS = 2.0
"""伤害目标里"敌人"的加权：乘 ``P(敌)``（I1；已明敌人是 1.0、未知座位按配比），
小到不盖过手牌防御贴现，只作同价时的偏向。"""

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

取值只需盖过"两个**非斩杀**目标之间"的常见分差——已明敌人（:data:`_ENEMY_FOCUS` 2.0）
与手牌贴现（:data:`_HAND_DEFENSE_WEIGHT` × :data:`_HAND_DEFENSE_CAP` = 1.0）之和 3.0——
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
    """这一下落到 ``seat`` 身上是不是**当场打死**（``_damage_to >= 目标体力``）。

    这是**唯一的斩杀判据**（D2，票 19）：:func:`_score_attack` 的斩杀项问"现在就够不够"，
    :func:`_wine_enables_kill` 问"加【酒】才够、现在不够"——两条都读这里，不各写一份
    ``_damage_to`` 比较（改伤害模型时只有一处会漂）。``wine`` 语义同 :func:`_damage_to`：
    ``None`` 读当前状态，``True`` 推演"这一口已经喝下"。

    只读公开面：目标体力（公开）与 :func:`_damage_to`（藤甲 / 白银狮子 / 【酒】/ 古锭刀都在
    它里面）。**不**读目标手牌牌面：这一刀会不会被【闪】抵消是概率问题，归 C1（记牌那票），
    本票的"斩杀"只回答"实际伤害够不够"。
    """
    return _damage_to(card, view, seat, wine=wine) >= view.players[seat].hp


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


def _trick_expectation(query: Query, action: Action, score: float) -> float:
    """把"目标可能用【无懈可击】取消"折进这张锦囊的分（C2 的**唯一**落点）。

    只在 :func:`_score_card_use` 调用：``_score_attack`` 也被本模块别处直接调（测试 / 将来
    的单卡估值），锦囊的取消期望属于"出牌阶段用这张牌"这一步，不掺进纯伤害模型。
    """
    if not isinstance(action, UseCard) or action.card.type not in _NULLIFIABLE_TRICKS:
        return score
    view = query.view
    others = [
        seat
        for seat in action.targets
        if seat != view.seat and 0 <= seat < len(view.players)
    ]
    if len(others) != 1:
        return score
    return _expected_trick_score(score, _nullification_probability(view, others[0]))


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
# 每类询问一个 scorer：``(query, action) -> float``
# ---------------------------------------------------------------------------


def _score_attack(action: Action, view: PlayerView) -> float:
    """伤害期望：目标非己方恒正；每一点**预期**伤害与"躲掉也消耗一张牌"各自计价。

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
    if not targets:
        return _DAMAGE_BASE
    me = view.seat
    others = [
        seat
        for seat in targets
        if seat != me and 0 <= seat < len(view.players) and view.players[seat].alive
    ]
    if not others:
        return -_SAVE_VALUE
    card = getattr(action, "card", None)
    score = _DAMAGE_BASE + _TARGET_BONUS * len(others)
    for seat in others:
        hand_defense = _HAND_DEFENSE_WEIGHT * min(
            view.players[seat].hand_count, _HAND_DEFENSE_CAP
        )
        damage = _damage_to(card, view, seat)
        if damage <= 0:
            score -= _WASTED_ATTACK_PENALTY
            score -= hand_defense
        else:
            dodge_probability = (
                _dodge_probability(view, seat) if _defended_by_dodge(card) else None
            )
            if dodge_probability is None:
                score += _DAMAGE_POINT_VALUE * (damage - 1)
                score -= hand_defense
            else:
                score += _expected_attack_score(damage, dodge_probability)
        # 身份后验（I1）：P(敌) 只作权重——主公对反贼是 1.0，未知座位按配比均分（5 人局
        # 主公眼里 3/4）；不升格成"必须打谁"的硬判，也不给 P(友) 扣分（与 I1 之前一样，
        # 打盟友没有额外惩罚）。小到不能盖过"手牌越多越难命中"的贴现。
        score += _ENEMY_FOCUS * _identity_relation(view, seat)[0]
        if _lethal_to(card, view, seat):
            score += _expected_lethal_score(view, seat)
    return score


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
    return _WINE_LETHAL_VALUE if _wine_enables_kill(query) else _WINE_WASTE_PENALTY


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
        return 0.0
    elemental = _elemental_ready(view)
    score = 0.0
    for seat in targets:
        if not 0 <= seat < len(view.players):
            continue
        enemy, ally = _identity_relation(view, seat)
        if view.players[seat].chained:
            score += _IRON_CHAIN_RESET_FRIENDLY_VALUE * ally
            # 常量名是 ``..._PENALTY``（M6 P0 之前这一行写成了不存在的 ``..._VALUE``：该分支
            # 在启用池 16 名时没被随机对局走到，扩到 19 名后第一次命中就 NameError；回归
            # 测试见 `tests/test_m5_ai_military.py`）。
            score -= _IRON_CHAIN_RESET_ENEMY_PENALTY * enemy
        else:
            score -= _IRON_CHAIN_EXPOSE_FRIENDLY_PENALTY * ally
            if elemental:
                score += _IRON_CHAIN_CHAIN_ENEMY_VALUE * enemy
    return score


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
        return _FIRE_ATTACK_NO_SPARE_PENALTY
    score = _FIRE_ATTACK_BASE + _FIRE_ATTACK_SPARE_WEIGHT * min(
        spare, _FIRE_ATTACK_SPARE_CAP
    )
    for seat in others:
        if _wears(view.players[seat], _VINE_ARMOR):
            score += _FIRE_ATTACK_VINE_BONUS
        score += _ENEMY_FOCUS * _identity_relation(view, seat)[0]
    return score


def _score_reforge(action: Action) -> float:
    """【铁索连环】重铸：换一张牌（不是使用、不占次数、不触发使用锦囊类技能）。"""
    assert isinstance(action, Reforge)
    del action
    return _REFORGE_VALUE


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
    """
    assert isinstance(action, (UseCard, PlayCard))
    view = query.view
    card = action.card
    if _damage_card(card):
        score = _score_attack(action, view)
    elif card.type == "wine":
        score = _score_wine(query, view)
    elif card.type == "peach":
        score = _HEAL_VALUE if _wounded(view) else 0.0
    elif card.type == "iron_chain":
        score = _score_iron_chain(action, view)
    elif card.type == "fire_attack":
        score = _score_fire_attack(action, view)
    elif card.slot is not None:
        score = _equip_setup_score(query, card)
    else:
        score = _TRICK_VALUES.get(card.type, _UNREGISTERED_TRICK_VALUE)
    return _trick_expectation(query, action, score)


def _score_play_phase(query: Query, action: Action) -> float:
    """``PLAY_PHASE``：能造成伤害且目标非己方 ＞ 装备 ＞ 过牌；技能查语义表。

    次序不是死的：装备在"先装后杀才斩得掉"时会被抬到那条【杀】之前（D4，票 25），
    见 :func:`_equip_setup_score`。
    """
    view = query.view
    if isinstance(action, EndPhase):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _skill_value(query, action)
    if isinstance(action, Reforge):
        return _score_reforge(action)
    if isinstance(action, (UseCard, PlayCard)):
        return _score_card_use(query, action)
    return 0.0


def _score_dodge(query: Query, action: Action) -> float:
    """``DODGE``：能抵消就抵消（响应牌不占使用次数）。"""
    if isinstance(action, Pass):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _DODGE_SKILL_VALUE
    if isinstance(action, (UseCard, PlayCard)) and action.card.type == "dodge":
        return _DODGE_VALUE
    return 0.0


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


def _score_respond(query: Query, action: Action) -> float:
    """``RESPOND``：能抵消就抵消；技能类提供者（【护驾】/【激将】）同价。

    **无懈窗口是唯一的例外**（票 18 / spec §6.1 D1）：先按 :func:`_nullify_declares`
    判"该不该宣"；不该宣时给无懈候选 :data:`_NULLIFY_ABSTAIN_VALUE`（**严格低于**
    ``Pass``），由最大值直接选出 ``Pass``。其余窗口（【南蛮】/【万箭】/【决斗】/借刀 /
    势力求助 / 改判）仍是"有牌就抵消"的常量分——它们没有"取消对谁有利"的二义性。
    """
    if isinstance(action, Pass):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _DODGE_SKILL_VALUE
    if isinstance(action, (UseCard, PlayCard)):
        if _nullify_declares(query) is False:
            return _NULLIFY_ABSTAIN_VALUE
        return _RESPOND_VALUE
    return 0.0


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
        return 0.0
    if isinstance(action, ActivateSkill):
        return _peach_save_value(query)
    if not isinstance(action, (UseCard, PlayCard)) or action.card.type not in _RESCUE_CARD_TYPES:
        return 0.0
    return _peach_save_value(query)


def _score_discard(query: Query, action: Action) -> float:
    """``DISCARD``：弃价值最低（【桃】/【闪】最后弃，装备按槽位价值排序）。"""
    del query
    if not isinstance(action, DiscardCards):
        return 0.0
    return -sum(_card_value(card) for card in action.cards)


def _skill_value(query: Query, action: ActivateSkill) -> float:
    """技能收益查 :data:`SKILL_VALUES`；表里没有 ⇒ 保守（低于 Pass）。"""
    entry = SKILL_VALUES.get(action.skill_id)
    if entry is None:
        return _UNKNOWN_SKILL_VALUE
    scorer, _note = entry
    return scorer(query, action, query.view)


def _score_skill_activation(query: Query, action: Action) -> float:
    """``SKILL_ACTIVATION``：查技能语义表，缺省 ``Pass``（拿不准就不发动）。"""
    if isinstance(action, Pass) or not isinstance(action, ActivateSkill):
        return 0.0
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
        return _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
    committed = _is_committed_skill_step(query)
    short = _step_skill_short(query) if committed else ""
    fire_attack_discard = not committed and _is_fire_attack_discard(query)
    total = 0.0
    for index in action.picks:
        if not 0 <= index < len(query.slots):
            return _ILLEGAL_PENALTY
        slot = query.slots[index]
        if fire_attack_discard:
            total += _FIRE_ATTACK_DISCARD_VALUE - _card_value(slot.card)
            continue
        total += _step_card_score(
            slot.card, slot.ref.seat, query.view.seat, short, view=query.view
        )
    if short in _GIVE_STEPS and len(action.picks) >= 2 and _wounded(query.view):
        total += _RENDE_RECOVER_VALUE
    return total


_OPTION_VALUES: Mapping[str, float] = MappingProxyType(
    {
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
    }
)


def _enemy_identities(mine: str) -> frozenset[str]:
    """``mine`` 这个身份眼里的**敌人身份集合**。

    逐项等于票 21 之前的 :func:`_is_revealed_enemy`：主公侧 = 反贼 / 内奸、反贼 = 主公侧、
    内奸 = 忠臣 / 反贼（**不含主公**，见 :data:`_RENEGADE_ENEMIES`）。
    """
    if mine in _LORD_SIDE:
        return _REBEL_SIDE
    if mine == _REBEL:
        return _LORD_SIDE
    if mine == _RENEGADE:
        return _RENEGADE_ENEMIES
    return frozenset()


def _ally_identities(mine: str) -> frozenset[str]:
    """``mine`` 这个身份眼里的**盟友身份集合**（内奸没有盟友；按**阵营**算，不是按人）。

    比票 21 之前的 :func:`_is_revealed_ally` **宽一档**：旧函数只认"主公与我同在主公阵营"，
    这里认整个同阵营（主公侧的忠臣 / 反贼一方的反贼）。今天只有主公与 ``identity_visible``
    会亮明身份，故扩宽是**潜伏**的；一旦 M5 的亮明效果让忠臣 / 反贼露牌，按阵营记盟友才是
    对的——那条语义由 :func:`_identity_relation` 的"已亮明 ⇒ 精确 0/1"分支承载。
    """
    if mine in _LORD_SIDE:
        return _LORD_SIDE
    if mine == _REBEL:
        return frozenset({_REBEL})
    return frozenset()


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

    自己恒 ``(0.0, 1.0)``；``P(敌) + P(友) ≤ 1``，剩下的质量是"两边都不是"（反贼眼里的
    内奸、内奸眼里的主公）。**``P(敌)`` 只作权重**：调用方乘在原 0/1 的幅度上，不把它
    升格成"必须打谁"的硬判（spec §6.4）。
    """
    if seat == view.seat:
        return 0.0, 1.0
    if not 0 <= seat < len(view.players):
        return 0.0, 0.0
    mine = view.players[view.seat].identity
    if mine is None:
        return 0.0, 0.0
    enemies = _enemy_identities(mine)
    allies = _ally_identities(mine)
    theirs = view.players[seat].identity
    if theirs is not None:
        return (
            1.0 if theirs in enemies else 0.0,
            1.0 if theirs in allies else 0.0,
        )
    remainder = _unknown_identity_remainder(view)
    if remainder is None:
        return 0.0, 0.0
    remaining, unknown = remainder
    if unknown <= 0:  # pragma: no cover - 未知座位自己就是其中一个，防御分支
        return 0.0, 0.0
    return (
        sum(1 for identity in remaining if identity in enemies) / unknown,
        sum(1 for identity in remaining if identity in allies) / unknown,
    )


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
    # 攻心（票 01；界吕蒙 ☆ / 神吕蒙移动版共用）：看谁的牌 + 可能弃 / 置顶他的牌 ⇒ 对手。
    # 不登记会落进"帮盟友"的缺省分支（`docs/agents/ai-adaptation.md` §1 第 2 条点名的坑）。
    "gongxin:",
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
        return 0.0
    if not 0 <= seat < len(view.players):
        return 0.0
    enemy, ally = _identity_relation(view, seat)
    skill = (
        str(skill_id)
        if skill_id is not None
        else str(getattr(query, "skill_id", "") or "")
    )
    if conversion or skill.startswith(_HOSTILE_SEAT_STEPS):
        return _SEAT_TARGET_VALUE * enemy
    if skill.startswith(_STOLEN_CARD_SEAT_STEPS):
        return _STOLEN_CARD_SEAT_VALUE + (
            _SEAT_TARGET_VALUE - _STOLEN_CARD_SEAT_VALUE
        ) * enemy
    if skill.startswith(_YIJI_GIVE_STEP) and seat == view.seat:
        return _YIJI_SELF_GIVE_VALUE
    return _SEAT_TARGET_VALUE * (ally - enemy)


def _score_select_option(query: Query, action: Action) -> float:
    """``SELECT_OPTION``：座位步按敌我估值、其余按特征表贪心；未知选项一律 0。

    「取消一次已发动的技能」（发动后流水线里的 ``Pass``）是例外：给它一个惩罚，
    否则 AI 会在座位步挑「取消」，再回头重新发动同一条技能 —— 空转（见
    :func:`_is_committed_skill_step`）。
    """
    assert isinstance(query, SelectOptionQuery)
    if not isinstance(action, SelectOption):
        return _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
    if action.choice.startswith(_SEAT_PREFIX):
        return _score_seat_option(query, action.choice)
    return _OPTION_VALUES.get(action.choice, 0.0)


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
    """
    picks: list[int] = []
    choices: list[str] = []
    prior: list[str] = []
    short = str(skill_id).rsplit(".", 1)[-1]
    multi_target: tuple[object, tuple[str, ...]] | None = None
    for step in sorted(steps, key=lambda item: item.order):
        row = _pool_row_for(step, tuple(prior))
        if row is None:
            return None
        options = tuple(row.options)
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
        manifests = {str(skill_id): steps} if skill_id and steps else {}
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
        return 0.0
    order = tuple(action.order)
    if sorted(order) != list(range(len(query.revealed))):
        return _ILLEGAL_PENALTY
    if not 0 <= action.split <= len(order):
        return _ILLEGAL_PENALTY
    size = len(order)
    return sum(
        _card_value(query.revealed[order[index]]) * (size - index)
        for index in range(action.split)
    )


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
        QueryKind.ARRANGE: _score_arrange,
    }
)
"""选路表：**九类询问各一个 scorer**（键集恰等于 ``QueryKind``，由测试钉住）。"""


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
                    "直接 fail-closed（票 21）",
                ),
                "equip": (True, "装备区牌面：防具 / 武器 / 白银狮子的实际伤害；也进 ``_visible_cards``"),
                "judge": (True, "判定区是公开信息，进 ``_visible_cards``（选牌步的可见面）"),
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
                    "(P(敌), P(友))——伤害目标 / 座位步 / 连环 / 火攻 / 无懈 / 濒死救援都读它"
                    "（救援一侧：C2 起 _rescue_probability 用它算「谁愿意救濒死者」）；"
                    "算不出就退回中性的 (0, 0)，不做行为 / 历史推断",
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
                "transform_preactivations": (True, "转化技清单（候选已收回）：填 ``ActivateSkill(来源, 目标)``"),
                "transform_actions": (True, "被收回的转化候选：确认路径的成员判定（清单从它派生）"),
                "kind": (True, "选路键：``_SCORERS[QueryKind(query.kind)]`` 与候选生成的派发"),
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
                    "（D1，``_nullify_declares``）；其余窗口仍是常量'能抵消就抵消'",
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
        return value

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
        return bonus - _picked_value(view, action)
    if not view.self_hand:
        return 0.0
    if _wounded(view) and len(view.self_hand) >= 2:
        return _RENDE_RECOVER_VALUE - _skeleton_cost(view, 2)
    return -_skeleton_cost(view, 1)


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
        return bonus - _picked_value(view, action)
    if not view.self_hand:
        return 0.0
    if len(view.self_hand) >= 2:
        return _JIE_RENDE_BASIC_VALUE - _skeleton_cost(view, 2)
    return -_skeleton_cost(view, 1)


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
        return _CYCLE_VALUE * len(action.picks) - _picked_value(view, action)
    mine = view.players[view.seat]
    values = sorted(_card_value(card) for card in (*view.self_hand, *mine.equip))
    count = sum(1 for value in values if value < _CYCLE_VALUE)
    if count == 0:
        # 没有值得换的牌，但 ``lo=1`` 仍逼着弃一张 ⇒ 发动只会白丢最便宜的那张。
        # 给**负分**（而不是 0.0）：否则与"结束阶段"打平，AI 会随机把限一次的机会浪费掉。
        return _CYCLE_VALUE - values[0] if values else 0.0
    return _CYCLE_VALUE * count - sum(values[:count])


def _score_keji(query: Query, action: Action, view: PlayerView) -> float:
    """【克己】（M6 票 03）：跳过弃牌阶段 ⇒ 保住"超出体力值"的那几张手牌。

    收益按保住的张数估（每张约一张未知牌的价值）：手牌数 − 当前体力值。
    不超出上限时不发动（0 分 ⇒ 与 Pass 打平，由下一层的"放弃"胜出）。
    """
    del query, action
    me = view.players[view.seat]
    return float(max(0, me.hand_count - me.hp)) * _unknown_card_value(view)


def _score_kurou(query: Query, action: Action, view: PlayerView) -> float:
    """【苦肉】（M6 票 03）：失去 1 点体力换两张牌；**1 点体力时不换**（再失去即濒死）。

    标准版没有"每阶段一次"⇒ 这个打分会被反复问：体力 > 1 时每次都是正分，
    掉到 1 点后转为负分，AI 因此不会把自己苦肉死。
    """
    del query, action
    if view.players[view.seat].hp <= 1:
        return -_unknown_card_value(view)
    return 2.0 * _unknown_card_value(view)


def _score_qingnang(query: Query, action: Action, view: PlayerView) -> float:
    """【青囊】：弃一张手牌令**已受伤**角色回复 1 点（= 3 点收益 − 牌价）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - _picked_value(view, action)


def _score_fanjian(query: Query, action: Action, view: PlayerView) -> float:
    """【反间】：交付一张牌，目标猜错花色则受伤（期望为正）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 2.5 - _picked_value(view, action)


def _score_lijian(query: Query, action: Action, view: PlayerView) -> float:
    """【离间】：弃一张牌令两名男性角色决斗（把伤害转给别人）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - _picked_value(view, action)


def _score_jieyin(query: Query, action: Action, view: PlayerView) -> float:
    """【结姻】：弃两张手牌，双方各回复 1 点（自己回血是主要收益）。

    票 03 起候选是**骨架**（``picks`` 为空，成本改由发动后的 ``SELECT_CARDS`` 挑）⇒
    按"手里最便宜的两张"预估成本，否则裸候选看起来免费、AI 无脑发动（胜率回归）。
    自己没受伤时收益只剩"替目标回血"（目标还可能是敌人），故大幅降值。
    """
    assert isinstance(action, ActivateSkill)
    del query
    bonus = 4.0 if _wounded(view) else 1.0
    return bonus - _skill_cost(view, action, 2)


def _score_liuli(query: Query, action: Action, view: PlayerView) -> float:
    """【流离】：弃一张牌把【杀】转移给合法新目标（候选存在即意味着有目标）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - 0.5 * _picked_value(view, action)


def _score_jie_kurou(query: Query, action: Action, view: PlayerView) -> float:
    """界【苦肉】（M6 票 17）：弃一张牌 + 失去 1 点体力，换来【诈降】**摸三张**。

    净牌数 = +2（摸三张、弃一张），另有"本阶段红【杀】无距离 / 不可闪 / 次数 +1"；
    1 点体力时不换（再失去即濒死）。成本按骨架候选预估一张最便宜的牌。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if view.players[view.seat].hp <= 1:
        return -_unknown_card_value(view)
    return 3.0 * _unknown_card_value(view) - _skill_cost(view, action, 1)


def _score_jie_guose(query: Query, action: Action, view: PlayerView) -> float:
    """界【国色】（M6 票 17）：二选一 + 稳定摸一张。

    选项 1 是把方块牌变成【乐不思蜀】（控制），选项 2 是弃两张牌解掉场上的控制；
    两者之外**恒摸一张**，故即使方块牌本身是成本，净收益仍为正。
    """
    assert isinstance(action, ActivateSkill)
    del query, view
    return 2.0


def _score_jie_jieyin(query: Query, action: Action, view: PlayerView) -> float:
    """界【结姻】（M6 票 17）：成本 1 张牌，较高的摸一张、较低的回复 1 点。

    自己受伤时回复归自己（主收益）；都不受伤且体力相同 ⇒ 两种效果都不执行，纯亏成本，
    故降值。成本按骨架候选预估一张最便宜的牌。
    """
    assert isinstance(action, ActivateSkill)
    del query
    bonus = 4.0 if _wounded(view) else 1.0
    return bonus - _skill_cost(view, action, 1)


def _score_jie_jiuyuan(query: Query, action: Action, view: PlayerView) -> float:
    """界【救援】（M6 票 17）：决定者是**正在回复体力的那个吴势力角色**。

    收益 = 自己**确定摸一张**，外加把这次回复转给主公（对主公阵营是好事；不做敌我模型，
    按 D16"平衡读数只记录、不设阈值"给一个常量为正）。不发动则 0 分。
    """
    assert isinstance(action, ActivateSkill)
    del query, view
    return 2.0


def _score_chuli(query: Query, action: Action, view: PlayerView) -> float:
    """界华佗【除疠】（M6 票 18）：自己弃一张、每名目标各弃一张；黑桃还有摸牌。

    净收益 = 拆掉对手若干张 − 自己的成本一张；黑桃摸牌不打折（不明牌面，按保守常数）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    return 2.0 - 0.5 * _skeleton_cost(view, 1)


def _score_shizhan(query: Query, action: Action, view: PlayerView) -> float:
    """界华雄【势斩】（M6 票 18）：令其他角色视为对我使用【决斗】。

    手里有【杀】才有得打：一张也能赢"对方打不出【杀】"的局面，两张以上才扛得住一次
    来回。1 点体力时不做这笔冒险（决斗输掉即濒死）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    me = view.players[view.seat]
    if me.hp <= 1:
        return -_unknown_card_value(view)
    slashes = sum(
        1
        for card in view.self_hand
        if _is_slash_card(card)
    )
    return 1.0 if slashes else -_unknown_card_value(view)


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
        return _JUSHSOU_FLIPPED_VALUE
    if me.hp <= 1:
        return _JUSHSOU_LAST_STAND_VALUE
    return _JUSHSOU_FACE_UP_VALUE


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
        return _AXE_KILL_VALUE
    return _AXE_SALVAGE_VALUE - _skill_cost(view, action, 2)


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
        return _QIANXUN_LAST_STAND_VALUE
    return _QIANXUN_VALUE


_HAND_REQUIRED_SEAT_STEPS: frozenset[tuple[str, str]] = frozenset(
    {
        # 【义绝】的目标必须"有手牌"（规则 §十九 L2106：选择一名有手牌的其他角色）。
        ("hero.jie_guanyu.yijue", "seat"),
    }
)
"""座位步要求"目标有手牌"的技能（**公开的** ``hand_count`` 可判，不读暗牌）。

引擎的候选池**故意不按手牌收缩**（【义绝】的池只按"存活 / 不是自己"定：展示步为空时
整步取消、提交点之前就停，重入不会因目标已被拿走手牌而误取消）。代价是 AI 可能选中
一个空手目标：展示步空面 ⇒ 取消 ⇒ 不落「阶段已用」位 ⇒ 同一个出牌阶段里反复发动同一
个空操作（32 种子混合池扫里真出现过）。故 AI 用**公开**的手牌数避开空手目标。
"""


SKILL_VALUES: dict[str, tuple[Callable[[Query, Action, PlayerView], float], str]] = {
    # 魏
    "hero.caocao.jianxiong": (_constant(4.0), "受伤后获得造成伤害的牌：净赚一张，恒发动"),
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
    "hero.liubei.jijiang": (_constant(_RESPOND_VALUE), "主公技：视为使用/打出一张【杀】"),
    "hero.guanyu.wusheng": (_constant(2.0), "红色牌当【杀】：多一条进攻路径，按牌价保守为正"),
    "hero.zhaoyun.longdan": (_constant(2.0), "【杀】⇄【闪】互转：攻防两便"),
    "hero.machao.tieqi": (_constant(2.5), "使用【杀】后判定，通过则目标不可响应：助此【杀】命中"),
    "hero.zhugeliang.guanxing": (_constant(3.0), "准备阶段观看牌堆顶并按任意顺序放回：改善摸牌"),
    "hero.huangyueying.jizhi": (_constant(3.0), "使用非延时锦囊时摸一张：净赚"),
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
        "主公技：①求助窗口视为使用 / 打出【杀】；②盟友回合外打出【杀】时令主公摸一张",
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
        if not actions:
            raise ValueError(f"{query.kind} 询问没有候选行动，参与者无法应答")
        scorer = _SCORERS[QueryKind(query.kind)]
        scores = [scorer(query, action) for action in actions]
        best = max(scores)
        winners = [action for score, action in zip(scores, actions) if score == best]
        return winners[self._rng.randbelow(len(winners))]
