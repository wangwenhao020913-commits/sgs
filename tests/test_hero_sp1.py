"""SP武将组1·马良：【自书】/【应援】（sp1-sunshangxiang-maliang 票 01；用户 2026-11-18 签）。

**【自书】一组**（锁定技：三条钩子，零询问）：

- ① 回合外（其他角色的回合）**获得牌**（`gains_card` 判据：落点是自己手牌、来源不限）
  ⇒ 给这次获得的**每一张**牌打「自书」标记（`CardTagType.ZISHU` 牌面标签）；
- ① 结算点 = **`TURN_END`**（用户原话"当前角色回合结束时"）+「回合角色存活 ∧ ≠ 我」守卫：
  把**我手里带标记的牌**全部置入弃牌堆；回合角色阵亡 ⇒ **不弃置**；
- 标记寿命两条：**离开手牌即清**（`LEAVE_ZONE`，载体自己在 `MOVE_AFTER` 里调
  `clear_card_tags`）+ **回合开始时清除**（`TURN_START` 簿记钩子）；
- ② 自己的回合内**获得牌**（来源不限）⇒ 摸一张（单独一次摸牌；一次获得 N 张仍只摸 1 张）；
  自书自己摸的那一张不再触发自己。

**【应援】一组**（触发技，钩子 + 一步选座位）：

- 触发点 = `MOVE_AFTER`（`reason=USE` ∧ 落点弃牌堆 ∧ 就是本次使用的那张）——移动版
  「置入弃牌堆**后**」的落点；装备牌（留装备区）/ 延时锦囊（进判定区）天然不触发；
- 效果 = 把**那张牌**从弃牌堆交给一名其他角色；**每种牌名每回合限一次**（`yingyuan_names`），
  **发动（提交）即占位、放弃不占位**；
- 牌名取**产物牌名**；转化牌交出的是它进弃牌堆的那批**来源实体牌**。

判据一律走外部缝：牌标签状态、`tag_add` / `card_move` 记录、询问载荷、`legal_actions`。
"""

from __future__ import annotations

from sgs.data import CardTagType, load_content_pack, hero_skill_id
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.hero_sp1 import FANXIANG, LIANGZHU, XIAOJI_SP, ZISHU, YINGYUAN
from sgs.state import (
    ActivateSkill,
    CardTag,
    EndPhase,
    MoveReason,
    Pass,
    Phase,
    SelectOption,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, run_queries, run_turns

PACK = load_content_pack(deck_profile="standard_108")

MALIANG = "maliang"
ZISHU_TAG = CardTagType.ZISHU

# 座位约定：0 = 另一个角色（回合角色），1 = 马良，2 = 第三个角色。
ME = 1
OTHER = 2


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _state(
    *,
    hand: tuple[int, ...] = (),
    turn_seat: int | None = 0,
    phase: Phase = Phase.PLAY,
    seats: int = 3,
) -> tuple[Engine, list[int]]:
    """马良在座位 1、另一个角色在座位 0 的构造局面（返回引擎与"我"的手牌）。"""
    specs: list[SeatSpec] = [SeatSpec() for _ in range(seats)]
    specs[0] = SeatSpec()
    specs[ME] = SeatSpec(hero=MALIANG, hand=hand)
    if seats > 2:
        specs[OTHER] = SeatSpec()
    used = tuple(hand)
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=_deck_excluding(*used),
        turn_seat=turn_seat,
        phase=phase,
    )
    engine = Engine.from_state(state, seed=7)
    return engine, list(hand)


def _tag(engine: Engine, card_id: int, *, owner: int = ME) -> None:
    """给一张牌挂「自书」标签（走引擎唯一写入口，与技能同一条路）。"""
    engine.add_card_tag(
        card_id,
        CardTag(tag_type=ZISHU_TAG, owner=owner, skill_id=ZISHU),
    )


def _has_tag(state, card_id: int) -> bool:
    return any(tag.tag_type is ZISHU_TAG for tag in state.tags_of(card_id))


