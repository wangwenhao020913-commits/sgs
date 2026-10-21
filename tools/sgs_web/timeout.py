"""超时兜底：**纯函数**，只读询问（ADR-012 / 地图 D9）。

超时是**接入层**的事实，不是对局事实：它不进**记录流**，只进会话的侧车日志（D10）。
因此这里的代答必须确定性——同一个询问调两次得到同一个动作，不取随机、不读时钟，
"同种子同输入 → 同输出"这条保证因此不受超时影响。

优先级：**遗计"交给谁"特判** → ``Pass`` → ``EndPhase`` → ``legal_actions[0]``。

- 【遗计】的"交给谁"步（``SELECT_OPTION``，``skill_id`` 前缀 ``yiji:give:``）落点是
  **把剩下的牌交给自己**（spec D2 / D9.1）：``seat:{query.actor}``。它不看候选顺序——
  候选的第一条通常是 ``seat:0``，不一定是持有者；也不给任何取消出口（该步不可放弃）。
- 响应类询问（``DODGE`` / ``RESPOND`` / ``PEACH``）通常带 ``Pass``，落点是"不响应"；
- 出牌阶段带 ``EndPhase``，落点是"结束阶段"；
- 弃牌 / 选牌类两样都没有，只能退到候选里的第一个（**那是合法动作**，只是不聪明）。
  【遗计】的**选牌**步就是这一档：候选按面内位置枚举，第一条选第一张亮牌（票 10 实测）。
"""

from __future__ import annotations

from sgs.state import Action, EndPhase, Pass, Query, SelectOption

__all__ = [
    "fallback_action",
    "FALLBACK_ORDER",
    "YIJI_GIVE_PREFIX",
    "yiji_give_self_choice",
]


FALLBACK_ORDER: tuple[type[Action], ...] = (Pass, EndPhase)
"""兜底动作的优先级（按构造子类型）。两者都没有时取候选里的第一个。"""

YIJI_GIVE_PREFIX = "yiji:give:"
"""【遗计】"交给谁"步的询问 ``skill_id`` 前缀（执行器发 ``yiji:give:<点号>:<轮号>``）。

超时兜底唯一一处按询问标识特判的地方：该步不可放弃、也不该"随便挑第一个座位"，
确定性的落点是**交给自己**（spec D9.1）。前缀是稳定契约（选牌步的 ``skill_id`` 为
``None``，给牌步的缓存键必须带点号 / 轮号，见 ``skills/damage_after.py::_yiji``）。
"""


def yiji_give_self_choice(query: Query) -> SelectOption | None:
    """【遗计】"交给谁"步 → 候选里那条 ``seat:{actor}``；不是该步 / 候选里没有则 ``None``。

    代答（:func:`fallback_action`）与倒计时文案（``display.prompt_display``）共用这一条
    谓词：两处各写一遍时，"文案说交给自己、代答却选了座位 0" 这种漂移不会被测试抓住。
    """
    if getattr(query, "kind", None) != "SELECT_OPTION":
        return None
    skill_id = getattr(query, "skill_id", None)
    if not isinstance(skill_id, str) or not skill_id.startswith(YIJI_GIVE_PREFIX):
        return None
    wanted = f"seat:{query.actor}"
    for candidate in query.legal_actions:
        if isinstance(candidate, SelectOption) and candidate.choice == wanted:
            return candidate
    return None


def fallback_action(query: Query) -> Action:
    """给一个询问选一个**确定性**的代答动作。

    询问没有候选是引擎不该出现的状态（参与者无从应答），故直接抛错而不是返回 ``None``：
    ``None`` 会被 ``Engine.submit`` 当成结构损坏，报出来的错会指向错误的地方。
    """
    if not query.legal_actions:
        raise ValueError(f"{query.kind} 询问没有候选行动，超时兜底无从代答")

    give_self = yiji_give_self_choice(query)
    if give_self is not None:
        return give_self

    for constructor in FALLBACK_ORDER:
        for candidate in query.legal_actions:
            if isinstance(candidate, constructor):
                return candidate

    return query.legal_actions[0]
