# 10: 武将牌上的牌区派生 + 空转重绘

**What to build:** 【谦逊】牌回手后武将牌上的牌区（玩家口中的"标记"）必须立刻消失，而不是等下一次询问；
并堵住"没有新记录时永不重绘"这一类陈旧显示。

**Blocked by:** 07（提示区的空转重绘出口，本票与它共用同一条判据）
Status: resolved
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

## Answer

### 落了什么

**A. `applyLiveDelta` 补 `hero` 分支（票面第 1 条 / spec §1 #11）**

- `card_move` 分支末尾两行：`from.zone === "hero"` ⇒ 从区里**减**、`to.zone === "hero"` ⇒
  往区里**加**。区按 `zone_ref.skill_instance` 认，宿主 = `ref.seat`（区可跨座位：牌扣在别人
  武将牌上时 `seat` 是宿主、`owner` 是放置技能的持有者）。
- 四个纯函数兄弟住 `session.js`（紧挨 `liveRemoveCards`）：
  `liveHeroZoneKey(zone)` = **区键**（`owner:skill_id`；行里将来若直接带 `skill_instance` 就用它）、
  `liveHeroZoneParts(skillInstance)` = 把 `owner:skill_id` 拆回两半（不合形 = `null`，不猜）、
  `liveHeroZoneName(players, skillId)` = 区标签（同一份快照的技能清单翻译名）、
  `liveHeroZoneMove(players, ref, cards, count, adding)` = 一条 `card_move` 的**一侧**落到 live view。
- **空区把整行删掉**（不是留一行 0 张）：投影只画有牌的区（`projection.py:643`），界面留一行空框
  就是报障的"牌已回手、行还在"。反方向，空批移动**不建区**（引擎的 `place_on_hero_card` 同样不建）。
- 牌面只有宿主拿得到（`visibility.py:hero_zone_ref_visible`）：宿主按牌面增删、`count` 重算成
  `cards.length`；非宿主只有 `card_count` ⇒ **只改张数、不碰 `cards`**。
- **手牌数 / 手牌面不在这里动**：仍由现存的 `to.zone === "hand"` 两半管（不重复加、不重复扣）。
- `hp_max_change` 分支（票面第 3 条的**前端那一半**）：`player.hp_max = Number(record.after)`。
  `armor_change`（W7）/ `marker_change`（票 06）本来就有，这次只补上限这一条。

**B. 空转重绘的指纹（票面第 2 条）**

- `snapshotFingerprint` 的**显式字段表**把 `players[].hero_zones` 接进来：逐区
  `区键 × 张数 : 牌面 id`。同时删掉原来那一格 `hero_zone_count`——`PlayerPublic.hero_zone_count`
  是 `exclude=True`，**根本不进网页载荷**（在网页快照里恒为 `undefined`），而且"同一座位一进一出、
  张数不变"它也看不出来。
- 出口不变（票 07 立的，**没有第二条空转路径**）：`poll() → pollIsIdle（真）→ 指纹变了 →
  applyIdleRepaint`（只 `renderTable()`，**不换 `state.view`、不清选择**）；指纹没变才 `applyIdlePoll`。

**C. 测试垫片**

- 新探针 `tests/support/live_delta_probe.mjs`：真 `session.js` + 最小垫片（只要 `state` /
  `document` / `$`），用**真记录流**驱动 `cloneLiveView` + `applyLiveDelta`（与 `stepPlayback`
  同一条派生口），打印逐帧读数（体力 / 上限 / 手牌数 / 武将牌上的牌区 / 自己的手牌面）与两份指纹。
- `tests/support/ui_smoke.mjs` 加一个一次性 GET body 注入 `nextGetSnapshot`（姿态同
  `nextGetPending`，用完即清），供 `U-IDLE-01` 造"服务端只重算投影、不记新记录"的那种响应。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| 归还的 `HERO → HAND` 在该条之后就空了（不是等 `commitView`） | `test_web.py::test_a_hero_zone_return_is_applied_to_the_live_view_of_the_same_batch`（真引擎 `place_on_hero_card` + `return_hero_card_zones` 产记录、`visible_snapshot` 产底稿、`visible_record` 产人类载荷；三帧逐条断：底稿无区 → 放置那一条**长出**区（区键 `0:hero.jie_luxun.qianxun`、`skill_name` 谦逊、`count` 2、牌面 2 张、`hand_count` 只扣一次）→ 归还那一条**整行消失**且 `hand_count` / `self_hand` 补齐；全程没调过 `commitView`） |
