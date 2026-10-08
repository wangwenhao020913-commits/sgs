"""Golden Replay 的场景脚本玩家（票 10 §6 / 票 12 §3）。

**只在录制 / 更新时使用**（``pytest --update-golden``）。每个 slug 声明一个**写死的
``seed`` 常量**：开发时从候选区间搜出第一个满足目标段判据的种子后写死，录制期零搜索。

两条段机制（票 12 §3）：

- **目标段**：按 slug 精准命中该链；
- **收尾段**：固定确定性策略——能出【杀】就出【杀】；被【杀】且手里有【闪】就用【闪】，
  否则放弃；濒死且手里有【桃】就**只自救**（对方濒死一律放弃）；出牌阶段不用【桃】。

录制失败（目标段没命中 / 超过回合上限 / 没走到终止点）就是**录制失败**，
必须重新选种并显式改写 ``SCENARIOS`` 里的常量，而不是放宽断言。

票 22 之后**每条基准都以 ``game_end`` 收局**（真胜负或牌堆耗尽平局）：死亡结算、
胜负判定、清栈与终局收尾都已落地。

票 23 追加 ``06-deck-reshuffle``：重洗没有对应的询问，脚本看不见它，故用
:class:`_ReshuffleWatcher` 在每次应答前扫一遍记录流，出现重洗才把目标段切到收尾段。
重洗落地后"两堆皆空"在实战不可达，故除平局构造局面外每条基准都靠**打死一方**收局。
"""

from __future__ import annotations

from dataclasses import dataclass

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, dump_journal
from sgs.state import Action, EndPhase, Pass, Query, UseCard
from sgs.timing import Timing

__all__ = ["SCENARIOS", "Scenario", "record"]


@dataclass(frozen=True)
class Scenario:
    """一条基准的录制参数；``max_turns`` 是终止保证（票 12 §3）。"""

    slug: str
    seed: int
    max_turns: int


SCENARIOS: dict[str, Scenario] = {
    "01-slash-dodge-cancel": Scenario(slug="01-slash-dodge-cancel", seed=101, max_turns=40),
    "02-slash-hit-damage": Scenario(slug="02-slash-hit-damage", seed=4, max_turns=40),
    "03-near-death-self-peach": Scenario(
        slug="03-near-death-self-peach", seed=28, max_turns=40
    ),
    "04-near-death-other-peach": Scenario(
        slug="04-near-death-other-peach", seed=9, max_turns=40
    ),
    "05-near-death-death": Scenario(slug="05-near-death-death", seed=1, max_turns=40),
    "06-deck-reshuffle": Scenario(slug="06-deck-reshuffle", seed=2026, max_turns=80),
}
"""M1 的 6 条基准（票 12 §3）；`06-deck-reshuffle` 由票 23 追加。"""


@dataclass
class _Recording:
    """一次录制的进行时状态。"""

    goal_done: bool = False
    goal_seat: int | None = None
    """命中目标段的那一步是由哪个座位做出的（用于把"已命中"告诉另一个座位）。"""

    def announce(self, seat: int) -> None:
        self.goal_done = True
        self.goal_seat = seat


class _ReshuffleWatcher:
    """把"记录流里出现了重洗"告诉场景脚本（票 23）。

    重洗没有对应的询问，脚本玩家自己看不见；它发生在摸牌阶段、下一问（出牌阶段）
    之前，故在 ``respond`` 入口处扫一遍记录流即可把目标段及时切到收尾段。
    """

    def __init__(self, engine: Engine, recording: _Recording, seat: int, inner: "ScenarioPlayer") -> None:
        self._engine = engine
        self._recording = recording
        self._seat = seat
        self._inner = inner

    def respond(self, query: Query) -> Action:
        if not self._recording.goal_done and _reshuffle_seen(self._engine):
            self._recording.announce(self._seat)
        return self._inner.respond(query)


def _reshuffle_seen(engine: Engine) -> bool:
    return any(
        record.kind == "delta"
        and record.delta_type == "deck_shuffle"
        and record.from_zone is not None
        for record in engine.records
    )


