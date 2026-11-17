"""确定性探针：按牌名从牌堆取一张（框架票 03）——跨 ``PYTHONHASHSEED`` 的逐字节比对用。

- 名字不以 ``test_`` 开头，pytest 不收集它；
- 同一 seed 下连取三档牌名（【杀】/ 【杀】+【闪】/ 【桃】+【酒】），打印取到的 ``card_id``、
  取完之后的牌堆头部与手牌 —— 这三样合起来足以看出"抽签结果"与"牌堆顺序"有没有被进程级
  哈希翻动；
- ``tests/test_determinism.py`` 起两个子进程（不同 ``PYTHONHASHSEED``）比较 stdout 字节。

判据落在**候选排序**上：候选必须是**牌堆顺序**（``GameState.deck`` 是有序列表），
牌名集合只用于成员判定；若哪天有人改成"迭代牌名集合挑第一张"，set 的迭代序会随哈希种子
变化，这条探针当场红。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine

from support import SeatSpec, build_state, deck_excluding

__all__ = ["NAME_SETS", "probe_line"]

PACK = load_content_pack(deck_profile="standard_108")

NAME_SETS = (("杀",), ("杀", "闪"), ("桃", "酒"))
"""三档牌名集合：单名 / 两名 / 两名——都命中时走的是同一条抽签路径。"""


def probe_line(seed: int = 424242) -> str:
    """同一 seed 下连取三档，返回可比对的单行文本。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=seed)
    taken = []
    for names in NAME_SETS:
        card = engine.take_named_card_from_deck(None, names=set(names), to_seat=0)
        taken.append(None if card is None else card.card_id)
    return f"{taken} {engine.state.deck[:8]} {list(engine.state.player(0).hand)}"


def main() -> int:
    print(probe_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
