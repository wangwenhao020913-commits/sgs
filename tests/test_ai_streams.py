"""每座位独立随机子流 ``ai:<seat>``（票 12；spec §2.10）。

落点 = ``sgs.engine.ai_stream`` 的公开面。判据四条：

1. 一个座位多取一次数，**不改写**其他座位的后续抽签值（旧的全桌共用队列正相反）；
2. ``ai:<seat>`` 与旧共用 ``ai`` 流**不相等**（证明切换真的发生，不是空转）；
3. 建流点只有 helper 一个入口（静态扫描 + 语法树 + 三处生产建座点的运行时调用点）；
4. 座位号是 0 基非负整数。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from sgs.engine import RandomSource, ai_stream

ROOT = Path(__file__).resolve().parent.parent
SEED = 20261028
DRAWS = 8


def _draws(stream: RandomSource, count: int = DRAWS) -> tuple[int, ...]:
    return tuple(stream.randbelow(1000) for _ in range(count))


def test_one_seats_extra_draws_do_not_move_the_other_seats():
    """座位 2 在自己的流上多取 97 次，其余座位的后续抽签值不变。"""
    streams = {seat: ai_stream(SEED, seat) for seat in range(5)}
    for seat in range(5):
        _draws(streams[seat], 3)

    # 模拟"座位 2 这局多问了一次"：在**它自己那条流**上多取 97 个数。共用一条 ai
    # 队列时，这会平移其余四座的抽签位置（票 03 实测中位数 195 条应答被改写）。
    for _ in range(97):
        streams[2].randbelow(1000)

    later = {seat: _draws(streams[seat], 5) for seat in (0, 1, 3, 4)}
    for seat in (0, 1, 3, 4):
        reference = ai_stream(SEED, seat)
        _draws(reference, 3)
        assert later[seat] == _draws(reference, 5), f"座位 {seat} 被座位 2 的取数改写了"


def test_seat_streams_are_not_the_old_shared_ai_stream():
    """旧共用 ``ai`` 流与任何座位的 ``ai:<seat>`` 都不是同一条。"""
    legacy = _draws(RandomSource(SEED).substream("ai"))
    assert all(_draws(ai_stream(SEED, seat)) != legacy for seat in range(8))


def test_each_seat_has_its_own_stream():
    values = {_draws(ai_stream(SEED, seat), 4) for seat in range(8)}
    assert len(values) == 8, "八个座位必须拿到八条不同的子流"


def test_seat_must_be_a_non_negative_integer():
    for bad in (-1, True, 1.5, "0"):
        with pytest.raises(ValueError):
            ai_stream(SEED, bad)  # type: ignore[arg-type]


LEGACY_SHARED_STREAM = re.compile(r"substream\(\s*f?['\"]ai(?::[^'\"]*)?['\"]")
"""手工建 ``ai`` 共用流（或绕过 helper 拼 ``ai:<seat>``）的字面量形状。

这是**绊线**不是证明：它只抓字面量，抓不到 ``name = "ai"`` 再 ``substream(name)`` 的
数据流；行为面由上面的座位隔离 / 新旧不相等与下面的运行时调用点用例兜。
"""

ALLOWED_LEGACY_SITES = {
    "src/sgs/engine/rng.py",  # helper 自己的实现
    "tests/test_ai_streams.py",  # 本模块要拿旧流作"不相等"对照
    "tests/replays/scenarios.py",  # 冻结磁带的录制驱动：重录是独立显式动作（票 12 DoD 不重录）
    "tests/test_rng.py",  # substream 契约测试用原始用途名测"派生不消耗根"（不是建座点）
}


def legacy_ai_stream_sites(root: Path) -> list[str]:
    """扫描 ``src/`` / ``tools/`` / ``tests/`` 下所有手工建 ai 流的行。"""
    hits: list[str] = []
    for top in ("src", "tools", "tests"):
        for path in sorted((root / top).rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            if relative in ALLOWED_LEGACY_SITES:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if LEGACY_SHARED_STREAM.search(line):
                    hits.append(f"{relative}:{number}")
    return hits


def test_no_code_builds_the_ai_stream_by_hand():
    assert legacy_ai_stream_sites(ROOT) == [], (
        "建座位的随机流必须走 sgs.engine.ai_stream（唯一格式串），"
        "不许各抄一份 substream(\"ai\")"
    )


def test_the_scan_flags_a_synthetic_shared_stream(tmp_path: Path):
    """合成反例：扫描器必须能抓到一棵"有人手写共用流"的树。"""
    bad = tmp_path / "src/sgs/example.py"
    bad.parent.mkdir(parents=True)
    bad.write_text('stream = RandomSource(1).substream("ai")\n', encoding="utf-8")

    assert legacy_ai_stream_sites(tmp_path) == ["src/sgs/example.py:1"]


PRODUCTION_SEAT_BUILDERS = (
    "src/sgs/cli.py",
    "tools/sgs_web/session.py",
    "tools/sgs_batch/runner.py",
)


def _ai_stream_calls(source: str) -> list[ast.Call]:
    """源码里真正的 ``ai_stream(...)`` 调用表达式（注释 / 字符串不算）。"""
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "ai_stream"
    ]


def test_every_production_seat_builder_calls_the_helper():
    for relative in PRODUCTION_SEAT_BUILDERS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert _ai_stream_calls(source), f"{relative} 没有真正的 ai_stream(...) 调用"


def _tools_on_path() -> None:
    tools = str(ROOT / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)


def _spy_stream(monkeypatch, module, calls: list[tuple[int, int]]):
    """把 ``module.ai_stream`` 换成记录调用的包装（生产线仍走真 helper）。"""
    original = module.ai_stream

    def spy(seed: int, seat: int):
        calls.append((seed, seat))
        return original(seed, seat)

    monkeypatch.setattr(module, "ai_stream", spy)


def test_the_cli_builds_one_stream_per_seat(monkeypatch, capsys):
    import sgs.cli as cli

    calls: list[tuple[int, int]] = []
    _spy_stream(monkeypatch, cli, calls)

    assert cli.main(["--seed", "7", "--players", "2", "--ai", "random"]) == 0
    capsys.readouterr()
    assert calls == [(7, 0), (7, 1)]


def test_the_batch_builder_builds_one_stream_per_seat(monkeypatch):
    _tools_on_path()
    from sgs.data import Mode

    import sgs_batch.runner as runner

    calls: list[tuple[int, int]] = []
    _spy_stream(monkeypatch, runner, calls)

    batch = runner.run_games(
        seeds=(7,),
        mode=Mode.NO_IDENTITY,
        players=2,
        seat_policies=("random", "random"),
    )
    assert batch.summary["errors"] == 0
    assert calls == [(7, 0), (7, 1)]


def test_the_web_session_builds_one_stream_per_ai_seat(monkeypatch):
    _tools_on_path()
    import sgs_web.session as web_session

    calls: list[tuple[int, int]] = []
    _spy_stream(monkeypatch, web_session, calls)

    web_session.Session(
        seed=11,
        player_count=2,
        ai="random",
        human_seat=0,
        timeout_seconds=None,
    )
    assert calls == [(11, 1)], "人类座位不建 AI 流"
