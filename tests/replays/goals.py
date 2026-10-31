"""M2 基准的**事后判据**（票 32 §1）。

M1 的六条基准用 ``ScenarioPlayer`` 在**录制期**迫使目标链发生；M2 的 27 条改用
**随机驱动 + 事后判据**：录制时用与产品同源的 :class:`~sgs.agents.RandomPlayer`
（测试侧种子子流，确定性）跑完一整局，再断言"这条基准要证的那件事真的发生过"。
磁带冻结的是**决策内容**，回放期一律 :class:`~sgs.agents.TapePlayer`，与驱动无关。

判据只读**记录流与终局态**，所以它们同时也是"这条基准到底在证什么"的可读规格。
种子由开发期搜索得到（``tests/replays/seed_search.py``），写死在 ``scenarios.py`` 里。
"""

from __future__ import annotations

from sgs.data import Identity
from sgs.engine import Engine, JournalRecord
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    MoveReason,
    PlayCard,
    QueryKind,
    SlotKind,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

__all__ = [
    "M1_GOALS",
    "M2_GOALS",
    "M3_GOALS",
    "M5_GOALS",
    "M6_GOALS",
    "PREACTIVATION_CANCEL_SKILL",
    "goal_for",
]


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
# 27 条 M2 基准的判据
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
    "34-dismantle-or-snatch-equip-judge": _peel_equip_or_judge,
}
"""27 条 M2 基准的 slug → 事后判据。"""


# ---------------------------------------------------------------------------
# M3 专项基准的判据（票 14 §8）
# ---------------------------------------------------------------------------


def _skill_activations(engine: Engine, skill_id: str) -> list[JournalRecord]:
    """被接受、且 ``skill_id`` 命中的 ``ActivateSkill`` 行动记录。"""
    return [
        record
        for record in _accepted(engine)
        if isinstance(record.action, ActivateSkill) and record.action.skill_id == skill_id
    ]


def _window_cause(engine: Engine, cause: WindowCause) -> bool:
    return any(
        record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and record.query.cause is cause
        for record in _records(engine)
    )


def _yiji_triggers_per_damage_point(engine: Engine) -> bool:
    """郭嘉【遗计】逐点触发：一次伤害里问两次、发动两次（B 族的 ``per_point``）。"""
    return len(_skill_activations(engine, "hero.guojia.yiji")) >= 2


def _guicai_rejudge_cycle(engine: Engine) -> bool:
    """【鬼才】改判窗口真的开过（``WindowCause.REJUDGE``；票 11 的循环）。"""
    return _window_cause(engine, WindowCause.REJUDGE)


def _lord_request(engine: Engine) -> bool:
    """【护驾】/【激将】的**势力求助窗口**真的开过（``WindowCause.LORD_REQUEST``）。"""
    return _window_cause(engine, WindowCause.LORD_REQUEST)


def _lost_cards_family(engine: Engine) -> bool:
    """【连营】与【枭姬】各发动过，且有一次**顶替**（装备 ``LOSE``）留下记录。"""
    replaced = any(
        move.from_zone is not None
        and move.from_zone.zone is Zone.EQUIP
        and move.reason is MoveReason.LOSE
        for move in _card_moves(engine)
    )
    return (
        bool(_skill_activations(engine, "hero.luxun.lianying"))
        and bool(_skill_activations(engine, "hero.sunshangxiang.xiaoji"))
        and replaced
    )


def _hand_sizes_over_time(engine: Engine) -> list[tuple[int, list[int]]]:
    """记录流里每次 ``card_move`` **之前**的各座位手牌数（``(seq, sizes)``）。

    用来判 C 族的条件前提（【空城】只在"没有手牌"时把目标挡出池）：记录流本身不含
    手牌张数，故从 delta 增量重建——与 fuzz 的 delta 重建同一口径。
    """
    sizes = [0] * len(engine.state.players)
    snapshots: list[tuple[int, list[int]]] = []
    for record in _records(engine):
        if record.kind != "delta" or record.delta_type != "card_move":
            continue
        snapshots.append((record.seq, list(sizes)))
        count = len(record.cards)
        if record.from_zone is not None and record.from_zone.zone is Zone.HAND:
            sizes[record.from_zone.seat] -= count
        if record.to_zone is not None and record.to_zone.zone is Zone.HAND:
            sizes[record.to_zone.seat] += count
    return snapshots


