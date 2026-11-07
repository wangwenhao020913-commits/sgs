"""自制包·锋·于禁（``feng-yujin`` 票 01）：【定乱】+「【定乱】牌」的标签自身效果。

数据面住 ``heroes.homebrew.json``（``pack_id = homebrew``）。本模块只有**钩子族**的两个
定义：【定乱】（``Timing.ROUND_START`` 上的可选技，**非共享**）与【定乱】牌的**标签自身效果**
（``hero.feng_yujin.dingluan_mark``，``CarrierKind.CARD_TAG``）。【节钺】是 A 族主动技，
按族归 :mod:`sgs.skills.active`（与神甘宁【魄袭】住 ``active.py``、`【劫营】`+「营」效果住
`hero_lei.py` 同一分工）。

牌面（用户 `D:\\SGS_HEROS\\自制包\\锋·于禁\\技能.txt` 第 1–2 行，逐字）：

    锋·于禁 4/4 魏
    【定乱】：每名角色限1次，每轮开始时，你可以展示一名角色至多X张牌(X为3-此前此技能发动过的次数)并标记为【定乱】牌（【定乱】牌不能被当作【杀】使用或打出直到进入弃牌堆，当【定乱】牌非因使用或打出而进入弃牌堆时，拥有者摸1张牌）

读法（用户 2026-11-07 裁 **B2 + D1**，两票同批；``04`` §3 的 A1 改判，补记见
``.scratch/feng-yujin/issues/04-d1-dingluan-readings.md``）：

    每轮开始时，**于禁**可以展示**一名角色**至多 X 张牌（X = 3 − 此前发动过的次数），
    并标记为【定乱】牌；**每名角色整局至多被定乱 1 次**。

- **B2 非共享（== 已裁）**：【定乱】是**于禁自己的技能**（``SkillDef.shared`` 保持默认
  ``False``），每轮开始时**只问于禁**——别的座位连候选都没有。改判原因：共享技把这条实例
  借给了全场，座位 1 的庞统也能自己发动并亮牌，"放弃 = 无事发生"因此永远不成立；
- **D1 目标侧限次（== 已裁）**：限次记在**被展示者**头上——每名角色整局至多被定乱 1 次，
  于禁可以反复发动；目标池因此排除"已经被定乱过"的座位（含自己）；【节钺】的标记**不**
  占用这份记账（两条技能各记各的）；
- **X = 3 − 已被定乱过的角色数**（= 3 − 此前发动次数：一次发动恰好定乱一名角色；记账落
  在定乱实例的座位位段 bit 24+i，置位 = "座位 i 已经被定乱过"）、发动那一刻现算、X=0
  不能发动；
- **R3=A**：展示的牌由**发动者**（= 于禁）挑——目标的手牌按位置**盲挑**，装备区可见可选；
- **R4=A**：被标记的牌不能作为【杀】使用 / 打出（含本来就是【杀】与转化；多来源也算），
  读点见 ``effects/actions.py`` 的 :func:`~sgs.effects.forbidden_as_slash`；
- **R5**：拥有者只看**手牌 / 装备区**（``MoveEvent.from_zone``），其他区不摸；
- **R6**：标签**公开**（``CardTagVisibility.PUBLIC``）。

栈：标签本身住 ``sgs.data.CARD_TAG_TYPES[CardTagType.DINGLUAN]``（清除触发 =
``ENTER_DISCARD``、读取方 = ``FORBIDDEN_AS_SLASH``、效果定义 = 本模块的
``hero.feng_yujin.dingluan_mark``）；标签的**写入**在【定乱】与【节钺】两处（后者在
``active.py``，写入的 ``skill_id`` 恒取 :data:`DINGLUAN`，标签名因此恒显示「定乱」）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import CardTagType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.skills.catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    instance_key,
    resolve_instances,
)
from sgs.skills.flow import seat_choice
from sgs.skills.move_after import move_event
from sgs.state import CardTag, GameState, MoveReason, Zone
from sgs.timing import Timing

__all__ = [
    "DINGLUAN",
    "DINGLUAN_LIMIT",
    "DINGLUAN_MARK",
    "DINGLUAN_TAG",
    "mark_dingluan_cards",
    "yujin_skill_definitions",
]

FENG_YUJIN = "feng_yujin"
"""锋·于禁的武将 id（``heroes.homebrew.json`` 的 ``id``）。"""

DINGLUAN = hero_skill_id(FENG_YUJIN, "dingluan")
"""【定乱】的全限定技能 id。"""

DINGLUAN_MARK = hero_skill_id(FENG_YUJIN, "dingluan_mark")
"""「【定乱】牌」的**标签自身效果**定义 id（``CardTagSpec.effect`` 指向它）。

