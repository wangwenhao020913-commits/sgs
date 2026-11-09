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

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_serializer

from sgs.data import Color, Identity, Kingdom
from sgs.timing import Timing

from .models import CardRef, Marker, Phase, Zone, ZoneRefField, _FrozenModel

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
    "HeroCardRef",
    "Pass",
    "PeachContext",
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
    "RejudgeOffer",
    "SkillActivationContext",
    "SkillActivationQuery",
    "Slot",
    "Step",
    "StepPool",
    "SubstituteJudgeCard",
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
    SELECT_OPTIONS = "SELECT_OPTIONS"
    ARRANGE = "ARRANGE"
    DISTRIBUTE = "DISTRIBUTE"


class WindowCause(str, Enum):
    """响应窗口的**成因**（票 19 §3；M2 闭集，M3 只许增）。

    票 27 落四个效果内窗口；``NULLIFY`` 由票 28 的无懈窗口使用，此处先把闭集建全。
    票 09（M3）加 ``LORD_REQUEST``：**势力求助窗口**的成因——主公技令其他同势力角色
    打出一张牌（【护驾】/【激将】），与"这张牌自己的响应窗口"（``DODGE`` / ``DUEL`` 等）
    分列，使记录流里读得出"这次询问来自求助"。
    票 11（M3）加 ``REJUDGE``：**改判窗口**的成因——判定牌生效前，有改判能力的角色
    **用一张牌代替 / 替换**之（`11.3`；feng-zhangjiao 票 02 起候选是
    :class:`SubstituteJudgeCard`，不是打出）；它挂在判定帧上、可循环重开，与效果自己的
    响应窗口分列。
    ``LUANWU``（lin-batch2 票 03，E2 已批 / 用户 2026-11-08 签 Q2 = A）：贾诩移动版
    【乱武】逐座位的**二选一**窗口——「对距离最近的另一名角色使用一张【杀】，或失去
    1 点体力」。它复用既有的 ``RESPOND`` + ``RespondMode.USE`` 面（与借刀同一扇窗），
    只多一个成因值，使记录流与界面文案都读得出"这次询问来自乱武"（借用
    ``BORROWED_SWORD`` 会让提示条与 ``Pass`` 按钮说谎成"交出武器"）。

    **本枚举是"要牌窗口"的成因闭集**（yijiang-2014-round2 票 16）：每个成员都必须被
    :data:`sgs.skills.catalog.CARD_REQUEST_WINDOWS` 的某一行认领（写清那扇窗口的 scope /
    mode / 要的牌 / 需要还是可以 / 谁可以参与）。新加一个成员 = 新加一扇窗口，
    ``tests/test_card_request_windows.py`` 与 ``tests/test_ai_adaptation.py`` 会同时逼出
    那一次裁定；``REJUDGE`` 是其中"登记但排除"的一行（改判不是使用 / 打出，提供者与
    转化都不参与）。
    """

    NULLIFY = "NULLIFY"
    BARBARIAN_INVASION = "BARBARIAN_INVASION"
    ARROW_BARRAGE = "ARROW_BARRAGE"
    DUEL = "DUEL"
    BORROWED_SWORD = "BORROWED_SWORD"
    LORD_REQUEST = "LORD_REQUEST"
    REJUDGE = "REJUDGE"
    LUANWU = "LUANWU"


class RespondMode(str, Enum):
    """响应窗口要求的是**打出**、**使用**，还是**改判**（票 19 §1；feng-zhangjiao 票 02 加第三档）。

    打出（南蛮 / 万箭 / 决斗）⇒ 候选 ``PlayCard``；借刀杀人的"使用【杀】"⇒ 候选 ``UseCard``
    （目标由 ``use_targets`` 给出）；**改判**（【鬼才】标 / 界、【鬼道】）⇒ 候选
    :class:`SubstituteJudgeCard`——它不是打出：不压 PLAY 帧、不触发"打出时 / 使用后"钩子，
    引擎压一个 ``FrameKind.REJUDGE`` 改判事件帧。``mode`` 只用于**生成候选**，
    不进记录——记录里读得出候选是哪种构造子。
    """

    PLAY = "PLAY"
    USE = "USE"
    REJUDGE = "REJUDGE"


@dataclass(frozen=True, slots=True)
class RejudgeOffer:
    """改判窗口里**一条技能**的候选声明（feng-zhangjiao 票 02；不进记录流）。

    一个座位可以有多条改判技能（当前最多一条）：每条各自回答三件事——候选取自哪几个区、
    要不要按颜色筛选；原判定牌的去向由 :class:`~sgs.skills.catalog.SkillDef` 的
    ``rejudge_original_to`` 回答（窗口按 ``skill_id`` 现查）。``color`` 为 ``None`` = 不限颜色。
    """

    skill_id: str
    zones: tuple[Zone, ...] = (Zone.HAND,)
    color: Color | None = None


class Slot(_FrozenModel):
    """选择面上的一个可点位置（票 19 §3）。

    ``ref`` 是区域坐标；``card = None`` 表示**隐藏槽**（只有位置、没有牌面身份，
    如他人手牌）。Action 用**面内位置**引用槽位，故不泄露隐藏牌面。
    """

    ref: ZoneRefField
    card: CardRef | None = None


class SelectionSpec(_FrozenModel):
    """选择面的**声明式**约束（票 19 §3）：展示哪些区、总共 / 每区各取几张。

    ``optional`` / ``sequential`` 是效果属性，M2 的拆、顺、五谷都取 ``(1, 1)`` 且非依次；
    字段按"M3 只许增"保留在协议里。

    ``max_per_suit``（神吕蒙移动版【涉猎】的加法）：**每种花色至多取几张**（``None`` =
    不约束）。它约束的是**候选生成**（``effects/actions.py:_select_cards`` 只枚举满足它的
    组合），不是界面层的灰显——界面读同一个字段把"同花色的其余牌"置灰。缺省 ``None``
    **不进序列化**（与 ``StepPool.max`` 同姿态）：``SelectionSpec`` 随 ``SelectCardsQuery``
    落记录流，多一个 ``"max_per_suit": null`` 会让所有带选牌询问的金录像字节变化。
    """

    zones: tuple[Zone, ...]
    total: tuple[int, int]
    per_zone: tuple[tuple[Zone, int, int], ...] = ()
    optional: bool = False
    sequential: bool = False
    max_per_suit: int | None = None

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if data.get("max_per_suit") is None:
            data.pop("max_per_suit", None)
        return data


