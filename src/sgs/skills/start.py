r"""L0「游戏开始时」的技能（wuming 票 01）：自制包·无名自制版【天成】。

牌面（`D:\SGS_HEROS\自制包\无名\技能.txt`，2026-11-03 修订版，逐字）::

    无名 2/2 群 男
    【天成】(锁)：游戏开始时，依次展示3张真名不在场上的随机武将牌，且每次展示你须选择：
    1.获得该武将牌上的1个技能；2.增加一点体力上限，回复一点体力，摸X张牌（X为你选择选项2的次数）

**形状**（`docs/agents/skill-authoring/shapes.md` §2）：L0 **帧外时机钩子**（锁定技 ⇒ 派发环
不问"是否发动"）+ **手写主体**（3 次展示的变长循环，每次一问）。`SkillFlow` 是定长元组，
没有循环步，故三次展示的游标全部住**帧私有数据**（`_dispatch_phase_body` 给 L0 的持久帧，
重入复用同一份）：抽到的三张只抽一次、展示到第几张、已选过几次选项 2。X **不落
:class:`~sgs.state.Marker`**——那是公开计数（界面上会多一行），而 X 只在这条技能的
三次展示里有意义。

三次展示的**次序与可重入**（`CODING_STANDARDS.md` §3）：

1. 首次进入：``draw_hero_cards(3)`` 一次性抽完（**不幂等**，故只在这里抽；抽到的真名当场进
   屏蔽集合 ⇒ 重入不会换批）+ 游标清零。
2. 每次展示：``ask_option`` 问一次（询问 id 带展示序号 ``tiancheng:reveal:<k>``——帧内答案
   缓存键 = ``(skill_id, actor, 范围)``，三次展示共用一个 id 时第二问会读到第一问的答案）。
3. 选项 2：**加上限 → 回复 → 摸 X 张**，依次压 ``MAXLIFE`` / ``RECOVER`` 帧并 ``"stay"``；
   帧出栈后重入本钩子，按 :data:`_STAGE_MAXLIFE` / :data:`_STAGE_RECOVER` / :data:`_STAGE_DRAW`
   续跑（阶段指针先写、再压帧，故重入既不会重做也不会漏做）。
4. 选项 1：``grant_skill`` 当场授予，立即生效（实例解析读 ``Player.granted_skills``）。

**展示恒为 3 次**（`01-wuming.md` 第四轮裁定 1）：可抽真名不足 3 个时，缺的那几次**没有
武将牌**（``HeroCardRef(hero_id=None)``），只给选项 2；该武将的非 ☆ 技能**全已拥有**时同样
只剩置灰行 + 选项 2，照常弹窗。**没有「放弃」**：牌面已由用户改「须」，询问恒
``optional=False``。

::询问剧本（`01-wuming.md` §1，用户 2026-11-03 已签）

| 步 / 段 | 呈现 | 落点 |
|---|---|---|
| 触发（游戏开始时） | 牌面原文、**不发询问**（锁定技） | :data:`TIANCHENG_REQUIREMENT` |
| 展示第 k/3 张 + 选一项（同一问） | 弹窗标题「天成」、副标题「第 k/3 张」、武将块 + 技能行 + 选项 2 | ``Engine.ask_option(..., disabled=, option_texts=, hero_card=)`` |
| 效果 ①（获得技能） | 点按钮即提交，无二次询问 | :meth:`Engine.grant_skill` |
| 效果 ②（上限 / 回复 / 摸） | 同一行文案里的 X 是**具体数字** | :meth:`Engine.raise_hp_max` → ``RECOVER`` 帧 → :meth:`Engine.draw_for_hand` |
"""

from __future__ import annotations

from typing import Any

from sgs.data import hero_card_skill_ids, hero_skill_id
from sgs.effects import CarrierKind
from sgs.state import FrameKind, HeroCardRef
from sgs.timing import Timing

from .catalog import Hook, SkillDef

WUMING = "wuming"
"""无名自制版的武将 id（数据面 = ``heroes.homebrew.json`` 的「无名自制版」条目）。"""

TIANCHENG = hero_skill_id(WUMING, "tiancheng")
"""【天成】的全限定技能 id。"""

TIANCHENG_MAX_HP = "tiancheng:max_hp"
"""选项 2 的选项 id（**不是**技能 id：这一行的文本由引擎现算，见
:attr:`~sgs.state.SelectOptionContext.option_texts`）。与 ``ui_smoke.mjs`` 的合成载荷同一个 id。"""

TIANCHENG_REVEALS = 3
"""展示次数（**分母恒为 3**：可抽真名不足时缺的那几次没有武将牌，仍展示 3 次）。"""

TIANCHENG_REQUIREMENT = (
    "(锁)：游戏开始时，依次展示3张真名不在场上的随机武将牌，且每次展示你须选择："
    "1.获得该武将牌上的1个技能；2.增加一点体力上限，回复一点体力，摸X张牌"
    "（X为你选择选项2的次数）"
)
"""``SkillDef.requirement``：牌面原文**逐字**（提示条 / 询问上下文读它）。"""

