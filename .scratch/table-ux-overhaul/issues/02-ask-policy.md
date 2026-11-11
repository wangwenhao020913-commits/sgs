# 02: 询问策略位——"无合法候选时要不要问"收成一个开关

**What to build:** 把散落在各调用方的"跳过 / 照问"收成一条**显式策略**（三类 + 一个会话级开关），
不再由每个窗口各写一遍；开关缺省 ON（允许自动跳过）。

**Blocked by:** None
Status: open
Type: task
Touches: src/sgs/state/protocol.py, src/sgs/engine/game.py, src/sgs/engine/steps.py, src/sgs/effects/actions.py, src/sgs/effects/ops.py, src/sgs/skills/lord_request.py, tools/sgs_web/session.py, tools/sgs_web/serve.py, tools/sgs_web/replay.py, tests/test_nullify.py
Ruling: spec §2 Q4 / Q19（用户 2026-11-11 签）

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

## 要做什么

1. **加策略位**：`RespondContext.skip_if_empty: bool`（默认 `False` = 照问，保旧行为可复现），
   由**调用方按类别**置位：
   - A 类窗口（required / cause 为 DODGE / PEACH / NULLIFY / 决斗 / 南蛮 / 万箭 / 护驾求助）＝"可由策略跳过"；
   - C 类不进这条判据（它们本来就该跳）。
2. **加一条统一预筛口**：`Engine.respond_candidates`（`game.py:2686`，已有"不问/不记/不耗序号"的预览）
   在窗口开窗前被调用；候选里**除 `Pass` 外没有别的东西** ⇒ 该座位不进 askers。
   **合格判据必须包含转化能力**（票 05 的【看破】场景），预览读的就是 `legal_actions_for`，天然包含。
3. **B 类补目标维度**：`ActivationPlan`（`catalog.py:506-524`，现只有 optional / available / actor）加"必选目标"档，
   让 `available` 在"必选而无合法目标"时为 `False` ⇒ 一个询问都不发（走 `catalog.py:1792` 的既有路径）。
4. **开关落会话配置**：`Session.__init__` 加参数，`serve.py` 建局读 body，**进侧车 meta**
   （`tools/sgs_web/replay.py:78-92` 的 `build_meta`），`replay_journal` 还原它；**不进 `RuleConfig`**
   （`src/sgs/data/rule_config.py:22-61` 会进 journal header ⇒ 打翻全部基准字节）。
5. **缺省 ON**（用户 Q4/Q19）：网页会话缺省允许自动跳过；引擎层参数缺省也取 ON。

## 验收

- 新增：`test_a_seat_without_any_legal_response_card_is_not_asked_when_auto_skip_is_on`
  （无懈 / 闪 / 求桃 三扇窗口各一条）。
- 新增：`test_auto_skip_off_still_asks_a_pass_only_window`（开关关 = 现状，用来证明可复现）。
- 新增：`test_a_forced_skill_with_no_legal_target_is_not_run_and_records_nothing`（B 类）。
- 新增：`test_the_sidecar_meta_freezes_the_ask_policy_and_replay_restores_it`。
- 记录流会变（少掉 Pass-only 的 `query` 记录）⇒ **基准重录并入票 06**，本票先只把清单写进 `## Answer`。

## 会红的既有断言

- `tests/test_nullify.py:444`「没有无懈的座位仍会被问」——**现行口径的钉子**，本票要改写它。
- 60 条 `tests/replays/*.jsonl` 中含无懈窗口的 45 条（以及 Pass-only 闪/桃窗口）在本票落地后会不一致，
  但**不要在票 02 里重录**（票 06 统一做）。
- `tests/test_ask_points.py`（新增询问点必须登记 `_ASK_*`）、`tests/test_ai_adaptation.py`（AI 读点表）。
