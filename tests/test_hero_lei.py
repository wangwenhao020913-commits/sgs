"""神话再临·雷包：郝昭【镇骨】（01 票；lei 包脚手架的首个载体）。

牌面（用户录入 `D:\\SGS_HEROS\\神话再临-雷包\\郝昭\\技能.txt`，逐字）：

    镇骨
    　结束阶段，你可以选择一名其他角色，你的回合结束时和该角色的下个回合结束时，
      其将手牌摸至或弃置至与你手牌数相同（摸牌时至多摸至五张）。

用户 2026-11-05 签的裁定（`.scratch/lei-batch/issues/90-zhengku-open-questions.md`）：

- Q1 = A：弃哪些牌由**目标自己**选（``SELECT_CARDS`` 的 ``actor`` = 目标）；
- Q2 = A：「至多摸至五张」= **手牌终值上限 5**（同【节命】）；
- Q3 = A：标记来源阵亡 ⇒ 标记清除（``clear_on_source_death``）、该次结算不发生；
- Q4 = A：目标的"下个回合"被跳过（翻面）⇒ 顺延到下一个真实执行的回合末；
- Q5 = A：郝昭在自己回合内阵亡 ⇒ 第 1 次结算不发生。

标记记法：一枚「镇骨」标记、``count = 2`` = 两次结算都欠着；你的回合末只减 1，
目标的回合末清空（见 `src/sgs/skills/hero_lei.py` 的模块头）。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.effects import (
    check_count,
    check_targets,
    delayed_settle_target,
    legal_actions_for,
)
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    SelectCards,
    SelectOption,
    SlotKind,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="standard_108")
ZHENGKU = "hero.haozhao.zhengku"
HERO = "haozhao"
MARK = "镇骨"


class Scripted:
    """按座位脚本应答：是否发动【镇骨】、选谁、弃牌步挑几张。"""

    def __init__(
        self,
        *,
        activate: bool = False,
        seat_choice: str | None = None,
        pick_size: int | None = None,
    ) -> None:
        self.activate = activate
        self.seat_choice = seat_choice
        self.pick_size = pick_size
        self.queries: list = []
        self.selections: list = []
        self.options: list = []

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION" and str(query.skill_id) == ZHENGKU:
            self.queries.append(query)
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "PLAY_PHASE":
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if kind == "SELECT_OPTION":
            self.options.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and (
                    str(action.choice) == self.seat_choice
                ):
                    return action
            return query.legal_actions[0]
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectCards) and len(action.picks) == self.pick_size:
                    return action
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectCards)
            )
        return query.legal_actions[0]


def _state(
    *,
    owner_hand: tuple[int, ...],
    target_hand: tuple[int, ...] = (),
    third_hand: tuple[int, ...] = (),
    owner_hp: int = 4,
    owner_hp_max: int = 4,
    target_hp: int = 4,
    target_alive: bool = True,
    seats: int = 2,
):
    used = {*owner_hand, *target_hand, *third_hand}
    specs = [
        SeatSpec(hero=HERO, hp=owner_hp, hp_max=owner_hp_max, hand=owner_hand),
        SeatSpec(hp=target_hp, hp_max=target_hp, hand=target_hand, alive=target_alive),
    ]
    if seats >= 3:
        specs.append(SeatSpec(hp=4, hp_max=4, hand=third_hand))
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    return build_state(pack=PACK, seats=specs, deck=deck, turn_seat=0, phase=Phase.PLAY)


def _engine(
    state,
    *,
    activate: bool,
    seat_choice: str | None = "seat:1",
    pick_size: int | None = None,
    target_pick_size: int | None = None,
):
    engine = Engine.from_state(state, seed=7)
    owner = Scripted(activate=activate, seat_choice=seat_choice, pick_size=pick_size)
    others = [
        Scripted(
            activate=False,
            seat_choice=seat_choice,
            pick_size=target_pick_size,
        )
        for _ in range(len(state.players) - 1)
    ]
    return engine, [owner, *others]


def _markers(engine, seat: int) -> list:
    return [
        marker
        for marker in engine.state.player(seat).markers
        if marker.name == MARK and marker.source_seat == 0
    ]


def _marker_counts(engine) -> list[tuple[int, int]]:
    """记录流里的「镇骨」标记变化：``(座位, 绝对值 count)``。"""
    return [
        (record.seat, record.count)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "marker_change"
        and record.name == MARK
    ]


def _completed_turns(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
    )


def _run_turns(engine, players, *, turns: int = 1):
    """跑到 ``turns`` 个**完整回合**（``TURN_AFTER_END``）为止，不跨进下一回合。"""
    run_queries(engine, players, lambda e: _completed_turns(e) >= turns)


def test_zhengku_marks_the_target_with_two_owed_settlements():
    """发起：结束阶段选一名其他角色 ⇒ 给他挂「镇骨」，count = 2（两次结算都欠着）。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)

    assert len(players[0].queries) == 1, "结束阶段问一次「是否发动」"
    assert _marker_counts(engine) == [(1, 2), (1, 1)], "挂 2、我的回合末减到 1"
    assert _markers(engine, 1)[0].count == 1
    assert _markers(engine, 1)[0].clear_on_source_death is True, "Q3：来源阵亡即清除"


