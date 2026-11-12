# 15: 自家手牌选牌的合法性也由后端现算（业炎花色收口；`spec §2 Q6` 的最后一条）

**What to build:** 把 `SELECT_CARDS`（自家手牌面）的**局部合法性**从"前端第二份真源"改成
"后端现算、前端只读"，兑现 `spec §2 Q6` 的"业炎花色…由后端算 ⇒ 走 Q10 的预览请求"，
并删掉票 09 的止损判据。这是 `/code-review` Spec 轴在收口时点出的缺口：票 09 落了止损、
票 11/12 按各自票面没接手，`Q6` 的业炎那一半至今没有实现。

**Blocked by:** None（票 09 / 11 / 12 均已 resolved）
Status: resolved
Type: task
Touches: tools/sgs_web/serve.py, tools/sgs_web/static/candidates.js, tools/sgs_web/static/card-face.js, tools/sgs_web/static/options.js, src/sgs/engine/game.py, tests/test_web.py, tests/test_web_static.py, tests/support/ui_smoke.mjs
Ruling: spec §2 Q6 / Q10 + §6 不变量 1；票 09 的止损注释与 `test_web_static.py::test_the_own_zone_suit_cap_is_one_shared_predicate_reading_the_payload`；票 11 的预览端点纪律

## 根因（review finding，已核到代码）

【业炎】的代价面是 `SelectionSpec(zones=(HAND,), total=(4,4), max_per_suit=1)`
（`src/sgs/skills/active.py:4813` 一带），**不是**预发动清单步 ⇒ 票 11 的
`POST /preview` 服务不了它（`serve.py` 要求 `skill_id ∈ issued_manifest(pending, …)`）。
于是票 09 落的止损判据今天仍在**前端**数花色：
`static/candidates.js:ownZoneSuitTaken` + `static/card-face.js:cardFace` +
`static/options.js:toggleSlotPick`，它的注释自认"前端实现一遍规则的第二份真源"。
违反 `spec §6` 不变量 1（前端不许自己推导规则合法性）与 Q6（业炎花色由后端算）。

## 要做什么

1. **预览端点扩一档**：`POST /api/sessions/<id>/preview` 在 pending 是
   `SELECT_CARDS`（且询问者 = 提交者）时接受
   `{ "answers_seq": <pending.seq>, "picks": ["slot:3", …] }`（`skill_id` 可省），
   回 `{ "disabled": ["slot:5", …], "can_submit": bool, "can_advance": bool }`。
   `disabled` 的判据 = **引擎自己**在提交时用的那条判据。注意它住在**候选生成**里而不是
   提交校验里：`SelectionSpec.max_per_suit` 只枚举"每种花色至多 k 张"的组合
   （`src/sgs/effects/actions.py:1386-1405`），提交是 fail-closed 的成员判定
   ⇒ 预览要复用**同一个枚举器**回答"给定当前 picks，哪些槽位还出现在任一合法补全里"
   （禁用集 = 不在任何含当前 picks 的合法组合里的槽位），不许另写一份花色计数。
   - 校验姿态照票 11：`answers_seq` 不等于当前 pending 的 seq ⇒ 409；
     畸形 picks ⇒ 400；**只读**：不建询问、不写记录、不耗序号、不搬牌、不抽随机数；
     **不碰** `view()` / 投影 / 快照。
   - 票 11 的清单预览（预发动）行为与回包字段逐字不变。
2. **前端改读回包**：own-zone 直选判据（`isOwnZonePick` / 选牌点击）不再自己数花色，
   改读预览回包的 `disabled`（去抖 80–120 ms + 版本号丢弃过期回包，与票 11 的预发动同一姿态）。
   **fail-closed**：取不到回包 ⇒ 该步**一个都点不动**，并提示重试（不许回退成"全都点得动"）。
3. **退役止损**：删掉 `candidates.js:ownZoneSuitTaken` 及其在 `card-face.js` / `options.js`
   的读点；`test_web_static.py::test_the_own_zone_suit_cap_is_one_shared_predicate_reading_the_payload`
   改写为"读的是预览回包的 `disabled`，前端零花色推导"。
4. **`## Answer` 里写**：单次选牌预览耗时（目标 < 5 ms）、同源断言的判据、退役了哪几个函数。

