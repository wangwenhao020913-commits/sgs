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

## 已知过渡态（后续票会重录）

- `01` / `02` 的收尾段仍以**牌堆耗尽平局**（`reason = "deck_exhausted"`）收场：伤害已在票 20 落地，
  但**濒死（21）/ 死亡与胜负（22）尚未实现**，故一方体力降到 0 以下也不会终结对局、而是一直打到牌堆耗尽。
  票 21 / 22 落地后须 `--update-golden` 重录这两条。
- 因此 `02` 的记录流里会出现 **体力 < 0** 的行：官方明说"角色的体力有可能小于 0"，它是濒死结算的中间态。
  `开发文档/05` §4 的"体力范围"不变量只写"濒死结算中间态除外"；把它精确收窄成
  "`hp < 0` 仅允许出现在 `NEAR_DEATH` 帧在栈上时"的是 **票 11 §79**——该帧归票 21。
  上限不变量（`hp ≤ hp_max`）在本条里逐行成立。
