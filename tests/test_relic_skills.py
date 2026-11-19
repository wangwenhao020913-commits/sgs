"""遗物技能 6 条（M7 票 26；票 02 §6 / §7）。

遗物**就是技能**，但住**独立遗物栏**、每场战斗开局自动生效、**不算武将牌上的技能**。
本文件按"效果一条一条"钉住六类各一条：起始手牌 +1 / 每回合摸牌 +1 / 手牌上限 +2 /
造成伤害 +1 / 桃回复 +1 / 距离 −1；每条都给**正 + 反**两半（有遗物 / 没有遗物），
并要求同时在场时能叠加。

形状（`docs/agents/skill-authoring/shapes.md` §2）：

- 起始手牌 +1 = L0「游戏开始时」锁定技钩子（``GAME_START``，零询问）；
- 其余五条 = 纯修饰器（无询问），各自挂在既有挂点上：

| 效果 | 挂点 | `ctx` 读法 |
|---|---|---|
| 每回合摸牌 +1 | `ModifierPoint.DRAW_COUNT` | `ctx.subject` = 摸牌的人 |
| 手牌上限 +2 | `ModifierPoint.HAND_LIMIT_REPLACE` | `ctx.subject` = 被查询的座位 |
| 造成伤害 +1 | `ModifierPoint.DAMAGE_DEAL_AMOUNT` | `ctx.subject` = 伤害来源 |
| 桃回复 +1 | `ModifierPoint.RECOVER_AMOUNT` | `ctx.subject` = 用【桃】的人、`ctx.card` = 那张牌 |
| 距离 −1 | `ModifierPoint.DISTANCE` | 主体侧与客体侧都减（双向） |

**没有新增询问形态 / DOM**（C3）：六条里只有一条有钩子，且它是锁定技、不问"是否发动"；
其余全是纯修饰器。
"""

from __future__ import annotations

from sgs.data import (
    Identity,
    Mode,
    PveConfig,
    PveSeat,
    PveVictory,
    PveVictoryKind,
    RuleConfig,
    SkillType,
    load_content_pack,
    load_hero_tables,
    load_skill_values,
    hero_skill_ids,
)
from sgs.effects import CarrierKind, discard_count, distance, hand_limit
from sgs.engine import DRAW_COUNT, Engine
from sgs.skills import CATALOG, SkillSourceKind, resolve_instances
from sgs.skills.relics import (
    RELIC_DAMAGE_DEALT,
    RELIC_DISTANCE,
    RELIC_EXTRA_DRAW,
    RELIC_EXTRA_START_HAND,
    RELIC_HAND_LIMIT,
    RELIC_PEACH_HEAL,
)
from sgs.state import Phase, UseCard
from sgs.timing import Timing

from support import (
    PassPlayer,
    PeachInPlayPhasePlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
    run_turns,
)

PACK = load_content_pack(deck_profile="standard_108")

RELIC_IDS = (
    "relic.damage_dealt",
    "relic.distance",
    "relic.extra_draw",
    "relic.extra_start_hand",
    "relic.hand_limit",
    "relic.peach_heal",
)
"""六条遗物的 id（``relic.<短 id>``：**不在** ``hero_skill_id`` 的派生式上）。

排序 = 出厂 ``skill-values.json`` 的遗物段顺序（按 id 升序）。
"""


def _pve_rule(*seats: PveSeat, grants: tuple[tuple[int, str], ...] = ()) -> RuleConfig:
    """一局 PvE：座位 0 = 主角（友），其余敌人；``grants`` = ``(座位, 遗物 id)``。"""
    from sgs.data import SkillGrant

    identities = (Identity.ALLY, *(Identity.ENEMY for _ in seats[1:]))
    return RuleConfig(
        player_count=len(seats),
        mode=Mode.PVE,
        pve=PveConfig(
            identities=identities,
            principal=0,
            victory=PveVictory(kind=PveVictoryKind.ROUT),
            seats=seats,
            grants=tuple(
                SkillGrant(seat=seat, kind="relic", id=relic_id)
                for seat, relic_id in grants
            ),
        ),
    )


def _seat(*, hero=None, name="", hp=4, hp_max=4) -> PveSeat:
    """一名座位（缺省 = 满体力合成武将）。"""
    return PveSeat(hero=hero, name=name, hp=hp, hp_max=hp_max)


