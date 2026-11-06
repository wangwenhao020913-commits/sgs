"""变化事件（delta）的载荷类型：一次状态变化的纯事实（票 06 §2 / §3）。

``delta_type`` 是记录流 schema 的一部分，后补会动事件版本，所以即使 M1 的骨架期只用得到
``card_move`` 也先建位。现有 11 种：M1 的八种（``card_move`` / ``hp_change`` /
``chained_change`` / ``wine_change`` / ``card_reveal`` / ``deck_shuffle`` /
``identity_deal`` / ``identity_reveal``）之后，``chained_change``（M5-03 横置）、
``wine_change``（M5-04 酒）与 ``card_reveal``（M5-06 火攻亮牌）都是就地追加；M6 票 04 再
追加技能三态的 ``skill_gain`` / ``skill_lose`` / ``skill_suppress``（技能获得 / 失去 /
失效），M6 票 11 追加 ``turn_change``（额外回合 / 轮次变化），M6 票 20 追加标签增删的
``tag_add`` / ``tag_remove``（spec §投影与记录流），记录流仍为事件版本 2。记录层
（``engine``）在这些模型上**继承**出带 ``seq`` / ``frame_id`` / ``depth`` 的记录子类，
从而 JSON 是平铺的。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_serializer

from sgs.data import CardTagType, Identity

from .models import (
    CardRef,
    MoveReason,
    SkillSuppressionPredicate,
    SkillSuppressionScope,
    ZoneRefField,
    _FrozenModel,
)

__all__ = [
    "CardMoveDelta",
    "CardRevealDelta",
    "ChainedChangeDelta",
    "FlippedChangeDelta",
    "MarkerChangeDelta",
    "DeckShuffleDelta",
    "Delta",
    "HpChangeDelta",
    "HpMaxChangeDelta",
    "IdentityDealDelta",
    "IdentityRevealDelta",
    "PindianDelta",
    "SkillGainDelta",
    "SkillLoseDelta",
    "SkillSuppressDelta",
    "TagAddDelta",
    "TagRemoveDelta",
    "TurnChangeDelta",
    "WineChangeDelta",
]


class CardMoveDelta(_FrozenModel):
    """一批实体牌从**一个**来源区域移动到目标区域（一次操作一条）。

    ``from_zone`` / ``to_zone`` 是**区域坐标**（区种类 + 座位）：一次弃置两张不同来源的牌
    是两条 delta，故每条只有一个来源坐标。``owner`` 是**归属**——"本次移动算谁的"
    （判定牌 = 被判定者、弃置 / 使用 = 弃置者 / 使用者、摸牌 = 摸牌者、装备置入 = 持有者）；
    只有"无角色主体"的移动（如终局收尾）为 ``None``。归属 ≠ 区域坐标：判定区里的牌
    坐标带座位，但牌本身仍不为任何角色所拥有（规则 11.6）。

    ``to_top``（票 02 的落点）：这批牌进入目标区域时是**成为牌堆顶**（``True``）还是接在
    目标区尾部（缺省 ``False``）。只有进牌堆的移动才有落点——别的区没有"顶"这个位置，
    故 ``move_card`` 对其它目标区直接 ``ValueError``。有了它，"置于牌堆顶"是一次**跨区
    移动**（不是"先进牌堆尾、再同区重排"），挂起劈不开两步，记录流也自描述：重建器读
    这一条 delta 就能落到堆顶，不必再从"上一条 ``SelectOption(deck_top)``"猜。

    **缺省值不进序列化**（与 ``protocol.SelectionSpec.max_per_suit`` /
    ``StepPool.max`` 同一姿态）：``record_json`` 是唯一 canonical 序列化器、不做默认值
    过滤，若每条 ``card_move`` 都多一个 ``"to_top":false``，70 条录像的"录制 == 重放"
    会全线字节变化。故这里挂一个只在 ``True`` 时写字面段的 wrap serializer：旧文件
    （没有该字段）解析回 ``False``、再 dump 仍是同一串字节。
    """

    delta_type: Literal["card_move"] = "card_move"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRefField
    to_zone: ZoneRefField
    reason: MoveReason
    owner: int | None = None
    """本次移动的归属角色；"无角色主体"时为 ``None``。"""
    to_top: bool = False
    """落点：``True`` = 成为牌堆新顶（元组顺序即自顶向下）；缺省 ``False`` = 接在区尾。"""

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if not data.get("to_top"):
            data.pop("to_top", None)
        return data


class HpChangeDelta(_FrozenModel):
    """体力被写成某个值（``apply_hp`` 是唯一改体力的口）。"""

    delta_type: Literal["hp_change"] = "hp_change"
    seat: int
    before: int
    after: int
    amount: int


class HpMaxChangeDelta(_FrozenModel):
    """一个座位的**体力上限**被写成某个值（M6 票 06 的觉醒技"减 1 点体力上限"）。

    与 :class:`HpChangeDelta` 并列而不是复用它：体力上限与当前体力是**两个独立事实**
    （§四 4.15 的四种数值变化事件各自建模），合并会让"减上限但体力不变"（【潜心】）
    与"上限与体力同时下调"（【勤学】在满体力时）无法区分。减上限同时须调整体力值的那
    一档由调用方另发一条 ``hp_change``（规则：减上限前体力值等于上限 ⇒ 同步调整），
    故记录流里两条 delta 并存就是完整事实。

    **公开事实**：体力上限在投影（``PlayerPublic.hp_max``）里本来就可见，故载荷原样下发，
    不进不泄露的裁剪面（与 ``hp_change`` 同档）。
    """

    delta_type: Literal["hp_max_change"] = "hp_max_change"
    seat: int
    before: int
    after: int
    amount: int


class ChainedChangeDelta(_FrozenModel):
    """一个座位的**横置**状态被写成某个值（M5-03 / 决策 D5）。

    写入口唯一：``Engine.set_chained``。铁索连环的 toggle 与属性伤害的"结算后立即重置"
    都经它发这条 delta——重置因此发生在**同一伤害事件内**（记录流里与那条
    ``hp_change`` 同帧），不另开事件帧，也不产生第二个时机。
    """

    delta_type: Literal["chained_change"] = "chained_change"
    seat: int
    chained: bool


class FlippedChangeDelta(_FrozenModel):
    """一个座位的**翻面**状态被写成某个值（本票的翻面机制）。

    写入口唯一：``Engine.set_flipped``。两处调用：技能效果把它翻到背面（【据守】），
    回合环在"回合开始前"把它翻回正面（规则集 13.2 的固定流程）。与 ``chained_change``
    同形（只记**新值**：``flipped``），因为它同样是"座位状态 + 网页座位卡读投影"。
    """

    delta_type: Literal["flipped_change"] = "flipped_change"
    seat: int
    flipped: bool


class MarkerChangeDelta(_FrozenModel):
    """一个座位身上的**标记**数量被写成某个值（票 06；用户 Q6）。

    唯一写入口 ``Engine.add_marker`` / ``Engine.remove_marker``；`count` 是**绝对值**
    （0 = 该标记已移除）。实例身份 = `(目标座位, name, source_seat, source_skill_id)`；
    同名同来源合并、异来源分列；标记是公开信息（显示在目标武将牌上），但**不进记录流**的
    投影面（``PlayerPublic.markers`` 为 ``exclude``），变化经这条 delta 下发。
    """

    delta_type: Literal["marker_change"] = "marker_change"
    seat: int
    name: str
    source_seat: int
    source_skill_id: str
    count: int


class WineChangeDelta(_FrozenModel):
    """一个座位的【酒】加伤状态被写成某个值（M5-04 / 决策 D5）。

    写入口唯一：``Engine.set_wine_active``。出牌阶段使用【酒】把它写成 ``True``；兑现给
    下一张【杀】的使用、以及回合末清理都经它写成 ``False``。与 ``chained_change`` 同形
    （只记**新值**：``active``），因为它同样是"回合内的临时状态 + 网页座位卡读投影"。

    加伤只在本回合有效，故它不会跨回合出现在记录流里；标准 108 无【酒】，故标准基准
    一条都不会出现这条 delta。
    """

    delta_type: Literal["wine_change"] = "wine_change"
    seat: int
    active: bool


class CardRevealDelta(_FrozenModel):
    """一张**手牌**成为公开事实（M5-06 的【火攻】亮牌 / 决策 D10）。

    只揭示、不移动：亮出的牌仍留在原区（火攻亮出的手牌留在手里，不离开手牌区）。
    因此它**不是** ``card_move``——不进牌守恒的搬运记账，只回答"哪张牌此刻被公开了"。
    ``seat`` = 亮出者（手牌持有者），``card`` = 被公开的那一张。

    **不持久**：不写任何"已知"状态（手牌区不留标记），记录流里的这一条就是全部公开
    事实；未亮出的手牌一张都不在这里出现。标准 108 无【火攻】，故 47 条金录像一条都
    不会出现这条 delta。
    """

    delta_type: Literal["card_reveal"] = "card_reveal"
    seat: int
    card: CardRef


class PindianDelta(_FrozenModel):
    """一次**拼点**的揭晓（huo-taishici-xunyu 票 03）：拼点弹窗与结果展示的数据面。

    拼点不是使用 / 打出（规则第十二章 12.3 L1336），它的"结果"没有别的记录形态可承载：
    两条时机记录只给时点、``card_move`` 只给搬运。故这是**结果本身**的唯一公开事实：

    - ``initiator`` / ``targets`` / ``cards``：牌序 = 发起者的拼点牌在前，其余按 ``targets``
      序一一对应（1 位发起者 vs N 位被拼点者）；
    - ``won``：与 ``targets`` 对齐的布尔；**平点 = 双方都没赢**（规则 12.1 L1318–L1319 /
      12.4 L1345–L1353）⇒ 平点时 ``won`` 为假；
    - ``tied``：与 ``targets`` 对齐的**平点**位——``won`` 只有两态，平点与"输"都落
      ``won=False``；展示面要区分「都没赢」（用户 2026-11-04 的 R7 口径）就必须有这一位，
      否则前端只能拿两张牌的点数自己比一次（那会把规则的比较搬第二份到界面上）。缺省空
      元组 = 旧记录没有这个事实（按"没赢"画）；
    - ``title``：发动拼点的技能名（弹窗标题；服务端给，前端不拼）。
    - ``ranks``：与 ``cards`` 对齐的**生效点数**（tao-caocao 票 03）——"牌扣置入处理区后、
      亮出前"有技能可以调整点数（``RANK_COMPARE_PLACED`` + ``Engine.boost_pindian``），
      而牌面点数已经印在卡面上：不把生效点数给出来，弹窗就会出现"5 点输给 4 点"的错觉。
      服务端已经把规则 12.2 第 4 步的上下限（1..13）钳过；本字段只是**事实**，不参与判定。
      缺省空元组 = 旧记录没有这个事实（前端不画点数，也不猜）。

    **全场可见**（揭晓后牌面即公开事实）；揭晓前没有任何一条 delta 带这两张牌的面——
    扣置阶段它们**还留在各自手牌里**（本仓把"扣置"与"揭晓"合并在同一步做）。
    """

    delta_type: Literal["pindian"] = "pindian"
    title: str
    initiator: int
    targets: tuple[int, ...]
    cards: tuple[CardRef, ...]
    won: tuple[bool, ...]
    tied: tuple[bool, ...] = ()
    ranks: tuple[int | None, ...] = ()


class DeckShuffleDelta(_FrozenModel):
    """一次洗牌置换；``cards`` 是**洗后**自顶向下的牌堆顺序，不记洗前顺序。

    洗牌**无归属**（无角色主体）；区域引用与 ``card_move`` 同形，一并升为区域坐标。
    """

    delta_type: Literal["deck_shuffle"] = "deck_shuffle"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRefField | None
    """初次洗牌为 ``None``；重洗为 ``{"zone": "discard", "seat": null}``。"""

    to_zone: ZoneRefField


class IdentityDealDelta(_FrozenModel):
    """一个座位被发到身份（票 13 §3）：逐座一条、落在发牌**之前**、不新增时机节点。

    身份不是牌：不进 ``cards``、不占牌守恒、与 :class:`MoveReason` 无关。
    """

    delta_type: Literal["identity_deal"] = "identity_deal"
    seat: int
    identity: Identity


class IdentityRevealDelta(_FrozenModel):
    """一个座位阵亡时亮明身份（票 25 / 决策回票 14 §5）：死亡结算内、检测之前逐座一条。

    与 :class:`IdentityDealDelta` 同形但语义不同——发放是开局**暗发**（逐座落在发牌前），
    亮明是阵亡**公开**（`DEATH_REVEAL_IDENTITY_BEFORE` 的时机 dispatch 之后）。身份不是牌：
    不进 ``cards``、不占牌守恒，与 :class:`MoveReason` 无关。
    """

    delta_type: Literal["identity_reveal"] = "identity_reveal"
    seat: int
    identity: Identity


class SkillGainDelta(_FrozenModel):
    """一个座位**获得**了一条技能实例（M6 票 04 的第四种来源）。

    技能不是牌：不进 ``cards``、不占牌守恒、与 :class:`MoveReason` 无关。``skill_id`` 是
    被授予的**技能定义 id**；来源种类恒为 ``GRANTED``（实例本身由载体解析按座位状态重建，
    故 delta 只记"谁获得了哪条"，不重复来源形状）。
    """

    delta_type: Literal["skill_gain"] = "skill_gain"
    seat: int
    skill_id: str


class SkillLoseDelta(_FrozenModel):
    """一个座位**失去**了一条技能（M6 票 04）：实例不再存在，且不可恢复。

    与 :class:`SkillGainDelta` 同形；"失去 ≠ 失效"由两个 delta 类型分开表达
    （`GLOSSARY.md`：失效可恢复、失去不可恢复）。
    """

    delta_type: Literal["skill_lose"] = "skill_lose"
    seat: int
    skill_id: str


class SkillSuppressDelta(_FrozenModel):
    """一个座位的**失效**记录被加上 / 撤掉（M6 票 04）。

    ``skill_id`` = **施加失效的那条技能**（不是被挡住的技能）：失效率是座位级的，
    一条记录覆盖整个座位，逐技能展开会写出与规则无关的噪声。``suppressed=False`` 是
    作用域到期（回合末）那一条，使"可恢复"在记录流里可被看见。
    """

    delta_type: Literal["skill_suppress"] = "skill_suppress"
    seat: int
    skill_id: str
    suppressed: bool
    scope: SkillSuppressionScope = SkillSuppressionScope.TURN
    predicate: SkillSuppressionPredicate = SkillSuppressionPredicate.ALL_NON_LOCKED


class TagAddDelta(_FrozenModel):
    """一张**实体牌**被挂上一个标签（M6 票 20 补；spec §投影与记录流「标签增删」）。

    标签是牌上的**持久事实、与所在区域无关**（``CardTag``）：本 delta 只记"哪张牌被挂上
    哪个标签、来源实例是谁"，不移动牌、不占牌守恒（与 ``card_reveal`` 一样不是
    ``card_move``）。``tag_type`` 的**可见性**由登记表（``sgs.data.CARD_TAG_TYPES``）声明：
    ``HIDDEN`` 的下发面整条裁掉；``FACE_VISIBLE`` 只在网页载荷按牌面可见补 tags，
    记录流的**人类下发**仍不出现标签 delta（全知通道照旧；见 ``sgs_web.visibility.tag_visible``）。

    ``owner`` / ``skill_id`` = 来源实例的规范身份（与 ``CardTag`` 同形）；标签的生存期与
    清除触发也由登记表回答，故本 delta 不重复声明。
    """

    delta_type: Literal["tag_add"] = "tag_add"
    card: CardRef
    tag_type: CardTagType
    owner: int
    skill_id: str


class TagRemoveDelta(_FrozenModel):
    """一张**实体牌**的标签被清除（M6 票 20 补；spec §投影与记录流「标签增删」）。

    与 :class:`TagAddDelta` 同形：``card`` + ``tag_type`` + 来源实例。清除的三条触发都在
    记录流里落这一条——回合末（``TURN_END``）、随来源实例消失（失去 / 失效）、以及
    ``LEAVE_ZONE`` / ``USED_OR_PLAYED`` 这两个 M6 无载体的形状。**牌不动**：标签的增删
    不改变牌所在的区域，也不改变投影里公开的「手牌数」。
    """

    delta_type: Literal["tag_remove"] = "tag_remove"
    card: CardRef
    tag_type: CardTagType
    owner: int
    skill_id: str


class TurnChangeDelta(_FrozenModel):
    """一个**回合边界**的事实（M6 票 11；spec D13 / §回合与轮次）。

    "哪一回合"由既有的 ``TURN_START`` 时机回答；本 delta 只记**它读不出来的那两件事**：

    - ``extra=True``：``seat`` 的**额外回合**开始了（从待执行栈弹出并接管）。额外回合的
      ``TURN_START`` 与自然回合逐字相同，故没有这一条时"同一个座位连着两个回合"会被读成
      "进入了下一轮"（``table.js:roundNumber`` 的旧派生就是这一条）。
    - ``extra=False``：**轮次变化**——新一轮从 ``seat`` 的自然回合开始，``round`` 是新轮次。
      本轮起点座位中途阵亡时，自然指针越过它也算一圈（见 ``Engine._advance_turn``）。

    **只在有载体的对局里出现**：轮次只有「每轮限 X 次」的技能会读（M6 = 界吕蒙【博图】），
    没有读者时轮次不改变任何规则结果，故不落记录——标准 47 条金录像因此逐字节不变。

    公开事实（回合座位与轮次都在明面上），无需按可见性裁剪：载荷原样下发。
    """

    delta_type: Literal["turn_change"] = "turn_change"
    seat: int
    round: int
    extra: bool = False


Delta = Annotated[
    CardMoveDelta
    | HpChangeDelta
    | HpMaxChangeDelta
    | ChainedChangeDelta
    | FlippedChangeDelta
    | MarkerChangeDelta
    | WineChangeDelta
    | CardRevealDelta
    | PindianDelta
    | DeckShuffleDelta
    | IdentityDealDelta
    | IdentityRevealDelta
    | SkillGainDelta
    | SkillLoseDelta
    | SkillSuppressDelta
    | TagAddDelta
    | TagRemoveDelta
    | TurnChangeDelta,
    Field(discriminator="delta_type"),
]
