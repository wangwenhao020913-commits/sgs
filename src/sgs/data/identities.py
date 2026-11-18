"""身份与模式：由座位数唯一决定的**规则常数**（票 13 §1 / §2）。

与内容包 / 牌堆档位 / 规则配置三分：配比不随 pack 变化、与用哪些牌无关、
也不是本局做出的"选择"——它是座位数的函数。因此与 ``INITIAL_HP`` / ``DRAW_COUNT``
同姿态：模块级不可变常量 + 一个 fail-closed 访问器。

``Identity`` / ``Mode`` 放这里而不是 ``state/models.py``：``state`` 本就 import ``sgs.data``，
反过来会造出 ``data ↔ state`` 的模块级环。

**M7 票 07 的两处扩容**：``Mode`` 增第三种 ``PVE``（``RuleConfig.mode`` 因此由派生属性变成
字段，缺省仍按座位数派生）；``Identity`` 增 ``ALLY`` / ``ENEMY`` 两个 PvE 阵营值。
两者都**不进** ``IDENTITY_ROSTERS``：PvE 的阵营由 ``PveConfig.identities`` 逐座给出
（全公开、不走身份子流），身份局规则在 PvE 一律不生效。
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
    "NO_IDENTITY_LAYOUTS",
    "Identity",
    "Mode",
    "ModeSite",
    "roster_for",
]


class Mode(str, Enum):
    """一局的模式：标准局两种按座位数派生，PvE 必须显式声明（票 13 §1 / M7 票 07）。

    ``RuleConfig.mode`` 是**字段**（进 header，票 07 起），但缺省值按座位数派生
    （2 = 无身份局、其余 = 身份局）——既有 60 条录像与全部调用点因此不用写它。
    PvE 没有座位数可推（3 人既可能是身份局也可能是 PvE），**只在显式声明时成立**。
    取值名一律用术语表的**规范名**（无身份局 / 身份局），不用"决斗"这类被术语表
    明确回避的口语（`GLOSSARY.md` §身份与模式），也避开牌名 id ``duel``（【决斗】）。
    """

    NO_IDENTITY = "no_identity"
    """无身份局（`GLOSSARY.md`：不发放身份、胜负只看存活数的两人模式）。"""

    IDENTITY = "identity"
    """身份局（座位数 4~8：``IDENTITY_ROSTERS`` 里的那几档）。"""

    PVE = "pve"
    """剧本模式的战斗（M7）：阵营由 ``PveConfig.identities`` 逐座给出，胜负由逐场条件判。"""


NO_IDENTITY_LAYOUTS: frozenset[int] = frozenset({2})
"""无身份局的**构成表**：只有 2 人档（两个座位都不发身份）。

它与 :data:`IDENTITY_ROSTERS` 同姿态——"本模式有哪些人数的布局"是一张表，不是一段区间。
``RuleConfig`` 的构造期校验因此键在"这张表里有没有这个人数的布局"上，而不是键在
"2、4~8"这类硬编码的人数区间上（用户 2026-11-19 裁定：人数不是模式判据，规则才是）。
3 人局落在两张表的**缝里**：无身份局没有 3 人档、身份局的配比表也没有 3 人档，
故 3 人必须由 ``pve`` 的自证布局（``PveConfig.identities``）承担，缺省派生因此拒它。
"""


MODE_SITE_SLOTS: tuple[str, ...] = ("relation", "victory", "steps", "projection")
"""``MODE_SITES`` 每行固定四格（顺序即表的读法）。