# ---------------------------------------------------------------------------
# 一、六条都是 RELIC 载体的 catalog 条目，且不在任何武将牌上
# ---------------------------------------------------------------------------


def test_the_six_relics_are_relic_carrier_catalog_definitions():
    """六条各一个 id、载体是 ``RELIC``、锁定（牌面没有「可以」，每场开局自动生效）。

    ``CARRIER`` 这一档是票 26 的框架加法（``values.py`` 的语义门早就按**载体名**判遗物）；
    它同时把"遗物不是武将技能"变成结构事实：``dump_catalog`` / 形态表 / 描述表都按载体
    分区，故这六条不会混进武将技那两半。
    """
    table = load_skill_values()

    relics = {
        skill_id
        for skill_id, definition in CATALOG.items()
        if definition.carrier is CarrierKind.RELIC
    }
    assert relics == set(RELIC_IDS), "遗物那半 = 恰好这六条（多一条 / 少一条都红）"
    assert set(RELIC_IDS) == {
        RELIC_EXTRA_START_HAND,
        RELIC_EXTRA_DRAW,
        RELIC_HAND_LIMIT,
        RELIC_DAMAGE_DEALT,
        RELIC_PEACH_HEAL,
        RELIC_DISTANCE,
    }, "六个 id 常量与票面那六类一一对应"

    for skill_id in RELIC_IDS:
        definition = CATALOG[skill_id]
        assert definition.carrier is CarrierKind.RELIC, skill_id
        assert definition.locked is True, (
            f"{skill_id}: 遗物每场战斗开局自动生效、牌面没有「可以」⇒ 锁定（不问是否发动）"
        )
        assert definition.skill_type is SkillType.NORMAL, skill_id
        assert definition.requirement, f"{skill_id} 缺一句人话（提示条 / 描述读它）"

    assert set(table.relics) == set(RELIC_IDS), "价值表遗物段登记这六条"
    assert all(value is True for value in table.relics.values()), (
        "出厂全启用（禁用只是开关，不是稀有度语义）"
    )


def test_the_relics_are_not_on_any_hero_card():
    """遗物 id 不在任何武将牌的技能清单里（``hero.<武将>.<短 id>`` 派生式打不到它们）。"""
    on_hero_cards: set[str] = set()
    for hero_table in load_hero_tables():
        for hero in hero_table.heroes:
            on_hero_cards.update(hero_skill_ids(hero.id))

    assert not (set(RELIC_IDS) & on_hero_cards), (
        "遗物不是武将技能：id 不许出现在任何武将牌的技能清单里"
    )


# ---------------------------------------------------------------------------
# 二、起始手牌 +1（GAME_START 钩子；先手 / 后手都核）
# ---------------------------------------------------------------------------


def _hand_sizes(*, relic: bool, first_seat: int) -> list[int]:
    """跑到第一回合的 ``TURN_BEFORE_START``（所有人还没摸牌），返回两手牌数。"""
    grants = ((0, RELIC_EXTRA_START_HAND),) if relic else ()
    rule = _pve_rule(
        _seat(hero="guanyu"),
        _seat(name="黄巾力士"),
        grants=grants,
    ).model_copy(update={"first_seat": first_seat})
    engine = Engine.new_game(seed=2601 + first_seat, rule_config=rule, pack=PACK)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.TURN_BEFORE_START),
    )
    return [len(player.hand) for player in engine.state.players]


def test_the_starting_hand_relic_draws_one_more_card_for_either_first_seat():
    """起始手牌 +1：**游戏开始时**多摸一张，先手是谁不影响（L0.3 在发牌之后、第一回合之前）。"""
    for first_seat in (0, 1):
        assert _hand_sizes(relic=True, first_seat=first_seat) == [5, 4], (
            f"first_seat={first_seat}：座位 0 带遗物 ⇒ 4 + 1 张；座位 1 没有 ⇒ 4 张"
        )
        assert _hand_sizes(relic=False, first_seat=first_seat) == [4, 4], (
            f"first_seat={first_seat}：没有遗物 ⇒ 起始手牌仍是 4 张（反例）"
        )


# ---------------------------------------------------------------------------
# 三、每回合摸牌 +1（DRAW_COUNT 挂点）
# ---------------------------------------------------------------------------


