"""回放与基准：日志反序列化、版本闸门、行动序列投影与全量逐行比对（票 10 / 票 15 / 票 19）。

落点 = 出料边界（``sgs.engine.read_journal`` / ``sgs.agents.tape_from_journal``）
与主缝（``Engine.new_game`` + ``TapePlayer`` + ``dump_journal``）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from replays import scenarios
from sgs.agents import TapeError, TapePlayer, tape_from_journal
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import (
    EVENT_SCHEMA_VERSION,
    Engine,
    JournalVersionError,
    dump_journal,
    read_journal,
)

from support import play_standard

PACK = load_content_pack()
REPLAYS_DIR = Path(__file__).resolve().parent / "replays"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _journal_with_version(version: str) -> str:
    """拿一条真实记录流，只替换首行的版本值（保持 canonical 形状）。"""
    text = play_standard(seed=7).journal()
    line, rest = text.split("\n", 1)
    payload = json.loads(line)
    payload["event_schema_version"] = json.loads(version)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" + rest


# ---------------------------------------------------------------------------
# 日志反序列化 = 唯一入口（票 15 §2）
# ---------------------------------------------------------------------------


def test_read_journal_is_the_symmetric_deserialiser_of_dump_journal(tmp_path):
    text = play_standard(seed=7).journal()
    path = _write(tmp_path / "game.jsonl", text)

    records = read_journal(path)

    assert records[0].kind == "header"
    assert records[0].event_schema_version == EVENT_SCHEMA_VERSION
    assert dump_journal(records) == text, "唯一序列化器与唯一反序列化器互为逆"


def test_read_journal_rejects_an_unknown_event_schema_version_and_reports_both(tmp_path):
    path = _write(tmp_path / "future.jsonl", _journal_with_version("99"))

    with pytest.raises(JournalVersionError) as caught:
        read_journal(path)

    assert caught.value.file_version == 99
    assert caught.value.supported_versions == (EVENT_SCHEMA_VERSION,)
    message = str(caught.value)
    assert "99" in message and str(EVENT_SCHEMA_VERSION) in message, "拒绝时必须报双方版本"
    assert str(path) in message


def test_read_journal_treats_a_missing_version_as_a_structural_error(tmp_path):
    line, rest = play_standard(seed=7).journal().split("\n", 1)
    payload = json.loads(line)
    del payload["event_schema_version"]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" + rest
    path = _write(tmp_path / "no-version.jsonl", text)

    with pytest.raises(Exception) as caught:
        read_journal(path)

    assert not isinstance(caught.value, JournalVersionError), "缺版本号是结构错误，不是版本不符"


def test_read_journal_treats_a_non_integer_version_as_a_structural_error(tmp_path):
    path = _write(tmp_path / "string-version.jsonl", _journal_with_version('"1"'))

    with pytest.raises(Exception) as caught:
        read_journal(path)

    assert not isinstance(caught.value, JournalVersionError), "非整数版本号是结构错误，不是版本不符"


# ---------------------------------------------------------------------------
# tape 投影与回放执行者（票 10 §7 / 票 05 §6）
# ---------------------------------------------------------------------------


def test_tape_from_journal_returns_the_seed_and_the_actions_in_line_order(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())

    seed, tape = tape_from_journal(read_journal(path))

    assert seed == 7
    assert tape == tuple(
        record.action for record in engine.records if record.kind == "action"
    )
    assert tape, "随机玩家的一局里必然有应答"


def test_replaying_the_tape_reproduces_the_journal_byte_for_byte(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)
    player = TapePlayer(tape)
    replayed.run([player, player])

    assert dump_journal(replayed.records) == engine.journal()


def test_tape_player_reports_a_divergence_when_the_tape_runs_out(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)

    with pytest.raises(TapeError):
        replayed.run([TapePlayer(tape[:-1])] * 2)


def test_tape_player_reports_a_stale_answers_seq(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))
    stale = (tape[0].model_copy(update={"answers_seq": tape[0].answers_seq + 1}), *tape[1:])

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)

    with pytest.raises(TapeError):
        replayed.run([TapePlayer(stale)] * 2)


# ---------------------------------------------------------------------------
# Golden Replay：录制入口、全量逐行比对与索引互等（票 10 / 票 19）
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """录制模式遍历场景清单；验证模式遍历基准文件（票 10 §6）。"""
    if "slug" not in metafunc.fixturenames:
        return
    if metafunc.config.getoption("--update-golden"):
        slugs = list(scenarios.SCENARIOS)
    else:
        slugs = [path.stem for path in _golden_files()]
    metafunc.parametrize("slug", sorted(slugs))


def _golden_files() -> list[Path]:
    return sorted(REPLAYS_DIR.glob("*.jsonl"))


_README_ROW = re.compile(r"^\|\s*`([0-9]{2}-[a-z0-9-]+)`\s*\|")


def _readme_slugs() -> set[str]:
    text = (REPLAYS_DIR / "README.md").read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in (_README_ROW.match(line) for line in text.splitlines())
        if match is not None
    }


def test_golden_replay_matches_byte_for_byte(slug: str, tmp_path: Path, update_golden: bool) -> None:
    """**真的重跑引擎**再逐行比对；禁止把文件与它自身比较（票 10 §1）。"""
    path = REPLAYS_DIR / f"{slug}.jsonl"
    if update_golden:
        _write(path, scenarios.record(slug))

    expected = path.read_text(encoding="utf-8")
    records = read_journal(path)
    header = records[0]
    assert (header.pack_id, header.deck_profile) == (PACK.pack_id, PACK.deck_profile), (
        "回放以 header 为准重开一局；header 声明的 pack / deck 必须能被当前数据加载"
    )
    seed, tape = tape_from_journal(records)
    engine = Engine.new_game(seed=seed, rule_config=header.rule_config, pack=PACK)
    player = TapePlayer(tape)
    engine.run([player, player])

    actual = dump_journal(engine.records)
    if actual != expected:
        artifact = tmp_path / f"{slug}.actual.jsonl"
        artifact.write_text(actual, encoding="utf-8")
        raise AssertionError(f"{_first_divergence(expected, actual)}\n本次实际记录流：{artifact}")


def test_readme_index_matches_the_golden_file_set() -> None:
    assert _readme_slugs() == {path.stem for path in _golden_files()}, (
        "索引表与基准文件集合互相等于（防孤儿基准 / 孤儿行）"
    )


def test_every_declared_scenario_is_recorded() -> None:
    assert set(scenarios.SCENARIOS) == {path.stem for path in _golden_files()}


def test_the_divergence_report_names_the_first_line_seq_kind_and_field() -> None:
    expected = (
        '{"cards":[],"delta_type":"card_move","from_zone":"hand","kind":"delta",'
        '"owner":0,"reason":"USE","seq":7,"to_zone":"deck"}\n'
    )
    actual = (
        '{"cards":[],"delta_type":"card_move","from_zone":"hand","kind":"delta",'
        '"owner":0,"reason":"USE","seq":7,"to_zone":"discard"}\n'
    )

    report = _first_divergence(expected, actual)

    assert "第 1 行" in report
    assert "seq=7" in report and "kind=delta" in report
    assert "to_zone" in report and "deck" in report and "discard" in report


def test_the_divergence_report_flags_a_length_mismatch() -> None:
    longer = _first_divergence('{"kind":"timing"}\n', '{"kind":"timing"}\n{"kind":"timing"}\n')
    shorter = _first_divergence('{"kind":"timing"}\n{"kind":"timing"}\n', '{"kind":"timing"}\n')

    assert "第 2 行" in longer and "多出" in longer
    assert "第 2 行" in shorter and "缺失" in shorter


def _first_divergence(expected: str, actual: str) -> str:
    """报首个分歧的行号 + ``seq`` / ``kind`` + 首个不同字段路径与两侧取值（票 10 §2）。"""
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for index in range(min(len(expected_lines), len(actual_lines))):
        if expected_lines[index] != actual_lines[index]:
            return _describe_line(index + 1, expected_lines[index], actual_lines[index])
    if len(expected_lines) != len(actual_lines):
        line_no = min(len(expected_lines), len(actual_lines)) + 1
        if len(actual_lines) > len(expected_lines):
            return f"首个分歧：第 {line_no} 行是首个多出的行（基准只有 {len(expected_lines)} 行）"
        return f"首个分歧：第 {line_no} 行是首个缺失的行（基准有 {len(expected_lines)} 行）"
    return "两个记录流逐行相同"


def _describe_line(line_no: int, expected_line: str, actual_line: str) -> str:
    expected = json.loads(expected_line)
    actual = json.loads(actual_line)
    where = (
        f"seq={expected.get('seq', '（信封）')} kind={expected.get('kind', '（信封）')}"
    )
    diff = _first_field_diff(expected, actual, "")
    if diff is None:  # pragma: no cover - 字节不同但解析相同：只能是空白差异
        return f"首个分歧：第 {line_no} 行 {where}（逐行字节不同）"
    path, left, right = diff
    return (
        f"首个分歧：第 {line_no} 行 {where}；"
        f"字段 {path or '/'}：期望 {_truncate(left)}，实得 {_truncate(right)}"
    )


def _first_field_diff(left: object, right: object, path: str):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in [*left, *(key for key in right if key not in left)]:
            child = f"{path}.{key}" if path else key
            if key not in left:
                return (child, None, right[key])
            if key not in right:
                return (child, left[key], None)
            found = _first_field_diff(left[key], right[key], child)
            if found is not None:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        for index in range(min(len(left), len(right))):
            found = _first_field_diff(left[index], right[index], f"{path}[{index}]")
            if found is not None:
                return found
        if len(left) != len(right):
            return (f"{path}.length", len(left), len(right))
        return None
    if left != right:
        return (path, left, right)
    return None


def _truncate(value: object, limit: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…"
