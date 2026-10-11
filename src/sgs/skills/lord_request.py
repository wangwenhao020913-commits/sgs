r"""主公技族（F，票 09）：势力作用域、**势力求助窗口**与回复量修饰。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` / :mod:`sgs.skills.response_window` 同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，
:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

**"主公技机制"不新造**（票 09 §要做什么 / R4-Q2）：它与武将技能同构，只差票 01 落在
:func:`~sgs.skills.catalog.resolve_instances` 的**身份门控**（非主公 ⇒ 武将牌上根本没有这个技能）。
三条技能的落点：

=======================  ======  ==========================================================
技能                       锁定     落点
=======================  ======  ==========================================================
曹操【护驾】               非锁定    ``CANDIDATES(DODGE)`` → 势力求助窗口（需要【闪】）
刘备【激将】               非锁定    ``CANDIDATES(RESPOND)`` → 势力求助窗口（需要【杀】）
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
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.data import SkillType
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
)
from sgs.resolution import Control, Frame
from sgs.state import (
    ActivateSkill,
    GameState,
    PlayCard,
    RespondMode,
    WindowCause,
    seats_from,
)
from sgs.timing import Timing

from .catalog import HeroSkillDecl, SkillDef, same_kingdom

__all__ = [
    "HUJIA",
    "JIJIANG",
    "JIUYUAN",
    "LORD_REQUEST_KEY",
    "LORD_REQUEST_SKILLS",
    "PROVIDERS",
    "PROVIDER_ACTOR_KEY",
    "PROVIDER_SATISFIED_KEY",
    "PROVIDER_SKILL_KEY",
    "LordSkill",
    "advance_provider",
    "dispatch_provider",
    "lord_request_skill_definitions",
    "lord_skill_definitions",
    "provider_registered",
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
结果写在 :data:`PROVIDER_SATISFIED_KEY`。
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


def start_provider(
    host: Any, frame: Frame, skill_id: str, actor: int, timing: Timing
) -> tuple[Control, bool | None]:
    """**开始**一次提供者展开：记进度键 → 派发一次。

    返回 ``(control, satisfied)``：``control`` 非 ``None`` ⇒ 还在展开（调用方原样透传）；
    ``control`` 为 ``None`` ⇒ 已收尾，``satisfied`` 是写回的结果（``True`` / ``False``）。
    """
    frame.data[PROVIDER_SKILL_KEY] = skill_id
    frame.data[PROVIDER_ACTOR_KEY] = actor
    control = dispatch_provider(host, frame, skill_id, actor, timing)
    if control is not None:
        return control, None
    frame.data.pop(PROVIDER_SKILL_KEY, None)
    frame.data.pop(LORD_REQUEST_KEY, None)
    return None, bool(frame.data.pop(PROVIDER_SATISFIED_KEY, False))


def advance_provider(host: Any, frame: Frame, timing: Timing) -> tuple[Control, bool | None]:
    """**重入**当前正在展开的提供者（若有）。

    返回 ``(control, satisfied)``：``(None, None)`` ⇒ 当前没有在展开的提供者（窗口照常继续）；
    ``(control, None)``（``control`` 非 ``None``）⇒ 还在展开；``(None, satisfied)`` ⇒ 刚收尾。
    """
    skill_id = frame.data.get(PROVIDER_SKILL_KEY)
    if skill_id is None:
        return None, None
    control = dispatch_provider(host, frame, skill_id, frame.data[PROVIDER_ACTOR_KEY], timing)
    if control is not None:
        return control, None
    frame.data.pop(PROVIDER_SKILL_KEY, None)
    frame.data.pop(LORD_REQUEST_KEY, None)
    return None, bool(frame.data.pop(PROVIDER_SATISFIED_KEY, False))


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
            # 代打是**真打出**：走 L6、消耗响应者的实体牌、不占响应者的使用次数。
            host.play_card(action.card, source=asker, timing=timing)
            return "stay"
        request["next"] += 1
    request["done"] = True
    frame.data[PROVIDER_SATISFIED_KEY] = False
    return None


@dataclass(frozen=True, slots=True)
class LordSkill(HeroSkillDecl):
    """F 族一条"求助型"技能的**声明**：需要什么牌 + 在哪个候选语境里提供。

    ``scope`` 决定候选回调在哪条 ``CANDIDATES`` 派发里生效（【护驾】= ``DODGE``、
    【激将】= ``RESPOND``）；``respond_mode`` 只在 ``RESPOND`` 语境里再筛一层
    （【激将】按票面只覆盖**打出**【杀】的窗口：南蛮 / 决斗，不含【借刀杀人】的"使用【杀】"）。
    """

    required: str
    scope: CandidateScope
    respond_mode: RespondMode | None = None

    def offer(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``CANDIDATES`` 回调：需要者**自己**的这条实例给它追加"视为发动"候选。

        与【八卦阵】同构（票 30 的提供者先例）：只在需要者自己的实例上追加
        （``ctx.owner == ctx.subject``）——这不是平台约束，而是**本技能的谓词**；
        跨座位注入是合法用法（`03` §4「技能描述模型」的「技能定义 / 实例 / 派发的冻结面」小节），
        【黄天】式技能会在这里写 ``ctx.owner != ctx.subject`` 的分支。
        "每提供者只问一次"由候选生成器统一过滤 ``ctx.providers_used``。
        """
        del state
        if ctx.owner != ctx.subject or ctx.scope is not self.scope:
            return current
        if ctx.scope is CandidateScope.RESPOND:
            if ctx.respond_required != self.required:
                return current
            if self.respond_mode is not None and ctx.respond_mode is not self.respond_mode:
                return current
        return (*current, ActivateSkill(skill_id=self.skill_id, answers_seq=ctx.answers_seq))

    def provide(self, host: Any, frame: Frame, actor: int, timing: Timing) -> Control:
        """提供者回调：开势力求助窗口（见 :func:`_run_lord_request`）。"""
        return _run_lord_request(host, frame, self, actor, timing)


