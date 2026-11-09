"""对局状态：玩家、牌实例、区域与结算栈的帧类型。

状态只用**有序容器**（``list`` / ``tuple`` / ``dict``，``dict`` 按插入序）：
``set`` / ``frozenset`` 不得作为字段出现——它们的迭代序逐进程变化，会破坏
"同种子逐字节复现"（票 08 §7）。牌实例的 ``card_id`` 就是 ``cards`` 里的下标，
一旦开局分配就不再变化；洗牌只置换区域列表。

本模块只装**状态**：行为字段（使用时机 / 目标规则 / 次数限制 / 效果列表）归 ``effects``，
记录流 schema 归 ``engine``。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from sgs.data import CardTagType, Color, Gender, Identity, Kingdom, RuleConfig

__all__ = [
    "ARMOR_CAP",
    "CardRef",
    "CardTag",
    "FrameKind",
    "GameState",
    "HeroCardZone",
    "LIFE_DECREASE_DAMAGE_KEY",
    "Marker",
    "MoveReason",
    "Phase",
    "Player",
    "SEALED_ZONE_EQUIP",
    "SEALED_ZONE_IDS",
    "SEALED_ZONE_JUDGE",
    "SkillSuppression",
    "SkillSuppressionPredicate",
    "SkillSuppressionScope",
    "SlotKind",
    "Zone",
    "ZoneRef",
    "ZoneRefField",
    "zone_sealed",
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
    （票面 D10 的"失效率不逐技能展开"）；锁定技不受它影响。用户裁定（票 09）：
    这里的"技能"只指**武将技能**，装备牌 / 虚拟装备带来的技能不在其列。
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


JUDGE_SLOTS: tuple[str, ...] = ("judge_1", "judge_2", "judge_3")
"""**判定区的三个槽位**（lukang-mobile 票 03；用户 2026-11-07 裁定的逐槽口径）。

判定区在规则里是一个区域，界面与"同一判定区不能有两张同名延时锦囊"（账本 L626）把它
固定成三格：今天的三种延时锦囊（乐不思蜀 / 兵粮寸断 / 闪电）恰好各占一格。**位置**本身
没有规则含义（读取面只回答"这个位置能不能放牌"），槽数因此与界面布局同源——将来某个包
加出第四种延时锦囊时，这里与 `tools/sgs_web/static/seat-view.js:judgeSlotLayout` 一起加。
"""

EQUIP_SLOTS: tuple[str, ...] = (
    SlotKind.WEAPON.value,
    SlotKind.ARMOR.value,
    SlotKind.HORSE_OFFENSE.value,
    SlotKind.HORSE_DEFENSE.value,
)
"""**装备区的四个槽位**（顺序 = 界面从左到右：武器 / 防具 / 攻马 / 防马）。

宝物槽位当前不启用（ADR-006），故不在这张表里；将来启用时加进来，读取面与
`abolish_slots` 的闭集一起看到。
"""

SEALED_SLOT_IDS: tuple[str, ...] = (*JUDGE_SLOTS, *EQUIP_SLOTS)
"""``Player.sealed_zones`` 的**槽位闭集**（7 个；顺序 = 判定 3 + 装备 4）。

lukang-mobile 票 03 起，「废除」的**状态粒度 = 槽**（用户 2026-11-07）：雄乱的一次发动 =
一个事件废除这 7 个槽；决堰的一次发动 = 一个事件废除 1 或 2 个装备槽。**不再有** `judge` /
`equip` 两个"整区"取值——"某区被废除"改由 :func:`judge_zone_sealed` /
:func:`equip_zone_sealed` **派生**（该区的槽全被封印）。
"""

SEALED_ZONE_IDS: frozenset[str] = frozenset(SEALED_SLOT_IDS)
"""槽位闭集的 set 形态（`Engine.abolish_slots` / `restore_slots` 的校验用；同一份真源）。"""

SEALED_ZONE_JUDGE = "judge"
"""**整区**名：不是状态值，只作读取面的兼容入口（:func:`zone_sealed` 把它读成
"3 个判定槽全封"）。``Player.sealed_zones`` 里**永远不出现**它（票 03 起状态粒度 = 槽）。"""

SEALED_ZONE_EQUIP = "equip"
"""**整区**名：同 :data:`SEALED_ZONE_JUDGE`，读取面兼容入口（4 个装备槽全封）。"""


def judge_zone_sealed(player: "Player") -> bool:
    """判定区是否处于**封印状态** = 三个判定槽**全部**被封印。

    读取面用它与旧的"整区 `judge`"对齐：延时锦囊的目标规则、闪电的转移都问这一句；
    只封 1～2 个判定槽（今天的载体不会产生）**不**等于判定区被废除。
    """
    return all(slot in player.sealed_zones for slot in JUDGE_SLOTS)


def equip_zone_sealed(player: "Player") -> bool:
    """装备区是否处于**封印状态** = 四个装备槽**全部**被封印。"""
    return all(slot in player.sealed_zones for slot in EQUIP_SLOTS)


def zone_sealed(player: "Player", zone_id: str) -> bool:
    """该座位这个**区 / 槽**是否被废除（读取面的唯一判据）。

    - 槽位 id（`weapon` / `armor` / `horse_offense` / `horse_defense` / `judge_1..3`）：
      只看**自己**那一位——封印武器槽不等于废除装备区，防具槽照常可用；
    - 整区名（`judge` / `equip`）：**兼容入口**，读成"该区的槽全封"（派生，不存状态）；
    - 其它值一律 ``False``（fail-closed）。

    **宝物槽位（``treasure``）的口径**：ADR-006 下宝物槽位不启用、不在
    :data:`SEALED_SLOT_IDS`，故它既不能被废除、也不会被读成已封印。
    """
    if zone_id in SEALED_ZONE_IDS:
        return zone_id in player.sealed_zones
    if zone_id == SEALED_ZONE_JUDGE:
        return judge_zone_sealed(player)
    if zone_id == SEALED_ZONE_EQUIP:
        return equip_zone_sealed(player)
    return False



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
    """实体牌所在的区域种类（票 08 §1；M6 票 10 增 ``HERO``）。

    ``HAND`` / ``EQUIP`` / ``JUDGE`` 是**逐座位**的区，座位在 :class:`ZoneRef` 坐标里；
    ``DECK`` / ``DISCARD`` / ``PROCESSING`` 是共享区。``HERO``（武将牌上的牌）是**按
    （座位, 技能实例）再分片**的逐座位区：座位答"扣在谁的武将牌上"，技能实例答"哪个技能
    放的"（spec D14；同一座位可同时存在多个相互独立的放置区）。
    """

    DECK = "deck"
    HAND = "hand"
    DISCARD = "discard"
    PROCESSING = "processing"
    EQUIP = "equip"
    JUDGE = "judge"
    HERO = "hero"


class HeroCardZone(BaseModel):
    """**武将牌上的一个放置区**（M6 票 10；`GLOSSARY.md`「武将牌上的牌」）。

    区键 = （座位, 技能实例）：座位是 :class:`Player.hero_cards` 的宿主（扣在谁的武将牌
    上，可跨座位），技能实例由字典的键回答（``owner:skill_id``，见
    ``skills.hero_card_zone_key``）。**不同技能实例的区彼此独立**：牌的归还 / 可见性 /
    附加效果都由各自的声明决定，不会串到别的区。

    规则性质（`三国杀详细规则.txt` §十五）：这些牌是"移出游戏的牌"，不为任何角色所拥有、
    不在手牌 / 装备 / 判定 / 牌堆 / 弃牌堆 / 处理区，故**不算手牌**、不计入手牌上限，
    也不能被拆 / 顺指定。区在**失去技能时不受影响**（规则 15.2 明文），故这里的字段是
    技能的**身份**（持有者 + 技能 id），不引用运行期 ``SkillInstance``（那个会随失去消失）。
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    """放置它的技能 id（全限定）——归还时机 / 附加效果按它查数据声明。"""

    owner: int
    """放置它的技能实例的**持有者**座位（归还的收件人；与宿主座位可不同）。"""

    cards: list[int] = Field(default_factory=list)
    """区里的实体牌 id（按置入序；与其它区域一样，列表就是区域的真源）。"""


