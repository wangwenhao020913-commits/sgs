"""引擎门面：一局的回合环、询问交付与终局。

**低层入口**接受人为指定的局面（:meth:`Engine.from_state`）；:func:`new_game` 是它的
便利包装——实例化牌堆（``card_id = 0..n-1`` 按数据表顺序）→ 洗牌 → 按座序发牌。

引擎只在**询问**处让出控制权（票 04 §3）：``run`` 循环要么推进结算栈 / 回合环一步，
要么把当前询问交给该座位的参与者。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from sgs.agents import Participant
from sgs.data import (
    Color,
    ContentPack,
    Hero,
    Identity,
    Mode,
    RuleConfig,
    color_of_suit,
    deal_heroes,
    load_heroes,
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
    apply_hp,
    behavior_for,
    delayed_settle_target,
    discard_count,
    draw_from_deck,
    legal_actions_for,
    locate,
    move_card,
    targets_within_attack_range,
)
from sgs.resolution import Control, Frame, ResolutionStack, StepTable, Tag, has_tag
from sgs.skills import (
    MOVE_EVENT_KEY,
    RUNNING_SKILL_KEY,
    ActivationPlan,
    MoveEvent,
    SeamKind,
    SkillInstance,
    advance_provider,
    dispatch_move_after,
    dispatch_seam,
    dispatch_timing,
    conversion_preactivations,
    is_transformed_action,
    move_after_providers,
    owned_conversion_skill_ids,
    preactivation_for,
    preactivated_skill_ids,
    preactivations_for,
    provider_registered,
    prune_skill_state,
    reset_phase_skill_state,
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
    DiscardQuery,
    DodgeContext,
    DodgeQuery,
    FrameKind,
    GameState,
    HpChangeDelta,
    IdentityRevealDelta,
    MoveReason,
    Pass,
    PeachQuery,
    Phase,
    PlayPhaseQuery,
    Player,
    Query,
    QueryKind,
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
    Slot,
    SlotKind,
    Step,
    UseCard,
    WindowCause,
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
from .steps import build_steps
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

_MOVE_EVENTS = "move_events"
"""帧私有槽名：本帧已经派发过的 ``MOVE_AFTER`` 次数（票 07 的派发幂等时钟）。

同一帧里可以有**多次移动**（【方天画戟】的额外目标 / 顶替的先旧后新 / 拆顺的两次搬牌），
而带提问的钩子会让 ``move_in_frame`` 从**同一个步骤**重入（帧内游标不前进）。这个计数
让"第 n 次移动"与"第 n 次重入"区分开：这次移动的幂等键带上它的序号
（见 :meth:`Engine._move_key`），故"同一次移动"只落一条记录、只派发一遍。
帧外移动没有帧可挂标记，那份幂等键住 :attr:`Engine._move_recorded`。
"""

_JUDGE_SUBSTITUTION = "judge_substitution"
"""帧私有槽名：一次改判「**代替**」的在途进度（票 11 §② 的重入；code-review C2）。

``{"old": CardRef, "new": CardRef, "seat": int, "stage": int}``：``stage`` = 已搬完的段数
（0 = 都还没搬、1 = 新牌已进处理区、2 = 原判定牌也已进弃牌堆）。**原判定牌在首次进入时
定格**，故重入不会把它错读成刚写进 ``judge_card`` 的新牌；``stage`` 让重入跳过已搬段，
既不搬第二遍、也不会把原判定牌遗留在处理区。``resume`` 在
:func:`~sgs.engine.steps.judge_before_effect` 顶部消费它。
"""


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
    ``channel`` = 使用的实体牌、``nature`` 恒 ``NORMAL``、``chain`` 恒 ``False``。

    ``targets`` 由效果执行器显式给**当前目标**（票 27）：多目标结算里每一层效果都只
    作用于 ``targets[cursor]``，子帧因此不会把整张牌的目标列表继承下去。
    """
    data: dict[str, Any] = {
        "source": parent_data.get("source"),
        "targets": tuple(parent_data.get("targets", ())) if targets is None else tuple(targets),
        "card": parent_data.get("card"),
        "use_timing": parent_data.get("use_timing"),
    }
    for key, value in scalars:
        if key in data:
            raise ValueError(f"行为表的标量 {key!r} 会覆盖子帧继承的字段")
        data[key] = value
    if kind is FrameKind.DAMAGE:
        targets_value = data["targets"]
        data["target"] = targets_value[0] if targets_value else None
        data["channel"] = data["card"]
        data["nature"] = DamageNature.NORMAL
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


