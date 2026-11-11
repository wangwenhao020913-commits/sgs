# 11: 步骤预览端点——每一步的可点集合都由后端现算

**What to build:** 新增一个**只读、不写记录**的"步骤预览"端点：预发动每变一次选择就问一次后端，
后端用**与校验器同一个** `pool(chosen)` 现算"此刻这一步还能点谁 / 能不能进下一步 / 能不能提交"。
这样前端的合法性永远等于后端，且 `MANIFEST_POOL_LIMIT` 退化为不需要。

**Blocked by:** 01（`view()` 的 O(N) 必须先拆掉，否则每次预览都贵）
Status: resolved
Type: task
Touches: tools/sgs_web/serve.py, tools/sgs_web/session.py, tools/sgs_web/projection.py, src/sgs/skills/preactivation.py, src/sgs/skills/flow.py, src/sgs/engine/game.py, tools/sgs_web/static/preactivation.js, tests/test_web.py, tests/support/action_probe.mjs
Ruling: spec §2 Q6 / Q10（用户 2026-11-11 签 A）

## 根因（spec §1 #6 的机制层）

预发动清单是**在询问那一刻一次性算好**的：每个"前序答案组合"一行池（`StepPool.when` = 前序答案的完整文本，
`options` = 那一刻能选谁）。组合数 > `MANIFEST_POOL_LIMIT = 32`（`skills/flow.py:1318`）就**整步退化**
（`pool=()` + `min=0,max=1`，`flow.py:1792-1811`），前端 `static/table.js:303-304` 兜底成"所有存活座位可点"。
更根本的是：**同一步内**的动态约束（除疠"势力互异"：选了 A 之后同势力 B 必须变灰）静态快照根本表达不了。

## 要做什么

### A. 服务端端点

- `POST /api/sessions/<id>/preview`，body：
  ```json
  { "answers_seq": 42, "skill_id": "hero.jie_huatuo.chuli",
    "answers": [ {"name": "seat", "values": [3]} ] }
  ```
- 校验：`answers_seq` 必须等于当前 pending 的 seq（否则 409，与 `/answer` 同姿态）；
  `skill_id` 必须是这次询问里下发了清单的技能；`answers` 的**步名与顺序**必须与清单一致（前缀，允许空）。
- 回：
  ```json
  { "cursor": 1, "kind": "seat",
    "options": ["seat:3","seat:5","seat:7"], "disabled": [],
    "can_advance": false, "can_submit": false, "next": null }
  ```
  其中 `options`/`disabled` = 用 `_with_answers(...)` 挂上前序答案后调**同一个** pool/lo/hi 回调的结果
  （`skills/flow.py` 的 `_assign_activation` 与 `manifest` 已经共用这份派生，直接复用）。
- **只读**：不建询问、不写记录、不耗序号、不搬牌、不抽随机数（与 `Engine.respond_candidates` 同一条纪律）。
- **不许碰 `view()` / 投影 / 快照**：这是本票的性能底线。

### B. 前端接入

- `static/preactivation.js`：每次选择变化（点牌 / 点座位 / 点选项 / 取消 / 回第一步）后发一次预览
  （**去抖 80–120 ms**，带本地版本号，丢弃过期响应）；用它刷新"可点集合 / 下一步可点 / 提交可点"。
- **fail-closed**：预览失败 / 超时 ⇒ 这一步**不许自动推进、不许提交**，提示"取不到候选，请重试"。
- 自动推进（票 08）的判据改为读预览回包，不在前端另算。
- 牌桌可点集合（`table.js:seatInteraction` / `card-face.js`）改读预览回包；**取消** `table.js:303-304` 的
  "全可点"兜底。

### C. 退役静态池

- 清单不再下发 `pool` / `groups` / `disabled`（保留字段只为回放兼容，读取方切到预览）；
  `MANIFEST_POOL_LIMIT` 与 `_prior_contexts` 退化分支一并删除（用户裁定：**永不退化**）。
- `Step.wording` / `kind` / `min` / `max` **保留**（文案与步类型仍然静态可读）。

## 验收

- 新增：`test_the_preview_endpoint_returns_the_same_pool_the_validator_uses`
  （对同一组 answers，预览的 `options` 与 `validate_activation` 的裁决必须一致——**同源断言**）。
