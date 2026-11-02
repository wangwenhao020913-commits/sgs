"""火包·移动版：典韦移动版【强袭】。

牌面（`D:\\SGS_HEROS\\神话再临-火包\\典韦\\技能.txt`，逐字）：

    出牌阶段限一次，你可以失去1点体力或者弃置一张武器牌，然后对你攻击范围内的一名
    其他角色造成1点伤害。

用户 2026-11-02 签的三条落地判据（`huo-dianwei-mobile` 票 01 §5）：

1. **平台差异（A1）**：官方 OL 典韦页的【强袭】是另一条技能（限两次 / 没有攻击范围 /
   每名目标每回合一次）——本仓按**移动版**实现，武将名「典韦移动版」；
2. **代价与目标的先后（D1）**：「然后」⇒ 先付代价、再定目标，目标池按**代价付掉之后**的
   攻击范围算（与【流离】W4-2 同一条判据）；
3. **收窄（O1）**：没有武器牌时「弃置一张武器牌」不出现（不置灰、不另开 UI 面）。

三条验收全走外部缝：询问 / 记录流 / 终局状态。
"""

from __future__ import annotations

import pytest

from sgs.data import Kingdom, load_content_pack, load_hero_tables
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    Phase,
    QueryKind,
    SelectCards,
    SelectOption,
    SlotKind,
)
from sgs.state import FrameKind
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 档：本文件只用它的武器牌与牌堆序（数据文件是唯一真源）。"""

QIANGXI = "hero.dianwei_mobile.qiangxi"
"""典韦移动版【强袭】的全限定 id。"""

HERO = "dianwei_mobile"

LOSE_HP = "qiangxi_lose_hp"
DISCARD_WEAPON = "qiangxi_discard_weapon"


def _cards(type_id: str) -> list[int]:
    """某牌名的全部 ``card_id``（按数据表顺序）。"""
    return cards_of_type(PACK, type_id)


CROSSBOW = _cards("crossbow")[0]
"""攻击范围 1 的武器（弃掉它不会改变攻击范围）。"""
QINGGANG = _cards("qinggang_sword")[0]
"""攻击范围 2 的武器：4 人桌上够得到对面第二家（距离 2）。"""


# ---------------------------------------------------------------------------
# 脚本参与者：点技能 → 选代价（＋弃哪张武器）→ 选目标；其余一律放弃
# ---------------------------------------------------------------------------


class _QiangxiDriver:
    """【强袭】的脚本玩家。

    ``cost`` / ``weapon`` / ``target`` 指定每一步的答案；``decline`` 在代价步送 ``Pass``
    （「可以」的放弃出口）。``play_queries`` / ``cost_options`` / ``target_options`` 是
    断言用的读数：每一步实际收到了什么。
    """

    def __init__(
        self,
        *,
        cost: str = LOSE_HP,
        weapon: int | None = None,
        target: int | None = None,
        times: int = 1,
        decline: bool = False,
    ) -> None:
        self.cost = cost
        self.weapon = weapon
        self.target = target
        self.times = times
        self.decline = decline
        self.play_queries = 0
        self.activations = 0
        self.saw_candidate: list[bool] = []
        self.cost_options: list[tuple[str, ...]] = []
        self.target_options: list[tuple[str, ...]] = []
        self.extra_asks: list[str] = []

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            self.play_queries += 1
            candidate = next(
                (
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill) and action.skill_id == QIANGXI
                ),
                None,
            )
            self.saw_candidate.append(candidate is not None)
            if candidate is not None and self.times > 0:
                self.times -= 1
                self.activations += 1
                return candidate
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if query.kind == QueryKind.SELECT_OPTION:
            skill = query.skill_id or ""
            options = tuple(
                action.choice
                for action in query.legal_actions
                if isinstance(action, SelectOption)
            )
            if skill.endswith(":option"):
                self.cost_options.append(options)
                if self.decline:
                    return next(
                        action
                        for action in query.legal_actions
                        if isinstance(action, Pass)
                    )
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectOption) and action.choice == self.cost
                )
            if skill.endswith(":target"):
                self.target_options.append(options)
                if self.target is not None:
                    wanted = f"seat:{self.target}"
                    return next(
                        action
                        for action in query.legal_actions
                        if isinstance(action, SelectOption) and action.choice == wanted
                    )
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectOption)
                )
            self.extra_asks.append(skill)
            return next(
                action for action in query.legal_actions if isinstance(action, SelectOption)
            )
        if query.kind == QueryKind.SELECT_CARDS:
            index = self._weapon_slot(query)
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectCards) and action.picks == (index,)
            )
        # 其余（响应 / 濒死求桃 / 触发技的"是否发动"）一律放弃：被测链不被改道。
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]

    def _weapon_slot(self, query) -> int:
        """选面里哪一个槽位是要弃的武器（``weapon`` 给了就按实体牌 id 找）。"""
        for index, slot in enumerate(query.slots):
            if slot.card is None or slot.card.slot is not SlotKind.WEAPON:
                continue
            if self.weapon is None or slot.card.card_id == self.weapon:
                return index
        raise AssertionError(f"选面里没有要弃的武器牌：{[s.card for s in query.slots]}")


def _play_phase_queries(engine) -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.PLAY_PHASE.value
    ]


def _run(state, driver, stop, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_queries(engine, [driver, _Idle()], stop)
    return engine


class _Idle:
    """其他座位：能结束阶段就结束，其余一律放弃。"""

    def respond(self, query):
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _offers_qiangxi(query) -> bool:
    """这一次出牌阶段询问里还有没有【强袭】的候选（不走预发动清单，候选就在 legal_actions）。"""
    return any(
        isinstance(action, ActivateSkill) and action.skill_id == QIANGXI
        for action in query.legal_actions
    )


def _after_the_skill(engine) -> bool:
    """停止条件：技能已结算完、引擎又摆出**第二次**出牌阶段询问（等待下一次行动）。"""
    queries = _play_phase_queries(engine)
    return (
        len(queries) >= 2
        and engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
    )


def _state(*seats: SeatSpec, deck_exclude: tuple[int, ...] = ()):
    """3 人桌（除非调用方自己给座位）：座位 0 是典韦移动版，出牌阶段轮到他。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), *seats],
        deck=deck_excluding(PACK, *deck_exclude),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _damage_deal(engine) -> list[tuple[int | None, tuple[int, ...]]]:
    """``DAMAGE_DEAL`` 时机的 ``(actor, targets)`` 序列。"""
    return [
        (record.actor, tuple(record.targets))
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ]


