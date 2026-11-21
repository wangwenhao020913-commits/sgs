r"""界一将成名-2014·移动版 界曹真【司敌】（jie-yijiang-2014-wuyi-caozhen 票 03；用户 2026-11-18 签）。

牌面（移动版 WIKI「经典」段 = 用户资产，逐字；②的牌型按用户 2026-11-18 的补正）：

    ①当你使用**除延时锦囊牌以外的牌**结算完毕后，你可以选择一名其他角色，预测其使用的
      下一张**除延时锦囊牌以外的牌**指定的唯一目标。
    ②若你预测正确且该目标：
      【是你】你摸一张牌；
      【不是你】你选择一项：
      1.取消之，然后若此时场上没有任何角色处于濒死状态，你对其造成1点伤害；
      2.你摸两张牌。

**用户 2026-11-18 的六条口径**（裁定票 ``04-caozhen-ruling.md`` 的 Q1–Q6）：

- **Q1 = A**：实现移动版「预测型」（= 用户资产 = 移动版 WIKI 的当前版本）。
- **牌型口径（用户 2026-11-18 补正）**：①与②的牌型都是「**除延时锦囊以外的任何牌**」
  （基本牌 / 普通锦囊 / 装备牌都算）。用户原文：「这里是一个歧义点，其实是【除延时锦囊外的
  任何牌】；包括预测也是【除延时锦囊外的任何牌】+唯一目标；如果要表示「非延时**锦囊**牌」，
  一般采用"普通锦囊牌"这个说法。」⇒「非延时锦囊牌」不是官方用语，本技能**不**按它收窄。
- **Q3 = B（锚点 = 下一张**任意**牌）**：被预测的角色**使用任意一张牌**时立刻判定——
  中 / 没中就那一下、判完**立刻清除**。② 的锚点因此与 ① 的牌型**同一档**（除延时锦囊以外
  的任何牌），两者一起由 Q3 + 2026-11-18 的牌型补正定死。
- **Q2 = 使用者挨打**：「你对其造成1点伤害」的「其」= **使用那张牌的角色**（被预测者），
  不是预测的目标角色。一手依据：2021-12-13 移动版技改公告的改动前文本里「其使用此牌…
  你对其造成1点伤害」的「其」就是使用者；用户给的例子（界曹真预测骆统以吕布为目标、
  骆统出【杀】⇒ 取消这张【杀】并对**骆统**造成 1 点）逐字如此。
- **Q4 = 清除**：预测失败也清除（一次性）。
- **Q5 = 不能重复指定**：已在预测中的角色不进 ① 第 1 步的候选池。
- **Q6 = 公开「谁被司敌」、不公开"预测目标"**：被预测者身上挂一枚公开**标记**「司敌」
  （座位武将牌上可读，用户要的"组件"就在这一面）；**预测的目标角色只住在引擎侧**
  （:attr:`sgs.state.Player.sidi_marks`），不进任何载荷。

两个时机的落点（``shapes.md`` §3.1）：

- ① 用 ``RESOLVE_AFTER_END``（**使用结算结束后**；USE 帧最后一步 ``use_after_end``），
  条件自校"这张牌是我用的"（``MATCH_OWNER_KEY`` + ``frame.data["source"]``）；
- ② 用 ``USE_AFTER_DESIGNATE_TARGET``（B5「指定目标后」，目标已终局），
  ``forced=True``——牌面 ② 没有「可以」，故**不问"是否发动"**，条件成立即跑；
  它必须在 ``use_push_resolve`` 之前把"目标级无效"写进 USE 帧（随帧继承进 RESOLVE 帧），
  本时机正好在那个窗口里。

**「取消之」= 目标级无效**（否定第 2 层，``sgs.skills.negation``）：② 只在"唯一目标"上成立
⇒ 让那一个目标无效 = 整张牌不结算（``resolve_start`` 的 C1 终止，**不进**无懈窗口）。
账本 L942 已把"被无懈抵消"与"对目标无效"并成同一个终止点。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, behavior_for
from sgs.resolution import Frame
from sgs.state import FrameKind, GameState
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import PickSeats, SkillFlow
from .negation import mark_targets_ineffective

__all__ = ["SIDI", "sidi_skill_definitions"]

SIDI = hero_skill_id("jie_caozhen_mobile", "sidi")
"""界曹真移动版【司敌】的全限定技能 id（与 ``hero.caozhen_mobile.sidi`` **不同 id**）。"""

SIDI_MARK = "司敌"
"""被预测者身上那枚公开标记的**名字**（用户 Q6：全场能看到"谁被司敌"）。"""

_SIDI_PREDICTED_ONCE = "sidi_predicted_once"
"""帧私有：① 的这一次提交已经写过（重入不重写）。"""

_SIDI_PREDICTED = "sidi_predicted_target"
"""帧私有：这一次兑现的**预测目标**（挂起续跑时预测表已清掉，故必须定格）。"""

_SIDI_HIT = "sidi_hit"
"""帧私有：这一次预测**中了没有**（同上，定格一次）。"""

_SIDI_CLEARED = "sidi_cleared"
"""帧私有：这一次预测**已经判定并清除**（重入不重判、不重清）。"""

_SIDI_DRAWN_ONE = "sidi_drawn_one"
"""帧私有：② 的「是你」那一支已经摸过一张（重入不重摸）。"""

_SIDI_DRAWN_TWO = "sidi_drawn_two"
"""帧私有：② 的「摸两张牌」那一支已经摸过（重入不重摸）。"""

_SIDI_DAMAGED = "sidi_damaged"
"""帧私有：② 的「取消之…造成1点伤害」那一段已经造过伤（重入不重造）。"""

SIDI_CANCEL = "sidi:cancel"
"""② 二选一的选项 id：取消之（然后若无濒死，对其造成 1 点）。"""

SIDI_DRAW_TWO = "sidi:draw_two"
"""② 二选一的选项 id：摸两张牌。"""

_REQUIREMENT = (
    "当你使用除延时锦囊牌以外的牌结算完毕后，你可以选择一名其他角色，"
    "预测其使用的下一张牌指定的唯一目标"
)
"""① 的发动文案（``SkillDef.requirement``）：牌型 = 除延时锦囊以外的任何牌（用户 2026-11-18
补正）；锚点 = 他使用的下一张牌（Q3 = B）。"""


def _is_non_delayed_card(card: Any) -> bool:
    """这张牌是不是**除延时锦囊以外的牌**（① 的牌型闸；读**产物**牌型，转化 / 视为使用同样算）。

    用户 2026-11-18 的口径：基本牌 / 普通锦囊 / 装备牌都算，只有**延时锦囊**（
    ``behavior.is_delayed``，【乐不思蜀】/【兵粮寸断】/【闪电】一类）不算。
    """
    if card is None:
        return False
    return not behavior_for(card).is_delayed


def _sidi_predict_pool(state: GameState, owner: int) -> tuple[int, ...]:
    """① 第 1 步的候选：**其他存活角色中还没被指定过**的（Q5：不能重复指定）。"""
    marks = state.player(owner).sidi_marks
    return tuple(
        int(player.seat)
        for player in state.players
        if player.alive and int(player.seat) != owner and int(player.seat) not in marks
    )


def _sidi_first_pool(
    state: GameState, frame: Frame, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """:class:`~sgs.skills.flow.PickSeats` 的池回调（四个形参版；判据同上）。"""
    del frame, chosen
    return _sidi_predict_pool(state, owner)


def _sidi_target_pool(state: GameState, frame: Frame, owner: int, chosen: tuple[int, ...]) -> tuple[int, ...]:
    """① 第 2 步的候选：**全体存活角色**（含自己——「是你」那一支要求能预测到自己）。"""
    del frame, owner, chosen
    return tuple(int(player.seat) for player in state.players if player.alive)


def _sidi_predict_condition(host: Any, frame: Frame) -> bool:
    """① 的条件：**这张（除延时锦囊以外的）牌是我用的** ∧ 还有可以指定的其他角色。

    自校三件：``MATCH_OWNER_KEY`` 指向我、``frame.data["source"]`` 是我、牌型**不是延时锦囊**。
    池为空（所有其他角色都已在预测中）⇒ 候选阶段就不产出（不发没有候选的询问）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if frame.data.get("source") != owner:
        return False
    if not _is_non_delayed_card(frame.data.get("card")):
        return False
    return bool(_sidi_predict_pool(host.state, owner))


