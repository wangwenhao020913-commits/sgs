"""武将总表：25 名标准 + 界版已到达条目的数据断言与登记（票 12；M6 P0 三票收口 16 → 25）。

本文件只装**跨技能**的登记事实（逐技能的构造局面单测按族住各自文件）：

- ``CATALOG`` 的规模与分区：装备 22 + 武将 64 = **86 条**，且武将那半恰是数据文件里
  全部启用武将的技能清单（``heroes.standard.json`` + ``heroes.jie.json`` 是名单的唯一真源）；
- **同一 owner 同一时机只有一条技能**（票 12 §要做什么 3）：这是 R5-Q1「同一时点多技能
  自选顺序不建」的落地依据——将来加武将时这条断言先变红，逼出一次重新裁定；
- 25 名的 ``gender`` / ``kingdom`` / ``hp_max`` 与规则文档第九章逐项一致（表驱动，
  期望值是**手抄的独立真源**，不是从数据文件回读）；
- **P0 收口**（M6 票 03）：标准 25 名全部启用，启用池恰是这 25 名（没有"落选"了）。

**测试函数名的历史口径**：带 ``sixteen`` / ``twenty_nine`` 的名字是 M3 收口时的节点名，
被 ``tests/test_dod_m3.py`` 的 DoD 绑定（node id 必须继续存在）；M6 P0 只改函数体里的
数字，不重命名（M3 DoD 记的是 M3 当时的事实）。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sgs.data import (
    Gender,
    Kingdom,
    SkillType,
    enabled_hero_ids,
    hero_skill_id,
    load_content_pack,
    load_heroes,
)
from sgs.effects import ModifierPoint
from sgs.engine import DRAW_COUNT, Engine
from sgs.skills import CATALOG, PROVIDERS, flow_for
from sgs.skills.catalog import MATCH_OWNER_KEY
from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent
from sgs.state import (
    CardRef,
    EndPhase,
    GameState,
    MoveReason,
    Pass,
    SelectOption,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import DISPATCH_WHITELIST, Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
HEROES = load_heroes()
JIE_HEROES = load_heroes("jie")
"""界标准的第一份数据（M6 票 04 起陆续到达）：与 ``HEROES`` **分开存**、同一套校验。"""

# 25 名启用武将（票 12 §落地表的 16 行 + M6 票 01 的魏三名 + 票 02 的魏/蜀三名 +
# 票 03 的吴三名 —— P0 收口：整张标准表全开）。
ENABLED = (
    "caocao",
    "simayi",
    "xiahoudun",
    "zhangliao",
    "xuchu",
    "guojia",
    "zhenji",
    "liubei",
    "guanyu",
    "zhangfei",
    "zhaoyun",
    "machao",
    "zhugeliang",
    "huangyueying",
    "sunquan",
    "ganning",
    "lvmeng",
    "huanggai",
    "zhouyu",
    "luxun",
    "daqiao",
    "sunshangxiang",
    "huatuo",
    "lvbu",
    "diaochan",
)

# 落选 0 名（M6 票 03 的 P0 收口：标准 25 名全部启用）。
DISABLED: tuple[str, ...] = ()

# 界标准已到达的武将（M6 票 04 起逐票追加；本票只有界关羽 / 界马超，且两名都启用）。
JIE_ENABLED = tuple(hero.id for hero in JIE_HEROES.heroes if hero.enabled)

# 规则文档第九章 933–1054 行的逐项事实（手抄的真源）：id → (势力, 性别, 体力上限)。
_HERO_STATS = {
    "caocao": (Kingdom.WEI, Gender.MALE, 4),
    "simayi": (Kingdom.WEI, Gender.MALE, 3),
    "guojia": (Kingdom.WEI, Gender.MALE, 3),
    "liubei": (Kingdom.SHU, Gender.MALE, 4),
    "guanyu": (Kingdom.SHU, Gender.MALE, 4),
    "zhaoyun": (Kingdom.SHU, Gender.MALE, 4),
    "machao": (Kingdom.SHU, Gender.MALE, 4),
    "zhugeliang": (Kingdom.SHU, Gender.MALE, 3),
    "sunquan": (Kingdom.WU, Gender.MALE, 4),
    "zhouyu": (Kingdom.WU, Gender.MALE, 3),
    "luxun": (Kingdom.WU, Gender.MALE, 3),
    "daqiao": (Kingdom.WU, Gender.FEMALE, 3),
    "sunshangxiang": (Kingdom.WU, Gender.FEMALE, 3),
    "huatuo": (Kingdom.QUN, Gender.MALE, 3),
    "lvbu": (Kingdom.QUN, Gender.MALE, 4),
    "diaochan": (Kingdom.QUN, Gender.FEMALE, 3),
    # P0 补齐的另外 9 名同样逐项核对（结构存在即为它们负责）。
    "xiahoudun": (Kingdom.WEI, Gender.MALE, 4),
    "zhangliao": (Kingdom.WEI, Gender.MALE, 4),
    "xuchu": (Kingdom.WEI, Gender.MALE, 4),
    "zhangfei": (Kingdom.SHU, Gender.MALE, 4),
    "huangyueying": (Kingdom.SHU, Gender.FEMALE, 3),
    "ganning": (Kingdom.WU, Gender.MALE, 4),
    "lvmeng": (Kingdom.WU, Gender.MALE, 4),
    "huanggai": (Kingdom.WU, Gender.MALE, 4),
    "zhenji": (Kingdom.WEI, Gender.FEMALE, 3),
}

PLAYABLE = ("slash", "peach", "dodge")


# ---------------------------------------------------------------------------
# ① CATALOG 的一分为二（装备 22 + 武将 64 = 86）
# ---------------------------------------------------------------------------


def test_the_catalog_holds_twenty_two_equipment_and_twenty_nine_hero_skills():
    """验收 1：``CATALOG`` = **86** 条（22 装备 + 64 武将），且两半由 ``carrier`` 分区。

    M5-01a 补 5 条军争装备技能占位（白银狮子 / 藤甲 / 古锭刀 / 朱雀羽扇 / 骅骝）；
    M6 票 01 把武将那半 29 → 32（【刚烈】/【突袭】/【裸衣】），票 02 → 37
    （【洛神】/【倾国】/【咆哮】/【集智】/【奇才】），票 03 → 40
    （【奇袭】/【克己】/【苦肉】 = P0 收口），票 04 → 44（界关羽【武圣】/【义绝】、
    界马超【马术】/【铁骑】 = 首份界版数据），票 05 → 46（界张飞【咆哮】/【替身】 =
    首个**限定技**载体），票 06 → 52（界徐庶【诛害】/【潜心】/☆【荐言】、
    界吕蒙【克己】/【勤学】/☆【攻心】 = 首个**觉醒技**与首个 ☆ 派生技能载体），
    票 07 起 56（界甘宁【奇袭】/【奋威】、界貂蝉【离间】/【闭月】 = 首个**限定技**否定载体
    与首个"不可响应"载体），票 08 起 62（界周瑜【英姿】/【反间】、界夏侯惇【刚烈】/【清俭】、
    界黄月英【集智】/【奇才】 = **手牌上限双轴**与**事件帧无效**的首批载体），
    票 09 起 64（界甄姬【洛神】/【倾国】 = 首个**牌标签**载体）。
    函数名沿用 M3 的节点名（``test_dod_m3`` 的 DoD 绑定），数字以函数体为准。
    """
    equipment = [d for d in CATALOG.values() if d.carrier.value == "EQUIPMENT"]
    heroes = [d for d in CATALOG.values() if d.carrier.value == "HERO"]

    assert len(CATALOG) == 86
    assert len(equipment) == 22
    assert len(heroes) == 64


def test_the_hero_half_of_the_catalog_is_exactly_the_enabled_hero_sheet():
    """武将那半 = 全部启用武将的技能清单（标准表 + 界版扩展表；数据文件是唯一真源）。"""
    expected = {
        f"hero.{hero.id}.{skill.id}"
        for table in (HEROES, JIE_HEROES)
        for hero in table.heroes
        if hero.enabled
        for skill in hero.skills
    }
    actual = {d.skill_id for d in CATALOG.values() if d.carrier.value == "HERO"}

    assert actual == expected
    assert len(expected) == 64


def test_every_hero_skill_id_is_fully_qualified():
    enabled = set(ENABLED) | set(JIE_ENABLED)
    for definition in CATALOG.values():
        if definition.carrier.value != "HERO":
            continue
        parts = definition.skill_id.split(".")
        assert parts[0] == "hero"
        assert parts[1] in enabled, "技能 id 的武将在启用名单里"
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# ② 同一 owner 同一时机只有一条技能（R5-Q1 的落地依据）
# ---------------------------------------------------------------------------


def test_the_enabled_heroes_have_no_two_skills_on_the_same_timing():
    """验收 2：25 名里不存在同一 owner 同一时机的两条技能。

    判据 = 该武将的每条技能定义的 ``hooks`` 的时机集合两两不交；主公技与普通技同样
    受此约束（它们同住一张武将牌）。将来加武将时这条断言先变红。
    """
    duplicated: dict[str, list[str]] = defaultdict(list)
    for hero in (*HEROES.heroes, *JIE_HEROES.heroes):
        if hero.id not in set(ENABLED) | set(JIE_ENABLED):
            continue
        by_timing: dict[Timing, list[str]] = defaultdict(list)
        for skill in hero.skills:
            definition = CATALOG[f"hero.{hero.id}.{skill.id}"]
            for hook in definition.hooks:
                by_timing[hook.timing].append(skill.id)
        for timing, owners in by_timing.items():
            if len(owners) > 1:
                duplicated[f"{hero.id}:{timing.value}"] = owners

    assert not duplicated, f"同一时机的多条技能必须有重新裁定：{dict(duplicated)}"


# ---------------------------------------------------------------------------
# ③ 25 名的 gender / kingdom / hp_max 与第九章一致
# ---------------------------------------------------------------------------


def test_every_hero_matches_the_rule_book_stats():
    """验收 4：表驱动逐名核对（期望值是手抄的独立真源，不从数据文件回读）。"""
    assert set(_HERO_STATS) == {hero.id for hero in HEROES.heroes}
    for hero in HEROES.heroes:
        kingdom, gender, hp_max = _HERO_STATS[hero.id]
        assert (hero.kingdom, hero.gender, hero.hp_max) == (kingdom, gender, hp_max), hero.id


# ---------------------------------------------------------------------------
# ④ 启用门控：25 名全开（P0 收口）
# ---------------------------------------------------------------------------


def test_the_enabled_pool_is_exactly_the_sixteen_and_the_rest_stay_dark():
    """验收 5：启用池 = **25** 名（M6 票 03 的 P0 收口："另外几名的启停"这一档消失了）。

    函数名沿用 M3 的节点名（``test_dod_m3`` 的 DoD 绑定），数字以函数体为准。启用字段
    仍是唯一判据（不是"数据里存在即启用"）：读回的门控行为由
    :func:`test_enabled_hero_ids_reads_the_table_enablement`（``tests/test_hero_dealing.py``）
    用一张只开一名的合成表继续钉住。
    """
    ids = enabled_hero_ids(HEROES)

    assert set(ids) == set(ENABLED) == {hero.id for hero in HEROES.heroes}
    assert len(ids) == 25
    assert DISABLED == (), "P0 收口后没有落选武将"
    assert all(HEROES.by_id(hero_id).enabled for hero_id in ENABLED)


def test_a_hero_skill_missing_from_the_catalog_never_produces_an_instance(monkeypatch):
    """技能不在 ``CATALOG`` 里 ⇒ 连构造局面都产不出实例（原"未启用武将"的判据）。

    P0 收口后 25 名全部启用，"未启用 ⇒ 无实例"这一档不再有真实载体；它当时真正依赖的
    机制是**技能定义缺席**（``resolve_instances`` 只查 ``CATALOG``，不看 ``enabled``——
    启用门控在**发放**侧，见 ``test_the_enabled_pool_feeds_a_real_deal_without_the_hero_substream``）。
    故这里用 monkeypatch 拿掉甘宁的【奇袭】定义，断言同一判据仍然成立。
    """
    from sgs.skills import catalog as catalog_module
    from sgs.skills import resolve_instances

    without_qixi = {
        skill_id: definition
        for skill_id, definition in catalog_module.CATALOG.items()
        if skill_id != "hero.ganning.qixi"
    }
    monkeypatch.setattr(catalog_module, "CATALOG", MappingProxyType(without_qixi))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="ganning"), SeatSpec(hero="lvmeng")],
    )

    owned = {instance.skill_id for instance in resolve_instances(state)}
    assert "hero.ganning.qixi" not in owned, "缺定义 ⇒ 不产出实例"
    assert "hero.lvmeng.keji" in owned, "同一局面里别人的已登记技能照常产出"


def test_the_enabled_pool_feeds_a_real_deal_without_the_hero_substream():
    """``hero_pool=()`` = 数据文件的启用池 ⇒ 真发牌取到 25 名里的两名。"""
    from sgs.data import deal_heroes
    from sgs.engine import RandomSource

    dealt = deal_heroes(
        seat_count=2,
        stream=RandomSource(1).substream("hero"),
        hero_pool=(),
        table=HEROES,
    )

    assert dealt is not None
    assert len(set(dealt)) == 2
    assert set(dealt) <= set(ENABLED)

    # 启用门控的**发放侧**判据：合成一张只开第一名的表，启用池随之收缩到一名。
    only_first = HEROES.model_copy(
        update={
            "heroes": (
                HEROES.heroes[0].model_copy(update={"enabled": True}),
                *(
                    hero.model_copy(update={"enabled": False})
                    for hero in HEROES.heroes[1:]
                ),
            )
        }
    )
    shrunk = deal_heroes(
        seat_count=1,
        stream=RandomSource(1).substream("hero"),
        hero_pool=(),
        table=only_first,
    )
    assert shrunk == (HEROES.heroes[0].id,), "禁用其它武将后启用池只剩第一名"


# ---------------------------------------------------------------------------
# ⑤ 性别接线：真实对局里 ``Player.gender`` 由武将数据填值
# ---------------------------------------------------------------------------


def test_a_real_game_fills_every_seat_gender_from_its_hero():
    """票 04 的遗留议题在此收口：性别随武将数据填进 ``Player``。"""
    from sgs.data import RuleConfig
    from sgs.engine import build_initial

    state, _ = build_initial(
        seed=3,
        rule_config=RuleConfig(player_count=4),
        pack=PACK,
        heroes=("diaochan", "zhouyu", "daqiao", "lvbu"),
    )

    assert [player.gender for player in state.players] == [
        Gender.FEMALE,
        Gender.MALE,
        Gender.FEMALE,
        Gender.MALE,
    ]
    assert [player.gender for player in state.players] == [
        HEROES.by_id(player.hero).gender for player in state.players
    ]


# ---------------------------------------------------------------------------
# ⑥ 时机面纪律：本批不新解封枚举位
# ---------------------------------------------------------------------------


def test_the_four_new_skills_only_use_dispatched_timings():
    """票 12 只「照表登记」：四条新技能的钩子时机都已在派发白名单里（不新解封枚举位）。"""
    for skill_id in (
        "hero.machao.mashu",
        "hero.daqiao.liuli",
        "hero.zhouyu.yingzi",
        "hero.diaochan.biyue",
    ):
        definition = CATALOG[skill_id]
        for hook in definition.hooks:
            assert hook.timing in DISPATCH_WHITELIST, (skill_id, hook.timing)
        assert definition.seams == (), f"{skill_id} 不该带帧内接缝"


# ---------------------------------------------------------------------------
# 共用脚本参与者与推进器（与票 10 的手写推进同形，已收进 ``support``）
# ---------------------------------------------------------------------------

_PASS = PassPlayer()


def _run(engine, players, stop):
    return run_queries(engine, players, stop)


def _drawn(engine) -> bool:
    """阶段体的**结算后**判据：时机记录在派发**之前**落（票 10 的帧外阶段体），
    故推进器要用「钩子已经跑完」这个更晚的事实当停止条件——摸牌阶段看基础摸牌、
    结束阶段看下一回合的 ``TURN_AFTER_END``。"""
    return any(
        record.kind == "timing" and record.timing is Timing.PHASE_DRAW_END
        for record in engine.records
    )


def _after(engine, timing: Timing) -> bool:
    """``timing`` 之后**再过一条**同帧记录才停：用于"钩子已跑完"的观察点。"""
    if not ran_timing(engine, timing):
        return False
    if timing is Timing.PHASE_DRAW:
        return _drawn(engine)
    return ran_timing(engine, Timing.TURN_AFTER_END)


class _PhaseBodyPlayer:
    """阶段体技能的脚本玩家：是否发动 + 其余最小应答。"""

    def __init__(self, *, skill_id: str, activate: bool) -> None:
        self.skill_id = skill_id
        self.activate = activate
        self.queries = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == self.skill_id:
            self.queries.append(query)
            if not self.activate:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return next(a for a in query.legal_actions if a.kind == "ActivateSkill")
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


# ---------------------------------------------------------------------------
# ⑦ 周瑜【英姿】：摸牌阶段多摸一张（锁定：非锁定，问一次）
# ---------------------------------------------------------------------------


def test_yingzi_draws_one_extra_card_in_the_draw_phase():
    deck = tuple(range(10, 20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhouyu"), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _PhaseBodyPlayer(skill_id="hero.zhouyu.yingzi", activate=True)

    _run(engine, [player, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert len(player.queries) == 1, "问一次「是否发动」"
    assert len(engine.state.player(0).hand) == DRAW_COUNT + 1
    assert list(engine.state.player(0).hand) == list(deck[: DRAW_COUNT + 1])


def test_declining_yingzi_leaves_the_draw_phase_at_two_cards():
    deck = tuple(range(10, 20))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhouyu"), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _PhaseBodyPlayer(skill_id="hero.zhouyu.yingzi", activate=False)

    _run(engine, [player, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert len(player.queries) == 1
    assert list(engine.state.player(0).hand) == list(deck[:DRAW_COUNT])


# ---------------------------------------------------------------------------
# ⑧ 貂蝉【闭月】：结束阶段摸一张
# ---------------------------------------------------------------------------


def test_biyue_draws_one_card_in_the_finish_phase():
    """结束阶段摸一张：摸到的正是牌堆顶。

    摸牌阶段的 2 张在弃牌阶段被弃掉（4 + 2 > 体力上限 4），故结束时手里是
    「原来的 4 张 + 结束阶段摸的第 3 张」。
    """
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4, hero="diaochan", hand=tuple(range(50, 54))), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _PhaseBodyPlayer(skill_id="hero.diaochan.biyue", activate=True)

    _run(engine, [player, _PASS], lambda e: _after(e, Timing.PHASE_FINISH))

    assert len(player.queries) == 1, "结束阶段问一次「是否发动」"
    assert engine.state.player(0).hp_max == 4
    assert sorted(engine.state.player(0).hand) == sorted([*range(50, 54), deck[2]])
    assert [engine.state.card(card_id).card_id for card_id in engine.state.discard] == list(
        deck[:2]
    )


def test_declining_biyue_leaves_the_hand_alone():
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4, hero="diaochan", hand=tuple(range(50, 54))), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _PhaseBodyPlayer(skill_id="hero.diaochan.biyue", activate=False)

    _run(engine, [player, _PASS], lambda e: _after(e, Timing.PHASE_FINISH))

    assert len(player.queries) == 1
    assert sorted(engine.state.player(0).hand) == list(range(50, 54)), "不发动 ⇒ 一张都不摸"


# ---------------------------------------------------------------------------
# ⑨ 张辽【突袭】：摸牌阶段放弃摸牌，改为获得至多两名其他角色各一张手牌（M6 票 01）
#
# 官方 §九 L948–950。形态 = 阶段体（``PHASE_DRAW``）+ 一步座位询问（``PickSeats``，
# 1–2 名、可少选）+ 不摸牌（引擎的摸牌张数被技能改写成 0）。
# ---------------------------------------------------------------------------

TUXI = "hero.zhangliao.tuxi"
LUOYI = "hero.xuchu.luoyi"


class _TuxiPlayer:
    """【突袭】的脚本玩家：按 ``seats`` 依次选人，选完用 ``Pass`` 收「至多两名」。"""

    def __init__(self, *, seats: tuple[int, ...] = (), activate: bool = True) -> None:
        self._seats = list(seats)
        self.activate = activate
        self.queries: list = []
        self.options: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == TUXI:
            self.queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "SELECT_OPTION" and str(query.skill_id).startswith("tuxi:"):
            self.options.append(query)
            if self._seats:
                seat = self._seats.pop(0)
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectOption)
                    and action.choice == f"seat:{seat}"
                )
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
            return query.legal_actions[0]
        if query.kind == "PLAY_PHASE":
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        return query.legal_actions[0]


def _tuxi_deck(*hands: tuple[int, ...]) -> tuple[int, ...]:
    used = {card_id for hand in hands for card_id in hand}
    return tuple(index for index in range(len(PACK.deck.cards)) if index not in used)


def test_tuxi_takes_one_hidden_hand_card_from_up_to_two_others_instead_of_drawing():
    """发动 ⇒ 牌堆一张不动，两名角色各少一张手牌、张辽多两张。"""
    dodges = cards_of_type(PACK, "dodge")
    hand_a, hand_b = dodges[0], dodges[1]
    deck = _tuxi_deck((hand_a,), (hand_b,))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhangliao"),
            SeatSpec(hand=(hand_a,)),
            SeatSpec(hand=(hand_b,)),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(seats=(1, 2))

    _run(engine, [player, _PASS, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert len(player.queries) == 1, "摸牌阶段问一次「是否发动」"
    assert len(player.options) == 2, "至多两名 ⇒ 选完一人再问一人"
    assert "seat:1" in player.options[0].options and "seat:2" in player.options[0].options
    assert "seat:1" not in player.options[1].options, "同一角色只取一张"
    assert sorted(engine.state.player(0).hand) == sorted([hand_a, hand_b])
    assert engine.state.player(1).hand == []
    assert engine.state.player(2).hand == []
    assert list(engine.state.deck) == list(deck), "放弃摸牌 ⇒ 牌堆一张都没少"


def test_tuxi_can_take_from_only_one_character():
    """「至多两名」⇒ 选一名后放弃，仍算发动（牌堆照样不摸）。"""
    dodges = cards_of_type(PACK, "dodge")
    hand_a, hand_b = dodges[0], dodges[1]
    deck = _tuxi_deck((hand_a,), (hand_b,))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhangliao"),
            SeatSpec(hand=(hand_a,)),
            SeatSpec(hand=(hand_b,)),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(seats=(1,))

    _run(engine, [player, _PASS, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert len(player.options) == 2, "第一问选人、第二问放弃"
    assert sorted(engine.state.player(0).hand) == [hand_a]
    assert engine.state.player(1).hand == []
    assert engine.state.player(2).hand == [hand_b], "没被选中的人手牌不动"
    assert list(engine.state.deck) == list(deck)


def test_declining_tuxi_draws_two_cards():
    dodges = cards_of_type(PACK, "dodge")
    hand_a, hand_b = dodges[0], dodges[1]
    deck = _tuxi_deck((hand_a,), (hand_b,))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="zhangliao"),
            SeatSpec(hand=(hand_a,)),
            SeatSpec(hand=(hand_b,)),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(activate=False)

    _run(engine, [player, _PASS, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert len(player.queries) == 1
    assert player.options == []
    assert list(engine.state.player(0).hand) == list(deck[:DRAW_COUNT])
    assert engine.state.player(1).hand == [hand_a]
    assert engine.state.player(2).hand == [hand_b]


def test_tuxi_is_not_offered_when_no_other_character_has_hand_cards():
    """没有可拿的手牌 ⇒ 该实例连"是否发动"都不问（正常摸两张）。"""
    deck = tuple(range(10, 30))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhangliao"), SeatSpec(), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _TuxiPlayer(seats=())

    _run(engine, [player, _PASS, _PASS], lambda e: _after(e, Timing.PHASE_DRAW))

    assert player.queries == []
    assert list(engine.state.player(0).hand) == list(deck[:DRAW_COUNT])


# ---------------------------------------------------------------------------
# ⑩ 许褚【裸衣】：摸牌阶段少摸一张；若如此做，本回合【杀】/【决斗】伤害 +1
#
# 官方 §九 L952–954。形态 = 阶段体（``PHASE_DRAW``，把摸牌张数改写成 1 并落本回合
# 状态）+ ``DAMAGE_DEAL_AMOUNT`` 修饰器（来源侧）+ 回合末清理。
# ---------------------------------------------------------------------------


class _LuoyiPlayer:
    """【裸衣】的脚本玩家：第一次「是否发动」同意；出牌阶段用第一张指向座位 1 的收益牌。"""

    def __init__(self, *, activate: bool = True, card_type: str = "slash") -> None:
        self.activations_left = 1 if activate else 0
        self.card_type = card_type
        self.queries: list = []
        self.cards_used = 0

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == LUOYI:
            self.queries.append(query)
            if self.activations_left:
                self.activations_left -= 1
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.type == self.card_type
                    and action.targets == (1,)
                ):
                    self.cards_used += 1
                    return action
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if query.kind == "DODGE":
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return query.legal_actions[0]


def _turn_ends(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
    )


def test_luoyi_draws_one_fewer_and_this_turns_slash_deals_one_more():
    slash = cards_of_type(PACK, "slash")[0]
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index != slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="xuchu", hand=(slash,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(player.queries) == 1
    assert engine.state.player(0).hand == [deck[0]], "少摸一张 ⇒ 只摸到牌堆顶第 1 张"
    assert engine.state.player(1).hp == 2, "本回合的【杀】基础 1 点 + 【裸衣】1 点"
    assert player.cards_used == 1


def test_luoyi_also_adds_one_to_this_turns_duel_damage():
    """规则写的是「【杀】或【决斗】」：决斗造成的伤害同样 +1（付 1 张换 2 点）。"""
    duel = cards_of_type(PACK, "duel")[0]
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index != duel)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="xuchu", hand=(duel,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer(card_type="duel")

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert player.cards_used == 1
    assert engine.state.player(1).hp == 2, "【决斗】基础 1 点 + 【裸衣】1 点"
    assert engine.state.player(0).hp == 4, "许褚自己不受伤"


def test_declining_luoyi_draws_two_and_the_slash_deals_one():
    slash = cards_of_type(PACK, "slash")[0]
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index != slash)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="xuchu", hand=(slash,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer(activate=False)

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(player.queries) == 1
    assert list(engine.state.player(0).hand) == list(deck[:DRAW_COUNT])
    assert engine.state.player(1).hp == 3, "不发动 ⇒ 【杀】只有基础 1 点"


def test_the_luoyi_bonus_is_gone_after_the_turn_ends():
    """+1 的作用域 = **本回合结束**：下个自己的回合再用【杀】，伤害回到 1 点。"""
    slash_ids = cards_of_type(PACK, "slash")
    first, second = slash_ids[0], slash_ids[1]
    non_slash = [
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type != "slash" and index not in (first, second)
    ]
    deck = (non_slash[0], non_slash[1], non_slash[2], second, *non_slash[3:], slash_ids[2])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="xuchu", hand=(first,)), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _LuoyiPlayer()

    _run(engine, [player, _PASS], lambda e: _turn_ends(e) >= 3)

    # 回合 1：裸衣 + 【杀】= 2 点；回合 2 是对手的空过；回合 3 的【杀】= 1 点。
    assert player.cards_used == 2
    assert engine.state.player(1).hp == 1, "回合结束 +1 消失，第三回合的【杀】只造成 1 点"
    assert engine.state.player(0).hand == [non_slash[0], non_slash[3]], (
        "回合 3 正常摸两张（无【裸衣】），用掉一张【杀】"
    )


# ---------------------------------------------------------------------------
# ⑪ 吕蒙【克己】（M6 票 03）：出牌阶段内未使用 / 打出过【杀】⇒ 可以跳过弃牌阶段
#
# §九 L1011–1012。形态 = 阶段体（``PHASE_DISCARD_START``，非锁定 ⇒ 问"是否发动"）+
# 座位级事实"本回合出牌阶段内是否用过 / 打出过【杀】"（引擎在 ``start_use`` /
# ``play_card`` 写、``_phase_play_start`` 复位）+ ``Engine.skip_discard_phase``。
# 判据只从外部观测：弃牌阶段的 ``SKILL_ACTIVATION`` 询问、DISCARD 询问、手牌最终张数。
# ---------------------------------------------------------------------------

KEJI = "hero.lvmeng.keji"


class _KejiPlayer:
    """【克己】脚本：可选先用一张【杀】，再决定是否发动克己，其余最小应答。"""

    def __init__(self, *, use_slash: bool = False, activate: bool = True) -> None:
        self.use_slash = use_slash
        self.activate = activate
        self.slash_used = False
        self.keji_queries: list = []
        self.discard_queries: list = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == KEJI:
            self.keji_queries.append(query)
            if self.activate:
                for action in query.legal_actions:
                    if getattr(action, "kind", None) == "ActivateSkill":
                        return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "PLAY_PHASE":
            if self.use_slash and not self.slash_used:
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == "slash"
                        and action.targets == (1,)
                    ):
                        self.slash_used = True
                        return action
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if query.kind == "DISCARD":
            self.discard_queries.append(query)
        if query.kind == "DODGE":
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        return query.legal_actions[0]


def _keji_state():
    """4 血吕蒙、起手 4 张（摸牌阶段后 6 张 > 上限 4）——不跳过就要弃 2 张。"""
    dodges = cards_of_type(PACK, "dodge")
    slash = cards_of_type(PACK, "slash")[0]
    hand = (slash, *dodges[:3])
    deck = _keji_deck(hand)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4, hand=hand, hero="lvmeng"), SeatSpec()],
        deck=deck,
        turn_seat=0,
    )
    return state, hand, deck


def _keji_deck(hand: tuple[int, ...]) -> tuple[int, ...]:
    used = set(hand)
    return tuple(index for index in range(len(PACK.deck.cards)) if index not in used)


def test_keji_skips_the_discard_phase_when_no_slash_was_used():
    """正面：本回合没用过【杀】⇒ 弃牌阶段开始问一次，发动后一张都不弃、直接到结束阶段。"""
    state, hand, deck = _keji_state()
    engine = Engine.from_state(state, seed=7)
    player = _KejiPlayer(activate=True)

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(player.keji_queries) == 1, "弃牌阶段开始时问一次「是否发动」"
    assert player.discard_queries == [], "跳过弃牌阶段 ⇒ 不发 DISCARD 询问"
    assert sorted(engine.state.player(0).hand) == sorted((*hand, *deck[:2])), (
        "6 张手牌全部保留（正常弃牌阶段会弃到体力上限 4）"
    )


def test_declining_keji_still_runs_the_discard_phase():
    """反例（可选性）：同样没用过【杀】，放弃发动 ⇒ 照常弃到体力上限。"""
    state, hand, deck = _keji_state()
    engine = Engine.from_state(state, seed=7)
    player = _KejiPlayer(activate=False)

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(player.keji_queries) == 1
    assert len(player.discard_queries) == 1, "不发动 ⇒ 正常弃牌询问"
    assert len(engine.state.player(0).hand) == 4, "弃到体力上限"


def test_keji_is_not_offered_after_using_a_slash_in_the_play_phase():
    """反例（条件）：本回合出牌阶段用过【杀】⇒ 连「是否发动」都不问，照常弃牌。"""
    state, hand, deck = _keji_state()
    engine = Engine.from_state(state, seed=7)
    player = _KejiPlayer(use_slash=True, activate=True)

    _run(engine, [player, _PASS], lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert player.slash_used, "本局确实用过一张【杀】"
    assert player.keji_queries == [], "用过【杀】⇒ 克己不可发动（连询问都不发）"
    assert len(player.discard_queries) == 1
    assert len(engine.state.player(0).hand) == 4


SKILL_FILES: dict[str, str] = {
    # 魏
    "hero.caocao.jianxiong": "test_hero_damage_after.py",
    "hero.caocao.hujia": "test_hero_lord.py",
    "hero.simayi.fankui": "test_hero_damage_after.py",
    "hero.simayi.guicai": "test_hero_rejudge.py",
    "hero.xiahoudun.ganglie": "test_hero_damage_after.py",
    "hero.zhangliao.tuxi": "test_hero_catalog.py",
    "hero.xuchu.luoyi": "test_hero_catalog.py",
    "hero.guojia.tiandu": "test_hero_rejudge.py",
    "hero.guojia.yiji": "test_hero_damage_after.py",
    "hero.zhenji.luoshen": "test_hero_judgment_loop.py",
    "hero.zhenji.qingguo": "test_hero_transform.py",
    # 蜀
    "hero.liubei.rende": "test_hero_active.py",
    "hero.liubei.jijiang": "test_hero_lord.py",
    "hero.guanyu.wusheng": "test_hero_transform.py",
    "hero.zhangfei.paoxiao": "test_hero_limits.py",
    "hero.zhaoyun.longdan": "test_hero_transform.py",
    "hero.machao.mashu": "test_hero_target_change.py",
    "hero.machao.tieqi": "test_hero_response_window.py",
    "hero.zhugeliang.guanxing": "test_hero_arrange.py",
    "hero.zhugeliang.kongcheng": "test_hero_target_pool.py",
    "hero.huangyueying.jizhi": "test_hero_use_after.py",
    "hero.huangyueying.qicai": "test_hero_limits.py",
    # 吴
    "hero.sunquan.zhiheng": "test_hero_active.py",
    "hero.sunquan.jiuyuan": "test_hero_lord.py",
    "hero.ganning.qixi": "test_hero_transform.py",
    "hero.lvmeng.keji": "test_hero_catalog.py",
    "hero.huanggai.kurou": "test_hero_active.py",
    "hero.zhouyu.yingzi": "test_hero_catalog.py",
    "hero.zhouyu.fanjian": "test_hero_active.py",
    "hero.luxun.qianxun": "test_hero_target_pool.py",
    "hero.luxun.lianying": "test_hero_move_after.py",
    "hero.daqiao.guose": "test_hero_transform.py",
    "hero.daqiao.liuli": "test_hero_target_change.py",
    "hero.sunshangxiang.jieyin": "test_hero_active.py",
    "hero.sunshangxiang.xiaoji": "test_hero_move_after.py",
    # 群
    "hero.huatuo.jijiu": "test_hero_transform.py",
    "hero.huatuo.qingnang": "test_hero_active.py",
    "hero.lvbu.wushuang": "test_hero_response_window.py",
    "hero.diaochan.lijian": "test_hero_active.py",
    "hero.diaochan.biyue": "test_hero_catalog.py",
    # 界标准（M6 票 04 起）
    "hero.jie_guanyu.wusheng": "test_hero_jie.py",
    "hero.jie_guanyu.yijue": "test_hero_jie.py",
    "hero.jie_machao.mashu": "test_hero_jie.py",
    "hero.jie_machao.tieqi": "test_hero_jie.py",
    "hero.jie_zhangfei.paoxiao": "test_hero_jie.py",
    "hero.jie_zhangfei.tishen": "test_hero_jie.py",
    # M6 票 07：否定分类四层 + 界甘宁 / 界貂蝉（族级单测文件按票收）
    "hero.jie_ganning.qixi": "test_hero_jie_negation.py",
    "hero.jie_ganning.fenwei": "test_hero_jie_negation.py",
    "hero.jie_diaochan.lijian": "test_hero_jie_negation.py",
    "hero.jie_diaochan.biyue": "test_hero_jie_negation.py",
    # M6 票 06：觉醒技 + ☆ 派生技能 + 界徐庶【诛害】/ 界吕蒙【克己】
    "hero.jie_xushu.zhuhai": "test_hero_jie.py",
    "hero.jie_xushu.qianxue": "test_hero_jie.py",
    "hero.jie_xushu.jianyan": "test_hero_jie.py",
    "hero.jie_lvmeng.keji": "test_hero_catalog.py",
    "hero.jie_lvmeng.qinxue": "test_hero_jie.py",
    "hero.jie_lvmeng.gongxin": "test_hero_jie.py",
    # M6 票 09：标签骨架 + 界甄姬【洛神】/【倾国】
    "hero.jie_zhenji.luoshen": "test_tags.py",
    "hero.jie_zhenji.qingguo": "test_tags.py",
    # M6 票 08：手牌上限双轴 + 界周瑜 / 界夏侯惇 / 界黄月英
    "hero.jie_zhouyu.yingzi": "test_hero_jie_hand_limit.py",
    "hero.jie_zhouyu.fanjian": "test_hero_jie_hand_limit.py",
    "hero.jie_xiahoudun.ganglie": "test_hero_jie_hand_limit.py",
    "hero.jie_xiahoudun.qingjian": "test_hero_jie_hand_limit.py",
    "hero.jie_huangyueying.jizhi": "test_hero_jie_hand_limit.py",
    "hero.jie_huangyueying.qicai": "test_hero_jie_hand_limit.py",
}
"""64 条启用技能 → **点名它的族级单测文件**（票 14 §1 的机械覆盖表；M6 票 01 加 3 条、
票 02 加 5 条、票 03 加 3 条、票 04 加 4 条界将技能、票 05 加界张飞 2 条、票 06 加界徐庶 /
界吕蒙 6 条、票 07 加界甘宁 / 界貂蝉 4 条、票 08 加界周瑜 / 界夏侯惇 / 界黄月英 6 条、
票 09 加界甄姬 2 条）。"""


def test_every_enabled_hero_skill_is_named_by_a_family_test_file():
    """**每条启用技能都至少被一条族级单测点名**（票 14 §1 / 票 12 §要做什么 3 的机械补全）。"""
    skills = {
        hero_skill_id(hero.id, skill.id)
        for table in (HEROES, JIE_HEROES)
        for hero in table.heroes
        if hero.enabled
        for skill in hero.skills
    }

    assert set(SKILL_FILES) == skills, "覆盖表必须恰等于全部启用武将的技能"

    tests_dir = Path(__file__).resolve().parent
    unnamed = sorted(
        skill_id
        for skill_id, filename in SKILL_FILES.items()
        if skill_id.rsplit(".", 1)[1]
        not in (tests_dir / filename).read_text(encoding="utf-8")
    )

    assert not unnamed, "这些技能登记了测试文件、但短 id 没出现在文件里：" + "；".join(unnamed)


# ---------------------------------------------------------------------------
# ⑤ 钩子条件的**持有者自校**：表驱动机械检查（票 13 §要做什么 4）
#
# 报障两例（【反馈】【流离】）同根因：``Hook.condition`` 在条件求值期读不到持有者
# （``skill_owner`` 要到回调前才写），于是"关于我"的判据退化成"关于任何人"——派发环会给
# **每个存活玩家**的实例求条件，缺这一判据 = 任何人触发。这条检查让**同型的第三例再也
# 进不来**：正表逐条给出"持有者是自己"的合成帧（条件为真），再把持有者换成别人、断言为假；
# 覆盖断言要求 正表 ∪ 豁免表 == ``CATALOG`` 里**全部**带条件的钩子（机械发现，不手抄名单）。
# ---------------------------------------------------------------------------

ME = 1
"""合成帧里的**技能持有者**（"我"）。"""

OTHER = 2
"""合成帧里的**别人**——把 ``MATCH_OWNER_KEY`` 换成它，就得到"持有者在别人身上"。"""

_PROBE_PROCESSING_SLASH = cards_of_type(PACK, "slash")[0]
_PROBE_CHANNEL_SLASH = cards_of_type(PACK, "slash")[1]
_PROBE_SOURCE_CARD = cards_of_type(PACK, "peach")[0]
_PROBE_TRICK_CARD = cards_of_type(PACK, "dismantlement")[0]
"""【集智】的正面探针要一张**非延时类锦囊**牌面（命中"使用者是我 + 牌面合格"那一侧）。

