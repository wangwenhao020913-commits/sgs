"""技能价值表：成长层的**元层**进料（M7 票 15）。

一张表两用（商店价 = 满包折价）+ 5 个本地预设 + 学习池查询；依据 **票 02 §4–§5**。

⚠ 与 **AI 的 ``SKILL_VALUES``**（``src/sgs/agents/heuristic.py`` 的**牌名价值表**）不是一回事：
那个回答"这张牌多有用"，本模块回答"这条技能值多少铜钱、抽不抽得到"。两者名字相近，
读代码时以 ``data.values`` / ``ValueTable`` 区分。

三条设计事实（它们互相咬合，改一条要同时看另外两条）：

- **仓内默认表是显式清单**（``files/skill-values.json``）：登记**现有全部已实现的武将技能**
  （默认启用、稀有度先全「普通」占位）+ 遗物段。**不在清单里 = 普通 + 禁用**——
  "新技能默认普通 + PVE 禁用"因此是 :func:`skill_value` 的缺省语义，不靠人记得改表；
  而**清单里写错 id 要拒**（语义门），否则"默认禁用"会把拼写错误藏起来。
  清单口径**不看武将的 ``enabled``**（那是发放门控，见 ``spec.md`` §5.9"剧本点名无视
  ``enabled``"）：把一名武将下架不该让整张表加载失败；只看"**已实现**"（id 在 ``CATALOG``）。
  遗物段按 **票 26 的六条**登记（``relic.*``，只有启用 / 禁用、没有稀有度）；写进来的 id
  必须是**载体为 ``RELIC``** 的技能（``CarrierKind.RELIC``，票 26 落地的第五种载体），
  冒充的装备 / 标记 / 武将技能由语义门拒。
- **价与权重不落表**（:data:`RARITY_PRICES` / :data:`RELIC_PRICE` / :data:`RARITY_WEIGHTS`
  是代码里的纪律常数）：店里卖多少钱、满包折多少、抽取权重都是规则，不是内容；
  表里出现 ``price`` 一类的键由结构门（``extra="forbid"``）当场拒绝，堵死"第二份真相"。
- **5 个本地预设只存与默认表的差异**（启用集合 + 稀有度覆盖），落 ``runs/`` 一份 JSON
  （文件名见 :data:`SKILL_VALUE_PRESETS_FILENAME`；**路径由入口层给**，本模块不猜 cwd）。
  读回后 :func:`merge_skill_values` 与默认表合并得到生效表。预设是**本地便利状态**：
  读坏 / 版本认不出 ⇒ 5 个空槽（与将池预设同口径，坏档不静默只约束存档），
  写入则 fail-closed（半个槽位比没有更难查）。

"表的冻结 / 只读语义（存档存在 ⇒ 预设只读）"属票 19–20 的入口层，这里只交数据面与查询。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from enum import Enum
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, model_validator

from .errors import DataLoadError, LoadError
from .loader import _parse_json, _structural_errors, _version_error

__all__ = [
    "RELIC_PRICE",
    "RARITY_PRICES",
    "RARITY_WEIGHTS",
    "SKILL_VALUE_PRESET_SLOTS",
    "SKILL_VALUE_PRESETS_FILENAME",
    "SKILL_VALUE_PRESETS_SCHEMA_VERSION",
    "SKILL_VALUES_FILENAME",
    "SKILL_VALUES_SCHEMA_VERSION",
    "Rarity",
    "SkillValue",
    "SkillValueOverride",
    "SkillValuePreset",
    "SkillValuePresets",
    "SkillValueTable",
    "diff_skill_values",
    "learning_pool",
    "load_skill_value_presets",
    "load_skill_values",
    "merge_skill_values",
    "save_skill_value_preset",
    "skill_value",
    "validate_skill_values",
]

SKILL_VALUES_FILENAME: Final = "skill-values.json"
"""仓内默认表的文件名（住在 ``sgs/data/files/`` 单层目录：随包、不用改 ``pyproject.toml``）。"""

SKILL_VALUES_SCHEMA_VERSION: Final = 1
"""价值表认得的 ``schema_version``；认不出就**报错重来**（不做兼容、不迁移）。"""

SKILL_VALUE_PRESETS_FILENAME: Final = "skill-value-presets.json"
"""本地面板预设的文件名（落点由入口层钉在 ``runs/``，与 ``hero-presets.json`` 同处）。"""

SKILL_VALUE_PRESETS_SCHEMA_VERSION: Final = 1
"""预设文件认得的 ``schema_version``；认不出 ⇒ 当"没有预设"（本地便利状态，不炸面板）。"""

SKILL_VALUE_PRESET_SLOTS: Final = 5
"""预设槽位数（票 02 §4「本地 5 个预设」；与将池预设的 5 个常驻槽位同形）。"""


class Rarity(str, Enum):
    """技能稀有度（票 02 §4）：只影响抽取与铜钱，**不影响战斗数值**。

    取值用英文（数据文件口径），显示名归界面层；四个成员与两张常数表一一对应
    （测试钉住"每个稀有度都有价、也有权重"，加第五档时加载器不用改，但这两张表会红）。
    """

    COMMON = "common"
    """普通。"""

    RARE = "rare"
    """稀有。"""

    EPIC = "epic"
    """史诗。"""

    LEGENDARY = "legendary"
    """传说。"""


RARITY_PRICES: Mapping[Rarity, int] = MappingProxyType(
    {
        Rarity.COMMON: 3,
        Rarity.RARE: 6,
        Rarity.EPIC: 15,
        Rarity.LEGENDARY: 30,
    }
)
"""**商店价 = 满包折价**（票 02 §4：一张表两用，不设第二个价格字段）。"""

RELIC_PRICE: Final = 10
"""遗物固定售价（票 02 §4）；遗物**不可折铜钱**——那条属于票 17 的发奖逻辑，不在这里。"""

_RELIC_CARRIER: Final = "RELIC"
"""遗物技能的**载体名**（``CarrierKind.RELIC`` 的取值名，票 26 落地）。

