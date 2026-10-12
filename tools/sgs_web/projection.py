"""可见面：**唯一**的过滤点（ADR-012 / 地图 D8）。

两条口径，别混：

1. **给人类座位的状态快照** = 既有纯函数 :func:`sgs.state.project` 的输出。
   本模块**不**自己从 ``engine.state`` 拼字段（那就是第二份真源的雏形），也不缓存。
2. **记录流的下发** = 引擎记录流本身是**全知**的（``cli.py`` 的 docstring 自己写着），
   所以逐条过 :func:`visible_record`：
   - ``query`` 记录**不带** ``view`` 与 ``legal_actions``：非人类座位的询问带的是**别人的视角**，
     而人类座位的候选在"当前挂起"那一条里单独给（前端只需要能点的那一份）；
   - ``action`` / ``timing`` / ``delta`` / ``frame`` / ``game_end`` 是公开事实，原样下发。
"""

from __future__ import annotations

import json
from typing import Any

from sgs.data import load_heroes
from sgs.engine import record_json
from sgs.skills import resolve_instances
from sgs.state import Query, project

from .session import Session

__all__ = [
    "build_skills",
    "pending_payload",
    "serialize_record",
    "visible_record",
    "visible_snapshot",
]


def visible_snapshot(session: Session) -> dict[str, Any]:
    """人类座位的**玩家视角**快照（每次现算、不缓存）。

    这是 ``tests/test_web.py`` 的"不泄露"判据盯住的那个对象：对手只有 ``hand_count``、
    身份只出现"本人 / 主公 / 已阵亡"三种可见情形、牌堆只有数量无顺序。
    """
    view = project(session.engine.state, session.human_seat)
    return view.model_dump(mode="json")


def build_skills(session: Session, seat: int) -> list[dict[str, Any]]:
    """``seat`` 的**技能清单**（刀 4）：名称 / 类型 / 是否锁定 / **此刻是否可用**。

    三件事必须说清，否则这条清单是编的：

    - **谁的技能**：技能实例由 ``sgs.skills.resolve_instances`` 解析，它已经含身份门控
      （非主公的武将牌上**没有**主公技），故"有的技能"不是前端猜的；
    - **什么类型**：``locked`` 是官方"牌面有没有『可以』"的逐字判据，中文名与
      ``skill_type`` 取自**武将面数据**（`HeroSkill.name` / `.skill_type`）；
    - **此刻能不能用**：`available` = 当前挂起的询问里有没有针对这个技能的
      ``ActivateSkill`` 候选。**不去问引擎"这个技能满不满足条件"**——那是第二条真源。

    只列 ``seat`` 自己的实例：别人的技能是**暗置**信息，不下发。
    """
    state = session.engine.state
    pending = session.pending
    offered = {
        action.skill_id
        for action in (pending.legal_actions if pending is not None else ())
        if action.kind == "ActivateSkill"
    }
    hero_id = state.players[seat].hero
    names: dict[str, tuple[str, str]] = {}
    if hero_id is not None:
        hero = load_heroes(state.pack_id).by_id(hero_id)
        if hero is not None:
            # 武将面里的技能 id 是**短名**（`guose`），而实例上的 `skill_id` 带命名空间
            # （`hero.daqiao.guose`）；两边靠**末段**对上，别去猜前缀。
            names = {
                skill.id.rsplit(".", 1)[-1]: (skill.name, getattr(skill.skill_type, "value", str(skill.skill_type)))
                for skill in hero.skills
            }

    out: list[dict[str, Any]] = []
    for instance in resolve_instances(state):
        if instance.owner != seat:
            continue
        definition = instance.definition
        # 没有中文名的只剩装备技能；它们由装备牌承载，先不列（地图 D20 登记）。
        short_id = instance.skill_id.rsplit(".", 1)[-1]
        name, skill_type = names.get(
            short_id, (None, getattr(definition.skill_type, "value", str(definition.skill_type)))
        )
        if name is None:
            continue
        out.append(
            {
                "skill_id": definition.skill_id,
                "name": name,
                "skill_type": skill_type,
                "locked": bool(definition.locked),
                "carrier": getattr(definition.carrier, "value", str(definition.carrier)),
                "available": definition.skill_id in offered,
            }
        )
    out.sort(key=lambda item: (item["locked"], item["skill_id"]))
    return out


def _query_summary(query: Query) -> dict[str, Any]:
    """询问的**公开摘要**：不带视角、不带候选（候选只随"当前挂起"给）。"""
    return {
        "seq": query.seq,
        "actor": query.actor,
        "kind": query.kind,
        "timing": query.timing.value,
        "prompt": query.prompt,
    }


def pending_payload(session: Session | Query | None) -> dict[str, Any] | None:
    """一次询问的**可应答形态**——带候选、带询问者自己的视角（前端只 pick，不自己拼 Action）。

    收 ``Session``（取它的当前挂起）、``Query``（409 里附带的现状）或 ``None``（无挂起）。
    """
    if session is None:
        return None
    query = session.pending if isinstance(session, Session) else session
    if query is None:
        return None
    payload = query.model_dump(mode="json")
    payload["legal_actions"] = [action.model_dump(mode="json") for action in query.legal_actions]
    return payload


def visible_record(record: object) -> dict[str, Any] | None:
    """把一条记录转成可下发的 JSON；``None`` = 这一条不下发。"""
    kind = getattr(record, "kind", None)
    if kind == "query":
        return {"kind": "query", "seq": record.seq, "query": _query_summary(record.query)}
    raw = json.loads(record_json(record))
    if kind == "action":
        # 公开事实：谁应答了、被接受与否；**具体选了什么**只对人类座位自己的应答展开。
        action = raw.get("action")
        if isinstance(action, dict):
            raw["action"] = {"kind": action.get("kind"), "answers_seq": action.get("answers_seq")}
    return raw


def serialize_record(record: object) -> dict[str, Any] | None:
    """``visible_record`` 的别名，名字更贴近"序列化"的调用点。"""
    return visible_record(record)
