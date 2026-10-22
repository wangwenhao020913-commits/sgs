"""M4 接入层的纯函数与机制单测（票 01 起；不起服务、不连网、**不 import threading**）。

本文件刻意不住在 ``src/sgs/`` 的测试域里——它测的是 ``tools/sgs_web/``。
``sys.path`` 引导只做一次（见 :func:`_bootstrap`），让 ``sgs`` 与 ``sgs_web`` 都可导入。
"""

from __future__ import annotations

import json
import re
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
from sgs.data import Identity, Kingdom, Suit, load_content_pack, validate_rule_config  # noqa: E402
from sgs.engine import Engine, RandomSource, dump_journal  # noqa: E402
from sgs.resolution import Frame, ResolutionStack  # noqa: E402
from sgs.skills import FREE_SPLIT  # noqa: E402
from sgs.state import (  # noqa: E402
    Action,
    ActivateSkill,
    ArrangeQuery,
    EndPhase,
    FrameKind,
    Pass,
    PeachQuery,
    Phase,
    PlayerPublic,
    PlayPhaseQuery,
    QueryKind,
    RespondQuery,
    SelectCards,
    SelectCardsQuery,
    SelectionSpec,
    SelectOptionQuery,
    Slot,
    Step,
    StepPool,
    UseCard,
    WindowCause,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing  # noqa: E402
from sgs_web.display import (  # noqa: E402
    CARD_ATTRIBUTES,
    CARD_KINDS,
    SUIT_LABELS,
    card_display,
    prompt_display,
    suit_labels,
)
from sgs_web.descriptions import skill_description  # noqa: E402
from sgs_web.projection import (  # noqa: E402
    HARVEST_REQUIREMENT,
    build_skills,
    candidate_index,
    harvest_state,
    option_seats,
    pending_payload,
    query_context,
    seat_skill_display,
    visible_record,
    visible_snapshot,
)
from sgs_web.replay import list_runs, load_run, replay_journal, save_run  # noqa: E402
from sgs_web.session import Session, StaleAnswer  # noqa: E402
from sgs_web.timeout import fallback_action  # noqa: E402
from support.positions import SeatSpec, build_state, cards_of_type  # noqa: E402
from support.scripts import PassPlayer, run_queries  # noqa: E402

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


# ---------------------------------------------------------------------------
# 6b. 开局点将（票 19）：建局参数 ``my_hero`` + 点将列表只读面
# ---------------------------------------------------------------------------


def test_a_picked_hero_lands_on_the_human_seat_and_nowhere_else() -> None:
    """建局参数 ``my_hero`` = "我要的武将"；其余 AI 不得再拿到他/她。"""
    session = Session(
        seed=37,
        ai="random",
        timeout_seconds=None,
        player_count=5,
        my_hero="liubei",
    )

    assigned = session.hero_assignments
    assert assigned[session.human_seat] == "liubei"
    assert "liubei" not in assigned[1:], "本局其它座位不得选我已点的武将"
    assert len(set(assigned)) == len(assigned), "其余座位仍是无放回随机"


def test_the_pick_follows_the_human_seat_not_the_literal_seat_zero() -> None:
    """人类座位可选（开局页的「我的座位」）⇒ 点将必须落在**他那张椅子**上。"""
    session = Session(
        seed=37, ai="random", timeout_seconds=None, player_count=5, human_seat=3, my_hero="guanyu"
    )

    assert session.hero_assignments[3] == "guanyu"
    assert "guanyu" not in session.hero_assignments[:3] + session.hero_assignments[4:]


def test_no_pick_reproduces_the_pre_ticket_random_session() -> None:
    """开关关着 ⇒ 逐字节就是引入点将之前那条纯随机路径（默认参数不动行为）。"""
    plain = Session(seed=41, ai="random", timeout_seconds=None, player_count=5)
    explicit_none = Session(
        seed=41, ai="random", timeout_seconds=None, player_count=5, my_hero=None
    )

    assert plain.hero_assignments == explicit_none.hero_assignments
    assert len(set(plain.hero_assignments)) == 5, "关着仍是随机发放，不是不发"


def test_an_unknown_pick_is_refused_at_session_construction() -> None:
    """未知 id ⇒ fail-closed 报错，不静默退回随机。"""
    with pytest.raises(ValueError) as caught:
        Session(seed=43, ai="random", timeout_seconds=None, player_count=5, my_hero="nobody")

    assert "nobody" in str(caught.value)


def test_the_registry_passes_the_pick_through_from_the_request_body() -> None:
    """打 ``GameRegistry.create`` 这一缝：**接线断了**要比"引擎报错"先被看见。"""
    from sgs_web.serve import GameRegistry

    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {
            "player_count": 5,
            "ai": "random",
            "seed": 20261012,
            "timeout_seconds": None,
            "my_hero": "diaochan",
        }
    )
    payload = registry.view(session, after=-1, logs_after=0)

    assert payload["heroes"][0] == "diaochan"
    assert "diaochan" not in payload["heroes"][1:]


def test_the_hero_catalog_lists_exactly_the_enabled_pool() -> None:
    """点将列表的只读面：只有**启用**武将，且与发放用的启用池同源同序。"""
    from sgs.data import enabled_hero_ids, load_heroes
    from sgs_web.serve import hero_catalog

    catalog = hero_catalog()

    assert [entry["id"] for entry in catalog] == list(enabled_hero_ids(load_heroes()))
    assert all(entry["name"] for entry in catalog), "列表要能给人看（名称非空）"


# ---------------------------------------------------------------------------
# 3b. 牌堆档位：建局读 body / 未知档 400 / 开局页文案（M5-01b）
# ---------------------------------------------------------------------------


def _post_with_the_real_handler(registry, path: str, body: dict) -> tuple[int, dict]:
    """把一条 POST 交给**真的** ``serve._Handler``（进程内；不开端口、不起线程）。

    未知档位的 400 是 **HTTP 层**的判据，只能从 handler 的响应里读。假 socket 只补
    stdlib handler 需要的两个动作（``makefile`` / ``sendall``）：请求 / 响应仍按 HTTP
    报文走，解析、路由、状态码与 JSON 都是产品代码给出的。
    """

    import io as _io

    from sgs_web.serve import _Handler

    class _Buffer(_io.BytesIO):
        def close(self):  # handler.finish() 会关流；响应还要读，故忽略关闭
            pass

    class _Socket:
        def __init__(self, raw: bytes) -> None:
            self._reader = _Buffer(raw)
            self._writer = _Buffer()

        def makefile(self, mode, *args, **kwargs):  # noqa: ANN002, ANN003 - 基类签名
            return self._reader if "r" in mode else self._writer

        def sendall(self, data: bytes) -> None:
            self._writer.write(data)

    payload = json.dumps(body).encode("utf-8")
    raw = (
        f"POST {path} HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload
    handler_cls = type("_BoundHandler", (_Handler,), {"registry": registry})
    socket = _Socket(raw)
    handler_cls(socket, ("127.0.0.1", 0), None)
    response = socket._writer.getvalue()
    head, _, response_body = response.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(response_body.decode("utf-8"))


def test_the_registry_builds_the_deck_profile_from_the_request_body() -> None:
    """打 ``GameRegistry.create`` 这一缝：建局请求里的档位真的进了会话与下发体。"""
    from sgs_web.serve import GameRegistry

    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {
            "player_count": 2,
            "ai": "random",
            "seed": 20261012,
            "timeout_seconds": None,
            "deck_profile": "standard_ex_160",
        }
    )
    payload = registry.view(session, after=-1, logs_after=0)

    assert session.pack.deck_profile == "standard_ex_160"
    assert len(session.pack.deck.cards) == 160
    assert payload["deck_profile"] == "standard_ex_160"


def test_the_registry_defaults_to_standard_108_without_a_deck_profile() -> None:
    """不传档位 = 内容包默认档；M5 之前每一局走的就是这条路。"""
    from sgs_web.serve import GameRegistry

    registry = GameRegistry(runs_dir="/tmp/unused")
    session = registry.create(
        {"player_count": 2, "ai": "random", "seed": 20261012, "timeout_seconds": None}
    )

    assert session.pack.deck_profile == "standard_108"
    assert len(session.pack.deck.cards) == 108


def test_an_unknown_deck_profile_is_a_400_and_creates_no_session() -> None:
    """未知档位：HTTP 400，且 registry 里**不留半局会话**。"""
    from sgs_web.serve import GameRegistry

    registry = GameRegistry(runs_dir="/tmp/unused")
    status, body = _post_with_the_real_handler(
        registry,
        "/api/sessions",
        {
            "player_count": 2,
            "ai": "random",
            "seed": 20261012,
            "timeout_seconds": None,
            "deck_profile": "standard_999",
        },
    )

    assert status == 400
    assert "decks.unknown_profile" in body["error"]
    assert registry.sessions == {}, "未知档位不得留下半局会话"


def test_the_deck_catalog_lists_the_packs_profiles_with_server_labels() -> None:
    """开局页的档位选项由服务端给：标准 108 / 军争 160，缺省档带标记。

    ``basic_53`` 是 M1 回归网的 53 张测试切片，不给人选——但会话层仍接受它
    （值域 = 内容包 ``decks`` 的全部键），这里只钉开局页铺哪几项。
    """
    from sgs_web.serve import deck_catalog

    pack = load_content_pack()
    catalog = deck_catalog()

    assert [entry["id"] for entry in catalog] == ["standard_108", "standard_ex_160"]
    assert "basic_53" not in {entry["id"] for entry in catalog}
    assert all(entry["id"] in pack.decks for entry in catalog), "文案表里的档位必须真在数据里"
    assert [entry["id"] for entry in catalog if entry["default"]] == ["standard_108"]
    assert all(entry["name"] for entry in catalog), "每个档位都要有人看的标签"
    assert all(entry["description"] for entry in catalog), "每个档位都要有说明文案"


def test_the_lobby_form_offers_the_deck_profile_from_server_content() -> None:
    """开局页「牌堆档位」的静态契约：选择器在位、建局带字段、**前端不抄档位文案表**。

    行为由 ``tests/support/ui_smoke.mjs`` 的 ⓪13–⓪19 驱动**真表单**覆盖（真 HTTP）；
    这里钉的是接线本身，以及"选项 / 标签只从 `GET /api/decks` 来"这条票面纪律。
    """
    static = REPO_ROOT / "tools" / "sgs_web" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    lobby = (static / "lobby.js").read_text(encoding="utf-8")
    main = (static / "main.js").read_text(encoding="utf-8")

    assert 'id="in-deck-profile"' in html, "开局页缺「牌堆档位」选择器"
    assert "deck_profile:" in lobby, "建局请求体没带 deck_profile"
    assert "loadDeckCatalog" in lobby and "loadDeckCatalog();" in main, "开局时没拉档位列表"
    scripts = "\n".join(path.read_text(encoding="utf-8") for path in static.glob("*.js"))
    assert not re.search(r"""["']standard_(?:108|ex_160)["']""", scripts), (
        "前端抄了第二份档位 id / 文案表；选项与标签只能由服务端 GET /api/decks 下发"
    )
    assert "军争 160" not in scripts and "标准 108" not in scripts


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


def test_the_yiji_timeout_gives_the_remaining_cards_to_the_owner() -> None:
    """U2-12 / D9.1：遗计两步的超时落点。

    - **选牌步**：本轮删掉了 ``Pass``，候选按面内位置枚举 ⇒ 兜底落到第一条 = 第一张亮牌；
    - **"交给谁"步**：``skill_id`` 前缀 ``yiji:give:``，兜底是 ``seat:{actor}``（把剩下的牌
      交给自己）。候选第一条通常是 ``seat:0``、不一定是持有者，故必须特判；
    - 两步都是**纯函数**（同一询问两次调用同一答案），且都不再提供 ``Pass``。

    这是**真会话**：真引擎走到【遗计】的选牌步，再把选牌答案送回去拿到真的给牌询问。
    """
    from sgs.state import SelectOption
    from sgs_web.display import prompt_display
    from sgs_web.timeout import yiji_give_self_choice

    session = _yiji_real_session()
    pick = session.pending
    assert pick is not None and pick.kind == "SELECT_CARDS"
    assert not any(action.kind == "Pass" for action in pick.legal_actions), (
        "选牌步不可放弃（D9.1）"
    )
    first = fallback_action(pick)
    assert isinstance(first, SelectCards) and first.picks == (0,), (
        f"选牌步兜底 = 面内第一张亮牌：{first}"
    )
    assert first == pick.legal_actions[0], "候选按面内位置枚举，第一条就是选位置 0"
    assert fallback_action(pick) == first, "兜底必须是纯函数"
    assert prompt_display(pick)["default_label"] == "超时将自动选择第一张亮牌"

    # 直接把选牌答案交给**引擎**（不走 `Session.submit_answer`：会话壳的 `drive()` 会让
    # 脚本参与者把下一问一起答掉，这里要停在真的『交给谁』询问上）。
    session.engine.submit(first)
    for _ in range(100):
        if session.engine.pending is not None:
            break
        session.engine._step()  # noqa: SLF001 - 与 `_advance_to_the_yiji_card_step` 同一手法
    give = session.engine.pending
    assert give is not None and give.kind == "SELECT_OPTION"
    assert str(give.skill_id).startswith("yiji:give:"), "这一步就是『交给谁』"
    assert give.actor == 1, "前置：持有者 / 应答者是座位 1（不是第一个座位）"
    assert give.legal_actions[0].choice != f"seat:{give.actor}", (
        "候选第一条不是自己——证明下面的落点来自特判，不是『取第一条』"
    )
    assert not any(action.kind == "Pass" for action in give.legal_actions), (
        "『交给谁』也不可放弃"
    )
    target = fallback_action(give)
    assert isinstance(target, SelectOption) and target.choice == f"seat:{give.actor}", (
        f"超时把剩下的牌交给自己：{target}"
    )
    assert fallback_action(give) == target, "兜底必须是纯函数"
    assert yiji_give_self_choice(give) == target, (
        "代答与倒计时文案共用同一条谓词（评审 Standards：display.py 不再重算它）"
    )
    assert prompt_display(give)["default_label"] == "超时将把剩下的牌交给自己", (
        "提示条文案要说清超时的落点（D2）"
    )


def test_the_ice_sword_timeout_takes_the_first_legal_slot_of_the_round() -> None:
    """U2-13 / D9.2：【寒冰剑】轮内的超时落点 = 第一个合法槽位。

    这一轮的形状与【反馈】相同（``SELECT_CARDS``、``total=(1, 1)``、``optional=False``、
    槽位在**目标**的区里，手牌是隐藏槽），因此同样没有 ``Pass``；兜底按既有顺序落到
    ``legal_actions[0]`` = 面内第一个槽位。本票**只改文案**、不特判技能，故这里把
    "第一个合法"钉在寒冰剑的形状上，防止以后有人为"必须两张"另加一条非确定性的兜底。
    """
    from sgs.effects import legal_actions_for
    from sgs.state import SelectContext

    armor = cards_of_type(_context_pack(), "eight_diagrams")[0]
    _, state = _context_fixture(
        [SeatSpec(), SeatSpec()],
        frame_kind=FrameKind.DAMAGE,
        frame_data={
            "source": 0,
            "target": 1,
            "targets": (1,),
            "running_skill": "equip.ice_sword",
        },
    )
    slots = (
        Slot(ref=ZoneRef(Zone.HAND, 1), card=None),
        Slot(ref=ZoneRef(Zone.HAND, 1), card=None),
        Slot(ref=ZoneRef(Zone.EQUIP, 1), card=state.card(armor)),
    )
    spec = SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1), optional=False)
    actions = legal_actions_for(
        state,
        0,
        QueryKind.SELECT_CARDS,
        answers_seq=1,
        payload=SelectContext(spec=spec, slots=slots),
    )
    query = SelectCardsQuery(
        seq=1,
        actor=0,
        timing=Timing.DAMAGE_DEAL,
        prompt="请从选择面里选择一张牌。",
        legal_actions=actions,
        view=project(state, 0),
        spec=spec,
        slots=slots,
    )

    assert not any(isinstance(action, Pass) for action in actions), "轮内没有 Pass（D9.2）"
    first = fallback_action(query)
    assert isinstance(first, SelectCards) and first == actions[0], (
        f"超时取第一个合法槽位：{first}"
    )
    assert fallback_action(query) == first, "兜底必须是纯函数"


def test_fallback_action_answers_the_target_of_a_fanjian_step() -> None:
    """票 06 / 用户验收：反间的步 2/3 由**目标**应答（不是发动者）。

    确认目标之后不可退回：选牌步 ``optional=False``，候选里没有 ``Pass``；超时兜底
    **取第一个合法项**（面内第一张手牌），不是"取消这一步"。"""
    from sgs.effects import legal_actions_for
    from sgs.state import QueryKind, SelectContext

    pack = _context_pack()
    cards = cards_of_type(pack, "slash")[:2]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=tuple(cards), hero="zhouyu"), SeatSpec()],
        turn_seat=0,
    )
    # 步 3 的形状：**目标**（座位 1）应答，选择面铺的是**发动者**（座位 0）的手牌。
    slots = tuple(
        Slot(ref=ZoneRef(Zone.HAND, 0), card=state.card(card_id)) for card_id in cards
    )
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=False)
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

    assert not any(action.kind == "Pass" for action in actions), "盲挑步不可放弃"
    first = fallback_action(query)
    assert isinstance(first, SelectCards) and first == actions[0], (
        f"超时取第一个合法项（面内第一张手牌）：{first}"
    )
    assert fallback_action(query) == first, "兜底必须是纯函数"


def _session_at_the_fanjian_suit_step(*, timeout_seconds: float | None) -> Session:
    """一局 AI 周瑜（座位 0）/ 人类目标（座位 1）的会话，停在【反间】的**花色步**。

    W4-2 起第一段是**预发动清单**（AI 的随机档不会填表），故给座位 0 注入一个会确认
    复合动作的参与者；之后引擎逐步把花色 / 盲挑选牌问**目标**（人类座位）。
    """
    human_seat = 1

    class _Activator:
        """座位 0：**第一次**见到【反间】清单就确认第一段（选人类座位），其余交给随机档。"""

        def __init__(self, fallback) -> None:  # noqa: ANN001 - 与 Participant 协议一致
            self._fallback = fallback
            self._done = False

        def respond(self, query):
            if (
                not self._done
                and query.kind == "PLAY_PHASE"
                and "hero.zhouyu.fanjian" in query.preactivations
            ):
                self._done = True
                return ActivateSkill(
                    skill_id="hero.zhouyu.fanjian",
                    choices=(f"seat:{human_seat}",),
                    answers_seq=query.seq,
                )
            return self._fallback.respond(query)

    session = Session(
        seed=0,
        player_count=2,
        ai="random",
        human_seat=human_seat,
        heroes=("zhouyu", "guanyu"),
        timeout_seconds=timeout_seconds,
    )
    session.participants = (
        _Activator(session.participants[0]),
        session.participants[1],
    )
    for _ in range(400):
        pending = session.pending
        if pending is None:
            break
        if pending.kind == "SELECT_OPTION" and str(pending.skill_id) == "fanjian:suit":
            return session
        session.submit_answer(fallback_action(pending))
    raise AssertionError("没有走到【反间】的花色步（AI 座位 0 的发动/清单没跑通）")


def test_a_timeout_answers_the_target_of_fanjian_in_a_real_session() -> None:
    """票 06 / 用户验收：反间的步 2/3 由**目标**应答。真实会话里（AI 周瑜反间人类座位）
    超时兜底要在"等目标作答"时替**目标**代答一次，且代答之后该局仍能跑完。

    确认目标后不可退回：花色步没有 ``Pass``，超时取**第一个合法花色**（``heart``）。
    W4-2 起第一段是**预发动清单**（AI 的随机档不会填表 ⇒ W4-4 才补），故这里给座位 0
    注入一个会确认复合动作的参与者；第二段仍由引擎逐步问**目标**（人类座位）。
    """
    session = _session_at_the_fanjian_suit_step(timeout_seconds=1.0)

    pending = session.pending
    assert pending is not None and pending.skill_id == "fanjian:suit", (
        "扫到了【反间】的第二段（第一段已由复合动作确认）"
    )
    assert pending.actor == session.human_seat, "这一步问的正是**目标**（人类座位）"
    assert not any(action.kind == "Pass" for action in pending.legal_actions), (
        "花色步不可放弃（确认目标后不可退回）"
    )
    first = fallback_action(pending)
    assert first == pending.legal_actions[0], "超时取第一个合法花色"
    assert getattr(first, "choice", None) == "heart", (
        "第一个合法花色 = Suit 枚举序的红桃"
    )

    session.mark_pending(100.0)
    assert session.expire_if_due(101.0, fallback_action) is True, "到点替目标代答"
    assert session.timeouts == 1
    assert session.session_log[-1].kind == "timeout"
    assert session.session_log[-1].detail["query_kind"] == "SELECT_OPTION"

    for _ in range(2000):
        if session.pending is None:
            break
        session.submit_answer(fallback_action(session.pending))

    assert session.pending is None, "被代答之后该局仍能跑完"


def test_the_pending_payload_labels_the_fanjian_suits_and_masks_the_blind_pick() -> None:
    """票 08（D10）：反间的两步在同一份**询问载荷**里带齐界面要的东西。

    花色步：``option_labels`` 是服务端派生的中文名 + 符号 + 红黑（前端不抄第二份表）；
    盲挑步：``slots[].card`` 全为 ``None``（牌面不可见），槽位仍按**位置**下发、且指向
    发动者的手牌。两步都是**真会话**（AI 周瑜反间人类目标），不是合成形状。
    """
    from sgs.state import SelectOption

    session = _session_at_the_fanjian_suit_step(timeout_seconds=None)

    suit = pending_payload(session)
    assert suit is not None
    assert suit["kind"] == "SELECT_OPTION"
    assert suit["skill_id"] == "fanjian:suit"
    assert suit["actor"] == session.human_seat, "花色步由**目标**应答"
    assert list(suit["options"]) == ["heart", "diamond", "spade", "club"]
    assert suit["option_labels"] == {
        "heart": {"name": "红桃", "symbol": "♥", "color": "red"},
        "diamond": {"name": "方块", "symbol": "♦", "color": "red"},
        "spade": {"name": "黑桃", "symbol": "♠", "color": "black"},
        "club": {"name": "梅花", "symbol": "♣", "color": "black"},
    }
    assert suit["option_seats"] == {}, "花色不是座位选项"
    assert not any(action["kind"] == "Pass" for action in suit["legal_actions"]), (
        "用户验收：确认目标后花色步不可退回，引擎不再给 Pass"
    )

    session.submit_answer(
        next(action for action in session.pending.legal_actions if isinstance(action, SelectOption))
    )
    blind = pending_payload(session)
    assert blind is not None
    assert blind["kind"] == "SELECT_CARDS"
    assert blind["option_labels"] is None, "盲挑不是选项枚举，没有可复用的展示标签"
    assert list(blind["spec"]["zones"]) == ["hand"]
    assert blind["spec"]["optional"] is False, "用户验收：盲挑步不可退回"
    hand = session.engine.state.player(0).hand
    assert len(blind["slots"]) == len(hand), "槽位数 = 发动者手牌数（位置公开、牌面隐藏）"
    assert all(slot["card"] is None for slot in blind["slots"]), "盲挑：一个牌面都不下发"
    assert {slot["ref"]["seat"] for slot in blind["slots"]} == {0}
    assert {slot["ref"]["zone"] for slot in blind["slots"]} == {"hand"}
    assert blind["context"]["requirement"] == "由目标从你的手牌中选择一张"
    assert not any(action["kind"] == "Pass" for action in blind["legal_actions"]), (
        "用户验收：盲挑步不可退回，超时取第一个合法项"
    )
    first = fallback_action(session.pending)
    assert isinstance(first, SelectCards) and first.picks == (0,), (
        f"盲挑超时 = 面内第一张手牌：{first}"
    )