``relics`` 段只收**载体为 ``RELIC``** 的 id：六条 ``relic.*`` 之外（武将技 / 装备 / 标记 /
牌标签效果）写进来都会被语义门按"不是遗物技能"拒。按**载体名**比而不是按 id 前缀比——
前缀可以被任何表抄错，载体是技能定义上的字段（``CarrierKind``）。
"""

RARITY_WEIGHTS: Mapping[Rarity, int] = MappingProxyType(
    {
        Rarity.COMMON: 60,
        Rarity.RARE: 25,
        Rarity.EPIC: 12,
        Rarity.LEGENDARY: 3,
    }
)
"""抽取权重（票 02 §4）：三选一与商店库存用同一套，逐张不放回。"""


class SkillValue(BaseModel):
    """价值表里**一行**：启用 + 稀有度（不逐条填铜钱数）。

    名字里的"价值"指它属于**价值表**（票 02 §4 的术语），不是"这条技能有多强"——
    价格与抽取权重是代码里的常数（:data:`RARITY_PRICES` / :data:`RARITY_WEIGHTS`），
    一行只装"能不能拿到"与"多稀有"。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: StrictBool
    """是否进学习池 / 商店 / 事件奖励。``False`` = 该技能在 PvE 里不出现（不是删掉）。"""

    rarity: Rarity
    """稀有度；出厂全「普通」，默认分布是内容期的事（票 15 备注）。"""


class SkillValueTable(BaseModel):
    """一份**已通过三段门**的价值表（``files/skill-values.json``）。

    ``skills`` 只收武将技能（``equip.*`` / 遗物技能 / NPC 技能都不在这里）；
    ``relics`` 只有启用与否（无稀有度、不进抽取池），id 必须是**载体为 ``RELIC`` 的遗物技能**
    （M7 票 26 的六条 ``relic.*``；判据见 :data:`_RELIC_CARRIER`）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    skills: Mapping[str, SkillValue]
    relics: Mapping[str, StrictBool]

    def enabled_skill_ids(self) -> tuple[str, ...]:
        """已启用的武将技能 id（表内顺序）。"""
        return tuple(
            skill_id for skill_id, value in self.skills.items() if value.enabled
        )


class SkillValueOverride(BaseModel):
    """预设里对**一行**的覆盖：只写改过的字段（``enabled`` / ``rarity`` 至多两个）。

    空对象不是差异（:func:`diff_skill_values` 不会产出它，写入口也会拒），
    故"至少一项"在构造期就 fail-closed。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: StrictBool | None = None
    rarity: Rarity | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "SkillValueOverride":
        if self.enabled is None and self.rarity is None:
            raise ValueError("预设覆盖至少要有 enabled / rarity 之一（空对象不是差异）")
        return self


