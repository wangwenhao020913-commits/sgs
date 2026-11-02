"""内容包的定位、解析与加载期校验。

校验顺序固定为 **版本 → 结构 → 语义**：

1. 版本：未知 ``schema_version`` 立即拒绝，**不**继续做结构校验 ——
   这样旧/新档错配报的是"版本不符"，而不是一堆误导性的字段错误。
2. 结构：pydantic v2，``extra="forbid"``；全部错误一次报完。
3. 语义：手写检查，在结构通过之后跑（结构没过时字段本身还不可信）。

任何一层不过都抛 :class:`~sgs.data.DataLoadError`，**绝不返回部分数据**。

语义层里有两条**跨层校验**（票 09 §7 的 #9/#10）：牌名 ↔ 行为表双向覆盖、
行为表里的 ``CallOp`` 操作名 ∈ ``OPS`` 注册表；以及装备牌名 ⇒ 技能表里有对应的
``equip.<牌名 id>``（票 09 §7 的 #8）。它们是"行为留在代码、数据与代码不漂移"的
机械保证——故加载器**跨层调用** ``sgs.effects`` 与 ``sgs.skills`` 的只读查询。
两个导入都放在函数体内：``sgs.state`` 反向依赖 ``sgs.data``，模块级导入会成环。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from .errors import DataLoadError, LoadError
from .models import (
    Category,
    CardType,
    ContentPack,
    Deck,
    Hero,
    HeroTable,
    PlacementDecl,
    SkillType,
)

__all__ = [
    "DEFAULT_PACK_ID",
    "HERO_SKILL_PREFIX",
    "HERO_SUPPLEMENT_PACK_IDS",
    "KNOWN_HEROES_SCHEMA_VERSIONS",
    "KNOWN_SCHEMA_VERSIONS",
    "card_category",
    "enabled_hero_ids",
    "hero_card_skill_ids",
    "hero_by_id",
    "hero_placement_decl",
    "hero_placement_decls",
    "hero_skill_id",
    "hero_skill_ids",
    "load_content_pack",
    "load_hero_tables",
    "load_heroes",
    "validate_content_pack",
    "validate_heroes",
]

KNOWN_SCHEMA_VERSIONS: frozenset[int] = frozenset({2})
"""加载器认得的**数据模型**版本；与事件 schema 版本、规则版本是三件事。

升 2 的判据落在"旧加载器读新文件"：``Deck.slot_limit`` / ``CardType.attack_range`` /
``CardType.delayed`` / ``DeckExpect.by_suit`` / ``by_category`` 在旧代码的
``extra="forbid"`` 下是结构错误——这正是该由版本号先拦下的情形（票 09 §7）。
"""

KNOWN_HEROES_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
"""武将表（``heroes.<pack_id>.json``）认得的版本集合（票 01）。

与 ``KNOWN_SCHEMA_VERSIONS`` **分开**：武将表是新文件、独立升版，牌堆侧的改动不该
牵动它，反之亦然。首版取 1。
"""

DEFAULT_PACK_ID = "standard"

HERO_SUPPLEMENT_PACK_IDS: tuple[str, ...] = (
    "jie",
    "jie_mobile",
    "feng",
    "feng_mobile",
    "huo_mobile",
    "homebrew",
)
"""与内容包表并行装载的**扩展武将包**（M6 票 04：界标准独立成一份数据）。

界标准 29 名与标准 25 名**分开存**（ADR-006 的"分开存"这次真落），故
``files/heroes.jie.json`` 是独立的一份 :class:`~sgs.data.models.HeroTable`，**不是**
内容包（内容包管牌堆与牌名，武将包管武将面）。它由 :func:`load_hero_tables` 与内容包
自己的表一起交给读取面：载体解析按同一个武将 id 查全部表，故界将能被构造局面直接发放。
后续界将票只往这个文件追加条目（真名配将与默认将池属于票 12：票 12 起启用池 / 默认
将池是**两张表的并集**，配将再按真名分组）。

``jie_mobile`` 是**移动版**那一份（``files/heroes.jie_mobile.json``）：与 OL 系列
**版本并存**——同一真名可以有 OL 版与移动版两条记录，版本字段分别是 ``jie-ol`` 与
``jie-mobile``，显示名按"OL 不加后缀、移动版加「移动版」"命名（用户裁定）。所以扩展包
不是"一个版本"，而是"每个版本一份表"；读取面（:func:`load_hero_tables`）按声明序并起来，
缺表时才报错。

