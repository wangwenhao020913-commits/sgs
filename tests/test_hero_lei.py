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

import pytest

from sgs.data import Suit, load_content_pack
from sgs.effects import (
    check_count,
    check_targets,
    delayed_settle_target,
    designation_actions_for,
    distance,
    legal_actions_for,
)
from sgs.engine import Engine, IllegalActionError
from sgs.resolution import Frame
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    EQUIP_SLOTS,
    JUDGE_SLOTS,
    SEALED_SLOT_IDS,
    SelectCards,
    SelectOption,
    SlotKind,
    UseCard,
    Zone,
    ZoneRef,
    judge_zone_sealed,
    zone_sealed,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    ran_timing,
    run_queries,
)

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


def test_abolish_judge_slots_discard_the_judge_zone_then_seal_all_three_slots():
    """判定区 3 槽**一次事件**全封 ⇒ 判定区里的牌先强制弃置。"""
    lightning = cards_of_type(PACK, "lightning")[0]
    engine = _seal_engine(judge=(lightning,))
    engine.abolish_slots(_frame(engine), 0, JUDGE_SLOTS)

    assert engine.state.player(0).judge == [], "槽里的牌先被强制弃置"
    assert lightning in engine.state.discard, "弃置的牌进了弃牌堆"
    assert engine.state.player(0).sealed_zones == JUDGE_SLOTS, "三个判定槽各落一位"
    assert judge_zone_sealed(engine.state.player(0)) is True
    assert engine.restore_slots(None, 0, JUDGE_SLOTS) is True
    assert engine.state.player(0).sealed_zones == ()
    assert engine.restore_slots(None, 0, JUDGE_SLOTS) is False, "重复恢复是零动作"


def test_abolish_equip_slots_discard_every_equipment_and_block_using_equipment():
    weapon = cards_of_type(PACK, "crossbow")[0]
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _seal_engine(equip=(weapon, armor))
    engine.abolish_slots(_frame(engine), 0, EQUIP_SLOTS)

    assert engine.state.player(0).equip == []
    assert {weapon, armor} <= set(engine.state.discard)
    assert engine.state.player(0).sealed_zones == EQUIP_SLOTS
    # 手牌里再抽一张装备牌：四个槽都被封印 ⇒ 用不了。
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
    engine.abolish_slots(_frame(engine), 0, ("weapon",))

    assert engine.state.player(0).sealed_zones == ("weapon",)
    assert check_targets(engine.state, 0, engine.state.card(weapon)) == ()
    assert check_targets(engine.state, 0, engine.state.card(armor)) == (0,)


def test_one_judge_slot_alone_does_not_seal_the_judge_zone():
    """只封 1 个判定槽**不**等于判定区被废除（票 03 的派生口径）。"""
    indulgence = cards_of_type(PACK, "indulgence")[0]
    engine = _seal_engine()
    engine.abolish_slots(_frame(engine), 1, ("judge_2",))
    card = engine.state.card(indulgence)
    assert engine.state.player(1).sealed_zones == ("judge_2",)
    assert judge_zone_sealed(engine.state.player(1)) is False
    assert 1 in check_targets(engine.state, 0, card), "判定区未全封 ⇒ 仍是合法目标"


def test_a_judge_zone_sealed_seat_is_not_a_legal_target_of_a_delayed_trick():
    indulgence = cards_of_type(PACK, "indulgence")[0]
    engine = _seal_engine()
    engine.abolish_slots(_frame(engine), 1, JUDGE_SLOTS)
    card = engine.state.card(indulgence)

    pool = check_targets(engine.state, 0, card)
    assert 1 not in pool, "判定区被封印（3 槽全封）的角色不是延时锦囊的合法目标"
    assert 0 not in pool, "延时锦囊不以自己为目标"


def test_lightning_skips_a_judge_sealed_seat():
    lightning = cards_of_type(PACK, "lightning")[0]
    engine = _seal_engine()
    engine.abolish_slots(_frame(engine), 1, JUDGE_SLOTS)
    card = engine.state.card(lightning)

    # 座位 0 的闪电判完非 ♠2–9 ⇒ 顺延：座位 1 的判定区被封印 ⇒ 越过它回到座位 0。
    assert delayed_settle_target(engine.state, card, 0) == 0


def test_put_equipment_card_does_not_fire_into_a_sealed_slot():
    """界【结姻】的"置入装备区"：目标槽位被废除 ⇒ 置入不发动。"""
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _seal_engine()
    engine.abolish_slots(_frame(engine), 1, ("armor",))
    engine.state.player(0).hand.append(armor)

    frame = _frame(engine)
    result = engine.put_equipment_card(
        frame, holder=1, card=engine.state.card(armor), actor=0
    )
    assert result is None, "置入被废除的槽位是零动作"
    assert engine.state.player(1).equip == []
    assert armor in engine.state.player(0).hand, "牌留在原处"


def test_the_sealed_slot_closed_set_is_the_seven_slots_and_rejects_zone_names():
    """``sealed_zones`` 的闭集 = **7 个槽**（判定 3 + 装备 4）；整区名不是状态值。

    lukang-mobile 票 03（用户 2026-11-07）：`judge` / `equip` 两个整区取值**退役**——
    传进写入口即 ``ValueError``；读取面 ``zone_sealed`` 把它们读成"该区的槽全封"（派生）。
    宝物槽位当前**不启用**（ADR-006），故既不能被废除、也不会被读成已封印。
    """
    engine = _seal_engine()
    player = engine.state.player(0)
    assert SEALED_SLOT_IDS == (*JUDGE_SLOTS, *EQUIP_SLOTS)
    assert not any(zone_sealed(player, slot) for slot in SEALED_SLOT_IDS)
    engine.abolish_slots(_frame(engine), 0, EQUIP_SLOTS)

    for slot in EQUIP_SLOTS:
        assert zone_sealed(player, slot) is True, f"{slot}：四个装备槽各落一位"
    assert zone_sealed(player, "treasure") is False, "宝物槽不启用、不在闭集 ⇒ 不算封印"
    assert zone_sealed(player, "equip") is True, "整区兼容名 = 该区的槽全封"
    assert zone_sealed(player, "judge") is False

    # 整区名**不是**状态值：写入口拒收（提示两个名字只是只读兼容名）。
    import pytest

    with pytest.raises(ValueError):
        engine.abolish_slots(_frame(engine), 0, ("equip",))
    with pytest.raises(ValueError):
        engine.restore_slots(None, 0, ("judge",))