def _sidi_predict(host: Any, frame: Frame, answers: Any) -> Any:
    """① 的提交点：写下"谁 → 预测谁"并在他身上挂那枚公开的「司敌」标记。

    两处写入都是幂等的（``set_sidi_mark`` 覆盖同键、``add_marker`` 同键合并），故挂起续跑
    顶多重写同一个值；游标只用来跳过"已经写过的这一次"。
    """
    owner = frame.data.get("skill_owner")
    if owner is None:
        owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return None
    owner = int(owner)
    if frame.data.get(_SIDI_PREDICTED_ONCE):
        return None
    predicted = tuple(int(seat) for seat in answers.get("first", ()))
    target = tuple(int(seat) for seat in answers.get("target", ()))
    if not predicted or not target:
        return None
    frame.data[_SIDI_PREDICTED_ONCE] = True
    host.set_sidi_mark(owner, predicted[0], target[0])
    host.add_marker(
        frame,
        predicted[0],
        name=SIDI_MARK,
        source_seat=owner,
        source_skill_id=SIDI,
        description="被界曹真指定为「司敌」目标",
    )
    return None


def _sidi_redeem_condition(host: Any, frame: Frame) -> bool:
    """② 的条件：**别人的牌**进了 B5、那个人的牌**不是延时锦囊**、且他正在我的预测表里。

    **延时锦囊不锚定**（官方现行文本逐字：「其使用的第一张**除延时锦囊以外的牌**仅指定
    "司敌"目标为唯一目标时（否则清除…）」）⇒ 延时锦囊的那次使用既不判中、也**不**清掉
    预测。旧实现让任何牌都当锚点：被预测者先贴一张【乐不思蜀】就把预测白清了（用户
    2026-11-21 报障"司敌偶尔不触发"的一条来源）。
    """
    if not _is_non_delayed_card(frame.data.get("card")):
        return False
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    source = frame.data.get("source")
    if source is None or int(source) == owner:
        return False
    return int(source) in host.state.player(owner).sidi_marks


