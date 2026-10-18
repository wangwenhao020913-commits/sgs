"""**预发动**的注册表与公开入口（W4-1）。

预发动 = **接入层**的交互态：点技能后按**预发动清单**收集代价与目标，确认时提交一个
**复合动作**（``ActivateSkill(picks, choices)``）；引擎用**与候选同源**的谓词校验它。
清单与校验都从同一份 ``SkillFlow.steps`` 渲染（`sgs.skills.flow`），本模块只回答两件事：

- **哪条技能有清单**：主动技读 :data:`~sgs.skills.active.ACTIVE_SKILLS` 的 ``flow``，
  被动技读 :data:`~sgs.skills.catalog.CATALOG` 里钩子 / 接缝的 ``flow``；
- **哪条技能已经翻到预发动**：声明上的 ``preactivated`` 开关（本票只落机制，W4-2 逐条翻）。

不新增询问类型、不改 ``SkillFlow`` 的步骤声明字段：登记表是**派生**的（不手抄 id 名单），
技能加一条 flow 就自动有清单。
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from sgs.state import GameState, Step
from sgs.timing import Timing

from .active import ACTIVE_SKILLS, ActiveSkill
from .catalog import CATALOG, SkillDef
from .flow import SkillFlow, manifest, validate_flow_activation

__all__ = [
    "FLOWS",
    "flow_for",
    "preactivation_for",
    "preactivated_skill_ids",
    "preactivations_for",
    "validate_activation",
]

FLOWS: Mapping[str, tuple[tuple[Timing | None, SkillFlow], ...]]
"""``skill_id`` → 该技能的流水线（W4-1）：``(时机 或 None, SkillFlow)``。

主动技的条目时机为 ``None``（出牌阶段空闲）；被动技按钩子的时机登记，帧内接缝也是
``None``。一个技能可以有多条（不同时机各一条 flow），:func:`flow_for` 按询问时机挑。
"""


def _build_flows(
    active: tuple[ActiveSkill, ...] | list[ActiveSkill] = ACTIVE_SKILLS,
    catalog: Mapping[str, SkillDef] = CATALOG,
) -> Mapping[str, tuple[tuple[Timing | None, SkillFlow], ...]]:
    """从**声明**派生登记表（不手抄 id 名单）。测试可注入一份声明验"派生"本身。"""
    table: dict[str, list[tuple[Timing | None, SkillFlow]]] = {}
    for skill in active:
        if skill.flow is not None:
            table.setdefault(skill.skill_id, []).append((None, skill.flow))
    for skill_id, definition in catalog.items():
        for hook in definition.hooks:
            if hook.flow is not None:
                table.setdefault(skill_id, []).append((hook.timing, hook.flow))
        for seam in definition.seams:
            if seam.flow is not None:
                table.setdefault(skill_id, []).append((None, seam.flow))
    return MappingProxyType({skill_id: tuple(rows) for skill_id, rows in table.items()})


FLOWS = _build_flows()
"""当前声明派生出的登记表（:func:`_build_flows` 的模块级快照）。"""


def flow_for(skill_id: str, timing: Timing | None = None) -> SkillFlow | None:
    """取一条技能的流水线；``timing`` 命中该时机的钩子优先，其次取无时机的条目。

    主动技登记为无时机（出牌阶段空闲）；被动技若同一技能在两个时机各有一条流，
    ``timing`` 必须参与选择，否则会拿到另一条时机的步骤声明。
    """
    entries = FLOWS.get(skill_id, ())
    if not entries:
        return None
    if timing is not None:
        for entry_timing, flow in entries:
            if entry_timing is timing:
                return flow
    for entry_timing, flow in entries:
        if entry_timing is None:
            return flow
    return entries[0][1]


def preactivated_skill_ids(
    active: tuple[ActiveSkill, ...] | list[ActiveSkill] = ACTIVE_SKILLS,
    catalog: Mapping[str, SkillDef] = CATALOG,
) -> frozenset[str]:
    """已经翻到预发动的技能 id（声明上的 ``preactivated`` 开关，**唯一真源**）。

    本票不翻任何一条（``frozenset()``）：引擎把"已翻过去的技能"的 ``ActivateSkill`` 骨架
    从出牌阶段候选里删掉，可用性只由 ``preactivations`` 表达。参数注入让"派生"这条判据
    能对一份声明单独验证，而不必改全局注册表。
    """
    ids = {skill.skill_id for skill in active if skill.preactivated}
    ids.update(
        skill_id for skill_id, definition in catalog.items() if definition.preactivated
    )
    return frozenset(ids)


def preactivation_for(
    state: GameState,
    seat: int,
    skill_id: str,
    *,
    timing: Timing | None = None,
    frame: Any = None,
) -> tuple[Step, ...]:
    """一条技能此刻的预发动清单；空元组 = 没有可下发的清单（未迁移 / 不可见 / 池为空）。

    分步文案（W4-fix-3）取自这条技能在 :data:`~sgs.skills.catalog.CATALOG` 里的声明
    （:attr:`SkillDef.step_requirements`），由 :func:`~sgs.skills.flow.manifest` 填进
    ``Step.wording``——声明只有一份，渲染器不 import 声明表。
    """
    flow = flow_for(skill_id, timing)
    if flow is None:
        return ()
    definition = CATALOG.get(skill_id)
    return manifest(
        flow,
        state,
        seat,
        frame=frame,
        step_requirements=getattr(definition, "step_requirements", None),
    )


def preactivations_for(
    state: GameState,
    seat: int,
    skill_ids: tuple[str, ...] | list[str],
    *,
    timing: Timing | None = None,
    frame: Any = None,
) -> dict[str, tuple[Step, ...]]:
    """``skill_ids`` 里**有清单**的那些：技能 id → 清单（按 id 排序，确定性）。"""
    out: dict[str, tuple[Step, ...]] = {}
    for skill_id in sorted(set(skill_ids)):
        steps = preactivation_for(state, seat, skill_id, timing=timing, frame=frame)
        if steps:
            out[skill_id] = steps
    return out


def validate_activation(
    state: GameState,
    actor: int,
    skill_id: str,
    picks: tuple[int, ...] | list[int],
    choices: tuple[str, ...] | list[str],
    *,
    timing: Timing | None = None,
    frame: Any = None,
) -> str | None:
    """复合动作的**唯一公开校验入口**（W4-1）：首个失败原因 / ``None``。

    不要求动作出现在 ``legal_actions`` 里——这是"候选 = 接受"唯一让开的地方；校验器
    与候选生成读同一份 ``SkillFlow.steps``（区域 / 牌名 / 张数 / ``check_targets`` 链）。
    """
    flow = flow_for(skill_id, timing)
    if flow is None:
        return f"技能 {skill_id} 没有预发动清单"
    return validate_flow_activation(
        flow,
        state,
        actor,
        tuple(int(card_id) for card_id in picks),
        tuple(str(choice) for choice in choices),
        frame=frame,
    )
