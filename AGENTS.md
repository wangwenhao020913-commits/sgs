# AGENTS.md

本文件记录本项目（SGS）中所有协作者与自动化 Agent 必须遵守的注意事项。

## 注意事项

1. 每次改动后，必须编写或更新相关测试，并在交付前跑通测试和验证。
2. 唯一门禁是仓根的 **`bash tests/run_all.sh`**（全部 shell 校验 + pytest，约 2.5 分钟）。
   **没有 CI、也没有默认 hook 会替你跑它。** 先跑一次 `bash tools/install-hooks.sh`
   把它接到 pre-commit 上（改动落在 `src/` 或 `tests/` 时才触发）。

## Agent skills

### Issue tracker

工单以 markdown 文件形式存放于本仓库的 `.scratch/` 目录。详见 `docs/agents/issue-tracker.md`。

### Domain docs

单上下文（single-context）布局。当前 ADR 见 `开发文档/06-里程碑与风险.md` 第 4 节，稳定态设计见 `开发文档/`。详见 `docs/agents/domain.md`。

### Coding standards

评审期（`code-review` 的 Standards 轴）的判断题标准见 `CODING_STANDARDS.md`。
