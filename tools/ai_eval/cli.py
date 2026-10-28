"""``python -m ai_eval`` 的参数解析 / 摘要 / 退出码（票 13；spec §2.9 / §2.8）。

退出码（与 ``sgs_batch`` 同口径）：

- **0**：跑完且 ``errors == 0``；
- **1**：评估**运行期**失败——有对局报错 / worker 崩溃 / 用例子进程起不来（重跑可能变好，
  但结论不可用）；
- **2**：参数、规格、版本加载错误（缺 tag 是这一类——**没有**"静默打回 HEAD"）。

摘要恒走 stderr（JSON 走 ``--out``，省略时走 stdout）：``seal`` 的摘要同样只有聚合
（批名 / 摘要 / 逐版本胜率），**不打印逐种子**（spec §2.4 B）。

例外是 ``live``（实机读数，spec §5）：它不产 JSON、不判 verdict、不打胜率，两行摘要与空
报账表直接写 **stdout**，供人 / agent 原地转写。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from sgs.data import DataLoadError
from sgs_batch import BatchSpecError, BatchWorkerError

from .errors import AiEvalError, AiEvalRunError
from .live import DEFAULT_RUNS_DIR, render_live, run_live
from .run import run_promote, run_quick, run_seal
from .versions import HEURISTIC_PATH, load_version, repository_root

__all__ = ["main"]

DEFAULT_DEALS = {"quick": 128, "promote": 768}
"""判决批的缺省副数（spec §2.5）；``seal`` 的 1024 写死在 ``run.SEAL_DEALS``。"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai_eval",
        description="AI 评估：判决批上的决策 diff / 配对胜率差 / 封印聚合（spec §2.9）。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def _common(
        sub: argparse.ArgumentParser, *, deals: int | None = None
    ) -> None:
        if deals is not None:
            sub.add_argument(
                "--deals",
                type=int,
                default=deals,
                help=f"判决批的副数（缺省 {deals}；测试用 4 副这种极小集）。",
            )
        sub.add_argument("--workers", type=int, default=1, help="进程数 1..8（默认 1）。")
        sub.add_argument(
            "--out", type=Path, default=None, help="结果 JSON 路径；省略 = 写 stdout。"
        )

    quick = commands.add_parser("quick", help="快检：128 副的决策 diff（不看胜负）。")
    quick.add_argument("--baseline", required=True, help="父版本 ref（tag，如 ai-v0）。")
    quick.add_argument(
        "--candidate-ref", default="HEAD", help="被评估的提交 ref（缺省 HEAD）。"
    )
    _common(quick, deals=DEFAULT_DEALS["quick"])
    quick.add_argument(
        "--no-cases", action="store_true", help="不跑 tests/test_ai_cases.py（cases 记为未测）。"
    )
    quick.add_argument(
        "--predicted-kind",
        action="append",
        default=[],
        help="预注册的询问类型（可重复）；给出后才判 diff_attributed。",
    )

    promote = commands.add_parser(
        "promote", help="晋级：换座配对 ×（父版本 + 原点基线）+ diff + 分身份。"
    )
    promote.add_argument("--baseline", required=True, help="父版本 ref（如 ai-vN）。")
    promote.add_argument("--anchor", required=True, help="原点基线 ref（如 ai-v0）。")
    promote.add_argument(
        "--candidate-ref", default="HEAD", help="被评估的提交 ref（缺省 HEAD）。"
    )
    _common(promote, deals=DEFAULT_DEALS["promote"])
    promote.add_argument(
        "--no-cases", action="store_true", help="不跑 tests/test_ai_cases.py（cases 记为未测）。"
    )
    promote.add_argument(
        "--predicted-kind",
        action="append",
        default=[],
        help="预注册的询问类型（可重复）；给出后才判 diff_attributed。",
    )

    seal = commands.add_parser(
        "seal", help="封印：1024 副固定批、只出聚合、不打印逐种子。"
    )
    seal.add_argument("--batch", required=True, help="封印批名字（如 sealed-1，用一次退役）。")
    seal.add_argument(
        "--versions",
        nargs="+",
        required=True,
        help="参与封印的版本 ref（至少一个；每个版本自身四副本对打）。",
    )
    # 副数**不暴露**：spec §2.4 B 的 1024 副写死，缩水就不是封印批了。
    _common(seal)

    live = commands.add_parser(
        "live", help="实机读数：一局 journal + meta 的两行摘要 + 空报账表（spec §5）。"
    )
    live.add_argument("--run", required=True, help="录像文件名（不含 .jsonl / .meta.json 后缀）。")
    live.add_argument(
        "--runs-dir",
        type=Path,
        default=DEFAULT_RUNS_DIR,
        help=f"录像目录（缺省 {DEFAULT_RUNS_DIR}）。",
    )
    return parser


