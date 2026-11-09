"""武将总表：25 名标准 + 界版已到达条目的数据断言与登记（票 12；M6 P0 三票收口 16 → 25）。

本文件只装**跨技能**的登记事实（逐技能的构造局面单测按族住各自文件）：

- ``CATALOG`` 的规模与分区：装备 22 + 武将 77 = **99 条**，且武将那半恰是数据文件里
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
from sgs.skills.catalog import MATCH_ACTOR_KEY, MATCH_OWNER_KEY
from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent
from sgs.state import (
    HeroCardZone,
    CardRef,
    EndPhase,
    GameState,
    Marker,
    MoveReason,
    Pass,
    Phase,
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

JIE_MOBILE_HEROES = load_heroes("jie_mobile")
"""**移动版**那一份（M6 票 04 起）：与 OL 系列**版本并存**，同一真名可以各持一版。

显示名按"OL 不加后缀、移动版加「移动版」"命名（用户裁定），真名共用（`曹仁`），
版本字段是 ``jie-mobile``。"""

FENG_HEROES = load_heroes("feng")
FENG_MOBILE_HEROES = load_heroes("feng_mobile")
"""**神话再临·风包**那一份（票 03 起）：独立系列包，版本字段 ``feng``（黄忠 / 烈弓）。"""

HUO_MOBILE_HEROES = load_heroes("huo_mobile")
"""**神话再临·火包的移动版**那一份（huo-dianwei-mobile 票 01 起）：移动版独立成表，
版本字段 ``huo-mobile``，显示名加「移动版」后缀（典韦移动版 / 强袭）。"""

HUO_HEROES = load_heroes("huo")
"""**神话再临·火包的 OL 版**那一份（huo-shenzhugeliang 票 01 起）：独立系列包，版本字段
``huo``，显示名不加后缀（神诸葛亮 / 七星 / 狂风 / 大雾）。"""

LEI_MOBILE_HEROES = load_heroes("lei_mobile")
"""**神话再临·雷包的移动版**那一份（lukang-mobile 票 01 起）：独立表、版本字段
``lei-mobile``，显示名加「移动版」后缀（陆抗移动版 / 决堰 / 破势 / ☆怀柔）。"""

HOMEBREW_HEROES = load_heroes("homebrew")
"""**自制包**那一份（韬·刘备自制版）：独立系列包，版本字段 ``homebrew``，真名「刘备」
（与标准版同名 ⇒ 同一局仍只出一个）。"""

LEI_HEROES = load_heroes("lei")
"""**神话再临·雷包**那一份（lei-batch 票 01 起）：独立系列包，版本字段 ``lei``，
显示名不加版本后缀（郝昭 / 镇骨；后续张绣 / 诸葛瞻逐票追加）。"""

JIE_FENG_MOBILE_HEROES = load_heroes("jie_feng_mobile")
"""**界限突破-风包的移动版**那一份（jie-feng-batch 票 01 起）：移动版独立成表，
版本字段 ``jie-feng-mobile``、显示名加「移动版」后缀（界黄忠移动版 / 烈弓）。"""

LIN_MOBILE_HEROES = load_heroes("lin_mobile")
"""**神话再临·林包的移动版**那一份（lin-batch 票 01 起）：移动版独立成表，版本字段
``lin-mobile``、显示名加「移动版」后缀（神曹操移动版 / 归心 / 飞影）。"""

YIJIANG_2014_HEROES = load_heroes("yijiang_2014")
"""**一将成名-2014** 那一份（jushou-lusu 票 01 起）：独立系列包（不是标准版的另一版本、
也不是移动版），版本字段 ``yijiang-2014``、显示名**不加**版本后缀（沮授 / 渐营 / 矢北）。"""

JIE_YIJIANG_2014_MOBILE_HEROES = load_heroes("jie_yijiang_2014_mobile")
"""**界一将成名-2014·移动版**那一份（jie-yijiang-2014-mobile 票 01 起）：界 + 系列包 +
移动版三者叠加 ⇒ 版本字段 ``jie-yijiang-2014-mobile``、显示名加「移动版」后缀
（界陈群移动版 / 定品）。"""

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

JIE_MOBILE_ENABLED = tuple(
    hero.id for hero in JIE_MOBILE_HEROES.heroes if hero.enabled
)

FENG_ENABLED = tuple(hero.id for hero in FENG_HEROES.heroes if hero.enabled)
FENG_MOBILE_ENABLED = tuple(
    hero.id for hero in FENG_MOBILE_HEROES.heroes if hero.enabled
)
HUO_MOBILE_ENABLED = tuple(
    hero.id for hero in HUO_MOBILE_HEROES.heroes if hero.enabled
)
HUO_ENABLED = tuple(hero.id for hero in HUO_HEROES.heroes if hero.enabled)
HOMEBREW_ENABLED = tuple(hero.id for hero in HOMEBREW_HEROES.heroes if hero.enabled)
LEI_ENABLED = tuple(hero.id for hero in LEI_HEROES.heroes if hero.enabled)
LEI_MOBILE_ENABLED = tuple(hero.id for hero in LEI_MOBILE_HEROES.heroes if hero.enabled)
JIE_FENG_MOBILE_ENABLED = tuple(
    hero.id for hero in JIE_FENG_MOBILE_HEROES.heroes if hero.enabled
)
LIN_MOBILE_ENABLED = tuple(
    hero.id for hero in LIN_MOBILE_HEROES.heroes if hero.enabled
)
YIJIANG_2014_ENABLED = tuple(
    hero.id for hero in YIJIANG_2014_HEROES.heroes if hero.enabled
)
JIE_YIJIANG_2014_MOBILE_ENABLED = tuple(
    hero.id for hero in JIE_YIJIANG_2014_MOBILE_HEROES.heroes if hero.enabled
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
# ① CATALOG 的一分为二（装备 22 + 武将 66 = 88）
# ---------------------------------------------------------------------------


def test_the_catalog_holds_twenty_two_equipment_and_twenty_nine_hero_skills():
    """验收 1：``CATALOG`` = **88** 条（22 装备 + 66 武将），且两半由 ``carrier`` 分区。

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
    票 09 起 64（界甄姬【洛神】/【倾国】 = 首个**牌标签**载体），票 10 起 66
    （界陆逊【谦逊】/【连营】 = 首个**武将牌上的牌区**载体），票 11 起 67
    （界吕蒙【博图】 = 首个**额外回合 / 轮次**载体），票 14 起 73
    （界曹操【奸雄】/【护驾】、界司马懿【反馈】/【鬼才】、界张辽【突袭】、
    界许褚【裸衣】 = 首个**决定者 ≠ 受益者**的钩子与跨回合伤害加成），
    票 15 起 77（界郭嘉【天妒】/【遗计】、界李典【恂恂】/【忘隙】 = 首个**双向
    逐点触发**的伤害后技能与固定切分的摸牌阶段排列），票 16 起 83（界刘备
    【仁德】/【激将】、界赵云【龙胆】/【涯角】、界诸葛亮【观星】/【空城】 = 首个
    "每名角色限一次 + 视为使用基本牌"的给牌技、首个"尾部紧凑段"虚拟牌与首个
    "结束阶段再次观星"的双时机排列技能）。huo-wolong-yanliang 票 01 起 164
    （火包·OL 卧龙诸葛【八阵】/【火计】/【看破】 = 首个"锁定技只往别人的【闪】窗口里
    补一条候选"的载体，也是转化族在**无懈的收集式窗口**里的首个载体），票 02 起 165
    （火包·OL 颜良文丑【双雄】 = 首个"阶段体自己开判定、再把判定结果的颜色交给一条
    ``CANDIDATES`` 修饰器"的载体）。
    函数名沿用 M3 的节点名（``test_dod_m3`` 的 DoD 绑定），数字以函数体为准。
    """
    equipment = [d for d in CATALOG.values() if d.carrier.value == "EQUIPMENT"]
    heroes = [d for d in CATALOG.values() if d.carrier.value == "HERO"]

    # lin-batch2 票 03：林包移动版·贾诩移动版三条（完杀 / 乱武 / 帷幕）⇒ 185 → 188、
    # 武将技 161 → 164（装备 22 不变）。
    # jushou-lusu 票 01：一将成名-2014·沮授两条（渐营 / 矢北）⇒ 188 → 190、武将技 164 → 166。
    # jushou-lusu 票 02：林包移动版·鲁肃两条（好施 / 缔盟）⇒ 190 → 192、武将技 166 → 168。
    # jie-feng-batch2 票 01：界夏侯渊移动版【神速】⇒ 192 → 193、武将技 168 → 169。
    # jie-feng-batch2 票 02：界张角移动版三条（雷击 / 鬼道 / 黄天）⇒ 193 → 196、武将技 169 → 172。
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】⇒ 196 → 197、武将技 172 → 173；
    # 票 02：界顾雍移动版【慎行】⇒ 197 → 198、武将技 173 → 174。
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】⇒ 198 → 199、武将技 174 → 175
    # （【法恩】C1 摘出 ⇒ 本将只带一条技能入池）。
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】⇒ 199 → 200、武将技 175 → 176
    # （【秉壹】C3 摘出 ⇒ 本将只带一条技能入池）。
    # yijiang-2014 票 09（W5b）：同一个顾雍补【秉壹】⇒ 200 → 201、武将技 176 → 177
    # （座位多选面落地后解锁；数据行同一条武将，启用版本 / 真名都不变）。
    # yijiang-2014 票 08（W4b）：同一个陈群补【法恩】（状态位写入口解封后）⇒ 201 → 202、
    # 武将技 177 → 178（数据行同一条武将，启用版本 / 真名都不变）。
    assert len(CATALOG) == 202
    assert len(equipment) == 22, "装备不变：本票一条是武将技"
    assert len(heroes) == 178


def test_the_hero_half_of_the_catalog_is_exactly_the_enabled_hero_sheet():
    """武将那半 = 全部启用武将的技能清单（标准表 + 界版扩展表；数据文件是唯一真源）。"""
    expected = {
        f"hero.{hero.id}.{skill.id}"
        for table in (
            HEROES,
            JIE_HEROES,
            JIE_MOBILE_HEROES,
            FENG_HEROES,
            FENG_MOBILE_HEROES,
            HUO_MOBILE_HEROES,
            HUO_HEROES,
            HOMEBREW_HEROES,
            LEI_HEROES,
            LEI_MOBILE_HEROES,
            JIE_FENG_MOBILE_HEROES,
            LIN_MOBILE_HEROES,
            YIJIANG_2014_HEROES,
            JIE_YIJIANG_2014_MOBILE_HEROES,
        )
        for hero in table.heroes
        if hero.enabled
        for skill in hero.skills
    }
    actual = {d.skill_id for d in CATALOG.values() if d.carrier.value == "HERO"}

    assert actual == expected
    # jushou-lusu 票 01：沮授两条 ⇒ 164 → 166；票 02：鲁肃两条 ⇒ 166 → 168。
    # jie-feng-batch2 票 01：界夏侯渊移动版【神速】⇒ 168 → 169；票 02：界张角移动版三条 ⇒ 169 → 172。
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】⇒ 172 → 173；票 02：界顾雍移动版 ⇒ 174。
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】⇒ 174 → 175。
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】⇒ 175 → 176；票 09 补【秉壹】⇒ 177。
    # yijiang-2014 票 08（W4b）：同一个陈群补【法恩】⇒ 177 → 178。
    assert len(expected) == 178


def test_every_hero_skill_id_is_fully_qualified():
    enabled = (
        set(ENABLED)
        | set(JIE_ENABLED)
        | set(JIE_MOBILE_ENABLED)
        | set(FENG_ENABLED)
        | set(FENG_MOBILE_ENABLED)
        | set(HUO_MOBILE_ENABLED)
        | set(HUO_ENABLED)
        | set(HOMEBREW_ENABLED)
        | set(LEI_ENABLED)
        | set(LEI_MOBILE_ENABLED)
        | set(JIE_FENG_MOBILE_ENABLED)
        | set(LIN_MOBILE_ENABLED)
        | set(YIJIANG_2014_ENABLED)
        | set(JIE_YIJIANG_2014_MOBILE_ENABLED)
    )
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
    for hero in (
        *HEROES.heroes,
        *JIE_HEROES.heroes,
        *JIE_MOBILE_HEROES.heroes,
        *FENG_HEROES.heroes,
        *FENG_MOBILE_HEROES.heroes,
        *HUO_MOBILE_HEROES.heroes,
        *JIE_FENG_MOBILE_HEROES.heroes,
        *LIN_MOBILE_HEROES.heroes,
        *YIJIANG_2014_HEROES.heroes,
        *JIE_YIJIANG_2014_MOBILE_HEROES.heroes,
    ):
        if hero.id not in (
            set(ENABLED)
            | set(JIE_ENABLED)
            | set(JIE_MOBILE_ENABLED)
            | set(FENG_ENABLED)
            | set(FENG_MOBILE_ENABLED)
            | set(HUO_MOBILE_ENABLED)
            | set(JIE_FENG_MOBILE_ENABLED)
            | set(LIN_MOBILE_ENABLED)
            | set(YIJIANG_2014_ENABLED)
            | set(JIE_YIJIANG_2014_MOBILE_ENABLED)
        ):
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
# 官方 §九 L964–966。形态 = 阶段体（``PHASE_DRAW``）+ 一步座位询问（``PickSeats``，
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
# 官方 §九 L968–970。形态 = 阶段体（``PHASE_DRAW``，把摸牌张数改写成 1 并落本回合
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
# §九 L1027–1028。形态 = 阶段体（``PHASE_DISCARD_START``，非锁定 ⇒ 问"是否发动"）+
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
    # 火包 OL（神诸葛亮）：三条技能共用一支测试文件
    "hero.shen_zhugeliang.qixing": "test_hero_huo.py",
    "hero.taishici.tianyi": "test_hero_huo.py",
    "hero.xunyu.quhu": "test_hero_huo.py",
    "hero.xunyu.jieming": "test_hero_huo.py",
    "hero.shen_zhugeliang.kuangfeng": "test_hero_huo.py",
    "hero.shen_zhugeliang.dawu": "test_hero_huo.py",
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
    # 本票：移动版界曹仁【据守】
    "hero.jie_caoren_mobile.jushou": "test_hero_feng.py",
    # 票 03：风包黄忠【烈弓】
    "hero.huangzhong.liegong": "test_hero_feng.py",
    # 票 04 / 票 06：风包移动版神关羽【武神】/【武魂】
    "hero.shen_guanyu_mobile.wushen": "test_hero_feng.py",
    "hero.shen_guanyu_mobile.wuhun": "test_hero_feng.py",
    # 票 01：风包移动版神吕蒙【涉猎】/【攻心】
    "hero.shen_lvmeng_mobile.shelie": "test_hero_feng.py",
    "hero.shen_lvmeng_mobile.gongxin": "test_hero_feng.py",
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
    # M6 票 11：额外回合 + 轮次 + 界吕蒙【博图】
    "hero.jie_lvmeng.botu": "test_hero_extra_turn.py",
    # M6 票 09：标签骨架 + 界甄姬【洛神】/【倾国】
    "hero.jie_zhenji.luoshen": "test_tags.py",
    "hero.jie_zhenji.qingguo": "test_tags.py",
    # M6 票 10：武将牌上的牌区 + 界陆逊【谦逊】/【连营】
    "hero.jie_luxun.qianxun": "test_hero_card_zone.py",
    "hero.jie_luxun.lianying": "test_hero_card_zone.py",
    # M6 票 14：界·魏 A（曹操 / 司马懿 / 张辽 / 许褚）
    "hero.jie_caocao.jianxiong": "test_hero_jie_wei.py",
    "hero.jie_caocao.hujia": "test_hero_jie_wei.py",
    "hero.jie_simayi.fankui": "test_hero_jie_wei.py",
    "hero.jie_simayi.guicai": "test_hero_jie_wei.py",
    # feng-zhangjiao 票 01：张角移动版三条（同一张票、同一个测试文件）
    "hero.zhangjiao_mobile.leiji": "test_hero_feng.py",
    "hero.zhangjiao_mobile.guidao": "test_hero_feng.py",
    "hero.zhangjiao_mobile.huangtian": "test_hero_feng.py",
    # huo-dianwei-mobile 票 01：火包移动版典韦【强袭】（新包 → 新测试文件）
    "hero.dianwei_mobile.qiangxi": "test_hero_huo.py",
    # huo-mobile-batch 票 01：火包移动版庞德【马术】/【猛进】
    "hero.pangde_mobile.mashu": "test_hero_huo.py",
    "hero.pangde_mobile.mengjin": "test_hero_huo.py",
    # huo-mobile-batch 票 02：火包移动版庞统【连环】/【涅槃】
    "hero.pangtong_mobile.lianhuan": "test_hero_huo.py",
    "hero.pangtong_mobile.niepan": "test_hero_huo.py",
    "hero.shen_zhouyu_mobile.qinyin": "test_hero_huo.py",
    "hero.shen_zhouyu_mobile.yeyan": "test_hero_huo.py",
    # huo-mobile-batch 票 03：火包移动版袁绍【乱击】/【血裔】
    "hero.yuanshao_mobile.luanji": "test_hero_huo.py",
    "hero.yuanshao_mobile.xueyi": "test_hero_huo.py",
    "hero.jie_zhangliao.tuxi": "test_hero_jie_wei.py",
    "hero.jie_xuchu.luoyi": "test_hero_jie_wei.py",
    # M6 票 15：界·魏 B（郭嘉 / 李典）
    "hero.jie_guojia.tiandu": "test_hero_jie_wei_b.py",
    "hero.jie_guojia.yiji": "test_hero_jie_wei_b.py",
    "hero.jie_lidian.xunxun": "test_hero_jie_wei_b.py",
    "hero.jie_lidian.wangxi": "test_hero_jie_wei_b.py",
    # M6 票 16：界·蜀（刘备 / 赵云 / 诸葛亮）
    "hero.jie_liubei.rende": "test_hero_jie_shu.py",
    "hero.jie_liubei.jijiang": "test_hero_jie_shu.py",
    "hero.jie_zhaoyun.longdan": "test_hero_jie_shu.py",
    "hero.jie_zhaoyun.yajiao": "test_hero_jie_shu.py",
    "hero.jie_zhugeliang.guanxing": "test_hero_jie_shu.py",
    "hero.jie_zhugeliang.kongcheng": "test_hero_jie_shu.py",
    # M6 票 17：界·吴（孙权 / 黄盖 / 大乔 / 孙尚香）
    "hero.jie_sunquan.zhiheng": "test_hero_jie_wu.py",
    "hero.jie_sunquan.jiuyuan": "test_hero_jie_wu.py",
    "hero.jie_huanggai.kurou": "test_hero_jie_wu.py",
    "hero.jie_huanggai.zhaxiang": "test_hero_jie_wu.py",
    "hero.jie_daqiao.guose": "test_hero_jie_wu.py",
    "hero.jie_daqiao.liuli": "test_hero_jie_wu.py",
    "hero.jie_sunshangxiang.jieyin": "test_hero_jie_wu.py",
    "hero.jie_sunshangxiang.xiaoji": "test_hero_jie_wu.py",
    # M6 票 08：手牌上限双轴 + 界周瑜 / 界夏侯惇 / 界黄月英
    "hero.jie_zhouyu.yingzi": "test_hero_jie_hand_limit.py",
    "hero.jie_zhouyu.fanjian": "test_hero_jie_hand_limit.py",
    "hero.jie_xiahoudun.ganglie": "test_hero_jie_hand_limit.py",
    "hero.jie_xiahoudun.qingjian": "test_hero_jie_hand_limit.py",
    "hero.jie_huangyueying.jizhi": "test_hero_jie_hand_limit.py",
    "hero.jie_huangyueying.qicai": "test_hero_jie_hand_limit.py",
    # M6 票 18：界·群（华佗 / 吕布 / 华雄 / 公孙瓒）
    "hero.jie_huatuo.jijiu": "test_hero_jie_qun.py",
    "hero.jie_huatuo.chuli": "test_hero_jie_qun.py",
    "hero.jie_lvbu.wushuang": "test_hero_jie_qun.py",
    "hero.jie_lvbu.liyu": "test_hero_jie_qun.py",
    "hero.jie_huaxiong.yaowu": "test_hero_jie_qun.py",
    "hero.jie_huaxiong.shizhan": "test_hero_jie_qun.py",
    "hero.jie_gongsunzan.qiaomeng": "test_hero_jie_qun.py",
    "hero.jie_gongsunzan.yicong": "test_hero_jie_qun.py",
    # 风包魏延【狂骨】（feng-weiyan 票 01）：锁定技，造伤后逐点回复。
    "hero.weiyan.kuanggu": "test_hero_feng.py",
    "hero.xiahouyuan.shensu": "test_hero_feng.py",
    "hero.jie_xiahouyuan_mobile.shensu": "test_hero_jie_feng.py",
    # jie-feng-batch2 票 02：界张角移动版三条
    "hero.jie_zhangjiao_mobile.leiji": "test_hero_jie_feng.py",
    "hero.jie_zhangjiao_mobile.guidao": "test_hero_jie_feng.py",
    "hero.jie_zhangjiao_mobile.huangtian": "test_hero_jie_feng.py",
    "hero.yuji_mobile.guohuo": "test_hero_feng.py",
    # 界限突破-风包·移动版（jie-feng-batch 票 01）：界黄忠移动版【烈弓】。
    "hero.jie_huangzhong_mobile.liegong": "test_hero_jie_feng.py",
    # jie-feng-batch 票 02：界魏延移动版【狂骨】【奇谋】。
    "hero.jie_weiyan_mobile.kuanggu": "test_hero_jie_feng.py",
    "hero.jie_weiyan_mobile.qimou": "test_hero_jie_feng.py",
    # huo-wolong-yanliang 票 01：火包·OL 卧龙诸葛（八阵 / 火计 / 看破）。
    "hero.wolong_zhugeliang.bazhen": "test_hero_huo.py",
    "hero.wolong_zhugeliang.huoji": "test_hero_huo.py",
    "hero.wolong_zhugeliang.kanpo": "test_hero_huo.py",
    "hero.yanliang_wenchou.shuangxiong": "test_hero_huo.py",
}
"""启用技能 → **点名它的族级单测文件**（票 14 §1 的机械覆盖表；M6 票 01 加 3 条、
票 02 加 5 条、票 03 加 3 条、票 04 加 4 条界将技能、票 05 加界张飞 2 条、票 06 加界徐庶 /
界吕蒙 6 条、票 07 加界甘宁 / 界貂蝉 4 条、票 08 加界周瑜 / 界夏侯惇 / 界黄月英 6 条、
票 09 加界甄姬 2 条、票 10 加界陆逊 2 条、票 11 加界吕蒙【博图】1 条、
票 14 加界·魏 A 6 条、票 15 加界·魏 B 4 条、票 16 加界·蜀 6 条；
jie-feng-batch 票 01 加界黄忠移动版 1 条）。"""


def test_every_enabled_hero_skill_is_named_by_a_family_test_file():
    """**每条启用技能都至少被一条族级单测点名**（票 14 §1 / 票 12 §要做什么 3 的机械补全）。"""
    skills = {
        hero_skill_id(hero.id, skill.id)
        for table in (
            HEROES,
            JIE_HEROES,
            JIE_MOBILE_HEROES,
            FENG_HEROES,
            FENG_MOBILE_HEROES,
            HUO_MOBILE_HEROES,
            HUO_HEROES,
            JIE_FENG_MOBILE_HEROES,
        )
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
_PROBE_DODGE_CARD = cards_of_type(PACK, "dodge")[0]
"""界【护驾】②的正面探针要一张【闪】牌面（"其他魏势力角色使用 / 打出【闪】"那一侧）。"""
_PROBE_TRICK_CARD = cards_of_type(PACK, "dismantlement")[0]
"""【集智】的正面探针要一张**非延时类锦囊**牌面（命中"使用者是我 + 牌面合格"那一侧）。