def test_suit_labels_cover_the_four_suits_and_fail_closed_on_mixed_options() -> None:
    """票 08（D10）：花色标签是**纯展示派生**，四种齐全、红黑由颜色真源派生。

    非花色选项 / 混进非花色的闭集一律 ``None``——不给出半份标签让前端去补齐（那样前端
    就会长出第二份表）。
    """
    assert SUIT_LABELS == {
        "heart": {"name": "红桃", "symbol": "♥", "color": "red"},
        "diamond": {"name": "方块", "symbol": "♦", "color": "red"},
        "spade": {"name": "黑桃", "symbol": "♠", "color": "black"},
        "club": {"name": "梅花", "symbol": "♣", "color": "black"},
    }
    assert suit_labels(("heart", "diamond", "spade", "club")) == SUIT_LABELS
    assert suit_labels(("heart", "spade")) == {
        "heart": SUIT_LABELS["heart"],
        "spade": SUIT_LABELS["spade"],
    }
    assert suit_labels(("discard_one", "draw_one")) is None
    assert suit_labels(("heart", "discard_one")) is None, "混入非花色 ⇒ 整组不翻"
    assert suit_labels(()) is None
    assert suit_labels(None) is None



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
                # `legal_actions` 保持可回传（不带 display_*），`candidate_index(query)`
                # 是未装饰的纯索引 ⇒ 两边都是牌面事实，可以直接比。展示字段的补充由
                # `pending_payload` 单独做（`test_the_pending_payload_carries_display_fields...`）。

                # ② 候选 ⊆ 索引：每条带牌的候选都在索引里，且挂在**自己那张牌**下。
                #    重铸是独立 Action（M5-08b / D6），没有 targets，不贡献 combos。
                for action in payload["legal_actions"]:
                    card = action.get("card")
                    if card is None:
                        continue
                    entry = index.get(str(card["card_id"]))
                    assert entry is not None, f"{action['kind']} 的牌不在候选索引里"
                    assert entry["card"] == card, "索引里的牌面必须与候选里的是同一张"
                    if action["kind"] == "Reforge":
                        continue
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


def test_the_candidate_index_for_a_halberd_borrowed_window_keeps_bn_in_every_combo() -> None:
    """W7-01：借刀窗口（方天 + 最后一张手牌【杀】）的索引 = 1..3 目标，且 combo 恒含 Bn。

    界面据 ``min_targets`` / ``max_targets`` 说"还要选几个"、据 ``combos`` 判提交可否；
    Bn **不需要**第二个协议字段：它已经写在每一组组合里（W7 spec §3.6 / 评审 S10）。
    """
    from sgs.effects import legal_actions_for
    from sgs.state import QueryKind, RespondContext, RespondMode

    pack = _context_pack()
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    halberd = cards_of_type(pack, "halberd")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(halberd,)),
            SeatSpec(),
            SeatSpec(),
        ],
        turn_seat=0,
    )
    query = RespondQuery(
        seq=1,
        actor=1,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        prompt="【借刀杀人】：对 1 号位使用【杀】，或交出武器。",
        legal_actions=legal_actions_for(
            state,
            1,
            QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BORROWED_SWORD,
                required="slash",
                mode=RespondMode.USE,
                use_targets=(0,),
            ),
        ),
        view=project(state, 1),
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        target=1,
    )

    entry = candidate_index(query)[str(slash)]

    assert entry["min_targets"] == 1, "额定 1 目标"
    assert entry["max_targets"] == 3, "方天在最后一张手牌时放宽到 3"
    assert all(0 in combo for combo in entry["combos"]), "每一组组合都含 Bn（借刀指定的目标）"
    assert {len(combo) for combo in entry["combos"]} == {1, 2, 3}, "1 / 2 / 3 目标的组合都在"
    assert entry["pool"] == [0, 2, 3], "池是完整的 check_targets 池（含额外目标）"


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
    """票 05 / W4-2：【仁德】走预发动清单 ⇒ 界面**没有**手选模式（``ui.manual`` 已删除）。

    "多选手牌 → 点武将"由清单的两步表达；可用性来自 ``preactivations``（这条合成询问
    没带清单 ⇒ 退回骨架候选的成员判定）。
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
    assert entry["preactivated"] is True, "已翻预发动：界面走清单入口"
    assert "manual" not in entry["ui"], "手选模式已删除（没有生产者）"
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
    assert context["requirement"] == "选择将这些牌交给哪名角色", "合成键要能查回技能定义"


def test_the_rejudge_window_links_guicai_by_the_shared_requirement_text() -> None:
    """U2-04：提示区技能组靠「这一步的 requirement = 技能声明的 requirement」认出【鬼才】。

    改判窗口是 ``RESPOND`` / ``REJUDGE``：候选是手牌的 ``PlayCard``（没有
    ``ActivateSkill``），故底部技能区的 ``available`` 判据看不见它。前端因此只做一次
    **服务端字符串相等**——两条都是声明面的"需要做什么"。这个测试走**真的判定帧**
    （座位 0 的【乐不思蜀】判定 → 座位 1 的司马懿被问改判），把那份相等钉住：改任一句
    都红，提示区的鬼才入口不会静默消失（它不是合法性判据）。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    indulgence = cards_of_type(pack, "indulgence")[0]
    hand = cards_of_type(pack, "slash")[0]
    judge_card = next(
        card_id for card_id in range(len(pack.deck.cards)) if card_id not in {indulgence, hand}
    )
    deck = (
        judge_card,
        *(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {indulgence, hand, judge_card}
        ),
    )
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hp=4, judge=(indulgence,)), SeatSpec(hp=4, hero="simayi", hand=(hand,))],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    def reached(engine) -> bool:
        query = engine.pending
        return (
            query is not None
            and query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.REJUDGE
        )

    run_queries(engine, [PassPlayer(), PassPlayer()], reached)
    query = engine.pending
    assert query is not None and query.actor == 1, "改判询问弹给有能力的座位（司马懿）"
    context = query_context(engine, query)
    skills = {
        entry["skill_id"]: entry
        for entry in build_skills(SimpleNamespace(engine=engine, pending=query), 1)
    }

    assert context is not None
    assert {action.kind for action in query.legal_actions} == {"PlayCard", "Pass"}, (
        "改判窗口的候选是手牌 PlayCard + Pass（没有 ActivateSkill）"
    )
    guicai = skills["hero.simayi.guicai"]
    assert guicai["available"] is False, "没有 ActivateSkill ⇒ 底部技能区的 available 是假"
    assert guicai["ui"]["passive"] is False, "鬼才不是触发技 / 锁定技"
    assert context["requirement"], "改判窗口要有『需要做什么』的文案"
    assert guicai["ui"]["requirement"] == context["requirement"], (
        "提示区鬼才入口的判据：窗口 requirement 与技能声明 requirement 是同一句"
    )


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


# ---------------------------------------------------------------------------
# W6-03：观星排列的 count / split 由**投影**读 SkillDef.deck_arrange 下发（spec S2）
#
# 写方 = 技能声明（与 `Arrange` 步**同一个** `DeckTopArrange` 对象）；读方 = `app.js`
# 的双行排列面（`pending.arrange`）。`ARRANGE` 询问自己只带 `revealed`（`ArrangeContext`
# 被 `_build_query` 丢掉、不进记录流），所以规格只能由接入层补。
# ---------------------------------------------------------------------------

GUANXING = "hero.zhugeliang.guanxing"


def _session_at_guanxing_arrange() -> Session:
    """真会话：4 人局、人类座位 0 号位诸葛亮；种子 7 的第一问就是「是否发动【观星】」。

    答发动 ⇒ 引擎停在 ``ARRANGE``（X = min(存活 4, 5) = 4）。这条路径不碰引擎：只是
    把人类座位的应答送回，然后读投影。
    """
    session = Session(
        seed=7,
        ai="random",
        timeout_seconds=None,
        player_count=4,
        human_seat=0,
        my_hero="zhugeliang",
    )
    query = session.pending
    assert query is not None and query.kind == "SKILL_ACTIVATION", (
        f"种子 7 的 4 人局第一问应是技能发动（实际 {getattr(query, 'kind', None)}）"
    )
    assert int(query.actor) == 0 and int(session.human_seat) == 0
    assert query.skill_id == GUANXING
    activate = next(
        action
        for action in query.legal_actions
        if action.kind == "ActivateSkill" and action.skill_id == GUANXING
    )
    session.submit_answer(activate)
    pending = session.pending
    assert pending is not None and pending.kind == "ARRANGE"
    assert len(pending.revealed) == 4, "X = min(存活 4, 5) = 4"
    return session


def test_the_pending_payload_carries_the_guanxing_arrange_spec() -> None:
    """S2：``pending_payload['arrange']`` = 投影从 ``SkillDef.deck_arrange`` 读出的
    张数 / 切分；牌面**只下发一次**（``payload['revealed']``），规格里不再重发。

    W6-07 / 评审 J9：``arrange.cards`` 已删 —— 同一张列表序列化两次会让"哪一份是真源"
    变含混，前端读 ``pending.revealed`` 即可。
    """
    session = _session_at_guanxing_arrange()
    query = session.pending

    payload = pending_payload(session)

    assert payload is not None
    spec = payload["arrange"]
    assert spec is not None, "观星的 ARRANGE 必须下发排列规格（否则前端只能猜）"
    assert spec["count"] == len(query.revealed) == 4
    assert spec["split"] == FREE_SPLIT, "观星 = 自由切分（DeckTopArrange 的缺省档）"
    assert "cards" not in spec, f"牌面只在 revealed 里下发一次（实际键 = {sorted(spec)}）"
    assert [card["card_id"] for card in payload["revealed"]] == [
        card.card_id for card in query.revealed
    ], "revealed 就是询问授予的牌面（唯一真源）"

    # 规格是**投影派生**：询问模型里没有 arrange / count / split 三个顶层字段。
    record = next(
        record
        for record in reversed(session.engine.records)
        if record.kind == "query" and record.query is query
    )
    dumped = record.query.model_dump(mode="json")
    assert "arrange" not in dumped and "count" not in dumped and "split" not in dumped, (
        f"排列规格不得进记录流：顶层键 = {sorted(dumped)}"
    )


def test_the_arrange_spec_reports_the_skill_definition_split_and_fails_closed(monkeypatch) -> None:
    """``fixed`` 档原样下发声明；规格缺失 / 张数对不上 ⇒ ``None``（不猜、不静默截断）。

    ``CATALOG`` 是只读 ``MappingProxyType``，故在投影的**定义查询**这一层换一份
    ``SkillDef``（真实会话 + 真实询问不变，只替换声明的 ``deck_arrange``）。
    """
    from dataclasses import replace

    import sgs_web.projection as projection
    from sgs.skills import CATALOG
    from sgs.skills.flow import DeckTopArrange, FixedSplit

    session = _session_at_guanxing_arrange()
    definition = CATALOG[GUANXING]

    def use(spec: object) -> None:
        replaced = replace(definition, deck_arrange=spec)
        monkeypatch.setattr(projection, "_definition_of", lambda _state, _skill: replaced)

    use(DeckTopArrange(count=4, split=FixedSplit(2, 2)))
    fixed = pending_payload(session)["arrange"]
    assert fixed is not None
    assert fixed["split"] == {"top": 2, "bottom": 2}, "fixed 档原样下发声明，不猜切分"

    use(DeckTopArrange(count=5, split=FixedSplit(2, 3)))
    assert pending_payload(session)["arrange"] is None, (
        "声明张数（5）与实际牌数（4）对不上 ⇒ fail-closed"
    )

    use(None)
    assert pending_payload(session)["arrange"] is None, (
        "没有 deck_arrange 的技能 ⇒ 投影不凭空造规格"
    )


def test_the_arrange_spec_evaluates_a_callable_count_and_fails_closed(monkeypatch) -> None:
    """W6-07（评审 SPEC-a）：**可调用**张数也要与实际牌数比对，对不上就 ``None``。

    旧实现只比 ``isinstance(declared, int)``，而唯一消费者观星的 ``count`` 是
    ``_guanxing_count``（可调用）⇒ 那条分支在生产里从未被执行过（旧测试靠 monkeypatch
    一个 int 才走到）。这条用可调用档重新钉住它，并单独钉"求值抛异常 ⇒ 不猜"。
    """
    from dataclasses import replace

    import sgs_web.projection as projection
    from sgs.skills import CATALOG
    from sgs.skills.flow import DeckTopArrange, FixedSplit

    session = _session_at_guanxing_arrange()
    definition = CATALOG[GUANXING]
    seen: list[tuple[object, object]] = []

    def use(spec: object) -> None:
        replaced = replace(definition, deck_arrange=spec)
        monkeypatch.setattr(projection, "_definition_of", lambda _state, _skill: replaced)

    def counted(value: int):
        def count(state: object, frame: object) -> int:
            seen.append((state, frame))
            return value

        return count

    # 可调用档：与本次实际牌数（4）一致 ⇒ 下发；投影把 state 与**当前帧**都喂给声明。
    use(DeckTopArrange(count=counted(4)))
    assert pending_payload(session)["arrange"] == {"count": 4, "split": FREE_SPLIT}
    assert seen, "可调用张数必须被求值（否则比对整段失效）"
    assert seen[-1][0] is session.engine.state, "求值喂的是引擎状态"
    assert seen[-1][1] is None, (
        "阶段体的排列询问不压栈 ⇒ 当前帧就是 None（声明自己决定用不用）"
    )
    # 有帧时按**栈顶**原样转发（纯函数直测；上面那条真实会话恰好是空栈）。
    sentinel = object()
    known, value = projection._declared_arrange_count(
        lambda state, frame: 7 if (state is session.engine.state and frame is sentinel) else 0,
        session.engine.state,
        sentinel,
    )
    assert (known, value) == (True, 7), "求值把 state 与栈顶帧原样喂给声明"

    # 可调用档与牌数对不上 ⇒ fail-closed（旧实现这里会放行）。
    use(DeckTopArrange(count=counted(5)))
    assert pending_payload(session)["arrange"] is None, (
        "可调用张数（5）与实际牌数（4）对不上 ⇒ fail-closed，不拿声明猜"
    )

    def broken(_state: object, _frame: object) -> int:
        raise KeyError("frame context")

    use(DeckTopArrange(count=broken))
    assert pending_payload(session)["arrange"] is None, "求值抛异常 ⇒ 判定不了 ⇒ fail-closed"

    # 固定切分的两行必须恰好排完这几张牌（评审 J6）：3 + 2 != 4 ⇒ 不下发。
    use(DeckTopArrange(count=counted(4), split=FixedSplit(3, 2)))
    assert pending_payload(session)["arrange"] is None, (
        "top + bottom != count ⇒ 投影 fail-closed（错误切分不进 _arrange 的 ValueError）"
    )


def test_the_409_bare_query_does_not_get_an_arrange_spec() -> None:
    """409 附带的是**裸 ``Query``**（拿不到引擎）：不能猜规格，前端显示「排列不可用」。"""
    session = _session_at_guanxing_arrange()
    query = session.pending
    stale = query.legal_actions[0].model_copy(update={"answers_seq": -1})

    with pytest.raises(StaleAnswer) as info:
        session.submit_answer(stale)
    error = info.value
    assert error.pending is query, "409 附带的就是引擎当前挂起（原样）"

    payload = pending_payload(error.pending, human_seat=error.human_seat)

    assert payload is not None
    assert payload["arrange"] is None, (
        "没有引擎 ⇒ 没有 SkillDef.deck_arrange ⇒ 投影不得凭空造一份排列规格"
    )


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


def test_a_160_profile_session_replays_as_standard_ex_160(tmp_path: Path) -> None:
    """M5-01b：160 局存档后从侧车重建仍是 160 档，不回落内容包默认档。"""
    pack = load_content_pack(deck_profile="standard_ex_160")
    session = _finished_session(seed=83, pack=pack)
    save_run(tmp_path, session)
    name = next(tmp_path.glob("*.jsonl")).name[: -len(".jsonl")]

    meta = load_run(tmp_path, name)["meta"]
    assert meta["pack_id"] == "standard"
    assert meta["deck_profile"] == "standard_ex_160"

    replayed = replay_journal(meta)
    assert replayed[0].deck_profile == "standard_ex_160", "重放不得静默回落到 standard_108"
    assert len(load_content_pack(meta["pack_id"], deck_profile=meta["deck_profile"]).deck.cards) == 160
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


def test_the_replay_api_carries_the_queries_and_zone_moves_the_page_draws(tmp_path: Path) -> None:
    """W4-6：回放页的询问面板 / 手牌 / 装备 / 判定 / 处理区**只能**从这份 API 读。

    记录流是**全知**的（上帝视角），故回放页不需要第二份投影：``query`` 记录带询问文案与
    候选，``card_move`` 记录带 ``from_zone`` / ``to_zone``，手牌与装备 / 判定 / 处理区都能
    由增量重建。这里钉的是"API 不把这些字段裁掉"——裁掉时页面会静默变成空壳。
    """
    session = _finished_session(seed=1)
    save_run(tmp_path, session)
    name = next(tmp_path.glob("*.jsonl")).name[: -len(".jsonl")]

    loaded = load_run(tmp_path, name)
    records = loaded["records"]

    queries = [record["query"] for record in records if record["kind"] == "query"]
    assert queries, "回放页要画询问面板，记录流里必须有 query 记录"
    assert any(query["prompt"] for query in queries), "询问记录必须带文案（回放页直接显示它）"
    assert any(query["legal_actions"] for query in queries), "询问记录必须带候选（回放页列出它们）"
    assert all(query["view"]["players"] for query in queries), (
        "询问记录必须带逐座位公开事实（回放页的体力 / 存活初值读它）"
    )
    assert any(query.get("slots") for query in queries), (
        "选择面询问必须带 slots（回放页画槽位与区域）"
    )

    moves = [record for record in records if record.get("delta_type") == "card_move"]
    zones = {
        zone
        for record in moves
        for zone in (record["from_zone"]["zone"], record["to_zone"]["zone"])
    }
    assert {"hand", "equip", "judge", "processing"} <= zones, (
        f"回放页要画手牌 / 装备 / 判定 / 处理区，记录流里的牌移动必须覆盖这些区；实得 {sorted(zones)}"
    )

    assert loaded["meta"]["player_count"] == session.rule_config.player_count
    assert loaded["meta"]["human_seat"] == session.human_seat, "回放页靠它标出「本局人类」"


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
            "preactivated",
            "description",
            "ui",
        }
        assert entry["description"], "U1-04b：每条技能都要带服务端下发的全文"
        assert isinstance(entry["locked"], bool)
        assert isinstance(entry["available"], bool)
        assert isinstance(entry["preactivated"], bool)
        assert entry["skill_type"] in ("NORMAL", "LORD")
        assert entry["carrier"] in ("HERO", "EQUIPMENT")
        # `ui` 是**表现层契约**：改键名必须同时改 app.js 的读法（同候选索引的姿态）。
        assert set(entry["ui"]) == {
            "requirement",
            "passive",
            "convert_sources",
        }, f"技能界面提示的键集合变了：{sorted(entry['ui'])}"
        assert isinstance(entry["ui"]["passive"], bool)
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


def test_every_seat_carries_the_public_skill_list_for_the_detail_window() -> None:
    """U1-06：详情模态对**每个**座位都要有武将技能 / 装备技能。

    武将牌是公开信息（`PlayerPublic.hero` 的 docstring），故详情里的技能清单也可以对
    所有座位下发；它是**只读展示**清单，不带 `available` / `preactivated` /
    `ui.convert_sources`——那三样读的是**当前询问与本人手牌**，对别人就是暗置信息。
    """
    session = Session(seed=20261012, ai="heuristic", timeout_seconds=None, player_count=4)
    snapshot = visible_snapshot(session)

    assert len(snapshot["players"]) == 4
    for payload, player in zip(snapshot["players"], session.engine.state.players, strict=True):
        skills = payload["skills"]
        operable = {entry["skill_id"]: entry for entry in build_skills(session, player.seat)}
        assert skills, f"座位 {player.seat} 的详情没有技能"
        assert {skill["skill_id"] for skill in skills} == set(operable), (
            f"座位 {player.seat} 的详情清单与可操作清单的技能 id 必须一致"
        )
        for skill in skills:
            assert skill["name"] == operable[skill["skill_id"]]["name"], (
                f"{skill['skill_id']} 的名字在两个清单里漂了"
            )
            assert skill["locked"] == operable[skill["skill_id"]]["locked"]
            assert skill["carrier"] == operable[skill["skill_id"]]["carrier"]
        assert [skill["carrier"] for skill in skills] == ["HERO"] * len(skills)
        for skill in skills:
            assert set(skill) == {
                "skill_id",
                "name",
                "skill_type",
                "locked",
                "carrier",
                "description",
                "ui",
            }, f"详情技能的键集合变了：{sorted(skill)}"
            assert set(skill["ui"]) == {"passive"}
            assert skill["description"] != "暂无描述", f"{skill['skill_id']} 没有描述"


