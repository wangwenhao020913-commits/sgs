# 14: 建局严格按 init——L0 边界暂停 + 服务端下发开局底稿

**What to build:** 建局响应止于"游戏开始时"派发完、第一回合之前；开局底稿由**服务端**下发，
前端不再从 post-init 快照倒推。玩家看到的顺序必须是：安排座位 / 初始化体力 / 发初始牌 →
"游戏开始时"时机 → 第一轮 → 第一角色的第一个回合。

**Blocked by:** None（与票 10 共用 `openingLiveView` 的收尾）
Status: resolved
Type: task
Touches: tools/sgs_web/session.py, tools/sgs_web/serve.py, tools/sgs_web/projection.py, src/sgs/engine/game.py, tools/sgs_web/static/session.js, tests/test_web.py, tests/support/ui_smoke.mjs
Ruling: spec §2 Q7（用户 2026-11-11 签 A，并明确 init 的定义）

## 根因（spec §1 #1，已判否引擎提前推进）

- **引擎没有提前推进**：`Session.__init__→drive()`（`tools/sgs_web/session.py:203,274-284`）只跑到**人类第一问**；
  8 人局实测建局响应 56 条记录：seq1 洗牌、seq10 发牌时机、seq11-18 逐座位 4 张、**seq19 = GAME_START**、
  seq20-54 = 2 号位（AI，主公）的**整个第一回合**、seq55 才是人类 RESPOND。
  引擎守卫成立（`game.py:6025-6031` 栈优先 → `:6025` `if not self._started: self._start()` → `:6060-6071`
  `_started` 只在 GAME_START 派发返回非 `"stay"` 后置真）。带 GAME_START 技能（AI 的【天成】）实测：
  seq11=GAME_START、seq12-50 = 天成 3 问 3 答、**seq51 才 TURN_BEFORE_START**——**没有**提前进回合环的路径。
- **真凶 = 开局底稿从 post-init 快照倒推**：`openingLiveView`（`static/session.js:297-325`）只重置
  identity / hand / equip / judge / hero_zones / deck / phase / turn，`hp` 直接写 `hp_max ?? hp`，
  **`hp_max` / `armor` / `markers` 原样继承**；而 `applyLiveDelta`（`:507-686`）**没有 `hp_max_change` 分支**。
  实测 AI【天成】：**t0 就显示 5/5**，播到 seq21 血条反而 5/5 → **3/5 倒退** ⇒ 玩家观感 = "init 没走完 AI 已经操作过"。
- 只改前端**压不干净**：前端拿不到 pre-init 的 `hp_max`（载荷只给终值），只能扫 `hp_max_change.before` 反推
  ⇒ 界面从记录猜规则，违反仓里「前端不猜」。

## 要做什么

1. **服务端 L0 边界暂停点**：`Session.drive()` 的停点从"人类第一问 / 终局"扩一条
   "**L0 刚完成**"（`engine._started` 由假变真、且还没进回合环）。建局响应止于 GAME_START 之后；
   后续记录由客户端下一次 `fetchView()` 拉（现有 `after` 游标机制，零新增协议）。
   - 注意：`drive()` 的循环条件（`not finished and pending is None`）要加"且未撞到 L0 边界"；
     撞到后置一个内部标志，下一次 `drive()` 不再停。
   - D16（"一次驱动到下一个暂停点或终局"）的**暂停点定义**要在 `tools/sgs_web/session.py` 顶部注释里改写。
2. **服务端下发开局底稿**：建局响应（`after < 0` 那一次）额外给一份 `setup` 快照
   （座位 / 武将 / 技能 / `hp` = 各自 `hp_max` / `hp_max` / `armor` / `markers` / `hand_count = 0` /
   空区），由引擎状态**在发牌前**导出（`build_initial` 之后的 `GameState` 是发牌前真值；若拿了旧值，
   就在 `Engine` 里留一份 post-`build_initial` / pre-`_start` 的快照）。
