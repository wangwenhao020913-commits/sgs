"""奖励的算术（M7 票 17；票 03 §4 / 票 02 §3、§4）：累加 / 轮次 / 候选 / 价格。

三件事各自只有**一处**定义，编译（进入节点时抽候选）与结算（发奖时算账）都读它：

1. **累加**：战斗奖励 = 全队模板奖励逐项相加（铜钱 + 三类 ``pick k`` 次数）+ 房间
   ``extra_reward``（票 03 §4）。"全队" = **本场的敌方座位**——队友模板的奖励不是战利品；
2. **轮次**：``pick k`` = 一次"抽 k 张选 1"，字段的**值**是这种轮次的次数，于是
   ``pick1: 2, pick3: 1`` ⇒ 三轮：两轮各 1 张候选、一轮 3 张（小的在前，顺序稳定）。
   轮次序列就是 ``BattleSpec.rewards`` 的长度，也是 ``script.reward:<节点>:<n>`` 的 ``n``；
3. **价格**：技能 = 它的稀有度价（:data:`sgs.data.RARITY_PRICES`，商店价 = 满包折价，
   一张表两用）；遗物 = :data:`sgs.data.RELIC_PRICE`。**不**在别处再写一份价。

抽取权重同源（:data:`sgs.data.RARITY_WEIGHTS` 的 60/25/12/3）：稀有度只影响抽取与铜钱，
不影响战斗数值（票 02 §4）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from sgs.data import (
    RELIC_PRICE,
    RARITY_PRICES,
    RARITY_WEIGHTS,
    Reward,
    Rarity,
    Room,
    ScriptLibrary,
    SkillValueTable,
    learning_pool,
    skill_value,
)

from . import lookup
from .errors import ScriptError
from .rng import reward_stream, shop_stream, uniform_sample, weighted_sample
from .state import Id, RunState, SHOP_RELIC_OFFERS, SHOP_SKILL_OFFERS, equipped_skills, held_skills

__all__ = [
    "BOSS_COINS",
    "BOSS_PICK_ROUNDS",
    "BOSS_PICK_SIZE",
    "EMPTY_POOL_COINS",
    "accumulate",
    "battle_rounds",
    "clean_pool",
    "draw_boss_relic",
    "draw_candidates",
    "enemy_reward",
    "round_sizes",
    "shop_stock",
    "skill_price",
]

BOSS_PICK_ROUNDS: Final = 2
"""Boss 房的固定"两次三选一"（票 02 §8）：规则常数，数据关不掉。"""

BOSS_PICK_SIZE: Final = 3
"""Boss 房那两次的候选张数：三选一。"""

BOSS_COINS: Final = 15
"""Boss 房固定的 15 铜钱（票 02 §5 / §8）。"""

EMPTY_POOL_COINS: Final = RARITY_PRICES[Rarity.COMMON]
"""学习池空 ⇒ 这三铜钱（票 02 §3：按普通档价折）。"""


def accumulate(*rewards: Reward) -> Reward:
    """逐项相加（铜钱与三类 ``pick k`` 次数），票 03 §4 的"全队模板奖励累加"。"""
    return Reward(
        coins=sum(reward.coins for reward in rewards),
        pick1=sum(reward.pick1 for reward in rewards),
        pick2=sum(reward.pick2 for reward in rewards),
        pick3=sum(reward.pick3 for reward in rewards),
    )


def round_sizes(reward: Reward) -> tuple[int, ...]:
    """一个 ``Reward`` 的 k 选 1 轮次序列（每项 = 这一轮最多几张候选）。

    ``pick k`` 的**值**是轮次次数（票 03 §4 的字段语义）：``pick1: 2`` = 两轮各 1 张。
    小的在前（``pick1`` → ``pick2`` → ``pick3``），于是轮次与 ``script.reward:<节点>:<n>``
    的 ``n`` 一一对应、与模板书写顺序无关。
    """
    return (1,) * reward.pick1 + (2,) * reward.pick2 + (3,) * reward.pick3


def enemy_reward(*, enemy_templates: Sequence[str], library: ScriptLibrary) -> Reward:
    """本场敌方座位的模板奖励累加（队友模板的奖励不计：那不是战利品）。"""
    return accumulate(
        *(lookup.template_of(library, template_id).reward for template_id in enemy_templates)
    )


def battle_rounds(
    *, room: Room, enemy_templates: Sequence[str], library: ScriptLibrary
) -> tuple[int, ...]:
    """一场战斗结算时的 k 选 1 轮次序列（每项 = 候选张数）。

    - **Boss 房**：固定两次三选一（规则），**不**累加模板奖励（票 02 §8「Boss 战不另给
      标准奖励」），之后才是房间的 ``extra_reward``；
    - **非 Boss**：先累加敌方模板，再追加 ``extra_reward``。
    """
    battle = room.battle
    if battle is None:
        raise ScriptError(
            rule_id="script.rewards.not_a_battle_room",
            loc=room.id,
            reason=f"房间 {room.id!r} 没有 battle 载荷：算不出一场战斗的奖励",
        )
    sizes = (
        (BOSS_PICK_SIZE,) * BOSS_PICK_ROUNDS
        if room.is_boss
        else round_sizes(enemy_reward(enemy_templates=enemy_templates, library=library))
    )
    return tuple(sizes) + round_sizes(battle.extra_reward)


def clean_pool(run: RunState, value_table: SkillValueTable) -> tuple[str, ...]:
    """学习池 = 价值表里已启用的武将技能 − 已拥有（背包 ∪ 出战；票 02 §3）。"""
    return learning_pool(
        value_table,
        backpack=held_skills(run.backpack),
        equipped=equipped_skills(run.backpack),
    )


def draw_candidates(
    *,
    sizes: Sequence[int],
    pool: Sequence[str],
    value_table: SkillValueTable,
    seed: int,
    node_key: str,
) -> tuple[tuple[Id, ...], ...]:
    """按轮次抽候选：第 ``n`` 轮走 ``script.reward:<节点>:<n>``，**整场跨轮不放回**。

    跨轮不放回是票 17 的读法：票 02 §3 的"三张互不相同"管一轮，这里连跨轮也不重复，
    于是玩家不会在第二轮又看到上一轮放弃掉的那张（"重复技能折铜钱"那条兜底因此
    只服务于事件 / 商店这类奖励）。池子不够 ⇒ 后面几轮**有几张给几张**（可以是空轮，
    空轮在结算时折 :data:`EMPTY_POOL_COINS`）。
    """
    remaining = list(pool)
    rounds: list[tuple[Id, ...]] = []
    for index, size in enumerate(sizes):
        weights = {
            skill_id: RARITY_WEIGHTS[skill_value(value_table, skill_id).rarity]
            for skill_id in remaining
        }
        drawn = weighted_sample(
            pool=remaining,
            weights=weights,
            count=min(size, len(remaining)),
            source=reward_stream(seed, node_key, index),
        )
        for skill_id in drawn:
            remaining.remove(skill_id)
        rounds.append(drawn)
    return tuple(rounds)


def draw_boss_relic(
    *,
    run: RunState,
    value_table: SkillValueTable,
    seed: int,
    node_key: str,
    index: int,
) -> Id | None:
    """Boss 那 1 件遗物：从「已启用且未拥有」里均匀抽一件；无货 ⇒ ``None``。

    它排在**奖励序列的下一个** ``script.reward:<节点>:<n>``（``index`` = 技能轮数）：
    票 03 §5 的九条流里没有 ``script.relic:*``，而它就属于这一场的奖励。
    """
    candidates = tuple(
        relic_id
        for relic_id, enabled in value_table.relics.items()
        if enabled and relic_id not in run.relics
    )
    picked = uniform_sample(
        pool=candidates, count=1, source=reward_stream(seed, node_key, index)
    )
    return picked[0] if picked else None


def shop_stock(
    *, run: RunState, value_table: SkillValueTable, seed: int, node_key: str
) -> tuple[tuple[Id, ...], tuple[Id, ...]]:
    """商店库存 = **6 技能 + 2 遗物**（票 03 §1）：一次抽完、售完不补货。

    技能走同一套稀有度权重（票 02 §4「商店库存用同一套权重抽 6 技能」）且**不卖已拥有**；
    遗物没有稀有度（票 02 §4），从「已启用且未拥有」里均匀抽。表里的货不足 ⇒ 有几张给几张。
    """
    stream = shop_stream(seed, node_key)
    pool = clean_pool(run, value_table)
    weights = {
        skill_id: RARITY_WEIGHTS[skill_value(value_table, skill_id).rarity]
        for skill_id in pool
    }
    skills = weighted_sample(
        pool=pool, weights=weights, count=SHOP_SKILL_OFFERS, source=stream
    )
    relics = uniform_sample(
        pool=tuple(
            relic_id
            for relic_id, enabled in value_table.relics.items()
            if enabled and relic_id not in run.relics
        ),
        count=SHOP_RELIC_OFFERS,
        source=stream,
    )
    return skills, relics


def skill_price(value_table: SkillValueTable, skill_id: str) -> int:
    """一条技能的铜钱价：**它的稀有度价**（商店价 = 满包折价，票 02 §4）。"""
    return RARITY_PRICES[skill_value(value_table, skill_id).rarity]
