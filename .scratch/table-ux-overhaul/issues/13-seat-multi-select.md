# 13: 座位多选收口——连营 / 大雾 / 突袭 / 奋威 / 除疠

**What to build:** 把"一次选多名角色"统一到座位多选模板（`SelectOptions` + `option_seats`，牌桌多点、一次提交），
删掉手写的逐座循环；【除疠】的"势力互异"走票 11 的预览现算。

**Blocked by:** 11（预览端点；除疠需要同步内动态置灰）
Status: resolved
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

## Answer

**已实现（2026-11-11）** —— 票面六条"要做什么"全部落地；`## 验收` 的五条新断言全绿。
金录像按 `spec §3 注①` **在本提交里重录一条**（`58-jie-luxun-hero-zone`，见下「重录清单」），
`tests/replays/goals.py` **一字未改、一条未放宽**。全部改动在引擎 / 技能声明 / AI / 测试 / 文档；
**前端零改动**（票 11/12 的座位多选面直接接住）。

### 1 · 改动摘要（逐条对应票面）

1. **统一收口**：`Engine.ask_seats(frame, actor, timing, *, skill_id, pool, lo, hi, optional)`
   （`src/sgs/engine/game.py:3351`，紧跟 `ask_options`）——内部固定 `options = seat:N`、
   `min_selected=lo`、`max_selected=hi`、`enumerate_candidates=False`，返回**座位元组**
   （`Pass` ⇒ 空元组，挂起 ⇒ `SUSPEND`），答案按 `(skill_id, actor, 派发范围)` 帧内缓存。
   "至多 N 名"这句话从此只写一次。`seat_choice` / `option_seat` / `SEAT_PREFIX` 由
   `sgs.skills` 导出（与 `SEAT_PREFIX` 同一处真源，不在引擎里再抄字面量）。
2. **步骤语言侧**：`flow.py:_flow_step_seats` 拆成两档——`hi > 1` 且 `not ordered` ⇒
   `_flow_step_seats_once`（一次 `host.ask_seats`，**:1164**）；其余（`hi == 1` 的定值步、
   或 `ordered=True` 的有序多选）保留逐座 `ask_option` 循环（`:1120` 的 `while` 还在，目前
   没有消费者落在 `ordered=True` 上）。
3. **三个手写 while 归位**（连营 / 大雾 / 奋威）：全部改成一次 `ask_seats`，
   各自的**后置段与幂等游标原样保留**（`lianying_drawn` / `dawu_targets` + `dawu_marked` /
   `FENWEI_DONE_KEY`）——**先选齐人、再逐座结算**，与除疠同姿态；询问名去掉轮号
   （`lianying:seat` / `dawu:seat` / `fenwei:seat`），投影的 `_step_requirement` 因此还能命中
   `prompts.json` 的 `seat` 步文案。
4. **除疠**：`CHULI_FLOW` 的座位步改成 `PickSeats(ordered=False, hi=len(Kingdom), pool=_chuli_targets)`
   （`active.py:1804`）。"势力互异"的即时置灰由**票 11 的预览**按同一份
   `_chuli_targets(state, frame, owner, chosen)` 现算。**服务端仍自己裁决**：执行器在答案落地后的
   第一次重入按 `step.pool` 的**前缀**复核（`flow.py:_seat_set_is_buildable:1227`，与
   `_assign_activation` 的复合动作校验 / 预览是同一份回调），越池集合整步取消（零副作用）；
   复核结果记在帧私有 `_SEAT_SET_CHECKED` 上，故 effect 改过状态后的重入不会误判 `cancel`。
   提交点里"逐名问弃哪张"**原样保留**（`_chuli_effect`，`reveal_hand=True` ⇒ 票 09 的牌桌直选）。