def _two_seat_turn_engine(*relics: str) -> Engine:
    """两座、座位 0 先手、从准备阶段起跑；``relics`` 挂在座位 0 的遗物栏上。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    state.player(0).relics = list(relics)
    engine = Engine.from_state(state, seed=2603)
    # 停在第 1 个回合**之后**、第 2 个回合的第一次询问处：此时两个座位各摸过一次牌，
    # 且都还没进入会改手牌的阶段（座位 1 的摸牌在第 1 回合结束后就已发生）。
    run_turns(engine, [PassPlayer(), PassPlayer()], turns=1)
    return engine


def test_the_draw_relic_adds_one_card_in_its_owners_draw_phase():
    """摸牌阶段 = 额定 ``DRAW_COUNT`` + 1；**逐座位**生效（没有遗物的座位仍是额定张数）。

    停在第 2 个回合的第一次询问处：两个座位各摸过一次牌。座位 0（带遗物）摸 3 张、
    座位 1 摸 2 张；两张手牌都没到上限（4），故弃牌阶段不会再动它们。
    """
    boosted = _two_seat_turn_engine(RELIC_EXTRA_DRAW)
    plain = _two_seat_turn_engine()

    assert [len(player.hand) for player in boosted.state.players] == [DRAW_COUNT + 1, DRAW_COUNT], (
        f"额定 {DRAW_COUNT}：带遗物 3 张、没有遗物 2 张"
    )
    assert [len(player.hand) for player in plain.state.players] == [DRAW_COUNT, DRAW_COUNT], (
        "反例：两座都没有遗物 ⇒ 各摸额定张数"
    )


# ---------------------------------------------------------------------------
# 四、手牌上限 +2（HAND_LIMIT_REPLACE 挂点，持续加式）
# ---------------------------------------------------------------------------


def _limit_state(*relics: str, hand: tuple[int, ...] = ()):
    """两座、座位 0 带 ``relics``；手牌用来核"弃牌阶段要不要弃"。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=hand), SeatSpec(hp=4)],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).relics = list(relics)
    return state


def test_the_hand_limit_relic_raises_the_limit_by_two():
    """手牌上限 = max(体力, 0) + 2；**逐座位**生效（座位 1 仍是 4）。

    上限是**查询期**的修正链（弃牌阶段读它），故直接读公开查询
    :func:`~sgs.effects.hand_limit`；弃牌张数随之少 2，用
    :func:`~sgs.effects.discard_count` 钉住"多出来的两格真的免弃"。
    """
    five_cards = tuple(range(5))
    boosted = _limit_state(RELIC_HAND_LIMIT, hand=five_cards)
    plain = _limit_state(hand=five_cards)

    assert hand_limit(boosted, boosted.player(0)) == 6, "体力 4 + 遗物 2"
    assert hand_limit(boosted, boosted.player(1)) == 4, "别的座位不加（逐座位）"
    assert discard_count(boosted, boosted.player(0)) <= 0, "5 张 < 上限 6 ⇒ 不用弃"

    assert hand_limit(plain, plain.player(0)) == 4, "反例：没有遗物 ⇒ 上限 = 体力"
    assert discard_count(plain, plain.player(0)) == 1, "反例：5 张 > 上限 4 ⇒ 弃 1 张"


# ---------------------------------------------------------------------------
# 五、造成伤害 +1（DAMAGE_DEAL_AMOUNT 挂点；走伤害链，不只直伤）
# ---------------------------------------------------------------------------


class _SlashPlayer:
    """出牌阶段对指定座位使用一张【杀】；其余询问走最小合法应答。"""

    def __init__(self, target: int) -> None:
        self.target = target
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            for candidate in query.legal_actions:
                if isinstance(candidate, UseCard) and candidate.card.type == "slash":
                    if self.target in candidate.targets:
                        return candidate
            return self._fallback.respond(query)
        return self._fallback.respond(query)


def _slash_victim_hp(*relics: str) -> tuple[int, Any]:
    """座位 0 用一张【杀】打座位 1；返回受害者剩余体力与引擎。"""
    slash = cards_of_type(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(slash,)), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).relics = list(relics)
    engine = Engine.from_state(state, seed=2604)
    run_turns(engine, [_SlashPlayer(1), PassPlayer()], turns=1)
    return engine.state.player(1).hp, engine


