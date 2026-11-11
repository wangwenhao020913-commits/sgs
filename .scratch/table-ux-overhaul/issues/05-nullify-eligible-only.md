# 05: 无懈可击——只问合格者 + 同一窗口呈现（**接窗口，引擎缺省 OFF**）

**What to build:** 把 A 类窗口接到票 02 的策略预筛上：只问"此刻真的有办法打出这张牌"的座位
（无懈含【看破】这类转化能力），并把同一窗口的声明在一次界面里呈现。
**引擎缺省 OFF / 网页会话缺省 ON** ⇒ 金录像逐字节不变。

**Blocked by:** 02（策略管道）、04（档位拆面；无懈窗口也要按档问）
Status: resolved
Type: task
Touches: src/sgs/engine/steps.py, src/sgs/effects/ops.py, src/sgs/skills/lord_request.py, src/sgs/engine/game.py, tools/sgs_web/static/pending.js, tools/sgs_web/static/session.js, tests/test_nullify.py, tests/test_card_request_windows.py
规则原文: 三国杀详细规则.txt §4.16 L443–L466
Ruling: spec §2 Q3 + §3 注①（用户 2026-11-11 签 (i) + B）

## 根因（spec §1 #8）

收集式窗口 `_collected_response_window`（`engine/steps.py:803-891`）已经实现
**"收齐声明、首个声明者赢（= 应答者区间序）、输家不耗牌、不压帧"**——用户要的语义今天已经成立。
问题是**顺序收且不筛人**：`response_askers = seats_from(...)`（`:826-831`）是**全体存活**，
`while` 逐个 `ask_respond`，没牌的人也会收到一条只有 `Pass` 的询问。
`tests/replays/16-nullify-cancels-a-trick.jsonl` 每个窗口连发 6 条 RESPOND、**其中 5 条只有 `Pass`**。

## 要做什么

1. **把五处 A 类窗口接到票 02 的预筛口**（`Engine.should_ask_respond`）：
   无懈 `steps.py:826-831`、闪 `steps.py:760`、求桃 `steps.py:1510`、
   护驾求助 `lord_request.py:381`、决斗 `ops.py:568` / 南蛮·万箭 `ops.py:804`。
   **按策略开关生效**：引擎缺省 OFF ⇒ 引擎层（金录像 / AI 对战 / 旧录像回放）逐字节不变；
   网页会话 ON ⇒ 玩家看到新行为。
2. **合格判据必须包含转化能力**：卧龙诸葛【看破】（黑牌当无懈）、界【渐营】②、蛊惑等
   都是 `ActivateSkill` 提供者 / 转化候选，`legal_actions_for` 里本来就有；
   **不要**在预筛里另写一份"有没有真无懈"的判据（票 02 的 `respond_candidates` 已经是同一个生成器）。
3. **赢家与输家语义保持不变**：`declared[0]` 生效；输家的牌不消耗、不压帧、不产生使用事件。
4. **前端同窗呈现**：同一 `window_frame`（`steps.py:850` 的 `window_frame=frame.frame_id`）的若干条声明，
   在事件流里合并成一条"无懈窗口"条目，不逐座刷；玩家自己的询问仍是唯一一次 pending
   （`static/pending.js` 的 `isOwnPending` 已保证只看自己的）。
5. **AI 适配**：`heuristic.py` 的无懈判据在座位被预筛掉时不会被调用——确认无副作用
   （它只读自身 view + target）。

## 验收

- 新增（全部在**策略 ON** 的会话/引擎上构造）：
  - `test_a_seat_without_a_nullify_or_any_conversion_is_not_asked`
  - `test_a_seat_with_kanpo_black_card_is_still_asked`（卧龙诸葛：真无懈 0、黑牌 1 ⇒ 必须被问）
  - `test_a_seat_with_jianying_conversion_is_still_asked`
  - `test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card`
    （断言输家手牌张数不变、没有 `card_move`、只有赢家一条 `UseCard`）
  - `test_a_web_session_defaults_the_policy_on_and_the_engine_defaults_it_off`（与票 02 的判据接起来）
