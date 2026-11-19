"""剧本编辑器的**库端点专测**（M7 票 24）：用户库读写 / 校验面板 / D30 删除 / 解除连线。

落点与口径（spec §4「删除与解除连线」、A8；票 03 §9 的 D30 追加段；票 14 的加载器）：

- 全部请求走**真的** ``sgs_web.serve._Handler``（进程内假 socket，与 ``tests/test_web.py``
  的同名帮手同一条缝）：解析、路由、状态码与 JSON 都是产品代码给出的；
- 进料是 ``tests/support/script_mode.py`` 里**过得了整份校验器**的那份库（``save_library``），
  不读内置剧本——内置内容随票 28 变，本票判的是接线与规则；
- 用户库文件是 ``runs/script-library.json``（票 03 §7 钉的名字）：本文件同时断言它的
  原子写（``.tmp`` + ``.bak``）、``revision`` 乐观锁与"允许保存坏库"（D30）。
"""

from __future__ import annotations

import json
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
    DataLoadError,
    LoadError,
    Script,
    ScriptLibrary,
    library_digest,
    load_builtin_script_library,
    load_script_library,
    script_digest,
)

from support import script_mode  # noqa: E402

LIBRARY_FILENAME = "script-library.json"
"""用户剧本库的文件名（票 03 §7 / 票 24 的落点）。"""


# ---------------------------------------------------------------------------
# 进料
# ---------------------------------------------------------------------------


def library() -> ScriptLibrary:
    """一份**过得了**票 14 整份校验器的库：剧本 ``save`` + 大节 ``s1`` + 全部共享表。"""
    return script_mode.save_library()


def dump(value) -> dict:
    """模型 → 磁盘形状（``by_alias=True``：``Edge.from`` 的键是 ``from``）。"""
    return value.model_dump(mode="json", by_alias=True)


def as_dict(error: LoadError) -> dict:
    return {
        "file": error.file,
        "loc": error.loc,
        "expected": error.expected,
        "got": error.got,
        "rule_id": error.rule_id,
    }


# ---------------------------------------------------------------------------
# 进程内的真处理器（与 tests/test_web_script.py 的同名帮手同一条缝）
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
    directory = tmp_path / "runs"
    directory.mkdir()
    return directory


@pytest.fixture()
def registry(runs_dir: Path):
    from sgs_web.serve import GameRegistry

    return GameRegistry(runs_dir=runs_dir)


def library_path(runs_dir: Path) -> Path:
    return runs_dir / LIBRARY_FILENAME


def write_user_library(runs_dir: Path, value: ScriptLibrary) -> bytes:
    """手写一份用户库（模拟"上次编辑器保存的"）；返回落盘字节。"""
    text = json.dumps(dump(value), ensure_ascii=False, sort_keys=True, indent=2)
    path = library_path(runs_dir)
    path.write_text(text, encoding="utf-8")
    return path.read_bytes()


def get_library(registry) -> tuple[int, dict]:
    return call(registry, "GET", "/api/script-library")


def put_library(registry, value: ScriptLibrary | dict, revision: str) -> tuple[int, dict]:
    library_body = dump(value) if isinstance(value, ScriptLibrary) else value
    return call(
        registry,
        "PUT",
        "/api/script-library",
        {"expected_revision": revision, "library": library_body},
    )


def layer_of(body: dict, index: int = 0) -> dict:
    """写端点返回体里的某一个大节（``library`` 是整库的磁盘形状）。"""
    return body["library"]["scripts"][0]["layers"][index]


def edge_pairs(body: dict, index: int = 0) -> set[tuple[str, str]]:
    return {(edge["from"], edge["to"]) for edge in layer_of(body, index)["edges"]}


def revision_of(registry) -> str:
    status, body = get_library(registry)
    assert status == 200, body
    return body["revision"]


# ---------------------------------------------------------------------------
# A. 库的读（GET）与校验面板的数据源
# ---------------------------------------------------------------------------


