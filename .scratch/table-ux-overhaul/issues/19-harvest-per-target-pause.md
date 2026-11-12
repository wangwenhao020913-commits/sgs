# 19: 五谷取牌逐目标可见——`drive()` 在每名目标取牌后交还控制权

**What to build:** 让浏览器**看得见**每一次五谷取牌：AI 目标那一张被拿走之后，下一次视图
刷新就能读到"那张已置灰 + 座N、目标条翻成已拿"，而不是等到整段结算结束才跳变。
做法 = 接入层在**每名目标取牌后**暂停驱动（与票 14 的 L0 边界暂停同一种机制），
客户端下一次 `fetchView()` 拿到中间状态。

**Blocked by:** None（票 18 已 resolved）
Status: resolved
Type: task
Touches: tools/sgs_web/session.py, tests/test_web.py, tests/test_web.sh, tests/support/ui_smoke.mjs
（实施期扩面见 `## Answer` 的「Touches 扩面」：引擎的驱动判据 / `serve.py` / 两个 static 脚本 /
烟测桥 / 三条既有用例的驱动循环。票面只列了接入层与断言，实际改动面由「一次响应最多一个暂停点」
与「没有询问时空窗该不该开窗」两件事逼出来。）
Ruling: 用户 2026-11-11 实机报障 + `.scratch/table-ux-overhaul/map.md` 缺口 #癸；票 18 的 `## Answer`「残余缺口」小节（方案 ①）

## 根因（票 18 的一手证据）

AI 目标的取牌由 `Session.drive()` **同步答完**：一局五谷的整段结算在服务端一次驱动里跑完，
浏览器只拿到最后一批记录；`view.harvest`（票 18 的服务端 D 方案）在**帧还活着**时是对的，
但 AI 取牌完成时帧已出栈 ⇒ 下一次视图里 `harvest` 是 `None` ⇒ 亮牌面板整段不变。
（人类自己的取牌那一问仍可见——它本身就是一条 pending。）

## 要做什么

1. **逐目标暂停**：`Session.drive()` 的停点再加一条"**五谷帧还在、且刚有一名目标取走了
   牌**"——判据读**记录流事实**（本批里出现 `card_move` 且 `from.zone == processing`、
   `to.zone` 是手牌/弃牌，且五谷帧仍活），不要靠"再跑一步看看"。
   暂停后由客户端下一次 `fetchView()` 继续（现有 `after` 游标，零新协议）。
   - 与票 14 的 `_l0_boundary_reached` 同姿态：置一个内部标志，下一次 `drive()` 不再因
     **同一条**记录停第二次。
   - **不得**在有人类 pending 时暂停（那时本来就该停）；**不得**改变记录流 / 随机数 /
     询问条数（纯驱动节流）。
2. **客户端不改判据**：亮牌节点的数据仍全部来自 `view.harvest`（票 18 已接好）；本票只让
   中间状态**到得了**浏览器。若一次暂停里记录较多，播放节奏沿用既有分批动画。
3. **不许改**：`view()` 的其它字段、票 14 的 L0 停点、2 s 轮询、五谷的引擎行为。

## 验收

- 新增（`tests/test_web.py`，真会话）：`test_each_harvest_target_pick_is_observable_in_a_view`
  （4 座、五谷、AI 目标：连续两次 `fetchView()` 之间 `view.harvest.revealed[*].taken_by`
  至少多一个非空项，且那次视图里 `harvest` 非空——这正是票 18 缺的断言）。
- 新增（`tests/test_web.py`）：`test_the_harvest_pause_stops_once_per_target`
  （取 3 名目标 ⇒ 恰好 3 次中间视图，不多不少；每名目标只停一次）。
- 新增（烟测）：`U-HV-05` 两次轮询（无新 pending，只有新 `view.harvest`）⇒ 牌面置灰 +
  `座N`、目标条 `is-taken`；第三名目标再翻一次。
- 回归：`tests/test_web.sh`（真 HTTP tape）、`tests/test_web.py` 全绿；票 14 的
  `test_the_create_response_stops_at_the_end_of_game_start` 保持绿；AI 三份绿。
- 金录像：`bash tools/check_goldens.sh` 47 条逐字节不变（纯接入层节流）。
- `## Answer` 里写清：每名目标的视图读法、暂停判据、以及与 2 s 轮询的交互（不额外加延时）。

## 会红的既有断言

- `tests/test_web.py` 里"一次 `fetchView` 就跑到下一个 pending / 终局"的用例（本票改变了
  接入层节奏）——逐条核，按"多了中间视图、内容同源"改写，**不许**放宽成"跳过中间状态"。
