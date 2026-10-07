# Issue tracker：本地 Markdown

本仓库的工单（issues）与规格（specs）以 markdown 文件形式存放在 `.scratch/` 下。

## 约定

- 一个特性一个目录：`.scratch/<feature-slug>/`
- 规格文档：`.scratch/<feature-slug>/spec.md`
  （注意与仓库内长期设计文档 `开发文档/` 区分：前者是一次性工作产物，后者是稳定态设计）
- 实现工单每票一个文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，
  从 `01` 起编号，**不要**写成单个合并的 tickets 文件
- 工单文件顶部用 `Status:` 行记录状态
- 评论与对话历史追加到文件底部的 `## Comments` 标题下

## 当某个技能说“publish to the issue tracker”

在 `.scratch/<feature-slug>/` 下新建文件（目录不存在则创建）。

## 当某个技能说“fetch the relevant ticket”

读取所引用路径的文件。通常你会直接拿到路径或工单号。

## Wayfinding 操作

供 `/wayfinder` 使用。**地图（map）**是一个文件，每张工单对应一个**子文件**。

- **地图**：`.scratch/<effort>/map.md`（含 Notes / Decisions-so-far / Fog 正文）。
- **子工单**：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 起编号，正文写问题。
  `Type:` 行记录工单类型（`research`/`prototype`/`grilling`/`task`）；
  `Status:` 行记录 `claimed`/`resolved`。
- **阻塞**：顶部附近的 `Blocked by: NN, NN` 行。
  当它列出的每个文件都是 `resolved` 时，该工单解除阻塞。
- **前沿（frontier）**：扫描 `.scratch/<effort>/issues/`，找出未关闭、未阻塞、未被认领的文件；编号最小者优先。
- **认领**：先写 `Status: claimed` 并保存，再开始任何工作。
- **解决**：在 `## Answer` 标题下追加答案，置 `Status: resolved`，
  然后把一条上下文指针（摘要 + 链接）追加到 `map.md` 的 Decisions-so-far。
