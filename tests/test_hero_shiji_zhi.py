"""始计篇-智包：神郭嘉（shiji-zhi 票 01；用户 2026-11-14 逐条签 Q1–Q13）。

四条技能各一组断言：

- 【慧识】判定过程连续：判定牌留处理区、花色重复才终止、**第一张不加上限**、全部获得
  （含最后那张）、交给后按"全场最多（含并列）"减上限；上限 10 时不可发动。
- 【辉逝】真支 = **立即**强制觉醒（框架票 05）、假支 = 摸四张、两支都减 2 上限、限定技整局一次。
- 【天翊】觉醒条件读**整局**伤害事实；+2 上限 / 回复 1 / 授予 ☆佐幸（可给自己）。
- ☆【佐幸】代价只认「神郭嘉」这个角色；「视为使用」= 无源纯虚拟牌；牌名闭集 + 置灰。

驱动纪律：脚本玩家只应答**被测那一步**（按 ``skill_id`` 的步键），其余走最小合法应答
（出牌阶段结束 / 放弃）；引擎开到"该技能从出牌阶段候选里消失"的那一刻为止（= 这次发动
已经收尾），故断言看到的是**出牌阶段结束时**的状态，不掺弃牌阶段。
"""

from __future__ import annotations

from sgs.data import RuleConfig, Suit, hero_skill_id, load_content_pack
from sgs.engine import Engine
from sgs.skills import awakening_skill_awakened, limited_skill_used
from sgs.state import (
    ActivateSkill,
    CardRef,
    EndPhase,
    FrameKind,
    GameState,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
)
from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")

HUISHI = hero_skill_id("shen_guojia", "huishi")
HUI_SHI = hero_skill_id("shen_guojia", "hui_shi")
TIANYI = hero_skill_id("shen_guojia", "tianyi")
ZUOXING = hero_skill_id("shen_guojia", "zuoxing")
QIANXUE = hero_skill_id("jie_xushu", "qianxue")
JIANYAN = hero_skill_id("jie_xushu", "jianyan")

SHEN_GUOJIA = "shen_guojia"


# ---------------------------------------------------------------------------
# 构造与驱动助手
# ---------------------------------------------------------------------------


def _of_suit(suit: Suit, count: int = 1) -> list[int]:
    """同花色的前 ``count`` 张牌（判定结果只看花色）。"""
    ids = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit is suit
    ]
    assert len(ids) >= count
    return ids[:count]


def _deck_with(*judged: int, exclude: tuple[int, ...] = ()) -> tuple[int, ...]:
    """牌堆：前 2 张是摸牌阶段的填充牌，接着依次是 ``judged``，其余接在后面。"""
    used = set(judged) | set(exclude)
    rest = [cid for cid in range(len(PACK.deck.cards)) if cid not in used]
    fillers = rest[:2]
    return (*fillers, *judged, *rest[2:])


def _step_key(skill_id: str) -> str:
    """询问 ``skill_id`` 的步键（与 ``projection._step_requirement`` 同一条取法）。"""
    _head, sep, rest = skill_id.partition(":")
    if not sep:
        return ""
    stem, colon, tail = rest.rpartition(":")
    return stem if colon and tail.isdigit() else rest


class Script:
    """按 ``{步键: 选择}`` 应答的脚本玩家；其余走最小合法应答。"""

    def __init__(self, answers: dict[str, str | None] | None = None) -> None:
        self.answers = answers or {}

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            action = self._activate(query)
            if action is not None:
                return action
            return next(
                item for item in query.legal_actions if isinstance(item, EndPhase)
            )
        if query.kind == "SELECT_OPTION":
            key = _step_key(str(getattr(query, "skill_id", "")))
            if key in self.answers:
                want = self.answers[key]
                if want is None:
                    return next(
                        item for item in query.legal_actions if isinstance(item, Pass)
                    )
                for item in query.legal_actions:
                    if isinstance(item, SelectOption) and item.choice == want:
                        return item
        if query.kind == "SELECT_CARDS":
            for item in query.legal_actions:
                if isinstance(item, SelectCards):
                    return item
        for item in query.legal_actions:
            if isinstance(item, Pass):
                return item
        return query.legal_actions[0]

    def _activate(self, query):
        wanted = self.answers.get("activate")
        if not wanted:
            return None
        for item in query.legal_actions:
            if isinstance(item, ActivateSkill) and item.skill_id == wanted:
                return item
        return None