def test_the_library_endpoint_serves_the_builtin_library_until_the_first_write(
    registry, runs_dir: Path
) -> None:
    status, body = get_library(registry)

    assert status == 200, body
    builtin = load_builtin_script_library()
    assert body["source"] == "builtin"
    assert body["revision"] == library_digest(builtin)
    assert [script["id"] for script in body["library"]["scripts"]] == [
        script.id for script in builtin.scripts
    ]
    assert body["errors"] == []
    assert not library_path(runs_dir).exists(), "GET 不落盘（内置只是只读进料）"


def test_the_library_endpoint_reads_the_user_file_and_reports_no_errors(
    registry, runs_dir: Path
) -> None:
    value = library()
    write_user_library(runs_dir, value)

    status, body = get_library(registry)

    assert status == 200, body
    assert body["source"] == "user"
    assert body["revision"] == library_digest(value)
    assert [script["id"] for script in body["library"]["scripts"]] == ["save"]
    assert body["errors"] == []


def test_the_validate_endpoint_uses_the_loaders_own_rule_lists(
    registry, runs_dir: Path
) -> None:
    """校验面板与加载器**同源**：同一份坏库两边报完全相同的 ``loc/expected/rule_id``。"""
    from sgs.data.scripts import validate_script_library as loader_validator
    from sgs_web import script_library as editor

    assert editor.validate_script_library is loader_validator, (
        "编辑器的校验必须是加载器那一个函数对象——第二份实现会漂移"
    )
    broken = dump(library())
    broken["rooms"] = [room for room in broken["rooms"] if room["id"] != "room.story"]
    expected = loader_validator(broken, file=str(library_path(runs_dir)))

    status, body = call(registry, "POST", "/api/script-library/validate", {"library": broken})

    assert status == 200, body
    assert body["ok"] is False
    assert body["errors"] == [as_dict(error) for error in expected]
    assert [error["loc"] for error in body["errors"]] == ["/scripts/0/layers/0/nodes/0/room"]
    assert body["errors"][0]["rule_id"] == "script_library.unknown_room"


def test_the_validate_endpoint_on_a_healthy_library_is_empty(
    registry, runs_dir: Path
) -> None:
    value = library()
    status, body = call(
        registry, "POST", "/api/script-library/validate", {"library": dump(value)}
    )

    assert status == 200, body
    assert body["ok"] is True
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# B. 库的写（PUT）：原子写 + .bak、坏值拒绝且文件不变、revision 409
# ---------------------------------------------------------------------------


def test_the_first_library_write_is_atomic_and_the_second_keeps_a_backup(
    registry, runs_dir: Path
) -> None:
    value = library()
    status, body = put_library(registry, value, revision_of(registry))

    assert status == 200, body
    path = library_path(runs_dir)
    assert path.exists()
    assert not path.with_name(f"{path.name}.tmp").exists(), "写盘不留 .tmp"
    first = path.read_bytes()
    assert json.loads(first)["scripts"][0]["id"] == "save"
    assert body["revision"] == library_digest(value)
    assert body["errors"] == []
    assert not path.with_name(f"{path.name}.bak").exists(), "第一次写没有旧档可轮换"

    status, body = put_library(registry, value, body["revision"])

    assert status == 200, body
    assert path.read_bytes() == first
    assert path.with_name(f"{path.name}.bak").read_bytes() == first
    assert not path.with_name(f"{path.name}.tmp").exists()


def test_a_library_that_cannot_be_modelled_is_refused_and_the_file_is_untouched(
    registry, runs_dir: Path
) -> None:
    value = library()
    status, body = put_library(registry, value, revision_of(registry))
    assert status == 200, body
    before = library_path(runs_dir).read_bytes()
    revision = body["revision"]
    good = dump(value)

    missing_version = dict(good)
    del missing_version["script_schema_version"]
    unknown_version = {**good, "script_schema_version": 99}
    structural = {**good, "scripts": [{"id": "save", "name": 3}]}
    extra_key = {**good, "unknown_table": []}

    for bad in (missing_version, unknown_version, structural, extra_key):
        status, body = put_library(registry, bad, revision)
        assert status == 400, (bad, status, body)
        assert library_path(runs_dir).read_bytes() == before, "坏值不落盘"


