"""确定性探针：用**库 API** 跑同一 seed 的一局并写出 canonical journal（票 10 §4 / 票 26）。

落点 = 库门面（主缝）：``new_game`` + 两个 ``RandomPlayer`` 的整局记录流。

- 名字不以 ``test_`` 开头，pytest 不收集它；
- **不**动产品 CLI（票 02：M1 参数只要 ``--seed``）：跨 ``PYTHONHASHSEED`` 的逐字节比对由
  ``tests/test_determinism.py`` 起两个子进程调用本脚本完成；
- 驱动复用 ``tests/support`` 的 :func:`play_standard`，与 CLI 默认对局同源
  （``Engine.new_game`` + 两个 ``RandomPlayer`` + ``ai`` 独立子流），
  故失败消息里的 ``python -m sgs --seed S`` 能原样复现同一局。

用法::

    python tests/journal_probe.py --seed 7 --out /tmp/journal.jsonl
    python tests/journal_probe.py --seed 7 --ai heuristic --out /tmp/h.jsonl
"""

from __future__ import annotations

import argparse
from pathlib import Path

from support import play_standard

__all__ = ["journal_for_seed", "main"]


def journal_for_seed(seed: int, *, ai: str = "random") -> str:
    """库 API 跑完整局并返回 canonical JSONL（唯一序列化器的入口）。"""
    return play_standard(seed, ai=ai).journal()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出一局的 canonical journal（确定性探针）。")
    parser.add_argument("--seed", type=int, required=True, help="对局种子。")
    parser.add_argument("--out", type=Path, required=True, help="journal 落盘路径。")
    parser.add_argument(
        "--ai",
        choices=("random", "heuristic"),
        default="random",
        help="参与者档位（票 13）；缺省 random，与 M2 探针口径一致。",
    )
    args = parser.parse_args(argv)
    # newline="" 关掉平台换行翻译：canonical 形态写死为 LF（票 10 §5）。
    args.out.write_text(journal_for_seed(args.seed, ai=args.ai), encoding="utf-8", newline="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
