# 14: 建局严格按 init——L0 边界暂停 + 服务端下发开局底稿

**What to build:** 建局响应止于"游戏开始时"派发完、第一回合之前；开局底稿由**服务端**下发，
前端不再从 post-init 快照倒推。玩家看到的顺序必须是：安排座位 / 初始化体力 / 发初始牌 →
"游戏开始时"时机 → 第一轮 → 第一角色的第一个回合。

**Blocked by:** None（与票 10 共用 `openingLiveView` 的收尾）
Status: open
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
