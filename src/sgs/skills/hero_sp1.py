r"""SP武将组1 的武将技能：马良【自书】【应援】（``sp1`` 包；sp1-sunshangxiang-maliang 票 01）。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 马良【自书】= 本模块（``MOVE_AFTER`` ×2 + ``TURN_END`` + ``TURN_START`` 簿记：锁定技、
  零询问、没有流水线）；
- 马良【应援】= 本模块（``MOVE_AFTER`` 钩子 + 一步选座位流水线）；
- 同一包的另一名武将（SP孙尚香）的【良助】【返乡】住本模块（票 02 追加），
  ☆【枭姬】住 :mod:`sgs.skills.move_after`（D 族"失去牌后"的唯一装载点，与马云騄【马术】
  住 :mod:`sgs.skills.target_change` 同一条理由）。

牌面权威 = **用户资产**（``D:\\SGS_HEROS\\SP武将组1\\马良\\技能.txt``；用户 2026-11-18 逐条签）：

    自书：锁定技，①其他角色的结束阶段结束时，你将本回合获得的牌置入弃牌堆；
          ②你的回合内，当你不因此技能获得牌时，你摸一张牌。
    应援：当你于回合内使用牌结算结束置入弃牌堆时，你可以将之交给一名其他角色
          （每种牌名的牌每回合限一次）。

用户 2026-11-18 签的口径（票 01 §0.2 / `03-zishu-card-tag-ruling.md`）：

- **应援按移动版现行 = OL 历史第 2 版**（用户资产）实现；OL 现行是**另一个技能**
  （每种类别 / 让目标随机从牌堆获得同类型牌），**不实现**、账本留档（A1 事实成立）；
- **自书① 的结算点 = ``TURN_END``**（用户原话"当前角色回合结束时"）**+「回合角色存活 ∧
  回合角色 ≠ 我」守卫**——身份局里死者是回合角色时 ``steps.py:death_before_start`` 会补发
  一条 ``TURN_END``，没有守卫就会违背"当前角色阵亡则不弃置"；
- **自书标记 = 牌标签**（``CardTagType.ZISHU`` + 新读取方 ``CardTagReader.SKILL_LOCAL``）：
  寿命两条都由**载体自己**兑现——离开手牌（``LEAVE_ZONE``，本模块的 ``MOVE_AFTER`` 调
  ``clear_card_tags(..., cards=…)``）+ 回合开始时清除（``TURN_START`` 簿记钩子）；
- **自书② 是「获得牌」不是「摸牌」**（用户逐字补一句）：判据 = :func:`gains_card`（落点
  是自己手牌），**来源不限**（摸牌 / 顺手牵羊 / 交给 / 遗计都算）；粒度 = **逐事件**
  （一次移动 N 张只摸 1 张）；
- **「每种牌名」= 产物牌名**（转化 / 虚拟牌按产物），**发动（提交）即占位**、放弃不占位；
  交出的是**进入弃牌堆的那批来源实体牌**（丈八蛇矛的两张手牌当【杀】⇒ 交 2 张）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import CardTagClear, CardTagType, SkillType, hero_skill_id
from sgs.effects import CarrierKind, locate
from sgs.resolution import Control, Frame
from sgs.state import CardTag, GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import PickSeats, SkillFlow
from .move_after import gains_card, move_event

__all__ = [
    "MALIANG_SKILL_DEFS",
    "ZISHU",
    "ZISHU_TAG",
    "YINGYUAN",
    "YINGYUAN_FLOW",
    "maliang_skill_definitions",
    "sp1_skill_definitions",
]

ZISHU = hero_skill_id("maliang", "zishu")
"""马良【自书】的全限定技能 id。"""

YINGYUAN = hero_skill_id("maliang", "yingyuan")
"""马良【应援】的全限定技能 id。"""

ZISHU_TAG = CardTagType.ZISHU
"""「自书」牌面标记的标签类型（登记表在 :data:`sgs.data.CARD_TAG_TYPES`）。"""

_ZISHU_LAST = "zishu_last"
"""帧私有：已为哪一次获得事件追加过那"单独一张"（同一个帧会被多次移动复用 ⇒ 按事件去重）。"""

_ZISHU_GUARD = "zishu_guard"
"""帧私有：本次摸牌**是自书自己摸的**（嵌套派发期间置位，摸完清掉）。"""

_ZISHU_BATCH = "zishu_batch"
"""帧私有：本次 `TURN_END` 要弃置的那批牌（在飞帧定格，挂起续跑不重算）。"""

_ZISHU_SWEPT = "zishu_swept"
"""帧私有：本次 `TURN_START` 的到期清扫已经做过（重入不重扫）。"""

_YINGYUAN_DONE = "yingyuan_done"
"""帧私有：本次发动的"牌名占位"已经落过（提交点重入不重复追加）。"""

_YINGYUAN_MOVED = "yingyuan_moved"
"""帧私有：这张牌已经交给目标（搬牌挂起后的续跑不重搬）。"""

_YINGYUAN_CARD = "yingyuan_card"
"""帧私有：本次触发对应的**那张被使用的牌**。

