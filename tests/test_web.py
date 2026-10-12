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
from sgs.state import Action  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    build_skills,
    pending_payload,
    visible_record,
    visible_snapshot,
)
from sgs_web.replay import list_runs, load_run, replay_journal, save_run  # noqa: E402
from sgs_web.session import Session, StaleAnswer  # noqa: E402
from sgs_web.timeout import fallback_action  # noqa: E402

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
        assert set(entry) == {"skill_id", "name", "skill_type", "locked", "carrier", "available"}
        assert isinstance(entry["locked"], bool)
        assert isinstance(entry["available"], bool)
        assert entry["skill_type"] in ("NORMAL", "LORD")
        assert entry["carrier"] == "HERO"


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

    这里只钉一件事：清单里**没有**任何条目是我自己筛出来的——它恰等于
    `resolve_instances` 里 `owner == seat` 的那些（装备技因暂无中文名被略去）。
    """
    from sgs.skills import resolve_instances

    session = Session(seed=20261012, ai="heuristic", timeout_seconds=None, player_count=8)
    mine = {entry["skill_id"] for entry in build_skills(session, session.human_seat)}
    owners_instances = {
        instance.skill_id
        for instance in resolve_instances(session.engine.state)
        if instance.owner == session.human_seat
    }

    assert mine <= owners_instances, "清单里出现了不属于本座位的实例"
    assert owners_instances - mine <= owners_instances, "（装备技暂不列，见地图 D20）"
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
