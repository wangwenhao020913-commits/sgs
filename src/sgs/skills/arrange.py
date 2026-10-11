r"""G 族：授予可见性与排列（票 10）。

一件今天完全不存在的能力：**把牌堆顶的牌面授予某一次询问**。`04`（§2「信息可见性」）明写
"牌堆顺序仅引擎（技能'观看牌堆顶'按效果逐张授予）"，而授予通道一直只有 ``PlayerView.deck_count``
这一个读点——本模块是那条通道的**第一个消费者**，也是它保持**通用**的理由。

**授予通道**住协议层（``_QueryBase.revealed``）：本次询问授予该参与者的牌面，随询问结束即
失效，不写状态、不进 ``PlayerView``、不泄露给其它座位。引擎侧的两个入口
（``Engine.peek_deck`` = 观看、``Engine.ask_query(..., revealed=...)`` = 授予）都**不认识
任何技能 id**；本模块只调它们。故将来的【遗计】（只看不排，票 05）与【观星】（看后排）
共用同一条，而不是各造一条。

**诸葛亮【观星】**（规则 991）：

    准备阶段（回合开始阶段）开始时，你可以观看牌堆顶的 X 张牌
    （X 为存活角色数且至多为 5），然后将其中任意数量的牌以任意顺序置于牌堆顶，
    其余以任意顺序置于牌堆底。

- **挂阶段体** ``PHASE_PREPARE``（不是 ``PHASE_PREPARE_START``——后者在 ``EMPTY_LOG``，
  R6-Q1 裁定不解封）；挂点由 :func:`arrange_skill_definitions` 交给引擎的通用阶段体派发。
- **X = min(存活角色数, 5)**：存活角色数是公开信息，不额外泄露。
- **非锁定**：技能面写了"你可以"，故先发"是否发动"询问；答 ``ActivateSkill`` 才开排列询问。
- **放置口径**：``ArrangeCards`` 的 ``order`` 自顶向下、``split`` = 前几张置顶；"置底"
  = 接在**原有牌堆底之下**（`effects.place_on_deck` 的 ``to_top=False``）。

牌面只出现在那次 ``ARRANGE`` 询问的 ``revealed`` 里；排列本身在记录流里只以
``ArrangeCards`` 的**面内位置**出现，不另发带牌面的 delta（票 10 §验收 2 / 用户故事 47）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, place_on_deck
from sgs.resolution import Control, Frame
from sgs.state import ArrangeCards, CardRef, GameState, alive_seats
from sgs.timing import Timing

from .catalog import ActivationPlan, Hook, SkillDef

__all__ = [
    "GUANXING",
    "GUANXING_MAX",
    "arrange_activation_plans",
    "arrange_skill_definitions",
    "guanxing_handler",
]

GUANXING = hero_skill_id("zhugeliang", "guanxing")
"""诸葛亮【观星】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

GUANXING_MAX = 5
"""观星的 X 封顶（规则 991 的"至多为 5"）。"""

_CARDS_KEY = "guanxing_cards"
_DONE_KEY = "guanxing_done"


def _guanxing_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """观星的发动前提：**技能持有者就是当前回合角色**。

    只有自己的准备阶段才谈得上"准备阶段开始时"。``available=False`` ⇒ 该实例不进候选、
    连"是否发动"都不问（票 19 §6 的姿态）——区别于"有这条技能但这次不发动"。
    读 ``instance`` 而不是 ``frame.data["skill_owner"]``，因为**条件 / 形态在派发器写
    ``skill_owner`` 之前求值**（``_matching_hooks`` 先于那行）。
    """
    del frame
    return ActivationPlan(available=instance.owner == state.turn_seat)


def arrange_activation_plans() -> dict[str, Any]:
    """G 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。"""
    return {GUANXING: _guanxing_plan}


def _apply_arrangement(
    state: GameState, cards: tuple[CardRef, ...], order: tuple[int, ...], split: int
) -> None:
    """按 ``order`` / ``split`` 把被授予的牌放回：顶段按序置顶、底段按序接在牌堆底之下。"""
    top = tuple(cards[index] for index in order[:split])
    bottom = tuple(cards[index] for index in order[split:])
    place_on_deck(state, top, to_top=True)
    place_on_deck(state, bottom, to_top=False)


def guanxing_handler(host: Any, frame: Frame) -> Control:
    """【观星】的钩子回调：是否发动已由派发器问过；这里看牌堆顶 X 张、请其排列、放回。

    ``_dispatch`` 的帧内答案缓存让"钩子重入"不会重看、重问、重放；``_DONE_KEY`` 是
    放回后的短路，避免同一帧里二次进入。
    """
    if frame.data.get(_DONE_KEY):
        return None
    state = host.state
    seat = frame.data["skill_owner"]
    cards = frame.data.get(_CARDS_KEY)
    if cards is None:
        count = min(len(alive_seats(state)), GUANXING_MAX)
        cards = host.peek_deck(frame, count)
        frame.data[_CARDS_KEY] = cards
    if not cards:
        # 两堆皆空的短观看：没有可排列的牌，这次发动无事发生（终局判定由摸牌路径负责）。
        frame.data[_DONE_KEY] = True
        return None
    action = host.ask_arrange(frame, seat, Timing.PHASE_PREPARE, cards=cards)
    if host.suspended(action):
        return "stay"
    assert isinstance(action, ArrangeCards)  # 候选只有 ArrangeCards，成员判定已保证
    _apply_arrangement(state, cards, action.order, action.split)
    frame.data[_DONE_KEY] = True
    return None


def arrange_skill_definitions() -> tuple[SkillDef, ...]:
    """G 族的技能定义（本票只有【观星】一条；【遗计】归票 05 并复用同一通道）。

    非锁定（技能面写了"你可以"）、``carrier = HERO``（优先级档由载体派生）、
    ``skill_type = NORMAL``（不是主公技）。
    """
    return (
        SkillDef(
            skill_id=GUANXING,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.PHASE_PREPARE, guanxing_handler),),
        ),
    )
