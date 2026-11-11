# 07: 提示区单一 reset + 可编辑文案模板

**What to build:** ① 把"清询问面"收敛成一个与 `renderPending` 同源的 reset（提示文本 / 步骤提示 / 技能串 /
选项格 / 弹窗标题镜像 / 页脚），堵住四类残留；② 新增**可编辑的文案模板系统**：每个（技能, 步骤）一对
`ask` / `done` 模板，逐级累加拼出"弃置一张【桃♥7】，选择1名男性角色对【3号位陆逊】使用决斗"这种句子。

**Blocked by:** None
Status: open
Type: task
Touches: tools/sgs_web/static/pending.js, tools/sgs_web/static/modal.js, tools/sgs_web/static/answer.js, tools/sgs_web/static/session.js, tools/sgs_web/static/core.js, tools/sgs_web/static/table.js, tests/test_web_static.py, tests/support/ui_smoke.mjs
Ruling: spec §2 Q11 / Q18 / Q23（用户 2026-11-11 签）

## 根因（spec §1 #5）

清除点只有三处：`blankAskSurface`（`pending.js:70-99`，**唯一调用点** `session.js:778`，只在一批记录起播时）、
`renderPending` 头部（`pending.js:126/166/168`）、`closeDialogDom`（`modal.js:758-785`）。四类缺欠：

1. **只写不清**：`answer.js` 七处 `#t-choice` 没有寿命（含 `takeover()` 的"已请服务端判定一次…"，
   它写在 `refresh()` 之后，只能等下一次 `renderPending` 被冲掉）；`core.js:364-367` 同。
2. **清的是另一个元素**：`syncDialogHint`（`modal.js:695-704`）把 `#t-target-hint` 抄进弹窗标题条
   `#t-dialog-hint`；`blankAskSurface` 只清源、不通知镜像，且 `keepDialog`（亮牌 / 涉猎结果态，
   `pending.js:88-98`）时窗不关 ⇒ 上一问的步骤提示留在弹窗标题条。
3. **pending 消失后没复位**：终局分支 `session.js:1488-1501` 在 `renderPending`（`:1502`）**之前 return**
   ⇒ `#t-skill-hints` / `#t-choice` 停在上一条文案；`#t-context` 全项目**无清除点**。
4. **空转不重绘**：`pollIsIdle`（`session.js:1576-1582`）只看 cursor 与 `pending.seq`，`applyIdlePoll` 只同步时钟
   ⇒ 任何"无新记录的状态变化"永远不会重绘（与票 10 同源）。

## 要做什么

### A. 单一 reset（结构）

- 新增 `resetAskSurface()`（命名由实现定）：清 `#t-prompt` / `#t-target-hint` / `#t-dialog-hint`（镜像）/
  `#t-skill-hints` / `#t-choice` / `#t-options` / `#t-prompt-skills`，并复位 `#t-context` 的归属说明。
- **三个调用点全部走它**：`blankAskSurface`（起播）、终局分支（把 `return` 提前的那段改成"先 reset 再 return"）、
  每次 `renderPending` 之前。
- `#t-choice` 的提示文本给**显式寿命**（写的时候带一个序号/时间戳，下一次 reset 或超时清掉），
  不许再靠"下一次 renderPending"兜。
- 新增"空转期快照变了就重绘座位块"的出口（`pollIsIdle` 增加"快照指纹变了"这一路；与票 10 共用）。

### B. 文案模板（用户要的"逐级累加"）

- 数据形状：`prompts.json`（新文件，与 `anim.ini` 同姿态：页面启动时异步载入，**缺键逐级回退**）：

  ```
  {
    "hero.diaochan.lijian": {
      "steps": {
        "card":   { "ask": "选择1张牌弃置",                      "done": "弃置一张【{card}】" },
        "user":   { "ask": "选择1名男性角色成为决斗使用者",        "done": "选择【{seat}】成为决斗使用者" },
        "victim": { "ask": "选择1名男性角色成为决斗目标",          "done": "对【{seat}】使用决斗" }
      }
    },
    "window:dodge": { "ask": "是否打出【闪】" }
  }
  ```

- 拼法：`提示文本 = Σ(done[i] for i < cursor) + ask[cursor]`，用「，」连接。
  例（用户原话）：`选择1张牌弃置` → `弃置一张【桃♥7】，选择1名男性角色成为决斗目标` →
  `弃置一张【桃♥7】，选择1名男性角色对【3号位陆逊】使用决斗`。
- 占位符白名单（**只这些**，写错即回退）：`{card}`（牌面名+花色点数，如 `桃♥7`）、`{seat}`（`3号位陆逊`）、
  `{name}`、`{n}`、`{suit}`、`{rank}`、`{hero}`。
- 三层回退：`prompts.json` → 服务端 `Step.wording` → 前端通用文案（`选择{n}张牌` / `选择{n}名角色`）。
- **编辑入口**：游戏内"改这段文案"（只改当前询问 / 当前步骤）→ 写 `localStorage['sgs.prompts']` 覆盖层，
  并提供"导出 JSON"方便贴回仓里。覆盖层与文件层按"键级覆盖"合并。
- **硬约束**：文案只做**显示覆盖**，**绝不**写进 `Query.prompt`——它在记录流里（`QueryRecord` 内联整份 Query），
  改了会打翻全部基准。
- **默认值初稿**：由 63 处 `step_requirements` + 34 条 `SkillFlow` 的步骤名反推；本票先出初稿文件，
  用户改字（问一次会话级确认，不阻塞实现）。

## 验收

- 新增（`tests/test_web_static.py`）：
  - `test_the_ask_surface_is_cleared_by_one_single_function`（grep：只剩一个清除实现，三个调用点都在）。
  - `test_the_prompt_templates_are_loaded_with_a_documented_fallback_chain`。
  - `test_a_template_slot_whitelist_rejects_unknown_placeholders`。
- 新增（烟测 `ui_smoke.mjs`）：
  - 一条"上一问的步骤提示不残留在弹窗标题条"（`keepDialog` 分支）。
  - 一条"终局后提示区三件套全空"。
  - 一条"离间三级的累加文案逐字等于用户给的三句"。
- `## Answer` 里贴 `prompts.json` 初稿路径与"待用户改字"的清单。

## 会红的既有断言

- `ui_smoke.mjs:2905`（`#t-prompt` 含"预发动【渐营】"）——票 08 会改这句，本票先不动它。
- `tests/test_web_static.py` 里对 `#t-choice` 写入点的静态断言（若有）。