- 新增：`test_the_preview_endpoint_writes_nothing`（预览前后 `engine.records` 长度不变、`pending.seq` 不变）。
- 新增：`test_the_preview_endpoint_rejects_a_stale_answers_seq`（409，与 `/answer` 同形）。
- 新增（烟测）：`U-PV-01` 除疠选 3 号位后，同势力座位立刻变灰（`options` 由预览回包给）。
- 新增（烟测）：`U-PV-02` 预览失败时不许自动推进、不许提交。
- 性能：`## Answer` 里贴"单次预览耗时"（本机应 < 5 ms；若 > 20 ms 要给出原因）。

## 会红的既有断言

- `tests/test_web.py:1665-1705`（合成 Query 的清单转发）、`tests/support/ui_smoke.mjs:4647-4690`（PZ10–12
  把"退化后座位仍可点"钉成契约）——本票要改写它们。
- `tests/test_skill_flow.py:871-886`（显式 `limit=1` 的退化用例）——删除退化分支后要删/改。

## Answer

### 落地摘要

**A · 服务端端点（只读）**

- `POST /api/sessions/<id>/preview`，body `{answers_seq, skill_id, answers:[{name, values}]}`，
  回 `{cursor, kind, options, disabled, can_advance, can_submit, next}`（形状与票面一字不差）。
- 派生住在 `src/sgs/skills/flow.py`：`StepPreview`（只读 dataclass）+ `preview_step(...)`。
  它**复用**清单与校验器共用的那几份派生——`_with_answers` 挂前序答案、`_seat_bounds` 取
  `lo/hi`、`step.pool(state, framed, owner, chosen)` 取座位池、`_resolve_options` 取选项闭集 /
  置灰集、`_card_options` / `_provide_options` 取选牌面——不新写一条谓词。
- 公开入口 `src/sgs/skills/preactivation.py::preview_activation`（`flow_for` 找流水线）；
  `src/sgs/engine/game.py` 只加了一个**只读**访问器 `Engine.activation_frame`（帧外时机的
  清单渲染用的是承载帧，预览要读同一份上下文，与 `submit` 的校验器同一条取法）。
- `tools/sgs_web/projection.py::issued_manifest(query, skill_id)` = "这次询问下发给它的那份
  清单"的**唯一取法**（顺序与 `Engine._validate_structured` 的成员判定同源：逐候选 →
  主动入口 → 转化清单 → 单份清单）；`serve.GameRegistry.preview` 做 `answers_seq` 核对（对不上
  ⇒ `StaleAnswer` ⇒ 409，**与 `/answer` 同形**：`{error, pending}`）、`skill_id` 核对（400）、
  前缀步名核对（400），然后序列化 `StepPreview`。
- **只读全停**：不建询问、不写记录、不耗序号、不搬牌、不抽随机数；**不碰** `view()` / 投影 /
  快照（不走 `advance_clock`——预览不许触发超时代答；只打一次"最后访问"戳供会话回收）。
- **接入层只走公开面**（M4 的第一纪律，`tests/test_dod_m4.py`）：`sgs.skills` 再导出
  `preview_activation`，`projection` / `serve` 只从 `sgs.skills` 取（**不**深挖
  `sgs.skills.preactivation`）。第一版提交就是被这条拦下的（`FAILED
  tests/test_dod_m4.py::test_the_engine_tree_is_not_touched_by_the_web_layer`），已修。
- **算不出来 = 400**：池回调要帧上下文而这一问没有、或提供步给了不在池里的牌 id，都按
  "这份载荷结构非法"回 400——**不许**漏成 `KeyError`（那会被 `do_POST` 的"未知会话"分支
  接走、回一个与真相无关的 404）。

**B · 前端接入**

- 新载荷键 `pending.payload["preview"] = true` 是**新信息通道的能力声明**（与既有
  `structural` / `default_choice` 同一姿态：只有这一档才带这个键）。`projection._preview_supported`
  判据 = 这次询问里**至少有一份清单的技能有流水线**；合成载荷 / 转化清单（龙胆一类）/
  借刀的手牌清单都不带 ⇒ 界面照旧读清单静态行，旧载荷逐字段逐行为不变。
- `static/preactivation.js` 新增预览客户端：**去抖 90 ms**（票面 80–120 窗口）+
  **本地版本号**（`pa.previewVersion` 只增，回包按 `版本号 + 询问 seq` 双重核对，过期一律
  丢弃）+ 3 s 墙钟超时（按失败处理）。每次选择变化（点牌 / 点座位 / 点选项 / 取消 /
  回第一步 / 点步骤 chip / 切转化模式 / 进清单）都排一次。
