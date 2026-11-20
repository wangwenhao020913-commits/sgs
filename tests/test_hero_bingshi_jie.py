"""兵势篇-节包：势赵云【武翊】【摧锋】（bingshi-jie 票 01）。

用户 2026-11-18 签的 13 条口径（票面「用户裁定」表）里，本文件逐条钉住能被机械断言的那些：

- **Q1** = 属性【杀】并入【杀】：四个牌名 ⇒ X 上限 4；
- **Q2** = ① 的两个窗口共用「每轮限一次」（响应面只有被【杀】要求**使用**【闪】那一扇；
  「视为**使用**」⇒ 万箭 / 南蛮 / 决斗的**打出**窗口不参与，用户 2026-11-18 复核）；
- **Q3** = 出牌阶段只给【杀】（预发动清单里**没有**【闪】这一项）；
- **Q5** = ④ 回填到 4（多于 4 自己选弃、少于 4 摸到 4），不碰手牌上限；
- **Q6** = 摧锋在**自己**的回合末不提供候选（门槛 = 【决斗】不能指定自己为目标）；
- **Q7 / Q8** = +X 动态、卡牌级（双方都吃）——票面注意 2 的 ``1+2+1+1=5`` 逐字复现；
- **Q10** = 座位标签「武翊 X」常驻（X = 0 也画）。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import RuleConfig, hero_skill_id, load_content_pack
from sgs.effects import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    check_count,
    check_targets,
)
from sgs.engine import Engine
from sgs.skills import CATALOG, PROVIDERS
from sgs.skills.active import (
    _wuyi_name_of,
    _wuyi_range_exempt,
    _wuyi_unlimited_basics,
)
from sgs.skills.hero_bingshi import _cuifeng_condition, _cuifeng_damage_bonus
from sgs.skills.virtual import pure_virtual_card
from sgs.state import (
    ActivateSkill,
    RespondMode,
    CardRef,
    EndPhase,
    GameState,
    Pass,
    Phase,
    PlayCard,
    DiscardCards,
    Reforge,
    SelectCards,
    UseCard,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries

PACK = load_content_pack(deck_profile="standard_108")
PACK_160 = load_content_pack(deck_profile="standard_ex_160")

WUYI = hero_skill_id("shi_zhaoyun", "wuyi")
CUIFENG = hero_skill_id("shi_zhaoyun", "cuifeng")
HERO = "shi_zhaoyun"


# ---------------------------------------------------------------------------
# 数据面 / 声明面
# ---------------------------------------------------------------------------


def test_the_pack_ships_the_hero_with_both_skills() -> None:
    """数据面：兵势篇-节包装载得出势赵云（真名赵云、蜀、4 体力），两条技能都在 CATALOG 里。"""
    from sgs.data import hero_by_id, hero_skill_ids

    hero = hero_by_id(HERO)
    assert hero is not None
    assert hero.name == "势赵云"
    assert hero.true_name == "赵云"
    assert hero.hp_max == 4
    assert hero_skill_ids(HERO) == (WUYI, CUIFENG)
    assert WUYI in CATALOG and CUIFENG in CATALOG


def test_wuyi_keeps_all_four_faces_on_one_declaration() -> None:
    """武翊：每轮限次 / 座位标签 / 三条强制钩子 / 两个修饰器 + 提供者，**一条声明**装齐。"""
    definition = CATALOG[WUYI]
    assert definition.per_round_limit is True
    assert definition.badge is not None
    assert [hook.timing for hook in definition.hooks] == [
        Timing.USE_ON_USE,
        Timing.PLAY_ON_PLAY,
        Timing.MOVE_AFTER,
    ]
    assert all(hook.forced for hook in definition.hooks), "④ 与计数都不问「是否发动」"
    points = {modifier.point for modifier in definition.modifiers}
    assert points == {
        ModifierPoint.CANDIDATES,
        ModifierPoint.COUNT_LIMIT,
        ModifierPoint.TARGET_RANGE_EXEMPT,
    }
    assert WUYI in PROVIDERS, "① 的【闪】那一半走提供者面（`dodge` 窗口）"


def test_cuifeng_declares_the_round_limit_and_the_card_level_bonus() -> None:
    """摧锋：每轮限次 + 四条钩子 + 一个 `DAMAGE_DEAL_AMOUNT` 修饰器。"""
    definition = CATALOG[CUIFENG]
    assert definition.per_round_limit is True
    assert [hook.timing for hook in definition.hooks] == [
        Timing.TURN_END,
        Timing.PLAY_ON_PLAY,
        Timing.TURN_START,
        Timing.RESOLVE_AFTER_END,
    ]
    assert {modifier.point for modifier in definition.modifiers} == {
        ModifierPoint.DAMAGE_DEAL_AMOUNT
    }


# ---------------------------------------------------------------------------
# 武翊：X 的牌名口径（Q1）
# ---------------------------------------------------------------------------


def _card(pack, type_id: str) -> Any:
    for card in pack.deck.cards:
        if card.type == type_id:
            return card
    raise AssertionError(f"{type_id} 不在牌堆里")


def test_wuyi_counts_only_four_names_and_merges_attribute_slashes() -> None:
    """Q1 = A：X 只数【杀】【闪】【桃】【酒】；火【杀】/ 雷【杀】并入【杀】。"""
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "slash")) == "slash"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "fire_slash")) == "slash"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "thunder_slash")) == "slash"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "dodge")) == "dodge"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "peach")) == "peach"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "wine")) == "wine"
    assert _wuyi_name_of(PACK_160, _card(PACK_160, "duel")) is None, "锦囊不算"


# ---------------------------------------------------------------------------
# 武翊：②③ 的门槛（查询期）
# ---------------------------------------------------------------------------


def _wuyi_state(*, names: tuple[str, ...], seats: int = 3) -> GameState:
    specs = [SeatSpec(hero=HERO)]
    specs.extend(SeatSpec() for _ in range(seats - 1))
    state = build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).wuyi_names = names
    return state


def test_wuyi_second_and_third_tiers_are_query_time_gates() -> None:
    """② 无次数限制（X≥2）与 ③ 无距离限制（X≥3）都是**查询期**现算。"""
    slash = pure_virtual_card(_wuyi_state(names=()), "slash")
    for names, expect_tier2, expect_tier3 in (
        (("slash",), False, False),
        (("slash", "dodge"), True, False),
        (("slash", "dodge", "peach"), True, True),
    ):
        state = _wuyi_state(names=names)
        ctx = ModifierContext(subject=0, owner=0, card=slash)
        assert (_wuyi_unlimited_basics(state, ctx, 1) is None) is expect_tier2
        exempt = _wuyi_range_exempt(state, ctx, ())
        assert (len(exempt) == 3) is expect_tier3


def test_wuyi_second_tier_lifts_the_slash_use_limit() -> None:
    """②：X ≥ 2 之后同一回合能连续出【杀】（引擎的 `check_count` 读同一条修饰器）。"""
    state = _wuyi_state(names=("slash",))
    slash = pure_virtual_card(state, "slash")
    state.player(0).card_use_counts["slash"] = 1
    assert check_count(state, 0, slash) is True, "X = 1 时照常受 1 张的限制"
    state.player(0).wuyi_names = ("slash", "dodge")
    assert check_count(state, 0, slash) is False, "X ≥ 2 之后无次数限制"


def test_wuyi_third_tier_lets_the_virtual_slash_reach_anyone() -> None:
    """③：X ≥ 3 之后目标池不再受距离限制（求值点就在 `check_targets` 的 ②）。"""
    state = _wuyi_state(names=("slash", "dodge"))
    slash = pure_virtual_card(state, "slash")
    near = check_targets(state, 0, slash)
    state.player(0).wuyi_names = ("slash", "dodge", "peach")
    far = check_targets(state, 0, slash)
    assert set(near) <= set(far)
    assert set(far) == {1, 2}, "三座全在攻击范围内"


# ---------------------------------------------------------------------------
# 摧锋：发动门槛（Q6）与每轮限两次
# ---------------------------------------------------------------------------


class _Frame:
    def __init__(self, **data: Any) -> None:
        self.data = dict(data)


class _Host:
    def __init__(self, state: GameState) -> None:
        self.state = state


def _cuifeng_state(*, round_uses: int = 0) -> GameState:
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=1,
        phase=Phase.PLAY,
    )
    if round_uses:
        from sgs.skills.catalog import instance_key, resolve_instances

        for instance in resolve_instances(state):
            if instance.skill_id == CUIFENG and instance.owner == 0:
                state.round_use_counts[instance_key(instance)] = round_uses
    return state


def test_cuifeng_is_a_gate_on_my_own_turn_end() -> None:
    """Q6 = B：触发面看**每个**回合末，但当前回合角色是自己时不提供候选。"""
    from sgs.skills.catalog import MATCH_OWNER_KEY

    state = _cuifeng_state()
    host = _Host(state)
    other_turn = _Frame(**{MATCH_OWNER_KEY: 0, "source": 1})
    own_turn = _Frame(**{MATCH_OWNER_KEY: 0, "source": 0})
    assert _cuifeng_condition(host, other_turn) is True, "别人的回合末可以发动"
    assert _cuifeng_condition(host, own_turn) is False, "自己的回合末是门槛"


def test_cuifeng_stops_after_two_in_a_round() -> None:
    """「每轮限两次」：第三次同一轮不再提供候选。"""
    from sgs.skills.catalog import MATCH_OWNER_KEY

    host = _Host(_cuifeng_state(round_uses=2))
    frame = _Frame(**{MATCH_OWNER_KEY: 0, "source": 1})
    assert _cuifeng_condition(host, frame) is False


# ---------------------------------------------------------------------------
# 摧锋：+X 的卡牌级与动态（Q7 / Q8）
# ---------------------------------------------------------------------------


def test_cuifeng_bonus_is_card_level_and_live() -> None:
    """+X 只认**那张牌**，且 X 每次现读（`cui_feng_plays`）。"""
    state = _cuifeng_state()
    duel = pure_virtual_card(state, "duel")
    state.player(0).cui_feng_duel = int(duel.card_id)
    state.player(0).cui_feng_plays = 4

    # 赵云是来源（决斗赢的那一侧）⇒ 加。
    assert _cuifeng_damage_bonus(
        state, ModifierContext(subject=0, target=1, owner=0, card=duel), 1
    ) == 5
    # 赵云是**受伤**那一侧、来源是对手（`subject=1`）⇒ 同样加（卡牌级）。
    assert _cuifeng_damage_bonus(
        state, ModifierContext(subject=1, target=0, owner=0, card=duel), 1
    ) == 5
    # X 变了立刻跟着变（动态）。
    state.player(0).cui_feng_plays = 1
    assert _cuifeng_damage_bonus(
        state, ModifierContext(subject=0, target=1, owner=0, card=duel), 1
    ) == 2
    # 别的牌不吃这张记账。
    other = pure_virtual_card(state, "slash")
    assert _cuifeng_damage_bonus(
        state, ModifierContext(subject=0, target=1, owner=0, card=other), 1
    ) == 1


# ---------------------------------------------------------------------------
# 武翊 ① 的【闪】窗口（Q2）：只认"使用【闪】"那一问，不替玩家"打出【闪】"
# ---------------------------------------------------------------------------


def _dodge_window_offers(type_id: str) -> list[Any]:
    """让座位 1 对座位 0 使用一张 ``type_id``，回座位 0 那一问的候选（技能 id 列表）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=1,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    state.player(0).wuyi_names = ("slash",)
    engine = Engine.from_state(state, seed=21)
    engine.start_use(pure_virtual_card(state, type_id), source=1, targets=(0,))

    class _Pass:
        def respond(self, query):
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]

    players = [_Pass(), _Pass()]
    for _ in range(120):
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        query = engine.pending
        offers = [
            getattr(action, "skill_id", None)
            for action in query.legal_actions
            if isinstance(action, ActivateSkill)
        ]
        if query.actor == 0 and offers:
            return offers
        engine.submit(players[query.actor].respond(query))
    return []


