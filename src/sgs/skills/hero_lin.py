r"""神话再临·林包·移动版武将技能（``lin_mobile`` 包；lin-batch 票 02 起 = 孙坚【英魂】）。

本模块只装**林包·移动版**的武将技能声明；族级原语（钩子 / 步骤流水线 / 摸牌 / 选定 /
帧内搬牌）一律复用既有实现，不新增机制。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 神曹操移动版【归心】= 手写逐点钩子，住 :mod:`sgs.skills.damage_after`；
- 神曹操移动版【飞影】= 纯修饰器，住 :mod:`sgs.skills.target_change`；
- 孙坚移动版【英魂】= 本模块（准备阶段钩子 + 座位 → 选项两步流水线 + 手写主体）；
- 徐晃移动版【断粮】= 转化族（lin-batch 票 03），住 :mod:`sgs.skills.heroes` 的
  ``CONVERSION_SKILLS``——一条 ``ConversionSkill`` 声明（与【国色】同一档：单源 → 延时
  锦囊）＋ 同一张声明上的 ``DISTANCE_LIMIT`` 修饰器（② 的额定距离 1 → 2）；**不**在本模块
  再声明一遍（一个技能一条定义）。

【英魂】（孙坚，吴 / 男 / 4 体力；牌面权威 = 用户 `技能.txt`，口径 = **移动版**，逐字）：

    准备阶段，若你已受伤，你可以选择一名其他角色并选择一项：
    1.令其摸X张牌，然后其弃置一张牌；2.令其摸一张牌，然后其弃置X张牌。
    （X为你已损失的体力值）

用户 2026-11-08 签的裁定（票面 `## 用户裁定` ③ 与 Q1–Q6 全按建议 A）：

- **Q1 = A**：选项标签逐字写「X」（`:data:`YINGHUN_OPTIONS` 的两个键 → 前端
  ``OPTION_LABELS`` 的两行），不按当时 X 渲染成具体数字；
- **Q2 = A**：弃置面 = 目标的**手牌 + 装备区**（不含判定区），**由目标自己选**；
- **Q3 = A**：可用牌数 < X ⇒ **全部弃置**（《官方规则集》「游戏牌·弃置」逐字，
  同条算例就是英魂自己）；
- **Q4 = A**：X 在发动时定格（帧私有 :data:`_X_KEY`），本次结算内是常数；
- **Q5 = A**：「可以」给的是**整段发动的放弃出口**（预发动那一问），发动后座位步与
  选项步都必须答（两步都 ``optional=False``）；
- **Q6 = A**：AI 按**敌对**口径登记（座位步登记进 ``_HOSTILE_SEAT_STEPS``，选项 2 正分）。

**形状**（`shapes.md` §2 第 52 行「手写主体」）：``SkillFlow`` 只装前两步
（:class:`~sgs.skills.flow.PickSeats` / :class:`~sgs.skills.flow.PickOption`），两段效果
住在 :func:`_yinghun_effect` 里，原因是三条缺一不可：

1. **张数是动态的**：弃置张数 = ``1`` 或 ``X``（按选项），而 ``PickCards.lo`` 只收定值
   ``int`` ⇒ 三步流水线表达不了"恰好 X 张"；``total=(1, X)`` 会允许"只弃 1 张"，与牌面
   和《官方规则集》都对不上。
2. **动作的先后跨"步骤 / 效果"**：必须先**摸**（可能是 X 张）再**弃**，摸牌无法表达成
   步骤 ⇒ 两段都在 ``effect`` 里按游标推进。
3. **弃置者是目标**：与 (1)(2) 叠加后定值 + 次序仍然不成立。

样板 = 典韦移动版【强袭】（选项步留流水线、后续住 ``effect``）+ 夏侯惇【刚烈】
（让别人在自己的区里选牌并弃置的手写询问）+ 郝昭【镇骨】（阶段体钩子 + 座位步）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, locate
from sgs.resolution import Frame
from sgs.state import GameState, MoveReason, SelectionSpec, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import FlowAnswers, PickOption, PickSeats, SkillFlow

__all__ = [
    "LIN_SKILL_DEFS",
    "SUNJIAN_MOBILE",
    "YINGHUN",
    "YINGHUN_DRAW_ONE_DISCARD_X",
    "YINGHUN_DRAW_X_DISCARD_ONE",
    "YINGHUN_FLOW",
    "YINGHUN_OPTIONS",
    "lin_skill_definitions",
]

SUNJIAN_MOBILE = "sunjian_mobile"
"""孙坚移动版的武将 id（数据表 ``heroes.lin_mobile.json``）。"""

YINGHUN = hero_skill_id(SUNJIAN_MOBILE, "yinghun")
"""孙坚移动版【英魂】的全限定技能 id（``hero.<武将 id>.<短 id>``）。"""

YINGHUN_DRAW_X_DISCARD_ONE = "yinghun_draw_x_discard_one"
YINGHUN_DRAW_ONE_DISCARD_X = "yinghun_draw_one_discard_x"
"""选项闭集的两个键（Q1 = A：标签逐字写「X」，见 ``options.js:OPTION_LABELS`` 的同名键）。