class StepPool(_FrozenModel):
    """预发动清单里**一步的候选池**（W4-1）：``when`` = 前序步骤的答案，``options`` = 此刻的候选。

    ``when`` 是按步骤顺序摊平后的答案文本：选牌步给实体牌 id（十进制文本），选座位步给
    ``seat:N``，选项步给选项文本。空元组 = 这份子池与前序选择无关。

    **逐选项子池一次下发**：池依赖前序步骤的技能（【流离】选不选武器、【离间】先点谁）
    会为每个前序选项各给一行；接入层按玩家当前已选的值找对应行。行数超过
    :data:`~sgs.skills.flow.MANIFEST_POOL_LIMIT`、或前序是**多选**步且池确实随它变时，
    引擎**退化**为该步不下发候选（``Step.pool = ()``），由校验器裁决——退化只削弱界面
    提示，不改变合法性。

    ``max``（W7-03）是**这一行**最多可选几个：``None`` = 与步骤的 ``Step.max`` 相同。
    多选座位步的 ``Step.max`` 是**全局**最大值，而同一个技能的不同来源组可以有不同上限
    （【方天画戟】只放宽"最后一张手牌"的转化源）。``options`` 只回答"哪些目标能点"，
    不回答"最多点几个"，故逐来源上限必须由行自己带出——界面据此把可选数收在该来源的
    合法数目内。值不进记录流（清单整体 ``exclude``），且缺省 ``None`` 时不序列化。

    ``disabled``（票 04 §3）：``options`` 里**显示但不可选**的那些。选项原本只有"在不在
    候选里"两态，而【蛊惑】的牌名弹窗要"列全牌堆名、把这个名字此刻用不了的那几项置灰"
    ——"列出来"是提示（"此刻没有合法目标"），"选不了"是合法性，两件事各由一格回答。
    **校验器与它同源**（:func:`sgs.skills.flow._assign_activation` 拒绝 disabled 里的项），
    故置灰不是靠界面挡的。缺省空元组**不进序列化**（与 ``max`` 同姿态）。
    """

    when: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    max: int | None = None
    disabled: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if data.get("max") is None:
            data.pop("max", None)
        if not data.get("disabled"):
            data.pop("disabled", None)
        return data


class Step(_FrozenModel):
    """预发动清单的一步（W4-1）：``{name, zones, min, max, pool, order}``。

    ``name`` = 步骤键（效果按它从答案里取）；``zones`` = 选牌步铺哪些区（座位步 / 选项步
    为空）；``min`` / ``max`` = 张数区间。``max = None`` 表示"全部可选的牌"（声明面）；
    渲染器会把它解析成**当前可选牌数**（与 ``SelectionSpec.total`` 同一条解析），故下发的
    清单里通常是整数。``pool`` = 候选池（见 :class:`StepPool`）；
    ``order`` = 步骤序号（0 起）。

    清单由 ``SkillFlow.steps`` **渲染**而来（:func:`sgs.skills.flow.manifest`），校验器读
    同一份声明——两者不是两套规则。``pool`` 为空元组 = 本步没有（或退化掉了）候选索引，
    接入层可退化为"全部都点得动、提交时由校验器裁决"。``wording`` 是本步的人话文案
    （W4-fix-3，见字段注释）。
    """

    name: str
    zones: tuple[Zone, ...] = ()
    min: int = 0
    max: int | None = None
    pool: tuple[StepPool, ...] = ()
    order: int = 0
    wording: str = ""
    """这一步**要玩家做什么**的人话（W4-fix-3）；空串 = 没有专门文案，读取方退回通用文案。

    取自技能声明上的 :attr:`~sgs.skills.catalog.SkillDef.step_requirements`（键 = 步骤名），
    由 :func:`sgs.skills.flow.manifest` 渲染时填入（缺失 ⇒ 空串）。它让清单界面画得出
    "选择决斗使用者 / 决斗目标"这类**分步文案**——旧逐步流程里这句话只住在
    ``step_requirements``，而清单的 ``Step`` 没有承载它的字段，界面只能画通用的"选目标"。
    与 ``pool`` 同姿态：清单整体在询问字段里 ``exclude``，故**不进记录流**。
    """

    groups: tuple[tuple[str, ...], ...] = ()
    """多来源步的**合法来源组合**（huo-mobile-batch 票 10）：每个元素是一组能**一起提交**的
    来源牌 id（十进制文本，与 ``StepPool.options`` 同一口径），由服务端从候选池派生
    （:func:`sgs.skills.heroes._manifest_steps`：``per_source`` 的键就是合法组），故界面不必
    自己判花色 / 牌名——它只回答"哪几张能配成一组"。

    只给 ``min == max > 1`` 的来源步，且**只有存在非法组合时**才下发：任意 ``count`` 张都能
    配成一组（【丈八蛇矛】的"任意两张"）时省略——缺省 / 空 = "每个组合都合法"，旧载荷因此
    逐字段不变。**不进记录流**（清单整体 ``exclude``）；查看者不是询问者时由
    :func:`sgs_web.projection._mask_foreign_preactivations` 与 ``pool[*].options`` 一起抹掉
    （来源牌 id 是询问者的手牌）。
    """

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if not data.get("groups"):
            data.pop("groups", None)
        return data