与上面三张同属**合成局面用到的牌 id**（牌堆档位固定，模块级算一次）。"""


class _HookFrame:
    """只带 ``data`` 的最小帧：``Hook.condition`` 的输入面就是 ``frame.data``（+ ``host.state``）。"""

    def __init__(self, **data: Any) -> None:
        self.data = data


class _HookHost:
    """只带 ``state`` 的最小宿主：条件里唯一用到的宿主面。

    ``outcome_decided``（lin-batch2 票 01）：【行殇】的条件要读**终局位**——那个事实只挂在
    引擎上（`Engine.pending_outcome`），`ACTIVATION_PLANS` 的构造器拿不到，故只能写在
    `condition` 里（票面 Q3 的实现代价）。合成宿主恒报"未判胜"，让正面对照成立。
    """

    def __init__(self, state: GameState) -> None:
        self.state = state

    def outcome_decided(self) -> bool:
        return False


def _congtao_probe_state() -> GameState:
    """【从讨】的正面对照需要两件事：**五座环**（座位 0 在 ME 的攻击范围内、却不在座位 2
    的攻击范围内——这样"持有者换成别人"那一步才会翻假），以及一个非空牌堆（条件要求
    "有牌可观看"）。"""
    slash = cards_of_type(PACK, "slash")[0]
    deck = tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id != slash
    )
    return build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=deck,
    )


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


def _bengkuai_probe_state() -> GameState:
    """【崩坏】的正面对照：**别人比我低**（ME 3 体力、座位 2 是 2 体力）。

    "不是全场体力值最低"的反向判据 = **存在**其他存活角色 `hp < 我`；默认探针局面里
    ME 3 / 其余 4 全 ⇒ 我反而是最低，条件为假，故另给一个"有人严格更低"的局面。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hp=3),
            SeatSpec(hp=2),
        ],
    )


