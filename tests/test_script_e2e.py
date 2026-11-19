"""M7 剧本模式的**端到端**（票 29）：真 HTTP 打完整条路线 + 退出续档 + 可复现。

三条判据（spec §Acceptance A1 / A3，逐条对应票 29 的验收）：

- **A1 正打**（:func:`test_a_full_script_run_walks_the_authored_route_over_real_http`）：
  新剧本 → 选角色 → 棋盘 → 真战斗（每次询问一条 HTTP，非预置 tape）→ 事件 / 商店 /
  问号房各至少一次 → Boss → 出口 → 通关；胜负由服务端从 ``game_end`` 判，**不信客户端**。
- **A1 退出继续**（:func:`test_exit_then_resume_returns_the_same_board_and_finishes`）：
  中途 ``suspend`` → ``resume`` → 同一份棋盘（逐字段）→ 打完。
- **A3 可复现**（:func:`test_two_identical_runs_produce_the_same_spec_sequence_and_run_state`、
  :func:`test_a_finished_script_battle_replays_back_to_the_same_record_stream`、
  :func:`test_reading_the_same_save_twice_never_redraws_the_pending_spec`）：同 seed + 同选择
  序列两遍逐字段相同（除 ``revision``）；单场战斗的记录流与回放器重建一致；同一档读两次
  不重抽（商店库存 / 事件 / 问号揭晓 / ``rewards`` 全同）。

**进料 = 内置剧本的路线 + 测试内替换的战斗编排**（见 :func:`e2e_library`）：节点 / 边 /
房间 / 事件 / 商店 / 标签门 / 两节结构全部读 ``scripts.huangjin.json``（票 28 的路线），只把
每个战斗房的敌人换成一个 1 体力、无技能、无随机池的模板。原因**不是**跳过战斗（每场都真打、
每次询问都真往返），而是当前占位数值里的 ``npc.guard``（受到伤害 -1）让 1 伤【杀】永远打不
出伤害：票 28 的 8 体力「程远志」对 4 体力主角是**不可胜**的（实测 0/28 个种子，见票 29
``## Answer`` 与 ``## 复现配方``）。数值是 D20 的内容期（HITL）欠账，不在本票重写范围；本测试
保留**路线与结算语义**（``rout`` / ``slay_boss`` / Boss 结算顺序 / 标签门），把战斗难度留给
内容期，同时保证门禁里有一条真的、可重复走完的端到端。

``runs/`` 用 pytest 的临时目录（与票 19 的端点专测同一条纪律）：绝不碰仓里的真存档；写进
该目录的 ``script-library.json`` 走的是**用户库优先于内置库**那条真实路径（票 19 §4）。
"""

from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.agents.heuristic import HeuristicPlayer  # noqa: E402
from sgs.data import (  # noqa: E402
    RandomSkills,
    Reward,
    Room,
    Seat,
    SeatRole,
    ScriptLibrary,
    Template,
    load_builtin_script_library,
)
from sgs.engine.rng import ai_stream  # noqa: E402

SEED = 20261129
"""整条路线的固定种子（同 seed 两遍 = A3 的可复现判据）。"""

HERO = "jie_liubei"
""""界刘备"在内置 roster 里（另外两名是能人志士 / 界关羽）。"""

SCRIPT_ID = "huangjin"
"""内置剧本「讨伐黄巾军」（票 28）：本文件走的就是它编出来的路线。"""

ROUTE: tuple[tuple[str, str], ...] = (
    ("l1", "entry"),  # 剧情战（真打）
    ("l1", "village"),  # 事件房（0 号选项）
    ("l1", "market"),  # 商店（标签门：village 的 on_clear 开它）
    ("l1", "boss"),  # 剧情 Boss（slay_boss）
    ("l1", "exit"),  # 出口商店 ⇒ 进第 2 大节
    ("l2", "entry"),  # 第 2 大节剧情战
    ("l2", "question"),  # 问号房（进入即揭晓）
    ("l2", "vanguard"),  # 遭遇战（按档抽的编排被替换成同一个弱模板）
    ("l2", "boss"),  # 剧情 Boss（slay_boss）
    ("l2", "exit"),  # 最后一个出口 ⇒ 通关、删档
)
"""票 28 §「交给票 29 的 e2e 走法」逐节点照抄（入口 / 事件 / 商店 / Boss / 出口）。"""

