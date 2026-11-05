"""进程池批量执行（票 11；spec §1.5）。

三条纪律在此落地：

1. **每 worker 加载一次内容包常驻**：池的 ``initializer`` 按同一 ``deck_profile`` 调一次
   ``load_content_pack``，对象缓存在本模块的 ``_pack`` 里，此后每局只复用；父进程那一份
   只用于写 ``config``（串行路径才直接复用）。
2. **总线程数 = workers**：父进程**创建池前**就把 BLAS / OMP / MKL / numexpr 的线程数各压到
   1；worker 初始化再兜底一次（父进程若被调用方绕过也不至于超订）。
3. **崩溃点名、不返回半批**：``BrokenProcessPool`` 转成 :class:`BatchWorkerError`，消息列出
   还没拿到结果的种子；单局异常早在 :func:`~sgs_batch.runner.run_one` 内部收成本局
   ``error``，故不会走到这里。

v1 不设单局超时，多进程**不承诺线性加速**，只保证正确性与确定性（spec §1.5）。
"""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from sgs.data import ContentPack, load_content_pack

from .errors import BatchWorkerError
from .models import GameResult, GameSpec
from .runner import ParticipantsFactory, run_one

THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)
"""BLAS / OMP 的线程上限变量；每个 worker 各一份，故全批总线程数 = workers。"""

_pack: ContentPack | None = None
_pack_deck: str | None = None


def _limit_threads() -> None:
    """把本进程的数值库线程数压到 1（父进程在建池前、worker 初始化各调用一次）。"""
    for name in THREAD_ENV_VARS:
        os.environ[name] = "1"


def _load_pack(deck_profile: str | None) -> ContentPack:
    """worker 进程内的内容包缓存：同一 ``deck_profile`` 只加载一次。"""
    global _pack, _pack_deck
    if _pack is None or _pack_deck != deck_profile:
        _pack = load_content_pack(deck_profile=deck_profile)
        _pack_deck = deck_profile
    return _pack


def _init_worker(deck_profile: str | None) -> None:
    """进程池 initializer：压线程数 + 加载一次内容包常驻（spec §1.5）。"""
    _limit_threads()
    _load_pack(deck_profile)


@dataclass(frozen=True)
class _Task:
    """一个 worker 任务 = 一局；字段全部可 pickle（factory 也必须是顶层可 pickle 的）。"""

    spec: GameSpec
    keep_journal: bool
    capture_decisions: bool
    participants_factory: ParticipantsFactory | None


def _run_task(task: _Task) -> GameResult:
    return run_one(
        task.spec,
        pack=_load_pack(task.spec.deck_profile),
        keep_journal=task.keep_journal,
        capture_decisions=task.capture_decisions,
        participants_factory=task.participants_factory,
    )


def _context() -> multiprocessing.context.BaseContext:
    """优先 ``fork``：worker 直接继承父进程已 import 的模块，不为每批付一次 ``__main__`` 重导入。

    非 POSIX 平台退回解释器默认档；批量层不承诺跨平台的可复现性，只承诺同机同参数一致。
    """
    if "fork" in multiprocessing.get_all_start_methods():
        return multiprocessing.get_context("fork")
    return multiprocessing.get_context()


def _missing_seeds(tasks: Sequence[_Task], results: Sequence[GameResult]) -> list[int]:
    """还没拿到结果的种子：崩溃时点名它们，避免"半批被当全批"。

    不能问 ``future.done()``：池崩时执行器会把**仍在跑**的 future 一并标成 done 并挂上
    ``BrokenProcessPool``（实测 140 的任务被这样标掉），故以**已收进结果的 seed** 为准。
    """
    returned = {result.seed for result in results}
    return sorted(task.spec.seed for task in tasks if task.spec.seed not in returned)


def _crash_error(missing: list[int]) -> BatchWorkerError:
    return BatchWorkerError(
        f"worker 进程崩溃（不返回半批）；没有拿到结果的种子：{missing}"
    )


def run_parallel(
    specs: Sequence[GameSpec],
    *,
    deck_profile: str | None,
    keep_journal: bool,
    capture_decisions: bool,
    participants_factory: ParticipantsFactory | None,
    workers: int,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[GameResult]:
    """用 ``workers`` 个进程跑完 ``specs``；结果按**完成顺序**返回（排序归 ``run_games``）。"""
    _limit_threads()
    tasks = [
        _Task(
            spec=spec,
            keep_journal=keep_journal,
            capture_decisions=capture_decisions,
            participants_factory=participants_factory,
        )
        for spec in specs
    ]
    results: list[GameResult] = []
    futures: dict[Future[GameResult], int] = {}
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=_context(),
        initializer=_init_worker,
        initargs=(deck_profile,),
    ) as executor:
        try:
            for task in tasks:
                futures[executor.submit(_run_task, task)] = task.spec.seed
            for future in as_completed(futures):
                seed = futures[future]
                try:
                    result = future.result()
                except BrokenProcessPool as error:
                    raise _crash_error(_missing_seeds(tasks, results)) from error
                except Exception as error:  # 单局异常已在 run_one 内收成本局结果，这里是基础设施错
                    raise BatchWorkerError(
                        f"seed={seed} 的 worker 任务失败（不返回半批）："
                        f"{type(error).__name__}: {error}"
                    ) from error
                results.append(result)
                if on_progress is not None:
                    on_progress(len(results), len(tasks))
        except BrokenProcessPool as error:
            # 建池 / 派发阶段就崩（还没有任何结果可报）。
            raise _crash_error(_missing_seeds(tasks, results)) from error
    return results
