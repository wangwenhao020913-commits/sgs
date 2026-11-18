"""结算：奖励累加 / `pick k` / **Boss 顺序** / 失败三态（M7 票 17；票 03 §4 / 票 02 §3、§8、§9）。

本文件判三件事：

1. **奖励**：全队模板奖励累加（铜钱 + 三类 ``pick k`` 次数）再 + 房间 ``extra_reward``；
   ``pick k`` 各轮的候选数、池不足有几张给几张、池空折 3 铜钱、满包照常弹（折稀有度价）
   且可放弃；
2. **Boss 顺序**：先失去本场 4 条出战技能（从 run 彻底删、池里仍在）→ 两次三选一 →
   1 件遗物（无货折 10）→ 15 铜钱 → 再追加 ``extra_reward``。顺序**用事件序列断言**，
   不用"最终状态相等"糊过去；"失去"那一段数据关不掉；
3. **失败**：剧情 / Boss 败整轮结束；意外战败关节点 + 扣半（向下取整）+ 不发奖。

结算**不再取随机**：候选与 Boss 的遗物在进入节点时就物化了（票 04 §0 纪律 2），所以另有
一条"把同一份 ``pending`` 装进另一个 seed 的 run，结算逐事件相同"的断言——它把"节点内
不再抽签"变成可执行的判据。
"""

from __future__ import annotations

import pytest

from sgs.data import RELIC_PRICE, RARITY_PRICES, Rarity, Reward, learning_pool
from sgs.script import (
    CoinsGained,
    CoinsLost,
    FlagsSet,
    LoseEquipped,
    NodeClosed,
    NodeStatus,
    PickOffered,
    RelicOffered,
    RelicsGained,
    RunEnded,
    ScriptError,
    SettleOrder,
    SkillsGained,
    accumulate,
    enter_node,
    equipped_skills,
    held_skills,
    progress_key,
    round_sizes,
    settle_node,
)

from support.script_mode import (
    LAYER_ID,
    RELIC_Y,
    SEED,
    SKILL_A,
    SKILL_B,
    SKILL_C,
    SKILL_D,
    SKILL_G,
    backpack,
    completed,
    library,
    other_hero_skill_ids,
    run_state,
    value_table,
)

ENTRY_KEY = progress_key(LAYER_ID, "entry")
"""主线入口（剧情战斗）的进度键。"""

VILLAGE_ROOM_KEY = progress_key(LAYER_ID, "unknown_village")
"""只揭晓村庄的入口节点：事件语义用它，才不必依赖"抽到哪条事件"。"""

PLAYED = completed(LAYER_ID, "entry")
"""让 col 1 的节点可进：入口已结算。"""


def _library():
    return library()


def _table():
    return value_table()


def _enter(node_id: str, *, table=None, **overrides):
    return enter_node(
        run=run_state(**overrides),
        library=_library(),
        node_id=node_id,
        value_table=_table() if table is None else table,
    )


def _spec(run):
    assert run.pending is not None
    return run.pending.spec


def _settle(run, *, table=None, **order):
    return settle_node(
        run=run,
        library=_library(),
        value_table=_table() if table is None else table,
        order=SettleOrder(**order),
    )


def _kinds(result) -> list[str]:
    return [event.kind for event in result.events]


def _first_picks(spec) -> tuple[str | None, ...]:
    """每轮取第一张候选（``None`` = 该轮没有候选）。"""
    return tuple((round_[0] if round_ else None) for round_ in spec.rewards)


# ---------------------------------------------------------------------------
# 一、奖励累加：铜钱 + 三类 pick 次数 + extra_reward
# ---------------------------------------------------------------------------


