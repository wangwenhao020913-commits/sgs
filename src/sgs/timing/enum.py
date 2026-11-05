"""权威时机表（票 01 研究产物的枚举落地）。

83 个节点一次建全。**M2 定格**（票 17 §3 / 票 32）：**64 个**是已实现类
（其中若干是**空实现**：dispatch 了但无效果），**19 个**是只留枚举位类。

在已实现类之上再落一层**记录流可观测性**（票 18 的登记：L2 边界与阶段间节点不
dispatch、不记）：

- :data:`DISPATCH_WHITELIST`（53）：真的会 dispatch、可在记录流里观测到的节点；
- :data:`EMPTY_LOG`（11）：已实现类里**空日志**的 L2 节点——枚举位保留，但流程结构性
  地不经过，故"从实际记录流收集 dispatch 集合 ⊇ 64"按字面不成立（最多 53 条可见，
  票 18 已登记此口径）。覆盖清单以 :data:`DISPATCH_WHITELIST` 为白名单，见
  ``tests/support/timing_coverage.py``。

以上三个数字是 **M2 定格**。此后两批解封：M3 的 ``MOVE_AFTER``（票 07）与 wuming 票 04
的 ``MAXLIFE_INCREASE_BEFORE/AFTER``（加体力上限一侧）——今天的实测计数见
:data:`IMPLEMENTED` / :data:`DISPATCH_WHITELIST` / :data:`ENUM_ONLY` 的 docstring，
账本在 ``06`` §5「程序化约定与偏离一手」的"时机四轴计数"段。

纪律：

- 枚举值就是**官方标识符**（如 ``RESOLVE_BEFORE_EFFECT``），层号与序号由
  :attr:`Timing.layer` / :attr:`Timing.order` **派生**，不落盘（票 06 §4）。
- 伪时机（``防止伤害`` / ``扣减体力`` / ``回复体力时`` / ``增减体力上限时、后`` /
  ``亮出拼点牌后`` / 裸 ``濒死``）**不得**成为枚举成员；它们不是官方时机名。
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "DISPATCH_WHITELIST",
    "EMPTY_LOG",
    "ENUM_ONLY",
    "IMPLEMENTED",
    "FORBIDDEN_TIMING_NAMES",
    "Timing",
]


class Timing(str, Enum):
    """官方时机节点；``value`` 是标识符，层号与序号是派生属性。"""

    def __new__(cls, identifier: str, layer: int, order: int) -> "Timing":
        member = str.__new__(cls, identifier)
        member._value_ = identifier
        member.layer = layer
        member.order = order
        return member

    # 层号与序号由本表派生，不落盘（票 06 §4）。
    layer: int
    order: int

    # ---- L0 游戏级（回合之外）------------------------------------------------
    GAME_HERO_REVEALED = ("GAME_HERO_REVEALED", 0, 1)
    GAME_INITIAL_HAND_DEALING = ("GAME_INITIAL_HAND_DEALING", 0, 2)
    GAME_START = ("GAME_START", 0, 3)

    # ---- L1 回合级 -----------------------------------------------------------
    TURN_BEFORE_START = ("TURN_BEFORE_START", 1, 1)
    TURN_START = ("TURN_START", 1, 2)

    # ---- L2 阶段层（6 阶段 × 3 + 5 阶段间 + 回合结束前后）--------------------
    PHASE_PREPARE_START = ("PHASE_PREPARE_START", 2, 1)
    PHASE_PREPARE = ("PHASE_PREPARE", 2, 2)
    PHASE_PREPARE_END = ("PHASE_PREPARE_END", 2, 3)
    INTERVAL_PREPARE_TO_JUDGE = ("INTERVAL_PREPARE_TO_JUDGE", 2, 4)
    PHASE_JUDGE_START = ("PHASE_JUDGE_START", 2, 5)
    PHASE_JUDGE = ("PHASE_JUDGE", 2, 6)
    PHASE_JUDGE_END = ("PHASE_JUDGE_END", 2, 7)
    INTERVAL_JUDGE_TO_DRAW = ("INTERVAL_JUDGE_TO_DRAW", 2, 8)
    PHASE_DRAW_START = ("PHASE_DRAW_START", 2, 9)
    PHASE_DRAW = ("PHASE_DRAW", 2, 10)
    PHASE_DRAW_END = ("PHASE_DRAW_END", 2, 11)
    INTERVAL_DRAW_TO_PLAY = ("INTERVAL_DRAW_TO_PLAY", 2, 12)
    PHASE_PLAY_START = ("PHASE_PLAY_START", 2, 13)
    PHASE_PLAY_IDLE = ("PHASE_PLAY_IDLE", 2, 14)
    PHASE_PLAY_END = ("PHASE_PLAY_END", 2, 15)
    INTERVAL_PLAY_TO_DISCARD = ("INTERVAL_PLAY_TO_DISCARD", 2, 16)
    PHASE_DISCARD_START = ("PHASE_DISCARD_START", 2, 17)
    PHASE_DISCARD = ("PHASE_DISCARD", 2, 18)
    PHASE_DISCARD_END = ("PHASE_DISCARD_END", 2, 19)
    INTERVAL_DISCARD_TO_FINISH = ("INTERVAL_DISCARD_TO_FINISH", 2, 20)
    PHASE_FINISH_START = ("PHASE_FINISH_START", 2, 21)
    PHASE_FINISH = ("PHASE_FINISH", 2, 22)
    PHASE_FINISH_END = ("PHASE_FINISH_END", 2, 23)
    TURN_END = ("TURN_END", 2, 24)
    TURN_AFTER_END = ("TURN_AFTER_END", 2, 25)

    # ---- L4 使用结算前（牌级）------------------------------------------------
    USE_CHOOSE_TARGET = ("USE_CHOOSE_TARGET", 4, 1)
    USE_ON_USE = ("USE_ON_USE", 4, 2)
    USE_DESIGNATE_TARGET = ("USE_DESIGNATE_TARGET", 4, 3)
    USE_BECOME_TARGET = ("USE_BECOME_TARGET", 4, 4)
    USE_AFTER_DESIGNATE_TARGET = ("USE_AFTER_DESIGNATE_TARGET", 4, 5)
    USE_AFTER_BECOME_TARGET = ("USE_AFTER_BECOME_TARGET", 4, 6)

    # ---- L5 使用结算中 + 后（逐目标）-----------------------------------------
    RESOLVE_START = ("RESOLVE_START", 5, 1)
    RESOLVE_BEFORE_EFFECT = ("RESOLVE_BEFORE_EFFECT", 5, 2)
    RESOLVE_EFFECT = ("RESOLVE_EFFECT", 5, 3)
    RESOLVE_AFTER_EFFECT = ("RESOLVE_AFTER_EFFECT", 5, 4)
    RESOLVE_END = ("RESOLVE_END", 5, 5)
    RESOLVE_AFTER_END = ("RESOLVE_AFTER_END", 5, 6)

    # ---- L6 打出事件（M1 无打出场景，只留枚举位）-----------------------------
    PLAY_DECLARE = ("PLAY_DECLARE", 6, 1)
    PLAY_ON_PLAY = ("PLAY_ON_PLAY", 6, 2)

    # ---- L7 伤害事件 ---------------------------------------------------------
    DAMAGE_BEFORE_START = ("DAMAGE_BEFORE_START", 7, 1)
    DAMAGE_START = ("DAMAGE_START", 7, 2)
    DAMAGE_DEAL = ("DAMAGE_DEAL", 7, 3)
    DAMAGE_TAKE = ("DAMAGE_TAKE", 7, 4)
    DAMAGE_AFTER_DEAL = ("DAMAGE_AFTER_DEAL", 7, 5)
    DAMAGE_AFTER_TAKE = ("DAMAGE_AFTER_TAKE", 7, 6)
    DAMAGE_END = ("DAMAGE_END", 7, 7)
    DAMAGE_AFTER_END = ("DAMAGE_AFTER_END", 7, 8)

    # ---- L8 扣减体力事件 -----------------------------------------------------
    LIFE_DECREASE_BEFORE = ("LIFE_DECREASE_BEFORE", 8, 1)
    LIFE_DECREASE_ON = ("LIFE_DECREASE_ON", 8, 2)
    LIFE_DECREASE_AFTER = ("LIFE_DECREASE_AFTER", 8, 3)

    # ---- L9 失去体力事件（无来源，只留枚举位）--------------------------------
    LIFE_LOSE_ON = ("LIFE_LOSE_ON", 9, 1)
    LIFE_LOSE_AFTER = ("LIFE_LOSE_AFTER", 9, 2)

    # ---- L10 回复体力事件 ----------------------------------------------------
    RECOVER_VALUE = ("RECOVER_VALUE", 10, 1)
    RECOVER_AFTER = ("RECOVER_AFTER", 10, 2)

    # ---- L11 体力上限事件（加 / 减是两个独立流程，各只有「前 / 后」）----------
    MAXLIFE_DECREASE_BEFORE = ("MAXLIFE_DECREASE_BEFORE", 11, 1)
    MAXLIFE_DECREASE_AFTER = ("MAXLIFE_DECREASE_AFTER", 11, 2)
    MAXLIFE_INCREASE_BEFORE = ("MAXLIFE_INCREASE_BEFORE", 11, 3)
    MAXLIFE_INCREASE_AFTER = ("MAXLIFE_INCREASE_AFTER", 11, 4)

    # ---- L12 濒死事件 --------------------------------------------------------
    NEAR_DEATH_ENTER = ("NEAR_DEATH_ENTER", 12, 1)
    NEAR_DEATH_AT = ("NEAR_DEATH_AT", 12, 2)
    NEAR_DEATH_AFTER = ("NEAR_DEATH_AFTER", 12, 3)

    # ---- L13 死亡事件 --------------------------------------------------------
    DEATH_BEFORE_START = ("DEATH_BEFORE_START", 13, 1)
    DEATH_REVEAL_IDENTITY_BEFORE = ("DEATH_REVEAL_IDENTITY_BEFORE", 13, 2)
    DEATH_REVEAL_IDENTITY_AFTER = ("DEATH_REVEAL_IDENTITY_AFTER", 13, 3)
    DEATH_ON = ("DEATH_ON", 13, 4)
    DEATH_AFTER = ("DEATH_AFTER", 13, 5)

    # ---- L14 判定事件（只留枚举位）-------------------------------------------
    JUDGE_ON = ("JUDGE_ON", 14, 1)
    JUDGE_CARD_BEFORE_EFFECT = ("JUDGE_CARD_BEFORE_EFFECT", 14, 2)
    JUDGE_CARD_AFTER_EFFECT = ("JUDGE_CARD_AFTER_EFFECT", 14, 3)

    # ---- L15 拼点事件（只留枚举位）-------------------------------------------
    RANK_COMPARE_ON = ("RANK_COMPARE_ON", 15, 1)
    RANK_COMPARE_RESULT_AFTER = ("RANK_COMPARE_RESULT_AFTER", 15, 2)

    # ---- L16 牌移动事件（M1 不建时机，只留枚举位）----------------------------
    MOVE_BEFORE = ("MOVE_BEFORE", 16, 1)
    MOVE_AFTER = ("MOVE_AFTER", 16, 2)

    # ---- L17 牌状态改变（只留枚举位）-----------------------------------------
    STATE_FLIPPED = ("STATE_FLIPPED", 17, 1)
    STATE_CHAINED = ("STATE_CHAINED", 17, 2)
    HERO_REVEALED = ("HERO_REVEALED", 17, 3)
    HERO_REMOVED = ("HERO_REMOVED", 17, 4)
    CARD_SHOWN = ("CARD_SHOWN", 17, 5)

    @property
    def label(self) -> str:
        """人类可读渲染：``L5.2 RESOLVE_BEFORE_EFFECT``（仅供显示，不进记录字段）。"""
        return f"L{self.layer}.{self.order} {self.value}"


IMPLEMENTED: tuple[Timing, ...] = (
    # L0：发牌与开局（票 13 §2）
    Timing.GAME_INITIAL_HAND_DEALING,
    Timing.GAME_START,
    # L1：回合轮换的起点与"回合内/外"分界
    Timing.TURN_BEFORE_START,
    Timing.TURN_START,
    # L2：全部 25 个阶段节点（空阶段只发阶段体，阶段间不 dispatch）
    Timing.PHASE_PREPARE_START,
    Timing.PHASE_PREPARE,
    Timing.PHASE_PREPARE_END,
    Timing.INTERVAL_PREPARE_TO_JUDGE,
    Timing.PHASE_JUDGE_START,
    Timing.PHASE_JUDGE,
    Timing.PHASE_JUDGE_END,
    Timing.INTERVAL_JUDGE_TO_DRAW,
    Timing.PHASE_DRAW_START,
    Timing.PHASE_DRAW,
    Timing.PHASE_DRAW_END,
    Timing.INTERVAL_DRAW_TO_PLAY,
    Timing.PHASE_PLAY_START,
    Timing.PHASE_PLAY_IDLE,
    Timing.PHASE_PLAY_END,
    Timing.INTERVAL_PLAY_TO_DISCARD,
    Timing.PHASE_DISCARD_START,
    Timing.PHASE_DISCARD,
    Timing.PHASE_DISCARD_END,
    Timing.INTERVAL_DISCARD_TO_FINISH,
    Timing.PHASE_FINISH_START,
    Timing.PHASE_FINISH,
    Timing.PHASE_FINISH_END,
    Timing.TURN_END,
    Timing.TURN_AFTER_END,
    # L4 / L5：使用流程六 + 六时机
    Timing.USE_CHOOSE_TARGET,
    Timing.USE_ON_USE,
    Timing.USE_DESIGNATE_TARGET,
    Timing.USE_BECOME_TARGET,
    Timing.USE_AFTER_DESIGNATE_TARGET,
    Timing.USE_AFTER_BECOME_TARGET,
    Timing.RESOLVE_START,
    Timing.RESOLVE_BEFORE_EFFECT,
    Timing.RESOLVE_EFFECT,
    Timing.RESOLVE_AFTER_EFFECT,
    Timing.RESOLVE_END,
    Timing.RESOLVE_AFTER_END,
    # L6：打出（票 19 的独立构造子；无目标、不占使用次数）
    Timing.PLAY_DECLARE,
    Timing.PLAY_ON_PLAY,
    # L7：伤害骨架五时机
    Timing.DAMAGE_DEAL,
    Timing.DAMAGE_TAKE,
    Timing.DAMAGE_AFTER_DEAL,
    Timing.DAMAGE_AFTER_TAKE,
    Timing.DAMAGE_END,
    # L8：扣减体力三时机
    Timing.LIFE_DECREASE_BEFORE,
    Timing.LIFE_DECREASE_ON,
    Timing.LIFE_DECREASE_AFTER,
    # L10：回复体力二时机
    Timing.RECOVER_VALUE,
    Timing.RECOVER_AFTER,
    # L11：加体力上限二时机（wuming 票 04 解封**加**侧；减侧 `MAXLIFE_DECREASE_*` 继续留枚举位）
    Timing.MAXLIFE_INCREASE_BEFORE,
    Timing.MAXLIFE_INCREASE_AFTER,
    # L12：濒死三时机
    Timing.NEAR_DEATH_ENTER,
    Timing.NEAR_DEATH_AT,
    Timing.NEAR_DEATH_AFTER,
    # L13：死亡四时机（无身份局跳过 REVEAL_BEFORE）——M2 身份局五段
    Timing.DEATH_BEFORE_START,
    Timing.DEATH_REVEAL_IDENTITY_BEFORE,
    Timing.DEATH_REVEAL_IDENTITY_AFTER,
    Timing.DEATH_ON,
    Timing.DEATH_AFTER,
    # L14：判定三时机（票 26 起 JUDGE 帧在 PHASE_JUDGE 体内 dispatch）
    Timing.JUDGE_ON,
    Timing.JUDGE_CARD_BEFORE_EFFECT,
    Timing.JUDGE_CARD_AFTER_EFFECT,
    # L15：拼点二时机（huo-taishici-xunyu 票 03 解封；RANK_COMPARE 帧的步骤表 dispatch）
    Timing.RANK_COMPARE_ON,
    Timing.RANK_COMPARE_RESULT_AFTER,
    # L16：牌移动事件（票 07 的 M3 唯一解封；**只解封 after**，`MOVE_BEFORE` 继续留枚举位）
    Timing.MOVE_AFTER,
)
"""已实现类（M2 定格 64 + M3 解封 1 + wuming 票 04 解封 2 + huo-taishici-xunyu 票 03 解封 2 = 69）：流程真的会走到（允许空实现 / 空日志）。