3. **前端**：`openingLiveView` 不再倒推——直接读 `setup`；`applyLiveDelta` 补 `hp_max_change` /
   `armor_change` / `marker_change` 的派生（与票 10 的 hero 区派生同一批）。
4. **前端不得因"L0 之后还要拉一次"而卡顿**：`applyView` 在开局批播完、`commitView` 之后
   自然 `poll()`（现有 2 s 节奏）或立刻 `refresh()` 一次（推荐立刻，避免 2 s 空窗）。
5. **回放不受影响**：`setup` 只出现在建局响应，**不进记录流**（`exclude=True` 或只走 view 载荷）。

## 验收

- 新增：`test_the_create_response_stops_at_the_end_of_game_start`
  （8 人 / seed 12：记录最后一条的 `timing` 是 `GAME_START` 之后的收尾，且**不含任何 `TURN_*`**）。
- 新增：`test_the_create_response_carries_a_pre_game_setup_snapshot`
  （`setup.players[i].hand_count == 0`、`hp == hp_max`、`hp_max` 是该武将的基础值）。
- 新增：`test_a_game_start_max_hp_change_does_not_leak_into_the_opening_frame`
  （AI 无名【天成】：t0 的 `hp_max` 是基础值，播到 `MAXLIFE` 那条才变 5）。
- 新增（烟测）：`U-INIT-01` 建局后逐条看：手牌 0→4、`turn` 一直"—"直到 `TURN_START`、阶段空、
  没有任何"等待其他座位响应"提前出现。
- `## Answer` 里附 `bsk` 实机配方（人数 4 / **点将：我的座位选神诸葛亮【七星】** / 观察前 15 秒）。

## 会红的既有断言

- `tests/test_web.py` 里"建局响应一次给全（`after=-1` 含全部记录）"的断言——本票**改变了这一点**，
  要逐条核并改写（`records` 变短、`cursor` 变小）。
- `tests/support/ui_smoke.mjs` 里依赖"建局响应含若干 AI 回合"的用例。
- 金录像：接入层行为，**0 条重录**（跑 `check_goldens.sh` 确认）。

## Answer

### 改动摘要

1. **L0 边界暂停**（`tools/sgs_web/session.py` + `src/sgs/engine/game.py`）
   - `Engine.run(participants, *, stop_after_game_start=False)`：边界住在引擎循环**内部**
     （`_step` 里 `_start` 跑完就 `return`，`_started` 由假翻真），外面看不见那一刻 ⇒
     必须由引擎让出。缺省 `False` ⇒ 金录像 / `batch_games` / `ai_eval` / 录像回放**零变化**。
   - `Session.drive()` 的停点从两处（人类第一问 / 终局）扩成三处（模块 docstring 的 D16
     段落已按新定义重写）：`_l0_boundary_reached` 为假时带
     `stop_after_game_start=True` 驱动，引擎一让出就置真并返回 ⇒ **一局只停一次**。
   - `Session(pause_after_game_start=False)`：这个开关是**接入层的建局口径**，只在
     `GameRegistry.create` 打开；库层 / 构造局面夹具 / 单测缺省仍是"直接驱动到人类第一问"
     （否则 100+ 条既有单测要一起改口径）。
   - 后续记录由客户端**下一次取增量**拉：`GameRegistry.advance_clock` 现在先
     `session.drive()` 再打戳（`mark_pending` 看到的是这一趟刚产出的那一问，倒计时不晚
     2 秒起算）。零新增协议。