def _bingyi_probe_state() -> GameState:
    """【秉壹】的正面对照需要"我手上有牌"（0 张手牌不给入口 ⇒ 条件为假）。

    默认探针局面里 ME（座位 1）的手牌**故意为空**（【连营】要"移动后手牌为空"），
    故另给一份：ME 两张手牌（【秉壹】的"展示所有手牌"要真的有牌可亮）。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hand=tuple(cards_of_type(PACK, "dodge")[:2])),
            SeatSpec(),
        ],
    )


def _baonue_probe_state() -> GameState:
    """【暴虐】的正面对照：伤害来源（座位 0）与 ME 同为**群**势力，座位 2 是魏。

    持有者换成座位 2（OTHER）后 `same_kingdom(帧来源, 持有者)` 翻假 ⇒ 负断言成立。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(kingdom=Kingdom.QUN),
            SeatSpec(kingdom=Kingdom.QUN),
            SeatSpec(kingdom=Kingdom.WEI),
        ],
    )


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


def _qianxun_probe_state() -> GameState:
    """【谦逊】的正面对照需要"手里有牌"：ME 拿一张手牌（其余条件由合成帧给）。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hp=3, hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(),
        ],
        processing=(_PROBE_PROCESSING_SLASH,),
    )


def _shen_zhugeliang_probe_state() -> GameState:
    """神诸葛亮的正面对照：ME 有手牌，且武将牌上有一个**非空的「星」区**（区按实例键建）。

    【七星】② 的条件要"有星也有手牌"；【狂风】/【大雾】的条件只要"这一步是问我的"。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hp=3, hand=(_PROBE_SOURCE_CARD,)), SeatSpec()],
    )
    from sgs.skills.catalog import hero_card_zone_key

    star_cards = list(cards_of_type(PACK, "peach")[1:3])
    key = hero_card_zone_key(ME, "hero.shen_zhugeliang.qixing")
    state.player(ME).hero_cards[key] = HeroCardZone(skill_id="hero.shen_zhugeliang.qixing", owner=ME, cards=star_cards)
    return state


def _changyi_probe_state() -> GameState:
    """【倡义】的正面对照：**双方都有手牌**，且"这一次的回合角色"是座位 2。

    ``PHASE_PLAY_END`` 那条钩子的条件要"回合角色 ≠ 持有者"，故帧的 ``source`` = 座位 2：
    持有者是 ME 时条件成立；把持有者换成座位 2 就变成"自己回合的出牌阶段结束时"⇒ 假。
    """
    hand = tuple(cards_of_type(PACK, "dodge")[:2])
    return build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hand=(hand[0],)), SeatSpec(hand=(hand[1],))],
    )


def _congjian_probe_state() -> GameState:
    """【从谏】的正面对照：ME 手里有牌（"付得起"这一半），另有两名存活角色当"其他目标"。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hand=(_PROBE_TRICK_CARD,)),
            SeatSpec(),
        ],
        processing=(_PROBE_PROCESSING_SLASH,),
    )


def _zhengku_probe_state() -> GameState:
    """【镇骨】的正面对照：座位 1（ME）身上有一枚**来源是 ME** 的「镇骨」欠账。

    结算钩子的条件读的是标记记账（"这笔欠账是不是来源是我的"）——持有者换成座位 2 之后，
    场上没有任何"来源是座位 2"的欠账 ⇒ 条件必须翻假。
    """
    state = _probe_state()
    state.player(ME).markers = (
        Marker(
            name="镇骨",
            source_seat=ME,
            source_skill_id="hero.haozhao.zhengku",
            count=2,
            clear_on_source_death=True,
        ),
    )
    return state


def _hujia_probe_state() -> GameState:
    """界【护驾】②的正面对照：ME（座位 1）与【闪】的使用者（座位 0）都是**魏**势力。

    座位 2 没有势力 ⇒ ``three`` 的负面对照里"持有者换成别人"必然不同势力，条件恒假
    （这正是"关于我"的判据：把持有者挪走，②就不该成立）。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(kingdom=Kingdom.WEI),
            SeatSpec(hp=3, kingdom=Kingdom.WEI),
            SeatSpec(),
        ],
    )


def _jiuyuan_probe_state() -> GameState:
    """界【救援】的正面对照：ME（座位 1）是**主公**，正在回复的吴势力角色是座位 0。

    ME 与座位 0 同属吴；座位 0 在自己的回合（``turn_seat = 0``）且体力 3 ≥ ME 的 2。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, kingdom=Kingdom.WU),
            SeatSpec(hp=2, kingdom=Kingdom.WU),
            SeatSpec(hp=4),
        ],
        turn_seat=0,
    )
    return state


def _zhaxiang_probe_state() -> GameState:
    """界【诈降】的正面对照：ME 的"本阶段"效果已生效、渠道牌是**红色**【杀】。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(_PROBE_SOURCE_CARD,)), SeatSpec(hp=3), SeatSpec()],
        processing=(_PROBE_PROCESSING_SLASH,),
    )
    state.player(ME).zhaxiang_active = True
    return state


def _liegong_probe_state() -> GameState:
    """【烈弓】的正面对照：ME（座位 1，体力 3）在自己的**出牌阶段**，目标座位 0 只有 1 张手牌。

    1 ≥ 3 不成立、1 ≤ 攻击范围 1 成立 ⇒ 条件为真；把持有者换成别人后 ``source != owner``，
    条件必须为假（"关于我"的判据）。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(_PROBE_SOURCE_CARD,)),
            SeatSpec(hp=3),
            SeatSpec(),
        ],
        turn_seat=ME,
        phase=Phase.PLAY,
    )


def _jijiang_probe_state() -> GameState:
    """界【激将】②的正面对照：ME（座位 1）与【杀】的打出者（座位 0）都是**蜀**势力。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(kingdom=Kingdom.SHU),
            SeatSpec(hp=3, kingdom=Kingdom.SHU),
            SeatSpec(),
        ],
    )


def _dingluan_probe_state() -> GameState:
    """【定乱】的正面对照：持有者（ME）的**武将牌上真有**这条技能，且场上有可展示的牌。

    票 07 起【定乱】非共享 ⇒ 条件自校"``MATCH_OWNER_KEY`` 是定乱实例的持有者"；ROUND_START
    帧不带"这一次问谁"这个事实，故正 / 反两侧只由检查改写 ``MATCH_OWNER_KEY`` 来翻。
    """
    state = _probe_state()
    state.player(ME).hero = "feng_yujin"
    return state


def _haoshi_probe_state() -> GameState:
    """【好施】的正面对照：条件 = **我的**摸牌阶段（阶段体帧的 ``source`` 是我）。默认探针
    局面没有回合角色（``turn_seat = None``），故另给一份"座位 1 = ME 是当前回合角色"的局面。"""
    return build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hp=3), SeatSpec()],
        turn_seat=ME,
        phase=Phase.DRAW,
    )