def test_the_detail_skill_list_puts_hero_skills_before_equipment_and_keeps_virtual_grants() -> None:
    """U1-06：「装备技能」= 装备区 + 虚拟装备；顺序武将在前、装备在后。

    合成局面（`build_state`）里给关羽一张【诸葛连弩】+ 一条虚拟【八阵】授予：
    详情清单必须是「武将技能 → 实体装备技 → 虚拟装备技」，且名字取装备牌名。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    crossbow = cards_of_type(pack, "crossbow")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(
                hero="guanyu",
                equip=(crossbow,),
                virtual_equipment=("equip.eight_diagrams",),
            ),
            SeatSpec(hero="sunquan"),
        ],
    )
    engine = Engine.from_state(state, seed=1)

    entries = seat_skill_display(SimpleNamespace(engine=engine), 0)

    assert [entry["skill_id"] for entry in entries] == [
        "hero.guanyu.wusheng",
        "equip.crossbow",
        "equip.eight_diagrams",
    ], "详情必须武将技能在前、装备技能（含虚拟授予）在后"
    assert [entry["carrier"] for entry in entries] == ["HERO", "EQUIPMENT", "EQUIPMENT"]
    by_id = {entry["skill_id"]: entry for entry in entries}
    assert by_id["equip.crossbow"]["name"] == "诸葛连弩"
    assert by_id["equip.crossbow"]["locked"] is True
    assert by_id["equip.eight_diagrams"]["name"] == "八卦阵"
    assert by_id["equip.eight_diagrams"]["description"] == skill_description(
        "equip.eight_diagrams"
    )
    # 名字 / 锁定 / 载体与可操作清单同源（两张清单允许字段不同，但公开事实不能漂）。
    operable = {
        entry["skill_id"]: entry
        for entry in build_skills(SimpleNamespace(engine=engine, pending=None), 0)
    }
    for skill_id, entry in by_id.items():
        assert entry["name"] == operable[skill_id]["name"]
        assert entry["locked"] == operable[skill_id]["locked"]
        assert entry["carrier"] == operable[skill_id]["carrier"]


def test_the_actionable_skill_list_puts_hero_skills_before_equipment() -> None:
    """技能按钮区（U1-06）的排序：武将技能在前、装备技能在后，同组内沿用原稳定键。"""
    from types import SimpleNamespace

    pack = _context_pack()
    crossbow = cards_of_type(pack, "crossbow")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero="guanyu", equip=(crossbow,), virtual_equipment=("equip.eight_diagrams",)),
            SeatSpec(hero="sunquan"),
        ],
    )
    engine = Engine.from_state(state, seed=1)

    entries = build_skills(SimpleNamespace(engine=engine, pending=None), 0)

    carriers = [entry["carrier"] for entry in entries]
    assert carriers[0] == "HERO", f"武将技能必须排在最前：{carriers}"
    assert set(carriers[1:]) == {"EQUIPMENT"}, f"装备技能必须排在武将技能之后：{carriers}"
    assert len(entries) == 3


def test_a_dead_hero_still_carries_its_hero_skills_in_the_detail_list() -> None:
    """U1-06：阵亡武将也要能点开详情，故详情清单不能复用「存活实例」那条路。

    ``resolve_instances`` 跳过死者是**派发**口径（死人不能发动技能）；详情读的是
    公开武将面，故与生死无关。这里同时钉住两条口径的差：可操作清单为空，详情不为空。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hero="guanyu", alive=False, hp=0), SeatSpec(hero="sunquan")],
    )
    engine = Engine.from_state(state, seed=1)

    entries = seat_skill_display(SimpleNamespace(engine=engine), 0)

    assert [entry["skill_id"] for entry in entries] == ["hero.guanyu.wusheng"]
    assert entries[0]["name"] == "武圣"
    assert build_skills(SimpleNamespace(engine=engine, pending=None), 0) == [], (
        "可操作清单读存活实例：死者为空；详情若复用它就会开出一张空窗口"
    )

    # 读取方走**真实产出路径**（CODING_STANDARDS §1/§6）：`visible_snapshot` 才是
    # `players[].skills` 的写入者；只测裸 helper 不能证明详情拿到的是这一份。
    snapshot = visible_snapshot(SimpleNamespace(engine=engine, human_seat=0, pack=pack))
    assert [entry["skill_id"] for entry in snapshot["players"][0]["skills"]] == [
        "hero.guanyu.wusheng"
    ], "阵亡武将的详情清单没有从 visible_snapshot 下发到前端"



def test_the_public_detail_list_applies_the_lord_skill_gate() -> None:
    """详情清单也要过主公技门控：非主公的武将牌上看不到主公技。

    与 `build_skills` 的 `resolve_instances` 门控同一口径，但详情读的是公开武将面
    （死者也要列），故门控必须在这里显式再核一次。
    """
    from types import SimpleNamespace

    pack = _context_pack()
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero="caocao", identity=Identity.LOYALIST),
            SeatSpec(hero="liubei", identity=Identity.LORD),
        ],
    )
    engine = Engine.from_state(state, seed=1)
    session = SimpleNamespace(engine=engine)

    loyalist = {entry["skill_id"] for entry in seat_skill_display(session, 0)}
    lord = {entry["skill_id"] for entry in seat_skill_display(session, 1)}

    assert "hero.caocao.jianxiong" in loyalist
    assert "hero.caocao.hujia" not in loyalist, "非主公不该看到主公技"
    assert "hero.liubei.jijiang" in lord


def test_skills_never_leak_another_seats_inventory() -> None:
    """**可操作**清单只列该座位自己的实例：别座位的可用性 / 转化来源不下发。

    武将详情另有一份**公开只读**清单（`seat_skill_display`，只含武将牌 / 装备区，
    不带可用性）；两者不是同一个面，别把这条测试当成"对手技能完全不下发"。
    """
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
    """`available` 只由**当前挂起**回答：候选里的骨架 ∪ 随询问下发的两份清单。

    W4-fix-2：转化技的候选已从 ``legal_actions`` 收回，可用性改由
    ``transform_preactivations`` 表达——它同样是"当前挂起里的那份数据"，不另问状态。
    """
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
        offered.update((getattr(pending, "preactivations", None) or {}).keys())
        offered.update((getattr(pending, "transform_preactivations", None) or {}).keys())
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
    """【离间】三步各有各的问法：先"弃置1张牌"，再分别"决斗使用者 / 决斗目标"。

    步 1 是 ``SELECT_CARDS``（询问自带不了步骤名），步 2/3 是 ``SELECT_OPTION``（步骤名在
    ``skill_id`` 冒号后）。两处都只读**询问里已有的标识** ⇒ 询问协议与 Golden Replay 不动。

    W4-fix-3：步 2/3 不再逐字相同（旧文案两键一模一样，界面分不出谁是使用者）——使用者
    与目标各自写明。
    """
    engine, state = _context_fixture(
        [SeatSpec(hero="diaochan"), SeatSpec(), SeatSpec()],
        frame_kind=FrameKind.SKILL,
        frame_data={"skill_id": "hero.diaochan.lijian", "source": 0},
    )

    cards_query = _skill_step_cards_query(
        state, zones=(Zone.HAND, Zone.EQUIP), optional=True
    )
    assert query_context(engine, cards_query)["requirement"] == (
        "弃置1张牌（手牌 / 装备区）"
    )

    wording = {}
    for step in ("user", "victim"):
        option_query = _skill_step_option_query(
            state, skill_id=f"lijian:{step}", options=("seat:1", "seat:2")
        )
        wording[step] = query_context(engine, option_query)["requirement"]
    assert "使用者" in wording["user"], f"步 2 的问法：{wording['user']!r}"
    assert "目标" in wording["victim"], f"步 3 的问法：{wording['victim']!r}"
    assert wording["user"] != wording["victim"], "两问的文案必须能分辨"


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

    U2-13：寒冰剑从"至多两张"改为**两张**——轮内没有收手出口（``optional=False``、
    两轮强制），文案必须与规则一致；这是同一批改动的落点，不是回归。
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
        "依次弃置目标的两张牌（手牌 / 装备区）"
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
    """【遗计】分配两步各有各的问法（票 11 / 票 15）：先"从亮出的牌里选择一至两张"，再"给谁"。

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

    cards_query = _skill_step_cards_query(state, zones=(Zone.HAND,), optional=False)
    assert query_context(engine, cards_query)["requirement"] == "从亮出的牌里选择一至两张"

    give_query = _skill_step_option_query(state, skill_id="yiji:give:0:1", options=("seat:1",))
    assert query_context(engine, give_query)["requirement"] == "选择将这些牌交给哪名角色"


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
      （summary 是 "观看牌堆顶两张牌，然后交给任意角色"）。
    """
    session = _yiji_real_session()
    query = session.pending

    assert query is not None and query.kind == "SELECT_CARDS"
    assert query.skill_id is None, "【遗计】选牌步的询问不带 skill_id（真实形状）"
    data = session.engine.stack.frames[-1].data
    assert data.get("running_skill") == "hero.guojia.yiji"
    assert "skill_id" not in data, "被动帧从不写 skill_id —— 这正是旧判据读不到的原因"

    # 载荷这一段的断言（dialog / 不可放弃 / 超时文案）要以**挂起者本人**的视角看：默认的
    # 人类座位 0 不是挂起者，那份载荷会把亮牌与弹窗提示一起抹掉（票 15 的私密收口）。
    session.human_seat = int(query.actor)
    payload = pending_payload(session)

    assert payload is not None and payload["context"] is not None
    assert payload["context"]["requirement"] == "从亮出的牌里选择一至两张", (
        "读取方必须真的读到分步文案；退回一句话 summary 就说明判据又读错了键"
    )
    # U2-12：这一步的表面是**亮牌弹窗**，由服务端以**形状**标记（SELECT_CARDS + 授予牌面），
    # 前端不抄技能 id。同一份载荷还钉了「不可放弃」与超时文案。
    assert payload["dialog"] == "reveal"
    assert all(action["kind"] != "Pass" for action in payload["legal_actions"]), (
        "选牌步不可放弃（U2-12 / D9.1）：界面因此没有『放弃』键"
    )
    assert payload["spec"]["optional"] is False
    assert payload["prompt_display"]["default_label"] == "超时将自动选择第一张亮牌"


def test_the_watched_cards_are_private_to_the_pending_seat() -> None:
    """**私密观看**（票 15）：看牌堆顶的牌面只在**挂起者本人**的那份载荷里。

    用户口径（`acceptance.md` §C）："仅此技能拥有者观看，其他人不可见"。引擎侧已经保证
    观看不写 ``PlayerView``、不落带牌面的 delta（见 `test_hero_damage_after.py` 的视角判据）；
    接入层这条"当前挂起"的下发是**读取方**，同一处也要成立：收 ``Session`` 时，挂起者
    不是人类座位 ⇒ ``slots[].card`` / ``revealed`` 必须抹掉（否则任何"人类不是挂起者"的
    下发——陈旧应答的 409 现状等——都会把别人观看的牌面直接送出去）。

    **正反两面都断**（`CODING_STANDARDS.md` §6）：把 ``human_seat`` 换成挂起者本人时，
    同一个载荷必须**带着**牌面——否则"恒抹掉"也会让这条测试变绿，而私密观看就退化成了
    "谁都不能看"。
    """
    session = _yiji_real_session()
    query = session.pending
    assert query is not None and query.kind == "SELECT_CARDS"
    assert query.revealed, "【遗计】的选牌询问带着观看授予（前置）"
    watched = {card.card_id for card in query.revealed}

    stranger = pending_payload(session)
    assert stranger is not None, "人类座位 0 不是挂起者，但载荷仍要给（只是不带牌面）"
    assert stranger["actor"] == 1, "这条询问的挂起者是座位 1（郭嘉）"
    assert len(stranger["slots"]) == len(watched), "位置（有几个）是公开事实，照给"
    assert all(slot["card"] is None for slot in stranger["slots"]), (
        f"别人的载荷里不得出现观看的牌面：{stranger['slots']}"
    )
    assert stranger["revealed"] == [], "观看授予本身就是牌面，别人的载荷里必须为空"
    assert stranger["dialog"] is None, (
        "亮牌弹窗提示也只发给挂起者本人：别人的屏幕连『这是私密亮牌』都不用知道"
    )

    # 反向对照：把人类座位换成挂起者本人 ⇒ 同一个载荷**必须**带牌面。
    session.human_seat = 1
    owner = pending_payload(session)
    assert owner is not None
    assert owner["dialog"] == "reveal", "持有者本人这一步走亮牌弹窗"
    assert {slot["card"]["card_id"] for slot in owner["slots"] if slot["card"]} == watched, (
        "持有者本人必须看得到那两张牌（否则'私密'变成了'谁都不能看'）"
    )
    assert {card["card_id"] for card in owner["revealed"]} == watched


def test_the_409_path_does_not_hand_the_watched_faces_to_a_query_without_a_viewer() -> None:
    """评审 finding ①：票 15 的**私密观看**在 409 那条**裸 ``Query``** 路径上也必须判归属。

    ``serve.py`` 的 409 分支把 :attr:`StaleAnswer.pending`（一份**裸 ``Query``**，不带
    ``session``）交给 :func:`pending_payload`。旧签发把裸 ``Query`` 当"构造时本就只为人类
    座位"**原样放行**，而"当前挂起者是人类座位"这条判据只写在收 ``Session`` 的分支里 ——
    于是两张观看牌面随 409 直接下发（本例在修复前实测泄露：桃园结义 / 万箭齐发）。

    这里走**产品的 409 机制本身**：真 :meth:`Session.submit_answer` 因序号过期抛
    :class:`StaleAnswer`，其 ``pending`` 就是引擎的当前挂起（带真【遗计】的 ``revealed``）。
    拿不到查看者（裸 ``Query``）时**默认抹掉**（fail-closed）；拿得到且与挂起者同座时才带
    牌面 —— 正反两面都在这里断，免得"恒抹掉"也能变绿。
    """
    session = _yiji_real_session()
    query = session.pending
    assert query is not None and query.kind == "SELECT_CARDS"
    assert int(query.actor) != int(session.human_seat), "前置：挂起者不是人类座位（0）"
    watched = {card.card_id for card in query.revealed}
    assert watched, "【遗计】的选牌询问带着观看授予（前置）"
    stale = query.legal_actions[0].model_copy(update={"answers_seq": -1})

    with pytest.raises(StaleAnswer) as info:
        session.submit_answer(stale)
    error = info.value
    assert error.pending is query, "409 附带的就是引擎当前挂起（原样）"

    payload = pending_payload(error.pending)
    assert payload is not None
    assert len(payload["slots"]) == len(watched), "位置（有几个）是公开事实，照给"
    assert all(slot["card"] is None for slot in payload["slots"]), (
        f"裸 Query（409 路径）拿不到查看者 ⇒ 默认不得带牌面：{payload['slots']}"
    )
    assert payload["revealed"] == [], "观看授予本身就是牌面，拿不到查看者时须为空"
    assert payload["dialog"] is None, "拿不到查看者时也不下发亮牌弹窗提示（fail-closed）"

    # 产品调用点（``serve.py`` 的 409 分支）：查看者 = 这个会话的人类座位（0）≠ 挂起者（1）
    # ⇒ 照样抹掉。异常自己带着 human_seat，那条路径因此判得出归属。
    product = pending_payload(error.pending, human_seat=error.human_seat)
    assert product is not None
    assert all(slot["card"] is None for slot in product["slots"]), (
        "409 的下发方只需原样转交 StaleAnswer.human_seat，不得再漏牌面"
    )
    assert product["dialog"] is None, "非挂起者的 409 载荷同样不带亮牌弹窗提示"

    # 反向对照：把查看者声明成挂起者本人 ⇒ 同一份裸 Query 必须带牌面。
    owner = pending_payload(error.pending, human_seat=int(query.actor))
    assert owner is not None
    assert {slot["card"]["card_id"] for slot in owner["slots"] if slot["card"]} == watched, (
        "持有者本人必须看得到那两张牌（否则'私密'变成了'谁都不能看'）"
    )
    assert owner["dialog"] == "reveal", "持有者本人的 409 载荷照常开亮牌弹窗"


def test_the_hanging_payload_still_shows_the_watched_faces_to_the_owner() -> None:
    """反向（票 15 私密观看的**正常挂起路径**）：人类座位就是挂起者时，牌面必须照给。

    与上一条互补：那条钉"409 拿不到查看者 ⇒ 抹掉"，这条钉"拿得到且同座 ⇒ 不抹"。
    恒抹掉会让私密观看退化成"谁都不能看"。
    """
    session = _yiji_real_session()
    query = session.pending
    assert query is not None and query.kind == "SELECT_CARDS"
    watched = {card.card_id for card in query.revealed}
    session.human_seat = int(query.actor)  # 正常挂起：人类就是挂起者

    payload = pending_payload(session)
    assert payload is not None
    assert {slot["card"]["card_id"] for slot in payload["slots"] if slot["card"]} == watched
    assert {card["card_id"] for card in payload["revealed"]} == watched
    assert payload["dialog"] == "reveal", "正常挂起路径：持有者本人的选牌步走亮牌弹窗"


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


def test_every_product_pending_payload_call_names_its_viewer() -> None:
    """CODING_STANDARDS §6 的机械半（票 15 私密观看泄露的回归闸）。

    ``pending_payload`` 要知道"这是谁的挂起"才能决定抹不抹掉观看牌面。产品调用点要么传
    ``Session``（查看者从会话里读），要么**显式** ``human_seat=``；裸传一个 ``Query`` 等于把
    判定权交给默认值——票 15 的 409 路径（``serve.py`` 把 ``StaleAnswer.pending`` 原样交出去）
    就是这么把两张牌面吐出去的。测试自己当然可以用裸 ``Query`` 去钉 fail-closed 行为。
    """
    src = (REPO_ROOT / "tools" / "sgs_web" / "serve.py").read_text(encoding="utf-8")

    bad: list[str] = []
    for line in src.splitlines():
        code = line.split("#", 1)[0]
        if "pending_payload(" not in code or code.lstrip().startswith("def "):
            continue
        args = code.split("pending_payload(", 1)[1].strip()
        if "human_seat=" in args or args.startswith("session"):
            continue
        bad.append(code.strip())

    assert not bad, (
        "产品代码里的 pending_payload 调用没有声明查看者"
        "（裸 Query 会 fail-closed 抹掉牌面）：\n  " + "\n  ".join(bad)
    )


# ---------------------------------------------------------------------------
# W4-1：预发动清单走**真会话**下发（主动入口），取消是纯本地态、零 action
# ---------------------------------------------------------------------------

RENDE = "hero.liubei.rende"


def _session_at_rende_play_phase() -> Session:
    """真会话：人类座位 = 刘备，驱动到"出牌阶段且【仁德】有预发动清单"那一刻。"""
    session = Session(
        seed=20261012,
        ai="random",
        timeout_seconds=None,
        player_count=2,
        heroes=("liubei", "zhouyu"),
        human_seat=0,
    )
    for _ in range(400):
        pending = session.pending
        if pending is None:
            raise AssertionError("会话提前结束，没走到【仁德】的出牌阶段")
        if pending.kind == "PLAY_PHASE" and RENDE in pending.preactivations:
            return session
        session.submit_answer(fallback_action(pending))
    raise AssertionError("400 次应答内没走到【仁德】的出牌阶段")


def test_the_pending_payload_carries_the_play_phase_preactivations() -> None:
    """跨线契约：``preactivations`` 必须由 ``pending_payload`` 显式下发（读取方 = app.js）。"""
    session = _session_at_rende_play_phase()

    payload = pending_payload(session)

    assert payload is not None
    assert "preactivations" in payload and "preactivation" in payload, (
        "两个键恒在（没有清单时给空值），读取方不必先判键存在"
    )
    steps = payload["preactivations"][RENDE]
    assert [step["name"] for step in steps] == ["card", "seat"]
    assert [step["order"] for step in steps] == [0, 1]
    # W4-fix-3：`wording` = 声明上的 `step_requirements`（界面据此画这一问的人话）。
    assert set(steps[0]) == {"name", "zones", "min", "max", "pool", "order", "wording"}
    assert steps[0]["wording"] == "选择任意张要交给其他角色的手牌"
    assert steps[0]["zones"] == ["hand"]
    assert steps[0]["max"] >= steps[0]["min"] >= 1
    assert steps[1]["wording"] == "选择交给哪名其他角色"
    assert steps[1]["pool"][0]["options"], "选座位步的候选（seat:N）也下发了"
    assert payload["preactivation"] == [], "主动入口没有单份清单"


def test_the_preactivation_manifest_is_not_written_into_the_journal() -> None:
    """清单是**派生数据**：不进记录流，既有 Golden Replay 因此逐字节不变。"""
    session = _session_at_rende_play_phase()
    record = next(
        record
        for record in reversed(session.engine.records)
        if record.kind == "query" and record.query is session.pending
    )

    raw = record.query.model_dump(mode="json")

    assert "preactivations" not in raw and "preactivation" not in raw


def test_the_pending_payload_forwards_a_passive_activation_manifest() -> None:
    """W4-2：**被动入口**的单份清单也要由 ``pending_payload`` 下发。

    真实产出路径：``Engine._build_query(SKILL_ACTIVATION)`` 把
    ``SkillActivationContext.preactivation``（模型字段）写进询问；这里用**同一个询问模型**
    的形状验投影那一半（投影曾把询问字段读成另一个名字 ⇒ 清单恒空，前端永远进不了被动
    预发动）。读取方 = ``app.js:preactivationManifest``。
    """
    from sgs.state import SkillActivationQuery, Step, StepPool

    pack = _context_pack()
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(), hero="sunquan"), SeatSpec()],
        turn_seat=0,
    )
    step = Step(name="card", zones=(Zone.HAND,), min=1, max=1,
                pool=(StepPool(when=(), options=("7",)),), order=0)
    query = SkillActivationQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="是否发动该技能？",
        legal_actions=(),
        view=project(state, 0),
        skill_id="equip.green_dragon_blade",
        preactivation=(step,),
    )

    payload = pending_payload(query, human_seat=0)

    assert payload is not None
    assert payload["preactivation"] and payload["preactivation"][0]["name"] == "card"
    assert payload["preactivation"][0]["pool"][0]["options"] == ["7"]
    assert payload["preactivations"] == {}, "被动入口没有「技能 id → 清单」那张表"


def test_the_skill_entry_is_available_through_the_manifest_and_carries_the_switch() -> None:
    """W4-2：可用性只剩清单（骨架候选已删），且技能条目带上 `preactivated` 供界面分流。"""
    session = _session_at_rende_play_phase()

    entry = next(item for item in build_skills(session, 0) if item["skill_id"] == RENDE)

    assert entry["available"] is True
    assert entry["preactivated"] is True, "界面据这个键把入口切到清单驱动（读取方 = app.js）"
    assert not any(
        action.kind == "ActivateSkill" and action.skill_id == RENDE
        for action in session.pending.legal_actions
    ), "已翻预发动：不再以骨架候选表达可用性"


def test_a_foreign_viewer_does_not_get_the_actors_hand_from_the_manifest() -> None:
    """清单里的选牌候选 = **询问者**的手牌 id：换一个查看者就不能原样下发。"""
    session = _session_at_rende_play_phase()
    foreign = (session.human_seat + 1) % session.rule_config.player_count

    payload = pending_payload(session.pending, human_seat=foreign)

    steps = payload["preactivations"][RENDE]
    assert steps, "步骤形状（区域 / 张数 / 顺序）是公开的，仍然下发"
    assert all(not row["options"] for row in steps[0]["pool"]), "选牌候选被抹掉"
    assert all(not row["when"] for row in steps[1]["pool"]), (
        "座位步的 when 可能含被选中的牌 id，也必须抹掉；候选（seat:N）保留"
    )
    assert steps[1]["pool"][0]["options"], "公开的目标候选照常保留"


# ---------------------------------------------------------------------------
# W4-3：转化技的**本地入口清单**也要按 app.js 的键下发
#
# 写方 = 引擎的 ``_build_query``（``transform_preactivations``，`exclude` 不进记录流），
# 读方 = ``app.js:preactivationManifest`` → ``pending.preactivations[skill_id]``。
# 两侧各由这里的真产出路径与 ``ui_smoke`` 的合成载荷钉一次（`CODING_STANDARDS.md` §1）。
# ---------------------------------------------------------------------------

WUSHENG = "hero.guanyu.wusheng"
JIJIU = "hero.huatuo.jijiu"


