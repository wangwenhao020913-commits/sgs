"""漏动作口径（票 13；spec §2.11）：``pass_with_options`` 与 ``unfilled_composite``。

**只实现一次**：本模块是唯一口径，``quick`` / ``promote`` / ``seal`` 与票 15 的
``ai_eval live`` 都从这里数。活数据有两种粒度，用同一个 :class:`DecisionFact` 归一口：

- :func:`fact_from_query`——手上有**真实询问对象**时（live 从 journal 反序列化出的
  ``Query``、夹具、批量里包的参与者）。``legal_actions`` / 复合清单都能逐字段读；
- :func:`fact_from_batch_decision`——手上只有 ``sgs_batch`` 的决策侧车投影
  （``kind`` / ``actor`` / ``legal_count`` / ``action``）时。缺的字段填 ``None``，
  :func:`count_misses` **把它们从分母里剔除**，不猜。

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
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "COMPOSITE_OFFER_FIELDS",
    "DecisionFact",
    "MissCounts",
    "count_misses",
    "fact_from_batch_decision",
    "fact_from_query",
    "has_composite_offer",
    "is_composite_answer",
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
class MissCounts:
    """两个廉价指标的分子 / 分母；比率在分母为 0 时返回 ``None``（未测 ≠ 0）。"""

    pass_with_options: int
    pass_denominator: int
    unfilled_composite: int
    composite_denominator: int

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
    """按 §2.11 数两个指标；未知字段（``None``）整条剔除，不猜。"""
    pass_misses = 0
    pass_denominator = 0
    composite_misses = 0
    composite_denominator = 0
    for fact in facts:
        if fact.non_pass_legal is True:
            pass_denominator += 1
            if fact.answered_pass:
                pass_misses += 1
        if fact.composite_offered is True:
            composite_denominator += 1
            if not fact.composite_filled:
                composite_misses += 1
    return MissCounts(
        pass_with_options=pass_misses,
        pass_denominator=pass_denominator,
        unfilled_composite=composite_misses,
        composite_denominator=composite_denominator,
    )


def has_composite_offer(query: Any) -> bool:
    """该询问是否下发了任一份复合清单（预发动 / 转化 / 借刀附带指定 / 被动预发动）。"""
    return any(getattr(query, name, None) for name in COMPOSITE_OFFER_FIELDS)


def is_composite_answer(action: Any) -> bool:
    """应答是否**填了**复合动作：预发动载荷、借刀附带指定、或转化来源。"""
    kind = getattr(action, "kind", None)
    if kind == "ActivateSkill":
        return bool(getattr(action, "picks", ()) or getattr(action, "choices", ()))
    if kind in {"UseCard", "PlayCard"}:
        if getattr(action, "designations", ()):
            return True
        card = getattr(action, "card", None)
        return bool(getattr(card, "virtual_sources", ()))
    return False


def fact_from_query(query: Any, action: Any) -> DecisionFact:
    """从**真实询问对象** + 应答造一条事实（live / 夹具 / 批量参与者都用这一条）。"""
    return DecisionFact(
        kind=query.kind,
        answered_pass=action.kind == "Pass",
        non_pass_legal=any(
            candidate.kind != "Pass" for candidate in query.legal_actions
        ),
        composite_offered=has_composite_offer(query),
        composite_filled=is_composite_answer(action),
    )


def _is_composite_payload(payload: Mapping[str, Any]) -> bool:
    kind = payload.get("kind")
    if kind == "ActivateSkill":
        return bool(payload.get("picks") or payload.get("choices"))
    if kind in {"UseCard", "PlayCard"}:
        if payload.get("designations"):
            return True
        card = payload.get("card")
        if isinstance(card, Mapping):
            return bool(card.get("virtual_sources"))
    return False


def fact_from_batch_decision(decision: Mapping[str, Any]) -> DecisionFact:
    """从 ``sgs_batch`` 的决策投影造一条事实（缺的字段填 ``None``）。

    - ``non_pass_legal``：合法候选多于一个、或应答本身不是 Pass ⇒ 是（``legal_actions``
      里恰有一条 Pass，故"多于一个"必然意味着另有一条非 Pass）；
    - ``composite_offered``：侧车不带清单 ⇒ ``None``（批量档的 ``unfilled_composite``
      因此是"未测"而不是 0）。
    """
    action = decision.get("action") or {}
    if not isinstance(action, Mapping):
        action = {}
    kind = str(decision.get("kind") or "")
    action_kind = action.get("kind")
    answered_pass = action_kind == "Pass"
    legal_count = int(decision.get("legal_count") or 0)
    return DecisionFact(
        kind=kind,
        answered_pass=answered_pass,
        non_pass_legal=bool(legal_count > 1 or not answered_pass),
        composite_offered=None,
        composite_filled=_is_composite_payload(action),
    )
