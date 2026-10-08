"""询问与行动的协议 schema，以及参与者视角投影（票 05 / 票 07）。

四类询问（``PLAY_PHASE`` / ``DODGE`` / ``PEACH`` / ``DISCARD``）各一个 frozen 模型，
以 ``kind`` 作判别字段；四个行动构造子（``UseCard`` / ``Pass`` / ``EndPhase`` /
``DiscardCards``）同构。候选行动**内联**在询问里，参与者只从中挑一个。

``answers_seq`` 是行动自带的字段：候选在**构造询问时**生成，那时询问的 ``seq`` 已定，
于是"被提供的"与"被接受的"共享同一个陈旧输入校验键。

外来应答经 :func:`parse_action` 解析：这是行动 schema 的**唯一入口**，schema 不认的
输入（未知 ``kind`` / 缺字段 / 多余字段）抛 pydantic ``ValidationError``，由引擎归类为
``Malformed``（票 07 §5）。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from sgs.timing import Timing

from .models import CardRef, Phase, _FrozenModel

__all__ = [
    "Action",
    "DiscardCards",
    "DiscardQuery",
    "DodgeQuery",
    "EndPhase",
    "Pass",
    "PeachQuery",
    "PlayPhaseQuery",
    "PlayerPublic",
    "PlayerView",
    "Query",
    "QueryKind",
    "UseCard",
    "parse_action",
]


class QueryKind(str, Enum):
    """四类询问的标识；候选生成器按它分派（票 05 §1）。"""

    PLAY_PHASE = "PLAY_PHASE"
    DODGE = "DODGE"
    PEACH = "PEACH"
    DISCARD = "DISCARD"


class PlayerPublic(_FrozenModel):
    """公开区里的一个座位：对手只暴露手牌**数**，不暴露身份。"""

    seat: int
    hp: int
    hp_max: int
    hand_count: int
    alive: bool


class PlayerView(_FrozenModel):
    """参与者视角：只含自己的手牌与全部公开信息（票 05 §5）。

    牌堆只给**剩余数量**、不给顺序；``discard`` 按置入序（先入在前）；
    ``processing``（处理区）是公开区，按置入序（票 07 §3）。
    """

    seat: int
    players: tuple[PlayerPublic, ...]
    self_hand: tuple[CardRef, ...]
    deck_count: int
    discard: tuple[CardRef, ...]
    processing: tuple[CardRef, ...]
    turn_seat: int | None
    phase: Phase | None


# ---------------------------------------------------------------------------
# 行动（四个构造子）
# ---------------------------------------------------------------------------


class _ActionBase(_FrozenModel):
    answers_seq: int
    """本行动所应答的询问的 ``seq``；陈旧输入即失配（票 05 §3）。"""


class UseCard(_ActionBase):
    kind: Literal["UseCard"] = "UseCard"
    card: CardRef
    targets: tuple[int, ...] = ()


class Pass(_ActionBase):
    kind: Literal["Pass"] = "Pass"


class EndPhase(_ActionBase):
    kind: Literal["EndPhase"] = "EndPhase"


class DiscardCards(_ActionBase):
    kind: Literal["DiscardCards"] = "DiscardCards"
    cards: tuple[CardRef, ...]


Action = Annotated[UseCard | Pass | EndPhase | DiscardCards, Field(discriminator="kind")]

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


def parse_action(raw: object) -> Action:
    """把**外来应答**解析成 :data:`Action`：唯一入口，``extra="forbid"`` 即拒多余字段。

    schema 不认的输入（未知 ``kind`` / 缺字段 / 类型不符 / 根本不是对象）抛 pydantic
    ``ValidationError``——引擎据此记 ``MalformedAction`` 并抛 ``IllegalActionError``
    （票 07 §5 的 ``Malformed`` 一类）。已经是 ``Action`` 实例的输入原样返回，
    故参与者照常可以直接返回协议对象。
    """
    return _ACTION_ADAPTER.validate_python(raw)


# ---------------------------------------------------------------------------
# 询问（四类）
# ---------------------------------------------------------------------------


class _QueryBase(_FrozenModel):
    seq: int
    actor: int
    timing: Timing
    prompt: str
    legal_actions: tuple[Action, ...]
    view: PlayerView


class PlayPhaseQuery(_QueryBase):
    kind: Literal["PLAY_PHASE"] = "PLAY_PHASE"


class DodgeQuery(_QueryBase):
    kind: Literal["DODGE"] = "DODGE"


class PeachQuery(_QueryBase):
    kind: Literal["PEACH"] = "PEACH"
    dying: int


class DiscardQuery(_QueryBase):
    kind: Literal["DISCARD"] = "DISCARD"
    count: int
    """本阶段须弃置的张数（= 手牌数 − 当前体力值）。"""


Query = Annotated[
    PlayPhaseQuery | DodgeQuery | PeachQuery | DiscardQuery,
    Field(discriminator="kind"),
]
