# 代码构造：这条技能落成哪种形状（Shapes）

先跑 `.venv/bin/python tools/dump_catalog.py` 看库里已有的形状指纹，再用
`tools/similar_skills.py` 找最像的现成技能。**选形状 = 选样板**，不是选风格。

## 1. 五种原语（不许自创第六种）

| 原语 | 定义在哪 | 干什么 | 什么时候选它 |
|---|---|---|---|
| **钩子** | `src/sgs/skills/catalog.py:Hook` | 在某个**时机**被叫醒（可问"是否发动"） | 牌面写「当…时 / 当…后」 |
| **接缝** | `src/sgs/skills/catalog.py:SeamKind` | 时机枚举里**没有**的节点（杀被抵消、装备被弃） | 牌面写「…被【闪】抵消时」「当…被弃置时」 |
| **修饰器** | `src/sgs/effects/modifiers.py:Modifier` | 改**一个值**（距离 / 次数 / 张数 / 上限 / 目标池） | 牌面写「+1 / 无限制 / 不能成为目标 / 手牌上限等于…」 |
| **流水线步骤** | `src/sgs/skills/flow.py:SkillFlow` | 一次发动里的**多步交互** | 牌面写「你可以…，然后…」且要玩家选牌/选人/选一项 |
| **提供者** | `src/sgs/skills/lord_request.py:PROVIDERS` | "我需要一张 B"时让别人代出 | 牌面写「令其他…角色打出一张…」 |

四种"进入游戏"的方式（与上表正交，别混）：

| 方式 | 判据（看牌面怎么写） | 样板（`tools/similar_skills.py` 可反查） |
|---|---|---|
| **自动预发动** | 「当…时/后，你可以…」——**触发时自动问**一次 | 【贯石斧】`equip.axe`、【八卦阵】`equip.eight_diagrams`、界【铁骑】`hero.jie_machao.tieqi` |
| **点击即发动** | 「出牌阶段（限一次），你可以…」——**玩家点技能按钮** | 【制衡】`hero.sunquan.zhiheng`、界【结姻】`hero.jie_sunshangxiang.jieyin` |
| **转化声明** | 「你可以将一张 X 当 Y 使用或打出」 | 【武圣】`hero.guanyu.wusheng`、界【国色】`hero.jie_daqiao.guose` |
| **纯声明 / 状态位** | 只有被动约束或状态（空城、马术、谦逊放置） | 【空城】`hero.zhugeliang.kongcheng`、【谦逊】`hero.jie_luxun.qianxun` |

## 2. 形状表（选中**一行**，命不中 ⇒ 停 C1）

| 形状 | 典型牌面 | 样板技能（必须存在于 `CATALOG`） |
|---|---|---|
| 零步流水线（只问"是否发动"） | 「当…后，你可以摸 N 张」 | `hero.sunshangxiang.xiaoji` |
| 单步选牌 → 效果 | 「弃置任意张牌，然后摸等量的牌」 | `hero.sunquan.zhiheng` |
| 多步：座位 → 选牌 | 「交给一名其他角色任意张手牌」 | `hero.liubei.rende` |
| 多步：选牌 → 座位 | 「弃置一张牌，令一名…角色回复」 | `hero.huatuo.qingnang` |
| 多步：座位 → 选项 → 选牌 | 「选择一名男性角色，然后弃置一张手牌或将一张装备牌置入其装备区」 | `hero.jie_sunshangxiang.jieyin` |
| 选项步（闭集，可动态） | 「选择一项：1… 2…」 | `hero.jie_xushu.jianyan` |
| 手写询问（循环/逐点/逐目标） | 「逐点触发、每个伤害点各问一次」「逐个目标」 | `hero.guojia.yiji`、`hero.jie_machao.tieqi` |
| 钩子 + 修饰器（触发后改值） | 「当你失去 1 点体力后…本阶段…」 | `hero.jie_huanggai.zhaxiang` |
| 纯修饰器（无询问） | 「锁定技，你与其他角色的距离 -1」 | `hero.machao.mashu` |
| 目标池修饰器（剔除自己） | 「若你没有手牌，你不能成为【杀】或【决斗】的目标」 | `hero.zhugeliang.kongcheng` |
| 接缝（时机枚举里没有） | 「当你的【杀】被【闪】抵消后」 | `equip.axe` |
| 提供者（势力求助） | 「令其他蜀势力角色打出一张【杀】」 | `hero.liubei.jijiang` |
| 判定 / 改判 | 「判定牌生效前，你可以打出一张牌代替之」 | `hero.simayi.guicai` |
| 放置区（牌扣在武将牌上） | 「将手牌置于你的武将牌上」 | `hero.jie_luxun.qianxun` |
| 觉醒 / 限定（一次性转变） | 「觉醒技 / 限定技，…」 | `hero.jie_xushu.qianxue`、`hero.jie_zhangfei.tishen` |
| 阶段体（阶段里改摸牌/弃牌） | 「摸牌阶段，你可以多摸一张牌」 | `hero.zhouyu.yingzi` |
| 状态位 + 触发器 | 「横置 / 翻面 / 标记」 | 横置 = `Player.chained` + `Engine.set_chained`（无武将载体，铁索连环卡的机制） |