def test_a_victory_accumulates_every_enemy_reward_and_the_extra_reward() -> None:
    """两条敌人奖励（2 + 3 铜钱、``pick3: 1``）+ 房间 ``extra_reward``（4 铜钱、``pick2: 1``）。

    队友模板的 9 铜钱**不计**：累加的是敌方模板（战利品），不是全桌的模板。
    """
    entered = _enter("entry")
    spec = _spec(entered)
    assert [len(round_) for round_ in spec.rewards] == [3, 2], (
        "轮次 = 渠帅的 pick3 → 3 张 + extra 的 pick2 → 2 张"
    )

    result = _settle(entered, picks=_first_picks(spec))

    assert result.run is not None and result.outcome == "continue"
    assert result.run.coins == 2 + 3 + 4, "两条敌人 + extra；队友模板的 9 不算"
    assert _kinds(result) == ["coins", "pick", "coins", "pick"], (
        "先累加模板铜钱 → 它的 k 选 1 轮次 → extra 的铜钱 → extra 的轮次"
    )
    assert [event.amount for event in result.events if isinstance(event, CoinsGained)] == [5, 4]
    assert [event.round for event in result.events if isinstance(event, PickOffered)] == [0, 1]


def test_accumulate_adds_the_coins_and_all_three_pick_counts() -> None:
    """`pick k` 的**三项**都逐项相加，轮次顺序小的在前（票 03 §4 的字段语义）。"""
    packed = accumulate(
        Reward(coins=2, pick1=1),
        Reward(coins=3, pick2=2, pick3=1),
        Reward(coins=4, pick1=1),
    )

    assert packed == Reward(coins=9, pick1=2, pick2=2, pick3=1)
    assert round_sizes(packed) == (1, 1, 2, 2, 3), "pick1 → pick2 → pick3，各自重复其次数"
    assert accumulate() == Reward()


def test_the_chosen_skill_lands_in_the_first_empty_slot() -> None:
    """选中的技能进背包的**第一个空格**（= 出战槽优先，票 02 §2）。"""
    entered = _enter("entry", backpack=backpack(SKILL_G))
    spec = _spec(entered)
    first = spec.rewards[0][0]

    result = _settle(entered, picks=(first, None))

    assert result.run is not None
    assert result.run.backpack.slots[1] == first
    pick = next(event for event in result.events if isinstance(event, PickOffered))
    assert (pick.chosen, pick.coins) == (first, 0)


def test_a_victory_completes_the_node_sets_its_flags_and_clears_pending() -> None:
    """节点结算 = 一次落：``node_status`` / 房间标签 / ``pending`` 清空（票 04 §2）。"""
    entered = _enter("unknown_village")
    result = _settle(entered, option=0)

    assert result.run is not None
    assert result.run.node_status[VILLAGE_ROOM_KEY] is NodeStatus.COMPLETED
    assert result.run.pending is None
    assert "flag.event_done" in result.run.flags, "房间 on_clear.set_flags"
    assert any(
        isinstance(event, FlagsSet) and "flag.event_done" in event.flags
        for event in result.events
    )


def test_two_pick_rounds_offer_distinct_skills_and_never_repeat_across_rounds() -> None:
    spec = _spec(_enter("entry"))
    offered = [skill for round_ in spec.rewards for skill in round_]

    assert all(len(set(round_)) == len(round_) for round_ in spec.rewards)
    assert len(set(offered)) == len(offered)


def test_pick_k_offers_that_many_candidates_until_the_pool_runs_out() -> None:
    """池不足有几张给几张：只剩三张的小池里，第二轮（pick2）空。"""
    full = value_table()
    small = full.model_copy(
        update={
            "skills": {
                SKILL_A: full.skills[SKILL_A],
                SKILL_B: full.skills[SKILL_B],
                SKILL_C: full.skills[SKILL_C],
            }
        }
    )

    entered = enter_node(
        run=run_state(), library=_library(), node_id="entry", value_table=small
    )
    spec = _spec(entered)
    assert [len(round_) for round_ in spec.rewards] == [3, 0], (
        "池里 3 张：第一轮（pick3）给满 3 张，第二轮（extra 的 pick2）空"
    )

    result = _settle(entered, table=small, picks=(spec.rewards[0][0], None))

    empty = [
        event for event in result.events if isinstance(event, PickOffered) and event.round == 1
    ]
    assert empty and empty[0].candidates == ()
    assert empty[0].coins == RARITY_PRICES[Rarity.COMMON] == 3, "池空折 3 铜钱"
    assert result.run is not None
    assert result.run.coins == 5 + 4 + 3, "模板 5 + extra 4 + 空轮 3"