_OPTION_TWO_TEXT = "增加 1 点体力上限，回复 1 点体力，摸 {x} 张牌"
"""选项 2 的**动态**标签（`01-wuming.md` 的询问剧本逐字；X 由引擎算成具体数字）。

不落 :data:`~sgs.skills.catalog.SkillDef.step_requirements`、也不落静态文案表：X 是每一次
询问现算的（1 / 2 / 3），静态表表达不了，故走 :meth:`Engine.ask_option` 的 ``option_texts``
（票 07 给的效果行通道：**有文本 = 技能自己的效果行**，技能行不给文本）。"""

# ---------------------------------------------------------------------------
# 帧私有槽名（全部住 L0 的持久帧；不进记录流、不参与任何规则判据）
# ---------------------------------------------------------------------------

_TC_CARDS = "tiancheng:hero_cards"
"""一次抽到的武将牌 id（抽到的先后序；可能短于 3）。**只写一次**——抽取不幂等。"""

_TC_STEP = "tiancheng:reveal"
"""当前展示到第几张（0 起）。"""

_TC_STAGE = "tiancheng:stage"
"""这一次展示走到哪一段（:data:`_STAGE_ASK` / … / :data:`_STAGE_ADVANCE`）。"""

_TC_X = "tiancheng:x"
"""X = **已选过选项 2 的次数**（含已结算的那几次；本次的 X 是它 + 1）。"""

_TC_DONE = "tiancheng:done"
"""整条技能已跑完（三次展示都结算完 / 短摸平局提前收尾）。"""

_STAGE_ASK = "ask"
_STAGE_MAXLIFE = "maxlife"
_STAGE_RECOVER = "recover"
_STAGE_DRAW = "draw"
_STAGE_ADVANCE = "advance"


def _revealed_hero_id(frame: Any, step: int) -> str | None:
    """第 ``step`` 次展示的武将牌 id；抽到的牌不足时 ``None``（缺的那几次）。"""
    cards = frame.data.get(_TC_CARDS, ())
    return cards[step] if step < len(cards) else None


def _owned_skill_ids(state: Any, seat: int) -> frozenset[str]:
    """``seat`` **已拥有**的技能 id 集合（置灰判据 = 同 **skill id**，`01-wuming.md` O1）。

    真源 = 存活实例（:func:`~sgs.skills.catalog.resolve_instances`）：武将牌载体 ∪ 运行时
    获得的技能（``Player.granted_skills``，含 ☆ 技能与【天成】自己上一张武将牌授予的），
    不另抄一份名单。装备技 id 形如 ``equip.*``，与武将牌的 ``hero.*`` 永不相交。
    """
    from .catalog import resolve_instances

    return frozenset(
        instance.skill_id
        for instance in resolve_instances(state)
        if instance.owner == seat
    )


