#!/usr/bin/env python3
"""批量跑对局的薄启动器（票 11；spec §1.2）。

``python tools/batch_games.py`` 时 ``sys.path[0]`` 就是 ``tools/``，故 ``sgs_batch`` 无需
安装即可 import；库入口在 ``tools/sgs_batch/``，本文件只转发参数与退出码。

例::

    .venv/bin/python tools/batch_games.py --seeds 136..143 --mode identity --players 5 \
        --out .scratch/ai-light/results/batch.json --decisions-out .scratch/ai-light/results/actions.jsonl
"""

from sgs_batch.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
