"""README 索引表生成器（票 32）：六列 slug / 中文场景名 / 类 / 档位 / 人数 / 验证的规则条目。

``tests/replays/README.md`` 的索引表由本模块生成，**别再手改那张表**：

- slug / 档位 / 人数 = ``scenarios.SCENARIOS``；
- 类 = ``distribution.SLUG_CLASSES``；
- 中文场景名与"验证的规则条目" = 本模块的 :data:`NAMES`（人工维护的单一真源）。

``tests/test_replays.py`` 断言 README 里的表与 :func:`table` 逐行相等（防手改后漂移）。

用法（在仓库根目录；``tests/`` 是导入根）::

    PYTHONPATH=tests .venv/bin/python -m replays.readme_index          # 打印索引表
    PYTHONPATH=tests .venv/bin/python -m replays.readme_index --write  # 写回 README.md
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from replays import distribution, scenarios

__all__ = [
    "HEADER",
    "NAMES",
    "README_PATH",
    "SEPARATOR",
    "main",
    "replace_table",
    "row",
    "table",
]

README_PATH = Path(__file__).resolve().parent / "README.md"

HEADER = "| slug | 中文场景名 | 类 | 档位 | 人数 | 验证的规则条目 |"
SEPARATOR = "|---|---|---|---|---|---|"

NAMES: dict[str, tuple[str, str]] = {
    "01-slash-dodge-cancel": ("【杀】→【闪】抵消", "使用与打出 / 无效·抵消·失效 / 使用全步骤（规则 4.3 / 17.6 / 17.10）；L4 六个时机 + L5 的 `RESOLVE_BEFORE_EFFECT` / `RESOLVE_END` / `RESOLVE_AFTER_END`（**不出现** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）；手牌 → 处理区 → 弃牌堆两段移动（`reason` 均为 `USE`）"),
    "02-slash-hit-damage": ("【杀】→不闪→伤害", "使用全步骤 / 数值四事件（17.10 / 4.13–4.15）；L5 六个（**含** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）→ 伤害帧 L7 五个 → 其中嵌套扣减体力帧 L8 三个；一条 `hp_change` delta"),
    "03-near-death-self-peach": ("濒死求桃：自救", "濒死（规则 4.17 / 4.7）；伤害 → 扣减体力 → `NEAR_DEATH_ENTER` / `AT` / `AFTER` 三时机；濒死者自己在 `NEAR_DEATH_AT` 出【桃】→ 回复帧 L10 两个"),
    "04-near-death-other-peach": ("濒死求桃：他人救", "同 `03`，但**当前回合角色**对濒死者用【桃】→ 后面的应答者不再被问；回复结算同样插在濒死之下"),
    "05-near-death-death": ("濒死无人救 → 死亡 → 胜负", "濒死（规则 4.17）与死亡 / 胜负判定时点（4.18 / 4.19）；`DEATH_*` 四段 → 逐帧 `discard` 五帧 → `game_end`（**最后一条**）"),
    "06-deck-reshuffle": ("牌堆耗尽 → 重洗", "牌堆耗尽（规则 10.4）：每次重洗只留**一条** `deck_shuffle` delta；**不出现** `card_move(DISCARD→DECK)`；初次洗牌仍是 `seq=1`、`frame_id=0`"),
    "07-identity-4p-rebel-win": ("4 人身份局 · 反贼胜", "身份配比 1-1-1-1；身份发放先于发牌；主公 +1 体力与先手；反贼胜的终局 `side=rebel`"),
    "08-identity-5p-lord-win": ("5 人身份局 · 主忠胜", "身份配比 1-1-2-1；主忠方胜的终局 `side=lord`、`winners` 含阵亡者"),
    "09-identity-6p-renegade-win": ("6 人身份局 · 内奸胜", "身份配比 1-1-3-1；主公死亡瞬间除主公外唯一存活者为内奸 ⇒ `side=renegade`"),
    "10-identity-7p-lord-win": ("7 人身份局 · 主忠胜", "身份配比 1-2-3-1；7 人座次与回合轮转"),
    "11-identity-8p-rebel-win": ("8 人身份局 · 反贼胜", "身份配比 1-2-4-1；8 人座次、逆时针轮转与终局"),
    "12-indulgence-skips-play": ("【乐不思蜀】命中", "延时锦囊使用（L4 / L5）→ 判定阶段 L14 三时机；非红桃 ⇒ 跳过出牌阶段"),
    "13-indulgence-heart-misses": ("【乐不思蜀】未命中", "同上，判定牌为红桃 ⇒ 正常进入出牌阶段"),
    "14-lightning-thunder-damage": ("【闪电】命中", "♠2~9 ⇒ 3 点**无来源**雷电伤害；`hp_change.amount = -3`"),
    "15-lightning-defers-next-seat": ("【闪电】顺延下家", "未命中 ⇒ `PROCESSING → JUDGE(下一合法目标)`，`owner=null`"),
    "16-nullify-cancels-a-trick": ("无懈抵消单体锦囊", "逐目标无懈窗口（`RESPOND/NULLIFY`）挂在该目标 `RESOLVE` 帧的 `生效前`；奇偶律"),
    "17-nullify-nested-two": ("无懈嵌套 2 层", "无懈也是锦囊：嵌套 `RESOLVE` 帧、后发生的事件先结算"),
    "18-nullify-nested-three": ("无懈嵌套 3 层终止", "更深嵌套的链在同一出栈序里自底向上传播"),
    "19-nullify-on-multi-target": ("多目标锦囊被逐目标无懈", "A 段外层 / C 段逐目标：无懈窗口按目标分槽"),
    "20-nullify-lightning-defers": ("被无懈的延时锦囊传下家", "闪电被无懈 ⇒ 收尾把实体牌传下家判定区（`card_scroll.txt:219`）"),
    "21-rebel-killed-reward": ("杀反贼摸 3", "死亡奖惩（规则 4.8）：身份检测 → 系统处理牌 → 执行奖惩；凶手摸 3 张"),
    "22-lord-kills-loyalist": ("主公杀忠臣罚弃", "主公杀死忠臣 ⇒ 主公弃置自己的手牌 / 装备区 / 判定区"),
    "23-two-deaths-revealed": ("两次阵亡与亮明", "`identity_reveal` 在 `DEATH_REVEAL_IDENTITY_BEFORE` 内部、时机技能之后、检测之前"),
    "24-multi-player-peach-window": ("多人求桃窗口", "应答者区间 = 当前回合角色起逆时针、终点 = 其上家；一个窗口多个应答者"),
    "25-rollback-same-start": ("新濒死回退 · 同一起点重开", "同一 `NEAR_DEATH` 帧内第二条 `NEAR_DEATH_AT`；第二窗起点 = 刚响应完的 B"),
    "26-rollback-start-advances": ("新濒死回退 · 起点前进", "新区间 = 旧区间的后缀：B 之前已问过的人不再问"),
    "27-slash-beyond-adjacent": ("距离 / 攻击范围可观测", "一次【杀】指到座位距离 ≥ 2 的目标（武器攻击范围）"),
    "28-equipment-replacement": ("装备替换", "新牌 `PUT` 入槽 + 旧牌 `LOSE` 入弃牌堆（一次移动同时失去新旧两张）"),
    "29-serpent-spear-virtual-slash": ("丈八蛇矛 · 转化【杀】", "两张手牌转化为虚拟【杀】，虚拟牌 `card_id < 0`、`virtual_sources` 非空"),
    "30-halberd-three-targets": ("方天画戟 · 多目标", "此【杀】是你最后的手牌时可多指定至多两个目标"),
    "31-play-card-response": ("打出链（南蛮 / 万箭 / 决斗）", "`PlayCard` 独立构造子：无目标、走 L6、**不占使用次数**"),
    "32-five-grains-selection": ("五谷丰登 · 选牌", "`SELECT_CARDS` 选择面；X = 目标数、剩余牌进弃牌堆"),
    "33-borrowed-sword-use-slash": ("借刀杀人 · 使用【杀】", "`RESPOND/BORROWED_SWORD`，候选是 `UseCard`（使用而非打出）"),
    "34-dismantle-or-snatch-equip-judge": ("拆顺取装备区 / 判定区", "`SELECT_CARDS` 选择面含装备区或判定区；判定区先入在前"),
    # ---- 8. 武将技能（票 14 §8） ----
    "35-hero-damage-points": ("【遗计】逐点伤害", "B 族按点触发：一次多点伤害里同一时机被派发多次、各自问一次「是否发动」；`DAMAGE_AFTER_TAKE` 的两个时机实例"),
    "36-hero-rejudge-cycle": ("【鬼才】改判循环", "I 族改判窗口（`WindowCause.REJUDGE`）：判定牌生效前打出代替；`JUDGE_CARD_BEFORE_EFFECT` 可循环重开"),
    "37-hero-lord-request": ("【护驾】/【激将】势力求助", "F 族主公技：`WindowCause.LORD_REQUEST` 的势力作用域求助窗口；同势力角色打出【闪】/【杀】"),
    "38-hero-lost-cards": ("【连营】+【枭姬】含顶替", "D 族 `MOVE_AFTER` 内联时机：失去最后的手牌 / 失去装备区里的一张牌（含同槽顶替的旧装备那条 `LOSE`）"),
    "39-hero-target-pool": ("【空城】+【谦逊】目标池", "C 族目标池真排除：【谦逊】无条件、【空城】手牌为零时，受限牌的目标候选里没有受保护座位"),
    "40-hero-wushuang": ("【无双】两张【闪】", "E 族响应窗口张数：一次【杀】的生效前连开两个 `DODGE` 窗口，两张【闪】才抵消"),
    "41-hero-guanxing": ("【观星】排列", "G 族授予可见性与排列：`ARRANGE` 询问 + `ArrangeCards`（只记面内位置，无带牌面的 delta）"),
    "42-hero-transform": ("转化族（武圣 / 龙胆 / 国色 / 急救）", "H 族转化牌泛化：`UseCard` / `PlayCard` 的 `virtual_sources` 非空；来源实体牌经使用链搬进处理区再进弃牌堆"),
}
"""slug → ``(中文场景名, 验证的规则条目)``：索引表里两列人工维护的**单一真源**。"""


def row(slug: str) -> str:
    """一条基准的六列表格行。"""
    scenario = scenarios.SCENARIOS[slug]
    name, detail = NAMES[slug]
    classes = "、".join(distribution.SLUG_CLASSES[slug])
    return (
        f"| `{slug}` | {name} | {classes} | `{scenario.deck_profile}` | "
        f"{scenario.player_count} | {detail} |"
    )


def table() -> str:
    """完整索引表（表头 + 分隔行 + 每条基准一行），以换行结尾。"""
    lines = [HEADER, SEPARATOR, *(row(slug) for slug in scenarios.SCENARIOS)]
    return "\n".join(lines) + "\n"


def replace_table(text: str) -> str:
    """把 ``text`` 里的索引表整段替换成 :func:`table`（纯函数；表头定位、行边界收尾）。

    只动表头到表格末行这一段，前后文原样保留；输入以换行结尾时输出也以换行结尾。
    """
    lines = text.splitlines()
    start = lines.index(HEADER)
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    replaced = [*lines[:start], *table().splitlines(), *lines[end:]]
    return "\n".join(replaced) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="把索引表写回 README.md")
    args = parser.parse_args(argv)

    if not args.write:
        print(table(), end="")
        return 0

    README_PATH.write_text(replace_table(README_PATH.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"已重写 {README_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 开发期入口
    raise SystemExit(main())