- `tests/support/web_client.py` 的 tape 驱动可能需要跟上（新的一批记录里 `harvest` 非空）。

## Answer

### 改动摘要（按"谁停在哪一步"读）

1. **引擎只多一个驱动判据**（`src/sgs/engine/game.py:Engine.run`）：新增
   `stop_when: Callable[[], bool] | None = None`，每个 `_step` 之后问一次"要不要让出"，
   为真就返回。缺省 `None` ⇒ 金录像 / `batch_games` / `ai_eval` / 录像回放逐字节不变
   （它们都不传）。**它只改"这一次驱动停在哪"**：记录流、随机数、询问条数一条都不动。
2. **接入层（`tools/sgs_web/session.py`）**：
   - `drive()` 把 `stop_when=self._harvest_pick_pause_due` 交给引擎；`run` 正常返回的三种
     原因（终局 / L0 边界 / 五谷暂停）都算"这一趟停过了"，故一律返回、不在同一趟里续推。
   - `_harvest_pick_pause_due()` = 暂停判据（见下「暂停判据」），进度记在
     `_harvest_pause_mark`（`(链键, 已拿数)`），同一份进度不二次停。
   - `harvest_paused`（只读属性）+ `pause_delivered()`：暂停点**由一次视图下发消费**。
3. **HTTP 面（`tools/sgs_web/serve.py`）**：`advance_clock` 在 `harvest_paused` 为真时
   **不再推进**（否则一次应答把两个暂停点并进一份响应，前一次就看不见了）；
   `view()` 末尾调 `session.pause_delivered()`，三条路由（建局 / 取增量 / 应答）共用这一处。
4. **客户端不改判据、只补窗寿命**（票面「客户端不改判据」= `taken_by` / `status` 仍全部
   来自服务端派生）：
   - `reveal.js:idleHarvestState()`：**没有询问**时亮牌窗该不该显示（读 `view.harvest`；
     **不要求 `current`**——最后一名目标取走后没有当前目标，那正是"全部拿完"那一帧）。
   - `modal.js:dialogRevealSource()`：亮牌窗的数据源 = 有询问用询问，否则用视图级 harvest；
     `syncDialog` 的空闲分支据此把窗口**保持打开**（同链键、`persistent` ⇒ 不暗化），
     `applyDialogContentMode` 的持久分支 / 标题 / 提示都改读这个 source。
   - 窗口自己重画仍走**同一个** `renderReveal`；`view.harvest` 变 `null` 的那一拍照旧收窗。
5. **测试**：3 条新 pytest（真会话 + 真 `GameRegistry` 的 answer / advance_clock / view 三条
   产品路由）+ 烟测 `U-HV-05a..d`；改写"驱动到终局"的循环（它们现在要知道"没有 pending"
   也是合法态）。
6. **文档**：`docs/agents/frontend-map.md` 的 `reveal.js` / `modal.js` 两行补上
   `idleHarvestState` / `dialogRevealSource` 与空窗窗寿命。

### 每名目标的视图读法

| 事实 | 服务端字段 | 中间视图长什么样 |
|---|---|---|
| 牌被谁拿走 | `view.harvest.revealed[*].taken_by` | 每停一拍**恰好多一个非空**（4 座：`1→2→3→4`） |
| 目标条四态 | `view.harvest.targets[*].status` | 已拿 `taken` / 当前 `current` / 未轮到 `pending`；最后一帧全 `taken` |
| 当前目标 | `view.harvest.current` | 一路前移，最后一帧 `null`（结算收尾，不再有"在等谁"） |
| 链身份 | `view.harvest.key` | 整段结算一个键（`harvest:<帧>:<牌>:<使用者>`），窗口不关不闪 |
| 这一拍有没有询问 | `pending` | **`null`**——中间视图不是询问；人类自己取牌那一问的**应答**就是第一个暂停点 |

界面读法（真会话由 `test_each_harvest_target_pick_is_observable_in_a_view` 钉）：
人类取一张之后，`fetchView()` 返回的那一份里 `harvest` 非空、`pending` 为 `null`；
此后每次取增量，`revealed` 里多一张置灰 + `座N`，目标条同一刻翻成"已拿"、当前目标前移。

### 暂停判据

- **读的是同一份派生**：`projection.harvest_state(engine, cache=Session.harvest_cache)`——
  与 `view.harvest` / `pending.harvest` 同一份（票 18 的服务端 D 方案），并共用同一个扫描缓存。
  这里只读 `targets[*].status` 里 `taken` 的**个数**。
