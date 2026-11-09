r"""主公技族（F，票 09）：势力作用域、**势力求助窗口**与回复量修饰。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` / :mod:`sgs.skills.response_window` 同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，
:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

**"主公技机制"不新造**（票 09 §要做什么 / R4-Q2）：它与武将技能同构，只差票 01 落在
:func:`~sgs.skills.catalog.resolve_instances` 的**身份门控**（非主公 ⇒ 武将牌上根本没有这个技能）。
五条技能（含界【护驾】的两半）的落点：

=======================  ======  ==========================================================
技能                       锁定     落点
=======================  ======  ==========================================================
曹操【护驾】               非锁定    ``CANDIDATES(DODGE)`` → 势力求助窗口（需要【闪】，使用 / 打出
                                    两档都住这一扇窗口）
界曹操【护驾】①            非锁定    同上（同一条 ``LordSkill.offer`` 声明）
界曹操【护驾】②            非锁定    ``USE_ON_USE`` / ``PLAY_ON_PLAY`` 钩子：其他魏势力角色于其
                                    回合外使用 / 打出【闪】时，**其**可以令主公摸一张牌
刘备【激将】               非锁定    ``CANDIDATES(RESPOND)`` → 势力求助窗口（需要【杀】；打出 =
                                    南蛮 / 决斗 / 万箭，使用 = 借刀 / 乱武的被迫使用）+ 出牌
                                    阶段主动使用【杀】的入口（票 20 的声明级流水线）
孙权【救援】               锁定      ``Modifier(RECOVER_AMOUNT, …)``：其他吴势力角色对其用【桃】时 +1
=======================  ======  ==========================================================

**势力作用域不建字段**（票 09 §要做什么 1）："其他同势力角色"由 :func:`~sgs.skills.same_kingdom`
比座位表达；求助窗口的应答者 = 从当前回合角色起逆时针、每人一次、非本势力者不收到询问。

**求助窗口是短路式，不是收集式**（票 09 §要做什么 3 / R3-Q1）：与无懈的"收齐后取最高优先级"
（``_collected_response_window``）**刻意不同**——首个打出者即满足需要，后面的同势力角色
根本不收到询问。应答复用 :meth:`sgs.engine.Engine.ask_respond`。

**代打是真打出**（票 09 §要做什么 4）：走 L6 的 ``PLAY_DECLARE`` / ``PLAY_ON_PLAY``
（:meth:`sgs.engine.Engine.play_card`）、消耗响应者的实体牌、**不占响应者的使用次数**。

引擎侧只做**通用提供者派发**（:func:`dispatch_provider`）：引擎不认识任何具体主公技 id，
只把窗口里被选中的 ``ActivateSkill`` 交给本模块的注册表；【八卦阵】那类"选中才惰性判定"的
提供者仍走引擎既有路径（本注册表无它的条目时由引擎回退）。

注册表**不只服务主公技**（M6 票 01）：移动版于吉【蛊惑】用同一个口子实现"需要牌时先扣置
再质疑"，由 :func:`register_provider` 在加载期并入（调用点 = `catalog.py` 末尾，那里两个
模块都已 import）。引擎侧的分派代码因此一个字都不用改。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.data import Kingdom, SkillType, Suit, hero_skill_id
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    check_targets,
    check_usable,
    satisfies_required,
)
from sgs.resolution import Control, Frame, Tag, has_tag
from sgs.state import (
    ActivateSkill,
    CardRef,
    FrameKind,
    GameState,
    PlayCard,
    RespondMode,
    WindowCause,
    seats_from,
)
from sgs.timing import Timing

from .catalog import (
    ActivationPlan,
    Hook,
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    HeroSkillDecl,
    SkillDef,
    mark_skill_used_this_turn,
    same_kingdom,
    skill_used_this_turn,
)
from .flow import PickSeats, SkillFlow, run_flow
from .virtual import pure_virtual_card

__all__ = [
    "BAONUE",
    "BAONUE_FLOW",
    "HUJIA",
    "JIE_HUJIA",
    "JIE_HUJIA_ECHO",
    "JIE_HUJIA_LORD",
    "JIE_JIJIANG",
    "JIE_JIJIANG_ECHO",
    "JIE_JIJIANG_LORD",
    "JIJIANG",
    "JIJIANG_USE_FLOW",
    "JIE_JIUYUAN",
    "JIE_JIUYUAN_FLOW",
    "JIE_JIUYUAN_REPLACED_KEY",
    "JIUYUAN",
    "LORD_REQUEST_KEY",
    "LORD_REQUEST_SKILLS",
    "PROVIDERS",
    "PROVIDER_ACTOR_KEY",
    "PROVIDER_CARD_KEY",
    "PROVIDER_REQUIRED_KEY",
    "PROVIDER_SATISFIED_KEY",
    "PROVIDER_SKILL_KEY",
    "LORD_ECHO_FLOW",
    "LordEcho",
    "LordSkill",
    "LORD_REQUEST_DELIVER_KEY",
    "USE_OR_PLAY_MODES",
    "advance_provider",
    "dispatch_provider",
    "jie_hujia_activation_plan",
    "jijiang_use_activate",
    "lord_echo_activation_plan",
    "lord_request_activation_plans",
    "lord_request_skill_definitions",
    "lord_skill_definitions",
    "provider_deliver",
    "provider_give_up",
    "provider_registered",
    "provider_required",
    "register_provider",
    "start_provider",
]

PROVIDER_SKILL_KEY = "provider_skill"
"""帧私有键：当前正在展开的**技能提供者** id（``None`` = 没有）。

引擎的响应窗口在收到 ``ActivateSkill`` 之后把 id 记在这里；提供者需要跨多次重入
（逐个问同势力角色、等人打出）时，引擎据此**重入同一个提供者**而不是重发外层询问
（``ask`` 的应答是**一次性**的，重发会串味）。
"""

PROVIDER_ACTOR_KEY = "provider_actor"
"""帧私有键：发起这次提供者展开的座位（需要者自己）。"""

PROVIDER_SATISFIED_KEY = "provider_satisfied"
"""帧私有键：提供者收尾时写回的结果——需要是否被满足（``True`` / ``False``）。"""

PROVIDER_REQUIRED_KEY = "provider_required"
"""帧私有键：**这扇窗口要一张什么牌**（牌名 ``dodge`` / ``peach`` / ``nullify``，或杀族请求
``slash``）。由 :func:`start_provider` 的 ``required`` 写入。

提供者据此收窄声明范围（【蛊惑】只列能当它的牌名），**不从外层帧的牌反推**：濒死求【桃】
的帧上没有牌、无懈窗口的帧上是被无懈的那张锦囊、借刀的帧上是【借刀杀人】本身——三处反推
都会得到"需要的不是这张"。
"""

PROVIDER_CARD_KEY = "provider_card"
"""帧私有键：提供者这次**交出的产物牌**（虚拟牌；没有产物 = 没写这个键）。

提供者的契约是"答完就已经把牌付清了"：主公技（【护驾】/【激将】）的产物是**别人**打出的
实体牌、由技能自己走 L6，故不写这个键；【蛊惑】的产物是它扣置的牌当成的虚拟牌，**交付给
窗口**——六扇窗口对它的消费各不相同（【闪】= 使用、南蛮 / 万箭 / 决斗 = 打出、求桃 =
``recover_from_peach``、借刀 = 对 Bn 使用、无懈 = 先声明后由赢家使用），故"怎么消费"只有
窗口自己知道。技能因此不必认识窗口形状。

``satisfied = True`` 且**没有**产物，读法是"需要已被别人付清，窗口没有牌可交付"（主公技式）。
六扇窗口对它的处理因此分两类，这是**窗口自己的事实**、不是两种契约：

- 【闪】/ 南蛮 / 万箭 / 决斗：需要已经满足 ⇒ 记一次成功、不压帧（``_accept(None)`` /
  ``_play_produced(None)``）。今天唯一的这种提供者是【护驾】/【激将】，它们只出现在这里；
- 求桃 / 借刀 / 无懈：这三扇的"需要"就是**窗口要交付的那张牌**（回复、打向 Bn 的【杀】、
  一条无懈的声明），没有产物 ⇒ 这次响应没发生（求桃按放弃记、借刀落回真牌窗口、无懈
  不记声明）。今天没有能走到这三扇、又交不出产物的提供者（登记表里只有【蛊惑】）。
"""

LORD_REQUEST_KEY = "lord_request"
"""帧私有键：**势力求助窗口**的进度（``askers`` / ``next`` / ``done`` / ``satisfied``）。

