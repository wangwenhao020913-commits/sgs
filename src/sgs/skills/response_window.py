r"""响应窗口族（E，票 08）：所需张数 = 修饰器，不可响应 = 帧标签。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` 同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个
``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

E 族两件都改"响应窗口"，但按 R5-Q4 的分界走两条路——**改一个值 ⇒ 修饰器；加一条禁令 /
标志 ⇒ 帧标签**：

=======================  ==========  ==========================================================
技能                      锁定        落点
=======================  ==========  ==========================================================
吕布【无双】              锁定        ``Modifier(RESPONSE_COUNT, 2)``（见下方两半）
马超【铁骑】              非锁定      ``Hook(USE_AFTER_DESIGNATE_TARGET)`` + 判定 + 帧标签 ``UNRESPONSIVE``
=======================  ==========  ==========================================================

【无双】（规则 1046）：牌名 ∈ {【杀】,【决斗】} ⇒ 本次响应窗口需要**两张**，但**改的是哪一方**
两半不同（票 23）：

- **【杀】**：「你**使用**【杀】指定一名角色为目标后，该角色需连续使用两张【闪】」⇒ 改的是
  **使用者**那一侧，判据 ``owner == subject``；
- **【决斗】**：§九 L1046「你使用【决斗】……**或成为其他角色【决斗】的目标后**，该角色每次
  响应需连续打出两张【杀】」⇒ 改的是**被决斗的一方**那一侧，判据 = 引擎标注的
  :data:`~sgs.effects.ModifierRole.TARGET`（这一问的响应者就是那张【决斗】指定的目标）
  ∧ 持有者是这场【决斗】的任一方。漏掉后半句会让吕布挨【决斗】时只需一张【杀】。

两处"数数"都读 :func:`sgs.effects.response_count` 这一处真源（引擎的【闪】窗口与【决斗】
结算 + 网页层的"需要打几张"文案同一个入口），故引擎里不出现技能名。

【铁骑】（规则 987，）：非锁定技，``USE_AFTER_DESIGNATE_TARGET`` 逐目标判定；红色 ⇒ 给**该目标**
打 :data:`~sgs.resolution.Tag.UNRESPONSIVE`（**逐目标档**）。这个标签只负责"跳过本窗口"
（【杀】跳过【闪】窗口、锦囊跳过无懈窗口是同一个标签的两个后果），窗口该不该存在仍由行为表的
``response_window`` 决定；"视为使用【闪】"（【八卦阵】）也因此一并被封——**视为使用仍是使用**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    ModifierRole,
    satisfies_required,
)
from sgs.resolution import Control, Frame, Tag, add_tag
from sgs.state import (
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    SKILL_JUDGMENT_CARD_KEY,
    HeroSkillDecl,
    Hook,
    HookHandler,
    SkillDef,
)
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "JIE_TIEQI",
    "JIE_WUSHUANG",
    "RESPONSE_WINDOW_SKILLS",
    "TIEQI_FLOW",
    "response_window_skill_definitions",
]


RESPONSE_COUNT_NEEDED = 2
"""【无双】把本次响应窗口所需张数改成 **2**（票 08 §要做什么 3）。"""

TIEQI_PENDING = "tieqi_pending"
"""帧私有键：本次【铁骑】的判定帧已压出（重入时据此读判定结果）。"""
TIEQI_CURSOR = "tieqi_cursor"
"""帧私有键：逐目标判定的游标（多目标【杀】里一个目标一次判定）。"""


# ---------------------------------------------------------------------------
# 吕布【无双】：RESPONSE_COUNT 修饰器
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResponseCountSkill(HeroSkillDecl):
    """E 族"改所需张数"一条技能的声明：牌名闭集 + 目标张数。"""

    card_types: tuple[str, ...]
    count: int = RESPONSE_COUNT_NEEDED

    def response_count(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``RESPONSE_COUNT`` 回调：把本次响应的所需张数改成 ``count``。

        牌面 ①（【杀】）与 ②（【决斗】）改的是**不同的一方**，故两条分支各写一处判据
        （票 23 补 ② 的反向，`06`（§5「程序化约定与偏离一手」））：

        - **【杀】**：改的是**使用者**——"当你使用【杀】指定一名角色为目标后，该角色需连续
          使用两张【闪】"。故只有 ``ctx.owner == ctx.subject``（牌是我用的）才生效；
        - **【决斗】**：改的是**吕布的对手**每次响应的张数——牌面写"当你使用【决斗】指定一名
          角色为目标后，**或成为其他角色【决斗】的目标后**，该角色每次响应需连续打出两张
          【杀】"。两半里的"该角色"都是**跟吕布决斗的那一方**（标准版牌面把这一半直接写成
          "**与你进行【决斗】的角色**每次需连续打出两张【杀】"），故：

          * 吕布**使用**【决斗】⇒ 对手（被指定的那个目标）每次两张、吕布本人一张；
          * 吕布**成为**【决斗】的目标 ⇒ 仍然是**对手**（这次是使用者）每次两张、
            **吕布本人一张**——牌面命名的对象是"对手"，不是"先响应的那个人"。

          判据因此是"响应者**不是**吕布 ∧ 吕布在这场【决斗】里"：``ctx.owner`` 与
          ``ctx.subject`` / ``ctx.target`` 的两种组合正好覆盖两个方向，不需要特判谁是使用者。

        牌名闭集按 :func:`~sgs.effects.satisfies_required` 逐项匹配（【杀】族按共享计数键，
        M5-05b-fix；【决斗】仍逐字相等）。
        """
        del state
        if ctx.card is None:
            return current
        if "slash" in self.card_types and satisfies_required(ctx.card, "slash"):
            return self.count if ctx.owner == ctx.subject else current
        if "duel" in self.card_types and satisfies_required(ctx.card, "duel"):
            if ctx.owner not in (ctx.subject, ctx.target):
                # 这场【决斗】跟吕布无关（两个别的角色在决斗）⇒ 不动张数。
                return current
            responder = ctx.target if ctx.role is ModifierRole.TARGET else ctx.subject
            # 吕布本人响应 ⇒ 一张；对手响应 ⇒ 两张。
            return current if ctx.owner == responder else self.count
        return current


