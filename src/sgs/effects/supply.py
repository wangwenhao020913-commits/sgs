"""**需求（Demand）× 供给（Supply）**——需要（使用 / 打出）牌时的两个值对象与匹配器。

用户 2026-11-20 的提案（`spec.md` §1 逐字）把"每一档能提供什么"与"这一问要什么"变成
可比较的两件事；本模块只放**值对象 + 匹配 + 合法性谓词**，不产生候选
（候选生成在 ``effects/actions.py``）。三个消费点（全部在供需闸门链上）：

- :func:`demand_for`：把窗口的 ``required`` + ``response_suits`` 打包成维度可加的
  :class:`Demand`；
- :func:`supplies_of_action`：**一条动作的全部供给**（票 04 的"一动作多供给"口子；
  除预转化外恒等于 :func:`supply_of_action` 的一元组）。闸门读它，故"这一条动作能提供
  什么"只有一份判据；
- :func:`supply_satisfies` / :func:`supply_is_legal`：匹配核心与**合法性闸门**（票 02）——
  ``effects/actions.py::_supply_gate`` 按后者收窄交到玩家手上的那一面。**候选期判不了**的
  供给只有一种形态——提供者**没声明**（``supply is None``，票 07 起判据的唯一载体），合法性对
  它失败关闭（见该函数的 docstring）。

**维度可加**（用户 Q3 的落地口径）：牌名 / 花色 / 颜色 / 点数 / 虚拟·转化 / 转化源张数。
今天只有前两维有生产者（牌名 = ``required``、花色 = ``response_suits``，单骑第三段），
其余四维**同形上线、暂时恒 ``None``**；将来烈弓 / 同颜色响应只新增一个 :class:`Demand`
实例，不改框架。``None`` = 不限；``()`` = 记录为空 ⇒ 什么也满足不了（值的三态）。

**无花色 / 无点数的供给确定地不满足对应维度的限制**（不是"未知"，spec §3 第 3 条）：
【八卦阵】/【八阵】的纯虚拟【闪】与多源转化（【丈八蛇矛】/ 单骑）都没有花色 ⇒ 花色
需求下必然不满足。"候选期不知道"由**没声明**（提供者不报供给）单独表达：匹配器不收这种
输入，合法性（票 02 的 :func:`supply_is_legal`）在需求带收窄维度时失败关闭。票 07 删掉了
票 01 立的 ``Supply.unknown`` 字段（"候选期不可知"）：票 03 的 relay 与票 04 的预转化都改成
**可判**之后它没有生产者了（W6-06a 的"死字段"教训），失败关闭因此只剩"没声明"这一档。

**需求传递**（``kind="relay"``，票 03）是第三条路：它不是"不知道"，而是"不在这一档判"——
【护驾】/【激将】不产出转化 / 虚拟牌，真正做的是把**同一份** :class:`Demand` 依次传给
同势力角色（用户 Q2），收窄维度在**传到的那个座位的询问**里才展开。故供给是**具体**的
（牌名就是声明事实），匹配时**过牌名维度之后就不判死**——见
:func:`_mismatch` 里那条分支。持有者自己被挡住（【义绝】封手牌）时**连候选都不产出**
（``skills/lord_request.py::LordSkill.offer`` 在候选生成前就撤掉这条 ``ActivateSkill``），
故闸门见不到它——判据不读 ``GameState``。

**预转化**（``kind="preconversion"``，票 04）是第四条路，但它**没有自己的判据**：来源牌
就是那张手牌，牌面事实照搬 ``CardRef``，故匹配与 ``kind="transformed"`` 的单源转化逐字
相同（用户 Q1：预转化与转化同形）。它的特殊性只在**供给的粒度**：一个提供者候选对应
**每一张手牌**一条供给——见 :func:`supplies_of_action` 与 :func:`_preconversion_supplies`。

循环 import（票面 §5 的纪律）：``effects/actions.py`` 与本模块互相看得见（``actions`` 的
供需闸门读本模块，本模块的 :func:`demand_for` / :func:`_preconversion_product` 读
``actions`` 的两个谓词），故本模块对 ``.actions`` 与 ``sgs.skills.catalog`` 一律**函数内
惰性 import**（仓里既有姿态，见 ``actions.py:_transformed_peaches``）；反方向（``actions``
模块级 import 本模块）安全——本模块的模块级依赖只有 ``sgs.state`` 与 ``.behaviors``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.state import (
    ActivateSkill,
    CardRef,
    DodgeContext,
    PeachContext,
    PlayCard,
    QueryKind,
    RespondContext,
    UseCard,
)

from .behaviors import (
    CARD_BEHAVIORS,
    DODGE_RESPONSE_CARD,
    PEACH_RESPONSE_CARD,
    SLASH_FAMILY_KEY,
)

__all__ = [
    "Demand",
    "PRECONVERSION_SUPPLY_KIND",
    "RELAY_SUPPLY_KIND",
    "SUPPLY_KINDS",
    "Supply",
    "demand_for",
    "supplies_of_action",
    "supply_is_legal",
    "supply_of_action",
    "supply_satisfies",
]

SELF_RESCUE_CARD = "wine"
"""求桃窗口里**濒死者自救**那第二张牌名（【酒】）。

