# AI 适配契约（AI adaptation）

**何时读**：这一票**新增**了询问类型 / 技能形状 / 动作面 / 信息通道，或者要上**新模式**的时候。
本文件是单一真源：两套 checklist 的正文与三张登记表的读法都只在这里；`AGENTS.md` 与
`docs/agents/skill-authoring/batch-ticket.md` 各留一行指针，正文不抄第二份。

判据的来源是 `.scratch/ai-light/spec.md` 的 §4（一次性产物，落地后没人读）；代码侧的机械
断言住 `tests/test_ai_adaptation.py`。本文件是**长期**的那一份。

## 0 · 机械面与非机械面

| 面 | 谁承载 | 判据 |
|---|---|---|
| 能机械判的（键集 / 覆盖 / 引用可解析） | `tests/test_ai_adaptation.py` | 漏登、幽灵登记、坏引用都必红 |
| 判不了的（语义是否反转、信息通道变没变） | **票面勾选** + 评审 | 人读，票面写"已做 / 不适用 + 一句理由" |

契约只约束**新进门**，不重评既有 `CATALOG`（§5 的边界）。能进仓门禁的只有代码侧断言：
"记牌守恒量变没变"这类声明写不进断言，就老老实实放票面。

## 1 · 新机制票 checklist

逐条在票面写"已做 / 不适用 + 一句理由"。六条对应六种"AI 被悄悄打废"的方式。

1. **询问面**：新增 `QueryKind` 成员？
   - 有 ⇒ **同一票**交：`_SCORERS` 一个分支 + ≥1 条确定性用例。scorer 可以先保守（常量也行），
     **但不许没有**——今天缺分支的症状是运行期 `KeyError`，不是一条红测试。
   - 复用既有询问 ⇒ 写"复用 X"，并确认**打分语义不反转**。例：拼点若复用 `SELECT_CARDS`，
     "牌面越大越好"与"想输就出小牌"正好相反 ⇒ 必须自带分支，或在票面写明豁免与理由。
   - 机械面：`set(_SCORERS) == set(QueryKind)`。
2. **动作面**：AI 要**自己构造**的候选变了吗？
   - 复合候选（预发动清单 / 转化清单 / 借刀两步 / 指定池 / 座位池）不在 `legal_actions` 里，
     AI 用 `getattr` 自建。query 上再长一个这类字段 ⇒ **登记 AI 读点表**，否则 AI 会静默忽略
     它（没有报错、只是永远走不到新分支）。
   - 纯 `legal_actions` ⇒ 不适用。
   - 已知坑一：`SelectOptionQuery.options` 不被读——非座位选项按 `action.choice` 查
     `_OPTION_VALUES`。新选项文本不登记就落 0 分、与"放弃"打平。
   - 已知坑二：座位步（`seat:` 选项）的敌我估值读的是 **`skill_id`**——`_score_seat_option`
     拿它去查 `_HOSTILE_SEAT_STEPS` / `_STOLEN_CARD_SEAT_STEPS` 两张前缀表。新的座位步技能
     不在那两张表里，就会静默落进"帮盟友"的缺省分支。
3. **语义条目**：`SKILL_VALUES`（`tests/test_heuristic_ai.py` 的三向同步会自动逼）。强制 / 锁定技
   写 `0.0` + 说明；条目说明写清"有无代价 / 读哪些字段"。
4. **覆盖场景**：AI 能合法发动一次（`_COVERAGE_CASES`，由数据派生）。不出询问的锁定 / 强制技
   用构造测试（直接造 query 与候选）。
5. **信息通道**：新增或遮挡了可见区？记牌守恒量（53 / 108 / 160）还成不成立？未知池变大要登记。
   ⚠️ 字段可以 `exclude=True`——`PlayerView` / `PlayerPublic` 上的先例是 `chained` / `hero` /
   `kingdom`，询问上的先例是 `skill_id` / `preactivation`。它们**不进记录流**，所以改动
   **不一定**触发金录像字节告警。票面因此必须写明"记牌 / 身份推断的输入变没变"，别指望
   金录像替你报警。
6. **回归**：≥1 条"不犯已知的蠢"用例进 `tests/test_ai_cases.py`；本票进决策 diff 批，diff 非空
   逐条解释（"为什么改对了"比"改了"重要）。

## 2 · 新模式票 checklist

