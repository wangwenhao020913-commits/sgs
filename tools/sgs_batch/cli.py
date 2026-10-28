"""``python tools/batch_games.py`` 的参数解析 / 写盘 / 退出码（票 11；spec §1.4）。

退出码：全部完成且无错 = 0；任一局 ``error`` 非空 = 1；参数 / 模式 / 规格错误 = 2。
主 JSON 走 ``--out``（省略 = stdout），决策侧车走 ``--decisions-out``（给出即隐式开启
``capture_decisions``）；侧车每局一行 ``sgs.batch.actions.v1``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sgs.data import DataLoadError, Mode

from .errors import BatchSpecError, BatchWorkerError
from .models import BatchResult
from .runner import run_games


def _csv(raw: str) -> tuple[str, ...]:
    """``a,b,c`` → ``("a", "b", "c")``；空白与空项一律剔除（与 CLI 的同一约定）。"""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def parse_seeds(raw: str) -> list[int]:
    """``136..143`` / ``136,138`` / ``136..139,150`` → 种子列表（升序与去重在批量层判）。"""
    seeds: list[int] = []
    for part in (item.strip() for item in raw.split(",")):
        if not part:
            continue
        if ".." in part:
            start, _, end = part.partition("..")
            try:
                first, last = int(start), int(end)
            except ValueError as error:
                raise BatchSpecError(f"--seeds 里的区间不是整数：{part!r}") from error
            if last < first:
                raise BatchSpecError(f"--seeds 的区间上界小于下界：{part!r}")
            seeds.extend(range(first, last + 1))
        else:
            try:
                seeds.append(int(part))
            except ValueError as error:
                raise BatchSpecError(f"--seeds 里有非整数：{part!r}") from error
    if not seeds:
        raise BatchSpecError("--seeds 为空")
    return seeds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="batch_games",
        description="批量跑《三国杀》对局，输出 sgs.batch.v1 结果 JSON。",
    )
    parser.add_argument(
        "--seeds",
        required=True,
        help="种子：136..143 或 136,138,140（升序执行、拒绝重复）。",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=[mode.value for mode in Mode],
        help="模式（显式入参，不从人数推导）。",
    )
    parser.add_argument(
        "--players",
        type=int,
        default=5,
        help="座位数；可跑 2 + no_identity 与 4..8 + identity。",
    )
    parser.add_argument(
        "--deck-profile",
        default=None,
        help="牌堆档位；省略 = 内容包默认档（standard_108）。",
    )
    parser.add_argument(
        "--heroes",
        type=_csv,
        default=(),
        help="显式武将（逗号分隔、按座位序）。",
    )
    parser.add_argument(
        "--hero-pool",
        type=_csv,
        default=None,
        help="启用池（逗号分隔）；省略 = 启用的并集（与 128 局同口径）。",
    )
    parser.add_argument(
        "--no-heroes",
        action="store_true",
        help="无武将模式（与 --heroes / --hero-pool 互斥）。",
    )
    parser.add_argument(
        "--seat-policies",
        type=_csv,
        default=None,
        help="每座位档位（逗号分隔，长度 = players；可选 heuristic / random）；省略 = 全 heuristic。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="进程数 1..8（默认 1，不做 auto）。",
    )
    parser.add_argument(
        "--keep-journal",
        action="store_true",
        help="把 canonical journal 写进主 JSON（只建议小批量）。",
    )
    parser.add_argument(
        "--decisions-out",
        type=Path,
        default=None,
        help="决策侧车（JSONL）；给出即隐式开启 capture_decisions。",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="主 JSON 路径；省略 = 写 stdout。",
    )
    return parser


def _write(path: Path, text: str) -> None:
    """按 LF 写盘（与 canonical 口径一致，关掉平台换行翻译）。"""
    path.write_text(text, encoding="utf-8", newline="")


def main(argv: list[str] | None = None) -> int:
    """跑一批并返回进程退出码（0 / 1 / 2，spec §1.4）。"""
    args = _parser().parse_args(argv)
    try:
        seeds = parse_seeds(args.seeds)
        if args.no_heroes and (args.heroes or args.hero_pool is not None):
            raise BatchSpecError("--no-heroes 与 --heroes / --hero-pool 互斥")
        hero_pool = None if args.no_heroes else (() if args.hero_pool is None else args.hero_pool)
        batch = run_games(
            seeds=seeds,
            mode=args.mode,
            players=args.players,
            deck_profile=args.deck_profile,
            heroes=args.heroes,
            hero_pool=hero_pool,
            seat_policies=args.seat_policies,
            keep_journal=args.keep_journal,
            capture_decisions=args.decisions_out is not None,
            workers=args.workers,
        )
    except (BatchSpecError, DataLoadError) as error:
        print(f"参数错误：{error}", file=sys.stderr)
        return 2
    except BatchWorkerError as error:
        print(f"批量失败：{error}", file=sys.stderr)
        return 1

    _emit(batch, out=args.out, decisions_out=args.decisions_out)
    return 0 if batch.summary["errors"] == 0 else 1


def _emit(batch: BatchResult, *, out: Path | None, decisions_out: Path | None) -> None:
    """写主 JSON 与（可选的）决策侧车；主 JSON 省略路径时写 stdout。"""
    text = json.dumps(batch.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        _write(out, text)
        summary = batch.summary
        print(
            f"games={summary['games']} finished={summary['finished']} errors={summary['errors']}",
            file=sys.stderr,
        )
    if decisions_out is not None:
        _write(decisions_out, "".join(f"{line}\n" for line in batch.actions_lines()))