| 同一批里"牌已回手、行还在"不再出现（烟测，真 DOM） | 烟测 `U-QX-01`（底稿 = 区里 2 张、手牌 1 张；`applyViewRaw` + `stepPlayback(true)` **只播一条**，`state.playback` 仍在 ⇒ **批还没播完**）：`#t-self-hero-zones` 的 hero 行从有到无、`hidden === true`、`#t-self-hand` 从 `1/1` → `3/3`、`#t-hand` 3 枚牌面；`U-QX-01b` 播完提交后同一形状（派生与服务端快照对得上、不留残影） |
| 空转轮询：快照变了 ⇒ 只重绘座位块、不清选择 | `test_web.py::test_the_idle_repaint_fingerprint_covers_hero_zone_rows`（同一份真快照只把区整行拿掉 ⇒ `pollIsIdle` 为真**且**指纹 `changed` 为真；同一份内容再回一次 ⇒ 指纹不变）+ 烟测 `U-IDLE-01` / `U-IDLE-01b`（真会话 + GET 注入：`state.view` 同一对象、`pending` 同一份、`#t-seats` 首节点**重建**、区行出现 / 再消失，而 `focusCardId` / `selectedTargets` / `preactivation` / 倒计时锚点都没动） |
| `hp_max_change` 的 live 派生（与票 14 共用的那一半） | `test_web.py::test_a_hp_max_change_is_applied_to_the_live_view_ceiling`（记录来自 `Engine.raise_hp_max` 的产品写入口；期望值取 `engine.state.player(seat).hp_max`，不是"代码怎么写就怎么断"） |

### 刻意读法（评审要看的三处判断）

1. **区键的拼法**：记录里只有合起来的 `zone_ref.skill_instance`，投影行只有 `skill_id` /
   `owner` 两半（`seat_hero_zones`）⇒ 前端按 `owner:skill_id` 拼出来比对。这就是
   `sgs.skills.catalog.hero_card_zone_key` 的同一形状，**不是**另造一维键；行里哪天直接带
   `skill_instance` 就优先用它。
2. **区标签的来源**：放置 / 归还的记录**不带中文名**（`card_move` 只有 `skill_instance`）。
   标签取**同一份快照**的 `players[].skills`（`seat_skill_display` 是公开事实），翻不到退回
   `skill_id`——与投影的 fail-visible 口径（`_skill_display_name(...) or zone.skill_id`）一致，
   不编名字、不另开通道。
3. **指纹用行、不用张数合计**：`hero_zone_count`（`PlayerPublic` 的公开张数）不进网页载荷，
   且看不出"同一座位一进一出、总数不变"的换牌；登记 `hero_zones` 的 `区键 × 张数 : 牌面`
   才既看见"行没了"也看见"换了一张"。票 07 的字段表纪律（座位块多读一格就多登记一格）不变。

**另两条按裁定没动的**：`recordTouchesTableView` **不动**（票 01 的裁定：`hp_max_change` 有意
不列进 `NON_TABLE_DELTAS` ⇒ 仍然整桌重建）；**不新增记录通道**（spec §6 不变量 4 的 `after`
游标口径用不上——`card_move` 里本来就有 HERO↔HAND）。

### 会红的既有断言：0 条

- `tests/test_web_static.py` 里**没有**对 `applyLiveDelta` 分支的静态断言（票面预测的那一项落空），
  无需改。438 → **439 passed**（只多了本票的新用例）。
- 金录像：**0 条重录**——纯前端派生，`bash tools/check_goldens.sh` → `PASS: 47 条标准 108 金录像
  逐字节不变`。
- 烟测既有用例全绿，尤其 `U3-05t`（指纹**没变** ⇒ 纯时钟刷新、不重绘）与 `M6-6/6b/6c`（hero 行 /
  标记行的 DOM 形状）：本票只**多看一格**指纹、只**多派生一个区**，没有换节点形状。

### 门禁结果