HUJIA = LordSkill(
    hero_id="caocao",
    short_id="hujia",
    required="dodge",
    scope=CandidateScope.DODGE,
)
"""曹操【护驾】：需要【闪】时令其他魏势力角色打出【闪】（规则 937）。"""

JIJIANG = LordSkill(
    hero_id="liubei",
    short_id="jijiang",
    required="slash",
    scope=CandidateScope.RESPOND,
    respond_mode=RespondMode.PLAY,
)
"""刘备【激将】：需要【杀】时令其他蜀势力角色打出【杀】（规则 972）。"""

LORD_REQUEST_SKILLS: tuple[LordSkill, ...] = (HUJIA, JIJIANG)
"""F 族两条"求助型"技能的逐字声明（票 09 §要做什么 3 的③表）。"""


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


PROVIDERS: Mapping[str, ProviderHandler] = MappingProxyType(
    {skill.skill_id: skill.provide for skill in LORD_REQUEST_SKILLS}
)
"""``skill_id`` → 势力求助提供者；**模块级不可变**，引擎按 :func:`dispatch_provider` 通用派发。"""


def lord_request_skill_definitions() -> tuple[SkillDef, ...]:
    """两条求助型技能（【护驾】/【激将】）的 :class:`~sgs.skills.catalog.SkillDef`。

    两条都**非锁定**（技能面写"你可以"）：候选由 ``CANDIDATES`` 挂点产出，
    选中 ``ActivateSkill`` 就是发动；``skill_type = LORD`` ⇒ 身份门控由载体解析兑现。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            modifiers=(Modifier(ModifierPoint.CANDIDATES, skill.offer),),
        )
        for skill in LORD_REQUEST_SKILLS
    )


def lord_skill_definitions() -> tuple[SkillDef, ...]:
    """F 族三条主公技的 :class:`~sgs.skills.catalog.SkillDef`（票 12 §落地表的 F 行）。"""
    return (
        *lord_request_skill_definitions(),
        SkillDef(
            skill_id=JIUYUAN.skill_id,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            modifiers=(Modifier(ModifierPoint.RECOVER_AMOUNT, JIUYUAN.recover_amount),),
        ),
    )
