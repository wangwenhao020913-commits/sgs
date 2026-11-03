"""引擎门面：一局的回合环、询问交付与终局。

**低层入口**接受人为指定的局面（:meth:`Engine.from_state`）；:func:`new_game` 是它的
便利包装——实例化牌堆（``card_id = 0..n-1`` 按数据表顺序）→ 洗牌 → 按座序发牌。

引擎只在**询问**处让出控制权（票 04 §3）：``run`` 循环要么推进结算栈 / 回合环一步，
要么把当前询问交给该座位的参与者。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from sgs.agents import Participant
from sgs.data import (
    CardTagClear,
    Color,
    ContentPack,
    Hero,
    HeroDealConfig,
    Identity,
    Mode,
    PlacementReturn,
    RuleConfig,
    card_tag_spec,
    color_of_suit,
    deal_heroes,
    hero_by_id,
    hero_placement_decl,
    roster_for,
)
from sgs.effects import (
    OPS,
    CallOp,
    DamageNature,
    EffectContext,
    EffectStep,
    PushFrame,
    Randomness,
    WINE_COUNT_KEY,
    WINE_DAMAGE_BONUS,
    apply_hp,
    behavior_for,
    delayed_settle_target,
    designation_actions_for,
    discard_count,
    draw_from_deck,
    is_own_play_phase,
    is_slash,
    legal_actions_for,
    locate,
    move_card,
    targets_within_attack_range,
)
from sgs.resolution import (
    Control,
    Frame,
    ResolutionStack,
    StepTable,
    Tag,
    has_tag,
    take_event_frame_invalid,
)
from sgs.skills import (
    CATALOG,
    MATCH_ACTOR_KEY,
    MOVE_EVENT_KEY,
    RUNNING_SKILL_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    ActivationPlan,
    MoveEvent,
    SeamKind,
    SkillInstance,
    advance_provider,
    card_preactivations,
    dispatch_move_after,
    dispatch_seam,
    dispatch_timing,
    conversion_preactivations,
    flow_for,
    hero_card_zone_key,
    is_transformed_action,
    move_after_providers,
    owned_conversion_skill_ids,
    preactivation_for,
    preactivated_skill_ids,
    preactivations_for,
    provider_registered,
    prune_card_tags,
    prune_skill_state,
    rejudge_original_to,
    reset_phase_skill_state,
    reset_round_skill_state,
    reset_turn_skill_state,
    round_limit_present,
    resolve_conversion_confirmation,
    start_provider,
    timing_hooks_present,
    transform_candidates,
    validate_activation,
)
from sgs.state import (
    Action,
    ActivateSkill,
    ArrangeContext,
    ArrangeQuery,
    CardRef,
    Distribute,
    DistributeContext,
    DistributeQuery,
    CardRevealDelta,
    CardTag,
    ChainedChangeDelta,
    FlippedChangeDelta,
    DiscardQuery,
    DodgeContext,
    DodgeQuery,
    FrameKind,
    GameState,
    HeroCardZone,
    HpChangeDelta,
    HpMaxChangeDelta,
    IdentityRevealDelta,
    Marker,
    MarkerChangeDelta,
    MoveReason,
    Pass,
    PeachQuery,
    Phase,
    PlayPhaseQuery,
    Player,
    Query,
    QueryKind,
    Reforge,
    RejudgeOffer,
    RespondContext,
    RespondMode,
    RespondQuery,
    SelectCards,
    SelectCardsQuery,
    SelectContext,
    SelectionSpec,
    SelectOption,
    SelectOptionContext,
    SelectOptionQuery,
    SkillActivationContext,
    SkillActivationQuery,
    SkillGainDelta,
    SkillLoseDelta,
    SkillSuppressDelta,
    SkillSuppression,
    SkillSuppressionScope,
    Slot,
    SlotKind,
    Step,
    TagAddDelta,
    TagRemoveDelta,
    TurnChangeDelta,
    UseCard,
    WindowCause,
    WineChangeDelta,
    Zone,
    ZoneRef,
    next_alive_seat,
    order_targets,
    parse_action,
    project,
)
from sgs.timing import Timing

from .errors import IllegalActionError
from .journal import (
    ActionRecord,
    DeckShuffleRecord,
    FrameRecord,
    GameEndRecord,
    Header,
    IdentityDealRecord,
    JournalRecord,
    MalformedAction,
    QueryRecord,
    TimingRecord,
    dump_journal,
    wrap_delta,
)
from .rng import RandomSource
from .steps import CHAIN_TRIGGER_KEY, build_steps
from .victory import DeathFact, Side, solve_identity_outcome

__all__ = ["DRAW_COUNT", "INITIAL_HP", "Engine", "Outcome", "instantiate_cards", "new_game"]

INITIAL_HP = 4
"""**无武将模式**的体力上限回落值（M1/M2 口径：无武将、无体力上限变化）。

有武将时体力上限读武将数据（票 02）；本常数只作"没有武将牌可读"时的兜底，
故 M2 的 34 条基准一行不改。"""

DRAW_COUNT = 2
"""摸牌阶段的额定摸牌数；M1 无修正摸牌数的效果。"""

REBEL_REWARD_DRAW = 3
"""杀死反贼的奖惩摸牌数（规则 4.8；票 14 §4.2）。"""

_RESPONSE_CANCELED = "response_canceled"
"""一次响应型使用**自身**是否被抵消的帧私有标记（票 11 §2）。

与逐目标分槽的 ``canceled`` 分列：后者会被游标推进清掉，而传播发生在帧**出栈之后**。
在 :meth:`Engine._propagate_cancel` 标记父帧时一并写入，子帧出栈时据此决定是否传播。"""

_MOVE_DONE = "move_done"
"""帧私有槽名：本帧**已经搬完的移动**的身份（code-review 复核的重入判据）。

身份 = ``(正在执行的技能 id, 这批牌, 目标区域)``。判据 = "身份相同 **且** 这批牌此刻真的
在目标区域"：重入（``MOVE_AFTER`` 钩子提问把自己挂起、应答消费后承载帧原地重跑同一个
op）两条都成立——那条 ``card_move`` 已记过、牌已在目标区；而同一帧里真正的新移动要么
换了技能身份，要么牌还在来源区，故不会被误吞。
"""

_EQUIP_SLOT_SCANNED = "equip_slot_scanned"
"""帧私有槽名：本帧入槽时的**同槽扫描**已经做过（票 06；复审改名，见下）。

「使用装备牌」的顶替路径先搬走同槽旧牌、再 ``PUT`` 新牌。旧牌那次移动会内联派发
``MOVE_AFTER``（白银狮子压 ``RECOVER`` 帧）并让出，续跑时**重入同一个 op**——那时槽位里
可能已经站着新牌（旧牌搬完后若新牌的 ``PUT`` 先跑完），再扫一遍"同槽已有牌"就会把玩家
刚装上的那张当旧装备搬走。故"这次同槽扫描已经做过"必须落**帧私有**（§3 的帧私有键纪律），
且写在搬牌**之前**（§3.1）：值 = 这次要入槽的那张新牌的 ``card_id``，使同一帧里真出现
第二次顶替时仍是新实例。

名字**只说做过的事**：每次使用装备牌都扫这一遍（没有牌被顶替时也扫），故不叫"顶替已决定"。
"""

_MOVE_EVENTS = "move_events"
"""帧私有槽名：本帧已经派发过的 ``MOVE_AFTER`` 次数（票 07 的派发幂等时钟）。

同一帧里可以有**多次移动**（【方天画戟】的额外目标 / 顶替的先旧后新 / 拆顺的两次搬牌），
而带提问的钩子会让 ``move_in_frame`` 从**同一个步骤**重入（帧内游标不前进）。这个计数
让"第 n 次移动"与"第 n 次重入"区分开：这次移动的幂等键带上它的序号
（见 :meth:`Engine._move_key`），故"同一次移动"只落一条记录、只派发一遍。
帧外移动没有帧可挂标记，那份幂等键住 :attr:`Engine._move_recorded`。
"""

SKIPPED_TURN_KEY = "skipped_turn"
"""``_turn_data`` 私有键：这个回合被**翻面跳过**了（规则集 13.2）。

只影响一件事：收尾步不发 ``TURN_AFTER_END``（该角色没有进入过这个回合）。它随
``_turn_data`` 在回合环回卷时清掉。
"""

_DISPATCH_SCOPE = "dispatch_scope"
"""帧私有槽名：**当前这一次派发的范围键**（M6 票 20 的询问缓存修正）。

钩子的"是否发动 / 选哪个"询问按帧内缓存（挂起重入时不重发），旧键只有
``(skill, actor)``：同一帧里同一技能的**第二次触发**会复用上一次的答案——人类 / AI
根本没被问过。本槽在 :meth:`Engine._dispatch_move_after` 里写成这次移动的幂等键
（``(时机, 牌, 本帧第几次移动)``），续跑帧从派发帧继承，故"同一次触发"的重入仍命中
同一槽，而"同帧的下一次触发"是另一个槽。
"""


def _selection_answer_key(owner: Any, key: str) -> tuple[Any, ...]:
    """``ask_selection`` 的帧内答案键：``("select", 询问位置, 询问名)``（唯一真源）。

    读（:meth:`Engine.accepted_selection`）与写（:meth:`Engine.ask_selection`）共用，
    故"重入时先认已接受的答案"与"发问时缓存"认的是同一个槽。两处各拼一份键，正是
    "答案与面不同源"那一类 bug 的土壤（票 21 / 票 22 的判决）。
    """
    return ("select", owner, key)


def _option_answer_key(frame: Frame, skill_id: str, actor: int) -> tuple[Any, ...]:
    """``ask_option`` 的帧内答案键（唯一真源；M6 票 20 起带上**这次派发的范围**）。

    同 :func:`_selection_answer_key`：读（:meth:`Engine.accepted_option`）与写
    （:meth:`Engine.ask_option`）共用同一份槽名，不容许第二处拼键。
    """
    return ("option", skill_id, actor, frame.data.get(_DISPATCH_SCOPE))


_ANSWER_BACKLOG_LIMIT = 32
"""孤立的**被接受应答**最多留这么多条（票 02 的应答归属）：超过即淘汰最旧的一条。

正常流程里每条应答都由它自己那一问立刻取走（:attr:`Engine._answers` 恒空一瞬）；只有
"问了、答了，但问的那一帧还没重入"才会留一条。32 条同时悬空只可能是病态局面，那时淘汰
最旧的一条：它那一问会重问一遍（规则仍正确），但内存不随对局长度无界增长。

**这是登记过的程序化约定**（本项目的偏离一手账本，`CODING_STANDARDS.md` §1）：官方无
条文，上限与"淘汰即重问"的张力登记在 `开发文档/06-里程碑与风险.md` §5「程序化约定与
偏离一手」的应答归属行；要改这个数或改成"永不淘汰"是一次**重新裁定**，先改那一行。
"""

_ANSWER_ORIGIN_KEY = "answer_origin"
"""帧私有槽名：这一族帧的**发问身份**（票 02 的应答归属键第三位）。

真实帧直接用 :attr:`Frame.frame_id`；只有 ``MOVE_AFTER`` 的**续跑帧**需要它
（``frame_id = 0``、由 :meth:`Engine._resume_frame` 建）：续跑帧从承载帧继承帧私有数据，
故**建帧时**把来源帧的发问身份记进来（见 :func:`_ask_origin`）。不把续跑帧的 ``frame_id``
改成来源帧号——那份坐标是 38 / 39 号冻结基准已经录下的，不能动。
"""


def _ask_origin(frame: Frame) -> int | None:
    """一次询问的**发问帧身份**（票 02 的应答归属键第三位）。

    真实帧 = 帧号；``MOVE_AFTER`` 的续跑帧 = 建帧时记下的来源帧身份。``None`` 只剩两种：
    真的没有承载帧的回合环发问（``PLAY_PHASE`` / ``DISCARD``，栈空时才问），以及手工构造
    的孤立临时帧（没有来源帧可继承）——它们没有可区分的身份：宁可把"同一问"认宽一点，
    也不把真实帧的应答错配掉。
    """
    if frame.frame_id:
        return frame.frame_id
    return frame.data.get(_ANSWER_ORIGIN_KEY)


_DEAD_CARDS_CLEARED = "dead_cards_cleared"
"""死亡帧私有槽名：Ⅰ.a 的"死者区域已清空"位（票 33 的重入幂等）。

清区自身不挂起，但它与"可能挂起"的死亡奖惩同一步（``death_settle_cards``），续跑会再进
一次。位落在帧私有（``CODING_STANDARDS.md`` §3）而不是靠"死者的区恰好是空的"：后者是
**状态巧合**，钩子若在结算中途往死者的区里放了牌，第二次进来会把它当成 Ⅰ.a 的遗漏。
"""

_LORD_PENALTY_GROUPS = "lord_penalty_groups"
"""死亡帧私有槽名：主公杀忠臣那一次弃置要搬的**来源区分组**（票 33）。

形如 ``((ZoneRef, (card_id, ...)), ...)``，在**首次进入**时定格。定格的必要性与
``sgs.skills.move_after._QINGJIAN_GROUPS_KEY`` 同源：【连营】一类的"失去牌后"钩子会在
弃置的**结算中途**把牌摸进主公的手牌——而弃置的"所有牌"指的是**触发那一刻**的牌，不是
重入时看到的牌。不定格就会在重入时把刚摸到的牌当成下一次弃置，形成"弃一张、摸一张、
再弃一张"的死循环。
"""

_LORD_PENALTY_MOVED = "lord_penalty_moved"
"""死亡帧私有槽名：上面那组分群里**已经搬完**的组数（票 33 的挂起续跑游标）。

钩子提问时本步返回 ``"stay"``，应答到达后整步重入。两份帧私有各管一件事：

- **游标**管"不要重进**已经搬完**的组"——挂起可能发生在后面的组，重入若从头再来，
  前面的组会再搬一遍（:data:`_MOVE_DONE` 只有一个槽、记的是**最近一次**移动，兜不住
  更早那几组）；
- **分组**管"哪些牌算这一次弃置"——钩子在结算中途摸进来的新牌不在定格名单里。

重入**尚未前进的那一组**时，搬牌由 :data:`_MOVE_DONE` 的"这次移动已经搬完"判据挡掉
（卡组不变、牌已在弃牌堆），故那一组只需把游标推进一格。
"""


class _PendingMove(NamedTuple):
    """挂起栈的一项（票 25）：续跑一次 ``MOVE_AFTER`` 需要的四件事。

    ``event`` = 这次移动的载荷；``key`` = 它的幂等键；``resume_frame`` = 带钩子询问缓存 /
    效果游标的临时帧；``carrying`` = 原始承载帧（只用于"时机记录去重"的旧语义；帧外移动
    为 ``None``）。字段用具名元组而不是位置下标，是因为栈里现在可能同时有多层，读错一项
    就会续跑错层。
    """

    event: MoveEvent
    key: tuple
    resume_frame: Frame
    carrying: Frame | None


class _Suspend:
    """``ask`` 的哨兵：询问已发出，等参与者应答。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试可读性
        return "SUSPEND"


SUSPEND = _Suspend()

_NOT_STRUCTURED: object = object()
"""``submit`` 的三态哨兵：这个 ``ActivateSkill`` **不是**结构化入口的候选。

``None`` = 结构化校验通过；字符串 = 首个失败原因；本哨兵 = 走原来的成员判定路径
（也含所有非 ``ActivateSkill`` 的行动）。三态必须分开，否则"不合格但也不是结构化入口"
会被误当成"校验通过"。
"""

_NOT_CONVERSION: object = object()
"""``submit`` 的转化确认哨兵（W4-fix-2）：这次提交**不是**转化技清单确认。

``None`` = 清单解析通过（答案在 :attr:`Engine._conversion_answer`）；字符串 = 首个失败
原因；本哨兵 = 不适用，继续走普通复合动作 / 成员判定。
"""

_NOT_DESIGNATION: object = object()
"""``submit`` 的附带指定哨兵（W5-02a）：这条 ``UseCard`` **不带** ``designations``。

``None`` = 它是本次 ``PlayPhaseQuery.designation_actions`` 的成员；字符串 = 首个失败
原因（要写进拒绝记录）；本哨兵 = 不是附带指定动作，交给既有路径。
"""

_BooleanPlayerFlag = Literal[
    "luoyi_active", "luoyi_until_next_turn", "paoxiao_bonus", "zhaxiang_active"
]
"""座位级布尔技能状态的字段闭集（:meth:`Engine._set_player_flag` 的 ``field``）。"""


