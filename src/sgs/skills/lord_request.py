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
曹操【护驾】               非锁定    ``CANDIDATES(DODGE)`` → 势力求助窗口（需要【闪】）
界曹操【护驾】①            非锁定    同上（同一条 ``LordSkill.offer`` 声明）
界曹操【护驾】②            非锁定    ``USE_ON_USE`` / ``PLAY_ON_PLAY`` 钩子：其他魏势力角色于其
                                    回合外使用 / 打出【闪】时，**其**可以令主公摸一张牌
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

注册表**不只服务主公技**（M6 票 01）：移动版于吉【蛊惑】用同一个口子实现"需要牌时先扣置
再质疑"，由 :func:`register_provider` 在加载期并入（调用点 = `catalog.py` 末尾，那里两个
模块都已 import）。引擎侧的分派代码因此一个字都不用改。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    satisfies_required,
)
from sgs.resolution import Control, Frame
from sgs.state import (
    ActivateSkill,
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
    HeroSkillDecl,
    SkillDef,
    mark_skill_used_this_turn,
    same_kingdom,
    skill_used_this_turn,
)
from .flow import SkillFlow

__all__ = [
    "HUJIA",
    "JIE_HUJIA",
    "JIE_HUJIA_ECHO",
    "JIE_HUJIA_LORD",
    "JIE_JIJIANG",
    "JIE_JIJIANG_ECHO",
    "JIE_JIJIANG_LORD",
    "JIJIANG",
    "JIE_JIUYUAN",
    "JIE_JIUYUAN_FLOW",
    "JIE_JIUYUAN_REPLACED_KEY",
    "JIUYUAN",
    "LORD_REQUEST_KEY",
    "LORD_REQUEST_SKILLS",
    "PROVIDERS",
    "PROVIDER_ACTOR_KEY",
    "PROVIDER_SATISFIED_KEY",
    "PROVIDER_SKILL_KEY",
    "LORD_ECHO_FLOW",
    "LordEcho",
    "LordSkill",
    "advance_provider",
    "dispatch_provider",
    "jie_hujia_activation_plan",
    "lord_echo_activation_plan",
    "lord_request_activation_plans",
    "lord_request_skill_definitions",
    "lord_skill_definitions",
    "provider_registered",
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

JIE_HUJIA = hero_skill_id("jie_caocao", "hujia")
"""界曹操【护驾】的全限定技能 id（§十九 L2014–L2018：①求助窗口 + ②决定者不是受益者）。"""

JIE_HUJIA_LORD = LordSkill(
    hero_id="jie_caocao",
    short_id="hujia",
    required="dodge",
    scope=CandidateScope.DODGE,
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
    respond_mode=RespondMode.PLAY,
)
"""刘备【激将】：需要【杀】时令其他蜀势力角色打出【杀】（规则 972）。"""

JIE_JIJIANG = hero_skill_id("jie_liubei", "jijiang")
"""界刘备【激将】的全限定技能 id（§十九 L2062–L2065：①求助窗口 + ②决定者不是受益者）。"""

JIE_JIJIANG_LORD = LordSkill(
    hero_id="jie_liubei",
    short_id="jijiang",
    required="slash",
    scope=CandidateScope.RESPOND,
    respond_mode=RespondMode.PLAY,
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
# §十九 L2014–L2018（界护驾②）：
#
#     ②每回合限一次，当其他魏势力角色于其回合外使用或打出【闪】时，其可以令你摸一张牌。
#
# §十九 L2062–L2065（界激将②）：
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
# 界孙权【救援】（M6 票 17）：**转移回复**——其他吴势力角色于其回合内回复体力时，可以
# 改为让主公回复 1 点、然后自己摸一张
#
# §十九 L2124–L2127（`## Comments` 的订正：**不是**"改判"，是转移回复）：
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
"""界孙权【救援】的全限定技能 id（§十九 L2124–L2127）。"""

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
    """
    echo = _ECHO_BY_LORD.get(skill)
    hooks: tuple[Hook, ...] = () if echo is None else echo.hooks
    requirement = {"dodge": "打出【闪】", "slash": "打出【杀】"}[skill.required]
    if echo is not None:
        requirement = _lord_echo_requirement(skill, echo)
    return SkillDef(
        skill_id=skill.skill_id,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.LORD,
        hooks=hooks,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, skill.offer),),
        requirement=requirement,
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
    )
