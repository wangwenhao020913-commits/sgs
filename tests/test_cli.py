"""命令行门面：两个入口同源，``--seed`` 被解析。"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

import sgs.__main__ as module_entry
from sgs.cli import main, render

CONSOLE_SCRIPT = Path(sys.executable).parent / "sgs"


def test_console_script_points_at_the_same_entry_function():
    scripts = {point.name: point.value for point in entry_points(group="console_scripts")}

    assert scripts["sgs"] == "sgs.cli:main"


def test_module_entry_is_the_same_function():
    assert module_entry.main is main


def test_seed_is_parsed():
    assert main(["--seed", "7"]) == 0


def test_seed_may_be_omitted():
    assert main([]) == 0


def test_players_defaults_to_two(capsys):
    assert main(["--seed", "7"]) == 0

    header = capsys.readouterr().out.splitlines()[1]
    assert "'player_count': 2" in header, "省略 --players 时必须仍是 2 人局"


@pytest.mark.parametrize("count", [2, 4, 5, 6, 7, 8])
def test_each_supported_seat_count_runs_a_whole_game(count, capsys):
    """票 23：命令行接受 2/4/5/6/7/8，且每档都真的跑到 `game_end`。"""
    assert main(["--seed", "7", "--players", str(count)]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert f"'player_count': {count}" in lines[1]
    assert "终局" in lines[-1], f"{count} 人局必须跑到终局"


@pytest.mark.parametrize("count", ["0", "1", "3", "9"])
def test_an_unsupported_seat_count_reports_to_stderr_and_exits_1(count, capsys):
    assert main(["--players", count]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "人数非法" in captured.err
    assert "player_count" in captured.err


def test_the_renderer_labels_identity_deals_in_chinese():
    """票 24：身份发放也是 ``delta``，渲染必须走中文标签而不是误落到"洗牌"分支。"""
    from sgs.data import Identity
    from sgs.engine import IdentityDealRecord

    line = render(
        IdentityDealRecord(seq=2, frame_id=0, depth=0, seat=1, identity=Identity.LORD)
    )

    assert "身份" in line and "座位=1" in line and "主公" in line


def test_the_renderer_labels_identity_reveals_in_chinese():
    """票 25：阵亡亮明也是 ``delta``，渲染同样不能误落到"洗牌"分支。"""
    from sgs.data import Identity
    from sgs.engine import IdentityRevealRecord

    line = render(
        IdentityRevealRecord(seq=3, frame_id=9, depth=6, seat=2, identity=Identity.REBEL)
    )

    assert "亮明身份" in line and "座位=2" in line and "反贼" in line


def test_unknown_option_is_rejected():
    with pytest.raises(SystemExit):
        main(["--nope"])


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def test_module_entry_runs():
    result = _run(sys.executable, "-m", "sgs", "--seed", "7")

    assert result.returncode == 0, result.stderr


def test_console_script_runs():
    result = _run(str(CONSOLE_SCRIPT), "--seed", "7")

    assert result.returncode == 0, result.stderr


BAD_RAW_PACK = {"schema_version": 99}
"""坏数据基底：未知数据模型版本；由真实的加载器生成报告，避免手抄错误文案。"""

BAD_PACK_PROBE = f"""
import sgs.cli as cli
from sgs.data import validate_content_pack

def load_bad_pack(pack_id="standard", *, deck_profile=None):
    validate_content_pack({BAD_RAW_PACK!r}, file="files/standard.json")