5. **AI 适配**：执行期一次问齐 ⇒ AI 走既有的**不枚举档组装路径**
   （`heuristic._assembled_option_set`，已按 `is_legal_option_set` 拼集合）；预发动清单里
   `_pick_options` 的**子集路径**照旧填满多选目标步。新增一张表
   `_DYNAMIC_POOL_SEAT_STEPS = {"chuli:seat"}` + `_distinct_kingdom_options`：清单 / 不枚举面
   只带**首问快照**，而除疠的池按已选收缩 ⇒ AI 只能按**公开事实**（势力，`PlayerView.players
   [i].kingdom`）自己收窄，否则它会把服务端不会铺的集合交上去（引擎拒 / 整步取消）。与
   `_HAND_REQUIRED_SEAT_STEPS` 同一姿态（判据只读公开面）。
6. **普查表**见 §2。

`_bingyi_seats`（界顾雍【秉壹】，本模板的首个载体）也改走 `ask_seats`（行为逐字不变，
`phase_body.py:2037`）：座位多选的载荷从此只有一处。

### 2 · 普查表：全仓"一次/多次选角色"的落点（本票的验收文档）

**换成座位多选（一次问齐）**

| 技能 | 位置（换后） | 换前 | 换后 |
|---|---|---|---|
| 界陆逊【连营】 | `hero_zone.py:_lianying:207` | `while len(chosen) < X` + `ask_option(skill_id=f"lianying:seat:{n}")`，逐座单选 | 一次 `ask_seats("lianying:seat", pool=存活, lo=1, hi=X, optional=True)`；`lianying_drawn` 摸牌游标保留 |
| 神诸葛亮【大雾】 | `shen_zhugeliang.py:_dawu_apply:415` | `while len(chosen) < len(stars)` + `dawu:seat:{n}`，`optional=False` | 一次 `ask_seats("dawu:seat", lo=min(k, 池), hi=k, optional=False)`；`dawu_targets` / `dawu_marked` 保留 |
| 张辽【突袭】 | `phase_body.py:672` 的 `PickSeats(lo=1, hi=2)` | 由 `_flow_step_seats` 逐座问两次 `SELECT_OPTION` | 步骤语言一次 `ask_seats`（技能声明一行不改） |
| 界张辽【突袭】 | `phase_body.py:752` 同上 | 同上 | 同上 |
| 界甘宁【奋威】 | `negation.py:_fenwei_handler:119` | `while True` + `fenwei:{n}`（第一问不可 Pass、其后可） | 一次 `ask_seats("fenwei:seat", pool=目标, lo=1, hi=len(目标), optional=False)`；`FENWEI_DONE_KEY` 保留 |
| 界华佗【除疠】 | `active.py:1794` 的 `PickSeats` | `ordered=True`（清单不下发候选索引）⇒ 执行期逐座问 | `ordered=False` ⇒ 一次 `ask_seats`；势力互异的置灰走票 11 预览 + 执行器前缀复核 |
| 界顾雍【秉壹】 | `phase_body.py:_bingyi_seats:2037` | 手写 `ask_options(min_selected/max_selected/enumerate_candidates=False)` | `ask_seats`（行为逐字不变；首个载体归位） |

**不动（结构不同，票面点名）**

| 技能 | 为什么不动 |
|---|---|
| 界郭嘉【遗计】 | 牌 ↔ 人**配对**（先看牌再选人），不是一个座位集合 |
| 鲁肃【缔盟】 | 两个**异质槽位**（A 的手牌 ↔ B 的手牌），不是"同一池里选多名" |
| 貂蝉 / 界【离间】 | 使用者 / 受害者**两个不同池**的两步（第二步池依赖第一步，走 `ordered` 或两条步骤，不是同一池多选） |
| 界夏侯渊【神速】 | 3 项**选项**复选（`SELECT_OPTIONS` 的非座位形态，`jie_shensu:items`），不是座位 |
| 神周瑜【业炎】 | **计数式分配**（`DISTRIBUTE`，每座位一个点数），不是集合 |

