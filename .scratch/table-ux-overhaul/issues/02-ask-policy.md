# 02: 询问策略位——"无合法候选时要不要问"收成一个开关（**只落管道，不接线**）

**What to build:** 把散落在各调用方的"跳过 / 照问"收成一条**显式策略**（三类 + 一个会话级开关）：
策略对象、会话配置、侧车 meta、统一预筛口全部就位，但**本票不接任何窗口** ⇒
记录流零变化、金录像零重录。真正的消费者在票 05。

**Blocked by:** None
Status: resolved
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

## Answer

### 改动摘要（只落管道，不接窗口）

1. **策略对象** `AskPolicy(auto_skip: bool = False)` 落在 `src/sgs/state/protocol.py`
   （与 `RespondContext` 同模块：它是"询问流"的两档配置之一，不是 schema；`sgs.state` 再导出）。
   引擎侧 `Engine.__init__` / `new_game` / `from_state` 都带
   `ask_policy: AskPolicy = AskPolicy()`（**缺省 OFF**）并存成 `Engine.ask_policy`；
   网页侧 `Session.__init__` 带 `ask_policy: AskPolicy = DEFAULT_WEB_ASK_POLICY`
   （**缺省 ON**），并把同一份对象传给 `Engine.new_game`。
2. **窗口类别位** `RespondContext.skip_if_empty: bool = False`；唯一置位通道是
   `Engine.ask_respond(..., skip_if_empty=...)`（落进 payload）。**本票没有任何生产置位调用点**。
3. **统一预筛口** `Engine.should_ask_respond(...)`：判据 = `ask_policy.auto_skip` ×
   `skip_if_empty` × `respond_candidates` 预览（"除 `Pass` 外是否还有候选"）。参数与
   `ask_respond` 逐项同名；预览复用 `respond_candidates` / `legal_actions_for`，不另写
   "这个座位有没有真无懈 / 转化能力"的判据。给 `respond_candidates` 补了与 `ask_respond`
   对齐的 `declared=` 转参（收集式窗口"赢家已定 ⇒ 提供者闭嘴"在预览里也必须成立）。
4. **B 类目标维度** `ActivationPlan.mandatory_targets: tuple[int, ...] | None = None`：
   `None` = 无目标维度（既有全部技能，行为不变）；`()` = 必选而**没有合法目标** ⇒
   `activation_plan` 压 `available=False`，候选收集口一个候选都不造。与 flow 必选目标步的
   `"cancel"` 静默跳过**行为等价**：记录流逐字节不变，只少一条空转的表现层发动事实
   （`Engine.skill_activations` 是只增缓冲，不进记录流）。
5. **会话配置落盘**：`serve.GameRegistry.create` 读 body `{"ask_policy": {"auto_skip": bool}}`
   （也接受 `bool` 简写；字段缺席 = 网页缺省 ON；形状不对走既有 400）→
   `build_meta` 写 `"ask_policy": {"auto_skip": ...}` → 回放经
   `engine_from_meta(meta)`（从 `replay_journal` 拆出的同一重建路径）还原；**旧录像没有该
   字段 ⇒ 回落引擎缺省 OFF**。`META_VERSION` 保持 1、不列为必填（升版会让旧录像整批读不动）。
   **不进 `RuleConfig`**：`RuleConfig` / journal header / 任何记录一字未动。
6. **不接任何窗口**：五处 A 类窗口、前端、`view()` 下发面、`RuleConfig` 全未动；`src/` 里
   `should_ask_respond` 的调用点为 0、`skip_if_empty=True` 的生产调用点为 0。

### 验收 → 断言（全部存在且绿）

| 验收条目 | 断言 |
|---|---|
| 引擎缺省 OFF / 网页会话缺省 ON（并真的落到引擎上） | `tests/test_web.py::test_the_ask_policy_defaults_off_at_the_engine_and_on_in_a_web_session` |
| 预筛口只认"除 Pass 外还有候选"；策略 OFF 或类别位未声明 ⇒ 照问 | `tests/test_web.py::test_the_prefilter_helper_picks_no_candidate_beyond_pass_as_askable` |
| 预筛是纯预览：不耗询问序号、不写记录、不换挂起询问 | `tests/test_web.py::test_the_prefilter_helper_does_not_consume_a_query_slot` |
| 侧车冻结策略 + 回放还原 + 旧录像回落 OFF | `tests/test_web.py::test_the_sidecar_meta_freezes_the_ask_policy_and_replay_restores_it` |
| spec §1 #10「B 类无合法目标直接跳过」（acceptance-map 票 02 行） | `tests/test_skills.py::test_a_forced_skill_with_no_legal_target_is_not_run_and_records_nothing` |
| 硬验收：本票零重录、记录流逐字节不变 | `bash tools/check_goldens.sh` → PASS（47 条标准 108 冻结字节） |

### 会红的既有断言（0，符合预计）

- `tests/test_nullify.py:444`（每个存活座位每层都被问一次，即使没有无懈）**保持绿**：本票
  不调用预筛口。
