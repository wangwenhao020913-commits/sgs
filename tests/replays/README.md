# Golden Replay 基准索引

一局一文件：文件内容就是引擎的**记录流**（canonical JSONL，首行 `header`），
既是期望输出、也是回放输入（种子在 `header`，完整 `Action` 内联在 `action` 记录里）。
验证 = 从文件抽出 `(seed, tape)`，用当前引擎重跑一局，与文件**全量逐行字节比对**（票 10 §2）。

- 录制 / 重录的唯一入口：`pytest --update-golden`（票 10 §6；不给产品 CLI 加参数）。
- 场景脚本玩家（`scenarios.py`）**只在录制时使用**；验证期一律 `TapePlayer`。
- `tests/test_replays.py` 断言本表的 slug 集合与 `*.jsonl` 集合**互相等于**（防孤儿）。

| slug | 中文场景名 | 验证的规则条目 | 录制脚本 slug |
|---|---|---|---|
| `01-slash-dodge-cancel` | 【杀】→【闪】抵消 | 使用与打出 / 无效·抵消·失效 / 使用全步骤（规则 4.3 / 17.6 / 17.10）；L4 六个时机 + L5 的 `RESOLVE_BEFORE_EFFECT` / `RESOLVE_END` / `RESOLVE_AFTER_END`（**不出现** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）；手牌 → 处理区 → 弃牌堆两段移动（`reason` 均为 `USE`） | `01-slash-dodge-cancel` |
| `02-slash-hit-damage` | 【杀】→不闪→伤害 | 使用全步骤 / 数值四事件（17.10 / 4.13–4.15）；L5 六个（**含** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）→ 伤害帧 L7 五个（`DAMAGE_DEAL` / `DAMAGE_TAKE` / `DAMAGE_AFTER_DEAL` / `DAMAGE_AFTER_TAKE` / `DAMAGE_END`）→ 其中嵌套扣减体力帧 L8 三个（`LIFE_DECREASE_BEFORE` / `ON` / `AFTER`，插在"造成伤害后"之前）；一条 `hp_change` delta（座位 / 前值 / 后值 / 增减量，`frame_id` = 扣减体力帧） | `02-slash-hit-damage` |
| `03-near-death-self-peach` | 濒死求桃：自救 | 濒死（规则 4.17 / 4.7）；伤害 → 扣减体力 → `NEAR_DEATH_ENTER` / `AT` / `AFTER` 三时机；`PEACH` 询问按官方 L12.2 从当前回合角色起逆时针逐个发（一个询问一个应答者）；濒死者自己在 `NEAR_DEATH_AT` 出【桃】→ 回复帧 L10 两个（`RECOVER_VALUE` / `RECOVER_AFTER`）**插在濒死之下** → 救回后回到被中断的伤害结算（`LIFE_DECREASE_AFTER` 与 L7 的 `DAMAGE_AFTER_*` 照常出现） | `03-near-death-self-peach` |
| `04-near-death-other-peach` | 濒死求桃：他人救 | 同 `03`，但**当前回合角色**（座位 0，也是第一个被问的人）对濒死者用【桃】→ 它一出桃就脱离濒死，**后面的应答者不再被问**；回复结算同样插在濒死之下 | `04-near-death-other-peach` |
| `05-near-death-death` | 濒死无人救 → 死亡 → 胜负 | 濒死（规则 4.17）与死亡 / 胜负判定时点（4.18 / 4.19）；双方都放弃 → 濒死帧正常 dispatch 三时机、随后压入 **DEATH** 子帧：`DEATH_BEFORE_START`（空实现）→ `DEATH_REVEAL_IDENTITY_AFTER`（**胜负检测点**，检测后不终止）→ `DEATH_ON`（仅 Ⅰ.a 弃遗牌）→ `DEATH_AFTER`（空实现）→ 逐帧 `discard` 五帧（`NEAR_DEATH / LIFE_DECREASE / DAMAGE / RESOLVE / USE`）→ 处理区遗留牌入弃牌堆 → `game_end`（**最后一条**，`winner` = 存活方） | `05-near-death-death` |
| `06-deck-reshuffle` | 牌堆耗尽 → 重洗 | 牌堆耗尽（规则 10.4）：需要摸牌而牌堆为空、弃牌堆非空 ⇒ 把弃牌堆洗混成新牌堆继续使用，"要 2 张、牌堆只剩 1 张"也自然补足；每次重洗只留**一条** `delta`：`delta_type="deck_shuffle"`、`from_zone={"zone":"discard","seat":null}` → `to_zone={"zone":"deck","seat":null}`、`cards` = **洗后**新牌堆顺序；**不出现** `card_move(DISCARD→DECK)`；初次洗牌仍是 `seq=1`、`frame_id=0`（无帧哨兵）、`from_zone=null` | `06-deck-reshuffle` |

