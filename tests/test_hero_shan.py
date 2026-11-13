"""山包·左慈【化身】/【新生】（shan-baozuoci 票 04）的构造局面单测。

牌面（用户 `技能.txt`，OL 现行，逐字见 `src/sgs/skills/zuoci.py`）；结算按官方规则集
2018.3-16 左慈条 + 用户 2026-11-13 的五条裁定：

- 技能范围 = 无标签技能 + 锁定技（限定 / 觉醒 / 主公除外）；
- 化身② 只从手里的化身牌重选；只有【新生】加牌；
- 性别 / 势力随当前化身牌；失去【化身】回男 / 群；
- 化身牌只有自己可见（本文件只断言状态，展示面在票 05）；
- 换化身 = 可逆失去旧技能 + 获得新技能（不写 `lost_skills`）。

外部缝：询问载荷（`SELECT_OPTION` 的 options / `option_texts`）、投影、记录流、引擎状态。
"""

from __future__ import annotations

from types import SimpleNamespace

from sgs.data import RuleConfig, hero_by_id, load_content_pack
from sgs.engine import Engine
from sgs.skills import resolve_instances
from sgs.skills import zuoci as zuoci_module
from sgs.state import ActivateSkill, Pass, Phase, QueryKind, SelectOption
from sgs.timing import Timing