> 表里没有的形状，就是 C1：**停手开架构缺口票**，不许在技能代码里绕。

## 3. 封闭集登记表（审计测试核这张表）

新增成员时**必须**在这里加一行归属；漏登记 = `tests/test_skill_authoring_docs.py` 红。

### 3.1 钩子时机（只列被武将技能用到的；完整枚举跑 `dump_catalog.py --timings`）

| 时机 | 常见用途 |
|---|---|
| `PHASE_PREPARE` | 准备阶段（洛神、勤学、替身、观星） |
| `PHASE_DRAW` | 摸牌阶段（英姿、突袭、裸衣、恂恂） |
| `PHASE_DISCARD_START` | 弃牌阶段开始（克己） |
| `PHASE_FINISH` | 结束阶段（闭月、观星二段、诛害、勤学二段） |
| `TURN_END` | 回合结束（博图） |
| `DAMAGE_AFTER_TAKE` | 受到伤害后（奸雄、反馈、遗计、刚烈、忘隙、耀武）——**注意 `per_point`** |
| `DAMAGE_AFTER_DEAL` | 造成伤害后（忘隙、潜心） |
| `LIFE_DECREASE_AFTER` | 失去体力后（诈降） |
| `MOVE_AFTER` | 失去牌后（连营、枭姬、清俭） |
| `USE_ON_USE` | 使用牌时（集智） |
| `USE_AFTER_DESIGNATE_TARGET` | 使用并指定目标后（铁骑、奋威、无双类） |
| `USE_BECOME_TARGET` | 成为目标时（流离） |
| `PLAY_ON_PLAY` | 打出牌时（界护驾/界激将 ②） |
| `RESOLVE_EFFECT` | 生效时（界谦逊） |
| `JUDGE_CARD_AFTER_EFFECT` | 判定牌生效后（天妒） |
| `RECOVER_VALUE` | 回复数值确定时（界救援） |

### 3.2 接缝 `SeamKind`

| 接缝 | 归属 |
|---|---|
| `SLASH_CANCELED` | 【青龙偃月刀】续杀、【贯石斧】弃两张保伤害 |
| `EQUIPMENT_DISCARD` | 界【奇才】② 取消其他角色弃置自己防具/宝物（事件帧无效） |

### 3.3 修饰点 `ModifierPoint`

| 修饰点 | 归属 |
|---|---|
| `CANDIDATES` | 补候选（转化技 / 护驾 / 激将） |
| `TARGET_POOL` | 剔除或补目标（空城、谦逊、界武圣②、诈降） |
| `TARGET_COUNT` | 额定目标数（方天画戟类） |
| `DISTANCE` | 座位距离（马术、义从、坐骑） |
| `DISTANCE_LIMIT` | 额定距离上限（奇才） |
| `COUNT_LIMIT` | 使用次数上限（咆哮、诈降、酒） |
| `HAND_LIMIT_REPLACE` | 手牌上限替换式（界英姿②） |
| `EFFECTIVENESS` | 效果生效性（仁王盾 / 青釭剑一类） |
| `RESPONSE_COUNT` | 本次响应的张数（无双） |
| `RECOVER_AMOUNT` | 回复量加式（标准救援） |
| `DAMAGE_DEAL_AMOUNT` | 造成伤害量（裸衣、义绝、咆哮②） |
| `DAMAGE_TAKE_AMOUNT` | 受到伤害量（藤甲） |
| `DAMAGE_CAP` | 伤害上限（白银狮子一类） |

### 3.4 询问种类 `QueryKind`

| 询问 | 谁发起 | 备注 |
|---|---|---|
| `PLAY_PHASE` | 出牌阶段 | 唯一带预发动清单的询问 |
| `DODGE` | 【杀】的【闪】窗口 | 提供者（八卦阵/护驾）在这里 |
| `PEACH` | 濒死求桃 | |
| `DISCARD` | 弃牌阶段 | 逐张点选 |
| `RESPOND` | 通用响应窗口（打出/使用一张牌） | 决斗的【杀】、万箭的【闪】 |
| `SELECT_CARDS` | 流水线选牌步 | 自己区域的直选在 `candidates.js` 判 |
| `SKILL_ACTIVATION` | 钩子的"是否发动" | 键含时机；同帧第二次触发会复用答案（已裁定） |
| `SELECT_OPTION` | 选项步 / 手写二选一 | 单个选项 + `Pass` 走"提交/放弃"确认形态 |
| `ARRANGE` | 观星式排列 | 不能下发预发动清单 |

