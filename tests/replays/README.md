# Golden Replay 基准索引

一局一文件：文件内容就是引擎的**记录流**（canonical JSONL，首行 `header`），
既是期望输出、也是回放输入（种子在 `header`，完整 `Action` 内联在 `action` 记录里）。
验证 = 从文件抽出 `(seed, tape)`，用当前引擎重跑一局，与文件**全量逐行字节比对**（票 10 §2）。

- 录制 / 重录的唯一入口：`pytest --update-golden`（票 10 §6；不给产品 CLI 加参数）。
- 场景脚本玩家（`scenarios.py`）**只在录制时使用**；验证期一律 `TapePlayer`。
- `tests/test_replays.py` 断言本表的 slug 集合与 `*.jsonl` 集合**互相等于**（防孤儿），
  并按 `distribution.py` 的**十一类**必需表判决**分布判据**（票 17 §1 / 票 32 §1 / 票 14 §8 /
  M5-09a / M6 票 20 / M7 票 12）。

## 分布判据（票 32 §1 / 票 14 §8）

`≥30` 条只是门槛，不是目标。真源 = `tests/replays/distribution.py`：**类 → 最少条数**
（人数骨架 5 / 判定与延时锦囊 4 / 无懈可击与响应链 5 / 死亡奖惩 3 / 多人濒死 3 /
装备·距离·转化 4 / 普通锦囊与打出 4 = **28 条 M2 主题新增** + 武将技能 8 = **36 条新增**）
+ M1 六条回归 + 脚本专项（`43`–`47` 五条；`33` 由 M2 移入本表，已算在 28 条里，不另计）
+ 军争机制 8 条（`48`–`55`）+ 界局机制 5 条（`56`–`60`）+ **PvE 终局 6 条**（`61`–`66`）
= **66 条**；判决"每类达标、每个 slug 恰属至少一类、索引 == 文件集合 == 类表"。

## 录制驱动（票 32 §1 / 票 14 §8）

- M1 六条走 `ScenarioPlayer`：录制期精确迫使目标链发生（票 12 §3）。
- **脚本专项**（票 17 起）同样走 `ScenarioPlayer`，但不属 M1 回归网：它们是"某个机制
  在随机驱动下凑不齐触发前提"的确定性复现（`43-double-sword-opposite-gender` = 需要某人装备
  【雌雄双股剑】、手里有【杀】、对**异性**目标使用，而标准牌堆只有一张雌雄）。驱动里
  **读局面事实**（性别）来挑目标，判据在 `goals.M1_GOALS`，录制期与回放期各验一遍。
  `44-preactivation-cancel-no-side-effect`（W4-5）证的是接入层本地态：进入预发动、按清单
  填好复合动作并交给引擎同一个校验器、然后**取消**，与"从不进预发动"的同种子局逐字节相等
  （录制期由 `scenarios.preactivation_cancel_pair`、门禁期由 `tests/test_replays.py` 各判一次）。
  W5-02b 起 `33`（借刀 a 支）也移入本表（裸候选收回后随机驱动产不出借刀动作，但它仍是
  「普通锦囊与打出」的覆盖项），并新增 `45`（auto-b）与 `46`（窗口内转化【杀】）；
  W7-01 新增 `47`（借刀窗口里的方天：持刀者 Bn + 2 个额外目标，真【杀】）。
- M2 27 条走**随机驱动 + 事后判据**：录制期用与产品同源的 `RandomPlayer`（测试侧种子子流，
  确定性）跑完整局，再断言"这条基准要证的事真的发生过"（判据在 `goals.py`）。
- M3 8 条武将技能专项基准同样走随机驱动 + 事后判据，但用 `Scenario.heroes` **显式逐座位**
  指定武将（`heroes=()` 是"数据文件启用池随机发放"，无武将则缺省 `None`）；判据在
  `goals.M3_GOALS`，与 M2 同一门禁口径。磁带冻结的是**决策内容**，回放期一律 `TapePlayer`，
  与驱动无关；种子由开发期搜索得到、写死在 `scenarios.py`，录制期零搜索。
- M5 8 条军争基准（`48`–`55`）走**构造局面**：手牌 / 装备 / 牌堆顶 / 横置态全部写死
  （`replays/m5.py` 的 `Plan` + `Engine.from_state`），不依赖随机种子、不做种子搜索；
  回放与录制共用 `scenarios.engine_for` 这一个开局入口。判据在 `goals.M5_GOALS`，覆盖
  11 个新牌名（火杀 / 雷杀 / 酒 / 兵粮寸断 / 火攻 / 铁索连环 / 白银狮子 / 藤甲 /
  古锭刀 / 朱雀羽扇 / 骅骝）与 spec §6 A3 的 10 个覆盖项（属性 / 连环 / 酒 / 藤甲 /
  白银 / 古锭刀 / 朱雀 / 火攻 / 兵粮 / 重铸）。
- M6 5 条界局基准（`56`–`60`）同走**构造局面**（`replays/m6.py`），判据在 `goals.M6_GOALS`。
- **PvE 6 条**（`61`–`66`，M7 票 12）也走**构造局面**（`replays/pve.py` 的 `Plan` +
  `Engine.from_state`）：**座位表来自 header 的 `pve.seats`**（本模块只声明人数 / 档位 / 种子），
  手牌 / 牌堆顶写死在 `pve.py`；判据在 `goals.PVE_GOALS`，终局类由
  `tests/test_replays.py` 用 `solve_pve_outcome` 逐条重算复核。

## 录制 M2 基准：先跑种子搜索（加基准 / 换种子时）

**新增 M2 基准、或事后判据录制期没命中要换种子时，先读这里。** 从零搜出全部 27 条
`(n, seed)` 并核对下表写死的值：

```bash
PYTHONPATH=tests .venv/bin/python -m replays.seed_search
```

