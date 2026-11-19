"""剧本模式的**接入层**：存档文件 I/O + 端点 + 战斗会话 + 进程内锁（M7 票 19）。

依据 **[票 04 §2、§3、§4、§6、§7、§8]** 与票 19 的验收。规则层一行不写在这里：
``RunState`` 状态机 / 棋盘 / 编译 / 结算 / 存档三段门全在 :mod:`sgs.script`（票 16–18），
本模块只做「读文件 → 调纯函数 → 写文件 / 起会话」，HTTP 路由在 :mod:`sgs_web.serve`。

**存档文件**（``runs/`` 下，与录像的 ``*.meta.json`` 不相交）：

- ``script-save.json``：主档；``.bak``：上一份；``.tmp``：写入中间名；
- 落盘纪律（票 04 §2）：先写 ``.tmp`` → ``os.replace(main, main.bak)`` → ``os.replace(tmp, main)``
  ——**两次 rename**。进程在两次 rename 之间被杀时主档缺失而 ``.bak`` 在，读档把这一格判成
  **可恢复的中断**（``broken`` + 「从我上一份恢复」），不判无存档。
- ``saved_at`` 由这里注入（``src/sgs/**`` 全域禁时钟）；``revision`` 的算术在
  :func:`sgs.script.stage_save`（内容没变 ⇒ 跳过写、不 +1）。

**三个进程级前提**（写进实现期文档归票 30）：

1. **同一 ``runs/`` 只许一个 ``serve.py``**——跨进程不做锁，只靠 ``revision`` 409 兜底；
   本仓既有隐含前提（``serve._SAVED`` 也是进程级）。
2. **一把进程内锁**串行化「读档 → 改 → 写」与「推进 / 作废会话」：``ThreadingHTTPServer``
   下两条请求可能交错，而存档是整份读写的。
3. **会话 id 不入档**：``s1/s2`` 是每个 ``GameRegistry`` 各自编号、进程重启从 ``s1`` 重来，
   故档内的身份是 ``run_id``（+ ``revision``）；会话 id 只是内存句柄。同一 run 同时**最多
   一个**战斗会话：新开 / 继续 / 再次进入战斗前，先按 ``run_id`` 作废旧会话（票 04 §4）。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final, NoReturn

from sgs.data import (
    EnemyDraw,
    Event,
    Mode,
    Requirement,
    Room,
    RoomKind,
    RuleConfig,
    Script,
    ScriptLibrary,
    SkillValueTable,
    load_builtin_script_library,
    load_script_library,
    load_skill_value_presets,
    load_skill_values,
    merge_skill_values,
    script_digest,
)
from sgs.engine import new_seed, solve_pve_outcome
from sgs.script import (
    SCRIPT_MODE_VERSION,
    Backpack,
    BattleSpec,
    NodeStatus,
    RunState,
    SaveStatus,
    SaveView,
    ScriptSave,
    ScriptError,
    SettleOrder,
    available_next,
    current_node,
    dump_save,
    enter_node,
    inspect_save,
    layer_of,
    progress_key,
    settle_node,
    stage_save,
)

from .session import Session

__all__ = [
    "SCRIPT_LIBRARY_FILENAME",
    "SCRIPT_SAVE_FILENAME",
    "SKILL_VALUE_PRESETS_FILENAME",
    "SaveConflict",
    "ScriptHttpError",
    "ScriptRuns",
    "script_not_found",
]

SCRIPT_SAVE_FILENAME: Final = "script-save.json"
"""剧本存档的主档名（票 04 §2）；``.bak`` / ``.tmp`` 由它派生。"""

SCRIPT_LIBRARY_FILENAME: Final = "script-library.json"
"""用户剧本库的文件名（票 03 §7 / 票 24）。文件不在 ⇒ 用内置库（本票只读它）。"""

SKILL_VALUE_PRESETS_FILENAME: Final = "skill-value-presets.json"
"""价值表本地预设的文件名（票 15 交出来的名字；锁表口径由本模块执行）。"""

_ACTIVE_VALUE_SLOT: Final = 0
"""生效的价值表预设槽位（票 15 的 ``SkillValuePresets`` 没有 ``active`` 字段）。