def _target_pool_exclusion(engine: Engine) -> bool:
    """【空城】/【谦逊】的目标池真排除在整局里可观测（C 族）。

    判据读**使用行动的 ``targets``**：受保护座位（诸葛亮 / 陆逊）在一次受限牌的使用里
    **不在任何候选目标里**，而同一张牌确实指到了别人——

    - 【谦逊】无条件：陆逊不可被【顺手牵羊】/【过河拆桥】指定；
    - 【空城】有条件：诸葛亮**手牌为 0** 时不可被【杀】/【决斗】指定（前提由
      :func:`_hand_sizes_over_time` 重建）。
    """
    heroes = {player.seat: player.hero for player in engine.state.players}
    qianxun = next((seat for seat, hero in heroes.items() if hero == "luxun"), None)
    kongcheng = next((seat for seat, hero in heroes.items() if hero == "zhugeliang"), None)

    sizes_before = dict(_hand_sizes_over_time(engine))

    def _used(types: frozenset[str]):
        return [
            record
            for record in _accepted(engine)
            if isinstance(record.action, (UseCard, PlayCard))
            and getattr(record.action, "targets", ())
            and record.action.card.type in types
        ]

    def _excluded(record, seat: int) -> bool:
        return seat not in record.action.targets

    qianxun_ok = qianxun is not None and any(
        _excluded(record, qianxun) for record in _used(frozenset({"snatch", "dismantlement"}))
    )
    kongcheng_ok = kongcheng is not None and any(
        sizes_before.get(record.seq, [0] * len(heroes))[kongcheng] == 0
        and _excluded(record, kongcheng)
        for record in _used(frozenset({"slash", "duel"}))
    )
    return qianxun_ok and kongcheng_ok


def _wushuang_two_responses(engine: Engine) -> bool:
    """吕布【无双】：同一次响应里连开两个同类型窗口（两个连续的 ``DODGE`` 询问）。"""
    run = best = 0
    for record in _records(engine):
        if record.kind != "query":
            continue
        if record.query.kind == QueryKind.DODGE:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best >= 2


def _guanxing_arranged(engine: Engine) -> bool:
    """诸葛亮【观星】真的排过一次牌（``ArrangeCards`` 被接受）。"""
    return any(
        record.kind == "action" and record.accepted and isinstance(record.action, ArrangeCards)
        for record in _records(engine)
    )


def _transform_family(engine: Engine) -> bool:
    """H 族的转化：某次使用 / 打出带着**非空** ``virtual_sources``（武圣 / 龙胆 / 国色 / 急救）。"""
    heroes = {player.seat: player.hero for player in engine.state.players}
    transform_heroes = {"guanyu", "zhaoyun", "daqiao", "huatuo"}
    actor: int | None = None
    for record in _records(engine):
        if record.kind == "query":
            actor = record.query.actor
        elif record.kind == "action" and record.accepted:
            action = record.action
            if isinstance(action, (UseCard, PlayCard)) and action.card.virtual_sources:
                if actor is not None and heroes.get(actor) in transform_heroes:
                    return True
    return False


M3_GOALS = {
    "35-hero-damage-points": _yiji_triggers_per_damage_point,
    "36-hero-rejudge-cycle": _guicai_rejudge_cycle,
    "37-hero-lord-request": _lord_request,
    "38-hero-lost-cards": _lost_cards_family,
    "39-hero-target-pool": _target_pool_exclusion,
    "40-hero-wushuang": _wushuang_two_responses,
    "41-hero-guanxing": _guanxing_arranged,
    "42-hero-transform": _transform_family,
}
"""8 条 M3 专项基准的 slug → 事后判据（票 14 §8 的逐类清单）。"""


