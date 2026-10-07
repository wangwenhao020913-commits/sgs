# AGENTS.md

本文件记录本项目（SGS）中所有协作者与自动化 Agent 必须遵守的注意事项。

## 注意事项

1. 每次改动完成时，都必须创建一个对应的 git commit，以便后续追踪和回滚。
2. 每次改动后，必须编写或更新相关测试，并在交付前跑通测试和验证。

## Agent skills

### Issue tracker

工单以 markdown 文件形式存放于本仓库的 `.scratch/` 目录。详见 `docs/agents/issue-tracker.md`。

### Domain docs

单上下文（single-context）布局。当前 ADR 见 `开发文档/06-里程碑与风险.md` 第 4 节，稳定态设计见 `开发文档/`。详见 `docs/agents/domain.md`。
