"""剧本存档：``RunState`` ↔ JSON 文本 + 三段门 + ``.bak`` 恢复语义（M7 票 18）。

依据票 04 §1（文件形状与逐字段表）、§2（写入时机）、§5（坏档与版本）、§8.5（术语）。
本模块是**纯函数侧**：不读写任何文件、不读时钟（``src/sgs/**`` 全域禁
``time/datetime/uuid/threading``）、不持锁。文件 I/O（两次 rename）、端点、会话 id 是票 19。

存档是**纯数据**，顶层恰好五个字段（票 04 §1）：

===========================  ==============================================  ================
字段                          形状                                            谁写 / 谁读
===========================  ==============================================  ================
``script_mode_version``      ``int``（v1 = 1）                              存档 I/O / 读档第一道门
``saved_at``                 ISO-8601 字符串                                 **调用方注入** / 只做展示
``run``                      :class:`~sgs.script.RunState`                   状态机 / 棋盘
``script_snapshot``          **整份** :class:`~sgs.data.ScriptLibrary`       编译 / 结算 / 审计
``value_table``              :class:`~sgs.data.SkillValueTable`              三选一 / 商店 / 悬浮面板
===========================  ==============================================  ================

三条纪律：

1. **不存派生值**：``available_next`` / 入口 / 出口 / Boss 结算顺序 / 遗物效果全部现算（票 04 §0
   纪律 1）；落盘的派生值会变成第二份真相。``saved_at`` 由调用方注入（票 19 的时钟），本模块
   只钉字段与形状；``revision`` 原样往返，只有 :func:`revision_for_write` 这一条**纯判定**。
2. **三段门 = 版本 → 结构 → 语义**，报错形状 ``LoadError(file, loc, expected, got, rule_id)``，
   **全量聚合**（票 04 §5）。语义段的**快照**部分复用票 14 的
   :func:`~sgs.data.validate_script_library`（同一份结构门，不另写一套），本模块只补
   "``run`` 指向的东西在快照里还得在"那几条链。
3. **坏档不静默**：三态是 ``ok`` / ``missing`` / ``broken``；``missing`` 只在**主档与 ``.bak``
   都不在**时出现（唯一允许的静默）。``broken`` 一律带非空 ``reason``，且**不**给出一份"修好的"
   存档——绝不把坏档当空档、绝不静默删、绝不静默丢字段（与将池预设的宽松口径相反）。

``script_snapshot`` 为什么是**整份剧本库**而不是一个 :class:`~sgs.data.Script`：票 03 §7 / 票 04 §1
括号里列的自足内容是"大节 / 节点 / 边 / **房间** / 战斗 / 座位 / **模板** / **技能池** / 事件 /
事件池 / **全局技能**"——房间与共享表住在库这一层，``Script`` 自己只有大节；而编译
（:func:`sgs.script.compile_battle`）与结算（:func:`sgs.script.settle_node`）的入参本来就是
``ScriptLibrary``。故快照 = **含这份剧本的整个库**，带 ``script_schema_version`` 库头，整份过
票 14 的校验器。"整份剧本快照"因此同时满足"run 期间改库不影响本轮"（自足）与"同一份结构门"。

**版本四件套里只有 ``script_mode_version`` 参与拒绝**（票 03 §7）：``script.version`` /
``script_digest`` 只做审计，本模块**不查**它们（查了才是 bug：改库不影响既有存档）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sgs.data import (
    DataLoadError,
    LoadError,
    ScriptLibrary,
    SkillValueTable,
    validate_script_library,
)

from .state import RunState

__all__ = [
    "KNOWN_SCRIPT_MODE_VERSIONS",
    "SCRIPT_MODE_VERSION",
    "SaveStatus",
    "SaveView",
    "ScriptSave",
    "dump_save",
    "inspect_save",
    "load_save",
    "revision_for_write",
    "stage_save",
    "validate_save",
]

SCRIPT_MODE_VERSION: Final = 1
"""认得的 ``script_mode_version``（**存档格式**版本，票 03 §7 的四件套之一）。"""

KNOWN_SCRIPT_MODE_VERSIONS: frozenset[int] = frozenset({SCRIPT_MODE_VERSION})
"""版本门的合法值集合。认不出 ⇒ **拒绝续档、不做迁移**（票 03 §7 已裁）。"""

_MEMORY_FILE: Final = "<memory>"
"""没有文件名时的报错占位（与 ``sgs.data`` 的加载器同口径）。"""


class ScriptSave(BaseModel):
    """一份剧本存档（``runs/script-save.json`` 的形状，票 04 §1）。

    不可变、无多余字段：拼错的键在结构段当场判红（``extra_forbid``），"静默丢字段"不可能。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    script_mode_version: int
    """存档格式版本。**读**的时候由版本门先查（本字段自己不拒，便于构造函数化未来版本）。"""

    saved_at: str = Field(min_length=1)
    """ISO-8601 时间串，**由调用方注入**（``src/sgs/**`` 禁时钟）；任何逻辑都不读它。"""

    run: RunState
    """本轮唯一的可变部分（票 04 §1）。"""

    script_snapshot: ScriptLibrary
    """开局拷进来的**整份**剧本库：run 期间改用户库不影响本轮（票 03 §7）。"""

    value_table: SkillValueTable
    """开局锁定的价值表快照：三选一 / 商店 / 悬浮面板都读它，不读本地预设（票 02 §4）。"""


