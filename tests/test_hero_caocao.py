"""自制包·韬·曹操自制版：【鸿志】/【倡义】的构造局面单测。

两条技能各走一种"既有原语的组合"（框架侧只补了两处，见票 `02` / `03`）：

- 【鸿志】= 锁定技的**两条**帧外时机钩子（``TURN_START`` + ``TURN_END``）+ 手写
  「摸至 / 弃置至」（样板是【镇骨】与【节命】）；
- 【倡义】= 帧外时机钩子（``PHASE_PLAY_END``）+ 手写段机（拼点 → 段 3 授予 → 段 4 二选一），
  外加同一技能的第二条钩子（``RANK_COMPARE_PLACED`` = 扣置后、亮出前）做「点数 +X」。

判据一律走外部缝：候选集合、询问载荷（``spec`` / ``disabled`` / ``option_texts``）、
记录流里的时机与 ``pindian`` delta、技能实例集合。不读回调内部结构。
"""

from __future__ import annotations

import pytest

from sgs.data import RuleConfig, load_content_pack, load_heroes
from sgs.engine import Engine
from sgs.skills import round_skill_uses
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
    Zone,
)
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, ran_timing, run_queries

PACK = load_content_pack(deck_profile="standard_108")

CAOCAO = "tao_caocao"
HONGZHI = "hero.tao_caocao.hongzhi"
CHANGYI = "hero.tao_caocao.changyi"

PASS = PassPlayer()


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _rank(card_id: int) -> int:
    return int(PACK.deck.cards[card_id].rank)