@dataclass(frozen=True, slots=True)
class RespondContext:
    """``RESPOND`` 候选生成的类型化载荷（票 19 §4 的 ``payload``）。

    ``providers_used`` 只在"需要【闪】"的响应窗口里用（票 10 §5.5 / 票 30）：把它记的
    技能 id 从候选里去掉，实现"每提供者只问一次"。

    ``zones`` = 候选牌取自**应答者的哪几个区**（缺省只有手牌）。改判窗口是唯一会放宽的
    消费者：标准【鬼才】"用一张**手牌**"、界【鬼才】"用**一张牌**"、张角【鬼道】"一张**黑色**
    牌"⇒ 界版 / 鬼道带装备区，由 :func:`sgs.skills.rejudge_offers` 从技能定义派生。
    **不进记录流**（与 ``cause`` / ``required`` 同为候选生成载荷）。

    ``rejudge_offers`` = **改判窗口**每条技能各自的一条声明（feng-zhangjiao 票 02）：
    候选按它逐条枚举（区 + 颜色），产出的构造子是 :class:`SubstituteJudgeCard`。
    只服务 ``mode = REJUDGE``；其余窗口恒空。**不进记录流**（同上）。

    ``declared`` = 这扇**收集式**窗口已经收到过一条声明（票 03）：赢家已定（首个声明者），
    后面再声明的牌**不会进结算**。提供者（【蛊惑】）据此收口——它的产物是扣置一张手牌换来的，
    让它在赢家已定之后还发候选，玩家点下去只会白扣一张牌。**不进记录流**（同上）。

    ``floor`` = 本次需要牌**事件**里已经定下的优先级档（票 03 §4-2；档越小越优先，
    ``HERO=0`` / ``EQUIPMENT=1`` / ``CARD_USE=2``）。候选生成口据此执行"不升档"：
    档 < ``floor`` 的候选不进面（曹操先发动【八卦阵】⇒ 判定黑 ⇒ 没有〖护驾〗的时机了）。
    窗口在"这一次响应成立"处把它清回 0（``_dodge_window`` 的 ``dodges_done`` 每加一、
    【决斗】每收下一次响应）——floor 的粒度是**一个需要牌事件**，不是整扇窗口（补充一手
    §2.1.2.2(2) 的"每时机一次响应机会"+ §4.2.2 的 ❶❷❸）。**不进记录流**（同上）。
    """

    cause: WindowCause
    required: str | None = None
    target: int | None = None
    window_frame: int = 0
    mode: RespondMode = RespondMode.PLAY
    use_targets: tuple[int, ...] = ()
    providers_used: tuple[str, ...] = ()
    zones: tuple[Zone, ...] = (Zone.HAND,)
    declared: bool = False
    rejudge_offers: tuple[RejudgeOffer, ...] = ()
    floor: int = 0


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

    ``floor`` = 本次**需要【闪】事件**里已经定下的优先级档（票 03 §4-2，语义与
    :attr:`RespondContext.floor` 逐字相同）：档 < ``floor`` 的候选不进面，档值越小越优先。
    ``_dodge_window`` 在 ``dodges_done`` 每加一处把它清回 0——【无双】的两张【闪】是
    **两次**需要牌事件，第二张的 floor 从头开始（补充一手 §2.1.2.2(2)）。**不进记录流**。
    """

    providers_used: tuple[str, ...] = ()
    floor: int = 0


@dataclass(frozen=True, slots=True)
class PeachContext:
    """``PEACH`` 候选生成的类型化载荷（票 03 §4）：本次求桃里已定下的优先级档 ``floor``。

    求桃窗口此前没有载荷（候选只由真牌 / 转化源构成）；票 03 起它也有**提供者**
    （【蛊惑】"扣置一张手牌当【桃】"），故与另外三扇需要牌窗口走**同一道**"不升档"闸门，
    载荷与 :class:`DodgeContext` 同形（档 < ``floor`` 的候选不进面；档越小越优先）。

    求桃的"这一次响应成立"= :func:`sgs.engine.steps._peach_use`（回复结算开始的那一刻）：
    在那之后要么脱离濒死、要么回退开新窗（同一帧内的**新**事件），floor 都在那里清回 0。
    **不进记录流**（:class:`PeachQuery` 的序列化字段一个不加）。
    """

    floor: int = 0


@dataclass(frozen=True, slots=True)
class SkillActivationContext:
    """``SKILL_ACTIVATION`` 候选生成的类型化载荷（票 19 §5 / 票 30；W6-06a 收窄）。

    发动 = **一次询问**：``optional`` 决定候选里有没有 ``Pass``（"可以 / 必须"是效果属性，
    与 ``locked`` 正交）；候选为**裸 ``ActivateSkill``** —— 成本 / 载荷在发动后由
    ``SkillFlow`` 的步骤询问。``min_picks`` / ``max_picks`` / ``slots`` 仍随冻结报文
    ``SkillActivationQuery`` 下传（A-4 协议清理另立票）；当前生产者恒传 ``0 / 0 / ()``，
    保 15 份录像逐字节。``zones`` / ``card_types`` 一类候选烘焙字段已在 W6-06a 删除。

    票 01（同一时机的激活结算）把"一条面"从单技能扩成**本轮的全部候选**：``candidates``
    带档、``floor`` / ``used`` 是闸门的两个输入（判据在
    :func:`~sgs.effects.actions.activation_allowed`，本结构只承载事实）。三者缺省为空 /
    ``0`` / 空 ⇒ 旧构造（单候选 ``skill_id``）的候选逐条不变。
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
    candidates: tuple[tuple[str, int], ...] = ()
    """本轮同一张面上的**全部候选**：``(技能 id, 优先级档)``，按链序（票 01 §4-2）。

    空元组 = 只有一个 ``skill_id``（旧构造 / 单候选面）——候选生成器按单条展开，
    与改造前逐字节相同。档用 ``int(priority_class)``：``HERO=0`` / ``EQUIPMENT=1`` /
    ``CARD_USE=2``（越小越先），闸门据此判"不升档"。
    """
    floor: int = 0
    """**不升档**闸门（票 01 §4-3）：档 < ``floor`` 的候选不进面。

    初值 = 最高档（``HERO`` = 0，故首轮全部档都进面）；每选定一条后 ``floor = 它的档``，
    低档照进、高档不再进（选过装备档之后武将档不再上脸）。本 pass 结束即随帧私有游标清。
    """
    used: tuple[str, ...] = ()
    """本座位本 pass **已经跑过**的技能 id（每技能一次；票 01 §4-6）。

    与 ``floor`` 同属闸门的第二个输入：已跑过的技能不再进面——"每技能一次"由这个显式集合
    兑现，不再靠答案缓存当去重手段。
    """
    preactivations: tuple[tuple[str, tuple[Step, ...]], ...] = ()
    """按候选分发的预发动清单（票 01；票 02 的读取面）：``(技能 id, 清单)`` 有序对。

    与 :attr:`preactivation` 同源（都由 ``SkillFlow.steps`` 渲染），区别只在**粒度**：
    单候选面上两者内容相同，多候选面只有这里能回答"每条候选各自的清单"。接入层
    （:func:`sgs_web.projection.pending_payload`）据此逐候选下发；**不进记录流**。
    """