class Outcome(BaseModel):
    """终局判定结果；由唯一的 :meth:`Engine.check_game_end` 产出。

    ``side`` / ``winners`` 形状统一（票 25 / 决策回票 14 §1）：无身份局填
    ``side=None``、胜局 ``winners=(获胜座位,)``、平局 ``()``；身份局 ``winner=None``、
    ``side`` 与 ``winners`` = 获胜一侧的全部座位（**含阵亡者**、按座位升序）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    result: Literal["winner", "draw"]
    winner: int | None = None
    side: Side | None = None
    winners: tuple[int, ...] = ()
    reason: str


def _card_from_hand(state: GameState, source: int, card: CardRef) -> bool:
    """这张使用 / 打出的牌此刻是不是**手牌**（M6 票 16 的界【涯角】判据）。

    必须在牌离开手牌**之前**调用（``start_use`` / ``play_card`` 建帧时）：
    - 实体牌：它在来源座位的手牌区里；
    - 转化牌：【涯角】牌面写的是"使用或打出**手牌**"，单源 / 多源转化的来源牌只要有一张
      在手牌区就算（【丈八蛇矛】是两张手牌）；
    - 纯虚拟牌：没有来源实体牌（【八卦阵】的视为【闪】）⇒ 不是手牌。

    结果写进 USE / PLAY 帧的 ``from_hand``（帧私有载荷、不进记录流），故钩子仍按统一的
    ``(host, frame)`` 面读它，引擎不把"哪条技能读它"写死在这里。
    """
    if card.is_virtual:
        hand = state.player(source).hand
        return any(card_id in hand for card_id in card.virtual_sources)
    return card.card_id in state.player(source).hand


def _child_payload(
    kind: FrameKind,
    parent_data: dict[str, Any],
    scalars: Sequence[tuple[str, Any]],
    *,
    targets: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """子帧载荷 = 从使用帧继承 ``source`` / ``targets`` / ``card`` / ``use_timing``
    + 行为表的标量字面量。

    ``use_timing`` 一路带着走，好让"这次使用是从哪个窗口开的"在后面每一层都读得到
    （濒死求桃开的【桃】用它把自己的回复结算与行为表的效果帧区分开，票 21）。
    伤害帧再规范化成官方**六要素**（票 07 §6）：``target`` = 唯一目标、
    ``channel`` = 使用的实体牌、``chain`` 恒 ``False``；``nature`` 读伤害步的声明
    （火杀 FIRE / 雷杀 THUNDER），未声明即 ``NORMAL``——属性住行为表，``CardRef`` 无此字段。

    ``targets`` 由效果执行器显式给**当前目标**（票 27）：多目标结算里每一层效果都只
    作用于 ``targets[cursor]``，子帧因此不会把整张牌的目标列表继承下去。
    """
    data: dict[str, Any] = {
        "source": parent_data.get("source"),
        "targets": tuple(parent_data.get("targets", ())) if targets is None else tuple(targets),
        "card": parent_data.get("card"),
        "use_timing": parent_data.get("use_timing"),
    }
    if "deal_bonus" in parent_data:
        # 使用级的来源侧伤害加伤（M5-04 的【酒】+1）：随使用帧 → RESOLVE → DAMAGE 一路带走。
        # 只在**有**这个键时继承，故其它牌的子帧载荷与旧路径逐键相同（帧私有，不进记录流）。
        data["deal_bonus"] = parent_data["deal_bonus"]
    for key, value in scalars:
        if key in data:
            raise ValueError(f"行为表的标量 {key!r} 会覆盖子帧继承的字段")
        data[key] = value
    if kind is FrameKind.DAMAGE:
        targets_value = data["targets"]
        data["target"] = targets_value[0] if targets_value else None
        data["channel"] = data["card"]
        data.setdefault("nature", DamageNature.NORMAL)
        data["chain"] = False
    return data


def _raw_kind(response: object) -> str:
    """损坏载荷声明的 ``kind``；读不出来就报 Python 类型名（只作诊断）。"""
    if isinstance(response, dict):
        kind = response.get("kind")
        if isinstance(kind, str):
            return kind
    return type(response).__name__


def _raw_answers_seq(response: object) -> int | None:
    """尽量从损坏载荷里取出 ``answers_seq``；只作诊断，不参与任何判定。"""
    if isinstance(response, dict):
        value = response.get("answers_seq")
    else:
        value = getattr(response, "answers_seq", None)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _first_validation_issue(error: ValidationError) -> str:
    """结构错误的首条"路径 + 说明"，好让记录流里的拒绝能直接定位。"""
    first = error.errors(include_url=False)[0]
    where = ".".join(str(part) for part in first["loc"]) or "（根）"
    return f"{where}: {first['msg']}"


def _raw_text(response: object) -> str:
    """损坏载荷的 canonical 文本，供记录流留存；不可 JSON 化时退化为 ``repr``。"""
    payload = response.model_dump(mode="json") if isinstance(response, BaseModel) else response
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(response)


def _activation_payload(
    activation: Mapping[str, tuple[Any, ...]], skill_id: str, timing: Timing | None
) -> dict[str, Any]:
    """复合动作的帧私有载荷：``(picks, choices)`` + 归属。

    归属同时带 **技能 id**（给记录 / 调试）与**流水线名**（给
    :func:`~sgs.skills.flow._activation_belongs`）。后者是必需的：界版技能的流水线名带
    ``jie_`` 前缀（``jie_rende`` / ``jie_tuxi``），与技能 id 的短名（``rende`` / ``tuxi``）
    不再相等；只按短名判归属会让界版的**复合动作不被摊回**、改去逐步重问（M6 票 16
    的界【仁德】第一次撞到）。流水线名在**提交那一刻**按同一份登记表解析，与执行器随后
    读到的是同一个对象。
    """
    payload: dict[str, Any] = {**activation, "skill_id": skill_id}
    flow = flow_for(skill_id, timing)
    if flow is not None:
        payload["flow"] = flow.name
    return payload


class Engine:
    """一局的驱动器：显式结算栈 + 回合环 + 记录流。"""

    def __init__(
        self,
        state: GameState,
        *,
        seed: int,
        init_records: Sequence[JournalRecord] = (),
        shuffle_source: Randomness | None = None,
        journal: bool = True,
    ) -> None:
        self.state = state
        self._journal = journal
        """是否**保留**记录流（票 11 的 opt-in 开关；缺省 ``True`` = 既有行为一字不变）。

        ``False`` 时只关**保留 / 追加**：header 与开局记录**照常构造**（spec §1.1 的
        ``norecords-append``，不是"整条不构造"），只是不装进 :attr:`_records`；局内每次
        ``_emit`` 的记录对象同样照常在调用点构造，这里只跳过 append。``_seq`` /
        ``_last_seq`` 照旧推进，故记录坐标与开记录时是同一套逻辑序号。
        """
        initial: list[JournalRecord] = [
            Header(
                seed=seed,
                pack_id=state.pack_id,
                deck_profile=state.deck_profile,
                rule_config=state.rule_config,
            )
        ]
        initial.extend(init_records)
        self._records: list[JournalRecord] = initial if journal else []
        self._seq = len(init_records)
        self._last_seq = len(init_records)

        self._shuffle: Randomness = (
            shuffle_source
            if shuffle_source is not None
            else RandomSource(seed).substream("shuffle")
        )
        """洗牌子流：初次洗牌与中局重洗共用同一个位置（票 08 §3 / 票 23）。

        ``Engine.new_game`` 把初始化那次洗牌用过的同一个句柄交进来；``from_state`` 起于位置 0。
        与 ``ai`` 子流相互独立，回放不被参与者消耗随机数破坏。
        """

        self._select: Randomness = RandomSource(seed).substream("select")
        """**隐藏槽乱序**子流（票 19 §12 / 票 16 §2）：只给选择面里的他人手牌打乱位置。

        与 ``shuffle`` / ``ai`` / ``identity`` 相互独立；判定仍走 ``shuffle``（票 03 不变），
        故"新增一条子流"不扰动任何既有随机序列。
        """

        self.stack = ResolutionStack()
        self.steps: StepTable = build_steps(state.rule_config.mode)
        """帧类型 → 步骤表：使用 / 结算的步骤在 :mod:`sgs.engine.steps`。

        死亡段的形状按模式分叉（票 25）：身份局多一段"亮出身份牌前"，
        无身份局保持 M1 的四段。
        """

        self.pending: Query | None = None
        self.pending_outcome: Outcome | None = None
        self.finished = False

        self._answers: dict[
            tuple[object, ...], tuple[Action, ActivateSkill | None]
        ] = {}
        """**被接受、但还没被它自己那一问取走**的应答（票 02 的应答归属）。

        键 = :meth:`_ask_identity` 的三元组；值 = ``(应答, 复合动作载荷或 None)``。正常流程
        里提交后立刻被重入的那一问取走（恒空一瞬）；"问了、答了，但问的那一帧还没重入"时
        留在这里等它重入 —— 不重问（``CODING_STANDARDS.md`` §3）。上限见
        :data:`_ANSWER_BACKLOG_LIMIT`。
        """

        self._asked_identity: tuple[object, ...] | None = None
        """当前挂起那一问的身份；:meth:`Engine.submit` 据此把应答记到**它**名下。"""
        self._structured_activation: ActivateSkill | None = None
        """最近一次**经校验器接受**的复合 ``ActivateSkill``（W4-1）。

        ``submit`` 的成员判定之外只有这一条让开的路：复合动作不在 ``legal_actions`` 里，
        但它的 ``picks`` / ``choices`` 是**实体牌 id / 座位**（不是面内位置），后续
        ``start_skill`` / ``ask_skill_activation`` 据这个身份把载荷原样挂进帧，交给
        ``run_flow`` 跳过本人应答的步骤——而不是当成面内位置去索引。
        """

        self._activation_frame: Frame | None = None
        """最近一次 ``SKILL_ACTIVATION`` 询问的**承载帧**（票 20）。

        帧外时机的钩子（阶段体 / 回合末）用**不压栈**的持久帧发问，故应答提交时栈是空的、
        校验器读不到上下文（【诛害】的选牌池按当前回合角色算）。这里留一份引用：栈非空时
        仍以栈顶为准（既有行为逐字不变），栈空时用它——与发问时渲染清单的那份上下文同源。
        """

        self._conversion_answer: Action | None = None
        """最近一次**转化技确认**解析出的普通使用 / 打出动作（W4-fix-2）。"""
        self._started = False
        self._frame_seq = 0
        self._check_seq: int | None = None
        self._deaths_pending = 0
        """同批未关闭的死亡单元计数（票 14 §3）：``enter_death`` +1、DEATH 帧出栈 -1。"""

        self._batch_facts: list[DeathFact] = []
        """本批每次死亡在检测点定格的事实；批次关闭时求解一次，未终局则清空。"""
        self._turn_pc = 0
        self._move_recorded: tuple | None = None
        """最近一次**无承载帧**移动已落过记录的那个幂等键（票 07）。

        帧外移动没有 ``frame.data`` 可挂"记录过了"的标记，故这个判据住引擎上；
        重入同一批牌时不再重复落 ``MOVE_AFTER`` 记录。
        """
        self._move_pending: list[_PendingMove] = []
        """被钩子挂起的 ``MOVE_AFTER`` **栈**（票 07 的单槽 → M6 票 25 的栈）。

        每项是 :class:`_PendingMove`：**续跑帧**（帧私有里带着钩子的询问缓存与效果游标，
        M6 票 08）与**原始承载帧**（只用于"时机记录去重"的旧语义，见
        :meth:`_flush_pending_move`）分列。字段名见该类型；读栈的代码只用属性，不按下标。

        "失去牌后"是**内联时机**，它的钩子可以提问。提问把控制权交回驱动循环之后，
        重入**不一定落在同一个步骤上**（拆顺会继续跑效果帧的后续步骤、使用链会继续跑
        L4/L5），故挂起状态存在引擎上，由 :meth:`_flush_pending_move` 在"引擎空闲"处
        接着派发完——这是"移动入口不建帧"（R1-Q2）的代价，也是它的唯一落点。

        **必须是栈**（票 25）：钩子里的摸牌 / 弃牌本身就是另一次 ``MOVE_AFTER``，它的
        钩子也可以提问（【连营】逐座摸牌里触发【清俭】）。内层先挂起、外层在展开返回时
        才知道自己也要挂起——若只有一个槽，后写的一层会覆盖另一层，被覆盖那层的续跑
        游标永久丢失、待答询问随后被下一个询问错配。内层先挂起、却在外层之后入栈，故
        入栈下标取**派发开始前**的栈深（见 :meth:`_dispatch_move_after`），而不是简单
        append：这样"最深一层永远在栈顶"（后进先出），:meth:`_flush_pending_move` 才能
        先续内层、再续外层。
        """
        self._conduction: dict[str, Any] | None = None
        """进行中的**连环传导**（M5-03 / spec §5.1.1）：名单 + 游标 + 源伤害事实。

        名单在传导开始时**一次锁定**（``seats`` 元组），此后中途新横置的角色不会进来；
        轮到时已重置 / 已阵亡的座位在派发前跳过。它是引擎规则处理，不是 ``Timing`` /
        技能钩子，故不进记录流：回放重跑引擎时会按同一份状态重建。
        """
        self._turn_data: dict[str, Any] = {}
        self._turn_steps: tuple[Callable[[], Any], ...] = (
            self._turn_before_start,
            self._turn_start,
            self._phase_prepare,
            self._phase_judge,
            self._phase_draw_start,
            self._phase_draw,
            self._phase_draw_end,
            self._phase_play_start,
            self._phase_play_idle,
            self._phase_play_end,
            self._phase_discard_start,
            self._phase_discard,
            self._phase_discard_end,
            self._phase_finish,
            self._turn_end,
            self._turn_after_end,
        )

    # -- 便利包装 -----------------------------------------------------------

    @classmethod
    def new_game(
        cls,
        *,
        seed: int,
        rule_config: RuleConfig,
        pack: ContentPack,
        heroes: Sequence[str] = (),
        hero_pool: Sequence[str] | None = None,
        pinned_hero: str | None = None,
        pinned_seat: int = 0,
        hero_config: HeroDealConfig | None = None,
        journal: bool = True,
    ) -> "Engine":
        """造初始状态（实例化 → 洗牌 → 发身份 → 发武将 → 发牌）并带上开局那几条记录。

        初次洗牌与后续重洗共用同一个 ``shuffle`` 子流句柄：重洗从初次洗牌之后的位置继续取数
        （票 08 §3），因此同一 seed 的整局（含每一次重洗）逐字节可复现。
        ``heroes`` / ``hero_pool`` 与 :func:`build_initial` 同义（票 02）：缺省 = 无武将模式。
        ``pinned_hero`` / ``pinned_seat`` 同义（票 19 的点将：钉住一个座位、其余随机）；
        ``hero_config`` 是票 12 的**会话配置**（禁将 + 手选），与种子一起冻结、不进记录流。
        ``journal`` 见 :meth:`__init__`：开局那几条记录**照常构造**，``False`` 时只是不保留。
        """
        shuffle_source = RandomSource(seed).substream("shuffle")
        state, init_records = build_initial(
            seed=seed,
            rule_config=rule_config,
            pack=pack,
            shuffle_source=shuffle_source,
            heroes=heroes,
            hero_pool=hero_pool,
            pinned_hero=pinned_hero,
            pinned_seat=pinned_seat,
            hero_config=hero_config,
        )
        return cls(
            state,
            seed=seed,
            init_records=init_records,
            shuffle_source=shuffle_source,
            journal=journal,
        )

    @classmethod
    def from_state(cls, state: GameState, *, seed: int, journal: bool = True) -> "Engine":
        """低层入口：从**人为指定的局面**起跑（只写 header，不虚构开局记录）。"""
        return cls(state, seed=seed, journal=journal)

    # -- 记录流 -------------------------------------------------------------

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        return tuple(self._records)

    def journal(self) -> str:
        """canonical JSONL（唯一序列化器的入口）；关记录时是空串。"""
        return dump_journal(self._records)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, record: JournalRecord) -> None:
        """**局内**记录追加的唯一入口：先推进序号，再按开关决定是否保留（票 11 §1.1）。

        header 与开局记录在 :meth:`__init__` 里一次性装入，不走这里。顺序很要紧：
        ``_last_seq`` 是引擎唯一会回读的记录派生量（``check_game_end`` 的序号对账），
        关记录时也必须与开记录时一致；而 :attr:`_records` 只是保留面。
        """
        seq = getattr(record, "seq", None)
        if seq is not None:
            self._last_seq = seq
        if self._journal:
            self._records.append(record)

    def emit_timing(
        self,
        timing: Timing,
        *,
        actor: int | None = None,
        source: int | None = None,
        targets: tuple[int, ...] = (),
        cards: tuple[CardRef, ...] = (),
        frame_id: int = 0,
        depth: int = 0,
        event: Any = None,
    ) -> Control:
        """dispatch 一个时机；空实现也照记。

        帧外的时机（回合环 / 判定阶段收尾）没有承载帧，M2 的装备钩子都挂在 L4 / L5 / L7，
        故这里只记录、不派发技能；返回 ``None`` 让"返回控制值"的接缝在形状上统一
        （票 19 §8 / 票 30）。

        ``event`` 是**帧外时机**的私有载荷（票 07 的 ``MOVE_AFTER`` 用在无承载帧的移动上，
        如弃牌阶段）：写进一个**不压栈的临时帧**，使钩子回调拿到与帧内派发同形的上下文；
        ``frame_id`` / ``depth`` 仍由调用方给定，故记录流里的坐标与同一次移动的
        ``card_move`` 一致（临时帧不进记录流，不会被看见）。
        """
        dispatch_frame = frame_id == 0 and event is not None
        self._emit(
            TimingRecord(
                seq=self._next_seq(),
                timing=timing,
                frame_id=frame_id,
                depth=depth,
                actor=actor,
                source=source,
                targets=targets,
                cards=cards,
            )
        )
        if not dispatch_frame:
            return None
        if not move_after_providers(self.state, event=event):
            return None
        frame = Frame(
            kind=FrameKind.RESOLVE,
            data={"source": actor, "targets": targets, "card": None},
            frame_id=frame_id,
        )
        frame.data[MOVE_EVENT_KEY] = event
        dispatch_move_after(self, frame)
        return None

    def emit_delta(self, delta: Any, *, frame_id: int = 0, depth: int = 0) -> None:
        self._emit(wrap_delta(delta, seq=self._next_seq(), frame_id=frame_id, depth=depth))

    def _emit_frame(self, op: str, frame: Frame) -> None:
        self._emit(
            FrameRecord(
                seq=self._next_seq(),
                op=op,
                frame_id=frame.frame_id,
                frame_kind=frame.kind,
                depth=self.stack.depth,
                return_to=frame.return_to,
            )
        )

    # -- 结算栈 -------------------------------------------------------------

    def push_frame(self, kind: FrameKind, data: dict[str, Any], *, timing: Timing) -> Frame:
        """压入一个子帧；``return_to`` 命名式记录"从哪回来"（父帧 kind + 时机 id）。

        胜负检测点之后**拒绝插入任何新结算**（票 13 §3 / 票 04 §5 的压栈守卫），
        **只放行 DEATH 帧**（票 14 §3）：同批的第二次死亡必须能进入，才谈得上"批次末宣布"。
        M1 无技能，实战天然不发生，守卫仍在，使"检测点之后不再开新结算"是结构事实。
        """
        if self.pending_outcome is not None and kind is not FrameKind.DEATH:
            raise RuntimeError("胜负已判定：拒绝插入新结算（票 13 §3 / 票 04 §5）")
        parent = self.stack.top if self.stack else None
        return_to = (parent.kind.value, timing.value) if parent is not None else None
        self._frame_seq += 1
        frame = Frame(kind=kind, data=data, return_to=return_to, frame_id=self._frame_seq)
        self.stack.push(frame)
        self._emit_frame("push", frame)
        return frame

    def open_phase_body_frame(self, frame: Frame) -> Frame:
        """给**不压栈**的阶段体帧一个记录流身份（只在需要展示承载时用）。

        阶段体（准备 / 摸牌 / 结束）的帧住 ``_turn_data``、不进结算栈，缺省
        ``frame_id = 0``（见 :meth:`_dispatch_phase_body`）。展示项的寿命挂
        ``frame_id``：正数 = 等该帧 ``pop`` 后才淡出，0 = 固定 1s 停留。故像【裸衣】
        这样"亮牌后还要问一次"的阶段体技能，必须让亮牌有一个真实帧号；否则牌会在
        询问被回答之前淡出。

        这不是把帧压栈：只分配单调帧号并落一条 ``push``，生命周期由
        :meth:`close_phase_body_frame` 在同一阶段体收尾时闭合。没有展示承载需求的
        阶段体不调用它，记录流因此与旧行为逐字节相同。
        """
        if frame.frame_id > 0:
            return frame
        self._frame_seq += 1
        frame.frame_id = self._frame_seq
        self._emit_frame("push", frame)
        return frame

    def close_phase_body_frame(self, frame: Frame) -> None:
        """关闭 :meth:`open_phase_body_frame` 打开的帧；没打开过则零动作。"""
        if frame.frame_id <= 0:
            return
        self._emit_frame("pop", frame)
        frame.frame_id = 0

    def enter_death(self, frame: Frame, seat: int) -> Frame:
        """死亡入口：标记阵亡并压入 **DEATH** 子帧（票 22）。

        死亡结算挂在濒死帧之上（官方 `flow/neardeath`：A 死亡后**插入**死亡结算），
        故濒死帧及其下整条伤害链留在栈上，留给终局清栈逐帧记 ``discard``。
        ``alive`` 在检测点之前置假，唯一终局入口据此判"存活角色数 ≤ 1"。
        进入死亡单元时把同批计数 +1（票 14 §3）：DEATH 帧出栈时 -1，归零即批次关闭。

        ``damage_source`` 是**未经资格检查**的致死来源座位（濒死帧从扣减体力帧带下来）；
        ``death_before_start`` 再把它定格成 ``killer``（票 14 §4.1）。它放在帧私有载荷里、
        不进任何 ``timing`` 记录的 ``source``，故记录面一个字节都不动。
        """
        self.state.player(seat).alive = False
        self._deaths_pending += 1
        return self.push_frame(
            FrameKind.DEATH,
            {
                "target": seat,
                "targets": (seat,),
                "source": frame.data.get("source"),
                "damage_source": frame.data.get("damage_source"),
            },
            timing=Timing.DEATH_BEFORE_START,
        )

    def pass_away(self, frame: Frame, seat: int) -> Frame:
        """**技能直接致死**（票 07 V1 / 神关羽【武魂】②）：无伤害来源、无 killer、不进濒死。

        与 :meth:`enter_death` 的唯一差别是来源：``source`` / ``damage_source`` 恒 ``None``
        ⇒ ``death_before_start`` 不定格 killer ⇒ **无死亡奖惩**（反贼不摸 3、主公不弃牌）。
        ``alive=False`` / 同批计数 / DEATH 帧与既有死亡完全同形；濒死 / 求桃不经过（牌面写
        的是"该角色死亡"，不是"受到伤害"）。
        """
        self.state.player(seat).alive = False
        self._deaths_pending += 1
        return self.push_frame(
            FrameKind.DEATH,
            {
                "target": seat,
                "targets": (seat,),
                "source": None,
                "damage_source": None,
            },
            timing=Timing.DEATH_BEFORE_START,
        )

    def outcome_decided(self) -> bool:
        """终局是否已判定（只读；死亡时技能据此跳过，票 07 V3）。"""
        return self.pending_outcome is not None

    def discard_dead_cards(self, frame: Frame) -> None:
        """``DEATH_ON`` Ⅰ.a：把死亡角色区域里所有的牌置入弃牌堆（规则 4.8 第 2 步）。

        逐来源区各发一条 delta（票 08 §7）：手牌 / 装备区 / 判定区各一条，**不**新增复合
        delta。归属 = 阵亡者本人（弃置者）；判定区的实体牌**直接**进弃牌堆（不经过处理区）。
        M1 的 ``basic_53`` 没有装备与延时锦囊，故实测仍只有手牌那一条。

        **不派发"失去牌后"**（票 33 的裁定）：死者自己的区域清空**仍直接调**
        :func:`~sgs.effects.move_card`——"死亡结算中死者的技能还能不能发动"官方手册没有
        明文（社区通说是不发动），本票只钉**存活者**一侧（主公杀忠臣的弃置），死者一侧维持
        现状。理由与扩展路径登记在 `开发文档/06` §5「程序化约定与偏离一手」。注意这条与
        :meth:`_lord_discards_everything` 的不对称是**有意的**：后者弃的是存活主公的牌。

        **重入是零动作**（票 33 的 §3 纪律）：本方法自身不会挂起，但它在
        :func:`~sgs.engine.steps.death_settle_cards` 里与"可能挂起"的奖惩同一步，故续跑会
        再进一次。帧私有 :data:`_DEAD_CARDS_CLEARED` 让第二次直接返回——不靠"死者的区恰好
        是空的"这种**状态巧合**当守卫（死者的区里若被钩子放过牌，那也不属于这一次 Ⅰ.a）。
        """
        if frame.data.get(_DEAD_CARDS_CLEARED):
            return
        seat = frame.data["target"]
        ctx = EffectContext(
            actor=seat,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )
        for source_ref in (
            ZoneRef(Zone.HAND, seat),
            ZoneRef(Zone.EQUIP, seat),
            ZoneRef(Zone.JUDGE, seat),
            *(
                ZoneRef(Zone.HERO, seat, key)
                for key in tuple(self.state.player(seat).hero_cards)
            ),
        ):
            card_ids = tuple(self.state.zone(source_ref))
            if not card_ids:
                continue
            delta = move_card(
                self.state,
                ctx,
                self.state.refs(card_ids),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=seat,
            )
            self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        self._clear_dead_markers(frame, seat)
        frame.data[_DEAD_CARDS_CLEARED] = True

    def reveal_dead_identity(self, frame: Frame) -> None:
        """``DEATH_REVEAL_IDENTITY_BEFORE``：亮出阵亡者的身份，发一条 ``identity_reveal``。

        只进身份局的死亡段（票 25）；1v1 的步骤表里没有这一步。主公也照发——
        它开局已亮明，再发一次只是**形状统一**（票 14 §5）。
        """
        seat = frame.data["target"]
        self.emit_delta(
            IdentityRevealDelta(seat=seat, identity=self.state.player(seat).identity),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )

    def apply_death_rewards(self, frame: Frame) -> Control:
        """``DEATH_ON`` 的 Ⅱ：执行死亡奖惩（票 25 / 决策回票 14 §4.2）。

        奖惩顺序在 Ⅰ（系统处理牌）之后；只进身份局。无来源伤害（闪电）、自杀、
        失去体力 ⇒ ``killer`` 未定格 ⇒ 无奖惩；killer 已阵亡 ⇒ 跳过（保"阵亡者区域为空"）。
        杀反贼 ⇒ killer 摸 3 张；主公杀忠臣 ⇒ **主公**弃置自己的手牌 / 装备区 / 判定区
        （一手 `flow_death.txt`「所有牌」，登记 `R:323` 偏差）。

        **返回值透传"失去牌后"的挂起**（票 33）：主公的弃置是**存活角色**的规则动作，
        走移动入口派发 ``MOVE_AFTER``，钩子提问时本方法返回 ``"stay"``，由调用方
        （``steps.death_settle_cards``）原样透传——吞掉它会让引擎带着待答询问继续搬牌。
        """
        if self.state.rule_config.mode is not Mode.IDENTITY:
            return None
        killer = frame.data.get("killer")
        if killer is None or not self.state.player(killer).alive:
            return None
        dead = frame.data["target"]
        dead_identity = self.state.player(dead).identity
        if dead_identity is Identity.REBEL:
            self._reward_killer_draws(frame, killer)
            return None
        if dead_identity is Identity.LOYALIST and self.state.player(killer).identity is Identity.LORD:
            return self._lord_discards_everything(frame, killer)
        return None

    def _lord_discards_everything(self, frame: Frame, lord: int) -> Control:
        """主公杀死忠臣：逐来源区（手牌 / 装备区 / 判定区）各一条 ``card_move`` 入弃牌堆。

        **经移动入口搬牌**（票 33）：承载帧 = 死亡帧，故 delta 的坐标与旧实现逐字相同
        （帧外临时帧会把 ``frame_id`` 记成 0，打翻既有基准），但每次弃置都派发
        ``MOVE_AFTER``——主公是**存活角色**，"失去最后的手牌 / 装备区里的一张牌"这类
        钩子按规则必须被问。

        **重入按"定格的分组 + 游标"续搬**（:data:`_LORD_PENALTY_GROUPS` /
        :data:`_LORD_PENALTY_MOVED`）：钩子提问会让本步挂起、应答后整步重入；而钩子
        可能已经在结算中途把牌摸回主公手里（【连营】就是），所以"要弃哪些牌"必须定格在
        首次进入的那一刻，搬的进度也必须记在帧私有里。续跑把同一组再交进来时，搬牌本身
        由 :data:`_MOVE_DONE` 的重入判据挡掉（"这次移动已经搬完"）。
        """
        groups = frame.data.get(_LORD_PENALTY_GROUPS)
        if groups is None:
            found: list[tuple[ZoneRef, tuple[int, ...]]] = []
            for source_ref in (
                ZoneRef(Zone.HAND, lord),
                ZoneRef(Zone.EQUIP, lord),
                ZoneRef(Zone.JUDGE, lord),
            ):
                card_ids = tuple(self.state.zone(source_ref))
                if card_ids:
                    found.append((source_ref, card_ids))
            groups = tuple(found)
            frame.data[_LORD_PENALTY_GROUPS] = groups
        moved = int(frame.data.get(_LORD_PENALTY_MOVED, 0))
        while moved < len(groups):
            _source_ref, card_ids = groups[moved]
            control = self.move_in_frame(
                frame,
                self.state.refs(card_ids),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=lord,
                actor=lord,
            )
            if control == "stay":
                return "stay"
            moved += 1
            frame.data[_LORD_PENALTY_MOVED] = moved
        return None

    def _reward_killer_draws(self, frame: Frame, killer: int) -> None:
        """杀死反贼的奖惩摸牌：与摸牌阶段**同一原语、同一洗牌子流**（票 14 §5）。

        短摸（两堆皆空）⇒ 置 ``both_decks_empty`` 平局并**覆盖**已定胜负；
        摸到几张进几张（可能 0 张、不发 ``card_move``）。
        """
        drawn = draw_from_deck(self.state, REBEL_REWARD_DRAW, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_delta(drawn.shuffle, frame_id=frame.frame_id, depth=self.stack.depth)
        if drawn.cards:
            ctx = EffectContext(
                actor=killer,
                source=killer,
                targets=(frame.data["target"],),
                frame_id=frame.frame_id,
                depth=self.stack.depth,
            )
            delta = move_card(
                self.state,
                ctx,
                drawn.cards,
                to_zone=ZoneRef(Zone.HAND, killer),
                reason=MoveReason.DRAW,
                owner=killer,
            )
            self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        if len(drawn.cards) < REBEL_REWARD_DRAW:
            self.check_game_end(draw_reason="both_decks_empty")

    def pop_frame(self) -> Frame:
        frame = self.stack.pop()
        self._after_pop(frame)
        return frame

    def _after_pop(self, popped: Frame) -> None:
        """唯一的出栈收尾：记一条 ``pop``，再跑响应型使用的**成败传播**（票 11 §2）。

        ``pop_frame`` 与 :meth:`_step` 都经这里，故"帧的生命周期"在记录流里没有缺口、
        传播也只可能跑一次。DAMAGE 帧出栈后另跑**连环传导**的引擎规则（M5-03）：它是
        "初始目标的伤害事件处理完之后"由系统决定派发的属性伤害，不是新时机。
        """
        self._emit_frame("pop", popped)
        self._propagate_cancel(popped)
        if popped.kind is FrameKind.DAMAGE:
            self._settle_chain_conduction(popped)

    def _conduction_order(self) -> tuple[int, ...]:
        """**锁定名单**：从当前回合角色起、座位号递增（逆时针、绕环）扫描仍横置的座位。

        扫描**只看横置**（存活与否留给轮到时跳过）：这样"名单里的角色在轮到他之前死亡"
        与"一开始就阵亡"走同一条跳过路径。当前回合角色若仍横置则排第一，否则第一个
        横置角色自然成为起点。初始目标此刻已重置，故不会出现在名单里。
        """
        total = len(self.state.players)
        if total <= 0:
            return ()
        start = self.state.turn_seat if self.state.turn_seat is not None else 0
        return tuple(
            (start + offset) % total
            for offset in range(total)
            if self.state.player((start + offset) % total).chained
        )

    def _settle_chain_conduction(self, popped: Frame) -> None:
        """DAMAGE 帧出栈后的连环传导（M5-03 / spec §5.1.1 的逐条裁定）。

        - **触发**：只有源头属性伤害（``chain=False``、火 / 雷）命中横置角色——判据由
          :func:`~sgs.engine.steps.damage_finalize_amount` 写进帧私有 ``chain_trigger``；
        - **锁定**：初始目标的伤害事件处理完之后才建名单（``_conduction_order``），此后
          中途新横置的不进名单；轮到时已重置 / 已阵亡的跳过；
        - **派发**：名单逐个压 **DAMAGE 帧**（``chain=True``）：来源 / 属性 / 渠道沿用源伤害，
          数量 = 初始目标**实际受到**的伤害值（帧私有 ``amount`` 已过修饰器链）；
        - **不再传导**：传导帧的 ``chain_trigger`` 恒假（``chain=True``），故本方法只会在
          下一帧出栈时推进游标，不会开第二轮。
        """
        conduction = self._conduction
        if conduction is not None and conduction["frame_id"] == popped.frame_id:
            conduction["cursor"] += 1
            self._push_next_conduction()
            return
        if self.finished or self.pending_outcome is not None:
            return
        if not popped.data.get(CHAIN_TRIGGER_KEY):
            return
        seats = self._conduction_order()
        if not seats:
            return
        self._conduction = {
            "seats": seats,
            "cursor": 0,
            "frame_id": 0,
            "source": popped.data.get("source"),
            "nature": popped.data.get("nature"),
            "amount": popped.data["amount"],
            "channel": popped.data.get("channel"),
            "card": popped.data.get("card"),
            "use_timing": popped.data.get("use_timing"),
        }
        self._push_next_conduction()

    def _push_next_conduction(self) -> None:
        """按锁定名单派发下一个传导伤害；名单走完即清空传导状态。"""
        conduction = self._conduction
        assert conduction is not None
        if self.finished or self.pending_outcome is not None:
            self._conduction = None
            return
        while conduction["cursor"] < len(conduction["seats"]):
            seat = conduction["seats"][conduction["cursor"]]
            player = self.state.player(seat)
            if not player.alive or not player.chained:
                # 轮到时已阵亡 / 已解除横置 ⇒ 跳过（名单本身不重算）。
                conduction["cursor"] += 1
                continue
            frame = self.push_frame(
                FrameKind.DAMAGE,
                {
                    "source": conduction["source"],
                    "targets": (seat,),
                    "card": conduction["card"],
                    "channel": conduction["channel"],
                    "use_timing": conduction["use_timing"],
                    "target": seat,
                    "nature": conduction["nature"],
                    "chain": True,
                    "amount": conduction["amount"],
                },
                timing=Timing.DAMAGE_DEAL,
            )
            conduction["frame_id"] = frame.frame_id
            return
        self._conduction = None

    def _propagate_cancel(self, popped: Frame) -> None:
        """出栈钩子：响应型使用的 RESOLVE 帧出栈即向**它指向的那一层**传播成败（票 11 §2）。

        深层总在浅层之前出栈，故出栈序**就是**自底向上序——"等链全部结算完再自底向上
        做一次"因此只落在这一个钩子上，不必另建裁决阶段。成功的响应把它指向的那一层标成
        被抵消；**自身被抵消的那层什么都不抵消**（子帧已先把父帧标掉，传播自然停在它）。

        成败读 :data:`_RESPONSE_CANCELED` 而不是 ``canceled``：后者是**逐目标分槽**的窗口
        状态，游标推进时会被清掉（见 ``steps._PER_TARGET_FRAME_KEYS``），而本钩子在帧
        **出栈之后**才读它。被抵消时两个键一起写，故出栈时结果仍在。

        ``cancel_target`` 里的座位会被**核对**：传播只许落在"该目标仍在窗口里"的那一层，
        否则宁可 fail-closed——静默标错目标会在多目标结算里抵消掉别的目标。
        """
        if popped.kind is not FrameKind.RESOLVE:
            return
        target = popped.data.get("cancel_target")
        if target is None or popped.data.get(_RESPONSE_CANCELED):
            return
        parent_id, seat = target
        for frame in reversed(self.stack.frames):
            if frame.frame_id != parent_id:
                continue
            if self.current_target(frame) != seat:
                raise RuntimeError(f"响应指向座位 {seat}，但结算帧停在 {self.current_target(frame)}")
            frame.data["canceled"] = True
            frame.data[_RESPONSE_CANCELED] = True
            return
        raise RuntimeError(f"响应指向的结算帧 {parent_id} 不在栈上；结算栈被破坏")

    # -- 结算帧步骤可用的操作面（步骤表在 steps.py，只经这些方法改状态）---------

    def emit_frame_timing(
        self,
        frame: Frame,
        timing: Timing,
        *,
        actor: int | None = None,
        cards=(),
        targets: tuple[int, ...] | None = None,
        event: Any = None,
        emit_record: bool = True,
    ) -> Control:
        """以 ``frame`` 为主体 dispatch 一个时机；``source`` 随帧继承，并派发技能钩子。

        ``targets`` 缺省取帧的目标列表；C 段的时机记录由步骤表显式给**当前目标**
        （票 11 §3.3），使逐目标结算在记录流里可定位。

        ``event`` 是这一次时机的**私有载荷**（票 07 的 ``MOVE_AFTER`` 用它带 :class:`MoveEvent`），
        写进本帧的私有槽后原样透传给 :func:`~sgs.skills.dispatch_timing` 的钩子。

        **记录幂等**（票 30）：钩子可以因询问而暂停，重入时步骤会再调本方法一次。
        同一个"时机实例"只在**第一次**落一条 ``timing`` 记录；``_pending_timing`` 记住
        "上一次派发是否停在这里"，重入同键即跳过记录、继续派发。真正重复出现的时机
        （逐目标的 C2–C5、逐窗口的 ``NEAR_DEATH_AT``、**同帧的多次移动**）键不同或中间隔了
        一次完成的派发，故不会被误吞——同帧的多次移动靠 ``_MOVE_EVENTS`` 计数把"第 n 次
        移动"与"第 n 次重入"分开（票 07）。

        ``emit_record=False`` 表示**这一轮只派发、不再落记录**（票 05 的逐点触发：一次伤害
        事件只有一条 ``DAMAGE_AFTER_TAKE`` 记录，第 2 轮起由派发侧显式关掉记录）。
        """
        if isinstance(event, MoveEvent):
            # 只有"失去牌后"的载荷需要落进帧私有槽（钩子经 ``move_event`` 读它）。
            frame.data[MOVE_EVENT_KEY] = event
        key = (
            timing,
            actor,
            tuple(targets) if targets is not None else None,
            tuple(card.card_id for card in cards),
            frame.data.get(_MOVE_EVENTS, 0) if event is not None else None,
        )
        if emit_record and frame.data.get("_pending_timing") != key:
            self.emit_timing(
                timing,
                frame_id=frame.frame_id,
                depth=self.stack.depth,
                actor=actor,
                source=frame.data.get("source"),
                targets=(
                    tuple(frame.data.get("targets", ())) if targets is None else tuple(targets)
                ),
                cards=tuple(cards),
            )
        if timing is Timing.DAMAGE_AFTER_TAKE and has_tag(frame, Tag.PREVENTED):
            # **伤害被减为 0 ⇒ B 族一次都不触发**（票 05 §验收 2）：时机记录照落一条
            # （M2 的【寒冰剑】路径就是这样，改它会动既有基准），但一个钩子都不派发——
            # "防止伤害"的既有语义不被"受到伤害后"的时点绕开。
            frame.data.pop("_pending_timing", None)
            return None
        # 这一次派发的对象（韬·刘备【从讨】）：逐目标的时机每次只服务一个目标，而
        # ``Hook.condition`` 拿不到派发参数 —— 帧上只有整份 ``targets``。写进帧私有槽、
        # 派发后还原（与 ``_DISPATCH_SCOPE`` 同姿态）；帧私有数据不进记录流，不读它的
        # 技能因此零行为变化。
        prior_actor = frame.data.get(MATCH_ACTOR_KEY)
        had_actor = MATCH_ACTOR_KEY in frame.data
        frame.data[MATCH_ACTOR_KEY] = actor
        try:
            control = dispatch_timing(self, frame, timing)
        finally:
            if had_actor:
                frame.data[MATCH_ACTOR_KEY] = prior_actor
            else:
                frame.data.pop(MATCH_ACTOR_KEY, None)
        if control == "stay":
            frame.data["_pending_timing"] = key
        else:
            frame.data.pop("_pending_timing", None)
        return control

    def dispatch_seam(self, frame: Frame, kind: SeamKind) -> Control:
        """派发一个**帧内接缝**（票 12 §Q12 / 票 30）：键不是 ``Timing``。"""
        return dispatch_seam(self, frame, kind)

    def provider_registered(self, skill_id: str) -> bool:
        """技能层有没有登记这条 ``skill_id`` 的**提供者回调**（票 09）。

        引擎据此把响应窗口里的 ``ActivateSkill`` 分到两条路：登记了 ⇒ 求助窗口（技能层通用
        派发）；没登记 ⇒ 引擎既有的【八卦阵】判定路径。引擎两处都不出现具体技能 id。
        """
        return provider_registered(skill_id)

    def advance_provider(self, frame: Frame, timing: Timing) -> Any:
        """重入当前正在展开的提供者（票 09）：``(control, satisfied, card)``，见 ``sgs.skills``。"""
        return advance_provider(self, frame, timing)

    def start_provider(
        self, frame: Frame, skill_id: str, actor: int, timing: Timing, *, required: str
    ) -> Any:
        """开始一次提供者展开（票 09）：``(control, satisfied, card)``，见 ``sgs.skills``。

        ``required`` = 这扇窗口**要一张什么牌**（牌名或杀族请求）：提供者据此收窄声明范围，
        引擎与技能都不必从外层帧的牌反推（濒死 / 无懈 / 借刀三扇窗口反推都会得到别的牌）。
        """
        return start_provider(self, frame, skill_id, actor, timing, required=required)

    def suspended(self, value: Any) -> bool:
        """``ask`` 的返回值是否为"已暂停等应答"哨兵。"""
        return value is SUSPEND

    def ask_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        *,
        frame: Frame | None = None,
        dying: int | None = None,
        payload: RespondContext
        | SelectContext
        | DodgeContext
        | SkillActivationContext
        | SelectOptionContext
        | ArrangeContext
        | DistributeContext
        | None = None,
        revealed: tuple[CardRef, ...] = (),
    ) -> Any:
        """构造并交付一次询问；**重入时只返回这一问自己被接受的应答**（票 02 的应答归属）。

        ``frame`` 是这次询问的**承载帧**（发起它、重入时也由它恢复的那一帧）：应答按
        ``(kind, actor, 发问帧身份)`` 归属，故带帧的窗口（`steps._dodge_window` 的【闪】
        与求桃）必须传它；缺省 ``None`` 只留给真的没有承载帧的发问。

        ``revealed`` 是**本次询问授予该参与者的牌面**（票 10 §①，缺省空 = 不授予）：
        它是通用通道，任何询问都能带（【观星】的排列与将来【遗计】的"只看不排"共用），
        随询问结束即失效、不写状态、不进 ``PlayerView``。
        """
        return self.ask(
            lambda seq: self._build_query(
                kind, actor, timing, seq, dying=dying, payload=payload, revealed=revealed
            ),
            identity=self._ask_identity(kind, actor, frame),
        )

    def ask_arrange(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        cards: tuple[CardRef, ...],
        split: int | None = None,
    ) -> Any:
        """发一次 ``ARRANGE`` 询问（票 10 §②）：把 ``cards`` 授予 ``actor`` 并请其排列。

        ``cards`` 是**自顶向下**的被授予牌面（就是这次询问的 ``revealed``）；候选 =
        全部排列 × 全部切分点（:class:`~sgs.state.ArrangeContext`）。答案按
        ``(actor, timing, 牌 id 元组)`` 帧内缓存，钩子重入时不重发（票 07 的帧内记忆姿态）。
        返回 ``ArrangeCards`` / ``SUSPEND``。

        ``split``（W6-02）：``None`` = 自由切分（全部切分点，观星与既有行为逐字相同）；
        整数 = 固定切分（只铺这一个置顶张数，来自 ``DeckTopArrange.split`` 的 ``fixed``
        档）。它**不进**答案缓存键：一帧一次排列，键的口径保持票 10 的
        ``("arrange", actor, timing, cards)``（spec S1）。
        """
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("arrange", actor, timing.value, tuple(card.card_id for card in cards))
        if answer_key in answers:
            return answers[answer_key]
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.ARRANGE,
                actor,
                timing,
                seq,
                payload=ArrangeContext(count=len(cards), split=split),
                revealed=cards,
            ),
            identity=self._ask_identity(QueryKind.ARRANGE, actor, frame),
        )
        if action is SUSPEND:
            return SUSPEND
        answers[answer_key] = action
        return action

    def ask_distribution(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        skill_id: str,
        seats: tuple[int, ...],
        max_total: int = 3,
        per_seat_max: int = 3,
    ) -> Any:
        """发一次 ``DISTRIBUTE`` 询问（huo-shenzhouyu-mobile 票 05）：计数式分配。

        ``seats`` 是可分配的座位（调用方给、座位升序）；候选 = 「每座 0..``per_seat_max``、
        合计 ≤ ``max_total``」的全量枚举。答案按 ``(skill_id, actor)`` 帧内缓存——同一帧
        重入不重发（与 :meth:`ask_option` 同一口径）。返回 ``Distribute`` / ``SUSPEND``。
        """
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("distribute", skill_id, actor)
        if answer_key in answers:
            return answers[answer_key]
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.DISTRIBUTE,
                actor,
                timing,
                seq,
                payload=DistributeContext(
                    skill_id=skill_id,
                    seats=tuple(seats),
                    max_total=max_total,
                    per_seat_max=per_seat_max,
                ),
            ),
            identity=self._ask_identity(QueryKind.DISTRIBUTE, actor, frame),
        )
        if action is SUSPEND:
            return SUSPEND
        answers[answer_key] = action
        return action

    def peek_deck(self, frame: Frame, n: int) -> tuple[CardRef, ...]:
        """**观看牌堆顶** ``n`` 张：返回牌面、**不移动**它们（票 10 §①）。

        与摸牌同一套规则（沿用 ``shuffle`` 子流、牌堆不够时把弃牌堆重洗补足、只记一条
        既有的 ``deck_shuffle``），但不落 ``card_move``——被观看的牌仍在牌堆顶，
        授予只发生在**这一次询问**的 ``revealed`` 里（"逐次授予"）。
        """
        drawn = draw_from_deck(self.state, n, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_frame_delta(frame, drawn.shuffle)
        return drawn.cards

    def start_use(
        self,
        card: CardRef,
        *,
        source: int,
        targets: tuple[int, ...],
        use_timing: Timing | None = None,
        cancel_target: tuple[int, int] | None = None,
        counts: bool = True,
        designations: tuple[int, ...] = (),
    ) -> Frame:
        """开始一次使用：记一次本回合已用、压入 USE 帧（票 07 §6）。

        ``use_timing`` 覆盖压栈步骤的 ``return_to`` 标签：默认是出牌阶段的
        ``USE_CHOOSE_TARGET``，濒死求桃里出【桃】时是 ``NEAR_DEATH_AT``
        （票 04 §4 的"从哪回来"要读得出是哪个窗口开的使用）。

        **目标顺序在这里定格**（票 15 §4）：B / C 两段都读同一个列表；缺省是“从当前回合
        角色起、逆时针”（使用者与当前回合角色不同（【借刀杀人】迫使的【杀】）时仍按当前
        回合角色起算，U-15-1）。行为表声明了 ``preserve_target_order`` 的牌（【铁索连环】）
        例外——它的特殊规则是“按使用者选择的顺序结算”（spec §5.1.2），故原样使用
        ``targets``；多目标【杀】没有这条声明，仍走通用排法。

        ``cancel_target`` 只给**响应型使用**（无懈一类的牌）用（票 11 §2）：记下这次使用指向
        "哪一层的哪个目标"（``(被挂起帧 id, 目标座位)``），由子帧随 ``use_push_resolve``
        继承进它的 RESOLVE 帧，出栈时 :meth:`_propagate_cancel` 据此把那一层标成被抵消。

        ``counts=False`` 用于**不计次数**的使用（【青龙偃月刀】的续杀，票 12）。

        计数写在**行为表声明的计数键**上（``CardBehavior.count_key``），而不是写死牌型：
        普通【杀】/ 火【杀】/ 雷【杀】因此共用一个「本回合已用【杀】」计数（M5-02）。

        ``designations``（W5-02a）是**附带指定**（借刀的 Bn）：原样带进 USE 帧，再随
        :func:`~sgs.engine.steps.use_push_resolve` 的 ``dict(frame.data)`` 继承进 RESOLVE
        帧，由 :func:`~sgs.effects.resolve_borrowed_sword` 读出。空元组不落帧键——没有
        附带指定的每一条使用与旧路径的帧载荷逐键相同。

        **【酒】的加伤在"使用"时兑现**（M5-04，spec §5.1 酒）：若来源座位正处于
        ``wine_active`` 且这次使用属于【杀】家族（:func:`~sgs.effects.is_slash`，含转化 /
        虚拟产物），就把 +1 作为本次使用的**来源侧**加伤写进帧（``deal_bonus``，随子帧
        一路传到伤害帧的 :func:`~sgs.effects.damage_amount`），并**立刻消费**状态
        （落一条 ``wine_change``）。兑现点必须是使用而不是造成伤害：被【闪】抵消 / 未造成
        伤害的那张【杀】同样消耗掉加伤，不能留给下一张。其它牌（锦囊 / 其它基本牌）与
        打出都不消费、也不加伤。引擎只问"是不是【杀】家族"，不认识"酒"这个牌名。
        """
        behavior = behavior_for(card)
        if counts:
            counts_by_type = self.state.player(source).card_use_counts
            key = behavior.count_key
            counts_by_type[key] = counts_by_type.get(key, 0) + 1
        self._note_play_phase_slash(card, source)
        deal_bonus = 0
        if is_slash(behavior) and self.state.player(source).wine_active:
            deal_bonus = WINE_DAMAGE_BONUS
        data: dict[str, Any] = {
            "card": card,
            "source": source,
            "targets": (
                tuple(targets)
                if behavior.preserve_target_order
                else order_targets(self.state, tuple(targets))
            ),
            "use_timing": use_timing,
            "from_hand": _card_from_hand(self.state, source, card),
        }
        if deal_bonus:
            data["deal_bonus"] = deal_bonus
        if designations:
            data["designations"] = tuple(designations)
        if cancel_target is not None:
            data["cancel_target"] = cancel_target
        frame = self.push_frame(
            FrameKind.USE,
            data,
            timing=use_timing if use_timing is not None else Timing.USE_CHOOSE_TARGET,
        )
        if deal_bonus:
            self.set_wine_active(frame, source, False)
        return frame

    def current_target(self, frame: Frame) -> int:
        """C 段的**当前目标**：``targets[cursor]``；空目标（响应牌）退回使用者。"""
        targets = tuple(frame.data.get("targets", ()))
        if not targets:
            return frame.data["source"]
        return targets[frame.data.get("cursor", 0)]

    def play_card(self, card: CardRef, *, source: int, timing: Timing) -> Frame:
        """开始一次**打出**：压入 PLAY 帧（L6 两个时机），**不占使用次数**（票 19 §2）。

        M6 票 03：打出也是【克己】的判据之一（"使用**或打出**过【杀】"），故这里同样记
        座位级事实；判据与 ``start_use`` 共用 :meth:`_note_play_phase_slash`。
        """
        self._note_play_phase_slash(card, source)
        return self.push_frame(
            FrameKind.PLAY,
            {
                "card": card,
                "source": source,
                "targets": (),
                "from_hand": _card_from_hand(self.state, source, card),
            },
            timing=timing,
        )

    def start_skill(self, action: ActivateSkill, *, source: int) -> Frame:
        """发动一个**主动技**：压入 SKILL 帧（票 04）。

        载荷（``picks`` / ``choices``）由候选构造时付清、原样带进帧；效果主体住技能层，
        由 SKILL 帧的唯一步骤按 ``skill_id`` 查表通用派发（``sgs.skills.activate_skill``）
        ——引擎不认识任何具体技能 id。``use_timing`` 取 ``PHASE_PLAY_IDLE``，使技能开的
        使用 / 伤害 / 回复子帧在记录流里读得出"从出牌阶段空闲时间点来"。
        """
        return self.push_frame(
            FrameKind.SKILL,
            self._skill_payload(action, source),
            timing=Timing.PHASE_PLAY_IDLE,
        )

    def start_reforge(self, action: Reforge, *, source: int) -> Frame:
        """发动重铸：压一个 **MOVE 帧**跑"弃置 → 摸一张"（M5-03 / 决策 D6）。

        重铸不是使用，故不建 USE / RESOLVE 帧（不算使用锦囊、不进 use count、不开无懈窗口）。
        MOVE 帧只做两件帧内的事：``reason = REFORGE`` 的移动（会派发「失去牌后」，钩子可
        提问并暂停）与随后的摸一张——两步走保证重铸自己的摸牌排在「失去牌后」之后。
        """
        return self.push_frame(
            FrameKind.MOVE,
            {
                "source": source,
                "targets": (),
                "card": action.card,
                "use_timing": Timing.PHASE_PLAY_IDLE,
            },
            timing=Timing.PHASE_PLAY_IDLE,
        )

    def _skill_payload(self, action: ActivateSkill, source: int) -> dict[str, Any]:
        """SKILL 帧的载荷；结构化复合动作额外带 ``activation``（W4-1）。

        ``picks`` / ``choices`` 在结构化入口里是**实体牌 id / 座位文本**（不是面内位置），
        故不能沿用"载荷原样进帧"的旧读法：这里把它们放进 ``activation``，由
        :func:`sgs.skills.flow.run_flow` 摊回逐步答案；普通骨架候选的载荷形状一字不改。
        """
        data: dict[str, Any] = {
            "source": source,
            "targets": (),
            "skill_id": action.skill_id,
            "picks": tuple(action.picks),
            "choices": tuple(action.choices),
            "use_timing": Timing.PHASE_PLAY_IDLE,
        }
        activation = self._take_structured_activation(action)
        if activation is not None:
            # 归属随载荷一起走（W4-fix-2 的跨技能泄漏修复：见 `flow._activation_belongs`）。
            data["activation"] = _activation_payload(
                activation, action.skill_id, None
            )
        return data

    def _take_structured_activation(
        self, action: ActivateSkill
    ) -> dict[str, tuple[Any, ...]] | None:
        """若 ``action`` 是**经校验器接受**的复合动作，取走它的载荷（并清掉引擎标记）。

        两个消费点（主动技的 ``_skill_payload`` 与被动技的 ``ask_skill_activation``）共用
        这一处，免得"实体牌 id / 座位"的读法各写一遍。标记按**对象身份**认领：骨架候选
        永远不是同一个对象，故不会误把普通候选当成复合动作。
        """
        if self._structured_activation is not action:
            return None
        self._structured_activation = None
        return {"picks": tuple(action.picks), "choices": tuple(action.choices)}

    def recover_from_peach(
        self, frame: Frame, card: CardRef, *, asker: int, dying: int
    ) -> None:
        """有人在濒死窗口出【桃】：把**回复结算插在濒死之下**，再走【桃】自己的使用链。

        官方 4.17："每回复 1 点体力即检查"。所以回复（内含体力写入）必须先于
        "【桃】的使用结算结束"发生——于是先压 ``RECOVER`` 帧、再把这次使用挂在它上面
        （票 21 第 3 条"回复结算插入在濒死之下，完成后回到被中断的原结算继续"）；
        反之若先走使用链、出栈后再写体力，濒死判定就会读到过期的体力。

        使用帧里的【桃】**不再**走"生效后"的效果列表：这次回复由本方法显式建立
        （否则行为表会再压一个回复帧，同一次回复被写两遍）。压栈那一步的
        ``return_to`` 标签是 ``NEAR_DEATH_AT``，记录流里因此读得出"这次使用是求桃窗口开的"，
        效果层也据此辨认出"这张牌的效果已在此落地"。
        """
        self.push_frame(
            FrameKind.RECOVER,
            {
                "source": asker,
                "targets": (dying,),
                "card": card,
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        self.start_use(
            card,
            source=asker,
            targets=(dying,),
            use_timing=Timing.NEAR_DEATH_AT,
            # 濒死自救的【酒】**不记账**（无次数限制、也不占用出牌阶段那一次）：用户
            # 2026-11-02 裁定。求桃窗口里唯一声明 ``near_death_self_only`` 的牌就是【酒】，
            # 故这里读行为表而不是写死牌名；【桃】本来无次数上限，记账与否等价。
            counts=not behavior_for(card).target_rule.near_death_self_only,
        )

    def move_used_card_to_processing(self, frame: Frame) -> Control:
        """手牌 → 处理区（``reason = USE``）：在 L4.1 / L4.2 之间（票 07 §3）。

        虚拟牌不落区域（票 12 §Q21）：纯虚拟牌（八阵的【闪】）无事发生；**转化牌**
        （丈八蛇矛的【杀】）把它的**来源实体牌**走同一条路径（手牌 → 处理区），
        故牌守恒对转化路径仍然成立。
        """
        card = frame.data["card"]
        if card.is_virtual:
            return self._move_virtual_sources(frame, to_zone=ZoneRef(Zone.PROCESSING))
        return self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def move_processing_card_to_discard(self, frame: Frame) -> None:
        """处理区 → 弃牌堆（``reason = USE``）：L5.6"使用结算结束后"（票 07 §3）。

        **只在牌仍留在处理区时才搬**：结算期间它可以被别人的技能拿去（【奸雄】把造成伤害的
        那张【杀】搬进曹操手里），那时它已经不在处理区，本次收尾就无事可做。少了这条守卫，
        ``move_card`` 会照着"牌现在在哪"把它从**曹操手里**再搬进弃牌堆——技能发动了、牌却没了
        （本轮报障）。判定路径的同名守卫见 :meth:`_finish_judge_round` 与 :meth:`discard_judge_card`。
        """
        card = frame.data["card"]
        if card.is_virtual:
            self._move_virtual_sources(frame, to_zone=ZoneRef(Zone.DISCARD))
            return
        if card.card_id not in self.state.processing:
            return
        self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def _move_virtual_sources(self, frame: Frame, *, to_zone: ZoneRef) -> None:
        """把转化牌的来源实体牌从当前位置搬到 ``to_zone``（虚拟牌自己不动）。"""
        sources = tuple(getattr(frame.data["card"], "virtual_sources", ()))
        if not sources:
            return
        self.move_in_frame(
            frame,
            self.state.refs(sources),
            to_zone=to_zone,
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def move_used_card_to_judge_zone(self, frame: Frame) -> None:
        """处理区 → 目标座位的判定区（``reason = PUT``、归属 = 使用者）。

        延时锦囊"使用当时"的落点（票 10 §7）：L4 走完就把实体牌置入目标判定区，
        L5 推迟到目标自己的判定阶段。

        **转化延时锦囊**（【国色】的方块牌当【乐不思蜀】）：判定区只装实体牌，故置入的是
        **来源实体牌**，并在 :attr:`~sgs.state.GameState.virtual_judge` 记下"它其实代表哪张
        虚拟牌"；判定阶段据此按虚拟牌结算。纯虚拟牌（无来源）置入判定区没有实体可放，跳过。
        """
        card = frame.data["card"]
        target = frame.data["targets"][0]
        if card.is_virtual:
            sources = tuple(getattr(card, "virtual_sources", ()))
            if len(sources) != 1:
                return  # 纯虚拟牌 / 多源转化：标准版没有这样的延时锦囊生产者
            self.move_in_frame(
                frame,
                self.state.refs(sources),
                to_zone=ZoneRef(Zone.JUDGE, target),
                reason=MoveReason.PUT,
                owner=frame.data["source"],
            )
            self.state.virtual_judge[sources[0]] = card
            return
        self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.JUDGE, target),
            reason=MoveReason.PUT,
            owner=frame.data["source"],
        )

    def move_used_card_to_equip_zone(self, frame: Frame) -> Control:
        """处理区 → 装备区（``reason = PUT``）；同槽已有牌则先被"顶替"出去。

        替换 = **顶替**（票 12 §Q3）：新牌 ``PUT`` 入槽、旧牌 ``LOSE`` 入弃牌堆
        （**不算弃置**，故 M3 的【枭姬】/【旋风】类"失去装备"仍能被触发）。一般"置入"
        撞已占槽位由 :func:`~sgs.effects.move_card` 的编程错误兜底直接抛，只有"使用装备牌"
        这条路径能替换，故两条移动的次序必须是**先旧后新**——先 ``PUT`` 新牌会立刻撞上
        那条兜底（"先新后旧"是票 12 §Q3 自己撤销掉的提案）。

        **返回 ``"stay"`` = 这次调用让出了**（票 06）：来源可以是旧装备那次移动的
        ``MOVE_AFTER`` 钩子压了子帧（顶替白银狮子 ⇒ ``RECOVER``），也可以是末尾 ``PUT``
        新牌那次移动的钩子让出（今天没有这样的钩子，故只是形状上的可能）。调用方必须原样
        透传，子帧才会先于新装备入槽结算——否则同一趟里接着 ``PUT`` 新牌，记录流里的次序
        就变成"新装备入场 → 回复"。

        **同槽扫描落帧私有游标，且写在搬牌之前**（``CODING_STANDARDS.md`` §3.1）：续跑会
        重入本方法，没有游标就会重扫一遍"同槽已有牌"——若那时新牌已经入了槽，扫到的就是
        **玩家刚装上的那张**，于是把这次顶替又做一遍（旧牌进弃牌堆两次、新牌跟着离场）。
        """
        card = frame.data["card"]
        seat = frame.data["source"]
        if card.is_virtual:  # 虚拟装备不是牌（票 07 §6）：装备区只放实体牌
            return None
        if frame.data.get(_EQUIP_SLOT_SCANNED) != card.card_id:
            # 同槽扫描的游标在**压帧之前**落位（§3.1）：这次扫描只做一次，续跑只补搬还没搬完的步。
            frame.data[_EQUIP_SLOT_SCANNED] = card.card_id
            for card_id in list(self.state.player(seat).equip):
                if self.state.card(card_id).slot is card.slot:
                    if (
                        self.move_in_frame(
                            frame,
                            (self.state.card(card_id),),
                            to_zone=ZoneRef(Zone.DISCARD),
                            reason=MoveReason.LOSE,
                            owner=seat,
                        )
                        == "stay"
                    ):
                        return "stay"
        return self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.EQUIP, seat),
            reason=MoveReason.PUT,
            owner=seat,
        )

    def put_equipment_card(
        self, frame: Frame, *, holder: int, card: CardRef, actor: int
    ) -> Control:
        """把一张**装备牌**置入 ``holder`` 的装备区（M6 票 17 的界【结姻】）。

        **不替换**：目标同槽已有牌 ⇒ 这是非法调用（牌面写的是"**置入**"，与"使用装备牌"
        同一条口径，空槽才放得下）。合法候选由技能侧的选择面保证
        （`_jie_jieyin_equipment_cards` 只留空槽那张），故这里用断言守住不变量，而不是
        静默顶替——用户裁定：不能替换已有装备，同槽已有牌就是"非法选项"（票 23）。

        来源可以是**手牌，也可以是发动者自己的装备区**（"一张装备牌"含装备区，票 23）；
        归属 / 接收者是 ``holder``——牌面写的是"置入**其**装备区"，跨座位的置入因此走既有的
        移动原因。

        返回 ``"stay"`` = 这次移动内联派发了"失去牌后"并挂起（发动者自己的【枭姬】），
        调用方原样透传；重入时 :meth:`move_in_frame` 的帧内幂等守卫认得出这次移动已经搬完。
        """
        if card.card_id in self.state.player(holder).equip:
            # **幂等**：这次置入已经搬完，只因内联派发（发动者自己的【枭姬】）挂起而重入 —
            # 目标槽位现在当然"已被占"，那是本函数刚刚放进去的那张牌。
            return None
        occupied = [
            card_id
            for card_id in self.state.player(holder).equip
            if self.state.card(card_id).slot is card.slot
        ]
        assert not occupied, (
            f"置入装备区不能替换已有装备：座位 {holder} 的 {card.slot} 槽已有 {occupied}"
        )
        return self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.EQUIP, holder),
            reason=MoveReason.PUT,
            owner=holder,
            actor=actor,
        )

    # -- 打出帧（票 27）：手牌 → 处理区 → 弃牌堆 --------------------------------

    def move_played_card_to_processing(self, frame: Frame) -> None:
        """打出：手牌 → 处理区（``reason = USE``）——L6 的第一段移动（转化牌同姿态）。"""
        card = frame.data["card"]
        if card.is_virtual:
            return self._move_virtual_sources(frame, to_zone=ZoneRef(Zone.PROCESSING))
        return self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def move_played_card_to_discard(self, frame: Frame) -> None:
        """打出：处理区 → 弃牌堆（``reason = USE``）——打出没有使用结算。"""
        card = frame.data["card"]
        if card.is_virtual:
            self._move_virtual_sources(frame, to_zone=ZoneRef(Zone.DISCARD))
            return
        if card.card_id not in self.state.processing:
            return
        self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    # -- 票 27 的效果面：移动 / 摸牌 / 选择面 / 响应窗口 -------------------------

    def emit_frame_delta(self, frame: Frame, delta: Any) -> None:
        """效果回调的公共记录口：把一条纯 delta 挂到当前帧上。"""
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)

    def draw_for_hand(self, frame: Frame, seat: int, n: int) -> bool | str:
        """给 ``seat`` 摸 ``n`` 张（沿用 ``shuffle`` 子流）；短摸 ⇒ 平局并终止。

        与摸牌阶段 / 奖惩摸牌同口径（票 14）：重洗只发一条 ``deck_shuffle``；
        返回 ``False`` 表示两堆皆空、已判平局。

        **摸牌也算「获得牌」（票 24 的 Q1）之后，摸牌会内联派发"获得牌后"的钩子并可能挂起**
        （【清俭】在别人的回合里摸牌、或自己在出牌阶段摸牌）。挂起时本方法返回 ``"stay"``
        —— 调用方**必须**把它原样返回（像 :func:`_lianying` 那样带游标续跑），否则引擎会
        在带待答询问的状态下继续搬牌，那条应答随后被下一个询问错配（随机局 seed=663 复现：
        连营逐座摸牌里触发清俭，答复被出牌阶段的询问吃掉，最终按主动技发动报错）。
        """
        drawn = draw_from_deck(self.state, n, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_frame_delta(frame, drawn.shuffle)
        if drawn.cards:
            control = self.move_in_frame(
                frame,
                drawn.cards,
                to_zone=ZoneRef(Zone.HAND, seat),
                reason=MoveReason.DRAW,
                owner=seat,
                actor=seat,
            )
            if control == "stay":
                return "stay"
        if len(drawn.cards) < n:
            self.check_game_end(draw_reason="both_decks_empty")
            self.finish()
            return False
        return True

    def draw_open(self, frame: Frame, n: int, *, owner: int | None) -> tuple[CardRef, ...]:
        """亮出牌堆顶 ``n`` 张到处理区（【五谷丰登】的执行动作）。

        **不判平局**：牌不够是"执行动作未完整执行"，由调用方终止这次使用结算
        （`A/card_scroll.txt:194`）；重洗仍沿用 ``shuffle`` 子流。
        """
        drawn = draw_from_deck(self.state, n, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_frame_delta(frame, drawn.shuffle)
        if drawn.cards:
            self.move_in_frame(
                frame,
                drawn.cards,
                to_zone=ZoneRef(Zone.PROCESSING),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
        return drawn.cards

    def has_zone_cards(self, seat: int) -> bool:
        """``seat`` 的三区里是否至少有一张牌（拆 / 顺的第 0 层前提）。

        **不含**武将牌上的牌（M6 票 10）：那些是"移出游戏的牌"，不是拆 / 顺的处理对象
        （`三国杀详细规则.txt` §十五 15.2 明文）。
        """
        player = self.state.player(seat)
        return bool(player.hand or player.equip or player.judge)

    # -- 武将牌上的牌（M6 票 10；spec D14）----------------------------------

    def place_on_hero_card(
        self,
        frame: Frame | None,
        *,
        owner: int,
        skill_id: str,
        holder: int,
        cards: Sequence[CardRef],
    ) -> Control:
        """把一批牌置于**武将牌上**（唯一写入口；M6 票 10 / spec D14）。

        ``owner`` = 放置技能实例的持有者（这批牌的归属、也是回合末归还的收件人）；
        ``skill_id`` = 放置技能的 id（区键的第二维）；``holder`` = **武将牌的主人**
        （扣在谁身上，可跨座位）。区 = （holder 座位, ``owner:skill_id``），第一次放置时
        建区（同键复用；空区保留，规则 15.2：失去技能不影响武将牌上的牌）。

        **放置类技能必须在代码里登记**（``SkillDef.places_on_hero_card``）：未登记的技能
        走到这里即编程错误（不是静默造区）。搬牌走 :meth:`move_in_frame`，故照常记
        ``card_move``、派发"失去牌后"（【谦逊】把牌放上武将牌**就是**失去手牌，会点亮
        【连营】）。

        空的一批牌 = 无动作、**不建区**（没有牌就没有可归还的东西，也不该留下一个空区
        让"这条技能发动过"被误读）。返回 ``"stay"`` 表示这次移动内联的钩子提问挂起了。
        """
        definition = CATALOG.get(skill_id)
        if definition is None or not definition.places_on_hero_card:
            raise ValueError(
                f"{skill_id!r} 不是登记在案的放置类技能：往武将牌上放牌必须先声明 "
                "SkillDef.places_on_hero_card 并在数据里写三项声明"
            )
        if any(card.is_virtual for card in cards):
            raise ValueError("虚拟牌不落任何区域，不能置于武将牌上")
        if not cards:
            return None
        key = hero_card_zone_key(owner, skill_id)
        player = self.state.player(holder)
        if key not in player.hero_cards:
            player.hero_cards[key] = HeroCardZone(skill_id=skill_id, owner=owner)
        return self.move_in_frame(
            frame,
            tuple(cards),
            to_zone=ZoneRef(Zone.HERO, holder, key),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )

    def return_hero_card_zones(self) -> Control:
        """把**归还时机 = 当前回合结束时**的放置区整批还给放置技能的持有者（M6 票 10）。

        逐区一条 ``card_move``（一次移动只能有一个来源区），与死亡清区 / 弃牌阶段同姿态：
        帧外移动走 :meth:`move_in_frame`，故照常派发"失去牌后"（归还是**获得**牌，可能
        点亮【清俭】一类"获得牌后"的钩子）；钩子提问挂起时返回 ``"stay"``，由回合步
        重新进入本方法——已经还完的区是空区，天然跳过，故不会重搬。

        判据读**数据声明**（:func:`~sgs.data.hero_placement_decl`），不读技能定义：
        ``return_timing = TURN_END`` 且 ``extra_effect = False`` 才由引擎代还；声明为
        ``NEVER`` / 有附加效果的区留给技能自己（M6 无此类载体）。数据里没有声明的区
        （只可能来自测试构造）**跳过、不动**——加载期保证真实放置类技能都有声明。

        持有者已阵亡（跨座位区、宿主还活着）时改为置入弃牌堆：不给死者手里塞牌，也不
        破坏"阵亡者区域为空"的不变量。
        """
        for player in self.state.players:
            for key in tuple(player.hero_cards):
                zone = player.hero_cards[key]
                if not zone.cards:
                    continue
                decl = hero_placement_decl(zone.skill_id, pack_id=self.state.pack_id)
                if (
                    decl is None
                    or decl.return_timing is not PlacementReturn.TURN_END
                    or decl.extra_effect
                ):
                    continue
                alive = self.state.player(zone.owner).alive
                control = self.move_in_frame(
                    None,
                    self.state.refs(tuple(zone.cards)),
                    to_zone=(
                        ZoneRef(Zone.HAND, zone.owner)
                        if alive
                        else ZoneRef(Zone.DISCARD)
                    ),
                    reason=MoveReason.PUT if alive else MoveReason.LOSE,
                    owner=zone.owner,
                    actor=zone.owner,
                )
                if control == "stay":
                    return "stay"
        return None

    def selection_face(
        self, seat: int, zones: tuple[Zone, ...], *, reveal_hand: bool = False
    ) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
        """按 ``zones`` 给出的区（固定顺序）铺开 ``seat`` 的选择面。

        他人手牌是**隐藏槽**（只有位置）、装备 / 判定区是可见槽；``reveal_hand=True``
        用于"挑牌者就是手牌持有者本人"（【贯石斧】成本、【雌雄双股剑】弃牌、
        【青龙偃月刀】挑【杀】），此时自己的手牌是**可见槽**（票 19 §3）。
        隐藏槽的位置用新增的 ``select`` 子流打乱（票 19 §8 / §12），使"选了第几个"
        不携带手牌顺序信息；返回的 ``card_ids`` 是**引擎私有**的位置 → 实体牌映射，
        不进询问记录，故不泄露牌面身份。
        """
        player = self.state.player(seat)
        slots: list[Slot] = []
        card_ids: list[int] = []
        for zone in zones:
            if zone is Zone.HAND:
                if reveal_hand:
                    for card_id in player.hand:
                        slots.append(
                            Slot(ref=ZoneRef(Zone.HAND, seat), card=self.state.card(card_id))
                        )
                        card_ids.append(card_id)
                    continue
                order = list(range(len(player.hand)))
                self._select.shuffle(order)
                for index in order:
                    slots.append(Slot(ref=ZoneRef(Zone.HAND, seat), card=None))
                    card_ids.append(player.hand[index])
            elif zone is Zone.EQUIP:
                for card_id in player.equip:
                    slots.append(
                        Slot(ref=ZoneRef(Zone.EQUIP, seat), card=self.state.card(card_id))
                    )
                    card_ids.append(card_id)
            elif zone is Zone.JUDGE:
                for card_id in player.judge:
                    slots.append(
                        Slot(ref=ZoneRef(Zone.JUDGE, seat), card=self.state.card(card_id))
                    )
                    card_ids.append(card_id)
            else:  # pragma: no cover - 选择面只对逐座位的三个区有意义
                raise ValueError(f"{zone.value} 不是可选择的逐座位区域")
        return tuple(slots), tuple(card_ids)

    def shuffle_positions(self, order: list[int]) -> None:
        """把"隐藏槽的位置序"就地打乱（票 19 §8 / §12 的 ``select`` 子流）。

        :meth:`selection_face` 铺他人手牌时用它，使"选了第几个"不携带手牌顺序信息。
        技能层自铺选择面（【反馈】的手牌隐藏槽 + 装备可见槽）时也必须走这里——随机来源
        只能有一个（``tests/test_source_discipline.py`` §1），技能层不得自己抽随机数。
        """
        self._select.shuffle(order)

    def attack_range_targets(self, seat: int) -> tuple[int, ...]:
        """``seat`` 攻击范围内的其他存活座位（【借刀杀人】迫使的用牌目标候选）。"""
        return targets_within_attack_range(self.state, seat)

    def weapon_of(self, seat: int) -> CardRef | None:
        """``seat`` 装备区里的武器牌；没有则 ``None``（【借刀杀人】的"交出武器"）。"""
        for card_id in self.state.player(seat).equip:
            card = self.state.card(card_id)
            if card.slot is SlotKind.WEAPON:
                return card
        return None

    def ask_respond(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        cause: WindowCause,
        required: str | None = None,
        target: int | None = None,
        window_frame: int = 0,
        mode: RespondMode = RespondMode.PLAY,
        use_targets: tuple[int, ...] = (),
        providers_used: tuple[str, ...] = (),
        zones: tuple[Zone, ...] = (Zone.HAND,),
        declared: bool = False,
        rejudge_offers: tuple[RejudgeOffer, ...] = (),
    ) -> Any:
        """向 ``actor`` 发一次 ``RESPOND`` 询问；重入直接返回已存应答（票 19 §8）。

        ``mode = USE`` 时候选是"使用某张牌"：``use_targets`` 非空时每个目标一个候选
        （借刀杀人迫使的【杀】），**为空时是无目标的响应使用**（无懈一类的牌，目标由
        ``window_frame`` + ``target`` 承载）；``mode = PLAY`` 是打出（无目标）。

        ``providers_used`` 供"需要【闪】"窗口过滤已问过的装备提供者（票 10 §5.5）。
        ``zones`` = 候选牌取自应答者的哪几个区（缺省手牌）；改判窗口按
        :func:`~sgs.skills.rejudge_card_zones` 传入（界【鬼才】含装备区，M6 票 14）。
        ``declared`` 只服务**收集式**窗口（票 03）：赢家已定（首个声明者）时提供者闭嘴
        ——它的产物要扣置一张手牌，赢家已定时不会进结算。
        """
        payload = RespondContext(
            cause=cause,
            required=required,
            target=target,
            window_frame=window_frame,
            mode=mode,
            use_targets=tuple(use_targets),
            providers_used=tuple(providers_used),
            zones=tuple(zones),
            declared=declared,
            rejudge_offers=tuple(rejudge_offers),
        )
        return self.ask(
            lambda seq: self._build_query(
                QueryKind.RESPOND, actor, timing, seq, payload=payload
            ),
            identity=self._ask_identity(QueryKind.RESPOND, actor, frame),
        )

    def respond_candidates(
        self,
        actor: int,
        *,
        cause: WindowCause,
        required: str | None = None,
        target: int | None = None,
        mode: RespondMode = RespondMode.PLAY,
        use_targets: tuple[int, ...] = (),
        providers_used: tuple[str, ...] = (),
        rejudge_offers: tuple[RejudgeOffer, ...] = (),
    ) -> tuple[Action, ...]:
        """**预览**一次响应窗口的候选（W5-02b 的 auto-b）：不问、不记、不耗序号。

        读的是与 :meth:`ask_respond` **同一个**候选生成器（``legal_actions_for``）——"此刻
        有没有 a"因此不会与"窗口真发出来时给什么"漂移。补充说明两点：

        - **只读状态**：不压帧、不搬牌、不抽随机数（转化候选的生成也全是纯谓词）；
        - ``answers_seq`` 取 ``self._seq + 1``——这正是随后那次 ``ask`` 会拿到的序号；
          预览的产物只是被丢弃的候选，序号在这里不承载语义，但也不造出一个"未来值"。
        """
        payload = RespondContext(
            cause=cause,
            required=required,
            target=target,
            window_frame=0,
            mode=mode,
            use_targets=tuple(use_targets),
            providers_used=tuple(providers_used),
            rejudge_offers=tuple(rejudge_offers),
        )
        return legal_actions_for(
            self.state,
            actor,
            QueryKind.RESPOND,
            answers_seq=self._seq + 1,
            payload=payload,
        )

    def accepted_selection(
        self, frame: Frame, *, owner: Any, key: str
    ) -> tuple[int, ...] | None:
        """**本帧已接受**的这一问的答案；还没问过 ⇒ ``None``（重入的"先认答案"入口）。

        与 :meth:`ask_selection` 的帧内缓存**同键同源**（:func:`_selection_answer_key`），
        故调用方可以在"按活状态算池 / 铺面"**之前**先问一句"这一步是不是已经答过了"。
        步骤流水线（:func:`sgs.skills.flow.run_flow` 的各步执行器）正是靠它把"重入时
        现算池"从续跑路径上摘掉的：``effect`` 已经改过的状态不再参与这一步的裁决
        —— 本 effort 票 01 的类 bug 是【攻心】把目标最后一张手牌搬进牌堆后，按活状态
        重算的座位池已经空了，整条流被误判成 ``"cancel"``（"零副作用"）。

        返回空元组 ``()`` 与返回 ``None`` 是**两件事**：前者是"问过，答案是'一张没选'"
        （可选 ``Pass`` / 刚好被缓存的空池），后者是"这一问还没发生"。
        """
        answers = frame.data.get("ask_answers")
        if not answers:
            return None
        return answers.get(_selection_answer_key(owner, key))

    def ask_selection(
        self,
        frame: Frame,
        *,
        owner: Any,
        key: str,
        actor: int,
        timing: Timing,
        spec: SelectionSpec,
        build_face: Any = None,
        revealed: tuple[CardRef, ...] = (),
        provide_type: str | None = None,
        face: tuple[tuple[Slot, ...], tuple[int, ...]] | None = None,
        ask_when_empty: bool = False,
    ) -> Any:
        """发一次 ``SELECT_CARDS`` 询问；**选择面只构造一次**（帧内记忆，重入复用）。

        ``build_face() -> (slots, card_ids)`` 会消耗一次 ``select`` 子流的随机数，
        故绝不能因重入而重跑；答案回来后把该位置解析成实体 ``card_id`` 元组返回。
        池为空时缺省**不构造询问**、直接返回空元组（票 19 §6）；``ask_when_empty=True``
        则照发一条**空选择面**的询问——调用方要的是"没有合法牌也要让对手看见我做了
        选择"的信息面（【铁骑】的"没有同花色牌"），故这条询问只有 ``Pass``。

        **``owner`` 是这次询问的**询问位置**（票 22，**必填**；不给在发问前就
        ``TypeError``）**：帧内缓存（答案与自建的面）按 ``(owner, key)`` 分桶，于是"这一步
        问的答案"与"哪一步问的"**绑在一起**。``owner`` 与 ``key`` 是两件事 —— ``key`` 是
        这一步的**询问名**（也是面 / 记录流里的键），``owner`` 是**询问位置**（逐处唯一）：

        - 步骤流水线（:func:`~sgs.skills.flow.run_flow`）给 ``(flow.name, index)`` ——
          重入时"从头重跑一遍步骤"，位置索引天然稳定；
        - 手写回调各用一个模块级常量（:data:`~sgs.skills.flow.AskIdentity` 的两个形状）。

        术语见 ``GLOSSARY.md`` 的**询问标识**；这里两个名字都与**归属**（移动报文里那个
        冻结的 ``owner`` 字段）无关，也与座位意义上的持有者无关。

        本仓不配 typechecker，故用运行期必填参数而不是类型系统来挡"漏给位置"。票 21 的
        ``KeyError`` 就是两个步骤共用 ``key`` 导致的：后者的询问命中前者留下的答案，
        取回的 id 与**自己那份面**不同源。

        面的真源**只有一处**（评审 Standards finding 2）：**恰好**给一个 ——

        - ``build_face``：调用方给惰性构造器，面缓存在这里的
          ``frame.data["asks"][(owner, key)]``；
        - ``face``：调用方**自己**已经缓存了面（如 :func:`~sgs.skills.flow._flow_step_provide`
          的 ``provide_faces[(owner, key)]``），这里直接用它、**不再另存一份**。两份缓存曾让
          "面"与"答案"可能分母不同（票 21 的 ``KeyError`` 就是它），现在结构上不可能：
          同一帧里这个询问只认一个面。

        答案也按 ``(owner, key)`` 帧内缓存（票 30）：钩子可以跨多次重入，重入时既不重发
        询问、也不重算选择面。``spec.optional = True`` 时可选 ``Pass`` 表示"不再选"
        （票 04 的顺序多轮用它收尾），返回空元组。

        ``revealed`` 是本次询问**授予 ``actor`` 的牌面**（票 05 的【遗计】：选牌面位置上
        铺的是"牌堆顶 2 张"这样的实体牌，但牌面本身本不该进任何视角）。通道与
        :meth:`ask_query` 完全同一条（票 10 §①）。
        """
        if (build_face is None) == (face is None):
            raise TypeError(
                "ask_selection 必须**恰好**给一个面来源：build_face（这里缓存）或 face（调用方缓存）"
            )
        identity = (owner, key)
        answer_key = _selection_answer_key(owner, key)
        accepted = self.accepted_selection(frame, owner=owner, key=key)
        if accepted is not None:
            return accepted
        answers = frame.data.setdefault("ask_answers", {})
        owned_store: dict[Any, Any] | None = None
        if face is None:
            owned_store = frame.data.setdefault("asks", {})
            if identity not in owned_store:
                owned_store[identity] = build_face()
            slots, card_ids = owned_store[identity]
        else:
            slots, card_ids = face
        if not slots and not ask_when_empty:
            answers[answer_key] = ()
            return ()
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.SELECT_CARDS,
                actor,
                timing,
                seq,
                payload=SelectContext(
                    spec=spec,
                    slots=slots,
                    skill_id=frame.data.get("skill_id"),
                    provide_type=provide_type,
                ),
                revealed=revealed,
            ),
            identity=self._ask_identity(QueryKind.SELECT_CARDS, actor, frame),
        )
        if action is SUSPEND:
            return SUSPEND
        if isinstance(action, Pass):
            # ``SelectionSpec(optional=True)`` 的"够了 / 不选了"出口（票 04 的顺序多轮）：
            # 空元组与"池为空"同形，调用方据此收尾。
            if owned_store is not None:
                owned_store.pop(identity, None)
            answers[answer_key] = ()
            return ()
        assert isinstance(action, SelectCards)
        if owned_store is not None:
            owned_store.pop(identity, None)
        result = tuple(card_ids[index] for index in action.picks)
        answers[answer_key] = result
        return result

    def ask_skill_activation(
        self,
        frame: Frame,
        instance: SkillInstance,
        timing: Timing,
        plan: ActivationPlan,
        *,
        key: str | None = None,
    ) -> Any:
        """发一次 ``SKILL_ACTIVATION`` 询问（票 19 §5 / 票 30；W6-06a 收窄）。

        候选恒为**一条裸 ``ActivateSkill``**：成本 / 载荷由发动后的 ``SkillFlow`` 步骤问，
        故这里不再把面内位置烘进候选（`ActivationPlan` 的载荷字段已删）。``plan`` 只决定
        ``optional``；``available=False`` 由 ``_dispatch`` 在调用前挡掉。答案按
        ``(skill_id, key)`` 帧内缓存，重入不重发（``key`` 让同一时机上的真钩子与帧内
        接缝不共用缓存槽）。询问模型仍带 ``min_picks=0 / max_picks=0 / slots=()`` 的
        冻结报文（A-4 另立票；15 份录像逐字节）。

        **应答者默认是技能持有者，但可以由计划指定**（M6 票 14 的 ``plan.actor``）：
        【护驾】②的"其可以令你摸一张牌"里决定者是**使用 / 打出【闪】的那个角色**，收益者
        是技能持有者（主公）。询问因此发给 ``plan.actor``；``preactivation`` 清单也按
        应答者取（清单是"这一问交给谁"的一部分，不是持有者的属性）。
        """
        actor = instance.owner if plan.actor is None else plan.actor
        # 帧外时机的询问：记下承载帧，供 ``submit`` 的复合动作校验器读同一份上下文（票 20）。
        self._activation_frame = frame
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("activate", instance.skill_id, key or str(timing))
        if answer_key in answers:
            return answers[answer_key]
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.SKILL_ACTIVATION,
                actor,
                timing,
                seq,
                payload=SkillActivationContext(
                    skill_id=instance.skill_id,
                    optional=plan.optional,
                    min_picks=0,
                    max_picks=0,
                    slots=(),
                    preactivation=(
                        # 清单只随**已翻预发动**的技能下发（与 PLAY_PHASE 删骨架同一个开关）。
                        # 界面按这同一个开关找入口（`preactivation.js:preactivatedSkill`）：
                        # 清单在、开关没开 ⇒ 浮动按钮画不出、被动技按钮又不接 click ⇒ 这一问
                        # 没有任何入口（票 23 报障）。没翻的技能改走直提态，步骤照旧逐步问。
                        preactivation_for(
                            self.state,
                            actor,
                            instance.skill_id,
                            timing=timing,
                            frame=frame,
                        )
                        if instance.skill_id in preactivated_skill_ids()
                        else ()
                    ),
                ),
            ),
            identity=self._ask_identity(QueryKind.SKILL_ACTIVATION, actor, frame),
        )
        if action is SUSPEND:
            return SUSPEND
        if isinstance(action, ActivateSkill):
            activation = self._take_structured_activation(action)
            if activation is not None:
                # W4-1：复合动作的 picks 是**实体牌 id**，不是面内位置；原样交给
                # ``run_flow`` 摊回逐步答案。W4-fix-2：归属随载荷一起走——同一帧上后续
                # 别的技能的流水线不得读它（`flow._activation_belongs`）。
                frame.data["activation"] = _activation_payload(
                    activation, instance.skill_id, timing
                )
        answers[answer_key] = action
        return action

    def accepted_option(
        self, frame: Frame, *, skill_id: str, actor: int
    ) -> Any | None:
        """**本帧已接受**的这一问的答案；还没问过 ⇒ ``None``（与 :meth:`accepted_selection` 同姿态）。

        返回值是 :class:`~sgs.state.SelectOption` **或** :class:`~sgs.state.Pass` —— 后者
        也是"答过了"（"这一步被取消"是**已接受**的裁决），故调用方必须按"是不是 ``Pass``"
        分流，不能按真假值判，也不能把它当成"没问过"再问一遍。

        与 :meth:`ask_option` 的帧内缓存同键同源（:func:`_option_answer_key`，含
        ``_DISPATCH_SCOPE``）：续跑的"先认答案"与发问的"存答案"认的永远是同一个槽。
        """
        answers = frame.data.get("ask_answers")
        if not answers:
            return None
        return answers.get(_option_answer_key(frame, skill_id, actor))

    def ask_option(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        skill_id: str,
        options: tuple[str, ...],
        optional: bool = False,
        revealed: tuple[CardRef, ...] = (),
    ) -> Any:
        """发一次 ``SELECT_OPTION`` 询问（票 12 §Q6 / 票 30）：选项闭集。

        缺省**强制选择**（既有消费者的口径）；``optional=True`` 时候选里多一条 ``Pass``，
        并把它**原样返回**给调用方——"这一步可以取消"的判据由调用方自己定（票 00 / N4）。

        ``revealed``（韬·刘备【义起】的加法）：本次询问**授予 ``actor`` 的牌面**，与
        :meth:`ask_selection` 的同名参数共用同一条授予通道（票 10 §①）。投影按"选项询问
        带授予牌面"这个**形状**开选择型弹窗：牌面画在弹窗里（只读、仅本人可见）、选项按钮
        也在同一弹窗 —— "先看到这几张牌，再选一项"因此是**一问**，不会被拆成两步。

        答案按 ``(skill_id, actor)`` 帧内缓存，重入不重发。
        """
        answers = frame.data.setdefault("ask_answers", {})
        # M6 票 20：键带上**这次派发的范围**（``_DISPATCH_SCOPE``）。同一帧里同一技能的第二次
        # 触发是另一次派发、另一个范围键，故不会复用上一次的选择；同一次触发的挂起重入
        # 范围键不变，仍然命中缓存（重入不重发）。键只此一处拼（:func:`_option_answer_key`），
        # ``accepted_option`` 读的是同一个槽。
        answer_key = _option_answer_key(frame, skill_id, actor)
        if answer_key in answers:
            return answers[answer_key]
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.SELECT_OPTION,
                actor,
                timing,
                seq,
                payload=SelectOptionContext(
                    skill_id=skill_id, options=options, optional=optional
                ),
                revealed=revealed,
            ),
            identity=self._ask_identity(QueryKind.SELECT_OPTION, actor, frame),
        )
        if action is SUSPEND:
            return SUSPEND
        assert isinstance(action, (SelectOption, Pass))
        answers[answer_key] = action
        return action

    def open_skill_judgment(self, frame: Frame, subject: int, skill_id: str) -> None:
        """技能触发的一次**惰性判定**：压 JUDGE 帧（票 10 §1 / §5.3）。

        ``after`` 指向通用的判定后果 op；``skill_id`` + ``activation_parent`` 让后果
        把"红 / 黑"写回发起判定的那个帧（八阵出闪的成败）。
        """
        self.push_frame(
            FrameKind.JUDGE,
            {
                "subject": subject,
                "targets": (subject,),
                "source": frame.data.get("source"),
                "card": frame.data.get("card"),
                "judge_card": None,
                "use_timing": frame.data.get("use_timing"),
                "after": "after_skill_judgment",
                "skill_id": skill_id,
                "activation_parent": frame.frame_id,
            },
            timing=Timing.JUDGE_ON,
        )

    def note_skill_judgment(self, frame: Frame) -> None:
        """判定后果：把"红 = 成功"写回发起判定的帧（票 10 §5.3）。

        M6 票 01 起同时回写**判定牌本身**（:data:`~sgs.skills.SKILL_JUDGMENT_CARD_KEY`）：
        【刚烈】要读"不为红桃"——方块是红色但不终止，布尔读不出来。既有读
        ``skill_result`` 的两条（【八卦阵】/【铁骑】）不受影响；判定牌在写入前已进弃牌堆，
        回写的是不可变的 ``CardRef`` 事实，不是位置。

        M6 票 02 起**发起帧不一定是栈上的结算帧**：【洛神】是**阶段体**（准备阶段）里的
        多轮判定，而阶段体的那个帧住 ``Engine._turn_data``、从不压栈（票 10 的"帧外阶段体"）。
        ``frame_id`` 因此对阶段体帧恒为 0，唯一可用的身份是"它正在跑哪个技能"
        （:data:`~sgs.skills.RUNNING_SKILL_KEY`，派发环在调回调前写入），与判定帧自己带的
        ``skill_id`` 对照。找不到任何发起帧才抛错——那是结算栈真被破坏了。
        """
        parent_id = frame.data.get("activation_parent")
        judge_card = frame.data.get("judge_card")
        red = judge_card is not None and color_of_suit(judge_card.suit) is Color.RED
        for candidate in reversed(self.stack.frames):
            if candidate.frame_id == parent_id:
                candidate.data["skill_result"] = red
                candidate.data[SKILL_JUDGMENT_CARD_KEY] = judge_card
                return
        skill_id = frame.data.get("skill_id")
        for value in self._turn_data.values():
            if (
                isinstance(value, Frame)
                and value.frame_id == parent_id
                and value.data.get(RUNNING_SKILL_KEY) == skill_id
            ):
                value.data["skill_result"] = red
                value.data[SKILL_JUDGMENT_CARD_KEY] = judge_card
                return
        raise RuntimeError(f"判定发起帧 {parent_id} 不在栈上；结算栈被破坏")

    def apply_timing_ops(self, frame: Frame, timing: Timing) -> Any:
        """跑绑定在这个时机上的调用型步骤（票 27 的 ``CardBehavior.timing_ops``）。

        与 :meth:`apply_effects` 同一派发姿态：只按名查 :data:`OPS`，引擎不出现效果名。
        返回值透传 ``Control``（供未来会提问的时机 op 让出控制权）。
        """
        for op_timing, step in behavior_for(frame.data["card"]).timing_ops:
            if op_timing is not timing:
                continue
            op = OPS.get(step.op)
            if op is None:
                raise NotImplementedError(f"未登记的效果原语 {step.op!r}（票 09 §4 的 OPS 表）")
            control = op(self, frame)
            if control is not None:
                return control
        return None

    # -- 判定帧（票 26）：取判定牌 / 入处理区 / 入弃牌堆 / 后果 op -----------------

    def draw_judge_card(self, frame: Frame) -> bool:
        """判定取牌：复用摸牌原语、沿用 ``shuffle`` 子流、**不算摸牌**（票 10 §2）。

        重洗时就地先发**一条** ``deck_shuffle``、不另发 ``card_move``（与摸牌阶段同姿态）；
        短摸（两堆皆空）⇒ 按"任何短摸即平局"判 ``both_decks_empty`` 并终止，返回 ``False``。
        """
        drawn = draw_from_deck(self.state, 1, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_delta(drawn.shuffle, frame_id=frame.frame_id, depth=self.stack.depth)
        if not drawn.cards:
            self.check_game_end(draw_reason="both_decks_empty")
            self.finish()
            return False
        frame.data["judge_card"] = drawn.cards[0]
        return True

    def move_judge_card_to_processing(self, frame: Frame) -> None:
        """判定牌 牌堆 → 处理区（``reason = JUDGE``、归属 = 被判定者）。"""
        subject = frame.data["subject"]
        self.move_in_frame(
            frame,
            (frame.data["judge_card"],),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.JUDGE,
            owner=subject,
            actor=subject,
        )

    def substitute_judge_card(
        self, frame: Frame, *, seat: int, new_card: CardRef, skill_id: str
    ) -> Control:
        """改判（feng-zhangjiao 票 02）：压一个 **REJUDGE 事件帧**，把新牌变成判定牌。

        与旧实现的裁决**逐字段相同**（新牌 → 处理区 ``reason = JUDGE``、归属 = 被判定者、
        原牌去向由 :attr:`~sgs.skills.catalog.SkillDef.rejudge_original_to` 回答），差别只在
        **形状**：两段移动住在一个独立的 ``FrameKind.REJUDGE`` 子帧里，故记录流读得出
        "这一次改判"是一次独立事件，而不是 JUDGE 帧里的两条散装移动；动作构造子也不再是
        ``PlayCard``（用户裁定：改判不是打出）。

        帧私有 ``judge_card`` 在这里**先换成新牌**：窗口要等这个子帧出栈后才重开
        （``judge_before_effect`` 重入），期间没有别的读者，故"先写后搬"与旧实现等价。

        原判定牌的身份在**压帧时定格**（帧私有 ``old``）：挂起重入由帧自己的步骤续跑
        （``steps.rejudge_substitute``），不会把已经换成新牌的 ``judge_card`` 误当原牌
        ——code-review C2 的那条缺陷在帧边界上不再可能发生。
        """
        old = frame.data["judge_card"]
        original_to = rejudge_original_to(self.state, seat, skill_id)
        frame.data["judge_card"] = new_card
        self.push_frame(
            FrameKind.REJUDGE,
            {
                "subject": frame.data["subject"],
                "targets": (frame.data["subject"],),
                "source": seat,
                "card": frame.data.get("card"),
                "use_timing": frame.data.get("use_timing"),
                "seat": seat,
                "skill_id": skill_id,
                "old": old,
                "new": new_card,
                "original_to": original_to.value,
            },
            timing=Timing.JUDGE_CARD_BEFORE_EFFECT,
        )
        return "stay"

    def discard_judge_card(self, frame: Frame) -> None:
        """判定牌 处理区 → 弃牌堆（``reason = JUDGE``、归属 = 被判定者）：判定结算结束。

        判定牌**已被"生效后"的效果获得**（【天妒】等）时不再入弃牌堆（规则 11.2
        "若未被【天妒】等获得"）：判据是"牌还在不在处理区"，与具体技能 id 无关。
        """
        subject = frame.data["subject"]
        judge_card = frame.data["judge_card"]
        if judge_card.card_id not in self.state.processing:
            return
        self.move_in_frame(
            frame,
            (judge_card,),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.JUDGE,
            owner=subject,
            actor=subject,
        )

    def apply_judge_after(self, frame: Frame) -> None:
        """调判定后果 op（帧私有 ``after`` = :data:`OPS` 注册名）；缺省即无后果。"""
        after = frame.data.get("after")
        if after is None:
            return
        OPS[after](self, frame)

    def skip_play_phase(self, seat: int) -> None:
        """让 ``seat`` 的**出牌阶段**整体跳过（【乐不思蜀】的判定后果）。

        只对**当前回合角色**生效——判定阶段本来就跑在该角色自己的回合里；跳过由
        :meth:`_phase_play_start` 消费（连阶段体一起不发，直奔弃牌阶段）。
        """
        if self.state.turn_seat == seat:
            self._turn_data["skip_play"] = True

    def skip_judge_phase(self, seat: int) -> None:
        """让 ``seat`` 的**判定阶段**整体跳过（风包夏侯渊【神速】①）。

        与 :meth:`skip_play_phase` / :meth:`skip_draw_phase` 同姿态：只对**当前回合角色**
        生效，跳过由 :meth:`_phase_judge` 的顶部消费（连阶段体与边界一并越过，直奔摸牌阶段）。
        **判定区里的牌保留**（`三国杀详细规则.txt` §11.5 L1280 / 账本行「被跳过的判定阶段
        不消费判定区里的牌」）——它只是不结算，不是被弃置。不是技能钩子，故不新增 ``Timing``。
        """
        if self.state.turn_seat == seat:
            self._turn_data["skip_judge"] = True

    def skip_draw_phase(self, seat: int) -> None:
        """让 ``seat`` 的**摸牌阶段**整体跳过（M5-06【兵粮寸断】的判定后果）。

        与 :meth:`skip_play_phase` 同姿态：只对**当前回合角色**生效（判定阶段跑在该角色
        自己的回合里），跳过由 :meth:`_phase_draw_start` 消费——连阶段体（多摸牌类技能）
        与两条边界一并越过，直奔出牌阶段。**不是**技能钩子，故不新增 ``Timing``：
        它是阶段推进的状态（spec §5.1.4 / 票面纪律）。
        """
        if self.state.turn_seat == seat:
            self._turn_data["skip_draw"] = True

    def skip_discard_phase(self, seat: int) -> None:
        """让 ``seat`` 的**弃牌阶段**整体跳过（M6 票 03【克己】）。

        与 :meth:`skip_play_phase` / :meth:`skip_draw_phase` 同姿态：只对**当前回合角色**
        生效，跳过由 :meth:`_phase_discard_start` 消费（连 DISCARD 询问与
        ``PHASE_DISCARD_END`` 一并越过，直奔结束阶段）。技能只在弃牌阶段**开始**时
        决定跳不跳，故 ``PHASE_DISCARD_START`` 本身照发（那是技能窗口）。
        """
        if self.state.turn_seat == seat:
            self._turn_data["skip_discard"] = True

    def set_draw_phase_count(self, seat: int, count: int) -> None:
        """把 ``seat`` 本回合摸牌阶段的**基础摸牌数**改成 ``count``（M6 票 01 的加法）。

        与 :meth:`skip_draw_phase` 同姿态：只对**当前回合角色**生效（阶段体钩子跑在
        他自己的回合里），由 :meth:`_phase_draw` 消费——**0 = 放弃摸牌**（【突袭】）、
        正整数是从额定张数改写（【裸衣】经 :meth:`reduce_draw_phase_by`）。它是"阶段体
        对基础摸牌的数值改写"，不是跳过阶段（跳过阶段另有 ``skip_draw_phase``：连阶段体
        与边界一起越过）。负数在发问前就 ``ValueError``（比静默夹取更容易发现调用错误）。
        """
        if count < 0:
            raise ValueError(f"摸牌张数不能为负，得到 {count!r}")
        if self.state.turn_seat == seat:
            self._turn_data["draw_count"] = count

    def draw_phase_count(self) -> int:
        """当前回合摸牌阶段**当前**计划摸几张（缺省 :data:`DRAW_COUNT`）。"""
        return int(self._turn_data.get("draw_count", DRAW_COUNT))

    def reduce_draw_phase_by(self, seat: int, count: int) -> None:
        """把 ``seat`` 本回合摸牌阶段的基础摸牌数**减少** ``count`` 张（不低于 0）。"""
        if count < 0:
            raise ValueError(f"少摸的张数不能为负，得到 {count!r}")
        self.set_draw_phase_count(seat, max(0, self.draw_phase_count() - count))

    def schedule_extra_turn(self, seat: int) -> None:
        """把一个**额外回合**压进待执行栈（M6 票 11 的唯一写入口；【博图】的落点）。

        压在这里 = "插在当前回合结算完毕之后"：回合环在下一个回合边界上弹出并接管
        （:meth:`_advance_turn`），**自然指针不前进**，故"回到本轮起始座位"不会发生、
        「每轮限 X 次」不会被额外回合刷新。**不发 delta**：记录流只在额外回合**真的
        开始**的那一刻落 ``turn_change``（记事实，不记打算）。
        """
        self.state.extra_turns.append(seat)

    def consume_delayed_card(self, frame: Frame) -> None:
        """把**延时锦囊本体**标记为"已消耗"：判定阶段循环的收尾据此送它进弃牌堆。

        【闪电】命中（♠2~9）走这条；标记而非就地搬牌，是为了让"伤害后入弃牌堆"在
        记录流里也真的发生在伤害帧结算之后（伤害帧是本 JUDGE 帧的子帧，先出栈）。
        未标记的牌由收尾按 :func:`~sgs.effects.delayed_settle_target` 顺延。
        """
        self._turn_data["judge_consumed"] = frame.data["card"].card_id

    def _effect_context(self, frame: Frame | None, *, actor: int | None) -> EffectContext:
        """叶子原语要的结算主体信息：从当前帧继承 `source` / `targets` / `card` 与层级。

        ``actor`` 是这次原语调用的主体（移动牌的操使者 / 体力被写的座位），由调用点给。
        ``frame=None`` 是**没有承载帧**的移动（弃牌阶段等回合环步骤，票 07）：主体只剩
        ``actor``，帧字段全取缺省。
        """
        if frame is None:
            return EffectContext(actor=actor)
        return EffectContext(
            actor=actor,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            card=frame.data.get("card"),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )

    def move_in_frame(
        self,
        frame: Frame | None,
        cards: tuple[CardRef, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int | None = None,
        to_top: bool = False,
    ) -> Control:
        """移动一批牌、记一条 delta，并在 ``move_card`` 之后内联派发 ``MOVE_AFTER``。

        判定路径的"无角色主体"移动（判定牌的两次移动、延时锦囊本体）主体是**被判定者**，
        与 ``source``（可能为 ``None``）分列，故调用点可显式给 ``actor``。

        这也是**效果回调的公共移动口**（票 27）：``OPS`` 里的 op 只经它搬牌，
        故"搬牌必记 delta"这条不变量对使用链与效果链是同一个实现。

        ``to_top``（票 02 的落点）：这次移动**在落点就写进牌堆顶**——一次跨区移动，不是
        "先进牌堆尾、再由调用方 ``place_on_deck`` 搬到顶"的两步。落点随 delta 落记录
        （``CardMoveDelta.to_top``）并进钩子载荷（:class:`~sgs.skills.MoveEvent`），故
        挂起劈不开两步、重建器也不必再从行动记录里猜落点；钩子看到的是**已经落在落点**
        的局面（【攻心】的置顶在钩子提问前就完成）。别的目标区没有"顶"，``move_card``
        对它们直接拒绝（编程错误）。

        **失去牌后（票 07）**：有承载帧的移动在本帧内派发 ``MOVE_AFTER``（帧私有载荷
        :class:`~sgs.skills.MoveEvent`，帧私有数据不进记录流）；**没有承载帧**的移动
        （弃牌阶段的弃置、判定阶段循环的两次收尾等回合环步骤）给 ``frame=None``，
        由 :meth:`_dispatch_move_after` 用一个不压栈的临时帧派发。钩子若提问，挂起状态
        记在引擎上（``_move_pending``），由 :meth:`_flush_pending_move` 在下一个询问应答
        之后续跑——故这里的返回值只用于"步骤可以被安全推进"的判断，挂起的续跑不依赖它。

        **重入判据是"这次移动已经搬完"，不是"还挂着"**：续跑清掉 ``_move_pending`` 之后，
        承载帧上的 op 会原地重入本方法一次；那一次必须经帧私有 :data:`_MOVE_DONE` 认出
        "同一次移动"，否则会重放搬牌并留下同区幻影 delta（见该属性的说明）。

        **它不是全项目唯一的搬牌点**：使用链、效果回调、上面两类帧外移动与**主公杀忠臣的
        弃置**（:meth:`_lord_discards_everything`，票 33 起以死亡帧为承载帧）都经本方法，
        但另有一小组**直接调** :func:`~sgs.effects.move_card` 的收尾点——阵亡者的区域清空
        （:meth:`discard_dead_cards`）、奖惩摸牌（:meth:`_reward_killer_draws`）、摸牌阶段
        （:meth:`_phase_draw`）、终局清栈（:meth:`_teardown_processing`）与开局发牌
        （:func:`build_initial`）。它们**不派发**失去牌后：摸牌阶段 / 奖惩摸牌 / 开局发牌是
        "牌堆 → 手牌"（`R5-Q3`：摸牌不算失去），终局清栈发生在胜负已定之后，而阵亡弃牌
        仍登记为**有意**的缺口（票 33 只钉存活者一侧，裁定见 `开发文档/06`
        §5「程序化约定与偏离一手」）。勿据此在本方法里加回唯一性论断。
        """
        card_ids = tuple(card.card_id for card in cards)
        # 这次移动的**身份** = "哪个技能、把哪些牌、搬到哪里、落在哪里"。
        head = (
            frame.data.get(RUNNING_SKILL_KEY) if frame is not None else None,
            card_ids,
            to_zone,
            to_top,
        )
        if (
            frame is not None
            and frame.data.get(_MOVE_DONE) == head
            and all(locate(self.state, card_id) == to_zone for card_id in card_ids)
        ):
            # 同一次移动的**重入**：牌已搬完、delta 已记，只是钩子的派发还没续跑。
            # 不再搬第二遍（牌已不在来源区），把控制权交回驱动循环去消费那条应答。
            #
            # 判据挂在"这次移动**已经发生**"这个持久事实上——它写在**承载帧私有**的
            # :data:`_MOVE_DONE` 里，而不是挂在"``_move_pending`` 还在"上：
            # :meth:`_flush_pending_move` 消费完应答就把挂起状态清掉，之后承载帧才原地重入
            # 同一个 op，若把重入与挂起混为一谈，第二遍 ``move_card`` 会照记一条
            # ``processing → processing`` 的幻影 delta。
            #
            # 两个条件都必需，且各自挡住一种误判：
            #
            # - **身份带技能**（:data:`RUNNING_SKILL_KEY`）挡住"同帧里另一次参数相同的移动"
            #   被当成重入：多点伤害里两个提供者可以把同一张牌搬向同一个区域（【遗计】交给
            #   郭嘉、【反馈】再拿郭嘉那张），此时牌 + 目标逐字段相同，但技能不同。
            # - **牌真的在目标区**挡住"同技能、同牌、同目标的**新**移动"被当成重入：真正的
            #   新移动发生在来源区（牌还没搬），而重入时那条 delta 已经记过、牌已在目标区。
            #
            # **判据按帧私有存储**：续跑的钩子可能在**临时帧**上搬牌（【连营】的摸牌就走
            #   :meth:`_dispatch_move_after` 的临时帧），状态挂在引擎上会被那次搬牌覆盖，
            #   于是这次重入认不出来。
            #
            # 返回 ``None`` 而不是 ``"stay"``：重入的调用点看到的是"这次移动已经完成"，
            # 若回 ``"stay"``，把返回值透传出去的 op（``use_choose_target`` 一类）会认为
            # 自己还没跑完、把游标留在原处，驱动循环于是原地空转。
            return None
        # **事件帧无效**（M6 票 08 的【奇才】②）：只有"装备区 → 弃牌堆"的移动才开这个
        # 接缝（"其他角色弃置你装备区里的防具牌"的唯一形状），且只在有承载帧时——帧外
        # 移动（弃牌阶段的自弃、阵亡清区）没有"其他角色"可判。票 33 起主公杀忠臣的弃置
        # 也带承载帧（死亡帧），但它同样判不出"其他角色"：死亡帧没有 `source`、也没有
        # 正在跑的技能（`_move_causer` 因此是 `None`）⇒ 接缝的条件自己挡掉。
        # 技能标记后整个事件被取消：不搬牌、不记 delta、不派发 `MOVE_AFTER`，移动入口原样返回。
        if frame is not None and to_zone.zone is Zone.DISCARD:
            from_zone = locate(self.state, card_ids[0])
            if from_zone.zone is Zone.EQUIP:
                frame.data[MOVE_EVENT_KEY] = MoveEvent(
                    cards=tuple(cards),
                    from_zone=from_zone,
                    to_zone=to_zone,
                    reason=reason,
                    owner=owner,
                    after_holder=to_zone.seat,
                    actor=actor,
                    causer=self._move_causer(frame),
                    to_top=to_top,
                )
                control = self.dispatch_seam(frame, SeamKind.EQUIPMENT_DISCARD)
                if control == "stay":
                    return "stay"
                cancelled = take_event_frame_invalid(frame)
                frame.data.pop(MOVE_EVENT_KEY, None)
                if cancelled:
                    return None
        ctx = self._effect_context(
            frame,
            actor=(frame.data["source"] if frame is not None else None) if actor is None else actor,
        )
        delta = move_card(
            self.state,
            ctx,
            tuple(cards),
            to_zone=to_zone,
            reason=reason,
            owner=owner,
            to_top=to_top,
        )
        self.emit_delta(
            delta,
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        prune_skill_state(self.state)
        # 牌移动后同样核一次来源：装备 / 获得来源离场时，随来源消失的标签立刻清掉
        # （M6 票 09；武将技能失去 / 失效另走各自写入口的同一次清理）。
        self._prune_card_tags()
        count = frame.data.get(_MOVE_EVENTS, 0) + 1 if frame is not None else 0
        if frame is not None:
            # 这次移动的**持久事实**落在承载帧私有；重入时同一份身份 + "牌已在目标区"即认出。
            frame.data[_MOVE_DONE] = head
            frame.data[_MOVE_EVENTS] = count
        event = self._move_event(delta, actor=actor)
        key = self._move_key(event, count)
        control = self._dispatch_move_after(
            frame,
            event,
            count=count,
            key=key,
            frame_id=frame.frame_id if frame is not None else 0,
        )
        return "stay" if control == "stay" else None

    @staticmethod
    def _move_causer(frame: Frame) -> int | None:
        """**这次移动的发起人**：当时正在跑的技能的持有者；没有技能时退回帧的 ``source``。

        弃置类效果常把 ``actor`` 写成牌的归属者（【寒冰剑】/界【刚烈】都这样），故"是不是
        **其他角色**弃置的"只能由"谁在跑"回答。两个键都由派发环在回调前写；帧外的收尾移动
        没有帧，调用点也不会问它（只在有承载帧的装备区弃置前调用）。
        """
        running = frame.data.get(RUNNING_SKILL_KEY)
        if running and frame.data.get("skill_owner") is not None:
            return int(frame.data["skill_owner"])
        source = frame.data.get("source")
        return None if source is None else int(source)

    def _move_event(self, delta: Any, *, actor: int | None) -> MoveEvent:
        """把一条 ``card_move`` 的形状装成 :class:`~sgs.skills.MoveEvent`（派发载荷）。

        落点（``to_top``）一并透传：钩子读到的"这次移动"要与它落进记录的那条 delta 同形，
        否则"牌已经在牌堆顶"这件事在钩子侧只能靠读 ``state.deck`` 才看得出来。
        """
        return MoveEvent(
            cards=tuple(delta.cards),
            from_zone=delta.from_zone,
            to_zone=delta.to_zone,
            reason=delta.reason,
            owner=delta.owner,
            after_holder=delta.to_zone.seat,
            actor=actor,
            to_top=delta.to_top,
        )

    @staticmethod
    def _resume_frame(dispatch_frame: Frame, event: MoveEvent) -> Frame:
        """挂起后续跑用的**不压栈临时帧**（M6 票 08）。

        形状与票 07 的临时帧逐字相同（``source = event.actor`` / 无目标 / 无渠道牌、
        ``frame_id = 0``），故续跑里所有 delta 与时机记录的坐标与"帧外移动"一致——这是
        冻结基准（38 / 39 号录像）已经录下的坐标，不能改。

        **但帧私有数据从派发帧继承**：钩子的询问缓存（``ask_answers``）与效果游标都住在
        帧私有里，续跑换一条空帧会把它们丢光——零步钩子只是把同一问再问一遍（旧行为），
        多步钩子（【清俭】是第一条）会整条静默丢失并把下一条应答串给下一个询问。
        继承时只覆盖三个"帧语义"键（``source`` / ``targets`` / ``card``），其余原样共享
        （``ask_answers`` 是同一个 dict，故后续每一步的答案继续累在它上面）。
        """
        data = dict(dispatch_frame.data)
        data["source"] = event.actor
        data["targets"] = ()
        data["card"] = None
        # **发问身份随数据一起继承**（票 02 的应答归属）：续跑帧的 ``frame_id`` 恒为 0，
        # 而钩子在续跑里重问的是**同一问**——应答必须仍认得出它（见 :func:`_ask_origin`）。
        # 来源帧本身若也是续跑帧，就沿用它的那份身份。
        origin = _ask_origin(dispatch_frame)
        if origin is not None:
            data[_ANSWER_ORIGIN_KEY] = origin
        # 本次移动的载荷由**续跑帧自己**带一份（票 23）：``_dispatch_move_after`` 在派发
        # 结束后会把 ``move_event`` 还原成外层帧原来那份（嵌套搬牌不许改写外层载荷），
        # 故不能指望 dispatch_frame 上还留着"这一轮"的那条。
        data[MOVE_EVENT_KEY] = event
        return Frame(kind=FrameKind.RESOLVE, data=data, frame_id=0)

    def _dispatch_move_after(
        self,
        frame: Frame | None,
        event: MoveEvent,
        *,
        count: int,
        key: tuple,
        frame_id: int = 0,
    ) -> Control:
        """把这次移动当"失去牌后"派发 ``MOVE_AFTER``（票 07；M6 票 08 补挂起续跑）。

        ``key`` 是这次时机实例的幂等键（见 :meth:`_move_key`）：重入续跑时沿用它，
        故"同一次移动"只落一条记录、只派发一遍。承载帧可能已经不在栈上（挂起期间
        上层帧出栈），故此时改用一条**不压栈的临时帧**。

        钩子提问而挂起时，把**续跑帧**（:meth:`_resume_frame`：临时帧形状 + 派发帧的帧私有
        数据）连同**原始承载帧**一起压进 :meth:`_flush_pending_move` 的挂起栈：前者保住
        询问缓存 / 效果游标，后者保住"时机记录去重"的旧语义（承载帧上
        ``_pending_move_timing`` 若被同帧后续移动覆盖，续跑要补记一条 ``MOVE_AFTER``——
        冻结基准里真有这一条）。

        **入栈下标 = 派发开始前的栈深**（票 25）：内层移动先挂起、外层钩子在展开返回时
        才知道自己也要挂起；若直接 append，外层会压在内层之上，续跑顺序就反了。按下标
        插在"本次派发之前就存在的项"之上、本次派发里新产生的项之下，栈顶始终是最深一层。
        """
        pending_depth = len(self._move_pending)
        if frame is not None:
            if not move_after_providers(self.state, event=event):
                # **惰性守卫**（票 07 §要做什么 2）：没有提供者 ⇒ 一个时机事件都不发、
                # 一个钩子都不跑。无武将模式的记录流因此**逐字节不变**（M2 的 34 条基准
                # 不重录）——这是本票的硬约束，比"每条移动都落一条记录"更重要。
                frame.data.pop("_pending_move_timing", None)
                return None
            carried = frame.frame_id == frame_id
            if frame.data.get("_pending_move_timing") != key:
                self.emit_timing(
                    Timing.MOVE_AFTER,
                    frame_id=frame_id,
                    depth=self.stack.depth,
                    actor=event.actor,
                    source=frame.data.get("source"),
                    targets=(),
                    cards=event.cards,
                )
            dispatch_frame = frame
            if not carried:
                # 承载帧已经不在栈上（挂起期间上层帧出栈）：用临时帧续跑，
                # 不往一个已出栈的帧里写载荷。
                dispatch_frame = Frame(
                    kind=FrameKind.RESOLVE,
                    data={"source": event.actor, "targets": (), "card": None},
                    frame_id=frame_id,
                )
            # **本次移动的载荷要在派发前先存起来**（票 23）：钩子里的摸牌 / 弃牌本身就是一次
            # ``move_in_frame``，它的嵌套派发会就地把 ``move_event`` 改写成"牌堆 → 手牌"。
            # 外层钩子若在摸过牌之后才重入（【枭姬】逐张触发要问第二次），条件读到的是那次
            # 摸牌的载荷 ⇒ ``from_zone`` 不再是装备区、判据整体翻假、剩下的触发静默丢失。
            # ``move_event`` 是"这一轮派发"的事实，与外层帧的其它字段一样只属于派发的调用方。
            prior_event = dispatch_frame.data.get(MOVE_EVENT_KEY)
            prior_had_event = MOVE_EVENT_KEY in dispatch_frame.data
            dispatch_frame.data[MOVE_EVENT_KEY] = event
            # 这次派发的范围键（M6 票 20）：本次移动的幂等键。同一帧里的下一次移动是下一个
            # 范围，故同一技能第二次触发会重新发问；挂起续跑用的临时帧从派发帧继承它。
            # **嵌套派发要还原**：效果里的搬牌（内联 MOVE_AFTER）会把范围改掉，而外层钩子
            # 在续跑后还要用**自己**那份范围命中缓存——不还原就会重发一个已答的询问，把
            # 下一条应答（例如【反间】的选项）错配给座位步。
            prior_scope = dispatch_frame.data.get(_DISPATCH_SCOPE)
            prior_had_scope = _DISPATCH_SCOPE in dispatch_frame.data
            dispatch_frame.data[_DISPATCH_SCOPE] = key
            # **把"正在跑的技能"还给承载帧**：``_dispatch`` 在跑每条钩子前把
            # ``RUNNING_SKILL_KEY`` 改成钩子的 id，而 ``move_in_frame`` 的**重入判据**
            # （:data:`_MOVE_DONE`）拿它当"这次移动是谁发起的"的一部分。挂起续跑完成后，
            # 承载帧的 op 会原地重入 ``move_in_frame``；若不还原，身份会变成最后一条钩子的
            # id、认不出"这次移动已经搬完"，于是把整条 ``MOVE_AFTER`` 再派发一遍
            # （【连营】因此多摸一次牌；票 12 的混合局回归）。
            prior_skill = frame.data.get(RUNNING_SKILL_KEY)
            prior_had_skill = RUNNING_SKILL_KEY in frame.data
            result = dispatch_move_after(self, dispatch_frame)
            if prior_had_event:
                dispatch_frame.data[MOVE_EVENT_KEY] = prior_event
            else:
                dispatch_frame.data.pop(MOVE_EVENT_KEY, None)
            if prior_had_skill:
                frame.data[RUNNING_SKILL_KEY] = prior_skill
            else:
                frame.data.pop(RUNNING_SKILL_KEY, None)
            if result == "stay":
                frame.data["_pending_move_timing"] = key
                # 续跑帧在**还原范围之前**建：它要带着这次派发的范围，续跑里的询问才命中
                # 同一份缓存（票 20 的答案缓存策略）。
                self._move_pending.insert(
                    pending_depth,
                    _PendingMove(
                        event,
                        key,
                        self._resume_frame(dispatch_frame, event),
                        frame,
                    ),
                )
            else:
                frame.data.pop("_pending_move_timing", None)
            if prior_had_scope:
                dispatch_frame.data[_DISPATCH_SCOPE] = prior_scope
            else:
                dispatch_frame.data.pop(_DISPATCH_SCOPE, None)
            return result
        # 无承载帧（弃牌阶段等）：先判惰性守卫，再落记录并派发。
        if not move_after_providers(self.state, event=event):
            return None
        if self._move_recorded != key:
            self.emit_timing(
                Timing.MOVE_AFTER,
                frame_id=frame_id,
                depth=self.stack.depth,
                actor=event.actor,
                source=event.actor,
                targets=(),
                cards=event.cards,
            )
            self._move_recorded = key
        # **不压栈**的临时帧只服务这一次派发（记录坐标取 ``frame_id=0``/``depth=0``，
        # 与它那条 ``card_move`` 一致）。
        transient = Frame(
            kind=FrameKind.RESOLVE,
            data={"source": event.actor, "targets": (), "card": None},
            frame_id=frame_id,
        )
        transient.data[MOVE_EVENT_KEY] = event
        # 这次派发的范围键（M6 票 20）在**帧外**路径同样要写（票 25 的嵌套挂起）：临时帧
        # 服务整次派发，钩子里的摸牌 / 弃牌会在这同一条帧上再派发 MOVE_AFTER；不写范围键，
        # 同一技能第二次触发会命中上一次的答案缓存 → 第二次连"是否发动"都不问。
        transient.data[_DISPATCH_SCOPE] = key
        result = dispatch_move_after(self, transient)
        if result == "stay":
            self._move_pending.insert(pending_depth, _PendingMove(event, key, transient, None))
        return result

    @staticmethod
    def _move_key(event: MoveEvent, count: int) -> tuple:
        """一次 ``MOVE_AFTER`` 时机实例的幂等键。

        键 = ``(时机, 这批牌, 本帧第几次移动)``——**必须带序号**：同一帧里"同一批牌"
        可能移动到不同区域（顶替的先旧后新、拆顺的两次搬牌），只按键里的牌 id 会把
        后一次移动误当成前一次的记录重入（票 07）。
        """
        return (Timing.MOVE_AFTER, tuple(card.card_id for card in event.cards), count)

    def _move_cards(
        self,
        card_ids: tuple[int, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int,
    ) -> Control:
        """**帧外**移动一批牌（帧已出栈的收尾移动，票 10 §3.6）：转调 :meth:`move_in_frame`。

        判定阶段循环的两次收尾移动（延时锦囊 JUDGE→PROCESSING 与「仍在处理区」的去向）
        没有承载帧，主体是当前回合角色；``frame=None`` 正是 :meth:`move_in_frame` 表达
        "本次移动没有承载帧"的形式，故这里是**纯转调**（票 07 的帧外直调点在 C1 收口）：
        "搬牌必记 delta、必派发失去牌后"这条不变量不再有第二份实现。
        """
        return self.move_in_frame(
            None,
            self.state.refs(card_ids),
            to_zone=to_zone,
            reason=reason,
            owner=owner,
            actor=actor,
        )

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> Any:
        """解释行为表的步骤列表：``PushFrame`` 压子帧、``CallOp`` 查 :data:`OPS` 表调用。

        ``CallOp`` 只按**名**通用派发（回调签名 ``(host, frame)``），引擎里不出现任何
        具体效果名（票 09 §4）；未登记的名字是加载期就该拦下的错误（``card_type.unknown_op``），
        运行期再撞到即 fail-closed。

        **帧内游标**（票 19 §8）：进度记在 ``frame.data["effect_pc"]`` 上，故

        - 压出一个子帧后返回 ``"stay"``——子帧先跑，出栈后回到本步接着跑下一个效果；
        - 回调提问时返回 ``"stay"``——游标**不前进**，重入时重跑同一个回调，
          而 ``ask`` 直接返回已存应答，不会重复发问。

        M1 的每张牌至多一个效果步，故记录流逐字节不变（多出的一次重入不产生任何记录）。
        """
        index = frame.data.get("effect_pc", 0)
        while index < len(effects):
            step = effects[index]
            if isinstance(step, PushFrame):
                data = _child_payload(
                    step.kind,
                    frame.data,
                    step.scalars,
                    targets=(
                        (self.current_target(frame),)
                        if frame.data.get("targets")
                        else ()
                    ),
                )
                self.push_frame(step.kind, data, timing=timing)
                frame.data["effect_pc"] = index + 1
                return "stay"
            assert isinstance(step, CallOp)  # union 只有两类步骤
            op = OPS.get(step.op)
            if op is None:
                raise NotImplementedError(f"未登记的效果原语 {step.op!r}（票 09 §4 的 OPS 表）")
            frame.data["effect_pc"] = index + 1
            control = op(self, frame)
            if control == "stay":
                frame.data["effect_pc"] = index
                return "stay"
            index = frame.data["effect_pc"]
        return None

    def _note_play_phase_slash(self, card: CardRef, source: int) -> None:
        """把"出牌阶段内使用 / 打出过【杀】"记进座位级事实（M6 票 03【克己】）。

        判据三条同时成立：这是**当前回合角色自己的出牌阶段**
        （:func:`~sgs.effects.is_own_play_phase`——回合外的响应、别人的出牌阶段都不算）、
        牌面属于【杀】**家族**（:func:`~sgs.effects.is_slash`，火 / 雷 / 转化 / 虚拟同族）。
        写入唯一入口；复位见 :meth:`_phase_play_start`。不发 delta（见 ``Player`` 字段文档）。
        """
        if not is_own_play_phase(self.state, source):
            return
        if not is_slash(behavior_for(card)):
            return
        self.state.player(source).slash_in_play_phase = True

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> HpChangeDelta:
        """调 ``apply_hp`` 这个叶子原语并把返回的变化事件写进记录流（票 07 §1）。

        ``frame_id`` 即调用它的扣减 / 回复帧，故"这次体力变化属于谁"可由帧关联推出。
        """
        ctx = self._effect_context(frame, actor=seat)
        delta = apply_hp(self.state, ctx, seat, value)
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        return delta

    def reduce_hp_max(self, frame: Frame, seat: int, amount: int = 1) -> HpMaxChangeDelta:
        """**减体力上限**（M6 票 06 的唯一写入口；觉醒技"减 1 点体力上限"）。

        规则（§四 4.15「体力上限变化」）：
        - 减上限**不会**自动回血（体力值不因上限变小而上升）；
        - 若减上限前体力值**等于**上限，则减上限的同时**须把体力值调整至**新上限
          （`L436–437`）。因此"满体力觉醒"会同时落两条 delta：``hp_max_change`` 与
          ``hp_change``，而"已受伤觉醒"只有前一条；
        - 上限减为 0 ⇒ 直接死亡、不进濒死（`L431–433`）。M6 的两个载体只会 4 → 3，
          该档**没有载体**，故本原语只接受减后仍 ≥ 1 的调用（越界即编程错误，不静默
          实现半套死亡流程）。

        返回值 = 这条 ``hp_max_change`` 事件；``frame`` 是承载帧（技能帧），记录坐标与
        同帧的 ``skill_gain`` 一致。
        """
        player = self.state.player(seat)
        before_max = player.hp_max
        before_hp = player.hp
        after_max = before_max - amount
        if after_max < 1:
            raise ValueError(
                f"体力上限 {before_max} 减 {amount} 后为 {after_max}：M6 没有该档载体"
                "（上限减为 0 的直接死亡流程未落地）"
            )
        player.hp_max = after_max
        delta = HpMaxChangeDelta(
            seat=seat, before=before_max, after=after_max, amount=amount
        )
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        if before_hp == before_max and before_hp > after_max:
            # 规则 L436–437：减上限前满体力 ⇒ 体力值同步调整到新上限（**不回血**，只下调）。
            self.apply_hp_change(frame, seat, after_max)
        return delta

    def note_damage_dealt(self, seat: int) -> bool:
        """记"``seat`` 本回合**造成过伤害**"的座位级事实（M6 票 06 的【诛害】判据）。

        写入唯一入口：伤害数值定稿且 > 0、未被防止、有来源时由
        :func:`~sgs.engine.steps.damage_finalize_amount` 调一次。作用域 = **本回合**：
        :meth:`_clear_turn_effects` 在回合末把所有座位的这一位清掉（无论伤害是谁造成的），
        故"本回合造成过伤害"只覆盖当前回合。**不发 delta**（与 ``luoyi_active`` 同姿态：
        没有 JSON 读者，回放按同一条伤害结算重建）。返回值 = 状态真的变了吗。
        """
        player = self.state.player(seat)
        if player.damage_dealt_this_turn:
            return False
        player.damage_dealt_this_turn = True
        return True

    def set_chained(self, frame: Frame | None, seat: int, chained: bool) -> bool:
        """写一个座位的**横置**状态并落一条 ``chained_change``（M5-03 的唯一写入口）。

        返回值 = 状态真的变了吗。铁索连环的 toggle 与属性伤害的"结算后立即重置"都经这里，
        故"重置在伤害事件内、先于 `DAMAGE_AFTER_TAKE`"由**调用点**决定，这里只管写状态 +
        记 delta：``frame`` 是承载帧（重置 = LIFE_DECREASE 帧，记录流里因此与那条
        ``hp_change`` 同帧），帧外写状态时为 ``None``（记录坐标 ``frame_id = 0``）。
        """
        player = self.state.player(seat)
        if player.chained == chained:
            return False
        player.chained = chained
        self.emit_delta(
            ChainedChangeDelta(seat=seat, chained=chained),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        return True

    def set_flipped(self, frame: Frame | None, seat: int, flipped: bool) -> bool:
        """写一个座位的**翻面**状态并落一条 ``flipped_change``（翻面机制的唯一写入口）。

        返回值 = 状态真的变了吗。两处调用点：①技能效果把武将牌翻过去（【据守】的
        "然后翻面"是 **toggle**：正 → 背、背 → 正）；②回合环在"回合开始前"把背面朝上的
        角色翻回正面（规则集 13.2 的固定流程，帧外 ⇒ ``frame=None``，记录坐标 0）。

        与 :meth:`set_chained` 同形：状态 + 一条 delta，"回放可重建"不靠调用点自觉。
        """
        player = self.state.player(seat)
        if player.flipped == flipped:
            return False
        player.flipped = flipped
        self.emit_delta(
            FlippedChangeDelta(seat=seat, flipped=flipped),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        return True

    def add_marker(
        self,
        frame: Frame | None,
        seat: int,
        *,
        name: str,
        source_seat: int,
        source_skill_id: str,
        count: int = 1,
        max_count: int | None = None,
        effect: str | None = None,
        description: str | None = None,
        clear_on_source_death: bool = False,
    ) -> bool:
        """给 ``seat`` 加一枚/几枚标记（票 06 的唯一写入口）。

        合并键 = ``(name, source_seat, source_skill_id)``：同键相加（有上限则夹住），异键
        追加。每次真实变化发一条 ``marker_change``（绝对值）；没有变化（已达上限等）返回
        ``False`` 且不发 delta。标记是公开事实（投影 ``PlayerPublic.markers``），但投影面
        ``exclude``、不进记录流的询问视角；变化本身经 delta 进记录流。
        """
        player = self.state.player(seat)
        for index, marker in enumerate(player.markers):
            if (
                marker.name == name
                and marker.source_seat == source_seat
                and marker.source_skill_id == source_skill_id
            ):
                limit = marker.max_count
                new_count = marker.count + int(count)
                if limit is not None:
                    new_count = min(new_count, limit)
                if new_count == marker.count:
                    return False
                updated = marker.model_copy(update={"count": new_count})
                player.markers = (
                    player.markers[:index] + (updated,) + player.markers[index + 1 :]
                )
                self._emit_marker_change(frame, seat, updated)
                return True
        if count <= 0:
            return False
        marker = Marker(
            name=name,
            source_seat=source_seat,
            source_skill_id=source_skill_id,
            count=int(count),
            max_count=max_count,
            effect=effect,
            description=description,
            clear_on_source_death=clear_on_source_death,
        )
        player.markers = (*player.markers, marker)
        self._emit_marker_change(frame, seat, marker)
        return True

    def remove_marker(
        self,
        frame: Frame | None,
        seat: int,
        *,
        name: str,
        source_seat: int,
        source_skill_id: str,
        count: int | None = None,
    ) -> bool:
        """移去标记（票 06）：``count=None`` 清空该条；否则减 ``count``，减到 0 即清空。"""
        player = self.state.player(seat)
        for index, marker in enumerate(player.markers):
            if (
                marker.name != name
                or marker.source_seat != source_seat
                or marker.source_skill_id != source_skill_id
            ):
                continue
            new_count = 0 if count is None else max(0, marker.count - int(count))
            if new_count == marker.count:
                return False
            if new_count == 0:
                player.markers = player.markers[:index] + player.markers[index + 1 :]
                removed = marker
            else:
                removed = marker.model_copy(update={"count": new_count})
                player.markers = (
                    player.markers[:index] + (removed,) + player.markers[index + 1 :]
                )
            self.emit_delta(
                MarkerChangeDelta(
                    seat=seat,
                    name=name,
                    source_seat=source_seat,
                    source_skill_id=source_skill_id,
                    count=new_count,
                ),
                frame_id=(frame.frame_id if frame is not None else 0),
                depth=self.stack.depth,
            )
            return True
        return False

    def _emit_marker_change(self, frame: Frame | None, seat: int, marker: Marker) -> None:
        self.emit_delta(
            MarkerChangeDelta(
                seat=seat,
                name=marker.name,
                source_seat=marker.source_seat,
                source_skill_id=marker.source_skill_id,
                count=marker.count,
            ),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )

    def _clear_dead_markers(self, frame: Frame, seat: int) -> None:
        """阵亡清标记（票 06；用户 Q6 规则 7）：目标为死者全清；来源身死的按寿命策略清。

        与死者的弃牌**同一钩子**（:meth:`discard_dead_cards`；用户本轮："梦魇清除时机和
        阵亡后弃置卡牌同一钩子"）。逐条发 ``marker_change(count=0)``；顺序 = 座位序 +
        标记插入序，保同种子逐字节复现。
        """
        for player in self.state.players:
            kept: list[Marker] = []
            removed: list[Marker] = []
            for marker in player.markers:
                if player.seat == seat or (
                    marker.clear_on_source_death and marker.source_seat == seat
                ):
                    removed.append(marker)
                else:
                    kept.append(marker)
            if not removed:
                continue
            player.markers = tuple(kept)
            for marker in removed:
                self.emit_delta(
                    MarkerChangeDelta(
                        seat=player.seat,
                        name=marker.name,
                        source_seat=marker.source_seat,
                        source_skill_id=marker.source_skill_id,
                        count=0,
                    ),
                    frame_id=frame.frame_id,
                    depth=self.stack.depth,
                )

    def set_wine_active(self, frame: Frame | None, seat: int, active: bool) -> bool:
        """写一个座位的【酒】加伤状态并落一条 ``wine_change``（M5-04 的唯一写入口）。

        返回值 = 状态真的变了吗。三处调用点：出牌阶段的【酒】效果置位（RESOLVE 帧）、
        下一张【杀】被使用时消费（USE 帧）、回合末（含回合内阵亡跳段）与下一回合开始时的
        清理（帧外，``frame=None`` → 记录坐标 ``frame_id = 0``）。集中到这一个口子，
        "状态变化必有 delta"与"回放可重建"因此不靠调用点自觉。
        """
        player = self.state.player(seat)
        if player.wine_active == active:
            return False
        player.wine_active = active
        self.emit_delta(
            WineChangeDelta(seat=seat, active=active),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        return True

    def _clear_wine_active(self, seat: int) -> None:
        """把一个座位的【酒】加伤清掉（回合的收尾点与下一回合起点各调一次，幂等）。"""
        if self.state.player(seat).wine_active:
            self.set_wine_active(None, seat, False)

    # 四个座位级布尔技能状态（标准 / 界【裸衣】、【咆哮】②、【诈降】）形状相同：
    # 幂等写入、**不发 delta**、清理点由调用方决定。唯一写入口收在这对 helper 上；
    # 各公开 setter 只保留名字与文档（skills / state.models 的交叉引用仍指向它们）。

    def _set_player_flag(self, seat: int, field: _BooleanPlayerFlag, active: bool) -> bool:
        """写一个座位的布尔技能状态；返回值 = 状态真的变了吗（幂等）。"""
        player = self.state.player(seat)
        if getattr(player, field) == active:
            return False
        setattr(player, field, active)
        return True

    def _clear_player_flag(self, seat: int, field: _BooleanPlayerFlag) -> None:
        """把一个座位的布尔技能状态清掉（清理点由调用方决定，幂等）。"""
        if getattr(self.state.player(seat), field):
            self._set_player_flag(seat, field, False)

    def set_luoyi_active(self, seat: int, active: bool) -> bool:
        """写一个座位的【裸衣】加伤状态（M6 票 01 的唯一写入口；**不发 delta**）。

        与 :meth:`set_wine_active` 同形但少一条 ``wine_change`` 式的事件：本状态没有 JSON
        读者（网页座位卡不读它、记录流不写它），回放按同一条 ``SKILL_ACTIVATION`` 动作
        重跑即可重建。返回值 = 状态真的变了吗；回合末与下一回合起点的清理都走这里，
        因此"置过而没用掉"与"从来没置过"在状态面同形。
        """
        return self._set_player_flag(seat, "luoyi_active", active)

    def _clear_luoyi_active(self, seat: int) -> None:
        """把一个座位的【裸衣】加伤清掉（回合的收尾点与下一回合起点各调一次，幂等）。"""
        self._clear_player_flag(seat, "luoyi_active")

    def set_luoyi_until_next_turn(self, seat: int, active: bool) -> bool:
        """写一个座位的**界**【裸衣】"直到你的下个回合开始"加伤状态（M6 票 14 的唯一写入口）。

        与 :meth:`set_luoyi_active` 同形、同样**不发 delta**，但是**另一个字段**：标准版
        裸衣到**本回合结束**（收尾点 :meth:`_turn_after_end`），界版到**你的下个回合开始**
        （§十九 L2051–L2055），清理点因此落在该座位自己的 ``TURN_START``——两者共用一个
        字段会让其中一版的作用域被另一版的收尾点提前抹掉。
        """
        return self._set_player_flag(seat, "luoyi_until_next_turn", active)

    def _clear_luoyi_until_next_turn(self, seat: int) -> None:
        """把界【裸衣】加伤清掉（**只**在该座位自己的 ``TURN_START`` 调，幂等）。"""
        self._clear_player_flag(seat, "luoyi_until_next_turn")

    def set_paoxiao_bonus(self, seat: int, active: bool) -> bool:
        """写一个座位的【咆哮】②"下一次【杀】伤害 +1"待消费标记（M6 票 05 的唯一写入口）。

        与 :meth:`set_luoyi_active` 同形、同样**不发 delta**：状态没有 JSON 读者（网页座位卡
        不读它、记录流不写它），回放按同一条【闪】的应答重跑即可重建。返回值 = 状态真的变了
        吗；回合末与下一回合起点的清理都走这里，故"置过而没用掉"与"从来没置过"在状态面同形。
        "一次性效果不随来源消失撤回"（D9）由**座位级**存储兑现：技能实例失去 / 失效不会
        抹掉这个已经挂上的 +1。
        """
        return self._set_player_flag(seat, "paoxiao_bonus", active)

    def _clear_paoxiao_bonus(self, seat: int) -> None:
        """把一个座位的【咆哮】待消费标记清掉（回合收尾点与下一回合起点各调一次，幂等）。"""
        self._clear_player_flag(seat, "paoxiao_bonus")

    def set_zhaxiang_active(self, seat: int, active: bool) -> bool:
        """写一个座位的界【诈降】"本阶段"效果状态（M6 票 17 的唯一写入口）。

        写入方 = 【诈降】的 ``LIFE_DECREASE_AFTER`` 钩子（"此刻是我的出牌阶段"时置位）；
        清理点 = **该座位的出牌阶段结束**（:meth:`_phase_play_end`，起点再兜一次）——与
        ``slash_in_play_phase`` 的"阶段起点复位"刻意不同：这三条加成必须在阶段结束就消失，
        否则别的回合里被"借刀杀人"被迫使用的【杀】会错误地吃到它们。**不发 delta**：
        与 ``luoyi_active`` 同姿态（没有 JSON 读者，回放按同一次失去体力重建）。
        """
        return self._set_player_flag(seat, "zhaxiang_active", active)

    def _clear_zhaxiang_active(self, seat: int) -> None:
        """把界【诈降】的"本阶段"效果清掉（出牌阶段起止各调一次，幂等）。"""
        self._clear_player_flag(seat, "zhaxiang_active")

    # -- 技能三态（M6 票 04）：获得 / 失去 / 失效 -------------------------------

    def grant_skill(self, seat: int, skill_id: str, *, frame: Frame | None = None) -> bool:
        """写一个座位的**获得**技能记录并落一条 ``skill_gain``（M6 票 04 的唯一写入口）。

        返回值 = 状态真的变了吗。获得是**第四种技能来源**（`GLOSSARY.md`「技能来源」）：
        载体解析读 :attr:`~sgs.state.Player.granted_skills` 产出 ``GRANTED`` 实例，与
        装备 / 虚拟授予 / 武将牌同一条链、同一条去重口径。``frame`` 是承载帧（觉醒技在
        技能帧里授予 ☆ 技能），帧外写状态时为 ``None``（记录坐标 ``frame_id = 0``）。
        M6 没有"获得技能"的武将（票 06 的 ☆ 技能是第一个消费者），故本票只以构造测试钉形状。
        """
        player = self.state.player(seat)
        if skill_id in player.granted_skills:
            return False
        player.granted_skills.append(skill_id)
        self.emit_delta(
            SkillGainDelta(seat=seat, skill_id=skill_id),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        return True

    def lose_skill(self, seat: int, skill_id: str, *, frame: Frame | None = None) -> bool:
        """写一个座位的**失去**技能记录并落一条 ``skill_lose``（M6 票 04 的唯一写入口）。

        失去 = 实例不再存在且**不可恢复**（`GLOSSARY.md`「技能失去」）：载体解析在去重后
        减去 ``Player.lost_skills``，故武将牌上的技能清单不动、连续型效果（距离 / 目标池）
        也随之消失。"不可恢复"由**解析顺序**兑现：之后再 ``grant_skill`` 同一个 id，实例
        仍在"减失去"那一步被减掉（记录照写，但那是一次可见的无效授予）。
        失去让 per-instance 状态随来源一起清掉（``prune_skill_state``）。
        """
        player = self.state.player(seat)
        if skill_id in player.lost_skills:
            return False
        player.lost_skills.append(skill_id)
        self.emit_delta(
            SkillLoseDelta(seat=seat, skill_id=skill_id),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        prune_skill_state(self.state)
        # 牌标签的第四项声明（M6 票 09）：来源实例真的消失 ⇒ 随来源消失的标签一并清掉。
        self._prune_card_tags()
        return True

    def suppress_skills(
        self,
        seat: int,
        skill_id: str,
        *,
        frame: Frame | None = None,
    ) -> bool:
        """给一个座位加一条**失效**记录并落一条 ``skill_suppress``（M6 票 04 的唯一写入口）。

        失效 = 实例**还在**、但被座位级谓词挡在解析之外（`GLOSSARY.md`「技能失效」）：
        失效率不逐技能展开，故一条记录覆盖该座位此刻与**此后获得**的全部非锁定**武将**
        技能；装备牌 / 虚拟装备带来的技能不失效（用户裁定，票 09）。锁定技不受影响。
        ``skill_id`` 是**施加者**（【义绝】/【铁骑】），不是被挡的技能。
        作用域 ``TURN`` = 本回合，回合末由 :meth:`_clear_turn_effects` 清掉并落一条
        ``suppressed=False``。返回值 = 状态真的变了吗。
        """
        player = self.state.player(seat)
        if any(record.skill_id == skill_id for record in player.suppressed_skills):
            return False
        player.suppressed_skills.append(SkillSuppression(skill_id=skill_id))
        self.emit_delta(
            SkillSuppressDelta(
                seat=seat, skill_id=skill_id, suppressed=True
            ),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        # 失效也算"来源实例不再解析"：随来源消失的牌标签立刻清掉（票 09 验收）。
        self._prune_card_tags()
        return True

    def set_hand_card_prohibition(self, seat: int, prohibited: bool) -> bool:
        """写一个座位的"**不能使用或打出手牌**"状态（M6 票 04 的【义绝】黑色分支）。

        座位级、作用域 = 本回合（回合末清掉）；**不发 delta**（与 ``luoyi_active`` 同一
        姿态：回放按同一条技能动作重跑即可重建，目前也没有 JSON 读者）。候选生成侧的唯一
        读取方是 :func:`sgs.effects.legal_actions_for`，故"哪些动作算使用 / 打出手牌"
        只有一处判据。返回值 = 状态真的变了吗。
        """
        player = self.state.player(seat)
        if player.hand_card_prohibited == prohibited:
            return False
        player.hand_card_prohibited = prohibited
        return True

    def add_heart_slash_bonus(self, seat: int, target: int) -> None:
        """记下"本回合 ``seat`` 使用红桃【杀】对 ``target`` 的伤害 +1"（【义绝】黑色分支）。

        唯一写入方是【义绝】的效果；读取方是同一条技能定义上的 ``DAMAGE_DEAL_AMOUNT``
        修饰器（查询期从**活着的实例**算，失去 / 失效即消失）。作用域 = 本回合，回合末
        由 :meth:`_clear_turn_effects` 清空；**不发 delta**（同 ``set_hand_card_prohibition``）。
        """
        bonus = self.state.player(seat).heart_slash_bonus_against
        if target not in bonus:
            bonus.append(target)

    def add_hand_limit_bonus(self, seat: int, amount: int) -> None:
        """给 ``seat`` 记一笔**本回合的手牌上限加式增量**（M6 票 08 的唯一写入口）。

        `三国杀详细规则.txt` §四 L334–L336 的"手牌上限"是派生量，spec D11 把它拆成
        **替换式**（界【英姿】②，查询期从活着的实例算）与**加式**（【清俭】/【集智】，
        落座位状态）。本方法只写加式那一轴：``amount`` 累加到 ``Player.hand_limit_bonus``
        （多笔来源可叠加），过期 = 回合末（:meth:`_clear_turn_effects`）。作用座位由**调用方**
        决定（【清俭】传当时的 ``state.turn_seat``、【集智】传持有者本人），写进来之后
        **不再重定向**——D9 的一次性效果不随来源技能失去 / 失效撤回，死者名下的那笔也
        只是再没人读它（回合末统一清掉）。**不发 delta**：与 ``set_paoxiao_bonus`` 同姿态。
        """
        player = self.state.player(seat)
        player.hand_limit_bonus += int(amount)

    def add_card_tag(self, card_id: int, tag: CardTag) -> bool:
        """给一张**实体牌**挂一个标签（M6 票 09 的唯一写入口；spec D12）。

        标签是**牌上的持久事实、与区域无关**：牌在牌堆 / 手牌 / 弃牌堆 / 处理区都照样带它，
        故这里不做任何区域判断。写入口先读登记表（:func:`~sgs.data.card_tag_spec`）——
        **未登记的类型读不到"清除触发"**，在这里就 fail-closed，不让它落进状态。
        同一标签重复挂是幂等的（返回 ``False``）。返回值 = 状态真的变了吗。
        """
        if card_id < 0:
            raise ValueError(
                f"牌 {card_id} 是虚拟牌：虚拟牌不落任何区域、也不持久存在，不能挂标签"
            )
        self.state.card(card_id)  # 未知 card_id ⇒ 越界失败（fail-closed）
        card_tag_spec(tag.tag_type)  # 未登记的类型在写入口拒绝
        tags = self.state.tags_of(card_id)
        if tag in tags:
            return False
        self.state.card_tags[card_id] = (*tags, tag)
        self._emit_tag(TagAddDelta, card_id, tag)
        return True

    def _emit_tag(self, model: type, card_id: int, tag: CardTag) -> None:
        """落一条标签增删 delta（M6 票 20）：牌面 + 标签类型 + 来源实例（帧外坐标 = 0）。"""
        self.emit_delta(
            model(
                card=self.state.card(card_id),
                tag_type=tag.tag_type,
                owner=tag.owner,
                skill_id=tag.skill_id,
            )
        )

    def clear_card_tags(self, trigger: CardTagClear) -> int:
        """按**清除触发**清标签（M6 票 09；D12 的"生存期"唯一执行入口）。

        逐张牌留下"触发不等于 ``trigger``"的标签，清空即删掉该牌的表项；返回清掉的条数。
        M6 只把 ``TURN_END`` 接到真实触发点（:meth:`_clear_turn_effects`）；``LEAVE_ZONE`` /
        ``USED_OR_PLAYED`` 这两类形状**无载体**，触发点随后续里程碑的载体一起接
        （形状由 ``tests/test_tags.py`` 的构造测试钉住）。**不发 delta**：与
        ``set_hand_card_prohibition`` 同姿态——没有 JSON 读者，回放按同一条技能决策重建。
        """
        if not self.state.card_tags:
            return 0
        removed = 0
        for card_id in list(self.state.card_tags):
            tags = self.state.card_tags[card_id]
            gone = tuple(
                tag
                for tag in tags
                if card_tag_spec(tag.tag_type).clear_trigger is trigger
            )
            kept = tuple(tag for tag in tags if tag not in gone)
            removed += len(gone)
            if kept:
                self.state.card_tags[card_id] = kept
            else:
                del self.state.card_tags[card_id]
            for tag in gone:
                self._emit_tag(TagRemoveDelta, card_id, tag)
        return removed

    def _prune_card_tags(self) -> None:
        """清掉来源实例已消失的标签，并逐条落 ``tag_remove``（M6 票 20 / 票 09 的清理入口）。"""
        for card_id, tag in prune_card_tags(self.state):
            self._emit_tag(TagRemoveDelta, card_id, tag)

    def _clear_turn_effects(self) -> None:
        """回合末清理作用域 = ``TURN`` 的座位级效果（M6 票 04；票 05 补【咆哮】②、
        票 08 补手牌上限加式）。

        六样一起清：失效记录（逐条落 ``skill_suppress = false``，使"可恢复"在记录流里
        可被看见）、手牌禁令、红桃【杀】加伤目标、【咆哮】②的"下一次【杀】伤害 +1"
        待消费标记、M6 票 06 的"本回合造成过伤害"（【诛害】的判据）、以及 M6 票 08 的
        手牌上限加式（【清俭】/【集智】的 +X）。与【酒】/【裸衣】同一收尾点
        （:meth:`_turn_after_end`），故"本回合"的作用域不跨回合。
        """
        for player in self.state.players:
            for record in tuple(player.suppressed_skills):
                if record.scope is not SkillSuppressionScope.TURN:
                    continue
                player.suppressed_skills.remove(record)
                self.emit_delta(
                    SkillSuppressDelta(
                        seat=player.seat,
                        skill_id=record.skill_id,
                        suppressed=False,
                        scope=record.scope,
                        predicate=record.predicate,
                    ),
                    frame_id=0,
                    depth=self.stack.depth,
                )
            player.hand_card_prohibited = False
            player.heart_slash_bonus_against.clear()
            player.damage_dealt_this_turn = False
            player.hand_limit_bonus = 0
            self._clear_paoxiao_bonus(player.seat)
        # 牌标签的"本回合不计入"（M6 票 09）：与其它 ``TURN`` 作用域状态同一收尾点，
        # 走声明触发的唯一入口（逐类型读登记表，不在这里特判标签类型）。
        self.clear_card_tags(CardTagClear.TURN_END)
        reset_turn_skill_state(self.state)

    def reveal_card(self, frame: Frame | None, seat: int, card: CardRef) -> None:
        """把 ``seat`` 的一张**手牌**标成公开事实并落一条 ``card_reveal``（M5-06）。

        唯一写入口：目前只有【火攻】的"目标亮 1 张"调它。**只揭示、不移动**——牌仍留在
        原区（所以不是 ``card_move``、不进牌守恒），也**不写任何"已知"状态**：亮牌不是
        持久知识，记录流里的这一条就是全部公开事实（spec §5.1.4 / D10）。``frame`` 是承载
        帧（【火攻】的 RESOLVE 帧），记录坐标因此与那次结算的其它 delta 同帧。
        """
        self.emit_delta(
            CardRevealDelta(seat=seat, card=card),
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )

    # -- 询问 ---------------------------------------------------------------

    @staticmethod
    def _ask_identity(
        kind: QueryKind, actor: int, frame: Frame | None
    ) -> tuple[object, ...]:
        """一次询问的**逻辑身份**（票 02 的应答归属）：同一问重入时不变，不同的问不同。

        = ``(kind, actor, 发问帧身份)``；发问帧身份见 :func:`_ask_origin`。带承载帧的询问
        必须把帧传进来；``None`` 只留给真的没有承载帧的发问（回合环那两问）。身份**不**含
        询问序号：序号每次重入都新取一个，拿它当身份会把"同一问的重入"认成新的一问。
        """
        return (
            kind,
            actor,
            None if frame is None else _ask_origin(frame),
        )

    def ask(
        self,
        build: Callable[[int], Query],
        *,
        identity: tuple[object, ...],
    ) -> Action | _Suspend:
        """把一次询问交给参与者；**只有这一问自己**被接受过时，才直接返回那条应答。

        票 02（界司马懿【反馈】）：派发环在跑完一条 effect（它压出了子帧）之后**继续**问下
        一个实例，于是恢复点落在新子帧上——被挂起的那一问还没重入，应答却先回来了。旧实现
        「``_answer`` 非空就交还」让下一问（无懈收集式窗口）吃掉它；那一问的候选里根本没有
        这条动作，fail-closed 于是抛 ``NotImplementedError``。

        故应答按询问身份记账（:meth:`_ask_identity`）：这里只取走 ``identity`` 名下那条；
        取不到就照常建询问、挂起。**被孤立的应答不丢**（:attr:`_answers`）——它那一问真正重入
        时取走，不重问（``CODING_STANDARDS.md`` §3）。这是对侦察稿选项 (d) 的**落地读法**：
        侦察稿写的是"被孤立的应答随下一次 ``submit`` 覆盖、那一问重问一遍"，本项目按 §3 改成
        "留到它自己那一问重入"；上限与淘汰是登记过的程序化约定，见
        :data:`_ANSWER_BACKLOG_LIMIT`。

        **身份粒度** = ``(kind, actor, 发问帧身份)``：同一帧里同一 kind+actor 的两个不同
        询问仍共用一个身份（要更细得用各帮手的**询问标识**，本票不做）；真的没有承载帧的
        只剩回合环那两问（``PLAY_PHASE`` / ``DISCARD``），它们没有帧号可区分，只按
        ``(kind, actor)`` 认同一问。
        """
        accepted = self._answers.pop(identity, None)
        if accepted is not None:
            answer, structured = accepted
            # 复合动作的载荷按**对象身份**认领（:meth:`_take_structured_activation`）：应答
            # 可能在别的询问先回来之后再被取走，标记不能指望一直躺在引擎上。
            self._structured_activation = structured
            return answer
        seq = self._next_seq()
        query = build(seq)
        self._emit(QueryRecord(seq=seq, query=query))
        self.pending = query
        self._asked_identity = identity
        return SUSPEND

    def _answer_to(self, action: Action) -> None:
        """把被接受的应答记在**接受它的那一问**名下（票 02 的应答归属），并清掉挂起态。

        键 = ``ask`` 挂起时记下的 :attr:`_asked_identity`：别的询问（哪怕同一 actor、同一帧）
        取不到它，只有把挂起态推进到它自己那一问，才取得走。
        """
        assert self._asked_identity is not None, "被接受的应答必须有它所属的那一问"
        if len(self._answers) >= _ANSWER_BACKLOG_LIMIT:
            # 病态局面的兜底（见 _ANSWER_BACKLOG_LIMIT）：淘汰最旧的一条，它那一问会重问。
            self._answers.pop(next(iter(self._answers)))
        self._answers[self._asked_identity] = (action, self._structured_activation)
        self._asked_identity = None
        self.pending = None

    def submit(self, response: object) -> None:
        """接受一个应答；合法性 = **成员判定**（含 W4-1 的复合动作例外）。

        三类非法输入都在这里拒绝（票 24）：**结构损坏**（payload 不在行动 schema 内）/
        **应答序号陈旧**（``answers_seq`` 失配）/ **不在候选集合内**。三者都先写一条
        ``accepted = false`` 的 ``action`` 记录再抛 :class:`IllegalActionError`——
        拒绝本身也必须可观测；被拒前不碰任何游戏状态，也不重试、不超时。

        **W4-1 的例外**：``ActivateSkill`` 还多一条"复合动作"的路——它**不要求**在
        ``legal_actions`` 里，但只对 ``PLAY_PHASE`` / ``SKILL_ACTIVATION`` 开放，且必须
        通过 :func:`sgs.skills.validate_activation`。失败时首因写进拒绝记录的 ``reason``；
        通过时照常落一条 ``accepted = true``，并把动作身份记到
        :attr:`_structured_activation` 供执行侧摊回答案。

        **W4-fix-2 的例外**（用户裁定 A：转化技只经预发动）：转化候选已从
        ``legal_actions`` 收回，故 ``submit`` 的"候选或结构化动作"之外多两条**同源**的路：

        - **转化确认**：``ActivateSkill(sources, targets)``（清单里的来源 / 目标）由
          :func:`sgs.skills.resolve_conversion_confirmation` 对回**内部候选池**里的那条
          普通使用 / 打出动作，通过即以**该动作**落账并执行；失败的首因同样落进拒绝记录；
        - **已解析的转化动作**：回放磁带（``TapePlayer``）与程序化提交直接给那条普通动作，
          成员判定改读内部池 ``transform_actions``——它本来就是本次询问的合法动作池。

        **W5-02a 的第四条路**：``UseCard.designations`` 非空 = 借刀的**附带指定**动作。
        它只对 ``PLAY_PHASE`` 开放，且必须是本次 ``PlayPhaseQuery.designation_actions``
        的成员（同源池，不接受"牌名 + 任意 A/Bn"）；不合格照样写拒绝记录、零状态改动。
        裸借刀的成员判定路径不变（暗发布，收回在 02b）。
        """
        pending = self.pending
        if pending is None:
            raise RuntimeError("当前没有待应答的询问")
        # 上一条经过校验器的复合动作若没被消费就进入了新的询问，标记不能跨询问存活
        # （它只服务"接受 → 建帧 / 派发"这一段）。
        self._structured_activation = None
        self._conversion_answer = None
        try:
            action = parse_action(response)
        except ValidationError as error:
            self._reject(
                MalformedAction(
                    answers_seq=_raw_answers_seq(response),
                    detail=_first_validation_issue(error),
                    raw=_raw_text(response),
                ),
                reason="结构损坏：payload 不在行动 schema 内",
            )
            raise IllegalActionError(
                reason_code="MalformedAction",
                message="应答结构损坏，解析不出任何一种行动构造子",
                expected="UseCard / Pass / EndPhase / DiscardCards / PlayCard / SelectCards 之一",
                got=_raw_kind(response),
                answers_seq=_raw_answers_seq(response),
            ) from error
        if action.answers_seq != pending.seq:
            self._reject(action, reason="answers_seq 陈旧")
            raise IllegalActionError(
                reason_code="StaleAction",
                message="应答的 answers_seq 与当前询问不符",
                expected=str(pending.seq),
                got=str(action.answers_seq),
                answers_seq=action.answers_seq,
            )
        designation_failure = self._designation_failure(action, pending)
        if designation_failure is not _NOT_DESIGNATION:
            if designation_failure is not None:
                # W5-02a：附带指定确实走了池校验，首个失败原因要落进记录（可观测）。
                self._reject(action, reason=f"附带指定校验失败：{designation_failure}")
                raise IllegalActionError(
                    reason_code="IllegalAction",
                    message=designation_failure,
                    expected="按借刀附带指定清单填写的持刀者 / Bn 组合",
                    got=action.kind,
                    answers_seq=action.answers_seq,
                )
            self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
            self._answer_to(action)
            return
        if action not in pending.legal_actions:
            confirmation = self._conversion_confirmation(action, pending)
            if confirmation is not _NOT_CONVERSION:
                if confirmation is not None:
                    # W4-fix-2：转化确认确实走了清单解析，首个失败原因要落进记录。
                    self._reject(action, reason=f"转化确认失败：{confirmation}")
                    raise IllegalActionError(
                        reason_code="IllegalAction",
                        message=confirmation,
                        expected="按转化技清单填写的来源 / 目标",
                        got=action.kind,
                        answers_seq=action.answers_seq,
                    )
                resolved = self._conversion_answer
                assert resolved is not None
                self._emit(
                    ActionRecord(seq=self._next_seq(), accepted=True, action=resolved)
                )
                self._answer_to(resolved)
                return
            if action in pending.transform_actions:
                # 回放磁带 / 程序化提交：转化动作不在**暴露面**里，但仍是本次询问的合法池。
                self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
                self._answer_to(action)
                return
            failure = self._structured_activation_failure(action, pending)
            if failure is _NOT_STRUCTURED:
                self._reject(action, reason="不在候选集合内")
                raise IllegalActionError(
                    reason_code="IllegalAction",
                    message="行动不在候选集合内",
                    expected="候选集合的成员之一",
                    got=action.kind,
                    answers_seq=action.answers_seq,
                )
            if failure is not None:
                # W4-1：结构化动作确实走了校验器，但第一个失败原因要落进记录（可观测）。
                self._reject(action, reason=f"预发动校验失败：{failure}")
                raise IllegalActionError(
                    reason_code="IllegalAction",
                    message=failure,
                    expected="按预发动清单填写的复合动作",
                    got=action.kind,
                    answers_seq=action.answers_seq,
                )
            self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
            self._structured_activation = action
            self._answer_to(action)
            return
        self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
        self._answer_to(action)

    def _designation_failure(
        self, action: Action, pending: Query
    ) -> str | None | object:
        """附带指定（W5-02a）：``None`` = 池成员，``str`` = 首个失败原因，哨兵 = 不是这条路。

        判据只有"提交带不带 ``designations``"与"它是不是**本次** ``PLAY_PHASE`` 的
        ``designation_actions`` 成员"——池由 :func:`sgs.effects.designation_actions_for`
        用既有目标谓词生成，故这里不写第二份 A / Bn 合法性。``answers_seq`` 已在
        ``submit`` 前段校验；池里每项的序号就是这一问的序号，成员判定因此也覆盖它。
        """
        if not isinstance(action, UseCard) or not action.designations:
            return _NOT_DESIGNATION
        if pending.kind != QueryKind.PLAY_PHASE:
            return "附带指定只在出牌阶段开放"
        if action not in pending.designation_actions:
            return "指定的持刀者 / 目标组合不在候选池内"
        return None

    def _conversion_confirmation(
        self, action: Action, pending: Query
    ) -> str | None | object:
        """转化技确认（W4-fix-2）：``None`` = 通过（答案存 :attr:`_conversion_answer`）。

        ``str`` = 首个失败原因；:data:`_NOT_CONVERSION` = 这不是一次转化技确认（不适用），
        交给既有的两条路。判据只有"这条技能此刻算不算转化技"（声明上的
        ``virtual_type_ids``）与"这一问有没有它的清单"——**合法性本身**由
        :func:`sgs.skills.resolve_conversion_confirmation` 在**内部候选池**里解析，
        与清单同源，不写第二份谓词。
        """
        if not isinstance(action, ActivateSkill):
            return _NOT_CONVERSION
        if pending.kind not in (
            QueryKind.PLAY_PHASE,
            QueryKind.DODGE,
            QueryKind.PEACH,
            QueryKind.RESPOND,
        ):
            return _NOT_CONVERSION
        if action.skill_id not in owned_conversion_skill_ids(self.state, pending.actor):
            return _NOT_CONVERSION
        steps = (pending.transform_preactivations or {}).get(action.skill_id, ())
        if not steps:
            return f"技能 {action.skill_id} 此刻没有可用的转化清单"
        resolution = resolve_conversion_confirmation(
            self.state,
            pending.actor,
            action.skill_id,
            action.picks,
            action.choices,
            pending.transform_actions,
            steps=steps,
        )
        if isinstance(resolution, str):
            return resolution
        self._conversion_answer = resolution
        return None

    def _structured_activation_failure(
        self, action: Action, pending: Query
    ) -> str | None | object:
        """复合动作的校验（W4-1）：``None`` = 通过；``str`` = 首个原因；哨兵 = 不适用。

        只有两类询问有资格让"不在候选里"的 ``ActivateSkill`` 通过：

        - ``PLAY_PHASE``：该技能必须在 :attr:`PlayPhaseQuery.preactivations` 里（可用性
          的第二个真源；未迁移的技能仍走骨架候选的成员判定）；
        - ``SKILL_ACTIVATION``：必须是这一问的那条技能、且它随询问下发了清单。

        其余询问（响应窗口 / 选项 / 选牌…）一律不适用——不然一次响应里的
        ``ActivateSkill`` 就能绕过窗口的候选集合。
        """
        if not isinstance(action, ActivateSkill):
            return _NOT_STRUCTURED
        if pending.kind == QueryKind.PLAY_PHASE:
            if action.skill_id not in pending.preactivations:
                return _NOT_STRUCTURED
            frame = None
        elif pending.kind == QueryKind.SKILL_ACTIVATION:
            if action.skill_id != pending.skill_id or not pending.preactivation:
                return _NOT_STRUCTURED
            # 被动入口的池常要读**真帧**（【流离】的使用者 / 【麒麟弓】的目标）：
            # 挂起期间栈顶就是发问的那一帧，校验器必须读同一份上下文。阶段体 / 回合末这类
            # **帧外**时机没有栈顶（帧住 ``_turn_data``），退回发问时记下的承载帧（票 20）。
            frame = self.stack.top if self.stack else self._activation_frame
        else:
            return _NOT_STRUCTURED
        return validate_activation(
            self.state,
            pending.actor,
            action.skill_id,
            action.picks,
            action.choices,
            timing=pending.timing,
            frame=frame,
        )

    def _reject(self, action: Action | MalformedAction, *, reason: str) -> None:
        """把一次被拒应答写进记录流（``accepted = false``）；不碰任何游戏状态。"""
        self._emit(
            ActionRecord(seq=self._next_seq(), accepted=False, reason=reason, action=action)
        )

    def _build_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        seq: int,
        *,
        dying: int | None = None,
        payload: RespondContext
        | SelectContext
        | DodgeContext
        | SkillActivationContext
        | SelectOptionContext
        | ArrangeContext
        | DistributeContext
        | None = None,
        revealed: tuple[CardRef, ...] = (),
    ) -> Query:
        actions = legal_actions_for(
            self.state, actor, kind, answers_seq=seq, dying=dying, payload=payload
        )
        preactivations: dict[str, tuple[Step, ...]] = {}
        designation_actions: tuple[UseCard, ...] = ()
        card_preactivation_manifests: dict[str, tuple[Step, ...]] = {}
        if kind is QueryKind.PLAY_PHASE:
            # W4-1：可用性从**候选回调**来（骨架仍在），清单按同一批技能 id 渲染。
            # 已翻到预发动的技能（``preactivated``）把骨架从候选里删掉——可用性只剩这份
            # 清单；清单渲不出来 ⇒ 该技能此刻不可用（声明错误应当可见，不 fail-open）。
            offered = tuple(
                action.skill_id
                for action in actions
                if isinstance(action, ActivateSkill)
            )
            preactivations = preactivations_for(
                self.state, actor, offered, timing=timing
            )
            flagged = preactivated_skill_ids()
            if flagged:
                actions = tuple(
                    action
                    for action in actions
                    if not (
                        isinstance(action, ActivateSkill)
                        and action.skill_id in flagged
                    )
                )
            # W5-02b：借刀的**附带指定池**与从它派生的两步清单。两者都不进记录流
            # （``exclude``）。裸候选已在候选生成处（`_play_phase`）收回 ⇒ 可用性只剩
            # 这份清单；池服务第四条提交路。
            designation_actions = designation_actions_for(self.state, actor, seq)
            card_preactivation_manifests = card_preactivations(designation_actions)
        # W4-fix-2：转化候选从**暴露面**收回（`legal_actions` 里不再有"红牌直接当
        # 【杀】"），但**内部池**照旧保留——清单 / 确认 / 回放成员判定都读这一份，故
        # "清单 = 候选"没有第二份谓词。
        #
        # W4-fix-6（A）：响应窗口按**语境**（`RespondContext`）而不是按询问种类区分；
        # W5-02b 起借刀杀人的"被迫使用"与其余响应窗口同形（目标由窗口承载）。
        #
        # W7-01b：`fixed_target=True` 只声明"本语境的目标由窗口承载"，**不再**无条件省掉
        # 目标步——`_manifest_steps` 按候选回答：目标完全唯一（求桃 / 【万箭】 / 无方天的
        # 借刀）⇒ 仍只有来源步；多目标候选（W7 的方天式额外目标）⇒ 下发 `1..max` 目标步
        # （借刀窗口的首项 = Bn）。因此这里**不**把 RESPOND 一类简单设成 `False`。
        internal_transform_actions = transform_candidates(kind, actions)
        transform_preactivations = conversion_preactivations(
            self.state,
            actor,
            internal_transform_actions,
            fixed_target=kind in (
                QueryKind.PEACH,
                QueryKind.DODGE,
                QueryKind.RESPOND,
            ),
        )
        actions = tuple(action for action in actions if not is_transformed_action(action))
        view = project(self.state, actor)
        if kind is QueryKind.PLAY_PHASE:
            return PlayPhaseQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="出牌阶段：使用一张牌，或结束出牌阶段。",
                legal_actions=actions,
                view=view,
                revealed=revealed,
                preactivations=preactivations,
                transform_preactivations=transform_preactivations,
                transform_actions=internal_transform_actions,
                designation_actions=designation_actions,
                card_preactivations=card_preactivation_manifests,
            )
        if kind is QueryKind.DISCARD:
            player = self.state.player(actor)
            count = discard_count(self.state, player)
            return DiscardQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt=f"弃牌阶段：请弃置 {count} 张手牌。",
                legal_actions=actions,
                view=view,
                count=count,
                revealed=revealed,
            )
        if kind is QueryKind.DODGE:
            return DodgeQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="以你为目标的【杀】生效前：是否使用【闪】？",
                legal_actions=actions,
                view=view,
                revealed=revealed,
                transform_preactivations=transform_preactivations,
                transform_actions=internal_transform_actions,
            )
        if kind is QueryKind.PEACH:
            if dying is None:
                raise ValueError("PEACH 询问必须给出濒死者座位")
            return PeachQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt=f"座位 {dying} 处于濒死状态：是否对其使用【桃】？",
                legal_actions=actions,
                view=view,
                dying=dying,
                revealed=revealed,
                transform_preactivations=transform_preactivations,
                transform_actions=internal_transform_actions,
            )
        if kind is QueryKind.RESPOND:
            if not isinstance(payload, RespondContext):
                raise ValueError("RESPOND 询问必须给出 RespondContext")
            if payload.mode is RespondMode.USE:
                prompt = "响应窗口：请使用一张牌，或放弃。"
            elif payload.mode is RespondMode.REJUDGE:
                # 改判**不是打出**（feng-zhangjiao 票 02）：窗口文案按"用一张牌代替 / 替换"
                prompt = "改判窗口：用一张牌代替此判定牌，或放弃。"
            else:
                prompt = "响应窗口：请打出一张牌，或放弃。"
            return RespondQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt=prompt,
                legal_actions=actions,
                view=view,
                cause=payload.cause,
                required=payload.required,
                target=payload.target,
                window_frame=payload.window_frame,
                revealed=revealed,
                transform_preactivations=transform_preactivations,
                transform_actions=internal_transform_actions,
            )
        if kind is QueryKind.SELECT_CARDS:
            if not isinstance(payload, SelectContext):
                raise ValueError("SELECT_CARDS 询问必须给出 SelectContext")
            return SelectCardsQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="请从选择面里选择一张牌。",
                legal_actions=actions,
                view=view,
                spec=payload.spec,
                slots=payload.slots,
                skill_id=payload.skill_id,
                provide_type=payload.provide_type,
                revealed=revealed,
            )
        if kind is QueryKind.SKILL_ACTIVATION:
            if not isinstance(payload, SkillActivationContext):
                raise ValueError("SKILL_ACTIVATION 询问必须给出 SkillActivationContext")
            return SkillActivationQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="是否发动该技能？",
                legal_actions=actions,
                view=view,
                skill_id=payload.skill_id,
                optional=payload.optional,
                min_picks=payload.min_picks,
                max_picks=payload.max_picks,
                slots=payload.slots,
                preactivation=payload.preactivation,
                revealed=revealed,
            )
        if kind is QueryKind.SELECT_OPTION:
            if not isinstance(payload, SelectOptionContext):
                raise ValueError("SELECT_OPTION 询问必须给出 SelectOptionContext")
            return SelectOptionQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="请选择一项。",
                legal_actions=actions,
                view=view,
                skill_id=payload.skill_id,
                options=payload.options,
                revealed=revealed,
            )
        if kind is QueryKind.DISTRIBUTE:
            if not isinstance(payload, DistributeContext):
                raise ValueError("DISTRIBUTE 询问必须给出 DistributeContext")
            return DistributeQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="请为角色分配点数。",
                legal_actions=actions,
                view=view,
                skill_id=payload.skill_id,
                seats=payload.seats,
                max_total=payload.max_total,
                per_seat_max=payload.per_seat_max,
                revealed=revealed,
            )
        if kind is QueryKind.ARRANGE:
            if not isinstance(payload, ArrangeContext):
                raise ValueError("ARRANGE 询问必须给出 ArrangeContext")
            return ArrangeQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="请排列这些牌：给出顺序与前几张置于牌堆顶。",
                legal_actions=actions,
                view=view,
                revealed=revealed,
            )
        raise ValueError(f"还没有 {kind.value} 询问的构造器")

    # -- 终局 ---------------------------------------------------------------

    def check_game_end(self, *, draw_reason: str | None = None) -> Outcome | None:
        """唯一终局入口：死亡锚点与牌堆耗尽平局共用（票 13 §3 / 票 08 §6）。

        按 :attr:`RuleConfig.mode` 分支（票 25 / 决策回票 14 §1）：无身份局保持 M1 的
        "存活数 ≤ 1"；身份局按**本批死亡事实**求解（检测点只置**暂定**结果）。
        短摸平局覆盖任何已定胜负（票 14 §3）。
        """
        if draw_reason is not None:
            outcome = Outcome(result="draw", winner=None, reason=draw_reason)
        elif self.state.rule_config.mode is Mode.IDENTITY:
            outcome = self._solve_identity()
            if outcome is None:
                return None
        else:
            alive = [player.seat for player in self.state.players if player.alive]
            if len(alive) > 1:
                return None
            if len(alive) == 1:
                outcome = Outcome(
                    result="winner",
                    winner=alive[0],
                    winners=(alive[0],),
                    reason="last_surviving",
                )
            else:
                outcome = Outcome(result="draw", winner=None, reason="no_survivor")
        self.pending_outcome = outcome
        # check_seq = **本局第一次成功检测**那条记录的序号（票 08 §6 / 票 14 §3）：
        # 同批的后续死亡检测不再改写它。死亡路径上检测点与终止点之间还会 emit
        # 死亡结算的记录，所以必须在此刻定格，不能在 finish() 时再取。
        if self._check_seq is None:
            self._check_seq = self._last_seq
        return outcome

    def record_death_fact(self, frame: Frame) -> None:
        """检测点：把一个 DEATH 帧的死亡事实追加进本批（票 14 §3）。

        由 `DEATH_REVEAL_IDENTITY_AFTER` 的步骤在 :meth:`check_game_end` **之前**显式调用，
        故"记事实"与"求解"不会被栈顶形状悄悄分开。``snapshot`` = 该死亡瞬间**除死者外**
        的存活座位集合（死者的 ``alive`` 已在 :meth:`enter_death` 置假）。
        """
        if self.state.rule_config.mode is not Mode.IDENTITY:
            return
        seat = frame.data["target"]
        self._batch_facts.append(
            DeathFact(
                identity=self.state.player(seat).identity,
                snapshot=tuple(player.seat for player in self.state.players if player.alive),
            )
        )

    def _solve_identity(self) -> Outcome | None:
        """按当前批次事实 + 当前存活集合求一次身份局胜负。"""
        victory = solve_identity_outcome(
            identities=[player.identity for player in self.state.players],
            facts=self._batch_facts,
            alive=[player.seat for player in self.state.players if player.alive],
        )
        if victory is None:
            return None
        return Outcome(
            result="winner",
            winner=None,
            side=victory.side,
            winners=victory.winners,
            reason=victory.reason,
        )

    def _close_death_batch(self) -> None:
        """终止点：同批最后一次 ``DEATH_AFTER`` 之后宣布胜负（票 14 §3）。

        身份局在这里用**批次末存活集合**重解一次（检测点那次只是暂定）；已置的
        **平局**不被重解覆盖（短摸平局覆盖已定胜负，票 14 §3）。未达终局则清空本批事实、
        继续对局。
        """
        if self.state.rule_config.mode is Mode.IDENTITY and (
            self.pending_outcome is None or self.pending_outcome.result == "winner"
        ):
            outcome = self._solve_identity()
            if outcome is not None:
                self.pending_outcome = outcome
        if self.pending_outcome is not None:
            self.finish()
        self._batch_facts.clear()

    def finish(self) -> None:
        """终止点：清栈（逐帧记被丢弃的帧）→ 终局收尾 → 终局记录（流里最后一条）。

        清栈顺序 = **内层在前**（票 04 §5 的 `DISCARD_FRAME` 顺序可在记录流里断言）：
        死亡路径实测丢弃 `NEAR_DEATH / LIFE_DECREASE / DAMAGE / RESOLVE / USE` 五帧。
        收尾 = 补做"使用结算结束时"，把处理区遗留的实体牌一次性置入弃牌堆
        （`reason = USE`，票 07 §6 的终局收尾；它是一次原语调用，不受压栈守卫约束），
        使**牌守恒含终局**无条件成立。终局记录携带检测点那条记录的 ``seq``。
        """
        if self.finished:
            return
        while self.stack:
            self._emit_frame("discard", self.stack.pop())
        outcome = self.pending_outcome
        if outcome is None or self._check_seq is None:
            raise RuntimeError("没有终局判定就清栈")
        self._teardown_processing()
        self._emit(
            GameEndRecord(
                seq=self._next_seq(),
                result=outcome.result,
                winner=outcome.winner,
                side=outcome.side,
                winners=outcome.winners,
                reason=outcome.reason,
                check_seq=self._check_seq,
            )
        )
        self.finished = True

    def _teardown_processing(self) -> None:
        """终局收尾：处理区实体牌一次性置入弃牌堆（补做 L5.6，票 07 §6）。

        这是"无角色主体"的收尾移动（处理区是共享区、结算已经结束），故归属为 ``None``。
        """
        if not self.state.processing:
            return
        delta = move_card(
            self.state,
            EffectContext(actor=self.state.rule_config.first_seat),
            self.state.refs(tuple(self.state.processing)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.USE,
            owner=None,
        )
        self.emit_delta(delta)

    # -- 驱动 ---------------------------------------------------------------

    def run(self, participants: Sequence[Participant]) -> tuple[JournalRecord, ...]:
        """推进到终局；每次询问交给 ``participants[actor]`` 应答。"""
        while not self.finished:
            if self.pending is None:
                self._step()
                continue
            query = self.pending
            self.submit(participants[query.actor].respond(query))
        return self.records

    def _flush_pending_move(self) -> None:
        """把被钩子挂起的 ``MOVE_AFTER`` 派发完（票 07；M6 票 08 补"帧私有续跑"）。

        询问刚被应答、下一帧还没跑时调用：此时钩子重入需要的那条应答已经记在**它那一问**
        名下（票 02 的应答归属；续跑帧与承载帧是同一问，见 :func:`_ask_origin`），故续跑会
        把它取走。没有挂起的移动时是**零动作**。

        **续跑帧** = 挂起时记下的那条临时帧（:meth:`_resume_frame`）：询问缓存与效果游标
        都在它上面，多步流水线（【清俭】）因此不会从头重问、也不会把下一条应答错配给
        上一个询问。**时机记录去重**按旧语义复刻：承载帧的 ``_pending_move_timing`` 若在
        挂起期间被同帧后续移动覆盖，这里要补记一条 ``MOVE_AFTER``（38 号冻结基准录了它）；
        帧外移动（无承载帧）走 ``_move_recorded`` 同一条判据。

        **按栈顶逐层续跑**（票 25）：栈里更深的一层先答完，外层才接着跑；续跑期间新产生的
        挂起项落在当前项之上。正常情况下 ``"stay"`` 会一路传上来，**当前项仍在栈顶**，这一轮
        就直接返回；若某个调用方吞掉了 ``"stay"``（契约上不许，但既有代码里真出现过），栈顶
        会变成新挂起的那一项——此时也返回，让更深的一层先跑，当前项留在原位等下一轮。
        两种情形都不会出栈错项。旧实现的单槽会在这一步**丢掉外层**（【连营】的第二座永远
        不摸）。
        """
        while self._move_pending:
            entry = self._move_pending[-1]
            event, key, resume_frame, carrying = entry
            if carrying is not None:
                if carrying.data.get("_pending_move_timing") != key:
                    self.emit_timing(
                        Timing.MOVE_AFTER,
                        frame_id=0,
                        depth=self.stack.depth,
                        actor=event.actor,
                        source=carrying.data.get("source"),
                        targets=(),
                        cards=event.cards,
                    )
            elif self._move_recorded != key:
                self.emit_timing(
                    Timing.MOVE_AFTER,
                    frame_id=0,
                    depth=self.stack.depth,
                    actor=event.actor,
                    source=event.actor,
                    targets=(),
                    cards=event.cards,
                )
                self._move_recorded = key
            control = dispatch_move_after(self, resume_frame)
            if control == "stay" or self._move_pending[-1] is not entry:
                return
            if carrying is not None:
                carrying.data.pop("_pending_move_timing", None)
            self._move_pending.pop()

    def _step(self) -> None:
        if self.finished:
            return
        self._flush_pending_move()
        if self.pending is not None:
            # 续跑的 ``MOVE_AFTER`` 派发自己又发问了（M6 票 08【清俭】/ 票 10【连营】
            # 这类会问目标的失去牌后技能）：这一问就是本次 ``_step`` 的产物，必须停在这里。
            # 否则结算栈会继续走、再发一条无关询问把它覆盖，那条应答随后被前一个问句
            # 错配（M6 票 12 的混合局暴露；回归 = test_hero_card_zone 的同名用例）。
            return
        if not self._started:
            self._start()
            return
        if self.stack:
            popped = self.stack.step(self.steps, self)
            if popped is not None:
                self._after_pop(popped)
            if popped is not None and popped.kind is FrameKind.DEATH:
                # DEATH 帧出栈：同批计数 -1；归零即**批次关闭**，此刻才宣布胜负
                # （票 14 §3）。检测点只置了暂定结果，死亡单元永远原子跑完。
                self._deaths_pending -= 1
                if self._deaths_pending == 0:
                    self._close_death_batch()
            return
        self._step_turn()

    def _start(self) -> None:
        self.state.turn_seat = self._first_seat()
        # 轮次从 1 起、本轮起点 = 先手座位（M6 票 11）：自然指针再次轮到它时轮次 +1。
        self.state.round = 1
        self.state.round_start_seat = self.state.turn_seat
        self.state.turn_extra = False
        self.emit_timing(Timing.GAME_START)
        self._started = True

    def _first_seat(self) -> int:
        """先手座位：身份局扫主公座位（从 state 派生）、无身份局读配置（票 13 §4）。

        身份局里 ``rule_config.first_seat`` 不被信任（构造期已被钉为默认 0），
        唯一真源是"谁是主公"这一状态事实。
        """
        if self.state.rule_config.mode is Mode.NO_IDENTITY:
            return self.state.rule_config.first_seat
        for player in self.state.players:
            if player.identity is Identity.LORD:
                return player.seat
        raise RuntimeError("身份局必须先发出一个主公座位（票 13 §4）")

    def _step_turn(self) -> None:
        if self._turn_is_over():
            # 非主公在**自己的回合**内阵亡 ⇒ 剩余阶段整体跳过（票 14 §4 / `flow_death`
            # "死亡结算开始前：若不是主公，则结束其回合"）。死亡段已在 ``DEATH_BEFORE_START``
            # 发过 ``TURN_END``；这里把 pc 直接拨到 ``_turn_after_end``（本步就执行它），
            # 只补 ``TURN_AFTER_END`` 并轮转到下一个存活座位，不再发第二条 ``TURN_END``、
            # 也不让阵亡者摸牌 / 出牌。
            #
            # 跳过阶段 = **放弃正在进行的那一段**，故它的收尾要在这里补做：被跳过的那段可能
            # 正是判定阶段，而延时锦囊的 L5.6 收尾归那一段的循环（见 :meth:`_phase_judge`），
            # 循环再也不会续跑（W4-fix-4）。
            self._settle_abandoned_judgment()
            self._turn_pc = self._turn_steps.index(self._turn_after_end)
        step = self._turn_steps[self._turn_pc]
        if step() == "stay":
            return
        self._turn_pc += 1
        if self._turn_pc >= len(self._turn_steps):
            self._turn_pc = 0
            self._turn_data.clear()
            self._advance_turn()

    def _advance_turn(self) -> None:
        """回合之间：先结算**自然指针与轮次**，再让**额外回合栈**插队接管（M6 票 11 / D13）。

        两件事的次序就是 spec 「回合与轮次」的两句话：

        1. **自然指针只在非额外回合结束时前进**：刚结束的是自然回合 ⇒ 指针走到下一个存活
           座位（``next_seat``）；座位环转过本轮起始座位时轮次 +1 并清空每轮次数。
           刚结束的是**额外回合** ⇒ 指针与轮次**一动不动**，故额外回合不会触发"回到起始
           座位"，「每轮限 X 次」不会被刷新（票面核心反例）。
        2. **待执行栈非空 ⇒ 弹出并接管**（``turn_extra = True``）：额外回合因此**插在**
           自然下家之前，而不是把它挤掉；栈空时下一个回合就是指针指向的那个自然回合。

        轮次 +1 落一条 ``turn_change``——**只在有「每轮限 X 次」载体的对局里**（见
        :class:`~sgs.state.TurnChangeDelta`；没有读者时不落记录，标准金录像逐字节不变）。
        额外回合开始也落一条（``extra=True``），使"同一个座位连着两个回合"在记录流里
        读得出是额外回合而不是新一轮。
        """
        state = self.state
        finished = state.turn_seat
        if not state.turn_extra and finished is not None and state.players:
            # 回合只在**存活座位**之间轮转（票 15 §6）；座位环是唯一真源。
            # 存活 ≤ 1 时终局已抢先，故正常对局必然读到一个存活下家。
            nxt = next_alive_seat(state, finished)
            if nxt is not None:
                state.next_seat = nxt
                state.turn_seat = nxt
                self._finish_round_and_advance(finished, nxt)
        extra = self._pop_extra_turn()
        if extra is not None:
            state.turn_seat = extra
            state.turn_extra = True
            self.emit_delta(TurnChangeDelta(seat=extra, round=state.round, extra=True))
            return
        state.turn_extra = False
        if state.next_seat is not None:
            # 额外回合结束后回到"本来该轮到的那个自然回合"；自然回合结束时它刚被算出来，
            # 这一句因此是幂等的（同一个座位）。
            state.turn_seat = state.next_seat

    def _finish_round_and_advance(self, finished: int, nxt: int) -> None:
        """自然指针从 ``finished`` 走到 ``nxt``：走完一圈 ⇒ 轮次 +1、本轮起点推进到 ``nxt``。

        自然回合只按座位升序环转，故"回到本轮起始座位"在座位环上等价于
        ``pos(nxt) <= pos(finished)``（``pos`` 相对本轮起始座位）。用**环位置**而不是
        "``nxt`` 就是起始座位"，是因为起始座位可能在本轮中途阵亡——那样"回到它"永远不会
        发生、轮次会卡死；越过它同样意味着这一圈走完了。

        ``round_start_seat is None``（人为局面直接从轮中途起跑）⇒ 把**当前回合**
        （``finished``）当作本轮起点，轮次不动；下一次回到它才 +1。
        """
        state = self.state
        start = state.round_start_seat
        if start is None:
            state.round_start_seat = finished
            return
        size = len(state.players)
        if (nxt - start) % size > (finished - start) % size:
            return
        state.round += 1
        state.round_start_seat = nxt
        # 每轮次数随轮次清空（M6 票 11）：清在**新轮开始之前**，故新一轮的第一次
        # 发动读到的就是 0；额外回合不经过这里，故它不会刷新它们。
        reset_round_skill_state(state)
        if round_limit_present(state):
            self.emit_delta(TurnChangeDelta(seat=nxt, round=state.round))

    def _pop_extra_turn(self) -> int | None:
        """弹出待执行的额外回合（**LIFO**；轮到已阵亡的座位时直接丢弃）。

        栈序 = "最后压入的那个紧接当前回合"（官方"插在当前回合结算完毕之后"的直读）。
        阵亡座位的额外回合不执行（没有它的回合），也不落 ``turn_change``——记录流只记
        **真的开始了**的回合。
        """
        while self.state.extra_turns:
            seat = self.state.extra_turns.pop()
            if self.state.player(seat).alive:
                return seat
        return None

    def _turn_is_over(self) -> bool:
        """当前回合角色已阵亡且终局未判 ⇒ 该回合立即结束（票 14 §4 的回合结束分支）。"""
        if self.finished:
            return False
        seat = self.state.turn_seat
        if seat is None:
            return False
        return not self.state.player(seat).alive

    # -- 回合环的每一步 -----------------------------------------------------

    def _turn_before_start(self) -> None:
        """回合开始前（L2.1）：先记时机，再看是不是**翻面跳过**。

        规则集 13.2 的固定流程：轮到该角色的"回合开始前" → **先翻面**（背面 → 正面）→
        **然后跳过整个回合**（准备 / 判定 / 摸牌 / 出牌 / 弃牌 / 结束六阶段全部不执行），
        直接进入下家的"回合开始前"。

        跳过 = **没有进入回合**，故：

        - ``TURN_START`` 不发 ⇒ 该座位的每回合清理（使用次数、【酒】/【裸衣】等加伤的清理点、
          "本回合置入弃牌堆"的花色）都不发生。这正是【界裸衣】"直到你的下个回合开始"在
          被跳过的回合里**不清理**的原因（`06`（§5「程序化约定与偏离一手」）登记了这条）；
        - 六个阶段的时机一个都不发 ⇒ 该角色的所有阶段体技能（准备 / 摸牌 / 出牌 / 弃牌 /
          结束）都不触发；
        - ``TURN_AFTER_END`` 也不发（`_turn_after_end` 读 :data:`SKIPPED_TURN_KEY` 跳过那条
          记录）；但**收尾清理照样跑**——那些作用域都属于上一个真实回合，跑一次是幂等的。
        """
        seat = self.state.turn_seat
        self.emit_timing(Timing.TURN_BEFORE_START, actor=seat)
        if seat is not None and self.state.player(seat).flipped:
            self.set_flipped(None, seat, False)
            self._turn_data[SKIPPED_TURN_KEY] = True
            # 本步返回后 ``_step_turn`` 会 ``_turn_pc += 1``，故拨到收尾步的前一格。
            self._skip_to(self._turn_after_end)

    def _turn_start(self) -> Control:
        """回合开始时（L1.2）：每回合清理 + **帧外钩子派发**（夏侯渊【神速】的首个载体）。

        「回合开始时」是与准备阶段 / 回合结束时同类的**帧外时机**：没有结算帧，钩子却
        必须能提问、能跨重入续跑。故它走与 :meth:`_dispatch_phase_body` 同一条路径
        （`TURN_END` 的【博图】是同一形态的先例）：无钩子时只照记一条 ``TURN_START``
        （记录流与 M2 逐字节相同），有钩子时给它一条不压栈、可 "stay" 的帧。

        **每回合清理只跑一次**（``turn_start_reset`` 闸）：钩子挂起后整个 ``_turn_start``
        会被重入，若不加闸，钩子效果里刚落下的回合级事实（本回合置入弃牌堆的花色等）
        会被第二次清理抹掉——【神速】恰好在回合开始时就会弃置装备牌。
        """
        seat = self.state.turn_seat
        if not self._turn_data.get("turn_start_reset"):
            # 该座位自己的 TURN_START 是使用次数计数器的重置点（票 07 §6）。
            # "本回合置入弃牌堆的牌"（M6 票 11 的【博图】判据）作用域 = **一个回合**：
            # 每个回合（含额外回合）起点清空，故额外回合各自重算、不继承上一个回合的花色。
            self.state.discard_suits_this_turn.clear()
            if seat is not None:
                self.state.player(seat).card_use_counts.clear()
                # 【酒】加伤只在本回合有效（M5-04）：回合收尾已清，这里再幂等兜一次——
                # 两处都走唯一写入口，故"清掉"与"从来没置过"的记录形态相同（无 delta）。
                self._clear_wine_active(seat)
                # 【裸衣】同口径（M6 票 01）：作用域 = 本回合结束，回合收尾已清，这里幂等兜底。
                self._clear_luoyi_active(seat)
                # 界【裸衣】的清理点**只在**这里（M6 票 14）：它的作用域是"直到**你的**下个回合
                # 开始"，故别人的回合末不清、只有该座位自己的回合起点清（上一句是标准版）。
                self._clear_luoyi_until_next_turn(seat)
                # 【咆哮】②的"下一次 +1"同口径（M6 票 05）。
                self._clear_paoxiao_bonus(seat)
            self._turn_data["turn_start_reset"] = True
        return self._dispatch_phase_body(Timing.TURN_START, "turn_start")

    def _phase_prepare(self) -> str | None:
        # 准备阶段体照记；有技能钩子（M3 的【观星】式）时惰性建帧派发（票 10 §③）。
        self.state.phase = Phase.PREPARE
        return self._dispatch_phase_body(Timing.PHASE_PREPARE, "phase_prepare")

    def _dispatch_phase_body(
        self,
        timing: Timing,
        key: str,
        *,
        data: Mapping[str, Any] | None = None,
    ) -> Control:
        """**帧外阶段体**的钩子派发（票 10）：空实现照记，有提供者才惰性建帧。

        ``data``（huo-shenzhouyu-mobile 票 03）：调用点交给钩子的**帧私有事实**
        （本票只有 ``PHASE_DISCARD_END`` 的"本阶段弃置的手牌数"）。它并进阶段体帧
        的 ``data``，从而能被钩子条件读到（``PhaseBodySkill.condition``）；缺省
        ``None`` ⇒ 既有六个调用点的建帧载荷逐字不变。

        阶段体（准备 / 摸牌 / 结束）没有结算帧，而钩子必须能提问并跨重入续跑，故这里给
        它一个**不压栈**的持久帧（住 ``_turn_data``）：它不进记录流、不参与步骤表，
        只作钩子的上下文与帧内答案缓存（与 ``emit_timing`` 给 ``MOVE_AFTER`` 造的
        临时帧同形，区别只是要跨暂停存活）。没有注册在该时机上的实例时**一个帧都不建**，
        记录流与 M2 逐字节相同（票 10 的硬纪律：34 条基准一行不改）。

        同一机制也服务**回合结束时**（``TURN_END``，M6 票 11）：它同样是帧外时机、
        同样要能提问，故不另造一套——区别只在调用点与 ``key``。

        时机记录只发一次（``_turn_data`` 的键作幂等闸）；派发返回 ``"stay"`` 时整个阶段体
        原地重入，重用同一个帧与同一份应答缓存。
        """
        store = self._turn_data
        if key not in store:
            self.emit_timing(timing, actor=self.state.turn_seat)
            if not timing_hooks_present(self.state, timing):
                store[key] = None
                return None
            payload: dict[str, Any] = {
                "source": self.state.turn_seat,
                "targets": (),
                "card": None,
            }
            if data:
                payload.update(data)
            store[key] = Frame(
                kind=FrameKind.RESOLVE,
                data=payload,
                frame_id=0,
            )
        frame = store[key]
        if frame is None:
            return None
        control = dispatch_timing(self, frame, timing)
        if control == "stay":
            return "stay"
        # 阶段体里若有亮牌（【裸衣】）给这条不压栈的帧开了记录身份，收尾时闭合
        # push/pop；没开过则零动作，旧记录流逐字节不变。
        self.close_phase_body_frame(frame)
        store[key] = None
        return None

    def phase_body_skill(self) -> str | None:
        """**阶段体**里正在跑的技能 id（不压栈的那条帧），没有则 ``None``。

        阶段体帧住 ``_turn_data``（见 :meth:`_dispatch_phase_body`），不在结算栈上，故
        接入层的询问上下文（"这是哪条技能在问"）读不到栈顶。这里把那条帧的
        :data:`~sgs.skills.catalog.RUNNING_SKILL_KEY` 读出来——只读、不改行为，服务的是
        【观星】这类阶段体技能的询问提示（item 4）。
        """
        for value in self._turn_data.values():
            if isinstance(value, Frame):
                skill_id = value.data.get(RUNNING_SKILL_KEY)
                if skill_id:
                    return str(skill_id)
        return None

    def _phase_judge(self) -> str | None:
        """判定阶段：**自循环回合步骤**，逐张结算当前回合角色判定区里的延时锦囊（票 10 §3）。

        每轮**重新读** ``reversed(判定区)``（"最后置入"者优先、不预先快照），跳过本阶段
        已结算的牌实例（``judged`` 守卫——官方"然后重复此流程"没写刚判完又放回的牌是否
        重判，本实现取**不重判**以终止循环，程序化约定 U-10-1）。

        结算一张的形态：该牌 ``JUDGE→PROCESSING``（``reason=LOSE``、无归属）→ 压 **RESOLVE**
        帧只跑 L5（``source=None``、``use_timing=PHASE_JUDGE``，**不重发** L4 的六个时机）→
        帧出栈后本循环做 L5.6：dispatch ``RESOLVE_AFTER_END``，再把仍在处理区的牌统一收尾
        （【闪电】顺延到下一个合法目标的判定区，其余弃牌堆，票 11 §4）。
        """
        seat = self._turn_seat()
        if self._turn_data.pop("skip_judge", False):
            # 【神速】①（风包夏侯渊）：**跳过判定阶段**——判定区里的牌保留、一张都不判
            # （§11.5 L1280），阶段体与边界一并越过；返回 None 后回合环的 pc 自然落到
            # ``_phase_draw_start``（与跳过摸牌 / 出牌同形）。
            self.state.phase = Phase.JUDGE
            return None
        if not self._turn_data.get("judge_entered"):
            self.state.phase = Phase.JUDGE
            self.emit_timing(Timing.PHASE_JUDGE, actor=seat)
            self._turn_data["judge_entered"] = True
        judged: list[int] = self._turn_data.setdefault("judge_judged", [])
        inflight = self._turn_data.pop("judge_inflight", None)
        if inflight is not None:
            self._finish_judge_round(seat, inflight)
            judged.append(inflight)
            return "stay"
        card_id = self._next_judge_card(seat, judged)
        if card_id is None:
            return None
        # 判定区装实体牌，但转化延时锦囊（【国色】）代表的是一张**虚拟牌**：先把那层
        # 映射读出来，再搬牌（搬出判定区会清掉映射，见 :func:`~sgs.effects.move_card`）。
        judged_card = self.state.virtual_judge.get(card_id, self.state.card(card_id))
        # 每轮重取的是"仍在判定区"的牌：结算前离开判定区（被拆 / 顺走）的牌自然不会
        # 被选中，其使用流程随之终止（官方 `flow_use.txt:15`）。
        self._move_judge_card_to_processing(seat, card_id)
        self._turn_data["judge_inflight"] = card_id
        resolve = self.push_frame(
            FrameKind.RESOLVE,
            {
                "card": judged_card,
                "source": None,
                "targets": (seat,),
                "use_timing": Timing.PHASE_JUDGE,
            },
            timing=Timing.PHASE_JUDGE,
        )
        self._turn_data["judge_frame_id"] = resolve.frame_id
        return "stay"

    def _next_judge_card(self, seat: int, judged: list[int]) -> int | None:
        """本轮该结算的牌：判定区里**最后置入**且本阶段还没判过的那张。"""
        for card_id in reversed(self.state.player(seat).judge):
            if card_id not in judged:
                return card_id
        return None

    def _move_judge_card_to_processing(self, seat: int, card_id: int) -> None:
        """延时锦囊 判定区 → 处理区（``reason = LOSE``、无角色主体，票 10 §7）。"""
        self._move_cards(
            (card_id,),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.LOSE,
            owner=None,
            actor=seat,
        )

    def _finish_judge_round(self, seat: int, card_id: int) -> None:
        """判定阶段循环的收尾（本循环拥有延时锦囊的 L5.6，票 10 §3.6 / 票 11 §4）。

        dispatch ``RESOLVE_AFTER_END``（``actor = 该座位``、``cards = 该牌``，帧 id 沿用
        刚出栈的 RESOLVE 帧）；仍在处理区的牌按后果 op 是否**消耗**它（【闪电】命中）与
        :func:`~sgs.effects.delayed_settle_target`（【闪电】顺延 / 其余弃牌堆）决定去向。
        """
        card = self.state.card(card_id)
        self.emit_timing(
            Timing.RESOLVE_AFTER_END,
            actor=seat,
            cards=(card,),
            frame_id=self._turn_data.pop("judge_frame_id", 0),
        )
        consumed = self._turn_data.pop("judge_consumed", None)
        if card_id not in self.state.processing:
            return
        target = None if consumed == card_id else delayed_settle_target(self.state, card, seat)
        if target is None:
            to_zone, reason = ZoneRef(Zone.DISCARD), MoveReason.USE
        else:
            to_zone, reason = ZoneRef(Zone.JUDGE, target), MoveReason.PUT
        self._move_cards((card_id,), to_zone=to_zone, reason=reason, owner=None, actor=seat)

    def _settle_abandoned_judgment(self) -> None:
        """回合在判定阶段里被**放弃**时补做那一轮的收尾（W4-fix-4）。

        判定阶段是**自循环阶段步骤**：它压出 RESOLVE 帧后把"这一张判过了、收尾还没做"
        记在 ``_turn_data["judge_inflight"]``，等循环下一轮自己回来收尾（:meth:`_phase_judge`
        的第一支）。若被判定者被这一轮**打死**（【闪电】命中 = 3 点雷电），回合在该帧出栈后
        立即结束（:meth:`_turn_is_over` 的跳过分支）——循环再也不会续跑，收尾就丢了：
        牌留在处理区，直到终局清栈才被顺带搬走（手动验收：闪电生效后效果区一直挂着它）。
        故"放弃一个回合"要把它的收尾一起做掉，而不是把收尾留给一个不会发生的下一轮。

        幂等：``judge_inflight`` 取出即清（回合环走完一圈还会 ``_turn_data.clear()``）；
        牌已不在处理区时 :meth:`_finish_judge_round` 也早退。没有在飞的那张 = 零动作。
        """
        card_id = self._turn_data.pop("judge_inflight", None)
        if card_id is None:
            return
        self._finish_judge_round(self._turn_seat(), card_id)

    def _phase_draw_start(self) -> None:
        self.state.phase = Phase.DRAW
        if self._turn_data.pop("skip_draw", False):
            # 【兵粮寸断】判定非梅花 ⇒ **跳过摸牌阶段**（官方"跳过其摸牌阶段"）：
            # 阶段体与边界一并越过，直奔出牌阶段（M5-06；与【乐不思蜀】跳过出牌阶段同形）。
            self._skip_to(self._phase_play_start)
            return
        self.emit_timing(Timing.PHASE_DRAW_START, actor=self.state.turn_seat)

    def _phase_draw(self) -> str | None:
        seat = self._turn_seat()
        control = self._dispatch_phase_body(Timing.PHASE_DRAW, "phase_draw")
        if control == "stay":
            return "stay"
        if self._turn_data.get("phase_draw_drawn"):
            # 钩子在同一阶段体里提过问：重入时只补完基础摸牌、不重摸。
            return None
        self._turn_data["phase_draw_drawn"] = True
        # M6 票 01：阶段体钩子可以把**基础摸牌数**改写（【突袭】0 = 放弃摸牌、【裸衣】少一张）。
        # 缺省仍是额定 DRAW_COUNT，故无技能时与旧路径逐字节相同。
        count = int(self._turn_data.pop("draw_count", DRAW_COUNT))
        drawn = draw_from_deck(self.state, count, self._shuffle)
        if drawn.shuffle is not None:
            # 重洗只发**一条** deck_shuffle（记洗后顺序），不另发牌移动事件（票 08 §5 / 票 23）。
            self.emit_delta(drawn.shuffle)
        if drawn.cards:
            delta = move_card(
                self.state,
                EffectContext(actor=seat),
                drawn.cards,
                to_zone=ZoneRef(Zone.HAND, seat),
                reason=MoveReason.DRAW,
                owner=seat,
            )
            self.emit_delta(delta)
        if len(drawn.cards) < count:
            # 短摸 ⟺ 两堆皆空：弃牌堆非空时 draw_from_deck 已经重洗补足（票 23）。
            self.check_game_end(draw_reason="both_decks_empty")
            self.finish()
        return None

    def _phase_draw_end(self) -> None:
        self.emit_timing(Timing.PHASE_DRAW_END, actor=self.state.turn_seat)

    def _phase_play_start(self) -> None:
        self.state.phase = Phase.PLAY
        # 出牌阶段限一次的技能计数在本步复位：与"座位自己的 TURN_START 清空
        # card_use_counts"同一重置口径（票 04 §要做什么 2 / 票 07 §6）。
        reset_phase_skill_state(self.state)
        # M6 票 03【克己】："本回合**出牌阶段内**是否使用 / 打出过【杀】"。复位点就是
        # 出牌阶段起点（被【乐不思蜀】跳过时也复位——本阶段一张都没用，事实为假）。
        if self.state.turn_seat is not None:
            self.state.player(self.state.turn_seat).slash_in_play_phase = False
            # M6 票 17【诈降】：正常收尾在 ``_phase_play_end``；这里再兜一次（上一阶段异常
            # 中断时不让"本阶段"跨进新阶段）。
            self._clear_zhaxiang_active(self.state.turn_seat)
        if self._turn_data.pop("skip_play", False):
            # 【乐不思蜀】判定非红桃 ⇒ **跳过出牌阶段**（官方"跳过其出牌阶段"，非跳过回合）：
            # 阶段体与边界一并越过，直奔弃牌阶段（票 26）。
            self._skip_to(self._phase_discard_start)
            return
        self.emit_timing(Timing.PHASE_PLAY_START, actor=self.state.turn_seat)

    def _skip_to(self, step: Callable[[], Any]) -> None:
        """把回合环的 pc 拨到 ``step``：本步返回后 ``_step_turn`` 的 +1 正好落到它。"""
        self._turn_pc = self._turn_steps.index(step) - 1

    def _phase_play_idle(self) -> str | None:
        seat = self._turn_seat()
        if not self._turn_data.get("play_idle_emitted"):
            self.emit_timing(Timing.PHASE_PLAY_IDLE, actor=seat)
            self._turn_data["play_idle_emitted"] = True
        action = self.ask(
            lambda seq: self._build_query(QueryKind.PLAY_PHASE, seat, Timing.PHASE_PLAY_IDLE, seq),
            identity=self._ask_identity(QueryKind.PLAY_PHASE, seat, None),
        )
        if action is SUSPEND:
            return "stay"
        if action.kind == "EndPhase":
            return None
        if isinstance(action, ActivateSkill):
            # 主动技（票 04）：压 SKILL 帧跑发动主体，出栈后回到本步重新询问。
            self.start_skill(action, source=seat)
            return "stay"
        if isinstance(action, Reforge):
            # 重铸（M5-03）：压 MOVE 帧跑"弃置 + 摸一张"，出栈后回到本步重新询问。
            self.start_reforge(action, source=seat)
            return "stay"
        # 使用一张牌：压入 USE 帧；子帧结算完再回到本步重新询问（票 07 §3）。
        # W5-02a：借刀的附带指定（Bn）随动作进帧，其余每一条使用都是空元组。
        self.start_use(
            action.card,
            source=seat,
            targets=action.targets,
            designations=action.designations,
        )
        return "stay"

    def _phase_play_end(self) -> None:
        self.emit_timing(Timing.PHASE_PLAY_END, actor=self.state.turn_seat)
        # M6 票 17 界【诈降】：三条加成的"本阶段"作用域在阶段**结束**就清掉（不是等下一个
        # 出牌阶段起点）——否则别人的回合里被"借刀杀人"被迫使用的【杀】会错误地吃到加成。
        if self.state.turn_seat is not None:
            self._clear_zhaxiang_active(self.state.turn_seat)

    def _phase_discard_start(self) -> str | None:
        """弃牌阶段开始：发 ``PHASE_DISCARD_START``，有阶段体钩子（【克己】）时惰性建帧。

        跳过 = 阶段体钩子经 :meth:`skip_discard_phase` 置的状态，在这里消费：DISCARD
        询问与 ``PHASE_DISCARD_END`` 一并越过（与【乐不思蜀】/【兵粮寸断】的跳过同形），
        直奔结束阶段。
        """
        self.state.phase = Phase.DISCARD
        control = self._dispatch_phase_body(
            Timing.PHASE_DISCARD_START, "phase_discard_start"
        )
        if control == "stay":
            return "stay"
        if self._turn_data.pop("skip_discard", False):
            self._skip_to(self._phase_finish)
        return None

    def _phase_discard(self) -> str | None:
        seat = self._turn_seat()
        if not self._turn_data.get("discard_emitted"):
            self.emit_timing(Timing.PHASE_DISCARD, actor=seat)
            self._turn_data["discard_emitted"] = True
        player = self.state.player(seat)
        # 弃到**计数**回落为止（票 09 的排除计数标签）：玩家可以弃掉"不计数"的那张
        # （它仍是手牌），但那不会让 ``hand_count`` 下降 ⇒ 必须再问一次，直到计数 ≤ 上限。
        # 只问一次会让"弃掉带标签的牌"留下计数仍超上限的非法边界（M6 票 12 的混合局
        # 由 fuzz 抓到）。
        while discard_count(self.state, player) > 0:
            action = self.ask(
                lambda seq: self._build_query(QueryKind.DISCARD, seat, Timing.PHASE_DISCARD, seq),
                identity=self._ask_identity(QueryKind.DISCARD, seat, None),
            )
            if action is SUSPEND:
                return "stay"
            # 弃牌阶段没有承载帧，但"弃置"也是**失去**（票 07 §要做什么 5）：走同一个移动入口，
            # 由它按惰性守卫派发 `MOVE_AFTER`；记录坐标（frame_id=0 / depth=stack.depth）与
            # 既有那条 `card_move` 一致——无武将时零派发，M2 基准一行不变。
            self.move_in_frame(
                None,
                action.cards,
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=seat,
                actor=seat,
            )
            # **本阶段实际弃置的手牌数**（huo-shenzhouyu-mobile 票 03）：【琴音】的
            # 触发条件读它。累计点在这里而不是搬牌原语：弃牌阶段的唯一弃置来源就是
            # 这一循环，且 ``move_in_frame`` 返回时就已搬完（``"stay"`` 分支也要计，
            # 挂起重入不能漏）。
            self._turn_data["discard_phase_hand_discards"] = (
                int(self._turn_data.get("discard_phase_hand_discards", 0))
                + len(action.cards)
            )
        return None

    def _phase_discard_end(self) -> Control:
        """弃牌阶段结束时（huo-shenzhouyu-mobile 票 03）：**帧外时机 + 钩子派发**。

        与 ``_phase_finish`` 同形：没有注册在该时机上的实例时只落一条时机记录
        （与 ``emit_timing`` 的旧实现逐字节相同），有则惰性建阶段体帧、把"本阶段
        弃置的手牌数"交给钩子条件。【琴音】是第一个消费者。
        """
        return self._dispatch_phase_body(
            Timing.PHASE_DISCARD_END,
            "phase_discard_end",
            data={
                "discard_phase_hand_discards": int(
                    self._turn_data.get("discard_phase_hand_discards", 0)
                )
            },
        )

    def _phase_finish(self) -> str | None:
        self.state.phase = Phase.FINISH
        return self._dispatch_phase_body(Timing.PHASE_FINISH, "phase_finish")

    def _turn_end(self) -> str | None:
        """回合结束时（L2.24）：**帧外时机**，与阶段体同一套惰性派发（M6 票 11）。

        没有注册在该时机上的实例时一个帧都不建、只落时机记录——记录流与 M5 逐字节相同
        （标准 47 条金录像的前提）。界吕蒙【博图】是第一个消费者：它问"是否发动"、
        答发动就把一个额外回合压进待执行栈，故本步可以返回 ``"stay"``（钩子提过问），
        回合环于是原地重入本步、复用同一个帧与同一份应答缓存。
        """
        return self._dispatch_phase_body(Timing.TURN_END, "turn_end_body")

    def _turn_after_end(self) -> str | None:
        # 回合彻底走完（含"回合内阵亡 ⇒ 跳过整段、直接落到本步"的路径）：
        # M6 票 10 先把"当前回合结束时归还"的武将牌放置区整批还回去。它走帧外移动，
        # 钩子提问会挂起 ⇒ 返回 "stay" 让回合环原地重入本步（已还完的区是空区，天然
        # 跳过）；故这一步必须在清回合状态**之前**做——归还发生在"本回合"里。
        if self.return_hero_card_zones() == "stay":
            return "stay"
        # 才清【酒】加伤：它只在**本回合**有效，不能留到别人的回合里（M5-04）。清理走
        # 唯一写入口，故置过而没用掉的那一份会留下一条 ``wine_change = false``。
        seat = self.state.turn_seat
        if seat is not None:
            self._clear_wine_active(seat)
            # 【裸衣】的作用域是"直到回合结束"（§九 L968–970 / M6 票 01）：同一收尾点清掉。
            self._clear_luoyi_active(seat)
        # 技能失效 / 手牌禁令 / 红桃【杀】加伤的作用域 = 本回合（M6 票 04）：同一收尾点清掉。
        self._clear_turn_effects()
        # 【酒】的"每回合一次"按**游戏回合**记（spec §5.1 酒）：濒死自救可能发生在别人的
        # 回合、记在濒死者身上，故回合末必须清**所有座位**的 wine 计数键，不能只清回合角色。
        # 只动这一个键：其它键（【杀】等）仍按座位自己的 ``TURN_START`` 重置——【杀】的
        # 计数只在**他自己的出牌阶段**被 ``check_count`` 读（W5-04），回合末这里不动它，
        # 借刀窗口那类回合外读点也看不到差异。
        for player in self.state.players:
            player.card_use_counts.pop(WINE_COUNT_KEY, None)
        # 重入只可能来自上面那次挂起续跑：``TURN_AFTER_END`` 已经落过就不要再落一条。
        # 被翻面跳过的回合**没有进入过**，故也不发"回合结束后"（清理照跑，见
        # :meth:`_turn_before_start`）。
        if not self._turn_data.get("turn_after_end_emitted") and not self._turn_data.get(
            SKIPPED_TURN_KEY
        ):
            self._turn_data["turn_after_end_emitted"] = True
            self.emit_timing(Timing.TURN_AFTER_END, actor=self.state.turn_seat)
        return None

    def _turn_seat(self) -> int:
        seat = self.state.turn_seat
        if seat is None:
            raise RuntimeError("回合环启动前没有当前回合角色")
        return seat


_SLOT_BY_SUBCATEGORY: dict[str, SlotKind] = {
    "weapon": SlotKind.WEAPON,
    "armor": SlotKind.ARMOR,
    "horse_defense": SlotKind.HORSE_DEFENSE,
    "horse_offense": SlotKind.HORSE_OFFENSE,
}
"""``CardType.subcategory`` → 装备槽位（票 08 §2）。