住帧私有 ``data``（不进记录流）：请求的应答者区间在开窗时**定格**（与濒死求桃同姿态），
``next`` 记"问到第几个"、``done`` 记"是否已收尾"。
"""


# ---------------------------------------------------------------------------
# 势力作用域：求助窗口的应答者区间
# ---------------------------------------------------------------------------


def _request_askers(state: GameState, owner: int) -> tuple[int, ...]:
    """求助窗口的应答者：**其他同势力角色**，从当前回合角色起逆时针、每人一次。

    判据 = ``seat != owner`` ∧ :func:`~sgs.skills.same_kingdom`（票 09 §要做什么 3）。
    区间读法与濒死求桃 / 改判队列同源（:func:`~sgs.state.seats_from`），故"逆时针 = 座位号 +1"
    只有一处真源；死者不在区间里（``seats_from`` 只留存活者）。
    """
    return tuple(
        seat
        for seat in seats_from(state, state.turn_seat, len(state.players))
        if seat != owner and same_kingdom(state, owner, seat)
    )


# ---------------------------------------------------------------------------
# 通用提供者派发（引擎侧的唯一入口）
# ---------------------------------------------------------------------------


ProviderHandler = Callable[[Any, Frame, int, Timing], Control]
"""提供者回调：``(host, frame, actor, timing) -> Control``，与钩子 / ``OPS`` 同形。

``actor`` = 发起这次提供者展开的座位（需要者），``timing`` = 外层窗口所在的时机
（求助询问的记录语境）。返回 ``"stay"`` = 还没跑完（已挂起或已压子帧）；``None`` = 已收尾，
结果写在 :data:`PROVIDER_SATISFIED_KEY`（产物牌若归窗口消费，另写 :data:`PROVIDER_CARD_KEY`）。

"这扇窗口要一张什么牌"读 :data:`PROVIDER_REQUIRED_KEY`（由 :func:`start_provider` 写好），
**不由回调的签名携带**：它是窗口的事实，主公技把它声明在技能定义上、【蛊惑】按它现算声明范围。
"""


def dispatch_provider(
    host: Any, frame: Frame, skill_id: str, actor: int, timing: Timing
) -> Control:
    """把一个被选中的技能提供者交给技能层**通用派发**（引擎里不出现具体技能 id）。

    未登记 ⇒ fail-closed（运行期撞到即编程错误，不静默跳过）；引擎对未登记的
    ``ActivateSkill`` 保留既有【八卦阵】路径（见模块 docstring）。
    """
    handler = PROVIDERS.get(skill_id)
    if handler is None:
        raise NotImplementedError(f"技能 {skill_id!r} 没有登记提供者回调")
    return handler(host, frame, actor, timing)


def provider_registered(skill_id: str) -> bool:
    """``skill_id`` 有没有登记提供者回调（引擎据此在"求助"与"判定"两条路之间分派）。"""
    return skill_id in PROVIDERS


def _finish_provider(frame: Frame) -> tuple[Control, bool, CardRef | None]:
    """收尾：取走通用结果键，返回引擎要的三元组（见 :func:`start_provider`）。"""
    frame.data.pop(PROVIDER_REQUIRED_KEY, None)
    frame.data.pop(LORD_REQUEST_KEY, None)
    satisfied = bool(frame.data.pop(PROVIDER_SATISFIED_KEY, False))
    card = frame.data.pop(PROVIDER_CARD_KEY, None)
    return None, satisfied, card


# ---------------------------------------------------------------------------
# 提供者回调的**公共件**（票 16 §2.4）：受理面读 required + 产物交接
#
# 这三件是"提供者只声明我在哪些窗口参与，共用一份实现"的**受理半边**（候选半边见
# `skills/catalog.py` 的 `ProviderWindows`）：技能回调不再各自 `frame.data[...] = ...`
# 地手抄结果键，也不再各自写一份"required 缺失 = 编程错误"。
# ---------------------------------------------------------------------------


def provider_required(frame: Frame) -> str:
    """提供者回调读**这扇窗口要哪张牌**（``PROVIDER_REQUIRED_KEY``）。

    缺失 ⇒ ``NotImplementedError``：引擎在派发提供者之前一定写好它（见
    :func:`start_provider`），撞到即编程错误——不猜、不回退。
    """
    required = frame.data.get(PROVIDER_REQUIRED_KEY)
    if required is None:
        raise NotImplementedError(
            "提供者缺少窗口的 required（引擎应在派发前写 PROVIDER_REQUIRED_KEY）"
        )
    return str(required)


def provider_deliver(frame: Frame, card: CardRef) -> None:
    """产物交接：**这次响应成立**，产物牌交给窗口。

    写 ``satisfied=True`` + :data:`PROVIDER_CARD_KEY`；窗口自己决定怎么消费（使用 / 打出 /
    回复 / 打向 Bn / 无懈的"先声明、赢家才使用"）。
    """
    frame.data[PROVIDER_SATISFIED_KEY] = True
    frame.data[PROVIDER_CARD_KEY] = card


def provider_give_up(frame: Frame) -> None:
    """产物交接：**这次响应没发生**（``satisfied=False``，不写产物，牌一张不动）。

    外层窗口拿到 ``False`` 后照常继续（问真牌 / 记放弃）——这就是"作废不需要受理后回滚"
    的那条契约（轮询发生在受理之前）。
    """
    frame.data[PROVIDER_SATISFIED_KEY] = False


def start_provider(
    host: Any,
    frame: Frame,
    skill_id: str,
    actor: int,
    timing: Timing,
    *,
    required: str,
) -> tuple[Control, bool | None, CardRef | None]:
    """**开始**一次提供者展开：记进度键 → 派发一次。

    返回 ``(control, satisfied, card)``：

    - ``control`` 非 ``None`` ⇒ 还在展开（调用方原样透传）；
    - ``control`` 为 ``None`` ⇒ 已收尾：``satisfied`` 是写回的结果（``True`` / ``False``），
      ``card`` 是提供者**交给窗口**的产物（``None`` = 没有产物可交付，见
      :data:`PROVIDER_CARD_KEY`）。

    ``required`` = **这扇窗口要一张什么牌**，写进帧私有键 :data:`PROVIDER_REQUIRED_KEY`
    供回调读（引擎不猜、技能也不必从外层帧的牌反推）。
    """
    frame.data[PROVIDER_SKILL_KEY] = skill_id
    frame.data[PROVIDER_ACTOR_KEY] = actor
    frame.data[PROVIDER_REQUIRED_KEY] = required
    control = dispatch_provider(host, frame, skill_id, actor, timing)
    if control is not None:
        return control, None, None
    frame.data.pop(PROVIDER_SKILL_KEY, None)
    return _finish_provider(frame)


def advance_provider(
    host: Any, frame: Frame, timing: Timing
) -> tuple[Control, bool | None, CardRef | None]:
    """**重入**当前正在展开的提供者（若有）。

    返回 ``(control, satisfied, card)``：``(None, None, None)`` ⇒ 当前没有在展开的提供者
    （窗口照常继续）；``(control, None, None)``（``control`` 非 ``None``）⇒ 还在展开；
    ``(None, satisfied, card)`` ⇒ 刚收尾（语义同 :func:`start_provider`）。
    """
    skill_id = frame.data.get(PROVIDER_SKILL_KEY)
    if skill_id is None:
        return None, None, None
    control = dispatch_provider(host, frame, skill_id, frame.data[PROVIDER_ACTOR_KEY], timing)
    if control is not None:
        return control, None, None
    frame.data.pop(PROVIDER_SKILL_KEY, None)
    return _finish_provider(frame)


# ---------------------------------------------------------------------------
# 势力求助窗口（【护驾】/【激将】共用）
# ---------------------------------------------------------------------------


def _run_lord_request(
    host: Any, frame: Frame, skill: "LordSkill", actor: int, timing: Timing
) -> Control:
    """势力求助窗口的帧内游标（一次问一个应答者，重入从原处接着走）。

    - 应答者区间在**第一次**展开时定格（``LORD_REQUEST_KEY``）；
    - 每个应答者一次询问；``Pass`` ⇒ 轮到下一个（该座位不再被问第二次）；
    - **首个打出者即止**：记 ``done``、代打者走 L6（``host.play_card``），并让出控制权等
      PLAY 帧结算完，再在下一次重入写回"需要已满足"。

    **交付模式**（:data:`LORD_REQUEST_DELIVER_KEY`）：调用方要**自己开使用链**时，窗口把
    响应者选出的那张牌 ``provider_deliver`` 交回去、**不**替它打出——"视为由你使用"由调用
    方的 ``start_use`` 完成（票 17 的产物交接姿态）。置位者是三处**使用**窗口：出牌阶段主动
    使用【杀】（票 20，住 :func:`_jijiang_use_effect`）与两扇"被迫使用【杀】"的效果窗口
    （【借刀杀人】/【乱武】，票 22，住 `effects/ops.py` / `skills/active.py`——它们本来就
    自己 ``start_use`` 提供者产物）。**打出**窗口（南蛮 / 决斗 / 万箭 / 【闪】步）读不到这个
    键，语义逐字不变：代打是**真打出**。
    """
    request = frame.data.get(LORD_REQUEST_KEY)
    if request is None:
        request = {
            "askers": _request_askers(host.state, actor),
            "next": 0,
            "done": False,
            "satisfied": False,
        }
        frame.data[LORD_REQUEST_KEY] = request
    if request["done"]:
        frame.data[PROVIDER_SATISFIED_KEY] = request["satisfied"]
        return None
    while request["next"] < len(request["askers"]):
        asker = request["askers"][request["next"]]
        action = host.ask_respond(
            frame,
            asker,
            timing,
            cause=WindowCause.LORD_REQUEST,
            required=skill.required,
            target=actor,
            window_frame=frame.frame_id,
            mode=RespondMode.PLAY,
        )
        if host.suspended(action):
            return "stay"
        if isinstance(action, PlayCard):
            request["done"] = True
            request["satisfied"] = True
            if frame.data.get(LORD_REQUEST_DELIVER_KEY):
                # 使用侧（票 20）：**不**在求助里 start_use / play_card——把那张牌交回
                # 调用方，由它开使用链（"视为由你使用"）。产物经 PROVIDER_CARD_KEY 交付。
                provider_deliver(frame, action.card)
                return None
            # 代打是**真打出**：走 L6、消耗响应者的实体牌、不占响应者的使用次数。
            host.play_card(action.card, source=asker, timing=timing)
            return "stay"
        request["next"] += 1
    request["done"] = True
    frame.data[PROVIDER_SATISFIED_KEY] = False
    return None


# ---------------------------------------------------------------------------
# 【激将】的「使用」侧（yijiang-2014-round2 票 20）：出牌阶段主动**使用【杀】**
#
# 票面（官网 2026-11-09 逐字）：
#
#     主公技，其他蜀势力角色可以在你需要时代替你**使用或打出**【杀】（视为由你使用或打出）。
#
# 票 09 / M6 票 16 只落了「打出」半边（南蛮 / 决斗 / 万箭一类的响应窗口）。【激将】「使用」
# 这一侧有两条路：
#
# 1. **出牌阶段主动使用**（票 20，本段）：最常见、也最容易被 AI 无限点 ⇒ 配两道门槛
#    （用户 2026-11-09 的防卡死裁定）；
# 2. **被动的「被迫使用【杀】」窗口**（票 22）：【借刀杀人】与【乱武】——目标由窗外定死、
#    不需要也不该有门槛，故候选只靠声明的 ``respond_modes`` 含 ``USE`` 就能进来，产物由
#    那扇窗口 ``start_use``（:data:`LORD_REQUEST_DELIVER_KEY` 的交付模式）。
#
# **目标由主公先选**（只属第 1 条）：一条 :class:`~sgs.skills.flow.PickSeats` 步，池 = 「这张
# 虚拟【杀】此刻的合法目标」——候选 / 清单 / 校验器三处读同一个 :func:`_jijiang_use_legal`。
#
# **产物是主公的「使用」**：求助窗口把牌交回调用方（:data:`LORD_REQUEST_DELIVER_KEY`），
# 由调用方开**使用**链（``start_use``）——不在求助里 ``start_use`` / ``play_card``（票 17 的
# 产物交接姿态，"视为由你使用"）。
#
# **两道门槛**（用户原话：「一个是合法性门槛、一个是防卡死的机械门槛」）：
#
# 1. **合法性**：没有【杀】的次数（且不是无限）或没有合法目标 ⇒ 候选不出现。判据**直接
#    复用**票 18 抽出的 :func:`~sgs.effects.check_usable`（时机 + 禁用 + 次数 + 目标池
#    四件一起判），技能里不重抄"有没有次数 / 有没有目标"；
# 2. **机械锁**：PLAY 发动**开始问第一个盟友之前**就把 ``Player.jijiang_locked`` 置真；
#    解锁事件 = 该持有者**使用或打出**一张【杀】（``USE_ON_USE`` / ``PLAY_ON_PLAY``）
#    **或**他自己的新出牌阶段开始（``PHASE_PLAY_START``，用户 2026-11-09 补：「新的出牌
#    阶段开始也要解锁」）。被动窗口（南蛮 / 决斗…）**既不查也不置**这把锁（用户原话：
#    「这个锁只防止出牌阶段内主动发动激将，如果是被动响应使用/打出杀则不影响」），
#    【护驾】完全不锁（不能主动使用 / 打出【闪】⇒ 无卡死风险）。
#
# 锁**不进记录流**（不新增 delta / 时机），47 条金录像逐字节不变。
# ---------------------------------------------------------------------------

LORD_REQUEST_DELIVER_KEY = "lord_request_deliver"
"""帧私有键：这次求助的产物**交回调用方**（``True``）而不是由求助窗口自己打出。

