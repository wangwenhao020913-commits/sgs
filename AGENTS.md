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
   把它接到 pre-commit 上：`src/` / `tests/` 改动跑全量，`开发文档/` / `GLOSSARY.md` /
   `AGENTS.md` / `CODING_STANDARDS.md` / `docs/` 改动跑**快速文档检查**（分级在 `tools/gate-staged.sh`）；**合并提交**
   由同源的 `.githooks/pre-merge-commit` 覆盖（`git merge` 不触发 pre-commit）。
   hook 的全量档走 **`tests/run_all.sh --quiet`**：只打每条检查一行 PASS 与最终结果
   （成功摘要约 **227B**），失败时**照旧补打失败项的完整输出**；**人工跑全量用不带参数的
   `bash tests/run_all.sh`**（全部 PASS 行与 pytest 输出）。`--quiet` 是 hook / agent 的省流
   档，门禁判据不变。
   **放后台跑**：它超过多数前台工具调用的超时（前台跑会被打断、输出丢掉）；用后台任务
   起它，再取输出。**装好 hook 后**，提交时 hook 会替你跑对应级别，别在提交前手动重跑一遍
   （每票白等 3 分钟）；迭代期要快就用**单文件子集**：**`.venv/bin/pytest <file>`** ——
   `python` 不在 PATH，门禁也只认 `$ROOT/.venv/bin/pytest`、不回退系统 pytest —— 或
   `bash tests/smoke-web.sh`。**全量失败先 `grep -nE 'FAIL|失败' <log>`** 看完全部失败，
   不要只 `tail`。
3. **派发子代理之前先提交**（或先明确告知它"当前工作树未提交"）。子代理会在自己的会话里
   跑 git 命令收尾自己的临时改动；工作树未提交时，一条 `git checkout -- <file>`
   就把你手上的活打回 HEAD 了。本仓已发生过两次：`ui_smoke.mjs` 被抹回 HEAD；票 13 的实现者
   用 `git stash` 做"改前对照"，把自己的活从工作树里清掉（靠 `git fsck` 找回游离对象）。
   **要对照基线就走安全通道**：`bash tools/baseline-worktree.sh` —— 它在仓外另开一棵 worktree，
   绝不动你手上的工作树。
   **linked worktree 共用 `.git/config`**：`SGS-retro`（M5 retro，分支 `retro/m5-env`）是
   SGS 仓的 linked worktree，**与主工作树共用 `SGS/.git/config`**。在那边跑环境探针只许读
   （`git config --get ...`），**不许写** `core.bare` / `user.name` / `user.email`；写坏后主
   工作树的 git 会一起报 `fatal: this operation must be run in a work tree`。
   `bash tools/check_env.sh` 现在对 `core.bare=true` 或 `probe` 身份**硬失败**。真碰到那句
   fatal，先 `git config --get core.bare` 确认，再按 `check_env` 的修复清单恢复
   `core.bare=false` 与身份 `dsh-agent <dsh-agent@localhost>`。
4. 前端改动想快速看一眼，用 **`bash tests/smoke-web.sh`**（约 4 秒：起临时服务 +
   跑 `ui_smoke.mjs` + 收尾）。它在门禁之外，跑完整门禁才用 `tests/run_all.sh`。
   **实机验收**：改动 DOM API 的 UI 票（`children` 形状、节点结构、模板、选择面等）在置
   `resolved` 前必须走一遍**真实浏览器**（`bsk`）；最小覆盖 = **进入处理区 / 舞台卡更新 /
   弃牌**。`bsk` 可用时由 **agent 自己驱动**（`browser_session` / `browser_page` /
   `browser_inspect` / `browser_interact`）；**只有 `bsk` 缺失时**才准备服务器 + 复现配方，
   然后**结束 turn**（不要用 `ask_user_question` 阻塞等结果），由用户手动验收。
   验收前先跑 **`bash tools/check_env.sh`**（现在含共享 `.git/config` 护栏）；后台验收用
   **`.venv/bin/python tools/sgs_web/serve.py --port 8080 --quiet`**（缺省端口 8100，8080
   只是习惯用法；服务把实际地址打到 stdout；`--quiet` 只关逐请求访问日志）。配方见
   `docs/agents/acceptance-web.md`。
5. **派发**：同一 checkout 同时只能有一个实现者。确需并行时，每个实现者只
   `git add <显式路径>`，禁止 `git add -A` 与带 `-am` 的提交；任何人不得对别人未提交的改动做
   `git checkout --` / `git stash` / `git reset`。并行安全的前提是"改的文件不重叠"，而它只能
   从**显式路径**读出来——`git add -A` 会把别人的半成品卷进你的提交（本轮真踩过：两张票同时
   改 `virtual.py`，被迫撤回）。
6. **长跑检查点**：预计超过约 30 分钟的票，实现者在过半时落一个 `wip:` 提交（交付前 squash
   或 amend 掉）；机器中断时，工作树就是恢复点。本仓丢过一整轮上下文，靠工作树里的半成品
   才接上。
7. **写回 / 验收工具**：改完 `06`（§5「程序化约定与偏离一手」）先跑
   **`.venv/bin/pytest -q tests/test_writeback_landing.py && bash tests/test_dev_docs.sh`**，
   再跑全量；找该节裁定行用 **`.venv/bin/python tools/ledger_index.py --grep <票号/关键词>`**；
   实机验收找种子用 **`.venv/bin/python tools/acceptance_seed.py --players 4 --card <type>`**
   （只读起始手牌；配方写进验收票的 `## 复现配方`）。

## Agent skills

### Issue tracker

工单以 markdown 文件形式存放于本仓库的 `.scratch/` 目录。详见 `docs/agents/issue-tracker.md`。

### Domain docs

单上下文（single-context）布局。当前 ADR 见 `开发文档/06-里程碑与风险.md` 第 4 节，稳定态设计见 `开发文档/`。详见 `docs/agents/domain.md`。

### Coding standards

评审期（`code-review` 的 Standards 轴）的判断题标准见 `CODING_STANDARDS.md`。

### Frontend / Web

改 `tools/sgs_web/static/` 或验收前端行为前，先按这三份读：

- `docs/agents/frontend-map.md`：脚本职责、舞台 / live view / 轮询 / 动效入口、测试垫片与
  烟测清单——先定位文件，不要从头通读 `session.js`。
- `docs/agents/acceptance-web.md`：实机验收配方（环境预检、起服务、browser-skill、种子 12）。
- 评审口径：`CODING_STANDARDS.md` §4（次序断言）与 §6（读取方断言走真实产出路径）。
