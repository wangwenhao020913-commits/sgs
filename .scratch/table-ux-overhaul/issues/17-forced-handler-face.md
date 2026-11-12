# 17: 强制段面只收"有发动形态"的段——大雾/狂风不再在准备阶段被问

**What to build:** 玩家在**准备阶段**被问「大雾 / 狂风 是否发动」，而规则上这两条只在
**结束阶段**发动。修掉这个面：`Hook.forced` 的**纯回调段**（没有 flow：标记过期、伤害防止）
只按链序**静默**跑，绝不进玩家可见的"选一条 / 是否发动"面。

**Blocked by:** None
Status: resolved
Type: task
Touches: src/sgs/skills/catalog.py, tests/test_hero_huo.py, tests/test_hero_lei.py
Ruling: 用户 2026-11-11 实机报障（"大雾狂风在准备阶段触发了，预期为只有回合结束时触发"）+ `.scratch/table-ux-overhaul/map.md` 缺口 #癸；规则原文见 `src/sgs/skills/shen_zhugeliang.py` 文件头（两技均"结束阶段"）

## 根因（已实测复现，不是本 effort 的回归）

- 神诸葛亮两条技能的钩子形状：`狂风/大雾` = `PHASE_FINISH` 上的 **flow**（可选，玩家真发动）
  + `TURN_START` 上的 **`forced=True` 纯回调**（`_kuangfeng_expire` / `_dawu_expire`，标记过期）
  + `大雾` 另有 `DAMAGE_DEAL` 的强制回调（防止）。
- `catalog._dispatch` 的"同座位同档多段强制效果由玩家选先后"面（`open_mandatory`）把
  **纯回调段**也算进去了：两条 `TURN_START` 过期回调 ⇒ `face = declinable + open_mandatory`
  长度 2 ⇒ 摆出「是否发动该技能？」面（无 Pass）。
- 实测（2 座、神诸葛亮坐 0 号位，`TURN_START` 时机的 `SKILL_ACTIVATION`）：
  每回合**两次**这种面（`TURN_START` + `PHASE_FINISH`），一局 256 次；
  **在基线 `3a37f57` 上逐字相同** ⇒ 陈年缺陷，不是本轮引入。
- 玩家的观感正是"准备阶段触发了"：那张面里只有 `hero.shen_zhugeliang.dawu` /
  `kuangfeng` 两条 `ActivateSkill`，提示语还是「是否发动该技能？」，与真正的结束阶段面
  长得一样。

## 要做什么

1. **判据**：一个候选进"玩家可见面"的前提是它在这一个时机上**有发动形态**——即匹配到的
   `items` 里至少一条是 ``Hook.flow`` 或"可放弃的 handler"；**全是 ``forced`` 纯 handler**
   （`flow is None`）的候选属于**簿记段**，按链序静默跑（`announce=True`，与既有强制段同路）。
   命名与落点由实现定（建议在 `_activation_candidates` 里给候选加一个 `silent` /
   `has_flow` 判据，或把 `open_mandatory` 的收集条件收窄）。
2. **不许改**：`劫营③ + 镇骨` 那种"都得跑、先跑哪个由你定"的面必须照旧出现
   （`tests/test_hero_lei.py` 里钉它的用例保持绿；那两条是 **flow** 段）。
3. **不许改**：`PHASE_FINISH` 上大雾/狂风的正常发动面（该问还是要问、Pass 语义不变）、
   `DAMAGE_DEAL` 的强制防止、`TURN_START` 的标记过期（只是不再问）。
4. 若同座位同时有"簿记段 + 可发动段"，只把可发动段拼面；簿记段先按链序跑完再问
   （与既有"强制段先于可选项"同姿态）。

## 验收

- 新增（`tests/test_hero_huo.py`）：
  - `test_dawu_and_kuangfeng_are_never_offered_at_turn_start`（真引擎、2 座、神诸葛亮坐 0：
    驱动一整回合，断言**没有**任何 `SKILL_ACTIVATION` 询问在 `TURN_START` 上提供这两条技能；
    `PHASE_FINISH` 上仍然提供）。
  - `test_the_turn_start_expiry_still_runs_without_a_question`（标记照旧过期：帧里
    `dawu_marked` / 标记数在回合开始后清掉，记录流里没有应答）。
