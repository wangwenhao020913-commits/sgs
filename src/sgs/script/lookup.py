"""剧本库的**只读查找**：引用一条不存在的实体 ⇒ 规则层错误（M7 票 17）。

编译（:mod:`sgs.script.compile`）与结算（:mod:`sgs.script.settle`）都要按 id 从
「剧本快照」里取出实体。查找是**同一个**出口，免得两处各写一份"找不到怎么办"：
缺项一律 :class:`~sgs.script.ScriptError`，带 ``loc``（引用方给的 id）与可读原因。

这一层只读、不缓存、不改快照：库里有什么就返回什么（票 03 §7：run 与库的关系是
"开局拷一份快照"，所以找的是**快照**里的东西，不是磁盘上的最新库）。
"""

from __future__ import annotations

from sgs.data import (
    Event,
    EventPool,
    GlobalSkill,
    Layer,
    Node,
    Room,
    Script,
    ScriptLibrary,
    SkillPool,
    Template,
)

from .errors import ScriptError
from .state import Pending, progress_key

__all__ = [
    "event_of",
    "event_pool_of",
    "global_skill_of",
    "layer_of",
    "node_of",
    "pending_node_of",
    "room_of",
    "script_of",
    "skill_pool_of",
    "template_of",
]


def script_of(library: ScriptLibrary, script_id: str) -> Script:
    """按 id 取剧本（``RunState.script_id`` 指向的那一份快照）。"""
    for script in library.scripts:
        if script.id == script_id:
            return script
    raise ScriptError(
        rule_id="script.lookup.unknown_script",
        loc=script_id,
        reason=f"剧本快照里没有剧本 {script_id!r}：run 指向的剧本与快照不一致",
    )


def layer_of(script: Script, layer_index: int) -> Layer:
    """按 ``RunState.layer_index`` 取大节（越界 ⇒ 可读错误，不是 IndexError）。"""
    layers = script.layers
    if not 0 <= layer_index < len(layers):
        raise ScriptError(
            rule_id="script.lookup.layer_index_out_of_range",
            loc=f"{script.id}/layers[{layer_index}]",
            reason=(
                f"剧本 {script.id!r} 只有 {len(layers)} 个大节："
                f"layer_index = {layer_index} 越界"
            ),
        )
    return layers[layer_index]


def node_of(layer: Layer, node_id: str) -> Node:
    """按**层内**节点 id 取节点。"""
    for node in layer.nodes:
        if node.id == node_id:
            return node
    raise ScriptError(
        rule_id="script.lookup.unknown_node",
        loc=progress_key(layer.id, node_id),
        reason=f"大节 {layer.id!r} 里没有节点 {node_id!r}",
    )


def pending_node_of(layer: Layer, pending: Pending) -> Node:
    """``pending.node_id``（进度键）→ 节点；跨大节 ⇒ 可读错误。"""
    layer_id, _, node_id = pending.node_id.partition("/")
    if layer_id != layer.id:
        raise ScriptError(
            rule_id="script.lookup.pending_outside_layer",
            loc=pending.node_id,
            reason=(
                f"pending 落在别的大节 {layer_id!r}：当前大节是 {layer.id!r}"
                f"（进度键形如 {progress_key(layer.id, '<node_id>')}）"
            ),
        )
    return node_of(layer, node_id)


def room_of(library: ScriptLibrary, room_id: str) -> Room:
    for room in library.rooms:
        if room.id == room_id:
            return room
    raise ScriptError(
        rule_id="script.lookup.unknown_room",
        loc=room_id,
        reason=f"剧本快照里没有房间 {room_id!r}（节点指向的房间被删了？）",
    )


def template_of(library: ScriptLibrary, template_id: str) -> Template:
    for template in library.templates:
        if template.id == template_id:
            return template
    raise ScriptError(
        rule_id="script.lookup.unknown_template",
        loc=template_id,
        reason=f"剧本快照里没有敌人模板 {template_id!r}",
    )


def skill_pool_of(library: ScriptLibrary, pool_id: str) -> SkillPool:
    for pool in library.skill_pools:
        if pool.id == pool_id:
            return pool
    raise ScriptError(
        rule_id="script.lookup.unknown_pool",
        loc=pool_id,
        reason=f"剧本快照里没有技能池 {pool_id!r}",
    )


def event_of(library: ScriptLibrary, event_id: str) -> Event:
    for event in library.events:
        if event.id == event_id:
            return event
    raise ScriptError(
        rule_id="script.lookup.unknown_event",
        loc=event_id,
        reason=f"剧本快照里没有事件 {event_id!r}",
    )


def event_pool_of(library: ScriptLibrary, pool_id: str) -> EventPool:
    for pool in library.event_pools:
        if pool.id == pool_id:
            return pool
    raise ScriptError(
        rule_id="script.lookup.unknown_event_pool",
        loc=pool_id,
        reason=f"剧本快照里没有事件池 {pool_id!r}",
    )


def global_skill_of(library: ScriptLibrary, skill_id: str) -> GlobalSkill:
    for skill in library.global_skills:
        if skill.id == skill_id:
            return skill
    raise ScriptError(
        rule_id="script.lookup.unknown_global_skill",
        loc=skill_id,
        reason=f"剧本快照里没有副本全局技能 {skill_id!r}",
    )