def _session_at_wusheng_play_phase() -> Session:
    """真会话：人类座位 = 关羽，驱动到"出牌阶段且【武圣】有转化入口清单"那一刻。"""
    session = Session(
        seed=1,
        ai="random",
        timeout_seconds=None,
        player_count=2,
        heroes=("guanyu", "zhouyu"),
        human_seat=0,
    )
    for _ in range(200):
        pending = session.pending
        if pending is None:
            raise AssertionError("会话提前结束，没走到【武圣】的出牌阶段")
        if pending.kind == "PLAY_PHASE" and WUSHENG in pending.transform_preactivations:
            return session
        session.submit_answer(fallback_action(pending))
    raise AssertionError("200 次应答内没走到【武圣】的出牌阶段清单")


def test_the_pending_payload_forwards_the_transform_preactivation_manifest() -> None:
    """转化技的入口清单：来源步（红牌）+ 目标步（seat:N），按 app.js 的键下发。"""
    from sgs.data import color_of_suit, Color

    session = _session_at_wusheng_play_phase()
    state = session.engine.state
    player = state.player(session.human_seat)
    red_sources = {
        str(card_id)
        for card_id in (*player.hand, *player.equip)
        if color_of_suit(state.card(card_id).suit) is Color.RED
    }

    payload = pending_payload(session)

    assert payload is not None
    steps = payload["preactivations"][WUSHENG]
    assert [step["name"] for step in steps] == ["card", "seat"]
    assert [step["order"] for step in steps] == [0, 1]
    assert steps[0]["min"] == steps[0]["max"] == 1, "一张红牌"
    # ``zones`` 列的是**此刻这些来源所在的区**（清单从候选派生）；装备区的红牌同样会成为
    # 来源——那条由 ``tests/test_hero_transform.py`` 的国色 / 龙胆用例钉住。
    assert steps[0]["zones"] == ["hand"]
    assert set(steps[0]["pool"][0]["options"]) == red_sources, (
        f"来源候选 = 此刻的红牌（手牌 / 装备）：{steps[0]['pool'][0]['options']}"
    )
    assert steps[0]["pool"][0]["when"] == []
    assert steps[1]["pool"][0]["options"], "目标候选（seat:N）也下发了"


def test_the_pending_payload_forwards_a_multi_target_transform_manifest() -> None:
    """W7-01b 跨线契约：目标步 ``1..max``、逐来源组全部目标、``wording`` 都**原样转发**。

    写方 = ``conversion_preactivations``（本测试直接给一份与它同形的 ``Step``；多目标候选的
    生成归 W7-02），读方 = ``app.js`` 的清单渲染 + 座位多选。接入层不重算池、不改键。
    """
    pack = _context_pack()
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=tuple(range(len(pack.deck.cards))),
        turn_seat=0,
    )
    source = cards_of_type(pack, "dodge")[0]
    steps = (
        Step(
            name="card",
            zones=(Zone.HAND,),
            min=1,
            max=1,
            pool=(StepPool(when=(), options=(str(source),)),),
            order=0,
        ),
        Step(
            name="seat",
            min=1,
            max=3,
            pool=(
                StepPool(
                    when=(str(source),),
                    options=("seat:2", "seat:1", "seat:3"),
                ),
            ),
            order=1,
            wording="选择【杀】的目标",
        ),
    )
    query = PlayPhaseQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="出牌阶段：使用一张牌，或结束出牌阶段。",
        legal_actions=(),
        view=project(state, 0),
        transform_preactivations={"hero.zhaoyun.longdan": steps},
    )

    payload = pending_payload(query, human_seat=0)

    assert payload is not None
    forwarded = payload["preactivations"]["hero.zhaoyun.longdan"]
    assert forwarded == [step.model_dump(mode="json") for step in steps], (
        "接入层原样转发：不重算池、不改键"
    )
    target = forwarded[1]
    assert (target["min"], target["max"]) == (1, 3), "目标步 = 1..max"
    assert target["pool"] == [
        {"when": [str(source)], "options": ["seat:2", "seat:1", "seat:3"]}
    ], "逐来源组的全部目标（首项 = 候选排序的自然首条，借刀窗口里就是 Bn）"
    assert target["wording"] == "选择【杀】的目标", "Step.wording 原样下发"


# ---------------------------------------------------------------------------
# W7-02：方天 × 转化【杀】的接入层面（清单 1..3 原样转发 / 借刀窗口的候选索引）
# ---------------------------------------------------------------------------


def _halberd_wusheng_play_state():
    """4 人局：关羽手里只剩一张红牌、已装方天（自然局：方天自己也是红色来源）。"""
    pack = _context_pack()
    halberd = cards_of_type(pack, "halberd")[0]
    red = next(
        card_id
        for card_id in cards_of_type(pack, "dodge")
        if pack.deck.cards[card_id].suit in (Suit.HEART, Suit.DIAMOND)
    )
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(red,), equip=(halberd,), hero="guanyu"),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {red, halberd}
        ),
        turn_seat=0,
    )
    return state, red, halberd


def test_the_pending_payload_forwards_a_halberd_transformed_slash_manifest() -> None:
    """W7-02 跨线契约：出牌阶段转化【杀】的清单（方天 ⇒ 目标步 1..3）原样转发。

    写方 = 引擎同形的构造（``legal_actions_for`` → ``transform_candidates`` →
    ``conversion_preactivations``；多目标候选来自 W7-02 的 ``virtual_card_use``），
    读方 = ``app.js`` 的清单多选（W7-01b）。接入层不重算池、不改键。
    """
    from sgs.effects import legal_actions_for
    from sgs.skills import (
        conversion_preactivations,
        is_transformed_action,
        transform_candidates,
    )
    from sgs.state import QueryKind as _QueryKind

    state, red, halberd = _halberd_wusheng_play_state()
    candidates = legal_actions_for(state, 0, _QueryKind.PLAY_PHASE, answers_seq=1)
    internal = transform_candidates(_QueryKind.PLAY_PHASE, candidates)
    manifests = conversion_preactivations(state, 0, internal)
    query = PlayPhaseQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="出牌阶段：使用一张牌，或结束出牌阶段。",
        legal_actions=tuple(
            action for action in candidates if not is_transformed_action(action)
        ),
        view=project(state, 0),
        transform_preactivations=manifests,
        transform_actions=internal,
    )

    payload = pending_payload(query, human_seat=0)

    assert payload is not None
    steps = payload["preactivations"]["hero.guanyu.wusheng"]
    assert [step["name"] for step in steps] == ["card", "seat"]
    target = steps[1]
    assert (target["min"], target["max"]) == (1, 3), "目标步 = 1..3"
    # W7-03 / D4：逐来源一行，且行上带该来源的**上限**（`options` 两行相同，说不出上限）。
    assert target["pool"] == [
        {"when": [str(halberd)], "options": ["seat:1", "seat:2", "seat:3"], "max": 1},
        {"when": [str(red)], "options": ["seat:1", "seat:2", "seat:3"], "max": 3},
    ], "目标池按来源 / 候选顺序原样下发，每行带逐来源上限"
    assert set(steps[0]["pool"][0]["options"]) == {str(red), str(halberd)}, (
        "来源步两条：手牌的最后一张红牌 + 装备区的方天"
    )


def test_the_candidate_index_for_a_halberd_transformed_borrow_window_keeps_bn_in_every_combo() -> None:
    """W7-02：借刀窗口的**转化【杀】**候选索引 = 1..3 目标，且 combo 恒含 Bn。

    与 W7-01 的真【杀】用例同形，只是候选来自``virtual_card_use``（龙胆：【闪】当【杀】）。
    """
    from sgs.effects import legal_actions_for
    from sgs.skills import transformed_card
    from sgs.state import QueryKind as _QueryKind
    from sgs.state import RespondContext, RespondMode

    pack = _context_pack()
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    halberd = cards_of_type(pack, "halberd")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(dodge,), equip=(halberd,), hero="zhaoyun"),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {borrowed, dodge, halberd}
        ),
        turn_seat=0,
    )
    query = RespondQuery(
        seq=1,
        actor=1,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        prompt="【借刀杀人】：对 0 号位使用【杀】，或交出武器。",
        legal_actions=legal_actions_for(
            state,
            1,
            _QueryKind.RESPOND,
            answers_seq=1,
            payload=RespondContext(
                cause=WindowCause.BORROWED_SWORD,
                required="slash",
                mode=RespondMode.USE,
                use_targets=(0,),
            ),
        ),
        view=project(state, 1),
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        target=1,
    )
    virtual_id = str(transformed_card(state, (dodge,), "slash").card_id)

    entry = candidate_index(query)[virtual_id]

    assert entry["min_targets"] == 1
    assert entry["max_targets"] == 3, "方天在最后一张手牌时放宽到 3"
    assert all(0 in combo for combo in entry["combos"]), "每一组组合都含 Bn"
    assert {len(combo) for combo in entry["combos"]} == {1, 2, 3}
    assert entry["pool"] == [0, 2, 3], "完整 check_targets 池（含额外目标）"
    assert entry["card"]["virtual_sources"] == [dodge], "键仍是那张虚拟【杀】（负 id）"


def test_the_pending_payload_forwards_the_card_preactivation_manifest() -> None:
    """W5-02b 跨线契约：借刀的**两步清单**由 ``pending_payload`` 显式下发。

    写方 = 引擎的 ``_build_query``（``card_preactivations``，`exclude` 不进记录流），
    读方 = ``app.js`` 的手牌入口（点借刀 ⇒ 按这张表进入两步清单）。键 = ``card.type``
    （``borrowed_sword``，不是中文名），接入层**原样转发**、不另算一遍池。
    """
    pack = _context_pack()
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(equip=(weapon,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {borrowed, weapon}
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_queries(
        engine,
        [PassPlayer() for _ in range(4)],
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )

    payload = pending_payload(engine.pending, human_seat=0)

    assert payload is not None
    assert set(payload["card_preactivations"]) == {"borrowed_sword"}, (
        f"键 = 卡牌类型 id（两张借刀同型）：{set(payload['card_preactivations'])}"
    )
    steps = payload["card_preactivations"]["borrowed_sword"]
    assert [step["name"] for step in steps] == ["seat", "seat"]
    assert [step["order"] for step in steps] == [0, 1]
    assert [step["min"] for step in steps] == [1, 1]
    assert steps[0]["wording"] == "选择持刀者（装备区有武器、且有合法【杀】目标）"
    assert steps[1]["wording"] == "选择【杀】的目标（由你指定）"
    assert steps[0]["pool"] == [{"when": [], "options": ["seat:1"]}]
    assert steps[1]["pool"] == [
        {"when": ["seat:1"], "options": ["seat:0", "seat:2", "seat:3"]}
    ], "Bn 子池按 when='seat:A' 分列"
    assert steps == [
        step.model_dump(mode="json")
        for step in engine.pending.card_preactivations["borrowed_sword"]
    ], "接入层原样转发，不重算池"
    assert not any(
        action.get("kind") == "UseCard"
        and (action.get("card") or {}).get("type") == "borrowed_sword"
        for action in payload["legal_actions"]
    ), "裸借刀候选不在下发候选里（入口 = card_preactivations）"


def test_the_borrowed_sword_prompt_names_bn_even_when_only_a_transform_can_use_it():
    """W5-02b：持刀者只有转化【杀】时，投影的提示行仍写出使用者指定的 Bn。

    真会话（与 ``pending_payload`` 同一条写入面）：座 0 按清单指定 [A=1, Bn=2]；
    座 1 = 赵云、装备武器、手里只有一张【闪】（转化候选已从 ``legal_actions`` 收回）。
    提示行必须从结算帧的 ``designations`` 读 Bn，不能只从候选的 ``targets`` 猜——否则
    这条只可能由【龙胆】提供 a 的窗口会退化成"对合法目标"。
    """
    pack = _context_pack()
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "crossbow")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(dodge,), equip=(weapon,), hero="zhaoyun"),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {borrowed, weapon, dodge}
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [PassPlayer() for _ in range(3)]
    run_queries(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == "PLAY_PHASE",
    )
    play = engine.pending
    designation = next(
        action for action in play.designation_actions if action.designations == (2,)
    )
    engine.submit(designation)
    run_queries(
        engine,
        players,
        lambda running: running.pending is not None
        and running.pending.kind == "RESPOND"
        and running.pending.cause is WindowCause.BORROWED_SWORD,
    )

    window = engine.pending
    assert window is not None and window.actor == 1
    assert not any(
        getattr(action, "targets", None) for action in window.legal_actions
    ), "前提：这是一个没有真牌 a 的窗口（转化候选不在暴露面）"
    context = query_context(engine, window)
    assert context is not None
    assert context["requirement"] == "对 2 号位使用【杀】，或交出武器", (
        context["requirement"]
    )


def test_the_pending_payload_forwards_the_jijiu_manifest_in_the_peach_window() -> None:
    """求桃窗口：**急救**的清单 = 红色牌，**无目标步**（濒死者由窗口固定）。

    W4-fix-3 起同一条真会话还钉**收口后的候选面**（写方 = 引擎的 ``_build_query``）：
    只有真【桃】的使用 + 放弃；转化【桃】只经【急救】的技能入口，故 ``virtual_index`` 是
    **空的**（候选里没有虚拟牌），选项也不再出现"使用【桃】→ 座位 1"两条。
    """
    from sgs.data import Color, color_of_suit
    from sgs.state import QueryKind, UseCard

    pack = _context_pack()
    slash = cards_of_type(pack, "slash")[0]
    red_dodge = next(
        card_id
        for card_id in cards_of_type(pack, "dodge")
        if color_of_suit(pack.deck.cards[card_id].suit) is Color.RED
    )
    red_peach = next(
        card_id
        for card_id in cards_of_type(pack, "peach")
        if color_of_suit(pack.deck.cards[card_id].suit) is Color.RED
    )
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(hp=1),
            SeatSpec(hand=(red_peach, red_dodge), hero="huatuo"),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in (slash, red_dodge, red_peach)
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    def respond(query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == slash:
                    return action
            for action in query.legal_actions:
                if action.kind == "EndPhase":
                    return action
        for action in query.legal_actions:
            if action.kind == "Pass":
                return action
        return query.legal_actions[0]

    query = None
    for _ in range(3000):
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        if engine.pending.kind == QueryKind.PEACH and engine.pending.actor == 2:
            query = engine.pending
            break
        engine.submit(respond(engine.pending))
    assert query is not None, "没走到座位 2（回合外的华佗）的求桃询问"

    payload = pending_payload(query, human_seat=2)

    assert payload is not None
    steps = payload["preactivations"][JIJIU]
    assert [step["name"] for step in steps] == ["card"], "无目标步：濒死者由窗口固定"
    assert steps[0]["pool"][0]["options"] == [str(red_peach), str(red_dodge)], (
        "急救的来源 = 两张红色牌（真【桃】也可以当来源）"
    )
    assert steps[0]["zones"] == ["hand"]

    # 候选面：真【桃】一条 + 放弃；没有虚拟牌 ⇒ ``virtual_index`` 空。
    legal = payload["legal_actions"]
    assert [action["kind"] for action in legal] == ["UseCard", "Pass"], legal
    assert legal[0]["card"]["card_id"] == red_peach
    assert legal[0]["card"]["type"] == "peach"
    assert legal[0]["targets"] == [1], "目标是濒死者"
    assert payload["virtual_index"] == {}, "求桃窗口没有转化候选 ⇒ 来源索引为空（别读它）"
    labels = [
        (action["card"]["name"], tuple(action["targets"]))
        for action in legal
        if action.get("card")
    ]
    assert labels == [("桃", (1,))], f"选项不再重复：{labels}"

    # 【急救】的入口文案：技能名 + "当【桃】"，与手牌真【桃】在界面上分得开。
    context = query_context(engine, query)
    assert context is not None
    hints = [hint for hint in context["skill_hints"] if hint["skill_id"] == JIJIU]
    assert [hint["name"] for hint in hints] == ["急救"]
    assert "当【桃】" in hints[0]["requirement"]
    assert "急救" not in labels[0][0], "手牌真【桃】的选项文案里没有急救"


# ---------------------------------------------------------------------------
# W4-fix-4：【闪电】命中打死被判定者之后，浏览器下一帧拿到的效果区必须是空的
# ---------------------------------------------------------------------------


def _lethal_lightning_session() -> Session:
    """真会话：座位 1 判定区一张【闪电】、牌堆第三张是一张 ♠2~9，3 点雷电恰好打死 3 血的他。

    4 人身份局（模式由座位数派生）：先手是主公（座位 0，也是人类座位），被判定者是**反贼**
    —— 死他一个不终局，回合照常轮转（"非主公在自己回合内阵亡 ⇒ 剩余阶段整体跳过"那一支）。

    推进到**被判定者阵亡之后**人类座位的第一问（此刻浏览器会收到下一份响应体）：

    - 各家一律 :class:`PassPlayer`（出牌阶段结束、其余放弃）：濒死求桃**不许**旁人真去救，
      否则"被判定者阵亡"这个前提不成立；
    - 引擎换成人为局面（随机建局走不到这个形状），但会话壳、``GameRegistry.view`` 与
      ``visible_record`` 这条**下发链路**一字不换（同 :func:`_yiji_real_session` 的手法）。
    """
    pack = _context_pack()
    lightning = cards_of_type(pack, "lightning")[0]
    spade = next(
        index
        for index, card in enumerate(pack.deck.cards)
        if index != lightning and card.suit is Suit.SPADE and 2 <= card.rank <= 9
    )
    others = [index for index in range(len(pack.deck.cards)) if index not in (lightning, spade)]
    # 先手是主公（座位 0，人类）⇒ 它在判定前先摸两张，判定牌必须排在**第三张**。
    deck = tuple(others[:2]) + (spade,) + tuple(others[2:])
    rule_config = validate_rule_config({"player_count": 4})
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, identity=Identity.LORD),
            SeatSpec(hp=3, judge=(lightning,), identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.LOYALIST),
            SeatSpec(hp=4, identity=Identity.RENEGADE),
        ],
        deck=deck,
        phase=Phase.PREPARE,
        rule_config=rule_config,
    )
    session = Session(
        pack=pack, seed=1, ai="random", timeout_seconds=None, player_count=4, human_seat=0
    )
    session.engine = Engine.from_state(state, seed=1)
    session.participants = tuple(PassPlayer() for _ in range(4))
    run_queries(session.engine, session.participants, _after_the_lethal_judgment)
    return session


def _after_the_lethal_judgment(engine: Engine) -> bool:
    """停止条件：被判定者已经阵亡，且引擎停在了人类座位（0）的出牌阶段。"""
    pending = engine.pending
    return (
        not engine.state.player(1).alive
        and pending is not None
        and pending.kind == "PLAY_PHASE"
        and int(pending.actor) == 0
    )


def test_a_real_session_clears_the_effect_area_after_a_lethal_lightning_judgment() -> None:
    """W4-fix-4 的复现（接入层那一半）：命中把人打死时，下发的记录流里也要有"离开处理区"。

    手动验收：**【闪电】判定命中后，这张牌一直留在效果区**。根因在引擎（见
    ``tests/test_judgment.py`` 的
    ``test_lightning_hit_that_kills_the_judged_seat_still_clears_the_processing_zone``），
    但报障看得见的那一层是**下发的响应体**：效果区在等新快照到达时按 ``card_move`` 逐事件
    派生（``app.js::applyEffectDelta``），牌卡住 = 那条 delta 在记录流里根本没出现 / 快照里
    还带着它。故这里钉的是 ``GameRegistry.view`` 这份**浏览器实际收下的 dict**：

    - 快照 ``processing`` 里没有这张【闪电】；
    - 记录流里有一条 ``processing → discard``（``reason = USE``）的 ``card_move``，且它排在
      当前挂起的那条询问**之前** —— 浏览器收到这一批时就会把效果区那张牌拿掉（下一帧清空）。

    修之前这条与引擎用例同时红（快照里还带着那张牌），修之后同时绿：一条复现钉两头。
    """
    from sgs_web.serve import GameRegistry

    session = _lethal_lightning_session()
    lightning = cards_of_type(_context_pack(), "lightning")[0]
    query = session.pending
    assert query is not None and query.kind == "PLAY_PHASE", "停在被判定者阵亡之后的下一问"
    assert int(query.actor) == int(session.human_seat), "停的是人类座位自己的下一问"
    assert not session.engine.state.player(1).alive, "前置：被判定者被这 3 点雷电打死"

    body = GameRegistry(runs_dir="/tmp/unused").view(session, after=-1, logs_after=0)

    processing = [card["card_id"] for card in body["snapshot"]["processing"]]
    assert lightning not in processing, "命中生效后处理区（= 效果区）不得再留着它"

    moves = [
        record
        for record in body["records"]
        if record["kind"] == "delta"
        and record["delta_type"] == "card_move"
        and any(card["card_id"] == lightning for card in record["cards"])
    ]
    assert moves, "下发的记录流里一条【闪电】的 card_move 都没有（接线断了）"
    leaving = [record for record in moves if record["from_zone"]["zone"] == "processing"]
    assert leaving, "没有「离开处理区」的 delta，效果区只能一直挂着它"
    assert leaving[-1]["to_zone"]["zone"] == "discard", leaving[-1]["to_zone"]
    assert leaving[-1]["reason"] == "USE", "命中后的去向与既有约定一致（reason = USE）"
    assert leaving[-1]["seq"] < query.seq, "这条 delta 必须在本批（停在下一问之前）里送达"


# ---------------------------------------------------------------------------
# U1-04 展示契约：座位状态字段（chained / wine_active）
# ---------------------------------------------------------------------------


def test_the_public_seat_exposes_chained_and_wine_defaults() -> None:
    """U1 先下发默认值：新局所有人 ``chained=False`` / ``wine_active=False``。"""
    session = Session(seed=61, ai="random", timeout_seconds=None, player_count=5)
    snapshot = visible_snapshot(session)

    assert snapshot["players"], "快照必须有座位"
    for player in snapshot["players"]:
        assert player["chained"] is False, f"座位 {player['seat']} 的横置默认未下发"
        assert player["wine_active"] is False, f"座位 {player['seat']} 的酒状态默认未下发"


def test_the_public_seat_reads_chained_and_wine_from_engine_state() -> None:
    """字段是真状态：引擎状态置真时投影读得到（M5 接状态时只写状态，不改投影）。"""
    pack = load_content_pack(deck_profile="standard_108")
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=tuple(range(10)),
    )
    state.players[0].chained = True
    state.players[1].wine_active = True

    view = project(state, 0)

    assert view.players[0].chained is True and view.players[0].wine_active is False
    assert view.players[1].wine_active is True and view.players[1].chained is False
    assert "chained" in PlayerPublic.model_fields
    assert "wine_active" in PlayerPublic.model_fields


# ---------------------------------------------------------------------------
# U1-04 展示契约：纯函数（牌面 / 提示条）
# ---------------------------------------------------------------------------