def _gain(engine: Engine, card_id: int, *, reason: MoveReason = MoveReason.DRAW) -> None:
    """把一张牌从牌堆送给座位 1（马良）——"获得牌"，走移动入口（会派发 MOVE_AFTER）。"""
    engine.move_in_frame(
        None,
        engine.state.refs((card_id,)),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=reason,
        owner=ME,
        actor=ME,
    )


# ---------------------------------------------------------------------------
# 数据面 / 声明
# ---------------------------------------------------------------------------


def test_maliang_lands_in_the_sp1_table_with_two_skills():
    """数据面：``sp1`` 表里一条马良（蜀 / 男 / 3 体力 / 真名马良），两条技能都在 CATALOG 里。"""
    from sgs.data import load_heroes

    table = load_heroes("sp1")
    heroes = [hero for hero in table.heroes if hero.id == MALIANG]
    assert len(heroes) == 1
    hero = heroes[0]
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("shu", "male", 3)
    assert hero.true_name == "马良"
    assert hero.version == "sp1"
    assert hero.enabled is True
    assert [skill.id for skill in hero.skills] == ["zishu", "yingyuan"]
    assert all(hero_skill_id(MALIANG, skill.id) in CATALOG for skill in hero.skills)


def test_zishu_declares_the_card_text_verbatim_and_never_asks():
    """`requirement` 逐字（用户资产）+ 锁定技 + 三条钩子 + 没有流水线。"""
    definition = CATALOG[ZISHU]
    assert definition.requirement == (
        "锁定技，①其他角色的结束阶段结束时，你将本回合获得的牌置入弃牌堆；"
        "②你的回合内，当你不因此技能获得牌时，你摸一张牌。"
    )
    assert definition.locked is True
    assert definition.preactivated is False
    assert [hook.timing for hook in definition.hooks] == [
        Timing.MOVE_AFTER,
        Timing.TURN_END,
        Timing.TURN_START,
    ]
    assert all(hook.flow is None for hook in definition.hooks), "锁定技：不造 SkillFlow"


def test_yingyuan_declares_the_card_text_verbatim_with_one_seat_step():
    """`requirement` 逐字 + 一步选座位流水线 + `step_requirements["seat"]`。"""
    definition = CATALOG[YINGYUAN]
    assert definition.requirement == (
        "当你于回合内使用牌结算结束置入弃牌堆时，你可以将之交给一名其他角色"
        "（每种牌名的牌每回合限一次）。"
    )
    assert definition.locked is False
    assert definition.preactivated is False, "首步是座位步：按【突袭】那一档不翻"
    assert definition.step_requirements == {"seat": "将之交给一名其他角色"}
    (hook,) = definition.hooks
    assert hook.timing is Timing.MOVE_AFTER
    assert hook.flow is not None
    assert [type(step).__name__ for step in hook.flow.steps] == ["PickSeats"]


# ---------------------------------------------------------------------------
# 【自书】① 打标记
# ---------------------------------------------------------------------------


def test_zishu_tags_every_gained_entity_card_outside_its_owners_turn():
    """回合外获得牌 ⇒ 这次获得的每一张都带「自书」标签。"""
    first, second = _cards("peach")[0], _cards("dodge")[0]
    engine, _ = _state(hand=())
    engine.move_in_frame(
        None,
        engine.state.refs((first, second)),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=MoveReason.LOSE,
        owner=ME,
        actor=0,
    )
    assert _has_tag(engine.state, first) and _has_tag(engine.state, second)


def test_zishu_does_not_tag_cards_gained_in_its_own_turn():
    """「其他角色的…」⇒ 自己的回合里获得的牌**不打标记**（改走 ② 的摸一张）。"""
    card = _cards("peach")[0]
    engine, _ = _state(hand=(), turn_seat=ME)
    _gain(engine, card)
    assert not _has_tag(engine.state, card)


def test_zishu_marks_are_cleared_when_the_card_leaves_the_hand():
    """寿命②：离开手牌即清（弃置 / 交给别人 / 使用都算）。"""
    card = _cards("peach")[0]
    engine, _ = _state(hand=(card,))
    _tag(engine, card)
    assert _has_tag(engine.state, card), "前置：标签已挂上"
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=ME,
        actor=ME,
    )
    assert not _has_tag(engine.state, card)


