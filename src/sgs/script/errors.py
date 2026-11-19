"""剧本模式规则层的失败（M7 票 16）。

规则层**不静默**：算不出下一步时报错并带可读原因，不返回空集让调用方把"死路"当成
"没有可选节点"糊过去（票 16 的红-绿判据）。错误形状沿用 ``sgs.data`` 的
``LoadError(file, loc, expected, got, rule_id)`` 的精神：``rule_id`` 是给代码与测试认的
稳定标识，``loc`` 指向出错的位置（进度键 / ``layers[i]`` / ``/``），``reason`` 是给人看的句子。

两个具体错误分开，因为调用方的处置不同：

- :class:`DeadEndError` = **这轮走不下去了**（无出边且非出口、出边全被标签挡住、出口已关闭…）
  ——运行期报错终止本 run（票 03 §2 的死路防御），编辑期对应"入口到不了任何出口"的警告
  （票 24）；
- :class:`LayerCleared` = **本大节已打通**（出口节点已结算），是正常终局信号：
  下一步是 :func:`sgs.script.next_layer_index` 推进大节，不是"再选一个节点"。
"""

from __future__ import annotations

__all__ = ["DeadEndError", "LayerCleared", "ScriptError"]


class ScriptError(Exception):
    """规则层失败的基类：``rule_id`` + ``loc`` + ``reason``（可读原因）。

    ``loc`` 的三种形状：进度键 ``"l1/n3"``（节点级）、``"layers[2]"``（大节级）、
    ``"/"``（整份剧本级）。
    """

    def __init__(self, *, rule_id: str, loc: str, reason: str) -> None:
        self.rule_id = rule_id
        self.loc = loc
        self.reason = reason
        super().__init__(f"{loc}: {reason}（{rule_id}）")


class DeadEndError(ScriptError):
    """死路：按当前的进度与标签**算不出任何可到达节点**，且这不是正常的通关。

    "返回空元组"在这里是**错误答案**：空集与"这一步恰好只有零个候选"无法区分，调用方
    会静默软锁。运行期一律抛这个错误终止本 run（票 03 §2）。
    """


class LayerCleared(ScriptError):
    """本大节已打通（有出口节点已 ``completed``）：正常终局信号，不是死路。

    与 :class:`DeadEndError` 分开，是为了让调用方能把"通关 / 进下一节"与"卡死"走两条路；
    它同样**不是**空元组。
    """