def test_an_empty_learning_pool_pays_three_coins_for_every_round() -> None:
    """学习池空 ⇒ 每一轮都折 3 铜钱（票 02 §3：按普通档价）。"""
    full = value_table()
    disabled = full.model_copy(
        update={
            "skills": {
                skill_id: value.model_copy(update={"enabled": False})
                for skill_id, value in full.skills.items()
            }
        }
    )

    entered = enter_node(
        run=run_state(), library=_library(), node_id="entry", value_table=disabled
    )
    spec = _spec(entered)
    assert spec.rewards == ((), ()), "池空 ⇒ 两轮都没有候选（不是少一轮）"

    result = _settle(entered, table=disabled)

    assert [event.coins for event in result.events if isinstance(event, PickOffered)] == [3, 3]
    assert result.run is not None
    assert result.run.coins == 5 + 4 + 3 + 3


def test_a_full_backpack_keeps_offering_and_converts_the_pick_to_coins() -> None:
    """满包照常弹：选中的那张折成它的稀有度价，背包一格不动。"""
    full = other_hero_skill_ids()
    entered = _enter("entry", backpack=backpack(*full))
    spec = _spec(entered)
    chosen = spec.rewards[0][0]

    result = _settle(entered, picks=(chosen, None))

    pick = next(event for event in result.events if isinstance(event, PickOffered))
    expected = RARITY_PRICES[_table().skills[chosen].rarity]
    assert (pick.chosen, pick.coins) == (chosen, expected)
    assert result.run is not None
    assert result.run.backpack.slots == backpack(*full).slots, "背包一格不动"
    assert result.run.coins == 5 + 4 + expected


def test_a_full_backpack_pick_may_still_be_abandoned() -> None:
    """满包也**可放弃**：放弃 = 不折铜钱、不进包。"""
    entered = _enter("entry", backpack=backpack(*other_hero_skill_ids()))

    result = _settle(entered, picks=(None, None))

    picks = [event for event in result.events if isinstance(event, PickOffered)]
    assert [(event.chosen, event.coins) for event in picks] == [(None, 0), (None, 0)]
    assert result.run is not None
    assert result.run.coins == 5 + 4, "放弃不折价"


def test_a_choice_that_was_never_offered_is_refused() -> None:
    entered = _enter("entry")

    with pytest.raises(ScriptError) as caught:
        _settle(entered, picks=(SKILL_A,))

    assert caught.value.rule_id == "script.settle.pick_not_offered"


def test_more_choices_than_rounds_is_refused() -> None:
    entered = _enter("entry")

    with pytest.raises(ScriptError) as caught:
        _settle(entered, picks=(SKILL_A, SKILL_B, SKILL_C))

    assert caught.value.rule_id == "script.settle.too_many_picks"


def test_missing_choices_are_treated_as_abandoned() -> None:
    """短于轮次数的选择按"放弃"补齐：结算不因为少传一个 null 就拒。"""
    entered = _enter("entry")
    spec = _spec(entered)

    result = _settle(entered, picks=(spec.rewards[0][0],))

    picks = [event for event in result.events if isinstance(event, PickOffered)]
    assert [(event.chosen is None, event.coins) for event in picks] == [(False, 0), (True, 0)]


# ---------------------------------------------------------------------------
# 二、Boss 顺序：先失去 → 两次三选一 → 遗物 → 15 铜钱 → extra
# ---------------------------------------------------------------------------


def _boss_run(**overrides):
    return _enter("boss", node_status=completed(LAYER_ID, "late"), **overrides)


def test_the_boss_settles_in_the_pinned_order() -> None:
    """**事件序列**钉死顺序：失去 → 三选一 ×2 → 遗物 → 15 铜钱 → extra 铜钱 → extra 轮次。"""
    carried = other_hero_skill_ids(2)
    entered = _boss_run(backpack=backpack(*carried))
    spec = _spec(entered)
    assert [len(round_) for round_ in spec.rewards] == [3, 3, 1], (
        "两次固定的三选一 + extra 的 pick1（池里 7 条全空着）"
    )

    result = _settle(entered, picks=_first_picks(spec))
    kinds = _kinds(result)

    assert kinds == [
        "lose_equipped",
        "pick",
        "pick",
        "relic",
        "coins",
        "coins",
        "pick",
    ], "Boss 的固定顺序（票 02 §8）+ extra_reward 追加在最后"
    assert isinstance(result.events[0], LoseEquipped)
    assert result.events[0].skills == carried, "先失去本场的出战技能"
    assert [event.chosen is not None for event in result.events[1:3]] == [True, True]
    relic = result.events[3]
    assert isinstance(relic, RelicOffered) and relic.relic is not None
    assert result.events[4].amount == 15 and result.events[4].reason == "boss"
    assert result.events[5].amount == 6 and result.events[5].reason == "extra"
    assert result.events[6].round == 2, "extra 的 pick1 是第 2 轮"


