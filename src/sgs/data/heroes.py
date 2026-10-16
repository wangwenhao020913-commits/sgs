"""开局**武将发放**：从启用池里不重复抽取、按座位序分配（M3 票 02）。

这一模块只回答一个问题——"哪一名武将坐到哪个座位"；它不碰牌堆、不碰身份、不碰体力
上限（体力上限由 ``engine.build_initial`` 读武将数据派生，主公 +1 的既有初始化段不动）。

**随机源口径**（`GLOSSARY.md`「子流」/ `spec.md` §机制面的统一约定 §4）：发放走唯一随机源
的**专用子流** ``hero``，与 ``shuffle`` / ``ai`` / ``identity`` / ``select`` 并列、互不消耗。
子流按用途名哈希派生，与调用顺序无关；**显式指定武将时一个随机数都不取**。

**分配不抽随机**：抽取本身即"无放回随机抽 N 名"，抽出的先后序就是座位序——"按座位序分配"
因此不额外消耗随机数（票 02 §发放 2）。

随机句柄由**调用方**（``engine.build_initial``，住引擎层）派生并交进来：本子包不得
``import sgs.engine``（``tests/test_scaffold.py`` 的跨子包深层导入扫描会判红——数据层是
引擎层的下游，反向依赖会把分层倒过来）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .loader import enabled_hero_ids, load_heroes
from .models import HeroTable

__all__ = ["HERO_SUBSTREAM", "HeroDealError", "Randomness", "deal_heroes"]

HERO_SUBSTREAM = "hero"
"""发放武将用的子流名（票 02 §发放 1）：与洗牌 / AI / 身份 / 隐藏槽乱序并列。"""


class Randomness(Protocol):
    """发放所需的最小随机面（与 ``sgs.effects`` 的 ``Randomness`` 同形）。

    数据层只声明它**需要**什么，不依赖实现：结构化协议让引擎层可以交进任何满足
    ``randbelow`` 的句柄，而本子包不必 import 引擎。
    """

    def randbelow(self, n: int) -> int:  # pragma: no cover - 协议声明
        ...


class HeroDealError(ValueError):
    """发放配置非法（池不够、id 未知、池内重复）：fail-closed，不静默发空位。"""


def _validate_pool(pool: Sequence[str]) -> tuple[str, ...]:
    """池的**唯一性**闸门；重复 id 会让"不重复抽取"在抽取阶段空转。"""
    ordered = tuple(pool)
    if len(set(ordered)) != len(ordered):
        raise HeroDealError(f"启用池里有重复的武将 id：{ordered!r}")
    return ordered


def _known_hero(table: HeroTable, hero_id: str) -> None:
    if table.by_id(hero_id) is None:
        raise HeroDealError(f"武将表里不存在武将 {hero_id!r}")


def _deal_from_pool(pool: tuple[str, ...], seat_count: int, *, stream: Randomness) -> tuple[str, ...]:
    """从 ``pool`` 里**无放回**抽 ``seat_count`` 名；返回序即座位序。

    无放回的拒绝采样：重复命中就跳过。结果数 = 座位数；重复命中会推进子流位置，
    这正是"无放回"的定义——**分配本身**（把抽出的序对应到座位）一个随机数都不取。
    """
    if len(pool) < seat_count:
        raise HeroDealError(
            f"启用池只有 {len(pool)} 名（{list(pool)}），不够发 {seat_count} 个座位"
        )
    picked: list[str] = []
    while len(picked) < seat_count:
        candidate = pool[stream.randbelow(len(pool))]
        if candidate not in picked:
            picked.append(candidate)
    return tuple(picked)


def deal_heroes(
    *,
    seat_count: int,
    stream: Randomness,
    heroes: Sequence[str] = (),
    hero_pool: Sequence[str] | None = None,
    pinned_hero: str | None = None,
    pinned_seat: int = 0,
    table: HeroTable | None = None,
) -> tuple[str, ...] | None:
    """决定每个座位的武将 id；``None`` = **无武将模式**（一局不发武将）。

    - ``heroes`` 非空 ⇒ **显式指定**（按座位序原样落地），**不消耗** ``hero`` 子流；
    - ``heroes`` 为空且 ``hero_pool is None`` ⇒ **无武将模式**，返回 ``None``
      （M2 的 34 条基准靠它保持有效）；
    - ``hero_pool`` **给出**（哪怕是空元组）⇒ 随机发放：从池里无放回抽 ``seat_count`` 名；
      空元组 = 用**数据文件里的启用池**（``enabled_hero_ids``，票 12 翻牌后非空）；
    - ``pinned_hero`` 非 ``None``（票 19 的**点将**）⇒ **部分预指派**：``pinned_seat`` 拿
      ``pinned_hero``，其余座位从**去掉它的池**里无放回随机抽——同一局里别人**抽不到**它。
      "去掉"因此不是过滤而是池本身变了，抽取仍只走 ``hero`` 子流、**不引入新的随机源**。

    点将是"在随机发放之上钉住一个座位"，故与其它三种语义的边界必须显式：
    ``heroes`` 非空（全显式）或 ``hero_pool is None``（无池可发其余座位）时给
    ``pinned_hero`` 一律 **fail-closed**，不猜用户想走哪条路。``pinned_seat`` 越界同理。

    ``table`` 入参只为"启用池"这条缺省能被测试注入替换；缺省读当前包的武将表。
    """
    if heroes:
        if pinned_hero is not None:
            raise HeroDealError(
                f"全显式（heroes={list(heroes)!r}）与点将（pinned_hero={pinned_hero!r}）"
                "是两种互斥语义，只能给一个"
            )
        pool = _validate_pool(tuple(heroes))
        if table is not None:
            for hero_id in pool:
                _known_hero(table, hero_id)
        if len(pool) != seat_count:
            raise HeroDealError(
                f"显式指定的武将数 {len(pool)} 与座位数 {seat_count} 不一致"
            )
        return pool

    if hero_pool is None:
        if pinned_hero is not None:
            raise HeroDealError(
                f"无武将模式（hero_pool=None）没有池来发其余座位，点将 {pinned_hero!r} 无从落地"
            )
        return None

    if pinned_hero is not None and not 0 <= pinned_seat < seat_count:
        raise HeroDealError(f"点将座位越界：{pinned_seat}（本局座位 0..{seat_count - 1}）")

    if hero_pool == ():
        resolved = load_heroes() if table is None else table
        pool = _validate_pool(enabled_hero_ids(resolved))
    else:
        pool = _validate_pool(tuple(hero_pool))
        resolved = table
    if resolved is not None:
        for hero_id in pool:
            _known_hero(resolved, hero_id)

    if pinned_hero is None:
        return _deal_from_pool(pool, seat_count, stream=stream)

    if pinned_hero not in pool:
        raise HeroDealError(f"点将的武将 {pinned_hero!r} 不在本局可发的池里（{list(pool)}）")
    rest = _deal_from_pool(
        tuple(hero_id for hero_id in pool if hero_id != pinned_hero),
        seat_count - 1,
        stream=stream,
    )
    return rest[:pinned_seat] + (pinned_hero,) + rest[pinned_seat:]
