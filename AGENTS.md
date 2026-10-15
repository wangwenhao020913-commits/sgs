# AGENTS.md

本文件记录本项目（SGS）中所有协作者与自动化 Agent 必须遵守的注意事项。

## 注意事项

1. 每次改动后，必须编写或更新相关测试，并在交付前跑通测试和验证。
2. 唯一门禁是仓根的 **`bash tests/run_all.sh`**（全部 shell 校验 + pytest，**约 3 分钟**）。
   **没有 CI、也没有默认 hook 会替你跑它。** 先跑一次 `bash tools/install-hooks.sh`
   把它接到 pre-commit 上（改动落在 `src/` 或 `tests/` 时才触发）。
   **放后台跑**：它超过多数前台工具调用的超时（前台跑会被打断、输出丢掉）；用后台任务
   起它，再取输出。**提交时 hook 会替你跑同一件事，别在提交前手动重跑一遍**（每票白等
   3 分钟）；迭代期要快就用单文件 pytest 子集或 `bash tests/smoke-web.sh`。
3. **派发子代理之前先提交**（或先明确告知它"当前工作树未提交"）。子代理会在自己的会话里
   跑 git 命令收尾自己的临时改动；工作树未提交时，一条 `git checkout -- <file>`
   就把你手上的活打回 HEAD 了。本仓已发生过一次（`ui_smoke.mjs` 被抹回 HEAD）。
4. 前端改动想快速看一眼，用 **`bash tests/smoke-web.sh`**（约 4 秒：起临时服务 +
   跑 `ui_smoke.mjs` + 收尾）。它在门禁之外，跑完整门禁才用 `tests/run_all.sh`。

## Agent skills

### Issue tracker

工单以 markdown 文件形式存放于本仓库的 `.scratch/` 目录。详见 `docs/agents/issue-tracker.md`。

### Domain docs

单上下文（single-context）布局。当前 ADR 见 `开发文档/06-里程碑与风险.md` 第 4 节，稳定态设计见 `开发文档/`。详见 `docs/agents/domain.md`。

### Coding standards

评审期（`code-review` 的 Standards 轴）的判断题标准见 `CODING_STANDARDS.md`。