class SkillValuePreset(BaseModel):
    """一个预设槽位 = 与默认表的**差异**（启用集合 + 稀有度覆盖），除此不装任何东西。

    ``relics`` 只存"与默认表不同的启用与否"（遗物没有稀有度）。
    差异里的 id 必须能在默认表里找到（:func:`merge_skill_values` 校验）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    skills: Mapping[str, SkillValueOverride] = Field(default_factory=dict)
    relics: Mapping[str, StrictBool] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        """这个槽位有没有覆盖任何东西。"""
        return not self.skills and not self.relics


class SkillValuePresets(BaseModel):
    """预设文件（``skill-value-presets.json``）：版本 + **恰好 5 个槽位**。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    slots: tuple[SkillValuePreset, ...]

    @model_validator(mode="after")
    def _exactly_five_slots(self) -> "SkillValuePresets":
        if len(self.slots) != SKILL_VALUE_PRESET_SLOTS:
            raise ValueError(
                f"预设必须恰好 {SKILL_VALUE_PRESET_SLOTS} 槽，实得 {len(self.slots)}"
            )
        return self


def skill_value(table: SkillValueTable, skill_id: str) -> SkillValue:
    """一条技能的价值行；**不在清单里 = 普通 + 禁用**（票 02 §4 的默认语义）。

    这条缺省是"新技能默认普通 + PVE 禁用"的**唯一**实现处：读取方不许各自写
    ``table.skills.get(...) or default``，否则"默认值"会有第二份定义。
    """
    return table.skills.get(skill_id) or SkillValue(
        enabled=False, rarity=Rarity.COMMON
    )


def learning_pool(
    table: SkillValueTable,
    *,
    backpack: Iterable[str] = (),
    equipped: Iterable[str] = (),
) -> tuple[str, ...]:
    """**学习池** = 表里已启用的武将技能 − 已拥有（背包 ∪ 出战），按表内顺序（票 02 §3）。

    已拥有按 ``skill_id`` 去重：同一个技能同时躺在背包与出战槽里也只减一次。
    "只收武将技能"由**表**保证（``skills`` 段只装武将技能，加载器拒 ``equip.*`` /
    遗物 / 标记）；本函数不按前缀过滤，否则会造出第二份"什么算武将技能"的定义。
    """
    owned = frozenset(backpack) | frozenset(equipped)
    return tuple(
        skill_id for skill_id in table.enabled_skill_ids() if skill_id not in owned
    )


@lru_cache(maxsize=None)
def _implemented_hero_skill_ids() -> frozenset[str]:
    """语义门的**合法 id 集合** = 武将牌上、且**已在 ``CATALOG`` 实现**的技能。

    两条判据各回答一件事，缺一不可：

    - "武将牌上有没有这条"由**武将表**回答（不按前缀猜）：``CATALOG`` 里另外两条
      ``hero.*`` 条目是标记 / 牌标签效果（``*_mark``），不是武将技能；
    - "实现了没有"由 ``CATALOG`` 回答：没实现的技能写进清单就会被抽到，而它跑不起来。

    **不看武将的 ``enabled``**：那只是发放门控（同 ``spec.md`` §5.9 的"剧本点名无视
    ``enabled``"），把一名武将下架不该让整张价值表加载失败。这与 ``tools/pool_census.py``
    的"启用武将技能集"在**今天**是同一个 212 条（每个带技能的武将都启用着），口径差异只在
    将来有人下架武将时显现——那时清单里已实现的技能仍然合法。
    """
    from sgs.skills import CATALOG

    from .loader import hero_card_skill_id, load_hero_tables

    found: set[str] = set()
    for table in load_hero_tables():
        for hero in table.heroes:
            for skill in hero.skills:
                skill_id = hero_card_skill_id(hero, skill)
                if skill_id in CATALOG:
                    found.add(skill_id)
    return frozenset(found)