比"换一副牌"更深的规则变化（队伍 / 胜负条件 / 回合结构变了）走这一套：**第 0 问** + 五条。

**第 0 问 · `Mode` 显式化**：新值加在 `src/sgs/data/identities.py` 的 `Mode` 上；要不要加
`RuleConfig` 字段？加了就是 header 三键变更 + 60 条金录像 / 47 条冻结录像全红——**这笔账在
模式票里先裁**，不留给实现者临场。值域靠座位数切分干净才不加字段（现有的两个模式都是）。

1. **关系**：AI 从哪个字段认敌友？队伍公开 ⇒ 退化成 0/1，隐藏 ⇒ 走身份概率档；投影里怎么可见。
   登记 `MODE_SITES[mode]["relation"]`。
2. **胜负**：`Victory.side` 现在的值域要扩成什么、终局条件是什么、AI 要不要读"我方已败"。
   登记 `MODE_SITES[mode]["victory"]`——尽量落成 `sgs/engine/victory.py` 里的**纯函数**
   （可单测、可审计）。
3. **信息通道**：新增 / 遮挡哪些可见区；记牌守恒量还成不成立；身份 / 队伍可见性怎么裁。
   身份在投影期就该抹掉（见 `_visible_identity`）：让"不泄露"是结构事实，不是约定。
4. **读点够不够**：`PlayerView` 缺什么 ⇒ **先改协议（另票）**，不许在 AI 里按 `getattr` 猜。
5. **步骤表 / 回合结构**：`build_steps(mode)` 该分叉哪些帧、先手怎么定、有没有共享池。
   登记 `MODE_SITES[mode]["steps"]`。
6. **回归**：≥32 局专属种子批，四判据与 `test_all_ai_games` 相同（跑完 / 终局与身份胜负一致 /
   零非法应答 / 可回放）+ ≥1 条关系用例。

机械面：`set(MODE_SITES) == set(Mode)`，四格齐全，实现站点能 `importlib` 解析到可调用者。

## 3 · 三张登记表

### 3.1 选路表 `_SCORERS`（`src/sgs/agents/heuristic.py`）

键 = `QueryKind` 成员，值 = `scorer(query, action) -> float`。断言：键集**恰等于** `QueryKind`；
合成反例：删一条分支 / 注入一个不存在的 kind 都必须变红。新增询问类型 ⇒ 同一票加分支。

### 3.2 AI 读点表 `_AI_READ_POINTS`（同文件）

按类分桶：`PlayerView` / `PlayerPublic` / `Query`（九类询问的**公共面**：`_QueryBase` 的字段与
各自声明的 `kind`）/ 各询问子类（**只**收自有字段）。值是 `(读?, 一句理由)`。

- 枚举的是 **`model_fields`**：漏登一个字段必红；登记协议里没有的字段（幽灵）必红；同一字段
  两处重复登记必红；理由留空必红。
- `exclude=True` 的派生面同样要登记——它们不进记录流、但**进询问**，AI 读不读是必须做的一次裁定。
  测试另有一条覆盖元断言专门盯这一点。
- 「读 / 不读」是**人工评审面**：机械断言只保证"漏登即红"，挡不住刻意写假的条目。理由里写
  **读它的那一处行为**（函数 / 表 / 分支，行号会漂移不必写），评审才复核得动。
- 最容易写错的方向是"其实读了、登记成不读"：鸭子读法 `getattr(query, "xxx")` 在字段名上不留
  痕，源码扫描也扫不全（同名字段跨类复用还会假红），所以机械面到"漏登即红"为止。写"不读"前
  先打开读取方（`CODING_STANDARDS.md` 第 1 条）——本表就漏过一次
  `SelectOptionQuery.skill_id`（座位步的敌我估值靠它查前缀表）。
- 新字段进来时：在**它所属的桶**里加一行。跨九类同义的字段（如 `kind`）放共用桶，别抄九遍。

现状里几处"不读"值得知道（这几处是有意为之，不是漏登记）：`RespondQuery` 的 `required` /
`window_frame`（候选里只有合法牌；嵌套靠处理区顶牌是无懈来判，不读层号）、
`SkillActivationQuery` 的三个冻结报文（生产者恒给 `0 / 0 / ()`）。
反方向的先例是 `RespondQuery` 的 `cause` / `target`：D1 无懈目标化（票 18）起它们进打分，
写成"不读"就把那条规则藏起来了——评审时对照读取方。另一条同类先例是 `deck_count` /
`discard`：C1 记牌 P(闪)（票 20）起两者进 `_score_attack` 的守恒量 / 未知池计算，登记已从
"不读"翻成"读"（理由指到 `_dodge_probability`）。

