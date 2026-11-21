"""谋攻篇-同包：谋刘赪【掠影】【莺舞】（mou-gong-tong 票 01）。

用户 2026-11-21 签的 Q1–Q10（票面 §7）里能被机械钉住的那些：

- **Q2** = ② 逐目标折算：一名目标 1 个标记、两名目标 2 个、> 两名目标也只 2 个；
- **Q3** = 两条技能共用**一个**「椎」池，来源 = 掠影（失去掠影整池清空、失效不动）；
- **Q4** = ① 没有合法目标时，强制的"移去 2 个 + 摸 1 张"照做，只是不发"是否视为使用"那一问；
- **Q6** = 「若你拥有技能『掠影』」= **实例还在**（失效仍算拥有，失去才不算）；
- **Q7** = 每阶段计数复用 `catalog._PHASE_USE_MASK` 位段（不动共享代码），出牌阶段起点复位；
- **Q8** = 「无次数限制的【杀】」= `counts=False`（不占次数、也不受上限）。

时机面另钉两条（票面「本票的工程口径」第 1、2 条）：② 在**指定目标时**
（`USE_DESIGNATE_TARGET`，不是铁骑 / 烈弓的「指定目标后」），① 在 `RESOLVE_AFTER_END`。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_by_id, hero_skill_ids, load_content_pack
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.catalog import (
    MATCH_OWNER_KEY,
    instance_key,
    reset_phase_skill_state,
    resolve_instances,
)
from sgs.skills.hero_mou_gong import (
    _DAMAGE_TRICKS,
    _lveying_finish_condition,
    _lveying_gain,
    _lveying_target_condition,
    _mark_phase_use,
    _marker_count,
    _phase_uses,
    _yingwu_finish_condition,
    _yingwu_gain,
    _yingwu_target_condition,
    LVEYING,
    MARKER,
    YINGWU,
)
from sgs.state import (
    EndPhase,
    GameState,
    Pass,
    Phase,
    PlayCard,
    SelectCards,
    SelectOption,
    UseCard,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries

PACK = load_content_pack(deck_profile="standard_108")
PACK_160 = load_content_pack(deck_profile="standard_ex_160")
HERO = "mou_liucheng"


class _Host:
    """条件 / 回调只需要 ``state``（``_Host`` = 真实引擎也行，两者同形）。"""

    def __init__(self, state: GameState) -> None:
        self.state = state


class _Frame:
    """条件 / 回调只读 ``frame.data``（外加 `add_marker` 用的 ``frame_id``）。"""

    def __init__(self, **data: Any) -> None:
        self.data = dict(data)
        self.frame_id = 1


def _state(*, seats: int = 3, turn_seat: int = 0, phase: Phase = Phase.PLAY) -> GameState:
    specs = [SeatSpec(hero=HERO)]
    specs.extend(SeatSpec() for _ in range(seats - 1))
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK),
        turn_seat=turn_seat,
        phase=phase,
    )


def _state_160(*, seats: int = 3, turn_seat: int = 0, phase: Phase = Phase.PLAY) -> GameState:
    """160 档局面（多出【火攻】【铁索连环】【兵粮寸断】）。"""
    specs = [SeatSpec(hero=HERO)]
    specs.extend(SeatSpec() for _ in range(seats - 1))
    return build_state(
        pack=PACK_160,
        seats=specs,
        deck=deck_excluding(PACK_160),
        turn_seat=turn_seat,
        phase=phase,
    )


def _card(state: GameState, type_id: str) -> Any:
    """牌堆里第一张该牌名的 ``CardRef``（牌型判据只读 ``type``）。"""
    for card in state.cards:
        if card.type == type_id:
            return card
    raise AssertionError(f"{type_id} 不在牌堆里")


def _frame_for(
    state: GameState,
    *,
    card: Any,
    targets: tuple[int, ...],
    owner: int = 0,
    source: int | None = None,
) -> _Frame:
    return _Frame(
        **{
            MATCH_OWNER_KEY: owner,
            "source": owner if source is None else source,
            "card": card,
            "targets": targets,
        }
    )


def _markers(state: GameState, seat: int = 0) -> int:
    return _marker_count(state, seat)


# ---------------------------------------------------------------------------
# 数据面 / 声明面
# ---------------------------------------------------------------------------


def test_the_pack_ships_the_hero_with_both_skills() -> None:
    """数据面：谋攻篇-同包装载得出谋刘赪（真名刘赪、群、女、3 体力），两条技能都在 CATALOG。"""
    hero = hero_by_id(HERO)
    assert hero is not None
    assert hero.name == "谋刘赪"
    assert hero.true_name == "刘赪"
    assert hero.kingdom.value == "qun"
    assert hero.gender.value == "female"
    assert hero.hp_max == 3
    assert hero_skill_ids(HERO) == (LVEYING, YINGWU)
    assert LVEYING in CATALOG and YINGWU in CATALOG


def test_both_skills_are_two_forced_hooks() -> None:
    """声明面：每条技能 = 两个**强制**钩子（牌面无「可」）；② 是簿记段、① 不是。"""
    for skill_id in (LVEYING, YINGWU):
        definition = CATALOG[skill_id]
        assert definition.skill_type is SkillType.NORMAL
        assert definition.locked is False
        assert [hook.timing for hook in definition.hooks] == [
            Timing.USE_DESIGNATE_TARGET,
            Timing.RESOLVE_AFTER_END,
        ]
        assert all(hook.forced for hook in definition.hooks), "牌面没有「可以」⇒ 触发段不由玩家确认"
        assert definition.hooks[0].bookkeeping is True, "② 只是记账（加一个标记），不上脸"
        assert definition.hooks[1].bookkeeping is False, "① 有真效果（移去 / 摸牌 / 可选视为使用）"
        assert set(definition.step_requirements) == {"use", "target"}


def test_the_damage_trick_list_is_the_official_one() -> None:
    """伤害类锦囊 = 2022 第三章 L66 的官方名单里本仓有牌面的那四张（其余五张尚无牌堆数据）。"""
    types_160 = {card.type for card in PACK_160.deck.cards}
    assert _DAMAGE_TRICKS <= types_160, "名单里的牌名必须都在本仓牌堆里，否则是写错了 id"
    assert _DAMAGE_TRICKS == {"duel", "fire_attack", "barbarian_invasion", "arrow_barrage"}
    types_108 = {card.type for card in PACK.deck.cards}
    assert _DAMAGE_TRICKS - types_108 == {"fire_attack"}, "108 档没有【火攻】，它只在 160 档"
    for type_id in (
        "dismantlement",
        "snatch",
        "ex_nihilo",
        "nullify",
        "peach_garden",
        "borrowed_sword",
        "harvest",
        "iron_chain",
    ):
        assert type_id in types_160
        assert type_id not in _DAMAGE_TRICKS, "非伤害类普通锦囊不能被算进伤害名单"


# ---------------------------------------------------------------------------
# ② 的条件：自己的出牌阶段 / 额度 / 牌型 / 目标
# ---------------------------------------------------------------------------


def test_lveying_marker_needs_my_own_play_phase() -> None:
    """「出牌阶段限两次」⇒ 只在自己的出牌阶段（2022 第二章 L81；账本界【渐营】② 同一口径）。"""
    slash = _card(_state(), "slash")
    own = _state()
    other_turn = _state(turn_seat=1)
    wrong_phase = _state(phase=Phase.DRAW)
    assert _lveying_target_condition(_Host(own), _frame_for(own, card=slash, targets=(1,)))
    assert not _lveying_target_condition(
        _Host(other_turn), _frame_for(other_turn, card=slash, targets=(1,))
    ), "别人的出牌阶段不算"
    assert not _lveying_target_condition(
        _Host(wrong_phase), _frame_for(wrong_phase, card=slash, targets=(1,))
    ), "自己的摸牌阶段也不算"


def test_lveying_marker_needs_my_own_slash() -> None:
    """条件自校：不是我使用的【杀】、或那不是【杀】、或没有目标 ⇒ 不触发。"""
    state = _state()
    slash = _card(state, "slash")
    dismantlement = _card(state, "dismantlement")
    host = _Host(state)
    assert not _lveying_target_condition(
        host, _frame_for(state, card=slash, targets=(1,), source=1)
    ), "别人用的【杀】不给我标记"
    assert not _lveying_target_condition(
        host, _frame_for(state, card=dismantlement, targets=(1,))
    ), "锦囊不是【杀】"
    assert not _lveying_target_condition(host, _frame_for(state, card=slash, targets=()))


def test_gaining_markers_is_capped_at_two_per_play_phase() -> None:
    """用户裁定：一名目标 1 个、两名目标 2 个、> 两名目标也只 2 个；满额后不再给。"""
    state = _state(seats=4)
    slash = _card(state, "slash")
    engine = Engine.from_state(state, seed=7)

    _lveying_gain(engine, _frame_for(state, card=slash, targets=(1,)))
    assert _markers(state) == 1
    assert _phase_uses(state, 0, LVEYING) == 1

    _lveying_gain(engine, _frame_for(state, card=slash, targets=(1, 2)))
    assert _markers(state) == 2
    assert _phase_uses(state, 0, LVEYING) == 2

    _lveying_gain(engine, _frame_for(state, card=slash, targets=(1, 2, 3)))
    assert _markers(state) == 2, "额度用完 ⇒ 第三个目标也给不了标记"
    assert _phase_uses(state, 0, LVEYING) == 2


def test_gaining_merges_the_markers_into_one_pool_entry() -> None:
    """Q3 = A：两条技能都以掠影为来源 ⇒ 同名同来源合并成**一条**（`椎×2`，不是两条）。"""
    state = _state(seats=4)
    slash = _card(state, "slash")
    engine = Engine.from_state(state, seed=8)
    _lveying_gain(engine, _frame_for(state, card=slash, targets=(1, 2, 3)))
    entries = [marker for marker in state.player(0).markers if marker.name == MARKER]
    assert len(entries) == 1
    assert entries[0].count == 2
    assert entries[0].source_skill_id == LVEYING


def test_yingwu_counts_a_trick_that_targets_itself() -> None:
    """「指定一名角色」含自己 ⇒【无中生有】（目标是自己）照给 1 个标记。"""
    state = _state()
    ex_nihilo = _card(state, "ex_nihilo")
    engine = Engine.from_state(state, seed=9)
    assert _yingwu_target_condition(_Host(state), _frame_for(state, card=ex_nihilo, targets=(0,)))
    _yingwu_gain(engine, _frame_for(state, card=ex_nihilo, targets=(0,)))
    assert _markers(state) == 1


def test_yingwu_ignores_damage_tricks() -> None:
    """决斗 / 南蛮入侵 / 万箭齐发 / 火攻是**伤害类**普通锦囊 ⇒ ② 的条件为假。"""
    state = _state()
    for type_id in ("duel", "barbarian_invasion", "arrow_barrage"):
        card = _card(state, type_id)
        assert not _yingwu_target_condition(
            _Host(state), _frame_for(state, card=card, targets=(1,))
        ), type_id
    state_160 = _state_160()
    assert not _yingwu_target_condition(
        _Host(state_160),
        _frame_for(state_160, card=_card(state_160, "fire_attack"), targets=(1,)),
    ), "火攻"


def test_yingwu_needs_the_lveying_skill() -> None:
    """「若你拥有技能『掠影』」：失去掠影 ⇒ 不拥有（Q6 = A）。"""
    state = _state()
    ex_nihilo = _card(state, "ex_nihilo")
    engine = Engine.from_state(state, seed=11)
    engine.lose_skill(0, LVEYING)
    assert not _yingwu_target_condition(
        _Host(state), _frame_for(state, card=ex_nihilo, targets=(0,))
    )


def test_yingwu_still_gains_while_skills_are_suppressed() -> None:
    """技能**失效** ≠ 失去：失效仍算"拥有"（GLOSSARY「技能失效」）。"""
    state = _state()
    ex_nihilo = _card(state, "ex_nihilo")
    engine = Engine.from_state(state, seed=12)
    engine.suppress_skills(0, "hero.jie_machao.tieqi")
    assert _yingwu_target_condition(
        _Host(state), _frame_for(state, card=ex_nihilo, targets=(0,))
    ), "失效的掠影仍算「拥有」"


def test_markers_vanish_when_lveying_is_lost() -> None:
    """Q3 = A：「椎」的池挂在掠影上——失去掠影整池清空，失效不动标记。"""
    state = _state()
    slash = _card(state, "slash")
    engine = Engine.from_state(state, seed=13)
    _lveying_gain(engine, _frame_for(state, card=slash, targets=(1, 2)))
    assert _markers(state) == 2

    engine.suppress_skills(0, "hero.jie_machao.tieqi")
    assert _markers(state) == 2, "失效（无效）不动标记"

    state.player(0).suppressed_skills.clear()
    engine.lose_skill(0, LVEYING)
    assert _markers(state) == 0, "失去掠影 ⇒ 因它产生的标记全清"


def test_the_phase_counter_is_reset_by_the_play_phase_start() -> None:
    """Q7：每阶段计数住在 `_PHASE_USE_MASK` 位段，出牌阶段起点由既有复位点清掉。"""
    state = _state()
    for _ in range(2):
        _mark_phase_use(state, 0, LVEYING)
    assert _phase_uses(state, 0, LVEYING) == 2
    reset_phase_skill_state(state)
    assert _phase_uses(state, 0, LVEYING) == 0
    _mark_phase_use(state, 0, LVEYING)
    assert _phase_uses(state, 0, YINGWU) == 0, "按实例键分桶，两条技能互不串"


def test_the_phase_counter_lives_on_the_instance_key() -> None:
    """计数住 `skill_state[instance_key]`（与界顾雍移动版【慎行】同一段、同一复位点）。"""
    state = _state()
    _mark_phase_use(state, 0, YINGWU)
    keys = [
        instance_key(instance)
        for instance in resolve_instances(state, include_suppressed=True)
        if instance.owner == 0 and instance.skill_id == YINGWU
    ]
    assert len(keys) == 1
    assert state.skill_state.get(keys[0], 0) >> 32 == 1


# ---------------------------------------------------------------------------
# ① 的条件
# ---------------------------------------------------------------------------


def test_finish_needs_two_markers() -> None:
    """「椎」< 2 ⇒ 连条件都不成立（不发帧、不摸牌）。"""
    state = _state()
    slash = _card(state, "slash")
    host = _Host(state)
    assert not _lveying_finish_condition(host, _frame_for(state, card=slash, targets=(1,)))
    engine = Engine.from_state(state, seed=14)
    engine.add_marker(None, 0, name=MARKER, source_seat=0, source_skill_id=LVEYING, count=2)
    assert _lveying_finish_condition(host, _frame_for(state, card=slash, targets=(1,)))


def test_finish_is_not_limited_to_the_play_phase() -> None:
    """① 不锁阶段（OL 文本；移动版把「出牌阶段内」写在整条技能开头，见账本行 5）。"""
    state = _state(turn_seat=1, phase=Phase.DISCARD)
    engine = Engine.from_state(state, seed=15)
    engine.add_marker(None, 0, name=MARKER, source_seat=0, source_skill_id=LVEYING, count=2)
    host = _Host(state)
    assert _lveying_finish_condition(host, _frame_for(state, card=_card(state, "slash"), targets=(1,)))
    assert _yingwu_finish_condition(
        host, _frame_for(state, card=_card(state, "dismantlement"), targets=(1,))
    )


def test_yingwu_finish_fires_on_a_used_nullify() -> None:
    """【无懈可击】是普通锦囊（非伤害类）⇒ ① 触发；它不指定角色 ⇒ ② 永不因它触发。"""
    state = _state()
    nullify = _card(state, "nullify")
    engine = Engine.from_state(state, seed=16)
    engine.add_marker(None, 0, name=MARKER, source_seat=0, source_skill_id=LVEYING, count=2)
    host = _Host(state)
    assert _yingwu_finish_condition(host, _frame_for(state, card=nullify, targets=(1,)))
    assert not _yingwu_target_condition(
        host, _frame_for(state, card=nullify, targets=())
    ), "无懈的额定目标数是 0 ⇒ 它从不指定一名角色为目标"


# ---------------------------------------------------------------------------
# 端到端：① 的段机（移去 → 摸 → 问 → 选目标 → 视为使用）
# ---------------------------------------------------------------------------


class _Script:
    """最小脚本：按顺序打出指定的牌名，然后答 `SELECT_OPTION` / `SELECT_CARDS`。"""

    def __init__(
        self,
        *,
        plays: tuple[str, ...] = (),
        target: int = 1,
        accept: bool = True,
        slash_count: int = 0,
    ) -> None:
        self.plays = list(plays)
        self.target = target
        self.accept = accept
        self.slash_count = slash_count
        self.engine: Any = None
        self._armed = False
        self.options: list[str] = []

    @staticmethod
    def _passable(query: Any) -> Any:
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]

    def respond(self, query: Any) -> Any:
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "PLAY_PHASE":
            if self.slash_count and not self._armed:
                # 【杀】的次数在**出牌阶段起点**被复位，故摆局面只能在第一问这里补记。
                self._armed = True
                self.engine.state.player(0).card_use_counts["slash"] = self.slash_count
            for wanted in list(self.plays):
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == wanted:
                        self.plays.remove(wanted)
                        return action
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if kind == "SELECT_OPTION":
            skill_id = str(getattr(query, "skill_id", "") or "")
            self.options.append(skill_id)
            if skill_id.endswith(":target"):
                for action in query.legal_actions:
                    if isinstance(action, SelectOption) and action.choice == f"seat:{self.target}":
                        return action
                raise AssertionError(f"目标座位不在候选里：{query.legal_actions!r}")
            if self.accept:
                for action in query.legal_actions:
                    if isinstance(action, SelectOption):
                        return action
            return self._passable(query)
        if kind == "SELECT_CARDS":
            for action in query.legal_actions:
                if isinstance(action, SelectCards):
                    return action
            return self._passable(query)
        if kind == "RESPOND":
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.type == "slash":
                    return action
            return self._passable(query)
        return self._passable(query)


def _battle(
    *,
    plays: tuple[str, ...],
    target: int = 1,
    accept: bool = True,
    seed: int = 21,
    slash_count: int = 0,
):
    """两人局：座 0 = 谋刘赪（手里摆 `plays` 的牌），座 1 有两张手牌（可被拆）。"""
    hand = tuple(cards_of_type(PACK, type_id)[0] for type_id in plays)
    prey = cards_of_type(PACK, "dodge")[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=hand), SeatSpec(hand=tuple(prey))],
        deck=deck_excluding(PACK, *hand, *prey),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    if slash_count:
        state.player(0).card_use_counts["slash"] = slash_count
    engine = Engine.from_state(state, seed=seed)
    script = _Script(plays=plays, target=target, accept=accept, slash_count=slash_count)
    script.engine = engine
    players = [script, _Script()]

    def stop(e: Any) -> bool:
        return (
            e.pending is not None
            and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE"
            and bool(script.options)
        )

    run_queries(engine, players, stop)
    return engine, script


def test_a_trick_and_a_slash_pay_for_a_virtual_dismantlement() -> None:
    """端到端：先【无中生有】（莺舞② +1）再【杀】（掠影② +1）⇒ 掠影① 移去 2、摸 1、
    问一次"是否视为使用【过河拆桥】"、选目标、真的使用；那张虚拟【过河拆桥】结算结束后
    又触发莺舞②（新的非伤害类普通锦囊）⇒ 最终余 1 个标记。
    """
    engine, script = _battle(plays=("ex_nihilo", "slash"))
    state = engine.state
    assert [item.split(":")[-1] for item in script.options] == ["use", "target"]
    assert _markers(state) == 1, "无中生有 +1、杀 +1、掠影① 移去 2、虚拟拆桥 +1"
    assert _phase_uses(state, 0, YINGWU) == 2, "无中生有 1 次 + 虚拟【过河拆桥】1 次"
    assert _phase_uses(state, 0, LVEYING) == 1, "只有【杀】那一次（虚拟拆桥不是【杀】）"


def test_declining_the_virtual_dismantlement_keeps_the_spend_and_the_draw() -> None:
    """「可以」= 必含放弃出口：放弃 ⇒ 标记照扣、牌照摸，只是不视为使用。"""
    engine, script = _battle(plays=("ex_nihilo", "slash"), accept=False)
    state = engine.state
    assert script.options and script.options[0].endswith(":use")
    assert _markers(state) == 0, "放弃之后没有新的锦囊使用 ⇒ 不再加标记"
    assert _phase_uses(state, 0, YINGWU) == 1, "只有【无中生有】那一次"


def test_the_virtual_slash_does_not_consume_the_slash_count() -> None:
    """Q8：莺舞① 的产物【杀】**不占**本回合次数（`counts=False`），且用完上限也能用。

    局面：本阶段【杀】的次数已经用完（记账 1），两张非伤害类普通锦囊攒出两颗「椎」⇒
    莺舞① 出虚拟【杀】。若产物计次，`card_use_counts` 会变成 2。
    """
    engine, script = _battle(plays=("ex_nihilo", "dismantlement"), slash_count=1)
    state = engine.state
    assert [item.split(":")[-1] for item in script.options] == ["use", "target"]
    assert state.player(0).card_use_counts.get("slash") == 1, "产物【杀】不计次数"
    assert _markers(state) == 1, "虚拟【杀】又触发掠影②（+1）"
    assert _phase_uses(state, 0, LVEYING) == 1