def test_zishu_marks_are_swept_at_the_next_turn_start():
    """寿命①：**回合开始时清除**（`TURN_START` 簿记钩子）——牌留在手里、标记消失。

    这一条盖的是"当前角色阵亡 ⇒ 未被弃置"那一支留下的残标记：它不能活到下一个回合。
    """
    card = _cards("peach")[0]
    engine, _ = _state(hand=(card,), turn_seat=0)
    _tag(engine, card)
    engine._turn_start()
    assert card in engine.state.player(ME).hand, "清扫只去标记，不动牌"
    assert not _has_tag(engine.state, card)


# ---------------------------------------------------------------------------
# 【自书】① 回合结束时弃置
# ---------------------------------------------------------------------------


def test_zishu_discards_every_tagged_card_at_the_other_turn_end():
    """其他角色的回合结束时 ⇒ 带标记的牌**全部**置入弃牌堆（不带标记的不动）。"""
    marked, clean = _cards("peach")[0], _cards("dodge")[0]
    engine, _ = _state(hand=(marked, clean), turn_seat=0)
    _tag(engine, marked)
    engine._turn_end()
    assert marked in engine.state.discard
    assert clean in engine.state.player(ME).hand, "没带标记的牌不受影响"
    assert not _has_tag(engine.state, marked), "弃置离开手牌 ⇒ 寿命② 顺手清标记"


def test_zishu_does_not_discard_when_nothing_is_tagged():
    """没有带标记的牌 ⇒ 条件为假、连回调都不跑（零副作用）。"""
    clean = _cards("peach")[0]
    engine, _ = _state(hand=(clean,), turn_seat=0)
    engine._turn_end()
    assert clean in engine.state.player(ME).hand


def test_zishu_does_not_discard_when_the_turn_player_died():
    """「如果当前角色阵亡，则不会弃置这些牌」——`TURN_END` 的存活守卫。"""
    marked = _cards("peach")[0]
    engine, _ = _state(hand=(marked,), turn_seat=0)
    _tag(engine, marked)
    engine.state.player(0).alive = False  # 回合角色已阵亡
    engine._turn_end()
    assert marked in engine.state.player(ME).hand, "死者的回合结束不弃置我的自书牌"


# ---------------------------------------------------------------------------
# 【自书】② 摸一张
# ---------------------------------------------------------------------------


def test_zishu_draws_one_card_per_gain_event_in_its_owner_turn():
    """自己回合内获得牌 ⇒ 摸一张（一次获得 2 张仍是**一次事件** ⇒ 只多摸 1 张）。"""
    first, second = _cards("peach")[0], _cards("dodge")[0]
    engine, _ = _state(hand=(), turn_seat=ME)
    hand_before = len(engine.state.player(ME).hand)
    engine.move_in_frame(
        None,
        engine.state.refs((first, second)),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=MoveReason.DRAW,
        owner=ME,
        actor=ME,
    )
    # 获得 2 张 + 摸 1 张 = 3 张（不是 4 张）。
    assert len(engine.state.player(ME).hand) == hand_before + 3


def test_zishu_does_not_recurse_on_its_own_draw():
    """自书自己摸的那一张不再触发自己（帧私有 guard）。"""
    card = _cards("peach")[0]
    engine, _ = _state(hand=(), turn_seat=ME)
    engine.move_in_frame(
        None,
        engine.state.refs((card,)),
        to_zone=ZoneRef(Zone.HAND, ME),
        reason=MoveReason.DRAW,
        owner=ME,
        actor=ME,
    )
    assert len(engine.state.player(ME).hand) == 2, "获得 1 + 摸 1，且摸的那张不再触发"


def test_zishu_tagged_cards_still_count_toward_the_hand_limit():
    """「自书」牌**仍是普通手牌**：reader 不是 HAND_COUNT（否则弃牌阶段会少弃）。"""
    from sgs.data import CARD_TAG_TYPES, CardTagReader
    from sgs.effects import hand_count

    spec = CARD_TAG_TYPES[ZISHU_TAG]
    assert spec.reader is CardTagReader.SKILL_LOCAL
    card = _cards("peach")[0]
    engine, _ = _state(hand=(card,))
    _tag(engine, card)
    assert hand_count(engine.state, engine.state.player(ME)) == 1