def test_the_boss_takes_away_the_four_equipped_skills_and_keeps_them_in_the_pool() -> None:
    """失去那一步：从 run **彻底删**（4 个出战槽清空），但池里仍在（以后还能再抽到）。"""
    entered = _boss_run(backpack=backpack(SKILL_A, SKILL_B, SKILL_C, SKILL_D))
    before = learning_pool(value_table(), backpack=held_skills(entered.backpack))

    result = _settle(entered, picks=())
    assert result.run is not None
    lost = result.events[0]
    assert isinstance(lost, LoseEquipped)
    assert lost.skills == (SKILL_A, SKILL_B, SKILL_C, SKILL_D)
    assert equipped_skills(result.run.backpack) == (), "出战槽清空"

    after = learning_pool(value_table(), backpack=held_skills(result.run.backpack))
    assert set(lost.skills) <= set(after), "失去的技能回到学习池（池里仍在）"
    assert set(lost.skills) & set(before) == set(), "前置：失去之前它们不在池里"


def test_the_lose_equipped_step_cannot_be_switched_off_by_data() -> None:
    """数据一分钱不给、身上也没有出战技能 ⇒ 第一步仍是**失去**（规则段，数据关不掉）。

    走 ``plainboss`` 剧本：它唯一的房间不带 ``extra_reward``、也不带模板奖励。
    """
    entered = enter_node(
        run=run_state(script_id="plainboss"),
        library=_library(),
        node_id="entry",
        value_table=_table(),
    )
    played = settle_node(
        run=entered, library=_library(), value_table=_table(), order=SettleOrder()
    )
    assert played.run is not None
    boss = enter_node(
        run=played.run, library=_library(), node_id="boss", value_table=_table()
    )

    settled = settle_node(
        run=boss, library=_library(), value_table=_table(), order=SettleOrder()
    )

    assert _kinds(settled)[:5] == ["lose_equipped", "pick", "pick", "relic", "coins"]
    assert isinstance(settled.events[0], LoseEquipped)
    assert settled.events[0].skills == (), "没有出战技能也要有这一步（数据关不掉）"
    assert settled.events[4].amount == 15
    assert settled.outcome == "cleared", "Boss 是最后一节的出口 ⇒ 通关"
    assert _kinds(settled)[-1] == "run_ended"
    assert settled.events[-1].reason == "cleared"


def test_a_boss_room_with_no_relic_left_folds_it_into_ten_coins() -> None:
    """遗物无货 ⇒ 折 10 铜钱（票 02 §8）；它仍是遗物那一步，不变成惩罚。"""
    table = value_table().model_copy(update={"relics": {}})
    entered = _boss_run(table=table, backpack=backpack(*other_hero_skill_ids(1)))
    spec = _spec(entered)
    assert spec.boss_relic is None

    result = _settle(entered, table=table, picks=_first_picks(spec))

    relic = next(event for event in result.events if isinstance(event, RelicOffered))
    assert (relic.relic, relic.coins) == (None, RELIC_PRICE)
    assert result.run is not None
    assert result.run.relics == (), "没有遗物进栏"
    assert result.run.coins == 15 + RELIC_PRICE + 6, "Boss 15 + 折 10 + extra 6"


