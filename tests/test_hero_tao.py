"""自制包·韬·刘备自制版：【从讨】/【义起】（含 ☆武圣 / ☆咆哮）的构造局面单测。

两条技能各走一种"既有原语的组合"（不再是新机制）：

- 【从讨】= 手写钩子（``USE_BECOME_TARGET`` 逐目标）+ 观看牌堆顶两张 + 二选一。样板是
  【遗计】（私密观看 + 选牌 + 交牌）与【涯角】（交给某人 / 放回牌堆顶 = 不搬牌）；
- 【义起】= 主动技能的**限定技**版本：观看步（0 张选择 + ``revealed`` 授予 ⇒ 亮牌弹窗）
  → 类型步（恒 3 项、不可放弃）→ 获得 / 失去 / 授予。

判据一律走外部缝：候选集合、询问载荷（``slots`` / ``spec`` / ``revealed`` / ``options``）、
记录流里的目标与牌移动、技能实例集合。不读回调内部结构。
"""

from __future__ import annotations

import pytest

from sgs.data import Category, load_content_pack
from sgs.engine import Engine
from sgs.skills import CATALOG, limited_skill_used
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    SelectCards,
    SelectOption,
    UseCard,
    Zone,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")

CONGTAO = "hero.tao_liubei.congtao"
YIQI = "hero.tao_liubei.yiqi"
WUSHENG = "hero.guanyu.wusheng"
PAOXIAO = "hero.zhangfei.paoxiao"
LIUBEI = "tao_liubei"


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _hit(engine, seat: int) -> bool:
    return any(
        record.kind == "delta"
        and record.delta_type == "hp_change"
        and record.seat == seat
        for record in engine.records
    )


PASS = PassPlayer()


# ---------------------------------------------------------------------------
# 【从讨】：成为【杀】的目标时，观看牌堆顶两张 → 一张交给使用者 → 另一张二选一
# ---------------------------------------------------------------------------

SLASH = _cards("slash")[0]
DODGE = _cards("dodge")[0]
DISMANTLEMENT = _cards("dismantlement")[0]


class _CongtaoDriver:
    """座位 1 使用【杀】指向座位 2；座位 0（刘备）按脚本应答【从讨】。

    ``activations`` / ``picks`` / ``leftovers`` 分别录下三次询问，供断言读形状。
    """

    def __init__(
        self,
        *,
        activate: bool = True,
        pick_index: int = 0,
        leftover: str = "congtao_discard",
        targets: tuple[int, ...] = (2,),
        card: int = SLASH,
        play_seat: int = 1,
    ) -> None:
        self.activate = activate
        self.play_seat = play_seat
        self.pick_index = pick_index
        self.leftover = leftover
        self.targets = targets
        self.card = card
        self.activations: list = []
        self.picks: list = []
        self.leftovers: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == CONGTAO:
            self.activations.append(query)
            if not self.activate:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == "SELECT_CARDS" and query.actor == 0:
            self.picks.append(query)
            return next(
                a
                for a in query.legal_actions
                if isinstance(a, SelectCards) and a.picks == (self.pick_index,)
            )
        if query.kind == "SELECT_OPTION" and query.actor == 0:
            self.leftovers.append(query)
            return next(
                a
                for a in query.legal_actions
                if getattr(a, "choice", None) == self.leftover
            )
        if query.kind == "PLAY_PHASE" and query.actor == self.play_seat:
            for action in query.legal_actions:
                card = getattr(action, "card", None)
                if (
                    card is not None
                    and card.card_id == self.card
                    and tuple(getattr(action, "targets", None) or ()) == self.targets
                ):
                    return action
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _congtao_engine(
    *,
    seats: int = 3,
    hero: str | None = LIUBEI,
    hand: tuple[int, ...] = (SLASH,),
    deck: tuple[int, ...] | None = None,
) -> Engine:
    """座位 1 的回合：它手里有【杀】，指向座位 2（3 人环上座位 0↔2 的距离也是 1）。"""
    specs = [SeatSpec(hero=hero)] + [SeatSpec(hand=hand)] + [SeatSpec() for _ in range(seats - 2)]
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=_deck_excluding(*hand) if deck is None else deck,
        turn_seat=1,
    )
    return Engine.from_state(state, seed=23)