@dataclass(frozen=True, slots=True)
class SelectOptionContext:
    """``SELECT_OPTION`` 候选生成的类型化载荷（票 12 §Q6 / 票 30）：选项 id 闭集。

    ``optional`` 是**取消能力**的显式开关（票 00 / N4）：缺省 ``False`` ⟹ 强制闭集，
    候选一条不多（雌雄双股剑、遗计"交给谁"的既有口径不变）；置 ``True`` 才多一条
    ``Pass``，供"发动后中途取消、取消不消耗技能使用次数"的技能流水线用。

    **展示面**（票 07；票 02 §4.2 B2）三件都是可选、缺省为"没有"：

    - ``disabled``：**显示但不可选**的选项（如"已拥有的技能"）——它们在
      :attr:`options` 里保留原位，但**不进** ``legal_actions``（与 ``StepPool.disabled``
      同语义，走逐步询问这条面）；
    - ``option_texts``：**引擎给**的选项显示文本（键 = 选项 id）；武将牌上的技能行不给，
      由投影从数据表派生名字与描述（见 :attr:`SelectOptionQuery.option_texts`）；
    - ``hero_card``：本次询问展示的**武将牌**（id + n-of-m）；``hero_id=None`` = 这一次
      没有武将牌（池子抽空，只给技能自己的那一行）。
    """

    skill_id: str
    options: tuple[str, ...]
    optional: bool = False
    disabled: tuple[str, ...] = ()
    option_texts: Mapping[str, str] = field(default_factory=dict)
    hero_card: "HeroCardRef | None" = None


@dataclass(frozen=True, slots=True)
class SelectOptionsContext:
    """``SELECT_OPTIONS`` 候选生成的类型化载荷（jie-feng-batch2 票 01a）：**多选**选项闭集。

    与 :class:`SelectOptionContext` 的差别只有"答案是一组值"：``options`` 仍是全部选项
    （含置灰），可提交的组合由 ``legal_actions`` 表达（每一问全量枚举 [min,max] 内、
    不含置灰项的子集）。``min_selected`` 缺省 0 = 允许一个都不选（空集是候选里的第一条，
    故超时兜底天然是"什么都不做"）；``max_selected=None`` = 只受选项数约束。
    """
    skill_id: str
    options: tuple[str, ...]
    optional: bool = False
    disabled: tuple[str, ...] = ()
    min_selected: int = 0
    max_selected: int | None = None
    option_texts: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DistributeContext:
    """``DISTRIBUTE`` 候选生成的类型化载荷（huo-shenzhouyu-mobile 票 05）。

    这是**计数式分配**这一询问形状（一张弹窗里给若干座位各配一个可累加计数）的
    全部输入：可分配的座位（座位升序）、合计上限、单座上限。候选 = 「每座 0..单座上限、
    合计 ≤ 上限」的**全量枚举**（4 人局 35 条、8 人局 165 条）；枚举出的 ``counts`` 省略
    0 值、按座位升序，故全 0 的那一条的键最小、恒排在 ``legal_actions[0]``——超时兜底
    （:func:`sgs_web.timeout.fallback_action` 的"取第一个候选"）因此天然是"不分配"。
    """

    skill_id: str
    seats: tuple[int, ...]
    max_total: int = 3
    per_seat_max: int = 3


@dataclass(frozen=True, slots=True)
class ArrangeContext:
    """``ARRANGE`` 候选生成的类型化载荷（票 10；W6-02 加 ``split``）。

    候选 = ``count`` 个**面内位置**的全部排列 × 全部切分点，故候选生成只需要张数；
    牌面本身由询问的 :attr:`_QueryBase.revealed` 承载（对参与者可见、对其它座位不可见）。

    ``split``（W6-02）是**只铺哪些切分点**：``None`` = 自由切分（``0..count`` 全部，
    观星与既有行为逐字相同）；整数 = 固定切分，候选只给这一个置顶张数
    （``DeckTopArrange.split`` 的 ``fixed`` 档，spec S3）。它只影响候选生成、**不进询问 /
    记录流**（``_build_query`` 对 ``ARRANGE`` 不序列化本载荷）。
    """

    count: int
    split: int | None = None
    enumerate_candidates: bool = True
    """是否**枚举**全部排列 × 切分点（huo-shenzhugeliang 票 02 的加法）。

    ``True`` = 既有行为（观星 ≤ 5 张、恂恂 4 张定切分，候选 ≤ 720 条）；``False`` = 只给
    一条**恒等兜底**（= 不改变现状），界面自己构造答案、服务端做结构校验——服务的是
    "面内张数太大、全枚举不可接受"的两行交换（七星：星 ≤ 7 + 手牌可到 10+ ⇒ 最坏 19,448
    条组合）。与 ``count`` / ``split`` 一样**不进询问记录**（``_build_query`` 对 ``ARRANGE``
    不序列化本载荷），故既有金录像逐字节不变。
    """


