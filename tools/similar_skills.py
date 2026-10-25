#!/usr/bin/env python3
"""相似技能反查：给定一条技能的"形状"，列出库里最像的几条（只读）。

新增技能时回答 **「有没有同形的现成技能可抄」**——比通读 `CATALOG` 快，也比凭印象可靠。
形状来自 :mod:`tools.dump_catalog` 的指纹（钩子时机 / 接缝 / 修饰点 / 步骤序列 / 预发动位）。

    # 一条已在库里的技能跟谁最像（写"界版参考标准版"这种场合最常用）
    .venv/bin/python tools/similar_skills.py --like hero.jie_sunshangxiang.jieyin

    # 还没写代码的技能：直接报形状
    .venv/bin/python tools/similar_skills.py --hook PHASE_DRAW
    .venv/bin/python tools/similar_skills.py --step option --step cards
    .venv/bin/python tools/similar_skills.py --flow seats,option,cards
    .venv/bin/python tools/similar_skills.py --modifier COUNT_LIMIT --hook DAMAGE_AFTER_TAKE

打分是**可解释的加权和**（不是黑箱）：每一分都能在输出的 `+` 列表里看到来源。只读、确定性。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dump_catalog import SkillShape, collect  # noqa: E402

__all__ = ["Scored", "rank", "main"]

# 权重：改了这些数就改了"像"的定义，故集中在一处并写清理由。
# 时机 / 接缝最贵（它们决定"什么时候被叫起来"，选错就要换原语）；
# 修饰点其次（连续型 vs 一次性）；步骤序列再次（交互形状）；其余是决胜的细枝末节。
WEIGHTS = {
    "hook": 6,
    "seam": 6,
    "modifier": 4,
    "step": 3,
    "flow_length": 2,
    "preactivated": 1,
    "locked": 1,
    "regtion": 1,
}
"""各特征的权重（``regtion`` 是"同上区 / 上轮"之外的杂项：放置类 / 改判类）。"""


@dataclass(frozen=True)
class Scored:
    """一条候选与它的得分来源（``why`` 逐项列出加了多少分）。"""

    shape: SkillShape
    score: int
    why: tuple[str, ...]


def _target(args: argparse.Namespace, shapes: tuple[SkillShape, ...]) -> SkillShape | None:
    """``--like`` 时取库里那条技能的形状（找不到就退出 2）。"""
    if not args.like:
        return None
    for shape in shapes:
        if shape.skill_id == args.like:
            return shape
    raise SystemExit(f"没有这条技能：{args.like}")


def rank(
    shapes: tuple[SkillShape, ...],
    *,
    like: str | None = None,
    hooks: tuple[str, ...] = (),
    seams: tuple[str, ...] = (),
    modifiers: tuple[str, ...] = (),
    steps: tuple[str, ...] = (),
    flow: tuple[str, ...] = (),
    preactivated: bool | None = None,
    locked: bool | None = None,
    limit: int = 5,
) -> tuple[Scored, ...]:
    """按形状相似度排序；``like`` 给定的那条技能自身不计入结果。"""
    base = None
    if like is not None:
        base = next((s for s in shapes if s.skill_id == like), None)
        if base is None:
            raise ValueError(f"没有这条技能：{like}")
        hooks = hooks or tuple(timing for timing, _ in base.hooks)
        seams = seams or base.seams
        modifiers = modifiers or base.modifiers
        steps = steps or tuple(step.split("(")[0] for step in base.steps)
        flow = flow or tuple(base.steps)
        preactivated = base.preactivated if preactivated is None else preactivated
        locked = base.locked if locked is None else locked

    wanted_steps = {step.split("(")[0] for step in steps}
    scored: list[Scored] = []
    for shape in shapes:
        if base is not None and shape.skill_id == base.skill_id:
            continue
        score = 0
        why: list[str] = []
        for timing in hooks:
            if timing in {name for name, _ in shape.hooks}:
                score += WEIGHTS["hook"]
                why.append(f"时机 {timing}")
        for seam in seams:
            if seam in shape.seams:
                score += WEIGHTS["seam"]
                why.append(f"接缝 {seam}")
        for point in modifiers:
            if point in shape.modifiers:
                score += WEIGHTS["modifier"]
                why.append(f"修饰点 {point}")
        for step in sorted(wanted_steps & {s.split("(")[0] for s in shape.steps}):
            score += WEIGHTS["step"]
            why.append(f"步骤 {step}")
        if flow:
            shape_flow = tuple(step.split("(")[0] for step in shape.steps)
            if shape_flow == tuple(step.split("(")[0] for step in flow):
                score += WEIGHTS["flow_length"]
                why.append("步骤序列相同")
        if preactivated is not None and shape.preactivated == preactivated:
            score += WEIGHTS["preactivated"]
            why.append("预发动位相同" if preactivated else "都不预发动")
        if locked is not None and shape.locked == locked:
            score += WEIGHTS["locked"]
            why.append("锁定位相同" if locked else "都非锁定")
        if shape.rejudge or shape.places_on_hero_card:
            other = base.rejudge if base else False
            if shape.rejudge and other:
                score += WEIGHTS["regtion"]
                why.append("都是改判类")
        if score <= 0:
            continue
        scored.append(Scored(shape=shape, score=score, why=tuple(why)))
    scored.sort(key=lambda item: (-item.score, item.shape.skill_id))
    return tuple(scored[:limit])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="相似技能反查（只读）：按形状给库里最像的几条排序。",
    )
    parser.add_argument("--like", help="以库里这条技能为参照（如 hero.jie_sunshangxiang.jieyin）")
    parser.add_argument("--hook", action="append", default=[], help="时机名（可重复）")
    parser.add_argument("--seam", action="append", default=[], help="接缝名（可重复）")
    parser.add_argument("--modifier", action="append", default=[], help="修饰点名（可重复）")
    parser.add_argument(
        "--step", action="append", default=[], help="步骤短名（cards / seats / option / provide / arrange）"
    )
    parser.add_argument("--flow", help="完整步骤序列，逗号分隔（如 seats,option,cards）")
    parser.add_argument("--preactivated", action="store_true", help="只看预发动的同类")
    parser.add_argument("--locked", action="store_true", help="只看锁定技的同类")
    parser.add_argument("--limit", type=int, default=5, help="最多输出几条（缺省 5）")
    args = parser.parse_args(argv)

    shapes = collect(include_equipment=False)
    if not args.like and not (args.hook or args.seam or args.modifier or args.step or args.flow):
        parser.error("至少给一个形状来源：--like，或 --hook / --seam / --modifier / --step / --flow")

    scored = rank(
        shapes,
        like=args.like,
        hooks=tuple(args.hook),
        seams=tuple(args.seam),
        modifiers=tuple(args.modifier),
        steps=tuple(args.step),
        flow=tuple(args.flow.split(",")) if args.flow else (),
        preactivated=args.preactivated or None,
        locked=args.locked or None,
        limit=args.limit,
    )
    if not scored:
        print("没有形状相近的现成技能——很可能是架构缺口（见 shapes.md 的「新机制三档判定」）")
        return 0
    for item in scored:
        print(f"{item.score:3d}  {item.shape.skill_id:34s} {' + '.join(item.why)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