# ---------------------------------------------------------------------------
# 脚本基准的事后判据（票 17）
# ---------------------------------------------------------------------------


def _double_sword_chain(engine: Engine) -> bool:
    """【雌雄双股剑】的整条链真的发生过（票 17，脚本基准 slug 43）。

    判据分两半：**询问**（谁被问、问什么）与**结算**（目标选了哪一支、那一支真的落地）。
    它只读记录流，故同时也是"这条基准到底在证什么"的可读规格：

    1. 存在一次选项询问，恰是 ``("discard_one", "draw_one")``——这一对选项在本引擎里
       只有【雌雄双股剑】会发；
    2. 该询问的 ``actor`` 是**目标**（座 1），不是使用者（座 0）——票 12 §Q17 的选择权归属；
    3. 座 1 答了 ``draw_one``，且随后**座 0 摸了一张**（``to_zone = HAND(0)``、
       ``reason = DRAW``）——"令其摸一张"必须真的让使用者摸到；
    4. 全程**没有**给座 2 / 座 3（与座 0 同性）发过【雌雄】的选项询问——同性不触发。
    """
    option_records = [
        record
        for record in _queries(engine)
        if record.query.kind == QueryKind.SELECT_OPTION
        and record.query.options == ("discard_one", "draw_one")
    ]
    if len(option_records) != 1:
        return False
    option = option_records[0]
    if option.query.actor != 1:
        return False
    if any(
        record.query.kind == QueryKind.SELECT_OPTION and record.query.actor in (2, 3)
        for record in _queries(engine)
    ):
        return False
    answered = any(
        record.kind == "action"
        and record.accepted
        and record.seq == option.seq + 1
        and getattr(record.action, "choice", None) == "draw_one"
        for record in _records(engine)
    )
    if not answered:
        return False
    return any(
        record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == 0
        and record.seq > option.seq
        for record in _records(engine)
    )


PREACTIVATION_CANCEL_SKILL = "hero.sunquan.zhiheng"
"""W4-5 取消专项里被"进预发动 → 取消"的技能（座 0 = 孙权）。

定义在这里而不是场景表里：判据与场景共用同一个 id，两侧不会各写一份而漂移
（``scenarios.py`` 从这里 import）。"""


def _preactivation_cancel_records_nothing(engine: Engine) -> bool:
    """预发动**取消零副作用**（W4-5，脚本基准 44）：进得去、填得出、但一个 action 都没有。

    判据读**记录流里的询问与行动**：

    1. 至少有一条 ``PLAY_PHASE`` 询问随附【制衡】的预发动清单——那一刻技能可用、接入层
       能进入预发动（清单是引擎下发的真值，取消专项的前提）；
    2. 整局**没有**任何被接受的 ``ActivateSkill`` 命中它——取消（不提交）与"从未发动"
       在记录流里同形；
    3. 记录流照常走完（终局由 ``record()`` 另行断言）。

    第 2 条只读到"没发动"；取消路径与不发动路径**逐字节相等**由
    ``scenarios.preactivation_cancel_pair`` + ``tests/test_replays.py`` 的同种子双跑钉住，
    因为"取消"本身是接入层本地态，记录流里不该有它的位置。
    """
    offered = [
        record
        for record in _queries(engine)
        if record.query.kind == QueryKind.PLAY_PHASE
        and PREACTIVATION_CANCEL_SKILL in record.query.preactivations
    ]
    if not offered:
        return False
    return not _skill_activations(engine, PREACTIVATION_CANCEL_SKILL)


def _borrowed_use_with_designation(engine: Engine) -> bool:
    """借刀以**附带指定**的复合使用落账过（W5-02b 起入口只剩清单）。"""
    return any(
        isinstance(record.action, UseCard)
        and record.action.card.type == "borrowed_sword"
        and record.action.designations
        for record in _accepted(engine)
    )