def _zhangxiu_state(*, equip=(), judge=(), hand=(), others=(), seat_count=3):
    used = {*equip, *judge, *hand, *others}
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    seats = [SeatSpec(hero="zhangxiu", hand=hand, equip=equip, judge=judge)]
    seats.extend(SeatSpec() for _ in range(seat_count - 1))
    return build_state(
        pack=PACK,
        seats=seats,
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


def test_xiongluan_seals_the_seven_slots_and_applies_its_two_effects():
    """雄乱：**一个事件**废除 7 个槽（牌先弃置）→ 其不能用牌 + 我对其无距离与次数限制。"""
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
    assert player0.sealed_zones == (*JUDGE_SLOTS, *EQUIP_SLOTS), (
        "一个事件落 7 个槽（判定 3 + 装备 4），不是两个整区"
    )
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


def test_xiongluan_is_no_longer_offered_once_all_seven_slots_are_sealed():
    """隐藏 cost：7 个槽全封 ⇒ 清单渲不出、候选也没有（即使限定位被恢复）。"""
    engine = Engine.from_state(_zhangxiu_state(), seed=7)
    # 两个区都是空的 ⇒ 没有牌要搬；仍走**唯一写入口** ``abolish_slots``。帧在这里只是
    # 搬牌凭证（不入栈）：``_play_phase`` 后面要驱动真正的阶段帧。
    frame = Frame(kind=FrameKind.SKILL, data={"skill_owner": 0})
    assert engine.abolish_slots(frame, 0, JUDGE_SLOTS) is None
    assert engine.abolish_slots(frame, 0, EQUIP_SLOTS) is None
    assert engine.state.player(0).sealed_zones == (*JUDGE_SLOTS, *EQUIP_SLOTS)
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
        # 断**引擎给出的次序本身**（§4：次序断言不许先 sorted()/set()/Counter() 归一化）。
        return tuple(
            action.targets
            for action in legal_actions_for(
                state, 0, QueryKind.PLAY_PHASE, answers_seq=1
            )
            if isinstance(action, UseCard) and action.card.type == "slash"
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

    lei-batch 票 04 的缺陷 B（当年靠回调里的 ``TargetRule.self_only`` 守卫挡的那一半）：
    雄乱生效时【无中生有】/【桃】不能指定雄乱目标。票 03 把它变成**结构事实**——自用牌在
    ① `TargetRule` 就返回了，走不到 ② 的距离豁免，故守卫本身随旧落点一起删掉；本用例钉住
    "删了守卫之后这条判据仍然成立"。
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

    def slash_candidates():
        return tuple(
            action.targets
            for action in legal_actions_for(
                state, 0, QueryKind.PLAY_PHASE, answers_seq=1
            )
            if isinstance(action, UseCard) and action.card.type == "slash"
        )

    assert slash_candidates() == (), "额度用尽且没有雄乱目标 ⇒ 没有【杀】候选"
    assert check_count(state, 0, state.card(slash)) is True

    state.player(0).xiongluan_target = 1
    assert check_count(state, 0, state.card(slash)) is True, (
        "不给目标维度 ⇒ 问的是'整张牌还准不准用'，答案仍是超额"
    )
    assert slash_candidates() == ((1,),), (
        "次数用尽时仍枚举目标，只保留被豁免角色那组（不再出现对别人的组合）"
    )
    assert check_count(state, 0, state.card(slash), targets=(1,)) is False
    assert check_count(state, 0, state.card(slash), targets=(2,)) is True


def test_xiongluan_waives_distance_against_the_chosen_target():
    """效果①的**距离**半边：攻击范围 1 够不着距离 2 的座位 2；雄乱点名后只把它补回池。

    次数半边由 `test_xiongluan_count_waiver_is_per_target` 钉——两条用例各断各的那一半，
    不再用"距离与次数"的名字只断次数（§4：名字承诺的那条判据要真断）。
    """
    slash = cards_of_type(PACK, "slash")[0]
    state = _zhangxiu_state(hand=(slash,), seat_count=4)
    assert distance(state, 0, 2) == 2, (
        "前提：4 座局里 0 到 2 的距离是 2（攻击范围 1 够不着）"
    )

    def candidates():
        return tuple(
            action.targets
            for action in legal_actions_for(
                state, 0, QueryKind.PLAY_PHASE, answers_seq=1
            )
            if isinstance(action, UseCard) and action.card.type == "slash"
        )

    assert candidates() == ((1,), (3,)), "距离 2 的座位 2 不在【杀】的目标池里"
    state.player(0).xiongluan_target = 2
    assert candidates() == ((1,), (2,), (3,)), (
        "雄乱点名 ⇒ 距离豁免只把座位 2 补回池（1 / 3 的次序不变）"
    )


def _xiongluan_board(*, hand=(), seats=()):
    """张绣（座位 0）出牌阶段；``seats`` = 座位 1 起的构造参数（缺省两个空白座位）。"""
    specs = [SeatSpec(hero="zhangxiu", hand=hand), *(seats or (SeatSpec(), SeatSpec()))]
    used = {card for spec in specs for card in (*spec.hand, *spec.equip, *spec.judge)}
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )


# ---------------------------------------------------------------------------
# 票 03：雄乱只豁免**距离 / 攻击范围**，且豁免求值在 ③④⑤ **之前** ⇒ 任何被
# TargetRule / 延时封印 / TARGET_FILTERS / TARGET_POOL 判非法的座位都不能被它复活。
#
# 四条用例的**观测面都两处**：``check_targets``（池本身）与出牌阶段的真实询问
# （``Engine`` 建出来的 ``PLAY_PHASE`` query）——后者的「第二处」按牌型落在不同字段上：
# 借刀是 ``card_preactivations``（裸候选已从 ``legal_actions`` 收回，清单是唯一入口），
# 其余三张是 ``legal_actions`` 里的 ``UseCard.targets``。
# ---------------------------------------------------------------------------


def test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon():
    """【借刀杀人】：④「有武器 ∧ 攻击范围内有合法【杀】目标」判非法的座位，雄乱**不**复活。

    票 03 的根因（spec §1 #2）：旧回调在 ⑤ 里无条件 ``(*current, target)`` ⇒ 池 ``()`` 变
    ``(1,)``，清单文案仍写"装备区有武器"、提交被接受；持刀者没有武器 ⇒ 交给武器那步读
    ``None`` ⇒ **整张牌空转**。新挂点在 ② 只豁免距离，④ 之后照旧 ⇒ 座位 1 进不了池，
    清单与提交两侧一起关死。
    """
    borrowed = cards_of_type(PACK, "borrowed_sword")[0]
    state = _xiongluan_board(hand=(borrowed,))
    state.player(0).xiongluan_target = 1
    assert state.player(1).equip == [], "前提：座位 1 装备区没有武器"

    assert check_targets(state, 0, state.card(borrowed)) == ()
    assert designation_actions_for(state, 0, answers_seq=1) == (), "清单与池同源"

    engine = Engine.from_state(state, seed=7)
    query = _play_phase(engine, [Scripted(activate=False) for _ in range(3)])
    assert dict(query.card_preactivations) == {}, (
        "清单渲不出来 = 此刻不可用（不 fail-open）"
    )
    assert query.designation_actions == (), "内部附带指定池同样空"

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            UseCard(
                card=state.card(borrowed),
                targets=(1,),
                designations=(2,),
                answers_seq=query.seq,
            )
        )
    assert "候选池" in caught.value.message, "不在池里的附带指定必须被拒（旧实现会接受）"


def test_xiongluan_never_resurrects_a_dismantle_target_with_no_cards():
    """【过河拆桥】：④ ``has_zone_cards`` 判非法的座位，雄乱**不**复活。"""
    dismantle = cards_of_type(PACK, "dismantlement")[0]
    filler = cards_of_type(PACK, "slash")[0]
    state = _xiongluan_board(
        hand=(dismantle,), seats=(SeatSpec(hand=(filler,)), SeatSpec())
    )
    state.player(0).xiongluan_target = 2
    empty = state.player(2)
    assert not (empty.hand or empty.equip or empty.judge), "前提：座位 2 三个区都空"

    assert check_targets(state, 0, state.card(dismantle)) == (1,)

    engine = Engine.from_state(state, seed=7)
    query = _play_phase(engine, [Scripted(activate=False) for _ in range(3)])
    assert _targets_in(query, "dismantlement") == ((1,),), (
        "出牌阶段的候选面读同一份池：座位 2 不出现"
    )


def test_xiongluan_never_resurrects_a_sealed_judge_area_target():
    """【乐不思蜀】：③「判定区已封印」判非法的座位，雄乱**不**复活。"""
    indulgence = cards_of_type(PACK, "indulgence")[0]
    state = _xiongluan_board(hand=(indulgence,))
    engine = Engine.from_state(state, seed=7)
    assert (
        engine.abolish_slots(
            Frame(kind=FrameKind.SKILL, data={"skill_owner": 0}), 1, JUDGE_SLOTS
        )
        is None
    ), "前提：座位 1 的判定区一次封完"
    state = engine.state
    state.player(0).xiongluan_target = 1
    assert judge_zone_sealed(state.player(1)), "前提：座位 1 判定区已封印"

    assert check_targets(state, 0, state.card(indulgence)) == (2,)

    query = _play_phase(engine, [Scripted(activate=False) for _ in range(3)])
    assert _targets_in(query, "indulgence") == ((2,),), (
        "出牌阶段的候选面读同一份池：判定区已封印的座位 1 不出现"
    )


def test_xiongluan_never_resurrects_a_kongcheng_seat():
    """【空城】：⑤ 里删掉的座位不被雄乱加回（修饰器管道序把雄乱排在界诸葛亮**之后**）。"""
    slash = cards_of_type(PACK, "slash")[0]
    state = _xiongluan_board(
        hand=(slash,), seats=(SeatSpec(), SeatSpec(hero="jie_zhugeliang"))
    )
    state.player(0).xiongluan_target = 2
    assert state.player(2).hand == [], "前提：界诸葛亮没有手牌 ⇒ 【空城】生效"
    assert distance(state, 0, 2) <= 1, "前提：座位 2 在攻击范围内（只由 ⑤ 删它）"

    assert check_targets(state, 0, state.card(slash)) == (1,)

    engine = Engine.from_state(state, seed=7)
    query = _play_phase(engine, [Scripted(activate=False) for _ in range(3)])
    assert _targets_in(query, "slash") == ((1,),), (
        "出牌阶段的候选面读同一份池：【空城】删掉的座位 2 不出现"
    )


def _targets_in(query, card_type: str):
    """出牌阶段询问里某牌名的全部 ``UseCard.targets``（次序 = 引擎给的次序，不排序）。"""
    return tuple(
        action.targets
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.type == card_type
    )


