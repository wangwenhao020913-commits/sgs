"""局势打分（`ai-view` 票 09）：四类读数的自洽 / 终局 / 事件打点 / 读数是纯读数。

**不跑判决批**：门禁用构造视图 + 1 局自弈去钉形状（`curve` 的 128 副读数在票面 Answer 里，
由 `ai_eval curve` 实跑）。判据分四组：

1. **分布**：四类之和 == 1、都在 [0, 1]；终局视图收敛到唯一存活方（全灭 = 平局）；
2. **事件**：阵亡 / 体力归零 / 关键装备变更三类都在 `events_between` 的产出里；
3. **纯读数**：接入 `position.read` 前后，同种子的 `journal` / `action_digest` **逐字节相同**
   （票面"接入读数前后决策 diff 为空"）；
4. **可复现**：同一局重跑两次，曲线逐点一致；CLI 输出带 `schema` / `tier` / 读数。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs.agents import HeuristicPlayer, position  # noqa: E402
from sgs.data import Identity, Mode  # noqa: E402
from sgs.engine import ai_stream  # noqa: E402
from sgs.state import PlayerPublic, PlayerView  # noqa: E402
from sgs_batch import GameSpec, run_one  # noqa: E402

from ai_eval.curve import (  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path
    batch_from_seeds,
    events_between,
    prior_for_players,
    run_curve,
)

SEED = 7008
PLAYERS = 4


def _public(
    seat: int,
    *,
    hp: int = 4,
    alive: bool = True,
    hand: int = 2,
    equip: tuple[int, ...] = (),
    identity: Identity | None = None,
) -> PlayerPublic:
    return PlayerPublic(
        seat=seat,
        hp=hp,
        hp_max=4,
        hand_count=hand,
        alive=alive,
        equip=tuple(_card(card_id) for card_id in equip),
        judge=(),
        identity=identity,
    )


def _card(card_id: int):
    from sgs.state import CardRef

    return CardRef(card_id=card_id, type="slash", name="杀", suit="spade", rank=7)


def _view(players: tuple[PlayerPublic, ...], *, seat: int = 0) -> PlayerView:
    return PlayerView(
        seat=seat,
        players=players,
        self_hand=(),
        deck_count=40,
        discard=(),
        processing=(),
        turn_seat=0,
        phase=None,
    )


def _five(*, seat: int = 0) -> PlayerView:
    """5 人身份局的开口视图：主公已明，其余暗。"""
    return _view(
        (
            _public(0, identity=Identity.LORD),
            _public(1),
            _public(2),
            _public(3),
            _public(4),
        ),
        seat=seat,
    )


# ---------------------------------------------------------------------------
# ① 分布
# ---------------------------------------------------------------------------


def test_the_four_probabilities_are_a_distribution() -> None:
    """四类之和 == 1、都在 [0, 1]；开局读数不是"我方会不会赢"的单个数。"""
    reading = position.read(_five())
    values = reading.probabilities
    assert len(values) == 4
    assert sum(values) == pytest.approx(1.0, abs=1e-12)
    assert all(0.0 <= value <= 1.0 for value in values)
    assert all(value > 0.0 for value in values[:3]), "三个阵营开局都该有非零可能"
    assert reading.p("draw") >= 0.0
    assert reading.as_dict() == dict(zip(position.POSITION_SIDES, values, strict=True))


def test_the_prior_is_the_roster_mix() -> None:
    """恒先验 = 配比：5 人局主忠方 2/5、反贼 2/5、内奸 1/5、平局 0。"""
    assert prior_for_players(5) == (0.4, 0.4, 0.2, 0.0)
    assert prior_for_players(4) == (0.5, 0.25, 0.25, 0.0)


def test_a_terminal_view_concentrates_on_the_only_alive_side() -> None:
    """只剩一方存活 ⇒ 该方概率 1（票面"读到终局应趋近确定"的机械版）。"""
    lord_only = _view(
        (
            _public(0, identity=Identity.LORD),
            _public(1, alive=False, hp=0, identity=Identity.LOYALIST),
            _public(2, alive=False, hp=0, identity=Identity.REBEL),
            _public(3, alive=False, hp=0, identity=Identity.REBEL),
            _public(4, alive=False, hp=0, identity=Identity.RENEGADE),
        )
    )
    assert position.read(lord_only).p("lord") == pytest.approx(1.0)

    rebel_only = _view(
        (
            _public(0, alive=False, hp=0, identity=Identity.LORD),
            _public(1, alive=False, hp=0, identity=Identity.LOYALIST),
            _public(2, identity=Identity.REBEL),
            _public(3, identity=Identity.REBEL),
            _public(4, alive=False, hp=0, identity=Identity.RENEGADE),
        )
    )
    assert position.read(rebel_only).p("rebel") == pytest.approx(1.0)

    nobody = _view(
        (
            _public(0, alive=False, hp=0, identity=Identity.LORD),
            _public(1, alive=False, hp=0, identity=Identity.LOYALIST),
            _public(2, alive=False, hp=0, identity=Identity.REBEL),
            _public(3, alive=False, hp=0, identity=Identity.REBEL),
            _public(4, alive=False, hp=0, identity=Identity.RENEGADE),
        )
    )
    assert position.read(nobody).p("draw") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# ② 事件打点
# ---------------------------------------------------------------------------


def test_events_between_marks_death_hp_zero_and_equipment_change() -> None:
    """三类事件都能被标出来（阵亡 / 体力 0 / 装备区增减），且顺序确定。"""
    before = ((0, True, 4, ()), (1, True, 3, (5,)), (2, True, 2, ()))
    after = ((0, False, 0, ()), (1, True, 0, (6,)), (2, True, 2, ()))
    events = events_between(before, after)
    assert events == (
        {"kind": "death", "seat": 0},
        {"kind": "hp_zero", "seat": 0},
        {"kind": "hp_zero", "seat": 1},
        {"kind": "equip_change", "seat": 1, "added": [6], "removed": [5]},
    )
    assert events_between(None, after) == ()


# ---------------------------------------------------------------------------
# ③ 纯读数：接入前后决策 / 记录流零变化
# ---------------------------------------------------------------------------


class _Reader:
    """应答前先读一次局势（读数丢弃）：模拟"接入读数"的最坏形状。"""

    def __init__(self, inner, sink: list[tuple[float, ...]]) -> None:
        self._inner = inner
        self._sink = sink

    def respond(self, query):
        self._sink.append(position.read(query.view).probabilities)
        return self._inner.respond(query)


def _run_game(*, with_reading: bool):
    def factory(spec):
        players = []
        sink: list[tuple[float, ...]] = []
        for seat in range(spec.players):
            inner = HeuristicPlayer(ai_stream(spec.seed, seat))
            players.append(_Reader(inner, sink) if with_reading else inner)
        return players

    return run_one(
        GameSpec(seed=SEED, mode=Mode.IDENTITY, players=PLAYERS),
        participants_factory=factory,
        keep_journal=True,
        capture_decisions=True,
    )


def test_the_reading_does_not_change_decisions_or_the_record_stream() -> None:
    """票面最重要的一条：接入读数前后，**决策与 journal 逐字节相同**。"""
    plain = _run_game(with_reading=False)
    read = _run_game(with_reading=True)
    assert plain.finished and read.finished
    assert read.action_digest == plain.action_digest
    assert read.journal == plain.journal
    assert read.outcome == plain.outcome


# ---------------------------------------------------------------------------
# ④ 曲线可复现 + CLI
# ---------------------------------------------------------------------------


def test_the_curve_is_reproducible_on_the_same_seed() -> None:
    """同一局重放两次：曲线逐点一致（读数只依赖公开视图）。"""
    first = run_curve(batch_from_seeds([SEED]), players=PLAYERS).to_dict()
    second = run_curve(batch_from_seeds([SEED]), players=PLAYERS).to_dict()
    assert first["curves"] == second["curves"]
    assert first["readings"] == second["readings"]
    assert first["event_counts"] == second["event_counts"]
    assert first["curves"][0]["points"], "曲线至少要有一次询问"


def test_the_curve_cli_emits_schema_and_readings() -> None:
    """`python -m ai_eval curve` 的公开面：schema / tier / 读数 / 事件计数。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "ai_eval",
            "curve",
            "--seeds",
            f"{SEED}:{SEED + 1}",
            "--players",
            str(PLAYERS),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "sgs.ai_eval.v1"
    assert payload["tier"] == "curve"
    assert payload["deals"] == 1
    readings = payload["readings"]
    assert readings["samples"] > 0
    assert 0.0 <= readings["log_loss"] <= 10.0
    assert payload["curves"][0]["points"]
    assert set(payload["event_counts"]) <= {"death", "hp_zero", "equip_change"}
