"""特征权重的**结构**与**先验中心**（`ai-view` 票 08a；spec §2.7 / §2.10 / §2.12）。

本票**不写学出来的数值**（那是票 `08b` 的事，只在训练机上做）。这里定下三件事：

1. **键的语法**（三族，名字寻址、只许尾部追加）：

   - ``b|<项名>`` —— **B 族（局面轴）**，跨模式共享。项名与
     :meth:`~sgs.agents.heuristic.Score.add` 的项名逐字相同：一个既有 ``Score.add`` 项 =
     一条特征 = 一个可学参数。值由**既有的打分器自己**产出（票 02 的 trace），本模块不另算。
   - ``c|<事实类>|<关系类>`` —— **C 族（交互）**：公开事件 × 关系类（恒定 5 值）的**计数**，
     机械展开、不人工挑选交互项。
   - ``a|<技能 id>|<触发点>|<序>|<事实类>|<关系类>`` —— **A 族（事件轴）**：效果编号
     （`GLOSSARY.md`「效果编号」）再乘公开载荷类别与关系类。它没有既有对应，是**新键**。

2. **分块**（spec §2.7 的裁定：族的边界就是可迁移性的边界）。B 族跨模式共享（牌价值 /
   期望伤害 / P(闪) 在两种模式里稳定），A / C 族**按模式各一份**
   （"对敌 / 对友做这件事值多少分"带着各模式的胜负结构）：:data:`SHARED_B` 与
   :data:`NO_IDENTITY_AC` / :data:`IDENTITY_AC` / :data:`PVE_AC`。

3. **锚 = 手写值**（用户 2026-11-25 裁定 Q2 / Q7：先验中心，不是"下限 + 叠加"的两段式）。
   票 08b 的拟合目标是 ``拟合误差 + λ·Σ(w_key − anchor[key])²``，于是：

   - 样本多 ⇒ 数据的声音盖过橡皮筋，键自由移动；
   - 样本少 ⇒ 橡皮筋把它按在 :func:`anchor` 附近，**并且是连续的**（不设"多少样本算够"的
     阈值——阈值会让 20 场 / 21 场之间分数突变，而且阈值本身是拍的）；
   - **0 样本的键读出来是 :func:`anchor`，不是 0**：新武将上线时 AI 对那条新技能仍有意
     见（读的是手写值），不会"恰好对最新那条技能没有任何意见"。

**锚为什么是"从键派生"而不是一张几百行的表**：锚只取决于键自己在命名空间里的位置——
B 族 = 既有系数（已烘进项值）、C 族 = 既有关系系数（按关系类）、A 族 = ``SKILL_VALUES``
里那条技能的手写值。派生的写法让**新武将 / 新技能自动有锚**（不必回来补表），也让
``08b`` 的拟合器只需写"拟合值"那一层::

    weight(key, mode) -> 拟合值（表里有） 或 anchor(key)（0 样本）

**本票的表全是空的**（没有任何学出来的数值）：所以每个键都读 `anchor`，而
:func:`~sgs.agents.features.linear` 在 B 族上逐 float 等于今天的 ``Score`` 总分（票面
parity 就是这一条）。票 08b 往 :meth:`WeightTable.fit` 里追加行；**只许尾部追加**——
新增一行不改任何既有键的数值。

**为什么 :func:`anchor` 要延迟 import 启发式**：票 08b 起 ``heuristic → weights`` 是
**单向**依赖（打分读权重表）。若本模块在顶层 ``import heuristic``，那条边就成环了。A 族的
锚必须来自 ``SKILL_VALUES``（手写值的唯一真源，不抄第二份），故它走一次函数内的延迟
import（模块已在 ``sys.modules`` 里，只查字典）。这条延迟 import 由
``tests/test_source_discipline.py`` 的第十节机械钉住（顶层 import 即红）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType

from sgs.data import Mode

from .weights_data import IDENTITY_AC_ROWS, SHARED_B_ROWS

__all__ = [
    "A_SKILL_ANCHOR_FALLBACK",
    "B_ANCHOR",
    "C_ANCHORS",
    "FAMILY_A",
    "FAMILY_B",
    "FAMILY_C",
    "IDENTITY_AC",
    "MODE_BLOCKS",
    "NO_IDENTITY_AC",
    "PVE_AC",
    "RELATION_CLASSES",
    "SHARED_B",
    "WeightTable",
    "a_key",
    "anchor",
    "b_key",
    "c_key",
    "family",
    "table_for",
    "unregistered_anchor",
    "weight",
]


# ---------------------------------------------------------------------------
# 键的语法：三族的唯一拼法（读取方一律经这三个构造子，不手写 f-string）
# ---------------------------------------------------------------------------

FAMILY_B = "b"
"""B 族（局面轴）的前缀：``b|<项名>``——项名就是 ``Score`` 的项名。"""

FAMILY_C = "c"
"""C 族（交互）的前缀：``c|<事实类>|<关系类>``。"""

FAMILY_A = "a"
"""A 族（事件轴）的前缀：``a|<技能>|<触发点>|<序>|<事实类>|<关系类>``。"""

_SEP = "|"
"""键的分隔符。三族的**机械类别**（``move:DRAW`` 一类）自带冒号，故分隔符不能是冒号。"""

WEIGHT_SCALE = 1000
"""拟合值的**整数刻度**（`ai-view` 票 08b）：仓里存 ``round(w × 1000)``，读口除回来。