# ---------------------------------------------------------------------------
# 【应援】
# ---------------------------------------------------------------------------


class _Yingyuan:
    """座位 1 的脚本：出牌阶段打出一张【杀】指定座位 0，应援询问答"发动"、座位选 0。"""

    def __init__(self, state, *, use_card: int, activate: bool = True, seat: int = 0):
        self._state = state
        self.use_card = use_card
        self.activate = activate
        self.seat = seat
        self.used = False
        self.asked = 0
        self.seat_options: list[tuple[str, ...]] = []

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                self.used = True
                return UseCard(
                    card=self._state.card(self.use_card),
                    targets=(0,),
                    answers_seq=query.seq,
                )
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == "SKILL_ACTIVATION":
            self.asked += 1
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == YINGYUAN:
                    if self.activate:
                        return action
                    break
            return _pass(query)
        if query.kind == "SELECT_OPTION":
            values = tuple(getattr(query, "options", ()) or ())
            self.seat_options.append(values)
            wanted = f"seat:{self.seat}"
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == wanted:
                    return action
            return _pass(query)
        return _pass(query)


def _pass(query):
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def test_yingyuan_hands_the_used_card_to_another_character():
    """使用【杀】结算结束进弃牌堆 ⇒ 交给座位 0 的手牌（并记该牌名已用过）。"""
    slash = _cards("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hero=MALIANG, hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(slash),
        turn_seat=ME,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=11)
    script = _Yingyuan(state, use_card=slash, seat=0)
    players = [PassPlayer(), script, PassPlayer()]
    run_queries(engine, players, lambda e: slash in e.state.player(0).hand)
    assert script.asked >= 1, "应援必须被问一次"
    assert slash in engine.state.player(0).hand, "那张用过的【杀】被交给了座位 0"
    assert engine.state.player(ME).yingyuan_names == ("slash",)


def _yingyuan_condition_frame(engine: Engine, card_id: int):
    """构造一份"这张牌刚进弃牌堆"的 MOVE_AFTER 帧（只喂条件，不驱动整局）。"""
    from types import SimpleNamespace

    from sgs.skills.catalog import MATCH_OWNER_KEY
    from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent

    event = MoveEvent(
        cards=(engine.state.card(card_id),),
        from_zone=ZoneRef(Zone.PROCESSING),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.USE,
        owner=ME,
        after_holder=None,
        actor=ME,
    )
    return SimpleNamespace(
        data={
            MATCH_OWNER_KEY: ME,
            "skill_owner": ME,
            "card": engine.state.card(card_id),
            "source": ME,
            "targets": (),
            MOVE_EVENT_KEY: event,
        }
    )


def test_yingyuan_condition_refuses_a_name_that_was_already_used_this_turn():
    """每种牌名每回合一次：本回合已发动过的牌名 ⇒ 条件为假（连问都不问）。"""
    from sgs.skills.hero_sp1 import _yingyuan_condition

    card = _cards("ex_nihilo")[0]
    engine, _ = _state(hand=(card,), turn_seat=ME)
    assert _yingyuan_condition(engine, _yingyuan_condition_frame(engine, card)) is True
    engine.state.player(ME).yingyuan_names = ("ex_nihilo",)
    assert _yingyuan_condition(engine, _yingyuan_condition_frame(engine, card)) is False


def test_yingyuan_declining_does_not_spend_the_name():
    """放弃不占位：`yingyuan_names` 只在**提交点**写 ⇒ 没发动过就仍可发动。"""
    card = _cards("ex_nihilo")[0]
    engine, _ = _state(hand=(card,), turn_seat=ME)
    frame = _yingyuan_condition_frame(engine, card)
    from sgs.skills.hero_sp1 import _yingyuan_condition

    assert _yingyuan_condition(engine, frame) is True
    assert engine.state.player(ME).yingyuan_names == (), "只是问了一句，不占位"
    assert frame.data.get("yingyuan_done") is None


# ---------------------------------------------------------------------------
# SP孙尚香【良助】（sp1-sunshangxiang-maliang 票 02；用户 2026-11-18 签 Q1–Q5 / Q7）
# ---------------------------------------------------------------------------