**明说读法**：5 个槽位里只把第 0 槽当成「当前预设」（票 20 / 22 若加槽位选择面，改这一格
即可；存档里冻结的是**合并后的整张表**，所以放宽这一格不影响既有存档）。"""


# ---------------------------------------------------------------------------
# 错误：HTTP 层的三种语义（400 / 404 / 409）在这里生成，路由只负责发出去
# ---------------------------------------------------------------------------


class ScriptHttpError(Exception):
    """带状态码与**返回体**的接入层错误（路由捕获后原样下发）。

    三种用法对应票 04 §7 的 400 / 404 / 409：结构非法与规则拒绝是 400、寻址不到是 404、
    存档不可用或 ``revision`` 不匹配是 409（409 的体里带当前 ``revision`` 与摘要）。
    """

    def __init__(self, status: int, payload: dict[str, Any], message: str = "") -> None:
        self.status = status
        self.payload = payload
        super().__init__(message or str(payload.get("error", "")))


def script_not_found(message: str) -> ScriptHttpError:
    """404：没有存档 / 没有这一轮 / 没有这个会话。"""
    return ScriptHttpError(404, {"error": message}, message)


class SaveConflict(Exception):
    """409：``expected_revision`` 对不上 / 存档现在读不动（体里带当前 ``revision`` 与摘要）。"""

    def __init__(self, payload: dict[str, Any], message: str = "") -> None:
        self.payload = payload
        super().__init__(message or str(payload.get("error") or "存档状态已变化"))


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _now() -> str:
    """注入存档的 ``saved_at``（**时钟只住这一层**：``src/sgs/**`` 禁时钟）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dump_requirement(requirement: Requirement) -> dict[str, Any]:
    return {
        "flags": list(requirement.flags),
        "not_flags": list(requirement.not_flags),
    }


def _int_field(body: Mapping[str, Any], name: str) -> int:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数：{value!r}")
    return value


def _expected_revision(body: Mapping[str, Any]) -> int:
    """写端点的 ``expected_revision``（必填：缺它 = 结构非法，400 而不是 409）。"""
    if "expected_revision" not in body:
        raise ValueError("写端点必须带 expected_revision（票 04 §7）")
    return _int_field(body, "expected_revision")


def _optional_revision(body: Mapping[str, Any]) -> int | None:
    if "expected_revision" not in body or body["expected_revision"] is None:
        return None
    return _int_field(body, "expected_revision")


def _id_list(body: Mapping[str, Any], name: str) -> tuple[str | None, ...]:
    raw = body.get(name, ())
    if raw is None:
        return ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ValueError(f"{name} 必须是数组：{raw!r}")
    out: list[str | None] = []
    for item in raw:
        if item is not None and (not isinstance(item, str) or not item):
            raise ValueError(f"{name} 的元素必须是非空技能 id 或 null：{item!r}")
        out.append(item)
    return tuple(out)


def _purchases(body: Mapping[str, Any]) -> tuple[str, ...]:
    """商店购买清单：技能 / 遗物 id 的数组（``null`` 不是一件商品）。"""
    items = _id_list(body, "purchases")
    if any(item is None for item in items):
        raise ValueError(f"purchases 的元素必须是非空技能 id：{body.get('purchases')!r}")
    return tuple(item for item in items if item is not None)


def _int_list(body: Mapping[str, Any], name: str) -> tuple[int, ...] | None:
    raw = body.get(name)
    if raw is None:
        return None
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ValueError(f"{name} 必须是数组：{raw!r}")
    out: list[int] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"{name} 的元素必须是整数：{item!r}")
        out.append(item)
    return tuple(out)


def _unique_or_refuse(ids: Sequence[str | None], *, label: str) -> None:
    seen = {item for item in ids if item is not None}
    if len(seen) != sum(1 for item in ids if item is not None):
        raise ValueError(f"{label}里有重复的技能 id：同一技能不可重复持有（票 02 §3）")


# ---------------------------------------------------------------------------
# 剧本模式接入层
# ---------------------------------------------------------------------------


class ScriptRuns:
    """``runs/`` 这一份剧本存档的全部读写与端点语义。

    ``sessions`` 是 :class:`sgs_web.serve.GameRegistry`（鸭子类型：``sessions`` 表 +
    ``create_battle_session``）——战斗会话与录像的落盘规则复用既有那一条路，不另建注册表。
    """

    def __init__(self, *, runs_dir: Path, sessions: Any) -> None:
        self.runs_dir = Path(runs_dir)
        self._sessions = sessions
        # 一把锁串行化「读档 → 改 → 写」与「推进 / 作废会话」（票 04 §4：跨进程不做锁）。
        self._lock = threading.RLock()
        # run_id → session_id：**内存句柄**，绝不进档（票 04 §4）。
        self._battles: dict[str, str] = {}

    # -- 路径 ---------------------------------------------------------------

    @property
    def save_path(self) -> Path:
        return self.runs_dir / SCRIPT_SAVE_FILENAME

    @property
    def backup_path(self) -> Path:
        return self.save_path.with_name(f"{self.save_path.name}.bak")

    @property
    def tmp_path(self) -> Path:
        return self.save_path.with_name(f"{self.save_path.name}.tmp")

    @property
    def library_path(self) -> Path:
        return self.runs_dir / SCRIPT_LIBRARY_FILENAME

    @property
    def presets_path(self) -> Path:
        return self.runs_dir / SKILL_VALUE_PRESETS_FILENAME

    # -- 文件读写（两次 rename） --------------------------------------------

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def save_exists(self) -> bool:
        """锁表的唯一判据：**只看文件存在**（含只剩 ``.bak`` 的恢复态、含坏档，票 04 §6）。"""
        return self.save_path.exists() or self.backup_path.exists()

    def save_view(self) -> SaveView:
        """三态判定（纯函数 :func:`sgs.script.inspect_save` 读两坨字节）。"""
        return inspect_save(
            main=self._read_bytes(self.save_path),
            backup=self._read_bytes(self.backup_path),
            file=str(self.save_path),
            backup_file=str(self.backup_path),
        )

    def _write_save(self, save: ScriptSave) -> None:
        """整份落盘：``.tmp`` → 轮换 ``.bak`` → 主档（**两次 rename**，票 04 §2）。"""
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path.write_text(dump_save(save), encoding="utf-8")
        if self.save_path.exists():
            os.replace(self.save_path, self.backup_path)
        os.replace(self.tmp_path, self.save_path)

    def _restore_bytes(self, data: bytes) -> None:
        """把一坨**原字节**写回主档（「从我上一份恢复」：不改 ``revision``、不轮换备份）。"""
        self.save_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_path.write_bytes(data)
        os.replace(self.tmp_path, self.save_path)

    def _delete_files(self) -> None:
        """删档三路共用的落点：主档 + ``.bak``（``.tmp`` 顺手清掉，它从来不是存档）。"""
        for path in (self.save_path, self.backup_path, self.tmp_path):
            path.unlink(missing_ok=True)

    # -- 摘要 / 三态返回体 ---------------------------------------------------

    @staticmethod
    def _summary(save: ScriptSave) -> dict[str, Any]:
        """开局页摘要行的那几个数（票 04 §6；展示归票 20，数据在这里）。"""
        run = save.run
        script = next(
            (item for item in save.script_snapshot.scripts if item.id == run.script_id),
            None,
        )
        return {
            "script_id": run.script_id,
            "script_name": script.name if script is not None else run.script_id,
            "layer_index": run.layer_index,
            "layer_count": len(script.layers) if script is not None else 0,
            "settled_nodes": len(run.node_status),
            "coins": run.coins,
        }

    def save_payload(self) -> dict[str, Any]:
        """``GET /api/script-save``：三态 + 摘要 + ``.bak`` 可恢复性 + 锁表。"""
        return self._view_payload(self.save_view())

    def _view_payload(self, view: SaveView) -> dict[str, Any]:
        save = view.save
        return {
            "status": view.status.value,
            "run_id": None if save is None else save.run.run_id,
            "revision": None if save is None else save.run.revision,
            "saved_at": view.saved_at,
            "summary": None if save is None else self._summary(save),
            "bak_available": view.bak_available,
            "bak_saved_at": view.bak_saved_at,
            "reason": view.reason,
            "value_table_locked": self.save_exists(),
        }

    def _conflict_payload(self, *, reason: str = "") -> dict[str, Any]:
        """409 的体：当前 ``revision`` + 摘要（票 04 §4），坏档时再用 ``reason`` 说原因。"""
        payload = self.save_payload()
        if reason:
            payload["error"] = reason
        else:
            payload["error"] = "revision 已过期：请按当前 revision 重取棋盘"
        return payload

    def _refuse_unusable(self, view: SaveView) -> NoReturn:
        raise SaveConflict(
            {**self._view_payload(view), "error": f"存档不可用：{view.reason}"}
        )

    def _require_save(self) -> tuple[SaveView, ScriptSave]:
        """读当前存档；``missing`` ⇒ 404、``broken`` ⇒ 409（绝不把坏档当空档）。"""
        view = self.save_view()
        if view.status is SaveStatus.MISSING:
            raise script_not_found("没有剧本存档")
        if view.status is SaveStatus.BROKEN or view.save is None:
            self._refuse_unusable(view)
        return view, view.save

    def _require_run(self, run_id: str) -> tuple[SaveView, ScriptSave]:
        view, save = self._require_save()
        if save.run.run_id != run_id:
            raise script_not_found(f"没有这一轮：{run_id}")
        return view, save

    def _require_revision(self, save: ScriptSave, expected: int) -> None:
        if expected != save.run.revision:
            raise SaveConflict(self._conflict_payload())

    def _checked_revision(self, save: ScriptSave, body: Mapping[str, Any]) -> None:
        self._require_revision(save, _expected_revision(body))

    # -- 价值表（锁表 / 生效表） --------------------------------------------

    def value_table(self) -> SkillValueTable:
        """本地生效表 = 随包默认表 + 当前本地预设（第 0 槽；见模块常量）。"""
        base = load_skill_values()
        presets = load_skill_value_presets(self.presets_path, base=base)
        return merge_skill_values(base, presets.slots[_ACTIVE_VALUE_SLOT])

    # -- 剧本库 -------------------------------------------------------------

    def library(self) -> ScriptLibrary:
        """运行期库：用户库（``runs/script-library.json``）优先，没有就用内置库。

        坏库 ⇒ :class:`~sgs.data.DataLoadError`（路由映射 400）：运行期严格档（票 14 的
        ``load_script_library``），"允许保存坏库"只属于编辑器路径（票 24）。
        """
        if not self.library_path.exists():
            return load_builtin_script_library()
        return load_script_library(
            self.library_path.read_bytes(), file=str(self.library_path)
        )

    def _script(self, library: ScriptLibrary, script_id: object) -> Script:
        if not isinstance(script_id, str) or not script_id:
            raise ValueError(f"script_id 必须是非空字符串：{script_id!r}")
        script = next((item for item in library.scripts if item.id == script_id), None)
        if script is None:
            raise ValueError(
                f"剧本库里没有 {script_id!r}（现有：{[item.id for item in library.scripts]}）"
            )
        return script

    # -- 战斗会话 -----------------------------------------------------------

    def _kill_battle(self, run_id: str) -> bool:
        """按 ``run_id`` 作废该 run 的战斗会话（僵尸清扫 / ``suspend`` 的共同落点）。

        "作废" = 标 ``abandoned`` + 从 ``GameRegistry.sessions`` 移除 ⇒ 后续取 404、
        ``advance_clock`` 再也碰不到它、``save_if_finished`` 对它零动作（票 04 §4）。
        """
        session_id = self._battles.pop(run_id, None)
        if session_id is None:
            return False
        session = self._sessions.sessions.pop(session_id, None)
        if session is not None:
            session.abandoned = True
        return True

    def _kill_all_battles(self) -> None:
        """作废**全部**已跟踪的战斗会话（换档 / 删档时用：一份存档只有一轮）。"""
        for run_id in list(self._battles):
            self._kill_battle(run_id)

    @staticmethod
    def _battle_spec(run: RunState) -> BattleSpec | None:
        """``pending.spec`` 是不是一场战斗（问号房揭晓成战斗也算）。"""
        if run.pending is None:
            return None
        spec = run.pending.spec
        if isinstance(spec, BattleSpec):
            return spec
        inner = getattr(spec, "revealed", None)
        if inner is not None and isinstance(inner.spec, BattleSpec):
            return inner.spec
        return None

    def _start_battle(self, run: RunState, spec: BattleSpec) -> str:
        """用档内冻结的 ``PveConfig`` + ``battle_seed`` 起一局（重打该场 = 同一场）。

        写 S_enter **之后**才走到这里：写失败 ⇒ 400 且不起会话，不留「档里没有、内存里有」
        的半个节点（票 04 §3）。
        """
        rule_config = RuleConfig(
            player_count=len(spec.pve.identities),
            mode=Mode.PVE,
            pve=spec.pve,
        )
        session = self._sessions.create_battle_session(
            rule_config=rule_config, seed=spec.battle_seed
        )
        self._battles[run.run_id] = session.session_id
        return session.session_id

    # -- 棋盘视图 -----------------------------------------------------------

    def _board(
        self,
        view: SaveView,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """棋盘 / 继续 / 背包三条路共用的视图（票 04 §3：继续 = 读档回棋盘，不重建引擎）。"""
        save = view.save
        assert save is not None, "棋盘只从 ok 存档里画"
        run = save.run
        library = save.script_snapshot
        script = next(item for item in library.scripts if item.id == run.script_id)
        layer = script.layers[run.layer_index]

        board_error: dict[str, str] | None = None
        available: tuple[Any, ...] = ()
        if run.pending is None:
            try:
                available = available_next(
                    layer, flags=run.flags, node_status=run.node_status
                )
            except ScriptError as error:  # 死路 / 大节已打通：棋盘上要能看见原因
                board_error = {
                    "rule_id": error.rule_id,
                    "loc": error.loc,
                    "reason": error.reason,
                }
        available_ids = [node.id for node in available]
        if run.pending is not None:
            current_id = run.pending.node_id.partition("/")[2]
        else:
            node = current_node(
                layer, node_status=run.node_status, pending=run.pending
            )
            current_id = None if node is None else node.id

        statuses = run.node_status
        nodes = [
            {
                "id": node.id,
                "row": node.row,
                "col": node.col,
                "status": self._status_of(statuses, progress_key(layer.id, node.id)),
                "current": node.id == current_id,
                "available": node.id in available_ids,
                "room": self._room(library, node.room),
            }
            for node in layer.nodes
        ]
        payload: dict[str, Any] = {
            "run_id": run.run_id,
            "revision": run.revision,
            "script": {
                "id": script.id,
                "name": script.name,
                "version": script.version,
                "digest": script_digest(script),
            },
            "layer": {
                "id": layer.id,
                "name": layer.name,
                "index": run.layer_index,
                "count": len(script.layers),
                "rows": layer.grid.rows,
                "cols": layer.grid.cols,
            },
            "nodes": nodes,
            "edges": [
                {
                    "from": edge.from_,
                    "to": edge.to,
                    "requires": _dump_requirement(edge.requires),
                }
                for edge in layer.edges
            ],
            "available_next": available_ids,
            "board_error": board_error,
            "pending": (
                None
                if run.pending is None
                else {
                    "node_id": run.pending.node_id,
                    "spec": run.pending.spec.model_dump(mode="json"),
                }
            ),
            "run": {
                "seed": run.seed,
                "chosen_hero": run.chosen_hero,
                "roster_candidates": list(run.roster_candidates),
                "layer_index": run.layer_index,
                "flags": list(run.flags),
                "coins": run.coins,
                "backpack": run.backpack.model_dump(mode="json"),
                "relics": list(run.relics),
                "node_status": {
                    key: value.value if isinstance(value, NodeStatus) else value
                    for key, value in run.node_status.items()
                },
            },
            "save": self._view_payload(view),
            "value_table_locked": self.save_exists(),
            "session_id": session_id,
        }
        return payload

    @staticmethod
    def _status_of(statuses: Mapping[str, Any], key: str) -> str | None:
        value = statuses.get(key)
        return None if value is None else str(getattr(value, "value", value))

    # -- 房间（悬停卡的进料：够画座位布局与敌人信息，票 21） -----------------

    def _room(self, library: ScriptLibrary, room_id: str) -> dict[str, Any]:
        room = next((item for item in library.rooms if item.id == room_id), None)
        if room is None:  # 存档语义门已经拒绝断链，这里只剩调用方传错 id
            raise script_not_found(f"房间里没有 {room_id!r}")
        payload: dict[str, Any] = {
            "id": room.id,
            "name": room.name,
            "kind": room.kind.value,
            "is_boss": room.is_boss,
            "text": room.text,
            "battle": None,
            "event_pool": room.event_pool,
            "events": [],
            "pool": [],
            "on_clear": {"set_flags": list(room.on_clear.set_flags)},
        }
        if room.battle is not None:
            payload["battle"] = self._battle(library, room)
        if room.kind is RoomKind.EVENT or room.event_pool is not None:
            pool = next(
                (item for item in library.event_pools if item.id == room.event_pool),
                None,
            )
            payload["events"] = [
                self._event(event)
                for event_id in (() if pool is None else pool.events)
                for event in library.events
                if event.id == event_id
            ]
        if room.kind is RoomKind.UNKNOWN:
            inner = []
            for entry in room.pool or ():
                revealed = self._room(library, entry.room)
                inner.append(
                    {
                        "room": entry.room,
                        "weight": entry.weight,
                        "kind": revealed["kind"],
                        "name": revealed["name"],
                    }
                )
            payload["pool"] = inner
        return payload

    def _battle(self, library: ScriptLibrary, room: Room) -> dict[str, Any]:
        battle = room.battle
        assert battle is not None
        seats = [
            {
                "template": seat.template,
                "protected": seat.protected,
                "boss": seat.boss,
            }
            for seat in battle.allies
        ]
        if isinstance(battle.enemies, EnemyDraw):
            draw = battle.enemies.draw
            enemies: dict[str, Any] = {
                "kind": "draw",
                "band": draw.band,
                "count": list(draw.count),
            }
            templates = [item for item in library.templates if item.band == draw.band]
        else:
            enemies = {
                "kind": "fixed",
                "seats": [
                    {
                        "template": seat.template,
                        "protected": seat.protected,
                        "boss": seat.boss,
                    }
                    for seat in battle.enemies
                ],
            }
            wanted = [
                seat.template for seat in battle.enemies if seat.template is not None
            ]
            templates = [
                next(item for item in library.templates if item.id == template_id)
                for template_id in wanted
            ]
        return {
            "allies": seats,
            "enemies": enemies,
            "victory": battle.victory.model_dump(mode="json"),
            "global_skills": [
                reference.model_dump(mode="json") for reference in battle.global_skills
            ],
            "extra_reward": battle.extra_reward.model_dump(mode="json"),
            "templates": [
                {
                    "id": template.id,
                    "name": template.name,
                    "band": template.band,
                    "hp": template.hp,
                    "hp_max": template.hp_max,
                    "initial_skills": list(template.initial_skills),
                    "reward": template.reward.model_dump(mode="json"),
                }
                for template in templates
            ],
        }

    @staticmethod
    def _event(event: Event) -> dict[str, Any]:
        return {
            "id": event.id,
            "name": event.name,
            "text": event.text,
            "options": [
                {
                    "text": option.text,
                    "requires": _dump_requirement(option.requires),
                    "effects": [
                        effect.model_dump(mode="json") for effect in option.effects
                    ],
                }
                for option in event.options
            ],
        }

    # ======================================================================
    # 端点
    # ======================================================================

    # -- 开局页（票 04 §6） -------------------------------------------------

    def roster(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/script-roster``：剧本清单 + 每份的 3 名初始角色候选（**不落盘**）。

        "抽 3 候选"是**作者编排的数据**（``Script.roster`` 恰好 3 名、库校验器钉死，票 14）：
        这就是 ``script.roster`` 子流在本轮的全部输入，故本端点不消耗随机数、也不写档——
        3 选 1 期间关页面 = 没建过（票 04 §2 的时机 1）。
        """
        library = self.library()
        scripts = [
            {
                "id": script.id,
                "name": script.name,
                "version": script.version,
                "roster_candidates": list(script.roster),
            }
            for script in library.scripts
        ]
        script_id = body.get("script_id")
        selected: dict[str, Any] | None = None
        if script_id is not None:
            script = self._script(library, script_id)
            selected = next(item for item in scripts if item["id"] == script.id)
        return {
            "script_id": None if selected is None else selected["id"],
            "roster_candidates": [] if selected is None else selected["roster_candidates"],
            "scripts": scripts,
        }

    def create_run(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST /api/script-runs``：建档（写 S₀）——第一次可续的点（票 04 §2 时机 1）。

        已有存档时**必须**带当前 ``expected_revision``：这就是「新剧本撞旧档」的确认
        （前端三选一里的「覆盖新开」，票 04 §6）；不带 ⇒ 409 + 当前 revision 与摘要。
        """
        with self._lock:
            library = self.library()
            script_id = body.get("script_id")
            chosen_hero = body.get("chosen_hero")
            if not isinstance(chosen_hero, str) or not chosen_hero:
                raise ValueError(f"chosen_hero 必须是非空武将 id：{chosen_hero!r}")
            script = self._script(library, script_id)
            if chosen_hero not in script.roster:
                raise ValueError(
                    f"{chosen_hero!r} 不在剧本 {script.id!r} 的初始角色候选里"
                    f"（{[*script.roster]}）：3 选 1 只能选抽出来的那几名"
                )
            seed = body.get("seed", None)
            if seed is None:
                seed = new_seed()
            if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 1 << 64:
                raise ValueError(f"seed 必须是 [0, 2**64) 的整数：{seed!r}")

            view = self.save_view()
            if view.status is SaveStatus.BROKEN:
                self._refuse_unusable(view)
            previous = view.save
            if previous is not None:
                # 「新剧本撞旧档」：**缺 expected_revision 也当冲突**（不是 400）——操作层
                # 的含义是"已经有一轮了，确认要不要覆盖"（票 04 §6 的三选一），409 的体
                # 正好给出当前 revision 与摘要，前端据此弹确认框。
                expected = _optional_revision(body)
                if expected is None or expected != previous.run.revision:
                    raise SaveConflict(self._conflict_payload())

            run = RunState(
                run_id=f"{seed}-{self._next_index(previous, seed)}",
                revision=0,
                seed=seed,
                script_id=script.id,
                script_version=script.version,
                script_digest=script_digest(script),
                roster_candidates=tuple(script.roster),
                chosen_hero=chosen_hero,
                backpack=Backpack(slots=(None,) * 24),
            )
            candidate = ScriptSave(
                script_mode_version=SCRIPT_MODE_VERSION,
                saved_at=_now(),
                run=run,
                script_snapshot=library,
                value_table=self.value_table(),
            )
            # 新一轮的 revision 从 **0** 起（不是上一轮 +1）：run_id 变了，它是另一轮。
            self._write_save(candidate)
            self._kill_all_battles()
            return self._board(self.save_view())

    @staticmethod
    def _next_index(previous: ScriptSave | None, seed: int) -> int:
        """``run_id`` 的 ``<n>``：同一 seed 的建档序号（落盘前查重，票 04 §1）。"""
        if previous is None or previous.run.seed != seed:
            return 1
        _, _, tail = previous.run.run_id.rpartition("-")
        return (int(tail) if tail.isdigit() else 0) + 1

    # -- 继续 / 棋盘 ---------------------------------------------------------

    def resume(self, run_id: str) -> dict[str, Any]:
        """``POST …/resume``：读档 + 回棋盘；顺带作废该 run 未结算的战斗会话（票 04 §4）。"""
        with self._lock:
            view, save = self._require_run(run_id)
            self._kill_battle(run_id)
            return self._board(view)

    def view(self, run_id: str) -> dict[str, Any]:
        """``GET …/<run_id>``：棋盘视图（只读；不动会话）。"""
        with self._lock:
            view, _ = self._require_run(run_id)
            return self._board(view)

    # -- 进入节点（S_enter） --------------------------------------------------

    def enter(self, run_id: str, node_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST …/nodes/<node_id>``：写 S_enter；战斗房起会话并回 ``session_id``。

        同一节点再进一次（``pending.node_id`` 就是它）⇒ **不重抽**，直接把冻结的 ``spec``
        交回（票 04 §3.2），并换一个新的战斗会话（旧的按 run 作废）。
        """
        with self._lock:
            _, save = self._require_run(run_id)
            self._checked_revision(save, body)
            run = save.run
            script = next(
                item for item in save.script_snapshot.scripts if item.id == run.script_id
            )
            key = progress_key(layer_of(script, run.layer_index).id, node_id)
            if run.pending is not None and run.pending.node_id == key:
                updated = run  # 同一节点：不重抽、不写档（票 04 §3.2）
            else:
                updated = enter_node(
                    run=run,
                    library=save.script_snapshot,
                    node_id=node_id,
                    value_table=save.value_table,
                )
            candidate = save.model_copy(update={"run": updated, "saved_at": _now()})
            staged = stage_save(save, candidate)
            if staged is not None:
                try:
                    self._write_save(staged)
                except OSError as error:  # 写失败 ⇒ 400 且不起会话（票 04 §3.2）
                    raise ValueError(f"写 S_enter 失败，未起会话：{error}") from error
            final = self.save_view()
            self._kill_battle(run_id)
            spec = self._battle_spec(final.save.run)
            session_id = (
                None if spec is None else self._start_battle(final.save.run, spec)
            )
            return self._board(final, session_id=session_id)

    # -- 结算（S_done / 删档） -------------------------------------------------

    def settle(self, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``POST …/settle``：结算 ``pending``（票 04 §3.4–§3.6）。

        **胜负由服务端判**：战斗房的 ``won`` 从该会话的记录流 ``game_end`` 与
        :func:`sgs.engine.solve_pve_outcome` 读，body 里的 ``won`` 一概不看（不接受客户端自报）。
        结算结果 ``run is None`` ⇒ 通关 / 失败 ⇒ 删主档 + ``.bak``（票 04 §6）。
        """
        with self._lock:
            _, save = self._require_run(run_id)
            self._checked_revision(save, body)
            run = save.run
            order, session = self._settle_order(run, body)
            result = settle_node(
                run=run,
                library=save.script_snapshot,
                value_table=save.value_table,
                order=order,
            )
            if session is not None:
                # 打完的这一场照常是一份录像（票 04 §4.3 只豁免**被放弃**的战斗）。
                # 客户端的最后一拍轮询通常已经写过；这里补一次幂等落盘（``_SAVED`` 去重）。
                self._sessions.save_if_finished(session)
            self._kill_battle(run_id)
            events = [event.model_dump(mode="json") for event in result.events]
            if result.run is None:
                self._delete_files()
                return {
                    "run_id": run_id,
                    "outcome": result.outcome,
                    "events": events,
                    "save": self.save_payload(),
                    "board": None,
                }
            candidate = save.model_copy(
                update={"run": result.run, "saved_at": _now()}
            )
            staged = stage_save(save, candidate)
            if staged is not None:
                self._write_save(staged)
            view = self.save_view()
            return {
                "run_id": run_id,
                "outcome": result.outcome,
                "events": events,
                "save": self._view_payload(view),
                "board": self._board(view),
            }

    def _settle_order(
        self, run: RunState, body: Mapping[str, Any]
    ) -> tuple[SettleOrder, Session | None]:
        """把 body + 服务端事实拼成 :class:`SettleOrder`（形状由房间类型决定，票 17）。

        第二个返回值 = 这一场要结算的战斗会话（非战斗节点是 ``None``）：调用方据此在结算
        之后把这一局的录像落盘、再作废句柄。
        """
        spec = self._battle_spec(run)
        if spec is None:
            if body.get("session_id") is not None:
                raise ValueError("非战斗节点不接受 session_id：这一问没有会话")
            return SettleOrder(
                picks=_id_list(body, "picks"),
                option=body.get("option"),
                purchases=_purchases(body),
            ), None
        if body.get("purchases") or body.get("option") is not None:
            raise ValueError("战斗节点只接受 picks：purchases / option 属于别的房间类型")
        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("战斗节点必须带 session_id（结算从该会话的记录流判胜负）")
        tracked = self._battles.get(run.run_id)
        if tracked != session_id:
            raise script_not_found(f"没有这个战斗会话：{session_id}")
        session = self._sessions.get(session_id)
        won = self._battle_won(session)
        return SettleOrder(won=won, picks=_id_list(body, "picks")), session

    def _battle_won(self, session: Session) -> bool:
        """这一场赢没赢：``game_end`` 的侧别 + ``solve_pve_outcome`` 重算，两处必须一致。"""
        if not session.finished:
            raise ValueError("战斗还没到终局：结算发生在该局结束之后（票 04 §3.4）")
        end = next(
            (
                record
                for record in reversed(session.records())
                if getattr(record, "kind", None) == "game_end"
            ),
            None,
        )
        if end is None:  # pragma: no cover - finished 必有 game_end
            raise ValueError("记录流里没有 game_end：这一局没有可判的胜负")
        config = session.rule_config.pve
        if config is None:  # pragma: no cover - 剧本会话恒为 PvE
            raise ValueError("这个会话不是 PvE 局：判不出剧本的胜负")
        alive = frozenset(
            player.seat for player in session.engine.state.players if player.alive
        )
        expected = solve_pve_outcome(
            config=config, alive=alive, round_no=session.engine.state.round
        )
        if expected is None:
            raise ValueError("终局了却算不出这一场的胜负：配置与终局事实对不上")
        if end.side is not None and end.side != expected.side:
            raise ValueError(
                f"记录流的胜负（{end.side}）与重算的胜负（{expected.side}）不一致"
            )
        return expected.side == "ally"

    # -- 背包（立即写、幂等） ---------------------------------------------------

    def backpack(self, run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """``PUT …/backpack``：交换 / 移动 / 丢弃，**立即写**；同内容 ⇒ 不写、不 +1。

        只在节点之间可编辑（战斗中与事件 / 商店流程内不可编辑，票 22）：``pending`` 非空即拒。
        """
        with self._lock:
            _, save = self._require_run(run_id)
            self._checked_revision(save, body)
            run = save.run
            if run.pending is not None:
                raise ValueError(
                    f"节点 {run.pending.node_id} 内不可编辑背包："
                    "背包只在节点之间可编辑（票 22）"
                )
            slots = body.get("slots")
            if isinstance(slots, str) or not isinstance(slots, Sequence):
                raise ValueError(f"slots 必须是 24 格的数组：{slots!r}")
            _unique_or_refuse(slots, label="背包")
            equipped = _int_list(body, "equipped_slots")
            backpack = Backpack(
                slots=tuple(slots),
                equipped_slots=(
                    run.backpack.equipped_slots if equipped is None else equipped
                ),
            )
            candidate = save.model_copy(
                update={
                    "run": run.model_copy(update={"backpack": backpack}),
                    "saved_at": _now(),
                }
            )
            staged = stage_save(save, candidate)
            if staged is not None:
                self._write_save(staged)
            return self._board(self.save_view())

    # -- 放弃 / 暂停 -----------------------------------------------------------

    def suspend(self, run_id: str) -> dict[str, Any]:
        """``POST …/suspend``：棋盘上 = 空操作；战斗中 = 作废会话（**都不写档**）。

        前端每次离桌都调一次，于是「离桌」只有一条路径（票 04 §2 / Q3=A）。
        """
        with self._lock:
            _, save = self._require_run(run_id)
            suspended = self._kill_battle(run_id)
            return {"suspended": suspended, "save": self.save_payload()}

    def discard(self, *, expected_revision: int | None) -> dict[str, Any]:
        """``DELETE /api/script-save``：放弃存档 = 删两份 + 解锁价值表（票 04 §6）。

        坏档**也允许放弃**（版本不认识时仍要能脱身，票 04 §5）；此时没有 revision 可对，
        故只在 ``ok`` 一态校验 ``expected_revision``。
        """
        with self._lock:
            view = self.save_view()
            if view.status is SaveStatus.MISSING:
                raise script_not_found("没有剧本存档")
            if view.status is SaveStatus.OK and view.save is not None:
                if expected_revision is None:
                    raise ValueError("放弃存档必须带 expected_revision（票 04 §7）")
                self._require_revision(view.save, expected_revision)
            self._kill_all_battles()
            self._delete_files()
            return {"save": self.save_payload()}

    def restore(self) -> dict[str, Any]:
        """``POST /api/script-save/restore``：用 ``.bak`` 的原字节写回主档（**不改 revision**）。

        只在坏档态可用（票 04 §5 的「从我上一份恢复」）；主档可用时 409——恢复一个没坏的
        存档等于静默丢弃它。
        """
        with self._lock:
            view = self.save_view()
            if view.status is SaveStatus.OK:
                raise SaveConflict(
                self._conflict_payload(reason="主档可用，不需要从备份恢复")
            )
            if view.status is SaveStatus.MISSING or not view.bak_available:
                raise ScriptHttpError(
                    400,
                    {
                        **self._view_payload(view),
                        "error": "备份不可用：只能放弃存档（票 04 §5）",
                    },
                )
            data = self._read_bytes(self.backup_path)
            assert data is not None, "bak_available ⇒ 备份文件在"
            self._restore_bytes(data)
            fresh = self.save_view()
            return {
                "save": self._view_payload(fresh),
                "revision": None if fresh.save is None else fresh.save.run.revision,
            }
