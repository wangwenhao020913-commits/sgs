r"""**否定分类**（M6 票 07；spec D15）与界甘宁【奋威】。

规则原文：§十九 L2128–L2132（界甘宁【奇袭】/【奋威】）。

**四层否定各自实现、不共用类型**（D15 / 票面"不做统一抽象"）：目标产生前取消 /
目标级无效 / 事件帧无效 / 响应窗口不产生。本模块只装**目标级无效**这一层的
M6 载体（界甘宁【奋威】）；其余三层的落点分别是
:mod:`sgs.skills.target_pool`（目标产生前取消，M6 无载体）、
票 08 的【奇才】②（事件帧无效）与 :mod:`sgs.skills.active` 的界貂蝉【离间】
（响应窗口不产生）。四层的**审计清单**住 ``tests/test_negation_sites.py``：
新增一个未登记的否定站点会判红（与 ``tests/test_card_family_sites.py`` 同姿态）。

**目标级无效（【奋威】）**：

    【奋威】限定技，当一张锦囊牌指定至少两个目标后，你可以令此牌对其中任意名目标角色无效。

"无效"在 :mod:`GLOSSARY` 里就是"牌已使用、但对该目标的效果被否定"：目标**照常被指定**
（仍进 ``frame.data["targets"]``、仍参与"目标数"类计算），但轮到它自己的
"使用结算开始时"就**终止对该目标的结算**——因此它**不会**进入"生效前"、也没有无懈窗口。
本模块只做两件事：

- **记录**：把被选中的目标写进**本次使用帧**的私有键 :data:`TARGET_INEFFECTIVE_KEY`；
  ``use_push_resolve`` 的 ``dict(frame.data)`` 把它带进 RESOLVE 帧（与 ``designations``
  同一继承面），故"哪一次使用的哪几个目标无效"随帧存在、不落任何全局状态；
- **读取**：引擎的 :func:`~sgs.engine.steps.resolve_start` 在 C1 调
  :func:`target_is_ineffective`，命中则直接推进游标（与既有的"桃园对未受伤角色无效 /
  仁王盾对黑色【杀】无效"走同一个终止点，只是判据来源是帧而不是修饰器链）。

**裁定**（`06` §5「程序化约定与偏离一手」登记）：「其中任意名目标角色」= 此牌的**全部**
目标角色，**含发动者本人**（当他是目标之一时）——牌面没有"其他角色"的限定，
故【桃园结义】【五谷丰登】这类包含自己的锦囊也可以把自己选为无效目标。

限定技的"整局一次"由 :func:`~sgs.skills.catalog._dispatch` 统一落"已用"位，本模块
不重复记账（与【替身】同姿态）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import CarrierKind
from sgs.state import Pass
from sgs.timing import Timing

from .catalog import Hook, SkillDef
from .flow import option_seat, seat_choice

__all__ = [
    "FENWEI",
    "TARGET_INEFFECTIVE_KEY",
    "mark_targets_ineffective",
    "negation_skill_definitions",
    "target_is_ineffective",
]

FENWEI = hero_skill_id("jie_ganning", "fenwei")
"""界甘宁【奋威】的全限定技能 id（§十九 L2130–L2131）。"""

TARGET_INEFFECTIVE_KEY = "ineffective_targets"
"""帧私有：**本次使用**里被目标级否定的目标座位（有序 ``tuple[int, ...]``）。