def _use_frames(engine) -> list[int]:
    """压过的 ``USE`` 帧（技能伤害不该有）。"""
    return [
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.USE
    ]


def _hp_changes(engine) -> list[tuple[int, int, int]]:
    """记录流里的体力变化：``(座位, 变化前, 变化后)``。"""
    return [
        (record.seat, record.before, record.after)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "hp_change"
    ]


# ---------------------------------------------------------------------------
# ① 两条代价分支：失去 1 点体力 / 弃置一张武器牌
# ---------------------------------------------------------------------------


def test_qiangxi_can_pay_by_losing_one_hp() -> None:
    """失去 1 点体力那一档：自己 4 → 3、目标 4 → 3，伤害是 1 点普通伤害。"""
    state = _state(SeatSpec(), SeatSpec())
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert driver.activations == 1
    assert engine.state.player(0).hp == 3, "失去 1 点体力（不是受到伤害）"
    assert engine.state.player(1).hp == 3, "对目标造成 1 点伤害"
    assert _hp_changes(engine) == [(0, 4, 3), (1, 4, 3)], "两条体力变化各一条 delta"
    assert _damage_deal(engine) == [(0, (1,))], "一次 DAMAGE_DEAL 时机、来源是典韦"
    assert not _use_frames(engine), "技能伤害没有渠道牌 ⇒ 不压 USE 帧"


