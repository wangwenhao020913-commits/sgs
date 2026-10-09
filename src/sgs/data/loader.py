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
from importlib.resources import files
from typing import Any

from pydantic import ValidationError

from .errors import DataLoadError, LoadError
from .models import Category, CardType, ContentPack, Deck

__all__ = [
    "DEFAULT_PACK_ID",
    "KNOWN_SCHEMA_VERSIONS",
    "load_content_pack",
    "validate_content_pack",
]

KNOWN_SCHEMA_VERSIONS: frozenset[int] = frozenset({2})
"""加载器认得的**数据模型**版本；与事件 schema 版本、规则版本是三件事。

升 2 的判据落在"旧加载器读新文件"：``Deck.slot_limit`` / ``CardType.attack_range`` /
``CardType.delayed`` / ``DeckExpect.by_suit`` / ``by_category`` 在旧代码的
``extra="forbid"`` 下是结构错误——这正是该由版本号先拦下的情形（票 09 §7）。
"""

DEFAULT_PACK_ID = "standard"

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


def _version_error(file: str, parsed: Mapping[str, Any]) -> LoadError | None:
    """版本闸门：未知 / 缺失 ``schema_version`` 一律拒绝（fail-closed）。"""
    if "schema_version" not in parsed:
        return LoadError(
            file=file,
            loc="/schema_version",
            expected=f"数据模型版本之一：{sorted(KNOWN_SCHEMA_VERSIONS)}",
            got="缺失",
            rule_id="version.missing",
        )
    value = parsed["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int) or value not in KNOWN_SCHEMA_VERSIONS:
        return LoadError(
            file=file,
            loc="/schema_version",
            expected=f"数据模型版本之一：{sorted(KNOWN_SCHEMA_VERSIONS)}",
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
