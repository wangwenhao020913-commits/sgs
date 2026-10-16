"""M4 接入层的纯函数与机制单测（票 01 起；不起服务、不连网、**不 import threading**）。

本文件刻意不住在 ``src/sgs/`` 的测试域里——它测的是 ``tools/sgs_web/``。
``sys.path`` 引导只做一次（见 :func:`_bootstrap`），让 ``sgs`` 与 ``sgs_web`` 都可导入。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    """把 ``tools/`` 与 ``src/`` 放进 ``sys.path``（幂等）。"""
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.agents import RandomPlayer, TapePlayer  # noqa: E402
from sgs.data import validate_rule_config  # noqa: E402
from sgs.engine import Engine, RandomSource, dump_journal  # noqa: E402
from sgs.resolution import Frame, ResolutionStack  # noqa: E402
from sgs.state import (  # noqa: E402
    Action,
    ArrangeQuery,
    FrameKind,
    PeachQuery,
    RespondQuery,
    SelectCardsQuery,
    SelectionSpec,
    SelectOptionQuery,
    Slot,
    WindowCause,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    build_skills,
    candidate_index,
    option_seats,
    pending_payload,
    query_context,
    visible_record,
    visible_snapshot,
)
from sgs_web.replay import list_runs, load_run, replay_journal, save_run  # noqa: E402
from sgs_web.session import Session, StaleAnswer  # noqa: E402
from sgs_web.timeout import fallback_action  # noqa: E402
from support.positions import SeatSpec, build_state, cards_of_type  # noqa: E402

TWO_PLAYERS = {"player_count": 2}
"""2 人局：无身份 1v1，跑得快，且足以覆盖"挂起 → 应答 → 续跑"的全部分支。"""


class _FirstChoice:
    """每次都取第一个候选——确定性的脚本参与者（**真正的参与者协议**，不是替身）。"""

    def respond(self, query) -> Action:  # noqa: ANN001 - 与 Participant 协议一致
        return query.legal_actions[0]


def _play_to_end(session: Session) -> int:
    """用 :class:`Session` 的挂起驱动把一局走完，返回应答次数。"""
    answers = 0
    while session.pending is not None:
        session.submit_answer(session.pending.legal_actions[0])
        answers += 1
    return answers


# ---------------------------------------------------------------------------
# 1. 挂起驱动：可跑完、序号不重复、与"不挂起的一次性跑法"逐字节相同
# ---------------------------------------------------------------------------


def test_the_session_drives_a_whole_game_to_the_end() -> None:
    session = Session(seed=7, ai="random", timeout_seconds=None, **TWO_PLAYERS)

    assert session.pending is not None, "建局后应当立刻停在一个询问上（挂起驱动）"
    assert not session.finished

    answers = _play_to_end(session)

    assert session.finished, "挂起驱动必须能把一局走完"
    assert answers > 0
    assert session.pending is None


def test_no_query_is_ever_emitted_twice_across_suspend_and_resume() -> None:
    """挂起/续跑是**重新进入** ``run``；询问因此不得被重发（否则记录流会漂）。"""
    session = Session(seed=11, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    _play_to_end(session)

    queries = Counter(
        record.query.seq for record in session.records() if getattr(record, "kind", None) == "query"
    )
    duplicated = [seq for seq, count in queries.items() if count > 1]

    assert duplicated == [], f"这些询问序号被发了不止一次：{duplicated}"


@pytest.mark.parametrize("seed", [3, 101, 4096])
def test_the_halt_driven_journal_is_byte_identical_to_a_one_shot_run(seed: int) -> None:
    """M4 的机制判据：**挂起驱动不改变记录流**。

    对照跑法必须与 :class:`Session` **同配置**：AI 座位同样用 ``RandomPlayer``
    （它会消耗 ``ai`` 子流），人类座位用"每次都取第一个候选"的**真参与者**替代替身——
    唯一允许的差异是"人类座位是不是抛跳出信号"，其余一律相同。
    用不消耗随机数的脚本参与者当对照是**假对照**：那样两边连随机序列都不同。
    """
    session = Session(seed=seed, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    _play_to_end(session)
    halt_driven = dump_journal(session.records())

    straight = Engine.new_game(
        seed=seed,
        rule_config=validate_rule_config(TWO_PLAYERS),
        pack=session.pack,
        heroes=session.heroes,
        hero_pool=session.hero_pool,
    )
    ai_stream = RandomSource(seed).substream("ai")
    straight.run([_FirstChoice(), RandomPlayer(ai_stream)])

    assert halt_driven == dump_journal(straight.records)


def test_the_halt_driven_journal_replays_through_the_tape_player() -> None:
    """挂起驱动的产出必须仍是**合法回放输入**（``tape_from_journal`` + ``TapePlayer``）。"""
    from sgs.agents import tape_from_journal

    session = Session(seed=101, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    _play_to_end(session)
    seed, tape = tape_from_journal(session.records())

    replay = Engine.new_game(
        seed=seed,
        rule_config=session.rule_config,
        pack=session.pack,
        heroes=session.heroes,
        hero_pool=session.hero_pool,
    )
    replay.run([TapePlayer(tape)] * session.rule_config.player_count)

    assert dump_journal(replay.records) == dump_journal(session.records())


# ---------------------------------------------------------------------------
# 2. 过期应答：不写记录、不改状态（地图 D15）
# ---------------------------------------------------------------------------


def test_a_stale_answer_is_refused_without_touching_the_journal() -> None:
    session = Session(seed=5, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    pending = session.pending
    assert pending is not None
    before = dump_journal(session.records())

    stale = pending.legal_actions[0]
    session.submit_answer(stale)  # 先正常答一次，让那个 seq 过期
    after_first = dump_journal(session.records())
    assert after_first != before

    with pytest.raises(StaleAnswer):
        session.submit_answer(stale)  # 再把同一个应答重放一次

    assert dump_journal(session.records()) == after_first, "过期应答不得写记录、不得改状态"


def test_answering_when_nothing_is_pending_is_refused() -> None:
    session = Session(seed=13, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    stale = session.pending.legal_actions[0]  # type: ignore[union-attr]
    _play_to_end(session)

    with pytest.raises(StaleAnswer):
        session.submit_answer(stale)


# ---------------------------------------------------------------------------
# 3. 会话的只读面：游标、逐座位武将、侧车日志
# ---------------------------------------------------------------------------


def test_the_cursor_advances_to_the_last_sequence_number() -> None:
    session = Session(seed=17, ai="random", timeout_seconds=None, **TWO_PLAYERS)

    assert session.next_cursor(-1) >= 1
    assert session.records_after(-1)[0].kind == "header"

    cursor = session.records()[-1].seq
    assert session.records_after(cursor) == ()
    assert session.next_cursor(0) == cursor


def test_hero_assignments_are_read_from_the_engine_state() -> None:
    """武将不在记录流里（不是牌、没有 delta），故侧车数据的真源只能是引擎状态。"""
    session = Session(
        seed=23, ai="random", timeout_seconds=None, hero_pool=None, **TWO_PLAYERS
    )

    assert session.hero_assignments == (None,) * 2, "hero_pool=None 是无武将模式"


def test_the_hero_pool_is_dealt_from_the_enabled_pool_by_default() -> None:
    session = Session(
        seed=29, ai="random", timeout_seconds=None, player_count=5, hero_pool=()
    )

    assigned = session.hero_assignments
    assert all(hero is not None for hero in assigned), "hero_pool=() 应当从启用池发放武将"
    assert len(set(assigned)) == len(assigned), "发放不重复"


def test_the_session_log_stays_out_of_the_journal() -> None:
    """侧车日志是接入层事实：写它**不得**动记录流一个字节（地图 D10）。"""
    session = Session(seed=31, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    before = dump_journal(session.records())

    session.log(1234.5, "connect", seat=session.human_seat)

    assert dump_journal(session.records()) == before
    assert session.session_log[0].as_dict() == {
        "at": 1234.5,
        "kind": "connect",
        "detail": {"seat": 0},
    }


# ---------------------------------------------------------------------------
# 4. 超时兜底：纯函数（票 02；地图 D9）
# ---------------------------------------------------------------------------


def test_fallback_action_is_pure_and_gives_the_first_candidate_when_nothing_else() -> None:
    """同一个询问调两次结果相同；没有 ``Pass`` / ``EndPhase`` 时取候选里的第一个。"""
    session = Session(seed=41, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    pending = session.pending
    assert pending is not None

    first = fallback_action(pending)
    second = fallback_action(pending)

    assert first == second, "兜底必须是纯函数：同一询问两次调用结果相同"
    assert first.answers_seq == pending.seq


def test_fallback_action_prefers_end_phase_in_the_play_phase() -> None:
    """出牌阶段有 ``EndPhase`` 时，兜底就是"结束阶段"而不是"用第一张牌"。"""
    from sgs.state import EndPhase

    session = Session(seed=43, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    pending = session.pending
    assert pending is not None
    assert pending.kind == "PLAY_PHASE"
    assert any(action.kind == "EndPhase" for action in pending.legal_actions)

    assert isinstance(fallback_action(pending), EndPhase)


def test_fallback_action_prefers_pass_when_the_query_offers_it() -> None:
    """响应类询问带 ``Pass`` 时，兜底就是"不响应"。"""
    from sgs.state import Pass

    session = Session(seed=47, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    for _ in range(300):
        pending = session.pending
        if pending is None:
            break
        if any(action.kind == "Pass" for action in pending.legal_actions):
            assert isinstance(fallback_action(pending), Pass)
            return
        session.submit_answer(pending.legal_actions[0])
    pytest.skip("本局没走到带 Pass 的询问（不变量由构造局面单测覆盖）")


def test_fallback_action_answers_the_target_of_a_fanjian_step() -> None:
    """票 06：反间的步 2/3 由**目标**应答（不是发动者）。超时兜底要在"等目标作答"时给出
    一个**合法**代答 —— 带 ``Pass`` 的选牌步落到"取消这一步"，而不是发出非法动作。"""
    from sgs.effects import legal_actions_for
    from sgs.state import Pass, QueryKind, SelectContext

    pack = _context_pack()
    hearts = cards_of_type(pack, "heart")[:2]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=tuple(hearts), hero="zhouyu"), SeatSpec()],
        turn_seat=0,
    )
    # 步 3 的形状：**目标**（座位 1）应答，选择面铺的是**发动者**（座位 0）的手牌。
    slots = tuple(
        Slot(ref=ZoneRef(Zone.HAND, 0), card=state.card(card_id)) for card_id in hearts
    )
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True)
    actions = legal_actions_for(
        state,
        1,
        QueryKind.SELECT_CARDS,
        answers_seq=1,
        payload=SelectContext(spec=spec, slots=slots, skill_id="hero.zhouyu.fanjian"),
    )
    query = SelectCardsQuery(
        seq=1,
        actor=1,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="请从选择面里选择一张牌。",
        legal_actions=actions,
        view=project(state, 1),
        spec=spec,
        slots=slots,
        skill_id="hero.zhouyu.fanjian",
    )

    assert any(isinstance(action, Pass) for action in actions), "这一步可取消（流水线语义）"
    assert isinstance(fallback_action(query), Pass), "超时代答 = 取消这一步，不是非法动作"


def test_a_timeout_answers_the_target_of_fanjian_in_a_real_session() -> None:
    """票 06：反间的步 2/3 由**目标**应答。真实会话里（AI 周瑜反间人类座位）超时兜底要能
    在"等目标作答"时替**目标**代答一次，且代答之后该局仍能跑完。"""
    from sgs.state import Pass

    session = Session(
        seed=0,
        player_count=2,
        ai="random",
        human_seat=1,
        heroes=("zhouyu", "guanyu"),
        timeout_seconds=1.0,
    )
    for _ in range(400):
        pending = session.pending
        if pending is None:
            break
        if pending.kind == "SELECT_OPTION" and str(pending.skill_id).startswith("fanjian:"):
            break
        session.submit_answer(fallback_action(pending))

    pending = session.pending
    assert pending is not None and pending.skill_id == "fanjian:suit", (
        "扫到了 AI 发动的【反间】的第二步"
    )
    assert pending.actor == session.human_seat, "这一步问的正是**目标**（人类座位）"
    assert isinstance(fallback_action(pending), Pass)

    session.mark_pending(100.0)
    assert session.expire_if_due(101.0, fallback_action) is True, "到点替目标代答"
    assert session.timeouts == 1
    assert session.session_log[-1].kind == "timeout"
    assert session.session_log[-1].detail["query_kind"] == "SELECT_OPTION"

    for _ in range(400):
        if session.pending is None:
            break
        session.submit_answer(fallback_action(session.pending))

    assert session.pending is None, "被代答之后该局仍能跑完"


def test_a_timeout_touches_the_session_log_but_not_the_journal() -> None:
    """超时只落侧车日志；**记录流一个字节都不加**（地图 D10）。

    对照跑法必须**答同样的动作**（都用 :func:`fallback_action`），唯一的差异是"谁触发的"：
    一边走超时判定、一边直接提交。这样"超时机制本身不写记录"才是被断言的事实。
    """
    normal = Session(seed=53, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    while normal.pending is not None:
        normal.submit_answer(fallback_action(normal.pending))

    timed = Session(seed=53, ai="random", timeout_seconds=1.0, **TWO_PLAYERS)
    timed.mark_pending(1000.0)
    assert timed.expire_if_due(1000.5, fallback_action) is False, "还没到时限，不该代答"
    assert timed.expire_if_due(1001.5, fallback_action) is True, "到时限必须代答"
    assert timed.timeouts == 1
    assert timed.session_log[-1].kind == "timeout"
    while timed.pending is not None:
        timed.mark_pending(2000.0)
        if not timed.expire_if_due(3000.0, fallback_action):
            timed.submit_answer(fallback_action(timed.pending))

    assert dump_journal(timed.records()) == dump_journal(normal.records()), (
        "超时代答改变的只是『谁触发应答』，不得改变记录流"
    )
    assert timed.timeouts >= 1


def test_mark_pending_does_not_restart_the_countdown() -> None:
    """每次轮询都重置倒计时的话，超时永远不会到点。"""
    session = Session(seed=59, ai="random", timeout_seconds=10.0, **TWO_PLAYERS)
    session.mark_pending(100.0)
    session.mark_pending(105.0)

    assert session.pending_since == 100.0


def test_a_new_question_gets_a_fresh_deadline() -> None:
    """**报障 1**：倒计时必须挂在**当前**询问上，换了询问就得重新起算。

    否则第一次询问的 ``pending_since`` 会一直沿用到后面的每一次询问：人类思考超过
    时限后按下提交，``answer()`` 里随后的 ``advance_clock`` 会拿旧戳去比新询问，
    把刚产生的询问当场代答——出牌阶段里的表现就是「点了提交，出牌阶段直接结束」。
    """
    session = Session(seed=53, ai="random", timeout_seconds=10.0, **TWO_PLAYERS)
    session.mark_pending(100.0)
    first = session.pending
    session.submit_answer(fallback_action(first))
    assert session.pending is not None
    assert session.pending.seq != first.seq, "提交后引擎推进到了新的询问"

    # 人类思考 11 秒后才提交：answer() 会先 submit_answer、再 advance_clock。
    session.mark_pending(111.0)

    assert session.pending_since == 111.0, "新询问的倒计时从它出现的那一刻起算"
    assert session.expire_if_due(111.0, fallback_action) is False, "刚出现的询问不该被立刻代答"
    assert session.timeouts == 0


# ---------------------------------------------------------------------------
# 5. 可见面：不泄露（票 03；判据 2）
# ---------------------------------------------------------------------------


def test_the_snapshot_never_exposes_other_hands_or_hidden_identity() -> None:
    session = Session(seed=61, ai="random", timeout_seconds=None, player_count=5)
    snapshot = visible_snapshot(session)

    assert snapshot["seat"] == session.human_seat
    assert "self_hand" in snapshot and snapshot["self_hand"], "自己的手牌必须可见"
    lord = next((p["seat"] for p in snapshot["players"] if p["identity"] == "lord"), None)
    for player in snapshot["players"]:
        if player["seat"] == session.human_seat:
            continue
        assert "self_hand" not in player
        if player["alive"] and player["seat"] != lord:
            assert player["identity"] is None, f"存活非主公的身份不得可见：{player}"
    assert isinstance(snapshot["deck_count"], int)
    assert "deck" not in snapshot, "牌堆只能给数量，不能给顺序"


def test_the_human_seat_learns_its_own_identity() -> None:
    session = Session(seed=67, ai="random", timeout_seconds=None, player_count=5)
    snapshot = visible_snapshot(session)
    me = next(p for p in snapshot["players"] if p["seat"] == session.human_seat)

    assert me["identity"] is not None, "自己的身份必须可见"


def test_non_human_queries_are_redacted_in_the_wire_form() -> None:
    """下发的 ``query`` 记录**不带**视角与候选：别人的视角是泄露面（地图 D8）。"""
    session = Session(seed=71, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    payloads = [visible_record(r) for r in session.records()]

    queries = [p for p in payloads if p is not None and p["kind"] == "query"]
    assert queries, "记录流里应当有询问"
    for payload in queries:
        assert "view" not in payload["query"], "询问记录不得带视角"
        assert "legal_actions" not in payload["query"], "询问记录不得带候选"


def test_the_pending_query_carries_the_candidates_for_the_human_seat() -> None:
    """人类座位需要的那一份候选单独给（前端只 pick，不自己拼 Action）。"""
    session = Session(seed=73, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    payload = pending_payload(session)

    assert payload is not None
    assert payload["actor"] == session.human_seat
    assert payload["legal_actions"], "挂起的那一条必须带候选"
    assert all(a["answers_seq"] == payload["seq"] for a in payload["legal_actions"])


# ---------------------------------------------------------------------------
# 5b. 候选索引：与合法行动集合**同源**（票 02 的四不变量 ①②）
# ---------------------------------------------------------------------------


def test_the_respond_query_carries_no_second_target_stage() -> None:
    """``RESPOND`` 询问体**不带** ``use_targets`` / ``mode``：借刀杀人恒单阶段（spec D11）。

    引擎内部确实有这两个东西（``RespondContext``），但它们只用于**生成候选**——
    官方"使用者选 An 的同时选 An 使用【杀】的合法目标 Bn"已经被烘进候选
    （``UseCard{杀, targets=(Bn,)}``），故线上没有第二阶段可问。
    这条断言防的是"顺手把 ``use_targets`` 也下发"：那会让界面长出一次多余的目标选择。
    """
    checked = 0
    for seed in (31, 37):
        session = Session(seed=seed, ai="random", timeout_seconds=None, player_count=8)
        guard = 0
        while session.pending is not None and guard < 400:
            guard += 1
            query = session.pending
            if str(query.kind) == "RESPOND":
                checked += 1
                payload = query.model_dump(mode="json")
                assert "use_targets" not in payload, "RESPOND 询问不得带 use_targets（第二阶段不存在）"
                assert "mode" not in payload, "RESPOND 询问不得带 mode（候选里读得出是打出还是使用）"
            session.submit_answer(query.legal_actions[0])
    # 覆盖元断言：一条"逐条检查"的循环若一条都没检查到，它就是空转通过。
    assert checked >= 50, f"取样太少（只查到 {checked} 次 RESPOND），断言谈不上机械"


def test_the_candidate_index_is_derived_from_the_legal_actions() -> None:
    """候选索引是**索引**，不是第二份合法性：它算出的每个池、每组组合都能在候选里找到。

    判据（spec §四条不变量）：
    ① **池 ⊆ 候选**、**组合 ⊆ 候选**（每组组合都对应一条该牌的候选）；
    ② **候选 ⊆ 索引**（每条带牌的候选都进了某张牌的 ``combos``，不丢候选）。

    跨多个种子、多种人数跑局取样——一张牌的池会随存活人数与装备技能变化，
    单局取样会漏掉【方天画戟】那种"额定一组 + 追加一组"的形状。
    """
    checked = 0
    for seed in (11, 23, 37):
        for player_count in (2, 5, 8):
            session = Session(
                seed=seed, ai="random", timeout_seconds=None, player_count=player_count
            )
            guard = 0
            while session.pending is not None and guard < 400:
                guard += 1
                query = session.pending
                index = candidate_index(query)
                payload = pending_payload(session)
                assert payload is not None

                # ② 候选 ⊆ 索引：每条带牌的候选都在索引里，且挂在**自己那张牌**下
                for action in payload["legal_actions"]:
                    card = action.get("card")
                    if card is None:
                        continue
                    entry = index.get(str(card["card_id"]))
                    assert entry is not None, f"{action['kind']} 的牌不在候选索引里"
                    assert entry["card"] == card, "索引里的牌面必须与候选里的是同一张"
                    assert action.get("targets", []) in entry["combos"], (
                        f"{card['name']} 的候选 targets={action.get('targets')} 不在 combos 里"
                    )

                # ① 池 / 组合 ⊆ 候选：索引里的每一组组合都能在候选里找到那一条
                by_card_and_targets = {
                    (str(action["card"]["card_id"]), tuple(action.get("targets", [])))
                    for action in payload["legal_actions"]
                    if action.get("card") is not None
                }
                for card_id, entry in index.items():
                    for combo in entry["combos"]:
                        assert (card_id, tuple(combo)) in by_card_and_targets, (
                            f"索引凭空多出一组组合：card_id={card_id} targets={combo}"
                        )
                    for seat in entry["pool"]:
                        assert any(seat in combo for combo in entry["combos"]), (
                            f"池里的座位 {seat} 不在任何组合里"
                        )
                    assert entry["min_targets"] <= entry["max_targets"]
                    sizes = {len(combo) for combo in entry["combos"]}
                    assert entry["min_targets"] == min(sizes)
                    assert entry["max_targets"] == max(sizes)
                    # 键名是**契约**：界面按 `card` / `pool` / `min_targets` / `max_targets` / `combos`
                    # 读；只断言"值算得对"而不断言键名，会让"界面读了一个不存在的键"这类
                    # 静默失配穿过全部测试（前端读不到只会退化，不会抛错）。
                    assert set(entry) == {"card", "pool", "min_targets", "max_targets", "combos"}, (
                        f"候选索引的键集合变了：{sorted(entry)}；改键名必须同时改 app.js 的读法"
                    )
                    assert isinstance(entry["card"], dict) and "card_id" in entry["card"]
                    assert isinstance(entry["pool"], list)
                    assert isinstance(entry["combos"], list) and entry["combos"]
                    assert isinstance(entry["min_targets"], int)
                    assert isinstance(entry["max_targets"], int)
                checked += 1
                session.submit_answer(query.legal_actions[0])

    assert checked > 200, f"取样太少（{checked} 次询问），断言谈不上机械"


# ---------------------------------------------------------------------------
# 5c. 转化来源索引（报障 4）：点【闪】要命中"当【杀】"的那条虚拟候选
# ---------------------------------------------------------------------------


def _play_query(state, seat: int = 0):
    """人为局面上的一个 ``PLAY_PHASE`` 询问（只为 ``build_skills`` / ``virtual_index`` 取样）。"""
    from sgs.effects import legal_actions_for
    from sgs.state import PlayPhaseQuery, QueryKind as _QueryKind

    return PlayPhaseQuery(
        seq=1,
        actor=seat,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="",
        legal_actions=legal_actions_for(state, seat, _QueryKind.PLAY_PHASE, answers_seq=1),
        view=project(state, seat),
    )


def test_the_virtual_index_aliases_a_conversion_source_to_its_virtual_card() -> None:
    """【龙胆】的【闪】→【杀】：界面按**实体【闪】**的 id 读得到那条虚拟候选。

    不建这份别名，玩家点【闪】时按实体 id 查候选索引必然查空 ——【闪】被画成"用不出"，
    这正是报障 4 截图里那张灰掉的【闪】。
    """
    from sgs_web.projection import virtual_index

    pack = _context_pack()
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    query = _play_query(engine.state)

    aliases = virtual_index(query, engine.state)[str(dodge)]

    assert len(aliases) == 1, "单来源转化：这张【闪】此刻只产出一张虚拟牌"
    assert aliases[0]["skill_ids"] == ["hero.zhaoyun.longdan"]
    entry = candidate_index(query)[str(aliases[0]["card_id"])]
    assert entry["card"]["type"] == "slash", "别名指向的是那张虚拟【杀】"
    assert entry["card"]["virtual_sources"] == [dodge], "虚拟牌的来源就是这张实体牌"


def test_the_pending_payload_carries_the_virtual_source_index() -> None:
    """跨线契约：``pending_payload`` 必须下发 ``virtual_index``（否则前端读不到）。"""
    session = Session(seed=20261012, ai="random", timeout_seconds=None, player_count=8)

    payload = pending_payload(session)

    assert payload is not None
    assert "virtual_index" in payload
    assert isinstance(payload["virtual_index"], dict)


def test_rende_skill_entry_is_server_driven_not_manual() -> None:
    """票 05：【仁德】改走步骤流水线 ⇒ 界面**不再**走"直接多选手牌 → 直接点武将"的
    ``ui.manual`` 模式（那是候选烘载荷时代的产物），改由服务端的
    ``SELECT_CARDS → SELECT_OPTION`` 两步驱动；候选仍是可点的骨架。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    hand = (*cards_of_type(pack, "slash")[:2], cards_of_type(pack, "dodge")[0])
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=hand, hero="liubei"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    session = SimpleNamespace(engine=engine, pending=_play_query(engine.state, 0))

    entry = next(
        item for item in build_skills(session, 0) if item["skill_id"] == "hero.liubei.rende"
    )

    assert entry["available"] is True, "骨架候选仍在出牌阶段池里（技能按钮可点）"
    assert entry["ui"]["manual"] is False, "改由服务端逐步询问，前端不再自行拼合载荷"
    assert entry["ui"]["pick_cards"] == [] and entry["ui"]["pick_seats"] == []
    assert entry["ui"]["requirement"] == "交给一名其他角色任意张手牌"


