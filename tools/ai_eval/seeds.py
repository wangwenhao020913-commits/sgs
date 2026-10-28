"""判决批派生与 1:1:2:1 配比（票 13；spec §2.7 / §2.4 / §5.6）。

**判决批在调参时不存在**：种子由 candidate **commit**（解析后的完整 sha 文本）经 LCG 派生，
提交一改批次即换；同一轮所有参战版本跑同一批（spec §2.4 A）。

§2.7 给了两件事：**抽号规则**（LCG + 下界 + 间距 + 排序摘要）与一句"再按座位 0 的身份凑成
1:1:2:1"。抽号部分逐字照写：

1. ``digest = sha256(commit)``，``state = int.from_bytes(digest[:16], "big")``；
2. 循环 ``state = (state * 6364136223846793005 + 1442695040888963407) mod 2**64``；
3. 丢弃 ``state < 97``、与已选种子相差 ``< 97``、以及落进**实机带** 7001–7099 的候选
   （§5.6：概率 ~0 也要重派生）；
4. 排序后记 sha256（``"<seed>\\n"`` 连接，UTF-8）写进结果 JSON 的 ``seed_batch.sha256``。

**配比的兑现方式是本实现的显式裁定**（§2.7 只给"凑成 1:1:2:1"一句，没给凑法）：抽号时按
座位 0 的身份**定额**——该身份额度已满的候选直接丢弃，继续抽，直到四格填满。这样"批大小"
与"配比"同时成立；额度用**最大余数法**（平票按主-忠-反-内）——768 副 ⇒ 154 / 154 / 307 /
153，与 spec §10 的例子逐值一致。身份本身离线算（``RandomSource(seed).substream("identity")``
洗一次 ``roster_for(5)``，取座位 0），与引擎发身份同一份规则。

封印批（``seal``）不是 commit 派生：名字（如 ``sealed-1``）写死、**1024 副写死**（CLI 不暴露
副数），派生用同一个 LCG，输入换成 ``"sealed:<name>"``。"用一次退役"是人工纪律，工具只如实
记下名字。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from sgs.data import roster_for
from sgs.engine import RandomSource

from .errors import AiEvalSpecError

__all__ = [
    "IDENTITY_ORDER",
    "LCG_INCREMENT",
    "LCG_MULTIPLIER",
    "LIVE_SEED_RANGE",
    "MIN_GAP",
    "MIN_SEED",
    "SeedBatch",
    "derive_batch",
    "derive_sealed_batch",
    "identity_quota",
    "is_reserved_seed",
    "seat_zero_identity",
]

DEFAULT_PLAYERS = 5
"""赛制固定 5 人身份局（spec §2.5）：2 人局测不到记牌 / 身份推断，不进判决批。"""

MIN_SEED = 97
"""候选种子的下界（§2.7：``state < 97`` 丢弃）。"""

MIN_GAP = 97
"""同一批内任意两个种子的最小间距（§2.7）。"""

LCG_MULTIPLIER = 6364136223846793005
LCG_INCREMENT = 1442695040888963407
MODULUS = 1 << 64

LIVE_SEED_RANGE = range(7001, 7100)
"""实机带：``7001–7099``（spec §5.2 / §5.6）；右开区间的写法让边界不必再写 ``+1``。"""

_QUOTA_ROSTER = roster_for(DEFAULT_PLAYERS)
"""5 人身份配比的**唯一真源**（``sgs.data.identities.roster_for``）：本模块不重抄 1:1:2:1。"""

IDENTITY_ORDER: tuple[str, ...] = tuple(
    dict.fromkeys(identity.value for identity in _QUOTA_ROSTER)
)
"""配比与 JSON 里身份键的**固定次序**：主-忠-反-内（最大余数的平票也按它裁）。"""

_IDENTITY_WEIGHTS: dict[str, int] = {
    identity.value: _QUOTA_ROSTER.count(identity) for identity in _QUOTA_ROSTER
}


@dataclass(frozen=True)
class SeedBatch:
    """一批判决种子 + 配比 + 摘要：结果 JSON ``seed_batch`` 的全部事实。"""

    kind: str
    deals: int
    seeds: tuple[int, ...]
    allocation: dict[str, int]
    sha256: str
    batch: str | None = None
    """封印批的名字（``sealed-1``）；commit 派生的批为 ``None``。"""

    def to_dict(self) -> dict[str, object]:
        """§2.8 的 ``seed_batch`` 对象；封印批多一个 ``batch`` 键（spec 未定其形状）。"""
        payload: dict[str, object] = {
            "kind": self.kind,
            "deals": self.deals,
            "sha256": self.sha256,
            "identity_allocation": dict(self.allocation),
        }
        if self.batch is not None:
            payload["batch"] = self.batch
        return payload


def _check_deals(deals: int) -> None:
    if isinstance(deals, bool) or not isinstance(deals, int):
        raise AiEvalSpecError(f"deals 必须是整数，得到 {type(deals).__name__}：{deals!r}")
    if deals <= 0:
        raise AiEvalSpecError(f"deals 必须是正整数，得到 {deals}")


def is_reserved_seed(seed: int) -> bool:
    """该种子是否落进**实机带** 7001–7099（spec §5.6：派生的判决种子不得进这个带）。"""
    return seed in LIVE_SEED_RANGE


def identity_quota(deals: int, *, players: int = DEFAULT_PLAYERS) -> dict[str, int]:
    """按 1:1:2:1 把 ``deals`` 分给四个身份（**最大余数法**，平票按 :data:`IDENTITY_ORDER`）。

    768 副 ⇒ ``{lord: 154, loyalist: 154, rebel: 307, renegade: 153}``，与 spec §10 的例子
    逐值相同；128 副 ⇒ 26 / 26 / 51 / 25。总和恒等于 ``deals``。
    """
    _check_deals(deals)
    if players != DEFAULT_PLAYERS:
        raise AiEvalSpecError(
            f"判决批口径固定 {DEFAULT_PLAYERS} 人身份局，得到 players={players}（spec §2.5）"
        )
    total = len(_QUOTA_ROSTER)
    base = {identity: deals * weight // total for identity, weight in _IDENTITY_WEIGHTS.items()}
    remainder = deals - sum(base.values())
    ranked = sorted(
        IDENTITY_ORDER,
        key=lambda identity: (
            -(deals * _IDENTITY_WEIGHTS[identity] % total),
            IDENTITY_ORDER.index(identity),
        ),
    )
    for identity in ranked[:remainder]:
        base[identity] += 1
    return {identity: base[identity] for identity in IDENTITY_ORDER}


def seat_zero_identity(seed: int, *, players: int = DEFAULT_PLAYERS) -> str:
    """座位 0 的身份（离线算，不跑对局）——与引擎发身份**同一份规则**。

    引擎（``sgs.engine.game._deal_identities``）取配比多重集、用 ``identity`` 子流洗一次、
    下标 i 发给座位 i；这里只取洗完之后的下标 0。``tests/test_ai_eval.py`` 有一条断言拿
    真实 ``Engine.new_game`` 逐种子对照，规则若改它会红。
    """
    roster = list(roster_for(players))
    RandomSource(seed).substream("identity").shuffle(roster)
    return roster[0].value


def _seed_digest(seeds: Iterable[int]) -> str:
    """排序后的种子批摘要（spec §2.7）：每行一个十进制种子、末行也有换行、UTF-8。"""
    text = "".join(f"{seed}\n" for seed in sorted(seeds))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _draw(seed_material: str, deals: int) -> tuple[int, ...]:
    """LCG 抽种：间距、下界、实机带、1:1:2:1 额度全过才收下。"""
    state = int.from_bytes(hashlib.sha256(seed_material.encode("utf-8")).digest()[:16], "big")
    quota = identity_quota(deals)
    filled = {identity: 0 for identity in quota}
    selected: list[int] = []
    while len(selected) < deals:
        state = (state * LCG_MULTIPLIER + LCG_INCREMENT) % MODULUS
        if state < MIN_SEED:
            continue
        if any(abs(state - chosen) < MIN_GAP for chosen in selected):
            continue
        if is_reserved_seed(state):
            continue
        identity = seat_zero_identity(state)
        if filled[identity] >= quota[identity]:
            continue
        selected.append(state)
        filled[identity] += 1
    return tuple(sorted(selected))


def derive_batch(commit: str, deals: int) -> SeedBatch:
    """从 candidate commit 派生判决批（spec §2.7）；同一 commit 恒得同一批。"""
    _check_deals(deals)
    if not isinstance(commit, str) or not commit:
        raise AiEvalSpecError(f"commit 必须是非空字符串，得到 {commit!r}")
    seeds = _draw(commit, deals)
    return SeedBatch(
        kind="commit_derived",
        deals=deals,
        seeds=seeds,
        allocation=identity_quota(deals),
        sha256=_seed_digest(seeds),
    )


def derive_sealed_batch(name: str, deals: int) -> SeedBatch:
    """封印批（spec §2.4 B / §2.9）：名字写死、只出聚合、用一次退役。

    与 commit 派生共用同一套 LCG / 间距 / 配比纪律，只是输入从 commit 换成
    ``"sealed:<name>"``——同一名字永远得到同一批，便于"事后复核当时封印的是什么"。
    """
    _check_deals(deals)
    if not isinstance(name, str) or not name.strip():
        raise AiEvalSpecError(f"封印批需要非空名字（如 sealed-1），得到 {name!r}")
    seeds = _draw(f"sealed:{name.strip()}", deals)
    return SeedBatch(
        kind="sealed",
        deals=deals,
        seeds=seeds,
        allocation=identity_quota(deals),
        sha256=_seed_digest(seeds),
        batch=name.strip(),
    )