2. **服务端下发开局底稿**（`tools/sgs_web/projection.py:setup_snapshot` + `serve.py`）
   - `Session.setup` 在 `__init__` 里**于 `drive()` 之前**冻结：那一刻 `engine.state` 是
     `build_initial` 的产物、「游戏开始时」的钩子（【天成】改上限 / 摸牌）还没跑 ——
     这是"若拿了旧值就没救"的那一份真值，故不需要往 `Engine` 里塞快照。
   - 它由 `visible_snapshot` 派生（同一份过滤口径：技能 / 标记 / 手牌上限 / 封印区 / 武将
     块标签都直接读），只把"发牌前还没发生"的几格按住：`hand_count=0`、`self_hand=[]`、
     `hp=hp_max`、`identity/identity_label=None`、三个牌区空、`discard`/`processing` 空、
     `deck_count=0`、`phase=None`、`turn_seat=None`、`turn_extra=False`、`round=1`。
   - 只在 `after < 0` 的那一份视图里出现（**其余批不带这个键**，不是 `null`）。

3. **前端读底稿**（`tools/sgs_web/static/session.js`）
   - `openingLiveView(body)` 直接读 `body.setup`，**倒推整段删除**（`hp = hp_max ?? hp`、
     继承 `hp_max`/`armor`/`markers`、identity / 各区 / 阶段 / 回合的清零都不在前端做了）。
     没带 `setup` 的载荷（旧录像 / 合成视图）**不猜**：以权威快照起播 + 一条控制台提示。
   - `applyView` 把"这一批是不是开局批"记进 `state.playback.opening`；`finishPlayback`
     在**开局批 ∧ 没有挂起询问 ∧ 没终局**时立刻 `refresh()`（不等 2 s 轮询，避免空窗）。
   - `refresh()` 记下发起时的 `state.sessionId`，回包时若已经换局就**丢弃**——"立即 refresh"
     让"回包比换局慢"这条竞态真的可撞到（回到开局页又开一局）。

4. **回放不受影响**：`setup` 不是 `JournalRecord`、不是 delta，只走 view 载荷；
   `bash tools/check_goldens.sh` ⇒ `PASS: 47 条标准 108 金录像逐字节不变`（**0 条重录**）。

### 验收条目 → 断言

| 验收 | 断言 | 位置 |
|---|---|---|
| 建局响应止于 `GAME_START`（8 人 / seed 12，无任何 `TURN_*`） | `test_the_create_response_stops_at_the_end_of_game_start` | `tests/test_web.py` |
| 建局响应带发牌前底稿（`hand_count==0` / `hp==hp_max` / `hp_max` = 武将基础值） | `test_the_create_response_carries_a_pre_game_setup_snapshot` | `tests/test_web.py` |
| 【天成】的 `MAXLIFE` 不泄进 t0（t0 = 基础值 2，逐条抬到 5） | `test_a_game_start_max_hp_change_does_not_leak_into_the_opening_frame` | `tests/test_web.py` |
| 烟测：手牌 0→4 / `turn` 一直"—" / 阶段空 / 无"等待其他座位响应"提前出现 | `U-INIT-01a` … `U-INIT-01g` | `tests/support/ui_smoke.mjs` |
| 金录像 0 重录 | `bash tools/check_goldens.sh` | 见下"门禁结果" |

前三条的**真实产出路径**（`CODING_STANDARDS.md` §6）：`GameRegistry.create` / `view` /
`advance_clock` → 真引擎记录 → `visible_snapshot` / `setup_snapshot`；前端跑**真的**
`session.js`（`tests/support/live_delta_probe.mjs` 新加 `opening: true`，走
`openingLiveView` + `applyLiveDelta`，与 `applyView` / `stepPlayback` 同一条派生口）。
第三条的期望值取两条独立真源：武将数据表的 `hp_max`（无名 2）与【天成】三次选项 2 的
`2→3→4→5`（记录里的 `after`）。烟测的 `U-INIT-01` 走**原始建局响应**（`postRaw`）逐条
`stepPlayback`。

### 会红的既有断言（本票**故意**改写）