def test_longdan_skill_entry_lists_the_convertible_source_cards() -> None:
    """报障 4：转牌没有 ``ActivateSkill`` 候选，界面靠 ``ui.convert_sources`` 让按钮点得动。"""
    from types import SimpleNamespace

    pack = _context_pack()
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    session = SimpleNamespace(engine=engine, pending=_play_query(engine.state, 0))

    entry = next(
        item for item in build_skills(session, 0) if item["skill_id"] == "hero.zhaoyun.longdan"
    )

    assert entry["available"] is False, "转牌不是 ActivateSkill 候选"
    assert entry["ui"]["convert_sources"] == [dodge], "这张【闪】此刻当【杀】"
    assert entry["ui"]["manual"] is False


def _provide_query(engine, *, actor: int = 0, type_id: str = "slash"):
    """人为局面上的一个「提供一张 B」询问（票 14）：面取自引擎的同一个 ``provide_face``。"""
    from sgs.skills.flow import provide_face

    slots, _ = provide_face(engine.state, actor, type_id, (Zone.HAND,))
    return SelectCardsQuery(
        seq=1,
        actor=actor,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt=f"提供一张【{type_id}】继续使用（目标不变；可发动转化技）",
        legal_actions=(),
        view=project(engine.state, actor),
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
        slots=slots,
        provide_type=type_id,
    )