``feng`` 是**神话再临·风包**那一份（``files/heroes.feng.json``，票 03 起）：它既不是标准版
的"另一版本"、也不是移动版，而是一个**独立的系列包**——故单独一表、版本标记取 ``"feng"``。
风包武将逐名追加到这张表；与界标准 / 移动版同一读取面（同一道三段校验、同一个默认将池）。

``huo_mobile`` 是**神话再临·火包的移动版**那一份（``files/heroes.huo_mobile.json``，
huo-dianwei-mobile 票 01 起）：与 ``feng_mobile`` 同一姿态——移动版独立成表、版本标记取
``"huo-mobile"``、显示名加「移动版」后缀。火包武将逐个追加到这张表；将来做 **OL 火包**
时另开 ``files/heroes.huo.json``（版本标记 ``"huo"``），两表并存、同真名可各持一版
（用户裁定「移动版与 OL 系列版本并存」）。

``homebrew`` 是**自制包**那一份（``files/heroes.homebrew.json``）：同样是一个独立系列包，
版本标记取 ``"homebrew"``，显示名不带任何版本后缀（自制武将自带「韬·」系列前缀）。首个载体
是韬·刘备自制版（真名刘备，与标准版同名 ⇒ 同一局仍只出一个）。"""

HERO_SKILL_PREFIX = "hero."
"""武将技能 id 的前缀（票 01 §Data / 票 12）：``hero.<武将 id>.<短 id>``。

前缀与装备的 ``equip.`` 并列，是"两张表能双向对上、漏配能机械检出"的落点：
加载期的启用武将引用校验、派发期的载体解析都经 :func:`hero_skill_id` 一处派生。
"""

RANK_MIN = 1
RANK_MAX = 13

SUBCATEGORIES: frozenset[str] = frozenset(
    {"instant", "delayed", "weapon", "armor", "horse_offense", "horse_defense"}
)
"""``CardType.subcategory`` 的**闭集**（票 09 §7 #11）；基本牌取 ``null``。