- 回归：`tests/test_hero_lei.py` 的"两段强制效果由玩家选先后"用例保持绿；
  `tests/test_hero_huo.py` 的既有大雾/狂风用例（防止、增伤、标记寿命）保持绿。
- AI：`tests/test_all_ai_games.py`、`tests/test_ai_cases.py`、`tests/test_heuristic_ai.py` 绿。
- 金录像：先跑 `bash tools/check_goldens.sh`。**预计 0 条**（神诸葛亮不在标准 108 里）；
  若真有基准含这种 `TURN_START` 面 ⇒ **本票在自己的提交里重录**（`spec §3 注①`），
  清单写进 `## Answer`，`tests/replays/goals.py` 一字不改。

## 会红的既有断言

- 没有按设计应该红的（这张面此前无人断言）；若 `test_hero_huo.py` 里有用例**依赖那张
  `TURN_START` 面**（例如用"激活应答"推进标记过期），按新口径改写成"静默过期"。

## Answer

**落地**（本票一个提交；`src/sgs/skills/catalog.py` / `src/sgs/skills/shen_zhugeliang.py`
/ `tests/test_hero_huo.py` / `tests/test_web.py` / `docs/agents/skill-authoring/shapes.md`）：

- `catalog.py`：`Hook` 新增 `bookkeeping: bool = False`（**簿记段**声明，`__post_init__`
  自校"必须是 `forced` 的纯 handler"）+ `ActivationCandidate.bookkeeping`（**唯一的派生
  判据**，全部匹配项都声明为簿记 ⇒ 候选是簿记段）+ `_dispatch` 收窄：簿记段候选在**当前
  档先按链序静默跑完**（走既有 `pass_forced` 同一条路、`announce=True`），再摆可选面；
  `locked_mandatory` / `open_mandatory` 两个子队列都不再收簿记段。
- `shen_zhugeliang.py`：【狂风】【大雾】的 `TURN_START` 到期清扫各加 `bookkeeping=True`。
  结束阶段的 flow、`DAMAGE_DEAL` 的防止、标记寿命（`_own_turn_start` 自校）**一字未改**。
- `docs/agents/skill-authoring/shapes.md`：登记簿记段的形状与判据（为什么不能只看形状）。
- `tests/test_hero_huo.py`：新增两条票面用例；把**依赖旧那张 `TURN_START` 面**的
  `test_the_two_expiry_hooks_share_an_order_face_without_a_pass`（该名字已不存在）按"会红的既有断言"一节
  改写成 `test_the_two_expiry_hooks_run_silently_in_chain_order`（票面说"此前无人断言"，
  实际有这一条，见下面的刻意读法 4）。
- `tests/test_web.py`：`_session_at_a_two_skill_activation_face` 的注释按新口径改写
  （只改注释，无行为改动）。

**验收判据 → 断言**：

- 准备阶段不再提供这两条技能 → `test_dawu_and_kuangfeng_are_never_offered_at_turn_start`：
  真引擎 / 2 座 / 神诸葛亮 0 号位，驱动一整回合（开局扣置 → 准备 → 摸牌 → 出牌 → 弃牌 →
  结束阶段，停在结束阶段那张面）：`TURN_START` 面 **0 张**；`PHASE_FINISH` 面仍是
  `{hero.shen_zhugeliang.dawu, hero.shen_zhugeliang.kuangfeng}`。
- 标记照旧过期、且没有应答 → `test_the_turn_start_expiry_still_runs_without_a_question`：
  两条 `marker_change` delta 都是 `count=0`（正向断言，不会"标记本来就没有"而空过）；记录流里
  **没有** `ActivateSkill(dawu/kuangfeng)` 应答；`TURN_START` 上没有面。
- 红/绿对照：把 `ActivationCandidate.bookkeeping` 临时改回恒 `False`（= 修复前行为，
  `probe/unfix_plugin.py`），两条新用例**都红**（第二条红在"记录流里有
  `hero.shen_zhugeliang.dawu` 的应答"）；修复后都绿。