def _borrowed_auto_handover(engine: Engine) -> bool:
    """auto-b（脚本基准 45）：**没有持刀者窗口**，但武器从装备区进了使用者手牌。

    判据三条，缺一不可：
    1. 借刀以带 ``designations`` 的复合使用落账（入口 = 清单）；
    2. 整局**没有** ``BORROWED_SWORD`` 询问——a 不可行 ⇒ 不 ``ask_respond``；
    3. 一次 ``reason = LOSE`` 的装备区 → 手牌移动，武器牌进座位 0（使用者）。
    """
    if not _borrowed_use_with_designation(engine):
        return False
    if _window_cause(engine, WindowCause.BORROWED_SWORD):
        return False
    return any(
        move.reason is MoveReason.LOSE
        and move.from_zone is not None
        and move.from_zone.zone is Zone.EQUIP
        and move.to_zone is not None
        and move.to_zone.zone is Zone.HAND
        and move.to_zone.seat == 0
        and any(card.slot is SlotKind.WEAPON for card in move.cards)
        for move in _card_moves(engine)
    )


def _borrowed_transform_slash(engine: Engine) -> bool:
    """借刀窗口里的**转化【杀】**（脚本基准 46）：窗口开过，且之后落账的是虚拟【杀】。

    判据只读记录流：``BORROWED_SWORD`` 询问之后有一条被接受的 ``UseCard``，
    牌面是【杀】、``virtual_sources`` 非空（转化牌）、目标非空（固定为 Bn）。
    """
    windows = [
        record.seq
        for record in _queries(engine)
        if record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.BORROWED_SWORD
    ]
    if not windows:
        return False
    first = min(windows)
    return any(
        record.seq > first
        and isinstance(record.action, UseCard)
        and record.action.card.type == "slash"
        and record.action.card.virtual_sources
        and record.action.targets
        for record in _accepted(engine)
    )


def _halberd_borrowed_multi(engine: Engine) -> bool:
    """借刀窗口里的**方天多目标真【杀】**（W7-01，脚本基准 47）：窗口形状 + 结算落点。

    判据只读记录流，四条缺一不可：

    1. **外层**借刀以 ``designations=(0,)``、``targets=(1,)`` 落账——附带指定与目标的主体
       分列不变（W5 语义）；
    2. 持刀者窗口（``BORROWED_SWORD``）的【杀】候选里**恰好一组 3 目标**组合，且每条候选
       都恒含 Bn=0（方天"最后一张手牌"的 max = 3）；
    3. 窗口的 ``view`` 是**持刀者**的，且其 ``self_hand`` 恰是那张【杀】——"使用前是最后
       一张手牌"这个触发条件在窗口那一刻成立；
    4. 窗口之后落账的【杀】``UseCard`` 的 ``targets`` 恰是那组 3 目标、``designations``
       **为空**（Bn 与额外目标都在 ``targets`` 里）。
    """
    outer = [
        record.action
        for record in _accepted(engine)
        if isinstance(record.action, UseCard)
        and record.action.card.type == "borrowed_sword"
    ]
    if not outer or outer[-1].designations != (0,) or outer[-1].targets != (1,):
        return False
    windows = [
        record.query
        for record in _queries(engine)
        if record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.BORROWED_SWORD
    ]
    if len(windows) != 1:
        return False
    window = windows[0]
    offers = [
        action
        for action in window.legal_actions
        if isinstance(action, UseCard) and action.card.type == "slash"
    ]
    three = [action for action in offers if len(action.targets) == 3]
    if len(three) != 1 or any(0 not in action.targets for action in offers):
        return False
    chosen = three[0]
    hand = [card.card_id for card in window.view.self_hand]
    if hand != [chosen.card.card_id]:
        return False
    return any(
        record.seq > window.seq
        and isinstance(record.action, UseCard)
        and record.action.card.card_id == chosen.card.card_id
        and record.action.targets == chosen.targets
        and record.action.designations == ()
        for record in _accepted(engine)
    )


# ---------------------------------------------------------------------------
# M5 军争构造基准的判据（M5-09a / spec §6 A3）
#
# 8 条基准从**构造局面**起跑（``replays/m5.py``），判据只读记录流与终局态：
# 每个覆盖项都落到一条可观测事实（牌名使用 / 一条 delta / 装备区 / 时机序列），
# 而不是"场景脚本宣布命中"。
# ---------------------------------------------------------------------------


