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

## 已知过渡态（后续票会重录）

- `01` 的收尾段以**牌堆耗尽平局**（`reason = "deck_exhausted"`）收场：本票只实现使用链与
  【闪】抵消，伤害帧是票 20 的交付物，故双方都打不出伤害。票 20 落地伤害后会重录本条
  （升版判据见票 15 §3：拿既有基准重跑、字节变了就必须处置）。