cli.load_content_pack = load_bad_pack
raise SystemExit(cli.main([]))
"""


def test_bad_content_pack_reports_to_stderr_and_exits_1(monkeypatch, capsys):
    """坏数据：命令行打印中文报告到 stderr、退出码 1（票 03 §6）。"""
    import sgs.cli as cli
    from sgs.data import validate_content_pack

    def load_bad_pack(pack_id="standard", *, deck_profile=None):
        validate_content_pack(BAD_RAW_PACK, file="files/standard.json")

    monkeypatch.setattr(cli, "load_content_pack", load_bad_pack)

    assert cli.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "内容包加载失败" in captured.err
    assert "version.unknown" in captured.err
    assert "99" in captured.err


def test_bad_content_pack_exit_code_in_subprocess():
    """跨进程确认退出码 1 与 stderr 报告（入口同源由 smoke 断言）。"""
    result = _run(sys.executable, "-c", BAD_PACK_PROBE)

    assert result.returncode == 1
    assert "内容包加载失败" in result.stderr
    assert "version.unknown" in result.stderr
    assert result.stdout == ""


def test_cli_runs_a_whole_game_and_prints_the_seed_then_the_stream(capsys):
    assert main(["--seed", "7"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "seed = 7"
    assert lines[1].startswith("header 种子=7 ")
    assert lines[-1].startswith("#")
    # 票 22 已让死亡结算与胜负判定落地：随机驱动的一局真的走到 `game_end`，
    # 末行是终局记录（不是停在死亡入口）。
    stream = "\n".join(lines)
    assert "NEAR_DEATH" in stream, "随机驱动的一局应当真的走到过濒死"
    assert "终局" in lines[-1], "命令行必须跑到真实胜负"
    assert "结果=winner" in lines[-1]


def test_module_entry_and_console_script_agree_byte_for_byte():
    module = _run(sys.executable, "-m", "sgs", "--seed", "7")
    script = _run(str(CONSOLE_SCRIPT), "--seed", "7")

    assert module.returncode == 0, module.stderr
    assert script.returncode == 0, script.stderr
    assert module.stdout == script.stdout


def test_same_seed_twice_is_byte_identical_across_processes():
    first = _run(sys.executable, "-m", "sgs", "--seed", "20261008")
    second = _run(sys.executable, "-m", "sgs", "--seed", "20261008")

    assert first.stdout == second.stdout
    assert first.stdout != ""


def _printed_seed(stream: str) -> int:
    """命令行首行 ``seed = N`` 里的种子（省略 ``--seed`` 时由 CLI 现生成并打印）。"""
    return int(stream.splitlines()[0].removeprefix("seed = "))


def test_omitted_seed_is_generated_recorded_and_printed(capsys):
    assert main([]) == 0

    stream = capsys.readouterr().out
    lines = stream.splitlines()
    seed = _printed_seed(stream)
    assert 0 <= seed < (1 << 64)
    assert lines[1].startswith(f"header 种子={seed} ")


def test_a_generated_seed_replays_the_same_stream(capsys):
    """省略种子只是现生成一个种子；拿它重跑必须得到同一份记录流（票 08 / 票 27）。"""
    assert main([]) == 0
    generated = capsys.readouterr().out
    seed = _printed_seed(generated)

    assert main(["--seed", str(seed)]) == 0
    replayed = capsys.readouterr().out

    assert replayed == generated


def test_seed_outside_the_64_bit_range_is_rejected(capsys):
    assert main(["--seed=-1"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "种子必须落在" in captured.err


def test_unsupported_player_count_exit_code_in_subprocess():
    """票 23：非法人数跨进程也是退出码 1 + stderr 报告（3 人仍加载失败）。"""
    result = _run(sys.executable, "-m", "sgs", "--players", "3")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "人数非法" in result.stderr
    assert "player_count" in result.stderr


# ---------------------------------------------------------------------------
# 武将发放：``--heroes`` 显式指定 / ``--hero-pool`` 换启用池（票 02）
# ---------------------------------------------------------------------------


def test_explicit_heroes_run_a_whole_game(capsys):
    assert main(["--seed", "7", "--players", "2", "--heroes", "caocao,guanyu"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "终局" in lines[-1]


def test_heroes_are_parsed_as_a_comma_list():
    """空项与空白被剔除，不把 ``" caocao"`` 当成另一个武将。"""
    import sgs.cli as cli

    assert cli.main(["--seed", "7", "--heroes", " caocao , guanyu "]) == 0


def test_an_unknown_explicit_hero_reports_to_stderr_and_exits_1(capsys):
    assert main(["--seed", "7", "--players", "2", "--heroes", "caocao,nobody"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "武将发放非法" in captured.err
    assert "nobody" in captured.err


def test_an_empty_pool_flag_uses_the_data_file_enabled_pool(capsys):
    """``--hero-pool ""``（与全空白项的写法）都解析成空元组 = 用数据文件的启用池。

    票 12 起该池有 16 名，故 2 人局发得出牌；"池不够"的 fail-closed 由引擎侧
    （``test_hero_dealing.py`` 的"池比座位少"）钉住。
    """
    assert main(["--seed", "7", "--players", "2", "--hero-pool", ""]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines()[-1].startswith("#")

    assert main(["--seed", "7", "--players", "2", "--hero-pool", ","]) == 0
    assert capsys.readouterr().err == ""


def test_an_explicit_pool_switches_the_dealt_heroes(capsys):
    """换池：2 人局从 ``caocao,guanyu`` 里不重复抽两名。"""
    assert main(["--seed", "7", "--players", "2", "--hero-pool", "caocao,guanyu"]) == 0

    assert capsys.readouterr().out.splitlines()[-1].startswith("#")


def test_omitting_the_hero_flags_stays_in_no_hero_mode(capsys):
    """缺省仍是**无武将模式**（M2 的 34 条基准靠它保持有效）。"""
    assert main(["--seed", "7", "--players", "2"]) == 0

    stream = capsys.readouterr().out
    assert "'heroes'" not in stream and "hero=" not in stream


def test_hero_deal_failure_exit_code_in_subprocess():
    result = _run(
        sys.executable, "-m", "sgs", "--players", "2", "--heroes", "caocao,nobody"
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "武将发放非法" in result.stderr


# ---------------------------------------------------------------------------
# 非法行动：stderr 报告 + 退出码 1（票 07 §5 / 票 24）
# ---------------------------------------------------------------------------

ILLEGAL_ACTION_PROBE = """
import sgs.cli as cli
from sgs.state import Pass

class IllegalPlayer:
    def __init__(self, rng):
        self._rng = rng

    def respond(self, query):
        return Pass(answers_seq=query.seq)

cli.RandomPlayer = IllegalPlayer
raise SystemExit(cli.main(["--seed", "7"]))
"""

MALFORMED_ACTION_PROBE = """
import sgs.cli as cli