def test_the_card_contract_maps_fire_and_thunder_slashes_to_the_product_name() -> None:
    """火杀 / 雷杀都显示「杀」；属性分开（火焰 / 雷电），转化牌带来源牌。

    标准包还没有军争的 ``fire_slash`` / ``thunder_slash``（U1-04b 只预写描述），
    故这里是**契约单元**的合成牌面；真实载荷路径（普通杀 / 闪 / 桃）由
    ``test_every_visible_card_carries_the_display_contract`` 与
    ``test_the_pending_payload_carries_display_fields...`` 覆盖。
    """
    from sgs.state import CardRef
    from sgs_web.display import card_display

    source = CardRef(card_id=2, type="dodge", name="闪", suit="heart", rank=2, color="red")
    fire = CardRef(
        card_id=-3, type="fire_slash", name="火杀", virtual_sources=(2,)
    )
    thunder = CardRef(card_id=-4, type="thunder_slash", name="雷杀")
    plain = CardRef(card_id=1, type="slash", name="杀", suit="spade", rank=7, color="black")
    peach = CardRef(card_id=5, type="peach", name="桃", suit="heart", rank=3, color="red")

    fire_fields = card_display(fire, lookup={2: source})
    assert fire_fields["display_name"] == "杀", "火杀显示产物名「杀」，不是「火杀」"
    assert fire_fields["attribute"] == "火焰"
    assert fire_fields["kind"] == "transformed"
    assert [card["card_id"] for card in fire_fields["source_cards"]] == [2]
    assert fire_fields["source_cards"][0]["display_name"] == "闪"

    thunder_fields = card_display(thunder)
    assert thunder_fields["display_name"] == "杀"
    assert thunder_fields["attribute"] == "雷电"
    assert thunder_fields["kind"] == "virtual", "无实体来源的虚拟牌是 virtual"

    assert card_display(plain)["display_name"] == "杀"
    assert card_display(plain)["attribute"] == "普通"
    assert card_display(plain)["kind"] == "normal"
    assert card_display(peach)["display_name"] == "桃"
    assert card_display(peach)["source_cards"] == []


def test_the_card_contract_keeps_single_source_suit_and_rank() -> None:
    """单源转化继承原牌的花色 / 点数（引擎构造器已经把两者带上，展示层原样给）。"""
    from sgs.skills import transformed_card
    from sgs_web.display import card_display

    pack = _context_pack()
    source_id = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(source_id,)), SeatSpec()],
        deck=tuple(card_id for card_id in range(len(pack.deck.cards)) if card_id != source_id),
        turn_seat=0,
    )
    source = state.card(source_id)
    virtual = transformed_card(state, (source_id,), "slash")

    fields = card_display(virtual, lookup={source_id: source})

    assert virtual.suit == source.suit and virtual.rank == source.rank, "单源继承花色 / 点数"
    assert virtual.color == source.color
    assert fields["kind"] == "transformed"
    assert fields["source_cards"][0]["suit"] == source.suit
    assert fields["source_cards"][0]["rank"] == source.rank


def test_the_card_contract_shows_a_multi_source_transform_without_suit_or_rank() -> None:
    """多源转化（【丈八蛇矛】）：花色 / 点数恒无，颜色按来源同色派生；来源全列。

    单源形态（继承花色 / 点数）与火 / 雷属性由
    ``test_the_card_contract_maps_fire_and_thunder_slashes_to_the_product_name``
    覆盖；这里补的是**唯一另一条分支** —— 多来源。
    """
    from sgs.state import CardRef
    from sgs_web.display import card_display

    first = CardRef(card_id=2, type="dodge", name="闪", suit="heart", rank=2, color="red")
    second = CardRef(card_id=18, type="slash", name="杀", suit="heart", rank=10, color="red")
    transformed = CardRef(
        card_id=-237,
        type="slash",
        name="杀",
        suit=None,
        rank=None,
        color="red",
        virtual_sources=(2, 18),
    )

    fields = card_display(transformed, lookup={2: first, 18: second})

    assert fields["display_name"] == "杀"
    assert fields["attribute"] == "普通"
    assert fields["kind"] == "transformed"
    assert fields["source_cards"] and [card["card_id"] for card in fields["source_cards"]] == [2, 18]
    assert fields["source_cards"][1]["display_name"] == "杀", "来源牌自己的展示字段也要有"
    assert transformed.suit is None and transformed.rank is None, "多源不继承花色 / 点数"
    assert fields["description"], "产物牌名有描述全文（悬停用）"


def _real_transform_engine() -> tuple[object, Engine, int, int]:
    """真引擎的丈八转化【杀】局面：停在出牌阶段，清单里有两来源。

    返回 ``(pack, engine, first_source_id, second_source_id)``。
    """
    pack = _context_pack()
    first = cards_of_type(pack, "slash")[0]
    second = cards_of_type(pack, "dodge")[0]
    serpent = cards_of_type(pack, "serpent_spear")[0]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(first, second), equip=(serpent,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=tuple(
            card_id
            for card_id in range(len(pack.deck.cards))
            if card_id not in {first, second, serpent}
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    for _ in range(200):
        if engine.pending is not None and engine.pending.kind == QueryKind.PLAY_PHASE:
            break
        engine._step()  # noqa: SLF001 - 与 test_hero_transform.py 同一推进手法
    query = engine.pending
    assert query is not None and "equip.serpent_spear" in query.transform_preactivations
    return pack, engine, first, second


def _real_transform_use_session() -> tuple[Session, int, int]:
    """一个**真引擎**的丈八转化【杀】局面：停在目标的 DODGE 窗口。

    返回 ``(session, first_source_id, second_source_id)``。来源牌已由引擎移入处理区、
    结算栈顶的 USE / RESOLVE 帧带着那张虚拟【杀】——这正是网页载荷要读的产出路径。
    """
    pack, engine, first, second = _real_transform_engine()
    query = engine.pending
    assert query is not None
    engine.submit(
        ActivateSkill(
            skill_id="equip.serpent_spear",
            picks=(first, second),
            choices=("seat:1",),
            answers_seq=query.seq,
        )
    )
    for _ in range(200):
        if engine.pending is not None or engine.finished:
            break
        engine._step()  # noqa: SLF001
    assert engine.pending is not None and engine.pending.kind == QueryKind.DODGE, (
        "真引擎应当停在目标出【闪】的窗口（此时来源牌还在处理区）"
    )
    session = Session(pack=pack, seed=1, ai="random", timeout_seconds=None, player_count=4)
    session.engine = engine
    session.human_seat = engine.pending.actor
    return session, first, second


def test_the_pending_payload_lists_the_transform_product_for_each_source() -> None:
    """U1-07 跨线契约：转化清单的来源牌 → 产物（给「将以【X】使用」读）。

    清单只说"选哪些来源牌"；产物牌名 / 颜色由这份**展示派生**给出。同一来源组的两张牌
    指向同一张虚拟【杀】；查看者不是询问者时整份为空（fail-closed）。
    """
    pack, engine, first, second = _real_transform_engine()
    session = Session(pack=pack, seed=1, ai="random", timeout_seconds=None, player_count=4)
    session.engine = engine
    session.human_seat = 0

    payload = pending_payload(session)
    assert payload is not None
    products = payload["transform_products"]
    product = next(
        (
            candidate
            for candidate in products
            if {card["card_id"] for card in candidate["source_cards"]} == {first, second}
        ),
        None,
    )
    assert product is not None, f"来源组 {first},{second} 的产物必须下发：{len(products)} 张"
    assert product["kind"] == "transformed"
    assert product["display_name"] == "杀"
    assert len({candidate["card_id"] for candidate in products}) == len(products), (
        "同一产物只下发一次"
    )

    assert engine.pending is not None
    foreign = pending_payload(engine.pending, human_seat=1)
    assert foreign is not None and foreign["transform_products"] == [], (
        "别人的转化方向不随询问下发"
    )


def test_a_real_transform_use_shows_the_product_in_the_processing_area() -> None:
    """U1-07：真引擎用过丈八之后，网页处理区画的是**产物**，不是两张来源牌。

    读取方是 ``visible_snapshot`` / ``pending_payload`` 的内嵌视角；两条都查，免得只有
    刷新快照对了、应答载荷还画着来源牌。记录流仍是引擎原有的事实（历史进事件流）。
    """
    session, first, second = _real_transform_use_session()
    product_id = next(
        int(frame.data["card"].card_id)
        for frame in session.engine.stack.frames
        if getattr(frame.data.get("card"), "is_virtual", False)
    )
    assert set(session.engine.state.processing) == {first, second}, "引擎处理区里是来源实体牌"

    snapshot = visible_snapshot(session)
    assert [card["card_id"] for card in snapshot["processing"]] == [product_id]
    product = snapshot["processing"][0]
    assert product["kind"] == "transformed"
    assert product["display_name"] == "杀"
    assert product["attribute"] == "普通"
    source_ids = [card["card_id"] for card in product["source_cards"]]
    assert len(source_ids) == 2 and set(source_ids) == {first, second}, (
        "两组来源牌都在（集合相等；顺序不是这条断言的判据）"
    )
    assert all("display_name" in card for card in product["source_cards"])
    assert {first, second}.isdisjoint({card["card_id"] for card in snapshot["processing"]}), (
        "来源牌不得在处理区重复出现"
    )

    payload = pending_payload(session)
    assert payload is not None
    embedded = payload["view"]["processing"]
    assert [card["card_id"] for card in embedded] == [product_id]
    assert embedded[0]["source_cards"][0]["card_id"] in {first, second}


def test_a_real_pure_virtual_use_shows_as_the_processing_product() -> None:
    """U1-07：纯虚拟牌（【八卦阵】的视为【闪】）没有来源实体牌，结算期间直接进处理区。

    引擎的 ``state.processing`` 仍是空的（虚拟牌不落区域）；展示层从结算栈读出这张牌，
    让中央舞台 / 处理区显示产物。
    """
    from sgs.skills import virtual_dodge

    pack = _context_pack()
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec()],
        deck=tuple(range(len(pack.deck.cards))),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    virtual = virtual_dodge(state)
    engine.start_use(virtual, source=0, targets=(), use_timing=Timing.RESOLVE_BEFORE_EFFECT)
    session = Session(pack=pack, seed=1, ai="random", timeout_seconds=None, player_count=2)
    session.engine = engine
    session.human_seat = 0

    assert engine.state.processing == []
    processing = visible_snapshot(session)["processing"]
    assert [card["card_id"] for card in processing] == [virtual.card_id]
    assert processing[0]["kind"] == "virtual"
    assert processing[0]["display_name"] == "闪"
    assert processing[0]["source_cards"] == []


def test_the_prompt_contract_carries_the_text_and_the_timeout_default() -> None:
    """提示条契约：文本 + 超时默认动作 + 给人看的默认动作说明。"""
    from sgs_web.display import prompt_display
    from sgs_web.timeout import fallback_action

    session = Session(seed=5, ai="random", timeout_seconds=None, player_count=4)
    pending = session.pending
    assert pending is not None

    contract = prompt_display(pending)

    assert contract["text"] == pending.prompt
    assert contract["default_action"]["kind"] == fallback_action(pending).kind
    assert contract["default_label"].startswith("超时将自动")
    if contract["default_action"]["kind"] == "Pass":
        assert "放弃" in contract["default_label"]
    elif contract["default_action"]["kind"] == "EndPhase":
        assert "结束出牌阶段" in contract["default_label"]
    else:
        assert "第一项" in contract["default_label"]


def test_the_arrange_prompt_says_the_timeout_submits_the_current_arrangement() -> None:
    """D9.3 / 票 07：观星到时限 = **前端**提交当前排列，倒计时文案必须这么说。

    反例（本轮评审发现）：通用兜底把 `ARRANGE` 写成「超时将自动选择第一项」，与
    `answer.js:maybeAutoSubmitArrange` 的实际行为相反。服务端 `fallback_action` 仍是
    `ArrangeCards` 候选第一条，所以契约的 `default_action` 照旧由它派生；改的只是给人
    看的那句话。
    """
    from sgs_web.display import prompt_display

    session = _session_at_guanxing_arrange()
    pending = session.pending
    assert pending is not None and pending.kind == "ARRANGE"

    contract = prompt_display(pending)

    assert contract["default_action"]["kind"] == "ArrangeCards"
    assert contract["default_label"] == "超时将按当前排列提交"
    assert "第一项" not in contract["default_label"], (
        "排列到时限提交的是当前排列，不是候选里的第一项"
    )


# ---------------------------------------------------------------------------
# U1-04 展示契约：投影 / 当前挂起真的把展示字段发下去
# ---------------------------------------------------------------------------


def _visible_cards(snapshot: dict) -> list[dict]:
    """快照里所有**看得见的**牌（自己手牌 / 公共区 / 逐座位装备判定区）。"""
    cards: list[dict] = []
    for key in ("self_hand", "discard", "processing"):
        cards.extend(snapshot.get(key) or [])
    for player in snapshot.get("players") or []:
        for key in ("equip", "judge"):
            cards.extend(player.get(key) or [])
    return cards


def test_every_visible_card_carries_the_display_contract() -> None:
    session = Session(seed=61, ai="random", timeout_seconds=None, player_count=5)
    snapshot = visible_snapshot(session)

    cards = _visible_cards(snapshot)
    assert cards, "快照里必须有牌，否则这条断言是空转"
    for card in cards:
        assert card["display_name"], f"牌 {card['card_id']} 缺 display_name"
        assert card["attribute"] in CARD_ATTRIBUTES, card
        assert card["kind"] in CARD_KINDS, card
        assert isinstance(card["source_cards"], list), card

    for player in snapshot["players"]:
        assert "chained" in player and "wine_active" in player
        assert "identity_label" in player, "座位身份标签由服务端映射一次，前端不再另抄"
    me = next(p for p in snapshot["players"] if p["seat"] == session.human_seat)
    assert me["identity_label"] in ("主公", "忠臣", "反贼", "内奸"), me


def test_the_pending_payload_carries_display_fields_and_the_prompt_contract() -> None:
    session = Session(seed=5, ai="random", timeout_seconds=None, player_count=4)
    pending = session.pending
    assert pending is not None
    payload = pending_payload(session)

    assert payload["prompt_display"]["text"] == pending.prompt
    assert payload["prompt_display"]["default_label"].startswith("超时将自动")
    assert payload["prompt_display"]["default_action"]["kind"] == fallback_action(pending).kind

    seen = 0
    for entry in payload["candidate_index"].values():
        assert "display_name" in entry["card"]
        seen += 1
    # `legal_actions` 必须保持**可回传**：Action 模型 extra="forbid"，多一个 display_* 键
    # 就会让提交变成 400。展示字段只放只读载体上。
    for action in payload["legal_actions"]:
        card = action.get("card")
        if isinstance(card, dict):
            assert "display_name" not in card, f"候选卡被写了展示字段，回传会 400：{card}"
            seen += 1
    assert seen > 0, "这条询问必须至少有一张牌，否则展示契约没有消费者"

    view = payload["view"]
    for player in view["players"]:
        assert "identity_label" in player
        assert "chained" in player and "wine_active" in player
    for card in view["self_hand"]:
        assert "display_name" in card


def test_the_seat_contract_maps_the_identity_label_and_live_state() -> None:
    """座位展示契约：身份中文标签只映射一次；横置 / 酒读引擎状态。"""
    from sgs_web.display import seat_display

    pack = load_content_pack(deck_profile="standard_108")
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, identity=Identity.LORD),
            SeatSpec(hp=4, identity=Identity.REBEL),
        ],
        deck=tuple(range(10)),
    )
    state.players[0].chained = True
    state.players[1].wine_active = True
    view = project(state, 0)

    lord = seat_display(view.players[0])
    rebel = seat_display(view.players[1])
    assert lord["identity_label"] == "主公"
    assert lord["chained"] is True and lord["wine_active"] is False
    assert rebel["identity_label"] is None, "查看者 0 看不到反贼的身份 ⇒ 标签也是 None"
    assert rebel["wine_active"] is True


def test_the_seat_contract_names_the_hero_and_kingdom() -> None:
    """座位卡的武将块要写**名称与势力**，不是引擎 id（U1-02）。

    名称来自武将表（`load_heroes`），势力是 ``PlayerPublic`` 已有的公开字段；
    没有武将的座位（无武将模式 / 人为局面）两栏都是 ``None``，界面据此退回"（无武将）"。
    """
    from sgs.data import load_heroes
    from sgs_web.display import seat_display

    pack = load_content_pack(deck_profile="standard_108")
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hp=4, hero="guojia", kingdom=Kingdom.WEI),
            SeatSpec(hp=4, hero="zhaoyun", kingdom=Kingdom.SHU),
            SeatSpec(hp=4),
        ],
        deck=tuple(range(10)),
    )
    view = project(state, 0)
    heroes = load_heroes()

    guojia = seat_display(view.players[0], heroes=heroes)
    zhaoyun = seat_display(view.players[1], heroes=heroes)
    empty = seat_display(view.players[2], heroes=heroes)

    assert guojia["hero_name"] == "郭嘉"
    assert guojia["kingdom"] == "wei"
    assert guojia["kingdom_label"] == "魏"
    assert zhaoyun["hero_name"] == "赵云"
    assert zhaoyun["kingdom_label"] == "蜀"
    assert empty["hero_name"] is None and empty["kingdom"] is None
    assert empty["kingdom_label"] is None


def test_the_web_snapshot_carries_each_seats_hero_name_and_kingdom() -> None:
    """真值走完整 ``visible_snapshot``：前端不需要自己把 id 翻成名称 / 势力。"""
    from sgs.data import load_heroes

    session = Session(seed=61, ai="random", timeout_seconds=None, player_count=5)
    heroes = load_heroes()

    snapshot = visible_snapshot(session)
    for payload in snapshot["players"]:
        hero_id = session.hero_assignments[payload["seat"]]
        hero = heroes.by_id(hero_id)
        assert hero is not None
        assert payload["hero_name"] == hero.name, f"座位 {payload['seat']} 的武将名"
        assert payload["kingdom"] == hero.kingdom.value
        assert payload["kingdom_label"] in ("魏", "蜀", "吴", "群")


def test_the_web_snapshot_reveals_a_seat_marked_identity_visible() -> None:
    """M5 亮明效果的投影入口：字段一旦写进状态，网页快照立刻带标签。

    本票不新增亮明效果（不加 delta），故写字段的**产品调用点**要等 M5；这里用真的
    ``visible_snapshot`` 走完整投影 / 展示链路，证明读取方不是只读合成 ``project``。
    """
    session = Session(seed=67, ai="random", timeout_seconds=None, player_count=5)
    target = next(
        player
        for player in session.engine.state.players
        if player.identity is not Identity.LORD and player.alive
    )
    target.identity_visible = True

    snapshot = visible_snapshot(session)
    payload = next(p for p in snapshot["players"] if p["seat"] == target.seat)

    assert payload["identity"] == target.identity.value, "字段为真 ⇒ 身份可见"
    assert payload["identity_label"] in ("主公", "忠臣", "反贼", "内奸")


def test_the_web_snapshot_reads_true_chained_and_wine_from_the_state() -> None:
    """真值走完整 ``visible_snapshot``：座位字段不是只在 ``project`` 里可读。"""
    session = Session(seed=61, ai="random", timeout_seconds=None, player_count=5)
    target = session.engine.state.players[1]
    target.chained = True
    target.wine_active = True

    snapshot = visible_snapshot(session)
    payload = next(p for p in snapshot["players"] if p["seat"] == target.seat)

    assert payload["chained"] is True
    assert payload["wine_active"] is True


def test_a_bare_query_rebuilds_transformed_source_cards_from_the_query_itself() -> None:
    """409 拿不到引擎：来源牌从询问自己的视角 / 选择面里找，不给空来源就完事。"""
    from sgs.state import CardRef, Slot, Zone, ZoneRef

    pack = load_content_pack(deck_profile="standard_108")
    source_id = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hp=4, hand=(source_id,)), SeatSpec(hp=4)],
        deck=tuple(range(10)),
    )
    view = project(state, 0)
    virtual = CardRef(
        card_id=-3, type="slash", name="杀", virtual_sources=(source_id,)
    )
    query = SelectCardsQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="提供一张【杀】继续使用",
        legal_actions=(),
        view=view,
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
        slots=(Slot(ref=ZoneRef(Zone.HAND, 0), card=virtual),),
    )

    payload = pending_payload(query, human_seat=0)

    card = payload["slots"][0]["card"]
    assert card["kind"] == "transformed"
    assert [item["card_id"] for item in card["source_cards"]] == [source_id]
    assert payload["prompt_display"] is None, "没有候选时显式不给提示契约，不伪造默认动作"


# ---------------------------------------------------------------------------
# U1-04b 描述全文：43 个牌名 + 51 条技能（独立内容文件，不进引擎 / 录像）
# ---------------------------------------------------------------------------

MILITARY_CARD_TYPE_IDS = frozenset(
    {
        "fire_slash",
        "thunder_slash",
        "wine",
        "supply_shortage",
        "fire_attack",
        "iron_chain",
        "silver_lion",
        "vine_armor",
        "ancient_scimitar",
        "vermilion_feather_fan",
        "hualiu",
    }
)
"""U1-04b 预写的 11 个军争牌名 id；M5-01a 起已并入内容包，本清单保留作独立核对。"""


def test_every_card_name_and_skill_id_has_a_description() -> None:
    """覆盖测试：32 个标准牌名 + 11 个军争预写牌名 + 51 条技能一条不缺。

    缺一条时断言消息**直接列出缺的 key**（不是只报一个总数）——这是票面的验收判据。
    多余 / 拼错的 key 也一并拦：否则「43 条」可以是 42 条真条目 + 1 条错拼。
    """
    from sgs.skills import CATALOG

    from sgs_web.descriptions import CARD_DESCRIPTIONS, SKILL_DESCRIPTIONS

    standard = {card_type.id for card_type in load_content_pack(deck_profile="standard_108").card_types}
    expected_cards = standard | MILITARY_CARD_TYPE_IDS
    assert len(expected_cards) == 43, f"牌名边界漂了：标准 {len(standard)} + 军争 {len(MILITARY_CARD_TYPE_IDS)}"

    missing_cards = sorted(expected_cards - set(CARD_DESCRIPTIONS))
    assert not missing_cards, f"descriptions.json 缺牌名条目：{missing_cards}"
    stale_cards = sorted(set(CARD_DESCRIPTIONS) - expected_cards)
    assert not stale_cards, f"descriptions.json 有陈旧 / 拼错的牌名条目：{stale_cards}"

    expected_skills = set(CATALOG)
    assert len(expected_skills) == 51, f"CATALOG 技能数变了：{len(expected_skills)}"
    missing_skills = sorted(expected_skills - set(SKILL_DESCRIPTIONS))
    assert not missing_skills, f"descriptions.json 缺技能条目：{missing_skills}"
    stale_skills = sorted(set(SKILL_DESCRIPTIONS) - expected_skills)
    assert not stale_skills, f"descriptions.json 有陈旧 / 拼错的技能条目：{stale_skills}"

    for key, text in {**CARD_DESCRIPTIONS, **SKILL_DESCRIPTIONS}.items():
        assert isinstance(text, str) and text.strip(), f"{key} 的描述是空串"
        assert len(text) >= 10, f"{key} 的描述过短，疑似占位：{text!r}"