## 验收

- 新增（`tests/test_web.py`）：
  - `test_the_pick_preview_disables_the_slots_the_engine_would_reject`
    （同源：把预览的 `disabled` 与"真提交那条答案会被拒"逐项对齐——业炎四花色场景）。
  - `test_the_pick_preview_writes_nothing`（前后记录长度 / `pending.seq` 不变）。
  - `test_the_pick_preview_rejects_a_stale_answers_seq`（409）。
- 改写（烟测）：`U-YS-01` 改为"点第 1 张红桃后其余红桃由**回包**置灰"；
  新增 `U-YS-02`：预览失败 ⇒ 这一步一个都点不动（fail-closed）。
- 静态断言：前端源码里不再有"按花色计数"的判据（grep 型）。
- 金录像：`bash tools/check_goldens.sh` 47 条逐字节不变（本票只改读法，不改记录流）。

## 会红的既有断言

- `tests/test_web_static.py` 里钉 `ownZoneSuitTaken` 的那条（本票改写）。
- `tests/support/ui_smoke.mjs` 的 `U-YS-01a..e`（改为读回包）。

## Answer

### 落了什么

**A · 引擎侧：一个枚举器，两个消费者（不新写规则）**

- `src/sgs/effects/actions.py`：把 `_select_cards` 里的组合枚举原样抽成公开的
  `select_card_picks(slots, spec)`（顺序 / `allowed()` 的 `max_per_suit` 收窄一字未改，
  `_select_cards` 改为逐条装 `SelectCards`），新增 `pick_completions(slots, spec, picks)`
  = **含已选这几张**的全部合法组合。两者都是纯函数，`sgs/effects/__init__.py` 再导出
  （接入层只走 `sgs.effects` 这条公开面，M4 的深导入纪律照旧）。
- `tools/sgs_web/serve.py`：`GameRegistry.preview` 变成**两档分派**（`body` 带 `picks`
  ⇒ 选牌档，否则照旧走清单档）；清单档原样抽成 `_manifest_preview`，字段 / 行为**逐字不变**。
  选牌档 `_pick_preview` 的判据只有一行规则味道：`pick_completions`；`disabled` = 不在任何
  含当前 picks 的补全里的槽位，`can_submit` = 当前 picks 本身是一条合法答案，
  `can_advance` = 还补得下去。`_pick_slots` 只校验形状（数组 / `"slot:N"` 或整数 / 不越界 /
  不重复 / 不收 bool·float），非法一律 400；`answers_seq` 对不上仍是 409（与 `/answer` 同形）。
  只读全停：不建询问、不写记录、不耗序号、不搬牌、不抽随机数、不碰 `view()` / 投影 / 快照
  （只 `session.touch()` 打一次会话回收用的访问戳，与票 11 同一行）。
- `tools/sgs_web/projection.py`：`pending_payload` 给 `SELECT_CARDS` ∧ 查看者=应答者的载荷加
  **`pick_preview: true`**（新信息通道的能力声明，与票 11 的 `preview` / 既有 `structural` /
  `default_choice` 同一姿态）：**键的缺席是保证**——合成载荷 / 旧载荷逐字段逐行为不变。
  它是网页载荷字段、不是 `Query` 模型字段 ⇒ 不进记录流。

**B · 前端：置灰只读回包（退役止损）**

- `tools/sgs_web/static/candidates.js`：删 `ownZoneSuitTaken`（票 09 的止损），新增选牌预览
  客户端：`ownPickPreviewSupported` / `ownPickPreviewEnabled`（多张的自己区域面 + 有会话 +
  非回放）/ `ownPickPreviewSchedule`（**去抖 90 ms** + **版本号只增** + 同一问同一组 picks
  只排一次）/ `requestOwnPickPreview`（3 s 墙钟超时 + 回包按 `version` 与 `seq` 双重核对）/
  `ownPickSlotDisabled`（**唯一**判据）/ `ownPickSubmitAllowed`（`can_submit`）/
  `ownPickPreviewIssue`（失败与死路的话术）/ `ownPickGreyNote`。失败后 2 s 重问（轮询的渲染
  自然会再排一次）。
