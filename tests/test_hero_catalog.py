"""武将总表：16 名 × 29 条技能的数据断言与登记（票 12）。

本文件只装**跨技能**的登记事实（逐技能的构造局面单测按族住各自文件）：

- ``CATALOG`` 的规模与分区：装备 17 + 武将 29 = **46 条**，且武将那半恰是数据文件里
  16 名启用武将的技能清单（``heroes.standard.json`` 是名单的唯一真源）；
- **同一 owner 同一时机只有一条技能**（票 12 §要做什么 3）：这是 R5-Q1「同一时点多技能
  自选顺序不建」的落地依据——将来加武将时这条断言先变红，逼出一次重新裁定；
- 16 名的 ``gender`` / ``kingdom`` / ``hp_max`` 与规则文档第九章逐项一致（表驱动，
  期望值是**手抄的独立真源**，不是从数据文件回读）；
- 启用门控：未启用的 9 名不产出实例、不参与发放；启用池恰是那 16 名。
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sgs.data import (
    Gender,
    Kingdom,
    enabled_hero_ids,
    hero_skill_id,
    load_content_pack,
    load_heroes,
)
from sgs.engine import DRAW_COUNT, Engine
from sgs.skills import CATALOG
from sgs.state import EndPhase, Pass
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

# 16 名启用武将（票 12 §落地表的 16 行；名单与 `spec.md` §名单一致）。
ENABLED = (
    "caocao",
    "simayi",
    "guojia",
    "liubei",
    "guanyu",
    "zhaoyun",
    "machao",
    "zhugeliang",
    "sunquan",
    "zhouyu",
    "luxun",
    "daqiao",
    "sunshangxiang",
    "huatuo",
    "lvbu",
    "diaochan",
)

# 落选 9 名（数据文件里结构存在、引用不查、不参与发放）。
DISABLED = (
    "xiahoudun",
    "zhangliao",
    "xuchu",
    "zhangfei",
    "huangyueying",
    "ganning",
    "lvmeng",
    "huanggai",
    "zhenji",
)

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
    # 落选 9 名同样逐项核对（结构存在即为它们负责）。
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
# ① CATALOG 的一分为二（装备 17 + 武将 29 = 46）
# ---------------------------------------------------------------------------


def test_the_catalog_holds_seventeen_equipment_and_twenty_nine_hero_skills():
    """验收 1：``CATALOG`` = 46 条（17 装备 + 29 武将），且两半由 ``carrier`` 分区。"""
    equipment = [d for d in CATALOG.values() if d.carrier.value == "EQUIPMENT"]
    heroes = [d for d in CATALOG.values() if d.carrier.value == "HERO"]

    assert len(CATALOG) == 46
    assert len(equipment) == 17
    assert len(heroes) == 29


def test_the_hero_half_of_the_catalog_is_exactly_the_enabled_hero_sheet():
    """武将那半 = 16 名启用武将的技能清单（数据文件是名单的唯一真源，代码不另抄）。"""
    expected = {
        f"hero.{hero.id}.{skill.id}"
        for hero in HEROES.heroes
        if hero.id in ENABLED
        for skill in hero.skills
    }
    actual = {d.skill_id for d in CATALOG.values() if d.carrier.value == "HERO"}

    assert actual == expected
    assert len(expected) == 29


def test_every_hero_skill_id_is_fully_qualified():
    for definition in CATALOG.values():
        if definition.carrier.value != "HERO":
            continue
        parts = definition.skill_id.split(".")
        assert parts[0] == "hero"
        assert parts[1] in ENABLED, "技能 id 的武将在启用名单里"
        assert len(parts) == 3


# ---------------------------------------------------------------------------
# ② 同一 owner 同一时机只有一条技能（R5-Q1 的落地依据）
# ---------------------------------------------------------------------------


def test_the_sixteen_enabled_heroes_have_no_two_skills_on_the_same_timing():
    """验收 2：16 名里不存在同一 owner 同一时机的两条技能。

    判据 = 该武将的每条技能定义的 ``hooks`` 的时机集合两两不交；主公技与普通技同样
    受此约束（它们同住一张武将牌）。将来加武将时这条断言先变红。
    """
    duplicated: dict[str, list[str]] = defaultdict(list)
    for hero in HEROES.heroes:
        if hero.id not in ENABLED:
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
# ③ 16 名的 gender / kingdom / hp_max 与第九章一致
# ---------------------------------------------------------------------------


def test_every_hero_matches_the_rule_book_stats():
    """验收 4：表驱动逐名核对（期望值是手抄的独立真源，不从数据文件回读）。"""
    assert set(_HERO_STATS) == {hero.id for hero in HEROES.heroes}
    for hero in HEROES.heroes:
        kingdom, gender, hp_max = _HERO_STATS[hero.id]
        assert (hero.kingdom, hero.gender, hero.hp_max) == (kingdom, gender, hp_max), hero.id


# ---------------------------------------------------------------------------
# ④ 启用门控：9 名不产出实例、不参与发放
# ---------------------------------------------------------------------------


def test_the_enabled_pool_is_exactly_the_sixteen_and_the_rest_stay_dark():
    """验收 5：启用池 = 16 名；另外 9 名结构存在但不启用。"""
    ids = enabled_hero_ids(HEROES)

    assert set(ids) == set(ENABLED)
    assert len(ids) == 16
    assert all(HEROES.by_id(hero_id).enabled for hero_id in ENABLED)
    assert all(not HEROES.by_id(hero_id).enabled for hero_id in DISABLED)


def test_a_disabled_hero_never_produces_an_instance():
    """未启用的武将连牌都不发；构造局面里显式写死也不产出技能实例。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhenji"), SeatSpec(hero="xiahoudun")],
    )

    from sgs.skills import resolve_instances

    assert not [
        instance
        for instance in resolve_instances(state)
        if instance.owner in (0, 1)
    ], "落选武将的技能没有登记 ⇒ 一条实例都不产出"


