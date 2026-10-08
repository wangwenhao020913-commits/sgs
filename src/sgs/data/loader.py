"""内容包的定位、解析与加载期校验。

校验顺序固定为 **版本 → 结构 → 语义**：

1. 版本：未知 ``schema_version`` 立即拒绝，**不**继续做结构校验 ——
   这样旧/新档错配报的是"版本不符"，而不是一堆误导性的字段错误。
2. 结构：pydantic v2，``extra="forbid"``；全部错误一次报完。
3. 语义：手写检查，在结构通过之后跑（结构没过时字段本身还不可信）。

任何一层不过都抛 :class:`~sgs.data.DataLoadError`，**绝不返回部分数据**。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from importlib.resources import files
from typing import Any

from pydantic import ValidationError

from .errors import DataLoadError, LoadError
from .models import ContentPack, Deck

__all__ = ["DEFAULT_PACK_ID", "KNOWN_SCHEMA_VERSIONS", "load_content_pack", "validate_content_pack"]

KNOWN_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})
"""加载器认得的**数据模型**版本；与事件 schema 版本、规则版本是三件事。"""

DEFAULT_PACK_ID = "standard"

RANK_MIN = 1
RANK_MAX = 13
SLOT_LIMIT = 2
# 同一 (花色,点数) 槽位至多两张：标准版每槽印两张（票 03 §3）。

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

    for profile, deck in pack.decks.items():
        base = f"/decks/{profile}"
        errors.extend(_deck_errors(file, base, deck, seen_ids))

    return errors


def _deck_errors(file: str, base: str, deck: Deck, known_ids: set[str]) -> list[LoadError]:
    errors: list[LoadError] = []
    slot_counts: dict[tuple[str, int], int] = {}

    for index, card in enumerate(deck.cards):
        # 牌名引用只在有定义可查时才逐张判定：card_types 为空是单点根因，已由
        # card_types.empty 报出；再逐张报"引用不存在"只会用 53 条噪声淹没它。
        if known_ids and card.type not in known_ids:
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
        if slot_counts[slot] == SLOT_LIMIT + 1:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/cards/{index}",
                    expected=f"同一 (花色,点数) 槽位至多 {SLOT_LIMIT} 张",
                    got=f"{slot_counts[slot]} 张",
                    rule_id="deck.slot_limit",
                )
            )

    if deck.expect is not None:
        actual_total = len(deck.cards)
        if deck.expect.total != actual_total:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"{base}/expect/total",
                    expected=str(deck.expect.total),
                    got=str(actual_total),
                    rule_id="deck.expect_total",
                )
            )
        actual_by_type = Counter(card.type for card in deck.cards)
        for type_id, declared in deck.expect.by_type.items():
            actual = actual_by_type.get(type_id, 0)
            if declared != actual:
                errors.append(
                    LoadError(
                        file=file,
                        loc=f"{base}/expect/by_type/{type_id}",
                        expected=str(declared),
                        got=str(actual),
                        rule_id="deck.expect_by_type",
                    )
                )
        if deck.expect.by_type:
            # 反向：实况出现但期望没声明的牌名，也是"不相符"。
            for type_id, actual in actual_by_type.items():
                if type_id not in deck.expect.by_type:
                    errors.append(
                        LoadError(
                            file=file,
                            loc=f"{base}/expect/by_type/{type_id}",
                            expected="0（期望未声明该牌名）",
                            got=str(actual),
                            rule_id="deck.expect_by_type",
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


def load_content_pack(pack_id: str = DEFAULT_PACK_ID) -> ContentPack:
    """加载打包数据里的内容包（显式调用，import 期零副作用）。"""
    resource = files("sgs.data").joinpath("files", f"{pack_id}.json")
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=f"files/{pack_id}.json",
                    loc="/",
                    expected="存在的内容包文件",
                    got=pack_id,
                    rule_id="pack.not_found",
                )
            ]
        ) from exc
    return validate_content_pack(text, file=f"files/{pack_id}.json")
