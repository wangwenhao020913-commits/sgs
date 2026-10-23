"""M5 军争金录像的**构造局面 + 脚本驱动**（M5-09a / spec D14 / §6 A3）。

与 M2 / M3 的「随机驱动 + 种子搜索」不同，M5 的 8 条基准从**人为构造的局面**
（:func:`support.build_state` + ``Engine.from_state``）起跑：手牌 / 装备 / 牌堆顶 /
横置态都是写死的 ``card_id``，不依赖任何随机种子，也不做录制期的种子搜索。
回放侧用同一份构造重新开局（``tests/test_replays.py``），磁带冻结的仍是决策内容。

一条基准 = 一个 :class:`Plan`：若干**步骤**（座位 + 询问种类 + 选哪条候选）按顺序
焊死；计划走完后，座位 0 用【杀】收尾把对局带到终局。步骤是「从 ``legal_actions``
里挑」而不是「拼一个动作」——录下来的仍是引擎放行的那条候选。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sgs.data import ContentPack, RuleConfig, Suit
from sgs.engine import Engine
from sgs.state import (
    Action,
    ActivateSkill,
    EndPhase,
    GameState,
    Pass,
    Query,
    SelectCards,
    UseCard,
)
from support import SeatSpec, build_state

from .goals import M5_GOALS

__all__ = [
    "M5_DECK_PROFILE",
    "M5_SETUPS",
    "M5Player",
    "build_engine",
    "build_players",
    "plan_for",
]

M5_DECK_PROFILE = "standard_ex_160"
"""军争档位：8 条 M5 基准**全部**显式选它（spec §6 A3 的硬条件）。"""


# ---------------------------------------------------------------------------
# 计划：步骤 = 「哪个座位、在哪种询问里、挑哪条候选」
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """计划里的一步：座位 + 询问种类 + 候选谓词（或一条预发动清单的键）。"""

    actor: int
    kind: str
    matches: Callable[[Query, Action], bool] | None = None
    manifest: str | None = None
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
    """一条构造基准：逐座位局面 + 目标段步骤。"""

    seats: tuple[SeatSpec, ...]
    steps: tuple[Step, ...]
    deck_top: tuple[int, ...] = ()
    """牌堆顶的固定顺序（其前两张被座位 0 的首回合摸牌拿走）。"""


def _ids(pack: ContentPack, type_id: str) -> list[int]:
    return [index for index, card in enumerate(pack.deck.cards) if card.type == type_id]


def _one(pack: ContentPack, type_id: str) -> int:
    ids = _ids(pack, type_id)
    assert ids, f"standard_ex_160 里没有 {type_id}"
    return ids[0]


def _by_suit(pack: ContentPack, suit: Suit, *, exclude: set[int] = frozenset()) -> list[int]:
    return [
        index
        for index, card in enumerate(pack.deck.cards)
        if card.suit is suit and index not in exclude
    ]


def _full_deck(pack: ContentPack, *, used: set[int], top: Sequence[int] = ()) -> tuple[int, ...]:
    """牌守恒：``top`` 在前，其余未用到的牌按数据表顺序补齐。"""
    reserved = set(top) | set(used)
    return (*top, *(i for i in range(len(pack.deck.cards)) if i not in reserved))


def _use(type_id: str, *, targets: tuple[int, ...] | None = None) -> Callable[[Query, Action], bool]:
    """出牌阶段的「使用该牌名」谓词；``targets`` 非空时还要目标完全一致。"""

    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            return False
        return targets is None or action.targets == targets

    return matches


def _select_card(card_id: int) -> Callable[[Query, Action], bool]:
    """选牌面里**指定实体牌**的那条候选。"""

    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, SelectCards) or len(action.picks) != 1:
            return False
        slot = query.slots[action.picks[0]]
        return slot.card is not None and slot.card.card_id == card_id

    return matches


def _select_suit(suit: Suit) -> Callable[[Query, Action], bool]:
    """选牌面里**指定花色**的第一张（火攻亮牌 / 同花色弃牌用；不写死实体牌 id）。"""

    def matches(query: Query, action: Action) -> bool:
        if not isinstance(action, SelectCards) or len(action.picks) != 1:
            return False
        slot = query.slots[action.picks[0]]
        # ``CardRef.suit`` 在实体牌上落成字符串（与数据表的 ``Suit`` 同值），故两种形态都认。
        return slot.card is not None and slot.card.suit in (suit, suit.value)

    return matches


def _end_phase(query: Query, action: Action) -> bool:
    return isinstance(action, EndPhase)


def _rules(player_count: int) -> RuleConfig:
    return RuleConfig(player_count=player_count, first_seat=0)


# ---------------------------------------------------------------------------
# 8 条构造局面（覆盖 11 个新牌名与 A3 的 10 个覆盖项）
# ---------------------------------------------------------------------------


def _plan_48(pack: ContentPack) -> Plan:
    """48：普通【杀】对藤甲无效；火【杀】对藤甲 +1（属性 / 藤甲）。"""
    slash = _one(pack, "slash")
    fire = _one(pack, "fire_slash")
    vine = _one(pack, "vine_armor")
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(slash, fire)),
            SeatSpec(hp=2, hp_max=4, hand=(vine,)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="首回合让先（等藤甲上身）"),
            Step(1, "PLAY_PHASE", _use("vine_armor"), label="座位 1 装上藤甲"),
            Step(0, "PLAY_PHASE", _end_phase, label="装上后的本回合收手"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="普通杀 → 藤甲无效"),
            Step(0, "PLAY_PHASE", _end_phase, label="普通杀后收手"),
            Step(0, "PLAY_PHASE", _use("fire_slash", targets=(1,)), label="火杀 → 藤甲 +1"),
        ),
    )


def _plan_49(pack: ContentPack) -> Plan:
    """49：铁索连环横置双方 → 雷【杀】的属性伤害沿连环传导（连环 / 属性）。

    2 人局：座位 0 把自己与座位 1 都横置，再对座位 1 出雷【杀】——传导名单从当前回合
    角色（座位 0，仍横置）起，故座位 0 也吃到同一份雷伤；两名角色的横置态各自重置。
    """
    iron = _one(pack, "iron_chain")
    thunder = _one(pack, "thunder_slash")
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(iron, thunder)),
            SeatSpec(hp=2, hp_max=4),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("iron_chain", targets=(0, 1)), label="铁索横置 0/1"),
            Step(0, "PLAY_PHASE", _use("thunder_slash", targets=(1,)), label="雷杀命中 1"),
        ),
    )


def _plan_50(pack: ContentPack) -> Plan:
    """50：【酒】+1 伤害被【白银狮子】上限压回 1（酒 / 白银）。"""
    wine = _one(pack, "wine")
    slashes = _ids(pack, "slash")[:2]
    silver = _one(pack, "silver_lion")
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(wine, *slashes)),
            SeatSpec(hp=2, hp_max=4, hand=(silver,)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _end_phase, label="首回合让先（等白银上身）"),
            Step(1, "PLAY_PHASE", _use("silver_lion"), label="座位 1 装上白银狮子"),
            Step(0, "PLAY_PHASE", _use("wine", targets=(0,)), label="酒（目标自己）"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="杀 → 白银 cap"),
        ),
    )


def _plan_51(pack: ContentPack) -> Plan:
    """51：火攻亮牌 + 同花色弃牌 → 1 点火焰伤害（火攻）。"""
    fire = _one(pack, "fire_attack")
    spades = _by_suit(pack, Suit.SPADE, exclude={fire})
    # 座位 0 手里留一张黑桃作同花色弃牌；座位 1 亮一张黑桃、另有一张未亮出的梅花。
    discard = spades[0]
    reveal = spades[1]
    hidden = _by_suit(pack, Suit.CLUB)[0]
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(fire, discard)),
            SeatSpec(hp=1, hp_max=4, hand=(reveal, hidden)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("fire_attack", targets=(1,)), label="火攻 → 座位 1"),
            Step(1, "SELECT_CARDS", _select_card(reveal), label="目标亮出黑桃"),
            Step(0, "SELECT_CARDS", _select_suit(Suit.SPADE), label="弃同花色"),
        ),
    )


def _plan_52(pack: ContentPack) -> Plan:
    """52：古锭刀 + 骅骝 —— 兵粮跳过座位 1 的摸牌 ⇒ 空手牌 +1；距离 2 恰在攻击范围 2 内。"""
    scimitar = _one(pack, "ancient_scimitar")
    hualiu = _one(pack, "hualiu")
    slash = _one(pack, "slash")
    supply = _one(pack, "supply_shortage")
    judge = next(
        index
        for index, card in enumerate(pack.deck.cards)
        if card.suit is not Suit.CLUB and index not in {scimitar, hualiu, slash, supply}
    )
    fillers = [
        index
        for index in range(len(pack.deck.cards))
        if index not in {scimitar, hualiu, slash, supply, judge}
    ][:2]
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(scimitar, supply, slash)),
            SeatSpec(hp=2, hp_max=4, hand=(hualiu,)),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("supply_shortage", targets=(1,)), label="兵粮 → 座位 1"),
            Step(0, "PLAY_PHASE", _use("ancient_scimitar"), label="座位 0 装上古锭刀"),
            Step(0, "PLAY_PHASE", _end_phase, label="本回合收手"),
            Step(1, "PLAY_PHASE", _use("hualiu"), label="座位 1 装上骅骝（跳过摸牌）"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="空手牌 + 古锭 = 2"),
        ),
        deck_top=(*fillers, judge),
    )


def _plan_53(pack: ContentPack) -> Plan:
    """53：兵粮寸断判定非梅花 ⇒ 跳过摸牌阶段（兵粮）。"""
    supply = _one(pack, "supply_shortage")
    slash = _one(pack, "slash")
    # 座位 0 首回合摸走前两张；座位 1 判定摸走第三张 ⇒ 必须非梅花（跳过摸牌）。
    non_club = next(
        index
        for index, card in enumerate(pack.deck.cards)
        if card.suit is not Suit.CLUB and index not in {supply, slash}
    )
    fillers = [
        index
        for index in range(len(pack.deck.cards))
        if index not in {supply, slash, non_club}
    ][:2]
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(supply, slash)),
            SeatSpec(hp=1, hp_max=4),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("supply_shortage", targets=(1,)), label="兵粮 → 座位 1"),
            Step(0, "PLAY_PHASE", _end_phase, label="结束阶段（等判定）"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="判定后收尾"),
        ),
        # 座位 0 首回合摸走两张 filler；第三张 = 座位 1 的判定牌（非梅花 ⇒ 跳过摸牌）。
        deck_top=(*fillers, non_club),
    )


def _plan_54(pack: ContentPack) -> Plan:
    """54：铁索连环重铸 = 独立 Action（不算使用、不开无懈、弃一张摸一张）（重铸）。"""
    iron = _one(pack, "iron_chain")
    slash = _one(pack, "slash")

    def reforge(query: Query, action: Action) -> bool:
        return action.kind == "Reforge" and action.card.card_id == iron

    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(iron, slash)),
            SeatSpec(hp=1, hp_max=4),
        ),
        steps=(
            Step(0, "PLAY_PHASE", reforge, label="重铸铁索"),
            Step(0, "PLAY_PHASE", _use("slash", targets=(1,)), label="重铸后收尾"),
        ),
    )


def _plan_55(pack: ContentPack) -> Plan:
    """55：朱雀羽扇把普通【杀】转化成火【杀】（朱雀 / 属性）。"""
    fan = _one(pack, "vermilion_feather_fan")
    slash = _one(pack, "slash")
    return Plan(
        seats=(
            SeatSpec(hp=4, hand=(fan, slash)),
            SeatSpec(hp=1, hp_max=4),
        ),
        steps=(
            Step(0, "PLAY_PHASE", _use("vermilion_feather_fan"), label="座位 0 装上羽扇"),
            Step(0, "PLAY_PHASE", _end_phase, label="本回合收手"),
            Step(
                0,
                "PLAY_PHASE",
                manifest="equip.vermilion_feather_fan",
                label="按清单确认羽扇转化",
            ),
        ),
    )


M5_SETUPS: dict[str, Callable[[ContentPack], Plan]] = {
    "48-fire-slash-vine-armor": _plan_48,
    "49-iron-chain-thunder-conduction": _plan_49,
    "50-wine-silver-lion-cap": _plan_50,
    "51-fire-attack-reveal-discard": _plan_51,
    "52-scimitar-empty-hand-hualiu": _plan_52,
    "53-supply-shortage-skips-draw": _plan_53,
    "54-reforge-iron-chain": _plan_54,
    "55-fan-converts-normal-slash": _plan_55,
}
"""8 条 M5 构造基准：slug → 局面 / 步骤构造器。"""


def plan_for(slug: str, pack: ContentPack) -> Plan:
    return M5_SETUPS[slug](pack)


def _state_for(slug: str, pack: ContentPack) -> GameState:
    plan = plan_for(slug, pack)
    used = {
        card_id
        for seat in plan.seats
        for card_id in (*seat.hand, *seat.equip, *seat.judge)
    }
    return build_state(
        pack=pack,
        seats=list(plan.seats),
        deck=_full_deck(pack, used=used, top=plan.deck_top),
        turn_seat=0,
        rule_config=_rules(len(plan.seats)),
    )


def build_engine(slug: str, *, seed: int, pack: ContentPack) -> Engine:
    """M5 基准的开局：从构造局面起跑（与回放侧同一份构造）。"""
    return Engine.from_state(_state_for(slug, pack), seed=seed)


# ---------------------------------------------------------------------------
# 脚本玩家：按计划挑候选，计划走完后座位 0 用【杀】收尾
# ---------------------------------------------------------------------------


_SLASH_FAMILY = ("slash", "fire_slash", "thunder_slash")


def _pass_or_first(query: Query) -> Action:
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _manifest_activation(query: Query, manifest: str) -> ActivateSkill | None:
    """按预发动清单填一条 ``ActivateSkill``（来源取来源步首项、目标取目标池首项）。"""
    steps = (getattr(query, "transform_preactivations", None) or {}).get(manifest)
    if not steps:
        return None
    source = int(steps[0].pool[0].options[0])
    choices: tuple[str, ...] = ()
    if len(steps) > 1:
        seat = steps[1].pool[0].options[0].removeprefix("seat:")
        choices = (f"seat:{seat}",)
    return ActivateSkill(
        skill_id=manifest, picks=(source,), choices=choices, answers_seq=query.seq
    )


class M5Player:
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
            action = self._pick(step, query)
            if action is None:
                continue
            if self.progress.complete(index, len(self.plan.steps)) and self.recording:
                self.recording.announce(self.seat)
            return action
        return None

    def _pick(self, step: Step, query: Query) -> Action | None:
        if step.manifest is not None:
            return _manifest_activation(query, step.manifest)
        assert step.matches is not None
        for action in query.legal_actions:
            if step.matches(query, action):
                return action
        return None

    def _fallback(self, query: Query) -> Action:
        if query.kind == "PLAY_PHASE":
            if self.seat == 0 and len(self.progress.done) == len(self.plan.steps):
                slash = self._cleanup_slash(query)
                if slash is not None:
                    return slash
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
            return query.legal_actions[0]
        if query.kind in ("DODGE", "PEACH", "RESPOND", "SELECT_CARDS", "SELECT_OPTION"):
            return _pass_or_first(query)
        return _pass_or_first(query)

    @staticmethod
    def _cleanup_slash(query: Query) -> Action | None:
        for action in query.legal_actions:
            if (
                isinstance(action, UseCard)
                and action.card.type in _SLASH_FAMILY
                and action.targets
            ):
                return action
        return None


def build_players(slug: str, *, pack: ContentPack, player_count: int, recording=None) -> list[M5Player]:
    """一条 M5 基准的逐座位脚本玩家（共享同一份步骤进度）。"""
    plan = plan_for(slug, pack)
    assert len(plan.seats) == player_count, f"{slug}：构造局面座位数与场景声明不一致"
    progress = Progress()
    return [
        M5Player(seat, slug, plan, progress, recording)
        for seat in range(player_count)
    ]


assert set(M5_SETUPS) == set(M5_GOALS), "M5 的构造基准与事后判据必须一一对应"
