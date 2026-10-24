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
from sgs.skills import validate_activation
from sgs.state import (
    Action,
    ActivateSkill,
    DiscardCards,
    EndPhase,
    Pass,
    Query,
    SelectOption,
    SlotKind,
    UseCard,
    WindowCause,
)
from sgs.timing import Timing

from .goals import PREACTIVATION_CANCEL_SKILL, goal_for
from . import m5, m6

__all__ = [
    "M1_DECK_PROFILE",
    "M2_DECK_PROFILE",
    "M5_SCENARIOS",
    "M6_SCENARIOS",
    "PREACTIVATION_CANCEL_SCENARIO",
    "SCENARIOS",
    "Scenario",
    "engine_for",
    "play_random",
    "preactivation_cancel_pair",
    "record",
]

M1_DECK_PROFILE = "basic_53"
"""M1 六条基准**显式选中的档位**（票 16 §9）。

产品默认档在 M2 集成票翻到 ``standard_108`` 之后，M1 回归网仍必须跑 53 张切片——
这里写死档位名，不让"默认档"的改动泄漏进基准录制。"""

M2_DECK_PROFILE = "standard_108"
"""M2 的随机基准跑**标准版全表**（票 32 §1）。"""

PREACTIVATION_CANCEL_SCENARIO = "44-preactivation-cancel-no-side-effect"
"""W4-5 的**预发动取消零副作用**专项基准（脚本驱动，座 0 = 孙权）。

它证的不是某条技能的效果，而是**接入层本地态**：进入预发动 → 按清单填好复合动作（含
交给引擎同一个校验器的那次计算）→ **取消**，与"从不进预发动"的同一局逐字节相等。
"""

PREACTIVATION_CANCEL_HEROES: tuple[str, ...] = ("sunquan", "guanyu")
"""座 0 孙权给出【制衡】的预发动清单（本专项的取消对象）；座 1 关羽只是填满 2 人局。"""

HALBERD_BORROW_SCENARIO = "47-halberd-borrowed-sword"
"""W7-01 的脚本专项：**借刀窗口里的方天（真【杀】）**——Bn + 2 个额外目标。

场景（4 人局，座 0 = 使用者、座 1 = 持刀者，**种子 1733**）：座 0 起手有【借刀杀人】；
座 1 起手有【方天画戟】与恰好一张【杀】，其余全是可在出牌阶段装上 / 用掉的牌（用不掉的
牌 ⇒ 淘汰该种子，见本模块的录制纪律）。座 1 先装其它装备、**最后**换上方天（保证它留在
武器槽），把手牌用到只剩那张【杀】；座 0 在座 1 有武器后的出牌阶段按清单指定
`[A=1, Bn=0]`，持刀者在窗口选**目标数最多**的 `UseCard`（3 目标），不是首条单目标候选。
种子 1733 是**脚本基准**的种子（不是 Web 实机种子；后者由 `tools/acceptance_seed.py`
另行挑选）。
"""

HALBERD_BORROW_HOLDER = 1
"""持刀者的座位（方天 + 最后一张手牌【杀】）。"""

HALBERD_BORROW_USER = 0
"""使用者的座位（起手有【借刀杀人】；Bn = 0 指向他自己）。"""

DOUBLE_SWORD_HEROES: tuple[str, ...] = ("liubei", "sunshangxiang", "guanyu", "luxun")
"""票 17 的脚本基准写死的逐座位武将（座 0 刘备男 / 座 1 孙尚香女 ⇒ 异性目标**必然**在场）。

性别由**武将数据**派生（``_initial_player``），无武将局里性别恒 ``None`` ⇒【雌雄双股剑】
的 ``available`` 恒假——这是复现困难的一半根因，故这条基准必须显式指定武将。座位 2 / 3
只是填满 4 人身份局；换成别人不改这条基准要证的事。"""