def _seats(
    *,
    hero: str = SHEN_GUOJIA,
    hp: int = 3,
    hp_max: int = 3,
    alive: bool = True,
    hand: tuple[int, ...] = (),
    other_hero: str | None = None,
    other_hand: tuple[int, ...] = (),
) -> list[SeatSpec]:
    return [
        SeatSpec(hero=hero, hp=hp, hp_max=hp_max, alive=alive, hand=hand),
        SeatSpec(hero=other_hero, hp=4, hp_max=4, hand=other_hand),
    ]


def _state(
    seats: list[SeatSpec],
    *,
    deck: tuple[int, ...],
    phase: Phase = Phase.PLAY,
    turn_seat: int = 0,
) -> GameState:
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=2, first_seat=turn_seat),
    )


def _capture_virtual_uses(engine: Engine, captured: list[CardRef]) -> None:
    for frame in engine.stack.frames:
        if frame.kind is not FrameKind.USE:
            continue
        card = frame.data.get("card")
        if (
            card is not None
            and card.is_virtual
            and not card.virtual_sources
            and card not in captured
        ):
            captured.append(card)


def _drive_until_done(
    engine: Engine,
    skill_id: str,
    players: list[Script],
    *,
    captured: list[CardRef] | None = None,
    limit: int = 4_000,
) -> Engine:
    """推进到"该技能已从出牌阶段候选里消失"为止（= 这次发动收尾，弃牌阶段之前）。"""
    for _ in range(limit):
        if captured is not None:
            _capture_virtual_uses(engine, captured)
        query = engine.pending
        if (
            query is not None
            and query.kind == "PLAY_PHASE"
            and not any(
                isinstance(item, ActivateSkill) and item.skill_id == skill_id
                for item in query.legal_actions
            )
        ):
            return engine
        if query is None:
            if engine.finished:
                return engine
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有走完这次发动")


# ---------------------------------------------------------------------------
# 慧识
# ---------------------------------------------------------------------------


def test_huishi_first_card_does_not_raise_max_hp_and_duplicate_ends_the_flow():
    """第 1 张不加；第 2 张花色重复 ⇒ 不以「加1并重复」发问、直接终止。"""
    judged = _of_suit(Suit.HEART, 2)
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [Script({"activate": HUISHI, "repeat": "huishi_continue"}), Script()],
    )

    assert engine.state.player(0).hp_max == 3, "第一张判定牌不增加体力上限"
    assert len(engine.state.player(0).hand) == 4, "2 张摸牌 + 两张判定牌（含重复花色那张）"
    assert not engine.state.processing, "判定牌已离开处理区"


def test_huishi_new_suit_raises_max_hp_once_from_the_second_card():
    judged = (*_of_suit(Suit.HEART), *_of_suit(Suit.SPADE, 2))
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [
            Script(
                {
                    "activate": HUISHI,
                    "repeat": "huishi_continue",
                    "repeat-plus": "huishi_continue",  # 第 2 张花色新 ⇒ 加 1 并再判
                }
            ),
            Script(),
        ],
    )

    assert engine.state.player(0).hp_max == 4, "第 2 张花色新 ⇒ 加 1 点体力上限"
    assert len(engine.state.player(0).hand) == 5, "2 张摸牌 + 三张判定牌"


