# 05: 无懈可击——只问合格者 + 同一窗口呈现

**What to build:** 【无懈可击】窗口只问"此刻真的有办法打出无懈"的座位（含【看破】这类转化能力），
并把同一窗口的声明在一次界面里呈现，而不是每个座次刷一遍。

**Blocked by:** 02（策略位与预筛口）、04（档位拆分；无懈窗口也要按档问）
Status: open
Type: task
Touches: src/sgs/engine/steps.py, src/sgs/effects/actions.py, tools/sgs_web/static/pending.js, tools/sgs_web/static/session.js, tools/sgs_web/projection.py, tests/test_nullify.py, tests/test_card_request_windows.py
规则原文: 三国杀详细规则.txt §4.16 L443–L466
Ruling: spec §2 Q3（用户 2026-11-11 签 (i)）

## 根因（spec §1 #8）

收集式窗口 `_collected_response_window`（`engine/steps.py:803-891`）已经实现
**"收齐声明、首个声明者赢（= 应答者区间序）、输家不耗牌、不压帧"**——用户要的语义今天已经成立。
问题是**顺序收且不筛人**：`response_askers = seats_from(...)`（`:826-831`）是**全体存活**，
`while` 逐个 `ask_respond`，没牌的人也会收到一条只有 `Pass` 的询问。
`tests/replays/16-nullify-cancels-a-trick.jsonl` 每个窗口连发 6 条 RESPOND、**其中 5 条只有 `Pass`**。

## 要做什么

1. **开窗前预筛**：用票 02 的 `Engine.respond_candidates`（`game.py:2686`，读 `legal_actions_for`，"不问/不记/不耗序号"）
   算每个座位此刻的候选；**除 `Pass` 外为空 ⇒ 不进 `response_askers`**。
   **合格判据必须包含转化能力**：卧龙诸葛【看破】（黑牌当无懈）、界【渐营】②（当任意基本牌）、
   【蛊惑】——它们都是 `ActivateSkill` 提供者 / 转化候选，`legal_actions_for` 里本来就有；
   不要在预筛里另写一份"有没有真无懈"的判据。
2. **赢家与输家语义保持不变**：`declared[0]` 生效；输家的牌不消耗、不压帧、不产生使用事件。
3. **前端同窗呈现**：同一 `window_frame`（`steps.py:850` 的 `window_frame=frame.frame_id`）的若干条声明，
   在事件流里合并成一条"无懈窗口"条目，不逐座刷；玩家自己的询问仍是唯一的一次 pending
   （`static/pending.js` 的 `isOwnPending` 已保证只看自己的）。
4. **AI 适配**：`heuristic.py` 的无懈判据（`_nullify` 附近）在座位被预筛掉时**不会被调用**——确认没有副作用
   （它只读自身 view + target）。
5. **写一条账本行**（票 06 落）：`无懈窗口"OL 特殊处理"` 的依据 —— 官方 2018 规则集按语：
   「特別地，OL 對【無懈可擊】…做了特殊處理，沒有嚴格按照多角色結算原則順序，但這麼處理顯然利大於弊，
   因為大大減少了玩家等待別人回應的時間」。

## 验收

- 新增：`test_a_seat_without_a_nullify_or_any_conversion_is_not_asked`。
- 新增：`test_a_seat_with_kanpo_black_card_is_still_asked`（卧龙诸葛 A 场景：真无懈 0、黑牌 1 ⇒ 必须被问）。
- 新增：`test_a_seat_with_jianying_conversion_is_still_asked`。
- 新增：`test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card`
  （断言输家的手牌张数不变、没有 `card_move`、只有赢家一条 `UseCard`）。
- 改写：`tests/test_nullify.py:444`「每个存活座位每层都被问一次，即使没有无懈」——它是**现行口径的钉子**。
- 记录流会变（少掉 Pass-only 的 `query` 记录）⇒ 基准重录并入票 06。
