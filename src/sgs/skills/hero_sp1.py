r"""SP武将组1 的武将技能：马良【自书】【应援】（``sp1`` 包；sp1-sunshangxiang-maliang 票 01）。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 马良【自书】= 本模块（``MOVE_AFTER`` ×2 + ``TURN_END`` + ``TURN_START`` 簿记：锁定技、
  零询问、没有流水线）；
- 马良【应援】= 本模块（``MOVE_AFTER`` 钩子 + 一步选座位流水线）；
- 同一包的另一名武将（SP孙尚香）的【良助】【返乡】住本模块（票 02 追加），
  ☆【枭姬】住 :mod:`sgs.skills.move_after`（D 族"失去牌后"的唯一装载点，与马云騄【马术】
  住 :mod:`sgs.skills.target_change` 同一条理由）。

**SP孙尚香**（票 02）同样住本模块：**良助**（`RECOVER_AFTER` 钩子 + 一步选项）、**返乡**
（觉醒技：`PHASE_PREPARE` + 零步流水线）；☆**枭姬**住 :mod:`sgs.skills.move_after`（D 族"失去
牌后"的唯一装载点，与标准 / 界【枭姬】**同一份牌面、同一个回调**）。

票 01 牌面权威 = **用户资产**（``D:\\SGS_HEROS\\SP武将组1\\马良\\技能.txt``；用户 2026-11-18 逐条签）：

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
from sgs.effects import CarrierKind, is_own_play_phase, locate
from sgs.resolution import Control, Frame
from sgs.state import CardTag, FrameKind, GameState, MoveReason, Zone, ZoneRef, alive_seats
from sgs.timing import Timing

from .awakening import _AWAKENING_COMMITTED, _awakening_verdict
from .catalog import MATCH_OWNER_KEY, RECOVERED_AMOUNT_KEY, Hook, SkillDef, instance_key
from .flow import PickOption, PickSeats, SkillFlow
from .move_after import gains_card, move_event

__all__ = [
    "FANXIANG",
    "FANXIANG_FLOW",
    "LIANGZHU",
    "LIANGZHU_FLOW",
    "MALIANG_SKILL_DEFS",
    "SUNSHANGXIANG_SP_SKILL_DEFS",
    "XIAOJI_SP",
    "ZISHU",
    "ZISHU_TAG",
    "YINGYUAN",
    "YINGYUAN_FLOW",
    "liangzhu_used_on",
    "maliang_skill_definitions",
    "sp1_skill_definitions",
    "sunshangxiang_sp_skill_definitions",
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

_YINGYUAN_MINE = "yingyuan_mine"
"""帧私有：这一次是「**我使用**的那张牌」进弃牌堆（**打出不算**）。