与上面三张同属**合成局面用到的牌 id**（牌堆档位固定，模块级算一次）。"""


class _HookFrame:
    """只带 ``data`` 的最小帧：``Hook.condition`` 的输入面就是 ``frame.data``（+ ``host.state``）。"""

    def __init__(self, **data: Any) -> None:
        self.data = data


class _HookHost:
    """只带 ``state`` 的最小宿主：条件里唯一用到的宿主面。"""

    def __init__(self, state: GameState) -> None:
        self.state = state


def _probe_state() -> GameState:
    """合成局面：座位 0 手里有牌（【反馈】的**伤害来源**）、座位 1 **已受伤**（【潜心】的
    "若你已受伤"）、座位 2 本回合造成过伤害（【诛害】）、处理区有一张【杀】（【奸雄】的渠道牌）。

    座位 1（ME）的手牌保持为空 —— 【连营】的正面对照要"移动后手牌为空"。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(_PROBE_SOURCE_CARD,)), SeatSpec(hp=3), SeatSpec()],
        processing=(_PROBE_PROCESSING_SLASH,),
    )
    # 【诛害】的判据是座位级"本回合造成过伤害"事实（合成局面直接置位）。
    state.player(OTHER).damage_dealt_this_turn = True
    return state


def _qinxue_probe_state() -> GameState:
    """【勤学】的正面对照需要"手牌数减体力值 ≥ 2"：ME 体力 1、三张手牌 ⇒ 2。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hp=1, hand=tuple(cards_of_type(PACK, "dodge")[:3])),
            SeatSpec(),
        ],
        processing=(_PROBE_PROCESSING_SLASH,),
    )


def _probe_state_for(skill_id: str) -> GameState:
    """逐技能的正面对照局面（默认同一份；条件里有额外状态前提的单独给）。"""
    if skill_id == "hero.jie_lvmeng.qinxue":
        return _qinxue_probe_state()
    return _probe_state()


def _owner_probes(state: GameState) -> dict[str, _HookFrame]:
    """技能 id → **"持有者是自己"**的合成帧（条件必须为真的那一侧）。

    帧里的 ``target`` / ``targets`` / ``source`` / 移动载荷都指向**别人**（座位 0 / 座位 1），
    只有 ``MATCH_OWNER_KEY`` 由检查本身按 ME / OTHER 改写。逐条的"我"分别是：
    受伤者（【奸雄】【遗计】【刚烈】）、受伤者 + 拿来源牌的持有者（【反馈】）、
    本次【杀】的目标（【流离】）、刚失去最后一张手牌 / 装备的失主（【连营】【枭姬】）。
    """
    move_lianying = MoveEvent(
        cards=(),
        from_zone=ZoneRef(Zone.HAND, ME),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=ME,
        after_holder=None,
        actor=ME,
    )
    move_xiaoji = MoveEvent(
        cards=(),
        from_zone=ZoneRef(Zone.EQUIP, ME),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=ME,
        after_holder=None,
        actor=ME,
    )
    # M5-05a：装备技能也有"关于我"的时机钩子（白银狮子只认**它自己**离开装备区）。
    # 合成一张牌面即可——条件只读 ``move.cards`` 的牌名 id 与 ``from_zone``，不查牌堆。
    move_silver_lion = MoveEvent(
        cards=(
            CardRef(card_id=0, type="silver_lion", name="白银狮子", suit="spade", rank=1),
        ),
        from_zone=ZoneRef(Zone.EQUIP, ME),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.LOSE,
        owner=ME,
        after_holder=None,
        actor=ME,
    )
    return {
        # 每条技能一个**元组**：本技能的每条带条件钩子各一帧（【勤学】两个时机两条）。
        "hero.caocao.jianxiong": (
            _HookFrame(target=ME, source=0, card=state.card(_PROBE_PROCESSING_SLASH)),
        ),
        "hero.simayi.fankui": (_HookFrame(target=ME, source=0),),
        "hero.guojia.yiji": (_HookFrame(target=ME),),
        # M6 票 02【集智】：使用牌帧的 source 是我、牌面是非延时类锦囊。
        "hero.huangyueying.jizhi": (
            _HookFrame(source=ME, card=state.card(_PROBE_TRICK_CARD)),
        ),
        # M6 票 01【刚烈】：受伤的是我、伤害来源是座位 0（活着、不是自己）。
        "hero.xiahoudun.ganglie": (_HookFrame(target=ME, source=0),),
        "hero.daqiao.liuli": (
            _HookFrame(
                source=0, actor=ME, targets=(ME,), card=state.card(_PROBE_CHANNEL_SLASH)
            ),
        ),
        "hero.luxun.lianying": (_HookFrame(**{MOVE_EVENT_KEY: move_lianying}),),
        "hero.sunshangxiang.xiaoji": (_HookFrame(**{MOVE_EVENT_KEY: move_xiaoji}),),
        "equip.silver_lion": (_HookFrame(**{MOVE_EVENT_KEY: move_silver_lion}),),
        # M6 票 08：界【集智】与标准版同一条条件（使用者是我 + 非延时转化锦囊）；
        # 【清俭】的正面帧 = 一次"处理区 → 我的手牌"的获得（ME 是获得者）；
        # 界【刚烈】与标准版同形（受伤的是我、来源是座位 0）。
        "hero.jie_huangyueying.jizhi": (
            _HookFrame(source=ME, card=state.card(_PROBE_TRICK_CARD)),
        ),
        "hero.jie_xiahoudun.qingjian": (
            _HookFrame(
                **{
                    MOVE_EVENT_KEY: MoveEvent(
                        cards=(),
                        from_zone=ZoneRef(Zone.PROCESSING),
                        to_zone=ZoneRef(Zone.HAND, ME),
                        reason=MoveReason.PUT,
                        owner=ME,
                        after_holder=ME,
                        actor=ME,
                    )
                }
            ),
        ),
        "hero.jie_xiahoudun.ganglie": (_HookFrame(target=ME, source=0),),
        # M6 票 06：觉醒技与【诛害】的持有者自校。
        # - 【潜心】：伤害来源 = 我（归属）∧ 我已受伤（基础局面 ME 3/4）；
        # - 【勤学】：阶段体帧的 source = 我（两条挂接共用同一条条件）；
        # - 【诛害】：当前回合角色是**别人**（source=2）且其本回合造成过伤害。
        "hero.jie_xushu.qianxue": (_HookFrame(source=ME),),
        "hero.jie_lvmeng.qinxue": (
            _HookFrame(source=ME),
            _HookFrame(source=ME),
        ),
        "hero.jie_xushu.zhuhai": (_HookFrame(source=OTHER),),
    }


EXEMPT_OWNER_SELF_CHECK: dict[str, str] = {
    "hero.jie_ganning.fenwei": (
        "M6 票 07：触发条件关于**这张牌**（锦囊 ∧ ≥2 个目标），不关于持有者——"
        "任何角色使用的多目标锦囊都要问界甘宁；没有『我』可自校（与『关于我』的钩子正交）"
    ),
}
"""**豁免表**：技能 id → "为什么它的条件不需要持有者判据"。

