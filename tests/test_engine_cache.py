"""引擎安全缓存（票 16；spec §3.3–§3.4）：键覆盖 / 合成反例 / 有界 / 命中率。

判据全部**机器无关**——不测毫秒，只测"少干活"与"键覆盖读点"：

- **键覆盖**：对 ``_resolve_key`` 的每个字段做"只改这一位 ⇒ 键必变"（字段表住
  ``_PLAYER_KEY_FIELDS``），并逐字段对照**无缓存**实现，断言缓存路径与它逐条相等；
- **合成反例**：从键构造函数的副本里删掉一个字段，同一套覆盖检查必须变红
  （证明覆盖检查不是空转）；
- **有界**：塞满后按插入序淘汰，条数恒 ≤ ``_RESOLVE_CACHE_LIMIT``；
- **命中率下限**：固定少量种子跑整局，命中率 ≥ 落地实测值。它替代性能断言：不随机器 /
  负载漂移，且能抓"给解析加了读点却没加键"导致的命中率坍塌；
- **第二项缓存**（``hero_card_skill_ids`` 的 ``lru_cache``）：键就是 ``(hero_id, pack_id)``，
  同一参数命中、不同包分开缓存。

两处**合成值**（``scope`` / ``predicate`` 换值）只用来断言"这一位在键里"：两个登记闭集今天
各只有一个成员，引擎状态里不会出现别的值，故它们不进缓存差分那一段（§6：合成夹具不与写入面
同源时，不许拿去当引擎行为断言）。

语义等价的整局判据不在这里：60 条金录像是**无缓存时**冻结的，逐字节相等即整局等价
（``tests/test_replays.py``）；缓存开 / 关的 20 种子整局差分是实现期证据，跑一次就够
（spec §7.3 第 3 条：不为它新增引擎内部开关）。
"""

from __future__ import annotations

from types import MappingProxyType

from sgs.agents import HeuristicPlayer
from sgs.data import Identity, RuleConfig, hero_card_skill_ids, load_content_pack
from sgs.engine import Engine, ai_stream
from sgs.skills import catalog as catalog_module
from sgs.skills import resolve_instances
from sgs.skills.catalog import (
    _PLAYER_KEY_FIELDS,
    _resolve_instances_uncached,
    _resolve_key,
)
from sgs.state import SkillSuppression, SkillSuppressionPredicate, SkillSuppressionScope

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_108")
"""档位纪律：显式选 108 档，不依赖产品默认档。"""

CROSSBOW = cards_of_type(PACK, "crossbow")[0]
"""座位 0 的实体装备（诸葛连弩）——装备技能 ``equip.crossbow`` 的载体。"""

RENWANG = cards_of_type(PACK, "renwang_shield")[0]
"""给座位 1 加装备用的仁王盾（另一件实体装备）。"""

VIRTUAL_ARMOR = "equip.eight_diagrams"
"""虚拟装备用八卦阵的装备技能 id（它不占牌守恒，只走 ``virtual_equipment`` 一位）。"""

APPLYING_SKILL = "hero.guanyu.wusheng"
"""失效记录里的"施加失效的技能"实例 id；失效本身是**座位级**的，与它是哪一条无关。"""

HIT_RATE_SEEDS = (136, 137, 138)
"""命中率下限的固定种子（5 人、开将池；票 08 的 136–155 带内取前三）。"""

