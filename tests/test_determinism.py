"""跨 ``PYTHONHASHSEED`` 的逐字节确定性（票 08 §7 / 票 10 §4 / 票 26）。

落点 = **库 API 的跨进程确定性**：探针子进程产出的 canonical journal 字节，不经产品 CLI。

同一种子的同一局在**两个不同的哈希种子**下必须产出同一份 canonical journal。
探针走**库 API**（``tests/journal_probe.py``），不经产品 CLI。

**不固定** ``PYTHONHASHSEED``——固定是掩盖而不是检查（票 08 §7）：真出了"迭代 set"
的缺陷，固定值只让本机恰好复现。故本文件另有一条判据：门禁脚本 / pytest 配置里
不得出现 ``PYTHONHASHSEED``，跨值比对必须在子进程里显式给两个不同的值。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
PROBE = TESTS_DIR / "journal_probe.py"
REPLAYS_DIR = TESTS_DIR / "replays"

HASH_SEEDS = ("0", "1")
"""两个不同的哈希种子；由子进程显式给定，与 pytest 进程自己的随机哈希无关。"""

GATE_FILES = (TESTS_DIR / "run_all.sh", TESTS_DIR / "conftest.py", ROOT / "pyproject.toml")
"""唯一门禁的组成文件；它们都不得把 ``PYTHONHASHSEED`` 定死。"""


def _representative_seed() -> int:
    """代表局 = **行数最多**的那条基准的 seed（票 10 §4）。"""
    replays = sorted(REPLAYS_DIR.glob("*.jsonl"))
    assert replays, f"没有基准文件：{REPLAYS_DIR}"
    longest = max(replays, key=lambda path: len(path.read_text(encoding="utf-8").splitlines()))
    header = json.loads(longest.read_text(encoding="utf-8").splitlines()[0])
    return int(header["seed"])


def _probe_bytes(seed: int, hash_seed: str, out: Path) -> bytes:
    """在带指定 ``PYTHONHASHSEED`` 的子进程里跑探针，返回 journal 的原始字节。"""
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, str(PROBE), "--seed", str(seed), "--out", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return out.read_bytes()


def test_the_same_game_is_byte_identical_across_python_hash_seeds(tmp_path):
    seed = _representative_seed()
    runs = {hash_seed: _probe_bytes(seed, hash_seed, tmp_path / f"{hash_seed}.jsonl") for hash_seed in HASH_SEEDS}

    first = runs[HASH_SEEDS[0]]
    assert first, "探针必须产出一条非空的 journal"
    assert first.endswith(b"\n"), "canonical journal 每行（含末行）都以 LF 结尾"
    assert all(payload == first for payload in runs.values()), (
        f"seed={seed} 在两个 PYTHONHASHSEED={HASH_SEEDS} 下的 journal 不是逐字节一致"
    )


def test_the_probe_reaches_a_real_game_end(tmp_path):
    """代表局真的跑完整局：首行 header 的 seed 对得上、末行是 ``game_end``。"""
    seed = _representative_seed()
    lines = _probe_bytes(seed, HASH_SEEDS[0], tmp_path / "probe.jsonl").decode("utf-8").splitlines()

    assert json.loads(lines[0])["kind"] == "header"
    assert json.loads(lines[0])["seed"] == seed
    assert json.loads(lines[-1])["kind"] == "game_end"


def test_the_gate_does_not_pin_pythonhashseed():
    """固定 ``PYTHONHASHSEED`` 是掩盖而不是检查；门禁与 pytest 配置都不得碰它。"""
    for path in GATE_FILES:
        assert "PYTHONHASHSEED" not in path.read_text(encoding="utf-8"), (
            f"{path.name} 固定了 PYTHONHASHSEED（票 08 §7：跨值比对在子进程里显式给两个值）"
        )
