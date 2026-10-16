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
from sgs.effects import response_count
from sgs.engine import record_json
from sgs.skills import CATALOG, conversion_offers, resolve_instances
from sgs.state import FrameKind, Query, WindowCause, project
from sgs.timing import Timing

from .session import Session

__all__ = [
    "build_skills",
    "candidate_index",
    "option_seats",
    "pending_payload",
    "query_context",
    "serialize_record",
    "virtual_index",
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
    actions = tuple(pending.legal_actions) if pending is not None else ()
    offered = {
        action.skill_id
        for action in actions
        if action.kind == "ActivateSkill"
    }
    hand = tuple(state.player(seat).hand)
    virtual = _virtual_index(pending, state, owner=seat) if pending is not None else {}
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
        short_id = instance.skill_id.rsplit(".", 1)[-1]
        name, skill_type = names.get(
            short_id, (None, getattr(definition.skill_type, "value", str(definition.skill_type)))
        )
        if name is None and definition.skill_id.startswith("equip."):
            # 装备技能没有**武将面**的名字：中文名取它所依附的**装备牌名**
            # （``equip.crossbow`` → 诸葛连弩）。装备与武将技能同列，界面据 ``carrier``
            # 分别标注（报障：装备技能此前整条被略去）。
            name = _card_type_name(state, definition.skill_id[len("equip.") :])
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
                # 界面怎么摆这个技能：多选手牌 → 点座位（【仁德】）与转化来源牌（【龙胆】）。
                # **纯表现层派生**，只读候选与实例声明，不参与任何合法性判定。
                "ui": _skill_ui(definition, actions, hand, virtual),
            }
        )
    out.sort(key=lambda item: (item["locked"], item["skill_id"]))
    return out


def _seat_of_choice(choice: Any) -> int | None:
    """候选载荷里的 ``seat:N`` → 座位；别的文本（花色等）→ ``None``。"""
    if isinstance(choice, str) and choice.startswith("seat:"):
        try:
            return int(choice.split(":", 1)[1])
        except ValueError:
            return None
    return None