- **硬验收**：`bash tools/check_goldens.sh` **全绿**（引擎缺省 OFF ⇒ 记录流零变化）。
- 引擎级注入 ON 后重跑 `tests/test_nullify.py` 的旧用例，逐条确认"行为变化只发生在 ON"。

## 会红的既有断言（预计 0）

- `tests/test_nullify.py:444`（"每个存活座位每层都被问一次，即使没有无懈"）在**引擎缺省 OFF** 下**保持绿**；
  另加一条 ON 时的新断言，不改写旧口径。
- `tests/test_card_request_windows.py` 里对"空窗口仍被问"的断言同理。

## Answer

**已实现（2026-11-11）** —— 票面五条"要做什么"全部落地，`## 验收` 的 5 条断言全绿；
**引擎缺省 OFF ⇒ 金录像逐字节不变**（`bash tools/check_goldens.sh` → **PASS：47 条标准 108
金录像逐字节不变**，本票**零重录**）。

### 改动摘要

1. **五处 A 类窗口接上票 02 的预筛口**（发问**之前**问一句，跳过 = 与答 `Pass` **逐字等价**
   的那条分支；`skip_if_empty=True` 同一个值同时给预筛与随后的 ask）：
   - 无懈 `steps._collected_response_window`（`should_ask_respond`，带 `declared` / `floor`）
   - 【闪】`steps._dodge_window`（`should_ask_query`，载荷 = 随后那次询问的同一份
     `DodgeContext`：`providers_used` + `floor`）
   - 求桃 `steps.near_death_ask`（`should_ask_query`，`PeachContext(floor=...)`）
   - 护驾求助 `lord_request._run_lord_request`（`should_ask_respond`）
   - 决斗 `ops.resolve_duel` / 南蛮·万箭 `ops._resist_or_damage`（`should_ask_respond`，
     `providers_used` 与 `floor` 与真询问同源）
2. **为什么多了一个 `Engine.should_ask_query`**：`ask_respond` 与 `ask_query` 的候选载荷
   不同（`RespondContext` vs `DodgeContext` / `PeachContext`）。硬塞进一个入口就得在一处
   伪造另一种载荷，预览与真询问立刻不同源；两个入口因此共用**同一句判据**（会话策略 ×
   窗口类别位 × 候选预览），各读自己的载荷。`FrameHost` 协议同步补了两条声明。
3. **合格判据不另写**：判据就是窗口真发问时用的那一个生成器（`respond_candidates` /
   `legal_actions_for`）。真牌 `UseCard`、单源转化（卧龙【看破】：虚拟无懈的 `UseCard`，
   今天住在询问的 `transform_actions`/转化清单里）、提供者（界【渐营】② 的
   `ActivateSkill`）都算合格——引擎里**没有**第二份"有没有真无懈"的谓词。
4. **赢家 / 输家语义一字未改**：`declared[0]` 生效、输家的牌不消耗 / 不压帧 / 不产生使用
   事件；ON 的用例把这三条重钉了一遍。
5. **前端同窗呈现**：`tools/sgs_web/static/log.js` 新增「无懈窗口」合并条目——同一
   `window_frame` 的若干条询问 / 声明只占**一条** `<li>`（DOM 形状不变：仍是普通 `<li>` +
   innerHTML），每问一个座位就把座位添进「询问」列表、每声明一次补一条「声明」，**不逐座刷
   行**。记账按**事件流容器**分开（`WeakMap`：牌桌 `#t-log` / 回放 `#r-log` 互不串味），
   三处"清空 / 重画整条流"接上了 `resetWindowLogs`（`renderLog`、`applyView` 换会话、
   `renderReplayLog`）。玩家自己的那一问仍是唯一一次 pending（`pending.js` 的
   `isOwnPending` 未改、也不需要改）。
6. **`AskPolicy` / `RespondContext.skip_if_empty` 的 docstring** 去掉了"本票不接窗口"的
   旧话，改成"消费者 = 五处 A 类窗口"；`protocol.py` 的字段说明同步。

### 验收 → 断言（前四条全部在**策略 ON** 的引擎上构造；第五条读两份**真实缺省**）

