"""Golden Replay 的场景脚本玩家（票 10 §6 / 票 12 §3）。

**只在录制 / 更新时使用**（``pytest --update-golden``）。每个 slug 声明一个**写死的
``seed`` 常量**：开发时从候选区间搜出第一个满足目标段判据的种子后写死，录制期零搜索。

两条段机制（票 12 §3）：

- **目标段**：按 slug 精准命中该链；
- **收尾段**：固定确定性策略——能出【杀】就出【杀】；被【杀】且手里有【闪】就用【闪】，
  否则放弃；濒死且手里有【桃】就**只自救**（对方濒死一律放弃）；出牌阶段不用【桃】。

录制失败（目标段没命中 / 超过回合上限 / 没走到终止点）就是**录制失败**，
必须重新选种并显式改写 ``SCENARIOS`` 里的常量，而不是放宽断言。

票 22 之后**每条基准都以 ``game_end`` 收局**（真胜负或牌堆耗尽平局）：死亡结算、
胜负判定、清栈与终局收尾都已落地。

票 23 追加 ``06-deck-reshuffle``：重洗没有对应的询问，脚本看不见它，故用
:class:`_ReshuffleWatcher` 在每次应答前扫一遍记录流，出现重洗才把目标段切到收尾段。
重洗落地后"两堆皆空"在实战不可达，故除平局构造局面外每条基准都靠**打死一方**收局。
"""

from __future__ import annotations

from dataclasses import dataclass

from sgs.agents import RandomPlayer
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource, dump_journal
from sgs.state import Action, EndPhase, Pass, Query, UseCard
from sgs.timing import Timing

from .goals import goal_for

__all__ = [
    "M1_DECK_PROFILE",
    "M2_DECK_PROFILE",
    "SCENARIOS",
    "Scenario",
    "play_random",
    "record",
]

M1_DECK_PROFILE = "basic_53"
"""M1 六条基准**显式选中的档位**（票 16 §9）。

产品默认档在 M2 集成票翻到 ``standard_108`` 之后，M1 回归网仍必须跑 53 张切片——
这里写死档位名，不让"默认档"的改动泄漏进基准录制。"""

M2_DECK_PROFILE = "standard_108"
"""M2 的 28 条基准跑**标准版全表**（票 32 §1）。"""


@dataclass(frozen=True)
class Scenario:
    """一条基准的录制参数；``max_turns`` 是终止保证（票 12 §3）。

    ``driver`` 是录制期的驱动：M1 的六条用 ``"scripted"``（``ScenarioPlayer`` 迫使目标链
    发生）；M2 的 28 条用 ``"random"``（与产品同源的随机玩家跑完整局，事后判据只负责
    "这条基准要证的事真的发生过"）。磁带冻结决策内容，回放期一律 ``TapePlayer``。

    ``heroes`` / ``hero_pool``（票 14 §8）：M3 的专项基准用**显式逐座位武将**
    （``heroes``）保证被测技能在场；缺省 ``None`` ⇒ 无武将模式，M2 的 34 条保持逐字节不变。
    """

    slug: str
    seed: int
    max_turns: int
    player_count: int = 2
    deck_profile: str = M1_DECK_PROFILE
    driver: str = "scripted"
    heroes: tuple[str, ...] | None = None
    hero_pool: tuple[str, ...] | None = None


M1_SCENARIOS: dict[str, Scenario] = {
    "01-slash-dodge-cancel": Scenario(slug="01-slash-dodge-cancel", seed=101, max_turns=40),
    "02-slash-hit-damage": Scenario(slug="02-slash-hit-damage", seed=4, max_turns=40),
    "03-near-death-self-peach": Scenario(
        slug="03-near-death-self-peach", seed=28, max_turns=40
    ),
    "04-near-death-other-peach": Scenario(
        slug="04-near-death-other-peach", seed=9, max_turns=40
    ),
    "05-near-death-death": Scenario(slug="05-near-death-death", seed=1, max_turns=40),
    "06-deck-reshuffle": Scenario(slug="06-deck-reshuffle", seed=2026, max_turns=80),
}
"""M1 的 6 条基准（票 12 §3）；`06-deck-reshuffle` 由票 23 追加。"""


def _m2(slug: str, *, seed: int, player_count: int, max_turns: int = 400) -> Scenario:
    return Scenario(
        slug=slug,
        seed=seed,
        max_turns=max_turns,
        player_count=player_count,
        deck_profile=M2_DECK_PROFILE,
        driver="random",
    )