def test_wuyi_is_offered_when_a_slash_asks_for_a_used_dodge() -> None:
    """① 的响应面：被【杀】要求**使用**【闪】时点得出武翊（唯一的响应窗口）。"""
    assert "hero.shi_zhaoyun.wuyi" in _dodge_window_offers("slash")


@pytest.mark.parametrize("type_id", ("arrow_barrage", "barbarian_invasion", "duel"))
def test_wuyi_does_not_answer_play_card_windows(type_id: str) -> None:
    """反例：牌面写「视为**使用**」⇒ 万箭 / 南蛮 / 决斗的"**打出**"窗口不参与。

    用户 2026-11-18 复核逐字：「武翊确实只能使用而非打出」（先按"也该能打出"放开过四扇，
    同日回退）。
    """
    assert "hero.shi_zhaoyun.wuyi" not in _dodge_window_offers(type_id)


def test_wuyi_provider_only_answers_slash_or_dodge_requests() -> None:
    """反例：窗口要的牌名不是【杀】/【闪】（求桃 / 无懈）⇒ 不参与（产物牌名收口）。"""
    from sgs.skills.active import _wuyi_dodge_payable

    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    state.player(0).wuyi_names = ("slash",)
    ctx = ModifierContext(subject=0, owner=0, scope=CandidateScope.DODGE)
    assert _wuyi_dodge_payable(state, ctx, "dodge") is True
    assert _wuyi_dodge_payable(state, ctx, "slash") is False, "打出【杀】的窗口不参与"
    assert _wuyi_dodge_payable(state, ctx, "peach") is False
    played = ModifierContext(
        subject=0, owner=0, scope=CandidateScope.DODGE, respond_mode=RespondMode.PLAY
    )
    assert _wuyi_dodge_payable(state, played, "dodge") is False, "万箭的「打出【闪】」不参与"


