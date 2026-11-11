# 05: 无懈可击——只问合格者 + 同一窗口呈现（**接窗口，引擎缺省 OFF**）

**What to build:** 把 A 类窗口接到票 02 的策略预筛上：只问"此刻真的有办法打出这张牌"的座位
（无懈含【看破】这类转化能力），并把同一窗口的声明在一次界面里呈现。
**引擎缺省 OFF / 网页会话缺省 ON** ⇒ 金录像逐字节不变。

**Blocked by:** 02（策略管道）、04（档位拆面；无懈窗口也要按档问）
Status: open
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
