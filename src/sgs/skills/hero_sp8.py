r"""SP武将组8 的武将技能：刘焉【图射】（``sp8`` 包；sp8-liuyan 票 01）。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 刘焉【图射】= 本模块（``USE_AFTER_DESIGNATE_TARGET`` 钩子 + 零步流水线 + 摸 X 张）；
- 刘焉【立牧】= **A 族主动技**（牌面「出牌阶段，你可以…」= 点击即发动），住
  :mod:`sgs.skills.active`——A 族的唯一装载点是那边的 ``ACTIVE_SKILLS``（与马云騄【马术】
  住 :mod:`sgs.skills.target_change` 同一条理由：一族一声明面，不按武将分文件）。
  立牧的询问剧本 / 读法表见 ``.scratch/sp8-liuyan/issues/01-liuyan.md`` §2。

牌面权威 = **OL 现行**（账本 L963「技能文本以 OL 现行为准」）：OL 刘焉页
（``https://www.sanguosha.com/hero/634``，2026-11-17 抓取）与移动版官方页
（``https://www.sanguosha.cn/pc/hero-detail-239.html``，同日）**同文**：

    图射：当你使用非装备牌指定目标后，若你没有基本牌，则你可以摸X张牌
          （X为此牌指定的目标数）。

用户 2026-11-17 签的裁定（票 01「询问剧本」+ §0.2 的 Q-A）：

- **闸门是"产物大类不是装备牌"，不是"有没有目标"**（用户逐字口径）：「装备牌也是指定目标
  的，但是该技能限制了『非装备牌』，也就是说使用『装备牌』不摸牌的原因不是没有指定目标，
  而是技能硬限制」⇒ 装备牌照样把**自己**指定为目标，挡它的是文本；反过来，**自用的非装备
  牌**（【桃】/【无中生有】/【酒】/【闪电】/转化【乐不思蜀】）照样触发、X = 1；
- **Q-A = 不触发**：0 个座位目标的使用（无懈可击）不产出询问、不摸牌（`X = 0`）；
- **X = 本次使用的座位目标数**，含自己（官方移动版攻略：「像桃、酒、闪、杀等很多牌打出时
  仅能指定一个目标而摸一牌」）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import CarrierKind
from sgs.resolution import Control, Frame
from sgs.state import GameState
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "TUSHE",
    "TUSHE_FLOW",
    "liuyan_skill_definitions",
]

TUSHE = hero_skill_id("liuyan", "tushe")
"""刘焉【图射】的全限定技能 id。"""

_TUSHE_DRAWN = "tushe_drawn"
"""帧私有：本次发动的摸牌段已经跑过（挂起续跑不重摸）。"""

_TUSHE_X = "tushe_x"
"""帧私有：本次发动的 X（首次执行时定格；摸牌会改手牌，重入不得重算）。"""

_REQUIREMENT_TUSHE = (
    "当你使用非装备牌指定目标后，若你没有基本牌，则你可以摸X张牌"
    "（X为此牌指定的目标数）。"
)
"""【图射】的牌面逐字（OL 现行；``requirement`` 槽位）。"""


def _hand_has_basic(state: GameState, seat: int) -> bool:
    """``seat`` 的**手牌**里有没有基本牌（牌面「若你没有基本牌」）。

    判据 = 内容包的牌名大类（:func:`sgs.data.card_category`），不抄牌名清单。区域口径：
    默认读法（一）③「『基本牌』= **手牌中的**该类」；装备区 / 判定区都不可能装基本牌，
    故"手牌区"与"所有区域"在这一条上等价。
    """
    return any(
        card_category(state.pack_id, state.card(card_id).type) is Category.BASIC
        for card_id in state.player(seat).hand
    )


def _tushe_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**本人**使用 ∧ 产物不是装备牌 ∧ 手牌没有基本牌 ∧ 至少 1 个座位目标。

    「本人」读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY` + 使用帧的 ``source``（样板
    ``hero.mayunlu.fengpo`` 的 ``_fengpo_condition``）；「非装备牌」读**产物**的牌名大类
    ——转化 / 纯虚拟牌因此按产物判（【武圣】的虚拟【杀】算非装备，见默认读法（一）④
    「使用类效果不看区域」）。

    目标数 < 1 直接把条件判假：`X = 0` 时"摸 0 张"与不触发在结算上相同，而多发一条询问
    只涨噪音（用户 2026-11-17 签 Q-A = 不触发）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    if len(tuple(frame.data.get("targets", ()))) < 1:
        return False
    card = frame.data.get("card")
    if card is None:
        return False
    if card_category(host.state.pack_id, card.type) is Category.EQUIPMENT:
        return False
    return not _hand_has_basic(host.state, int(owner))


def _tushe(host: Any, frame: Frame) -> Control:
    """效果：摸 X 张（X = 本次使用的座位目标数）。

    **游标先落、再摸**：摸牌会内联派发「获得牌后」并可能挂起（``Engine.draw_for_hand``
    的 docstring 逐字要求"调用方必须把 ``"stay"`` 原样返回"）⇒ 挂起续跑会重跑本函数，
    没有 :data:`_TUSHE_DRAWN` 就会多摸一次。
    """
    if frame.data.get(_TUSHE_DRAWN):
        return None
    x = frame.data.get(_TUSHE_X)
    if x is None:
        x = len(tuple(frame.data.get("targets", ())))
        frame.data[_TUSHE_X] = x
    if int(x) <= 0:
        return None
    frame.data[_TUSHE_DRAWN] = True
    if host.draw_for_hand(frame, int(frame.data["skill_owner"]), int(x)) == "stay":
        return "stay"
    return None


TUSHE_FLOW = SkillFlow(name="tushe", steps=(), effect=zero_step_effect(_tushe))
"""【图射】的零步流水线：[是否发动] → 摸 X 张（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例。
"""

_TUSHE_DEF = SkillDef(
    skill_id=TUSHE,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.USE_AFTER_DESIGNATE_TARGET,
            condition=_tushe_condition,
            flow=TUSHE_FLOW,
        ),
    ),
    requirement=_REQUIREMENT_TUSHE,
)

LIUYAN_SKILL_DEFS: tuple[SkillDef, ...] = (_TUSHE_DEF,)
"""刘焉的技能定义：只装**非 A 族**那一条（【立牧】由 ``ACTIVE_SKILLS`` 派生，见模块头）。"""


def liuyan_skill_definitions() -> tuple[SkillDef, ...]:
    """SP武将组8 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return LIUYAN_SKILL_DEFS