@dataclass(frozen=True)
class Scenario:
    """一条基准的录制参数；``max_turns`` 是终止保证（票 12 §3）。

    ``driver`` 是录制期的驱动：M1 的六条用 ``"scripted"``（``ScenarioPlayer`` 迫使目标链
    发生）；M2 的 27 条用 ``"random"``（与产品同源的随机玩家跑完整局，事后判据只负责
    "这条基准要证的事真的发生过"）。磁带冻结决策内容，回放期一律 ``TapePlayer``。

    **W4-fix-2 的两个例外**：``29-serpent-spear-virtual-slash`` 与 ``42-hero-transform``
    改回 ``"scripted"``——转化候选已从 ``legal_actions`` 收回（用户裁定 A：转化技只经
    预发动），随机驱动**不可能**再产出转化动作；脚本按预发动清单确认一次，记录流里落的
    仍是服务端解析出的那条普通使用 / 打出（``goals.py`` 的判据因此一字不改）。

    **W5-02b 的例外**：``33-borrowed-sword-use-slash`` 仍是「普通锦囊与打出」的覆盖项
    （类不变），但借刀入口只剩两步清单、随机驱动产不出借刀动作，驱动因此改成
    ``"scripted"`` 并移出 M2 的“随机驱动 + 种子搜索”表；45/46 是同一改动的两条脚本专项
    （auto-b / 窗口内转化【杀】）。

    ``heroes`` / ``hero_pool``（票 14 §8）：M3 的专项基准用**显式逐座位武将**
    （``heroes``）保证被测技能在场；缺省 ``None`` ⇒ 无武将模式，既有 M2 基准保持逐字节不变。
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
"""M1 的六条基准（票 12 §3 定格，`06` 由票 23 追加）。"""

SCRIPTED_SCENARIOS: dict[str, Scenario] = {
    # 票 17：给【雌雄双股剑】一条**确定性可复现**的脚本基准（先复现，再谈要不要改）。
    # 要证的事需要三件事同时成立：某人**装备**了雌雄、手里有【杀】、对**异性**目标使用。
    # 实机里三件事都靠运气——缺省局的武将随机发放（性别因此随机），而标准牌堆只有**一张**
    # 雌雄；随机驱动的 M2/M3 基准从没命中过这条链。这条基准用脚本驱动把三件事按次序焊死，
    # 整局记录流因此成了一份可直接开来看的录像。种子 77 由开发期搜索得到（座 0 起始手牌
    # 同时有雌雄与【杀】、座 1 为异性；`.scratch/t17/search_double_sword.py`，被 gitignore）。
    "43-double-sword-opposite-gender": Scenario(
        slug="43-double-sword-opposite-gender",
        seed=77,
        max_turns=60,
        player_count=4,
        deck_profile=M2_DECK_PROFILE,
        heroes=DOUBLE_SWORD_HEROES,
    ),
    # W4-5：【预发动取消零副作用】。脚本在座 0 的第一条随附【制衡】清单的出牌阶段询问上
    # 按清单**填一个复合动作**（并交给引擎同一个校验器）然后**取消**；同一颗种子再跑一局
    # "从不进预发动"作对照，两条记录流必须逐字节相等（`_record_scripted` 与
    # `tests/test_replays.py` 各钉一次）。取消是本地态，录制物因此与"没发动"完全同形。
    # 种子 9：座 0 起手有【杀】（收尾段能推进对局），且第一回合就随附【制衡】清单；
    # 2 人局的记录条数最少（407 条 ≈ 140 KB，7 个回合就以座 1 阵亡收局）。
    PREACTIVATION_CANCEL_SCENARIO: Scenario(
        slug=PREACTIVATION_CANCEL_SCENARIO,
        seed=9,
        max_turns=80,
        player_count=2,
        heroes=PREACTIVATION_CANCEL_HEROES,
    ),
    # W5-02b：【借刀杀人】的 auto-b（a 不可行 ⇒ 无窗口、持刀者交出武器）。
    # 脚本驱动：座 1 装上武器并在自己的回合用掉【杀】（次数闸门挡住 a），座 0 下一回合
    # 按清单指定 [A=1, Bn=0]；结算时非 Pass 候选为空 ⇒ 直接 b。种子 1：该牌序下座 1
    # 有武器、座 0 有借刀（seed 0 目标段不命中，探针见本 effort 的 seed_probe.py）。
    "45-borrowed-sword-auto-handover": Scenario(
        slug="45-borrowed-sword-auto-handover",
        seed=1,
        max_turns=80,
        player_count=2,
        deck_profile=M2_DECK_PROFILE,
    ),
    # W5-02b：【借刀窗口里的转化【杀】】。座 1 = 赵云，装上武器后**不使用**真【杀】，
    # 座 0 下一回合按清单指定 [A=1, Bn=0]；持刀者在窗口按【龙胆】清单确认那张转化【杀】。
    "46-borrowed-sword-transform-slash": Scenario(
        slug="46-borrowed-sword-transform-slash",
        seed=0,
        max_turns=80,
        player_count=2,
        deck_profile=M2_DECK_PROFILE,
        heroes=("guanyu", "zhaoyun"),
    ),
    # W7-01：借刀窗口里的方天（真【杀】）——Bn + 2 个额外目标。种子由开发期搜索得到
    # （`.scratch/w7-fangtian/search47.py`，被 gitignore）：4 人局、座 0 有借刀、座 1 拿到
    # 方天 + 恰好一张【杀】且其余牌都能在出牌阶段用掉 / 装上（用不掉 ⇒ 淘汰该种子）。
    HALBERD_BORROW_SCENARIO: Scenario(
        slug=HALBERD_BORROW_SCENARIO,
        seed=1733,
        max_turns=200,
        player_count=4,
        deck_profile=M2_DECK_PROFILE,
    ),
    # W5-02b：借刀原来的 M2 随机基准。裸候选从 `legal_actions` 收回（入口只剩两步清单）
    # 后，随机驱动产不出借刀动作，故改**脚本驱动**；它仍是「普通锦囊与打出」的覆盖项
    # （`distribution.py` 的类不变），但不再属于 M2 的“随机驱动 + 种子搜索”面，故移到
    # 本表。目标段 = 座 0 按清单指定 [持刀者, Bn]，持刀者用真【杀】。
    "33-borrowed-sword-use-slash": Scenario(
        slug="33-borrowed-sword-use-slash",
        seed=0,
        max_turns=80,
        player_count=2,
        deck_profile=M2_DECK_PROFILE,
    ),
}
"""票 17 起的**脚本驱动专项基准**：与 M1 六条同驱动（``ScenarioPlayer``），但不属于
票 12 定格的回归网，故**单列**——M1 那张表被
``test_the_frozen_m1_six_stay_in_the_file_set`` 逐键钉死，往里加条目等于谎报回归网。"""


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
    # W5-02b：借刀的裸候选收回后，seed 148 的随机局不再以主忠胜收局（判据 `_side_is("lord")`
    # 未命中）；按 retro S6 的纪律重选 148 → 197（`seed_search --slug ... --limit 200` 的
    # 最小候选，1593 条），不放松判据、不做脚本化。
    "08-identity-5p-lord-win": _m2("08-identity-5p-lord-win", seed=197, player_count=5),
    "09-identity-6p-renegade-win": _m2(
        "09-identity-6p-renegade-win", seed=521, player_count=6
    ),
    # W5-02b：种子 49 → 68。借刀裸候选 / 转化清单改变随机驱动的走向后，49 重录涨到
    # 6465 条 / 6.6 MiB（超单文件体积绊线）；按本模块纪律**重新选种**：68 命中同一判据
    # （7 人主忠胜）且是 0–99 里记录条数最少的候选（2897 条），判据与人数档一字未改。
    "10-identity-7p-lord-win": _m2("10-identity-7p-lord-win", seed=68, player_count=7),
    # W4-fix-2：种子 37 → 99。转化候选从 `legal_actions` 收回后随机驱动的走向改变，
    # 37 那局涨到 6.93 MiB（超单文件体积绊线）；按本模块纪律**重新选种**而不是调预算：
    # 99 命中同一判据（8 人反贼胜）且在该档位搜索空间（0–199）里记录条数最少
    # （约 2.3 MB / 17 回合）。
    # W5-02b：种子 99 → 35。02b 的候选面变化让 99 重录涨到 3501 条 / 4.4 MiB（超单文件
    # 绊线）；0–99 里命中最少的是 35（1827 条），判据（8 人反贼胜）与人数档不变。
    "11-identity-8p-rebel-win": _m2("11-identity-8p-rebel-win", seed=35, player_count=8),
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
    # W5-01（B1）：种子 165 → 13。旧录像里有一条**非法动作**——【无懈可击】被当成
    # 【闪】用出去抵消【杀】（保存下来的 bug 现场）。修后回放必然失配，故按本模块纪律
    # **重新选种**而不是保留那条非法动作：13 命中同一判据（装备替换），且记录条数最少
    # （1092；`PYTHONPATH=tests .venv/bin/python -m replays.seed_search --slug
    # 28-equipment-replacement --limit 60` 的前 5 名里最少，旧种子 165 重录后涨到 4.83 MiB
    # 会撞单文件体积绊线）。
    "28-equipment-replacement": _m2(
        "28-equipment-replacement", seed=13, player_count=4
    ),
    # W4-fix-2：转化技只经预发动 ⇒ 随机驱动产不出转化动作，本条改**脚本驱动**
    # （目标段 = 装上丈八蛇矛后按清单确认一次）。种子见 `SCENARIOS` 处的说明。
    "29-serpent-spear-virtual-slash": Scenario(
        slug="29-serpent-spear-virtual-slash",
        seed=106,
        max_turns=400,
        player_count=4,
        deck_profile=M2_DECK_PROFILE,
    ),
    "30-halberd-three-targets": _m2(
        "30-halberd-three-targets", seed=230, player_count=6
    ),
    "31-play-card-response": _m2("31-play-card-response", seed=190, player_count=4),
    "32-five-grains-selection": _m2(
        "32-five-grains-selection", seed=170, player_count=4
    ),
    "34-dismantle-or-snatch-equip-judge": _m2(
        "34-dismantle-or-snatch-equip-judge", seed=288, player_count=4
    ),
}
"""M2 的 27 条基准（票 32 §1）：随机驱动 + 事后判据，种子由开发期搜索得到。