def _skill_ui(
    definition: Any,
    actions: tuple[Any, ...],
    hand: tuple[int, ...],
    virtual: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """一条技能的**界面提示**：它此刻要求玩家怎么点。

    - ``manual``：这条技能的候选带 ``picks``（手牌）且有唯一目标 —— 即"多选手牌 →
      点一位在场角色"，界面据此走**直接点手牌 / 直接点武将**的路，而不是铺选项按钮
      （【仁德】【青囊】【结姻】）。判据全部取自候选：有一条带非空 ``picks`` 的候选、
      每个候选恰好一个 ``choices``、且 ``picks`` 都是本人手牌。
    - ``convert_sources``：本技能此刻能转化为虚拟牌的**来源手牌 id**（【龙胆】的
      "一张【闪】当【杀】"）。转牌没有 ``ActivateSkill`` 候选，界面据这份来源清单
      让技能按钮点得动、并只高亮可转化的手牌。
    """
    skill_id = definition.skill_id
    own = [
        action
        for action in actions
        if getattr(action, "kind", None) == "ActivateSkill" and action.skill_id == skill_id
    ]
    chosen: set[int] = set()
    seats: list[int] = []
    has_picks = False
    single_seat = bool(own)
    all_hand = bool(own)
    for action in own:
        picks = tuple(getattr(action, "picks", ()))
        if picks:
            has_picks = True
        for card_id in picks:
            card_id = int(card_id)
            chosen.add(card_id)
            if card_id not in hand:
                all_hand = False
        choices = tuple(getattr(action, "choices", ()))
        if len(choices) != 1:
            single_seat = False
        for choice in choices:
            seat = _seat_of_choice(choice)
            if seat is not None and seat not in seats:
                seats.append(seat)
    manual = has_picks and bool(seats) and single_seat and all_hand
    return {
        "requirement": definition.requirement or "",
        "manual": manual,
        "pick_cards": [card_id for card_id in hand if card_id in chosen] if manual else [],
        "pick_seats": seats if manual else [],
        "convert_sources": sorted(
            int(source)
            for source, aliases in virtual.items()
            if any(skill_id in alias["skill_ids"] for alias in aliases)
        ),
    }


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


def _event_context(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """这次询问**正在发生什么**的人话："0号位郭嘉对0号位郭嘉使用遗计"。

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
        # 判定路径的 ``RESOLVE`` 帧**没有使用者**（``source=None``，由 ``_phase_judge`` 压出），
        # 但"被判定者"就在 ``targets`` 里。不点名他，界面只画一条通用提示——玩家看见处理区
        # 一张【乐不思蜀】和另一名角色判定区里的另一张，就会以为在问**别人的**牌。
        if targets and data.get("use_timing") is Timing.PHASE_JUDGE:
            who = "、".join(_seat_label(state, int(seat)) for seat in targets)
            return {
                "actor": int(targets[0]),
                "name": name,
                "targets": [int(seat) for seat in targets],
                "label": f"{who}判定{name}（判定开始前）",
            }
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


# ---------------------------------------------------------------------------
# "需要做什么"（item 4）：技能文案取自 SkillDef.requirement；卡牌窗口走表现层表
# ---------------------------------------------------------------------------


def _definition_of(state: Any, skill_id: str):
    """技能 id → :class:`~sgs.skills.catalog.SkillDef`；合成键（``yiji:give:0:0``）走短 id。"""
    head = skill_id.split(":", 1)[0]
    definition = CATALOG.get(head)
    if definition is not None:
        return definition
    short = head.rsplit(".", 1)[-1]
    for candidate in CATALOG.values():
        if candidate.skill_id.rsplit(".", 1)[-1] == short:
            return candidate
    return None


def _skill_of_query(engine: Any, query: Query, data: dict[str, Any]) -> str | None:
    """这次询问归**哪条技能**（按可靠度依次）：询问自带 id → 帧 ``running_skill`` →
    ``SKILL`` 帧的 ``skill_id``（主动技顺序选牌）→ **阶段体**里正在跑的技能（【观星】）。

    都没有 ⇒ ``None``（这是一条纯卡牌窗口，文案走表现层表）。
    """
    skill_id = getattr(query, "skill_id", None)
    if skill_id:
        return str(skill_id)
    if data.get("running_skill"):
        return str(data["running_skill"])
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    if frames and getattr(frames[-1], "kind", None) is FrameKind.SKILL and data.get("skill_id"):
        return str(data["skill_id"])
    phase_body = getattr(engine, "phase_body_skill", None)
    if callable(phase_body):
        running = phase_body()
        if running:
            return str(running)
    return None


def _need_text(verb: str, card_name: str, need: int) -> str:
    """"需要打出一张 X / 连续两张 X"的人话；数量 > 1 时补"依次、打一张再问一次"。"""
    if need > 1:
        done = "打完" if verb == "打出" else f"{verb}完"
        return f"需要{verb}{need}张{card_name}（依次{verb}两张，每{done}一张再询问一次）"
    return f"需要{verb}{card_name}"


def _count_needed(engine: Any, query: Query, data: dict[str, Any]) -> int:
    """本窗口需要几张：唯一真源 :func:`sgs.effects.response_count`（【无双】= 2）。"""
    source = data.get("source")
    if source is None:
        return 1
    target = getattr(query, "target", None)
    if target is None:
        target = getattr(query, "actor", None)
    return int(response_count(engine.state, source=source, card=data.get("card"), target=target))


def _card_window_requirement(engine: Any, query: Query, data: dict[str, Any]) -> str | None:
    """**不带技能归属**的窗口的"需要做什么"（表现层表；不新增真源）。"""
    kind = str(getattr(query, "kind", ""))
    if kind == "PLAY_PHASE":
        return "使用一张牌（或发动技能），或结束出牌阶段"
    if kind == "DISCARD":
        return f"弃置 {getattr(query, 'count', 0)} 张手牌"
    if kind == "DODGE":
        return _need_text("使用", "【闪】", _count_needed(engine, query, data))
    if kind == "PEACH":
        dying = getattr(query, "dying", None)
        return f"使用【桃】救回 {dying} 号位" if dying is not None else "使用【桃】"
    if kind == "ARRANGE":
        return "排列这些牌：给出顺序与前几张置于牌堆顶"
    if kind == "SELECT_CARDS":
        card_type = getattr(data.get("card"), "type", None)
        if card_type in ("dismantlement", "snatch"):
            return "选择目标区域里的一张牌"
        if card_type == "harvest":
            return "从亮出的牌里选一张获得"
        return "从选择面里选择要交出的牌"
    if kind == "RESPOND":
        cause = getattr(query, "cause", None)
        if cause is WindowCause.NULLIFY:
            return "需要使用【无懈可击】"
        if cause is WindowCause.DUEL:
            return _need_text("打出", "【杀】", _count_needed(engine, query, data))
        if cause is WindowCause.BARBARIAN_INVASION:
            return "需要打出【杀】，否则受到使用者 1 点伤害"
        if cause is WindowCause.ARROW_BARRAGE:
            return "需要打出【闪】，否则受到使用者 1 点伤害"
        if cause is WindowCause.BORROWED_SWORD:
            return "对合法目标使用【杀】，或放弃（放弃＝交出武器）"
        if cause is WindowCause.LORD_REQUEST:
            required = getattr(query, "required", None)
            card = {"slash": "【杀】", "dodge": "【闪】"}.get(required, "一张牌")
            return f"需要打出{card}"
        if cause is WindowCause.REJUDGE:
            return "打出一张手牌，代替此判定牌"
    return None


def _skill_hints(state: Any, query: Query) -> list[dict[str, str]]:
    """这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）及其文案。

    来源只有**候选与选择面槽位**里的公开事实：``ActivateSkill`` 候选自带 ``skill_id``；
    转化技的候选是虚拟牌（不带技能归属），故按
    :func:`~sgs.skills.heroes.conversion_offers` 的**来源**逐张对回技能（票 14），
    不在声明表里的虚拟牌退回 :attr:`SkillDef.virtual_type_ids` 的牌名匹配。
    """
    actor = int(query.actor)
    hints: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(skill_id: str) -> None:
        definition = _definition_of(state, skill_id.split(":", 1)[0])
        if definition is None or not definition.requirement or definition.skill_id in seen:
            return
        seen.add(definition.skill_id)
        hints.append(
            {
                "skill_id": definition.skill_id,
                "name": _skill_display_name(state, definition.skill_id) or definition.skill_id,
                "requirement": definition.requirement,
            }
        )

    for action in query.legal_actions:
        if getattr(action, "kind", None) == "ActivateSkill":
            add(str(action.skill_id))
    # 收**候选与选择面槽位**两处的虚拟牌（票 14）："提供一张 B"的虚拟 B 只在后者上，
    # 不收它这条窗口就列不出【龙胆】/【武圣】的文案。归属按
    # :func:`~sgs.skills.heroes.conversion_offers` **逐张核对来源**（需要【杀】只归【闪】、
    # 需要【闪】只归【杀】）；不在声明表里的虚拟牌（【八卦阵】"视为使用【闪】"等）
    # 退回按牌名匹配。
    for card in _virtual_cards(query):
        if not card.is_virtual:
            continue
        source = int(card.virtual_sources[0]) if len(card.virtual_sources) == 1 else None
        attributed = {
            skill_id
            for card_id, skill_id in conversion_offers(state, actor, card.type)
            if source is not None and int(card_id) == source
        }
        if attributed:
            for skill_id in attributed:
                add(skill_id)
            continue
        for instance in resolve_instances(state):
            if instance.owner != actor:
                continue
            definition = instance.definition
            if definition.virtual_type_ids and card.type in definition.virtual_type_ids:
                add(definition.skill_id)
    return hints


def _step_wording(steps: Any, step: str) -> str | None:
    """步骤名 → 逐步文案；名字里的**序号段**（末尾的 ``:<数字>``，可多段）先剥掉再查。

    执行器给"同一步问多次"补序号只为答案缓存不撞车（``seat:0`` / ``seat:1``，见
    :func:`~sgs.skills.flow._flow_option_id`），文案仍归那一步。【遗计】的"给谁"步更进一层：
    它的 ``skill_id`` 是手写的 ``yiji:give:<点号>:<轮号>``（票 05 的逐轮缓存键，两个数字段
    都必须留 —— 否则同一伤害点的第 2 轮会读回第 1 轮的答案）。故按**循环**剥末尾的数字段，
    单段（``seat:0``）与多段（``give:0:1``）走同一条判据。
    """
    found = steps.get(step)
    while found is None:
        stem, colon, index = step.rpartition(":")
        if not colon or not index.isdigit():
            break
        step = stem
        found = steps.get(step)
    return found


def _step_requirement(definition: Any, query: Query) -> str | None:
    """**多步技能**逐步的"需要做什么"（票 01）；取不到 ⇒ ``None``（退回一句话的文案）。

    键的取法只读询问里已有的标识，**不动询问协议**（故 Golden Replay 一行不改）：

    - ``SELECT_OPTION``：询问自带 ``skill_id`` 形如 ``lijian:user`` ⇒ 键 = 冒号后那段
      （末尾的序号段先剥掉，见 :func:`_step_wording`）；
    - ``SELECT_CARDS``：技能的那一步选牌 ⇒ 键 = ``"card"``。这一问归**哪条技能**已由
      :func:`_skill_of_query` 判过（询问 ``skill_id`` → 帧 ``running_skill`` → ``SKILL``
      帧 → 阶段体，与 ``SELECT_OPTION`` **同一条判据**），传进来的 ``definition`` 就是它，
      故这里**不再另读帧键**。

    旧判据读 ``frame.data["skill_id"]`` 并要求它等于 ``definition.skill_id``；而**被动帧
    （DAMAGE / USE）从不写这个键**——全仓只有 SKILL 帧与 JUDGE 帧写它，派发环写的是
    ``running_skill``。于是【流离】成本 / 【青龙】/【贯石斧】/【麒麟弓】/【寒冰剑】/
    【雌雄】/【遗计】的逐步文案在生产路径上**全部退回一句话 summary**（票 12 §1）。
    真会话回归见 ``tests/test_web.py::test_a_real_session_reaches_the_yiji_card_step_wording``。
    """
    steps = getattr(definition, "step_requirements", None)
    if not steps:
        return None
    raw = getattr(query, "skill_id", None)
    if raw:
        _head, sep, step = str(raw).partition(":")
        if sep:
            return _step_wording(steps, step)
    if getattr(query, "kind", None) == "SELECT_CARDS":
        return steps.get("card")
    return None


def query_context(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """这次询问的两行人话：**正在发生什么**（``label``）+ **需要做什么**（``requirement``）。

    ``requirement`` 的落点分三档：

    1. 询问能归属到技能（``skill_id`` / 帧 ``running_skill`` / ``SKILL`` 帧 / 阶段体）⇒
       读该技能定义上写死的 :attr:`~sgs.skills.catalog.SkillDef.requirement`（与技能代码同处）；
    2. 否则走**表现层**的卡牌窗口表（张数读唯一真源 :func:`sgs.effects.response_count`）；
    3. ``skill_hints`` 另列这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）的文案。

    纯派生、只读公开事实；不参与合法性判定。两个都取不到时返回 ``None``（界面不画）。
    """
    if query is None:
        return None
    state = engine.state
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    data: dict[str, Any] = frames[-1].data if frames else {}

    base = _event_context(engine, query)
    skill_id = _skill_of_query(engine, query, data)
    definition = _definition_of(state, skill_id) if skill_id is not None else None
    requirement = definition.requirement if definition is not None else None
    if definition is not None:
        # 分步技能：这一步的问法优先于一句话的 summary（票 01）。
        requirement = _step_requirement(definition, query) or requirement
    if requirement is None:
        requirement = _card_window_requirement(engine, query, data)
    hints = _skill_hints(state, query)
    if definition is not None:
        hints = [hint for hint in hints if hint["skill_id"] != definition.skill_id]

    if base is None and requirement is None and not hints:
        return None
    if base is None:
        base = {"actor": int(query.actor), "name": None, "targets": [], "label": None}
    base["requirement"] = requirement
    base["skill_hints"] = hints
    return base


def virtual_index(query: Query, state: Any | None = None) -> dict[str, list[dict[str, Any]]]:
    """转化候选的**来源牌索引**：``"<实体来源牌 id>": [{"card_id": <虚拟牌 id>, "skill_ids": [...]}]``。

    【龙胆】这类转化技的候选里带的 ``card`` 是**虚拟牌**（``card_id < 0``），而界面手牌区
    渲染的是**实体牌**（``card_id > 0``）。不建这份索引，玩家点【闪】时界面按实体 id
    查不到候选，【闪】就被画成"用不出"（报障 4 的原样）。

    只收**单来源**转化（源头只有一张实体牌）：这是标准版【武圣】【龙胆】【国色】【急救】
    的形状；多来源（【丈八蛇矛】的两张手牌当【杀】）需要"先选几张"的第二维，留给它自己
    的界面改造，不在本轮射程。

    **``skill_ids`` 按声明的源谓词逐张核对**（票 14）：旧实现按 ``SkillDef.virtual_type_ids``
    的**牌名并集**归属，而【龙胆】两个方向共用一个技能 id（``("dodge", "slash")``）⇒ 两个
    方向分不开、"点龙胆后所有手牌都亮"（用户实机）。现在读
    :func:`~sgs.skills.heroes.conversion_offers`（目标牌名 + 该声明的区域 / 牌面 / 时机
    谓词）：需要【杀】时只归【闪】、需要【闪】时只归【杀】。拿不到引擎（409 里附带的裸
    ``Query``）时退回牌名匹配——谓词需要 ``state``。

    ``SELECT_CARDS`` 的**"提供一张 B"面**（票 14）没有 ``action.card``，虚拟 B 全在
    ``query.slots`` 里，故两个来源都要收：缺了后者，【青龙】追问时【龙胆】/【武圣】的
    技能按钮就点不亮（"待答期间技能栏仍可点"这条验收会红）。
    """
    return _virtual_index(query, state, owner=int(query.actor))


def _virtual_index(
    query: Query, state: Any | None, *, owner: int
) -> dict[str, list[dict[str, Any]]]:
    """``virtual_index`` 的实现：``owner`` 显式传入（技能清单按**查看者**算，不是询问者）。"""
    typed: list[tuple[str, frozenset[str]]] = []
    if state is not None:
        for instance in resolve_instances(state):
            if instance.owner != owner:
                continue
            definition = instance.definition
            if definition.virtual_type_ids:
                typed.append((definition.skill_id, frozenset(definition.virtual_type_ids)))
    # 牌名 → {来源实体牌 id: 技能 id 元组}；按需现算（一次询问里通常只有一两种牌名）。
    offers: dict[str, dict[int, tuple[str, ...]]] = {}

    def offerers(card: Any) -> list[str]:
        if state is None:
            return [skill_id for skill_id, types in typed if card.type in types]
        table = offers.get(card.type)
        if table is None:
            grouped: dict[int, list[str]] = {}
            for source, skill_id in conversion_offers(state, owner, card.type):
                grouped.setdefault(source, []).append(skill_id)
            table = {source: tuple(ids) for source, ids in grouped.items()}
            offers[card.type] = table
        return list(table.get(int(card.virtual_sources[0]), ()))

    out: dict[str, list[dict[str, Any]]] = {}
    for card in _virtual_cards(query):
        if not card.is_virtual or len(card.virtual_sources) != 1:
            continue
        bucket = out.setdefault(str(int(card.virtual_sources[0])), [])
        if not any(item["card_id"] == card.card_id for item in bucket):
            bucket.append({"card_id": card.card_id, "skill_ids": offerers(card)})
    return out


def _virtual_cards(query: Query) -> list[Any]:
    """这次询问里的全部牌面：候选带的 ``card`` 与**选择面槽位**上的牌（票 14）。

    "提供一张 B"的虚拟 B 只出现在后者的 ``slots[].card`` 上（``SELECT_CARDS`` 的候选
    只带面内位置），故两个来源都要读——顺序固定（候选在前、槽位在后）以保证可复现。
    """
    cards: list[Any] = []
    for action in query.legal_actions:
        card = getattr(action, "card", None)
        if card is not None:
            cards.append(card)
    for slot in getattr(query, "slots", ()) or ():
        if slot.card is not None:
            cards.append(slot.card)
    return cards


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


def _mask_watched_faces(payload: dict[str, Any]) -> None:
    """把**观看来的牌面**从一份不归本座位的载荷里抹掉（票 15；`CODING_STANDARDS.md` §1 读取方）。

    ``SELECT_CARDS`` 的 ``slots[].card`` 与询问的 ``revealed`` 是**授予本次应答者观看**的
    牌面（【遗计】看牌堆顶两张、将来【观星】看牌堆顶若干）。引擎侧它们不进任何 ``PlayerView``、
    不落带牌面的 delta；接入层这条"当前挂起"的载荷因此也必须只在**该询问的挂起者就是人类座位**
    时才带牌面——否则任何一次"人类不是挂起者"的下发（陈旧应答的 409 现状等）都会把别人
    观看的牌面直接送出去。

    公开事实（谁在看、有几个位置）保留：``slots`` 仍按**位置**下发（``card=None``），
    与 :func:`~sgs.state.project` 对隐藏区域的"只有位置"是同一形状。
    """
    slots = payload.get("slots")
    if isinstance(slots, list):
        payload["slots"] = [
            {**slot, "card": None} if isinstance(slot, dict) else slot for slot in slots
        ]
    payload["revealed"] = []


def pending_payload(session: Session | Query | None) -> dict[str, Any] | None:
    """一次询问的**可应答形态**——带候选、带询问者自己的视角（前端只 pick，不自己拼 Action）。

    收 ``Session``（取它的当前挂起）、``Query``（409 里附带的现状）或 ``None``（无挂起）。

    ``legal_actions`` 保留（既有测试、超时兜底与"提交必须原样取自候选"的自我核对都读它），
    ``candidate_index`` 是本轮新增的**索引形状**：界面用它手选目标，不再逐条铺候选按钮。

    **观看私密（票 15）**：收 ``Session`` 且它的当前挂起**不属于人类座位**时，这份载荷抹掉
    ``slots[].card`` / ``revealed``（见 :func:`_mask_watched_faces`）——"当前挂起"是给人类
    座位应答的，别人的询问不该连牌面一起下发。裸 ``Query``（409 里附带的那份现状，构造时
    本就只为人类座位）走原样，不改既有的 :func:`query_context` 单测形状。
    """
    if session is None:
        return None
    query = session.pending if isinstance(session, Session) else session
    if query is None:
        return None
    payload = query.model_dump(mode="json")
    if isinstance(session, Session) and int(query.actor) != int(session.human_seat):
        _mask_watched_faces(payload)
    payload["legal_actions"] = [action.model_dump(mode="json") for action in query.legal_actions]
    payload["candidate_index"] = candidate_index(query)
    # 询问窗口的"谁对谁做了什么"：从**结算栈顶帧 + 询问本身**派生的表现层事实。
    # 只有拿得到 ``Session``（= 拿得到引擎）时才算；409 里附带的那份现状（裸 ``Query``）为 None。
    payload["context"] = (
        query_context(session.engine, query) if isinstance(session, Session) else None
    )
    payload["option_seats"] = option_seats(query)
    # 这一步是不是"提供一张 B"（票 14）：非空 = B 的牌名 id。字段在记录流里被 ``exclude``，
    # 故**不进** Golden Replay 的字节；界面靠它把这一类选择面**就地**铺到本人的可见区域上。
    payload["provide_type"] = getattr(query, "provide_type", None)
    # 转化候选的**来源手牌**索引（【龙胆】：点【闪】要能命中"当【杀】"的那条候选）。
    # 拿不到引擎（409 里附带的裸 ``Query``）时仍下发来源 → 虚拟牌的别名，只是 ``skill_ids`` 为空。
    payload["virtual_index"] = _virtual_index(
        query,
        session.engine.state if isinstance(session, Session) else None,
        owner=int(query.actor),
    )
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