- 读点：`card-face.js:cardFace`（手牌置灰）、`options.js:toggleSlotPick`（拒点，置灰不只在
  样式上）、`options.js:renderOwnZonePick`（排预览 + 提示）、`pending.js:refreshSubmit`
  （自家区域面的「提交」读 `can_submit`；`#t-choice` 与 `#t-target-hint` 同读一句异常话术）。
  `core.js` 的 `state` 里新增 `ownPickPreview` 本地态（同 `state.preactivation.preview*` 的姿态）。
- **fail-closed**：没有当前这一问的回包（还没回来 / 失败 / 超时）⇒ 该步**一个都点不动**，
  两处提示都读「取不到候选，请重试」；已选的槽位恒可点（再点 = 取消），故死路永远退得出来。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| 同源：预览 `disabled` 与"真提交会被拒"逐项对齐（业炎四花色） | `tests/test_web.py::test_the_pick_preview_disables_the_slots_the_engine_would_reject`（真会话 + 真 HTTP 处理器；业炎代价面 6 张 ♣♥♥♣♦♠：`disabled=[]` → 点♣后 `["slot:3"]` → 再点♥后 `["slot:2","slot:3"]`；把每个 `disabled` 槽位塞进一条满额组合真提交 ⇒ `IllegalActionError`，四色齐那条真提交 ⇒ 引擎收下。同一条用例里钉 `pending_payload(...)["pick_preview"] is True`） |
| 只读、不写记录 | `tests/test_web.py::test_the_pick_preview_writes_nothing`（`engine.records` 逐条不变、`pending is` 同一个且 `seq` 不变、每座位手牌数不变、牌堆顺序不变） |
| `answers_seq` 过期 ⇒ 409、与 `/answer` 同形 | `tests/test_web.py::test_the_pick_preview_rejects_a_stale_answers_seq`（`set(body) == {"error","pending"}`、`pending.seq` = 当前那一问、`"过期"` 在 error 里） |
| 畸形 picks ⇒ 400；不是选牌面 / 不是本人的面 ⇒ 400 | `tests/test_web.py::test_the_pick_preview_rejects_malformed_picks_and_the_wrong_face`（非数组 / 越界 / 重复 / bool / float / `slot:x` / 负数七种；另加"出牌阶段的询问带 picks ⇒ 400"） |
| 性能（贴单次耗时） | `tests/test_web.py::test_the_pick_preview_is_cheap_enough_for_every_pick`（200 次，中位 / p95 各钉 20 ms 宽上限）+ 下面的实测表 |
| 烟测：`U-YS-01` 改为"点第 1 张红桃后其余红桃由**回包**置灰" | `tests/support/ui_smoke.mjs` `U-YS-01a`（面形状 + **回包未到一个都点不动** + 提示「取不到候选，请重试」）、`b`（回包 `disabled=[]` ⇒ 五张全亮）、`c`（点第 1 张红桃 + 回包 ⇒ 另一张红桃置灰、其余三色照常）、`d`（点置灰的那张不收下）、`e`（四色齐 ⇒ 提交可点、载荷 = 候选那条升序 `SelectCards`）、`f`（取消 ⇒ 回包解除置灰、提交又灰） |
| 烟测：新增 `U-YS-02`（预览失败 ⇒ 一个都点不动） | `U-YS-02a`（失败 ⇒ 全灰 + `state.ownPickPreview.error`）、`b`（`#t-target-hint` 与 `#t-choice` **同读**「取不到候选，请重试」）、`c`（点灰着的牌不收下）、`d`（恢复后重问 ⇒ 可点集合 = 回包的 `disabled` 之外） |
| 静态：前端不再有"按花色计数"的判据（grep 型） | `tests/test_web_static.py::test_the_own_zone_suit_cap_reads_the_pick_preview_instead_of_counting_suits`（`ownZoneSuitTaken` / `max_per_suit` / `suitSlotState` 在 `candidates.js` / `card-face.js` / `options.js` 里**一处都不剩**；唯一判据读 `ownPickPreview.disabled`；两个消费者共用；去抖 / 版本号 / 超时 / 重问常量在；通道读 `pick_preview === true` 且只服务多张面） |
| 金录像 47 条逐字节不变 | `bash tools/check_goldens.sh` → **PASS: 47 条标准 108 金录像逐字节不变**（`select_card_picks` 的枚举顺序与原 `_select_cards` 逐字相同 ⇒ 记录流里的候选一字未改） |