def _exempt_everyone_board(monkeypatch):
    """合成局面：张绣【雄乱】的修饰器被换成"对**全体其他存活**座位豁免距离"。

    六人环、座位 0 攻击范围 1、黑色【顺手牵羊】额定距离 1 ⇒ 距离分别 1 / 2 / 3 / 2 / 1。
    回调不认任何牌面（不查【杀】/ 回合），故两条豁免路径拿到的是**同一份**豁免集。
    """
    from dataclasses import replace as dc_replace
    from types import MappingProxyType

    import sgs.skills.catalog as catalog_module
    from sgs.effects import Modifier, ModifierPoint

    snatch = next(
        card_id
        for card_id in cards_of_type(PACK, "snatch")
        if PACK.deck.cards[card_id].suit is Suit.SPADE
    )
    state = _xiongluan_board(
        hand=(snatch,),
        seats=(
            SeatSpec(hand=(0,)),
            SeatSpec(hero="jiaxu_mobile", hand=(1,)),
            SeatSpec(),
            SeatSpec(hand=(2,)),
            SeatSpec(hand=(3,)),
        ),
    )

    def _exempt_everyone(state_, ctx, current):
        """合成回调：本次使用对全体其他存活座位豁免距离（只**追加**座位）。"""
        return (
            *current,
            *(
                player.seat
                for player in state_.players
                if player.alive and player.seat != ctx.subject
            ),
        )

    definition = dc_replace(
        catalog_module.CATALOG["hero.zhangxiu.xiongluan"],
        modifiers=(Modifier(ModifierPoint.TARGET_RANGE_EXEMPT, _exempt_everyone),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType(
            {**catalog_module.CATALOG, "hero.zhangxiu.xiongluan": definition}
        ),
    )
    return state, snatch


def test_the_range_exempt_hook_is_evaluated_before_the_target_filters(monkeypatch):
    """票 03 的层次：② 的新挂点只让"被距离挡掉"的座位**重新走进** ③④⑤。

    合成修饰器（替换张绣【雄乱】的修饰器表，回调不认任何牌面）= 本次使用对**全体其他存活
    座位**豁免距离。局面（六人环，座位 0 攻击范围 1，黑色【顺手牵羊】额定距离 1）：

    - 座位 1 / 5（距离 1）：本来就在池里；
    - 座位 4（距离 2、区域里有牌）：**挂点把它补进池**——旧写法只能靠 `TARGET_POOL` 并集；
    - 座位 3（距离 3、区域里**没有牌**）：被 ④ ``has_zone_cards`` 判非法 ⇒ 挂点救不回来；
    - 座位 2（距离 2、区域里有牌，但是贾诩【帷幕】）：被 ⑤ 删掉 ⇒ 挂点也救不回来。

    期望池 = (1, 4, 5)：座位 3 / 2 各自钉住"④ / ⑤ 在挂点**之后**照常跑"，座位 4 钉住
    "挂点确实在 ② 生效"——少了任何一半，下面的断言都会给出另一个元组。
    """
    state, snatch = _exempt_everyone_board(monkeypatch)
    assert (distance(state, 0, 1), distance(state, 0, 2), distance(state, 0, 3), distance(state, 0, 4), distance(state, 0, 5)) == (1, 2, 3, 2, 1), (
        "前提：六人环上到座位 2 / 3 / 4 的距离分别是 2 / 3 / 2"
    )
    assert check_targets(state, 0, state.card(snatch)) == (1, 4, 5), (
        "挂点在 ② 只补回被距离挡掉的座位 4；④ / ⑤ 的判据在它之后照常跑"
    )


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


# ---------------------------------------------------------------------------
# 神甘宁【魄袭】【劫营】（lei-shenganning 票 01；用户 2026-11-07 逐条签）
#
# 牌面（逐字，见票面 §0）：
#
#     魄袭：出牌阶段限一次，你可以观看一名其他角色的手牌，且你可以弃置你与其手牌中共计
#           四张不同花色的牌，然后若你以此法被弃置的牌数为：0 减 1 点体力上限；1 结束
#           出牌阶段且本回合手牌上限-1；3 回复 1 点体力；4 摸四张牌。
#     劫营：①回合开始时，若场上没有角色有"营"标记，你获得"营"标记；②结束阶段，你可以
#           将"营"标记交给一名其他角色；③有"营"标记的其他角色的回合结束时，移去"营"
#           标记，然后你获得其所有手牌。
#           【"营"标记：摸牌阶段多摸一张、出牌阶段使用【杀】次数上限+1、手牌上限+1。】
#
# 用户裁定的三处（票面 §R）：① 1 档 = 玩家点「结束出牌阶段」同一效果（框架票 92）；
# ② 选牌面 = 两行观看面（框架票 93）；③ 营的三个加成是**标记自身效果**（框架票 94）、
# 不进同时机顺序选择（框架票 95）。
# ---------------------------------------------------------------------------

SHEN_GANNING = "shen_ganning"
POXI = "hero.shen_ganning.poxi"
JIEYING = "hero.shen_ganning.jieying"
JIEYING_EFFECT = "hero.shen_ganning.jieying_mark"
YING = "营"
SUITS = ("spade", "heart", "club", "diamond")


def _by_suit(*, excluded: tuple[int, ...] = ()) -> dict[str, int]:
    """四种花色各取一张（牌堆数据序，确定性）。"""
    found: dict[str, int] = {}
    for card_id, card in enumerate(PACK.deck.cards):
        if card_id in excluded or card.suit in found:
            continue
        found[card.suit] = card_id
        if len(found) == len(SUITS):
            break
    return found


def _cards(*, suits: tuple[str, ...] = SUITS, excluded: tuple[int, ...] = ()) -> tuple[int, ...]:
    pick = _by_suit(excluded=excluded)
    return tuple(pick[suit] for suit in suits)


def _ganning_state(
    *,
    mine: tuple[int, ...] = (),
    theirs: tuple[int, ...] = (),
    other: tuple[int, ...] = (),
    seats: int = 2,
    their_hero: str | None = None,
    my_hp: int = 6,
    my_hp_max: int | None = None,
    their_hp: int = 8,
    turn_seat: int = 0,
    phase=Phase.PLAY,
    their_judge: tuple[int, ...] = (),
    deck_top: tuple[int, ...] = (),
):
    """神甘宁（座位 0）+ 目标（座位 1）；体力 6 = 牌面（`SeatSpec` 缺省是 4，必须显式给）。"""
    used = {*mine, *theirs, *other, *their_judge, *deck_top}
    rest = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    specs = [
        SeatSpec(
            hero=SHEN_GANNING,
            hp=my_hp,
            hp_max=my_hp if my_hp_max is None else my_hp_max,
            hand=mine,
        ),
        SeatSpec(
            hero=their_hero,
            hp=their_hp,
            hp_max=their_hp,
            hand=theirs,
            judge=their_judge,
        ),
    ]
    if seats >= 3:
        specs.append(SeatSpec(hp=4, hp_max=4, hand=other))
    return build_state(
        pack=PACK,
        seats=specs,
        deck=(*deck_top, *rest),
        turn_seat=turn_seat,
        phase=phase,
    )


class _Ganning:
    """座位 0 的脚本：出牌阶段发动【魄袭】；选牌面按 ``picks`` 提交；结束阶段是否交出「营」。"""

    def __init__(
        self,
        *,
        picks: tuple[int, ...] | None = None,
        activate: bool = True,
        give_away: bool = True,
        seat: str = "seat:1",
    ) -> None:
        self.picks = picks
        self.activate = activate
        self.give_away = give_away
        self.seat = seat
        self.activations: list = []
        self.selections: list = []
        self.offers: list = []
        self.played = False

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION" and str(query.skill_id) == JIEYING:
            self.activations.append(query)
            if self.give_away:
                return next(
                    a for a in query.legal_actions if isinstance(a, ActivateSkill)
                )
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if kind == "SELECT_OPTION":
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and str(action.choice) == self.seat:
                    return action
            return next(a for a in query.legal_actions if isinstance(a, SelectOption))
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            slots = {
                slot.card.card_id: index
                for index, slot in enumerate(query.slots)
                if slot.card is not None
            }
            if self.picks is None:
                for action in query.legal_actions:
                    if isinstance(action, Pass):
                        return action
                return query.legal_actions[0]
            # 引擎的 `SelectCards` 候选按面内位置**升序**枚举（票 93 的提交纪律），
            # 故提交前排序——与前端 `toggleWatchPick` 的规范化同一口径。
            return SelectCards(
                picks=tuple(sorted(slots[card_id] for card_id in self.picks)),
                answers_seq=query.seq,
            )
        if kind == "PLAY_PHASE":
            if self.activate and not self.played:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == POXI:
                        self.played = True
                        self.offers.append(query)
                        return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _ying(engine, holder: int, *, source: int = 0) -> None:
    """给 ``holder`` 放一枚「营」（与 ① 的写入面同一组参数）。"""
    engine.add_marker(
        None,
        holder,
        name=YING,
        source_seat=source,
        source_skill_id=JIEYING,
        count=1,
        effect=JIEYING_EFFECT,
        clear_on_source_death=True,
        clear_on_source_skill_lost=True,
    )


def _marker_changes(engine) -> list[tuple[int, str, int]]:
    return [
        (record.seat, record.name, record.count)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "marker_change"
        and record.name == YING
    ]


def _moves(engine, *, reason, to_zone: Zone | None = None, seat: int | None = None) -> list:
    out = []
    for record in engine.records:
        if record.kind != "delta" or record.delta_type != "card_move":
            continue
        if record.reason is not reason:
            continue
        if to_zone is not None and record.to_zone.zone is not to_zone:
            continue
        if seat is not None and record.to_zone.seat != seat:
            continue
        out.append(record)
    return out


def _poxi_engine(state, *, picks=None, activate=True, seat="seat:1"):
    engine = Engine.from_state(state, seed=7)
    return engine, _Ganning(picks=picks, activate=activate, seat=seat)


# --- 魄袭 ---------------------------------------------------------------


def test_poxi_target_pool_is_other_alive_with_hand_cards():
    """目标池 = 其他 + 存活 + **手牌非空**（没有牌的人看不了，也不该出现在选座候选里）。"""
    from sgs.skills.active import _poxi_targets

    state = _ganning_state(mine=(0,), theirs=(10,), other=(20,), seats=3)
    assert _poxi_targets(state, 0) == (1, 2)
    state.player(2).hand = []
    assert _poxi_targets(state, 0) == (1,), "手牌空 ⇒ 不进池"
    state.player(1).alive = False
    assert _poxi_targets(state, 0) == ()


def test_poxi_is_not_offered_without_a_watched_target():
    """一个可观看的目标都没有 ⇒ 候选不产出、按钮不出现（零副作用）。"""
    state = _ganning_state(mine=(0, 1), theirs=())
    engine, player = _poxi_engine(state)
    run_queries(engine, [player, PassPlayer()], lambda e: e.state.phase is Phase.DISCARD)
    assert player.offers == [], "池空 ⇒ 连「发动魄袭」这一条都不出现"


def test_poxi_still_watches_when_no_four_suits():
    """凑不出四色 ⇒ 仍发一条**空选择面**（看得见目标整副手牌），出口只有「放弃」。"""
    hearts = tuple(
        card_id for card_id, card in enumerate(PACK.deck.cards) if card.suit == "heart"
    )[:2]
    state = _ganning_state(theirs=hearts)
    engine, player = _poxi_engine(state, picks=None)
    run_queries(engine, [player, PassPlayer()], lambda e: bool(player.selections))
    query = player.selections[0]
    assert query.slots == (), "凑不出四色 ⇒ 空选择面（只有 Pass）"
    assert [type(action).__name__ for action in query.legal_actions] == ["Pass"]
    assert [card.card_id for card in query.revealed] == list(hearts), (
        "整副被观看的手牌仍在 revealed 里（空面约定：只含**上行**那一手）"
    )


def test_poxi_timeout_passes_without_any_tier_effect():
    """「超时 = 放弃」⇒ 不弃置、不结算任何分档。

    引擎侧只认 `Pass`（超时的**落点**由接入层给：`tools/sgs_web/timeout.py` 的
    `FALLBACK_ORDER` 让 `Pass` 优先，钉在
    `tests/test_web.py::test_the_two_row_watch_face_times_out_to_pass` —— 那一面与本技能的
    询问面同一形状）。故这里答 `Pass`，断言"零副作用"。
    """
    cards = _cards()
    state = _ganning_state(theirs=cards)
    engine, player = _poxi_engine(state, picks=None)
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: bool(player.selections)
        and ran_timing(e, Timing.PHASE_DISCARD_START),
    )
    assert engine.state.player(1).hand == list(cards), "一张没弃"
    assert engine.state.player(0).hp_max == 6, "0 档效果不发生"
    assert engine.state.discard == [], "弃牌堆为空"


