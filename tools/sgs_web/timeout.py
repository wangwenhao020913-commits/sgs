"""超时兜底：**纯函数**，只读询问（ADR-012 / 地图 D9）。

超时是**接入层**的事实，不是对局事实：它不进**记录流**，只进会话的侧车日志（D10）。
因此这里的代答必须确定性——同一个询问调两次得到同一个动作，不取随机、不读时钟，
"同种子同输入 → 同输出"这条保证因此不受超时影响。

优先级：``Pass`` → ``EndPhase`` → ``legal_actions[0]``。

- 响应类询问（``DODGE`` / ``RESPOND`` / ``PEACH``）通常带 ``Pass``，落点是"不响应"；
- 出牌阶段带 ``EndPhase``，落点是"结束阶段"；
- 弃牌 / 选牌类两样都没有，只能退到候选里的第一个（**那是合法动作**，只是不聪明）。
"""

from __future__ import annotations

from sgs.state import Action, EndPhase, Pass, Query

__all__ = ["fallback_action", "FALLBACK_ORDER"]


FALLBACK_ORDER: tuple[type[Action], ...] = (Pass, EndPhase)
"""兜底动作的优先级（按构造子类型）。两者都没有时取候选里的第一个。"""


def fallback_action(query: Query) -> Action:
    """给一个询问选一个**确定性**的代答动作。

    询问没有候选是引擎不该出现的状态（参与者无从应答），故直接抛错而不是返回 ``None``：
    ``None`` 会被 ``Engine.submit`` 当成结构损坏，报出来的错会指向错误的地方。
    """
    if not query.legal_actions:
        raise ValueError(f"{query.kind} 询问没有候选行动，超时兜底无从代答")

    for constructor in FALLBACK_ORDER:
        for candidate in query.legal_actions:
            if isinstance(candidate, constructor):
                return candidate

    return query.legal_actions[0]