from support import (
    PassPlayer,
    declare_death,
    find_action,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")

ZUOCI = "zuoci"
HUASHEN = "hero.zuoci.huashen"
XINSHENG = "hero.zuoci.xinsheng"


class _ZuociPlayer:
    """答左慈的化身询问：``picks`` 逐问给出 ``"first"`` / ``"current"`` / 技能 id 后缀。"""

    def __init__(self, picks: tuple[str, ...] = ()) -> None:
        self.picks = list(picks)
        self.current: str | None = None
        self.queries: list = []
        self._fallback = PassPlayer()

    def respond(self, query):
        task = str(getattr(query, "skill_id", None) or "")
        if query.kind == QueryKind.SELECT_OPTION and task.startswith(
            ("huashen", HUASHEN)
        ):
            index = len(self.queries)
            self.queries.append(query)
            pick = self.picks[index] if index < len(self.picks) else "first"
            return self._choose(query, pick)
        if query.kind == QueryKind.SKILL_ACTIVATION:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == XINSHENG:
                    return action
        return self._fallback.respond(query)

    def _choose(self, query, pick: str):
        options = [
            action for action in query.legal_actions if isinstance(action, SelectOption)
        ]
        if pick == "current":
            return find_action(
                query,
                lambda action: isinstance(action, SelectOption)
                and action.choice == self.current,
            )
        if pick == "pass":
            return find_action(query, lambda action: isinstance(action, Pass))
        if pick.startswith("."):
            return find_action(
                query,
                lambda action: isinstance(action, SelectOption)
                and str(action.choice).endswith(pick),
            )
        if pick == "first":
            return options[0]
        return find_action(
            query, lambda action: isinstance(action, SelectOption) and action.choice == pick
        )


def _engine(*, seed: int = 7, seats: int = 2) -> Engine:
    """真开局（含起始手牌）之后、第一次 ``_step`` 之前：左慈在 0 号位，其余无武将。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=seats),
        pack=PACK,
        pinned_hero=ZUOCI,
        pinned_seat=0,
        hero_pool=(),
    )


def _run(engine: Engine, players, predicate) -> Engine:
    return run_queries(engine, players, predicate)


def _queries(engine: Engine, suffix: str) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and str(getattr(record.query, "skill_id", None) or "").endswith(suffix)
    ]


def _hero_of(state, skill_id: str):
    """某个化身技能属于哪张武将牌（在手里那几张里找）。"""
    for card in state.player(0).avatar_cards:
        hero = hero_by_id(card)
        if hero is not None and any(
            f"hero.{hero.id}.{item.id}" == skill_id for item in hero.skills
        ):
            return hero
    return None


def _start_game(*, seed: int = 7):
    """跑到第一回合开始前：化身① 已结算完（抽两张 + 选一个技能）。"""
    engine = _engine(seed=seed)
    player = _ZuociPlayer()
    _run(engine, [player, PassPlayer()], lambda e: ran_timing(e, Timing.TURN_BEFORE_START))
    return engine, player


def _turn_start_settled(engine: Engine) -> bool:
    """跑到准备阶段：回合开始时的化身② 已经问完并结算（问句在 TURN_START 的派发里）。"""
    return ran_timing(engine, Timing.TURN_START) and engine.state.phase is Phase.PREPARE


def test_zuoci_draws_two_avatar_cards_and_takes_a_skill_at_game_start():
    engine, player = _start_game()

    assert len(engine.state.player(0).avatar_cards) == 2, "开局抽两张化身牌"
    assert len(player.queries) == 1, "化身① 只问一次（选技能）"
    assert engine.state.player(0).granted_skills, "选中的技能进了授予表"
    chosen = engine.state.player(0).granted_skills[-1]
    assert any(
        instance.owner == 0 and instance.skill_id == chosen
        for instance in resolve_instances(engine.state)
    ), "视为拥有的技能真的产出了实例"


def test_zuoci_takes_the_identity_of_the_chosen_avatar_card():
    engine, _player = _start_game()
    chosen = engine.state.player(0).granted_skills[-1]
    hero = _hero_of(engine.state, chosen)
    assert hero is not None, f"选中的技能 {chosen} 必须来自手里某张化身牌"
    assert engine.state.player(0).gender == hero.gender
    assert engine.state.player(0).kingdom == hero.kingdom


def test_zuoci_only_offers_untagged_and_locked_skills():
    """选项闭集：每一条都是非 ☆ 且 `skill_type == NORMAL`（锁定技在列、限定 / 觉醒 / 主公除外）。"""
    engine, player = _start_game()
    options = [
        action.choice
        for action in player.queries[0].legal_actions
        if isinstance(action, SelectOption)
    ]
    assert options
    for skill_id in options:
        hero = _hero_of(engine.state, skill_id)
        assert hero is not None
        short = skill_id.rsplit(".", 1)[-1]
        entry = next(item for item in hero.skills if item.id == short)
        assert entry.granted_by is None, "☆ 技能不在化身牌的技能清单里"
        assert entry.skill_type.value == "NORMAL", "限定 / 觉醒 / 主公不进化身选项"


def test_zuoci_reselects_without_losing_the_skill_for_good():
    engine, player = _start_game()
    current = engine.state.player(0).granted_skills[-1]
    other = None
    # 找一个与当前不同的选项（第二张牌的技能也可能只有一个，故扫全量）。
    for action in player.queries[0].legal_actions:
        if isinstance(action, SelectOption) and action.choice != current:
            other = action.choice
            break
    assert other is not None, "构造前提：至少还有另一个合格技能可选"

    player.current = current
    player.queries.clear()
    player.picks = [other]
    _run(engine, [player, PassPlayer()], _turn_start_settled)

    assert player.queries[-1].skill_id.endswith(":skill"), "回合开始时的重新化身询问"
    assert other in engine.state.player(0).granted_skills
    assert current not in engine.state.player(0).granted_skills, "旧技能被可逆失去"
    assert engine.state.player(0).lost_skills == [], "可逆失去不写 lost_skills"


def test_zuoci_reselecting_the_same_skill_is_a_no_op():
    engine, player = _start_game()
    current = engine.state.player(0).granted_skills[-1]
    player.current = current
    player.queries.clear()
    player.picks = ["current"]
    before = list(engine.state.player(0).granted_skills)
    _run(engine, [player, PassPlayer()], _turn_start_settled)

    assert engine.state.player(0).granted_skills == before, "选同一条 ⇒ 零动作"
    assert not [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "skill_lose"
    ]


def test_zuoci_does_not_ask_again_when_it_holds_no_avatar_cards():
    """用户 2026-11-13：**只有手里没有化身牌**才不问；其余情况都问。"""
    engine, player = _start_game()
    engine.state.player(0).avatar_cards.clear()

    _run(engine, [player, PassPlayer()], _turn_start_settled)

    assert not _queries(engine, ":skill"), "库空 ⇒ 连问都不发"


def test_xinsheng_adds_one_avatar_card_and_needs_huashen():
    engine, _player = _start_game()
    frame = SimpleNamespace(data={"skill_owner": 0})
    before = len(engine.state.player(0).avatar_cards)

    zuoci_module._xinsheng_effect(engine, frame, {})

    assert len(engine.state.player(0).avatar_cards) == before + 1, "新生加一张"

    probe = SimpleNamespace(data={zuoci_module.MATCH_OWNER_KEY: 0, "target": 0})
    assert zuoci_module._xinsheng_condition(engine, probe) is True
    probe.data[zuoci_module.MATCH_OWNER_KEY] = 1
    assert zuoci_module._xinsheng_condition(engine, probe) is False, "别人挨打不触发"

    engine.lose_skill(0, HUASHEN)
    probe.data[zuoci_module.MATCH_OWNER_KEY] = 0
    assert zuoci_module._xinsheng_condition(engine, probe) is False, "没有化身就不问"


def test_a_dead_zuoci_releases_his_avatar_cards():
    engine, player = _start_game()
    held = tuple(engine.state.player(0).avatar_cards)
    assert held
    blocked_before = set(engine.state.drawn_true_names)
    assert blocked_before

    declare_death(engine, 0)
    _run(engine, [player, PassPlayer()], lambda e: e.finished)

    assert engine.state.player(0).avatar_cards == [], "死亡 ⇒ 化身库清空"
    assert engine.state.drawn_true_names == (), "化身牌回堆：真名解除禁用"
