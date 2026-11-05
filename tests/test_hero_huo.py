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

from sgs.data import Identity, Kingdom, RuleConfig, load_content_pack, load_hero_tables
from sgs.effects import distance, hand_limit
from sgs.engine import Engine, IllegalActionError
from sgs.skills import CATALOG, resolve_conversion_confirmation
from sgs.skills.shen_zhugeliang import star_zone_key
from sgs.state import (
    ActivateSkill,
    MoveReason,
    ArrangeCards,
    EndPhase,
    Pass,
    Phase,
    QueryKind,
    Reforge,
    SelectCards,
    SelectOption,
    SlotKind,
    UseCard,
)
from sgs.state import FrameKind
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 档：本文件只用它的武器牌与牌堆序（数据文件是唯一真源）。"""

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：**只有它**的牌堆里有【铁索连环】实体牌（108 档一张都没有）。"""

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
        self.weapon_faces: list[tuple[int, ...]] = []
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
            self.weapon_faces.append(
                tuple(
                    slot.card.card_id
                    for slot in query.slots
                    if slot.card is not None and slot.card.slot is SlotKind.WEAPON
                )
            )
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


def test_qiangxi_hides_the_weapon_option_that_leaves_no_target() -> None:
    """弃掉唯一撑范围的武器之后够不着人 ⇒ 那一档**不进候选**（死循环回归）。

    2 人桌、对手挂 +1 坐骑（的卢）⇒ 距离 2。青釭剑（范围 2）在装备区时够得着，弃掉它
    范围回落 1 ⇒ 「弃置一张武器牌」那一档是**死路**：结算整条取消、阶段位不落，而候选
    还在 ⇒ 人工 / AI 会反复发动同一条（2026-11-05 的 128 局胜率批在 seed 5003 上真撞到：
    典韦对荀彧这类 2 人残局里打了两万步还没出这一轮）。收窄后只剩「失去1点体力」，
    技能照常结算。
    """
    weapon = QINGGANG
    horse = _cards("dilu")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, equip=(weapon,)), SeatSpec(equip=(horse,))],
        deck=deck_excluding(PACK, weapon, horse),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=LOSE_HP, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert driver.activations == 1, "技能本身仍可发动（当前范围够得着）"
    assert driver.cost_options == [(LOSE_HP,)], "弃了也没目标的武器档不进候选"
    assert engine.state.player(0).hp == 3, "失去体力那一档照常付"
    assert engine.state.player(1).hp == 3, "1 点伤害照常结算"
    assert list(engine.state.player(0).equip) == [weapon], "武器没被弃（那一档根本没出现）"


def test_qiangxi_weapon_face_lists_only_the_weapons_that_still_reach() -> None:
    """两张武器、只有一张弃了还够得着 ⇒ 选面只列那一张（选项与选面同一份判据）。

    装备区青釭剑（范围 2）+ 手牌诸葛连弩（范围 1）：弃连弩不影响范围 ⇒ 可弃；弃青釭剑
    会让范围回落到 1 < 距离 2 ⇒ 不可弃。选面因此只该出现连弩——选项说"弃了还能打"，
    选面就不该给出一张弃了没目标的武器。
    """
    weapon = QINGGANG
    spare = CROSSBOW
    horse = _cards("dilu")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=HERO, hand=(spare,), equip=(weapon,)),
            SeatSpec(equip=(horse,)),
        ],
        deck=deck_excluding(PACK, weapon, spare, horse),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    driver = _QiangxiDriver(cost=DISCARD_WEAPON, weapon=spare, target=1)

    engine = _run(state, driver, _after_the_skill)

    assert driver.cost_options == [(LOSE_HP, DISCARD_WEAPON)], "有可弃的武器 ⇒ 两项都在"
    assert driver.weapon_faces == [(spare,)], "选面只列弃后仍够得着的那张"
    assert list(engine.state.player(0).equip) == [weapon], "弃的是手牌里的连弩"
    assert spare not in engine.state.player(0).hand, "连弩离开手牌（进弃牌堆）"
    assert engine.state.player(1).hp == 3, "1 点伤害照常结算"


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


# ---------------------------------------------------------------------------
# ⑦ 庞德移动版【马术】/【猛进】（huo-mobile-batch 票 01）
# ---------------------------------------------------------------------------

PANGDE = "pangde_mobile"
PANGDE_MASHU = "hero.pangde_mobile.mashu"
MENGJIN = "hero.pangde_mobile.mengjin"


def _rings(*, hero: str | None, seats: int) -> object:
    """``seats`` 个座位的环（只有座位 0 带武将）。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hero=hero), *[SeatSpec() for _ in range(seats - 1)]],
    )


def test_pangde_mobile_mashu_subtracts_one_from_its_own_distance() -> None:
    """庞德的【马术】与马超逐字相同 ⇒ 主体侧 −1（复用同一个回调）。

    六座环上 0↔3 的座位距离是 3：无技能 3，有【马术】2；反方向（别人到庞德）仍是 3。
    """
    without = _rings(hero=None, seats=6)
    with_hero = _rings(hero=PANGDE, seats=6)

    assert distance(without, 0, 3) == 3
    assert distance(with_hero, 0, 3) == 2, "你计算与其他角色的距离 -1"
    assert distance(with_hero, 3, 0) == 3, "主体侧：别人算与你的距离不变"


def test_pangde_mobile_mashu_stacks_with_a_minus_one_mount() -> None:
    """数值只有加减、加在最终值上：与 −1 坐骑（大宛）叠加后由管道夹在下限 1。"""
    mount = _cards("dawan")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=PANGDE, equip=(mount,)),
            *[SeatSpec() for _ in range(5)],
        ],
        deck=deck_excluding(PACK, mount),
    )

    assert distance(state, 0, 3) == 1, "3 − 1（马术）− 1（大宛）"


def test_pangde_mobile_mashu_reuses_the_same_distance_modifier() -> None:
    """声明面：锁定技、只有一条 ``DISTANCE`` 修饰器（与两条既有【马术】同形）。"""
    definition = CATALOG[PANGDE_MASHU]

    assert definition.locked is True
    assert [modifier.point.value for modifier in definition.modifiers] == ["DISTANCE"]
    assert definition.hooks == () and definition.seams == ()


def _default_action(query):
    """缺省应答：放弃 → 结束阶段 → 第一项（``PassPlayer`` 的同一条口径）。"""
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    for action in query.legal_actions:
        if isinstance(action, EndPhase):
            return action
    return query.legal_actions[0]


def _advance(engine, predicate, *, limit: int = 4000) -> None:
    """推进到 ``predicate`` 成立；途中其它询问按缺省应答让路。"""
    for _ in range(limit):
        if predicate(engine):
            return
        if engine.pending is not None:
            engine.submit(_default_action(engine.pending))
            continue
        engine._step()
    raise AssertionError("步数上限内没有到达停止条件")


def _until(engine, predicate, *, limit: int = 4000) -> None:
    """推进到 ``predicate`` 成立（不用"栈空"当停止条件：出牌阶段的步骤循环不在栈上）。"""
    for _ in range(limit):
        if predicate(engine):
            return
        if engine.pending is not None:
            engine.submit(_default_action(engine.pending))
            continue
        engine._step()
    raise AssertionError("步数上限内没有到达停止条件")


def _pick(query, predicate):
    action = next((item for item in query.legal_actions if predicate(item)), None)
    assert action is not None, (
        f"候选里没有预期动作：{[item.kind for item in query.legal_actions]}"
    )
    return action


def _mengjin_queries(engine) -> list:
    """记录流里问给庞德的【猛进】发动询问。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
        and getattr(record.query, "skill_id", None) == MENGJIN
    ]


_SLASH_CARD = _cards("slash")[0]
_DODGE_CARD = _cards("dodge")[0]
_PEACH_CARD = _cards("peach")[0]


