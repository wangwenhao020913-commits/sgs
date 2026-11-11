# 07: 提示区单一 reset + 可编辑文案模板

**What to build:** ① 把"清询问面"收敛成一个与 `renderPending` 同源的 reset（提示文本 / 步骤提示 / 技能串 /
选项格 / 弹窗标题镜像 / 页脚），堵住四类残留；② 新增**可编辑的文案模板系统**：每个（技能, 步骤）一对
`ask` / `done` 模板，逐级累加拼出"弃置一张【桃♥7】，选择1名男性角色对【3号位陆逊】使用决斗"这种句子。

**Blocked by:** None
Status: resolved
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

## Answer

### 落了什么

**A. 单一 reset（结构）**

- `pending.js` 新增 `resetAskSurface({keepOptions})` —— 清询问面的**唯一**实现：`#t-prompt` /
  `#t-target-hint` / `#t-dialog-hint`（镜像，走 `modal.js:resetDialogHint`）/ `#t-skill-hints` /
  `#t-choice` / `#t-options` / `#t-prompt-skills`，并复位 `#t-context` 的归属说明。
  **三个调用点**：`blankAskSurface`（起播）、`renderPending` **头部**（每次重绘之前）、
  `session.js:commitView` 的终局分支（先 reset 再 `return`）。
- `#t-choice` 的**显式寿命**：唯一写入口 `pending.js:setAskChoice(text, ttlMs)`（每次写入带序号，
  新写入 / reset 作废在飞的超时；判定类文案 8s 兜底清空），唯一清除点 `resetAskChoice`。
  `answer.js` 的七处与 `core.js:showPageNotice` 的牌桌那一格全部改走它——`$("t-choice").textContent =`
  在四份文件里一处不剩（静态断言钉住）。
- **空转重绘出口**（与票 10 共用）：`session.js:snapshotFingerprint(snapshot)`（`players[]` 的可见字段
  + 三组牌的一行摘要）+ `applyIdleRepaint(body)`；`poll()` 的空转分支里**指纹变了**就重绘座位块
  （走 `renderTable()`，**不清本地选择**），指纹没变才走原来的 `applyIdlePoll`（只同步时钟）。

**B. 文案模板**

- 新文件 `tools/sgs_web/static/prompts.js`：`prompts.json` 与 `anim.ini` 同姿态（页面启动即
  `fetch("prompts.json")`，相对路径、静态资源、无服务端路由），键族 `<skill_id>` / `window:<cause>`，
  文件层 ⊕ `localStorage['sgs.prompts']` 覆盖层**键级**合并（同一步逐字段覆盖，覆盖层保持稀疏）。
- 拼法 `promptComposedText` = `Σ(done[i] for i < 光标) + ask[光标]`，用「，」连接、空段跳过；
  `done` 的槽取**本步**答案，`ask` 的槽取**前一步最近一次已答**（界【离间】第 3 步的 `{seat}` = 第 2 步选的目标）。
- 三层回退的**唯一**实现 `promptQuestionForStep`：模板层 → `Step.wording` → 前端通用文案
  （`选择{n}张牌` / `选择{n}名角色` / `选一项`）。消费点两处：`preactivation.js:preactivationStepHint`
  （步骤提示 + 页脚同源，保留"第 k/N 步（…）"的进度/计数外壳）与 `pending.js` 末尾的
  `promptApplyWindowAsk`（`window:<cause>` **只填空**）。
- 占位符白名单**逐字**七个；白名单外 / 值取不到 / 空模板 ⇒ **整条作废**、落到下一层（不印 `{card}`）。
- 游戏内编辑（最小面，不新开屏幕）：提示条动作行里两个 ghost 按钮 `#btn-prompt-edit`（只改**当前步骤的
  ask**）/ `#btn-prompt-export`（覆盖层非空才出现）；写 `localStorage['sgs.prompts']`、导出可贴回仓里的 JSON。
