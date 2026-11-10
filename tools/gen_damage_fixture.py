#!/usr/bin/env python3
"""真会话夹具：`闪电 → 连环传导`（damage-fx-natures 票 04）。

产出一份**与 `/api/sessions` 增量同一组投影函数**产出的载荷片段——`records`（服务端下发的
可见记录）+ `damage_events`（伤害事实派生通道）——喂给前端烟测的 `loadDamageBatch`，补上票 10
记的那条欠账（`damage-fx/issues/10-single-source-and-rename.md:52-58`；既有合成用例保留）。
HTTP 外壳（游标 / snapshot / narrative）不在夹具里：这里用的是 `serve.py:view()` 的同一组
投影函数，不另造一层。

**为什么是这条剧本**：它一次同时钉住只有属性演出才会踩到的两个形状——

- `source: null`（天降：默认牌堆的【闪电】没有来源）；
- **同批多条**（横置 / 连环传导：一目标一帧，`src/sgs/engine/game.py` 的
  `_push_next_conduction`）。

两点合成事件也能造，但造不出"服务端真的这么下发"。夹具因此必须是**真引擎跑出来的**。

**剧本**（写死，不带随机）：`seed` / `standard_108` / 4 人；0 号位的判定区置一张【闪电】，
0 / 1 / 2 号横置、3 号不横置；牌堆顶是一张 ♠2–9（判定必中）。跑 0 号位的回合，得到：

    判定中 → 3 点无来源雷电（seat 0）→ 传导 seat 1 → 传导 seat 2（3 号未横置，不传导）
    → 【闪电】入弃牌堆

**边界（照实记，不假装覆盖）**：横置状态与判定区是**局面构造**给定（标准 108 里没有
【铁索连环】，横置在纯打牌路径上到不了），从 `Engine.from_state` 起跑——与引擎测试同一姿态。
从判定、伤害、连环传导到弃牌，每一步都是**真引擎代码路径**；夹具是"今天的引擎会产出什么"
的冻结证据，引擎行为变了（判定区间 / 传导 / 事件字段）重跑生成器就会红。

用法（仓根执行）：

    .venv/bin/python tools/gen_damage_fixture.py            # 打印（不写盘）
    .venv/bin/python tools/gen_damage_fixture.py --update   # 重写提交的夹具

`--update` 才写盘：夹具是**证据**，随手改掉它等于让"引擎变了"这件事静默过关。
`tests/test_web_static.py` 的重跑比对走这个入口（默认打印、逐字节比）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, instantiate_cards
from sgs.state import EndPhase, GameState, Pass, Phase, Player, Zone
from sgs_web.projection import (
    concealed_by_later_reveal,
    concealed_guohuo_cards,
    damage_events_after,
    decorate_records_card_tags,
    query_actor_map,
    visible_record,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "support" / "fixtures" / "thunder-conduction.json"

SEED = 20261111
"""冻结剧本的种子：只影响洗牌（本剧本的判定牌是显式置顶的，不靠它抽牌）。"""

PLAYER_COUNT = 4
DECK_PROFILE = "standard_108"
HUMAN_SEAT = 0
"""查看者座位 = 0（与实机开局的缺省座位一致）：记录按这个视角裁剪，与网页收到的一致。"""

CHAINED_SEATS = (0, 1, 2)
"""横置的座位；3 号**不**横置——传导名单因此有一个"该跳过的人"。"""

EXPECTED_EVENTS = 3
"""【闪电】命中 3 点 + 传导给另外两个横置角色 = 同批三条。"""

LIGHTNING_AMOUNT = 3
"""【闪电】命中固定 3 点（`game.py` 的雷电伤害）；剧本自检按它断，避免"剧本自己改了点数"。"""

RECORD_CAP = 2000
"""剧本的保险丝：正常剧本 72 条记录就收尾；引擎行为漂到"闪电不再收尾"时别把门禁拖死
（剧本只检查到站条件，被动局本来就不会自然终局——见 `tests/support/positions.py` 的注记）。"""


class _Halt(Exception):
    """`Engine.run` 的跳出信号：剧本跑到【闪电】落弃牌堆即停（与测试驱动同一手法）。"""


class _ScriptedPlayer:
    """确定性脚本参与者：到站即跳出，否则取最小合法应答（结束阶段 / 放弃 / 第一个候选）。

    与 `tests/support/positions.py:RecordingPlayer` 同姿态；工具不 import 测试支持，
    故这里只留剧本需要的那几条（不记询问、不做策略）。
    """

    def __init__(self, engine: Engine, stop: Callable[[Engine], bool]) -> None:
        self._engine = engine
        self._stop = stop

    def respond(self, query: Any) -> Any:
        if self._stop(self._engine):
            raise _Halt
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _lightning_card(pack: Any) -> int:
    for index, card in enumerate(pack.deck.cards):
        if card.type == "lightning":
            return index
    raise RuntimeError("standard_108 里没有【闪电】")


def _judge_card(pack: Any, *, excluded: tuple[int, ...]) -> int:
    """牌堆顶那张判定牌：♠2–9（【闪电】命中的花色点数区间），不依赖随机抽牌。"""
    for index, card in enumerate(pack.deck.cards):
        if index in excluded:
            continue
        if card.suit.value == "spade" and 2 <= card.rank <= 9:
            return index
    raise RuntimeError("standard_108 里找不到 ♠2–9 的判定牌")


def _build_scenario(pack: Any, *, lightning: int, judge: int) -> GameState:
    """把剧本写成 `GameState`（唯一的局面构造点）：判定区 / 横置 / 置顶的判定牌。"""
    excluded = (lightning, judge)
    deck = (judge,) + tuple(
        card_id for card_id in range(len(pack.deck.cards)) if card_id not in excluded
    )
    players = tuple(
        Player(
            seat=seat,
            hp=4,
            hp_max=4,
            alive=True,
            hand=[],
            equip=[],
            judge=[lightning] if seat == 0 else [],
            chained=seat in CHAINED_SEATS,
        )
        for seat in range(PLAYER_COUNT)
    )
    return GameState(
        rule_config=RuleConfig(),
        pack_id=pack.pack_id,
        deck_profile=pack.deck_profile,
        cards=instantiate_cards(pack),
        players=players,
        deck=list(deck),
        turn_seat=0,
        phase=Phase.PREPARE,
    )


def _lightning_discarded(engine: Engine, *, lightning: int) -> bool:
    """【闪电】真的进了弃牌堆（剧本的到站条件）。"""
    return lightning in engine.state.discard


def _lightning_discarded_or_out_of_budget(engine: Engine, *, lightning: int) -> bool:
    return _lightning_discarded(engine, lightning=lightning) or len(engine.records) > RECORD_CAP


def _run_until_lightning_discarded(engine: Engine, *, lightning: int) -> None:
    stop = lambda current: _lightning_discarded_or_out_of_budget(  # noqa: E731
        current, lightning=lightning
    )
    players = [_ScriptedPlayer(engine, stop) for _ in range(PLAYER_COUNT)]
    try:
        engine.run(players)
    except _Halt:
        pass
    if not _lightning_discarded(engine, lightning=lightning):
        raise RuntimeError(
            "剧本没有走到【闪电】入弃牌堆（记录数上限 "
            f"{RECORD_CAP}）：引擎行为变了，先看记录流"
        )


def _discard_seq(engine: Engine, *, lightning: int) -> int:
    """【闪电】入弃牌堆那一条 `card_move` 的 seq（夹具窗口的右端）。"""
    for record in reversed(engine.records):
        if getattr(record, "kind", None) != "delta":
            continue
        if getattr(record, "delta_type", None) != "card_move":
            continue
        if record.to_zone.zone is not Zone.DISCARD:
            continue
        if any(card.card_id == lightning for card in record.cards):
            return int(record.seq)
    raise RuntimeError("找不到【闪电】入弃牌堆的那条 card_move：剧本形状变了")


def _assert_scenario(events: list[dict[str, Any]], window: list[Any]) -> None:
    """剧本自检：形状漂了就在这里炸，别让夹具悄悄变成另一条剧本。"""
    if len(events) != EXPECTED_EVENTS:
        raise RuntimeError(f"期望同批 {EXPECTED_EVENTS} 条伤害事件，实际 {len(events)}：{events}")
    if [event["target"] for event in events] != list(CHAINED_SEATS):
        raise RuntimeError(f"传导目标不对：{[event['target'] for event in events]}")
    for event in events:
        if event["source"] is not None:
            raise RuntimeError(f"【闪电】是无来源伤害，这里却有来源：{event}")
        if event["nature"] != "THUNDER":
            raise RuntimeError(f"【闪电】是雷电伤害，这里却是 {event['nature']!r}")
        if event["amount"] != LIGHTNING_AMOUNT:
            raise RuntimeError(f"【闪电】固定 {LIGHTNING_AMOUNT} 点，这里却是 {event['amount']}")
    seqs = {int(record.seq) for record in window}
    for event in events:
        if event["seq"] not in seqs:
            raise RuntimeError(f"事件锚点 {event['seq']} 不在记录窗口里：夹具切片错了")
    hp_changes = [
        record
        for record in window
        if getattr(record, "kind", None) == "delta"
        and getattr(record, "delta_type", None) == "hp_change"
    ]
    if len(hp_changes) != EXPECTED_EVENTS:
        raise RuntimeError(f"期望 {EXPECTED_EVENTS} 条 hp_change，实际 {len(hp_changes)}")


def _payload() -> dict[str, Any]:
    """跑一遍剧本，返回**要提交的**载荷（键序由 `json.dumps(sort_keys=True)` 统一）。"""
    pack = load_content_pack(deck_profile=DECK_PROFILE)
    lightning = _lightning_card(pack)
    judge = _judge_card(pack, excluded=(lightning,))
    engine = Engine.from_state(
        _build_scenario(pack, lightning=lightning, judge=judge),
        seed=SEED,
    )
    _run_until_lightning_discarded(engine, lightning=lightning)
    discard_seq = _discard_seq(engine, lightning=lightning)
    all_records = engine.records
    # `header` 不带 seq、只在建局那一批（`after < 0`）下发；夹具是"从第一回合开始"的片段，
    # 故从第一条带 seq 的记录切起（服务端 `records_after(after=0)` 的同一形状）。
    window = [
        record
        for record in all_records
        if isinstance(getattr(record, "seq", None), int) and record.seq <= discard_seq
    ]
    events = damage_events_after(
        window,
        after=-1,
        state=engine.state,
        events=engine.damage_events,
    )
    _assert_scenario(events, window)
    # 记录通道与 `serve.py:view()` **同一组投影函数**（本剧本没有【蛊惑】扣置，`concealed`
    # 因此是空表；标签增删照常过 `decorate_records_card_tags`——声明与 HTTP 外壳无关，
    # 这里只是不经过 `WebService` 那一层）。`query_actors` 取全量记录：询问可能早于窗口。
    actors = query_actor_map(all_records)
    concealed = {
        **concealed_by_later_reveal(window, engine.state),
        **concealed_guohuo_cards(engine),
    }
    records = []
    for record in window:
        record_payload = visible_record(
            record,
            viewer=HUMAN_SEAT,
            query_actors=actors,
            state=engine.state,
            concealed=concealed,
        )
        if record_payload is not None:
            records.append(record_payload)
    decorate_records_card_tags(records, engine.state, HUMAN_SEAT)
    return {
        "meta": {
            "scenario": "闪电 → 连环传导",
            "seed": SEED,
            "deck_profile": DECK_PROFILE,
            "player_count": PLAYER_COUNT,
            "human_seat": HUMAN_SEAT,
            "chained_seats": list(CHAINED_SEATS),
            "judge_card": {
                "card_id": judge,
                "type": pack.deck.cards[judge].type,
                "suit": pack.deck.cards[judge].suit.value,
                "rank": pack.deck.cards[judge].rank,
            },
            "lightning_card": lightning,
            "note": (
                "横置 / 判定区为局面构造给定（standard_108 无【铁索连环】）；"
                "判定 → 伤害 → 传导 → 弃牌走真引擎。"
            ),
        },
        "records": records,
        "damage_events": events,
    }


def render() -> str:
    """夹具的 canonical 文本（唯一序列化口；`--update` 与逐字节比对都读它）。"""
    return json.dumps(_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出真会话夹具：闪电 → 连环传导")
    parser.add_argument(
        "--update",
        action="store_true",
        help="重写提交的夹具（缺省只打印，不写盘）",
    )
    args = parser.parse_args(argv)
    text = render()
    if args.update:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8")
        print(
            f"已重写 {FIXTURE.relative_to(ROOT)}（{len(text.encode('utf-8'))} 字节）",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