置位者 = **三扇「使用」窗口**（它们自己 ``start_use`` 产物）：

- 出牌阶段主动使用【杀】（票 20，:func:`_jijiang_use_effect`）；
- 【借刀杀人】的「被迫使用【杀】」（票 22，`effects/ops.py:resolve_borrowed_sword`）；
- 【乱武】的「对最近的座位使用一张【杀】」（票 22，`skills/active.py:_luanwu_effect`）。

窗口把响应者选出的那张牌写进 :data:`PROVIDER_CARD_KEY` 交回去，由调用方开**使用**链
（"视为由你使用"）；打出窗口（南蛮 / 决斗 / 万箭 / 【闪】步）读不到它（缺省 ``False``），
仍是 :func:`_run_lord_request` 的既有语义"代打是**真打出**"。
"""

_JIJIANG_ASKED = "jijiang_asked"
"""帧私有：PLAY 发动的求助窗口**已经开过**（重入只续跑它，不重开、不重锁）。"""

_JIJIANG_DONE = "jijiang_done"
"""帧私有：这次 PLAY 发动**已经收尾**（产物已交付使用 / 没人响应）——重入直接结束。"""


def _jijiang_slash_card(state: GameState) -> Any:
    """这条通道的产物：一张**普通**【杀】的纯虚拟牌（只用来读使用规则，不进任何区域）。

    【激将】的代打产物按牌面是普通【杀】（无属性、无花色 / 点数），故 ``check_usable`` 与
    目标池对它的求值就是"这张【杀】此刻能不能用 / 能打谁"。纯虚拟牌走
    :func:`~sgs.skills.virtual.pure_virtual_card` 这条既有构造（与【神速】/【离间】的产物
    同源），不为"问一句能不能用"另造一张假牌。
    """
    return pure_virtual_card(state, "slash")


def _jijiang_use_legal(state: GameState, owner: int) -> bool:
    """出牌阶段主动发动【激将】使用【杀】的**唯一判据**（三项读者共读这一处）。

    读者 = PLAY 候选（:meth:`LordSkill.offer`）、预发动清单的目标池（:func:`_jijiang_use_targets`）
    与复合动作校验器（同一份 ``pool``，见 ``flow._assign_activation``）——故"候选里没有"
    与"提交也会被拒"结构上不可能各说一套（``Engine._structured_activation_failure`` 是
    "不在候选里也能提交"的那道缝，受理侧读到的必须是同一份裁决）。

    两件事：①**机械锁**（:attr:`~sgs.state.models.Player.jijiang_locked`）；②合法性 =
    :func:`~sgs.effects.check_usable` 的整条使用前校验（时机 / 禁用 / 次数 / 目标池），
    故次数用尽是假、【诸葛连弩】的无限次数照旧为真——不把判据写死成"每回合一次"。
    """
    if state.player(owner).jijiang_locked:
        return False
    return check_usable(state, owner, _jijiang_slash_card(state))


def _jijiang_use_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """目标步的池：**这张虚拟【杀】当下的合法目标**（``check_targets`` 的完整池）。

    不可发动（锁着 / 次数用尽 / 没有目标）⇒ **空池**：清单因此整条不下发
    （``flow.manifest`` 的空池分支），而复合动作的校验器照同一份池拒收——受理侧因此也
    有门槛，不只是候选侧藏起来。

    ``frame`` / ``chosen`` 不读：这一步没有前序答案（单步），池只取决于局面。
    """
    del frame, chosen
    if not _jijiang_use_legal(state, owner):
        return ()
    return check_targets(state, owner, _jijiang_slash_card(state))


def _jijiang_use_effect(host: Any, frame: Frame, answers: Any) -> Control:
    """提交点：锁 → 问同势力角色（求助窗口）→ **主公使用**产物。

    三个要点（都对应用户的裁定）：

    - **置点在问第一个人之前**（用户原话「每次发动时**立刻**禁止再次发动」）——锁写在
      开求助窗口之前，故"没人响应"与"有人响应"一样都锁上；
    - **求助窗口只在这里被当作提供者展开**（``start_provider`` / ``advance_provider``）：
      它可能跨多次重入（逐个问同势力角色），帧私有游标记住"已经开过"；
    - **产物交给使用链**：求助窗口在 ``LORD_REQUEST_DELIVER_KEY`` 模式下交回响应者选出的
      那张牌（不自己打出），这里 ``start_use`` 它、目标 = 主公刚才选的座位 ⇒ 记录流里是
      主公的一次**使用**（"视为由你使用"），不是响应者的一次打出。
    """
    owner = int(frame.data["source"])
    targets = tuple(int(seat) for seat in answers["seat"])
    if frame.data.get(_JIJIANG_DONE):
        return None
    if not frame.data.get(_JIJIANG_ASKED):
        frame.data[_JIJIANG_ASKED] = True
        host.state.player(owner).jijiang_locked = True
        frame.data[LORD_REQUEST_DELIVER_KEY] = True
        control, satisfied, produced = host.start_provider(
            frame,
            str(frame.data["skill_id"]),
            owner,
            Timing.PHASE_PLAY_IDLE,
            required="slash",
        )
    else:
        control, satisfied, produced = host.advance_provider(
            frame, Timing.PHASE_PLAY_IDLE
        )
    if control is not None:
        return control
    frame.data[_JIJIANG_DONE] = True
    frame.data.pop(LORD_REQUEST_DELIVER_KEY, None)
    if satisfied and produced is not None:
        host.start_use(
            produced,
            source=owner,
            targets=targets,
            use_timing=Timing.PHASE_PLAY_IDLE,
        )
        return "stay"
    return None


JIJIANG_USE_FLOW = SkillFlow(
    name="jijiang_use",
    steps=(
        PickSeats(lo=1, hi=1, pool=_jijiang_use_targets, optional=False, key="seat"),
    ),
    effect=_jijiang_use_effect,
    # 牌面不限次数（官网逐字没有"限一次"）⇒ 阶段位不落；唯一的频率闸门是机械锁。
    once_per_phase=False,
)
"""【激将】「出牌阶段主动使用【杀】」的流水线：选目标 → 求助窗口 → 主公使用产物。

