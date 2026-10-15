"""不变量 fuzz：随机对局驱动的性质检验（票 11 §4 / 票 26 / 票 32 §2）。

落点 = 库门面（主缝）：``new_game`` + 每座一个 ``RandomPlayer`` 的**多局**记录流与活状态。

- 预算：默认 **100 局 / 120 s**，以先到者为准终止；默认预算内跑不满 100 局即判失败
  （性能退化，票 17 §2）。``SGS_FUZZ_GAMES`` / ``SGS_FUZZ_SECONDS`` 可放大做长跑。
- 人数按 **2 → 4 → 5 → 6 → 7 → 8 六档确定性轮转**（2 人档护住 ADR-007 的
  "检测点 ≠ 终止点"分支）；``SGS_FUZZ_PLAYERS`` 可覆盖成固定人数。
- 逐局验不变量：**牌守恒 / 体力范围 / 区域互斥 / 手牌上限 / 事件序号单调 / 每局必达终局 /
  终局牌守恒**，外加票 32 新增的四类：**阵亡者三区为空 / 装备槽位唯一 / 判定区同名唯一 /
  终局身份-胜负一致**，以及票 04 新增、票 14 收口的**候选数上界**（技能层两维组合 ≤ 128；
  五条一维枚举按票 10 的 `DiscardCards` 先例逐条豁免，显式表见
  :data:`EXEMPT_ONE_DIMENSIONAL_FAMILIES`、裁定口径见 :func:`_check_candidate_cap`）。
- **不进 fuzz**：同时死亡、无懈链、改判——它们是**路径性质**，随机局不保证命中，
  落基准与构造局面单测更可判决（票 17 §2）。
- 对局种子来自**测试侧 OS 熵**（``secrets``），**不占游戏内随机源**；失败消息带可复现的
  ``seed``、人数与**步号**，可直接 ``python -m sgs --seed S --players N`` 重跑同一局。
- 全部走**库 API**（``Engine.new_game`` + ``RandomPlayer``），不经产品 CLI。

断言分两层（票 11 §4）：

1. **活状态层**：``_FuzzEngine`` 覆写记录流的唯一出口 ``_emit``，每写出一条记录就在**活
   ``GameState``** 上验体力范围 / 区域互斥 /（边界上的）手牌上限 / 座位级三区约束；
2. **记录流层**：从 ``delta`` 重建六区（座位级）并与终局活状态互校——"状态变了却没有
   ``delta``"（`01` 原则 6 的漏记）由此被抓到。

本文件另有一组"fuzz 本身不空转"的元测试：对**人为破坏**的局面断言检查器确实报错。
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import pytest

import sgs.agents as agents
from sgs.data import ContentPack, Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, JournalRecord, QueryRecord, RandomSource, TimingRecord
from sgs.skills import CANDIDATE_CAP
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    CardRef,
    DiscardCards,
    EndPhase,
    FrameKind,
    GameState,
    Pass,
    PlayPhaseQuery,
    SelectCards,
    SlotKind,
    UseCard,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing

from support import SeatSpec, build_state

DEFAULT_GAMES: Final = 100
DEFAULT_SECONDS: Final = 120.0
PLAYER_ROTATION: Final = (2, 4, 5, 6, 7, 8)
"""票 17 §2：六档人数确定性轮转；2 人档是无身份 1v1，护住 ADR-007 的分支。"""
GAMES_ENV: Final = "SGS_FUZZ_GAMES"
SECONDS_ENV: Final = "SGS_FUZZ_SECONDS"
SEED_ENV: Final = "SGS_FUZZ_SEED"
PLAYERS_ENV: Final = "SGS_FUZZ_PLAYERS"
SEED_SPACE: Final = 1 << 64
"""种子值域上界（票 08 §1：``0 ≤ seed < 2**64``）。"""

QUERY_BUDGET: Final = 50_000
"""单局询问数上限（票 17 §2：20 000 → 50 000）：防"引擎卡死不收局"把门禁吊住。"""

PACK: Final[ContentPack] = load_content_pack(deck_profile="standard_108")
CARD_COUNT: Final[int] = len(PACK.deck.cards)

SIDE_IDENTITIES: Final[dict[str, frozenset[Identity]]] = {
    "lord": frozenset({Identity.LORD, Identity.LOYALIST}),
    "rebel": frozenset({Identity.REBEL}),
    "renegade": frozenset({Identity.RENEGADE}),
}


class FuzzInvariantError(AssertionError):
    """不变量失败；消息里带可复现的对局种子与步号。"""


def _fail(seed: int, n: int, record: JournalRecord | None, message: str) -> None:
    if record is None:
        where = "（引擎初始化）"
    else:
        where = f"步号 seq={getattr(record, 'seq', '?')} kind={record.kind}"
    raise FuzzInvariantError(
        f"不变量失败：{message}\n"
        f"对局种子 seed={seed}，人数 n={n}，{where}\n"
        f"可复现：python -m sgs --seed {seed} --players {n}"
    )


# ---------------------------------------------------------------------------
# 驱动：库 API × 每座一个随机玩家
# ---------------------------------------------------------------------------


class _FuzzEngine(Engine):
    """fuzz 专用引擎：每 emit 一条记录就在**活状态**上验不变量（票 11 §4 的第一层）。

    ``_emit`` 是引擎写出记录的**唯一出口**；覆写它得到"每条记录之后"的观测点。
    生产代码不感知本类，引擎行为一个字节都不改。
    """

    game_seed: int = 0
    game_players: int = 2

    def _emit(self, record: JournalRecord) -> None:
        super()._emit(record)
        _check_live_invariants(self, record)


class _BudgetExceeded(Exception):
    """一局的询问数超过安全上限：引擎没有收局。"""


class _GameTimeout(Exception):
    """单局超过墙上时钟兜底：性能退化。"""


class _BudgetedPlayer:
    """给随机玩家加共享的**询问预算**与**墙上时钟兜底**，把"卡死"变成可复现的失败。"""

    def __init__(
        self,
        inner: agents.RandomPlayer,
        remaining: list[int],
        deadline: float | None,
    ) -> None:
        self._inner = inner
        self._remaining = remaining
        self._deadline = deadline

    def respond(self, query):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise _GameTimeout
        self._remaining[0] -= 1
        if self._remaining[0] < 0:
            raise _BudgetExceeded
        return self._inner.respond(query)


def _play(seed: int, *, players: int = 4, deadline: float | None = None) -> _FuzzEngine:
    """跑完一局（种子由测试侧给定、不占游戏内随机源）。

    票 14 §11：**开启武将**（``hero_pool=()`` = 数据文件的启用池，随机发放）——不变量
    集合不变，另加"候选数上界"断言；记录失败时带可复现种子（既有口径）。
    """
    engine = _FuzzEngine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=players),
        pack=PACK,
        hero_pool=(),
    )
    engine.game_seed = seed
    engine.game_players = players
    # 开局那几条记录（初次洗牌 + 发牌）由 ``build_initial`` 直接构造、不走 ``_emit``，
    # 故在这里补一次活状态检查；其后每条记录由 ``_emit`` 覆写逐一检查。
    _check_live_invariants(engine, None)
    ai_stream = RandomSource(seed).substream("ai")
    remaining = [QUERY_BUDGET]
    participants = [
        _BudgetedPlayer(agents.RandomPlayer(ai_stream), remaining, deadline)
        for _ in range(players)
    ]
    try:
        engine.run(participants)
    except _BudgetExceeded:
        _fail(seed, players, engine.records[-1], f"超过 {QUERY_BUDGET} 次询问仍未收局")
    except _GameTimeout:
        _fail(seed, players, engine.records[-1], "单局超过墙上时钟兜底仍未收局（性能退化）")
    return engine


def _play_and_check(seed: int, *, players: int, deadline: float | None = None) -> _FuzzEngine:
    engine = _play(seed, players=players, deadline=deadline)
    _check_journal_invariants(engine)
    return engine


# ---------------------------------------------------------------------------
# 第一层：活状态不变量
# ---------------------------------------------------------------------------


def _check_live_invariants(engine: _FuzzEngine, record: JournalRecord | None) -> None:
    state = engine.state
    seed = engine.game_seed
    n = engine.game_players

    for player in state.players:
        # 体力范围：hp ≤ max_hp 恒成立（唯一改体力的口 apply_hp 只查上限）。hp < 0 只允许
        # 在"伤害 / 扣减体力 / 濒死"结算在栈上时：3 点闪电把 1~2 体力打到 −1/−2 的那一刻，
        # 负体力先落进状态、濒死帧随后才压（票 20 §6）。阵亡者保留死亡时的负体力，
        # 故对 ``alive == False`` 不再要求。
        if player.hp > player.hp_max:
            _fail(seed, n, record, f"座位 {player.seat} 体力 {player.hp} 超过上限 {player.hp_max}")
        if player.hp < 0 and player.alive and not _hp_may_be_negative(engine):
            _fail(seed, n, record, f"座位 {player.seat} 体力 {player.hp} < 0 却没有伤害 / 濒死帧在栈上")

        # 阵亡者三区为空（票 17 §2 / 票 32 §2）：死亡结算清空手牌 / 装备区 / 判定区。
        # 只在**死亡单元跑完**（DEATH 帧不在栈上）时断言——``alive`` 在压 DEATH 帧之前
        # 就置位，弃牌发生在 ``DEATH_ON``，故濒死→死亡结算中间态仍持有牌是合法的。
        if (
            not player.alive
            and not _death_on_stack(engine)
            and (player.hand or player.equip or player.judge)
        ):
            _fail(
                seed,
                n,
                record,
                f"座位 {player.seat} 已阵亡，仍有手牌 {len(player.hand)} / "
                f"装备 {len(player.equip)} / 判定 {len(player.judge)}",
            )

        # 装备槽位唯一（票 17 §2）：每个座位每槽 ≤ 1（move_card 的编程错误兜底在此复验）。
        slots = [state.cards[card_id].slot for card_id in player.equip]
        if len(slots) != len(set(slots)):
            _fail(seed, n, record, f"座位 {player.seat} 装备槽位重复：{slots}")

        # 判定区同名唯一（票 17 §2）：每个座位的判定区每牌名 ≤ 1。
        names = [state.cards[card_id].name for card_id in player.judge]
        if len(names) != len(set(names)):
            _fail(seed, n, record, f"座位 {player.seat} 判定区同名牌重复：{names}")

    # 区域互斥：一张实体牌任一时刻只在一个区域（六个区：三个共享区 + 每座位三区）。
    everywhere = [*state.deck, *state.discard, *state.processing]
    for player in state.players:
        everywhere.extend(player.hand)
        everywhere.extend(player.equip)
        everywhere.extend(player.judge)
    if len(everywhere) != len(state.cards) or len(set(everywhere)) != len(everywhere):
        _fail(seed, n, record, "区域互斥被破坏：有实体牌丢失或同时出现在多个区域")

    # 手牌上限 = 当前体力值，只在**边界处属于谁的座位**上断言（摸牌途中允许超上限；
    # 对手的手牌要到它自己回合的弃牌阶段才受约束）。每个座位都会在自己回合的
    # `PHASE_DISCARD_END` / `TURN_AFTER_END` 被检查到。
    if _is_hand_limit_point(record) and state.turn_seat is not None:
        player = state.player(state.turn_seat)
        if player.alive and len(player.hand) > player.hp:
            _fail(
                seed,
                n,
                record,
                f"回合边界座位 {player.seat} 手牌 {len(player.hand)} 超过体力 {player.hp}",
            )

    _check_candidate_cap(record, seed, n)
    _check_decline_sorts_last(record, seed, n)


def _check_decline_sorts_last(record: JournalRecord | None, seed: int, n: int) -> None:
    """候选次序（retro S4）：**放弃（``Pass``）恒在最后一条**。

    ``_sort_key``（``effects/actions.py``）把"放弃"放在兜底档 ``(1, …)``、真候选放在
    ``(0, …)``，故候选里只要出现 ``Pass``，它就必须排在最后。这条不变量是**补**的：
    ``_sort_key`` 曾漏了 ``SelectOption`` 分支，让它与 ``Pass`` 同档、又按
    ``"Pass" < "SelectOption"`` 排到**真候选之前** —— 取首个候选的贪心消费者
    （``tests/test_web.py::_play_to_end``）因此"发动→取消"空转、门禁无声挂死。当时
    ``tests/test_queries_views_actions.py`` 的断言先 ``sorted()`` 再比，把要断的次序
    抹掉了，所以没拦住。**次序类断言不许先归一化**（``CODING_STANDARDS.md`` §4）。
    """
    if record is None or record.kind != "query":
        return
    actions = record.query.legal_actions
    for index, action in enumerate(actions):
        if isinstance(action, Pass) and index != len(actions) - 1:
            _fail(
                seed,
                n,
                record,
                f"『放弃』候选排在第 {index} 条（共 {len(actions)} 条），不在最后",
            )


OVER_CAP_BY_DESIGN: frozenset[str] = frozenset({"hero.liubei.rende"})
"""**按裁定**允许超过 ``CANDIDATE_CAP`` 的技能（本轮用户裁定，报障 2）。