1. `tests/test_web.py`
   - `_play_to_end` / `_settle` 两个助手：经 `GameRegistry.create` 建的会话现在停在 L0 边界，
     测试要断言"已收口"那一份时先 `drive()` / `advance_clock()` + 再取一次增量（**真客户端
     走的就是这一步**）。改写的三条用例：`test_an_idle_poll_is_silent_until_the_deadline_answers_for_you`、
     `test_the_view_payload_never_json_round_trips_the_record_stream`、
     `test_the_view_only_derives_the_new_narrative_records`——都只是**先驱动再取增量**，
     判据一条没放宽；另两条只读武将 / 档位 / 技能清单的建局用例（`heroes` / `deck_profile` /
     `skills`）不受影响，未动。
2. `tests/support/ui_smoke.mjs`：建局一律走 `post()`，它现在**POST + 再取一次 `after=-1`
   的增量**，于是 40+ 条用例看到的仍是本票之前那份"开局已收口"的视图（记录流 / 快照 / 询问
   逐字相同，还多带 `setup`）。**新口径本身**由 `U-INIT-01` 用 `postRaw` 直接钉住。
3. `tests/support/web_client.py`：`play` / 火攻 tape / 酒 tape 三处建局后加
   `settle_after_create`（真 HTTP 取一次增量）；构造局面夹具
   （`m5_fixture_server` / `m6_fixture_server`）不传 `pause_after_game_start`，一字未改。
4. `tests/test_web.sh` ⑤：409 用例的 `pending` 从**建局响应**改读**取一次增量之后**的那份
   （原来那条 `curl -o /dev/null` 白跑一次，现在把回包用起来）。

### 故意读法（每一处都可被驳）

1. **"最后一个停点"= 一局只停一次**：`_l0_boundary_reached` 置真之后同一局不再停。否则每次
   取增量都会在 L0 边界卡一次（第二趟 `drive()` 还是从回合环前起步）。
2. **`pause_after_game_start` 缺省关闭**：`Session` 是库对象（单测 / 构造局面夹具直接造），
   建局口径属于 `GameRegistry.create`。放在 ctor 上而不是写在 `drive()` 里，是为了让"建局
   停在 L0"成为**会话的配置事实**而不是每个调用点自己记得的事。
3. **底稿在 `__init__` 里冻结，而不是让 `Engine` 存一份 pre-`_start` 的状态**：`build_initial`
   之后的 `GameState` 就是发牌前真值，而 `Session.__init__` 是唯一在"它已经是真值、`_start`
   还没跑"之间能插一脚的地方。省一份 `GameState` 深拷贝（每局一次、AI 对战也会付）。
4. **`setup` 里的身份一律 `null`**：身份由 `identity_deal` 逐条亮（本人 / 主公可见，其余
   `null`，与 `sgs.state.project` 的可见性同源）。底稿先给 = t0 就把全场身份摆上桌。
5. **`setup` 里 `round = 1`**：`_start` 之前引擎的 `state.round` 还是 0（它由 `_start` 写 1），
   而底稿是"第一轮即将开始"那一帧；写 1 与随后第一条 `turn_change` 一致，也保住
   `tests/support/ui_smoke.mjs` 的 M6-B2c（播完未 commit 时 `#t-round` 与快照同值）。
6. **`setup` 里的 `hand_limit` / `hero_tags` 由服务端给**（票 10 刻意没有派生这两个字段）：
   它们是"体力值 → 替换链 → 加式"的派生量，前端算就是第二份真源。
7. **`after < 0` 就下发 `setup`（不只是建局那一次）**：`after=-1` 本来就是"从发牌前重来一遍"
   的语义（断线 / 首屏 / 烟测的 `post()` 都走它），带上底稿才对得上；它不是记录流，回放逐字
   不受影响。
8. **开局批播完才 refresh，且只在"没有挂起询问"时**：有询问 = 已经停在暂停点上，没什么可拉；
   `pending` 非空的开局批（人类在「游戏开始时」被问住）不 refresh，直接画那一问。