| 验收条目 | 断言 |
|---|---|
| 没有真无懈、也没有任何转化能力的座位**不被问** | `tests/test_nullify.py::test_a_seat_without_a_nullify_or_any_conversion_is_not_asked`（只剩持真牌的座位 1 被问，锦囊照样被抵消） |
| 【看破】黑牌 ⇒ 仍必须被问 | `tests/test_nullify.py::test_a_seat_with_kanpo_black_card_is_still_asked`（面上那条虚拟无懈的 `virtual_sources == (黑牌,)`） |
| 界【渐营】② 转化 ⇒ 仍必须被问（求桃窗口） | `tests/test_nullify.py::test_a_seat_with_jianying_conversion_is_still_asked`（真【杀】打到 0 体力 ⇒ 求桃窗口打开，回合角色被问且面上有 `…jianying` 的 `ActivateSkill`） |
| 两条声明：赢家生效、输家不耗牌 | `tests/test_nullify.py::test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card`（输家手牌不变 / 无它的 `card_move` / 只有 2 条 USE 帧 / 它的声明被接受但没产生使用） |
| 网页缺省 ON、引擎缺省 OFF，且**真的改窗口行为** | `tests/test_web.py::test_a_web_session_defaults_the_policy_on_and_the_engine_defaults_it_off`（两份缺省从 `Engine.new_game` / `Session.__init__` 取，装进同一扇真无懈窗口：OFF ⇒ `[0, 1]`，ON ⇒ `[]`；**缺省值本身**仍由票 02 的 `test_the_ask_policy_defaults_off_at_the_engine_and_on_in_a_web_session` 钉住，本用例不重复那三条断言） |
| 五处窗口**都**接了（机械闸：少接一处就红） | `tests/test_card_request_windows.py::test_every_a_class_window_consults_the_ask_policy_prefilter`（AST 扫全树 `should_ask_respond` / `should_ask_query` 且带 `skip_if_empty=True` 的调用点，与 `A_CLASS_WINDOWS` 六行逐条对账，多出来 / 少一条都红） |
| 硬验收：零重录 | `bash tools/check_goldens.sh` → PASS（47 条） |

### 刻意选择的读法

1. **「合格」算的是"**当前这一档**的面"，不是"整扇窗口的并集"**：`should_ask_*` 读的是
   `payload.floor` —— 也就是随后那次发问会带的那一份载荷。**两者等价**：引擎只在一档确实
   **还有候选**时才把 floor 推进下一档（`next_need_card_tier` 与 `legal_actions_for` 读同一个
   `_actions_for`），所以"当前面除 `Pass` 外为空" ⇔ "整扇窗口对这个座位为空"。硬造并集反而
   会与真询问漂移（并集非空但当前面为空 ⇒ 问了却只有 `Pass`，正是本票要修的报障）。
   无懈窗口今天更平：真牌与一切转化都在档 2（`CARD_USE`），没有更低档的提供者 ⇒ floor 恒 0，
   一次预览就是整扇窗口。
2. **合并键 = `query.window_frame`**（`RespondQuery` 的字段，窗口写
   `window_frame=frame.frame_id`），**不是"记录相邻"**：无懈窗口里提供者的结算帧会插进记录
   流（扣置搬牌 / 逐座位质疑 / 扣体力压子帧），只有帧号能把后面那条询问接回同一条目。应答
   靠 `answers_seq` 回指它的那一问（顶层派生字段与行动内副本两处都认）；声明只收
   `accepted` 且非 `Pass`；另一个 `window_frame` ⇒ 另起一条。
3. **显示层不进记录流**（spec §6 不变量 3）：合并只动 `#t-log` 的渲染，`state.logRecords`、
   服务端叙事、`Query.prompt` 一个字没动；`model_dump` 里没有 `skip_if_empty`（`exclude`）。
4. **不新增"跳过"分支的语义**：每处跳过都复用各窗口**既有的 `Pass` 分支**（无懈 = 不声明、
   闪 = 不抵消、求桃 = 问下一个、护驾 = 下一个、决斗 / 南蛮 = `_push_damage`），没有第二套
   "被跳过"的收尾；`reset_need_card_event` 仍是 floor + `providers_used` 的**唯一**复位点，
   本票没有在任何别处清这两个键。