它按三句话算法搜（`seed_search.py` 是唯一真源）：每个 `(n, seed)` 只跑一次引擎、把
`goals.M2_GOALS` 全套一遍收集候选；每条基准只留**记录条数最少**的候选（更短的局 = 更小的
golden + 更快的回放）；候选最少的基准先做贪心去重。**人数档**也写在那里：人数骨架
`07`–`11` 锁死 4/5/6/7/8 人，其余 22 条在 4/5/6 人里自由选。

退出码非零 = 有基准没搜到候选、或下表写死的值的**有效性**核对失败（`--verify` 单独跑）；
要按票 32 Done when 的字面口径把"搜索 ≠ 写死值"也当失败，加 `--strict`。
注意从零搜索的最小候选会与写死值不同：`goals.py` 与引擎在票 32 的搜索之后才定型，同一条
`(n, seed)` 的记录条数已经变了（例如 `16` 由搜索期的 1293 变成 golden 里的 1797）；
工具把这种漂移原样打印，但不据此判失败——写死的旧候选仍然有效，换基准时才需要用搜到的新
值重写 `scenarios.py` 并 `pytest --update-golden` 重录。

它是**开发期工具，不在门禁里**（不是第二个门禁入口）：门禁只回放，不做搜索。

下面那张六列索引表由 `python -m replays.readme_index --write` 生成（类 / 档位 / 人数取自
`distribution.py` 与 `scenarios.py`，中文名与规则条目取自 `readme_index.NAMES`）——**别手改表格行**。

