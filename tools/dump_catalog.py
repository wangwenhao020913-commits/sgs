#!/usr/bin/env python3
"""技能目录盘点器：把 :data:`sgs.skills.CATALOG` 的**形状指纹**打出来（只读）。

新增一条技能时先跑它，回答两个问题：**「有没有同形的现成技能可抄」** 与 **「这条技能要用的
原语存在吗」**。用法（`docs/agents/skill-authoring/shapes.md` 是它的说明文档）：

    # 全部武将技能，一行一条：形状指纹 + 钩子 / 修饰器 / 接缝 / 步骤
    .venv/bin/python tools/dump_catalog.py

    # 只看某个武将 / 某条技能
    .venv/bin/python tools/dump_catalog.py --hero jie_sunshangxiang
    .venv/bin/python tools/dump_catalog.py --skill hero.jie_sunshangxiang.jieyin

    # 反查：哪些技能挂了这条时机 / 这个修饰点 / 这种步骤
    .venv/bin/python tools/dump_catalog.py --hook PHASE_DRAW
    .venv/bin/python tools/dump_catalog.py --modifier COUNT_LIMIT
    .venv/bin/python tools/dump_catalog.py --step PickOption

    # 枚举面：框架此刻支持哪些时机 / 接缝 / 修饰点 / 询问种类 / 区域
    .venv/bin/python tools/dump_catalog.py --timings --seams --points --queries --zones --steps

    # 机器可读（给审计测试与其它工具用）
    .venv/bin/python tools/dump_catalog.py --json

**只读**：不建局、不模拟、不写文件。`--all` 连装备技能一起列（缺省只列 `carrier == HERO`）。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SkillShape",
    "collect",
    "enumerate_surface",
    "format_row",
    "main",
]

# 步骤类型 → 形状指纹里用的短名（与 `shapes.md` 的表格逐字一致）。
_STEP_NAMES = {
    "PickCards": "cards",
    "PickSeats": "seats",
    "PickOption": "option",
    "PickProvide": "provide",
    "Arrange": "arrange",
}


@dataclass(frozen=True)
class SkillShape:
    """一条技能的形状指纹（**派生**，不新增状态）：给"有没有同形的技能"做反查用。

    ``kind`` 是这条技能**怎么进入游戏**的四档之一：``flow``（主动技，有步骤）、
    ``hook``（被动技，挂在时机上）、``modifier``（连续型，只改值）、``declare``（只有声明，
    例如纯转化技 / 纯放置类）。一条技能可以同时有多种（例如【武圣】= 转化声明 + 目标池修饰器），
    故四个字段并列而不是一个枚举。
    """

    skill_id: str
    carrier: str
    locked: bool
    skill_type: str
    preactivated: bool
    hooks: tuple[tuple[str, bool], ...] = ()
    """``(时机名, per_point)``。"""
    seams: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    flow_name: str | None = None
    zones: tuple[str, ...] = ()
    rejudge: bool = False
    rejudge_zones: tuple[str, ...] = ()
    places_on_hero_card: bool = False
    requirement: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kinds(self) -> tuple[str, ...]:
        """这条技能走哪几种机制（见类 docstring 的四档）。"""
        found: list[str] = []
        if self.steps:
            found.append("flow")
        if self.hooks:
            found.append("hook")
        if self.modifiers:
            found.append("modifier")
        if not found:
            found.append("declare")
        return tuple(found)

    def fingerprint(self) -> str:
        """一行形状指纹：``flow:seats>option>cards | hook:- | modifier:-``。"""
        parts: list[str] = []
        if self.steps:
            parts.append("flow:" + ">".join(self.steps))
        for timing, per_point in self.hooks:
            parts.append(f"hook:{timing}" + ("(逐点)" if per_point else ""))
        for seam in self.seams:
            parts.append(f"seam:{seam}")
        for point in self.modifiers:
            parts.append(f"modifier:{point}")
        if self.rejudge:
            parts.append("rejudge:" + "/".join(self.rejudge_zones or ("hand",)))
        if self.places_on_hero_card:
            parts.append("place:hero-card")
        if not parts:
            parts.append("declare")
        return " ".join(parts)


def _flow_of(skill_id: str) -> Any:
    """这条技能的 ``SkillFlow``（主动技住 ``ACTIVE_SKILLS``，被动技住钩子上）。"""
    from sgs.skills import active

    for declaration in active.ACTIVE_SKILLS:
        if declaration.skill_id == skill_id:
            return declaration.flow
    return None


def _step_name(step: Any) -> str:
    """步骤 → 指纹短名；``PickCards`` 的 ``hi`` 是"恰好 / 至多"的分界，值得带上。"""
    name = _STEP_NAMES[type(step).__name__]
    if name == "cards":
        lo, hi = getattr(step, "lo", 1), getattr(step, "hi", None)
        if lo == hi:
            return f"{name}(恰好{lo})"
        return f"{name}(至多)" if hi is None else f"{name}({lo}..{hi})"
    if name == "option":
        return "option(动态)" if callable(getattr(step, "options", None)) else name
    return name


def collect(*, include_equipment: bool = False) -> tuple[SkillShape, ...]:
    """扫 :data:`sgs.skills.CATALOG`，返回每条技能的形状指纹（按 id 排序）。"""
    from sgs.skills import CATALOG

    shapes: list[SkillShape] = []
    for skill_id, definition in sorted(CATALOG.items()):
        carrier = definition.carrier.name
        if not include_equipment and carrier != "HERO":
            continue
        flow = _flow_of(skill_id)
        shapes.append(
            SkillShape(
                skill_id=skill_id,
                carrier=carrier,
                locked=definition.locked,
                skill_type=definition.skill_type.name,
                preactivated=definition.preactivated,
                hooks=tuple(
                    (hook.timing.name, bool(getattr(hook, "per_point", False)))
                    for hook in definition.hooks
                ),
                seams=tuple(seam.kind.name for seam in definition.seams),
                modifiers=tuple(modifier.point.name for modifier in definition.modifiers),
                steps=tuple(_step_name(step) for step in flow.steps) if flow else (),
                flow_name=flow.name if flow else None,
                rejudge=definition.rejudge_capable,
                rejudge_zones=tuple(zone.name for zone in definition.rejudge_zones),
                places_on_hero_card=definition.places_on_hero_card,
                requirement=definition.requirement or "",
            )
        )
    return tuple(shapes)


def enumerate_surface() -> dict[str, tuple[str, ...]]:
    """框架此刻支持的**封闭集**（供 ``--timings`` 等枚举开关与审计测试共用）。

    这些都是代码里的枚举 / 类型，不手抄：``Timing`` / ``SeamKind`` / ``ModifierPoint`` /
    ``QueryKind`` / ``Zone`` / 步骤类型。审计测试用它做"文档有没有漏登记"的反向核对。
    """
    from sgs.effects import ModifierPoint
    from sgs.skills.catalog import SeamKind
    from sgs.skills.flow import Arrange, PickCards, PickOption, PickProvide, PickSeats
    from sgs.state import FrameKind, QueryKind, Zone
    from sgs.timing import Timing

    return {
        "timings": tuple(timing.name for timing in Timing),
        "seams": tuple(kind.name for kind in SeamKind),
        "points": tuple(point.name for point in ModifierPoint),
        "queries": tuple(kind.name for kind in QueryKind),
        "zones": tuple(zone.name for zone in Zone),
        "steps": tuple(
            _STEP_NAMES[t.__name__] for t in (PickCards, PickSeats, PickOption, PickProvide, Arrange)
        ),
        "frames": tuple(kind.name for kind in FrameKind),
    }


def format_row(shape: SkillShape) -> str:
    """一条技能的人读行（默认输出格式）。"""
    flags = []
    flags.append("locked" if shape.locked else "optional")
    if shape.skill_type != "NORMAL":
        flags.append(shape.skill_type)
    if shape.preactivated:
        flags.append("预发动")
    if shape.carrier != "HERO":
        flags.append(shape.carrier)
    return f"{shape.skill_id:34s} {'/'.join(flags):22s} {shape.fingerprint()}"


def _matches(shape: SkillShape, args: argparse.Namespace) -> bool:
    if args.hero and not shape.skill_id.startswith(f"hero.{args.hero}."):
        return False
    if args.skill and not shape.skill_id.startswith(args.skill):
        return False
    if args.hook and args.hook not in {timing for timing, _ in shape.hooks}:
        return False
    if args.seam and args.seam not in shape.seams:
        return False
    if args.modifier and args.modifier not in shape.modifiers:
        return False
    if args.step and not any(step.startswith(args.step) for step in shape.steps):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="技能目录盘点（只读）：形状指纹、反查、封闭集枚举。",
    )
    parser.add_argument("--hero", help="武将 id（如 jie_sunshangxiang）")
    parser.add_argument("--skill", help="技能 id 前缀（如 hero.jie_sunshangxiang.jieyin）")
    parser.add_argument("--hook", help="按时机名筛（如 PHASE_DRAW）")
    parser.add_argument("--seam", help="按接缝名筛（如 EQUIPMENT_DISCARD）")
    parser.add_argument("--modifier", help="按修饰点名筛（如 COUNT_LIMIT）")
    parser.add_argument("--step", help="按步骤短名筛（如 option / cards / seats）")
    parser.add_argument("--all", action="store_true", help="连装备技能一起列")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    for surface in ("timings", "seams", "points", "queries", "zones", "steps", "frames"):
        parser.add_argument(
            f"--{surface}",
            action="store_true",
            help=f"列出框架此刻支持的 {surface}（封闭集，来自代码枚举）",
        )
    args = parser.parse_args(argv)

    surfaces = enumerate_surface()
    asked_surface = [
        name
        for name in ("timings", "seams", "points", "queries", "zones", "steps", "frames")
        if getattr(args, name)
    ]
    if asked_surface:
        payload = {name: surfaces[name] for name in asked_surface}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            for name in asked_surface:
                print(f"== {name} ({len(payload[name])})")
                for item in payload[name]:
                    print(f"  {item}")
        return 0

    shapes = [shape for shape in collect(include_equipment=args.all) if _matches(shape, args)]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "skill_id": shape.skill_id,
                        "carrier": shape.carrier,
                        "locked": shape.locked,
                        "skill_type": shape.skill_type,
                        "preactivated": shape.preactivated,
                        "kinds": list(shape.kinds),
                        "fingerprint": shape.fingerprint(),
                        "hooks": [list(item) for item in shape.hooks],
                        "seams": list(shape.seams),
                        "modifiers": list(shape.modifiers),
                        "steps": list(shape.steps),
                        "rejudge": shape.rejudge,
                        "rejudge_zones": list(shape.rejudge_zones),
                        "places_on_hero_card": shape.places_on_hero_card,
                        "requirement": shape.requirement,
                    }
                    for shape in shapes
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    for shape in shapes:
        print(format_row(shape))
    print(f"\n共 {len(shapes)} 条技能", file=__import__("sys").stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