M3 的增量只有 :attr:`Timing.MOVE_AFTER` 一个（票 07 / ADR-010 的不对称解封）：它在
:meth:`~sgs.engine.game.Engine.move_in_frame` 这个**唯一移动入口**里内联派发，且只在
当前局面有注册在该时机上的实例时派发（惰性守卫）——无武将模式因此零派发、M2 基准一行不变。

wuming 票 04 的增量是 L11 的**加体力上限**二时机（``MAXLIFE_INCREASE_BEFORE/AFTER``）：
由 ``FrameKind.MAXLIFE`` 事件帧的步骤表派发（唯一入口 ``Engine.raise_hp_max``，首个调用者 =
票 01 的【天成】，``src/sgs/skills/start.py``）。**减**侧两个
（``MAXLIFE_DECREASE_*``）继续留枚举位：既有
``Engine.reduce_hp_max``（界徐庶【潜心】/ 界吕蒙【勤学】）不迁进帧，迁会改既有
``hp_max_change.frame_id`` 并多两条时机记录（E1）。
"""

_IMPLEMENTED_MEMBERS = frozenset(IMPLEMENTED)

ENUM_ONLY: tuple[Timing, ...] = tuple(
    timing for timing in Timing if timing not in _IMPLEMENTED_MEMBERS
)
"""只留枚举位类（M2 定格 19 − M3 解封 1 − wuming 票 04 解封 2 − huo-taishici-xunyu 票 03 解封 2 = 14）：不 dispatch（遍历 ``Timing`` 保证顺序确定）。