种子策略（票 17 §9 的实现期落点）：在允许的人数档里搜**记录条数最少**的命中局，
再贪心分配使 27 个 ``(人数, 种子)`` 互不相同——更短的局 = 更小的 golden 文件 +
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
    # 种子 109 → 198（U2-12 / D9.1）：删掉【遗计】选牌步的 ``Pass`` 后，109 那局的随机驱动
    # 不再命中判据（≥2 次逐点发动）——``Pass`` 是一整类候选，删掉它改变了随机档的行动序列。
    # 按同一纪律**重新选种**而不是放宽判据：198 在同样四名武将下命中判据，且记录条数最少
    # （804 条，票 10 的 ``yiji_seed_probe.py 200`` 扫 1–200 的候选集得到）。
    "35-hero-damage-points": _m3(
        "35-hero-damage-points",
        seed=198,
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
    # 种子 76 → 103 → 23 → 579：票 03 迁移【结姻】、票 03 修候选次序（Pass 曾排在真候选之前）、
    # 票 04 迁移【制衡】（本局有孙权），每一步都改变了随机驱动的行动序列；W4-2 再删掉
    # 【制衡】/【结姻】的 PLAY_PHASE 骨架候选，23 那局不再命中【连营】/【枭姬】/顶替三件事。
    # 按本模块纪律**重新选种**而不是放宽判据：579 在同样四名武将下命中判据，且记录条数最少
    # （897，`python -m replays.seed_search --slug 38-hero-lost-cards --limit 900` 扫 0–899 得到）。
    # W4-fix-2：种子 579 → 364——收回转化候选后 579 那局不再命中三件事；364 命中判据且
    # 记录条数最少（扫描 1–599 的候选集，约 874 KB / 11 回合）。
    # W5-02b：种子 364 → 388——借刀候选面改变后 364 那局不再命中三件事；在 0–699 的
    # 扫描里 388 命中判据且记录条数最少（1355，同一组四名武将），判据一字未改。
    "38-hero-lost-cards": _m3(
        "38-hero-lost-cards",
        seed=388,
        heroes=("luxun", "sunshangxiang", "guanyu", "sunquan"),
    ),
    # C 族目标池真排除：【空城】+【谦逊】。
    "39-hero-target-pool": _m3(
        "39-hero-target-pool",
        seed=128,
        heroes=("zhugeliang", "luxun", "sunquan", "lvbu"),
    ),
    # E 族响应窗口张数：吕布【无双】两张【闪】。
    # W4-fix-2：种子 46 → 396——收回转化候选后 46 那局不再出现"连续两张【闪】"；
    # 396 命中同一判据且记录条数最少（扫描 1–599，约 349 KB / 4 回合）。
    "40-hero-wushuang": _m3(
        "40-hero-wushuang",
        seed=396,
        heroes=("lvbu", "guanyu", "zhaoyun", "luxun"),
    ),
    # G 族授予可见性与排列：诸葛亮【观星】。
    # 种子 202 → 626：票 04 迁移【制衡】（本局有孙权）改变了随机驱动的行动序列，
    # 202 那局不再命中【观星】的排列判据；按本模块纪律**重新选种**，626 记录条数最少（533）。
    "41-hero-guanxing": _m3(
        "41-hero-guanxing",
        seed=626,
        heroes=("zhugeliang", "sunquan", "luxun", "guojia"),
    ),
    # H 族转化牌泛化：武圣 / 龙胆 / 国色 / 急救。
    # W4-fix-2：同 29——转化候选已从 `legal_actions` 收回，随机驱动产不出转化动作，
    # 本条改**脚本驱动**（目标段 = 任意一名转化族武将按清单确认一次）。
    "42-hero-transform": Scenario(
        slug="42-hero-transform",
        seed=145,
        max_turns=400,
        player_count=4,
        deck_profile=M2_DECK_PROFILE,
        heroes=("guanyu", "zhaoyun", "daqiao", "huatuo"),
    ),
}
"""8 条 M3 专项基准（票 14 §8）：每条 pin 一族机制的整局记录流，种子由开发期搜索得到。

W4-fix-2：``42-hero-transform`` 改脚本驱动（转化技的唯一入口 = 预发动清单），其余七条
仍是随机驱动。"""