def _semantic_errors(file: str, table: SkillValueTable) -> list[LoadError]:
    """结构通过之后的语义校验；坏 id **一次报完**（``loc`` 指向表里的那个键）。

    ``CATALOG`` 在函数体内导入：与 ``loader._hero_semantic_errors`` 同姿态，避免
    ``data ↔ skills`` 的模块级环。
    """
    from sgs.skills import CATALOG

    errors: list[LoadError] = []
    implemented = _implemented_hero_skill_ids()
    for skill_id in table.skills:
        if skill_id not in implemented:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/skills/{skill_id}",
                    expected="武将牌上**已实现**的技能 id（``hero.<武将>.<短 id>``，且在 CATALOG 里）",
                    got=skill_id,
                    rule_id="skill_values.unknown_skill",
                )
            )
    for relic_id in table.relics:
        definition = CATALOG.get(relic_id)
        if definition is None:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/relics/{relic_id}",
                    expected="``CATALOG`` 里登记的技能 id",
                    got=relic_id,
                    rule_id="skill_values.unknown_relic",
                )
            )
        elif definition.carrier.value != _RELIC_CARRIER:
            errors.append(
                LoadError(
                    file=file,
                    loc=f"/relics/{relic_id}",
                    expected=f"遗物技能 id（载体 ``{_RELIC_CARRIER}``，如 ``relic.*``）",
                    got=relic_id,
                    rule_id="skill_values.not_a_relic",
                )
            )
    return errors


def validate_skill_values(
    raw: str | bytes | Mapping[str, Any], *, file: str = "<memory>"
) -> SkillValueTable:
    """校验一份价值表；不过则抛 :class:`DataLoadError`（聚合全部错误）。

    与内容包 / 武将表同一条**版本 → 结构 → 语义**三段式（复用 ``loader`` 的三件工具：
    同一套错误码与 ``LoadError`` 五要素，不在这里抄第二份）。
    """
    parsed = _parse_json(file, raw)

    version_error = _version_error(
        file, parsed, known=frozenset({SKILL_VALUES_SCHEMA_VERSION})
    )
    if version_error is not None:
        raise DataLoadError([version_error])

    try:
        table = SkillValueTable.model_validate(parsed)
    except ValidationError as exc:
        raise DataLoadError(_structural_errors(file, exc)) from exc

    semantic_errors = _semantic_errors(file, table)
    if semantic_errors:
        raise DataLoadError(semantic_errors)
    return table


@lru_cache(maxsize=None)
def load_skill_values() -> SkillValueTable:
    """加载打包数据里的价值表（``files/skill-values.json``）。

    纯函数式缓存：文件是**只读进料**，恒得同一张已校验的表（与 ``load_heroes`` 同姿态）。
    """
    file = f"files/{SKILL_VALUES_FILENAME}"
    resource = files("sgs.data").joinpath("files", SKILL_VALUES_FILENAME)
    try:
        text = resource.read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError) as exc:
        raise DataLoadError(
            [
                LoadError(
                    file=file,
                    loc="/",
                    expected="存在的价值表文件",
                    got=SKILL_VALUES_FILENAME,
                    rule_id="skill_values.not_found",
                )
            ]
        ) from exc
    return validate_skill_values(text, file=file)


def _changed_fields(
    value: SkillValue,
    *,
    enabled: bool | None,
    rarity: Rarity | None,
) -> tuple[bool | None, Rarity | None]:
    """只留**真的改了**的字段；与表相同的字段折成 ``None``（差异格式的唯一实现处）。

    三个读取方各要不同的处置（写入口**拒**、读取时**丢**、求差时**略**），但"什么算一次
    差异"只有这一份判据——三处各写一遍，迟早会有一处对"未改"的判定漂开。
    """
    changed_enabled = enabled if enabled is not None and enabled != value.enabled else None
    changed_rarity = rarity if rarity is not None and rarity != value.rarity else None
    return changed_enabled, changed_rarity


def _require_known_overrides(base: SkillValueTable, preset: SkillValuePreset) -> None:
    """覆盖只许引用默认表里**已有**的 id：差异不是"新增一行"（新增行只能改默认表）。"""
    for skill_id in preset.skills:
        if skill_id not in base.skills:
            raise ValueError(f"预设覆盖了默认表里没有的技能：{skill_id}")
    for relic_id in preset.relics:
        if relic_id not in base.relics:
            raise ValueError(f"预设覆盖了默认表里没有的遗物：{relic_id}")