姿态照 `tests/test_fuzz.py:OVER_CAP_BY_DESIGN`：例外必须显式列名 + 写明理由，覆盖断言会核
"正表 ∪ 这张表 == 全部带条件的钩子"，故新增一条同型钩子时逼你二选一（写探针 / 登记豁免）。
M6 票 07 起不再为空：【奋威】的条件是"这张牌"的属性，不是"我"的属性（见上表理由）。
"""


def _conditional_hook_skills() -> set[str]:
    """``CATALOG`` 里**带条件**的钩子所属的技能 id（机械发现，不手抄名单）。"""
    return {
        skill_id
        for skill_id, definition in CATALOG.items()
        for hook in definition.hooks
        if hook.condition is not None
    }


def test_the_owner_self_check_exemption_table_holds_only_reviewed_entries() -> None:
    """豁免表**只许登记经过裁定的"不关于我"条件**：每条都必须是真实带条件钩子 + 写明理由。

    票 07 起第一条合法例外是【奋威】（条件关于"这张牌"）。这条挡住"顺手往例外里塞一条"
    而没人看见：增删条目都要同步这条断言。
    """
    assert set(EXEMPT_OWNER_SELF_CHECK) == {"hero.jie_ganning.fenwei"}, (
        "豁免表增删必须同步本条（只许登记经过裁定的『不关于我』条件）"
    )
    for skill_id, reason in EXEMPT_OWNER_SELF_CHECK.items():
        assert reason.strip(), f"{skill_id} 的豁免理由不能为空"
        assert skill_id in CATALOG, f"{skill_id} 不在 CATALOG 里"
        assert any(hook.condition is not None for hook in CATALOG[skill_id].hooks), (
            f"{skill_id} 没有带条件的钩子，不该出现在豁免表"
        )


def test_every_conditional_hook_goes_false_when_the_owner_is_someone_else() -> None:
    """**"关于我"的钩子条件必须自校持有者**（票 13 §要做什么 4 的机械检查）。

    逐条：先证明"我是持有者"时条件为真（正面对照，防"恒假的假绿"），再把
    ``frame.data[MATCH_OWNER_KEY]`` 换成**别人**（座位 2）⇒ 条件必须为假。
    覆盖断言 = 正表 ∪ 豁免表 恰等于全部带条件的钩子：漏写一条同型钩子的探针就红。
    """
    probes = _owner_probes(_probe_state())
    discovered = _conditional_hook_skills()

    assert discovered == set(probes) | set(EXEMPT_OWNER_SELF_CHECK), (
        "有带条件的钩子既不在正表、也不在豁免表里（漏写探针）："
        f"{sorted(discovered - set(probes) - set(EXEMPT_OWNER_SELF_CHECK))}"
    )

    for skill_id, frames in probes.items():
        hooks = [hook for hook in CATALOG[skill_id].hooks if hook.condition is not None]
        assert len(hooks) == len(frames), (
            f"{skill_id} 的带条件钩子 {len(hooks)} 条，探针给了 {len(frames)} 帧"
            "（每条带条件钩子都要有一段正 / 反断言）"
        )
        host = _HookHost(_probe_state_for(skill_id))
        for hook, frame in zip(hooks, frames):
            condition = hook.condition

            frame.data[MATCH_OWNER_KEY] = ME
            assert condition(host, frame) is True, (
                f"{skill_id}：持有者是自己时条件必须成立（正面对照，否则负断言是假绿）"
            )
            frame.data[MATCH_OWNER_KEY] = OTHER
            assert condition(host, frame) is False, (
                f"{skill_id}：持有者换成别人后条件仍成立 ⇒ 触发条件没自校持有者"
            )


# ---------------------------------------------------------------------------
# W6-01：零步技能登记成**模块级** ``SkillFlow``，且仍走旧候选路径
# ---------------------------------------------------------------------------

# ``ZERO_STEP_SKILL_IDS`` 由 ``SKILL_SHAPE`` 派生（定义在下面那张表之后，W6-07 / 评审 J7）：
# 手抄第二份名单只会与形态表漂移，而"零步技能"本来就等于 ``SKILL_SHAPE`` 里那一格。


def test_the_seven_zero_step_skills_are_registered_flows_with_no_steps():
    """14 条零步技能（M6 票 01 加【裸衣】、票 02 加【集智】、票 03 加【克己】/【苦肉】、
    票 06 加界【克己】、票 07 加界【闭月】）：
    ``flow_for`` 非空、``steps == ()``、``preactivated`` 仍 ``False``。

    这条钉的是 W6-01 的**执行路径统一**判据（spec §7）：迁移只把 ``Hook(handler=…)``
    换成 ``Hook(flow=…)``，不翻预发动开关（Q5=A）——界面 / 候选 / 记录流因此一行不改。
    ``flow_for`` 读 :data:`~sgs.skills.preactivation.FLOWS`（由 ``CATALOG`` 的钩子派生），
    故"非 ``None``"同时证明该流水线已被某条 ``Hook`` 登记（不是模块级死常量）。
    """
    for skill_id in ZERO_STEP_SKILL_IDS:
        flow = flow_for(skill_id)
        assert flow is not None, f"{skill_id} 没有登记流水线（W6-01 迁移漏了？）"
        assert flow.steps == (), f"{skill_id} 是零步技能，流水线不该有步骤"
        assert CATALOG[skill_id].preactivated is False, (
            f"{skill_id} 只统一执行路径，不翻预发动（spec Q5=A）"
        )


# ---------------------------------------------------------------------------
# W6-04：68 条技能的**形态**分类（静态表 + 全量覆盖）
#
# 形态是**读法**：`SkillDef` 上没有 shape 字段，也不该有。形态由既有声明读出来
# （`locked` / `hooks` / `modifiers` / `seams` / `flow_for` / `deck_arrange` /
# `rejudge_capable`），这张表把那次阅读机械化钉住：新增 / 删除技能时先缺键 / 多键变红，
# 逼出一次重新分类。**不要**把这张表与 `SKILL_FILES` 混用——后者是 32 条启用技能 →
# 点名它的族级单测文件（票 14），语义不同。
# ---------------------------------------------------------------------------

SKILL_SHAPE: dict[str, str] = {
    # 主动技能 6（出牌阶段主动发动：有 SkillFlow、无钩子 / 接缝）
    "hero.sunquan.zhiheng": "主动技能",
    "hero.huanggai.kurou": "零步技能",
    "hero.liubei.rende": "主动技能",
    "hero.zhouyu.fanjian": "主动技能",
    "hero.huatuo.qingnang": "主动技能",
    "hero.diaochan.lijian": "主动技能",
    "hero.sunshangxiang.jieyin": "主动技能",
    "hero.jie_guanyu.yijue": "主动技能",
    # M6 票 08：界【反间】是两步主动技（手牌 → 目标；提交点里目标二选一）
    "hero.jie_zhouyu.fanjian": "主动技能",
    # M6 票 06 的两条 ☆ 派生技能（觉醒后才存在的出牌阶段主动技）
    "hero.jie_xushu.jianyan": "主动技能",
    "hero.jie_lvmeng.gongxin": "主动技能",
    # 触发技能 6（钩子 / 接缝驱动的非零步流水线；M6 票 01 的【突袭】是阶段体 + 座位步）
    "hero.simayi.fankui": "触发技能",
    "hero.daqiao.liuli": "触发技能",
    "hero.zhangliao.tuxi": "触发技能",
    "hero.huangyueying.jizhi": "零步技能",
    # M6 票 08：界【集智】= 零步（摸一张 + 提交点里的可选弃置）
    "hero.jie_huangyueying.jizhi": "零步技能",
    # M6 票 08：界【清俭】= 触发技能（MOVE_AFTER 钩子 + 两步流水线）
    "hero.jie_xiahoudun.qingjian": "触发技能",
    "equip.green_dragon_blade": "触发技能",
    "equip.axe": "触发技能",
    "equip.qilin_bow": "触发技能",
    # 零步技能 12（SkillFlow(steps=())；执行路径统一，preactivated 不翻）
    "hero.luxun.lianying": "零步技能",
    "hero.sunshangxiang.xiaoji": "零步技能",
    "hero.caocao.jianxiong": "零步技能",
    "hero.guojia.tiandu": "零步技能",
    "hero.lvmeng.keji": "零步技能",
    "hero.zhouyu.yingzi": "零步技能",
    "hero.diaochan.biyue": "零步技能",
    "hero.machao.tieqi": "零步技能",
    "hero.xuchu.luoyi": "零步技能",
    "hero.jie_zhangfei.tishen": "零步技能",
    "hero.jie_lvmeng.keji": "零步技能",
    # 排列技能 1（deck_arrange + Arrange 步）
    "hero.zhugeliang.guanxing": "排列技能",
    # 多轮技能 5（手写帧内游标循环；flow_for is None；M6 票 01 的【刚烈】= 判定 → 选项 → 选牌，
    # 票 02 的【洛神】= 可重复判定循环）
    "hero.guojia.yiji": "多轮技能",
    "hero.xiahoudun.ganglie": "多轮技能",
    # M6 票 08：界【刚烈】是判定 → 红 / 黑分叉的手写多轮（与标准版整句重写）
    "hero.jie_xiahoudun.ganglie": "多轮技能",
    "hero.zhenji.luoshen": "多轮技能",
    # M6 票 09：界【洛神】与标准版共用同一条手写多轮循环（差别只在获得牌的标签）
    "hero.jie_zhenji.luoshen": "多轮技能",
    "hero.jie_machao.tieqi": "多轮技能",
    # M6 票 06：觉醒族的【诛害】是"其他角色的结束阶段"的触发技（目标在帧里给定）
    "hero.jie_xushu.zhuhai": "触发技能",
    # M6 票 06 的两条觉醒技（强制发动 + 一次性转变）
    "hero.jie_xushu.qianxue": "觉醒技",
    "hero.jie_lvmeng.qinxue": "觉醒技",
    # M6 票 07：界甘宁 / 界貂蝉
    "hero.jie_ganning.qixi": "转化技",
    "hero.jie_ganning.fenwei": "多轮技能",
    "hero.jie_diaochan.lijian": "主动技能",
    "hero.jie_diaochan.biyue": "零步技能",
    "equip.ice_sword": "多轮技能",
    "equip.double_sword": "多轮技能",
    # 锁定技 22（locked=True，含带目标池 / 修饰器的锁定技；M5-01a 的 4 件军争装备占位）
    "equip.crossbow": "锁定技",
    "equip.qinggang_sword": "锁定技",
    "equip.renwang_shield": "锁定技",
    "equip.chitu": "锁定技",
    "equip.jueying": "锁定技",
    "equip.dilu": "锁定技",
    "equip.zhuahuang_feidian": "锁定技",
    "equip.dawan": "锁定技",
    "equip.zixing": "锁定技",
    "equip.silver_lion": "锁定技",
    "equip.vine_armor": "锁定技",
    "equip.ancient_scimitar": "锁定技",
    "equip.hualiu": "锁定技",
    "hero.zhugeliang.kongcheng": "锁定技",
    "hero.luxun.qianxun": "锁定技",
    "hero.lvbu.wushuang": "锁定技",
    "hero.sunquan.jiuyuan": "锁定技",
    "hero.machao.mashu": "锁定技",
    "hero.jie_machao.mashu": "锁定技",
    "hero.zhangfei.paoxiao": "锁定技",
    "hero.jie_zhangfei.paoxiao": "锁定技",
    "hero.huangyueying.qicai": "锁定技",
    # M6 票 08：界【英姿】/ 界【奇才】都是锁定技（牌面写"锁定技"）
    "hero.jie_zhouyu.yingzi": "锁定技",
    "hero.jie_huangyueying.qicai": "锁定技",
    # 转化技 7（virtual_type_ids 的 CANDIDATES）
    "hero.guanyu.wusheng": "转化技",
    "hero.zhaoyun.longdan": "转化技",
    "hero.daqiao.guose": "转化技",
    "hero.huatuo.jijiu": "转化技",
    "hero.zhenji.qingguo": "转化技",
    "hero.ganning.qixi": "转化技",
    # M6 票 09：界【倾国】与标准版逐字相同（同一条 ConversionSkill 声明）
    "hero.jie_zhenji.qingguo": "转化技",
    "hero.jie_guanyu.wusheng": "转化技",
    "equip.serpent_spear": "转化技",
    "equip.vermilion_feather_fan": "转化技",
    # 能力位 1（rejudge_capable，没有对持有者的询问）
    "hero.simayi.guicai": "能力位",
    # 候选生成器 1（TARGET_POOL + TARGET_COUNT，从不发「是否发动」）
    "equip.halberd": "候选生成器",
    # 候选提供者 3（CANDIDATES + 势力求助窗口 / 引擎回退）
    "hero.caocao.hujia": "候选提供者",
    "hero.liubei.jijiang": "候选提供者",
    "equip.eight_diagrams": "候选提供者",
}
"""74 条技能 → 形态（W6-04，spec §4.4）：**读法**，不是代码字段。