M5_SCENARIOS: dict[str, Scenario] = {
    # M5-09a：8 条**构造局面**基准（spec D14 / §6 A3）。种子只是 header 里的记录，
    # 局面的手牌 / 装备 / 牌堆顶全部写死在 ``replays/m5.py``，不依赖种子搜索。
    "48-fire-slash-vine-armor": Scenario(
        slug="48-fire-slash-vine-armor",
        seed=901,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "49-iron-chain-thunder-conduction": Scenario(
        slug="49-iron-chain-thunder-conduction",
        seed=902,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "50-wine-silver-lion-cap": Scenario(
        slug="50-wine-silver-lion-cap",
        seed=903,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "51-fire-attack-reveal-discard": Scenario(
        slug="51-fire-attack-reveal-discard",
        seed=904,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "52-scimitar-empty-hand-hualiu": Scenario(
        slug="52-scimitar-empty-hand-hualiu",
        seed=905,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "53-supply-shortage-skips-draw": Scenario(
        slug="53-supply-shortage-skips-draw",
        seed=906,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "54-reforge-iron-chain": Scenario(
        slug="54-reforge-iron-chain",
        seed=907,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
    "55-fan-converts-normal-slash": Scenario(
        slug="55-fan-converts-normal-slash",
        seed=908,
        max_turns=80,
        player_count=2,
        deck_profile=m5.M5_DECK_PROFILE,
        driver="m5",
    ),
}
"""8 条 M5 军争基准（M5-09a）：覆盖 11 个新牌名与 spec §6 A3 的 10 个覆盖项。

驱动是 ``"m5"``：**从构造局面起跑**（``Engine.from_state`` + :mod:`replays.m5` 的脚本
计划），不是随机种子；回放侧用同一份构造（:func:`engine_for`）重开。
"""

M6_SCENARIOS: dict[str, Scenario] = {
    # M6 票 20：5 条**构造局面**基准，覆盖 spec §投影与记录流 的新原语（技能失效 / 获得、
    # 武将牌上的牌区、额外回合与轮次、标签增删）。局面写死在 ``replays/m6.py``，种子只是
    # header 里的记录（与 M5 同姿态）。
    "56-jie-guanyu-yijue-suppress": Scenario(
        slug="56-jie-guanyu-yijue-suppress",
        seed=1001,
        max_turns=40,
        player_count=2,
        deck_profile=m6.M6_DECK_PROFILE,
        driver="m6",
    ),
    "57-jie-xushu-awakening": Scenario(
        slug="57-jie-xushu-awakening",
        seed=1002,
        max_turns=40,
        player_count=2,
        deck_profile=m6.M6_DECK_PROFILE,
        driver="m6",
    ),
    "58-jie-luxun-hero-zone": Scenario(
        slug="58-jie-luxun-hero-zone",
        seed=1003,
        max_turns=40,
        player_count=2,
        deck_profile=m6.M6_DECK_PROFILE,
        driver="m6",
    ),
    "59-jie-lvmeng-extra-turn": Scenario(
        slug="59-jie-lvmeng-extra-turn",
        seed=1004,
        max_turns=40,
        player_count=2,
        deck_profile=m6.M6_DECK_PROFILE,
        driver="m6",
    ),
    "60-jie-zhenji-luoshen-tags": Scenario(
        slug="60-jie-zhenji-luoshen-tags",
        seed=1005,
        max_turns=40,
        player_count=2,
        deck_profile=m6.M6_DECK_PROFILE,
        driver="m6",
    ),
}
"""5 条 M6 界局构造基准（票 20）：覆盖 M6 新原语（spec §投影与记录流）。

驱动是 ``"m6"``：从构造局面起跑（``Engine.from_state`` + :mod:`replays.m6` 的脚本计划），
回放侧用同一份构造（:func:`engine_for`）重开。"""


SCENARIOS: dict[str, Scenario] = {
    **M1_SCENARIOS,
    **M2_SCENARIOS,
    **M3_SCENARIOS,
    **SCRIPTED_SCENARIOS,
    **M5_SCENARIOS,
    **M6_SCENARIOS,
}


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
    """按 slug 走目标段、其余走收尾段的脚本玩家（票 12 §3）。

    ``engine``（票 17）：目标段要按**局面事实**（性别）挑目标时用得着；缺省 ``None`` 时
    只有不读局面的目标段能用（01–06 全是这一类，故它们的调用点一字未改）。
    """

    def __init__(
        self, seat: int, slug: str, recording: _Recording, engine: Engine | None = None
    ) -> None:
        self.seat = seat
        self.slug = slug
        self.recording = recording
        self.engine = engine

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
        if self.slug == "43-double-sword-opposite-gender":
            if self.recording.goal_done:
                return None
            return self._double_sword(query)
        if self.slug == "29-serpent-spear-virtual-slash":
            return self._serpent_spear_transform(query)
        if self.slug == "42-hero-transform":
            return self._enter_transform(query)
        if self.slug == "33-borrowed-sword-use-slash":
            return self._borrowed_use_slash(query)
        if self.slug == "45-borrowed-sword-auto-handover":
            return self._borrowed_auto_handover(query)
        if self.slug == "46-borrowed-sword-transform-slash":
            return self._borrowed_transform_slash(query)
        if self.slug == HALBERD_BORROW_SCENARIO:
            return self._halberd_borrow(query)
        raise AssertionError(f"没有为场景 {self.slug} 写目标段")  # pragma: no cover - 开发期护栏

    # ---- 29 / 42（W4-fix-2）：转化技的唯一入口 = **预发动清单** ----
    #
    # 转化候选已从 ``legal_actions`` 收回（用户裁定 A），随机驱动**不可能**再产出转化动作。
    # 这两条基准改为脚本驱动：按清单确认一次转化（来源 / 目标由清单给），记录流里落的是
    # 服务端解析出的那条普通使用 / 打出——``goals.py`` 的判据因此一字不改。

    def _serpent_spear_transform(self, query: Query) -> Action | None:
        """29：先装上丈八蛇矛，再按清单确认一次"两张手牌当【杀】"。"""
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            equip = _use(query, "serpent_spear")
            if equip is not None:
                return equip
        return self._enter_transform(query, "equip.serpent_spear")

    def _enter_transform(self, query: Query, skill_id: str | None = None) -> Action | None:
        """按清单确认一条转化技（来源 / 目标取清单的首行）；没有清单 ⇒ ``None``（走收尾段）。"""
        manifests = getattr(query, "transform_preactivations", None) or {}
        for key in sorted(manifests):
            if skill_id is not None and key != skill_id:
                continue
            answers = _manifest_first_answers(manifests[key])
            if answers is None:
                continue
            picks, choices = answers
            self.recording.announce(self.seat)
            return ActivateSkill(
                skill_id=key, picks=picks, choices=choices, answers_seq=query.seq
            )
        return None

    # ---- 45 / 46 / 33（W5-02b）：借刀的两个入口 + a / b ----
    #
    # 借刀的裸候选已从 ``legal_actions`` 收回（入口只剩两步清单），随机驱动**不可能**再
    # 产出借刀动作；三条基准改为脚本驱动：座 0 按清单指定 [持刀者 A=1, Bn=0]，
    # 持刀者侧再决定 a（使用【杀】/ 转化【杀】）或 b（交出武器 / auto-b）。

    def _borrowed_auto_handover(self, query: Query) -> Action | None:
        """45：a 不可行 ⇒ 无窗口自动 b（持刀者交出武器，记录流里没有询问）。"""
        return self._borrowed_segments(query, holder=1, window="none")

    def _borrowed_transform_slash(self, query: Query) -> Action | None:
        """46：持刀者在借刀窗口按转化清单确认【杀】（龙胆）。"""
        return self._borrowed_segments(query, holder=1, window="transform")

    def _borrowed_use_slash(self, query: Query) -> Action | None:
        """33：持刀者在借刀窗口用**真【杀】**（a 支）。"""
        return self._borrowed_segments(query, holder=1, window="slash")

    def _borrowed_segments(self, query: Query, *, holder: int, window: str) -> Action | None:
        if self.seat == 0:
            return self._borrowed_user(query, holder)
        if self.seat == holder:
            return self._borrowed_holder(query, window)
        return None

    def _borrowed_user(self, query: Query, holder: int) -> Action | None:
        """座 0：等持刀者装上武器，再按清单指定 [A=holder, Bn]；保留手里的借刀。

        指定成功即目标段结束，本座位回到收尾段（它会用【杀】把对局推向终局）。
        """
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            if _seat_has_weapon(self.engine, holder):
                action = _first_designation(query, "borrowed_sword")
                if action is not None:
                    self.recording.announce(self.seat)
                    return action
            return _find(query, EndPhase)
        if query.kind == "DISCARD":
            keep = _hand_card_of_type(self.engine, self.seat, "borrowed_sword")
            return _discard_keeping(query, {keep} if keep is not None else set())
        if query.kind == "DODGE":
            return _use(query, "dodge") or _find(query, Pass)
        if query.kind == "PEACH":
            return _use(query, "peach") or _find(query, Pass)
        return None

    def _borrowed_holder(self, query: Query, window: str) -> Action | None:
        """持刀者：先装武器；``window`` 决定它在借刀窗口里怎么答。

        借刀窗口**先于** ``goal_done`` 判断：使用者已按清单指定（目标段命中）之后，
        持刀者仍要把这一层 a / b 答完；答完才回到收尾段。
        """
        if query.kind == "RESPOND" and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD:
            if window == "transform":
                action = self._enter_transform(query, "hero.zhaoyun.longdan")
                if action is not None:
                    self.recording.announce(self.seat)
                    return action
            if window == "slash":
                action = _use(query, "slash")
                if action is not None:
                    self.recording.announce(self.seat)
                    return action
            return _find(query, Pass)
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            weapon = _unequipped_weapon(query)
            if weapon is not None:
                return weapon
            if window == "none":
                # auto-b 的前提：本回合已用过【杀】（次数闸门挡住 a），或手里根本没有。
                slash = _use(query, "slash")
                if slash is not None:
                    return slash
            return _find(query, EndPhase)
        if query.kind == "DISCARD":
            keep = None
            if window == "transform":
                keep = _hand_card_of_type(self.engine, self.seat, "dodge")
            elif window == "slash":
                keep = _hand_card_of_type(self.engine, self.seat, "slash")
            return _discard_keeping(query, {keep} if keep is not None else set())
        if query.kind == "DODGE":
            return _use(query, "dodge") or _find(query, Pass)
        if query.kind == "PEACH":
            return _use(query, "peach") or _find(query, Pass)
        return None

    # ---- 47（W7-01）：借刀窗口里的方天（真【杀】）----
    #
    # 座 0 = 使用者（起手借刀，指定 [A=1, Bn=0]）；座 1 = 持刀者（方天 + 最后一张手牌【杀】）。
    # 持刀者在窗口里**选目标数最多**的候选——这条不能用 `_manifest_first_answers`：真【杀】
    # 走的是 `legal_actions`（不是清单），首条候选是单目标的 `(Bn,)`。

    def _halberd_borrow(self, query: Query) -> Action | None:
        if self.seat == HALBERD_BORROW_USER:
            return self._halberd_borrow_user(query)
        if self.seat == HALBERD_BORROW_HOLDER:
            return self._halberd_borrow_holder(query)
        return None

    def _halberd_borrow_user(self, query: Query) -> Action | None:
        """座 0：等持刀者装上武器，再按清单指定 `[A=1, Bn=0]`；借刀一路留着。"""
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            holder_slash = _hand_card_of_type(self.engine, HALBERD_BORROW_HOLDER, "slash")
            if _seat_has_weapon(self.engine, HALBERD_BORROW_HOLDER) and holder_slash is not None:
                action = _designation_for(
                    query,
                    "borrowed_sword",
                    holder=HALBERD_BORROW_HOLDER,
                    designate=HALBERD_BORROW_USER,
                )
                if action is not None:
                    self.recording.announce(self.seat)
                    return action
            return _find(query, EndPhase)
        if query.kind == "DISCARD":
            keep = _hand_card_of_type(self.engine, self.seat, "borrowed_sword")
            return _discard_keeping(query, {keep} if keep is not None else set())
        if query.kind == "DODGE":
            return _use(query, "dodge") or _find(query, Pass)
        if query.kind == "PEACH":
            return _use(query, "peach") or _find(query, Pass)
        return None

    def _halberd_borrow_holder(self, query: Query) -> Action | None:
        """座 1：先把自己打空到只剩【杀】，再在借刀窗口选**目标数最多**的组合。

        出牌顺序是判据的一部分：**其它装备先装、方天最后装**（后装的武器占槽，方天必须
        是最终留在武器槽里的那张）；自指的延时锦囊顺手用掉；多余的【杀】只用一张
        （次数闸门限制），剩下的牌只要能用的都用——用不掉就说明这颗种子该淘汰。
        """
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD
        ):
            slashes = [
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == "slash"
            ]
            if slashes:
                self.recording.announce(self.seat)
                return max(slashes, key=lambda action: (len(action.targets), action.targets))
            return _find(query, Pass)
        if self.recording.goal_done:
            return None
        if query.kind == "PLAY_PHASE":
            other_equipment = _use_equipment(query, excluding_type="halberd")
            if other_equipment is not None:
                return other_equipment
            halberd = _use(query, "halberd")
            if halberd is not None:
                return halberd
            delayed = _use_one_of(query, ("indulgence", "lightning"))
            if delayed is not None:
                return delayed
            spare = self._spare_slash(query)
            if spare is not None:
                return spare
            return _find(query, EndPhase)
        if query.kind == "DISCARD":
            keep = _hand_card_of_type(self.engine, self.seat, "slash")
            return _discard_keeping(query, {keep} if keep is not None else set())
        if query.kind == "DODGE":
            return _use(query, "dodge") or _find(query, Pass)
        if query.kind == "PEACH":
            return _use(query, "peach") or _find(query, Pass)
        return None

    def _spare_slash(self, query: Query) -> Action | None:
        """手里还有**不止一张**【杀】时，用掉多余的那张（留一张给借刀窗口的方天）。"""
        state = self.engine.state
        slashes = [
            card_id
            for card_id in state.player(self.seat).hand
            if state.card(card_id).type == "slash"
        ]
        if len(slashes) <= 1:
            return None
        return _use(query, "slash")

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

    # ---- 43：座位 0 装雌雄 → 对异性座位 1 出【杀】 → 目标答"令其摸一张" ----

    def _double_sword(self, query: Query) -> Action | None:
        """把【雌雄双股剑】的三件事按次序焊死：装雌雄 → 对异性出【杀】 → 目标选一支。

        目标从**局面事实**里挑（性别不同的最近座位），不是写死座 1——写死的目标一旦
        不是异性，这条基准就证不了"异性 ⇒ 询问 / 同性 ⇒ 不问"这条判据。
        """
        if query.kind == "PLAY_PHASE" and self.seat == 0:
            # 先装雌雄（装备牌在出牌阶段就是一次 ``UseCard``），装上以后每个出牌阶段都
            # 对异性目标出【杀】——一击不中（目标有【闪】/ 贴了防具）就下一轮再来，
            # 这正是实机里的被动姿态。
            equip = _use(query, "double_sword")
            if equip is not None:
                return equip
            target = self._opposite_gender_target()
            if target is not None:
                slash = _use(query, "slash", targets=(target,))
                if slash is not None:
                    return slash
        if query.kind == "DODGE":
            return _find(query, Pass)
        if query.kind == "SELECT_OPTION":
            self.recording.announce(self.seat)
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "draw_one":
                    return action
        return None

    def _opposite_gender_target(self) -> int | None:
        """局面上第一个与座 0 **异性**的存活座位；没有则 ``None``（性别未知也算没有）。"""
        state = self.engine.state
        source = state.player(0)
        for seat in range(len(state.players)):
            player = state.player(seat)
            if (
                seat != 0
                and player.alive
                and source.gender is not None
                and player.gender is not None
                and source.gender is not player.gender
            ):
                return seat
        return None


class _PreativationCancelPlayer(ScenarioPlayer):
    """44：**进预发动 → 填好复合动作 → 取消**（``enter=True``）与**从不进**（``enter=False``）。

    两条路径的差别只有接入层本地态：

    - 取消路径：在座 0 第一条随附【制衡】清单的出牌阶段询问上，按清单填一个复合
      ``ActivateSkill``，再把它交给**引擎同一个校验器**（``validate_activation``）——这一步
      就是界面上"确认键已可点"所需的全部计算；然后**取消**（``_target_segment`` 返回
      ``None``，本询问照常由收尾段应答）。
    - 不发动路径：根本不读清单，直接由收尾段应答。

    取消 / 填表都不向引擎提交任何东西，故两条路径的记录流必须逐字节相等
    （:func:`preactivation_cancel_pair` 在录制期与门禁期各断言一次）。
    """

    def __init__(
        self, seat: int, slug: str, recording: _Recording, engine: Engine, *, enter: bool
    ) -> None:
        super().__init__(seat, slug, recording, engine)
        self._enter = enter
        self._cancelled = False

    def _target_segment(self, query: Query) -> Action | None:
        if (
            not self._enter
            or self._cancelled
            or self.seat != 0
            or query.kind != "PLAY_PHASE"
            or PREACTIVATION_CANCEL_SKILL not in query.preactivations
        ):
            return None
        filled = _filled_activation(query)
        reason = validate_activation(
            self.engine.state,
            self.seat,
            filled.skill_id,
            filled.picks,
            filled.choices,
        )
        assert reason is None, (
            "取消专项：按清单填好的复合动作必须被同一个校验器放行"
            f"（否则清单与校验器已漂移：{reason}）"
        )
        self._cancelled = True
        self.recording.announce(self.seat)
        return None


def _filled_activation(query: Query) -> ActivateSkill:
    """按清单给【制衡】填一个复合动作：代价牌取第一步候选池的第一项（恰好一张 ≥ 下限）。"""
    steps = query.preactivations.get(PREACTIVATION_CANCEL_SKILL, ())
    assert steps and steps[0].pool and steps[0].pool[0].options, (
        "取消专项：【制衡】清单必须带代价牌候选（否则这次「进预发动」没有可填的东西）"
    )
    pick = int(steps[0].pool[0].options[0])
    return ActivateSkill(
        skill_id=PREACTIVATION_CANCEL_SKILL,
        picks=(pick,),
        choices=(),
        answers_seq=query.seq,
    )


def _manifest_first_answers(steps) -> tuple[tuple[int, ...], tuple[str, ...]] | None:
    """清单的第一组答案（W4-fix-2 的脚本确认）：来源取来源步前 ``min`` 项、目标取首行。

    逐选项子池按 ``when``（前序答案文本）匹配，与界面 / AI 读清单的方式同源；填不出来
    （池被服务端退化 / 没有来源）返回 ``None``，调用方退回收尾段。
    """
    picks: list[int] = []
    choices: list[str] = []
    prior: list[str] = []
    for step in sorted(steps, key=lambda item: item.order):
        row = next((entry for entry in step.pool if tuple(entry.when) == tuple(prior)), None)
        if row is None:
            row = next((entry for entry in step.pool if not entry.when), None)
        if row is None or not row.options:
            return None
        if step.name == "card":
            take = max(1, step.min)
            chosen = [str(option) for option in row.options[:take]]
            picks.extend(int(text) for text in chosen)
        else:
            chosen = [str(row.options[0])]
            choices.extend(chosen)
        prior.extend(chosen)
    if not picks:
        return None
    return tuple(picks), tuple(choices)


def _use(query: Query, type_id: str, targets: tuple[int, ...] | None = None) -> Action | None:
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != type_id:
            continue
        if targets is not None and action.targets != targets:
            continue
        return action
    return None


def _seat_has_weapon(engine: Engine, seat: int) -> bool:
    """``seat`` 的装备区里有没有武器（借刀第 0 层的第一个条件）。"""
    state = engine.state
    return any(
        state.card(card_id).slot is SlotKind.WEAPON
        for card_id in state.player(seat).equip
    )


def _hand_card_of_type(engine: Engine, seat: int, type_id: str) -> int | None:
    """``seat`` 手里第一张该牌名的牌（脚本的"要留住它"）。"""
    state = engine.state
    for card_id in state.player(seat).hand:
        if state.card(card_id).type == type_id:
            return card_id
    return None


def _unequipped_weapon(query: Query) -> Action | None:
    """出牌阶段里第一张可用的武器牌（装上它，借刀的第 0 层才成立）。"""
    for action in query.legal_actions:
        if isinstance(action, UseCard) and action.card.slot is SlotKind.WEAPON:
            return action
    return None


def _first_designation(query: Query, type_id: str) -> Action | None:
    """本次出牌阶段附带指定池里该牌名的第一条（脚本的"按清单指定 [A, Bn]"）。"""
    for action in getattr(query, "designation_actions", ()):
        if (
            isinstance(action, UseCard)
            and action.card.type == type_id
            and action.designations
        ):
            return action
    return None


def _designation_for(
    query: Query, type_id: str, *, holder: int, designate: int
) -> Action | None:
    """本次出牌阶段附带指定池里**精确**的那一条 ``[A=holder, Bn=designate]``。"""
    for action in getattr(query, "designation_actions", ()):
        if (
            isinstance(action, UseCard)
            and action.card.type == type_id
            and action.targets == (holder,)
            and action.designations == (designate,)
        ):
            return action
    return None


def _use_equipment(query: Query, *, excluding_type: str | None = None) -> Action | None:
    """出牌阶段里第一张可用的装备牌（``excluding_type``：把某张牌名的装备留到最后装）。"""
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.slot is not None
            and action.card.type != excluding_type
        ):
            return action
    return None


def _use_one_of(query: Query, type_ids: tuple[str, ...]) -> Action | None:
    """出牌阶段里第一张牌名命中 ``type_ids`` 的可用牌。"""
    for action in query.legal_actions:
        if isinstance(action, UseCard) and action.card.type in type_ids:
            return action
    return None


def _discard_keeping(query: Query, keep: set[int]) -> Action:
    """弃牌阶段：挑一条**不弃 ``keep``** 的候选（保留借刀 / 转化源 /【杀】）。"""
    for action in query.legal_actions:
        if isinstance(action, DiscardCards) and not (
            keep & {card.card_id for card in action.cards}
        ):
            return action
    return query.legal_actions[0]


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


def engine_for(
    scenario: Scenario,
    *,
    seed: int,
    pack=None,
    rule_config: RuleConfig | None = None,
) -> Engine:
    """按场景声明开一局基准：**录制与回放共用**的同一条入口。

    M5 的 8 条基准从**构造局面**起跑（``Engine.from_state``，见 :mod:`replays.m5`）：
    手牌 / 装备 / 牌堆顶都写死，回放只凭「场景 slug + seed」就能重建同一份局面，故
    录像不依赖随机种子；其余基准仍是 ``Engine.new_game``（随机发牌 + 冻结磁带）。
    """
    if pack is None:
        pack = load_content_pack(deck_profile=scenario.deck_profile)
    if scenario.slug in m5.M5_SETUPS:
        return m5.build_engine(scenario.slug, seed=seed, pack=pack)
    if scenario.slug in m6.M6_SETUPS:
        return m6.build_engine(scenario.slug, seed=seed, pack=pack)
    return Engine.new_game(
        seed=seed,
        rule_config=rule_config or RuleConfig(player_count=scenario.player_count),
        pack=pack,
        heroes=scenario.heroes or (),
        hero_pool=scenario.hero_pool,
    )


def _new_scripted_engine(scenario: Scenario) -> Engine:
    """按场景声明开一局脚本基准（档位 / 人数 / 逐座位武将与真人局同一条入口）。"""
    return engine_for(scenario, seed=scenario.seed)


def _run_scripted(
    scenario: Scenario, slug: str, *, enter: bool | None = None
) -> tuple[Engine, _Recording]:
    """跑一局脚本驱动并返回引擎与录制状态。

    ``enter``（W4-5）：只有取消专项有两条路径——``True`` = 进预发动后取消，
    ``False`` = 从不进预发动；其余场景必须保持 ``None``。
    """
    engine = _new_scripted_engine(scenario)
    recording = _Recording()
    if scenario.driver == "m5":
        assert enter is None, "M5 构造基准没有取消 / 不发动两条路径"
        pack = load_content_pack(deck_profile=scenario.deck_profile)
        players = m5.build_players(
            slug,
            pack=pack,
            player_count=scenario.player_count,
            recording=recording,
        )
    elif scenario.driver == "m6":
        assert enter is None, "M6 构造基准没有取消 / 不发动两条路径"
        pack = load_content_pack(deck_profile=scenario.deck_profile)
        players = m6.build_players(
            slug,
            pack=pack,
            player_count=scenario.player_count,
            recording=recording,
        )
    elif slug == PREACTIVATION_CANCEL_SCENARIO:
        assert enter is not None, f"{slug}：调用方必须显式选取消 / 不发动路径"
        players: list = [
            _PreativationCancelPlayer(seat, slug, recording, engine, enter=enter)
            for seat in range(scenario.player_count)
        ]
    else:
        assert enter is None, "只有取消专项有两条路径"
        players = [
            ScenarioPlayer(seat, slug, recording, engine)
            for seat in range(scenario.player_count)
        ]
    if slug == "06-deck-reshuffle":
        players = [
            _ReshuffleWatcher(engine, recording, seat, player)
            for seat, player in enumerate(players)
        ]
    engine.run(players)
    return engine, recording


def preactivation_cancel_pair(
    slug: str = PREACTIVATION_CANCEL_SCENARIO,
) -> tuple[Engine, Engine]:
    """同一种子跑两遍：**取消路径** / **不发动路径**（W4-5 的专项基准）。

    两次只差接入层本地态（读清单 + 填复合动作 + 交给校验器），引擎看不到取消，故记录流
    必须逐字节相等。录制期由 :func:`_record_scripted` 判一次，门禁期由
    ``tests/test_replays.py`` 再判一次。
    """
    scenario = SCENARIOS[slug]
    assert scenario.driver == "scripted", f"{slug}：取消专项必须是脚本驱动的确定性基准"
    cancel, _ = _run_scripted(scenario, slug, enter=True)
    plain, _ = _run_scripted(scenario, slug, enter=False)
    return cancel, plain


def _record_scripted(slug: str, scenario: Scenario) -> Engine:
    """M1 路径：``ScenarioPlayer`` 迫使目标段发生。

    ``heroes``（票 17）：脚本基准也能指定逐座位武将——【雌雄双股剑】的"异性"判据读的是
    **武将数据派生的性别**，没有武将时性别恒 ``None``、这条链根本进不了候选。缺省
    ``None`` ⇒ 无武将模式，01–06 六条基准的录制一字未变。

    W4-5 的取消专项（44）在录制期**同时**跑不发动路径：两条记录流不一致就拒绝录制，
    免得"取消留下了副作用"被写进基准。
    """
    if slug == PREACTIVATION_CANCEL_SCENARIO:
        engine, recording = _run_scripted(scenario, slug, enter=True)
        plain, _ = _run_scripted(scenario, slug, enter=False)
        assert dump_journal(engine.records) == dump_journal(plain.records), (
            f"{slug}：取消与不发动两条路径的后续记录不一致——"
            "取消是本地态，不得留下任何记录 / 副作用"
        )
    else:
        engine, recording = _run_scripted(scenario, slug)
    assert recording.goal_done, f"{slug}：目标段没有命中（seed={scenario.seed}）"
    if scenario.driver in ("m5", "m6"):
        # 脚本宣布命中 ≠ 记录流里有那回事：构造基准**再独立复读一遍**事后判据。
        assert goal_for(slug)(engine), (
            f"{slug}：构造局面的计划走完了，但记录流没有命中 goals 里的判据"
        )
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