def test_the_boss_hands_over_its_relic_before_the_fifteen_coins() -> None:
    entered = _boss_run(backpack=backpack(SKILL_A))
    spec = _spec(entered)
    assert spec.boss_relic is not None

    result = _settle(entered, picks=_first_picks(spec))

    relic = next(event for event in result.events if isinstance(event, RelicOffered))
    assert relic.relic == spec.boss_relic
    assert result.run is not None
    assert result.run.relics == (spec.boss_relic,)
    assert _kinds(result).index("relic") < _kinds(result).index("coins")
    assert [event.amount for event in result.events if isinstance(event, CoinsGained)] == [15, 6]


def test_the_boss_battle_does_not_accumulate_template_rewards() -> None:
    """Boss 战不另给标准奖励（票 02 §8）：模板的 5 铜钱不进账，只有 15 + extra 6。"""
    entered = _boss_run(backpack=backpack(*other_hero_skill_ids(1)))

    result = _settle(entered, picks=(None, None, None))

    assert result.run is not None
    assert result.run.coins == 15 + 6


# ---------------------------------------------------------------------------
# 三、失败三态
# ---------------------------------------------------------------------------


def test_a_story_defeat_ends_the_run() -> None:
    """剧情战败 ⇒ 整轮结束（``run=None``）：不发奖、不扣钱（整轮都不要了）。"""
    entered = _enter("entry", coins=10)

    result = _settle(entered, won=False)

    assert result.outcome == "failed"
    assert result.run is None
    assert _kinds(result) == ["run_ended"]
    assert isinstance(result.events[0], RunEnded)
    assert result.events[0].reason == "story_loss"


def test_a_boss_defeat_ends_the_run() -> None:
    entered = _boss_run(backpack=backpack(SKILL_A))

    result = _settle(entered, won=False)

    assert result.outcome == "failed" and result.run is None
    assert result.events[0].reason == "boss_loss"


def test_an_unexpected_battle_defeat_closes_the_node_and_halves_the_coins() -> None:
    """意外战败：节点 ``closed``、整轮继续、失去一半铜钱（向下取整）、不发奖。"""
    entered = _enter("draw", node_status=PLAYED, coins=7)

    result = _settle(entered, won=False)

    assert result.outcome == "continue"
    assert result.run is not None
    assert result.run.node_status[progress_key(LAYER_ID, "draw")] is NodeStatus.CLOSED
    assert result.run.coins == 7 - 7 // 2 == 4, "失去一半（向下取整）⇒ 7 - 3 = 4"
    assert result.run.pending is None
    assert _kinds(result) == ["node_closed", "coins_lost"]
    assert isinstance(result.events[0], NodeClosed)
    assert isinstance(result.events[1], CoinsLost) and result.events[1].amount == 7 // 2


def test_an_unexpected_battle_defeat_gives_no_rewards() -> None:
    """战败不发奖：没有 pick / 没有铜钱收入 / 背包一格不动 / 0 铜钱不发扣半事件。"""
    entered = _enter("draw", node_status=PLAYED, coins=0)
    before = entered.backpack

    result = _settle(entered, won=False)

    assert result.run is not None
    assert result.run.backpack == before
    assert not any(
        isinstance(event, (PickOffered, CoinsGained, SkillsGained)) for event in result.events
    )
    assert [event.kind for event in result.events] == ["node_closed"]


def test_picks_on_a_defeat_are_refused() -> None:
    entered = _enter("entry")

    with pytest.raises(ScriptError) as caught:
        _settle(entered, won=False, picks=(SKILL_A,))

    assert caught.value.rule_id == "script.settle.picks_on_defeat"


# ---------------------------------------------------------------------------
# 四、事件 / 商店 / 问号房的结算
# ---------------------------------------------------------------------------


def _village():
    return _enter("unknown_village")


def test_an_event_option_applies_its_effects() -> None:
    result = _settle(_village(), option=0)

    assert result.run is not None
    assert result.run.coins == 2, "ev.village 的第一个选项收下 2 铜钱"
    assert _kinds(result)[0] == "coins"


def test_a_locked_event_option_is_refused() -> None:
    """选项的 ``requires`` 与边共用同一套标签条件（票 03 §2）：不满足 ⇒ 拒。"""
    with pytest.raises(ScriptError) as caught:
        _settle(_village(), option=1)

    assert caught.value.rule_id == "script.settle.event_option_locked"