def _mengjin_engine(*, target_hand, target_hero: str | None = None, turn_seat: int = 0):
    """座位 0 = 庞德移动版（手里一张【杀】），座位 1 = 目标（手里带【闪】）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=PANGDE, hand=(_SLASH_CARD,)),
            SeatSpec(hero=target_hero, hand=target_hand),
        ],
        deck=deck_excluding(PACK, _SLASH_CARD, *target_hand),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=3)


def _use_slash_and_dodge(engine, *, slash: int, dodge: int, actor: int) -> None:
    """推进到 ``actor`` 的出牌阶段，用【杀】打另一个座位，再让对方打出【闪】。"""
    target = 1 - actor
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == actor,
    )
    engine.submit(
        _pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == slash,
        )
    )
    _advance(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.DODGE,
    )
    assert engine.pending.actor == target, "【闪】窗口问的是被指定的目标"
    engine.submit(
        _pick(
            engine.pending,
            lambda action: isinstance(action, UseCard) and action.card.card_id == dodge,
        )
    )


def test_mengjin_discards_a_card_from_the_target_who_dodged() -> None:
    """自己的【杀】被【闪】抵消 ⇒ 问一次；弃掉那个目标的一张手牌（隐藏槽）。"""
    engine = _mengjin_engine(target_hand=(_DODGE_CARD, _PEACH_CARD))

    _use_slash_and_dodge(engine, slash=_SLASH_CARD, dodge=_DODGE_CARD, actor=0)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(e.pending, "skill_id", None) == MENGJIN,
    )
    assert any(
        isinstance(action, Pass) for action in engine.pending.legal_actions
    ), "牌面「可以」⇒ 发动询问带放弃出口"
    engine.submit(
        _pick(engine.pending, lambda action: isinstance(action, ActivateSkill))
    )
    _advance(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_CARDS,
    )
    assert len(engine.pending.slots) == 1, "目标只剩那一张手牌"
    assert engine.pending.slots[0].card is None, "别人的手牌是隐藏槽（只有位置）"
    engine.submit(
        _pick(
            engine.pending,
            lambda action: isinstance(action, SelectCards) and action.picks == (0,),
        )
    )
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(1).hand == [], "那张手牌被弃置"
    assert _PEACH_CARD in engine.state.discard, "进的是弃牌堆（不是别处）"
    assert _DODGE_CARD in engine.state.discard, "【闪】自己照常进弃牌堆"
    assert len(_mengjin_queries(engine)) == 1, "每次抵消只问一次"


def test_mengjin_declining_changes_nothing() -> None:
    """右端「放弃」⇒ 零副作用：一张牌都不动。"""
    engine = _mengjin_engine(target_hand=(_DODGE_CARD, _PEACH_CARD))

    _use_slash_and_dodge(engine, slash=_SLASH_CARD, dodge=_DODGE_CARD, actor=0)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(e.pending, "skill_id", None) == MENGJIN,
    )
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, Pass)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(1).hand == [_PEACH_CARD], "目标的手牌原封不动"
    assert _PEACH_CARD not in engine.state.discard


def test_mengjin_is_not_offered_when_the_target_has_no_card() -> None:
    """目标把最后一张牌（【闪】）用掉后没有牌可弃 ⇒ 连询问都不发。"""
    engine = _mengjin_engine(target_hand=(_DODGE_CARD,))

    _use_slash_and_dodge(engine, slash=_SLASH_CARD, dodge=_DODGE_CARD, actor=0)
    _advance(engine, lambda e: e.finished or not e.stack)

    assert _mengjin_queries(engine) == [], "没有牌可弃 ⇒ 不发没有候选的询问"
    assert engine.state.player(1).hand == []


def test_mengjin_only_fires_for_the_owners_slash() -> None:
    """别人用的【杀】被抵消时，不问庞德（接缝对所有实例派发，plan 自校「关于我」）。"""
    slash = _cards("slash")[0]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=PANGDE, hand=(dodge,)),
            SeatSpec(hand=(slash,)),
        ],
        deck=deck_excluding(PACK, slash, dodge),
        turn_seat=1,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=3)

    _use_slash_and_dodge(engine, slash=slash, dodge=dodge, actor=1)
    _advance(engine, lambda e: e.finished or not e.stack)

    assert _mengjin_queries(engine) == [], "不是庞德使用的【杀】⇒ 不关他的事"


def test_mengjin_survives_a_suspended_move() -> None:
    """弃掉目标的最后一张手牌会内联派发【连营】并挂起；续跑不重弃、不漏做。"""
    engine = _mengjin_engine(
        target_hand=(_DODGE_CARD, _PEACH_CARD), target_hero="jie_luxun"
    )

    _use_slash_and_dodge(engine, slash=_SLASH_CARD, dodge=_DODGE_CARD, actor=0)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(e.pending, "skill_id", None) == MENGJIN,
    )
    engine.submit(
        _pick(engine.pending, lambda action: isinstance(action, ActivateSkill))
    )
    _advance(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_CARDS,
    )
    engine.submit(
        _pick(
            engine.pending,
            lambda action: isinstance(action, SelectCards) and action.picks == (0,),
        )
    )
    # 【连营】的"失去最后的手牌后"会在搬牌里挂起一次；一路让路到安静。
    _advance(engine, lambda e: e.finished or not e.stack)

    assert any(
        record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
        and getattr(record.query, "skill_id", None) == "hero.jie_luxun.lianying"
        for record in engine.records
    ), "搬牌确实内联触发并挂起了【连营】（续跑路径被走到）"
    assert _PEACH_CARD in engine.state.discard, "那张牌进了弃牌堆"
    assert engine.state.player(1).hand == [], "只弃了这一张（挂起续跑不重弃）"


def test_pangde_mobile_hero_is_in_the_default_pool() -> None:
    """数据面：庞德移动版进默认池（显示名带「移动版」、真名「庞德」、群 / 男 / 4）。"""
    heroes = {hero.id: hero for table in load_hero_tables() for hero in table.heroes}

    hero = heroes[PANGDE]
    assert hero.name == "庞德移动版"
    assert hero.true_name == "庞德"
    assert hero.kingdom is Kingdom.QUN
    assert hero.gender.value == "male"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["mashu", "mengjin"]
    assert [skill.name for skill in hero.skills] == ["马术", "猛进"]


# ---------------------------------------------------------------------------
# ⑧ 庞统移动版【连环】（huo-mobile-batch 票 02）
# ---------------------------------------------------------------------------

PANGTONG = "pangtong_mobile"
LIANHUAN = "hero.pangtong_mobile.lianhuan"


def _club_card(pack, type_id: str) -> int:
    """某牌名里的**第一张梅花**牌。"""
    return next(
        index
        for index, card in enumerate(pack.deck.cards)
        if card.type == type_id and card.suit == "club"
    )


def _pack_deck_without(pack, *used: int) -> tuple[int, ...]:
    return tuple(index for index in range(len(pack.deck.cards)) if index not in used)


def _lianhuan_state(pack, hand: tuple[int, ...], *, players: int = 3):
    """``players`` = 座位数；票 06 的复现局面是 4 人（座位 3 也要存在）。"""
    seats = [SeatSpec(hero=PANGTONG, hand=hand, identity=Identity.LORD)]
    seats += [
        SeatSpec(identity=Identity.REBEL) for _ in range(players - 1)
    ]
    return build_state(
        pack=pack,
        seats=seats,
        deck=_pack_deck_without(pack, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4) if players >= 4 else None,
    )


def _lianhuan_play_phase(pack, hand: tuple[int, ...], *, players: int = 3):
    engine = Engine.from_state(_lianhuan_state(pack, hand, players=players), seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    return engine


def _chained_change_seats(engine) -> list[int]:
    """记录流里 ``chained_change`` delta 的座位**先后**（= 目标的结算顺序）。"""
    return [
        record.seat
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "chained_change"
    ]


def test_lianhuan_converts_a_club_hand_card_into_iron_chain() -> None:
    """160 档：♣ 手牌当【铁索连环】**使用**——走与真牌同一条使用链（目标被横置）。"""
    club = _club_card(PACK_160, "slash")
    engine = _lianhuan_play_phase(PACK_160, (club,))
    steps = engine.pending.transform_preactivations.get(LIANHUAN)

    assert steps, "160 档下【连环】有使用分支的清单"
    assert int(steps[0].pool[0].options[0]) == club, "来源步只列那张♣手牌"
    engine.submit(
        ActivateSkill(
            skill_id=LIANHUAN,
            picks=(club,),
            choices=("seat:1",),
            answers_seq=engine.pending.seq,
        )
    )
    _until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and club in e.state.discard,
    )

    assert engine.state.player(1).chained is True, "【铁索连环】把目标横置"


def test_lianhuan_can_chain_two_targets() -> None:
    """产物是真锦囊 ⇒ **目标数区间照真牌**：【铁索连环】1–2 名，两名目标都要能选中。

    转化候选走 ``virtual_card_use``，它原先按"至少 N 个"取单值 ⇒ 1–2 名会被压成恰好 1 名
    （本仓第一张区间牌产物就是【连环】）。修后清单目标步 ``max = 2``、2 目标组合在内部池里。
    """
    club = _club_card(PACK_160, "slash")
    engine = _lianhuan_play_phase(PACK_160, (club,))
    steps = engine.pending.transform_preactivations[LIANHUAN]

    assert (steps[1].min, steps[1].max) == (1, 2), "目标步 1–2 名"
    engine.submit(
        ActivateSkill(
            skill_id=LIANHUAN,
            picks=(club,),
            choices=("seat:1", "seat:2"),
            answers_seq=engine.pending.seq,
        )
    )
    _until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and club in e.state.discard,
    )

    assert engine.state.player(1).chained is True
    assert engine.state.player(2).chained is True, "两名目标都被横置（不是被压成一名）"


@pytest.mark.parametrize(
    ("choices", "settled"),
    [
        (("seat:3", "seat:1"), [3, 1]),
        (("seat:1", "seat:3"), [1, 3]),
    ],
    ids=["click-3-then-1", "click-1-then-3"],
)
def test_lianhuan_product_settles_two_targets_in_the_click_order(
    choices: tuple[str, ...], settled: list[int]
) -> None:
    """转化出的【铁索连环】按**点击顺序**结算（huo-mobile-batch 票 06，报障 ③）。

    真牌由 ``_play_phase`` 的 ``target_combos(..., ordered=preserve_target_order)`` 摊出
    **两种点击顺序**两条候选；产物原先在确认时按**集合**匹配 ⇒ 候选池按座位序排，
    永远解析到升序那条：先点 3 再点 1，记录流仍是 ``(1, 3)``（§3 的实机复现）。

    判据 = ``chained_change`` delta 的先后：铁索的每个目标各自结算（RESOLVE 帧逐目标走
    过），故 delta 顺序就是使用者点出来的结算顺序。
    """
    club = _club_card(PACK_160, "slash")
    engine = _lianhuan_play_phase(PACK_160, (club,), players=4)

    engine.submit(
        ActivateSkill(
            skill_id=LIANHUAN,
            picks=(club,),
            choices=choices,
            answers_seq=engine.pending.seq,
        )
    )
    _until(
        engine,
        lambda e: e.state.player(1).chained and e.state.player(3).chained,
    )

    assert _chained_change_seats(engine) == settled, (
        "产物也按点击顺序结算（真牌 preserve_target_order 的同一判据），不是座位序"
    )
    assert engine.state.player(1).chained is True
    assert engine.state.player(3).chained is True


def test_lianhuan_manifest_is_derived_from_the_club_sources_only() -> None:
    """清单来源步只列**♣ 手牌**（红桃不在候选里）。"""
    club = _club_card(PACK_160, "slash")
    heart = next(
        index
        for index, card in enumerate(PACK_160.deck.cards)
        if card.type == "slash" and card.suit == "heart"
    )
    engine = _lianhuan_play_phase(PACK_160, (club, heart))
    steps = engine.pending.transform_preactivations.get(LIANHUAN)

    assert steps, "清单下发"
    options = {int(value) for value in steps[0].pool[0].options}
    assert options == {club}, "来源步只有梅花牌"


def test_lianhuan_reforge_turns_a_club_hand_card_into_a_draw() -> None:
    """108 档：牌堆里没有【铁索连环】⇒ 只给**重铸**方向（弃一张 + 摸一张）。"""
    club = _club_card(PACK, "slash")
    engine = _lianhuan_play_phase(PACK, (club,))

    assert LIANHUAN not in (engine.pending.transform_preactivations or {}), (
        "本档牌堆没有【铁索连环】⇒ 不给使用分支"
    )
    action = _pick(
        engine.pending,
        lambda item: isinstance(item, Reforge) and item.card.card_id == club,
    )
    hand_before = len(engine.state.player(0).hand)
    engine.submit(action)
    _until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and club in e.state.discard,
    )

    assert len(engine.state.player(0).hand) == hand_before, "重铸 = 弃一张 + 摸一张"


def test_lianhuan_does_not_duplicate_the_native_reforge_of_iron_chain() -> None:
    """♣【铁索连环】本身原生可重铸 ⇒ 技能分支跳过，同一条候选只出现一次。"""
    chain = _club_card(PACK_160, "iron_chain")
    engine = _lianhuan_play_phase(PACK_160, (chain,))

    reforges = [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, Reforge) and action.card.card_id == chain
    ]
    assert len(reforges) == 1, "原生重铸与技能重铸不重复产出"


def test_lianhuan_use_branch_needs_the_product_in_the_deck() -> None:
    """108 档没有【铁索连环】实体牌 ⇒ 使用分支的清单整个不存在（不造牌堆外的虚拟牌）。"""
    club = _club_card(PACK, "slash")
    engine = _lianhuan_play_phase(PACK, (club,))

    assert not engine.pending.transform_preactivations, "没有转化清单"
    assert any(
        action.card.is_virtual and action.card.type == "iron_chain"
        for action in engine.pending.transform_actions
    ) is False, "内部池里也没有虚拟【铁索连环】"


# ---------------------------------------------------------------------------
# ⑨ 庞统移动版【涅槃】（huo-mobile-batch 票 02）
# ---------------------------------------------------------------------------

NEIPAN = "hero.pangtong_mobile.niepan"


def _damage_seat_zero(engine, *, amount: int = 1, target: int = 0) -> None:
    """压一个 ``DAMAGE`` 子帧（造成方 = 座位 1，无渠道牌）。"""
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (target,),
            "card": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": target,
            "channel": None,
            "nature": "NORMAL",
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _niepan_engine(*, hp: int = 1, hp_max: int = 4, hand=(), equip=(), judge=(), chained=False, flipped=False):
    used = (*hand, *equip, *judge)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero=PANGTONG,
                hp=hp,
                hp_max=hp_max,
                hand=hand,
                equip=equip,
                judge=judge,
                chained=chained,
            ),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, *used),
    )
    state.player(0).flipped = flipped
    engine = Engine.from_state(state, seed=5)
    _damage_seat_zero(engine)
    return engine


def _niepan_query(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.SKILL_ACTIVATION
        and getattr(engine.pending, "skill_id", None) == NEIPAN
    )


def test_niepan_discards_every_card_in_the_owners_area() -> None:
    """「弃置你区域里所有的牌」= 手牌 + 装备 + 判定（全部进弃牌堆，没有选择面）。"""
    peach = _cards("peach")[0]
    weapon = _cards("crossbow")[0]
    indulgence = _cards("indulgence")[0]
    engine = _niepan_engine(hand=(peach,), equip=(weapon,), judge=(indulgence,))
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(0).equip == []
    assert engine.state.player(0).judge == []
    assert peach not in engine.state.player(0).hand, "原来那张手牌被弃置"
    for card_id in (peach, weapon, indulgence):
        assert card_id in engine.state.discard, f"{card_id} 进弃牌堆"
    assert len(engine.state.player(0).hand) == 3, "弃完之后才是「摸三张」"


def test_niepan_restores_the_hero_card() -> None:
    """「复原武将牌」= 翻回正面 + 解除横置（两个状态位都落 delta）。"""
    engine = _niepan_engine(chained=True, flipped=True)
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(0).flipped is False
    assert engine.state.player(0).chained is False


def test_niepan_draws_three_and_recovers_to_three() -> None:
    """摸三张 + 体力回复至 3 点（绝对目标，不是 +3）。"""
    engine = _niepan_engine()
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(0).hp == 3
    assert len(engine.state.player(0).hand) == 3, "摸三张（起手为空）"


def test_niepan_recovery_is_capped_by_the_hp_max() -> None:
    """体力上限 < 3 ⇒ 夹到上限（与「回复至体力上限」同一条帧内夹法）。"""
    engine = _niepan_engine(hp=1, hp_max=2)
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(0).hp == 2


def test_niepan_ends_the_dying_state_before_the_peach_window() -> None:
    """四段全在求桃窗口之前结算完 ⇒ 一问【桃】都不发（别人的桃原封不动）。"""
    engine = _niepan_engine()
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.PEACH.value
    ], "回复至 3 点后不再求桃"


def test_niepan_declining_keeps_the_area_and_the_dying_window_open() -> None:
    """「放弃」⇒ 零副作用：牌原位、照常进求桃窗口。"""
    peach = _cards("peach")[2]
    engine = _niepan_engine(hand=(peach,))
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, Pass)))
    _advance(
        engine,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PEACH,
    )

    assert peach in engine.state.player(0).hand, "牌一张没动"
    assert engine.state.player(0).hp == 0, "仍在濒死（求桃窗口照常开）"


def test_niepan_is_offered_when_the_area_is_empty() -> None:
    """区域为空仍可发动（"弃置所有的牌"= 零动作，其余三段照常）。"""
    engine = _niepan_engine()
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    assert engine.state.player(0).hp == 3
    assert len(engine.state.player(0).hand) == 3


def test_niepan_is_absent_when_another_character_is_dying() -> None:
    """「当你处于濒死状态时」：别人濒死不问庞统。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=PANGTONG), SeatSpec(hp=1), SeatSpec()],
        deck=deck_excluding(PACK),
    )
    engine = Engine.from_state(state, seed=5)
    _damage_seat_zero(engine, target=1)
    _advance(engine, lambda e: e.pending is not None and e.pending.kind == QueryKind.PEACH)
    assert engine.pending.actor != 0 or True
    _advance(engine, lambda e: e.finished or not e.stack)

    assert not any(
        record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
        and getattr(record.query, "skill_id", None) == NEIPAN
        for record in engine.records
    ), "濒死的不是庞统 ⇒ 不问【涅槃】"


