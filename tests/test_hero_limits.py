"""P 族：限制豁免（M6 票 02）——【咆哮】无次数限制 / 【奇才】无距离限制。

规则原文：§十七 L1656–1711（17.2 "无次数限制" / 17.3 "无距离限制"）与 §九 L995 / L1014。
两条都是**锁定技**（没有"是否发动"询问），落点全部是外部缝：

- ``check_count``（次数闸门）与 ``legal_actions_for``（候选与闸门同源）；
- ``check_targets``（距离是目标池第 0 层的条件）；
- 真引擎的一整段出牌阶段（【咆哮】同回合连用两张【杀】）。

【咆哮】的判据是【杀】**家族**（:func:`sgs.effects.is_slash`），与【诸葛连弩】同一条链；
【奇才】的判据是**锦囊牌**（数据文件的牌名大类），且只豁免距离条件、**不**动攻击范围
——"无距离限制"是条件豁免而不是数值修正（§十七 L1699–1708）。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.effects import check_count, check_targets, legal_actions_for
from sgs.engine import Engine
from sgs.state import EndPhase, Pass, Phase, QueryKind, UseCard
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, ran_timing, run_until
from test_skills import _deck_excluding

PACK = load_content_pack(deck_profile="standard_108")
PACK_EX = load_content_pack(deck_profile="standard_ex_160")
"""军争牌（【酒】/【兵粮寸断】）只在 160 档里，故这两条用例显式换档（档位纪律）。"""


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _cards_ex(type_id: str) -> list[int]:
    return cards_of_type(PACK_EX, type_id)


def _deck_without(pack, *card_ids: int) -> tuple[int, ...]:
    excluded = set(card_ids)
    return tuple(index for index in range(len(pack.deck.cards)) if index not in excluded)


def _play_candidates(state, seat: int = 0):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _uses_of(actions, card_id: int) -> list[UseCard]:
    return [
        action
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id == card_id
    ]


# ---------------------------------------------------------------------------
# 张飞【咆哮】：你于出牌阶段内使用【杀】无次数限制（§九 L995；§十七 17.2 ④）
# ---------------------------------------------------------------------------


def _slash_state(
    *, hero: str | None, used: int, turn_seat: int = 0, phase=Phase.PLAY
) -> object:
    """两张【杀】在手；``used`` = 座位 0 已用【杀】数，``turn_seat`` / ``phase`` = 谁的什么阶段。"""
    first, second = _cards("slash")[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), hero=hero), SeatSpec()],
        deck=_deck_excluding(first, second),
        turn_seat=turn_seat,
        phase=phase,
    )
    state.player(0).card_use_counts["slash"] = used
    return state


def test_paoxiao_lifts_the_slash_count_limit_after_the_first_slash():
    """正例：已用一张【杀】后，张飞的【杀】仍过闸门、仍进候选。"""
    state = _slash_state(hero="zhangfei", used=1)
    first, second = state.player(0).hand

    assert not check_count(state, 0, state.card(first)), "咆哮：不受【杀】次数上限约束"
    actions = _play_candidates(state)
    assert _uses_of(actions, first) and _uses_of(actions, second), (
        "两张【杀】都该在候选里"
    )


def test_without_paoxiao_the_slash_limit_still_bites():
    """反例：同局面去掉【咆哮】⇒ 已用一张后第二张【杀】过不了闸门、进不了候选。"""
    state = _slash_state(hero=None, used=1)
    first = state.player(0).hand[0]

    assert check_count(state, 0, state.card(first)), "无技能 ⇒ 额定每回合 1 张"
    assert not _uses_of(_play_candidates(state), first)


def test_paoxiao_does_not_lift_the_wine_limit():
    """族判据只认【杀】：【酒】的每回合一次（``wine`` 计数键）不受【咆哮】影响。"""
    wine = _cards_ex("wine")[0]
    state = build_state(
        pack=PACK_EX,
        seats=[SeatSpec(hand=(wine,), hero="zhangfei"), SeatSpec()],
        deck=_deck_without(PACK_EX, wine),
        turn_seat=0,
    )
    state.player(0).card_use_counts["wine"] = 1

    assert check_count(state, 0, state.card(wine)), "咆哮不豁免【酒】的每回合一次"


class _SlashSpam:
    """出牌阶段能用【杀】就用（目标座位 1），否则结束阶段；响应一律放弃。"""

    def __init__(self) -> None:
        self.used: list[int] = []

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type == "slash":
                    self.used.append(action.card.card_id)
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def test_paoxiao_lets_zhangfei_use_two_slashes_in_one_play_phase():
    """整局链：同一出牌阶段真的用掉两张【杀】，各造成 1 点（计数落在 ``slash`` 键）。"""
    first, second = _cards("slash")[:2]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(first, second), hero="zhangfei"),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SlashSpam()

    run_until(
        engine, [player, PassPlayer()], lambda e: ran_timing(e, Timing.PHASE_DISCARD_START)
    )

    assert player.used == [first, second], "同一出牌阶段依手牌序各用一次"
    assert engine.state.player(1).hp == 2, "两张【杀】都命中"
    assert engine.state.player(0).card_use_counts.get("slash") == 2


def test_without_paoxiao_the_second_slash_never_becomes_a_candidate():
    """反例的整局链：无【咆哮】时同一脚本只用得出一张，第二张留在手里。"""
    first, second = _cards("slash")[:2]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second)), SeatSpec(hp=4, hp_max=4)],
        deck=_deck_excluding(first, second),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SlashSpam()

    run_until(
        engine, [player, PassPlayer()], lambda e: ran_timing(e, Timing.PHASE_DISCARD_START)
    )

    assert player.used == [first], "额度已满 ⇒ 第二张【杀】不进候选"
    assert engine.state.player(1).hp == 3
    assert engine.state.player(0).card_use_counts.get("slash") == 1
    assert second in engine.state.player(0).hand, "没用出去的【杀】还在手里"


# ---------------------------------------------------------------------------
# 次数限制的**适用范围**（W5-04）：只在"他自己的出牌阶段"读计数
# ---------------------------------------------------------------------------


def test_the_slash_count_binds_only_the_owners_own_play_phase():
    """「每回合限一次」只约束**他自己出牌阶段**的主动使用（W5-04 用户裁定 2026-10-30）。

    §3.5 L183–L185 / §17.1 L1659 的"每回合限一次"在裁定下收窄：响应、其他阶段、其他角色
    回合里的【杀】使用**不计次数、也不受次数限制**。计数键仍只在该座位自己的 ``TURN_START``
    重置；陈旧计数只是不再被错误的窗口读到（W5-04 的实机根因）。
    """
    slash = _cards("slash")[0]
    own_play = _slash_state(hero=None, used=1, turn_seat=0, phase=Phase.PLAY)
    other_play = _slash_state(hero=None, used=1, turn_seat=1, phase=Phase.PLAY)
    own_judge = _slash_state(hero=None, used=1, turn_seat=0, phase=Phase.JUDGE)

    assert check_count(own_play, 0, own_play.card(slash)), (
        "自己的出牌阶段：已用 1 张 ⇒ 第二张不能再出"
    )
    assert not check_count(other_play, 0, other_play.card(slash)), (
        "别人的回合：即便计数键上还留着上一回合的 1，也不查次数"
    )
    assert not check_count(own_judge, 0, own_judge.card(slash)), (
        "自己的回合但不在出牌阶段：响应 / 技能驱动的使用同样不查次数"
    )


def test_narrowing_the_slash_limit_leaves_other_count_keys_alone():
    """只收窄【杀】族：【酒】的每回合一次照旧读计数（包括在别人的回合里）。"""
    wine = _cards_ex("wine")[0]
    state = build_state(
        pack=PACK_EX,
        seats=[SeatSpec(hand=(wine,)), SeatSpec()],
        deck=_deck_without(PACK_EX, wine),
        turn_seat=1,
        phase=Phase.PLAY,
    )
    state.player(0).card_use_counts["wine"] = 1

    assert check_count(state, 0, state.card(wine)), (
        "【酒】的计数键不是【杀】族，语义不动：已用一次 ⇒ 仍受限"
    )


# ---------------------------------------------------------------------------
# 黄月英【奇才】：你使用锦囊牌无距离限制（§九 L1014）
# ---------------------------------------------------------------------------


def _snatch_state(*, hero: str | None) -> object:
    """【顺手牵羊】在手、座位 2 在距离 2（4 人局：0→2 = 2，0→1 / 0→3 = 1）。

    【顺手牵羊】另有"目标区域里有牌"的第 0 层过滤，故每个候选座位都放一张手牌。
    """
    snatch = _cards("snatch")[0]
    hand = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(snatch,), hero=hero),
            SeatSpec(hand=(hand,)),
            SeatSpec(hand=(hand + 1,)),
            SeatSpec(hand=(hand + 2,)),
        ],
        deck=_deck_excluding(snatch, hand, hand + 1, hand + 2),
        turn_seat=0,
    )
    return state


def test_qicai_lets_a_trick_reach_a_target_beyond_its_distance_limit():
    """正例：【顺手牵羊】额定距离上限 1；【奇才】把它豁免到"任意其他角色"。"""
    state = _snatch_state(hero="huangyueying")
    snatch = state.player(0).hand[0]

    assert check_targets(state, 0, state.card(snatch)) == (1, 2, 3), (
        "奇才：距离不再是目标条件（1 与 2 都在池里）"
    )
    actions = _play_candidates(state)
    assert {target for action in _uses_of(actions, snatch) for target in action.targets} == {
        1,
        2,
        3,
    }


def test_without_qicai_the_trick_distance_limit_still_bites():
    """反例：同局面去掉【奇才】⇒ 距离 2 的座位不在池里。"""
    state = _snatch_state(hero=None)
    snatch = state.player(0).hand[0]

    assert check_targets(state, 0, state.card(snatch)) == (1, 3), (
        "无技能 ⇒ 只能选距离 1 内的座位"
    )


def test_qicai_also_lifts_the_delayed_trick_distance_limit():
    """【兵粮寸断】同是"距离 1 以内"的锦囊 ⇒ 同一个挂点（不是按牌名白名单）。"""
    shortage = _cards_ex("supply_shortage")[0]
    state = build_state(
        pack=PACK_EX,
        seats=[
            SeatSpec(hand=(shortage,), hero="huangyueying"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=_deck_without(PACK_EX, shortage),
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(shortage)) == (1, 2, 3)


def test_qicai_does_not_widen_the_slash_attack_range():
    """反例：奇才只豁免锦囊的距离条件，【杀】仍按攻击范围（1）取目标。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,), hero="huangyueying"), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(slash),
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == (1, 3), (
        "【杀】的距离条件不受奇才影响（邻居 1 / 3；距离 2 的座位够不到）"
    )