`MoveReason.USE` 是**使用与打出共用的**（`Engine.move_processing_card_to_discard` 与
`move_played_card_to_discard` 都写 `USE`）⇒ 单看移动原因会把"别人响应打出的【闪】"
误判成"我使用的牌"（用户 2026-11-18 实机报障）。判据 = **承载帧是 USE 帧** ∧ 帧的
`source` 是持有者；结论缓存到帧私有——挂起续跑帧的 `kind` 是 `RESOLVE`、`source` 被改写成
`event.actor`，现算会翻假，只有帧私有会被继承。
"""

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
    if frame.data.get(_YINGYUAN_MINE) is None:
        # 只有「**我使用**的牌」算：打出（别人的响应 / 我自己打出的【闪】【杀】）不算。
        if frame.kind is not FrameKind.USE or frame.data.get("source") != owner:
            return False
        frame.data[_YINGYUAN_MINE] = True
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


# ---------------------------------------------------------------------------
# SP孙尚香【良助】：当一名角色于**其**出牌阶段内回复体力后，你可以选择一项
# （sp1-sunshangxiang-maliang 票 02；用户 2026-11-18 签 Q1–Q5）
#
#     良助：当一名角色于其出牌阶段内回复体力后，你可以选择一项：1.摸一张牌；2.令其摸两张牌。
#     返乡：觉醒技，准备阶段，若场上有已受伤的角色且你对其发动过"良助"令其以此法获得过牌，
#           你加1点体力上限并回复1点体力，然后失去技能"良助"并获得技能"枭姬"。
#
# 读法（票 02 §1.1 / §2.1）：
# - 「于**其**出牌阶段内」= **回复者自己的**出牌阶段（一手：华佗对别人【青囊】时孙尚香不能
#   对他发动良助）；判据 = `is_own_play_phase(state, 回复者)`；
# - 触发粒度 = **逐事件**（`RECOVER` 帧一次派发一次；单体回复 2 点仍只问一次）；
# - **回复 0 点不算事件**（用户签 Q7）：读 `RECOVERED_AMOUNT_KEY`（帧私有、被体力上限夹过）；
# - 「令其以此法获得过牌」= **只由选项 2 写入**、**整局**寿命的按座位事实（用户签 Q1 / Q2），
#   落 `skill_state` 的座位位段（bit 36..43，照【定乱】的 24..31），失去【良助】时随实例清；
# - 返乡两条件**同一个座位**（一手三源同读，Q3）；**含自己**（用户 2026-11-18 补例：
#   「对自己用选项 2，然后准备阶段自己已受伤也觉醒」）；
# - 次序 = **先加上限、再回复**（一手「觉醒后从 3 勾玉满血变成 4 勾玉满血」），然后失去 / 获得。
# ---------------------------------------------------------------------------

LIANGZHU = hero_skill_id("sunshangxiang_sp", "liangzhu")
"""SP孙尚香【良助】的全限定技能 id。"""

FANXIANG = hero_skill_id("sunshangxiang_sp", "fanxiang")
"""SP孙尚香【返乡】（觉醒技）的全限定技能 id。"""

XIAOJI_SP = hero_skill_id("sunshangxiang_sp", "xiaoji")
"""SP孙尚香 ☆【枭姬】的全限定技能 id（只经【返乡】的 ``grant_skill`` 获得）。"""

_LIANGZHU_DRAW_ONE = "liangzhu:draw_one"
_LIANGZHU_DRAW_TWO = "liangzhu:draw_two"

_LIANGZHU_SHIFT = 36
"""【良助】"我对座位 i 发动过良助②"位段的起始位（整局）。

**为什么从 36 起**：`GameState.skill_state` 的同一个整数里还住着别的位段——0..22 是 A 族阶段位
（每个出牌阶段清）、24..31 是【定乱】的整局座位位段、32..35 是【慎行】的本阶段次数。本事实是
**整局**位，故取 36..43（`int` 无位宽上限；与【定乱】同一条理由）。
"""

_LIANGZHU_SEAT_BITS = 0xFF << _LIANGZHU_SHIFT
"""座位位段：bit (36 + i) = **座位 i 被我用过良助②**（整局至多一次，无上限）。"""

_LIANGZHU_DRAWN = "liangzhu_drawn"
"""帧私有：这一次良助的摸牌已经发过（挂起续跑不重摸——照【涅槃】`_NIEPAN_DRAWN` 范式）。"""

_FANXIANG_STAGE = "fanxiang:stage"
"""帧私有段机游标：``maxlife`` / ``recover`` / ``grant`` / ``done``。"""

_REQUIREMENT_LIANGZHU = (
    "当一名角色于其出牌阶段内回复体力后，你可以选择一项：1.摸一张牌；2.令其摸两张牌。"
)
"""【良助】的牌面逐字（用户资产；OL 官网页同文）。"""

_REQUIREMENT_FANXIANG = (
    "觉醒技，准备阶段，若场上有已受伤的角色且你对其发动过\"良助\"令其以此法获得过牌，"
    "你加1点体力上限并回复1点体力，然后失去技能\"良助\"并获得技能\"枭姬\"。"
)
"""【返乡】的牌面逐字（用户资产；OL 官网页同义）。"""


def _liangzhu_instance(state: GameState, owner: int) -> Any:
    """``owner`` 名下的【良助】实例（不在场 = ``None``）。"""
    from .catalog import resolve_instances

    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == owner and instance.skill_id == LIANGZHU:
            return instance
    return None


def liangzhu_used_on(state: GameState, owner: int, seat: int) -> bool:
    """``owner`` 是否**对这个座位**发动过【良助】②（整局事实的唯一读点）。"""
    instance = _liangzhu_instance(state, owner)
    if instance is None:
        return False
    bits = int(state.skill_state.get(instance_key(instance), 0))
    return bool(bits >> (_LIANGZHU_SHIFT + int(seat)) & 1)


def _mark_liangzhu_used(state: GameState, owner: int, seat: int) -> None:
    """落"我对这个座位用过良助②"（唯一写入口；实例不在场时零动作）。"""
    instance = _liangzhu_instance(state, owner)
    if instance is None:
        return None
    key = instance_key(instance)
    state.skill_state[key] = state.skill_state.get(key, 0) | (
        1 << (_LIANGZHU_SHIFT + int(seat))
    )
    return None


def _liangzhu_condition(host: Any, frame: Frame) -> bool:
    """触发条件：回复发生在**回复者自己的出牌阶段**，且这次**真的回复了 ≥1 点**。

    「其」= 回复体力的那名角色（一手：华佗对别人【青囊】时不能发动），故判据读帧的
    ``targets[0]``（RECOVER 帧的回复者）而不是持有者的阶段。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    targets = tuple(frame.data.get("targets") or ())
    if not targets:
        return False
    recoverer = int(targets[0])
    state = host.state
    if not state.player(recoverer).alive:
        return False
    if not is_own_play_phase(state, recoverer):
        return False
    return int(frame.data.get(RECOVERED_AMOUNT_KEY, 0)) > 0