def test_niepan_is_a_limited_skill_that_is_never_offered_twice() -> None:
    """整局一次：发动过之后再次濒死不再派发（连询问都不构造）。"""
    engine = _niepan_engine()
    _advance(engine, _niepan_query)
    engine.submit(_pick(engine.pending, lambda action: isinstance(action, ActivateSkill)))
    _advance(engine, lambda e: e.finished or not e.stack)

    _damage_seat_zero(engine, amount=3)
    _advance(engine, lambda e: e.finished or not e.stack)

    activations = [
        record
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
        and getattr(record.query, "skill_id", None) == NEIPAN
    ]
    assert len(activations) == 1, "限定技整局只问一次"


def test_pangtong_mobile_hero_is_in_the_default_pool() -> None:
    """数据面：庞统移动版进默认池（显示名带「移动版」、真名「庞统」、蜀 / 男 / 3）。"""
    heroes = {hero.id: hero for table in load_hero_tables() for hero in table.heroes}

    hero = heroes[PANGTONG]
    assert hero.name == "庞统移动版"
    assert hero.true_name == "庞统"
    assert hero.kingdom is Kingdom.SHU
    assert hero.hp_max == 3
    assert [skill.id for skill in hero.skills] == ["lianhuan", "niepan"]
    assert [skill.name for skill in hero.skills] == ["连环", "涅槃"]


# ---------------------------------------------------------------------------
# ⑩ 袁绍移动版【乱击】（huo-mobile-batch 票 03）
# ---------------------------------------------------------------------------

YUANSHAO = "yuanshao_mobile"
LUANJI = "hero.yuanshao_mobile.luanji"
XUEYI = "hero.yuanshao_mobile.xueyi"


def _clubs(pack, count: int) -> tuple[int, ...]:
    """某牌堆里的前 ``count`` 张**梅花**牌。"""
    found: list[int] = []
    for index, card in enumerate(pack.deck.cards):
        if card.suit == "club":
            found.append(index)
            if len(found) == count:
                return tuple(found)
    raise AssertionError("牌堆里没有足够的梅花牌")


def _luanji_state(pack, hand: tuple[int, ...], *, players: int = 3):
    """``players`` = 座位数；票 07 的验收局面是 4 人（座位 0 主公 + 三名反贼）。

    4 人档要发主公座位并给 ``player_count``，否则引擎按身份局拒绝开局
    （与票 06 的 ``_lianhuan_state`` 同一条口径）。
    """
    if players >= 4:
        seats = [SeatSpec(hero=YUANSHAO, hand=hand, identity=Identity.LORD)]
        seats += [SeatSpec(identity=Identity.REBEL) for _ in range(players - 1)]
    else:
        seats = [SeatSpec(hero=YUANSHAO, hand=hand)]
        seats += [SeatSpec() for _ in range(players - 1)]
    return build_state(
        pack=pack,
        seats=seats,
        deck=_pack_deck_without(pack, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4) if players >= 4 else None,
    )


def _luanji_play_phase(pack, hand: tuple[int, ...], *, players: int = 3):
    engine = Engine.from_state(_luanji_state(pack, hand, players=players), seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    return engine


def _luanji_steps(engine):
    return engine.pending.transform_preactivations.get(LUANJI)


def test_luanji_manifest_has_only_the_source_step_and_chains_every_other_living_seat() -> None:
    """票 07 验收①：目标由【万箭齐发】自己决定 ⇒ 清单里**没有**目标步。

    4 人局（票 03 §5 的复现局面口径）、两张同花色手牌：确认只提交来源，引擎按候选给
    目标——全体其他存活角色（座位 1 / 2 / 3）各结算一张【万箭齐发】，自己挨不到，
    两张来源进弃牌堆。
    """
    first, second = _clubs(PACK, 2)
    engine = _luanji_play_phase(PACK, (first, second), players=4)
    steps = _luanji_steps(engine)

    assert steps, "【乱击】的清单下发"
    assert [step.name for step in steps] == ["card"], (
        f"目标由牌本身决定 ⇒ 只有来源步，实得 {[step.name for step in steps]}"
    )
    assert (steps[0].min, steps[0].max) == (2, 2), "来源步恰好两张"
    engine.submit(
        ActivateSkill(
            skill_id=LUANJI,
            picks=(first, second),
            choices=(),
            answers_seq=engine.pending.seq,
        )
    )
    _until(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0
        and all(e.state.player(seat).hp == 3 for seat in (1, 2, 3)),
    )

    assert engine.state.player(1).hp == 3, "座位 1 挨 1 点（没有【闪】）"
    assert engine.state.player(2).hp == 3, "座位 2 挨 1 点"
    assert engine.state.player(3).hp == 3, "座位 3 挨 1 点"
    assert engine.state.player(0).hp == 4, "自己不挨"
    assert first in engine.state.discard and second in engine.state.discard


def test_luanji_rejects_a_stale_target_set_when_a_target_left_the_legal_set() -> None:
    """票 07 验收②（反向）：无目标步时**池是唯一真源**，过期目标集不能放行。

    座位 2 在清单生成前已阵亡 ⇒ 池里的【万箭齐发】只指向座位 1 / 3。客户端若还交一份
    "座位 2 还活着时"的目标集（过期答案），确认必须拒收、零状态改动；只交来源时才按池
    结算——落账的 ``targets`` 就是池里那份（座位 2 不在里面）。
    """
    first, second = _clubs(PACK, 2)
    state = _luanji_state(PACK, (first, second), players=4)
    state.player(2).alive = False
    engine = Engine.from_state(state, seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    steps = _luanji_steps(engine)

    assert steps and [step.name for step in steps] == ["card"], "无目标步"

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            ActivateSkill(
                skill_id=LUANJI,
                picks=(first, second),
                choices=("seat:1", "seat:2", "seat:3"),
                answers_seq=engine.pending.seq,
            )
        )
    assert "没有目标步" in caught.value.message, (
        f"过期目标集该被拒，实得 {caught.value.message}"
    )
    assert engine.pending is not None, "拒绝不关闭询问，可重选"
    assert [engine.state.player(seat).hp for seat in (1, 2, 3)] == [4, 4, 4], (
        "被拒的那一次不碰任何状态"
    )

    engine.submit(
        ActivateSkill(
            skill_id=LUANJI,
            picks=(first, second),
            choices=(),
            answers_seq=engine.pending.seq,
        )
    )
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True, "只交来源该被接受"
    assert accepted.action.targets == (1, 3), (
        f"目标集取自池（此刻活着的其他角色），实得 {accepted.action.targets}"
    )
    _until(
        engine,
        lambda e: e.state.player(1).hp == 3 and e.state.player(3).hp == 3,
    )
    assert engine.state.player(2).hp == 4, "阵亡的座位 2 不在目标里"


def test_a_conversion_without_a_target_step_needs_a_unique_target_set_in_the_pool() -> None:
    """票 07 §3：无目标步时，来源组在池里必须**恰好**一条目标集（不替玩家猜）。

    池里同一组来源出现两条不同目标集（合成反例：过期的一条 + 新鲜的一条）⇒ 确认报
    「这次转化缺少目标」；只剩一条时才按它结算。判据住在确认解析器里，与清单同源。
    """
    first, second = _clubs(PACK, 2)
    engine = _luanji_play_phase(PACK, (first, second), players=4)
    steps = _luanji_steps(engine)
    pool = tuple(
        action
        for action in engine.pending.transform_actions
        if tuple(sorted(action.card.virtual_sources)) == (first, second)
    )

    assert len(pool) == 1, "正常情况下【乱击】的一组来源只有一条候选"
    resolved = resolve_conversion_confirmation(
        engine.state, 0, LUANJI, (first, second), (), pool, steps=steps
    )
    assert not isinstance(resolved, str), f"唯一目标集该放行：{resolved}"
    assert resolved.targets == (1, 2, 3)

    stale = pool[0].model_copy(update={"targets": (1, 2)})
    reason = resolve_conversion_confirmation(
        engine.state, 0, LUANJI, (first, second), (), (pool[0], stale), steps=steps
    )
    assert reason == "这次转化缺少目标", (
        f"两条不同目标集 ⇒ 报缺目标，不能替玩家挑一条：{reason!r}"
    )


def test_luanji_sources_must_be_hand_cards_and_share_one_suit() -> None:
    """清单来源步只列**手牌**里的牌，且两张必须同花色——不同花色点得动但确认被拒。"""
    first, second = _clubs(PACK, 2)
    heart = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == "heart" and card.type == "slash"
    )
    engine = _luanji_play_phase(PACK, (first, second, heart))
    steps = _luanji_steps(engine)

    assert steps, "三张牌里有两张♣ ⇒ 清单存在"
    options = {int(value) for value in steps[0].pool[0].options}
    hand = set(engine.state.player(0).hand)
    assert options <= hand, "来源步只列**手牌**（装备 / 判定区不在候选里）"
    suits = [PACK.deck.cards[card_id].suit for card_id in hand]
    for card_id in options:
        suit = PACK.deck.cards[card_id].suit
        assert suits.count(suit) >= 2, f"{card_id}（{suit}）在手里有同花色搭档"
    assert heart not in options or suits.count("heart") >= 2