### 单次选牌预览耗时（本机）

`GameRegistry.preview` 直接调用（含 `answers_seq` / 形状核对与序列化），业炎代价面（6 槽、
`C(6,4)=15` 次枚举），各 300 次：

| 场景 | 中位 | p95 | max |
|---|---|---|---|
| `picks=[]` | 0.012 ms | 0.017 ms | 0.050 ms |
| `picks=[0,1]` | 0.013 ms | 0.028 ms | 0.199 ms |
| `picks=[0,1,4,5]`（满额） | 0.012 ms | 0.028 ms | 0.078 ms |

枚举器本体 `select_card_picks(6 槽)` ≈ 0.009 ms/次。远低于票面 **< 5 ms** 的目标：它只枚举
**这一面**的组合，不建投影、不扫记录流（票 01 之前一次 `view()` 是 2.4 s）。

### 退役的函数（票 09 的止损，一处不剩）

- `candidates.js:ownZoneSuitTaken`（连注释里的退役条款一起删）；
- 它的两个读点改写：`card-face.js:cardFace` 的 `taken`、`options.js:toggleSlotPick` 的拒点；
- `options.js:renderOwnZonePick` 里那句读 `spec.max_per_suit` 的文案（`每种花色至多 k 张…`）
  一并删掉——文案只说"灰是什么意思"，不再复述规则；
- `tests/test_web_static.py` 里钉它的那条静态断言（整条改写，见上表）；
- `tests/support/ui_bridge.js` 的桥名同步（`ownZoneSuitTaken` → 新的 `ownPickPreview*` 四口）。

### 刻意读法（评审请逐条看）

1. **`disabled` 不含已选的槽位**。再点一下是取消：把已选的锁死会让玩家退不出这一组（服务端
   自己的候选集也从不排除"再点一次"）。**这条同时是死路的出口**——回包说"补不出来"时，
   除已选之外全灰，玩家仍能退一张、退完立刻重问。
2. **`can_advance` 的口径**：票面只给了字段名，这里读作"**还补得下去**"（当前 picks 至少是
   某条合法补全的前缀）；`can_submit` 才是"这一组**本身**是合法答案"（= 提交会被接受）。
   `can_submit ⇒ can_advance`；两者都假 = 死路，前端据此说「这一组凑不出合法组合，点已选的
   牌取消一张」。
3. **fail-closed 的边界**：票面原话是"取不到回包 ⇒ 一个都点不动 + 提示重试"，故**"还没回来"
   与"失败"是同一个态**（同一个函数分支），回包未到的 ≈90 ms + 一个往返里提示也读
   「取不到候选，请重试」。**提交不因失败而锁死**：锁的是"点不动"那一半——picks 若已经是
   候选里那条（`answer.js:buildAction` 的成员判定，服务端下发的 `legal_actions`），提交仍会
   被接受；锁死它只会让玩家在没有可点项时连出口都没有。失败后 2 s 重问（真机上由 2 s 轮询
   的渲染排下一次）。
4. **同问内沿用上一份回包**（票 11 的 stale 窗口姿态）：点完牌到新回包之间约一百毫秒里，旧
   `disabled` 仍然生效。两个方向都安全：已选只增 ⇒ 补全集合只减 ⇒ 旧集合只少不多（新回包只会
   再多灰几张）；已选减少 ⇒ 旧集合可能多灰一张 ⇒ 那一张晚一个往返才亮（仍是 fail-closed 方向）。
5. **能力声明的粒度**：`pick_preview` 只服务**多张**的自己区域面。单张面（火攻 / 铁骑 / 雌雄
   弃牌…）不问：它的候选恒是"每一槽一张"，按同一个枚举器算 `disabled` 必为空集——问一次只会
   白加一个往返和一段全灰窗口。**它不是"前端猜"**：这一档的判断是"没有信息可问"，不是"能不能点"。
6. **`slot:N` 文本口径**：请求接受 `3` 与 `"slot:3"`（与票 11 的座位 `3` / `"seat:3"` 同姿态），
   回包一律 `slot:N`（与 `StepPool` 的文本候选同口径）；`picks` 按**集合**处理（服务端升序
   规范化后比对，选牌语义不关心点击先后）。