def test_congtao_gives_one_card_to_the_slash_user_and_the_other_to_the_discard_pile():
    engine = _congtao_engine()
    driver = _CongtaoDriver(pick_index=0, leftover="congtao_discard")

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 2))

    assert len(driver.activations) == 1, "成为【杀】的目标时问一次「是否发动」"
    assert [q.spec.total for q in driver.picks] == [(1, 1)], "选一张交给使用者"
    view = driver.picks[0]
    revealed = sorted(card.card_id for card in view.revealed)
    assert len(revealed) == 2, "观看的是牌堆顶两张（私密授予，不是手牌）"
    got = [card_id for card_id in engine.state.player(1).hand if card_id in revealed]
    assert got == [revealed[0]], "选中的那张交给【杀】的使用者"
    assert revealed[1] in engine.state.discard, "另一张按二选一置入弃牌堆"
    assert len(driver.leftovers) == 1, "另一张的去处问一次"
    assert set(driver.leftovers[0].options) == {"congtao_discard", "congtao_deck_top"}


def test_congtao_can_put_the_leftover_back_on_top_of_the_deck():
    engine = _congtao_engine()
    driver = _CongtaoDriver(pick_index=1, leftover="congtao_deck_top")

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 2))

    revealed = sorted(card.card_id for card in driver.picks[0].revealed)
    got = [card_id for card_id in engine.state.player(1).hand if card_id in revealed]
    assert got == [revealed[1]], "选中的（第二张）交给使用者"
    assert engine.state.discard == [], "放回牌堆顶 ⇒ 不进弃牌堆"
    assert engine.state.deck[0] == revealed[0], "另一张本来就在牌堆顶上（不搬家）"


def test_congtao_is_not_offered_when_the_target_is_out_of_attack_range():
    """5 人环：座位 2 与座位 0 的距离是 2 > 攻击范围 1 ⇒ 连「是否发动」都不问。"""
    engine = _congtao_engine(seats=5, hero=LIUBEI, hand=(SLASH,))
    driver = _CongtaoDriver(targets=(2,))

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 2))

    assert driver.activations == [], "目标不在攻击范围内 ⇒ 不触发"
    assert engine.state.discard == []


def test_congtao_is_not_offered_when_the_holder_is_the_target():
    """【杀】指向座位 0（刘备自己）⇒「其它角色」不成立，不触发。"""
    engine = _congtao_engine()
    driver = _CongtaoDriver(targets=(0,))

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 0))

    assert driver.activations == []


def test_congtao_is_not_offered_for_a_non_slash():
    """同一局面换成【过河拆桥】：C2 同样派发，但不是【杀】⇒ 不触发。"""
    engine = _congtao_engine(hand=(DISMANTLEMENT,))
    driver = _CongtaoDriver(card=DISMANTLEMENT, targets=(2,))

    run_queries(engine, [driver, driver, PASS], lambda e: e.state.turn_seat != 1 or e.finished)

    assert driver.activations == []


def test_congtao_fires_once_per_target_and_stops_at_two_per_round():
    """刘备自己的三目标【杀】（方天画戟）：每个在范围内的目标各触发一次，但每轮封顶 2 次。

    为了让他手里只剩那**唯一一张**【杀】（方天画戟的"最后一张手牌"），用【兵粮寸断】跳过
    摸牌阶段：牌堆顶（♥ 非梅花）⇒ 判定不中 ⇒ 不摸牌。
    """
    pack = load_content_pack(deck_profile="standard_ex_160")
    slash = cards_of_type(pack, "slash")[0]
    fan = cards_of_type(pack, "halberd")[0]
    shortage = cards_of_type(pack, "supply_shortage")[0]
    excluded = {slash, fan, shortage}
    deck = tuple(
        card_id for card_id in range(len(pack.deck.cards)) if card_id not in excluded
    )
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero=LIUBEI, hand=(slash,), equip=(fan,), judge=(shortage,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=29)
    driver = _CongtaoDriver(card=slash, targets=(1, 2, 3), play_seat=0)

    # 逐个目标「成为目标 → 闪 → 结算」是**逐目标串行**的，故跑到刘备自己的回合结束为止。
    run_queries(
        engine,
        [driver, PASS, PASS, PASS, PASS],
        lambda e: e.state.turn_seat != 0 or e.finished,
    )

    # 两次触发各把一张牌交给"【杀】的使用者"= 刘备自己 ⇒ 手牌恰好 2 张（另一张进弃牌堆）。
    hand = engine.state.player(0).hand
    assert len(hand) == 2, "两次触发各收到一张"
    assert len(engine.state.discard) >= 2, "两次触发各置入弃牌堆一张（外加用掉的【杀】/兵粮寸断）"
    # 同帧的"是否发动"答案按 (skill, timing) 缓存（账本 F8：同帧第二次触发复用答案），
    # 故只问一次、但**两次触发各跑一遍**（两次选牌询问 + 两次收牌）。
    assert len(driver.activations) == 1, "同一帧的「是否发动」只问一次（答案复用）"
    assert len(driver.picks) == 2, "逐目标触发两次，第三次被「每轮限2次」挡掉"
    assert engine.state.round_use_counts, "每轮次数已记账"
    assert list(engine.state.round_use_counts.values()) == [2], "恰好 2 次"


