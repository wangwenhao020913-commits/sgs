"""不变量 fuzz：随机对局驱动的性质检验（票 11 §4 / 票 26）。

落点 = 库门面（主缝）：``new_game`` + 两个 ``RandomPlayer`` 的**多局**记录流与活状态。

- 预算：默认 **200 局 / 30 s**，以先到者为准终止；默认预算内跑不满 200 局即判失败
  （性能退化，票 11 §4）。``SGS_FUZZ_GAMES`` / ``SGS_FUZZ_SECONDS`` 可放大做长跑。
- 逐局验五条不变量：**牌守恒 / 体力范围 / 区域互斥 / 手牌上限 / 事件序号单调**；
  另加"**每局必达终局**"与"**终局牌守恒**"。
- 对局种子来自**测试侧 OS 熵**（``secrets``），**不占游戏内随机源**；失败消息带可复现的
  ``seed`` 与**步号**，可直接 ``python -m sgs --seed S`` 重跑同一局。
- 全部走**库 API**（``Engine.new_game`` + 两个 ``RandomPlayer``），不经产品 CLI。

断言分两层（票 11 §4）：

1. **活状态层**：``_FuzzEngine`` 覆写记录流的唯一出口 ``_emit``，每写出一条记录就在**活
   ``GameState``** 上验体力范围 / 区域互斥 /（边界上的）手牌上限；
2. **记录流层**：从 ``delta`` 重建四个区域与手牌池，与终局活状态互校——"状态变了却没有
   ``delta``"（`01` 原则 6 的漏记）由此被抓到。

本文件另有一组"fuzz 本身不空转"的元测试：对**人为破坏**的局面断言检查器确实报错。
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Final

import pytest

import sgs.agents as agents
from sgs.data import ContentPack, RuleConfig, load_content_pack
from sgs.engine import Engine, JournalRecord, RandomSource, TimingRecord
from sgs.state import FrameKind, GameState, SlotKind, Zone, ZoneRef
from sgs.timing import Timing

from support import SeatSpec, build_state

DEFAULT_GAMES: Final = 200
DEFAULT_SECONDS: Final = 30.0
GAMES_ENV: Final = "SGS_FUZZ_GAMES"
SECONDS_ENV: Final = "SGS_FUZZ_SECONDS"
SEED_ENV: Final = "SGS_FUZZ_SEED"
SEED_SPACE: Final = 1 << 64
"""种子值域上界（票 08 §1：``0 ≤ seed < 2**64``）。"""

QUERY_BUDGET: Final = 20_000
"""单局询问数上限：防"引擎卡死不收局"把门禁吊住；正常一局远低于它。"""

PACK: Final[ContentPack] = load_content_pack()
RULE_CONFIG: Final[RuleConfig] = RuleConfig()
CARD_COUNT: Final[int] = len(PACK.deck.cards)


class FuzzInvariantError(AssertionError):
    """不变量失败；消息里带可复现的对局种子与步号。"""


def _fail(seed: int, record: JournalRecord | None, message: str) -> None:
    if record is None:
        where = "（引擎初始化）"
    else:
        where = f"步号 seq={getattr(record, 'seq', '?')} kind={record.kind}"
    raise FuzzInvariantError(
        f"不变量失败：{message}\n"
        f"对局种子 seed={seed}，{where}\n"
        f"可复现：python -m sgs --seed {seed}"
    )


# ---------------------------------------------------------------------------
# 驱动：库 API × 两个随机玩家
# ---------------------------------------------------------------------------


class _FuzzEngine(Engine):
    """fuzz 专用引擎：每 emit 一条记录就在**活状态**上验不变量（票 11 §4 的第一层）。

    ``_emit`` 是引擎写出记录的**唯一出口**；覆写它得到"每条记录之后"的观测点。
    生产代码不感知本类，引擎行为一个字节都不改。
    """

    game_seed: int = 0

    def _emit(self, record: JournalRecord) -> None:
        super()._emit(record)
        _check_live_invariants(self, record)


class _BudgetExceeded(Exception):
    """一局的询问数超过安全上限：引擎没有收局。"""


class _GameTimeout(Exception):
    """单局超过墙上时钟兜底：性能退化。"""


class _BudgetedPlayer:
    """给随机玩家加共享的**询问预算**与**墙上时钟兜底**，把"卡死"变成可复现的失败。"""

    def __init__(
        self,
        inner: agents.RandomPlayer,
        remaining: list[int],
        deadline: float | None,
    ) -> None:
        self._inner = inner
        self._remaining = remaining
        self._deadline = deadline

    def respond(self, query):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise _GameTimeout
        self._remaining[0] -= 1
        if self._remaining[0] < 0:
            raise _BudgetExceeded
        return self._inner.respond(query)


def _play(seed: int, *, deadline: float | None = None) -> _FuzzEngine:
    """跑完一局（种子由测试侧给定、不占游戏内随机源）。"""
    engine = _FuzzEngine.new_game(seed=seed, rule_config=RULE_CONFIG, pack=PACK)
    engine.game_seed = seed
    # 开局那几条记录（初次洗牌 + 发牌）由 ``build_initial`` 直接构造、不走 ``_emit``，
    # 故在这里补一次活状态检查；其后每条记录由 ``_emit`` 覆写逐一检查。
    _check_live_invariants(engine, None)
    ai_stream = RandomSource(seed).substream("ai")
    remaining = [QUERY_BUDGET]
    players = [
        _BudgetedPlayer(agents.RandomPlayer(ai_stream), remaining, deadline)
        for _ in range(RULE_CONFIG.player_count)
    ]
    try:
        engine.run(players)
    except _BudgetExceeded:
        _fail(seed, engine.records[-1], f"超过 {QUERY_BUDGET} 次询问仍未收局")
    except _GameTimeout:
        _fail(seed, engine.records[-1], "单局超过墙上时钟兜底仍未收局（性能退化）")
    return engine


def _play_and_check(seed: int, *, deadline: float | None = None) -> _FuzzEngine:
    engine = _play(seed, deadline=deadline)
    _check_journal_invariants(engine)
    return engine


# ---------------------------------------------------------------------------
# 第一层：活状态不变量
# ---------------------------------------------------------------------------


def _check_live_invariants(engine: _FuzzEngine, record: JournalRecord | None) -> None:
    state = engine.state
    seed = engine.game_seed

    # 体力范围：hp ≤ max_hp 恒成立（唯一改体力的口 apply_hp 只查上限）；hp < 0 只允许
    # 在濒死帧在栈上时（票 11 §4）。M1 伤害恒 1、体力按 1 递减，故 hp < 0 实际不可达。
    for player in state.players:
        if player.hp > player.hp_max:
            _fail(seed, record, f"座位 {player.seat} 体力 {player.hp} 超过上限 {player.hp_max}")
        if player.hp < 0 and not _near_death_on_stack(engine):
            _fail(seed, record, f"座位 {player.seat} 体力 {player.hp} < 0 却没有濒死帧在栈上")

    # 区域互斥：一张实体牌任一时刻只在一个区域（六个区：三个共享区 + 每座位三区）。
    everywhere = [*state.deck, *state.discard, *state.processing]
    for player in state.players:
        everywhere.extend(player.hand)
        everywhere.extend(player.equip)
        everywhere.extend(player.judge)
    if len(everywhere) != len(state.cards) or len(set(everywhere)) != len(everywhere):
        _fail(seed, record, "区域互斥被破坏：有实体牌丢失或同时出现在多个区域")

    # 手牌上限 = 当前体力值，只在**边界处属于谁的座位**上断言（摸牌途中允许超上限；
    # 对手的手牌要到它自己回合的弃牌阶段才受约束）。每个座位都会在自己回合的
    # `PHASE_DISCARD_END` / `TURN_AFTER_END` 被检查到。
    if _is_hand_limit_point(record) and state.turn_seat is not None:
        player = state.player(state.turn_seat)
        if player.alive and len(player.hand) > player.hp:
            _fail(
                seed,
                record,
                f"回合边界座位 {player.seat} 手牌 {len(player.hand)} 超过体力 {player.hp}",
            )


def _near_death_on_stack(engine: Engine) -> bool:
    return any(frame.kind is FrameKind.NEAR_DEATH for frame in engine.stack.frames)


def _is_hand_limit_point(record: JournalRecord | None) -> bool:
    return record is not None and record.kind == "timing" and record.timing in (
        Timing.PHASE_DISCARD_END,
        Timing.TURN_AFTER_END,
    )


# ---------------------------------------------------------------------------
# 第二层：记录流不变量 + 从 delta 重建牌守恒并与活状态互校
# ---------------------------------------------------------------------------


def _check_journal_invariants(engine: _FuzzEngine) -> None:
    seed = engine.game_seed
    records = engine.records

    # 事件序号单调：全流单一 seq，从 1 起、严格递增（不必连续）。
    sequenced = [record for record in records if record.kind != "header"]
    if not sequenced or sequenced[0].seq != 1:
        _fail(seed, sequenced[0] if sequenced else None, "记录流的 seq 必须从 1 起")
    for previous, current in zip(sequenced, sequenced[1:]):
        if current.seq <= previous.seq:
            _fail(seed, current, f"事件序号必须严格递增：{previous.seq} → {current.seq}")

    # 每局必达终局，且 game_end 是最后一条记录。
    tail = records[-1]
    if tail.kind != "game_end":
        _fail(seed, tail, "每局必达终局：最后一条记录必须是 game_end")
    if tail.result not in {"winner", "draw"}:
        _fail(seed, tail, f"终局结果只能是 winner / draw，实得 {tail.result!r}")
    if tail.result == "winner" and tail.winner is None:
        _fail(seed, tail, "winner 局必须有胜者")
    if tail.result == "draw" and tail.winner is not None:
        _fail(seed, tail, "平局不得有胜者")

    # 终局牌守恒：清栈收尾（补做 L5.6）之后处理区必空。
    if engine.state.processing:
        _fail(seed, tail, "终局收尾后处理区必须为空")

    _replay_deltas_and_cross_check(engine, seed)


def _ids(record: JournalRecord) -> list[int]:
    return [card.card_id for card in record.cards]


@dataclass
class _Piles:
    """从 ``delta`` 重建的六个区域；逐座位的三区按座位分装（票 08 §7 / 票 16 §2）。

    区域坐标带座位，故重建是**座位级**的：``from_zone`` / ``to_zone`` 直接给出座位，
    手牌 / 装备区 / 判定区各归各座，不再是"全局手牌池"。
    """

    seats: int
    deck: list[int] = field(default_factory=list)
    discard: list[int] = field(default_factory=list)
    processing: list[int] = field(default_factory=list)
    hands: list[list[int]] = field(default_factory=list)
    equip: list[list[int]] = field(default_factory=list)
    judge: list[list[int]] = field(default_factory=list)
    slots: dict[int, SlotKind | None] = field(default_factory=dict)
    """每条记录内联的牌面槽位（``card_id`` → ``slot``），供装备区按槽位枚举序重建。"""

    @property
    def total(self) -> int:
        return (
            len(self.deck)
            + len(self.discard)
            + len(self.processing)
            + sum(len(pile) for pile in self.hands)
            + sum(len(pile) for pile in self.equip)
            + sum(len(pile) for pile in self.judge)
        )

    def reset(self, cards: list[int]) -> None:
        self.deck = list(cards)
        self.discard = []
        self.processing = []
        self.hands = [[] for _ in range(self.seats)]
        self.equip = [[] for _ in range(self.seats)]
        self.judge = [[] for _ in range(self.seats)]

    def _pile(self, ref: ZoneRef) -> list[int]:
        if ref.zone is Zone.DECK:
            return self.deck
        if ref.zone is Zone.DISCARD:
            return self.discard
        if ref.zone is Zone.PROCESSING:
            return self.processing
        if ref.seat is None:
            raise AssertionError(f"{ref.zone.value} 区按座位分装，缺座位的坐标无法定位")
        if ref.zone is Zone.HAND:
            return self.hands[ref.seat]
        if ref.zone is Zone.EQUIP:
            return self.equip[ref.seat]
        if ref.zone is Zone.JUDGE:
            return self.judge[ref.seat]
        raise AssertionError(f"未知区域：{ref.zone!r}")

    def take(self, record: JournalRecord, seed: int) -> None:
        pile = self._pile(record.from_zone)
        for card_id in _ids(record):
            if card_id not in pile:
                _fail(
                    seed,
                    record,
                    f"delta 声称从 {record.from_zone.zone.value} 移走牌 {card_id}，但它不在那里",
                )
            pile.remove(card_id)

    def place(self, record: JournalRecord) -> None:
        ref = record.to_zone
        pile = self._pile(ref)
        pile.extend(_ids(record))
        if ref.zone is Zone.EQUIP:
            # 装备区存储序 = 槽位枚举序（票 12 §Q2）；重放器必须复刻，否则座位级互校会误报。
            order = list(SlotKind)
            pile.sort(key=lambda card_id: order.index(self.slots[card_id]))

    def note_slots(self, record: JournalRecord) -> None:
        """记下这条记录内联的牌面槽位；装备区的排序键来自这里（``hp_change`` 没有牌）。"""
        for card in getattr(record, "cards", ()):
            self.slots[card.card_id] = card.slot

    def merge_reshuffle(self, record: JournalRecord, seed: int) -> None:
        """重洗：弃牌堆洗混后并入牌堆尾（票 23 的 ``_reshuffle_into_deck`` 语义）。

        先验"这次重洗记录的牌集恰是当时的弃牌堆"，再合并——否则重建只是**自洽**，
        并没有对齐活状态。
        """
        shuffled = _ids(record)
        if sorted(shuffled) != sorted(self.discard):
            _fail(seed, record, "重洗 delta 的牌集与当时弃牌堆不符")
        self.discard = []
        self.deck.extend(shuffled)


def _replay_deltas_and_cross_check(engine: _FuzzEngine, seed: int) -> None:
    """从 ``delta`` 重建六个区域，逐条查牌守恒，再与终局活状态互校。

    区域坐标带座位 ⇒ 重建是**座位级**的（票 16 §2：重放器补座位级重建），
    故"手牌 / 装备区 / 判定区归错座位"也能被抓到，而不只是池级守恒。
    """
    piles = _Piles(seats=len(engine.state.players))

    for record in engine.records:
        if record.kind != "delta":
            continue
        piles.note_slots(record)
        if record.delta_type == "deck_shuffle":
            if record.from_zone is None:
                piles.reset(_ids(record))
            else:
                piles.merge_reshuffle(record, seed)
        elif record.delta_type == "card_move":
            piles.take(record, seed)
            piles.place(record)
        else:
            continue  # hp_change 不动牌，与牌守恒无关

        if piles.total != CARD_COUNT:
            _fail(seed, record, f"牌守恒被破坏：六个区域合计 {piles.total} 张，应为 {CARD_COUNT} 张")

    if piles.deck != engine.state.deck:
        _fail(seed, None, "牌堆与从 delta 重建的结果不一致（状态变了却没有 delta）")
    if piles.discard != engine.state.discard:
        _fail(seed, None, "弃牌堆与从 delta 重建的结果不一致")
    if piles.processing != engine.state.processing:
        _fail(seed, None, "处理区与从 delta 重建的结果不一致")
    for seat, player in enumerate(engine.state.players):
        if piles.hands[seat] != player.hand:
            _fail(seed, None, f"座位 {seat} 手牌与从 delta 重建的结果不一致")
        if piles.equip[seat] != player.equip:
            _fail(seed, None, f"座位 {seat} 装备区与从 delta 重建的结果不一致")
        if piles.judge[seat] != player.judge:
            _fail(seed, None, f"座位 {seat} 判定区与从 delta 重建的结果不一致")
    if sorted(
        [
            *piles.deck,
            *piles.discard,
            *piles.processing,
            *(card_id for pile in piles.hands for card_id in pile),
            *(card_id for pile in piles.equip for card_id in pile),
            *(card_id for pile in piles.judge for card_id in pile),
        ]
    ) != list(range(CARD_COUNT)):
        _fail(seed, None, "牌守恒被破坏：并非每张实体牌恰在一个区域")


# ---------------------------------------------------------------------------
# 预算与种子（测试侧）
# ---------------------------------------------------------------------------


def _env_positive(name: str, default, convert):
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = convert(raw)
    assert value > 0, f"{name} 必须是正数，得到 {raw!r}"
    return value


def _seed_base() -> int:
    """种子基数：默认取测试侧 OS 熵，不占游戏内随机源；``SGS_FUZZ_SEED`` 可复跑同一序列。"""
    raw = os.environ.get(SEED_ENV)
    if raw is not None:
        return int(raw) % SEED_SPACE
    return secrets.randbits(64)


# ---------------------------------------------------------------------------
# 性质检验
# ---------------------------------------------------------------------------


def test_random_games_satisfy_the_invariants():
    """默认 200 局 / 30 s：以先到者为准；默认预算内跑不满即为性能退化（票 11 §4）。"""
    game_limit = _env_positive(GAMES_ENV, DEFAULT_GAMES, int)
    seconds = _env_positive(SECONDS_ENV, DEFAULT_SECONDS, float)
    base = _seed_base()
    deadline = time.monotonic() + seconds

    played = 0
    while played < game_limit:
        if time.monotonic() >= deadline:
            pytest.fail(
                f"不变量 fuzz 超时：{seconds:g}s 内只跑完 {played}/{game_limit} 局（性能退化，票 11 §4）。\n"
                f"本次种子基数 base={base}；用 {SEED_ENV}={base} 可复跑同一序列。"
            )
        _play_and_check((base + played) % SEED_SPACE, deadline=deadline)
        played += 1

    assert played == game_limit


# ---------------------------------------------------------------------------
# 元测试：fuzz 检查器不空转
# ---------------------------------------------------------------------------


def _drop_one_card(state: GameState) -> None:
    for pile in (state.discard, state.deck, state.processing):
        if pile:
            pile.pop()
            return
    for player in state.players:
        if player.hand:
            player.hand.pop()
            return
    raise AssertionError("没有可丢的牌，测试前提不成立")


def _duplicate_one_card(state: GameState) -> None:
    for pile in (state.discard, state.deck, state.processing):
        if pile:
            pile.append(pile[0])
            return
    for player in state.players:
        if player.hand:
            player.hand.append(player.hand[0])
            return
    raise AssertionError("没有可复制的牌，测试前提不成立")


def test_the_live_checker_catches_a_lost_card():
    engine = _play(1)
    _drop_one_card(engine.state)

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_a_duplicated_card():
    engine = _play(1)
    _duplicate_one_card(engine.state)

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_covers_the_equipment_and_judge_zones():
    """区域互斥扩到六个区：把一张已在别处的牌塞进装备区 / 判定区必须被抓到（票 21）。"""
    engine = _play(1)
    pile = engine.state.discard or engine.state.deck or engine.state.processing
    assert pile, "这一局必然有牌落在共享区"
    engine.state.player(0).equip.append(pile[0])

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_hp_above_the_maximum():
    engine = _play(1)
    player = engine.state.players[0]
    player.hp = player.hp_max + 1

    with pytest.raises(FuzzInvariantError, match="超过上限"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_a_hand_over_the_limit_at_a_turn_boundary():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=0, hp_max=4, hand=(0,)), SeatSpec(hp=4)],
        deck=tuple(range(1, CARD_COUNT)),
        turn_seat=0,
    )
    engine = _FuzzEngine.from_state(state, seed=1)
    engine.game_seed = 1
    boundary = TimingRecord(seq=999, timing=Timing.TURN_AFTER_END, frame_id=0, depth=0)

    with pytest.raises(FuzzInvariantError, match="手牌"):
        _check_live_invariants(engine, boundary)


def test_the_replay_check_catches_live_state_drifting_from_the_deltas():
    engine = _play(1)
    engine.state.discard.append(0)  # 状态变了却没有 delta

    with pytest.raises(FuzzInvariantError, match="不一致"):
        _replay_deltas_and_cross_check(engine, engine.game_seed)


def test_the_failure_message_carries_the_seed_and_the_step():
    engine = _play(1)
    _drop_one_card(engine.state)

    with pytest.raises(FuzzInvariantError) as caught:
        _check_live_invariants(engine, engine.records[-1])

    message = str(caught.value)
    assert "seed=1" in message
    assert f"步号 seq={engine.records[-1].seq}" in message
    assert "python -m sgs --seed 1" in message
