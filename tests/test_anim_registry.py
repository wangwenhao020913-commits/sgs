"""U4-01 / spec D3：飞行分类表必须对引擎的每个 ``reason × to_zone`` 给出裁定。

失败模式（本文件的判据）：**引擎多出一个 reason / 一个去向区，而前端分类表没登记**。
形状照 ``tests/test_m5_no_leak.py::test_every_delta_type_has_a_no_leak_case`` 的"新类型
必须登记"：登记表不是测试里另抄一份，而是从产品文件 ``tools/sgs_web/static/anim.js`` 的
``const ANIM_FLIGHT_RULES = [...]`` 字面量解析出来；期望面从**引擎真源**来：

- reason 清单 = :class:`sgs.state.models.MoveReason`（新增枚举值 ⇒ 这里变红）；
- 去向区清单 = :class:`sgs.state.models.Zone`（新增区 ⇒ 这里变红）；
- 真记录里的 ``(reason, to_zone, from_zone)`` 组合 = ``tests/replays/*.jsonl`` 的 47 条
  标准录像（引擎唯一写出的组合面）。每条都必须解析到一条**非空**裁定——D3 的唯一例外
  是 ``processing → discard``（使用 / 判定收尾不飞，见票 01 的 D3 最后一行）。

匹配规则与产品同一条：按 ``anim.js`` 里的**文件顺序**取第一条命中的规则；``reason: "*"``
是"任意 reason"的通配（只用于 ``processing → discard`` 这一条不放行的裁定）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from sgs.state.models import MoveReason, Zone

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "tools" / "sgs_web" / "static"
REPLAYS_DIR = REPO_ROOT / "tests" / "replays"

_RULES_BLOCK = re.compile(r"const ANIM_FLIGHT_RULES =\s*\[(?P<body>.*?)\n\];", re.S)
_RULE = re.compile(
    r"\{\s*reason:\s*\"(?P<reason>[A-Z_]+|\*)\"\s*,\s*to:\s*\"(?P<to>[a-z_]+)\"\s*"
    r"(?:,\s*from:\s*\"(?P<from>[a-z_]+)\"\s*)?,\s*kind:\s*"
    r"(?P<kind>null|\"[a-z_]+\")\s*\}",
    re.M,
)


@dataclass(frozen=True)
class Rule:
    reason: str
    to: str
    from_zone: str | None
    kind: str | None


def _flight_rules() -> list[Rule]:
    """``anim.js`` 的分类表（按文件顺序；匹配"第一条命中"就用这个顺序）。"""
    path = STATIC_DIR / "anim.js"
    assert path.is_file(), "缺少产品文件 anim.js（U4-01 把原型提升成产品文件）"
    block = _RULES_BLOCK.search(path.read_text(encoding="utf-8"))
    assert block is not None, (
        "anim.js 里找不到 `const ANIM_FLIGHT_RULES = [ ... ];` 字面量；"
        "D3 的登记表必须是可机械读到的有序规则表"
    )
    rules: list[Rule] = []
    for match in _RULE.finditer(block.group("body")):
        kind_token = match.group("kind")
        kind = None if kind_token == "null" else kind_token.strip('"')
        rules.append(
            Rule(
                reason=match.group("reason"),
                to=match.group("to"),
                from_zone=match.group("from"),
                kind=kind,
            )
        )
    assert rules, "anim.js 的 ANIM_FLIGHT_RULES 是空的（分类表没落地）"
    return rules


def _resolve(
    rules: list[Rule], reason: str, to_zone: str, from_zone: str
) -> Rule | None:
    """产品同一口径：首条命中（``reason`` 通配 ``*``；``from`` 缺省 = 任意来源区）。"""
    for rule in rules:
        if rule.reason not in (reason, "*"):
            continue
        if rule.to != to_zone:
            continue
        if rule.from_zone is not None and rule.from_zone != from_zone:
            continue
        return rule
    return None


def _recorded_moves() -> set[tuple[str, str, str]]:
    """47 条标准录像里真实写出的 ``(reason, to_zone, from_zone)`` 组合。"""
    combos: set[tuple[str, str, str]] = set()
    for path in sorted(REPLAYS_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record.get("delta_type") != "card_move":
                continue
            combos.add(
                (
                    record["reason"],
                    record["to_zone"]["zone"],
                    record["from_zone"]["zone"],
                )
            )
    return combos


def test_every_move_reason_has_a_registered_flight_ruling() -> None:
    """新增一种 ``MoveReason`` 必须先在分类表里登记（通配 ``*`` 不算登记 reason）。"""
    rules = _flight_rules()
    registered = {rule.reason for rule in rules}
    missing = sorted({reason.value for reason in MoveReason} - registered)
    assert not missing, (
        "sgs.state.models.MoveReason 新增了未登记的移动原因："
        f"{missing}。请在 anim.js 的 ANIM_FLIGHT_RULES 里给每个去向区补一条裁定"
    )


def test_every_zone_has_a_registered_destination_ruling() -> None:
    """新增一个 ``Zone`` 必须至少有一种"落到这个区"的裁定（去向锚点 / 飞或不飞）。"""
    rules = _flight_rules()
    registered = {rule.to for rule in rules}
    missing = sorted({zone.value for zone in Zone} - registered)
    assert not missing, (
        "sgs.state.models.Zone 新增了未登记的目的地区："
        f"{missing}。请在 anim.js 的 ANIM_FLIGHT_RULES 里登记该去向区（可写 kind: null）"
    )


def test_only_processing_to_discard_is_registered_without_a_flight() -> None:
    """D3 只有一条"不飞"：处理区 → 弃牌堆（使用 / 判定收尾）。

    其它任何组合登记成 ``kind: null`` 都是在悄悄吞掉一条牌移动；要停飞必须改 D3 并说明。
    """
    no_flight = [rule for rule in _flight_rules() if rule.kind is None]
    assert [(rule.reason, rule.to, rule.from_zone) for rule in no_flight] == [
        ("*", "discard", "processing")
    ], f"只有 processing → discard 允许不飞，实际的不飞裁定：{no_flight}"


def test_every_recorded_move_resolves_to_a_flight() -> None:
    """引擎真记录里的每个组合都要解析到裁定；除 processing → discard 外都必须飞。"""
    rules = _flight_rules()
    combos = _recorded_moves()
    assert len(combos) >= 25, (
        "录像扫描面异常（只找到 "
        f"{len(combos)} 种组合）：判据样本不足以代表引擎会写出的组合面"
    )
    unregistered: list[str] = []
    swallowed: list[str] = []
    wrong_exemption: list[str] = []
    for reason, to_zone, from_zone in sorted(combos):
        rule = _resolve(rules, reason, to_zone, from_zone)
        label = f"{reason} {from_zone}→{to_zone}"
        if rule is None:
            unregistered.append(label)
            continue
        exempt = to_zone == "discard" and from_zone == "processing"
        if exempt and rule.kind is not None:
            wrong_exemption.append(label)
        elif not exempt and rule.kind is None:
            swallowed.append(label)
    assert not unregistered, (
        "引擎会写出、但 anim.js 没有裁定的组合（未登记 = 界面上没有飞行）：\n"
        + "\n".join(unregistered)
    )
    assert not swallowed, (
        "D3 只让 processing → discard 不飞，这些真移动却被登记成了不飞：\n"
        + "\n".join(swallowed)
    )
    assert not wrong_exemption, (
        "处理区 → 弃牌堆必须不飞（D3 最后一行），这些组合登记成了会飞：\n"
        + "\n".join(wrong_exemption)
    )


def test_a_new_reason_or_zone_is_not_accidentally_covered_by_the_wildcard() -> None:
    """守卫的反面：没登记的新 reason / 新去向区解析为 None（= 界面会标"未登记"）。

    只按 ``(reason, to_zone)`` 判未登记：登记过的组合换一个来源区仍算已登记（``from``
    只是细化，不是第二把钥匙）。
    """
    rules = _flight_rules()
    assert _resolve(rules, "TELEPORT", "hand", "deck") is None
    assert _resolve(rules, "DRAW", "moon", "deck") is None
    assert _resolve(rules, "DRAW", "hand", "moon") is not None, (
        "``from`` 只是 (reason, to_zone) 的细化：已登记的组合换来源区仍然有裁定"
    )
