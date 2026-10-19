# Issue tracker：本地 Markdown

本仓库的工单（issues）与规格（specs）以 markdown 文件形式存放在 `.scratch/` 下。

## 约定

- 一个特性一个目录：`.scratch/<feature-slug>/`
- 规格文档：`.scratch/<feature-slug>/spec.md`
  （注意与仓库内长期设计文档 `开发文档/` 区分：前者是一次性工作产物，后者是稳定态设计）
- 实现工单每票一个文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，
  从 `01` 起编号，**不要**写成单个合并的 tickets 文件
- 工单文件顶部用一行规范 `Status:` 记录状态，取值限词表：`open`（未认领）/ `claimed`（已认领）/ `resolved`（已解决）。
  写成 `Status: <值>`（**不加粗**、不追加别的词）；`tests/test_agent_skills.sh` 会机械校验 `.scratch/*/issues/*.md`。
- 评论与对话历史追加到文件底部的 `## Comments` 标题下
- **收口（`## Answer`）**：**开工时先置 `Status: claimed`**（让"在飞"可见 —— 派发被中断时，
  这是别人判断"做到哪了"的唯一线索）；**解决时在文件底部的 `## Answer` 标题下写答案**
  （非空正文），再置 `Status: resolved`。
  想对本 feature 启用机械校验，就在它的 `spec.md`（或 `design.md`）里写一行
  `Answer contract: on`：此后该 feature 的每张 `resolved` 票都必须有非空 `## Answer`
  （`tests/test_agent_skills.sh` 核）。不写则沿用旧约定（`## Comments` 收口），不追溯历史。
- **`Touches:`**：顶部可选一行 `Touches: <逗号分隔的仓库内路径>`，声明本票会改哪些文件。
  `/implement-spec` 据此**机械**判断前沿上的票能不能并行（文件不重叠才并行）；
  它也让"票面说可并行、实际全撞同一个文件"在**写票时**就暴露，而不是等执行时才发现。
  写了就必须每条路径都存在（`tests/test_agent_skills.sh` 核）。
- **`Ruling:`**（可选）：写回票在 `Touches:` 下加一行 `Ruling: <原文票或路径>`，指向裁定原文。
  `06`（§5「程序化约定与偏离一手」）的账本行用 `tools/ledger_index.py --grep <票号/关键词>` 定位；票面说「某行」但账本
  里没有时，先查原票，不要假设行存在。

## 当某个技能说“publish to the issue tracker”

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则创建）。

## 当某个技能说“fetch the relevant ticket”

读取所引用路径的文件。通常你会直接拿到路径或工单号。

## 验收 / 实机票

- 实机验收票必须带 `## 复现配方`：人数 / 种子 / 点将 / 操作步骤。没有配方就用
  `.venv/bin/python tools/acceptance_seed.py --players 4 --card <type> [--other-slot weapon] [--other-card <type>]`
  扫起始手牌（只读，不模拟出牌）。
- 多步人工验收准备好服务器 + 配方后**结束 turn**，不要用 `ask_user_question` 阻塞等待。
- 浏览器自动化先跑 `bash tools/check_env.sh`；缺 `bsk` 时 browser-skill 不可用，由用户手动验收。
- 写回/收口前先跑
  `.venv/bin/pytest -q tests/test_writeback_landing.py && bash tests/test_dev_docs.sh`；
  全量门禁等文档冻结后只跑一次；失败先 `grep -nE 'FAIL|失败'` 看全部失败，不要只 `tail`。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图（map）**是一个文件，每张工单对应一个**子文件**。

- **地图**：`.scratch/<effort>/map.md`（含 Notes / Decisions-so-far / Fog 正文）。
- **子工单**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 起编号，正文写问题。
  `Type:` 行记录工单类型（`research`/`prototype`/`grilling`/`task`）；
  `Status:` 行记录 `open` / `claimed` / `resolved`（词表同「约定」节）。
- **阻塞**：顶部附近的 `Blocked by: NN, NN` 行。
  当它列出的每个文件都是 `resolved` 时，该工单解除阻塞。
- **前沿（frontier）**：扫描 `.scratch/<effort>/issues/`，找出未关闭、未阻塞、未被认领的文件；编号最小者优先。
- **认领**：先写 `Status: claimed` 并保存，再开始任何工作。
- **解决**：在 `## Answer` 标题下追加**非空**答案，置 `Status: resolved`，
  然后把一条上下文指针（摘要 + 链接）追加到 `map.md` 的 Decisions-so-far。

## 逐票派发（retro-2 S5）

多票并行只在**文件不重叠**时才划算——`Touches:` 就是为此存在的机械判据。默认节奏是**串行**：

- **一票一个实现者**；票面 `Touches:` 有交集就**别并行**。`app.js` / `projection.py` /
  `catalog.py` 这类共享文件一撞，人工解冲突比串行更贵。
- 派发前先提交（或明确告知实现者"工作树未提交"，见 `AGENTS.md` 注意事项 3）。
- **落地后父代理先核 diff、再独立跑一遍门禁**；不要只信实现者的自报。
- 实现者被中断时，**先读它的 `Status:` 与 `## Answer`**（`claimed` + 无 Answer = 半成品），
  再用 `git status` 认未提交的 WIP，然后决定接手还是重派——不要凭猜。
- **票宽就是上下文预算**：`Touches:` 跨 **2 个以上顶层模块**（`src/sgs/`、`tools/sgs_web/`、
  `开发文档/` …）的票，**先切成两票再派**。本波有实现者在 10 文件的宽票上把上下文烧爆
  （883 KB 会话日志）——票本身没错，是**形状**错了。
- **窄但开放的诊断票同理**：先派一次**有界侦察**（只产出"一条稳定变红的命令"），再派修复。
  把"找复现"和"改代码"塞进同一个上下文，本波在**同一张 2 文件的票**上连死两位实现者。
- **等实现者时不要轮询**：让"子代理已结束"的通知驱动下一步；间隙只做**只读**工作
  （验收面 sweep、文档订正、核断言是否真实存在），别隔几分钟起一个等待循环。
  `code-review` 的两轴子代理同样：**不要 `list_agents` 轮询**；docs-only diff 把 diff 内联、
  限定读两份改动文档 + 票面，别让子代理通读大 `.scratch` 文件。
