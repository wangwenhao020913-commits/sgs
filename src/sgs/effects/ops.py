"""调用型效果原语的注册表（票 09 §4）。

``CallOp.op`` 的取值 = 本注册表里的 **Python 函数名**：模块级、普通回调，
引擎只做通用派发（撞到 ``CallOp`` 就查表调用），``engine/`` 里永不出现具体效果名。
与票 07 的装备技能 ``CATALOG`` 同一姿态；不建参数模式 DSL，``scalars`` 只装数字字面量。

M2 的调用型效果（无中生有 / 过河拆桥 / 顺手牵羊 / 五谷丰登 / 借刀杀人 / 决斗、
两张延时锦囊的判定后果）由票 26 与票 27 随各自机制落地本表；本票先立起注册表本身，
使加载期的"操作引用存在"校验（``card_type.unknown_op``）有唯一判据可依。
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ("OPS",)

OPS: dict[str, Callable[..., object]] = {}
"""操作名 → Python 回调；值在票 26 / 票 27 填入，加载器按名做成员校验。"""