WUSHUANG = ResponseCountSkill(
    hero_id="lvbu",
    short_id="wushuang",
    card_types=("slash", "duel"),
)
"""吕布【无双】：锁定技，牌名是【杀】/【决斗】时本次响应需要两张（规则 1046）。

【杀】改的是**使用者**那一侧、【决斗】改的是**吕布的对手**那一侧——两半的判据都在
:meth:`ResponseCountSkill.response_count` 里（票 23）。
"""

JIE_WUSHUANG = hero_skill_id("jie_lvbu", "wushuang")
"""界吕布【无双】的全限定技能 id（§十九 L2188–L2191）。

界版牌面与标准版**逐字同义**（①【杀】要两张【闪】、②【决斗】要两张【杀】，只是改写成
①/② 的编号形态）⇒ 同一条 ``RESPONSE_COUNT`` 回调、同一条修饰器，界版只是另一个武将 id
（与【马术】【克己】【流离】的"另一个武将 id"同一姿态，不复制第二份行为）。
"""


# ---------------------------------------------------------------------------
# 马超【铁骑】：判定 + 逐目标帧标签
# ---------------------------------------------------------------------------


def _tieqi(host: Any, frame: Frame) -> Control:
    """【铁骑】的 ``USE_AFTER_DESIGNATE_TARGET`` 钩子：逐目标判定，红 ⇒ 给该目标打标签。

    多目标【杀】（【方天画戟】）里**逐个目标各判一次**——"指定一名角色为目标后"是逐目标的
    时机，而 :class:`~sgs.skills.catalog.Hook` 的一次派发带的是整张目标表，故用帧内游标
    （``tieqi_cursor``）逐个推进。判定的"红 / 黑"由 :meth:`~sgs.engine.Engine.note_skill_judgment`
    写回帧私有 ``skill_result``，本回调取出后**立刻弹掉**，避免它随 ``dict(frame.data)``
    流进 RESOLVE 帧、被【八卦阵】的【闪】窗口误当作自己的判定结果。
    """
    targets = tuple(frame.data.get("targets", ()))
    cursor = frame.data.get(TIEQI_CURSOR, 0)
    if cursor >= len(targets):
        return None
    if frame.data.pop(TIEQI_PENDING, False):
        if frame.data.pop("skill_result", False):
            add_tag(frame, Tag.UNRESPONSIVE, target=targets[cursor])
        frame.data[TIEQI_CURSOR] = cursor + 1
        return "stay"  # 下一轮：要么为下一个目标判定，要么在上面的守卫处收尾
    frame.data[TIEQI_PENDING] = True
    host.open_skill_judgment(frame, targets[cursor], TIEQI.skill_id)
    return "stay"