SUNSHANGXIANG = "sunshangxiang_sp"


def _liangzhu_frame(*, recoverer: int, amount: int, owner: int = ME):
    """构造一份"回复者刚回复了 ``amount`` 点"的 RECOVER_AFTER 帧（只喂条件）。"""
    from types import SimpleNamespace

    from sgs.skills.catalog import MATCH_OWNER_KEY, RECOVERED_AMOUNT_KEY

    return SimpleNamespace(
        data={
            MATCH_OWNER_KEY: owner,
            "skill_owner": owner,
            "source": recoverer,
            "targets": (recoverer,),
            "card": None,
            RECOVERED_AMOUNT_KEY: amount,
        }
    )


def _sp_state(*, turn_seat: int | None = 0, phase: Phase = Phase.PLAY, seats: int = 3):
    """座位 1 = SP孙尚香、座位 0 = 回复者（体力 2/3）的构造局面。"""
    specs = [SeatSpec(hp=2) for _ in range(seats)]
    specs[ME] = SeatSpec(hero=SUNSHANGXIANG)
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=_deck_excluding(),
        turn_seat=turn_seat,
        phase=phase,
    )
    return Engine.from_state(state, seed=5)


def test_sunshangxiang_sp_lands_in_the_sp1_table_with_three_skills():
    """数据面：``sp1`` 表里的 SP孙尚香（蜀 / 女 / 3 体力 / 真名孙尚香 / 三条技能，枭姬是 ☆）。"""
    from sgs.data import load_heroes

    table = load_heroes("sp1")
    hero = next(h for h in table.heroes if h.id == SUNSHANGXIANG)
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("shu", "female", 3)
    assert (hero.name, hero.true_name, hero.version) == ("SP孙尚香", "孙尚香", "sp1")
    assert [skill.id for skill in hero.skills] == ["liangzhu", "fanxiang", "xiaoji"]
    granted = {skill.id: skill.granted_by for skill in hero.skills}
    assert granted["xiaoji"] == "fanxiang", "☆枭姬只由【返乡】授予"


def test_liangzhu_declares_the_card_text_verbatim_with_one_option_step():
    definition = CATALOG[LIANGZHU]
    assert definition.requirement == (
        "当一名角色于其出牌阶段内回复体力后，你可以选择一项：1.摸一张牌；2.令其摸两张牌。"
    )
    assert definition.locked is False
    assert definition.preactivated is False
    (hook,) = definition.hooks
    assert hook.timing is Timing.RECOVER_AFTER
    assert [type(step).__name__ for step in hook.flow.steps] == ["PickOption"]
    assert definition.step_requirements == {"option": "选择一项：1.摸一张牌；2.令其摸两张牌"}


def test_liangzhu_needs_the_recoverers_own_play_phase():
    """「于**其**出牌阶段内」= 回复者自己的出牌阶段（一手：华佗【青囊】不能触发）。"""
    from sgs.skills.hero_sp1 import _liangzhu_condition

    engine = _sp_state(turn_seat=0, phase=Phase.PLAY)
    assert _liangzhu_condition(engine, _liangzhu_frame(recoverer=0, amount=1)) is True
    # 回复者不是回合角色（青囊那一手）⇒ 不触发
    assert _liangzhu_condition(engine, _liangzhu_frame(recoverer=ME, amount=1)) is False
    # 回合角色在自己的出牌阶段以外回复 ⇒ 不触发
    engine.state.phase = Phase.DRAW
    assert _liangzhu_condition(engine, _liangzhu_frame(recoverer=0, amount=1)) is False


def test_liangzhu_ignores_a_zero_point_recovery():
    """用户 2026-11-18 签 Q7：满体力时压出的"回复 0 点"不算回复事件。"""
    from sgs.skills.hero_sp1 import _liangzhu_condition

    engine = _sp_state(turn_seat=0, phase=Phase.PLAY)
    assert _liangzhu_condition(engine, _liangzhu_frame(recoverer=0, amount=0)) is False