【仁德】的前端交互是"直接多选手牌 + 直接点座位、一次交出去"：界面要能一次提交
"这组牌 → 这个座位"，所以它**不降级**、非空子集 × 目标全部进池（手牌一多必然超过 128；
6 人局实测 315 条）。这不是漏降级，别的两维相乘技能本表里一个都没有。
"""


def _check_candidate_cap(record: JournalRecord | None, seed: int, n: int) -> None:
    """候选数上界（票 04 §验收 7 / `spec.md` §Testing Decisions / 票 14 §3）：≤ ``CANDIDATE_CAP``。

    **审计口径**：上界只审**两维相乘**的技能候选——`spec.md` 的 §机制面的统一约定 明写
    "组合爆炸只对**两维相乘**的技能加闸（上界 128）"，一维的子集枚举与询问级的排列各有其
    天然的、**不属于技能层**的组合空间。按票 10 建议的"`DiscardCards` 先例"，五条一维枚举
    逐条豁免，表在 :data:`EXEMPT_ONE_DIMENSIONAL_FAMILIES`（含每条的"为什么不是两维技能
    组合"）；`.scratch/m3-standard-heroes/map.md` 的 Decisions-so-far 第 7 轮（C6）记了这条
    裁定的完整口径——**为什么字面判据"任何询问 ≤ 128"无法在不改 M2 行为的前提下满足**。

    余下的：两维相乘的 ``ActivateSkill`` **按技能分别计数**（同一询问里几条互不相关的技能
    合法地各有自己的候选，票 14 实测一局 6 手牌的【仁德】126 条 + 3 条普通候选 = 129 总数）；
    其余非技能候选按总数查。豁免表因此**仍有咬合力**：两维相乘的技能一旦忘了降级，它自己
    的那一组立刻判红（``test_the_candidate_cap_checker_catches_an_over_cap_two_dimensional_skill``）。
    本轮另加一条**按裁定**的例外 :data:`OVER_CAP_BY_DESIGN`（【仁德】：界面要一次提交
    "这组牌 → 这个座位"，故有意不降级），由 ``..._exempts_rende_by_design`` 钉住它只有一条。
    """
    if record is None or record.kind != "query":
        return
    by_skill: dict[str, int] = {}
    others = 0
    for action in record.query.legal_actions:
        if _is_one_dimensional_enumeration(action):
            continue
        if isinstance(action, ActivateSkill):
            # 审计面 = **两维相乘**的技能候选（`picks` × `choices`）：按技能分别计数。
            by_skill[action.skill_id] = by_skill.get(action.skill_id, 0) + 1
        else:
            others += 1
    for skill_id, count in by_skill.items():
        if skill_id in OVER_CAP_BY_DESIGN:
            # 【仁德】：**有意**超过上界（用户裁定）——界面要一次提交"这组牌 → 这个座位"，
            # 故它不降级、非空子集 × 目标全进池。见该常量的说明。
            continue
        if count > CANDIDATE_CAP:
            _fail(
                seed,
                n,
                record,
                f"技能 {skill_id} 的候选数 {count} 超过上界 {CANDIDATE_CAP}"
                f"（询问 {record.query.kind}）",
            )
    if others > CANDIDATE_CAP:
        _fail(
            seed,
            n,
            record,
            f"非技能候选数 {others} 超过上界 {CANDIDATE_CAP}（询问 {record.query.kind}）",
        )


@dataclass(frozen=True)
class _ExemptFamily:
    """一条**一维枚举**豁免：候选空间只在一个维度上展开，不属技能层两维相乘。"""

    label: str
    """可读标签（元测试按它与样本一一对应）。"""

    rationale: str
    """为什么它不是两维相乘的技能组合（C6 裁定里的豁免理由）。"""

    matches: Callable[[object], bool]
    """该族候选的机械判据（唯一真源：``_is_one_dimensional_enumeration`` 遍历本表）。"""


EXEMPT_ONE_DIMENSIONAL_FAMILIES: tuple[_ExemptFamily, ...] = (
    _ExemptFamily(
        "DiscardCards",
        "M2 的 C(手牌, 手牌−体力) 子集（票 04 实测 210，无武将也会撞）",
        lambda action: isinstance(action, DiscardCards),
    ),
    _ExemptFamily(
        "ArrangeCards",
        "ARRANGE 的 X!×(X+1) 排列（票 10，询问级候选、非技能组合）",
        lambda action: isinstance(action, ArrangeCards),
    ),
    _ExemptFamily(
        "ActivateSkill(choices=())",
        "一维成本子集：只动 ``picks``、不动目标维（票 04 的裁定：一维 2ⁿ 不设闸）",
        lambda action: isinstance(action, ActivateSkill) and not action.choices,
    ),
    _ExemptFamily(
        "UseCard(virtual_sources≠())",
        "M2 转化族的两张组合（丈八蛇矛 C(n,2)，票 08 实测 135）",
        lambda action: isinstance(action, UseCard) and bool(action.card.virtual_sources),
    ),
    _ExemptFamily(
        "SelectCards",
        "``SELECT_CARDS`` 的 C(n, lo..hi) 子集面：与 ``DiscardCards`` 同一类的一维枚举"
        "（票 04 迁移后【制衡】的任意张走这里；询问级候选，非技能层两维组合）",
        lambda action: isinstance(action, SelectCards),
    ),
)
"""五条一维枚举豁免的**显式表**（C6：审计口径自文档化，判据与理由同处一张表）。