`_resume_frame` 会把帧语义键 ``card`` 重写成 ``None``（续跑帧的形状与"帧外移动"一致），
而**帧私有数据**会被继承 ⇒ "这一问在问哪张牌"必须另存一份，否则挂起续跑后条件翻假、
整条流水线被静默丢掉（【清俭】用 `MoveEvent` 避开同一个坑；本技能还要**产物牌名**，
只能把牌面本身记下来）。写的是自己的私有槽、且幂等（只在缺失时写一次）。
"""

_REQUIREMENT_ZISHU = (
    "锁定技，①其他角色的结束阶段结束时，你将本回合获得的牌置入弃牌堆；"
    "②你的回合内，当你不因此技能获得牌时，你摸一张牌。"
)
"""【自书】的牌面逐字（用户资产；``requirement`` 槽位）。"""

_REQUIREMENT_YINGYUAN = (
    "当你于回合内使用牌结算结束置入弃牌堆时，你可以将之交给一名其他角色"
    "（每种牌名的牌每回合限一次）。"
)
"""【应援】的牌面逐字（用户资产 = 移动版现行 = OL 历史第 2 版；``requirement`` 槽位）。"""


# ---------------------------------------------------------------------------
# 【自书】：锁定技，三段（打标记 / 弃置 / 寿命清扫）+ ② 的摸牌
# ---------------------------------------------------------------------------


def _tagged_cards(state: GameState, seat: int, tag_type: CardTagType) -> tuple[int, ...]:
    """``seat`` **手牌里**带 ``tag_type`` 标签的牌 id（按手牌序）。"""
    return tuple(
        card_id
        for card_id in state.player(seat).hand
        if any(tag.tag_type is tag_type for tag in state.tags_of(card_id))
    )


def _leaves_my_hand(event: Any, owner: int) -> bool:
    """这次移动是不是"牌离开我的手牌"（寿命②的判据；手牌 → 自己的手牌如重排不算）。"""
    if event.from_zone.zone is not Zone.HAND or event.from_zone.seat != owner:
        return False
    return not (
        event.to_zone.zone is Zone.HAND and event.to_zone.seat == owner
    )


def _zishu_move_condition(host: Any, frame: Frame) -> bool:
    """触发条件：这次移动**关于我**——我获得了牌，或牌离开了我的手牌。

    ① 的"回合外获得"与 ② 的"回合内获得"共用这一问（两者互斥，由处理段按
    `state.turn_seat` 分支）；寿命②（离开手牌即清）也在这里一次判掉。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    event = move_event(frame)
    if event is None:
        return False
    return gains_card(event, owner) or _leaves_my_hand(event, owner)