def _m5_uses(engine: Engine, *type_ids: str) -> list[tuple[int, UseCard]]:
    """被接受、且牌名在 ``type_ids`` 里的 ``UseCard``：``(seq, action)``。"""
    return [
        (record.seq, record.action)
        for record in _accepted(engine)
        if isinstance(record.action, UseCard) and record.action.card.type in type_ids
    ]


def _m5_moved_to_equip(engine: Engine, seat: int, type_id: str) -> bool:
    """装备是**这局里真的装上**的：记录流里有 ``PUT → EQUIP(seat)`` 的移动。

    不看终局装备区：目标阵亡后装备随死亡结算进弃牌堆，终局态读不到它。
    """
    return any(
        move.to_zone.zone is Zone.EQUIP
        and move.to_zone.seat == seat
        and any(card.type == type_id for card in move.cards)
        for move in _card_moves(engine)
    )


def _m5_hp(engine: Engine, seat: int) -> list[JournalRecord]:
    return [record for record in _hp_changes(engine) if record.seat == seat]


def _m5_chained(engine: Engine) -> list[tuple[int, bool]]:
    return [(record.seat, record.chained) for record in _deltas(engine, "chained_change")]


def _m5_wine(engine: Engine) -> list[tuple[int, bool]]:
    return [(record.seat, record.active) for record in _deltas(engine, "wine_change")]


def _m5_reveals(engine: Engine) -> list[JournalRecord]:
    return _deltas(engine, "card_reveal")


def _m5_card_moves(engine: Engine) -> list[JournalRecord]:
    return _card_moves(engine)


def _m5_fire_vs_vine(engine: Engine) -> bool:
    """48：普通【杀】对藤甲无效（零伤害）⇒ 火【杀】+1（2 点伤害）。"""
    normal = _m5_uses(engine, "slash")
    fire = _m5_uses(engine, "fire_slash")
    hp = _m5_hp(engine, 1)
    return (
        bool(normal)
        and bool(fire)
        and len(hp) == 1
        and hp[0].amount == -2
        and _m5_moved_to_equip(engine, 1, "vine_armor")
    )


def _m5_chain_conduction(engine: Engine) -> bool:
    """49：铁索横置双方 → 雷【杀】的属性伤害沿连环传导，命中后各座重置。"""
    iron = [action for _seq, action in _m5_uses(engine, "iron_chain")]
    thunder = _m5_uses(engine, "thunder_slash")
    chained = _m5_chained(engine)
    hp = _m5_hp(engine, 0) + _m5_hp(engine, 1)
    return (
        bool(iron)
        and iron[0].targets == (0, 1)
        and bool(thunder)
        and thunder[0][1].targets == (1,)
        and {(seat, True) for seat in (0, 1)} <= set(chained)
        and {(seat, False) for seat in (0, 1)} <= set(chained)
        and {record.seat for record in hp} == {0, 1}
    )


def _m5_wine_silver_cap(engine: Engine) -> bool:
    """50：【酒】置位 → 那一张【杀】的伤害被白银狮子从 2 压回 1。"""
    wine = _m5_uses(engine, "wine")
    slash = _m5_uses(engine, "slash")
    changes = _m5_wine(engine)
    hp = _m5_hp(engine, 1)
    if not (wine and slash and changes and hp):
        return False
    wine_on = [seat for seat, active in changes if active]
    wine_off = [seat for seat, active in changes if not active]
    first_slash_seq = slash[0][0]
    after = [record for record in hp if record.seq > first_slash_seq]
    return (
        wine[0][1].targets == (0,)
        and 0 in wine_on
        and 0 in wine_off
        and bool(after)
        and after[0].amount == -1
        and _m5_moved_to_equip(engine, 1, "silver_lion")
    )