def test_an_event_option_may_grant_a_relic_skill_and_flags() -> None:
    """解锁条件成立时，第二个选项同时给标签 / 技能 / 遗物（事件是三者唯一的组合点）。"""
    entered = _enter(
        "unknown_village", flags=("flag.event_done",), coins=0
    )

    result = _settle(entered, option=1)

    assert result.run is not None
    assert "flag.helped" in result.run.flags
    assert SKILL_B in held_skills(result.run.backpack)
    assert result.run.relics == (RELIC_Y,)
    assert any(isinstance(event, RelicsGained) for event in result.events)
    assert any(isinstance(event, SkillsGained) for event in result.events)


def test_an_event_option_that_costs_more_than_the_purse_is_refused() -> None:
    """事件花费超过身上铜钱 ⇒ 可读错误（``RunState.coins`` 不允许为负）。"""
    with pytest.raises(ScriptError) as caught:
        _settle(_village(), option=2)

    assert caught.value.rule_id == "script.settle.not_enough_coins"


def test_an_event_option_out_of_range_is_refused() -> None:
    with pytest.raises(ScriptError) as caught:
        _settle(_village(), option=9)

    assert caught.value.rule_id == "script.settle.event_option_out_of_range"


def test_an_event_node_must_be_settled_with_a_choice() -> None:
    with pytest.raises(ScriptError) as caught:
        _settle(_village())

    assert caught.value.rule_id == "script.settle.event_option_required"


def test_a_shop_purchase_costs_its_price() -> None:
    """买技能付稀有度价、买遗物付 10；买到的进包 / 进栏（票 02 §4、§6）。"""
    entered = _enter("shop", node_status=PLAYED, coins=40)
    spec = _spec(entered)
    skill = spec.stock.skills[0]
    relic = spec.stock.relics[0]
    price = RARITY_PRICES[value_table().skills[skill].rarity]

    result = _settle(entered, purchases=(skill, relic))

    assert result.run is not None
    assert result.run.coins == 40 - price - RELIC_PRICE
    assert skill in held_skills(result.run.backpack)
    assert result.run.relics == (relic,)
    assert any(isinstance(event, SkillsGained) for event in result.events)
    assert any(isinstance(event, RelicsGained) for event in result.events)
    assert [event.amount for event in result.events if isinstance(event, CoinsLost)] == [
        price,
        RELIC_PRICE,
    ]


def test_a_shop_refuses_what_it_does_not_stock_or_the_purse_cannot_afford() -> None:
    entered = _enter("shop", node_status=PLAYED, coins=0)
    spec = _spec(entered)

    with pytest.raises(ScriptError) as caught:
        _settle(entered, purchases=("hero.nobody.no_such_skill",))
    assert caught.value.rule_id == "script.settle.not_in_stock"

    with pytest.raises(ScriptError) as caught:
        _settle(entered, purchases=(spec.stock.skills[0],))
    assert caught.value.rule_id == "script.settle.not_enough_coins"


def test_a_shop_refuses_a_skill_already_owned_or_a_full_backpack() -> None:
    """两条护栏（票 02 §5）：满包置灰、不卖已拥有——挡的是"到了结算才发现"的旧档。"""
    entered = _enter("shop", node_status=PLAYED, coins=99)
    stock = _spec(entered).stock
    stale = entered.model_copy(
        update={"backpack": backpack(stock.skills[0])}
    )

    with pytest.raises(ScriptError) as caught:
        _settle(stale, purchases=(stock.skills[0],))
    assert caught.value.rule_id == "script.settle.already_owned"

    full = entered.model_copy(
        update={"backpack": backpack(*other_hero_skill_ids())}
    )
    with pytest.raises(ScriptError) as caught:
        _settle(full, purchases=(stock.skills[0],))
    assert caught.value.rule_id == "script.settle.backpack_full"


def test_a_question_room_settles_by_its_revealed_room() -> None:
    """问号房按**揭晓出来的那个房间**结算（这里揭晓出事件房：要选一个选项）。"""
    entered = _enter("unknown", coins=10)

    result = _settle(entered, option=0)

    assert result.run is not None
    assert result.run.node_status[progress_key(LAYER_ID, "unknown")] is NodeStatus.COMPLETED
    assert "flag.event_done" in result.run.flags, "内层事件房的 on_clear 标签照样落"