# ---------------------------------------------------------------------------
# 界马超【铁骑】（M6 票 04）：封印目标技能 + 判定 + 由目标决定弃牌
# ---------------------------------------------------------------------------

JIE_TIEQI = hero_skill_id("jie_machao", "tieqi")
"""界马超【铁骑】的全限定技能 id（§十九 L2093–L2096）。"""

_JIE_TIEQI_CURSOR = "jie_tieqi_cursor"
"""帧私有键：逐目标推进的游标（多目标【杀】里一个目标一次判定 + 一次弃牌询问）。"""

_JIE_TIEQI_STAGE = "jie_tieqi_stage"
"""帧私有键：当前目标停在哪个阶段——``"judge"``（判定帧已压出）/ ``"discard"``（该搬弃牌）。"""

_JIE_TIEQI_PICK = "jie_tieqi_pick"
"""帧私有键：目标选中的那张同花色牌（搬到弃牌堆之前先落这里，挂起后据此只搬一次）。"""

_JIE_TIEQI_SUIT = "jie_tieqi_suit"
"""帧私有键：本次判定的**花色**（判定牌读出来后即定格）。

判定牌回写的帧私有键在"算出花色"那一步就被弹掉；重入（目标弃牌询问挂起后续跑）若再读
一次，就已经是 ``None`` —— 面会变空、直接判成"不能响应"而**漏掉那条应答**（票 04 的
评审发现：``_answer`` 泄漏到下一个询问）。故花色必须在中途定格。"""

_ASK_JIE_TIEQI_DISCARD = "hero.jie_machao.tieqi.discard"
"""手写询问位置：【铁骑】"弃置一张与判定结果花色相同的牌"的选牌步（`:meth:`Engine.ask_selection`
的 ``owner``；询问名 ``key`` 逐目标带序号）。"""


