"""diy-heroes 票 05：``examples/diy-hero/`` 端到端样板（复用已登记技能，**不进**官方池）。

样板 = 一张**从磁盘读**的 ``heroes.diy.json`` + 旁边的 README：给仓外作者一份"照抄就能用"的
最小武将表。两件事必须同时成立（spec 裁定 4）：

- **真的能用**：``validate_heroes`` 通过，且能坐进一局、两份技能清单列得出【武圣】；
- **不进官方池**：``HERO_SUPPLEMENT_PACK_IDS`` 里没有 ``diy``，``enabled_hero_ids()`` 里没有这张
  牌 ⇒ ``tools/pool_census.py`` 的 15 处计数一处不动。

样板包**故意没注册**进加载面，故"能坐进一局"那条测试必须**显式注入**（``monkeypatch``
``sgs.data.loader.load_hero_tables``）——这正是 README 里"只在测试里注入"那条路的完整例子。
测试只经公开接缝：``validate_heroes`` / ``HeroTable`` / ``enabled_hero_ids`` 与
``seat_skill_display`` / ``build_skills``；样板 JSON 一律**从磁盘读**，不用字符串夹具。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    """把 ``tools/`` 与 ``src/`` 放进 ``sys.path``（幂等；``sgs`` 与 ``sgs_web`` 都可导入）。"""
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import (  # noqa: E402
    HERO_SUPPLEMENT_PACK_IDS,
    DataLoadError,
    Gender,
    HeroTable,
    Kingdom,
    SkillType,
    enabled_hero_ids,
    hero_card_skill_ids,
    load_content_pack,
    load_hero_tables,
    validate_heroes,
)
from sgs.engine import Engine  # noqa: E402
from sgs_web.projection import build_skills, seat_skill_display  # noqa: E402
from support.positions import SeatSpec, build_state  # noqa: E402

EXAMPLE_JSON = REPO_ROOT / "examples" / "diy-hero" / "heroes.diy.json"
EXAMPLE_README = REPO_ROOT / "examples" / "diy-hero" / "README.md"
EXAMPLE_FILE_LABEL = "examples/diy-hero/heroes.diy.json"
"""``validate_heroes`` 的 ``file`` 参数：错误行里指名**样板文件**，不是测试编的名字。"""

SAMPLE_HERO = "diy_hero"
SAMPLE_SKILL = "hero.jie_guanyu.wusheng"
"""样板引用的技能：界关羽【武圣】（``CATALOG`` 里已登记、无 ``placement`` / ``granted_by``）。"""


def _read_example() -> dict[str, Any]:
    """从**磁盘**读样板 JSON（唯一的进料口；改坏副本的测试也先读它）。"""
    return json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))


def _validate(raw: dict[str, Any]) -> HeroTable:
    return validate_heroes(raw, file=EXAMPLE_FILE_LABEL)


@pytest.fixture(autouse=True)
def _fresh_caches() -> Any:
    """清掉进程级缓存：注入的样板表只在**本测试**里存在（缓存键含 ``pack_id``）。"""
    hero_card_skill_ids.cache_clear()
    yield
    hero_card_skill_ids.cache_clear()


def _seat_the_example(monkeypatch: pytest.MonkeyPatch, table: HeroTable) -> Engine:
    """把加载面换成"既有表 + 样板表"（等价 README 的"只在测试里注入"），再坐一局。"""
    existing = load_hero_tables()
    monkeypatch.setattr(
        "sgs.data.loader.load_hero_tables",
        lambda pack_id="standard": (*existing, table),
    )
    hero_card_skill_ids.cache_clear()
    state = build_state(pack=load_content_pack(), seats=[SeatSpec(hero=SAMPLE_HERO)])
    return Engine.from_state(state, seed=1)


# ---------------------------------------------------------------------------
# ① 文件真的能用：结构 → 语义两段校验都过（从磁盘读）
# ---------------------------------------------------------------------------


def test_the_example_pack_validates() -> None:
    """``json.loads`` → ``HeroTable.model_validate`` → ``validate_heroes`` 全过，字段就是作者要抄的。"""
    raw = _read_example()

    table = HeroTable.model_validate(raw)
    validated = _validate(raw)

    assert validated == table
    assert table.pack_id == "diy"
    hero = table.by_id(SAMPLE_HERO)
    assert hero is not None
    assert (hero.name, hero.true_name, hero.version, hero.enabled) == (
        "自制武圣",
        "自制武圣",
        "diy",
        True,
    )
    assert (hero.kingdom, hero.gender, hero.hp, hero.hp_max) == (
        Kingdom.QUN,
        Gender.MALE,
        4,
        4,
    )
    assert [
        (skill.id, skill.skill_id, skill.name, skill.skill_type) for skill in hero.skills
    ] == [("wusheng", SAMPLE_SKILL, "武圣", SkillType.NORMAL)]


# ---------------------------------------------------------------------------
# ② 能坐进一局：注入样板表后，两条技能清单走真实产出路径列出【武圣】
# ---------------------------------------------------------------------------


def test_the_example_pack_can_seat_a_hero(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入样板表 → ``build_state`` → 详情 / 按钮清单都列出【武圣】（不 mock 引擎、不手拼清单）。

    注入的是什么、为什么：样板包**故意不注册**（裁定 4 的代价是"正常加载面看不见它"），
    故这里只把 ``sgs.data.loader.load_hero_tables`` 换成"既有表 + 样板表"；载体解析、投影
    依旧走各自的真实实现。
    """
    engine = _seat_the_example(monkeypatch, _validate(_read_example()))

    listed = {
        entry["skill_id"]: entry
        for entry in seat_skill_display(SimpleNamespace(engine=engine), 0)
    }
    assert listed[SAMPLE_SKILL]["name"] == "武圣"
    assert listed[SAMPLE_SKILL]["carrier"] == "HERO"

    operable = {
        entry["skill_id"]: entry
        for entry in build_skills(SimpleNamespace(engine=engine, pending=None), 0)
    }
    assert operable[SAMPLE_SKILL]["name"] == "武圣"
    assert operable[SAMPLE_SKILL]["available"] is False, "没有候选 ⇒ 不可点（不是编出来的可用）"