def _m5_fire_attack(engine: Engine) -> bool:
    """51：火攻亮出目标的一张手牌（公开事实）→ 同花色弃牌 → 1 点火焰伤害。"""
    use = _m5_uses(engine, "fire_attack")
    reveals = _m5_reveals(engine)
    if not use or not reveals:
        return False
    reveal = reveals[0]
    hp = _m5_hp(engine, reveal.seat)
    return (
        use[0][1].targets == (reveal.seat,)
        and reveal.seat == 1
        and bool(hp)
        and hp[0].amount == -1
        and hp[0].seq > reveal.seq
    )


def _m5_scimitar_hualiu(engine: Engine) -> bool:
    """52：古锭刀（目标无手牌 +1）造成 2 点伤害；骅骝的 +1 距离被攻击范围 2 抵消。"""
    slash = _m5_uses(engine, "slash")
    hp = _m5_hp(engine, 1)
    return (
        bool(slash)
        and slash[0][1].targets == (1,)
        and len(hp) == 1
        and hp[0].amount == -2
        and _m5_moved_to_equip(engine, 0, "ancient_scimitar")
        and _m5_moved_to_equip(engine, 1, "hualiu")
    )


def _m5_turn_spans(engine: Engine) -> list[tuple[int, int, int]]:
    """每个回合的 ``(回合角色, TURN_START seq, 下一个 TURN_START seq)``；末回合以流末 seq 收尾。"""
    starts = [
        (record.actor, record.seq)
        for record in _timings(engine)
        if record.timing is Timing.TURN_START
    ]
    last = _records(engine)[-1].seq
    return [
        (actor, seq, starts[index + 1][1] if index + 1 < len(starts) else last + 1)
        for index, (actor, seq) in enumerate(starts)
    ]


def _m5_supply_shortage(engine: Engine) -> bool:
    """53：兵粮寸断判定非梅花 ⇒ 该回合跳过摸牌阶段（判定牌序列里没有 DRAW）。

    判定牌本身是一条 ``reason=JUDGE`` 的 ``card_move``（兵粮本体随后以 ``USE`` 进弃牌堆），
    故这里只认"这个回合里出现过 JUDGE 移动"，再看同一回合有没有该角色的摸牌。
    """
    put = [
        move
        for move in _m5_card_moves(engine)
        if move.to_zone.zone is Zone.JUDGE
        and any(card.type == "supply_shortage" for card in move.cards)
    ]
    if not put:
        return False
    for actor, start, end in _m5_turn_spans(engine):
        span = [move for move in _m5_card_moves(engine) if start <= move.seq < end]
        judged = any(move.reason is MoveReason.JUDGE for move in span)
        drew = [
            move
            for move in span
            if move.reason is MoveReason.DRAW and move.owner == actor
        ]
        if judged and not drew:
            return True
    return False


def _m5_reforge(engine: Engine) -> bool:
    """54：重铸是独立 Action，牌按 ``REFORGE`` 进弃牌堆、随后摸一张，且不开无懈窗口。"""
    reforges = [
        record for record in _accepted(engine) if record.action.kind == "Reforge"
    ]
    if not reforges:
        return False
    reforge = reforges[0]
    if reforge.action.card.type != "iron_chain":
        return False
    moved = next(
        (
            move.seq
            for move in _m5_card_moves(engine)
            if move.reason is MoveReason.REFORGE
            and any(card.card_id == reforge.action.card.card_id for card in move.cards)
        ),
        None,
    )
    if moved is None:
        return False
    drew = any(
        move.reason is MoveReason.DRAW
        and move.owner == 0
        and move.seq > moved
        for move in _m5_card_moves(engine)
    )
    no_nullify = not any(
        record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.NULLIFY
        and reforge.seq < record.seq < moved + 2
        for record in _records(engine)
    )
    return drew and no_nullify