`SkillDef` 上没有 shape 字段，也不该有：形态是从既有声明读出来的归类（`locked` /
`skill_type` / `hooks` / `modifiers` / `seams` / `flow_for` / `deck_arrange` /
`rejudge_capable`），不是第二份真源。这张表把那次阅读**机械化钉住**：新增 / 删除技能时先缺键 / 多键变红，逼出一次
重新分类；改表 = 重新裁定形态。

**不要**把这张表与 `SKILL_FILES` 混用：后者是 32 条启用技能 → 点名它的族级单测文件
（票 14 的机械覆盖表），语义完全不同。
"""

SHAPE_COUNTS: dict[str, int] = {
    "主动技能": 11,
    "触发技能": 8,
    "零步技能": 15,
    "排列技能": 1,
    "多轮技能": 9,
    "锁定技": 24,
    "转化技": 11,
    "能力位": 1,
    "候选生成器": 1,
    "候选提供者": 3,
    "觉醒技": 2,
}
"""每格的条数（spec §4.4；M6 票 09 后
11 + 8 + 15 + 1 + 9 + 24 + 11 + 1 + 1 + 3 + 2 = 86）。

票 03 的三条：**苦肉**是零步主动技（裸发动，无步骤 ⇒ 机械形态落在「零步技能」格）；
**克己**是阶段体零步技（同格）；**奇袭**是单源转化（转化技格）。票 04 的四条：**义绝**
是三步主动技（主动技能格）；**武圣**（界）是转化技；**马术**（界）是锁定技；**铁骑**（界）
是逐目标手写的多段钩子（多轮技能格）。票 05 的两条：**替身**是阶段体零步技（零步技能格），
**咆哮**（界）是锁定技（锁定技格）。票 06 的六条：**荐言** / **攻心**是两个 ☆ 主动技
（主动技能格）；**诛害**是结束阶段触发技（触发技能格）；**克己**（界）是阶段体零步技
（零步技能格）；**潜心** / **勤学**按 `skill_type = AWAKENING` 单列「觉醒技」格——
它们不是锁定技（不并进 ``locked``），"强制发动"由派发环按类型判。票 07 的四条：
**奇袭**（界）是转化技（与标准甘宁共用一行声明）；**奋威**是手写的"逐目标选无效"循环
（多轮技能格）；**离间**（界）是三步主动技（主动技能格）；**闭月**（界）是零步阶段体
（零步技能格）。票 08 的六条：**反间**（界）是两步主动技（主动技能格）；**集智**（界）
是零步（零步技能格）；**清俭**是 MOVE_AFTER 钩子 + 两步流水线（触发技能格）；**刚烈**
（界）是手写多轮（多轮技能格）；**英姿**（界）/ **奇才**（界）是锁定技。票 09 的两条：
**洛神**（界）与标准版共用同一条手写多轮循环（多轮技能格）；**倾国**（界）与标准版共用
同一条转化声明（转化技格）。锁定技牌面写
"锁定技"，故即使英姿带一条零步阶段体流水线也按**类型**归「锁定技」格（零步格因此
**排除**锁定技，见 :func:`test_every_shape_has_the_documented_count_and_members`）。"""

STEP_LANGUAGE_SHAPES: frozenset[str] = frozenset(
    {"主动技能", "触发技能", "零步技能", "排列技能", "觉醒技"}
)
"""在步骤语言内的五个形态（37 条 = 11 + 8 + 15 + 1 + 2）。觉醒技也走 ``SkillFlow``
（【潜心】零步、【勤学】一步选项），故"内 == 有 ``flow_for``"仍成立。"""

IN_STEP_LANGUAGE: frozenset[str] = frozenset(
    skill_id
    for skill_id, shape in SKILL_SHAPE.items()
    if shape in STEP_LANGUAGE_SHAPES
)
"""在步骤语言内（37）：**派生**自 :data:`SKILL_SHAPE` 的五个步骤语言形态。