def test_poxi_zero_cards_cuts_the_max_hp():
    """0 档（四张全来自对方手牌）⇒ 你减少 1 点体力上限。"""
    cards = _cards()
    state = _ganning_state(theirs=cards)
    engine, player = _poxi_engine(state, picks=cards)
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: bool(player.selections) and e.state.player(0).hp_max != 6,
    )
    assert engine.state.player(0).hp_max == 5
    assert set(engine.state.discard) == set(cards), "四张都进了弃牌堆"


def test_poxi_zero_cards_at_a_max_hp_of_one_now_kills():
    """0 档在**体力上限已为 1** 时**照减**：上限 1 → 0，体力置 0，**直接死亡**（票 04）。

    用户 2026-11-07 裁定「体力为 1 也不兜底，必须扣减」：这不是"零效果"，而是规则 §四
    4.15 L431–433 的 0 档——**不进濒死**、不能进行濒死结算，直接死亡。弃置四张照常发生
    （它在减上限之前）。
    """
    cards = _cards()
    state = _ganning_state(theirs=cards, my_hp=1, my_hp_max=1)
    engine, player = _poxi_engine(state, picks=cards)
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: not e.state.player(0).alive or len(_moves(e, reason=MoveReason.DISCARD)) == 1,
    )
    assert set(cards) <= set(engine.state.discard), "弃置照常发生（在减上限之前）"
    dead = engine.state.player(0)
    assert (dead.alive, dead.hp, dead.hp_max) == (False, 0, 0), "上限 1 减 1 ⇒ 体力置 0 + 死亡"
    # 记录流：hp_max_change(1→0) 与 hp_change(1→0) 按序；**没有**濒死帧 / 濒死时机。
    deltas = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type in ("hp_max_change", "hp_change")
        and record.seat == 0
    ]
    assert [(record.delta_type, record.before, record.after) for record in deltas] == [
        ("hp_max_change", 1, 0),
        ("hp_change", 1, 0),
    ]
    assert not [
        record
        for record in engine.records
        if record.kind == "frame" and record.frame_kind is FrameKind.NEAR_DEATH
    ], "上限归零不进濒死（规则 L431–433）"
    assert any(
        record.kind == "frame" and record.frame_kind is FrameKind.DEATH
        for record in engine.records
    ), "直接死亡：DEATH 帧照常"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing.value.startswith("NEAR_DEATH")
    ], "没有一个濒死时机节点被经过"


def test_poxi_one_card_ends_the_play_phase_and_cuts_the_hand_limit():
    """1 档（只有 1 张来自我）⇒ 结束出牌阶段 + 本回合手牌上限 -1（框架票 92 的挂起语义）。"""
    pick = _by_suit()
    mine = (pick["spade"],)
    theirs = (pick["heart"], pick["club"], pick["diamond"])
    state = _ganning_state(mine=mine, theirs=theirs)
    engine, player = _poxi_engine(state, picks=(*mine, *theirs))
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DISCARD_START) or e.finished,
    )
    assert player.offers, "构造：这一次确实是发动过的"
    assert engine.state.player(0).hand_limit_bonus == -1, "本回合手牌上限 -1"
    assert ran_timing(engine, Timing.PHASE_PLAY_END), "出牌阶段就此结束（走进结束边界）"
    played = [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.PLAY_PHASE.value
    ]
    assert len(played) == 1, (
        f"结束出牌阶段后不再问第二问（与玩家点「结束出牌阶段」同一效果）；实得 {len(played)} 问"
    )


def test_poxi_two_cards_from_self_has_no_effect():
    """2 档：牌面三平台都没有【2】⇒ 只有弃置发生、没有后续。"""
    pick = _by_suit()
    mine = (pick["spade"], pick["heart"])
    theirs = (pick["club"], pick["diamond"])
    state = _ganning_state(mine=mine, theirs=theirs)
    engine, player = _poxi_engine(state, picks=(*mine, *theirs))
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: len(_moves(e, reason=MoveReason.DISCARD)) == 2,
        limit=200,
    )
    assert engine.state.player(0).hp_max == 6, "2 档不碰上上限"
    assert engine.state.player(0).hand_limit_bonus == 0, "也不动手牌上限"
    assert set(engine.state.discard) == set((*mine, *theirs)), "弃置照常发生"


def test_poxi_three_cards_recovers_one_hp():
    """3 档（3 张来自我）⇒ 你回复 1 点体力。"""
    pick = _by_suit()
    mine = (pick["spade"], pick["heart"], pick["club"])
    theirs = (pick["diamond"],)
    state = _ganning_state(mine=mine, theirs=theirs, my_hp=4, my_hp_max=6)
    engine, player = _poxi_engine(state, picks=(*mine, *theirs))
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: bool(player.selections) and e.state.player(0).hp == 5,
    )
    assert engine.state.player(0).hp == 5, "回复 1 点"


def test_poxi_three_cards_at_full_hp_is_a_legal_no_op():
    """3 档在**满体力**时回复落空，但弃置与结算照常（不是"卡片不能选"）。"""
    pick = _by_suit()
    mine = (pick["spade"], pick["heart"], pick["club"])
    theirs = (pick["diamond"],)
    state = _ganning_state(mine=mine, theirs=theirs, my_hp=6, my_hp_max=6)
    engine, player = _poxi_engine(state, picks=(*mine, *theirs))
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: len(_moves(e, reason=MoveReason.DISCARD)) == 2,
    )
    assert engine.state.player(0).hp == 6, "满体力 ⇒ 回复落空（不是非法动作）"
    assert set(engine.state.discard) == set((*mine, *theirs)), "四张照常弃置"


def test_poxi_four_cards_draws_four():
    """4 档（四张全来自我）⇒ 你摸四张牌（一条摸牌移动）。"""
    pick = _by_suit()
    mine = tuple(pick[suit] for suit in SUITS)
    theirs = (_by_suit(excluded=mine)["spade"],)
    state = _ganning_state(mine=mine, theirs=theirs)
    engine, player = _poxi_engine(state, picks=mine)

    def _drew_four(engine) -> bool:
        return any(
            len(move.cards) == 4
            for move in _moves(engine, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=0)
        )

    run_queries(engine, [player, PassPlayer()], _drew_four)
    # 起手的摸牌阶段也会摸（3 张：摸 2 + 「营」的标记加成 1），故这里认"**有一条** 4 张的摸牌"。
    assert _drew_four(engine), "4 档 ⇒ 一次摸四张（一条 card_move）"


def test_poxi_discards_both_hands_in_two_moves():
    """四张跨两副手牌 ⇒ 必须拆成**两次**搬牌（`move_card` 拒绝跨来源的一次搬牌）。"""
    pick = _by_suit()
    mine = (pick["spade"], pick["heart"])
    theirs = (pick["club"], pick["diamond"])
    state = _ganning_state(mine=mine, theirs=theirs)
    engine, player = _poxi_engine(state, picks=(*mine, *theirs))
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: len(_moves(e, reason=MoveReason.DISCARD)) == 2,
        limit=200,
    )
    discards = _moves(engine, reason=MoveReason.DISCARD)
    assert len(discards) == 2, f"两次搬牌（我方 / 对方各一次）；实得 {len(discards)}"
    assert {record.from_zone.seat for record in discards} == {0, 1}
    assert sorted(
        card.card_id for record in discards for card in record.cards
    ) == sorted((*mine, *theirs))


class _Accepts:
    """被动入口一律发动（阶段体 / 钩子的「是否发动」问都答发动），其余走最小合法应答。"""

    def respond(self, query):
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "ActivateSkill":
                return action
        return PassPlayer().respond(query)