5. **AI 适配（票面 5）**：`heuristic._nullify_declares` 是**纯函数**（只读 `query.view` 与
   `query.target`），座位被预筛掉时它根本不会被调用 ⇒ 零副作用；AI 的对局（`batch_games` /
   `ai_eval` / `test_all_ai_games`）跑的是引擎缺省 OFF ⇒ 询问逐字不变。
   `docs/agents/ai-adaptation.md` §1 逐条**不适用**：没有新 `QueryKind`、没有新候选构造子、
   没有新信息通道（`skip_if_empty` 是票 02 就有的 `exclude` 字段，本票只加消费者）。

### 会红的既有断言（预计 0 → 实际 7）

6 条是"网页会话行为有意变更 / 新调用点"的直接后果，第 7 条是**同一判据**的种子重扫：

1. `tests/test_web.py::test_the_halt_driven_journal_is_byte_identical_to_a_one_shot_run[3/101/4096]`
   —— 对照跑法用 `Engine.new_game(...)` 的**引擎缺省**（OFF），而 `Session` 缺省 ON ⇒ 比的是
   两份不同配置。改法：对照引擎带上 `ask_policy=session.ask_policy`（"挂起驱动不改记录流"
   这条判据本身不变，docstring 早已要求"对照跑法必须与 Session 同配置"）。
2. `tests/test_web.py::test_the_halt_driven_journal_replays_through_the_tape_player` —— 同上：
   回放引擎要带会话的策略，否则它会问出录像里没有的空询问、磁带对不上。
3. `tests/test_web.py::test_the_respond_query_carries_no_second_target_stage` —— 样本量：
   ON 后**人类座位自己**的 RESPOND 询问从每局几十条掉到个位数（空手座位不再被问）。按
   `CODING_STANDARDS.md` §5 **加样本、不降阈值**：样本改从**记录流**取（每个座位的 RESPOND
   询问都在里面，同一份 `RespondQuery` 模型），三颗种子合计 **89** 次，阈值 50 不动。
4. `tests/support/ui_smoke.mjs`「唯一候选不铺按钮，只说明将提交什么」—— 它假设"建局首问是
   只有 `Pass` 的 RESPOND"，而 ON 后这类询问不再存在（首问换成真表面：选牌 / 手牌直选）。
   判据改成"不管落到哪一档都不许出现**候选行按钮**"，`direct` 那一档仍钉原来的两件事
   （零按钮 + 说明将提交什么）。
5. `tests/support/ui_smoke.mjs`「U2-R11 真会话驱动到真实取牌步」—— 旧断言 `declined > 0`
   钉的是"人类把唯一一张【无懈】用掉后，其余每个目标的窗口还问它一遍"；ON 后不再问
   ⇒ `declined == 0`（循环保留，其余字段一字未改）。
6. `tests/test_card_family_sites.py::test_every_slash_literal_in_the_source_tree_is_classified`
   —— 决斗那处 `required="slash"` 从 1 处变 2 处：票 05 在**发问前**多了一次同一份请求
   （预筛口用与真询问逐项同名的入参）。登记改成 `count=2` 并写明"第二处不另判族，只是把
   同一个字面量转发给候选预览"（判据仍是 `FAMILY`）。
7. `tests/test_web.sh` 的「酒 tape（濒死自救）」—— **种子重扫（不是放宽判据）**：ON 后一次
   `drive` 里人类被问的次数变少 ⇒ AI 回合跑得更远，旧 seed 16 在"自救成功"之后、人类被问到
   下一问之前就死了（自救本身没问题：记录流里 `hp_change 0 → 1` 在 frame 94）。按**同一固定
   策略、同一命中判据**逐 seed 重扫（探针 = `_wine_tape_near_death` 本身，
   `io.redirect_stdout` 吞掉逐条 FAIL），命中最短路径的 **seed 9**（17 次真实 POST；9 未被其余
   tape 占用：火攻 4 / 16、出牌阶段 65）。改的是 `_WINE_TAPE_NEAR_DEATH_SEED` 与它的台账注释。

