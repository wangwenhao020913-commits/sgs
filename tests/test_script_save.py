"""剧本存档的序列化、三段门与 ``.bak`` 恢复语义（M7 票 18；票 04 §1、§2、§5、§8.5）。

本文件只测**纯函数侧**：``RunState`` ↔ JSON 文本、``script_mode_version`` 门、版本 → 结构 →
语义三段门、``.bak`` 的三态判定（``ok`` / ``missing`` / ``broken``）与 ``revision`` 语义。
文件 I/O（两次 rename）、端点、会话 id 是票 19；本模块一行文件都不碰。

两条进料口径（票面「断言必须走真实产出路径」）：

1. **不手搓 dict**：存档一律经 :class:`ScriptSave` 构造、:func:`dump_save` 出文本、
   :func:`load_save` / :func:`inspect_save` 读回来；``pending`` 一律经
   :func:`sgs.script.enter_node` 物化（不手写 spec 的 dict）。
2. **坏档用「改一份好档的文本」造**：坏档的字节是**真实产出路径的产物**被改坏一格，而不是
   从零手写的假 JSON —— 这样"坏档不静默"测的是真实的解析 / 校验链，不是测试自己的拼装。

``script_snapshot`` 是**整份剧本库**（票 03 §7：大节 / 节点 / 房间 / 模板 / 技能池 / 事件 /
事件池 / 全局技能），故它整份走票 14 的 ``validate_script_library``——同一份结构门，不另写一套。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sgs.data import DataLoadError
from sgs.script import (
    KNOWN_SCRIPT_MODE_VERSIONS,
    SCRIPT_MODE_VERSION,
    BattleSpec,
    EventSpec,
    NodeStatus,
    Pending,
    RunState,
    SaveStatus,
    ScriptSave,
    ShopSpec,
    UnknownSpec,
    dump_save,
    enter_node,
    inspect_save,
    load_save,
    revision_for_write,
    stage_save,
    validate_save,
)
from support import script_mode

SAVED_AT = "2026-11-09T21:04:11+08:00"
"""``saved_at`` 由调用方注入（``src/sgs/**`` 禁时钟）；本票只钉字段，不产时间。"""

FILE = "runs/script-save.json"
"""主档的名字（票 04 §2 的文件名纪律）。"""

BAK_FILE = FILE + ".bak"


# ---------------------------------------------------------------------------
# 进料：一份真实产出的存档
# ---------------------------------------------------------------------------


def _run(**overrides: Any) -> RunState:
    fields: dict[str, Any] = {"script_id": script_mode.SAVE_SCRIPT_ID}
    fields.update(overrides)
    return script_mode.run_state(**fields)


def save(run: RunState | None = None, *, saved_at: str = SAVED_AT) -> ScriptSave:
    """一份合法的存档（快照整份过得了票 14 的校验器）。"""
    return ScriptSave(
        script_mode_version=SCRIPT_MODE_VERSION,
        saved_at=saved_at,
        run=_run() if run is None else run,
        script_snapshot=script_mode.save_library(),
        value_table=script_mode.value_table(),
    )


def entered(node_id: str) -> RunState:
    """经真实路径进一个节点（**进入 = 一次抽完**，票 17 的 ``enter_node``）。"""
    node_status: dict[str, Any] = (
        {} if node_id == "entry" else script_mode.completed(script_mode.SAVE_LAYER_ID, "entry")
    )
    return enter_node(
        run=_run(node_status=node_status),
        library=script_mode.save_library(),
        node_id=node_id,
        value_table=script_mode.value_table(),
    )


def good_text() -> str:
    return dump_save(save())


def broken_text(needle: str, replacement: str, *, text: str | None = None) -> str:
    """把一份**真实产出**的好文本改坏一格（坏档的字节来源仍是生产路径）。"""
    text = good_text() if text is None else text
    assert needle in text, f"前置：好档里应当有 {needle!r}"
    return text.replace(needle, replacement, 1)


# ---------------------------------------------------------------------------
# 1. 往返：dump → load → 逐字段相等
# ---------------------------------------------------------------------------


def test_a_save_round_trips_through_its_json_text() -> None:
    original = save()

    loaded = load_save(dump_save(original), file=FILE)

    for name in ScriptSave.model_fields:
        assert getattr(loaded, name) == getattr(original, name), f"字段 {name} 往返后变了"
    assert loaded == original


def test_the_save_text_carries_exactly_the_five_fields_of_the_ticket() -> None:
    """票 04 §1 的文件顶层：版本 / 时间 / run / 剧本快照 / 锁定价值表，一个不多一个不少。"""
    parsed = json.loads(good_text())

    assert set(parsed) == {
        "script_mode_version",
        "saved_at",
        "run",
        "script_snapshot",
        "value_table",
    }
    assert parsed["script_mode_version"] == SCRIPT_MODE_VERSION
    assert parsed["saved_at"] == SAVED_AT
    # 快照是**整份剧本库**：带库头版本 + 五类共享表（票 03 §7 的自足口径）。
    snapshot = parsed["script_snapshot"]
    assert snapshot["script_schema_version"] == 1
    assert set(snapshot) == {
        "script_schema_version",
        "scripts",
        "rooms",
        "templates",
        "skill_pools",
        "events",
        "event_pools",
        "global_skills",
    }
    # 价值表沿用既有模型／序列化形状，不另造一份。
    assert validate_save(good_text(), file=FILE) == ()
    assert parsed["value_table"] == script_mode.value_table().model_dump(mode="json")


def test_the_dump_is_stable_across_calls() -> None:
    """同一份存档两次 dump 逐字节相同（幂等写的前提；排序键序 + 固定空白）。"""
    assert dump_save(save()) == dump_save(load_save(good_text(), file=FILE))


@pytest.mark.parametrize(
    ("node_id", "spec_type"),
    [
        ("entry", BattleSpec),
        ("event", EventSpec),
        ("shop", ShopSpec),
        ("unknown", UnknownSpec),
    ],
)
def test_every_pending_spec_round_trips_through_the_save_text(
    node_id: str, spec_type: type
) -> None:
    """``pending.spec`` 五种形状各一条：四种顶层 ``kind`` + 揭晓后的内层（嵌在 unknown 里）。"""
    original = save(entered(node_id))
    assert isinstance(original.run.pending.spec, spec_type)

    loaded = load_save(dump_save(original), file=FILE)

    assert loaded.run.pending == original.run.pending
    assert loaded.run.pending.spec.model_dump() == original.run.pending.spec.model_dump()


def test_the_revealed_inner_spec_keeps_the_room_it_drew() -> None:
    """问号房揭晓结果进档：内层房间 + 物化 spec 一起往返（票 16 的唯一形状扩展）。"""
    original = save(entered("unknown"))
    spec = original.run.pending.spec
    assert isinstance(spec, UnknownSpec)

    loaded = load_save(dump_save(original), file=FILE)

    assert isinstance(loaded.run.pending.spec, UnknownSpec)
    assert loaded.run.pending.spec.revealed == spec.revealed


# ---------------------------------------------------------------------------
# 2. 版本门：认不出 ⇒ 拒绝续档、不迁移
# ---------------------------------------------------------------------------


def test_an_unknown_script_mode_version_is_refused_with_a_named_error() -> None:
    """认不出的存档格式版本 ⇒ 拒绝（**不做迁移**），报错带稳定的 ``rule_id``。"""
    text = broken_text(
        f'"script_mode_version": {SCRIPT_MODE_VERSION}', '"script_mode_version": 2'
    )

    with pytest.raises(DataLoadError) as caught:
        load_save(text, file=FILE)

    errors = caught.value.errors
    assert len(errors) == 1
    assert errors[0].rule_id == "script_save.version.unknown"
    assert errors[0].loc == "/script_mode_version"
    assert "2" in errors[0].got
    assert validate_save(text, file=FILE) == errors


def test_the_version_gate_runs_before_the_structure_gate() -> None:
    """版本先行：版本不认识时不再报字段错误（否则修数据的人会先看到一堆误导性的字段错）。"""
    text = broken_text(
        f'"script_mode_version": {SCRIPT_MODE_VERSION}', '"script_mode_version": 2'
    )
    text = broken_text("  \"saved_at\"", "  \"saved_at_typo\"", text=text)

    errors = validate_save(text, file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.version.unknown"]


def test_a_missing_script_mode_version_is_refused() -> None:
    text = broken_text(f'  "script_mode_version": {SCRIPT_MODE_VERSION},\n', "")

    errors = validate_save(text, file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.version.missing"]


def test_the_known_mode_versions_are_the_pinned_one() -> None:
    assert KNOWN_SCRIPT_MODE_VERSIONS == {SCRIPT_MODE_VERSION}


# ---------------------------------------------------------------------------
# 3. 三段门：结构 + 语义（断链不静默）
# ---------------------------------------------------------------------------


def test_an_extra_top_level_key_is_a_structure_error() -> None:
    text = broken_text(
        '"value_table": {', '"available_next": ["n1"],\n  "value_table": {'
    )

    errors = validate_save(text, file=FILE)

    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == "/available_next"


def test_a_dangling_snapshot_reference_is_broken_at_the_reference_site() -> None:
    """断链快照：语义段复用票 14 的库校验器，``loc`` 指回存档里的引用位置。"""
    text = broken_text('"tpl.grunt"', '"tpl.gone"')

    errors = validate_save(text, file=FILE)

    assert [error.rule_id for error in errors] == ["script_library.unknown_template"]
    assert errors[0].loc == "/script_snapshot/rooms/0/battle/enemies/0/template"
    assert errors[0].got == "tpl.gone"


def test_a_run_pointing_at_a_script_missing_from_the_snapshot_is_broken() -> None:
    text = dump_save(save(_run(script_id="nosuch")))

    errors = validate_save(text, file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.unknown_script"]
    assert errors[0].loc == "/run/script_id"


def test_a_pending_node_outside_the_snapshot_is_broken() -> None:
    """``pending`` 指向快照里不存在的节点 ⇒ 断链（续档会当场算不出棋盘）。"""
    valid = entered("event")
    assert valid.pending is not None
    broken = valid.model_copy(
        update={
            "pending": Pending(
                node_id=f"{script_mode.SAVE_LAYER_ID}/nosuch", spec=valid.pending.spec
            )
        }
    )

    errors = validate_save(dump_save(save(broken)), file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.unknown_pending_node"]
    assert errors[0].loc == "/run/pending/node_id"


def test_a_node_status_key_outside_the_snapshot_is_broken() -> None:
    broken = _run().model_copy(
        update={"node_status": {f"{script_mode.SAVE_LAYER_ID}/nosuch": NodeStatus.COMPLETED}}
    )

    errors = validate_save(dump_save(save(broken)), file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.unknown_node_status"]


def test_a_layer_index_outside_the_snapshot_is_broken() -> None:
    errors = validate_save(dump_save(save(_run(layer_index=5))), file=FILE)

    assert [error.rule_id for error in errors] == ["script_save.layer_out_of_range"]
    assert errors[0].loc == "/run/layer_index"


# ---------------------------------------------------------------------------
# 4. 三态：missing / ok / broken + .bak 恢复语义（票 04 §5）
# ---------------------------------------------------------------------------


def test_missing_is_only_when_the_main_and_the_backup_are_both_gone() -> None:
    """``missing`` = 唯一允许的静默：主档与 ``.bak`` 都不在。"""
    view = inspect_save(main=None, backup=None, file=FILE, backup_file=BAK_FILE)

    assert view.status is SaveStatus.MISSING
    assert view.reason == ""
    assert view.errors == () and view.bak_errors == ()
    assert view.save is None and view.backup is None and view.run is None
    assert view.bak_available is False


def test_a_good_main_is_ok_and_names_the_run_the_time_and_the_revision() -> None:
    view = inspect_save(main=good_text(), backup=None, file=FILE)

    assert view.status is SaveStatus.OK
    assert view.run == save().run
    assert view.run is not None and view.run.run_id == save().run.run_id
    assert view.saved_at == SAVED_AT
    assert view.errors == () and view.reason == ""
    assert view.bak_available is False, "没有 .bak ⇒ 不可回滚"

    with_backup = inspect_save(
        main=good_text(), backup=good_text(), file=FILE, backup_file=BAK_FILE
    )
    assert with_backup.status is SaveStatus.OK
    assert with_backup.bak_available is True
    assert with_backup.bak_saved_at == SAVED_AT


def test_an_illegal_json_main_is_broken_not_missing_and_hands_no_repaired_save() -> None:
    """坏档不静默：**不**把坏档当空档、**不**产出一份"修好的"存档（原字节留给票 19 处置）。"""
    view = inspect_save(main=good_text()[:-5], backup=None, file=FILE)

    assert view.status is SaveStatus.BROKEN
    assert view.status is not SaveStatus.MISSING
    assert view.reason
    assert view.save is None and view.run is None
    assert [error.rule_id for error in view.errors] == ["structure.invalid_json"]


def test_a_dangling_snapshot_main_is_broken_with_a_readable_reason() -> None:
    view = inspect_save(
        main=broken_text('"tpl.grunt"', '"tpl.gone"'), backup=None, file=FILE
    )

    assert view.status is SaveStatus.BROKEN
    assert "tpl.gone" in view.reason
    assert view.run is None


def test_a_broken_main_still_offers_the_backup_it_can_recover_from() -> None:
    view = inspect_save(
        main=good_text()[:-5], backup=good_text(), file=FILE, backup_file=BAK_FILE
    )

    assert view.status is SaveStatus.BROKEN
    assert view.bak_available is True
    assert view.backup == save()
    assert view.bak_saved_at == SAVED_AT
    assert view.run is None, "坏档不静默：主档坏了就不该给出一份自动修好的内容"


def test_a_missing_main_with_a_backup_is_broken_not_missing() -> None:
    """「主档缺失 + ``.bak`` 在」判**可恢复的中断**（票 04 §2 的两次 rename 窗口）。"""
    view = inspect_save(main=None, backup=good_text(), file=FILE, backup_file=BAK_FILE)

    assert view.status is SaveStatus.BROKEN
    assert view.status is not SaveStatus.MISSING
    assert "中断" in view.reason
    assert view.bak_available is True and view.backup == save()


def test_recovery_hands_the_backup_back_verbatim_and_keeps_its_revision() -> None:
    """恢复的输入是 ``.bak`` 字节：过三段门后**语义等价于主档内容**，且**不改** ``revision``。"""
    bak = save(_run(revision=7, coins=22))

    view = inspect_save(main=None, backup=dump_save(bak), file=FILE, backup_file=BAK_FILE)

    restored = view.backup
    assert restored is not None
    for name in ScriptSave.model_fields:
        assert getattr(restored, name) == getattr(bak, name), f"字段 {name} 恢复后变了"
    assert restored.run.revision == 7, "恢复不改 revision（票 04 §5）"
    assert dump_save(restored) == dump_save(bak), "恢复写回的就是 .bak 的字节，不产出第二份内容"


def test_a_broken_backup_is_not_offered_for_recovery() -> None:
    view = inspect_save(main=None, backup="{", file=FILE, backup_file=BAK_FILE)

    assert view.status is SaveStatus.BROKEN
    assert view.bak_available is False
    assert view.backup is None
    assert view.bak_errors and view.bak_errors[0].file == BAK_FILE
    assert "备份" in view.reason


def test_a_broken_main_with_a_broken_backup_says_the_backup_is_unusable() -> None:
    view = inspect_save(main="{", backup="{", file=FILE, backup_file=BAK_FILE)

    assert view.status is SaveStatus.BROKEN
    assert view.bak_available is False
    assert "备份" in view.reason


def test_the_backup_is_audited_even_when_the_main_is_ok() -> None:
    """主档 ok ⇒ ``ok``（主角是主档）；备份坏了只是"不能从备份恢复"，不改主档的判定。"""
    view = inspect_save(main=good_text(), backup="{", file=FILE, backup_file=BAK_FILE)

    assert view.status is SaveStatus.OK
    assert view.bak_available is False
    assert view.bak_errors and view.bak_errors[0].file == BAK_FILE


# ---------------------------------------------------------------------------
# 5. 版本四件套：只有 script_mode_version 参与拒绝
# ---------------------------------------------------------------------------


def test_a_script_version_or_digest_mismatch_is_not_a_refusal() -> None:
    """``script.version`` / ``script_digest`` 只做审计（票 03 §7）：改库不影响既有存档。"""
    run = _run(script_version=99, script_digest="sha256:" + "ff" * 32)

    text = dump_save(save(run))

    assert validate_save(text, file=FILE) == ()
    view = inspect_save(main=text, backup=None, file=FILE)
    assert view.status is SaveStatus.OK
    assert view.run is not None
    assert view.run.script_version == 99
    assert view.run.script_digest == "sha256:" + "ff" * 32


# ---------------------------------------------------------------------------
# 6. 节点 = 一个事务（票 04 §2 / §3）
# ---------------------------------------------------------------------------


def test_a_node_in_progress_reloads_as_the_board_before_it_plus_the_pending_node() -> None:
    before = _run(node_status=script_mode.completed(script_mode.SAVE_LAYER_ID, "entry"))
    entered_run = entered("event")
    assert entered_run.pending is not None

    loaded = load_save(dump_save(save(entered_run)), file=FILE).run

    assert loaded.pending == entered_run.pending
    board = {"exclude": {"pending"}}
    assert loaded.model_dump(**board) == before.model_dump(**board), (
        "读档回来 = 进入前的棋盘 + pending（节点内不落任何别的状态）"
    )


def test_reading_the_same_node_twice_never_draws_a_second_spec() -> None:
    """S_enter 已落盘 ⇒ 同一 ``node_id`` 再取一次拿到的是**逐字段相同**的 spec（不重抽）。"""
    text = dump_save(save(entered("unknown")))

    first = load_save(text, file=FILE).run
    second = load_save(text, file=FILE).run

    assert first.pending == second.pending
    assert dump_save(save(first)) == text, "再写一遍是同一份字节：节点内没有新的抽取"


# ---------------------------------------------------------------------------
# 7. revision 语义：0 起、每次真写 +1、内容没变跳过
# ---------------------------------------------------------------------------


def test_the_first_write_starts_at_revision_zero() -> None:
    assert revision_for_write(previous=None, current=_run()) == 0


def test_an_unchanged_run_skips_the_write_and_keeps_the_revision() -> None:
    previous = _run(revision=3, coins=22)

    assert revision_for_write(previous=previous, current=_run(revision=3, coins=22)) is None
    # revision 本身不是"内容"：陈旧的内存 revision 不该造出一次假写。
    assert revision_for_write(previous=previous, current=_run(revision=9, coins=22)) is None


def test_a_changed_run_bumps_the_revision_by_one() -> None:
    previous = _run(revision=3)

    assert revision_for_write(previous=previous, current=_run(revision=3, coins=1)) == 4
    assert revision_for_write(previous=previous, current=_run(revision=3, flags=("x",))) == 4


def test_staging_a_save_stamps_the_revision_and_skips_when_nothing_changed() -> None:
    previous = save(_run(revision=3, coins=5))

    assert stage_save(previous, save(_run(revision=3, coins=5))) is None

    staged = stage_save(previous, save(_run(revision=3, coins=6)))
    assert staged is not None
    assert staged.run.revision == 4
    assert staged.run.coins == 6

    first = stage_save(None, save())
    assert first is not None and first.run.revision == 0


def test_entering_a_node_is_a_real_write() -> None:
    """S₀ → S_enter 是一次真写（+1）；进入后 pending 不同 ⇒ 不能靠"内容没变"省掉它。"""
    zero = save(_run(node_status=script_mode.completed(script_mode.SAVE_LAYER_ID, "entry")))

    entered_write = stage_save(zero, save(entered("event")))

    assert entered_write is not None
    assert entered_write.run.revision == 1
    assert entered_write.run.pending is not None


# ---------------------------------------------------------------------------
# 8. 两条补充：坏版本也是 broken（带 reason），快照随档自足
# ---------------------------------------------------------------------------


def test_an_unknown_mode_version_makes_the_whole_save_broken_with_a_reason() -> None:
    """三态里的 ``broken`` 覆盖"版本不认识"这一路（不只是 JSON 坏了）。"""
    text = broken_text(
        f'"script_mode_version": {SCRIPT_MODE_VERSION}', '"script_mode_version": 2'
    )

    view = inspect_save(main=text, backup=None, file=FILE)

    assert view.status is SaveStatus.BROKEN
    assert view.status is not SaveStatus.MISSING
    assert view.reason, "坏档不静默：坏版本也必须给一行原因"
    assert [error.rule_id for error in view.errors] == ["script_save.version.unknown"]
    assert view.run is None


def test_the_snapshot_travels_with_the_archive_not_with_the_library() -> None:
    """快照自足：读档只读档内的快照，库后来改了什么（同名剧本换了名字）都不影响这一轮。"""
    captured = save()
    text = dump_save(captured)
    renamed = script_mode.save_library().model_copy(
        update={
            "scripts": (
                script_mode.save_script().model_copy(update={"name": "库后来改过的名字"}),
            )
        }
    )
    assert renamed.scripts[0].name != captured.script_snapshot.scripts[0].name

    loaded = load_save(text, file=FILE)

    assert loaded.script_snapshot == captured.script_snapshot
    assert loaded.script_snapshot.scripts[0].name != renamed.scripts[0].name
