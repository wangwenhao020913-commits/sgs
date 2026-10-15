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

## 当某个技能说“publish to the issue tracker”

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则创建）。

## 当某个技能说“fetch the relevant ticket”

读取所引用路径的文件。通常你会直接拿到路径或工单号。

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
