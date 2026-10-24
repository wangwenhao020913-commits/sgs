"""对局状态：玩家、牌实例、区域与结算栈的帧类型。

状态只用**有序容器**（``list`` / ``tuple`` / ``dict``，``dict`` 按插入序）：
``set`` / ``frozenset`` 不得作为字段出现——它们的迭代序逐进程变化，会破坏
"同种子逐字节复现"（票 08 §7）。牌实例的 ``card_id`` 就是 ``cards`` 里的下标，
一旦开局分配就不再变化；洗牌只置换区域列表。

本模块只装**状态**：行为字段（使用时机 / 目标规则 / 次数限制 / 效果列表）归 ``effects``，
记录流 schema 归 ``engine``。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from sgs.data import Color, Gender, Identity, Kingdom, RuleConfig

__all__ = [
    "CardRef",
    "FrameKind",
    "GameState",
    "MoveReason",
    "Phase",
    "Player",
    "SkillSuppression",
    "SkillSuppressionPredicate",
    "SkillSuppressionScope",
    "SlotKind",
    "Zone",
    "ZoneRef",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillSuppressionScope(str, Enum):
    """技能失效记录的**作用域**（M6 票 04；`GLOSSARY.md`「技能失效」）。

    M6 只有一档：``TURN`` = 本回合（回合末由 ``Engine._clear_turn_effects`` 清掉）。
    记录带作用域而不是裸布尔，是因为"失效可恢复"的那一半取决于**何时**恢复——条件撤销
    可能是回合末、也可能是某张牌离场；把作用域放进记录，清理点就不必逐技能特判。
    """

    TURN = "turn"


class SkillSuppressionPredicate(str, Enum):
    """技能失效记录的**谓词**：它挡住该座位的哪些技能实例（M6 票 04）。

    ``ALL_NON_LOCKED`` = "其所有非锁定技失效"（【义绝】/【铁骑】的牌面用语）。
    谓词是**座位级**的：一条记录覆盖该座位名下此刻与**此后获得**的全部非锁定技
    （票面 D10 的"失效率不逐技能展开"）；锁定技不受它影响。
    """

    ALL_NON_LOCKED = "all_non_locked"


class SkillSuppression(_FrozenModel):
    """一个座位的**失效记录**（M6 票 04）：``GLOSSARY.md``「技能失效」的实例形状。

    ``skill_id`` = **施加失效的那条技能**的实例 id（【义绝】/【铁骑】）——不是被挡住的
    技能；一条记录覆盖整个座位，故这里不会逐技能展开。"谁让它失效的"由这条技能实例的
    持有者推出，不另存一个座位号。
    """

    skill_id: str
    scope: SkillSuppressionScope = SkillSuppressionScope.TURN
    predicate: SkillSuppressionPredicate = SkillSuppressionPredicate.ALL_NON_LOCKED


class SlotKind(str, Enum):
    """装备区里的五个槽位（票 08 §2）；取值与数据层的 ``subcategory`` 同形。

    宝物槽位属界限突破，**预留不启用**（ADR-006）；M2 只可能见到前四个。
    """

    WEAPON = "weapon"
    ARMOR = "armor"
    HORSE_DEFENSE = "horse_defense"
    HORSE_OFFENSE = "horse_offense"
    TREASURE = "treasure"


class CardRef(_FrozenModel):
    """内联的牌面事实（票 06 §3）。

    ``card_id`` 是机器键；牌面（牌名 / 花色 / 点数）是渲染所需的事实，不是表现文本。
    ``slot`` 是装备槽位这一**牌面事实**（票 08 §2）：由 ``CardType.subcategory`` 在实例化时
    一次派生，使移动原语不必拿到 ``ContentPack``。非装备牌恒为 ``None``。
    ``attack_range`` 同为**牌面事实**（票 09 §6 / 票 12 §1）：由 ``CardType.attack_range``
    在实例化时一次派生，使 ``attack_range()`` 真读武器牌而不必回头拿内容包；非武器恒为
    ``None``。两者都是"渲染 / 校验要读的事实"，不是行为字段。

    票 30 的虚拟牌 / 转化牌：``card_id < 0`` 是虚拟牌的**唯一判据**（票 12 §Q21）。
    虚拟牌不参与牌守恒与区域互斥（它从不进任何区域），故 ``suit`` / ``rank``
    **可空**、``color`` 由花色派生或显式给出（纯虚拟牌三者皆无）。实体牌仍由
    :func:`sgs.data.color_of_suit` 在实例化时一次派生颜色，不另抄映射。
    """

    card_id: int
    type: str
    name: str
    suit: str | None = None
    rank: int | None = None
    slot: SlotKind | None = None
    attack_range: int | None = None
    color: Color | None = None
    virtual_sources: tuple[int, ...] = ()
    """转化牌的**来源实体牌 id**（票 12 §Q21 的"一条 delta 带两张"）。

    纯虚拟牌为空元组（无实体来源，如【八卦阵】视为使用/打出的【闪】）；
    转化牌（【丈八蛇矛】两张手牌当【杀】）装上被转化的实体牌 id，使用结界里据此把
    来源牌按正常使用路径移动（手牌 → 处理区 → 弃牌堆），而虚拟牌本身不落任何区域。
    """

    @property
    def is_virtual(self) -> bool:
        """虚拟牌 / 转化牌：``card_id < 0``（票 12 §Q21 的判据）。"""
        return self.card_id < 0


class Zone(str, Enum):
    """实体牌所在的区域种类（票 08 §1）。

    ``HAND`` / ``EQUIP`` / ``JUDGE`` 是**逐座位**的区，座位在 :class:`ZoneRef` 坐标里；
    ``DECK`` / ``DISCARD`` / ``PROCESSING`` 是共享区。
    """

    DECK = "deck"
    HAND = "hand"
    DISCARD = "discard"
    PROCESSING = "processing"
    EQUIP = "equip"
    JUDGE = "judge"


@dataclass(frozen=True, slots=True)
class ZoneRef:
    """**区域坐标**（票 08 §1）：区种类 + 座位。

    ``seat`` 只对 ``HAND`` / ``EQUIP`` / ``JUDGE`` 有意义，共享区为 ``None``。
    坐标说的是"**区域**在谁那里"，不是"牌归谁"——判定区的牌坐标带座位，但牌本身
    仍"不为任何角色所拥有"（规则 11.6）；"归谁"由 :class:`CardMoveDelta.owner` 回答。

    冻结值类型（用 ``dataclass`` 而非 pydantic）：pydantic 把 stdlib dataclass 字段
    序列化成 ``{"zone": ..., "seat": ...}``（``seat`` 显式写出，含 ``null``），
    与 ``CardRef`` 的平铺形状一致，且允许位置构造 ``ZoneRef(Zone.HAND, 0)``。
    """

    zone: Zone
    seat: int | None = None


class MoveReason(str, Enum):
    """**来源**：牌实例移动到当前区域的操作原因（票 08 §4）。

    官方清单只界定**角色**失去牌的原因；牌堆方向的移动（摸牌）须另列。
    洗牌走 ``delta_type = deck_shuffle``，**不**占这里的取值。
    """

    DRAW = "DRAW"
    USE = "USE"
    DISCARD = "DISCARD"
    PUT = "PUT"
    """置入：手牌 / 牌堆 → 判定区或装备区（`17.10` B1"置入相应区域"）。"""
    JUDGE = "JUDGE"
    """判定：牌堆 → 处理区 → 弃牌堆（亮判定牌与判定牌生效后入弃牌堆，`11.6`）。"""
    LOSE = "LOSE"
    """失去：判定区 / 装备区 → 弃牌堆或他处（替换失去旧装备、拆 / 顺，`4.10` / `11.5`）。"""
    REFORGE = "REFORGE"
    """重铸（M5-03）：手牌 → 弃牌堆，**不算弃置、算失去**（§5.1.3）。

    "失去"一侧的判据与 :class:`MoveReason` 无关（``move_after`` 只看区域与移动后的持有），
    故 REFORGE 照常触发【连营】/【枭姬】；"弃置"类触发若出现，必须显式比较原因、把
    REFORGE 排除在外。事件流据此写「重铸」，不写「弃置」。
    """


class Phase(str, Enum):
    """回合的六个阶段（票 01 L2）。"""

    PREPARE = "prepare"
    JUDGE = "judge"
    DRAW = "draw"
    PLAY = "play"
    DISCARD = "discard"
    FINISH = "finish"


class FrameKind(str, Enum):
    """结算事件的帧类型（票 04 §6）。

    前八个是 M2 真的会走到 `push` 的结算单元（``JUDGE`` 自票 26 从占位转真帧）；
    ``MOVE`` 自 M5-03 起承载**重铸**的"弃置 + 摸一张"；其余是留位（触发它们的机制在
    本里程碑被排除），只作 ``frame`` 记录 ``frame_kind`` 的合法取值。
    """

    # M2 会走到
    USE = "USE"
    RESOLVE = "RESOLVE"
    DAMAGE = "DAMAGE"
    LIFE_DECREASE = "LIFE_DECREASE"
    NEAR_DEATH = "NEAR_DEATH"
    DEATH = "DEATH"
    RECOVER = "RECOVER"
    JUDGE = "JUDGE"
    MOVE = "MOVE"
    # 留枚举位
    PLAY = "PLAY"
    LIFE_LOSE = "LIFE_LOSE"
    MAXLIFE = "MAXLIFE"
    RANK_COMPARE = "RANK_COMPARE"
    SKILL = "SKILL"


class Player(BaseModel):
    """一个座位。``seat`` 就是它在 :attr:`GameState.players` 里的下标。"""

    model_config = ConfigDict(extra="forbid")

    seat: int
    hp: int
    hp_max: int
    alive: bool = True
    hand: list[int] = Field(default_factory=list)
    equip: list[int] = Field(default_factory=list)
    """装备区（一个整体区）；列表序 = 槽位枚举序（票 12 §Q2），每槽至多一张。"""
    judge: list[int] = Field(default_factory=list)
    """判定区（**先入在前**）；"最后置入"的读取端用 ``reversed()`` 表达（票 08 §8）。"""
    chained: bool = False
    """是否横置（铁索连环）。

    U1-04 先落座位字段；**M5-03 接线**：写入唯一入口是 ``Engine.set_chained``（铁索连环的
    toggle 与属性伤害的"结算后立即重置"），每次变化发一条 ``chained_change`` delta。
    """
    wine_active: bool = False
    """【酒】的"下一张【杀】+1"是否生效（M5-04 接线：出牌阶段的【酒】写入，回合末清空；
    每次变化发一条 ``wine_change`` delta）。"""
    luoyi_active: bool = False
    """【裸衣】的"本回合【杀】/【决斗】伤害 +1"是否生效（M6 票 01）。

    写入唯一入口 ``Engine.set_luoyi_active``（许褚的摸牌阶段写入、回合末清空）；读点是
    :func:`sgs.skills.phase_body._luoyi_damage_bonus` 的 ``DAMAGE_DEAL_AMOUNT`` 回调。
    **不发 delta**：与 ``wine_active`` 不同，目前没有任何 JSON 读者（网页座位卡不读它、
    记录流不写它），回放按同一条 ``SKILL_ACTIVATION`` 动作重跑即可重建；等出现 UI 读取方
    时再补 ``luoyi_change`` delta（同时要登记 no-leak 判据）。
    """
    card_use_counts: dict[str, int] = Field(default_factory=dict)
    """本回合各**计数键**的已用次数；该座位自己的 ``TURN_START`` 重置（票 07 §6）。

    只存总已用次数：额定 / 额外两个上限记在行为表里，M1 的判定只用总数（票 07 §6）。
    键通常是牌型 id，也可以是行为表声明的**共用键**（M5-02：普通【杀】/ 火【杀】/
    雷【杀】共用 ``"slash"``）；读写的唯一真源是 ``CardBehavior.count_key``。
    """
    slash_in_play_phase: bool = False
    """本回合**出牌阶段内**是否使用或打出过【杀】（M6 票 03 的【克己】）。

    写在**当前回合角色自己的出牌阶段**里（``Engine.start_use`` 的"使用"与
    ``Engine.play_card`` 的"打出"两个入口）：判据是牌面事实（``is_slash``，火 / 雷 /
    转化 / 虚拟【杀】同族）∧ ``state.phase is Phase.PLAY`` ∧ ``source == state.turn_seat``。
    复位点是 :meth:`Engine._phase_play_start`（每个出牌阶段开始时清掉，被【乐不思蜀】
    跳过时也清），故"本阶段"的作用域不跨回合。

    **不发 delta**：与 ``luoyi_active`` 同姿态——没有 JSON 读者，记录流里的使用 / 打出
    动作已足以在回放时重建它。
    """
    identity: Identity | None = None
    """座位级身份（票 13 §3）：无身份局恒 ``None``，身份局由 ``build_initial`` 一次发放。

    判定一律比枚举成员；主公在开局即亮明（投影侧负责可见性，票 13 §6）。
    """
    identity_visible: bool = False
    """是否已对**所有人**亮明身份（U1-04）。

    引擎状态而不是投影派生：主公在 ``_initial_player`` 置 ``True``；阵亡亮明仍由投影按
    ``alive`` 派生（既有口径），未来 M5 的"亮明"效果写这个字段即可，不必改投影。
    **不发 delta**（M5 不新增亮明效果前它只在开局写一次），故标准 47 条金录像逐字节不变。
    """
    gender: Gender | None = None
    """座位级性别（票 30 / 票 05 §7.1 的【雌雄双股剑】判据）。

    性别是**武将牌**的事实；无武将模式 ⇒ 恒 ``None``（"未知"），"异性"判据因此不成立。
    **接口位**：由武将数据填值的落点属票 02 的发放段（本票尚未落地），装备效果的形状不变。
    """
    hero: str | None = None
    """座位级**武将 id**（票 01 §载体接入 / 票 02 的发放段）。

    ``None`` 专指**未发放武将**（无武将模式与人为局面的默认），与"暗置武将"（国战）
    不是一回事。载体解析**已读它**：``resolve_instances`` 据此取该武将的技能清单
    （``sgs.data.hero_skill_ids``），武将技能因此与装备技能同构地进链；把它填上的
    发放段属票 02（本票尚未落地）。
    """
    kingdom: Kingdom | None = None
    """座位级**势力**（`GLOSSARY.md`「势力」；票 01 / 票 02）。

    与 ``hero`` 同源的**事实**字段（同样由票 02 的发放段填值，本票尚未落地）。
    势力的**行为**（"其他同势力角色"）落点是技能回调自己比座位，属票 09；本票不建
    任何作用域字段，也不在这里做判定。
    """
    virtual_equipment: tuple[str, ...] = ()
    """**虚拟装备**（"视为装备"）的持久授予态：授予它的技能 id 元组（票 07 §6 / 票 30）。

    虚拟装备**不是牌**，绝不进装备区、不占牌守恒（票 07 §6 的"绝不为它造 CardRef"）；
    `装备区` 只放实体牌，`已装备` = 实体装备 ∪ 虚拟授予（由 ``skills.is_equipped`` 求并集）。
    M2 没有生产者（【八阵】类技能属 M3），字段是接口位。
    """
    granted_skills: list[str] = Field(default_factory=list)
    """**获得**的技能 id（第四种技能来源，M6 票 04；`GLOSSARY.md`「技能来源」）。

    与装备区 / 虚拟授予 / 武将牌三种载体并列，由 :meth:`Engine.grant_skill` 写入；
    载体解析读它产出 ``SkillSourceKind.GRANTED`` 实例。M6 没有"获得技能"的武将
    （觉醒技的 ☆ 技能由票 06 用同一个入口授予），故本票只以构造测试钉住形状。
    """
    lost_skills: list[str] = Field(default_factory=list)
    """**已失去**的技能 id（M6 票 04；`GLOSSARY.md`「技能失去」）。

    失去 = 实例**不再存在**且不可恢复：载体解析在去重之后减去这一份，故武将牌上的技能
    清单一字未动、实例照样不产出，连续型效果（距离 / 目标池）随之消失。写入唯一入口
    :meth:`Engine.lose_skill`；M6 无载体（断肠 / 左慈留给后续里程碑）。
    """
    suppressed_skills: list[SkillSuppression] = Field(default_factory=list)
    """**失效**记录（M6 票 04；`GLOSSARY.md`「技能失效」）：座位级的条件记录。

    载体解析的最后一道：实例仍在，但被谓词挡在解析之外（不产出候选、不进修饰器链、
    不触发钩子）；作用域到期后恢复。失效率是**座位级**的，一条记录覆盖该座位此后获得的
    非锁定技；锁定技不受 ``ALL_NON_LOCKED`` 影响。写入唯一入口
    :meth:`Engine.suppress_skills`，回合末由 ``Engine._clear_turn_effects`` 清掉。
    """
    hand_card_prohibited: bool = False
    """本回合**不能使用或打出手牌**（M6 票 04 的【义绝】黑色分支）。

    座位级、回合末清理；候选生成在 :func:`sgs.effects.legal_actions_for` 的**唯一**闸门里
    读它（使用 / 打出的候选按来源区过滤，弃置 / 重铸等不是"使用或打出"的动作照常）。
    **不发 delta**：与 ``luoyi_active`` 同姿态——回放按同一条技能动作重跑即可重建。
    """
    heart_slash_bonus_against: list[int] = Field(default_factory=list)
    """本回合"**你使用红桃【杀】对其造成的伤害 +1**"的目标座位（【义绝】黑色分支）。

    由 :meth:`Engine.add_heart_slash_bonus` 追加、回合末清空；读取方是【义绝】技能定义上的
    ``DAMAGE_DEAL_AMOUNT`` 修饰器（查询期从活着的实例算，失去 / 失效即消失）。**不发
    delta**：与 ``hand_card_prohibited`` 同一条理由。
    """


class GameState(BaseModel):
    """一局的全部棋盘状态。

    ``cards`` 里第 ``i`` 个牌实例的 ``card_id`` 恒为 ``i``；区域字段装的是
    ``card_id``（有序：``deck`` 顶端在前、``discard`` 先入在前、``hand`` 按持有顺序）。
    """

    model_config = ConfigDict(extra="forbid")

    rule_config: RuleConfig
    pack_id: str
    deck_profile: str
    cards: tuple[CardRef, ...] = ()
    players: tuple[Player, ...] = ()
    deck: list[int] = Field(default_factory=list)
    discard: list[int] = Field(default_factory=list)
    processing: list[int] = Field(default_factory=list)
    virtual_judge: dict[int, CardRef] = Field(default_factory=dict)
    """判定区里的**虚拟延时锦囊**：实体来源牌 id → 它代表的那张虚拟牌。

    判定区只装**实体牌** id（虚拟牌不进任何区域，见 :attr:`CardRef.is_virtual`）。故
    【国色】这类"一张方块牌当【乐不思蜀】"的转化延时锦囊把**来源牌**置入判定区，并在这里
    记下"它其实是哪张虚拟牌"，供判定阶段按虚拟牌结算、以及"判定区同名"检查读。
    字典**不进记录流**（回放重跑引擎时按同样的决策重建）；来源牌离开判定区即清掉。
    """
    turn_seat: int | None = None
    phase: Phase | None = None
    skill_state: dict[str, int] = Field(default_factory=dict)
    """**per-instance 技能状态**（票 07 §6 / 票 30）：实例键 → 计数（如"已用次数"）。

    实例键 = ``(owner, skill_id, source)`` 的规范文本（``skills.instance_key``）；
    来源消失（装备离场）时由 ``skills.prune_skill_state`` 清理。M2 的 17 条装备没有
    持久状态，故本字段实际恒空——它是"M3 只许增"的接口位，形状在此钉死。
    """

    def player(self, seat: int) -> Player:
        return self.players[seat]

    def card(self, card_id: int) -> CardRef:
        """按 ``card_id`` 取牌面；虚拟牌（``card_id < 0``）不在 ``cards`` 里，取它即编程错误。

        负下标若被静默放行会取到牌堆末尾的另一张实体牌（Python 负索引），那会静默破坏
        "虚拟牌不参与牌守恒"这条不变量，故这里 fail-closed。
        """
        if card_id < 0:
            raise ValueError(f"牌 {card_id} 是虚拟牌，不在实体牌表里")
        return self.cards[card_id]

    def refs(self, card_ids: tuple[int, ...] | list[int]) -> tuple[CardRef, ...]:
        return tuple(self.cards[card_id] for card_id in card_ids)

    def zone(self, ref: ZoneRef) -> list[int]:
        """区域坐标 → 该区域的 ``card_id`` 列表（唯一入口，原语只经它改区域）。

        共享区（牌堆 / 弃牌堆 / 处理区）忽略座位；逐座位的三个区必须给出座位。
        """
        if ref.zone is Zone.DECK:
            return self.deck
        if ref.zone is Zone.DISCARD:
            return self.discard
        if ref.zone is Zone.PROCESSING:
            return self.processing
        if ref.seat is None:
            raise ValueError(f"{ref.zone.value} 区按座位分装，区域坐标必须带座位")
        player = self.player(ref.seat)
        if ref.zone is Zone.HAND:
            return player.hand
        if ref.zone is Zone.EQUIP:
            return player.equip
        if ref.zone is Zone.JUDGE:
            return player.judge
        raise ValueError(f"未知区域：{ref.zone!r}")
