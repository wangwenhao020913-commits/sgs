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
   - `drive()` 把 `stop_when=…` 交给引擎；`run` 正常返回的三种原因（终局 / L0 边界 /
     五谷暂停）都算"这一趟停过了"，故一律返回、不在同一趟里续推。**返回值** =
     "这一趟停在五谷逐目标暂停上"（`submit_answer` 原样透传）。
   - `_harvest_pick_pause_due()` = 暂停判据（见下「暂停判据」），进度记在
     `_harvest_pause_mark`（`(链键, 已拿数)`），同一份进度不二次停。
   - **没有会话级的暂停标志**：这个事实只活在一次调用栈里（`submit_answer` 返回 →
     HTTP 层据此不再补推）。跨请求的标志会要求"谁先下发视图谁清它"，而会话本来就不加锁
     （`ThreadingHTTPServer`）——把它做成返回值，那类竞态不存在。
3. **HTTP 面（`tools/sgs_web/serve.py`）**：`answer()` 拿到 `submit_answer` 的返回值，
   给 `advance_clock(..., drive=not paused)`（否则一次应答把两个暂停点并进一份响应，
   前一次就看不见了）。`view()` 是纯投影（不再改会话状态）。
4. **客户端不改判据、只补窗寿命**（票面「客户端不改判据」= `taken_by` / `status` 仍全部
   来自服务端派生）：
   - `reveal.js:harvestStateWithoutPending()`：**没有询问**时亮牌窗该不该显示（读 `view.harvest`；
     **不要求 `current`**——最后一名目标取走后没有当前目标，那正是"全部拿完"那一帧）。
   - `modal.js:dialogRevealSource()`：亮牌窗的数据源 = 有询问用询问，否则用视图级 harvest；
     `syncDialog` 的空闲分支据此把窗口**保持打开**（同链键、`persistent` ⇒ 不暗化），
     `applyDialogContentMode` 的持久分支读它；标题走 `dialogTitleSource(kind)`（只有亮牌
     模板读那份「只有 harvest」的最小载荷）。
   - `pending.js`：空窗里提示行写 `harvest.hint`（这一步是什么），不再说「（没有待应答的询问）」
     ——与弹窗标题条的「等待N号位拿牌…」不再打架。
   - 窗口自己重画仍走**同一个** `renderReveal`；`view.harvest` 变 `null` 的那一拍照旧收窗。
5. **测试**：3 条新 pytest（真会话 + 真 `GameRegistry` 的 answer / advance_clock / view 三条
   产品路由）+ 烟测 `U-HV-05a..d`；改写"驱动到终局"的循环（它们现在要知道"没有 pending"
   也是合法态）。
6. **文档**：`docs/agents/frontend-map.md` 的 `reveal.js` / `modal.js` 两行补上
   `harvestStateWithoutPending` / `dialogRevealSource` 与空窗窗寿命。

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
- **一次响应最多一个暂停点**：`submit_answer` 的返回值说明"这一趟已经有一个中间视图"，
  同一次响应的 `advance_clock` 因此不再推进。于是"每个暂停点都真的交给浏览器一次"，
  不会两个并成一个。这个事实**只活在一次调用栈里**（会话上不留标志）。

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
| 每份中间视图**恰好**带走一条取牌移动（不多停 = 不并批、不少停 = 不跳目标） | `test_the_harvest_pause_stops_once_per_target` 的 `_pick_moves == [1, 1, 1]`（读视图自带的 `records`） | `tests/test_web.py` |
| 暂停点是 `drive()` 的返回值（HTTP 层据此不补推） | 同一用例的 `session.submit_answer(...) is True` | `tests/test_web.py` |
| 烟测：空窗两次"轮询"⇒ 置灰 + 座N + 目标条翻态（提示行写这一步是什么）；第三名再翻；结算结束收窗 + 提示行回中性 | `U-HV-05a` / `U-HV-05b` / `U-HV-05c` / `U-HV-05d` | `tests/support/ui_smoke.mjs` |
| 真 HTTP tape（建局 / 8 人正局 / 160 张 / 火攻 / 酒 / 固定 tape） | `bash tests/test_web.sh` | 见下 |
| 票 14 的 L0 停点不变 | `test_the_create_response_stops_at_the_end_of_game_start` | `tests/test_web.py`（保持绿） |
| 金录像 47 条不变 | `bash tools/check_goldens.sh` | 见下 |