顺序 = 票 10 的文档顺序；增删一条必须同时改这里与元测试的样本表，
故"悄悄放宽审计"会直接判红。
"""


def _is_one_dimensional_enumeration(action: object) -> bool:
    """五族**一维枚举**候选的机械判据（= :data:`EXEMPT_ONE_DIMENSIONAL_FAMILIES` 的并集）。"""
    return any(family.matches(action) for family in EXEMPT_ONE_DIMENSIONAL_FAMILIES)


_HP_NEGATIVE_FRAMES = (FrameKind.DAMAGE, FrameKind.LIFE_DECREASE, FrameKind.NEAR_DEATH)
"""负体力在结算中合法的帧：伤害 → 扣减体力 → 濒死（票 20 §6 的 3 点闪电）。"""


def _hp_may_be_negative(engine: Engine) -> bool:
    return any(frame.kind in _HP_NEGATIVE_FRAMES for frame in engine.stack.frames)


def _death_on_stack(engine: Engine) -> bool:
    return any(frame.kind is FrameKind.DEATH for frame in engine.stack.frames)


def _is_hand_limit_point(record: JournalRecord | None) -> bool:
    """手牌上限的检查点（票 14 §11 的读法收窄）。

    **只认弃牌阶段结束**：上限是**弃牌阶段**的性质（弃到 ≤ 体力），而 M3 的
    【闭月】在**结束阶段**摸牌——那之后 ``TURN_AFTER_END`` 的手牌合法地超过体力，
    继续在那里断言会把一条正确规则判红。不变量集合本身不变（每个存活座位仍在自己
    回合的弃牌阶段末被检查），只是把那一个**过期**的检查点去掉。
    """
    return record is not None and record.kind == "timing" and (
        record.timing is Timing.PHASE_DISCARD_END
    )


# ---------------------------------------------------------------------------
# 第二层：记录流不变量 + 从 delta 重建牌守恒并与活状态互校
# ---------------------------------------------------------------------------


def _check_journal_invariants(engine: _FuzzEngine) -> None:
    seed = engine.game_seed
    n = engine.game_players
    records = engine.records

    # 事件序号单调：全流单一 seq，从 1 起、严格递增（不必连续）。
    sequenced = [record for record in records if record.kind != "header"]
    if not sequenced or sequenced[0].seq != 1:
        _fail(seed, n, sequenced[0] if sequenced else None, "记录流的 seq 必须从 1 起")
    for previous, current in zip(sequenced, sequenced[1:]):
        if current.seq <= previous.seq:
            _fail(seed, n, current, f"事件序号必须严格递增：{previous.seq} → {current.seq}")

    # 每局必达终局，且 game_end 是最后一条记录。
    tail = records[-1]
    if tail.kind != "game_end":
        _fail(seed, n, tail, "每局必达终局：最后一条记录必须是 game_end")
    if tail.result not in {"winner", "draw"}:
        _fail(seed, n, tail, f"终局结果只能是 winner / draw，实得 {tail.result!r}")

    _check_endgame_identity_consistency(engine, tail)

    # 终局牌守恒：清栈收尾（补做 L5.6）之后处理区必空。
    if engine.state.processing:
        _fail(seed, n, tail, "终局收尾后处理区必须为空")

    _replay_deltas_and_cross_check(engine, seed, n)


def _check_endgame_identity_consistency(engine: _FuzzEngine, tail: JournalRecord) -> None:
    """终局身份-胜负一致（票 17 §2）。

    判据读**引擎全知态**，不读 ``PlayerView``（其余角色身份未亮明）。无身份局（2 人）
    沿用 M1 形状；身份局（4~8 人）按 ``side`` 断言。
    """
    seed = engine.game_seed
    n = engine.game_players
    identities = [player.identity for player in engine.state.players]

    if n == 2:
        if tail.result == "winner" and tail.winner is None:
            _fail(seed, n, tail, "无身份局 winner 局必须有胜者")
        if tail.result == "draw" and tail.winner is not None:
            _fail(seed, n, tail, "平局不得有胜者")
        return

    # 身份局：draw ⇒ side=None 且 winners 空；winner ⇒ side 合法且 winners 非空。
    if tail.result == "draw":
        if tail.side is not None or tail.winners:
            _fail(seed, n, tail, "身份局平局不得有 side / winners")
        return

    if tail.side not in SIDE_IDENTITIES:
        _fail(seed, n, tail, f"身份局胜局的 side 必须是三条之一，实得 {tail.side!r}")
        return
    if not tail.winners:
        _fail(seed, n, tail, "身份局胜局必须有 winners")
        return
    expected = {
        seat for seat, identity in enumerate(identities) if identity in SIDE_IDENTITIES[tail.side]
    }
    if set(tail.winners) != expected:
        _fail(seed, n, tail, f"winners 必须恰为该侧全部座位：期望 {sorted(expected)}，实得 {list(tail.winners)}")

    lord_seat = next(
        player.seat for player in engine.state.players if player.identity is Identity.LORD
    )
    lord_dead = not engine.state.players[lord_seat].alive
    if tail.side == "lord":
        # 主忠方胜：要么主公存活，要么主公阵亡当批只剩内奸同死（见票 14）。名字上只要求
        # winners 含主公或全部反贼与内奸已死；这里断言较弱的、结构可靠的一条：side=lord
        # 时**主公未死**或**所有反贼与内奸皆死**。
        others_dead = all(
            player.identity in SIDE_IDENTITIES["lord"] or not player.alive
            for player in engine.state.players
        )
        if not (not lord_dead or others_dead):
            _fail(seed, n, tail, "side=lord 但主公已阵亡且仍有非主忠方存活")
    if tail.side == "rebel" and not lord_dead:
        _fail(seed, n, tail, "side=rebel 必须以主公阵亡为前提")
    if tail.side == "renegade":
        if not lord_dead:
            _fail(seed, n, tail, "side=renegade 必须以主公阵亡为前提")
        alive_others = [
            player.seat
            for player in engine.state.players
            if player.alive and player.seat != lord_seat
        ]
        if not (
            len(alive_others) == 1 and identities[alive_others[0]] is Identity.RENEGADE
        ):
            _fail(seed, n, tail, f"side=renegade 但除主公外唯一存活者不是内奸：{alive_others}")


def _ids(record: JournalRecord) -> list[int]:
    return [card.card_id for card in record.cards]


@dataclass
class _Piles:
    """从 ``delta`` 重建的六个区域；逐座位的三区按座位分装（票 08 §7 / 票 16 §2）。

    区域坐标带座位，故重建是**座位级**的：``from_zone`` / ``to_zone`` 直接给出座位，
    手牌 / 装备区 / 判定区各归各座，不再是"全局手牌池"。
    """

    seats: int
    deck: list[int] = field(default_factory=list)
    discard: list[int] = field(default_factory=list)
    processing: list[int] = field(default_factory=list)
    hands: list[list[int]] = field(default_factory=list)
    equip: list[list[int]] = field(default_factory=list)
    judge: list[list[int]] = field(default_factory=list)
    slots: dict[int, SlotKind | None] = field(default_factory=dict)
    """每条记录内联的牌面槽位（``card_id`` → ``slot``），供装备区按槽位枚举序重建。"""

    @property
    def total(self) -> int:
        return (
            len(self.deck)
            + len(self.discard)
            + len(self.processing)
            + sum(len(pile) for pile in self.hands)
            + sum(len(pile) for pile in self.equip)
            + sum(len(pile) for pile in self.judge)
        )

    def reset(self, cards: list[int]) -> None:
        self.deck = list(cards)
        self.discard = []
        self.processing = []
        self.hands = [[] for _ in range(self.seats)]
        self.equip = [[] for _ in range(self.seats)]
        self.judge = [[] for _ in range(self.seats)]

    def _pile(self, ref: ZoneRef) -> list[int]:
        if ref.zone is Zone.DECK:
            return self.deck
        if ref.zone is Zone.DISCARD:
            return self.discard
        if ref.zone is Zone.PROCESSING:
            return self.processing
        if ref.seat is None:
            raise AssertionError(f"{ref.zone.value} 区按座位分装，缺座位的坐标无法定位")
        if ref.zone is Zone.HAND:
            return self.hands[ref.seat]
        if ref.zone is Zone.EQUIP:
            return self.equip[ref.seat]
        if ref.zone is Zone.JUDGE:
            return self.judge[ref.seat]
        raise AssertionError(f"未知区域：{ref.zone!r}")

    def take(self, record: JournalRecord, seed: int) -> None:
        pile = self._pile(record.from_zone)
        for card_id in _ids(record):
            if card_id not in pile:
                _fail(
                    seed,
                    self.seats,
                    record,
                    f"delta 声称从 {record.from_zone.zone.value} 移走牌 {card_id}，但它不在那里",
                )
            pile.remove(card_id)

    def place(self, record: JournalRecord) -> None:
        ref = record.to_zone
        pile = self._pile(ref)
        pile.extend(_ids(record))
        if ref.zone is Zone.EQUIP:
            # 装备区存储序 = 槽位枚举序（票 12 §Q2）；重放器必须复刻，否则座位级互校会误报。
            order = list(SlotKind)
            pile.sort(key=lambda card_id: order.index(self.slots[card_id]))

    def note_slots(self, record: JournalRecord) -> None:
        """记下这条记录内联的牌面槽位；装备区的排序键来自这里（``hp_change`` 没有牌）。"""
        for card in getattr(record, "cards", ()):
            self.slots[card.card_id] = card.slot

    def merge_reshuffle(self, record: JournalRecord, seed: int) -> None:
        """重洗：弃牌堆洗混后并入牌堆尾（票 23 的 ``_reshuffle_into_deck`` 语义）。

        先验"这次重洗记录的牌集恰是当时的弃牌堆"，再合并——否则重建只是**自洽**，
        并没有对齐活状态。
        """
        shuffled = _ids(record)
        if sorted(shuffled) != sorted(self.discard):
            _fail(seed, self.seats, record, "重洗 delta 的牌集与当时弃牌堆不符")
        self.discard = []
        self.deck.extend(shuffled)

    def arrange(self, action: ArrangeCards, cards: tuple[CardRef, ...]) -> None:
        """把一次**只记位置**的排列落到重建牌堆上（票 14 §4 / 票 10 §读法 3）。

        ``place_on_deck`` 刻意不产 delta（避免把被授予的牌面露给记录流），故排列对重建器
        是"不可见"的：牌面只能从那条 ``ARRANGE`` 询问的 ``revealed`` 取，位置从
        ``ArrangeCards.order`` / ``split`` 取——复刻
        :func:`sgs.skills.arrange._apply_arrangement` 的两次 ``place_on_deck``。
        """
        ids = [card.card_id for card in cards]
        top = [ids[index] for index in action.order[: action.split]]
        bottom = [ids[index] for index in action.order[action.split :]]
        for batch, to_top in ((top, True), (bottom, False)):
            if not batch:
                continue
            self.deck[:] = [card_id for card_id in self.deck if card_id not in batch]
            if to_top:
                self.deck[:0] = batch
            else:
                self.deck.extend(batch)


def _replay_deltas_and_cross_check(engine: _FuzzEngine, seed: int, n: int) -> None:
    """从 ``delta`` 重建六个区域，逐条查牌守恒，再与终局活状态互校。

    区域坐标带座位 ⇒ 重建是**座位级**的（票 16 §2：重放器补座位级重建），
    故"手牌 / 装备区 / 判定区归错座位"也能被抓到，而不只是池级守恒。
    """
    piles = _Piles(seats=len(engine.state.players))
    arranged: tuple[CardRef, ...] = ()
    """最近一次 ``ARRANGE`` 询问授予的牌面（自顶向下）；排列行动据此落到重建牌堆上。"""

    for record in engine.records:
        if record.kind == "query" and record.query.kind == "ARRANGE":
            arranged = tuple(record.query.revealed)
            continue
        if (
            record.kind == "action"
            and getattr(record, "accepted", False)
            and isinstance(record.action, ArrangeCards)
            and arranged
        ):
            # 排列是**同一区域内部**的重排（无 delta，见 `place_on_deck`），故重建器要
            # 在这个行动记录处自己把牌堆顺序改过来（票 14 §4）。
            piles.arrange(record.action, arranged)
            arranged = ()
            continue
        if record.kind != "delta":
            continue
        piles.note_slots(record)
        if record.delta_type == "deck_shuffle":
            if record.from_zone is None:
                piles.reset(_ids(record))
            else:
                piles.merge_reshuffle(record, seed)
        elif record.delta_type == "card_move":
            piles.take(record, seed)
            piles.place(record)
        else:
            continue  # hp_change / identity_* 不动牌，与牌守恒无关

        if piles.total != CARD_COUNT:
            _fail(
                seed,
                n,
                record,
                f"牌守恒被破坏：六个区域合计 {piles.total} 张，应为 {CARD_COUNT} 张",
            )

    if piles.deck != engine.state.deck:
        _fail(seed, n, None, "牌堆与从 delta 重建的结果不一致（状态变了却没有 delta）")
    if piles.discard != engine.state.discard:
        _fail(seed, n, None, "弃牌堆与从 delta 重建的结果不一致")
    if piles.processing != engine.state.processing:
        _fail(seed, n, None, "处理区与从 delta 重建的结果不一致")
    for seat, player in enumerate(engine.state.players):
        if piles.hands[seat] != player.hand:
            _fail(seed, n, None, f"座位 {seat} 手牌与从 delta 重建的结果不一致")
        if piles.equip[seat] != player.equip:
            _fail(seed, n, None, f"座位 {seat} 装备区与从 delta 重建的结果不一致")
        if piles.judge[seat] != player.judge:
            _fail(seed, n, None, f"座位 {seat} 判定区与从 delta 重建的结果不一致")
    if sorted(
        [
            *piles.deck,
            *piles.discard,
            *piles.processing,
            *(card_id for pile in piles.hands for card_id in pile),
            *(card_id for pile in piles.equip for card_id in pile),
            *(card_id for pile in piles.judge for card_id in pile),
        ]
    ) != list(range(CARD_COUNT)):
        _fail(seed, n, None, "牌守恒被破坏：并非每张实体牌恰在一个区域")


# ---------------------------------------------------------------------------
# 预算、人数轮转与种子（测试侧）
# ---------------------------------------------------------------------------


def _env_positive(name: str, default, convert):
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = convert(raw)
    assert value > 0, f"{name} 必须是正数，得到 {raw!r}"
    return value


def _players_for(game_index: int) -> int:
    """人数轮转：默认 2/4/5/6/7/8 六档确定性轮转；``SGS_FUZZ_PLAYERS`` 覆盖成固定值。"""
    raw = os.environ.get(PLAYERS_ENV)
    if raw is not None:
        value = int(raw)
        assert value in PLAYER_ROTATION, f"{PLAYERS_ENV} 必须是 {PLAYER_ROTATION} 之一"
        return value
    return PLAYER_ROTATION[game_index % len(PLAYER_ROTATION)]


def _seed_base() -> int:
    """种子基数：默认取测试侧 OS 熵，不占游戏内随机源；``SGS_FUZZ_SEED`` 可复跑同一序列。"""
    raw = os.environ.get(SEED_ENV)
    if raw is not None:
        return int(raw) % SEED_SPACE
    return secrets.randbits(64)


# ---------------------------------------------------------------------------
# 性质检验
# ---------------------------------------------------------------------------


def test_random_games_satisfy_the_invariants():
    """默认 100 局 / 120 s：以先到者为准；默认预算内跑不满即为性能退化（票 17 §2）。"""
    game_limit = _env_positive(GAMES_ENV, DEFAULT_GAMES, int)
    seconds = _env_positive(SECONDS_ENV, DEFAULT_SECONDS, float)
    base = _seed_base()
    deadline = time.monotonic() + seconds

    played = 0
    while played < game_limit:
        if time.monotonic() >= deadline:
            pytest.fail(
                f"不变量 fuzz 超时：{seconds:g}s 内只跑完 {played}/{game_limit} 局（性能退化，票 17 §2）。\n"
                f"本次种子基数 base={base}；用 {SEED_ENV}={base} 可复跑同一序列。"
            )
        players = _players_for(played)
        _play_and_check((base + played) % SEED_SPACE, players=players, deadline=deadline)
        played += 1

    assert played == game_limit


def test_the_player_rotation_covers_the_six_identity_branches():
    """六档轮转是确定性的，且 2 人档（无身份分支）确在轮转里（票 17 §2）。"""
    rotation = [_players_for(index) for index in range(len(PLAYER_ROTATION))]

    assert set(rotation) == set(PLAYER_ROTATION)
    assert len(rotation) == len(set(rotation))
    assert 2 in rotation


# ---------------------------------------------------------------------------
# 元测试：fuzz 检查器不空转
# ---------------------------------------------------------------------------


def _drop_one_card(state: GameState) -> None:
    for pile in (state.discard, state.deck, state.processing):
        if pile:
            pile.pop()
            return
    for player in state.players:
        if player.hand:
            player.hand.pop()
            return
    raise AssertionError("没有可丢的牌，测试前提不成立")


def _duplicate_one_card(state: GameState) -> None:
    for pile in (state.discard, state.deck, state.processing):
        if pile:
            pile.append(pile[0])
            return
    for player in state.players:
        if player.hand:
            player.hand.append(player.hand[0])
            return
    raise AssertionError("没有可复制的牌，测试前提不成立")


def test_the_live_checker_catches_a_lost_card():
    engine = _play(1)
    _drop_one_card(engine.state)

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_a_duplicated_card():
    engine = _play(1)
    _duplicate_one_card(engine.state)

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_covers_the_equipment_and_judge_zones():
    """区域互斥扩到六个区：把一张已在别处的牌塞进装备区 / 判定区必须被抓到（票 21）。"""
    engine = _play(1)
    pile = engine.state.discard or engine.state.deck or engine.state.processing
    assert pile, "这一局必然有牌落在共享区"
    survivor = next(player for player in engine.state.players if player.alive)
    survivor.equip.append(pile[0])

    with pytest.raises(FuzzInvariantError, match="区域互斥"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_hp_above_the_maximum():
    engine = _play(1)
    player = engine.state.players[0]
    player.hp = player.hp_max + 1

    with pytest.raises(FuzzInvariantError, match="超过上限"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_live_checker_catches_a_hand_over_the_limit_at_the_discard_phase_end():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=0, hp_max=4, hand=(0,)), SeatSpec(hp=4)],
        deck=tuple(range(1, CARD_COUNT)),
        turn_seat=0,
    )
    engine = _FuzzEngine.from_state(state, seed=1)
    engine.game_seed = 1
    engine.game_players = 2
    boundary = TimingRecord(seq=999, timing=Timing.PHASE_DISCARD_END, frame_id=0, depth=0)

    with pytest.raises(FuzzInvariantError, match="手牌"):
        _check_live_invariants(engine, boundary)


def test_the_live_checker_catches_a_dead_player_holding_cards():
    """阵亡者三区为空（票 32 §2）：给阵亡者留一张手牌必须被抓到。"""
    engine = _play(1)
    victim = engine.state.players[1]
    victim.alive = False
    victim.hand.append(engine.state.deck[0])
    engine.state.deck.remove(engine.state.deck[0])

    with pytest.raises(FuzzInvariantError, match="已阵亡"):
        _check_live_invariants(engine, engine.records[-1])


def test_the_replay_check_catches_live_state_drifting_from_the_deltas():
    engine = _play(1)
    engine.state.discard.append(0)  # 状态变了却没有 delta

    with pytest.raises(FuzzInvariantError, match="不一致"):
        _replay_deltas_and_cross_check(engine, engine.game_seed, engine.game_players)


def test_the_failure_message_carries_the_seed_the_player_count_and_the_step():
    engine = _play(1)
    _drop_one_card(engine.state)

    with pytest.raises(FuzzInvariantError) as caught:
        _check_live_invariants(engine, engine.records[-1])

    message = str(caught.value)
    assert "seed=1" in message
    assert f"n={engine.game_players}" in message
    assert f"步号 seq={engine.records[-1].seq}" in message
    assert "python -m sgs --seed 1" in message


def _query_record(state, actions) -> QueryRecord:
    query = PlayPhaseQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="测试用",
        legal_actions=tuple(actions),
        view=project(state, 0),
    )
    return QueryRecord(seq=1, query=query)


def test_the_candidate_cap_checker_catches_an_over_cap_query():
    """合成反例：超过闸值的询问必红（票 04 §验收 7 不空转）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    record = _query_record(
        state, [EndPhase(answers_seq=1) for _ in range(CANDIDATE_CAP + 1)]
    )

    with pytest.raises(FuzzInvariantError, match="候选数"):
        _check_candidate_cap(record, 1, 2)

    green = _query_record(state, [EndPhase(answers_seq=1) for _ in range(CANDIDATE_CAP)])
    _check_candidate_cap(green, 1, 2)


