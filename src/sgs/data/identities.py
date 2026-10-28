"""身份与模式：由座位数唯一决定的**规则常数**（票 13 §1 / §2）。

与内容包 / 牌堆档位 / 规则配置三分：配比不随 pack 变化、与用哪些牌无关、
也不是本局做出的"选择"——它是座位数的函数。因此与 ``INITIAL_HP`` / ``DRAW_COUNT``
同姿态：模块级不可变常量 + 一个 fail-closed 访问器。

``Identity`` / ``Mode`` 放这里而不是 ``state/models.py``：``state`` 本就 import ``sgs.data``，
反过来会造出 ``data ↔ state`` 的模块级环。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import NamedTuple

__all__ = [
    "IDENTITY_LABELS",
    "IDENTITY_ROSTERS",
    "MODE_SITES",
    "MODE_SITE_SLOTS",
    "Identity",
    "Mode",
    "ModeSite",
    "roster_for",
]


class Mode(str, Enum):
    """一局的模式，由 :attr:`~sgs.data.RuleConfig.player_count` 派生（票 13 §1）。

    **不是配置字段**：值域被座位数切分干净，任何"模式开关"都会给 2 人局 header 多一键。
    取值名一律用术语表的**规范名**（无身份局 / 身份局），不用"决斗"这类被术语表
    明确回避的口语（`GLOSSARY.md` §身份与模式），也避开牌名 id ``duel``（【决斗】）。
    """

    NO_IDENTITY = "no_identity"
    """无身份局（`GLOSSARY.md`：不发放身份、胜负只看存活数的两人模式）。"""

    IDENTITY = "identity"
    """身份局（座位数 4~8）。"""


MODE_SITE_SLOTS: tuple[str, ...] = ("relation", "victory", "steps", "projection")
"""``MODE_SITES`` 每行固定四格（顺序即表的读法；AI 适配契约，票 17 / spec §4.4–§4.5）。

- ``relation``：**关系**——AI 从哪个字段认敌友（新模式的票要先回答"队伍公开 ⇒ 退化成
  0/1，还是隐藏 ⇒ 走身份概率档"）；
- ``victory``：**胜负求解**——终局怎么判、AI 要不要读"我方已败"；
- ``steps``：**步骤表**——``FrameKind`` 帧的步骤序列怎么装配；
- ``projection``：**投影**——身份 / 队伍在参与者视角里怎么可见。

四格必须都回答：指向实现（``refs``）或写清"不适用"的理由（``reason``）。前两格是新增
模式的票在**写票时**就要裁掉的账，不许留给实现者临场（spec §4.4 的"三问 + 两声明"）。
"""


class ModeSite(NamedTuple):
    """``MODE_SITES`` 的一格：站点引用（``"模块路径:属性路径"``）+ 一句理由。

    用**字符串引用**而不是直接 import：``data`` 不反向依赖 ``engine`` / ``agents`` /
    ``state``，否则会造出模块级环。解析留给审计（``tests/test_ai_adaptation.py`` 用
    ``importlib`` 逐条核到可调用者）——所以"登记了但指不到东西"是可断言的，而不是靠读
    代码自觉。

    ``refs`` 为空 = 本模式在这格上"不适用"，此时 ``reason`` 必须是一句**理由**；有引用时
    ``reason`` 说明"这一格由什么实现 / 为什么是这几个站点"。
    """

    refs: tuple[str, ...]
    reason: str


MODE_SITES: Mapping[Mode, Mapping[str, ModeSite]] = MappingProxyType(
    {
        Mode.NO_IDENTITY: MappingProxyType(
            {
                "relation": ModeSite(
                    (),
                    "不适用：无身份局不发放身份 ⇒ 没有队伍，AI 的 `_is_friendly` 只剩"
                    "'自己'（其余座位一律不是盟友）；不读 identity 面",
                ),
                "victory": ModeSite(
                    ("sgs.engine.game:Engine.check_game_end",),
                    "存活数 ≤ 1 直接判（票 13 §3）；该分支内联在唯一终局入口的 else 支，"
                    "尚无独立纯函数——新模式应尽量落成 `victory.py` 里的纯求解器",
                ),
                "steps": ModeSite(
                    ("sgs.engine.steps:build_steps",),
                    "DEATH 帧用 `DEATH_STEPS`（不亮身份、不求身份胜负），其余帧两模式同表",
                ),
                "projection": ModeSite(
                    (),
                    "不适用：没有身份 ⇒ `PlayerView` 里不存在要抹掉的字段，投影无需派生",
                ),
            }
        ),
        Mode.IDENTITY: MappingProxyType(
            {
                "relation": ModeSite(
                    (
                        "sgs.agents.heuristic:_is_friendly",
                        "sgs.agents.heuristic:_is_revealed_enemy",
                    ),
                    "只认 `PlayerPublic.identity`（本人 / 主公 / 已阵亡可见）；隐藏身份不猜"
                    "（ADR-011 的不做身份推断）——所以敌友是两档，不是概率",
                ),
                "victory": ModeSite(
                    ("sgs.engine.victory:solve_identity_outcome",),
                    "按本批死亡事实 + 批次末存活集合求解（票 25 / 决策回票 14）；"
                    "`Engine._solve_identity` 只是取状态再调它",
                ),
                "steps": ModeSite(
                    ("sgs.engine.steps:build_steps",),
                    "DEATH 帧用 `IDENTITY_DEATH_STEPS`（亮身份 → 记死亡事实 → 检测终局）",
                ),
                "projection": ModeSite(
                    ("sgs.state.projection:_visible_identity",),
                    "投影时就把不可见的身份抹成 None（票 13 §6）：'身份不泄露'是结构事实",
                ),
            }
        ),
    }
)
"""``Mode`` → 四个格子（票 17 的第三张登记表；断言住 ``tests/test_ai_adaptation.py``）。

新增一个模式 = 给这里加一行、四格都要有答案；`tests/test_ai_adaptation.py` 会核
"每个 ``Mode`` 成员都在表里"与"登记为实现的站点能 ``importlib`` 解析到可调用者"。
"""


class Identity(str, Enum):
    """身份（票 13 §2）：英文值，中文只出现在 :data:`IDENTITY_LABELS` 里。

    判定一律比枚举成员；把中文串塞进状态会让"同一篇文档只用一个名字"失效。
    """

    LORD = "lord"
    LOYALIST = "loyalist"
    REBEL = "rebel"
    RENEGADE = "renegade"


IDENTITY_LABELS: Mapping[Identity, str] = {
    Identity.LORD: "主公",
    Identity.LOYALIST: "忠臣",
    Identity.REBEL: "反贼",
    Identity.RENEGADE: "内奸",
}
"""身份 → 中文标签（渲染用；判定读枚举成员，不读标签）。"""


IDENTITY_ROSTERS: Mapping[int, tuple[Identity, ...]] = {
    4: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE),
    5: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
    6: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
    7: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
    8: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
}
"""4~8 人的身份配比多重集（票 04 主表），主-忠-反-内。

备选配置不采用（票 04）：每个配置只有一个主公、至多两个忠臣、至多一个内奸。
"""


def roster_for(player_count: int) -> tuple[Identity, ...]:
    """某人数下的身份配比多重集；只认 4~8，其余 fail-closed（票 13 §2）。"""
    try:
        return IDENTITY_ROSTERS[player_count]
    except KeyError:
        raise ValueError(f"没有 {player_count} 人局的身份配比（只有 4~8）") from None