class Engine:
    """一局的驱动器：显式结算栈 + 回合环 + 记录流。"""

    def __init__(
        self,
        state: GameState,
        *,
        seed: int,
        init_records: Sequence[JournalRecord] = (),
        shuffle_source: Randomness | None = None,
    ) -> None:
        self.state = state
        self._records: list[JournalRecord] = [
            Header(
                seed=seed,
                pack_id=state.pack_id,
                deck_profile=state.deck_profile,
                rule_config=state.rule_config,
            )
        ]
        self._records.extend(init_records)
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

        self._answer: Action | None = None
        self._structured_activation: ActivateSkill | None = None
        """最近一次**经校验器接受**的复合 ``ActivateSkill``（W4-1）。

        ``submit`` 的成员判定之外只有这一条让开的路：复合动作不在 ``legal_actions`` 里，
        但它的 ``picks`` / ``choices`` 是**实体牌 id / 座位**（不是面内位置），后续
        ``start_skill`` / ``ask_skill_activation`` 据这个身份把载荷原样挂进帧，交给
        ``run_flow`` 跳过本人应答的步骤——而不是当成面内位置去索引。
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
        self._move_pending: tuple[MoveEvent, tuple, Frame | None] | None = None
        """被钩子挂起的 ``MOVE_AFTER``（票 07）：``(载荷, 幂等键, 承载帧或 None)``。

        "失去牌后"是**内联时机**，它的钩子可以提问。提问把控制权交回驱动循环之后，
        重入**不一定落在同一个步骤上**（拆顺会继续跑效果帧的后续步骤、使用链会继续跑
        L4/L5），故挂起状态存在引擎上，由 :meth:`_flush_pending_move` 在"引擎空闲"处
        接着派发完——这是"移动入口不建帧"（R1-Q2）的代价，也是它的唯一落点。
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
    ) -> "Engine":
        """造初始状态（实例化 → 洗牌 → 发身份 → 发武将 → 发牌）并带上开局那几条记录。

        初次洗牌与后续重洗共用同一个 ``shuffle`` 子流句柄：重洗从初次洗牌之后的位置继续取数
        （票 08 §3），因此同一 seed 的整局（含每一次重洗）逐字节可复现。
        ``heroes`` / ``hero_pool`` 与 :func:`build_initial` 同义（票 02）：缺省 = 无武将模式。
        ``pinned_hero`` / ``pinned_seat`` 同义（票 19 的点将：钉住一个座位、其余随机）。
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
        )
        return cls(
            state, seed=seed, init_records=init_records, shuffle_source=shuffle_source
        )

    @classmethod
    def from_state(cls, state: GameState, *, seed: int) -> "Engine":
        """低层入口：从**人为指定的局面**起跑（只写 header，不虚构开局记录）。"""
        return cls(state, seed=seed)

    # -- 记录流 -------------------------------------------------------------

    @property
    def records(self) -> tuple[JournalRecord, ...]:
        return tuple(self._records)

    def journal(self) -> str:
        """canonical JSONL（唯一序列化器的入口）。"""
        return dump_journal(self._records)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _emit(self, record: JournalRecord) -> None:
        self._records.append(record)
        seq = getattr(record, "seq", None)
        if seq is not None:
            self._last_seq = seq

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
        if not move_after_providers(self.state):
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

    def discard_dead_cards(self, frame: Frame) -> None:
        """``DEATH_ON`` Ⅰ.a：把死亡角色区域里所有的牌置入弃牌堆（规则 4.8 第 2 步）。

        逐来源区各发一条 delta（票 08 §7）：手牌 / 装备区 / 判定区各一条，**不**新增复合
        delta。归属 = 阵亡者本人（弃置者）；判定区的实体牌**直接**进弃牌堆（不经过处理区）。
        M1 的 ``basic_53`` 没有装备与延时锦囊，故实测仍只有手牌那一条。
        """
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

    def apply_death_rewards(self, frame: Frame) -> None:
        """``DEATH_ON`` 的 Ⅱ：执行死亡奖惩（票 25 / 决策回票 14 §4.2）。

        奖惩顺序在 Ⅰ（系统处理牌）之后；只进身份局。无来源伤害（闪电）、自杀、
        失去体力 ⇒ ``killer`` 未定格 ⇒ 无奖惩；killer 已阵亡 ⇒ 跳过（保"阵亡者区域为空"）。
        杀反贼 ⇒ killer 摸 3 张；主公杀忠臣 ⇒ **主公**弃置自己的手牌 / 装备区 / 判定区
        （一手 `flow_death.txt`「所有牌」，登记 `R:323` 偏差）。
        """
        if self.state.rule_config.mode is not Mode.IDENTITY:
            return
        killer = frame.data.get("killer")
        if killer is None or not self.state.player(killer).alive:
            return
        dead = frame.data["target"]
        dead_identity = self.state.player(dead).identity
        if dead_identity is Identity.REBEL:
            self._reward_killer_draws(frame, killer)
            return
        if dead_identity is Identity.LOYALIST and self.state.player(killer).identity is Identity.LORD:
            self._lord_discards_everything(frame, killer)

    def _lord_discards_everything(self, frame: Frame, lord: int) -> None:
        """主公杀死忠臣：逐来源区（手牌 / 装备区 / 判定区）各一条 ``card_move`` 入弃牌堆。"""
        ctx = EffectContext(
            actor=lord,
            source=lord,
            targets=(frame.data["target"],),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )
        for source_ref in (
            ZoneRef(Zone.HAND, lord),
            ZoneRef(Zone.EQUIP, lord),
            ZoneRef(Zone.JUDGE, lord),
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
                owner=lord,
            )
            self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)

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
        传播也只可能跑一次。
        """
        self._emit_frame("pop", popped)
        self._propagate_cancel(popped)

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
        control = dispatch_timing(self, frame, timing)
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
        """重入当前正在展开的提供者（票 09）：``(control, satisfied)``，见 ``sgs.skills``。"""
        return advance_provider(self, frame, timing)

    def start_provider(self, frame: Frame, skill_id: str, actor: int, timing: Timing) -> Any:
        """开始一次提供者展开（票 09）：``(control, satisfied)``，见 ``sgs.skills``。"""
        return start_provider(self, frame, skill_id, actor, timing)

    def suspended(self, value: Any) -> bool:
        """``ask`` 的返回值是否为"已暂停等应答"哨兵。"""
        return value is SUSPEND

    def ask_query(
        self,
        kind: QueryKind,
        actor: int,
        timing: Timing,
        *,
        dying: int | None = None,
        payload: RespondContext
        | SelectContext
        | DodgeContext
        | SkillActivationContext
        | SelectOptionContext
        | ArrangeContext
        | None = None,
        revealed: tuple[CardRef, ...] = (),
    ) -> Any:
        """构造并交付一次询问；已有应答（重入）则直接返回它。

        ``revealed`` 是**本次询问授予该参与者的牌面**（票 10 §①，缺省空 = 不授予）：
        它是通用通道，任何询问都能带（【观星】的排列与将来【遗计】的"只看不排"共用），
        随询问结束即失效、不写状态、不进 ``PlayerView``。
        """
        return self.ask(
            lambda seq: self._build_query(
                kind, actor, timing, seq, dying=dying, payload=payload, revealed=revealed
            )
        )

    def ask_arrange(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        cards: tuple[CardRef, ...],
    ) -> Any:
        """发一次 ``ARRANGE`` 询问（票 10 §②）：把 ``cards`` 授予 ``actor`` 并请其排列。

        ``cards`` 是**自顶向下**的被授予牌面（就是这次询问的 ``revealed``）；候选 =
        全部排列 × 全部切分点（:class:`~sgs.state.ArrangeContext`）。答案按
        ``(actor, timing, 牌 id 元组)`` 帧内缓存，钩子重入时不重发（票 07 的帧内记忆姿态）。
        返回 ``ArrangeCards`` / ``SUSPEND``。
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
                payload=ArrangeContext(count=len(cards)),
                revealed=cards,
            )
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
    ) -> Frame:
        """开始一次使用：记一次本回合已用、压入 USE 帧（票 07 §6）。

        ``use_timing`` 覆盖压栈步骤的 ``return_to`` 标签：默认是出牌阶段的
        ``USE_CHOOSE_TARGET``，濒死求桃里出【桃】时是 ``NEAR_DEATH_AT``
        （票 04 §4 的"从哪回来"要读得出是哪个窗口开的使用）。

        **目标顺序在这里定格为"从当前回合角色起、逆时针"**（票 15 §4）：B / C 两段都读
        同一个排好序的列表；使用者与当前回合角色不同（【借刀杀人】迫使的【杀】）时仍按
        当前回合角色起算（U-15-1，程序化约定）。

        ``cancel_target`` 只给**响应型使用**（无懈一类的牌）用（票 11 §2）：记下这次使用指向
        "哪一层的哪个目标"（``(被挂起帧 id, 目标座位)``），由子帧随 ``use_push_resolve``
        继承进它的 RESOLVE 帧，出栈时 :meth:`_propagate_cancel` 据此把那一层标成被抵消。

        ``counts=False`` 用于**不计次数**的使用（【青龙偃月刀】的续杀，票 12）。
        """
        if counts:
            counts_by_type = self.state.player(source).card_use_counts
            counts_by_type[card.type] = counts_by_type.get(card.type, 0) + 1
        data: dict[str, Any] = {
            "card": card,
            "source": source,
            "targets": order_targets(self.state, tuple(targets)),
            "use_timing": use_timing,
        }
        if cancel_target is not None:
            data["cancel_target"] = cancel_target
        return self.push_frame(
            FrameKind.USE,
            data,
            timing=use_timing if use_timing is not None else Timing.USE_CHOOSE_TARGET,
        )

    def current_target(self, frame: Frame) -> int:
        """C 段的**当前目标**：``targets[cursor]``；空目标（响应牌）退回使用者。"""
        targets = tuple(frame.data.get("targets", ()))
        if not targets:
            return frame.data["source"]
        return targets[frame.data.get("cursor", 0)]

    def play_card(self, card: CardRef, *, source: int, timing: Timing) -> Frame:
        """开始一次**打出**：压入 PLAY 帧（L6 两个时机），**不占使用次数**（票 19 §2）。"""
        return self.push_frame(
            FrameKind.PLAY,
            {"card": card, "source": source, "targets": ()},
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
            data["activation"] = {**activation, "skill_id": action.skill_id}
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
        self, frame: Frame, action: UseCard, *, asker: int, dying: int
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
                "card": action.card,
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        self.start_use(
            action.card,
            source=asker,
            targets=(dying,),
            use_timing=Timing.NEAR_DEATH_AT,
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

    def move_used_card_to_equip_zone(self, frame: Frame) -> None:
        """处理区 → 装备区（``reason = PUT``）；同槽已有牌则先被"顶替"出去。

        替换 = **顶替**（票 12 §Q3）：新牌 ``PUT`` 入槽、旧牌 ``LOSE`` 入弃牌堆
        （**不算弃置**，故 M3 的【枭姬】/【旋风】类"失去装备"仍能被触发）。一般"置入"
        撞已占槽位由 :func:`~sgs.effects.move_card` 的编程错误兜底直接抛，只有"使用装备牌"
        这条路径能替换，故两条移动的次序必须是**先旧后新**——先 ``PUT`` 新牌会立刻撞上
        那条兜底（"先新后旧"是票 12 §Q3 自己撤销掉的提案）。
        """
        card = frame.data["card"]
        seat = frame.data["source"]
        if card.is_virtual:  # 虚拟装备不是牌（票 07 §6）：装备区只放实体牌
            return
        for card_id in list(self.state.player(seat).equip):
            if self.state.card(card_id).slot is card.slot:
                self.move_in_frame(
                    frame,
                    (self.state.card(card_id),),
                    to_zone=ZoneRef(Zone.DISCARD),
                    reason=MoveReason.LOSE,
                    owner=seat,
                )
        self.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.EQUIP, seat),
            reason=MoveReason.PUT,
            owner=seat,
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

    def draw_for_hand(self, frame: Frame, seat: int, n: int) -> bool:
        """给 ``seat`` 摸 ``n`` 张（沿用 ``shuffle`` 子流）；短摸 ⇒ 平局并终止。

        与摸牌阶段 / 奖惩摸牌同口径（票 14）：重洗只发一条 ``deck_shuffle``；
        返回 ``False`` 表示两堆皆空、已判平局。
        """
        drawn = draw_from_deck(self.state, n, self._shuffle)
        if drawn.shuffle is not None:
            self.emit_frame_delta(frame, drawn.shuffle)
        if drawn.cards:
            self.move_in_frame(
                frame,
                drawn.cards,
                to_zone=ZoneRef(Zone.HAND, seat),
                reason=MoveReason.DRAW,
                owner=seat,
                actor=seat,
            )
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
        """``seat`` 的三区里是否至少有一张牌（拆 / 顺的第 0 层前提）。"""
        player = self.state.player(seat)
        return bool(player.hand or player.equip or player.judge)

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
    ) -> Any:
        """向 ``actor`` 发一次 ``RESPOND`` 询问；重入直接返回已存应答（票 19 §8）。

        ``mode = USE`` 时候选是"使用某张牌"：``use_targets`` 非空时每个目标一个候选
        （借刀杀人迫使的【杀】），**为空时是无目标的响应使用**（无懈一类的牌，目标由
        ``window_frame`` + ``target`` 承载）；``mode = PLAY`` 是打出（无目标）。

        ``providers_used`` 供"需要【闪】"窗口过滤已问过的装备提供者（票 10 §5.5）。
        """
        payload = RespondContext(
            cause=cause,
            required=required,
            target=target,
            window_frame=window_frame,
            mode=mode,
            use_targets=tuple(use_targets),
            providers_used=tuple(providers_used),
        )
        return self.ask(
            lambda seq: self._build_query(
                QueryKind.RESPOND, actor, timing, seq, payload=payload
            )
        )

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
    ) -> Any:
        """发一次 ``SELECT_CARDS`` 询问；**选择面只构造一次**（帧内记忆，重入复用）。

        ``build_face() -> (slots, card_ids)`` 会消耗一次 ``select`` 子流的随机数，
        故绝不能因重入而重跑；答案回来后把该位置解析成实体 ``card_id`` 元组返回。
        池为空时**不构造询问**、直接返回空元组（票 19 §6）。

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
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("select", *identity)
        if answer_key in answers:
            return answers[answer_key]
        owned_store: dict[Any, Any] | None = None
        if face is None:
            owned_store = frame.data.setdefault("asks", {})
            if identity not in owned_store:
                owned_store[identity] = build_face()
            slots, card_ids = owned_store[identity]
        else:
            slots, card_ids = face
        if not slots:
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
            )
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
        """发一次 ``SKILL_ACTIVATION`` 询问（票 19 §5 / 票 30）：候选 = 付得起的载荷组合。

        载荷（面内位置）烘进 ``ActivateSkill`` 候选；返回 ``ActivateSkill`` / ``Pass``
        / ``SUSPEND``；答案按 ``(skill_id, key)`` 帧内缓存，重入不重发（``key`` 让同一时机
        上的真钩子与帧内接缝不共用缓存槽）。``plan.card_types`` 非空时选取面只铺这些牌名
        （【青龙偃月刀】只让挑【杀】）。选中后的位置解析成实体牌 id，存进
        ``activation_picks`` 供 handler 读。
        """
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("activate", instance.skill_id, key or str(timing))
        if answer_key in answers:
            return answers[answer_key]
        slots: tuple[Slot, ...] = ()
        card_ids: tuple[int, ...] = ()
        face_key: str | None = None
        if plan.zones:
            store = frame.data.setdefault("asks", {})
            face_key = f"activation:{key or timing.value}:{instance.skill_id}"
            if face_key not in store:
                store[face_key] = self.selection_face(
                    instance.owner, plan.zones, reveal_hand=True
                )
            slots, card_ids = store[face_key]
            if plan.card_types:
                kept = [
                    (slot, card_id)
                    for slot, card_id in zip(slots, card_ids)
                    if slot.card is not None and slot.card.type in plan.card_types
                ]
                slots = tuple(slot for slot, _ in kept)
                card_ids = tuple(card_id for _, card_id in kept)
        action = self.ask(
            lambda seq: self._build_query(
                QueryKind.SKILL_ACTIVATION,
                instance.owner,
                timing,
                seq,
                payload=SkillActivationContext(
                    skill_id=instance.skill_id,
                    optional=plan.optional,
                    min_picks=plan.min_picks,
                    max_picks=plan.max_picks,
                    slots=tuple(slots),
                    preactivation=preactivation_for(
                        self.state,
                        instance.owner,
                        instance.skill_id,
                        timing=timing,
                        frame=frame,
                    ),
                ),
            )
        )
        if action is SUSPEND:
            return SUSPEND
        if face_key is not None:
            frame.data.setdefault("asks", {}).pop(face_key, None)
        if isinstance(action, ActivateSkill):
            activation = self._take_structured_activation(action)
            if activation is not None:
                # W4-1：复合动作的 picks 是**实体牌 id**，不是面内位置；原样交给
                # ``run_flow`` 摊回逐步答案，别拿它去索引 ``card_ids``。
                # W4-fix-2：归属随载荷一起走——同一帧上后续别的技能的流水线不得读它
                # （`flow._activation_belongs`）。
                frame.data["activation"] = {**activation, "skill_id": instance.skill_id}
            elif action.picks:
                frame.data.setdefault("activation_picks", {})[instance.skill_id] = tuple(
                    card_ids[index] for index in action.picks
                )
        answers[answer_key] = action
        return action

    def activation_picks(self, frame: Frame, skill_id: str) -> tuple[int, ...]:
        """上一次"是否发动"询问里烘进候选的成本载荷（实体牌 id 元组）。"""
        return tuple(frame.data.get("activation_picks", {}).get(skill_id, ()))

    def ask_option(
        self,
        frame: Frame,
        actor: int,
        timing: Timing,
        *,
        skill_id: str,
        options: tuple[str, ...],
        optional: bool = False,
    ) -> Any:
        """发一次 ``SELECT_OPTION`` 询问（票 12 §Q6 / 票 30）：选项闭集。

        缺省**强制选择**（既有消费者的口径）；``optional=True`` 时候选里多一条 ``Pass``，
        并把它**原样返回**给调用方——"这一步可以取消"的判据由调用方自己定（票 00 / N4）。

        答案按 ``(skill_id, actor)`` 帧内缓存，重入不重发。
        """
        answers = frame.data.setdefault("ask_answers", {})
        answer_key = ("option", skill_id, actor)
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
            )
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
        """判定后果：把"红 = 成功"写回发起判定的帧（票 10 §5.3）。"""
        parent_id = frame.data.get("activation_parent")
        judge_card = frame.data.get("judge_card")
        red = judge_card is not None and color_of_suit(judge_card.suit) is Color.RED
        for candidate in reversed(self.stack.frames):
            if candidate.frame_id == parent_id:
                candidate.data["skill_result"] = red
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

    def substitute_judge_card(self, frame: Frame, *, seat: int, new_card: CardRef) -> Control:
        """改判「**代替**」（票 11 §②）：新牌 手牌 → 处理区、原判定牌 处理区 → 弃牌堆。

        与 :func:`sgs.effects.substitute_judge_card` 的**代替**口径逐字段一致
        （``reason = JUDGE``、归属 = **被判定者**、新牌进处理区、原牌进弃牌堆），
        只是走 :meth:`move_in_frame` 这个"搬牌必记 delta、必派发失去牌后"的公共移动口：
        替代掉的手牌因此照常触发 D 族的"失去牌后"，而不是绕开移动入口直接改状态。

        两条移动各发一条 ``card_move``（来源区不同）。完成时把帧私有 ``judge_card``
        换成新牌 —— 它成为新的"生效前"判定牌（窗口据此重开）。

        **可重入（code-review C2）**：第一次移动可能因内联钩子提问而挂起（例如改判的
        是【连营】持有者的最后一张手牌）。进度记在帧私有 :data:`_JUDGE_SUBSTITUTION`：
        原判定牌**首次进入时定格**、``stage`` 记住已搬段，故重入要么由本方法续跑、
        要么由 :meth:`resume_judge_substitution` 续跑——两者都不会把原判定牌遗留处理区。
        """
        progress = frame.data.get(_JUDGE_SUBSTITUTION)
        if progress is None:
            progress = {
                "old": frame.data["judge_card"],
                "new": new_card,
                "seat": seat,
                "stage": 0,
            }
            frame.data[_JUDGE_SUBSTITUTION] = progress
        return self._advance_judge_substitution(frame, progress)

    def resume_judge_substitution(self, frame: Frame) -> Control:
        """把被打断的改判「代替」接着做完；没有在途替代时是**零动作**（code-review C2）。

        改判窗口是**步骤级**循环：挂起重入时 ``_rejudge_window`` 已经把这个座位记进
        "已问过"，不会再次调 :meth:`substitute_judge_card`。故续跑点放在
        :func:`~sgs.engine.steps.judge_before_effect` 的入口——它先收尾在途替代，
        再开新一轮询问。
        """
        progress = frame.data.get(_JUDGE_SUBSTITUTION)
        if progress is None:
            return None
        return self._advance_judge_substitution(frame, progress)

    def _advance_judge_substitution(self, frame: Frame, progress: dict[str, Any]) -> Control:
        """按 ``progress["stage"]`` 搬完改判的两段移动；每段搬完即记进度、挂起即透传。

        ``stage`` 在**调用移动之前**前进：``move_in_frame`` 返回 ``"stay"`` 时牌**已经**
        搬完（只是钩子的派发还没续跑），故重入必须跳过这一段。
        """
        subject = frame.data["subject"]
        if progress["stage"] < 1:
            progress["stage"] = 1
            control = self.move_in_frame(
                frame,
                (progress["new"],),
                to_zone=ZoneRef(Zone.PROCESSING),
                reason=MoveReason.JUDGE,
                owner=subject,
                actor=progress["seat"],
            )
            frame.data["judge_card"] = progress["new"]
            if control is not None:
                return control
        if progress["stage"] < 2:
            progress["stage"] = 2
            control = self.move_in_frame(
                frame,
                (progress["old"],),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.JUDGE,
                owner=subject,
                actor=subject,
            )
            if control is not None:
                return control
        frame.data.pop(_JUDGE_SUBSTITUTION, None)
        return None

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
    ) -> Control:
        """移动一批牌、记一条 delta，并在 ``move_card`` 之后内联派发 ``MOVE_AFTER``。

        判定路径的"无角色主体"移动（判定牌的两次移动、延时锦囊本体）主体是**被判定者**，
        与 ``source``（可能为 ``None``）分列，故调用点可显式给 ``actor``。

        这也是**效果回调的公共移动口**（票 27）：``OPS`` 里的 op 只经它搬牌，
        故"搬牌必记 delta"这条不变量对使用链与效果链是同一个实现。

        **失去牌后（票 07）**：有承载帧的移动在本帧内派发 ``MOVE_AFTER``（帧私有载荷
        :class:`~sgs.skills.MoveEvent`，帧私有数据不进记录流）；**没有承载帧**的移动
        （弃牌阶段的弃置、判定阶段循环的两次收尾等回合环步骤）给 ``frame=None``，
        由 :meth:`_dispatch_move_after` 用一个不压栈的临时帧派发。钩子若提问，挂起状态
        记在引擎上（``_move_pending``），由 :meth:`_flush_pending_move` 在下一个询问应答
        之后续跑——故这里的返回值只用于"步骤可以被安全推进"的判断，挂起的续跑不依赖它。

        **重入判据是"这次移动已经搬完"，不是"还挂着"**：续跑清掉 ``_move_pending`` 之后，
        承载帧上的 op 会原地重入本方法一次；那一次必须经帧私有 :data:`_MOVE_DONE` 认出
        "同一次移动"，否则会重放搬牌并留下同区幻影 delta（见该属性的说明）。

        **它不是全项目唯一的搬牌点**：使用链、效果回调与上面两类帧外移动都经本方法，
        但另有一小组**直接调** :func:`~sgs.effects.move_card` 的收尾点——阵亡者的区域清空
        （:meth:`discard_dead_cards`）、主公杀忠臣的弃置（:meth:`_lord_discards_everything`）、
        奖惩摸牌（:meth:`_reward_killer_draws`）、摸牌阶段（:meth:`_phase_draw`）、终局清栈
        （:meth:`_teardown_processing`）与开局发牌（:func:`build_initial`）。它们**不派发**
        失去牌后：摸牌阶段 / 奖惩摸牌 / 开局发牌是"牌堆 → 手牌"（`R5-Q3`：摸牌不算失去），
        终局清栈发生在胜负已定之后，而阵亡弃牌 / 主公弃置仍是票 07 §范围外登记在案的缺口。
        勿据此在本方法里加回唯一性论断。
        """
        card_ids = tuple(card.card_id for card in cards)
        # 这次移动的**身份** = "哪个技能、把哪些牌、搬到哪里"。
        head = (
            frame.data.get(RUNNING_SKILL_KEY) if frame is not None else None,
            card_ids,
            to_zone,
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
        ctx = self._effect_context(
            frame,
            actor=(frame.data["source"] if frame is not None else None) if actor is None else actor,
        )
        delta = move_card(
            self.state, ctx, tuple(cards), to_zone=to_zone, reason=reason, owner=owner
        )
        self.emit_delta(
            delta,
            frame_id=(frame.frame_id if frame is not None else 0),
            depth=self.stack.depth,
        )
        prune_skill_state(self.state)
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
        if control == "stay":
            self._move_pending = (event, key, frame)
        return "stay" if control == "stay" else None

    def _move_event(self, delta: Any, *, actor: int | None) -> MoveEvent:
        """把一条 ``card_move`` 的形状装成 :class:`~sgs.skills.MoveEvent`（派发载荷）。"""
        return MoveEvent(
            cards=tuple(delta.cards),
            from_zone=delta.from_zone,
            to_zone=delta.to_zone,
            reason=delta.reason,
            owner=delta.owner,
            after_holder=delta.to_zone.seat,
            actor=actor,
        )

    def _dispatch_move_after(
        self,
        frame: Frame | None,
        event: MoveEvent,
        *,
        count: int,
        key: tuple,
        frame_id: int = 0,
    ) -> Control:
        """把这次移动当"失去牌后"派发 ``MOVE_AFTER``（票 07）。

        ``key`` 是这次时机实例的幂等键（见 :meth:`_move_key`）：重入续跑时沿用它，
        故"同一次移动"只落一条记录、只派发一遍。承载帧可能已经不在栈上（挂起期间
        上层帧出栈），故不使用帧自己的私有标记；载荷写进 ``frame.data`` 供钩子读。
        """
        if frame is not None:
            if not move_after_providers(self.state):
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
            if not carried:
                # 承载帧已经不在栈上（挂起期间上层帧出栈）：用临时帧续跑，
                # 不往一个已出栈的帧里写载荷。
                transient = Frame(
                    kind=FrameKind.RESOLVE,
                    data={"source": event.actor, "targets": (), "card": None},
                    frame_id=frame_id,
                )
                transient.data[MOVE_EVENT_KEY] = event
                return dispatch_move_after(self, transient)
            frame.data[MOVE_EVENT_KEY] = event
            result = dispatch_move_after(self, frame)
            if result == "stay":
                frame.data["_pending_move_timing"] = key
            else:
                frame.data.pop("_pending_move_timing", None)
            return result
        # 无承载帧（弃牌阶段等）：先判惰性守卫，再落记录并派发。
        if not move_after_providers(self.state):
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
        return dispatch_move_after(self, transient)

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

    def apply_hp_change(self, frame: Frame, seat: int, value: int) -> HpChangeDelta:
        """调 ``apply_hp`` 这个叶子原语并把返回的变化事件写进记录流（票 07 §1）。

        ``frame_id`` 即调用它的扣减 / 回复帧，故"这次体力变化属于谁"可由帧关联推出。
        """
        ctx = self._effect_context(frame, actor=seat)
        delta = apply_hp(self.state, ctx, seat, value)
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)
        return delta

    # -- 询问 ---------------------------------------------------------------

    def ask(self, build: Callable[[int], Query]) -> Action | _Suspend:
        """把一次询问交给参与者；已有应答则直接返回它。"""
        if self._answer is not None:
            answer, self._answer = self._answer, None
            return answer
        seq = self._next_seq()
        query = build(seq)
        self._emit(QueryRecord(seq=seq, query=query))
        self.pending = query
        return SUSPEND

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
                self._answer = resolved
                self.pending = None
                return
            if action in pending.transform_actions:
                # 回放磁带 / 程序化提交：转化动作不在**暴露面**里，但仍是本次询问的合法池。
                self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
                self._answer = action
                self.pending = None
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
            self._answer = action
            self._structured_activation = action
            self.pending = None
            return
        self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
        self._answer = action
        self.pending = None

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
            # 挂起期间栈顶就是发问的那一帧，校验器必须读同一份上下文。
            frame = self.stack.top if self.stack else None
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
        | None = None,
        revealed: tuple[CardRef, ...] = (),
    ) -> Query:
        actions = legal_actions_for(
            self.state, actor, kind, answers_seq=seq, dying=dying, payload=payload
        )
        preactivations: dict[str, tuple[Step, ...]] = {}
        if kind is QueryKind.PLAY_PHASE:
            # W4-1：可用性从**候选回调**来（骨架仍在），清单按同一批技能 id 渲染。
            # 已翻到预发动的技能（``preativated``）把骨架从候选里删掉——可用性只剩这份
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
        # W4-fix-2：转化候选从**暴露面**收回（`legal_actions` 里不再有"红牌直接当
        # 【杀】"），但**内部池**照旧保留——清单 / 确认 / 回放成员判定都读这一份，故
        # "清单 = 候选"没有第二份谓词。
        #
        # W4-fix-6（A）：响应窗口按**语境**（`RespondContext`）而不是按询问种类区分——
        # 【万箭】的转化【闪】是 `UseCard`，借刀杀人的"被迫使用"才是唯一要排除的例外。
        response = payload if isinstance(payload, RespondContext) else None
        internal_transform_actions = transform_candidates(
            kind, actions, respond=response
        )
        transform_preactivations = conversion_preactivations(
            self.state,
            actor,
            internal_transform_actions,
            # 响应 / 求桃窗口的目标由窗口固定：清单只下发来源步（`spec.md` §2 急救行与
            # W4-fix-6 的【万箭】行）。
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
            )
        if kind is QueryKind.DISCARD:
            player = self.state.player(actor)
            count = discard_count(player)
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
        """把被钩子挂起的 ``MOVE_AFTER`` 派发完（票 07）。

        询问刚被应答、下一帧还没跑时调用：此时钩子重入需要的那条应答已经在
        ``_answer`` 里，故续跑会把它消费掉。没有挂起的移动时是**零动作**。
        """
        while self._move_pending is not None:
            event, key, pending_frame = self._move_pending
            control = self._dispatch_move_after(
                pending_frame, event, count=key[2], key=key
            )
            if control == "stay":  # pragma: no cover - 本族的钩子不提问（见 move_after 模块）
                return
            self._move_pending = None

    def _step(self) -> None:
        if self.finished:
            return
        self._flush_pending_move()
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
            if self.state.turn_seat is not None and self.state.players:
                # 回合只在**存活座位**之间轮转（票 15 §6）；座位环是唯一真源。
                # 存活 ≤ 1 时终局已抢先，故正常对局必然读到一个存活下家。
                next_seat = next_alive_seat(self.state, self.state.turn_seat)
                if next_seat is not None:
                    self.state.turn_seat = next_seat

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
        self.emit_timing(Timing.TURN_BEFORE_START, actor=self.state.turn_seat)

    def _turn_start(self) -> None:
        # 该座位自己的 TURN_START 是使用次数计数器的重置点（票 07 §6）。
        seat = self.state.turn_seat
        if seat is not None:
            self.state.player(seat).card_use_counts.clear()
        self.emit_timing(Timing.TURN_START, actor=seat)

    def _phase_prepare(self) -> str | None:
        # 准备阶段体照记；有技能钩子（M3 的【观星】式）时惰性建帧派发（票 10 §③）。
        self.state.phase = Phase.PREPARE
        return self._dispatch_phase_body(Timing.PHASE_PREPARE, "phase_prepare")

    def _dispatch_phase_body(self, timing: Timing, key: str) -> Control:
        """**帧外阶段体**的钩子派发（票 10）：空实现照记，有提供者才惰性建帧。

        阶段体（准备 / 摸牌 / 结束）没有结算帧，而钩子必须能提问并跨重入续跑，故这里给
        它一个**不压栈**的持久帧（住 ``_turn_data``）：它不进记录流、不参与步骤表，
        只作钩子的上下文与帧内答案缓存（与 ``emit_timing`` 给 ``MOVE_AFTER`` 造的
        临时帧同形，区别只是要跨暂停存活）。没有注册在该时机上的实例时**一个帧都不建**，
        记录流与 M2 逐字节相同（票 10 的硬纪律：34 条基准一行不改）。

        时机记录只发一次（``_turn_data`` 的键作幂等闸）；派发返回 ``"stay"`` 时整个阶段体
        原地重入，重用同一个帧与同一份应答缓存。
        """
        store = self._turn_data
        if key not in store:
            self.emit_timing(timing, actor=self.state.turn_seat)
            if not timing_hooks_present(self.state, timing):
                store[key] = None
                return None
            store[key] = Frame(
                kind=FrameKind.RESOLVE,
                data={"source": self.state.turn_seat, "targets": (), "card": None},
                frame_id=0,
            )
        frame = store[key]
        if frame is None:
            return None
        control = dispatch_timing(self, frame, timing)
        if control == "stay":
            return "stay"
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
        self.emit_timing(Timing.PHASE_DRAW_START, actor=self.state.turn_seat)

    def _phase_draw(self) -> str | None:
        seat = self._turn_seat()
        control = self._dispatch_phase_body(Timing.PHASE_DRAW, "phase_draw")
        if control == "stay":
            return "stay"
        if self._turn_data.get("phase_draw_drawn"):
            # 钩子在同一阶段体里提过问：重入时只补完基础摸牌，不重发时机记录、不重摸。
            return None
        self._turn_data["phase_draw_drawn"] = True
        drawn = draw_from_deck(self.state, DRAW_COUNT, self._shuffle)
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
        if len(drawn.cards) < DRAW_COUNT:
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
            lambda seq: self._build_query(QueryKind.PLAY_PHASE, seat, Timing.PHASE_PLAY_IDLE, seq)
        )
        if action is SUSPEND:
            return "stay"
        if action.kind == "EndPhase":
            return None
        if isinstance(action, ActivateSkill):
            # 主动技（票 04）：压 SKILL 帧跑发动主体，出栈后回到本步重新询问。
            self.start_skill(action, source=seat)
            return "stay"
        # 使用一张牌：压入 USE 帧；子帧结算完再回到本步重新询问（票 07 §3）。
        self.start_use(action.card, source=seat, targets=action.targets)
        return "stay"

    def _phase_play_end(self) -> None:
        self.emit_timing(Timing.PHASE_PLAY_END, actor=self.state.turn_seat)

    def _phase_discard_start(self) -> None:
        self.state.phase = Phase.DISCARD
        self.emit_timing(Timing.PHASE_DISCARD_START, actor=self.state.turn_seat)

    def _phase_discard(self) -> str | None:
        seat = self._turn_seat()
        if not self._turn_data.get("discard_emitted"):
            self.emit_timing(Timing.PHASE_DISCARD, actor=seat)
            self._turn_data["discard_emitted"] = True
        player = self.state.player(seat)
        if discard_count(player) <= 0:
            return None
        action = self.ask(
            lambda seq: self._build_query(QueryKind.DISCARD, seat, Timing.PHASE_DISCARD, seq)
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
        return None

    def _phase_discard_end(self) -> None:
        self.emit_timing(Timing.PHASE_DISCARD_END, actor=self.state.turn_seat)

    def _phase_finish(self) -> str | None:
        self.state.phase = Phase.FINISH
        return self._dispatch_phase_body(Timing.PHASE_FINISH, "phase_finish")

    def _turn_end(self) -> None:
        self.emit_timing(Timing.TURN_END, actor=self.state.turn_seat)

    def _turn_after_end(self) -> None:
        self.emit_timing(Timing.TURN_AFTER_END, actor=self.state.turn_seat)

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
) -> tuple[GameState, tuple[JournalRecord, ...]]:
    """实例化牌堆 → 洗牌 → 发身份（身份局）→ 发武将 → 发牌；顺带产出开局那几条记录。

    ``seq = 1`` 必是初次 ``deck_shuffle``（``frame_id = 0``：无帧哨兵）；身份局的
    ``identity_deal`` 逐座紧随其后（票 13 §3：先发身份 → 亮主 → 发起始手牌），
    再是发牌时机与按座序每座位一条 ``card_move``（票 08 §5 / 票 06 §7）。
    无身份局完全不产生 ``identity_deal``，故 M1 六条基准的 seq 布局不变。
    ``shuffle_source`` 由 :meth:`Engine.new_game` 交进来，好让中局重洗接着同一子流取数。

    **武将发放**（票 02）走唯一随机源的专用子流 ``hero``：``heroes`` 为空 = **无武将模式**
    （M2 的 34 条基准靠它保持有效）、非空 = 显式指定；``hero_pool`` 非 ``None`` 时从该池
    不重复随机抽 N 名。``pinned_hero`` / ``pinned_seat``（票 19 的点将）钉住一个座位、其余
    从去掉它的池里随机——仍只取 ``hero`` 子流。发放段本身**不产出记录**（武将不是牌、
    也没有 delta），故关掉武将时记录流逐字节不变。
    """
    heroes_table = load_heroes(pack.pack_id)
    assigned = deal_heroes(
        seat_count=rule_config.player_count,
        stream=RandomSource(seed).substream("hero"),
        heroes=heroes,
        hero_pool=hero_pool,
        pinned_hero=pinned_hero,
        pinned_seat=pinned_seat,
        table=heroes_table,
    )
    cards = instantiate_cards(pack)
    identities = _deal_identities(seed=seed, rule_config=rule_config)
    players = tuple(
        _initial_player(
            seat,
            identities[seat] if identities is not None else None,
            None if assigned is None else heroes_table.by_id(assigned[seat]),
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
) -> GameState:
    """便利包装：造初始状态（实例化牌堆 → 洗牌 → 发身份 → 发武将 → 发牌），不返回记录流。

    要"跑一局并拿到记录流"用 :meth:`Engine.new_game`。``heroes`` / ``hero_pool`` 同义。
    """
    return build_initial(
        seed=seed,
        rule_config=rule_config,
        pack=pack,
        heroes=heroes,
        hero_pool=hero_pool,
        pinned_hero=pinned_hero,
        pinned_seat=pinned_seat,
    )[0]