9. **`t-round` 之外，"回合位"在提交那一帧显示先手**：`_start` 会写 `state.turn_seat`
   （先手是规则事实），所以开局批 commit 之后顶栏写「座位 N」。回放期一直是「—」
   （整批没有 `TURN_*` 记录）——`U-INIT-01g` 把这一帧**显式钉住**，不当它不存在。
10. **`_l0_boundary_reached` 用 `not pause_after_game_start` 初始化**：关掉开关 = "已经越过"，
    于是 `drive()` 永远不带 `stop_after_game_start=True`，旧路径一个字节不差。
11. **`advance_clock` 里的顺序改成"先 drive、再打戳"**：新产出的那一问从**这一刻**起算倒计时；
    反过来会让第一问的时限白等一个轮询周期。
12. **AI 适配契约**（`docs/agents/ai-adaptation.md` §1）：**不适用**——本票没有新
    `QueryKind` / 新动作面 / 新技能语义；`setup` 只进网页 view 载荷，`project()` /
    `PlayerView` / 记录流一个字段没动（`session.engine.state` 与 AI 读面同源，未遮未增），
    记牌守恒量（108 / 160）不变。引擎只多一个**驱动**开关，AI 不经过它。

### 实机配方（`bsk` 不可用 ⇒ 交给用户手动验收）

前置：`bash tools/check_env.sh` 通过。静态脚本每请求从磁盘读，改过 `static/*.js` 强刷即可；
本票改了 `serve.py` / `projection.py` / `session.py`，**必须新起服务**。

```bash
# 起服务（沙箱里用受管后台任务；本机手动就用生命周期脚本）
bash tools/serve-dev.sh start 8100      # → http://127.0.0.1:8100/
```

浏览器打开 URL → 开局页：

1. **人数 = 4**（「人数」下拉）。
2. 勾上「点将」→「我的座位」选 **神诸葛亮【七星】**（自制/火包 OL 那一条；选它是因为
   【七星】开局就要动标记，是最容易看出"底稿被倒推"的座位）。
3. 「开始」→ **只看前 15 秒**，按这个次序读屏：
   - t0：所有人**满体力**（血条 4/4 这种满格）、手牌 **0 张**、牌堆 **0**、回合位「—」、
     阶段位空、没有任何「等待其他座位响应…」；
   - 然后手牌**一张一张**发到 4 张，牌堆数字**一次写成** 108（不是从 0 跳成某个中间值）；
   - 「游戏开始时」之后**才**轮到第一个座位的回合（回合位从「—」变成「座位 N」）；
   - 我自己的武将块上【七星】的标记数在开局帧里是**基数**（不是跑完之后的值），
     `切` 到自己的血条分母：若这一局有 AI 带【天成】，**不许**出现 t0 就显示改过的上限。
   - 全程**不许**出现「AI 已经在操作」的观感：开局这段里没有别人的询问、没有 AI 的行动记录。
4. 开局批播完之后**不许有空窗**：最后一条开局记录演完立刻进到"我的第一问"（本票的立即
   refresh），不该出现 2 秒的「（没有待应答的询问）」。

跑烟测自检（与手动同一批判据，无浏览器）：

```bash
bash tests/smoke-web.sh --verbose   # 含 U-INIT-01a…g
```

### 门禁结果

- `.venv/bin/pytest tests/test_web.py` ⇒ **318 passed**
- `bash tests/smoke-web.sh` ⇒ `PASS: 前端渲染冒烟`（含 `U-INIT-01a..g` 全绿）
- `bash tests/test_web.sh` ⇒ `PASS: M4 端到端门禁全部通过`（8 人正局 + 军争 160 + 409 + 三条
  构造 tape + M6 三条构造 tape + `--host 0.0.0.0`）
- `bash tools/check_goldens.sh` ⇒ `PASS: 47 条标准 108 金录像逐字节不变`（**0 条重录**）
- 提交时由 pre-commit hook 跑全量 `tests/run_all.sh --quiet`（结果见提交）

