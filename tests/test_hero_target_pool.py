"""C 族：目标池真过滤（票 06）。

落点与票 06 §验收一致，全部走**外部缝**：

- ``check_targets`` 返回的**合法池**（目标池真源）；
- ``legal_actions_for`` 的候选集合（候选与校验**同源**：候选里出现的目标集合恰等于池）；
- ``Engine.from_state`` 的人为局面 + 脚本玩家驱动的记录流（强制指定目标的路径、
  池空时走既有"目标减为 0"终止路径）。

两条锁定技的逐字语义（票 06 §要做什么 3）：

- 诸葛亮【空城】：自己没有手牌 **且** 牌名 ∈ {【杀】,【决斗】} ⇒ 自己从池里去掉；
- 陆逊【谦逊】：牌名 ∈ {顺手牵羊】,【乐不思蜀】} ⇒ 自己从池里去掉（与手牌无关，恒生效）。

不测回调的私有分支；方天画戟的空操作处理器与新语义共存由 ``tests/test_equipment.py``
的既有断言继续钉住。
"""

from __future__ import annotations

from sgs.data import Gender, RuleConfig
from sgs.effects import (
    CARD_BEHAVIORS,
    UseContext,
    check_targets,
    legal_actions_for,
    targets_within_attack_range,
)
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
)
from sgs.skills import pure_virtual_card

from support import SeatSpec, build_state, cards_of_type, run_until
from test_hero_active import Scripted, _decline, _find, _run, _skill_frame_popped
from test_skills import PACK, _deck_excluding

LIJIAN = "hero.diaochan.lijian"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _targets_of(actions, card_id: int) -> set[int]:
    """候选里该牌**每个目标**的并集（"候选里出现的目标集合"）。"""
    return {
        target
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id == card_id
        for target in action.targets
    }


# ---------------------------------------------------------------------------
# 诸葛亮【空城】：无手牌时不能被【杀】/【决斗】指定
# ---------------------------------------------------------------------------