M2_SCENARIOS: dict[str, Scenario] = {
    "07-identity-4p-rebel-win": _m2("07-identity-4p-rebel-win", seed=233, player_count=4),
    "08-identity-5p-lord-win": _m2("08-identity-5p-lord-win", seed=148, player_count=5),
    "09-identity-6p-renegade-win": _m2(
        "09-identity-6p-renegade-win", seed=521, player_count=6
    ),
    "10-identity-7p-lord-win": _m2("10-identity-7p-lord-win", seed=49, player_count=7),
    "11-identity-8p-rebel-win": _m2("11-identity-8p-rebel-win", seed=37, player_count=8),
    "12-indulgence-skips-play": _m2(
        "12-indulgence-skips-play", seed=258, player_count=4
    ),
    "13-indulgence-heart-misses": _m2(
        "13-indulgence-heart-misses", seed=43, player_count=5
    ),
    "14-lightning-thunder-damage": _m2(
        "14-lightning-thunder-damage", seed=285, player_count=4
    ),
    "15-lightning-defers-next-seat": _m2(
        "15-lightning-defers-next-seat", seed=28, player_count=4
    ),
    "16-nullify-cancels-a-trick": _m2(
        "16-nullify-cancels-a-trick", seed=298, player_count=6
    ),
    "17-nullify-nested-two": _m2("17-nullify-nested-two", seed=117, player_count=4),
    "18-nullify-nested-three": _m2("18-nullify-nested-three", seed=14, player_count=4),
    "19-nullify-on-multi-target": _m2(
        "19-nullify-on-multi-target", seed=213, player_count=5
    ),
    "20-nullify-lightning-defers": _m2(
        "20-nullify-lightning-defers", seed=105, player_count=4
    ),
    "21-rebel-killed-reward": _m2("21-rebel-killed-reward", seed=101, player_count=4),
    "22-lord-kills-loyalist": _m2("22-lord-kills-loyalist", seed=35, player_count=5),
    "23-two-deaths-revealed": _m2("23-two-deaths-revealed", seed=36, player_count=4),
    "24-multi-player-peach-window": _m2(
        "24-multi-player-peach-window", seed=272, player_count=5
    ),
    "25-rollback-same-start": _m2("25-rollback-same-start", seed=241, player_count=4),
    "26-rollback-start-advances": _m2(
        "26-rollback-start-advances", seed=133, player_count=4
    ),
    "27-slash-beyond-adjacent": _m2(
        "27-slash-beyond-adjacent", seed=89, player_count=6
    ),
    "28-equipment-replacement": _m2(
        "28-equipment-replacement", seed=165, player_count=4
    ),
    "29-serpent-spear-virtual-slash": _m2(
        "29-serpent-spear-virtual-slash", seed=106, player_count=4
    ),
    "30-halberd-three-targets": _m2(
        "30-halberd-three-targets", seed=230, player_count=6
    ),
    "31-play-card-response": _m2("31-play-card-response", seed=190, player_count=4),
    "32-five-grains-selection": _m2(
        "32-five-grains-selection", seed=170, player_count=4
    ),
    "33-borrowed-sword-use-slash": _m2(
        "33-borrowed-sword-use-slash", seed=105, player_count=5
    ),
    "34-dismantle-or-snatch-equip-judge": _m2(
        "34-dismantle-or-snatch-equip-judge", seed=288, player_count=4
    ),
}
"""M2 的 28 条基准（票 32 §1）：随机驱动 + 事后判据，种子由开发期搜索得到。

种子策略（票 17 §9 的实现期落点）：在允许的人数档里搜**记录条数最少**的命中局，
再贪心分配使 28 个 ``(人数, 种子)`` 互不相同——更短的局 = 更小的 golden 文件 +
更快的门禁回放。搜索脚手架在 ``.scratch/t32/``（被 gitignore，不进交付）。"""


def _m3(slug: str, *, seed: int, heroes: tuple[str, ...], player_count: int = 4, max_turns: int = 400) -> Scenario:
    """M3 专项基准（票 14 §8）：显式逐座位武将 + 随机驱动 + 事后判据。"""
    return Scenario(
        slug=slug,
        seed=seed,
        max_turns=max_turns,
        player_count=player_count,
        deck_profile=M2_DECK_PROFILE,
        driver="random",
        heroes=heroes,
    )