M3 只从中移走 :attr:`Timing.MOVE_AFTER` 一个（票 07）；wuming 票 04 再移走
``MAXLIFE_INCREASE_BEFORE/AFTER`` 两个（"加"侧真的 dispatch，"减"侧仍无载体）；huo-taishici-xunyu
票 03 再移走 L15 的 ``RANK_COMPARE_ON`` / ``RANK_COMPARE_RESULT_AFTER``（``RANK_COMPARE`` 帧的
步骤表 dispatch）。其余 14 个仍无载体。
"""

EMPTY_LOG: tuple[Timing, ...] = (
    # 准备阶段：只发阶段体 PHASE_PREPARE，边界不 dispatch。
    Timing.PHASE_PREPARE_START,
    Timing.PHASE_PREPARE_END,
    Timing.INTERVAL_PREPARE_TO_JUDGE,
    # 判定阶段的**边界节点**不 dispatch（阶段体 PHASE_JUDGE 发；判定事件 L14 自票 26
    # 起在阶段体内由 JUDGE 帧 dispatch——判定阶段因此**不是**空阶段，票 03/票 17）。
    Timing.PHASE_JUDGE_START,
    Timing.PHASE_JUDGE_END,
    Timing.INTERVAL_JUDGE_TO_DRAW,
    # 出牌 / 摸牌 / 弃牌阶段的阶段体与边界都发（弃牌阶段不是空阶段），只有阶段之间不发。
    Timing.INTERVAL_DRAW_TO_PLAY,
    Timing.INTERVAL_PLAY_TO_DISCARD,
    Timing.INTERVAL_DISCARD_TO_FINISH,
    # 结束阶段：只发阶段体 PHASE_FINISH，边界不 dispatch。
    Timing.PHASE_FINISH_START,
    Timing.PHASE_FINISH_END,
)
"""已实现类里的**空日志**节点（M2 定格 = 11）：枚举位保留、记录流里不出现。