class _DrawHost:
    """只记录"谁摸了几张"的最小宿主（效果面单测；引擎整局由其他用例覆盖）。"""

    def __init__(self, state):
        self.state = state
        self.drawn: list[tuple[int, int]] = []

    def draw_for_hand(self, frame, seat, n):  # noqa: ANN001, D401
        self.drawn.append((int(seat), int(n)))
        return True


def _recover_state():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2),
            SeatSpec(hero=SUNSHANGXIANG),
            SeatSpec(),
        ],
        deck=_deck_excluding(),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return state


def test_liangzhu_option_two_draws_two_for_the_recoverer_and_records_the_fact():
    """选项 2：**回复者**摸两张 + 记"我对这个座位用过良助②"（整局按座位）。"""
    from sgs.skills.hero_sp1 import _liangzhu_effect, liangzhu_used_on

    state = _recover_state()
    engine = Engine.from_state(state, seed=3)
    host = _DrawHost(engine.state)
    frame = _liangzhu_frame(recoverer=0, amount=1)
    assert _liangzhu_effect(host, frame, {"option": "liangzhu:draw_two"}) is True
    assert host.drawn == [(0, 2)], "摸两张的是**回复者**（座位 0），不是孙尚香"
    assert liangzhu_used_on(engine.state, ME, 0) is True
    assert liangzhu_used_on(engine.state, ME, OTHER) is False, "只记被选中的那个座位"
    # 重入（挂起续跑）不重摸：游标在摸牌之前就写了
    assert _liangzhu_effect(host, frame, {"option": "liangzhu:draw_two"}) is None
    assert host.drawn == [(0, 2)]


def test_liangzhu_option_one_draws_one_for_the_owner_and_records_nothing():
    """选项 1：**我**摸一张，且**不**记事实（用户 2026-11-18 签 Q1）。"""
    from sgs.skills.hero_sp1 import _liangzhu_effect, liangzhu_used_on

    state = _recover_state()
    engine = Engine.from_state(state, seed=3)
    host = _DrawHost(engine.state)
    frame = _liangzhu_frame(recoverer=0, amount=1)
    assert _liangzhu_effect(host, frame, {"option": "liangzhu:draw_one"}) is True
    assert host.drawn == [(ME, 1)]
    assert liangzhu_used_on(engine.state, ME, 0) is False


def test_fanxiang_awakens_raises_the_max_then_heals_then_swaps_the_skill():
    """【返乡】觉醒：先 +1 上限、再回复 1 点，然后失去【良助】、获得 ☆【枭姬】。"""
    from sgs.skills.hero_sp1 import LIANGZHU, XIAOJI_SP, liangzhu_used_on

    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2),
            SeatSpec(hero=SUNSHANGXIANG, hp=1, hp_max=3),
            SeatSpec(),
        ],
        deck=_deck_excluding(),
        turn_seat=ME,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=9)
    # 前置：座位 0 已受伤 ∧ 我对他用过良助②（整局位段）
    from sgs.skills.catalog import instance_key, resolve_instances

    for instance in resolve_instances(state):
        if instance.owner == ME and instance.skill_id == LIANGZHU:
            state.skill_state[instance_key(instance)] = 1 << (36 + 0)
    assert liangzhu_used_on(state, ME, 0) is True

    players = [PassPlayer() for _ in range(3)]
    run_queries(
        engine,
        players,
        lambda e: XIAOJI_SP in e.state.player(ME).granted_skills,
    )
    player = engine.state.player(ME)
    assert player.hp_max == 4 and player.hp == 2, "先 +1 上限、再回复 1 点（1/3 → 2/4）"
    assert LIANGZHU in player.lost_skills
    assert XIAOJI_SP in player.granted_skills


def test_sp_xiaoji_reuses_the_standard_declaration_and_removes_equipment():
    """☆【枭姬】：与标准 / 界**同一条回调**；失去自己的装备即问、答发动摸两张。"""
    from sgs.skills.move_after import XIAOJI_FLOW
    from sgs.skills.hero_sp1 import XIAOJI_SP as SP_XIAOJI

    definition = CATALOG[SP_XIAOJI]
    (hook,) = definition.hooks
    assert hook.flow is XIAOJI_FLOW, "同一条零步流水线"
    assert definition.requirement == CATALOG["hero.sunshangxiang.xiaoji"].requirement