def test_declining_zhengku_leaves_no_marker_and_no_adjustment():
    """不发动 ⇒ 没有标记、目标一张不摸。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=False
    )
    _run_turns(engine, players, turns=1)

    assert len(players[0].queries) == 1
    assert _marker_counts(engine) == []
    assert len(engine.state.player(1).hand) == 1, "目标一张不摸"


def test_first_settlement_draws_only_the_deficit_at_the_owners_turn_end():
    """第 1 次结算（你的回合末）：目标摸到与你相同的张数。"""
    # 郝昭 2 张 → 摸牌阶段 +2 = 4 张；目标 1 张 ⇒ 摸 3 张。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)

    owner_hand = len(engine.state.player(0).hand)
    assert owner_hand == 4, "构造：2 + 摸牌阶段 2"
    assert len(engine.state.player(1).hand) == owner_hand, "目标摸到与之相同"


def test_second_settlement_happens_at_the_targets_own_turn_end_and_clears_the_marker():
    """第 2 次结算（该角色的下个回合结束时）：再调整一次，标记清空。"""
    # 目标体力上限 8 ⇒ 它的弃牌阶段不掷牌，回合末手牌 2（摸牌阶段 2 张）< 郝昭 4。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(), target_hp=8), activate=True
    )
    _run_turns(engine, players, turns=2)

    assert _marker_counts(engine) == [(1, 2), (1, 1), (1, 0)], "两次结算后清空"
    assert _markers(engine, 1) == [], "标记已清除"
    assert len(engine.state.player(1).hand) == len(engine.state.player(0).hand)


def test_the_target_chooses_which_cards_to_discard():
    """弃置支路（Q1 = A）：弃哪些牌由**目标自己**选，不是郝昭代选。"""
    # 郝昭 1 张 → 摸牌阶段 +2 = 3 张；目标 4 张 ⇒ 目标自己弃 1 张。
    engine, players = _engine(
        _state(owner_hand=(0,), target_hand=(10, 11, 12, 13)),
        activate=True,
        target_pick_size=1,
    )
    _run_turns(engine, players, turns=1)

    assert len(players[1].selections) >= 1, "选牌询问发给了目标（座位 1）"
    assert players[0].selections == [], "郝昭没有被问（不代选）"
    assert len(engine.state.player(1).hand) == 3, "目标弃到与郝昭相同"


def test_draw_cap_is_a_final_hand_size_of_five():
    """上限口径（Q2 = A）：「至多摸至五张」= 手牌**终值** ≤ 5。"""
    # 郝昭 5 张、体力上限 8（弃牌阶段不掷牌）→ 摸牌 +2 = 7 张；目标 1 张 ⇒ 只摸到 5。
    engine, players = _engine(
        _state(
            owner_hand=(0, 1, 2, 3, 4),
            target_hand=(10,),
            owner_hp=8,
            owner_hp_max=8,
            target_hp=8,
        ),
        activate=True,
    )
    _run_turns(engine, players, turns=1)

    assert len(engine.state.player(0).hand) == 7, "构造：5 + 2"
    assert len(engine.state.player(1).hand) == 5, "摸牌终值封顶 5，不是摸 5 张"


def test_no_activation_question_when_no_other_character_is_alive():
    """池空 ⇒ 连「是否发动」都不问（不发没有候选的询问）。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,), target_alive=False),
        activate=True,
    )
    _run_turns(engine, players, turns=1)

    assert players[0].queries == [], "没有其他存活角色 ⇒ 不问"
    assert _marker_counts(engine) == []


def test_an_unrelated_turn_end_does_not_settle():
    """别人的回合末不结算：标记只在**你自己**与**目标**的回合末兑现。"""
    # 三个座位、目标 = 座位 2：座位 1 的回合末不该动它。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,), third_hand=(20,), seats=3),
        activate=True,
        seat_choice="seat:2",
    )
    _run_turns(engine, players, turns=2)  # 我的回合 + 座位 1 的回合

    assert _marker_counts(engine) == [(2, 2), (2, 1)], "座位 1 的回合末不结算"
    assert _markers(engine, 2), "标记还在（等座位 2 的回合末）"

    _run_turns(engine, players, turns=3)  # 座位 2 的回合
    assert _marker_counts(engine) == [(2, 2), (2, 1), (2, 0)], "目标的回合末结算并清空"