def _liangzhu_effect(host: Any, frame: Frame, answers: Any) -> Control:
    """提交点：选项 1 = 我摸一张；选项 2 = **回复者**摸两张 + 记一枚按座位的整局事实。

    摸牌可能内联触发"获得牌后"并挂起（【清俭】一类）——游标**先写再摸**（挂起 = 移动已发生），
    故续跑既不重摸、也不重复记事实。
    """
    owner = int(frame.data["skill_owner"])
    target = int(tuple(frame.data["targets"])[0])
    choice = str(answers.get("option") or "")
    if frame.data.get(_LIANGZHU_DRAWN):
        return None
    frame.data[_LIANGZHU_DRAWN] = True
    if choice == _LIANGZHU_DRAW_TWO:
        # 「令其以此法获得过牌」= **只由选项 2** 写入（用户 2026-11-18 签 Q1）；
        # 记在搬牌**之前**（先写再摸：挂起 = 这次发动已经发生）。
        _mark_liangzhu_used(host.state, owner, target)
        return host.draw_for_hand(frame, target, 2)
    return host.draw_for_hand(frame, owner, 1)


LIANGZHU_FLOW = SkillFlow(
    name="liangzhu",
    steps=(
        # 二选一（「可以选择一项」）：放弃出口在**发动那一问**，本步必答。
        PickOption(
            options=(_LIANGZHU_DRAW_ONE, _LIANGZHU_DRAW_TWO),
            optional=False,
            key="option",
        ),
    ),
    effect=_liangzhu_effect,
)
"""【良助】的一步流水线：二选一（两项恒可执行 ⇒ 永不置灰）。"""

_LIANGZHU_DEF = SkillDef(
    skill_id=LIANGZHU,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(Hook(Timing.RECOVER_AFTER, flow=LIANGZHU_FLOW, condition=_liangzhu_condition),),
    requirement=_REQUIREMENT_LIANGZHU,
    step_requirements={"option": "选择一项：1.摸一张牌；2.令其摸两张牌"},
)
"""【良助】：`RECOVER_AFTER` 的**首个载体**（帧外事件：任何人的出牌阶段都可能触发）。"""