- 读回包的位置：`preactivationOptions` / `preactivationDisabled`（可点集合与置灰集）、
  `preactivationAdvanceAllowed`（「下一步」）、`preactivationSubmitAllowed`（「提交」与
  `buildPreativationAction`）、`preactivationShouldAutoAdvance`（**自动推进**：服务端
  `can_advance` + 它给的光标确实往前 + 这一步不需要显式「下一步」——判据全在服务端，
  前端只补交互形状）。**删掉的三处"全都点得动"兜底**：`table.js` 的
  `|| snapshot.players.filter(alive)`、`preactivation.js` 模态正文里的同一条座位兜底、以及
  `preactivationOptions` 返回 `null` 时三处 call-site 的放行（现在 `null` = 点不了）。
- **fail-closed**：失败 / 超时 / 409 ⇒ `pa.preview = null` + `pa.previewError = true` ⇒
  这一步**不自动推进、不许提交**、`#t-target-hint` 与 `#t-choice` 同读
  「取不到候选，请重试。」（两处同一份 `preactivationStepHint`，不各写一套）。

**C · 退役静态池（按 orchestrator 裁定 #乙）**

- **删**：`MANIFEST_POOL_LIMIT`（常量 + `flow.__all__`）、`manifest(..., limit=…)` 参数、
  `_prior_contexts` 的"超上限 ⇒ `None`"路径、`manifest` 里两处 `degraded` 分支（动态座位步的
  `min=0,max=1,pool=()` 退化档 + 座位池的 `except KeyError` 退化路）。合成器从此**永不退化**
  （spec §2 Q6）：清单永远下发它算得出来的候选。
- **保留**：`Step.wording` / `kind` / `min` / `max`（票面点名）+ `Step.pool` / `StepPool` /
  `Step.groups` / `Step.disabled` **字段与它们的生产者**（见下面的"刻意读法"第 5 条）。
  `src/sgs/state/protocol.py` 的两段 docstring 与 `开发文档/04` §W4-1、`开发文档/06` 的 W4-1
  账本行同步改成"没有退化档、逐步候选走预览端点"。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| 同源（预览的 `options` = `validate_activation` 的裁决） | `tests/test_web.py::test_the_preview_endpoint_returns_the_same_pool_the_validator_uses`（真会话 + 真 HTTP 处理器；【除疠】选 1 号位后 2 号位从候选消失，**逐个**把预览给的点扔给 `validate_activation` 复核，预览不给的那一个必须被拒） |
| 只读、不写记录 | `tests/test_web.py::test_the_preview_endpoint_writes_nothing`（`engine.records` 逐条不变、`pending is` 同一个且 `seq` 不变、每个座位的手牌数不变） |
| `answers_seq` 过期 ⇒ 409、与 `/answer` 同形 | `tests/test_web.py::test_the_preview_endpoint_rejects_a_stale_answers_seq`（`set(body) == {"error","pending"}`、`pending.seq` = 当前那一问） |
| `skill_id` 必须是这次询问下发过清单的技能 | `tests/test_web.py::test_the_preview_endpoint_rejects_a_skill_without_an_issued_manifest`（400） |
| `answers` 必须是清单步名的**前缀**（步名 / 顺序 / 长度 / 形状） | `tests/test_web.py::test_the_preview_endpoint_rejects_a_malformed_answers_prefix`（三种畸形载荷各 400） |
| 性能（贴单次耗时） | `tests/test_web.py::test_the_preview_endpoint_is_cheap_enough_for_every_selection_change`（200 次，中位 / p95 各钉一条 20 ms 的宽上限）+ 本文的实测数 |
| 烟测 `U-PV-01` | `tests/support/ui_smoke.mjs` `㉿U-PV-01a`（进清单就发预览，body = `answers_seq` + `skill_id` + 步名前缀）、`㉿U-PV-01b`（可点集合 = 回包 `options`，静态行是空的）、`㉿U-PV-01`（**除疠形状：选 1 号位后同势力的 2 号位立刻变灰**、3 号位仍亮）、`㉿U-PV-01c`（多选步停在原地，回包 `can_advance` 也不跳走）、`㉿U-PV-01d`（提交可点 = 回包 `can_submit`） |
| 烟测 `U-PV-02` | `㉿U-PV-02a`（对照组：成功时座位可点）、`㉿U-PV-02`（失败 ⇒ 光标不动 = **不自动推进**）、`㉿U-PV-02b`（不许提交 + 提示「取不到候选，请重试」）、`㉿U-PV-02c`（候选退回静态行，这一步没有 ⇒ 一个都不点） |
| 真端点（不是只测合成载荷） | `㉿R10a`（真局【仁德】的回包 `can_advance`）、`㉿R10b`（真局拼出复合动作）——两条都打**真的** `POST …/preview` |
| 永不退化 | `tests/test_skill_flow.py::test_manifest_never_degrades_however_many_prior_options_there_are`（33 张手牌 > 旧上限 32 ⇒ 仍然 33 行） |

