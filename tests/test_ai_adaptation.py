r"""AI 适配契约（票 17；spec §4.2 / §4.5 / §4.7）：三张登记表的机械面。

契约正文（两套 checklist + 三张表的读法）住 `docs/agents/ai-adaptation.md`；本文件只核
**机械面**——能进仓门禁的只有代码侧断言，"信息通道变没变"这类声明留在票面上。

三条断言，各带一个合成反例（删字段 / 删条目 / 注入非法键必须变红）：

1. **选路表键集**：``set(_SCORERS) == set(QueryKind)``。``heuristic`` 的 docstring 早就
   宣称"键集恰等于 ``QueryKind``，由测试钉住"——本条兑现它（此前只有运行期 ``KeyError``）。
2. **AI 读点表**：``PlayerView`` / ``PlayerPublic`` / 每类询问的**全部** ``model_fields``
   都有登记（读 / 不读 + 一句理由），**含** ``exclude=True`` 的派生面。漏登即红。
3. **``Mode`` 表**：``set(表) == set(Mode)``；每个模式四个格子（关系 / 胜负 / 步骤表 /
   投影）；登记为实现的站点用**字符串引用 + importlib** 解析到可调用者，「不适用」的格子
   必须带一句理由。

**机械面与非机械面的分工**：这里只核"有没有登记 / 能不能解析"。"读 / 不读"这一列本身是
人读的声明——机械面挡不住刻意写假的条目，但**漏登一个新字段一定会红**，逼出那一句裁定。
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, get_args

from pydantic import BaseModel

from sgs.agents import heuristic
from sgs.data import MODE_SITE_SLOTS, MODE_SITES, Mode
from sgs.state import PlayerPublic, PlayerView, Query

ReadPoint = tuple[bool, str]
ReadTable = Mapping[str, Mapping[str, ReadPoint]]

SHARED_BUCKET = "Query"
"""询问**公共面**的登记桶：``_QueryBase`` 的字段 + 每个子类各自声明的 ``kind``。