**其余 `PickSeats` 消费者（30 处 `hi == 1`，含界【渐营】③ 的**可调用** `hi ∈ {0,1}`）**：
单座步，逐座单选形状与记录流**逐字节不变**，本票不动。

### 3 · 验收 → 断言（全部存在且绿）

| 验收条目 | 断言 |
|---|---|
| `test_ask_seats_is_the_only_multi_seat_entry`（grep：三处手写 while 消失） | `tests/test_seat_multi_select.py::test_ask_seats_is_the_only_multi_seat_entry`（`ask_seats` 定义恰一处、调用者恰五个文件各一次；`f"lianying:seat:` / `f"dawu:seat:` / `f"fenwei:` 全树消失；`while len(chosen)` 只剩 `flow.py` 的 ordered 档） |
| `test_jie_lianying_picks_all_targets_in_one_ask` | `tests/test_hero_card_zone.py::test_jie_lianying_picks_all_targets_in_one_ask`（`SELECT_OPTIONS` + `option_seats` 非空 + 区间 `[1, 2]` + 一次提交后逐座各摸 1 张 + 之后没有任何 `SELECT_OPTION`） |
| `test_chuli_grays_out_same_kingdom_seats_after_the_first_pick` | `tests/test_hero_jie_qun.py::test_chuli_grays_out_same_kingdom_seats_after_the_first_pick`（清单 → `preview_step` → `validate_activation` 三条真产出路径：选了魏之后 2 号位从 `options` 消失、3 号位还在、预览说不能点的校验器就拒） |
| `test_chuli_still_asks_one_discard_per_target_after_the_seat_step` | `tests/test_hero_jie_qun.py::test_chuli_still_asks_one_discard_per_target_after_the_seat_step`（座位只问一次 `SELECT_OPTIONS`，`SELECT_CARDS` 恰 3 次 = 自己 + 两名目标；三张牌的去向逐条核） |
| `test_dawu_and_fenwei_pick_their_targets_in_one_ask` | `tests/test_seat_multi_select.py::test_dawu_and_fenwei_pick_their_targets_in_one_ask`（两个真引擎：大雾 `dawu:` 一问、`[1,1]`；奋威 `fenwei:` 一问、`[1,3]`、池 = 全部目标；被无效的目标不掉血） |
| AI 适配（票面第 5 条） | `tests/test_seat_multi_select.py::test_the_ai_fills_the_whole_seat_set_in_one_answer`（真询问 → `HeuristicPlayer` 交出一个集合、长度在区间内、引擎接受） |
| 步骤语言的一次问齐档 | `tests/test_hero_active.py::test_a_multi_seat_step_is_asked_once_in_a_single_select_options`（注入一条 `PickSeats(lo=1, hi=2)`：一问 `SELECT_OPTIONS`；`effect` 读到的仍是座位元组） |
| 服务端不采信越池集合 | `tests/test_hero_jie_qun.py::test_chuli_refuses_a_same_kingdom_set_the_pool_never_offered`（两份魏 ⇒ 整步取消、零副作用、次数不消耗） |

### 4 · 会红的既有断言（都在本提交里改掉，逐条说明"为什么它必然红"）

1. `tests/test_hero_card_zone.py::test_lianying_counts_the_hand_cards_lost_this_time_and_draws_for_each_chosen_seat`（**本分支已不存在**：改名 `::test_jie_lianying_picks_all_targets_in_one_ask` 并重写）——旧名断的正是"两次逐座询问"：`len(asked) == 2` + `asked[1] == ("seat:0",)`，本票要消灭的就是这两条。
2. `::test_lianying_triggers_when_a_trick_takes_the_last_hand_card` /
   `::test_lianying_triggers_when_the_last_hand_card_goes_into_another_hand`：
   应答口从 `SELECT_OPTION` 改成 `SELECT_OPTIONS`（X = 1 仍只问一次，断言的**机制**未变）。
