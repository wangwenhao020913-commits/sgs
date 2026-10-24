"""M6 票 20 的**固定 tape** 服务：三条构造局面各覆盖一个 M6 新原语（真 HTTP）。

与 M5 的固定 tape 服务（``m5_fixture_server.py``）同一条姿态：复用产品 HTTP 门面，
只把 ``GameRegistry.create`` 换成"按名字取构造局面"的注册表。三条 tape 的手牌 / 体力 /
牌堆都是写死的 ``card_id``，不依赖随机种子（种子只喂 AI 子流）：

- ``awakening``：人类座位是**界徐庶**。人类用一张【杀】造成伤害 ⇒ 【潜心】**觉醒**
  （强制发动：上限 -1 + 获得 ☆【荐言】，没有"是否发动"询问），随后人类自己发动 ☆【荐言】
  （两次选项询问都由人类经 HTTP 应答）；
- ``extra_turn``：人类座位是**界吕蒙**。弃牌阶段弃出四花色 ⇒ 【博图】的"是否发动"询问由
  人类应答 ⇒ 引擎落 ``turn_change(extra=True)`` 并插入一个额外回合（回合环里同座位连着
  两个回合）；
- ``hero_zone``：人类座位是**界陆逊**。对手的【过河拆桥】唯一指向他 ⇒ 【谦逊】的
  "是否发动"由人类应答 ⇒ 全部手牌置于 ``Zone.HERO``（武将牌上的牌区），同一帧的
  【连营】选人询问也由人类应答；查看者的快照里能看到那个区。

每次询问都走 ``POST /api/sessions/<id>/answer``，与产品服务同一条路径。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = TESTS_DIR.parent
for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from sgs.data import ContentPack, Gender, RuleConfig, load_content_pack  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.state import GameState  # noqa: E402
from sgs_web.serve import GameRegistry, make_server  # noqa: E402
from sgs_web.session import create_session  # noqa: E402

from support.positions import SeatSpec, build_state  # noqa: E402
from support.m5_fixture_server import Fixture  # noqa: E402

__all__ = ["FIXTURES", "FIXTURE_NAMES", "build_fixture", "main"]

TAPE_PROFILE = "standard_108"
"""三条 M6 tape 都在标准 108 档：被覆盖的原语与军争新牌名无关。"""

FIXTURE_SEED = 62020
"""AI 子流的固定种子；构造局面与它无关（只影响 AI 的选牌）。"""


def _deck(pack: ContentPack, used: set[int]) -> tuple[int, ...]:
    return tuple(index for index in range(len(pack.deck.cards)) if index not in used)


def _card(pack: ContentPack, *, type_id: str | None = None, suit: str | None = None) -> int:
    for index, card in enumerate(pack.deck.cards):
        if type_id is not None and card.type != type_id:
            continue
        if suit is not None and card.suit.value != suit:
            continue
        return index
    raise AssertionError(f"standard_108 里没有可用牌：type={type_id!r} suit={suit!r}")


def _awakening_fixture(pack: ContentPack) -> Fixture:
    """界徐庶用【杀】造成伤害 ⇒ 【潜心】觉醒；随后人类发动 ☆【荐言】。"""
    slash = _card(pack, type_id="slash", suit="spade")
    filler = _card(pack, suit="club")
    used = {slash, filler}
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=3, hp_max=4, hero="jie_xushu", gender=Gender.MALE, hand=(slash,)),
            SeatSpec(hp=2, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=(filler,)),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "awakening",
        state,
        note="界徐庶造成伤害 ⇒ 【潜心】觉醒（上限 -1 + ☆【荐言】）；人类再发动【荐言】",
    )


def _extra_turn_fixture(pack: ContentPack) -> Fixture:
    """界吕蒙弃出四花色 ⇒ 【博图】额外回合（人类应答"是否发动"）。"""
    suits: list[int] = []
    for suit in ("spade", "heart", "club", "diamond"):
        pick = next(
            index
            for index, card in enumerate(pack.deck.cards)
            if card.suit.value == suit and index not in suits
        )
        suits.append(pick)
    slash = _card(pack, type_id="slash")
    used = {*suits, slash}
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(
                hp=1,
                hp_max=4,
                hero="jie_lvmeng",
                gender=Gender.MALE,
                hand=(*suits, slash),
            ),
            SeatSpec(hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "extra_turn",
        state,
        note="界吕蒙弃四花色 ⇒ 【博图】额外回合（人类应答）⇒ 额外回合里【杀】收局",
    )


def _hero_zone_fixture(pack: ContentPack) -> Fixture:
    """界陆逊被【过河拆桥】指定 ⇒ 【谦逊】把全部手牌放上武将牌（人类应答）。"""
    slash = _card(pack, type_id="slash")
    peach = _card(pack, type_id="peach")
    dismantle = _card(pack, type_id="dismantlement")
    used = {slash, peach, dismantle}
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(
                hp=3,
                hp_max=3,
                hero="jie_luxun",
                gender=Gender.MALE,
                hand=(slash, peach),
            ),
            SeatSpec(hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=(dismantle,)),
        ],
        deck=_deck(pack, used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    return Fixture(
        "hero_zone",
        state,
        note="对手【过河拆桥】唯一指定界陆逊 ⇒ 【谦逊】置牌上武将牌 + 【连营】选人（人类应答）",
    )


FIXTURES = {
    "awakening": _awakening_fixture,
    "extra_turn": _extra_turn_fixture,
    "hero_zone": _hero_zone_fixture,
}
FIXTURE_NAMES = tuple(FIXTURES)


def build_fixture(name: str, *, pack: ContentPack | None = None) -> Fixture:
    if name not in FIXTURES:
        raise KeyError(f"未知的 M6 固定 tape 局面：{name!r}（可选 {FIXTURE_NAMES}）")
    return FIXTURES[name](pack if pack is not None else load_content_pack(deck_profile=TAPE_PROFILE))


class FixtureRegistry(GameRegistry):
    """每建一局都从构造局面起跑；其余（取增量 / 应答 / 落盘）与产品注册表同一份。"""

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
    parser = argparse.ArgumentParser(description="M6 固定 tape 的构造局面 HTTP 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--runs-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    server = make_fixture_server(args.port, args.runs_dir, host=args.host)
    host, port = server.server_address[:2]
    print(f"sgs-m6-fixtures listening on http://{host}:{port}/", flush=True)
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
