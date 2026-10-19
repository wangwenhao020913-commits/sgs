"""验收种子扫描工具的行为测试（W5-03 retro）。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import new_game
from sgs.state import SlotKind

ROOT = Path(__file__).resolve().parent.parent
PACK = load_content_pack(deck_profile="standard_108")


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "acceptance_seed", ROOT / "tools" / "acceptance_seed.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _first_human_card() -> str:
    state = new_game(
        seed=0,
        rule_config=RuleConfig(player_count=2),
        pack=PACK,
        hero_pool=(),
    )
    return state.cards[state.players[0].hand[0]].type


def test_find_seeds_matches_a_card_the_human_actually_has() -> None:
    tool = _load_tool()
    card = _first_human_card()
    matches = tool.find_seeds(players=2, human_seat=0, card=card, limit=1, show=1)
    assert len(matches) == 1
    assert matches[0].seed == 0
    assert card in matches[0].hand


def test_find_seeds_filters_on_the_other_seats_hand() -> None:
    tool = _load_tool()
    card = _first_human_card()
    matches = tool.find_seeds(
        players=4,
        human_seat=0,
        card=card,
        other_slot=SlotKind.WEAPON,
        limit=200,
        show=2,
    )
    assert matches, "200 个 4 人种子里应至少有一个其他座位手牌带武器"
    for match in matches:
        assert match.others, "带 other_slot 的命中必须记录满足条件的其他座位"


def test_find_seeds_filters_on_another_card() -> None:
    tool = _load_tool()
    matches = tool.find_seeds(
        players=4,
        human_seat=0,
        card="slash",
        other_card="slash",
        limit=200,
        show=2,
    )
    assert matches, "200 个 4 人种子里应至少有一个其他座位也有【杀】"
    for match in matches:
        assert match.others, "带 other_card 的命中必须记录满足条件的其他座位"


def test_cli_smoke_prints_a_match_for_the_first_seed() -> None:
    card = _first_human_card()
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "acceptance_seed.py"),
            "--players",
            "2",
            "--card",
            card,
            "--limit",
            "1",
            "--show",
            "1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[0].startswith("seed")
    assert lines[1].startswith("0\t"), lines[1]
