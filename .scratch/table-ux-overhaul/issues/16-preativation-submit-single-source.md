# 16: 预发动：提交判据单源 + 提示区字符串化（去步骤按钮 / 只显示当前步）

**What to build:** 修用户实机报障的"填完了却提交不了"，并按用户口径把提示区从**选项组件**
退回**字符串**：步骤不再画成一排大按钮，只显示**当前这一步**的问法；真正的选项
（【雌雄双股剑】那种二选一）仍然是按钮。

**Blocked by:** None（07 / 08 / 11 / 15 均已 resolved）
Status: resolved
Type: task
Touches: tools/sgs_web/static/preactivation.js, tools/sgs_web/static/options.js, tools/sgs_web/static/pending.js, tools/sgs_web/static/answer.js, tools/sgs_web/static/style.css, tests/support/ui_smoke.mjs, tests/test_web_static.py
Ruling: 用户 2026-11-11 实机报障（截图两张）+ `.scratch/table-ux-overhaul/map.md` 缺口 #壬；spec §2 Q5 / Q12 / Q18

## 根因（已核到代码）

**① 提交判据有两份，互不一致（这就是"填完了却提交不了"）。**

- 标题/提示行写「清单已填完，可以确认发动。」、底部「确认发动」也亮着——读的是
  `preactivationSubmitAllowed`（票 11 之后：服务端回包 `can_submit`，本地兜底按
  **票 08 的语义**：最后一步填满即可，`cursor >= steps.length - 1`）。
- 页脚却写「预发动清单还没填完。」并且**真的拦住提交**——`options.js:397`
  `selectionError` 用的是 `preactivationReady`（**票 08 之前**的语义：要求
  `cursor >= pa.steps.length`，即光标被推出清单末尾）。票 08 改成"最后一步停在原地等
  提交"之后，`cursor` 永远不会越过末尾 ⇒ `preactivationReady` **恒假** ⇒
  页脚永远说没填完、`answer.js` 的提交闸永远拒绝。
  两张截图（界貂蝉【离间】三步、界貂蝉【贯石斧】一步）都是这个形状。

**② 步骤被画成一排 `.opt` 大按钮**（`preactivation.js:1220-1232`）：清单每一步一个
可点 chip（`1. … ✓`），座位步 / 选牌步时 `#t-options` 里**只有这排 chip**。
步数一多就换行，把上面的提示行（真正的那句问法）挤出可视区——用户原话："第一步文本
被第二步第三步挤到上面去看不到了"。

## 要做什么

1. **判据单源**：删掉 `preactivationReady` 这套旧语义，或把它改成
   `preactivationSubmitAllowed` 的别名并只保留一处实现；`selectionError` 的
   `ActivateSkill` 分支改读**同一个**判据（`preactivationSubmitAllowed`）。
   修完之后：提示行说"已填完"时页脚**不许**再说"还没填完"，「确认发动」按下后能真提交。
2. **步骤字符串化**：删掉步骤 chip 那一排（不再往 `#t-options` 里铺步骤按钮）。
   当前是哪一步**只由提示行那句问法**表达（票 07 的三层回退：`prompts.json` 累加 →
   `Step.wording` → 通用文案；累加本身就带"前面几步已完成"的意思）。
   - `jumpPreativationStep` 若因此失去生产调用点，**删掉**并同步改测试；不要留只有
     测试在调的死代码。
   - 「取消」仍按票 08 的语义回第一步（唯一复位点），不靠点步骤条回到前面的步。
3. **只留真选项**：真正的一次性选项（【雌雄双股剑】的 `discard_one` / `draw_one`、
   【崩坏】那种必答二选一）仍画成按钮；`≥3 项 / 复选` 仍进询问弹窗（票 08 的
   `preactivationDialogKind`，不动）。选项按钮**收紧尺寸**（与 `#t-options` 里的
   常规按钮同高，不要占掉提示区半屏）。
4. **不动**：卡片 / 座位仍是牌桌直选；提交键两档语义（中间步确认 / 最后一步发动）；
   预览回包字段；`spec §6` 不变量 1（合法性仍由后端算）。

## 验收

- 新增（烟测，`tests/support/ui_smoke.mjs`）：
  - `U-PA-07` 填满后提示行说"清单已填完"⇒ **页脚不再说"还没填完"**、`#t-choice` 无
    该句、且提交真的发出（复现两张截图的形状：一步 card 步 + 一步 seat 步 / 一步
    card 步 + 一步 option 步）。
  - `U-PA-08` 预发动期间 `#t-options` 里**没有步骤按钮**（座位步 / 选牌步时 `#t-options`
    为空或只含真选项）；提示行只出现**当前步**那一句（不出现"1. … / 2. … / 3. …"的清单）。
  - `U-PA-09` 真选项仍在：雌雄二选一在 `#t-options` 里画成按钮、点得动。
- 改写：`ui_smoke.mjs` 里所有依赖步骤 chip 的断言（`data-step` 选择器 :689、
  `jumpPreativationStep` :7039 / :7279，以及 ㉿JS 组的步骤条断言）。
- 静态断言（`tests/test_web_static.py`）：`preactivationReady` 要么不存在、要么是
  `preactivationSubmitAllowed` 的别名（防两份判据再长出来）。
- 门禁：`bash tests/smoke-web.sh`、`.venv/bin/pytest tests/test_web_static.py`、
  全量 `tests/run_all.sh`；金录像 47 条逐字节不变（本票纯前端）。

## 会红的既有断言

- `ui_smoke.mjs` 的 ㉿JS3 / ㉿JS6（标题与页脚"同源"的旧判据）、:5334（片段）、
  以及任何按 `data-step` 点步骤 chip 的用例——按上面口径**改写**而不是删。

