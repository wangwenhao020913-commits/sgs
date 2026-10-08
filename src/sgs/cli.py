"""命令行门面。

M1 的参数只有 ``--seed``；省略时现生成、写进记录首行并打印（见票 08）。
本票负责解析参数、加载内容包，并在坏数据时给出退出码 1；跑通一局由后续票据接入。
"""

from __future__ import annotations

import argparse
import sys

from sgs.data import DataLoadError, load_content_pack

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """解析命令行参数、加载内容包并返回进程退出码。"""
    parser = argparse.ArgumentParser(
        prog="sgs",
        description="跑一局《三国杀》（M1 引擎骨架）并打印事件流。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="随机种子；省略时现生成一个 64 位种子。",
    )
    parser.parse_args(argv)

    try:
        load_content_pack()
    except DataLoadError as error:
        print(error.report(), file=sys.stderr)
        return 1
    return 0