def test_the_transformation_entries_distinguish_product_and_source() -> None:
    """票面判据：纯虚拟牌 / 转化牌的描述要说清「产物」与「来源」的区别。

    这些条目会被 U1-02 的悬停原样显示；只断言“有文本”会让文案退化成官方牌面而不再
    解释产物 / 来源，玩家就分不清「【闪】当【杀】」到底是哪张牌在生效。
    """
    from sgs_web.descriptions import CARD_DESCRIPTIONS, SKILL_DESCRIPTIONS

    card_keys = (
        "slash",
        "dodge",
        "peach",
        "indulgence",
        "duel",
        "fire_slash",
        "thunder_slash",
        "serpent_spear",
        "vermilion_feather_fan",
    )
    for key in card_keys:
        text = CARD_DESCRIPTIONS[key]
        assert "产物" in text and "来源" in text, f"{key} 没写清产物 / 来源：{text}"

    skill_keys = (
        "hero.guanyu.wusheng",
        "hero.zhaoyun.longdan",
        "hero.daqiao.guose",
        "hero.huatuo.jijiu",
        "hero.diaochan.lijian",
        "equip.serpent_spear",
        "equip.eight_diagrams",
    )
    for key in skill_keys:
        text = SKILL_DESCRIPTIONS[key]
        assert "产物" in text and "来源" in text, f"{key} 没写清产物 / 来源：{text}"


def test_the_description_lookup_fails_closed_to_the_explicit_placeholder() -> None:
    """未知 key 必须显式得到「暂无描述」；查表键就是 ``CATALOG`` 的完整 skill_id。"""
    from sgs_web.descriptions import (
        CARD_DESCRIPTIONS,
        DESCRIPTION_FALLBACK,
        SKILL_DESCRIPTIONS,
        card_description,
        skill_description,
    )

    assert DESCRIPTION_FALLBACK == "暂无描述", "缺条目文案是界面契约的一部分，不能悄悄改"
    assert card_description("slash") == CARD_DESCRIPTIONS["slash"]
    assert card_description("no_such_card") == DESCRIPTION_FALLBACK
    assert card_description(None) == DESCRIPTION_FALLBACK, "空 key 也走同一条兜底，不返回空串"

    yiji = "hero.guojia.yiji"
    assert skill_description(yiji) == SKILL_DESCRIPTIONS[yiji]
    assert skill_description("hero.nobody.skill") == DESCRIPTION_FALLBACK
    assert skill_description(None) == DESCRIPTION_FALLBACK


def test_the_card_and_skill_contracts_carry_the_server_side_description() -> None:
    """展示契约下发全文：快照 / 候选 / 技能清单都带同一条 description。"""
    from sgs.state import CardRef

    from sgs_web.descriptions import card_description, skill_description
    from sgs_web.display import card_display

    session = Session(seed=5, ai="random", timeout_seconds=None, player_count=4)
    snapshot = visible_snapshot(session)

    # 真产品路径：HTTP 门面拼的响应体（serve.GameRegistry.view）里就得有全文。
    from sgs_web.serve import GameRegistry

    body = GameRegistry(runs_dir="/tmp/unused").view(session, after=-1, logs_after=0)
    assert body["skills"], "响应体里没有技能清单，描述没有消费者"
    assert all(skill["description"] != "暂无描述" for skill in body["skills"]), body["skills"]
    assert all(card["description"] for card in body["snapshot"]["self_hand"]), "手牌描述没下发"

    cards = _visible_cards(snapshot)
    assert cards, "快照里必须有牌，否则这条断言是空转"
    for card in cards:
        assert card["description"] == card_description(card["type"]), card

    skills = build_skills(session, session.human_seat)
    assert skills, "人类座位至少有一条武将技能"
    for skill in skills:
        assert skill["description"] == skill_description(skill["skill_id"]), skill
        assert skill["description"] != "暂无描述", f"CATALOG 里的技能不该缺条目：{skill['skill_id']}"

    pending = session.pending
    assert pending is not None
    payload = pending_payload(session)
    seen = 0
    for entry in payload["candidate_index"].values():
        card = entry["card"]
        assert card["description"] == card_description(card["type"]), card
        seen += 1
    assert seen > 0, "这条询问必须至少有一张候选牌，否则候选描述没有消费者"

    unknown = CardRef(card_id=-77, type="no_such_card", name="？")
    assert card_display(unknown)["description"] == "暂无描述", "缺条目路径由展示层显式兜底"


def test_the_ice_sword_description_says_two_cards_not_at_most_two() -> None:
    """D9.2 / 票 13：寒冰剑有牌必须弃满两张——展示内容不得再写「至多两张」。

    规则侧早已强制两张（`optional=False`、两轮逐张弃置），票 13 只改逐步文案；这句
    描述是同一口径的第二处文案，评审判定它仍停在「至多」。内容文件没有金录像，故这条
    只是内容断言：两个键都在、都把「至多」去掉。
    """
    from sgs_web.descriptions import card_description, skill_description

    card_text = card_description("ice_sword")
    skill_text = skill_description("equip.ice_sword")
    assert card_text and skill_text, "两处寒冰剑文案都必须有内容，否则这条断言是空转"
    for text in (card_text, skill_text):
        assert "至多两张" not in text, f"寒冰剑不得再写「至多两张」：{text}"
        assert "两张" in text, f"寒冰剑文案要写清是两张：{text}"


def test_the_description_content_stays_out_of_the_engine_journal() -> None:
    """描述只住接入层内容文件：不进引擎数据、不进记录流（金录像因此不变）。"""
    from sgs.data.models import CardType

    from sgs_web.descriptions import (
        CARD_DESCRIPTIONS,
        DESCRIPTIONS_PATH,
        SKILL_DESCRIPTIONS,
    )

    assert DESCRIPTIONS_PATH.is_relative_to(REPO_ROOT / "tools" / "sgs_web"), (
        f"描述必须住接入层 tools/sgs_web/，实际 {DESCRIPTIONS_PATH}"
    )
    assert "description" not in CardType.model_fields, "描述不得进引擎的牌名数据模型"

    session = Session(seed=5, ai="random", timeout_seconds=None, player_count=4)
    journal = dump_journal(session.records())
    assert '"description"' not in journal, "记录流里出现了 description 键（会打翻金录像）"
    for text in (*CARD_DESCRIPTIONS.values(), *SKILL_DESCRIPTIONS.values()):
        assert text not in journal, f"描述全文进了记录流：{text[:24]}…"


def test_the_static_frontend_does_not_copy_the_description_text() -> None:
    """文案只有一份：服务端的 ``descriptions.json``；static/*.js 不抄任何全文。

    读取行为（牌面 / 技能按钮的悬停 title）归 ``tests/support/ui_smoke.mjs`` 的
    U1-04d / U1-04e——它跑真前端脚本 + 真服务端形状，不是源码字符串断言。
    """
    from sgs_web.descriptions import CARD_DESCRIPTIONS, SKILL_DESCRIPTIONS

    static = REPO_ROOT / "tools" / "sgs_web" / "static"
    scripts = {path.name: path.read_text(encoding="utf-8") for path in static.glob("*.js")}
    for key, text in {**CARD_DESCRIPTIONS, **SKILL_DESCRIPTIONS}.items():
        for name, source in scripts.items():
            assert text not in source, f"{name} 硬编码了 {key} 的描述全文（必须由服务端下发）"


# ---------------------------------------------------------------------------
# U1-05 叙事契约：观察者过滤后的简易事件流（服务端生成，前端只渲染）
# ---------------------------------------------------------------------------

NARRATIVE_LINE = frozenset({"line", "divider"})
"""叙事条目的两种形态；前端只按 ``segments[].cls`` 上色，不再自己判断事实。"""

NARRATIVE_SEGMENT_CLASSES = frozenset(
    {
        "seat",
        "hero",
        "card",
        "card-fire",
        "card-thunder",
        "skill",
        "act",
        "dmg",
        "dmg-fire",
        "dmg-thunder",
    }
)
"""跨线颜色契约（U1-05）：``segments[].cls`` 的闭集 = ``static/style.css`` 里 ``.nar-*`` 的类名。

新增一种颜色要同时改这里与 CSS；不断言的话，服务端拼错一个类名只会安静地掉回默认色。
"""


def _record_payload(record: object) -> dict:
    """一条记录 → JSON 形态（真记录走 ``record_json``，已下发的录像记录原样返回）。"""
    if isinstance(record, dict):
        return record
    from sgs.engine import record_json

    return json.loads(record_json(record))


def _narrative(records, *, viewer, heroes, pack_id="standard"):
    from sgs_web.narrative import build_narrative

    return build_narrative(
        records, viewer=viewer, heroes=tuple(heroes), pack_id=pack_id
    )


def _line_at(entries: list[dict], seq: int) -> dict | None:
    """某条记录对应的叙事行（同一 seq 只应有一行；分隔线与它共享 seq 时优先取 line）。"""
    matches = [entry for entry in entries if entry["seq"] == seq and entry["kind"] == "line"]
    return matches[0] if matches else None


def _line_text(entries: list[dict]) -> str:
    return "\n".join(entry["text"] for entry in entries if entry["kind"] == "line")


def test_the_narrative_is_a_flat_observer_readable_stream() -> None:
    """简易流 = 一条行动一行、只有人话：没有询问 / 帧 / 结算深度这些引擎话术。"""
    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)

    entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )

    assert entries, "完整一局必须生成叙事条目"
    assert {entry["kind"] for entry in entries} <= NARRATIVE_LINE
    for entry in entries:
        assert isinstance(entry["seq"], int) and entry["seq"] > 0, entry
        assert entry["text"], f"空条目：{entry}"
        if entry["kind"] == "line":
            assert entry["segments"], f"行没有分段：{entry}"
            assert "".join(seg["text"] for seg in entry["segments"]) == entry["text"], (
                f"text 与 segments 漂了：{entry}"
            )
            for segment in entry["segments"]:
                assert segment["cls"] in NARRATIVE_SEGMENT_CLASSES, segment
        else:
            assert "segments" not in entry, f"分隔线不该带颜色分段：{entry}"

    text = _line_text(entries)
    assert re.search(r"受到 \d+ 点伤害", text), f"没有伤害行：\n{text[:800]}"
    assert "发动【" in text, f"没有技能发动行：\n{text[:800]}"
    assert re.search(r"获得 \d+ 张牌|获得【", text), f"没有获得牌行：\n{text[:800]}"
    for forbidden in ("RESPOND", "SKILL_ACTIVATION", "PLAY_PHASE", "结算帧", "#"):
        assert forbidden not in text, f"叙事里漏出引擎话术 {forbidden!r}：\n{text[:800]}"


def test_the_narrative_uses_the_display_contract_for_cards_and_damage_colors() -> None:
    """火杀 / 雷杀显示「杀」且牌名 / 伤害数字分色（服务端查同一份展示契约）。"""
    from sgs.state import CardRef

    fire = CardRef(card_id=-9, type="fire_slash", name="火杀")
    thunder = CardRef(card_id=-10, type="thunder_slash", name="雷杀")

    def timing(seq: int, name: str, **extra) -> dict:
        return {
            "kind": "timing",
            "seq": seq,
            "timing": name,
            "frame_id": 1,
            "depth": 0,
            **extra,
        }

    def action(seq: int, kind: str, card: CardRef, targets: tuple[int, ...]) -> dict:
        return {
            "kind": "action",
            "seq": seq,
            "accepted": True,
            "action": {
                "kind": kind,
                "answers_seq": 0,
                "card": card.model_dump(mode="json"),
                "targets": list(targets),
            },
        }

    def hp(seq: int, seat: int, amount: int) -> dict:
        return {
            "kind": "delta",
            "seq": seq,
            "delta_type": "hp_change",
            "frame_id": 1,
            "depth": 0,
            "seat": seat,
            "before": 4,
            "after": 4 + amount,
            "amount": amount,
        }

    def query(seq: int, actor: int) -> dict:
        return {
            "kind": "query",
            "seq": seq,
            "query": {"kind": "PLAY_PHASE", "actor": actor, "prompt": ""},
        }

    records = [
        timing(1, "TURN_START", actor=0),
        timing(2, "PHASE_PLAY_START", actor=0),
        query(3, 0),
        action(4, "UseCard", fire, (1,)),
        timing(5, "DAMAGE_TAKE", actor=1, source=0, targets=[1], cards=[fire.model_dump(mode="json")]),
        hp(6, 1, -1),
        query(7, 1),
        action(8, "UseCard", thunder, (0,)),
        timing(9, "DAMAGE_TAKE", actor=0, source=1, targets=[0], cards=[thunder.model_dump(mode="json")]),
        hp(10, 0, -1),
    ]

    entries = _narrative(records, viewer=0, heroes=("guojia", "simayi"))
    use_fire = _line_at(entries, 4)
    use_thunder = _line_at(entries, 8)
    hurt_fire = _line_at(entries, 6)
    hurt_thunder = _line_at(entries, 10)
    assert use_fire is not None and hurt_fire is not None
    assert use_thunder is not None and hurt_thunder is not None

    assert "【杀】" in use_fire["text"] and "火杀" not in use_fire["text"]
    assert any(seg["text"] == "【杀】" and seg["cls"] == "card-fire" for seg in use_fire["segments"]), use_fire
    assert any(seg["text"] == "1" and seg["cls"] == "dmg-fire" for seg in hurt_fire["segments"]), hurt_fire
    assert any(seg["text"] == "【杀】" and seg["cls"] == "card-thunder" for seg in use_thunder["segments"]), use_thunder
    assert any(seg["text"] == "1" and seg["cls"] == "dmg-thunder" for seg in hurt_thunder["segments"]), hurt_thunder
    # 座位号 / 武将名 / 技能 / 动作的分色也在同一条契约里。
    assert any(seg["cls"] == "seat" for seg in hurt_fire["segments"]), hurt_fire
    assert any(seg["cls"] == "hero" and seg["text"].strip() == "司马懿" for seg in hurt_fire["segments"]), hurt_fire
    assert any(seg["cls"] == "act" for seg in hurt_fire["segments"]), hurt_fire


