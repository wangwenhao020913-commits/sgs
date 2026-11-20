#!/usr/bin/env python3
"""构造局面注入服务（hero-flow-v2 票 03）：把「座位 / 体力 / 手牌 / 装备 / 阶段」写成一份
JSON，起一个**从那个局面开始**的牌桌，让人用真浏览器实机验收。

为什么要有它：实机验收是自由局（用户 2026-11-20 Q6 B），盲随机局里"让敌方恰好一刀砍到
1 血再轮到我"这种条件可能几十局都撞不上；用户点名要某个局面时（"必要构造场景注入服务时
我会和你说的"），需要一条**随时能起、随时能停**的路径。

它不新造引擎能力：局面构造用测试侧同一个入口 ``tests.support.positions.build_state``
（`SeatSpec` 已是声明式参数），HTTP 门面用产品 ``sgs_web.serve.make_server``，只把
``GameRegistry.create`` 换成"按这份局面建局"的注册表——与 `tests/support/m5_fixture_server.py`
同一条姿态（那边的注释也解释了为什么只换这一处）。每步询问仍走
``POST /api/sessions/<id>/answer``，与正常服务同一条路径。

用法::

    python tools/serve-position.py --position tools/positions/one-hp-two-cards.json
    python tools/serve-position.py --position <file>.json --port 0 --check   # 只解析、不起服务

局面文件（字段名照 ``SeatSpec``；``hp`` / ``hp_max`` 必填，不做"猜武将上限"）::

    {
      "seed": 4909,                      # 可选：AI 子流的种子（只影响 AI 选牌）
      "deck_profile": "standard_108",    # 可选；缺省 standard_108
      "human_seat": 0,                   # 可选；缺省 0
      "turn_seat": 0,                    # 可选；缺省 0
      "phase": "play",                   # 可选；prepare/judge/draw/play/discard/finish
      "seats": [
        {"hero": "zhangchunhua_mobile", "hp": 1, "hp_max": 3,
         "hand": ["slash", "peach"], "equip": ["crossbow"], "judge": [],
         "chained": false, "identity": "lord", "kingdom": "wei"},
        {"hero": "xusheng", "hp": 4, "hp_max": 4, "hand": ["dodge"]}
      ],
      "deck": ["slash", "dodge"],        # 可选：牌堆顶顺序（牌名）；缺省 = 其余全部牌
      "discard": []                      # 可选
    }

多座位（>2）的 ``identity`` 缺省：座位 0 = 主公，其余 = 反贼——身份局必须有主公座位，
这条缺省只是省掉重复书写，写错（比如两个主公）会被 ``RuleConfig`` / 引擎当场拒掉。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
# 测试侧的构造入口是单一份真源（`tests/support`）——宁可让本工具多一条 sys.path，
# 也不在这里另抄一份"JSON → GameState"的翻译，免得两边漂移。
for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from sgs.data import ContentPack, Identity, Kingdom, RuleConfig, load_content_pack  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.state import GameState, Phase  # noqa: E402
from sgs_web.serve import GameRegistry, make_server  # noqa: E402
from sgs_web.session import create_session  # noqa: E402
from support.positions import SeatSpec, build_state, cards_of_type  # noqa: E402

DEFAULT_DECK_PROFILE = "standard_108"
DEFAULT_SEED = 4909
"""与 M5/M6 固定 tape 同一个种子：构造局面本身与种子无关，它只决定 AI 怎么选牌。"""


@dataclass(frozen=True)
class Position:
    """一份解析好的局面：状态 + 开局参数 + 一句给人看的说明。"""

    state: GameState
    pack: ContentPack
    seed: int
    human_seat: int
    note: str

    @property
    def player_count(self) -> int:
        return len(self.state.players)


def _fail(where: str, message: str) -> None:
    raise ValueError(f"{where}：{message}")


def _resolve_card(pack: ContentPack, used: set[int], name: Any, where: str) -> int:
    """按牌名取一张**尚未被占用**的实例；未知牌名与"用光了"分开报。"""
    if not isinstance(name, str) or not name:
        _fail(where, f"牌名必须是非空字符串，实际 {name!r}")
    candidates = cards_of_type(pack, name)
    if not candidates:
        _fail(where, f"牌堆里没有牌名 {name!r}（常见：用了中文名而不是数据表 type_id）")
    for index in candidates:
        if index not in used:
            used.add(index)
            return index
    _fail(where, f"{name!r} 的牌已经全部被别的座位 / 牌堆占用了")
    raise AssertionError("unreachable")


def _cards(pack: ContentPack, used: set[int], raw: Any, where: str) -> tuple[int, ...]:
    """一个牌区：数组（可省），每项是牌名。写成字符串会被当场拒掉，免得按字符猜。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        _fail(where, f"必须是牌名数组，实际 {type(raw).__name__}")
    return tuple(_resolve_card(pack, used, name, where) for name in raw)


