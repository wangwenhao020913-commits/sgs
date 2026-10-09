"""调用型效果原语的注册表（票 09 §4）。

``CallOp.op`` 的取值 = 本注册表里的 **Python 函数名**：模块级、普通回调，签名统一为
``(host, frame) -> None``，引擎只做通用派发（撞到 ``CallOp`` 就查表调用），``engine/``
里永不出现具体效果名。与票 07 的装备技能 ``CATALOG`` 同一姿态；不建参数模式 DSL，
``scalars`` 只装数字字面量。

M2 的调用型效果随机制落地；票 26 先填**两张延时锦囊**的四个回调：

- ``resolve_indulgence`` / ``resolve_lightning``：延时锦囊 L5「生效后」压出 **JUDGE 帧**；
- ``after_indulgence`` / ``after_lightning``：JUDGE 帧的 ``judge_finish`` 在判定牌入弃牌堆
  之后回调的**后果 op**（读帧私有 ``judge_card`` 拿结果，不写回外层帧）。

其余调用型效果（无中生有 / 过河拆桥 / 顺手牵羊 / 五谷丰登 / 借刀杀人 / 决斗）由票 27 填。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sgs.data import Suit
from sgs.resolution import Frame
from sgs.state import FrameKind
from sgs.timing import Timing

from .behaviors import DamageNature

__all__ = ("OPS",)

LIGHTNING_DAMAGE = 3
"""【闪电】命中时的雷电伤害值（官方 `A/card_scroll.txt:217`）。"""

LIGHTNING_RANK_MIN = 2
LIGHTNING_RANK_MAX = 9
"""【闪电】命中的点数区间：♠2~9。"""


def _judge_payload(frame: Frame, *, subject: int, after: str) -> dict[str, Any]:
    """JUDGE 帧的私有载荷（票 10 §1）：``subject`` 与 ``judge_card`` **与渠道牌分列**。

    ``card`` 是从外层 RESOLVE 帧继承的渠道牌（延时锦囊本体），``judge_card`` 由
    ``judge_on`` 步骤填入；``after`` 是判定后果 op 的注册名。
    """
    return {
        "subject": subject,
        "targets": (subject,),
        "source": frame.data.get("source"),
        "card": frame.data.get("card"),
        "judge_card": None,
        "use_timing": frame.data.get("use_timing"),
        "after": after,
    }


def _open_judgment(host: Any, frame: Frame, *, after: str) -> None:
    """压出判定帧：被判定者 = 本层 RESOLVE 帧的当前目标，后果 op 按名登记。"""
    subject = frame.data["targets"][0]
    host.push_frame(
        FrameKind.JUDGE,
        _judge_payload(frame, subject=subject, after=after),
        timing=Timing.JUDGE_ON,
    )


def resolve_indulgence(host: Any, frame: Frame) -> None:
    """【乐不思蜀】生效后：压 JUDGE 帧，后果 op 为 :func:`after_indulgence`。"""
    _open_judgment(host, frame, after=after_indulgence.__name__)


def resolve_lightning(host: Any, frame: Frame) -> None:
    """【闪电】生效后：压 JUDGE 帧，后果 op 为 :func:`after_lightning`。"""
    _open_judgment(host, frame, after=after_lightning.__name__)


def after_indulgence(host: Any, frame: Frame) -> None:
    """【乐不思蜀】判定后果：非红桃 ⇒ 被判定者**跳过出牌阶段**（不搬牌）。"""
    if Suit(frame.data["judge_card"].suit) is not Suit.HEART:
        host.skip_play_phase(frame.data["subject"])


def after_lightning(host: Any, frame: Frame) -> None:
    """【闪电】判定后果：♠2~9 ⇒ 3 点**无来源**雷电伤害，本牌随后入弃牌堆；否则不动。

    "随后入弃牌堆"落在**判定阶段循环的统一收尾**（伤害帧是 JUDGE 帧的子帧，先结算完），
    故这里只把本牌标记为"已消耗"；其余情形（非 ♠2~9、以及被无懈的路径）由同一收尾
    顺延到下一个合法目标的判定区（票 11 §4）。
    """
    judge_card = frame.data["judge_card"]
    if Suit(judge_card.suit) is not Suit.SPADE:
        return
    if not LIGHTNING_RANK_MIN <= judge_card.rank <= LIGHTNING_RANK_MAX:
        return
    subject = frame.data["subject"]
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": None,
            "targets": (subject,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": subject,
            "channel": frame.data.get("card"),
            "nature": DamageNature.THUNDER,
            "chain": False,
            "amount": LIGHTNING_DAMAGE,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    host.consume_delayed_card(frame)


OPS: dict[str, Callable[[Any, Frame], None]] = {
    resolve_indulgence.__name__: resolve_indulgence,
    resolve_lightning.__name__: resolve_lightning,
    after_indulgence.__name__: after_indulgence,
    after_lightning.__name__: after_lightning,
}
"""操作名 → Python 回调；加载器按名做成员校验，``apply_effects`` 按名通用派发。"""
