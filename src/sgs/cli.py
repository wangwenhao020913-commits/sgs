"""命令行门面：跑一局、打印人类可读的**全知**记录流、退出码 0。

M1 的参数只有 ``--seed``（票 02 §2）；省略时按票 08 现生成 64 位种子、写进 header
并在 stdout 打印，事后可原样重跑。坏数据与非法行动都走 stderr + 退出码 1。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sgs.agents import RandomPlayer
from sgs.data import DataLoadError, RuleConfig, load_content_pack
from sgs.engine import (
    Engine,
    IllegalActionError,
    JournalRecord,
    RandomSource,
    new_seed,
)
from sgs.state import CardRef, Zone

__all__ = ["main", "render"]


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
    args = parser.parse_args(argv)

    seed = args.seed if args.seed is not None else new_seed()
    # 种子闸门只在唯一随机源里有第二份；这里直接借它的报错，不再抄一遍值域。
    try:
        source = RandomSource(seed)
    except ValueError as error:
        print(f"种子非法：{error}", file=sys.stderr)
        return 1

    try:
        pack = load_content_pack()
    except DataLoadError as error:
        print(error.report(), file=sys.stderr)
        return 1

    rule_config = RuleConfig()
    engine = Engine.new_game(seed=seed, rule_config=rule_config, pack=pack)
    ai_stream = source.substream("ai")
    participants = [RandomPlayer(ai_stream) for _ in range(rule_config.player_count)]
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
                f"#{record.seq:<4} 牌移动 {record.from_zone.value}→{record.to_zone.value} "
                f"来源={record.reason.value} {_cards(record.cards)}"
            )
        if record.delta_type == "hp_change":
            return (
                f"#{record.seq:<4} 体力 座位={record.seat} "
                f"{record.before}→{record.after}（{record.amount:+d}）"
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
    return (
        f"#{record.seq:<4} 终局 结果={record.result} 胜者={record.winner} "
        f"原因={record.reason} 检测点序号={record.check_seq}"
    )


def _cards(cards: Sequence[CardRef]) -> str:
    return "、".join(f"{card.name}{card.suit}{card.rank}" for card in cards)


def _zone_label(zone: Zone | None) -> str:
    if zone is None:
        return "（初次）"
    return {
        Zone.DECK: "牌堆",
        Zone.HAND: "手牌",
        Zone.DISCARD: "弃牌堆",
        Zone.PROCESSING: "处理区",
    }.get(zone, zone.value)