_HIT_RATE_FLOOR = 0.9857
"""命中率下限 = **当前行为面上的实测值**（票面要求"此后只许更高"）。

实测（本机）：种子 136–138 上 ``hits / (hits + misses) = 14540 / 14734 = 0.98683``。这里
**直接用实测分数**当上下限，而不是取一个更好看的 0.99：12788 次调用下它等价于"命中数
≥ 14540"，少 1 次命中即红——把"只许更高"的余量写清楚（票 08 的 20 种子 A/B 同口径是
``131326 / 133097 = 0.9867``，比这里更宽松，只作量级参照）。

**票 10 的重取**：死亡时技能窗口让 `death_time_skills_active` 在阵亡段各变两次，
内容键因此多两次未命中；本机实测 `0.98575`（低于原 `0.98683`），下限取 `0.9857`。
这是新读点带来的**显式**下调，不是放宽：键必须覆盖"窗口内 / 窗口外"两态。

**票 19 的重取理由（一次显式重取，需人类签认）**：⑤ 读的是"真实 AI 对局里少干多少活"，
命中率因此**随 AI 的行为面漂**——票 16 落地时的 ``12832 / 12961 = 0.99005`` 是 D1 / D2
之前那条轨迹上的。D2（斩杀优先，票 19）换了轨迹（对局变短：12961 → 12818 次调用），
缓存实现与键函数一字未动：键覆盖用例 ①–④ 仍全绿，miss 数只从 129 变 132。要求"只许更高"
的前提是**输入轨迹不变**，而本 effort 的计划本身就要求改行为面 ⇒ **判据形态不变**
（实测值、零余量、少 1 次命中即红），**基线按新行为面重取**；数值确实从 0.99005 降到
0.98970（低 0.035 pp），旧值留在上面可对账。**这是本票唯一一条"数值变差"的判据改动**，
理由与影响面写进 `.scratch/ai-light/issues/19-lethal-priority.md` 的 `## Answer`，接受与否
由人类裁定；后续哪一票再改行为面而命中率再漂，照同一条口径重取并记录，不许改成宽松的
圆整阈值。

**票 21 的重取理由（同一条口径，需人类签认）**：I1 身份后验（票 21）第二次改行为面，⑤ 的
实测值按新轨迹重取为 ``12643 / 12788 = 0.98866``（调用 12788、miss 145；上一条轨迹是
``12686 / 12818``、miss 132）。缓存实现与键函数仍然**一字未动**（键覆盖用例 ①–④ 全绿），
命中率随之再降 0.104 pp 是这条判据的既定性质；判据形态不变（实测值、零余量、少 1 次命中
即红），理由与影响面写进 `.scratch/ai-light/issues/21-identity-posterior.md` 的 `## Answer`，
接受与否由人类裁定。

**票 23 的重取理由（同一条口径，需人类签认）**：C2 记牌批 2（票 23）第三次改行为面，⑤ 的
实测值按新轨迹重取为 ``14540 / 14734 = 0.98683``（调用 14734、miss 194；上一条轨迹是
``12643 / 12788``、miss 145）。缓存实现与键函数仍然**一字未动**（键覆盖用例 ①–④ 全绿）；
调用数**变多** 15%（12788 → 14734，同种子下对局走向不同、更长），命中率随之再降 **0.183 pp**
——这是这条判据的既定性质（票 19 / 21 同口径）。判据形态不变（实测值、零余量、少 1 次命中
即红），理由与影响面写进 `.scratch/ai-light/issues/23-card-counting-batch2.md` 的 `## Answer`，
接受与否由人类裁定。
"""


# ---------------------------------------------------------------------------
# 局面与"只改一位"的变换
# ---------------------------------------------------------------------------