- **硬约束**：只做显示覆盖，回写面为零——`bash tools/check_goldens.sh` 47 条逐字节不变。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| 只剩一个清除实现 + 三个调用点 | `test_web_static.py::test_the_ask_surface_is_cleared_by_one_single_function`（`function resetAskSurface(` 恰好 1 份；`blankAskSurface` / `renderPending` / 终局分支各 1 处；终局那处早于 `return;`；`#t-choice` 直写归零 + `setAskChoice` 带序号与 `setTimeout`） |
| 模板载入 + 三层回退 | `test_the_prompt_templates_are_loaded_with_a_documented_fallback_chain`（真 `core.js`+`card-view.js`+`preactivation.js`+`prompts.js` 的 node 探针：文件层载入、空表时模板层让位 → `Step.wording` → `选择2张牌`/`选择1名角色`/`选一项`；覆盖层键级叠加/保存/导出/清除；离间三级逐字） |
| 白名单拒绝未知占位符 | `test_a_template_slot_whitelist_rejects_unknown_placeholders`（白名单逐字 7 项；`{who}` 整条回退；`{card}` 无值也回退；七槽填充逐字） |
| 初稿键/步骤名不是手抖 | `test_the_prompt_template_keys_are_real_skill_ids_and_real_step_names`（对 `sgs.skills.catalog.CATALOG` 核：技能 id 存在、步骤名在 `step_requirements` ∪ `SkillFlow` 的键里） |
| 弹窗标题条不残留（keepDialog） | 烟测 `票 07-K2`（亮牌窗**不关**、`#t-dialog-hint` 立刻空且 hidden）+ `K1` 前置 / `K3` 起播期三件套也空 |
| 终局后提示区三件套全空 | 烟测 `票 07-E1`（先摆满技能串/页脚/描述/技能组，`applyView(finished)` 后：`#t-skill-hints` / `#t-context` / `#t-target-hint` / `#t-dialog-hint` 全空、`#t-options` 空、`#t-prompt-skills` 0 个按钮、`#t-prompt` = "本局已结束。"、页脚 = 新的终局提示） |
| 离间三级逐字 = 用户三句 | 烟测 `票 07-T1/T2/T3`（真 `renderPreativation` + 真 `#t-target-hint`；`promptComposedText` 逐字 `===`，提示行 `includes` 同一句） |
| 编辑写覆盖层 + 导出 | 烟测 `票 07-T4..T9`（按钮可见且有处理器 → 点它写 `localStorage['sgs.prompts']`（只那一格）→ 立刻重画 → 导出 JSON → 清覆盖层回到文件层） |

### `prompts.json` 初稿

- 路径：`tools/sgs_web/static/prompts.json`（生成器：`.scratch/table-ux-overhaul/gen_prompts_draft.py`，一次性、不入库）。
- 规模：**82 条技能 / 142 处步骤**（spec 写的"63 处 step_requirements + 34 条 SkillFlow"是旧数——
  今天实测 82 条技能带 `step_requirements`、142 处站点、**108** 条 `SkillFlow` 实例）。
  每条 = 该技能 `step_requirements` 的 `ask` 原文（**与服务端那一句逐字相同** ⇒ 没有模板的技能行为不变），
  外加界/标两版【离间】的 `ask` + `done`（用户原话那三句）。

### 待用户改字（不阻塞；改完即生效）

1. **两版【离间】**已经按用户 2026-11-11 的口径写成 ask/done（`hero.jie_diaochan.lijian`：
   `card.done="弃置一张【{card}】"`、`victim.ask="选择1名男性角色成为决斗目标"`、
   `user.ask="选择1名男性角色对【{seat}】使用决斗"`；标准版次序相反、`user.done` 点名使用者）。**其余 80 条**
   目前只有 `ask`（原文照抄）——要"逐级累加"得自己补 `done`；缺 `done` 的步骤在累加里不贡献任何一段。
2. 想改哪一句就改 `prompts.json`，或游戏内点「改这段文案」（写 `localStorage['sgs.prompts']`、再「导出文案 JSON」
   贴回仓里）。覆盖层是**稀疏**的：只存改过的那几格，键级叠加在文件层上。
3. 键 = 服务端 `skill_id`、步骤名 = 清单 `Step.name`；打错**不报错**只会静默不生效（门禁只核"是不是真技能 / 真步骤"）。
4. `window:<cause>`（例：`window:DODGE`）这一族**一句都没写**：它只填空（渲染器没写步骤提示时才用），
   现在没写 = 不覆盖各专用面的规则话术。要统一改窗口问法再往里加。

### 刻意读法（与票面字面不同的地方）

1. **reset 的"镜像"清除口住 `modal.js`**（`resetDialogHint`），由 `resetAskSurface` 调——镜像的写入本来就在
   `modal.js:syncDialogHint`，两边各写一份就是票面第二类缺欠的翻版。静态断言因此核"reset 调了它 + 它真清 `#t-dialog-hint`"。
2. **"每次 renderPending 之前"落在函数头部**（等价且不会漏：每条 return 路径都从清空的面开始）。
3. **空转重绘出口落在 `poll()` 的空转分支**，`pollIsIdle` 保持"有没有新事实"的原义：若让 `pollIsIdle` 因指纹变化返回
   false，就会走 `applyView → commitView → resetSelection()`，把玩家选到一半的牌清掉。故新增
   `snapshotFingerprint` + `applyIdleRepaint`，`poll()` 里"指纹变了"优先于 `applyIdlePoll`——**票 10 在这条缝上接武将牌区派生**。
4. **`#t-choice` 的寿命分两类**：判定类（提交被拒 / 已请判定 / 已过期 / 页面提示）带 8s 兜底寿命；状态类
   （"将提交：…" / "还要选 2 名角色"）只受 reset 管——给它们超时会让页脚在玩家没动时自己变空。
