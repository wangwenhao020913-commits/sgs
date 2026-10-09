"""M2 基准的**事后判据**（票 32 §1）。

M1 的六条基准用 ``ScenarioPlayer`` 在**录制期**迫使目标链发生；M2 的 28 条改用
**随机驱动 + 事后判据**：录制时用与产品同源的 :class:`~sgs.agents.RandomPlayer`
（测试侧种子子流，确定性）跑完一整局，再断言"这条基准要证的那件事真的发生过"。
磁带冻结的是**决策内容**，回放期一律 :class:`~sgs.agents.TapePlayer`，与驱动无关。

判据只读**记录流与终局态**，所以它们同时也是"这条基准到底在证什么"的可读规格。
种子由开发期搜索得到（``tests/replays/seed_search.py``），写死在 ``scenarios.py`` 里。
"""

from __future__ import annotations

from sgs.data import Identity
from sgs.engine import Engine, JournalRecord
from sgs.state import MoveReason, PlayCard, QueryKind, UseCard, WindowCause, Zone
from sgs.timing import Timing

__all__ = ["M2_GOALS", "goal_for"]


def _records(engine: Engine) -> tuple[JournalRecord, ...]:
    return engine.records


def _deltas(engine: Engine, delta_type: str) -> list[JournalRecord]:
    return [
        record
        for record in _records(engine)
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _accepted(engine: Engine) -> list[JournalRecord]:
    return [record for record in _records(engine) if record.kind == "action" and record.accepted]


def _card_moves(engine: Engine) -> list[JournalRecord]:
    return _deltas(engine, "card_move")


def _hp_changes(engine: Engine) -> list[JournalRecord]:
    return _deltas(engine, "hp_change")


def _reveals(engine: Engine) -> list[JournalRecord]:
    return _deltas(engine, "identity_reveal")


def _timings(engine: Engine) -> list[JournalRecord]:
    return [record for record in _records(engine) if record.kind == "timing"]


def _queries(engine: Engine) -> list[JournalRecord]:
    return [record for record in _records(engine) if record.kind == "query"]


def _actor_of(engine: Engine, answers_seq: int) -> int | None:
    for record in _records(engine):
        if record.kind == "query" and record.query.seq == answers_seq:
            return record.query.actor
    return None


def _nullify_uses(engine: Engine) -> list[JournalRecord]:
    """每次【无懈可击】使用的 ``action`` 记录。"""
    return [
        record
        for record in _accepted(engine)
        if getattr(record.action, "card", None) is not None
        and record.action.card.type == "nullify"
    ]


def _nullify_chain_depth(engine: Engine) -> int:
    """一条链上的最大嵌套层数：连续的【无懈】回答**不同**的窗口帧即加深一层（票 28）。"""
    sequence = _records(engine)
    current = 0
    previous = None
    best = 0
    for index, record in enumerate(sequence):
        if record.kind != "action" or not record.accepted:
            continue
        card = getattr(record.action, "card", None)
        if card is None or card.type != "nullify":
            continue
        query = None
        for back in range(index - 1, -1, -1):
            if sequence[back].kind == "query":
                query = sequence[back].query
                break
        window_frame = getattr(query, "window_frame", None)
        current = current + 1 if (previous is not None and window_frame != previous) else 1
        previous = window_frame
        best = max(best, current)
    return best


def _indulgence_results(engine: Engine) -> list[tuple[int, str | None, int]]:
    """每次【乐不思蜀】判定的 ``(被判定者, 花色, 判定记录 seq)``。"""
    results = []
    for record in _timings(engine):
        if record.timing is not Timing.JUDGE_CARD_AFTER_EFFECT:
            continue
        for card in record.cards:
            if card.type == "indulgence":
                results.append(
                    (record.actor, str(card.suit) if card.suit else None, record.seq)
                )
    return results


def _play_phase_ran_after(engine: Engine, subject: int, judge_seq: int) -> bool:
    """判定之后的同一回合里，``subject`` 是否真的走了出牌阶段（票 26 的"跳过"判据）。

    跳过的记录形状：``PHASE_PLAY_START`` 不发，判定阶段之后直接到 ``PHASE_DISCARD_START``；
    否则 ``PHASE_PLAY_START`` 先出现。只看该座位自己的时机，故不受邻座影响。
    """
    for record in _records(engine):
        if record.kind != "timing" or record.actor != subject or record.seq <= judge_seq:
            continue
        if record.timing is Timing.PHASE_PLAY_START:
            return True
        if record.timing is Timing.PHASE_DISCARD_START:
            return False
    return False


def _indulgence_skipped_play_phase(engine: Engine) -> bool:
    return any(
        suit != "heart" and not _play_phase_ran_after(engine, subject, seq)
        for subject, suit, seq in _indulgence_results(engine)
    )


def _indulgence_kept_play_phase(engine: Engine) -> bool:
    return any(
        suit == "heart" and _play_phase_ran_after(engine, subject, seq)
        for subject, suit, seq in _indulgence_results(engine)
    )


def _delayed_deferred(engine: Engine, type_id: str) -> bool:
    """延时锦囊顺延到下一座位：``PROCESSING → JUDGE(owner=None)``（票 11 §4）。"""
    return any(
        move.from_zone is not None
        and move.from_zone.zone is Zone.PROCESSING
        and move.to_zone is not None
        and move.to_zone.zone is Zone.JUDGE
        and move.owner is None
        and any(card.type == type_id for card in move.cards)
        for move in _card_moves(engine)
    )


def _window_starts(engine: Engine) -> list[int]:
    """每个濒死窗口的首个被问者座位（窗口边界 = 第 N 条 ``NEAR_DEATH_AT``）。"""
    starts: list[int] = []
    current: int | None = None
    for record in _records(engine):
        if record.kind == "timing" and record.timing is Timing.NEAR_DEATH_AT:
            if current is not None:
                starts.append(current)
            current = None
        elif record.kind == "query" and record.query.kind == QueryKind.PEACH and current is None:
            current = record.query.actor
    if current is not None:
        starts.append(current)
    return starts


def _multi_peach_window(engine: Engine) -> int:
    """单个濒死窗口里被问的应答者数的最大值。"""
    count = 0
    best = 0
    for record in _records(engine):
        if record.kind == "timing" and record.timing is Timing.NEAR_DEATH_AT:
            count = 0
        if record.kind == "query" and record.query.kind == QueryKind.PEACH:
            count += 1
            best = max(best, count)
    return best


# ---------------------------------------------------------------------------
# 28 条 M2 基准的判据
# ---------------------------------------------------------------------------


def _side_is(side: str):
    return lambda engine: engine.records[-1].side == side


def _multi_target_nullified(engine: Engine) -> bool:
    multi = [
        (record.seq, record.action.targets)
        for record in _accepted(engine)
        if isinstance(record.action, UseCard) and len(record.action.targets) >= 2
    ]
    if not multi:
        return False
    for nullify in _nullify_uses(engine):
        actor = _actor_of(engine, nullify.action.answers_seq)
        for multi_seq, targets in multi:
            if multi_seq < nullify.seq and actor in targets:
                return True
    return False


def _nullify_then_delayed_defer(engine: Engine) -> bool:
    """一次【无懈】之后，被它指定的座位的延时锦囊被传下家（票 11 §4 的闪电专条）。"""
    for nullify in _nullify_uses(engine):
        actor = _actor_of(engine, nullify.action.answers_seq)
        if actor is None:
            continue
        for move in _card_moves(engine):
            if move.seq <= nullify.seq:
                continue
            if (
                move.from_zone is not None
                and move.from_zone.zone is Zone.JUDGE
                and move.from_zone.seat == actor
                and move.reason is MoveReason.LOSE
                and any(card.type in ("lightning", "indulgence") for card in move.cards)
            ):
                return True
    return False


def _rebel_reward(engine: Engine) -> bool:
    """杀死反贼 ⇒ 凶手摸 3 张（票 14 §4.2）。"""
    revealed = any(record.identity is Identity.REBEL for record in _reveals(engine))
    reward = any(
        move.from_zone is not None
        and move.from_zone.zone is Zone.DECK
        and move.to_zone is not None
        and move.to_zone.zone is Zone.HAND
        and len(move.cards) == 3
        for move in _card_moves(engine)
    )
    return revealed and reward


def _lord_kills_loyalist(engine: Engine) -> bool:
    """主公杀死忠臣 ⇒ **主公**弃置自己的所有牌（票 14 §4.2）。"""
    loyal = [
        record.seq for record in _reveals(engine) if record.identity is Identity.LOYALIST
    ]
    if not loyal:
        return False
    lord = next(
        (player.seat for player in engine.state.players if player.identity is Identity.LORD),
        None,
    )
    if lord is None:
        return False
    return any(
        move.to_zone is not None
        and move.to_zone.zone is Zone.DISCARD
        and move.owner == lord
        and move.seq > min(loyal)
        for move in _card_moves(engine)
    )


def _slash_beyond_adjacent(engine: Engine) -> bool:
    """一次【杀】指到座位距离 ≥ 2 的目标（距离 / 攻击范围可观测，票 29）。"""
    seats = engine.state.rule_config.player_count
    for record in _accepted(engine):
        if not isinstance(record.action, UseCard) or record.action.card.type != "slash":
            continue
        actor = _actor_of(engine, record.action.answers_seq)
        if actor is None:
            continue
        for target in record.action.targets:
            if min((target - actor) % seats, (actor - target) % seats) >= 2:
                return True
    return False


def _equipment_replacement(engine: Engine) -> bool:
    """装备替换 = 同一座位同一槽位既有旧牌 ``LOSE`` 入弃牌堆、又有新牌 ``PUT`` 置入。"""
    lost = {
        (move.from_zone.seat, card.slot)
        for move in _card_moves(engine)
        if move.reason is MoveReason.LOSE
        and move.from_zone is not None
        and move.from_zone.zone is Zone.EQUIP
        and move.to_zone is not None
        and move.to_zone.zone is Zone.DISCARD
        for card in move.cards
    }
    put = {
        (move.to_zone.seat, card.slot)
        for move in _card_moves(engine)
        if move.reason is MoveReason.PUT
        and move.to_zone is not None
        and move.to_zone.zone is Zone.EQUIP
        for card in move.cards
    }
    return bool(lost & put)


def _serpent_spear(engine: Engine) -> bool:
    """丈八蛇矛：两张手牌转化为一张虚拟【杀】（``virtual_sources`` 非空）。"""
    return any(
        isinstance(record.action, UseCard)
        and record.action.card.type == "slash"
        and bool(record.action.card.virtual_sources)
        for record in _accepted(engine)
    )


def _halberd_three_targets(engine: Engine) -> bool:
    """方天画戟：一张【杀】多指定两个目标。"""
    return any(
        isinstance(record.action, UseCard)
        and record.action.card.type == "slash"
        and len(record.action.targets) >= 3
        for record in _accepted(engine)
    )


def _play_card(engine: Engine) -> bool:
    """一次**打出**（``PlayCard``，走 L6、不占使用次数；票 19 §2）。"""
    return any(isinstance(record.action, PlayCard) for record in _accepted(engine))


def _five_grains(engine: Engine) -> bool:
    used = any(
        getattr(record.action, "card", None) is not None
        and record.action.card.type == "harvest"
        for record in _accepted(engine)
    )
    selected = any(record.query.kind == QueryKind.SELECT_CARDS for record in _queries(engine))
    return used and selected


def _borrowed_sword(engine: Engine) -> bool:
    return any(
        record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.BORROWED_SWORD
        for record in _queries(engine)
    )


def _peel_equip_or_judge(engine: Engine) -> bool:
    """拆 / 顺取装备区或判定区的牌（选择面里出现这两个区）。"""
    for record in _queries(engine):
        if record.query.kind != QueryKind.SELECT_CARDS:
            continue
        zones = {slot.ref.zone for slot in record.query.slots}
        if Zone.EQUIP in zones or Zone.JUDGE in zones:
            return True
    return False


M2_GOALS = {
    "07-identity-4p-rebel-win": _side_is("rebel"),
    "08-identity-5p-lord-win": _side_is("lord"),
    "09-identity-6p-renegade-win": _side_is("renegade"),
    "10-identity-7p-lord-win": _side_is("lord"),
    "11-identity-8p-rebel-win": _side_is("rebel"),
    "12-indulgence-skips-play": _indulgence_skipped_play_phase,
    "13-indulgence-heart-misses": _indulgence_kept_play_phase,
    "14-lightning-thunder-damage": lambda e: any(
        change.amount <= -3 for change in _hp_changes(e)
    ),
    "15-lightning-defers-next-seat": lambda e: _delayed_deferred(e, "lightning"),
    "16-nullify-cancels-a-trick": lambda e: _nullify_chain_depth(e) >= 1,
    "17-nullify-nested-two": lambda e: _nullify_chain_depth(e) >= 2,
    "18-nullify-nested-three": lambda e: _nullify_chain_depth(e) >= 3,
    "19-nullify-on-multi-target": _multi_target_nullified,
    "20-nullify-lightning-defers": _nullify_then_delayed_defer,
    "21-rebel-killed-reward": _rebel_reward,
    "22-lord-kills-loyalist": _lord_kills_loyalist,
    "23-two-deaths-revealed": lambda e: len(_reveals(e)) >= 2,
    "24-multi-player-peach-window": lambda e: _multi_peach_window(e) >= 2,
    "25-rollback-same-start": lambda e: (
        len(_window_starts(e)) >= 2 and _window_starts(e)[0] == _window_starts(e)[1]
    ),
    "26-rollback-start-advances": lambda e: (
        len(_window_starts(e)) >= 2 and _window_starts(e)[0] != _window_starts(e)[1]
    ),
    "27-slash-beyond-adjacent": _slash_beyond_adjacent,
    "28-equipment-replacement": _equipment_replacement,
    "29-serpent-spear-virtual-slash": _serpent_spear,
    "30-halberd-three-targets": _halberd_three_targets,
    "31-play-card-response": _play_card,
    "32-five-grains-selection": _five_grains,
    "33-borrowed-sword-use-slash": _borrowed_sword,
    "34-dismantle-or-snatch-equip-judge": _peel_equip_or_judge,
}
"""28 条 M2 基准的 slug → 事后判据。"""


def goal_for(slug: str):
    """取一条基准的判据；未登记即抛（录制期护栏）。"""
    try:
        return M2_GOALS[slug]
    except KeyError:  # pragma: no cover - 开发期护栏
        raise AssertionError(f"没有为场景 {slug} 写事后判据") from None