def test_a_stale_library_revision_is_a_409_and_the_file_stays_untouched(
    registry, runs_dir: Path
) -> None:
    first = library()
    stale = revision_of(registry)
    status, body = put_library(registry, first, stale)
    assert status == 200, body
    current = body["revision"]
    before = library_path(runs_dir).read_bytes()

    second = dump(first)
    second["scripts"][0]["name"] = "改过的名字"
    status, body = put_library(registry, second, stale)

    assert status == 409, body
    assert body["revision"] == current
    assert library_path(runs_dir).read_bytes() == before


def test_a_write_without_a_revision_is_a_409_once_the_user_library_exists(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    current = revision_of(registry)
    status, body = call(
        registry, "PUT", "/api/script-library", {"library": dump(library())}
    )

    assert status == 409, body
    assert body["revision"] == current


# ---------------------------------------------------------------------------
# C. D30：允许保存坏库；编辑器宽松 / 运行期严格，同一条错误
# ---------------------------------------------------------------------------


def test_a_broken_library_is_saved_and_the_runtime_refuses_the_same_error(
    registry, runs_dir: Path
) -> None:
    """D30：删完被引用体之后**仍能保存**；面板立刻标红，运行期按同一条拒绝启动。"""
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/rooms/room.story?expected_revision={body['revision']}",
    )
    assert status == 200, body
    panel = body["errors"]
    assert [error["loc"] for error in panel] == ["/scripts/0/layers/0/nodes/0/room"]
    assert [error["rule_id"] for error in panel] == ["script_library.unknown_room"]
    assert body["warnings"] == panel, "被引用点列出来只警告"
    assert library_path(runs_dir).exists()

    # 编辑器读同一份库：宽松档（不因为断链拒绝打开），面板照旧红。
    status, body = get_library(registry)
    assert status == 200, body
    assert body["errors"] == panel

    # 运行期读同一份库：严格档，报**完全相同**的一条（file/loc/expected/got/rule_id）。
    path = library_path(runs_dir)
    with pytest.raises(DataLoadError) as caught:
        load_script_library(path.read_bytes(), file=str(path))
    runtime_errors = [as_dict(error) for error in caught.value.errors]
    assert runtime_errors == panel

    status, body = call(
        registry,
        "POST",
        "/api/script-runs",
        {"script_id": "save", "chosen_hero": "jie_liubei", "seed": 7},
    )
    assert status == 400, body
    assert "unknown_room" in body["error"]


def test_the_references_query_lists_the_sites_a_delete_would_dangle(
    registry, runs_dir: Path
) -> None:
    """D30 的确认框要**删之前**拿到引用点：``references`` 与删除后的 ``warnings`` 同一条。"""
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry, "GET", "/api/script-library/rooms/room.story/references"
    )
    assert status == 200, body
    references = body["references"]
    assert [error["loc"] for error in references] == ["/scripts/0/layers/0/nodes/0/room"]
    assert [error["rule_id"] for error in references] == ["script_library.unknown_room"]
    assert references[0]["got"] == "room.story"

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/rooms/room.story?expected_revision={body['revision']}",
    )
    assert status == 200, body
    assert body["warnings"] == references, "同一个判据：引用点列表 == 删除后的警告"


def test_an_unreferenced_shared_entity_has_no_references(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry, "GET", "/api/script-library/skill_pools/pool.extra/references"
    )

    assert status == 200, body
    assert body["references"] == []


# ---------------------------------------------------------------------------
# D. D30 归属删除：级联、永不拒绝
# ---------------------------------------------------------------------------