def test_the_damage_relic_adds_one_point_through_the_damage_chain():
    """造成伤害 +1：【杀】打 2 点（**伤害链**上的来源侧加成，不是某种直伤特例）。"""
    plain, _ = _slash_victim_hp()
    boosted, engine = _slash_victim_hp(RELIC_DAMAGE_DEALT)

    assert plain == 3, "反例：没有遗物 ⇒【杀】1 点伤害"
    assert boosted == 2, "带遗物 ⇒【杀】2 点伤害（DAMAGE_DEAL_AMOUNT 链）"
    assert all(
        getattr(record, "delta_type", None) != "hp_max_change"
        for record in engine.records
    ), "遗物是查询期修饰器，不落 delta"


def test_the_damage_relic_is_source_side_and_not_counted_on_conductions():
    """来源侧的既有口径：只加**源头伤害帧**，传导帧的基数已含它，不重算。"""
    from sgs.effects import damage_amount

    slash = cards_of_type(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, slash),
    )
    state.player(0).relics = [RELIC_DAMAGE_DEALT]

    source_side = damage_amount(
        state, source=0, target=1, card=state.card(slash), base=1, chain=False
    )
    conduction = damage_amount(
        state, source=0, target=1, card=state.card(slash), base=1, chain=True
    )

    assert source_side == 2, "源头帧：基础 1 + 遗物 1"
    assert conduction == 1, "传导帧不重算来源侧加成"


# ---------------------------------------------------------------------------
# 六、桃回复 +1（RECOVER_AMOUNT 挂点）
# ---------------------------------------------------------------------------


def test_the_peach_relic_heals_one_extra_point():
    """【桃】回复 = 1 + 1 = 2：遗物持有者用的那张【桃】多回 1 点（体力 2/4 → 4/4）。

    回复量走 ``RECOVER_AMOUNT``（``ctx.subject`` = 用【桃】的人、``ctx.card`` = 那张牌），
    故"谁的【桃】"才是判据——不是"谁被回复"。反例：同一局面没有遗物 ⇒ 只回 1 点。
    """
    peach = cards_of_type(PACK, "peach")[0]

    def _heal(*relics: str) -> int:
        state = build_state(
            pack=PACK,
            seats=[SeatSpec(hp=2, hp_max=4, hand=(peach,)), SeatSpec(hp=4)],
            deck=deck_excluding(PACK, peach),
            turn_seat=0,
            phase=Phase.PREPARE,
        )
        state.player(0).relics = list(relics)
        engine = Engine.from_state(state, seed=2605)
        run_turns(engine, [PeachInPlayPhasePlayer(), PassPlayer()], turns=1)
        return engine.state.player(0).hp

    assert _heal() == 3, "反例：没有遗物 ⇒【桃】回 1 点（2 → 3）"
    assert _heal(RELIC_PEACH_HEAL) == 4, "带遗物 ⇒【桃】回 2 点（2 → 4）"


def test_the_peach_relic_only_boosts_its_owners_peach():
    """逐座位：别人用的【桃】不加成（遗物属于谁，才加成谁用的那张牌）。"""
    peach = cards_of_type(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=2, hp_max=4, hand=(peach,))],
        deck=deck_excluding(PACK, peach),
        turn_seat=1,
        phase=Phase.PREPARE,
    )
    state.player(0).relics = [RELIC_PEACH_HEAL]
    engine = Engine.from_state(state, seed=2606)
    # 座位 1 的回合是第 2 个回合：跑到第 2 个回合**结束之后**才停，否则会在它的出牌阶段
    # 第一次询问处就跳出（那时【桃】还没用）。
    run_turns(engine, [PassPlayer(), PeachInPlayPhasePlayer()], turns=2)

    assert engine.state.player(1).hp == 3, "座位 1（没有遗物）的【桃】只回 1 点"


# ---------------------------------------------------------------------------
# 七、距离 −1（DISTANCE 挂点；进攻与防守两个方向）
# ---------------------------------------------------------------------------