def _zishu_move(host: Any, frame: Frame) -> Control:
    """一次移动里的三段（互斥，按事件判）：

    - **离开我的手牌** ⇒ 寿命②：清掉这批牌上的「自书」标记（``LEAVE_ZONE`` 的触发点由
      载体自己调——登记表不替它接）；
    - **回合外获得** ⇒ ①：给这次获得的**每一张**牌打标记（逐张，用户裁定"给这个牌一个标记"）；
    - **自己的回合内获得** ⇒ ②：摸一张（单独一次摸牌，防自递归）。
    """
    owner = int(frame.data["skill_owner"])
    event = move_event(frame)
    if event is None:
        return None
    if _leaves_my_hand(event, owner):
        host.clear_card_tags(CardTagClear.LEAVE_ZONE, cards=event.card_ids)
    if not gains_card(event, owner):
        return None
    state = host.state
    if state.turn_seat is None:
        # 没有"当前回合角色"（开局发牌一类）：既不是"其他角色的回合"、也不是"你的回合"。
        return None
    if int(state.turn_seat) != owner:
        for card_id in event.card_ids:
            host.add_card_tag(
                int(card_id),
                CardTag(tag_type=ZISHU_TAG, owner=owner, skill_id=ZISHU),
            )
        return None
    return _zishu_draw(host, frame, owner, event)


def _zishu_discard_condition(host: Any, frame: Frame) -> bool:
    """① 的结算条件：**其他角色的回合结束** ∧ 回合角色**存活** ∧ 我手里有带标记的牌。

    「存活」这一条是用户 2026-11-18 明写的「如果当前角色阵亡，则不会弃置这些牌」——
    身份局里死者是回合角色时，``steps.py`` 的 ``death_before_start`` 会补发一条
    ``TURN_END``，没有守卫就会在那一刻弃置。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    state = host.state
    seat = state.turn_seat
    if seat is None or int(seat) == owner:
        return False
    if not state.player(int(seat)).alive:
        return False
    return bool(_tagged_cards(state, owner, ZISHU_TAG))


def _zishu_discard(host: Any, frame: Frame) -> Control:
    """① 把**本回合获得的那些牌**（= 带标记的手牌）置入弃牌堆。

    这批牌在飞帧里定格（``_ZISHU_BATCH``）：弃置会内联派发"失去牌后"并可能挂起，
    续跑时按同一批牌重入（``move_in_frame`` 用 ``_MOVE_DONE`` 认出"这次移动已搬完"）。
    """
    owner = int(frame.data["skill_owner"])
    state = host.state
    batch = frame.data.get(_ZISHU_BATCH)
    if batch is None:
        batch = _tagged_cards(state, owner, ZISHU_TAG)
        frame.data[_ZISHU_BATCH] = batch
    if not batch:
        return None
    return host.move_in_frame(
        frame,
        state.refs(batch),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=owner,
        actor=owner,
    )


def _zishu_sweep_condition(host: Any, frame: Frame) -> bool:
    """寿命①的条件：我名下**还有**带标记的牌（没有 ⇒ 这条簿记钩子零动作）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    return bool(_tagged_cards(host.state, int(owner), ZISHU_TAG))


def _zishu_sweep(host: Any, frame: Frame) -> None:
    """寿命①：**回合开始时清除**（用户裁定）——由载体自己的簿记钩子兑现，清到零。

    为什么不是 `CardTagClear.TURN_START`：登记表是一类一格（``clear_trigger`` 单值），
    而本技能要两条寿命；「回合开始时清除」由技能自己清扫即可表达，**不改共享数据模型**
    （样板 = 神诸葛亮【狂风】/【大雾】的 `TURN_START` 到期清扫）。
    """
    if frame.data.get(_ZISHU_SWEPT):
        return None
    frame.data[_ZISHU_SWEPT] = True
    owner = int(frame.data["skill_owner"])
    ids = _tagged_cards(host.state, owner, ZISHU_TAG)
    if ids:
        host.clear_card_tags(CardTagClear.LEAVE_ZONE, cards=ids)
    return None