- `tests/test_ask_points.py` 的 `_ASK_*` 机械闸**未改**（本票不加询问点）。
- 新增的登记项（见下）是**机械闸逼出来的**、不是行为变化：`tests/test_card_request_windows.py`
  把任何 `respond_candidates(...)` 调用都算"要牌站点"，故 `CARD_REQUEST_WINDOWS` 补一行
  `prefilter.respond`（`REQUEST_PLUMBING`）认领 `Engine.should_ask_respond` 内部那次预览。
  它明说"不是窗口"：五处 A 类窗口在票 05 共用这一个转发入口。
- AI 适配 `docs/agents/ai-adaptation.md` §1：**不适用**——没有新 `QueryKind` / 新候选构造子 /
  新信息通道；策略缺省 OFF 且预筛零调用点 ⇒ AI 收到的询问逐字不变。登记表无需改动。

### 刻意读法（实现期裁定）

1. **`AskPolicy` 住 `state/protocol.py` 而不进 engine/data**：它是"询问流"的两档配置，与
   `RespondContext.skip_if_empty` 是一对（会话总开关 × 窗口类别位），放一起才不会让网页层
   反向依赖引擎的私有面；放 `RuleConfig` 会进 journal header 字节面（明令禁止）。
2. **预筛口是"策略感知"的**（不是裸"有没有料"）：`auto_skip=False` ⇒ 恒 `True`。否则票 05
   的窗口只要漏判一次策略，引擎缺省 OFF（金录像零变化）的保证就落空。类别位缺省 `False`
   ⇒ 它也不按候选内容替调用方猜"这扇窗口可不可跳过"。
3. **`skip_if_empty` 是"字段 + 形参"两处**：字段是文档/载荷（本票只加字段），形参
   `ask_respond(..., skip_if_empty=...)` 是唯一置位通道。票 05 在 A 类窗口给
   `should_ask_respond(..., skip_if_empty=True)` 与 `ask_respond(..., skip_if_empty=True)`
   传**同一个值**（前者决定问不问，后者让候选生成载荷带上同一份窗口声明）。
4. **`mandatory_targets` 用"候选元组"而不是布尔**：布尔说不清"必选而无合法目标"；计划构造
   方本来就能读 `state` / `frame` 算池。空元组是唯一起作用的档（压 `available`），非空元组
   不改变既有 `available` 判据。**普查**：今天目录里没有"强制段 + 必选目标步"的技能
   （`locked` / `Hook.forced` 的流水线全是零步或 `PickOption`），故这是**给下一条 B 类技能
   的声明点**；验收用合成锁定技钉住闸门 + "记录流逐字节不变"的等价性。
5. **`engine_from_meta` 从 `replay_journal` 拆出**：策略不进记录流，没有独立重建入口的话
   "回放还原"只能靠跑出来的记录流反推、等于测不到；拆出后回放与读方断言共用同一条重建路径
   （`CODING_STANDARDS.md` §6）。
6. **侧车字段可选、版本不升**：`read_run_meta` 只认 `META_VERSION = 1`，升版会把旧录像整批
   读不动；而"字段缺席 = 当年就是 OFF"正是回放旧录像想要的语义。

### 门禁结果

- `.venv/bin/pytest tests/test_web.py tests/test_ask_points.py` → **307 passed**。
- `bash tools/check_goldens.sh` → **PASS: 47 条标准 108 金录像逐字节不变**（零重录）。
- 手工补充子集：`test_card_request_windows` / `test_skills` / `test_nullify` /
  `test_ai_adaptation` / `test_source_discipline` / `test_action_contract` /
  `test_engine_journal` / `test_engine_answer_binding` / `test_hero_catalog` /
  `test_engine_cache` / `test_determinism` / `test_skill_flow` / `test_skill_authoring_docs`
  → 全绿。
- 全量门禁 = 本提交的 `pre-commit`（`tests/run_all.sh --quiet`，`src/`+`tests/` 改动走全量）：
  **本提交能落地即代表它全绿**（hook 拒绝则提交不落地）。

### 票 05 的调用面（下一手照这个接）

```python
# 五处 A 类窗口在发问前（参数与随后的 ask_respond 逐项同名）：
if not self.should_ask_respond(
    asker,
    cause=WindowCause.NULLIFY,          # 各自窗口的 cause
    required=NULLIFY_RESPONSE_CARD,     # 各自窗口要的牌名
    target=target,                      # 有就传（无懈的窗口目标）
    mode=RespondMode.USE,               # 借刀 / 无懈是 USE；打出是 PLAY
    declared=bool(frame.data["response_declared"]),  # 收集式窗口：与发问同源
    floor=need_card_floor(frame, asker),             # 与 ask_respond 同源；改判不传
    skip_if_empty=True,                 # ← 窗口类别位：A 类置 True
):
    continue
action = self.ask_respond(..., skip_if_empty=True)   # 同一份入参 + 同一类别位
```

- 网页会话（`Session` 缺省）已经 ON；引擎缺省仍是 OFF ⇒ 金录像 / AI 对战 / 旧录像零变化。
- 无懈"合格者"（真牌 ∪ 【看破】一类转化 / 提供者）**不要另写判据**：预筛内部的
  `legal_actions_for` 已经同时给出真牌 `UseCard` 与 `ActivateSkill` 提供者。
- `should_ask_respond` 对 `mode=REJUDGE` 不传 `floor`（与 `respond_candidates` 同一条豁免）。