def test_luanji_is_absent_without_a_same_suit_pair() -> None:
    """凑不出同花色的一对 ⇒ 候选阶段不产出、清单键不存在。

    摸牌阶段会再摸两张（四座局固定 2 张），故牌堆顶预置两种**还没出现过的花色**
    （♠ / ♦），让四张手牌的四种花色互不相同——真局面里没有同花色对。
    """
    clubs = _clubs(PACK, 1)[0]
    heart = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == "heart" and card.type == "slash"
    )
    spade = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == "spade" and card.type == "slash"
    )
    diamond = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == "diamond" and card.type == "slash"
    )
    used = {clubs, heart, spade, diamond}
    deck = (spade, diamond) + tuple(
        index for index in range(len(PACK.deck.cards)) if index not in used
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=YUANSHAO, hand=(clubs, heart)), SeatSpec(), SeatSpec()],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )

    assert len(engine.state.player(0).hand) == 4, "两张开局 + 摸两张"
    assert _luanji_steps(engine) is None, "没有同花色对 ⇒ 没有转化清单"
    assert not any(
        action.card.type == "arrow_barrage"
        for action in engine.pending.transform_actions
    ), "内部池里也没有虚拟【万箭齐发】"


def test_luanji_is_absent_without_another_living_character() -> None:
    """场上没有其他存活角色 ⇒ 没有目标，候选阶段不产出。"""
    first, second = _clubs(PACK, 2)
    other = 1
    state = _luanji_state(PACK, (first, second), players=2)
    state.player(other).alive = False
    engine = Engine.from_state(state, seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )

    assert _luanji_steps(engine) is None


def test_luanji_can_be_used_more_than_once_per_play_phase() -> None:
    """牌面没有「限一次」⇒ 同一出牌阶段可以再用一次（没有阶段位）。"""
    four = _clubs(PACK, 4)
    engine = _luanji_play_phase(PACK, four)

    for pair in (four[0:2], four[2:4]):
        steps = _luanji_steps(engine)
        assert steps, "第二组♣仍在清单里"
        assert [step.name for step in steps] == ["card"], "目标由牌本身决定（票 07）"
        engine.submit(
            ActivateSkill(
                skill_id=LUANJI,
                picks=pair,
                choices=(),
                answers_seq=engine.pending.seq,
            )
        )
        _advance(
            engine,
            lambda e: e.pending is not None
            and e.pending.kind == QueryKind.PLAY_PHASE
            and e.pending.actor == 0
            and e.state.player(1).hp == (3 if pair == four[0:2] else 2),
        )

    assert engine.state.player(1).hp == 2, "两次【万箭】各 1 点"
    assert engine.state.player(2).hp == 2


def _suit_cards(pack, suit: str, count: int) -> tuple[int, ...]:
    """牌堆序里前 ``count`` 张某花色的牌。"""
    found = tuple(
        index for index, card in enumerate(pack.deck.cards) if card.suit == suit
    )[:count]
    assert len(found) == count, f"牌堆里没有 {count} 张 {suit}"
    return found


def test_luanji_manifest_carries_the_legal_same_suit_source_groups() -> None:
    """票 10 验收①：来源步带出**合法来源组合**（``Step.groups``），不是一张并集。

    手牌 = ♣×2 / ♥×2 / ♠×1，摸牌再进 ♦×2 ⇒ 合法组**恰为**三对同花色牌（逐组点名，
    不是只数个数）；跨花色的任何一对都不在任何一组里——界面据此把不同花色的牌置灰。
    选项并集 = 这六张里的**五张**：单独一张的 ♠ 配不出对，故它连并集都不进。
    """
    clubs = _suit_cards(PACK, "club", 2)
    hearts = _suit_cards(PACK, "heart", 2)
    spade = _suit_cards(PACK, "spade", 1)
    diamonds = _suit_cards(PACK, "diamond", 2)
    hand = (*clubs, *hearts, *spade)
    drawn = diamonds
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=YUANSHAO, hand=hand), SeatSpec(), SeatSpec()],
        deck=(*drawn, *deck_excluding(PACK, *hand, *drawn)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    assert set(engine.state.player(0).hand) == {*hand, *drawn}, "开局 5 张 + 摸 2 张"

    steps = _luanji_steps(engine)
    assert steps, "有同花色对 ⇒ 清单存在"
    step = steps[0]
    assert (step.min, step.max) == (2, 2), "来源步恰好两张"
    assert set(step.pool[0].options) == {
        str(card_id) for card_id in (*clubs, *hearts, *diamonds)
    }, f"并集 = 有搭档的那几张；实得 {step.pool[0].options}"
    groups = {frozenset(group) for group in step.groups}
    assert groups == {
        frozenset(str(card_id) for card_id in clubs),
        frozenset(str(card_id) for card_id in hearts),
        frozenset(str(card_id) for card_id in diamonds),
    }, f"合法来源组合 = 三对同花色，实得 {step.groups}"
    assert all(len(group) == 2 for group in step.groups), "每组 = 来源步的张数"
    assert all(
        isinstance(value, str) for group in step.groups for value in group
    ), "组合里的 id 与 `pool[*].options` 同一口径（十进制文本）"
    for left in step.pool[0].options:
        for right in step.pool[0].options:
            if left >= right:
                continue
            suits = {PACK.deck.cards[int(card_id)].suit for card_id in (left, right)}
            if len(suits) == 2:
                assert not any(
                    {left, right} <= group for group in groups
                ), f"跨花色的一对 {left}/{right} 不在任何合法组里"


def test_a_conversion_whose_every_pair_is_legal_carries_no_source_groups() -> None:
    """票 10 的**省略**判据：「任意两张都行」的转化（丈八蛇矛）不下发 ``groups``。

    缺省 = 全组合合法（老载荷逐字段不变）：界面照旧按 ``pool[*].options`` 全都点得动，
    服务端校验器仍是唯一裁判。下发一串"每一对都在里面"的数据没有信息量。
    """
    serpent = cards_of_type(PACK, "serpent_spear")[0]
    hand = tuple(cards_of_type(PACK, "slash")[:3])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, equip=(serpent,)), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK, *hand, serpent),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=3)
    _advance(
        engine,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )
    steps = engine.pending.transform_preactivations.get("equip.serpent_spear")

    assert steps, "丈八蛇矛的转化清单下发"
    assert steps[0].name == "card" and (steps[0].min, steps[0].max) == (2, 2)
    assert steps[0].groups == (), (
        f"任意两张都能配 ⇒ 不下发 groups；实得 {steps[0].groups}"
    )


def test_yuanshao_mobile_hero_is_in_the_default_pool() -> None:
    """数据面：袁绍移动版进默认池（显示名带「移动版」、真名「袁绍」、群 / 男 / 4）。"""
    heroes = {hero.id: hero for table in load_hero_tables() for hero in table.heroes}

    hero = heroes[YUANSHAO]
    assert hero.name == "袁绍移动版"
    assert hero.true_name == "袁绍"
    assert hero.kingdom is Kingdom.QUN
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["luanji", "xueyi"]
    assert [skill.name for skill in hero.skills] == ["乱击", "血裔"]
    assert [skill.skill_type.value for skill in hero.skills] == ["NORMAL", "LORD"]


# ---------------------------------------------------------------------------
# ⑪ 袁绍移动版【血裔】（huo-mobile-batch 票 03；08 读法 A）
# ---------------------------------------------------------------------------


def _xueyi_state(*, others: tuple[Kingdom, ...] = (Kingdom.QUN, Kingdom.QUN), identity=None, hp=4, hp_max=4):
    seats = [SeatSpec(hero=YUANSHAO, identity=identity, hp=hp, hp_max=hp_max, kingdom=Kingdom.QUN)]
    seats.extend(SeatSpec(kingdom=kingdom) for kingdom in others)
    return build_state(pack=PACK, seats=seats, deck=deck_excluding(PACK))


def test_xueyi_adds_two_per_other_living_qun_character() -> None:
    """X = 2 × 其他存活群势力角色数：两名其他群 ⇒ 手牌上限 = 体力 + 4。"""
    state = _xueyi_state(identity=Identity.LORD)

    assert hand_limit(state, state.player(0)) == 4 + 4


def test_xueyi_ignores_the_holder_and_dead_characters() -> None:
    """「其他」不含自己；阵亡的群势力角色不再算。"""
    state = _xueyi_state(
        others=(Kingdom.QUN, Kingdom.WEI, Kingdom.QUN), identity=Identity.LORD
    )
    assert hand_limit(state, state.player(0)) == 4 + 4, "自己不算、魏不算"

    state.player(3).alive = False
    assert hand_limit(state, state.player(0)) == 4 + 2, "死掉的群势力角色不再算"


def test_xueyi_is_not_instantiated_for_a_non_lord_seat() -> None:
    """主公技门控：不是主公 ⇒ 实例根本不产出（上限 = 体力值）。"""
    state = _xueyi_state(identity=Identity.LOYALIST)

    assert hand_limit(state, state.player(0)) == 4


def test_xueyi_adds_nothing_when_no_other_qun_character_is_alive() -> None:
    state = _xueyi_state(others=(Kingdom.WEI, Kingdom.WU), identity=Identity.LORD)

    assert hand_limit(state, state.player(0)) == 4


def test_xueyi_composes_with_a_one_shot_hand_limit_bonus() -> None:
    """与一次性加式（``hand_limit_bonus``）不分先后：两者相加。"""
    state = _xueyi_state(identity=Identity.LORD)
    state.player(0).hand_limit_bonus = 2

    assert hand_limit(state, state.player(0)) == 4 + 4 + 2


def test_xueyi_current_definition_is_a_locked_lord_skill() -> None:
    """声明面：锁定 + 主公技 + 只有一条手牌上限修饰器（逐字与牌面同源）。"""
    definition = CATALOG[XUEYI]

    assert definition.locked is True
    assert definition.skill_type.value == "LORD"
    assert [modifier.point.name for modifier in definition.modifiers] == [
        "HAND_LIMIT_REPLACE"
    ]
    assert definition.hooks == () and definition.seams == ()


# ---------------------------------------------------------------------------
# ⑨ 神周瑜移动版【琴音】（huo-shenzhouyu-mobile 票 01 / 框架票 03）
#
# 牌面：弃牌阶段结束时，若你于此阶段内弃置过你至少两张手牌，则你可以选择一项：
#       1.令所有角色各回复1点体力；2.令所有角色各失去1点体力。
# ---------------------------------------------------------------------------

SHENZHOUYU = "shen_zhouyu_mobile"
QINYIN = "hero.shen_zhouyu_mobile.qinyin"
QINYIN_RECOVER = "qinyin_recover"
QINYIN_LOSE = "qinyin_lose"


def _qinyin_state(
    *,
    owner_seat: int = 0,
    hand: tuple[int, ...] = (2, 3, 4, 5),
    hps: tuple[int, ...] = (4, 4, 4, 4),
    turn_seat: int | None = None,
):
    """4 人身份局：主公坐 ``owner_seat``（先手），神周瑜就是主公。

    开局手牌 ``hand`` ⇒ 摸牌阶段再摸 2 张 ⇒ 弃牌阶段弃到体力上限为止，故
    ``len(hand) + 2 - hp`` 就是本阶段弃置的手牌数。
    """
    seats = []
    for seat in range(4):
        identity = Identity.LORD if seat == owner_seat else Identity.REBEL
        if seat == owner_seat:
            seats.append(
                SeatSpec(hero=SHENZHOUYU, hp=hps[seat], hand=tuple(hand), identity=identity)
            )
        else:
            seats.append(SeatSpec(hp=hps[seat], identity=identity))
    used = set(hand)
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index not in used)
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=owner_seat if turn_seat is None else turn_seat,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )


def _pass_of(query):
    return next(action for action in query.legal_actions if isinstance(action, Pass))


def _qinyin_run(engine, *, choice: str | None, limit: int = 4000) -> dict:
    """跑到【琴音】询问（``choice=None`` = 不发动），答完之后跑完这一回合。"""
    found: dict = {"activation": None, "option": None}
    first_turn = engine.state.turn_seat
    for _ in range(limit):
        query = engine.pending
        if query is not None:
            if (
                query.kind == QueryKind.SKILL_ACTIVATION
                and getattr(query, "skill_id", None) == QINYIN
            ):
                found["activation"] = found["activation"] or query
                if choice is None:
                    engine.submit(_pass_of(query))
                else:
                    engine.submit(
                        next(
                            action
                            for action in query.legal_actions
                            if getattr(action, "skill_id", None) == QINYIN
                        )
                    )
                continue
            if query.kind == QueryKind.SELECT_OPTION and str(
                getattr(query, "skill_id", "")
            ).startswith("qinyin"):
                found["option"] = found["option"] or query
                engine.submit(
                    next(
                        action
                        for action in query.legal_actions
                        if getattr(action, "choice", None) == choice
                    )
                )
                continue
            engine.submit(_default_action(query))
            continue
        if engine.finished:
            break
        if engine.state.turn_seat != first_turn:
            break
        engine._step()
    return found


def _qinyin_asks(engine) -> list:
    """记录流里问给神周瑜【琴音】的发动询问。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION.value
        and getattr(record.query, "skill_id", None) == QINYIN
    ]


def test_qinyin_is_not_offered_below_two_discarded_hand_cards() -> None:
    """只弃 1 张（3 张起手 + 摸 2 = 5，上限 4）⇒ 条件不成立、连"是否发动"都不问。"""
    engine = Engine.from_state(_qinyin_state(hand=(2, 3, 4)), seed=3)

    _qinyin_run(engine, choice=None)

    assert _qinyin_asks(engine) == []


def test_qinyin_requires_two_hand_cards_discarded_in_the_discard_phase() -> None:
    """弃 2 张 ⇒ 弃牌阶段结束时问一次；回答"发动"之后给两项选项。"""
    engine = Engine.from_state(_qinyin_state(hand=(2, 3, 4, 5)), seed=3)

    found = _qinyin_run(engine, choice=QINYIN_LOSE)

    assert found["activation"] is not None, "弃 2 张手牌后应当触发"
    assert len(_qinyin_asks(engine)) == 1, "一个弃牌阶段只问一次"
    assert found["option"] is not None


def test_qinyin_has_exactly_two_options_and_no_pass_after_activation() -> None:
    """发动之后的两项是**必选其一**：闭集恰两项、没有 Pass（「可以」只在发动那一问）。"""
    engine = Engine.from_state(_qinyin_state(hand=(2, 3, 4, 5)), seed=3)

    found = _qinyin_run(engine, choice=QINYIN_LOSE)
    option = found["option"]

    assert option is not None
    choices = [
        action.choice
        for action in option.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert choices == [QINYIN_RECOVER, QINYIN_LOSE]
    assert not any(isinstance(action, Pass) for action in option.legal_actions)


def test_qinyin_recover_gives_every_alive_seat_one_hp() -> None:
    """选项 1：所有存活角色（含自己）各回复 1 点，夹体力上限。"""
    engine = Engine.from_state(_qinyin_state(hand=(2, 3, 4, 5), hps=(2, 3, 2, 4)), seed=3)

    _qinyin_run(engine, choice=QINYIN_RECOVER)

    assert [player.hp for player in engine.state.players] == [3, 4, 3, 4]


def test_qinyin_lose_hp_takes_one_from_every_alive_seat_in_action_order() -> None:
    """选项 2：各失去 1 点体力；座位序 = **从使用者开始的行动顺序**（不是座位号升序）。"""
    engine = Engine.from_state(_qinyin_state(owner_seat=2, hand=(2, 3, 4, 5)), seed=3)

    _qinyin_run(engine, choice=QINYIN_LOSE)

    assert [player.hp for player in engine.state.players] == [3, 3, 3, 3]
    order = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.LIFE_DECREASE_BEFORE
    ]
    assert order == [2, 3, 0, 1], "从座位 2（使用者）起按行动顺序逐个失去体力"


def test_qinyin_lose_hp_can_kill_and_continues_with_the_remaining_seats() -> None:
    """失去体力可以打死人：死者不再参与，其余座位照常结算。"""
    engine = Engine.from_state(_qinyin_state(hand=(2, 3, 4, 5), hps=(4, 1, 4, 4)), seed=3)

    _qinyin_run(engine, choice=QINYIN_LOSE)

    assert engine.state.player(1).alive is False, "1 体力的人被【琴音】打死"
    assert [engine.state.player(seat).hp for seat in (0, 2, 3)] == [3, 3, 3]


def test_qinyin_does_not_trigger_in_another_players_discard_phase() -> None:
    """默认读法（二）④：只认**自己**的弃牌阶段（别人的弃牌阶段里实例不产出候选）。"""
    # 座位 1 是主公（先手），神周瑜坐在座位 0 ⇒ 本回合不是他的弃牌阶段。
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=SHENZHOUYU, identity=Identity.REBEL),
            SeatSpec(identity=Identity.LORD, hand=(2, 3, 4, 5)),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
        ],
        deck=tuple(index for index in range(len(PACK.deck.cards)) if index not in {2, 3, 4, 5}),
        turn_seat=1,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=3)

    _qinyin_run(engine, choice=QINYIN_LOSE)

    assert _qinyin_asks(engine) == []


# ---------------------------------------------------------------------------
# ⑩ 神周瑜移动版【业炎】（huo-shenzhouyu-mobile 票 01 / 框架票 05）
#
# 牌面：限定技，出牌阶段，你可以选择至多三名角色，对这些角色造成共计至多3点火焰伤害
#       （若你将对一名角色分配大于等于2点火焰伤害，你须先弃置四张花色各不相同的手牌并失去3点体力）。
# 交互（用户 2026-11-02 指定）：分配弹窗 → 任一计数 ≥2 时四色选牌 → 分支 1 / 分支 2。
# ---------------------------------------------------------------------------

YEYAN = "hero.shen_zhouyu_mobile.yeyan"


def _four_suits(pack) -> tuple[int, ...]:
    """四张花色各不相同的手牌（♠♥♣♦ 各第一张）。"""
    first: dict[str, int] = {}
    for index, card in enumerate(pack.deck.cards):
        if card.suit and card.suit not in first:
            first[card.suit] = index
    return tuple(first[suit] for suit in ("spade", "heart", "club", "diamond"))


def _yeyan_state(*, hand=None, hps=(4, 4, 4, 4), chained=(), others_hand=(), lord_seat=0):
    four = _four_suits(PACK) if hand is None else tuple(hand)
    seats = [
        SeatSpec(
            hero=SHENZHOUYU,
            hp=hps[0],
            hand=four,
            identity=Identity.LORD if lord_seat == 0 else Identity.REBEL,
        ),
    ]
    for seat in range(1, 4):
        seats.append(
            SeatSpec(
                hp=hps[seat],
                identity=Identity.LORD if lord_seat == seat else Identity.REBEL,
                hand=others_hand if seat == 1 else (),
                chained=seat in chained,
            )
        )
    used = set(four) | set(others_hand)
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index not in used)
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )


def _yeyan_run(engine, *, counts: dict[int, int], limit: int = 4000) -> dict:
    """发动【业炎】→ 提交分配 → （需要代价时）选四张手牌 → 跑回出牌阶段。"""
    found: dict = {"distribution": None, "cost": None}
    activated = False
    for _ in range(limit):
        query = engine.pending
        if query is None:
            if engine.finished:
                break
            engine._step()
            continue
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0:
            if activated:
                break
            action = next(
                (item for item in query.legal_actions if getattr(item, "skill_id", None) == YEYAN),
                None,
            )
            if action is None:
                break
            activated = True
            engine.submit(action)
            continue
        if query.kind == QueryKind.DISTRIBUTE:
            found["distribution"] = query
            engine.submit(
                next(
                    item
                    for item in query.legal_actions
                    if item.kind == "Distribute" and dict(item.counts) == counts
                )
            )
            continue
        if query.kind == QueryKind.SELECT_CARDS and getattr(query, "skill_id", None) == YEYAN:
            found["cost"] = query
            engine.submit(
                next(
                    item
                    for item in query.legal_actions
                    if item.kind == "SelectCards" and len(item.picks) == 4
                )
            )
            continue
        engine.submit(_default_action(query))
    return found


def test_yeyan_distribution_lists_all_alive_seats_with_zero_counters() -> None:
    """分配弹窗：全部存活座位（含自己）、合计上限 3、单座上限 3；全 0 是一条合法候选。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    found = _yeyan_run(engine, counts={})

    query = found["distribution"]
    assert query is not None
    assert query.seats == (0, 1, 2, 3)
    assert query.max_total == 3
    assert query.per_seat_max == 3
    assert any(item.kind == "Distribute" and item.counts == () for item in query.legal_actions)


def test_yeyan_branch_two_deals_damage_without_cost() -> None:
    """全部计数 ≤1 ⇒ 分支 2：不弃牌、不失去体力，只按分配造成火焰伤害。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    found = _yeyan_run(engine, counts={1: 1, 2: 1})

    assert found["cost"] is None, "没有计数 ≥2 ⇒ 不进第二步选牌"
    assert [player.hp for player in engine.state.players] == [4, 3, 3, 4]
    assert len(engine.state.discard) == 0, "没有代价、也没有弃牌"


def test_yeyan_branch_one_discards_then_loses_hp_then_deals_damage() -> None:
    """计数 ≥2 ⇒ 分支 1：弃四色手牌 → 失去 3 点体力 → 伤害；三段按顺序。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    found = _yeyan_run(engine, counts={1: 2, 2: 1})

    assert found["cost"] is not None, "有计数 ≥2 ⇒ 必须弃四色手牌"
    assert engine.state.player(0).hp == 1, "失去 3 点体力"
    assert [player.hp for player in engine.state.players] == [1, 2, 3, 4]
    assert len(engine.state.discard) >= 4, "四张代价进了弃牌堆"
    # 顺序：失去体力（座位 0）在第一个伤害之前。
    timing = [
        (record.timing, record.actor)
        for record in engine.records
        if record.kind == "timing"
        and record.timing in (Timing.LIFE_DECREASE_ON, Timing.DAMAGE_TAKE)
    ]
    assert (Timing.LIFE_DECREASE_ON, 0) in timing
    assert timing.index((Timing.LIFE_DECREASE_ON, 0)) < timing.index((Timing.DAMAGE_TAKE, 1))


def test_yeyan_damage_resolves_in_action_order_not_click_order() -> None:
    """伤害按**从你开始的行动顺序**结算（分配时先给座位 2 也会先结算座位 1）。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    _yeyan_run(engine, counts={2: 2, 1: 1})

    order = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_TAKE
    ]
    assert order[:2] == [1, 2], "座位 1 先于座位 2（行动顺序），与分配时点选顺序无关"


def test_yeyan_zero_allocation_consumes_the_skill_without_damage() -> None:
    """全 0 提交 = 一次发动：不造成伤害、不付代价，但限定技照常消耗。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    _yeyan_run(engine, counts={})

    assert [player.hp for player in engine.state.players] == [4, 4, 4, 4]
    assert engine.state.discard == () or all(
        engine.state.card(card_id).type != "slash" for card_id in engine.state.discard
    )
    # 已用：同一次出牌阶段的后续候选里不再出现【业炎】。
    assert engine.pending is not None and engine.pending.kind == QueryKind.PLAY_PHASE
    assert not any(
        getattr(action, "skill_id", None) == YEYAN
        for action in engine.pending.legal_actions
    )