def _sidi_damage(host: Any, frame: Frame, *, source: int, target: int) -> Any:
    """对 ``target`` 压一个 1 点伤害帧（无渠道牌、无属性；来源 = 曹真）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "channel": None,
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "nature": None,
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    return None


def _sidi_redeem(host: Any, frame: Frame) -> Any:
    """② 的执行体：判定 → 清除 → 按"是你 / 不是你"兑现（强制段，不问是否发动）。

    判定（Q3）：锚点牌此刻的**最终**目标集合（B5 之后不再改变）**恰等于** ``(预测目标,)``
    ⇒ 中；其余一律失败（多目标 / 不含它 / 空目标 / 延时锦囊 / 装备 / 基本牌都走这里）。
    **中与不中都清除**（Q4），且清除**先于**任何兑现（牌面 ② 的"然后若…"）。

    兑现：
    - 预测目标 = 曹真自己 ⇒ **摸一张牌**（不问）；
    - 否则 ⇒ **必答二选一**（``optional=False``，牌面没有放弃出口）：
      「取消之」= 把那个目标写成**目标级无效**（随 USE 帧继承进 RESOLVE 帧，C1 终止结算），
      然后**若此刻场上没有任何角色处于濒死状态**，对**使用那张牌的角色**（Q2）造成 1 点伤害；
      或「摸两张牌」。
    """
    owner = frame.data.get("skill_owner")
    if owner is None:
        return None
    owner = int(owner)
    source = frame.data.get("source")
    if source is None:
        return None
    source = int(source)
    # 判定结果**先算一次、住在帧私有**：选项步会挂起，续跑时预测表已经清掉了
    # （"清除先于兑现"是牌面的次序），不缓存就会把这一次兑现读成"没有预测"。
    predicted = frame.data.get(_SIDI_PREDICTED)
    if predicted is None:
        predicted = host.state.player(owner).sidi_marks.get(source)
        if predicted is None:
            return None
        frame.data[_SIDI_PREDICTED] = int(predicted)
        targets = tuple(int(seat) for seat in (frame.data.get("targets") or ()))
        frame.data[_SIDI_HIT] = len(targets) == 1 and targets[0] == int(predicted)
    predicted = int(predicted)
    hit = bool(frame.data.get(_SIDI_HIT))
    if not frame.data.get(_SIDI_CLEARED):
        frame.data[_SIDI_CLEARED] = True
        host.clear_sidi_mark(owner, source)
        host.remove_marker(
            frame,
            source,
            name=SIDI_MARK,
            source_seat=owner,
            source_skill_id=SIDI,
        )
    if not hit:
        return None
    if int(predicted) == owner:
        if frame.data.get(_SIDI_DRAWN_ONE):
            return None
        frame.data[_SIDI_DRAWN_ONE] = True
        return host.draw_for_hand(frame, owner, 1)
    answer = host.ask_option(
        frame,
        owner,
        Timing.USE_AFTER_DESIGNATE_TARGET,
        skill_id="sidi:choice",
        options=(SIDI_CANCEL, SIDI_DRAW_TWO),
    )
    if host.suspended(answer):
        return "stay"
    # ``ask_option`` 交回的是**动作**（``SelectOption``），不是选项 id 本身。
    choice = str(getattr(answer, "choice", answer))
    if choice == SIDI_CANCEL:
        mark_targets_ineffective(frame, (int(predicted),))
        if host.near_death_in_flight():
            return None
        if frame.data.get(_SIDI_DAMAGED):
            return None
        frame.data[_SIDI_DAMAGED] = True
        return _sidi_damage(host, frame, source=owner, target=source)
    if frame.data.get(_SIDI_DRAWN_TWO):
        return None
    frame.data[_SIDI_DRAWN_TWO] = True
    return host.draw_for_hand(frame, owner, 2)


SIDI_PREDICT_FLOW = SkillFlow(
    name="jie_caozhen_sidi_predict",
    steps=(
        PickSeats(lo=1, hi=1, pool=_sidi_first_pool, key="first", optional=False),
        PickSeats(lo=1, hi=1, pool=_sidi_target_pool, key="target", optional=False),
    ),
    effect=_sidi_predict,
)
"""① 的两步座位流水线：先选"预测谁"，再选"预测他会打谁"。"""


def sidi_skill_definitions() -> tuple[SkillDef, ...]:
    """界曹真【司敌】的 :class:`~sgs.skills.catalog.SkillDef`（并入 ``CATALOG``）。

    两条钩子同住一条声明：``RESOLVE_AFTER_END``（① 攒预测）与 ``USE_AFTER_DESIGNATE_TARGET``
    （② 兑现，``forced=True``）。**不是锁定技**——① 牌面写「可以」，② 牌面把它当成一次"预测
    的兑现"、没有放弃出口（所以只把那一条钩子标成强制段，不影响失效语义）。
    """
    return (
        SkillDef(
            skill_id=SIDI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.RESOLVE_AFTER_END,
                    condition=_sidi_predict_condition,
                    flow=SIDI_PREDICT_FLOW,
                ),
                Hook(
                    Timing.USE_AFTER_DESIGNATE_TARGET,
                    handler=_sidi_redeem,
                    condition=_sidi_redeem_condition,
                    forced=True,
                ),
            ),
            requirement=_REQUIREMENT,
            step_requirements={
                "first": "选择一名其他角色（他还没被你指定过）",
                "target": "预测他使用的下一张牌指定的唯一目标",
            },
        ),
    )