def test_deleting_a_layer_cascades_from_the_script_and_is_never_refused(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    revision = body["revision"]

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/scripts/save/layers/s1?expected_revision={revision}",
    )

    assert status == 200, body
    script = body["library"]["scripts"][0]
    assert script["layers"] == [], "删大节 = 从剧本 layers 里摘掉（归属，不是引用）"
    assert script["version"] == 2, "剧本语义变了 ⇒ version +1"
    assert body["warnings"] == [] and body["errors"] == []


def test_deleting_a_node_takes_its_incident_edges_and_no_others(
    registry, runs_dir: Path
) -> None:
    value = library()
    status, body = put_library(registry, value, revision_of(registry))
    assert status == 200, body
    edges_before = {
        (edge["from"], edge["to"])
        for edge in dump(value)["scripts"][0]["layers"][0]["edges"]
    }
    assert ("entry", "shop") in edges_before and ("shop", "exit") in edges_before

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/scripts/save/layers/s1/nodes/shop"
        f"?expected_revision={body['revision']}",
    )

    assert status == 200, body
    layer = layer_of(body)
    assert [node["id"] for node in layer["nodes"]] == [
        "entry",
        "event",
        "unknown",
        "exit",
    ]
    remaining = edge_pairs(body)
    assert remaining == edges_before - {("entry", "shop"), ("shop", "exit")}
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# E. 解除连线：删一条 = 只影响这一条；重复边明确禁止
# ---------------------------------------------------------------------------


def test_unlinking_an_edge_touches_only_that_edge_and_can_be_redrawn(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    base = "/api/script-library/scripts/save/layers/s1"

    status, body = call(
        registry,
        "DELETE",
        f"{base}/edges/entry/event?expected_revision={body['revision']}",
    )
    assert status == 200, body
    edges = edge_pairs(body)
    assert ("entry", "event") not in edges
    assert len(edges) == 5, "只摘掉被解的那一条，别的边一条不动"

    status, body = call(
        registry,
        "POST",
        f"{base}/edges",
        {"expected_revision": body["revision"], "entity": {"from": "entry", "to": "event"}},
    )
    assert status == 200, body
    assert body["entity"] == {
        "from": "entry",
        "to": "event",
        "requires": {"flags": [], "not_flags": []},
    }
    edges = edge_pairs(body)
    assert ("entry", "event") in edges and len(edges) == 6

    status, body = call(
        registry,
        "POST",
        f"{base}/edges",
        {"expected_revision": body["revision"], "entity": {"from": "entry", "to": "event"}},
    )
    assert status == 409, ("重复边明确禁止（边身份 = from/to）", body)
    status, body = call(registry, "GET", f"{base}/edges")
    pairs = [(edge["from"], edge["to"]) for edge in body["items"]]
    assert pairs.count(("entry", "event")) == 1


def test_unlinking_an_edge_that_is_not_there_is_a_404(registry, runs_dir: Path) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/scripts/save/layers/s1/edges/exit/entry"
        f"?expected_revision={body['revision']}",
    )

    assert status == 404, body


# ---------------------------------------------------------------------------
# F. 另存为副本 / 重命名保引用
# ---------------------------------------------------------------------------


