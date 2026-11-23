#!/usr/bin/env python3
"""公开面可见性表的脚手架：从声明派生全部待确认的行（ai-view 票 05；spec §2.4）。

**只读**：不建局、不模拟、不写文件。它回答的问题是"哪些步骤需要一条可见性登记、各自的
默认值是什么、现在登记成了什么"——供人确认默认值 / 改例外，而不是让人从零枚举一百多条
技能。表本身住 :mod:`sgs.skills.visibility`；本工具不另存第二份数据。

用法：

    # 全部行（技能侧 + 卡牌侧）：owner / step / kind / 默认值 / 已登记值
    .venv/bin/python tools/dump_public_surface.py

    # 只列**还没登记**的行（给"新增了一条步骤"的人）
    .venv/bin/python tools/dump_public_surface.py --missing

    # 核对：漏登记 / 幽灵登记 / 同一步骤两种 kind 都判非零退出
    .venv/bin/python tools/dump_public_surface.py --check

    # 把多目标牌的 target 行按座位展开（N = 这一局人数；主公是其中一格）
    .venv/bin/python tools/dump_public_surface.py --players 5

**没有语义列**：输出只有步骤名、步骤类别与两个可见性值。默认值由
:func:`default_step_visibility` 按**步骤形状**推出，不读技能语义；``CLAIM`` 没有形状
可推（只有【蛊惑】的声明步），必须人工登记。

**技能侧从哪派生**：``FLOWS`` 由 ``ActiveSkill.flow`` / ``SkillDef.flow`` / ``Hook.flow`` /
``Seam.flow`` 合并而来，故本工具同时覆盖主动技与被动钩子的流水线。``FlowBranch`` 的子步
按声明展开（不按答案），所以"两步分支都可能出现的步骤"都在表里。**手写段机的答案不是
``FlowStep``**，不在本工具的派生里（边界见 :data:`STEP_VISIBILITY` 的 docstring）。

**卡牌侧**（用户 2026-11-23 裁定的追加要求）：卡牌不是技能、没有 ``SkillFlow``，但
"拆的区选择步"与"多目标牌的逐目标效果"同样是公开面的边界，故从 ``CARD_BEHAVIORS``
派生 ``card:<牌名>`` 行：多目标牌给出 ``target``（一行**模式**，``--players N`` 时展开成
N 行），**区选**原语给出 ``pick``（登记表 = :data:`CARD_ZONE_PICK_OPS`，新增区选原语要在
那里登记）。其它卡牌提问（火攻的亮牌 / 弃牌、借刀的响应…）不在这张表——本表只覆盖
用户追加要求点名的两类。**默认可见性不参与运行期**：表里每一行都是显式登记的。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sgs.effects import CARD_BEHAVIORS, CallOp, TargetCount
from sgs.skills.flow import Arrange, PickCards, SkillFlow, flow_steps
from sgs.skills.preactivation import FLOWS
from sgs.skills.visibility import (
    CARD_RESOLUTION_VISIBILITY,
    STEP_VISIBILITY,
    StepVisibility,
)
from sgs.timing import Timing

__all__ = [
    "CARD_PER_TARGET_KIND",
    "CARD_ZONE_PICK_OPS",
    "Row",
    "card_rows",
    "card_step_keys",
    "default_step_visibility",
    "ghost_steps",
    "kind_conflicts",
    "main",
    "missing_rows",
    "missing_steps",
    "problems",
    "render",
    "skill_rows",
    "skill_step_keys",
    "step_kind",
]


CARD_PER_TARGET_KIND = "PerTarget"
"""卡牌侧"逐目标效果"行的 kind（``target`` 一行模式；``--players N`` 展开成 N 行）。"""

CARD_ZONE_PICK_OPS: Mapping[str, str] = {
    "resolve_dismantlement": "pick",
    "resolve_snatch": "pick",
}
"""**区选**原语 → 步骤名（卡牌侧派生用的**封闭登记表**）。

