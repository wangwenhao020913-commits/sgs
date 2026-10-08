"""记录流 schema（票 06）：七种 ``kind`` 的 frozen 记录模型 + canonical JSONL 序列化器。

七个 kind = ``header`` / ``timing`` / ``delta`` / ``query`` / ``action`` / ``frame`` /
``game_end``。全流共用**一个** ``seq``：``header`` 不带，其余从 1 起严格递增
（不必连续：询问、应答、帧、终局都占号）。

``dump_journal`` 是**唯一**序列化器：UTF-8 无 BOM、LF、一记录一行、键排序、
紧凑分隔符、不转义中文、末行也有换行。写死这些才算"逐行字节可比对"。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from sgs.data import RuleConfig
from sgs.state import (
    Action,
    CardMoveDelta,
    CardRef,
    DeckShuffleDelta,
    Delta,
    FrameKind,
    HpChangeDelta,
    Query,
)
from sgs.timing import Timing

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "ActionRecord",
    "CardMoveRecord",
    "DeckShuffleRecord",
    "DeltaRecord",
    "FrameRecord",
    "GameEndRecord",
    "Header",
    "HpChangeRecord",
    "JournalRecord",
    "QueryRecord",
    "TimingRecord",
    "dump_journal",
    "record_json",
    "wrap_delta",
]

EVENT_SCHEMA_VERSION = 1
"""记录流结构版本；与数据版本 ``schema_version`` 字段名不同、各自升版（票 15）。"""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Header(_FrozenModel):
    """信封：本局可复现的全部前置事实；首行、**不带** ``seq``、禁易变字段。"""

    kind: Literal["header"] = "header"
    event_schema_version: int = EVENT_SCHEMA_VERSION
    seed: int
    pack_id: str
    deck_profile: str
    rule_config: RuleConfig


class TimingRecord(_FrozenModel):
    """一次时机 dispatch；空实现也照记（"缝存在"可被观测）。"""

    kind: Literal["timing"] = "timing"
    seq: int
    timing: Timing
    frame_id: int
    depth: int
    actor: int | None = None
    source: int | None = None
    targets: tuple[int, ...] = ()
    cards: tuple[CardRef, ...] = ()


class CardMoveRecord(CardMoveDelta):
    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class HpChangeRecord(HpChangeDelta):
    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class DeckShuffleRecord(DeckShuffleDelta):
    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


DeltaRecord = Annotated[
    CardMoveRecord | HpChangeRecord | DeckShuffleRecord,
    Field(discriminator="delta_type"),
]


class QueryRecord(_FrozenModel):
    """一次询问；内联完整 Query（含候选与视角），自足、不摘要。"""

    kind: Literal["query"] = "query"
    seq: int
    query: Query


class ActionRecord(_FrozenModel):
    """一次应答；``answers_seq`` 由内联行动转出，是同一个键的单一真源。"""

    kind: Literal["action"] = "action"
    seq: int
    accepted: bool
    reason: str | None = None
    action: Action

    @computed_field  # type: ignore[prop-decorator]
    @property
    def answers_seq(self) -> int:
        return self.action.answers_seq


class FrameRecord(_FrozenModel):
    """结算栈帧的生命周期；**不**记帧私有 ``data`` / ``pc``（票 04 §3）。"""

    kind: Literal["frame"] = "frame"
    seq: int
    op: Literal["push", "pop", "discard"]
    frame_id: int
    frame_kind: FrameKind
    depth: int
    return_to: tuple[str, str] | None = None


class GameEndRecord(_FrozenModel):
    """终局：清栈之后落定，是流里**最后**一条记录。"""

    kind: Literal["game_end"] = "game_end"
    seq: int
    result: Literal["winner", "draw"]
    winner: int | None = None
    reason: str
    check_seq: int


JournalRecord = (
    Header
    | TimingRecord
    | DeltaRecord
    | QueryRecord
    | ActionRecord
    | FrameRecord
    | GameEndRecord
)
"""七种记录的 tagged union；判别字段 ``kind``（delta 再按 ``delta_type`` 细分）。"""


def wrap_delta(delta: Delta, *, seq: int, frame_id: int, depth: int) -> DeltaRecord:
    """把叶子原语返回的纯 delta 包成带 ``seq`` / ``frame_id`` / ``depth`` 的记录。"""
    payload = delta.model_dump()
    if isinstance(delta, CardMoveDelta):
        return CardMoveRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, HpChangeDelta):
        return HpChangeRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    return DeckShuffleRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)


def record_json(record: JournalRecord) -> str:
    """单条记录的 canonical 文本（不含换行）。"""
    payload = record.model_dump(mode="json")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def dump_journal(records: Sequence[JournalRecord]) -> str:
    """把记录流序列化成 canonical JSONL（唯一序列化器）。"""
    return "".join(f"{record_json(record)}\n" for record in records)
