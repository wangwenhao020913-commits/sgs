# 13: 座位多选收口——连营 / 大雾 / 突袭 / 奋威 / 除疠

**What to build:** 把"一次选多名角色"统一到座位多选模板（`SelectOptions` + `option_seats`，牌桌多点、一次提交），
删掉手写的逐座循环；【除疠】的"势力互异"走票 11 的预览现算。

**Blocked by:** 11（预览端点；除疠需要同步内动态置灰）
Status: open
Type: task
Touches: src/sgs/engine/game.py, src/sgs/skills/flow.py, src/sgs/skills/hero_zone.py, src/sgs/skills/shen_zhugeliang.py, src/sgs/skills/phase_body.py, src/sgs/skills/negation.py, src/sgs/skills/active.py, tests/test_hero_card_zone.py, tests/test_hero_jie_qun.py
Ruling: spec §2 Q6 / Q10；`.scratch/chenqun-guyong/issues/05-seat-multi-select.md`

## 根因（spec §1 #13）

- 座位多选模板已经存在（`SelectOptions` + `option_seats`，`min_selected`/`max_selected`，
  `enumerate_candidates=False` 走结构面；前端 `modal.js:206 isSeatMultiStep` → `pending.js:386 seat-multi` →
  `table.js:98 seatMultiPickable` → `interaction.js:91 toggleSeatMultiPick` → `answer.js:143 seatMultiAction`），
  首个载体 = 界顾雍【秉壹】（`phase_body.py:2046`）。
- 但多条技能仍是**手写逐座循环**（写于 2026-11-09 座位多选面之前）：

| 技能 | 位置 | 现状 | 换法 |
|---|---|---|---|
| 界陆逊【连营】 | `hero_zone.py:203-213` | `while len(chosen) < x` + `ask_option(skill_id=f"lianying:seat:{n}")` | 一次 `min=max=x`；**保留** `lianying_drawn` 摸牌游标 |
| 神诸葛亮【大雾】 | `shen_zhugeliang.py:405-424` | 同上（k = 弃星数） | 一次 `min=max=k` |
| 张辽 / 界【突袭】 | `phase_body.py:667-674 / 749-756` | `PickSeats(lo=1, hi=2)` 由 `_flow_step_seats` 逐座问 | `flow.py:1084 _flow_step_seats` 的 `ordered=False ∧ hi>1` 改走 `ask_seats` |
| 界甘宁【奋威】 | `negation.py:120-140` | `while True` + `fenwei:<n>` | 一次 `min=1, max=len(targets)` |
| 界华佗【除疠】 | `active.py:1793-1820` | `PickSeats(ordered=True)` ⇒ 清单不下发，执行器逐座问 | 一次 `min=1, max=len(Kingdom)`，势力互异由预览现算 |

- **不能换的**（结构不同，别硬套）：界郭嘉【遗计】（牌↔人配对）、鲁肃【缔盟】（两异质槽位）、
  貂蝉/界【离间】（使用者/受害者两槽）、界夏侯渊【神速】（3 项**选项**复选，不是座位）、
  神周瑜【业炎】（计数式分配 `DISTRIBUTE`）。

## 要做什么

1. **统一收口**：在 `Engine.ask_options`（`game.py:3068`）旁加 `Engine.ask_seats(frame, actor, timing, *, skill_id, pool, lo, hi, optional)`
   ——内部固定 `options = seat_choice(...)`、`min_selected=lo`、`max_selected=hi`、`enumerate_candidates=False`，
   返回座位元组。"至多 N 名"从此只写一次。
2. **步骤语言侧**：`flow.py:1107-1136` 的 `while len(chosen) < hi` 改为
   "`ordered == False` 且 `hi > 1` ⇒ 一次 `host.ask_seats(...)`"；`ordered=True`（除疠之外的消费者）保留循环。
   注意：`ordered=True` 在【除疠】上的原意是"池依赖已选"⇒ 改走预览后它也可以 `ordered=False`。
3. **三个手写 while 归位**（连营 / 大雾 / 奋威），保留它们各自的摸牌 / 标记**后置段**与幂等游标
   （`lianying_drawn` / `dawu_targets` / `FENWEI_DONE_KEY`）——**先选齐人、再逐座结算**，与除疠同姿态。
4. **除疠**：`PickSeats(ordered=False, hi=len(Kingdom), pool=_chuli_targets)`；"势力互异"的即时置灰由票 11 预览给
   （`_chuli_targets(state, frame, owner, chosen)` 已经是"传入已选"的形状，直接复用）。
   提交点里的"逐名问弃哪张"**保留**（`_chuli_effect`，自己那一步 `reveal_hand=True` ⇒ 牌桌直选，靠票 09 的判据）。
5. **AI 适配**：`agents/heuristic.py` 现在按"逐座一次"填；改成一次填子集（`_pick_options` 已有子集路径）。
6. **`## Answer` 里附完整普查表**（本票是"复选座位"的收口票，表就是它的验收文档）。

## 验收

- 新增：`test_ask_seats_is_the_only_multi_seat_entry`（grep：三处手写 `while` 消失）。
- 新增（`tests/test_hero_card_zone.py`）：`test_jie_lianying_picks_all_targets_in_one_ask`
  （断言 `query.kind == "SELECT_OPTIONS"`、`option_seats` 非空、一次提交后逐座摸 1 张）。
- 新增（`tests/test_hero_jie_qun.py`）：`test_chuli_grays_out_same_kingdom_seats_after_the_first_pick`
  （预览回包的 `options` 收窄）、`test_chuli_still_asks_one_discard_per_target_after_the_seat_step`。
- 新增：`test_dawu_and_fenwei_pick_their_targets_in_one_ask`。
- 金录像：`tests/replays/58-jie-luxun-hero-zone.jsonl` 写死 `lianying:seat:0/1` ⇒ **本票会改记录流**
  ⇒ 重录清单交票 06（票 06 可能已经收了；若已收，本票自己补一次并在 Answer 里说明）。

## 会红的既有断言

- `tests/replays/58-jie-luxun-hero-zone.jsonl`（`lianying:seat:*` 询问名消失）。
- `tests/test_hero_jie_yijiang.py` 等对 `PickSeats` 逐座询问的断言（`flow.py:1107` 改动会波及所有 `PickSeats` 消费者
  ⇒ **必须跑全量**）。
- `tests/test_ai_cases.py` 里逐座填表的用例。