M3_SCENARIOS: dict[str, Scenario] = {
    # B 族逐点伤害：【遗计】在一次多点伤害里问两次（郭嘉在场）。
    # 种子 150 → 109：遗计修复（只在**持有者**受伤时触发）后，150 那局的郭嘉不再因别人
    # 受伤而反复触发，判据（≥2 次逐点）不再命中。按本模块的纪律**重新选种**而不是放宽判据：
    # 109 在同样四名武将下命中判据，且记录条数更少（925 vs 3111），基准更小。
    "35-hero-damage-points": _m3(
        "35-hero-damage-points",
        seed=109,
        heroes=("guojia", "caocao", "simayi", "luxun"),
    ),
    # I 族改判循环：司马懿【鬼才】的改判窗口（判定牌生效前打出代替）。
    "36-hero-rejudge-cycle": _m3(
        "36-hero-rejudge-cycle",
        seed=169,
        heroes=("simayi", "guojia", "sunquan", "luxun"),
    ),
    # F 族势力求助窗口：主公在曹操 / 刘备之中，且**同势力**的求助对象在场（司马懿 / 关羽）。
    # 种子 116 → 94：【奸雄】修复（牌不再被 L5.6 从曹操手里弃掉）后，116 那局的走向改变、
    # 势力求助窗口不再出现。按本模块纪律**重新选种**而不是放宽判据：94 在同样四名武将下
    # 命中判据，且记录条数更少（910 vs 原局）。
    "37-hero-lord-request": _m3(
        "37-hero-lord-request",
        seed=94,
        heroes=("caocao", "simayi", "liubei", "guanyu"),
    ),
    # D 族失去牌后：【连营】+【枭姬】含顶替。
    # 种子 76 → 103：票 03 迁移【结姻】（候选由"手牌对 × 目标"改为恒一条骨架 + 两步流水线）
    # 改变了随机驱动在同一局里的行动序列，76 那局不再同时命中【连营】/【枭姬】/顶替三件事；
    # 随后修掉 `_sort_key` 缺 `SelectOption` 分支的候选次序 bug（Pass 曾排在真候选之前），
    # 又让中途选的 890 不再命中。按本模块纪律**重新选种**而不是放宽判据：103 在同样四名
    # 武将下命中判据，且记录条数最少（1563，候选由 `.scratch/seed38.py` 扫 0–1199 得到）。
    "38-hero-lost-cards": _m3(
        "38-hero-lost-cards",
        seed=103,
        heroes=("luxun", "sunshangxiang", "guanyu", "sunquan"),
    ),
    # C 族目标池真排除：【空城】+【谦逊】。
    "39-hero-target-pool": _m3(
        "39-hero-target-pool",
        seed=128,
        heroes=("zhugeliang", "luxun", "sunquan", "lvbu"),
    ),
    # E 族响应窗口张数：吕布【无双】两张【闪】。
    "40-hero-wushuang": _m3(
        "40-hero-wushuang",
        seed=46,
        heroes=("lvbu", "guanyu", "zhaoyun", "luxun"),
    ),
    # G 族授予可见性与排列：诸葛亮【观星】。
    "41-hero-guanxing": _m3(
        "41-hero-guanxing",
        seed=202,
        heroes=("zhugeliang", "sunquan", "luxun", "guojia"),
    ),
    # H 族转化牌泛化：武圣 / 龙胆 / 国色 / 急救。
    "42-hero-transform": _m3(
        "42-hero-transform",
        seed=145,
        heroes=("guanyu", "zhaoyun", "daqiao", "huatuo"),
    ),
}
"""8 条 M3 专项基准（票 14 §8）：每条 pin 一族机制的整局记录流，种子由开发期搜索得到。"""

SCENARIOS: dict[str, Scenario] = {**M1_SCENARIOS, **M2_SCENARIOS, **M3_SCENARIOS}


@dataclass
class _Recording:
    """一次录制的进行时状态。"""

    goal_done: bool = False
    goal_seat: int | None = None
    """命中目标段的那一步是由哪个座位做出的（用于把"已命中"告诉另一个座位）。"""

    def announce(self, seat: int) -> None:
        self.goal_done = True
        self.goal_seat = seat


class _ReshuffleWatcher:
    """把"记录流里出现了重洗"告诉场景脚本（票 23）。

    重洗没有对应的询问，脚本玩家自己看不见；它发生在摸牌阶段、下一问（出牌阶段）
    之前，故在 ``respond`` 入口处扫一遍记录流即可把目标段及时切到收尾段。
    """

    def __init__(self, engine: Engine, recording: _Recording, seat: int, inner: "ScenarioPlayer") -> None:
        self._engine = engine
        self._recording = recording
        self._seat = seat
        self._inner = inner

    def respond(self, query: Query) -> Action:
        if not self._recording.goal_done and _reshuffle_seen(self._engine):
            self._recording.announce(self._seat)
        return self._inner.respond(query)