def test_poxi_resumes_without_discarding_twice():
    """G7：弃置内联派发"失去牌后"（对方的【连营】）⇒ 续跑既不重搬、也不重复触发。"""
    cards = _cards()
    state = _ganning_state(theirs=cards, their_hero="luxun")
    engine, player = _poxi_engine(state, picks=cards)
    run_queries(
        engine,
        [player, _Accepts()],
        lambda e: ran_timing(e, Timing.PHASE_DISCARD_START) or e.finished,
    )
    discards = _moves(engine, reason=MoveReason.DISCARD)
    assert len(discards) == 1, f"只搬一次（续跑不重搬）；实得 {len(discards)}"
    assert len(discards[0].cards) == 4
    assert len(engine.state.player(1).hand) == 1, "【连营】摸的那一张还在（没有被二次搬走）"


# --- 劫营 ---------------------------------------------------------------


def test_jieying_gains_the_marker_at_the_turn_start():
    """① 自己的回合开始时，场上没有「营」⇒ 自己获得一枚（强制段，不问）。"""
    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=(10,)), seed=7)
    player = _Ganning(activate=False, give_away=False)
    _run_turns(engine, [player, _Accepts()], turns=1)
    assert [marker.name for marker in engine.state.player(0).markers] == [YING], (
        "回合开始时获得「营」（② 放弃了，故它留在我身上）"
    )
    assert _marker_changes(engine)[0] == (0, YING, 1)
    marker = engine.state.player(0).markers[0]
    assert marker.effect == JIEYING_EFFECT, "标记带着**自身效果**的定义 id"
    assert marker.clear_on_source_death and marker.clear_on_source_skill_lost, (
        "两条寿命策略都在（来源阵亡 / 来源失去【劫营】）"
    )
    assert player.activations, "① 是强制段 ⇒ 不问；这一条问的是 ②（结束阶段交营）"


def test_jieying_does_not_grant_a_second_marker():
    """① 的闸门 = **场上**没有「营」（别人身上有也照挡，否则交出营之后每个回合都再得一枚）。"""
    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=(10,)), seed=7)
    _ying(engine, 1)
    player = _Ganning(activate=False)
    _run_turns(engine, [player, _Accepts()], turns=1)
    assert engine.state.player(0).markers == (), "我自己不再获得（场上已经有营）"
    assert [marker.name for marker in engine.state.player(1).markers] == [YING]
    assert _marker_changes(engine) == [(1, YING, 1)], "① 一个标记都没发"


def test_jieying_give_away_is_not_offered_without_the_marker():
    """② 的闸门 = **营在我身上**：营在别人身上时，我的结束阶段连问都不发（零副作用）。

    构造：营在座位 1（② 之前先交出去，或 ① 之前就在别人身上）；我的结束阶段照常到达，
    但「是否发动」那一问不出现——否则玩家会看到一个"发动了也交不出去"的按钮。
    """
    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=(10,)), seed=7)
    _ying(engine, 1)
    player = _Ganning(activate=False)
    _run_turns(engine, [player, _Accepts()], turns=1)
    assert ran_timing(engine, Timing.PHASE_FINISH), "构造：我的结束阶段确实跑过"
    assert player.activations == [], "营不在我身上 ⇒ ② 不问"


def test_jieying_marker_transfer_needs_another_alive_seat():
    """② 的两个前提之池：营在我身上，但**没有其他存活角色** ⇒ 不问（零副作用）。"""
    from sgs.skills.hero_lei import _jieying_hand_over_condition

    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=(10,)), seed=7)
    _ying(engine, 0)
    frame = _Frame(hook_owner=0, source=0)
    assert _jieying_hand_over_condition(_Host(engine.state), frame) is True
    engine.state.player(1).alive = False
    engine.state.player(1).hand = []
    assert _jieying_hand_over_condition(_Host(engine.state), frame) is False


def test_jieying_takes_the_holders_hand_at_turn_end():
    """③ 有营的其他角色回合结束时 ⇒ 移去营，然后**一次搬牌**拿走其所有手牌。"""
    theirs = (10, 11, 12)
    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=theirs), seed=7)
    player = _Ganning(activate=False)
    _run_turns(engine, [player, _Accepts()], turns=2)

    assert not [m for m in engine.state.player(1).markers if m.name == YING]
    assert set(theirs) <= set(engine.state.player(0).hand), "其所有手牌归我"
    moves = _moves(engine, reason=MoveReason.PUT, to_zone=Zone.HAND, seat=0)
    assert len(moves) == 1, f"一次搬牌拿走整副手牌（不逐张触发 ③）；实得 {len(moves)} 次"
    assert set(theirs) <= {card.card_id for card in moves[0].cards}, (
        "搬的是他的**整副**手牌（含摸牌阶段摸到的那些）"
    )
    assert _marker_changes(engine) == [
        (0, YING, 1),
        (1, YING, 1),
        (0, YING, 0),
        (1, YING, 0),
    ], "① 挂在我身上 → ② 交给他（先加后移）→ ③ 移去"


def test_jieying_takes_nothing_when_the_holder_has_no_hand():
    """G2：持有者**没有手牌**时 ③ 仍然发生——只移去「营」，不搬任何牌。

    直接驱动 ③ 的回调（引擎即 host）：真局里持有者在自己的回合会摸牌，故"回合末 0 手牌"
    要么是他把牌花光了（本测不构造那条长链），要么是构造局面。这里只钉"零手牌不崩、不搬"。
    """
    from sgs.skills.hero_lei import _jieying_take

    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=()), seed=7)
    _ying(engine, 1)
    frame = engine.push_frame(
        FrameKind.SKILL, {"skill_owner": 0, "source": 1}, timing=Timing.TURN_END
    )
    assert _jieying_take(engine, frame) is None
    assert engine.state.player(1).markers == (), "标记照常移去"
    assert not _moves(engine, reason=MoveReason.PUT, to_zone=Zone.HAND, seat=0), (
        "没有手牌 ⇒ 不产生搬牌"
    )


def test_jieying_and_zhengku_force_an_order_at_turn_end():
    """③ 与【镇骨】同一时点（`TURN_END`）：两条都不可放弃 ⇒ 面**没有 Pass**，必须选一条。"""
    engine = Engine.from_state(
        _ganning_state(mine=(0, 1), theirs=(10, 11, 12)), seed=7
    )
    engine.grant_skill(0, ZHENGKU)
    _ying(engine, 1)
    engine.add_marker(
        None,
        1,
        name=MARK,
        source_seat=0,
        source_skill_id=ZHENGKU,
        count=1,
        clear_on_source_death=True,
    )
    orders: list = []

    class _Order:
        """【劫营】② 答发动；同一时机的两条强制段（劫营③ / 镇骨）⇒ 先结劫营。"""

        def respond(self, query):
            kind = str(getattr(query.kind, "value", query.kind))
            if kind == "SKILL_ACTIVATION":
                picks = [
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                ]
                if len(picks) >= 2:
                    orders.append(query)
                    return next(
                        action for action in picks if action.skill_id == JIEYING
                    )
                if picks:
                    return picks[0]
                return PassPlayer().respond(query)
            if kind == "SELECT_OPTION":
                return PassPlayer().respond(query)
            if kind == "PLAY_PHASE":
                return next(a for a in query.legal_actions if isinstance(a, EndPhase))
            return PassPlayer().respond(query)

    player = _Order()
    _run_turns(engine, [player, _Accepts()], turns=2)

    assert orders, "同一时机的两条强制段 ⇒ 出一张「先结哪一条」的面"
    assert not any(
        action.__class__.__name__ == "Pass" for action in orders[0].legal_actions
    ), "两条都不可放弃 ⇒ 面里没有 Pass（必须选一条）"
    assert {
        str(action.skill_id)
        for action in orders[0].legal_actions
    } == {JIEYING, ZHENGKU}
    # 先劫营 ⇒ 对方手牌被拿空；随后【镇骨】把他摸回与我相同（差值 ≥ 1 时）。
    assert engine.state.player(1).markers == (), "两条的标记都结算掉了"


def _play_phases(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
    )


def test_jieying_extra_draw_skips_a_skipped_draw_phase():
    """标记自身效果·摸牌：持有者的摸牌阶段一次摸 3 张；被【兵粮寸断】跳过时整段不存在。

    构造：座位 1 先持营（`_ying`，与 ① 的写入面同一组参数），跑到**座位 1 的出牌阶段**
    （第 2 个 `PHASE_PLAY_START`）为止——那之后 ③ 会拿走它的手牌，故只看摸牌结果。
    """
    # 对照组（108 档）：座位 1 持营 ⇒ 摸牌阶段摸 3 张（2 + 营的 1）。
    plain = Engine.from_state(_ganning_state(mine=(0,), theirs=(10,)), seed=7)
    _ying(plain, 1)
    run_queries(plain, [_Accepts(), _Accepts()], lambda e: _play_phases(e) >= 2)
    draws = _moves(plain, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=1)
    assert [len(move.cards) for move in draws] == [3], (
        f"2 + 营的 1 = **一次**摸 3 张；实得 {[len(m.cards) for m in draws]}"
    )

    # 跳过：判定区有【兵粮寸断】且判非梅花 ⇒ 摸牌阶段整段不存在 ⇒ +1 无从生效。
    # （【兵粮寸断】只在 160 档的牌堆里 ⇒ 这一半用 160 档建局。）
    pack = load_content_pack(deck_profile="standard_ex_160")
    shortage = cards_of_type(pack, "supply_shortage")[0]
    judge = next(
        card_id
        for card_id, card in enumerate(pack.deck.cards)
        if card.suit != "club" and card_id not in (shortage, 0, 1)
    )
    used = {shortage, judge, 0, 1}
    deck = (judge, *(c for c in range(len(pack.deck.cards)) if c not in used))
    built = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero=SHEN_GANNING, hp=6, hp_max=6, hand=(0,)),
            SeatSpec(hp=8, hp_max=8, hand=(1,), judge=(shortage,)),
        ],
        deck=deck,
    )
    skipped = Engine.from_state(built, seed=7)
    _ying(skipped, 1)
    run_queries(skipped, [_Accepts(), _Accepts()], lambda e: _play_phases(e) >= 2)
    assert skipped.state.player(1).hand == [1], "一张都没摸（+1 无处生效）"
    assert skipped.state.player(1).hand_limit_bonus == 0, "标记本身没有被顺手撤掉"