它与【桃】同档是既有事实的两处表达：真牌路径读 ``TargetRule.near_death_self_only``
（``effects/actions.py:_peach``），提供者路径读 ``skills/active.py:_GUOHUO_SELF_RESCUE_NAMES``。
需求若只说【桃】，自救的那张【酒】会被匹配器判成不满足 ⇒ 闸门会把它从面里删掉——故这里读
同一份语境事实（``asker == dying``），牌名仍是行为表里的牌型 id，不另写"哪些牌能自救"的名单。
"""

SUPPLY_KINDS: frozenset[str] = frozenset(
    {"real", "virtual", "transformed", "relay", "preconversion"}
)
"""供给种类的闭集（票面 §1）：真牌 / 纯虚拟 / 转化 / 需求传递 / 预转化。

为什么是闭集：匹配按它分支（relay 在牌名之后不判死、预转化在 :func:`supplies_of_action`
里展开），未知种类会让分支静默落进"真牌"那一档 —— 与 ``sgs.effects`` 的"声明错误应当
可见"同一姿态。
"""

RELAY_SUPPLY_KIND = "relay"
"""**需求传递**那一档（``Supply.kind`` 的闭集成员；票 03 起唯一载体 =【护驾】/【激将】）。

本模块的读者 = 匹配器 :func:`_mismatch`（牌名之后不判死）。写成一个常量而不是散落的
字符串：改名时不能只改一半。
"""

PRECONVERSION_SUPPLY_KIND = "preconversion"
"""**预转化**那一档（``Supply.kind``；need-card-supply 票 04 起唯一载体 =【蛊惑】）。

用户 2026-11-20 Q1 的口径（spec §6 Q1 逐字）：蛊惑把任意一张手牌转化为另一张手牌，在通过
"质疑校验"之前是**预转化**状态。故供给与转化档**同形**——一条 ``Supply`` 的来源牌面就是
那一张被扣置的手牌（§16.3：转化牌的花色 / 点数 / 颜色**继承来源牌**），匹配因此与
``kind="transformed"`` 的单源转化**逐字相同**（本模块不给它第二套比对）。

