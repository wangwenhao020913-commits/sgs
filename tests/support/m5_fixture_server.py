"""M5-09a 的**固定 tape** 服务：从构造局面起跑的真 HTTP 会话（spec D12 / §6 A2）。

产品服务（``tools/sgs_web/serve.py``）只从**种子**开局——构造局面是测试侧的能力，故这里
复用同一份 HTTP 门面（``make_server`` 的注册表与处理器），只把 ``GameRegistry.create``
换成一个**按名字取构造局面**的注册表：

- ``fire_attack``：人类座位手里有【火攻】+ 三种花色的牌 +【杀】。人类**对自己**用火攻：
  亮牌与同花色弃牌两步都由**人类**经 HTTP 回答（各一次真实 POST），随后【杀】收局；
  对手有 2 张暗手（只用于"未亮出的手牌不外泄"的负断言）；
- ``reforge``：人类手里有【铁索连环】+【杀】 ⇒ 一次 ``Reforge`` 真实往返 + 一次【杀】收局；
- ``wine``：人类手里有【酒】+【杀】 ⇒ 一次【酒】（出牌阶段）真实往返 + 一次【杀】收局。

三条 tape **都不依赖随机种子**：手牌 / 体力 / 牌堆都是写死的 ``card_id``，种子只喂给
AI 子流。每次询问都走 ``POST /api/sessions/<id>/answer``，与产品服务同一条路径。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_DIR.parent
for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from sgs.data import ContentPack, RuleConfig, load_content_pack  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.state import GameState  # noqa: E402
from sgs_web.serve import GameRegistry, make_server  # noqa: E402
from sgs_web.session import create_session  # noqa: E402

from support.positions import SeatSpec, build_state, cards_of_type  # noqa: E402

__all__ = ["FIXTURES", "FIXTURE_NAMES", "Fixture", "build_fixture", "main"]

TAPE_PROFILE = "standard_ex_160"
"""三条固定 tape 都在军争 160 档（A2 的硬条件）。"""

FIXTURE_SEED = 4909
"""AI 子流的固定种子；构造局面与它无关（只影响 AI 的选牌，比如火攻亮哪一张）。"""


class Fixture:
    """一条构造局面：2 人局、人类座位 0、首回合出牌阶段。"""

    def __init__(self, name: str, state: GameState, *, note: str) -> None:
        self.name = name
        self.state = state
        self.note = note

    @property
    def player_count(self) -> int:
        return len(self.state.players)


def _deck(pack: ContentPack, used: set[int]) -> tuple[int, ...]:
    return tuple(index for index in range(len(pack.deck.cards)) if index not in used)


def _first_of_suit(pack: ContentPack, suit, *, exclude: set[int]) -> int:
    for index, card in enumerate(pack.deck.cards):
        if card.suit is suit and index not in exclude:
            return index
    raise AssertionError(f"standard_ex_160 里没有 {suit} 的牌")


def _fire_attack_fixture(pack: ContentPack) -> Fixture:
    """人类【火攻】**自己**：自己亮 1 张 + 弃同花色 ⇒ 1 点火焰伤害；随后【杀】收局。

    亮牌与弃牌两步都由**人类**应答（A2 要的是真人经 HTTP 的亮牌往返，不是 AI 亮牌）。
    对手的 2 张暗手只服务"未亮出的手牌不外泄"的负断言：人类作为亮牌者时，自己的手牌
    本来就是本人可见，故负断言盯的是**对手**的暗手。
    """
    from sgs.data import Suit

    fire = cards_of_type(pack, "fire_attack")[0]
    slash = cards_of_type(pack, "slash")[0]
    used = {fire, slash}
    # 人类手里三种花色各一张：亮出哪一种花色，弃牌步都有同花色候选；【杀】放最后，
    # 弃牌步只碰得到亮出的那张花色牌（不误弃收局用的【杀】）。
    hand0 = [fire]
    for suit in (Suit.SPADE, Suit.CLUB, Suit.HEART):
        pick = _first_of_suit(pack, suit, exclude=used)
        used.add(pick)
        hand0.append(pick)
    hand0.append(slash)
    # 对手的暗手：两张非【闪】、非【桃】（不会响应【杀】、也不会自救），只作负断言。
    hand1: list[int] = []
    for type_id in ("duel", "barbarian_invasion"):
        pick = next(
            index for index in cards_of_type(pack, type_id) if index not in used
        )
        used.add(pick)
        hand1.append(pick)
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, hand=tuple(hand0)),
            SeatSpec(hp=1, hp_max=4, hand=tuple(hand1)),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "fire_attack",
        state,
        note="人类火攻自己（真人亮牌 + 同花色弃牌，各一次 HTTP 往返）→ 1 点火焰伤害；【杀】收局",
    )


def _reforge_fixture(pack: ContentPack) -> Fixture:
    iron = cards_of_type(pack, "iron_chain")[0]
    slash = cards_of_type(pack, "slash")[0]
    used = {iron, slash}
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, hand=(iron, slash)),
            SeatSpec(hp=1, hp_max=4),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "reforge",
        state,
        note="铁索重铸（独立 Reforge 动作，弃一张摸一张）+【杀】收局",
    )


def _wine_fixture(pack: ContentPack) -> Fixture:
    wine = cards_of_type(pack, "wine")[0]
    slash = cards_of_type(pack, "slash")[0]
    used = {wine, slash}
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, hand=(wine, slash)),
            SeatSpec(hp=1, hp_max=4),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "wine",
        state,
        note="酒（出牌阶段，目标为自己）+【杀】收局",
    )


FIXTURES: dict[str, Callable[[ContentPack], Fixture]] = {
    "fire_attack": _fire_attack_fixture,
    "reforge": _reforge_fixture,
    "wine": _wine_fixture,
}
FIXTURE_NAMES = tuple(FIXTURES)


def build_fixture(name: str, *, pack: ContentPack | None = None) -> Fixture:
    if name not in FIXTURES:
        raise KeyError(f"未知的固定 tape 局面：{name!r}（可选 {FIXTURE_NAMES}）")
    return FIXTURES[name](pack if pack is not None else load_content_pack(deck_profile=TAPE_PROFILE))


class FixtureRegistry(GameRegistry):
    """每建一局都从构造局面起跑；其余（取增量 / 应答 / 落盘）与产品注册表同一份。"""

    def __init__(self, runs_dir: Path | None = None) -> None:
        super().__init__(runs_dir=runs_dir)

    def create(self, body: dict):  # noqa: ANN001, ANN201 - 与 GameRegistry.create 同签名
        name = str(body.get("fixture") or "")
        if name not in FIXTURES:
            raise ValueError(f"缺少固定的 fixture 名（可选 {FIXTURE_NAMES}）：{name!r}")
        pack = load_content_pack(deck_profile=TAPE_PROFILE)
        fixture = build_fixture(name, pack=pack)
        self._counter += 1
        session = create_session(
            seed=FIXTURE_SEED,
            player_count=fixture.player_count,
            ai="heuristic",
            human_seat=0,
            hero_pool=None,
            timeout_seconds=None,
            pack=pack,
            session_id=f"s{self._counter}",
        )
        session.engine = Engine.from_state(fixture.state, seed=FIXTURE_SEED)
        session.drive()
        session.mark_pending(time.monotonic())
        self.sessions[session.session_id] = session
        return session


def make_fixture_server(port: int = 0, runs_dir: Path | None = None, host: str = "127.0.0.1"):
    server = make_server(port, runs_dir, host=host)
    registry = FixtureRegistry(runs_dir=runs_dir)
    server.registry = registry  # type: ignore[attr-defined]
    server.RequestHandlerClass.registry = registry  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M5 固定 tape 的构造局面 HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--runs-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    server = make_fixture_server(args.port, args.runs_dir, host=args.host)
    host, port = server.server_address[:2]
    print(f"sgs-m5-fixtures listening on http://{host}:{port}/", flush=True)
    print(f"fixtures: {', '.join(FIXTURE_NAMES)}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