WEAK_TEMPLATE = "tpl.e2e.weak"
"""测试内替换用的 1 体力敌人模板：**只**改战斗难度，不改路线与结算语义。"""


# ---------------------------------------------------------------------------
# 进料：内置路线 + 可打的战斗编排
# ---------------------------------------------------------------------------


def _weak_battle(room: Room) -> Room:
    """把一个战斗房换成"1 名 1 体力敌人"的同一场：房间 id / 类型 / 胜负 / Boss 标记不动。"""
    battle = room.battle
    assert battle is not None, "只有战斗房才走这里"
    # 两种敌人来源都要认（固定编排 / 按档抽）；Boss 标记只在固定编排里。
    boss = isinstance(battle.enemies, tuple) and any(
        seat.boss for seat in battle.enemies
    )
    weak = Seat(role=SeatRole.ENEMY, template=WEAK_TEMPLATE, boss=boss)
    return room.model_copy(
        update={"battle": battle.model_copy(update={"enemies": (weak,)})}
    )


def e2e_library() -> ScriptLibrary:
    """内置剧本库 + 一个弱战斗模板；节点 / 边 / 房间 / 事件 / 商店 / 标签门全部原样。"""
    builtin = load_builtin_script_library()
    weak = Template(
        id=WEAK_TEMPLATE,
        name="试炼黄巾",
        band="band1",
        hp=1,
        hp_max=1,
        random=RandomSkills(pool="pool.grunts", count=(0, 0)),
        reward=Reward(coins=2),
    )
    battles = {room.id: _weak_battle(room) for room in builtin.rooms if room.battle}
    return builtin.model_copy(
        update={
            "rooms": tuple(battles.get(room.id, room) for room in builtin.rooms),
            "templates": builtin.templates + (weak,),
        }
    )


# ---------------------------------------------------------------------------
# 进程内的真处理器（与 tests/test_web.py / test_web_script.py 同一条缝：
# 请求字节 → 真的 ``serve._Handler`` → 状态码 + JSON；不开端口、不起线程）
# ---------------------------------------------------------------------------


class _Buffer(io.BytesIO):
    def close(self) -> None:  # handler.finish() 会关流；响应还要读，故忽略关闭
        pass


class _Socket:
    def __init__(self, payload: bytes) -> None:
        self._reader = _Buffer(payload)
        self._writer = _Buffer()

    def makefile(self, mode: str, *args: object, **kwargs: object) -> _Buffer:
        return self._reader if "r" in mode else self._writer

    def sendall(self, data: bytes) -> None:
        self._writer.write(data)