def _by_rank(rank: int, *exclude: int) -> int:
    """牌堆里第一张该点数的牌（避开 ``exclude``）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        if _rank(card_id) == rank:
            return card_id
    raise AssertionError(f"牌堆里没有点数 {rank} 的牌")


def _seat(hero: str | None = None, hand: tuple[int, ...] = (), **kwargs) -> SeatSpec:
    return SeatSpec(hero=hero, hand=hand, **kwargs)


# ---------------------------------------------------------------------------
# §1 【鸿志】：两个时点 + 「摸到 / 弃到 min(体力上限, 5)」
# ---------------------------------------------------------------------------


def _hongzhi_engine(*, hand: tuple[int, ...], turn_seat: int = 0, seats: int = 3) -> Engine:
    specs = [_seat(CAOCAO, hand)] + [_seat() for _ in range(seats - 1)]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=_deck_excluding(*hand),
        turn_seat=turn_seat,
        # ``_start`` 会按先手座位覆写 ``turn_seat``（规则 §3.9 L229–L233）⇒ 构造局面时
        # 必须同时给先手位，否则第一个回合永远是 0 号位。
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    return Engine.from_state(state, seed=31)


def test_hongzhi_refills_an_empty_hand_at_turn_start():
    """回合开始时无手牌 ⇒ 摸到 min(体力上限, 5) = 4。"""
    engine = _hongzhi_engine(hand=())

    run_queries(engine, [PASS, PASS, PASS], lambda state: ran_timing(state, Timing.TURN_START))

    assert len(engine.state.player(0).hand) == 4, "无手牌 ⇒ 摸到体力上限（4）"


def test_hongzhi_leaves_a_non_empty_hand_alone_at_turn_start():
    """有手牌 ⇒ ① 的条件不成立，连摸牌都不发生。"""
    card = _cards("slash")[0]
    engine = _hongzhi_engine(hand=(card,))

    run_queries(engine, [PASS, PASS, PASS], lambda state: ran_timing(state, Timing.TURN_START))

    assert engine.state.player(0).hand == [card], "有手牌 ⇒ 这一段不跑"


def test_hongzhi_tops_up_at_turn_end_only_when_changyi_is_off_the_field():
    """回合结束时场上无【倡义】⇒ 摸到手牌数 4（这里 1 + 摸牌 2 = 3 ⇒ 再摸 1）。"""
    card = _cards("slash")[0]
    engine = _hongzhi_engine(hand=(card,))
    engine.lose_skill(0, CHANGYI)  # 构造局面：曹操已经不在场上持有【倡义】

    run_queries(engine, [PASS, PASS, PASS], lambda state: ran_timing(state, Timing.TURN_END))

    assert len(engine.state.player(0).hand) == 4, "3 张 ⇒ 摸到 4"


def test_hongzhi_is_silent_at_turn_end_while_changyi_is_still_on_the_field():
    """场上仍有【倡义】（曹操自己持有）⇒ ② 的条件不成立。"""
    card = _cards("slash")[0]
    engine = _hongzhi_engine(hand=(card,))

    run_queries(engine, [PASS, PASS, PASS], lambda state: ran_timing(state, Timing.TURN_END))

    assert len(engine.state.player(0).hand) == 3, "1 + 摸牌阶段 2 = 3，回合末不再调整"


def test_hongzhi_discards_down_to_the_cap_after_the_round_ends():
    """回合末手牌超出 ⇒ **弃到** 4（双向调整），弃哪几张由曹操自己选。

    构造：出牌阶段结束后（弃牌阶段已把牌压到上限）再给他塞两张——正常情况下手牌不会在
    回合末超过上限，这里要测的是"超出那一半"本身。
    """
    card = _cards("slash")[0]
    extra = _cards("dodge")[:2]
    engine = _hongzhi_engine(hand=(card,))
    engine.lose_skill(0, CHANGYI)
    players = [PASS, PASS, PASS]

    run_queries(engine, players, lambda state: state.state.phase is Phase.FINISH)
    top = tuple(engine.state.deck[: len(extra)])
    del engine.state.deck[: len(extra)]
    engine.state.player(0).hand.extend(top)
    assert len(engine.state.player(0).hand) == 5, "3 + 塞进去的 2"

    # 弃置那一问会挂起 ⇒ 停止条件不能只看"时机记录出现过"（记录先落、钩子后跑）。
    run_queries(engine, players, lambda state: state.state.turn_seat != 0)

    assert len(engine.state.player(0).hand) == 4, "超出 1 张 ⇒ 弃到 4"
    assert len(engine.state.discard) == 1, "弃掉的那张进弃牌堆"


# ---------------------------------------------------------------------------
# §2 【倡义】：出牌阶段结束时 → 拼点 → 授予 / 二选一
# ---------------------------------------------------------------------------


class _Driver:
    """按脚本应答【倡义】的整条链（座位可配，故曹操与对手各用一个实例）。

    - ``SKILL_ACTIVATION``：发动 / 放弃；
    - ``SELECT_CARDS``：``total`` 上界为 ``None`` 的是"弃至少 X 张"，取前 ``discard`` 张；
      否则是拼点选牌，挑 ``pick_rank`` 点数的牌；
    - ``SELECT_OPTION``：按 ``skill_id`` 的后缀分派（``boost`` / ``grant`` / ``choice`` /
      ``target``）。
    """

    def __init__(
        self,
        *,
        seat: int = 0,
        activate: bool = True,
        boost: bool = False,
        grant: bool = False,
        choice: str = "changyi:punish",
        target: int = 2,
        pick_rank: int | None = None,
        discard: int = 0,
    ) -> None:
        self.seat = seat
        self.activate = activate
        self.boost = boost
        self.grant = grant
        self.choice = choice
        self.target = target
        self.pick_rank = pick_rank
        self.discard = discard
        self.activations: list = []
        self.selections: list = []
        self.options: list = []
        self.option_texts: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == CHANGYI:
            self.activations.append(query)
            if not self.activate:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            self.selections.append(query)
            return self._cards_answer(query)
        if query.kind == "SELECT_OPTION":
            self.options.append(query)
            self.option_texts.append(dict(query.option_texts))
            return self._option_answer(query)
        if query.kind == "PLAY_PHASE" and query.actor == self.seat:
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]

    def _cards_answer(self, query):
        low, _high = query.spec.total
        if Zone.EQUIP in query.spec.zones:
            wanted = max(int(low), self.discard)
            for action in query.legal_actions:
                if isinstance(action, SelectCards) and len(action.picks) == wanted:
                    return action
        for action in query.legal_actions:
            if not isinstance(action, SelectCards) or len(action.picks) != 1:
                continue
            slot = query.slots[action.picks[0]]
            card = getattr(slot, "card", None)
            if card is None:
                continue
            if self.pick_rank is None or card.rank == self.pick_rank:
                return action
        return query.legal_actions[0]

    def _option_answer(self, query):
        suffix = str(query.skill_id).rsplit(":", 1)[-1]
        if suffix == "boost":
            return self._option(query, "changyi:boost" if self.boost else None)
        if suffix == "grant":
            return self._option(query, "changyi:grant" if self.grant else None)
        if suffix == "choice":
            return self._option(query, self.choice)
        if suffix == "target":
            return self._option(query, f"seat:{self.target}")
        return query.legal_actions[0]

    def _option(self, query, wanted: str | None):
        if wanted is None:
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        target = next(
            (
                a
                for a in query.legal_actions
                if isinstance(a, SelectOption) and a.choice == wanted
            ),
            None,
        )
        if target is None:
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        return target


def _changyi_engine(
    *,
    hand0: tuple[int, ...],
    hand1: tuple[int, ...] = (),
    hand2: tuple[int, ...] = (),
    seats: int = 3,
    turn_seat: int = 1,
) -> Engine:
    specs = [_seat(CAOCAO, hand0), _seat(hand=hand1)]
    if seats > 2:
        specs.append(_seat(hand=hand2))
    specs.extend(_seat() for _ in range(3, seats))
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=_deck_excluding(*hand0, *hand1, *hand2),
        turn_seat=turn_seat,
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    return Engine.from_state(state, seed=17)


def _pindian_delta(engine):
    return next(
        record for record in engine.records if getattr(record, "delta_type", None) == "pindian"
    )


def _settled(engine) -> bool:
    """整条【倡义】跑完（收尾会落一次每轮计数，或技能已失去）。"""
    return ran_timing(engine, Timing.PHASE_PLAY_END) and (
        round_skill_uses(engine.state, 0, CHANGYI) >= 1
        or CHANGYI not in [instance.skill_id for instance in _instances(engine, 0)]
    )


def _instances(engine, seat: int):
    from sgs.skills import resolve_instances

    return [instance for instance in resolve_instances(engine.state) if instance.owner == seat]


def test_changyi_fires_at_the_end_of_another_players_play_phase():
    """座位 1 的出牌阶段结束时问一次「是否发动」（座位 0 = 曹操）。"""
    engine = _changyi_engine(hand0=(_by_rank(9),), hand1=(_by_rank(4),))
    caocao = _Driver(activate=False)

    run_queries(engine, [caocao, PASS, PASS], lambda state: bool(caocao.activations))

    assert len(caocao.activations) == 1
    assert caocao.activations[0].skill_id == CHANGYI
    assert ran_timing(engine, Timing.PHASE_PLAY_END), "触发点 = 出牌阶段结束时"


def test_changyi_is_not_offered_without_a_hand_on_either_side():
    """对手没有手牌 ⇒ 拼点不成立（规则 12.1 L1316）⇒ 连问都不问。"""
    engine = _changyi_engine(hand0=(_by_rank(9),), hand1=())
    caocao = _Driver()

    run_queries(engine, [caocao, PASS, PASS], lambda state: ran_timing(state, Timing.PHASE_PLAY_END))

    assert caocao.activations == []


def test_changyi_grants_a_copy_of_the_skill_when_the_opponent_did_not_win():
    """曹操赢（K vs 2）⇒「其没赢」⇒ 可以令其获得【倡义】（复制，曹操保留自己的）。"""
    engine = _changyi_engine(hand0=(_by_rank(13),), hand1=(_by_rank(2),))
    caocao = _Driver(pick_rank=13, grant=True)

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=2), PASS], _settled)

    assert CHANGYI in engine.state.player(1).granted_skills, "对手获得一份【倡义】"
    assert CHANGYI in [instance.skill_id for instance in _instances(engine, 0)], "给予者保留"
    grant_texts = next(
        query.option_texts
        for query in caocao.options
        if str(query.skill_id).endswith(":grant")
    )
    assert grant_texts == {"changyi:grant": "令其获得【倡义】"}


def test_changyi_tie_is_not_winning_on_either_side():
    """平点 ⇒ 段 3（授予）与段 4（二选一）**都**出现。"""
    engine = _changyi_engine(hand0=(_by_rank(5),), hand1=(_by_rank(5),))
    caocao = _Driver(pick_rank=5, grant=True, choice="changyi:draw")

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=5), PASS], _settled)

    suffixes = [
        str(query.skill_id).rsplit(":", 1)[-1]
        for query in caocao.options
        if not str(query.skill_id).endswith(":boost")
    ]
    assert suffixes[:2] == ["grant", "choice"], "先授予、后二选一"
    assert _pindian_delta(engine).tied == (True,), "平点事实回写进了 delta"


def test_changyi_punish_branch_discards_x_then_deals_one_damage():
    """输了 ⇒ 选项 1：弃至少 X 张（3 人局 X = 2）→ 对一名没有【倡义】的角色造成 1 点伤害。"""
    engine = _changyi_engine(
        hand0=(_by_rank(5), _by_rank(3), _by_rank(7), _by_rank(8)),
        hand1=(_by_rank(9),),
    )
    caocao = _Driver(pick_rank=5, choice="changyi:punish", discard=2, target=2)

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=9), PASS], _settled)

    discards = [query for query in caocao.selections if Zone.EQUIP in query.spec.zones]
    assert [query.spec.total for query in discards] == [(2, 3)], "「至少 X 张」= lo=X、上界=此刻可弃的全部"
    assert len(engine.state.discard) >= 2, "弃掉的牌进弃牌堆"
    assert engine.state.player(2).hp == 3, "目标挨了 1 点伤害"


def test_changyi_punish_is_disabled_when_it_cannot_pay():
    """拼点用掉唯一一张手牌 ⇒ 付不起 X=2 ⇒ 选项 1 置灰、只剩选项 2。"""
    engine = _changyi_engine(hand0=(_by_rank(5),), hand1=(_by_rank(9),))
    caocao = _Driver(pick_rank=5, choice="changyi:draw")

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=9), PASS], _settled)

    choice_query = next(
        query for query in caocao.options if str(query.skill_id).endswith(":choice")
    )
    assert choice_query.disabled == ("changyi:punish",), "付不起 ⇒ 置灰"
    assert {
        action.choice for action in choice_query.legal_actions if isinstance(action, SelectOption)
    } == {"changyi:draw"}, "可提交的只剩选项 2"


def test_changyi_draw_branch_draws_x_and_loses_the_skill():
    """输了 ⇒ 选项 2：摸 X 张（3 人局 X = 2）→ 失去【倡义】（不可恢复）。"""
    engine = _changyi_engine(hand0=(_by_rank(5),), hand1=(_by_rank(9),))
    caocao = _Driver(pick_rank=5, choice="changyi:draw")

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=9), PASS], _settled)

    assert len(engine.state.player(0).hand) == 2, "拼点牌用掉 1 张，再摸 2 张"
    assert CHANGYI not in [instance.skill_id for instance in _instances(engine, 0)], "技能已失去"


def test_changyi_x_is_recounted_after_the_grant():
    """X 每次取值现算：平点授予之后，二选一那一格的 X 少 1（2 → 1）。"""
    engine = _changyi_engine(hand0=(_by_rank(5), _by_rank(3)), hand1=(_by_rank(5),))
    caocao = _Driver(pick_rank=5, grant=True, choice="changyi:draw")

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=5), PASS], _settled)

    texts = next(
        query.option_texts
        for query in caocao.options
        if str(query.skill_id).endswith(":choice")
    )
    assert texts["changyi:draw"] == "摸1张牌，然后你失去此技能", "授予后 X 从 2 降到 1"


def test_changyi_point_boost_flips_a_losing_pindian():
    """5 点 vs 6 点本来会输；「点数 +X」（X = 2）⇒ 7 > 6 ⇒ 赢。"""
    engine = _changyi_engine(hand0=(_by_rank(5),), hand1=(_by_rank(6),))
    caocao = _Driver(pick_rank=5, boost=True)

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=6), PASS], _settled)

    delta = _pindian_delta(engine)
    assert delta.ranks == (7, 6), "生效点数 = 牌面 + X"
    assert delta.won == (True,), "加点之后赢"
    assert delta.cards[0].rank == 5, "牌面点数不动"


def test_changyi_point_boost_can_be_declined():
    """放弃加点 ⇒ 生效点数 = 牌面点数，5 输给 6。"""
    engine = _changyi_engine(hand0=(_by_rank(5), _by_rank(3), _by_rank(7)), hand1=(_by_rank(6),))
    caocao = _Driver(pick_rank=5, boost=False, choice="changyi:draw")

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=6), PASS], _settled)

    delta = _pindian_delta(engine)
    assert delta.ranks == (5, 6)
    assert delta.won == (False,)


def test_changyi_is_limited_to_once_per_round():
    """成功发动一次之后，本轮同一技能的第二次机会不再出现。"""
    engine = _changyi_engine(
        hand0=(_by_rank(5), _by_rank(3), _by_rank(7)),
        hand1=(_by_rank(9),),
        hand2=(_by_rank(4),),
    )
    caocao = _Driver(pick_rank=5, choice="changyi:punish", discard=2, target=2)

    run_queries(engine, [caocao, _Driver(seat=1, pick_rank=9), PASS], _settled)
    assert round_skill_uses(engine.state, 0, CHANGYI) == 1

    # 座位 2 的下一个回合照样有"出牌阶段结束时"，但本轮次数已经用完。
    run_queries(engine, [caocao, PASS, PASS], lambda state: ran_timing(state, Timing.TURN_END))
    assert len(caocao.activations) == 1, "本轮只发动一次"


# ---------------------------------------------------------------------------
# §3 数据面
# ---------------------------------------------------------------------------


def test_the_hero_sheet_is_registered_in_the_homebrew_table():
    """武将表 + 描述：显示名 / 真名 / 群 / 4 体力 / 两条技能。"""
    hero = load_heroes("homebrew").by_id(CAOCAO)
    assert hero is not None
    assert hero.name == "韬·曹操自制版"
    assert hero.true_name == "曹操"
    assert hero.kingdom == "qun"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["hongzhi", "changyi"]
    assert hero.enabled

    import json
    from pathlib import Path

    descriptions = json.loads(
        (Path(__file__).resolve().parent.parent / "tools" / "sgs_web" / "descriptions.json")
        .read_text(encoding="utf-8")
    )["skills"]
    assert "hero.tao_caocao.hongzhi" in descriptions
    assert "hero.tao_caocao.changyi" in descriptions
