# 08: 预发动改造——去弹窗 / 自动推进 / 取消回第一步

**What to build:** 预发动不再开弹窗，改为**询问控件区 + 牌桌表面**；非必要的步自动推进；
"取消"退回第一步；**最后一步选好后必须点「提交」才发动**。

**Blocked by:** 07（同一片提示区 DOM）、11（自动推进需要服务端现算的步骤池）
Status: open
Type: task
Touches: tools/sgs_web/static/preactivation.js, tools/sgs_web/static/pending.js, tools/sgs_web/static/modal.js, tools/sgs_web/static/table.js, tools/sgs_web/static/options.js, tests/support/ui_smoke.mjs, tests/support/action_probe.mjs
Ruling: spec §2 Q5 / Q12；`开发文档/04-AI与交互设计.md` §4「预发动」

## 根因（spec §1 #4）

- 预发动**是弹窗**：`modal.js:417-424` 的 `dialogWantedKind` 对 `state.preactivation` 返回 `"preactivation"`，
  `:659` 把 `#t-options` 搬进 `#t-dialog-body`，`:695-704` 把步骤提示镜像到标题条。
- 座位步在弹窗正文里**还铺了一遍座位按钮**（`preactivation.js:830-855`，注释自认"模态窗口居中后可能挡住某几个座位"）
  ——这正是"仁德第二步列出所有其他玩家"的来源；去弹窗后这一份应当消失，只留牌桌点武将。
- 强制「下一步」只发生在 **card 步**与**多选座位步**（`preactivation.js:856-867`）；单选座位步与选项步本来就自动推进。
- 「取消」现状：被动入口送 `Pass`（时机消费），主动入口只清本地态（`cancelOrClear:499-527`）；
  **没有"退回第一步"的复位点**：`pa.answers[*]`、`cursor`、`#t-target-hint`/`#t-prompt`/`#t-choice`、
  `lastRejectReason`、`focusCardId`/`selectedTargets`/`picks`/`selectedOption`、`state.convertSkill`、
  弹窗折叠态都得手工复位，今天只清了一部分。

## 要做什么

1. **去弹窗**：`dialogWantedKind` 不再对 `state.preactivation` 返回模板；预发动期间牌桌保持可点
   （`selectionEnabled` 判据不变），步骤条 + 提示文本全部住询问控件区（`#t-options` 与 `#t-target-hint`）。
2. **表面清单**（Q12，**判据不写技能名**）：
   | 步 | 表面 |
   |---|---|
   | 座位步（单选 / 多选） | 牌桌点武将；控件区只写计数与「提交」；**不再铺座位按钮** |
   | 选牌步（自己手牌 / 装备） | 牌桌直选（点牌即选）；不再铺槽位 / 牌名按钮 |
   | 选项步 ≤2 项（如神太史慈【破围】） | 控件区按钮，点后自动进下一步 |
   | 选项步 ≥3 项 或 复选（界夏侯渊【神速】3 项） | 保留询问弹窗（`multiChoice`） |
   | 转化清单（来源 → 产物） | 保留询问弹窗 |
   | 未知区域（于禁【定乱】盲挑他人手牌） | 保留询问弹窗 |
   | 借刀两步（A → Bn） | **全删弹窗**：牌桌点 A 自动进 Bn，点「提交」发动 |
3. **自动推进判据（服务端说，前端不猜）**：`min == max` **且本步已填满** ⇒ 自动进下一步；
   `min != max`（"弃置任意张"、"选择任意名"）⇒ 需要点「提交」确认这一步。
   例（用户原话）：【离间】第一步 `min==max==1`，点 1 张牌即自动进第二步；第二步同理。
   **例外**：合法答案唯一（如只剩一个可选座位）⇒ 也自动推进。
4. **最后一步必须点「提交」**：`cursor` 到末尾后**不自动提交**；「提交」的文案与判据沿用现状
   （`pending.js:290-292`、`preactivationReady`）。
5. **「取消」= 回第一步**：新增一个统一复位函数（清 `pa.answers` / `cursor=0` / `convertSkill` /
   焦点 / `picks` / `selectedTargets` / `selectedOption` / `lastRejectReason` / 折叠态，并重画提示区）；
   **已在第一步** ⇒ 主动入口退出本地态（零提交）、被动入口送 `Pass`。
6. **退化 / 没池时不得自动推进**：拿不到"这一步能选谁"时一律要求玩家点「提交」（fail-closed），
   不许替玩家猜答案。
7. **删掉"预发动【X】：按清单逐步选择"这类泛化 prompt**（`pending.js:142-145`），改由票 07 的模板给逐句文案。

## 验收

- 改写 `tests/support/ui_smoke.mjs`：`:2794-2795 / 2911-2912 / 2989 / 3144-3145`（按"下一步"按钮断言）、
  `:2905`（`#t-prompt` 含"预发动【渐营】"）、`tests/support/action_probe.mjs:351`（直调 `advancePreativation`）。
- 新增烟测：
  - `U-PA-01` 预发动全程无弹窗（`#t-dialog` 保持 hidden），座位步不铺座位按钮。
  - `U-PA-02` 离间三步：点牌自动进第二步、点座位自动进第三步、**停在第三步等提交**。
  - `U-PA-03` 取消 → 回第一步且答案清空；再取消 → 退出本地态（主动）/ 送 `Pass`（被动）。
  - `U-PA-04` 破围：2 选项住控件区；选 1 进分支，再取消回第一步、可改选 2。
  - `U-PA-05` 借刀：点 A 自动进 Bn、点提交发动、全程无弹窗。
  - `U-PA-06` 界神速 / 定乱 / 转化清单仍开弹窗。
- `## Answer` 里附 `bsk` 实机验收配方（人数 / 种子 / 点将 / 操作步骤）。

## 会红的既有断言

- 上面列出的 5 条旧断言（全部要改写而不是删除）。
- `tests/test_web_static.py` 里对 `preactivation.js` 的"下一步"静态断言。