3. `::test_lianying_keeps_drawing_after_a_qingjian_ask_suspends_mid_loop`：
   两问合一（`len(asked) == 1`），挂起深度的判据 `depths == [2]` 与"两座各摸一张"原样保留。
4. `::test_a_second_lianying_trigger_in_the_same_frame_asks_a_fresh_question`：
   询问种类换成 `SELECT_OPTIONS`（缓存键仍按派发范围分桶，第二次仍必须重新发问）。
5. `tests/test_hero_move_after.py::test_lianying_fires_when_the_lord_kills_a_loyalist_and_loses_the_whole_hand`：
   `_pick_our_own_seat` 改成一次提交 `SelectOptions`；`SELECT_OPTION` 计数改成 `SELECT_OPTIONS`。
6. `tests/test_hero_jie_qun.py::test_chuli_second_target_must_have_a_different_kingdom`（**本分支已不存在**：改名 `::test_chuli_grays_out_same_kingdom_seats_after_the_first_pick` 并重写）——旧断言
   "第二次询问的池不含同势力"在新形状里不存在；机制改由**预览**证明，并加了"预览说不能点的
   校验器就拒"的同源断言）。另三条除疠用例经同一个脚本 helper 适配（座位一次交齐）。
7. `tests/test_hero_jie_negation.py` 的【奋威】四条：`_fenwei_script` 改成一次交集合；
   `test_fenwei_may_invalidate_the_user_when_the_card_targets_him` 的询问种类读数跟着改。
8. `tests/test_hero_catalog.py` 的【突袭】四条 / `tests/test_hero_jie_wei.py` 的【突袭】族：
   脚本玩家改一次交集合；`len(player.options)` 的"两问"断言改成"一问 + `max_selected == 2`"。
9. `tests/test_hero_active.py::test_the_flow_shape_can_express_a_dependent_two_seat_pick`：
   注入的步骤正是 `PickSeats(lo=2, hi=2, ordered=False)` —— 本票把它改成一次问齐，
   故这条**必须**改写：依赖池的那一档现在归 `ordered=True`（同名重写，机制不变）。
10. `tests/test_negation_sites.py` 的 `SITES` 登记项：
    `mark_targets_ineffective(frame, tuple(chosen))` → `mark_targets_ineffective(frame, chosen)`
    （同一站点，`chosen` 已是元组）。
11. `tests/support/web_client.py::_hero_zone_handlers`（M6 的 `hero_zone` tape 驱动）：
    【连营】的询问改成 `SELECT_OPTIONS`（取骨架候选 = 池里第一个座位 = "令自己摸"，与前 tape 同义）。
12. `tests/replays/m6.py` 的计划 58：两步 `SELECT_OPTION` 合成一步（新增 `Step.build`：
    不枚举档的答案由计划**组装**、引擎按同一份形状谓词校验），第二次【连营】的
    "重新发问"证据换成一次「放弃」。
13. `开发文档/06-里程碑与风险.md` §5 的两条账本行（【连营】X 口径 / 【除疠】目标集合）：
    引用的测试名与"逐次选人"的措辞随之更新（否则 `test_writeback_landing.py` 判红）。
14. `docs/agents/skill-authoring/shapes.md` 的 `SELECT_OPTIONS` 行：补 `Engine.ask_seats`。

### 5 · 重录清单（哪条基准 / 变了什么 / 为什么）

**行为有意变更**（`spec §3 注①` / `§4`：本 effort 只有票 04 与票 13 动基准字节）：

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `58-jie-luxun-hero-zone` | 同种子重录：56,187 → **54,999** 字节（153 → **151** 条记录） | 【连营】的"令至多 X 名各摸一张"从**逐座两问** `SELECT_OPTION`（`lianying:seat:0/1`）变成**一问** `SELECT_OPTIONS`（`lianying:seat`，不枚举档）：两条询问记录合成一条、两条 `SelectOption` 应答合成一条 `SelectOptions(choices=["seat:0","seat:1"])` |