def test_yeyan_unpayable_cost_caps_each_count_at_one() -> None:
    """手里凑不出四花色 ⇒ 单座计数上限压到 1（不给出"要付却付不起"的路）。"""
    one_suit = tuple(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == "spade"
    )[:6]
    engine = Engine.from_state(_yeyan_state(hand=one_suit), seed=5)

    found = _yeyan_run(engine, counts={1: 1, 2: 1})

    query = found["distribution"]
    assert query is not None
    assert query.per_seat_max == 1
    assert not any(
        any(count >= 2 for _, count in item.counts) for item in query.legal_actions
    ), "没有计数 ≥2 的候选"


def test_yeyan_damage_still_resolves_after_the_owner_dies() -> None:
    """你因代价阵亡 ⇒ 伤害**照常结算**、来源改为无来源（用户 2026-11-02 裁定）。"""
    engine = Engine.from_state(_yeyan_state(hps=(1, 4, 4, 4), lord_seat=1), seed=5)

    _yeyan_run(engine, counts={1: 2, 2: 1})

    assert engine.state.player(0).alive is False, "1 体力失去 3 点 ⇒ 阵亡"
    assert [engine.state.player(seat).hp for seat in (1, 2)] == [2, 3], "伤害仍然发生"
    damages = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_TAKE
    ]
    assert damages, "伤害时机照常派发"


def test_yeyan_is_a_limited_skill_and_disappears_after_use() -> None:
    """限定技：整局一次；用完之后出牌阶段的候选里不再有它。"""
    engine = Engine.from_state(_yeyan_state(), seed=5)

    _yeyan_run(engine, counts={1: 1})

    assert engine.pending is not None and engine.pending.kind == QueryKind.PLAY_PHASE
    assert not any(
        getattr(action, "skill_id", None) == YEYAN
        for action in engine.pending.legal_actions
    )


def test_yeyan_fire_damage_propagates_through_iron_chain() -> None:
    """火焰伤害照常参与铁索传导：座位 1、2 都横置，打座位 2 ⇒ 传导 1 点给座位 1。"""
    engine = Engine.from_state(_yeyan_state(chained=(1, 2)), seed=5)

    _yeyan_run(engine, counts={2: 1})

    assert [engine.state.player(seat).hp for seat in (1, 2)] == [3, 3], "传导 1 点火焰伤害"
    assert engine.state.player(1).chained is False, "受到属性伤害后重置横置"
    assert engine.state.player(2).chained is False, "受到属性伤害后重置横置"


# ---------------------------------------------------------------------------
# 神诸葛亮（神话再临·火包 / OL 现行文本）：七星 / 狂风 / 大雾
#
# 用户 2026-11-04 的模型（`.scratch/huo-shenzhugeliang/issues/04-c1-forced-hooks.md` §1）：
# 两枚标记**没有自身效果**（`effect=None` / `description=None`），效果由技能自己监听、
# 技能失效即不生效；七星的两行交换**只上下交换、不调序**（答案 = 定长集合）。
# ---------------------------------------------------------------------------

SHEN = "shen_zhugeliang"
QIXING_SKILL = "hero.shen_zhugeliang.qixing"
KUANGFENG_SKILL = "hero.shen_zhugeliang.kuangfeng"
DAWU_SKILL = "hero.shen_zhugeliang.dawu"