def _zishu_event_key(event: Any) -> tuple[Any, ...]:
    """一次获得事件的帧内去重键（同一个帧会被多次移动复用 ⇒ 不能只看"摸过没有"）。"""
    return (event.to_zone.seat, event.card_ids)


def _zishu_draw(host: Any, frame: Frame, owner: int, event: Any) -> Control:
    """② 摸一张（**单独的一次摸牌**，不是把本次获得数 +1）。

    重入纪律（照【展骥】`_zhanji`）：``_ZISHU_LAST`` 按事件去重，``_ZISHU_GUARD`` 在嵌套
    派发期间置位（自书自己摸的那一张不再触发自己）。
    """
    key = _zishu_event_key(event)
    if frame.data.get(_ZISHU_LAST) == key:
        # 挂起后的续跑：同一次事件已经摸过，收掉 guard 返回（既不重摸也不多问）。
        frame.data.pop(_ZISHU_GUARD, None)
        return None
    if frame.data.get(_ZISHU_GUARD):
        # 嵌套派发：正在搬的是**自书自己摸出来的那一张**，不再触发自己。
        return None
    frame.data[_ZISHU_LAST] = key
    frame.data[_ZISHU_GUARD] = True
    control = host.draw_for_hand(frame, owner, 1)
    if control == "stay":
        return "stay"
    frame.data.pop(_ZISHU_GUARD, None)
    return None


_ZISHU_DEF = SkillDef(
    skill_id=ZISHU,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # 一次移动里的三段（打标记 / 清标记 / 摸一张）合并**一条**钩子：三者都由同一个
        # `MoveEvent` 判、互斥，且"是否关于我"是一条判据（与【连营】【枭姬】的
        # `condition` 同姿态；同技能同时机挂多条钩子会把"关于我"的探针拆成互相矛盾的两问）。
        Hook(Timing.MOVE_AFTER, handler=_zishu_move, condition=_zishu_move_condition),
        # ① 其他角色的回合结束时弃置那批牌（锁定技：不问，`forced` 只表达"这一段强制"）。
        Hook(Timing.TURN_END, handler=_zishu_discard, condition=_zishu_discard_condition),
        # 寿命① 回合开始时清除（簿记段：静默、不进玩家的面）。
        Hook(
            Timing.TURN_START,
            handler=_zishu_sweep,
            condition=_zishu_sweep_condition,
            forced=True,
            bookkeeping=True,
        ),
    ),
    requirement=_REQUIREMENT_ZISHU,
)
"""【自书】：锁定技，五条钩子、零询问（`locked=True` ⇒ 派发环的 forced 分支直接跑）。"""


# ---------------------------------------------------------------------------
# 【应援】：使用牌结算结束置入弃牌堆 → 交给一名其他角色（每种牌名每回合一次）
# ---------------------------------------------------------------------------


