"""**帧标签袋**：挂在本次使用（或本次事件帧）上的不可变标志集合（票 08 / R4-Q1）。

分界是 `开发文档/03` §5「效果原语」写下的那条：**改一个值 ⇒ 修饰器；加一条禁令 / 标志 ⇒
帧标签**。最典型的取值是"不可响应"（:data:`Tag.UNRESPONSIVE`）——被标上的这次使用**跳过它的
响应窗口**；窗口该不该存在仍由牌的规则（行为表 ``response_window``）决定，标签只负责"这一次不响应"。

设计约束（票 08 §要做什么 4 / `spec.md` §机制面的统一约定 §3）：

- **值不可变**：``UseTags`` 是 frozen dataclass，``add_tag`` 换一个新的值进去，故
  ``use_push_resolve`` 的 ``dict(frame.data)`` **浅拷贝安全**；
- **迭代顺序确定**：内部一律用 ``tuple`` 追加，**不用** ``set`` / ``frozenset``
  （源码纪律 §3 明禁 `for` 迭代无序容器）；
- **两档**：**全场档**（``target=None``，标签作用于整次使用 / 整个帧）与**逐目标档**
  （带目标座位）两档；``has_tag`` 带目标时"全场档也算命中"。

两档**分住两个帧私有键**（R4-Q1 的绑定读法，code-review C3）：

- :data:`USE_TAGS_KEY`（``target_tags``）**登记**在 ``engine.steps._PER_TARGET_FRAME_KEYS``
  里，逐目标游标推进时被结构性清除；
- :data:`WHOLE_FIELD_TAGS_KEY`（``global_tags``）**刻意不登记**——全场档作用于整次使用的
  每一个目标，多目标【杀】推进游标时必须存活。

早先两档共用一个键，于是"推进游标"会连全场档一起清掉（票 08 登记的被迫读法）；
C3 按 R4-Q1 的原文把只登记逐目标档这条兑现成结构性事实。

帧私有数据不进记录流（``engine/journal.py`` 不记 ``frame.data``），故挂标签不改任何既有基准。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .stack import Frame

__all__ = [
    "EMPTY_TAGS",
    "USE_TAGS_KEY",
    "WHOLE_FIELD_TAGS_KEY",
    "Tag",
    "UseTags",
    "add_tag",
    "has_tag",
]


class Tag(str, Enum):
    """帧标签的**取值闭集**（M3 只许增）。

    ``UNRESPONSIVE`` 是票 08 的"不可响应"（【铁骑】）；``PREVENTED`` 与 ``SAVED`` 是既有两个
    帧私有标志的迁移落点（【寒冰剑】的"防止此伤害"、【贯石斧】的"依然造成伤害"）——迁进同一个
    袋子后，"标志挂在帧私有载荷上、不进记录流"这条事实只有一个落点。
    """

    UNRESPONSIVE = "UNRESPONSIVE"
    PREVENTED = "PREVENTED"
    SAVED = "SAVED"


@dataclass(frozen=True, slots=True)
class UseTags:
    """帧标签袋的值对象：**全场档** + **逐目标档**，两个 ``tuple`` 保序、可浅拷贝共享。"""

    global_tags: tuple[Tag, ...] = ()
    """全场档：作用于本次使用 / 本帧的每一个目标。"""

    target_tags: tuple[tuple[int, Tag], ...] = ()
    """逐目标档：``(目标座位, 标签)``，按加入顺序排列（一次多目标的使用里互不串味）。"""

    def with_tag(self, tag: Tag, *, target: int | None = None) -> "UseTags":
        """加入一个标签；已在袋里时原样返回（幂等，且不改变迭代顺序）。"""
        if target is None:
            if tag in self.global_tags:
                return self
            return replace(self, global_tags=(*self.global_tags, tag))
        if (target, tag) in self.target_tags:
            return self
        return replace(self, target_tags=(*self.target_tags, (target, tag)))

    def has(self, tag: Tag, *, target: int | None = None) -> bool:
        """袋里有没有这个标签；带 ``target`` 时**全场档也算命中**（对每个目标都成立）。"""
        if tag in self.global_tags:
            return True
        return target is not None and (target, tag) in self.target_tags


EMPTY_TAGS = UseTags()
"""空袋：没有标签时的缺省值（也是"无标签帧"的唯一读法）。"""

USE_TAGS_KEY = "use_tags"
"""帧私有槽名：**逐目标档**帧标签（``UseTags.target_tags``）在 ``frame.data`` 里的键。

它登记在 ``engine.steps._PER_TARGET_FRAME_KEYS`` 里，故逐目标游标推进时被结构性清除
（R4-Q1：只登记逐目标档，不靠手工 ``del``）。
"""

WHOLE_FIELD_TAGS_KEY = "use_whole_field_tags"
"""帧私有槽名：**全场档**帧标签（``UseTags.global_tags``）在 ``frame.data`` 里的键。

**刻意不登记**在 ``_PER_TARGET_FRAME_KEYS`` 里：全场档作用于整次使用的每一个目标，
多目标【杀】推进游标时它必须存活（R4-Q1 的"只登记逐目标档"，code-review C3）。
"""


def _read(frame: Frame, key: str) -> UseTags:
    """读出某个键上的半个袋子；没有 / 类型不对（构造局面里的裸帧）时退回空袋。"""
    value = frame.data.get(key)
    return value if isinstance(value, UseTags) else EMPTY_TAGS


def _tags_of(frame: Frame) -> UseTags:
    """把两个键上的半个袋子合成一个 :class:`UseTags`（读取侧的唯一入口）。"""
    return UseTags(
        global_tags=_read(frame, WHOLE_FIELD_TAGS_KEY).global_tags,
        target_tags=_read(frame, USE_TAGS_KEY).target_tags,
    )


def add_tag(frame: Frame, tag: Tag, *, target: int | None = None) -> None:
    """给 ``frame`` 打一个标签；``target`` 缺省 = 全场档，给出 = 逐目标档。

    两档分写两个键：``target`` 缺省写 :data:`WHOLE_FIELD_TAGS_KEY`（跨目标存活），
    给出目标写 :data:`USE_TAGS_KEY`（随游标推进清除）。
    """
    key = USE_TAGS_KEY if target is not None else WHOLE_FIELD_TAGS_KEY
    frame.data[key] = _read(frame, key).with_tag(tag, target=target)


def has_tag(frame: Frame, tag: Tag, *, target: int | None = None) -> bool:
    """``frame`` 上有没有这个标签；带 ``target`` 时把全场档也算命中。"""
    return _tags_of(frame).has(tag, target=target)