def test_jieying_slash_limit_only_binds_the_holders_play_phase():
    """标记自身效果·【杀】次数：只给**持有者**自己的出牌阶段 +1 点上限，不改别人的。"""
    slash = cards_of_type(PACK, "slash")[0]
    engine = Engine.from_state(_ganning_state(mine=(slash, 1)), seed=7)
    card = engine.state.card(slash)
    # 出牌阶段由 `check_count` 自己判（【杀】只在"自己的出牌阶段"当限制读），故把局面摆到
    # 座位 0 的出牌阶段，再用"已用 1 张"区分上限 1 / 2。
    engine.state.player(0).card_use_counts["slash"] = 1
    assert check_count(engine.state, 0, card) is True, "没有营 ⇒ 上限 1、已用 1 ⇒ 超额"
    _ying(engine, 0)
    assert check_count(engine.state, 0, card) is False, "有营 ⇒ 上限 2、已用 1 ⇒ 还能出"

    # 别人身上没有营 ⇒ 别人不吃这条加成（把回合 / 阶段摆到座位 1，让他受次数约束）。
    other = Engine.from_state(_ganning_state(mine=(slash, 1)), seed=7)
    _ying(other, 0)
    other.state.turn_seat = 1
    other.state.phase = Phase.PLAY
    other.state.player(1).card_use_counts["slash"] = 1
    assert check_count(other.state, 1, other.state.card(slash)) is True, (
        "营在座位 0 身上 ⇒ 座位 1 的上限仍是 1"
    )


def test_jieying_hand_limit_returns_after_the_marker_leaves():
    """标记自身效果·手牌上限：查询期**持续加式**，标记一走立刻消失。"""
    from sgs.effects import hand_limit

    engine = Engine.from_state(_ganning_state(mine=(0, 1)), seed=7)
    base = hand_limit(engine.state, engine.state.player(0))
    _ying(engine, 0)
    bumped = hand_limit(engine.state, engine.state.player(0))
    assert bumped == base + 1, f"手牌上限 +1（{base} → {bumped}）"
    engine.remove_marker(
        None, 0, name=YING, source_seat=0, source_skill_id=JIEYING
    )
    assert hand_limit(engine.state, engine.state.player(0)) == base, "标记一走即恢复"


def test_jieying_turn_end_move_resumes_without_double_moving():
    """G7：③ 的"获得所有手牌"内联派发"失去牌后"（【连营】）⇒ 续跑不重搬。"""
    theirs = (10, 11, 12)
    engine = Engine.from_state(
        _ganning_state(mine=(0,), theirs=theirs, their_hero="luxun"), seed=7
    )
    player = _Ganning(activate=False)
    _run_turns(engine, [player, _Accepts()], turns=2)

    moves = _moves(engine, reason=MoveReason.PUT, to_zone=Zone.HAND, seat=0)
    assert len(moves) == 1, f"只搬一次；实得 {len(moves)}"
    assert set(theirs) <= {card.card_id for card in moves[0].cards}
    assert len(engine.state.player(1).hand) == 1, "【连营】摸的那一张留在对方手里"


def test_jieying_marker_survives_a_skipped_turn():
    """G9：持有者的回合被翻面跳过 ⇒ 该回合没有 `TURN_END`，③ 顺延到下一个真实回合末。

    计数只认 ``TURN_AFTER_END``（被跳过的回合**连这条记录都不发**），故翻面之后的
    `turns=2` 停在**我的第 2 个回合末**——那一轮正是"跳过"发生的位置。
    """
    engine = Engine.from_state(_ganning_state(mine=(0,), theirs=(10, 11)), seed=7)
    player = _Ganning(activate=False)
    _run_turns(engine, [player, _Accepts()], turns=1)  # 我的回合：① 得营、② 交给座位 1
    assert _marker_changes(engine) == [(0, YING, 1), (1, YING, 1), (0, YING, 0)], (
        "构造：我获得、又交出（先加后移 ⇒ 我这一枚归 0）"
    )

    engine.set_flipped(None, 1, True)  # 座位 1 的下个回合整轮跳过（翻回正面、不发 TURN_END）
    _run_turns(engine, [player, _Accepts()], turns=2)
    assert _marker_changes(engine) == [(0, YING, 1), (1, YING, 1), (0, YING, 0)], (
        "跳过的那一轮不结算 ③（标记留着）"
    )
    assert [marker.name for marker in engine.state.player(1).markers] == [YING]
    assert set((10, 11)) <= set(engine.state.player(1).hand), "手牌也没被拿走"

    _run_turns(engine, [player, _Accepts()], turns=3)  # 座位 1 的下一个**真实**回合
    assert _marker_changes(engine)[-1] == (1, YING, 0), "顺延到下一个真实回合末才移去"
    assert set((10, 11)) <= set(engine.state.player(0).hand), "那一次照常拿走其所有手牌"


def _hand_limit(engine, seat: int) -> int:
    from sgs.effects import hand_limit

    return hand_limit(engine.state, engine.state.player(seat))


def test_the_ying_marker_keeps_its_buffs_while_jieying_is_suppressed():
    """框架票 94 的 M1：【劫营】失效（被无效）⇒ 营还在、**三个加成**照常（用户 2026-11-07 裁）。"""
    slash = cards_of_type(PACK, "slash")[0]
    engine = Engine.from_state(_ganning_state(mine=(slash, 1)), seed=7)
    _ying(engine, 0)
    base_limit = _hand_limit(engine, 0)
    engine.state.player(0).card_use_counts["slash"] = 1

    engine.suppress_skills(0, "hero.anyone.tieqi")

    assert [marker.name for marker in engine.state.player(0).markers] == [YING], (
        "失效**不动标记**"
    )
    assert _marker_changes(engine) == [(0, YING, 1)], "失效不发 marker_change"
    assert _hand_limit(engine, 0) == base_limit, "手牌上限 +1 照常"
    assert check_count(engine.state, 0, engine.state.card(slash)) is False, (
        "【杀】次数上限 +1 照常"
    )
    # 第三条（摸牌 +1）在这条路的**实跑**里看：失效之后走一遍自己的摸牌阶段。
    suppressed = Engine.from_state(_ganning_state(mine=(0,)), seed=7)
    _ying(suppressed, 0)
    suppressed.suppress_skills(0, "hero.anyone.tieqi")
    run_queries(suppressed, [_Accepts(), _Accepts()], lambda e: _play_phases(e) >= 1)
    draws = _moves(suppressed, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=0)
    assert [len(move.cards) for move in draws] == [3], (
        "失效后摸牌仍是 2 + 营的 1（= 一次摸 3 张）"
    )


def test_losing_jieying_removes_the_ying_marker():
    """框架票 94 的 M2：来源**失去**【劫营】⇒ 标记移除、加成消失。"""
    engine = Engine.from_state(_ganning_state(mine=(0,)), seed=7)
    base = _hand_limit(engine, 1)
    _ying(engine, 1)
    assert _hand_limit(engine, 1) == base + 1, "构造：标记在时有 +1"

    assert engine.lose_skill(0, JIEYING) is True

    assert engine.state.player(1).markers == (), "标记被移除了"
    assert _marker_changes(engine)[-1] == (1, YING, 0), "走唯一写入口（绝对值 0）"
    assert _hand_limit(engine, 1) == base, "加成随之消失"


def test_the_marker_effect_ignores_suppression_on_either_side():
    """框架票 94 的 M8：**持有者**被失效时营的加成照常，而他自己那条技能**真的**停了。

    持有者 = 周瑜（有【英姿】，非锁定 ⇒ 可被失效）：失效后摸牌阶段少掉英姿那 +1，
    营的那 +1 仍在 ⇒ 一次摸 3 张（而不是 4）。
    """
    engine = Engine.from_state(
        _ganning_state(mine=(0,), theirs=(10,), their_hero="zhouyu"), seed=7
    )
    base = _hand_limit(engine, 1)
    _ying(engine, 1)
    assert _hand_limit(engine, 1) == base + 1, "构造：标记在时有 +1"
    engine.suppress_skills(1, "hero.anyone.tieqi")
    assert _hand_limit(engine, 1) == base + 1, "持有者自己被失效 ⇒ 营的加成照常"

    # 失效的作用域是**本回合**：先走完座位 0 的回合，再在他的回合里施加，才盖得住他的摸牌阶段。
    run_queries(engine, [_Accepts(), _Accepts()], lambda e: _completed_turns(e) >= 1)
    engine.suppress_skills(1, "hero.anyone.tieqi")
    run_queries(engine, [_Accepts(), _Accepts()], lambda e: _play_phases(e) >= 2)
    draws = _moves(engine, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=1)
    assert [len(move.cards) for move in draws] == [3], (
        "英姿被失效（少 1）+ 营照常（多 1）⇒ 一次摸 3 张；实得 "
        f"{[len(m.cards) for m in draws]}"
    )


