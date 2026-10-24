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
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, computed_field, model_validator

from sgs.data import RuleConfig
from sgs.state import (
    Action,
    CardMoveDelta,
    CardRef,
    CardRevealDelta,
    ChainedChangeDelta,
    DeckShuffleDelta,
    Delta,
    FrameKind,
    HpChangeDelta,
    HpMaxChangeDelta,
    IdentityDealDelta,
    IdentityRevealDelta,
    Query,
    SkillGainDelta,
    SkillLoseDelta,
    SkillSuppressDelta,
    TurnChangeDelta,
    WineChangeDelta,
)
from sgs.timing import Timing

from .errors import JournalVersionError
from .victory import Side

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "SUPPORTED_EVENT_VERSIONS",
    "ActionRecord",
    "CardMoveRecord",
    "CardRevealRecord",
    "ChainedChangeRecord",
    "DeckShuffleRecord",
    "DeltaRecord",
    "FrameRecord",
    "GameEndRecord",
    "Header",
    "HpChangeRecord",
    "HpMaxChangeRecord",
    "IdentityDealRecord",
    "IdentityRevealRecord",
    "JournalRecord",
    "MalformedAction",
    "QueryRecord",
    "SkillGainRecord",
    "SkillLoseRecord",
    "SkillSuppressRecord",
    "TimingRecord",
    "TurnChangeRecord",
    "WineChangeRecord",
    "dump_journal",
    "read_journal",
    "record_json",
    "wrap_delta",
]

EVENT_SCHEMA_VERSION = 2
"""记录流结构版本；与数据版本 ``schema_version`` 字段名不同、各自升版（票 15 / 票 16）。

M2 集体一升：区域引用升 ``ZoneRef``、``owner`` 改义、``PlayerPublic`` 增公开区字段……
此后任何票新增 / 变更记录字段或 ``delta_type`` 一律**就地追加**到票 16 §2 的 v2 清单。
"""

SUPPORTED_EVENT_VERSIONS: frozenset[int] = frozenset({EVENT_SCHEMA_VERSION})
"""本引擎读得懂的记录流版本集合（M2 起只有 2；**不建** v1 兼容读取器，票 16 §4）。"""


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


class HpMaxChangeRecord(HpMaxChangeDelta):
    """一次**体力上限**变化（M6 票 06）：觉醒技"减 1 点体力上限"。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class ChainedChangeRecord(ChainedChangeDelta):
    """一次横置状态变化（M5-03）：铁索的 toggle 与属性伤害命中的"结算后立即重置"。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class WineChangeRecord(WineChangeDelta):
    """一次【酒】加伤状态变化（M5-04）：出牌阶段置位 / 下一张【杀】兑现 / 回合末清理。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class SkillGainRecord(SkillGainDelta):
    """一次**技能获得**（M6 票 04）：运行时授予的第四种来源。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class SkillLoseRecord(SkillLoseDelta):
    """一次**技能失去**（M6 票 04）：实例不再存在、不可恢复。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class SkillSuppressRecord(SkillSuppressDelta):
    """一次**技能失效 / 恢复**（M6 票 04）：座位级记录被加上或撤掉。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class TurnChangeRecord(TurnChangeDelta):
    """一个**回合边界**的事实（M6 票 11）：额外回合开始 / 轮次变化。

    直接落在回合环（帧外、``frame_id = 0``），故坐标与既有的回合级记录一致。
    """

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class DeckShuffleRecord(DeckShuffleDelta):
    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class CardRevealRecord(CardRevealDelta):
    """一张手牌成为公开事实（M5-06）：【火攻】亮出的那一张，牌仍留在手里。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class IdentityDealRecord(IdentityDealDelta):
    """一次身份发放（票 13 §3 / 票 24）：开局逐座各一条、落在发牌之前。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


class IdentityRevealRecord(IdentityRevealDelta):
    """一次身份亮明（票 25 / 决策回票 14 §5）：阵亡结算内逐座各一条、检测之前。"""

    kind: Literal["delta"] = "delta"
    seq: int
    frame_id: int
    depth: int


DeltaRecord = Annotated[
    CardMoveRecord
    | HpChangeRecord
    | HpMaxChangeRecord
    | ChainedChangeRecord
    | WineChangeRecord
    | CardRevealRecord
    | DeckShuffleRecord
    | IdentityDealRecord
    | IdentityRevealRecord
    | SkillGainRecord
    | SkillLoseRecord
    | SkillSuppressRecord
    | TurnChangeRecord,
    Field(discriminator="delta_type"),
]


class QueryRecord(_FrozenModel):
    """一次询问；内联完整 Query（含候选与视角），自足、不摘要。"""

    kind: Literal["query"] = "query"
    seq: int
    query: Query


class MalformedAction(_FrozenModel):
    """**结构损坏**的应答在 ``action`` 记录里的承载（票 07 §5 的 ``Malformed`` 一类）。

    它**不是**一种行动：``Action`` 协议（票 05）冻结为四个构造子，损坏的应答按定义
    读不出任何构造子。若不给它一个位置，"被拒应答记 ``accepted = false``"（票 24）
    就只剩一个异常，拒绝不可观测；故 ``action`` 记录的 ``action`` 字段放宽为
    ``Action | MalformedAction``。合法应答的序列化字节**一个都不变**，既有基准因此
    不升版（票 15 的升版判据 = 既有基准重跑字节是否变）。
    """

    kind: Literal["MalformedAction"] = "MalformedAction"
    answers_seq: int | None = None
    """从损坏载荷里**尽力**读出的应答序号；读不出即 ``None``。"""

    detail: str
    """结构错误的首条路径与说明（pydantic 校验错误的首项）。"""

    raw: str
    """损坏载荷的 canonical 文本（JSON；不可 JSON 化时退化为 ``repr``）。"""