class CardTag(_FrozenModel):
    """挂在一张牌上的**持久事实**（M6 票 09；spec D12 / §标签）。

    形状 = **（标签类型、来源实例）**：标签类型是 ``sgs.data`` 的登记闭集
    （:class:`~sgs.data.CardTagType`，四项声明住 :data:`~sgs.data.CARD_TAG_TYPES`）；
    来源实例用 ``(owner, skill_id)`` 标识——载体解析（``resolve_instances``）按同一对字段
    去重，故它是"哪一个技能实例"的规范身份，不随来源种类（武将牌 / 装备 / 获得）漂移。

    **与所在区域无关**：标签存在 :attr:`GameState.card_tags` 里，不随牌移动被搬走或清掉；
    牌在牌堆顶 / 手牌 / 弃牌堆都照样带着它。清除只由**登记表声明的触发**（:class:`CardTagClear`）
    或"来源实例消失"（``disappears_with_source``）决定。

    只挂在**实体牌**上（``card_id >= 0``）：虚拟牌从不进任何区域、也不持久存在。
    """

    tag_type: CardTagType
    owner: int
    """来源实例的持有者座位。"""

    skill_id: str
    """来源实例的技能 id（全限定，如 ``hero.jie_zhenji.luoshen``）。"""



@dataclass(frozen=True, slots=True)
class ZoneRef:
    """**区域坐标**（票 08 §1；M6 票 10 增第三个维度 ``skill_instance``）。

    ``seat`` 只对 ``HAND`` / ``EQUIP`` / ``JUDGE`` / ``HERO`` 有意义，共享区为 ``None``。
    ``skill_instance`` 只对 ``HERO`` 有意义（放置技能的实例键；其余区为 ``None``）。
    坐标说的是"**区域**在谁那里"，不是"牌归谁"——判定区的牌坐标带座位，但牌本身
    仍"不为任何角色所拥有"（规则 11.6）；"归谁"由 :class:`CardMoveDelta.owner` 回答。

    冻结值类型（用 ``dataclass`` 而非 pydantic）：pydantic 把 stdlib dataclass 字段
    序列化成 ``{"zone": ..., "seat": ...}``（``seat`` 显式写出，含 ``null``），
    与 ``CardRef`` 的平铺形状一致，且允许位置构造 ``ZoneRef(Zone.HAND, 0)``。
    第三个字段**只在武将牌上的牌区出现**：:data:`ZoneRefField` 的写出去掉 ``None`` 的
    那一档，故非 ``HERO`` 区的记录字节与 M2 逐字相同。
    """

    zone: Zone
    seat: int | None = None
    skill_instance: str | None = None


def _zone_ref_wire(value: ZoneRef) -> dict[str, Any]:
    """``ZoneRef`` 的记录形态：``zone`` / ``seat`` 恒写，``skill_instance`` 仅在 HERO 区写。

    ``seat`` 显式写出（含 ``null``）是 M2 冻结口径（票 08 §7）；新增的第三维**不能**
    让 47 条标准金录像多出一个 ``"skill_instance": null``——那会改既有记录字节，故这一档
    只透出非 ``None`` 的实例键。
    """
    data: dict[str, Any] = {"zone": value.zone.value, "seat": value.seat}
    if value.skill_instance is not None:
        data["skill_instance"] = value.skill_instance
    return data


ZoneRefField = Annotated[ZoneRef, PlainSerializer(_zone_ref_wire, return_type=dict)]
"""在**记录模型**（delta / 选择面槽位）里引用 :class:`ZoneRef` 的字段类型。

序列化器只在这里挂（而不是把 ``ZoneRef`` 换成 pydantic 模型）：``ZoneRef`` 保持
dataclass 的位置构造与冻结语义，字段形状仍在 ``models`` 一处声明。
"""


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

    PINDIAN = "PINDIAN"
    """拼点扣置（huo-taishici-xunyu 票 03）：手牌 → 处理区（扣置）→ 弃牌堆。

    规则第十二章 12.2 L1323 / 12.7 L1372–L1374：拼点 = **因拼点失去一张牌**，
    但拼点牌进入弃牌堆**不算「弃置」**。故它与 ``DISCARD``、``LOSE`` 都不同：

    - 「失去牌后」类钩子（【连营】）按**区域**判、**不看** :class:`MoveReason`
      （``skills/move_after.py`` 的中心纪律）⇒ 用最后一张手牌拼点照常触发【连营】；
    - 「弃置」类触发必须显式把 ``PINDIAN`` 排除在外（比较 reason 的站点要逐条核）。
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
    REJUDGE = "REJUDGE"
    """**改判事件帧**（feng-zhangjiao 票 02）：一次"用一张牌代替 / 替换判定牌"。

    用户 2026-11-02 裁定：改判**不是打出**（打出只发生在"手牌 → 处理区"的牌响应里，
    界【鬼才】/【鬼道】允许拿**装备区**的牌，而装备区的牌根本不能打出）。故它有自己的
    事件帧：帧内两段移动（新牌 → 处理区成为判定牌；原牌 → 弃牌堆（**代替**）/
    改判者手牌（**替换**）），不派发任何"打出时 / 使用后"钩子。帧私有载荷：
    ``subject`` / ``seat`` / ``skill_id`` / ``old`` / ``new`` / ``original_to``。
    """


LIFE_DECREASE_DAMAGE_KEY = "life_decrease_from_damage"
"""``FrameKind.LIFE_DECREASE`` 帧的**来源判据**（实机报障 2026-10-31）：``True`` = 这次体力
扣减是**伤害**带来的，**不是**「失去体力」。