顺序 = 牌面两条分支的顺序（「1.令其摸X张…」「2.令其摸一张…」）。**两项恒可执行**：X = 1
时后果相同是用户 2026-11-08 裁定的**有意重复**，故两项都列、都不置灰、不合并。
"""

YINGHUN_OPTIONS: tuple[str, str] = (
    YINGHUN_DRAW_X_DISCARD_ONE,
    YINGHUN_DRAW_ONE_DISCARD_X,
)

_X_KEY = "yinghun_x"
"""帧私有：发动时定格的 X（体力上限 − 当前体力，Q4 = A）。"""

_DRAWN_KEY = "yinghun_drawn"
"""帧私有：摸牌那一段已经做过（**搬牌之前**落位，重入不重摸）。"""

_DISCARDED_KEY = "yinghun_discarded"
"""帧私有：弃置那一段**全部搬完**（搬牌内联挂起时留假，续跑接着搬同一批、不重搬）。"""

_GROUPS_KEY = "yinghun_groups"
"""帧私有：选中的牌按**来源区**分组后的 ``[(zone, (card_id, ...)), ...]``。

``move_card`` 一次只能有一个来源区（手牌 / 装备区各一批）；分组也在帧内定格，
重入不重算（与【清俭】的 ``_QINGJIAN_GROUPS_KEY`` 同一姿态）。
"""

_MOVED_KEY = "yinghun_moved"
"""帧私有：已搬完的来源组数（搬牌内联挂起后据此续搬，不重搬已完成的组）。"""

_ASK_YINGHUN_DISCARD = "hero.sunjian_mobile.yinghun.discard"
"""手写询问位置（票 22 的 ``AskIdentity``）：本技能唯一一处 ``ask_selection``。"""


def _other_alive_seats(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """座位步的目标池：「一名**其他**角色」= 存活且 ``seat != owner``，座位序。

    签名与 :data:`~sgs.skills.flow.SeatPool` 对齐（``chosen`` 对单选无意义）。池空时
    候选根本不产出（条件里同一判据先挡一次），不会发出没有候选的询问。
    """
    del frame, chosen
    return tuple(
        player.seat for player in state.players if player.alive and player.seat != owner
    )


def _yinghun_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**自己的**准备阶段 ∧ 自己已受伤 ∧ 场上有其他存活角色。

    ``PHASE_PREPARE`` 是**每个座位**的准备阶段都派发的帧外时机，故必须自校"这一步的
    回合角色是不是我"（`H2`：不写这一条就会在别人的准备阶段问孙坚）。条件为假 ⇒ 这条
    钩子连"是否发动"都不问（`edge-cases.md` 总纪律 1：不发没有候选的询问）。

    "已受伤"= ``hp < hp_max``（L741 的既有口径）；"一名其他角色"不含自己（L776②）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    player = host.state.player(int(owner))
    if not player.alive or player.hp >= player.hp_max:
        return False
    return bool(_other_alive_seats(host.state, frame, int(owner), ()))


def _yinghun_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：**目标**先摸（X 或 1 张），然后**目标**自选弃置（1 或 X 张；不足则弃光）。

    两段都由**目标执行**（用户 2026-11-08 裁定「3.对的，两者结算相同，都是【英魂】发动者
    选 1 项，令目标执行」）：摸牌是给目标摸；弃牌那一问的应答者是目标，面铺的是它自己的
    手牌 + 装备区（``reveal_hand=True``——应答者是手牌持有者本人，与【刚烈】/【镇骨】同一口径）。

    **重入纪律**（G7）：摸牌可能内联派发"获得牌后"（【清俭】一类）、弃置可能内联派发
    "失去牌后"（【连营】/【枭姬】）并挂起，续跑既不重摸也不重搬：

    - ``yinghun_drawn`` 在**摸牌之前**落位（``draw_for_hand`` 不幂等，重入再摸会多摸一遍）；
    - ``yinghun_discarded`` 在**全部来源组搬完之后**才落位——续跑会再调一次
      ``move_in_frame``，它按帧私有的 ``_MOVE_DONE`` 认出"同一次移动"、不重搬（询问本身
      也按帧内答案缓存，不会重问）。

    弃置张数是 ``min(X, 手牌 + 装备区)``（Q3 = A：不足 X 张则全部弃置），规格恒可满足；
    可用牌数 0 时该段整体不出现（不发空询问）。

    **跨区的弃置**：选取面覆盖手牌 + 装备区（Q2 = A），而 ``move_card`` 一次只接受**一个**
    来源区 ⇒ 选中的牌按来源区分组、逐组搬（与【清俭】的 ``_QINGJIAN_GROUPS_KEY`` 同一
    姿态）。同区的一批仍是一次搬牌（G4：两张手牌一条 ``card_move``，不逐张派发"失去牌后"）。
    """
    state = host.state
    owner = int(frame.data["skill_owner"])
    target = int(answers["seat"][0])
    option = str(answers["option"])
    draw_x = option == YINGHUN_DRAW_X_DISCARD_ONE

    x = frame.data.get(_X_KEY)
    if x is None:
        player = state.player(owner)
        x = player.hp_max - player.hp
        frame.data[_X_KEY] = x

    # 段 1：目标摸牌（选项 1 摸 X 张 / 选项 2 摸一张）。
    if not frame.data.get(_DRAWN_KEY):
        frame.data[_DRAWN_KEY] = True
        if host.draw_for_hand(frame, target, x if draw_x else 1) == "stay":
            return "stay"
        if host.finished:
            # 短摸即平局（G10）：既有引擎行为，这里只是不再往下结算。
            return None

    # 段 2：目标弃置（选项 1 弃 1 张 / 选项 2 弃 X 张；不足 X 张则全部弃置）。
    if frame.data.get(_DISCARDED_KEY):
        return None
    need = 1 if draw_x else x
    player = state.player(target)
    available = len(player.hand) + len(player.equip)
    if available <= 0:
        # 没有可弃的牌 ⇒ 这一段整体不出现（不发没有候选的询问）。
        frame.data[_DISCARDED_KEY] = True
        return None
    take = min(need, available)
    picked = host.ask_selection(
        frame,
        owner=_ASK_YINGHUN_DISCARD,
        key="yinghun:discard",
        actor=target,
        timing=Timing.PHASE_PREPARE,
        spec=SelectionSpec(
            zones=(Zone.HAND, Zone.EQUIP),
            total=(take, take),
            optional=False,
        ),
        # 应答的是**牌的所有者本人** ⇒ 可见槽（与【刚烈】/【镇骨】的自选口径一致）。
        build_face=lambda: host.selection_face(
            target, (Zone.HAND, Zone.EQUIP), reveal_hand=True
        ),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        # 防御分支：``optional=False`` 时引擎不会给空答案；硬塞空元组时收尾。
        frame.data[_DISCARDED_KEY] = True
        return None
    groups = frame.data.get(_GROUPS_KEY)
    if groups is None:
        buckets: dict[Zone, list[int]] = {}
        for card_id in picked:
            buckets.setdefault(locate(state, card_id).zone, []).append(card_id)
        groups = tuple((zone, tuple(ids)) for zone, ids in buckets.items())
        frame.data[_GROUPS_KEY] = groups
    moved = int(frame.data.get(_MOVED_KEY, 0))
    while moved < len(groups):
        # ``move_card`` 一次只接受一个来源区（手牌 / 装备区各一批；分组在帧内定格，
        # 重入不重算 —— 与【清俭】的 ``_QINGJIAN_GROUPS_KEY`` 同一条纪律）。
        _zone, card_ids = groups[moved]
        control = host.move_in_frame(
            frame,
            state.refs(card_ids),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=target,
            actor=target,
        )
        if control == "stay":
            # 续跑会**再调一次** ``move_in_frame``（它按 ``_MOVE_DONE`` 认出同一次移动、
            # 不重搬），故 ``yinghun_discarded`` 只在全部搬完之后落位；询问本身已按帧内
            # 答案缓存，不会重问。
            return "stay"
        moved += 1
        frame.data[_MOVED_KEY] = moved
    frame.data[_DISCARDED_KEY] = True
    return None


YINGHUN_FLOW = SkillFlow(
    name="yinghun",
    steps=(
        # 「选择一名其他角色」：不含自己、不含死者。
        PickSeats(lo=1, hi=1, pool=_other_alive_seats, optional=False, key="seat"),
        # 「并选择一项」：两项恒可执行（Q1 = A 的逐字标签；X = 1 的同后果是有意重复）。
        PickOption(options=YINGHUN_OPTIONS, optional=False, key="option"),
    ),
    effect=_yinghun_effect,
)
"""【英魂】的两步流水线：选一名其他角色 → 二选一 → 目标摸牌 → 目标自选弃置。"""


_YINGHUN = SkillDef(
    skill_id=YINGHUN,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    # 阶段体时机（不挂 "开始时 / 结束时" 两个边界）：效果挂**准备阶段**本身。
    hooks=(
        Hook(
            Timing.PHASE_PREPARE,
            flow=YINGHUN_FLOW,
            condition=_yinghun_condition,
        ),
    ),
    requirement=(
        "准备阶段，若你已受伤，你可以选择一名其他角色并选择一项："
        "1.令其摸X张牌，然后其弃置一张牌；2.令其摸一张牌，然后其弃置X张牌。"
        "（X为你已损失的体力值）"
    ),
    step_requirements={
        "seat": "选择一名其他角色",
        "option": (
            "选择一项：1.令其摸X张牌，然后其弃置一张牌；"
            "2.令其摸一张牌，然后其弃置X张牌"
        ),
        # 手写询问的 SELECT_CARDS 逐步文案：投影先试 ``skill_id`` 冒号后那段，取不到再
        # 回落到 ``"card"``（被动帧不写 ``skill_id``，故真正命中的是 ``"card"``）。
        # 两个键同文——与【刚烈】的 ``_STEP_REQUIREMENTS["ganglie"]`` 同一姿态。
        "card": "令其弃置一张牌 / X张牌",
        "discard": "令其弃置一张牌 / X张牌",
    },
)
"""孙坚移动版【英魂】的完整声明（一条技能、一条准备阶段钩子 + 两步流水线）。"""


LIN_SKILL_DEFS: tuple[SkillDef, ...] = (_YINGHUN,)
"""林包·移动版的全部技能定义（逐票追加）。"""


def lin_skill_definitions() -> tuple[SkillDef, ...]:
    """林包·移动版的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return LIN_SKILL_DEFS
