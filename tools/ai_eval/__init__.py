"""评估基础设施（票 13；spec §2.1–§2.9 / §7.2）。

**不进 ``src/``**：评估是仓库工具，不是发行包的一部分（与 ``tools/sgs_batch`` 同层；
它要跑进程池、读 git、起子进程，``src/sgs`` 的源码纪律禁这三样）。用法::

    PYTHONPATH=tools .venv/bin/python -m ai_eval quick --baseline ai-v0 --out runs/q.json

``PYTHONPATH=tools`` 是**必须**的：``.venv`` 是 editable 只指 ``src/``，漏写会
``No module named ai_eval``——响亮失败，不是静默打错版本（spec §2.9）。

公开面分四组：

- **判决批**：:func:`derive_batch` / :func:`derive_sealed_batch` /
  :func:`identity_quota` / :func:`seat_zero_identity` / :func:`is_reserved_seed`
  （spec §2.7）；
- **统计纯函数**：:func:`paired_diff_ci` / :func:`noise_floor` / :func:`by_identity`
  （spec §2.3 / §2.5）；
- **漏动作口径**：:class:`DecisionFact` / :func:`count_misses` / :func:`fact_from_query`
  （spec §2.11；票 15 的 ``live`` 同源复用）；
- **三档命令**：:func:`run_quick` / :func:`run_promote` / :func:`run_seal` + :func:`main`
  （spec §2.8 / §2.9）与版本加载器 :func:`load_version`（spec §2.6）。
"""

from .cases import CasesResult, run_cases
from .cli import main
from .decisions import Divergence, decision_diff, diff_decisions
from .errors import AiEvalError, AiEvalRunError, AiEvalSpecError, AiVersionError
from .misses import DecisionFact, MissCounts, count_misses, fact_from_query
from .seeds import (
    SeedBatch,
    derive_batch,
    derive_sealed_batch,
    identity_quota,
    is_reserved_seed,
    seat_zero_identity,
)
from .run import run_promote, run_quick, run_seal
from .stats import PairedComparison, by_identity, compare_pairs, noise_floor, paired_diff_ci
from .versions import LoadedVersion, load_version

__all__ = (
    "AiEvalError",
    "AiEvalRunError",
    "AiEvalSpecError",
    "AiVersionError",
    "CasesResult",
    "Divergence",
    "DecisionFact",
    "LoadedVersion",
    "MissCounts",
    "PairedComparison",
    "SeedBatch",
    "by_identity",
    "compare_pairs",
    "count_misses",
    "decision_diff",
    "diff_decisions",
    "derive_batch",
    "derive_sealed_batch",
    "fact_from_query",
    "identity_quota",
    "is_reserved_seed",
    "main",
    "load_version",
    "noise_floor",
    "paired_diff_ci",
    "run_cases",
    "run_promote",
    "run_quick",
    "run_seal",
    "seat_zero_identity",
)