class MalformedPlayer:
    def __init__(self, rng):
        self._rng = rng

    def respond(self, query):
        return {"kind": "Frobnicate", "answers_seq": query.seq}

cli.RandomPlayer = MalformedPlayer
raise SystemExit(cli.main(["--seed", "7"]))
"""


def _illegal_player_class(response):
    """造一个总投喂固定（非法）应答的参与者类，替掉 CLI 默认的随机玩家。"""

    class _IllegalPlayer:
        def __init__(self, rng):
            self._rng = rng

        def respond(self, query):
            return response(query)

    return _IllegalPlayer


def test_an_illegal_action_reports_to_stderr_and_exits_1(monkeypatch, capsys):
    import sgs.cli as cli
    from sgs.state import Pass

    monkeypatch.setattr(
        cli, "RandomPlayer", _illegal_player_class(lambda q: Pass(answers_seq=q.seq))
    )

    assert cli.main(["--seed", "7"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "非法行动" in captured.err
    assert "IllegalAction" in captured.err


def test_a_malformed_action_reports_to_stderr_and_exits_1(monkeypatch, capsys):
    import sgs.cli as cli

    monkeypatch.setattr(
        cli,
        "RandomPlayer",
        _illegal_player_class(lambda q: {"kind": "Frobnicate", "answers_seq": q.seq}),
    )

    assert cli.main(["--seed", "7"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "非法行动" in captured.err
    assert "MalformedAction" in captured.err


def test_illegal_action_exit_code_in_subprocess():
    result = _run(sys.executable, "-c", ILLEGAL_ACTION_PROBE)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "非法行动" in result.stderr
    assert "IllegalAction" in result.stderr


def test_malformed_action_exit_code_in_subprocess():
    result = _run(sys.executable, "-c", MALFORMED_ACTION_PROBE)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "非法行动" in result.stderr
    assert "MalformedAction" in result.stderr


def test_the_ai_tier_defaults_to_random(capsys, monkeypatch):
    """票 13 §档位：缺省仍是随机——默认参与者不动，M2 的 34 条基准一行不改。"""
    import sgs.cli as cli

    seen: list[str] = []

    def factory(name, stream):
        seen.append(name)
        return cli.RandomPlayer(stream)

    monkeypatch.setattr(cli, "_player", factory)

    assert cli.main(["--seed", "7"]) == 0
    assert seen == ["random", "random"]


def test_the_heuristic_tier_runs_a_whole_game(capsys):
    assert main(["--seed", "7", "--ai", "heuristic"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "seed = 7"
    assert "终局" in lines[-1], "启发式档位必须跑到真实胜负"


def test_the_heuristic_tier_runs_with_heroes(capsys):
    assert main(["--seed", "7", "--players", "5", "--hero-pool", "", "--ai", "heuristic"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "'player_count': 5" in lines[1]
    assert "终局" in lines[-1]


def test_an_unknown_ai_tier_is_rejected():
    with pytest.raises(SystemExit):
        main(["--ai", "oracle"])


# ---------------------------------------------------------------------------
# 牌堆档位：``--deck-profile``（M5-01b）
# ---------------------------------------------------------------------------


def test_the_deck_profile_flag_builds_a_160_card_game(capsys):
    """票 01b：命令行显式选 160 档，header 记新档位且整局跑到终局。"""
    assert main(["--seed", "7", "--players", "2", "--deck-profile", "standard_ex_160"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "牌堆档位=standard_ex_160" in lines[1]
    assert "终局" in lines[-1], "160 张档也要真的跑到终局"


def test_omitting_the_deck_profile_keeps_the_standard_108_game(capsys):
    """不传档位 = 内容包默认档（standard_108）：默认对局一个字节都不变。"""
    assert main(["--seed", "7", "--players", "2"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "牌堆档位=standard_108" in lines[1]
    assert "终局" in lines[-1]


def test_an_unknown_deck_profile_reports_the_loader_error_and_exits_1(capsys):
    """非法档位照加载器的 ``decks.unknown_profile`` 报错，退出码非零。"""
    assert main(["--seed", "7", "--deck-profile", "standard_999"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "decks.unknown_profile" in captured.err
    assert "standard_999" in captured.err


def test_the_renderer_labels_hp_max_changes_in_chinese():
    """体力上限变化（觉醒技的"减 1 点上限"）也要有自己的转写，不落进"洗牌"兜底。"""
    from sgs.engine import HpMaxChangeRecord

    line = render(
        HpMaxChangeRecord(seq=4, frame_id=0, depth=0, seat=2, before=4, after=3, amount=-1)
    )

    assert "体力上限" in line and "座位=2" in line and "4→3" in line


def test_the_renderer_never_crashes_on_a_delta_it_does_not_transcribe():
    """未专门转写的 delta 只报类型：**不许**读别的 delta 才有的字段（旧兜底会 KeyError/AttributeError）。"""
    from sgs.engine import TurnChangeRecord

    line = render(
        TurnChangeRecord(seq=5, frame_id=0, depth=0, seat=1, round=2, extra=False)
    )

    assert "turn_change" in line
