# 贡献指南（CONTRIBUTING）

先读 [`AGENTS.md`](AGENTS.md)（协作与门禁的唯一真源）与 [`CODING_STANDARDS.md`](CODING_STANDARDS.md)
（评审口径）。这个仓库是**一票一提交、门禁唯一**的工程，下面是你需要知道的最小集。

## 1. 环境

Linux / macOS / WSL、**Python 3.14+**、**Node 20+**（只给前端烟测用）。

```bash
bash tools/setup-dev.sh     # 幂等：换行口径 + git hook + .venv + 环境预检
```

## 2. 门禁（唯一）

```bash
bash tests/run_all.sh                        # 全量：9 条 shell 校验 + pytest（≈8 分钟）
.venv/bin/pytest tests/test_web.py -q        # 迭代期只跑单文件
bash tests/smoke-web.sh                      # 前端快回路（起临时服务 + 真前端脚本，60–70 s）
```

- 提交时 `.githooks/pre-commit` 会按路径自动跑门禁：`src/` `tests/` → 全量，文档 → 快速文档检查；
  `git merge` 由同源的 `pre-merge-commit` 覆盖。hook 失败会拒绝提交，**不要用 `--no-verify`**。
- GitHub 上的 CI 跑**同一条**门禁（`.github/workflows/ci.yml` 只调 `bash tests/run_all.sh`），
  不另立第二套判据。

## 3. 改动纪律（详见 `AGENTS.md`）

- **一个事实一个落点**：技能只登记在 `src/sgs/skills/catalog.py`；可见面只有
  `tools/sgs_web/projection.py` 这一个过滤点；前端不推导规则（只读载荷）。
- **行为改动同批交测试**：改 `src/` 必须带 `tests/` 的断言；纯文档 / 纯数据改动可以不带。
- **金录像**：47 条标准对局逐字节冻结。行为变了就要显式重录，并在提交信息里说明"为什么"。
- **新技能 / 新武将**：先读 `docs/agents/skill-authoring.md`（六问流水线 + 13 条硬停判据），
  一将一票。
- **前端**：先读 `docs/agents/frontend-map.md`；没有构建步骤，`index.html` 的 `<script src>`
  顺序就是运行顺序（新增脚本必须同时进清单、并有 `"use strict";` 首语句）。

## 4. 提交流程

1. 开 Issue 说清要改什么（bug 用模板；行为标准请附牌面 / 规则出处）。
2. 从 `master` 切分支 → 改 → 门禁绿 → 提 PR（PR 模板会问"改了哪条判据 / 哪条金录像"）。
3. 一个 PR 只做一件事；合并用 squash，提交信息写"为什么"，正文写落点。

## 5. 不接受的内容

- **版权素材**：语音、卡图、武将画、规则全文。仓库现有的语音与规则文本是研究引用
  （见 [`NOTICE`](NOTICE)），不再新增同类文件；PR 里新增会被拒绝。
- 新的门禁入口（`Makefile` / `tox.ini` / `noxfile.py` / 覆盖率配置等）——`run_all.sh` 是唯一门禁，
  `tests/test_dod.py::test_there_is_no_second_gate_entry` 会判红。
- 不要把 `runs/`、`.scratch/`、`.venv/` 提交进来（已在 `.gitignore`）。

## 6. 环境相关的坑

- 门禁只认 `$ROOT/.venv/bin/pytest`，**不回退**到系统 pytest（缺了就报错，不静默跳过）。
- 换行：全仓 LF（`.gitattributes`），金录像是**逐字节**判据，CRLF 会整片变红。
- 卡牌语音是可选素材：没有也不影响运行（前端会静音），详见 `tools/sgs_web/static/voices/README.md`。