W6-07 / 评审 J7：这里原先手抄了 19 个 id，与 `SKILL_SHAPE` 是同一份事实的两份拷贝——
形态表一改，第二份名单就悄悄过期。现在它只是那张表的一个投影；真正的判据留在
:func:`test_the_step_language_boundary_is_exactly_the_flow_backed_skills`（"内 == 有
`flow_for`"），不是这行的规模。
"""

OUTSIDE_STEP_LANGUAGE: frozenset[str] = frozenset(SKILL_SHAPE) - IN_STEP_LANGUAGE
"""不在步骤语言内（49）：多轮 9 + 锁定 24 + 转化 11 + 能力位 1 + 候选生成器 1 + 候选提供者 3。

同样**派生**（:data:`SKILL_SHAPE` 减 :data:`IN_STEP_LANGUAGE`）；44 这个数由
`SHAPE_COUNTS` 的其余六格加出来，测试里另有一条规模断言。
"""

ZERO_STEP_SKILL_IDS: tuple[str, ...] = tuple(
    skill_id for skill_id, shape in SKILL_SHAPE.items() if shape == "零步技能"
)
"""W6-01 的零步技能（spec §4.1）：连营 / 枭姬 / 奸雄 / 天妒 / 英姿 / 闭月 / 铁骑，
M6 票 01 加【裸衣】、票 02 加【集智】、票 03 加【克己】/【苦肉】、票 06 加界【克己】、
票 07 加界【闭月】（共 14 条；觉醒技【潜心】虽有零步流水线，按类型单列「觉醒技」格，
不在此列）。