# ---------------------------------------------------------------------------
# ①② 版本 → 结构
# ---------------------------------------------------------------------------


def _parse_json_object(raw: str | bytes | Mapping[str, object], *, file: str) -> Mapping[str, object]:
    """JSON 文本 → 对象；非法 JSON / 根不是对象 ⇒ 一条可定位的 ``LoadError``。

    与 ``sgs.data.loader`` 的解析门同一判据、同一错误码家族（``structure.invalid_json`` /
    ``structure.root_type``）：存档信封不另立一套 JSON 规矩。**快照**的结构门不在这里——那是
    :func:`~sgs.data.validate_script_library` 的事。
    """
    if isinstance(raw, Mapping):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="合法 JSON",
                    got=f"{exc.msg}（行 {exc.lineno} 列 {exc.colno}）",
                    rule_id="structure.invalid_json",
                )
            ]
        ) from exc
    if not isinstance(parsed, Mapping):
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="JSON 对象",
                    got=type(parsed).__name__,
                    rule_id="structure.root_type",
                )
            ]
        )
    return parsed


def _mode_version_error(file: str, parsed: Mapping[str, object]) -> LoadError | None:
    """版本门（第一道）：未知 / 缺失 ``script_mode_version`` 一律拒绝，**不再报字段错误**。

    顺序与数据层一致（版本先行）：版本错配时该报的是"版本不符"，不是一堆误导性的字段错误。
    **不做迁移**（票 03 §7）：认不出就是认不出，迁移代码不在本仓。
    """
    expected = f"存档格式版本之一：{sorted(KNOWN_SCRIPT_MODE_VERSIONS)}"
    if "script_mode_version" not in parsed:
        return LoadError(
            file=file,
            loc="/script_mode_version",
            expected=expected,
            got="缺失",
            rule_id="script_save.version.missing",
        )
    value = parsed["script_mode_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in KNOWN_SCRIPT_MODE_VERSIONS:
        return LoadError(
            file=file,
            loc="/script_mode_version",
            expected=expected,
            got=repr(value),
            rule_id="script_save.version.unknown",
        )
    return None


_EXPECTED_BY_PYDANTIC_ERROR: dict[str, str] = {
    "missing": "必填字段",
    "extra_forbidden": "无多余字段",
    "enum": "枚举取值之一",
    "literal_error": "字面量取值之一",
    "string_type": "字符串",
    "string_too_short": "非空字符串",
    "int_type": "整数",
    "int_parsing": "可解析为整数的值",
    "bool_type": "布尔值",
    "model_type": "对象",
    "dict_type": "对象",
    "list_type": "数组",
    "tuple_type": "数组",
    "too_short": "非空集合",
}
"""常见 pydantic 错误码 → 期望值的中文（与 ``sgs.data.loader`` 的同一张表形状相同）。

未登记的错误码照实带出（``f"符合模型约束（{type}）"``），不静默归到通用文案。
"""


def _json_pointer(loc: tuple[object, ...]) -> str:
    """pydantic 的 ``loc`` → JSON 指针形状（``/run/backpack/slots/3``）。"""
    if not loc:
        return "/"
    return "/" + "/".join(str(part) for part in loc)


def _structure_errors(file: str, exc: ValidationError) -> list[LoadError]:
    """结构门（第二道）：存档信封的 pydantic 错误 → 可定位清单，**一次报完**。"""
    errors: list[LoadError] = []
    for detail in exc.errors():
        error_type = detail["type"]
        errors.append(
            LoadError(
                file=file,
                loc=_json_pointer(tuple(detail["loc"])),
                expected=_EXPECTED_BY_PYDANTIC_ERROR.get(
                    error_type, f"符合模型约束（{error_type}）"
                ),
                got="缺失" if error_type == "missing" else repr(detail.get("input")),
                rule_id=f"structure.{error_type}",
            )
        )
    return errors


# ---------------------------------------------------------------------------
# ③ 语义：快照整份过票 14 的校验器 + run ↔ 快照的链
# ---------------------------------------------------------------------------


def _snapshot_errors(file: str, snapshot: ScriptLibrary) -> list[LoadError]:
    """快照**整份**复用票 14 的 ``validate_script_library``（同一份结构门，不另写一套）。

    ``loc`` 前面加上 ``/script_snapshot``：报错要指向**存档里的位置**，而不是一个独立的库文件
    ——同一份库进了存档，引用断链的位置仍然是 ``/scripts/0/layers/0/edges/10/to`` 那一个。
    """
    errors = validate_script_library(snapshot.model_dump(mode="json"), file=file)
    return [replace(error, loc=f"/script_snapshot{error.loc}") for error in errors]


def _run_link_errors(file: str, save: ScriptSave) -> list[LoadError]:
    """``run`` 指向的东西必须在快照里还在（版本 / 指纹**不在**此列：它们只做审计）。

    这几条是"存档坏掉"与"剧本库改过"的分界线：改库不影响本轮（快照自足），但一份
    ``run`` 指向快照里不存在的剧本 / 大节 / 节点，就是**断链的坏档**——续档会当场算不出棋盘。
    """
    run = save.run
    library = save.script_snapshot
    script = next((item for item in library.scripts if item.id == run.script_id), None)
    if script is None:
        return [
            LoadError(
                file=file,
                loc="/run/script_id",
                expected=f"快照里的剧本 id 之一：{[item.id for item in library.scripts]}",
                got=repr(run.script_id),
                rule_id="script_save.unknown_script",
            )
        ]
    if not 0 <= run.layer_index < len(script.layers):
        return [
            LoadError(
                file=file,
                loc="/run/layer_index",
                expected=f"剧本 {script.id!r} 的大节下标之一：0..{len(script.layers) - 1}",
                got=repr(run.layer_index),
                rule_id="script_save.layer_out_of_range",
            )
        ]

    errors: list[LoadError] = []
    layer = script.layers[run.layer_index]
    node_ids = {node.id for node in layer.nodes}
    if run.pending is not None:
        layer_id, _, node_id = run.pending.node_id.partition("/")
        if layer_id != layer.id or node_id not in node_ids:
            errors.append(
                LoadError(
                    file=file,
                    loc="/run/pending/node_id",
                    expected=f"当前大节里的进度键（形如 {layer.id}/<node_id>）",
                    got=repr(run.pending.node_id),
                    rule_id="script_save.unknown_pending_node",
                )
            )
    for progress in run.node_status:
        layer_id, _, node_id = progress.partition("/")
        target = next((item for item in script.layers if item.id == layer_id), None)
        if target is None or node_id not in {node.id for node in target.nodes}:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/run/node_status/{progress}",
                    expected="指向快照里真实存在的节点（形如 <layer_id>/<node_id>）",
                    got=repr(progress),
                    rule_id="script_save.unknown_node_status",
                )
            )
    return errors