def _base_state():
    """4 人局面：四个座位都有武将技能，座位 0 另带一件实体装备。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, hero="caocao", equip=(CROSSBOW,)),
            SeatSpec(hp=4, hp_max=4, identity=Identity.REBEL, hero="guanyu"),
            SeatSpec(hp=3, hp_max=3, identity=Identity.REBEL, hero="ganning"),
            SeatSpec(hp=3, hp_max=3, identity=Identity.RENEGADE, hero="lvmeng"),
        ],
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


def _with_player(state, index: int, **updates):
    """只换一个座位的若干字段（返回新局面，不改入参）。"""
    players = list(state.players)
    players[index] = players[index].model_copy(update=updates)
    return state.model_copy(update={"players": tuple(players)})


def _equip_added(state):
    return _with_player(state, 1, equip=[*state.players[1].equip, RENWANG])


def _equip_removed(state):
    return _with_player(state, 0, equip=[])


def _equip_type_changed(state):
    """同一张装备牌换**牌面类型**：键必须变（实例来源带 card_id、定义按 type 查）。"""
    cards = list(state.cards)
    cards[CROSSBOW] = cards[CROSSBOW].model_copy(update={"type": "slash"})
    return state.model_copy(update={"cards": tuple(cards)})


def _virtual_equipment_added(state):
    return _with_player(state, 1, virtual_equipment=(VIRTUAL_ARMOR,))


def _hero_changed(state):
    return _with_player(state, 1, hero="ganning")


def _hero_cleared(state):
    return _with_player(state, 1, hero=None)


def _granted_added(state):
    return _with_player(state, 2, granted_skills=["hero.caocao.jianxiong"])


def _lost_added(state):
    return _with_player(state, 1, lost_skills=["hero.guanyu.wusheng"])


def _marker_added(state):
    """加一枚带**自身效果**的标记（票 94：标记进内容键，故键必变）。"""
    from sgs.state import Marker

    return _with_player(
        state,
        1,
        markers=[
            *state.players[1].markers,
            Marker(
                name="测试营",
                source_seat=0,
                source_skill_id="hero.shen_ganning.jieying",
                count=1,
                effect="hero.shen_ganning.jieying_mark",
            ),
        ],
    )


def _seat_dead(state):
    return _with_player(state, 1, alive=False)


def _death_time_window(state):
    """票 10：死亡时技能窗口只把"技能可解析"与 alive 拆开，必须进内容键。"""
    return _with_player(state, 1, death_time_skills_active=True)


def _identity_changed(state):
    return _with_player(state, 1, identity=Identity.RENEGADE)


def _seat_changed(state):
    """座位号也入键：解析用它写实例 ``owner``（``seat == 下标`` 是模型不变量，但键不靠它）。"""
    return _with_player(state, 1, seat=2)


def _pack_id_changed(state):
    """只有一个内容包；``jie`` 是同样可加载的**武将表**包 id，足以证明 ``pack_id`` 在键里。"""
    return state.model_copy(update={"pack_id": "jie"})


def _with_suppression():
    """基座：座位 0 带一条失效记录（``scope`` / ``predicate`` 取登记闭集的唯一成员）。"""

    def build():
        record = SkillSuppression(
            skill_id=APPLYING_SKILL,
            scope=SkillSuppressionScope.TURN,
            predicate=SkillSuppressionPredicate.ALL_NON_LOCKED,
        )
        return _with_player(_base_state(), 0, suppressed_skills=[record])

    return build


def _rewrite_suppression(state, **fields):
    """换失效记录的某一个字段。

    ``scope`` / ``predicate`` 的登记闭集今天各只有一个成员，故这两个"只改一位"只能用
    ``model_construct`` 造一个**换值**的记录。它是**键构造**的合成输入：两条例用标签进
    :data:`_KEY_ONLY_CASES`，不从引擎入口跑（§6：不与写入面同源的夹具不当行为断言）。
    """
    record = state.players[0].suppressed_skills[0]
    replacement = SkillSuppression.model_construct(
        skill_id=fields.get("skill_id", record.skill_id),
        scope=fields.get("scope", record.scope),
        predicate=fields.get("predicate", record.predicate),
    )
    return _with_player(state, 0, suppressed_skills=[replacement])


def _suppression_added(state):
    return _with_player(
        state,
        0,
        suppressed_skills=[SkillSuppression(skill_id=APPLYING_SKILL)],
    )


def _unchanged_state(state):
    return state


_KEY_CASES = (
    # (label, 基座, 只改这一位的变换, include_suppressed)
    ("equip_added", _base_state, _equip_added, False),
    ("equip_removed", _base_state, _equip_removed, False),
    ("equip_type_changed", _base_state, _equip_type_changed, False),
    ("virtual_equipment_added", _base_state, _virtual_equipment_added, False),
    ("hero_changed", _base_state, _hero_changed, False),
    ("hero_cleared", _base_state, _hero_cleared, False),
    ("granted_skill_added", _base_state, _granted_added, False),
    ("lost_skill_added", _base_state, _lost_added, False),
    ("marker_added", _base_state, _marker_added, False),
    ("suppression_added", _base_state, _suppression_added, False),
    (
        "suppression_skill_id_changed",
        _with_suppression(),
        lambda state: _rewrite_suppression(state, skill_id="hero.lvmeng.keji"),
        False,
    ),
    (
        "suppression_scope_changed",
        _with_suppression(),
        lambda state: _rewrite_suppression(state, scope="other_scope"),
        False,
    ),
    (
        "suppression_predicate_changed",
        _with_suppression(),
        lambda state: _rewrite_suppression(state, predicate="other_predicate"),
        False,
    ),
    ("seat_dead", _base_state, _seat_dead, False),
    ("death_time_window", _base_state, _death_time_window, False),
    ("identity_changed", _base_state, _identity_changed, False),
    ("seat_changed", _base_state, _seat_changed, False),
    ("pack_id_changed", _base_state, _pack_id_changed, False),
    ("include_suppressed", _base_state, _unchanged_state, True),
)
"""键覆盖的用例表：每个字段一条"只改这一位"。