def test_the_pending_payload_carries_the_provide_type() -> None:
    """票 14：``pending_payload`` 必须**显式**下发 ``provide_type``。

    它**不在** ``SELECT_CARDS`` 的记录流里（``exclude=True``，既有 Golden Replay 一字不改），
    故界面只能从这份显式载荷读到"这一步是提供一张 B"。丢了它，前端就退回新开选择面——
    正是用户实机报障的"界面套娃"。
    """
    pack = _context_pack()
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    query = _provide_query(engine)

    payload = pending_payload(query)

    assert payload is not None
    assert payload["provide_type"] == "slash"
    assert "provide_type" not in query.model_dump(mode="json"), "不进记录流"


def test_the_virtual_index_reads_the_provide_faces_slots() -> None:
    """票 14：「提供一张 B」的虚拟 B 只在 ``slots`` 上（候选只有面内位置）。

    ``virtual_index`` 必须把槽上的来源实体牌（【龙胆】的【闪】）映射到那张虚拟【杀】，
    否则【青龙】追问时【龙胆】的技能按钮点不亮（"待答期间技能栏仍可点"这条验收会红）。
    """
    from sgs_web.projection import virtual_index

    pack = _context_pack()
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    query = _provide_query(engine)

    aliases = virtual_index(query, engine.state)

    assert list(aliases) == [str(dodge)], "面上那张虚拟【杀】的来源就是这张【闪】"
    assert aliases[str(dodge)][0]["skill_ids"] == ["hero.zhaoyun.longdan"]
    assert aliases[str(dodge)][0]["card_id"] < 0, "别名指向的是虚拟牌（没有实体 id）"


def test_the_provide_window_lists_the_conversion_skill_hint() -> None:
    """票 14：``skill_hints`` 也要读到**选择面槽位**上的虚拟 B。

    只看候选时，"提供一张 B"的窗口列不出【龙胆】/【武圣】的文案——玩家就不知道
    这一步还能发动转化技（用户口径"青龙追问时再点龙胆"）。
    """
    pack = _context_pack()
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    query = _provide_query(engine)

    context = query_context(engine, query)

    hints = {hint["skill_id"]: hint for hint in context["skill_hints"]}
    assert "hero.zhaoyun.longdan" in hints, f"提供面里的虚拟【杀】没归到【龙胆】：{hints}"
    # 【龙胆】两个方向共用一条 `SkillDef`，requirement 是两条声明用"；"并起来的。
    assert "选择1张【闪】当【杀】使用或打出" in hints["hero.zhaoyun.longdan"]["requirement"]