W6-07 / 评审 J7：**派生**自 :data:`SKILL_SHAPE` 的「零步技能」那一格（原先手抄 7 个 id）。"""


def _shape_members(shape: str) -> frozenset[str]:
    """某个形态在静态表里的技能集合。"""
    return frozenset(
        skill_id for skill_id, assigned in SKILL_SHAPE.items() if assigned == shape
    )


def _flow_steps(skill_id: str):
    """``flow_for`` 的步骤元组；没有流水线 ⇒ ``None``（零步技能是 ``()``，不是 ``None``）。"""
    flow = flow_for(skill_id)
    return None if flow is None else flow.steps


def test_the_skill_shape_table_covers_the_catalog_exactly_once() -> None:
    """86 条恰好一形态：键 = ``CATALOG`` 的键、值 = 11 个形态闭集之一。"""
    assert set(SKILL_SHAPE) == set(CATALOG)
    assert len(SKILL_SHAPE) == len(CATALOG) == 86
    assert set(SKILL_SHAPE.values()) == set(SHAPE_COUNTS)
    assert len(SHAPE_COUNTS) == 11
    assert all(SKILL_SHAPE.values()), "表里没有空形态"


def test_every_shape_has_the_documented_count_and_members() -> None:
    """逐格核条数，并用既有声明复核能机械读出的形态（读法不是手抄愿望）。"""
    actual_counts = {shape: len(_shape_members(shape)) for shape in SHAPE_COUNTS}
    assert actual_counts == SHAPE_COUNTS

    def skills(predicate) -> frozenset[str]:
        return frozenset(
            skill_id
            for skill_id, definition in CATALOG.items()
            if predicate(definition)
        )

    awakening = skills(lambda definition: definition.skill_type is SkillType.AWAKENING)
    # 觉醒技按**类型**单列一格；两条觉醒技都有流水线（零步 / 一步），故从"零步 / 触发"
    # 两个机械谓词里排除，避免一条技能落进两格。
    active = frozenset(
        skill_id
        for skill_id in CATALOG
        if (_steps := _flow_steps(skill_id)) is not None
        and _steps != ()
        and not CATALOG[skill_id].hooks
        and not CATALOG[skill_id].seams
    )
    triggered = frozenset(
        skill_id
        for skill_id in CATALOG
        if (_steps := _flow_steps(skill_id)) is not None
        and _steps != ()
        and (CATALOG[skill_id].hooks or CATALOG[skill_id].seams)
        and CATALOG[skill_id].deck_arrange is None
        and skill_id not in awakening
    )
    zero_step = frozenset(
        skill_id
        for skill_id in CATALOG
        # 锁定技按**类型**单列一格（界【英姿】带一条零步阶段体流水线也是锁定技），
        # 故零步格排除锁定技——与"觉醒技单列"同一条互斥纪律。
        if _flow_steps(skill_id) == ()
        and skill_id not in awakening
        and not CATALOG[skill_id].locked
    )
    arranged = skills(lambda definition: definition.deck_arrange is not None)
    locked = skills(lambda definition: definition.locked)
    conversion = skills(lambda definition: bool(definition.virtual_type_ids))
    ability = skills(
        lambda definition: definition.rejudge_capable
        and not definition.hooks
        and not definition.modifiers
        and not definition.seams
    )
    generator = skills(
        lambda definition: {modifier.point for modifier in definition.modifiers}
        == {ModifierPoint.TARGET_POOL, ModifierPoint.TARGET_COUNT}
    )
    providers = frozenset(PROVIDERS) | {"equip.eight_diagrams"}

    assert _shape_members("主动技能") == active
    assert _shape_members("触发技能") == triggered
    assert _shape_members("零步技能") == zero_step
    assert _shape_members("排列技能") == arranged
    assert _shape_members("锁定技") == locked
    assert _shape_members("觉醒技") == awakening
    assert _shape_members("转化技") == conversion
    assert _shape_members("能力位") == ability
    assert _shape_members("候选生成器") == generator
    assert _shape_members("候选提供者") == providers
    assert set(PROVIDERS) == {"hero.caocao.hujia", "hero.liubei.jijiang"}

    multi_round = _shape_members("多轮技能")
    assert len(multi_round) == 9
    assert all(_flow_steps(skill_id) is None for skill_id in multi_round)
    assert all(
        any(hook.handler is not None for hook in CATALOG[skill_id].hooks)
        for skill_id in multi_round
    ), "七条多轮技能都保持手写钩子（flow_for is None）"


def test_the_halberd_declares_target_pool_and_target_count_only() -> None:
    """W7-01：【方天画戟】的声明 = ``TARGET_POOL``（空操作）+ ``TARGET_COUNT``（+2）。

    不再有 ``CANDIDATES`` 回调——枚举型追加换成"查询期修正最大目标数"，否则两套会生成
    重复候选（W7 spec §0 第 3 条 / S1）。这条把声明形状本身钉住，形态分类由
    :func:`test_every_shape_has_the_documented_count_and_members` 的谓词复核。

    ``TARGET_POOL`` 是**有意的空操作**（W7 spec §3.2 的"合法性同标准"明写落点），
    W7-03 / D6 复核后保留；它不是靠本测试自证——行为面由
    :func:`test_halberd_target_pool_is_unchanged_by_the_modifier`（池与是否装备方天无关）
    与 :func:`test_halberd_extra_targets_also_read_the_filtered_pool` 两条真行为断言钉住，
    本测试只钉**声明形状**（G2 的形态表同样要求它同时具备这两个点）。
    """
    definition = CATALOG["equip.halberd"]

    assert {modifier.point for modifier in definition.modifiers} == {
        ModifierPoint.TARGET_POOL,
        ModifierPoint.TARGET_COUNT,
    }
    assert not definition.hooks and not definition.seams and not definition.locked


def test_the_step_language_boundary_is_exactly_the_flow_backed_skills() -> None:
    """37 在内 / 49 在外：两侧集合显式，且与「有没有 ``SkillFlow``」逐条同源。"""
    inside = frozenset(
        skill_id
        for skill_id, shape in SKILL_SHAPE.items()
        if shape in STEP_LANGUAGE_SHAPES
    )
    outside = frozenset(SKILL_SHAPE) - inside

    # 两侧集合是 ``SKILL_SHAPE`` 的投影（W6-07 / 评审 J7：不再手抄第二份名单）……
    assert inside == IN_STEP_LANGUAGE
    assert outside == OUTSIDE_STEP_LANGUAGE
    # ……真正的判据是它们与**执行路径**逐条同源（形态表若把一条没有 flow 的技能划进来，这里红）。
    assert len(inside) == 37
    assert len(outside) == 49
    assert inside.isdisjoint(outside)
    assert inside | outside == set(CATALOG)
    # 与**执行路径**逐条同源（形态表若把一条没有 flow 的技能划进来，这里红）。判据要扣掉
    # **锁定技**：M6 票 08 的界【英姿】带一条零步阶段体流水线，但按技能类型归「锁定技」格
    # （与觉醒技单列同一姿态）——"有没有 flow"不再是划分步骤语言内外的充分条件。
    locked = {skill_id for skill_id in CATALOG if CATALOG[skill_id].locked}
    flow_backed = {
        skill_id for skill_id in CATALOG if flow_for(skill_id) is not None
    }
    assert flow_backed - locked == inside
    assert ({skill_id for skill_id in CATALOG if flow_for(skill_id) is None} | (flow_backed & locked)) == outside
    assert ZERO_STEP_SKILL_IDS and set(ZERO_STEP_SKILL_IDS) <= inside