整数不是为了省内存，是为了**逐字节可复现**：浮点 ``repr`` 跨平台 / 跨 numpy 版本会漂，
而"拟合产出的 ``weights.py`` 逐字节相同"是票面的一条验收。整数另让"整数权重 vs 浮点参考
逐条同决策"这条 parity 可机械断言（见 ``tests/test_ai_weights_parity.py``）。
"""

RELATION_CLASSES: tuple[str, ...] = (
    "self",
    "known_ally",
    "unknown",
    "known_enemy",
    "lord",
)
"""关系类的**闭集**（恒定 5 值；spec §2.7）。

顺序即语义次序（自己 → 盟友 → 未知 → 敌人 → 主公），也是派生的次序；**不加第六档**：

- ``self``：我自己；
- ``known_ally`` / ``known_enemy``：**已亮明**身份，且敌对值表给出友 / 敌
  （判据走 ``heuristic._identity_relation`` 的"已亮明"那一档，不另算一份）；
- ``unknown``：身份未亮明，或亮明了但敌对值是**中立**（内奸行的连续 H 不到 0 / 1 也是
  这一档——5 值闭集里没有"已明但中立"的第六格，那条语义留给 A / C 族的权重去学）；
- ``lord``：主公（身份公开）——**恒独占一档**，优先于敌友：对主忠方它是"不能打"的规则级
  veto，对反贼它是首要目标，两者都不是"普通敌人"的价。