def _five_seat_distance_state(*relics: str):
    """五座：座位 0 与 3 的座位距离是 2（隔一个人），故 −1 之后是 1（不被下限 1 吃掉）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4) for _ in range(5)],
        deck=tuple(range(len(PACK.deck.cards))),
        rule_config=_pve_rule(*[_seat(name=f"黄巾力士{seat}") for seat in range(5)]),
    )
    state.player(0).relics = list(relics)
    return state


def test_the_distance_relic_shortens_distance_in_both_directions():
    """距离 −1：**进攻**（持有者算别人）与**防守**（别人算持有者）两个方向都减。

    座位距离 2（五座局里隔一个人）+ 遗物 −1 = 1（下限仍是 1，故取不到 0）。
    反例两半：没有遗物时两向都是 2；其他人之间（1 ↔ 3）也不受这条遗物影响。
    """
    plain = _five_seat_distance_state()
    boosted = _five_seat_distance_state(RELIC_DISTANCE)

    assert (distance(plain, 0, 3), distance(plain, 3, 0)) == (2, 2), "反例：两向都是座位距离 2"
    assert (distance(boosted, 0, 3), distance(boosted, 3, 0)) == (1, 1), (
        "进攻（0→3）与防守（3→0）都 −1"
    )
    assert distance(boosted, 1, 3) == 2, "没有遗物的座位之间不受影响（逐座位）"
    assert distance(boosted, 1, 0) == 1, "相邻座位已经是下限 1：不会减到 0"


# ---------------------------------------------------------------------------
# 八、六条同时在场：可叠加 / 开局自动生效 / 上限 6 / 不重复
# ---------------------------------------------------------------------------


def test_all_six_relics_can_be_granted_to_one_seat_at_opening_and_land_on_the_shelf():
    """**上限 6**：六件一次授给主角是合法边界；开局写入遗物栏、不落 delta。

    第 7 件由 ``PveConfig`` 构造期拒（矩阵见 ``tests/test_rule_config.py`` 的
    ``test_more_than_six_relic_grants_on_one_seat_are_rejected``）；同一 id 两次由
    ``...::test_the_same_grant_on_the_same_seat_twice_is_rejected`` 拒。本票只证明
    **内容侧恰好六件、六件都进遗物栏**。
    """
    rule = _pve_rule(
        _seat(hero="guanyu"),
        _seat(name="黄巾力士"),
        grants=tuple((0, relic_id) for relic_id in RELIC_IDS),
    )
    engine = Engine.new_game(seed=2607, rule_config=rule, pack=PACK)

    assert list(engine.state.player(0).relics) == list(RELIC_IDS), (
        "六件遗物全部写进独立遗物栏（顺序 = grant 顺序）"
    )
    assert engine.state.player(1).relics == [], "遗物只授主角；别的座位空着"
    assert all(
        getattr(record, "delta_type", None) != "skill_gain"
        for record in engine.records
    ), "开局授予**不落 delta**（票 09 的口径：只有局内 grant_skill 才发 skill_gain）"


def test_all_six_relics_stack_on_the_same_seat():
    """六条同座位时**同时生效**（效果互不吞掉）：上限 / 距离 / 加伤 / 桃回复 / 摸牌 / 起手。"""
    from sgs.effects import damage_amount, recover_amount

    peach = cards_of_type(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=2, hp_max=4), SeatSpec(hp=4), SeatSpec(hp=4), SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=deck_excluding(PACK, peach),
        turn_seat=0,
        phase=Phase.PREPARE,
        rule_config=_pve_rule(*[_seat(name=f"黄巾力士{seat}") for seat in range(5)]),
    )
    state.player(0).relics = list(RELIC_IDS)
    engine = Engine.from_state(state, seed=2608)
    run_turns(engine, [PassPlayer() for _ in range(5)], turns=1)
    after_turn = engine.state

    assert len(after_turn.player(0).hand) == 1 + DRAW_COUNT + 1, (
        "起手 +1（L0 摸的那张）+ 摸牌 +1：两条摸牌类遗物都生效（叠加不互相吞掉）"
    )
    assert hand_limit(after_turn, after_turn.player(0)) == 2 + 2, (
        "手牌上限：体力 2 + 2（摸牌后体力仍是 2）"
    )
    assert distance(after_turn, 0, 3) == 1 and distance(after_turn, 3, 0) == 1, "距离双向 −1"
    assert damage_amount(
        after_turn, source=0, target=1, card=None, base=1, chain=False
    ) == 2, "造成伤害 +1"
    assert recover_amount(
        after_turn, source=0, card=after_turn.card(peach), target=0, base=1
    ) == 2, "桃回复 +1"


# ---------------------------------------------------------------------------
# 九、来源面：遗物不是武将技能 ⇒ 不受「该角色所有非锁定技失效」影响
# ---------------------------------------------------------------------------


def _guanyu_state(*relics: str):
    """两座、座位 0 = 关羽（带非锁定技【武圣】/【义绝】）；``relics`` 进遗物栏。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="guanyu"), SeatSpec(hp=4)],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2),
    )
    state.player(0).relics = list(relics)
    return state


