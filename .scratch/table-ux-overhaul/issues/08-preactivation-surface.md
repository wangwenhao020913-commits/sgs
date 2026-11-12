# 08: 预发动改造——去弹窗 / 自动推进 / 取消回第一步

**What to build:** 预发动不再开弹窗，改为**询问控件区 + 牌桌表面**；非必要的步自动推进；
"取消"退回第一步；**最后一步选好后必须点「提交」才发动**。

**Blocked by:** 07（同一片提示区 DOM）、11（自动推进需要服务端现算的步骤池）
Status: resolved
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

## Answer

### 改动摘要

**前端交互面**（`tools/sgs_web/static/`，纯前端；`src/` 零改动）：

- `preactivation.js`
  - **去弹窗**：新增 `preactivationDialogKind()`（**只看步骤形状**：`option` 步 ≥3 项或复选
    才返回 `"preactivation"`），`modal.js:dialogWantedKind` 的 `state.preactivation` 分支只
    问它——座位步 / 选牌步 / ≤2 项选项步 / 借刀两步**一律不开窗**。`selectionEnabled` 判据
    一字未改（牌桌照常可点）。
  - **删掉弹窗正文里的座位按钮与「下一步」**（`renderPreativation`）：座位步只在牌桌上点
    武将、选牌步只在牌桌上点牌；选项 ≤2 项住 `#t-options`，≥3 项同一份按钮画在弹窗正文里。
  - **自动推进**：`preactivationStepSingleValue`（`min == max`）、`preactivationShouldAutoAdvance`
    （有预览通道：跟回包的 `cursor` / 只在 `can_advance` 时前进、`next === null` 即停在最后
    一步只亮「提交」）、`preactivationAdvanceNow`（没有预览通道的载荷：按清单形状本地判，
    **`options === null` ⇒ 不推进**，fail-closed）、`preactivationAdvanceAllowed`（这一步能不能
    走 / 中间步「提交」的可点态）。
  - **「取消」= 回第一步**：`resetPreativationProgress` 是唯一复位点（清 `answers` / `cursor=0`
    / 预览态含去抖计时器 / `convertSkill` / 焦点 / `picks` / `selectedTargets` /
    `selectedOption` / `lastRejectReason` / 弹窗折叠态，再重铺提示区）；`preactivationAtFirstStep`
    用本地 `touched` 标记判"第一步还没动过"（借刀窗口进入时就预选 Bn，按答案判会永远退不出去）。
  - **「提交」两档**：`preactivationConfirmable`（中间步 = 确认这一步）+ `preactivationStepHint`
    在最后一步填完时把「清单已填完，可以确认发动。」接在计数后面。
  - `preactivationSubmitAllowed` 的"回包新鲜度"判据从 `cursor` 相等改成
    `previewToken === previewVersion`（`preactivationPreviewFresh`）：单值步答完服务端会把光标
    推过这一步，拿 `cursor` 比会把"填完了"读成"回包过期"；没有预览通道时允许**最后一步填满即
    可提交**（`min != max` 的步不自动走，光标因此停在最后一步）。
- `pending.js`：删掉「预发动【X】：按清单逐步选择」这句泛化 prompt（`#t-prompt` 回服务端那句）；
  `cancelOrClear` 加「先回第一步、已在第一步才退出 / 送 Pass」级联；新增 `submitLeftAction`
  （底部「提交」键的分派：中间步确认这一步、最后一步确认发动）；`refreshSubmit` /
  `setActionLabels` 按同一判据出可点态与文案。
- `modal.js`：`dialogWantedKind` 只对"牌桌承载不了的那一步"开窗（注释同步）。
- `main.js`：`#btn-submit` 只挂 `submitLeftAction`（点击口一处判）。
- **文档写回**（map.md 缺口 #丙，票 06 有意留给本票）：`开发文档/04-AI与交互设计.md` §4 新增
  「预发动落地」块（不是弹窗 / 各步表面 / 自动推进 / 最后一步提交 / 取消回第一步）
  + §1 的「N = 本层存活数」改成 **N = 本层合格者数**（引擎缺省 OFF、网页会话缺省 ON）；
  `GLOSSARY.md`「预发动」去掉"票 08 的目标语义 / 落地前仍是弹窗"的旧注并补"最后一步提交"；
  `docs/agents/frontend-map.md` 三行（`preactivation.js` / `pending.js` / `modal.js`）同步。