def _warn_if_worktree_differs(candidate_ref: str) -> None:
    """工作树的启发式与 candidate 提交字节不同就警告一行：本批评估的是**提交字节**。"""
    try:
        candidate = load_version(candidate_ref)
    except AiEvalError:
        return  # 真正的加载失败在 run_* 里点名 ref 报错（这里不抢先吞掉错误语义）
    worktree = repository_root() / HEURISTIC_PATH
    if not worktree.exists():
        return
    digest = hashlib.sha256(worktree.read_bytes()).hexdigest()
    if digest != candidate.heuristic_sha256:
        print(
            f"警告：工作树的 {HEURISTIC_PATH} 与 candidate={candidate_ref}"
            f"（{candidate.commit[:12]}）不一致；本批评估的是**提交字节** {candidate.heuristic_sha256[:12]}。",
            file=sys.stderr,
        )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _summary(result: dict[str, Any]) -> list[str]:
    """摘要行（恒走 stderr）；封印批也只有聚合，逐种子一律不出现。"""
    tier = result["tier"]
    seed_batch = result["seed_batch"]
    head = (
        f"{tier} batch={seed_batch['sha256'][:12]} deals={seed_batch['deals']} "
        f"errors={result['errors']}"
    )
    if tier == "quick":
        diff = result["decision_diff"]
        cases = result["cases"]
        return [
            f"{head} differing={diff['deals_differing']}/{seed_batch['deals']} "
            f"kinds={diff['first_divergence_by_kind']}",
            f"candidate={result['candidate']['ref'][:12]} "
            f"baseline={result['baseline']['ref']} "
            f"cases={cases['passed']}/{cases['total']} "
            f"pass_with_options={result['miss_rate']['pass_with_options']}",
        ]
    if tier == "promote":
        diff = result["decision_diff"]
        baseline = result["vs_baseline"]
        anchor = result["vs_anchor"]
        verdict = result["verdict"]
        return [
            f"{head} differing={diff['deals_differing']}/{seed_batch['deals']} "
            f"kinds={diff['first_divergence_by_kind']}",
            f"vs_baseline candidate={baseline['win_rate_candidate']} "
            f"baseline={baseline['win_rate_baseline']} diff={baseline['diff']} "
            f"ci95={baseline['ci95']}",
            f"vs_anchor diff={anchor['diff']} ci95={anchor['ci95']}",
            f"verdict cases_green={verdict['cases_green']} "
            f"non_inferior={verdict['non_inferior']} "
            f"anchor_non_inferior={verdict['anchor_non_inferior']} "
            f"diff_attributed={verdict['diff_attributed']}",
        ]
    lines = [
        f"seal batch={seed_batch.get('batch')} sha256={seed_batch['sha256'][:12]} "
        f"deals={seed_batch['deals']} errors={result['errors']}"
    ]
    for entry in result["versions"]:
        lines.append(
            f"version={entry['ref']} commit={entry['commit'][:12]} "
            f"deals={entry['deals']} win_rate={entry['win_rate']} "
            f"ci95={entry['ci95']}"
        )
    return lines


def _emit(result: dict[str, Any], *, out: Path | None) -> None:
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        _write(out, text)
    for line in _summary(result):
        print(line, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """跑一档并返回退出码（0 / 1 / 2；spec §2.9 的命令面）。"""
    args = _parser().parse_args(argv)
    try:
        if args.command == "quick":
            _warn_if_worktree_differs(args.candidate_ref)
            result = run_quick(
                baseline=args.baseline,
                candidate_ref=args.candidate_ref,
                deals=args.deals,
                workers=args.workers,
                measure_cases=not args.no_cases,
                predicted_kinds=args.predicted_kind,
                progress=_progress,
            )
        elif args.command == "promote":
            _warn_if_worktree_differs(args.candidate_ref)
            result = run_promote(
                baseline=args.baseline,
                anchor=args.anchor,
                candidate_ref=args.candidate_ref,
                deals=args.deals,
                workers=args.workers,
                measure_cases=not args.no_cases,
                predicted_kinds=args.predicted_kind,
                progress=_progress,
            )
        elif args.command == "live":
            # 实机读数不是判决批：只有两行摘要 + 空报账表，没有 JSON / verdict / 胜率。
            sys.stdout.write(render_live(run_live(run=args.run, runs_dir=args.runs_dir)))
            return 0
        else:
            # 封印批副数写死（spec §2.4 B）：CLI 不传 deals。
            result = run_seal(
                batch=args.batch,
                versions=args.versions,
                workers=args.workers,
                progress=_progress,
            )
    except AiEvalRunError as error:
        print(f"评估失败：{error}", file=sys.stderr)
        return 1
    except (AiEvalError, BatchSpecError, DataLoadError) as error:
        print(f"参数错误：{error}", file=sys.stderr)
        return 2
    except BatchWorkerError as error:
        print(f"评估失败：{error}", file=sys.stderr)
        return 1
    _emit(result, out=args.out)
    return 0 if result["errors"] == 0 else 1


def _progress(label: str, done: int, total: int) -> None:
    print(f"  · {label}: {done}/{total}", file=sys.stderr)
