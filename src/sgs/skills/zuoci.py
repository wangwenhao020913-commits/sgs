"""山包·左慈：【化身】/【新生】（shan-baozuoci 票 04）。

牌面（用户 `技能.txt`，OL 现行，逐字）::

    化身 ①游戏开始时，你随机获得两张武将牌作为“化身”牌，然后你选择其中一张“化身”牌的一个技能
         （仅限无标签技能、锁定技）并亮出之，然后你视为拥有此技能，且性别和势力视为与“化身”牌相同。
         ②回合开始或结束时，你可以重新进行一次“化身”。
    新生 当你受到1点伤害后，若你有技能“化身”，你可以随机获得一张新的“化身”牌。

结算按《三國殺官方規則集 2018.3-16》左慈条 + 用户 2026-11-13 的五条裁定：

- **技能范围** = 无标签技能 + 锁定技（数据侧 `skill_type == NORMAL`；限定 / 觉醒 / 主公除外）；
- **② 只重选**：不重抽，只有【新生】往库里加牌；
- **性别 / 势力** 随当前化身牌；失去【化身】回男 / 群（非死亡收尾今天不可达，见
  `.scratch/shan-baozuoci/issues/03-c1-skill-lifecycle-callback.md`）；
- **化身牌只有自己可见**（票 05 的外观组件负责显示）；
- **换化身 = 失去旧技能 + 获得新技能**：走 `Engine.revoke_skill`（可逆，不写 `lost_skills`）+
  `grant_skill`；死亡时手里化身牌回堆（`Engine.clear_avatar_cards`，票 02）。

形状（`shapes.md` §2 第 79 行）：L0 强制段 + 手写选择循环（样板 = 自制包·无名【天成】）；
② 用帧外时机钩子（`TURN_START` / `TURN_END`），新生用逐点钩子（`per_point`）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_by_id, hero_card_skill_id, hero_skill_id
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, AppearanceKind, CarrierKind, Hook, SkillDef
from .catalog import resolve_instances
from .flow import PickOption, SkillFlow

__all__ = [
    "HUASHEN",
    "HUASHEN_REQUIREMENT",
    "XINSHENG",
    "XINSHENG_REQUIREMENT",
    "avatar_skill_rows",
    "current_avatar_skill",
    "eligible_skill_ids",
    "skill_name_on",
    "zuoci_skill_definitions",
]

ZUOCI = "zuoci"

HUASHEN = hero_skill_id(ZUOCI, "huashen")
XINSHENG = hero_skill_id(ZUOCI, "xinsheng")

HUASHEN_REQUIREMENT = (
    "游戏开始时，你随机获得两张武将牌作为“化身”牌，然后你选择其中一张“化身”牌的一个技能"
    "（仅限无标签技能、锁定技）并亮出之，然后你视为拥有此技能，且性别和势力视为与“化身”牌相同；"
    "回合开始或结束时，你可以重新进行一次“化身”"
)
XINSHENG_REQUIREMENT = (
    "当你受到1点伤害后，若你有技能“化身”，你可以随机获得一张新的“化身”牌"
)

_HUASHEN_START = f"{HUASHEN}:start"
_HUASHEN_AGAIN = f"{HUASHEN}:again"
_HUASHEN_DRAWN = "huashen:drawn"
_HUASHEN_ASKED = "huashen:asked"


# ---------------------------------------------------------------------------
# 候选面：手里的化身牌 × 每张牌的合格技能
# ---------------------------------------------------------------------------


def avatar_skill_rows(
    hero_id: str, *, pack_id: str
) -> tuple[tuple[str, bool], ...]:
    """一张化身牌上**列出来**的技能与它是否**合格**：``((skill_id, eligible), ...)``。

    两条判据分开，因为它们是两件事（用户 2026-11-13 的 Q4）：

    - **列出** = 非 ☆ 条目（``granted_by is None``）。☆ 技能觉醒前根本不在武将牌上，
      化身也拿不到它，故连"置灰"都不该出现；
    - **合格** = ``skill_type is SkillType.NORMAL``——**限定技 / 觉醒技 / 主公技除外**，
      锁定技照收（锁定是另一条轴，数据侧仍是 ``NORMAL``）。不合格的**列出来但置灰**
      （带完整描述、点不动），玩家才看得出"这张牌为什么不能用"。

    投影的外观组件（`sgs_web.projection` 的 ``AVATAR`` 构造器）与引擎自己的候选面
    （:func:`eligible_skill_ids`）读**同一份**判据，前端不抄第二份。
    """
    hero = hero_by_id(hero_id, pack_id=pack_id)
    if hero is None:
        return ()
    return tuple(
        # 票 07：候选 id 走数据层唯一派生口——条目可能带显式 skill_id（引用别的武将名下
        # 已登记的技能），按短 id 重拼会给出一个不在 CATALOG 里的幽灵 id，化身牌就选不到。
        (hero_card_skill_id(hero, skill), skill.skill_type is SkillType.NORMAL)
        for skill in hero.skills
        if skill.granted_by is None
    )


def eligible_skill_ids(hero_id: str, *, pack_id: str) -> tuple[str, ...]:
    """一张化身牌上**合格**的技能（数据文件序）——化身①/② 的候选闭集。"""
    return tuple(
        skill_id for skill_id, eligible in avatar_skill_rows(hero_id, pack_id=pack_id) if eligible
    )


def _pairs(state: Any, owner: int) -> tuple[tuple[str, str], ...]:
    """（化身牌 id, 合格技能 id）的全部组合，按手里化身牌的顺序。"""
    out: list[tuple[str, str]] = []
    for hero_id in tuple(state.player(owner).avatar_cards):
        for skill_id in eligible_skill_ids(hero_id, pack_id=state.pack_id):
            out.append((hero_id, skill_id))
    return tuple(out)


def skill_name_on(hero: Any, skill_id: str) -> str:
    """技能 id → 它在这张**武将牌**上的中文名（查不到 ⇒ 短 id）。

    数据表是唯一真源：询问的选项文本（``武将名·技能名``）与外观组件的第二行都读这一处，
    谁都不在自己那侧再写一遍"短 id 反查"。
    """
    short_id = skill_id.rsplit(".", 1)[-1]
    return next((item.name for item in hero.skills if item.id == short_id), short_id)


def _option_texts(state: Any, pairs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """选项显示文本：``武将名·技能名``（引擎给，前端不抄第二份）。"""
    texts: dict[str, str] = {}
    for hero_id, skill_id in pairs:
        hero = hero_by_id(hero_id, pack_id=state.pack_id)
        if hero is None:
            continue
        texts[skill_id] = f"{hero.name}·{skill_name_on(hero, skill_id)}"
    return texts


def current_avatar_skill(state: Any, owner: int) -> str | None:
    """当前“视为拥有”的那条化身技能：授予表里、且属于手里某张化身牌的合格技能。

    不另存一份字段（票 02 的口径）：它可由 `avatar_cards` + `granted_skills` 派生；
    左慈今天没有别的“获得技能”来源，派生判据不会误命中。
    """
    held = {skill_id for _hero_id, skill_id in _pairs(state, owner)}
    for skill_id in state.player(owner).granted_skills:
        if skill_id in held:
            return skill_id
    return None


def _has_huashen(state: Any, owner: int) -> bool:
    """座位此刻是否**在解析中**拥有【化身】（失效 / 失去都不算）。"""
    return any(
        instance.owner == owner and instance.skill_id == HUASHEN
        for instance in resolve_instances(state)
    )


def _apply(host: Any, frame: Any, owner: int, skill_id: str) -> None:
    """换化身：失去旧技能（可逆）+ 获得新技能 + 写性别 / 势力。选同一条 ⇒ 零动作。"""
    current = current_avatar_skill(host.state, owner)
    if current == skill_id:
        return
    if current is not None:
        host.revoke_skill(owner, current, frame=frame)
    hero_id = next(
        hero_id
        for hero_id, candidate in _pairs(host.state, owner)
        if candidate == skill_id
    )
    host.grant_skill(owner, skill_id, frame=frame)
    hero = hero_by_id(hero_id, pack_id=host.state.pack_id)
    if hero is not None:
        host.set_gender(owner, hero.gender)
        host.set_kingdom(owner, hero.kingdom)


def _ask_choice(
    host: Any, frame: Any, owner: int, *, skill_id: str, timing: Timing, optional: bool
) -> Any:
    """发一次“选化身牌的一个技能”的选项询问；返回答案 / ``"stay"`` / ``None``（无候选）。"""
    pairs = _pairs(host.state, owner)
    options = tuple(dict.fromkeys(skill for _hero, skill in pairs))
    if not options and not optional:
        # 强制段（化身①）没有合法答案 ⇒ 整条不出现（死路不算兜底）。
        return None
    answer = host.ask_option(
        frame,
        owner,
        timing,
        skill_id=skill_id,
        options=options,
        optional=optional,
        option_texts=_option_texts(host.state, pairs),
    )
    if host.suspended(answer):
        return "stay"
    choice = getattr(answer, "choice", None)
    return None if choice is None else str(choice)


# ---------------------------------------------------------------------------
# 化身①：游戏开始时（强制段：抽两张 + 选一个技能）
# ---------------------------------------------------------------------------


def _huashen_start(host: Any, frame: Any) -> Any:
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_HUASHEN_ASKED):
        return None
    if _HUASHEN_DRAWN not in frame.data:
        # 抽牌不幂等：一次性抽两张写进帧私有，重入不重抽（【天成】同款）。
        drawn = host.draw_hero_cards(2)
        host.gain_avatar_cards(owner, tuple(drawn))
        frame.data[_HUASHEN_DRAWN] = tuple(drawn)
    choice = _ask_choice(
        host, frame, owner, skill_id=_HUASHEN_START, timing=Timing.GAME_START, optional=False
    )
    if choice == "stay":
        return "stay"
    if choice is None:
        frame.data[_HUASHEN_ASKED] = True
        return None
    _apply(host, frame, owner, choice)
    frame.data[_HUASHEN_ASKED] = True
    return None


# ---------------------------------------------------------------------------
# 化身②：自己的回合开始 / 结束（可以重新进行一次；只有库空才不问）
# ---------------------------------------------------------------------------


def _again_condition(host: Any, frame: Any) -> bool:
    """“关于我”自校：**自己的**回合开始 / 结束（`source` = 当前回合角色）。"""
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _again_options(state: Any, frame: Any, owner: int, answers: Any) -> tuple[str, ...]:
    """候选 = 手里全部化身牌的全部合格技能（含当前那条）；库空 ⇒ 空 ⇒ 整条不出现。"""
    del frame, answers
    return tuple(dict.fromkeys(skill for _hero, skill in _pairs(state, owner)))


def _again_effect(host: Any, frame: Any, answers: Any) -> None:
    """提交点：选中哪条就换哪条；没有答案（这一步取消）⇒ 零动作。"""
    choice = answers.get("skill")
    if choice is None:
        return None
    _apply(host, frame, int(frame.data["skill_owner"]), str(choice))
    return None


HUASHEN_AGAIN_FLOW = SkillFlow(
    name="huashen",
    steps=(PickOption(options=_again_options, key="skill", optional=True),),
    effect=_again_effect,
)


# ---------------------------------------------------------------------------
# 新生：受到 1 点伤害后（逐点；有【化身】才能发动）
# ---------------------------------------------------------------------------


def _xinsheng_condition(host: Any, frame: Any) -> bool:
    """“关于我”自校（**受伤的是持有者**）+ 牌面前提“若你有技能‘化身’”。

    与【遗计】/【奸雄】同一条 ``_hurt_owner`` 判据（读条件期写好的 ``MATCH_OWNER_KEY``
    与帧的 ``target``）；“有没有化身”读**存活实例**（被失效 / 已失去都不算）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("target") != owner:
        return False
    return _has_huashen(host.state, int(owner))