# ---------------------------------------------------------------------------
# 5c. 询问窗口的"谁对谁做了什么"（本轮报障）
# ---------------------------------------------------------------------------


_CONTEXT_PACK = None


def _context_pack():
    """标准牌堆（惰性加载，避免模块导入期就建表）。"""
    global _CONTEXT_PACK
    if _CONTEXT_PACK is None:
        from sgs.data import load_content_pack

        _CONTEXT_PACK = load_content_pack(deck_profile="standard_108")
    return _CONTEXT_PACK


# ---------------------------------------------------------------------------
# 合成帧的**窄写方**键表（retro-2 S2）
#
# 教训：票 12 修的硬违规——`projection._step_requirement` 读 `frame.data["skill_id"]`，
# 而**被动帧（DAMAGE / USE）从不写它**（派发环写的是 `running_skill`）；合成用例却把这个
# 引擎不写的键注进了帧，于是 6 条断言全绿（`CODING_STANDARDS.md` §1 的 M4 同型错）。
#
# 表只收**有窄写方**的键：值 = 真的会写它的帧类型（写方见括注）。表外键一律放行——
# `frame.data` 是开放字典，技能自己会写一堆私有键（`liuli_used` / `yiji_seen:0` …），
# 全量枚举只会 drift。本表只回答一个问题：**这个键属于这一类帧吗**。
# ---------------------------------------------------------------------------
_KIND_SPECIFIC_KEYS: dict[str, frozenset[FrameKind]] = {
    # SKILL 帧（`engine/game.py` 的 SKILL 路径）与 JUDGE 帧（改判）写它；其余帧想让投影
    # 认领技能，走的是派发环写的 `running_skill`（`catalog._dispatch`）。
    "skill_id": frozenset({FrameKind.SKILL, FrameKind.JUDGE}),
}


def _context_fixture(seats, *, frame_kind, frame_data):
    """只有"一个栈顶帧"的最小引擎面：:func:`query_context` 只读 ``state`` 与 ``stack``。

    ``frame_data`` 可以是 dict，也可以是 ``state -> dict``（取 ``CardRef`` 需要先有 state）。

    **合成帧必须与引擎的写入面同源**（retro-2 S2）：窄写方的键（见
    :data:`_KIND_SPECIFIC_KEYS`）出现在不写它的帧类型上时**直接抛**，不让断言在假形状上变绿。
    断"读取方真的读到了"的测试走**真会话**，样板见
    :func:`test_a_real_session_reaches_the_yiji_card_step_wording`。
    """
    from types import SimpleNamespace

    state = build_state(pack=_context_pack(), seats=seats)
    data = frame_data(state) if callable(frame_data) else dict(frame_data)
    for key, kinds in _KIND_SPECIFIC_KEYS.items():
        if key in data and frame_kind not in kinds:
            allowed = " / ".join(sorted(kind.value for kind in kinds))
            raise AssertionError(
                f"合成帧注入了引擎不写的键：{key!r} 只由 {allowed} 帧写，"
                f"而这里是 {frame_kind.value} 帧（被动帧的技能归属走 running_skill）。"
                "—— 断'读取方读到了'的测试必须走真实形状（retro-2 S2 / CODING_STANDARDS §6）。"
            )
    stack = ResolutionStack()
    stack.push(Frame(kind=frame_kind, data=data))
    return SimpleNamespace(state=state, stack=stack), state


def test_query_context_names_the_skill_and_its_owner() -> None:
    """【遗计】的询问窗口要写得出"1号位郭嘉对1号位郭嘉使用遗计"。

    技能询问（``SKILL_ACTIVATION`` / ``SELECT_OPTION``）没有"使用的牌"，名字来自
    ``skill_id``（可能是 ``hero.guojia.yiji`` 这样的命名空间 id，也可能是
    ``yiji:give:0:0`` 这样的合成键）；作用者是技能持有者（= 询问的 actor），
    目标也是他自己——这就是报障给的样例形状。
    """
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="guojia")],
        frame_kind=FrameKind.DAMAGE,
        frame_data={"target": 1, "source": 0, "targets": (1,)},
    )
    query = SelectOptionQuery(
        seq=1,
        actor=1,
        timing=Timing.DAMAGE_AFTER_TAKE,
        prompt="请选择一项。",
        legal_actions=(),
        view=project(state, 1),
        skill_id="yiji:give:0:0",
        options=("seat:0", "seat:1"),
    )

    context = query_context(engine, query)

    assert context is not None
    assert context["label"] == "1号位郭嘉对1号位郭嘉使用遗计", context
    assert context["name"] == "遗计"
    assert context["targets"] == [1]
    # 合成键要能查回技能定义；票 11 起"给谁"步还有自己的逐步文案（键 = 剥掉 ``:<点>:<轮>``
    # 后的 ``give``），故这里读到的已是那一步的问法，而不是一句话的 summary。
    assert context["requirement"] == "选择将这张牌交给哪名角色", "合成键要能查回技能定义"


def test_query_context_names_the_card_user_and_the_current_target() -> None:
    """牌询问要写得出"0号位张飞对2号位貂蝉使用五谷丰登"（用**当前目标**，不是全部目标）。

    多目标锦囊在 C 段逐目标结算，``RESOLVE`` 帧的 ``cursor`` 指向当前目标；
    响应窗口（无懈）自带的 ``target`` 优先。这里用响应窗口的样例。
    """
    harvest = cards_of_type(_context_pack(), "harvest")[0]
    engine, state = _context_fixture(
        [SeatSpec(hero="zhangfei"), SeatSpec(), SeatSpec(hero="diaochan")],
        frame_kind=FrameKind.RESOLVE,
        frame_data=lambda s: {
            "card": s.card(harvest),
            "source": 0,
            "targets": (1, 2),
            "cursor": 1,
        },
    )
    query = RespondQuery(
        seq=2,
        actor=2,
        timing=Timing.RESOLVE_BEFORE_EFFECT,
        prompt="响应窗口：请使用一张牌，或放弃。",
        legal_actions=(),
        view=project(state, 2),
        cause=WindowCause.NULLIFY,
        target=2,
        window_frame=1,
    )

    context = query_context(engine, query)

    assert context is not None
    assert context["label"] == "0号位张飞对2号位貂蝉使用五谷丰登", context
    assert context["name"] == "五谷丰登"
    assert context["targets"] == [2]
    assert context["requirement"] == "需要使用【无懈可击】"


def test_the_pending_payload_carries_a_context_key() -> None:
    """联动：``pending_payload`` 必须下发 ``context``（哪怕这一问没有可写的上下文 = ``None``）。"""
    session = Session(seed=83, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    payload = pending_payload(session)

    assert payload is not None
    assert "context" in payload, "询问窗口的上下文是契约键，读不到只会静默退化"


def test_option_seats_maps_seat_choices_and_ignores_the_rest() -> None:
    """``option_seats`` 只把 ``seat:N`` 选项映射成座位；别的选项（弃牌 / 摸牌）不进。

    界面据此把座位画成可点的（【遗计】"交给谁"），解析在服务端、前端只读数据。
    """
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="guojia"), SeatSpec()],
        frame_kind=FrameKind.DAMAGE,
        frame_data={"target": 1, "source": 0, "targets": (1,)},
    )
    seat_query = SelectOptionQuery(
        seq=1,
        actor=1,
        timing=Timing.DAMAGE_AFTER_TAKE,
        prompt="请选择一项。",
        legal_actions=(),
        view=project(state, 1),
        skill_id="yiji:give:0:0",
        options=("seat:0", "seat:2"),
    )
    plain_query = SelectOptionQuery(
        seq=2,
        actor=1,
        timing=Timing.USE_AFTER_DESIGNATE_TARGET,
        prompt="请选择一项。",
        legal_actions=(),
        view=project(state, 1),
        skill_id="equip.double_sword",
        options=("discard_one", "draw_one"),
    )

    assert option_seats(seat_query) == {"seat:0": 0, "seat:2": 2}
    assert option_seats(plain_query) == {}
    assert option_seats(
        RespondQuery(
            seq=3,
            actor=0,
            timing=Timing.RESOLVE_BEFORE_EFFECT,
            prompt="响应窗口：请使用一张牌，或放弃。",
            legal_actions=(),
            view=project(state, 0),
            cause=WindowCause.NULLIFY,
        )
    ) == {}


def test_query_context_reads_the_running_skill_for_a_watch_style_card_pick() -> None:
    """【遗计】的"选哪张亮牌"窗口必须写"使用遗计"，**不能**写成造成伤害的那张牌。

    这条询问是 ``SELECT_CARDS`` 且带了 ``revealed`` 授予（观看型选牌）：名字取栈顶帧的
    ``running_skill``（谁在跑这个回调），而不是帧里的渠道牌。不这么分，窗口会写成
    "0号位X对1号位郭嘉使用杀"——玩家会以为自己在响应【杀】。
    """
    slash = cards_of_type(_context_pack(), "slash")[0]
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="guojia")],
        frame_kind=FrameKind.DAMAGE,
        frame_data=lambda s: {
            "card": s.card(slash),
            "source": 0,
            "targets": (1,),
            "target": 1,
            "running_skill": "hero.guojia.yiji",
        },
    )
    granted = state.card(slash)
    query = SelectCardsQuery(
        seq=1,
        actor=1,
        timing=Timing.DAMAGE_AFTER_TAKE,
        prompt="请从选择面里选择一张牌。",
        legal_actions=(),
        view=project(state, 1),
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True),
        slots=(Slot(ref=ZoneRef(Zone.HAND, 1), card=granted),),
        revealed=(granted,),
    )

    context = query_context(engine, query)

    assert context is not None
    assert context["label"] == "1号位郭嘉对1号位郭嘉使用遗计", context
    assert context["name"] == "遗计"