5. **第三层通用文案按票面升级**：`选牌 / 选目标` → `选择{n}张牌 / 选择{n}名角色`（票面 §B 的措辞）。
   步骤**条**仍用短标签"选牌"（它要短），故既有断言 `㉿WD4` 改了提示行那一半（**唯一的既有断言改动**）。
6. **`#t-prompt-skills` 在 reset 之后立刻按这一问重铺**（`renderPromptSkills(view)`）：技能组属于询问面，
   起播时随七件套一起让位，但正常重绘必须马上回来（否则底部/提示两处技能入口只剩一处）。
7. **终局仍是"终局文案"不是"全空"**：`#t-prompt` = "本局已结束。"、页脚 = `showPageNotice` 的"已结束（录像已存）"，
   两者都是**新事实**（由 reset 之后写入），残留的三件套（技能串 / 步骤提示 / 描述 / 选项格 / 技能组 / 镜像）全空。
8. **顺带核掉 map §Fog #6**：`Query.prompt` 是必填 `str`，`_build_query` 的每一条分支都写死非空字面量
   （唯一变量点 `RESPOND` 的三选一也全是非空常量）⇒ 引擎没有"询问无文本"面。

### 门禁

- `.venv/bin/pytest tests/test_web_static.py` → **128 passed**（新增 4 条）。
- `bash tests/smoke-web.sh` → **全部通过**（新增 `票 07-K1..K3 / T0..T9 / W1 / E0..E1` 共 16 条）。
- `.venv/bin/pytest tests/test_web.py` → **305 passed**。
- `bash tests/test_web.sh`（端到端）→ PASS。
- `bash tools/check_goldens.sh` → **47 条标准 108 金录像逐字节不变**（文案只做显示覆盖）。
- 全量：`git commit` 的 pre-commit hook（`tests/run_all.sh --quiet`）→ **ALL TESTS PASSED**。
- 实机（bsk）：页面加载 / 静态资源 200 / 零页面 console 错误已核；**点选被 bsk 的 cooperate
  握手拒绝**（`browser_interact` 的 click / fill / select 全被拒），交互那几步留给用户按
  `## 复现配方` 走——`bash tests/smoke-web.sh` 的 16 条（含离间的三级逐字、keepDialog 镜像、
  终局三件套、编辑 + 导出）是同一批 DOM 行为的自动半边。

## 复现配方（实机，票 07）

**环境**：`bash tools/check_env.sh` → PASS（含「bsk 可用」）。

**本次 agent 侧只走完"页面加载"**（受管后台任务起服务，收尾已 `job_kill`）：

```bash
.venv/bin/python tools/sgs_web/serve.py --host 127.0.0.1 --port 8137 --quiet \
  --runs-dir .scratch/table-ux-overhaul/runs      # 受管后台任务，不要 nohup
# 浏览器：http://127.0.0.1:8137/
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8137/prompts.json   # 200
```

- 开局页正常渲染；**页面级 console 零错误**（只有 `chrome-extension://` 自己的加载噪音）。
- `prompts.js` / `prompts.json` 都经静态路由取到（200，同一份 `_serve_static`）。
- **点选 / 填表全部被 bsk 拒绝**：`This action requires the context returned by the latest
  cooperate sync`（`browser_interact` 的 click / fill / select 与 `browser_page reload` 都是这一条；
  换新 session、`noFocus` 起、resize 也一样）。本会话的浏览器只能**读**不能**驱动**，故
  交互那几步按下面的配方留给用户 / 下一次 bsk 握手正常时走。

**用户点选配方**（4 人 / 点将 = 界貂蝉；其余随机，牌堆「标准 108」、时限 0、种子任选）：

1. 开局 → 进牌桌；底部技能区点「离间」进预发动清单。提示行应为
   `第 1/3 步：选择1张牌弃置（1–1 张，已选 0 张）。`，右下动作行出现「改这段文案」。
2. 点一张手牌 + 「下一步」→ 提示行变成
   `第 2/3 步：弃置一张【<牌名><花色><点数>】，选择1名男性角色成为决斗目标（点场上武将选择）。`
3. 点一名男性角色 → 提示行变成
   `第 3/3 步：弃置一张【<牌名><花色><点数>】，选择1名男性角色对【<N>号位<武将名>】使用决斗（点场上武将选择）。`
4. 点「改这段文案」→ 浏览器原生输入框 → 改一句 → 提示行立刻用新文案（覆盖层只改这一格）；
   右下出现「导出文案 JSON」，点它可复制 `localStorage['sgs.prompts']` 的 JSON 贴回仓里。
5. 打完 / 终局屏：`#t-prompt` = "本局已结束。"、页脚 = "已结束（录像已存）"，
   技能串 / 步骤提示 / 描述（`#t-context`）/ 选项格 / 技能组 / 弹窗标题条全空。
6. 顺带的最小覆盖（同一局就能看到）：进入处理区、舞台卡更新、弃牌堆 1s 停留 + 1s 淡出。