def test_huishi_gives_all_cards_and_only_then_checks_the_hand_count():
    """交给之后现算：收牌人手牌 6 张（全场最多）⇒ 神郭嘉 -1 上限。"""
    other_hand = tuple(_of_suit(Suit.CLUB, 4))
    deck = _deck_with(
        *_of_suit(Suit.HEART), *_of_suit(Suit.SPADE, 2), exclude=other_hand
    )
    state = _state(_seats(other_hand=other_hand, other_hero="guojia"), deck=deck)
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [
            Script(
                {
                    "activate": HUISHI,
                    "repeat": "huishi_continue",
                    "repeat-plus": "huishi_continue",
                    "give": "huishi_give",
                    "give-seat": "seat:1",
                }
            ),
            Script(),
        ],
    )

    assert engine.state.player(0).hp_max == 3, "3 + 1（第 2 张）- 1（收牌人最多）= 3"
    assert len(engine.state.player(0).hand) == 2, "只交出判定牌，摸来的两张还在手里"
    assert len(engine.state.player(1).hand) == 7, "收牌人 4 + 3 张判定牌"


def test_huishi_declining_the_give_keeps_the_cards_and_never_reduces():
    judged = _of_suit(Suit.HEART, 1)
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [Script({"activate": HUISHI, "repeat": None, "give": None}), Script()],
    )

    assert engine.state.player(0).hp_max == 3, "不交牌 ⇒ 不做「手牌最多」判定"
    assert len(engine.state.player(0).hand) == 3, "一张判定牌留在手里"
    assert engine.state.player(1).hand == []


def test_huishi_not_offered_at_max_hp_ten():
    state = _state(_seats(hp=10, hp_max=10), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, HUISHI, [Script({"activate": HUISHI}), Script()])

    assert engine.state.player(0).hp_max == 10
    assert not engine.state.processing, "上限 10 时技能不该出现"


# ---------------------------------------------------------------------------
# 辉逝
# ---------------------------------------------------------------------------


def test_huishi_limited_forces_an_untriggered_awakening_immediately():
    state = _state(_seats(other_hero="jie_xushu"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert awakening_skill_awakened(engine.state, 1, QIANXUE), "目标的觉醒技立即结算"
    assert engine.state.player(1).hp_max == 3, "【潜心】的上限 -1"
    assert JIANYAN in engine.state.player(1).granted_skills, "☆【荐言】照常授予"
    assert engine.state.player(0).hp_max == 1, "若如此做 ⇒ 神郭嘉减 2 上限（3 → 1）"


def test_huishi_draws_four_when_the_target_has_no_untriggered_awakening():
    state = _state(_seats(other_hero="guojia"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert len(engine.state.player(1).hand) == 4, "假支 = 其摸四张牌"
    assert engine.state.player(0).hp_max == 1, "两支都减 2 上限"


def test_huishi_is_a_limited_skill_once_per_game():
    state = _state(_seats(other_hero="guojia"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert limited_skill_used(engine.state, 0, HUI_SHI)


# ---------------------------------------------------------------------------
# 天翊
# ---------------------------------------------------------------------------


def _tianyi_state(*, damaged: bool = True) -> GameState:
    state = _state(_seats(other_hero="guojia"), deck=_deck_with(), phase=Phase.PREPARE)
    state.player(0).damage_taken_ever = damaged
    state.player(1).damage_taken_ever = damaged
    return state


def test_tianyi_awakens_only_when_every_living_seat_has_been_damaged():
    state = _tianyi_state(damaged=False)
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script(), Script()])

    assert engine.state.player(0).hp_max == 3, "有存活角色没受过伤 ⇒ 不触发"
    assert ZUOXING not in engine.state.player(0).granted_skills


def test_tianyi_adds_two_max_hp_recovers_one_and_grants_zuoxing():
    state = _tianyi_state()
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script({"grant-seat": "seat:1"}), Script()])

    assert engine.state.player(0).hp_max == 5, "加 2 点体力上限"
    assert engine.state.player(0).hp == 4, "回复 1 点体力（3 + 1）"
    assert ZUOXING in engine.state.player(1).granted_skills, "可以授予别的角色"
    assert awakening_skill_awakened(engine.state, 0, TIANYI)


def test_tianyi_can_grant_zuoxing_to_itself():
    state = _tianyi_state()
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script({"grant-seat": "seat:0"}), Script()])

    assert ZUOXING in engine.state.player(0).granted_skills


