"""M5-09a / 09c：人类座位载荷的**不泄露**判据（spec §6 A3）。

落点 = 接入层唯一过滤点（``sgs_web.projection``）：人类座位收到的**快照 / 挂起询问**
里，对手只有手牌**数量**、身份按「本人 / 主公 / 已亮明 / 阵亡」可见、牌堆只有数量；
【火攻】亮出的那张是公开信息，但目标**未亮出的手牌**不外泄，且亮牌不写任何「已知」
持久状态。

三条面（09a 补前两条，09c 补第三条）：

1. 真跑一局 **160 张 / 8 人**，逐次询问检查快照与挂起载荷（不是只看终局那一次）；
2. 构造局面里放一张【火攻】+ 目标两张手牌（只亮一张），逐条检查亮牌 / 弃牌两步；
3. **日志 / 记录流**（09c）：``card_move`` 的牌面按区域可见性裁剪——他人摸牌 / 隐藏
   手牌移动只给数量（``card_count``），公开区与自己的移动照发牌面；``card_reveal``
   仍公开。同一局也是「他人摸到的牌面」既有的泄露面（09a 当时没查）。

M6 票 20 把**新增的 delta 类型**（标签增删）补进逐类判据表，并给**武将牌上的牌区**
（HERO 区域）补一条区域行：区可见性读 ``PlacementDecl.visibility``，不是"HERO 一律
隐藏"——数据里【谦逊】是公开区，原先按隐藏区裁是**过度隐藏**。

口径与 M4 的 ``test_the_snapshot_never_exposes_other_hands_or_hidden_identity`` 同源，
只是 M5 把「逐次询问」「火攻亮牌」「日志记录」三维补满。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import Identity, RuleConfig, load_content_pack  # noqa: E402
from sgs.engine import (  # noqa: E402
    CardMoveRecord,
    CardRevealRecord,
    PindianRecord,
    ChainedChangeRecord,
    FlippedChangeRecord,
    DeckShuffleRecord,
    Engine,
    HpChangeRecord,
    HpMaxChangeRecord,
    IdentityDealRecord,
    IdentityRevealRecord,
    MarkerChangeRecord,
    SkillGainRecord,
    SkillLoseRecord,
    SkillSuppressRecord,
    TagAddRecord,
    TagRemoveRecord,
    TurnChangeRecord,
    WineChangeRecord,
    record_json,
)
from sgs.state import (  # noqa: E402
    CardRef,
    EndPhase,
    MoveReason,
    Pass,
    QueryKind,
    SelectCards,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs_web.projection import (  # noqa: E402
    pending_payload,
    visible_record,
    visible_snapshot,
)
from sgs_web.serve import GameRegistry  # noqa: E402
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
# 日志 / 记录流：隐藏区 card_move 只给数量（M5-09c / spec §6 A3）
# ---------------------------------------------------------------------------

LOG_PUBLIC_ZONES = frozenset({"discard", "processing", "equip", "judge"})
"""A3 的公开区闭集。测试侧独立写一份判据，不复用实现里的 helper——否则断言在自证。"""


def _card_move_visible_to(card_move: dict, viewer: int) -> bool:
    """一张牌移动的牌面**是否该**对 ``viewer`` 可见（spec §6 A3 的判据，独立写一遍）。"""
    source = card_move["from_zone"]
    target = card_move["to_zone"]
    # 武将牌上的牌（M6 票 10 / huo-shenzhugeliang 票 01）：**牌面只给宿主**（用户 2026-10
    # 口径，`tools/sgs_web/visibility.py::hero_zone_ref_visible`）⇒ 宿主本人看得见自己的
    # 「星」/【谦逊】的牌，别人只拿张数。这一档**优先于**"公开区"（服务端 `cards_visible`
    # 的先手分支：只要移动任一侧是 hero，就按宿主判，不再让"来源/去向是公开区"绕开裁剪）。
    if source["zone"] == "hero" or target["zone"] == "hero":
        hero_ref = target if target["zone"] == "hero" else source
        return hero_ref.get("seat") == viewer
    if source["zone"] in LOG_PUBLIC_ZONES or target["zone"] in LOG_PUBLIC_ZONES:
        return True
    if target["zone"] == "hand" and target.get("seat") == viewer:
        return True
    if source["zone"] == "hand" and source.get("seat") == viewer:
        return True
    return False


def _assert_card_faces_absent(payload: dict, cards: tuple[dict, ...]) -> None:
    """脱敏形态：``cards`` 为空 + ``card_count``，且下发字节里没有牌面字段。"""
    assert payload["cards"] == [], f"隐藏区牌面泄露：{payload}"
    assert payload.get("card_count") == len(cards), (
        f"脱敏后必须给出张数（应有 {len(cards)} 张）：{payload}"
    )
    text = json.dumps(payload, ensure_ascii=False)
    for key in ("card_id", "name", "suit", "rank"):
        assert f'"{key}"' not in text, f"脱敏记录仍带牌面字段 {key}：{payload}"


def test_the_human_log_records_never_name_hidden_card_moves_in_a_full_160_game() -> None:
    """逐次询问检查 160 张 8 人局的**人类日志记录**：隐藏区移动只有数量，没有牌面。

    这是 M5-09a 没查到的那个面：快照 / 挂起载荷当时都裁好了，但 ``visible_record`` 把
    ``CardMoveDelta.cards`` 原样下发——其他座位从牌堆摸到的牌名就在日志里。
    判据按区域：公开区（弃牌堆 / 处理区 / 装备区 / 判定区）照常可见；牌堆 / 手牌这类
    隐藏区只有查看者自己经手的那一侧可见；其余只给数量。
    """
    session = Session(
        seed=EIGHT_SEED,
        player_count=8,
        ai="heuristic",
        human_seat=0,
        timeout_seconds=None,
        pack=PACK,
    )
    human = session.human_seat
    stats = {
        "other_draw": 0,
        "own_draw": 0,
        "public": 0,
        "hidden_hand_to_hand": 0,
        "redacted": 0,
    }
    public_zones_seen: set[str] = set()
    after = -1
    while True:
        for record in session.records_after(after):
            if getattr(record, "kind", None) != "delta":
                continue
            if getattr(record, "delta_type", None) != "card_move":
                continue
            raw = json.loads(record_json(record))
            payload = visible_record(record, viewer=human)
            assert payload is not None
            cards = tuple(raw.get("cards") or ())
            assert cards, "引擎记录流保持全信息：真实 card_move 必带牌面"
            visible = _card_move_visible_to(raw, human)
            source = raw["from_zone"]
            target = raw["to_zone"]
            if visible:
                assert [card["card_id"] for card in payload["cards"]] == [
                    card["card_id"] for card in cards
                ], f"公开 / 自己经手的移动被误脱敏：{payload}"
                assert payload["cards"][0]["name"] == cards[0]["name"]
                stats["public"] += 1
                public_zones_seen.update(
                    {zone for zone in (source["zone"], target["zone"]) if zone in LOG_PUBLIC_ZONES}
                )
            else:
                _assert_card_faces_absent(payload, cards)
                stats["redacted"] += 1
            if (
                source["zone"] == "deck"
                and target["zone"] == "hand"
                and target.get("seat") != human
            ):
                stats["other_draw"] += 1
                assert not visible, "其他座位从牌堆摸到的牌面泄露"
            if (
                source["zone"] == "deck"
                and target["zone"] == "hand"
                and target.get("seat") == human
            ):
                stats["own_draw"] += 1
                assert visible, "自己的摸牌应当可见"
            if (
                source["zone"] == "hand"
                and target["zone"] == "hand"
                and source.get("seat") != human
                and target.get("seat") != human
            ):
                stats["hidden_hand_to_hand"] += 1
                assert not visible, "他人之间的手牌移动泄露"
        if session.finished:
            break
        query = session.pending
        assert query is not None, "未终局却没有挂起询问"
        after = session.next_cursor(after)
        session.submit_answer(_human_action(query))

    assert stats["other_draw"] >= 1, f"这一局没有他人摸牌样本：{stats}"
    assert stats["own_draw"] >= 1, f"这一局没有自己摸牌样本：{stats}"
    assert stats["hidden_hand_to_hand"] >= 1, f"这一局没有隐藏手牌移动样本：{stats}"
    assert stats["redacted"] >= stats["other_draw"] + stats["hidden_hand_to_hand"]
    assert public_zones_seen == LOG_PUBLIC_ZONES, (
        f"公开区移动没覆盖全（弃牌 / 处理 / 装备 / 判定）：{sorted(public_zones_seen)}"
    )
    assert session.engine.records[-1].kind == "game_end"


def test_the_served_view_payload_filters_hidden_card_moves_for_the_human_seat() -> None:
    """真下发口（``GameRegistry.view``）必须把 ``viewer`` 传给 ``visible_record``。

    只测投影函数不够：``serve.py`` 的调用点漏传 ``session.human_seat`` 时，函数测试照样
    绿、HTTP 日志照样泄露。这里直接读服务端响应体里的 ``records``（与浏览器收到的同形）。
    """
    session = Session(
        seed=EIGHT_SEED,
        player_count=8,
        ai="heuristic",
        human_seat=0,
        timeout_seconds=None,
        pack=PACK,
    )
    while not session.finished:
        session.submit_answer(_human_action(session.pending))
    view = GameRegistry().view(session, after=-1, logs_after=0)
    assert view["human_seat"] == session.human_seat
    raw_moves = {
        record.seq: json.loads(record_json(record))
        for record in session.records()
        if getattr(record, "delta_type", None) == "card_move"
    }
    human = session.human_seat
    stats = {"other_draw": 0, "own_draw": 0, "public": 0, "hidden_hand_to_hand": 0}
    for payload in view["records"]:
        if payload.get("kind") != "delta" or payload.get("delta_type") != "card_move":
            continue
        raw = raw_moves[payload["seq"]]
        cards = tuple(raw.get("cards") or ())
        source = raw["from_zone"]
        target = raw["to_zone"]
        visible = _card_move_visible_to(raw, human)
        if not visible:
            _assert_card_faces_absent(payload, cards)
        else:
            assert [card["card_id"] for card in payload["cards"]] == [
                card["card_id"] for card in cards
            ]
        if source["zone"] == "deck" and target["zone"] == "hand" and target.get("seat") != human:
            stats["other_draw"] += 1
            assert not visible
        if source["zone"] == "deck" and target["zone"] == "hand" and target.get("seat") == human:
            stats["own_draw"] += 1
            assert visible
        if (
            source["zone"] == "hand"
            and target["zone"] == "hand"
            and source.get("seat") != human
            and target.get("seat") != human
        ):
            stats["hidden_hand_to_hand"] += 1
            assert not visible
        if (
            source["zone"] in LOG_PUBLIC_ZONES or target["zone"] in LOG_PUBLIC_ZONES
        ) and source["zone"] != "hero" and target["zone"] != "hero":
            stats["public"] += 1
            assert visible
    assert stats["other_draw"] >= 1, f"下发口没有他人摸牌样本：{stats}"
    assert stats["own_draw"] >= 1, f"下发口没有自己摸牌样本：{stats}"
    assert stats["hidden_hand_to_hand"] >= 1, f"下发口没有隐藏手牌移动样本：{stats}"
    assert stats["public"] >= 1, f"下发口没有公开区移动样本：{stats}"

    # 同一下发口的两条新面（M5 retro）：牌堆只给数量、身份只按 A3 可见。
    raw_deltas = {
        record.seq: json.loads(record_json(record))
        for record in session.records()
        if getattr(record, "kind", None) == "delta"
    }
    shuffles = [
        payload
        for payload in view["records"]
        if payload.get("kind") == "delta"
        and payload.get("delta_type") == "deck_shuffle"
    ]
    assert shuffles, "真一局必有洗牌记录（否则牌堆判据是空转）"
    for payload in shuffles:
        raw = raw_deltas[payload["seq"]]
        assert payload["cards"] == [], f"下发口仍带整副牌序：{payload}"
        assert payload["card_count"] == len(raw["cards"]), (
            f"洗牌必须给张数（引擎记录有 {len(raw['cards'])} 张）：{payload}"
        )
        assert payload["from_zone"] == raw["from_zone"]
        assert payload["to_zone"] == raw["to_zone"]
    deals = [
        payload
        for payload in view["records"]
        if payload.get("kind") == "delta"
        and payload.get("delta_type") == "identity_deal"
    ]
    assert len(deals) == session.rule_config.player_count, (
        f"身份发放应逐座一条：{len(deals)} 条"
    )
    for payload in deals:
        seat = payload["seat"]
        if seat == human or payload["identity"] == "lord":
            assert payload["identity"] is not None, f"本人 / 主公身份不该被裁：{payload}"
        else:
            assert payload["identity"] is None, f"下发口仍带他人身份：{payload}"


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


def test_the_fire_attack_reveal_is_public_in_the_log_records_but_the_other_hand_card_hidden() -> None:
    """M5-09c：``visible_record`` 的裁剪不得把 ``card_reveal`` 也藏掉。

    亮出的那一张是**公开事实**（08a 的中央舞台读它），目标的**另一张**手牌仍未亮出，
    既不出现在 ``card_reveal`` 里，也不得被任何下发记录带出来。
    """
    session, fire, discard, reveal, hidden = _fire_attack_session()
    viewer = session.human_seat
    query = session.pending
    assert query is not None and query.kind == QueryKind.PLAY_PHASE
    session.submit_answer(_fire_use(query, fire))
    for _ in range(20):
        query = session.pending
        if query is None or query.kind == QueryKind.SELECT_CARDS:
            break
        session.submit_answer(_human_action(query))
    assert query is not None and query.kind == QueryKind.SELECT_CARDS

    payloads = [visible_record(record, viewer=viewer) for record in session.records()]
    reveals = [
        payload
        for payload in payloads
        if payload is not None and payload.get("delta_type") == "card_reveal"
    ]
    assert len(reveals) == 1, "亮牌只落一条 card_reveal，且必须下发"
    shown = reveals[0]["card"]
    assert shown["card_id"] == reveal, "下发的是目标亮出的那一张"
    assert shown.get("display_name"), "亮牌仍按展示契约补好牌名（08a 中央舞台读它）"

    text = json.dumps(payloads, ensure_ascii=False)
    assert f'"card_id": {reveal}' in text, "公开亮出的牌必须仍在人类日志里"
    assert f'"card_id": {hidden}' not in text, "未亮出的手牌泄露到了人类日志记录里"
    for card_move in payloads:
        if card_move is not None and card_move.get("delta_type") == "card_move":
            assert card_move["cards"], (
                f"火攻这一刻只有公开移动；隐藏区移动被误下发：{card_move}"
            )


# ---------------------------------------------------------------------------
# 全部 delta 的**逐类**不泄露判据（M5 retro：09c 只查了 card_move）
# ---------------------------------------------------------------------------
#
# 09c 的教训不是"card_move 漏了"，而是"只查了一种 delta"。这里把 ``sgs.state.deltas``
# 的**全部** delta_type（M6 票 20 后 15 种；本票的翻面后 16 种）枚举出来，逐类构造一条带隐藏信息的合成
# 记录，断言人类查看者收到的下发只留公开事实：
#
# - ``deck_shuffle``：整副牌序 → 只给 ``card_count``；
# - ``identity_deal``：A3 可见性（本人 / 主公 / 已由 ``identity_reveal`` 亮明 / 阵亡）；
# - ``card_reveal``：**公开事实**，不得被顺手藏掉；
# - ``tag_add`` / ``tag_remove``：**标签类型**的可见性声明说了算（M6 三类全是
#   ``HIDDEN`` ⇒ 整条裁掉，牌面与标签类型都不下发）；
# - 其余（体力 / 横置 / 酒 / 牌移动 / 身份亮明）各按既有口径。
#
# 新增一种 delta 时，``test_every_delta_type_has_a_no_leak_case`` 会要求在这里登记判据。

VIEWER = 0
"""人类座位：所有"下发给人的载荷"判据都从这个查看者出发。"""

HIDDEN_CARD = CardRef(
    card_id=9001, type="peach", name="桃", suit="heart", rank=3, color="red"
)
"""一张只该以**数量**出现在下发里的隐藏牌（牌堆 / 他人手牌）。"""

HIDDEN_CARD_2 = CardRef(
    card_id=9002, type="slash", name="杀", suit="spade", rank=7, color="black"
)

REVEALED_CARD = CardRef(
    card_id=9003, type="dodge", name="闪", suit="diamond", rank=2, color="red"
)


def _delta_records() -> dict[str, object]:
    """每种 delta_type 一条**带隐藏信息**的合成记录（引擎记录模型，非手写 JSON）。"""
    return {
        "card_move": CardMoveRecord(
            seq=101,
            frame_id=0,
            depth=0,
            cards=(HIDDEN_CARD,),
            from_zone=ZoneRef(Zone.DECK),
            to_zone=ZoneRef(Zone.HAND, 1),
            reason=MoveReason.DRAW,
            owner=1,
        ),
        "hp_change": HpChangeRecord(
            seq=102, frame_id=0, depth=0, seat=1, before=4, after=3, amount=1
        ),
        # M6 票 06 的体力上限变化（觉醒技）：与 hp_change 同档的公开事实。
        "hp_max_change": HpMaxChangeRecord(
            seq=112, frame_id=0, depth=0, seat=1, before=4, after=3, amount=1
        ),
        "chained_change": ChainedChangeRecord(
            seq=103, frame_id=0, depth=0, seat=1, chained=True
        ),
        # 翻面是本票的座位状态（与横置并列）：载荷只有座位 + 新值，没有隐藏面。
        "flipped_change": FlippedChangeRecord(
            seq=116, frame_id=0, depth=0, seat=1, flipped=True
        ),
        "wine_change": WineChangeRecord(
            seq=104, frame_id=0, depth=0, seat=1, active=True
        ),
        # 票 06：标记数量（公开信息；名称 / 来源 / 数量都在明面上，没有隐藏面）。
        "marker_change": MarkerChangeRecord(
            seq=117,
            frame_id=0,
            depth=0,
            seat=1,
            name="梦魇",
            source_seat=0,
            source_skill_id="hero.shen_guanyu_mobile.wuhun",
            count=2,
        ),
        "deck_shuffle": DeckShuffleRecord(
            seq=105,
            frame_id=0,
            depth=0,
            cards=(HIDDEN_CARD, HIDDEN_CARD_2),
            from_zone=ZoneRef(Zone.DISCARD),
            to_zone=ZoneRef(Zone.DECK),
        ),
        "card_reveal": CardRevealRecord(
            seq=106, frame_id=0, depth=0, seat=1, card=REVEALED_CARD
        ),
        # huo-taishici-xunyu 票 03 的拼点揭晓：**公开事实**（揭晓后牌面即公开），
        # 两张牌的面都必须照发——被顺手藏掉反而是错。
        "pindian": PindianRecord(
            seq=118,
            frame_id=0,
            depth=0,
            title="天义",
            initiator=0,
            targets=(1,),
            cards=(REVEALED_CARD, HIDDEN_CARD),
            won=(True,),
            tied=(False,),
        ),
        "identity_deal": IdentityDealRecord(
            seq=107, frame_id=0, depth=0, seat=1, identity=Identity.REBEL
        ),
        "identity_reveal": IdentityRevealRecord(
            seq=108, frame_id=0, depth=0, seat=1, identity=Identity.REBEL
        ),
        # M6 票 04 的技能三态：载荷只有座位 + 技能 id（武将牌明置，技能是公开事实），
        # 没有任何隐藏牌面 / 身份 ⇒ 下发面 = 原样。
        "skill_gain": SkillGainRecord(
            seq=109,
            frame_id=0,
            depth=0,
            seat=1,
            skill_id="hero.machao.mashu",
        ),
        "skill_lose": SkillLoseRecord(
            seq=110,
            frame_id=0,
            depth=0,
            seat=1,
            skill_id="hero.machao.mashu",
        ),
        "skill_suppress": SkillSuppressRecord(
            seq=111,
            frame_id=0,
            depth=0,
            seat=1,
            skill_id="hero.jie_guanyu.yijue",
            suppressed=True,
        ),
        # M6 票 11 的回合边界（额外回合 / 轮次变化）：回合座位与轮次都是公开事实
        # （顶栏本来就画轮次），没有隐藏面 ⇒ 下发面 = 原样。
        "turn_change": TurnChangeRecord(
            seq=113, frame_id=0, depth=0, seat=1, round=2, extra=True
        ),
        # M6 票 20 的标签增删（spec §投影与记录流）：标签挂在**手牌**上，故牌的实体身份
        # 与标签类型都属于隐藏面——隐藏标签的下发面 = 整条裁掉（M6 登记的三类全是 HIDDEN）。
        "tag_add": TagAddRecord(
            seq=114,
            frame_id=0,
            depth=0,
            card=HIDDEN_CARD,
            tag_type="luoshen_hand",
            owner=1,
            skill_id="hero.jie_zhenji.luoshen",
        ),
        "tag_remove": TagRemoveRecord(
            seq=115,
            frame_id=0,
            depth=0,
            card=HIDDEN_CARD,
            tag_type="luoshen_hand",
            owner=1,
            skill_id="hero.jie_zhenji.luoshen",
        ),
    }


def _delta_schema_types() -> dict[str, type]:
    """``sgs.state.deltas`` 里**全部** delta 模型：``delta_type`` → 类（新增类会进这里）。"""
    import sgs.state.deltas as schema
    from pydantic import BaseModel

    out: dict[str, type] = {}
    for name in schema.__all__:
        cls = getattr(schema, name)
        if name == "Delta" or not (isinstance(cls, type) and issubclass(cls, BaseModel)):
            continue
        out[cls.model_fields["delta_type"].default] = cls
    return out


def _assert_identity_hidden(payload: dict, identity_value: str) -> None:
    """身份不可见：字段置空，且下发字节里没有那个身份枚举值。"""
    assert payload.get("identity") is None, f"隐藏身份未脱敏：{payload}"
    text = json.dumps(payload, ensure_ascii=False)
    assert f'"{identity_value}"' not in text, f"脱敏记录仍带身份值 {identity_value}：{payload}"


def test_every_delta_type_has_a_no_leak_case() -> None:
    """delta 类型表与判据表**双向**对齐：新增类型必须登记一条不泄露判据。"""
    schema_types = _delta_schema_types()
    cases = _delta_records()
    assert len(schema_types) == 18, (
        f"``sgs.state.deltas`` 现有 {len(schema_types)} 种 delta（判据按 18 种写）："
        f"{sorted(schema_types)}"
    )
    missing = sorted(set(schema_types) - set(cases))
    stale = sorted(set(cases) - set(schema_types))
    assert not missing and not stale, (
        "delta 类型与不泄露判据表不同步：\n"
        f"  未登记判据：{missing}\n"
        f"  判据表多出（schema 已删）：{stale}\n"
        "新增一种 delta 时，在 _delta_records() 里补一条带隐藏信息的合成记录，"
        "并在 test_every_delta_record_is_redacted_for_a_human_viewer 里写明它的公开面。"
    )


def test_the_delta_coverage_check_bites() -> None:
    """探针：从判据表里拿掉一种 delta，覆盖检查必须报出它（防"11 种"断言空转）。"""
    cases = _delta_records()
    cases.pop("deck_shuffle")
    missing = sorted(set(_delta_schema_types()) - set(cases))
    assert missing == ["deck_shuffle"], missing


def test_every_delta_record_is_redacted_for_a_human_viewer() -> None:
    """逐类检查：人类查看者的下发里没有隐藏牌面 / 整副牌序 / 他人身份。"""
    for delta_type, record in _delta_records().items():
        raw = json.loads(record_json(record))
        payload = visible_record(record, viewer=VIEWER)
        if delta_type in ("tag_add", "tag_remove"):
            # 隐藏标签（M6 登记表三类全是 HIDDEN）：整条裁掉，牌面 / 标签类型都不下发。
            assert payload is None, f"隐藏标签的增删泄露到了人类载荷：{payload}"
            assert raw["card"]["card_id"] == HIDDEN_CARD.card_id, "夹具必须带隐藏牌面"
            continue
        assert payload is not None, f"{delta_type} 被整条丢弃（不是裁剪）"
        assert payload["seq"] == raw["seq"]
        if delta_type == "deck_shuffle":
            assert raw["cards"], "夹具必须带牌序，否则判据是空转"
            _assert_card_faces_absent(payload, tuple(raw["cards"]))
            assert payload["from_zone"] == raw["from_zone"]
            assert payload["to_zone"] == raw["to_zone"]
        elif delta_type == "identity_deal":
            assert payload["seat"] == 1
            _assert_identity_hidden(payload, "rebel")
        elif delta_type == "card_reveal":
            # 公开事实：亮出的那一张照发，且按既有展示契约补好牌名。
            assert payload["card"]["card_id"] == raw["card"]["card_id"]
            assert payload["card"].get("display_name")
        elif delta_type == "pindian":
            # 拼点揭晓是公开事实：两张牌的面与结果都照发。
            assert [card["card_id"] for card in payload["cards"]] == [
                card["card_id"] for card in raw["cards"]
            ]
            assert payload["won"] == [True]
            assert payload["tied"] == [False], "平点位也是公开事实，照发"
        elif delta_type == "identity_reveal":
            # 阵亡亮明身份是公开事实（A3 的 revealed / dead 那一支）。
            assert payload["identity"] == "rebel"
        elif delta_type == "card_move":
            _assert_card_faces_absent(payload, tuple(raw["cards"]))
        elif delta_type in (
            "skill_gain",
            "skill_lose",
            "skill_suppress",
            "hp_max_change",
            "turn_change",
        ):
            # 技能三态（M6 票 04）/ 体力上限变化（M6 票 06）/ 回合边界（M6 票 11）：
            # 座位 + 技能 id / 数值 / 轮次都是公开事实（体力上限与轮次本来就在投影 / 顶栏里），
            # 没有可裁的隐藏面。
            assert payload == raw, f"{delta_type} 是公开事实，不该被裁剪：{payload}"
            assert payload["seat"] == 1
        else:
            assert payload == raw, f"{delta_type} 是公开事实，不该被裁剪：{payload}"
        if delta_type in ("deck_shuffle", "identity_deal"):
            # 全知通道（内部调用 / 测试）不裁剪——裁剪只发生在这一个下发口。
            assert visible_record(record, viewer=None) == raw


def test_identity_deal_visibility_follows_a3() -> None:
    """A3：身份按「本人 / 主公 / 已亮明 / 阵亡」可见；其余暗置。

    ``identity_deal`` 是**开局暗发**那一条，故只放行本人与主公；"已亮明 / 阵亡"由后一条
    公开的 ``identity_reveal`` 承载（同一份记录流里下发，不需要读者回读引擎状态）。
    """

    def deal(seat: int, identity: Identity) -> object:
        return IdentityDealRecord(
            seq=200 + seat, frame_id=0, depth=0, seat=seat, identity=identity
        )

    own = visible_record(deal(VIEWER, Identity.LOYALIST), viewer=VIEWER)
    assert own["identity"] == "loyalist", "本人身份必须可见"
    lord = visible_record(deal(2, Identity.LORD), viewer=VIEWER)
    assert lord["identity"] == "lord", "主公身份公开"
    other = visible_record(deal(1, Identity.REBEL), viewer=VIEWER)
    _assert_identity_hidden(other, "rebel")
    as_self = visible_record(deal(3, Identity.RENEGADE), viewer=3)
    assert as_self["identity"] == "renegade", "本人看自己的发放必须可见"

    reveal = IdentityRevealRecord(
        seq=210, frame_id=0, depth=0, seat=3, identity=Identity.REBEL
    )
    assert visible_record(reveal, viewer=VIEWER)["identity"] == "rebel", (
        "阵亡亮明身份必须公开（A3 的 revealed / dead）"
    )


def test_the_no_leak_assertions_bite_on_the_unredacted_payloads() -> None:
    """合成反例：把**未脱敏**的原始记录喂给判据，两条都必须抛 AssertionError。"""
    cases = _delta_records()
    raw_shuffle = json.loads(record_json(cases["deck_shuffle"]))
    with pytest.raises(AssertionError):
        _assert_card_faces_absent(raw_shuffle, tuple(raw_shuffle["cards"]))

    raw_deal = json.loads(record_json(cases["identity_deal"]))
    with pytest.raises(AssertionError):
        _assert_identity_hidden(raw_deal, "rebel")


def test_a_card_move_that_lands_on_the_deck_top_still_hides_its_faces() -> None:
    """票 02 的新记录形状（``to_top``）**不是**第二条泄露通道。

    落点只加在既有的 ``card_move`` 上（不新增 delta 类型），故牌面可见性判据仍然只有
    一条：手牌 → 牌堆的移动对旁观者只给张数。这条用例钉住两件事——``to_top`` 在人类
    载荷里**保留**（界面上"置于牌堆顶"是可读的公开事实），而 ``cards`` 照旧被抹空。
    """
    landed = CardMoveRecord(
        seq=118,
        frame_id=0,
        depth=0,
        cards=(HIDDEN_CARD,),
        from_zone=ZoneRef(Zone.HAND, 1),
        to_zone=ZoneRef(Zone.DECK),
        reason=MoveReason.PUT,
        owner=1,
        to_top=True,
    )
    raw = json.loads(record_json(landed))
    assert raw["to_top"] is True, "落点必须落在记录里（自描述）"

    payload = visible_record(landed, viewer=VIEWER)
    _assert_card_faces_absent(payload, tuple(raw["cards"]))
    assert payload["to_top"] is True, "落点不是隐藏面：人类载荷照发"
    # 全知通道（内部 / 回放）不裁剪牌面——裁剪只发生在这一个下发口。
    assert visible_record(landed, viewer=None)["cards"], "全知通道照发牌面"


# ---------------------------------------------------------------------------
# M6 票 20：**标签增删**与**武将牌上的牌区**两条新面
#
# 标签：M6 登记的三类全声明为 ``HIDDEN`` ⇒ 增删 delta **整条**裁掉（牌面与标签类型都不
# 下发），且不放宽公开的「手牌数」。区域：整个区给不给看由放置技能的**数据声明**回答
# （``PlacementDecl.visibility``），不是"HERO 一律隐藏"——数据里【谦逊】是公开区。
# ---------------------------------------------------------------------------

JIE_LUOSHEN = "hero.jie_zhenji.luoshen"
LUOSHEN_REPEAT_PREFIX = "luoshen:repeat:"


class _LuoshenSeat:
    """座位 0 的脚本：发动一次【洛神】、不重复、出牌阶段直接结束、其余放弃。"""

    def respond(self, query):  # noqa: ANN001 - Participant 协议
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LUOSHEN:
            for action in query.legal_actions:
                if getattr(action, "kind", None) == "ActivateSkill":
                    return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id).startswith(
            LUOSHEN_REPEAT_PREFIX
        ):
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.PLAY_PHASE:
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def test_hidden_card_tags_never_reach_the_human_record_stream() -> None:
    """真一局界甄姬【洛神】：标签增删进引擎记录流，人类下发面**整条都没有**。

    判据同时钉住：① 夹具真的挂过标签（否则"下发面没有"是空转）；② 引擎记录流保留全信息
    （牌面 + 标签类型）；③ 人类查看者（含**本人**）收到的记录里一条标签 delta 都没有；
    ④ 公开的「手牌数」不受标签影响（仍是 ``len(hand)``）。
    """
    from types import SimpleNamespace

    from sgs.data import Suit
    from sgs.engine import Engine

    from support import PassPlayer, run_until

    pack = load_content_pack(deck_profile="standard_108")
    black = next(
        index for index, card in enumerate(pack.deck.cards) if card.suit is Suit.SPADE
    )
    hand = (next(index for index, card in enumerate(pack.deck.cards) if card.type == "peach"),)
    fillers = [index for index in range(len(pack.deck.cards)) if index not in {black, *hand}]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero="jie_zhenji", hp=3, hp_max=3, hand=hand),
            SeatSpec(),
        ],
        deck=(black, *fillers),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )
    engine = Engine.from_state(state, seed=7)
    players = [_LuoshenSeat(), PassPlayer()]
    run_until(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.PLAY_PHASE
        and running.pending.actor == 0,
    )
    query = engine.pending
    engine.submit(next(a for a in query.legal_actions if isinstance(a, EndPhase)))
    run_until(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == QueryKind.PLAY_PHASE
        and running.pending.actor == 1,
    )

    records = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) in ("tag_add", "tag_remove")
    ]
    assert {record.delta_type for record in records} == {"tag_add", "tag_remove"}, (
        f"这一局必须真的挂过又清过标签（否则判据空转）：{records}"
    )
    raw = json.loads(record_json(records[0]))
    assert "card" in raw and "tag_type" in raw, "引擎记录流保持全信息"

    for record in engine.records:
        if record.kind == "delta" and record.delta_type in ("tag_add", "tag_remove"):
            for viewer in (0, 1, None):
                payload = visible_record(record, viewer=viewer)
                if viewer is None:
                    assert payload is not None and payload["tag_type"] == "luoshen_hand", (
                        "全知通道不裁剪"
                    )
                else:
                    assert payload is None, (
                        f"隐藏标签的下发面必须整条裁掉（viewer={viewer}）：{payload}"
                    )
    text = json.dumps(
        [visible_record(record, viewer=0) for record in engine.records],
        ensure_ascii=False,
    )
    for key in ('"tag_add"', '"tag_remove"', '"tag_type"', '"luoshen_hand"'):
        assert key not in text, f"人类记录流里仍有标签字段 {key}"

    snapshot = visible_snapshot(
        SimpleNamespace(engine=engine, human_seat=0, pack=pack)
    )
    seat_zero = snapshot["players"][0]
    assert seat_zero["hand_count"] == len(state.player(0).hand), (
        "标签不改变公开的「手牌数」：它是 len(hand)，不是手牌计数"
    )


_QIANXUN_ZONE_KEY = "1:hero.jie_luxun.qianxun"
"""【谦逊】的放置区键 = ``{owner}:{skill_id}``（owner = 放置技能实例的持有者）。"""


def _hero_zone_move(from_zone: ZoneRef, to_zone: ZoneRef) -> CardMoveRecord:
    return CardMoveRecord(
        seq=301,
        frame_id=0,
        depth=0,
        cards=(HIDDEN_CARD,),
        from_zone=from_zone,
        to_zone=to_zone,
        reason=MoveReason.PUT,
        owner=1,
    )


def test_a_hero_card_zone_move_is_host_only() -> None:
    """HERO 区的牌面可见性 = **只给宿主**（2026-10 用户口径，不看旧 visibility 声明）。

    宿主 = ``ZoneRef.seat``（武将牌的主人）；非宿主（含放置技能的 owner）只拿张数；
    ``viewer=None``（全知 / 回放）不裁剪。未登记的区键同一默认：宿主可见、外人脱敏。
    """
    return_move = _hero_zone_move(
        ZoneRef(Zone.HERO, 1, _QIANXUN_ZONE_KEY), ZoneRef(Zone.HAND, 1)
    )
    put_move = _hero_zone_move(
        ZoneRef(Zone.HAND, 1), ZoneRef(Zone.HERO, 1, _QIANXUN_ZONE_KEY)
    )

    for move in (return_move, put_move):
        foreign = visible_record(move, viewer=0)
        _assert_card_faces_absent(foreign, (HIDDEN_CARD,))
        allowed = visible_record(move, viewer=1)
        assert allowed["cards"] and allowed["cards"][0]["card_id"] == HIDDEN_CARD.card_id, (
            "宿主本人（ZoneRef.seat）看得见 HERO 区的牌"
        )
        assert visible_record(move, viewer=None)["cards"], "全知通道不裁剪"

    # 未登记的区键（不是放置类技能）同一默认：宿主可见、外人脱敏。
    unregistered = _hero_zone_move(
        ZoneRef(Zone.HERO, 1, "1:hero.jie_luxun.lianying"), ZoneRef(Zone.HAND, 1)
    )
    _assert_card_faces_absent(visible_record(unregistered, viewer=0), (HIDDEN_CARD,))
    assert visible_record(unregistered, viewer=1)["cards"], (
        "未登记区的宿主仍看得见（不是整块 fail-closed）"
    )