"""


def b_key(item: str) -> str:
    """B 族键：``b|<项名>``（项名 = ``Score.add`` 的项名，一字不改）。"""
    if not item or _SEP in item:
        raise ValueError(f"B 族的项名必须非空且不含分隔符 {_SEP!r}：{item!r}")
    return f"{FAMILY_B}{_SEP}{item}"


def c_key(fact_class: str, relation: str) -> str:
    """C 族键：``c|<事实类>|<关系类>``。"""
    return f"{FAMILY_C}{_SEP}{_checked(fact_class, '事实类')}{_SEP}{_checked_relation(relation)}"


def a_key(skill_id: str, trigger: str, order: int, fact_class: str, relation: str) -> str:
    """A 族键：``a|<技能 id>|<触发点>|<序>|<事实类>|<关系类>``（效果编号 + 载荷类别 + 关系）。

    效果编号的三段**不包成一个参数对象**：键语法是本模块的公共面，而测试要直接拿"从声明
    派生的 ``(技能, 流水线名, 0)``"拼键（那一刻没有活的 ``Cause``，且 ``Cause`` 住
    ``sgs.state``——本模块只依赖 ``sgs.data`` 的规则常数）。多一层打包只会让调用方各写一次
    拆包，收不回任何东西。
    """
    if isinstance(order, bool) or not isinstance(order, int) or order < 0:
        raise ValueError(f"效果编号的序必须是非负整数（主动技恒 0）：{order!r}")
    return (
        f"{FAMILY_A}{_SEP}{_checked(skill_id, '技能 id')}"
        f"{_SEP}{_checked(trigger, '触发点')}{_SEP}{order}"
        f"{_SEP}{_checked(fact_class, '事实类')}{_SEP}{_checked_relation(relation)}"
    )


def _checked(text: str, what: str) -> str:
    if not isinstance(text, str) or not text or _SEP in text:
        raise ValueError(f"{what}必须是非空且不含分隔符 {_SEP!r} 的字符串：{text!r}")
    return text


def _checked_relation(relation: str) -> str:
    if relation not in C_ANCHORS:
        raise ValueError(
            f"关系类必须取自闭集 {RELATION_CLASSES}（恒定 5 值，不加第六档）：{relation!r}"
        )
    return relation


def family(key: str) -> str:
    """键属于哪一族（``"b"`` / ``"c"`` / ``"a"``）；认不出的前缀 ⇒ ``ValueError``。

    **fail-closed**：空键 / 未知前缀不静默落默认族——静默默认会让"键写错了"变成"读到
    一个别的族的锚"，那种错不会有人发现。
    """
    if not isinstance(key, str) or not key:
        raise ValueError(f"权重键必须是非空字符串：{key!r}")
    head, _, rest = key.partition(_SEP)
    if head not in (FAMILY_A, FAMILY_B, FAMILY_C) or not rest:
        raise ValueError(f"权重键的前缀必须是 {FAMILY_A}/{FAMILY_B}/{FAMILY_C}：{key!r}")
    return head


# ---------------------------------------------------------------------------
# 锚：手写值（本票唯一有数值的一层）
# ---------------------------------------------------------------------------

B_ANCHOR = 1.0
"""B 族的锚 = **1.0**（既有系数），不是"另给一个手写价"。

B 族的特征值**就是**既有打分器算出的那一项分数（``Score`` 的 trace，票 02）：系数
（``_DAMAGE_POINT_VALUE`` / ``_RELATION_FOCUS`` …）已经烘在项值里。故 B 族权重是**相对
既有打分的修正倍数**，锚 = 1.0 逐位等于"不改就是今天的分数"——票面 parity 因此是逐 float
相等，不是"近似"。

这也回答了"为什么不把系数再抄进本模块"：抄一份就是第二份真源，而训练 / 推理两边的系数
必然漂（spec §6 的头号风险）。
"""

C_ANCHORS: Mapping[str, float] = MappingProxyType(
    {
        # 自己：关系项不给自己符号（自己的动作按**收益**计价，不按关系）。
        "self": 0.0,
        # 已明盟友：−`heuristic._RELATION_FOCUS`（= `_RESPOND_VALUE` = 5.0）。这是既有实现里
        # "打这个目标值多少"的唯一幅度（票 01 的 `_RELATION_FOCUS × (P敌 − P友)`）。
        "known_ally": -5.0,
        # 未知：(0, 0) ⇒ 关系项 0（I1 的"算不出就中性"）。硬标签是降低安全边际的改动，不做。
        "unknown": 0.0,
        # 已明敌人：+`_RELATION_FOCUS`（与盟友同一个幅度的另一半）。
        "known_enemy": 5.0,
        # 主公：**锚 0**，不是 `_LORD_VETO_PENALTY`（−50）。−50 是 **veto 的实现值**（把候选
        # 压到不可选），不是打分系数；拿它当锚会让"每一次涉及主公的公开事实"先验惩罚 50 分，
        # 而主公在关系轴上本来就是"已明盟友 / 已明敌人"两档之一（按我的身份），已由上面两
        # 行定价。主公这一档要不要另有一条价，是 08b 从数据里学的事，本票不先验地下结论。
        "lord": 0.0,
    }
)
"""C 族每个**关系类**的锚（既有关系系数）。键集恰等于 :data:`RELATION_CLASSES`。"""

A_SKILL_ANCHOR_FALLBACK = 0.0
"""A 族的锚在**整张**语义表都没有可读的标量时读到 0.0（空表的逃生值，正常不会发生）。