# ---------------------------------------------------------------------------
# ③ 反向①：坏副本必须经 ``validate_heroes`` 报 ``heroes.skill_id_not_found``
# ---------------------------------------------------------------------------


def test_a_copy_with_a_bogus_skill_id_is_rejected(tmp_path: Path) -> None:
    """把样板里的 ``skill_id`` 换成一个不存在的 id（``tmp_path`` 副本）⇒ 加载期 fail-closed。

    证明"读了个文件就绿"不成立：同样的读取路径，只改一个字段，``validate_heroes`` 必须点名
    ``heroes.skill_id_not_found``（不是测试里自说自话的断言）。
    """
    raw = _read_example()
    raw["heroes"][0]["skills"][0]["skill_id"] = "hero.nobody.wusheng"
    copy = tmp_path / "heroes.diy.json"
    copy.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DataLoadError) as excinfo:
        validate_heroes(copy.read_text(encoding="utf-8"), file=EXAMPLE_FILE_LABEL)

    errors = excinfo.value.errors
    assert [error.rule_id for error in errors] == ["heroes.skill_id_not_found"]
    assert errors[0].loc == "/heroes/0/skills/0/skill_id"


# ---------------------------------------------------------------------------
# ④ 裁定 4 的机械证据：样板**不进**官方池（默认加载面里没有它）
# ---------------------------------------------------------------------------


def test_the_example_pack_does_not_touch_the_official_pool() -> None:
    """**不注入**的默认加载面里没有这张牌：扩展包清单没有 ``diy``，启用池里没有它。

    这就是"官方池计数一处不动"的判据——``tools/pool_census.py --check`` 的 15 处数字若变了，
    唯一的可能是有人把样板登记进了 ``HERO_SUPPLEMENT_PACK_IDS``（裁定 4 禁止）。
    """
    assert "diy" not in HERO_SUPPLEMENT_PACK_IDS
    assert SAMPLE_HERO not in enabled_hero_ids()


# ---------------------------------------------------------------------------
# ⑤ 反向②：README 点名的**仓库内路径**都必须存在（含样板 JSON 自己）
# ---------------------------------------------------------------------------

_REPO_PATH_RE = re.compile(r"(?:examples|src|tools|docs|tests|skills)/[A-Za-z0-9_./-]+")
"""README 里点名的仓内路径；行尾标点在下面剥掉（``src/sgs/data/files/`` 这类目录也算）。"""


def test_every_repo_path_the_readme_names_exists() -> None:
    """README 是给仓外作者照抄的：它指到的每个仓内路径都得真的在，否则抄了就是坑。"""
    text = EXAMPLE_README.read_text(encoding="utf-8")
    paths = tuple(
        dict.fromkeys(
            match.group(0).rstrip(".,;:，。；：）)")
            for match in _REPO_PATH_RE.finditer(text)
        )
    )

    assert EXAMPLE_FILE_LABEL in paths, "README 必须点名样板 JSON 自己（不然没得抄）"
    missing = [path for path in paths if not (REPO_ROOT / path).exists()]
    assert not missing, f"README 点名的仓库内路径不存在：{missing}"