def call(registry, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """把一条请求交给**真的** ``serve._Handler``；返回 ``(状态码, JSON)``。"""
    from sgs_web.serve import _Handler

    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    head = f"{method} {path} HTTP/1.1\r\nHost: test\r\n"
    if payload:
        head += (
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
        )
    raw = (head + "\r\n").encode("ascii") + payload
    handler_cls = type("_BoundHandler", (_Handler,), {"registry": registry})
    socket = _Socket(raw)
    handler_cls(socket, ("127.0.0.1", 0), None)
    response = socket._writer.getvalue()
    header, _, response_body = response.partition(b"\r\n\r\n")
    status = int(header.split(b" ", 2)[1])
    return status, json.loads(response_body.decode("utf-8"))


# ---------------------------------------------------------------------------
# 夹具与读档工具
# ---------------------------------------------------------------------------


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    """一个装着 e2e 剧本库的 ``runs/``（用户库优先于内置库；存档也落这里）。"""
    directory = tmp_path / "runs"
    directory.mkdir()
    (directory / "script-library.json").write_text(
        json.dumps(e2e_library().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


@pytest.fixture()
def registry(runs_dir: Path):
    from sgs_web.serve import GameRegistry

    return GameRegistry(runs_dir=runs_dir)


def save_path(runs_dir: Path) -> Path:
    return runs_dir / "script-save.json"


def save_run(runs_dir: Path) -> dict | None:
    """读盘上的 ``RunState``（唯一真源；棋盘体里的 ``run`` 是它的展示投影）。"""
    if not save_path(runs_dir).exists():
        return None
    return json.loads(save_path(runs_dir).read_text(encoding="utf-8"))["run"]


def without_revision(run: dict | None) -> dict | None:
    """A3 的判据是"除 ``revision`` 外逐字段相同"（它只随写次数走，不是对局事实）。"""
    if run is None:
        return None
    return {key: value for key, value in run.items() if key != "revision"}


def inner_spec(spec: dict) -> dict:
    """问号房的待结算形状是"外层未知 + 内层房间"；其余房间就是它自己。"""
    return spec["revealed"]["spec"] if spec["kind"] == "unknown" else spec


# ---------------------------------------------------------------------------
# 一轮的 HTTP 客户端（建档 / 进节点 / 真打 / 结算 / 退出 / 继续）
# ---------------------------------------------------------------------------


class Run:
    """一轮剧本的驱动。

    计数（:attr:`answers` / :attr:`queries` / :attr:`views` / :attr:`kinds`）就是 A1 的
    "每次询问真往返"与"事件 / 商店 / 问号各至少一次"的断言来源。
    """

    def __init__(self, registry, runs_dir: Path) -> None:
        self.registry = registry
        self.runs_dir = runs_dir
        self.run_id: str | None = None
        self.answers = 0
        self.queries = 0
        self.views = 0
        self.battles: list = []
        self.battle_answers: list[int] = []
        self.battle_seqs: list[set[int]] = []
        self.kinds: Counter[str] = Counter()
        self.specs: list[dict] = []
        self.run_states: list[dict | None] = []
        self.outcomes: list[str] = []

    # -- 请求 ---------------------------------------------------------------

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        status, payload = call(self.registry, method, path, body)
        assert status == 200, f"{method} {path} → HTTP {status}: {payload}"
        return payload

    def create(self) -> dict:
        board = self.request(
            "POST",
            "/api/script-runs",
            {"script_id": SCRIPT_ID, "chosen_hero": HERO, "seed": SEED},
        )
        self.run_id = board["run_id"]
        assert self.run_id == f"{SEED}-1", "同 seed 的第一次建档：run_id 的序号从 1 起"
        return board

    def save(self) -> dict:
        return self.request("GET", "/api/script-save")

    def resume(self) -> dict:
        return self.request("POST", f"/api/script-runs/{self.run_id}/resume", {})

    def suspend(self) -> dict:
        return self.request("POST", f"/api/script-runs/{self.run_id}/suspend", {})

    def discard(self, revision: int) -> None:
        self.request("DELETE", f"/api/script-save?expected_revision={revision}")

    # -- 节点 ---------------------------------------------------------------

    def enter(self, node_id: str, board: dict) -> dict:
        entered = self.request(
            "POST",
            f"/api/script-runs/{self.run_id}/nodes/{node_id}",
            {"expected_revision": board["revision"]},
        )
        assert entered["pending"] is not None, f"进入 {node_id} 之后必须有待结算节点"
        spec = entered["pending"]["spec"]
        self.specs.append(json.loads(json.dumps(spec)))
        # 计数按**外层的房间种类**（问号房算 unknown，不算它揭晓出来的内层）。
        self.kinds[spec["kind"]] += 1
        return entered

    def fight(self, entered: dict):
        """把这一场打到服务端终局：**每次询问一条真 HTTP**，动作由启发式 AI 现选。

        人类座位不是"预置 tape"：每个询问都从该会话当前的 ``pending`` 现选（与
        ``tests/support/web_client.py`` 的整局驱动同一条口径——动作原样进 HTTP）。
        """
        session = self.registry.get(entered["session_id"])
        human = HeuristicPlayer(ai_stream(session.seed, 0))
        answered = 0
        answered_seqs: set[int] = set()
        while not session.finished:
            pending = session.pending
            if pending is None:
                # 没有挂起是合法态（逐目标暂停 / 还没轮到人类）：与真客户端一样再取一次增量。
                self.request(
                    "GET", f"/api/sessions/{session.session_id}?after=-1&logs=0"
                )
                self.views += 1
                continue
            action = human.respond(pending)
            answered_seqs.add(pending.seq)
            self.request(
                "POST",
                f"/api/sessions/{session.session_id}/answer",
                {"action": action.model_dump(mode="json")},
            )
            self.answers += 1
            self.queries += 1
            answered += 1
        self.battles.append(session)
        self.battle_answers.append(answered)
        self.battle_seqs.append(answered_seqs)
        return session

    def settle(self, entered: dict, *, won: bool | None = None) -> dict:
        """按房间类型给结算指令；战斗先真打到终局，胜负由服务端判。"""
        spec = entered["pending"]["spec"]
        inner = inner_spec(spec)
        body: dict = {"expected_revision": entered["revision"]}
        if inner["kind"] == "battle":
            self.fight(entered)
            body["session_id"] = entered["session_id"]
            # 每轮三选一取服务端候选的第一张（选择序列因此也是确定的）。
            body["picks"] = [
                candidates[0] for candidates in inner.get("rewards") or [] if candidates
            ]
            if won is not None:
                # A1-3：客户端自报的胜负**不进结算**——写反了也照服务端从 game_end 判。
                body["won"] = won
        elif inner["kind"] == "event":
            body["option"] = 0
        else:
            body["purchases"] = []
        settled = self.request("POST", f"/api/script-runs/{self.run_id}/settle", body)
        self.outcomes.append(settled["outcome"])
        self.run_states.append(save_run(self.runs_dir))
        return settled

    # -- 整条路线 ------------------------------------------------------------

    def walk(
        self, *, exit_after: int | None = None, first_settle_won: bool | None = None
    ) -> dict:
        """按 :data:`ROUTE` 走完整条路线；``exit_after`` = 结算完第 N 个节点后退出再继续。"""
        board = self.create()
        resumed: dict | None = None
        for index, (_layer, node_id) in enumerate(ROUTE):
            entered = self.enter(node_id, board)
            settled = self.settle(
                entered, won=first_settle_won if index == 0 else None
            )
            if settled["outcome"] in ("cleared", "failed"):
                assert settled["board"] is None, "整轮结束的两条路都不给棋盘"
                board = None
                break
            if exit_after is not None and index == exit_after:
                # 棋盘上退出 = 最后一次写就是最新的（票 04 §2 时机 5/6）；此刻没有引擎会话。
                before = self.request("GET", f"/api/script-runs/{self.run_id}")
                on_disk = save_run(self.runs_dir)
                assert self.suspend()["suspended"] is False, "棋盘上没有战斗会话可作废"
                resumed = {
                    "before": before,
                    "after": self.resume(),
                    "save": on_disk,
                }
                board = resumed["after"]
            else:
                board = settled["board"]
                assert board is not None, "非终局的结算必须给回棋盘"
        assert self.outcomes[-1] == "cleared", self.outcomes
        assert board is None, "通关 ⇒ 删档 + board:null"
        return resumed or {}


# ---------------------------------------------------------------------------
# A1-1 / A1-3 / A1-4：正打一遍（真 HTTP、真战斗、五类房间、通关）
# ---------------------------------------------------------------------------


def test_a_full_script_run_walks_the_authored_route_over_real_http(
    registry, runs_dir: Path
) -> None:
    run = Run(registry, runs_dir)
    run.walk(first_settle_won=False)

    # 事件 / 商店 / 问号各至少一次；战斗至少有 5 场（票 28 的路线逐节点）。
    assert run.kinds["event"] >= 1, run.kinds
    assert run.kinds["shop"] >= 2, run.kinds
    assert run.kinds["unknown"] >= 1, run.kinds
    assert len(run.battles) >= 5, "票 28 的路线里至少 5 场真打的战斗"
    # A1-4：每次询问都真的过了一条 HTTP——询问题数 == 应答数，且每条应答都在那一场的
    # 记录流里留下一条 accepted 的 action（"打的是真会话"，不是把终局写进档里）。
    assert run.queries == run.answers > 0
    assert sum(run.battle_answers) == run.answers
    for session, answered, seqs in zip(
        run.battles, run.battle_answers, run.battle_seqs, strict=True
    ):
        recorded = {
            record.action.answers_seq
            for record in session.records()
            if getattr(record, "kind", None) == "action" and record.accepted
        }
        assert len(seqs) == answered and seqs <= recorded, (
            f"会话 {session.session_id}：{answered} 条 HTTP 应答应在记录流里各留一条 accepted 记录"
        )

    # A1-3：第一场结算时客户端写 `won: False`，服务端照样按 game_end 判胜。
    assert run.outcomes[0] == "continue", "客户端自报的 won 必须被忽略（服务端从记录流判）"
    assert run.outcomes[-1] == "cleared"
    # 通关后两件套都删了：继续剧本随即 missing。
    assert run.save()["status"] == "missing"
    assert not save_path(runs_dir).exists()
    assert not save_path(runs_dir).with_name("script-save.json.bak").exists()

    # 标签闭环：事件房的 on_clear 开商店那道门；剧情战 / Boss 的 on_clear 也进了 run。
    settled_runs = [state for state in run.run_states if state is not None]
    assert any("flag.village_helped" in state["flags"] for state in settled_runs)
    assert any("flag.taiping_cleared" in state["flags"] for state in settled_runs)
    assert any("flag.zhang_jiao_slain" in state["flags"] for state in settled_runs)
    assert any(state["coins"] > 0 for state in settled_runs), "奖励 / 铜钱真的进了 run"


# ---------------------------------------------------------------------------
# A1-2：中途退出 → 继续 → 同一份棋盘 → 打完
# ---------------------------------------------------------------------------


def test_exit_then_resume_returns_the_same_board_and_finishes(
    registry, runs_dir: Path
) -> None:
    run = Run(registry, runs_dir)
    resumed = run.walk(exit_after=3)
    assert resumed, "exit_after 必须真的走到退出 / 继续那一步"
    before = resumed["before"]
    after = resumed["after"]
    # "回到同一份棋盘"：退出前与继续后逐字段相同（suspend 不写档，resume 不重抽）。
    assert after == before
    assert before["pending"] is None, "退出发生在两个节点之间（棋盘态）"
    assert {"l1/entry", "l1/village", "l1/market", "l1/boss"} <= set(
        resumed["save"]["node_status"]
    ), "退出那一刻盘上已经有前四个节点的 S_done"
    # 继续之后从原位置接着走完（walk 已断言 outcome == cleared 且删档）。
    assert run.outcomes[-1] == "cleared"
    assert run.save()["status"] == "missing"


# ---------------------------------------------------------------------------
# A3-1：同 seed + 同选择序列 ⇒ 同 spec 序列与同 RunState（除 revision）
# ---------------------------------------------------------------------------


def test_two_identical_runs_produce_the_same_spec_sequence_and_run_state(
    registry, runs_dir: Path
) -> None:
    first = Run(registry, runs_dir)
    first.walk()
    first_specs = json.loads(json.dumps(first.specs))
    first_runs = [without_revision(state) for state in first.run_states]

    # 通关已删档 ⇒ 第二次建档仍是 `<seed>-1`，两遍是同一轮（除 revision）。
    second = Run(registry, runs_dir)
    second.walk()

    assert second.run_id == first.run_id
    assert json.loads(json.dumps(second.specs)) == first_specs, (
        "同 seed + 同选择序列：spec 序列（含 battle_seed / rewards / 商店库存 / 问号揭晓）"
        "必须逐字段相同"
    )
    assert [without_revision(state) for state in second.run_states] == first_runs, (
        "每一步结算后的 RunState 必须逐字段相同（revision 除外）"
    )


# ---------------------------------------------------------------------------
# A3-2：单场战斗的记录流与回放器重建一致
# ---------------------------------------------------------------------------


def test_a_finished_script_battle_replays_back_to_the_same_record_stream(
    registry, runs_dir: Path
) -> None:
    from sgs.engine import dump_journal
    from sgs_web.replay import list_runs, load_run, replay_journal

    run = Run(registry, runs_dir)
    run.walk()
    assert len(run.battles) >= 5, "整条路线里每场战斗都真打了"

    names = [entry["name"] for entry in list_runs(runs_dir)]
    assert len(names) == len(run.battles), "每一场打完的剧本战斗各落一份录像"
    payloads = [load_run(runs_dir, name) for name in names]
    seeds = {payload["meta"]["seed"] for payload in payloads}
    assert seeds == {session.seed for session in run.battles}, "录像按 battle_seed 对得上"

    for session in run.battles:
        payload = next(p for p in payloads if p["meta"]["seed"] == session.seed)
        assert payload["meta"]["rule_config"]["pve"] is not None, "剧本战斗的侧车带 PvE 一节"
        replayed = replay_journal(payload["meta"])
        assert dump_journal(replayed) == dump_journal(session.records()), (
            "同 battle_seed + 同冻结 PveConfig + 同应答 ⇒ 回放器重建出同一条记录流"
        )


# ---------------------------------------------------------------------------
# A3-3：同一档读两次，不重抽（商店 / 事件 / 问号 / rewards）
# ---------------------------------------------------------------------------


def _reach(run: Run, board: dict, *node_ids: str) -> dict:
    """依次进节点并结算（商店空购买 / 事件 0 号选项），返回下一个棋盘。"""
    for node_id in node_ids:
        board = run.settle(run.enter(node_id, board))["board"]
        assert board is not None
    return board


def test_reading_the_same_save_twice_never_redraws_the_pending_spec(
    registry, runs_dir: Path
) -> None:
    """四种"抽到什么"（战斗 rewards / 商店库存 / 事件 / 问号揭晓）各读两次。

    两次读都走 ``POST …/resume``（读盘 → 棋盘），不是把上一次的返回值存进变量：档里是
    冻结的 ``pending.spec``，读两次必须逐字段相同（票 04 §3"续档不重抽"）；盘上那份也要
    与棋盘体下发的完全一致。
    """
    reached = {
        "battle": lambda run, board: run.enter("entry", board),
        "shop": lambda run, board: run.enter("shop_entry", board),
        "event": lambda run, board: _reach_and_enter(run, board, "village", "shop_entry"),
        "unknown": lambda run, board: _reach_and_enter(
            run, board, "question", "shop_entry", "village"
        ),
    }
    for kind, reach in reached.items():
        run = Run(registry, runs_dir)
        board = reach(run, run.create())
        first = run.resume()
        second = run.resume()
        assert first["pending"] == second["pending"] == board["pending"], (
            f"{kind}：同一档读两次，pending 必须逐字段相同"
        )
        assert first["revision"] == board["revision"], "读档不写档：revision 不变"

        outer = board["pending"]["spec"]
        spec = inner_spec(outer)
        on_disk = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))["run"][
            "pending"
        ]["spec"]
        assert on_disk == board["pending"]["spec"], "棋盘体的 pending 就是盘上那份"
        if kind == "battle":
            assert spec["battle_seed"] and spec["rewards"], "战斗的种子与三选一候选都冻结了"
        elif kind == "shop":
            assert spec["stock"]["skills"] or spec["stock"]["relics"], "商店库存抽出来了"
        elif kind == "event":
            assert spec["event_id"], "抽到的事件 id 冻结了"
        else:
            assert outer["revealed"]["room"], "问号揭晓结果落进了 pending"
            assert outer["revealed"]["spec"]["kind"] in ("battle", "event", "shop")
        run.discard(first["revision"])
        assert run.save()["status"] == "missing"


def _reach_and_enter(run: Run, board: dict, node_id: str, *prefix: str) -> dict:
    board = _reach(run, board, *prefix)
    return run.enter(node_id, board)