日常的"查不到"**不**落在这里：见 :func:`unregistered_anchor`——新技能 / 形状档技能要读到
"手写值的典型价"，不是 0。
"""


@lru_cache(maxsize=1)
def _skill_anchors() -> Mapping[str, float]:
    """``SKILL_VALUES`` 里**常量档**的手写值：``技能 id -> 那个常量``。

    只认常量档：形状档的打分函数要 ``(query, action, view)`` 才有值，不是一个数，故不进
    本表（见 :func:`unregistered_anchor`）。``_constant`` 产出的打分器带 ``anchor``
    属性（只读元数据，不参与打分），本函数只读它。

    **延迟 import**：理由见模块 docstring（08b 起 ``heuristic → weights`` 单向，顶层 import
    会成环）。本函数在 ``sys.modules`` 里查字典，不是 IO，推理仍是纯函数。
    """
    from . import heuristic

    return MappingProxyType(
        {
            skill_id: float(scorer.anchor)
            for skill_id, (scorer, _note) in heuristic.SKILL_VALUES.items()
            if getattr(scorer, "anchor", None) is not None
        }
    )


@lru_cache(maxsize=1)
def unregistered_anchor() -> float:
    """A 族里**读不到标量**的那条技能读到的手写值：常量档的**众数**（今天 = 2.0）。

    两种键落在这里：语义表里**没有**这条技能（效果子技能 / 装备被动一类"从不被问是否发动"
    的声明），或它在表里是**形状档**（``_score_rende`` 一类，值依赖局面、**不是一个数**）。

    **为什么不给 0**：票面要的是"0 样本的键读出来是 anchor，不是 0"。新武将上线时它的技能
    正好落这一格——读到 0 就成了"AI 恰好对最新那条技能没有任何意见"（08b 票面点名的症状）。
    众数是**手写值的典型价**（今天 163 个常量档里 2.0 出现 45 次），由表自身派生，故新技能
    一进表就自动有中心；形状档也不会被一个 0 按在"什么都不做"上。

    平局取**较小的值**（确定性：不许让字典序偶然决定先后）；表里一个常量都没有时才落
    :data:`A_SKILL_ANCHOR_FALLBACK`。
    """
    counts: dict[float, int] = {}
    for value in _skill_anchors().values():
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return A_SKILL_ANCHOR_FALLBACK
    return min(counts, key=lambda value: (-counts[value], value))


def anchor(key: str) -> float:
    """键的**先验中心**（手写值）：0 样本的键读它，不是 0。

    三族的来源（spec §2.7）：B 族 = 既有系数（:data:`B_ANCHOR`）；C 族 = 既有关系系数
    （:data:`C_ANCHORS`，按关系类）；A 族 = ``SKILL_VALUES`` 里那条技能的手写值（形状档 /
    查不到 ⇒ :func:`unregistered_anchor` 的"典型价"，**不是 0**）。

    **对任何合法键都是全函数**——"键存在但没有样本"是常态（新武将），不许抛
    ``KeyError``：那会把"还没学到"变成"AI 崩了"。
    """
    which = family(key)
    _head, _, rest = key.partition(_SEP)
    if which == FAMILY_B:
        return B_ANCHOR
    if which == FAMILY_C:
        fact_class, sep, relation = rest.partition(_SEP)
        if not sep or not fact_class:
            raise ValueError(f"C 族键必须是 c|<事实类>|<关系类>：{key!r}")
        _checked_relation(relation)
        return C_ANCHORS[relation]
    # A 族：五段（技能 / 触发点 / 序 / 事实类 / 关系类）。
    parts = rest.split(_SEP)
    if len(parts) != 5:
        raise ValueError(
            f"A 族键必须是 a|<技能>|<触发点>|<序>|<事实类>|<关系类>：{key!r}"
        )
    skill_id, _trigger, order, _fact_class, relation = parts
    _checked(skill_id, "技能 id")
    if not order.isdigit():
        raise ValueError(f"A 族键的序必须是非负整数：{key!r}")
    _checked_relation(relation)
    return _skill_anchors().get(skill_id, unregistered_anchor())


# ---------------------------------------------------------------------------
# 权重表：名字寻址、只许尾部追加
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeightTable:
    """一族（或一个模式的族组合）的**拟合值**表：名字寻址、**只许尾部追加**。

    一行 = ``(键, 学出来的值)``。表里没有的键**不是错误**——读它的人（:func:`weight`）
    回落 :func:`anchor`（"0 样本不归零"）。08b 的拟合器只往 :meth:`fit` 追加行；不重排、
    不删、不改既有行，故"新增一行不改任何既有键的数值"是**结构事实**，不是纪律口号。

    冻结值类型 + ``fit`` 返回新表（不改原地）：拟合过程因此可复现、可比较（"这一版权重
    与上一版的 diff 只有新增"这句话是可核的）。
    """

    name: str
    """表名（只用于报错 / 台账可读）。"""

    families: tuple[str, ...]
    """这张表收哪几族的键（``("b",)`` 或 ``("a", "c")``）；写错族 ⇒ 构造期炸。"""

    rows: tuple[tuple[str, float], ...] = ()
    """``(键, 拟合值)`` 的**追加序**（先来的在前；不排序——顺序本身就是台账）。"""

    def __post_init__(self) -> None:
        seen: dict[str, int] = {}
        for index, (key, value) in enumerate(self.rows):
            if family(key) not in self.families:
                raise ValueError(
                    f"表 {self.name!r} 只收 {self.families} 族，收到 {key!r}"
                )
            if key in seen:
                raise ValueError(
                    f"表 {self.name!r} 里 {key!r} 重复（第 {seen[key]} 行与第 {index} 行）："
                    "权重表按名字寻址，重复键等于两条互相覆盖的账"
                )
            seen[key] = index
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"表 {self.name!r} 的 {key!r} 不是整数刻度值（×{WEIGHT_SCALE}）：{value!r}"
                )

    def keys(self) -> tuple[str, ...]:
        """已有拟合值的键（追加序）。"""
        return tuple(key for key, _value in self.rows)

    def values(self) -> tuple[int, ...]:
        """已有拟合值的**整数刻度**（与 :meth:`keys` 同序）；"尾部追加不改既有数值"的判据读它。"""
        return tuple(value for _key, value in self.rows)

    def value(self, key: str) -> int | None:
        """这个键的拟合值（整数刻度）；``None`` = **没有样本**（读方回落到 :func:`anchor`）。"""
        for candidate, value in self.rows:
            if candidate == key:
                return value
        return None

    def fit(self, key: str, value: int) -> "WeightTable":
        """**尾部追加**一行（返回新表；既有行逐位不变）。

        重复键 ⇒ ``ValueError``：同一条账写两次，后者会静默盖掉前者，而"哪个数算数"就没人
        答得上来。要改一行，是 08b 重跑拟合、整表重出的活。串族 / 非有限值由
        :meth:`__post_init__` 在构造期一起把关（这里不抄第二遍）。
        """
        if key in self.keys():
            raise ValueError(f"表 {self.name!r} 里已经有 {key!r}：权重表只许尾部追加")
        return WeightTable(
            name=self.name, families=self.families, rows=(*self.rows, (key, value))
        )


SHARED_B = WeightTable(
    name="shared_b", families=(FAMILY_B,), rows=SHARED_B_ROWS
)
"""**B 族（局面轴）**的拟合值表，跨模式共享（spec §2.7：牌价值 / 期望伤害 / P(闪) 跨模式稳定）。