只收"每个询问子类都有"的字段（九类的 ``model_fields`` 交集）——否则"给这一类的这个字段
做决定"就会被一条共用条目悄悄绕过去。
"""


# ---------------------------------------------------------------------------
# 协议面：类清单与字段一律从协议里枚举，不另抄一份
# ---------------------------------------------------------------------------


def _query_classes() -> tuple[type[BaseModel], ...]:
    """``Query`` 联合体的全部成员（新加一类询问 ⇒ 自动进入本文件的枚举）。"""
    return tuple(get_args(get_args(Query)[0]))


def _models() -> tuple[type[BaseModel], ...]:
    return (PlayerView, PlayerPublic, *_query_classes())


def _shared_query_fields() -> frozenset[str]:
    common: set[str] | None = None
    for cls in _query_classes():
        fields = set(cls.model_fields)
        common = fields if common is None else common & fields
    return frozenset(common or ())


def _concrete_buckets() -> tuple[str, ...]:
    return ("PlayerView", "PlayerPublic", *(cls.__name__ for cls in _query_classes()))


def _is_query(model: type[BaseModel]) -> bool:
    return model in _query_classes()


# ---------------------------------------------------------------------------
# 断言 1：选路表键集恰等于 QueryKind
# ---------------------------------------------------------------------------


def _scorer_key_gaps(table: Mapping[Any, Any]) -> tuple[frozenset[Any], frozenset[Any]]:
    """``(缺的 kind, 多出来的键)``；两个都空 = 键集相等。"""
    keys = frozenset(table)
    kinds = frozenset(heuristic.QueryKind)
    return kinds - keys, keys - kinds


def test_every_query_kind_has_a_scorer():
    missing, extra = _scorer_key_gaps(heuristic._SCORERS)

    assert not missing, f"这些 QueryKind 没有 scorer（新增询问必须同票交打分分支）：{sorted(map(str, missing))}"
    assert not extra, f"_SCORERS 里有 QueryKind 之外的键：{sorted(map(str, extra))}"


def test_every_scorer_is_callable():
    for kind, scorer in heuristic._SCORERS.items():
        assert callable(scorer), f"{kind} 的 scorer 不可调用：{scorer!r}"


def test_removing_one_scorer_goes_red():
    """合成反例：删一条分支，键集断言必须变红（防"表恰好空转通过"）。"""
    doctored = {k: v for k, v in heuristic._SCORERS.items() if k is not heuristic.QueryKind.ARRANGE}

    missing, _ = _scorer_key_gaps(doctored)

    assert missing == frozenset({heuristic.QueryKind.ARRANGE})


def test_injecting_an_unknown_kind_goes_red():
    """合成反例：注入一个不存在的 kind，键集断言必须变红。"""
    doctored = {**heuristic._SCORERS, "NOT_A_QUERY_KIND": lambda query, action: 0.0}

    _, extra = _scorer_key_gaps(doctored)

    assert extra == frozenset({"NOT_A_QUERY_KIND"})


# ---------------------------------------------------------------------------
# 断言 2：AI 读点表覆盖全部字段
# ---------------------------------------------------------------------------


def _read_point_gaps(table: ReadTable) -> list[str]:
    """读点表的全部缺口（空列表 = 通过）。

    五条规则：① 桶名必须是协议里的模型或共用桶；② 共用桶只收九类询问的交集；
    ③ 每个字段都必须在**它所属的那一类**（或询问的共用桶）里登记；④ 登记不得是幽灵字段
    （类上不存在）也不得两处重复；⑤ 每条的读标记是 ``bool``、理由非空。
    """
    gaps: list[str] = []
    concrete = _concrete_buckets()
    known = {SHARED_BUCKET, *concrete}
    for bucket in sorted(table):
        if bucket not in known:
            gaps.append(f"登记桶 {bucket!r} 不是协议里的模型")

    shared_fields = _shared_query_fields()
    shared_table = table.get(SHARED_BUCKET, {})
    for field in sorted(set(shared_table) - shared_fields):
        gaps.append(f"{SHARED_BUCKET}.{field} 不是每个询问子类都有的字段（共用桶只收交集）")

    for model in _models():
        name = model.__name__
        own = table.get(name, {})
        covered = set(own)
        if _is_query(model):
            covered |= set(shared_table)
        for field in model.model_fields:
            if field not in covered:
                gaps.append(f"{name}.{field} 没有登记（漏登即红）")
        for field in sorted(set(own) - set(model.model_fields)):
            gaps.append(f"{name}.{field} 不是 {name} 的字段（幽灵登记）")
        if _is_query(model):
            for field in sorted(set(own) & set(shared_table)):
                gaps.append(f"{name}.{field} 与共用桶重复登记（同一决定只登记一次）")

    buckets: dict[str, Mapping[str, ReadPoint]] = {SHARED_BUCKET: shared_table}
    buckets.update({name: table.get(name, {}) for name in concrete})
    for bucket, entries in sorted(buckets.items()):
        for field, entry in sorted(entries.items()):
            point = f"{bucket}.{field}"
            if not (isinstance(entry, tuple) and len(entry) == 2):
                gaps.append(f"{point} 的登记不是 (读?, 理由) 二元组：{entry!r}")
                continue
            read, reason = entry
            if not isinstance(read, bool):
                gaps.append(f"{point} 的读标记不是 bool：{read!r}")
            if not str(reason).strip():
                gaps.append(f"{point} 的理由是空的（每个字段都要一句理由）")
    return gaps


def _covered_fields() -> list[str]:
    """表**实际覆盖**到的字段实例（``类.字段``），供覆盖元断言使用。

    判据读的是**表**而不是协议的字段交集：表要是空了/退化了，这里必须跟着塌下去，否则
    "≥40 个字段"的元断言就成了自证。
    """
    covered: list[str] = []
    shared = set(heuristic._AI_READ_POINTS.get(SHARED_BUCKET, {}))
    for model in _models():
        name = model.__name__
        own = set(heuristic._AI_READ_POINTS.get(name, {}))
        for field in model.model_fields:
            if field in own or (_is_query(model) and field in shared):
                covered.append(f"{name}.{field}")
    return covered


def _with_changes(
    table: Mapping[Any, Mapping[str, Any]], changes: Mapping[Any, Any]
) -> dict[Any, Any]:
    """复制一份登记表并施加改动（``None`` 值 = 删掉那一格）；合成反例用，不碰真表。"""
    copied: dict[Any, dict[str, Any]] = {key: dict(entries) for key, entries in table.items()}
    for key, entries in changes.items():
        target = copied.setdefault(key, {})
        for name, entry in entries.items():
            if entry is None:
                target.pop(name, None)
            else:
                target[name] = entry
    return copied


def _doctored(**changes: Mapping[str, Any]) -> dict[str, dict[str, ReadPoint]]:
    return _with_changes(heuristic._AI_READ_POINTS, changes)


def test_the_read_table_covers_every_field_of_every_query():
    gaps = _read_point_gaps(heuristic._AI_READ_POINTS)

    assert not gaps, "AI 读点表有缺口：\n" + "\n".join(gaps)


def test_the_enumeration_really_reached_the_protocol():
    """覆盖元断言：枚举没扫到东西时，"全覆盖"会空转通过。"""
    covered = _covered_fields()
    excluded = [
        f"{model.__name__}.{name}"
        for model in _models()
        for name, field in model.model_fields.items()
        if field.exclude
    ]

    assert len(covered) >= 40, f"只覆盖了 {len(covered)} 个字段；枚举目标可能指错了"
    assert excluded, "state 协议里找不到 exclude=True 的派生面？枚举目标可能指错了"
    missing = sorted(set(excluded) - set(covered))
    assert not missing, f"exclude=True 的派生面没进读点表：{missing}"


def test_forgetting_one_field_registration_goes_red():
    """合成反例：删掉一条登记，覆盖检查必须变红。"""
    gaps = _read_point_gaps(_doctored(PlayerPublic={"identity": None}))

    assert any("PlayerPublic.identity" in gap for gap in gaps), gaps


def test_registering_a_ghost_field_goes_red():
    """合成反例：登记一个协议里不存在的字段，必须变红（防"多写一行也算登记"）。"""
    gaps = _read_point_gaps(_doctored(PlayerView={"no_such_field": (True, "幽灵")}))

    assert any("PlayerView.no_such_field" in gap for gap in gaps), gaps


def test_an_empty_read_point_reason_goes_red():
    """合成反例：理由留空必须变红（"读 / 不读"要能被人复核，理由就是那一句）。"""
    gaps = _read_point_gaps(_doctored(PlayerView={"seat": (True, "   ")}))

    assert any("PlayerView.seat" in gap and "理由" in gap for gap in gaps), gaps


def test_a_shared_entry_for_a_single_class_field_goes_red():
    """合成反例：把只属于一类的字段塞进共用桶，必须变红。"""
    gaps = _read_point_gaps(_doctored(Query={"dying": (True, "只有桃询问有")}))

    assert any(f"{SHARED_BUCKET}.dying" in gap for gap in gaps), gaps


# ---------------------------------------------------------------------------
# 断言 2′：询问的序列化面 = 冻结报文（票 01 的 Q6=B）
# ---------------------------------------------------------------------------


def test_the_skill_activation_query_keeps_its_frozen_serialized_face():
    """票 01（Q6=B）：本轮候选清单是**本地面**，``SkillActivationQuery`` 的序列化面一个字段
    都不加。

    15 份录像逐字节带着这张询问的报文（``min_picks`` / ``max_picks`` / ``slots`` 一类冻结
    字段）。本条把「哪些字段不进记录流」钉成**字段级**判据：``exclude=True`` 的名单变了
    就红——新加的本地面（``skill_ids`` / ``activation_preactivations``）也必须在名单里，
    否则 47 条金录像立刻变红（那是 ``tools/check_goldens.sh`` 的另一半判据）。
    """
    from sgs.state import SkillActivationQuery

    excluded = {
        name for name, field in SkillActivationQuery.model_fields.items() if field.exclude
    }
    assert excluded == {
        "transform_preactivations",
        "transform_actions",
        "preactivation",
        "skill_ids",
        "activation_preactivations",
    }, (
        "SkillActivationQuery 的 exclude=True 字段集变了：这正是「不进记录流」的那份名单；"
        "加一个进记录流的字段 = 15 份录像 / 47 条金录像一起变红"
    )
    # ``revealed`` 没有字段级 exclude，但序列化器在**为空**时把它整键删掉（``_QueryBase``
    # 的 ``_serialize``），故恒定落盘的字段集要把它单列出去。
    serialized = {
        name
        for name, field in SkillActivationQuery.model_fields.items()
        if not field.exclude and name != "revealed"
    }
    assert serialized == {
        "kind",
        "seq",
        "actor",
        "timing",
        "prompt",
        "legal_actions",
        "view",
        "skill_id",
        "optional",
        "min_picks",
        "max_picks",
        "slots",
    }, "冻结报文的字段集（票 01 前后逐字相同）"


# ---------------------------------------------------------------------------
# 断言 3：Mode 表（每个模式四个格子，站点可解析）
# ---------------------------------------------------------------------------


def _resolve(ref: str) -> Any:
    """``"模块路径:属性路径"`` → 对象（``data`` 不反向依赖 ``engine``，故用字符串引用）。"""
    module_name, _, attribute = ref.partition(":")
    if not module_name or not attribute:
        raise ValueError(f"引用必须是 `模块:属性` 形态：{ref!r}")
    target: Any = importlib.import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    return target


def _mode_site_gaps(table: Mapping[Any, Mapping[str, Any]]) -> list[str]:
    """``Mode`` 表的全部缺口（空列表 = 通过）。"""
    gaps: list[str] = []
    for mode in sorted(Mode, key=lambda member: member.value):
        if mode not in table:
            gaps.append(f"{mode.value} 没有登记")
            continue
        cells = table[mode]
        for slot in MODE_SITE_SLOTS:
            if slot not in cells:
                gaps.append(f"{mode.value}.{slot} 没有登记")
        for slot in sorted(set(cells) - set(MODE_SITE_SLOTS)):
            gaps.append(f"{mode.value}.{slot} 不是登记的格子（只有 {'/'.join(MODE_SITE_SLOTS)}）")
        for slot in MODE_SITE_SLOTS:
            cell = cells.get(slot)
            if cell is None:
                continue
            point = f"{mode.value}.{slot}"
            if not str(cell.reason).strip():
                gaps.append(f"{point} 的理由是空的")
            if not cell.refs:
                continue
            for ref in cell.refs:
                try:
                    resolved = _resolve(ref)
                except (ImportError, AttributeError, ValueError) as error:
                    gaps.append(f"{point} 的引用 {ref!r} 解析不到：{error}")
                    continue
                if not callable(resolved):
                    gaps.append(f"{point} 的引用 {ref!r} 不是可调用者：{resolved!r}")
    for mode in sorted(set(table) - set(Mode), key=str):
        gaps.append(f"{mode} 不是 Mode 的成员（幽灵登记）")
    return gaps


def _mode_gaps_with(changes: Mapping[Any, Mapping[str, Any]]) -> list[str]:
    return _mode_site_gaps(_with_changes(MODE_SITES, changes))


def test_the_mode_table_has_every_mode_slot_and_resolvable_sites():
    """两种模式 × 四格齐全 + 每个实现站点 ``importlib`` 解析到可调用者（正反两面的正面）。"""
    gaps = _mode_site_gaps(MODE_SITES)

    assert not gaps, "Mode 表有缺口：\n" + "\n".join(gaps)


def test_the_mode_table_still_finds_implemented_sites():
    """覆盖元断言：整列退化成「不适用」时，解析断言会空转通过。"""
    implemented = [
        f"{mode.value}.{slot}"
        for mode, cells in MODE_SITES.items()
        for slot, cell in cells.items()
        if cell.refs
    ]
    steps = [ref for cells in MODE_SITES.values() for ref in cells["steps"].refs]

    assert len(implemented) >= 4, f"只登记了 {len(implemented)} 个实现站点；表可能退化成全「不适用」"
    assert steps, "步骤表一列不该是「不适用」——两种模式都要装配步骤表"


def test_dropping_a_mode_goes_red():
    """合成反例：漏登记一个模式，必须变红。"""
    table = {mode: dict(cells) for mode, cells in MODE_SITES.items()}
    table.pop(Mode.NO_IDENTITY)

    gaps = _mode_site_gaps(table)

    assert any(Mode.NO_IDENTITY.value in gap for gap in gaps), gaps


def test_dropping_a_slot_goes_red():
    """合成反例：四个格子缺一个，必须变红。"""
    gaps = _mode_gaps_with({Mode.NO_IDENTITY: {"projection": None}})

    assert any("projection" in gap for gap in gaps), gaps


def test_an_unresolvable_site_reference_goes_red():
    """合成反例：写一个解析不到的引用，必须变红（"有值"不等于"可核"）。"""
    from sgs.data import ModeSite

    gaps = _mode_gaps_with(
        {Mode.NO_IDENTITY: {"steps": ModeSite(("sgs.no_such_module:x",), "坏引用")}}
    )

    assert any("解析不到" in gap for gap in gaps), gaps


def test_an_empty_mode_site_reason_goes_red():
    """合成反例：「不适用」的格子没有理由，必须变红。"""
    from sgs.data import ModeSite

    gaps = _mode_gaps_with({Mode.NO_IDENTITY: {"relation": ModeSite((), "  ")}})

    assert any(f"{Mode.NO_IDENTITY.value}.relation" in gap for gap in gaps), gaps