def _tieqi_suit_face(
    state: GameState, seat: int, suit: str | None
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """目标的**同花色选择面**：手牌 ∪ 装备区里花色等于判定结果的那些牌。

    "弃置一张牌"不限定手牌（与【贯石斧】/【雌雄双股剑】同一档：手牌 + 装备区）。面由技能
    自铺而不走 :meth:`Engine.selection_face`：那条路按区铺、不按花色筛，而这里的花色判据
    就是判定结果的直接后果。应答者 = 牌的持有者本人 ⇒ 全部是**可见槽**（不需要隐藏位置）。
    """
    slots: list[Slot] = []
    card_ids: list[int] = []
    player = state.player(seat)
    for zone, cards in ((Zone.HAND, player.hand), (Zone.EQUIP, player.equip)):
        for card_id in cards:
            card = state.card(card_id)
            if suit is not None and card.suit == suit:
                slots.append(Slot(ref=ZoneRef(zone, seat), card=card))
                card_ids.append(card_id)
    return tuple(slots), tuple(card_ids)


def _jie_tieqi_next(frame: Frame, cursor: int) -> None:
    """推进到下一个目标：清掉当前目标的阶段 / 选牌 / 花色游标（逐目标分槽，不跨目标串味）。"""
    frame.data[_JIE_TIEQI_CURSOR] = cursor + 1
    frame.data.pop(_JIE_TIEQI_STAGE, None)
    frame.data.pop(_JIE_TIEQI_PICK, None)
    frame.data.pop(_JIE_TIEQI_SUIT, None)


def _jie_tieqi(host: Any, frame: Frame) -> Control:
    """界马超【铁骑】：使用【杀】指定目标后，封印其非锁定技 → 判定 → 由其决定弃牌（§十九 L2093–2096）。

    牌面三段，逐目标各跑一遍（帧内游标 ``jie_tieqi_cursor``，重入从头）：

    1. **令其本回合所有非锁定技失效**：写一条座位级失效记录（``Engine.suppress_skills``）
       ——失效率不逐技能展开，故该座位**此刻与之后获得**的非锁定技都被挡（§票面验收 1）；
    2. **你进行一次判定**：``open_skill_judgment`` 压 JUDGE 帧，判定牌由引擎回写进本帧
       （取**花色**，不是"红 = 成功"的布尔）；
    3. **除非该角色弃置一张与判定结果花色相同的牌，否则其不能使用【闪】响应此【杀】**：
       "弃不弃"由**目标**决定（本票裁定，见 `06` §5「程序化约定与偏离一手」）——把同花色牌铺成一张可选面
       （``optional=True``，``Pass`` = 不弃）；弃了 ⇒ 窗口照常，没弃 ⇒ 给该目标打
       :data:`~sgs.resolution.Tag.UNRESPONSIVE`（逐目标档，【闪】窗口因此不会出现，
       【八卦阵】一类的"视为使用"也一并被封）。

    判定 / 弃牌都可能挂起（改判窗口、失去牌后钩子），故每段的完成事实都记在帧私有游标里。
    多目标时逐目标标签与既有标准版【铁骑】同形（``use_tags`` 随游标推进清除，见
    ``engine.steps._PER_TARGET_FRAME_KEYS``）。
    """
    state = host.state
    owner = frame.data["skill_owner"]
    targets = tuple(frame.data.get("targets", ()))
    cursor = int(frame.data.get(_JIE_TIEQI_CURSOR, 0))
    if cursor >= len(targets):
        return None
    target = int(targets[cursor])
    stage = frame.data.get(_JIE_TIEQI_STAGE)
    if stage is None:
        host.suppress_skills(target, JIE_TIEQI, frame=frame)
        frame.data[_JIE_TIEQI_STAGE] = "judge"
        stage = "judge"
    if stage == "judge":
        frame.data[_JIE_TIEQI_STAGE] = "decide"
        host.open_skill_judgment(frame, target, JIE_TIEQI)
        return "stay"
    if stage == "decide":
        if _JIE_TIEQI_SUIT not in frame.data:
            judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
            # 同帧的布尔结果只有本技能读（判定是刚开的），取完即弹。
            frame.data.pop("skill_result", None)
            frame.data[_JIE_TIEQI_SUIT] = None if judge_card is None else judge_card.suit
        suit = frame.data[_JIE_TIEQI_SUIT]
        face = _tieqi_suit_face(state, target, suit)
        # 没有同花色牌也**照发**这条询问（空选择面、只有 Pass）：不发就等于告诉对手
        # "他手里没有这个花色"（用户报障的博弈信息泄露）。选择面为空时
        # ``ask_when_empty`` 让引擎仍构造一条只有 ``Pass`` 的询问。
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIE_TIEQI_DISCARD,
            key=f"jie_tieqi:discard:{cursor}",
            actor=target,
            timing=Timing.USE_AFTER_DESIGNATE_TARGET,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP), total=(1, 1), optional=True
            ),
            face=face,
            ask_when_empty=True,
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            # 目标选择**不弃**（"除非"的另一侧）：不能使用【闪】响应此【杀】。
            add_tag(frame, Tag.UNRESPONSIVE, target=target)
            _jie_tieqi_next(frame, cursor)
            return "stay"
        frame.data[_JIE_TIEQI_PICK] = tuple(picked)
        frame.data[_JIE_TIEQI_STAGE] = "discard"
    picked = tuple(frame.data[_JIE_TIEQI_PICK])
    control = host.move_in_frame(
        frame,
        state.refs(picked),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=target,
        actor=target,
    )
    if control == "stay":
        # 搬牌内联派发"失去牌后"并挂起：游标**不前进**，重入只补搬这一次（``move_in_frame``
        # 自带同帧幂等守卫，故重复调用不会搬第二遍）。
        return "stay"
    _jie_tieqi_next(frame, cursor)
    return "stay"


