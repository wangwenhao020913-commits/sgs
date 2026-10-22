"""M5-09a：人类座位载荷的**不泄露**判据（spec §6 A3）。

落点 = 接入层唯一过滤点（``sgs_web.projection``）：人类座位收到的**快照 / 挂起询问**
里，对手只有手牌**数量**、身份按「本人 / 主公 / 已亮明 / 阵亡」可见、牌堆只有数量；
【火攻】亮出的那张是公开信息，但目标**未亮出的手牌**不外泄，且亮牌不写任何「已知」
持久状态。

两条断言：

1. 真跑一局 **160 张 / 8 人**，逐次询问检查快照与挂起载荷（不是只看终局那一次）；
2. 构造局面里放一张【火攻】+ 目标两张手牌（只亮一张），逐条检查亮牌 / 弃牌两步。

口径与 M4 的 ``test_the_snapshot_never_exposes_other_hands_or_hidden_identity`` 同源，
只是 M5 把「逐次询问」与「火攻亮牌」两维补满。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import RuleConfig, load_content_pack  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.state import (  # noqa: E402
    EndPhase,
    Pass,
    QueryKind,
    SelectCards,
    UseCard,
)
from sgs_web.projection import pending_payload, visible_snapshot  # noqa: E402
from sgs_web.session import Session  # noqa: E402

from support import SeatSpec, build_state, cards_of_type  # noqa: E402

PACK = load_content_pack(deck_profile="standard_ex_160")
"""160 张档：不泄露判据要在含 11 张新牌的档位上成立。"""

EIGHT_SEED = 20261012
"""与 ``tests/test_web.sh`` 的军争正局同一个种子（同一批行为也能在门禁里复现）。"""


def _assert_player_payload(player: dict, *, human_seat: int) -> None:
    """一个座位在快照 / 挂起视角里的公开面（唯一读点 = 投影输出）。"""
    seat = player["seat"]
    if seat == human_seat:
        assert player["identity"] not in (None, ""), "本人身份必须可见"
        return
    assert "self_hand" not in player, f"对手手牌牌面泄露：{player}"
    assert "hand" not in player and "hand_cards" not in player
    assert isinstance(player.get("hand_count"), int), "对手手牌只给数量"
    assert "known" not in player and "revealed_cards" not in player, (
        f"手牌区出现「已知」持久状态：{player}"
    )
    if not player["alive"]:
        assert player["identity"] is not None, "阵亡者的身份应亮明"
    elif player["identity"] == "lord":
        pass  # 主公身份公开
    else:
        assert player["identity"] is None, f"存活非主公的身份泄露：{player}"


def _assert_snapshot(snapshot: dict, *, human_seat: int) -> None:
    assert snapshot["seat"] == human_seat
    assert isinstance(snapshot.get("self_hand"), list), "本人手牌必须可见"
    me = next(
        player for player in snapshot["players"] if player["seat"] == human_seat
    )
    assert me["hand_count"] == len(snapshot["self_hand"])
    assert isinstance(snapshot.get("deck_count"), int), "牌堆只给数量"
    assert "deck" not in snapshot and "deck_cards" not in snapshot, "牌堆顺序 / 牌面泄露"
    for player in snapshot["players"]:
        _assert_player_payload(player, human_seat=human_seat)


def _human_action(query) -> object:
    """最小合法应答（与 web_client 的"原始取第一条候选"同姿态）。"""
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def test_the_human_payload_of_a_full_160_game_never_leaks_hands_identities_or_deck() -> None:
    """逐次询问检查：160 张 8 人局的每一次快照 / 挂起载荷都不泄露。"""
    session = Session(
        seed=EIGHT_SEED,
        player_count=8,
        ai="heuristic",
        human_seat=0,
        timeout_seconds=None,
        pack=PACK,
    )
    checks = 0
    lord_seen = False
    while not session.finished:
        query = session.pending
        assert query is not None, "未终局却没有挂起询问"
        snapshot = visible_snapshot(session)
        _assert_snapshot(snapshot, human_seat=session.human_seat)
        payload = pending_payload(session)
        assert payload is not None
        inner = payload.get("view") or {}
        if inner.get("players"):
            for player in inner["players"]:
                _assert_player_payload(player, human_seat=session.human_seat)
        lord_seen = lord_seen or any(
            player["identity"] == "lord" for player in snapshot["players"]
        )
        checks += 1
        session.submit_answer(_human_action(query))

    assert checks > 10, f"这一局只检查了 {checks} 次询问——样本太少，判据不可信"
    assert lord_seen, "8 人身份局必须能看到主公身份（否则身份可见性判据是空转）"
    final = visible_snapshot(session)
    _assert_snapshot(final, human_seat=session.human_seat)
    assert session.engine.records[-1].kind == "game_end"


# ---------------------------------------------------------------------------
# 火攻亮牌：公开的那一张 vs 未亮出的手牌
# ---------------------------------------------------------------------------


class _MinimalSeat:
    """AI 座位的最小应答：出牌阶段结束、能放弃就放弃、其余取首条候选。"""

    def respond(self, query):  # noqa: ANN001 - Participant 协议
        return _human_action(query)


class _HaltSeat:
    """人类座位：把询问交给 ``Session.pending``，由用例显式应答。"""

    def respond(self, query):  # noqa: ANN001
        from sgs_web.session import Halt

        raise Halt(query)


def _fire_attack_session():
    """2 人构造局面：座位 0（人类）【火攻】座位 1；座位 1 两张手牌只亮一张。"""
    fire = cards_of_type(PACK, "fire_attack")[0]
    spares = [
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type != "fire_attack"
    ]
    discard = spares[0]
    reveal = spares[1]
    hidden = spares[2]
    used = {fire, discard, reveal, hidden}
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=(fire, discard)),
            SeatSpec(hp=4, hand=(reveal, hidden)),
        ],
        deck=tuple(index for index in range(len(PACK.deck.cards)) if index not in used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    session = Session(
        seed=1,
        player_count=2,
        ai="random",
        human_seat=0,
        hero_pool=None,
        timeout_seconds=None,
        pack=PACK,
    )
    session.engine = Engine.from_state(state, seed=1)
    session.participants = (_HaltSeat(), _MinimalSeat())
    session.drive()
    return session, fire, discard, reveal, hidden


def _fire_use(query, fire: int):
    """对**座位 1** 使用火攻（不能取到自指的那条候选）。"""
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.card_id == fire
            and action.targets == (1,)
        ):
            return action
    raise AssertionError("出牌阶段没有【火攻】→ 座位 1 的候选")


def _reveal_deltas(session: Session) -> list[dict]:
    return [
        json.loads(record.model_dump_json())
        for record in session.records()
        if getattr(record, "delta_type", None) == "card_reveal"
    ]


def test_the_fire_attack_reveal_is_public_but_the_unrevealed_hand_never_leaks() -> None:
    """亮出的那一张是公开事实；目标另一张手牌不出现在任何下发字节里。"""
    session, fire, discard, reveal, hidden = _fire_attack_session()
    query = session.pending
    assert query is not None and query.kind == QueryKind.PLAY_PHASE
    session.submit_answer(_fire_use(query, fire))

    # 走完无懈窗口（人类只是应答者，放弃），AI 目标亮牌后人类拿到弃同花色询问。
    for _ in range(20):
        query = session.pending
        if query is None or query.kind == QueryKind.SELECT_CARDS:
            break
        session.submit_answer(_human_action(query))
    assert query is not None and query.kind == QueryKind.SELECT_CARDS
    reveals = _reveal_deltas(session)
    assert len(reveals) == 1, "亮牌只落一条 card_reveal"
    shown = reveals[0]["card"]["card_id"]
    assert shown == reveal, "目标亮出的就是那张牌"
    assert shown != hidden

    payload = pending_payload(session)
    assert payload["reveal"] is not None, "使用者看得到亮出的那一张（公开信息）"
    assert payload["reveal"]["card"]["card_id"] == shown
    assert payload["reveal"]["card"].get("display_name")
    text = json.dumps(payload, ensure_ascii=False)
    assert f'"card_id": {hidden}' not in text, (
        "未亮出的手牌（实体牌 id）泄露到了使用者的载荷里"
    )
    target = next(
        player
        for player in visible_snapshot(session)["players"]
        if player["seat"] == 1
    )
    assert target["hand_count"] == 2, "亮牌不移动牌：目标仍有 2 张手牌"
    assert "self_hand" not in target and "known" not in target

    # 同花色弃牌 ⇒ 1 点火焰伤害；之后不残留任何「已知」状态。
    same_suit = [
        action
        for action in query.legal_actions
        if isinstance(action, SelectCards)
        and len(action.picks) == 1
        and query.slots[action.picks[0]].card is not None
        and query.slots[action.picks[0]].card.suit
        == session.engine.state.card(shown).suit
    ]
    assert same_suit, "构造局面：人类手里必须有同花色牌"
    session.submit_answer(same_suit[0])
    session.drive()
    for player in visible_snapshot(session)["players"]:
        assert "known" not in player and "revealed_cards" not in player, (
            f"火攻结算后手牌区残留「已知」状态：{player}"
        )
    assert len(_reveal_deltas(session)) == 1, "亮牌只在结算期落一条，不是持久字段"
