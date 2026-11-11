# 06: 基准重录 + 账本/ADR 收口（一次性）

**What to build:** 02 / 04 / 05 三处询问流改动落地后，**一次性**重录受影响的基准，并把这一轮的四条口径
写进账本与 ADR：① 转化牌与真牌同档；② 需要牌按档逐档问（不升档改为迭代）；③ 无懈只问合格者（OL 特殊处理的依据）；
④ 询问跳过策略与会话配置。

**Blocked by:** 02, 04, 05
Status: open
Type: task
Touches: tests/replays, tests/test_replays.py, 开发文档/06-里程碑与风险.md, 开发文档/04-AI与交互设计.md, docs/agents/domain.md, GLOSSARY.md
Ruling: spec §4（用户 2026-11-11 签：金录像可以且必须重录）

## 为什么必须重录（用户裁定）

> 「很多改动都被金录像挡住了，但是包含太多陈旧数据的金录像不能被称为金录像，有时候必须改变。」

**纪律**（写进 `## Answer` 与账本行）：
1. 金录像 = **冻结的行为契约**；行为**有意**变更时**必须重录**，不是"不许变"。
2. 重录只换字节，**不换"这条基准在证什么"**：`tests/replays/goals.py` 的机制断言一条都不许删/改弱。
3. 票面必须列**重录清单**：文件 / 变化的记录类别 / 为什么变。
4. 冻结表（`tests/test_replays.py` 的 `STANDARD_108_REPLAY_BYTES`）与 `tests/replays/README.md` 索引一并更新
   （README 有生成器，`test_the_readme_index_table_is_exactly_what_the_generator_produces` 钉着）。

## 预计重录面（实测）

- 共 **60** 条 `.jsonl`；含无懈窗口 **45** 条、含 DODGE 窗口 **59** 条。
- **无懈预筛（票 05）+ 策略缺省 ON（票 02）** ⇒ 大多数基准会少若干 `query` 记录 ⇒ 重录面大。
- **档位拆面（票 04）** 只动"同一窗口同时有提供者与真牌"的少数：实测基准里 **0 条**含【八卦阵】发动、
  **1 条**含【护驾】⇒ 预期只影响 1–2 条。
- 先在票 04/05 的 `## Answer` 里得到"变红清单"，本票再按它重录。

## 要做什么

1. 跑 `bash tools/check_goldens.sh` 与 `.venv/bin/pytest tests/test_replays.py -q`，把变红清单原样贴进 `## Answer`。
2. 逐文件重录（`.venv/bin/pytest tests/test_replays.py --update-golden`），**录完复核**：
   `bash tools/check_goldens.sh` 全绿 + `test_the_frozen_108_...` 通过 + README 索引一致。
3. 账本（`开发文档/06-里程碑与风险.md` §5）新增/改写四行：
   - 「**转化牌与真牌同档（需要牌第 2 档）**」：依据 = 官方 2018 规则集只有三档原则、无转化条文；移动版实际行为；程序化约定。
   - 「**需要牌按档逐档问**」：改写 `.scratch/timing-activation/spec.md` R3 的落地口径（不升档 = 迭代顺序），
     并补一句"档 0 非转化技能 / 档 1 非转化装备技 / 档 2 牌与一切转化"。
   - 「**无懈窗口只问合格者**」：依据 = 官方 2018 规则集按语（OL 对无懈的特殊处理）。
   - 「**询问跳过策略与会话配置**」：三分类 + 缺省 ON + 侧车 meta 还原。
4. ADR-019 的来源级别**升级**：把 `docs/agents/domain.md` 的"先读这些"从"社区编纂《三国杀规则集 V3.0》"
   补一条"官方规则集 2018（凌天翼，官方微博发布）"作为同一条轴的更高一级来源（社区规则集仍是补充）。
5. `GLOSSARY.md` 补/改三条：**档位**（需要牌的三档 + 派发按档迭代）、**声明 / 生效**（补"只问合格者"一句）、
   **预发动**（补"不是弹窗"的实现口径，见票 08）。

## 验收

- `bash tools/check_goldens.sh` 全绿；`bash tests/test_dev_docs.sh` 绿；门禁全量绿。
- `## Answer` 里必须能看到：变了多少条、每条为什么变、机制断言为什么没变。
