"""描述全文（U1-04b）：独立内容文件 ``descriptions.json`` 的加载与查表。

**为什么单列一份内容文件**：

- 文案是**表现内容**，不是规则数据——它不进 ``src/sgs/`` 的牌名 / 技能定义，也不进
  记录流与录像（金录像因此逐字节不变）；
- 一份文件里同时放**牌名**（键 = :class:`~sgs.data.CardType` 的 ``id``）与
  **技能**（键 = :class:`~sgs.skills.SkillDef` 的 ``skill_id``），界面只需要一个来源；
- 缺条目时**显式**返回 :data:`DESCRIPTION_FALLBACK`（“暂无描述”），不返回空串——
  空串会让界面静默留白，看起来像渲染坏了。

**覆盖**：43 个牌名（标准 32 + 军争 11，M5-01a 起同一内容包）；99 条技能 =
``CATALOG`` 的全部装备技能（22）与武将技能（77，M6 票 15 后）。覆盖由
``tests/test_web.py::test_every_card_name_and_skill_id_has_a_description`` 逐 key 核。

**消费方**：服务端展示层把它并入载荷——:func:`sgs_web.display.card_display` 给每张牌加
``description``，:func:`sgs_web.projection.build_skills` 给每条技能加 ``description``；
前端读取口是 ``static/core.js`` 的 ``cardDescription`` / ``skillDescription``，调用点
在 ``static/interaction.js:cardChip``（牌面悬停）与 ``static/table.js:renderSkills``
（技能悬停），文案不在 JS 里抄第二份。

**裁切规则（ui-one-screen 票 01，spec §8 R7）**：内容文件里的正文后面常跟一段
实现注记（以「本仓」起头、以全角冒号收尾），它是给协作者的，不是给玩家的。裁切口就住在本模块：
:func:`trimmed_description` / :func:`display_skill_description` 是**唯一**实现——
服务端视图层（``serve.py`` / ``script_runs.py``）只调这两个口，前端（``static/``）零正则、
零第二份文案来源。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

__all__ = [
    "CARD_DESCRIPTIONS",
    "DESCRIPTION_FALLBACK",
    "DESCRIPTIONS_PATH",
    "NOTE_TRIM_RE",
    "SCHEMA_VERSION",
    "SKILL_DESCRIPTIONS",
    "card_description",
    "display_skill_description",
    "skill_description",
    "trimmed_description",
]

DESCRIPTION_FALLBACK: Final[str] = "暂无描述"
"""缺条目时给人看的显式文案（界面契约的一部分；不要改成空串）。"""

SCHEMA_VERSION: Final[int] = 1
"""内容文件的结构版本；改键形 / 表结构时升版。"""

DESCRIPTIONS_PATH: Final[Path] = Path(__file__).resolve().with_name("descriptions.json")
"""内容文件路径（``tools/sgs_web/descriptions.json``，与加载器同目录）。"""


def _load_table(document: Mapping[str, Any], key: str) -> Mapping[str, str]:
    """读 ``document[key]`` 的 ``{id: 全文}`` 表；结构与空值在**加载期**就报错。"""
    table = document.get(key)
    if not isinstance(table, dict):
        raise ValueError(f"{DESCRIPTIONS_PATH.name} 缺 {key!r} 表（或它不是对象）")
    entries: dict[str, str] = {}
    for entry_key, text in table.items():
        if not isinstance(entry_key, str) or not entry_key:
            raise ValueError(f"{DESCRIPTIONS_PATH.name}:{key} 的键必须是非空字符串：{entry_key!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"{DESCRIPTIONS_PATH.name}:{key}.{entry_key} 的全文必须是非空字符串")
        entries[entry_key] = text
    return MappingProxyType(entries)


def _load() -> dict[str, Any]:
    """读一次内容文件并校验顶层结构（失败即启动期报错，不留半份内容）。"""
    try:
        document = json.loads(DESCRIPTIONS_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"读不到描述内容文件：{DESCRIPTIONS_PATH}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{DESCRIPTIONS_PATH.name} 不是合法 JSON：{error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{DESCRIPTIONS_PATH.name} 的顶层必须是对象")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{DESCRIPTIONS_PATH.name} 的 schema_version 应为 {SCHEMA_VERSION}，"
            f"实得 {document.get('schema_version')!r}"
        )
    return document


_DOCUMENT = _load()

CARD_DESCRIPTIONS: Mapping[str, str] = _load_table(_DOCUMENT, "cards")
"""``CardType.id`` → 描述全文（43 条：标准 32 + 军争 11）。"""

SKILL_DESCRIPTIONS: Mapping[str, str] = _load_table(_DOCUMENT, "skills")
"""``SkillDef.skill_id`` → 描述全文（99 条：装备 22 + 武将 77）。"""

NOTE_TRIM_RE: Final = re.compile(r"本仓[^：]*：")
"""实现注记的起始标记（**裁切规则的唯一真源**，前端与服务端视图层都不许再抄一份）。

对 ``descriptions.json`` 的实测依据（``prototype/README.md``「裁切规则的实测依据」）：
标记有三种家族与多种带限定语的变体，其中一条括号**没闭合**（``…（口径 = **移动版**：``）
——按括号配对写的正则会漏掉它，故只能用 ``[^：]*``；没有任何条目含两个标记 ⇒「从第一个匹配
处裁到末尾」无歧义。
"""


def trimmed_description(text: str) -> str:
    """从**第一个** :data:`NOTE_TRIM_RE` 匹配处裁到字符串末尾；无匹配原样返回。"""
    match = NOTE_TRIM_RE.search(text)
    return text if match is None else text[: match.start()]


def card_description(card_type: str | None) -> str:
    """牌名 id → 描述全文；未知 / 空 key 显式返回「暂无描述」。"""
    if not isinstance(card_type, str) or not card_type:
        return DESCRIPTION_FALLBACK
    return CARD_DESCRIPTIONS.get(card_type, DESCRIPTION_FALLBACK)


def skill_description(skill_id: str | None) -> str:
    """技能 id → 描述全文；键就是 ``CATALOG`` 的完整 ``skill_id``。

    询问载荷里还会出现**合成键**（如 ``yiji:give:0:0``，由技能帧拼出来），它不是
    ``SkillDef.skill_id``，界面的技能清单 / 详情也不拿它查文案，故这里**不做**前缀猜测；
    未知 / 空 key 一律显式返回「暂无描述」。
    """
    if not isinstance(skill_id, str) or not skill_id:
        return DESCRIPTION_FALLBACK
    return SKILL_DESCRIPTIONS.get(skill_id, DESCRIPTION_FALLBACK)


def display_skill_description(skill_id: str | None) -> str:
    """技能 id → **给人看的**描述（:func:`skill_description` 的全文按注记裁过）。

    这是视图层（``serve.py`` / ``script_runs.py``）取展示文案的唯一口；未知 / 空 key 与
    合成键（如 ``yiji:give:0:0``）走 :func:`skill_description` 的同一兜底口径，不猜前缀。
    """
    return trimmed_description(skill_description(skill_id))
