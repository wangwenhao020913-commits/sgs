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
from typing import Any

from sgs.data import (
    Gender,
    Kingdom,
    enabled_hero_ids,
    hero_skill_id,
    load_content_pack,
    load_heroes,
)
from sgs.engine import DRAW_COUNT, Engine
from sgs.skills import CATALOG, flow_for
from sgs.skills.catalog import MATCH_OWNER_KEY
from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent
from sgs.state import EndPhase, GameState, MoveReason, Pass, Zone, ZoneRef
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
"""合成局面用到的牌 id（牌堆档位固定，模块级算一次）。"""


class _HookFrame:
    """只带 ``data`` 的最小帧：``Hook.condition`` 的输入面就是 ``frame.data``（+ ``host.state``）。"""

    def __init__(self, **data: Any) -> None:
        self.data = data


class _HookHost:
    """只带 ``state`` 的最小宿主：条件里唯一用到的宿主面。"""

    def __init__(self, state: GameState) -> None:
        self.state = state


def _probe_state() -> GameState:
    """合成局面：座位 0 手里有牌（【反馈】的**伤害来源**）、座位 1 / 2 空手（【连营】的"已失去"）、
    处理区有一张【杀】（【奸雄】的渠道牌）。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(_PROBE_SOURCE_CARD,)), SeatSpec(), SeatSpec()],
        processing=(_PROBE_PROCESSING_SLASH,),
    )


def _owner_probes(state: GameState) -> dict[str, _HookFrame]:
    """技能 id → **"持有者是自己"**的合成帧（条件必须为真的那一侧）。

    帧里的 ``target`` / ``targets`` / ``source`` / 移动载荷都指向**别人**（座位 0 / 座位 1），
    只有 ``MATCH_OWNER_KEY`` 由检查本身按 ME / OTHER 改写。逐条的"我"分别是：
    受伤者（【奸雄】【遗计】）、受伤者 + 拿来源牌的持有者（【反馈】）、
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
    return {
        "hero.caocao.jianxiong": _HookFrame(
            target=ME, source=0, card=state.card(_PROBE_PROCESSING_SLASH)
        ),
        "hero.simayi.fankui": _HookFrame(target=ME, source=0),
        "hero.guojia.yiji": _HookFrame(target=ME),
        "hero.daqiao.liuli": _HookFrame(
            source=0, actor=ME, targets=(ME,), card=state.card(_PROBE_CHANNEL_SLASH)
        ),
        "hero.luxun.lianying": _HookFrame(**{MOVE_EVENT_KEY: move_lianying}),
        "hero.sunshangxiang.xiaoji": _HookFrame(**{MOVE_EVENT_KEY: move_xiaoji}),
    }


EXEMPT_OWNER_SELF_CHECK: dict[str, str] = {}
"""**豁免表**：技能 id → "为什么它的条件不需要持有者判据"；**现在是空集**。

姿态照 `tests/test_fuzz.py:OVER_CAP_BY_DESIGN`：例外必须显式列名 + 写明理由，覆盖断言会核
"正表 ∪ 这张表 == 全部带条件的钩子"，故新增一条同型钩子时逼你二选一（写探针 / 登记豁免）。
"""


def _conditional_hook_skills() -> set[str]:
    """``CATALOG`` 里**带条件**的钩子所属的技能 id（机械发现，不手抄名单）。"""
    return {
        skill_id
        for skill_id, definition in CATALOG.items()
        for hook in definition.hooks
        if hook.condition is not None
    }


def test_the_owner_self_check_exemption_table_is_empty() -> None:
    """豁免表**现在是空集**：这条挡住"顺手往例外里塞一条"而没人看见。"""
    assert EXEMPT_OWNER_SELF_CHECK == {}, (
        "六条带条件的钩子全是『关于我』的 ⇒ 一条例外都不该有；要加必须写明理由并同步本条"
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

    state = _probe_state()
    host = _HookHost(state)
    for skill_id, frame in probes.items():
        hooks = [hook for hook in CATALOG[skill_id].hooks if hook.condition is not None]
        assert len(hooks) == 1, f"{skill_id} 的带条件钩子不止一条，探针只覆盖第一条"
        condition = hooks[0].condition

        frame.data[MATCH_OWNER_KEY] = ME
        assert condition(host, frame) is True, (
            f"{skill_id}：持有者是自己时条件必须成立（正面对照，否则负断言是假绿）"
        )
        frame.data[MATCH_OWNER_KEY] = OTHER
        assert condition(host, frame) is False, (
            f"{skill_id}：持有者换成别人后条件仍成立 ⇒ 触发条件没自校持有者"
        )


# ---------------------------------------------------------------------------
# W6-01：7 条零步技能登记成**模块级** ``SkillFlow``，且仍走旧候选路径
# ---------------------------------------------------------------------------

ZERO_STEP_SKILL_IDS = (
    "hero.luxun.lianying",
    "hero.sunshangxiang.xiaoji",
    "hero.caocao.jianxiong",
    "hero.guojia.tiandu",
    "hero.zhouyu.yingzi",
    "hero.diaochan.biyue",
    "hero.machao.tieqi",
)
"""W6-01 的 7 条零步技能（spec §4.1）：连营 / 枭姬 / 奸雄 / 天妒 / 英姿 / 闭月 / 铁骑。"""


def test_the_seven_zero_step_skills_are_registered_flows_with_no_steps():
    """7 条零步技能：``flow_for`` 非空、``steps == ()``、``preactivated`` 仍 ``False``。

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