# ---------------------------------------------------------------------------
# 引擎级：武翊 ④ 把手牌数拉回 4（Q5）
# ---------------------------------------------------------------------------


class _Script:
    """最小脚本：出牌阶段打一张【杀】（若合法）、其余放弃 / 结束阶段。"""

    def __init__(self, *, slash_target: int | None = None, killer: bool = False) -> None:
        self.slash_target = slash_target
        self.killer = killer
        self.play_queries = 0

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION":
            if getattr(query, "skill_id", None) == CUIFENG:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                    and action.skill_id == CUIFENG
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "PLAY_PHASE":
            self.play_queries += 1
            if self.slash_target is not None and self.play_queries == 1:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "slash":
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if kind == "SELECT_CARDS":
            for action in query.legal_actions:
                if isinstance(action, SelectCards):
                    return action
            return Pass()
        if kind == "RESPOND":
            for action in query.legal_actions:
                if isinstance(action, PlayCard) and action.card.type == "slash":
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return next(
            (
                action
                for action in query.legal_actions
                if not isinstance(action, PlayCard)
            ),
            query.legal_actions[0],
        )


def _finish_turn(engine, players, *, turns: int = 1):
    """跑到 ``turns`` 个完整回合（``TURN_AFTER_END``）为止（与【镇骨】组的驱动同一条）。"""

    def stop(e) -> bool:
        return sum(
            1
            for record in e.records
            if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
        ) >= turns

    run_queries(engine, players, stop)