def test_the_candidate_cap_checker_catches_an_over_cap_two_dimensional_skill():
    """两维相乘的技能候选超闸必红（票 14 §3：豁免变宽之后咬合力还在）。

    ``picks``（成本）与 ``choices``（目标）两个维度都在变的 ``ActivateSkill`` 是上界
    真正要管的形状；构造一组超过 128 的两维候选，检查器必须报错。
    【仁德】是本轮**有意**豁免的例外。票 03 迁走【结姻】后，本族已**没有**真实的
    两维相乘技能（那条 id 只作合成载荷的载体），故这里纯粹给检查器造样本、不再指向
    任何在产的降级路径。
    """
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    action = ActivateSkill(
        skill_id="hero.sunshangxiang.jieyin", picks=(0,), choices=("seat:1",), answers_seq=1
    )
    record = _query_record(state, [action for _ in range(CANDIDATE_CAP + 1)])

    with pytest.raises(FuzzInvariantError, match="候选数"):
        _check_candidate_cap(record, 1, 2)


def test_the_decline_sorts_last_checker_catches_a_pass_before_real_candidates():
    """合成反例（retro S4）：「放弃」排在真候选**之前**必红 —— 这正是 `_sort_key` 曾经的坑。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    record = _query_record(state, [Pass(answers_seq=1), EndPhase(answers_seq=1)])

    with pytest.raises(FuzzInvariantError, match="放弃"):
        _check_decline_sorts_last(record, 1, 2)


def test_the_decline_sorts_last_checker_passes_when_pass_is_last():
    """对照面：「放弃」在最后（`_sort_key` 的兜底档）时放行。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    record = _query_record(state, [EndPhase(answers_seq=1), Pass(answers_seq=1)])

    _check_decline_sorts_last(record, 1, 2)  # 不抛 = 放行