def test_the_second_settlement_is_skipped_when_the_targets_turn_is_skipped():
    """Q4 = A：目标的回合被翻面跳过 ⇒ 该轮没有回合结束，标记留着等真回合。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1]
    # 之后不再发动（否则第二个回合末会挂新账，混进来）。
    players[0].activate = False
    # 把目标翻面 ⇒ 它的下个回合整轮跳过（`TURN_BEFORE_START` 翻回正面、不发 TURN_END）。
    engine.set_flipped(None, 1, True)
    _run_turns(engine, players, turns=2)  # 跳过座位 1 的回合 → 走到座位 0 的下个回合末
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1], (
        "目标回合被跳过时不在我的下个回合末补结"
    )
    assert _markers(engine, 1), "标记留着，等目标的下一个真实回合末"

    # 目标的下一个**真实**回合末仍然兑现（翻回正面之后的那一轮）。
    _run_turns(engine, players, turns=3)
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1, 0]
    assert _markers(engine, 1) == []


# ---------------------------------------------------------------------------
# 废除区域原语（lei-batch 票 02 的框架面；用户 2026-11-05 裁定）
#
# 语义 = 「区域内牌先强制弃置，然后区域被废除」+「恢复区域」+ 三条读取面：
# 装备牌不能使用 / 置入被封印的槽位；判定区被封印的角色不是延时锦囊的合法目标、
# 闪电越过它。这些是**框架**的判据，故与英雄无关（本段只用构造局面）。
# ---------------------------------------------------------------------------


def _seal_engine(*, equip: tuple[int, ...] = (), judge: tuple[int, ...] = ()):
    """座位 0 带指定装备 / 判定牌的局面；座位 1 用来验证"目标池"一侧。"""
    used = {*equip, *judge}
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=(0,), equip=equip, judge=judge),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=7)


def _frame(engine):
    """压一个最小 SKILL 帧，让"废除"的弃牌移动有地方挂。"""
    return engine.push_frame(
        FrameKind.SKILL, {"skill_owner": 0}, timing=Timing.PHASE_FINISH
    )


def test_abolish_judge_discards_the_zones_cards_then_seals_it():
    lightning = cards_of_type(PACK, "lightning")[0]
    engine = _seal_engine(judge=(lightning,))
    engine.abolish_zone(_frame(engine), 0, "judge")

    assert engine.state.player(0).judge == [], "区域里的牌先被强制弃置"
    assert lightning in engine.state.discard, "弃置的牌进了弃牌堆"
    assert "judge" in engine.state.player(0).sealed_zones
    assert engine.restore_zone(None, 0, "judge") is True
    assert engine.state.player(0).sealed_zones == ()
    assert engine.restore_zone(None, 0, "judge") is False, "重复恢复是零动作"


def test_abolish_equip_discards_every_equipment_and_blocks_using_equipment():
    weapon = cards_of_type(PACK, "crossbow")[0]
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _seal_engine(equip=(weapon, armor))
    engine.abolish_zone(_frame(engine), 0, "equip")

    assert engine.state.player(0).equip == []
    assert {weapon, armor} <= set(engine.state.discard)
    # 手牌里再抽一张装备牌：装备区被封印 ⇒ 用不了。
    spare = next(
        card_id
        for card_id in range(60, 108)
        if engine.state.card(card_id).slot is not None
    )
    assert check_targets(engine.state, 0, engine.state.card(spare)) == ()


def test_a_sealed_weapon_slot_blocks_weapons_but_not_armor():
    weapon = cards_of_type(PACK, "crossbow")[0]
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _seal_engine()
    assert engine.state.card(weapon).slot is SlotKind.WEAPON
    engine.state.player(0).sealed_zones = ("weapon",)

    assert check_targets(engine.state, 0, engine.state.card(weapon)) == ()
    assert check_targets(engine.state, 0, engine.state.card(armor)) == (0,)


def test_a_judge_sealed_seat_is_not_a_legal_target_of_a_delayed_trick():
    indulgence = cards_of_type(PACK, "indulgence")[0]
    engine = _seal_engine()
    engine.state.player(1).sealed_zones = ("judge",)
    card = engine.state.card(indulgence)

    pool = check_targets(engine.state, 0, card)
    assert 1 not in pool, "判定区被封印的角色不是延时锦囊的合法目标"
    assert 0 not in pool, "延时锦囊不以自己为目标"


def test_lightning_skips_a_judge_sealed_seat():
    lightning = cards_of_type(PACK, "lightning")[0]
    engine = _seal_engine()
    engine.state.player(1).sealed_zones = ("judge",)
    card = engine.state.card(lightning)

    # 座位 0 的闪电判完非 ♠2–9 ⇒ 顺延：座位 1 被封印 ⇒ 越过它回到座位 0。
    assert delayed_settle_target(engine.state, card, 0) == 0


def test_put_equipment_card_does_not_fire_into_a_sealed_slot():
    """界【结姻】的"置入装备区"：目标槽位被废除 ⇒ 置入不发动。"""
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _seal_engine()
    engine.state.player(1).sealed_zones = ("armor",)
    engine.state.player(0).hand.append(armor)

    frame = _frame(engine)
    result = engine.put_equipment_card(
        frame, holder=1, card=engine.state.card(armor), actor=0
    )
    assert result is None, "置入被废除的槽位是零动作"
    assert engine.state.player(1).equip == []
    assert armor in engine.state.player(0).hand, "牌留在原处"


# ---------------------------------------------------------------------------
# 张绣【雄乱】/【从谏】（lei-batch 票 02；用户 2026-11-05 签）
# ---------------------------------------------------------------------------


def _zhangxiu_state(*, equip=(), judge=(), hand=(), others=()):
    used = {*equip, *judge, *hand, *others}
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhangxiu", hand=hand, equip=equip, judge=judge),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _play_phase(engine, players):
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and str(getattr(e.pending, "kind", "")) == "PLAY_PHASE",
    )
    return engine.pending


def test_xiongluan_seals_both_zones_and_applies_its_two_effects():
    """雄乱：废除判定区与装备区（牌先弃置）→ 其不能用牌 + 我对其无距离与次数限制。"""
    weapon = cards_of_type(PACK, "crossbow")[0]
    lightning = cards_of_type(PACK, "lightning")[0]
    engine = Engine.from_state(
        _zhangxiu_state(equip=(weapon,), judge=(lightning,)), seed=7
    )
    players = [Scripted(activate=False) for _ in range(3)]
    query = _play_phase(engine, players)
    manifest = tuple(query.preactivations.get("hero.zhangxiu.xiongluan", ()))
    assert manifest, "雄乱已翻预发动：清单要渲得出来（一步选人）"

    engine.submit(
        ActivateSkill(
            skill_id="hero.zhangxiu.xiongluan",
            choices=("seat:1",),
            answers_seq=query.seq,
        )
    )
    # 提交后引擎从 SKILL 帧继续推进（预发动是"确认 = 填表"，动作已经交出去了）。
    run_queries(
        engine, players, lambda e: bool(e.state.player(0).sealed_zones)
    )
    player0 = engine.state.player(0)
    assert player0.sealed_zones == ("judge", "equip"), "两个区域都封印"
    assert player0.judge == [] and player0.equip == [], "区域里的牌先被强制弃置"
    assert engine.state.player(1).hand_card_prohibited is True, "其不能使用或打出手牌"
    assert player0.xiongluan_target == 1, "本回合对其使用牌无距离与次数限制"
    # 装备区封印 ⇒ 装备牌不能再用（手牌里的装备也进不了候选）。
    spare = next(
        card_id
        for card_id in range(60, 108)
        if engine.state.card(card_id).slot is SlotKind.ARMOR
    )
    assert check_targets(engine.state, 0, engine.state.card(spare)) == ()


def test_xiongluan_is_no_longer_offered_once_both_zones_are_sealed():
    """隐藏 cost：区域都封印了 ⇒ 清单渲不出、候选也没有（即使限定位被恢复）。"""
    engine = Engine.from_state(_zhangxiu_state(), seed=7)
    engine.state.player(0).sealed_zones = ("judge", "equip")
    players = [Scripted(activate=False) for _ in range(3)]
    query = _play_phase(engine, players)

    assert "hero.zhangxiu.xiongluan" not in dict(query.preactivations)
    assert not any(
        isinstance(action, ActivateSkill)
        and action.skill_id == "hero.zhangxiu.xiongluan"
        for action in query.legal_actions
    )


def test_xiongluan_multi_target_card_counts_unless_only_the_chosen_target():
    """多目标牌：**目标集合 ⊆ {雄乱目标}** 才豁免，含别人的照常计次（02b §4 读法 A）。

    【方天画戟】把这张【杀】的最大目标数从 1 放宽到 3，故"只对他"与"他 + 别人"两种
    目标集合都表达得出来：前者豁免、后者计次。候选面因此只剩"只对他"那一组。
    """
    slash = cards_of_type(PACK, "slash")[0]
    halberd = cards_of_type(PACK, "halberd")[0]
    state = _zhangxiu_state(hand=(slash,), equip=(halberd,))
    state.player(0).xiongluan_target = 1

    def combos():
        return tuple(
            sorted(
                action.targets
                for action in legal_actions_for(
                    state, 0, QueryKind.PLAY_PHASE, answers_seq=1
                )
                if isinstance(action, UseCard) and action.card.type == "slash"
            )
        )

    assert combos() == ((1,), (1, 2), (2,)), (
        "前提：方天画戟把这张【杀】放宽成多目标牌（'他 + 别人'表达得出来）"
    )

    state.player(0).card_use_counts["slash"] = 9  # 额度用尽
    assert check_count(state, 0, state.card(slash), targets=(1,)) is False
    assert check_count(state, 0, state.card(slash), targets=(1, 2)) is True, (
        "一次使用同时打别人时，'对其'那句豁免覆盖不到别人 ⇒ 照常计次"
    )
    assert check_count(state, 0, state.card(slash), targets=(2,)) is True
    assert combos() == ((1,),), (
        "含别人的目标组合（方天的 (1, 2)）不豁免，只剩'只对其'那组"
    )


def test_xiongluan_does_not_widen_self_only_cards():
    """雄乱只豁免**距离 / 攻击范围**：自用牌的"只能对自己"是目标合法性，池保持原样。

    lei-batch 票 04 的缺陷 B：``TARGET_POOL`` 能把"自用牌"的目标放宽到别人（这是**保留**
    的手法，将来"可对别人使用【无中生有】/【桃】"类技能的模板），但雄乱这条修饰器不能
    顺手放宽它——否则雄乱生效时【无中生有】/【桃】能指定雄乱目标。
    """
    ex_nihilo = cards_of_type(PACK, "ex_nihilo")[0]
    peach = cards_of_type(PACK, "peach")[0]
    duel = cards_of_type(PACK, "duel")[0]
    slash = cards_of_type(PACK, "slash")[0]
    state = _zhangxiu_state(hand=(ex_nihilo, peach, duel, slash))
    state.player(0).hp = 3  # 【桃】要"未满体力"才能指定自己
    state.player(0).xiongluan_target = 1

    assert check_targets(state, 0, state.card(ex_nihilo)) == (0,), (
        "【无中生有】的'对自己使用'是目标规则，不是距离"
    )
    assert check_targets(state, 0, state.card(peach)) == (0,), "【桃】同理"
    assert 1 in check_targets(state, 0, state.card(duel)), (
        "【决斗】是额定 1 名他人的牌：'对其使用牌'照常能指定雄乱目标（别误伤）"
    )
    assert 1 in check_targets(state, 0, state.card(slash)), "【杀】同理"


def test_xiongluan_count_waiver_is_per_target():
    """次数豁免落到**角色**（02b 读法 A）：额度用尽后【杀】只以雄乱目标为目标。

    ``check_count`` 的目标维度是"这一次使用对谁"：给定目标集合时按目标判（对其豁免、
    对别人照常计次）；不给目标 = 问"整张牌还准不准用" ⇒ 照常超额（``_play_phase`` 据此
    把目标池收窄到被豁免的角色，而不是整次放行）。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _zhangxiu_state(hand=(slash,))
    state.player(0).card_use_counts["slash"] = 9  # 额度用尽
    assert check_count(state, 0, state.card(slash)) is True

    state.player(0).xiongluan_target = 1
    assert check_count(state, 0, state.card(slash)) is True, (
        "不给目标维度 ⇒ 问的是'整张牌还准不准用'，答案仍是超额"
    )
    candidates = tuple(
        action.targets
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.type == "slash"
    )
    assert candidates == ((1,),), (
        "次数用尽时仍枚举目标，只保留被豁免角色那组（不再出现对别人的组合）"
    )
    assert check_count(state, 0, state.card(slash), targets=(1,)) is False
    assert check_count(state, 0, state.card(slash), targets=(2,)) is True