class ActionRecord(_FrozenModel):
    """一次应答；``answers_seq`` 由内联行动转出，是同一个键的单一真源。

    ``action`` 通常是四个构造子之一；**结构损坏**的应答承载为 :class:`MalformedAction`
    （``accepted = false``），使"拒绝本身可观测"对三类非法输入都成立（票 07 §5 / 票 24）。
    """

    kind: Literal["action"] = "action"
    seq: int
    accepted: bool
    reason: str | None = None
    action: Action | MalformedAction

    @model_validator(mode="before")
    @classmethod
    def _drop_derived_answers_seq(cls, data: object) -> object:
        """反序列化时丢掉顶层那份派生副本；单一真源是内联行动（票 06 §3 / 票 18）。

        ``answers_seq`` 是 ``computed_field``：序列化会写出它，``extra="forbid"`` 却会
        在反序列化时拒收它。丢掉而不是校验，是因为它**没有独立真源**——权威值在内联行动里。
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key != "answers_seq"}
        return data

    @computed_field  # type: ignore[prop-decorator]
    @property
    def answers_seq(self) -> int | None:
        return getattr(self.action, "answers_seq", None)


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
    """终局：清栈之后落定，是流里**最后**一条记录。

    ``side`` / ``winners`` 是身份局的胜负事实（票 25 / 决策回票 14 §1）：``winners`` =
    获胜一侧的**全部**座位（含已阵亡者，按座位升序）、``winner`` 在身份局为 ``None``；
    无身份局与平局沿用 M1 的形状（``side=None``；胜局 ``winners=(获胜座位,)``、平局 ``()``）。
    """

    kind: Literal["game_end"] = "game_end"
    seq: int
    result: Literal["winner", "draw"]
    winner: int | None = None
    side: Side | None = None
    winners: tuple[int, ...] = ()
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
    if isinstance(delta, HpMaxChangeDelta):
        return HpMaxChangeRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, ChainedChangeDelta):
        return ChainedChangeRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, WineChangeDelta):
        return WineChangeRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, CardRevealDelta):
        return CardRevealRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, IdentityDealDelta):
        return IdentityDealRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, IdentityRevealDelta):
        return IdentityRevealRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, SkillGainDelta):
        return SkillGainRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, SkillLoseDelta):
        return SkillLoseRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, SkillSuppressDelta):
        return SkillSuppressRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
    if isinstance(delta, TurnChangeDelta):
        return TurnChangeRecord(seq=seq, frame_id=frame_id, depth=depth, **payload)
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


class _EventVersionProbe(BaseModel):
    """只读版本闸门用的最小模型：``strict`` 让"非整数版本号"变成结构错误（票 15 §2）。"""

    model_config = ConfigDict(strict=True, extra="ignore")

    event_schema_version: int


_DELTA_ADAPTER: TypeAdapter[DeltaRecord] = TypeAdapter(DeltaRecord)
_RECORD_MODELS: dict[str, type[BaseModel]] = {
    "header": Header,
    "timing": TimingRecord,
    "query": QueryRecord,
    "action": ActionRecord,
    "frame": FrameRecord,
    "game_end": GameEndRecord,
}
"""``kind`` → 记录模型；``delta`` 再按 ``delta_type`` 走 :data:`_DELTA_ADAPTER`。

手写分派而不是让 pydantic 猜联合类型：``kind`` 是七种记录的唯一判别字段，
而 ``delta`` 这一支内部还有一层 ``delta_type`` 判别（嵌套判别联合 pydantic 不直接支持）。
"""


def _parse_record(payload: object) -> JournalRecord:
    if not isinstance(payload, dict):
        raise ValueError(f"记录流的一行必须是 JSON 对象，实得 {type(payload).__name__}")
    kind = payload.get("kind")
    if kind == "delta":
        return _DELTA_ADAPTER.validate_python(payload)
    model = _RECORD_MODELS.get(kind) if isinstance(kind, str) else None
    if model is None:
        raise ValueError(f"未知的记录 kind：{kind!r}")
    return model.model_validate(payload)  # type: ignore[return-value]


def read_journal(path: str | Path) -> tuple[JournalRecord, ...]:
    """唯一反序列化器（:func:`dump_journal` 的对称物）：**版本先行** → 全量结构校验。

    版本号缺失 / 非整数 ⇒ 结构错误（pydantic 校验失败），**不**报版本不符；
    未知版本 ⇒ :class:`~sgs.engine.JournalVersionError`，消息里带双方版本（票 15 §2）。
    返回的元组含首行 ``header``，于是 :func:`sgs.agents.tape_from_journal` 的签名不变。
    """
    resolved = Path(path)
    lines = resolved.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"记录流为空：{resolved}")
    version = _EventVersionProbe.model_validate(json.loads(lines[0])).event_schema_version
    if version not in SUPPORTED_EVENT_VERSIONS:
        raise JournalVersionError(
            path=resolved,
            file_version=version,
            supported_versions=SUPPORTED_EVENT_VERSIONS,
        )
    return tuple(_parse_record(json.loads(line)) for line in lines)