def _reshuffle_seen(engine: Engine) -> bool:
    return any(
        record.kind == "delta"
        and record.delta_type == "deck_shuffle"
        and record.from_zone is not None
        for record in engine.records
    )


class ScenarioPlayer:
    """按 slug 走目标段、其余走收尾段的脚本玩家（票 12 §3）。"""

    def __init__(self, seat: int, slug: str, recording: _Recording) -> None:
        self.seat = seat
        self.slug = slug
        self.recording = recording

    def respond(self, query: Query) -> Action:
        target = self._target_segment(query)
        if target is not None:
            return target
        return _cleanup_for(self.seat, query)

    def _peers_goal(self) -> bool:
        """目标段已由**另一个座位**命中：本座位此后只走收尾段。"""
        return self.recording.goal_done and self.recording.goal_seat != self.seat

    def _self_goal(self) -> bool:
        """目标段已由**本座位**命中：目标段结束，本座位改走收尾段。"""
        return self.recording.goal_done and self.recording.goal_seat == self.seat

    def _any_goal(self) -> bool:
        """目标段已经命中过（无论哪个座位）。"""
        return self.recording.goal_done

    def _target_segment(self, query: Query) -> Action | None:
        if self.slug == "01-slash-dodge-cancel":
            if self.recording.goal_done:
                return None
            return self._slash_dodge_cancel(query)
        if self.slug == "02-slash-hit-damage":
            if self.recording.goal_done:
                return None
            return self._slash_hit_damage(query)
        if self.slug == "03-near-death-self-peach":
            return self._near_death_self_peach(query)
        if self.slug == "04-near-death-other-peach":
            return self._near_death_other_peach(query)
        if self.slug == "05-near-death-death":
            return self._near_death_death(query)
        if self.slug == "06-deck-reshuffle":
            # 目标段"不进攻"：只摸牌 + 弃至上限，直到 `_ReshuffleWatcher` 宣告重洗已出现。
            if self.recording.goal_done:
                return None
            return _idle(query)
        raise AssertionError(f"没有为场景 {self.slug} 写目标段")  # pragma: no cover - 开发期护栏

    # ---- 01 / 02：座位 0 出【杀】，座位 1 在【闪】窗口给出互斥的两种应答 ----

    def _slash_dodge_cancel(self, query: Query) -> Action | None:
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            dodge = _use(query, "dodge")
            if dodge is not None:
                self.recording.announce(self.seat)
            return dodge
        return None

    def _slash_hit_damage(self, query: Query) -> Action | None:
        if self.seat == 0 and query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,))
        if self.seat == 1 and query.kind == "DODGE":
            self.recording.announce(self.seat)
            return _find(query, Pass)
        return None

    # ---- 03：座位 1 在自己的回合被打到 0，被求桃时自救 ----

    def _near_death_self_peach(self, query: Query) -> Action | None:
        if query.kind == "PLAY_PHASE" and self.seat == 0 and not self._any_goal():
            # 命中前：反复进攻；命中后收手，好让另一方能活到牌堆耗尽。
            return _use(query, "slash", targets=(1,))
        if self._peers_goal() or self._self_goal():
            return None
        if self.seat == 1 and query.kind == "PEACH" and query.dying == 1:
            peach = _use(query, "peach", targets=(1,))
            if peach is not None:
                self.recording.announce(self.seat)
                return peach
            return _find(query, Pass)
        return None

    # ---- 04：座位 0（当前回合角色）在求桃窗口救座位 1 ----

    def _near_death_other_peach(self, query: Query) -> Action | None:
        if query.kind == "PLAY_PHASE" and self.seat == 0 and not self._any_goal():
            return _use(query, "slash", targets=(1,))
        if self._peers_goal() or self._self_goal():
            return None
        if self.seat == 0 and query.kind == "PEACH" and query.dying == 1:
            peach = _use(query, "peach", targets=(1,))
            if peach is not None:
                self.recording.announce(self.seat)
                return peach
            return _find(query, Pass)
        return None

    # ---- 05：座位 1 濒死而双方均放弃 ----

    def _near_death_death(self, query: Query) -> Action | None:
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            return _use(query, "slash", targets=(1,)) if self.seat == 0 else _find(query, EndPhase)
        if query.kind == "DODGE":
            return _find(query, Pass)
        if query.kind == "PEACH" and query.dying == 1:
            if not self.recording.goal_done:
                self.recording.announce(self.seat)
            return _find(query, Pass)
        return None


def _use(query: Query, type_id: str, targets: tuple[int, ...] | None = None) -> Action | None:
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            continue
        if targets is not None and action.targets != targets:
            continue
        return action
    return None