class PlayerPublic(_FrozenModel):
    """公开区里的一个座位：对手只暴露手牌**数**，身份按可见性规则给出。

    ``equip`` / ``judge`` 是**公开信息**且逐座位（票 08 §6）。手牌上限**不在这里**：
    它是**派生量**（M6 票 08 的双轴：``effects.hand_limit`` 在查询期算"体力值 → 替换式 →
    加式"），不新增冗余字段。

    ``hand_count`` 是**公开的"手牌数"**，恒 = ``len(hand)``：牌标签（M6 票 09）**不改变**
    这个数字——"不带排除计数标签的张数"是弃牌阶段的另一个查询（``effects.hand_count``），
    两者互不替换（spec D11），故这里不读标签、也不新增字段。
    ``identity`` 只对**本人 / 主公 / 已死亡者**可见，其余为 ``None``（票 13 §6）：
    投影时就把不可见的身份抹掉，"身份不泄露"因此是可断言的不变量。

    ``hero`` / ``kingdom``（票 02）是**公开信息**（标准版武将牌明置；`04`（§2「信息可见性」）），
    故照搬、不按观察者过滤。``hero = None`` **专指"未发放武将"**——无武将模式与人为局面的
    默认——与"暗置武将"（国战：发了牌但别人看不见）**不是一回事**：前者是"这张座位上没有
    武将牌"，后者是"有牌但不公开"，将来做国战时两者必须能同时存在。

    ``hero`` / ``kingdom`` / ``chained`` / ``wine_active`` **不进记录流**（``exclude``）：
    记录流里的询问视角不带这些字段，故标准 47 条金录像逐字节不变。``hero`` / ``kingdom``
    由 ``sgs_web.display.seat_display`` 翻成 ``hero_name`` / ``kingdom`` / ``kingdom_label``
    补进**网页载荷**（U1-02 的座位卡读这三个键）；``chained`` / ``wine_active`` 同样由
    ``decorate_snapshot`` 补。会话顶层的 ``heroes`` 名单仍是逐座位武将 id 的真源。

    ``hero_zone_count``（票 27）是**武将牌上的牌**的**公开张数**：与 ``hand_count`` /
    牌堆数同级的公开信息，恒 =
    ``sum(len(zone.cards) for zone in player.hero_cards.values())``。一个座位可能同时挂着
    多个放置技能实例的区（【谦逊】/ 将来的【破军】/【七星】），因此是**求和**而不是逐个实例
    各报一份；跨座位的区算在**宿主**（武将牌的主人）这一行，不是放置技能的持有者。牌面
    只有宿主本人拿得到（``PlayerView.hero_zone_cards``），他人只拿张数。
    """

    seat: int
    hp: int
    hp_max: int
    armor: int = Field(default=0, exclude=True)
    """**护甲值**：与体力并列的第二条体力轴（yijiang-2014-round2 票 07 / W7）。

    公开事实（画在座位卡的血条上），但 ``exclude`` 与 ``chained`` / ``wine_active`` /
    ``flipped`` 同一条纪律：**不进记录流**的询问视图，故 47 条金录像逐字节不变；网页载荷
    由 ``tools/sgs_web/display.py:seat_display`` 补下（``visible_snapshot`` 只做投影的
    ``model_dump``，拿不到 ``exclude`` 字段）。变化经 ``armor_change`` delta 下发
    （``hp_change`` 同档：公开事实、原样下发），回放逐条重建。
    """
    hand_count: int
    alive: bool
    equip: tuple[CardRef, ...]
    judge: tuple[CardRef, ...]
    sealed_zones: tuple[str, ...] = Field(default=(), exclude=True)
    """被**废除（封印）**的**槽**（lei 票 05 下发、lukang-mobile 票 03 改成逐槽；取值 =
    ``SEALED_SLOT_IDS`` 的闭集：``judge_1..3`` + 四个装备槽）。

    **公开事实**（牌桌上人人可见谁的哪个区域被废除了）：``judge`` / ``equip``（整个装备区）/
    ``weapon`` / ``armor`` / ``horse_offense`` / ``horse_defense``，与
    :attr:`sgs.state.models.Player.sealed_zones` 同名同值——投影只**照搬**，不另造名单。
    座位卡的判定 / 装备区据此叠**红色禁止角标**（``tools/sgs_web/static/seat-view.js`` 的
    ``.zone-sealed`` / ``.zone-ban``）：``equip`` 封整区，四个槽位 id 封单槽。

    ``exclude`` 与 ``chained`` / ``wine_active`` 同一条纪律：**不进记录流**，标准 47 条金录像
    逐字节不变；网页载荷由 ``tools/sgs_web/projection.py`` 的 ``visible_snapshot`` 补下（与
    ``hero_zones`` / ``hand_limit`` 同姿态）。**不发 delta**：投影每次从状态重算，回放按动作
    重跑即可重建（与 ``Player.sealed_zones`` 是同一条裁定）。
    """
    hero_zone_count: int = Field(default=0, exclude=True)
    """这个座位的**武将牌上有几张牌**（票 27；跨座位的区算宿主）。

    与 ``chained`` / ``hero`` 同一条纪律：``exclude`` **不进记录流**，标准 47 条金录像
    逐字节不变。区的**牌面**只给宿主本人（:attr:`PlayerView.hero_zone_cards`），张数
    则是公开信息（守恒量的一项，AI 记牌读它）。
    """
    chained: bool = Field(default=False, exclude=True)
    """横置状态（U1-04 下发的座位事实；M5-03 起由铁索连环 / 传导重置真实写入）。

    ``exclude`` 与 ``hero`` / ``kingdom`` 同一条纪律：**不进记录流**，标准 47 条金录像
    逐字节不变。它由 ``visible_snapshot`` 经 ``display.decorate_snapshot`` 写进网页载荷。
    """
    flipped: bool = Field(default=False, exclude=True)
    """武将牌是否**背面朝上**（翻面；本票的翻面机制）。

    同 ``chained``：``exclude`` 不进记录流，网页载荷由展示层补下。
    """
    wine_active: bool = Field(default=False, exclude=True)
    """【酒】的下一张【杀】+1 是否生效（U1-04 下发的座位事实；M5-04 起由 ``【酒】`` 写入、
    回合末清空，每次变化落一条 ``wine_change``）。

    同 ``chained``：``exclude`` 不进记录流，网页载荷由展示层补下。
    """
    markers: tuple[Marker, ...] = Field(default=(), exclude=True)
    """座位身上的**标记实例**（票 06；用户 Q6）。

    公开信息（显示在目标武将牌上），但 ``exclude`` **不进记录流**（同 ``chained`` /
    ``hero``）：变化经 ``marker_change`` delta 下发，网页载荷由 ``display.seat_display``
    补成带来源 / 说明的展示项；AI 的读点登记在 ``_AI_READ_POINTS``。
    """
    identity: Identity | None = None
    hero: str | None = Field(default=None, exclude=True)
    kingdom: Kingdom | None = Field(default=None, exclude=True)