它是 ``CATALOG`` 里一条 ``carrier = CarrierKind.CARD_TAG`` 的定义：**不是任何角色拥有
的技能**（武将面上没有它、按钮区也不列），只在有【定乱】标签时由 ``resolve_instances``
按 ``GameState.card_tags`` 产出——**不随于禁身死 / 失去技能消失**（用户 2026-11-07 签的
E2 口径：牌面写"直到进入弃牌堆"，效果只随标签消失而消失）。
"""

DINGLUAN_TAG = CardTagType.DINGLUAN
"""「【定乱】牌」的标签类型（登记表在 ``sgs.data.CARD_TAG_TYPES``）。"""

DINGLUAN_LIMIT = 3
"""X 的上限（牌面写死"3 − 此前此技能发动过的次数"）。"""

_DINGLUAN_SHIFT = 24
"""座位位段的起始位（bit 24 + i = 座位 i 已经被定乱过）。

**为什么从 24 起**：``state.skill_state`` 的同一个整数里还住着别的技能的位段——
A 族的阶段位（``_PHASE_STATE_MASK`` = bit 0..22：``_USED`` / 仁德目标位段 / 黄天座位位段…）
在**每个出牌阶段开始**被清、``_TURN_USED``（bit 5）在**每个回合末**被清。本技能要的是
**整局**位：放在 0..22 会被阶段复位抹掉（实测：发动一次后出牌阶段一开始就"忘了"），
故取 24..31（`int` 无位宽上限）。"""

_ASK_DINGLUAN_PICKED = "dingluan:card"
"""【定乱】选牌步的**询问位置**（帧内缓存按 ``(owner, key)`` 分桶）。

``owner`` 是常量（不随座位变），座位只带在帧内 ``key`` 上（``dingluan:card:<座位>``）。
**保留座位分桶**不是旧"共享技"语义的残留（票 07 §6.1 已允许保留）：它与 ``target`` /
``done`` 两个帧内键同一口径，重入时不与别的座位串答案。位置常量本身只一处
（`tests/test_ask_points.py` 的机械闸要求入口常量两两不同）。
"""

_SEAT_BITS = 0xFF << _DINGLUAN_SHIFT
"""座位位段：bit (24 + i) = 座位 i **已经被定乱过**（整局至多 1 次，限次在目标侧）。"""


def _dingluan_instance(state: GameState):
    """【定乱】的那条实例（没有 = 技能不在场上 / 于禁阵亡后失去技能）。"""
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.skill_id == DINGLUAN:
            return instance
    return None


def _used_bits(state: GameState) -> int:
    """已经被定乱过的座位位段（整局、按座位；技能不在场 = 0）。"""
    instance = _dingluan_instance(state)
    if instance is None:
        return 0
    bits = int(state.skill_state.get(instance_key(instance), 0))
    return bits & _SEAT_BITS


def _seat_used(state: GameState, seat: int) -> bool:
    """座位 ``seat`` 是否**已经被定乱过**（= 目标侧整局限次的唯一真源）。"""
    return bool(_used_bits(state) >> (_DINGLUAN_SHIFT + seat) & 1)


def _marked_characters(state: GameState) -> int:
    """已被定乱过的角色数 = 已置位的座位数（实现仍是 ``bit_count``）。"""
    return _used_bits(state).bit_count()


def _remaining_x(state: GameState) -> int:
    """X = 3 − 已被定乱过的角色数（发动那一刻现算，下限 0）。"""
    return max(0, DINGLUAN_LIMIT - _marked_characters(state))


def _mark_dingluan_used(state: GameState, seat: int) -> None:
    """记一次"座位 ``seat`` **已经被定乱过**"（整局位，不随阶段 / 回合复位）。"""
    instance = _dingluan_instance(state)
    if instance is None or not 0 <= seat < 8:
        return
    key = instance_key(instance)
    state.skill_state[key] = int(state.skill_state.get(key, 0)) | (
        1 << (_DINGLUAN_SHIFT + seat)
    )


def _own_card_count(state: GameState, seat: int) -> int:
    """``seat`` 的**手牌 + 装备区**张数（R3 / R6 的目标池判据）。"""
    player = state.player(seat)
    return len(player.hand) + len(player.equip)


def _dingluan_targets(state: GameState) -> tuple[int, ...]:
    """【定乱】的目标池：存活、**至少有一张可展示的牌**、且**还没被定乱过**的角色。

    目标侧限次（"每名角色整局至多被定乱 1 次"）就落在最后一条：已被定乱过的座位——
    包括于禁自己——整局不再进池（记账真源 = :func:`_seat_used`）。
    """
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and _own_card_count(state, player.seat) > 0
        and not _seat_used(state, player.seat)
    )


def _dingluan_condition(host: Any, frame: Any) -> bool:
    """这一问的**持有者**还能不能发动【定乱】（非共享技：只有于禁会被问）。

    "可以" ⇒ 非锁定。条件做两件事：先**自校持有者**——``MATCH_OWNER_KEY`` 是条件求值期写下的
    "这条实例归谁"，必须真是定乱实例的持有者（非共享技不写 ``ASK_SEAT_KEY``，共享技时代的
    "被问的座位"在这里没有意义）；再挡"这一问没有意义"：自己存活、``X > 0``、且**过滤后的
    目标池非空**（目标侧限次已由票 08 落在 :func:`_dingluan_targets`；A1 的"发动者已发动"
    整局限次已删除——于禁可以反复发动）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    instance = _dingluan_instance(host.state)
    if instance is None or instance.owner != owner:
        return False
    state = host.state
    if not state.player(owner).alive:
        return False
    if _remaining_x(state) <= 0:
        return False
    return bool(_dingluan_targets(state))


