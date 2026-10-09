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
from sgs.data import ContentPack, Identity, Mode, RuleConfig, roster_for
from sgs.effects import (
    OPS,
    CallOp,
    DamageNature,
    EffectContext,
    EffectStep,
    PushFrame,
    Randomness,
    apply_hp,
    delayed_settle_target,
    discard_count,
    draw_from_deck,
    legal_actions_for,
    move_card,
)
from sgs.resolution import Frame, ResolutionStack, StepTable
from sgs.state import (
    Action,
    CardRef,
    DiscardQuery,
    DodgeQuery,
    FrameKind,
    GameState,
    HpChangeDelta,
    IdentityRevealDelta,
    MoveReason,
    PeachQuery,
    Phase,
    PlayPhaseQuery,
    Player,
    Query,
    QueryKind,
    SlotKind,
    Zone,
    ZoneRef,
    next_alive_seat,
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
"""M1 的体力上限恒 4（无武将、无体力上限变化）。"""

DRAW_COUNT = 2
"""摸牌阶段的额定摸牌数；M1 无修正摸牌数的效果。"""

REBEL_REWARD_DRAW = 3
"""杀死反贼的奖惩摸牌数（规则 4.8；票 14 §4.2）。"""


class _Suspend:
    """``ask`` 的哨兵：询问已发出，等参与者应答。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试可读性
        return "SUSPEND"


SUSPEND = _Suspend()


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
) -> dict[str, Any]:
    """子帧载荷 = 从使用帧继承 ``source`` / ``targets`` / ``card`` / ``use_timing``
    + 行为表的标量字面量。

    ``use_timing`` 一路带着走，好让"这次使用是从哪个窗口开的"在后面每一层都读得到
    （濒死求桃开的【桃】用它把自己的回复结算与行为表的效果帧区分开，票 21）。
    伤害帧再规范化成官方**六要素**（票 07 §6）：``target`` = 唯一目标、
    ``channel`` = 使用的实体牌、``nature`` 恒 ``NORMAL``、``chain`` 恒 ``False``。
    """
    data: dict[str, Any] = {
        "source": parent_data.get("source"),
        "targets": tuple(parent_data.get("targets", ())),
        "card": parent_data.get("card"),
        "use_timing": parent_data.get("use_timing"),
    }
    for key, value in scalars:
        if key in data:
            raise ValueError(f"行为表的标量 {key!r} 会覆盖子帧继承的字段")
        data[key] = value
    if kind is FrameKind.DAMAGE:
        targets = data["targets"]
        data["target"] = targets[0] if targets else None
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
        self._started = False
        self._frame_seq = 0
        self._check_seq: int | None = None
        self._deaths_pending = 0
        """同批未关闭的死亡单元计数（票 14 §3）：``enter_death`` +1、DEATH 帧出栈 -1。"""

        self._batch_facts: list[DeathFact] = []
        """本批每次死亡在检测点定格的事实；批次关闭时求解一次，未终局则清空。"""
        self._turn_pc = 0
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
    def new_game(cls, *, seed: int, rule_config: RuleConfig, pack: ContentPack) -> "Engine":
        """造初始状态（实例化 → 洗牌 → 发牌）并带上开局那几条记录。

        初次洗牌与后续重洗共用同一个 ``shuffle`` 子流句柄：重洗从初次洗牌之后的位置继续取数
        （票 08 §3），因此同一 seed 的整局（含每一次重洗）逐字节可复现。
        """
        shuffle_source = RandomSource(seed).substream("shuffle")
        state, init_records = build_initial(
            seed=seed, rule_config=rule_config, pack=pack, shuffle_source=shuffle_source
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
    ) -> None:
        """dispatch 一个时机；空实现也照记。"""
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
        self._emit_frame("pop", frame)
        return frame

    # -- 结算帧步骤可用的操作面（步骤表在 steps.py，只经这些方法改状态）---------

    def emit_frame_timing(
        self, frame: Frame, timing: Timing, *, actor: int | None = None, cards=()
    ) -> None:
        """以 ``frame`` 为主体 dispatch 一个时机；``source`` / ``targets`` 随帧继承。"""
        self.emit_timing(
            timing,
            frame_id=frame.frame_id,
            depth=self.stack.depth,
            actor=actor,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            cards=tuple(cards),
        )

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
    ) -> Any:
        """构造并交付一次询问；已有应答（重入）则直接返回它。"""
        return self.ask(lambda seq: self._build_query(kind, actor, timing, seq, dying=dying))

    def start_use(
        self,
        card: CardRef,
        *,
        source: int,
        targets: tuple[int, ...],
        use_timing: Timing | None = None,
    ) -> Frame:
        """开始一次使用：记一次本回合已用、压入 USE 帧（票 07 §6）。

        ``use_timing`` 覆盖压栈步骤的 ``return_to`` 标签：默认是出牌阶段的
        ``USE_CHOOSE_TARGET``，濒死求桃里出【桃】时是 ``NEAR_DEATH_AT``
        （票 04 §4 的"从哪回来"要读得出是哪个窗口开的使用）。
        """
        counts = self.state.player(source).card_use_counts
        counts[card.type] = counts.get(card.type, 0) + 1
        return self.push_frame(
            FrameKind.USE,
            {
                "card": card,
                "source": source,
                "targets": tuple(targets),
                "use_timing": use_timing,
            },
            timing=use_timing if use_timing is not None else Timing.USE_CHOOSE_TARGET,
        )

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

    def move_used_card_to_processing(self, frame: Frame) -> None:
        """手牌 → 处理区（``reason = USE``）：在 L4.1 / L4.2 之间（票 07 §3）。"""
        self._move_in_frame(
            frame,
            (frame.data["card"],),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def move_processing_card_to_discard(self, frame: Frame) -> None:
        """处理区 → 弃牌堆（``reason = USE``）：L5.6"使用结算结束后"（票 07 §3）。"""
        self._move_in_frame(
            frame,
            (frame.data["card"],),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.USE,
            owner=frame.data["source"],
        )

    def move_used_card_to_judge_zone(self, frame: Frame) -> None:
        """处理区 → 目标座位的判定区（``reason = PUT``、归属 = 使用者）。

        延时锦囊"使用当时"的落点（票 10 §7）：L4 走完就把实体牌置入目标判定区，
        L5 推迟到目标自己的判定阶段。
        """
        self._move_in_frame(
            frame,
            (frame.data["card"],),
            to_zone=ZoneRef(Zone.JUDGE, frame.data["targets"][0]),
            reason=MoveReason.PUT,
            owner=frame.data["source"],
        )

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
        self._move_in_frame(
            frame,
            (frame.data["judge_card"],),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.JUDGE,
            owner=subject,
            actor=subject,
        )

    def discard_judge_card(self, frame: Frame) -> None:
        """判定牌 处理区 → 弃牌堆（``reason = JUDGE``、归属 = 被判定者）：判定结算结束。"""
        subject = frame.data["subject"]
        self._move_in_frame(
            frame,
            (frame.data["judge_card"],),
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

    def _effect_context(self, frame: Frame, *, actor: int) -> EffectContext:
        """叶子原语要的结算主体信息：从当前帧继承 `source` / `targets` / `card` 与层级。

        ``actor`` 是这次原语调用的主体（移动牌的操使者 / 体力被写的座位），由调用点给。
        """
        return EffectContext(
            actor=actor,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            card=frame.data.get("card"),
            frame_id=frame.frame_id,
            depth=self.stack.depth,
        )

    def _move_in_frame(
        self,
        frame: Frame,
        cards: tuple[CardRef, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int | None = None,
    ) -> None:
        """在当前帧里移动一批牌并记一条 delta；``actor`` 缺省取帧的 ``source``。

        判定路径的"无角色主体"移动（判定牌的两次移动、延时锦囊本体）主体是**被判定者**，
        与 ``source``（可能为 ``None``）分列，故调用点可显式给 ``actor``。
        """
        ctx = self._effect_context(
            frame, actor=frame.data["source"] if actor is None else actor
        )
        delta = move_card(
            self.state, ctx, tuple(cards), to_zone=to_zone, reason=reason, owner=owner
        )
        self.emit_delta(delta, frame_id=frame.frame_id, depth=self.stack.depth)

    def _move_cards(
        self,
        card_ids: tuple[int, ...],
        *,
        to_zone: ZoneRef,
        reason: MoveReason,
        owner: int | None,
        actor: int,
    ) -> None:
        """**帧外**移动一批牌并记一条 delta（帧已出栈的收尾移动，票 10 §3.6）。

        判定阶段循环的两次收尾移动（延时锦囊 JUDGE→PROCESSING 与「仍在处理区」的去向）
        没有承载帧，主体是当前回合角色；与 :meth:`_move_in_frame` 的唯一差别是
        ``EffectContext`` 只有 ``actor``。
        """
        delta = move_card(
            self.state,
            EffectContext(actor=actor),
            self.state.refs(card_ids),
            to_zone=to_zone,
            reason=reason,
            owner=owner,
        )
        self.emit_delta(delta)

    def apply_effects(
        self, frame: Frame, effects: Sequence[EffectStep], *, timing: Timing
    ) -> None:
        """解释行为表的步骤列表：``PushFrame`` 压子帧、``CallOp`` 查 :data:`OPS` 表调用。

        ``CallOp`` 只按**名**通用派发（回调签名 ``(host, frame)``），引擎里不出现任何
        具体效果名（票 09 §4）；未登记的名字是加载期就该拦下的错误（``card_type.unknown_op``），
        运行期再撞到即 fail-closed。
        """
        for step in effects:
            if isinstance(step, PushFrame):
                data = _child_payload(step.kind, frame.data, step.scalars)
                self.push_frame(step.kind, data, timing=timing)
                continue
            assert isinstance(step, CallOp)  # union 只有两类步骤
            op = OPS.get(step.op)
            if op is None:
                raise NotImplementedError(f"未登记的效果原语 {step.op!r}（票 09 §4 的 OPS 表）")
            op(self, frame)

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
        """接受一个应答；合法性 = **成员判定**，非法输入**零状态改动**（票 07 §5）。

        三类非法输入都在这里拒绝（票 24）：**结构损坏**（payload 不在行动 schema 内）/
        **应答序号陈旧**（``answers_seq`` 失配）/ **不在候选集合内**。三者都先写一条
        ``accepted = false`` 的 ``action`` 记录再抛 :class:`IllegalActionError`——
        拒绝本身也必须可观测；被拒前不碰任何游戏状态，也不重试、不超时。
        """
        pending = self.pending
        if pending is None:
            raise RuntimeError("当前没有待应答的询问")
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
                expected="UseCard / Pass / EndPhase / DiscardCards 之一",
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
            self._reject(action, reason="不在候选集合内")
            raise IllegalActionError(
                reason_code="IllegalAction",
                message="行动不在候选集合内",
                expected="候选集合的成员之一",
                got=action.kind,
                answers_seq=action.answers_seq,
            )
        self._emit(ActionRecord(seq=self._next_seq(), accepted=True, action=action))
        self._answer = action
        self.pending = None

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
    ) -> Query:
        actions = legal_actions_for(self.state, actor, kind, answers_seq=seq, dying=dying)
        view = project(self.state, actor)
        if kind is QueryKind.PLAY_PHASE:
            return PlayPhaseQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="出牌阶段：使用一张牌，或结束出牌阶段。",
                legal_actions=actions,
                view=view,
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
            )
        if kind is QueryKind.DODGE:
            return DodgeQuery(
                seq=seq,
                actor=actor,
                timing=timing,
                prompt="以你为目标的【杀】生效前：是否使用【闪】？",
                legal_actions=actions,
                view=view,
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
            )
        raise ValueError(f"M1 还没有 {kind.value} 询问的构造器")

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

    def _step(self) -> None:
        if self.finished:
            return
        if not self._started:
            self._start()
            return
        if self.stack:
            popped = self.stack.step(self.steps, self)
            if popped is not None:
                self._emit_frame("pop", popped)
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

    # -- 回合环的每一步 -----------------------------------------------------

    def _turn_before_start(self) -> None:
        self.emit_timing(Timing.TURN_BEFORE_START, actor=self.state.turn_seat)

    def _turn_start(self) -> None:
        # 该座位自己的 TURN_START 是使用次数计数器的重置点（票 07 §6）。
        seat = self.state.turn_seat
        if seat is not None:
            self.state.player(seat).card_use_counts.clear()
        self.emit_timing(Timing.TURN_START, actor=seat)

    def _phase_prepare(self) -> None:
        # 准备阶段在本里程碑没有内部事件：只发阶段体（票 06 §7）。
        self.state.phase = Phase.PREPARE
        self.emit_timing(Timing.PHASE_PREPARE, actor=self.state.turn_seat)

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
        # 每轮重取的是"仍在判定区"的牌：结算前离开判定区（被拆 / 顺走）的牌自然不会
        # 被选中，其使用流程随之终止（官方 `flow_use.txt:15`）。
        self._move_judge_card_to_processing(seat, card_id)
        self._turn_data["judge_inflight"] = card_id
        resolve = self.push_frame(
            FrameKind.RESOLVE,
            {
                "card": self.state.card(card_id),
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

    def _phase_draw_start(self) -> None:
        self.state.phase = Phase.DRAW
        self.emit_timing(Timing.PHASE_DRAW_START, actor=self.state.turn_seat)

    def _phase_draw(self) -> None:
        seat = self._turn_seat()
        self.emit_timing(Timing.PHASE_DRAW, actor=seat)
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

    def _phase_draw_end(self) -> None:
        self.emit_timing(Timing.PHASE_DRAW_END, actor=self.state.turn_seat)

    def _phase_play_start(self) -> None:
        self.state.phase = Phase.PLAY
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
        delta = move_card(
            self.state,
            EffectContext(actor=seat),
            action.cards,
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=seat,
        )
        self.emit_delta(delta)
        return None

    def _phase_discard_end(self) -> None:
        self.emit_timing(Timing.PHASE_DISCARD_END, actor=self.state.turn_seat)

    def _phase_finish(self) -> None:
        self.state.phase = Phase.FINISH
        self.emit_timing(Timing.PHASE_FINISH, actor=self.state.turn_seat)

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
    使移动原语不必拿到 ``ContentPack``（票 08 §2）。
    """
    slots = {
        card_type.id: _SLOT_BY_SUBCATEGORY.get(card_type.subcategory)
        for card_type in pack.card_types
    }
    names = {card_type.id: card_type.name for card_type in pack.card_types}
    return tuple(
        CardRef(
            card_id=index,
            type=deck_card.type,
            name=names[deck_card.type],
            suit=deck_card.suit.value,
            rank=deck_card.rank,
            slot=slots[deck_card.type],
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


def _initial_player(seat: int, identity: Identity | None) -> Player:
    """开局座位：主公体力与体力上限为初始值 +1，其余与无身份局同为 ``INITIAL_HP``。

    初始体力不进记录流（``Player`` 不在 header、也无 delta），故这一步不发 delta
    （票 13 §4）。
    """
    hp = INITIAL_HP + 1 if identity is Identity.LORD else INITIAL_HP
    return Player(seat=seat, hp=hp, hp_max=hp, identity=identity)


def build_initial(
    *,
    seed: int,
    rule_config: RuleConfig,
    pack: ContentPack,
    shuffle_source: Randomness | None = None,
) -> tuple[GameState, tuple[JournalRecord, ...]]:
    """实例化牌堆 → 洗牌 → 发身份（身份局）→ 发牌；顺带产出开局那几条记录。

    ``seq = 1`` 必是初次 ``deck_shuffle``（``frame_id = 0``：无帧哨兵）；身份局的
    ``identity_deal`` 逐座紧随其后（票 13 §3：先发身份 → 亮主 → 发起始手牌），
    再是发牌时机与按座序每座位一条 ``card_move``（票 08 §5 / 票 06 §7）。
    无身份局完全不产生 ``identity_deal``，故 M1 六条基准的 seq 布局不变。
    ``shuffle_source`` 由 :meth:`Engine.new_game` 交进来，好让中局重洗接着同一子流取数。
    """
    cards = instantiate_cards(pack)
    identities = _deal_identities(seed=seed, rule_config=rule_config)
    players = tuple(
        _initial_player(
            seat,
            identities[seat] if identities is not None else None,
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


def new_game(*, seed: int, rule_config: RuleConfig, pack: ContentPack) -> GameState:
    """便利包装：造初始状态（实例化牌堆 → 洗牌 → 发牌），不返回记录流。

    要"跑一局并拿到记录流"用 :meth:`Engine.new_game`。
    """
    return build_initial(seed=seed, rule_config=rule_config, pack=pack)[0]