- **面**：重录面 = `tests/replays/*.jsonl` 共 60 条；**只变这一条**，其余 59 条逐字节不变
  （`git status --porcelain tests/replays/` 只报它）。种子**未重选**（对局长度不变：
  153 → 151 条记录，`max_turns` / 人数 / 判据都不动）。
- **机制断言**：`goals.py` **一字未改、一条未放宽** —— `_m6_hero_zone` 仍断言 HERO 区域的
  放置 / 整批归还 + 【谦逊】的发动；重录后仍命中。
- **冻结表**：`58` 属于 **M6 构造基准**，不在 `STANDARD_108_REPLAY_BYTES` 的 47 条里 ⇒
  **那张表无需改动**（本票没有一条标准 108 基准变动）。`tests/replays/README.md` 的
  「重录登记」新增一节、索引里 58 的场景说明补上"一次座位多选"；`tests/test_replays.py`
  的模块 docstring 记下本轮的体积实测（60 条合计 **50,609,283 字节 ≈ 48.26 MiB**、
  最大单文件 **3,334,043 字节 ≈ 3.18 MiB** ⇒ **不动预算**）。
- **`check_goldens.sh`**：重录**前** PASS（文件仍是旧字节）→ `.venv/bin/pytest tests/test_replays.py
  --update-golden` → 重录**后** PASS：47 条标准 108 金录像逐字节不变。

### 6 · AI 适配（`docs/agents/ai-adaptation.md` §1 六条）

1. **询问面**：**复用** `SELECT_OPTIONS`（没有新 `QueryKind`）⇒ `_SCORERS` 不变。
2. **动作面**：不枚举档的组装路径（`_assembled_option_set`）本来就在；本票新增的
   **读点**只有一处：动态池座位步的公开事实收窄（`_DYNAMIC_POOL_SEAT_STEPS`）。不新增
   `query` 字段 ⇒ 三张登记表不动。
3. **语义条目**：`SKILL_VALUES` 不动（没有新技能 id）。
4. **覆盖场景**：`_COVERAGE_CASES` 不变（五个技能都已登记）。
5. **信息通道**：无新增可见区；AI 读的是既有的公开 `kingdom`（`exclude=True`，不进记录流，
   也不改记牌守恒量）。
6. **回归**：`tests/test_all_ai_games.py`（含界·群五人局的那条，会走到【除疠】的复合填表）、
   `tests/test_heuristic_ai.py`、`tests/test_ai_cases.py`、`tests/test_ai_adaptation.py` 全绿。

### 7 · 门禁结果（本机实测）

- `.venv/bin/pytest -q tests/test_hero_card_zone.py tests/test_hero_jie_qun.py tests/test_hero_jie_yijiang.py tests/test_ai_cases.py tests/test_seat_multi_select.py` → **213 passed**
- `.venv/bin/pytest -q tests/test_replays.py` → **96 passed**（`--update-golden` 后重跑仍绿）
- `bash tools/check_goldens.sh` → **PASS：47 条标准 108 金录像逐字节不变**（重录前后各一次）
- `bash tests/smoke-web.sh` → **PASS: 前端渲染冒烟**（"全部通过（驱动 8 步对局，覆盖 5 类询问）"）
- `.venv/bin/pytest -q tests/`（全量）→ **3915 passed / 0 failed**（438.75 s）
- 全量门禁 = 本提交的 pre-commit hook（`tests/run_all.sh --quiet`）绿。

### 8 · 实机 / 冒烟与手动复现配方