def _dingluan(host: Any, frame: Any) -> Any:
    """【定乱】的效果主体（手写两段：选一名角色 → 展示并标记至多 X 张牌）。

    非共享技：``MATCH_OWNER_KEY`` 既是**发动者**（= 于禁，派发环上唯一会被问的座位）又是
    技能实例持有者（= 标签来源，写进 ``CardTag.owner``），两者恒等，故函数里只有一个名字
    ``owner``。两段的答案按座位收在帧内键里（``dingluan:target:<座位>`` /
    ``dingluan:picked:<座位>``）：``ask_selection`` 的 ``owner=`` 实参是常量
    ``_ASK_DINGLUAN_PICKED``，座位只出现在 ``skill_id`` 与帧内 ``key`` 上，重入时不与别的
    座位串答案。
    """
    state = host.state
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return None
    owner = int(owner)
    # 非共享技：这一问的"我"就是实例持有者（票 07）。选人与选牌的询问都问 owner；
    # "已被定乱过"的记账落在 target 上（票 08：限次在目标侧，不在发动者侧）。
    x = _remaining_x(state)
    if x <= 0:
        return None
    timing = Timing.ROUND_START

    # ---- 第 1 步：选一名角色（含自己）------------------------------------
    target_key = f"dingluan:target:{owner}"
    if target_key not in frame.data:
        pool = _dingluan_targets(state)
        if not pool:
            return None
        choice = host.ask_option(
            frame,
            owner,
            timing,
            skill_id=f"dingluan:seat:{owner}",
            options=tuple(seat_choice(seat) for seat in pool),
        )
        if host.suspended(choice):
            return "stay"
        frame.data[target_key] = int(str(choice.choice).split(":", 1)[1])
    target = int(frame.data[target_key])

    # ---- 第 2 步：展示该角色至多 X 张牌（R3=A：发动者挑；隐藏手牌盲挑）----
    picked_key = f"dingluan:picked:{owner}"
    if picked_key not in frame.data:
        pool_size = _own_card_count(state, target)
        if pool_size <= 0:
            return None
        spec_hi = min(x, pool_size)
        picked = host.ask_selection(
            frame,
            owner=_ASK_DINGLUAN_PICKED,
            key=f"dingluan:card:{owner}",
            actor=owner,
            timing=timing,
            spec=_dingluan_spec(spec_hi),
            # 面只构造一次（引擎按 (owner, key) 缓存）：隐藏手牌的位置打乱消耗一次
            # `select` 子流，故绝不能因重入而重跑。
            build_face=lambda: host.selection_face(
                target,
                (Zone.HAND, Zone.EQUIP),
                reveal_hand=(target == owner),
            ),
        )
        if host.suspended(picked):
            return "stay"
        frame.data[picked_key] = tuple(int(card_id) for card_id in picked)
    picked = tuple(int(card_id) for card_id in frame.data[picked_key])
    if not picked:
        return None

    # ---- 效果：标记为【定乱】牌 → 展示（一起做，无询问）→ 记"目标已被定乱过"----
    done_key = f"dingluan:done:{owner}"
    if not frame.data.get(done_key):
        mark_dingluan_cards(host, owner, picked)
        for card_id in picked:
            host.reveal_card(frame, target, state.card(card_id))
        _mark_dingluan_used(state, target)
        frame.data[done_key] = True
    return None