写在 USE 帧上、随 ``use_push_resolve`` 的 ``dict(frame.data)`` 继承进 RESOLVE 帧；
**刻意不登记**在 :data:`sgs.engine.steps._PER_TARGET_FRAME_KEYS`：这一层是"整次使用
有哪几个目标无效"，逐目标游标推进时不能把它清掉（后面的目标还要读）。
"""

FENWEI_DONE_KEY = "fenwei_done"
"""帧私有：【奋威】的选择循环已经走完（重入不再进入循环）。"""


def mark_targets_ineffective(frame: Any, targets: tuple[int, ...]) -> None:
    """把 ``targets`` 记为**本次使用**的无效目标（幂等；保持首次写入的顺序）。

    这是"目标级无效"的**唯一写入口**：只有技能层写它，引擎侧只读，故这一层的语义
    不会在派发 / 结算两处各长一份。
    """
    frame.data[TARGET_INEFFECTIVE_KEY] = tuple(dict.fromkeys(targets))


def target_is_ineffective(frame: Any, target: int) -> bool:
    """这次使用是否把这个目标判成**无效**（引擎 C1 的读取口）。"""
    return int(target) in frame.data.get(TARGET_INEFFECTIVE_KEY, ())


def _fenwei_targets(frame: Any) -> tuple[int, ...]:
    """本次使用指定的**全部**目标（含发动者本人——见模块头的裁定）。"""
    return tuple(int(target) for target in frame.data.get("targets", ()))


def _fenwei_condition(host: Any, frame: Any) -> bool:
    """【奋威】的触发条件：一张**锦囊牌**指定了**至少两个**目标。

    条件只关于**这张牌**，不关于技能持有者——任何角色使用的锦囊都该问他一声
    （故本条是 ``tests/test_hero_catalog.py`` 的持有者自校豁免表里第一条：
    "关于这张牌"的触发条件没有"我"可自校；豁免理由写在表里）。
    牌名大类读数据层 :func:`sgs.data.card_category`（虚拟锦囊同样命中），不抄牌名清单。
    """
    card = frame.data.get("card")
    if card is None or len(_fenwei_targets(frame)) < 2:
        return False
    return card_category(host.state.pack_id, card.type) is Category.TRICK


def _fenwei_handler(host: Any, frame: Any) -> Any:
    """【奋威】的执行体：逐问"还要令这个目标无效吗"，选完把集合落进帧。

    一次询问一个目标（选项 ``seat:N``，与【突袭】的座位步同一形态）；**至少选一名**
    （第一次询问不可 ``Pass``——答"不发动"的出口是发动前的询问），选满全部目标即收尾。
    重入纪律：每个轮次一个独立的 ``skill_id``（``fenwei:<序号>``），故挂起后从头重跑
    循环时命中的是**同一批**已存答案，不会重问、也不会把同一目标记两次；
    写入 :data:`TARGET_INEFFECTIVE_KEY` 是幂等的。
    """
    owner = frame.data["skill_owner"]
    if frame.data.get(FENWEI_DONE_KEY):
        return None
    targets = _fenwei_targets(frame)
    chosen: list[int] = []
    while True:
        pool = tuple(target for target in targets if target not in chosen)
        if not pool:
            break
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_AFTER_DESIGNATE_TARGET,
            skill_id=f"fenwei:{len(chosen)}",
            options=tuple(seat_choice(target) for target in pool),
            # "任意名"：至少一名；选满之后循环自然结束。
            optional=bool(chosen),
        )
        if host.suspended(answer):
            return "stay"
        if isinstance(answer, Pass):
            break
        chosen.append(option_seat(answer))
    frame.data[FENWEI_DONE_KEY] = True
    if chosen:
        mark_targets_ineffective(frame, tuple(chosen))
    return None


def negation_skill_definitions() -> tuple[SkillDef, ...]:
    """否定族并入 :data:`sgs.skills.CATALOG` 的技能定义（本票只有【奋威】）。

    ``skill_type = LIMITED`` ⇒ 整局一次（派发环的"已用"位）；``locked = False`` ⇒
    技能面写了"你可以"，先问是否发动。挂点是 B5"指定目标后"
    （``Timing.USE_AFTER_DESIGNATE_TARGET``）——目标集合此刻已经定格、还没有开始结算，
    正是"令其中任意名目标无效"的落点。
    """
    return (
        SkillDef(
            skill_id=FENWEI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LIMITED,
            hooks=(
                Hook(
                    Timing.USE_AFTER_DESIGNATE_TARGET,
                    handler=_fenwei_handler,
                    condition=_fenwei_condition,
                ),
            ),
            requirement="令此牌对其中任意名目标角色无效（限定技：整局一次）",
        ),
    )
