"""命令行门面：跑一局、打印人类可读的**全知**记录流、退出码 0。

参数有 ``--seed``（票 02 §2）与 ``--players``（票 23：2/4/5/6/7/8、默认 2），
武将发放的 ``--heroes a,b,c`` / ``--hero-pool x,y,z``（票 02：显式指定 / 换启用池），
参与者档位 ``--ai random|heuristic``（票 13：**缺省仍是随机**——默认参与者不动，
M2 的 34 条基准磁带一行不改），以及牌堆档位 ``--deck-profile``（M5-01b：缺省 = 内容包
默认档 ``standard_108``；``standard_ex_160`` 要显式选，非法档位照加载器报错退出 1）。
种子省略时按票 08 现生成 64 位种子、写进 header 并在 stdout 打印，事后可原样重跑。
坏数据、非法人数与非法行动都走 stderr + 退出码 1。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sgs.agents import HeuristicPlayer, Participant, RandomPlayer
from sgs.data import (
    IDENTITY_LABELS,
    DataLoadError,
    HeroDealError,
    load_content_pack,
    validate_rule_config,
)
from sgs.engine import (
    Engine,
    IllegalActionError,
    JournalRecord,
    RandomSource,
    new_seed,
)
from sgs.state import CardRef, Zone, ZoneRef

__all__ = ["main", "render"]


def _csv(raw: str) -> tuple[str, ...]:
    """``a,b,c`` → ``("a", "b", "c")``；空白与空项一律剔除。"""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _player(name: str, stream) -> Participant:
    """按档位名造一个参与者；两档共用同一个 ``ai`` 子流（票 13 §档位）。"""
    if name == "heuristic":
        return HeuristicPlayer(stream)
    return RandomPlayer(stream)


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数、跑完整局并返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="sgs",
        description="跑一局《三国杀》（M1 引擎骨架）并打印事件流。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子；省略时现生成一个 64 位种子。",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=2,
        help="座位数：2/4/5/6/7/8；默认 2（2 人局是无身份 1v1）。",
    )
    parser.add_argument(
        "--heroes",
        type=_csv,
        default=(),
        help="显式指定武将（逗号分隔、按座位序）；给出时跳过随机发放。",
    )
    parser.add_argument(
        "--hero-pool",
        type=_csv,
        default=None,
        help="随机发放用的启用池（逗号分隔）；省略 = 无武将模式。",
    )
    parser.add_argument(
        "--ai",
        choices=("random", "heuristic"),
        default="random",
        help="参与者档位：random（缺省，均匀随机）或 heuristic（规则启发式）。",
    )
    parser.add_argument(
        "--deck-profile",
        default=None,
        help="牌堆档位（内容包 decks 的键）；省略 = 内容包默认档（standard_108）。",
    )
    args = parser.parse_args(argv)

    # 人数值域只在规则配置里有第二份；这里直接用它的加载期校验，不再抄一遍 2/4/5/6/7/8。
    try:
        rule_config = validate_rule_config({"player_count": args.players})
    except DataLoadError as error:
        details = "；".join(f"{item.loc} {item.got}" for item in error.errors)
        print(f"人数非法：{details}", file=sys.stderr)
        return 1

    seed = args.seed if args.seed is not None else new_seed()
    # 种子闸门只在唯一随机源里有第二份；这里直接借它的报错，不再抄一遍值域。
    try:
        source = RandomSource(seed)
    except ValueError as error:
        print(f"种子非法：{error}", file=sys.stderr)
        return 1

    try:
        pack = load_content_pack(deck_profile=args.deck_profile)
    except DataLoadError as error:
        print(error.report(), file=sys.stderr)
        return 1

    try:
        engine = Engine.new_game(
            seed=seed,
            rule_config=rule_config,
            pack=pack,
            heroes=args.heroes,
            hero_pool=args.hero_pool,
        )
    except HeroDealError as error:
        print(f"武将发放非法：{error}", file=sys.stderr)
        return 1
    ai_stream = source.substream("ai")
    participants: list[Participant] = [
        _player(args.ai, ai_stream) for _ in range(rule_config.player_count)
    ]
    try:
        engine.run(participants)
    except IllegalActionError as error:
        print(f"非法行动：{error}", file=sys.stderr)
        return 1

    print(f"seed = {seed}")
    for record in engine.records:
        print(render(record))
    return 0


def render(record: JournalRecord) -> str:
    """把一条记录渲染成人类可读的一行（表现层只渲染事件流，引擎不产出表现文本）。"""
    kind = record.kind
    if kind == "header":
        config = record.rule_config.model_dump(mode="json")
        return (
            f"header 种子={record.seed} 内容包={record.pack_id} "
            f"牌堆档位={record.deck_profile} 事件版本={record.event_schema_version} "
            f"规则配置={config}"
        )
    if kind == "timing":
        who = f" 座位={record.actor}" if record.actor is not None else ""
        cards = f" 牌={_cards(record.cards)}" if record.cards else ""
        return f"#{record.seq:<4} 时机 {record.timing.label}{who}{cards}"
    if kind == "delta":
        if record.delta_type == "card_move":
            return (
                f"#{record.seq:<4} 牌移动 {_zone_label(record.from_zone)}→"
                f"{_zone_label(record.to_zone)} 来源={record.reason.value} "
                f"{_cards(record.cards)}"
            )
        if record.delta_type == "hp_change":
            return (
                f"#{record.seq:<4} 体力 座位={record.seat} "
                f"{record.before}→{record.after}（{record.amount:+d}）"
            )
        if record.delta_type == "chained_change":
            # 横置 / 解除横置（M5-03）：只作调试转写；面向玩家的措辞归 M5-08d。
            state = "横置" if record.chained else "解除横置"
            return f"#{record.seq:<4} 连环 座位={record.seat} {state}"
        if record.delta_type == "identity_deal":
            return (
                f"#{record.seq:<4} 身份 座位={record.seat} "
                f"{IDENTITY_LABELS[record.identity]}"
            )
        if record.delta_type == "identity_reveal":
            return (
                f"#{record.seq:<4} 亮明身份 座位={record.seat} "
                f"{IDENTITY_LABELS[record.identity]}"
            )
        return f"#{record.seq:<4} 洗牌 {_zone_label(record.from_zone)}→牌堆 {_cards(record.cards)}"
    if kind == "query":
        candidates = "、".join(action.kind for action in record.query.legal_actions)
        return (
            f"#{record.seq:<4} 询问 {record.query.kind} → 座位={record.query.actor} "
            f"候选[{candidates}] {record.query.prompt}"
        )
    if kind == "action":
        state = "接受" if record.accepted else f"拒绝（{record.reason}）"
        return f"#{record.seq:<4} 应答 {record.action.kind} → {state}"
    if kind == "frame":
        label = f" return_to={record.return_to}" if record.return_to else ""
        return (
            f"#{record.seq:<4} 帧 {record.op} {record.frame_kind.value} "
            f"id={record.frame_id} 深度={record.depth}{label}"
        )
    winners = "、".join(str(seat) for seat in record.winners)
    if record.side is not None:
        # 身份局没有单一获胜座位：读获胜方 + 该侧全部座位（含阵亡者，票 25）。
        who = f"胜方={record.side} 获胜者=[{winners}]"
    else:
        who = f"胜者={record.winner} 获胜者=[{winners}]"
    return (
        f"#{record.seq:<4} 终局 结果={record.result} {who} "
        f"原因={record.reason} 检测点序号={record.check_seq}"
    )


def _cards(cards: Sequence[CardRef]) -> str:
    return "、".join(f"{card.name}{card.suit}{card.rank}" for card in cards)


def _zone_label(ref: ZoneRef | None) -> str:
    if ref is None:
        return "（初次）"
    label = {
        Zone.DECK: "牌堆",
        Zone.HAND: "手牌",
        Zone.DISCARD: "弃牌堆",
        Zone.PROCESSING: "处理区",
        Zone.EQUIP: "装备区",
        Zone.JUDGE: "判定区",
    }.get(ref.zone, ref.zone.value)
    if ref.seat is None:
        return label
    return f"座位{ref.seat} {label}"