清单 = 一步「选目标」（票 18 的 ``PickSeats`` 机制；``lo = hi = 1`` 是【杀】的额定目标数，
池由 :func:`_jijiang_use_targets` 现算）。锁在 ``effect`` 里、问第一个盟友之前置位。
"""


def jijiang_use_activate(host: Any, frame: Any) -> Any:
    """【激将】PLAY 发动的回调（登记在 :data:`sgs.skills.active.ACTIVATIONS`）。

    与 A 族的 :func:`~sgs.skills.active._activation_handler` 同形，但**不**落 ``_USED``
    阶段位：这条技能没有"出牌阶段限一次"，频率由机械锁表达。流水线名 / 步骤声明住在
    :data:`JIJIANG_USE_FLOW`，``run_flow`` 用复合动作的 ``activation`` 载荷摊回目标答案。
    """
    return run_flow(host, frame, JIJIANG_USE_FLOW)


def _jijiang_use_unlock_owner(frame: Frame) -> int | None:
    """解锁钩子的**持有者**（条件求值期由派发环写进 :data:`MATCH_OWNER_KEY`）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    return None if owner is None else int(owner)


def _jijiang_slash_unlock_condition(host: Any, frame: Frame) -> bool:
    """解锁事件①：**持有者自己**使用 / 打出【杀】族（含刚由这次【激将】产出的那张）。

    「牌名 ∈ {杀}」按 :func:`~sgs.effects.satisfies_required` 判族（火 / 雷 / 转化【杀】
    同族，与界【激将】② 同一条判据）；「来源 = 持有者」读使用 / 打出帧的既有载荷
    ``frame.data["source"]``。**别人**使用 / 打出【杀】不解锁（锁绑持有者）。
    """
    del host
    owner = _jijiang_use_unlock_owner(frame)
    if owner is None:
        return False
    source = frame.data.get("source")
    if source is None or int(source) != owner:
        return False
    card = frame.data.get("card")
    return card is not None and satisfies_required(card, "slash")


def _jijiang_play_phase_unlock_condition(host: Any, frame: Frame) -> bool:
    """解锁事件②：**持有者自己的**新出牌阶段开始（用户 2026-11-09 补）。

    阶段体帧的 ``source`` = 当前回合角色（``_dispatch_phase_body`` 写同一个值），故
    "source == 持有者"就是"这是他的出牌阶段"。对手的 ``PHASE_PLAY_START`` 因此**不**解锁
    ——锁按座位，不是全局开关。这一支覆盖「上一阶段不 productive（没出过【杀】）也不会
    被永久锁死」，也覆盖额外回合给出的出牌阶段。
    """
    del host
    owner = _jijiang_use_unlock_owner(frame)
    source = frame.data.get("source")
    return owner is not None and source is not None and int(source) == owner


def _jijiang_unlock(host: Any, frame: Any) -> Any:
    """解锁：把持有者的机械锁置假（三条解锁钩子共用这一个回调，幂等）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is not None:
        host.state.player(int(owner)).jijiang_locked = False
    return None


def _jijiang_unlock_hooks() -> tuple[Hook, ...]:
    """解锁的**三条**挂接：使用 / 打出【杀】（各一条）与自己的新出牌阶段开始。

    都是 ``forced=True``（**不问"是否发动"**：解锁是规则事实，不是可选技能段）；判据在
    ``condition`` 上（票 12 的姿态：条件门要能被 ``tests/test_hero_catalog.py`` 的
    ``_owner_probes`` 机械看见），效果体只做"置假"这一个幂等动作。
    """
    return (
        Hook(
            Timing.USE_ON_USE,
            condition=_jijiang_slash_unlock_condition,
            handler=_jijiang_unlock,
            forced=True,
        ),
        Hook(
            Timing.PLAY_ON_PLAY,
            condition=_jijiang_slash_unlock_condition,
            handler=_jijiang_unlock,
            forced=True,
        ),
        Hook(
            Timing.PHASE_PLAY_START,
            condition=_jijiang_play_phase_unlock_condition,
            handler=_jijiang_unlock,
            forced=True,
        ),
    )


@dataclass(frozen=True, slots=True)
class LordSkill(HeroSkillDecl):
    """F 族一条"求助型"技能的**声明**：需要什么牌 + 在哪个候选语境里提供。

    ``scope`` 决定候选回调在哪条 ``CANDIDATES`` 派发里生效（【护驾】= ``DODGE``、
    【激将】= ``RESPOND``）；``respond_modes`` 只在 ``RESPOND`` 语境里再筛一层——它回答
    「这一次响应算使用还是打出」（票 22 起是**一档集合**：两种都覆盖的技能写
    :data:`USE_OR_PLAY_MODES`）。今天的四条声明都是「使用或打出」的牌面：

    ============  =========================================  ====================
    技能           打出侧（``PLAY``）                          使用侧（``USE``）
    ============  =========================================  ====================
    【激将】       南蛮 / 决斗 / 万箭 / 求助子窗口            借刀 / 乱武的被迫使用
    【护驾】       万箭（``DODGE`` 语境，注册表 modes=PLAY）   【杀】的【闪】步（同上，
                                                              注册表 modes=USE）
    ============  =========================================  ====================

    【护驾】那一行两种模式都落在 ``DODGE`` 语境，而 ``DODGE`` 的候选派发**不带 mode 事实**
    （``actions._dodge`` 只给 ``respond_required``）⇒ 这一层对它是**声明性**的：写法在
    登记表上把「使用 / 打出」两半都认领了，行为与票 09 / M6 票 16 逐字相同（票 22 §2
    「牌面不动，只补口径」）。``RESPOND`` 语境的 mode 由 ``actions._respond`` 原样下传
    （南蛮 / 决斗 / 万箭 = ``PLAY``，借刀 / 乱武 = ``USE``），故【激将】的两半都是活的。

    ``activation_flow``（yijiang-2014-round2 票 20 的加法）：这条技能在**出牌阶段**作为
    一条"使用【杀】的入口"时的那条流水线（目标步 + 求助窗口 + 使用链）。缺省 ``None``
    ⇒ 不产出 PLAY 候选（【护驾】就是这种：不能主动使用 / 打出【闪】，用户 2026-11-09 的
    原话「护驾别锁，因为不能主动使用/打出闪」）。它有值时 ``offer`` 多出 PLAY 那一条、
    ``_lord_request_definition`` 把同一条流水线登记成声明级 ``flow`` 并翻预发动。
    """

    required: str
    scope: CandidateScope
    respond_modes: tuple[RespondMode, ...] | None = None
    activation_flow: SkillFlow | None = None

    def offer(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``CANDIDATES`` 回调：需要者**自己**的这条实例给它追加"视为发动"候选。

        与【八卦阵】同构（票 30 的提供者先例）：只在需要者自己的实例上追加
        （``ctx.owner == ctx.subject``）——这不是平台约束，而是**本技能的谓词**；
        跨座位注入是合法用法（`03` §4「技能描述模型」的「技能定义 / 实例 / 派发的冻结面」小节），
        【黄天】式技能会在这里写 ``ctx.owner != ctx.subject`` 的分支。
        "每提供者只问一次"由候选生成器统一过滤 ``ctx.providers_used``。

        **被动两侧**（``RESPOND`` / ``DODGE``）：只由窗口的 ``respond_required`` 与
        ``respond_modes`` 筛。票 22 起【激将】声明的 mode 集合含 ``USE`` ⇒【借刀杀人】
        （与【乱武】）的「被迫使用【杀】」窗口里也进候选；产物由窗口消费（求助窗口走
        :data:`LORD_REQUEST_DELIVER_KEY` 把牌交回去，窗口自己 ``start_use``）。

        **PLAY 那一条**（票 20）：``activation_flow`` 有值 ⇒ 出牌阶段像一条"使用【杀】的
        入口"那样出候选，闸门 = :func:`_jijiang_use_legal`（机械锁 + ``check_usable`` 整条
        使用前校验）。它与 ``RESPOND`` / ``DODGE`` 两条**互不影响**：同一份声明在别人回合的
        响应窗口里照样只由窗口的 ``respond_required`` / ``respond_modes`` 筛——被动窗口
        **既不查也不置**票 20 的机械锁（用户 2026-11-09：「如果是被动响应使用/打出杀则不影响」）。
        """
        if ctx.owner != ctx.subject:
            return current
        if ctx.scope is CandidateScope.PLAY:
            if self.activation_flow is None or not _jijiang_use_legal(state, ctx.owner):
                return current
            return (*current, ActivateSkill(skill_id=self.skill_id, answers_seq=ctx.answers_seq))
        if ctx.scope is not self.scope:
            return current
        if ctx.scope is CandidateScope.RESPOND:
            if ctx.respond_required != self.required:
                return current
            if self.respond_modes is not None and ctx.respond_mode not in self.respond_modes:
                return current
        return (*current, ActivateSkill(skill_id=self.skill_id, answers_seq=ctx.answers_seq))

    def provide(self, host: Any, frame: Frame, actor: int, timing: Timing) -> Control:
        """提供者回调：开势力求助窗口（见 :func:`_run_lord_request`）。"""
        return _run_lord_request(host, frame, self, actor, timing)