### 门禁结果

- `.venv/bin/pytest -q` ⇒ **3942 passed**（全量，实现后与评审修复后各独立复跑一次；
  修复那次由提交 hook 的 `tests/run_all.sh --quiet` 再跑一遍）。
- `bash tests/smoke-web.sh` ⇒ `PASS: 前端渲染冒烟`（含 `U-HV-05a..d`；`U2-R11/12/13`、
  `U-HV-04`、`M6-B1` 按新节奏改写后全绿）。
- `bash tests/test_web.sh` ⇒ `PASS: M4 端到端门禁全部通过`（8 人正局 / 军争 160 / 409 /
  火攻 / 酒 / 构造 tape / `--host 0.0.0.0`；评审修复后再跑一次仍全绿）。
- `bash tools/check_goldens.sh` ⇒ `PASS: 47 条标准 108 金录像逐字节不变`。
- `.venv/bin/python tools/check_acceptance.py .scratch/table-ux-overhaul` ⇒ `PASS`（19 张票 /
  110 个引用测试名全部存在且绿）。
- 判据代价（全 AI 自对弈一局 1593 条记录）：`_harvest_pick_pause_due` 调用 **1424 次**、
  总耗时 **5.61 ms**（≈ **4 µs/次**；没有五谷帧时是"取栈 → 早退"）。没有新增长扫。
- 两次提交都由 pre-commit hook 跑全量 `tests/run_all.sh --quiet` ⇒ `ALL TESTS PASSED`
  （`5139275` 与 `50e67f2`）。

### Touches 扩面（实施期裁定，票面外但被逼出来的面）

| 面 | 为什么必须动 |
|---|---|
| `src/sgs/engine/game.py` | 停点住在 `run` 的循环内部（`_step` 之后那一刻外面看不见）；与票 14 的 `stop_after_game_start` 同一种机制、同一个参数风格 |
| `tools/sgs_web/serve.py` | "一次响应最多一个暂停点"与"视图下发即消费"只能落在 `advance_clock` / `view` 两处 |
| `tools/sgs_web/static/{reveal,modal}.js` | 空窗里 `pending` 是 `null`，原 `syncDialog` 会**关窗**——不补这一段，"到得了浏览器"的中间状态仍然看不见（票面「客户端不改判据」指 `taken_by`/`status` 仍全部来自服务端，不是"客户端一个字都不改"） |
| `tests/support/{ui_bridge.js,web_client.py}` | 烟测桥要暴露 `harvestStateWithoutPending`；HTTP tape 的驱动循环要知道"没有 pending 也是合法态" |
| `tests/test_{armor,m5_no_leak,wire_contract}.py` | 同上的驱动循环（不改会**静默**少取样） |

### AI 适配（`docs/agents/ai-adaptation.md` §1）

**不适用**：本票没有新增 `QueryKind`、没有改动作面（`legal_actions` / 复合候选一个字段没动）、
没有新增或遮挡可见区。引擎那边只是给 `run` 多一个**驱动**判据（谁传它由调用方决定，AI 侧
`batch_games` / `ai_eval` / 录像回放都不传），`Query` / `PlayerView` / 记录流的读点一个都没变；
记牌守恒量（53 / 108 / 160）与身份推断的输入不变。客户端那段是纯显示窗寿命（数据仍来自
`view.harvest`）。

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

### `## 评审处置`（`/code-review` 双轴，范围 `0bce39c...5139275`）

两轴各出一份报告，处置如下（修复全在工作树，见本票的后续提交）。