def test_relic_modifiers_survive_all_non_locked_skill_suppression():
    """「该角色所有非锁定技失效」挡不住遗物：来源是 ``RELIC``（不是武将牌上的技能）。

    前置：同座位的武将牌非锁定技确实被挡掉了（否则这条断言是空转）。效果面同时核一眼
    ——加伤仍然 +1，证明遗物不只是"实例还在"，而是**真的还在链上**。
    """
    from sgs.effects import damage_amount

    state = _guanyu_state(RELIC_DAMAGE_DEALT)
    engine = Engine.from_state(state, seed=2609)
    engine.suppress_skills(0, "hero.jie_guanyu.yijue")

    owned = {
        instance.skill_id for instance in resolve_instances(state) if instance.owner == 0
    }
    assert "hero.guanyu.wusheng" not in owned, "前置：武将牌上的非锁定技确实被挡"
    assert RELIC_DAMAGE_DEALT in owned, "遗物不受技能失效影响"
    assert damage_amount(
        state, source=0, target=1, card=None, base=1, chain=False
    ) == 2, "效果仍挂在修正链上（不是只有实例还在）"

    relic = next(
        instance
        for instance in resolve_instances(state)
        if instance.owner == 0 and instance.skill_id == RELIC_DAMAGE_DEALT
    )
    assert relic.source.kind is SkillSourceKind.RELIC
    assert relic.source.card_id is None, "遗物不是一张牌"


def test_the_relic_source_is_immune_even_when_the_definition_is_not_locked(monkeypatch):
    """**来源**免疫而不是"锁定技免疫"：合成一条**非锁定**的 ``RELIC`` 定义，仍挡不住。

    判据样本刻意不给 ``locked``——六条真实遗物都是锁定的，只拿它们当样本证不出
    ``_NEVER_SUPPRESSED_SOURCES`` 的那一行（锁定技本来就不在 ``ALL_NON_LOCKED`` 的覆盖面里）。
    """
    from types import MappingProxyType

    from sgs.effects import Modifier, ModifierPoint
    from sgs.skills import catalog as catalog_module
    from sgs.skills.catalog import SkillDef

    probe_id = "relic.probe_not_locked"

    def probe(state, ctx, current):
        del state, ctx
        return current + 3

    probe = SkillDef(
        skill_id=probe_id,
        locked=False,
        carrier=CarrierKind.RELIC,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, probe),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, probe_id: probe}),
    )
    state = _guanyu_state(probe_id)
    engine = Engine.from_state(state, seed=2610)
    engine.suppress_skills(0, "hero.jie_guanyu.yijue")

    owned = {
        instance.skill_id for instance in resolve_instances(state) if instance.owner == 0
    }
    assert "hero.guanyu.wusheng" not in owned
    assert probe_id in owned, "RELIC 来源在 _NEVER_SUPPRESSED_SOURCES 里逐条登记"


# ---------------------------------------------------------------------------
# 十、C3：不新增询问形态 / DOM
# ---------------------------------------------------------------------------


def test_the_relics_introduce_no_new_query_shape_and_no_dom():
    """形状封闭：六条里没有流水线 / 接缝 / 排列 / 显示组件，唯一的钩子是锁定技。

    机械面 = 声明字段全空；运行面 = 一局带六件遗物的真局里**一条技能发动询问都没有**
    （锁定技不问"是否发动"，这正是不新增询问形态的判据）。
    """
    for skill_id in RELIC_IDS:
        definition = CATALOG[skill_id]
        assert definition.flow is None, skill_id
        assert definition.seams == (), skill_id
        assert definition.deck_arrange is None, skill_id
        assert definition.virtual_slot is None, skill_id
        assert definition.appearance is None, skill_id
        assert definition.fixed_hand_count is None, skill_id

    rule = _pve_rule(
        _seat(hero="guanyu"),
        _seat(name="黄巾力士"),
        grants=tuple((0, relic_id) for relic_id in RELIC_IDS),
    )
    engine = Engine.new_game(seed=2611, rule_config=rule, pack=PACK)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.TURN_BEFORE_START),
    )

    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SKILL_ACTIVATION"
    ], "六件遗物都是锁定效果：不新增『是否发动』询问"