@dataclass(frozen=True, slots=True)
class FrameTagSkill(HeroSkillDecl):
    """E 族"加一条禁令"一条技能的声明：触发时机 + 标签取值。"""

    timing: Timing
    locked: bool
    handler: HookHandler


TIEQI = FrameTagSkill(
    hero_id="machao",
    short_id="tieqi",
    timing=Timing.USE_AFTER_DESIGNATE_TARGET,
    locked=False,
    handler=_tieqi,
)
"""马超【铁骑】：非锁定技，判定为红 ⇒ 该目标不能使用【闪】响应此【杀】（规则 987）。"""


TIEQI_FLOW = SkillFlow(name="tieqi", steps=(), effect=zero_step_effect(TIEQI.handler))
"""【铁骑】的零步流水线（W6-01）：[是否发动] → 判定 + 逐目标打 ``UNRESPONSIVE``（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""


RESPONSE_WINDOW_SKILLS: tuple[ResponseCountSkill | FrameTagSkill, ...] = (WUSHUANG, TIEQI)
"""E 族两条技能的逐字声明（票 08 §要做什么 3 / 7）。"""

_REQUIREMENTS: dict[str, str] = {
    "wushuang": "使用【杀】/【决斗】时，响应者需要连续打出两张",
    "tieqi": "判定；红色则该目标不能使用【闪】响应此【杀】",
    "jie_tieqi": "令目标本回合非锁定技失效并判定：除非其弃置一张同花色牌，否则不能使用【闪】响应此【杀】",
}
"""E 族两条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


def response_window_skill_definitions() -> tuple[SkillDef, ...]:
    """E 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`。

    【无双】锁定（技能面没有"可以"）⇒ ``locked=True``，落 ``RESPONSE_COUNT`` 修饰器；
    【铁骑】非锁定（"你可以进行判定"）⇒ ``locked=False``，落 ``USE_AFTER_DESIGNATE_TARGET``
    钩子。两条 ``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=WUSHUANG.skill_id,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.RESPONSE_COUNT, WUSHUANG.response_count),),
            requirement=_REQUIREMENTS["wushuang"],
        ),
        SkillDef(
            skill_id=TIEQI.skill_id,
            locked=TIEQI.locked,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(TIEQI.timing, flow=TIEQI_FLOW),),
            requirement=_REQUIREMENTS["tieqi"],
        ),
        # 界马超【铁骑】（M6 票 04）：非锁定、逐目标的多段手写钩子（判定 + 条件弃牌）。
        SkillDef(
            skill_id=JIE_TIEQI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.USE_AFTER_DESIGNATE_TARGET, handler=_jie_tieqi),),
            requirement=_REQUIREMENTS["jie_tieqi"],
            step_requirements={"card": "弃置一张与判定结果花色相同的牌（由被指定者选择）"},
        ),
        # 界吕布【无双】（M6 票 18）：与标准版逐字同义 ⇒ 同一个回调 / 同一条修饰器。
        SkillDef(
            skill_id=JIE_WUSHUANG,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.RESPONSE_COUNT, WUSHUANG.response_count),),
            requirement=_REQUIREMENTS["wushuang"],
        ),
    )