def test_query_context_falls_back_to_the_damage_source_for_a_dying_window() -> None:
    """濒死求桃没有"使用的牌"，退化成"谁对谁造成了伤害"（否则这一问在窗口里没有上下文）。"""
    engine, state = _context_fixture(
        [SeatSpec(hero="zhangfei"), SeatSpec(hero="diaochan")],
        frame_kind=FrameKind.DEATH,
        frame_data={"target": 1, "dying": True, "damage_source": 0},
    )
    query = PeachQuery(
        seq=3,
        actor=1,
        timing=Timing.NEAR_DEATH_AT,
        prompt="座位 1 处于濒死状态：是否对其使用【桃】？",
        legal_actions=(),
        view=project(state, 1),
        dying=1,
    )

    context = query_context(engine, query)

    assert context is not None
    assert context["label"] == "0号位张飞对1号位貂蝉造成伤害", context
    assert context["targets"] == [1]


def test_query_context_points_a_nullify_window_at_the_card_not_the_nullifier() -> None:
    """无懈窗口的"目标"是被无懈的**那张牌**，不是无懈使用者自己（本轮报障）。

    报障原文：「无懈可击的目标是卡牌，不是自己」。核实为真：无懈自身的 ``RESOLVE`` 帧
    ``targets = ()``，"当前目标"因此退回 ``source``（= 无懈使用者），窗口上下文于是写成
    "1号位对1号位使用无懈可击"——看起来像自己无懈自己。正确读法是顺着 ``cancel_target``
    找到被无懈的那一层，把**原来那张牌**连同它的使用者 / 目标写进窗口。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    trick = cards_of_type(pack, "ex_nihilo")[0]
    nullify = cards_of_type(pack, "nullify")[0]
    state = build_state(pack=pack, seats=[SeatSpec(), SeatSpec(), SeatSpec()])
    stack = ResolutionStack()
    stack.push(
        Frame(
            kind=FrameKind.RESOLVE,
            data={"card": state.card(trick), "source": 0, "targets": (1,)},
            frame_id=7,
        )
    )
    stack.push(
        Frame(
            kind=FrameKind.RESOLVE,
            data={
                "card": state.card(nullify),
                "source": 1,
                "targets": (),
                "cancel_target": (7, 1),
            },
            frame_id=8,
        )
    )
    engine = SimpleNamespace(state=state, stack=stack)
    query = RespondQuery(
        seq=4,
        actor=2,
        timing=Timing.RESOLVE_BEFORE_EFFECT,
        prompt="响应窗口：请使用一张牌，或放弃。",
        legal_actions=(),
        view=project(state, 2),
        cause=WindowCause.NULLIFY,
        target=1,
        window_frame=8,
    )

    context = query_context(engine, query)

    assert context is not None
    assert context["label"] == "1号位使用【无懈可击】（抵消 0号位对1号位使用的【无中生有】）", context
    assert context["name"] == "无懈可击"
    assert context["targets"] == [1], "窗口仍要指向被无懈那张牌的目标座位"
    assert context["requirement"] == "需要使用【无懈可击】"


def test_query_context_names_the_judged_character_for_a_delayed_trick() -> None:
    """延时锦囊的无懈窗口要写得出"0号位吕布判定乐不思蜀（判定开始前）"（第 2 项报障）。

    报障原文：「修复乐不思蜀在其他角色回合询问无懈可击的bug……应该是自身判定阶段开始时、
    判定开始前询问」。引擎的**时机本来就是对的**（`_phase_judge` 只在回合角色自己的判定区
    取牌、压在 `PHASE_JUDGE`、`JUDGE_ON` 之前）。真正的问题是提示：判定路径的 `RESOLVE`
    帧 `source=None`，而 `query_context` 对 `source is None` 直接返回 `None` ⇒ 界面不写
    "本窗口"，玩家只看见处理区一张【乐不思蜀】和另一名角色判定区里的另一张，就误以为在问
    别人的牌。

    判据：窗口上下文要点名**被判定者**与**牌名**；时机线索（判定开始前）也写进去。
    """
    indulgence = cards_of_type(_context_pack(), "indulgence")[0]
    engine, state = _context_fixture(
        [SeatSpec(hero="lvbu"), SeatSpec(hero="diaochan"), SeatSpec()],
        frame_kind=FrameKind.RESOLVE,
        frame_data=lambda s: {
            "card": s.card(indulgence),
            "source": None,
            "targets": (0,),
            "use_timing": Timing.PHASE_JUDGE,
        },
    )
    query = RespondQuery(
        seq=11,
        actor=0,
        timing=Timing.RESOLVE_BEFORE_EFFECT,
        prompt="响应窗口：请使用一张牌，或放弃。",
        legal_actions=(),
        view=project(state, 0),
        cause=WindowCause.NULLIFY,
        required="nullify",
        target=0,
        window_frame=1,
    )

    context = query_context(engine, query)

    assert context is not None, "判定窗口的上下文不能是 None（否则界面写不出'这是谁的牌'）"
    assert context["label"] == "0号位吕布判定乐不思蜀（判定开始前）", context
    assert context["name"] == "乐不思蜀"
    assert context["targets"] == [0], "指向被判定者，不是别处"
    assert context["requirement"] == "需要使用【无懈可击】"


# ---------------------------------------------------------------------------
# 7c "需要做什么"（item 4）：卡牌窗口的张数、转化技文案、观星
# ---------------------------------------------------------------------------


def test_requirement_counts_a_wushuang_duel_demand_and_phrases_it():
    """样例②：3号位吕布对0号位曹操使用决斗 ⇒ 需要打出2张【杀】（【无双】）。"""
    duel = cards_of_type(_context_pack(), "duel")[0]
    engine, state = _context_fixture(
        [SeatSpec(hero="caocao"), SeatSpec(), SeatSpec(), SeatSpec(hero="lvbu")],
        frame_kind=FrameKind.RESOLVE,
        frame_data=lambda s: {"card": s.card(duel), "source": 3, "targets": (0,)},
    )
    query = RespondQuery(
        seq=2,
        actor=0,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        prompt="响应窗口：请打出一张牌，或放弃。",
        legal_actions=(),
        view=project(state, 0),
        cause=WindowCause.DUEL,
        required="slash",
        target=None,
        window_frame=1,
    )

    context = query_context(engine, query)

    assert context["label"] == "3号位吕布对0号位曹操使用决斗"
    assert context["requirement"] == "需要打出2张【杀】（依次打出两张，每打完一张再询问一次）"


def test_requirement_drops_the_increment_note_when_one_card_suffices():
    """正面对照：没有【无双】时只需一张，文案不带"依次两张"。"""
    duel = cards_of_type(_context_pack(), "duel")[0]
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()],
        frame_kind=FrameKind.RESOLVE,
        frame_data=lambda s: {"card": s.card(duel), "source": 1, "targets": (0,)},
    )
    query = RespondQuery(
        seq=3,
        actor=0,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        prompt="响应窗口：请打出一张牌，或放弃。",
        legal_actions=(),
        view=project(state, 0),
        cause=WindowCause.DUEL,
        required="slash",
        target=None,
        window_frame=1,
    )

    context = query_context(engine, query)

    assert context["requirement"] == "需要打出【杀】"


def test_requirement_names_the_wusheng_conversion_skill_in_the_play_phase():
    """样例⑤：关羽想出【杀】⇒ 窗口里列出【武圣】："选择1张红色牌当【杀】使用或打出"。

    转化技没有独立询问，候选是空的 `UseCard`（虚拟牌不带技能归属），故接入层按
    `SkillDef.virtual_type_ids` 把牌名对回技能，把它的 `requirement` 一并列出。
    """
    pack = _context_pack()
    red = next(index for index, card in enumerate(pack.deck.cards) if card.suit in ("heart", "diamond"))
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hero="guanyu", hand=(red,)), SeatSpec()],
        deck=tuple(index for index in range(len(pack.deck.cards)) if index != red),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=3)
    while engine.pending is None:
        engine._step()
    query = engine.pending
    assert query.kind == "PLAY_PHASE"

    context = query_context(engine, query)

    assert context["requirement"] == "使用一张牌（或发动技能），或结束出牌阶段"
    hints = {hint["skill_id"]: hint for hint in context["skill_hints"]}
    assert "hero.guanyu.wusheng" in hints, f"窗口里有虚拟【杀】候选却没列出【武圣】：{hints}"
    assert hints["hero.guanyu.wusheng"]["name"] == "武圣"
    assert hints["hero.guanyu.wusheng"]["requirement"] == "选择1张红色牌当【杀】使用或打出"


def test_requirement_names_the_guanxing_arrange_even_though_its_frame_is_not_on_the_stack():
    """【观星】的排列询问：阶段体帧不压栈，仍要写得出"需要做什么"。"""
    from types import SimpleNamespace

    from sgs.skills import CATALOG

    state = build_state(pack=_context_pack(), seats=[SeatSpec(hero="zhugeliang"), SeatSpec()])
    engine = SimpleNamespace(state=state, stack=ResolutionStack(), phase_body_skill=lambda: "hero.zhugeliang.guanxing")
    query = ArrangeQuery(
        seq=5,
        actor=0,
        timing=Timing.PHASE_PREPARE,
        prompt="请排列这些牌：给出顺序与前几张置于牌堆顶。",
        legal_actions=(),
        view=project(state, 0),
    )

    context = query_context(engine, query)

    assert context is not None, "排列询问也要写得出'需要做什么'"
    assert context["requirement"] == CATALOG["hero.zhugeliang.guanxing"].requirement
    assert context["label"] is None, "阶段体帧不压栈 ⇒ 事件行为空，但不该因此丢掉 requirement"


def test_the_candidate_index_never_carries_rule_judgements() -> None:
    """③ 索引无规则：它只聚合候选里已有的座位，不算距离 / 攻击范围 / 花色 / 体力 / 存活。

    取样的判据：索引里的座位**恰好**等于该牌候选的目标并集——多一个（"我算出来的合法目标"）
    或少一个（"我过滤掉了无效目标"）都会判红。这条断言把"别在接入层重新实现规则"钉死。
    """
    for seed in (13, 29):
        session = Session(seed=seed, ai="random", timeout_seconds=None, player_count=8)
        guard = 0
        while session.pending is not None and guard < 300:
            guard += 1
            query = session.pending
            index = candidate_index(query)
            expected: dict[str, set[int]] = {}
            for action in query.legal_actions:
                card = getattr(action, "card", None)
                if card is None:
                    continue
                expected.setdefault(str(card.card_id), set()).update(
                    getattr(action, "targets", ())
                )

            assert set(index) == set(expected), "索引的牌集合必须与候选的牌集合逐键相同"
            for card_id, seats in expected.items():
                assert set(index[card_id]["pool"]) == seats
            session.submit_answer(query.legal_actions[0])


# ---------------------------------------------------------------------------
# 6. 录像与回放闭环（票 05；判据 3）
# ---------------------------------------------------------------------------


def _finished_session(seed: int = 79, **kwargs) -> Session:
    session = Session(seed=seed, ai="random", timeout_seconds=None, **{**TWO_PLAYERS, **kwargs})
    _play_to_end(session)
    assert session.finished
    return session


def test_the_journal_round_trips_through_the_file(tmp_path: Path) -> None:
    from sgs.engine import read_journal

    session = _finished_session()
    path = save_run(tmp_path, session)

    raw = path.read_bytes()
    records = read_journal(path)

    assert dump_journal(records).encode("utf-8") == raw, "录像必须是可逐字节往返的 canonical JSONL"


def test_the_replay_closes_the_loop_from_the_sidecar_meta(tmp_path: Path) -> None:
    """判据 3：该局记录流经引擎重放**逐行相同**，且逐座位武将能从侧车放回原位。"""
    session = _finished_session(seed=83)
    save_run(tmp_path, session)
    name = next(tmp_path.glob("*.jsonl")).name[: -len(".jsonl")]

    loaded = load_run(tmp_path, name)
    meta = loaded["meta"]

    assert meta["heroes"] == list(session.hero_assignments), "逐座位武将必须能从侧车放回原位"
    replayed = replay_journal(meta)

    assert dump_journal(replayed) == dump_journal(session.records())


def test_saving_twice_never_overwrites(tmp_path: Path) -> None:
    session = _finished_session(seed=89)
    first = save_run(tmp_path, session)
    original = first.read_bytes()

    second = save_run(tmp_path, session)

    assert second != first
    assert first.read_bytes() == original, "同名不得覆盖（覆盖会悄悄吃掉一局战绩）"
    assert len(list(tmp_path.glob("*.jsonl"))) == 2
    assert len(list(tmp_path.glob("*.meta.json"))) == 2


def test_a_broken_run_is_refused_instead_of_half_read(tmp_path: Path) -> None:
    session = _finished_session(seed=97)
    path = save_run(tmp_path, session)
    name = path.name[: -len(".jsonl")]

    path.write_bytes(path.read_bytes()[: len(path.read_bytes()) // 2])  # 截断
    with pytest.raises(ValueError):
        load_run(tmp_path, name)

    (tmp_path / f"{name}.meta.json").unlink()
    with pytest.raises(ValueError):
        load_run(tmp_path, name)


def test_listing_runs_reports_the_pair_and_the_outcome(tmp_path: Path) -> None:
    session = _finished_session(seed=103)
    save_run(tmp_path, session)

    runs = list_runs(tmp_path)

    assert len(runs) == 1
    assert runs[0]["seed"] == session.seed
    assert runs[0]["player_count"] == session.rule_config.player_count
    assert isinstance(runs[0]["human_seat_won"], bool)


def test_the_three_hero_pool_states_stay_distinguishable() -> None:
    """``"enabled"``（标准局）/ ``None``（无武将）/ 序列，三态必须分得开（否则侧车会重放错局）。"""
    enabled = Session(seed=107, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    none = Session(seed=107, ai="random", timeout_seconds=None, hero_pool=None, **TWO_PLAYERS)
    explicit = Session(
        seed=107, ai="random", timeout_seconds=None, hero_pool=("liubei", "sunquan"), **TWO_PLAYERS
    )

    assert all(hero is not None for hero in enabled.hero_assignments)
    assert none.hero_assignments == (None, None)
    assert explicit.hero_assignments == ("liubei", "sunquan")


# ---------------------------------------------------------------------------
# 7. 技能清单（刀 4 后端片）：只给自己的、名称/类型/锁定/可用性都可核
# ---------------------------------------------------------------------------


def test_the_session_view_carries_the_human_seats_skills() -> None:
    """**响应体**必须带本人技能清单——引擎下发的可见面里没有技能，前端无从画按钮。

    注意这条打的是 `GameRegistry.view`（HTTP 层真正下发的那个 dict）这一**缝**，
    不是直接调 `build_skills`：直接调函数会漏掉"接线断了"这一类回归。
    """
    from sgs_web.serve import GameRegistry

    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 8, "ai": "heuristic", "seed": 20261012, "timeout_seconds": None}
    )
    payload = registry.view(session, after=-1, logs_after=0)

    assert "skills" in payload, "响应体里没有 skills（接线断了）"
    assert payload["skills"], "本人应当有技能"
    skills = payload["skills"]

    hero_id = session.hero_assignments[session.human_seat]
    assert hero_id is not None
    from sgs.data import load_heroes

    expected = {skill.name for skill in load_heroes(session.pack.pack_id).by_id(hero_id).skills}
    assert {skill["name"] for skill in skills} == expected, "技能清单必须与该武将的武将面一致"


def test_every_skill_entry_carries_type_lock_and_availability() -> None:
    session = Session(seed=7, ai="heuristic", timeout_seconds=None, player_count=8)
    for entry in build_skills(session, session.human_seat):
        assert set(entry) == {
            "skill_id",
            "name",
            "skill_type",
            "locked",
            "carrier",
            "available",
            "ui",
        }
        assert isinstance(entry["locked"], bool)
        assert isinstance(entry["available"], bool)
        assert entry["skill_type"] in ("NORMAL", "LORD")
        assert entry["carrier"] in ("HERO", "EQUIPMENT")
        # `ui` 是**表现层契约**：改键名必须同时改 app.js 的读法（同候选索引的姿态）。
        assert set(entry["ui"]) == {
            "requirement",
            "manual",
            "pick_cards",
            "pick_seats",
            "convert_sources",
        }, f"技能界面提示的键集合变了：{sorted(entry['ui'])}"
        assert isinstance(entry["ui"]["manual"], bool)
        assert isinstance(entry["ui"]["pick_cards"], list)
        assert isinstance(entry["ui"]["pick_seats"], list)
        assert isinstance(entry["ui"]["convert_sources"], list)


def test_equipment_skills_are_listed_under_the_equipment_card_name() -> None:
    """装备技能与武将技能**同列**：中文名 = 装备牌名、``carrier`` = ``EQUIPMENT``。

    报障原文：「将装备技能也按照规则绘制按钮，和武将技能放在一起」。原实现按"武将面里
    有没有这个短 id"取名，装备技能没有中文名 ⇒ 整条被 ``continue`` 掉（地图 D20 的旧登记）。
    判据：给座位装一张【诸葛连弩】，清单里必须出现 ``equip.crossbow`` 且名字为"诸葛连弩"；
    武将技能仍在同一份清单里。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    crossbow = cards_of_type(pack, "crossbow")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hero="guanyu", equip=(crossbow,))],
    )
    engine = Engine.from_state(state, seed=1)
    entries = build_skills(SimpleNamespace(engine=engine, pending=None), 0)
    by_id = {entry["skill_id"]: entry for entry in entries}

    equip = by_id.get("equip.crossbow")
    assert equip is not None, "装备技能必须出现在清单里（不再因没有中文名被略去）"
    assert equip["name"] == "诸葛连弩", "中文名取装备牌名"
    assert equip["carrier"] == "EQUIPMENT"
    assert equip["locked"] is True, "【诸葛连弩】是锁定技"
    assert equip["available"] is False, "没有 ActivateSkill 候选 ⇒ 不可点"
    assert any(entry["carrier"] == "HERO" for entry in entries), "武将技能与它同列"


