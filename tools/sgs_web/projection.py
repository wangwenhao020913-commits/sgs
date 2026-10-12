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

from sgs.data import load_content_pack, load_heroes
from sgs.engine import record_json
from sgs.skills import resolve_instances
from sgs.state import Query, WindowCause, project

from .session import Session

__all__ = [
    "build_skills",
    "candidate_index",
    "option_seats",
    "pending_payload",
    "query_context",
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


# ---------------------------------------------------------------------------
# 询问窗口的"谁对谁做了什么"
# ---------------------------------------------------------------------------


def _seat_label(state: Any, seat: int) -> str:
    """座位的中文人话："0号位郭嘉"（没发武将时退化成"0号位"）。"""
    hero_id = state.player(seat).hero
    if hero_id:
        hero = load_heroes(state.pack_id).by_id(hero_id)
        if hero is not None and hero.name:
            return f"{seat}号位{hero.name}"
    return f"{seat}号位"


def _card_type_name(state: Any, type_id: str) -> str | None:
    """牌名 id → 中文牌名（装备技能的 ``skill_id`` 是 ``equip.<牌名 id>``）。"""
    for card_type in load_content_pack(state.pack_id).card_types:
        if card_type.id == type_id:
            return card_type.name
    return None


def _skill_display_name(state: Any, skill_id: str) -> str | None:
    """技能 id → 中文技能名。

    两种形态都要认：命名空间 id（``hero.guojia.yiji``）与**合成键**
    （``yiji:give:0:0``——用冒号把点号/轮号拼进缓存键）。前者带武将 id、可直接定位；
    后者只留短 id，按短 id 在**全部**武将技能里查。``equip.<牌名 id>`` 取牌名。
    """
    head = skill_id.split(":", 1)[0]
    if head.startswith("equip."):
        return _card_type_name(state, head[len("equip.") :])
    parts = head.split(".")
    short = parts[-1]
    heroes = load_heroes(state.pack_id)
    if len(parts) >= 3 and parts[0] == "hero":
        hero = heroes.by_id(parts[1])
        pool = (hero,) if hero is not None else ()
    else:
        pool = heroes.heroes
    for hero in pool:
        if hero is None:
            continue
        for skill in hero.skills:
            if skill.id == short:
                return skill.name
    return None


def _frame_targets(data: dict[str, Any]) -> list[int]:
    """帧载荷里的**当前目标**：``targets[cursor]``；多目标结算逐目标取一个。"""
    targets = tuple(data.get("targets", ()))
    if not targets:
        return []
    cursor = data.get("cursor")
    if isinstance(cursor, int) and 0 <= cursor < len(targets):
        return [int(targets[cursor])]
    return [int(seat) for seat in targets]


def _context_targets(query: Query, data: dict[str, Any]) -> list[int]:
    """这次询问针对的座位：询问自带的 ``target`` / ``dying`` 优先，否则读栈顶帧的当前目标。

    "当前目标"用 ``RESOLVE`` 帧的 ``cursor`` 取——多目标锦囊逐目标结算时，
    询问窗口的人话应当是"对**这个**目标"，而不是把全部目标列一遍。
    """
    target = getattr(query, "target", None)
    if target is not None:
        return [int(target)]
    dying = getattr(query, "dying", None)
    if dying is not None:
        return [int(dying)]
    return _frame_targets(data)


def _nullified_frame(engine: Any, data: dict[str, Any]) -> dict[str, Any]:
    """顺着帧私有 ``cancel_target`` 找到**被无懈的那一层**帧载荷。

    无懈自身的 ``RESOLVE`` 帧 ``targets = ()``，"当前目标"退回 ``source``（= 无懈使用者），
    于是嵌套无懈窗口会写成"1号位对1号位使用无懈可击"（看起来像自己无懈自己）。
    无懈的载荷里记着它指向哪一层的哪个目标（``cancel_target``），故这里原地回溯——
    嵌套多层的无懈会一路跳到最外层那张**牌**。找不到父帧时返回原载荷（fail-soft，不编话）。
    """
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    seen: set[int] = set()
    while getattr(data.get("card"), "type", None) == "nullify":
        cancel = data.get("cancel_target")
        if not isinstance(cancel, tuple) or len(cancel) != 2:
            return data
        parent_id = cancel[0]
        if parent_id in seen:
            return data
        seen.add(parent_id)
        parent = next((frame for frame in frames if frame.frame_id == parent_id), None)
        if parent is None:
            return data
        data = parent.data
    return data


def option_seats(query: Query) -> dict[str, int]:
    """``SELECT_OPTION`` 里形如 ``seat:N`` 的选项 → 座位；其余询问 / 选项一律空 dict。

    界面据此把座位画成**可点的**（【遗计】的"交给谁"），省得玩家去读 ``选项：seat:3``
    这种报文串。解析放在服务端、前端只读数据——"界面不裁决规则"这条纪律因此不受影响。
    """
    options = getattr(query, "options", None)
    if options is None:
        return {}
    out: dict[str, int] = {}
    for option in options:
        if isinstance(option, str) and option.startswith("seat:"):
            try:
                out[option] = int(option.split(":", 1)[1])
            except ValueError:
                continue
    return out


def query_context(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """这次询问的**上下文人话**："0号位郭嘉对0号位郭嘉使用遗计"。

    数据来源只有**公开事实**：询问本身（``kind`` / ``skill_id`` / ``target`` / ``dying`` /
    ``revealed``）与结算栈顶帧的载荷（``source`` / ``card`` / ``targets`` / ``cursor`` /
    ``running_skill``）。它是**表现层**的派生，不参与
    任何合法性判定，也不新增真源——帧与询问都是引擎已经下发的对象。

    两种询问分叉：

    - **技能询问**（带 ``skill_id``）没有"使用的牌"：名字来自技能面，作用者是技能持有者
      （= 询问的 ``actor``），目标也是他自己（【遗计】这类"对自己生效"的直读）。
      装备技能例外：作用者 / 目标是那次**使用**的（读栈顶帧）。
    - **观看型选牌**（``SELECT_CARDS`` 且询问带了 ``revealed`` 授予，如【遗计】选哪张）：
      名字取栈顶帧的 **``running_skill``**（谁在跑这个回调），而不是造成伤害的那张牌。
    - **牌询问**：名字来自栈顶帧的牌面，作用者是帧的 ``source``，目标是当前目标。

    濒死求桃没有"使用的牌"，退化成"谁对谁造成了伤害"。都取不到时返回 ``None``
    （界面据此不画这一行，而不是编一句假话）。
    """
    if query is None:
        return None
    state = engine.state
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    data: dict[str, Any] = frames[-1].data if frames else {}

    skill_id = getattr(query, "skill_id", None)
    if skill_id is None and getattr(query, "revealed", ()):
        # 观看型选牌：牌面是**当前回调**授予的，名字该读"谁在跑"（【遗计】），
        # 而不是造成伤害的那张牌（那会写成"对郭嘉使用杀"）。
        skill_id = data.get("running_skill")
    if skill_id is not None:
        skill_id = str(skill_id)
        name = _skill_display_name(state, skill_id)
        if skill_id.startswith("equip."):
            source = data.get("source", query.actor)
            targets = _context_targets(query, data)
        else:
            source = query.actor
            targets = []
        if not targets:
            targets = [int(query.actor)]
    else:
        # 无懈窗口的"目标"是**被无懈的那张牌**（报障：不能写成"自己"）。
        # 顶层帧若本身就是一张【无懈可击】，它的 targets 为空、"当前目标"退回使用者，
        # 直读会写成"1号位对1号位使用无懈可击"；顺着 cancel_target 回溯到被无懈的牌。
        if (
            getattr(query, "cause", None) is WindowCause.NULLIFY
            and getattr(data.get("card"), "type", None) == "nullify"
        ):
            origin = _nullified_frame(engine, data)
            origin_card = origin.get("card")
            origin_name = getattr(origin_card, "name", None)
            origin_source = origin.get("source")
            origin_targets = _frame_targets(origin)
            if origin_name is not None and origin_source is not None:
                nullifier = data.get("source")
                head = (
                    f"{_seat_label(state, int(nullifier))}使用【无懈可击】"
                    if nullifier is not None
                    else "使用【无懈可击】"
                )
                source_label = _seat_label(state, int(origin_source))
                if origin_targets:
                    target_labels = "、".join(_seat_label(state, seat) for seat in origin_targets)
                    held = f"{source_label}对{target_labels}使用的【{origin_name}】"
                else:
                    held = f"{source_label}使用的【{origin_name}】"
                return {
                    "actor": int(nullifier) if nullifier is not None else int(origin_source),
                    "name": "无懈可击",
                    "targets": [int(seat) for seat in origin_targets],
                    "label": f"{head}（抵消 {held}）",
                }
        name = getattr(data.get("card"), "name", None)
        source = data.get("source", query.actor)
        targets = _context_targets(query, data)

    if name is None:
        # 濒死求桃这类"没有使用的牌"的窗口：退化成"谁对谁造成了伤害"。
        damage_source = data.get("damage_source")
        if damage_source is not None and getattr(query, "dying", None) is not None:
            targets = [int(query.dying)]
            return {
                "actor": int(damage_source),
                "name": None,
                "targets": targets,
                "label": (
                    f"{_seat_label(state, int(damage_source))}"
                    f"对{_seat_label(state, int(query.dying))}造成伤害"
                ),
            }
        return None
    if source is None:
        return None

    source_label = _seat_label(state, int(source))
    target_labels = [_seat_label(state, seat) for seat in targets]
    label = (
        f"{source_label}对{'、'.join(target_labels)}使用{name}"
        if target_labels
        else f"{source_label}使用{name}"
    )
    return {
        "actor": int(source),
        "name": name,
        "targets": [int(seat) for seat in targets],
        "label": label,
    }


def candidate_index(query: Query) -> dict[str, dict[str, Any]]:
    """按牌聚合的**候选索引**——界面据它高亮"能点谁"，据它判定提交**可否**。

    它是**合法行动集合**的索引，不是第二份合法性：每个 ``pool`` 里的座位、每组 ``combos``
    里的组合，都能在同一张牌的候选里找到对应的一条。故"候选与校验同源"不因下发形状变化而改变，
    可由一条断言机械钉死（`tests/test_web.py`）。

    三件事刻意不做：

    - **不裁决规则**：不读距离 / 攻击范围 / 花色 / 体力 / 存活；只把候选里有的事实聚合起来。
    - **不重排**：``pool`` 与 ``combos`` 保持候选的**首次出现顺序**（候选本身由引擎稳定排序）。
    - **不收无牌的候选**：``SelectOption`` / ``ActivateSkill`` 不是"牌 → 目标"，不进索引。

    ``min_targets`` / ``max_targets`` 是**这张牌这次使用**要选几个目标（【杀】恒 1；
    【南蛮入侵】类等于池大小），界面据此说"还要选 N 个"。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for action in query.legal_actions:
        card = getattr(action, "card", None)
        if card is None:
            continue
        entry = grouped.get(str(card.card_id))
        if entry is None:
            entry = {
                "card": card.model_dump(mode="json"),
                "pool": [],
                "combos": [],
                "_sizes": set(),
            }
            grouped[str(card.card_id)] = entry
        combo = tuple(getattr(action, "targets", ()))
        if combo not in entry["combos"]:
            entry["combos"].append(combo)
        entry["_sizes"].add(len(combo))
        for seat in combo:
            if seat not in entry["pool"]:
                entry["pool"].append(seat)

    for entry in grouped.values():
        sizes = entry.pop("_sizes")
        entry["min_targets"] = min(sizes) if sizes else 0
        entry["max_targets"] = max(sizes) if sizes else 0
        entry["combos"] = [list(combo) for combo in entry["combos"]]
    return grouped


def pending_payload(session: Session | Query | None) -> dict[str, Any] | None:
    """一次询问的**可应答形态**——带候选、带询问者自己的视角（前端只 pick，不自己拼 Action）。

    收 ``Session``（取它的当前挂起）、``Query``（409 里附带的现状）或 ``None``（无挂起）。

    ``legal_actions`` 保留（既有测试、超时兜底与"提交必须原样取自候选"的自我核对都读它），
    ``candidate_index`` 是本轮新增的**索引形状**：界面用它手选目标，不再逐条铺候选按钮。
    """
    if session is None:
        return None
    query = session.pending if isinstance(session, Session) else session
    if query is None:
        return None
    payload = query.model_dump(mode="json")
    payload["legal_actions"] = [action.model_dump(mode="json") for action in query.legal_actions]
    payload["candidate_index"] = candidate_index(query)
    # 询问窗口的"谁对谁做了什么"：从**结算栈顶帧 + 询问本身**派生的表现层事实。
    # 只有拿得到 ``Session``（= 拿得到引擎）时才算；409 里附带的那份现状（裸 ``Query``）为 None。
    payload["context"] = (
        query_context(session.engine, query) if isinstance(session, Session) else None
    )
    payload["option_seats"] = option_seats(query)
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