def _require_real_differences(base: SkillValueTable, preset: SkillValuePreset) -> None:
    """"只存差异"的写入口判据：与默认表相同的条目是冗余，拒掉（不是静默省略）。

    写入口收的就是**差异**（全量快照请先走 :func:`diff_skill_values`）：冗余条目说明调用方
    算错了差异，静默归一化会把它藏起来，而文件仍会长成两种写法。
    """
    for skill_id, override in preset.skills.items():
        value = base.skills[skill_id]
        changed_enabled, changed_rarity = _changed_fields(
            value, enabled=override.enabled, rarity=override.rarity
        )
        if override.enabled is not None and changed_enabled is None:
            raise ValueError(f"预设里的 {skill_id} 启用状态与默认表相同（不是差异）")
        if override.rarity is not None and changed_rarity is None:
            raise ValueError(f"预设里的 {skill_id} 稀有度与默认表相同（不是差异）")
    for relic_id, enabled in preset.relics.items():
        if enabled == base.relics[relic_id]:
            raise ValueError(f"预设里的 {relic_id} 启用状态与默认表相同（不是差异）")


def merge_skill_values(base: SkillValueTable, preset: SkillValuePreset) -> SkillValueTable:
    """**生效表** = 默认表 + 一个槽位的差异（只改覆盖到的字段，其余逐条不变）。"""
    _require_known_overrides(base, preset)
    skills: dict[str, SkillValue] = {}
    for skill_id, value in base.skills.items():
        override = preset.skills.get(skill_id)
        if override is None:
            skills[skill_id] = value
            continue
        skills[skill_id] = SkillValue(
            enabled=value.enabled if override.enabled is None else override.enabled,
            rarity=value.rarity if override.rarity is None else override.rarity,
        )
    relics: dict[str, bool] = {}
    for relic_id, enabled in base.relics.items():
        override_enabled = preset.relics.get(relic_id)
        relics[relic_id] = enabled if override_enabled is None else override_enabled
    return SkillValueTable(
        schema_version=base.schema_version, skills=skills, relics=relics
    )


def diff_skill_values(base: SkillValueTable, effective: SkillValueTable) -> SkillValuePreset:
    """生效表 − 默认表 → 差异（只写改过的字段；两张表的 id 集合必须相同）。"""
    if set(base.skills) != set(effective.skills):
        raise ValueError("两张价值表的武将技能 id 集合不同（差异只覆盖值，不增删行）")
    if set(base.relics) != set(effective.relics):
        raise ValueError("两张价值表的遗物 id 集合不同（差异只覆盖值，不增删行）")
    skills: dict[str, SkillValueOverride] = {}
    for skill_id, value in base.skills.items():
        changed = effective.skills[skill_id]
        enabled, rarity = _changed_fields(
            value, enabled=changed.enabled, rarity=changed.rarity
        )
        if enabled is not None or rarity is not None:
            skills[skill_id] = SkillValueOverride(enabled=enabled, rarity=rarity)
    relics = {
        relic_id: enabled
        for relic_id, enabled in effective.relics.items()
        if enabled != base.relics[relic_id]
    }
    return SkillValuePreset(skills=skills, relics=relics)


def _default_table(base: SkillValueTable | None) -> SkillValueTable:
    """``None`` = 仓内默认表；测试与入口层可以注入一张表（不重复写同一个判空）。"""
    return load_skill_values() if base is None else base


def _empty_presets() -> SkillValuePresets:
    """5 个空槽（每次新建，避免调用方共享同一份可变列表）。"""
    return SkillValuePresets(
        schema_version=SKILL_VALUE_PRESETS_SCHEMA_VERSION,
        slots=tuple(SkillValuePreset() for _ in range(SKILL_VALUE_PRESET_SLOTS)),
    )