7. **`Touches` 的正式偏离**（票面 `Touches` 之外 / 之内未改的，一并登记）：
   - **加**：`src/sgs/effects/__init__.py`（再导出两个纯函数）、`tools/sgs_web/projection.py`
     （`pick_preview` 能力键）、`tools/sgs_web/static/core.js`（本地态）、
     `tools/sgs_web/static/pending.js`（「提交」读 `can_submit` + 页脚同读异常话术）、
     `tests/support/ui_bridge.js`、`docs/agents/frontend-map.md`、`开发文档/04-AI与交互设计.md`。
   - **未改**：`src/sgs/engine/game.py` 与 `tools/sgs_web/session.py`。选牌档只需要
     `session.pending`（`kind` / `actor` / `slots` / `spec`）与 `session.human_seat`——加一个
     `Engine` 访问器只会为"一处纯函数"多绕一层；枚举器落在规则层（`sgs.effects`）也与
     "规则住引擎、接入层只读"的方向一致（票 11 的 `Engine.activation_frame` 是**帧上下文**，
     这里要的是**询问自己的面**，两者不是同一件事）。
8. **票 11 的清单预览逐字不变**：`_manifest_preview` 是被原样搬走的整段（`answers` / `skill_id`
   核对、`KeyError ⇒ 400`、回包七字段全在）；分派键是 `body` 里**有没有 `picks`**——清单预览
   从不带它，选牌预览不带 `skill_id`（混发 ⇒ 400，免得两种"缺键"互相掩盖）。
9. **`max_per_suit` 现在只活在后端**：前端仍读它的只剩**两个别的面**——`reveal.js`（涉猎 /
   两行观看手牌的花色面）与 `modal.js`（`stepDialogKind` 用它把"两行观看手牌"从亮牌窗里分出来）。
   两者都不是本票的自家手牌直选面，故不在退役面里；本票的静态断言把**三个自家区域文件**钉成 0 次。

### AI 适配契约（`docs/agents/ai-adaptation.md` §1，六条逐条）

1. **询问面**：没有新增 `QueryKind` / 询问字段 ⇒ 不适用；`_SCORERS` 未动。
2. **动作面**：`legal_actions` / 候选生成**逐字节未变**（枚举器只是被抽成函数，顺序相同——
   金录像 47 条为证）；预览是**网页只读通道**，AI 不调它 ⇒ 不适用；`_AI_READ_POINTS` 未动。
3. **语义条目 / 4. 覆盖场景**：没有新增或改动技能 ⇒ 不适用。
4. **信息通道**：**没有**改 `PlayerView` / `PlayerPublic` / 记录流；预览只回"应答者本人这一步
   还能点哪些**槽位下标**"（比界面已有的 `slots` 更少的信息），`pick_preview` 是网页载荷字段、
   不进 `Query` 模型 ⇒ AI 的记牌守恒量（53 / 108 / 160）与身份推断输入一个字节没变。
5. **回归**：AI 不读这条通道 ⇒ 决策 diff 为空；`tests/test_ai_adaptation.py` 逐字未改**且跑过**。

### 会红的既有断言（本票刻意改写）

1. `tests/test_web_static.py` 里钉票 09 止损的那条静态断言 ⇒ 现名
   `::test_the_own_zone_suit_cap_reads_the_pick_preview_instead_of_counting_suits`
   （同一个位置，判据从"前端数花色"翻成"读回包 + 零推导 + fail-closed"）。
   旧名 `test_the_own_zone_suit_cap_is_one_shared_predicate_reading_the_payload` 已**不存在**（本票改名）。
2. `tests/support/ui_smoke.mjs` 的 `U-YS-01a..e` ⇒ 改写为 `U-YS-01a..f`（每一步都先冲预览
   回包再断言；`a` 从"五张初始都可点"翻成"回包未到一个都点不动"，那是票面 fail-closed 的原文）。
   另新增 `U-YS-02a..d`。
3. `tests/support/ui_bridge.js` 的桥名清单（`ownZoneSuitTaken` 删除、`ownPickPreview*` 增加）。

### 门禁结果