### 单次预览耗时（本机）

`GameRegistry.preview` 直接调用（含 `answers_seq` / `skill_id` / 前缀核对与序列化），各 300 次：

| 场景 | 中位 | p95 | max |
|---|---|---|---|
| 【除疠】4 人局、`answers=[]` | 0.019 ms | 0.040 ms | 0.090 ms |
| 【除疠】4 人局、`answers=[seat 1]`（座位池带 `chosen`） | 0.025 ms | 0.051 ms | 0.108 ms |
| 【仁德】2 人局、`answers=[]`（选牌面 + 6 个候选） | 0.025 ms | 0.031 ms | 0.106 ms |
| 【仁德】2 人局、`answers=[card 65]` | 0.039 ms | 0.079 ms | 0.127 ms |

远低于票面 **< 5 ms** 的目标；原因是它只调这一步骤的派生回调（O(座位) / O(手牌)），
**不**建投影、不扫记录流（票 01 之前一次 `view()` 是 2.4 s）。HTTP 层另有 1 个 `json.dumps`
与一次路由（毫秒以下）。

### 刻意读法（评审请逐条看）

1. **`cursor` 的语义**：= **预览的那一步在清单里的下标**。`answers` 是清单步名的前缀，
   **最后一条可以是玩家正在挑的那一步**（选牌 / 提供 / `max > min` 的多选座位步要显式
   「下一步」，服务端的光标因此停在原地）。票面示例 `answers:[{seat,[3]}] → cursor:1` 描述
   的是"前缀已填完且该步是单值步"那一档：两种读法在**单值步**上逐个数字一致；多选步上必须
   停在原地，否则 `U-PV-01`（"同势力立刻变灰"）根本表达不出来——那是本票的中心症状。
2. **`can_advance` / `can_submit` / `next`**：`can_advance` = **连光标这一步也满足了**（可以
   离开这一步：这一步还空着就是 `false`）；`can_submit` = **整条清单**都满足（与光标无关，
   多选步停在最后一步上时也真）；`next` = 光标之后那一步的步骤键（`null` = 已是最后一步 ⇒
   必须点提交，spec §2 Q5）。**自动推进 = `can_advance` ∧ 服务端光标确实往前 ∧ 这一步不需要
   显式「下一步」**——全部落在服务端字段上，前端不算"填满没有"。
3. **答案值的两种写法**：请求的 `values` 座位接受 `3` 与 `"seat:3"`（清单候选是文本、记录流
   的 `choices` 也是文本，手写调用直接给座位号）；响应一律 **`StepPool` 口径**
   （座位 `seat:N`、选牌牌 id 文本、选项文本），前端只做字符串比较。
4. **能力声明键 `pending.preview`**：不加这个键就不发预览（不是"发了再失败"）。它是
   `exclude=True` 之外的**网页载荷**字段（不是 `Query` 模型字段），故 `_AI_READ_POINTS` /
   `_SCORERS` / 金录像都不受影响；合成载荷 / 转化清单 / 借刀清单没有它 ⇒ 旧行为逐字不变。
   这就是"新信息通道"的开关，也是本票能让 99% 的既有烟测一条不改的原因。