def test_qiangxi_can_pay_by_discarding_a_weapon_card() -> None:
    """弃武器那一档：那张武器牌进弃牌堆、体力一点不失，仍造成 1 点伤害。"""
    weapon = CROSSBOW
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=(weapon,)), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=weapon, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert driver.activations == 1
    assert engine.state.player(0).hp == 4, "弃武器不失去体力"
    assert weapon not in engine.state.player(0).hand
    assert weapon in engine.state.discard, "那张武器牌进弃牌堆"
    assert engine.state.player(1).hp == 3, "仍然造成 1 点伤害"


def test_qiangxi_weapon_can_come_from_the_equipment_zone() -> None:
    """「一张武器牌」= 手牌 ∪ 装备区（默认读法（一）②③）：装备区的武器同样能弃。"""
    weapon = CROSSBOW
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, equip=(weapon,)), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=weapon, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert not engine.state.player(0).equip, "装备区的武器被弃掉"
    assert weapon in engine.state.discard
    assert engine.state.player(1).hp == 3


# ---------------------------------------------------------------------------
# ② 目标池：攻击范围内、且按**代价付掉之后**的范围算（R2 的落点）
# ---------------------------------------------------------------------------


def test_qiangxi_only_offers_targets_within_attack_range() -> None:
    """没武器（范围 1）⇒ 4 人桌上只能选相邻的两家（距离 1），对面第二家（距离 2）不在池里。"""
    state = _state(SeatSpec(), SeatSpec(), SeatSpec())
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    _run(state, driver, _after_the_skill)

    assert driver.target_options == [("seat:1", "seat:3")], "池 = 攻击范围内的其他存活角色"


def test_qiangxi_range_is_recomputed_after_the_weapon_is_discarded() -> None:
    """**R2**：青釭剑（范围 2）撑起距离 2 ⇒ 弃掉它之后目标池回落，距离 2 的那家不可选。"""
    weapon = QINGGANG
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, equip=(weapon,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=weapon, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert driver.target_options == [("seat:1", "seat:3")], (
        "弃掉撑范围的武器后，距离 2 的座位 2 不在目标池里（规则读的是付完之后的场况）"
    )
    assert engine.state.player(2).hp == 4, "座位 2 没挨打"
    assert engine.state.player(1).hp == 3


def test_qiangxi_weapon_from_hand_keeps_the_range() -> None:
    """弃的是**手牌**里的武器 ⇒ 装备区的青釭剑还在，距离 2 的座位仍在池里。

    判据落在 `attacks_range(excluding=…)` 的**逐牌**语义上：排掉的只是这一张，
    不是"没有武器 = 1"（抄一份公式就会在这里露馅）。
    """
    weapon = QINGGANG
    spare = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=(spare,), equip=(weapon,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, weapon, spare),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=spare, target=2)

    engine = _run(state, driver, _after_the_skill)

    assert driver.target_options == [("seat:1", "seat:2", "seat:3")], "范围仍是 2"
    assert list(engine.state.player(0).equip) == [weapon], "装备区的武器没动"
    assert engine.state.player(2).hp == 3, "距离 2 的座位挨打"


# ---------------------------------------------------------------------------
# ③ 代价选项的闭集（O1 收窄）与「可以」的放弃出口
# ---------------------------------------------------------------------------


def test_qiangxi_narrows_the_cost_options_without_a_weapon() -> None:
    """没有武器牌 ⇒ 只剩「失去1点体力」（不列、不置灰，`_shensu_options` 的同一姿态）。"""
    state = _state(SeatSpec(), SeatSpec())
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    _run(state, driver, _after_the_skill)

    assert driver.cost_options == [(LOSE_HP,)]