USE_OR_PLAY_MODES: tuple[RespondMode, ...] = (RespondMode.PLAY, RespondMode.USE)
"""「使用或打出」两档（票 22）：牌面逐字这么写的主公技（两条【激将】/ 两条【护驾】）都声明它。

``PLAY`` = 打出（南蛮 / 决斗 / 万箭 / 求助子窗口本身），``USE`` = 使用（借刀的「被迫使用
【杀】」与【乱武】；【闪】的【闪】步在登记表里也是这一档）。声明成一个**集合**而不是
"最后一个 mode 胜"：两张【激将】在**出牌阶段主动使用**之外要同时覆盖这两半，只写其中一个
就会让另一半的窗口静默地没有候选——票 22 要补的正是借刀那一半。
"""


HUJIA = LordSkill(
    hero_id="caocao",
    short_id="hujia",
    required="dodge",
    scope=CandidateScope.DODGE,
    respond_modes=USE_OR_PLAY_MODES,
)
"""曹操【护驾】：需要【闪】时令其他魏势力角色打出【闪】（规则 937）。

mode 集合含 ``USE`` = 牌面的「使用或打出」两半都认领（票 22）：使用侧住 ``DODGE`` 语境的
【闪】步（登记表 modes=USE）、打出侧住万箭（同一语境的 modes=PLAY）。``DODGE`` 派发不带
mode 事实 ⇒ 这一层是**声明性**的，行为与票 09 逐字相同。
"""

JIE_HUJIA = hero_skill_id("jie_caocao", "hujia")
"""界曹操【护驾】的全限定技能 id（§十九 L2030–L2034：①求助窗口 + ②决定者不是受益者）。"""

JIE_HUJIA_LORD = LordSkill(
    hero_id="jie_caocao",
    short_id="hujia",
    required="dodge",
    scope=CandidateScope.DODGE,
    respond_modes=USE_OR_PLAY_MODES,
)
"""界曹操【护驾】①：与标准版**逐字相同**的求助窗口声明（同一条 ``LordSkill.offer``）。

它是另一名武将的另一个技能 id，故在 :data:`LORD_REQUEST_SKILLS` 里独立成一条；②的钩子
另挂在这条 :class:`~sgs.skills.catalog.SkillDef` 上（见 :func:`_jie_hujia_definition`）。
"""

JIJIANG = LordSkill(
    hero_id="liubei",
    short_id="jijiang",
    required="slash",
    scope=CandidateScope.RESPOND,
    respond_modes=USE_OR_PLAY_MODES,
    activation_flow=JIJIANG_USE_FLOW,
)
"""刘备【激将】：需要【杀】时令其他蜀势力角色打出 / 代使用【杀】（规则 972 + 票 20 / 22）。

- 「打出」半边 = ``RESPOND`` + ``RespondMode.PLAY``（南蛮 / 决斗…）；
- 「使用」半边 = 出牌阶段主动使用的 ``activation_flow``（:data:`JIJIANG_USE_FLOW`）**加上**
  ``RespondMode.USE`` 的被动窗口（【借刀杀人】/【乱武】的「被迫使用【杀】」，票 22）；
- 两个被动半边都**不查、不置**票 20 的机械锁（锁只挡"出牌阶段主动发动"）。
"""

JIE_JIJIANG = hero_skill_id("jie_liubei", "jijiang")
"""界刘备【激将】的全限定技能 id（§十九 L2078–L2081：①求助窗口 + ②决定者不是受益者）。"""

JIE_JIJIANG_LORD = LordSkill(
    hero_id="jie_liubei",
    short_id="jijiang",
    required="slash",
    scope=CandidateScope.RESPOND,
    respond_modes=USE_OR_PLAY_MODES,
    activation_flow=JIJIANG_USE_FLOW,
)
"""界刘备【激将】①：与标准版**逐字相同**的求助窗口声明（同一条 ``LordSkill.offer``）。

"令所有其他蜀势力角色依次选择是否打出一张【杀】，若有角色打出【杀】，视为你使用或打出
一张【杀】"与标准【激将】的"令其他蜀势力角色选择是否打出一张【杀】（视为由你使用或
打出）"是同一件事；故它是另一个武将 id 上的另一条声明，不复制行为。
"""

LORD_REQUEST_SKILLS: tuple[LordSkill, ...] = (
    HUJIA,
    JIE_HUJIA_LORD,
    JIJIANG,
    JIE_JIJIANG_LORD,
)
"""F 族四条"求助型"技能的逐字声明（票 09 §要做什么 3 的③表 + M6 票 14 的界【护驾】① +
M6 票 16 的界【激将】①）。"""


# ---------------------------------------------------------------------------
# 主公技的 ② 半句（M6 票 14 的界【护驾】/ M6 票 16 的界【激将】）：
# **决定者不是受益者**的钩子
#
# §十九 L2030–L2034（界护驾②）：
#
#     ②每回合限一次，当其他魏势力角色于其回合外使用或打出【闪】时，其可以令你摸一张牌。
#
# §十九 L2078–L2081（界激将②）：
#
#     ②每回合限一次，当其他蜀势力角色于其回合外使用或打出【杀】时，其可以令你摸一张牌。
#
# 两句同形，只差**势力**与**牌名**；故落成一条声明 :class:`LordEcho`（两个消费者）：
# 钩子方向 = **使用**（``USE_ON_USE``）与**打出**（``PLAY_ON_PLAY``）两个时机；
# 受益者是技能持有者（主公），**决定者是那个使用 / 打出牌的角色**——
# :attr:`ActivationPlan.actor` 把"是否发动"的询问发到**它的座位**上，效果仍落在
# ``skill_owner`` 身上。
#
# "每回合限一次"落在 ``skill_state`` 的本回合位上（与【清俭】同一条：技能在**任何人的**
# 回合都可能触发，不能复用"本出牌阶段"的位）。与【清俭】同姿态：**真的发动了**才落位——
# 决定者放弃不算"发动过"，同一回合里再来一张同名牌仍会被问。
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LordEcho(HeroSkillDecl):
    """主公技的 **② 半句**声明：势力由持有者与事件来源比、牌名由 ``card_type`` 声明。"""

    card_type: str
    """触发牌名（``dodge`` / ``slash``；按 :func:`~sgs.effects.satisfies_required` 判，
    故【杀】族按共享计数键放宽为火 / 雷 / 转化【杀】）。"""

    requirement: str = ""
    """② 半句的人话（拼在 ① 后面，供询问提示用）。"""

    def condition(self, host: Any, frame: Any) -> bool:
        """触发条件：**其他同势力角色于其回合外**使用 / 打出 ``card_type``，且本回合未发动。

        持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写），事件
        的当事角色读 ``frame.data["source"]``（使用 / 打出帧的既有载荷）。五段全成立才发问：
        ①牌名对得上（转化 / 虚拟牌按**产物牌型**同判）；②当事角色不是主公自己（"其他"）；
        ③当前回合不是它的回合（"于其回合外"）；④与自己同势力；⑤本回合还没发动过。
        """
        owner = frame.data.get(MATCH_OWNER_KEY)
        if owner is None:
            return False
        card = frame.data.get("card")
        if card is None or not satisfies_required(card, self.card_type):
            return False
        source = frame.data.get("source")
        if source is None or source == owner:
            return False
        state = host.state
        if state.turn_seat == source:
            return False
        if not same_kingdom(state, source, owner):
            return False
        return not skill_used_this_turn(state, owner, self.skill_id)

    @property
    def hooks(self) -> tuple[Hook, ...]:
        """② 的两条挂接（使用 / 打出），共用同一条条件与同一条零步流水线。"""
        return (
            Hook(Timing.USE_ON_USE, condition=self.condition, flow=LORD_ECHO_FLOW),
            Hook(Timing.PLAY_ON_PLAY, condition=self.condition, flow=LORD_ECHO_FLOW),
        )