### 3.5 区域 `Zone`

| 区域 | 读法要点 |
|---|---|
| `DECK` | 牌堆（顶 / 底） |
| `HAND` | 手牌（对别人不可见） |
| `EQUIP` | 装备区（公开） |
| `JUDGE` | 判定区（公开，但"牌不为主人拥有"） |
| `DISCARD` | 弃牌堆 |
| `PROCESSING` | 处理区（使用中的牌在这里） |
| `HERO` | 武将牌上（本仓按真区域建模，见账本） |

### 3.6 帧型 `FrameKind`

| 帧 | 用途 |
|---|---|
| `SKILL` | 技能发动主体 |
| `USE` | 一次使用（六要素） |
| `RESOLVE` | 一次结算（逐目标） |
| `DAMAGE` | 伤害链（逐点派发） |
| `LIFE_DECREASE` | 失去体力（无来源） |
| `RECOVER` | 回复 |
| `JUDGE` | 判定 |
| `MOVE` | 搬牌流程（五阶段） |
| `PLAY` | 打出帧（手牌 → 处理区 → 弃牌堆） |
| `NEAR_DEATH` | 濒死 |
| `DEATH` | 死亡结算 |
| `LIFE_LOSE` | 失去体力上限 |
| `MAXLIFE` | 体力上限变化 |
| `RANK_COMPARE` | 拼点类的比较帧 |

### 3.7 步骤类型

| 步骤 | 短名 | 用途 |
|---|---|---|
| `PickCards` | `cards` | 选牌（`lo`/`hi`/`zones`/`face`/`card_types`/`slot_kinds`/`exclude_cards`） |
| `PickSeats` | `seats` | 选角色（`pool` 可依赖前序答案） |
| `PickOption` | `option` | 闭集选项（`options` 可以是**可调用档**，按前序答案现算） |
| `PickProvide` | `provide` | 选一张用于"提供"的牌（转化 / 提供者） |
| `Arrange` | `arrange` | 排列（观星）；含它的流水线不下发预发动清单 |

## 4. UI 触点（每项必须落到一个已存在的测试垫片）

| 触点 | 位置 | 判据 |
|---|---|---|
| 自己的牌直选（不铺左下角面板） | `candidates.js:isOwnZonePick` | 只看载荷形状，不认技能名 |
| 选项按钮 / 单选项确认（提交 / 放弃） | `options.js` + `pending.js:isSingleOptionalOption` | 候选里恰一个 `SelectOption` + `Pass` |
| 选项中文标签 | `options.js:OPTION_LABELS` | 只在**多选项**形态读得到；单选项确认读不到 |
| 逐步文案 | `SkillDef.step_requirements` | SELECT_OPTION 读 `skill_id` 冒号后那段；SELECT_CARDS 恒读 `card` |
| 一句话文案 | `SkillDef.requirement` | 提示条与预发动清单 |
| 预发动清单 | `preactivation.js` | 由流水线步骤渲染；`Arrange` 不下发 |
| 舞台 / 动效 | `session.js` / `effects.js` | 见 `docs/agents/frontend-map.md` |
| 牌面标签 | `card-view.js:cardTagsNode` | 标签类型必须在 `CARD_TAG_TYPES` 登记 |
| 武将详情 / 被封锁技能图标 | `hero-detail.js` / `table.js` | |

新增 UI 形态（垫片里没有的）= **C3**：停手，另开 UI 票并走真实浏览器验收。

## 5. 新机制怎么进框架（三档判定）

先查它现在到哪一档，**别假设**：

| 档 | 现象 | 例子（本轮实测） |
|---|---|---|
| 无 | 全树没有对应字段 / 帧 / 时机 | **翻面**（`turn_over` / `flipped` 全无） |
| 只有枚举位 | `Timing` 里有名字，但没人派发、没有流程 | **拼点**（L15 只留枚举位，`RANK_COMPARE` 帧型空转） |
| 已解封 | 有帧 / 钩子 / 步骤，能被技能用 | **横置**（`Player.chained` + `set_chained` + delta） |

判定完：

- 已解封 ⇒ 按第 2 节选形状，正常做；
- 只有枚举位 / 无 ⇒ **C1 停**，产出架构缺口票（缺哪个原语、为什么现有五种组不出来、还要动哪些共享文件）。

## 6. 更新触发（谁改这张表）

新时机 / 新接缝 / 新修饰点 / 新询问 / 新帧型 / 新区域 / 新步骤类型，或某个机制从"枚举位"被
解封——**落地那张票**顺手改对应小节；`tests/test_skill_authoring_docs.py` 会反向核（漏登记即红）。