def _jianying_probe_state() -> GameState:
    """【渐营】的正面对照：【渐营】的条件读**座位事实**（本阶段上一张使用的牌），故合成
    局面必须①是 **ME 自己的出牌阶段**、②``play_phase_previous_card`` 已落一张与帧载荷
    同花色的牌（默认探针局面两者都不成立）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hp=3), SeatSpec()],
        turn_seat=ME,
        phase=Phase.PLAY,
    )
    state.player(ME).play_phase_previous_card = state.card(_PROBE_CHANNEL_SLASH)
    return state


def _probe_state_for(skill_id: str) -> GameState:
    """逐技能的正面对照局面（默认同一份；条件里有额外状态前提的单独给）。"""
    if skill_id == "hero.jie_liubei.jijiang":
        return _jijiang_probe_state()
    if skill_id == "hero.jie_lvmeng.qinxue":
        return _qinxue_probe_state()
    if skill_id == "hero.jie_luxun.qianxun":
        return _qianxun_probe_state()
    if skill_id == "hero.jie_caocao.hujia":
        return _hujia_probe_state()
    if skill_id == "hero.jie_sunquan.jiuyuan":
        return _jiuyuan_probe_state()
    if skill_id == "hero.tao_liubei.congtao":
        return _congtao_probe_state()
    if skill_id == "hero.jushou.jianying":
        return _jianying_probe_state()
    if skill_id == "hero.lu_su_mobile.haoshi":
        return _haoshi_probe_state()
    if skill_id == "hero.huangzhong.liegong":
        return _liegong_probe_state()
    if skill_id == "hero.jie_huangzhong_mobile.liegong":
        # 界烈弓的两条条件钩子：① 触发（"我用【杀】指定了目标"）；② RESOLVE_AFTER_END 清扫
        # （条件读座位上的「本次使用」记账 ⇒ 正面局面里必须先记一笔）。
        state = _probe_state()
        state.player(ME).liegong_bonus = (_PROBE_CHANNEL_SLASH, (0,))
        return state
    if skill_id == "hero.jie_huanggai.zhaxiang":
        return _zhaxiang_probe_state()
    if skill_id == "hero.haozhao.zhengku":
        return _zhengku_probe_state()
    if skill_id == "hero.zhangxiu.congjian":
        return _congjian_probe_state()
    if skill_id == "hero.dongzhuo_mobile.bengkuai":
        return _bengkuai_probe_state()
    if skill_id == "hero.dongzhuo_mobile.baonue":
        return _baonue_probe_state()
    if skill_id == "hero.guyong.bingyi":
        return _bingyi_probe_state()
    if skill_id == "hero.tao_caocao.changyi":
        return _changyi_probe_state()
    if skill_id.startswith("hero.shen_zhugeliang."):
        return _shen_zhugeliang_probe_state()
    if skill_id == "hero.shen_ganning.jieying":
        # ②「营在我身上」、③「营在回合角色身上」⇒ 两枚标记各就各位（①的"场上无营"
        # 读局面、放在回调里，故不影响本对照）。
        state = _probe_state()
        ying = Marker(
            name="营",
            source_seat=ME,
            source_skill_id="hero.shen_ganning.jieying",
            count=1,
            effect="hero.shen_ganning.jieying_mark",
        )
        state.player(ME).markers = (ying,)
        state.player(0).markers = (ying,)
        return state
    if skill_id == "hero.feng_yujin.dingluan":
        return _dingluan_probe_state()
    if skill_id == "hero.lukang_mobile.poshi":
        # 正面对照：ME 体力 1（条件「或体力值为1」成立；装备栏不必全封）。
        return build_state(
            pack=PACK,
            seats=[SeatSpec(hand=(_PROBE_SOURCE_CARD,)), SeatSpec(hp=1), SeatSpec()],
            processing=(_PROBE_PROCESSING_SLASH,),
        )
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
    # 票 23：【枭姬】逐张触发，触发次数 = 这批牌的张数，故探针必须带一张真牌面
    # （空批是构造不出来的局面：``move_card`` 至少要搬一张）。
    move_xiaoji = MoveEvent(
        cards=(
            CardRef(card_id=0, type="crossbow", name="诸葛连弩", suit="club", rank=1),
        ),
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
    # M6 票 10：界陆逊的两条条件钩子（【谦逊】生效时唯一目标 + 【连营】失去最后手牌）。
    move_lianying_jie = MoveEvent(
        cards=(
            CardRef(card_id=0, type="slash", name="杀", suit="spade", rank=7),
        ),
        from_zone=ZoneRef(Zone.HAND, ME),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=ME,
        after_holder=None,
        actor=ME,
    )
    return {
        # 每条技能一个**元组**：本技能的每条带条件钩子各一帧（【勤学】两个时机两条）。
        "hero.jie_luxun.qianxun": (
            _HookFrame(
                card=state.card(_PROBE_TRICK_CARD),
                targets=(ME,),
                cursor=0,
                source=0,
            ),
        ),
        "hero.jie_luxun.lianying": (
            _HookFrame(**{MOVE_EVENT_KEY: move_lianying_jie}),
        ),
        "hero.caocao.jianxiong": (
            _HookFrame(target=ME, source=0, card=state.card(_PROBE_PROCESSING_SLASH)),
        ),
        "hero.simayi.fankui": (_HookFrame(target=ME, source=0),),
        "hero.guojia.yiji": (_HookFrame(target=ME),),
        # jushou-lusu 票 02【好施】：阶段体帧的 source 是我（我的摸牌阶段）。
        "hero.lu_su_mobile.haoshi": (_HookFrame(source=ME),),
        # jushou-lusu 票 01【渐营】：使用牌帧的 source 是我、且这道条件还要读座位事实
        # （本阶段上一张牌）——正面局面见 :func:`_jianying_probe_state`。
        "hero.jushou.jianying": (
            _HookFrame(source=ME, card=state.card(_PROBE_CHANNEL_SLASH)),
        ),
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
        # huo-mobile-batch 票 02：庞统移动版【涅槃】——濒死的是我（NEAR_DEATH 帧的 target）。
        "hero.pangtong_mobile.niepan": (_HookFrame(target=ME),),
        # lukang-mobile 票 01：陆抗移动版【破势】——**我的**准备阶段（帧的 source 是我）。
        "hero.lukang_mobile.poshi": (_HookFrame(source=ME),),
        # huo-taishici-xunyu 票 02：荀彧【节命】——受伤的是我（逐点触发的 B 族同形）。
        "hero.xunyu.jieming": (_HookFrame(target=ME),),
        # huo-shenzhouyu-mobile 票 01/03：神周瑜移动版【琴音】——帧的 source 是我（我的
        # 弃牌阶段）、本阶段弃置了 2 张手牌。
        "hero.shen_zhouyu_mobile.qinyin": (
            _HookFrame(source=ME, discard_phase_hand_discards=2),
        ),
        "equip.silver_lion": (_HookFrame(**{MOVE_EVENT_KEY: move_silver_lion}),),
        # M6 票 08：界【集智】的正面帧与标准版同形（使用者是我 + 非延时类锦囊），但两版
        # **条件已按牌面分开**（标准版排除延时类、界版排除转化牌；M6 评审订正），此处只核
        # "关于我"；
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
        # 神诸葛亮（huo-shenzhugeliang 票 01）：三条都是"这一步是问我的"（阶段体帧的
        # source = 当前回合角色），【七星】另有"有星也有手牌"的状态前提。
        "hero.shen_zhugeliang.qixing": (_HookFrame(source=ME),),
        # 狂风 / 大雾 各有**两条**带条件钩子：结束阶段（自己的）与到期清扫（自己的回合开始）。
        "hero.shen_zhugeliang.kuangfeng": (_HookFrame(source=ME), _HookFrame(source=ME)),
        "hero.shen_zhugeliang.dawu": (_HookFrame(source=ME), _HookFrame(source=ME)),
        # 自制包·韬·刘备自制版【从讨】：这一次成为目标的是座位 0（在 ME 的攻击范围内，
        # 但**不在**座位 2 的攻击范围内 ⇒ 持有者换人后条件必须翻假）。逐目标的"这一次"
        # 由派发环写进 MATCH_ACTOR_KEY，持有者由检查本身改写。
        # M6 票 14：界·魏 A 的三条条件钩子。
        # - 界【奸雄】：与标准版同一条条件（受伤的是我 + 渠道牌还在处理区）；
        # - 界【反馈】：与标准版同一条条件（受伤的是我 + 来源有牌）；
        # - 界【护驾】②：两条挂接（使用 / 打出【闪】）共用同一条条件——我 = 主公，
        #   【闪】的使用者是座位 0（同为魏势力、不是当前回合角色）。
        "hero.jie_caocao.jianxiong": (
            _HookFrame(target=ME, source=0, card=state.card(_PROBE_PROCESSING_SLASH)),
        ),
        "hero.jie_simayi.fankui": (_HookFrame(target=ME, source=0),),
        "hero.tao_liubei.congtao": (
            _HookFrame(
                **{MATCH_ACTOR_KEY: 0},
                card=state.card(_PROBE_CHANNEL_SLASH),
                targets=(0,),
                source=0,
            ),
        ),
        # lei-batch 票 01：郝昭【镇骨】——两条条件钩子都自校"这一步的回合角色是谁"：
        # 发起 = 我的结束阶段；结算 = 我 / 目标的回合末（记账在标记上：持有者换人后，
        # 场上没有"来源是他"的欠账 ⇒ 必须翻假）。
        "hero.haozhao.zhengku": (
            _HookFrame(source=ME),
            _HookFrame(source=ME),
        ),
        # lei-batch 票 02：张绣【从谏】——条件自校"这一步问的是我"（MATCH_OWNER_KEY == actor），
        # 牌是一张**普通锦囊**（过河拆桥，非延时）、目标两个（含我）。
        "hero.zhangxiu.congjian": (
            _HookFrame(
                **{MATCH_ACTOR_KEY: ME},
                source=0,
                targets=(ME, 0),
                card=state.card(_PROBE_TRICK_CARD),
            ),
        ),
        # lei-batch 票 03：诸葛瞻【罪论】（自己的结束阶段）/【父荫】（这一次的目标是我，
        # 牌是一张【杀】；正面对照的手牌数 0 ≤ 使用者 1）。
        "hero.zhugezhan.zuilun": (_HookFrame(source=ME),),
        # 自制包·韬·曹操：两条技能的**四条**带条件钩子。
        # - 【鸿志】的 ①/② 都自校"这一回合是我的"，② 另外要"场上没有【倡义】"（合成局面
        #   没有武将牌载体 ⇒ 场上确实没有，见 ``_probe_state``）；
        # - 【倡义】的触发窗口自校"回合角色不是我"（帧 source = 座位 2），
        #   段 2（点数 +X）自校"这一次拼点里有我"（发起者 = ME）。
        "hero.tao_caocao.hongzhi": (
            _HookFrame(source=ME),
            _HookFrame(source=ME),
        ),
        "hero.tao_caocao.changyi": (
            _HookFrame(source=OTHER),
            _HookFrame(initiator=ME, targets=(0,)),
        ),
        "hero.zhugezhan.fuyin": (
            _HookFrame(
                **{MATCH_ACTOR_KEY: ME},
                source=0,
                card=state.card(_PROBE_CHANNEL_SLASH),
            ),
        ),
        "hero.jie_caocao.hujia": (
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=0),
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=0),
        ),
        # feng-zhangjiao 票 01：张角移动版【雷击】= 使用 / 打出两条同条件钩子，
        # 「我是谁」读 `source`（那张【闪】的使用者）、牌面必须是一张【闪】。
        "hero.zhangjiao_mobile.leiji": (
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=ME),
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=ME),
        ),
        # jie-feng-batch2 票 02：界张角移动版【雷击】= 同形的两条条件钩子。
        "hero.jie_zhangjiao_mobile.leiji": (
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=ME),
            _HookFrame(card=state.card(_PROBE_DODGE_CARD), source=ME),
        ),
        # M6 票 15：界·魏 B 的两条条件钩子。
        # - 界【遗计】：与标准版**同一条**条件（受伤的是我）；
        # - 界【忘隙】：两条挂接各一条条件，且**两侧的"我是谁"相反**——
        #   造成侧：我 = 来源（座位 ME）、受伤者是座位 0；受到侧：我 = 受伤者、来源是座位 0。
        "hero.jie_guojia.yiji": (_HookFrame(target=ME),),
        "hero.jie_lidian.wangxi": (
            _HookFrame(target=0, source=ME),
            _HookFrame(target=ME, source=0),
        ),
        # M6 票 16：界·蜀的两条条件钩子。
        # - 界【激将】②：两条挂接（使用 / 打出【杀】）共用同一条条件——我 = 主公，
        #   【杀】的打出者是座位 0（同为蜀势力、不是当前回合角色）。
        # - 界【涯角】：使用 / 打出的牌是**手牌**、且不是我自己的回合（两段挂接同一条条件）。
        "hero.jie_liubei.jijiang": (
            _HookFrame(card=state.card(_PROBE_CHANNEL_SLASH), source=0),
            _HookFrame(card=state.card(_PROBE_CHANNEL_SLASH), source=0),
        ),
        "hero.jie_zhaoyun.yajiao": (
            _HookFrame(card=state.card(_PROBE_CHANNEL_SLASH), source=ME, from_hand=True),
            _HookFrame(card=state.card(_PROBE_CHANNEL_SLASH), source=ME, from_hand=True),
        ),
        # M6 票 17：界·吴的四条条件钩子（界流离 / 界枭姬各复用标准版同一条条件）。
        # - 界【救援】：ME = 主公（技能持有者），帧目标 = 正在回复的座位 0；
        # - 界【流离】：与标准【流离】同一条条件（本次【杀】的目标是 ME）；
        # - 界【枭姬】：与标准【枭姬】同一条条件（ME 失去装备区里的一张牌）；
        # - 界【诈降】：两条挂接——失去体力的是 ME（LIFE_DECREASE_AFTER）/ 红色【杀】的
        #   使用者是 ME 且本阶段效果已生效（USE_AFTER_DESIGNATE_TARGET）。
        "hero.jie_sunquan.jiuyuan": (_HookFrame(targets=(0,)),),
        # 票 03：风包【烈弓】——自己的出牌阶段使用【杀】指定了座位 0（其 1 张手牌 ≤ 攻击范围 1）。
        "hero.huangzhong.liegong": (
            _HookFrame(
                source=ME,
                card=state.card(_PROBE_CHANNEL_SLASH),
                targets=(0,),
            ),
        ),
        "hero.jie_daqiao.liuli": (
            _HookFrame(
                source=0, actor=ME, targets=(ME,), card=state.card(_PROBE_CHANNEL_SLASH)
            ),
        ),
        "hero.jie_sunshangxiang.xiaoji": (_HookFrame(**{MOVE_EVENT_KEY: move_xiaoji}),),
        "hero.jie_huanggai.zhaxiang": (
            _HookFrame(target=ME, amount=1),
            _HookFrame(source=ME, card=state.card(_PROBE_CHANNEL_SLASH)),
        ),
        # M6 票 18：界·群的三条条件钩子。
        # - 界【利驭】/ 界【趫猛】：我用【杀】对座位 0（有牌可拿）造成伤害；
        # - 界【耀武】：受伤的是我，渠道牌是一张【杀】。
        "hero.jie_lvbu.liyu": (
            _HookFrame(
                source=ME, target=0, card=state.card(_PROBE_CHANNEL_SLASH)
            ),
        ),
        "hero.jie_gongsunzan.qiaomeng": (
            _HookFrame(
                source=ME, target=0, card=state.card(_PROBE_CHANNEL_SLASH)
            ),
        ),
        "hero.jie_huaxiong.yaowu": (
            _HookFrame(target=ME, card=state.card(_PROBE_CHANNEL_SLASH)),
        ),
        # 风包魏延【狂骨】（feng-weiyan 票 01）：两条钩子共用"这次伤害由我造成"这条判据——
        # ① 受伤害时定格距离；② 造成伤害后逐点回复（探针帧因此带上定格值）。
        "hero.weiyan.kuanggu": (
            _HookFrame(source=ME, target=0),
            _HookFrame(source=ME, target=0, kuanggu_distance=1),
        ),
        # jie-feng-batch 票 01：界黄忠移动版【烈弓】的两条条件钩子——
        # ① 使用【杀】指定目标后（我 = 使用者）；② RESOLVE_AFTER_END 清扫（我 = 记账的座位）。
        "hero.jie_huangzhong_mobile.liegong": (
            _HookFrame(
                source=ME,
                card=state.card(_PROBE_CHANNEL_SLASH),
                targets=(0,),
            ),
            _HookFrame(source=ME, card=state.card(_PROBE_CHANNEL_SLASH)),
        ),
        # jie-feng-batch 票 02：界魏延移动版【狂骨】的两条钩子——① 受伤害时定格距离；
        # ② 造成伤害后逐点询问（探针帧带上定格值 1）。
        "hero.jie_weiyan_mobile.kuanggu": (
            _HookFrame(source=ME, target=0),
            _HookFrame(source=ME, target=0, jie_kuanggu_mobile_distance=1),
        ),
        # 票 04 / 票 06：神关羽【武魂】的两条条件（逐点受伤后 / 死亡时）；朱雀羽扇的新触发
        # 条件（自己使用普通【杀】）。探针的"我"分别是受伤者 / 死者 / 使用者。
        "hero.shen_guanyu_mobile.wuhun": (
            _HookFrame(target=ME, source=0),
            _HookFrame(target=ME),
        ),
        "equip.vermilion_feather_fan": (
            _HookFrame(source=ME, card=state.card(_PROBE_CHANNEL_SLASH)),
        ),
        # lei-shenganning 票 01：神甘宁【劫营】的三条钩子各一条"关于我"的判据——
        # ① 我的回合开始；② 我的结束阶段；③ 别人的回合结束时（回合角色带着**我放的**营）。
        "hero.shen_ganning.jieying": (
            _HookFrame(source=ME),
            _HookFrame(source=ME),
            _HookFrame(source=0),
        ),
        # 「营」的**标记自身效果**：只有持有者自己的摸牌阶段才多摸一张。
        "hero.shen_ganning.jieying_mark": (_HookFrame(source=ME),),
        # feng-yujin 票 07：【定乱】从共享技改成**非共享**（B2）⇒ 条件自校持有者
        # （`MATCH_OWNER_KEY` 必须真是定乱实例的持有者）。ROUND_START 帧没有"这一次问谁"
        # 这个事实（非共享技不写 `ASK_SEAT_KEY`），故探针帧只靠检查本身改写的
        # `MATCH_OWNER_KEY` 判正 / 反两侧。
        "hero.feng_yujin.dingluan": (_HookFrame(),),
        # lin-batch 票 01：神曹操移动版【归心】——B 族同一条 `_hurt_owner` 判据（受伤的是
        # 我）。**Q1 = B** 只影响"全场无牌也照发问"，不影响"谁挨打"这一道自校。
        "hero.shen_caocao_mobile.guixin": (_HookFrame(target=ME),),
        # lin-batch 票 02：孙坚移动版【英魂】——**我的**准备阶段（帧的 source 是我）∧ 我已
        # 受伤（基础局面 ME 3/4）∧ 场上有其他存活角色（三座）。
        "hero.sunjian_mobile.yinghun": (_HookFrame(source=ME),),
        # lin-batch2 票 01：曹丕移动版【放逐】——B 族同一条 `_hurt_owner`（受伤的是我）∧
        # 场上有其他存活角色（三座，ME = 座位 1 ⇒ 池 = {0, 2} 非空）。
        "hero.caopi_mobile.fangzhu": (_HookFrame(target=ME, source=0),),
        # lin-batch2 票 02：董卓移动版的两条带条件钩子。
        # - 【崩坏】：**我的**结束阶段（阶段体帧的 source = 我）∧ 场上有体力值**严格更低**的
        #   存活角色（探针局面：ME 3 / 座位 2 = 2）；持有者换成座位 2 后 source 对不上。
        # - 【暴虐】：伤害来源是座位 0（群势力、不是我）∧ 与我同势力（探针局面 ME = 群、
        #   座位 2 = 魏 ⇒ 持有者换成别人后判据翻假）。
        "hero.dongzhuo_mobile.bengkuai": (_HookFrame(source=ME),),
        "hero.dongzhuo_mobile.baonue": (_HookFrame(source=0),),
        # yijiang-2014 票 09：顾雍【秉壹】——**我的**结束阶段（阶段体帧的 source = 我）∧
        # 我有手牌（0 张不给入口，Q3 = A）。正面局面见 :func:`_bingyi_probe_state`；
        # 持有者换成座位 2 后 source 与持有者对不上 ⇒ 条件翻假。
        "hero.guyong.bingyi": (_HookFrame(source=ME),),
    }


EXEMPT_OWNER_SELF_CHECK: dict[str, str] = {
    "hero.jie_ganning.fenwei": (
        "M6 票 07：触发条件关于**这张牌**（锦囊 ∧ ≥2 个目标），不关于持有者——"
        "任何角色使用的多目标锦囊都要问界甘宁；没有『我』可自校（与『关于我』的钩子正交）"
    ),
    "hero.feng_yujin.dingluan_mark": (
        "feng-yujin 票 01：【定乱】牌的**标签自身效果**——触发条件关于『这批牌身上有没有我的"
        "标签』（MoveEvent + 牌标签），『拥有者』由移动的来源区回答；没有『我』可自校"
    ),
    "hero.caopi_mobile.xingshang": (
        "lin-batch2 票 01（Q6 = A）【行殇】：「当**其他角色**死亡时」是关于**别人**的死——"
        "条件写的是『死者 ≠ 我自己』（MATCH_OWNER_KEY 对 target），把持有者换成别人后"
        "仍然成立；这正是豁免表存在的形态（与【奋威】/【定乱】同一类『不关于我』）"
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
    assert set(EXEMPT_OWNER_SELF_CHECK) == {
        "hero.jie_ganning.fenwei",
        "hero.feng_yujin.dingluan_mark",
        "hero.caopi_mobile.xingshang",
    }, "豁免表增删必须同步本条（只许登记经过裁定的『不关于我』条件）"
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
    """19 条零步技能（M6 票 01 加【裸衣】、票 02 加【集智】、票 03 加【克己】/【苦肉】、
    票 06 加界【克己】、票 07 加界【闭月】、票 10 加界【谦逊】、票 11 加界【博图】、
    票 14 加界【奸雄】/界【裸衣】）：
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
    # huo-taishici-xunyu 票 01：太史慈【天义】是"选目标 → 拼点 → 落位"的主动技
    "hero.taishici.tianyi": "主动技能",
    # huo-taishici-xunyu 票 02：荀彧【驱虎】= 主动技；【节命】= 逐点触发的钩子 + 座位步
    "hero.xunyu.quhu": "主动技能",
    "hero.xunyu.jieming": "触发技能",
    # M6 票 08：界【反间】是两步主动技（手牌 → 目标；提交点里目标二选一）
    "hero.jie_zhouyu.fanjian": "主动技能",
    # M6 票 16：界【仁德】是两步主动技（给任意张 → 本阶段未给过的角色），另有
    # 提交点里的"视为使用一张基本牌"后续询问。
    "hero.jie_liubei.rende": "主动技能",
    # M6 票 10：界【谦逊】= 零步（生效时把所有手牌置于武将牌上）
    "hero.jie_luxun.qianxun": "零步技能",
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
    # M6 票 14：界【反馈】共用【反馈】的一步流水线；界【突袭】是阶段体 + 座位步
    "hero.jie_simayi.fankui": "触发技能",
    "hero.jie_zhangliao.tuxi": "触发技能",
    "equip.green_dragon_blade": "触发技能",
    "equip.axe": "触发技能",
    "equip.qilin_bow": "触发技能",
    # feng-xiahouyuan 票 01：夏侯渊【神速】= 帧外时机（回合开始时）钩子 + 一步选项
    "hero.xiahouyuan.shensu": "触发技能",
    # jie-feng-batch2 票 01：界版是**零步流水线**（多选面住在 effect 里），故落「零步技能」格。
    "hero.jie_xiahouyuan_mobile.shensu": "零步技能",
    # jie-feng-batch2 票 02：界张角三条（雷击 = 触发；鬼道 = 能力位；黄天 = 主动）。
    "hero.jie_zhangjiao_mobile.leiji": "触发技能",
    "hero.jie_zhangjiao_mobile.guidao": "能力位",
    "hero.jie_zhangjiao_mobile.huangtian": "主动技能",
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
    # M6 票 11：界【博图】是**回合结束时**的零步技（压一个额外回合进待执行栈）
    "hero.jie_lvmeng.botu": "零步技能",
    # M6 票 14：界【奸雄】（拿牌 + 摸一张）与界【裸衣】（亮三张 → 拿 → 跨回合 +1）零步
    "hero.jie_caocao.jianxiong": "零步技能",
    "hero.jie_xuchu.luoyi": "零步技能",
    # M6 票 15：界【天妒】与标准版共用同一条零步流水线；界【忘隙】是手写回调
    #（摸两张 → 从这两张里挑一张交给该角色），按"手写钩子"归「多轮技能」格。
    "hero.jie_guojia.tiandu": "零步技能",
    "hero.jie_lidian.wangxi": "多轮技能",
    # M6 票 16：界【涯角】是手写的"亮牌 → 类别分叉 → 给谁 / 弃谁的"多轮（无 SkillFlow）。
    "hero.jie_zhaoyun.yajiao": "多轮技能",
    # 排列技能 2（deck_arrange + Arrange 步；M6 票 15 加固定切分 2 / 2 的【恂恂】）
    "hero.zhugeliang.guanxing": "排列技能",
    "hero.jie_lidian.xunxun": "排列技能",
    # 火包 OL：七星用两行交换的排列面（deck_arrange + Arrange 步），狂风 / 大雾是带流水线
    # 的触发技（阶段体时机 + 两条强制钩子）。
    "hero.shen_zhugeliang.qixing": "排列技能",
    "hero.shen_zhugeliang.kuangfeng": "触发技能",
    "hero.shen_zhugeliang.dawu": "触发技能",
    # M6 票 16：界【观星】张数按存活数 3 / 5，且准备 / 结束两个时机各挂一条同名的排列流。
    "hero.jie_zhugeliang.guanxing": "排列技能",
    # M6 票 17：界·吴。界制衡（单步清单）/ 界苦肉（一步成本）/ 界结姻（目标步 + 提交点手写
    # 询问）都是主动技能；界流离（钩子 + 两步清单）是触发技能；界救援 / 界国色 / 界枭姬
    # 是零步技能；界诈降是锁定技（两条钩子 + 两个修饰器）。
    "hero.jie_sunquan.zhiheng": "主动技能",
    "hero.jie_sunquan.jiuyuan": "零步技能",
    "hero.jie_huanggai.kurou": "主动技能",
    "hero.jie_huanggai.zhaxiang": "锁定技",
    "hero.jie_daqiao.guose": "零步技能",
    "hero.jie_daqiao.liuli": "触发技能",
    "hero.jie_sunshangxiang.jieyin": "主动技能",
    "hero.jie_sunshangxiang.xiaoji": "零步技能",
    # M6 票 18：界华佗【除疠】（座位步 + 提交点里逐名选牌）/ 界华雄【势斩】（一步座位 +
    # 次数由提交点自己数）都是主动技能；界吕布【利驭】/ 界公孙瓒【趫猛】是"钩子 + 一步
    # 选牌流水线"的触发技能；界吕布【无双】/ 界华雄【耀武】/ 界公孙瓒【义从】是锁定技；
    # 界华佗【急救】与标准版逐字相同（同一条转化声明）。
    "hero.jie_huatuo.chuli": "主动技能",
    "hero.jie_huaxiong.shizhan": "主动技能",
    "hero.jie_lvbu.liyu": "触发技能",
    "hero.jie_gongsunzan.qiaomeng": "触发技能",
    "hero.jie_lvbu.wushuang": "锁定技",
    "hero.jie_huaxiong.yaowu": "锁定技",
    # 风包魏延【狂骨】（feng-weiyan 票 01）：锁定 + 造伤后逐点 + 手写回调（无 SkillFlow）。
    "hero.weiyan.kuanggu": "锁定技",
    "hero.jie_gongsunzan.yicong": "锁定技",
    "hero.jie_huatuo.jijiu": "转化技",
    # 多轮技能 12（手写帧内游标循环；flow_for is None；M6 票 01 的【刚烈】= 判定 → 选项 → 选牌，
    # 票 02 的【洛神】= 可重复判定循环；票 15 的界【遗计】= 摸两张 → 逐张选择收牌人）
    "hero.guojia.yiji": "多轮技能",
    "hero.jie_guojia.yiji": "多轮技能",
    "hero.xiahoudun.ganglie": "多轮技能",
    # M6 票 08：界【刚烈】是判定 → 红 / 黑分叉的手写多轮（与标准版整句重写）
    "hero.jie_xiahoudun.ganglie": "多轮技能",
    "hero.zhenji.luoshen": "多轮技能",
    # M6 票 09：界【洛神】与标准版共用同一条手写多轮循环（差别只在获得牌的标签）
    "hero.jie_zhenji.luoshen": "多轮技能",
    "hero.jie_machao.tieqi": "多轮技能",
    # M6 票 10：界【连营】= 手写的逐座选人循环（X 名角色各摸一张）
    "hero.jie_luxun.lianying": "多轮技能",
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
    # huo-wolong-yanliang 票 01：火包·OL 卧龙诸葛
    #   八阵 = 锁定技（"视为装备"是连续型条件，只往别人的【闪】窗口补候选，没有询问）；
    #   火计 / 看破 = 转化声明（单源、`CANDIDATES` 注入，没有"是否发动"那一问）。
    "hero.wolong_zhugeliang.bazhen": "锁定技",
    "hero.wolong_zhugeliang.huoji": "转化技",
    "hero.wolong_zhugeliang.kanpo": "转化技",
    # 双雄 = 阶段体（`PHASE_DRAW`）+ 一条 `CANDIDATES`（本回合的颜色转化）；机械判据按
    # "声明了 `virtual_type_ids`"落「转化技」格（与【连环】同一姿态：转化那一半与钩子共存）。
    "hero.yanliang_wenchou.shuangxiong": "转化技",
    # lin-batch 票 03：神话再临·林包·移动版徐晃【断粮】= 单源转化（`CANDIDATES`）+ ② 的
    # `DISTANCE_LIMIT` 修饰器（额定距离 1 → 2，同一张武将牌上的连续型效果）。
    "hero.xuhuang_mobile.duanliang": "转化技",
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
    # M6 票 16：界【空城】与标准版逐字相同（同一个目标池谓词）。
    "hero.jie_zhugeliang.kongcheng": "锁定技",
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
    # M6 票 16：界【龙胆】沿用标准版两条声明并追加 酒↔桃 两向（同一技能 id）。
    "hero.jie_zhaoyun.longdan": "转化技",
    "hero.daqiao.guose": "转化技",
    "hero.huatuo.jijiu": "转化技",
    "hero.zhenji.qingguo": "转化技",
    "hero.ganning.qixi": "转化技",
    # M6 票 09：界【倾国】与标准版逐字相同（同一条 ConversionSkill 声明）
    "hero.jie_zhenji.qingguo": "转化技",
    "hero.jie_guanyu.wusheng": "转化技",
    "equip.serpent_spear": "转化技",
    "equip.vermilion_feather_fan": "零步技能",
    # 票 04：神关羽【武神】= 锁定转化（红桃手牌当普通【杀】）；【武魂】= 锁定触发（标记 / 死亡时）
    "hero.shen_guanyu_mobile.wushen": "转化技",
    "hero.shen_guanyu_mobile.wuhun": "锁定技",
    # 票 01：神吕蒙移动版【涉猎】= 阶段体零步（亮五张 + 手写选牌）；【攻心】= 主动技
    # （与界吕蒙 ☆攻心共用同一条流水线）
    "hero.shen_lvmeng_mobile.shelie": "零步技能",
    "hero.shen_lvmeng_mobile.gongxin": "主动技能",
    # 自制包·韬·刘备自制版【义起】= 主动技能（A 族第一条限定技；观看步 + 类型步）
    "hero.tao_liubei.yiqi": "主动技能",
    # 能力位 2（rejudge_capable，没有对持有者的询问）；M6 票 14 加界【鬼才】
    "hero.simayi.guicai": "能力位",
    # feng-zhangjiao 票 01：张角移动版 = 触发技（雷击）/ 能力位（鬼道）/ 主动技能（黄天，
    # 主公技的跨座位注入 —— 候选进**别座位**的出牌阶段）
    "hero.zhangjiao_mobile.leiji": "触发技能",
    "hero.zhangjiao_mobile.guidao": "能力位",
    "hero.zhangjiao_mobile.huangtian": "主动技能",
    # huo-dianwei-mobile 票 01：火包移动版典韦【强袭】= 一步选项步 + 手写主体（主动技能格）
    "hero.dianwei_mobile.qiangxi": "主动技能",
    # huo-mobile-batch 票 01：火包移动版庞德【马术】= 纯修饰器（锁定技格）；
    # 【猛进】= 一步选牌流水线 + 接缝（触发技能格）
    "hero.pangde_mobile.mashu": "锁定技",
    "hero.pangde_mobile.mengjin": "触发技能",
    # huo-mobile-batch 票 02：庞统【连环】= 单源转化（转化技格）；
    # 【涅槃】= 零步流水线（限定技 + 濒死钩子，按"零步技能"格）
    "hero.pangtong_mobile.lianhuan": "转化技",
    "hero.pangtong_mobile.niepan": "零步技能",
    # huo-mobile-batch 票 03：袁绍【乱击】= 两张同花色手牌的转化（转化技格）；
    # 【血裔】= 纯修饰器 + 锁定（锁定技格）
    "hero.yuanshao_mobile.luanji": "转化技",
    "hero.yuanshao_mobile.xueyi": "锁定技",
    "hero.jie_simayi.guicai": "能力位",
    # 本票：移动版界【曹仁】据守 = 阶段体零步（摸三张）+ 翻面，非锁定 ⇒ 零步技能格
    "hero.jie_caoren_mobile.jushou": "零步技能",
    # 票 03：风包黄忠【烈弓】= 零步流水线（只问「是否发动」）+ 逐目标帧标签
    "hero.huangzhong.liegong": "零步技能",
    # jie-feng-batch 票 01：界黄忠移动版【烈弓】= 零步流水线 + TARGET_POOL / 加伤修饰器
    "hero.jie_huangzhong_mobile.liegong": "零步技能",
    # jie-feng-batch 票 02：界魏延移动版【狂骨】= 手写钩子（逐点询问 + 二选一，无流��线）；
    # 【奇谋】= A 族限定技 + 单步选项（中文 X）⇒ 主动技能格。
    "hero.jie_weiyan_mobile.kuanggu": "多轮技能",
    "hero.jie_weiyan_mobile.qimou": "主动技能",
    # 自制包·韬·刘备自制版【从讨】= 手写钩子（逐目标 + 私密观看 + 二选一，无流水线）
    "hero.tao_liubei.congtao": "多轮技能",
    # wuming 票 01：无名自制版【天成】= L0（游戏开始时）锁定技：手写 3 次展示循环 +
    # 逐询问展示载荷（武将牌 / 置灰 / 具体 X）；``locked=True`` ⇒ 归「锁定技」格。
    "hero.wuming.tiancheng": "锁定技",
    # huo-shenzhouyu-mobile 票 01：神周瑜移动版【琴音】= 帧外时机（弃牌阶段结束时）+
    # 单步选项 + 手写主体（触发技能格）；【业炎】= 限定技 + 零步流水线 + 计数式分配弹窗
    # （主动技能格）。
    "hero.shen_zhouyu_mobile.qinyin": "触发技能",
    "hero.shen_zhouyu_mobile.yeyan": "零步技能",
    # lei-batch 票 01：郝昭【镇骨】= 结束阶段发起（一步选人流水线）+ 回合结束时强制结算，
    # 有 ``SkillFlow``、有钩子、``deck_arrange`` 为空 ⇒ 触发技能格。
    "hero.haozhao.zhengku": "触发技能",
    # 候选生成器 1（TARGET_POOL + TARGET_COUNT，从不发「是否发动」）
    "equip.halberd": "候选生成器",
    # 候选提供者 4（CANDIDATES + 势力求助窗口 / 引擎回退）；M6 票 14 加界【护驾】
    # （它另带②的两条零步钩子，但"候选提供者"是它在本表里的归类——见 zero_step 的排除）
    "hero.caocao.hujia": "候选提供者",
    "hero.jie_caocao.hujia": "候选提供者",
    "hero.liubei.jijiang": "候选提供者",
    # M6 票 16：界【激将】= ①求助窗口 + ②的两条零步钩子（按"提供者"单列，同界【护驾】）。
    "hero.jie_liubei.jijiang": "候选提供者",
    "equip.eight_diagrams": "候选提供者",
    # 风包·移动版于吉【蛊惑】：出牌阶段入口自 `04` 票 §3 起是**两步流水线**（选牌 → 声明，
    # 用户 2026-11-02 的 Q3A 裁定）⇒ 归「主动技能」格。它的**逐座位质疑**仍是段机里的手写
    # 循环（流水线语言没有循环步骤），但那一半住在 `SkillFlow.effect` 里，不是"手写钩子"
    # ⇒ 不进「多轮技能」格。原来的「手写主动技」一格因此**消失**（它唯一的成员就是这条）。
    "hero.yuji_mobile.guohuo": "主动技能",
    # lei-batch 票 02：张绣【雄乱】= A 族限定技 + 预发动（一步选人流水线、无钩子）⇒ 主动技能格；
    # 【从谏】= 成为目标时的钩子 + 两步流水线（选牌 → 座位）⇒ 触发技能格。
    "hero.zhangxiu.xiongluan": "主动技能",
    "hero.zhangxiu.congjian": "触发技能",
    # lei-batch 票 03：诸葛瞻【罪论】= 排列规格（deck_arrange）+ 一步流水线 ⇒ 排列技能格；
    # 【父荫】= 锁定技（不问发动、目标级无效）。
    "hero.zhugezhan.zuilun": "排列技能",
    "hero.zhugezhan.fuyin": "锁定技",
    # tao-caocao 票 01：自制包·韬·曹操【鸿志】= 锁定技（两条帧外时机钩子、不问发动、无流水线）；
    # 【倡义】= 帧外时机钩子 + 手写段机（拼点 → 授予 → 二选一，没有 ``SkillFlow``）⇒ 多轮技能格。
    "hero.tao_caocao.hongzhi": "锁定技",
    "hero.tao_caocao.changyi": "多轮技能",
    # lei-shenganning 票 01：神甘宁【魄袭】= A 族主动技 + 手写主体（观看 + 四色选牌）；
    # 【劫营】= 三条钩子 + 一步选座流水线（触发技能）；`jieying_mark` 是**标记自身效果**
    # 的定义（`carrier = MARKER`，手写钩子 + 两个修饰器、无流水线）⇒ 多轮技能格。
    "hero.shen_ganning.poxi": "主动技能",
    "hero.shen_ganning.jieying": "触发技能",
    "hero.shen_ganning.jieying_mark": "多轮技能",
    # feng-yujin 票 01：自制包·锋·于禁【节钺】= A 族主动技（两步流水线 + 手写二选一）；
    # 【定乱】= 帧外时机（ROUND_START）上的可选技（票 07 起**非共享**：只问持有者）、
    # 手写主体（无 SkillFlow）⇒ 多轮格；
    # 「【定乱】牌」的标签自身效果 = 手写 MOVE_AFTER 钩子（CarrierKind.CARD_TAG）⇒ 多轮格。
    "hero.feng_yujin.jieyue": "主动技能",
    "hero.feng_yujin.dingluan": "多轮技能",
    "hero.feng_yujin.dingluan_mark": "多轮技能",
    # lukang-mobile 票 01：陆抗移动版【决堰】= A 族主动技（单步选装备栏 + 三条本阶段/本回合
    # 效果）；☆【怀柔】= A 族主动技 + 预发动（选自己的 1 张装备牌 → 重铸）；
    # 【破势】= 觉醒技（准备阶段强制 + 零步流水线）。
    "hero.lukang_mobile.duanyan": "主动技能",
    "hero.lukang_mobile.huairou": "主动技能",
    "hero.lukang_mobile.poshi": "觉醒技",
    # lin-batch 票 01：神曹操移动版【归心】= 手写钩子（逐点 + 逐座位变长循环，无流水线）
    # ⇒ 多轮技能格；【飞影】= 纯修饰器 + 锁定（客体侧 DISTANCE +1）⇒ 锁定技格。
    "hero.shen_caocao_mobile.guixin": "多轮技能",
    "hero.shen_caocao_mobile.feiying": "锁定技",
    # lin-batch 票 02：孙坚移动版【英魂】= 准备阶段钩子 + 座位 / 选项两步流水线、主体手写
    # ⇒ 触发技能格（有 ``SkillFlow`` ⇒ 步骤语言内）。
    "hero.sunjian_mobile.yinghun": "触发技能",
    # lin-batch2 票 01：曹丕移动版【行殇】= `DEATH_ON` 钩子 + 零步流水线（主体手写搬牌）
    # ⇒ 零步技能格（`shapes.md` §2 第 44 行）；
    # 【放逐】= 受伤后钩子 + 单步选座流水线（触发技能格）；
    # 【颂威】= 判定族的零步流水线（主公技；闸门在 `ACTIVATION_PLANS` 的 `available` 里，
    # 与【天妒】同档）⇒ 零步技能格。
    "hero.caopi_mobile.xingshang": "零步技能",
    "hero.caopi_mobile.fangzhu": "触发技能",
    "hero.caopi_mobile.songwei": "零步技能",
    # lin-batch2 票 02：董卓移动版四条——【酒池】= 单源转化声明（步骤语言外）；
    # 【肉林】= 纯修饰器 + 锁定（锁定技格）；【崩坏】= 结束阶段钩子 + 单步选项 + 锁定
    # （按类型归「锁定技」格，与界【英姿】/【英魂】的互斥纪律同一姿态）；【暴虐】=
    # `DAMAGE_AFTER_DEAL` 零步流水线（零步技能格）。
    "hero.dongzhuo_mobile.jiuchi": "转化技",
    "hero.dongzhuo_mobile.roulin": "锁定技",
    "hero.dongzhuo_mobile.bengkuai": "锁定技",
    "hero.dongzhuo_mobile.baonue": "零步技能",
    # lin-batch2 票 03：贾诩移动版三条——【乱武】= A 族限定技 + **零步**流水线（手写逐座位
    # 循环全部住 `effect` 里，与【业炎】同形）⇒ 机械判据落在「零步技能」格（票面写的
    # 「主动技能 +1」是分析期的读法，机械谓词按 `_flow_steps == ()` 归零步格）；
    # 【完杀】= 纯修饰器（`CANDIDATES(PEACH)` 删候选、无流水线）与【帷幕】= 纯修饰器
    # （`TARGET_POOL` 剔除自己）⇒ 两条都按类型归「锁定技」格。
    "hero.jiaxu_mobile.wansha": "锁定技",
    "hero.jiaxu_mobile.luanwu": "零步技能",
    "hero.jiaxu_mobile.weimu": "锁定技",
    # jushou-lusu 票 01：一将成名-2014·沮授【渐营】（`USE_ON_USE` + 零步流水线，非锁定）
    # ⇒ 零步技能；【矢北】（锁定技 + 受伤后钩子、无流水线）⇒ 锁定技。
    "hero.jushou.jianying": "零步技能",
    "hero.jushou.shibei": "锁定技",
    # jushou-lusu 票 02：林包移动版·鲁肃【好施】（`PHASE_DRAW` + 零步流水线，座位问 / 交牌问
    # 都住 effect）⇒ 零步技能；【缔盟】（A 族 + 两步 `PickSeats` 流水线 + 手写主体）⇒ 主动技能。
    "hero.lu_su_mobile.haoshi": "零步技能",
    "hero.lu_su_mobile.dimeng": "主动技能",
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】（A 族 + 两步
    # `PickCards` → `PickSeats` 流水线，判定与三分支住 effect；非锁定、无钩子 / 接缝 /
    # 修饰器）⇒ 主动技能。
    "hero.jie_chenqun_mobile.dingpin": "主动技能",
    # jie-yijiang-2014-mobile 票 02：界顾雍移动版【慎行】（A 族 + 单步 `PickCards` 流水线，
    # 同色 / 异色的两段摸牌住 effect；非锁定、无钩子 / 接缝 / 修饰器）⇒ 主动技能。
    "hero.jie_guyong_mobile.shenxing": "主动技能",
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】（A 族 + 两步 `PickCards` → `PickSeats`
    # 流水线，判定与黑 / 红两分支住 effect；非锁定、无钩子 / 接缝 / 修饰器）⇒ 主动技能。
    # 【法恩】C1 摘出 ⇒ 本将只有这一行。
    "hero.chenqun.dingpin": "主动技能",
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】（A 族 + 单步 `PickCards` 流水线，
    # 弃两张 → 恒摸一张住 effect；非锁定、无钩子 / 接缝 / 修饰器）⇒ 主动技能。
    # 【秉壹】原随 C3（座位多选面）摘出，票 09 解锁后是下面那一行。
    "hero.guyong.shenxing": "主动技能",
    # yijiang-2014 票 09：顾雍【秉壹】（`PHASE_FINISH` 阶段体 + **零步流水线**，展示 /
    # 判颜色 / 座位多选 / 逐座位摸牌全住 effect ⇒ 机械判据落在「零步技能」格；
    # 座位步是**手写**的，不是 `PickSeats` 步——条件不成立时展示必须已经发生）。
    "hero.guyong.bingyi": "零步技能",
    # yijiang-2014 票 08（W4b）：陈群【法恩】（`STATE_FLIPPED` / `STATE_CHAINED` 两条钩子 +
    # **零步流水线**，摸一张住 effect；非锁定、无修饰器 / 接缝）⇒ 零步技能格。
    "hero.chenqun.faen": "零步技能",
}
"""171 条技能 → 形态（W6-04，spec §4.4）：**读法**，不是代码字段。

`SkillDef` 上没有 shape 字段，也不该有：形态是从既有声明读出来的归类（`locked` /
`skill_type` / `hooks` / `modifiers` / `seams` / `flow_for` / `deck_arrange` /
`rejudge_capable`），不是第二份真源。这张表把那次阅读**机械化钉住**：新增 / 删除技能时先缺键 / 多键变红，逼出一次
重新分类；改表 = 重新裁定形态。

**不要**把这张表与 `SKILL_FILES` 混用：后者是 32 条启用技能 → 点名它的族级单测文件
（票 14 的机械覆盖表），语义完全不同。
"""