官方把「受到伤害」与「失去体力」当两件事：伤害链里确实压一次 ``LIFE_DECREASE`` 子帧（L8 的
三个时机照常派发），但那是"扣减体力"，不是「失去体力」。挂在 ``LIFE_DECREASE_AFTER`` 的技能
（界黄盖【诈降】）必须读这个键把两者分开——只按时机挂会让"被打 1 点"触发"失去 1 点体力后"
（用户实机复现的原样）。

缺省（没有这个键）= 真正意义上的「失去体力」：【苦肉】/【反间】这类技能自己压的帧。
帧私有载荷不进记录流，故这条判据不改变任何金录像的字节。
"""

ARMOR_CAP = 5
"""**护甲值**的硬上限（yijiang-2014-round2 票 07 / W7；用户 2026-11-09 签 Q-A2）。

护甲是**第二条体力轴**（与 ``hp`` / ``hp_max`` 并列，见 :class:`Player.armor`）：可叠加、
超过上限**夹到 5**。它是**规则常数**而不是 per-player 字段（"封顶"不随武将数据变化），
故住模块级常量：``Player`` 只存当前值，任何"获得护甲"的效果都把结果夹进 ``[0, ARMOR_CAP]``
再交给 :func:`~sgs.effects.primitives.apply_armor`（原语对越界抛错，与 ``apply_hp`` 同姿态）。

**不是** ``Timing``、也不是一条帧：护甲在**别人打你时**生效（``life_decrease_on`` 的扣减
次序），没有"护甲时机"可挂。故它不进 ``IMPLEMENTED`` / ``ENUM_ONLY`` 四轴计数，
``shapes.md`` §5 的「零载体」一档由此解封（见 ``docs/agents/skill-authoring/shapes.md``）。
"""


class Marker(_FrozenModel):
    """一个座位身上的**标记实例**（票 06；用户 Q6 裁定）。

    字段 = 名称 / 来源（座位 + 全限定技能 id）/ 数量 / 上限 / 效果 / 效果说明 + 寿命策略。
    名字用中文（「梦魇」这类），**没有 id**；同一目标上 `(name, source_seat,
    source_skill_id)` 相同的标记合并成一条，来源不同则分列。标记不是牌：不进任何区域、
    不计手牌数 / 上限，不能被拆 / 顺 / 无懈；**被无效（suppressed）不动标记**（规则 §14.3）。

    ``effect`` / ``description`` 是**标记自身效果**的声明面（lei-shenganning 票 94 起接线）：
    ``effect`` 存一个 ``CATALOG`` 里的**效果定义 id**（与技能 id 同形），该定义由
    :func:`sgs.skills.resolve_instances` 以 ``MARKER`` 载体产出实例——**不受技能失效影响**
    （用户 2026-11-07 裁定："这个标记本身具有效果，所以就算劫营这个技能失效了，依然享受
    以上所说的3种加成"），标记不在 ⇒ 实例不在。两条寿命策略：``clear_on_source_death``
    （来源座位身死时清空，梦魇 True）与 ``clear_on_source_skill_lost``（来源**失去**放置它的
    那条技能时清空，营 True；"被无效不影响，营标记还在"——用户 2026-11-07）。目标身死则
    无条件清空（规则 §14.4 / 用户规则 7）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source_seat: int
    source_skill_id: str
    count: int = Field(ge=1)
    max_count: int | None = None
    effect: str | None = None
    description: str | None = None
    clear_on_source_death: bool = False
    clear_on_source_skill_lost: bool = False