def _seats(doc: dict[str, Any], pack: ContentPack, used: set[int]) -> list[SeatSpec]:
    raw = doc.get("seats")
    if not isinstance(raw, list) or not raw:
        _fail("seats", "必须是一个非空数组")
    specs: list[SeatSpec] = []
    for index, seat in enumerate(raw):
        where = f"seats[{index}]"
        if not isinstance(seat, dict):
            _fail(where, "必须是对象")
        hero = seat.get("hero")
        if not isinstance(hero, str) or not hero:
            _fail(where, "hero 必须是非空字符串（武将 id，例如 zhangchunhua_mobile）")
        hp, hp_max = seat.get("hp"), seat.get("hp_max")
        if not isinstance(hp, int) or not isinstance(hp_max, int):
            _fail(where, "hp / hp_max 必填且必须是整数（本工具不替你猜武将上限）")
        hand = _cards(pack, used, seat.get("hand"), f"{where}.hand")
        equip = _cards(pack, used, seat.get("equip"), f"{where}.equip")
        judge = _cards(pack, used, seat.get("judge"), f"{where}.judge")
        identity = seat.get("identity")
        if identity is not None:
            try:
                identity = Identity(str(identity))
            except ValueError as error:
                _fail(where, f"identity 认不出：{seat.get('identity')!r}（{error}）")
        kingdom = seat.get("kingdom")
        if kingdom is not None:
            try:
                kingdom = Kingdom(str(kingdom))
            except ValueError as error:
                _fail(where, f"kingdom 认不出：{seat.get('kingdom')!r}（{error}）")
        specs.append(
            SeatSpec(
                hp=hp,
                hp_max=hp_max,
                hand=hand,
                equip=equip,
                judge=judge,
                alive=bool(seat.get("alive", True)),
                identity=identity,
                hero=hero,
                kingdom=kingdom,
                chained=bool(seat.get("chained", False)),
            )
        )
    return specs


def parse_position(doc: dict[str, Any], *, pack: ContentPack | None = None) -> Position:
    """把一份 JSON 文档解析成 :class:`Position`；任何口径问题都在这里抛 ``ValueError``。"""
    if not isinstance(doc, dict):
        _fail("根部", "必须是一个 JSON 对象")
    profile = doc.get("deck_profile", DEFAULT_DECK_PROFILE)
    if not isinstance(profile, str) or not profile:
        _fail("deck_profile", "必须是非空字符串")
    resolved_pack = pack if pack is not None else load_content_pack(deck_profile=profile)
    seed = doc.get("seed", DEFAULT_SEED)
    if not isinstance(seed, int):
        _fail("seed", "必须是整数")
    human_seat = doc.get("human_seat", 0)
    if not isinstance(human_seat, int):
        _fail("human_seat", "必须是整数")

    used: set[int] = set()
    seats = _seats(doc, resolved_pack, used)

    player_count = len(seats)
    turn_seat = doc.get("turn_seat", 0)
    if not isinstance(turn_seat, int) or not 0 <= turn_seat < player_count:
        _fail("turn_seat", f"必须是 0..{player_count - 1} 的整数")
    if not 0 <= human_seat < player_count:
        _fail("human_seat", f"必须是 0..{player_count - 1} 的整数")

    phase_name = doc.get("phase")
    phase = None
    if phase_name is not None:
        try:
            phase = Phase(str(phase_name))
        except ValueError as error:
            _fail("phase", f"认不出 {phase_name!r}（{error}）")

    # 身份局（>2 人）必须有主公座位；缺省补成"座位 0 主公、其余反贼"，并记进说明，
    # 免得验收时看不出这是工具补的。
    notes: list[str] = []
    if player_count > 2 and any(seat.identity is None for seat in seats):
        patched = []
        for index, seat in enumerate(seats):
            if seat.identity is None:
                seat = dataclasses.replace(
                    seat, identity=Identity.LORD if index == 0 else Identity.REBEL
                )
            patched.append(seat)
        seats = patched
        notes.append("身份缺省已补：座位 0 = 主公，其余 = 反贼")

    deck = tuple(
        _resolve_card(resolved_pack, used, name, "deck") for name in doc.get("deck") or ()
    ) or tuple(index for index in range(len(resolved_pack.deck.cards)) if index not in used)
    discard = tuple(
        _resolve_card(resolved_pack, used, name, "discard") for name in doc.get("discard") or ()
    )

    state = build_state(
        pack=resolved_pack,
        seats=seats,
        deck=deck,
        discard=discard,
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=player_count, first_seat=turn_seat),
    )
    return Position(
        state=state,
        pack=resolved_pack,
        seed=seed,
        human_seat=human_seat,
        note="；".join(notes),
    )