## 记录面 v2（施工票 21 落地后）

- `header.event_schema_version = 2`、支持集合 = `{2}`（**不建** v1 兼容读取器；
  版本错误的报错带"过旧 / 过新"方向词）。仓库里不允许残留 v1 文件。
- 区域引用是**区域坐标**对象 `{"zone": ..., "seat": ...}`（`seat` 显式写出，含 `null`；
  共享区为 `null`）；`card_move` **没有** `from_seat` / `to_seat` 冗余字段。
- `card_move.owner` = **归属**（本次移动算谁的）：摸牌归摸牌者、弃置 / 使用归弃牌者 /
  使用者、装备置入归持有者；"无角色主体"的收尾移动才是 `null`。判定牌的两条移动
  （`DECK→PROCESSING`、`PROCESSING→DISCARD`）归**被判定者**（归属 ≠ 区域坐标）。
- 每条内联牌面多一个 `slot`（装备槽位；`basic_53` 的每张牌都是 `null`），
  `PlayerPublic` 多 `equip` / `judge` 两个公开列表；手牌上限仍是**派生函数**，不落字段。
- M1 六条基准的**决策轨迹**摘要冻结在 `tests/test_replays.py::M1_FROZEN_TRAJECTORIES`；
  摘要剔除 v2 新增的牌面事实字段（`slot`）后逐条相等，证明升版没改语义。

## 整局终止口径（票 22 落地后）

- **每条基准都以 `game_end` 收局**：死亡结算、胜负判定、终局清栈与处理区收尾都已落地；
  随机驱动的整局也一样（CLI 门禁断言末行是终局记录）。
- 死亡路径的逐帧 `discard` 顺序、`DEATH_ON` 遗牌、终局收尾与胜负语义由
  `tests/test_death.py` 逐条断言。
- 记录流里会出现 **体力 ≤ 0** 的行：官方明说"角色的体力有可能小于 0"
  （M1 的一次扣减恰好只降到 0），它是濒死结算的中间态。`开发文档/05` §4 的"体力范围"不变量
  只写"濒死结算中间态除外"；把它精确收窄成"`hp < 0` 仅允许出现在 `NEAR_DEATH` 帧在栈上时"
  的是 **票 11 §79**——`tests/test_near_death.py` 逐条断言了这条不变量。
  上限不变量（`hp ≤ hp_max`）在每条基准里逐行成立。

## 重洗落地后的收局口径（票 23）

- 牌堆耗尽不再等于终局：**弃牌堆非空就一定重洗补足**，故 `game_end.reason = "deck_exhausted"`
  不再产生；短摸只可能意味着**两堆皆空**（实战不可达，由构造局面单测覆盖）。
- 因此**谁都不进攻的整局不会自然终止**（牌堆被弃牌堆无限补足）；基准与结构性单测都靠
  "打死一方"或以 `tests/support/positions.py` 的 `run_turns` / `run_until` 主动跳出。
- `03` / `04` 因此**重录**：它们原先停在"牌堆耗尽平局"，现在继续跑并最终以胜负收局；
  `01` / `02` / `05` 在重洗落地前后逐字节不变（死亡先于第一次重洗）。
