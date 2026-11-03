"""**武将牌堆的占用面**（票 05；票 02 §4.5 E2）：哪些真名此刻不在可抽集合里。

规则原文：`三国杀详细规则.txt` §15.3 L1535–L1542（武将牌"移出本局"与"未加入游戏的武将牌堆"）。

"谁占着武将牌" = **两半的并集**：

- **被抽走**（:attr:`~sgs.state.GameState.drawn_true_names`）：存下来的那一半，写入唯一
  入口是 :meth:`~sgs.engine.Engine.note_drawn_true_name`（票 07 的 ``draw_hero_cards``
  抽到即调），解除走 :meth:`~sgs.engine.Engine.release_drawn_true_name`；
- **在场**：从座位**派生**——座位上写着这个真名，且该座位 ``alive`` 或还在**死亡结算
  窗口**里（:attr:`~sgs.state.Player.death_time_skills_active`）。

"在场"不存进状态是刻意的（票 02 §2.5）：存它就要在死亡流程里加写点，而派生能得到同样的
效果；:attr:`~sgs.state.Player.hero` 在阵亡后仍在（座位不销毁），故"只判 ``hero is not
None``"会让阵亡永不解除，与用户 2026-11-03 第三轮 Q18 的裁定冲突。判据里带上死亡结算
窗口是**第四轮第 3 条**的裁定：真名在死亡结算**收尾**（``death_time_skills_active`` 关窗）
之后才放回武将牌堆，而不是在死亡入口（``alive = False``）就放回。

真名一律经数据层取（:func:`sgs.data.hero_by_id` 的 :attr:`~sgs.data.Hero.true_name`）：
本模块**不**另建真名表、也**不**重实现 ``enabled_hero_ids`` / ``Hero.true_name`` /
``hero_by_id``——数据面不缺这三样（票 02 §4.5 "不新增实现"）。
"""

from __future__ import annotations

from sgs.data import hero_by_id
from sgs.state import GameState

__all__ = ["blocked_true_names"]


def blocked_true_names(state: GameState) -> frozenset[str]:
    """此刻**已被占用**的武将牌真名 = 被抽走的那一半 ∪ 在场的那一半。

    读取方是武将牌抽取（票 07）：可抽真名 = 数据面的真名投影 − 本函数。返回**集合**，
    成员判定是它的唯一用途——**不要迭代它**（``frozenset`` 的迭代序逐进程变化，
    ``tests/test_source_discipline.py`` §3）；需要顺序时先 ``sorted(...)``。

    空集合 = 无武将模式且没抽过牌。这就是"无初始化点"（E5）的形状：本函数不从任何配置
    或武将表**写**状态，故引入它之前的所有对局逐字节不变。

    "在场"含**死亡结算窗口**（``alive or death_time_skills_active``）：阵亡者本人的技能在
    那段结算里仍可解析，武将牌同理仍算被占用；窗口关闭（结算收尾）后真名才回到可抽集合。

    未知武将 id（不在数据表里）的座位**不贡献**真名：与 :func:`sgs.skills.resolve_instances`
    对未知 id 的静默同姿态；正常开局入口 ``build_initial`` 已在发将时 fail-closed。
    """
    names = set(state.drawn_true_names)
    for player in state.players:
        if player.hero is None:
            continue
        if not (player.alive or player.death_time_skills_active):
            continue
        hero = hero_by_id(player.hero, pack_id=state.pack_id)
        if hero is not None:
            names.add(hero.true_name)
    return frozenset(names)