def test_kongcheng_without_hand_cards_leaves_the_slash_pool():
    """手牌为 0 时，别人用【杀】的池里没有诸葛亮（票 06 §验收 1）。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hero="zhugeliang")],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == (), "空城的诸葛亮从【杀】池里去掉"
    assert _targets_of(_play_candidates(state), slash) == set(), "候选里也不出现"


def test_kongcheng_with_hand_cards_returns_to_the_slash_pool():
    """别人**有**手牌时诸葛亮回到池里（票 06 §验收 1 的反面对照，断言非空转）。"""
    slash, filler = _cards("slash")[0], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(filler,), hero="zhugeliang")],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == (1,)
    assert _targets_of(_play_candidates(state), slash) == {1}


# ---------------------------------------------------------------------------
# 陆逊【谦逊】：与手牌无关，恒挡住【顺手牵羊】/【乐不思蜀】
# ---------------------------------------------------------------------------


def test_qianxun_leaves_the_snatch_and_indulgence_pools_even_with_hand_cards():
    """无论手牌多少，【顺手牵羊】/【乐不思蜀】的池里没有陆逊；其它锦囊与【杀】不受影响。"""
    hand = (
        _cards("snatch")[0],
        _cards("indulgence")[0],
        _cards("dismantlement")[0],
        _cards("slash")[0],
    )
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=hand),
            SeatSpec(hand=(filler,), hero="luxun"),
            SeatSpec(hand=(filler,)),
        ],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(hand[0])) == (2,), "顺手牵羊去掉陆逊"
    assert check_targets(state, 0, state.card(hand[1])) == (2,), "乐不思蜀去掉陆逊"
    assert check_targets(state, 0, state.card(hand[2])) == (1, 2), "过河拆桥不受影响"
    assert check_targets(state, 0, state.card(hand[3])) == (1, 2), "【杀】不受影响"

    actions = _play_candidates(state)
    assert _targets_of(actions, hand[0]) == {2}
    assert _targets_of(actions, hand[1]) == {2}


def test_qianxun_leaves_the_pool_when_the_owner_has_no_hand_cards():
    """陆逊**没有手牌**时同样出池——谦逊与手牌无关（与空城的分界）。"""
    snatch = _cards("snatch")[0]
    equip = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(snatch,)), SeatSpec(equip=(equip,), hero="luxun")],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(snatch)) == ()


def test_kongcheng_does_not_remove_its_owner_from_the_snatch_pool():
    """两条锁定技互不串味：空城只挡【杀】/【决斗】，不挡【顺手牵羊】。"""
    snatch, slash = _cards("snatch")[0], _cards("slash")[0]
    equip = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(snatch, slash)), SeatSpec(equip=(equip,), hero="zhugeliang")],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(snatch)) == (1,), "空城不挡顺手牵羊"
    assert check_targets(state, 0, state.card(slash)) == (), "空城挡【杀】"


# ---------------------------------------------------------------------------
# 同源要求：候选里出现的目标集合恰等于 check_targets 返回的池（票 06 §要做什么 2）
# ---------------------------------------------------------------------------


def _play_phase_card_types() -> list[str]:
    """牌行为表里**在出牌阶段可用**的全部牌名（"每一张需要目标的牌"的枚举面）。"""
    return sorted(
        type_id
        for type_id, behavior in CARD_BEHAVIORS.items()
        if any(use.context is UseContext.PLAY_PHASE for use in behavior.use_timings)
    )


def _invariant_violations(state) -> list[str]:
    """对每张在手的出牌阶段牌，比对"候选里出现的目标集合"与 ``check_targets`` 的池。"""
    actions = _play_candidates(state)
    violations: list[str] = []
    for type_id in _play_phase_card_types():
        card_id = _cards(type_id)[0]
        if card_id not in state.player(0).hand:
            continue
        pool = set(check_targets(state, 0, state.card(card_id)))
        offered = _targets_of(actions, card_id)
        if offered != pool:
            violations.append(f"{type_id}: 候选目标 {sorted(offered)} != 池 {sorted(pool)}")
    return violations


def test_candidates_and_check_targets_read_the_same_pool_for_every_card_type():
    """全表逐一比对：候选与校验读**同一条链**（票 06 §要做什么 2 的断言）。"""
    filler = _cards("peach")[0]
    for type_id in _play_phase_card_types():
        card_id = _cards(type_id)[0]
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(card_id,)),
                SeatSpec(hand=(filler,)),
                SeatSpec(hand=(filler,)),
            ],
            turn_seat=0,
        )
        assert _invariant_violations(state) == [], f"{type_id} 的候选与池不同源"


def test_the_same_pool_invariant_holds_with_the_locked_skills_in_play():
    """两条锁定技在场时同源仍成立：池被删掉的目标**同样**从候选里消失。"""
    filler = _cards("peach")[0]
    for type_id in _play_phase_card_types():
        card_id = _cards(type_id)[0]
        state = build_state(
            pack=PACK,
            seats=[
                SeatSpec(hand=(card_id,)),
                SeatSpec(hand=(filler,), hero="luxun"),
                SeatSpec(hero="zhugeliang"),
            ],
            turn_seat=0,
        )
        assert _invariant_violations(state) == [], f"{type_id} 在有锁定技时不同源"


def test_the_locked_skills_shrink_candidates_exactly_as_much_as_the_pool():
    """空城 / 谦逊各自把候选与池**同幅**缩小（"同源"的正面判据，断言非空转）。"""
    slash, snatch = _cards("slash")[0], _cards("snatch")[0]
    filler = _cards("peach")[0]

    # 空城：诸葛亮在自己的回合里握有手牌 ⇒ 不回池；无手牌 ⇒ 出池。
    kongcheng_baseline = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(filler,)), SeatSpec(hand=(filler,))],
        turn_seat=0,
    )
    kongcheng = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(filler,)), SeatSpec(hero="zhugeliang")],
        turn_seat=0,
    )
    assert _targets_of(_play_candidates(kongcheng_baseline), slash) == {1, 2}
    assert _targets_of(_play_candidates(kongcheng), slash) == {1}, "空城删掉诸葛亮"

    # 谦逊：陆逊无论有没有手牌都出池；两个目标都有牌（过得了"区域里有牌"）。
    qianxun = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(snatch,)),
            SeatSpec(hand=(filler,), hero="luxun"),
            SeatSpec(hand=(filler,)),
        ],
        turn_seat=0,
    )
    assert _targets_of(_play_candidates(qianxun), snatch) == {2}, "谦逊删掉陆逊"

    for state, card_id in ((kongcheng, slash), (qianxun, snatch)):
        assert _targets_of(_play_candidates(state), card_id) == set(
            check_targets(state, 0, state.card(card_id))
        )


# ---------------------------------------------------------------------------
# 强制指定目标：借刀杀人的 Bn 候选也走同一条链（票 06 §验收 3）
# ---------------------------------------------------------------------------


def test_borrowed_sword_target_candidates_read_the_target_pool_chain():
    """持刀者"使用【杀】的合法目标 Bn"候选里不含空城状态的诸葛亮。"""
    slash = _cards("slash")[0]
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(hero="zhugeliang"),
        ],
        turn_seat=0,
    )

    engine_targets = targets_within_attack_range(state, 1)
    assert engine_targets == (0, 2), "引擎预选集含攻击范围内的全部座位"

    actions = legal_actions_for(
        state,
        1,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BORROWED_SWORD,
            required="slash",
            mode=RespondMode.USE,
            use_targets=engine_targets,
        ),
    )
    offered = {
        action.targets
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id == slash
    }

    assert check_targets(state, 1, state.card(slash)) == (0,), "空城把诸葛亮从【杀】池里去掉"
    assert offered == {(0,)}, "候选与池同源，不含诸葛亮"
    assert all(2 not in targets for targets in offered)


# ---------------------------------------------------------------------------
# W7-01：方天 × 借刀——Bn + 0..2 个额外目标（真【杀】）
# ---------------------------------------------------------------------------


def _halberd_borrow_candidates(state, holder: int, bn: int) -> set[tuple[int, ...]]:
    """持刀者窗口里该【杀】的全部目标组合（借刀的固定目标是 ``bn``）。"""
    actions = legal_actions_for(
        state,
        holder,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BORROWED_SWORD,
            required="slash",
            mode=RespondMode.USE,
            use_targets=(bn,),
        ),
    )
    return {
        action.targets
        for action in actions
        if isinstance(action, UseCard) and action.card.type == "slash"
    }


def test_a_halberd_holder_can_add_zero_one_or_two_targets_in_the_borrowed_window():
    """方天 + 最后一张手牌 ⇒ 借刀窗口的候选是 ``(Bn,)`` + 0..2 个其它合法目标（W7-01）。

    三档用**池大小**区分：0 个额外（2 人局，池只有 Bn）/ 1 个额外（空城把座位 3 滤掉）/
    2 个额外（4 人局全池）。每一条都恒含 Bn，且额外目标取自**同一个** ``check_targets`` 池。
    """
    slash = _cards("slash")[0]
    halberd = _cards("halberd")[0]

    two_player = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_cards("borrowed_sword")[0],)),
            SeatSpec(hand=(slash,), equip=(halberd,)),
        ],
        deck=_deck_excluding(slash, halberd),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    assert _halberd_borrow_candidates(two_player, 1, 0) == {(0,)}, "只有 Bn：0 个额外目标"

    one_extra = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_cards("borrowed_sword")[0],)),
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(),
            SeatSpec(hero="zhugeliang"),
        ],
        deck=_deck_excluding(slash, halberd),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    assert _halberd_borrow_candidates(one_extra, 1, 0) == {(0,), (0, 2)}, (
        "空城把座位 3 从池里去掉 ⇒ 只能加 1 个额外目标"
    )

    two_extra = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_cards("borrowed_sword")[0],)),
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(slash, halberd),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    candidates = _halberd_borrow_candidates(two_extra, 1, 0)
    assert candidates == {(0,), (0, 2), (0, 3), (0, 2, 3)}, "Bn + 至多两个其它合法目标"
    pool = check_targets(two_extra, 1, two_extra.card(slash))
    assert all(set(combo) <= set(pool) for combo in candidates), "额外目标过同一个 check_targets 池"
    assert all(0 in combo for combo in candidates), "每条候选都恒含 Bn"


def test_the_halberd_does_not_add_targets_when_the_slash_is_not_the_last_hand_card():
    """同样的持刀者窗口，手里还有别的牌 ⇒ 方天不成立：候选只有 ``(Bn,)``（W7-01）。"""
    slash, peach = _cards("slash")[0], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_cards("borrowed_sword")[0],)),
            SeatSpec(hand=(slash, peach), equip=(_cards("halberd")[0],)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_excluding(slash, peach),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )

    assert _halberd_borrow_candidates(state, 1, 0) == {(0,)}, (
        "此【杀】不是最后一张手牌 ⇒ 最大目标数仍为 1"
    )


def test_an_illegal_bn_leaves_no_halberd_candidate_and_the_preview_agrees():
    """Bn 在结算时已不合法（空城把它滤掉）⇒ 无 a 候选；auto-b 的预览读同一生成器（W7-01）。"""
    slash = _cards("slash")[0]
    halberd = _cards("halberd")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_cards("borrowed_sword")[0],)),
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(hero="zhugeliang"),
            SeatSpec(),
        ],
        deck=_deck_excluding(slash, halberd),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    assert 2 not in check_targets(state, 1, state.card(slash)), "Bn=2 被【空城】滤掉"

    assert _halberd_borrow_candidates(state, 1, 2) == set(), "Bn 不合法 ⇒ 这张牌没有候选"

    engine = Engine.from_state(state, seed=1)
    preview = engine.respond_candidates(
        1,
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        target=1,
        mode=RespondMode.USE,
        use_targets=(2,),
    )
    assert not [
        action for action in preview if isinstance(action, UseCard)
    ], "auto-b 预览（与 ask_respond 同一生成器）同样没有 a ⇒ 直接交出武器"
    assert any(isinstance(action, Pass) for action in preview)


# ---------------------------------------------------------------------------
# 池被清空 ⇒ 走既有的"目标减为 0"终止路径（票 06 §验收 4）
# ---------------------------------------------------------------------------


class _Scripted:
    """一个座位：出牌阶段按需打【借刀杀人】；响应窗口取第一张【杀】/ 放弃。"""

    def __init__(self, *, play_borrowed: bool = False) -> None:
        self._play_borrowed = play_borrowed

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            if self._play_borrowed:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "borrowed_sword":
                        self._play_borrowed = False
                        return action
                # W5-02b：借刀的入口只剩附带指定清单（裸候选已收回）。
                for action in getattr(query, "designation_actions", ()):
                    if isinstance(action, UseCard) and action.card.type == "borrowed_sword":
                        self._play_borrowed = False
                        return action
            return _first(query, EndPhase) or query.legal_actions[0]
        if query.kind == QueryKind.RESPOND:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type == "slash":
                    return action
            return _first(query, Pass) or query.legal_actions[0]
        if query.kind in (QueryKind.DODGE, QueryKind.PEACH):
            return _first(query, Pass) or query.legal_actions[0]
        return query.legal_actions[0]


def _first(query, action_type):
    for action in query.legal_actions:
        if isinstance(action, action_type):
            return action
    return None


def test_an_unplayable_forced_use_window_hands_over_the_weapon_without_asking():
    """a 不可行（Bn 在结算时已不合法）⇒ **不开窗口**、自动 b：武器进使用者手牌。

    场景：座位 0 打【借刀杀人】指定持刀的座位 1；座位 0 装了防御马（+1），故座位 1 的
    攻击范围内只有座位 2（空城的诸葛亮）。座位 1 手里有【杀】，但【杀】的池被【空城】
    清空 ⇒ 持刀者窗口的非 ``Pass`` 候选为空 ⇒ 不 ``ask_respond``，直接交出武器。
    """
    borrowed, slash = _cards("borrowed_sword")[0], _cards("slash")[0]
    weapon, horse = _cards("crossbow")[0], _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,), equip=(horse,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(hero="zhugeliang"),
        ],
        deck=_deck_excluding(borrowed, slash, weapon, horse),
        turn_seat=0,
    )
    assert targets_within_attack_range(state, 1) == (2,), "持刀者够得着的只有空城诸葛亮"
    assert check_targets(state, 1, state.card(slash)) == (), "池被链清空"

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(play_borrowed=True), _Scripted(), _Scripted()],
        lambda e: weapon in e.state.player(0).hand,
    )

    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and getattr(record.query, "cause", None) is WindowCause.BORROWED_SWORD
    ], "a 不可行 ⇒ 不发明询（自动 b）"
    assert weapon in engine.state.player(0).hand, "b：武器从持刀者装备区进使用者手牌"
    assert engine.state.player(1).equip == [], "武器已离区"
    assert slash in engine.state.player(1).hand, "没有 a ⇒ 那张【杀】原样留在手里"
    assert borrowed in engine.state.discard


def test_an_unplayable_forced_use_window_ignores_a_targetless_transform_candidate():
    """a 不可行（只有转化源、Bn 在结算时已不合法）⇒ 不发明询：转化候选不得退化成无目标使用。

    场景同上一条，但持刀者座位 1 = 赵云、手里一张【闪】（【龙胆】当【杀】用）、装备武器；
    座位 2 = 空城的诸葛亮。``forced_use_targets`` 对这张虚拟【杀】的池为空 ⇒
    ``virtual_card_use`` 的 RESPOND/USE 分支不得再造无目标候选，否则 auto-b 的预览会把它
    误算成"a 可行"，窗口里就会出现一条打向空气的【杀】。
    """
    borrowed, dodge = _cards("borrowed_sword")[0], _cards("dodge")[0]
    weapon, horse = _cards("crossbow")[0], _cards("jueying")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,), equip=(horse,)),
            SeatSpec(hand=(dodge,), equip=(weapon,), hero="zhaoyun"),
            SeatSpec(hero="zhugeliang"),
        ],
        deck=_deck_excluding(borrowed, dodge, weapon, horse),
        turn_seat=0,
    )
    assert targets_within_attack_range(state, 1) == (2,), "持刀者够得着的只有空城诸葛亮"

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(play_borrowed=True), _Scripted(), _Scripted()],
        lambda e: weapon in e.state.player(0).hand,
    )

    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and getattr(record.query, "cause", None) is WindowCause.BORROWED_SWORD
    ], "a 不可行 ⇒ 不发明询（转化候选也不得伪造无目标 a）"
    assert weapon in engine.state.player(0).hand, "b：武器进使用者手牌"
    assert engine.state.player(1).equip == []
    assert dodge in engine.state.player(1).hand, "没有 a ⇒ 转化源原样留在手里"


def test_a_play_phase_card_with_an_emptied_pool_is_simply_not_offered():
    """出牌阶段的池空 ⇒ 该牌不进候选（不新增"目标数为 0 就终止"的分支）。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hero="zhugeliang")],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == ()
    assert not any(
        isinstance(action, UseCard) and action.card.card_id == slash
        for action in _play_candidates(state)
    )