- **停点落在"目标条已翻成已拿"那一帧**：`status == taken` 只有在帧游标推进之后才成立，
  所以停点天然在 `resolve_advance` 之后（而不是 `pick_harvest` 搬牌那一步）。若在搬牌一步停，
  该目标的 `status` 还是 `current`、目标条要晚一拍才翻——正是报障观感。
- **一次取牌只停一次**：进度 = `(harvest.key, taken)`；同一份进度不二次停
  （与票 14 的 `_l0_boundary_reached` 同姿态）。换一局五谷结算（键变）自然重新起算。
- **有人类 pending 时不在这里停**：此时 `run` 的下一轮本来就会驱动到人类询问；
  但**进度照样记下**（那一问的视图已经带着这份派生，多停一次只会多一份没有新信息的视图）。
- **弃牌目的地不进判据**：五谷帧活着时 `processing → discard` 不会发生（`finish_harvest`
  跑在 **USE 帧**上、`frame_id` 不是五谷 `RESOLVE` 帧的 id；那时五谷帧也已出栈）——
  票面写的"手牌/弃牌"在实现里只需认"手牌"，弃牌那半由 `frame_id` 与帧存活条件天然排除。
- **不改记录流 / 随机数 / 询问条数**：`test_the_harvest_pause_is_pure_driving_throttling`
  把同一局（同种子、同参与者）用 Session 逐目标让出跑一遍、用 `Engine.run` 一次跑完再跑
  一遍，**记录流逐字节相同**。

### 与 2 s 轮询的交互（不额外加延时）

- 一个暂停点 = **一次 HTTP 响应**；不新增计时器、不改轮询间隔、不改回放节奏
  （每一批记录照旧逐条播，暂停只是把批次切小）。
- 提交那条**应答自带的视图就是第一个中间视图**（人类自己那张牌已置灰、目标条第 1 枚已翻）。
  之后每次 2 s 轮询推进一个暂停点：`advance_clock` 驱动 → 又一名目标拿牌 → 让出 → 视图。
  4 名目标合计 4 份中间视图（最后一份全员 `taken`、`current = null`），第 5 次取增量才结算
  结束（`view.harvest` 变 `null`，窗在同一拍收起）。
- **一次响应最多一个暂停点**：`submit_answer` 已经停在暂停点上时，同一次响应的
  `advance_clock` 不再推进（`harvest_paused` 为真）；这份事实由 `view()` 的
  `pause_delivered()` 消费。于是"每个暂停点都真的交给浏览器一次"，不会两个并成一个。

### 会红的既有断言（本票故意改写，判据一条都没放宽）

| 位置 | 改法 |
|---|---|
| `tests/test_web.py:_play_to_end` | 没有 `pending` 且未终局 ⇒ 再 `drive()` 一次（真客户端就是再取一次增量） |
| `tests/test_web.py` 三处"驱动到终局"取样循环（RESPOND 样本 / 候选索引 / 索引无规则） | 同上：`while not finished`，`pending is None` 时 `drive()` 并 `continue` |
| `tests/support/web_client.py`：`play` / 火攻 tape / 酒 tape（濒死）/ 固定 tape | 新增 `settle_pauses(...)`：没有 `pending` 时逐次 `GET` 取到下一个挂起 / 终局（中间记录照旧收账） |
| `tests/support/ui_smoke.mjs`：U2-02 真对局驱动 / U2-R11 取牌窗口循环 / M6-B1 收窗批 | 前者取一次增量再提交；M6-B1 的判据从"下一个 pending 不是五谷"改成"**视图级 `harvest` 变 `null`**"（`pending` 为 null 还可能只是暂停） |
| `tests/test_armor.py` / `tests/test_m5_no_leak.py`（3 条）/ `tests/test_wire_contract.py` | 同一句：没有挂起就再驱动一次（否则会**静默**少取样 / 少走一半对局） |

### 验收条目 → 断言

| 验收 | 断言 | 位置 |
|---|---|---|
| 每名目标取牌都能在视图里读到（4 座 / AI 目标） | `test_each_harvest_target_pick_is_observable_in_a_view` | `tests/test_web.py` |
| 取 3 名目标 ⇒ **恰好** 3 次中间视图（每名只停一次） | `test_the_harvest_pause_stops_once_per_target` | `tests/test_web.py` |
| 纯驱动节流（记录流逐字节不变） | `test_the_harvest_pause_is_pure_driving_throttling` | `tests/test_web.py` |
| 烟测：空窗两次"轮询"⇒ 置灰 + 座N + 目标条翻态；第三名再翻；结算结束收窗 | `U-HV-05a` / `U-HV-05b` / `U-HV-05c` / `U-HV-05d` | `tests/support/ui_smoke.mjs` |
| 真 HTTP tape（建局 / 8 人正局 / 160 张 / 火攻 / 酒 / 固定 tape） | `bash tests/test_web.sh` | 见下 |
| 票 14 的 L0 停点不变 | `test_the_create_response_stops_at_the_end_of_game_start` | `tests/test_web.py`（保持绿） |
| 金录像 47 条不变 | `bash tools/check_goldens.sh` | 见下 |