def test_skills_never_leak_another_seats_inventory() -> None:
    """别人的技能是**暗置**信息：清单里只能有 `seat` 自己的实例。"""
    session = Session(seed=20261012, ai="heuristic", timeout_seconds=None, player_count=8)
    mine = {entry["skill_id"] for entry in build_skills(session, session.human_seat)}

    assert mine, "人类座位应当有技能"
    for seat in range(session.rule_config.player_count):
        if seat == session.human_seat:
            continue
        # 无武将时该座位没有技能；有武将时其技能与我的**不共享 skill_id**（每人一名武将）。
        theirs = {entry["skill_id"] for entry in build_skills(session, seat)}
        assert not (mine & theirs), f"座位 {seat} 的技能与我的重叠：{mine & theirs}"


def test_the_lord_skill_gate_is_inherited_not_reimplemented() -> None:
    """主公技的门控**不在接入层**：清单直接读 `resolve_instances`，身份门控在引擎侧已兑现。

    这里钉两件事：清单里**没有**任何条目是我自己筛出来的——它恰等于 `resolve_instances`
    里 `owner == seat` 的那些（装备技自本轮起也列，故不再有"被略去"的差集）。
    """
    from sgs.skills import resolve_instances

    session = Session(seed=20261012, ai="heuristic", timeout_seconds=None, player_count=8)
    mine = {entry["skill_id"] for entry in build_skills(session, session.human_seat)}
    owners_instances = {
        instance.skill_id
        for instance in resolve_instances(session.engine.state)
        if instance.owner == session.human_seat
    }

    assert mine == owners_instances, "清单必须逐条对应本座位的实例（不多不少）"
    assert mine, "人类座位应当有技能"


def test_availability_follows_the_pending_queries_candidates() -> None:
    """`available` 只由"当前挂起里有没有这个技能的 ActivateSkill 候选"决定。"""
    session = Session(seed=20261012, ai="random", timeout_seconds=None, player_count=8)
    for _ in range(300):
        pending = session.pending
        if pending is None:
            break
        offered = {
            action.skill_id
            for action in pending.legal_actions
            if action.kind == "ActivateSkill"
        }
        for entry in build_skills(session, session.human_seat):
            assert entry["available"] is (entry["skill_id"] in offered), (
                f"{entry['name']} 的 available 与挂起询问的候选不一致"
            )
        session.submit_answer(pending.legal_actions[0])