SHAPE_COUNTS: dict[str, int] = {
    # feng-zhangjiao 票 01：张角移动版三条 ⇒ 主动技能 20 → 21（黄天）、触发技能 14 → 15
    # （雷击，两条钩子同一条流水线）、能力位 2 → 3（鬼道）。
    # huo-dianwei-mobile 票 01：火包移动版典韦【强袭】⇒ 主动技能 21 → 22。
    # huo-mobile-batch 票 01：庞德【马术】+【猛进】⇒ 锁定技 31 → 32、触发技能 15 → 16。
    # huo-taishici-xunyu 票 01：太史慈【天义】⇒ 主动技能 22 → 23。
    # lei-batch 票 01：郝昭【镇骨】⇒ 触发技能 20 → 21。
    # jie-feng-batch 票 01：界黄忠移动版【烈弓】⇒ 零步技能 29 → 30。
    # lei-shenganning 票 01：神甘宁【魄袭】⇒ 主动技能 26 → 27；【劫营】（三条钩子、
    # 一步选座流水线）⇒ 触发技能 22 → 23；「营」的**标记自身效果**（`carrier = MARKER`，
    # 手写钩子 + 两个修饰器、没有流水线）⇒ 多轮技能 16 → 17。
    # jushou-lusu 票 02：鲁肃【缔盟】（A 族 + 两步座位流水线）⇒ 主动技能 30 → 31。
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】⇒ 主动技能 32 → 33；
    # 票 02：界顾雍移动版【慎行】⇒ 33 → 34。
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】⇒ 主动技能 34 → 35。
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】⇒ 主动技能 35 → 36。
    "主动技能": 36,
    "触发技能": 26,
    # lin-batch2 票 02：董卓移动版【暴虐】（`DAMAGE_AFTER_DEAL` 零步流水线）⇒ 32 → 33。
    # lin-batch2 票 03：贾诩移动版【乱武】（A 族限定技 + 零步流水线，与【业炎】同格）
    # ⇒ 33 → 34。
    # jushou-lusu 票 01：沮授【渐营】（`USE_ON_USE` 零步流水线）⇒ 零步技能 34 → 35。
    # jushou-lusu 票 02：鲁肃【好施】（`PHASE_DRAW` 零步流水线）⇒ 35 → 36。
    # yijiang-2014 票 09：顾雍【秉壹】（`PHASE_FINISH` 零步流水线 + 手写座位多选面）
    # ⇒ 37 → 38。
    # yijiang-2014 票 08（W4b）：陈群【法恩】（两条状态位钩子 + 零步流水线）⇒ 38 → 39。
    "零步技能": 39,
    "排列技能": 5,
    # tao-caocao 票 01：韬·曹操【鸿志】⇒ 锁定技 35 → 36；【倡义】（无流水线的钩子段机）
    # ⇒ 多轮技能 14 → 15。
    # huo-wolong-yanliang 票 01：卧龙诸葛【八阵】（锁定技 + 候选生成器）⇒ 锁定技 36 → 37；
    # 【火计】【看破】两条转化声明 ⇒ 转化技 15 → 17。
    # lin-batch 票 01：神曹操移动版【归心】（手写逐座位循环、无流水线）⇒ 多轮技能 19 → 20；
    # 【飞影】（锁定技 + 纯修饰器）⇒ 锁定技 37 → 38。
    # lin-batch 票 02：孙坚移动版【英魂】（准备阶段钩子 + 两步流水线）⇒ 触发技能 23 → 24。
    # lin-batch 票 03：徐晃移动版【断粮】（单源转化声明 + 距离修饰器）⇒ 转化技 18 → 19。
    # lin-batch2 票 01：曹丕移动版【行殇】（`DEATH_ON` + 零步流水线）与【颂威】（判定族
    # 零步流水线）⇒ 零步技能 30 → 32；【放逐】（受伤后钩子 + 单步选座流水线）⇒ 触发技能
    # 24 → 25。
    # lin-batch2 票 02：董卓移动版【暴虐】（零步流水线）⇒ 零步技能 32 → 33；【肉林】/
    # 【崩坏】（锁定技）⇒ 锁定技 38 → 40；【酒池】（单源转化）⇒ 转化技 19 → 20。
    "多轮技能": 20,
    # lin-batch2 票 03：贾诩移动版【完杀】/【帷幕】（两条纯修饰器）⇒ 锁定技 40 → 42。
    # jushou-lusu 票 01：沮授【矢北】（锁定技 + 受伤后钩子、无流水线）⇒ 锁定技 42 → 43。
    "锁定技": 43,
    "转化技": 20,
    "能力位": 4,
    "候选生成器": 1,
    "候选提供者": 5,
    "觉醒技": 3,
}
"""每格的条数（spec §4.4；M6 票 18 后
17 + 13 + 26 + 3 + 13 + 30 + 13 + 2 + 1 + 5 + 2 = 125）。

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
同一条转化声明（转化技格）。票 11 的一条：**博图**是回合结束时触发的零步技（零步技能格）。
票 10 的两条：**谦逊**是生效时触发的零步技（零步技能格，
放置区的三项声明在数据里）；**连营**（界）是逐座选人的手写多轮（多轮技能格）。票 14 的
六条：**奸雄**（界，拿牌 + 摸一张）/ **裸衣**（界，亮三张 → 拿 → 跨回合 +1）是零步；
**反馈**（界，共用标准版的一步选牌流水线）/ **突袭**（界，阶段体 + 座位步）是触发技能；
**鬼才**（界）是能力位；**护驾**（界）是候选提供者（①）且另带②的两条零步钩子——
它按"候选提供者单列"归格，零步判据因此**排除**提供者（与"锁定技 / 觉醒技单列"同一条
互斥纪律）。锁定技牌面写
"锁定技"，故即使英姿带一条零步阶段体流水线也按**类型**归「锁定技」格（零步格因此
**互斥纪律）。锁定技牌面写
"锁定技"，故即使英姿带一条零步阶段体流水线也按**类型**归「锁定技」格（零步格因此
**排除**锁定技，见 :func:`test_every_shape_has_the_documented_count_and_members`)。
票 15 的四条：**天妒**（界，与标准版共用零步流水线）/ **忘隙**（手写回调，多轮格）与
标准版两张同格；**恂恂**是固定切分 2 / 2 的排列技能（排列格）；**遗计**（界）是手写的
"摸两张 → 逐张选择收牌人"多轮（多轮格）。"""