与 relay 的区别：relay 是"这一档不在我这儿判"（牌名之后不判死），预转化是**逐张可判**
——故它是 :func:`supplies_of_action` 里那个"一动作多供给"的承载：
一个 ``ActivateSkill`` 对应**每一张手牌**一条 ``Supply``（档的进面与否 = 这些供给里有
没有一条合法）。
"""


@dataclass(frozen=True, slots=True)
class Supply:
    """候选期的一条**供给描述**：这一档此刻能提供什么样的牌。

    - ``product``：产物牌名 id（如 ``"dodge"`` / ``"slash"`` / ``"fire_slash"``）；
    - ``suit`` / ``rank`` / ``color``：牌面事实；``None`` = **恒无**（纯虚拟 / 多源转化），
      **不是**"不知道"（spec §3 第 3 条）——候选期判不了的提供者**不报供给**（``None``），
      由合法性闸门失败关闭，见 :func:`supply_is_legal`；
    - ``kind``：:data:`SUPPLY_KINDS` 之一；
    - ``source_count``：转化源张数（真牌 / 纯虚拟恒 1）。

    票 07 删掉了票 01 立的 ``unknown`` 字段（"候选期不可知"）：票 03 / 04 之后没有真实声明
    会置它，而"判不了"的唯一形态变成"提供者没声明"（``Supply`` 根本不产生），失败关闭落在
    :func:`supply_is_legal` 的 ``supply is None`` 分支上。
    """

    product: str | None = None
    suit: str | None = None
    rank: int | None = None
    color: str | None = None
    kind: str = "real"
    source_count: int = 1

    def __post_init__(self) -> None:
        if self.kind not in SUPPLY_KINDS:
            raise ValueError(f"未知的供给种类：{self.kind!r}（可选 {sorted(SUPPLY_KINDS)}）")


@dataclass(frozen=True, slots=True)
class Demand:
    """一次询问的**精确需求**，六个维度都可加（``None`` = 不限、``()`` = 记录为空）。

    今天有生产者的只有 ``names``（窗口的 ``required``）与 ``suits``（``response_suits``，
    单骑第三段）；其余四维同形上线、暂时恒 ``None``——不是死字段：构造口与匹配器现在就
    写全（用户 Q3）。
    """

    names: tuple[str, ...] | None = None
    suits: tuple[str, ...] | None = None
    colors: tuple[str, ...] | None = None
    ranks: tuple[int, ...] | None = None
    kinds: tuple[str, ...] | None = None
    source_counts: tuple[int, ...] | None = None


def _name_allowed(name: str, product: str | None) -> bool:
    """牌名维度：逐字相等，或产物与它**同一族**（族键 = ``CardBehavior.count_key``）。

    族判据只有一份：``is_slash``（= :func:`~sgs.effects.satisfies_required` 的唯一放宽）
    读的就是 ``count_key == "slash"``，故火【杀】/ 雷【杀】/ 转化【杀】在【杀】族需求下
    都满足，而这里**不另抄**一张牌名名单。
    """
    if product is None:
        return False
    if product == name:
        return True
    behavior = CARD_BEHAVIORS.get(product)
    return behavior is not None and behavior.count_key == name


def _listed(allowed: tuple[Any, ...] | None, value: Any) -> bool:
    """一个维度的三态判定：``None`` = 不限；``()`` = 什么也不满足；否则按白名单。"""
    if allowed is None:
        return True
    return value is not None and value in allowed


def _mismatch(demand: Demand, supply: Supply) -> bool:
    """这条供给有没有哪个维度不满足需求（``True`` = 不满足）。

    **需求传递不判死**（``kind="relay"``，need-card-supply 票 03）：牌名维度之后直接
    ``False``。牌名仍是**声明事实**（产物牌名不落在这一问的牌名维度里 ⇒ 这一档压根不是这一问
    的提供者，与真牌 / 转化同一条判据），而花色 / 颜色 / 点数 / 虚拟·转化 / 转化源张数
    五个收窄维度**原样交给下游**——【护驾】把「需要方块闪」这个需求传给盟友，答案在那个座位
    的询问里（用户 2026-11-20 Q2：「护驾的发动无须交验合法性，因为不是直接提供转化 / 虚拟
    牌，而是全场逐一询问魏势力角色」）。

    这条判据住在**匹配器**而不是合法性闸门：闸门对可判的供给就是委托本函数，两边不可能
    对同一条供给给出两个答案。它**不读** ``GameState``（票 02 的纪律：纯函数），"持有者自己
    被挡住"在候选生成之前就已经把这一档删掉了。
    """
    if demand.names is not None and not any(
        _name_allowed(name, supply.product) for name in demand.names
    ):
        return True
    if supply.kind == RELAY_SUPPLY_KIND:
        return False
    if not _listed(demand.suits, supply.suit):
        return True
    if not _listed(demand.colors, supply.color):
        return True
    if not _listed(demand.ranks, supply.rank):
        return True
    if not _listed(demand.kinds, supply.kind):
        return True
    if not _listed(demand.source_counts, supply.source_count):
        return True
    return False


def supply_satisfies(demand: Demand | None, supply: Supply | None) -> bool:
    """这条供给满不满足这份需求（= 闸门 :func:`supply_is_legal` 的匹配核心）。

    - 没有供给 ⇒ ``False``；没有需求 ⇒ ``True``（不限）；
    - 否则按维度序列判（:func:`_mismatch`；需求传递在牌名之后不判死）。

    它只看**具体的**供给（每个 ``Supply`` 的牌面事实都是已知的）："提供者没声明"（``None``）
    与票 07 删掉的"候选期不可知"（``unknown``）都不是它的输入——那一格由
    :func:`supply_is_legal` 回答（票 02 起）。
    """
    if supply is None:
        return False
    if demand is None:
        return True
    return not _mismatch(demand, supply)


_NARROWED_DIMENSIONS: tuple[str, ...] = (
    "suits",
    "colors",
    "ranks",
    "kinds",
    "source_counts",
)
"""**收窄**维度（牌名之外的五个，spec §3 / Q3）：候选期看不到就判不了的限制。

