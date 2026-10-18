# AGENTS.md

本文件记录本项目（SGS）中所有协作者与自动化 Agent 必须遵守的注意事项。

## 注意事项

1. 每次改动后，必须编写或更新相关测试，并在交付前跑通测试和验证。收口时对本 effort 再跑一次
   **`.venv/bin/python tools/check_acceptance.py .scratch/<effort>`**：它扫该 effort 的
   `.scratch/*/issues/*.md` 里 `## Answer` 引用的 `test_*`，逐个核成"存在且绿"（模块名 /
   省略号占位 / 明说不存在的名字不算引用）。它是只读工具，**不进** `tests/run_all.sh` ——
   `.scratch/` 是 gitignore 的，门禁不能依赖它。
2. 唯一门禁是仓根的 **`bash tests/run_all.sh`**（全部 shell 校验 + pytest，**约 3 分钟**）。
   **没有 CI、也没有默认 hook 会替你跑它。** 先跑一次 `bash tools/install-hooks.sh`
   把它接到 pre-commit 上（改动落在 `src/` 或 `tests/` 时才触发）；**合并提交**由同源的
   `.githooks/pre-merge-commit` 覆盖（`git merge` 不触发 pre-commit，否则会整段绕过门禁）。
   **放后台跑**：它超过多数前台工具调用的超时（前台跑会被打断、输出丢掉）；用后台任务
   起它，再取输出。**提交时 hook 会替你跑同一件事，别在提交前手动重跑一遍**（每票白等
   3 分钟）；迭代期要快就用**单文件子集**：**`.venv/bin/pytest <file>`** —— `python` 不在 PATH，
   门禁也只认 `$ROOT/.venv/bin/pytest`、不回退系统 pytest —— 或 `bash tests/smoke-web.sh`。
3. **派发子代理之前先提交**（或先明确告知它"当前工作树未提交"）。子代理会在自己的会话里
   跑 git 命令收尾自己的临时改动；工作树未提交时，一条 `git checkout -- <file>`
   就把你手上的活打回 HEAD 了。本仓已发生过两次：`ui_smoke.mjs` 被抹回 HEAD；票 13 的实现者
   用 `git stash` 做"改前对照"，把自己的活从工作树里清掉（靠 `git fsck` 找回游离对象）。
   **要对照基线就走安全通道**：`bash tools/baseline-worktree.sh` —— 它在仓外另开一棵 worktree，
   绝不动你手上的工作树。
4. 前端改动想快速看一眼，用 **`bash tests/smoke-web.sh`**（约 4 秒：起临时服务 +
   跑 `ui_smoke.mjs` + 收尾）。它在门禁之外，跑完整门禁才用 `tests/run_all.sh`。
   人工**实机验收**：`.venv/bin/python tools/sgs_web/serve.py --port 8080`（**缺省端口 8100**，
   8080 只是习惯用法；服务把实际地址打到 stdout）。
5. **派发**：同一 checkout 同时只能有一个实现者。确需并行时，每个实现者只
   `git add <显式路径>`，禁止 `git add -A` 与带 `-am` 的提交；任何人不得对别人未提交的改动做
   `git checkout --` / `git stash` / `git reset`。并行安全的前提是"改的文件不重叠"，而它只能
   从**显式路径**读出来——`git add -A` 会把别人的半成品卷进你的提交（本轮真踩过：两张票同时
   改 `virtual.py`，被迫撤回）。
6. **长跑检查点**：预计超过约 30 分钟的票，实现者在过半时落一个 `wip:` 提交（交付前 squash
   或 amend 掉）；机器中断时，工作树就是恢复点。本仓丢过一整轮上下文，靠工作树里的半成品
   才接上。

## Agent skills

### Issue tracker

工单以 markdown 文件形式存放于本仓库的 `.scratch/` 目录。详见 `docs/agents/issue-tracker.md`。

### Domain docs

单上下文（single-context）布局。当前 ADR 见 `开发文档/06-里程碑与风险.md` 第 4 节，稳定态设计见 `开发文档/`。详见 `docs/agents/domain.md`。

### Coding standards

评审期（`code-review` 的 Standards 轴）的判断题标准见 `CODING_STANDARDS.md`。