STEP_LANGUAGE_SHAPES: frozenset[str] = frozenset(
    {"主动技能", "触发技能", "零步技能", "排列技能", "觉醒技"}
)
"""在步骤语言内的五个形态（69 条 = 22 + 15 + 27 + 3 + 2）。觉醒技也走 ``SkillFlow``
（【潜心】零步、【勤学】一步选项），故"内 == 有 ``flow_for``"仍成立。"""

IN_STEP_LANGUAGE: frozenset[str] = frozenset(
    skill_id
    for skill_id, shape in SKILL_SHAPE.items()
    if shape in STEP_LANGUAGE_SHAPES
)
"""在步骤语言内（68）：**派生**自 :data:`SKILL_SHAPE` 的五个步骤语言形态。

W6-07 / 评审 J7：这里原先手抄了 19 个 id，与 `SKILL_SHAPE` 是同一份事实的两份拷贝——
形态表一改，第二份名单就悄悄过期。现在它只是那张表的一个投影；真正的判据留在
:func:`test_the_step_language_boundary_is_exactly_the_flow_backed_skills`（"内 == 有
`flow_for`"），不是这行的规模。
"""

OUTSIDE_STEP_LANGUAGE: frozenset[str] = frozenset(SKILL_SHAPE) - IN_STEP_LANGUAGE
"""不在步骤语言内（67）：多轮 14 + 锁定 31 + 转化 13 + 能力位 3 + 候选生成器 1 + 候选提供者 5。

同样**派生**（:data:`SKILL_SHAPE` 减 :data:`IN_STEP_LANGUAGE`）；66 这个数由
`SHAPE_COUNTS` 的其余六格加出来，测试里另有一条规模断言。
"""