- `.venv/bin/pytest -q tests/test_web.py` → **323 passed**（含新增 5 条）。
- `.venv/bin/pytest -q tests/test_web.py tests/test_web_static.py tests/test_skill_flow.py
  tests/test_queries_views_actions.py tests/test_action_contract.py tests/test_ai_adaptation.py
  tests/test_dod_m4.py tests/test_hero_huo.py` → **701 passed**。
- `bash tests/smoke-web.sh` → **全部通过**（`--verbose`：**1695 条 PASS、0 条 FAIL**；
  `PASS: 前端渲染冒烟`），含改写的 `U-YS-01a..f` 与新增的 `U-YS-02a..d`（逐条读了输出）。
- `bash tests/test_web.sh`（整个 Web 端到端门禁：静态资源、409、`node --check`、烟测、三条
  固定 tape、酒 tape、M6 tape、跨机可达）→ **PASS: M4 端到端门禁全部通过**。
- `bash tools/check_goldens.sh` → **PASS: 47 条标准 108 金录像逐字节不变**。
- `bash tests/test_agent_skills.sh` / `bash tests/test_dev_docs.sh` → **全部通过**（改了
  `docs/agents/frontend-map.md` 与 `开发文档/04`）。
- 全量门禁：随本次提交的 pre-commit hook（`tests/run_all.sh --quiet`）——见提交信息末尾的状态行。
- `bsk`：**未驱动**（本环境 `browser_interact` 的 cooperate 握手失败，与票 07 / 09 / 11 同一
  实测结论），交互那半边由 `bash tests/smoke-web.sh`（同一批 DOM 行为的自动半边）+ 下面的配方兜。

### 手工复现配方（实机）

1. `bash tools/check_env.sh` → PASS；`bash tools/serve-dev.sh start 8137`（URL 以它打印的为准）。
2. 开局：4 人、时限 0、己方点将**神周瑜（移动版）**，进入对局打到自己的出牌阶段。
3. 攒到**四花色各一张**手牌（打一两回合 / 用牌）。在浏览器 DevTools 的 Network 里保持
   `/preview` 可见。
4. 发动【业炎】→ 分配弹窗里给某个座位计 **2 点** → 确定 → 代价选牌面出现：**先看到一个极短的
   「取不到候选，请重试」+ 全灰**（去抖 90 ms + 一个往返），随后五张手牌亮起；
   `/preview` 的请求体是 `{"answers_seq": N, "picks": []}`（**没有** `skill_id`）。
5. 点第 1 张红桃 ⇒ 约 100 ms 后另一张红桃置灰（请求体 `picks:[0]`，回包 `disabled:["slot:1"]`）；
   点置灰那张点不动；凑齐四色后「提交」才亮；再点已选的红桃 ⇒ 置灰立刻解除。
6. fail-closed：Network 面板切 Offline 后**取消一张**已选的牌 ⇒ 整面全灰、标题条与页脚同时
   「取不到候选，请重试」、点不动；恢复网络后等一次轮询（≤2 s）⇒ 自动恢复可点。
7. 对照（零回归）：【火攻】亮牌步 / 【雌雄双股剑】弃牌步这些**单张**自家手牌面**不**发
   `/preview`（点一张即提交），行为与票 09 逐字相同。

### 给后来的票 / orchestrator

- **`spec §2 Q6` 的业炎半 + `§6` 不变量 1** 自本票起真的成立：`SelectionSpec.max_per_suit`
  只剩后端两个消费者（候选生成 + 选牌预览），前端零花色推导。acceptance-map 里票 09 那条
  "止损（退役条件写在判据旁边）"应改为"**已退役**（票 15）"，`/preview` 端点的覆盖范围从
  "预发动清单步"改为"清单步 + `SELECT_CARDS` 选牌面"。
- 下一张要"某一步每次变化都由后端算"的票（例如座位面的动态置灰扩展）照抄两个入口：
  **能力键在 `projection.pending_payload`**、**判据复用引擎那个枚举器 / 池回调**；前端只读、
  失败 fail-closed。别在前端再写第二份谓词。
- `U-YS-02` 的恢复断言依赖 `state.ownPickPreview.retryAt` 清零来"模拟下一次轮询渲染"（垫片
  不控真实时钟）；以后加"失败自动重试"这类用例照这个姿势，别去等的真 2 s。
