"""基准的**分布判据**（票 17 §1 / 票 32 §1）：类 → 最少条数、slug → 类。

``≥30`` 条只是门槛，不是目标。这份表把"哪些整局路径必须各有一条"落成**单一真源**，
由 ``tests/test_replays.py`` 逐类判决：**每类达标、每个 slug 恰属至少一类、无未分类 slug**，
且 README 索引 == 文件集合 == 本表。

- M1 的六条基准标 ``m1-regression``（回归网，不计入八类必需表）；
- M2 的 28 条按七类必需表各就各位（一条 slug 可同属多类，本表里没有重叠）；
- M3 的 8 条武将技能专项基准标 ``武将技能``（票 14 §8）。
"""

from __future__ import annotations

__all__ = ["CATEGORY_MINIMUMS", "M1_CLASS", "SLUG_CLASSES"]

M1_CLASS = "m1-regression"
"""M1 六条回归基线的类标签：它们是回归网，不是身份局路径。"""


CATEGORY_MINIMUMS: dict[str, int] = {
    "人数骨架": 5,
    "判定与延时锦囊": 4,
    "无懈可击与响应链": 5,
    "死亡奖惩": 3,
    "多人濒死": 3,
    "装备/距离/转化": 4,
    "普通锦囊与打出": 4,
    "武将技能": 8,
}
"""八类必需表：类 → 最少条数；合计 = 28 条 M2 新增 + 8 条 M3 专项 + M1 六条 = 42 条。"""


SLUG_CLASSES: dict[str, tuple[str, ...]] = {
    # ---- M1 回归网（票 12 的六条；票 23 追加 06） ----
    "01-slash-dodge-cancel": (M1_CLASS,),
    "02-slash-hit-damage": (M1_CLASS,),
    "03-near-death-self-peach": (M1_CLASS,),
    "04-near-death-other-peach": (M1_CLASS,),
    "05-near-death-death": (M1_CLASS,),
    "06-deck-reshuffle": (M1_CLASS,),
    # ---- 1. 人数骨架（4/5/6/7/8 各 ≥1；三种结局各 ≥1） ----
    "07-identity-4p-rebel-win": ("人数骨架",),
    "08-identity-5p-lord-win": ("人数骨架",),
    "09-identity-6p-renegade-win": ("人数骨架",),
    "10-identity-7p-lord-win": ("人数骨架",),
    "11-identity-8p-rebel-win": ("人数骨架",),
    # ---- 2. 判定与延时锦囊 ----
    "12-indulgence-skips-play": ("判定与延时锦囊",),
    "13-indulgence-heart-misses": ("判定与延时锦囊",),
    "14-lightning-thunder-damage": ("判定与延时锦囊",),
    "15-lightning-defers-next-seat": ("判定与延时锦囊",),
    # ---- 3. 无懈可击与响应链 ----
    "16-nullify-cancels-a-trick": ("无懈可击与响应链",),
    "17-nullify-nested-two": ("无懈可击与响应链",),
    "18-nullify-nested-three": ("无懈可击与响应链",),
    "19-nullify-on-multi-target": ("无懈可击与响应链",),
    "20-nullify-lightning-defers": ("无懈可击与响应链",),
    # ---- 4. 死亡奖惩 ----
    "21-rebel-killed-reward": ("死亡奖惩",),
    "22-lord-kills-loyalist": ("死亡奖惩",),
    "23-two-deaths-revealed": ("死亡奖惩",),
    # ---- 5. 多人濒死 ----
    "24-multi-player-peach-window": ("多人濒死",),
    "25-rollback-same-start": ("多人濒死",),
    "26-rollback-start-advances": ("多人濒死",),
    # ---- 6. 装备 / 距离 / 转化 ----
    "27-slash-beyond-adjacent": ("装备/距离/转化",),
    "28-equipment-replacement": ("装备/距离/转化",),
    "29-serpent-spear-virtual-slash": ("装备/距离/转化",),
    "30-halberd-three-targets": ("装备/距离/转化",),
    # ---- 7. 普通锦囊与打出 ----
    "31-play-card-response": ("普通锦囊与打出",),
    "32-five-grains-selection": ("普通锦囊与打出",),
    "33-borrowed-sword-use-slash": ("普通锦囊与打出",),
    "34-dismantle-or-snatch-equip-judge": ("普通锦囊与打出",),
    # ---- 8. 武将技能（票 14 §8：逐族一条专项基准） ----
    "35-hero-damage-points": ("武将技能",),
    "36-hero-rejudge-cycle": ("武将技能",),
    "37-hero-lord-request": ("武将技能",),
    "38-hero-lost-cards": ("武将技能",),
    "39-hero-target-pool": ("武将技能",),
    "40-hero-wushuang": ("武将技能",),
    "41-hero-guanxing": ("武将技能",),
    "42-hero-transform": ("武将技能",),
}
"""slug → 类（**每个 slug 至少一类**；新增基准必须在这里登记）。"""
