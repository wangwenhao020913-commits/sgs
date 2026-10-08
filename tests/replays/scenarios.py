"""Golden Replay 的场景脚本玩家（票 10 §6 / 票 12 §3）。

**只在录制 / 更新时使用**（``pytest --update-golden``）。每个 slug 声明一个**写死的
``seed`` 常量**：开发时从候选区间搜出第一个满足目标段判据的种子后写死，录制期零搜索。

两条段机制（票 12 §3）：

- **目标段**：按 slug 精准命中该链；
- **收尾段**：固定确定性策略——能出【杀】就出【杀】；被【杀】且手里有【闪】就用【闪】，
  否则放弃；出牌阶段不用【桃】。

录制失败（目标段没命中 / 超过回合上限 / 没走到终局）就是**录制失败**，
必须重新选种并显式改写 ``SCENARIOS`` 里的常量，而不是放宽断言。
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
}
"""M1 的基准清单；后续票（21–23）在此追加 03–06。"""


@dataclass
class _Recording:
    """一次录制的进行时状态：目标段是否已命中。"""

    goal_done: bool = False


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
        return _cleanup(query)

    def _target_segment(self, query: Query) -> Action | None:
        if self.recording.goal_done:
            return None
        if self.slug == "01-slash-dodge-cancel":
            return self._slash_dodge_cancel(query)
        if self.slug == "02-slash-hit-damage":
            return self._slash_hit_damage(query)
        raise AssertionError(f"没有为场景 {self.slug} 写目标段")  # pragma: no cover - 开发期护栏

    def _slash_dodge_cancel(self, query: Query) -> Action | None:
        # 座位 0 用【杀】打座位 1；座位 1 在生效前用【闪】抵消。
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            dodge = _use(query, "dodge")
            if dodge is not None:
                self.recording.goal_done = True
            return dodge
        return None

    def _slash_hit_damage(self, query: Query) -> Action | None:
        # 座位 0 用【杀】打座位 1；座位 1 放弃【闪】，让【杀】真的造成 1 点伤害。
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            self.recording.goal_done = True
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


def _cleanup(query: Query) -> Action:
    """收尾段：能杀就杀、有闪就闪、不用【桃】；其余取候选里的第一个。"""
    if query.kind == "PLAY_PHASE":
        slash = _use(query, "slash")
        return slash if slash is not None else _find(query, EndPhase)
    if query.kind == "DODGE":
        dodge = _use(query, "dodge")
        return dodge if dodge is not None else _find(query, Pass)
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
    engine.run([ScenarioPlayer(seat, slug, recording) for seat in range(2)])

    assert recording.goal_done, f"{slug}：目标段没有命中（seed={scenario.seed}）"
    assert engine.records[-1].kind == "game_end", f"{slug}：没有走到终局"
    turns = sum(
        1
        for record_ in engine.records
        if record_.kind == "timing" and record_.timing is Timing.TURN_START
    )
    assert turns <= scenario.max_turns, f"{slug}：{turns} 回合超过上限 {scenario.max_turns}"
    return dump_journal(engine.records)