def test_congtao_declining_changes_nothing():
    engine = _congtao_engine()
    driver = _CongtaoDriver(activate=False)

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 2))

    assert len(driver.activations) == 1
    assert driver.picks == [] and driver.leftovers == []
    # 座位 1 的手牌 = 摸牌阶段那两张（【杀】已用掉）；放弃发动 ⇒ 牌堆一张都没被拿走。
    assert len(engine.state.player(1).hand) == 2, "只有自己摸牌阶段的那两张（没有收到赠牌）" 


def test_congtao_single_viewed_card_skips_the_choice():
    """牌堆只剩一张（弃牌堆空）⇒ 观看一张、交给使用者，**没有"另一张"** ⇒ 不问去处。"""
    # 先手是座位 0（身份局按主公/无身份按 first_seat）：它摸两张、座位 1 再摸两张，
    # 第三张才是【从讨】观看时牌堆里唯一的一张。
    five = (_cards("slash")[3], _cards("slash")[4], _cards("slash")[5], _cards("slash")[6], DODGE)
    engine = _congtao_engine(deck=five)
    only = five[4]
    driver = _CongtaoDriver()

    run_queries(engine, [driver, driver, PASS], lambda e: _hit(e, 2))

    assert len(driver.activations) == 1
    assert [len(q.revealed) for q in driver.picks] == [1]
    assert only in engine.state.player(1).hand
    assert driver.leftovers == [], "只有一张亮牌 ⇒ 去处那一步不出现"


# ---------------------------------------------------------------------------
# 【义起】：观看弃牌堆顶 x 张 → 恒 3 项类型选 1 → 获得 / 失去 / 授予
# ---------------------------------------------------------------------------

BASIC_TOP = _cards("slash")[1]
BASIC_TOP2 = _cards("slash")[2]
TRICK_TOP = _cards("dismantlement")[1]


def _filler(count: int, *used: int) -> tuple[int, ...]:
    """牌堆里没被这一局用到的 ``count`` 张（弃牌堆的"垫底"牌，不进观看的那 x 张）。"""
    excluded = set(used)
    picked: list[int] = []
    for card_id in range(len(PACK.deck.cards)):
        if card_id in excluded:
            continue
        picked.append(card_id)
        if len(picked) == count:
            break
    return tuple(picked)


class _YiqiDriver:
    """座位 0（刘备）在自己的出牌阶段发动【义起】。"""

    def __init__(self, *, choice: str = "basic") -> None:
        self.choice = choice
        self.activations: list = []
        self.views: list = []
        self.types: list = []

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and query.actor == 0:
            for action in query.legal_actions:
                if getattr(action, "skill_id", None) == YIQI:
                    self.activations.append(action)
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == "SELECT_CARDS" and query.actor == 0:
            self.views.append(query)
            return next(a for a in query.legal_actions if isinstance(a, SelectCards))
        if query.kind == "SELECT_OPTION" and query.actor == 0:
            self.types.append(query)
            return next(
                a for a in query.legal_actions if getattr(a, "choice", None) == self.choice
            )
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _yiqi_engine(*, discard: tuple[int, ...], hand: tuple[int, ...] = ()) -> Engine:
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=LIUBEI, hand=hand), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(*hand, *discard),
        discard=discard,
        turn_seat=0,
    )
    return Engine.from_state(state, seed=31)


def _play_phase(engine) -> Engine:
    """推到刘备自己的出牌阶段（循环到 PLAY_PHASE 询问挂起）。"""
    for _ in range(300):
        if engine.pending is not None and engine.pending.kind == "PLAY_PHASE":
            return engine
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("没有到达出牌阶段")


