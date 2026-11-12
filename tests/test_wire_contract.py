"""线格式的**唯一硬约束**：跨 JSON 的数字必须是 JS 能精确表示的整数（retro M1）。

浏览器 ``JSON.parse`` 只把 ``|n| < 2**53`` 的整数读成精确整数；更大的整数会变成浮点，
回传时被 pydantic 拒绝。手动验收实测过 ``card_id ≈ -7.006...e+183``（国色 / 急救提交被拒，
retro §1 #1）：Python 这一侧永远是精确整数，故"后端测试直接构造 ``Action`` 提交"看不见这个
缺陷——**必须往返一次 JSON 再断言**。上界从 :data:`sgs.skills.virtual.WIRE_INT_LIMIT` 取，
不写字面量（W4-fix-5 把它定为唯一真源）。

两层断言：

1. **单元层**：``kind ∈ 0..4`` × 各种来源形状派生出的虚拟 id，经
   ``json.dumps → json.loads`` 仍是同一个 ``int`` 且 ``|id| < WIRE_INT_LIMIT``；
2. **载荷层**：真会话（以及一个真的含虚拟牌的构造局面）的每个 pending，取
   :func:`sgs_web.projection.pending_payload`，把整份载荷递归往返一次 JSON，断言**任何**
   ``card_id`` 都是 ``int`` 且 ``|value| < WIRE_INT_LIMIT``；再把往返后的候选动作交给
   :func:`sgs.state.parse_action`，必须解析回**同一个动作**。

这条测试在 `f8794cd`（W4-fix-1 之前，``kind >= 2`` 的段容量是 ``base**90``）上是红的：
拿这棵基线树跑，失败消息里会打印越界的 id 本身。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    """把 ``tools/``、``src/``、``tests/`` 放进 ``sys.path``（幂等，与 ``test_web`` 同姿态）。"""
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import load_content_pack  # noqa: E402
from sgs.effects import legal_actions_for  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.skills.flow import provide_face  # noqa: E402
from sgs.skills.virtual import (  # noqa: E402
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    kind_source_ceiling,
    virtual_card_id,
)
from sgs.state import (  # noqa: E402
    PlayPhaseQuery,
    QueryKind,
    SelectCardsQuery,
    SelectionSpec,
    Zone,
    parse_action,
    project,
)
from sgs.timing import Timing  # noqa: E402
from sgs_web.projection import pending_payload  # noqa: E402
from sgs_web.session import Session  # noqa: E402
from sgs_web.timeout import fallback_action  # noqa: E402
from support.positions import SeatSpec, build_state, cards_of_type  # noqa: E402

try:  # pragma: no cover - 只在 f8794cd 基线（W4-fix-5 之前）缺这个常量
    from sgs.skills.virtual import WIRE_INT_LIMIT
except ImportError:
    # 基线没有"唯一真源"：仍要报出**越界的 id**，而不是停在 ImportError 上（见文件头）。
    WIRE_INT_LIMIT = None  # type: ignore[assignment]


def _wire_limit() -> int:
    """线上整数上限；缺失即红（否则这条测试会拿不到判据而空转）。"""
    assert WIRE_INT_LIMIT is not None, (
        "sgs.skills.virtual.WIRE_INT_LIMIT 不存在：跨 JSON 整数的上界没有唯一真源"
    )
    return int(WIRE_INT_LIMIT)


def _assert_wire_safe(value: int, what: str) -> None:
    """``what`` 必须落在 JS 精确整数区间内。基线缺常量时把 id 一并报出来。"""
    assert WIRE_INT_LIMIT is not None, (
        f"{what} = {value}：sgs.skills.virtual.WIRE_INT_LIMIT 不存在，无法判定它跨 JSON 后是否还精确"
    )
    limit = _wire_limit()
    assert abs(value) < limit, f"{what} = {value} 越过线上整数上限 {limit}（JS 会读成浮点）"


def _walk(value: Any, path: str = "") -> Any:
    """递归遍历 JSON 形状，产出 ``(路径, 叶子)``（路径只为报错可定位）。"""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")
    else:
        yield path, value


def _card_id_leaves(payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """载荷里**所有**叫 ``card_id`` 的字段（不管住得多深）。"""
    return [(path, value) for path, value in _walk(payload) if path.rsplit(".", 1)[-1] == "card_id"]


def _check_payload(payload: dict[str, Any], *, where: str) -> tuple[dict[str, Any], int]:
    """往返一次 JSON，核每个 ``card_id``；返回往返后的载荷与 ``card_id`` 个数。"""
    raw = json.loads(json.dumps(payload))
    leaves = _card_id_leaves(raw)
    for path, value in leaves:
        assert type(value) is int, (
            f"{where}: {path} = {value!r} 不是 int（JSON 往返后是 {type(value).__name__}）"
        )
        _assert_wire_safe(value, f"{where}: {path}")
    return raw, len(leaves)


def _check_actions(raw: dict[str, Any], actions: Any, *, where: str) -> None:
    """往返后的候选动作经 ``parse_action`` 必须还原成**同一个**动作。"""
    entries = raw["legal_actions"]
    assert len(entries) == len(actions), (
        f"{where}: legal_actions 条数变了（{len(entries)} != {len(actions)}）"
    )
    for index, (entry, action) in enumerate(zip(entries, actions, strict=True)):
        round_tripped = json.loads(json.dumps(entry))
        assert parse_action(round_tripped) == action, (
            f"{where}: legal_actions[{index}] 经 JSON → parse_action 后不是同一个动作"
            f"（往返后 kind={parse_action(round_tripped).kind}，原来是 {action.kind}）"
        )


def _source_cases(card_count: int, kind: int) -> list[tuple[int, ...]]:
    """代表性来源形状：空 / 单张 / 两张 / 该段满额 / 取最高 id（最坏折叠值）。

    "满额"读**该 kind 的段上界**（M6 票 03 起追加段比共享段更早撞线：
    ``DISMANTLEMENT`` 在 108 张档只到 6，见 :func:`kind_source_ceiling`）。
    """
    top = card_count - 1
    ceiling = kind_source_ceiling(kind, card_count)
    return [
        (),
        (0,),
        (top,),
        (0, top),
        tuple(range(ceiling)),
        (top,) * ceiling,
    ]


# ---------------------------------------------------------------------------
# 1. 单元层：id 派生 × JSON 往返
# ---------------------------------------------------------------------------


def test_every_kind_and_source_shape_stays_a_js_safe_integer() -> None:
    """``kind ∈ 0..4`` × 来源形状：派生 id 往返后仍是同一个 ``int``，且 ``< WIRE_INT_LIMIT``。

    兼容段（``kind < 2``）与泛化段（``kind >= 2``）都覆盖；``MAX_VIRTUAL_SOURCES`` 张取
    最高 id 的那一组是折叠的**最坏值**（``(L+1) * base**L - 1``），上界就在那里生效。
    """
    card_count = len(load_content_pack(deck_profile="standard_108").deck.cards)
    cases = [
        (
            f"kind={int(kind)} sources={sources}",
            virtual_card_id(kind=int(kind), sources=sources, card_count=card_count),
        )
        for kind in VirtualKind
        for sources in _source_cases(card_count, int(kind))
    ]
    # **最坏值先判**：越界时先报 |id| 最大的那一组（基线 f8794cd 上就是 kind>=2 的天文数）。
    cases.sort(key=lambda row: abs(row[1]), reverse=True)

    for what, value in cases:
        round_tripped = json.loads(json.dumps({"card_id": value}))["card_id"]
        assert type(round_tripped) is int, (
            f"{what}: 往返后不是 int（{type(round_tripped).__name__}）"
        )
        assert round_tripped == value, f"{what}: 往返后值变了（{round_tripped} != {value}）"
        _assert_wire_safe(value, what)

    assert sum(len(_source_cases(card_count, int(kind))) for kind in VirtualKind) == len(cases)


# ---------------------------------------------------------------------------
# 2. 载荷层：真会话的每个 pending 都过一遍线格式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "heroes"),
    [
        (7, ("guanyu", "huatuo")),  # 转化族（武圣 / 急救）在场
        (20261012, ("zhaoyun", "zhouyu")),  # 龙胆 / 反间
        (3, None),  # 缺省启用池
    ],
)
def test_every_pending_of_a_real_session_is_wire_safe(seed: int, heroes: Any) -> None:
    """把真会话从开局驱动到终局：**每次**挂起的整份载荷都过 JSON 往返与 ``parse_action``。"""
    kwargs: dict[str, Any] = {} if heroes is None else {"heroes": heroes}
    session = Session(
        seed=seed,
        ai="random",
        timeout_seconds=None,
        player_count=2,
        human_seat=0,
        **kwargs,
    )

    pendings = actions = card_ids = 0
    steps = 0
    while not session.finished and pendings < 200:
        steps += 1
        assert steps < 10_000, f"seed={seed}: 挂起驱动没有推进（活锁）"
        query = session.pending
        if query is None:
            session.drive()  # 五谷逐目标暂停（table-ux-overhaul 票 19）：没有挂起也要接着驱动
            continue
        payload = pending_payload(session)
        assert payload is not None, f"seed={seed} seq={query.seq}: 挂起却没有载荷"
        where = f"seed={seed} seq={query.seq} kind={query.kind}"
        raw, seen = _check_payload(payload, where=where)
        _check_actions(raw, query.legal_actions, where=where)
        card_ids += seen
        actions += len(query.legal_actions)
        pendings += 1
        session.submit_answer(fallback_action(query))

    assert pendings > 0, f"seed={seed}: 一次挂起都没走到，断言空转"
    assert actions > 0, f"seed={seed}: 一次候选都没有，parse_action 那半没被驱动"
    assert card_ids > 0, f"seed={seed}: 整局的载荷里一个 card_id 都没有，断言空转"


# ---------------------------------------------------------------------------
# 3. 载荷层：**真的含虚拟牌**的那一份（构造局面；W4-fix-2 后转化候选只走清单）
# ---------------------------------------------------------------------------


def _longdan_engine() -> Engine:
    """赵云手里一张【闪】：【龙胆】让它同时是一条虚拟【杀】的候选。"""
    pack = load_content_pack(deck_profile="standard_108")
    dodge = cards_of_type(pack, "dodge")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(dodge,), hero="zhaoyun"), SeatSpec(hand=())],
        turn_seat=0,
    )
    return Engine.from_state(state, seed=1)


def _play_phase_query(engine: Engine, *, seq: int = 1) -> PlayPhaseQuery:
    return PlayPhaseQuery(
        seq=seq,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="",
        legal_actions=legal_actions_for(engine.state, 0, QueryKind.PLAY_PHASE, answers_seq=seq),
        view=project(engine.state, 0),
    )


def _provide_query(engine: Engine, *, seq: int = 2) -> SelectCardsQuery:
    """「提供一张【杀】」：虚拟 B 只出现在 ``slots[].card`` 上（青龙追问的形状）。"""
    slots, _ = provide_face(engine.state, 0, "slash", (Zone.HAND,))
    return SelectCardsQuery(
        seq=seq,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="提供一张【杀】继续使用（目标不变；可发动转化技）",
        legal_actions=(),
        view=project(engine.state, 0),
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
        slots=slots,
        provide_type="slash",
    )


def test_a_payload_that_carries_a_virtual_card_is_wire_safe() -> None:
    """含虚拟牌的载荷也必须过线格式：候选里的虚拟【杀】要能原样提交回去。

    两条真实形状各取一份：``PLAY_PHASE`` 的候选带虚拟牌（``legal_actions`` /
    ``candidate_index`` / ``virtual_index``），``SELECT_CARDS`` 的提供面把虚拟 B 放在
    ``slots[].card`` 上。先断言"这份载荷真的含虚拟牌"——否则这条用例会绿得毫无意义
    （``CODING_STANDARDS.md`` §6：断"读取方读到了"的测试要走真实产出路径）。
    """
    engine = _longdan_engine()

    play = _play_phase_query(engine)
    play_payload = pending_payload(play, human_seat=0)
    assert play_payload is not None
    raw, _ = _check_payload(play_payload, where="构造局面 / PLAY_PHASE")
    virtual = [value for _, value in _card_id_leaves(raw) if value < 0]
    assert virtual, "这条载荷必须真的带虚拟牌（【龙胆】的虚拟【杀】），否则没有覆盖"
    _check_actions(raw, play.legal_actions, where="构造局面 / PLAY_PHASE")

    provide = _provide_query(engine)
    provide_payload = pending_payload(provide, human_seat=0)
    assert provide_payload is not None
    raw_provide, _ = _check_payload(provide_payload, where="构造局面 / SELECT_CARDS 提供面")
    virtual_slots = [value for _, value in _card_id_leaves(raw_provide) if value < 0]
    assert virtual_slots, "提供面的 slots 上必须真的带虚拟牌（虚拟【杀】），否则没有覆盖"