def test_yingzi_and_the_ying_marker_add_up_to_one_draw():
    """框架票 94 的 M6：界【英姿】（基础 +1）与营（基础 +1）叠加 ⇒ 一次摸 4 张。"""
    engine = Engine.from_state(
        _ganning_state(mine=(0,), theirs=(10,), their_hero="jie_zhouyu"), seed=7
    )
    _ying(engine, 1)
    run_queries(engine, [_Accepts(), _Accepts()], lambda e: _play_phases(e) >= 2)
    draws = _moves(engine, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=1)
    assert [len(move.cards) for move in draws] == [4], (
        f"2 + 英姿 1 + 营 1 = 一次摸 4 张；实得 {[len(m.cards) for m in draws]}"
    )


def test_the_marker_buff_never_asks_for_an_order():
    """框架票 95 的 T7：标记自身效果**不进**同时机顺序面（周瑜【英姿】与营同在摸牌阶段）。"""
    engine = Engine.from_state(
        _ganning_state(mine=(0,), theirs=(10,), their_hero="zhouyu"), seed=7
    )
    _ying(engine, 1)
    asked: list = []

    class _Yingzi:
        def respond(self, query):
            kind = str(getattr(query.kind, "value", query.kind))
            if kind == "SKILL_ACTIVATION":
                asked.append(query)
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill):
                        return action
                return PassPlayer().respond(query)
            return _Accepts().respond(query)

    run_queries(engine, [_Accepts(), _Yingzi()], lambda e: _play_phases(e) >= 2)
    assert asked, "【英姿】照常问一次"
    assert all(str(query.skill_id) == "hero.zhouyu.yingzi" for query in asked), (
        f"同一时机只有【英姿】那一条技能效果；实得 {[str(q.skill_id) for q in asked]}"
    )
    assert JIEYING_EFFECT not in {
        str(action.skill_id)
        for query in asked
        for action in query.legal_actions
        if isinstance(action, ActivateSkill)
    }, "标记自身效果从不出现在「先结哪一条」的面里"
    draws = _moves(engine, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=1)
    assert [len(move.cards) for move in draws] == [4], (
        "英姿 1 + 营 1 + 基础 2 ⇒ 一次摸 4 张（两条加成走同一条写入口）"
    )


def test_poxi_four_card_draw_resumes_after_a_gaining_hook():
    """G7：4 档的"摸四张"内联派发"获得牌后"（【清俭】）⇒ 续跑不重摸、也不再结算一次分档。"""
    from sgs.skills.move_after import JIE_QINGJIAN

    mine = tuple(_by_suit()[suit] for suit in SUITS)
    theirs = (_by_suit(excluded=mine)["spade"],)
    engine, player = _poxi_engine(_ganning_state(mine=mine, theirs=theirs), picks=mine)
    engine.grant_skill(0, JIE_QINGJIAN)
    asked: list = []

    class _Decline:
        """除【清俭】那一问（答放弃）之外全部交给【魄袭】的脚本玩家。"""

        def respond(self, query):
            if str(getattr(query, "skill_id", "")) == JIE_QINGJIAN:
                asked.append(query)
                return PassPlayer().respond(query)
            return player.respond(query)

    run_queries(
        engine,
        [_Decline(), PassPlayer()],
        lambda e: bool(player.selections) and bool(asked),
    )
    assert asked, "构造：【清俭】在摸完四张后确实问过（这一次摸牌因此挂起过）"
    draws = _moves(engine, reason=MoveReason.DRAW, to_zone=Zone.HAND, seat=0)
    assert [len(move.cards) for move in draws].count(4) == 1, (
        f"摸四张只发生一次；实得 {[len(m.cards) for m in draws]}"
    )


# ---------------------------------------------------------------------------
# 陆抗移动版【决堰】/【破势】/ ☆【怀柔】（lukang-mobile 票 01；用户 2026-11-07 逐条签）
#
# 牌面（逐字）：
#
#     决堰
#     　出牌阶段限一次，你可以废除你装备区里的一种装备栏，然后执行对应的选项：
#       【武器栏】，此阶段内你使用【杀】的次数上限+3；
#       【防具栏】，摸三张牌且本回合的手牌上限+3；
#       【两个坐骑栏】，本回合你使用牌无距离限制。
#
#     破势
#     　觉醒技，准备阶段，若你的装备栏均被废除或体力值为1，你减1点体力上限并将手牌摸至
#       体力上限，然后你失去技能“决堰”并获得技能“怀柔”。
#
#     ☆ 怀柔
#     　出牌阶段，你可以重铸装备牌。
#
# 口径（票 01 的询问剧本）：废除 = **一个事件**废除该栏的槽（票 03）；「无距离限制」= 含
# 【杀】攻击范围 + 含【锦囊】额定距离；减上限**不兜底**（票 04）；【怀柔】= 预发动选 1 张
# 自己的装备牌（手牌 / 装备区）→ 重铸。
# ---------------------------------------------------------------------------

LUKANG = "lukang_mobile"
DUANYAN = "hero.lukang_mobile.duanyan"
POSHI = "hero.lukang_mobile.poshi"
HUAJROU = "hero.lukang_mobile.huairou"

DUANYAN_WEAPON = "duanyan_weapon"
DUANYAN_ARMOR = "duanyan_armor"
DUANYAN_HORSES = "duanyan_horses"


def _lukang_state(
    *,
    hp: int = 4,
    hp_max: int = 4,
    hand: tuple[int, ...] = (),
    equip: tuple[int, ...] = (),
    seats: int = 2,
):
    """``seats`` 人局：座位 0 = 陆抗移动版（其余座位无武将）。"""
    used = set(hand) | set(equip)
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    specs = [SeatSpec(hp=hp, hp_max=hp_max, hand=hand, equip=equip, hero=LUKANG)]
    specs += [SeatSpec(hp=4) for _ in range(seats - 1)]
    return build_state(pack=PACK, seats=specs, deck=deck, turn_seat=0)


def _lukang_players(engine):
    return [PassPlayer() for _ in range(len(engine.state.players))]


def _lukang_play(engine):
    players = _lukang_players(engine)
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    return engine.pending


def _lukang_submit_duanyan(engine, choice: str):
    """发动【决堰】并选一个装备栏，跑回下一次出牌阶段询问。"""
    players = _lukang_players(engine)
    query = _lukang_play(engine)
    engine.submit(ActivateSkill(skill_id=DUANYAN, answers_seq=query.seq))
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind != QueryKind.PLAY_PHASE.value,
    )
    assert str(engine.pending.skill_id) == "duanyan:column", engine.pending
    option = next(
        action
        for action in engine.pending.legal_actions
        if getattr(action, "kind", "") == "SelectOption"
        and getattr(action, "choice", "") == choice
    )
    engine.submit(option)
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    return engine


def _lukang_awaken(*, hp: int = 1, hp_max: int = 4, hand: tuple[int, ...] = (), equip: tuple[int, ...] = (), seats: int = 2):
    """让【破势】在准备阶段觉醒，跑回出牌阶段询问（返回 ``engine``，``pending`` 即那一问）。"""
    engine = Engine.from_state(
        _lukang_state(hp=hp, hp_max=hp_max, hand=hand, equip=equip, seats=seats), seed=7
    )
    _lukang_play(engine)
    return engine


def test_duanyan_weapon_column_seals_the_slot_and_raises_the_slash_limit():
    """【武器栏】：废除武器槽（武器先弃置）+ 本阶段【杀】次数上限 +3（阶段末清）。"""
    weapon = cards_of_type(PACK, "crossbow")[0]
    slash = cards_of_type(PACK, "slash")[0]
    engine = Engine.from_state(_lukang_state(hand=(slash,), equip=(weapon,)), seed=7)
    _lukang_submit_duanyan(engine, DUANYAN_WEAPON)

    player = engine.state.player(0)
    assert player.sealed_zones == ("weapon",), "一个事件只封武器槽（不是整个装备区）"
    assert player.equip == [] and weapon in engine.state.discard, "槽里的牌先强制弃置"
    assert player.duanyan_slash_bonus == 3
    # 基础上限 1 + 3 = 4：用掉 1 张仍能再用，用掉 4 张才超额。
    player.card_use_counts["slash"] = 1
    assert check_count(engine.state, 0, engine.state.card(slash)) is False
    player.card_use_counts["slash"] = 4
    assert check_count(engine.state, 0, engine.state.card(slash)) is True
    # 「此阶段内」：出牌阶段一结束就清（补充一手 Q6：下一回合不再有）。
    end = next(
        action for action in engine.pending.legal_actions if action.kind == "EndPhase"
    )
    engine.submit(end)
    run_queries(
        engine,
        _lukang_players(engine),
        lambda e: e.pending is not None and e.pending.kind == QueryKind.DISCARD.value,
    )
    assert engine.state.player(0).duanyan_slash_bonus == 0, "阶段末清掉那一轴"