## Answer

**症状与根因（已复现红）**：`selectionError` 的 `ActivateSkill` 分支读的是另一份判据
（要求光标**越过**清单末尾）。票 08 让光标停在最后一步之后那份判据恒假 ⇒ 提示行说
「清单已填完」、页脚说「预发动清单还没填完。」、`answer.js:submitAction` 在本地闸口就
返回、**一个请求都不发**。红证据：把旧判据临时放回去跑 `smoke-web.sh`，
`U-PA-07b/07d` 报 `sent=[] foot="预发动清单还没填完。"`；恢复单源后转绿。

**改了什么**

1. **判据单源**：删掉旧谓词（源码里一个字都不剩），`preactivationSubmitAllowed` 成为唯一实现
   （本地兜底 = `cursor >= steps.length - 1` 且 `firstUnfilledPreativationStep() === null`）；
   `options.js:selectionError` 改读它。提示行 / 「提交」可点态 / `buildPreativationAction` /
   页脚未就绪原因四处同源。
2. **步骤字符串化**：删掉 `renderPreativation` 里的步骤 chip 行（`data-step` 那一排 `.opt`
   按钮），选牌步 / 座位步的 `#t-options` 现在是**空的**；当前步只由提示行那句问法表达
   （票 07 的三层回退，模板层累加自带"前几步已完成"）。失去最后一个生产调用方的
   `jumpPreativationStep` 一并删除（回到第一步走「取消」，票 08 的复位点）。选项步分支的
   提示补了句号（`选择一项。清单已填完…`，原来会粘成一句）。
3. **只留真选项**：选项步 ≤2 项仍画按钮（【雌雄双股剑】的 `discard_one` / `draw_one`、崩坏
   那种必答二选一），≥3 项 / 复选仍进询问弹窗（`preactivationDialogKind` 未动）。提示区里的
   `.opt` 收紧到与 `.prompt-actions` 同高（`.promptbar .options > .opt`，弹窗正文不受影响）并
   删掉只为步骤条存在的 `.options.opt-compact > .cards` 死规则。
4. **没动**：牌桌直选、两档提交键语义、预览回包字段、引擎侧合法性（spec §6 不变量 1）。

**测试**（全部绿）

- `tests/support/ui_smoke.mjs`：新增 `U-PA-07a–d`（截图两种形状：card+seat / card+option；
  填满后页脚无"还没填完"、真实发出 `ActivateSkill`）、`U-PA-08a–e`（`#t-options` 无步骤按钮 /
  只有真选项；提示行只说当前步，含"没有 1. … / 2. … 清单"）、`U-PA-09`/`09b–d`（雌雄二选一仍是
  可点按钮，预发动选项步同理）。改写依赖 chip 的旧断言：㉿JS2/JS2b/JS3/JS6/JS7/JS8、
  ㉿WD1–WD4、㉿PZ7、㉿PZ19、㉿AF12、㉿T5、㉿BN7、㉿WC5、U2-04d、U-PA-01b/06c；删掉
  `preactivationStepRow` / `preactivationStepChip` 助手与桥上的两道出口。
- `tests/test_web_static.py::test_the_preactivation_submit_verdict_has_one_single_implementation`：
  静态钉住"只有一处实现"（旧谓词不存在 / 本地兜底是 `steps.length - 1` 而非"光标越过末尾" /
  页脚与 `buildPreativationAction` 同读 / 没有第二个 `…Ready()` 谓词 / `data-step` 与跳步入口
  已删）。
- 门禁：`bash tests/smoke-web.sh`（全绿）、`bash tests/test_web.sh`（全绿）、
  `.venv/bin/pytest tests/test_web_static.py`（132 passed）、`bash tools/check_goldens.sh`
  （47 条标准 108 金录像逐字节不变）、全量 `tests/run_all.sh --quiet`（本次提交的 hook 输出）。

**没做到的**

- `bsk` 在本环境不可驱动 ⇒ 没走真实浏览器验收（本票改了 DOM 形状）；`smoke-web.sh` 是功能闸。
  选项按钮的新尺寸只是 CSS 估算，需要用户实机看一眼是否还占掉提示区。
- 截图里【贯石斧】的"单步弃两张"形状由既有 `U-PA-02`（三步）与 `U-PA-07` 的同源判据覆盖，
  没有为它单独造一条用例。

**手动验收配方（约 3 分钟）**

```bash
bash tools/check_env.sh
bash tools/serve-dev.sh start 8100     # 或受管后台任务起 serve.py；URL 以打印为准
# 4 人 / 种子 12 / 牌堆 standard_ex_160 / 勾「开局点将」选界貂蝉
```

1. 出牌阶段点【离间】→ 提示行「第 1/3 步：…」→ 点一张手牌 → 点一名男性角色 → 再点另一名
   → 提示行出现「清单已填完，可以确认发动。」，页脚**不再**出现「还没填完」，点「确认发动」
   → 动作真的发出去（对手开始决斗）。
2. 观察提示区：**没有**「1. … / 2. … / 3. …」那排大按钮；提示行只写当前这一步。
3. 装【贯石斧】后用【杀】打一名出【闪】的角色 → 单步「弃置两张牌」→ 选两张 → 「确认发动」
   能打出（截图里的第二种形状）。
4. 反向：任何一步点「取消」= 回到第 1 步（答案清空），再点「取消」才退出 / 放弃。
5. 真选项仍在：触发【雌雄双股剑】或【崩坏】时那两三个按钮仍可点，且比改前矮一档。
