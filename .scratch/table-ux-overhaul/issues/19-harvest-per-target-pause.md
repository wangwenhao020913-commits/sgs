# 19: 五谷取牌逐目标可见——`drive()` 在每名目标取牌后交还控制权

**What to build:** 让浏览器**看得见**每一次五谷取牌：AI 目标那一张被拿走之后，下一次视图
刷新就能读到"那张已置灰 + 座N、目标条翻成已拿"，而不是等到整段结算结束才跳变。
做法 = 接入层在**每名目标取牌后**暂停驱动（与票 14 的 L0 边界暂停同一种机制），
客户端下一次 `fetchView()` 拿到中间状态。

**Blocked by:** None（票 18 已 resolved）
Status: open
Type: task
Touches: tools/sgs_web/session.py, tests/test_web.py, tests/test_web.sh, tests/support/ui_smoke.mjs
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