def test_the_revealed_battle_room_is_settled_as_a_battle() -> None:
    entered = _enter("unknown_battle")
    spec = _spec(entered)
    assert spec.revealed.spec.kind == "battle"

    result = _settle(entered, won=False)

    assert result.outcome == "continue", "内层是意外战斗：败 ⇒ 关节点、整轮继续"
    assert result.run is not None
    assert (
        result.run.node_status[progress_key(LAYER_ID, "unknown_battle")] is NodeStatus.CLOSED
    )


# ---------------------------------------------------------------------------
# 五、结算不再取随机 / 指令形状 / 事务
# ---------------------------------------------------------------------------


def test_settling_the_same_run_and_order_twice_gives_the_same_result() -> None:
    entered = _enter("entry")
    spec = _spec(entered)
    order = SettleOrder(picks=_first_picks(spec))

    first = settle_node(run=entered, library=_library(), value_table=_table(), order=order)
    second = settle_node(run=entered, library=_library(), value_table=_table(), order=order)

    assert first == second


def test_settling_does_not_read_the_seed_any_more() -> None:
    """结算只吃 ``spec``：同一份 ``pending`` 装进另一个 seed 的 run，结果逐事件相同。

    这是"节点内不再取随机"的可执行判据——候选与 Boss 遗物在进入时已物化（票 04 §0）。
    """
    entered = _enter("entry")
    spec = _spec(entered)
    order = SettleOrder(picks=_first_picks(spec))
    other = run_state(seed=SEED + 1).model_copy(update={"pending": entered.pending})

    first = settle_node(run=entered, library=_library(), value_table=_table(), order=order)
    second = settle_node(run=other, library=_library(), value_table=_table(), order=order)

    assert first.events == second.events
    assert first.run is not None and second.run is not None
    assert first.run.coins == second.run.coins
    assert first.run.backpack == second.run.backpack


def test_settling_without_a_pending_node_is_a_readable_error() -> None:
    with pytest.raises(ScriptError) as caught:
        settle_node(run=run_state(), library=_library(), value_table=_table())

    assert caught.value.rule_id == "script.settle.no_pending"


def test_a_settle_order_carrying_another_rooms_fields_is_refused() -> None:
    """商店不接受 ``picks``、战斗不接受 ``option``：指令形状由房间类型决定。"""
    shop = _enter("shop", node_status=PLAYED, coins=99)
    with pytest.raises(ScriptError) as caught:
        _settle(shop, picks=(SKILL_A,))
    assert caught.value.rule_id == "script.settle.unexpected_choice"

    battle = _enter("entry")
    with pytest.raises(ScriptError) as caught:
        _settle(battle, option=1)
    assert caught.value.rule_id == "script.settle.unexpected_choice"


def test_settling_a_node_twice_is_a_readable_error() -> None:
    settled = _settle(_village(), option=0)
    assert settled.run is not None

    with pytest.raises(ScriptError) as caught:
        _settle(settled.run, option=0)

    assert caught.value.rule_id == "script.settle.no_pending"


def test_a_run_survives_a_path_of_two_nodes_deterministically() -> None:
    """同 run seed + 同选择序列 ⇒ 同一条 spec 序列与同一份 RunState（票 17 的验收）。"""

    def walk() -> list[object]:
        seen: list[object] = []
        run = run_state(node_status=PLAYED, coins=40)
        entered = enter_node(
            run=run, library=_library(), node_id="shop", value_table=_table()
        )
        seen.append(entered.pending.spec if entered.pending else None)
        played = settle_node(
            run=entered, library=_library(), value_table=_table(), order=SettleOrder()
        )
        assert played.run is not None
        run = played.run
        boss = enter_node(run=run, library=_library(), node_id="exit", value_table=_table())
        seen.append(boss.pending.spec if boss.pending else None)
        spec = _spec(boss)
        settled = settle_node(
            run=boss,
            library=_library(),
            value_table=_table(),
            order=SettleOrder(picks=_first_picks(spec)),
        )
        seen.append(settled.run)
        return seen

    assert walk() == walk()
