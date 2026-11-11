# 11: 步骤预览端点——每一步的可点集合都由后端现算

**What to build:** 新增一个**只读、不写记录**的"步骤预览"端点：预发动每变一次选择就问一次后端，
后端用**与校验器同一个** `pool(chosen)` 现算"此刻这一步还能点谁 / 能不能进下一步 / 能不能提交"。
这样前端的合法性永远等于后端，且 `MANIFEST_POOL_LIMIT` 退化为不需要。

**Blocked by:** 01（`view()` 的 O(N) 必须先拆掉，否则每次预览都贵）
Status: open
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
