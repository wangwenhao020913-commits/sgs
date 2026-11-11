# 12: 转化清单逐张置灰 + 渐营动态池

**What to build:** 转化清单里**每一张源牌都按合法性置灰**（不合法的直接灰、合法的亮），
选定源牌后**逐产物置灰**；界沮授【渐营】的目标池改为按当前答案**现算**（不再逐组合预生成行）。

**Blocked by:** 11（预览端点）
Status: open
Type: task
Touches: src/sgs/skills/heroes.py, src/sgs/skills/virtual.py, tools/sgs_web/projection.py, tools/sgs_web/static/preactivation.js, tools/sgs_web/static/card-face.js, tests/test_hero_transform.py, tests/test_hero_jie_yijiang.py, tests/test_web.py
Ruling: spec §2 Q15 / Q20（三级置灰）、Q16（转化牌与真牌同档）

## 根因（spec §1 #6-渐营）

1. **转化候选不进 `candidate_index`**：`transform_candidates` 被从 `legal_actions` 摘进 `transform_actions`
   （不进序列化，`engine/game.py:5579-5590`）⇒ 前端对转化牌的目标合法性**完全没有判据**，
   唯一通道是预发动清单。
2. **渐营第 3 步（选目标）的动态池**：`JIE_JIANYING_FLOW`（`skills/active.py:7309-7336`）的
   `PickSeats(lo=_lo, hi=_hi, pool=_pool)` 是**可调用档**，池由 `_jie_jianying_targets` / `_jie_jianying_target_pool`
   按前序答案（牌名）现算——**服务端同源是做到的**；断点在清单渲染按"源牌 × 牌名"逐行下发，
   组合数超过 32 就整步退化（`flow.py:1318/1506-1527/1792-1811`）。实测 160 包满血 **9 张自有牌**即触发。
3. **转化清单没有逐张合法性**：源牌步只给"能作为来源"的选项，不判"这个产物此刻能不能用"
   （次数用尽 / 没有合法目标 / 被禁用）。

## 要做什么

1. **源牌级置灰**（Q20）：该源牌的**所有产物都不可用**才灰。
   "可用"读 `check_usable` 的前三条（时机 / 禁用 / 次数，`effects/actions.py:465-500`）。
   计算点：**询问时算一次**（不用预览请求——源牌是全集的、与已选无关），规模 ≤ 20 源 × 6 产物 = 120 次纯谓词，< 1 ms。
2. **产物级置灰**：选定源牌后，逐产物判"此刻能不能用"（含**有合法目标**这一条）：
   空城诸葛亮 ⇒ 【杀】灰、【桃】满血灰、【酒】/【无中】亮。
   **注意**：产物级用的是"**能否使用**"，不是"能否选目标"——目标是下一步的事。
3. **目标级**：进目标步，由票 11 的预览回包决定能点谁（空城 ⇒ 无座位可点 ⇒ 进不了下一步）。
4. **渐营的动态池走预览**：`MANIFEST_POOL_LIMIT` 与逐组合行随票 11 一起退役；目标步的池由
   `_jie_jianying_target_pool` 现算（**同一个函数**，不许另写一份）。
5. **灰色也要能"看见"**：置灰的源牌仍显示（`disabled`），悬停给一句"此刻没有可用的产物"；
   置灰的产物同理。**不要**直接从清单里删掉（玩家会以为清单漏牌）。
6. **合法性判据只许有一份**：源牌级 / 产物级都调用引擎侧已有函数（`check_usable` / `transform_candidates`），
   前端不重算。

## 验收

- 新增（`tests/test_hero_transform.py`）：
  - `test_a_source_card_with_no_usable_product_is_disabled_in_the_manifest`
    （【杀】次数用尽且手里只有能做【杀】的牌 ⇒ 全灰）。
  - `test_a_product_without_legal_targets_is_disabled_but_the_source_card_stays_enabled`
    （空城诸葛亮：源牌亮、【杀】灰、【桃】/【酒】按自身状态判）。
  - `test_every_transform_source_is_checked_against_the_engine_predicate`
    （清单里的 disabled 集合与引擎 `transform_candidates` 的差集必须为空——**同源断言**）。
- 新增（`tests/test_hero_jie_yijiang.py`）：`test_jianying_target_step_has_a_live_pool_with_many_cards`
  （20 张自有牌的局面下，目标步仍有池、且池 = `_jie_jianying_target_pool` 的结果）。
- 新增（烟测）：`U-TF-01` 转化清单里灰项画出来但点不动；`U-TF-02` 渐营选完源牌 + 牌名后可点座位。

## 会红的既有断言

- `tests/test_hero_jie_yijiang.py:2290-2310`（只测 2 张牌，永远不命中退化）——补一条真命中。
- `tests/support/ui_smoke.mjs:4647-4690`（PZ10–12）——票 11 已改写，本票确认无残留。
- 金录像：`tests/replays/29-serpent-spear-virtual-slash.jsonl` / `42-hero-transform.jsonl` 若清单形状变化
  （`disabled` 字段），**记录流不变**（清单整体 `exclude=True`）⇒ 预计 0 条重录；跑 `check_goldens.sh` 确认。