class PlayerView(_FrozenModel):
    """参与者视角：只含自己的手牌与全部公开信息（票 05 §5）。

    牌堆只给**剩余数量**、不给顺序；``discard`` 按置入序（先入在前）；
    ``processing``（处理区）是公开区，按置入序（票 07 §3）。

    ``hero_zone_cards``（票 27）是**本人武将牌上的牌面**，与 ``self_hand`` 对称：只有自己
    当宿主的区（``Player.hero_cards``）会进来，他人武将牌上的牌只经
    :attr:`PlayerPublic.hero_zone_count` 给张数。与 M6 的 2026-10 口径一致——牌面只给宿主，
    未登记 / 畸形的区同一默认（不是整块 fail-closed）。``exclude``：不进记录流，金录像不变。
    """

    seat: int
    players: tuple[PlayerPublic, ...]
    self_hand: tuple[CardRef, ...]
    deck_count: int
    discard: tuple[CardRef, ...]
    processing: tuple[CardRef, ...]
    turn_seat: int | None
    phase: Phase | None
    hero_zone_cards: tuple[CardRef, ...] = Field(default=(), exclude=True)
    """**本人**武将牌上的牌面（票 27）。

    与 ``self_hand`` 对称，是本人视角的"本人区里的牌"；他人武将牌上的牌不在任何
    ``PlayerView`` 里（张数走 :attr:`PlayerPublic.hero_zone_count`）。区键 = （座位, 技能
    实例），同一座位可有多个区；跨座位的区只有在查看者是宿主时才进来。``exclude``：不进
    记录流（47 条金录像逐字节不变），但进询问、是 AI 记牌的"已出现的牌"之一。
    """


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
    designations: tuple[int, ...] = ()
    """**附带指定**（W5 借刀杀人）：与 ``targets``（这张牌的目标）分列的座位。

    借刀的官方形状是"你在选择 An 为目标的同时选择 Bn"：``targets=(A,)`` 是持刀者，
    ``designations=(Bn,)`` 是**使用者**一并指定的【杀】目标。空元组 = 没有附带指定
    （除借刀以外的每一条 ``UseCard``），**不落盘**（:meth:`_serialize`）：既有录像
    因此逐字节不变。``extra="forbid"`` 只管多余键，缺省键由字段默认值补齐，
    :func:`parse_action` 照常接受不含它的应答。
    """

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if not self.designations:
            data.pop("designations", None)
        return data


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


class SubstituteJudgeCard(_ActionBase):
    """**改判**：用这张牌代替 / 替换正在生效前的判定牌（feng-zhangjiao 票 02）。

    必须与 ``PlayCard`` 分开（用户 2026-11-02 裁定："代替 / 替换走特殊的改判事件帧，
    不要走打出"）：打出只发生在"确认一张牌 → 处理区 → 弃牌堆"的牌响应里，且只认**手牌**；
    改判可以拿**装备区**的牌，也不触发任何"打出时 / 使用后"钩子。载荷：
    ``skill_id`` = 由哪条技能（【鬼才】标 / 界、【鬼道】）发动——代替 / 替换与颜色筛选都
    由那条技能的声明回答，窗口不另判；``card`` = 这张实体牌。
    """

    kind: Literal["SubstituteJudgeCard"] = "SubstituteJudgeCard"
    skill_id: str
    card: CardRef


class Reforge(_ActionBase):
    """重铸（M5-03 / 决策 D6）：这张牌离开手牌进弃牌堆，摸一张；**不是使用**。

    必须是独立构造子：重铸不算使用锦囊、不进 ``use_count``、不走使用结算栈、不能被无懈
    ——把它做成 ``UseCard`` 的变体会让这四条在记录流与规则层同时失守。载荷沿用其它行动的
    线形：``card``（这张实体牌）+ ``answers_seq``（本行动应答的询问）。
    """

    kind: Literal["Reforge"] = "Reforge"
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


class SelectOptions(_ActionBase):
    """多选选项的答复（jie-feng-batch2 票 01a）：``choices`` 是按 ``options`` 序排好的子集。

    空元组 = 一个都不选（合法，当且仅当这一问允许空集）。去重与排序由引擎侧
    :func:`sgs.effects.actions._select_options` 枚举出的候选承担：客户端必须**原样**
    选中候选里的那一条，不自己拼集合（与 :class:`Distribute` 同姿态）。"""

    kind: Literal["SelectOptions"] = "SelectOptions"
    choices: tuple[str, ...] = ()