四格各自回答什么、「不适用」怎么写、以及新增模式时要裁的那笔账，见
``docs/agents/ai-adaptation.md`` §3.3——正文只放那一份，这里不抄第二份。
"""


class ModeSite(NamedTuple):
    """``MODE_SITES`` 的一格：字符串引用（``"模块路径:属性路径"``）+ 一句理由。

    ``refs`` 为空 = 本模式在这格上"不适用"，此时 ``reason`` 是一句**理由**。为什么用字符串
    引用而不是直接 import（``data`` 不反向依赖 ``engine`` / ``agents``）、解析断言在哪，见
    ``docs/agents/ai-adaptation.md`` §3.3。
    """

    refs: tuple[str, ...]
    reason: str


MODE_SITES: Mapping[Mode, Mapping[str, ModeSite]] = MappingProxyType(
    {
        Mode.NO_IDENTITY: MappingProxyType(
            {
                "relation": ModeSite(
                    (),
                    "不适用：无身份局不发放身份 ⇒ 没有队伍可认、也没有配比可减；`identity` 恒"
                    "None，故 `sgs.agents.heuristic._identity_relation` 对别的座位恒答 (0, 0)"
                    "（只有自己 (0, 1)）——AI 侧因此没有要读的 identity 面",
                ),
                "victory": ModeSite(
                    ("sgs.engine.game:Engine.check_game_end",),
                    "存活数 ≤ 1 直接判（票 13 §3）；该分支内联在唯一终局入口的 else 支，"
                    "没有独立的纯求解器",
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
                    ("sgs.agents.heuristic:_identity_relation",),
                    "`PlayerPublic.identity`（本人 / 主公 / 已阵亡 / `identity_visible`）先从"
                    "`IDENTITY_ROSTER` 里减掉，存活未知座位按人头均分得 `(P(敌), P(友))`"
                    "（I1 身份保守档，票 21；只作权重、不硬判）；算不出就退回中性的 (0, 0)",
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
        Mode.PVE: MappingProxyType(
            {
                "relation": ModeSite(
                    ("sgs.agents.heuristic:_identity_relation",),
                    "PvE 开局全部身份公开（票 01 §3）⇒ 读到的是**确定的**友 / 敌，不走概率档；"
                    "读点仍是同一个函数、同一个 `PlayerPublic.identity` 字段（不新增读点）。"
                    "`ALLY → (0, 1)` / `ENEMY → (1, 0)` 两个映射归票 08：今天它俩都落空集、"
                    "答 (0, 0)，`tests/test_ai_adaptation.py` 的 strict xfail 钉住这个空窗",
                ),
                "victory": ModeSite(
                    ("sgs.engine.victory:solve_pve_outcome",),
                    "只读「配置 + 存活集合 + 当前轮次」三样（票 01 §4）：全歼 / 存活 N 轮 / "
                    "N 轮内全歼 / 击败 Boss 四类逐场条件，胜负两侧都只判 `ally` / `enemy`；"
                    "接进 `Engine.check_game_end` 的 PvE 分支归票 08",
                ),
                "steps": ModeSite(
                    ("sgs.engine.steps:build_steps",),
                    "DEATH 帧用 `DEATH_STEPS`——与无身份局同表（PvE 不亮身份、不奖惩、"
                    "没有主公死亡锚点，票 01 §5）",
                ),
                "projection": ModeSite(
                    ("sgs.state.projection:_visible_identity",),
                    "不适用新实现：PvE 开局全部 `identity_visible`（票 01 §3），"
                    "现有投影已认这个字段 ⇒ 投影层零改动",
                ),
            }
        ),
    }
)
"""``Mode`` → 四个格子（票 17 的第三张登记表；断言住 ``tests/test_ai_adaptation.py``）。

新增一个模式 = 给这里加一行、四格都要有答案（读法与责任划分见
``docs/agents/ai-adaptation.md`` §3.3 与 §4）。
"""


class Identity(str, Enum):
    """身份（票 13 §2）：英文值，中文只出现在 :data:`IDENTITY_LABELS` 里。

    判定一律比枚举成员；把中文串塞进状态会让"同一篇文档只用一个名字"失效。

    M7 票 07 增 ``ALLY`` / ``ENEMY``：它们**只**出现在 PvE（``PveConfig.identities`` 逐座
    给出、开局即公开），不进 :data:`IDENTITY_ROSTERS` 的配比表，身份局的那三条奖惩规则
    在 PvE 一律不生效。
    """

    LORD = "lord"
    LOYALIST = "loyalist"
    REBEL = "rebel"
    RENEGADE = "renegade"
    ALLY = "ally"
    ENEMY = "enemy"


IDENTITY_LABELS: Mapping[Identity, str] = {
    Identity.LORD: "主公",
    Identity.LOYALIST: "忠臣",
    Identity.REBEL: "反贼",
    Identity.RENEGADE: "内奸",
    Identity.ALLY: "友",
    Identity.ENEMY: "敌",
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
这张表**只**服务身份局：PvE 的阵营由 ``PveConfig.identities`` 逐座自证（票 07 起），
无身份局根本不发身份（:data:`NO_IDENTITY_LAYOUTS`），两者都不从这里取配比。
"""


def roster_for(player_count: int) -> tuple[Identity, ...]:
    """某人数下的身份配比多重集；只认 4~8，其余 fail-closed（票 13 §2）。

    "没有这个人数"因此是一条**构成表事实**（``IDENTITY_ROSTERS`` 里没有那一档），
    不是人数区间判断——3 人局落在这里的 ``ValueError`` 上，与"3 这个数字不好"无关。
    """
    try:
        return IDENTITY_ROSTERS[player_count]
    except KeyError:
        raise ValueError(f"没有 {player_count} 人局的身份配比（只有 4~8）") from None