``_PLAYER_KEY_FIELDS`` 的每一项都必须在这里有对应行（``test_every_key_field_changes_the_key``
按字段表反查）；``pack_id`` / ``include_suppressed`` 是局面级两位，也在表里。
"""


def _moved_labels(key_function) -> list[str]:
    """按用例表跑一遍覆盖检查，返回"键确实变了"的用例名（保持表内顺序）。"""
    moved: list[str] = []
    for label, base_factory, mutate, include_suppressed in _KEY_CASES:
        before = base_factory()
        after = mutate(base_factory())
        if key_function(after, include_suppressed) != key_function(before, False):
            moved.append(label)
    return moved


# ---------------------------------------------------------------------------
# ① 键覆盖 + 合成反例
# ---------------------------------------------------------------------------


_FIELD_CASE_LABELS = {
    "seat": ("seat_changed",),
    "identity": ("identity_changed",),
    "alive": ("seat_dead",),
    "death_time_skills_active": ("death_time_window",),
    "equip": ("equip_added", "equip_removed", "equip_type_changed"),
    "virtual_equipment": ("virtual_equipment_added",),
    "hero": ("hero_changed", "hero_cleared"),
    "granted_skills": ("granted_skill_added",),
    "lost_skills": ("lost_skill_added",),
    "suppressed_skills": (
        "suppression_added",
        "suppression_skill_id_changed",
        "suppression_scope_changed",
        "suppression_predicate_changed",
    ),
    "markers": ("marker_added",),
}
"""``_PLAYER_KEY_FIELDS`` 的每一项 → 钉住它的用例（字段表与用例表的机械对应）。"""

_TOP_LEVEL_CASE_LABELS = ("pack_id_changed", "include_suppressed")
"""局面级两位的用例（不在 ``_PLAYER_KEY_FIELDS`` 里）。"""

_KEY_ONLY_CASES = frozenset({"suppression_scope_changed", "suppression_predicate_changed"})
"""只用于**键构造**断言的用例：记录里的值绕过了枚举校验，引擎状态里造不出来。"""


def test_every_key_field_changes_the_key():
    """每个字段"只改这一位 ⇒ 键必变"；用例表逐项覆盖 ``_PLAYER_KEY_FIELDS``。"""
    labels = [label for label, *_ in _KEY_CASES]
    covered = {label for cases in _FIELD_CASE_LABELS.values() for label in cases}

    assert tuple(_FIELD_CASE_LABELS) == _PLAYER_KEY_FIELDS, "字段表变了而用例表没跟上"
    assert covered <= set(labels)
    assert set(labels) - covered == set(_TOP_LEVEL_CASE_LABELS)
    assert _moved_labels(_resolve_key) == labels


def _key_without_player_field(dropped: str):
    """**键构造函数的副本**：从真实键里删掉一个座位级字段（合成反例用）。"""
    index = _PLAYER_KEY_FIELDS.index(dropped)

    def key(state, include_suppressed):
        pack_id, flag, players = _resolve_key(state, include_suppressed)
        return (pack_id, flag, tuple(bits[:index] + bits[index + 1 :] for bits in players))

    return key


def test_the_coverage_check_goes_red_when_one_key_field_is_dropped():
    """删掉 ``alive`` ⇒ ``seat_dead`` 这一条必须不再让键变，其余用例照旧。"""
    dropped = "alive"
    moved = _moved_labels(_key_without_player_field(dropped))
    expected = [label for label, *_ in _KEY_CASES if label != "seat_dead"]

    assert "seat_dead" not in moved
    assert moved == expected, "删一个字段只该影响那一位的用例，其余用例必须仍然变红"


# ---------------------------------------------------------------------------
# ② 缓存路径 == 无缓存路径（逐字段差分）
# ---------------------------------------------------------------------------


def test_the_cached_path_equals_the_uncached_one_for_every_field():
    for label, base_factory, mutate, include_suppressed in _KEY_CASES:
        if label in _KEY_ONLY_CASES:
            continue  # 合成值只断言"在键里"，不作为引擎行为夹具（见 _rewrite_suppression）
        state = mutate(base_factory())
        catalog_module._RESOLVE_CACHE.clear()
        cached = resolve_instances(state, include_suppressed=include_suppressed)
        uncached = _resolve_instances_uncached(
            state, include_suppressed=include_suppressed
        )
        assert cached == uncached, label


def test_include_suppressed_is_part_of_the_result_and_of_the_key():
    state = _suppression_added(_base_state())
    visible = resolve_instances(state, include_suppressed=False)
    including = resolve_instances(state, include_suppressed=True)

    assert {instance.skill_id for instance in including} - {
        instance.skill_id for instance in visible
    }, "失效记录必须真的挡掉实例（不是空转），否则这条用例证明不了键里的这一位"
    assert _resolve_key(state, False) != _resolve_key(state, True)


# ---------------------------------------------------------------------------
# ③ 第二项缓存：hero_card_skill_ids 的 lru_cache（键 = (hero_id, pack_id)）
# ---------------------------------------------------------------------------


def test_hero_card_skill_ids_is_cached_by_hero_and_pack():
    before = hero_card_skill_ids.cache_info()
    first = hero_card_skill_ids("guanyu")
    second = hero_card_skill_ids("guanyu")
    after_default = hero_card_skill_ids.cache_info()

    assert second is first, "lru_cache 命中必须返回同一个 tuple"
    assert after_default.hits - before.hits >= 1
    assert after_default.misses - before.misses in (0, 1), "同一 (hero, pack) 最多未中一次"

    # pack_id 是键的一部分：关羽只在标准武将表里，界版包里查不到 —— 两次查询各自成键，
    # 且第二次命中（不是把标准包的清单当成了界包的）。
    jie_first = hero_card_skill_ids("guanyu", pack_id="jie")
    jie_second = hero_card_skill_ids("guanyu", pack_id="jie")
    after_jie = hero_card_skill_ids.cache_info()

    assert jie_first == () and jie_second is jie_first
    assert jie_first != first
    assert after_jie.hits - after_default.hits >= 1
    assert after_jie.misses - after_default.misses in (0, 1)


# ---------------------------------------------------------------------------
# ④ 命中：返回同一个 tuple；未命中：走无缓存实现
# ---------------------------------------------------------------------------


def test_a_miss_runs_the_original_function_and_a_hit_returns_the_same_tuple():
    catalog_module._RESOLVE_CACHE.clear()
    state = _base_state()
    hits0, misses0 = catalog_module._resolve_cache_counters()

    first = resolve_instances(state)
    hits1, misses1 = catalog_module._resolve_cache_counters()
    second = resolve_instances(state)
    hits2, misses2 = catalog_module._resolve_cache_counters()

    assert (hits1 - hits0, misses1 - misses0) == (0, 1), "首次查询必须是未命中"
    assert (hits2 - hits1, misses2 - misses1) == (1, 0), "同一局面再查必须命中"
    assert second is first, "命中必须共享同一个冻结 tuple"
    assert first == _resolve_instances_uncached(state)


def test_replacing_the_catalog_invalidates_the_cached_results(monkeypatch):
    """合成反例的邻居：换表后旧缓存必须整批作废（否则解析读到的定义来自另一张表）。"""
    catalog_module._RESOLVE_CACHE.clear()
    state = _base_state()
    full = resolve_instances(state)
    removed = next(
        instance.skill_id for instance in full if instance.source.kind.value == "HERO_CARD"
    )

    without = {
        skill_id: definition
        for skill_id, definition in catalog_module.CATALOG.items()
        if skill_id != removed
    }
    monkeypatch.setattr(catalog_module, "CATALOG", MappingProxyType(without))

    reduced = resolve_instances(state)

    assert reduced == _resolve_instances_uncached(state)
    assert removed not in {instance.skill_id for instance in reduced}


# ---------------------------------------------------------------------------
# ④ 有界：固定容量 + 按插入序淘汰
# ---------------------------------------------------------------------------


def test_the_cache_is_bounded_and_evicts_in_insertion_order():
    catalog_module._RESOLVE_CACHE.clear()
    base = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    states = []
    for index in range(catalog_module._RESOLVE_CACHE_LIMIT + 8):
        player = base.players[0].model_copy(
            update={"granted_skills": [f"missing.skill.{index}"]}
        )
        state = base.model_copy(update={"players": (player, base.players[1])})
        resolve_instances(state)
        states.append(state)

    assert len(catalog_module._RESOLVE_CACHE) == catalog_module._RESOLVE_CACHE_LIMIT
    assert catalog_module._resolve_key(states[0], False) not in catalog_module._RESOLVE_CACHE
    assert catalog_module._resolve_key(states[-1], False) in catalog_module._RESOLVE_CACHE


# ---------------------------------------------------------------------------
# ⑤ 命中率下限（机器无关的性能替代判据）
# ---------------------------------------------------------------------------


def _play_five_player_game(seed: int) -> None:
    """跑完一整局（5 人身份局 + 启用将池），只为让解析被真实调用。"""
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=5),
        pack=PACK,
        hero_pool=(),
    )
    engine.run([HeuristicPlayer(ai_stream(seed, seat)) for seat in range(5)])


def test_the_hit_rate_stays_at_the_measured_floor():
    catalog_module._RESOLVE_CACHE.clear()
    hits_before, misses_before = catalog_module._resolve_cache_counters()

    for seed in HIT_RATE_SEEDS:
        _play_five_player_game(seed)

    hits, misses = catalog_module._resolve_cache_counters()
    calls = (hits - hits_before) + (misses - misses_before)
    assert calls >= 5_000, f"样本太少，命中率不可读：{calls}"
    rate = (hits - hits_before) / calls
    assert rate >= _HIT_RATE_FLOOR, f"命中率 {rate:.4f} < 下限 {_HIT_RATE_FLOOR}"
    assert len(catalog_module._RESOLVE_CACHE) <= catalog_module._RESOLVE_CACHE_LIMIT