class Player(BaseModel):
    """一个座位。``seat`` 就是它在 :attr:`GameState.players` 里的下标。"""

    model_config = ConfigDict(extra="forbid")

    seat: int
    hp: int
    hp_max: int
    armor: int = 0
    """**护甲值**：与体力并列的第二条体力轴（yijiang-2014-round2 票 07 / W7）。

    用户 2026-11-09 签 Q-A1 = 一（独立第二血条）：受到伤害时**先扣护甲、不足再扣体力**
    （次序落在唯一写点 :func:`sgs.engine.steps.life_decrease_on`，且**只对伤害帧**——
    「失去体力」帧不吸收）；回复体力 / 任何「失去体力」/ 体力上限变化**都不碰护甲**；
    护甲扣光**不濒死**（濒死只看 ``hp``）。可叠加，封顶 :data:`ARMOR_CAP`。

    写入口唯一 = :meth:`Engine.apply_armor_change` → :func:`~sgs.effects.apply_armor`
    （照 ``apply_hp`` 的姿态：调用帧先把值夹进 ``[0, ARMOR_CAP]``，原语对越界抛错）。
    变化落一条 ``armor_change`` delta（与 ``hp_change`` 同帧、**排在它前面**）；它是公开
    事实，但不进记录流里的询问视图（``PlayerPublic.armor`` 是 ``exclude``，与
    ``chained`` / ``wine_active`` 同一条纪律 ⇒ 47 条金录像逐字节不变），网页载荷由
    ``sgs_web.display.seat_display`` 补下。
    """
    alive: bool = True
    death_time_skills_active: bool = False
    """死亡时技能窗口（票 10）：死者本人的技能在阵亡结算里**暂时**可解析。

    ``alive=False`` 仍在死亡入口立即落（胜负 / 距离 / 人数必须看见死者）；本字段只把
    "技能可解析"从"活着"里拆出来：死亡段在「阵亡时」空节点把它置真、在「失去技能」收尾
    置假（`DEATH_ON` / `DEATH_AFTER` 与官方弃牌 / 奖惩在窗口内）。窗口只对**当前死亡者**
    打开；先前死亡者在各自收尾已关窗，仍静默。

    读点只有 :func:`sgs.skills.resolve_instances` 的实例过滤（以及它的缓存键）；不发
    delta、不进投影 / 记录流——它不是可见状态，只是解析期的一次窗口。
    """
    markers: tuple[Marker, ...] = ()
    """座位身上的**标记实例**（票 06；用户 Q6 裁定）。

    按插入序（同种子逐字节复现）；同一 `(name, source_seat, source_skill_id)` 合并成一条。
    公开信息（显示在目标武将牌上），但**不进记录流**（投影 `exclude`），变化经
    ``marker_change`` delta 下发；阵亡清点在 :meth:`Engine.discard_dead_cards`（死亡弃牌
    同一钩子）。标记不是牌，故不在任何 ``Zone`` 里。
    """
    hand: list[int] = Field(default_factory=list)
    equip: list[int] = Field(default_factory=list)
    """装备区（一个整体区）；列表序 = 槽位枚举序（票 12 §Q2），每槽至多一张。"""
    judge: list[int] = Field(default_factory=list)
    """判定区（**先入在前**）；"最后置入"的读取端用 ``reversed()`` 表达（票 08 §8）。"""
    hero_cards: dict[str, HeroCardZone] = Field(default_factory=dict)
    """**武将牌上的牌**的全部放置区（M6 票 10；spec D14）：区键 → 区。

    区键 = 放置技能的**实例键** ``owner:skill_id``（``skills.hero_card_zone_key`` 是唯一
    派生处）：座位 = 这张武将牌的主人（扣牌的目标，可跨座位），键里的 ``owner`` = 放置技能
    的持有者。字典按插入序（同种子逐字节复现），空区**保留**（规则 15.2：失去技能不影响
    武将牌上的牌，区本身不随一次归还消失；再次放置进同一个键）。

    这些牌**不在手牌区**，故手牌数 / 手牌上限 / 拆顺的候选都自动不含它们（票面验收）。
    写入唯一入口 :meth:`Engine.place_on_hero_card`；回合末归还由
    :meth:`Engine.return_hero_card_zones` 按数据声明做，阵亡清区在
    :meth:`Engine.discard_dead_cards`。
    """
    chained: bool = False
    """是否横置（铁索连环）。

    U1-04 先落座位字段；**M5-03 接线**：写入唯一入口是 ``Engine.set_chained``（铁索连环的
    toggle 与属性伤害的"结算后立即重置"），每次变化发一条 ``chained_change`` delta。
    """
    flipped: bool = False
    """武将牌是否**背面朝上**（翻面；票 1 的翻面机制）。

    与 :attr:`chained` 是**两个独立状态**（规则集 13.1 / 13.3：横置管属性伤害连环、翻面管
    跳过回合，可以同时存在）。写入唯一入口 ``Engine.set_flipped``，每次变化发一条
    ``flipped_change`` delta。读点两处：回合环的"回合开始前"——背面朝上的角色在那里
    翻回正面，然后**整个回合被跳过**（规则集 13.2）；以及 AI 的【据守】打分器（票 22：
    已翻面 ⇒ 这次翻面是翻回正面、不跳回合 ⇒ 恒发动；正面 1 体力 ⇒ Pass）。
    """
    wine_active: bool = False
    """【酒】的"下一张【杀】+1"是否生效（M5-04 接线：出牌阶段的【酒】写入，回合末清空；
    每次变化发一条 ``wine_change`` delta）。"""
    sealed_zones: tuple[str, ...] = ()
    """被**废除（封印）**的**槽位**（lei-batch 票 02 立、lukang-mobile 票 03 改成逐槽）。

    取值是 :data:`SEALED_SLOT_IDS` 的闭集（7 个）：判定区 ``judge_1`` / ``judge_2`` /
    ``judge_3`` + 装备区 ``weapon`` / ``armor`` / ``horse_offense`` / ``horse_defense``。
    **没有** ``judge`` / ``equip`` 两个"整区"取值——它们只是读取面的兼容名
    （:func:`judge_zone_sealed` / :func:`equip_zone_sealed`，= 该区的槽全被封）。

    写入**唯一入口**是 ``Engine.abolish_slots`` / ``Engine.restore_slots``：**一次调用 =
    一个废除事件**（雄乱 = 7 槽、决堰 = 1～2 槽）；废除 = 先把这些槽里的牌强制弃置
    （进弃牌堆、``MoveReason.DISCARD``，按来源区分组搬牌），再落这些位；恢复 = 去掉这些位。

    读点（三条系统规则，`Engine` 与 :func:`~sgs.effects.check_targets` 各一半）：
    装备区 / 对应槽位封印 ⇒ 该角色不能**使用**装备牌、也不能把牌**置入**它；
    判定区封印 ⇒ 它不是延时锦囊的合法目标（【闪电】转移时越过它）。

    **网页看得见**（lei 票 05）：投影面 ``PlayerPublic.sealed_zones`` 照搬这一位，座位卡的
    判定 / 装备区据此叠红色禁止角标（``tools/sgs_web/static/seat-view.js``）。**仍不发
    delta**（lei 票 05 复核后的二选一）：投影每次从状态重算，记录流不含投影；"废除"那一串
    弃牌移动照常进记录流，回放按动作重跑即可重建——按"网页要画封印图标就补 delta"的旧
    承诺因此不再成立，no-leak 八类也不用为此多登记一行。
    """
    luoyi_active: bool = False
    """【裸衣】的"本回合【杀】/【决斗】伤害 +1"是否生效（M6 票 01）。

    写入唯一入口 ``Engine.set_luoyi_active``（许褚的摸牌阶段写入、回合末清空）；读点是
    :func:`sgs.skills.phase_body._luoyi_damage_bonus` 的 ``DAMAGE_DEAL_AMOUNT`` 回调。
    **不发 delta**：与 ``wine_active`` 不同，目前没有任何 JSON 读者（网页座位卡不读它、
    记录流不写它），回放按同一条 ``SKILL_ACTIVATION`` 动作重跑即可重建；等出现 UI 读取方
    时再补 ``luoyi_change`` delta（同时要登记 no-leak 判据）。
    """
    luoyi_until_next_turn: bool = False
    """界【裸衣】的"直到你的下个回合开始【杀】/【决斗】基础伤害 +1"是否生效（M6 票 14）。

    与标准版【裸衣】（``luoyi_active``）**分字段**：两者作用域不同——标准版到**本回合结束**，
    界版到**你的下个回合开始**（§十九 L2051–L2055），故不能共用"回合末清空"的清理点。
    写入唯一入口 ``Engine.set_luoyi_until_next_turn``（界许褚的摸牌阶段写入）；清理点 =
    该座位自己的 ``TURN_START``（:meth:`Engine._turn_start`）——那正是"你的下个回合开始"。
    读点是 :func:`sgs.skills.phase_body._jie_luoyi_damage_bonus` 的 ``DAMAGE_DEAL_AMOUNT``
    回调。**不发 delta**：与 ``luoyi_active`` 同姿态（没有 JSON 读者，回放按同一条
    ``SKILL_ACTIVATION`` 动作重跑即可重建）。
    """
    shuangxiong_judge_color: Color | None = None
    """【双雄】本回合"判定结果的颜色"（huo-wolong-yanliang 票 02 的唯一写入口面）。

    ``None`` = 本回合没有【双雄】的延时类效果；红 / 黑 = 已发动且判定结果的颜色。牌面：
    「摸牌阶段，你可以改为进行一次判定，然后你获得此判定牌。若如此做，**本回合**你可以将
    任意一张与判定结果**颜色不同**的手牌当【决斗】使用」。

    写入唯一入口 :meth:`Engine.set_shuangxiong_judge_color`（双雄自己的判定帧出栈后写）；
    清理点 = :meth:`Engine._clear_turn_effects`（作用域 ``TURN``）。读点是
    :func:`sgs.skills.phase_body._shuangxiong_candidate` 的 ``CANDIDATES`` 回调。
    **不发 delta**：与 ``luoyi_active`` 同姿态——判定牌本身已经进记录流（技能自己的
    判定），颜色由它派生，回放按同一条 ``SKILL_ACTIVATION`` 动作重跑即可重建。

    **已知偏离**（账本 06 §5.1 有行，用户 2026-11-06 裁）：这一位与**座位**绑定、不随技能
    实例消失，但**候选入口**仍由双雄实例的 ``CANDIDATES`` 修饰器提供 ⇒ 失去技能后入口随之
    消失。《三国杀规则集》第二章 L195 的例（「失去其所有武将技能，但是〖双雄〗的延时类效果
    依然能执行」）要求的是"入口也不消失"，本仓没有那种原语（架构缺口见
    `.scratch/huo-wolong-yanliang/issues/06-skill-instance-decoupled-candidate-source.md`）。
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
    zhaxiang_active: bool = False
    """界黄盖【诈降】的"本阶段"效果是否生效（M6 票 17；§十九 L2164–L2167）。

    牌面："然后若此时是你的出牌阶段，你本阶段：使用红色【杀】无距离限制且不能被【闪】
    响应、使用【杀】的次数上限 +1。"写入唯一入口 ``Engine.set_zhaxiang_active``（诈降的
    ``LIFE_DECREASE_AFTER`` 钩子在"此刻是我的出牌阶段"时置位）；**清理点 = 出牌阶段结束**
    （:meth:`Engine._phase_play_end`，``_phase_play_start`` 再兜一次）——与
    ``slash_in_play_phase`` 的"阶段起点复位"不同："本阶段"的效果必须在阶段**结束**就消失，
    否则别人的回合里被"借刀杀人"被迫使用的【杀】会错误地吃到这三条加成。

    读点是 :mod:`sgs.skills.life_after` 的三条回调（``TARGET_POOL`` / ``COUNT_LIMIT`` /
    ``USE_AFTER_DESIGNATE_TARGET`` 的帧标签）。**不发 delta**：与 ``luoyi_active`` 同姿态。
    """
    tianyi_won: bool = False
    tianyi_lost: bool = False
    """太史慈【天义】的"本阶段"拼点结果（huo-taishici-xunyu 票 01；两条互斥的座位布尔）。

    ``tianyi_won`` = 拼点**赢**（三条加成生效：次数上限 +1 / 无距离限制 / 目标数上限 +1）；
    ``tianyi_lost`` = **没赢**（平点也算没赢）⇒ 本阶段不能**使用**【杀】（仍可**打出**）。
    两个都为假 = 本阶段尚未发动。

    写入唯一入口 ``Engine.set_tianyi_state(seat, won)``；**清理点 = 出牌阶段结束**
    （:meth:`Engine._phase_play_end`，``_phase_play_start`` 再兜一次）——与
    ``zhaxiang_active`` 同一作用域（牌面写"然后你本阶段根据拼点结果获得对应效果"）。
    读点是 :mod:`sgs.skills.active` 的三条修饰器回调。**不发 delta**：与
    ``zhaxiang_active`` 同姿态（没有 JSON 读者，回放按同一次拼点重建）。
    """
    damage_dealt_this_turn: bool = False
    """本回合是否**造成过伤害**（M6 票 06 的【诛害】判据：§十九 L2127–L2129）。

    写入唯一入口 :meth:`Engine.note_damage_dealt`（伤害数值定稿且 > 0、未被防止、
    有来源时由伤害帧调一次）；作用域 = **本回合**，回合末由
    :meth:`Engine._clear_turn_effects` 把所有座位的这一位清掉。判据是"本回合**造成过**
    伤害"而不是"最近一次伤害的来源"，故一次成立即保持到回合末。

    **不发 delta**：与 ``luoyi_active`` 同姿态——没有 JSON 读者，回放按同一条伤害结算
    重建（本字段不进记录流）。
    """
    damage_taken_this_turn: int = 0
    """本回合**受到过几次伤害**（jushou-lusu 票 01 的【矢北】判据；用户 2026-11-08 签 Q2）。

    ``damage_dealt_this_turn`` 的**镜像**（M6 票 06 的【诛害】）——同一文件、同一复位点，
    只换了三件事：记的是**挨打**那一侧、用**计数**而不是布尔（【矢北】要分"第 1 次 / 第
    ≥2 次"两档），并**去掉「有来源」**这一条（【闪电】一类无来源伤害照样是"受到伤害"）。

    写入唯一入口 :meth:`Engine.note_damage_taken`（伤害数值定稿且 > 0、未被防止时由
    :func:`~sgs.engine.steps.damage_finalize_amount` 调一次，**每个伤害事件 +1**，不是逐点）；
    作用域 = **本回合**（谁的回合都算）：回合末由 :meth:`Engine._clear_turn_effects` 把所有
    座位的这一位清零。**不发 delta**：没有 JSON 读者，回放按同一条伤害结算重建。
    """
    play_phase_last_card: CardRef | None = None
    """**本出牌阶段**自己**使用**的最后一张牌（jushou-lusu 票 01 的【渐营】判据；Q1 签）。

    写入唯一入口 :meth:`Engine._note_play_phase_card`（``start_use`` 入口；**打出不算**，
    故 ``play_card`` 不写）。判据与 ``slash_in_play_phase`` 同一组：``is_own_play_phase``
    成立才写（回合外的响应、别人的回合都不算）。复位点 = :meth:`Engine._phase_play_start`
    （被【乐不思蜀】跳过时也复位），故"本阶段"的作用域不跨回合。

    与:attr:`play_phase_previous_card` 是**一对**：``start_use`` 里先 ``previous ← last``、
    再 ``last ← 本张``（**旋转**），【渐营】的钩子在 ``USE_ON_USE`` 派发时读 ``previous``。
    分两格而不是一格，是因为写入点在**压 USE 帧之前**（``game.py`` 的 ``start_use``）：
    只留一格时钩子会读到"自己"，比较恒成立。**不发 delta**：与 ``slash_in_play_phase``
    同姿态（记录流里的使用动作已足以重放重建）。
    """
    play_phase_previous_card: CardRef | None = None
    """**本出牌阶段**自己使用过的**上一张**牌（【渐营】比较的左侧；见上一条字段）。

    生命周期与:attr:`play_phase_last_card` 逐字相同（同一次旋转、同一个复位点）。
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
    非锁定**武将**技能（装备牌 / 虚拟装备带来的技能不失效，用户裁定票 09）；锁定技不受
    ``ALL_NON_LOCKED`` 影响。写入唯一入口 :meth:`Engine.suppress_skills`，回合末由
    ``Engine._clear_turn_effects`` 清掉。
    """
    hand_card_prohibited: bool = False
    """本回合**不能使用或打出手牌**（M6 票 04 的【义绝】黑色分支）。

    座位级、回合末清理；候选生成在 :func:`sgs.effects.legal_actions_for` 的**唯一**闸门里
    读它（使用 / 打出的候选按来源区过滤，弃置 / 重铸等不是"使用或打出"的动作照常）。
    **不发 delta**：与 ``luoyi_active`` 同姿态——回放按同一条技能动作重跑即可重建。
    """
    xiongluan_target: int | None = None
    """本回合**我对这名角色使用牌无距离与次数限制**（张绣【雄乱】的效果①）。

    写入方 = 【雄乱】的提交点（一次发动只写一个座位）；回合末由
    ``Engine._clear_turn_effects`` 清掉。读取方是同一条技能定义上的两个修饰器：
    ``TARGET_POOL``（把他补回候选池 ⇒ 豁免距离 / 攻击范围）与 ``COUNT_EXEMPT``
    （``ModifierContext.use_targets`` ⊆ {他} ⇒ 这一次使用不受次数上限；候选生成期由
    ``check_count`` 的目标维度收窄，不是把整张牌放行）。**不发 delta**：与
    ``hand_card_prohibited`` 同姿态（回放按同一条技能动作重跑即可重建）。
    """
    heart_slash_bonus_against: list[int] = Field(default_factory=list)
    """本回合"**你使用红桃【杀】对其造成的伤害 +1**"的目标座位（【义绝】黑色分支）。

    由 :meth:`Engine.add_heart_slash_bonus` 追加、回合末清空；读取方是【义绝】技能定义上的
    ``DAMAGE_DEAL_AMOUNT`` 修饰器（查询期从活着的实例算，失去 / 失效即消失）。**不发
    delta**：与 ``hand_card_prohibited`` 同一条理由。
    """
    liegong_bonus: tuple[int, tuple[int, ...]] | None = None
    """**本次使用**的「界烈弓② 对该目标 +1 伤害」记账：``(渠道牌 id, 目标座位元组)``。

    写入唯一入口 :meth:`Engine.add_liegong_bonus`（界黄忠移动版【烈弓】② 的
    ``USE_AFTER_DESIGNATE_TARGET`` 钩子，只记满足「你的体力值不大于其体力值」的目标）；
    读取方是同一条技能定义上的 ``DAMAGE_DEAL_AMOUNT`` 修饰器（按 ``(牌 id, 目标)`` 精确
    命中，**不**串到本回合后续的【杀】）。清理 = 本次使用结束时（``RESOLVE_AFTER_END``
    清扫钩子）与回合末（:meth:`Engine._clear_turn_effects` 兜底）。

    与 :attr:`heart_slash_bonus_against`（【义绝】，**本回合**）**分字段**：作用域不同，
    与 :attr:`luoyi_until_next_turn` 的分字段同一条理由。**不发 delta**：没有 JSON 读者，
    回放按同一条技能动作重跑即可重建。
    """
    qimou_distance_x: int = 0
    """本回合「你计算与其他角色的距离 -X」的 X（界魏延移动版【奇谋】）。

    写入唯一入口 :meth:`Engine.set_qimou`（发动结算完落位）；读取方是同一条技能定义上的
    ``DISTANCE`` 修饰器（主体侧）。清理点 = 回合末（:meth:`Engine._clear_turn_effects`）。
    **不发 delta**：与 ``liegong_bonus`` 同姿态。
    """
    qimou_count_x: int = 0
    """本阶段「使用【杀】的次数上限 +X」的 X（界魏延移动版【奇谋】）。

    与 :attr:`qimou_distance_x` **分字段**：作用域不同（本阶段 vs 本回合）——次数字段在
    出牌阶段结束时由 :meth:`Engine._phase_play_end` 清掉，距离字段活到回合末。读取方是
    ``COUNT_LIMIT`` 修饰器（只对【杀】族；无次数限制者保持 ``None``）。**不发 delta**。
    """
    duanyan_slash_bonus: int = 0
    """本阶段「使用【杀】的次数上限 +N」的 N（陆抗移动版【决堰】·武器栏；lukang-mobile 票 01）。

    写入唯一入口 :meth:`Engine.set_duanyan_slash_bonus`（【决堰】提交点）；**清理点 = 出牌
    阶段结束**（:meth:`Engine._phase_play_end`，与【奇谋】的 ``qimou_count_x`` 同一个收尾点），
    回合末再兜一次。读取方是同一条技能定义上的 ``COUNT_LIMIT`` 修饰器（只对【杀】族；
    无次数限制者保持 ``None``）。**不发 delta**：与 ``qimou_count_x`` 同姿态。
    """
    duanyan_no_distance: bool = False
    """本回合「使用牌无距离限制」是否生效（【决堰】·两个坐骑栏；lukang-mobile 票 01）。

    写入唯一入口 :meth:`Engine.set_duanyan_no_distance`；**清理点 = 回合末**
    （:meth:`Engine._clear_turn_effects`——牌面写「本回合」）。读取方是同一条技能定义上的两条
    修饰器：``TARGET_POOL``（【杀】族把全体其他存活座位并入池 = 豁免攻击范围，与【诈降】
    同一手法）与 ``DISTANCE_LIMIT``（有额定距离上限的牌返回 ``None`` = 豁免距离）。**不发 delta**。
    """
    paoxiao_bonus: bool = False
    """本回合"下一次【杀】造成伤害 +1"的待消费标记（界张飞【咆哮】②，M6 票 05）。

    写入唯一入口 :meth:`Engine.set_paoxiao_bonus`：自己使用的【杀】被【闪】抵消时置真、
    那次伤害结算完（``DAMAGE_AFTER_TAKE``，伤害 > 0）置假；回合末与下一回合起点各清一次
    （与【酒】/【裸衣】同一收尾点）。这是 D9 的**一次性效果**：作用座位 = 自己、增量固定、
    过期 = 回合末，**不随技能实例存亡撤回**（故住座位而不是 ``skill_state``）。
    **不发 delta**：与 ``luoyi_active`` 同姿态——回放按同一条【闪】的应答重建。
    """
    discarded_this_turn: bool = False
    """本回合**弃置过牌**（lei-batch 票 03 的框架面；诸葛瞻【罪论】选项 2 的内置检测器）。

    用户 2026-11-05 裁定：「未弃置过牌」= 技能拥有者于**自身回合内**弃置牌时置 ``False``
    的检测器、**自身回合结束后**复位。落点 = :meth:`Engine.move_in_frame` 的
    ``reason is DISCARD`` 分支（``actor`` 是弃置者：自己被拆牌 / 被别人弃置不算"你弃置"，
    与【过河拆桥】一类"别人动手"的口径一致）。复位点与 ``damage_dealt_this_turn`` 同一处
    （``Engine._clear_turn_effects``）——那正是"回合结束后"，不需要新时机。
    **不发 delta**：与 ``hand_card_prohibited`` 同姿态。
    """
    turn_card_entries: list[tuple[int, str]] = Field(default_factory=list)
    """本回合该座位**使用或弃置过的牌**：``(card_id, 大类)`` 按首次出现序去重（【定品】判据）。

    写入唯一入口 = 引擎侧两处：① **使用** = :meth:`Engine._note_turn_card`（``start_use``
    入口，与 ``play_phase_last_card`` 同一个调用点；**打出不算**）；② **弃置** =
    :meth:`Engine.move_in_frame` 的 ``reason is DISCARD`` 分支（与 ``discarded_this_turn``
    同一行；``actor`` 是弃置者）。作用域 = **本回合**：回合末由
    :meth:`Engine._clear_turn_effects` 清空，故"本回合"的口径不跨回合。

    **为什么带 ``card_id`` 而不是只存去重后的大类**：界陈群【定品】的 ♥ 分支要把**本次**
    以此法弃置的那一张从记录里撤回（:meth:`Engine.exempt_turn_card`）——只存大类时，
    "本回合先前已弃过一张基本牌、这次以基本牌发动并判 ♥"会把先前那条一起撤掉 ⇒
    **过度放行**。读取面在技能里做投影（``{大类 for _, 大类 in entries}``），本字段是唯一真源。

    纯虚拟牌 / 查不到牌名大类的牌**不记录**（``card_category`` fail-closed 返回 ``None``）；
    对应的成本面同样把它们排除在外（"证不出它不属于用过的类型" ⇒ 不能当代价）。
    **不发 delta**：与 ``discarded_this_turn`` 同姿态——回放按同一批使用 / 弃置动作重建。
    """
    dingpin_barred_targets: list[int] = Field(default_factory=list)
    """本回合**不能再对其发动【定品】**的目标座位（去重、座位升序）。

    写入唯一入口 :meth:`Engine.bar_dingpin_target`（界陈群【定品】黑色判定分支的
    「然后你本回合不能再对其发动此技能」）；读取方 = 同一条技能声明的候选闸与座位池。
    作用域 = **本回合**（``Engine._clear_turn_effects`` 清空）。**不发 delta**：与
    ``heart_slash_bonus_against`` 同姿态（回放按那次发动的判定结果重建）。
    """
    hand_limit_bonus: int = 0
    """本回合**手牌上限的加式增量**（M6 票 08；spec D11 / D9 的第二轴）。
    写入唯一入口 :meth:`Engine.add_hand_limit_bonus`（【清俭】作用于**当前回合角色**，
    【集智】作用于自己）；读取方是 :func:`sgs.effects.hand_limit` 的加式段。形状 =
    D9 的**一次性效果**（作用座位、增量、过期 = 回合末）：增量住**座位**、不随来源技能
    的失去 / 失效撤回，回合末由 :meth:`Engine._clear_turn_effects` 清空。

    作用座位就是**落增量时**的 ``state.turn_seat``（【清俭】）/ 技能持有者本人（【集智】），
    此后**不再重定向**：当前回合角色中途阵亡时，增量仍记在那个座位名下（死者不再弃牌，
    读不到它），回合末与其它 ``TURN`` 作用域的状态一起清掉。**不发 delta**：与
    ``paoxiao_bonus`` 同姿态——没有 JSON 读者，回放按同一条技能动作重建。
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
    round: int = 1
    """当前**轮次**（M6 票 11；spec D13 / §回合与轮次）：自然回合转完一圈 +1，从 1 起。

    "轮"是 M6 新立的概念：`三国杀详细规则.txt` §三 3.8 把它定成"从当前回合角色开始，
    经过其余角色各一个回合、再次回到当前回合角色之前"。自然指针
    （:attr:`next_seat`）只在**非额外回合**结束时前进，回到本轮起始座位
    （:attr:`round_start_seat`）时轮次 +1——额外回合因此不会刷新「每轮限 X 次」。

    只有「每轮限 X 次」的技能读它（M6 = 界吕蒙【博图】），但**记录流只在有读者时**才落
    ``turn_change``（见该 delta 的注释）：没有读者时轮次不改变任何规则结果，
    标准 47 条金录像因此逐字节不变。
    """

    round_start_seat: int | None = None
    """本轮**起始座位**（轮次判据的锚点；``None`` = 还没定，人为局面从当前回合起算）。

    判据不是"回到它"而是"自然指针在座位环上**转过**它"：本轮起点座位可能在本轮中途
    阵亡，那时"回到它"永远不会发生、轮次会卡死；越过它同样意味着这一圈走完了
    （:meth:`Engine._advance_turn`）。
    """

    next_seat: int | None = None
    """**自然指针**：下一个**自然**回合的座位（M6 票 11 / spec D13）。

    只在**非额外回合**结束时前进（:meth:`Engine._advance_turn`）：额外回合出栈接管时它
    一动不动，故"额外回合插在自然下家之前"而不是把它挤掉。``None`` = 第一个回合还没结束。
    """

    turn_extra: bool = False
    """当前回合是不是**额外回合**（M6 票 11）。

    回合结束时据此决定自然指针要不要前进：额外回合结束**不**推进它，否则额外回合会被
    当成"又走了一圈"，把「每轮限 X 次」无限刷新。
    """

    extra_turns: list[int] = Field(default_factory=list)
    """**额外回合待执行栈**（M6 票 11 / spec D13）：回合结束时压入，下个回合开始时弹出。

    显式栈而不是"插队改 turn_seat"：压栈点与接管点因此各只有一处，且额外回合可以连续
    叠加（X > 1 时同一轮里能连拿几个）。弹出顺序 = **LIFO**（最后压入的那个紧接当前
    回合，官方"插在当前回合之后"的直读）；轮到已阵亡的座位时直接丢弃（没有它的回合）。
    """

    round_use_counts: dict[str, int] = Field(default_factory=dict)
    """**本轮**的「每轮限 X 次」已用次数（M6 票 11）：实例键 → 次数。

    与 :attr:`skill_state` 的位图分列而不是再挤一位：位图存"有没有用过"，而每轮限次是
    **计数**（X 可以大于 1，【博图】的 X = 存活角色数且至多 3），一位表达不了。键 = 实例键
    （``skills.instance_key``，与 ``skill_state`` 同一套身份）；写入口
    ``skills.mark_round_skill_used``，读入口 ``skills.round_skill_uses``，
    **唯一复位点** = 轮次 +1（``skills.reset_round_skill_state``）——额外回合不经过它，
    故额外回合不会刷新每轮次数（票面核心反例）。**不发 delta**：每轮次数可由记录流里的
    ``turn_change``（轮次）+ 发动动作重放重建，且它没有任何 JSON 读者。
    """

    discard_suits_this_turn: list[str] = Field(default_factory=list)
    """**本回合**置入弃牌堆的牌出现过的花色（M6 票 11 的【博图】判据；按首次出现序、去重）。

    判据的读取方只有一处：界吕蒙【博图】"若本回合置入弃牌堆的牌中包含四种花色"。裁定
    （`06`（§5「程序化约定与偏离一手」））是**计入本回合所有来源置入的牌**——阵亡者清区、
    主公杀忠臣的弃置、【铁索】重铸、判定牌入弃牌堆……都算，故写入点在**搬牌原语**
    （:func:`sgs.effects.move_card`）里，而不是各调用点自己记：少了那一道，任何一条没接上
    的来源都会让技能静默不发动。复位点 = 下一个回合的 ``TURN_START``（额外回合也各有一份：
    作用域 = **一个回合**，不是一轮）。
    """

    def note_discard_suits(self, cards: Iterable[CardRef]) -> None:
        """记下这一批**进入弃牌堆**的牌的花色（M6 票 11 的唯一写入口）。

        按首次出现序去重（本项目禁无序容器参与顺序，源码纪律 §3）；花色为空（纯虚拟牌）
        的牌不占位——它们从不进弃牌堆。
        """
        for card in cards:
            suit = card.suit
            if suit is not None and suit not in self.discard_suits_this_turn:
                self.discard_suits_this_turn.append(suit)

    skill_state: dict[str, int] = Field(default_factory=dict)
    """**per-instance 技能状态**（票 07 §6 / 票 30）：实例键 → 位图（"已用次数"等）。

    实例键 = ``(owner, skill_id, source)`` 的规范文本（``skills.instance_key``）；
    位布局住 :mod:`sgs.skills.catalog`（唯一声明处）：低位是 A 族**本出牌阶段**的阶段位
    （``PHASE_PLAY_START`` 只清它们），高位是限定技的**整局已用**位（永不随阶段复位）。
    来源消失（装备离场 / 技能失去）时由 ``skills.prune_skill_state`` 清理——暂时失效不算
    来源消失；M2 的 17 条装备没有持久状态，故本字段实际恒空。
    """
    card_tags: dict[int, tuple[CardTag, ...]] = Field(default_factory=dict)
    """**牌标签**：``card_id → 该牌上的标签（按加入序）``（M6 票 09；spec D12）。

    标签是**牌**的持久事实、**与区域无关**（:class:`CardTag` 的注释）：牌从手牌进弃牌堆，
    标签不会被搬走或清掉。主键是实体牌 id（虚拟牌恒不带标签），值一律用 ``tuple`` 保序、
    不用 ``set``（同种子逐字节复现，源码纪律 §3）。

    **不进记录流**（门票 09 未分配 delta）：回放按同一条技能决策重跑即可重建；写入唯一入口
    :meth:`~sgs.engine.Engine.add_card_tag`，清除走声明触发的唯一入口
    :meth:`~sgs.engine.Engine.clear_card_tags` 与 ``skills.prune_card_tags``。
    """
    drawn_true_names: tuple[str, ...] = ()
    """**已被抽走**的武将牌真名（票 05；票 02 §4.5 E1）：从武将牌堆抽走、本局不再可抽的那部分。

    只存"抽走"这**一半**；"在场"那一半由 :func:`sgs.skills.blocked_true_names` 从座位
    派生——座位上的真名不需要存（阵亡在死亡结算收尾后自动解除），而被抽走的必须存
    （否则重入会重抽、左慈也没法解除）。

    顺序 = **抽走序**（插入序、去重），故同种子逐字节复现。**不进记录流、不发 delta**：
    没有 JSON 读者，回放按同一份决策序列重跑引擎即可重建。写入 / 释放的唯一入口是
    :meth:`~sgs.engine.Engine.note_drawn_true_name` /
    :meth:`~sgs.engine.Engine.release_drawn_true_name`。

    **没有任何初始化点**（E5）：无武将模式 ⇒ 恒空元组 ⇒ M2 基准逐字节不变；有武将的
    座位同样不写它（在场是派生出来的，见上）。
    """

    def tags_of(self, card_id: int) -> tuple[CardTag, ...]:
        """一张牌上的全部标签（没有 = 空元组，按加入序）。"""
        return self.card_tags.get(card_id, ())

    def has_card_tag(self, card_id: int, tag_type: CardTagType) -> bool:
        """这张牌有没有某个**类型**的标签（读取方的唯一谓词）。"""
        return any(tag.tag_type is tag_type for tag in self.tags_of(card_id))


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

        共享区（牌堆 / 弃牌堆 / 处理区）忽略座位；逐座位的区必须给出座位；武将牌上的牌区
        还必须给出**技能实例**（区键的第二维，缺了会落进"同一张表"、把两个技能的区串了）。
        区**必须先存在**（放置入口 :meth:`Engine.place_on_hero_card` 建区）——目的地不存在
        时在这里 fail-closed，而不是悄悄造一个没有技能身份的区。
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
        if ref.zone is Zone.HERO:
            if ref.skill_instance is None:
                raise ValueError(
                    "武将牌上的牌区按技能实例独立，区域坐标必须带 skill_instance"
                )
            zone = player.hero_cards.get(ref.skill_instance)
            if zone is None:
                raise ValueError(
                    f"座位 {ref.seat} 的武将牌上没有 {ref.skill_instance!r} 这个放置区"
                    "（先由 Engine.place_on_hero_card 建立）"
                )
            return zone.cards
        raise ValueError(f"未知区域：{ref.zone!r}")

    def hero_zone(self, seat: int, skill_instance: str) -> tuple[int, ...]:
        """座位 ``seat`` 的武将牌上、``skill_instance`` 那个区里的牌（没有该区 = 空元组）。

        读访问器（不建区、不变状态）：投影（票 19）与构造局面测试都经它读，不必知道
        ``hero_cards`` 的内部形状。
        """
        zone = self.player(seat).hero_cards.get(skill_instance)
        return () if zone is None else tuple(zone.cards)