宝物槽位（``treasure``）属界限突破、M2 不启用，故不进这张表：它的牌面事实会是
``slot = None``，置入装备区即触发编程错误兜底。
"""


def instantiate_cards(pack: ContentPack) -> tuple[CardRef, ...]:
    """按数据表顺序把内容包实例化成牌实例：``card_id`` 即下标（票 08 §4）。

    牌实例的 id 在开局一次分配、随洗牌一起走；行为字段不在其中（归 ``effects``）。
    装备槽位是**牌面事实**：在实例化时由 ``CardType.subcategory`` 一次派生，
    使移动原语不必拿到 ``ContentPack``（票 08 §2）。攻击范围同姿态（票 12 §1）：
    由 ``CardType.attack_range`` 派生，使 ``attack_range()`` 真读武器牌。
    """
    slots = {
        card_type.id: _SLOT_BY_SUBCATEGORY.get(card_type.subcategory)
        for card_type in pack.card_types
    }
    names = {card_type.id: card_type.name for card_type in pack.card_types}
    ranges = {card_type.id: card_type.attack_range for card_type in pack.card_types}
    return tuple(
        CardRef(
            card_id=index,
            type=deck_card.type,
            name=names[deck_card.type],
            suit=deck_card.suit.value,
            rank=deck_card.rank,
            slot=slots[deck_card.type],
            attack_range=ranges[deck_card.type],
            color=color_of_suit(deck_card.suit),
        )
        for index, deck_card in enumerate(pack.deck.cards)
    )


def _deal_identities(*, seed: int, rule_config: RuleConfig) -> tuple[Identity, ...] | None:
    """身份局逐座发身份；无身份局返回 ``None``（票 13 §3）。

    座位 = 下标固定（只有身份随机）；取配比多重集、用 ``identity`` 子流洗一次，
    **下标 i 的牌发给座位 i**。子流名与创建顺序无关，故回放与"谁先被创建"无关。
    """
    if rule_config.mode is not Mode.IDENTITY:
        return None
    roster = list(roster_for(rule_config.player_count))
    RandomSource(seed).substream("identity").shuffle(roster)
    return tuple(roster)


def _initial_player(
    seat: int, identity: Identity | None, hero: Hero | None = None
) -> Player:
    """开局座位：体力上限 / 势力 / 性别都由**武将数据**派生，主公 +1 的既有初始化段不动。

    无武将（``hero is None``）时三项全部回落到 ``None`` / 引擎常数 ``INITIAL_HP``，故 M2
    行为与 34 条基准不变。初始体力不进记录流（``Player`` 不在 header、也无 delta），
    故这一步不发 delta（票 13 §4）。

    ``gender`` 由票 12 从武将数据接线（票 02 曾把它钉成独立议题，
    ``test_the_gender_field_is_still_a_separate_concern`` 原先断言它恒空）：【结姻】/【离间】
    因此能在**真实对局**里进候选——那是它们唯一的性别读点，也是票 04 遗留议题的收口；
    该断言同票改判为 ``test_the_gender_field_is_filled_from_the_hero_data``。
    """
    hp_max = INITIAL_HP if hero is None else hero.hp_max
    if identity is Identity.LORD:
        hp_max += 1
    return Player(
        seat=seat,
        hp=hp_max,
        hp_max=hp_max,
        identity=identity,
        identity_visible=identity is Identity.LORD,
        gender=None if hero is None else hero.gender,
        hero=None if hero is None else hero.id,
        kingdom=None if hero is None else hero.kingdom,
    )


def build_initial(
    *,
    seed: int,
    rule_config: RuleConfig,
    pack: ContentPack,
    shuffle_source: Randomness | None = None,
    heroes: Sequence[str] = (),
    hero_pool: Sequence[str] | None = None,
    pinned_hero: str | None = None,
    pinned_seat: int = 0,
    hero_config: HeroDealConfig | None = None,
) -> tuple[GameState, tuple[JournalRecord, ...]]:
    """实例化牌堆 → 洗牌 → 发身份（身份局）→ 发武将 → 发牌；顺带产出开局那几条记录。

    ``seq = 1`` 必是初次 ``deck_shuffle``（``frame_id = 0``：无帧哨兵）；身份局的
    ``identity_deal`` 逐座紧随其后（票 13 §3：先发身份 → 亮主 → 发起始手牌），
    再是发牌时机与按座序每座位一条 ``card_move``（票 08 §5 / 票 06 §7）。
    无身份局完全不产生 ``identity_deal``，故 M1 六条基准的 seq 布局不变。
    ``shuffle_source`` 由 :meth:`Engine.new_game` 交进来，好让中局重洗接着同一子流取数。

    **武将发放**（票 02 / 票 12）走唯一随机源的专用子流 ``hero``：``heroes`` 为空 =
    **无武将模式**（M2 的 34 条基准靠它保持有效）、非空 = 显式指定；``hero_pool`` 非
    ``None`` 时从该池按**真名**两段抽取（先抽不同真名、再抽版本）。``pinned_hero`` /
    ``pinned_seat``（票 19 的点将）与 ``hero_config``（票 12 的会话配置：禁将 + 手选）
    都只走 ``hero`` 子流。发放段本身**不产出记录**（武将不是牌、也没有 delta），故关掉
    武将时记录流逐字节不变。

    ``hero_config`` **不进记录流**（与 ``hero_assignments`` 同一条纪律）：禁将 / 手选是
    开局前做过的选择，不是局面事实；它由接入层冻结在会话配置里（票 13），这里只接受并
    遵守它。
    """
    dealt = deal_heroes(
        seat_count=rule_config.player_count,
        stream=RandomSource(seed).substream("hero"),
        heroes=heroes,
        hero_pool=hero_pool,
        pinned_hero=pinned_hero,
        pinned_seat=pinned_seat,
        hero_config=hero_config,
    )
    cards = instantiate_cards(pack)
    identities = _deal_identities(seed=seed, rule_config=rule_config)
    players = tuple(
        _initial_player(
            seat,
            identities[seat] if identities is not None else None,
            None if dealt is None else hero_by_id(dealt[seat], pack_id=pack.pack_id),
        )
        for seat in range(rule_config.player_count)
    )
    state = GameState(
        rule_config=rule_config,
        pack_id=pack.pack_id,
        deck_profile=pack.deck_profile,
        cards=cards,
        players=players,
        deck=list(range(len(cards))),
    )

    rng = shuffle_source if shuffle_source is not None else RandomSource(seed).substream("shuffle")
    rng.shuffle(state.deck)

    records: list[JournalRecord] = [
        DeckShuffleRecord(
            seq=1,
            frame_id=0,
            depth=0,
            cards=state.refs(state.deck),
            from_zone=None,
            to_zone=ZoneRef(Zone.DECK),
        ),
    ]
    seq = 1
    if identities is not None:
        for seat, identity in enumerate(identities):
            seq += 1
            records.append(
                IdentityDealRecord(seq=seq, frame_id=0, depth=0, seat=seat, identity=identity)
            )
    seq += 1
    records.append(
        TimingRecord(seq=seq, frame_id=0, depth=0, timing=Timing.GAME_INITIAL_HAND_DEALING)
    )
    ctx = EffectContext(actor=rule_config.first_seat)
    for player in state.players:
        drawn = draw_from_deck(state, rule_config.starting_hand_size, rng)
        taken = drawn.cards
        if not taken:
            continue
        assert drawn.shuffle is None, "发牌阶段牌堆必然够用，不会重洗"
        delta = move_card(
            state,
            ctx,
            taken,
            to_zone=ZoneRef(Zone.HAND, player.seat),
            reason=MoveReason.DRAW,
            owner=player.seat,
        )
        seq += 1
        records.append(wrap_delta(delta, seq=seq, frame_id=0, depth=0))
    return state, tuple(records)


def new_game(
    *,
    seed: int,
    rule_config: RuleConfig,
    pack: ContentPack,
    heroes: Sequence[str] = (),
    hero_pool: Sequence[str] | None = None,
    pinned_hero: str | None = None,
    pinned_seat: int = 0,
    hero_config: HeroDealConfig | None = None,
) -> GameState:
    """便利包装：造初始状态（实例化牌堆 → 洗牌 → 发身份 → 发武将 → 发牌），不返回记录流。

    要"跑一局并拿到记录流"用 :meth:`Engine.new_game`。``heroes`` / ``hero_pool`` /
    ``pinned_hero`` / ``hero_config`` 同义。
    """
    return build_initial(
        seed=seed,
        rule_config=rule_config,
        pack=pack,
        heroes=heroes,
        hero_pool=hero_pool,
        pinned_hero=pinned_hero,
        pinned_seat=pinned_seat,
        hero_config=hero_config,
    )[0]
