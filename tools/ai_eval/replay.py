"""回放台（`ai-view` 票 03；spec §2.2）：`ai_eval replay <run>` 的 explain / score / godview。

**一次引擎重放，三个读出口**。重放喂**记录里的动作**（磁带逐条驱动，状态因此与录像对齐），
在 AI 座位上另问一次 AI（影子决策）——AI 的读点不变，工具只把它的输入 / 推断 / 剪枝 /
候选 / 对照翻成人能读的五段。两个理由：

- 记录流里**没有**起因与公开面窗口（spec §2.3 把它们放在引擎侧、票 04/06 才进视图），
  只有重放看得见；本票的「公开面窗口」因此取**观察者过滤后的叙事**（`sgs_web.narrative`
  的 `NarrativeCursor`）——它同样是重放产物，且不会把隐藏手牌 / 牌堆顺序写进解释。
- 走「离线解析记录流」会把"为什么没被剪掉"变成第二份规则实现；`legal_actions` 是引擎
  剪枝的产物，故候选面与剪枝原因都由工具**读引擎的同一份事实**得出（`behavior_for` /
  `distance` / `attack_range`），**不重算**规则。

三个模式共用一次重放：

1. `--explain`：每条 AI 询问一段五段解释（输入 / 推断 / 剪枝 / 候选 / 对照）。
   候选分项来自生产代码自己的 `Score.add` trace（票 02）：工具临时把模块的 `_SCORERS`
   包一层，在真实的 `HeuristicPlayer.respond` 路径上打开 `Score.tracing()` ——**不复制
   候选组装、不复制打分器**（与 `tests/test_heuristic_ai.py` 的 parity 用例同一条路）。
   对照段比较「记录动作」与「重算 argmax」；不一致是版本或输入不匹配的 bug 信号，**不静默**：
   报告照常打印（含不一致的那一步），CLI 退出码为 1。
2. `--score`：用**免费标签**（阵亡亮明 / 终局）算对数损失 / ECE / Brier，只统计**当时还
   暗着的座位**。第一版只测两个基线——配比均匀后验与恒先验——先给出一个数再谈改进。
3. `--godview`：把错误判定与它的在位代价对齐：同一个（观察者, 目标）的连续错误关系判定
   记一个区间，代价 = 在位决策数 × 期间决策数。

**不做**：不判 verdict、不算胜率、不画局面曲线（spec §2.2 与票面「读法 / 不做」）。

夹具 / 用法（`PYTHONPATH=tools` 是必须的，与 `ai_eval` 的其它命令同一条）::

    PYTHONPATH=tools .venv/bin/python -m ai_eval replay <run> --explain --seq 511
    PYTHONPATH=tools .venv/bin/python -m ai_eval replay <run> --score
    PYTHONPATH=tools .venv/bin/python -m ai_eval replay <run> --godview
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

# 回放台是**仓库工具**（tools/，不进发行包）：它读的私有名字是 `HeuristicPlayer.respond`
# 自己读的那几行——候选派发表 `_SCORERS`、身份关系 `_identity_relation` / `_hostility` /
# `_renegade_hostility_from_balance` / `_unknown_identity_remainder` / `_enemy_identities` /
# `_ally_identities`、记牌 `_dodge_probability`、斩杀 `_lethal_to` / `_damage_card`。
# `Score` 已由 `sgs.agents` 公开再导出；工具不复制第二份打分器。依赖方向仍只有
# `tools/ → src/sgs`（`src/sgs` 内部不反向 import `tools/`），与
# `tests/test_heuristic_ai.py` 的 parity 用例、`tools/ai_eval/compat.py` 的
# `getattr(module, "_SCORERS", None)`（缺表 fail-closed）同一条先例。这是 tools 层的
# 刻意例外；`src/sgs/agents/__init__.py` 的"外部不得深导"约束仍对 src 的消费者生效。
import sgs.agents.heuristic as heuristic_scoring
from sgs.agents import HeuristicPlayer, Score
from sgs.data import IDENTITY_LABELS, IDENTITY_ROSTERS, Identity
from sgs.engine import Engine, JournalVersionError, ai_stream, read_journal
from sgs.state import Action, parse_action

from .errors import AiEvalRunError, AiEvalSpecError
from .live import DEFAULT_RUNS_DIR

__all__ = [
    "PUBLIC_WINDOW",
    "Candidate",
    "ExplainStep",
    "GodViewReport",
    "ReplayReport",
    "ScoreReport",
    "WrongJudgment",
    "render_replay",
    "run_replay",
]

PUBLIC_WINDOW = 8
"""解释里「公开面窗口」的条数：观察者视角下最近 N 条叙事（不是全知记录）。"""

IDENTITY_ORDER: tuple[Identity, ...] = (
    Identity.LORD,
    Identity.LOYALIST,
    Identity.REBEL,
    Identity.RENEGADE,
)
"""`--score` 的四个类别（顺序 = 报表列顺序；不迭代映射，跨进程稳定）。"""

_LOG_FLOOR = 1e-12
"""对数损失的下限：模型给 0 的概率时不让 `log(0)` 把整批数变成 inf。"""

_REL_ENEMY = "敌"
_REL_ALLY = "友"
_REL_NEUTRAL = "中立"
_REL_UNDECIDED = "未定"
_REL_SELF = "本人"
"""关系判定的闭集（解释 / 上帝视角共用；中文只出现在输出层，比较用这几个常量）。"""


# ---------------------------------------------------------------------------
# 结果对象：渲染只读这些对象（测试不必解析文本）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """一个候选动作的**同一份代码产出**的分数与分项（票 02 的 `Score.tracing`）。"""

    action: Action
    score: float
    items: tuple[tuple[str, float], ...]
    chosen: bool


@dataclass(frozen=True)
class IdentityRead:
    """解释「推断」段里的一个座位读数（全部来自 AI 自己读的 `PlayerView`）。"""

    seat: int
    public_identity: Identity | None
    alive: bool
    hp: int
    hp_max: int
    armor: int
    hand_count: int
    enemy: float
    ally: float
    label: str
    """关系标签：``敌`` / ``友`` / ``中立`` / ``未定``；本人写 ``本人``。"""
    confidence: float
    fraction: tuple[int, int, int] | None = None
    """配比后验的分数三元组 ``(敌方份数, 友方份数, 未知座位数)``；算不出时 ``None``。"""


@dataclass(frozen=True)
class PruneLine:
    """剪枝面的一行：谁 / 哪张牌不在候选里，因为什么规则。"""

    subject: str
    reason: str


@dataclass(frozen=True)
class ExplainStep:
    """一条 AI 询问的五段解释；渲染只读它。"""

    query_seq: int
    record_seq: int | None
    actor: int
    kind: str
    prompt: str
    view: Any
    identities: tuple[IdentityRead, ...]
    dodge: tuple[tuple[int, float | None], ...]
    lethal: tuple[tuple[int, int, bool | None], ...]
    prune: tuple[PruneLine, ...]
    candidates: tuple[Candidate, ...]
    chosen: Action
    resolved: Action
    """AI 的应答经引擎**同一个解析器**归一后的动作（复合转化 ⇒ 普通使用 / 打出）。"""
    resolution_error: str | None
    recorded: Action
    mismatch: bool
    best: float
    winners: int
    tie: bool
    public_window: tuple[str, ...]


@dataclass(frozen=True)
class BaselineScore:
    """一个基线（配比均匀后验 / 恒先验）在**同一批**座位步上的三个数。"""

    name: str
    log_loss: float
    brier: float
    ece: float


@dataclass(frozen=True)
class ScoreReport:
    """`--score` 的尺子读数；`samples` 是座位步数（不是询问数）。"""

    samples: int
    seats: int
    queries: int
    baselines: tuple[BaselineScore, ...]


@dataclass(frozen=True)
class WrongJudgment:
    """一个连续的错误关系判定区间；`cost = viewer_queries * all_queries`。"""

    viewer: int
    target: int
    judged: str
    truth: str
    start_seq: int
    end_seq: int
    viewer_queries: int
    all_queries: int

    @property
    def cost(self) -> int:
        return self.viewer_queries * self.all_queries


@dataclass(frozen=True)
class GodViewReport:
    """`--godview` 的复盘：错误判定按在位代价降序。"""

    wrong: tuple[WrongJudgment, ...]
    decisions: int
    open_at_end: int


@dataclass(frozen=True)
class ReplayReport:
    """一次重放的全部读数（explain / score / godview 共用）。"""

    run: str
    seed: int
    player_count: int
    human_seat: int | None
    queries: int
    recorded_actions: int
    explained: tuple[ExplainStep, ...]
    mismatches: tuple[ExplainStep, ...]
    score: ScoreReport | None
    godview: GodViewReport | None
    comparison_mismatches: tuple[tuple[int, str, str, str | None], ...] = ()
    """全部 AI 步骤的「记录 vs 重算」不一致（score / godview 不因此失败，但会在输出里提示）。"""
    total_queries: int = 0
    """录像里的询问总数；`--seq` 早停时 `queries` 只是已重放的那一段。"""


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


def run_replay(
    *,
    run: str,
    runs_dir: Path | str = DEFAULT_RUNS_DIR,
    explain: bool = False,
    score: bool = False,
    godview: bool = False,
    seq: int | None = None,
    seat: int | None = None,
    window: int = PUBLIC_WINDOW,
) -> ReplayReport:
    """重放一局并按请求的模式产出读数；坏输入一律点名路径 / seq 失败。"""
    if not (explain or score or godview):
        raise AiEvalSpecError("replay 至少要选一个模式：--explain / --score / --godview")
    if seq is not None and not explain:
        raise AiEvalSpecError("--seq 只与 --explain 一起用（尺子 / 上帝视角要整局）")
    if not explain and (seq is not None or seat is not None):
        raise AiEvalSpecError("--seq / --seat 只与 --explain 一起用")
    if explain and window <= 0:
        raise AiEvalSpecError(f"--window 必须是正整数，得到 {window}")
    if seat is not None and seat < 0:
        raise AiEvalSpecError(f"--seat 不能为负：{seat}")

    loaded = _load_run(Path(runs_dir), run)
    if seat is not None and seat >= loaded.player_count:
        raise AiEvalSpecError(
            f"--seat={seat} 越界（本局座位 0..{loaded.player_count - 1}）"
        )
    if seq is not None and seq not in loaded.query_aliases:
        available = sorted(loaded.query_aliases)
        if not available:
            raise AiEvalSpecError("录像里没有询问，--seq 没有可匹配的步骤")
        raise AiEvalSpecError(
            f"录像里没有 seq={seq} 的询问；可用 seq 从 {available[0]} 到 {available[-1]}"
            f"（共 {len(available)} 个）"
        )
    if score:
        _require_identity_mode(loaded, "score")
    if godview:
        _require_identity_mode(loaded, "godview")
    engine = _engine_for(loaded.meta)
    driver = _Driver(
        engine=engine,
        loaded=loaded,
        explain=explain,
        score=score,
        godview=godview,
        target_seq=seq,
        target_seat=seat,
        window=window,
    )
    try:
        engine.run([driver.participant(seat_index) for seat_index in range(loaded.player_count)])
    except _StopReplay:
        pass
    except (AiEvalSpecError, AiEvalRunError):
        raise
    except Exception as error:  # noqa: BLE001 - 回放边界：引擎侧任何失败都归"这次没读成"
        raise AiEvalRunError(f"重放失败：{loaded.journal_path}（{type(error).__name__}: {error}）") from error

    if explain and not driver.steps:
        if seq is not None:
            raise AiEvalSpecError(
                f"录像里没有 seq={seq} 的 AI 询问（记录里的 seq 是询问 seq 或动作记录 seq；"
                f"可用 seq 见 --explain 全量输出）"
            )
        raise AiEvalSpecError("录像里没有任何可解释的 AI 询问（全是人类座位？）")
    if (score or godview) and not engine.finished:
        raise AiEvalRunError(
            f"重放没有到终局（记录流可能被截断）：{loaded.journal_path}"
        )
    score_report = driver.score_report() if score else None
    godview_report = driver.godview_report() if godview else None
    return ReplayReport(
        run=run,
        seed=loaded.meta["seed"],
        player_count=loaded.player_count,
        human_seat=loaded.human_seat,
        queries=driver.queries,
        recorded_actions=loaded.recorded_actions,
        explained=tuple(driver.steps),
        mismatches=tuple(step for step in driver.steps if step.mismatch),
        score=score_report,
        godview=godview_report,
        comparison_mismatches=tuple(driver.comparison_mismatches),
        total_queries=loaded.total_queries,
    )


# ---------------------------------------------------------------------------
# 读入 / 校验（与 `live` 同一条"两份文件必须描述同一局"口径）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LoadedRun:
    meta: dict[str, Any]
    player_count: int
    human_seat: int | None
    journal_path: Path
    actions: dict[int, Action]
    """``answers_seq`` → 被接受的记录动作（驱动 + 对照的唯一真源）。"""
    action_record_seqs: dict[int, int]
    """``answers_seq`` → 应答记录自己的 ``seq``（人读的「记录 seq=511」）。"""
    identities: tuple[Identity | None, ...]
    revealed: dict[int, Identity]
    """阵亡 / 亮明事件里公开的身份（免费标签的优先来源；未亮明的幸存者才回落发牌表）。"""
    recorded_actions: int
    total_queries: int
    query_aliases: frozenset[int]
    """``--seq`` 可以写的数：询问 seq 与它的动作记录 seq（票面的人读口径是后者）。"""


def _load_run(runs_dir: Path, run: str) -> _LoadedRun:
    from sgs_web.replay import read_run_meta

    if not run:
        raise AiEvalSpecError("录像名不能为空")
    meta_path = runs_dir / f"{run}.meta.json"
    journal_path = runs_dir / f"{run}.jsonl"
    try:
        meta = read_run_meta(runs_dir, run)
    except (ValueError, OSError) as error:
        raise AiEvalSpecError(f"侧车元数据读不动：{meta_path}（{error}）") from error
    if not journal_path.is_file():
        raise AiEvalSpecError(f"找不到记录流：{journal_path}")
    try:
        records = read_journal(journal_path)
    except (ValueError, JournalVersionError, OSError) as error:
        raise AiEvalSpecError(f"记录流读不动：{journal_path}（{error}）") from error
    if not records or getattr(records[0], "kind", None) != "header":
        raise AiEvalSpecError(f"记录流首行不是 header：{journal_path}")
    _check_same_game(records[0], meta, journal_path)

    actions: dict[int, Action] = {}
    action_record_seqs: dict[int, int] = {}
    identities: list[Identity | None] = [None] * int(meta["player_count"])
    revealed: dict[int, Identity] = {}
    query_seqs: set[int] = set()
    for record in records:
        kind = getattr(record, "kind", None)
        if kind == "query":
            query_seqs.add(record.query.seq)
        elif kind == "action" and getattr(record, "accepted", False):
            actions[record.answers_seq] = record.action
            action_record_seqs[record.answers_seq] = record.seq
        elif kind == "delta" and getattr(record, "delta_type", None) == "identity_deal":
            seat = getattr(record, "seat", None)
            if isinstance(seat, int) and 0 <= seat < len(identities):
                identities[seat] = record.identity
        elif kind == "delta" and getattr(record, "delta_type", None) == "identity_reveal":
            seat = getattr(record, "seat", None)
            if isinstance(seat, int) and 0 <= seat < len(identities):
                revealed[seat] = record.identity

    tape_actions = _meta_tape(meta, meta_path)
    _check_tape_matches_journal(tape_actions, actions, meta_path, journal_path)
    human_seat = meta.get("human_seat")
    if human_seat is not None and (
        not isinstance(human_seat, int)
        or isinstance(human_seat, bool)
        or not 0 <= human_seat < int(meta["player_count"])
    ):
        raise AiEvalSpecError(f"侧车元数据的人类座位非法：{human_seat!r}（{meta_path}）")
    aliases = set(query_seqs)
    aliases.update(
        action_record_seqs[seq] for seq in query_seqs if seq in action_record_seqs
    )
    return _LoadedRun(
        meta=meta,
        player_count=int(meta["player_count"]),
        human_seat=human_seat,
        journal_path=journal_path,
        actions=actions,
        action_record_seqs=action_record_seqs,
        identities=tuple(identities),
        revealed=revealed,
        recorded_actions=len(actions),
        total_queries=len(query_seqs),
        query_aliases=frozenset(aliases),
    )


def _meta_tape(meta: dict[str, Any], meta_path: Path) -> dict[int, Action]:
    raw = meta.get("tape")
    if not isinstance(raw, list):
        raise AiEvalSpecError(f"侧车元数据的 tape 不是数组：{meta_path}")
    tape: dict[int, Action] = {}
    for entry in raw:
        try:
            action = parse_action(entry)
        except Exception as error:  # noqa: BLE001 - 坏磁带要点名文件
            raise AiEvalSpecError(f"侧车元数据的 tape 解析不动：{meta_path}（{error}）") from error
        tape[action.answers_seq] = action
    return tape


def _check_tape_matches_journal(
    tape: dict[int, Action],
    journal: dict[int, Action],
    meta_path: Path,
    journal_path: Path,
) -> None:
    """磁带（重放输入）与记录流（真值）必须逐条相同：改过任一份都响亮失败。"""
    if set(journal) - set(tape):
        missing = sorted(set(journal) - set(tape))[:5]
        raise AiEvalSpecError(
            f"侧车磁带的应答比记录流少（seq={missing}…）：两份文件不是同一局"
            f"（{meta_path} / {journal_path}）"
        )
    for seq, recorded in journal.items():
        if seq in tape and tape[seq] != recorded:
            raise AiEvalSpecError(
                f"侧车磁带与记录流的动作不一致（seq={seq}：磁带={tape[seq]!r}，"
                f"记录={recorded!r}）：录像被改过（{meta_path} / {journal_path}）"
            )


def _check_same_game(header: Any, meta: dict[str, Any], journal_path: Path) -> None:
    recorded = {
        "seed": header.seed,
        "player_count": header.rule_config.player_count,
        "pack_id": header.pack_id,
        "deck_profile": header.deck_profile,
    }
    for field, value in recorded.items():
        if meta.get(field) != value:
            raise AiEvalSpecError(
                f"记录流与侧车元数据不是同一局（{field}：记录={value!r}，"
                f"侧车={meta.get(field)!r}）：{journal_path}"
            )


def _engine_for(meta: dict[str, Any]) -> Engine:
    from sgs_web.replay import engine_from_meta

    try:
        return engine_from_meta(meta)
    except Exception as error:  # noqa: BLE001 - 重建失败点名原因
        raise AiEvalRunError(f"重建对局失败：{type(error).__name__}: {error}") from error


# ---------------------------------------------------------------------------
# 驱动：磁带喂动作，AI 座位另问一次（影子决策）
# ---------------------------------------------------------------------------


class _StopReplay(Exception):
    """`--seq` 命中后，在**下一次**参与询问时停下（不依赖引擎的私有步进接口）。"""


@dataclass(frozen=True)
class _ScoreSample:
    """一个座位步的两种基线预测 + 真值（避免用元组下标读同一组字段）。"""

    uniform: tuple[float, ...]
    prior: tuple[float, ...]
    truth: Identity


@dataclass
class _OpenJudgment:
    judged: str
    truth: str
    start_seq: int
    last_seq: int
    viewer_queries: int
    all_queries: int


class _Driver:
    """一局的重放实现：所有参与者共用它（一个座位一个薄壳）。"""

    def __init__(
        self,
        *,
        engine: Engine,
        loaded: _LoadedRun,
        explain: bool,
        score: bool,
        godview: bool,
        target_seq: int | None,
        target_seat: int | None,
        window: int,
    ) -> None:
        self.engine = engine
        self.loaded = loaded
        self.explain = explain
        self.score = score
        self.godview = godview
        self.target_seq = target_seq
        self.target_seat = target_seat
        self.window = window
        self.steps: list[ExplainStep] = []
        self.queries = 0
        self._stop_after = False
        self._ai: dict[int, HeuristicPlayer] = {
            seat: HeuristicPlayer(ai_stream(loaded.meta["seed"], seat))
            for seat in range(loaded.player_count)
            if seat != loaded.human_seat
        }
        self._narratives: dict[int, Any] = {}
        if explain:
            from sgs_web.narrative import NarrativeCursor

            heroes = tuple(loaded.meta.get("heroes") or ())
            for seat in self._ai:
                self._narratives[seat] = NarrativeCursor(
                    viewer=seat, heroes=heroes, pack_id=loaded.meta["pack_id"]
                )
        self._score_samples: list[_ScoreSample] = []
        self._score_queries = 0
        self._score_seats: set[int] = set()
        self._initial_counts = _initial_counts(loaded)
        self._open: dict[tuple[int, int], _OpenJudgment] = {}
        self._wrong: list[WrongJudgment] = []
        self._decisions = 0
        self.comparison_mismatches: list[tuple[int, str, str, str | None]] = []

    def participant(self, seat: int) -> "_SeatParticipant":
        return _SeatParticipant(self, seat)

    # -- 一次询问 -----------------------------------------------------------

    def respond(self, seat: int, query: Any) -> Action:
        if self._stop_after:
            raise _StopReplay
        self.queries += 1
        if self._narratives:
            self._feed_narratives()
        recorded = self.loaded.actions.get(query.seq)
        if recorded is None:
            raise AiEvalRunError(
                f"重放分歧：询问 {_kind_name(query.kind)} seq={query.seq}（座位 {seat}）"
                f"在记录流里没有应答——录像被截断或引擎版本不一致"
            )
        if recorded.answers_seq != query.seq:
            raise AiEvalRunError(
                f"重放分歧：记录动作 answers_seq={recorded.answers_seq} 对不上询问 seq={query.seq}"
            )
        if seat == self.loaded.human_seat:
            if self.godview:
                self._track_godview(seat, query)
            # 不筛座位（`--explain` 全量）⇒ 人类询问**跳过**，只解释 AI 座位；只有显式
            # `--seq` / `--seat` 点到人类询问时才报错（那是在问一个不存在的 AI 决策）。
            if (
                self.explain
                and (self.target_seq is not None or self.target_seat is not None)
                and self._matches(seat, query)
            ):
                raise AiEvalSpecError(
                    f"seq={query.seq}（记录 seq={self.loaded.action_record_seqs.get(query.seq)}）"
                    f"是人类的询问：没有 AI 决策可解释"
                )
            return recorded

        ai_action, captured = self._ask_ai(seat, query)
        resolved, resolution_error = _resolve_ai_action(
            self.engine.state, query, ai_action
        )
        mismatch = resolution_error is not None or resolved != recorded
        if mismatch:
            self.comparison_mismatches.append(
                (
                    query.seq,
                    _action_label(recorded),
                    _action_label(resolved),
                    resolution_error,
                )
            )
        if self.score:
            self._sample_score(seat, query)
        if self.godview:
            self._track_godview(seat, query)
        if self.explain and self._matches(seat, query):
            self.steps.append(
                self._explain(
                    seat,
                    query,
                    ai_action,
                    resolved,
                    resolution_error,
                    recorded,
                    captured,
                )
            )
            if self.target_seq is not None:
                self._stop_after = True
        return recorded

    def _matches(self, seat: int, query: Any) -> bool:
        if self.target_seat is not None and seat != self.target_seat:
            return False
        if self.target_seq is None:
            return True
        record_seq = self.loaded.action_record_seqs.get(query.seq)
        return self.target_seq in (query.seq, record_seq)

    def _ask_ai(
        self, seat: int, query: Any
    ) -> tuple[Action, list[tuple[Action, float, tuple[tuple[str, float], ...]]]]:
        """在**真实的** `HeuristicPlayer.respond` 路径上记录分项。

        临时把模块的 `_SCORERS` 包一层（单线程工具；`finally` 恢复），与
        `tests/test_heuristic_ai.py` 的 parity 用例同一种观察法：不复制候选组装、
        不复制打分器，分数与分项仍由生产代码算出。
        """
        player = self._ai[seat]
        if not self.explain:
            try:
                return player.respond(query), []
            except Exception as error:  # noqa: BLE001 - 点名 seq / 座位
                raise AiEvalRunError(
                    f"AI 应答失败：seat={seat} {_kind_name(query.kind)} seq={query.seq}"
                    f"（{type(error).__name__}: {error}）"
                ) from error
        captured: list[tuple[Action, float, tuple[tuple[str, float], ...]]] = []
        original = heuristic_scoring._SCORERS

        def wrap(kind: Any, scorer: Any) -> Any:
            def traced(candidate_query: Any, action: Action) -> float:
                with Score.tracing():
                    value = scorer(candidate_query, action)
                items = getattr(value, "items", ())
                captured.append((action, float(value), tuple(items)))
                return value

            return traced

        heuristic_scoring._SCORERS = MappingProxyType(
            {kind: wrap(kind, scorer) for kind, scorer in original.items()}
        )
        try:
            chosen = player.respond(query)
        except Exception as error:  # noqa: BLE001 - 点名 seq / 座位
            raise AiEvalRunError(
                f"AI 应答失败：seat={seat} {_kind_name(query.kind)} seq={query.seq}"
                f"（{type(error).__name__}: {error}）"
            ) from error
        finally:
            heuristic_scoring._SCORERS = original
        return chosen, captured

    # -- 五段解释 -----------------------------------------------------------

    def _explain(
        self,
        seat: int,
        query: Any,
        ai_action: Action,
        resolved: Action,
        resolution_error: str | None,
        recorded: Action,
        captured: list[tuple[Action, float, tuple[tuple[str, float], ...]]],
    ) -> ExplainStep:
        view = query.view
        if not captured:
            raise AiEvalRunError(
                f"生产代码没有给任何候选打分：seat={seat} {_kind_name(query.kind)} seq={query.seq}"
            )
        chosen_index = _index_of_action(captured, ai_action)
        if chosen_index is None:
            raise AiEvalRunError(
                f"AI 的返回值不在这批候选里：seat={seat} seq={query.seq}"
            )
        best = max(score for _, score, _ in captured)
        winners = [index for index, (_, score, _) in enumerate(captured) if score == best]
        candidates = tuple(
            Candidate(action=action, score=score, items=items, chosen=index == chosen_index)
            for index, (action, score, items) in enumerate(captured)
        )
        return ExplainStep(
            query_seq=query.seq,
            record_seq=self.loaded.action_record_seqs.get(query.seq),
            actor=seat,
            kind=_kind_name(query.kind),
            prompt=str(getattr(query, "prompt", "") or ""),
            view=view,
            identities=_identity_reads(view),
            dodge=_dodge_reads(view, resolved),
            lethal=_lethal_reads(view, resolved),
            prune=_prune_lines(
                self.engine.state,
                view,
                seat,
                _kind_name(query.kind),
                candidates,
                focus=resolved,
            ),
            candidates=candidates,
            chosen=ai_action,
            resolved=resolved,
            resolution_error=resolution_error,
            recorded=recorded,
            mismatch=resolution_error is not None or resolved != recorded,
            best=best,
            winners=len(winners),
            tie=len(winners) > 1,
            public_window=self._public_window(seat),
        )

    def _feed_narratives(self) -> None:
        records = self.engine.records
        for cursor in self._narratives.values():
            cursor.extend(records)

    def _public_window(self, seat: int) -> tuple[str, ...]:
        cursor = self._narratives.get(seat)
        if cursor is None:
            return ()
        entries = cursor.entries[-self.window :]
        return tuple(str(entry.get("text", "")) for entry in entries)

    # -- 尺子 ---------------------------------------------------------------

    def _sample_score(self, seat: int, query: Any) -> None:
        """记一个 AI 询问里每个**当时还暗着**的座位步。

        真值 = 记录流里的 `identity_deal`（与阵亡 / 终局亮明是同一张表）；采样只取
        `view.players[*].identity is None` 的时刻 ⇒ 标签是"后来才免费拿到"的那一份，
        亮明后的座位不进来（否则等于拿答案算答案，会把读数灌水）。
        """
        _require_identity_mode(self.loaded, "score")
        view = query.view
        self._score_queries += 1
        truth = self.loaded.identities
        hidden = [
            player.seat
            for player in view.players
            if player.alive and player.identity is None
        ]
        if not hidden:
            return
        remaining = _remaining_identities(view)
        if remaining is None or len(remaining) != len(hidden):
            raise AiEvalSpecError(
                f"身份配比与视图对不上（seat={seat} seq={query.seq}）："
                f"剩余 {remaining!r}，暗座 {hidden!r}"
            )
        unknown = len(hidden)
        for target in hidden:
            # 免费标签优先读**阵亡 / 亮明**；终局仍暗的幸存者（如只剩内奸）用发牌表兜底——
            # 终局的 side 已把它公开，发牌表是同一张事实的内部投影，不是另一份答案。
            label = self.loaded.revealed.get(target, truth[target])
            if label not in IDENTITY_ORDER:
                raise AiEvalSpecError(f"座位 {target} 的身份不可用：{label!r}")
            uniform = tuple(
                remaining.count(identity) / unknown for identity in IDENTITY_ORDER
            )
            prior = tuple(
                self._initial_counts[identity] / self.loaded.player_count
                for identity in IDENTITY_ORDER
            )
            self._score_samples.append(_ScoreSample(uniform, prior, label))
            self._score_seats.add(target)

    def score_report(self) -> ScoreReport:
        if not self._score_samples:
            raise AiEvalSpecError("--score 没有采到任何座位步（没有暗着的座位）")
        baselines = []
        for name, field in (("配比均匀后验", "uniform"), ("恒先验", "prior")):
            predictions = [getattr(sample, field) for sample in self._score_samples]
            truths = [sample.truth for sample in self._score_samples]
            baselines.append(
                BaselineScore(
                    name=name,
                    log_loss=_log_loss(predictions, truths),
                    brier=_brier(predictions, truths),
                    ece=_ece(predictions, truths),
                )
            )
        return ScoreReport(
            samples=len(self._score_samples),
            seats=len(self._score_seats),
            queries=self._score_queries,
            baselines=tuple(baselines),
        )

    # -- 上帝视角 -----------------------------------------------------------

    def _track_godview(self, seat: int, query: Any) -> None:
        if seat == self.loaded.human_seat:
            return
        self._decisions += 1
        for open_judgment in self._open.values():
            open_judgment.all_queries += 1
        for (viewer, _target), open_judgment in self._open.items():
            if viewer == seat:
                open_judgment.viewer_queries += 1
        view = query.view
        mine = view.players[seat].identity
        if mine is None:
            return
        hidden = {
            player.seat
            for player in view.players
            if player.alive and player.identity is None
        }
        for target in range(len(view.players)):
            if target == seat:
                continue
            key = (seat, target)
            if target not in hidden:
                self._close(key, query.seq)
                continue
            enemy, ally = heuristic_scoring._identity_relation(view, target)
            judged = _relation_label(enemy, ally)
            truth = _true_relation(self.engine.state, mine, self.loaded.identities[target])
            current = self._open.get(key)
            if current is not None and current.judged != judged:
                self._close(key, query.seq)
                current = None
            if judged in (_REL_ENEMY, _REL_ALLY, _REL_NEUTRAL) and current is None:
                self._open[key] = _OpenJudgment(
                    judged=judged,
                    truth=truth,
                    start_seq=query.seq,
                    last_seq=query.seq,
                    viewer_queries=1,
                    all_queries=1,
                )
            elif key in self._open:
                self._open[key].last_seq = query.seq

    def _close(self, key: tuple[int, int], seq: int) -> None:
        current = self._open.pop(key, None)
        if current is None:
            return
        if current.judged != current.truth:
            self._wrong.append(
                WrongJudgment(
                    viewer=key[0],
                    target=key[1],
                    judged=current.judged,
                    truth=current.truth,
                    start_seq=current.start_seq,
                    end_seq=seq,
                    viewer_queries=current.viewer_queries,
                    all_queries=current.all_queries,
                )
            )

    def godview_report(self) -> GodViewReport:
        for key in list(self._open):
            current = self._open[key]
            self._close(key, current.last_seq)
        wrong = tuple(
            sorted(
                self._wrong,
                key=lambda item: (
                    -item.cost,
                    item.viewer,
                    item.target,
                    item.start_seq,
                ),
            )
        )
        return GodViewReport(wrong=wrong, decisions=self._decisions, open_at_end=len(self._open))


class _SeatParticipant:
    """`Engine.run` 只认 `respond(query)`；座位号由壳带上，驱动仍是同一份。"""

    def __init__(self, driver: _Driver, seat: int) -> None:
        self._driver = driver
        self._seat = seat

    def respond(self, query: Any) -> Action:
        return self._driver.respond(self._seat, query)


# ---------------------------------------------------------------------------
# 推断 / 剪枝 / 读数
# ---------------------------------------------------------------------------


def _resolve_ai_action(state: Any, query: Any, action: Action) -> tuple[Action, str | None]:
    """把 AI 的复合应答归一成引擎**会落账**的那条动作（与 `Engine.submit` 同一条解析器）。

    转化技的确认（`ActivateSkill(skill, picks, choices)`）不在候选里，`submit` 由
    `resolve_conversion_confirmation` 对回内部候选池里的普通使用 / 打出动作，记录流里
    因此**没有**这条复合动作（票 36 的 W4-fix-2 口径）。不归一就会把"同一个决策"错报成
    版本分歧——`--explain` 的对照段要的是"决策是否一致"，不是"提交形状是否一致"。
    """
    from sgs.skills import owned_conversion_skill_ids, resolve_conversion_confirmation
    from sgs.state import ActivateSkill

    if not isinstance(action, ActivateSkill):
        return action, None
    if _kind_name(query.kind) not in ("PLAY_PHASE", "DODGE", "PEACH", "RESPOND"):
        return action, None
    if action.skill_id not in owned_conversion_skill_ids(state, query.actor):
        return action, None
    steps = (getattr(query, "transform_preactivations", None) or {}).get(
        action.skill_id, ()
    )
    if not steps:
        return action, None
    resolution = resolve_conversion_confirmation(
        state,
        query.actor,
        action.skill_id,
        action.picks,
        action.choices,
        getattr(query, "transform_actions", ()) or (),
        steps=steps,
    )
    if isinstance(resolution, str):
        return action, resolution
    return resolution, None


def _kind_name(kind: Any) -> str:
    return getattr(kind, "name", None) or str(kind)


def _index_of_action(
    captured: Sequence[tuple[Action, float, tuple[tuple[str, float], ...]]],
    chosen: Action,
) -> int | None:
    for index, (action, _, _) in enumerate(captured):
        if action is chosen:
            return index
    for index, (action, _, _) in enumerate(captured):
        if action == chosen:
            return index
    return None


def _identity_reads(view: Any) -> tuple[IdentityRead, ...]:
    reads = []
    for player in view.players:
        if player.seat == view.seat:
            reads.append(
                IdentityRead(
                    seat=player.seat,
                    public_identity=player.identity,
                    alive=player.alive,
                    hp=player.hp,
                    hp_max=player.hp_max,
                    armor=player.armor,
                    hand_count=player.hand_count,
                    enemy=0.0,
                    ally=1.0,
                    label=_REL_SELF,
                    confidence=1.0,
                )
            )
            continue
        enemy, ally = heuristic_scoring._identity_relation(view, player.seat)
        # 归一 ``-0.0``（``-0.0 == 0``）：输出里不要出现 "P(敌)=-0.00"。
        enemy = 0.0 if enemy == 0 else enemy
        ally = 0.0 if ally == 0 else ally
        # 判定读**同一个** `_identity_relation`（内奸那行是动态局面函数）；已亮明的事实由
        # 同一行的「反贼 / 忠臣 / 主公」单独展示，不把静态敌对表伪装成"AI 的判定"。
        label = _relation_label(enemy, ally)
        confidence = _relation_confidence(enemy, ally)
        fraction = _relation_fraction(view, player.seat, enemy, ally)
        reads.append(
            IdentityRead(
                seat=player.seat,
                public_identity=player.identity,
                alive=player.alive,
                hp=player.hp,
                hp_max=player.hp_max,
                armor=player.armor,
                hand_count=player.hand_count,
                enemy=enemy,
                ally=ally,
                label=label,
                confidence=confidence,
                fraction=fraction,
            )
        )
    return tuple(reads)


def _relation_label(enemy: float, ally: float) -> str:
    """三值判定：敌 / 友 / 中立；两类都拿不准（敌友平票）才写「未定」。"""
    neutral = max(0.0, 1.0 - enemy - ally)
    best = max(enemy, ally, neutral)
    if best == 0.0:
        return _REL_NEUTRAL
    winners = [
        name
        for name, value in (
            (_REL_ENEMY, enemy),
            (_REL_ALLY, ally),
            (_REL_NEUTRAL, neutral),
        )
        if value == best
    ]
    if len(winners) == 1:
        return winners[0]
    if _REL_NEUTRAL in winners:
        return _REL_NEUTRAL
    return _REL_UNDECIDED


def _relation_confidence(enemy: float, ally: float) -> float:
    return max(enemy, ally, max(0.0, 1.0 - enemy - ally))


def _relation_fraction(
    view: Any, seat: int, enemy: float, ally: float
) -> tuple[int, int, int] | None:
    """配比均匀后验的分数写法（``3/4``）；内奸的动态局面函数不硬套分数。"""
    remainder = heuristic_scoring._unknown_identity_remainder(view)
    if remainder is None:
        return None
    remaining, unknown = remainder
    mine = view.players[view.seat].identity
    if mine is None or mine == Identity.RENEGADE or unknown <= 0:
        return None
    if view.players[seat].identity is not None:
        return None
    enemies = heuristic_scoring._enemy_identities(mine)
    allies = heuristic_scoring._ally_identities(mine)
    enemy_count = sum(1 for identity in remaining if identity in enemies)
    ally_count = sum(1 for identity in remaining if identity in allies)
    if not math.isclose(enemy_count / unknown, enemy, abs_tol=1e-12):
        return None
    if not math.isclose(ally_count / unknown, ally, abs_tol=1e-12):
        return None
    return (enemy_count, ally_count, unknown)


def _dodge_reads(view: Any, action: Action) -> tuple[tuple[int, float | None], ...]:
    targets = _action_targets(action)
    return tuple(
        (seat, heuristic_scoring._dodge_probability(view, seat)) for seat in targets
    )


def _lethal_reads(view: Any, action: Action) -> tuple[tuple[int, int, bool | None], ...]:
    card = getattr(action, "card", None)
    reads = []
    for seat in _action_targets(action):
        player = view.players[seat]
        lethal = None
        if card is not None and heuristic_scoring._damage_card(card):
            try:
                lethal = heuristic_scoring._lethal_to(card, view, seat)
            except KeyError:  # pragma: no cover - 表外牌名（人为载荷）
                lethal = None
        reads.append((seat, player.hp + player.armor, lethal))
    return tuple(reads)


def _action_targets(action: Action) -> tuple[int, ...]:
    targets = tuple(getattr(action, "targets", ()) or ())
    designations = tuple(getattr(action, "designations", ()) or ())
    return tuple(dict.fromkeys((*targets, *designations)))


def _prune_lines(
    state: Any,
    view: Any,
    actor: int,
    kind: str,
    candidates: Sequence[Candidate],
    *,
    focus: Action | None,
) -> tuple[PruneLine, ...]:
    """剪枝面：手牌里哪张不在候选里 + 选中牌的目标面里谁被剪掉。

    数据源 = **引擎的合法行动**（候选集合，来自 `legal_actions`）与引擎的规则读数
    （`behavior_for` / `check_targets` / `distance` / `attack_range`）；这里只把"为什么"
    翻译成人话，**不重算**候选。故剪枝面与 AI 真正看到的面是同一份。

    **只对出牌阶段开目标剪枝**：响应窗口（`DODGE` / `PEACH` / `RESPOND`）的候选是
    "能打出的响应牌"，不是"这张牌能指定谁"；把出牌阶段的目标规则套上去会给出错误原因。
    非出牌阶段返回一行说明，不硬造剪枝。
    """
    from sgs.effects import attack_range, behavior_for, check_targets, distance

    if kind != "PLAY_PHASE":
        return (
            PruneLine(
                subject="剪枝面",
                reason="本询问不是出牌阶段（响应 / 选项 / 选牌），不做座位目标剪枝",
            ),
        )

    lines: list[PruneLine] = []
    used_cards: set[int] = set()
    for candidate in candidates:
        action = candidate.action
        card = getattr(action, "card", None)
        if card is not None:
            used_cards.add(card.card_id)
        if getattr(action, "kind", None) == "ActivateSkill":
            # `ActivateSkill.picks` 是**成本牌 id**（丈八蛇矛 / 贯石斧…）；`SelectCards.picks`
            # 是面内**位置**，两者不能混为一谈。
            used_cards.update(int(pick) for pick in (getattr(action, "picks", ()) or ()))
    for card in view.self_hand:
        if card.card_id in used_cards:
            continue
        lines.append(
            PruneLine(
                subject=f"手牌{_card_label(card)}",
                reason=_card_prune_reason(state, view, actor, card),
            )
        )

    chosen = focus
    card = getattr(chosen, "card", None) if chosen is not None else None
    face_note: str | None = None
    if card is None:
        card_candidates = [
            candidate
            for candidate in candidates
            if getattr(candidate.action, "card", None) is not None
        ]
        if not card_candidates:
            return tuple(lines)
        best = max(candidate.score for candidate in card_candidates)
        top = next(candidate for candidate in card_candidates if candidate.score == best)
        card = top.action.card
        face_note = (
            f"未选中牌（{_kind_name(getattr(chosen, 'kind', chosen))}）；"
            f"按最高分候选 {_card_label(card)} 的目标面分析"
        )
    legal_targets: set[int] = set(check_targets(state, actor, card))
    for candidate in candidates:
        action = candidate.action
        if getattr(action, "card", None) is None:
            continue
        if action.card.card_id != card.card_id:
            continue
        legal_targets.update(_action_targets(action))

    rule = behavior_for(card).target_rule
    seat_lines = [
        PruneLine(
            subject=f"座位{seat}",
            reason=_seat_prune_reason(state, view, actor, seat, card, rule),
        )
        for seat, player in enumerate(view.players)
        if seat != actor and seat not in legal_targets
    ]
    if face_note is not None:
        lines.append(PruneLine(subject="座位目标面", reason=face_note))
    if seat_lines:
        lines.extend(seat_lines)
    else:
        lines.append(
            PruneLine(
                subject="座位目标面",
                reason="没有座位被剪掉（这张牌对全部其他存活座位合法）",
            )
        )
    return tuple(lines)


def _distance_limit(rule: Any, state: Any, actor: int) -> tuple[str, int] | None:
    """这张牌的**距离上限**：``(上限名, 数值)``；不查距离 ⇒ ``None``。

    两条来源：攻击范围（``needs_distance``，【杀】族）与牌面额定上限（``max_distance``，
    【顺手牵羊】= 1）。手牌级与座位级剪枝共用这一份读法，不各写一条 if 级联。
    """
    from sgs.effects import attack_range

    if rule.needs_distance:
        return "攻击范围", attack_range(state, actor)
    if rule.max_distance is not None:
        return "牌面上限", rule.max_distance
    return None


def _card_prune_reason(state: Any, view: Any, actor: int, card: Any) -> str:
    from sgs.effects import behavior_for, distance

    try:
        behavior = behavior_for(card)
    except KeyError:  # pragma: no cover - 表外牌名（人为载荷）
        return "引擎没有给出合法行动（牌名不在行为表里）"
    rule = behavior.target_rule
    limit = _distance_limit(rule, state, actor)
    if limit is not None:
        label, reach = limit
        distances = [
            distance(state, actor, player.seat)
            for player in view.players
            if player.seat != actor and player.alive
        ]
        if distances and min(distances) > reach:
            return f"距离不足（最近距离 {min(distances)} > {label} {reach}）"
        return "没有合法目标（距离 / 目标规则）"
    if rule.self_only:
        return "当前使用条件不满足（只能对自己，且条件未成立）"
    if rule.rated_targets == 0 and not rule.includes_self:
        return "只能响应时使用"
    return "没有合法目标（牌面条件）"


def _seat_prune_reason(
    state: Any, view: Any, actor: int, seat: int, card: Any, rule: Any
) -> str:
    from sgs.effects import distance

    player = view.players[seat]
    if not player.alive:
        return "已阵亡"
    if rule.self_only:
        return "不是合法目标（这张牌只能对自己）"
    limit = _distance_limit(rule, state, actor)
    if limit is not None:
        label, reach = limit
        dist = distance(state, actor, seat)
        if dist > reach:
            return f"距离不足（距离 {dist} > {label} {reach}）"
        return "不是合法目标（目标规则 / 牌面条件）"
    return "不是合法目标（目标规则 / 牌面条件）"


# ---------------------------------------------------------------------------
# 基线统计
# ---------------------------------------------------------------------------


def _initial_counts(loaded: _LoadedRun) -> dict[Identity, int]:
    roster = IDENTITY_ROSTERS.get(loaded.player_count)
    if roster is None:
        return {identity: 0 for identity in IDENTITY_ORDER}
    return {identity: roster.count(identity) for identity in IDENTITY_ORDER}


def _identity_scoring_allowed(loaded: _LoadedRun) -> bool:
    return loaded.player_count in IDENTITY_ROSTERS and all(
        identity in IDENTITY_ORDER for identity in loaded.identities
    )


def _require_identity_mode(loaded: _LoadedRun, mode: str) -> None:
    """`--score` / `--godview` 需要 4~8 人身份局；一条判据、按模式给不同的话。"""
    if not _identity_scoring_allowed(loaded):
        raise AiEvalSpecError(
            f"本局不是 4~8 人身份局（或身份配比不可用）：--{mode} 没有免费身份标签"
        )


def _remaining_identities(view: Any) -> list[Identity] | None:
    roster = IDENTITY_ROSTERS.get(len(view.players))
    if roster is None:
        return None
    remaining = list(roster)
    for player in view.players:
        if player.identity is not None:
            try:
                remaining.remove(player.identity)
            except ValueError:
                return None
    return remaining


def _true_relation(state: Any, mine: Identity, theirs: Identity | None) -> str:
    """上帝视角的**真值**：按真实存活局面求关系（不是 AI 视角里的期望）。

    内奸那一行是**局面函数**（`_renegade_hostility` 按真实两阵营力量差求值），
    `_HOSTILITY` 的内奸行在代码里被注明是历史值、不是今天的取值；故非内奸读静态表、
    内奸读真实局面读出的动态值。隐藏座位在本函数里用**真实身份**（工具侧全知），
    与 AI 视图里的期望配比分开——这正是"上帝视角"与"AI 的判定"的差。
    """
    if theirs is None:
        return _REL_NEUTRAL
    if mine == Identity.RENEGADE:
        relationship = heuristic_scoring._renegade_hostility_from_balance(
            theirs, _true_renegade_balance(state)
        )
        if relationship > 0:
            return _REL_ENEMY
        if relationship < 0:
            return _REL_ALLY
        return _REL_NEUTRAL
    relation = heuristic_scoring._hostility(mine, theirs)
    return {"enemy": _REL_ENEMY, "ally": _REL_ALLY}.get(relation, _REL_NEUTRAL)


def _true_renegade_balance(state: Any) -> tuple[int, int, int]:
    """真实存活局面下的 ``(D, 主公侧存活, 反贼侧存活)``（内奸动态关系的输入）。"""
    alive = [player for player in state.players if player.alive]
    lord_side = sum(
        1 for player in alive if player.identity in (Identity.LORD, Identity.LOYALIST)
    )
    rebel_side = sum(1 for player in alive if player.identity == Identity.REBEL)
    return len(alive) - 1, lord_side, rebel_side


def _log_loss(predictions: Sequence[tuple[float, ...]], truths: Sequence[Identity]) -> float:
    total = 0.0
    for prediction, truth in zip(predictions, truths, strict=True):
        probability = prediction[IDENTITY_ORDER.index(truth)]
        total += -math.log(max(probability, _LOG_FLOOR))
    return total / len(predictions)


def _brier(predictions: Sequence[tuple[float, ...]], truths: Sequence[Identity]) -> float:
    total = 0.0
    for prediction, truth in zip(predictions, truths, strict=True):
        total += sum(
            (probability - (1.0 if identity == truth else 0.0)) ** 2
            for identity, probability in zip(IDENTITY_ORDER, prediction, strict=True)
        )
    return total / len(predictions)


def _ece(
    predictions: Sequence[tuple[float, ...]], truths: Sequence[Identity], *, bins: int = 10
) -> float:
    counts = [0] * bins
    confidence_sum = [0.0] * bins
    correct = [0] * bins
    for prediction, truth in zip(predictions, truths, strict=True):
        best_index = max(range(len(prediction)), key=prediction.__getitem__)
        confidence = prediction[best_index]
        bucket = min(int(confidence * bins), bins - 1)
        counts[bucket] += 1
        confidence_sum[bucket] += confidence
        correct[bucket] += 1 if IDENTITY_ORDER[best_index] == truth else 0
    total = len(predictions)
    ece = 0.0
    for bucket, count in enumerate(counts):
        if count:
            ece += count / total * abs(
                correct[bucket] / count - confidence_sum[bucket] / count
            )
    return ece


# ---------------------------------------------------------------------------
# 渲染（纯函数：同一份报告逐字节一致）
# ---------------------------------------------------------------------------


def render_replay(report: ReplayReport) -> str:
    """把报告渲染成人读文本；输出只依赖报告里的值（无时间 / 无随机 / 无路径前缀）。"""
    lines: list[str] = [_header(report)]
    for step in report.explained:
        lines.extend(_render_step(step))
    if report.score is not None:
        lines.extend(_render_score(report.score))
    if report.godview is not None:
        lines.extend(_render_godview(report.godview))
    if report.comparison_mismatches and not report.explained:
        lines.extend(
            [
                "",
                f"注意：对照不一致 {len(report.comparison_mismatches)} 步"
                "（版本或输入不匹配）；本读数仍以录像状态为准（--explain 可列出）",
            ]
        )
        for seq, recorded, resolved, error in report.comparison_mismatches[:5]:
            detail = f"（{error}）" if error else ""
            lines.append(f"  - seq={seq}：记录={recorded} ≠ 重算={resolved}{detail}")
    if report.mismatches:
        lines.extend(
            [
                "",
                f"对照失败：{len(report.mismatches)} 步的记录动作 ≠ 重算 argmax"
                "（版本或输入不匹配；红线，不静默）",
            ]
        )
        for step in report.mismatches:
            lines.append(
                f"  - seq={step.query_seq}：记录={_action_label(step.recorded)} "
                f"≠ 重算={_action_label(step.chosen)}"
            )
    return "\n".join(lines) + "\n"


def _header(report: ReplayReport) -> str:
    human = "无" if report.human_seat is None else f"座位{report.human_seat}"
    total = report.total_queries or report.queries
    queries = str(total) if report.queries == total else f"{report.queries}/{total}"
    return (
        f"回放 run={report.run} seed={report.seed} {report.player_count}p 人类={human} "
        f"询问={queries} 记录动作={report.recorded_actions}"
    )


def _render_step(step: ExplainStep) -> list[str]:
    record = "?" if step.record_seq is None else str(step.record_seq)
    lines = [
        "",
        f"## 询问 seq={step.query_seq}（记录 seq={record}）座位 {step.actor} · {step.kind}",
    ]
    if step.prompt:
        lines.append(f"提示：{step.prompt}")
    lines.extend(["", "### 输入", *_render_view(step)])
    lines.extend(["", "### 推断", *_render_inference(step)])
    lines.extend(["", "### 剪枝", *_render_prune(step.prune)])
    lines.extend(["", "### 候选", *_render_candidates(step)])
    lines.extend(["", "### 对照", *_render_compare(step)])
    return lines


def _render_view(step: ExplainStep) -> list[str]:
    view = step.view
    me = view.players[view.seat]
    hand = _cards_text(view.self_hand)
    lines = [
        f"- 我：座位{me.seat}（{_identity_text(me.identity)}）体力 {me.hp}/{me.hp_max}"
        f" 护甲 {me.armor} 手牌 {me.hand_count}：{hand}",
    ]
    for player in view.players:
        if player.seat == view.seat:
            continue
        lines.append(
            f"- 座位{player.seat}：{_identity_text(player.identity)} 体力 {player.hp}/{player.hp_max}"
            f" 护甲 {player.armor} 手牌 {player.hand_count}"
            f" 装备 {_cards_text(player.equip)} 判定 {_cards_text(player.judge)}"
            f"{'' if player.alive else ' · 已阵亡'}"
        )
    revealed = [
        f"座位{player.seat}={_identity_text(player.identity)}"
        for player in view.players
        if player.identity is not None
    ]
    lines.append(
        f"- 公开：牌堆 {view.deck_count} · 弃牌 {len(view.discard)} · 处理区 "
        f"{len(view.processing)} · 回合 {view.turn_seat} · 阶段 {_phase_text(view.phase)}"
    )
    if revealed:
        lines.append("- 已亮明身份：" + " ".join(revealed))
    lines.append(f"- 公开面窗口（观察者=座位{view.seat}，最近 {len(step.public_window)} 条）：")
    if not step.public_window:
        lines.append("  - （无公开叙事）")
    for entry in step.public_window:
        lines.append(f"  - {entry}")
    return lines


def _render_inference(step: ExplainStep) -> list[str]:
    lines = ["- 身份判定（P(敌) / P(友) 来自 AI 的同一函数 `_identity_relation`）："]
    for read in step.identities:
        if read.label == _REL_SELF:
            lines.append(f"  - 座位{read.seat}：本人（{_identity_text(read.public_identity)}）")
            continue
        if read.public_identity is not None:
            lines.append(
                f"  - 座位{read.seat}：{_identity_text(read.public_identity)} · "
                f"判定={read.label}({read.confidence:.2f}) · "
                f"决策权重 P(敌)={read.enemy:.2f} P(友)={read.ally:.2f}"
            )
            continue
        enemy_fraction = ""
        ally_fraction = ""
        if read.fraction:
            enemy_count, ally_count, unknown = read.fraction
            enemy_fraction = f"={enemy_count}/{unknown}"
            ally_fraction = f"={ally_count}/{unknown}"
        lines.append(
            f"  - 座位{read.seat}：未知 · "
            f"P(敌)={read.enemy:.2f}{enemy_fraction} "
            f"P(友)={read.ally:.2f}{ally_fraction} · "
            f"判定={read.label}({read.confidence:.2f})"
        )
    for seat, probability in step.dodge:
        shown = "—（记牌不可用）" if probability is None else f"{probability:.2f}"
        lines.append(f"- P(闪)：座位{seat} {shown}")
    for seat, needed, lethal in step.lethal:
        verdict = "—" if lethal is None else ("是（当场击杀）" if lethal else "否")
        lines.append(f"- 斩杀线：座位{seat} 体力+护甲={needed} 本张击杀={verdict}")
    return lines


def _render_prune(prune: Sequence[PruneLine]) -> list[str]:
    if not prune:
        return ["（没有剪掉的座位 / 手牌）"]
    return [f"- {line.subject}：{line.reason}" for line in prune]


def _render_candidates(step: ExplainStep) -> list[str]:
    lines = [
        "| # | 动作 | 分数 | 分项 |",
        "|---|---|---|---|",
    ]
    ordered = sorted(
        enumerate(step.candidates),
        key=lambda item: (-item[1].score, item[0]),
    )
    for rank, (_, candidate) in enumerate(ordered, start=1):
        mark = " ← 选中" if candidate.chosen else ""
        items = (
            "；".join(
                f"{name} {(0.0 if value == 0 else value):+.4f}"
                for name, value in candidate.items
            )
            or "（无）"
        )
        lines.append(
            f"| {rank} | {_action_label(candidate.action, hand=step.view.self_hand)}{mark} | "
            f"{candidate.score:.4f} | {items} |"
        )
    if step.tie:
        lines.append(
            f"- tie-break：{step.winners} 条候选同为 {step.best:.4f}，"
            f"按 ai:{step.actor} 子流抽签 → 选中 {_action_label(step.chosen, hand=step.view.self_hand)}"
        )
    else:
        lines.append(f"- tie-break：无平分（最高分 {step.best:.4f}）")
    return lines


def _render_compare(step: ExplainStep) -> list[str]:
    lines = [f"- 重算 argmax：{_action_label(step.chosen, hand=step.view.self_hand)}"]
    if step.resolved != step.chosen:
        lines.append(f"- 引擎解析：{_action_label(step.resolved)}")
    lines.append(f"- 记录动作：{_action_label(step.recorded, hand=step.view.self_hand)}")
    if step.resolution_error is not None:
        lines.append(f"- **不一致 ✗（AI 应答解析失败：{step.resolution_error}）**")
    elif step.mismatch:
        lines.append("- **不一致 ✗（版本或输入不匹配）**")
    else:
        lines.append("- 一致 ✓")
    return lines


def _render_score(report: ScoreReport) -> list[str]:
    lines = [
        "",
        "## 尺子（--score）",
        f"- 样本：座位步={report.samples}（AI 询问 {report.queries} 次 · "
        f"不同暗座={report.seats}）；只统计当时还暗着的座位",
        "- 口径：这是**公开配比基线**，不是 AI 的实际判定（票 07 才接）；"
        "ECE = top-label 10 bin。配比均匀后验在「只数暗座」上按构造校准，ECE=0 不是缺陷。",
        "",
        "| 基线 | 对数损失(nats) | Brier | ECE(10 bin) |",
        "|---|---|---|---|",
    ]
    for baseline in report.baselines:
        lines.append(
            f"| {baseline.name} | {baseline.log_loss:.4f} | {baseline.brier:.4f} | "
            f"{baseline.ece:.4f} |"
        )
    return lines


def _render_godview(report: GodViewReport) -> list[str]:
    lines = [
        "",
        "## 上帝视角（--godview）",
        f"- AI 决策={report.decisions} · 错误判定区间={len(report.wrong)} · "
        f"代价 = 在位决策数 × 期间决策数",
    ]
    if not report.wrong:
        lines.append("- 没有错误判定（基线在这个样本上全对）")
        return lines
    lines.extend(
        [
            "",
            "| 观察者 | 目标 | 判定 | 真值 | 起 seq | 止 seq | 在位决策 | 期间决策 | 代价 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for wrong in report.wrong:
        lines.append(
            f"| {wrong.viewer} | {wrong.target} | {wrong.judged} | {wrong.truth} | "
            f"{wrong.start_seq} | {wrong.end_seq} | {wrong.viewer_queries} | "
            f"{wrong.all_queries} | {wrong.cost} |"
        )
    return lines


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _identity_text(identity: Identity | None) -> str:
    return "未知" if identity is None else IDENTITY_LABELS[identity]


def _cards_text(cards: Iterable[Any]) -> str:
    names = [_card_label(card) for card in cards]
    return "无" if not names else "、".join(names)


def _card_label(card: Any) -> str:
    """牌面文字：花色 / 点数 / 显示名都读 `sgs_web.display` 的**唯一**映射表。"""
    from sgs_web.display import RANK_TEXT, SUIT_LABELS, card_display

    display = card_display(card)
    name = display.get("display_name") or card.name
    attribute = display.get("attribute") or "普通"
    raw_suit = getattr(card, "suit", None)
    suit_value = getattr(raw_suit, "value", raw_suit)
    suit = SUIT_LABELS.get(suit_value) if suit_value is not None else None
    rank = getattr(card, "rank", None)
    rank_text = RANK_TEXT.get(rank, "") if rank is not None else ""
    attribute_text = "" if attribute == "普通" else f"·{attribute}"
    virtual = "·虚拟" if getattr(card, "virtual_sources", ()) else ""
    return f"【{name}{attribute_text}{suit['symbol'] if suit else ''}{rank_text}{virtual}】"


def _phase_text(phase: Any) -> str:
    if phase is None:
        return "—"
    return str(getattr(phase, "value", phase))


def _action_label(action: Action, *, hand: Iterable[Any] = ()) -> str:
    kind = getattr(action, "kind", type(action).__name__)
    card = getattr(action, "card", None)
    targets = _action_targets(action)
    hand_names = {int(getattr(item, "card_id", -1)): _card_label(item) for item in hand}
    if kind in ("UseCard", "PlayCard", "Reforge", "SubstituteJudgeCard"):
        pieces = []
        if card is not None:
            pieces.append(_card_label(card))
        if targets:
            pieces.append("→ " + "、".join(f"座位{seat}" for seat in targets))
        return f"{kind}(" + " ".join(pieces) + ")"
    if kind == "DiscardCards":
        return f"DiscardCards({_cards_text(action.cards)})"
    if kind == "SelectCards":
        return "SelectCards(" + ",".join(f"#{pick}" for pick in action.picks) + ")"
    if kind == "ActivateSkill":
        pieces = [str(action.skill_id)]
        picks = tuple(getattr(action, "picks", ()) or ())
        choices = tuple(getattr(action, "choices", ()) or ())
        if picks:
            pieces.append(
                "picks=" + ",".join(hand_names.get(int(pick), f"#{pick}") for pick in picks)
            )
        if choices:
            pieces.append("choices=" + ",".join(str(item) for item in choices))
        return "ActivateSkill(" + " ".join(pieces) + ")"
    if kind == "SelectOption":
        return f"SelectOption({action.choice})"
    if kind == "SelectOptions":
        return "SelectOptions({" + ",".join(str(item) for item in action.choices) + "})"
    if kind == "ArrangeCards":
        return f"ArrangeCards(order={action.order!r} split={action.split})"
    if kind == "Distribute":
        return f"Distribute({dict(action.counts)!r})"
    if kind in ("Pass", "EndPhase"):
        return str(kind)
    return repr(action)