**保持绿的旧口径**：`tests/test_nullify.py::test_every_alive_seat_is_asked_once_per_layer_even_without_nullify`
（引擎缺省 OFF）、`tests/test_card_request_windows.py` 里对"空窗口仍被问"的断言——
本票**没有改写**它们（票面"会红（预计 0）"成立）。

### 门禁结果（本机实测）

- `.venv/bin/pytest tests/test_nullify.py tests/test_card_request_windows.py` → **36 passed**
- `.venv/bin/pytest tests/test_web.py` → **305 passed**
- `.venv/bin/pytest`（票 05 直接波及面）：`test_hero_response_window` / `test_near_death` /
  `test_near_death_rollback` / `test_hero_jie_yijiang` / `test_hero_huo` / `test_timing` /
  `test_ai_adaptation` / `test_ask_points` / `test_action_contract` / `test_engine_journal` /
  `test_engine_answer_binding` / `test_determinism` / `test_web_static` → 全绿；
  `test_heuristic_ai` → **133 passed**
- `bash tools/check_goldens.sh` → **PASS：47 条标准 108 金录像逐字节不变**（零重录）
- `bash tests/smoke-web.sh` → **PASS: 前端渲染冒烟（全部通过）**
- `bash tests/test_web.sh`（真 HTTP 端到端 + 五段 tape）→ **PASS: M4 端到端门禁全部通过**
  （含重扫后的「酒 tape（濒死自救）」）
- 全量门禁 = 本提交的 `pre-commit`（`tests/run_all.sh --quiet`）：**本提交能落地即代表它全绿**
  （hook 拒绝则提交不落地）。两次被 hook 拒绝、各自修掉再看输出：第一次红的是「酒 tape」
  （重扫 seed 后 `bash tests/test_web.sh` 手工 PASS），第二次红的是上面第 6 条
  【杀】家族站点计数（登记补 `count=2`）。

### 前端实机验收（`bsk` 未由本票驱动，留配方）

DOM 形状未变（合并条目仍是普通 `<li>` + innerHTML，`#t-options` / pending 面一个字未动），
最小实机覆盖：

1. `bash tools/check_env.sh && bash tools/serve-dev.sh start`（缺省 8100，起后打印实际 URL）；
2. 开局 3–4 人局（网页会话缺省 ON）。在**完整全部**档（`#t-log-mode = all`）下找一次
   【无中生有】/【过河拆桥】一类的锦囊结算：事件流里应当只有**一条**「无懈窗口」条目
   （文案形如 `#21 无懈窗口 · 目标 座位 0 · 询问 座位 0、1 · 座位 1 声明【无懈可击】`），
   而不再是每个座位一行「询问 RESPOND / 应答 放弃」；
3. 同一窗口里**你自己的**询问仍停在提示区（唯一一次 pending，不弹窗、不重复）；
4. 若那一问落到你手上但没有无懈：网页会话**不会再问你**（旧行为是发一条只有「放弃」的询问）。

### 留给下一手（票 06 / 08）

- 票 06：本票**零重录**（`check_goldens.sh` 47 条 PASS），只需在账本里登记"05 = 接窗口、
  引擎缺省 OFF、零重录"；acceptance-map 的票 05 行可用上面 5 条断言名落点。
- 票 08（预发动去弹窗）：五处窗口的**询问面**（pending / `#t-options`）本票一字未改；
  但 `tools/sgs_web/static/log.js` 的 `resetWindowLogs` 现在是"清空 / 重画 `#t-log` 时必须调"
  的一条（三处已接：`renderLog`、`applyView` 换会话、`renderReplayLog`）——票 08 若新增
  清除事件流的入口，记得一起调，否则新局的第一扇窗口会并进上一局那条已摘掉的节点。
- 本票新增的两个测试垫片方法：`_NullifyWindowHost.should_ask_respond`、
  `_DodgeWindowHost.should_ask_query`（最小宿主也要给预筛口回话；真判据由真引擎的用例覆盖）。