5. **Part C 的边界（刻意保留静态行）**：本票删的是**退化分支与"静态行 = 界面唯一候选"这条
   通路**（读取方已切到预览），**不是** `Step.pool` 字段本身。理由三条：
   ① `heroes._manifest_steps` 的 `Step.groups`（多来源步的合法来源组合，huo-mobile-batch 票 10）
   与 `card_preactivations`（借刀两步，W5-02b）**不在** `flow.py` 的退化分支里、也不在本票
   Touches（`src/sgs/skills/heroes.py` 不在列）；删它们会把两个已落地的契约打回"无判据"态，
   直到票 12 接手。
   ② 界【渐营】"目标由牌名决定 ⇒ 第 3 步自动跳过"（票 18）**只**由 `StepPool.max = 0` 表达；
   删行会让【桃】/【酒】卡在一步点不动的空步上（`preactivationNoChoiceStep` 没有第二个判据）。
   ③ 40 多条既有断言读这些行（`test_hero_active` / `jie_negation` / `jie_shu` / `jie_yijiang` /
   `lin` / `target_change` / `target_pool` / `transform`…），而票 12 本来就要把 `disabled` /
   `max` 重新定义成"询问时算一次"——行与断言应由它一并收口。
   故票面 §C 的"不再下发 `pool`/`groups`/`disabled`"按 **map.md 缺口 #乙** 落成"**`disabled`
   字段保留**（票 12 重定义语义）+ 退化档删除 + 读取方切到预览"，静态行留作快照 / 兼容载荷。
6. **fail-closed 的边界**：失败 / 超时 ⇒ 不推进、不提交、提示；但**已经下发过的静态行仍可点**
   ——那是服务端在同一份池回调上算出来的快照，不是"界面猜"（spec §6 #5 禁的是后者）。
   真载荷里票 11 之后【除疠】这类动态步**没有**静态行 ⇒ 预览失败时确实一个都不点。
7. **借刀的手牌清单**（`cardEntry`，`skill_id = null`）**不发预览**：端点按技能 id 取清单，
   没有流水线可现算；`schedulePreativationPreview` 显式跳过，行为与改前逐字一致。
8. **`issued_manifest` 的取法**与 `Engine._validate_structured`（复合动作成员判定）同源，
   避免"清单发过没有"与"动作收不收"各说一套。清单**不重渲**（重渲要枚举前序组合，正是本票
   要退役的开销）——预览读的就是玩家屏幕上那一份的 `Step.order` 映射。
9. **`preview` 的 stale 窗口**（去抖期 / 回包未到）：候选仍用**上一份服务端回包**（同一步），
   而"能不能进下一步 / 能不能提交"退回清单本地的形状判据（`preactivationStepFilled` 的
   `min/max` 计数、`preactivationReady`）——这条让"点完一张牌马上点下一步"不会卡 100 ms；
   **失败态**永远压过它（`previewError` ⇒ 全假）。
10. **`Step.wording` / `kind` 未改**：预览不回 `wording`（文案仍读清单），`kind` 用清单下发
    的那一格（`Step.kind` 是规则级事实，不按载荷形状反推）。

### AI 适配契约（`docs/agents/ai-adaptation.md` §1，六条逐条）

1. **询问面**：没有新增 `QueryKind`（复用既有九类）⇒ 不适用；`_SCORERS` 未动。
2. **动作面**：AI 自己构造的候选没变——预览是**网页只读通道**，AI 不调它，`legal_actions` /
   `preactivations` / `transform_preactivations` 的形状一字未改 ⇒ 不适用；`_AI_READ_POINTS` 未动。
3. **语义条目**：没有新增 / 改动技能 ⇒ 不适用。
4. **覆盖场景**：同上 ⇒ 不适用。
5. **信息通道**：**没有**改 `PlayerView` / `PlayerPublic` / 记录流；预览只回"应答者本人
   已经能看到的候选"（同一份池回调、同一份 `owner`），AI 的记牌守恒量（53 / 108 / 160）与
   身份推断的输入一个字节没变。`pending.preview` 是网页载荷字段，不进 `Query` 模型。
6. **回归**：AI 不读这条通道 ⇒ 决策 diff 为空，不适用；`tests/test_ai_adaptation.py` 逐字未改
   **且跑过**（248 passed 的那批里含它）。

### 会红的既有断言（本票刻意改写）

- `tests/test_skill_flow.py` 的"`limit=1` 退化用例"（票面 §会红第 3 条）⇒ **删掉重写**为
  `tests/test_skill_flow.py::test_manifest_never_degrades_however_many_prior_options_there_are`
  （33 张手牌 > 旧上限 32 ⇒ 仍然 33 行）。
