"""漏动作口径（票 13；spec §2.11 / §5.4）：``pass_with_options`` 与 ``unfilled_composite``。

**只实现一次**：本模块是唯一口径，``quick`` / ``promote`` / ``seal`` 与票 15 的
``ai_eval live`` 都从这里数。活数据有两种粒度，用同一个 :class:`DecisionFact` 归一口：

- :func:`fact_from_query`——手上有**真实询问对象**时（引擎产出的 ``Query``、票 15 的夹具）。
  ``legal_actions`` 与复合清单都能逐字段读。注意真实 **journal 读出来的清单是空的**：
  四份清单在记录流里是 ``exclude`` 的（``sgs.state.protocol``），所以 live 要覆盖复合清单
  这一支，只能用**引擎产出的询问**（夹具里重放一局）——批量档走的是下面那条投影路径；
- :func:`fact_from_batch_decision`——手上有 ``sgs_batch`` 的决策侧车投影
  （``kind`` / ``actor`` / ``legal_count`` / ``action``）时。参与者层不交询问对象，故缺的
  字段填 ``None``，:func:`count_misses` **把它们从分母里剔除**，不猜。

两个口径（§2.11 / §5.4 的**字面**读法，live 与批量共用这一条）：

- ``pass_with_options``：分母 = **含非 Pass 选项的 AI 询问**（``legal_actions`` 里至少一条
  不是 ``Pass``）；分子 = 其中应答恰为 ``Pass`` 的次数。注意两点：出牌阶段 / 弃牌阶段**在
  分母里**（它们的候选都是非 Pass 的），而 AI 的 ``EndPhase`` / ``DiscardCards`` 应答不算
  "pass"；只有"只剩 Pass 可答"的窗口（无牌可响应的响应窗）不在分母。§5.3 的例子
  ``pass=4/121(3.3%)`` 就是这条读法（若把分母再收紧成"Pass 合法且另有选项"，会只剩几十
  条，与 121 对不上）。
- ``unfilled_composite``：分母 = 询问下发了预发动 / 转化 / 借刀附带指定清单；分子 = 应答
  **没填**复合动作的次数。"填了"= ``ActivateSkill`` 带 ``picks`` / ``choices``、``UseCard``
  带 ``designations``、或应答牌带 ``virtual_sources``（转化）。

两个指标都**按询问类型分**（:attr:`MissCounts.by_kind`）——票 15 要按类型报账，总表只是
加权和（spec §2.11）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "COMPOSITE_OFFER_FIELDS",
    "DecisionFact",
    "KindMisses",
    "MissCounts",
    "count_misses",
    "fact_from_batch_decision",
    "fact_from_query",
    "has_composite_offer",
    "is_composite_answer",
    "merge_misses",
]

COMPOSITE_OFFER_FIELDS: tuple[str, ...] = (
    "preactivations",
    "transform_preactivations",
    "transform_actions",
    "card_preactivations",
    "designation_actions",
    "preactivation",
)
"""询问上承载复合清单的字段名（``PlayPhaseQuery`` 四份 + 基类转化 + 被动预发动）。