def _xinsheng_effect(host: Any, frame: Any, answers: Any) -> None:
    """零步流水线的提交点：抽一张武将牌进化身库（抽到的真名当场进屏蔽集合）。"""
    del answers
    owner = int(frame.data["skill_owner"])
    drawn = tuple(host.draw_hero_cards(1))
    host.gain_avatar_cards(owner, drawn)
    return None


XINSHENG_FLOW = SkillFlow(name="xinsheng", steps=(), effect=_xinsheng_effect)


# ---------------------------------------------------------------------------
# 定义
# ---------------------------------------------------------------------------


def zuoci_skill_definitions() -> tuple[SkillDef, ...]:
    """左慈两条技能的定义（山包；票 04）。

    两条都是**非锁定技**（牌面写“可以” / “选择”），但有三条钩子是**强制段**
    （`forced=True`：不问"是否发动"，由回调自己发选择询问）——牌面没有“可以”：
    化身① 的抽牌 + 选择、化身② 的“重新进行”入口。新生是真正的可选段（牌面“可以”），
    条件 = 有【化身】。
    """
    huashen = SkillDef(
        skill_id=HUASHEN,
        locked=False,
        carrier=CarrierKind.HERO,
        hooks=(
            Hook(Timing.GAME_START, handler=_huashen_start, forced=True),
            Hook(
                Timing.TURN_START,
                flow=HUASHEN_AGAIN_FLOW,
                condition=_again_condition,
                forced=True,
            ),
            Hook(
                Timing.TURN_END,
                flow=HUASHEN_AGAIN_FLOW,
                condition=_again_condition,
                forced=True,
            ),
        ),
        requirement=HUASHEN_REQUIREMENT,
        # 化身牌只有自己可见（用户 2026-11-13）⇒ 显示面由「技能外观组件」负责（票 05）：
        # 中键 / 「只查看」开只读弹窗，发动询问复用同一个组件（第一行化身牌、第二行技能）。
        appearance=AppearanceKind.AVATAR,
    )
    xinsheng = SkillDef(
        skill_id=XINSHENG,
        locked=False,
        carrier=CarrierKind.HERO,
        hooks=(
            Hook(
                Timing.DAMAGE_AFTER_TAKE,
                flow=XINSHENG_FLOW,
                condition=_xinsheng_condition,
                per_point=True,
            ),
        ),
        requirement=XINSHENG_REQUIREMENT,
    )
    return (huashen, xinsheng)
