# 02: 询问策略位——"无合法候选时要不要问"收成一个开关（**只落管道，不接线**）

**What to build:** 把散落在各调用方的"跳过 / 照问"收成一条**显式策略**（三类 + 一个会话级开关）：
策略对象、会话配置、侧车 meta、统一预筛口全部就位，但**本票不接任何窗口** ⇒
记录流零变化、金录像零重录。真正的消费者在票 05。

**Blocked by:** None
Status: open
Type: task
Touches: src/sgs/state/protocol.py, src/sgs/engine/game.py, src/sgs/skills/catalog.py, tools/sgs_web/session.py, tools/sgs_web/serve.py, tools/sgs_web/replay.py, tests/test_web.py, tests/test_ask_points.py
Ruling: spec §2 Q4 + §3 注①（用户 2026-11-11 签 B：引擎缺省 OFF / 网页会话缺省 ON）

## 根因（spec §1 #10）

引擎**完全不判空**：`Engine.ask`（`game.py:5174`）无条件建询问、写 `QueryRecord`、挂起；`ask_query` / `ask_respond`
只是薄包装；`submit` 是 fail-closed 成员判定。**"跳不跳"100% 由调用方决定**，于是：

| 类别 | 现状 | 位置 |
|---|---|---|
| A 需要/可以使用或打出牌 | **一律照问**（没牌的座位也给一条只有 `Pass` 的面） | 无懈 `steps.py:843`、闪 `:760`、求桃 `:1510`、护驾 `lord_request.py:381`、决斗 `ops.py:568`、南蛮/万箭 `ops.py:804` |
| A 的另一些 | **已预检跳过**（反而不统一） | 改判 `steps.py:1836-1852`、借刀 auto-b `ops.py:688-706`、乱武 `active.py:6355` |
| B 强制生效非牌类 | `forced` 不问直接跑，**没有目标维度判据** | `catalog.py:1799-1807 / 2050-2090` |
| C 可以类 | 已正确：空池跳过 / flow `"cancel"` / 雌雄付不起不进候选 | `game.py:2825`、`flow.py:2309`、`catalog.py:2945` |

**根障碍**：RESPOND 候选**恒含 `Pass`**（`effects/actions.py:1026/1260/1319`）⇒ A 类（"需要/可以打出一张牌"）
与 C 类（"可以放弃"）今天无法区分，缺一个"这扇窗口是否可放弃"的**显式位**。

## 要做什么（只做管道）

1. **策略对象**：`AskPolicy(auto_skip: bool)`（放哪由实现定）。
   **引擎构造参数缺省 `OFF`**（旧录像 / `batch_games` / `ai_eval` 逐字节可复现）；
   **`Session` 构造参数缺省 `ON`**（玩家在网页里立刻看到效果）。
2. **策略位**：`RespondContext.skip_if_empty: bool`（默认 `False` = 照问），由**调用方按类别**置位；
   本票只加字段与文档，**不加任何置位调用点**。
3. **统一预筛口**：把"这扇窗口该不该问这个座位"收成一个纯函数（建议 `Engine.should_ask_respond(...)`），
   内部读 `respond_candidates`（`game.py:2686`，已有"不问/不记/不耗序号"的预览）+
   "除 `Pass` 外是否有别的候选"。**本票只加这个函数，不调用它。**
4. **B 类补目标维度**：`ActivationPlan`（`catalog.py:506-524`）加"必选目标"档，让 `available` 在
   "必选而无合法目标"时为 `False`（现状靠 `flow` 的 `"cancel"` 静默跳过，行为等价 ⇒ 记录流不变）。
5. **落会话配置**：`Session.__init__` 参数 → `serve.py` 建局读 body → **进侧车 meta**
   （`tools/sgs_web/replay.py:78-92` 的 `build_meta`）→ `replay_journal` 还原。
   **不进 `RuleConfig`**（`src/sgs/data/rule_config.py:22-61` 会进 journal header ⇒ 打翻全部基准字节）。
6. **不做**：不接任何窗口（无懈 / 闪 / 求桃 / 护驾 / 决斗 / 南蛮 / 万箭）——票 05 才接。

## 验收

- 新增：`test_the_ask_policy_defaults_off_at_the_engine_and_on_in_a_web_session`。
- 新增：`test_the_prefilter_helper_picks_no_candidate_beyond_pass_as_askable`
  （合成一扇窗口：一个座位只有 `Pass`、另一个有真牌 ⇒ 前者不 askable，后者 askable）。
- 新增：`test_the_prefilter_helper_does_not_consume_a_query_slot`（预览前后 `pending.seq` / 记录数不变）。
- 新增：`test_the_sidecar_meta_freezes_the_ask_policy_and_replay_restores_it`。
- **硬验收**：`bash tools/check_goldens.sh` **全绿**（本票不接线，记录流必须逐字节不变）。
  若红 ⇒ 说明不小心接到了窗口，回退那处。

## 会红的既有断言（预计 0）

- 本票不接窗口 ⇒ `tests/test_nullify.py:444`（"没有无懈的座位仍会被问"）**保持绿**。
- `tests/test_ask_points.py` 若新增询问点要登记 `_ASK_*`；本票不加询问点。