def _fanxiang_condition(host: Any, frame: Frame) -> bool:
    """觉醒条件：**存在一个座位**同时满足「已受伤」∧「我对它用过良助②」（同一个座位，Q3）。

    时机半边（自己的准备阶段）由 :func:`~sgs.skills.awakening._awakening_verdict` 恒判。
    「场上有已受伤的角色」**含自己**（用户 2026-11-18 补例）。
    """
    verdict = _awakening_verdict(host, frame, FANXIANG)
    if verdict is not None:
        return verdict
    owner = int(frame.data[MATCH_OWNER_KEY])
    state = host.state
    return any(
        state.player(seat).hp < state.player(seat).hp_max
        and liangzhu_used_on(state, owner, seat)
        for seat in alive_seats(state)
    )


def _fanxiang_effect(host: Any, frame: Frame, answers: Any) -> Control:
    """提交点：+1 上限（`MAXLIFE` 帧）→ 回复 1 点（`RECOVER` 帧）→ 失去【良助】→ 获得 ☆【枭姬】。

    段机住帧私有游标（照【天翊】）：每段"先写游标再压帧 / 授予"，重入既不重做也不漏做。
    次序 = **先加上限、再回复**（一手「觉醒后从 3 勾玉满血变成 4 勾玉满血」）。
    """
    del answers
    owner = int(frame.data["skill_owner"])
    frame.data[_AWAKENING_COMMITTED] = owner
    stage = frame.data.get(_FANXIANG_STAGE)
    if stage is None:
        frame.data[_FANXIANG_STAGE] = "recover"
        host.raise_hp_max(owner, 1)
        return "stay"
    if stage == "recover":
        frame.data[_FANXIANG_STAGE] = "grant"
        host.push_frame(
            FrameKind.RECOVER,
            {
                "source": owner,
                "targets": (owner,),
                "card": None,
                "use_timing": frame.data.get("use_timing"),
                "amount": 1,
            },
            timing=Timing.RECOVER_VALUE,
        )
        return "stay"
    if stage == "grant":
        frame.data[_FANXIANG_STAGE] = "done"
        state = host.state
        if not state.player(owner).alive:
            # 觉醒过程中阵亡（极端情形）：后续段不再结算（照【破势】/【勤学】）。
            return None
        host.lose_skill(owner, LIANGZHU, frame=frame)
        host.grant_skill(owner, XIAOJI_SP, frame=frame)
        return None
    return None


FANXIANG_FLOW = SkillFlow(name="fanxiang", steps=(), effect=_fanxiang_effect)
"""【返乡】的零步流水线：四段全在 effect 里（"先变更状态、再压帧 / 授予"表达不成步骤）。"""

_FANXIANG_DEF = SkillDef(
    skill_id=FANXIANG,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.AWAKENING,
    hooks=(Hook(Timing.PHASE_PREPARE, flow=FANXIANG_FLOW, condition=_fanxiang_condition),),
    requirement=_REQUIREMENT_FANXIANG,
)
"""【返乡】：觉醒技（强制发动，不问"是否发动"），自己的准备阶段。"""

SUNSHANGXIANG_SP_SKILL_DEFS: tuple[SkillDef, ...] = (_LIANGZHU_DEF, _FANXIANG_DEF)
"""SP孙尚香的技能定义（☆【枭姬】住 :mod:`sgs.skills.move_after`）。"""


MALIANG_SKILL_DEFS: tuple[SkillDef, ...] = (_ZISHU_DEF, _YINGYUAN_DEF)
"""马良的全部技能定义。"""


def maliang_skill_definitions() -> tuple[SkillDef, ...]:
    """马良的技能定义（票 01 落地；由 :func:`sp1_skill_definitions` 汇总）。"""
    return MALIANG_SKILL_DEFS


def sunshangxiang_sp_skill_definitions() -> tuple[SkillDef, ...]:
    """SP孙尚香的技能定义（票 02 落地；由 :func:`sp1_skill_definitions` 汇总）。"""
    return SUNSHANGXIANG_SP_SKILL_DEFS


def sp1_skill_definitions() -> tuple[SkillDef, ...]:
    """SP武将组1 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return (*maliang_skill_definitions(), *sunshangxiang_sp_skill_definitions())