def _clean_preset(base: SkillValueTable, preset: SkillValuePreset) -> SkillValuePreset:
    """读取时的语义清理：表里没有的 id 与"其实没差异"的条目直接丢掉（本地状态容忍过期）。"""
    skills: dict[str, SkillValueOverride] = {}
    for skill_id, override in preset.skills.items():
        value = base.skills.get(skill_id)
        if value is None:
            continue
        enabled, rarity = _changed_fields(
            value, enabled=override.enabled, rarity=override.rarity
        )
        if enabled is None and rarity is None:
            continue
        skills[skill_id] = SkillValueOverride(enabled=enabled, rarity=rarity)
    relics = {
        relic_id: enabled
        for relic_id, enabled in preset.relics.items()
        if relic_id in base.relics and enabled != base.relics[relic_id]
    }
    return SkillValuePreset(skills=skills, relics=relics)


def _slot_payload(preset: SkillValuePreset) -> dict[str, Any]:
    """一个槽位 → 落盘 JSON（只写改过的字段、id 排序）。"""
    skills: dict[str, dict[str, Any]] = {}
    for skill_id in sorted(preset.skills):
        override = preset.skills[skill_id]
        entry: dict[str, Any] = {}
        if override.enabled is not None:
            entry["enabled"] = override.enabled
        if override.rarity is not None:
            entry["rarity"] = override.rarity.value
        skills[skill_id] = entry
    relics = {relic_id: preset.relics[relic_id] for relic_id in sorted(preset.relics)}
    return {"skills": skills, "relics": relics}


def _write_atomic(path: Path, text: str) -> None:
    """先写 ``.tmp`` 再 ``os.replace``（进程被杀不留半份；替换失败时目标还是上一版）。

    这是仓里同一形状的第三份（``tools/sgs_web/serve.py`` 的将池预设、``replay.py`` 的录像）：
    ``src/`` 不能 import ``tools/``（层级方向），把 6 行抽成跨层共享模块不划算——**纪律相同、
    实现各自一段**，别再新增第四份。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def load_skill_value_presets(
    path: Path, *, base: SkillValueTable | None = None
) -> SkillValuePresets:
    """读本地预设（``skill-value-presets.json``）；读不落盘。

    坏文件 / 认不出的 ``schema_version`` / 槽数不对 ⇒ **5 个空槽**：预设是本地便利状态
    （与 ``hero-presets.json`` 同口径），读不动不该把面板炸掉，下一次保存会覆盖它。
    **坏档不静默只约束存档**（票 04 §5）。表里已不存在的 id 与"其实没差异"的条目在读取时丢掉。
    """
    base_table = _default_table(base)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_presets()
    if not isinstance(raw, dict):
        return _empty_presets()
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return _empty_presets()
    if version != SKILL_VALUE_PRESETS_SCHEMA_VERSION:
        return _empty_presets()
    try:
        presets = SkillValuePresets.model_validate(raw)
    except ValidationError:
        return _empty_presets()
    return SkillValuePresets(
        schema_version=SKILL_VALUE_PRESETS_SCHEMA_VERSION,
        slots=tuple(_clean_preset(base_table, slot) for slot in presets.slots),
    )


def save_skill_value_preset(
    *,
    slot: int,
    preset: SkillValuePreset,
    path: Path,
    base: SkillValueTable | None = None,
) -> SkillValuePresets:
    """保存一个槽位（**覆盖**该槽位，不动别的槽位）；返回保存后的全部槽位。

    写入口 fail-closed：槽位越界 / 未知 id / 冗余条目 ⇒ ``ValueError`` 且**不落盘**
    （半份预设比没有更难查）。落盘先 ``.tmp`` 再 ``os.replace``。
    """
    if (
        isinstance(slot, bool)
        or not isinstance(slot, int)
        or not 0 <= slot < SKILL_VALUE_PRESET_SLOTS
    ):
        raise ValueError(
            f"预设槽位必须是 0..{SKILL_VALUE_PRESET_SLOTS - 1} 的整数：{slot!r}"
        )
    base_table = _default_table(base)
    _require_known_overrides(base_table, preset)
    _require_real_differences(base_table, preset)
    current = load_skill_value_presets(path, base=base_table)
    slots = list(current.slots)
    slots[slot] = preset
    updated = SkillValuePresets(
        schema_version=SKILL_VALUE_PRESETS_SCHEMA_VERSION, slots=tuple(slots)
    )
    payload = {
        "schema_version": SKILL_VALUE_PRESETS_SCHEMA_VERSION,
        "slots": [_slot_payload(item) for item in updated.slots],
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    _write_atomic(Path(path), text)
    return updated