class Distribute(_ActionBase):
    """计数式分配（huo-shenzhouyu-mobile 票 05；首个载体 = 神周瑜【业炎】）。

    ``counts`` = ``((座位, 点数), ...)``：**座位升序、省略 0**，空元组 = 一个点都不分配。
    座位是公开事实，故记录流里读得出"你把几点给了谁"；每一问的合法集合由引擎全量枚举
    （成员判定照旧），客户端按本地计数拼出同一条再在候选里查。
    """

    kind: Literal["Distribute"] = "Distribute"
    counts: tuple[tuple[int, int], ...] = ()


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
    | SubstituteJudgeCard
    | Reforge
    | SelectCards
    | ActivateSkill
    | SelectOption
    | SelectOptions
    | ArrangeCards
    | Distribute,
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

    与 :attr:`PlayPhaseQuery.preactivations`（**复合动作**清单）同形，但确认的是转化：
    W4-fix-2 起，按清单提交的是**复合动作** ``ActivateSkill(来源, 目标)``（引擎
    :meth:`~sgs.engine.game.Engine.submit` 的第三条路 + 前端恒发 ``ActivateSkill``），
    由**服务端**按与清单同源的内部候选池
    （:attr:`transform_actions`）解析成那次普通的使用 / 打出并落账——客户端不拼虚拟牌 id。
    接入层据清单收集来源 / 目标；确认动作本身**不要求**在 ``legal_actions`` 里。

    清单由 :func:`sgs.skills.conversion_preactivations` 从**这次询问的内部候选**
    （:attr:`transform_actions`）派生（来源组 = 候选的 ``virtual_sources``），四类语境
    都要下发：出牌阶段（主动使用）、``DODGE`` / ``RESPOND``（响应）、``PEACH``（求桃）。
    响应窗口里只有借刀杀人的"被迫使用"不给入口（W5），其余照旧下发。
    **不进记录流**（``exclude``），由 :func:`sgs_web.projection.pending_payload` 显式下发。
    """

    transform_actions: tuple[Action, ...] = Field(default=(), exclude=True)
    """被**收回**的内部转化候选（W4-fix-2，用户裁定 A）：``virtual_sources`` 非空的使用 / 打出。

    与 :func:`sgs.skills.is_transformed_action` 是**同一个判据**（W4-fix-6 收口）：这里的
    每一项都是转化牌（来源集合非空），纯虚拟牌（【八卦阵】的视为【闪】，没有来源）不在其中。
    这些动作**不在** :attr:`legal_actions` 里（"红牌直接当【杀】"的可选候选已收口），
    但仍属于本次询问的合法动作池：确认路径与回放磁带的成员判定都读这一份，清单也从它
    派生。**不进记录流**（``exclude``）——它是候选的派生数据，重放时由同一份状态重建。
    """

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        data = handler(self)
        if not self.revealed:
            data.pop("revealed", None)
        # 票 07：``SELECT_OPTION`` 的展示载荷缺省**不落盘**——空值不出现在记录流里，
        # 既有询问记录因此逐字节不变（与 ``revealed`` 同一姿态）。键只在这些子类上存在，
        # 对其余询问是 no-op。
        for absent in ("hero_card", "option_texts", "disabled"):
            if absent in data and not data[absent]:
                data.pop(absent)
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

    designation_actions: tuple[UseCard, ...] = Field(default=(), exclude=True)
    """借刀的**附带指定池**（W5-02a）：每张手牌 × 合法持刀者 A × 合法 Bn 的变体。

    这是"使用者一并指定 Bn"的合法候选面：``Engine.submit`` 的第四条路只要求提交的
    ``UseCard.designations`` 非空且**是这一份的成员**。与 :attr:`transform_actions`
    同姿态——**不进记录流**（``exclude``），重放时由同一份状态重建；裸借刀此刻仍在
    ``legal_actions``（暗发布，收回在 W5-02b）。
    """

    card_preactivations: dict[str, tuple[Step, ...]] = Field(
        default_factory=dict, exclude=True
    )
    """**牌名 → 预发动清单**（W5-02a）：借刀的 [持刀者 A, Bn] 两步。

    从 :attr:`designation_actions` 派生（同源，不另写合法性）：键 = 牌名
    （``card.type``；同名两张借刀同形），步骤 = 两个 ``name="seat"`` 的选座位步
    （第二步按 ``when="seat:A"`` 分列 Bn 子池）。**不进记录流**（``exclude``），
    02a 只在 query 侧下发；投影载荷在 02b 接。
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
    """是否发动（票 19 §5 / 票 30）：候选是**本轮的一到多条**裸 ``ActivateSkill``。

    ``min_picks`` / ``max_picks`` / ``slots`` 是 **A-4 待清理的冻结报文**：当前生产者恒传
    ``0 / 0 / ()``（W6-06a 删除了候选烘焙路径），但 15 份录像逐字节带着这三个字段，
    故协议清理另立票。

    **序列化面一个字段都不改**（票 01 的 Q6=B）：同一时机只有一个候选时，本询问的
    ``skill_id`` / ``optional`` / 冻结报文与候选 / 询问时机全都不变 ⇒ 47 条金录像逐字节。
    多候选面上"这一轮有哪些候选"由 ``legal_actions`` 承载（候选集合本来就在记录里），
    另加的两个字段都是 ``exclude=True`` 的**本地面**（票 01 §5）。
    """

    kind: Literal["SKILL_ACTIVATION"] = "SKILL_ACTIVATION"
    skill_id: str
    optional: bool = True
    min_picks: int = 0
    max_picks: int = 0
    slots: tuple[Slot, ...] = ()
    preactivation: tuple[Step, ...] = Field(default=(), exclude=True)
    """被动入口的**预发动清单**（W4-1）：空元组 = 这条技能此刻没有可下发的清单。

    与 :attr:`PlayPhaseQuery.preactivations` 同姿态：**不进记录流**，由接入层显式下发。
    多候选面上它是**代表候选**（``skill_id``）那份；逐候选的清单在
    :attr:`activation_preactivations`。
    """
    skill_ids: tuple[str, ...] = Field(default=(), exclude=True)
    """**本轮候选的技能 id 清单**（票 01 §5；与 ``preactivation`` 同姿态）。

    空元组 = 本询问不是由派发环的 pass 产出的（旧构造）；由 pass 产出时恒非空——单候选面
    = ``(skill_id,)``，多候选面 = 全部候选。**不进记录流**：候选集合已由 ``legal_actions``
    承载，这份清单只给接入层 / 本地消费者读。
    """
    activation_preactivations: dict[str, tuple[Step, ...]] = Field(
        default_factory=dict, exclude=True
    )
    """**按候选分发**的预发动清单（票 01；票 02 的读取面）：技能 id → 清单。

    与 :attr:`preactivation` 同源、只改粒度：单候选面上两者内容相同（旧面一字不变），
    多候选面上前端按选中的候选取自己那一份。**不进记录流**（``exclude``，与
    ``PlayPhaseQuery.transform_preactivations`` 同一姿态），由接入层显式下发。
    """