def test_qiangxi_offers_both_cost_options_with_a_weapon() -> None:
    """有武器牌 ⇒ 两项都列出，顺序 = 牌面叙述顺序（失去体力在前）。"""
    weapon = CROSSBOW
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=(weapon,)), SeatSpec()],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    _run(state, driver, _after_the_skill)

    assert driver.cost_options == [(LOSE_HP, DISCARD_WEAPON)]


def test_qiangxi_declining_the_cost_changes_nothing() -> None:
    """「可以」的放弃出口 = 代价步的 `Pass`：零副作用，且技能**没被消耗**（同阶段还在池里）。"""
    state = _state(SeatSpec(), SeatSpec())
    driver = _QiangxiDriver(decline=True)

    engine = _run(state, driver, _after_the_skill)

    assert driver.activations == 1
    assert _offers_qiangxi(_play_phase_queries(engine)[-1]), "放弃之后技能仍可发动"
    assert engine.state.player(0).hp == 4, "不失去体力"
    assert engine.state.player(1).hp == 4, "不造成伤害"
    assert _hp_changes(engine) == [], "一条体力 delta 都没有"


# ---------------------------------------------------------------------------
# ④ 次数与候选门
# ---------------------------------------------------------------------------


def test_qiangxi_is_once_per_play_phase() -> None:
    """出牌阶段限一次：结算完之后同阶段不再产出候选。"""
    state = _state(SeatSpec(), SeatSpec())
    driver = _QiangxiDriver(cost=LOSE_HP, target=1, times=2)

    engine = _run(state, driver, _after_the_skill)

    assert not _offers_qiangxi(_play_phase_queries(engine)[-1]), (
        "第二次出牌阶段询问里没有【强袭】"
    )
    assert driver.activations == 1
    assert len(driver.cost_options) == 1, "代价步只问了一次"


def test_qiangxi_is_absent_without_another_living_character() -> None:
    """场上没有别的存活角色 ⇒ 1 点伤害无处可去，候选阶段就不产出（总纪律 1）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO), SeatSpec(alive=False)],
        deck=deck_excluding(PACK),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=LOSE_HP)

    _run(state, driver, _after_the_skill)

    assert driver.saw_candidate == [False]


# ---------------------------------------------------------------------------
# ⑤ 状态在途中翻转：失去体力把自己送走 / 搬牌挂起
# ---------------------------------------------------------------------------


def test_qiangxi_owner_dying_deals_no_damage() -> None:
    """1 体力时失去体力 ⇒ 进濒死；没人救 ⇒ 典韦阵亡，伤害整段不发生。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hp=1, hp_max=4), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert engine.finished or not engine.state.player(0).alive, "典韦阵亡"
    assert engine.state.player(1).hp == 4, "目标一点伤害都没受到"
    assert _hp_changes(engine) == [(0, 1, 0)], "只有那一次失去体力"


def test_qiangxi_survives_a_suspended_move() -> None:
    """弃装备区的武器会内联派发「失去装备后」（【枭姬】）并挂起；续跑后仍要造成伤害。"""
    weapon = _cards("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, equip=(weapon,)), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK, weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(0).granted_skills.append("hero.jie_sunshangxiang.xiaoji")
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=weapon, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert not engine.state.player(0).equip, "武器照旧弃掉（重入不重搬）"
    assert weapon in engine.state.discard
    assert engine.state.player(1).hp == 3, "挂起续跑之后伤害仍然发生"


# ---------------------------------------------------------------------------
# ⑥ 数据面
# ---------------------------------------------------------------------------


def test_dianwei_mobile_hero_is_in_the_default_pool() -> None:
    """数据面：典韦移动版进默认池（显示名「典韦移动版」、真名「典韦」、魏 / 男 / 4）。"""
    heroes = {
        hero.id: hero for table in load_hero_tables() for hero in table.heroes
    }

    hero = heroes[HERO]
    assert hero.name == "典韦移动版"
    assert hero.true_name == "典韦"
    assert hero.kingdom is Kingdom.WEI
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["qiangxi"]
    assert [skill.name for skill in hero.skills] == ["强袭"]