def _dingluan_spec(hi: int):
    """选牌规格：自己 / 别人的**手牌 + 装备区**，1..``hi`` 张、不可放弃（"至多"含下限 1）。"""
    from sgs.state import SelectionSpec

    return SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, hi), optional=False)


def mark_dingluan_cards(host: Any, writer: int, card_ids: tuple[int, ...]) -> None:
    """把这批牌标成【定乱】牌（标签的**唯一写入点**，【定乱】与【节钺】共用）。

    ``writer`` = 技能实例持有者（``CardTag.owner``；标签自身效果的"关于我"按它认领）；
    ``skill_id`` 恒取 :data:`DINGLUAN`——标签名因此恒显示「定乱」（不论由哪条技能写下）。
    已有同类标签的牌幂等跳过（``add_card_tag`` 返回假）。
    """
    for card_id in card_ids:
        host.add_card_tag(
            int(card_id),
            CardTag(tag_type=DINGLUAN_TAG, owner=writer, skill_id=DINGLUAN),
        )


def _is_tagged(state: GameState, card_id: int, writer: int) -> bool:
    return any(
        tag.tag_type is DINGLUAN_TAG and tag.owner == writer
        for tag in state.tags_of(int(card_id))
    )


def _mark_condition(host: Any, frame: Any) -> bool:
    """「非因使用 / 打出而进入弃牌堆」的触发条件（标签自身效果，**强制段**）。

    ``MoveEvent`` 的 ``reason`` / ``to_zone`` 是唯一判据（使用与打出的移动都记
    ``MoveReason.USE``）；"关于我"按 ``tag.owner`` 与 ``MATCH_OWNER_KEY`` 认领——同一张
    牌若被两名持有者的标签同时覆盖，各认各的（正常局面只有于禁一条实例）。
    """
    event = move_event(frame)
    if event is None or event.to_zone.zone is not Zone.DISCARD:
        return False
    if event.reason is MoveReason.USE:
        return False
    writer = frame.data.get(MATCH_OWNER_KEY)
    if writer is None:
        return False
    return any(_is_tagged(host.state, card.card_id, int(writer)) for card in event.cards)


def _mark_draw(host: Any, frame: Any) -> Any:
    """拥有者摸牌：这批移动里**每张**【定乱】牌各摸 1 张（R5：只看手牌 / 装备区）。

    用户 2026-11-07 改判（覆盖票 03 / 04 的"一次移动只摸一张"）：同一批移动里带 N 张
    【定乱】牌 ⇒ 摸 N 张，每张独立结算一次。派发环"每技能一次"保证本回调每次移动最多
    跑一遍，故计数只按**这一批**的 ``event.cards`` 来；同批里未带标记的牌不额外摸。
    """
    state = host.state
    event = move_event(frame)
    if event is None:
        return None
    if event.from_zone.zone not in (Zone.HAND, Zone.EQUIP):
        return None
    seat = event.from_zone.seat
    if seat is None:
        return None
    seat = int(seat)
    if not state.player(seat).alive:
        return None
    writer = frame.data.get(MATCH_OWNER_KEY)
    if writer is None:
        return None
    marked = sum(
        1 for card in event.cards if _is_tagged(state, card.card_id, int(writer))
    )
    if marked <= 0:
        return None
    host.draw_for_hand(frame, seat, marked)
    return None


def yujin_skill_definitions() -> tuple[SkillDef, ...]:
    """锋·于禁的**钩子族**两条定义（【节钺】在 ``active.py``；数据面在 ``homebrew`` 表）。"""
    return (
        SkillDef(
            skill_id=DINGLUAN,
            locked=False,
            carrier=CarrierKind.HERO,
            hooks=(
                Hook(
                    Timing.ROUND_START,
                    condition=_dingluan_condition,
                    handler=_dingluan,
                ),
            ),
            requirement=(
                "每名角色限1次，每轮开始时，你可以展示一名角色至多X张牌"
                "(X为3-此前此技能发动过的次数)并标记为【定乱】牌"
            ),
            step_requirements={
                "seat": "选择一名角色（可以是自己）",
                "card": "展示该角色至多 X 张牌并标记为【定乱】牌（X = 3 − 已被定乱过的角色数）",
            },
        ),
        SkillDef(
            skill_id=DINGLUAN_MARK,
            locked=False,
            carrier=CarrierKind.CARD_TAG,
            hooks=(
                Hook(
                    Timing.MOVE_AFTER,
                    condition=_mark_condition,
                    handler=_mark_draw,
                    forced=True,
                ),
            ),
        ),
    )