| slug | 中文场景名 | 类 | 档位 | 人数 | 验证的规则条目 |
|---|---|---|---|---|---|
| `01-slash-dodge-cancel` | 【杀】→【闪】抵消 | m1-regression | `basic_53` | 2 | 使用与打出 / 无效·抵消·失效 / 使用全步骤（规则 4.3 / 17.6 / 17.10）；L4 六个时机 + L5 的 `RESOLVE_BEFORE_EFFECT` / `RESOLVE_END` / `RESOLVE_AFTER_END`（**不出现** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）；手牌 → 处理区 → 弃牌堆两段移动（`reason` 均为 `USE`） |
| `02-slash-hit-damage` | 【杀】→不闪→伤害 | m1-regression | `basic_53` | 2 | 使用全步骤 / 数值四事件（17.10 / 4.13–4.15）；L5 六个（**含** `RESOLVE_EFFECT` / `RESOLVE_AFTER_EFFECT`）→ 伤害帧 L7 五个 → 其中嵌套扣减体力帧 L8 三个；一条 `hp_change` delta |
| `03-near-death-self-peach` | 濒死求桃：自救 | m1-regression | `basic_53` | 2 | 濒死（规则 4.17 / 4.7）；伤害 → 扣减体力 → `NEAR_DEATH_ENTER` / `AT` / `AFTER` 三时机；濒死者自己在 `NEAR_DEATH_AT` 出【桃】→ 回复帧 L10 两个 |
| `04-near-death-other-peach` | 濒死求桃：他人救 | m1-regression | `basic_53` | 2 | 同 `03`，但**当前回合角色**对濒死者用【桃】→ 后面的应答者不再被问；回复结算同样插在濒死之下 |
| `05-near-death-death` | 濒死无人救 → 死亡 → 胜负 | m1-regression | `basic_53` | 2 | 濒死（规则 4.17）与死亡 / 胜负判定时点（4.18 / 4.19）；`DEATH_*` 四段 → 逐帧 `discard` 五帧 → `game_end`（**最后一条**） |
| `06-deck-reshuffle` | 牌堆耗尽 → 重洗 | m1-regression | `basic_53` | 2 | 牌堆耗尽（规则 10.4）：每次重洗只留**一条** `deck_shuffle` delta；**不出现** `card_move(DISCARD→DECK)`；初次洗牌仍是 `seq=1`、`frame_id=0` |
| `07-identity-4p-rebel-win` | 4 人身份局 · 反贼胜 | 人数骨架 | `standard_108` | 4 | 身份配比 1-1-1-1；身份发放先于发牌；主公 +1 体力与先手；反贼胜的终局 `side=rebel` |
| `08-identity-5p-lord-win` | 5 人身份局 · 主忠胜 | 人数骨架 | `standard_108` | 5 | 身份配比 1-1-2-1；主忠方胜的终局 `side=lord`、`winners` 含阵亡者 |
| `09-identity-6p-renegade-win` | 6 人身份局 · 内奸胜 | 人数骨架 | `standard_108` | 6 | 身份配比 1-1-3-1；主公死亡瞬间除主公外唯一存活者为内奸 ⇒ `side=renegade` |
| `10-identity-7p-lord-win` | 7 人身份局 · 主忠胜 | 人数骨架 | `standard_108` | 7 | 身份配比 1-2-3-1；7 人座次与回合轮转 |
| `11-identity-8p-rebel-win` | 8 人身份局 · 反贼胜 | 人数骨架 | `standard_108` | 8 | 身份配比 1-2-4-1；8 人座次、逆时针轮转与终局 |
| `12-indulgence-skips-play` | 【乐不思蜀】命中 | 判定与延时锦囊 | `standard_108` | 4 | 延时锦囊使用（L4 / L5）→ 判定阶段 L14 三时机；非红桃 ⇒ 跳过出牌阶段 |
| `13-indulgence-heart-misses` | 【乐不思蜀】未命中 | 判定与延时锦囊 | `standard_108` | 5 | 同上，判定牌为红桃 ⇒ 正常进入出牌阶段 |
| `14-lightning-thunder-damage` | 【闪电】命中 | 判定与延时锦囊 | `standard_108` | 4 | ♠2~9 ⇒ 3 点**无来源**雷电伤害；`hp_change.amount = -3` |
| `15-lightning-defers-next-seat` | 【闪电】顺延下家 | 判定与延时锦囊 | `standard_108` | 4 | 未命中 ⇒ `PROCESSING → JUDGE(下一合法目标)`，`owner=null` |
| `16-nullify-cancels-a-trick` | 无懈抵消单体锦囊 | 无懈可击与响应链 | `standard_108` | 6 | 逐目标无懈窗口（`RESPOND/NULLIFY`）挂在该目标 `RESOLVE` 帧的 `生效前`；奇偶律 |
| `17-nullify-nested-two` | 无懈嵌套 2 层 | 无懈可击与响应链 | `standard_108` | 4 | 无懈也是锦囊：嵌套 `RESOLVE` 帧、后发生的事件先结算 |
| `18-nullify-nested-three` | 无懈嵌套 3 层终止 | 无懈可击与响应链 | `standard_108` | 4 | 更深嵌套的链在同一出栈序里自底向上传播 |
| `19-nullify-on-multi-target` | 多目标锦囊被逐目标无懈 | 无懈可击与响应链 | `standard_108` | 5 | A 段外层 / C 段逐目标：无懈窗口按目标分槽 |
| `20-nullify-lightning-defers` | 被无懈的延时锦囊传下家 | 无懈可击与响应链 | `standard_108` | 4 | 闪电被无懈 ⇒ 收尾把实体牌传下家判定区（`card_scroll.txt:219`） |
| `21-rebel-killed-reward` | 杀反贼摸 3 | 死亡奖惩 | `standard_108` | 4 | 死亡奖惩（规则 4.8）：身份检测 → 系统处理牌 → 执行奖惩；凶手摸 3 张 |
| `22-lord-kills-loyalist` | 主公杀忠臣罚弃 | 死亡奖惩 | `standard_108` | 5 | 主公杀死忠臣 ⇒ 主公弃置自己的手牌 / 装备区 / 判定区 |
| `23-two-deaths-revealed` | 两次阵亡与亮明 | 死亡奖惩 | `standard_108` | 4 | `identity_reveal` 在 `DEATH_REVEAL_IDENTITY_BEFORE` 内部、时机技能之后、检测之前 |
| `24-multi-player-peach-window` | 多人求桃窗口 | 多人濒死 | `standard_108` | 5 | 应答者区间 = 当前回合角色起逆时针、终点 = 其上家；一个窗口多个应答者 |
| `25-rollback-same-start` | 新濒死回退 · 同一起点重开 | 多人濒死 | `standard_108` | 4 | 同一 `NEAR_DEATH` 帧内第二条 `NEAR_DEATH_AT`；第二窗起点 = 刚响应完的 B |
| `26-rollback-start-advances` | 新濒死回退 · 起点前进 | 多人濒死 | `standard_108` | 4 | 新区间 = 旧区间的后缀：B 之前已问过的人不再问 |
| `27-slash-beyond-adjacent` | 距离 / 攻击范围可观测 | 装备/距离/转化 | `standard_108` | 6 | 一次【杀】指到座位距离 ≥ 2 的目标（武器攻击范围） |
| `28-equipment-replacement` | 装备替换 | 装备/距离/转化 | `standard_108` | 4 | 新牌 `PUT` 入槽 + 旧牌 `LOSE` 入弃牌堆（一次移动同时失去新旧两张） |
| `29-serpent-spear-virtual-slash` | 丈八蛇矛 · 转化【杀】 | 装备/距离/转化 | `standard_108` | 4 | 两张手牌转化为虚拟【杀】，虚拟牌 `card_id < 0`、`virtual_sources` 非空 |
| `30-halberd-three-targets` | 方天画戟 · 多目标 | 装备/距离/转化 | `standard_108` | 6 | 此【杀】是你最后的手牌时可多指定至多两个目标 |
| `31-play-card-response` | 打出链（南蛮 / 万箭 / 决斗） | 普通锦囊与打出 | `standard_108` | 4 | `PlayCard` 独立构造子：无目标、走 L6、**不占使用次数** |
| `32-five-grains-selection` | 五谷丰登 · 选牌 | 普通锦囊与打出 | `standard_108` | 4 | `SELECT_CARDS` 选择面；X = 目标数、剩余牌进弃牌堆 |
| `34-dismantle-or-snatch-equip-judge` | 拆顺取装备区 / 判定区 | 普通锦囊与打出 | `standard_108` | 4 | `SELECT_CARDS` 选择面含装备区或判定区；判定区先入在前 |
| `35-hero-damage-points` | 【遗计】逐点伤害 | 武将技能 | `standard_108` | 4 | B 族按点触发：一次多点伤害里同一时机被派发多次、各自问一次「是否发动」；`DAMAGE_AFTER_TAKE` 的两个时机实例 |
| `36-hero-rejudge-cycle` | 【鬼才】改判循环 | 武将技能 | `standard_108` | 4 | I 族改判窗口（`WindowCause.REJUDGE`）：判定牌生效前打出代替；`JUDGE_CARD_BEFORE_EFFECT` 可循环重开 |
| `37-hero-lord-request` | 【护驾】/【激将】势力求助 | 武将技能 | `standard_108` | 4 | F 族主公技：`WindowCause.LORD_REQUEST` 的势力作用域求助窗口；同势力角色打出【闪】/【杀】 |
| `38-hero-lost-cards` | 【连营】+【枭姬】含顶替 | 武将技能 | `standard_108` | 4 | D 族 `MOVE_AFTER` 内联时机：失去最后的手牌 / 失去装备区里的一张牌（含同槽顶替的旧装备那条 `LOSE`） |
| `39-hero-target-pool` | 【空城】+【谦逊】目标池 | 武将技能 | `standard_108` | 4 | C 族目标池真排除：【谦逊】无条件、【空城】手牌为零时，受限牌的目标候选里没有受保护座位 |
| `40-hero-wushuang` | 【无双】两张【闪】 | 武将技能 | `standard_108` | 4 | E 族响应窗口张数：一次【杀】的生效前连开两个 `DODGE` 窗口，两张【闪】才抵消 |
| `41-hero-guanxing` | 【观星】排列 | 武将技能 | `standard_108` | 4 | G 族授予可见性与排列：`ARRANGE` 询问 + `ArrangeCards`（只记面内位置，无带牌面的 delta） |
| `42-hero-transform` | 转化族（武圣 / 龙胆 / 国色 / 急救） | 武将技能 | `standard_108` | 4 | H 族转化牌泛化：`UseCard` / `PlayCard` 的 `virtual_sources` 非空；来源实体牌经使用链搬进处理区再进弃牌堆 |
| `43-double-sword-opposite-gender` | 【雌雄双股剑】异性目标的二选一 | 装备/距离/转化 | `standard_108` | 4 | 装备牌在出牌阶段的 `UseCard` + 【杀】的 `USE_AFTER_DESIGNATE_TARGET`：`SELECT_OPTION` 的**选择权在目标**（`actor` = 目标）、选项 `(discard_one, draw_one)`；答 `draw_one` ⇒ 使用者摸一张（`to_zone=hand(0)`、`reason=draw`） |
| `44-preactivation-cancel-no-side-effect` | 预发动取消零副作用 | 武将技能 | `basic_53` | 2 | 接入层本地态不进记录流：`PLAY_PHASE` 随附【制衡】的预发动清单（`preactivations`）而整局没有任何被接受的 `ActivateSkill`；同种子下取消路径与「从不进预发动」路径的记录流逐字节相等 |
| `45-borrowed-sword-auto-handover` | 【借刀杀人】a 不可行 → 自动交出武器 | 普通锦囊与打出 | `standard_108` | 2 | 借刀的附带指定清单（`designations` + `card_preactivations`，规则 6.5）；持刀者没有可用【杀】/**转化【杀】** ⇒ **不发**持刀者窗口，武器直接从装备区移进使用者手牌（`reason=LOSE`）；记录流里没有 `BORROWED_SWORD` 询问 |
| `46-borrowed-sword-transform-slash` | 【借刀杀人】窗口内的转化【杀】 | 普通锦囊与打出、武将技能 | `standard_108` | 2 | 借刀窗口的目标固定为使用者指定的 Bn；持刀者按【龙胆】的预发动清单确认成 `UseCard(虚拟【杀】, (Bn,))`；转化技在响应窗口的清单只有**来源步**（`fixed_target=True`） |
| `47-halberd-borrowed-sword` | 【借刀杀人】+【方天画戟】· 多目标 | 普通锦囊与打出、装备/距离/转化 | `standard_108` | 4 | 【方天画戟】的 `TARGET_COUNT` 修饰器：持刀者的【杀】是**最后一张手牌**时最大目标数 +2 ⇒ 借刀窗口的候选恒含 Bn、另加 0..2 个其它合法目标（`targets` 里）；外层借刀的 `designations=(Bn,)` 不变，持刀者【杀】的 `designations` 为空 |
| `33-borrowed-sword-use-slash` | 借刀杀人 · 使用【杀】 | 普通锦囊与打出 | `standard_108` | 2 | 借刀的两步清单（`card_preactivations`）：使用者指定 [持刀者, Bn]，持刀者在 `RESPOND/BORROWED_SWORD` 窗口用真【杀】（`UseCard`，使用而非打出） |
| `48-fire-slash-vine-armor` | 火【杀】× 藤甲 | 军争机制 | `standard_ex_160` | 2 | 属性【杀】是独立牌型（火 / 雷）；普通【杀】对藤甲无效（零伤害）而火【杀】+1（`hp_change.amount = -2`）——两条 `UseCard` 各自落账，装备经 `PUT → EQUIP` 记录 |
| `49-iron-chain-thunder-conduction` | 铁索连环 · 雷【杀】传导 | 军争机制 | `standard_ex_160` | 2 | 【铁索连环】的 1–2 目标横置（两次 `chained_change=true`）；雷【杀】的属性伤害沿连环传导到另一横置角色，命中后各座 `chained_change=false`（同一伤害事件内重置） |
| `50-wine-silver-lion-cap` | 【酒】+【白银狮子】上限 | 军争机制 | `standard_ex_160` | 2 | 【酒】把伤害 +1（`wine_change=true`，兑现后 `false`）；【白银狮子】把这次伤害压回 1（只有一条 `hp_change=-1`），装备经 `PUT → EQUIP` 记录 |
| `51-fire-attack-reveal-discard` | 【火攻】亮牌 + 同花色弃牌 | 军争机制 | `standard_ex_160` | 2 | 目标亮出 1 张手牌（`card_reveal` 公开 delta，牌不移动）；使用者弃一张**同花色**手牌 ⇒ 1 点火焰伤害；未亮出的手牌不进记录流 |
| `52-scimitar-empty-hand-hualiu` | 【古锭刀】+【骅骝】+【兵粮寸断】 | 军争机制 | `standard_ex_160` | 2 | 【兵粮寸断】判定非梅花 ⇒ 跳过摸牌阶段（该回合零 DRAW）；目标因此空手牌 ⇒ 【古锭刀】+1（2 点伤害）；【骅骝】的 +1 距离（2）仍被攻击范围 2 够到 |
| `53-supply-shortage-skips-draw` | 【兵粮寸断】跳过摸牌阶段 | 军争机制 | `standard_ex_160` | 2 | 延时锦囊 `PUT → JUDGE(座位 1)`；判定牌非梅花 ⇒ `hp` 不变但该回合没有该角色的 DRAW 移动（跳过摸牌阶段） |
| `54-reforge-iron-chain` | 【铁索连环】重铸 | 军争机制 | `standard_ex_160` | 2 | 重铸是独立 `Reforge` Action（不是 `UseCard`）：牌按 `reason=REFORGE` 进弃牌堆、随后摸一张，且**不开**无懈窗口、不占使用次数 |
| `55-fan-converts-normal-slash` | 【朱雀羽扇】转化火【杀】 | 军争机制 | `standard_ex_160` | 2 | 票 09 起改为**询问式**（"当你使用普通【杀】时，可以将其转化为火【杀】"）：先装扇（`PUT → EQUIP`）、再用普通【杀】⇒ 引擎开一次「是否发动」（零步流水线，骨架 `ActivateSkill`）+ 确认后这次使用按**虚拟火【杀】**结算并造成火焰伤害（记录流里的 `UseCard` 仍是玩家选的那张普通【杀】） |
| `56-jie-guanyu-yijue-suppress` | 【义绝】施加技能失效 → 回合末到期 | 界局机制 | `standard_108` | 2 | 界关羽【义绝】弃一张牌 ⇒ 目标展示黑牌 ⇒ 座位级失效落一条 `skill_suppress`（`suppressed=true`，`scope=turn`），同一回合末落一条 `suppressed=false`；目标手里那张牌只以 `card_reveal` 公开、不移动 |
| `57-jie-xushu-awakening` | 【潜心】觉醒 → ☆【荐言】 | 界局机制 | `standard_108` | 2 | 觉醒技强制发动：造成伤害后（来源是自己、且自己已受伤）落 `hp_max_change`（4 → 3）与 `skill_gain`（☆【荐言】），两者之间没有「是否发动」询问；随后 ☆ 技能作为 `PLAY_PHASE` 的裸 `ActivateSkill` 候选真的被发动 |
| `58-jie-luxun-hero-zone` | 【谦逊】武将牌上的牌区 · 放置与归还 | 界局机制 | `standard_108` | 2 | 一张延时 / 普通锦囊唯一指向界陆逊 ⇒ 【谦逊】把**全部手牌**置于 `Zone.HERO`（区键 `0:hero.jie_luxun.qianxun`，`card_move` 的 `to_zone.skill_instance` 非空）并触发【连营】——「令至多 X 名角色各摸一张」是**一次**座位多选（`SELECT_OPTIONS` + 不枚举档，`lianying:seat`，一次点齐座位 0 / 1）；当前回合结束时整批归还（`HERO → HAND`）。同一帧里【连营】第二次触发**重新发问**（答案缓存按派发范围分桶） |
| `59-jie-lvmeng-extra-turn` | 【博图】额外回合 + 轮次变化 | 界局机制 | `standard_108` | 2 | 弃牌阶段弃出四花色 ⇒ 回合结束时【博图】给一个额外回合：`TURN_START` 序列出现同座位连着的两个回合，`turn_change` 先落 `extra=true`（轮次不变）；额外回合不推进自然指针、也不刷新「每轮限 X 次」，回到座位 0 的自然回合时落 `extra=false` 且 `round=2` |
| `60-jie-zhenji-luoshen-tags` | 【洛神】牌标签 · 增与删 | 界局机制 | `standard_108` | 2 | 界甄姬【洛神】判定为黑色 ⇒ 获得此牌并挂「本回合不计入」标签：`tag_add` 带牌面 + 标签类型 + 来源实例；回合末清除落同形的 `tag_remove`。标签与所在区域无关、也不改变公开的「手牌数」（手牌计数是另一个查询） |
| `61-pve-rout-win` | PvE · 全歼敌人（胜） | PvE 终局 | `standard_108` | 3 | `pve.victory.kind = rout`（票 01 §4 的表）：【万箭齐发】同一批打死两名敌人 ⇒ `game_end` 的 `side = ally` / `reason = enemies_routed` / `winners` = 全侧友方座位；身份局的奖惩与主公锚点在 PvE 全不生效（没有 `identity_reveal`、杀敌不摸 3） |
| `62-pve-survive-rounds-win` | PvE · 存活 N 轮（胜） | PvE 终局 | `standard_108` | 3 | `survive_rounds`（N = 1）：谁都不进攻，`GameState.round` 推进到 2（`round > N`）那一刻判胜 ⇒ `side = ally` / `reason = rounds_survived`；判点在**轮次 +1 之后**，轮次只有唯一计数源（不另建计数器） |
| `63-pve-rout-within-rounds-win` | PvE · N 轮内全歼（胜） | PvE 终局 | `standard_108` | 3 | `rout_within_rounds`（N = 1）：第 1 轮内全歼 ⇒ `side = ally` / `reason = enemies_routed_in_time`；硬边界 = **先判轮次、后判全歼**（跨过 N 之后即使敌人全灭也不记这一场的胜） |
| `64-pve-slay-boss-win` | PvE · 击败 Boss（胜） | PvE 终局 | `standard_108` | 2 | `slay_boss`（`pve.boss = 1`）：Boss 座位阵亡（死亡检测点，与主公锚点同层）⇒ `side = ally` / `reason = boss_slain`；1v1 里 Boss 是合成武将「黄巾渠帅」，不是武将表记录 |
| `65-pve-ally-team-rout` | PvE · 队友局 2v3（胜） | PvE 终局 | `standard_108` | 5 | 2 友 × 3 敌：身份由 `pve.identities` 直接给出且**全部 `identity_visible`**（开局公开，不洗身份、不消耗 `identity` 子流）；AI 队友座位（3）真的行动并用【万箭齐发】收局，`winners = (0, 3)` = 全侧友方座位（含队友） |
| `66-pve-protected-ally-death` | PvE · 保护队友阵亡（败） | PvE 终局 | `standard_108` | 4 | `pve.protected_ally = 1`：受保护队友阵亡 ⇒ `side = enemy` / `reason = protected_ally_dead`，而**主角还活着**（败因不是主角阵亡）；败优先于胜（与任何胜利条件同批发生也判败） |

## 记录面 v2（施工票 21 落地后）

- `header.event_schema_version = 2`、支持集合 = `{2}`（**不建** v1 兼容读取器；
  版本错误的报错带"过旧 / 过新"方向词）。仓库里不允许残留 v1 文件。
- 区域引用是**区域坐标**对象 `{"zone": ..., "seat": ...}`（`seat` 显式写出，含 `null`；
  共享区为 `null`）；`card_move` **没有** `from_seat` / `to_seat` 冗余字段。
- `card_move.owner` = **归属**（本次移动算谁的）：摸牌归摸牌者、弃置 / 使用归弃牌者 /
  使用者、装备置入归持有者；"无角色主体"的收尾移动才是 `null`。判定牌的两条移动
  （`DECK→PROCESSING`、`PROCESSING→DISCARD`）归**被判定者**（归属 ≠ 区域坐标）。
- 每条内联牌面多一个 `slot`（装备槽位；`basic_53` 的每张牌都是 `null`），
  `PlayerPublic` 多 `equip` / `judge` / `identity` 三个公开字段（身份只对本人 / 主公 /
  已死亡者可见，其余为 `null`，票 13 §6）；手牌上限仍是**派生函数**，不落字段。
- 记录流多两个 `delta_type`：`identity_deal`（开局暗发，票 24）与 `identity_reveal`
  （阵亡亮明，票 25）；`game_end` 多 `side` / `winners` 两个字段——无身份局/平局
  `side=null`（胜局 `winners=[获胜座位]`、平局 `[]`），身份局 `winner=null`、
  `side` + `winners` = 获胜一侧全部座位（含阵亡者，票 25 / 决策回票 14）。
- M1 六条基准的**决策轨迹**摘要冻结在 `tests/test_replays.py::M1_FROZEN_TRAJECTORIES`；
  摘要剔除 v2 新增的牌面事实字段后逐条相等，证明升版没改语义。

## 牌堆默认档（票 32 收口）

- 产品默认档 `load_content_pack()` = `standard_108`；`basic_53` 仍可按档显式选择，
  M1 六条基准与 M1 回归单测全部**写死** `basic_53`（票 16 §9）。
- 每条基准的 `header.deck_profile` 是回放选档的唯一真源（`test_replays` 按它加载），
  因此"默认档"的改动不会泄漏进基准。

## 整局终止口径（票 22 落地后）

- **每条基准都以 `game_end` 收局**：死亡结算、胜负判定、终局清栈与处理区收尾都已落地；
  随机驱动的整局也一样（CLI 门禁断言末行是终局记录）。
- 死亡路径的逐帧 `discard` 顺序、`DEATH_ON` 遗牌、终局收尾与胜负语义由
  `tests/test_death.py` 逐条断言。
- 记录流里会出现 **体力 ≤ 0** 的行（含 M2 闪电的 **−2**）：它是濒死结算的中间态，
  或阵亡者保留的负体力（fuzz 的体力不变量按"阵亡者除外"读）。

## 重洗落地后的收局口径（票 23）

- 牌堆耗尽不再等于终局：**弃牌堆非空就一定重洗补足**，故 `game_end.reason = "deck_exhausted"`
  不再产生；短摸只可能意味着**两堆皆空**（实战不可达，由构造局面单测覆盖）。
- 因此**谁都不进攻的整局不会自然终止**（牌堆被弃牌堆无限补足）；基准与结构性单测都靠
  "打死一方"或以 `tests/support/positions.py` 的 `run_turns` / `run_until` 主动跳出。
- `03` / `04` 因此**重录**：它们原先停在"牌堆耗尽平局"，现在继续跑并最终以胜负收局；
  `01` / `02` / `05` 在重洗落地前后逐字节不变（死亡先于第一次重洗）。

## 重录登记：需要牌窗口按档逐档问（table-ux-overhaul 票 04）

**行为有意改变**（spec §3 注① / §4：本 effort 只有票 04 与票 13 可以动基准字节）：四扇
"需要（使用 / 打出）牌"窗口的候选面改成**一档一张面**（Q13 / Q17 的档表：0 = 非转化
技能、1 = 非转化装备技、2 = 真牌与一切转化），`Pass` = 本档不发动、进下一档；同一时机
的激活结算同样逐档问（`Pass` = 本档不发动）。候选面变了 ⇒ 每次需要牌事件的询问条数
可能从 1 变成 2–3，随机驱动的行动序列也跟着变。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `09` / `20` / `31` | 同种子重录：行动序列偏移（下游动作 / 记录条数变化） | 档位分面改变了随机档在需要牌窗口里的可选面与随机流消耗 |
| `17` | 重新选种 117 → 163（1963 → 1065 条） | 同上的漂移让旧局变长（约 1.7 MB）；按本模块"记录条数最少"的纪律换种，判据（两层无懈嵌套）不变 |
| `30` | 重新选种 230 → 904（4188 → 2845 条） | 旧局涨过 4 MiB 单文件绊线；904 命中同一判据（方天一次指定 3 个目标）、人数档仍是 6 |
| `34` | 重新选种 288 → 187（2083 → 746 条） | 旧局变长（约 1.7 MB）；187 命中同一判据（拆顺取装备区 / 判定区） |
| `35` | 重新选种 198 → 73（1592 → 905 条） | 旧局变长；73 命中同一判据（【遗计】逐点 ≥2 次） |
| `36` | 重新选种 169 → 167（2114 → 853 条） | 旧局变长；167 命中同一判据（改判窗口开过） |
| `37` | 重新选种 94 → 209（1618 → 948 条） | 旧种子在新行为下**不再命中**判据（势力求助窗口没开）；209 命中同一判据 |

`goals.py` **一字未改、一条未放宽**（判据仍断言"这条基准在证什么"）；其余 51 条基准
逐字节不变。重录只换字节，不换口径：`pytest --update-golden` 是唯一入口，冻结表
（`tests/test_replays.py` 的 `STANDARD_108_REPLAY_BYTES`）与本文档同批更新。

## 重录登记：座位多选收口（table-ux-overhaul 票 13）

**行为有意改变**（spec §3 注① / §4：本 effort 只有票 04 与票 13 可以动基准字节）：
"一次选多名角色"的询问从**逐座多次** `SELECT_OPTION`（`lianying:seat:<n>` /
`dawu:seat:<n>` / `fenwei:<n>`）收口成**一次** `SELECT_OPTIONS`（`enumerate_candidates=False`
的不枚举档，答案是一个座位集合）；步骤语言的 `PickSeats(hi > 1 ∧ not ordered)` 同批走
`Engine.ask_seats`（【突袭】/ 界【突袭】/【除疠】）。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `58-jie-luxun-hero-zone` | 同种子重录（56,187 → 54,999 字节；153 → 151 条记录） | 界陆逊【连营】的两步逐座询问合成一步座位多选：询问从 2 条 `SELECT_OPTION` 变成 1 条 `SELECT_OPTIONS`（同一次触发里"点齐座位 0 / 1"），同一帧里第二次【连营】的"重新发问"证据保留（第二次是一次「放弃」） |

`goals.py` **一字未改、一条未放宽**（`_m6_hero_zone` 仍断言 HERO 区域的放置 / 归还与
【谦逊】的发动）；其余 59 条基准逐字节不变，种子也未重选（对局长度不变）。`58` 属于 M6
构造基准，不在 `STANDARD_108_REPLAY_BYTES` 的 47 条冻结表里，故那张表无需改动。
`replays/m6.py` 的 `Step.build` 是这一档的录制入口：不枚举档的答案由计划**组装**、引擎按
同一份形状谓词校验（与界面 / AI 走同一条路）。

## 重录登记：摸牌对 MOVE_AFTER 可见（sp5-pangtong-liuqi 报障修复）

**行为有意改变**（用户 2026-11-13 报障并签重录）：摸牌阶段的摸牌与反贼奖惩摸牌改走**移动
入口**（`move_in_frame`，`reason = DRAW`），不再用 `move_card` 旁路 —— 否则「获得牌 /
摸牌后」类技能（【清俭】【展骥】）在这些路径上**静默不触发**（展骥的"任何非展骥摸牌都
触发"因此才成立）。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `35` / `36` / `38` / `39` / `40` / `41` / `43` / `50` / `58` | 同种子重录：**只多出 `MOVE_AFTER` 时机记录**（35 +8、36 +11、38 +16、39 +36、40 +4、41 +7、43 +12、50 +3、58 +1 条），`query` / `action` / `delta` / `frame` 一条不变 | 每次摸牌现在也是一次"移动事件"（与弃置 / 交出同一条记录口径）；这些盘里都坐着带 `MOVE_AFTER` 钩子的武将（【连营】【枭姬】【清俭】…），惰性守卫因此落记录 |

`goals.py` **一字未改、一条未放宽**；其余 40 条标准 108 基准逐字节不变（没有 MOVE_AFTER
提供者的对局一条记录都不落）。冻结表（`tests/test_replays.py` 的
`STANDARD_108_REPLAY_BYTES`）与本文档同批更新。

## 重录登记：青釭剑封住【八卦阵】提供者（yijiang-2014-round2 票 25）

**行为有意改变**（用户 2026-11-17 批准重录）：青釭剑的「无视防具」按 OL 现行文本
「令其防具的**效果**无效直到此【杀】结算结束」实现——除三条伤害链外，【闪】响应窗口的
**候选面**也不再出现该目标防具的提供者（【八卦阵】/ 卧龙【八阵】的"视为使用【闪】"）。
标签（`Tag.ARMOR_IGNORED`）本身仍不进记录流；变的是它**推导出的 `legal_actions`**。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `13-indulgence-heart-misses` | 同种子重录（2,230,634 → 2,371,103 字节；2,238 → 2,495 条记录） | 座位 3 装青釭剑、座位 2 装八卦阵：旧录像的 DODGE 面上录着 `ActivateSkill(equip.eight_diagrams)`（**缺陷行为**），修好后该候选消失、随机驱动的行动序列随之偏移 |

`goals.py` **一字未改、一条未放宽**（回放后【乐不思蜀】未命中 ⇒ 正常进入出牌阶段的判据
仍命中）；其余 46 条标准 108 基准逐字节不变。冻结表（`tests/test_replays.py` 的
`STANDARD_108_REPLAY_BYTES`）与本文档同批更新。票 25 面写的「47 条逐字节不变」只对标签
本身成立——候选面在记录流里，故这一条必须重基线。

## 重录登记：header v3（`mode` / `pve`）（M7 票 07）

**不是行为改变，是 header 换了形状**（票 01 §2 授权的唯一一次 re-freeze）：`rule_config` 的
dump 从此含 `mode` 与 `pve` 两个键，故**全部 60 条**基准的首行都变，其余逐字节不变。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| 23 条无身份局 | 各涨 **32** 字节（首行多 `"mode":"no_identity",` 21 + `"pve":null,` 11） | `mode` 由派生属性改成字段、`pve` 一节随 `RuleConfig` 进 header；标准局 `pve` 恒 `null` |
| 37 条身份局 | 各涨 **29** 字节（`"mode":"identity",` 18 + 同 11） | 同上；`"identity"` 比 `"no_identity"` 短 3 字节 |

核过的三件事：**① 除首行外每一行都与改前逐字节相同**（逐文件 `git show HEAD:<file> |
tail -n +2` 的 sha256 与现文件同一段相等）；**②** `goals.py` 的判据一字未改、一条未放宽；
**③** 种子一个也没重选（首行不影响任何决策）。47 条标准 108 冻结表的 sha256 因此一次重算
（`tests/test_replays.py` 的 `STANDARD_108_REPLAY_BYTES`），这就是票 01 §2 说的**唯一一次**
re-freeze：票 09 / 10 往 `pve` 里填语义时，标准局仍是 `pve: null`，header 不再变。

体积：60 条合计 **50,791,313 字节 ≈ 48.44 MiB**（改前 50,789,504），仍在 52 MiB 总预算与
4 MiB 单文件上限之内，故**不动预算**。录制入口仍是 `pytest --update-golden`（一次跑完
60 条），没有新增基准、没有新增场景。

## 新增基准：6 条 PvE 与体积账（M7 票 12）

**纯新增，不碰既有 60 条**（票 01 §7 / spec §A6）：`61`–`66` 六条 PvE 构造基准覆盖票 01 §4
的四类逐场终局各一条 + 队友局（2v3）+ 保护队友阵亡（2v2 带 `protected_ally`）。
`git status --porcelain tests/replays/` 只报这 6 个新 `*.jsonl` + 生成器 `pve.py`；
47 条冻结表（`STANDARD_108_REPLAY_BYTES`）的 `(字节数, sha256)` **一条未改**。

**别在本文其余位置另起以 `` | `NN-…` | `` 开头的行**：`test_the_readme_declares_…` 会把
索引表之外的这类行也当索引行解析（重复行会互相覆盖）——本节的六条因此用列表、不用表格。

- `61-pve-rout-win`（种子 6101）：`rout`，1 友 × 2 敌 ⇒ `side=ally` / `reason=enemies_routed`。
- `62-pve-survive-rounds-win`（6102）：`survive_rounds`（N=1），1 友 × 2 敌 ⇒
  `side=ally` / `reason=rounds_survived`。
- `63-pve-rout-within-rounds-win`（6103）：`rout_within_rounds`（N=1），1 友 × 2 敌 ⇒
  `side=ally` / `reason=enemies_routed_in_time`。
- `64-pve-slay-boss-win`（6104）：`slay_boss`，1 友 × 1 Boss ⇒ `side=ally` / `reason=boss_slain`。
- `65-pve-ally-team-rout`（6105）：`rout`，2 友 × 3 敌（队友座位 3 真的行动）⇒
  `side=ally` / `winners=(0, 3)`。
- `66-pve-protected-ally-death`（6106）：`rout`，2 友 × 2 敌（`protected_ally=1`）⇒
  `side=enemy` / `reason=protected_ally_dead`。

四条读法（都在 `tests/test_replays.py` 有断言）：

1. **`rule_config` 必含 `pve`**：六条的 header 首行都带 `mode: "pve"` 与完整的 `pve` 一节
   （`identities` / `principal` / `protected_ally` / `victory` / `boss` / `seats`），
   座位表与重开出来的座位逐座相等（体力上限 / 阵营）。
2. **终局与纯函数一致**：每条都用终局存活集合与轮次重算 `solve_pve_outcome`，与
   `game_end` 的 `side` / `winners` / `reason` 逐字段相等；`game_end` 是记录流最后一条。
3. **种子避开登记带**：6101–6106 不在 `docs/agents/ai-versions.md` §3 的趋势带 / 实机带 /
   判决带里，也不在票 13 的 9000–9031 PvE 批测带里；构造局面不靠种子决策（种子只是 header
   里的记录，与 M5 / M6 同姿态）。
4. **票面「1v2 带 `protected_ally`」就地收窄成 2v2**：`protected_ally` 必须是第二个 ally
   座位（`PveConfig` 的构造期校验），3 座里放不下"1 友 + 保护对象"（与票 08 的 `## Answer` 同源）。

体积：66 条合计 **50,989,205 字节 ≈ 48.63 MiB**（原 60 条 50,791,313 + 新增 197,892 字节
≈ 193 KiB），最大单文件仍是 **3,334,072 字节 ≈ 3.18 MiB**（`10-identity-7p-lord-win.jsonl`）
——**优先吃余量**：仍在 52 MiB 总预算与 4 MiB 单文件上限之内，故**不动预算**（余量 ≈3.37 MiB）。
录制入口仍是 `pytest --update-golden`（`-k pve` 只跑这 6 条；产品 CLI 没有新参数）。

## 重录登记：扣减体力后 → 濒死（yijiang-2011-xusheng-chunhua 票 93）

**行为有意改变**（用户 2026-11-20 报障 + 签「允许重录 65 条金录像」）：伤害 / 失去体力把
体力扣到 0 或更低时，**「扣减体力后」（`LIFE_DECREASE_AFTER`）先结算、濒死帧再压上去**。
一手 = 《三國殺官方規則集 2018.3-16》的【伤逝】例："體力為2手牌數為1的張春華…在扣減3點體力後
能發動〖傷逝〗摸兩張牌，**然後進入瀕死狀態**"（`三国杀详细规则.txt` §4.15 的整理稿把插入点
写在"扣减体力时"下，与这条例子冲突；按 `readings.md` §4 以 2018 规则集为准）。

差异**只有两件事**（逐文件核过）：① `NEAR_DEATH` 帧的 push 与三条 `NEAR_DEATH_*` 记录整体
后移（`LIFE_DECREASE_AFTER` 排到它们前面）；② 受致命伤**死亡**的局里多出那一条
`LIFE_DECREASE_AFTER`（旧行为是整帧清栈、这次派发被丢掉）。**没有** delta / 决策 / 随机流
变化：`goals.py` 一字未改，六条 M1 基准的动作序列（去掉 `answers_seq` 后）逐条相同。

| 基准 | 变了什么 |
|---|---|
| 01–66 里字节真的变了的 **65 条**（`62-pve-survive-rounds-win` 不含濒死路径，逐字节不变） | 同种子重录（`pytest --update-golden`）：记录排布变化 + 受致命伤死亡局各多一条记录 |

`STANDARD_108_REPLAY_BYTES`（47 条）与 `M1_FROZEN_TRAJECTORIES`（6 条）同批重算——
后者摘要的是**带 `answers_seq` 的动作序列**，记录一换排布锚点就变，但决策序列未变。
`tools/check_goldens.sh` 与 `tests/test_replays.py` 同批 PASS。免费的重录入口仍是
`pytest tests/test_replays.py --update-golden`。