# ---------------------------------------------------------------------------
# ☆佐幸
# ---------------------------------------------------------------------------


def test_zuoxing_pays_guojia_max_hp_and_uses_a_source_free_virtual_trick():
    """持有者 = 神郭嘉本人；产物是**无源纯虚拟牌**（无色 / 无点 / 无属性）。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(0, ZUOXING)
    captured: list[CardRef] = []
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(
                {"activate": ZUOXING, "name": "过河拆桥", "target": "seat:1"}
            ),
            Script(),
        ],
        captured=captured,
    )

    assert engine.state.player(0).hp_max == 2, "神郭嘉 -1 点体力上限"
    assert captured, "「视为使用」产生一张无源纯虚拟牌"
    card = captured[0]
    assert card.type == "dismantlement"
    assert card.suit is None and card.rank is None and card.color is None
    assert card.virtual_sources == ()
    assert engine.state.player(1).hand == [], "【过河拆桥】弃掉了目标唯一的手牌"


def test_zuoxing_cost_always_lands_on_the_guojia_character():
    """佐幸由别人持有也一样：减的是「神郭嘉」这个角色的体力上限（Q11）。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(1, ZUOXING)
    captured: list[CardRef] = []
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(),
            Script({"activate": ZUOXING, "name": "过河拆桥", "target": "seat:0"}),
        ],
        captured=captured,
    )

    assert engine.state.player(0).hp_max == 2, "持有者不是神郭嘉，代价仍落在神郭嘉身上"
    assert engine.state.player(1).hp_max == 4, "持有者自己的体力上限不动"


def test_zuoxing_not_offered_when_guojia_is_dead_or_at_one_max_hp():
    for alive, hp, hp_max in ((False, 3, 3), (True, 1, 1)):
        other_hand = (_of_suit(Suit.CLUB)[0],)
        state = _state(
            _seats(
                hp=hp,
                hp_max=hp_max,
                alive=alive,
                other_hero="guojia",
                other_hand=other_hand,
            ),
            deck=_deck_with(exclude=other_hand),
            turn_seat=1,
        )
        engine = Engine.from_state(state, seed=7)
        engine.grant_skill(1, ZUOXING)
        _drive_until_done(engine, ZUOXING, [Script(), Script({"activate": ZUOXING})])

        assert not [
            record
            for record in engine.records
            if record.kind == "query"
            and any(
                isinstance(item, ActivateSkill) and item.skill_id == ZUOXING
                for item in getattr(record.query, "legal_actions", ())
            )
        ], f"前置不成立（alive={alive}, hp_max={hp_max}）时不得出现候选"


def test_zuoxing_name_list_is_the_normal_tricks_and_disables_unusable_ones():
    """牌名闭集 = 本牌堆普通锦囊（排除延时）；【无懈可击】此刻用不了 ⇒ 列出来但置灰。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(1, ZUOXING)
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(),
            Script({"activate": ZUOXING, "name": "过河拆桥", "target": "seat:0"}),
        ],
    )
    queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and _step_key(record.query.skill_id) == "name"
    ]
    assert queries, "应发出牌名询问"
    query = queries[0]
    assert "过河拆桥" in query.options
    assert "乐不思蜀" not in query.options, "延时锦囊不在普通锦囊闭集里"
    assert "无懈可击" in query.options
    assert "无懈可击" in query.disabled, "出牌阶段没有使用时机 ⇒ 置灰"
