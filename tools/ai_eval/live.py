"""实机读数（票 15；spec §5.1–§5.6 / §7.2）：一局 journal + meta → 两行摘要 + 空报账表。

**只回答"刺不刺眼、卡不卡、崩不崩"**：不判 verdict、不打胜率、不产决策 diff（n=1 只否决
"刺眼 / 卡 / 崩"，不否决统计结论）。完整配方见 `docs/agents/acceptance-ai.md`。

两个数据源各给一半，**不混用**：

- **journal**（``<stem>.jsonl``）是这局的真值：种子 / 人数 / 牌堆档位 / 人类身份 / 胜负，以及
  §2.11 的 ``pass_with_options``——``legal_actions`` 在记录流里有（``actor`` 就是被问的座位）；
- **meta**（``<stem>.meta.json``）提供重放输入（``tape`` / ``heroes``）与接入层事实
  （``human_seat`` / ``timeouts``）。复合清单（预发动 / 转化 / 借刀附带指定）在记录流里是
  ``exclude`` 的，**只能**重放一局拿引擎产出的 ``Query`` ⇒ ``unfilled_composite`` 从重放里数。

两条口径都走 :mod:`ai_eval.misses`（与 ``quick`` / ``promote`` / ``seal`` 同一份实现）；
**人类座位的 Pass 是他的选择**（spec §5.3），按 ``actor == human_seat`` 整条剔除。

同 stem 的两个文件必须描述同一局：种子 / 人数 / 包 / 档位有一项对不上就**响亮失败**，
不把两份文件混着读。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sgs.data import IDENTITY_LABELS, Identity, load_content_pack
from sgs.engine import JournalVersionError, read_journal

from .errors import AiEvalRunError, AiEvalSpecError
from .misses import MissCounts, count_misses, fact_from_query, merge_misses

__all__ = ["LEDGER_COLUMNS", "LiveReading", "render_live", "run_live"]

LEDGER_COLUMNS: tuple[str, ...] = (
    "seq",
    "座位",
    "身份",
    "询问类型",
    "当时事实",
    "实际选择",
    "期望选择",
    "D/S",
    "已知/新",
)
"""空报账表的列（spec §5.3 的字段，按 D/S 归类、标注已知 / 新）。"""

DEFAULT_RUNS_DIR = Path("runs")
"""``--runs-dir`` 的缺省（spec §5.4 的字面 ``runs/``，按 **cwd** 解析）。