- 劫营③ + 镇骨 的顺序面不变 → `tests/test_hero_lei.py::test_jieying_and_zhengku_force_an_order_at_turn_end`
  绿（**一行未改**）；既有大雾/狂风用例（防止 / 增伤 / 标记寿命 / 只在自己的回合开始清扫）全绿。
- 同座位两段清扫的链序：`test_the_two_expiry_hooks_run_silently_in_chain_order` 断言
  `removed == ["大雾", "狂风"]`（`_chain_order` 同座位按 `skill_id`：`dawu` < `kuangfeng`）
  且 `TURN_START` 无面。

**实测"准备阶段没有面"的数字**（同一探针 `.scratch/table-ux-overhaul/probe/probe17_before_after.py`：
`_shen_engine(seed=7, seats=2)` + 缺省应答，6000 次驱动；"判据关掉"= 修复前行为）：

| 判据关掉（修复前） | 判据生效（修复后） |
|---|---|
| `TURN_START` 提供 **256** 次（128 张面 × 2 条技能） | **0** |
| `PHASE_FINISH` 提供 256 次（128 张面 × 2 条技能） | 266 次（133 张面） |

同一驱动预算下修复后多走了 5 个回合（准备阶段不再停两次问），故结束阶段的绝对次数变大；
**每回合仍是 1 张结束阶段的面、2 条技能**。父探针
（`.scratch/table-ux-overhaul/probe_dawu_timing.py`，按"询问记录"计）由 256 条
（128 `TURN_START` + 128 `PHASE_FINISH`）变为 133 条（全部 `PHASE_FINISH`）。

**刻意读法**：

1. **票面 §1 的字面判据与票面 §2 自相矛盾，实测证据在 §1 一边是错的**：票面说
   「`items` 全是 `forced` 纯 handler（`flow is None`）⇒ 簿记段」并断言劫营③ / 镇骨是
   **flow** 段。探针实测：两条在 `TURN_END` 上都是 `Hook(handler=…, forced=True)`、
   handler 无 flow（候选形状 `('hero.haozhao.zhengku', True, ((True, 'Hook', False),))`，
   劫营同理，见 `.scratch/table-ux-overhaul/probe/probe17.py`）。按字面判据它们会被判成
   簿记段，那张**没有 Pass** 的「两条都得跑、先跑哪个由你定」的面会消失——与 §2「不许改」
   直接冲突。纯形状无法区分二者（`(forced=True, handler, flow=None)` 完全相同），故判据落成
   **显式声明**：`Hook.bookkeeping` 缺省 `False`（真效果，维持既有行为），只有到期清扫这一类
   技能记账声明 `True`。**没有**为此改 `hero_lei.py` 的任何声明，也没有动那条用例。
2. 簿记段在**当前档**先跑、再问面（票面 §4 的姿态）：实现落在 `_dispatch` 的 `start` 相，
   与既有"强制段先于可选项"同一条 `pass_forced` 路（`announce=True`，表现层的技能特效
   照旧有事实可读）。
3. 簿记段仍受"每技能一次 / 实例失效 / 条件自校"既有闸门约束（走的就是原来的强制段路径），
   票面 §3 的"失效即停摆"不变。
4. 票面"会红的既有断言"说"这张面此前无人断言"——实际有：
   `test_the_two_expiry_hooks_share_an_order_face_without_a_pass`（该名字已不存在）钉住了旧面（并要求按
   玩家选择顺序结算）。按同一节给出的改法改写成"静默、链序"，用的是同一条用户报障的
   新口径，不是弱化断言。

**金录像**：`bash tools/check_goldens.sh` ⇒ `PASS: 47 条标准 108 金录像逐字节不变`
（神诸葛亮不在标准 108 ⇒ **0 条重录**；`tests/replays/goals.py` 未动）。

**门禁**：本提交的 pre-commit hook `tests/run_all.sh --quiet` 通过（`src/` / `tests/` 改动
走全量档）。此前的人工聚焦检查：`test_hero_huo.py` / `test_hero_lei.py` /
`test_hero_catalog.py` / `test_ask_points.py` / `test_skill_authoring_docs.py` 全绿；
AI 三份（`test_all_ai_games.py` / `test_ai_cases.py` / `test_heuristic_ai.py`）258 条绿。
