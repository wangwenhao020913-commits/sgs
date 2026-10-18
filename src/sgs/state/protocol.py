"""询问与行动的协议 schema，以及参与者视角投影（票 05 / 票 07 / 票 27）。

M1 四类询问（``PLAY_PHASE`` / ``DODGE`` / ``PEACH`` / ``DISCARD``）各一个 frozen 模型，
以 ``kind`` 作判别字段；M2 票 27 按决策结构再补两类：

- ``RESPOND``：**响应窗口**——"从候选里挑一张牌行动或放弃"，覆盖**打出**（南蛮入侵 /
  万箭齐发 / 决斗）与**借刀杀人的"使用【杀】"**（无懈的窗口归票 28）；
- ``SELECT_CARDS``：**选牌**——从**选择面**（槽位表）里挑 k 张，覆盖过河拆桥 / 顺手牵羊 /
  五谷丰登。

行动构造子同样从四个扩到六个：新增 ``PlayCard``（打出：无目标、走 L6、**不占使用次数**，
不能用 ``UseCard(targets=())`` 冒充）与 ``SelectCards``（用**面内位置**引用，不携带牌面身份）。

候选行动**内联**在询问里，参与者只从中挑一个。``answers_seq`` 是行动自带的字段：候选在
**构造询问时**生成，那时询问的 ``seq`` 已定，于是"被提供的"与"被接受的"共享同一个陈旧输入
校验键。外来应答经 :func:`parse_action` 解析：这是行动 schema 的**唯一入口**，schema 不认的
输入（未知 ``kind`` / 缺字段 / 多余字段）抛 pydantic ``ValidationError``，由引擎归类为
``Malformed``（票 07 §5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_serializer

from sgs.data import Identity, Kingdom
from sgs.timing import Timing

from .models import CardRef, Phase, Zone, ZoneRef, _FrozenModel

__all__ = [
    "Action",
    "ActivateSkill",
    "ArrangeCards",
    "ArrangeContext",
    "ArrangeQuery",
    "DiscardCards",
    "DiscardQuery",
    "DodgeContext",
    "DodgeQuery",
    "EndPhase",
    "Pass",
    "PeachQuery",
    "PlayCard",
    "PlayPhaseQuery",
    "PlayerPublic",
    "PlayerView",
    "Query",
    "QueryKind",
    "RespondContext",
    "RespondMode",
    "RespondQuery",
    "SelectCards",
    "SelectCardsQuery",
    "SelectContext",
    "SelectOption",
    "SelectOptionContext",
    "SelectOptionQuery",
    "SelectionSpec",
    "SkillActivationContext",
    "SkillActivationQuery",
    "Slot",
    "Step",
    "StepPool",
    "UseCard",
    "WindowCause",
    "parse_action",
]


class QueryKind(str, Enum):
    """九类询问的标识；候选生成器按它分派（票 05 §1 / 票 19 §1 / 票 12 §Q6）。

    M1 四类一字不改；票 27 落 ``RESPOND`` / ``SELECT_CARDS``；票 30 落
    ``SKILL_ACTIVATION``（"是否发动"）与 ``SELECT_OPTION``（二选一，选择权在目标）；
    票 10 落 ``ARRANGE``（排列：一次观星的"任意顺序 + 切分点"是**一份决策**，
    拆成多次选牌会让顺序语义靠询问序号隐式表达）。
    """

    PLAY_PHASE = "PLAY_PHASE"
    DODGE = "DODGE"
    PEACH = "PEACH"
    DISCARD = "DISCARD"
    RESPOND = "RESPOND"
    SELECT_CARDS = "SELECT_CARDS"
    SKILL_ACTIVATION = "SKILL_ACTIVATION"
    SELECT_OPTION = "SELECT_OPTION"
    ARRANGE = "ARRANGE"


class WindowCause(str, Enum):
    """响应窗口的**成因**（票 19 §3；M2 闭集，M3 只许增）。

    票 27 落四个效果内窗口；``NULLIFY`` 由票 28 的无懈窗口使用，此处先把闭集建全。
    票 09（M3）加 ``LORD_REQUEST``：**势力求助窗口**的成因——主公技令其他同势力角色
    打出一张牌（【护驾】/【激将】），与"这张牌自己的响应窗口"（``DODGE`` / ``DUEL`` 等）
    分列，使记录流里读得出"这次询问来自求助"。
    票 11（M3）加 ``REJUDGE``：**改判窗口**的成因——判定牌生效前，有改判能力的角色
    打出一张手牌代替之（`11.3`）；它挂在判定帧上、可循环重开，与效果自己的响应窗口分列。
    """

    NULLIFY = "NULLIFY"
    BARBARIAN_INVASION = "BARBARIAN_INVASION"
    ARROW_BARRAGE = "ARROW_BARRAGE"
    DUEL = "DUEL"
    BORROWED_SWORD = "BORROWED_SWORD"
    LORD_REQUEST = "LORD_REQUEST"
    REJUDGE = "REJUDGE"


class RespondMode(str, Enum):
    """响应窗口要求的是**打出**还是**使用**（票 19 §1 的候选型别分野）。

    打出（南蛮 / 万箭 / 决斗）⇒ 候选 ``PlayCard``；借刀杀人的"使用【杀】"⇒ 候选 ``UseCard``
    （目标由 ``use_targets`` 给出）。``mode`` 只用于**生成候选**，不进记录——记录里读得出
    候选是哪种构造子。
    """

    PLAY = "PLAY"
    USE = "USE"


class Slot(_FrozenModel):
    """选择面上的一个可点位置（票 19 §3）。

    ``ref`` 是区域坐标；``card = None`` 表示**隐藏槽**（只有位置、没有牌面身份，
    如他人手牌）。Action 用**面内位置**引用槽位，故不泄露隐藏牌面。
    """

    ref: ZoneRef
    card: CardRef | None = None


class SelectionSpec(_FrozenModel):
    """选择面的**声明式**约束（票 19 §3）：展示哪些区、总共 / 每区各取几张。

    ``optional`` / ``sequential`` 是效果属性，M2 的拆、顺、五谷都取 ``(1, 1)`` 且非依次；
    字段按"M3 只许增"保留在协议里。
    """

    zones: tuple[Zone, ...]
    total: tuple[int, int]
    per_zone: tuple[tuple[Zone, int, int], ...] = ()
    optional: bool = False
    sequential: bool = False


class StepPool(_FrozenModel):
    """预发动清单里**一步的候选池**（W4-1）：``when`` = 前序步骤的答案，``options`` = 此刻的候选。

    ``when`` 是按步骤顺序摊平后的答案文本：选牌步给实体牌 id（十进制文本），选座位步给
    ``seat:N``，选项步给选项文本。空元组 = 这份子池与前序选择无关。

    **逐选项子池一次下发**：池依赖前序步骤的技能（【流离】选不选武器、【离间】先点谁）
    会为每个前序选项各给一行；接入层按玩家当前已选的值找对应行。行数超过
    :data:`~sgs.skills.flow.MANIFEST_POOL_LIMIT`、或前序是**多选**步且池确实随它变时，
    引擎**退化**为该步不下发候选（``Step.pool = ()``），由校验器裁决——退化只削弱界面
    提示，不改变合法性。
    """

    when: tuple[str, ...] = ()
    options: tuple[str, ...] = ()


class Step(_FrozenModel):
    """预发动清单的一步（W4-1）：``{name, zones, min, max, pool, order}``。

    ``name`` = 步骤键（效果按它从答案里取）；``zones`` = 选牌步铺哪些区（座位步 / 选项步
    为空）；``min`` / ``max`` = 张数区间。``max = None`` 表示"全部可选的牌"（声明面）；
    渲染器会把它解析成**当前可选牌数**（与 ``SelectionSpec.total`` 同一条解析），故下发的
    清单里通常是整数。``pool`` = 候选池（见 :class:`StepPool`）；
    ``order`` = 步骤序号（0 起）。

    清单由 ``SkillFlow.steps`` **渲染**而来（:func:`sgs.skills.flow.manifest`），校验器读
    同一份声明——两者不是两套规则。``pool`` 为空元组 = 本步没有（或退化掉了）候选索引，
    接入层可退化为"全部都点得动、提交时由校验器裁决"。
    """

    name: str
    zones: tuple[Zone, ...] = ()
    min: int = 0
    max: int | None = None
    pool: tuple[StepPool, ...] = ()
    order: int = 0


@dataclass(frozen=True, slots=True)
class RespondContext:
    """``RESPOND`` 候选生成的类型化载荷（票 19 §4 的 ``payload``）。

    ``providers_used`` 只在"需要【闪】"的响应窗口里用（票 10 §5.5 / 票 30）：把它记的
    技能 id 从候选里去掉，实现"每提供者只问一次"。
    """

    cause: WindowCause
    required: str | None = None
    target: int | None = None
    window_frame: int = 0
    mode: RespondMode = RespondMode.PLAY
    use_targets: tuple[int, ...] = ()
    providers_used: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelectContext:
    """``SELECT_CARDS`` 候选生成的类型化载荷：选择面 + 配额，逐字进询问。

    ``skill_id`` 是**这一步属于哪条技能**（票 05）：取自**调用方帧**的 ``skill_id``
    ——发动后流水线里那帧是 SKILL 帧，其余选牌窗口（顺、拆、五谷…）没有该键，为 ``None``。
    它随询问落在 :class:`SelectCardsQuery` 上供**本地消费者**（启发式 AI 的成本语义）读取，
    **不进记录流**（见该字段的 ``exclude``）——故既有 Golden Replay 一行不改。
    """

    spec: SelectionSpec
    slots: tuple[Slot, ...]
    skill_id: str | None = None
    provide_type: str | None = None
    """这一步是不是**"提供一张 B"**（票 14）：非空 = B 的牌名 id。

    非空时 ``slots`` 里除了区域里的**实体 B**，还有各转化技产出的**虚拟 B**
    （单来源，票 14 射程）；接入层据它把这张面**就地**铺到本人可见区域上，
    不再新开选择区（用户裁定）。与 ``skill_id`` 同姿态：**不进记录流**。
    """


@dataclass(frozen=True, slots=True)
class DodgeContext:
    """``DODGE`` 候选生成的类型化载荷（票 30）：本次"需要【闪】"里已问过的提供者 id。

    "每提供者只问一次"（票 10 §5）：【八卦阵】判定未通过后，再次询问同一个"需要【闪】"
    的窗口时必须不再提供它。载荷只装**已经问过**的技能 id，候选生成器据此过滤。
    """

    providers_used: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillActivationContext:
    """``SKILL_ACTIVATION`` 候选生成的类型化载荷（票 19 §5 / 票 30）。

    发动 = **一次询问**：``optional`` 决定候选里有没有 ``Pass``（"可以 / 必须"是效果属性，
    与 ``locked`` 正交）；``picks`` 型载荷（【贯石斧】弃两张）把面内位置烘进候选，
    **付不起的组合不进候选**（池 < ``min_picks`` ⇒ 候选为空 ⇒ 不构造询问）。
    """

    skill_id: str
    optional: bool = True
    min_picks: int = 0
    max_picks: int = 0
    slots: tuple[Slot, ...] = ()
    preactivation: tuple[Step, ...] = ()
    """本时机上的**预发动清单**（W4-1；被动入口）。

    由 ``SkillFlow.steps`` 渲染（:func:`sgs.skills.flow.manifest`），只含**由持有者本人**
    应答的前缀步骤；后续换成别人应答的段（反间猜花色 / 挑牌）仍然逐步询问。空元组 =
    这条技能此刻没有可下发的预发动清单（未迁移 / 选取面不可见 / 池为空）。
    """


@dataclass(frozen=True, slots=True)
class SelectOptionContext:
    """``SELECT_OPTION`` 候选生成的类型化载荷（票 12 §Q6 / 票 30）：选项 id 闭集。

    ``optional`` 是**取消能力**的显式开关（票 00 / N4）：缺省 ``False`` ⟹ 强制闭集，
    候选一条不多（雌雄双股剑、遗计"交给谁"的既有口径不变）；置 ``True`` 才多一条
    ``Pass``，供"发动后中途取消、取消不消耗技能使用次数"的技能流水线用。
    """

    skill_id: str
    options: tuple[str, ...]
    optional: bool = False


@dataclass(frozen=True, slots=True)
class ArrangeContext:
    """``ARRANGE`` 候选生成的类型化载荷（票 10）：本次授予了几张牌。

    候选 = ``count`` 个**面内位置**的全部排列 × 全部切分点，故候选生成只需要张数；
    牌面本身由询问的 :attr:`_QueryBase.revealed` 承载（对参与者可见、对其它座位不可见）。
    """

    count: int


class PlayerPublic(_FrozenModel):
    """公开区里的一个座位：对手只暴露手牌**数**，身份按可见性规则给出。

    ``equip`` / ``judge`` 是**公开信息**且逐座位（票 08 §6）。手牌上限**不在这里**：
    它由当前体力值派生（``effects.hand_limit``），不新增冗余字段。
    ``identity`` 只对**本人 / 主公 / 已死亡者**可见，其余为 ``None``（票 13 §6）：
    投影时就把不可见的身份抹掉，"身份不泄露"因此是可断言的不变量。

    ``hero`` / ``kingdom``（票 02）是**公开信息**（标准版武将牌明置；`04`（§2「信息可见性」）），
    故照搬、不按观察者过滤。``hero = None`` **专指"未发放武将"**——无武将模式与人为局面的
    默认——与"暗置武将"（国战：发了牌但别人看不见）**不是一回事**：前者是"这张座位上没有
    武将牌"，后者是"有牌但不公开"，将来做国战时两者必须能同时存在。

    两条新字段**不进记录流**（``exclude``）：无武将模式下加字段不改 M2 的记录流字节
    （票 02 §验收 5 的"34 条基准逐行通过"）；带武将的记录形态留给定票按需立项。
    """

    seat: int
    hp: int
    hp_max: int
    hand_count: int
    alive: bool
    equip: tuple[CardRef, ...]
    judge: tuple[CardRef, ...]
    identity: Identity | None = None
    hero: str | None = Field(default=None, exclude=True)
    kingdom: Kingdom | None = Field(default=None, exclude=True)


class PlayerView(_FrozenModel):
    """参与者视角：只含自己的手牌与全部公开信息（票 05 §5）。

    牌堆只给**剩余数量**、不给顺序；``discard`` 按置入序（先入在前）；
    ``processing``（处理区）是公开区，按置入序（票 07 §3）。
    """

    seat: int
    players: tuple[PlayerPublic, ...]
    self_hand: tuple[CardRef, ...]
    deck_count: int
    discard: tuple[CardRef, ...]
    processing: tuple[CardRef, ...]
    turn_seat: int | None
    phase: Phase | None


# ---------------------------------------------------------------------------
# 行动（四个构造子）
# ---------------------------------------------------------------------------


class _ActionBase(_FrozenModel):
    answers_seq: int
    """本行动所应答的询问的 ``seq``；陈旧输入即失配（票 05 §3）。"""


class UseCard(_ActionBase):
    kind: Literal["UseCard"] = "UseCard"
    card: CardRef
    targets: tuple[int, ...] = ()


class Pass(_ActionBase):
    kind: Literal["Pass"] = "Pass"


class EndPhase(_ActionBase):
    kind: Literal["EndPhase"] = "EndPhase"


class DiscardCards(_ActionBase):
    kind: Literal["DiscardCards"] = "DiscardCards"
    cards: tuple[CardRef, ...]


class PlayCard(_ActionBase):
    """打出：**无目标**、走 L6 的 ``PLAY_DECLARE`` / ``PLAY_ON_PLAY``、**不占使用次数**。

    必须是独立构造子（票 19 §2）：用 ``UseCard(targets=())`` 冒充会让"使用"与"打出"
    在记录流里不可分。
    """

    kind: Literal["PlayCard"] = "PlayCard"
    card: CardRef


class SelectCards(_ActionBase):
    """选牌：``picks`` 是**选择面内的位置**（``Slot`` 的下标），不携带牌面身份。"""

    kind: Literal["SelectCards"] = "SelectCards"
    picks: tuple[int, ...]


class ActivateSkill(_ActionBase):
    """发动一个技能（票 19 §2 / 票 30）：载荷（选项 / 面内位置）**烘进候选**。

    ``choices`` 是选项 id（一次性的"二选一"类），``picks`` 是面内位置（成本类载荷，
    如【贯石斧】的两张牌）。两者都在**构造候选时**就付得起才出现，故不存在
    "答了发动才发现付不起"的中间态。

    M2 的唯一"二选一"生产者【雌雄双股剑】的**选择权在目标**，故它走独立的
    ``SELECT_OPTION`` 询问（票 12 §Q6），``choices`` 是留给 M3 "由发动者自己选"的接口位。
    """

    kind: Literal["ActivateSkill"] = "ActivateSkill"
    skill_id: str
    choices: tuple[str, ...] = ()
    picks: tuple[int, ...] = ()


class SelectOption(_ActionBase):
    """在**选项闭集**里挑一个（票 12 §Q6）：【雌雄双股剑】的二选一，选择权在**目标**。"""

    kind: Literal["SelectOption"] = "SelectOption"
    choice: str


class ArrangeCards(_ActionBase):
    """排列（票 10 §②）：``order`` = **全部 X 个面内位置**的一个排列，``split`` = 前几张放牌堆顶。

    ``order`` 的方向读作**自顶向下**：``order[:split]`` 依次成为新的牌堆顶（``order[0]``
    在最上），``order[split:]`` 依次置于牌堆底。答复只带**面内位置**，不带牌面身份——
    位置 → 实体牌的映射留在引擎里（与 ``SelectCards.picks`` 同姿态），故记录流里
    这次排列只以"位置"出现。
    """

    kind: Literal["ArrangeCards"] = "ArrangeCards"
    order: tuple[int, ...]
    split: int


Action = Annotated[
    UseCard
    | Pass
    | EndPhase
    | DiscardCards
    | PlayCard
    | SelectCards
    | ActivateSkill
    | SelectOption
    | ArrangeCards,
    Field(discriminator="kind"),
]

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(raw: object) -> Action:
    """把**外来应答**解析成 :data:`Action`：唯一入口，``extra="forbid"`` 即拒多余字段。

    schema 不认的输入（未知 ``kind`` / 缺字段 / 类型不符 / 根本不是对象）抛 pydantic
    ``ValidationError``——引擎据此记 ``MalformedAction`` 并抛 ``IllegalActionError``
    （票 07 §5 的 ``Malformed`` 一类）。已经是 ``Action`` 实例的输入原样返回，
    故参与者照常可以直接返回协议对象。
    """
    return _ACTION_ADAPTER.validate_python(raw)


# ---------------------------------------------------------------------------
# 询问（四类）
# ---------------------------------------------------------------------------


class _QueryBase(_FrozenModel):
    seq: int
    actor: int
    timing: Timing
    prompt: str
    legal_actions: tuple[Action, ...]
    view: PlayerView
    revealed: tuple[CardRef, ...] = ()
    """**本次询问授予该参与者的牌面**（票 10 §①，默认空 = 不授予）。

    这是"观看牌堆顶"这类效果的**唯一授予通道**，属于**这一次询问**：询问结束即失效，
    既不写状态、不进 :class:`PlayerView`、也不泄露给其它座位。它不是"参与者视角快照"
    的一部分——塞进 ``PlayerView`` 会污染"常驻视角 = 全量快照"的语义，也会让可见性断言
    失去唯一读点（`04`（§2「信息可见性」）的"牌堆顺序仅引擎按效果逐张授予"）。

    **空元组不进序列化**（``_serialize``）：无授予的询问与 M2 逐字节相同，34 条基准
    因此一行不改；有授予时牌面只出现在这**一次**询问记录里，不落进任何 delta。
    """

    transform_preactivations: dict[str, tuple[Step, ...]] = Field(
        default_factory=dict, exclude=True
    )
    """**转化技的预发动清单**（W4-3）：技能 id → 清单。

    与 :attr:`PlayPhaseQuery.preactivations`（**复合动作**清单）分开：转化技的确认仍是
    **一次普通的使用 / 打出**，不产生 ``ActivateSkill`` 复合动作；接入层据清单收集
    来源 / 目标，由**服务端**解析成候选池里的那条动作（W4-fix-2）。

    清单由 :func:`sgs.skills.conversion_preactivations` 从**这次询问的内部候选**
    （:attr:`transform_actions`）派生（来源组 = 候选的 ``virtual_sources``），四类语境
    都要下发：出牌阶段（主动使用）、``DODGE`` / ``RESPOND``（响应）、``PEACH``（求桃）。
    **不进记录流**（``exclude``），由 :func:`sgs_web.projection.pending_payload` 显式下发。
    """

    transform_actions: tuple[Action, ...] = Field(default=(), exclude=True)
    """被**收回**的内部转化候选（W4-fix-2，用户裁定 A）：``virtual_sources`` 非空的使用 / 打出。

    这些动作**不在** :attr:`legal_actions` 里（"红牌直接当【杀】"的可选候选已收口），
    但仍属于本次询问的合法动作池：确认路径与回放磁带的成员判定都读这一份，清单也从它
    派生。**不进记录流**（``exclude``）——它是候选的派生数据，重放时由同一份状态重建。
    """

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if not self.revealed:
            data.pop("revealed", None)
        return data


class PlayPhaseQuery(_QueryBase):
    kind: Literal["PLAY_PHASE"] = "PLAY_PHASE"
    preactivations: dict[str, tuple[Step, ...]] = Field(default_factory=dict, exclude=True)
    """本出牌阶段**可预发动**的技能：技能 id → 清单（W4-1）。

    与 ``legal_actions`` 里的骨架候选**同源**：骨架仍由各技能的候选回调产出，接入层据它
    判断"此刻可用"，再渲染清单。迁移到预发动的技能会从 ``legal_actions`` 里删掉骨架，
    可用性就只剩这一份（AI 与前端同读）。

    **不进记录流**（``exclude``）：清单可由状态 + 技能声明重新渲染，落盘只是冗余；既有
    Golden Replay 因此逐字节不变。接入层在 :func:`sgs_web.projection.pending_payload`
    里显式下发它。
    """


class DodgeQuery(_QueryBase):
    kind: Literal["DODGE"] = "DODGE"


class PeachQuery(_QueryBase):
    kind: Literal["PEACH"] = "PEACH"
    dying: int


class DiscardQuery(_QueryBase):
    kind: Literal["DISCARD"] = "DISCARD"
    count: int
    """本阶段须弃置的张数（= 手牌数 − 当前体力值）。"""


class RespondQuery(_QueryBase):
    """响应窗口（票 19 §3）：从候选里挑一张牌行动或放弃。

    ``window_frame`` = 窗口挂在哪张 ``RESOLVE`` 帧上（嵌套无懈的层靠它区分）；
    ``target`` = 该窗口针对的座位（打出窗口为 ``None``、借刀为持刀者）。
    """

    kind: Literal["RESPOND"] = "RESPOND"
    cause: WindowCause
    required: str | None = None
    target: int | None = None
    window_frame: int = 0


class SelectCardsQuery(_QueryBase):
    """选牌（票 19 §3）：选择面随询问**落盘**、不进 ``PlayerView``。

    ``skill_id``（票 05）标识这一步属于哪条技能（发动后流水线的选牌步；别的窗口为
    ``None``）。它让只看得见询问的**本地消费者**（启发式 AI）能分辨"这一步的成本语义"
    ——【制衡】弃一张换一张 vs【仁德】把牌交出去。**不进记录流**（``exclude``，与
    ``PlayerPublic.hero`` 同一姿态）：加字段不改既有 Golden Replay 的字节。
    """

    kind: Literal["SELECT_CARDS"] = "SELECT_CARDS"
    spec: SelectionSpec
    slots: tuple[Slot, ...]
    skill_id: str | None = Field(default=None, exclude=True)
    provide_type: str | None = Field(default=None, exclude=True)
    """**"提供一张 B"** 的 B 牌名 id（票 14）；``None`` = 普通选牌面。**不进记录流**：
    加字段不改既有 Golden Replay 的字节（与 ``skill_id`` 同一姿态）。"""


class SkillActivationQuery(_QueryBase):
    """是否发动（票 19 §5 / 票 30）：载荷（选项 / 成本面）随询问一并给出。"""

    kind: Literal["SKILL_ACTIVATION"] = "SKILL_ACTIVATION"
    skill_id: str
    optional: bool = True
    min_picks: int = 0
    max_picks: int = 0
    slots: tuple[Slot, ...] = ()
    preactivation: tuple[Step, ...] = Field(default=(), exclude=True)
    """被动入口的**预发动清单**（W4-1）：空元组 = 这条技能此刻没有可下发的清单。

    与 :attr:`PlayPhaseQuery.preactivations` 同姿态：**不进记录流**，由接入层显式下发。
    """


class SelectOptionQuery(_QueryBase):
    """选项二选一（票 12 §Q6 / 票 30）：生产者 = 【雌雄双股剑】，选择权在**目标**。"""

    kind: Literal["SELECT_OPTION"] = "SELECT_OPTION"
    skill_id: str
    options: tuple[str, ...]


class ArrangeQuery(_QueryBase):
    """排列（票 10 §②）：被授予的牌面随询问给出，答复 = 一个排列 + 一个切分点。

    "观看牌堆顶 X 张并按任意顺序放回顶上 / 底下"是**一份决策**：拆成多次
    ``SELECT_CARDS`` 会让一次观星变成十几次询问、且顺序语义靠询问序号隐式表达
    （``SelectCards.picks`` 是无序集合，表达不出"任意顺序"）。故新增这一类询问。
    """

    kind: Literal["ARRANGE"] = "ARRANGE"


Query = Annotated[
    PlayPhaseQuery
    | DodgeQuery
    | PeachQuery
    | DiscardQuery
    | RespondQuery
    | SelectCardsQuery
    | SkillActivationQuery
    | SelectOptionQuery
    | ArrangeQuery,
    Field(discriminator="kind"),
]