# ---------------------------------------------------------------------------
# 分步技能的**逐步文案**（票 01 的【离间】样板 / 票 02 的【青囊】）
# ---------------------------------------------------------------------------


def _skill_step_cards_query(state, *, zones: tuple[Zone, ...], optional: bool = False):
    return SelectCardsQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="请从选择面里选择一张牌。",
        legal_actions=(),
        view=project(state, 0),
        spec=SelectionSpec(zones=zones, total=(1, 1), optional=optional),
        slots=(),
    )


def _skill_step_option_query(state, *, skill_id: str, options: tuple[str, ...]):
    return SelectOptionQuery(
        seq=2,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="请选择一项。",
        legal_actions=(),
        view=project(state, 0),
        skill_id=skill_id,
        options=options,
    )


def test_query_context_walks_the_lijian_steps_with_their_own_wording() -> None:
    """【离间】三步各有各的问法：先"弃置1张牌"，再"选择两名男性角色…"。

    步 1 是 ``SELECT_CARDS``（询问自带不了步骤名），步 2/3 是 ``SELECT_OPTION``（步骤名在
    ``skill_id`` 冒号后）。两处都只读**询问里已有的标识** ⇒ 询问协议与 Golden Replay 不动。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="diaochan"), SeatSpec(), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.diaochan.lijian", "source": 0},
    )

    cards_query = _skill_step_cards_query(
        state, zones=(Zone.HAND, Zone.EQUIP), optional=True
    )
    assert query_context(engine, cards_query)["requirement"] == "弃置1张牌"

    for step in ("user", "victim"):
        option_query = _skill_step_option_query(
            state, skill_id=f"lijian:{step}", options=("seat:1", "seat:2")
        )
        assert query_context(engine, option_query)["requirement"] == (
            "选择场上两名男性角色，先点选的是决斗使用者，后点选的是决斗的目标"
        ), f"步骤 {step} 的问法"


def test_query_context_walks_the_qingnang_steps_with_their_own_wording() -> None:
    """【青囊】两步各有各的问法（票 02）：先"弃置1张手牌"，再"选择一名已受伤角色…"。

    键的取法完全复用【离间】那一套：``SELECT_CARDS`` 读 ``"card"``、``SELECT_OPTION``
    读询问 ``skill_id`` 冒号后那段（执行器发的是 ``qingnang:seat``）。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="huatuo"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.huatuo.qingnang", "source": 0},
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,), optional=True)
    assert query_context(engine, cards_query)["requirement"] == "弃置1张手牌"

    option_query = _skill_step_option_query(
        state, skill_id="qingnang:seat", options=("seat:0", "seat:1")
    )
    assert query_context(engine, option_query)["requirement"] == "选择一名已受伤角色回复1点体力"


def test_query_context_reads_the_fankui_step_wording_for_a_damage_frame() -> None:
    """【反馈】是**被动技**：它的选牌步文案要在 ``DAMAGE`` 帧上取到（票 07）。

    被动技没有 ``SKILL`` 帧，技能的归属由派发环写的帧内 ``running_skill`` 给出（与【遗计】
    同一条链）；步骤键仍是 ``"card"``（``SELECT_CARDS`` 的既有约定）。这条钉的是"逐步文案
    不是主动技专属"——迁到流水线之后，被动技的每一步照样报得出"需要做什么"。
    """
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="simayi")],
        frame_kind=FrameKind.DAMAGE,
        frame_data={
            "source": 0,
            "target": 1,
            "targets": (1,),
            "running_skill": "hero.simayi.fankui",
            # **不**注 ``skill_id``：被动帧从不写这个键（全仓只有 SKILL 帧与 JUDGE 帧写它），
            # 归属只由派发环写的 ``running_skill`` 给出 —— 这就是真实帧形状（票 12 §1）。
        },
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND, Zone.EQUIP))

    assert query_context(engine, cards_query)["requirement"] == "获得伤害来源的一张牌"


def test_query_context_walks_the_liuli_steps_with_their_own_wording() -> None:
    """【流离】（票 08）两步各有各的问法：先"弃置一张牌"，再"选择转移给谁"。

    它是第二条走流水线的**被动技**（票 07 的【反馈】之后）：归属由派发环写的帧内
    ``running_skill`` 给出（被动技没有 ``SKILL`` 帧）。步 2 是 ``SELECT_OPTION``、
    ``skill_id`` = ``liuli:seat``（票 08 的交互变更），文案按冒号后那段取。
    """
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="daqiao")],
        frame_kind=FrameKind.USE,
        frame_data={
            "source": 0,
            "targets": (1,),
            "running_skill": "hero.daqiao.liuli",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND, Zone.EQUIP))
    assert query_context(engine, cards_query)["requirement"] == "弃置一张牌（手牌 / 装备区）"

    option_query = _skill_step_option_query(
        state, skill_id="liuli:seat", options=("seat:2",)
    )
    assert query_context(engine, option_query)["requirement"] == "选择将此【杀】转移给哪名其他角色"