def _yiqi_offers(engine) -> list:
    from sgs.effects import legal_actions_for
    from sgs.state import QueryKind

    return [
        action
        for action in legal_actions_for(
            engine.state, 0, QueryKind.PLAY_PHASE, answers_seq=1
        )
        if getattr(action, "skill_id", None) == YIQI
    ]


def _instances(engine) -> set[tuple[int, str]]:
    from sgs.skills import resolve_instances

    return {(item.owner, item.skill_id) for item in resolve_instances(engine.state)}


def test_yiqi_is_not_offered_when_the_discard_pile_is_under_ten_cards():
    engine = _yiqi_engine(discard=_filler(9))

    assert _yiqi_offers(engine) == [], "x = 0（弃牌堆 < 10 张）⇒ 候选不出现"


def test_yiqi_asks_one_question_that_shows_the_top_x_and_offers_three_types():
    """观看与三选一是**同一问**（2026-10-31 实机验收后的形状）。

    这一问是 ``SELECT_OPTION``：``revealed`` = 弃牌堆顶 x 张（投影据此开选择型弹窗，牌面
    与 3 个类型按钮同窗），``options`` 恒 3 项、不可放弃。**没有**额外的"只看不选"选牌步
    ——那一步既填不进预发动清单，也没有任何界面价值。
    """
    discard = _filler(18, BASIC_TOP, TRICK_TOP) + (BASIC_TOP, TRICK_TOP)
    engine = _yiqi_engine(discard=discard)
    driver = _YiqiDriver(choice="basic")

    assert _yiqi_offers(engine), "x = 2 ⇒ 有候选"
    run_queries(engine, [driver, PASS, PASS], lambda e: limited_skill_used(e.state, 0, YIQI))

    assert driver.views == [], "没有单独的「只看不选」选牌步"
    assert len(driver.types) == 1, "整条流水线只问一次"
    question = driver.types[0]
    assert question.options == ("basic", "trick", "equipment"), "恒 3 项（用户裁定）"
    assert sorted(card.card_id for card in question.revealed) == sorted(
        (BASIC_TOP, TRICK_TOP)
    ), "牌面走本次询问的授予通道 = 弃牌堆顶 x 张（x = 20 // 10 = 2，仅自己可见）"


def test_yiqi_gains_every_card_of_the_chosen_type_and_keeps_the_rest():
    discard = _filler(18, BASIC_TOP, BASIC_TOP2) + (BASIC_TOP, BASIC_TOP2)
    engine = _yiqi_engine(discard=discard)
    driver = _YiqiDriver(choice="basic")

    run_queries(engine, [driver, PASS, PASS], lambda e: limited_skill_used(e.state, 0, YIQI))

    hand = engine.state.player(0).hand
    assert BASIC_TOP in hand and BASIC_TOP2 in hand, (
        "获得该类**所有**牌（两张都是基本牌 ⇒ 两张都拿）"
    )
    assert BASIC_TOP not in engine.state.discard
    assert BASIC_TOP2 not in engine.state.discard


def test_yiqi_choosing_a_type_with_no_cards_gains_nothing_but_still_transforms():
    """3 项恒在：选中顶 x 张里没有的类型 ⇒ 不得牌，但仍失去【从讨】并获得【武圣】【咆哮】。"""
    discard = _filler(18, BASIC_TOP, BASIC_TOP2) + (BASIC_TOP, BASIC_TOP2)
    engine = _yiqi_engine(discard=discard)
    driver = _YiqiDriver(choice="equipment")

    run_queries(engine, [driver, PASS, PASS], lambda e: limited_skill_used(e.state, 0, YIQI))

    hand = engine.state.player(0).hand
    assert BASIC_TOP not in hand and BASIC_TOP2 not in hand, "没有装备牌可得（那两张仍在弃牌堆）"
    instances = _instances(engine)
    assert (0, CONGTAO) not in instances, "仍然失去【从讨】"
    assert (0, WUSHENG) in instances and (0, PAOXIAO) in instances, "仍然获得 ☆ 技能"