票 18 承票 06 §7 的明文登记：5 个 ``INTERVAL_*`` 与准备 / 判定 / 结束三个阶段的
``*_START`` / ``*_END`` 一律不 dispatch、不记；摸牌 / 出牌 / 弃牌三个阶段的阶段体与边界都发。
这些节点"结构性存在"（枚举成员 + 阶段口径），由 :data:`EMPTY_LOG` 单列，不混进
:data:`DISPATCH_WHITELIST`。

**票 26 起的读法**：本常量界定的是**边界节点**，不是"该阶段整体为空"。判定阶段的边界仍
留在本表（票 17 §3 明定"判定阶段边界节点**不转**"），但它的阶段体自票 26 起会 dispatch
L14 的判定事件——判定阶段因此不再空过。53/64/19/11 的 M2 定格由票 32 一次性执行。
"""

_EMPTY_LOG_MEMBERS = frozenset(EMPTY_LOG)

DISPATCH_WHITELIST: tuple[Timing, ...] = tuple(
    timing for timing in IMPLEMENTED if timing not in _EMPTY_LOG_MEMBERS
)
"""**dispatch 白名单**（M2 定格 53 + M3 解封 1 + wuming 票 04 解封 2 = 56）：真的会 dispatch、可在记录流里观测的节点。

= 67 个已实现类 − 11 个空日志节点（票 18 登记、票 25 落地、票 32 定格）。覆盖清单
（``tests/support/timing_coverage.py``）的键集必须**恰等于**本集合，且每个节点都要在
它声明的基准 / 用例产出的记录流里实测出现。
"""

FORBIDDEN_TIMING_NAMES: tuple[str, ...] = (
    "防止伤害",
    "扣减体力",
    "回复体力时",
    "增减体力上限时",
    "增减体力上限后",
    "亮出拼点牌后",
    "濒死",
)
"""伪时机：**不得**作为时机值出现（票 01 §B2 + 票 12 R-5 的 6 个 + 裸"濒死"）。"""
