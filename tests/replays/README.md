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
| `05-near-death-death` | 濒死无人救 → 死亡入口 | 濒死（规则 4.17 / 4.18）；双方都放弃 → 濒死帧正常 dispatch 三时机、随后**止步于死亡入口**：`LIFE_DECREASE_AFTER` 是最后一条时机，`DAMAGE_AFTER_DEAL` / `AFTER_TAKE` / `END` **一个都不出现**（濒死优先于"造成伤害后"），栈上仍留着 `USE / RESOLVE / DAMAGE` 三帧交给死亡结算 | `05-near-death-death` |

## 已知过渡态（后续票会重录）

- **死亡结算尚未实现（票 22）**：`01` / `02` / `05` 的收尾段会真的把人打到体力 ≤ 0，
  而引擎在**死亡入口**止步（官方死亡四段、胜负判定、清栈与终局记录归票 22），
  故这三条的终点是"走到死亡入口"而不是 `game_end`；`scenarios.py` 用
  `stops_at_death_entry` 显式标出。票 22 落地后把这三条一起改回"必达 `game_end`"并重录。
- `03` / `04` 已经能跑到 `game_end`（牌堆耗尽平局）：自救 / 他人救成功之后收尾段停止进攻，
  双方都不再受伤，故这两条是完整的整局基准。
- `01` / `02` / `05` 的记录流里会出现 **体力 ≤ 0** 的行：官方明说"角色的体力有可能小于 0"
  （M1 的一次扣减恰好只降到 0），它是濒死结算的中间态。`开发文档/05` §4 的"体力范围"不变量
  只写"濒死结算中间态除外"；把它精确收窄成"`hp < 0` 仅允许出现在 `NEAR_DEATH` 帧在栈上时"
  的是 **票 11 §79**——该帧已在票 21 落地，`tests/test_near_death.py` 逐条断言了这条不变量。
  上限不变量（`hp ≤ hp_max`）在每条基准里逐行成立。