JIE_HUJIA_ECHO = LordEcho(
    hero_id="jie_caocao",
    short_id="hujia",
    card_type="dodge",
    requirement=(
        "每回合限一次，其他魏势力角色于其回合外使用或打出【闪】时，其可以令你摸一张牌"
    ),
)
"""界曹操【护驾】②：其他**魏**势力角色于其回合外使用 / 打出【闪】时，其可以令主公摸一张。"""

JIE_JIJIANG_ECHO = LordEcho(
    hero_id="jie_liubei",
    short_id="jijiang",
    card_type="slash",
    requirement=(
        "每回合限一次，其他蜀势力角色于其回合外使用或打出【杀】时，其可以令你摸一张牌"
    ),
)
"""界刘备【激将】②：其他**蜀**势力角色于其回合外使用 / 打出【杀】时，其可以令主公摸一张。"""

_ECHO_BY_LORD: dict[LordSkill, LordEcho] = {
    JIE_HUJIA_LORD: JIE_HUJIA_ECHO,
    JIE_JIJIANG_LORD: JIE_JIJIANG_ECHO,
}
"""①声明 → ②声明（同一条 ``SkillDef`` 上挂两半；按声明对象身份分派，不按 id 字符串猜）。"""

_LORD_ECHO_DRAWN_KEY = "lord_echo_drawn"
"""帧私有：这张牌上已经因 ② 摸过牌（续跑不重摸）。"""