def test_cloning_a_script_rewrites_the_id_and_the_copies_are_independent(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    status, original = call(registry, "GET", "/api/script-library/scripts/save")
    assert status == 200, original

    status, body = call(
        registry,
        "POST",
        "/api/script-library/scripts/save/clone",
        {"expected_revision": body["revision"]},
    )

    assert status == 200, body
    assert body["entity"]["id"] == "save-copy"
    assert [script["id"] for script in body["library"]["scripts"]] == ["save", "save-copy"]
    assert body["errors"] == []

    copy = body["entity"]
    status, body = call(
        registry,
        "PUT",
        "/api/script-library/scripts/save-copy",
        {"expected_revision": body["revision"], "entity": {**copy, "name": "副本改名"}},
    )
    assert status == 200, body
    scripts = {script["id"]: script for script in body["library"]["scripts"]}
    assert scripts["save-copy"]["name"] == "副本改名"
    assert scripts["save-copy"]["version"] == 2
    assert scripts["save"]["name"] == "存档剧本"
    status, original_after = call(registry, "GET", "/api/script-library/scripts/save")
    assert original_after["digest"] == original["digest"], "改副本不改原件"
    assert original_after["entity"]["version"] == 1


SHARED_RENAMES = (
    ("rooms", "room.story"),
    ("templates", "tpl.grunt"),
    ("skill_pools", "pool.grunts"),
    ("events", "ev.village"),
    ("event_pools", "ep.village"),
    ("global_skills", "gs.enemy"),
)


def test_renaming_each_shared_entity_keeps_every_reference_resolved(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    revision = body["revision"]

    for kind, ident in SHARED_RENAMES:
        new_id = f"{ident}.renamed"
        status, body = call(
            registry,
            "POST",
            f"/api/script-library/{kind}/{ident}/rename",
            {"expected_revision": revision, "new_id": new_id},
        )
        assert status == 200, (kind, body)
        assert body["errors"] == [], (kind, body["errors"])
        text = json.dumps(body["library"], ensure_ascii=False)
        assert f'"{new_id}"' in text, kind
        assert f'"{ident}"' not in text, f"{kind}: 旧 id 仍有引用点"
        revision = body["revision"]


def test_renaming_a_node_rewrites_its_edge_endpoints(registry, runs_dir: Path) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry,
        "POST",
        "/api/script-library/scripts/save/layers/s1/nodes/entry/rename",
        {"expected_revision": body["revision"], "new_id": "entry2"},
    )

    assert status == 200, body
    layer = body["library"]["scripts"][0]["layers"][0]
    assert "entry" not in [node["id"] for node in layer["nodes"]]
    assert [edge["from"] for edge in layer["edges"] if edge["from"] == "entry"] == []
    assert len([edge for edge in layer["edges"] if edge["from"] == "entry2"]) == 3
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# G. 导入内置：克隆可编辑 / 原件只读 / id 不冲突
# ---------------------------------------------------------------------------


def test_importing_the_builtin_library_makes_an_editable_copy(
    registry, runs_dir: Path
) -> None:
    builtin_file = (
        REPO_ROOT / "src" / "sgs" / "data" / "files" / "scripts.huangjin.json"
    )
    builtin_before = builtin_file.read_bytes()
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body

    status, body = call(
        registry,
        "POST",
        "/api/script-library/import-builtin",
        {"expected_revision": body["revision"]},
    )

    assert status == 200, body
    assert [script["id"] for script in body["library"]["scripts"]] == [
        "save",
        "huangjin",
    ]
    assert body["imported"] == [{"id": "huangjin", "name": "讨伐黄巾军"}]
    assert body["errors"] == []

    imported = next(
        script for script in body["library"]["scripts"] if script["id"] == "huangjin"
    )
    status, body = call(
        registry,
        "PUT",
        "/api/script-library/scripts/huangjin",
        {"expected_revision": body["revision"], "entity": {**imported, "name": "我的黄巾军"}},
    )
    assert status == 200, body
    assert next(
        script for script in body["library"]["scripts"] if script["id"] == "huangjin"
    )["name"] == "我的黄巾军"
    assert builtin_file.read_bytes() == builtin_before, "内置库原件只读"

    status, body = call(
        registry,
        "POST",
        "/api/script-library/import-builtin",
        {"expected_revision": body["revision"]},
    )
    assert status == 200, body
    assert [script["id"] for script in body["library"]["scripts"]] == [
        "save",
        "huangjin",
        "huangjin-imported",
    ], "再导一次不撞 id"
    assert body["errors"] == []


# ---------------------------------------------------------------------------
# H. 每张表都是 CRUD；战斗 / 座位是房间归属；版本与指纹现算
# ---------------------------------------------------------------------------


NEW_ENTITIES = {
    "rooms": {"id": "room.new", "name": "新房间", "kind": "shop"},
    "templates": {
        "id": "tpl.new",
        "name": "新模板",
        "band": "early",
        "hp": 3,
        "hp_max": 3,
        "random": {"pool": "pool.empty", "count": [0, 0]},
    },
    "skill_pools": {"id": "pool.new", "name": "新技能池", "skills": ["hero.guanyu.wusheng"]},
    "events": {"id": "ev.new", "name": "新事件", "text": "一段文案"},
    "event_pools": {"id": "ep.new", "name": "新事件池", "events": ["ev.village"]},
    "global_skills": {
        "id": "gs.new",
        "name": "新全局技能",
        "side": "ally",
        "scale": {"hp": [0, 0], "draw": [0, 0], "slash": [1, 1]},
    },
    "scripts": {
        "id": "newscript",
        "name": "新剧本",
        "roster": ["nengren", "jie_liubei", "jie_guanyu"],
        "layers": [],
    },
}


@pytest.mark.parametrize("kind", sorted(NEW_ENTITIES))
def test_every_library_table_supports_create_read_update_delete(
    registry, runs_dir: Path, kind: str
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    entity = NEW_ENTITIES[kind]
    base = f"/api/script-library/{kind}"

    status, body = call(
        registry, "POST", base, {"expected_revision": body["revision"], "entity": entity}
    )
    assert status == 200, (kind, body)
    assert body["entity"]["id"] == entity["id"]
    assert body["errors"] == []
    created = body["entity"]
    revision = body["revision"]

    status, body = call(registry, "GET", base)
    assert entity["id"] in [item["id"] for item in body["items"]]

    status, body = call(registry, "GET", f"{base}/{entity['id']}")
    assert status == 200 and body["entity"]["id"] == entity["id"]

    status, body = call(
        registry,
        "PUT",
        f"{base}/{entity['id']}",
        {"expected_revision": revision, "entity": {**created, "name": "改过名字"}},
    )
    assert status == 200, (kind, body)
    assert body["entity"]["name"] == "改过名字"

    status, body = call(
        registry,
        "DELETE",
        f"{base}/{entity['id']}?expected_revision={body['revision']}",
    )
    assert status == 200, (kind, body)
    status, body = call(registry, "GET", base)
    assert entity["id"] not in [item["id"] for item in body["items"]]


def test_battle_and_seats_are_edited_through_their_room(registry, runs_dir: Path) -> None:
    value = library()
    status, body = put_library(registry, value, revision_of(registry))
    assert status == 200, body
    base = "/api/script-library/rooms/room.story/battle"

    status, body = call(registry, "GET", base)
    assert status == 200
    assert body["entity"]["allies"] == dump(value)["rooms"][0]["battle"]["allies"]
    revision = body["revision"]

    status, body = call(
        registry,
        "POST",
        f"{base}/allies",
        {
            "expected_revision": revision,
            "entity": {"role": "ally", "template": "tpl.ally", "protected": False},
        },
    )
    assert status == 200, body
    assert body["entity"] == {
        "role": "ally",
        "template": "tpl.ally",
        "protected": False,
        "boss": False,
    }
    assert body["errors"] == []
    status, body = call(registry, "GET", base)
    assert [seat["protected"] for seat in body["entity"]["allies"]] == [True, False]

    status, body = call(
        registry,
        "PUT",
        f"{base}/allies/1",
        {
            "expected_revision": body["revision"],
            "entity": {"role": "ally", "template": "tpl.ally", "protected": True},
        },
    )
    assert status == 200, body
    assert body["entity"]["protected"] is True

    status, body = call(
        registry,
        "DELETE",
        f"{base}/allies/1?expected_revision={body['revision']}",
    )
    assert status == 200, body
    status, body = call(registry, "GET", base)
    assert len(body["entity"]["allies"]) == 1

    status, body = call(
        registry,
        "DELETE",
        f"{base}?expected_revision={body['revision']}",
    )
    assert status == 200, body
    assert body["entity"] is None
    assert [error["loc"] for error in body["errors"]] == ["/rooms/0/battle"]
    assert [error["rule_id"] for error in body["errors"]] == [
        "script_library.room_payload_missing"
    ]
    assert body["warnings"] == body["errors"], "删战斗是归属删除：留下的窟窿立刻标红"

    status, body = call(
        registry,
        "PUT",
        base,
        {
            "expected_revision": body["revision"],
            "entity": dump(value)["rooms"][0]["battle"],
        },
    )
    assert status == 200, body
    assert body["errors"] == []


def test_saving_a_script_bumps_its_version_and_the_digest_is_recomputed(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    status, before = call(registry, "GET", "/api/script-library/scripts/save")
    assert before["entity"]["version"] == 1
    assert before["digest"].startswith("sha256:")

    script = before["entity"]
    status, body = call(
        registry,
        "PUT",
        "/api/script-library/scripts/save",
        {"expected_revision": body["revision"], "entity": {**script, "name": "改过"}},
    )
    assert status == 200, body
    assert body["entity"]["version"] == 2
    status, after = call(registry, "GET", "/api/script-library/scripts/save")
    assert after["digest"] != before["digest"]
    assert after["digest"] == script_digest(Script.model_validate(after["entity"]))

    status, body = call(
        registry,
        "PUT",
        "/api/script-library/scripts/save",
        {"expected_revision": body["revision"], "entity": {**after["entity"]}},
    )
    assert status == 200, body
    assert body["entity"]["version"] == 2, "内容没变不白涨版本"


def test_a_stale_revision_on_an_entity_endpoint_is_a_409(registry, runs_dir: Path) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    stale = body["revision"]
    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/rooms/room.shop?expected_revision={stale}",
    )
    assert status == 200, body
    before = library_path(runs_dir).read_bytes()

    status, body = call(
        registry,
        "DELETE",
        f"/api/script-library/rooms/room.draw?expected_revision={stale}",
    )

    assert status == 409, body
    assert library_path(runs_dir).read_bytes() == before


def test_a_whole_library_write_bumps_only_the_scripts_that_changed(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    changed = dump(library())
    changed["scripts"][0]["name"] = "整库改过"
    changed["scripts"][0]["version"] = 1  # 客户端送来的版本号不作数（服务端管）

    status, body = put_library(registry, changed, body["revision"])

    assert status == 200, body
    assert body["library"]["scripts"][0]["version"] == 2, "语义变了 ⇒ version +1"
    status, body = put_library(registry, changed, body["revision"])
    assert status == 200, body
    assert body["library"]["scripts"][0]["version"] == 2, "内容没变不白涨版本"


def test_layers_nodes_and_edges_are_created_read_updated_and_deleted(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    base = "/api/script-library/scripts/save/layers"

    status, body = call(
        registry,
        "POST",
        base,
        {
            "expected_revision": body["revision"],
            "entity": {"id": "s2", "name": "新大节", "grid": {"rows": 1, "cols": 2}},
        },
    )
    assert status == 200, body
    assert body["errors"] == []
    status, body = call(registry, "GET", base)
    assert [layer["id"] for layer in body["items"]] == ["s1", "s2"]

    status, body = call(registry, "GET", f"{base}/s2")
    assert status == 200 and body["entity"]["name"] == "新大节"

    for node_id, col in (("a", 0), ("b", 1)):
        status, body = call(
            registry,
            "POST",
            f"{base}/s2/nodes",
            {
                "expected_revision": body["revision"],
                "entity": {"id": node_id, "row": 0, "col": col, "room": "room.shop"},
            },
        )
        assert status == 200, body
        assert body["errors"] == []

    status, body = call(registry, "PUT", f"{base}/s2/nodes/b", {
        "expected_revision": body["revision"],
        "entity": {"id": "b", "row": 0, "col": 1, "room": "room.draw"},
    })
    assert status == 200, body
    assert body["entity"]["room"] == "room.draw"

    status, body = call(
        registry,
        "POST",
        f"{base}/s2/edges",
        {"expected_revision": body["revision"], "entity": {"from": "a", "to": "b"}},
    )
    assert status == 200, body
    assert body["errors"] == []

    status, body = call(registry, "GET", f"{base}/s2/edges/a/b")
    assert status == 200 and body["entity"]["requires"] == {"flags": [], "not_flags": []}

    status, body = call(
        registry,
        "PUT",
        f"{base}/s2/edges/a/b",
        {
            "expected_revision": body["revision"],
            "entity": {"requires": {"flags": ["flag.x"], "not_flags": []}},
        },
    )
    assert status == 200, body
    assert body["entity"]["requires"] == {"flags": ["flag.x"], "not_flags": []}

    status, body = call(
        registry,
        "POST",
        f"{base}/s2/clone",
        {"expected_revision": body["revision"]},
    )
    assert status == 200, body
    assert body["entity"]["id"] == "s2-copy"
    assert body["errors"] == []
    status, body = call(
        registry,
        "POST",
        f"{base}/s2/rename",
        {"expected_revision": body["revision"], "new_id": "s2-renamed"},
    )
    assert status == 200, body
    assert body["entity"]["id"] == "s2-renamed"

    status, body = call(
        registry,
        "DELETE",
        f"{base}/s2-renamed?expected_revision={body['revision']}",
    )
    assert status == 200, body
    status, body = call(registry, "GET", base)
    assert [layer["id"] for layer in body["items"]] == ["s1", "s2-copy"]


CLONE_CASES = (
    ("scripts", "save"),
    ("rooms", "room.story"),
    ("templates", "tpl.grunt"),
    ("skill_pools", "pool.grunts"),
    ("events", "ev.village"),
    ("event_pools", "ep.village"),
    ("global_skills", "gs.enemy"),
)


@pytest.mark.parametrize("kind,ident", CLONE_CASES)
def test_cloning_any_entity_rewrites_the_id_and_leaves_the_original_alone(
    registry, runs_dir: Path, kind: str, ident: str
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    status, before = call(registry, "GET", f"/api/script-library/{kind}/{ident}")
    assert status == 200, before

    status, body = call(
        registry,
        "POST",
        f"/api/script-library/{kind}/{ident}/clone",
        {"expected_revision": before["revision"]},
    )

    assert status == 200, (kind, body)
    assert body["entity"]["id"] == f"{ident}-copy"
    assert body["errors"] == [], (kind, body["errors"])
    status, original = call(registry, "GET", f"/api/script-library/{kind}/{ident}")
    assert original["entity"] == before["entity"], "副本不共享身份：原件逐字段不变"


def test_the_first_write_may_create_an_empty_library_without_a_revision(
    registry, runs_dir: Path
) -> None:
    """票 14 的"空库合法"：编辑器把剧本删光之后，第一次落盘没有旧 revision 可对。"""
    status, body = put_library(
        registry, {"script_schema_version": 1, "scripts": []}, None
    )

    assert status == 200, body
    assert body["library"]["scripts"] == []
    assert body["library"]["rooms"] == []
    assert body["errors"] == []
    status, body = get_library(registry)
    assert body["source"] == "user"
    assert body["library"]["scripts"] == []


def test_create_collisions_and_unknown_ids_follow_the_endpoint_semantics(
    registry, runs_dir: Path
) -> None:
    status, body = put_library(registry, library(), revision_of(registry))
    assert status == 200, body
    entity = NEW_ENTITIES["rooms"]
    base = "/api/script-library/rooms"

    status, body = call(
        registry, "POST", base, {"expected_revision": body["revision"], "entity": entity}
    )
    assert status == 200, body
    status, body = call(
        registry, "POST", base, {"expected_revision": body["revision"], "entity": entity}
    )
    assert status == 409, (body, "同 id 再建一次是冲突，不是覆盖")

    status, body = call(registry, "GET", f"{base}/room.missing")
    assert status == 404, body
    status, body = call(
        registry,
        "DELETE",
        f"{base}/room.missing?expected_revision={revision_of(registry)}",
    )
    assert status == 404, body