def _semantic_errors(file: str, save: ScriptSave) -> list[LoadError]:
    """语义门（第三道）：快照整份 + ``run`` 的链，全量聚合成一份清单。"""
    return [*_snapshot_errors(file, save.script_snapshot), *_run_link_errors(file, save)]


# ---------------------------------------------------------------------------
# 读写入口（纯函数；文件 I/O 在票 19）
# ---------------------------------------------------------------------------


def dump_save(save: ScriptSave) -> str:
    """存档 → JSON 文本（**规范化**：键序排序、两空格缩进、UTF-8 原文、末尾换行）。

    规范化让"同一份存档两次 dump 逐字节相同"成立（幂等写与人工排查都靠它）；内容比较仍以
    :class:`~sgs.script.RunState` 逐字段相等为准（票 04 §2 的"跳过写"），不拿文本比。
    """
    return (
        json.dumps(save.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def load_save(
    raw: str | bytes | Mapping[str, object], *, file: str = _MEMORY_FILE
) -> ScriptSave:
    """读一份存档：**版本 → 结构 → 语义**三段门，任一不过抛 :class:`DataLoadError`（聚合全部）。

    :raises DataLoadError: 非法 JSON / 未知 ``script_mode_version`` / 字段形状不符 / 快照断链 /
        ``run`` 指向快照里不存在的东西。
    """
    parsed = _parse_json_object(raw, file=file)
    version_error = _mode_version_error(file, parsed)
    if version_error is not None:
        raise DataLoadError([version_error])
    try:
        save = ScriptSave.model_validate(parsed)
    except ValidationError as exc:
        raise DataLoadError(_structure_errors(file, exc)) from exc
    semantic_errors = _semantic_errors(file, save)
    if semantic_errors:
        raise DataLoadError(semantic_errors)
    return save


def validate_save(
    raw: str | bytes | Mapping[str, object], *, file: str = _MEMORY_FILE
) -> tuple[LoadError, ...]:
    """校验一份存档，**返回**全部 ``LoadError``（空 = 合法）；与读档同一份判据。

    从不因为数据坏而抛（只有调用方传了既不是文本也不是映射的东西才会 ``TypeError``）：
    "拒绝"是调用方的策略（票 04 §5 的三态），与 ``validate_script_library`` 同姿态。
    """
    try:
        load_save(raw, file=file)
    except DataLoadError as exc:
        return exc.errors
    return ()


# ---------------------------------------------------------------------------
# 三态判定：ok / missing / broken（票 04 §5）
# ---------------------------------------------------------------------------


class SaveStatus(str, Enum):
    """``GET /api/script-save`` 的三态（票 04 §5 / §7）。"""

    OK = "ok"
    """主档在且过了三段门：可续档。"""

    MISSING = "missing"
    """主档与 ``.bak`` **都不在**——唯一允许的静默（「继续剧本」置灰而已）。"""

    BROKEN = "broken"
    """其余一切，含「主档缺失 + ``.bak`` 在」：**不静默**，必带非空 ``reason``。"""


@dataclass(frozen=True, slots=True)
class SaveView:
    """一份存档的**判定结果**（票 04 §5 的三态 + 摘要 + ``.bak`` 的可恢复性）。

    这是纯函数对"主档 / 备份这两坨字节现在是什么状态"的**唯一**答案；文件层面的事（写回、
    删档、409）由票 19 按它处置。派生字段一律走 property，不存第二份真相。
    """

    status: SaveStatus
    save: ScriptSave | None = None
    """主档过了三段门时的那一份（``ok`` 才有）；``broken`` 时**恒 None**——不给出"修好的"内容。"""

    backup: ScriptSave | None = None
    """``.bak`` 过了三段门时的那一份：**恢复的输入内容**（写回主档时用 ``.bak`` 的原字节）。"""

    errors: tuple[LoadError, ...] = ()
    """主档的全部加载错误（聚合；``ok`` / ``missing`` 时为空）。"""

    bak_errors: tuple[LoadError, ...] = ()
    """``.bak`` 的全部加载错误（备份坏了 / 没有时为空元组或非空）。"""

    reason: str = ""
    """给人看的一行原因（``broken`` 时**非空**；``ok`` / ``missing`` 时为空串）。"""

    @property
    def run(self) -> RunState | None:
        """``ok`` 时本轮的状态（续档的入口）；其余为 ``None``。"""
        return self.save.run if self.save is not None else None

    @property
    def saved_at(self) -> str | None:
        """主档的 ``saved_at``（开局页摘要用；``ok`` 之外为 ``None``）。"""
        return self.save.saved_at if self.save is not None else None

    @property
    def bak_available(self) -> bool:
        """能不能「从我上一份恢复」= ``.bak`` **存在且过了三段门**。

        备份在但坏了 ⇒ ``False``（坏备份不是退路；票 04 §5「``.bak`` 也坏 / 没有 ⇒ 只剩放弃存档」）。
        """
        return self.backup is not None

    @property
    def bak_saved_at(self) -> str | None:
        """备份的 ``saved_at``（恢复面要显示"上一份是几点"）。"""
        return self.backup.saved_at if self.backup is not None else None


def _reason(errors: Iterable[LoadError]) -> str:
    """把聚合的错误压成**一行**给人看的原因（带 ``rule_id``，便于对日志与报 issue）。"""
    return "；".join(
        f"{error.loc}：{error.expected}（实得 {error.got}，{error.rule_id}）"
        for error in errors
    )


def _read(
    raw: str | bytes | Mapping[str, object], *, file: str
) -> tuple[ScriptSave | None, tuple[LoadError, ...]]:
    """读一坨字节：成功 ⇒ ``(存档, ())``；失败 ⇒ ``(None, 全部错误)``（**不抛**）。"""
    try:
        return load_save(raw, file=file), ()
    except DataLoadError as exc:
        return None, exc.errors


def inspect_save(
    *,
    main: str | bytes | Mapping[str, object] | None,
    backup: str | bytes | Mapping[str, object] | None,
    file: str = _MEMORY_FILE,
    backup_file: str | None = None,
) -> SaveView:
    """判定主档 / 备份现在是哪一态（``ok`` / ``missing`` / ``broken``）+ 能否恢复。

    纯函数：只**读**传进来的两坨字节，**不**写、不删、不修（两次 rename 与删档是票 19）。

    判定表（票 04 §5）：

    ==================  ==========  ===========================================
    主档                备份        结果
    ==================  ==========  ===========================================
    不在                不在        ``missing``（唯一允许的静默）
    不在                在（合法）  ``broken`` + ``bak_available`` + 可恢复
    不在                在（坏了）  ``broken`` + ``bak_available=False``
    在（合法）          任意        ``ok``（备份好坏只决定"能不能回滚"）
    在（坏了）          在（合法）  ``broken`` + ``bak_available``
    在（坏了）          不在 / 坏了 ``broken`` + ``bak_available=False``
    ==================  ==========  ===========================================

    ``broken`` 时**不给** ``save``：本模块绝不把坏档当空档、也绝不静默丢字段——"恢复"是调用方
    拿 :attr:`SaveView.backup`（或原字节）显式选择的动作（票 04 §5 的「从我上一份恢复」）。

    :param main: 主档字节（或已解析的映射）；``None`` = 文件不存在。
    :param backup: ``.bak`` 的字节；``None`` = 备份不存在。
    :param file: 主档在报错里的名字。
    :param backup_file: 备份在报错里的名字；缺省 = ``f"{file}.bak"``。
    """
    if backup_file is None:
        backup_file = f"{file}.bak"
    if main is None and backup is None:
        return SaveView(status=SaveStatus.MISSING)
    bak, bak_errors = (
        (None, ()) if backup is None else _read(backup, file=backup_file)
    )
    if main is None:
        if bak is not None:
            return SaveView(
                status=SaveStatus.BROKEN,
                backup=bak,
                reason="主档缺失、备份在：上次保存被中断（可用「从我上一份恢复」）",
            )
        return SaveView(
            status=SaveStatus.BROKEN,
            bak_errors=bak_errors,
            reason="主档缺失，且备份也不可用：" + _reason(bak_errors),
        )
    save, errors = _read(main, file=file)
    if save is not None:
        return SaveView(
            status=SaveStatus.OK, save=save, backup=bak, bak_errors=bak_errors
        )
    reason = _reason(errors)
    if bak is None:
        reason += "；备份也不可用"
    return SaveView(
        status=SaveStatus.BROKEN,
        backup=bak,
        errors=errors,
        bak_errors=bak_errors,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# revision 语义：0 起、每次真写 +1、内容没变跳过（票 04 §2）
# ---------------------------------------------------------------------------


def _content(run: RunState) -> dict[str, object]:
    """``RunState`` 的**内容**（除 ``revision`` 外的全部字段）：比较"变没变"只看它。"""
    return run.model_dump(exclude={"revision"})


def revision_for_write(*, previous: RunState | None, current: RunState) -> int | None:
    """这次写该用的 ``revision``；``None`` = **不该写**（内容没变、也不 +1）。

    - 没有上一份（还没有档）：第一次写从 **0** 起（"``revision`` 0 起"，票 04 §1）；
    - 内容（除 ``revision`` 外逐字段）相等 ⇒ ``None``：跳过写、**不 +1**（``PUT …/backpack``
      的幂等与 ``suspend`` 的"不写"都靠这条，票 04 §2）；
    - 内容变了 ⇒ ``previous.revision + 1``（"每次真写 +1"）。

    比较**不含** ``revision`` 自身：手上那份状态的 ``revision`` 可能是旧值（还没写回），
    拿它当内容会造出一次假写。
    """
    if previous is None:
        return 0
    if _content(previous) == _content(current):
        return None
    return previous.revision + 1


def stage_save(previous: ScriptSave | None, candidate: ScriptSave) -> ScriptSave | None:
    """要落盘的那一份：``revision`` 已按 :func:`revision_for_write` 定好；``None`` = 跳过写。

    ``candidate`` 的其余字段（``saved_at`` / 快照 / 价值表）原样带走——时间与内容是调用方的
    事实，本模块只钉 ``revision`` 这一条算术。
    """
    revision = revision_for_write(
        previous=None if previous is None else previous.run, current=candidate.run
    )
    if revision is None:
        return None
    return candidate.model_copy(
        update={"run": candidate.run.model_copy(update={"revision": revision})}
    )