# ---------------------------------------------------------------------------
# 实机报障：两套"产生【杀】"的技能同时在场时，转化清单的步骤预览把界面拖进死路
# ---------------------------------------------------------------------------


def _play_phase_payload(*, with_serpent_spear: bool):
    """出牌阶段的第一问 + 网页载荷（武翊已解锁；可选装【丈八蛇矛】多一份转化清单）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from sgs_web.projection import pending_payload  # noqa: E402

    hand = cards_of_type(PACK, "dodge")[:2]
    equip = (cards_of_type(PACK, "serpent_spear")[0],) if with_serpent_spear else ()
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=tuple(hand), equip=equip),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, *hand, *equip),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).wuyi_names = ("slash",)
    engine = Engine.from_state(state, seed=31)
    players = [_Script(), _Script()]
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))
    return engine, pending_payload(engine.pending, human_seat=0)


def test_preview_flag_is_off_when_a_conversion_manifest_shares_the_query() -> None:
    """实机报障（2026-11-18）：【武翊】的清单 + 【丈八蛇矛】的转化清单同在出牌阶段时，载荷曾
    按 any 声明 `preview`（有一份有流水线就置真）⇒ 点【丈八蛇矛】发一次预览、端点按"没有
    预发动清单"拒掉、界面落到「取不到候选，请重试」。判据必须是 **all**：这一问下发的每份
    清单都有流水线才声明。"""
    engine, payload = _play_phase_payload(with_serpent_spear=True)
    assert engine.pending is not None
    assert "preview" not in payload, "混合了没有流水线的转化清单 ⇒ 整问都不该声明步骤预览"


def test_preview_flag_stays_on_when_every_manifest_is_flow_backed() -> None:
    """正面控制：同一局面**不装**丈八（只剩武翊那份有流水线的清单）⇒ 照旧声明 `preview`。"""
    engine, payload = _play_phase_payload(with_serpent_spear=False)
    assert engine.pending is not None
    assert payload.get("preview") is True


class _RecastScript:
    """最小脚本：出牌阶段**重铸**指定的那张牌；`SELECT_CARDS` 按面挑 ``picks`` 张。"""

    def __init__(self, *, reforge_card: int | None = None, picks: int = 1) -> None:
        self.reforge_card = reforge_card
        self.picks = picks
        self.discard_queries: list[Any] = []
        self.probe: Any = None
        """可选回调：在**应答之前**看一眼这一问（`query_context` 只在挂起期间可算）。"""

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "PLAY_PHASE":
            if self.reforge_card is not None:
                for action in query.legal_actions:
                    if isinstance(action, Reforge) and action.card.card_id == self.reforge_card:
                        self.reforge_card = None
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if kind == "SELECT_CARDS":
            self.discard_queries.append(query)
            if self.probe is not None:
                self.probe(query)
            for action in query.legal_actions:
                if isinstance(action, SelectCards) and len(action.picks) == self.picks:
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, SelectCards)
            )
        if kind == "DISCARD":
            self.discard_queries.append(query)
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, DiscardCards) and len(action.cards) == query.count
            )
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _run_turn(engine, players, *, turns: int = 1):
    def stop(e) -> bool:
        return sum(
            1
            for record in e.records
            if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
        ) >= turns

    run_queries(engine, players, stop)


def test_wuyi_fixes_the_hand_again_after_the_reforge_draw() -> None:
    """实机报障（用户 2026-11-18）：重铸依次做"失去一张 → 摸一张"，④ 在同一帧里要回填**两次**。

    手里 4 张（含【铁索连环】）⇒ 重铸失去它（3）→ ④ 补到 4 → 重铸自己摸 1（5）→ ④ 必须
    再问一次"将手牌数调整为4张"。旧实现在同一条帧里复用了第一次的询问答案（那几张牌早已
    不在手里），表现为"手牌 5 了却没有弃牌询问"。
    """
    recast = cards_of_type(PACK_160, "iron_chain")[0]
    others = cards_of_type(PACK_160, "dodge")[:3]
    state = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=HERO, hand=(recast, *others)), SeatSpec()],
        deck=deck_excluding(PACK_160, recast, *others),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=61)
    script = _RecastScript(reforge_card=recast)
    contexts: list[dict[str, Any]] = []

    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from sgs_web.projection import query_context  # noqa: E402

    script.probe = lambda query: contexts.append(query_context(engine, query) or {})
    players = [script, _Script()]
    # 摸牌阶段会先把牌补到 6 张——那一段与本票无关（会先触发一次④）。故等进到出牌阶段，
    # 再把"已解锁 + 手牌恰 4 张（含铁索）"摆好。
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))
    state.player(0).wuyi_names = ("slash", "dodge", "peach", "wine")
    state.player(0).hand = [recast, *others]
    # 重铸在第一次出牌阶段询问里打出；它的两次④询问由脚本自己答（每次弃 1 张），
    # 跑完回到出牌阶段。
    engine.submit(script.respond(engine.pending))
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))

    assert len(script.discard_queries) == 1, "重铸的摸牌之后必须再问一次"
    assert script.discard_queries[0].spec.total == (1, 1)
    assert len(engine.state.player(0).hand) == 4, "弃回 4 张"
    # 询问文案取技能的分步文案（用户 2026-11-18 逐字：不要"从选择面里选择要交出的牌"）
    assert contexts, "选牌询问应当被记录"
    assert contexts[0]["requirement"] == "将手牌数调整为4张", contexts[0]


def test_the_discard_phase_asks_once_and_does_not_refill_chase() -> None:
    """实机报障（用户 2026-11-18）：弃牌阶段只做一次"弃置至手牌上限"，不因④回填而反复问。

    局面：体力 3（手牌上限 3）、武翊已解锁第 4 档。摸牌阶段拿到 4 张 ⇒ 弃 1 张；④ 立刻把
    手牌补回 4 —— 旧实现按"计数仍 > 上限"再问一次，于是摸一张弃一张无限循环。
    """
    hand = cards_of_type(PACK, "dodge")[:2]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=tuple(hand), hp=3, hp_max=3),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).wuyi_names = ("slash", "dodge", "peach", "wine")
    engine = Engine.from_state(state, seed=62)
    script = _RecastScript()
    players = [script, _Script()]
    _run_turn(engine, players)

    assert len(script.discard_queries) == 1, "弃牌阶段只问一次"
    assert len(engine.state.player(0).hand) == 4, "弃一张后 ④ 补回 4"


def _is_kind(pending, name: str) -> bool:
    return pending is not None and str(
        getattr(pending.kind, "value", pending.kind)
    ) == name


def _play_one_slash_from_hand(*, names: tuple[str, ...], seed: int) -> Any:
    """把局面前推到出牌阶段的第一问，再把"4 张手牌（含一张【杀】）"摆好并打出那张【杀】。

    为什么要**手动摆牌**：引擎从回合起点跑起（准备 / 判定 / 摸牌），摸牌阶段会先给 2 张；
    本组要钉的是"用掉一张之后手牌数变成几"，故等出牌阶段到齐了再把牌面摆成确定值。
    返回**打完这张【杀】、回到出牌阶段**的引擎（此时 ④ 的回填已经跑完）。
    """
    slash = cards_of_type(PACK, "slash")[0]
    fillers = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].type != "slash"
    ][:3]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=(slash, *fillers)),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, slash, *fillers),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=seed)
    players = [_Script(slash_target=1), _Script()]
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))

    state.player(0).hand = [slash, *fillers]
    state.player(0).wuyi_names = names
    players[0].play_queries = 0
    engine.submit(players[0].respond(engine.pending))
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))
    return engine


def test_wuyi_restores_the_hand_to_four_after_a_use() -> None:
    """④：X ≥ 4 时，使用一张手牌后手牌数立刻回到 4（从牌堆补 1 张）。"""
    engine = _play_one_slash_from_hand(
        names=("slash", "dodge", "peach", "wine"), seed=11
    )
    assert len(engine.state.player(0).hand) == 4, "用掉 1 张后立刻补回 4"


def test_wuyi_does_not_touch_the_hand_before_the_fourth_name() -> None:
    """反例：X < 4 时④不生效——用掉一张就是 3 张。"""
    engine = _play_one_slash_from_hand(names=("slash",), seed=12)
    assert len(engine.state.player(0).hand) == 3, "没解锁第 4 档就不回填"
    assert engine.state.player(0).wuyi_names == ("slash",), "牌名不重复记"


# ---------------------------------------------------------------------------
# 引擎级：摧锋的伤害 = 1 + X（Q7 / Q8 的逐字场景）
# ---------------------------------------------------------------------------


def test_cuifeng_duel_deals_one_plus_the_live_slash_count() -> None:
    """用户注意 2 的逐字场景：本回合已有 2 张打出，决斗里双方各打出 1 张 ⇒ 1+4 = 5 点。"""
    from sgs.skills.catalog import MATCH_OWNER_KEY

    slashes = cards_of_type(PACK, "slash")[:3]
    state = build_state(
        pack=PACK,
        # 座位 0 = 势赵云（回合外）；座位 1 = 当前回合角色（6 体力，挨 5 点后还剩 1）。
        seats=[
            SeatSpec(hero=HERO, hand=tuple(slashes[:2])),
            SeatSpec(hp=6, hp_max=6, hand=(slashes[2],)),
        ],
        deck=deck_excluding(PACK, *slashes),
        turn_seat=1,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    engine = Engine.from_state(state, seed=13)
    players = [_Script(), _Script()]
    # 先走到当前回合的出牌阶段（`TURN_START` 的簿记在它之前就跑完了、会把计数清 0），
    # 再摆"本回合此前已打出 2 张【杀】"。
    run_queries(engine, players, lambda e: _is_kind(e.pending, "PLAY_PHASE"))
    engine.state.player(0).cui_feng_plays = 2
    _finish_turn(engine, players)

    assert engine.state.player(1).hp == 1, "1（基础）+ 4（本回合打出）= 5 点"
    assert engine.state.player(0).cui_feng_duel is None, "一次使用结算完就清账"


def test_cuifeng_is_not_offered_on_my_own_turn_end_in_a_real_turn() -> None:
    """Q6 = B 的引擎级对照：自己的回合末**没有**这条「是否发动」询问。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=14)
    players = [_Script(), _Script()]
    _finish_turn(engine, players)

    asked = [
        record
        for record in engine.records
        if record.kind == "query"
        and getattr(record.query, "skill_id", None) == CUIFENG
    ]
    assert asked == [], "自己的回合末不提供候选"


def test_the_wuyi_badge_shows_the_unlocked_name_count() -> None:
    """Q10：座位标签「武翊 X」常驻，X = 0 也画（与【勤政】同一条 fail-closed 例外）。"""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from sgs_web.projection import BADGE_PROVIDERS  # noqa: E402

    builder = BADGE_PROVIDERS[WUYI][0][1]
    state = _wuyi_state(names=())
    assert builder(state, 0) == {"tag": "wuyi", "label": "武翊 0"}
    state.player(0).wuyi_names = ("slash", "dodge")
    assert builder(state, 0) == {"tag": "wuyi", "label": "武翊 2"}