网页服务的缺省是**仓根**的 ``tools/sgs_web/serve.py:DEFAULT_RUNS_DIR``（``<repo>/runs``）。
配方在仓根起服务 / 跑读数，两者指向同一目录；但一个是命令行惯例、一个是服务端常量，
不合并成一个（合并会把 cwd 语义偷换成 repo-root 语义）。
"""


@dataclass(frozen=True)
class LiveReading:
    """一局实机读数的全部事实；渲染只读这个对象（测试不必解析文本）。"""

    seed: int
    player_count: int
    mode: str
    deck_size: int
    human_seat: int | None
    human_identity: str | None
    outcome: str
    winners: tuple[int, ...]
    queries: int
    misses: MissCounts
    timeouts: int


def run_live(*, run: str, runs_dir: Path | str = DEFAULT_RUNS_DIR) -> LiveReading:
    """读一局录像，返回两行摘要所需的读数；坏输入一律点名路径失败。"""
    runs_dir = Path(runs_dir)
    # 先读侧车：它校验录像名 / 版本 / 必填字段，并且比记录流小得多（缺 meta 时不必先读几 MB）。
    meta = _read_meta(runs_dir, run)
    meta_path = runs_dir / f"{run}.meta.json"
    journal_path = runs_dir / f"{run}.jsonl"
    if not journal_path.is_file():
        raise AiEvalSpecError(f"找不到记录流：{journal_path}")
    records = _read_journal(journal_path)

    header = records[0]
    if getattr(header, "kind", None) != "header":
        raise AiEvalSpecError(f"记录流首行不是 header：{journal_path}")
    _check_same_game(header, meta, journal_path)
    end = records[-1]
    if getattr(end, "kind", None) != "game_end":
        raise AiEvalSpecError(
            f"记录流没有终局记录（这局可能崩溃而没落盘）：{journal_path}"
        )

    human_seat = _human_seat(meta, meta_path)
    misses = _count_misses(records, meta, human_seat, journal_path)
    pack = load_content_pack(header.pack_id, deck_profile=header.deck_profile)
    return LiveReading(
        seed=header.seed,
        player_count=header.rule_config.player_count,
        mode=header.rule_config.mode.value,
        deck_size=len(pack.deck.cards),
        human_seat=human_seat,
        human_identity=_human_identity(records, human_seat),
        outcome=_outcome(end),
        winners=tuple(int(seat) for seat in getattr(end, "winners", ())),
        queries=sum(1 for record in records if getattr(record, "kind", None) == "query"),
        misses=misses,
        timeouts=_timeouts(meta, meta_path),
    )


def render_live(reading: LiveReading) -> str:
    """§5.3 的前两行 + 一张**空**报账表（第三行与结论由 agent / 人补，工具不替人写）。"""
    return "\n".join(
        [
            _headline(reading),
            _miss_line(reading),
            "",
            "| " + " | ".join(LEDGER_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in LEDGER_COLUMNS) + " |",
        ]
    ) + "\n"


# ---------------------------------------------------------------------------
# 读数
# ---------------------------------------------------------------------------


def _headline(reading: LiveReading) -> str:
    if reading.human_seat is None:
        human = "无"
    elif reading.human_identity is None:
        human = f"seat{reading.human_seat}"
    else:
        human = f"seat{reading.human_seat}({reading.human_identity})"
    winners = ",".join(str(seat) for seat in reading.winners)
    return (
        f"实机 seed={reading.seed} {reading.player_count}p {reading.mode} "
        f"{reading.deck_size} 人类={human} 终局={reading.outcome} 获胜者=[{winners}]"
    )


def _miss_line(reading: LiveReading) -> str:
    misses = reading.misses
    return (
        f"询问={reading.queries} AI漏动作 "
        f"pass={misses.pass_with_options}/{misses.pass_denominator}"
        f"{_rate(misses.pass_with_options, misses.pass_denominator)} "
        f"composite={misses.unfilled_composite}/{misses.composite_denominator}"
        f"{_rate(misses.unfilled_composite, misses.composite_denominator)} "
        f"人类超时={reading.timeouts}"
    )


def _rate(made: int, denominator: int) -> str:
    """分母为 0 写空串（模板 ``composite=0/0``）；否则一位小数的百分比。"""
    if not denominator:
        return ""
    return f"({made / denominator * 100:.1f}%)"


def _outcome(end: Any) -> str:
    side = getattr(end, "side", None)
    if side is not None:
        return f"{IDENTITY_LABELS[Identity(side)]}胜"
    if getattr(end, "result", None) == "draw":
        return "平局"
    return "获胜"


def _human_identity(records: Iterable[Any], human_seat: int | None) -> str | None:
    """人类身份取记录流里的**暗发**记录（阵亡亮明不改变"这局我是什么身份"）。"""
    if human_seat is None:
        return None
    for record in records:
        if (
            getattr(record, "delta_type", None) == "identity_deal"
            and getattr(record, "seat", None) == human_seat
        ):
            return IDENTITY_LABELS[record.identity]
    return None


def _count_misses(
    records: tuple[Any, ...],
    meta: dict[str, Any],
    human_seat: int | None,
    journal_path: Path,
) -> MissCounts:
    """§2.11 的两个指标：Pass 从记录流数，复合清单从重放出来的引擎询问数。"""
    pass_facts = [
        replace(fact_from_query(query, action), composite_offered=None)
        for query, action in _decisions(records)
        if not _is_human(query, human_seat)
    ]
    replayed = _replay(meta, journal_path)
    composite_facts = [
        replace(fact_from_query(query, action), non_pass_legal=None)
        for query, action in _decisions(replayed)
        if not _is_human(query, human_seat)
    ]
    return merge_misses(count_misses(pass_facts), count_misses(composite_facts))


def _decisions(records: Iterable[Any]) -> Iterator[tuple[Any, Any]]:
    """把 ``query`` 与**被接受**的 ``action`` 按 seq 配成对；被拒 / 未应答的不算答案。"""
    pending = None
    for record in records:
        kind = getattr(record, "kind", None)
        if kind == "query":
            pending = record.query
        elif (
            kind == "action"
            and record.accepted
            and pending is not None
            and record.answers_seq == pending.seq
        ):
            yield pending, record.action
            pending = None


def _is_human(query: Any, human_seat: int | None) -> bool:
    return human_seat is not None and query.actor == human_seat


# ---------------------------------------------------------------------------
# 读入 / 校验
# ---------------------------------------------------------------------------


def _read_journal(path: Path) -> tuple[Any, ...]:
    try:
        return read_journal(path)
    except (ValueError, JournalVersionError, OSError) as error:
        raise AiEvalSpecError(f"记录流读不动：{path}（{error}）") from error


def _read_meta(runs_dir: Path, name: str) -> dict[str, Any]:
    from sgs_web.replay import read_run_meta

    path = runs_dir / f"{name}.meta.json"
    if not name:
        # ``read_run_meta`` 的边界只拦路径分隔符 / 前导点；空名会拼成 ".meta.json"。
        raise AiEvalSpecError(f"录像名非法：{name!r}")
    try:
        return read_run_meta(runs_dir, name)
    except (ValueError, OSError) as error:
        raise AiEvalSpecError(f"侧车元数据读不动：{path}（{error}）") from error


def _replay(meta: dict[str, Any], journal_path: Path) -> tuple[Any, ...]:
    """重放这一局的磁带，拿回**带复合清单**的引擎询问（记录流里那些字段是 exclude 的）。"""
    from sgs_web.replay import replay_journal

    try:
        return replay_journal(meta)
    except Exception as error:  # noqa: BLE001 - 回放边界：引擎侧任何失败都归"这次没测成"
        raise AiEvalRunError(f"重放失败：{journal_path}（{error}）") from error


def _human_seat(meta: dict[str, Any], meta_path: Path) -> int | None:
    human_seat = meta.get("human_seat")
    if human_seat is None:
        return None
    player_count = meta.get("player_count")
    if (
        not isinstance(human_seat, int)
        or isinstance(human_seat, bool)
        or not isinstance(player_count, int)
        or not 0 <= human_seat < player_count
    ):
        raise AiEvalSpecError(f"侧车元数据的人类座位非法：{human_seat!r}（{meta_path}）")
    return human_seat


def _timeouts(meta: dict[str, Any], meta_path: Path) -> int:
    timeouts = meta.get("timeouts")
    if not isinstance(timeouts, int) or isinstance(timeouts, bool) or timeouts < 0:
        raise AiEvalSpecError(
            f"侧车元数据的超时次数非法：{timeouts!r}（未测 ≠ 0；{meta_path}）"
        )
    return timeouts


def _check_same_game(header: Any, meta: dict[str, Any], journal_path: Path) -> None:
    """同 stem 的两份文件必须描述同一局；否则重放出来的不是这条记录流。"""
    recorded = {
        "seed": header.seed,
        "player_count": header.rule_config.player_count,
        "pack_id": header.pack_id,
        "deck_profile": header.deck_profile,
    }
    for field, value in recorded.items():
        if meta.get(field) != value:
            raise AiEvalSpecError(
                f"记录流与侧车元数据不是同一局（{field}：记录={value!r}，"
                f"侧车={meta.get(field)!r}）：{journal_path}"
            )