# ---------------------------------------------------------------------------
# 强制指定目标（主动技侧）：离间视为使用的【决斗】目标也走同一条链（票 06 §要做什么 2）
# ---------------------------------------------------------------------------


def test_lijian_does_not_choose_a_kongcheng_character_as_the_duel_target():
    """空城的诸葛亮不能被离间选为【决斗】的目标，但仍可作为**使用者**。

    W4-2 起【离间】第一段进预发动：两个池都在**清单**里（``user`` / ``victim`` 两步），
    受害者池按前序的使用者逐行下发（``when`` = 前两步答案）。
    """
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(filler,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(hand=(filler,), gender=Gender.MALE),
            SeatSpec(hero="zhugeliang", gender=Gender.MALE),
        ],
        deck=_deck_excluding(filler),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(_decline), Scripted(_decline), Scripted(_decline)],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE.value,
    )
    steps = engine.pending.preactivations[LIJIAN]

    assert "seat:2" in tuple(
        option for row in steps[1].pool for option in row.options
    ), "诸葛亮仍可作为【决斗】的使用者（使用者一侧不受目标限制）"
    victims_for_kongcheng = {
        option for row in steps[2].pool for option in row.options
    }
    assert victims_for_kongcheng == {"seat:1"}, (
        "诸葛亮（无手牌 ⇒ 空城）不能被选为【决斗】目标，故受害者池里只剩座位 1"
    )