def test_yiqi_loses_congtao_keeps_itself_and_grants_the_standard_skills():
    discard = _filler(18, BASIC_TOP, TRICK_TOP) + (BASIC_TOP, TRICK_TOP)
    engine = _yiqi_engine(discard=discard)
    driver = _YiqiDriver(choice="trick")

    run_queries(engine, [driver, PASS, PASS], lambda e: limited_skill_used(e.state, 0, YIQI))

    instances = _instances(engine)
    assert (0, CONGTAO) not in instances, "「失去武将牌上除此技能外的所有技能」"
    assert (0, YIQI) in instances, "【义起】自己不失去（只是用完）"
    assert (0, WUSHENG) in instances, "获得标准版【武圣】"
    assert (0, PAOXIAO) in instances, "获得标准版【咆哮】"
    assert TRICK_TOP in engine.state.player(0).hand
    assert CATALOG[WUSHENG].virtual_type_ids == ("slash",), "武圣 = 标准版（红色牌当【杀】）"
    assert CATALOG[PAOXIAO].locked is True, "咆哮 = 标准版锁定技（【杀】无次数限制）"


def test_yiqi_is_a_limited_skill_and_disappears_after_use():
    discard = _filler(18, BASIC_TOP, TRICK_TOP) + (BASIC_TOP, TRICK_TOP)
    engine = _yiqi_engine(discard=discard)
    driver = _YiqiDriver(choice="basic")

    run_queries(engine, [driver, PASS, PASS], lambda e: limited_skill_used(e.state, 0, YIQI))

    assert limited_skill_used(engine.state, 0, YIQI) is True
    assert _yiqi_offers(engine) == [], "整局已用 ⇒ 不再产出候选"


def test_yiqi_lost_skill_ids_are_derived_from_the_hero_card():
    """用户 Q7：不写死【从讨】——换一名武将（界徐庶），派生结果就是它自己的技能清单。"""
    from sgs.skills import active as active_module

    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="jie_xushu"), SeatSpec(), SeatSpec()],
        turn_seat=0,
    )

    assert set(active_module._yiqi_lost_skill_ids(state, 0)) == {
        "hero.jie_xushu.qianxue",
        "hero.jie_xushu.zhuhai",
    }, "武将牌技能清单减【义起】——本将没有【义起】，故整份清单都在"


def test_granted_wusheng_really_turns_a_red_card_into_a_slash():
    """用户 Q2 的落地验证：授予的是**标准版** id，且它在别人身上照样产出转化候选。

    【义起】把 `hero.guanyu.wusheng` 加到刘备身上——若转化候选按"武将自己的技能"过滤，
    授予就成了摆设。这里直接问出牌阶段的候选：一张红色锦囊（桃园结义）在授予之后应出现
    「当【杀】」的候选；【咆哮】的声明则只认 `COUNT_LIMIT`（标准版锁定技）。
    """
    from sgs.effects import legal_actions_for, ModifierPoint
    from sgs.state import QueryKind

    red = next(
        card_id
        for card_id, card in enumerate(PACK.deck.cards)
        if card.suit in ("heart", "diamond") and card.type == "peach_garden"
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=LIUBEI, hand=(red,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(red),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=41)
    engine.grant_skill(0, WUSHENG)
    engine.grant_skill(0, PAOXIAO)

    actions = [
        action
        for action in legal_actions_for(
            engine.state, 0, QueryKind.PLAY_PHASE, answers_seq=1
        )
        if getattr(action, "card", None) is not None
        and action.card.type == "slash"
        and action.card.is_virtual
    ]
    assert actions, "授予【武圣】后，红色【桃园结义】应能当【杀】使用（转化候选按实例产出）"
    assert {modifier.point for modifier in CATALOG[PAOXIAO].modifiers} == {
        ModifierPoint.COUNT_LIMIT
    }, "【咆哮】= 标准版锁定技（只挂次数上限）"


def test_yiqi_never_enters_the_preactivation_manifest():
    """带观看授予的流水线**不下发预发动清单**（2026-10-31 实机报障的回归）。

    清单是"发动前一次填完"的形态：没有"先看到牌"这一环。义起被当成预发动技能时，
    界面上第一步是个空步骤、第二步被渲染成"点场上武将（座位 0..3）"，提交还被校验器拒
    （`步骤 type 的选项 'seat:1' 不在闭集内`）。故 `flow.manifest` 对含 `revealed` 步骤的
    流水线整条返回空元组 ⇒ 界面走"点技能按钮 → 引擎逐步问"，那一问就是带牌面的选项询问。
    """
    from sgs.skills.preactivation import preactivation_for

    discard = _filler(18, BASIC_TOP, TRICK_TOP) + (BASIC_TOP, TRICK_TOP)
    engine = _yiqi_engine(discard=discard)

    assert preactivation_for(engine.state, 0, YIQI) == (), "不下发预发动清单"
    assert _yiqi_offers(engine), "候选照旧在（走直提态 + 逐步问）"