牌名不在里面：它是每一扇需要牌窗口的**基础要求**（窗口登记行 / 候选生成口已经按它收窄过），
而**没声明**供给的提供者按它放行——它们只登记在"产物就是这一问要的牌名"的窗口上
（``ProviderWindows.keys``）。这五个维度才是"限制生效 ⇒ 失败关闭"的触发条件。
"""


def _narrowed(demand: Demand) -> bool:
    """这份需求带不带一条**收窄**（任一非牌名维度非 ``None``；``()`` 也算收窄）。"""
    return any(getattr(demand, dimension) is not None for dimension in _NARROWED_DIMENSIONS)


def supply_is_legal(demand: Demand | None, supply: Supply | None) -> bool:
    """**合法性闸门**（need-card-supply 票 02）：这一次需求放不放行这条供给。

    它回答的是"该不该进 ``legal_actions`` 的那一面"：

    - 供给**有声明**（``supply`` 不是 ``None``）⇒ 按维度匹配（需求传递那一种在牌名之后
      不判死，见 :func:`_mismatch`）；
    - 提供者**没声明**供给（``supply is None``）⇒ **只要需求带一条收窄维度就失败关闭**
      （= 票 01 之前那道"多禁"的同一结论）；没有收窄维度时照常放行（今天的窗口里它们本来
      就是候选）。票 02 的闸门起初把这一档读成"不筛它"（失败开放），票 07 修回失败关闭：
      这条分支因此**有生产读者**——``_supply_gate`` 对 ``supplies_of_action(...) is None``
      的动作正是传 ``None`` 进来。判据仍只有这一处：闸门里不另写"哪些维度算收窄"。

    **失败关闭只住在这里**（票面 §1 的判据只有一处）：匹配器 :func:`supply_satisfies` 只看
    具体的供给（票 07 删掉了 ``Supply.unknown``），故两边对同一条供给不可能给出相反回答。

    需求传递（票 03）**不走这条分支**：它是可判的供给，条件成立与否写在候选生成之前（持有者
    自己被 【义绝】 一类禁令封住时**连候选都不产出**）⇒ 这里见不到"该挡的 relay"，
    也**不**按技能名白名单放行任何一档。预转化（票 04）同理：闸门读到的是**逐张来源牌**的
    具体供给（:func:`supplies_of_action`），不是一条"没声明"的提供者。
    """
    if demand is None:
        return True
    if supply is None:
        return not _narrowed(demand)
    return supply_satisfies(demand, supply)


def demand_for(
    kind: QueryKind,
    payload: Any = None,
    *,
    dying: int | None = None,
    asker: int | None = None,
) -> Demand | None:
    """一次询问的 :class:`Demand`；**不是**需要牌窗口（含改判）⇒ ``None``。

    两个有生产者的维度就在这里接上（票面 §1）：

    - **牌名** = 窗口的 ``required``（【闪】窗口恒为 ``dodge``；响应窗口取自身载荷）；
    - **花色** = ``response_suits``（单骑第三段；``None`` 不限 / ``()`` 记录为空 /
      非空白名单三态原样进需求，不在这里塌成两态）。两扇带类型化载荷的窗口（``DODGE`` /
      ``RESPOND``）各读自己那一份：两扇的 ``response_suits`` 是**同一个事实**（承载帧的
      ``RESPONSE_SUITS_KEY``），票 02 起 ``RESPOND`` 这一扇也接上（合法性闸门要用它）。

    ``asker`` / ``dying`` 只服务求桃窗口的自救读法（见 :data:`SELF_RESCUE_CARD`）。
    窗口判据走 :func:`sgs.effects.actions.is_need_card_window`（``_window_floor`` 那一族），
    故"算不算需要牌窗口"与引擎分面**同源**。
    """
    from .actions import is_need_card_window

    if not is_need_card_window(kind, payload):
        return None
    if kind is QueryKind.DODGE:
        ctx = payload if isinstance(payload, DodgeContext) else DodgeContext()
        return Demand(names=(DODGE_RESPONSE_CARD,), suits=ctx.response_suits)
    if kind is QueryKind.PEACH:
        names = [PEACH_RESPONSE_CARD]
        if dying is not None and asker is not None and int(asker) == int(dying):
            names.append(SELF_RESCUE_CARD)
        return Demand(names=tuple(names))
    if kind is QueryKind.RESPOND:
        if not isinstance(payload, RespondContext):
            raise ValueError("RESPOND 询问必须给出 RespondContext")
        return Demand(
            names=None if payload.required is None else (payload.required,),
            # 票 02 补上这一维：``RESPOND`` 的载荷与 ``DODGE`` 同形地带着"这一次使用只许哪些
            # 花色的牌响应"（票 01 只接了 DODGE 那一扇）。合法性闸门在 ``RESPOND`` 窗口上
            # 必须与今天 ``_response_suit_allowed`` 的结论逐条相同，故它得先看得见这份事实。
            suits=payload.response_suits,
        )
    return None


def supply_of_action(action: Any) -> Supply | None:
    """一条候选动作的**供给描述**（**单条**）；``None`` = 它不是供给或提供者没声明。

    这是"动作 → 供给"的**唯一映射**（票 02 建立；闸门与预转化展开都读它，两处若各写一份，
    同一条动作就可能对"这一条到底能提供什么"给出两个答案）。

    预转化的那一档是**一动作多供给**：``ActivateSkill`` 的声明只说"我是预转化"，逐张来源牌
    的具体供给要展开（票 04）。那个口子开在 :func:`supplies_of_action` 上——本函数保持
    **一元**语义，故读"这一条动作的全部供给"请用后者。
    """
    if isinstance(action, ActivateSkill):
        return _declared_supply(action.skill_id)
    if isinstance(action, (UseCard, PlayCard)):
        return _supply_of_card(action.card)
    return None


def supplies_of_action(
    action: Any,
    *,
    state: Any,
    seat: int,
    kind: QueryKind,
    payload: Any = None,
    dying: int | None = None,
) -> tuple[Supply, ...] | None:
    """一条候选动作的**全部**供给（票 04 的"一动作多供给"口子）。

    返回值的三态与读取方：

    - ``None``：不是供给（``Pass``）或提供者**没声明**供给 ⇒ 闸门把 ``None`` 交给
      :func:`supply_is_legal`（票 07 §1：需求带收窄维度 ⇒ 失败关闭；只有牌名 ⇒ 照常进面）——
      闸门自己不另判一次；
    - ``()``：声明了、此刻却**一张也给不出来**（【蛊惑】被【义绝】封住手牌）⇒ 闸门把它删掉
      （"供给 none ⇒ 不进面"）；
    - 否则：这一条动作的全部供给。除预转化外恒为 :func:`supply_of_action` 的一元组。

    **闸门读这一个函数**：一条动作只要**有一条**供给合法就进面，"算出来一张都不行"才失败
    关闭（票面 §2）。预转化展开因此不需要第二份"这张来源牌能不能当这一问的牌"的枚举
    ——匹配器与闸门对可判供给本来就是同一条 :func:`_mismatch`。

    展开只发生在 ``kind == PRECONVERSION_SUPPLY_KIND`` 的**声明**上，不看技能名 / 技能 id：
    将来第二个预转化提供者只要声明同一个 kind 就自动接上（与 relay 一族同姿态）。
    """
    declared = supply_of_action(action)
    if declared is None:
        return None
    if declared.kind != PRECONVERSION_SUPPLY_KIND:
        return (declared,)
    return _preconversion_supplies(
        state, seat=int(seat), kind=kind, payload=payload, dying=dying
    )


def _preconversion_supplies(
    state: Any,
    *,
    seat: int,
    kind: QueryKind,
    payload: Any,
    dying: int | None,
) -> tuple[Supply, ...]:
    """**预转化**的逐张供给（票 04 §1）：每一张手牌一条 ``Supply``。

    "能预转化成的牌名"读 :func:`sgs.skills.guohuo_preconversion_products`（它包着
    ``skills/active.py:_guohuo_response_names`` 的**同一份表**，也是提供者候选 / 声明范围的
    判据）——本层不另写一份"哪些牌名能当这一问的牌"。走 ``sgs.skills`` 的公开面而不是深层
    导入 ``sgs.skills.active``：子包纪律由 ``tests/test_scaffold.py`` 挡着（票 04 的门禁撞过）。
    牌面事实取**来源牌**（§16.3：转化牌继承来源牌的花色 / 点数 / 颜色），故匹配器不需要
    预转化的第二套比对（用户 Q1：与 `kind="transformed"` 的单源转化逐字相同）。

    **上游禁令复用既有谓词**（票面 §2 / 用户 Q1 点名）：

    - :attr:`~sgs.state.Player.hand_card_prohibited`（【义绝】/【雄乱】的座位禁令）⇒ 一张牌
      都扣置不出去 ⇒ **整档无供给**；
    - :func:`~sgs.effects.forbidden_as_slash`（【定乱】"不能当作【杀】使用 / 打出"）⇒ 这一张
      来源牌不能出现在**杀族产物**的那一条供给里（别的产物不受影响）；
    - 牌名闸门由上面那份表本身兑现（``satisfies_required`` + 求桃窗口对濒死者的【酒】放宽）；
    - 目标 / 距离 / 次数**不在这里重判**：那是**产物**的事实，提供者自己的谓词
      （``_guohuo_provider_payable`` 的 ``forced_use_targets``）在候选生成之前已经挡过——
      来源牌的花色 / 点数不改"这张【杀】够不够得着 Bn"。

    不满足上游禁令的那张牌**不报一条供给**（与"产物牌名不匹配"分开：上游禁令不是牌名理由）。
    """
    from sgs.skills import guohuo_preconversion_products

    if state.player(int(seat)).hand_card_prohibited:
        return ()
    demand = demand_for(kind, payload, dying=dying, asker=seat)
    required = None if demand is None or not demand.names else demand.names[0]
    if required is None:
        return ()
    products = guohuo_preconversion_products(
        state, required, asker=seat, dying=dying
    )
    if not products:
        return ()
    supplies: list[Supply] = []
    for card_id in state.player(int(seat)).hand:
        card = state.card(int(card_id))
        product = _preconversion_product(state, card, required, products)
        if product is None:
            continue
        supplies.append(
            Supply(
                product=product,
                suit=card.suit,
                rank=card.rank,
                color=card.color,
                kind=PRECONVERSION_SUPPLY_KIND,
                source_count=1,
            )
        )
    return tuple(supplies)


def _preconversion_product(
    state: Any, card: CardRef, required: str, products: tuple[str, ...]
) -> str | None:
    """这张来源牌能预转化成的**那一个**产物牌名；一个都不行 ⇒ ``None``（这张牌不报供给）。

    次序是刻意写死的：先要**窗口要的那个牌名**（``required``），再按表序（牌堆顺序）取第一
    个可用的。选哪个都落入同一族（牌名维度是族判据），但**必须确定**——同一局面两次算出
    同一条供给，"至少有一条合法"的结论才可复现。

    【定乱】只挡**杀族产物**：标签说的是"不能当作【杀】使用 / 打出"，而这张牌仍能被扣置来
    当【闪】/【桃】/【无懈可击】（§16.3：预转化继承的是来源牌的花色 / 点数，牌名由声明决定）。
    """
    from .actions import forbidden_as_slash

    ordered = (required, *(product for product in products if product != required))
    for product in ordered:
        if product not in products:
            # ``required`` 不在这一问的牌名表里（本牌堆没有那种牌）⇒ 跳过它。
            continue
        behavior = CARD_BEHAVIORS.get(product)
        if (
            behavior is not None
            and behavior.count_key == SLASH_FAMILY_KEY
            and forbidden_as_slash(state, card)
        ):
            continue
        return product
    return None


def _supply_of_card(card: CardRef) -> Supply:
    """一张**候选牌**的供给描述：真牌 / 单源转化 / 多源转化 / 纯虚拟。

    牌面事实**原样**取 ``CardRef``（单源转化继承产物花色 / 点数 / 颜色，多源转化三者
    恒无 —— 那是构造期的事实，本层不重算）。``kind`` 的判据是 ``virtual_sources`` /
    ``is_virtual`` 这两条既有事实：有来源 = 转化，无来源的虚拟牌 = 纯虚拟。
    """
    sources = card.virtual_sources
    if sources:
        kind, source_count = "transformed", len(sources)
    elif card.is_virtual:
        kind, source_count = "virtual", 1
    else:
        kind, source_count = "real", 1
    return Supply(
        product=card.type,
        suit=card.suit,
        rank=card.rank,
        color=card.color,
        kind=kind,
        source_count=source_count,
    )


def _declared_supply(skill_id: str) -> Supply | None:
    """提供者的**声明**供给（``SkillDef.supply``）；惰性 import（``catalog`` 反向依赖本层）。"""
    from sgs.skills import CATALOG

    definition = CATALOG.get(skill_id)
    return None if definition is None else definition.supply