def _idle(query: Query) -> Action:
    """目标段"不进攻"：能结束阶段就结束、能放弃就放弃，弃牌取候选里的自然后者。"""
    if query.kind == "PLAY_PHASE":
        return _find(query, EndPhase)
    if query.kind in ("DODGE", "PEACH"):
        return _find(query, Pass)
    return query.legal_actions[0]


def _cleanup_for(seat: int, query: Query) -> Action:
    """收尾段（带座位）：能杀就杀、有闪就闪；濒死时只自救。"""
    if query.kind == "PLAY_PHASE":
        slash = _use(query, "slash")
        return slash if slash is not None else _find(query, EndPhase)
    if query.kind == "DODGE":
        dodge = _use(query, "dodge")
        return dodge if dodge is not None else _find(query, Pass)
    if query.kind == "PEACH":
        if query.dying == seat:
            peach = _use(query, "peach")
            return peach if peach is not None else _find(query, Pass)
        return _find(query, Pass)
    return query.legal_actions[0]


def _find(query: Query, action_type: type) -> Action:
    for action in query.legal_actions:
        if isinstance(action, action_type):
            return action
    raise AssertionError(f"{query.kind} 询问里没有 {action_type.__name__} 候选")  # pragma: no cover


def _record_scripted(slug: str, scenario: Scenario) -> Engine:
    """M1 路径：``ScenarioPlayer`` 迫使目标段发生。"""
    pack = load_content_pack(deck_profile=scenario.deck_profile)
    engine = Engine.new_game(
        seed=scenario.seed,
        rule_config=RuleConfig(player_count=scenario.player_count),
        pack=pack,
    )
    recording = _Recording()
    players: list = [
        ScenarioPlayer(seat, slug, recording) for seat in range(scenario.player_count)
    ]
    if slug == "06-deck-reshuffle":
        players = [
            _ReshuffleWatcher(engine, recording, seat, player)
            for seat, player in enumerate(players)
        ]
    engine.run(players)
    assert recording.goal_done, f"{slug}：目标段没有命中（seed={scenario.seed}）"
    return engine


def play_random(
    seed: int,
    player_count: int,
    *,
    deck_profile: str = M2_DECK_PROFILE,
    pack=None,
    heroes: tuple[str, ...] | None = None,
    hero_pool: tuple[str, ...] | None = None,
) -> Engine:
    """随机驱动跑完整局：M2 录制路径与**种子搜索**（``seed_search.py``）共用的唯一驱动。

    磁带冻结的是决策内容，回放与驱动无关；但"搜到的种子"必须与"录制时跑的种子"是同一局，
    否则搜索会拿别的局去验证判据。故只留这一份实现，两边都调它。

    ``heroes`` / ``hero_pool``（票 14 §8）与 :meth:`Engine.new_game` 同义：缺省 = 无武将。
    """
    if pack is None:
        pack = load_content_pack(deck_profile=deck_profile)
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=player_count),
        pack=pack,
        heroes=heroes,
        hero_pool=hero_pool,
    )
    stream = RandomSource(seed).substream("ai")
    engine.run([RandomPlayer(stream) for _ in range(player_count)])
    return engine


def _record_random(slug: str, scenario: Scenario) -> Engine:
    """M2/M3 路径：随机驱动跑完整局，事后判据断言目标机制真的发生过（票 32 §1 / 票 14 §8）。"""
    engine = play_random(
        scenario.seed,
        scenario.player_count,
        deck_profile=scenario.deck_profile,
        heroes=scenario.heroes,
        hero_pool=scenario.hero_pool,
    )
    assert goal_for(slug)(engine), (
        f"{slug}：事后判据没命中（seed={scenario.seed}, n={scenario.player_count}）；"
        f"改种子前先读 goals.py 里这条判据"
    )
    return engine


def record(slug: str) -> str:
    """跑完整局并返回 canonical JSONL；录制期断言目标链与终止保证。"""
    scenario = SCENARIOS[slug]
    if scenario.driver == "random":
        engine = _record_random(slug, scenario)
    else:
        engine = _record_scripted(slug, scenario)

    assert engine.records[-1].kind == "game_end", f"{slug}：没有走到终局"
    assert engine.records[-1].result in ("winner", "draw"), "终局结果只能是胜负或平局"
    turns = sum(
        1
        for record_ in engine.records
        if record_.kind == "timing" and record_.timing is Timing.TURN_START
    )
    assert turns <= scenario.max_turns, f"{slug}：{turns} 回合超过上限 {scenario.max_turns}"
    return dump_journal(engine.records)
