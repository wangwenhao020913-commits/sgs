"""虚拟牌 / 转化牌的构造（票 12 §6 / 票 30）。

**判据是 ``card_id < 0``**（票 12 §Q21）：虚拟牌不参与牌守恒与区域互斥——它从不进入
任何区域（``GameState.card`` 对负 id fail-closed），因此"牌守恒"这条整局不变量对它
天然成立。虚拟牌的 ``suit`` / ``rank`` / ``color`` 都可空：

- **纯虚拟牌**（【八卦阵】视为使用/打出的【闪】）：三者皆无（`A/glossary_gamecard.txt:127`）；
- **转化牌**（【丈八蛇矛】两张手牌当【杀】）：花色恒无（`A/glossary_gamecard.txt:91`）、
  点数按**用户裁定恒无**、颜色按"多张牌同色则有该颜色、否则无色"派生。

id 由**来源实体牌 id** 确定性地派生（同种子逐字节可复现）：同一种虚拟牌 + 同一组来源
恒得同一个 id，不同来源不同 id。纯虚拟牌用 ``sources=()`` 与另一段 kind 空间分开。
"""

from __future__ import annotations

from sgs.data import Color
from sgs.state import CardRef, GameState

__all__ = ["transformed_slash", "virtual_card_id", "virtual_dodge"]


def virtual_card_id(*, kind: int, sources: tuple[int, ...], card_count: int) -> int:
    """由 ``(kind, 来源 id)`` 派生一个**负数** id，不同组合不重复（确定性）。

    ``kind`` 是虚拟牌种类的命名空间（0 = 转化【杀】、1 = 视为【闪】）；``sources`` 是
    来源实体牌 id（纯虚拟牌为空）。用 ``card_count + 1`` 作进制把来源 id 拼成一个整数，
    故来源集合到 id 是单射；再按 ``kind * (card_count+1)**2`` 平移（stride 取"最多两张
    来源"的全量空间），三个种类不会撞——尤其纯虚拟牌（零来源）不会与来源 (0,1) 的转化牌
    撞在同一个 id 上。
    """
    base = card_count + 1
    mixed = 0
    for card_id in sources:
        mixed = mixed * base + card_id
    return -(1 + kind * (base**2) + mixed)


def _common_color(state: GameState, sources: tuple[int, ...]) -> Color | None:
    """多张转化："这些牌的颜色均相同 ⇒ 有该颜色，否则无色"（`A/glossary_gamecard.txt:91`）。

    颜色由每张来源牌的 ``CardRef.color``（花色派生的唯一真源）读，不重抄花色映射。
    """
    colors = [state.card(card_id).color for card_id in sources]
    first = colors[0]
    return first if all(color is first for color in colors) else None


def transformed_slash(state: GameState, sources: tuple[int, ...]) -> CardRef:
    """【丈八蛇矛】把两张手牌**转化**成一张【杀】（票 12 §Q21 / U-12-2/U-12-3）。

    与一手原文的两处偏离按用户裁定登记（实现期登记、不改冻结文档）：

    - **点数恒无**（U-12-3）：一手 `A/glossary_gamecard.txt:95` 写"点数为各牌点数之和"，
      用户裁定**不采用**（ADR-004 的字面在本图成立）；
    - **有距离限制**（U-12-2）：与 `:125` 的"无距离限制"相反；M2 不可观测（标准版没有
      "视为使用【杀】"的生产者，本函数是**转化**）。
    """
    ids = tuple(sorted(sources))
    return CardRef(
        card_id=virtual_card_id(kind=0, sources=ids, card_count=len(state.cards)),
        type="slash",
        name="杀",
        suit=None,
        rank=None,
        color=_common_color(state, ids),
        virtual_sources=ids,
    )


def virtual_dodge(state: GameState) -> CardRef:
    """【八卦阵】判定为红时"**视为**使用/打出【闪】"的纯虚拟牌（票 10 §5.3）。

    纯虚拟牌没有来源实体牌（``virtual_sources`` 为空），牌面三者皆无。它是"这次需要"
    的产物、与座位无关，故 id 里不含持有者——同一次需要里只用它一次即可。
    """
    return CardRef(
        card_id=virtual_card_id(kind=1, sources=(), card_count=len(state.cards)),
        type="dodge",
        name="闪",
        suit=None,
        rank=None,
        color=None,
    )
