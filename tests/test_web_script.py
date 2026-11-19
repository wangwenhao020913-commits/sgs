"""剧本模式的**端点专测**（M7 票 19）：存档 I/O、三态、revision 409、战斗会话与删档。

落点与口径（[票 04 §5、§7]、票 19 的验收）：

- 全部请求走**真的** ``sgs_web.serve._Handler``（进程内假 socket，与 ``tests/test_web.py``
  的同名帮手同一条缝）：解析、路由、状态码与 JSON 都是产品代码给出的；
- 进料是一份**手搓**剧本库（``tests/support/script_mode.py`` 的共享表 + 本文件补的两个
  短战斗房）：不读内置剧本——它随票 28 变，且本票判的是接线不是内容；
- 两场战斗都设计成**两轮内自然终局**（``survive_rounds(1)`` 胜 / ``rout_within_rounds(1)``
  败），由 ``fallback_action`` 代答人类座位：结算的胜负仍由服务端从 ``game_end`` 读。

``runs/`` 里的文件名是本文件钉住的协议：``script-save.json`` + ``.bak`` + ``.tmp``。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import (  # noqa: E402
    Battle,
    Grid,
    Layer,
    Node,
    PveVictory,
    PveVictoryKind,
    Rarity,
    Room,
    RoomKind,
    Script,
    ScriptLibrary,
    Seat,
    SeatRole,
    SkillValueOverride,
    SkillValuePreset,
    hero_by_id,
    hero_skill_name,
    load_skill_values,
    save_skill_value_preset,
)
from sgs.script import skill_price  # noqa: E402
from sgs_web.session import Session  # noqa: E402
from sgs_web.timeout import fallback_action  # noqa: E402

from support import script_mode  # noqa: E402


# ---------------------------------------------------------------------------
# 进料：两个剧本——``probe``（两节，够摆棋盘 / 摘要）与 ``clearable``（一格商店 = 通关）
# ---------------------------------------------------------------------------


def _story_room(*, id: str, name: str, victory: PveVictory) -> Room:
    """1v1 剧情战斗房：``victory`` 决定它两轮内是胜还是败。"""
    return Room(
        id=id,
        name=name,
        kind=RoomKind.STORY,
        battle=Battle(
            enemies=(Seat(role=SeatRole.ENEMY, template="tpl.grunt"),),
            victory=victory,
        ),
    )


def _layer_w1() -> Layer:
    """第一节：三个入口（胜 / 败两间战斗房 + 事件）都通向商店出口。"""
    return Layer(
        id="w1",
        name="交战",
        grid=Grid(rows=3, cols=2),
        nodes=(
            Node(id="win", row=0, col=0, room="room.probe"),
            Node(id="lose", row=1, col=0, room="room.timeout"),
            Node(id="event", row=2, col=0, room="room.event.village"),
            Node(id="exit", row=0, col=1, room="room.shop"),
        ),
        edges=(
            script_mode.edge("win", "exit"),
            script_mode.edge("lose", "exit"),
            script_mode.edge("event", "exit"),
        ),
    )


def _layer_w2() -> Layer:
    """第二节：一格的商店（既是入口又是出口）。"""
    return Layer(
        id="w2",
        name="收尾",
        grid=Grid(rows=1, cols=1),
        nodes=(Node(id="shop", row=0, col=0, room="room.shop"),),
        edges=(),
    )


def _layer_clearable() -> Layer:
    """只有一个商店格的一节：结算它就结算整轮（``next_layer_index`` ⇒ ``None``）。"""
    return Layer(
        id="c1",
        name="独节",
        grid=Grid(rows=1, cols=1),
        nodes=(Node(id="shop", row=0, col=0, room="room.shop"),),
        edges=(),
    )


def _layer_reveal() -> Layer:
    """问号房揭晓：入口（商店）→ 问号房（揭晓事件房）→ 出口，一条直线。"""
    return Layer(
        id="v1",
        name="揭晓",
        grid=Grid(rows=1, cols=3),
        nodes=(
            Node(id="entry", row=0, col=0, room="room.shop"),
            Node(id="unknown", row=0, col=1, room="room.unknown"),
            Node(id="exit", row=0, col=2, room="room.shop"),
        ),
        edges=(
            script_mode.edge("entry", "unknown"),
            script_mode.edge("unknown", "exit"),
        ),
    )


ROSTER = ("nengren", "jie_liubei", "jie_guanyu")


def library() -> ScriptLibrary:
    """过得了票 14 整份校验器的库：共享表沿用 ``support.script_mode``，剧本换成本文件的。"""
    return ScriptLibrary(
        script_schema_version=1,
        scripts=(
            Script(
                id="probe", name="探针剧本", version=1, roster=ROSTER,
                layers=(_layer_w1(), _layer_w2()),
            ),
            Script(
                id="clearable", name="一格商店", version=1, roster=ROSTER,
                layers=(_layer_clearable(),),
            ),
            Script(
                id="reveal", name="揭晓剧本", version=1, roster=ROSTER,
                layers=(_layer_reveal(),),
            ),
        ),
        rooms=(
            *script_mode.rooms(),
            _story_room(
                id="room.probe",
                name="守到天亮",
                victory=PveVictory(kind=PveVictoryKind.SURVIVE_ROUNDS, rounds=1),
            ),
            _story_room(
                id="room.timeout",
                name="限时清剿",
                victory=PveVictory(kind=PveVictoryKind.ROUT_WITHIN_ROUNDS, rounds=1),
            ),
        ),
        templates=script_mode.templates(),
        skill_pools=script_mode.skill_pools(),
        events=script_mode.events(),
        event_pools=script_mode.event_pools(),
        global_skills=script_mode.global_skills(),
    )


SEED = 0x5EED_2026
"""建档用的固定种子（``run_id`` = ``<seed>-<n>``，两处都读它）。"""


# ---------------------------------------------------------------------------
# 进程内的真处理器（与 tests/test_web.py 的同名帮手同一条缝）
# ---------------------------------------------------------------------------


def _handler_socket(raw: bytes):
    import io as _io

    class _Buffer(_io.BytesIO):
        def close(self):  # handler.finish() 会关流；响应还要读，故忽略关闭
            pass

    class _Socket:
        def __init__(self, payload: bytes) -> None:
            self._reader = _Buffer(payload)
            self._writer = _Buffer()

        def makefile(self, mode, *args, **kwargs):  # noqa: ANN002, ANN003 - 基类签名
            return self._reader if "r" in mode else self._writer

        def sendall(self, data: bytes) -> None:
            self._writer.write(data)

    return _Socket(raw)


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
    socket = _handler_socket(raw)
    handler_cls(socket, ("127.0.0.1", 0), None)
    response = socket._writer.getvalue()
    header, _, response_body = response.partition(b"\r\n\r\n")
    status = int(header.split(b" ", 2)[1])
    return status, json.loads(response_body.decode("utf-8"))


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    """一个装着本文件剧本库的 ``runs/``（用户库优先于内置库）。"""
    directory = tmp_path / "runs"
    directory.mkdir()
    (directory / "script-library.json").write_text(
        json.dumps(library().model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    return directory


@pytest.fixture()
def registry(runs_dir: Path):
    from sgs_web.serve import GameRegistry

    return GameRegistry(runs_dir=runs_dir)


def save_path(runs_dir: Path) -> Path:
    return runs_dir / "script-save.json"


def backup_path(runs_dir: Path) -> Path:
    return runs_dir / "script-save.json.bak"


def create(registry, **overrides) -> tuple[int, dict]:
    body = {"script_id": "probe", "chosen_hero": "jie_liubei", "seed": SEED}
    body.update(overrides)
    return call(registry, "POST", "/api/script-runs", body)


def entered(registry, run_id: str, node_id: str, revision: int) -> tuple[int, dict]:
    return call(
        registry,
        "POST",
        f"/api/script-runs/{run_id}/nodes/{node_id}",
        {"expected_revision": revision},
    )


def settled(registry, run_id: str, revision: int, **body) -> tuple[int, dict]:
    payload = {"expected_revision": revision, **body}
    return call(registry, "POST", f"/api/script-runs/{run_id}/settle", payload)


def drive_to_the_end(session: Session, *, limit: int = 400) -> None:
    """用超时代答档把人类座位答完（引擎终局的判据仍由记录流决定）。"""
    for _ in range(limit):
        if session.finished:
            return
        pending = session.pending
        if pending is None:
            session.drive()
        else:
            session.submit_answer(fallback_action(pending))
    raise AssertionError("400 次驱动仍未终局")


def _from_create(registry, **overrides) -> dict:
    status, body = create(registry, **overrides)
    assert status == 200, body
    return body


# ---------------------------------------------------------------------------
# A. 三态 GET + 两次 rename + 恢复
# ---------------------------------------------------------------------------


def test_the_save_endpoint_is_missing_before_any_run(registry, runs_dir: Path) -> None:
    status, body = call(registry, "GET", "/api/script-save")

    assert status == 200
    assert body["status"] == "missing"
    assert body["run_id"] is None and body["revision"] is None and body["summary"] is None
    assert body["reason"] == ""
    assert body["bak_available"] is False
    assert body["value_table_locked"] is False
    assert not save_path(runs_dir).exists(), "GET 不落盘"


def test_creating_a_run_writes_s0_at_revision_zero(registry, runs_dir: Path) -> None:
    status, body = create(registry)

    assert status == 200, body
    assert body["run_id"] == f"{SEED}-1"
    assert body["revision"] == 0
    assert body["pending"] is None
    assert body["save"]["status"] == "ok"

    saved = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    assert saved["script_mode_version"] == 1
    assert saved["saved_at"] == body["save"]["saved_at"]
    assert saved["run"]["run_id"] == f"{SEED}-1"
    assert saved["run"]["revision"] == 0
    assert saved["run"]["chosen_hero"] == "jie_liubei"
    assert saved["run"]["roster_candidates"] == list(ROSTER)
    assert saved["script_snapshot"]["scripts"][0]["id"] == "probe", "快照 = 整份库"
    assert saved["value_table"]["schema_version"] == 1
    assert not backup_path(runs_dir).exists(), "第一次写没有可轮换的旧档"


def test_the_save_endpoint_summarises_the_run_for_the_lobby(registry) -> None:
    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", created["revision"])[0] == 200

    status, body = call(registry, "GET", "/api/script-save")

    assert status == 200
    assert body["status"] == "ok"
    assert body["run_id"] == created["run_id"]
    assert body["revision"] == 1
    assert body["saved_at"]
    assert body["summary"] == {
        "script_id": "probe",
        "script_name": "探针剧本",
        "layer_index": 0,
        "layer_count": 2,
        "settled_nodes": 0,
        "coins": 0,
    }
    assert body["bak_available"] is True, "进入节点这一次真写把 S₀ 轮换成了 .bak"
    assert body["bak_saved_at"]
    assert body["value_table_locked"] is True


def test_a_second_write_rotates_the_main_file_into_the_backup(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", 0)[0] == 200

    saved = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    backup = json.loads(backup_path(runs_dir).read_text(encoding="utf-8"))
    assert saved["run"]["revision"] == 1
    assert saved["run"]["pending"]["node_id"] == "w1/event"
    assert backup["run"]["revision"] == 0, "旧档整份轮换成 .bak"
    assert backup["run"]["pending"] is None
    assert not save_path(runs_dir).with_name("script-save.json.tmp").exists()


def test_a_missing_main_with_the_backup_is_broken_and_recoverable(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", 0)[0] == 200
    # 手工造「两次 rename 之间被杀」：主档被搬走、新档还没落位。
    os.replace(save_path(runs_dir), backup_path(runs_dir))
    assert not save_path(runs_dir).exists() and backup_path(runs_dir).exists()

    status, body = call(registry, "GET", "/api/script-save")

    assert status == 200
    assert body["status"] == "broken", "主档缺失 + .bak 在 = 可恢复的中断，不是无存档"
    assert body["reason"]
    assert body["bak_available"] is True
    assert body["bak_saved_at"]
    assert body["run_id"] is None and body["revision"] is None
    assert not save_path(runs_dir).exists(), "GET 不修档（恢复是显式动作）"

    status, restored = call(registry, "POST", "/api/script-save/restore")

    assert status == 200, restored
    assert restored["save"]["status"] == "ok"
    assert restored["revision"] == 1, "恢复 = 写回 .bak 的原字节，不改 revision"
    assert save_path(runs_dir).read_bytes() == backup_path(runs_dir).read_bytes()
    assert call(registry, "GET", "/api/script-save")[1]["status"] == "ok"

    status, refused = call(registry, "POST", "/api/script-save/restore")
    assert status == 409, refused
    # 主档可用时恢复 = 静默丢弃它
    assert "恢复" in refused["error"]


def test_a_broken_main_is_reported_and_never_rewritten(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    save_path(runs_dir).write_text("{ not json", encoding="utf-8")
    before = save_path(runs_dir).read_bytes()

    status, body = call(registry, "GET", "/api/script-save")
    assert status == 200
    assert body["status"] == "broken" and body["status"] != "missing"
    assert "structure.invalid_json" in body["reason"]
    assert body["bak_available"] is False
    assert save_path(runs_dir).read_bytes() == before, "坏档不被静默重写"

    status, conflict = call(registry, "GET", f"/api/script-runs/{created['run_id']}")
    assert status == 409, conflict
    assert conflict["status"] == "broken", "409 的体带当前状态（坏档没有 revision）"
    assert conflict["reason"]

    status, refused = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 0, "slots": [None] * 24},
    )
    assert status == 409, refused
    assert save_path(runs_dir).read_bytes() == before


# ---------------------------------------------------------------------------
# B. 建档 / 恢复 / 读棋盘 / 404
# ---------------------------------------------------------------------------


def test_the_roster_endpoint_lists_the_authored_candidates(registry, runs_dir: Path) -> None:
    status, body = call(registry, "POST", "/api/script-roster", {})

    assert status == 200
    assert [entry["id"] for entry in body["scripts"]] == ["probe", "clearable", "reveal"]
    for entry in body["scripts"]:
        assert entry["roster_candidates"] == list(ROSTER)
    assert body["script_id"] is None and body["roster_candidates"] == []

    status, picked = call(registry, "POST", "/api/script-roster", {"script_id": "probe"})
    assert status == 200
    assert picked["script_id"] == "probe"
    assert picked["roster_candidates"] == list(ROSTER)
    assert not save_path(runs_dir).exists(), "3 选 1 期间不落盘（票 04 §2 时机 1）"


def test_creating_a_run_refuses_an_unknown_script_or_hero(registry, runs_dir: Path) -> None:
    status, body = create(registry, script_id="nope")
    assert status == 400 and "nope" in body["error"]
    assert not save_path(runs_dir).exists()

    status, body = create(registry, chosen_hero="hero.not_in_roster")
    assert status == 400 and "hero.not_in_roster" in body["error"]
    assert not save_path(runs_dir).exists()


def test_creating_a_run_over_an_existing_save_needs_its_revision(
    registry, runs_dir: Path
) -> None:
    first = _from_create(registry)
    before = save_path(runs_dir).read_bytes()

    status, conflict = call(
        registry, "POST", "/api/script-runs", {"script_id": "probe", "chosen_hero": "nengren"}
    )
    assert status == 409, conflict
    assert conflict["revision"] == 0, "409 带当前 revision"
    assert conflict["summary"]["script_name"] == "探针剧本"
    assert save_path(runs_dir).read_bytes() == before, "被拒时文件不变"

    status, second = create(registry, chosen_hero="nengren", expected_revision=0)
    assert status == 200, second
    assert second["run_id"] == f"{SEED}-2", "同一 seed 的建档序号 +1"
    assert second["revision"] == 0
    assert first["run_id"] != second["run_id"]


def test_the_board_view_lists_the_layer_its_edges_and_the_available_nodes(registry) -> None:
    created = _from_create(registry)
    status, board = call(registry, "GET", f"/api/script-runs/{created['run_id']}")

    assert status == 200, board
    assert board["run_id"] == created["run_id"] and board["revision"] == 0
    assert board["script"]["id"] == "probe"
    assert board["script"]["name"] == "探针剧本"
    assert board["script"]["version"] == 1
    assert board["script"]["digest"].startswith("sha256:")
    assert board["layer"] == {
        "id": "w1",
        "name": "交战",
        "index": 0,
        "count": 2,
        "rows": 3,
        "cols": 2,
    }
    assert board["available_next"] == ["win", "lose", "event"]
    assert board["board_error"] is None
    assert board["pending"] is None
    assert [node["id"] for node in board["nodes"]] == ["win", "lose", "event", "exit"]
    assert board["nodes"][0]["status"] is None
    assert board["nodes"][0]["available"] is True
    assert board["nodes"][3]["available"] is False
    assert [(edge["from"], edge["to"]) for edge in board["edges"]] == [
        ("win", "exit"),
        ("lose", "exit"),
        ("event", "exit"),
    ]
    assert board["run"]["chosen_hero"] == "jie_liubei"
    assert board["run"]["coins"] == 0 and board["run"]["layer_index"] == 0
    assert board["run"]["backpack"]["slots"] == [None] * 24
    assert board["save"]["status"] == "ok"

    status, resumed = call(
        registry, "POST", f"/api/script-runs/{created['run_id']}/resume", {}
    )
    assert status == 200 and resumed["revision"] == 0, "继续 = 读档回棋盘"


def test_the_board_carries_enough_room_detail_for_the_hover_card(registry) -> None:
    created = _from_create(registry)
    _, board = call(registry, "GET", f"/api/script-runs/{created['run_id']}")

    by_id = {node["id"]: node for node in board["nodes"]}
    event = by_id["event"]["room"]
    assert event["kind"] == "event"
    assert event["events"][0]["id"] == "ev.village"
    assert event["events"][0]["options"][0]["text"] == "收下"

    battle = by_id["win"]["room"]["battle"]
    assert battle["allies"] == []
    assert battle["enemies"]["kind"] == "fixed"
    assert battle["enemies"]["seats"][0]["template"] == "tpl.grunt"
    assert battle["victory"] == {"kind": "survive_rounds", "rounds": 1}
    assert [item["id"] for item in battle["templates"]] == ["tpl.grunt"]
    assert battle["templates"][0]["name"] == "黄巾力士"
    assert battle["templates"][0]["hp"] == 3


# ---------------------------------------------------------------------------
# 票 22：整备面 / 三选一 / 商店要用的**目录行**（名字 + 单价，服务端下发）
# ---------------------------------------------------------------------------


def _catalog_board(
    registry, *, script_id: str = "probe", slots: list[str | None] | None = None
) -> dict:
    """建一轮（无 pending）并把背包摆成 ``slots``；回棋盘体。"""
    created = _from_create(registry, script_id=script_id)
    if slots is None:
        return call(registry, "GET", f"/api/script-runs/{created['run_id']}")[1]
    status, board = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": created["revision"], "slots": slots},
    )
    assert status == 200, board
    return board


def test_the_board_carries_a_name_and_a_server_price_for_every_row_it_shows(
    registry,
) -> None:
    """票 22 的备注「铜钱 / 价格 / 折价数字一律服务端下发」：棋盘体带一份**目录行**。

    判据是**独立真源**：单价由 :func:`sgs.script.skill_price` 读**档内价值表**现算（默认表
    全普通 ⇒ 3），名字由 :func:`sgs.data.hero_skill_name` 给出。前端不按稀有度自己乘价。
    """
    slots: list[str | None] = [None] * 24
    slots[0] = script_mode.SKILL_A
    slots[1] = script_mode.SKILL_B
    board = _catalog_board(registry, script_id="clearable", slots=slots)
    base = load_skill_values()
    row = board["catalog"][script_mode.SKILL_A]
    hero = hero_by_id(script_mode.SKILL_A.split(".")[1])
    assert hero is not None
    assert row == {
        "name": hero_skill_name(hero, script_mode.SKILL_A),
        "kind": "skill",
        "rarity": "common",
        "price": 3,
    }, "整备面的 24 格读这一份：名字 + 稀有度 + 单价都来自服务端"
    assert board["catalog"][script_mode.SKILL_B]["price"] == skill_price(
        base, script_mode.SKILL_B
    )
    assert "hero.zhangfei.paoxiao" not in board["catalog"], "这一屏没显示的行不下发"

    # 商店：库存里的每一件都要有行（价格 / 名字由服务端算），前端只显示。
    shop = entered(registry, board["run_id"], "shop", board["revision"])[1]
    stock = shop["pending"]["spec"]["stock"]["skills"]
    assert len(stock) == 6, "默认表全启用 ⇒ 商店 6 技能"
    for skill_id in stock:
        assert shop["catalog"][skill_id]["price"] == skill_price(base, skill_id)
        assert shop["catalog"][skill_id]["kind"] == "skill"
        assert shop["catalog"][skill_id]["name"], "名字不许留空"


def test_a_relic_catalog_row_names_the_relic_from_its_skill_definition() -> None:
    """票 34：``relic.*`` 在棋盘体目录行里的名字 = ``CATALOG`` 那条定义的显示文本（不是 id）。

    整备面遗物栏 / 三选一 / 商店 / 事件后果都读这一份目录行（`script-loadout.js:scriptItemRow`）；
    遗物的中文名只有 ``SkillDef`` 一处来源（`relics.py` 的六条定义），前端不抄第二份映射，
    服务端也不按 ``relic.`` 前缀特判——判据只是 "id 在 ``CATALOG`` 里"。
    """
    from sgs.data import RELIC_PRICE, load_skill_values
    from sgs_web.script_runs import _catalog_item

    table = load_skill_values()
    assert _catalog_item(table, "relic.extra_draw") == {
        "name": "每回合摸牌阶段 +1",
        "kind": "relic",
        "rarity": None,
        "price": RELIC_PRICE,
    }
    assert _catalog_item(table, "relic.hand_limit")["name"] == "手牌上限 +2"


def test_an_entered_question_room_carries_the_revealed_inner_room(registry) -> None:
    """票 22「问号揭晓：揭晓后按内层房间的流程走」——内层房间的面必须下发给前端。

    ``pending.spec.revealed`` 只有 ``room``（id）+ 内层 spec（票 16），**认不出**房间的
    名字 / 文案 / 事件选项；故待结算的问号节点在自己的 ``room`` 上多一个 ``revealed``
    （与 ``_room`` 同形的整份房间面）。揭晓本身由服务端在进入时写档，重进不重抽。
    """
    created = _from_create(registry, script_id="reveal")
    first = entered(registry, created["run_id"], "entry", created["revision"])[1]
    assert settled(registry, first["run_id"], first["revision"], purchases=[])[0] == 200
    board = entered(registry, first["run_id"], "unknown", first["revision"] + 1)[1]
    spec = board["pending"]["spec"]
    assert spec["kind"] == "unknown"
    node = next(item for item in board["nodes"] if item["id"] == "unknown")
    revealed = node["room"]["revealed"]
    assert revealed["id"] == spec["revealed"]["room"]
    assert revealed["kind"] == "event"
    assert [event["id"] for event in revealed["events"]] == ["ev.village", "ev.ambush"], (
        "内层事件房的事件与选项要能画出来（前端不猜文案）"
    )
    assert (revealed["events"][0]["options"][0]["text"]) == "收下"
    assert "revealed" not in next(
        item for item in board["nodes"] if item["id"] == "exit"
    )["room"], "只有待结算的那个问号节点带内层面"
    # 内层房间的事件后果也要能显示名字（揭晓后按它的流程走 ⇒ 同一份目录）。
    grant = next(
        effect["skill"]
        for effect in revealed["events"][0]["options"][1]["effects"]
        if effect["kind"] == "grant_skill"
    )
    assert board["catalog"][grant]["kind"] == "skill"
    assert board["catalog"][grant]["price"] == skill_price(load_skill_values(), grant)


def test_the_catalog_price_reads_the_frozen_table_not_the_local_preset(
    registry, runs_dir: Path
) -> None:
    """价读**档内**冻结表：建完档再改本地预设（把那条抬成传说）⇒ 本轮价格不动。

    新的一轮（按本地预设建）价格才是 30——两半都要断，否则"一直发 3"也能绿。
    """
    base = load_skill_values()
    slots: list[str | None] = [None] * 24
    slots[0] = script_mode.SKILL_A
    frozen = _catalog_board(registry, slots=slots)
    assert frozen["catalog"][script_mode.SKILL_A]["price"] == 3

    save_skill_value_preset(
        slot=0,
        preset=SkillValuePreset(
            skills={script_mode.SKILL_A: SkillValueOverride(rarity=Rarity.LEGENDARY)}
        ),
        path=runs_dir / "skill-value-presets.json",
        base=base,
    )
    still = call(
        registry, "GET", f"/api/script-runs/{frozen['run_id']}"
    )[1]
    assert still["catalog"][script_mode.SKILL_A]["price"] == 3, (
        "档内价值表冻结（票 04 §6）：本地预设只影响新的一轮"
    )
    assert still["catalog"][script_mode.SKILL_A]["rarity"] == "common"

    # 删档（解锁）后新开一轮 ⇒ 按本地预设：传说 30。
    assert call(
        registry, "DELETE", f"/api/script-save?expected_revision={still['revision']}"
    )[0] == 200
    fresh_slots: list[str | None] = [None] * 24
    fresh_slots[0] = script_mode.SKILL_A
    fresh = _catalog_board(registry, slots=fresh_slots)
    assert fresh["catalog"][script_mode.SKILL_A]["price"] == 30
    assert fresh["catalog"][script_mode.SKILL_A]["rarity"] == "legendary"


def test_the_run_keeps_its_own_snapshot_when_the_library_changes(registry, runs_dir: Path) -> None:
    """快照自足：run 进行中改用户库，棋盘与后续节点内容不变（票 03 §7 / 票 04 §7.9）。"""
    created = _from_create(registry)
    _, before = call(registry, "GET", f"/api/script-runs/{created['run_id']}")
    rewritten = library()
    rewritten = rewritten.model_copy(
        update={"scripts": tuple(script for script in rewritten.scripts if script.id != "probe")}
    )
    (runs_dir / "script-library.json").write_text(
        json.dumps(rewritten.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8"
    )

    status, after = call(registry, "GET", f"/api/script-runs/{created['run_id']}")

    assert status == 200, after
    assert after["nodes"] == before["nodes"] and after["edges"] == before["edges"]
    assert after["script"]["name"] == "探针剧本", "本轮读的是档内快照，不是用户库"


def test_the_session_id_never_enters_the_save(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]
    assert entered_body["session_id"]

    text = save_path(runs_dir).read_text(encoding="utf-8")
    assert entered_body["session_id"] not in text, "会话 id 只是内存句柄（重启从 s1 编号）"


def test_an_unknown_run_id_is_a_404_on_every_run_endpoint(registry) -> None:
    assert call(registry, "GET", "/api/script-runs/123-9")[0] == 404
    assert call(registry, "POST", "/api/script-runs/123-9/resume", {})[0] == 404
    assert call(registry, "POST", "/api/script-runs/123-9/suspend", {})[0] == 404
    assert settled(registry, "123-9", 0)[0] == 404
    assert entered(registry, "123-9", "event", 0)[0] == 404
    assert (
        call(
            registry,
            "PUT",
            "/api/script-runs/123-9/backpack",
            {"expected_revision": 0, "slots": [None] * 24},
        )[0]
        == 404
    )


# ---------------------------------------------------------------------------
# C. 进入节点：S_enter + 不重抽 + 409
# ---------------------------------------------------------------------------


def test_entering_a_node_writes_s_enter_and_returns_the_materialised_face(registry) -> None:
    created = _from_create(registry)
    status, body = entered(registry, created["run_id"], "event", created["revision"])

    assert status == 200, body
    assert body["revision"] == 1
    assert body["pending"]["node_id"] == "w1/event"
    assert body["pending"]["spec"]["kind"] == "event"
    assert body["pending"]["spec"]["event_id"] == "ev.village", "单事件池：抽到的就是它"
    assert body["available_next"] == [], "节点内没有下一步可选"
    assert body["session_id"] is None, "非战斗房不起会话"
    assert call(registry, "GET", "/api/script-save")[1]["revision"] == 1


def test_entering_the_same_node_again_never_redraws(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    first = entered(registry, created["run_id"], "event", created["revision"])[1]
    before = save_path(runs_dir).read_bytes()

    status, second = entered(registry, created["run_id"], "event", first["revision"])

    assert status == 200, second
    assert second["pending"]["spec"] == first["pending"]["spec"]
    assert second["revision"] == first["revision"], "内容没变 ⇒ 跳过写、不 +1"
    assert save_path(runs_dir).read_bytes() == before


def test_entering_an_unreachable_node_is_a_400_and_changes_nothing(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    before = save_path(runs_dir).read_bytes()

    status, body = entered(registry, created["run_id"], "exit", created["revision"])

    assert status == 400, body
    assert body["rule_id"] == "script.enter.unreachable_node"
    assert "win" in body["error"]
    assert save_path(runs_dir).read_bytes() == before, "写失败 ⇒ 不起会话、不留半个节点"


def test_a_stale_revision_is_a_409_and_the_file_stays_untouched(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", created["revision"])[0] == 200
    before = save_path(runs_dir).read_bytes()

    status, conflict = call(
        registry,
        "POST",
        f"/api/script-runs/{created['run_id']}/nodes/event",
        {"expected_revision": 0},
    )

    assert status == 409, conflict
    assert conflict["revision"] == 1
    assert conflict["summary"]["settled_nodes"] == 0
    assert save_path(runs_dir).read_bytes() == before, "409 不写文件"

    status, retried = entered(registry, created["run_id"], "event", 1)
    assert status == 200 and retried["revision"] == 1


# ---------------------------------------------------------------------------
# D. 结算：S_done / 删档三路 / 胜负由服务端判
# ---------------------------------------------------------------------------


def test_settling_an_event_node_writes_s_done_and_returns_to_the_board(registry) -> None:
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "event", created["revision"])[1]

    status, body = settled(registry, created["run_id"], entered_body["revision"], option=0)

    assert status == 200, body
    assert body["outcome"] == "continue"
    assert body["board"]["pending"] is None
    assert body["board"]["run"]["coins"] == 2, "ev.village 的「收下」+2 铜钱"
    assert body["board"]["run"]["node_status"] == {"w1/event": "completed"}
    assert body["board"]["run"]["flags"] == ["flag.event_done"], "房间 on_clear 的标签"
    assert body["board"]["available_next"] == ["exit"]
    assert body["save"]["status"] == "ok" and body["board"]["revision"] == 2


def test_settling_a_battle_judges_the_outcome_from_the_record_stream(
    registry, runs_dir: Path
) -> None:
    """客户端自报的 ``won`` 不作数：胜负从这一局的 ``game_end`` 读。"""
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]
    assert entered_body["session_id"], "战斗节点要回会话 id"
    session = registry.get(entered_body["session_id"])
    drive_to_the_end(session)
    assert session.engine.records[-1].kind == "game_end"

    status, body = settled(
        registry,
        created["run_id"],
        entered_body["revision"],
        session_id=entered_body["session_id"],
        won=False,
        picks=[],
    )

    assert status == 200, body
    assert body["outcome"] == "continue", "服务端判胜 ⇒ 客户端的 won=False 被忽略"
    assert body["board"]["run"]["node_status"]["w1/win"] == "completed"
    assert [event["kind"] for event in body["events"]][:1] == ["coins"]
    assert body["events"][0]["reason"] == "rewards", "全队模板奖励累加（tpl.grunt +2）"
    with pytest.raises(KeyError):
        registry.get(entered_body["session_id"])
    assert len(list(runs_dir.glob("*.meta.json"))) == 1, "打完的战斗照常留一份录像"


def test_a_story_defeat_ends_the_run_and_deletes_both_files(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "lose", created["revision"])[1]
    session = registry.get(entered_body["session_id"])
    drive_to_the_end(session)
    end = session.engine.records[-1]
    assert end.kind == "game_end" and end.side == "enemy", "限时清剿跨过上限 ⇒ 败"

    status, body = settled(
        registry,
        created["run_id"],
        entered_body["revision"],
        session_id=entered_body["session_id"],
        won=True,
        picks=[],
    )

    assert status == 200, body
    assert body["outcome"] == "failed", "服务端判败 ⇒ 客户端的 won=True 被忽略"
    assert body["board"] is None
    assert body["save"]["status"] == "missing"
    assert not save_path(runs_dir).exists(), "失败即删主档"
    assert not backup_path(runs_dir).exists(), "连 .bak 一起删"


def test_the_last_exit_node_clears_the_run_and_deletes_the_save(registry, runs_dir: Path) -> None:
    created = _from_create(registry, script_id="clearable")
    shop = entered(registry, created["run_id"], "shop", created["revision"])[1]
    assert shop["layer"]["id"] == "c1" and shop["pending"]["spec"]["kind"] == "shop"

    status, cleared = settled(registry, created["run_id"], shop["revision"], purchases=[])

    assert status == 200, cleared
    assert cleared["outcome"] == "cleared"
    assert cleared["board"] is None
    assert cleared["save"]["status"] == "missing"
    assert not save_path(runs_dir).exists()
    assert not backup_path(runs_dir).exists()
    assert call(registry, "GET", "/api/script-save")[1]["status"] == "missing"


def test_settling_without_a_pending_node_or_an_unfinished_battle_is_a_400(registry) -> None:
    created = _from_create(registry)
    status, body = settled(registry, created["run_id"], 0)
    assert status == 400 and "pending" in body["error"].lower(), body

    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]
    status, body = settled(
        registry,
        created["run_id"],
        entered_body["revision"],
        session_id=entered_body["session_id"],
    )
    assert status == 400, body
    assert "终局" in body["error"] or "结束" in body["error"]


def test_settling_with_an_unknown_session_is_a_404(registry) -> None:
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]

    status, body = settled(
        registry,
        created["run_id"],
        entered_body["revision"],
        session_id="s999",
    )

    assert status == 404, body


# ---------------------------------------------------------------------------
# E. 背包：立即写、幂等、形状护栏
# ---------------------------------------------------------------------------


def test_the_backpack_endpoint_writes_immediately_and_is_idempotent(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    slots = list(created["run"]["backpack"]["slots"])
    slots[0] = "hero.liubei.rende"
    slots[5] = "hero.guanyu.wusheng"

    status, body = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 0, "slots": slots},
    )
    assert status == 200, body
    assert body["revision"] == 1
    assert body["run"]["backpack"]["slots"] == slots
    saved = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    assert saved["run"]["backpack"]["slots"] == slots, "立即写"

    status, again = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 1, "slots": slots, "equipped_slots": [0, 1, 2, 3]},
    )
    assert status == 200 and again["revision"] == 1, "同内容 ⇒ 不写真、不 +1"


def test_the_backpack_endpoint_refuses_duplicates_and_edits_inside_a_node(registry) -> None:
    created = _from_create(registry)
    duplicate = [None] * 24
    duplicate[0] = duplicate[1] = "hero.liubei.rende"
    status, body = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 0, "slots": duplicate},
    )
    assert status == 400 and "重复" in body["error"], body

    entered_body = entered(registry, created["run_id"], "event", created["revision"])[1]
    status, body = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": entered_body["revision"], "slots": [None] * 24},
    )
    assert status == 400 and "节点" in body["error"], body


def test_the_backpack_endpoint_refuses_equipped_slots_that_are_not_the_front_four(
    registry,
) -> None:
    """出战槽恒为**前 4 格**（spec「24 格、其中前 4 格是出战槽」；票 35 收紧）。

    UI 从不移动出战槽（票 22 M7-22c），故服务端直接拒掉别的四格 / 换序：``equipped_skills``
    按 ``equipped_slots`` 的次序读出战技能，放行任意四格等于让写请求悄悄改技能的出战顺序。
    """
    created = _from_create(registry)
    slots = list(created["run"]["backpack"]["slots"])
    slots[0] = "hero.liubei.rende"

    for equipped in ([1, 2, 3, 4], [3, 2, 1, 0]):
        status, body = call(
            registry,
            "PUT",
            f"/api/script-runs/{created['run_id']}/backpack",
            {"expected_revision": 0, "slots": slots, "equipped_slots": equipped},
        )
        assert status == 400 and "出战槽" in body["error"], (equipped, status, body)
        assert json.loads(save_path(registry.runs_dir).read_text(encoding="utf-8"))[
            "run"
        ]["revision"] == 0, "被拒的写请求不改档"

    # 格子数不对（3 格）仍按模型既有的形状门拒（这里只判状态码，判据归 test_script_state）。
    status, body = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 0, "slots": slots, "equipped_slots": [0, 1, 2]},
    )
    assert status == 400, body

    status, body = call(
        registry,
        "PUT",
        f"/api/script-runs/{created['run_id']}/backpack",
        {"expected_revision": 0, "slots": slots, "equipped_slots": [0, 1, 2, 3]},
    )
    assert status == 200, body
    assert body["revision"] == 1 and body["run"]["backpack"]["equipped_slots"] == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# F. 退出即作废（suspend / 僵尸会话）
# ---------------------------------------------------------------------------


def test_suspending_inside_a_battle_abandons_the_session_without_a_replay(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]
    session = registry.get(entered_body["session_id"])
    session.timeouts = 0
    # 打到终局但**一次都没取增量**（还没落盘）：作废后它也不许再落盘。
    drive_to_the_end(session)
    assert session.finished and list(runs_dir.glob("*.meta.json")) == []
    before = save_path(runs_dir).read_bytes()

    status, body = call(
        registry, "POST", f"/api/script-runs/{created['run_id']}/suspend", {}
    )

    assert status == 200, body
    assert body["suspended"] is True
    assert save_path(runs_dir).read_bytes() == before, "suspend 不写档"
    # 旧会话：404 + 不再被推进（timeouts 不增长）。
    assert call(registry, "GET", f"/api/sessions/{entered_body['session_id']}")[0] == 404
    assert call(
        registry,
        "POST",
        f"/api/sessions/{entered_body['session_id']}/answer",
        {"action": {"kind": "pass", "answers_seq": 0}},
    )[0] == 404
    assert session.timeouts == 0
    # 被放弃的战斗不产生录像：迟到的 GET（另一标签 / 在途请求）也推不动它。
    assert registry.save_if_finished(session) is None
    assert list(runs_dir.glob("*.meta.json")) == []
    assert list(runs_dir.glob("*.jsonl")) == []


def test_suspending_on_the_board_is_a_no_op(registry, runs_dir: Path) -> None:
    created = _from_create(registry)
    before = save_path(runs_dir).read_bytes()

    status, body = call(
        registry, "POST", f"/api/script-runs/{created['run_id']}/suspend", {}
    )

    assert status == 200 and body["suspended"] is False
    assert save_path(runs_dir).read_bytes() == before
    assert registry.sessions == {}


def test_entering_a_battle_again_reuses_the_spec_and_replaces_the_zombie(registry) -> None:
    created = _from_create(registry)
    first = entered(registry, created["run_id"], "win", created["revision"])[1]
    second = entered(registry, created["run_id"], "win", first["revision"])[1]

    assert second["revision"] == first["revision"], "同一节点再进一次不重抽、不写档"
    assert second["pending"]["spec"] == first["pending"]["spec"]
    assert second["session_id"] != first["session_id"], "同一个 run 同时最多一个战斗会话"
    assert call(registry, "GET", f"/api/sessions/{first['session_id']}")[0] == 404
    assert call(registry, "GET", f"/api/sessions/{second['session_id']}")[0] == 200

    status, resumed = call(
        registry, "POST", f"/api/script-runs/{created['run_id']}/resume", {}
    )
    assert status == 200 and resumed["revision"] == second["revision"]
    assert call(registry, "GET", f"/api/sessions/{second['session_id']}")[0] == 404, (
        "「继续」也按 run_id 作废未结算的会话（同一 run 同时最多一个）"
    )


def test_a_finished_script_battle_replays_from_its_sidecar_meta(registry, runs_dir: Path) -> None:
    """打完的剧本战斗仍是一局普通录像：侧车 meta 里的 ``rule_config`` 够重建同一场。

    PvE 的座位表在 header 里（``rule_config.pve.seats``），侧车的 ``heroes`` 对 AI 座位
    恒为 ``None``——重建时两条路互斥，故 ``engine_from_meta`` 只走座位表这一条。
    """
    from sgs.engine import dump_journal
    from sgs_web.replay import list_runs, load_run, replay_journal

    created = _from_create(registry)
    entered_body = entered(registry, created["run_id"], "win", created["revision"])[1]
    session = registry.get(entered_body["session_id"])
    drive_to_the_end(session)
    assert registry.save_if_finished(session) is not None

    names = [entry["name"] for entry in list_runs(runs_dir)]
    assert len(names) == 1, "打完的战斗照常落一份录像"
    payload = load_run(runs_dir, names[0])
    assert payload["meta"]["rule_config"]["pve"]["seats"][1]["name"] == "黄巾力士"
    replayed = replay_journal(payload["meta"])
    assert dump_journal(replayed) == dump_journal(session.records())


# ---------------------------------------------------------------------------
# G. 放弃存档 / 恢复
# ---------------------------------------------------------------------------


def test_discarding_the_save_deletes_both_files_and_unlocks_the_value_table(
    registry, runs_dir: Path
) -> None:
    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", created["revision"])[0] == 200
    assert save_path(runs_dir).exists() and backup_path(runs_dir).exists()
    assert call(registry, "GET", "/api/script-save")[1]["value_table_locked"] is True

    status, body = call(registry, "DELETE", "/api/script-save?expected_revision=1")

    assert status == 200, body
    assert body["save"]["status"] == "missing"
    assert not save_path(runs_dir).exists() and not backup_path(runs_dir).exists()
    assert call(registry, "GET", "/api/script-save")[1]["status"] == "missing"
    assert call(registry, "GET", "/api/script-save")[1]["value_table_locked"] is False


def test_discarding_refuses_a_stale_revision_and_a_missing_save(
    registry, runs_dir: Path
) -> None:
    assert call(registry, "DELETE", "/api/script-save")[0] == 404

    created = _from_create(registry)
    assert entered(registry, created["run_id"], "event", created["revision"])[0] == 200
    before = save_path(runs_dir).read_bytes()

    status, conflict = call(registry, "DELETE", "/api/script-save?expected_revision=0")
    assert status == 409, conflict
    assert conflict["revision"] == 1
    assert save_path(runs_dir).read_bytes() == before


def test_a_broken_save_can_still_be_discarded(registry, runs_dir: Path) -> None:
    _from_create(registry)
    save_path(runs_dir).write_text("{ not json", encoding="utf-8")

    status, body = call(registry, "DELETE", "/api/script-save")

    assert status == 200, body
    assert body["save"]["status"] == "missing"
    assert not save_path(runs_dir).exists()


# ---------------------------------------------------------------------------
# H. 锁表：有档时本地预设改了也不影响本轮；删档后按本地预设
# ---------------------------------------------------------------------------


def _disable_every_skill(runs_dir: Path) -> None:
    base = load_skill_values()
    save_skill_value_preset(
        slot=0,
        preset=SkillValuePreset(
            skills={
                skill_id: SkillValueOverride(enabled=False)
                for skill_id in base.enabled_skill_ids()
            }
        ),
        path=runs_dir / "skill-value-presets.json",
        base=base,
    )


def test_a_frozen_value_table_wins_over_the_local_preset(registry, runs_dir: Path) -> None:
    _disable_every_skill(runs_dir)
    created = _from_create(registry, script_id="clearable")
    saved = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    assert all(not row["enabled"] for row in saved["value_table"]["skills"].values())

    # 进入商店：库存现算自**档内**的 value_table ⇒ 一条可卖技能都没有。
    shop = entered(registry, created["run_id"], "shop", created["revision"])[1]
    assert shop["pending"]["spec"]["kind"] == "shop"
    assert shop["pending"]["spec"]["stock"]["skills"] == [], "档内表全禁用 ⇒ 商店无货"

    # 改本地预设（改成空 = 默认表），本轮不受影响。
    save_skill_value_preset(
        slot=0,
        preset=SkillValuePreset(),
        path=runs_dir / "skill-value-presets.json",
        base=load_skill_values(),
    )
    after = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    assert after["value_table"] == saved["value_table"], "档内价值表不随本地预设漂移"

    # 删档后按本地预设：新开一轮的表回到默认（全启用），商店有货。
    assert call(registry, "DELETE", f"/api/script-save?expected_revision={shop['revision']}")[0] == 200
    reopened = _from_create(registry, script_id="clearable", seed=SEED + 1)
    fresh = json.loads(save_path(runs_dir).read_text(encoding="utf-8"))
    assert any(row["enabled"] for row in fresh["value_table"]["skills"].values())
    assert reopened["run_id"] == f"{SEED + 1}-1"
    restocked = entered(registry, reopened["run_id"], "shop", reopened["revision"])[1]
    assert restocked["pending"]["spec"]["stock"]["skills"], "本地预设恢复 ⇒ 商店有货"