def test_lijian_is_not_offered_when_no_ordered_pair_is_legal():
    """回归（票 01 引入步骤流水线时踩到）：**没有任何合法有序对**时连候选都不造。

    旧形状把"合法有序对"烘进候选，天然不会出现空池；改成发动后再问之后，"使用者"这一问
    必须**至少有一个合法受害者**，否则参与者会收到一个没有候选的 ``SELECT_OPTION``
    （启发式 AI 在那里直接抛 ``ValueError``；由 ``tests/test_all_ai_games.py`` 的全 AI 局
    扫出来）。判据仍取自同一条 ``check_targets`` 链。
    """
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(filler,), hero="diaochan", gender=Gender.FEMALE),
            SeatSpec(hero="zhugeliang", gender=Gender.MALE),
            SeatSpec(hero="zhugeliang", gender=Gender.MALE),
        ],
        deck=_deck_excluding(filler),
        turn_seat=0,
    )
    males = (1, 2)
    duel = pure_virtual_card(state, "duel")
    assert all(
        not [victim for victim in males if victim != user and victim in check_targets(state, user, duel)]
        for user in males
    ), "两名男性互为对方的【决斗】目标时都因空城出池"

    assert not [
        action
        for action in _play_candidates(state)
        if isinstance(action, ActivateSkill) and action.skill_id == LIJIAN
    ], "没有合法有序对 ⇒ 离间不进候选"


