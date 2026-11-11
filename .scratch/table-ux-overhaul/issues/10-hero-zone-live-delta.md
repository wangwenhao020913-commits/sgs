# 10: 武将牌上的牌区派生 + 空转重绘

**What to build:** 【谦逊】牌回手后武将牌上的牌区（玩家口中的"标记"）必须立刻消失，而不是等下一次询问；
并堵住"没有新记录时永不重绘"这一类陈旧显示。

**Blocked by:** 07（提示区的空转重绘出口，本票与它共用同一条判据）
Status: open
Type: task
Touches: tools/sgs_web/static/session.js, tools/sgs_web/static/seat-view.js, tools/sgs_web/static/table.js, tests/support/ui_smoke.mjs, tests/test_web.py
Ruling: spec §2 Q7（伴随 #1 的底稿订正）；`GLOSSARY.md`「武将牌上的牌」

## 根因（spec §1 #11）

- 界陆逊【谦逊】的放置与归还**都有记录**：放置 = `card_move` → `to_zone = {zone:"hero", skill_instance:"0:hero.jie_luxun.qianxun"}`；
  归还 = `_turn_after_end`（`engine/game.py:6769-6772`）→ `return_hero_card_zones`（`:2480-2540`）逐区一条 `HERO → HAND`。
  两条人类记录流都有（`visibility.py:74-76`：宿主本人可见）。
- 投影也对：`players[].hero_zones`（`projection.py:591-597` → `seat_hero_zones:618-666`，空区不下发）。
- **前端不派生**：`applyLiveDelta`（`static/session.js:507-686`）的 `card_move` 分支只处理
  deck / discard / hand / equip / judge（`:627-665`），**没有 `hero` 分支**；`cloneLiveView` 又把
  `hero_zones` 深拷贝（`:366-380`）⇒ 整批回放期间该区**停在上一份快照值**。
  而"牌已回手"那半能派生（`:639-653` 改 `hand_count`/`self_hand`）⇒ 玩家看到**牌到手、行还在**。
- 唯一能修它的是 `commitView → renderTable`（`:1471-1484/1502`），而一批的 commit 恰在**该批播完、下一问下发**时
  ⇒ 表现为"要等下一次询问才刷新"。空转轮询（`pollIsIdle`，`:1576-1593`）连快照都不应用。

## 要做什么

1. **`applyLiveDelta` 补 `hero` 分支**：`card_move` 的 `from.zone === "hero"` / `to.zone === "hero"` 时，
   就地增删 `view.snapshot.players[seat].hero_zones[*].cards`（区按 `zone_ref.skill_instance` 认），
   并同时维护 `hand_count` / `self_hand`（现状那两半已经对，别重复加）。
   空区要点：**区为空时把该行整条删除**（投影就是这么下发的：空区不下发，`projection.py:643`）。
2. **空转重绘出口**：`pollIsIdle`（`session.js:1576-1582`）增加"快照指纹变了"这一路（指纹 = 座位手牌数 /
   体力 / 护甲 / hero_zones 张数 / 标记数 的短摘要）；变了就走"只重绘座位块"的轻路径，不换整个视图、不清选择。
3. **开局底稿订正**（与票 14 共用同一处）：`openingLiveView`（`session.js:297-325`）现在从 post-init 快照倒推，
   `hp` 写 `hp_max ?? hp`、`hp_max`/`armor`/`markers` 原样继承 ⇒ 开局第一帧就显示 init 期改过的上限/护甲/标记。
   本票负责**前端侧**的兜底派生（`hp_max_change` / `armor_change` / `marker_change` 的 live 派生），
   票 14 负责服务端下发真正的开局底稿。两票都落之后 `openingLiveView` 的"倒推"可以整段删掉。
4. **不要为新事实开新通道**：`card_move` 里已经有 HERO↔HAND，按规定走前端派生即可；
   确需新通道时**必须**按票 01 的 `after` 游标实现（`spec §6` 不变量 4）。

## 验收

- 新增（`tests/test_web.py`）：`test_a_hero_zone_return_is_applied_to_the_live_view_of_the_same_batch`
  （构造一条 `HERO → HAND` 的 `card_move`，断言 `liveView` 的 `hero_zones` 在**该条之后**就空了，
  而不是等 `commitView`）。
- 新增（烟测）：`U-QX-01` 回合结束拿回【谦逊】牌：牌进手牌区的同一步，武将牌上的牌区那一行消失。
- 新增（烟测）：`U-IDLE-01` 空转轮询时"快照变了就重绘座位块"（不换视图、不清选择）。

## 会红的既有断言

- `tests/test_web_static.py` 里对 `applyLiveDelta` 分支的静态断言（若有）。
- 金录像：纯前端派生 ⇒ 0 条重录。