class HeroCardRef(_FrozenModel):
    """一次询问**展示的武将牌**与它在本次展示里的序号（票 07；票 02 §4.2 B2）。

    只有"是哪张 + 第几张 / 共几张"三个事实——势力 / 名称 / 体力上限**不在这里**：它们是
    数据表的公开事实，由投影按 ``hero_id`` 派生下发（票 02 §4.2 B4）。本结构进询问记录，
    故回放 / 排查看得到"这一问问的是哪张武将牌、第几次展示"。

    ``hero_id=None`` = 这一次**没有武将牌**（可抽真名不足；01 的"展示恒为 3 次"里缺的那
    几次），此时 ``index`` / ``total`` 仍给出 n-of-m（分母恒为 3）。
    """

    hero_id: str | None = None
    index: int = 1
    """本次展示的序号（1 基；"第 k/m 张"的 k）。"""
    total: int = 1
    """本次展示的**分母**（"第 k/m 张"的 m；缺牌的那几次它也不缩水）。"""


class SelectOptionQuery(_QueryBase):
    """选项二选一（票 12 §Q6 / 票 30）：生产者 = 【雌雄双股剑】，选择权在**目标**。

    **展示载荷**（票 07；票 02 §4.2 B2–B3）三个字段都可选，缺省**不序列化**（既有询问记录
    逐字节不变）：

    - ``disabled``：显示但不可选的选项（置灰）——保留在 :attr:`options` 的原位，
      **只**不在 ``legal_actions`` 里；
    - ``option_texts``：引擎给的选项显示文本（键 = 选项 id）。**约定**：有文本的行是
      技能自己的效果选项（选项 2 的"摸 X 张"这类动态文案由引擎算）；**没有**文本的行是
      "武将牌上的一条技能"，名字与描述由投影从数据表 + ``descriptions.json`` 派生；
    - ``hero_card``：本次展示的武将牌（id / 第几张 / 共几张）。

    :attr:`options` 仍是**全部**选项（含置灰），因为它是展示面的真源；可提交性由
    ``legal_actions`` 表达。
    """

    kind: Literal["SELECT_OPTION"] = "SELECT_OPTION"
    skill_id: str
    options: tuple[str, ...]
    disabled: tuple[str, ...] = ()
    option_texts: dict[str, str] = Field(default_factory=dict)
    hero_card: HeroCardRef | None = None


class SelectOptionsQuery(_QueryBase):
    """多选选项（jie-feng-batch2 票 01a；首个载体 = 界夏侯渊【神速】的「至多三项」）。

    载荷与 :class:`SelectOptionQuery` 同姿态：``options`` 是**全部**选项（含置灰），
    ``disabled`` 保留原位、只不在 ``legal_actions`` 里；``min_selected`` / ``max_selected``
    是这次选择的区间，界面据此启用 / 禁用「提交」。答案是一组值
    （:class:`~sgs.state.SelectOptions`），不是单个 ``choice``。
    """

    kind: Literal["SELECT_OPTIONS"] = "SELECT_OPTIONS"
    skill_id: str
    options: tuple[str, ...]
    disabled: tuple[str, ...] = ()
    min_selected: int = 0
    max_selected: int | None = None
    option_texts: dict[str, str] = Field(default_factory=dict)


class DistributeQuery(_QueryBase):
    """计数式分配（huo-shenzhouyu-mobile 票 05）：一张弹窗里给若干座位各配一个计数。

    ``seats`` / ``max_total`` / ``per_seat_max`` 都是**公开事实**（谁在座上、能分几个），
    故随询问下发；界面据它画计数行，服务端据同一份枚举候选。
    """

    kind: Literal["DISTRIBUTE"] = "DISTRIBUTE"
    skill_id: str
    seats: tuple[int, ...]
    max_total: int = 3
    per_seat_max: int = 3


class ArrangeQuery(_QueryBase):
    """排列（票 10 §②）：被授予的牌面随询问给出，答复 = 一个排列 + 一个切分点。

    "观看牌堆顶 X 张并按任意顺序放回顶上 / 底下"是**一份决策**：拆成多次
    ``SELECT_CARDS`` 会让一次观星变成十几次询问、且顺序语义靠询问序号隐式表达
    （``SelectCards.picks`` 是无序集合，表达不出"任意顺序"）。故新增这一类询问。
    """

    kind: Literal["ARRANGE"] = "ARRANGE"
    structural: bool = Field(default=False, exclude=True)
    """不枚举档：答案**由界面构造**、只做结构校验（huo-shenzhugeliang 票 02）。

    ``True`` 时本询问的 ``legal_actions`` 只有一条恒等兜底（= 什么都不换），界面可以提交
    **任何**形状合法的 ``ArrangeCards``（引擎在 :meth:`Engine.ask_arrange` 校验 order 是
    排列、split 与声明一致）。``exclude=True`` ⇒ 不进记录流，既有 47 条金录像逐字节不变。
    """


Query = Annotated[
    PlayPhaseQuery
    | DodgeQuery
    | PeachQuery
    | DiscardQuery
    | RespondQuery
    | SelectCardsQuery
    | SkillActivationQuery
    | SelectOptionQuery
    | SelectOptionsQuery
    | ArrangeQuery
    | DistributeQuery,
    Field(discriminator="kind"),
]