def test_the_candidate_cap_checker_exempts_rende_by_design():
    """【仁德】是本轮**唯一**的"按裁定超上界"技能：例外集合不许无声变宽。"""
    assert OVER_CAP_BY_DESIGN == {"hero.liubei.rende"}, (
        "超上界例外只该有【仁德】（用户裁定：界面要一次提交『这组牌 → 这个座位』）"
    )

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    action = ActivateSkill(
        skill_id="hero.liubei.rende", picks=(0,), choices=("seat:1",), answers_seq=1
    )
    over_cap = CANDIDATE_CAP + 1

    _check_candidate_cap(_query_record(state, [action for _ in range(over_cap)]), 1, 2)  # 不抛 = 放行


def test_the_candidate_cap_checker_exempts_the_one_dimensional_enumerations():
    """五族一维枚举都放行，且**豁免表与样本一一对应**（票 14 §3 的审计口径 / C6）。

    这条是豁免规则的机械覆盖：
    ① 表恰五条、每条都有非空理由；
    ② 每条理由对应的候选样本**命中**该族判据；
    ③ 每条样本哪怕堆到 ``CANDIDATE_CAP + 1`` 也不报错（= 真的被放行）。
    """
    assert len(EXEMPT_ONE_DIMENSIONAL_FAMILIES) == 5, "豁免表恰五族（C6 的裁定 + 票 04 的 SelectCards）"
    assert all(family.rationale for family in EXEMPT_ONE_DIMENSIONAL_FAMILIES), (
        "每条豁免都必须写明『为什么不是两维技能组合』"
    )

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    card = state.card(0)
    virtual = CardRef(
        card_id=-1, type="slash", name="杀（丈八蛇矛转化）", virtual_sources=(0, 1)
    )
    over_cap = CANDIDATE_CAP + 1
    samples = {
        "DiscardCards": [
            DiscardCards(cards=(card,), answers_seq=1) for _ in range(over_cap)
        ],
        "ArrangeCards": [
            ArrangeCards(order=(0,), split=0, answers_seq=1) for _ in range(over_cap)
        ],
        "ActivateSkill(choices=())": [
            ActivateSkill(skill_id="hero.sunquan.zhiheng", picks=(0,), answers_seq=1)
            for _ in range(over_cap)
        ],
        "UseCard(virtual_sources≠())": [
            UseCard(card=virtual, targets=(), answers_seq=1) for _ in range(over_cap)
        ],
        "SelectCards": [
            SelectCards(picks=(0,), answers_seq=1) for _ in range(over_cap)
        ],
    }

    assert set(samples) == {family.label for family in EXEMPT_ONE_DIMENSIONAL_FAMILIES}, (
        "样本表与豁免表必须逐条对应（增删一族时两处同改）"
    )
    for family in EXEMPT_ONE_DIMENSIONAL_FAMILIES:
        actions = samples[family.label]
        assert all(family.matches(action) for action in actions), (
            f"{family.label} 的样本必须命中该族判据"
        )
        _check_candidate_cap(_query_record(state, actions), 1, 2)  # 不抛 = 放行