def test_xiongluan_waives_distance_and_count_limits_against_the_chosen_target():
    """效果①：距离与**次数**都按目标收窄（票 04 把次数侧从"整次豁免"改成按目标）。

    口径变更（lei-batch 票 04，02b 读法 A）：加雄乱目标之前额度用尽 ⇒ 没有【杀】候选；
    加上之后只多出"以那名角色为目标"的那一组，**不**把对别人的【杀】也放出来。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _zhangxiu_state(hand=(slash,))
    state.player(0).card_use_counts["slash"] = 9  # 额度用尽
    before = tuple(
        action.targets
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.type == "slash"
    )
    assert before == (), "额度用尽 ⇒ 没有【杀】候选"

    state.player(0).xiongluan_target = 1
    after = tuple(
        action.targets
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.type == "slash"
    )
    assert after == ((1,),), "只有对雄乱目标的【杀】回来（对别人的照常超额）"


def _congjian_engine():
    """座位 1 用【五谷丰登】⇒ 座位 0（张绣）成为多目标普通锦囊的目标。"""
    harvest = cards_of_type(PACK, "harvest")[0]
    gift = cards_of_type(PACK, "slash")[0]
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in {harvest, gift})
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhangxiu", hand=(gift,)),
            SeatSpec(hand=(harvest,)),
        ],
        deck=deck,
        turn_seat=1,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    return engine, harvest, gift


class _CongjianResponder:
    """座位 1 打出【五谷丰登】；座位 0 用复合动作发动【从谏】（``picks`` = 当时手牌第一张）。"""

    def __init__(self, *, giver: bool, engine=None) -> None:
        self.giver = giver
        self.engine = engine
        self.asked: list = []
        self.record_start: int | None = None

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if self.giver and kind == "SKILL_ACTIVATION":
            self.asked.append(query)
            if str(getattr(query, "skill_id", "")) == "hero.zhangxiu.congjian":
                self.record_start = len(self.engine.records)
                hand = self.engine.state.player(0).hand
                return ActivateSkill(
                    skill_id="hero.zhangxiu.congjian",
                    picks=(hand[0],),
                    choices=("seat:1",),
                    answers_seq=query.seq,
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, UseCard) and len(action.targets) == 2:
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        for action in query.legal_actions:
            if isinstance(action, (SelectCards, SelectOption)):
                return action
        return query.legal_actions[0]


def test_congjian_gives_a_card_and_draws_one_for_a_non_equipment_gift():
    engine, harvest, gift = _congjian_engine()
    seat0 = _CongjianResponder(giver=True, engine=engine)
    seat1 = _CongjianResponder(giver=False)
    run_queries(
        engine,
        [seat0, seat1],
        lambda e: gift in e.state.player(1).hand,
    )

    assert harvest not in engine.state.player(1).hand, "座位 1 打出了五谷丰登"
    assert seat0.asked, "从谏问过座位 0"
    assert gift in engine.state.player(1).hand, "给出的牌到了另一名目标手里"
    assert gift not in engine.state.player(0).hand
    # 摸牌支路：从发动那一刻起的记录流里，给座位 0 的 DRAW 恰好一条（非装备牌 ⇒ 摸 1）。
    assert seat0.record_start is not None
    draws = [
        record
        for record in engine.records[seat0.record_start :]
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.seat == 0
    ]
    assert len(draws) == 1, f"非装备牌 ⇒ 摸一张（实得 {len(draws)} 条摸牌记录）"


def test_discarding_a_card_is_tracked_for_the_turn_and_reset_after_it():
    """「未弃置过牌」检测器（lei-batch 票 03 的框架面；用户 2026-11-05 的口径）。

    弃置者 = ``actor``（自己被拆 / 被别人弃置不算"你弃置"）；复位点 = 回合末清理
    （即"自身回合结束后"），不需要新时机。
    """
    engine = _seal_engine()
    state = engine.state
    assert state.player(0).discarded_this_turn is False

    engine.move_in_frame(
        None,
        state.refs(state.player(0).hand[:1]),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=0,
        actor=0,
    )
    assert state.player(0).discarded_this_turn is True, "弃置者是自己 ⇒ 置真"

    engine._clear_turn_effects()
    assert state.player(0).discarded_this_turn is False, "回合结束后复位"


def test_replacing_equipment_is_losing_not_discarding():
    """**替换装备不算弃置、是失去原装备**（用户 2026-11-05 签；lei-batch 票 07 §2）。

    与上一条配对：那一条钉「弃置 ⇒ 置真 / 回合末复位」，本条钉顶替**不走弃置**——同槽
    已有牌时 ``move_used_card_to_equip_zone`` 先让旧牌以 ``LOSE`` 出区、再把新牌 ``PUT``
    入槽（`src/sgs/engine/steps.py` 的注释只是散文，这条断言才是会红的那一半）。故
    「本回合未弃置过牌」检测器（``Player.discarded_this_turn``，【罪论】选项 2 的判据）
    在顶替之后仍为假；将来有人把顶替改成 ``DISCARD``，这里的两条断言会一起变红。
    """
    old_horse = cards_of_type(PACK, "chitu")[0]
    new_horse = cards_of_type(PACK, "dawan")[0]
    used = {old_horse, new_horse}
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, equip=(old_horse,)), SeatSpec()],
        deck=tuple(card_id for card_id in range(60, 108) if card_id not in used),
        processing=(new_horse,),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    assert state.card(old_horse).slot is state.card(new_horse).slot is (
        SlotKind.HORSE_OFFENSE
    ), "两张牌必须争同一个槽位，否则走不到顶替"
    engine = Engine.from_state(state, seed=7)
    # 真路径的帧形状：使用帧的 data 里带 card / source，由步骤把牌从处理区搬进装备区。
    frame = engine.start_use(state.card(new_horse), source=0, targets=())
    assert engine.move_used_card_to_equip_zone(frame) is None

    moves = [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] in ([old_horse], [new_horse])
    ]
    assert moves == [("equip", "discard", "LOSE"), ("processing", "equip", "PUT")], (
        f"顶替 = 旧装备先失去（LOSE）、新装备再置入（PUT），实得 {moves}"
    )
    assert old_horse not in state.player(0).equip, "旧装备已出区"
    assert old_horse in state.discard
    assert new_horse in state.player(0).equip, "新装备进了同一槽位"
    assert state.player(0).discarded_this_turn is False, "顶替不算弃置 ⇒ 检测器不为真"


# ---------------------------------------------------------------------------
# 诸葛瞻（lei-batch 票 03）——判据函数先行（技能声明等两行排列弹窗就位后同票注册）
# ---------------------------------------------------------------------------


class _Frame:
    """只带 ``data`` 的最小帧（判据函数的输入面）。"""

    def __init__(self, **data) -> None:
        self.data = dict(data)


class _Host:
    def __init__(self, state) -> None:
        self.state = state


def _zhugezhan_state(*, hand=(0, 1), others=(), damaged=False, discarded=False):
    seats = [SeatSpec(hero=None, hand=hand), *[SeatSpec(hand=o) for o in others]]
    used = {*hand, *(card for o in others for card in o)}
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    state = build_state(
        pack=PACK, seats=seats, deck=deck, turn_seat=0, phase=Phase.PLAY
    )
    state.player(0).damage_dealt_this_turn = damaged
    state.player(0).discarded_this_turn = discarded
    return state


def test_zuilun_option_two_flips_with_the_discard_detector():
    """选项 2「本回合未弃置过牌」= 检测器的反面（弃置过 ⇒ 不满足）。"""
    from sgs.skills.hero_lei import zuilun_conditions

    clean = _zhugezhan_state(others=((10, 11),))
    assert zuilun_conditions(clean, 0)[1] is True, "没弃置过 ⇒ 满足"
    dirty = _zhugezhan_state(others=((10, 11),), discarded=True)
    assert zuilun_conditions(dirty, 0)[1] is False, "弃置过 ⇒ 不满足"


def test_zuilun_fewest_hand_counts_ties_as_the_fewest():
    """选项 3：自己 2、别人 2 / 3 ⇒ 满足（**含并列**）；自己 3、别人 2 ⇒ 不满足。"""
    from sgs.skills.hero_lei import zuilun_conditions

    tied = _zhugezhan_state(hand=(0, 1), others=((10, 11), (12, 13, 14)))
    assert zuilun_conditions(tied, 0)[2] is True
    behind = _zhugezhan_state(hand=(0, 1, 2), others=((10, 11), (12, 13, 14)))
    assert zuilun_conditions(behind, 0)[2] is False, "别人更少 ⇒ 不满足"
    # 阵亡角色不参与"全场"（它没有手牌，算进来会恒真）。
    state = _zhugezhan_state(hand=(0, 1), others=((10,),))
    state.player(1).alive = False
    state.player(1).hand = []
    assert zuilun_conditions(state, 0)[2] is True


def test_zuilun_x_is_the_number_of_met_options():
    from sgs.skills.hero_lei import zuilun_x

    state = _zhugezhan_state(others=((10, 11),), damaged=True)
    assert zuilun_x(state, 0) == 3, "造成过伤害 + 没弃置 + 全场最少（并列）⇒ 3"
    state.player(0).damage_dealt_this_turn = False
    assert zuilun_x(state, 0) == 2


def test_fuyin_needs_this_dispatch_to_be_me_against_a_slash_or_duel():
    """父荫：这一次成为【杀】的目标、使用者是别人、手牌数不大于使用者。"""
    from sgs.skills.hero_lei import fuyin_condition

    slash = cards_of_type(PACK, "slash")[0]
    duel = cards_of_type(PACK, "duel")[0]
    state = _zhugezhan_state(hand=(0, 1), others=((10,),))
    # 座位 1 是使用者（手里一张），座位 0 是目标（两张）⇒ 手牌数 2 > 1 ⇒ 不满足。
    assert (
        fuyin_condition(
            _Host(state),
            _Frame(
                hook_owner=0,
                hook_actor=0,
                source=1,
                card=state.card(slash),
            ),
        )
        is False
    )
    state.player(1).hand.extend((11, 12))
    assert fuyin_condition(
        _Host(state),
        _Frame(
            hook_owner=0,
            hook_actor=0,
            source=1,
            card=state.card(slash),
        ),
    ) is True, "使用者 3 张 ≥ 我的 2 张 ⇒ 满足"
    # 这一次不是我（hook_actor 不是我）⇒ 不触发。
    assert (
        fuyin_condition(
            _Host(state),
            _Frame(
                hook_owner=0,
                hook_actor=1,
                source=0,
                card=state.card(slash),
            ),
        )
        is False
    )
    # 既不是【杀】也不是【决斗】⇒ 不触发。
    trick = cards_of_type(PACK, "dismantlement")[0]
    assert (
        fuyin_condition(
            _Host(state),
            _Frame(
                hook_owner=0,
                hook_actor=0,
                source=1,
                card=state.card(trick),
            ),
        )
        is False
    )


def _zhugezhan_engine(*, seat1_hand, seat0_hand=(1,), owner_hp_max=8):
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhugezhan", hp=3, hp_max=owner_hp_max, hand=seat0_hand),
            SeatSpec(hand=seat1_hand),
        ],
        deck=tuple(range(60, 108)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=7), state


class _ZuilunResponder:
    """座位 0：发动【罪论】并按给定 order / split 提交排列；其他询问一律合法第一项。"""

    def __init__(self, *, activate: bool = True, split: int = 1) -> None:
        self.activate = activate
        self.split = split
        self.arrange = None
        self.queries = []

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION" and str(getattr(query, "skill_id", "")) == ZUILUN:
            self.queries.append(query)
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "ARRANGE":
            self.arrange = query
            return ArrangeCards(answers_seq=query.seq, order=(0, 1, 2), split=self.split)
        if kind == "PLAY_PHASE":
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return query.legal_actions[0]


ZUILUN = "hero.zhugezhan.zuilun"


def test_zuilun_gains_x_cards_and_puts_the_rest_back_on_top():
    """罪论：X=2 ⇒ 拿两张、剩一张按序放回牌堆顶（排列面 = 牌堆顶三张）。"""
    engine, state = _zhugezhan_engine(seat1_hand=(10, 11))
    state.player(0).damage_dealt_this_turn = True
    player = _ZuilunResponder(split=1)
    other = _ZuilunResponder(activate=False)
    run_queries(
        engine,
        [player, other],
        lambda e: player.arrange is not None and 63 in e.state.player(0).hand,
    )
    assert player.arrange is not None, "罪论发了一次排列询问"
    revealed = [card.card_id for card in player.arrange.revealed]
    assert revealed[:3] == [62, 63, 64], f"面 = 摸牌阶段之后牌堆顶三张（实得 {revealed}）"
    hand = engine.state.player(0).hand
    assert 63 in hand and 64 in hand, "下排两张进了手牌"
    assert 62 not in hand, "上排那张留在牌堆顶"
    assert engine.state.deck[0] == 62, "上排按序放回牌堆顶"


def test_zuilun_declining_leaves_the_deck_and_hand_untouched():
    engine, state = _zhugezhan_engine(seat1_hand=(10, 11))
    player = _ZuilunResponder(activate=False)
    other = _ZuilunResponder(activate=False)
    run_queries(
        engine,
        [player, other],
        lambda e: player.queries and len(e.state.player(0).hand) >= 3,
    )
    assert player.queries, "问过一次「是否发动」"
    assert player.arrange is None, "不发动就没有排列询问"


def test_fuyin_makes_the_first_slash_ineffective():
    """父荫：第一次成为【杀】的目标、手牌数不大于使用者 ⇒ 此牌对你无效（不掉体力）。"""
    slash = cards_of_type(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhugezhan", hp=3, hp_max=3, hand=(1,)),
            SeatSpec(hand=(slash,)),
        ],
        deck=tuple(card_id for card_id in range(60, 108) if card_id != slash),
        turn_seat=1,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)

    class _Attacker:
        def __init__(self) -> None:
            self.played = False

        def respond(self, query):
            kind = str(getattr(query.kind, "value", query.kind))
            if kind == "PLAY_PHASE":
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "slash":
                        self.played = True
                        return action
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, EndPhase)
                )
            return query.legal_actions[0]

    attacker = _Attacker()
    victim = _Attacker()
    run_queries(
        engine,
        [victim, attacker],
        lambda e: attacker.played and e.pending is None,
    )
    assert attacker.played
    assert engine.state.player(0).hp == 3, "此牌对父荫持有者无效（没有掉体力）"