def _lord_echo_draw(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：**主公**摸一张牌（决定者已在"是否发动"里答过），并落"本回合已发动"。

    收益归技能持有者（``skill_owner`` = 主公），不是答话的人。技能 id 读派发环写在帧上的
    :data:`~sgs.skills.catalog.RUNNING_SKILL_KEY`：同一条流水线服务两条主公技（界护驾 /
    界激将），落位必须落在**正在跑的那一条**上。帧私有游标挡住重入（摸牌可能内联挂起）。
    """
    del answers
    if frame.data.get(_LORD_ECHO_DRAWN_KEY):
        return None
    frame.data[_LORD_ECHO_DRAWN_KEY] = True
    owner = frame.data["skill_owner"]
    host.draw_for_hand(frame, owner, 1)
    skill_id = frame.data.get(RUNNING_SKILL_KEY)
    if skill_id:
        mark_skill_used_this_turn(host.state, owner, str(skill_id))
    return None


LORD_ECHO_FLOW = SkillFlow(name="lord_echo", steps=(), effect=_lord_echo_draw)
"""主公技 ② 的零步流水线：决定者在"是否发动"里作答 → 主公摸一张。"""


def lord_echo_activation_plan(
    state: GameState, frame: Frame, instance: Any
) -> ActivationPlan:
    """主公技 ② 的发动形态：**应答者 = 使用 / 打出的那个角色**（决定者 ≠ 受益者）。

    由 :data:`~sgs.skills.catalog.ACTIVATION_PLANS` 交给引擎的 ``ask_skill_activation``，
    询问因此出现在正确的座位上；``skill_owner`` 仍是主公，流水线的效果落在它身上。
    帧里没有 ``source`` 时退回 ``None``（= 问持有者，引擎缺省）——真实派发路径上恒有。
    """
    del state, instance
    source = frame.data.get("source")
    return ActivationPlan(actor=source if isinstance(source, int) else None)


def jie_hujia_activation_plan(
    state: GameState, frame: Frame, instance: Any
) -> ActivationPlan:
    """界【护驾】② 的发动形态（历史名字；与 :func:`lord_echo_activation_plan` 同一份判据）。"""
    return lord_echo_activation_plan(state, frame, instance)


def lord_request_activation_plans() -> dict[str, Any]:
    """F 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。"""
    return {
        JIE_HUJIA: lord_echo_activation_plan,
        JIE_JIJIANG: lord_echo_activation_plan,
        # M6 票 17：界【救援】的决定者 = 正在回复体力的那个吴势力角色。
        JIE_JIUYUAN: _jie_jiuyuan_plan,
        # lin-batch2 票 02：董卓移动版【暴虐】的决定者 = 造成伤害的那个群势力角色
        # （Q6 = A：判定者也落在它身上）。
        BAONUE: _baonue_plan,
    }


# ---------------------------------------------------------------------------
# 孙权【救援】：回复量修饰（锁定技）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecoverBoost(HeroSkillDecl):
    """F 族"改回复量"一条技能的声明：牌名闭集 + 加成值。"""

    card_types: tuple[str, ...] = ("peach",)
    step: int = 1

    def recover_amount(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``RECOVER_AMOUNT`` 回调：其他同势力角色对**自己**用【桃】时把回复量 +1。

        四个条件全部内联（票 09 §要做什么 3 的③表）：
        牌名是【桃】∧ 目标是自己（``ctx.owner == ctx.target``）∧
        来源是**其他**角色（``ctx.subject != ctx.owner``）∧ 来源势力 = 自己势力。
        自己对自己用【桃】因此不受影响（``ctx.subject == ctx.owner``）。
        """
        if ctx.card is None or ctx.card.type not in self.card_types:
            return current
        if ctx.owner != ctx.target or ctx.subject == ctx.owner:
            return current
        if not same_kingdom(state, ctx.subject, ctx.owner):
            return current
        return current + self.step


JIUYUAN = RecoverBoost(hero_id="sunquan", short_id="jiuyuan")
"""孙权【救援】：锁定技，其他吴势力角色对你使用【桃】时，回复的体力值 +1（规则 1004）。"""


# ---------------------------------------------------------------------------
# 袁绍移动版【血裔】（huo-mobile-batch 票 03；08 裁定读法 A）
#
#     血裔　主公技，锁定技，你的手牌上限+X（X为其他群势力角色数的两倍）。
#
# 查询期的**持续加式**，挂在唯一的"手牌上限修正链" `HAND_LIMIT_REPLACE` 上（用户
# 2026-11-02 签 08 = 读法 A）：`current` 是链上此刻的值（缺省 = 当前体力值、下限 0），
# 这里在原值上加 2 × 其他**存活**群势力角色数。与界周瑜【英姿】②的"替换成体力上限"
# 同座位并存时按**修饰器管道序**（= 解析序；派发链序在单座位内等价）决胜负——**当前局面
# 模型不可达**（一个座位只有一张武将牌），故今天
# 的可观测行为与"新增一个持续加式修饰点"完全一致；这就是不为此改共享原语的理由。
# ---------------------------------------------------------------------------

XUEYI = hero_skill_id("yuanshao_mobile", "xueyi")
"""袁绍移动版【血裔】的全限定技能 id（主公技 + 锁定技）。"""

_XUEYI_PER_OTHER_QUN = 2
"""每有一名**其他存活**群势力角色，手牌上限 +2（X = 该人数 × 2）。"""


def _xueyi_hand_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``HAND_LIMIT_REPLACE`` 回调：主公的手牌上限 + 2 × 其他存活群势力角色数。

    ``ctx.owner == ctx.subject`` 是"这条技能属于被查询的座位"的自校（修饰器链遍历的是
    全体存活实例，缺这一条会替所有人加上限；与界【英姿】② 同一姿态）。「其他」不含自己
    （账本默认读法（二）②）、「角色」只算存活（账本「X = 存活角色数…每次现算」）——
    查询期重算，不需要任何游标：群角色阵亡 / 失去主公身份 / 技能失效都在下一次读取
    立刻反映。与一次性加式（``Player.hand_limit_bonus``）并存时两者相加（`hand_limit`
    在替换链之后加那一轴，先后无差别）。
    """
    if ctx.owner != ctx.subject:
        return current
    others = sum(
        1
        for player in state.players
        if player.alive
        and player.seat != ctx.subject
        and player.kingdom is Kingdom.QUN
    )
    return current + _XUEYI_PER_OTHER_QUN * others


# ---------------------------------------------------------------------------
# 界孙权【救援】（M6 票 17）：**转移回复**——其他吴势力角色于其回合内回复体力时，可以
# 改为让主公回复 1 点、然后自己摸一张
#
# §十九 L2140–L2143（`## Comments` 的订正：**不是**"改判"，是转移回复）：
#
#     救援（主公技）：当其他吴势力角色于其回合内回复体力时，若其体力值不小于你，该角色
#           可以改为令你回复 1 点体力，然后其摸一张牌。
#
# 与标准版是**两条机制**：标准版是锁定的 ``RECOVER_AMOUNT`` 修饰器（其他吴势力角色对孙权
# 用【桃】时 +1），界版是**回复事件上的可选用应答**——落 ``RECOVER_VALUE`` 钩子，问的是
# **回复的那个吴势力角色**（决定者 ≠ 受益者，与界【护驾】② 同一个 ``ActivationPlan.actor``
# 机制；受益者 / 技能持有者仍是主公）。
#
# "改为"是**替换**：原回复不写体力（钩子在"确定回复数值"之后、写体力之前，帧私有键
# :data:`JIE_JIUYUAN_REPLACED_KEY` 让 RECOVER 帧跳过本次回复），改压一个主公的 RECOVER
# 子帧；子帧出栈后回到本钩子摸那一张（"然后"的严格次序：先回复、再摸牌）。`RECOVER_AFTER`
# 不再为被替换的目标发——它没有回复过体力。
#
# 触发判据四条全部内联：目标是**其他**角色 ∧ 其回合（``state.turn_seat``）∧ 同属吴势力 ∧
# 其体力值 ≥ 主公（比较发生在回复**之前**）。
# ---------------------------------------------------------------------------

JIE_JIUYUAN = hero_skill_id("jie_sunquan", "jiuyuan")
"""界孙权【救援】的全限定技能 id（§十九 L2140–L2143）。"""

JIE_JIUYUAN_REPLACED_KEY = "recover_replaced"
"""RECOVER 帧私有键：本次回复已被【救援】替换（引擎的 RECOVER 帧据此跳过体力写入）。

跨层契约：写方是本技能的钩子，读方是 :func:`sgs.engine.steps.recover_value`；与
:data:`~sgs.skills.hero_zone.QIANXUN_PLACED_KEY` 同一条姿态（skills 定义键、engine 读取）。
"""

_JIE_JIUYUAN_REDIRECTED = "jie_jiuyuan_redirected"
"""帧私有：转移已经成立（重入时跑"然后摸一张"、且条件据此保住，不因主公回血后比较翻转）。"""

_JIE_JIUYUAN_DRAWN = "jie_jiuyuan_drawn"
"""帧私有：那一张已经摸过（重入不重复摸）。"""


def _jie_jiuyuan_condition(host: Any, frame: Frame) -> bool:
    """触发条件（见本节头注）；转移成立后的重入直接放行——否则主公回血可能让
    "其体力值不小于你"翻转为假，第二次进入时钩子被挡掉、"然后摸一张"就丢了。"""
    if frame.data.get(_JIE_JIUYUAN_REDIRECTED):
        return True
    state = host.state
    lord = frame.data.get(MATCH_OWNER_KEY)
    targets = tuple(frame.data.get("targets", ()))
    if lord is None or len(targets) != 1:
        return False
    actor = int(targets[0])
    if actor == lord or state.turn_seat != actor:
        return False
    if not same_kingdom(state, actor, lord):
        return False
    return state.player(actor).hp >= state.player(lord).hp


def _jie_jiuyuan_plan(
    state: GameState, frame: Frame, instance: Any
) -> ActivationPlan:
    """发动形态：这一问交给**正在回复的那个吴势力角色**（决定者 ≠ 受益者）。"""
    del state, instance
    targets = tuple(frame.data.get("targets", ()))
    return ActivationPlan(actor=int(targets[0]) if targets else None)


def _jie_jiuyuan_redirect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：第一次进入 ⇒ 替换回复（不写体力）+ 压主公的 RECOVER 子帧；出栈后摸一张。"""
    del answers
    state = host.state
    lord = int(frame.data["skill_owner"])
    actor = int(tuple(frame.data.get("targets", ()))[0])
    if not frame.data.get(_JIE_JIUYUAN_REDIRECTED):
        frame.data[_JIE_JIUYUAN_REDIRECTED] = True
        frame.data[JIE_JIUYUAN_REPLACED_KEY] = True
        # 主公已满体力 ⇒ 没有实际的"回复 1 点"可写（不压一个零变化的 RECOVER 帧），
        # 直接进入"然后其摸一张牌"。
        if state.player(lord).hp < state.player(lord).hp_max:
            host.push_frame(
                FrameKind.RECOVER,
                {
                    "targets": (lord,),
                    "source": actor,
                    "card": None,
                    "use_timing": frame.data.get("use_timing"),
                    "amount": 1,
                },
                timing=Timing.RECOVER_VALUE,
            )
            return "stay"
    if not frame.data.get(_JIE_JIUYUAN_DRAWN):
        frame.data[_JIE_JIUYUAN_DRAWN] = True
        host.draw_for_hand(frame, actor, 1)
    return None


JIE_JIUYUAN_FLOW = SkillFlow(name="jie_jiuyuan", steps=(), effect=_jie_jiuyuan_redirect)
"""界【救援】的零步流水线：[是否发动] → 转移回复 + 摸一张（无步骤）。"""


# ---------------------------------------------------------------------------
# 林包·移动版董卓【暴虐】（lin-batch2 票 02；用户 2026-11-08 签 Q6 = A）
#
#     暴虐
#     　主公技，当其他群势力角色造成伤害后，其可以进行一次判定，
#     　若结果为♠️，你回复1点体力。
#
# 形状 = `shapes.md` §2 第 45 行「零步流水线」：`DAMAGE_AFTER_DEAL` 钩子（**每次伤害事件
# 一次**，``per_point=False``——牌面没有「1 点」）+ 零步流水线（只问"是否发动"）+
# :data:`~sgs.skills.catalog.ACTIVATION_PLANS` 里一条 `ActivationPlan(actor=来源)`。
#
# **决定者 ≠ 受益者**（牌面三处「其」同一先行词 = 造成这次伤害的那个群势力角色；「你」=
# 主公 / 技能持有者）：这一问发给**伤害来源**的座位（与界【护驾】② / 界【救援】同一条既有
# 机制，不新造形状），效果落在 ``skill_owner`` 身上。
#
# **判定者按移动版**（Q6 = A）：移动版牌面「其可以进行（一次）判定」⇒ 判定者是那个群势力
# 角色；OL 牌面「其可以令你判定」⇒ 判定者是董卓。两者结果相同（同一张牌、同一个花色判据），
# 差别只在 JUDGE 帧的 ``subject``。本仓按移动版落地（版本权威 = 用户 2026-11-08 第 1 点）。
# ---------------------------------------------------------------------------

BAONUE = hero_skill_id("dongzhuo_mobile", "baonue")
"""董卓移动版【暴虐】的全限定技能 id（主公技）。"""

BAONUE_REQUIREMENT = (
    "主公技，当其他群势力角色造成伤害后，其可以进行判定，若结果为黑桃，你回复1点体力"
)
"""一句话文案 = 移动版牌面逐字（票面 §4 询问剧本第一行；♠️ 按移动版官网写作「黑桃」）。"""

_BAONUE_JUDGED_KEY = "baonue_judged"
"""帧私有：判定帧已经压出（重入不重判）。"""

_BAONUE_RECOVERED_KEY = "baonue_recovered"
"""帧私有：回复那一段已经处理过（花色不符 / 满体力 / 已压 RECOVER 帧都算处理过）。"""


def _baonue_condition(host: Any, frame: Frame) -> bool:
    """触发条件：造成这次伤害的角色**不是**董卓本人 ∧ 与董卓**同势力**（群）。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写），事件
    来源读 ``frame.data["source"]``（伤害帧的既有载荷）。「其他」= 不含持有者自己
    （账本默认读法（二）②）；「群势力角色」= 两侧 ``Player.kingdom`` 都已发牌且相等
    （:func:`~sgs.skills.catalog.same_kingdom`，身份门控在 ``resolve_instances``）。

    **伤害被防止 / 数值 0 ⇒ 不成立**（票面 §4 询问剧本的兜底 ④）：引擎在
    ``damage_after_deal`` 里对被防止的伤害**仍会**走 ``_emit_damage``（`steps.py` 的
    "只落时机记录、逐点循环不跑"这一档，与 ``DAMAGE_AFTER_TAKE`` 在
    ``emit_frame_timing`` 里整条短路不同），故"这一问不该发"只能由条件自己判：
    ``amount <= 0`` 或帧标签 ``PREVENTED``（`sgs.resolution.has_tag`）时返回假。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if int(frame.data.get("amount", 1)) <= 0 or has_tag(frame, Tag.PREVENTED):
        return False
    source = frame.data.get("source")
    if source is None or int(source) == int(owner):
        return False
    return same_kingdom(host.state, int(source), int(owner))


def _baonue_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """发动形态：这一问交给**造成伤害的那个群势力角色**（决定者 ≠ 受益者）。

    与 :func:`lord_echo_activation_plan` / :func:`_jie_jiuyuan_plan` 同一条既有机制：帧里
    没有 ``source`` 时退回 ``None``（= 问持有者，引擎缺省）——真实派发路径上恒有。
    """
    del state, instance
    source = frame.data.get("source")
    return ActivationPlan(actor=source if isinstance(source, int) else None)


def _baonue_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：判定 → 黑桃则**玩家（技能持有者）**回复 1 点体力。

    两段都可挂起（判定帧可能被改判、回复可能触发别的钩子），各用一个帧私有游标：
    ``baonue_judged`` 在压 JUDGE 帧**之前**落位（重入不重判）；``baonue_recovered`` 在压
    RECOVER 帧**之前**落位（重入不重回复）。判定牌取完**立刻弹掉**（``SKILL_JUDGMENT_CARD_KEY``
    与同帧布尔 ``skill_result``），避免它们随帧数据流到同帧后续派发（先例 = 界【刚烈】）。

    满体力时**不压零变化帧**（与界孙权【救援】同一姿态），也不落 delta。
    """
    del answers
    owner = int(frame.data["skill_owner"])
    source = frame.data.get("source")
    if not frame.data.get(_BAONUE_JUDGED_KEY):
        frame.data[_BAONUE_JUDGED_KEY] = True
        # 判定者 = 造成伤害的群势力角色（Q6 = A：移动版「其进行判定」）。
        host.open_skill_judgment(frame, int(source), BAONUE)
        return "stay"
    if frame.data.get(_BAONUE_RECOVERED_KEY):
        return None
    frame.data[_BAONUE_RECOVERED_KEY] = True
    judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
    # 同帧的布尔结果只有本技能读（判定是刚开的、没有别人写），取完即弹。
    frame.data.pop("skill_result", None)
    if judge_card is None or judge_card.suit != Suit.SPADE.value:
        return None
    player = host.state.player(owner)
    if not player.alive or player.hp >= player.hp_max:
        return None
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": int(source),
            "targets": (owner,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )
    return "stay"


BAONUE_FLOW = SkillFlow(name="baonue", steps=(), effect=_baonue_effect)
"""【暴虐】的零步流水线：[是否发动] → 判定 → 黑桃则主公回复 1 点（无步骤）。"""


_PROVIDERS: dict[str, ProviderHandler] = {
    skill.skill_id: skill.provide for skill in LORD_REQUEST_SKILLS
}
"""``skill_id`` → 提供者回调的**唯一可变层**：初始只有四条主公技，加载期再由
:func:`register_provider` 并入技能层别的提供者（对外只读视图见 :data:`PROVIDERS`）。"""


def register_provider(skill_id: str, handler: ProviderHandler) -> None:
    """把一条**非主公技**的提供者登记进 :data:`PROVIDERS`（加载期一次，调用点 = `catalog.py`）。

    提供者不是主公技的专利：移动版于吉【蛊惑】在"需要一张基本牌 / 普通锦囊牌"时也走这条
    注册表（先扣置一张手牌、逐座位质疑，再决定这张牌算不算数）。"谁能当提供者"因此必须
    只有**一张**表——引擎按 :func:`provider_registered` 分派"求助 / 判定"两条路，表劈成
    两半就会让"选了 ``ActivateSkill`` 却落到判定路径"。

    只许在**加载期**登记：`catalog.py` 末尾（两个模块都已 import 时）调一次。运行期登记会
    让"这次窗口能派发哪些提供者"取决于时序。重复登记是编程错误——同一 id 有两个回调时
    没有"后者胜"的语义。
    """
    if skill_id in _PROVIDERS:
        raise ValueError(f"提供者 {skill_id!r} 已登记：同一技能不能有两个提供者回调")
    _PROVIDERS[skill_id] = handler


PROVIDERS: Mapping[str, ProviderHandler] = MappingProxyType(_PROVIDERS)
"""``skill_id`` → 提供者回调；对外**只读**（只有 :func:`register_provider` 在加载期写过一次），
引擎按 :func:`dispatch_provider` 通用派发。"""


def lord_request_skill_definitions() -> tuple[SkillDef, ...]:
    """四条求助型技能（【护驾】/界【护驾】/【激将】/界【激将】）的 ``SkillDef``。

    四条都**非锁定**（技能面写"你可以"）：候选由 ``CANDIDATES`` 挂点产出，
    选中 ``ActivateSkill`` 就是发动；``skill_type = LORD`` ⇒ 身份门控由载体解析兑现。
    界【护驾】/界【激将】另有 ② 的两条钩子（同一张定义上，见 :func:`_lord_request_definition`）。
    """
    return tuple(_lord_request_definition(skill) for skill in LORD_REQUEST_SKILLS)


def _lord_echo_requirement(skill: LordSkill, echo: LordEcho) -> str:
    """①② 都在一条技能上时的 ``requirement`` 文案：两段都要说（按声明取，不按 id 猜）。"""
    first = {
        "dodge": "当你需要使用或打出【闪】时，你可以令其他魏势力角色依次选择是否打出一张【闪】",
        "slash": "当你需要使用或打出【杀】时，你可以令其他蜀势力角色依次选择是否打出一张【杀】",
    }[skill.required]
    return f"①{first}；②{echo.requirement}"


def _lord_request_definition(skill: LordSkill) -> SkillDef:
    """一条求助型技能的 :class:`SkillDef`；带 ② 半句时多挂两条钩子。

    "多出来的那一半"是这一处显式的分支：``_ECHO_BY_LORD`` 的键是**声明对象**（这条声明
    就是界曹操的【护驾】/ 界刘备的【激将】），不是按 id 字符串猜的白名单。

    另两条分支也读声明、不读 id 白名单（yijiang-2014-round2 票 20）：

    - ``activation_flow`` 有值 ⇒ 声明级 ``flow``（出牌阶段「使用【杀】」的入口）+ 三条
      解锁钩子 + ``preactivated=True``（可用性只剩预发动清单）；清单文案取
      ``step_requirements``；
    - 没有 ⇒ 与票 09 / M6 票 16 逐字相同（【护驾】不主动使用 / 打出【闪】，故无锁、
      无流水线）。
    """
    echo = _ECHO_BY_LORD.get(skill)
    hooks: tuple[Hook, ...] = () if echo is None else echo.hooks
    if skill.activation_flow is not None:
        # 解锁钩子挂在**同一条**声明上（不是第二条 SkillDef：``CATALOG`` 是同 id 后者胜）。
        hooks = (*hooks, *_jijiang_unlock_hooks())
    requirement = {"dodge": "打出【闪】", "slash": "使用或打出【杀】"}[skill.required]
    if echo is not None:
        requirement = _lord_echo_requirement(skill, echo)
    return SkillDef(
        skill_id=skill.skill_id,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.LORD,
        hooks=hooks,
        flow=skill.activation_flow,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, skill.offer),),
        requirement=requirement,
        step_requirements=(
            {"seat": "选择【杀】的目标"} if skill.activation_flow is not None else None
        ),
        preactivated=skill.activation_flow is not None,
    )