def _m5_fan(engine: Engine) -> bool:
    """55：羽扇按**询问式**（票 09）把这次使用的普通【杀】转成火【杀】并命中座位 1。

    票 09 起羽扇是「当你使用普通【杀】时，可以将其转化为火【杀】」：转化发生在**这次
    使用内部**，记录流里的 ``UseCard`` 仍是玩家选的那张普通【杀】（不是虚拟产物），
    故判据读三件事：① 座位 0 真的装上了羽扇；② 羽扇的「是否发动」被**接受**
    （``ActivateSkill``，零步 ⇒ 无 picks）；③ 之后座位 1 掉 1 点体力。

    火属性本身不在记录流里（``hp_change`` 只记 amount），由
    ``tests/test_m5_fan.py::test_the_fan_product_deals_fire_damage_and_sends_the_source_to_the_discard_pile``
    钉住；本判据只证这条基准走到了"装扇 → 用【杀】→ 问 → 发动 → 命中"。
    """
    asks = [
        record.seq
        for record in _accepted(engine)
        if isinstance(record.action, ActivateSkill)
        and record.action.skill_id == "equip.vermilion_feather_fan"
    ]
    if not asks:
        return False
    ask = asks[0]
    used_before = [seq for seq, _action in _m5_uses(engine, "slash") if seq < ask]
    hp = _m5_hp(engine, 1)
    return (
        bool(used_before)
        and _m5_moved_to_equip(engine, 0, "vermilion_feather_fan")
        and any(record.seq > ask and record.amount == -1 for record in hp)
    )


M5_GOALS = {
    "48-fire-slash-vine-armor": _m5_fire_vs_vine,
    "49-iron-chain-thunder-conduction": _m5_chain_conduction,
    "50-wine-silver-lion-cap": _m5_wine_silver_cap,
    "51-fire-attack-reveal-discard": _m5_fire_attack,
    "52-scimitar-empty-hand-hualiu": _m5_scimitar_hualiu,
    "53-supply-shortage-skips-draw": _m5_supply_shortage,
    "54-reforge-iron-chain": _m5_reforge,
    "55-fan-converts-normal-slash": _m5_fan,
}
"""8 条 M5 构造基准的 slug → 事后判据（spec §6 A3 的 10 个覆盖项）。"""


# ---------------------------------------------------------------------------
# M6 界局构造基准（票 20）：各覆盖 spec §投影与记录流 的一类新原语
# ---------------------------------------------------------------------------


def _m6_suppress(engine: Engine) -> bool:
    """56：界关羽【义绝】弃黑牌 ⇒ 座位 1 的**技能失效**，回合末**到期**（一真一假两条）。"""
    records = [
        record
        for record in _deltas(engine, "skill_suppress")
        if record.seat == 1
    ]
    applied = [record for record in records if record.suppressed]
    cleared = [record for record in records if not record.suppressed]
    return (
        any(record.skill_id == "hero.jie_guanyu.yijue" for record in applied)
        and bool(cleared)
        and applied[0].seq < cleared[0].seq
        and any(
            isinstance(action, ActivateSkill)
            and action.skill_id == "hero.jie_guanyu.yijue"
            for action in (record.action for record in _accepted(engine))
        )
    )


def _m6_awakening(engine: Engine) -> bool:
    """57：造成伤害后【潜心】觉醒（上限 -1 + 获得 ☆【荐言】），随后 ☆ 技能真的被发动。"""
    maxima = [record for record in _deltas(engine, "hp_max_change") if record.seat == 0]
    gained = [
        record
        for record in _deltas(engine, "skill_gain")
        if record.seat == 0 and record.skill_id == "hero.jie_xushu.jianyan"
    ]
    activated = [
        record.action
        for record in _accepted(engine)
        if isinstance(record.action, ActivateSkill)
        and record.action.skill_id == "hero.jie_xushu.jianyan"
    ]
    return (
        bool(maxima)
        and maxima[0].before == 4
        and maxima[0].after == 3
        and bool(gained)
        and maxima[0].seq < gained[0].seq
        and bool(activated)
        and gained[0].seq < activated[0].answers_seq
    )


QIANXUN_ZONE = "0:hero.jie_luxun.qianxun"
"""56/58 用例里【谦逊】区的规范键（与 ``skills.hero_card_zone_key`` 同形）。"""


