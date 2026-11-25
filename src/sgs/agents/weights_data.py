"""拟合出来的整数权重（`ai-view` 票 08b）：**生成文件，不要手改**。

由 ``tools/sgs_train/fit.py`` 的 ``fit`` 子命令整块重写；每行 = ``(键, round(w × 1000))``。

空表 = 还没有落仓的拟合数据：每个键读 ``anchor``（手写值），决策逐 float 等于 08a 的落地态。
第一次全量拟合（λ 扫描）没有候选同时满足「``test_ai_cases`` 80/80」与「决策 diff 有界」，
按预注册停手回票——读数与下一步见 `.scratch/ai-view/issues/08b-offline-fit-and-tuning.md`。
"""

from __future__ import annotations

__all__ = [
    "FIT_LAMBDA",
    "FIT_SAMPLES",
    "IDENTITY_AC_ROWS",
    "SHARED_B_ROWS",
]

FIT_SAMPLES = 0
FIT_LAMBDA = 0.0
SHARED_B_ROWS: tuple[tuple[str, int], ...] = ()
IDENTITY_AC_ROWS: tuple[tuple[str, int], ...] = ()
