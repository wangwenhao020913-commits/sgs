"""M6 界局金录像（票 20）：**构造局面 + 脚本驱动**，覆盖 M6 的新原语。

与 M5 的军争构造基准同一条姿态（`replays/m5.py`）：局面（逐座位武将 / 手牌 / 牌堆顶）
写死在代码里，从 ``Engine.from_state`` 起跑；计划 = 若干「哪个座位、在哪种询问里、挑哪条
候选」的步骤，录下来的仍是引擎放行的那条候选。回放侧用同一份构造重新开局
（``tests/test_replays.py`` → :func:`replays.scenarios.engine_for`）。

五条基准各覆盖 spec §投影与记录流 的一类事实：

=====  ============================  ===============================================
slug   覆盖的原语                    记录流里的判据（``goals.M6_GOALS``）
=====  ============================  ===============================================
56     技能**失效**（施加 / 到期）     ``skill_suppress``（True 与 False 各一条）
57     技能**获得** + 体力上限（觉醒） ``hp_max_change`` + ``skill_gain`` + ☆ 技能被发动
58     **武将牌上的牌区**（放置 / 归还）``card_move`` 的 HERO 区域 + 【连营】的选项询问
59     **额外回合 / 轮次**             ``turn_change``（``extra=True`` 与 ``False`` 各一条）
60     标签**增删**（洛神式不计入）     ``tag_add`` + ``tag_remove``（同一张牌）
=====  ============================  ===============================================

**技能失去**（``skill_lose``）在 M6 没有载体（没有"令其失去技能"的技能，只有引擎写入口
与构造测试），故不在这里造假事件——它由 ``tests/test_hero_skill_state.py`` 覆盖。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sgs.data import ContentPack, Gender, RuleConfig
from sgs.engine import Engine
from sgs.state import (
    Action,
    ActivateSkill,
    DiscardCards,
    EndPhase,
    GameState,
    Pass,
    Query,
    SelectCards,
    SelectOption,
    SelectOptions,
    UseCard,
    Zone,
)
from support import SeatSpec, build_state

from .goals import M6_GOALS

__all__ = [
    "M6_DECK_PROFILE",
    "M6_SETUPS",
    "M6Player",
    "build_engine",
    "build_players",
    "plan_for",
]

M6_DECK_PROFILE = "standard_108"
"""M6 的 5 条构造基准走标准 108：被覆盖的原语与新增牌名无关，短录像更利于门禁回放。"""


# ---------------------------------------------------------------------------
# 计划：步骤 = 「哪个座位、在哪种询问里、挑哪条候选」
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """计划里的一步：座位 + 询问种类 + 候选谓词（或**组装器**）。"""

    actor: int
    kind: str
    matches: Callable[[Query, Action], bool] | None = None
    label: str = ""
    build: Callable[[Query], Action] | None = None
    """``build`` = 这一步的答案由**计划自己组装**（不枚举档的 ``SELECT_OPTIONS``）。

    座位多选恒走不枚举档（table-ux-overhaul 票 13）：候选里只有一条骨架，真正的答案
    （一个座位集合）由客户端拼出来、引擎按同一份形状谓词校验。故这一档的计划给的是
    构造器而不是"从 ``legal_actions`` 里挑"的谓词——录下来的仍是引擎放行的那条应答
    （``submit`` 的 ``structural`` 那一道闸）。"""


@dataclass
class Progress:
    """所有座位共享的步骤进度（一条基准一局）。"""

    done: set[int] = field(default_factory=set)

    def complete(self, index: int, total: int) -> bool:
        self.done.add(index)
        return len(self.done) == total


@dataclass(frozen=True)
class Plan:
    """一条构造基准：逐座位局面 + 目标段步骤。"""

    seats: tuple[SeatSpec, ...]
    steps: tuple[Step, ...]
    deck_top: tuple[int, ...] = ()
    """牌堆顶的固定顺序（其前两张被座位 0 的首回合摸牌拿走）。"""


def _ids(pack: ContentPack, type_id: str) -> list[int]:
    return [index for index, card in enumerate(pack.deck.cards) if card.type == type_id]


def _one(pack: ContentPack, type_id: str) -> int:
    ids = _ids(pack, type_id)
    assert ids, f"standard_108 里没有 {type_id}"
    return ids[0]


def _suit(pack: ContentPack, suit, *, exclude: set[int]) -> int:
    return next(
        index
        for index, card in enumerate(pack.deck.cards)
        if card.suit is suit and index not in exclude
    )


def _full_deck(pack: ContentPack, *, used: set[int], top: Sequence[int] = ()) -> tuple[int, ...]:
    """牌守恒：``top`` 在前，其余未用到的牌按数据表顺序补齐。"""
    reserved = set(top) | set(used)
    return (*top, *(i for i in range(len(pack.deck.cards)) if i not in reserved))


def _rules(player_count: int) -> RuleConfig:
    return RuleConfig(player_count=player_count, first_seat=0)


# ---------------------------------------------------------------------------
# 候选谓词（全部「从 legal_actions 里挑」，不拼动作）
# ---------------------------------------------------------------------------


def _use(type_id: str, *, targets: tuple[int, ...] | None = None) -> Callable[[Query, Action], bool]:
    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            return False
        return targets is None or action.targets == targets

    return matches


def _end_phase(query: Query, action: Action) -> bool:
    return isinstance(action, EndPhase)


def _activate(skill_id: str) -> Callable[[Query, Action], bool]:
    def matches(query: Query, action: Action) -> bool:
        return isinstance(action, ActivateSkill) and action.skill_id == skill_id

    return matches


def _select_card(card_id: int) -> Callable[[Query, Action], bool]:
    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, SelectCards) or len(action.picks) != 1:
            return False
        slot = query.slots[action.picks[0]]
        return slot.card is not None and slot.card.card_id == card_id

    return matches


def _pass(query: Query, action: Action) -> bool:
    return isinstance(action, Pass)


def _option(prefix: str, choice: str) -> Callable[[Query, Action], bool]:
    def matches(query: Query, action: Action) -> bool:
        return (
            isinstance(action, SelectOption)
            and str(getattr(query, "skill_id", "")).startswith(prefix)
            and action.choice == choice
        )

    return matches


def _option_seat(prefix: str, seat: int) -> Callable[[Query, Action], bool]:
    return _option(prefix, f"seat:{seat}")


def _seat_set(prefix: str, seats: Sequence[int]) -> Callable[[Query], Action]:
    """**组装**一次座位多选（票 13）：一次提交一个座位集合（不枚举档的合法答案）。"""

    def build(query: Query) -> Action:
        skill_id = str(getattr(query, "skill_id", ""))
        assert skill_id.startswith(prefix), f"座位多选询问的 skill_id 是 {skill_id!r}"
        return SelectOptions(
            choices=tuple(f"seat:{seat}" for seat in seats), answers_seq=query.seq
        )

    return build


def _seat_set_pass(prefix: str) -> Callable[[Query], Action]:
    """座位多选的「放弃」：``optional`` 那一问的 ``Pass``（Pass 不在骨架候选里）。"""

    def build(query: Query) -> Action:
        skill_id = str(getattr(query, "skill_id", ""))
        assert skill_id.startswith(prefix), f"座位多选询问的 skill_id 是 {skill_id!r}"
        return Pass(answers_seq=query.seq)

    return build


def _discard_four_suits(query: Query, action: Action) -> bool:
    """弃牌阶段的候选里挑**恰好覆盖四种花色**的那条（【博图】的条件）。"""
    if not isinstance(action, DiscardCards):
        return False
    return len({card.suit for card in action.cards}) == 4


# ---------------------------------------------------------------------------
# 5 条构造局面
# ---------------------------------------------------------------------------


def _pick(
    pack: ContentPack,
    *,
    type_id: str | None = None,
    suit: str | None = None,
    exclude: set[int],
) -> int:
    """按牌名 / 花色取一张未被占用的牌（数据表顺序，确定性）。"""
    for index, card in enumerate(pack.deck.cards):
        if index in exclude:
            continue
        if type_id is not None and card.type != type_id:
            continue
        if suit is not None and card.suit.value != suit:
            continue
        return index
    raise AssertionError(f"standard_108 里没有可用牌：type={type_id!r} suit={suit!r}")


def _plan_56(pack: ContentPack) -> Plan:
    """56：界关羽【义绝】弃一张牌 ⇒ 目标展示黑牌、技能**失效**；回合末**到期**."""
    slash = _pick(pack, type_id="slash", exclude=set())
    used = {slash}
    cost = _pick(pack, suit="club", exclude=used)
    used.add(cost)
    target_black = _pick(pack, suit="spade", exclude=used)
    used.add(target_black)
    target_other = _pick(pack, suit="heart", exclude=used)
    return Plan(
        seats=(
            SeatSpec(hp=4, hp_max=4, hero="jie_guanyu", gender=Gender.MALE, hand=(slash, cost)),
            SeatSpec(
                hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE,
                hand=(target_black, target_other),
            ),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _activate("hero.jie_guanyu.yijue"), label="发动【义绝】"),
            Step(0, "SELECT_CARDS", _select_card(cost), label="弃一张牌（成本）"),
            Step(0, "SELECT_OPTION", _option_seat("yijue:seat", 1), label="选目标座位 1"),
            Step(1, "SELECT_CARDS", _select_card(target_black), label="目标展示黑牌"),
            Step(0, "PLAY_PHASE", _end_phase, label="收手（本回合末失效到期）"),
            Step(1, "PLAY_PHASE", _end_phase, label="目标回合让先"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="【杀】收局"),
        ),
    )


def _plan_57(pack: ContentPack) -> Plan:
    """57：界徐庶造成伤害后【潜心】**觉醒**（上限 -1 + 获得 ☆【荐言】），随后发动【荐言】."""
    first = _pick(pack, type_id="slash", exclude=set())
    second = _pick(pack, type_id="slash", exclude={first})
    filler = _pick(pack, suit="club", exclude={first, second})
    used = {first, second, filler}
    deck_top = tuple(
        index for index in range(len(pack.deck.cards)) if index not in used
    )[:6]
    return Plan(
        seats=(
            SeatSpec(hp=3, hp_max=4, hero="jie_xushu", gender=Gender.MALE, hand=(first, second)),
            SeatSpec(hp=2, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=(filler,)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="造成伤害 ⇒ 觉醒"),
            Step(0, "PLAY_PHASE", _end_phase, label="本回合收手"),
            Step(1, "PLAY_PHASE", _end_phase, label="目标回合让先"),
            Step(0, "PLAY_PHASE", _activate("hero.jie_xushu.jianyan"), label="发动 ☆【荐言】"),
            Step(0, "SELECT_OPTION", _option("jianyan:", "basic"), label="选基本牌"),
            Step(0, "SELECT_OPTION", _option_seat("jianyan:seat", 0), label="令自己获得"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="【杀】收局"),
        ),
        deck_top=deck_top,
    )


def _plan_58(pack: ContentPack) -> Plan:
    """58：界陆逊【谦逊】把全部手牌**置于武将牌上** ⇒ 【连营】问令谁摸 ⇒ 回合末**归还**."""
    first = _pick(pack, type_id="slash", exclude=set())
    second = _pick(pack, type_id="slash", exclude={first})
    dismantle = _pick(pack, type_id="dismantlement", exclude={first, second})
    used = {first, second, dismantle}
    kill = _pick(pack, type_id="slash", exclude=used)
    return Plan(
        seats=(
            SeatSpec(hp=3, hp_max=3, hero="jie_luxun", gender=Gender.MALE,
                     hand=(first, second, kill)),
            SeatSpec(hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=(dismantle,)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="座位 0 让先"),
            Step(1, "PLAY_PHASE", _use("dismantlement", targets=(0,)), label="拆桥指向座位 0"),
            Step(0, "SKILL_ACTIVATION", _activate("hero.jie_luxun.qianxun"), label="发动【谦逊】"),
            Step(0, "SKILL_ACTIVATION", _activate("hero.jie_luxun.lianying"), label="发动【连营】"),
            # 票 13 起座位多选**一次问齐**：一个 SELECT_OPTIONS 里点齐两名角色（含自己）。
            Step(
                0,
                "SELECT_OPTIONS",
                build=_seat_set("lianying:", (0, 1)),
                label="连营一次点齐座位 0 / 1",
            ),
            # 同一帧里的第二次【连营】——答案缓存修正后它必须**重新发问**（票 20）：
            # 这一步与下一条同形，作为"真的又问了"的磁带证据。
            Step(
                0,
                "SELECT_OPTIONS",
                build=_seat_set_pass("lianying:"),
                label="第二次连营不再选人",
            ),
            Step(1, "PLAY_PHASE", _end_phase, label="座位 1 收手（回合末归还）"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="【杀】收局"),
        ),
    )


def _plan_59(pack: ContentPack) -> Plan:
    """59：【博图】四花色弃牌 ⇒ **额外回合**；再走一轮 ⇒ 轮次变化（turn_change 两条）."""
    suits: list[int] = []
    for suit in ("spade", "heart", "club", "diamond"):
        pick = _pick(pack, suit=suit, exclude=set(suits))
        suits.append(pick)
    slash = _pick(pack, type_id="slash", exclude=set(suits))
    return Plan(
        seats=(
            SeatSpec(hp=1, hp_max=4, hero="jie_lvmeng", gender=Gender.MALE,
                     hand=(*suits, slash)),
            # 座位 1 起手空、且首回合弃牌阶段没有牌可弃 ⇒ 两轮都只是让局面推进。
            SeatSpec(hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=()),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="不发动【克己】，进弃牌阶段"),
            Step(0, "DISCARD", _discard_four_suits, label="弃四花色"),
            Step(0, "SKILL_ACTIVATION", _activate("hero.jie_lvmeng.botu"), label="发动【博图】"),
            Step(0, "PLAY_PHASE", _end_phase, label="额外回合收手（不杀，等轮次）"),
            Step(1, "PLAY_PHASE", _end_phase, label="座位 1 收手"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="第 2 轮的【杀】收局"),
        ),
    )


def _plan_60(pack: ContentPack) -> Plan:
    """60：界甄姬【洛神】获得黑牌 ⇒ **挂标签**；回合末清除（tag_add + tag_remove）."""
    black = _pick(pack, suit="spade", exclude=set())
    cost = _pick(pack, suit="club", exclude={black})
    slash = _pick(pack, type_id="slash", exclude={black, cost})
    return Plan(
        seats=(
            SeatSpec(hp=3, hp_max=3, hero="jie_zhenji", gender=Gender.FEMALE,
                     hand=(cost, slash)),
            SeatSpec(hp=1, hp_max=4, hero="guanyu", gender=Gender.MALE, hand=()),
        ),
        steps=(
            Step(0, "SKILL_ACTIVATION", _activate("hero.jie_zhenji.luoshen"), label="发动【洛神】"),
            Step(0, "SELECT_OPTION", _pass, label="不重复（放弃）"),
            Step(0, "PLAY_PHASE", _end_phase, label="收手（本回合末清除标签）"),
            Step(1, "PLAY_PHASE", _end_phase, label="座位 1 让先"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="【杀】收局"),
        ),
        deck_top=(black,),
    )


M6_SETUPS: dict[str, Callable[[ContentPack], Plan]] = {
    "56-jie-guanyu-yijue-suppress": _plan_56,
    "57-jie-xushu-awakening": _plan_57,
    "58-jie-luxun-hero-zone": _plan_58,
    "59-jie-lvmeng-extra-turn": _plan_59,
    "60-jie-zhenji-luoshen-tags": _plan_60,
}
"""5 条 M6 界局构造基准：slug → 局面 / 步骤构造器。"""


def plan_for(slug: str, pack: ContentPack) -> Plan:
    return M6_SETUPS[slug](pack)


def _state_for(slug: str, pack: ContentPack) -> GameState:
    plan = plan_for(slug, pack)
    used = {
        card_id
        for seat in plan.seats
        for card_id in (*seat.hand, *seat.equip, *seat.judge)
    }
    state = build_state(
        pack=pack,
        seats=list(plan.seats),
        deck=_full_deck(pack, used=used, top=plan.deck_top),
        turn_seat=0,
        rule_config=_rules(len(plan.seats)),
    )
    return state


def build_engine(slug: str, *, seed: int, pack: ContentPack) -> Engine:
    """M6 基准的开局：从构造局面起跑（与回放侧同一份构造）。"""
    return Engine.from_state(_state_for(slug, pack), seed=seed)


# ---------------------------------------------------------------------------
# 脚本玩家
# ---------------------------------------------------------------------------


def _pass_or_first(query: Query) -> Action:
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class M6Player:
    """按 :class:`Plan` 应答的脚本玩家（录制期用；回放期一律 ``TapePlayer``）。"""

    def __init__(
        self,
        seat: int,
        slug: str,
        plan: Plan,
        progress: Progress,
        recording=None,
    ) -> None:
        self.seat = seat
        self.slug = slug
        self.plan = plan
        self.progress = progress
        self.recording = recording

    def respond(self, query: Query) -> Action:
        chosen = self._plan_step(query)
        if chosen is not None:
            return chosen
        return self._fallback(query)

    def _plan_step(self, query: Query) -> Action | None:
        for index, step in enumerate(self.plan.steps):
            if index in self.progress.done:
                continue
            if step.actor != self.seat or step.kind != query.kind:
                continue
            if step.build is not None:
                action = step.build(query)
                if self.progress.complete(index, len(self.plan.steps)) and self.recording:
                    self.recording.announce(self.seat)
                return action
            assert step.matches is not None
            for action in query.legal_actions:
                if step.matches(query, action):
                    if self.progress.complete(index, len(self.plan.steps)) and self.recording:
                        self.recording.announce(self.seat)
                    return action
        return None

    def _fallback(self, query: Query) -> Action:
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
        return _pass_or_first(query)


def build_players(slug: str, *, pack: ContentPack, player_count: int, recording=None) -> list[M6Player]:
    """一条 M6 基准的逐座位脚本玩家（共享同一份步骤进度）。"""
    plan = plan_for(slug, pack)
    assert len(plan.seats) == player_count, f"{slug}：构造局面座位数与场景声明不一致"
    progress = Progress()
    return [M6Player(seat, slug, plan, progress, recording) for seat in range(player_count)]


assert set(M6_SETUPS) == set(M6_GOALS), "M6 的构造基准与事后判据必须一一对应"