- `.venv/bin/pytest tests/test_web.py tests/test_web_static.py` → **439 passed**（19.7 s）。
- `bash tests/smoke-web.sh --verbose` → **全部通过**，新增四条：`U-QX-01` / `U-QX-01b` /
  `U-IDLE-01` / `U-IDLE-01b`。
- `bash tools/check_goldens.sh` → `PASS: 47 条标准 108 金录像逐字节不变`。
- 全量门禁 = **本提交的 pre-commit**（`.githooks` → `bash tests/run_all.sh --quiet`，仓规：手工跑
  全量只用于"不提交、先看红/绿"）。

### 实机复现配方（本环境 `bsk` 的合作握手不可用，未驱动浏览器；按仓规给配方）

- 环境：`bash tools/check_env.sh` → `bash tools/serve-dev.sh start`（缺省 8100，打印实际 URL）。
- 开局页：人数 **4**、AI **随机**、点将开、我的武将 **界陆逊（jie_luxun）**、牌堆 **标准 108**、
  seed **4**。
- 人类动作：出牌阶段点「结束出牌阶段」；技能弹窗一律点「发动」；其余询问按默认。
- 走到 **1 号位（座 1）** 的回合：他对你使用【决斗】⇒ 先弹【谦逊】是否发动 ⇒ 点「发动」⇒
  你的 3 张手牌（【桃】【爪黄飞电】【杀】）置于武将牌上，武将块出现「**谦逊 3**」（悬停出三张牌面）；
  随后【连营】（随意）；【决斗】的【杀】询问点「不打出」（掉 1 点体力）。
- 1 号位结束回合（他弃一张【闪】之后）⇒ 那 3 张牌回手牌区的**那一步**，「谦逊 3」那一行消失。
- 旧表现（本提交之前）：牌已经进手牌区（手牌读数与牌面都变了），那一行还在，要等下一次询问
  （整批播完、`commitView` 整桌重建）才消失——正是"要等下一次询问才刷新"。
- `U-IDLE-01` 的"只重算投影、不记新记录"没有可从界面触发的服务端路径（服务端每次变更都会记一条
  记录），故它由烟测的 GET 注入 + 上面那条指纹单元用例覆盖。

### 给后续票（11 / 12 / 13 / 14）

- **票 14 可以整段删掉 `openingLiveView` 的"倒推"**：`hp: Number(player.hp_max ?? player.hp) || 0`
  那一行（用 **post-init** 的 `hp_max` 倒推当前体力）、`hp_max` / `armor` / `markers` 三格
  "原样继承"、以及为凑发牌前骨架而清零的那一串（`identity` / `hand_count` / `equip` / `judge` /
  `hero_zones` / `hero_tags` / `chained` / `flipped` / `wine_active` / `alive` / `self_hand` /
  `discard` / `processing` / `deck_count` / `phase` / `turn_seat` / `turn_extra` / `round`）——
  服务端下发真正的 `setup` 底稿后，这些字段直接读底稿即可。**派生侧现在齐了**：
  `identity_deal` / `deck_shuffle` / `card_move`（含 **hero**）/ `hp_change` /
  **`hp_max_change`** / `armor_change` / `marker_change` / 时机（回合 / 阶段 / 阵亡）都有分支，
  票 14 不会再看到"第一帧显示 init 期改过的上限 / 护甲 / 标记"。
- **票 14 仍要自己给的两格**：`hand_limit`（服务端派生的展示量）与 `hero_tags`（手牌上限加式 /
  裸衣）——本票**有意不派生**（它们是投影派生的网页字段，前端重算会变成第二份真源），
  `setup` 快照请直接带上。
- **11 / 12 / 13 不受影响**：本票没有新记录通道、没有新 DOM 形状、没有新请求；`snapshotFingerprint`
  的字段表 = 每座位 16 格（`seat` / `hp` / `hp_max` / `armor` / `hand_count` / `alive` / `hero` /
  `identity` / `chained` / `flipped` / `wine_active` / **`hero_zones`** / `equip` / `judge` /
  `sealed_zones` / `markers`）+ 牌桌 7 组（`seat` / `turn_seat` / `phase` / `deck_count` /
  `self_hand` / `processing` / `discard`），以后哪个座位块读新字段，在那张表里加一格即可（票 07 的纪律）。
