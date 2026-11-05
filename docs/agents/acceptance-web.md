# Web 实机验收配方（Acceptance: Web）

**何时用**：改动 DOM API 的 UI 票（`children` 形状、节点结构、模板、选择面等）在置
`resolved` 前，用真实浏览器按本配方走一遍。最小覆盖 = **进入处理区 / 舞台卡更新 / 弃牌**。
这是**配方**，不是规格：验收判据住各 effort 的 spec，结论住 `.scratch/<effort>/acceptance.md`
（见 `docs/agents/domain.md`），票面写 `## 复现配方` 与 `## Answer`。

## 1 · 环境预检

```bash
bash tools/check_env.sh
```

- 共享 `.git/config` 被污染（`core.bare=true` 或 `probe` 身份）是**硬失败**：先按它打印的
  修复清单恢复，再继续——否则全仓 git 都不可用（`SGS-retro` 是共用配置的 linked worktree）。
- `bsk` 缺失 = browser-skill 不可用：**agent 不驱动浏览器**，准备好服务器 + 本配方后
  **结束 turn**，交给用户手动验收；不要用 `ask_user_question` 阻塞等结果。
- `bsk` 可用：由 agent 自己驱动（第 3 节）。

## 2 · 起服务

在 SGS 仓根起。优先用生命周期脚本（M5 retro；pid / log 在 gitignored 的 `runs/`）：

```bash
bash tools/serve-dev.sh start 8080        # 起并等到端口可连；打印 URL
bash tools/serve-dev.sh status 8080       # 在跑？pid / URL
bash tools/serve-dev.sh stop 8080         # 收尾
```

它底层就是 `.venv/bin/python tools/sgs_web/serve.py --host 0.0.0.0 --port 8080 --quiet`，
log 在 `runs/serve-8080.log`。需要 effort 专属录像目录时再用原始命令：

```bash
.venv/bin/python tools/sgs_web/serve.py --port 8080 --quiet \
  --runs-dir .scratch/<effort>/runs > .scratch/<effort>/serve.log 2>&1 &
```

- 启动行形如 `http://127.0.0.1:8080/`；以实际打印的地址为准。缺省端口是 **8100**，
  8080 只是习惯用法。端口被占就用 `--port 0`（内核分配）或换一个，不要杀未知进程。
- `--quiet`（别名 `--no-access-log`）只关逐请求访问日志，启动行与行为不变。
- **agent 沙箱里改用受管后台任务**：`serve-dev.sh start` / `nohup` / `setsid` 起的进程会随
  命令退出一起被回收（跨调用看不到 pid，`status` 报 stale）。改用
  `exec .venv/bin/python tools/sgs_web/serve.py --host 0.0.0.0 --port 8100 --quiet` 作为
  **受管后台任务**（`run_in_background`），验收结束用 `job_kill`；此时 pid / log 不落
  `runs/`，`serve-dev.sh status|stop` 管不到它。
- 静态脚本是**每请求从磁盘读**的：改 `static/*.js` 后浏览器强刷即可，不必重启；只有改了
  `serve.py` / `projection.py` / `visibility.py` 等接入层 Python 才重启服务。

## 3 · 浏览器操作（browser-skill）

```text
browser_session start            # 打开 Agent Window（可带初始 URL）
browser_page navigate            # 到 http://127.0.0.1:8080/
browser_inspect observe          # 读当前可达节点与文本
browser_inspect screenshot       # 看视觉效果（动效 / 舞台 / 布局）
browser_interact click|fill|press # 点座位 / 手牌 / 按钮，填输入框
```

- 每次页面有实质变化后**重新 observe**，再用新的 ref 操作。
- **`browser_inspect html` 会序列化整页**（含全部座位 / 手牌 / 覆盖层，几百 KB，常被截断）：
  只在必须读 DOM 属性 / 类名时用，并尽量带 ref 或选择器做**有界**读取；日常观察用
  `observe` / `screenshot`。
- `#t-skill-floats` 与 `#t-target-lines` 是**非交互覆盖层**（`pointer-events: none`，
  技能名 / 目标线的动画层），不要试图点它们；它们的窗口只有 0.4–2 秒，**截图**比读 DOM 可靠。
- 截图证据放 `.scratch/<effort>/screenshots/`，文件名带票号与场景。

## 4 · 种子 12 配方

| 项 | 值 |
|---|---|
| 人数 | 4 |
| 种子 | `12` |
| 牌堆档位 | `standard_ex_160`（军争 160） |
| 我的武将 | 周瑜（`zhouyu`），勾「开局点将」；其余随机 |
| 思考时限 | `0`（主流程）；超时代答项另开一局，`10` 秒 |

起始手牌（`acceptance_seed.py --players 4 --card fire_attack --deck-profile standard_ex_160`
扫得，实测一致）：**♦Q 火攻、♠2 寒冰剑、♦8 闪、♣4 过河拆桥**。

操作顺序：

1. 按上表开局，进入牌桌。
2. 摸牌阶段点「提交」发动【英姿】（多摸一张），手牌 7。
3. 点【火攻】→ 点孙权 → 提交 → 无懈窗口点「放弃」。种子 12 的孙权可能对火攻用无懈
   （可先耗掉，或直接观察无懈进入处理中组）。
4. 点【反间】→ 预发动清单选孙权 → 确认发动；观察技能名 / 白光 / 技能目标线，以及孙权获得
   一张手牌并展示（展示组）。
5. 点【过河拆桥】→ 选孙权 → 提交 → 无懈窗口放弃 → 在「座位1 手牌（牌面未知，共 4 张）」
   中**盲选**一张；观察弃牌组与底行 `1·孙权·手牌`。
6. 点【寒冰剑】→ 提交装备；自己的武器槽应立即出现（live view）。
7. 点「结束出牌」→ 弃牌阶段选 2 张提交；观察弃牌组 1s 停留 + 1s 淡出。让 AI 行动，
   观察 AI 的多目标牌（如【南蛮入侵】）逐目标画线。
8. 静默轮询：出牌阶段点一张手牌但**不提交**，停 3–5 秒（短于时限）；选择 / 焦点 / 预发动
   本地态不应被清掉。
9. 超时代答：另开一局同种子、思考时限 `10` 秒；初始【英姿】询问**不操作**，等 10–15 秒，
   观察询问自动推进；出牌 / 弃牌阶段同理（本局实测会自动结束出牌、自动弃 2 张）。

## 5 · 证据与记录

本次 U3 的示例截图（可直接按同样场景复拍）：

- `.scratch/u3-stage-and-pacing/screenshots/07-nanman-multitarget-lines.png`
  （孙权【南蛮入侵】→ 三目标各一条金线 + 处理中底行）
- `.scratch/u3-stage-and-pacing/screenshots/07-dismantle-line-processing.png`
  （自己【过河拆桥】→ 孙权一条线 + 处理中底行）
- `.scratch/u3-stage-and-pacing/screenshots/07-wuxie-processing.png`
  （无懈可击入处理中组 + 底行）

把复现结果写进票的 `## Answer`（配方留在 `## 复现配方`），结论按 `docs/agents/domain.md`
汇总到 `.scratch/<effort>/acceptance.md`；不要在 spec / 设计稿里复制结论。