ZERO_STEP_SKILL_IDS: tuple[str, ...] = tuple(
    skill_id for skill_id, shape in SKILL_SHAPE.items() if shape == "零步技能"
)
"""W6-01 的零步技能（spec §4.1）：连营 / 枭姬 / 奸雄 / 天妒 / 英姿 / 闭月 / 铁骑，
M6 票 01 加【裸衣】、票 02 加【集智】、票 03 加【克己】/【苦肉】、票 06 加界【克己】、
票 07 加界【闭月】、票 10 加界【谦逊】、票 11 加界【博图】、票 14 加界【奸雄】/界【裸衣】、
票 15 加界【天妒】、票 03 加风包【烈弓】（共 25 条；觉醒技【潜心】虽有零步流水线，
按类型单列「觉醒技」格，不在此列）。

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


def _flow_backed_active_ids() -> frozenset[str]:
    """机械读出的「主动技能」格：有**非空**流水线、没有钩子 / 接缝。

    两处读它（一处形态、一处边界），免得同一条谓词抄两遍就悄悄漂移：
    :func:`test_every_shape_has_the_documented_count_and_members` 的逐格核对，与
    :func:`test_the_step_language_boundary_is_exactly_the_flow_backed_skills` 的
    "提供者里谁按主动技列格"（移动版于吉【蛊惑】同时有出牌阶段的流水线与提供者入口）。
    """
    return frozenset(
        skill_id
        for skill_id, definition in CATALOG.items()
        if (_steps := _flow_steps(skill_id)) is not None
        and _steps != ()
        and not definition.hooks
        and not definition.seams
    )


# ---------------------------------------------------------------------------
# W6 / F2（yijiang-2014-round2 票 06）：A 族声明也能带钩子
# ---------------------------------------------------------------------------


def test_an_active_declaration_can_carry_hooks_and_a_flow_in_one_definition(monkeypatch):
    """``ActiveSkill.hooks`` 由声明透传进**同一条** ``SkillDef``（缺省空 ⇒ A 族既有声明零改动）。

    为什么必须住进**同一条**声明：``CATALOG`` 是 ``{skill_id: definition}`` 的字典推导，
    同 id 的后者**静默覆盖**前者——下面用两条同 id 的定义把这条语义钉出来；一条声明同时带
    钩子与 A 族流水线（界【渐营】的 ① + ②）才让两半都留下。
    """
    from sgs.skills import active as active_module
    from sgs.skills.active import ActiveSkill, active_skill_definitions
    from sgs.skills.catalog import CarrierKind, Hook, SkillDef
    from sgs.skills.flow import PickCards, SkillFlow
    from sgs.skills.preactivation import _build_flows

    def _noop(host, frame):
        del host, frame
        return None

    def _effect(host, frame, answers):
        del host, frame, answers
        return None

    hook = Hook(Timing.PHASE_DRAW, handler=_noop)
    flow = SkillFlow(
        name="w6_f2_probe",
        steps=(PickCards(zones=(Zone.HAND,), lo=1, hi=1),),
        effect=_effect,
    )
    declaration = ActiveSkill(
        "w6_probe",
        "f2",
        lambda state, context, current: current,
        flow=flow,
        hooks=(hook,),
        requirement="探针：一条声明同时带钩子与 A 族流水线",
    )
    monkeypatch.setattr(active_module, "ACTIVE_SKILLS", (declaration,))

    (definition,) = active_skill_definitions()

    assert definition.skill_id == "hero.w6_probe.f2"
    assert definition.hooks == (hook,), "缺失 F2 时这里恒为 ()（透传就是那 1 行）"
    assert [modifier.point for modifier in definition.modifiers] == [
        ModifierPoint.CANDIDATES
    ], "A 族的候选挂点仍在这一份定义上"
    flows = _build_flows(active=(declaration,), catalog={definition.skill_id: definition})
    assert flows[definition.skill_id] == ((None, flow),), "同一份声明的非空流水线照样登记"

    hooks_only = SkillDef(
        skill_id=definition.skill_id,
        locked=False,
        carrier=CarrierKind.HERO,
        hooks=(hook,),
    )
    # CATALOG 的合并姿态是 `{skill_id: definition}` 的字典推导：同 id 的**后者胜**。
    split = {item.skill_id: item for item in (definition, hooks_only)}
    assert split[definition.skill_id] is hooks_only
    assert split[definition.skill_id].modifiers == (), (
        "拆成两条同 id 的声明 ⇒ 后者整条覆盖前者（这里 A 族候选挂点消失）；F2 就是修这个"
    )


def test_the_skill_shape_table_covers_the_catalog_exactly_once() -> None:
    """168 条恰好一形态：键 = ``CATALOG`` 的键、值 = 11 个形态闭集之一。"""
    assert set(SKILL_SHAPE) == set(CATALOG)
    # lin-batch2 票 03：三条新技能进 CATALOG / SKILL_SHAPE ⇒ 185 → 188。
    # jushou-lusu 票 01：沮授两条 ⇒ 188 → 190；票 02：鲁肃两条 ⇒ 190 → 192。
    # jie-feng-batch2 票 01：界夏侯渊移动版【神速】⇒ 192 → 193；票 02：界张角三条 ⇒ 193 → 196。
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】⇒ 196 → 197；票 02：界顾雍移动版 ⇒ 198。
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】⇒ 198 → 199。
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】⇒ 199 → 200；票 09 补【秉壹】⇒ 201。
    # yijiang-2014 票 08（W4b）：陈群【法恩】（零步流水线）⇒ 201 → 202。
    assert len(SKILL_SHAPE) == len(CATALOG) == 202
    assert set(SKILL_SHAPE.values()) == set(SHAPE_COUNTS)
    assert len(SHAPE_COUNTS) == 11, (
        "原来 12 格；「手写主动技」那一格随票 04 §3 的预发动改造**消失**"
        "（它唯一的成员【蛊惑】归「主动技能」）"
    )
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
    active = _flow_backed_active_ids()
    triggered = frozenset(
        skill_id
        for skill_id in CATALOG
        if (_steps := _flow_steps(skill_id)) is not None
        and _steps != ()
        and (CATALOG[skill_id].hooks or CATALOG[skill_id].seams)
        and CATALOG[skill_id].deck_arrange is None
        and skill_id not in awakening
        # lin-batch2 票 02：**锁定技按类型单列一格**（与 :data:`zero_step` 同一条互斥纪律）。
        # 董卓移动版【崩坏】是第一条"锁定 + 非空流水线 + 带钩子"的技能（界【英姿】也锁定，
        # 但它的流水线是零步 ⇒ 本来就落在零步谓词之外），不扣掉它就会同时进「触发技能」格。
        and not CATALOG[skill_id].locked
    )
    # 「主动技能」这一格要在 providers 之前定格：移动版于吉【蛊惑】**同时**是有流水线的
    # 主动技与提供者（出牌阶段入口 + "需要牌时"的提供者），而本表一格一技，取它更根本的
    # 那一格——发动主体是那条两步流水线（提供者只是它的第二个入口）。故 providers 这里
    # 扣掉 flow-backed 的主动技（与"锁定技 / 觉醒技单列"同一条互斥纪律）。
    providers = (frozenset(PROVIDERS) | {"equip.eight_diagrams"}) - active
    zero_step = frozenset(
        skill_id
        for skill_id in CATALOG
        # 锁定技按**类型**单列一格（界【英姿】带一条零步阶段体流水线也是锁定技），
        # 故零步格排除锁定技——与"觉醒技单列"同一条互斥纪律。
        # 同理：候选提供者（界【护驾】）带 ② 的零步钩子，仍按"提供者"单列一格。
        # huo-wolong-yanliang 票 02：【双雄】是"零步阶段体 + 转化那一半"的第一条——
        # 它同时满足"零步"与"声明了 virtual_type_ids"两条机械判据，而本表一格一技：
        # 取**转化**那一格（与【连环】同姿态——转化是它的主格，零步只是执行形状）。
        if _flow_steps(skill_id) == ()
        and skill_id not in awakening
        and skill_id not in providers
        and not CATALOG[skill_id].locked
        and not CATALOG[skill_id].virtual_type_ids
    )
    arranged = skills(lambda definition: definition.deck_arrange is not None)
    locked = skills(
        lambda definition: definition.locked and not definition.virtual_type_ids
    )
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
    assert set(PROVIDERS) == {
        "hero.caocao.hujia",
        "hero.jie_caocao.hujia",
        "hero.liubei.jijiang",
        "hero.jie_liubei.jijiang",
        # M6 票 01：**非主公技**的提供者（移动版于吉【蛊惑】的"需要牌时"入口）——
        # `PROVIDERS` 由 `register_provider` 在 `catalog.py` 末尾并入，不是靠主公技枚举。
        "hero.yuji_mobile.guohuo",
    }

    multi_round = _shape_members("多轮技能")
    assert len(multi_round) == 20
    assert all(_flow_steps(skill_id) is None for skill_id in multi_round)
    assert all(
        any(hook.handler is not None for hook in CATALOG[skill_id].hooks)
        for skill_id in multi_round
    ), "多轮技能都保持手写钩子（flow_for is None）"


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
    """91 在内 / 83 在外（174 条）：两侧集合显式，且与「有没有 ``SkillFlow``」逐条同源。"""
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
    # lin-batch2 票 01：曹丕移动版【行殇】/【颂威】（零步）+【放逐】（触发）⇒ 内 92 → 95。
    # lin-batch2 票 02：董卓移动版【暴虐】（零步）⇒ 内 95 → 96；【酒池】（转化）/
    # 【肉林】/【崩坏】（锁定）⇒ 外 86 → 89。
    # lin-batch2 票 03：贾诩移动版【乱武】（零步流水线）⇒ 内 96 → 97；【完杀】/
    # 【帷幕】（纯修饰器、无流水线）⇒ 外 89 → 91。
    # jushou-lusu 票 01：沮授【渐营】（零步流水线）⇒ 内 97 → 98；【矢北】（锁定、无 flow）
    # ⇒ 外 91 → 92。票 02：鲁肃【好施】（零步）/【缔盟】（主动）各一条流水线 ⇒ 内 98 → 100。
    # jie-yijiang-2014-mobile 票 01：界陈群移动版【定品】（两步流水线）⇒ 内 103 → 104；
    # 票 02：界顾雍移动版【慎行】（单步流水线）⇒ 内 104 → 105。
    # yijiang-2014 票 02：一将成名-2014·陈群【定品】（两步流水线）⇒ 内 105 → 106。
    # yijiang-2014 票 03：一将成名-2014·顾雍【慎行】（单步流水线）⇒ 内 106 → 107。
    # yijiang-2014 票 09：顾雍【秉壹】（零步流水线，形态归「零步技能」）⇒ 内 107 → 108。
    # yijiang-2014 票 08（W4b）：陈群【法恩】（零步流水线，形态归「零步技能」）⇒ 内 108 → 109。
    assert len(inside) == 109
    assert len(outside) == 93
    assert inside.isdisjoint(outside)
    assert inside | outside == set(CATALOG)
    # 与**执行路径**逐条同源（形态表若把一条没有 flow 的技能划进来，这里红）。判据要扣掉
    # **锁定技**：M6 票 08 的界【英姿】带一条零步阶段体流水线，但按技能类型归「锁定技」格
    # （与觉醒技单列同一姿态）——"有没有 flow"不再是划分步骤语言内外的充分条件。
    locked = {skill_id for skill_id in CATALOG if CATALOG[skill_id].locked}
    # "提供者"这一格只收**按提供者单列**的那些：同时有主动技流水线的（【蛊惑】）归「主动
    # 技能」格，故这里先扣掉它们——否则一条 inside 的技能会同时被当成 outside 的证据。
    providers = (frozenset(PROVIDERS) | {"equip.eight_diagrams"}) - _flow_backed_active_ids()
    flow_backed = {
        skill_id for skill_id in CATALOG if flow_for(skill_id) is not None
    }
    # 候选提供者（界【护驾】带 ② 的零步钩子）也排除——与锁定技同一条"按格单列"。
    # **转化那一半带流水线的**同样排除（huo-wolong-yanliang 票 02：【双雄】= 零步阶段体 +
    # `CANDIDATES`，形态表按"转化"单列一格）——与锁定技 / 提供者同一条互斥纪律。
    conversions = {
        skill_id for skill_id in CATALOG if CATALOG[skill_id].virtual_type_ids
    }
    assert flow_backed - locked - providers - conversions == inside
    assert (
        {skill_id for skill_id in CATALOG if flow_for(skill_id) is None}
        | (flow_backed & (locked | providers | conversions))
    ) == outside
    assert ZERO_STEP_SKILL_IDS and set(ZERO_STEP_SKILL_IDS) <= inside


# ---------------------------------------------------------------------------
# 标记自身效果（lei-shenganning 票 94）
#
# 用户 2026-11-07 裁定："这个是标记本身的效果……所以就算劫营这个技能失效了，依然享受以上所说的
# 3 种加成。营标记的寿命：标记来源角色阵亡 / 标记来源角色的【劫营】失去（被无效不影响，营标记还在）"。
# 判据 = 标记带 `effect` ⇒ 以 `MARKER` 载体产出实例（**永不受失效影响**）；来源失去该技能时
# 按寿命策略移除。
# ---------------------------------------------------------------------------


def _marker_effect_catalog(monkeypatch, effect_id: str = "test.marker_effect"):
    """往 CATALOG 里塞一条**标记效果定义**（只为证明载体会被解析，不承载真实机制）。"""
    from sgs.effects import CarrierKind, Modifier, ModifierPoint
    from sgs.skills import catalog as catalog_module
    from sgs.skills.catalog import SkillDef

    def handler(state, ctx, current):
        return current

    fake = SkillDef(
        skill_id=effect_id,
        locked=False,
        carrier=CarrierKind.HERO,
        modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, handler),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, effect_id: fake}),
    )
    return effect_id


def _marker(effect: str | None, *, name: str = "营", clear_on_source_skill_lost: bool = True):
    return Marker(
        name=name,
        source_seat=0,
        source_skill_id="hero.shen_ganning.jieying",
        count=1,
        effect=effect,
        clear_on_source_skill_lost=clear_on_source_skill_lost,
    )


def test_a_marker_carries_its_own_effect_and_suppression_does_not_stop_it(monkeypatch):
    from sgs.skills import resolve_instances
    from sgs.skills.catalog import SkillSourceKind

    effect_id = _marker_effect_catalog(monkeypatch)
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    state.player(1).markers = (_marker(effect_id),)

    instances = [item for item in resolve_instances(state) if item.skill_id == effect_id]
    assert len(instances) == 1, "标记带 effect ⇒ 产出**恰好一条**实例"
    assert instances[0].owner == 1, "实例的 owner = 标记**持有者**（不是来源）"
    assert instances[0].source.kind is SkillSourceKind.MARKER
    assert instances[0].source.skill_id == "hero.shen_ganning.jieying", (
        "来源记『放置这枚标记的那条技能』"
    )

    engine = Engine.from_state(state, seed=1)
    engine.suppress_skills(1, "hero.anyone.applying")
    assert engine.state.player(1).markers == state.player(1).markers, (
        "失效**不动标记**（用户：被无效不影响，营标记还在）"
    )
    assert any(item.skill_id == effect_id for item in resolve_instances(engine.state)), (
        "失效也**不停标记效果**（用户：就算劫营失效，依然享受加成）"
    )


def test_losing_the_source_skill_removes_only_the_markers_that_ask_for_it(monkeypatch):
    from sgs.skills import resolve_instances

    effect_id = _marker_effect_catalog(monkeypatch)
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    state.player(1).markers = (
        _marker(effect_id),
        _marker(None, name="测试·不随技能失去", clear_on_source_skill_lost=False),
    )
    engine = Engine.from_state(state, seed=1)

    engine.lose_skill(0, "hero.shen_ganning.jieying")

    names = tuple(marker.name for marker in engine.state.player(1).markers)
    assert names == ("测试·不随技能失去",), f"只移除声明了那条寿命策略的标记；实得 {names}"
    assert not [item for item in resolve_instances(engine.state) if item.skill_id == effect_id], (
        "标记没了 ⇒ 它的效果实例也没了"
    )
    removed = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "marker_change"
    ]
    assert [(record.name, record.count) for record in removed] == [("营", 0)], (
        "移除走唯一写入口 ⇒ 照常落一条 marker_change（绝对值 0）"
    )
