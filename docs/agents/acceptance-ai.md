# AI 实机验收配方（Acceptance: AI）

**何时读**：一批规则工程收尾、打 tag 之前，用人 vs HEAD 打**一局**，回答"桌上刺不刺眼、
卡不卡、崩不崩"。判据住 `.scratch/ai-light/spec.md` §5，结论住
`.scratch/ai-light/acceptance.md`（验收结论的唯一真源）；本文件只是**配方**——票面与台账
只放指针，不复制结论。

## 1 · 定位与边界

- **实机 = 人机对局这一层**，只保留"人 vs HEAD"一臂。n=1 只否决"刺眼 / 卡 / 崩"，
  **不否决**统计结论：不产胜率、不产决策 diff、不进晋级的五条件。
- **不是门禁**：`bash tests/run_all.sh` 不跑它；跳过也能 tag，但版本台账的"一句话理由"
  要写"**未实机**"。
- **不加任何版本选择面**：网页现在就在打 HEAD（`_make_ai` 直接造启发式），接进网页零代码。
  `AI_TIERS` 是产品档位（会不会打牌），不是评估档位——不加 `--ai-version`、不加 lobby 下拉。
  想对照旧版手感走零代码路径：`bash tools/baseline-worktree.sh ai-vN` + 显式
  `PYTHONPATH=<worktree>/src` 起另一个端口的服务。
- **不触发 `bsk`**：这是用户驱动的人机对局，不是 DOM 验收；只有改
  `tools/sgs_web/static/` 或 DOM 契约才是 UI 票（那走 `acceptance-web.md`）。

## 2 · 配方表

| 项 | 值 | 为什么 |
|---|---|---|
| 人数 | 5（身份局） | 对齐评估口径；比 8 人局短 |
| 牌堆 | `standard_ex_160` | 军争牌（火杀 / 藤甲 / 酒 / 铁索）只有实机能看见 |
| 种子 | **`7001–7099` 带内轮换**（首版 7001，下一版 7002…） | 专用带：调参 / 判决批不读；与趋势带不重叠；判决批是 commit 派生的任意 64 位，撞不进来 |
| 人类座位 | 0 | 开局页缺省 |
| 武将 | 启用池随机、不点将、不禁将 | 走开局页缺省路径，不给"为了验收而挑将"留口子 |
| 思考时限 | 0（关） | 人类超时代答会把"你的选择"换成 UI 的兜底选择 |
| 身份 | 照发，摘要里记人类身份 | 不做身份覆盖——那是批量层的事 |

种子带**同时**登记在 `docs/agents/ai-versions.md` §3（"实机带"）与这里；派生批
（`tools/ai_eval/seeds.py` 的 `LIVE_SEED_RANGE`）主动跳过 7001–7099。

## 3 · 预检

```bash
bash tools/check_env.sh          # 共享 .git/config 护栏；红了先按清单修
git status --short               # 工作树干净：摘要的版本归属靠它
bash tests/run_all.sh            # 全绿（放受管后台任务跑，≈8 分钟）
```

`quick`（或 `promote`）已出结果、diff 已归因，再开始实机——实机不替代它们。

## 4 · 起服务

**沙箱里一律用受管后台任务**（`run_in_background`）。`serve-dev.sh start` / `nohup` /
`setsid` 起的进程会随命令退出被回收，跨调用看不到 pid：

```bash
exec .venv/bin/python tools/sgs_web/serve.py --host 0.0.0.0 --port 8100 --quiet \
    --runs-dir .scratch/ai-light/runs
```

- 启动行形如 `http://127.0.0.1:8100/`，以实际打印的地址为准；验收结束 **`job_kill`**。
- 录像落在 `--runs-dir` 指定的目录（一对 `.jsonl` + `.meta.json`）；**终局后写一次**，
  崩溃 / 非法动作不会留下终局录像——"`runs/` 里有没有新文件"本身就是第一读数。
- 改动 `static/*.js` 是每请求从磁盘读的，强刷即可；改接入层 Python 才需要重启。

## 5 · 出读数

终局后取最新一局，跑 `ai_eval live`：

```bash
ls -t .scratch/ai-light/runs/*.jsonl | head -1     # 取 <stem>（去掉 .jsonl）
PYTHONPATH=tools .venv/bin/python -m ai_eval live --run <stem> \
    --runs-dir .scratch/ai-light/runs
```

`PYTHONPATH=tools` 不可省。输出形如：

```text
实机 seed=7006 5p identity 160 人类=seat0(反贼) 终局=反贼胜 获胜者=[0,3]
询问=202 AI漏动作 pass=6/83(7.2%) composite=10/10(100.0%) 人类超时=0

| seq | 座位 | 身份 | 询问类型 | 当时事实 | 实际选择 | 期望选择 | D/S | 已知/新 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
```

- 前两行由工具出，**第三行与报账表由 agent / 人补**（模板见 §6）；工具不判 verdict、
  不打胜率、不打逐种子。
- 计数口径与 `quick` / `promote` / `seal` 同源（`tools/ai_eval/misses.py`）；`pass` 的分母 =
  **含非 Pass 选项的 AI 询问**，人类座位的 Pass 是他的选择、全部剔除；`composite` 的分母 =
  下发了预发动 / 转化 / 借刀附带指定清单的 AI 询问（清单只存在于重放出来的引擎询问里）。
- 退出码：0 = 读出；1 = 重放失败（这次没测成）；2 = 路径 / 录像内容错误。缺 journal / meta
  或两个文件不是同一局时**点名完整路径**失败，绝不静默打一行空摘要。
- 第三行模板里的版本归属要自己查：`git rev-parse HEAD` + 工作树是否 clean。第三行示例：
  `版本=HEAD/<sha>(clean) 崩溃=0 非法=0 报账=D1 S2`。

## 6 · 看什么

1. **硬失败**：崩溃 / 非法动作。读数 = 服务端 stderr 有无堆栈 + 是否留下终局录像 + UI 有无
   "取增量失败"。前两种失败都不会留下终局录像。
2. **AI 席位漏动作**：`ai_eval live` 的两个廉价指标（只统计 AI 座位）。人类座位的 Pass 是
   人的选择，不算漏；需要归因时按询问类型拆（`MissCounts.by_kind`）。
3. **桌面卡顿**：只记观感，**不加埋点**（每问耗时不在 journal 里，加埋点要动接入层）。
4. **人类报账**：用户边打边报"这里蠢"，agent 实时转写成报账表，每条记
   `seq / 座位 / 身份 / 询问类型 / 当时事实 / 实际选择 / 期望选择 / D/S / 已知|新`。

## 7 · 收尾与处置

- `job_kill` 服务；读数与结论写进 `.scratch/ai-light/acceptance.md`，票面 / 版本台账只放指针。
- **D 类（决策级）**：能写成"一个构造局面 + 一次询问 + 对答案的谓词" ⇒ 挂
  `xfail(strict=True)` 进 `tests/test_ai_cases.py`，先红后绿。
- **S 类（统计级）**：只能写成多局上的分布 ⇒ 进规则工程清单（预注册预测 + diff 归因），
  **不许**为它硬造单点用例。
- 跳过实机时，版本台账的"一句话理由"写"**未实机**"；批内中间 tag 写"未实机（批末补）"。