def test_the_enabled_pool_feeds_a_real_deal_without_the_hero_substream():
    """``hero_pool=()`` = 数据文件的启用池 ⇒ 真发牌取到 16 名里的两名。"""
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


SKILL_FILES: dict[str, str] = {
    # 魏
    "hero.caocao.jianxiong": "test_hero_damage_after.py",
    "hero.caocao.hujia": "test_hero_lord.py",
    "hero.simayi.fankui": "test_hero_damage_after.py",
    "hero.simayi.guicai": "test_hero_rejudge.py",
    "hero.guojia.tiandu": "test_hero_rejudge.py",
    "hero.guojia.yiji": "test_hero_damage_after.py",
    # 蜀
    "hero.liubei.rende": "test_hero_active.py",
    "hero.liubei.jijiang": "test_hero_lord.py",
    "hero.guanyu.wusheng": "test_hero_transform.py",
    "hero.zhaoyun.longdan": "test_hero_transform.py",
    "hero.machao.mashu": "test_hero_target_change.py",
    "hero.machao.tieqi": "test_hero_response_window.py",
    "hero.zhugeliang.guanxing": "test_hero_arrange.py",
    "hero.zhugeliang.kongcheng": "test_hero_target_pool.py",
    # 吴
    "hero.sunquan.zhiheng": "test_hero_active.py",
    "hero.sunquan.jiuyuan": "test_hero_lord.py",
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
}
"""29 条启用技能 → **点名它的族级单测文件**（票 14 §1 的机械覆盖表）。

一个显式登记，而不是从测试源码里猜：新增一条技能时这张表先缺键变红，逼出"给它写一条
断言"。短 id 在该文件里确实出现是第二道闸（防"登记了但文件里其实没提"）。"""


def test_every_enabled_hero_skill_is_named_by_a_family_test_file():
    """**每条启用技能都至少被一条族级单测点名**（票 14 §1 / 票 12 §要做什么 3 的机械补全）。"""
    skills = {
        hero_skill_id(hero.id, skill.id)
        for hero in HEROES.heroes
        if hero.enabled
        for skill in hero.skills
    }

    assert set(SKILL_FILES) == skills, "覆盖表必须恰等于 16 名的 29 条技能"

    tests_dir = Path(__file__).resolve().parent
    unnamed = sorted(
        skill_id
        for skill_id, filename in SKILL_FILES.items()
        if skill_id.rsplit(".", 1)[1]
        not in (tests_dir / filename).read_text(encoding="utf-8")
    )

    assert not unnamed, "这些技能登记了测试文件、但短 id 没出现在文件里：" + "；".join(unnamed)