def _other_alive_seats(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """「一名**其他**角色」的目标池：除持有者外的存活座位（座位序）。"""
    del frame, chosen
    return tuple(p.seat for p in state.players if p.alive and p.seat != owner)


def _yingyuan_name(card: Any) -> str:
    """这张牌**作为什么牌名**被使用：产物牌名（``CardRef.type``；转化 / 虚拟牌按产物）。"""
    return str(card.type)


def _yingyuan_used_card(frame: Frame) -> Any:
    """本次派发对应的"那张被使用的牌"：优先读帧私有缓存，其次读帧语义键 ``card``。

    首次派发时把牌面记进 :data:`_YINGYUAN_CARD`（挂起续跑帧会继承它）。
    """
    card = frame.data.get(_YINGYUAN_CARD)
    if card is not None:
        return card
    card = frame.data.get("card")
    if card is not None:
        frame.data[_YINGYUAN_CARD] = card
    return card


def _yingyuan_source_ids(card: Any) -> tuple[int, ...]:
    """这次使用的那张牌落到弃牌堆时对应的**实体牌 id**（虚拟牌 = 它的来源实体牌）。"""
    if card.is_virtual:
        return tuple(int(card_id) for card_id in card.virtual_sources)
    return (int(card.card_id),)


def _yingyuan_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**我的回合**里，这张我使用的牌**刚进弃牌堆**，且该牌名还没发动过。

    判据全是帧载荷上的事实（不认技能名）：
    ① ``MoveEvent.reason is USE``（用到弃牌堆，不是弃置 / 判定 / 置入）；
    ② ``to_zone`` 是弃牌堆；③这批牌就是本次使用的那张（虚拟牌 = 它的来源实体牌）；
    ④ 回合角色是我；⑤ 场上有其他存活角色；⑥ 该牌名本回合还没发动过。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    state = host.state
    event = move_event(frame)
    if event is None or event.reason is not MoveReason.USE:
        return False
    if event.to_zone.zone is not Zone.DISCARD:
        return False
    if state.turn_seat is None or int(state.turn_seat) != owner:
        return False
    card = _yingyuan_used_card(frame)
    if card is None:
        return False
    if tuple(event.card_ids) != _yingyuan_source_ids(card):
        return False
    if not _other_alive_seats(state, frame, owner, ()):
        return False
    return _yingyuan_name(card) not in state.player(owner).yingyuan_names


def _yingyuan_effect(host: Any, frame: Frame, answers: Any) -> Control:
    """提交点：**先占位**（发动即占，用户 2026-11-18 签 Q7），再把那张牌交给目标。

    交出的牌必须**仍在弃牌堆**（结算期间可能被别人的技能拿去 —— 那时零副作用返回，
    占位照旧：这次发动已经发生）。
    """
    owner = int(frame.data["skill_owner"])
    target = int(answers["seat"][0])
    state = host.state
    event = move_event(frame)
    if event is None:
        return None
    if not frame.data.get(_YINGYUAN_DONE):
        frame.data[_YINGYUAN_DONE] = True
        card = _yingyuan_used_card(frame)
        if card is not None:
            name = _yingyuan_name(card)
            player = state.player(owner)
            if name not in player.yingyuan_names:
                player.yingyuan_names = (*player.yingyuan_names, name)
    if frame.data.get(_YINGYUAN_MOVED):
        return None
    ids = tuple(
        int(card_id)
        for card_id in event.card_ids
        if locate(state, int(card_id)).zone is Zone.DISCARD
    )
    if not ids:
        frame.data[_YINGYUAN_MOVED] = True
        return None
    control = host.move_in_frame(
        frame,
        state.refs(ids),
        to_zone=ZoneRef(Zone.HAND, target),
        reason=MoveReason.PUT,
        owner=target,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    frame.data[_YINGYUAN_MOVED] = True
    return None


YINGYUAN_FLOW = SkillFlow(
    name="yingyuan",
    steps=(PickSeats(lo=1, hi=1, pool=_other_alive_seats, key="seat"),),
    effect=_yingyuan_effect,
)
"""【应援】的一步流水线：选一名**其他**角色（放弃出口在发动那一问，本步必答）。"""

_YINGYUAN_DEF = SkillDef(
    skill_id=YINGYUAN,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(Hook(Timing.MOVE_AFTER, flow=YINGYUAN_FLOW, condition=_yingyuan_condition),),
    requirement=_REQUIREMENT_YINGYUAN,
    step_requirements={"seat": "将之交给一名其他角色"},
)
"""【应援】：触发技，钩子 + 一步选座位（`preactivated=False` ⇒ 直提态 + 逐步问）。"""


MALIANG_SKILL_DEFS: tuple[SkillDef, ...] = (_ZISHU_DEF, _YINGYUAN_DEF)
"""马良的全部技能定义。"""


def maliang_skill_definitions() -> tuple[SkillDef, ...]:
    """马良的技能定义（票 01 落地；由 :func:`sp1_skill_definitions` 汇总）。"""
    return MALIANG_SKILL_DEFS


def sp1_skill_definitions() -> tuple[SkillDef, ...]:
    """SP武将组1 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return (*maliang_skill_definitions(),)