与 ``effects/ops.py`` 的 ``OPS`` 键同源。本表只覆盖'拆 / 顺'这一类**区域选牌**；新增
区选原语时在这里登记（同 ``ENGINE_LEVEL_FACT_CLASSES`` 的纪律）。火攻的亮牌 / 弃牌、
借刀的响应等其它卡牌提问不在本表（票 06 另行处理）。
"""


@dataclass(frozen=True, slots=True)
class Row:
    """派生表的一行：``(owner, 步骤名, kind, 默认可见性, 已登记可见性)``。"""

    owner: str
    """技能 id，或 ``card:<牌名>``（卡牌解析步骤）。"""

    step: str
    kind: str
    default: StepVisibility
    registered: StepVisibility | None


def step_kind(step: Any) -> str:
    """步骤的**类别名**（脚手架输出的一列；不参与运行期判据）。"""
    return type(step).__name__


def default_step_visibility(step: Any) -> StepVisibility:
    """脚手架提议的**默认可见性**（给人确认默认值 / 改例外；**不参与运行期**）。

    判据只看**步骤的形状**，不看技能语义：

    - ``Arrange``：只在排列者眼前发生 ⇒ ``HIDDEN``；
    - ``PickCards``：挑的是**隐藏面**（``reveal_hand=False``）或「武将牌上的牌」区
      ⇒ 保守取 ``HIDDEN``；
    - 其余（选座位 / 选选项 / 提供牌 / 其余选牌）：``PUBLIC``。

    于是"默认值"是一份保守的起点，表里每一行仍是人工确认后的显式值；``CLAIM`` 没有
    形状可推（只有【蛊惑】的声明步这一处），必须人工登记。
    """
    if isinstance(step, Arrange):
        return StepVisibility.HIDDEN
    if isinstance(step, PickCards):
        if not step.reveal_hand or step.hero_zone is not None:
            return StepVisibility.HIDDEN
    return StepVisibility.PUBLIC


def missing_steps(
    expected: Mapping[str, Collection[str]],
    registered: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """``expected`` 里有、``registered`` 里没有的步骤（**第一道断言**；纯函数）。"""
    return tuple(
        f"{owner}: 步骤 {step} 未登记可见性"
        for owner in sorted(expected)
        for step in sorted(set(expected[owner]) - set(registered.get(owner, ())))
    )


def ghost_steps(
    expected: Mapping[str, Collection[str]],
    registered: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """``registered`` 里有、``expected`` 里没有的步骤（**第二道断言**；纯函数）。"""
    return tuple(
        f"{owner}: 登记的步骤 {step} 不在派生集合里"
        for owner in sorted(registered)
        for step in sorted(set(registered[owner]) - set(expected.get(owner, ())))
    )


def _flow_keys(
    flows: Mapping[str, Sequence[tuple[Timing | None, SkillFlow]]],
) -> dict[str, set[str]]:
    """``skill_id -> 该技能所有流水线的叶子步骤名``（无步骤的技能不出现）。"""
    keys: dict[str, set[str]] = {}
    for skill_id, entries in flows.items():
        for _timing, flow in entries:
            keys.setdefault(skill_id, set()).update(step.key for step in flow_steps(flow))
    return {skill_id: names for skill_id, names in keys.items() if names}


def skill_step_keys(
    flows: Mapping[str, Sequence[tuple[Timing | None, SkillFlow]]] | None = None,
) -> dict[str, set[str]]:
    """技能侧**派生集合**（缺省读真实的 ``FLOWS``，可注入验派生）。"""
    return _flow_keys(FLOWS if flows is None else flows)


def card_step_keys(
    behaviors: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """卡牌侧**派生集合**：``card:<牌名> -> 步骤名 -> kind``（可注入验派生）。"""
    resolved = CARD_BEHAVIORS if behaviors is None else behaviors
    out: dict[str, dict[str, str]] = {}
    for type_id, behavior in resolved.items():
        steps: dict[str, str] = {}
        rule = behavior.target_rule
        multi_target = (
            rule.count is not TargetCount.RATED
            or (rule.max_targets or 0) > 1
            or rule.rated_targets > 1
        )
        if multi_target:
            steps["target"] = CARD_PER_TARGET_KIND
        for effect in behavior.effects:
            if isinstance(effect, CallOp) and effect.op in CARD_ZONE_PICK_OPS:
                steps[CARD_ZONE_PICK_OPS[effect.op]] = PickCards.__name__
        if steps:
            out[f"card:{type_id}"] = steps
    return out


def skill_rows(
    flows: Mapping[str, Sequence[tuple[Timing | None, SkillFlow]]] | None = None,
) -> tuple[Row, ...]:
    """技能侧的派生行（缺省读真实的 ``FLOWS`` / ``STEP_VISIBILITY``；可注入验派生）。"""
    resolved_flows = FLOWS if flows is None else flows

    steps: dict[tuple[str, str], object] = {}
    for skill_id in sorted(resolved_flows):
        for _timing, flow in resolved_flows[skill_id]:
            for step in flow_steps(flow):
                key = (skill_id, step.key)
                previous = steps.get(key)
                if previous is not None and step_kind(previous) != step_kind(step):
                    raise ValueError(
                        f"{skill_id} 的步骤 {step.key} 在两条流水线里类型不同："
                        f"{step_kind(previous)} / {step_kind(step)}（共用一把键的步骤必须同形）"
                    )
                steps[key] = step

    rows: list[Row] = []
    for (skill_id, step_name), step in sorted(steps.items()):
        rows.append(
            Row(
                owner=skill_id,
                step=step_name,
                kind=step_kind(step),
                default=default_step_visibility(step),
                registered=STEP_VISIBILITY.get(skill_id, {}).get(step_name),
            )
        )
    return tuple(rows)


def card_rows(seats: int | None = None) -> tuple[Row, ...]:
    """卡牌侧的派生行；``seats=N`` 时把多目标牌的 ``target`` 展开成 N 行（每座一格）。

    展开后 ``step`` 的写法是 ``target[<座位号>]``，``registered`` 仍取表里的
    ``target`` 那一行——**一行模式、N 行视图**：这一局哪几格是目标由目标规则决定，
    本表只管"这些格里的效果公开"。
    """
    rows: list[Row] = []
    for owner, steps in sorted(card_step_keys().items()):
        for step, kind in sorted(steps.items()):
            registered = CARD_RESOLUTION_VISIBILITY.get(owner, {}).get(step)
            if step == "target" and seats is not None:
                rows.extend(
                    Row(
                        owner=owner,
                        step=f"target[{seat}]",
                        kind=kind,
                        default=StepVisibility.PUBLIC,
                        registered=registered,
                    )
                    for seat in range(seats)
                )
                continue
            rows.append(
                Row(
                    owner=owner,
                    step=step,
                    kind=kind,
                    default=StepVisibility.PUBLIC,
                    registered=registered,
                )
            )
    return tuple(rows)


def missing_rows(rows: tuple[Row, ...]) -> tuple[Row, ...]:
    """还没登记可见性的行（``--missing`` 用）。"""
    return tuple(row for row in rows if row.registered is None)


def kind_conflicts(
    flows: Mapping[str, Sequence[tuple[Timing | None, SkillFlow]]] | None = None,
) -> tuple[str, ...]:
    """同一步骤名在一技能的两条流水线里类型不同 ⇒ 一条问题（纯函数）。"""
    try:
        skill_rows(flows)
    except ValueError as error:
        return (str(error),)
    return ()


def problems() -> tuple[str, ...]:
    """两道断言 + 卡牌侧的同一份纪律 + kind 冲突；空元组 = 全绿。"""
    expected = skill_step_keys()
    derived_cards = {
        owner: set(steps) for owner, steps in card_step_keys().items()
    }
    return (
        *missing_steps(expected, STEP_VISIBILITY),
        *ghost_steps(expected, STEP_VISIBILITY),
        *missing_steps(derived_cards, CARD_RESOLUTION_VISIBILITY),
        *ghost_steps(derived_cards, CARD_RESOLUTION_VISIBILITY),
        *kind_conflicts(),
    )


def render(rows: tuple[Row, ...] | None = None) -> str:
    """TSV：``owner step kind default registered``（``-`` = 未登记）。"""
    if rows is None:
        rows = skill_rows() + card_rows()
    lines = ["owner\tstep\tkind\tdefault\tregistered"]
    for row in rows:
        registered = "-" if row.registered is None else row.registered.value
        lines.append(
            f"{row.owner}\t{row.step}\t{row.kind}\t{row.default.value}\t{registered}"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="列出公开面可见性表的全部派生行（只读；不写文件）。"
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help="只列还没登记的行；有未登记行时退出码为 1。",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="核对漏登记 / 幽灵登记 / 同一步骤两种 kind；有任何问题退出码为 1。",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=None,
        metavar="N",
        help="把多目标牌的 target 行按 N 个座位展开（主公是其中一格）。",
    )
    args = parser.parse_args(argv)

    if args.check:
        found = problems()
        if found:
            for problem in found:
                print(problem, file=sys.stderr)
            return 1
        print("PASS: 每个步骤都登记了可见性，且没有幽灵登记")
        return 0

    rows = skill_rows() + card_rows(seats=args.players)
    if args.missing:
        rows = missing_rows(rows)
        sys.stdout.write(render(rows))
        return 1 if rows else 0
    sys.stdout.write(render(rows))
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(main())