### 会红的既有断言（全部改写，未删除）

- 票面点名的 5 条：`tests/support/ui_smoke.mjs` 的 `㉿R4`（2794-2795，「下一步」可点）、
  `㉿JY6`（2911-2912）、`㉿Q5`（2989 / 3144-3145 麒麟弓）、`㉿JY4`（2905，`#t-prompt` 含
  「预发动【渐营】」）——分别改写成"中间步用「提交」确认"、"单值步点完自动到底只亮提交"、
  "提示行第 1/1 步 + 不开窗"，以及 `tests/support/action_probe.mjs:348-351`（两处直调
  `advancePreativation`）改成"点牌后断言已自动进座位步 + `preactivationSubmitAllowed` 验提交门"。
- `tests/test_web_static.py`：**没有**"下一步"的静态断言（`grep 下一步` 的两处都是回放排程
  文案），该文件 130 条全绿、一字未改——票面这一条按"无残留"收口。
- 同一口径顺带红、一并改写的：`㉿R2`（泛化 prompt）、`㉿R8`、`㉿PZ5/7/8/9`、`㉿PZ19–PZ24`、
  `㉿BS7`、`㉿MT3/MT7`、`㉿BN6/BN7`、`㉿TS1/TS3/TS5`、`㉿WC2/4/5/6`、`㉿JS6`、`㉿AF12/AF14`、
  `U2-04d`、`U1-03p–v`、`U2-05i–i4`、`U2-06f–f5`、`票 10-A5/A6/B3`、`票 07-T2`。

### 新增断言（`tests/support/ui_smoke.mjs` 的 `U-PA-0x` 块，23 条）

- `U-PA-01`（a–e）预发动全程 `#t-dialog` 保持 hidden、座位步不铺座位按钮 / 没有「下一步」、
  点牌自动进第 2 步、点武将即填完可确认且零提交。
- `U-PA-02`（a–d）离间三步（预览回包注入驱动）：进清单先问预览 → 点牌 `can_advance` ⇒ 自动进
  第 2 步 → 点座位自动进第 3 步 → 最后一步填完**停在原地**：不自动提交、光标不越界、提交键亮、
  提示写「清单已填完」。
- `U-PA-03`（a–e）主动入口取消 = 回第一步（答案清空 / 光标 0 / 仍在预发动 / 零提交），再取消
  退出；被动入口选过一步后首次取消只回第一步（**不送 Pass**），再取消才送 `Pass`（恰一条）。
- `U-PA-04`（a–d）破围两项住控件区（无弹窗）、点一项自动进分支、取消回第一步后可改选另一支。
- `U-PA-05`（a–c）借刀两步全删弹窗：点牌进清单、点 A 自动进 Bn、点 Bn 后只亮「提交」并拼出
  `UseCard(借刀, targets=(A,), designations=(Bn,))`、未自动提交。
- `U-PA-06`（a–d）界神速三项仍 `multiChoice` 弹窗、定乱未知区域仍 `unknown` 弹窗、转化清单
  第 1 步（选源牌）仍是牌桌、走到「选牌名」步（4 项）才开窗且选项列在正文里。

### 刻意读数（deliberate readings）

1. **`dialogWantedKind` 仍会为 `state.preactivation` 返回模板，但只在"牌桌承载不了的那一步"**
   （`preactivationDialogKind`：选项 ≥3 / 复选）。票面根因那句"不再对 `state.preactivation`
   返回模板"与"要做什么 2"的"选项步 ≥3 或复选保留弹窗"在同一条票里互相约束；取前者为
   "不再**无条件**返回"，步表面清单按后者落。座位步 / 选牌步 / 借刀两步因此真的零弹窗
   （U-PA-01/05 正向断言），`selectionEnabled` 判据未动。
