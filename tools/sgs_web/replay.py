"""录像与回放：**侧车元数据**（ADR-012 / 地图 D12、D13、D14）。

**武将不在记录流里**——它不是牌、没有 delta，``PlayerPublic.hero`` 还是
``Field(exclude=True)``（``state/protocol.py``）。所以"谁是什么武将"装不进
**记录流**，只能进与它并排的那份**侧车元数据**。``Header`` 是引擎的冻结面，
动它会打翻全部基准，故本项目不改它——这正是侧车存在的理由。

一份录像 = 两个文件（``runs/`` 下、同名）：

- ``<seed>-<players>p-<YYYYmmdd-HHMMSS>.jsonl``：**canonical 记录流**，
  由 :func:`sgs.engine.dump_journal` 产出（不自己拼 JSON）；
- ``<seed>-<players>p-<YYYYmmdd-HHMMSS>.meta.json``：重放所需的前置事实 + 接入层事实。

落盘纪律（D14）：只在**终局**后写一次；先写 ``.tmp`` 再 ``os.replace``；
同名加 ``-2`` 后缀、**绝不覆盖**；坏文件**报错**、不静默跳过。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sgs.agents import TapePlayer, tape_from_journal
from sgs.data import load_content_pack, validate_rule_config
from sgs.engine import Engine, dump_journal, read_journal

from .narrative import NO_VIEWER, build_narrative
from .session import Session

__all__ = ["META_VERSION", "list_runs", "load_run", "read_run_meta", "save_run", "replay_journal"]

META_VERSION = 1
"""侧车元数据的结构版本；与记录流的 ``event_schema_version`` 各自升版。"""

_RUN_GLOB = "*.meta.json"


def _run_stem(seed: int, players: int, when: datetime) -> str:
    return f"{seed}-{players}p-{when.strftime('%Y%m%d-%H%M%S')}"


def _unique_path(runs_dir: Path, stem: str) -> tuple[Path, Path]:
    """给一对 (jsonl, meta) 找一个不冲突的名字；冲突加 ``-2`` / ``-3``……**不覆盖**。"""
    suffix = 0
    while True:
        candidate = stem if suffix == 0 else f"{stem}-{suffix + 1}"
        journal = runs_dir / f"{candidate}.jsonl"
        meta = runs_dir / f"{candidate}.meta.json"
        if not journal.exists() and not meta.exists():
            return journal, meta
        suffix += 1


def _human_seat_won_from_records(records: tuple[Any, ...], human_seat: int | None) -> bool | None:
    """从**已在内存里的**记录流读"人类座位是否在获胜一侧"；没有终局 / 没有人类座位 ⇒ ``None``。

    perf-web 票 01（用户 2026-11-08 指定）：这个事实以前由列表在 HTTP 请求里对**每局**
    调 ``read_journal`` 现算（908MB / 257 局 ≈ 48s）；现在在**落盘那一刻**算好写进侧车
    元数据，列表只读 meta——记录流一个字节都不用再解析。旧录像没有这个字段 ⇒ 列表给
    ``None``（回放页显示「—」），**不**回落到读记录流（那正是要消掉的成本）。
    """
    end = next((r for r in reversed(records) if getattr(r, "kind", None) == "game_end"), None)
    if end is None or human_seat is None:
        return None
    return human_seat in getattr(end, "winners", ())


def build_meta(session: Session, when: datetime) -> dict[str, Any]:
    """侧车元数据的内容：重放所需的前置事实 + 接入层事实。"""
    seed, tape = tape_from_journal(session.records())
    return {
        "version": META_VERSION,
        "event_schema_version": session.records()[0].event_schema_version,  # type: ignore[union-attr]
        "seed": seed,
        "player_count": session.rule_config.player_count,
        "pack_id": session.pack.pack_id,
        "deck_profile": session.pack.deck_profile,
        "rule_config": session.rule_config.model_dump(mode="json"),
        "heroes": list(session.hero_assignments),
        "hero_pool": list(session.hero_pool) if session.hero_pool is not None else None,
        "hero_pool_explicit": session.hero_pool is not None,
        "human_seat": session.human_seat,
        # perf-web 票 01：胜负在落盘时定格，`list_runs` 因此只读 meta（见上面的辅助函数）。
        "human_seat_won": _human_seat_won_from_records(session.records(), session.human_seat),
        "ai": session.ai,
        "timeout_seconds": session.timeout_seconds,
        "timeouts": session.timeouts,
        "finished_at": when.isoformat(),
        "session_log": [entry.as_dict() for entry in session.session_log],
        "tape": [action.model_dump(mode="json") for action in tape],
    }


def save_run(runs_dir: Path, session: Session, *, when: datetime | None = None) -> Path:
    """把一局写进录像目录；返回**记录流**文件的路径。

    只在终局后调用（调用方负责判），且只写一次。
    """
    if not session.finished:
        raise ValueError("本局还没结束，不写录像（D14：只在 game_end 之后写）")
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    journal_path, meta_path = _unique_path(
        runs_dir, _run_stem(session.seed, session.rule_config.player_count, when or datetime.now())
    )

    _write_atomic(journal_path, dump_journal(session.records()).encode("utf-8"))
    meta = build_meta(session, when or datetime.now())
    _write_atomic(
        meta_path, json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    )
    return journal_path


def _write_atomic(path: Path, data: bytes) -> None:
    """先写 ``.tmp`` 再 ``os.replace``：进程被杀最多丢这一局，绝不留半局文件。"""
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 读回
# ---------------------------------------------------------------------------


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """录像列表（按文件名倒序 = 新的在前）；只列出**配对齐全**的那些。

    **只读侧车元数据**（perf-web 票 01）：胜负在 `save_run` 落盘时已写进 meta，这里
    **一局记录流都不解析** —— 列表耗时与录像总字节数无关（此前 908MB / 257 局 ≈ 48s）。
    旧录像没有 `human_seat_won` 字段 ⇒ `None`（回放页显示「—」），不回落读记录流。
    """
    runs_dir = Path(runs_dir)
    if not runs_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for meta_path in sorted(runs_dir.glob(_RUN_GLOB), reverse=True):
        name = meta_path.name[: -len(".meta.json")]
        if not (runs_dir / f"{name}.jsonl").is_file():
            continue
        try:
            meta = read_run_meta(runs_dir, name)
        except ValueError:
            out.append({"name": name, "broken": True})
            continue
        out.append(
            {
                "name": name,
                "seed": meta["seed"],
                "player_count": meta["player_count"],
                "human_seat": meta["human_seat"],
                "heroes": meta.get("heroes", []),
                "finished_at": meta.get("finished_at"),
                # 只读侧车元数据：**不解析记录流**（perf-web 票 01）。旧录像没有该字段 ⇒ None。
                "human_seat_won": meta.get("human_seat_won"),
            }
        )
    return out


def read_run_meta(runs_dir: Path, name: str) -> dict[str, Any]:
    """读侧车元数据；缺字段 / 版本不认识 ⇒ :class:`ValueError`（**不静默跳过**）。"""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"录像名非法：{name!r}")
    path = Path(runs_dir) / f"{name}.meta.json"
    if not path.is_file():
        raise ValueError(f"录像没有侧车元数据：{path.name}")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"侧车元数据不是合法 JSON：{path.name}（{error}）") from error
    if not isinstance(meta, dict):
        raise ValueError(f"侧车元数据必须是 JSON 对象：{path.name}")
    if meta.get("version") != META_VERSION:
        raise ValueError(
            f"侧车元数据版本不认识：{meta.get('version')!r}（本程序只认 {META_VERSION}）"
        )
    for field in ("seed", "player_count", "pack_id", "deck_profile", "rule_config", "tape"):
        if field not in meta:
            raise ValueError(f"侧车元数据缺字段 {field}：{path.name}")
    return meta


def load_run(runs_dir: Path, name: str) -> dict[str, Any]:
    """读一局录像：侧车元数据 + 记录流的 JSON 形态（回放页消费）。

    坏文件一律报错（:class:`ValueError`），由 HTTP 层映射成 400——**不返回半局数据**。
    """
    meta = read_run_meta(runs_dir, name)
    journal_path = Path(runs_dir) / f"{name}.jsonl"
    if not journal_path.is_file():
        raise ValueError(f"录像没有记录流文件：{journal_path.name}")
    try:
        records = read_journal(journal_path)
    except Exception as error:  # JournalVersionError / 结构性错误
        raise ValueError(f"记录流读不动：{journal_path.name}（{error}）") from error
    header = records[0]
    if getattr(header, "kind", None) != "header":
        raise ValueError(f"记录流首行不是 header：{journal_path.name}")
    payloads = [json.loads(line) for line in dump_journal(records).splitlines()]
    # U1-05：牌桌与回放共用同一个叙事生成器；回放按**录像的人类座位**过滤
    # （U2 的座位选择器 / 上帝视角开关接入时再传具体查看者或 ``None``）。
    # 没有人类座位的录像（全是 AI）不能因为"查不到座位"退化成全知：``NO_VIEWER`` 是一个
    # 不存在座位的观察者，只留公开区 / 阵亡亮明，隐藏手牌与牌堆顺序照样不写。
    human_seat = meta.get("human_seat")
    viewer = int(human_seat) if human_seat is not None else NO_VIEWER
    return {
        "name": name,
        "meta": meta,
        "records": payloads,
        "narrative": build_narrative(
            payloads,
            viewer=viewer,
            heroes=meta.get("heroes") or (),
            pack_id=meta["pack_id"],
        ),
    }


def replay_journal(meta: dict[str, Any]) -> tuple[Any, ...]:
    """用侧车元数据重开一局并把磁带跑完，返回**这次重放**的记录。

    这是"回放闭环"判据的落点：调用方拿它与录像里的记录流逐行比对。
    武将走 meta 的 ``heroes`` / ``hero_pool``（与基准测试同一姿态，
    因为**武将不是 header 的一部分**）。
    """
    pack = load_content_pack(meta["pack_id"], deck_profile=meta["deck_profile"])
    engine = Engine.new_game(
        seed=meta["seed"],
        rule_config=validate_rule_config(meta["rule_config"]),
        pack=pack,
        heroes=tuple(meta.get("heroes") or ()),
        hero_pool=None if meta.get("hero_pool") is None else tuple(meta["hero_pool"]),
    )
    from sgs.state import parse_action

    tape = tuple(parse_action(raw) for raw in meta["tape"])
    engine.run([TapePlayer(tape)] * int(meta["player_count"]))
    return tuple(engine.records)