宝物槽位属界限突破、M2 不启用，故 ``treasure`` **不在**闭集里：预留的是槽位枚举，
不是可加载的子类别。
"""

_EQUIPMENT_SKILL_PREFIX = "equip."

_EXPECTED_BY_PYDANTIC_ERROR: dict[str, str] = {
    "missing": "必填字段",
    "extra_forbidden": "无多余字段",
    "enum": "枚举取值之一",
    "string_type": "字符串",
    "string_too_short": "非空字符串",
    "int_type": "整数",
    "int_parsing": "可解析为整数的值",
    "bool_type": "布尔值",
    "none_required": "null",
    "model_type": "对象",
    "dict_type": "对象",
    "list_type": "数组",
    "tuple_type": "数组",
    "too_short": "非空集合",
    "frozen_instance": "不可变对象（不允许赋值）",
}


def _json_pointer(loc: tuple[Any, ...]) -> str:
    if not loc:
        return "/"
    return "/" + "/".join(str(part) for part in loc)


def _structural_errors(file: str, exc: ValidationError) -> list[LoadError]:
    errors: list[LoadError] = []
    for detail in exc.errors():
        error_type = detail["type"]
        errors.append(
            LoadError(
                file=file,
                loc=_json_pointer(tuple(detail["loc"])),
                # 未登记的 pydantic 错误码也照实带出，避免"静默归到通用文案"。
                expected=_EXPECTED_BY_PYDANTIC_ERROR.get(
                    error_type, f"符合模型约束（{error_type}）"
                ),
                got="缺失" if error_type == "missing" else repr(detail.get("input")),
                rule_id=f"structure.{error_type}",
            )
        )
    return errors


def _parse_json(file: str, raw: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="合法 JSON",
                    got=f"{exc.msg}（行 {exc.lineno} 列 {exc.colno}）",
                    rule_id="structure.invalid_json",
                )
            ]
        ) from exc
    if not isinstance(parsed, Mapping):
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="JSON 对象",
                    got=type(parsed).__name__,
                    rule_id="structure.root_type",
                )
            ]
        )
    return parsed


def _version_error(
    file: str,
    parsed: Mapping[str, Any],
    *,
    known: frozenset[int] = KNOWN_SCHEMA_VERSIONS,
) -> LoadError | None:
    """版本闸门：未知 / 缺失 ``schema_version`` 一律拒绝（fail-closed）。

    ``known`` 让武将表（``heroes.<pack_id>.json``）复用同一条判据而不共用版本集合。
    """
    if "schema_version" not in parsed:
        return LoadError(
            file=file,
            loc="/schema_version",
            expected=f"数据模型版本之一：{sorted(known)}",
            got="缺失",
            rule_id="version.missing",
        )
    value = parsed["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in known:
        return LoadError(
            file=file,
            loc="/schema_version",
            expected=f"数据模型版本之一：{sorted(known)}",
            got=repr(value),
            rule_id="version.unknown",
        )
    return None


def _semantic_errors(file: str, pack: ContentPack) -> list[LoadError]:
    """结构通过之后的语义校验；一次收集全部问题。"""
    errors: list[LoadError] = []

    if not pack.card_types:
        errors.append(
            LoadError(
                file=file,
                loc="/card_types",
                expected="非空的牌名级定义列表",
                got="0 条",
                rule_id="card_types.empty",
            )
        )

    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for index, card_type in enumerate(pack.card_types):
        if card_type.id in seen_ids:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/card_types/{index}/id",
                    expected="全局唯一的牌名 id",
                    got=repr(card_type.id),
                    rule_id="card_types.duplicate_id",
                )
            )
        seen_ids.add(card_type.id)
        if card_type.name in seen_names:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/card_types/{index}/name",
                    expected="全局唯一的牌名",
                    got=repr(card_type.name),
                    rule_id="card_types.duplicate_name",
                )
            )
        seen_names.add(card_type.name)

    errors.extend(_card_type_errors(file, pack.card_types, pack_id=pack.pack_id))

    if pack.deck_profile not in pack.decks:
        errors.append(
            LoadError(
                file=file,
                loc="/deck_profile",
                expected=f"decks 中存在的档位之一：{sorted(pack.decks)}",
                got=repr(pack.deck_profile),
                rule_id="decks.unknown_profile",
            )
        )

    card_types = {card_type.id: card_type for card_type in pack.card_types}
    for profile, deck in pack.decks.items():
        base = f"/decks/{profile}"
        errors.extend(_deck_errors(file, base, deck, card_types))

    return errors


def _card_type_errors(
    file: str, card_types: tuple[CardType, ...], *, pack_id: str
) -> list[LoadError]:
    """牌名级语义校验：子类别闭集、延时自洽、武器范围、装备技能、行为表覆盖、op 引用。

    行为表是**代码侧的全集**（``CARD_BEHAVIORS`` 覆盖标准版全表），故"双向覆盖"拆成两半：

    - ``card_type.behavior_missing``：本包声明的牌名没有行为 —— **所有包**都查，
      否则该包根本执行不了；
    - ``card_type.behavior_orphan``：行为表里有、本包牌名表里没有 —— **只对标准包**查。
      标准包的 ``card_types`` 就是行为表要一一对应的那个全集；部分包（测试里的最小
      内容包）只是**借用**标准行为表的一个子集，此时表外的条目不是孤儿。

    两条跨层只读查询在函数体内导入（``sgs.state`` 反向依赖 ``sgs.data``，模块级导入会成环）。
    """
    from sgs.effects import CARD_BEHAVIORS, OPS, CallOp
    from sgs.skills import equipment_skill_ids

    errors: list[LoadError] = []
    behaviors, ops = CARD_BEHAVIORS, OPS
    skill_ids = equipment_skill_ids()
    known_ids = {card_type.id for card_type in card_types}
    if not card_types:
        # 空牌名表是单点根因（已由 card_types.empty 报出）：再逐条报孤儿只会淹没它。
        return errors

    for index, card_type in enumerate(card_types):
        base = f"/card_types/{index}"

        if card_type.category is Category.BASIC:
            if card_type.subcategory is not None:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/subcategory",
                        expected="null（基本牌没有子类别）",
                        got=repr(card_type.subcategory),
                        rule_id="card_type.unknown_subcategory",
                    )
                )
        elif card_type.subcategory is None or card_type.subcategory not in SUBCATEGORIES:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/subcategory",
                    expected=f"子类别闭集之一：{sorted(SUBCATEGORIES)}",
                    got=repr(card_type.subcategory),
                    rule_id="card_type.unknown_subcategory",
                )
            )

        expected_delayed = (
            card_type.category is Category.TRICK and card_type.subcategory == "delayed"
        )
        if card_type.delayed != expected_delayed:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/delayed",
                    expected=str(expected_delayed),
                    got=str(card_type.delayed),
                    rule_id="card_type.delayed_mismatch",
                )
            )

        if card_type.subcategory == "weapon":
            if card_type.attack_range is None or card_type.attack_range < 1:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/attack_range",
                        expected="≥ 1 的整数（武器必有攻击范围）",
                        got=repr(card_type.attack_range),
                        rule_id="card_type.weapon_range_required",
                    )
                )
        elif card_type.attack_range is not None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/attack_range",
                    expected="null（攻击范围只属武器）",
                    got=repr(card_type.attack_range),
                    rule_id="card_type.range_only_for_weapon",
                )
            )

        if (
            card_type.category is Category.EQUIPMENT
            and f"{_EQUIPMENT_SKILL_PREFIX}{card_type.id}" not in skill_ids
        ):
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/id",
                    expected=f"技能表里存在 {_EQUIPMENT_SKILL_PREFIX}{card_type.id}（装备必有技能）",
                    got=repr(card_type.id),
                    rule_id="card_type.equipment_skill_missing",
                )
            )

        behavior = behaviors.get(card_type.id)
        if behavior is None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/id",
                    expected="行为表中有对应条目",
                    got=repr(card_type.id),
                    rule_id="card_type.behavior_missing",
                )
            )
            continue
        for step in behavior.effects:
            if isinstance(step, CallOp) and step.op not in ops:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/id",
                        expected=f"OPS 注册表中的操作名：{sorted(ops)}",
                        got=repr(step.op),
                        rule_id="card_type.unknown_op",
                    )
                )

    # 反向：标准包的行为表里若有、牌名表里没有的条目，也是"不相符"
    # （防行为表长出孤儿）；部分包借用标准行为表的子集，不在此列。
    if pack_id == DEFAULT_PACK_ID:
        for orphan in sorted(card_id for card_id in behaviors if card_id not in known_ids):
            errors.append(
                LoadError(
                    file=file,
                    loc="/card_types",
                    expected="行为表的每个条目都有对应牌名",
                    got=repr(orphan),
                    rule_id="card_type.behavior_orphan",
                )
            )

    return errors


def _deck_errors(
    file: str, base: str, deck: Deck, card_types: Mapping[str, CardType]
) -> list[LoadError]:
    errors: list[LoadError] = []
    slot_counts: dict[tuple[str, int], int] = {}
    slot_limit = deck.slot_limit

    for index, card in enumerate(deck.cards):
        # 牌名引用只在有定义可查时才逐张判定：card_types 为空是单点根因，已由
        # card_types.empty 报出；再逐张报"引用不存在"只会用 53 条噪声淹没它。
        if card_types and card.type not in card_types:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/cards/{index}/type",
                    expected="card_types 中存在的牌名 id",
                    got=repr(card.type),
                    rule_id="deck.unknown_type",
                )
            )
        if not RANK_MIN <= card.rank <= RANK_MAX:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/cards/{index}/rank",
                    expected=f"{RANK_MIN}–{RANK_MAX} 的整数",
                    got=repr(card.rank),
                    rule_id="deck.rank_range",
                )
            )

        slot = (card.suit.value, card.rank)
        slot_counts[slot] = slot_counts.get(slot, 0) + 1
        if slot_counts[slot] == slot_limit + 1:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/cards/{index}",
                    expected=f"同一 (花色,点数) 槽位至多 {slot_limit} 张",
                    got=f"{slot_counts[slot]} 张",
                    rule_id="deck.slot_limit",
                )
            )

    if deck.expect is not None:
        errors.extend(_deck_expect_errors(file, base, deck, card_types))

    return errors


def _deck_expect_errors(
    file: str, base: str, deck: Deck, card_types: Mapping[str, CardType]
) -> list[LoadError]:
    """``expect`` 的双向穷尽核对：总数、按牌名、按花色、按类别。"""
    errors: list[LoadError] = []
    expect = deck.expect
    assert expect is not None  # 调用方已判

    actual_total = len(deck.cards)
    if expect.total != actual_total:
        errors.append(
            LoadError(
                file=file,
                loc=f"{base}/expect/total",
                expected=str(expect.total),
                got=str(actual_total),
                rule_id="deck.expect_total",
            )
        )

    dimensions = (
        (
            "type",
            "牌名",
            expect.by_type,
            Counter(card.type for card in deck.cards),
            "deck.expect_by_type",
        ),
        (
            "suit",
            "花色",
            expect.by_suit,
            Counter(card.suit.value for card in deck.cards),
            "deck.expect_by_suit",
        ),
        (
            "category",
            "类别",
            expect.by_category,
            Counter(
                card_types[card.type].category.value
                for card in deck.cards
                if card.type in card_types
            ),
            "deck.expect_by_category",
        ),
    )
    for key, label, declared, actual_counts, rule_id in dimensions:
        if declared is None:
            continue
        for declared_key, declared_count in declared.items():
            actual = actual_counts.get(declared_key, 0)
            if declared_count != actual:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/expect/by_{key}/{declared_key}",
                        expected=str(declared_count),
                        got=str(actual),
                        rule_id=rule_id,
                    )
                )
        if not declared:
            continue
        # 反向：实况出现但期望没声明的键，也是"不相符"。
        for actual_key, actual in actual_counts.items():
            if actual_key not in declared:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/expect/by_{key}/{actual_key}",
                        expected=f"0（期望未声明该{label}）",
                        got=str(actual),
                        rule_id=rule_id,
                    )
                )

    return errors


def validate_content_pack(
    raw: str | bytes | Mapping[str, Any], *, file: str = "<memory>"
) -> ContentPack:
    """校验一份内容包数据；不过则抛 :class:`DataLoadError`（聚合全部错误）。"""
    parsed = _parse_json(file, raw)

    # ① 版本先行：未知即拒，不再报字段错误。
    version_error = _version_error(file, parsed)
    if version_error is not None:
        raise DataLoadError([version_error])

    # ② 结构。
    try:
        pack = ContentPack.model_validate(parsed)
    except ValidationError as exc:
        raise DataLoadError(_structural_errors(file, exc)) from exc

    # ③ 语义。结构没过时不跑（字段本身还不可信），故这里是独立的一层。
    semantic_errors = _semantic_errors(file, pack)
    if semantic_errors:
        raise DataLoadError(semantic_errors)
    return pack


def load_content_pack(
    pack_id: str = DEFAULT_PACK_ID, *, deck_profile: str | None = None
) -> ContentPack:
    """加载打包数据里的内容包（显式调用，import 期零副作用）。

    ``deck_profile`` 缺省用包自带的默认档；显式给出时返回一份把 ``deck_profile``
    改写成该档的副本（**全部档位都已校验**，这里只做选择）。未知档位 fail-closed，
    报与数据层同一条 ``decks.unknown_profile``。
    """
    file = f"files/{pack_id}.json"
    resource = files("sgs.data").joinpath("files", f"{pack_id}.json")
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="存在的内容包文件",
                    got=pack_id,
                    rule_id="pack.not_found",
                )
            ]
        ) from exc
    pack = validate_content_pack(text, file=file)

    if deck_profile is None or deck_profile == pack.deck_profile:
        return pack
    if deck_profile not in pack.decks:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/deck_profile",
                    expected=f"decks 中存在的档位之一：{sorted(pack.decks)}",
                    got=repr(deck_profile),
                    rule_id="decks.unknown_profile",
                )
            ]
        )
    return pack.model_copy(update={"deck_profile": deck_profile})


@lru_cache(maxsize=None)
def _card_category_table(pack_id: str) -> Mapping[str, Category]:
    """``pack_id`` 的 ``card_type_id → 大类`` 只读表（按包缓存；``load_content_pack`` 本身也是缓存的）。"""
    pack = load_content_pack(pack_id)
    return MappingProxyType(
        {card_type.id: card_type.category for card_type in pack.card_types}
    )


def card_category(pack_id: str, type_id: str) -> Category | None:
    """``pack_id`` 内容包里 ``type_id`` 这张牌的**大类**；未知牌名返回 ``None``（M6 票 02）。

    牌名大类（``basic`` / ``trick`` / ``equipment``）是**数据文件**的事实（`03` §2「卡牌数据模型」），
    而运行期只有"这张牌是锦囊吗"这一个读取方（【集智】的非延时锦囊 / 【奇才】的锦囊）：
    两者都拿 ``CardRef.type`` 来问，故这里按 ``pack_id`` 建一张只读表。

    缓存只按 ``pack_id`` 分桶：``card_types`` 与 ``deck_profile`` 无关（档位只改牌堆组成）。
    查不到牌名**不抛错**——fail-closed 返回 ``None``，调用方按"不是锦囊"处理（虚拟牌 /
    未来新增牌名漏配时是"不发动"，不是崩）。
    """
    return _card_category_table(pack_id).get(type_id)


# ---------------------------------------------------------------------------
# 武将表：``heroes.<pack_id>.json`` 的定位、三段校验与只读查询（票 01）
# ---------------------------------------------------------------------------


def hero_skill_id(hero_id: str, skill_id: str) -> str:
    """武将技能 id 的**唯一派生处**：``hero.<武将 id>.<短 id>``（票 01 §Data）。

    加载期的引用校验与派发期的载体解析都走这里，故前缀不会在两处各抄一遍。
    """
    return f"{HERO_SKILL_PREFIX}{hero_id}.{skill_id}"


def _hero_semantic_errors(file: str, table: HeroTable) -> list[LoadError]:
    """武将表的语义校验：结构 / 唯一性（全体）+ 技能引用（**仅启用的武将**）。

    条件化是本票的核心（票 01 §Data #3；`03` §7「内容包与版本」的启用门控）：数据文件装全
    25 名，不因"还没实现"而砍表；
    ``enabled`` 的武将才要求 ``hero.<id>.<skill>`` 在 ``skills.CATALOG`` 里闭合，
    未启用的只做结构、唯一性与"≥1 技能"校验。``CATALOG`` 在函数体内导入——
    与 :func:`_card_type_errors` 同姿态，避免 ``data ↔ skills`` 的模块级环。
    """
    from sgs.skills import CATALOG

    errors: list[LoadError] = []

    if not table.heroes:
        errors.append(
            LoadError(
                file=file,
                loc="/heroes",
                expected="非空的武将列表",
                got="0 条",
                rule_id="heroes.empty",
            )
        )

    seen_ids: set[str] = set()
    seen_versions: dict[tuple[str, str], int] = {}
    """（真名, 版本）→ 首次出现的条目下标：同一真名的同一版本只许有一条记录。"""
    for index, hero in enumerate(table.heroes):
        base = f"/heroes/{index}"

        # M6 票 12：真名是配将的分组键。同一真名的**同一版本**出现两条记录时，"这一版是
        # 哪名武将"就不再唯一；跨表的同一真名（标准关羽 / 界关羽）是不同版本，不受限。
        version_key = (hero.true_name, hero.version)
        first_index = seen_versions.get(version_key)
        if first_index is not None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/true_name",
                    expected=(
                        f"同一真名的同一版本只出现一次（真名「{hero.true_name}」/ 版本 "
                        f"{hero.version!r} 已在 /heroes/{first_index} 出现）"
                    ),
                    got=repr(hero.true_name),
                    rule_id="heroes.duplicate_true_name_version",
                )
            )
        else:
            seen_versions[version_key] = index

        if hero.id in seen_ids:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/id",
                    expected="全局唯一的武将 id",
                    got=repr(hero.id),
                    rule_id="heroes.duplicate_id",
                )
            )
        seen_ids.add(hero.id)

        if hero.hp_max < 1:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/hp_max",
                    expected="正整数（体力上限）",
                    got=repr(hero.hp_max),
                    rule_id="hero.hp_max_range",
                )
            )

        if not hero.skills:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/skills",
                    expected="每名武将至少一个技能",
                    got="0 条",
                    rule_id="hero.skills_empty",
                )
            )

        seen_skills: set[str] = set()
        for skill_index, skill in enumerate(hero.skills):
            if skill.id in seen_skills:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}/id",
                        expected="同一武将内唯一的技能 id",
                        got=repr(skill.id),
                        rule_id="hero.skill_duplicate_id",
                    )
                )
            seen_skills.add(skill.id)

            if not hero.enabled:
                continue
            full_id = hero_skill_id(hero.id, skill.id)
            definition = CATALOG.get(full_id)
            if definition is None:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}/id",
                        expected=f"技能表里存在 {full_id}（已启用武将的技能引用必须闭合）",
                        got=repr(skill.id),
                        rule_id="hero.skill_missing",
                    )
                )
                continue
            # M6 票 10：放置类技能的三项声明（可见性 / 归还时机 / 附加效果）在**数据**里，
            # "这条技能会往武将牌上放牌"在**代码**里（SkillDef.places_on_hero_card）。
            # 两半必须同时成立：代码标了而数据没写 ⇒ 放置区没人负责归还；数据写了而代码
            # 没标 ⇒ 声明了一条不存在的机制。两项都 fail-closed（D14 / S8 登记表检查）。
            if definition.places_on_hero_card and skill.placement is None:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}",
                        expected="placement：可见性 / 归还时机 / 有无附加效果三项声明",
                        got="缺失",
                        rule_id="heroes.placement_missing",
                    )
                )
            elif skill.placement is not None and not definition.places_on_hero_card:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}/placement",
                        expected=f"{full_id} 是登记在案的放置类技能（SkillDef.places_on_hero_card）",
                        got="声明了放置、技能定义没有登记",
                        rule_id="heroes.placement_not_registered",
                    )
                )

        # M6 票 06：☆ 的 ``granted_by`` 必须指向**同一武将**的一条**觉醒技**。
        # 与技能引用闭合分开核对：觉醒技本身就在这份清单里，故不依赖"该技能已在
        # ``CATALOG`` 里闭合"（那条只对启用的武将查）。
        declared = {skill.id: skill for skill in hero.skills}
        for skill_index, skill in enumerate(hero.skills):
            if skill.granted_by is None:
                continue
            target = declared.get(skill.granted_by)
            if target is None:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}/granted_by",
                        expected="同一武将技能清单里的一条觉醒技短 id",
                        got=repr(skill.granted_by),
                        rule_id="heroes.granted_by_unknown",
                    )
                )
            elif target.skill_type is not SkillType.AWAKENING:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/skills/{skill_index}/granted_by",
                        expected=f"「{skill.granted_by}」是觉醒技（☆ 只能由觉醒技获得）",
                        got=target.skill_type.value,
                        rule_id="heroes.granted_by_not_awakening",
                    )
                )

    return errors


def validate_heroes(raw: str | bytes | Mapping[str, Any], *, file: str = "<memory>") -> HeroTable:
    """校验一份武将表数据；不过则抛 :class:`DataLoadError`（聚合全部错误）。

    与 :func:`validate_content_pack` 同姿态的**三段**：版本 → 结构 → 语义，
    任何一层不过都不返回部分数据。
    """
    parsed = _parse_json(file, raw)

    version_error = _version_error(file, parsed, known=KNOWN_HEROES_SCHEMA_VERSIONS)
    if version_error is not None:
        raise DataLoadError([version_error])

    try:
        table = HeroTable.model_validate(parsed)
    except ValidationError as exc:
        raise DataLoadError(_structural_errors(file, exc)) from exc

    semantic_errors = _hero_semantic_errors(file, table)
    if semantic_errors:
        raise DataLoadError(semantic_errors)
    return table


@lru_cache(maxsize=None)
def load_heroes(pack_id: str = DEFAULT_PACK_ID) -> HeroTable:
    """加载打包数据里的武将表（``files/heroes.<pack_id>.json``）。

    纯函数式缓存：文件是**只读进料**，同 ``pack_id`` 恒得同一张已校验的表，
    故派发热路径可以反复取用而不重读磁盘。缺文件 fail-closed，报 ``heroes.not_found``。
    """
    file = f"files/heroes.{pack_id}.json"
    resource = files("sgs.data").joinpath("files", f"heroes.{pack_id}.json")
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="存在的武将表文件",
                    got=pack_id,
                    rule_id="heroes.not_found",
                )
            ]
        ) from exc
    return validate_heroes(text, file=file)


def load_hero_tables(pack_id: str = DEFAULT_PACK_ID) -> tuple[HeroTable, ...]:
    """``pack_id`` 的武将表**连同扩展武将包**（M6 票 04）：``(标准表, 界版表, …)``。

    顺序 = :data:`HERO_SUPPLEMENT_PACK_IDS` 的声明序；每一份都各自走三段校验并各自缓存
    （与 :func:`load_heroes` 同姿态）。这是"分开存、同一读取面"的唯一定义处：
    :func:`hero_skill_ids` 读它，不在调用方各拼一份表名。
    """
    return tuple(
        load_heroes(table_pack_id)
        for table_pack_id in dict.fromkeys((pack_id, *HERO_SUPPLEMENT_PACK_IDS))
    )


def hero_by_id(hero_id: str, *, pack_id: str = DEFAULT_PACK_ID) -> Hero | None:
    """按 id 跨**全部**武将表取武将（M6 票 12；M6 票 04 的"分开存、同一读取面"）。

    配将的缺省将池是标准版 + 界版的并集，故开局初始化不能再只查内容包那张表：
    ``build_initial`` 按抽到的 id 找武将数据（体力 / 势力 / 性别 / 真名）时走这里。
    与 :func:`hero_skill_ids` 同姿态——未知 id 返回 ``None``，调用方 fail-closed。
    """
    for table in load_hero_tables(pack_id):
        hero = table.by_id(hero_id)
        if hero is not None:
            return hero
    return None


def hero_skill_ids(hero_id: str, *, pack_id: str = DEFAULT_PACK_ID) -> tuple[str, ...]:
    """某武将的**全限定技能 id**（``hero.<武将 id>.<短 id>``，按数据文件里的顺序）。

    查表面 = :func:`load_hero_tables`（内容包的表 + 扩展武将包的表）：界版武将与标准版
    走同一道载体解析，故"两名界将可用"不需要第二份查找路径。
    未知 ``hero_id`` 返回空元组：调用方（载体解析）与"装备牌查不到技能定义即跳过"
    同姿态，不因一处未知引用把整局打断；数据侧的"漏配"由加载期校验负责拦。
    """
    for table in load_hero_tables(pack_id):
        hero = table.by_id(hero_id)
        if hero is not None:
            return tuple(hero_skill_id(hero.id, skill.id) for skill in hero.skills)
    return ()


@lru_cache(maxsize=None)
def hero_placement_decls(pack_id: str = DEFAULT_PACK_ID) -> Mapping[str, PlacementDecl]:
    """**放置三项声明**的唯一读取面（M6 票 10；spec D14 / S8）。

    返回 ``全限定技能 id → PlacementDecl``（只含**声明了** ``placement`` 的条目）。
    引擎的"当前回合结束时整批归还"按它取 ``return_timing`` / ``extra_effect``；测试的
    登记表检查按它核"每个 ``SkillDef.places_on_hero_card`` 都有一份声明"。
    """
    found: dict[str, PlacementDecl] = {}
    for table in load_hero_tables(pack_id):
        for hero in table.heroes:
            for skill in hero.skills:
                if skill.placement is not None:
                    found[hero_skill_id(hero.id, skill.id)] = skill.placement
    return MappingProxyType(found)


def hero_placement_decl(
    skill_id: str, *, pack_id: str = DEFAULT_PACK_ID
) -> PlacementDecl | None:
    """一条技能的三项声明；不是放置类技能（或数据里没有它）返回 ``None``。"""
    return hero_placement_decls(pack_id).get(skill_id)


@lru_cache(maxsize=None)
def hero_card_skill_ids(hero_id: str, *, pack_id: str = DEFAULT_PACK_ID) -> tuple[str, ...]:
    """某武将**武将牌上**（非 ☆）的全限定技能 id，按数据文件里的顺序（M6 票 06）。

    与 :func:`hero_skill_ids` 的差别只有一处：**排除 ``granted_by`` 非空的 ☆ 条目**。
    载体解析的武将牌那一路读本函数——☆ 技能的实例只来自"获得"来源
    （``Player.granted_skills``），武将牌不产出它，故未觉醒时"没有这个技能"是结构事实，
    不靠条件在派发期挡。``hero_skill_ids`` 仍回答"这名武将总共带哪些技能"（加载期闭合
    校验与界面清单读它），两者共用同一份数据、不各抄一份名单。

    **纯函数 + 进程级缓存**（票 16）：返回值只依赖 ``(hero_id, pack_id)``，而武将表由
    :func:`load_heroes` 缓存、进程内不变，故 ``lru_cache`` 零语义风险——它是载体解析的
    最热读取面（每局上万次），命中时省掉"遍历武将表 + 重建 tuple"。
    """
    for table in load_hero_tables(pack_id):
        hero = table.by_id(hero_id)
        if hero is not None:
            return tuple(
                hero_skill_id(hero.id, skill.id)
                for skill in hero.skills
                if skill.granted_by is None
            )
    return ()


def enabled_hero_ids(
    table: HeroTable | None = None, *, pack_id: str = DEFAULT_PACK_ID
) -> tuple[str, ...]:
    """**已启用**武将的 id（按数据文件顺序）；缺省读**全部**武将表（M6 票 12）。

    这是启用门控的**只读面**：发放段用它当"本局可发的武将池"的缺省来源，而
    :func:`validate_heroes` 用同一份 ``enabled`` 字段做条件化引用校验——两处读同一字段，
    不各抄一份名单。

    ``table`` 入参让"派生"这条判据能对一份注入的表单独验证（仍只读那一张表）；不给时
    读 :func:`load_hero_tables` 的**并集**（内容包表 + 扩展武将包）——票 12 起默认将池
    是标准版与界版的全部启用版本（spec D19 的混合局），只读内容包那张表会让界将永远发
    不出来。顺序 = 表的声明序、表内 = 数据文件顺序，故并集也是确定的。
    """
    tables = load_hero_tables(pack_id) if table is None else (table,)
    return tuple(hero.id for source in tables for hero in source.heroes if hero.enabled)