2. **「提交」在中间步 = 确认这一步**（票面 3 的 `min != max` 一档）：同一颗底部键，文案在中间步
   是「提交」、最后一步是「确认发动 / 确认使用」；`submitLeftAction` 一处分派，`refreshSubmit`
   与 `setActionLabels` 同读 `preactivationConfirmable`。
3. **自动推进的落点跟服务端的 `cursor` 走，并夹在最后一步**：`cursor > 本地光标` = 服务端已替
   我们走过（单值步满足后它就推），`cursor == 本地光标 && can_advance` = 这一步要显式确认，
   按 Q5 只让 `min == max` 或"合法答案唯一"自动走；`next === null`（到末尾）时**停在最后一步**
   只亮「提交」，既不推进也不自动提交。顺带修正 `preactivationSubmitAllowed` 的新鲜度判据
   （版本号而不是光标相等），否则"填完了"会被读成"回包过期"。
4. **没有预览通道的载荷（合成夹具 / 借刀清单）按清单形状本地判**（`preactivationAdvanceNow`）：
   服务端没有可问的流水线时只能这样；仍然 `options === null ⇒ 不推进`，不退回"全都点得动"。
5. **「已在第一步」用本地 `touched` 标记判**，不看答案是否为空——借刀窗口进入时就预选必选首项
   Bn，按答案判会让"刚进来"永远退不出去。
6. **文档写回是本票的刻意扩面**（不是顺手改）：map.md 缺口 #丙 裁决由票 08 在同一个提交里收口
   `开发文档/04` 的 §4 + §1 与 `GLOSSARY.md`／`frontend-map.md` 的同一口径。

### 门禁结果

- `.venv/bin/pytest tests/test_web_static.py` → **130 passed**
- `bash tests/smoke-web.sh --verbose` → **1677 条 PASS，全部通过**（含上面 23 条 `U-PA-*`）
- `.venv/bin/pytest tests/test_action_contract.py` → **13 passed**
- `.venv/bin/pytest tests/test_web.py` → **315 passed**（前端读取方契约未动）
- `bash tools/check_goldens.sh` → **47 条标准金录像逐字节不变**（纯前端票，记录流零改动）
- `bash tests/test_dev_docs.sh` / `bash tests/test_agent_skills.sh` → PASS（文档写回不破格式 / 地图守护）
- 全量门禁 = commit hook 的 `tests/run_all.sh --quiet`（本提交）。

### bsk 实机验收配方（本环境 bsk 的 cooperate 握手失败，未自驱动；功能判据 = `smoke-web.sh`）

1. 预检 / 起服务：`bash tools/check_env.sh` → `bash tools/serve-dev.sh start` → 打开打印的 URL。
2. **仁德（去弹窗 / `min != max` 用「提交」/ 取消回第一步）**：4 人 / 种子 12 / 点将 `liubei`
   （或 `jie_liubei`）→ 轮到自己出牌阶段点【仁德】：①`#t-dialog` **不出现**，提示条写
   「第 1/2 步：选择任意张要交给其他角色的手牌…」；②点 1–2 张手牌 → 底部键变成「提交」，
   点它进第 2 步；③点一名其他角色 → 键变「确认发动」，提示写「清单已填完」；④点「取消预发动」
   → 回第 1 步、已选清空（Network 面板零 `/answer`），再点一次 → 退出本地态。
3. **借刀（全删弹窗）**：4 人 / 种子 4 / 点将任意（起始手牌有【借刀杀人】）→ 出牌阶段点
   【借刀杀人】→ 点持刀者 A（牌桌）→ 自动进 Bn（全程无弹窗，控件区无座位按钮 / 「下一步」）→
   点 Bn → 点「确认使用」。
4. **转化清单（保留弹窗）**：4 人 / 任意种子 / 点将 `jie_jushou_mobile`（界沮授）→ 出牌阶段点
   【渐营】→ 点一张手牌（牌桌直选、无弹窗）→ 走到「选牌名」步（4 项）**弹窗出现** → 选【杀】→
   点目标 → 「确认发动」。
5. **回归**：界【神速】的「至多三项」仍 `multiChoice` 弹窗、于禁【定乱】的盲挑仍未知区域弹窗；
   全流程任何一步都不该出现「下一步」按钮。