def test_the_narrative_hides_opponent_hand_and_deck_while_revealing_public_card_moves() -> None:
    """观察者过滤：牌堆 / 对手手牌只给张数，公开区的牌才给牌名；同一份记录换观察者会变。"""
    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    records = session.records()
    payloads = [_record_payload(record) for record in records]
    first_turn = next(
        payload["seq"]
        for payload in payloads
        if payload.get("kind") == "timing" and payload.get("timing") == "TURN_START"
    )

    entries = _narrative(
        records,
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    checked = 0
    for payload in payloads:
        if payload.get("kind") != "delta" or payload.get("delta_type") != "card_move":
            continue
        if payload["seq"] <= first_turn:
            continue  # 开局发牌：不进叙事（见模块注释）
        to_zone = payload["to_zone"]
        from_zone = payload["from_zone"]
        if to_zone["zone"] != "hand" or int(to_zone["seat"]) == session.human_seat:
            continue
        if from_zone["zone"] not in ("deck", "hand"):
            continue
        line = _line_at(entries, payload["seq"])
        assert line is not None, f"对手摸 / 拿牌的 {payload['seq']} 没有叙事行"
        assert "【" not in line["text"], f"对手手牌牌名漏进叙事：{line['text']}"
        assert re.search(r"获得 \d+ 张牌", line["text"]), line["text"]
        checked += 1
    assert checked >= 5, f"覆盖太少（只检查到 {checked} 次对手摸牌），断言谈不上机械"

    # 身份：开局逐座暗发；没有阵亡亮明时一条都不能出现。
    early = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    early_entries = _narrative(
        early.records(),
        viewer=early.human_seat,
        heroes=early.hero_assignments,
        pack_id=early.pack.pack_id,
    )
    early_text = _line_text(early_entries)
    for label in ("主公", "忠臣", "反贼", "内奸"):
        assert label not in early_text, f"暗置身份 {label} 漏进叙事：\n{early_text[:800]}"

    # 牌堆：洗牌记录不进叙事（顺序只归引擎）。
    shuffle_seqs = {
        payload["seq"] for payload in payloads if payload.get("delta_type") == "deck_shuffle"
    }
    assert shuffle_seqs, "真一局里应当有洗牌记录，否则这条断言是空转"
    assert all(entry["seq"] not in shuffle_seqs for entry in entries), "洗牌记录进了叙事"

    # 同一份记录、换观察者：别人手里转手的牌对第三个座位仍然只显示张数。
    hidden_give = next(
        payload
        for payload in payloads
        if payload.get("kind") == "delta"
        and payload.get("delta_type") == "card_move"
        and payload["from_zone"]["zone"] == "hand"
        and payload["to_zone"]["zone"] == "hand"
        and payload["from_zone"]["seat"] is not None
        and payload["to_zone"]["seat"] is not None
    )
    outsider = next(
        seat
        for seat in range(session.rule_config.player_count)
        if seat not in (hidden_give["from_zone"]["seat"], hidden_give["to_zone"]["seat"])
    )
    outsider_entries = _narrative(
        records,
        viewer=outsider,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    outsider_line = _line_at(outsider_entries, hidden_give["seq"])
    assert outsider_line is not None and "【" not in outsider_line["text"], outsider_line
    receiver_entries = _narrative(
        records,
        viewer=hidden_give["to_zone"]["seat"],
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    receiver_line = _line_at(receiver_entries, hidden_give["seq"])
    assert receiver_line is not None and "【" in receiver_line["text"], (
        f"收牌的人看得到自己拿到的牌：{receiver_line}"
    )


def test_the_narrative_has_round_and_phase_dividers_before_the_lines_they_introduce() -> None:
    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )

    dividers = [entry for entry in entries if entry["kind"] == "divider"]
    assert dividers, "简易流必须有轮次 / 阶段分隔"
    assert any("第 1 轮" in entry["text"] for entry in dividers), [e["text"] for e in dividers]
    assert any("出牌阶段" in entry["text"] for entry in dividers), [e["text"] for e in dividers]

    # 分隔线在它所引出的第一行**之前**（同一批增量里也不会反过来）。
    phase_index = next(
        index
        for index, entry in enumerate(entries)
        if entry["kind"] == "divider" and "出牌阶段" in entry["text"]
    )
    assert phase_index + 1 < len(entries)
    after = entries[phase_index + 1]
    assert after["seq"] >= entries[phase_index]["seq"], (entries[phase_index], after)

    # 轮次按 TURN_START 的“同一座位再次出现”累计——与服务端顶栏 / 前端 roundNumber 同口径：
    # 一个座位第一次行动时轮次不变（同一轮里每个座位都标「第 1 轮」），座位重复才进下一轮。
    round_dividers = [
        entry["text"] for entry in entries if entry["kind"] == "divider" and "轮" in entry["text"]
    ]
    assert round_dividers[0].startswith("第 1 轮"), round_dividers[:3]
    assert any(text.startswith("第 2 轮") for text in round_dividers), round_dividers[-5:]
    # 第 1 轮的标号只在真正回到第一个座位之前出现：每名玩家一次。
    first_round = [text for text in round_dividers if text.startswith("第 1 轮")]
    assert len(first_round) == session.rule_config.player_count, first_round


def test_the_card_seat_and_skill_names_come_from_the_data_tables() -> None:
    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    text = _line_text(entries)

    from sgs.data import load_heroes

    table = load_heroes(session.pack.pack_id)
    names = []
    for hero_id in session.hero_assignments:
        hero = table.by_id(hero_id) if hero_id else None
        if hero is not None:
            names.append(hero.name)
    assert names, "本局必须有武将"
    for name in names:
        assert name in text, f"武将名 {name} 没进叙事：\n{text[:800]}"

    # 技能名从武将面数据来（不是 skill_id / 描述全文）。
    skill_actions = [
        record
        for record in session.records()
        if getattr(record, "kind", None) == "action"
        and getattr(getattr(record, "action", None), "kind", None) == "ActivateSkill"
    ]
    assert skill_actions, "本局必须有技能发动"
    assert "观星" in text, f"技能中文名没进叙事：\n{text[:800]}"


def test_the_session_view_carries_incremental_narrative_by_the_record_cursor() -> None:
    """HTTP 载荷：`after` 是**记录 seq 游标**；下一批只带 seq 更大的叙事，拼起来等于全量。"""
    from sgs_web.serve import GameRegistry

    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    registry = GameRegistry(runs_dir="/tmp/unused")
    first = registry.view(session, after=-1, logs_after=0)
    assert first["narrative"], "建局响应必须带叙事"
    assert all(entry["seq"] > -1 for entry in first["narrative"])

    pending = session.pending
    assert pending is not None
    session.submit_answer(pending.legal_actions[0])
    after = first["cursor"]
    second = registry.view(session, after=after, logs_after=0)

    assert all(entry["seq"] > after for entry in second["narrative"]), (
        "增量里出现了已下发过的叙事条目"
    )
    full = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    merged = first["narrative"] + second["narrative"]
    assert [entry["seq"] for entry in merged] == [entry["seq"] for entry in full], (
        "两批拼接后的序号 / 顺序与全量不一致"
    )
    assert [entry["text"] for entry in merged] == [entry["text"] for entry in full]


def test_the_replay_payload_uses_the_same_observer_filtered_narrative(tmp_path: Path) -> None:
    """录像接口也下发叙事（同一份生成器）；按人类座位过滤，不把对手手牌写进条目。"""
    from sgs_web.replay import load_run, save_run

    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    save_run(tmp_path, session)
    run = load_run(tmp_path, next(path.stem for path in tmp_path.glob("*.jsonl")))

    assert run["narrative"], "回放载荷必须带叙事条目"
    expected = _narrative(
        run["records"],
        viewer=run["meta"]["human_seat"],
        heroes=run["meta"]["heroes"],
        pack_id=run["meta"]["pack_id"],
    )
    assert run["narrative"] == expected, "录像叙事与生成器 / 人类座位不一致"

    # 载入的 JSON 记录与引擎模型走的是同一条生成路径：两侧结果逐条相同。
    engine_entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    assert run["narrative"] == engine_entries, "模型记录与 JSON 记录生成了不同的叙事"

    # 观察者过滤在回放载入路径上也成立：对手摸牌只给张数。
    records = run["records"]
    checked = 0
    for record in records:
        if record.get("kind") != "delta" or record.get("delta_type") != "card_move":
            continue
        if record["to_zone"]["zone"] != "hand":
            continue
        if record["to_zone"]["seat"] == run["meta"]["human_seat"]:
            continue
        if record["from_zone"]["zone"] not in ("deck", "hand"):
            continue
        line = _line_at(run["narrative"], record["seq"])
        if line is None:
            continue  # 开局发牌不进叙事
        assert "【" not in line["text"], line
        checked += 1
    assert checked >= 5, f"回放观测过滤覆盖太少：{checked}"


def test_a_replay_without_a_human_seat_is_not_omniscient(tmp_path: Path) -> None:
    """没有人类座位的录像（全是 AI）不能因为"查不到查看者"退化成全知。

    回放页的简易档在 U1 仍按观察者过滤；``viewer=-1`` 是一个不存在的座位——公开区与
    阵亡亮明照写，隐藏手牌 / 牌堆顺序一条都不写。U2 的上帝视角开关才传 ``None``。
    """
    from sgs_web.narrative import NO_VIEWER
    from sgs_web.replay import load_run, save_run

    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    save_run(tmp_path, session)
    name = next(path.stem for path in tmp_path.glob("*.jsonl"))
    meta_path = tmp_path / f"{name}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["human_seat"] = None
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )

    run = load_run(tmp_path, name)

    expected = _narrative(
        run["records"], viewer=NO_VIEWER, heroes=meta["heroes"], pack_id=meta["pack_id"]
    )
    assert run["narrative"] == expected, "无人类座位时回放简易流没有走无视角过滤"
    assert run["narrative"] != _narrative(
        run["records"], viewer=None, heroes=meta["heroes"], pack_id=meta["pack_id"]
    ), "NO_VIEWER 与上帝视角（None）必须是两套结果"

    checked = 0
    for record in run["records"]:
        if record.get("kind") != "delta" or record.get("delta_type") != "card_move":
            continue
        if record["to_zone"]["zone"] != "hand":
            continue
        if record["from_zone"]["zone"] not in ("deck", "hand"):
            continue
        line = _line_at(run["narrative"], record["seq"])
        if line is None:
            continue  # 开局发牌不进叙事
        assert "【" not in line["text"], line
        checked += 1
    assert checked >= 5, f"无人类座位的过滤覆盖太少：{checked}"


def test_the_narrative_game_end_names_the_winner_once() -> None:
    """2 人局（无身份）的终局行不能把同一个赢家写两遍（"座位N 获胜 · 获胜座位：座位N"）。"""
    session = Session(seed=7, ai="random", timeout_seconds=None, player_count=2)
    _play_to_end(session)

    entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    end = next((entry for entry in entries if entry["text"].startswith("对局结束")), None)
    assert end is not None, "完整一局必须有终局行"
    assert end["text"].count("获胜座位") == 1, end["text"]
    assert end["text"].count("获胜") == 1, end["text"]


def test_the_narrative_only_reveals_identities_the_journal_revealed() -> None:
    """身份行只由 ``identity_reveal``（阵亡亮明）驱动；开局暗发的座位一条都不写。

    与 :func:`test_the_narrative_hides_opponent_hand_and_deck_while_revealing_public_card_moves`
    的早期快照互补：那条钉"还没亮明时没有身份"，这条钉"亮明之后只写亮明了的那一座"。
    """
    from sgs.data import IDENTITY_LABELS

    session = Session(seed=11, ai="random", timeout_seconds=None, player_count=4)
    _play_to_end(session)
    entries = _narrative(
        session.records(),
        viewer=session.human_seat,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    payloads = [_record_payload(record) for record in session.records()]
    revealed = {
        payload["seat"]: payload["identity"]
        for payload in payloads
        if payload.get("kind") == "delta" and payload.get("delta_type") == "identity_reveal"
    }
    assert revealed, "这一局必须有阵亡亮明，否则这条断言是空转"
    label_by_value = {
        getattr(identity, "value", str(identity)): label
        for identity, label in IDENTITY_LABELS.items()
    }

    lines = [entry for entry in entries if "亮明身份" in entry["text"]]
    assert len(lines) == len(revealed), (
        f"亮明行数 {len(lines)} 与 identity_reveal 数 {len(revealed)} 不一致："
        f"{[line['text'] for line in lines]}"
    )
    for line in lines:
        match = re.match(r"座位(\d+) .+亮明身份：(.+)$", line["text"])
        assert match is not None, line["text"]
        seat = int(match.group(1))
        assert seat in revealed, f"未亮明的座位 {seat} 出现在叙事里：{line['text']}"
        assert match.group(2) == label_by_value[revealed[seat]], line["text"]

    for seat in range(session.rule_config.player_count):
        if seat in revealed:
            continue
        assert not any(
            f"座位{seat}" in line["text"] and "亮明身份" in line["text"] for line in lines
        ), f"座位 {seat} 没有亮明记录，却有亮明行"


# ---------------------------------------------------------------------------
# U2-11（D4 / D10）：【五谷丰登】的服务端派生状态
#
# 这是**最高缝**：一次断言同时覆盖"亮牌弹窗能看见什么"（完整亮牌 + 获得者 + 目标条 +
# 当前目标）与"公开处理区不被查看者过滤误伤"。派生只读结算栈与记录流，**不改记录流**：
# 金录像 / 记录流字节在派生前后逐字节相等（本段最后一条钉住）。
# ---------------------------------------------------------------------------


class _HarvestDriver:
    """4 座构造局面的脚本参与者：座位 0 打五谷；可选在目标 0 上用无懈（及反无懈）。

    只用于构造"真会话走到五谷取牌步"的前置，不测规则本身；无懈窗口的识别用
    "栈顶帧是不是无懈"（反无懈窗口里栈顶是那张无懈的 USE / RESOLVE 帧）。
    """

    def __init__(self, *, nullify: bool, reverse: bool) -> None:
        self.nullify = nullify
        self.reverse = reverse
        self.used: set[str] = set()

    def respond(self, engine, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.type == "harvest":
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == QueryKind.SELECT_CARDS:
            return query.legal_actions[0]
        if query.kind == QueryKind.RESPOND and query.cause is WindowCause.NULLIFY:
            top = engine.stack.frames[-1] if engine.stack.frames else None
            reversing = getattr(getattr(top, "data", {}).get("card"), "type", None) == "nullify"
            if (
                self.nullify
                and query.actor == 1
                and not reversing
                and getattr(query, "target", None) == 0
                and "first" not in self.used
            ):
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "nullify":
                        self.used.add("first")
                        return action
            if self.reverse and query.actor == 2 and reversing and "reverse" not in self.used:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == "nullify":
                        self.used.add("reverse")
                        return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _harvest_game(*, nullify: bool = False, reverse: bool = False):
    """构造一局 4 座局面并返回 ``(engine, driver)``：座位 0 手牌是五谷，牌堆有序。

    座位 1 / 2 的手牌按需放无懈（反无懈需要两张）。牌堆 = 其余实体牌，顺序即数据表顺序，
    故亮出的牌可预测；测试只比较"记录里亮出的集合 / 顺序"与载荷，不硬编码牌 id。
    """
    from sgs.data import RuleConfig as _RuleConfig

    pack = _context_pack()
    harvest = cards_of_type(pack, "harvest")[0]
    nullifies = cards_of_type(pack, "nullify")
    used = {harvest}
    seats = [SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec(), SeatSpec()]
    if nullify or reverse:
        used.add(nullifies[0])
        seats[1] = SeatSpec(hand=(nullifies[0],))
    if reverse:
        used.add(nullifies[1])
        seats[2] = SeatSpec(hand=(nullifies[1],))
    deck = tuple(card_id for card_id in range(len(pack.deck.cards)) if card_id not in used)
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck,
        turn_seat=0,
        rule_config=_RuleConfig(first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    return engine, _HarvestDriver(nullify=nullify, reverse=reverse)


def _drive_to_harvest_pick(engine, driver, seat: int):
    """推进到**指定座位**的五谷取牌询问并返回它；其余询问由 ``driver`` 脚本应答。"""
    for _ in range(4_000):
        query = engine.pending
        if query is not None and query.kind == QueryKind.SELECT_CARDS and query.actor == seat:
            return query
        if query is None:
            engine._step()
            if engine.finished:  # pragma: no cover - 构造局面不会走到终局
                raise AssertionError(f"还没走到座位 {seat} 的五谷取牌步，对局已结束")
            continue
        engine.submit(driver.respond(engine, query))
    raise AssertionError(f"步数上限内没有走到座位 {seat} 的五谷取牌步")


def _web_session_for(engine, *, human_seat: int) -> Session:
    """把**构造出来的引擎**包进一个真 :class:`Session`（``pending_payload`` 需要真会话）。

    只替换 ``engine``：``human_seat`` / ``pack`` 由会话自己的构造参数给；``participants``
    在本段之后不再使用（驱动由 :func:`_drive_to_harvest_pick` 负责）。
    """
    session = Session(
        seed=0,
        player_count=4,
        ai="random",
        human_seat=human_seat,
        hero_pool=None,
        timeout_seconds=None,
        pack=_context_pack(),
    )
    session.engine = engine
    return session


def test_a_real_session_derives_the_full_harvest_state_and_the_chain_key() -> None:
    """真会话（4 座构造局面）走到目标 1 的取牌步：载荷带齐亮牌 / 获得者 / 目标条 / 当前目标。

    目标 0 已取走第一张亮牌；目标 1 正在取牌。完整亮牌集合必须来自记录里那条
    ``deck → processing`` 的移动（帧私有 ``revealed`` 会每取一张缩短一次），位置不能变。
    """
    engine, driver = _harvest_game()
    first = _drive_to_harvest_pick(engine, driver, 0)
    first_payload = pending_payload(_web_session_for(engine, human_seat=0))
    first_harvest = first_payload["harvest"]
    assert first_harvest is not None
    key = first_harvest["key"]
    revealed_ids = [card["card_id"] for card in first_harvest["revealed"]]
    assert len(revealed_ids) == 4, "X = 存活角色数（4 座）"
    assert [card["taken_by"] for card in first_harvest["revealed"]] == [None] * 4
    assert [target["status"] for target in first_harvest["targets"]] == [
        "current", "pending", "pending", "pending",
    ]
    assert first_harvest["current"] == 0
    assert first_harvest["active"] is True

    # 目标 0 取第一张（= 第一张剩余亮牌）；同时钉"超时取第一张剩余亮牌"的服务端口径。
    fallback = fallback_action(first)
    assert isinstance(fallback, SelectCards), fallback
    assert fallback.picks == (0,)
    engine.submit(fallback)

    second = _drive_to_harvest_pick(engine, driver, 1)
    session = _web_session_for(engine, human_seat=1)
    before = dump_journal(session.records())
    payload = pending_payload(session)
    after = dump_journal(session.records())

    harvest = payload["harvest"]
    assert harvest is not None, "五谷结算中必须下发派生状态"
    assert harvest["key"] == key, "同一个链键跨 X 个目标（不同 seq / actor）不变"
    assert harvest["title"] == "五谷丰登", "标题读牌面名，前端不抄第二份"
    assert harvest["hint"] == HARVEST_REQUIREMENT
    assert harvest["source"] == 0
    assert [card["card_id"] for card in harvest["revealed"]] == revealed_ids, (
        "完整亮牌集合 + 亮牌顺序（含已被取走的）"
    )
    assert [card["taken_by"] for card in harvest["revealed"]] == [0, None, None, None]
    assert harvest["revealed"][0]["description"], "卡面展示字段随亮牌下发（悬停可读）"
    assert [target["seat"] for target in harvest["targets"]] == [0, 1, 2, 3]
    assert [target["status"] for target in harvest["targets"]] == [
        "taken", "current", "pending", "pending",
    ]
    assert harvest["targets"][0]["card"] == revealed_ids[0], "目标条带已拿走的牌 id"
    assert harvest["targets"][0]["seat"] == 0
    assert harvest["targets"][-1]["cancelled"] is False
    assert harvest["targets"][-1]["reason"] is None, "'先指定后取消'的保留位暂为 null"
    assert harvest["current"] == 1
    assert [slot["card"]["card_id"] for slot in payload["slots"]] == revealed_ids[1:], (
        "当前取牌面 = 剩余亮牌（按亮牌顺序）"
    )
    fallback = fallback_action(second)
    assert isinstance(fallback, SelectCards)
    assert fallback.picks == (0,), "超时 = 第一张剩余亮牌（服务端 fallback_action）"
    assert second.slots[0].card.card_id == revealed_ids[1]

    assert payload["context"]["requirement"] == HARVEST_REQUIREMENT
    assert payload["context"]["name"] == "五谷丰登"
    assert payload["harvest"]["revealed"][0]["card_id"] == revealed_ids[0]
    assert after == before, "派生是只读的：调用前后记录流逐字节不变"

    # 409 里附带的裸 Query 拿不到引擎 ⇒ 键仍在、值为 None（前端 fail-closed 不开窗）。
    bare = pending_payload(second, human_seat=1)
    assert bare["harvest"] is None


def test_a_nullify_window_in_a_harvest_resolution_still_carries_the_active_harvest_state() -> None:
    """D4 / review-fix 的生产时序证据：无懈窗口发生在**亮牌之后**。

    引擎的 ``reveal_harvest`` 落在 ``USE_AFTER_DESIGNATE_TARGET``，收集式无懈窗口落在
    ``RESOLVE_BEFORE_EFFECT``——所以无懈 / 反无懈的 ``RESPOND`` pending 上 ``harvest``
    仍然活跃、还有当前目标。前端"整段结算持续显示"因此不是为不可达状态加的 hack。
    若将来把亮牌挪到无懈窗口之后，这条会红：到时持久展示路径必须重新评估。
    """
    engine, driver = _harvest_game(nullify=True)
    window = None
    for _ in range(400):
        pending = engine.pending
        if pending is None:
            engine._step()
            if engine.finished:  # pragma: no cover - 构造局面不会走到终局
                break
            continue
        if pending.kind == QueryKind.RESPOND and pending.cause is WindowCause.NULLIFY:
            window = pending
            break
        engine.submit(driver.respond(engine, pending))
    assert window is not None, "没走到五谷的无懈窗口"

    harvest = harvest_state(engine)
    assert harvest is not None, "无懈窗口必须已经在亮牌帧之后"
    assert harvest["active"] is True
    assert harvest["revealed"], "无懈窗口打开时亮牌已经发生（D4 的持续显示依赖这一点）"
    assert harvest["current"] is not None

    payload = pending_payload(_web_session_for(engine, human_seat=window.actor))
    assert payload["kind"] == "RESPOND"
    assert payload["cause"] == "NULLIFY"
    assert payload["harvest"] is not None, "RESPOND 载荷同样要带 harvest（前端持久显示读它）"
    assert payload["harvest"]["key"] == harvest["key"]
    assert payload["harvest"]["current"] == harvest["current"]


@pytest.mark.parametrize(("reverse", "expected"), [(False, "invalid"), (True, "taken")])
def test_the_harvest_state_tells_a_nullified_target_from_a_reverse_nullified_one(
    reverse: bool, expected: str
) -> None:
    """被无懈（最终没拿到）⇒ ``invalid``；无懈后被反无懈救回（照常拿牌）⇒ ``taken``。

    两者都由**记录**派生：目标处理完了没有（帧游标）+ 有没有它的 ``processing → hand``
    移动；不解析无懈链，故反无懈救回天然落在正常态（D10 的判定口径）。
    """
    engine, driver = _harvest_game(nullify=True, reverse=reverse)
    query = _drive_to_harvest_pick(engine, driver, 1)
    assert query.actor == 1
    payload = pending_payload(_web_session_for(engine, human_seat=1))
    harvest = payload["harvest"]
    assert harvest is not None
    first = harvest["targets"][0]
    assert first["seat"] == 0
    assert first["status"] == expected, harvest["targets"]
    if reverse:
        assert first["card"] == harvest["revealed"][0]["card_id"]
        assert harvest["revealed"][0]["taken_by"] == 0
        assert harvest["revealed"][1]["taken_by"] is None, "其余亮牌还没被拿走"
    else:
        assert first["card"] is None
        assert harvest["revealed"][0]["taken_by"] is None, "被无懈的目标一张都没拿"
    assert [target["status"] for target in harvest["targets"][1:]] == [
        "current", "pending", "pending",
    ]
    assert all(target["cancelled"] is False for target in harvest["targets"])


def test_the_public_processing_slots_survive_foreign_masking_but_watched_faces_do_not() -> None:
    """D4 / D10 的两难分开钉：**处理区是公开的**（AI 目标拿牌时亮牌不能被抹），
    **观看授予**仍只在应答者手里（【遗计】牌堆顶的牌面继续 fail-closed，票 12 不改判）。"""
    engine, driver = _harvest_game()
    _drive_to_harvest_pick(engine, driver, 1)
    session = _web_session_for(engine, human_seat=0)  # 查看者 0，询问者 1 ⇒ foreign
    payload = pending_payload(session)
    assert payload["harvest"] is not None and payload["harvest"]["revealed"]
    assert all(slot["card"] is not None for slot in payload["slots"]), (
        "五谷的处理区槽位是公开事实，foreign 下也要带牌面"
    )
    assert payload["revealed"] == [], "观看授予仍整份抹掉"

    # 非处理区的槽位 + 观看授予：同一条 masking 仍要隐藏牌面。
    state = engine.state
    pack = _context_pack()
    hand_card = cards_of_type(pack, "slash")[0]
    processing_card = state.processing[0]
    slots = (
        Slot(ref=ZoneRef(Zone.PROCESSING), card=state.card(processing_card)),
        Slot(ref=ZoneRef(Zone.HAND, 1), card=state.card(hand_card)),
    )
    query = SelectCardsQuery(
        seq=99,
        actor=1,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        prompt="请从选择面里选择一张牌。",
        legal_actions=(),
        view=project(state, 1),
        spec=SelectionSpec(zones=(Zone.PROCESSING, Zone.HAND), total=(1, 1)),
        slots=slots,
        revealed=(state.card(hand_card),),
    )
    masked = pending_payload(query, human_seat=None)
    assert masked["slots"][0]["card"]["card_id"] == processing_card
    assert masked["slots"][1]["card"] is None, "别人的手牌槽位照旧 only-position"
    assert masked["revealed"] == [], "观看授予照旧不下发"


def test_harvest_state_is_none_when_no_harvest_frame_is_on_the_stack() -> None:
    """没有五谷结算帧 ⇒ ``None``；调用方（前端）据此 fail-closed 不开亮牌弹窗。"""
    engine, driver = _harvest_game()
    _drive_to_harvest_pick(engine, driver, 1)
    assert harvest_state(engine) is not None
    # 取完最后一张后帧会出栈：这里用"把栈清空"模拟结算结束后的状态（同一函数、只读）。
    frames = engine.stack.frames
    saved = list(frames)
    frames.clear()
    try:
        assert harvest_state(engine) is None
    finally:
        frames.extend(saved)


# ---------------------------------------------------------------------------
# M5-08a：火攻界面（真会话 / 记录流 / 投影 / 叙事）
#
# 引擎侧（M5-06）已经保证：目标过滤 `has_hand_cards`、目标亮 1 张（`card_reveal` 公开
# delta）、同花色弃牌可选（`Pass`）、无同花色时**不发询问**（自动放弃）。本段钉的是
# **界面读取方**：目标过滤只出现在有手牌的角色上（含自己）、亮牌走 U2 的亮牌模板、
# 亮出的那一张才公开、弃一张同花色 = 1 点火焰伤害、放弃 / 无同花色 = 0 伤害且事件流
# 可读、手牌区不残留「已知」。
# ---------------------------------------------------------------------------

_FIRE_PACK = None


def _fire_pack():
    """军争 160 档（惰性加载；火攻不在此档之外）。"""
    global _FIRE_PACK
    if _FIRE_PACK is None:
        from sgs.data import load_content_pack

        _FIRE_PACK = load_content_pack(deck_profile="standard_ex_160")
    return _FIRE_PACK


def _fire_card_id() -> int:
    return cards_of_type(_fire_pack(), "fire_attack")[0]


def _fire_suit_cards(suit) -> list[int]:
    return [
        index
        for index, card in enumerate(_fire_pack().deck.cards)
        if card.suit is suit and card.type != "fire_attack"
    ]


class _FireDriver:
    """火攻脚本：座位 1 = 目标（亮牌），其余 AI 座位按最小合法应答。

    只用于把**真会话**推到界面要读的那一步，不测规则本身；人类座位的询问由用例自己答。
    """

    def __init__(self, *, reveal=None, discard=None) -> None:
        self.reveal = reveal
        self.discard = discard

    def _pick(self, query, wanted):
        for action in query.legal_actions:
            if not isinstance(action, SelectCards) or len(action.picks) != 1:
                continue
            slot = query.slots[action.picks[0]]
            card = slot.card
            if card is None:
                continue
            if wanted(card):
                return action
        return None

    def respond(self, query):  # noqa: ANN001 - Participant 协议
        return self.answer(query)

    def answer(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, EndPhase):
                    return action
            return query.legal_actions[0]
        if query.kind == QueryKind.SELECT_CARDS:
            if self.reveal is not None:
                action = self._pick(query, lambda card: card.card_id == self.reveal)
                if action is not None:
                    return action
            if self.discard is not None:
                action = self._pick(query, lambda card: card.card_id == self.discard)
                if action is not None:
                    return action
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


class _FireSpyEngine(Engine):
    """记下每一次压栈的 ``DAMAGE`` 帧：属性（FIRE）与渠道牌只能这样从真会话观测。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.damage_frames: list[dict] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        if kind is FrameKind.DAMAGE:
            self.damage_frames.append(dict(data))
        return frame


class _HaltSeat:
    """人类座位：把询问交给 ``Session.pending``，由用例显式应答（与产品同一条挂起驱动）。"""

    def respond(self, query):  # noqa: ANN001
        from sgs_web.session import Halt

        raise Halt(query)


def _fire_session(
    *,
    user_hand: tuple[int, ...],
    target_hand: tuple[int, ...] = (),
    other_hands: tuple[tuple[int, ...], ...] = ((), ()),
    top: tuple[int, ...] = (),
    human_seat: int = 0,
    spy: bool = False,
):
    """4 座构造局面：座位 0 = 火攻使用者、1 = 首个目标、2 / 3 = 其它（默认空手牌）。

    ``other_hands`` 给座位 2 / 3 的手牌；空手牌座位用来钉"不可指定"。
    """
    from sgs.data import RuleConfig as _RuleConfig

    pack = _fire_pack()
    fire = _fire_card_id()
    used = {fire, *user_hand, *target_hand, *top}
    for hand in other_hands:
        used.update(hand)
    seats = [
        SeatSpec(hp=4, hand=(fire, *user_hand)),
        SeatSpec(hp=4, hand=target_hand),
        SeatSpec(hp=4, hand=other_hands[0] if other_hands else ()),
        SeatSpec(hp=4, hand=other_hands[1] if len(other_hands) > 1 else ()),
    ]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=(*top, *tuple(i for i in range(len(pack.deck.cards)) if i not in used)),
        turn_seat=0,
        rule_config=_RuleConfig(first_seat=0),
    )
    from sgs_web.session import Session as _Session

    session = _Session(
        seed=1,
        player_count=4,
        ai="random",
        human_seat=human_seat,
        hero_pool=None,
        timeout_seconds=None,
        pack=pack,
    )
    engine_cls = _FireSpyEngine if spy else Engine
    session.engine = engine_cls.from_state(state, seed=1)
    session.participants = tuple(
        _HaltSeat() if seat == human_seat else _FireDriver(reveal=None)
        for seat in range(4)
    )
    session.drive()
    return session


def _step_until(session: Session, predicate, answer=None):
    """推进到 ``predicate(pending)`` 成立的询问；其余询问按 ``answer`` 应答。"""
    for _ in range(500):
        pending = session.pending
        if pending is None:
            break
        if predicate(pending):
            return pending
        session.submit_answer(answer(pending) if answer is not None else _FireDriver().answer(pending))
    raise AssertionError("500 步内没有走到目标询问")


def _fire_use_action(pending, *, target: int):
    fire = _fire_card_id()
    for action in pending.legal_actions:
        if isinstance(action, UseCard) and action.card.card_id == fire and action.targets == (target,):
            return action
    raise AssertionError(f"出牌阶段没有对座位 {target} 使用火攻的候选")


def _reveal_deltas(session: Session) -> list[dict]:
    out = []
    for record in session.records():
        payload = _record_payload(record)
        if payload.get("kind") == "delta" and payload.get("delta_type") == "card_reveal":
            out.append(payload)
    return out


def test_fire_attack_target_index_lists_only_seats_with_hand_cards_including_self() -> None:
    """第 0 层目标过滤的读取方：候选索引的池只有有手牌的角色（含自己），空手牌不可点。"""
    spade = _fire_suit_cards(Suit.SPADE)[0]
    session = _fire_session(
        user_hand=(spade,),
        target_hand=(_fire_suit_cards(Suit.HEART)[0],),
        other_hands=(()),  # 座位 2 空手牌
    )
    pending = session.pending
    assert pending is not None and pending.kind == QueryKind.PLAY_PHASE
    payload = pending_payload(session)
    entry = payload["candidate_index"][str(_fire_card_id())]
    assert entry["pool"] == [0, 1], "只有有手牌的角色在池里（座位 2 空手牌）"
    assert entry["combos"] == [[0], [1]], "可指定自己；空手牌座位 2 没有候选"
    assert entry["min_targets"] == 1 and entry["max_targets"] == 1

    snapshot = visible_snapshot(session)
    assert snapshot["players"][2]["hand_count"] == 0


def test_the_human_targets_fire_attack_reveal_step_uses_the_reveal_dialog_without_leaking() -> None:
    """人类被指定：亮牌步 = U2 亮牌模板（dialog=reveal），只有本人的手牌；外人拿不到牌面。"""
    spade = _fire_suit_cards(Suit.SPADE)[0]
    heart = _fire_suit_cards(Suit.HEART)[0]
    session = _fire_session(user_hand=(spade, heart), target_hand=())
    # 对自己使用：在候选区里把目标改成 0（用法仍原样取自 legal_actions）。
    use = _fire_use_action(session.pending, target=0)
    session.submit_answer(use)
    reveal = _step_until(
        session,
        lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0,
    )
    assert reveal is not None
    payload = pending_payload(session)
    assert payload["dialog"] == "reveal", "人类被指定 ⇒ 亮牌模板（四类弹窗之一）"
    assert payload["harvest"] is None, "火攻没有五谷的 harvest 状态"
    assert payload["context"]["name"] == "火攻"
    assert "亮出" in payload["context"]["requirement"]
    own_hand = [card["card_id"] for card in payload["view"]["self_hand"]]
    revealed = [card["card_id"] for card in payload["revealed"]]
    assert revealed == [slot["card"]["card_id"] for slot in payload["slots"]]
    assert set(revealed) == set(own_hand), "亮牌窗里是本人手牌；牌面只发给询问者本人"
    assert all(card["description"] for card in payload["revealed"]), "卡面契约随亮牌下发"

    # 外人的载荷：不是询问者 ⇒ 没有 dialog / revealed，槽位只剩位置。
    foreign = pending_payload(session.pending, human_seat=1)
    assert foreign["dialog"] is None
    assert foreign["revealed"] == []
    assert foreign["slots"] and all(slot["card"] is None for slot in foreign["slots"])


def test_the_human_targets_reveal_is_public_and_leaves_no_known_marker() -> None:
    """亮牌只有那一张成为公开事实；未亮出的手牌不泄露；结算后没有「已知」残留。"""
    spade = _fire_suit_cards(Suit.SPADE)[0]
    heart = _fire_suit_cards(Suit.HEART)[0]
    session = _fire_session(user_hand=(spade, heart), target_hand=(), other_hands=((spade,),))
    session.submit_answer(_fire_use_action(session.pending, target=0))
    reveal = _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)
    chosen = next(
        action
        for action in reveal.legal_actions
        if isinstance(action, SelectCards)
        and reveal.slots[action.picks[0]].card is not None
        and reveal.slots[action.picks[0]].card.card_id == spade
    )
    session.submit_answer(chosen)
    _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)

    reveals = _reveal_deltas(session)
    assert len(reveals) == 1, "亮牌只落一条 card_reveal"
    assert reveals[0]["seat"] == 0 and reveals[0]["card"]["card_id"] == spade
    assert reveals[0]["card"]["name"] == session.engine.state.card(spade).name

    snapshot = visible_snapshot(session)
    for player in snapshot["players"]:
        assert "known" not in player and "revealed_cards" not in player, (
            "手牌区不写「已知」状态"
        )
        if player["seat"] != session.human_seat:
            assert "self_hand" not in player
    shown = visible_record(next(
        record
        for record in session.records()
        if getattr(record, "delta_type", None) == "card_reveal"
    ))
    assert shown["card"]["display_name"] == session.engine.state.card(spade).name
    assert shown["card"]["description"], "中央舞台的牌面沿展示契约（悬停可读）"


def test_fire_attack_same_suit_discard_deals_one_fire_damage() -> None:
    """点一张同花色手牌 = 一次真实提交 → 恰 1 点火焰伤害；亮牌进事件流 / 叙事。"""
    spades = _fire_suit_cards(Suit.SPADE)
    hearts = _fire_suit_cards(Suit.HEART)
    session = _fire_session(
        user_hand=(spades[0], spades[1], hearts[0]),
        target_hand=(),
        spy=True,
    )
    session.submit_answer(_fire_use_action(session.pending, target=0))
    reveal = _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)
    reveal_action = next(
        action
        for action in reveal.legal_actions
        if isinstance(action, SelectCards)
        and reveal.slots[action.picks[0]].card is not None
        and reveal.slots[action.picks[0]].card.card_id == spades[0]
    )
    session.submit_answer(reveal_action)

    discard = _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)
    payload = pending_payload(session)
    assert payload["spec"]["optional"] is True
    assert payload["context"]["name"] == "火攻"
    assert "同花色" in payload["context"]["requirement"]
    assert payload["reveal"]["seat"] == 0
    assert payload["reveal"]["card"]["card_id"] == spades[0], "使用者看得见亮出的那一张"
    assert [slot["card"]["card_id"] for slot in payload["slots"]] == [spades[0], spades[1]], (
        "选择面只铺同花色手牌"
    )
    assert any(action["kind"] == "Pass" for action in payload["legal_actions"])

    discard_action = next(
        action
        for action in discard.legal_actions
        if isinstance(action, SelectCards)
        and discard.slots[action.picks[0]].card is not None
        and discard.slots[action.picks[0]].card.card_id == spades[1]
    )
    session.submit_answer(discard_action)
    _step_until(session, lambda p: p.kind == QueryKind.PLAY_PHASE and p.actor == 0)

    from sgs.effects import DamageNature

    assert session.engine.state.player(0).hp == 3, "对自己使用 ⇒ 自己受到 1 点火焰伤害"
    assert session.engine.state.player(0).hp_max == 4
    frames = session.engine.damage_frames
    assert len(frames) == 1, "一次火攻伤害只压一个伤害帧"
    assert frames[0]["nature"] is DamageNature.FIRE, "属性 = 火焰（WEB 会话的真实帧）"
    assert frames[0]["channel"].card_id == _fire_card_id(), "渠道牌 = 火攻本体"
    assert spades[1] in session.engine.state.discard, "弃掉的那张进弃牌堆"
    assert _fire_card_id() in session.engine.state.discard, "火攻本体随后也进弃牌堆"
    assert any(
        record.get("kind") == "delta"
        and record.get("delta_type") == "hp_change"
        and record.get("seat") == 0
        and record.get("amount") == -1
        for record in (_record_payload(r) for r in session.records())
    ), "目标受到 1 点伤害"

    entries = _narrative(
        session.records(),
        viewer=0,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    text = _line_text(entries)
    assert "亮出" in text and "弃置" in text and "受到 1 点伤害" in text


def test_fire_attack_pass_with_a_same_suit_card_deals_no_damage() -> None:
    """有同花色也可以放弃：一条 Pass = 零伤害，手牌 / 弃牌堆都不动。"""
    spades = _fire_suit_cards(Suit.SPADE)
    session = _fire_session(user_hand=(spades[0], spades[1]))
    session.submit_answer(_fire_use_action(session.pending, target=0))
    reveal = _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)
    session.submit_answer(reveal.legal_actions[0])
    discard = _step_until(session, lambda p: p.kind == QueryKind.SELECT_CARDS and p.actor == 0)

    before_discard = tuple(session.engine.state.discard)
    pass_action = next(action for action in discard.legal_actions if isinstance(action, Pass))
    session.submit_answer(pass_action)
    _step_until(session, lambda p: p.kind == QueryKind.PLAY_PHASE and p.actor == 0)

    assert session.engine.state.player(0).hp == 4, "放弃 ⇒ 零伤害"
    hand = tuple(session.engine.state.player(0).hand)
    assert spades[0] in hand and spades[1] in hand, "同花色手牌留在手里"
    assert spades[0] not in session.engine.state.discard
    assert spades[1] not in session.engine.state.discard
    assert set(session.engine.state.discard) - set(before_discard) == {_fire_card_id()}, (
        "只有火攻本体进弃牌堆"
    )
    assert not any(
        record.get("delta_type") == "hp_change"
        for record in (_record_payload(r) for r in session.records())
    ), "放弃不产生任何体力变化"
    entries = _narrative(
        session.records(),
        viewer=0,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    assert "放弃" in _line_text(entries)


def test_fire_attack_without_a_same_suit_card_has_no_discard_query_and_narrates_auto_pass() -> None:
    """无同花色 ⇒ 引擎不发弃牌询问（自动放弃）；事件流写「放弃（无可弃的同花色牌）」。"""
    club = _fire_suit_cards(Suit.CLUB)[0]
    spade = _fire_suit_cards(Suit.SPADE)[0]
    heart = _fire_suit_cards(Suit.HEART)[0]
    club2 = _fire_suit_cards(Suit.CLUB)[1]
    spade2 = _fire_suit_cards(Suit.SPADE)[1]
    # 人类：梅花 + 黑桃（无红桃）；摸牌也只摸梅花 / 黑桃；目标亮一张红桃 ⇒ 没有同花色。
    session = _fire_session(
        user_hand=(club, spade), target_hand=(heart,), top=(club2, spade2)
    )
    driver = _FireDriver(reveal=heart)
    session.participants = (_HaltSeat(), driver, _FireDriver(), _FireDriver())
    session.submit_answer(_fire_use_action(session.pending, target=1))
    selection_queries = []
    for _ in range(200):
        pending = session.pending
        if pending is None:
            break
        if pending.kind == QueryKind.SELECT_CARDS and pending.actor == 0:
            selection_queries.append(pending)
        if pending.kind == QueryKind.PLAY_PHASE and pending.actor == 0:
            break
        session.submit_answer(_FireDriver().answer(pending))
    assert selection_queries == [], "没有同花色 ⇒ 使用者收不到弃牌询问（不是死步骤）"
    assert session.engine.state.player(1).hp == 4, "零伤害"

    entries = _narrative(
        session.records(),
        viewer=0,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    text = _line_text(entries)
    assert "亮出" in text
    assert "放弃（无可弃的同花色牌）" in text, text
    assert "受到 1 点伤害" not in text


# ---------------------------------------------------------------------------
# M5-08b：铁索 1–2 目标 + 重铸入口（真会话 / 投影 / 叙事）
#
# 引擎侧（M5-03）已经保证：铁索是 1–2 目标的普通锦囊（可指定自己、无距离、0 目标非法、
# 同座不可重复、开无懈窗口、无次数限制），重铸是独立的 `Reforge` Action（仅出牌阶段、
# 不算使用、不开无懈、不消耗次数、牌进弃牌堆并摸一张、移动原因 `REFORGE`）。
# 本段钉的是**界面读取方**：候选索引里「使用」的组合与「重铸」分离、提交后两个目标
# 按各自状态独立翻转、重铸恰一次且不经过目标 / 无懈、事件流写「重铸」。
# ---------------------------------------------------------------------------

_IRON_PACK = None


def _iron_pack():
    """军争 160 档（惰性加载；铁索不在此档之外）。"""
    global _IRON_PACK
    if _IRON_PACK is None:
        from sgs.data import load_content_pack

        _IRON_PACK = load_content_pack(deck_profile="standard_ex_160")
    return _IRON_PACK


def _iron_card_id() -> int:
    return cards_of_type(_iron_pack(), "iron_chain")[0]


def _iron_card_ids() -> set[int]:
    return set(cards_of_type(_iron_pack(), "iron_chain"))


def _iron_session(*, hand=None, chained=(False, False, False, False)):
    """4 座构造局面：座位 0 = 人类、首回合出牌阶段；``chained`` 给各座初始横置态。

    与 :func:`_fire_session` 同一姿态：引擎行为真跑，只有人类座位的询问被挂起、由用例
    显式应答；其余座位走最小合法应答。``hand`` 缺省只有一张【铁索连环】。
    """
    from sgs.data import RuleConfig as _RuleConfig

    pack = _iron_pack()
    iron = _iron_card_id()
    user_hand = (iron,) if hand is None else tuple(hand)
    used = set(user_hand)
    seats = [
        SeatSpec(hp=4, hand=user_hand, chained=chained[0]),
        SeatSpec(hp=4, chained=chained[1]),
        SeatSpec(hp=4, chained=chained[2]),
        SeatSpec(hp=4, chained=chained[3]),
    ]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=tuple(i for i in range(len(pack.deck.cards)) if i not in used),
        turn_seat=0,
        rule_config=_RuleConfig(first_seat=0),
    )
    from sgs_web.session import Session as _Session

    session = _Session(
        seed=1,
        player_count=4,
        ai="random",
        human_seat=0,
        hero_pool=None,
        timeout_seconds=None,
        pack=pack,
    )
    session.engine = Engine.from_state(state, seed=1)
    session.participants = tuple(
        _HaltSeat() if seat == 0 else _FireDriver() for seat in range(4)
    )
    session.drive()
    return session


def _iron_use_action(pending, targets):
    iron = _iron_card_id()
    for action in pending.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.card_id == iron
            and action.targets == tuple(targets)
        ):
            return action
    raise AssertionError(f"出牌阶段没有对 {targets} 使用铁索的候选")


def _drive_until_human_play(session) -> list:
    """把人类座位推回出牌阶段；返回途中经过的无懈询问（真窗口，不是替身）。"""
    nullifies = []
    for _ in range(300):
        pending = session.pending
        if pending is None:
            break
        if pending.kind == QueryKind.PLAY_PHASE and pending.actor == 0:
            return nullifies
        if getattr(pending, "cause", None) == WindowCause.NULLIFY:
            nullifies.append(pending)
        session.submit_answer(_FireDriver().answer(pending))
    raise AssertionError("300 步内没有回到人类座位的出牌阶段")


def test_iron_chain_candidate_index_offers_self_and_one_or_two_targets() -> None:
    """候选索引：铁索可指定自己，组合只有 1 个或 2 个目标；重铸不冒充「使用」组合。"""
    session = _iron_session()
    pending = session.pending
    assert pending is not None and pending.kind == QueryKind.PLAY_PHASE
    payload = pending_payload(session)
    iron = _iron_card_id()

    entry = payload["candidate_index"][str(iron)]
    assert entry["pool"] == [0, 1, 2, 3], "4 座存活 ⇒ 池是全部座位（含自己）"
    assert [0] in entry["combos"], "可以只指定自己"
    assert [0, 1] in entry["combos"] and [1, 2] in entry["combos"], "两个目标的组合都在"
    assert entry["min_targets"] == 1 and entry["max_targets"] == 2, "min=1 / max=2"
    assert [] not in entry["combos"], "重铸是独立 Action，不得把 0 目标混进使用组合"

    reforge = [
        action for action in payload["legal_actions"] if action["kind"] == "Reforge"
    ]
    assert len(reforge) == 1, "可重铸的牌各给一条独立 Reforge 候选"
    assert reforge[0]["card"]["card_id"] == iron
    assert "targets" not in reforge[0], "重铸没有目标字段（不是 UseCard 的变体）"


def test_iron_chain_single_self_target_toggles_and_opens_the_nullify_window() -> None:
    """1 个目标（自己）：横置状态翻转、开无懈窗口、不消耗使用次数。"""
    session = _iron_session(chained=(False, True, False, False))
    pending = session.pending
    assert pending is not None and pending.kind == QueryKind.PLAY_PHASE
    session.submit_answer(_iron_use_action(pending, (0,)))

    nullifies = _drive_until_human_play(session)

    assert nullifies, "普通锦囊 ⇒ 使用后必须经过无懈窗口（真询问）"
    assert all(pending.kind == QueryKind.RESPOND for pending in nullifies)
    assert all(pending.cause == WindowCause.NULLIFY for pending in nullifies)
    assert all(
        any(action.kind == "Pass" for action in pending.legal_actions)
        for pending in nullifies
    ), "无懈窗口里至少有「放弃」"
    assert session.engine.state.player(0).chained is True, "自己：未横置 → 横置"
    assert session.engine.state.player(1).chained is True, "未指定的目标保持原状"
    assert _iron_card_id() in session.engine.state.discard, "结算后进弃牌堆"


def test_iron_chain_two_targets_toggle_each_seat_independently() -> None:
    """2 个目标：每个目标独立 toggle（已横置 → 重置、未横置 → 横置）。"""
    session = _iron_session(chained=(False, True, False, False))
    pending = session.pending
    assert pending is not None and pending.kind == QueryKind.PLAY_PHASE
    session.submit_answer(_iron_use_action(pending, (1, 2)))

    nullifies = _drive_until_human_play(session)

    assert nullifies, "2 目标同样开无懈窗口"
    assert session.engine.state.player(1).chained is False, "已横置 → 重置"
    assert session.engine.state.player(2).chained is True, "未横置 → 横置"
    assert session.engine.state.player(0).chained is False, "未指定的自己不动"
    assert session.engine.state.player(3).chained is False, "未指定的座位不动"


def test_reforge_moves_the_card_to_discard_and_draws_one_without_nullify() -> None:
    """重铸 = 恰一次真实提交：弃置 + 摸一张、不经过目标步 / 无懈窗口、不消耗使用次数。"""
    from sgs.state import Reforge

    session = _iron_session(hand=(_iron_card_id(),))
    pending = session.pending
    assert pending is not None and pending.kind == QueryKind.PLAY_PHASE
    payload = pending_payload(session)
    reforge = [action for action in pending.legal_actions if isinstance(action, Reforge)]
    assert len(reforge) == 1, "手里有可重铸的牌 ⇒ 恰一条 Reforge 候选"
    assert reforge[0].card.card_id == _iron_card_id()
    assert payload["legal_actions"].count(
        next(
            action for action in payload["legal_actions"]
            if action["kind"] == "Reforge"
        )
    ) == 1

    before_hand = tuple(session.engine.state.player(0).hand)
    before_deck = len(session.engine.state.deck)
    before_discard = tuple(session.engine.state.discard)
    before_counts = dict(session.engine.state.player(0).card_use_counts)

    session.submit_answer(reforge[0])

    after = session.pending
    assert after is not None and after.kind == QueryKind.PLAY_PHASE and after.actor == 0, (
        "重铸后直接回到出牌阶段：不进入目标选择、不开无懈窗口"
    )
    hand = tuple(session.engine.state.player(0).hand)
    assert _iron_card_id() not in hand, "重铸后牌离开手牌"
    assert len(hand) == len(before_hand), "弃一张 + 摸一张 ⇒ 手牌数不变"
    assert len(session.engine.state.deck) == before_deck - 1, "摸的那一张来自牌堆"
    assert _iron_card_id() in session.engine.state.discard, "牌进弃牌堆"
    assert set(session.engine.state.discard) - set(before_discard) == {_iron_card_id()}
    assert dict(session.engine.state.player(0).card_use_counts) == before_counts, (
        "重铸不算使用 ⇒ 不消耗任何使用次数"
    )

    moves = [
        record
        for record in (_record_payload(item) for item in session.records())
        if record.get("kind") == "delta"
        and record.get("delta_type") == "card_move"
        and record.get("reason") == "REFORGE"
    ]
    assert len(moves) == 1, "重铸只落一条 REFORGE 的移动记录"
    assert moves[0]["to_zone"]["zone"] == "discard"


def test_reforge_is_narrated_as_reforge_not_discard() -> None:
    """事件流（简易档）写「重铸」：既不写「弃置」，也不泄露 REFORGE / Reforge 英文。"""
    from sgs.state import Reforge

    session = _iron_session(hand=(_iron_card_id(),))
    session.submit_answer(next(
        action for action in session.pending.legal_actions if isinstance(action, Reforge)
    ))
    _drive_until_human_play(session)

    entries = _narrative(
        session.records(),
        viewer=0,
        heroes=session.hero_assignments,
        pack_id=session.pack.pack_id,
    )
    text = _line_text(entries)
    reforge_move = next(
        record
        for record in (_record_payload(item) for item in session.records())
        if record.get("kind") == "delta"
        and record.get("delta_type") == "card_move"
        and record.get("reason") == "REFORGE"
    )
    line = _line_at(entries, reforge_move["seq"])
    assert line is not None, "重铸必须有一行事件流"
    assert "重铸" in line["text"] and "铁索连环" in line["text"], line["text"]
    assert "弃置" not in line["text"], line["text"]
    assert "REFORGE" not in text and "Reforge" not in text, text


def test_reforge_is_not_offered_for_non_reforgeable_cards() -> None:
    """重铸入口以 `legal_actions` 里的 Reforge 为唯一判据：不可重铸的牌没有。"""
    pack = _iron_pack()
    slash = cards_of_type(pack, "slash")[0]
    session = _iron_session(hand=(_iron_card_id(), slash))
    payload = pending_payload(session)
    reforge_cards = {
        action["card"]["card_id"]
        for action in payload["legal_actions"]
        if action["kind"] == "Reforge"
    }
    assert reforge_cards == {_iron_card_id()}, "只有可重铸的【铁索连环】有 Reforge 候选"
    assert slash not in reforge_cards


def test_reforge_is_not_offered_outside_the_play_phase() -> None:
    """非出牌阶段（真会话的【闪】窗口）里，手里有铁索也没有 Reforge 候选。"""
    session = Session(
        seed=17,
        player_count=8,
        ai="heuristic",
        timeout_seconds=None,
        pack=_iron_pack(),
    )
    pending = session.pending
    assert pending is not None and pending.kind != QueryKind.PLAY_PHASE, (
        "这个种子的人类首个询问不是出牌阶段（前置断言）"
    )
    hand = tuple(session.engine.state.player(session.human_seat).hand)
    assert _iron_card_ids() & set(hand), "手里确实有铁索，否则这条断言是空转"
    payload = pending_payload(session)
    assert all(action["kind"] != "Reforge" for action in payload["legal_actions"]), (
        "非出牌阶段不出现重铸候选"
    )
