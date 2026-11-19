"""M7 PvE 金录像（票 12）：**构造局面 + 脚本驱动**，覆盖票 01 §4 的四类逐场终局。

与 M5 / M6 的构造基准同一条姿态（``replays/m5.py`` / ``replays/m6.py``）：局面写死在
代码里、从 ``Engine.from_state`` 起跑，计划 = 若干「哪个座位、在哪种询问里、挑哪条候选」
的步骤；录下来的仍是引擎放行的那条候选，回放侧用同一份构造重新开局
（``tests/test_replays.py`` → :func:`replays.scenarios.engine_for`）。

为什么六条都从构造局面起跑（而不是 ``Engine.new_game`` 随机发牌）：票 01 §7 要求
**≤5 轮、≤5 座**的短局，而"四点体力里恰好留一点、恰好有那一张【杀】/【万箭齐发】"在
随机发牌下要**搜种子**才能稳定命中；票 08 的阵亡类用例（ROUT / SLAY_BOSS / 保护队友）
已经用同一条收窄落了地，这里沿用。起局之后的每一步都是真引擎；PvE 的配置
（``identities`` / ``principal`` / ``protected_ally`` / ``victory`` / ``boss`` / 座位表）
全部在 **header 首行**，回放只凭 header + 本模块的构造就能重建同一场。

六条基准（``tests/test_replays.py`` 的 ``PVE_OUTCOME_CLASSES`` 是它们的终局冻结表）：

=====  ===========================  ==============================================
slug   逐场条件 / 座位                记录流里的判据（``goals.PVE_GOALS``）
=====  ===========================  ==============================================
61     ``ROUT``（1v2）               【万箭齐发】同一批打死两个敌人 ⇒ ``ally`` /
                                     ``enemies_routed``
62     ``SURVIVE_ROUNDS(1)``（1v2）   谁都不进攻，轮次推进到 2 ⇒ ``ally`` /
                                     ``rounds_survived``
63     ``ROUT_WITHIN_ROUNDS(1)``     第 1 轮内全歼 ⇒ ``ally`` / ``enemies_routed_in_time``
       （1v2）
64     ``SLAY_BOSS``（1v1）          Boss 座位阵亡 ⇒ ``ally`` / ``boss_slain``
65     ``ROUT``（2v3 队友局）         队友座位真的行动并用【万箭齐发】收局；胜方
                                     ``winners=(0, 3)``
66     ``ROUT``（2v2 带 ``protected_ally``）  敌人杀死受保护队友、主角还活着 ⇒ ``enemy`` /
                                     ``protected_ally_dead``
=====  ===========================  ==============================================

**票面「1v2 带 protected_ally」就地收窄成 2v2**：``protected_ally`` 必须是**第二个 ally
座位**（:class:`~sgs.data.rule_config.PveConfig` 的构造期校验），3 座里放不下"1 友 + 保护
对象"；这条收窄与票 08 的``## Answer``同源（那里已经在 2v2 上落了同一条用例）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sgs.data import (
    ContentPack,
    Identity,
    Mode,
    PveConfig,
    PveSeat,
    PveVictory,
    PveVictoryKind,
    RuleConfig,
)
from sgs.engine import Engine
from sgs.state import Action, EndPhase, GameState, Pass, Query, UseCard
from support import SeatSpec, build_state

from .goals import PVE_GOALS

__all__ = [
    "PVE_DECK_PROFILE",
    "PVE_SETUPS",
    "Plan",
    "PvePlayer",
    "build_engine",
    "build_players",
    "plan_for",
]

PVE_DECK_PROFILE = "standard_108"
"""PvE 基准走标准 108：被覆盖的是**逐场胜负**，与军争牌名无关，标准表就够（且录像更小）。"""

_ALLY = Identity.ALLY
_ENEMY = Identity.ENEMY


# ---------------------------------------------------------------------------
# 计划：步骤 = 「哪个座位、在哪种询问里、挑哪条候选」
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """计划里的一步：座位 + 询问种类 + 候选谓词。"""

    actor: int
    kind: str
    matches: Callable[[Query, Action], bool]
    label: str = ""


@dataclass
class Progress:
    """所有座位共享的步骤进度（一条基准一局，故不跨局复用）。"""

    done: set[int] = field(default_factory=set)

    def complete(self, index: int, total: int) -> bool:
        self.done.add(index)
        return len(self.done) == total


@dataclass(frozen=True)
class Plan:
    """一条 PvE 构造基准：``rule_config``（含 ``pve`` 一节）+ 逐座位手牌 + 目标段步骤。

    **座位表不在这里**：它住在 ``rule_config.pve.seats``（header 里），本模块只给手牌与
    牌堆顶——这样"这一场是谁"只有一个真源，header 自证的判据（
    ``test_every_pve_baseline_carries_its_pve_section_in_the_header``）才有意义。
    """

    rule_config: RuleConfig
    hands: tuple[tuple[int, ...], ...]
    steps: tuple[Step, ...]
    deck_top: tuple[int, ...] = ()
    """牌堆顶的固定顺序（其前两张被座位 0 的首回合摸牌拿走）。"""


def _pick(pack: ContentPack, *, type_id: str, exclude: set[int] = frozenset()) -> int:
    """按数据表顺序取第一张未被占用的某牌名（确定性，与 M5 / M6 同一写法）。"""
    for index, card in enumerate(pack.deck.cards):
        if index in exclude:
            continue
        if card.type == type_id:
            return index
    raise AssertionError(f"standard_108 里没有 {type_id}")


def _full_deck(
    pack: ContentPack, *, used: set[int], top: Sequence[int] = ()
) -> tuple[int, ...]:
    """牌守恒：``top`` 在前，其余未用到的牌按数据表顺序补齐。"""
    reserved = set(top) | set(used)
    return (*top, *(i for i in range(len(pack.deck.cards)) if i not in reserved))


def _use(
    type_id: str, *, targets: Sequence[int] | None = None
) -> Callable[[Query, Action], bool]:
    """出牌阶段的「使用该牌名」谓词；``targets`` 非空时按**集合**比对（顺序不钉）。"""

    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            return False
        return targets is None or set(action.targets) == set(targets)

    return matches


def _end_phase(query: Query, action: Action) -> bool:
    return isinstance(action, EndPhase)


def _rules(
    seats: Sequence[PveSeat],
    identities: Sequence[Identity],
    *,
    victory: PveVictory,
    boss: int | None = None,
    protected_ally: int | None = None,
    principal: int = 0,
) -> RuleConfig:
    """一局 PvE 的规则配置：阵营 / 主角 / 胜负条件 / 座位表全部**进 header**（票 01 §2）。"""
    return RuleConfig(
        player_count=len(identities),
        mode=Mode.PVE,
        first_seat=0,
        pve=PveConfig(
            identities=tuple(identities),
            principal=principal,
            protected_ally=protected_ally,
            victory=victory,
            boss=boss,
            seats=tuple(seats),
        ),
    )


# ---------------------------------------------------------------------------
# 6 条构造局面
# ---------------------------------------------------------------------------


def _plan_61(pack: ContentPack) -> Plan:
    """61：``ROUT``（1v2）——【万箭齐发】同一批打死两名敌人，主角不掉血。"""
    barrage = _pick(pack, type_id="arrow_barrage")
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
            ),
            identities=(_ALLY, _ENEMY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.ROUT),
        ),
        hands=((barrage,), (), ()),
        steps=(Step(0, "PLAY_PHASE", _use("arrow_barrage"), label="万箭齐发全歼"),),
    )


def _plan_62(pack: ContentPack) -> Plan:
    """62：``SURVIVE_ROUNDS(1)``（1v2）——全程不进攻，轮次推进到 2 即胜。"""
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="黄巾力士", hp=3, hp_max=3),
                PveSeat(name="黄巾力士", hp=3, hp_max=3),
            ),
            identities=(_ALLY, _ENEMY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.SURVIVE_ROUNDS, rounds=1),
        ),
        hands=((), (), ()),
        steps=(Step(0, "PLAY_PHASE", _end_phase, label="收手（等轮次推进）"),),
    )


def _plan_63(pack: ContentPack) -> Plan:
    """63：``ROUT_WITHIN_ROUNDS(1)``（1v2）——第 1 轮内全歼（硬边界在 ``solve_pve_outcome``）。"""
    barrage = _pick(pack, type_id="arrow_barrage")
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
            ),
            identities=(_ALLY, _ENEMY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.ROUT_WITHIN_ROUNDS, rounds=1),
        ),
        hands=((barrage,), (), ()),
        steps=(Step(0, "PLAY_PHASE", _use("arrow_barrage"), label="本轮内全歼"),),
    )


def _plan_64(pack: ContentPack) -> Plan:
    """64：``SLAY_BOSS``（1v1）——Boss 座位（合成武将「黄巾渠帅」）阵亡即胜。"""
    slash = _pick(pack, type_id="slash")
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="黄巾渠帅", hp=1, hp_max=5),
            ),
            identities=(_ALLY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.SLAY_BOSS),
            boss=1,
        ),
        hands=((slash,), ()),
        steps=(Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="杀 Boss"),),
    )


def _plan_65(pack: ContentPack) -> Plan:
    """65：队友局（2v3）——主角让先，**AI 队友**座位用【万箭齐发】收局。

    两名友方座位（0 = 主角、3 = 队友）× 三名敌人；队友的【万箭齐发】同时打到主角
    （1 点伤害，主角 4 点体力活着）与三名敌人（各 1 点体力 ⇒ 同一批阵亡）。胜方是
    **全侧友方座位** ``(0, 3)``，故"队友也算赢家"是记录流里读得出的事实。
    """
    barrage = _pick(pack, type_id="arrow_barrage")
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
                PveSeat(name="黄巾力士", hp=1, hp_max=3),
                PveSeat(name="老卒", hp=4, hp_max=4),
                PveSeat(name="黄巾渠帅", hp=1, hp_max=5),
            ),
            identities=(_ALLY, _ENEMY, _ENEMY, _ALLY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.ROUT),
        ),
        hands=((), (), (), (barrage,), ()),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="主角让先"),
            Step(3, "PLAY_PHASE", _use("arrow_barrage"), label="队友收局"),
        ),
    )


def _plan_66(pack: ContentPack) -> Plan:
    """66：受保护队友阵亡（2v2）——敌人杀死保护对象，主角还活着但这一场判败。

    座位 0 = 主角、1 = 受保护队友（``protected_ally``）、2 / 3 = 敌人。敌人 2 的【杀】
    打座位 1（1 点体力、无手牌 ⇒ 没有【闪】）⇒ 濒死无人救 ⇒ 阵亡 ⇒ 死亡检测点判
    ``protected_ally_dead``（票 01 §4 的败列；胜列的全歼此时还没成立）。
    """
    slash = _pick(pack, type_id="slash")
    return Plan(
        rule_config=_rules(
            seats=(
                PveSeat(name="游侠", hp=4, hp_max=4),
                PveSeat(name="受保护的队友", hp=1, hp_max=4),
                PveSeat(name="黄巾力士", hp=3, hp_max=3),
                PveSeat(name="黄巾力士", hp=3, hp_max=3),
            ),
            identities=(_ALLY, _ALLY, _ENEMY, _ENEMY),
            victory=PveVictory(kind=PveVictoryKind.ROUT),
            protected_ally=1,
        ),
        hands=((), (), (slash,), ()),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="主角让先"),
            Step(1, "PLAY_PHASE", _end_phase, label="受保护队友让先"),
            Step(2, "PLAY_PHASE", _use("slash", targets=(1,)), label="敌人杀死保护对象"),
        ),
    )


PVE_SETUPS: dict[str, Callable[[ContentPack], Plan]] = {
    "61-pve-rout-win": _plan_61,
    "62-pve-survive-rounds-win": _plan_62,
    "63-pve-rout-within-rounds-win": _plan_63,
    "64-pve-slay-boss-win": _plan_64,
    "65-pve-ally-team-rout": _plan_65,
    "66-pve-protected-ally-death": _plan_66,
}
"""6 条 PvE 基准：slug → 局面 / 步骤构造器。"""


def plan_for(slug: str, pack: ContentPack) -> Plan:
    return PVE_SETUPS[slug](pack)


# ---------------------------------------------------------------------------
# 开局：座位表来自 header，手牌 / 牌堆顶来自计划
# ---------------------------------------------------------------------------


def _state_for(plan: Plan, pack: ContentPack, rule_config: RuleConfig) -> GameState:
    pve = rule_config.pve
    assert pve is not None, "PvE 基准的 rule_config 必须带 pve 一节"
    assert len(pve.seats) == len(pve.identities) == len(plan.hands), (
        "座位表 / 阵营 / 手牌三方同长（座位表在 header 里，手牌在计划里）"
    )
    used = {card_id for hand in plan.hands for card_id in hand}
    seats = [
        SeatSpec(
            hp=seat.hp,
            hp_max=seat.hp_max,
            hand=plan.hands[index],
            hero=seat.hero,
            kingdom=seat.kingdom,
            gender=seat.gender,
            identity=pve.identities[index],
        )
        for index, seat in enumerate(pve.seats)
    ]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=_full_deck(pack, used=used, top=plan.deck_top),
        turn_seat=rule_config.first_seat,
        rule_config=rule_config,
    )
    # PvE 开局**全部身份公开**（票 01 §3）：构造局面照真开局置 ``identity_visible``，
    # 否则这一局跑在"没人看得见阵营"的局面上，与真 PvE 局差一层。
    for player in state.players:
        player.identity_visible = True
    return state


def build_engine(
    slug: str, *, seed: int, pack: ContentPack, rule_config: RuleConfig | None = None
) -> Engine:
    """PvE 基准的开局：**录制与回放共用**的入口（``rule_config`` 缺省取计划里的那份）。"""
    plan = plan_for(slug, pack)
    config = rule_config if rule_config is not None else plan.rule_config
    return Engine.from_state(_state_for(plan, pack, config), seed=seed)


# ---------------------------------------------------------------------------
# 脚本玩家
# ---------------------------------------------------------------------------


def _fallback(query: Query) -> Action:
    """计划走完后的确定性收尾：能结束阶段就结束、能放弃就放弃。"""
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class PvePlayer:
    """按 :class:`Plan` 应答的脚本玩家（录制期用；回放期一律 ``TapePlayer``）。"""

    def __init__(
        self,
        seat: int,
        plan: Plan,
        progress: Progress,
        recording=None,
    ) -> None:
        self.seat = seat
        self.plan = plan
        self.progress = progress
        self.recording = recording

    def respond(self, query: Query) -> Action:
        for index, step in enumerate(self.plan.steps):
            if index in self.progress.done:
                continue
            if step.actor != self.seat or step.kind != query.kind:
                continue
            for action in query.legal_actions:
                if step.matches(query, action):
                    if self.progress.complete(index, len(self.plan.steps)) and self.recording:
                        self.recording.announce(self.seat)
                    return action
        return _fallback(query)


def build_players(
    slug: str, *, pack: ContentPack, player_count: int, recording=None
) -> list[PvePlayer]:
    """一条 PvE 基准的逐座位脚本玩家（共享同一份步骤进度）。"""
    plan = plan_for(slug, pack)
    assert len(plan.hands) == player_count, f"{slug}：手牌数与场景声明的人数不一致"
    progress = Progress()
    return [PvePlayer(seat, plan, progress, recording) for seat in range(player_count)]


assert set(PVE_SETUPS) == set(PVE_GOALS), "PvE 的构造基准与事后判据必须一一对应"