### 门禁结果

- `.venv/bin/pytest -q` ⇒ **3942 passed**（562.9 s，全量）。
- `bash tests/smoke-web.sh` ⇒ `PASS: 前端渲染冒烟`（含 `U-HV-05a..d`；`U2-R11/12/13`、`U-HV-04`、`M6-B1` 按新节奏改写后全绿）。
- `bash tests/test_web.sh` ⇒ `PASS: M4 端到端门禁全部通过`（8 人正局 / 军争 160 / 409 / 火攻 / 酒 / 构造 tape / `--host 0.0.0.0`）。
- `bash tools/check_goldens.sh` ⇒ `PASS: 47 条标准 108 金录像逐字节不变`。
- 判据代价（全 AI 自对弈一局 1593 条记录）：`_harvest_pick_pause_due` 调用 **1424 次**、
  总耗时 **6.33 ms**（≈ **4 µs/次**；没有五谷帧时是"取栈 → 早退"）。没有新增长扫。
- 提交时由 pre-commit hook 跑全量 `tests/run_all.sh --quiet`（结果见本次提交）。

### Touches 扩面（实施期裁定，票面外但被逼出来的面）

| 面 | 为什么必须动 |
|---|---|
| `src/sgs/engine/game.py` | 停点住在 `run` 的循环内部（`_step` 之后那一刻外面看不见）；与票 14 的 `stop_after_game_start` 同一种机制、同一个参数风格 |
| `tools/sgs_web/serve.py` | "一次响应最多一个暂停点"与"视图下发即消费"只能落在 `advance_clock` / `view` 两处 |
| `tools/sgs_web/static/{reveal,modal}.js` | 空窗里 `pending` 是 `null`，原 `syncDialog` 会**关窗**——不补这一段，"到得了浏览器"的中间状态仍然看不见（票面「客户端不改判据」指 `taken_by`/`status` 仍全部来自服务端，不是"客户端一个字都不改"） |
| `tests/support/{ui_bridge.js,web_client.py}` | 烟测桥要暴露 `idleHarvestState`；HTTP tape 的驱动循环要知道"没有 pending 也是合法态" |
| `tests/test_{armor,m5_no_leak,wire_contract}.py` | 同上的驱动循环（不改会**静默**少取样） |

### 实机验收配方（`bsk` 在本机**不可驱动** ⇒ 交用户）

`bash tools/check_env.sh` 报 `PASS: bsk 可用`（静态预检只看插件在不在），但实机与 `map.md`
缺口 #戊 记录的同一个故障：`browser_session start` 成功、`browser_observe` 能读 DOM，
而所有 `browser_interact`（select / fill / click）都被
`This action requires the context returned by the latest cooperate sync` 拒绝——DSh 这一侧
拿不到 cooperate 上下文。按仓规改为**配方 + 结束 turn**。

```bash
# 起服务（托管后台任务；不要 nohup / &）
.venv/bin/python tools/sgs_web/serve.py --host 127.0.0.1 --port 8130 --quiet   # 打印 URL
```

1. 开局页：**人数 4**、**种子 15**（`tools/acceptance_seed.py --players 4 --card harvest`
   查得起始手牌含【五谷丰登】）、座位 0、AI **random**、思考时限 **0**（关）。
2. 等轮到自己（主公坐 0 ⇒ 第一回合就是自己）：摸牌 → 出牌阶段，点手牌里的**【五谷丰登】**，
   目标 = 全体（直接确认）。
3. **看四件事**：
   ① 亮牌窗里目标条在牌面下方（票 18 的口径不回退）；
   ② **点一张亮牌**（一击提交）之后**立刻**：你那张牌置灰 + `座0`，目标条第 1 枚 `座0✓`、
      第 2 枚 `座1◀`——窗**不关**；
   ③ 之后**每约 2 秒**（一次轮询）再多一张牌置灰 + `座N`，目标条同一刻再翻一枚；最后一名
      目标拿完后**仍是**"全员 ✓、没有 ◀"那一帧（窗还开着，`等待其他座位拿牌…`）；
   ④ 再一拍（2 秒）窗收起、回到自己的出牌阶段——**没有**"整段不变然后一下跳到结算结束"。
4. 反向自查：五谷结算完之后 `#t-dialog` 必须已经 `hidden`；中途折叠 / 展开照旧可用。