def test_query_context_walks_the_jieyin_steps_with_their_own_wording() -> None:
    """【结姻】两步各有各的问法（票 03）：先"弃置2张手牌"，再"选择一名已受伤男性角色…"。

    键的取法完全复用【离间】/【青囊】那一套：``SELECT_CARDS`` 读 ``"card"``、
    ``SELECT_OPTION`` 读询问 ``skill_id`` 冒号后那段（执行器发的是 ``jieyin:seat``）。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="sunshangxiang"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.sunshangxiang.jieyin", "source": 0},
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,), optional=True)
    assert query_context(engine, cards_query)["requirement"] == "弃置2张手牌"

    option_query = _skill_step_option_query(
        state, skill_id="jieyin:seat", options=("seat:1",)
    )
    assert query_context(engine, option_query)["requirement"] == (
        "选择一名已受伤男性角色回复1点体力"
    )


def test_query_context_reads_the_equipment_cost_steps_own_wording() -> None:
    """【青龙偃月刀】/【贯石斧】的成本步也报得出"需要做什么"（票 09）。

    两条是**装备接缝**（``SeamKind.SLASH_CANCELED``）上的被动技：迁移后成本从发动候选
    下沉成 ``SELECT_CARDS`` 步，归属仍由派发环写的帧内 ``running_skill`` 给出（与
    【反馈】/【流离】同一条链），步骤键仍是 ``SELECT_CARDS`` 的既有约定 ``"card"``。
    """
    engine, state = _context_fixture(
        [SeatSpec(equip=(cards_of_type(_context_pack(), "axe")[0],)), SeatSpec()],
        frame_kind=FrameKind.USE,
        frame_data={
            "source": 0,
            "targets": (1,),
            "running_skill": "equip.axe",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND, Zone.EQUIP))

    assert query_context(engine, cards_query)["requirement"] == (
        "弃置两张牌（手牌 / 装备区）令此【杀】依然造成伤害"
    )


def test_query_context_reads_the_qilin_bow_and_ice_sword_step_wording() -> None:
    """【麒麟弓】/【寒冰剑】的选牌步也报得出"需要做什么"（票 10）。

    两条都是 ``DAMAGE_DEAL`` 上的装备钩子：归属由派发环写的帧内 ``running_skill`` 给出
    （与【反馈】/【流离】/票 09 两条接缝同一条链），步骤键仍是 ``SELECT_CARDS`` 的既有
    约定 ``"card"``。【麒麟弓】迁上流水线、【寒冰剑】按票 10 的裁定保持手写，但逐步文案
    同落一处（:attr:`~sgs.skills.catalog.SkillDef.step_requirements`）。
    """
    engine, state = _context_fixture(
        [SeatSpec(equip=(cards_of_type(_context_pack(), "qilin_bow")[0],)), SeatSpec()],
        frame_kind=FrameKind.DAMAGE,
        frame_data={
            "source": 0,
            "target": 1,
            "targets": (1,),
            "running_skill": "equip.qilin_bow",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )
    cards_query = _skill_step_cards_query(state, zones=(Zone.EQUIP,))
    assert query_context(engine, cards_query)["requirement"] == (
        "弃置目标装备区里的一匹坐骑（只能选坐骑）"
    )

    engine, state = _context_fixture(
        [SeatSpec(equip=(cards_of_type(_context_pack(), "ice_sword")[0],)), SeatSpec()],
        frame_kind=FrameKind.DAMAGE,
        frame_data={
            "source": 0,
            "target": 1,
            "targets": (1,),
            "running_skill": "equip.ice_sword",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )
    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND, Zone.EQUIP))
    assert query_context(engine, cards_query)["requirement"] == (
        "依次弃置目标的一张牌（手牌 / 装备区），至多两张"
    )


def test_query_context_reads_the_double_sword_step_wording() -> None:
    """【雌雄双股剑】的手写钩子也报得出选牌步自己的问法（票 11）。

    与票 10 的【寒冰剑】同姿态：**不迁流水线不代表没有逐步文案**——逐步文案是投影侧的
    声明（:attr:`~sgs.skills.catalog.SkillDef.step_requirements`），与执行路径无关。
    选取面是**目标自己的手牌**（应答者 = 目标），步骤键仍是 ``SELECT_CARDS`` 的既有约定
    ``"card"``；归属由帧内 ``running_skill`` 给出。

    另一步（``discard_one`` / ``draw_one`` 的 ``SELECT_OPTION``）的询问 ``skill_id`` 是
    **裸技能 id**（``equip.double_sword``，没有冒号），投影按现有键约定取不到步骤名，
    故它照读一句话的 summary —— 那是记录流的一部分，票 11 不动（见票的 ``## Answer``）。
    """
    engine, state = _context_fixture(
        [SeatSpec(equip=(cards_of_type(_context_pack(), "double_sword")[0],)), SeatSpec()],
        frame_kind=FrameKind.USE,
        frame_data={
            "source": 0,
            "targets": (1,),
            "running_skill": "equip.double_sword",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,))

    assert query_context(engine, cards_query)["requirement"] == "由目标选择弃置一张手牌"


def test_query_context_walks_the_yiji_steps_with_their_own_wording() -> None:
    """【遗计】逐张分配的两步各有各的问法（票 11）：先"从亮出的牌里选择一张"，再"给谁"。

    两条的键都按**询问里已有的标识**取，不动询问协议：

    - 选牌步是 ``SELECT_CARDS``（步骤键 = ``"card"``；帧内有 ``running_skill``）；
    - "给谁"步是 ``SELECT_OPTION``，执行器发的 ``skill_id`` 带**点号 + 轮号**
      （``yiji:give:<点>:<轮>``，票 05 的逐轮缓存键）——投影去掉末尾的 ``:<数字>`` 段
      （可多段）后命中 ``"give"``。这一段的形状就是真实询问里的形状，不是合成键。
    """
    engine, state = _context_fixture(
        [SeatSpec(), SeatSpec(hero="guojia")],
        frame_kind=FrameKind.DAMAGE,
        frame_data={
            "source": 0,
            "target": 1,
            "targets": (1,),
            "running_skill": "hero.guojia.yiji",
            # 被动帧没有 ``skill_id``（真实帧形状；票 12 §1）。
        },
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,), optional=True)
    assert query_context(engine, cards_query)["requirement"] == "从亮出的牌里选择一张"

    give_query = _skill_step_option_query(state, skill_id="yiji:give:0:1", options=("seat:1",))
    assert query_context(engine, give_query)["requirement"] == "选择将这张牌交给哪名角色"


# ---------------------------------------------------------------------------
# 票 12 §1 的真会话回归：读取方在**真实帧**下真的读到了分步文案
# ---------------------------------------------------------------------------


class _ScriptedSeat:
    """真会话里的脚本参与者：有技能发动候选就发动，否则取第一个候选。"""

    def respond(self, query):
        for action in query.legal_actions:
            if getattr(action, "kind", None) == "ActivateSkill":
                return action
        return query.legal_actions[0]


def _advance_to_the_yiji_card_step(session: Session, *, limit: int = 20_000) -> None:
    """逐步把**真引擎**推到【遗计】的 ``SELECT_CARDS`` 询问（不到就炸，不静默退化）。"""
    engine = session.engine
    for _ in range(limit):
        pending = engine.pending
        if pending is not None and pending.kind == "SELECT_CARDS":
            return
        if pending is None:
            engine._step()  # noqa: SLF001 - 与 tests/test_hero_damage_after.py 同一推进手法
            if engine.finished:
                break
            continue
        engine.submit(session.participants[pending.actor].respond(pending))
    raise AssertionError("真会话没有走到【遗计】的选牌步")


def _yiji_real_session() -> Session:
    """一条**真会话**：真引擎 + 真 ``DAMAGE`` 帧 + 真派发环，停在【遗计】的选牌步。

    随机建局走不到【遗计】（武将与挨打都随机），故引擎换成人为局面；但会话壳、派发环、
    ``pending_payload`` / ``query_context`` 这条**读取方**链路一字不换 —— 这正是票 12 §1
    要的证据：不是合成帧上的读数，而是网页真会走到的那份数据。
    """
    pack = _context_pack()
    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hero="guojia")],
        deck=tuple(card_id for card_id in range(len(pack.deck.cards)) if card_id != slash),
        processing=(slash,),
    )
    session = Session(pack=pack, seed=1, ai="random", timeout_seconds=None, **TWO_PLAYERS)
    session.engine = Engine.from_state(state, seed=1)
    session.participants = tuple(_ScriptedSeat() for _ in range(2))
    session.engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 0,
            "targets": (1,),
            "card": state.card(slash),
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 1,
            "channel": state.card(slash),
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    _advance_to_the_yiji_card_step(session)
    return session


def test_a_real_session_reaches_the_yiji_card_step_wording() -> None:
    """票 12 §1 的关键回归：生产帧形状下，【遗计】选牌步的**分步文案真的被读到**。

    这条钉的是 ``CODING_STANDARDS.md`` §1 的读取方：旧判据读 ``frame.data["skill_id"]``，
    而被动帧**从不写**这个键（派发环只写 ``running_skill``）⇒ 逐步文案全部退回一句话
    summary，合成用例却因为把引擎从不写的键注进了帧而全绿（M4 同型错）。故这里

    - 跑**真引擎**到【遗计】的 ``SELECT_CARDS``（真 DAMAGE 帧 + 真派发环写 ``running_skill``）；
    - 先断言帧里**没有** ``skill_id``（真实形状，合成注入无从混入）；
    - 再断言读取方拿到的 ``context.requirement`` 是**分步文案**而不是 summary
      （summary 是 "观看牌堆顶两张牌，然后逐张交给任意角色"）。
    """
    session = _yiji_real_session()
    query = session.pending

    assert query is not None and query.kind == "SELECT_CARDS"
    assert query.skill_id is None, "【遗计】选牌步的询问不带 skill_id（真实形状）"
    data = session.engine.stack.frames[-1].data
    assert data.get("running_skill") == "hero.guojia.yiji"
    assert "skill_id" not in data, "被动帧从不写 skill_id —— 这正是旧判据读不到的原因"

    payload = pending_payload(session)

    assert payload is not None and payload["context"] is not None
    assert payload["context"]["requirement"] == "从亮出的牌里选择一张", (
        "读取方必须真的读到分步文案；退回一句话 summary 就说明判据又读错了键"
    )


def test_the_context_fixture_refuses_a_key_that_damage_frames_never_carry() -> None:
    """retro-2 S2 的守卫：合成帧不许注入引擎不写的键。

    正是这一档假绿盖住了票 12 的硬违规（被动帧读 ``skill_id``）。守卫只有几行，钉住它；
    **正反两面都断**，免得"守卫恒抛"也算通过。
    """
    with pytest.raises(AssertionError, match="skill_id"):
        _context_fixture(
            [SeatSpec(), SeatSpec()],
            frame_kind=FrameKind.DAMAGE,
            frame_data={"target": 1, "source": 0, "skill_id": "hero.guojia.yiji"},
        )
    # 正向对照：SKILL 帧本来就写 skill_id，照常放行。
    engine, _ = _context_fixture(
        [SeatSpec(hero="guojia"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.guojia.yiji", "source": 0},
    )
    assert engine.stack.frames[-1].data["skill_id"] == "hero.guojia.yiji"


def test_query_context_walks_the_zhiheng_step_with_its_own_wording() -> None:
    """【制衡】单步（票 04）也有自己的问法：``SELECT_CARDS`` 读 ``"card"`` 键。

    单步技能没有第二条步骤文案，故只钉这一条；键的取法与【离间】/【青囊】/【结姻】同源。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="sunquan"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.sunquan.zhiheng", "source": 0},
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND, Zone.EQUIP), optional=True)
    assert query_context(engine, cards_query)["requirement"] == "弃置任意张牌（手牌 / 装备区）"


def test_query_context_walks_the_rende_steps_with_their_own_wording() -> None:
    """【仁德】两步各有各的问法（票 05）：先"选择任意张要交给其他角色的手牌"，再"交给谁"。

    键的取法完全复用【离间】/【青囊】/【结姻】/【制衡】那一套：``SELECT_CARDS`` 读
    ``"card"``、``SELECT_OPTION`` 读询问 ``skill_id`` 冒号后那段（执行器发 ``rende:seat``）。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="liubei"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.liubei.rende", "source": 0},
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,), optional=True)
    assert query_context(engine, cards_query)["requirement"] == (
        "选择任意张要交给其他角色的手牌"
    )

    option_query = _skill_step_option_query(state, skill_id="rende:seat", options=("seat:1",))
    assert query_context(engine, option_query)["requirement"] == "选择交给哪名其他角色"


def test_query_context_walks_the_fanjian_steps_with_their_own_wording() -> None:
    """【反间】三步各有各的问法（票 06）：步 2/3 的文案要写出**归属**（"由目标…"），
    否则发动者看不懂为什么屏幕跑到别人那儿去了（票面明写的要求）。"""
    engine, state = _context_fixture(
        [SeatSpec(hero="zhouyu"), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.zhouyu.fanjian", "source": 0},
    )

    seat_query = _skill_step_option_query(state, skill_id="fanjian:seat", options=("seat:1",))
    assert query_context(engine, seat_query)["requirement"] == "选择一名其他角色"

    suit_query = _skill_step_option_query(
        state, skill_id="fanjian:suit", options=("heart", "spade")
    )
    assert query_context(engine, suit_query)["requirement"] == "由目标选择一种花色"

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,))
    assert query_context(engine, cards_query)["requirement"] == "由目标从你的手牌中选择一张"

    repeat_query = _skill_step_option_query(
        state, skill_id="fanjian:seat:1", options=("seat:1",)
    )
    assert query_context(engine, repeat_query)["requirement"] == "选择一名其他角色", (
        "同一步问多次时执行器补的序号（``:1``）只用于答案缓存，不该让文案掉回 summary"
    )


def test_query_context_falls_back_to_the_one_line_requirement_for_other_active_skills() -> None:
    """没有 ``step_requirements`` 的技能仍读一句话的 summary（旧技能一字不改）。

    A 族六条主动技自票 06 起**全部**有了逐步文案，样本因此是**装备技能**；票 11 又把
    【雌雄双股剑】补上了选牌步文案，故样本改用同样没有逐步文案的【丈八蛇矛】
    ——回退面本身是通用的（读 :attr:`~sgs.skills.catalog.SkillDef.requirement`），
    没有理由再绑在某条逐票要补文案的技能上（那会让本用例每票都要跟着改）。
    """
    engine, state = _context_fixture(
        [SeatSpec(equip=(cards_of_type(_context_pack(), "serpent_spear")[0],)), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "equip.serpent_spear", "source": 0},
    )

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,))
    assert query_context(engine, cards_query)["requirement"] == "选择两张手牌当【杀】使用或打出"