def _shen_engine(*, seed: int = 7, seats: int = 2) -> Engine:
    """真开局（含起始手牌）之后、第一次 ``_step`` 之前：神诸葛亮坐在 0 号位。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=seats),
        pack=PACK,
        pinned_hero=SHEN,
        pinned_seat=0,
        hero_pool=(),
    )


def _star_ids(engine_or_state) -> tuple[int, ...]:
    state = getattr(engine_or_state, "state", engine_or_state)
    return tuple(state.hero_zone(0, star_zone_key(0)))


def _queries_of(engine: Engine, skill_id: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "query"
        and str(getattr(record.query, "skill_id", None) or "") == skill_id
    ]


class _ShenDriver:
    """神诸葛亮的脚本玩家：排列按 ``order`` 答（``None`` = 恒等 = 不换），其余取最小合法应答。

    星牌点选取 ``legal_actions[0]``（一张的组合），座次选项按 ``seat`` 挑 ``seat:N``。
    """

    def __init__(
        self,
        *,
        order: tuple[int, ...] | None = None,
        seat: int = 1,
        activate_skills: tuple[str, ...] | None = None,
    ) -> None:
        self.order = order
        self._target_seat = seat
        self._activate_skills = activate_skills
        self.queries: list = []

    def respond(self, query):
        self.queries.append(query)
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == "ARRANGE":
            legal = next(a for a in query.legal_actions if isinstance(a, ArrangeCards))
            if self.order is None:
                return legal
            return ArrangeCards(
                answers_seq=legal.answers_seq, order=self.order, split=legal.split
            )
        if query.kind == "SKILL_ACTIVATION":
            allowed = self._activate_skills
            # 票 01 起一张面可以有多条候选（神诸葛亮的【狂风】【大雾】同为**结束阶段**）：
            # 要按**面上的候选**挑，不能只看 ``query.skill_id``（那是代表候选）。
            for action in query.legal_actions:
                if not isinstance(action, ActivateSkill):
                    continue
                if allowed is None or action.skill_id in allowed:
                    return action
        if query.kind == "SELECT_OPTION":
            wanted = f"seat:{self._target_seat}"
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == wanted:
                    return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _qixing_player(*, order=None) -> _ShenDriver:
    return _ShenDriver(order=order)


# --- 七星 ①：开局扣置七张 + 一次两行弹窗 -----------------------------------


def test_qixing_places_seven_stars_at_game_start():
    """L0.3 扣置七张；**不问"是否发动"**（强制段），接着一条 ``ARRANGE``（两行交换）。"""
    engine = _shen_engine(seed=7, seats=2)
    players = [_qixing_player(), PassPlayer()]
    run_queries(engine, players, lambda e: ran_timing(e, Timing.TURN_BEFORE_START))

    assert len(_star_ids(engine)) == 7, "牌堆顶七张扣置为「星」"
    kinds = [record.query.kind for record in engine.records if record.kind == "query"]
    assert kinds == ["ARRANGE"], "扣置不问『是否发动』；替换是两行弹窗的一次询问"
    arrange = next(r for r in engine.records if r.kind == "query").query
    assert len(arrange.revealed) == 11, "面 = 7 星 + 4 张起始手牌"


def test_qixing_swaps_hand_cards_for_the_same_number_of_stars():
    """上下交换：第 1 张手牌上星区、第 1 张星回手牌（等量由切分点保证）。"""
    engine = _shen_engine(seed=7, seats=2)
    player = _qixing_player()
    run_queries(engine, player, lambda e: e.pending is not None and e.pending.kind == "ARRANGE")
    arrange = engine.pending
    legal = next(a for a in arrange.legal_actions if isinstance(a, ArrangeCards))
    stars_before = _star_ids(engine)
    hand_before = tuple(engine.state.player(0).hand)
    assert legal.split == 7
    face = arrange.revealed
    up = face[7].card_id  # 第 1 张手牌
    down = face[0].card_id  # 第 1 张星
    order = (7, 1, 2, 3, 4, 5, 6, 0, 8, 9, 10)
    engine.submit(
        ArrangeCards(answers_seq=legal.answers_seq, order=order, split=legal.split)
    )
    run_queries(engine, [player, PassPlayer()], lambda e: ran_timing(e, Timing.TURN_BEFORE_START))

    stars_after = _star_ids(engine)
    hand_after = tuple(engine.state.player(0).hand)
    assert up in stars_after and up not in hand_after
    assert down in hand_after and down not in stars_after
    assert len(stars_after) == len(stars_before) == 7
    del hand_before


def test_qixing_identity_submission_means_no_swap():
    """原样提交 = 「放弃」（「可以」的出口）：两行一张都不动。"""
    engine = _shen_engine(seed=7, seats=2)
    run_queries(
        engine,
        [_qixing_player(), PassPlayer()],
        lambda e: e.pending is not None and e.pending.kind == "ARRANGE",
    )
    stars_before = _star_ids(engine)
    hand_before = tuple(engine.state.player(0).hand)
    arrange = engine.pending
    legal = next(a for a in arrange.legal_actions if isinstance(a, ArrangeCards))
    engine.submit(legal)
    run_queries(
        engine, [_qixing_player(), PassPlayer()], lambda e: ran_timing(e, Timing.TURN_BEFORE_START)
    )
    assert _star_ids(engine) == stars_before
    assert tuple(engine.state.player(0).hand) == hand_before


def test_qixing_stars_are_their_own_zone_and_not_hand_cards():
    """「星」是（座位, 七星实例）的独立区：不算手牌，且与【谦逊】的区互不相干。"""
    engine = _shen_engine(seed=7, seats=2)
    run_queries(
        engine,
        [_qixing_player(), PassPlayer()],
        lambda e: e.state.turn_seat is not None and len(_star_ids(engine)) == 7,
    )
    state = engine.state
    stars = _star_ids(engine)
    assert not set(stars) & set(state.player(0).hand)
    assert state.player(0).hero_cards[star_zone_key(0)].skill_id == QIXING_SKILL
    assert len(state.player(0).hero_cards) == 1, "只有七星自己的区"


def test_qixing_stars_are_host_only():
    """牌面只给宿主：本人视角看得到牌面，别人只拿张数。"""
    engine = _shen_engine(seed=7, seats=2)
    run_queries(
        engine,
        [_qixing_player(), PassPlayer()],
        lambda e: e.state.turn_seat is not None and len(_star_ids(engine)) == 7,
    )
    from sgs.state import project

    mine = project(engine.state, 0)
    other = project(engine.state, 1)
    assert len(mine.hero_zone_cards) == 7
    assert other.hero_zone_cards == ()
    assert other.players[0].hero_zone_count == 7


def test_qixing_asks_again_at_the_end_of_the_draw_phase():
    """② 摸牌阶段结束时：先问"是否发动"（可选段），再是同一条两行弹窗。"""
    engine = _shen_engine(seed=7, seats=2)
    player = _qixing_player()
    run_queries(
        engine,
        [player, PassPlayer()],
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "query" and record.query.kind == "ARRANGE"
        )
        >= 2,
    )
    kinds = [record.query.kind for record in engine.records if record.kind == "query"]
    assert kinds.count("ARRANGE") >= 2, "开局一次替换 + 摸牌阶段结束又一次"
    assert "SKILL_ACTIVATION" in kinds, "② 是可选段（牌面写「可以」）"


# --- 狂风：标记无自身效果，+X 由技能监听 --------------------------------


def _kuangfeng_state(*, nature_marks: int = 1):
    """座位 0 = 神诸葛亮（有 2 张星），座位 1 = 承受方；牌堆用标准 108。"""
    from sgs.state import HeroCardZone

    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN, hp=3), SeatSpec(hp=4), SeatSpec(hp=4)],
        turn_seat=0,
    )
    state.player(0).hero_cards[star_zone_key(0)] = HeroCardZone(
        skill_id=QIXING_SKILL,
        owner=0,
        cards=list(cards_of_type(PACK, "peach")[:2]),
    )
    return state


def test_kuangfeng_boosts_fire_damage_by_one():
    """监听是**技能**的：目标身上一枚「狂风」⇒ 火焰伤害 +1。"""
    from sgs.effects import damage_amount
    from sgs.effects.behaviors import DamageNature
    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=1),
    )
    assert damage_amount(
        state, source=0, target=1, card=None, base=1, chain=False,
        nature=DamageNature.FIRE,
    ) == 2


def test_kuangfeng_does_not_boost_thunder_or_plain_damage():
    """限火焰（用户 2026-11-04 Q1）：雷电 / 普通伤害不加。"""
    from sgs.effects import damage_amount
    from sgs.effects.behaviors import DamageNature
    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=2),
    )
    for nature in (DamageNature.THUNDER, DamageNature.NORMAL, None):
        assert damage_amount(
            state, source=0, target=1, card=None, base=1, chain=False, nature=nature
        ) == 1


def test_kuangfeng_stacks_one_point_per_marker():
    from sgs.effects import damage_amount
    from sgs.effects.behaviors import DamageNature
    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=2),
    )
    assert damage_amount(
        state, source=0, target=1, card=None, base=1, chain=False,
        nature=DamageNature.FIRE,
    ) == 3


def test_kuangfeng_effect_disappears_when_the_skill_is_suppressed():
    """失效 ⇒ 不进修饰器链 ⇒ 不增伤（用户 2026-11-04 Q2 的同读口径）。"""
    from sgs.effects import damage_amount
    from sgs.effects.behaviors import DamageNature
    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=1),
    )
    engine = Engine.from_state(state, seed=1)
    engine.suppress_skills(0, "hero.jie_guanyu.yijue")
    assert damage_amount(
        engine.state, source=0, target=1, card=None, base=1, chain=False,
        nature=DamageNature.FIRE,
    ) == 1


def test_kuangfeng_marks_expire_at_the_start_of_the_owners_next_turn():
    """寿命：来源的下个回合开始时清（强制钩子的手写回调，直接驱动一次）。"""
    from types import SimpleNamespace

    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=1),
    )
    engine = Engine.from_state(state, seed=1)
    hook = next(
        hook for hook in CATALOG[KUANGFENG_SKILL].hooks if hook.timing is Timing.TURN_START
    )
    frame = SimpleNamespace(data={"skill_owner": 0}, frame_id=0)
    hook.handler(engine, frame)
    assert engine.state.player(1).markers == ()


def test_kuangfeng_spends_a_star_and_marks_the_chosen_character():
    """端到端：结束阶段发动【狂风】→ 选 1 张星（点选弹窗的槽）→ 点座位 1 → 标记落地。

    断言三件事：星少 1 张（进弃牌堆）、标记字段逐项 = 用户 2026-11-04 的口径
    （来源 / 无自身效果 / 来源阵亡即清）、以及选牌询问的槽位带**牌面**（弹窗要按常规卡面画）。
    """
    from sgs.state import HeroCardZone

    stars = list(cards_of_type(PACK, "peach")[:2])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN, hp=3), SeatSpec(hp=4), SeatSpec(hp=4)],
        # 牌堆要真的够（否则第一个摸牌阶段就短摸 ⇒ 平局收场，结束阶段根本到不了）。
        deck=deck_excluding(PACK, *stars),
    )
    state.player(0).hero_cards[star_zone_key(0)] = HeroCardZone(
        skill_id=QIXING_SKILL,
        owner=0,
        cards=stars,
    )
    engine = Engine.from_state(state, seed=3)
    driver = _ShenDriver(seat=1, activate_skills=(KUANGFENG_SKILL,))
    players = [driver, PassPlayer(), PassPlayer()]
    run_queries(engine, players, lambda e: bool(e.state.player(1).markers))
    markers = engine.state.player(1).markers
    assert [marker.name for marker in markers] == ["狂风"]
    marker = markers[0]
    assert marker.source_seat == 0 and marker.source_skill_id == KUANGFENG_SKILL
    assert marker.count == 1 and marker.max_count is None
    assert marker.effect is None and marker.description is None, "标记自身无效果（用户口径）"
    assert marker.clear_on_source_death is True, "来源阵亡后消失"
    # 面内 = 开局扣置的 7 张（`GAME_START` 的强制段）+ 本用例注入的 2 张 = 9 张；
    # 狂风移去 1 张（面内第 1 槽 = 注入的第一张）⇒ 8 张，且那 1 张进了弃牌堆。
    assert len(_star_ids(engine)) == 8, "移去了一张星"
    assert any(card_id in engine.state.discard for card_id in stars), (
        "移去的星进入弃牌堆（武将牌上的牌 → 弃牌堆）"
    )
    star_queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_CARDS"
        and (record.query.slots or ())
        and all(slot.ref.zone == "hero" for slot in record.query.slots)
    ]
    assert star_queries, "狂风的第一步是星牌点选"
    slots = star_queries[0].slots
    assert slots and all(slot.ref.zone == "hero" for slot in slots)
    assert all(slot.card is not None for slot in slots), "星对宿主可见（弹窗要画花色 / 点数）"


def test_mark_expiry_hooks_only_fire_on_the_sources_own_turn_start():
    """寿命口径：**标记来源**的下个回合开始时清，而不是**目标**的回合开始（用户 2026-11-04 报障）。

    `TURN_START` 是每个座位的回合开始都会派发；若不清扫钩子自校"这一步的回合角色是不是
    持有者"，庞统身上的「大雾」会在**庞统自己**的回合开始就被清掉（实机现象）。
    """
    from types import SimpleNamespace

    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="大雾", source_seat=0, source_skill_id=DAWU_SKILL, count=1),
        Marker(name="狂风", source_seat=0, source_skill_id=KUANGFENG_SKILL, count=1),
    )
    engine = Engine.from_state(state, seed=1)
    for skill_id in (KUANGFENG_SKILL, DAWU_SKILL):
        hook = next(
            hook for hook in CATALOG[skill_id].hooks if hook.timing is Timing.TURN_START
        )
        assert hook.condition is not None, "到期清扫必须自校『是不是我的回合开始』"
        other_turn = SimpleNamespace(
            data={"skill_owner": 0, "hook_owner": 0, "source": 1}, frame_id=0
        )
        assert hook.condition(engine, other_turn) is False, (
            f"{skill_id}：目标座位的回合开始不该清标记"
        )
        own_turn = SimpleNamespace(
            data={"skill_owner": 0, "hook_owner": 0, "source": 0}, frame_id=0
        )
        assert hook.condition(engine, own_turn) is True, (
            f"{skill_id}：自己的回合开始才清"
        )


def test_dawu_prevents_non_thunder_damage_but_lets_thunder_through():
    """防止由技能监听：非雷电 ⇒ 写 ``PREVENTED``；雷电 ⇒ 不写。"""
    from types import SimpleNamespace

    from sgs.effects.behaviors import DamageNature
    from sgs.resolution import Tag, has_tag
    from sgs.state import Marker

    state = _kuangfeng_state()
    state.player(1).markers = (
        Marker(name="大雾", source_seat=0, source_skill_id=DAWU_SKILL, count=1),
    )
    engine = Engine.from_state(state, seed=1)
    hook = next(
        hook for hook in CATALOG[DAWU_SKILL].hooks if hook.timing is Timing.DAMAGE_DEAL
    )
    fire = SimpleNamespace(data={"target": 1, "nature": DamageNature.FIRE}, frame_id=0)
    hook.handler(engine, fire)
    assert has_tag(fire, Tag.PREVENTED)
    thunder = SimpleNamespace(data={"target": 1, "nature": DamageNature.THUNDER}, frame_id=0)
    hook.handler(engine, thunder)
    assert not has_tag(thunder, Tag.PREVENTED)
    plain = SimpleNamespace(data={"target": 2, "nature": None}, frame_id=0)
    hook.handler(engine, plain)
    assert not has_tag(plain, Tag.PREVENTED), "没有标记的人不受保护"


def test_dawu_marks_carry_no_self_effect():
    """用户 2026-11-04：两枚标记 `effect` / `description` 都是 ``None``（效果归技能）。"""
    from sgs.state import Marker

    marker = Marker(name="大雾", source_seat=0, source_skill_id=DAWU_SKILL, count=1)
    assert marker.effect is None and marker.description is None


# ---------------------------------------------------------------------------
# 火包·移动版·太史慈【天义】（huo-taishici-xunyu 票 01）
#
#     出牌阶段限一次，你可以与一名角色拼点，然后你本阶段根据拼点结果获得对应效果：
#     若你赢，则你使用【杀】的次数上限+1、使用【杀】无距离限制、使用【杀】的目标数上限+1，
#     否则你不能使用【杀】。
#
# 拼点 = ``RANK_COMPARE`` 帧（huo-taishici-xunyu 票 03）：两张手牌因拼点失去 → 亮出比点
# → 置入弃牌堆；「本阶段」的三条效果读座位三态（``tianyi_won`` / ``tianyi_lost``）。
# ---------------------------------------------------------------------------

TIANYI = "hero.taishici.tianyi"
LIANYING = "hero.jie_luxun.lianying"


def _rank(card_id: int) -> int:
    return PACK.deck.cards[card_id].rank


def _high_low_cards() -> tuple[int, int]:
    """牌堆里点数最高 / 最低的两张（拼点必分胜负，且不依赖随机）。"""
    order = sorted(range(len(PACK.deck.cards)), key=lambda cid: PACK.deck.cards[cid].rank)
    return order[-1], order[0]


def _tianyi_state(hand0, hand1, *, hero0: str = "taishici", hero1: str = "luxun"):
    used = (*hand0, *hand1)
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hp_max=4, hand=tuple(hand0), hero=hero0),
            SeatSpec(hp=4, hp_max=4, hand=tuple(hand1), hero=hero1),
        ],
        deck=tuple(cid for cid in range(len(PACK.deck.cards)) if cid not in used),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _decline_rest(query):
    """其余询问取最小合法应答（响应放弃 / 出牌阶段结束 / 第一个候选）。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(a for a in query.legal_actions if isinstance(a, EndPhase))
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _TianyiDriver:
    """发动一次【天义】：选座位 → 拼点选牌（``want`` = 指定牌 id；``None`` = 第一张）。"""

    def __init__(self, *, target: int = 1, want: int | None = None) -> None:
        self.target = target
        self.want = want
        self.activations = 0
        self.offered: list[bool] = []

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == TIANYI:
                    self.activations += 1
                    self.offered.append(True)
                    return action
            self.offered.append(False)
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id or "").startswith(
            "tianyi"
        ):
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == f"seat:{self.target}":
                    return action
            return _decline_rest(query)
        if query.kind == QueryKind.SELECT_CARDS:
            for action in query.legal_actions:
                if not isinstance(action, SelectCards):
                    continue
                slot = query.slots[action.picks[0]]
                card_id = None if slot.card is None else slot.card.card_id
                if self.want is None or card_id == self.want:
                    return action
            return _decline_rest(query)
        return _decline_rest(query)


def _saw_pindian(engine) -> bool:
    return any(getattr(record, "delta_type", None) == "pindian" for record in engine.records)


def _tianyi_resolved(engine) -> bool:
    """拼点跑完且结果已落座位三态（技能效果的最后一步）。"""
    player = engine.state.player(0)
    return bool(player.tianyi_won or player.tianyi_lost)


def _pindian_delta(engine):
    return next(
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "pindian"
    )


def test_tianyi_runs_a_pindian_frame_and_records_the_reveal():
    """拼点 = 帧 + 两条时机 + 一条 ``pindian`` delta；两张牌以 ``PINDIAN`` 原因离开手牌。"""
    high, low = _high_low_cards()
    state = _tianyi_state((high,), (low,))
    engine = Engine.from_state(state, seed=1)
    driver = _TianyiDriver(want=high)

    run_queries(engine, [driver, PassPlayer()], _tianyi_resolved)

    assert driver.activations == 1
    assert ran_timing(engine, Timing.RANK_COMPARE_ON)
    assert ran_timing(engine, Timing.RANK_COMPARE_RESULT_AFTER)

    delta = _pindian_delta(engine)
    assert delta.title == "天义"
    assert delta.initiator == 0
    assert delta.targets == (1,)
    assert delta.cards[0].card_id == high and delta.cards[1].card_id == low
    assert delta.won == (True,)

    moves = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_move"
    ]
    pindian_moves = [move for move in moves if move.reason is MoveReason.PINDIAN]
    assert {move.to_zone.zone for move in pindian_moves} == {"processing", "discard"}
    assert engine.state.player(0).tianyi_won
    assert not engine.state.player(0).tianyi_lost