def _hero_zone_moves(engine: Engine) -> tuple[list, list]:
    """一次 HERO 区域进 / 出（区域引用带 ``skill_instance`` 的那些 card_move）。"""
    entered = []
    left = []
    for record in _card_moves(engine):
        if record.to_zone.zone is Zone.HERO:
            entered.append(record)
        if record.from_zone.zone is Zone.HERO:
            left.append(record)
    return entered, left


def _m6_hero_zone(engine: Engine) -> bool:
    """58：【谦逊】把全部手牌置于武将牌上（进入 HERO 区），回合末**整批归还**。"""
    entered, left = _hero_zone_moves(engine)
    zone = next(
        (
            record
            for record in entered
            if record.to_zone.skill_instance == QIANXUN_ZONE
        ),
        None,
    )
    returned = next(
        (
            record
            for record in left
            if record.from_zone.skill_instance == QIANXUN_ZONE
            and record.to_zone.zone is Zone.HAND
            and record.to_zone.seat == 0
        ),
        None,
    )
    return (
        zone is not None
        and returned is not None
        and zone.seq < returned.seq
        and engine.state.hero_zone(0, QIANXUN_ZONE) == ()
        and any(
            isinstance(action, ActivateSkill)
            and action.skill_id == "hero.jie_luxun.qianxun"
            for action in (record.action for record in _accepted(engine))
        )
    )


def _m6_extra_turn(engine: Engine) -> bool:
    """59：【博图】的额外回合（``extra=True``）与之后的轮次变化（``extra=False, round=2``）。"""
    changes = [
        (record.seat, record.round, record.extra)
        for record in _deltas(engine, "turn_change")
    ]
    return (0, 1, True) in changes and (0, 2, False) in changes


def _m6_tags(engine: Engine) -> bool:
    """60：【洛神】获得黑牌挂标签（``tag_add``），回合末清除（``tag_remove``）。"""
    added = _deltas(engine, "tag_add")
    removed = _deltas(engine, "tag_remove")
    if not added or not removed:
        return False
    first = added[0]
    match = next(
        (
            record
            for record in removed
            if record.card.card_id == first.card.card_id
            and record.tag_type is first.tag_type
        ),
        None,
    )
    return (
        first.tag_type.value == "luoshen_hand"
        and first.skill_id == "hero.jie_zhenji.luoshen"
        and match is not None
        and first.seq < match.seq
    )


M6_GOALS = {
    "56-jie-guanyu-yijue-suppress": _m6_suppress,
    "57-jie-xushu-awakening": _m6_awakening,
    "58-jie-luxun-hero-zone": _m6_hero_zone,
    "59-jie-lvmeng-extra-turn": _m6_extra_turn,
    "60-jie-zhenji-luoshen-tags": _m6_tags,
}
"""5 条 M6 界局构造基准的 slug → 事后判据（spec §投影与记录流 的新原语）。"""


M1_GOALS = {
    "33-borrowed-sword-use-slash": _borrowed_sword,
    "43-double-sword-opposite-gender": _double_sword_chain,
    "44-preactivation-cancel-no-side-effect": _preactivation_cancel_records_nothing,
    "45-borrowed-sword-auto-handover": _borrowed_auto_handover,
    "46-borrowed-sword-transform-slash": _borrowed_transform_slash,
    "47-halberd-borrowed-sword": _halberd_borrowed_multi,
}
"""脚本基准的 slug → 事后判据（票 17 起；只收"脚本驱动 + 需要事后确认"的那条）。

为什么脚本基准也要事后判据：``_record_scripted`` 只保证**目标段命中过**
（``recording.goal_done``），而"命中"是脚本自己宣布的。判据在这里独立地把
"询问的形状 + 结算的落点"重新读一遍，免得脚本宣布命中、记录流里却没那回事。
"""


def goal_for(slug: str):
    """取一条基准的判据；未登记即抛（录制期护栏）。"""
    table = {**M1_GOALS, **M2_GOALS, **M3_GOALS, **M5_GOALS, **M6_GOALS}
    try:
        return table[slug]
    except KeyError:  # pragma: no cover - 开发期护栏
        raise AssertionError(f"没有为场景 {slug} 写事后判据") from None