### 3.3 `Mode` 表 `MODE_SITES`（`src/sgs/data/identities.py`）

键 = `Mode`，值 = 固定四格 `relation` / `victory` / `steps` / `projection`（顺序见
`MODE_SITE_SLOTS`），每格是 `ModeSite(refs, reason)`。四格各回答一件事，新增模式的票在**写票
时**就要裁（不要留给实现者临场）：

| 格子 | 回答什么 |
|---|---|
| `relation` | AI 从哪个字段认敌友：队伍公开 ⇒ 退化成 0/1，隐藏 ⇒ 走身份概率档 |
| `victory` | 终局怎么判、`Victory.side` 的值域要扩成什么 |
| `steps` | `FrameKind` 帧的步骤序列怎么装配（`build_steps(mode)` 分叉哪几帧） |
| `projection` | 身份 / 队伍在参与者视角里怎么可见（该抹掉的在投影期就抹掉） |

- **引用是字符串**（`"模块路径:属性路径"`，如 `"sgs.engine.steps:build_steps"`）：`data` 不
  反向依赖 `engine` / `agents`，直接 import 会造出模块级环。解析留给审计。
- 断言：每个模式都在表里、四格齐全、每个 `ref` 都能 `importlib` 解析到**可调用者**、
  每一格都有理由。
- **解析只证明"指得到"，不证明"引擎真的走它"**——那一条是评审面（`CODING_STANDARDS.md`
  第 1 条：落点要连读取方一起看）。现有的几处都由产品调用点驱动：`Engine.__init__` 调
  `build_steps`、`project` 调 `_visible_identity`、`Engine._solve_identity` 调
  `solve_identity_outcome`、`Engine.check_game_end` 自己就是无身份局的终局分支。新加一格
  时同样把调用方核一遍，别只核"能解析"。
- 「不适用」= `refs=()` + 一句理由。例：无身份局没有身份 ⇒ 关系与投影两格不适用；但**胜负与
  步骤表照样要指到实现**（总得有人判终局、装配步骤表）。
- 已知形状不齐：无身份局的胜负规则内联在 `Engine.check_game_end` 的 else 分支，没有独立纯
  求解器。新模式别再照抄这个形状——落成 `victory.py` 的纯函数，`tests/test_ai_adaptation.py`
  的解析断言就核得住。

## 4 · 维护责任表

| 变化 | 谁改 | 怎么逼出来 |
|---|---|---|
| 新增 `QueryKind` 成员 | 规则工程票的作者（**同票**） | `set(_SCORERS) == set(QueryKind)` 红 |
| 新增 / 改动 `PlayerView` / `PlayerPublic` / 询问字段 | 改协议的票加字段；AI 票写"读不读" | 读点表漏登即红 |
| 新增 `Mode` 成员 | 新模式票的作者（写票时先裁） | `set(MODE_SITES) == set(Mode)` 红 |
| 实现站点改名 / 搬家 | 动站点的那张票 | `importlib` 解析失败 = 红 |
| 新技能 / 新装备 | 一将一票的作者 | `tests/test_heuristic_ai.py` 的三向同步 |
| 新选项文本 / 新的靶向座位步 | 技能票的作者 | **无**机械面（`_OPTION_VALUES` / `_HOSTILE_SEAT_STEPS` 一类分组表），靠 §1 第 2–3 条与评审 |

## 5 · 边界（不做什么）

- **不重评既有 `CATALOG`**：契约只管新进门；123 条既有技能的语义不因为这张表而"重新登记一遍"。
- **`Victory.side` 三值不补课**：值域怎么扩由新模式票裁，不在这里先钉。
- **"武将牌上的牌不进 `view`" 保持已知近似**：不动协议。真把它补进 `view` 时，读点表会立刻
  逼出"记牌读不读它"，而不是再留一个静默近似。
- **不塞进 `docs/agents/skill-authoring.md`**：那边被"硬停判据恰 13 条"钉死，且只覆盖武将技能，
  新模式无处安放。两边的交叉点只有一行指针（`batch-ticket.md`）。
- **不加"`SKILL_VALUES` 条目必须非零"**：不可机械判，还会误杀 0.0 的觉醒技先例。