# ---------------------------------------------------------------------------
# 既有空操作处理器与新语义共存（票 06 §验收 5）
# ---------------------------------------------------------------------------


def test_halberd_extra_targets_also_read_the_filtered_pool():
    """【方天画戟】的 ``TARGET_POOL`` 处理器仍不改池，但它的额外目标取自**过滤后**的池。

    W7-01 起方天不再用 ``CANDIDATES`` 枚举追加组合，而是声明 ``TARGET_COUNT``（最后一张
    手牌 ⇒ 最大目标数 +2），候选由 ``effects.target_combos`` 按 ``额定..max`` 从**同一个**
    ``check_targets`` 池一次摊开——"额外目标过同一套合法性检测"因此仍是结构事实。
    """
    slash = _cards("slash")[0]
    halberd, filler = _cards("halberd")[0], _cards("peach")[0]

    baseline = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(hand=(filler,)),
            SeatSpec(hand=(filler,)),
        ],
        turn_seat=0,
    )
    with_locked = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(hand=(filler,)),
            SeatSpec(hero="zhugeliang"),
        ],
        turn_seat=0,
    )

    assert _targets_of(_play_candidates(baseline), slash) == {1, 2}, "方天画戟的既有语义"
    assert _targets_of(_play_candidates(with_locked), slash) == {1}, "额外目标也取过滤后的池"
    assert _targets_of(_play_candidates(with_locked), slash) == set(
        check_targets(with_locked, 0, with_locked.card(slash))
    )