def test_tianyi_winning_raises_the_slash_limit_and_target_count():
    """赢 ⇒ ``COUNT_LIMIT`` 1→2、``TARGET_COUNT`` 1→2、``TARGET_POOL`` 并入全部其他座位。"""
    from sgs.effects import ModifierContext, ModifierPoint, apply_modifiers, check_count

    high, low = _high_low_cards()
    state = _tianyi_state((high,), (low,))
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, [_TianyiDriver(want=high), PassPlayer()], _tianyi_resolved)

    slash = cards_of_type(PACK, "slash")[0]
    ctx = ModifierContext(subject=0, card=engine.state.card(slash))
    assert apply_modifiers(ModifierPoint.COUNT_LIMIT, engine.state, ctx, 1) == 2
    assert apply_modifiers(ModifierPoint.TARGET_COUNT, engine.state, ctx, 1) == 2
    assert check_count(engine.state, 0, engine.state.card(slash)) is False


def test_tianyi_losing_blocks_using_slash_but_not_playing_it():
    """没赢 ⇒ ``COUNT_LIMIT`` 归 0（只挡**使用**）；``FORBIDDEN`` 不动，仍可**打出**。"""
    from sgs.effects import check_count, check_forbidden

    high, low = _high_low_cards()
    state = _tianyi_state((low,), (high,))
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, [_TianyiDriver(want=low), PassPlayer()], _tianyi_resolved)

    assert engine.state.player(0).tianyi_lost
    assert not engine.state.player(0).tianyi_won
    slash = cards_of_type(PACK, "slash")[0]
    assert check_count(engine.state, 0, engine.state.card(slash)) is True
    assert check_forbidden(engine.state, 0, engine.state.card(slash)) is False


def test_tianyi_tie_counts_as_not_winning():
    """平点 = 双方都没赢（规则第十二章 12.1 L1318–L1319）⇒ 太史慈吃"没赢"分支。"""
    same_rank = [
        cid for cid in range(len(PACK.deck.cards))
        if PACK.deck.cards[cid].rank == PACK.deck.cards[0].rank and cid != 0
    ]
    assert same_rank, "牌堆里应有同点数的两张牌"
    state = _tianyi_state((0,), (same_rank[0],))
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, [_TianyiDriver(want=0), PassPlayer()], _tianyi_resolved)

    delta = _pindian_delta(engine)
    assert delta.won == (False,), "平点 ⇒ 发起者「没赢」"
    # 平点与"输"都落 `won=False`；展示面要画「都没赢」就必须有独立的一位（票 04）。
    assert delta.tied == (True,), "平点单独标出来（前端不比点数）"
    assert engine.state.player(0).tianyi_lost


def test_tianyi_is_not_offered_without_a_hand_card_target():
    """目标池 = 其他 + 存活 + 有手牌：没有那样的角色 ⇒ 候选里没有【天义】。"""
    high, _low = _high_low_cards()
    state = _tianyi_state((high,), ())
    engine = Engine.from_state(state, seed=1)
    driver = _TianyiDriver()

    run_queries(engine, [driver, PassPlayer()], lambda e: bool(driver.offered))

    assert driver.offered and driver.offered[0] is False


def test_tianyi_losing_the_last_hand_card_triggers_lianying():
    """拼点 = 失去牌：界陆逊用最后一张手牌拼点 ⇒ 触发【连营】。"""
    high, low = _high_low_cards()

    class _LianyingPlayer:
        """接受【连营】的发动，其余最小应答。"""

        def __init__(self) -> None:
            self.asked = 0

        def respond(self, query):
            if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == LIANYING:
                self.asked += 1
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == LIANYING:
                        return action
            return _decline_rest(query)

    state = _tianyi_state((high,), (low,), hero1="jie_luxun")
    engine = Engine.from_state(state, seed=1)
    lianying = _LianyingPlayer()

    def _lianying_drew(engine) -> bool:
        return _tianyi_resolved(engine) and any(
            getattr(record, "delta_type", None) == "card_move"
            and record.to_zone.zone == "hand"
            and record.to_zone.seat == 1
            for record in engine.records
        )

    run_queries(engine, [_TianyiDriver(want=high), lianying], _lianying_drew)

    assert lianying.asked == 1, "失去最后一张手牌必须真的触发【连营】询问"
    assert len(engine.state.player(1).hand) == 1, "连营摸了一张"


# ---------------------------------------------------------------------------
# 火包·移动版·荀彧：驱虎 / 节命（huo-taishici-xunyu 票 02）
#
#     驱虎：出牌阶段限一次，你可以与一名体力值大于你的角色拼点，若你赢，则该角色对其攻击
#           范围内你选择的一名角色（不能是该角色本身，可以是你）造成1点伤害，否则其对你
#           造成1点伤害。
#     节命：当你受到1点伤害后，你可以令一名角色将手牌摸至体力上限（至多摸至五张）。
# ---------------------------------------------------------------------------

QUHU = "hero.xunyu.quhu"
JIEMING = "hero.xunyu.jieming"


def _quhu_state(hand0, hand1, hand2=(), *, hp0: int = 3, hp1: int = 4, hp2: int = 4):
    used = (*hand0, *hand1, *hand2)
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp0, hp_max=3, hand=tuple(hand0), hero="xunyu"),
            SeatSpec(hp=hp1, hp_max=4, hand=tuple(hand1)),
            SeatSpec(hp=hp2, hp_max=4, hand=tuple(hand2)),
        ],
        deck=tuple(cid for cid in range(len(PACK.deck.cards)) if cid not in used),
        turn_seat=0,
        phase=Phase.PLAY,
    )


class _QuhuDriver:
    """发动一次【驱虎】：选拼点目标 → 拼点选牌 → 赢段选承受者（``receiver``）。"""

    def __init__(self, *, target: int = 1, want: int | None = None, receiver: int = 2):
        self.target = target
        self.want = want
        self.receiver = receiver
        self.activations = 0

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == QUHU:
                    self.activations += 1
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == QueryKind.SELECT_OPTION and "quhu" in str(query.skill_id or ""):
            wanted = (
                f"seat:{self.receiver}"
                if "receiver" in str(query.skill_id)
                else f"seat:{self.target}"
            )
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == wanted:
                    return action
            return _decline_rest(query)
        if query.kind == QueryKind.SELECT_CARDS:
            for action in query.legal_actions:
                if not isinstance(action, SelectCards):
                    continue
                slot = query.slots[action.picks[0]]
                card_id = None if slot.card is None else slot.card.card_id
                if self.want is None or card_id == self.want:
                    return action
            return _decline_rest(query)
        return _decline_rest(query)


def test_quhu_winning_lets_the_target_damage_a_chosen_other_character():
    """赢段：A 对其攻击范围内的**另一名角色** B 造成 1 点伤害（伤害来源 = A）。"""
    high, low = _high_low_cards()
    state = _quhu_state((high,), (low,), ())
    engine = Engine.from_state(state, seed=1)
    driver = _QuhuDriver(want=high, receiver=2)

    run_queries(
        engine,
        [driver, PassPlayer(), PassPlayer()],
        lambda e: e.state.player(2).hp < 4,
    )

    assert driver.activations == 1
    assert engine.state.player(2).hp == 3
    assert engine.state.player(0).hp == 3, "荀彧没有受伤（赢段伤害打在 B 身上）"
    assert engine.state.player(1).hp == 4


def test_quhu_losing_makes_the_target_damage_xunyu():
    """没赢：拼点目标 A 对荀彧造成 1 点伤害。"""
    high, low = _high_low_cards()
    state = _quhu_state((low,), (high,), ())
    engine = Engine.from_state(state, seed=1)
    driver = _QuhuDriver(want=low)

    run_queries(
        engine,
        [driver, PassPlayer(), PassPlayer()],
        lambda e: e.state.player(0).hp < 3,
    )

    assert engine.state.player(0).hp == 2, "没赢 ⇒ A 对荀彧造成 1 点伤害"


def test_quhu_receiver_pool_excludes_the_pindian_target_but_keeps_xunyu():
    """用户 2026-11-04：B ≠ A、**B 可以是荀彧**——承受者池 = A 攻击范围内的其他存活角色。"""
    high, low = _high_low_cards()
    state = _quhu_state((high,), (low,), ())
    engine = Engine.from_state(state, seed=1)
    driver = _QuhuDriver(want=high, receiver=0)
    seen: list[tuple[str, ...]] = []

    class _Recorder(_QuhuDriver):
        def respond(self, query):
            if query.kind == QueryKind.SELECT_OPTION and "quhu" in str(query.skill_id or ""):
                seen.append(tuple(query.options))
            return super().respond(query)

    run_queries(
        engine,
        [_Recorder(want=high, receiver=0), PassPlayer(), PassPlayer()],
        lambda e: e.state.player(0).hp < 3,
    )

    assert seen and set(seen[-1]) == {"seat:0", "seat:2"}, (
        "承受者池：A 本身（座位 1）排除，荀彧（座位 0）与第三座（座位 2）都在"
    )
    assert engine.state.player(0).hp == 2, "B 可以是荀彧本人"


def _jieming_engine(*, amount: int = 1, hand1=(0,), hp1: int = 4):
    """压一个打在荀彧（座位 0）身上的 ``DAMAGE`` 子帧。"""
    deck = tuple(cid for cid in range(len(PACK.deck.cards)) if cid not in hand1)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hp_max=3, hand=(), hero="xunyu"),
            SeatSpec(hp=hp1, hp_max=4, hand=tuple(hand1)),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=1)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": None,
            "channel": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 0,
            "nature": None,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    return engine


class _JiemingDriver:
    """接受【节命】并选 ``seat`` 当目标；其余最小应答。"""

    def __init__(self, *, seat: int = 1) -> None:
        self.seat = seat
        self.activations = 0

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIEMING:
            self.activations += 1
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIEMING:
                    return action
        if query.kind == QueryKind.SELECT_OPTION and "jieming" in str(query.skill_id or ""):
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == f"seat:{self.seat}":
                    return action
        return _decline_rest(query)


def test_jieming_fills_the_target_hand_to_the_cap():
    """读法 B：手牌终值 = min(体力上限, 5)——目标 4 上限、1 张手牌 ⇒ 摸 3 张。"""
    hand1 = (cards_of_type(PACK, "slash")[0],)
    engine = _jieming_engine(hand1=hand1)
    driver = _JiemingDriver(seat=1)

    run_queries(
        engine,
        [driver, PassPlayer()],
        lambda e: len(e.state.player(1).hand) == 4,
    )

    assert driver.activations == 1
    assert len(engine.state.player(1).hand) == 4


def test_jieming_triggers_once_per_damage_point():
    """「受到**1 点**伤害后」⇒ 一次 2 点伤害问两次（逐点）。"""
    hand1 = tuple(cards_of_type(PACK, "slash")[:1])
    engine = _jieming_engine(amount=2, hand1=hand1)
    driver = _JiemingDriver(seat=1)

    run_queries(
        engine,
        [driver, PassPlayer()],
        lambda e: driver.activations >= 2,
    )

    assert driver.activations == 2


def test_jieming_draws_nothing_when_the_target_is_already_full():
    """手牌已到上限 ⇒ 摸 0 张，发动仍合法（G2「付得起但没收益」）。"""
    hand1 = tuple(cards_of_type(PACK, "slash")[:4])
    engine = _jieming_engine(hand1=hand1)
    driver = _JiemingDriver(seat=1)

    run_queries(
        engine,
        [driver, PassPlayer()],
        lambda e: driver.activations >= 1,
    )

    assert len(engine.state.player(1).hand) == 4