def _reveal_options(
    host: Any, owner: int, hero_id: str | None, x_next: int
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """这一次展示的**选项闭集**、置灰项与选项 2 的文本（票 07 的三个展示载荷）。

    - 技能行 = 该武将牌上**非 ☆** 技能（:func:`~sgs.data.hero_card_skill_ids`：数据文件序，
      排除 ``granted_by`` 非空的 ☆ 条目），每行：技能名 + ``descriptions.json`` 原文，由投影
      从数据表派生（选项 id 就是全限定技能 id，界面不抄第二份）；
    - 已被座位拥有（同 skill id）⇒ **置灰**（保留原位，从 ``legal_actions`` 剔除）；
    - 选项 2 固定**最后**；没有武将牌时只剩它。
    """
    if hero_id is None:
        return (TIANCHENG_MAX_HP,), (), _OPTION_TWO_TEXT.format(x=max(x_next, 1))
    skill_ids = tuple(hero_card_skill_ids(hero_id, pack_id=host.state.pack_id))
    owned = _owned_skill_ids(host.state, owner)
    disabled = tuple(skill_id for skill_id in skill_ids if skill_id in owned)
    return (
        (*skill_ids, TIANCHENG_MAX_HP),
        disabled,
        _OPTION_TWO_TEXT.format(x=max(x_next, 1)),
    )


def _push_recover(host: Any, frame: Any, owner: int) -> None:
    """压一个 ``RECOVER`` 子帧（回复 1 点；满体力时由帧夹住 ⇒ 回复 0、不报错，规则 §4.15 L426）。

    与【勤学】的回复同一写法（``awakening._qinxue_push_recover``）：回复量、上限夹取与
    ``RECOVER_VALUE`` / ``RECOVER_AFTER`` 两个时机都仍由 ``RECOVER`` 步骤表统一处理，
    技能不直接写 ``hp``。
    """
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": owner,
            "targets": (owner,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _tiancheng(host: Any, frame: Any) -> Any:
    """【天成】的钩子回调（锁定 ⇒ 派发环直接跑；``frame.data["skill_owner"]`` = 持有者）。

    一个**可重入的状态机**：每次重入都从 ``_TC_STAGE`` 接着走，直到三次展示都结算完
    （返回 ``None``）或需要等一个询问 / 子帧（返回 ``"stay"``）。
    """
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_TC_DONE):
        return None
    if _TC_CARDS not in frame.data:
        # 一次性抽 3 张（两段随机；抽到的真名当场进屏蔽集合）——**重入不重抽**。
        frame.data[_TC_CARDS] = tuple(host.draw_hero_cards(TIANCHENG_REVEALS))
        frame.data[_TC_STEP] = 0
        frame.data[_TC_X] = 0
        frame.data[_TC_STAGE] = _STAGE_ASK
    while True:
        step = int(frame.data[_TC_STEP])
        if step >= TIANCHENG_REVEALS:
            frame.data[_TC_DONE] = True
            return None
        stage = frame.data[_TC_STAGE]
        if stage == _STAGE_ASK:
            hero_id = _revealed_hero_id(frame, step)
            x_next = int(frame.data[_TC_X]) + 1
            options, disabled, text = _reveal_options(host, owner, hero_id, x_next)
            answer = host.ask_option(
                frame,
                owner,
                Timing.GAME_START,
                # 询问 id 带展示序号：帧内答案缓存键 = (skill_id, actor, 范围)，三次展示
                # 共用一个 id 时第二问会读到第一问的答案（【神速】的 `shensu:target:<i>` 同款）。
                skill_id=f"{TIANCHENG}:reveal:{step + 1}",
                options=options,
                optional=False,
                disabled=disabled,
                option_texts={TIANCHENG_MAX_HP: text},
                hero_card=HeroCardRef(
                    hero_id=hero_id,
                    index=step + 1,
                    total=TIANCHENG_REVEALS,
                ),
            )
            if host.suspended(answer):
                return "stay"
            choice = str(answer.choice)
            if choice == TIANCHENG_MAX_HP:
                frame.data[_TC_X] = x_next
                frame.data[_TC_STAGE] = _STAGE_MAXLIFE
            else:
                # 效果 ①：当场授予（第四种技能来源）——立刻生效（如【武圣】的转化）。
                host.grant_skill(owner, choice, frame=frame)
                frame.data[_TC_STAGE] = _STAGE_ADVANCE
            continue
        if stage == _STAGE_MAXLIFE:
            # 效果 ② 第 1 段：+1 体力上限（`MAXLIFE` 事件帧，两个「加」时机各派发一次）。
            # 阶段指针**先写再压帧**：重入既不会重加、也不会漏加。
            frame.data[_TC_STAGE] = _STAGE_RECOVER
            host.raise_hp_max(owner, 1)
            return "stay"
        if stage == _STAGE_RECOVER:
            # 效果 ② 第 2 段：回复 1 点（满体力 ⇒ 回复 0，不报错）。
            frame.data[_TC_STAGE] = _STAGE_DRAW
            _push_recover(host, frame, owner)
            return "stay"
        if stage == _STAGE_DRAW:
            # 效果 ② 第 3 段：摸 X 张（X = 选过选项 2 的次数，含本次）；既有摸牌原语，
            # 短摸 ⇒ 既有平局口径（G10）。
            frame.data[_TC_STAGE] = _STAGE_ADVANCE
            control = host.draw_for_hand(frame, owner, int(frame.data[_TC_X]))
            if control == "stay":
                return "stay"
            if control is False:
                # 短摸 = 平局（引擎已 finish）：展示循环就此收尾，不再问下一张。
                frame.data[_TC_DONE] = True
                return None
            continue
        # _STAGE_ADVANCE：这一次展示结算完，推进到下一张。
        frame.data[_TC_STEP] = step + 1
        frame.data[_TC_STAGE] = _STAGE_ASK


def start_skill_definitions() -> tuple[SkillDef, ...]:
    """L0 族的 :class:`~sgs.skills.catalog.SkillDef`（今天只有【天成】一条）。

    ``locked=True`` ⇒ 派发环不问"是否发动"、直接跑回调（`shapes.md` §2 的锁定技变体）；
    ``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``（不是主公技）。
    挂点只有 ``GAME_START``（L0.3：起始手牌发完后、第一回合之前，规则 §3.9 L229–L233）——
    帧外派发点由票 03 落在 :meth:`sgs.engine.Engine._start`，本技能是它的**第一个载体**。

    ``requirement`` = 牌面原文逐字（询问剧本的"触发"一行：锁定技不发询问，只作提示条 / 日志）。
    """
    return (
        SkillDef(
            skill_id=TIANCHENG,
            locked=True,
            carrier=CarrierKind.HERO,
            hooks=(Hook(Timing.GAME_START, handler=_tiancheng),),
            requirement=TIANCHENG_REQUIREMENT,
        ),
    )