行由 ``tools/sgs_train/fit.py`` 生成（``weights_data.py``）。**空表** = 还没有拟合数据
⇒ 每个 B 键读 :data:`B_ANCHOR` = 1.0 ⇒ 打分逐 float 等于今天的 ``Score`` 总分；有行时
``weight`` 交回 ``行值 / WEIGHT_SCALE``。
"""

NO_IDENTITY_AC = WeightTable(name="no_identity_ac", families=(FAMILY_A, FAMILY_C))
"""无身份局的 A + C 族（"对敌 / 对友做这件事值多少分"带着各模式的胜负结构）。"""

IDENTITY_AC = WeightTable(
    name="identity_ac", families=(FAMILY_A, FAMILY_C), rows=IDENTITY_AC_ROWS
)
"""身份局的 A + C 族。"""

PVE_AC = WeightTable(name="pve_ac", families=(FAMILY_A, FAMILY_C))
"""PvE 的 A + C 族（阵营全公开：关系退化成确定 0 / 1，见 `MODE_SITES` 的 `relation` 格）。"""

MODE_BLOCKS: Mapping[Mode, WeightTable] = MappingProxyType(
    {
        Mode.NO_IDENTITY: NO_IDENTITY_AC,
        Mode.IDENTITY: IDENTITY_AC,
        Mode.PVE: PVE_AC,
    }
)
"""``Mode -> A + C 族块``：**每个模式各一份**（spec §2.7 / Q10）。