def test_duanyan_armor_column_draws_three_and_raises_the_hand_limit():
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = Engine.from_state(_lukang_state(equip=(armor,)), seed=7)
    query = _lukang_play(engine)
    before = len(engine.state.player(0).hand)
    players = _lukang_players(engine)
    engine.submit(ActivateSkill(skill_id=DUANYAN, answers_seq=query.seq))
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind != QueryKind.PLAY_PHASE.value,
    )
    engine.submit(
        next(
            action
            for action in engine.pending.legal_actions
            if getattr(action, "choice", "") == DUANYAN_ARMOR
        )
    )
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )

    player = engine.state.player(0)
    assert player.sealed_zones == ("armor",)
    assert armor in engine.state.discard
    assert len(player.hand) == before + 3, "摸三张（恰好 3）"
    assert player.hand_limit_bonus == 3, "本回合手牌上限 +3"


def test_duanyan_horses_column_seals_two_slots_and_waives_distance_for_slash_and_tricks():
    """【两个坐骑栏】：**一个事件**废除两个槽 ⇒ 本回合【杀】不查攻击范围、【锦囊】不查距离。"""
    offense = cards_of_type(PACK, "chitu")[0]
    defense = cards_of_type(PACK, "jueying")[0]
    slash = cards_of_type(PACK, "slash")[0]
    snatch = cards_of_type(PACK, "snatch")[0]
    state = _lukang_state(hand=(slash, snatch), equip=(offense, defense), seats=4)
    # 座位 2 有手牌：【顺手牵羊】的"区域里有牌"这条目标谓词与距离判据无关，先让它过闸。
    state.player(2).hand.append(cards_of_type(PACK, "dodge")[0])
    engine = Engine.from_state(state, seed=7)
    _lukang_submit_duanyan(engine, DUANYAN_HORSES)

    player = engine.state.player(0)
    assert player.sealed_zones == ("horse_offense", "horse_defense"), "两槽一次事件"
    assert {offense, defense} <= set(engine.state.discard)
    assert player.duanyan_no_distance is True
    # 座位 2 距离 2 > 攻击范围 1：平时够不着，骑栏效果下进池。
    assert 2 in check_targets(engine.state, 0, engine.state.card(slash))
    assert 2 in check_targets(engine.state, 0, engine.state.card(snatch)), "额定距离 1 被豁免"


def test_duanyan_offers_only_the_columns_that_are_still_available():
    engine = Engine.from_state(_lukang_state(), seed=7)
    # 游离帧（不入栈）：两个槽都是空的，帧只是"搬牌凭证"；push 进去会被回合环当成技能帧跑。
    carrier = Frame(kind=FrameKind.SKILL, data={"skill_owner": 0})
    assert engine.abolish_slots(carrier, 0, ("weapon", "armor")) is None
    query = _lukang_play(engine)
    engine.submit(ActivateSkill(skill_id=DUANYAN, answers_seq=query.seq))
    run_queries(
        engine,
        _lukang_players(engine),
        lambda e: e.pending is not None and e.pending.kind != QueryKind.PLAY_PHASE.value,
    )
    options = [
        action.choice
        for action in engine.pending.legal_actions
        if getattr(action, "kind", "") == "SelectOption"
    ]
    assert options == [DUANYAN_HORSES], "已封满的栏不再列出（不是置灰）"


def test_duanyan_is_not_offered_once_every_slot_is_sealed():
    """补充一手 Q5：所有装备栏都被废除 ⇒ 连技能按钮都不出现。"""
    engine = Engine.from_state(_lukang_state(), seed=7)
    carrier = Frame(kind=FrameKind.SKILL, data={"skill_owner": 0})
    assert engine.abolish_slots(carrier, 0, EQUIP_SLOTS) is None
    query = _lukang_play(engine)
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == DUANYAN
        for action in query.legal_actions
    ), "四槽全封 ⇒ 没有可废除的栏 ⇒ 候选不出现"


def test_poshi_awakens_when_every_equipment_slot_is_sealed():
    """装备栏均被废除 ⇒ 觉醒：减 1 上限、手牌摸至上限、失去【决堰】、获得 ☆【怀柔】。"""
    engine = Engine.from_state(_lukang_state(seats=2), seed=7)
    carrier = Frame(kind=FrameKind.SKILL, data={"skill_owner": 0})
    assert engine.abolish_slots(carrier, 0, EQUIP_SLOTS) is None
    _lukang_play(engine)

    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (3, 3), "满体力减上限 ⇒ 体力同步下调（规则 L436–437）"
    assert len(player.hand) == 3 + 2, "摸至体力上限 3，然后摸牌阶段 +2"
    assert player.lost_skills == [DUANYAN]
    assert player.granted_skills == [HUAJROU]


def test_poshi_awakens_at_one_hp_even_with_slots_left():
    """「或体力值为1」：装备栏还在也照样觉醒。"""
    engine = _lukang_awaken(hp=1)
    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (1, 3)
    assert player.granted_skills == [HUAJROU]
    assert player.sealed_zones == (), "装备栏没被动过（只减上限 + 换技能）"


def test_poshi_at_max_hp_one_dies_after_the_max_hp_drop():
    """票 04 的 0 档：上限 1 时**照减**到 0 ⇒ 直接死亡，其余段不结算（不兜底）。"""
    engine = Engine.from_state(_lukang_state(hp=1, hp_max=1), seed=7)
    run_queries(
        engine,
        _lukang_players(engine),
        lambda e: not e.state.player(0).alive or e.finished,
    )
    player = engine.state.player(0)
    assert (player.alive, player.hp, player.hp_max) == (False, 0, 0)
    assert player.granted_skills == [], "减死之后不再授予 ☆【怀柔】"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing.value.startswith("NEAR_DEATH")
    ], "上限归零不进濒死"


def test_huairou_reforges_a_hand_equipment_card():
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    engine = _lukang_awaken(hp=1, hand=(armor,))
    query = engine.pending
    assert HUAJROU in dict(query.preactivations), "已翻预发动：清单要渲得出来"
    before = len(engine.state.player(0).hand)
    engine.submit(ActivateSkill(skill_id=HUAJROU, picks=(armor,), answers_seq=query.seq))
    run_queries(
        engine,
        _lukang_players(engine),
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    assert armor in engine.state.discard, "重铸 = 弃置这张牌"
    assert len(engine.state.player(0).hand) == before - 1 + 1, "弃置一张 + 摸一张"


def test_huairou_reforges_an_equipped_card():
    """补充一手 Q7：**已装备**的牌也能重铸（手牌 + 装备区）。"""
    weapon = cards_of_type(PACK, "crossbow")[0]
    engine = _lukang_awaken(hp=1, equip=(weapon,))
    assert engine.state.player(0).equip == [weapon]
    query = engine.pending
    engine.submit(ActivateSkill(skill_id=HUAJROU, picks=(weapon,), answers_seq=query.seq))
    run_queries(
        engine,
        _lukang_players(engine),
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    assert engine.state.player(0).equip == []
    assert weapon in engine.state.discard
    assert weapon not in engine.state.player(0).hand


def test_huairou_is_not_offered_without_equipment_cards():
    engine = _lukang_awaken(hp=1)
    player = engine.state.player(0)
    player.hand = [cards_of_type(PACK, "slash")[0]]
    player.equip = []
    candidates = legal_actions_for(
        engine.state, 0, QueryKind.PLAY_PHASE, answers_seq=999
    )
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == HUAJROU
        for action in candidates
    ), "没有装备牌 ⇒ 连候选都不进池"


def test_huairou_rejects_a_non_equipment_card():
    engine = _lukang_awaken(hp=1)
    player = engine.state.player(0)
    slash = cards_of_type(PACK, "slash")[0]
    player.hand.append(slash)
    query = engine.pending
    with pytest.raises(Exception):
        engine.submit(
            ActivateSkill(skill_id=HUAJROU, picks=(slash,), answers_seq=query.seq)
        )
    assert slash in player.hand, "非装备牌不是合法载荷（校验器拒收）"

def test_the_caller_range_exempt_flag_feeds_the_same_single_evaluation(monkeypatch):
    """P3：``check_targets(..., distance_exempt=True)`` 与豁免钩子**同一份求值**。

    两条声明来源（调用方布尔 / `TARGET_RANGE_EXEMPT` 钩子）都只回答"② 的豁免集是哪些座位"，
    随后走同一条 ``player.seat not in exempt`` 判据。故在同一个局面、同一份豁免集下，两条路
    必须给出**逐值相同**的合法座位集：

    - 座位 4（距离 2、区域里有牌）两条路都补回；
    - 座位 3（④ ``has_zone_cards`` 判非法）与座位 2（⑤ `TARGET_POOL` 删掉）两条路都救不
      回来——豁免在 ③④⑤ **之前**求值，`TARGET_POOL` 的删也不能被它抵消。

    少了任何一半，"同一份求值"就会退化成两份各说各话的实现（一条 `if distance_exempt`
    短路、一条走修饰器链）。
    """
    state, snatch = _exempt_everyone_board(monkeypatch)
    card = state.card(snatch)

    hooked = check_targets(state, 0, card)
    flagged = check_targets(state, 0, card, distance_exempt=True)

    assert hooked == (1, 4, 5), "钩子路径：② 的豁免集 = 全体其他存活座位"
    assert flagged == hooked, (
        "调用方布尔必须与豁免钩子给出同一个座位集："
        f"布尔路径 {flagged}、钩子路径 {hooked}"
    )
    assert 3 not in flagged, "④（区域里有牌）判非法的座位不许被任何一条豁免路径复活"
    assert 2 not in flagged, "⑤（TARGET_POOL）删掉的座位不许被任何一条豁免路径复活"