前端**零改动**：座位多选面早就由票 11/12 的形状判据接住（`modal.js:isSeatMultiStep` ⇒
`pending.js` 的 `seat-multi` ⇒ `table.js:seatMultiPickable` ⇒ `interaction.js:toggleSeatMultiPick`
⇒ `answer.js:seatMultiAction`），本票只是让五个技能真的**产出**这个形状。
`bash tests/smoke-web.sh` 是功能门；手工复现（`bash tools/serve-dev.sh start`，种子 12）：

1. **【连营】**：界陆逊被人用【过河拆桥】拆掉最后一张手牌 ⇒ 发动【连营】⇒ 牌桌上**一次**
   点亮多名角色的武将牌、底部出现"已选 k/已到上限"、点「提交」后逐座摸一张
   （旧行为：选一人提交一次、再问下一人）。
2. **【突袭】**：张辽 / 界张辽摸牌阶段发动【突袭】⇒ **一次**点 1–2 名其他角色 ⇒ 提交后
   各取一张手牌。
3. **【奋威】**：界甘宁对多目标锦囊发动【奋威】⇒ **一次**点若干目标 ⇒ 提交后这些目标对本牌无效。
4. **【大雾】**：神诸葛亮结束阶段发动【大雾】、选 k 枚星 ⇒ **一次**点 k 名角色 ⇒ 每人一枚「大雾」。
5. **【除疠】**：界华佗出牌阶段发动【除疠】⇒ 点第一名目标后，**同势力的其他座立刻变灰**
   （票 11 预览按每次选择现算）、异势力仍可点；提交后逐名问"弃哪张"。

### 9 · 刻意选择的读法 / 票 14 必读

1. **"至多 N 名"的写法**：`lo` / `hi` 由**调用方**给，helper 只固定"一次问齐 + 不枚举档"。
   连营 = `[1, X]`（「至多」+ 可放弃），大雾 = `[min(k, 池), k]`（旧逐座档没有 `Pass` ⇒
   必然选满，池不够时选满池；下限因此跟着池走），奋威 = `[1, len(目标)]`（至少一名，
   不发 `Pass`），突袭 = `[1, 2]` + `optional`，除疠 = `[1, len(Kingdom)]` + `optional`。
2. **除疠的服务端复核**是**前缀池**复核（`_seat_set_is_buildable`），不是第二份规则：
   与 `_assign_activation` / 预览读同一份 `step.pool`。复核只在**答案落地后的第一次重入**
   （effect 尚未开始）跑，结果记在帧私有 `_SEAT_SET_CHECKED` —— 否则 effect 弃掉的牌会让
   目标掉出池、把已发生的发动误判成 `cancel`（`run_flow` 会因此抛 `RuntimeError`）。
3. **除疠的清单**（预发动）现在**有**候选索引了（`ordered=False` ⇒ 不再走"有序多选不下发
   索引"的分支）：界面按预览逐次收窄；AI 按 `_DYNAMIC_POOL_SEAT_STEPS` 用公开的势力收窄。
   新加一个"池依赖已选、且约束不是'排除已选'"的座位步时，**这张表必须登记**，否则
   `test_all_ai_games` 的"零非法应答"会红。
4. **`ordered` 的语义收窄**：现在它只剩"第二问依赖第一问的池"（执行期逐座循环 + 清单不下发
   候选索引）一档，**目前没有消费者**。下一次有人要写"两步座位、第二步池依赖第一步"时，
   用 `ordered=True`（先例：`tests/test_hero_active.py::test_the_flow_shape_can_express_a_dependent_two_seat_pick`）。
5. **票 14（建局）不需要改**：本票没有新增 `QueryKind` / 动作面 / 会话配置；开局底稿与
   `hp_max_change` 派生都在票 10/14 的范围内，与座位多选无关。
6. **票 14 若重跑 `check_acceptance`**：本票的 Answer 引用的 8 个新名字都在
   `tests/test_seat_multi_select.py` / `tests/test_hero_card_zone.py` /
   `tests/test_hero_jie_qun.py` / `tests/test_hero_active.py` 里，且全绿。