四份清单在记录流里是 ``exclude`` 的（`sgs.state.protocol`），所以**真实 journal 读出来
是空的**；批量决策侧车更不带它们。live 的夹具可以带上，批量档只能报 ``None``。
"""


@dataclass(frozen=True)
class DecisionFact:
    """一次询问 / 应答的漏动作事实；``None`` = 该粒度看不到，不参与分母。"""

    kind: str
    answered_pass: bool
    non_pass_legal: bool | None = None
    composite_offered: bool | None = None
    composite_filled: bool = False


@dataclass(frozen=True)
class KindMisses:
    """按询问类型拆开的一格（spec §2.11 的"按询问类型分"）。"""

    pass_with_options: int = 0
    pass_denominator: int = 0
    unfilled_composite: int = 0
    composite_denominator: int = 0


@dataclass(frozen=True)
class MissCounts:
    """两个廉价指标的分子 / 分母；比率在分母为 0 时返回 ``None``（未测 ≠ 0）。"""

    pass_with_options: int
    pass_denominator: int
    unfilled_composite: int
    composite_denominator: int
    by_kind: dict[str, KindMisses] = field(default_factory=dict)
    """按询问类型拆开的总表；不进结果 JSON（§2.8 的 ``miss_rate`` 只有两个比率）。"""

    @property
    def pass_rate(self) -> float | None:
        if not self.pass_denominator:
            return None
        return round(self.pass_with_options / self.pass_denominator, 4)

    @property
    def composite_rate(self) -> float | None:
        if not self.composite_denominator:
            return None
        return round(self.unfilled_composite / self.composite_denominator, 4)

    def to_dict(self) -> dict[str, float | None]:
        """§2.8 的 ``miss_rate``：测不到的口径写 ``null``，不写 0.0。"""
        return {
            "pass_with_options": self.pass_rate,
            "unfilled_composite": self.composite_rate,
        }


def count_misses(facts: Iterable[DecisionFact]) -> MissCounts:
    """按 §2.11 数两个指标（总数 + 分询问类型）；未知字段（``None``）整条剔除，不猜。"""
    totals = [0, 0, 0, 0]
    by_kind: dict[str, list[int]] = {}
    for fact in facts:
        bucket = by_kind.setdefault(fact.kind, [0, 0, 0, 0])
        if fact.non_pass_legal is True:
            bucket[1] += 1
            totals[1] += 1
            if fact.answered_pass:
                bucket[0] += 1
                totals[0] += 1
        if fact.composite_offered is True:
            bucket[3] += 1
            totals[3] += 1
            if not fact.composite_filled:
                bucket[2] += 1
                totals[2] += 1
    return MissCounts(
        *totals,
        by_kind={kind: KindMisses(*counts) for kind, counts in sorted(by_kind.items())},
    )


def merge_misses(left: MissCounts, right: MissCounts) -> MissCounts:
    """合并两块（批量分块跑时每块折一次）；``by_kind`` 逐类型相加。"""
    merged = dict(left.by_kind)
    for kind, counts in right.by_kind.items():
        previous = merged.get(kind, KindMisses())
        merged[kind] = KindMisses(
            pass_with_options=previous.pass_with_options + counts.pass_with_options,
            pass_denominator=previous.pass_denominator + counts.pass_denominator,
            unfilled_composite=previous.unfilled_composite + counts.unfilled_composite,
            composite_denominator=previous.composite_denominator
            + counts.composite_denominator,
        )
    return MissCounts(
        pass_with_options=left.pass_with_options + right.pass_with_options,
        pass_denominator=left.pass_denominator + right.pass_denominator,
        unfilled_composite=left.unfilled_composite + right.unfilled_composite,
        composite_denominator=left.composite_denominator
        + right.composite_denominator,
        by_kind=dict(sorted(merged.items())),
    )


def has_composite_offer(query: Any) -> bool:
    """该询问是否下发了任一份复合清单（预发动 / 转化 / 借刀附带指定 / 被动预发动）。"""
    return any(getattr(query, name, None) for name in COMPOSITE_OFFER_FIELDS)


def _filled(
    kind: Any,
    picks: Any,
    choices: Any,
    designations: Any,
    virtual_sources: Any,
) -> bool:
    """“应答填了复合动作”的**唯一**判据（对象 / 投影两种粒度共用一份）。"""
    if kind == "ActivateSkill":
        return bool(picks or choices)
    if kind in {"UseCard", "PlayCard"}:
        return bool(designations or virtual_sources)
    return False


def is_composite_answer(action: Any) -> bool:
    """应答是否**填了**复合动作：预发动载荷、借刀附带指定、或转化来源。"""
    card = getattr(action, "card", None)
    return _filled(
        getattr(action, "kind", None),
        getattr(action, "picks", ()),
        getattr(action, "choices", ()),
        getattr(action, "designations", ()),
        getattr(card, "virtual_sources", ()),
    )


def fact_from_query(query: Any, action: Any) -> DecisionFact:
    """从**真实询问对象** + 应答造一条事实（live / 夹具用这一条）。"""
    return DecisionFact(
        kind=query.kind,
        answered_pass=action.kind == "Pass",
        non_pass_legal=any(candidate.kind != "Pass" for candidate in query.legal_actions),
        composite_offered=has_composite_offer(query),
        composite_filled=is_composite_answer(action),
    )


def _is_composite_payload(payload: Mapping[str, Any]) -> bool:
    card = payload.get("card")
    return _filled(
        payload.get("kind"),
        payload.get("picks"),
        payload.get("choices"),
        payload.get("designations"),
        card.get("virtual_sources") if isinstance(card, Mapping) else (),
    )


def fact_from_batch_decision(decision: Mapping[str, Any]) -> DecisionFact:
    """从 ``sgs_batch`` 的决策投影造一条事实（缺的字段填 ``None``）。

    - ``non_pass_legal``：合法候选多于一个、或应答本身不是 Pass ⇒ 是（``legal_actions``
      里恰有一条 Pass，故“多于一个”必然意味着另有一条非 Pass）；
    - ``composite_offered``：侧车不带清单 ⇒ ``None``（批量档的 ``unfilled_composite``
      因此是“未测”而不是 0）。
    """
    action = decision.get("action") or {}
    if not isinstance(action, Mapping):
        action = {}
    answered_pass = action.get("kind") == "Pass"
    legal_count = int(decision.get("legal_count") or 0)
    return DecisionFact(
        kind=str(decision.get("kind") or ""),
        answered_pass=answered_pass,
        non_pass_legal=bool(legal_count > 1 or not answered_pass),
        composite_offered=None,
        composite_filled=_is_composite_payload(action),
    )