def lord_skill_definitions() -> tuple[SkillDef, ...]:
    """F 族五条主公技的 :class:`~sgs.skills.catalog.SkillDef`（票 12 §落地表的 F 行 + 票 17）。"""
    return (
        *lord_request_skill_definitions(),
        SkillDef(
            skill_id=JIUYUAN.skill_id,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            modifiers=(Modifier(ModifierPoint.RECOVER_AMOUNT, JIUYUAN.recover_amount),),
            requirement="其他吴势力角色对你使用【桃】时，回复的体力值 +1",
        ),
        # 界孙权【救援】（M6 票 17）：回复事件上的可选用应答（决定者 = 正在回复的吴势力
        # 角色，受益者 = 主公），与标准版的"锁定 +1"不是同一条机制。
        # 袁绍移动版【血裔】（huo-mobile-batch 票 03）：主公技 + 锁定技 + 持续加式。
        SkillDef(
            skill_id=XUEYI,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            modifiers=(Modifier(ModifierPoint.HAND_LIMIT_REPLACE, _xueyi_hand_limit),),
            requirement="主公技，锁定技，你的手牌上限+X（X为其他群势力角色数的两倍）",
        ),
        SkillDef(
            skill_id=JIE_JIUYUAN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            hooks=(
                Hook(
                    Timing.RECOVER_VALUE,
                    condition=_jie_jiuyuan_condition,
                    flow=JIE_JIUYUAN_FLOW,
                ),
            ),
            requirement=(
                "当其他吴势力角色于其回合内回复体力时，若其体力值不小于你，该角色可以改为"
                "令你回复 1 点体力，然后其摸一张牌"
            ),
        ),
        # 林包·移动版董卓【暴虐】（lin-batch2 票 02，Q6 = A）：主公技 + `DAMAGE_AFTER_DEAL`
        # 零步流水线；决定者 / 判定者 = 造成伤害的那个群势力角色，收益归主公。
        SkillDef(
            skill_id=BAONUE,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            hooks=(
                Hook(
                    Timing.DAMAGE_AFTER_DEAL,
                    condition=_baonue_condition,
                    flow=BAONUE_FLOW,
                ),
            ),
            requirement=BAONUE_REQUIREMENT,
        ),
    )