def load_position(path: Path, *, pack: ContentPack | None = None) -> Position:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"局面文件不存在：{path}") from None
    except json.JSONDecodeError as error:
        raise SystemExit(f"局面文件不是合法 JSON（{path}）：{error}") from None
    try:
        return parse_position(doc, pack=pack)
    except ValueError as error:
        raise SystemExit(f"局面文件有口径问题（{path}）：{error}") from None


class PositionRegistry(GameRegistry):
    """每建一局都从**这份构造局面**起跑；取增量 / 应答 / 落盘与产品注册表同一份。"""

    def __init__(self, position: Position, runs_dir: Path | None = None) -> None:
        super().__init__(runs_dir=runs_dir)
        self.position = position

    def create(self, body: dict[str, Any]) -> Any:  # noqa: ANN401 - 与 GameRegistry.create 同签名
        position = self.position
        self._counter += 1
        session = create_session(
            seed=position.seed,
            player_count=position.player_count,
            ai="heuristic",
            human_seat=position.human_seat,
            hero_pool=None,
            timeout_seconds=None,
            pack=position.pack,
            session_id=f"s{self._counter}",
        )
        session.engine = Engine.from_state(position.state, seed=position.seed)
        session.drive()
        session.mark_pending(time.monotonic())
        self.sessions[session.session_id] = session
        return session


def make_position_server(
    position: Position,
    port: int = 0,
    runs_dir: Path | None = None,
    host: str = "127.0.0.1",
):
    server = make_server(port, runs_dir, host=host)
    registry = PositionRegistry(position, runs_dir=runs_dir)
    server.registry = registry  # type: ignore[attr-defined]
    server.RequestHandlerClass.registry = registry  # type: ignore[attr-defined]
    return server


def describe(position: Position) -> str:
    """一行一句座位快照——`--check` 打印它，验收交付卡也用它。"""
    cards = position.pack.deck.cards
    parts = []
    for seat in position.state.players:
        hand = "+".join(cards[index].type for index in seat.hand) or "空手"
        parts.append(f"{seat.seat}:{seat.hero or '?'} {seat.hp}/{seat.hp_max} [{hand}]")
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从一份构造局面起一个可玩的牌桌（实机验收用）")
    parser.add_argument("--position", required=True, type=Path, help="局面 JSON 文件")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 内核分配（缺省），实际端口会打印")
    parser.add_argument("--runs-dir", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="只解析并打印座位，不起服务")
    args = parser.parse_args(argv)

    position = load_position(args.position)
    print(f"局面：{args.position}", flush=True)
    print(f"座位：{describe(position)}", flush=True)
    if position.note:
        print(f"补的缺省：{position.note}", flush=True)
    if args.check:
        return 0

    server = make_position_server(position, args.port, args.runs_dir, host=args.host)
    host, port = server.server_address[:2]
    print(f"sgs-position listening on http://{host}:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