**新增模式时**：这张表的键集由 ``tests/test_ai_features.py`` 钉成 ``set(Mode)``（漏一格即
红），所以新模式票必须顺手给出它自己的 A + C 块——"借用别的模式的权重"正是"符号相反"那
条检验要拆开的东西。
"""


def table_for(mode: Mode) -> WeightTable:
    """这个模式的 A + C 族块；未知模式 ⇒ ``KeyError``（不静默借用身份局）。"""
    return MODE_BLOCKS[mode]


def weight(key: str, mode: Mode = Mode.IDENTITY) -> float:
    """**读一个键的权重**：有拟合值读拟合值（整数刻度 ÷ :data:`WEIGHT_SCALE`），没有读
    :func:`anchor`（0 样本不归零）。

    B 族查 :data:`SHARED_B`（跨模式共享），A / C 族查该模式的块。这是唯一的读取口——
    08b 把它接进打分路径时，读法不再有第二种。
    """
    if family(key) == FAMILY_B:
        fitted = SHARED_B.value(key)
    else:
        fitted = table_for(mode).value(key)
    return anchor(key) if fitted is None else fitted / WEIGHT_SCALE


@lru_cache(maxsize=4096)
def b_weight_cached(item: str) -> float:
    """``b|<项名>`` 的权重的缓存读口（打分路径专用）。

    打分时每个候选的每个 ``Score.add`` 项都要查一次权重：键空间是**有限闭集**（项名由源码
    里的字面量决定），``lru_cache`` 因此不会变成无界缓存。**只给 B 族**——B 族跨模式共享、
    与 ``mode`` 无关，缓存键只有项名一个，天然无歧义；A / C 族的读法照旧走
    :func:`weight`（它们不是决策路径上的热读口）。
    """
    return weight(b_key(item))


def has_fitted_b() -> bool:
    """B 族**有没有**拟合值（决策路径据此决定要不要走"带权打分"那条更贵的路）。

    空表（08a 的落地态）= 今天的分数逐 float 不变，``HeuristicPlayer.respond`` 不打开
    trace、不多分配一个元组。
    """
    return bool(SHARED_B.rows)