| 轴 | finding | 处置 |
|---|---|---|
| Standards H | "一次响应最多一个暂停点"靠一个跨请求的会话布尔，`ThreadingHTTPServer` 下无锁 ⇒ 响应对不上号 | **已改**：删掉 `harvest_paused` / `pause_delivered` / `view()` 的副作用；事实改成 `drive()` / `submit_answer()` 的**返回值**，`answer()` 据此给 `advance_clock(drive=not paused)`。同一调用栈，无跨请求状态 |
| Standards H | `_l0_boundary_reached` 在**任何**正常返回上都置真，注释却宣称"L0 边界" | **已改**：先判"这一趟是不是五谷暂停"，是就不碰 L0 标志（`stop_when` 若在首次驱动就让出，票 14 的建局停点不会被吞掉） |
| Standards H | `test_m5_no_leak.py` 的三条 sweep 在 `pending is None` 时 `drive(); continue`，暂停那几拍**没有被查**，与 docstring 的"每一次"矛盾 | **已改**：空窗里**快照照样过** `_assert_snapshot`（没有询问载荷，但下发面还在）；三条 sweep + `test_armor` / `test_wire_contract` 补上驱动步上限（活锁现在是红，不是挂死） |
| Standards J | 引擎 `stop_when` 的 docstring 写"只读引擎已有的事实"，不可机械核 | **已改**：写成"**判据不得改引擎状态**"（可审的那一半） |
| Standards J | 空窗窗寿命没有 `applyViewRaw + stepPlayback` 的**批中**用例（票 18 的 §7 要求） | 见下「未做（如实记录）」 |
| Standards J | `web_client.py` 的 settle 循环复制了 4 处 | **已改**：`settle_pauses(..., visit=…)` 返回 `(body, polls)`，四个调用点各自只留一句 |
| Standards J | `dialogRevealSource()` 把"只有 harvest 的合成载荷"喂给所有模板的标题 | **已改**：新增 `dialogTitleSource(kind)`——只有亮牌模板读它，其余模板照读 `pending` |
| Standards J | `idleHarvestState` 名不副实（说"空闲"，实为"没有询问"） | **已改**：改名 `harvestStateWithoutPending`，docstring 写明与 `poll()` 的空转分支不是一回事 |
| Standards J | `assert session.harvest_paused is False` 是重言式 | **已删**：标志已不存在；改断 `submit_answer(...) is True` 与 `_pick_moves == [1,1,1]` |
| Standards J | 暂停期 `#t-prompt` 写「（没有待应答的询问）」，与弹窗"等待N号位拿牌…"打架 | **已改**：`renderPending` 的提示链补上 `view.harvest.hint`（空窗里说这一步是什么），`U-HV-05a` 钉文案、`U-HV-05d` 钉收窗后回中性文案 |
| Standards J | `frontend-map` 漏了决定性的 `syncDialog` 一跳 | **已改** |
| Spec 1 | 「不得在有人类 pending 时暂停」没有用例钉住 | **部分**：判据里的 `pending` 守卫是**防死锁**的安全条件（一个 `_step` 同时落取牌移动与 AI 询问时，若在这里停，`run` 会带着 AI 的 pending 退出、下一趟 `drive` 永不推进）。构造这种局面需要 MOVE_AFTER 类钩子，`_harvest_game` 的两个局面都没有；本轮按"注释写明为什么存在"处理，不造假夹具 |
| Spec 2 | 「弃牌不进判据」只有论证没有断言 | **已改**：3 目标用例的 `_pick_moves == [1, 1, 1]` 从**记录**侧面钉住"恰好三条取牌移动"——结算收尾的 `processing → discard` 若也停，就会是 4 份视图 / 多一条移动 |
| Standards J | 引擎 `stop_when` 的**问时机**没有引擎级用例 | 接受（已由上层钉住）：如果引擎在 `_step` **之前**问、或问完不让出，逐目标的"已拿数"序列就会整段错位——`[1,2,3,4]` / `[1,2,3]` / `[[taken,current,…], …]` 三组字面序列会当场红。再加一条只读引擎的用例不会增加覆盖 |
| Spec 3 | 3 目标靠 `dead_seat` 夹具而非真发牌 | 接受：与票 18 的既有夹具同源；`get/finished` 的随机局里有真发牌覆盖（`_play_to_end` 那个测试助手驱动的那批） |
| Spec b | `tests/test_web.py` 顺手把 ~20 处 `GameRegistry(runs_dir=…)` 改成 `_registry()`，属可避免的 churn | **已改**：除票 19 的新用例，其余调用点恢复原样 |

**未做（如实记录）**：空窗窗寿命的"批中"用例（`applyViewRaw + stepPlayback`）没补——现有
`U-HV-05a..d` 走的是**提交那一次**（`render()` = commit 形态），而批中形态由 `blankAskSurface`
的 `keepDialog` 分支兜住（票 18 的 `M6-B1` 覆盖的是同一分支的关闭方向）。真机验收（配方见上）
会把这 2 秒窗口走一遍；若用户验收时看到"先关再开"，按本票的 `syncDialog` 顺序（`blankAskSurface`
先保留、commit 后重画）应当是关不掉的，届时再补这条用例。