class ScenarioPlayer:
    """按 slug 走目标段、其余走收尾段的脚本玩家（票 12 §3）。"""

    def __init__(self, seat: int, slug: str, recording: _Recording) -> None:
        self.seat = seat
        self.slug = slug
        self.recording = recording

    def respond(self, query: Query) -> Action:
        target = self._target_segment(query)
        if target is not None:
            return target
        return _cleanup_for(self.seat, query)

    def _peers_goal(self) -> bool:
        """目标段已由**另一个座位**命中：本座位此后只走收尾段。"""
        return self.recording.goal_done and self.recording.goal_seat != self.seat

    def _self_goal(self) -> bool:
        """目标段已由**本座位**命中：目标段结束，本座位改走收尾段。"""
        return self.recording.goal_done and self.recording.goal_seat == self.seat

    def _any_goal(self) -> bool:
        """目标段已经命中过（无论哪个座位）。"""
        return self.recording.goal_done

    def _target_segment(self, query: Query) -> Action | None:
        if self.slug == "01-slash-dodge-cancel":
            if self.recording.goal_done:
                return None
            return self._slash_dodge_cancel(query)
        if self.slug == "02-slash-hit-damage":
            if self.recording.goal_done:
                return None
            return self._slash_hit_damage(query)
        if self.slug == "03-near-death-self-peach":
            return self._near_death_self_peach(query)
        if self.slug == "04-near-death-other-peach":
            return self._near_death_other_peach(query)
        if self.slug == "05-near-death-death":
            return self._near_death_death(query)
        if self.slug == "06-deck-reshuffle":
            # 目标段"不进攻"：只摸牌 + 弃至上限，直到 `_ReshuffleWatcher` 宣告重洗已出现。
            if self.recording.goal_done:
                return None
            return _idle(query)
        raise AssertionError(f"没有为场景 {self.slug} 写目标段")  # pragma: no cover - 开发期护栏

    # ---- 01 / 02：座位 0 出【杀】，座位 1 在【闪】窗口给出互斥的两种应答 ----

    def _slash_dodge_cancel(self, query: Query) -> Action | None:
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            dodge = _use(query, "dodge")
            if dodge is not None:
                self.recording.announce(self.seat)
            return dodge
        return None

    def _slash_hit_damage(self, query: Query) -> Action | None:
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            self.recording.announce(self.seat)
            return _find(query, Pass)
        return None

    # ---- 03：座位 1 在自己的回合被打到 0，被求桃时自救 ----

    def _near_death_self_peach(self, query: Query) -> Action | None:
        if query.kind == "PLAY_PHASE" and self.seat == 0 and not self._any_goal():
            # 命中前：反复进攻；命中后收手，好让另一方能活到牌堆耗尽。
            return _use(query, "slash", targets=(1,))
        if self._peers_goal() or self._self_goal():
            return None
        if self.seat == 1 and query.kind == "PEACH" and query.dying == 1:
            peach = _use(query, "peach", targets=(1,))
            if peach is not None:
                self.recording.announce(self.seat)
                return peach
            return _find(query, Pass)
        return None

    # ---- 04：座位 0（当前回合角色）在求桃窗口救座位 1 ----

    def _near_death_other_peach(self, query: Query) -> Action | None:
        if query.kind == "PLAY_PHASE" and self.seat == 0 and not self._any_goal():
            return _use(query, "slash", targets=(1,))
        if self._peers_goal() or self._self_goal():
            return None
        if self.seat == 0 and query.kind == "PEACH" and query.dying == 1:
            peach = _use(query, "peach", targets=(1,))
            if peach is not None:
                self.recording.announce(self.seat)
                return peach
            return _find(query, Pass)
        return None

    # ---- 05：座位 1 濒死而双方均放弃 ----

    def _near_death_death(self, query: Query) -> Action | None:
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,)) if self.seat == 0 else _find(query, EndPhase)
        if query.kind == "DODGE":
            return _find(query, Pass)
        if query.kind == "PEACH" and query.dying == 1:
            if not self.recording.goal_done:
                self.recording.announce(self.seat)
            return _find(query, Pass)
        return None


def _use(query: Query, type_id: str, targets: tuple[int, ...] | None = None) -> Action | None:
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            continue
        if targets is not None and action.targets != targets:
            continue
        return action
    return None


def _idle(query: Query) -> Action:
    """目标段"不进攻"：能结束阶段就结束、能放弃就放弃，弃牌取候选里的自然后者。"""
    if query.kind == "PLAY_PHASE":
        return _find(query, EndPhase)
    if query.kind in ("DODGE", "PEACH"):
        return _find(query, Pass)
    return query.legal_actions[0]


def _cleanup_for(seat: int, query: Query) -> Action:
    """收尾段（带座位）：能杀就杀、有闪就闪；濒死时只自救。"""
    if query.kind == "PLAY_PHASE":
        slash = _use(query, "slash")
        return slash if slash is not None else _find(query, EndPhase)
    if query.kind == "DODGE":
        dodge = _use(query, "dodge")
        return dodge if dodge is not None else _find(query, Pass)
    if query.kind == "PEACH":
        if query.dying == seat:
            peach = _use(query, "peach")
            return peach if peach is not None else _find(query, Pass)
        return _find(query, Pass)
    return query.legal_actions[0]


def _find(query: Query, action_type: type) -> Action:
    for action in query.legal_actions:
        if isinstance(action, action_type):
            return action
    raise AssertionError(f"{query.kind} 询问里没有 {action_type.__name__} 候选")  # pragma: no cover


def record(slug: str) -> str:
    """用场景脚本跑完整局，返回 canonical JSONL；录制期断言目标段与终止保证。"""
    scenario = SCENARIOS[slug]
    pack = load_content_pack()
    engine = Engine.new_game(seed=scenario.seed, rule_config=RuleConfig(), pack=pack)
    recording = _Recording()
    players: list = [ScenarioPlayer(seat, slug, recording) for seat in range(2)]
    if slug == "06-deck-reshuffle":
        players = [
            _ReshuffleWatcher(engine, recording, seat, player)
            for seat, player in enumerate(players)
        ]
    engine.run(players)

    assert recording.goal_done, f"{slug}：目标段没有命中（seed={scenario.seed}）"
    assert engine.records[-1].kind == "game_end", f"{slug}：没有走到终局"
    assert engine.records[-1].result in ("winner", "draw"), "终局结果只能是胜负或平局"
    turns = sum(
        1
        for record_ in engine.records
        if record_.kind == "timing" and record_.timing is Timing.TURN_START
    )
    assert turns <= scenario.max_turns, f"{slug}：{turns} 回合超过上限 {scenario.max_turns}"
    return dump_journal(engine.records)