- `tests/support/ui_smoke.mjs` `㉿PZ10–㉿PZ12`（把"退化后座位仍可点"钉成契约）⇒ 改写为
  `㉿PZ10`（仍识别为座位步）+ `㉿PZ11`（**一个都不点**）+ `㉿PZ12`（点不动、拼不出动作）。
- `tests/test_web.py::test_the_pending_payload_forwards_a_passive_activation_manifest` 加一条
  `payload["preview"] is True`（该技能有流水线 ⇒ 这一问声明预览能力）。
- **票面没预告的一条**：`tests/support/ui_smoke.mjs` 的 `㉿R10b`（真局【仁德】拼复合动作）。
  前端不再**同步**自动推进（光标跟着回包走），故该用例补了 `flushPreativationPreview()` 与
  新的 `㉿R10a`（真端点回包断言）。这不是行为回归：真浏览器里那 90 ms + 一个往返本来就要等。
- 票面写的 `tests/test_web.py:1665-1705`（"合成 Query 的清单转发"）在**当前 revision 已不是**
  那条用例（行号是旧的）；对应用例是 `…forwards_a_passive_activation_manifest`（见上）。

### 门禁

- `.venv/bin/pytest tests/test_web.py tests/test_skill_flow.py` → **360 passed**
- `.venv/bin/pytest tests/test_hero_jie_yijiang.py tests/test_web_static.py tests/test_ai_adaptation.py tests/test_action_contract.py` → **248 passed**
- `bash tests/smoke-web.sh` → **exit 0**（`--verbose`：1649 条 PASS，0 条 FAIL）
- `bash tools/check_goldens.sh` → **47 条标准 108 金录像逐字节不变**
- `bash tests/test_dev_docs.sh` → **全部通过**
- 全量门禁：随本次提交的 pre-commit hook（`tests/run_all.sh --quiet`）。**第一次提交被
  hook 拒了**——3903 passed / 1 failed，唯一那条是 `tests/test_dod_m4.py` 的"接入层不得深挖
  引擎"（`from sgs.skills.preactivation import …` 是三段）。改成 `from sgs.skills import …`
  之后重跑（见下方 `## Answer` 末尾的第二次提交）。

### 手工复现配方（`bsk` 在本环境不可用，故给配方）

1. `bash tools/serve-dev.sh start 8100`（或 `.venv/bin/python tools/sgs_web/serve.py --port 8100`）。
2. 开局页：4 人局、**己方点将「界华佗」**（群），其余随便（魏 / 蜀更直观）；
   进入对局、推进到自己的出牌阶段。
3. 点【除疠】技能按钮 → 步骤条出现「1. 选目标」。**点 1 号位**：同势力的另一个魏将立刻
   变灰、异势力仍亮、底部「确认发动」点亮（此时 `.scratch` 之外看不到任何前端规则判断——
   全是回包的 `options` / `can_submit`）。
4. fail-closed：把服务停掉（或断网）再点一次座位/取消 → 提示条与页脚同时出现
   「取不到候选，请重试。」、「确认发动」保持灰、光标不动。
5. 恢复服务 → 再动一次选择，一切恢复（下一次回包覆盖失败态）。

### 交接给下一票

- **票 08**：自动推进的判据已经在服务端（`can_advance` / `next` / `cursor`），前端唯一的
  落点是 `preactivationShouldAutoAdvance` 与 `preactivationAdvanceAllowed`；"最后一步必须点
  提交"= `next === null`（此时只亮「提交」，由 `can_submit` 决定）。别再在前端算填没填满。
- **票 12**：`disabled` 的语义扩展直接加在 `preview_step` 的 PickOption / PickCards 分支
  （服务端已有 `step.disabled` 与 `_resolve_options`，同一步内的已选值走 `answers` 的最后一格）；
  `MANIFEST_POOL_LIMIT` 已删、退化档已删，**静态池行可以放心在这里删**（连同上面第 5 条列的
  那批断言）——预览已经能服务每一步。
- **票 13**：座位多选步（连营 / 大雾 / 突袭 / 奋威 / 除疠）的可点集合已经在回包 `options` 里
  （含同一步内的 `chosen` 排除），前端 `preactivationOptions(pa, pa.cursor)` 直接读；
  `ownZoneSuitTaken`（票 09 的 stopgap）**未动**：本票没有让预览服务自家手牌步的"花色已占用"
  判据（业炎的花色上限是票 12 的加法）。
