"""开局**武将发放**：从启用池里按**真名**两段抽取、按座位序分配（M3 票 02 / M6 票 12）。

这一模块只回答一个问题——"哪一名武将坐到哪个座位"；它不碰牌堆、不碰身份、不碰体力
上限（体力上限由 ``engine.build_initial`` 读武将数据派生，主公 +1 的既有初始化段不动）。

**真名配将**（M6 票 12；spec D4 / D5）：一局里同一个**真名**至多落一个座位，因此发放
分两段——先从**真名池**（将池里至少一个版本未被**禁将**的真名）无放回抽 N 个不同的
真名，再在每个真名的**未被禁**版本里抽一版。手选（``pinned_hero`` 或
``HeroDealConfig.picks``）先占真名、且**无视禁将**：玩家点名的版本即使被划掉也照发，
但别的座位不能再抽到这个真名的任何版本。真名池（扣掉手选）小于剩余座位数 ⇒
``HeroDealError``（fail-closed，不靠放宽禁将、也不靠同一真名重复来凑）。

**随机源口径**（`GLOSSARY.md`「子流」/ `spec.md` §机制面的统一约定 §4）：发放走唯一随机源
的**专用子流** ``hero``，与 ``shuffle`` / ``ai`` / ``identity`` / ``select`` 并列、互不消耗。
子流按用途名哈希派生，与调用顺序无关；**显式指定武将时一个随机数都不取**。

**分配不抽随机**：抽取本身即"无放回随机抽 N 个真名 + 逐真名抽版本"，抽出的先后序就是
座位序——"按座位序分配"因此不额外消耗随机数（票 02 §发放 2）。单版本真名的版本抽签
``randbelow(1)`` 不消耗任何随机位，故"每个真名只有一个版本"的池与旧的一步抽样逐位相同。

随机句柄由**调用方**（``engine.build_initial``，住引擎层）派生并交进来：本子包不得
``import sgs.engine``（``tests/test_scaffold.py`` 的跨子包深层导入扫描会判红——数据层是
引擎层的下游，反向依赖会把分层倒过来）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .loader import enabled_hero_ids, load_hero_tables
from .models import Hero, HeroDealConfig, HeroPick, HeroTable

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
    """发放配置非法（池不够、id 未知、池内重复、真名池不足、手选冲突）：fail-closed。"""


def _validate_pool(pool: Sequence[str]) -> tuple[str, ...]:
    """池的**唯一性**闸门；重复 id 会让"不重复抽取"在抽取阶段空转。"""
    ordered = tuple(pool)
    if len(set(ordered)) != len(ordered):
        raise HeroDealError(f"启用池里有重复的武将 id：{ordered!r}")
    return ordered


def _hero_index(tables: Sequence[HeroTable]) -> dict[str, Hero]:
    """全部武将表的 id → 武将索引（标准表在前、扩展表在后；id 唯一）。"""
    index: dict[str, Hero] = {}
    for table in tables:
        for hero in table.heroes:
            index.setdefault(hero.id, hero)
    return index


def _known(index: Mapping[str, Hero], hero_id: str) -> Hero:
    hero = index.get(hero_id)
    if hero is None:
        raise HeroDealError(f"武将表里不存在武将 {hero_id!r}")
    return hero


def _true_name_groups(
    pool: tuple[str, ...], index: Mapping[str, Hero]
) -> dict[str, tuple[str, ...]]:
    """按**真名**分组：真名 → 池里的版本 id（顺序 = 池里的先后序）。

    dict 的插入顺序就是真名在池里首次出现的顺序，故抽取与 PYTHONHASHSEED 无关。
    """
    groups: dict[str, list[str]] = {}
    for hero_id in pool:
        groups.setdefault(index[hero_id].true_name, []).append(hero_id)
    return {true_name: tuple(versions) for true_name, versions in groups.items()}


def _draw_distinct(items: tuple[str, ...], count: int, *, stream: Randomness) -> tuple[str, ...]:
    """从 ``items`` 里**无放回**抽 ``count`` 个；返回序即座位序。

    无放回的拒绝采样：重复命中就跳过。重复命中会推进子流位置，这正是"无放回"的定义
    ——**分配本身**（把抽出的序对应到座位）一个随机数都不取。
    """
    picked: list[str] = []
    while len(picked) < count:
        candidate = items[stream.randbelow(len(items))]
        if candidate not in picked:
            picked.append(candidate)
    return tuple(picked)


def _resolve_picks(
    *,
    seat_count: int,
    pool: tuple[str, ...],
    index: Mapping[str, Hero],
    config: HeroDealConfig,
) -> dict[int, str]:
    """校验手选并返回 ``座位 → 版本 id``；同一真名占两个座位是构造期不变量违反。"""
    picks: dict[int, str] = {}
    picked_heroes: dict[str, int] = {}
    true_names: dict[str, int] = {}
    for pick in config.picks:
        if not 0 <= pick.seat < seat_count:
            raise HeroDealError(
                f"手选座位越界：{pick.seat}（本局座位 0..{seat_count - 1}）"
            )
        if pick.seat in picks:
            raise HeroDealError(f"手选座位 {pick.seat} 指定了两名武将")
        hero = _known(index, pick.hero)
        if pick.hero not in pool:
            raise HeroDealError(
                f"手选的武将 {pick.hero!r} 不在本局可发的池里（{list(pool)}）"
            )
        if pick.hero in picked_heroes:
            raise HeroDealError(
                f"手选的武将 {pick.hero!r} 被指定了两个座位"
                f"（{picked_heroes[pick.hero]} / {pick.seat}）"
            )
        first_seat = true_names.get(hero.true_name)
        if first_seat is not None:
            raise HeroDealError(
                f"手选占用了同一个真名「{hero.true_name}」的两个座位："
                f"{first_seat} / {pick.seat}（同一真名至多一个座位）"
            )
        picks[pick.seat] = pick.hero
        picked_heroes[pick.hero] = pick.seat
        true_names[hero.true_name] = pick.seat
    return picks


def _merge_pinned(
    config: HeroDealConfig | None,
    *,
    pinned_hero: str | None,
    pinned_seat: int,
) -> HeroDealConfig:
    """把票 19 的**点将**（单座位预指派）并进会话配置；两种手选只能给一个。"""
    if pinned_hero is None:
        return config if config is not None else HeroDealConfig()
    if config is not None and config.picks:
        raise HeroDealError(
            f"手选（hero_config.picks={list(config.picks)!r}）与点将"
            f"（pinned_hero={pinned_hero!r}）是两种互斥语义，只能给一个"
        )
    banned = () if config is None else config.banned_heroes
    return HeroDealConfig(
        banned_heroes=banned,
        picks=(HeroPick(seat=pinned_seat, hero=pinned_hero),),
    )


def deal_heroes(
    *,
    seat_count: int,
    stream: Randomness,
    heroes: Sequence[str] = (),
    hero_pool: Sequence[str] | None = None,
    pinned_hero: str | None = None,
    pinned_seat: int = 0,
    table: HeroTable | None = None,
    hero_config: HeroDealConfig | None = None,
) -> tuple[str, ...] | None:
    """决定每个座位的武将 id；``None`` = **无武将模式**（一局不发武将）。

    - ``heroes`` 非空 ⇒ **显式指定**（按座位序原样落地），**不消耗** ``hero`` 子流；
    - ``heroes`` 为空且 ``hero_pool is None`` ⇒ **无武将模式**，返回 ``None``
      （M2 的 34 条基准靠它保持有效）；
    - ``hero_pool`` **给出**（哪怕是空元组）⇒ 随机发放：从真名池里先抽 N 个不同的真名、
      再在每个真名的未被禁版本里抽一版（票 12）；空元组 = 用**数据文件里的启用池**
      （``enabled_hero_ids``：标准版 + 界版的启用版本并集，票 12 起默认混合局）；
    - ``pinned_hero`` 非 ``None``（票 19 的**点将**）⇒ 单座位手选；``hero_config.picks``
      是同一语义的复数形式。**手选无视禁将、且先占真名**，其余座位从剩余真名里抽。
      "去掉"因此不是过滤而是真名池变了，抽取仍只走 ``hero`` 子流、**不引入新的随机源**。

    点将是"在随机发放之上钉住一个座位"，故与其它三种语义的边界必须显式：
    ``heroes`` 非空（全显式）或 ``hero_pool is None``（无池可发其余座位）时给手选
    一律 **fail-closed**，不猜用户想走哪条路。``pinned_seat`` 越界同理。

    ``hero_config`` 是**会话配置**：禁将集合（版本粒度，只作用于随机配将）与手选。
    ``table`` 入参只为"启用池 / 武将面"这条缺省能被测试注入替换（注入单表 = 只认那一张
    表）；缺省读全部武将表（内容包表 + 扩展武将包）的并集。
    """
    config = _merge_pinned(hero_config, pinned_hero=pinned_hero, pinned_seat=pinned_seat)
    tables = load_hero_tables() if table is None else (table,)
    index = _hero_index(tables)

    if heroes:
        if config.picks:
            raise HeroDealError(
                f"全显式（heroes={list(heroes)!r}）与手选（{list(config.picks)!r}）"
                "是两种互斥语义，只能给一个"
            )
        pool = _validate_pool(tuple(heroes))
        seen_true_names: dict[str, str] = {}
        for hero_id in pool:
            true_name = _known(index, hero_id).true_name
            first = seen_true_names.get(true_name)
            if first is not None:
                # 构造期不变量对**全显式**同样成立：显式指定也不能把同一真名的两版
                # 摆进同一局（GLOSSARY「真名」）。
                raise HeroDealError(
                    f"显式指定的武将里同一个真名「{true_name}」占了两个座位："
                    f"{first!r} / {hero_id!r}（同一真名至多一个座位）"
                )
            seen_true_names[true_name] = hero_id
        if len(pool) != seat_count:
            raise HeroDealError(
                f"显式指定的武将数 {len(pool)} 与座位数 {seat_count} 不一致"
            )
        return pool

    if hero_pool is None:
        if config.picks:
            raise HeroDealError(
                f"无武将模式（hero_pool=None）没有池来发其余座位，手选 {config.picks[0]!r}"
                " 无从落地"
            )
        if config.banned_heroes:
            raise HeroDealError("无武将模式（hero_pool=None）没有将池，禁将集合无从作用")
        return None

    for hero_id in config.banned_heroes:
        _known(index, hero_id)
    banned = frozenset(config.banned_heroes)

    if hero_pool == ():
        pool = _validate_pool(enabled_hero_ids(table))
    else:
        pool = _validate_pool(tuple(hero_pool))
    for hero_id in pool:
        _known(index, hero_id)

    if pinned_hero is not None and not 0 <= pinned_seat < seat_count:
        raise HeroDealError(f"点将座位越界：{pinned_seat}（本局座位 0..{seat_count - 1}）")

    picks = _resolve_picks(
        seat_count=seat_count, pool=pool, index=index, config=config
    )

    groups = _true_name_groups(pool, index)
    # 真名池 = 将池里**至少一个版本未被禁**的真名；手选先占真名（即使它的版本全被禁）。
    true_name_pool = tuple(
        true_name
        for true_name, versions in groups.items()
        if any(version not in banned for version in versions)
    )
    reserved = {index[hero_id].true_name for hero_id in picks.values()}
    remaining = tuple(name for name in true_name_pool if name not in reserved)
    open_seats = seat_count - len(picks)
    if len(remaining) < open_seats:
        raise HeroDealError(
            f"真名池不足：将池 {list(pool)} 扣除手选后只剩 {len(remaining)} 个可用真名"
            f"（{list(remaining)}），不够发 {open_seats} 个座位"
            f"（座位数 {seat_count}、手选 {len(picks)} 个、禁将 {len(banned)} 个版本）"
        )

    drawn_names = _draw_distinct(remaining, open_seats, stream=stream)
    drawn: list[str] = []
    for true_name in drawn_names:
        versions = tuple(
            version for version in groups[true_name] if version not in banned
        )
        drawn.append(versions[stream.randbelow(len(versions))])

    assignment: list[str] = []
    cursor = 0
    for seat in range(seat_count):
        if seat in picks:
            assignment.append(picks[seat])
        else:
            assignment.append(drawn[cursor])
            cursor += 1
    return tuple(assignment)
