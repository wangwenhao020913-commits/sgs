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
| **提供者** | `src/sgs/skills/lord_request.py:PROVIDERS` | "我需要一张 B"时让别人（或自己）先付一张再决定算不算数 | 牌面写「令其他…角色打出一张…」，或「需要牌时你可以扣置一张手牌当它」 |

提供者的**接线面**是一组固定的"需要牌"窗口（引擎里各有一条 `ActivateSkill` 分支，六扇：
【闪】、南蛮 / 万箭 / 决斗（打出）、借刀（使用）、濒死求【桃】、无懈的收集式窗口）。
`start_provider(..., required=…)` 由**窗口**声明"这扇窗口要一张什么牌"，提供者据此收窄
声明范围（**不**从外层帧的牌反推：濒死帧没有牌、无懈帧上是那张锦囊、借刀帧上是借刀本身）。
产物**交给窗口**由它消费（使用 / 打出 / `recover_from_peach` / 对 Bn 使用 / 先声明赢家才
使用）。只有"别人已经付过一张"的主公技式提供者不交产物：`satisfied` 为真而没有产物时，
【闪】/ 南蛮 / 万箭 / 决斗记一次成功即可，而求桃 / 借刀 / 无懈的"需要"**就是窗口要交付的
那张牌**，没有产物按"这次响应没发生"处理（求桃记放弃、借刀落回真牌窗口、无懈不记声明）。

四种"进入游戏"的方式（与上表正交，别混）：

| 方式 | 判据（看牌面怎么写） | 样板（`tools/similar_skills.py` 可反查） |
|---|---|---|
| **自动预发动** | 「当…时/后，你可以…」——**触发时自动问**一次 | 【贯石斧】`equip.axe`、【八卦阵】`equip.eight_diagrams`、界【铁骑】`hero.jie_machao.tieqi` |
| **点击即发动** | 「出牌阶段（限一次），你可以…」——**玩家点技能按钮** | 【制衡】`hero.sunquan.zhiheng`、界【结姻】`hero.jie_sunshangxiang.jieyin` |
| **转化声明** | 「你可以将一张 X 当 Y 使用或打出」 | 【武圣】`hero.guanyu.wusheng`、界【国色】`hero.jie_daqiao.guose` |
| **纯声明 / 状态位** | 只有被动约束或状态（空城、马术、谦逊放置） | 【空城】`hero.zhugeliang.kongcheng`、【谦逊】`hero.jie_luxun.qianxun` |

> **「只能」型转化另立一档**：牌面写「**只能**将一张 X 当 Y 使用或打出」（当前只有神关羽
> 【武神】）时，在 `ConversionSkill` 上声明 `forced=True`。它与 `locked` **正交**：锁定技是
> 另一条规则类型（失效 / 形态表），`forced` 只回答"源牌还能不能按原用途用"——`forced=True`
> 的源牌由界面默认画成产物；「可以」型（`forced=False`，武圣 / 龙胆 / 国色 / 急救 / 丈八）
> 保留原牌，转化只经预发动。别用 `locked` 推 `forced`。

## 2. 形状表（选中**一行**，命不中 ⇒ 停 C1）

| 形状 | 典型牌面 | 样板技能（必须存在于 `CATALOG`） |
|---|---|---|
| 零步流水线（只问"是否发动"） | 「当…后，你可以摸 N 张」 | `hero.sunshangxiang.xiaoji` |
| 单步选牌 → 效果 | 「弃置任意张牌，然后摸等量的牌」 | `hero.sunquan.zhiheng` |
| 多步：座位 → 选牌 | 「交给一名其他角色任意张手牌」 | `hero.liubei.rende` |
| 多步：选牌 → 座位 | 「弃置一张牌，令一名…角色回复」 | `hero.huatuo.qingnang` |
| 多步：座位 → 选项 → 选牌 | 「选择一名男性角色，然后弃置一张手牌或将一张装备牌置入其装备区」 | `hero.jie_sunshangxiang.jieyin` |
| 选项步（闭集，可动态） | 「选择一项：1… 2…」 | `hero.jie_xushu.jianyan` |
| 选项步 + **置灰**（`disabled`：列出来但此刻不可选） | 「将此牌当任意一张基本牌或普通锦囊牌」——牌名**列全**，"此刻没有合法目标"的置灰 | `hero.yuji_mobile.guohuo`（移动版于吉【蛊惑】；`disabled` 见 `src/sgs/skills/flow.py:PickOption`） |
| 手写询问（循环/逐点/逐目标） | 「逐点触发、每个伤害点各问一次」「逐个目标」 | `hero.guojia.yiji`、`hero.jie_machao.tieqi` |
| 手写**主体**（`SkillFlow` 只装前几步，段机住在 `effect` 里） | 「扣置一张手牌…然后其他角色**依次**质疑」——一次发动里有**变长循环**（逐座位问）；或「失去 1 点体力**或**弃一张武器牌，然后…」——两条代价分支的**后续步骤不同**（一条要选牌、一条不要） | `hero.yuji_mobile.guohuo`（前两步是流水线，质疑循环住在提交点；判据见 `src/sgs/skills/flow.py:SkillFlow` 的"本语言没有循环 / 每轮提交步骤"）；**条件步**同形：`hero.dianwei_mobile.qiangxi`（选项步留在流水线，选牌 / 选目标 / 付费住在 `effect`——`steps` 是定长元组，任一步"没有候选"= 取消整条，没有"跳过这一步"） |
| 钩子 + 修饰器（触发后改值） | 「当你失去 1 点体力后…本阶段…」 | `hero.jie_huanggai.zhaxiang` |
| 纯修饰器（无询问） | 「锁定技，你与其他角色的距离 -1」 | `hero.machao.mashu` |
| 目标池修饰器（剔除自己） | 「若你没有手牌，你不能成为【杀】或【决斗】的目标」 | `hero.zhugeliang.kongcheng` |
| 转化声明（单源 / **多源**） | 「你可以将一张 X 当 Y 使用或打出」/「将**两张**花色相同的手牌当【万箭齐发】使用」 | `hero.pangtong_mobile.lianhuan`（单源 + `CANDIDATES` 追加 `Reforge` 的重铸分支）、`hero.yuanshao_mobile.luanji`（多源：两张同花色 → 虚拟【万箭齐发】；`virtual_source_count=2`） |
| 接缝（时机枚举里没有） | 「当你的【杀】被【闪】抵消后」 | `equip.axe` |
| 提供者（势力求助） | 「令其他蜀势力角色打出一张【杀】」 | `hero.liubei.jijiang`；**提供者不只服务主公技**：移动版于吉【蛊惑】用它实现"需要牌时先质疑再决定这张牌算不算数" |
| 判定 / 改判 | 「判定牌生效前，你可以**用**一张牌代替 / 替换之」（**不是打出**，走 `REJUDGE` 事件帧） | `hero.simayi.guicai`；张角【鬼道】= 同形 + `rejudge_card_color=BLACK` + `rejudge_original_to=HAND`（**替换**：原判定牌由改判者获得） |
| 主公技·**跨座位注入**（别座位的出牌阶段里出现的主动技） | 「主公技，其他 X 势力角色的出牌阶段限一次，该角色可以交给你一张…」 | `hero.zhangjiao_mobile.huangtian`（张角移动版【黄天】）：`CANDIDATES` 注入 + A 族主动技 + 按**发动者**记的阶段位段（`catalog._HUANGTIAN_MASK`）；投影 `build_skills` 只把"这一问是问我的"那些列进我的技能区 |
| 放置区（牌扣在武将牌上） | 「将手牌置于你的武将牌上」 | `hero.jie_luxun.qianxun` |
| 觉醒 / 限定（一次性转变） | 「觉醒技 / 限定技，…」 | `hero.jie_xushu.qianxue`、`hero.jie_zhangfei.tishen`；**A 族（主动技）的限定技** = `ActiveSkill(skill_type=LIMITED)`，样板 `hero.tao_liubei.yiqi` |
| 阶段体（阶段里改摸牌/弃牌） | 「摸牌阶段，你可以多摸一张牌」 | `hero.zhouyu.yingzi` |
| 阶段体 + 亮出牌堆顶、按花色取 0~k（一行式） | 「摸牌阶段，你可以改为亮出牌堆顶的五张牌，然后获得其中每种花色的牌各一张」 | `hero.shen_lvmeng_mobile.shelie` |
| **帧外时机**钩子 + 单步选项（回合开始时 / 回合结束时） | 「回合开始时，你可以选择一至两项…」「回合结束时…执行一个额外的回合」 | `hero.xiahouyuan.shensu`（`TURN_START`）、`hero.jie_lvmeng.botu`（`TURN_END`） |
| **计数式分配**（新询问形态） | 「对这些角色造成共计至多3点伤害」——一张弹窗里逐座位点计数 | `hero.shen_zhouyu_mobile.yeyan`（huo-shenzhouyu-mobile 票 05；`QueryKind.DISTRIBUTE` + `Action.Distribute`） |
| 帧外时机 + 单步选项（**弃牌阶段结束时**） | 「弃牌阶段结束时，若你于此阶段内弃置过…你可以选择一项」 | `hero.shen_zhouyu_mobile.qinyin`（huo-shenzhouyu-mobile 票 01/03） |
| **L0（游戏开始时）锁定技 + 手写展示循环**：帧外钩子（不问发动）+ 变长循环逐张弹选择型弹窗，每次的正文段是**武将牌**（武将块 / 技能行带描述 / 置灰 / 具体 X） | 「游戏开始时，依次展示3张真名不在场上的随机武将牌，且每次展示你须选择：1.获得该武将牌上的1个技能；2.增加一点体力上限，回复一点体力，摸X张牌」 | `hero.wuming.tiancheng`（wuming 票 01；`GAME_START` + `Engine.draw_hero_cards` + `ask_option(disabled=/option_texts=/hero_card=)`；循环游标住**帧私有**，加 / 减上限走 `MAXLIFE` 帧） |
| 状态位 + 触发器 | 「横置 / 翻面 / 标记」 | 横置 = `Player.chained` + `Engine.set_chained`；翻面 = `Player.flipped` + `Engine.set_flipped`（首个载体：移动版界曹仁【据守】）。**监听**「翻面 / 横置**后**」用 `STATE_FLIPPED` / `STATE_CHAINED` 上的钩子（chenqun-guyong 票 03 解封；见 §3.1 那一行的惰性派发口径），效果本身通常是第 1 行「零步流水线」 |

> **第 1 行的锁定技变体**：牌面写「锁定技」时形状不变、但那一问**不发**（`locked=True`，派发环的
> forced 分支直接跑回调），故效果与"零步"同形、不必造 `SkillFlow`（机械形态落「锁定技」格）。
>
> **逐条钩子的「不问」**（huo-shenzhugeliang 票 04）：一条非锁定技里可以既有可选段（「你可以…」）
> 又有强制段（「当…时，此伤害 +1」/「防止之」/ 标记的到期清扫）。后者在 `Hook` 上声明
> `forced=True`：派发环**不问**"是否发动"、直接跑，但它**不**改 `locked`（因此仍随实例失效而
> 停摆——这正是神诸葛亮【狂风】【大雾】要的语义："失效了就不增伤 / 不防止"）。
> 样板 = 界华雄【耀武】`hero.jie_huaxiong.yaowu`（造伤后自动效果）；风包魏延【狂骨】
> `hero.weiyan.kuanggu` 是它的"两条钩子"版：一条在 `DAMAGE_TAKE` 定格距离、一条在
> `DAMAGE_AFTER_DEAL` 逐点回复。

> 表里没有的形状，就是 C1：**停手开架构缺口票**，不许在技能代码里绕。

## 3. 封闭集登记表（审计测试核这张表）

新增成员时**必须**在这里加一行归属；漏登记 = `tests/test_skill_authoring_docs.py` 红。

### 3.1 钩子时机（只列被武将技能用到的；完整枚举跑 `dump_catalog.py --timings`）

| 时机 | 常见用途 |
|---|---|
| `GAME_START` | **游戏开始时**（L0.3：起始手牌发完后、第一回合前，规则 §3.9 L229–L233）：帧外派发点 = `_dispatch_phase_body` 的又一个调用者（`_start`；无钩子时只落原有那一条记录，记录流逐字节不变）。L0 帧 `source=None` / 记录 `actor=None`——不把"游戏开始时"伪装成第一个座位的回合事件，钩子的"关于我"自校读 `MATCH_OWNER_KEY`（`hook_owner`）。`_start` 可停可续：钩子提问挂起时 `_started` 留假，复入原地续跑（票 03）。**首个载体 = 【天成】**（`hero.wuming.tiancheng`，wuming 票 01）：它还会在派发里**压子帧**（`MAXLIFE` / `RECOVER`），故 `_step` 让**结算栈先出空、再重入 `_start`**——`return "stay"` 的语义因此与回合环步骤一致（帧跑完才续跑） |
| `TURN_START` | **回合开始时**（夏侯渊【神速】）：与 `PHASE_PREPARE` **不是一个时点**（它在准备阶段之前）。帧外派发点 = `_dispatch_phase_body` 的第五个消费者（`_turn_start`），每回合清理带一次性闸（钩子挂起重入不重复清理） |
| `PHASE_PREPARE` | 准备阶段（洛神、勤学、替身、观星） |
| `PHASE_DRAW` | 摸牌阶段（英姿、突袭、裸衣、恂恂） |
| `PHASE_DISCARD_START` | 弃牌阶段开始（克己） |
| `PHASE_DISCARD_END` | **弃牌阶段结束时**（神周瑜移动版【琴音】= 首个载体，huo-shenzhouyu-mobile 票 03）：与 `PHASE_FINISH` 同一条惰性派发（`_dispatch_phase_body`）；帧私有 `discard_phase_hand_discards` = 本阶段弃牌步实际弃置的手牌数，钩子条件读它 |
| `PHASE_DRAW_END` | **摸牌阶段结束时**（神诸葛亮【七星】② = 首个载体，huo-shenzhugeliang 票 03）：`_phase_draw_end` 改走 `_dispatch_phase_body`（惰性：没有注册实例时只落一条时机记录，记录流不变）。被【兵粮寸断】跳过的摸牌阶段**不经过本步** ⇒ 这个时点整段不存在、挂在上面的技能不触发 |
| `PHASE_FINISH` | 结束阶段（闭月、观星二段、诛害、勤学二段） |
| `PHASE_PLAY_END` | **出牌阶段结束时**（韬·曹操【倡义】= 首个载体，tao-caocao 票 02）：`_phase_play_end` 改走 `_dispatch_phase_body`（惰性：没有注册实例时只落一条时机记录，记录流不变）。**"本阶段"作用域的清理排在本步之后**；被【乐不思蜀】跳过的出牌阶段**不经过本步** ⇒ 这个时点整段不存在、挂在上面的技能不触发（与【兵粮寸断】跳摸牌阶段的 `PHASE_DRAW_END` 同一口径） |
| `TURN_END` | 回合结束（博图） |
| `DAMAGE_TAKE` | 受到伤害时（**风包【狂骨】的距离定格**：造伤后的效果要按「扣减体力前」的场况判，而濒死 / 死亡结算夹在两者之间——死者的装备技 / 义从类距离修饰器已不再解析，现算会低估 1。它是扣减体力前**最后一个派发点**） |
| `DAMAGE_AFTER_TAKE` | 受到伤害后（奸雄、反馈、遗计、刚烈、忘隙、耀武）——**注意 `per_point`** |
| `DAMAGE_AFTER_DEAL` | 造成伤害后（忘隙、潜心、狂骨）——**注意 `per_point`** |
| `LIFE_DECREASE_AFTER` | 失去体力后（诈降） |
| `MOVE_AFTER` | 失去牌后（连营、枭姬、清俭） |
| `USE_ON_USE` | 使用牌时（集智） |
| `USE_AFTER_DESIGNATE_TARGET` | 使用并指定目标后（铁骑、奋威、无双类） |
| `USE_BECOME_TARGET` | 成为目标时（流离） |
| `USE_AFTER_BECOME_TARGET` | **成为目标后**（B6；目标已定、不会再被转移）：诸葛瞻【父荫】是首个载体（「此牌对你无效」= 目标级无效，逐目标派发、`MATCH_ACTOR_KEY` = 这一次的目标） |
| `ROUND_START` | **每轮开始时**（自制包·锋·于禁【定乱】= 首个载体，feng-yujin 票 02）：轮边界（本轮第一个自然回合的「回合开始前」之前），帧外派发点 `Engine._dispatch_round_start`，座位环从**本轮起始座位**起；**惰性**——没有载体时一条记录都不落（标准 47 条金录像逐字节不变）。额外回合不推进轮次 ⇒ 不触发。 |
| `PLAY_ON_PLAY` | 打出牌时（界护驾/界激将 ②） |
| `RESOLVE_EFFECT` | 生效时（界谦逊） |
| `RESOLVE_AFTER_END` | **使用结算结束后**（界黄忠移动版【烈弓】② 的「本次使用 + 该目标 +1」记账清扫 = 首个载体，jie-feng-batch 票 01）：USE 帧的**最后一步** `use_after_end` 派发一次（多目标也只有一个 USE 帧，故它是"本次使用结束"的准确落点）；与逐目标的 `RESOLVE_END` / `RESOLVE_AFTER_END`（RESOLVE 帧里逐目标）不是同一个时点 |
| `JUDGE_CARD_AFTER_EFFECT` | 判定牌生效后（天妒） |
| `RANK_COMPARE_ON` | **进行拼点时**（huo-taishici-xunyu 票 03 解封）：`RANK_COMPARE` 帧的第一步派发；拼点先由技能发起（`Engine.rank_compare`），故「谁会拼点」不落枚举表 |
| `RANK_COMPARE_PLACED` | **拼点牌扣置入处理区后、亮出前**（韬·曹操【倡义】的「点数 +X」= 首个载体，tao-caocao 票 03；规则 12.3 L1337–L1340 的"拼点期间可以发动技能"窗口）：`RANK_COMPARE` 帧在 `place` 与 `reveal` 之间派发。此时机上的技能可经 `Engine.boost_pindian` 加**生效点数**（`rank_compare_reveal` 按牌面 + 增量算、再按规则 12.2 钳到 1..13），`pindian` delta 的 `ranks` 是该事实的公开面 |
| `RANK_COMPARE_RESULT_AFTER` | **拼点结果后**（赢 / 没赢；票 03）：结果写回发起帧之后、拼点牌置入弃牌堆之前派发；平点 = 双方都没赢（**平点事实经 `pindian_tied` 回写发起帧**，tao-caocao 票 03——`pindian_won` 只有两态） |
| `RECOVER_VALUE` | 回复数值确定时（界救援） |
| `DEATH_ON` | **死亡时**技能（神关羽移动版【武魂】②：死者本人的判定 / 直接致死）——`10` 起死者在这个窗口内仍可解析，`pass_away` 收口后静默 |
| `NEAR_DEATH_ENTER` | **濒死时**技能（庞统移动版【涅槃】：huo-mobile-batch 票 02 的首载体）——NEAR_DEATH 帧的**第一步**，在求桃窗口（`NEAR_DEATH_AT`）**之前**派发；帧外事件（任何人的回合都可能发生），效果在这里结算完 ⇒ 体力回到 1 以上时求桃窗口直接早退 |
| `MAXLIFE_INCREASE_BEFORE` / `MAXLIFE_INCREASE_AFTER` | **加体力上限**的前 / 后（L11；规则 §4.15 L429–L439：加 / 减是两个**独立流程**，各只有「前 / 后」，没有「增减体力上限时」）。今天是**框架解封**（wuming 票 04）：唯一写入口 `Engine.raise_hp_max(seat, amount=1)` 推 `FrameKind.MAXLIFE` 帧，**首个载体 = 票 01 的【天成】**（`src/sgs/skills/start.py`）。加上限**不同步体力**——"增加上限并回复一点"的回复是技能的第二段，走 `RECOVER` 帧。**减**侧 `MAXLIFE_DECREASE_*` 仍留枚举位（`reduce_hp_max` 未迁进帧，理由见 `06`（§5「程序化约定与偏离一手」）的时机四轴计数段） |
| `STATE_FLIPPED` / `STATE_CHAINED` | **翻面后 / 横置后**（chenqun-guyong 票 03 / W2 解封；首个载体 = 陈群【法恩】，承接票 08）。派发点是两个状态位**写入口**（`Engine.set_flipped` / `Engine.set_chained`），**惰性且延后**：写入口只把事件排进 `Engine._state_pending`，由**下一条** `_step` 的开头用一条**不压栈**的临时帧派发（与帧外阶段体同形）⇒ 9 个既有调用点一字不改、钩子照样能提问 / 续跑。口径（用户 2026-11-09 签 Q-W2 = **读法一**）：**值真的变了**才是一次事件——幂等写（同值、不落 delta）不算；「回合开始前翻回正面」的规则固定流程**算**；一趟【铁索连环】横置 2 名角色 = **2 个事件**（一个座位一次）。载荷 = 写入口那条 `flipped_change` / `chained_change` delta（`seat` + **新值**），钩子读 `sgs.skills.state_change(frame)` 而不是现算当前状态。没有注册实例时**一条记录都不落**（标准 47 条金录像逐字节不变） |

### 3.2 接缝 `SeamKind`

| 接缝 | 归属 |
|---|---|
| `SLASH_CANCELED` | 【青龙偃月刀】续杀、【贯石斧】弃两张保伤害 |
| `EQUIPMENT_DISCARD` | 界【奇才】② 取消其他角色弃置自己防具/宝物（事件帧无效） |

### 3.3 修饰点 `ModifierPoint`

| 修饰点 | 归属 |
|---|---|
| `CANDIDATES` | 补候选（转化技 / 护驾 / 激将 / 蛊惑）。语境由 `CandidateScope` 区分：`PLAY`（出牌阶段）、`DODGE`（【闪】）、`RESPOND`（打出 / 使用的响应窗口）、`PEACH`（濒死求【桃】）、`NULLIFY`（无懈的收集式窗口）、`CARD`（逐张牌的常规候选）。"这扇窗口要一张什么牌"随 `ModifierContext.respond_required` 下传（六扇窗口都填；`DODGE` 恒为 `dodge`） |
| `TARGET_POOL` | 剔除或补目标（空城、谦逊、界武圣②、诈降）。**补目标能放宽"自用牌"**（给【无中生有】/【桃】一类 `TargetRule.self_only` 的牌加别人，是"可对别人使用自用牌"类技能的模板）——但补目标前先分清自己豁免的是**距离**还是**目标合法性**：雄乱（`hero.zhangxiu.xiongluan`）只豁免距离与次数，故回调按 `TargetRule.self_only` 收窄（lei-batch 票 04 的缺陷 B：不加这道守卫，雄乱生效时【无中生有】/【桃】就能指定雄乱目标） |
| `TARGET_COUNT` | 额定目标数（方天画戟类） |
| `DISTANCE` | 座位距离（马术、义从、坐骑） |
| `DISTANCE_LIMIT` | 额定距离上限（奇才） |
| `COUNT_LIMIT` | 使用次数上限（咆哮、诈降、酒） |
| `COUNT_EXEMPT` | **按目标集合**豁免次数上限（雄乱「你对其使用牌无次数限制」）：`current` 从 `False` 起，回调把"这一次使用不受上限约束"写成 `True`；**只在 `check_count(..., targets=...)` 时派发**（`ModifierContext.use_targets` = 这一次使用已定下的目标集合），候选生成器的第一次询问（"整张牌还准不准用"）不派发它——超额时由 `_play_phase` 把目标池收窄到被豁免的角色 |
| `HAND_LIMIT_REPLACE` | 手牌上限的**查询期修正链**：替换式（界英姿②「手牌上限 = 体力上限」）+ **持续加式**（袁绍移动版【血裔】「上限 + 2×其他存活群势力角色数」，huo-mobile-batch 票 03 / 08 读法 A）——加式在 `current` 上加；**一次性**加式（`Player.hand_limit_bonus`）不走本挂点 |
| `EFFECTIVENESS` | 效果生效性（仁王盾 / 青釭剑一类） |
| `RESPONSE_COUNT` | 本次响应的张数（无双） |
| `RECOVER_AMOUNT` | 回复量加式（标准救援） |
| `DAMAGE_DEAL_AMOUNT` | 造成伤害量（裸衣、义绝、咆哮②） |
| `DAMAGE_TAKE_AMOUNT` | 受到伤害量（藤甲） |
| `DAMAGE_CAP` | 伤害上限（白银狮子一类） |
| `FORBIDDEN` | 这张牌不能按**原用途**使用 / 打出（神关羽移动版【武神】①的红桃手牌；`05`，`check_forbidden` 走链） |

### 3.4 询问种类 `QueryKind`

| 询问 | 谁发起 | 备注 |
|---|---|---|
| `PLAY_PHASE` | 出牌阶段 | 唯一带预发动清单的询问 |
| `DODGE` | 【杀】的【闪】窗口 | 提供者（八卦阵/护驾）在这里 |
| `PEACH` | 濒死求桃 | 提供者（蛊惑）也在这里：产物由窗口压 `recover_from_peach`（回复结算插在濒死之下） |
| `DISCARD` | 弃牌阶段 | 逐张点选 |
| `RESPOND` | 通用响应窗口（打出/使用一张牌） | 决斗的【杀】、万箭的【闪】、借刀的"使用【杀】"、无懈的收集式窗口；提供者在这些窗口里以 `CANDIDATES` 追加（`respond_declared` 为真时闭嘴：赢家已定） |
| `SELECT_CARDS` | 流水线选牌步 | 自己区域的直选在 `candidates.js` 判；`spec.max_per_suit` 存在时按花色收窄（涉猎，0 下限由牌面决定） |
| `SKILL_ACTIVATION` | 钩子的"是否发动" | 键含时机；同帧第二次触发会复用答案（已裁定） |
| `SELECT_OPTION` | 选项步 / 手写二选一 | 单个选项 + `Pass` 走"提交/放弃"确认形态；**置灰**的选项（`PickOption.disabled` → `StepPool.disabled`）在预发动清单里**照样列出来但不可选**（蛊惑的牌名弹窗：列全牌堆名，此刻用不了的画灰），引擎的逐步校验同样拒收它。**逐步询问**这一面自票 07 起同样有置灰口：`Engine.ask_option(..., disabled=..., hero_card=..., option_texts=...)` 把置灰项留在询问的 `options` 里、只从 `legal_actions` 剔除（提交被拒），投影派生 `option_details`（技能名 / `descriptions.json` 原文 / 置灰标记）给界面画灰——目前唯一载体是"展示武将牌"那一类（票 02 §4.2 B 段），首个真实调用者 = 【天成】（`hero.wuming.tiancheng`；选项 id 带展示序号 `tiancheng:reveal:<k>`，效果行文本给具体 X） |
| `SELECT_OPTIONS` | **多选选项**（界夏侯渊【神速】的「至多三项」= 首个载体，jie-feng-batch2 票 01a）：一张弹窗里逐项勾选 | 答案 = `Action.SelectOptions(choices)`（集合，**不是**单个 `choice`）；候选 = `[min_selected, max_selected]` 内、不含 `disabled` 的**每个子集**的全量枚举（空集排第一 ⇒ 超时兜底 = 一项都不做）；`disabled` 与单选面同语义（留在 `options` 里画灰、不进候选）；引擎入口 `Engine.ask_options`（手写询问，**不进** `steps` 闭集、不进预发动清单） |
| `ARRANGE` | 观星式排列 | 不能下发预发动清单 |
| `DISTRIBUTE` | **计数式分配**（神周瑜移动版【业炎】= 首个载体，huo-shenzhouyu-mobile 票 05）：一张弹窗里给若干座位各配一个计数（`seats` / `max_total` / `per_seat_max`） | 候选 = 「每座 0..单座上限、合计 ≤ 上限」的全量枚举；答案 = `Action.Distribute(counts)`（座位升序、省略 0）；`counts=()` 键最小 ⇒ 超时兜底 = 不分配 |

### 3.5 区域 `Zone`

| 区域 | 读法要点 |
|---|---|
| `DECK` | 牌堆（顶 / 底） |
| `HAND` | 手牌（对别人不可见） |
| `EQUIP` | 装备区（公开） |
| `JUDGE` | 判定区（公开，但"牌不为主人拥有"） |
| `DISCARD` | 弃牌堆 |
| `PROCESSING` | 处理区（使用中的牌在这里） |
| `HERO` | 武将牌上（本仓按真区域建模，见账本）。**坐标必须带 `skill_instance`**（区键 = `owner:skill_id`，「星」与【谦逊】是两个互不相干的区）；`Engine.selection_face` 与 `PickCards.hero_zone` 可把它当**可选面**铺开（huo-shenzhugeliang 票 02） |

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
| `MAXLIFE` | **体力上限变化事件帧**（wuming 票 04 解封「加」侧）：三步 = ① 派发 `MAXLIFE_INCREASE_BEFORE` → ② 落上限 + `emit_delta(HpMaxChangeDelta(amount=+N))` → ③ 派发 `MAXLIFE_INCREASE_AFTER`。帧私有带 `direction` + `amount`（`HpMaxChangeDelta.amount` 恒为幅度，方向由 `before` / `after` 读出）；唯一写入口 `Engine.raise_hp_max`（首个调用者 = 票 01 的【天成】，`src/sgs/skills/start.py`）。**不同步体力**（「满体力同步下调」只属于减侧）；"减"分支写好但**暂无调用者**——`reduce_hp_max` 不迁、`MAXLIFE_DECREASE_*` 不解封。**票 04 起减侧补上 0 档**：`Engine.reduce_hp_max` 减到 0 ⇒ 体力值置 0 + `pass_away` **直接死亡**（不进濒死、无来源）——与规则 §四 4.15 L431–433 一致，且"减少体力上限的效果一律不兜底"（用户 2026-11-07）；`maxlife_apply` 的减侧分支因此**委托** `reduce_hp_max`，时机仍不解封 |
| `RANK_COMPARE` | **拼点事件帧**（huo-taishici-xunyu 票 03 解封）：派发 `RANK_COMPARE_ON` → 逐参与者选一张手牌（不可放弃）→ 扣置处理区（`MoveReason.PINDIAN`，内联派发「失去牌后」）→ 亮出比点 + `pindian` delta + 结果回写发起帧（`pindian_result` / `pindian_won`）→ 派发 `RANK_COMPARE_RESULT_AFTER` → 两张牌置入弃牌堆（同一 `PINDIAN` 原因）。平点 = 双方都没赢；参与者缺手牌 ⇒ 整条中止（零副作用） |
| `REJUDGE` | **改判事件帧**（feng-zhangjiao 票 02）：一次"用一张牌代替 / 替换判定牌"。帧内两段移动（新牌 → 处理区；原牌 → 弃牌堆（**代替**）/ 改判者手牌（**替换**）），不派发任何"打出时 / 使用后"钩子。判据：动作是 `SubstituteJudgeCard`（不是 `PlayCard`），去向由 `SkillDef.rejudge_original_to` 回答 |

### 3.7 步骤类型

| 步骤 | 短名 | 用途 |
|---|---|---|
| `PickCards` | `cards` | 选牌（`lo`/`hi`/`zones`/`face`/`card_types`/`slot_kinds`/`exclude_cards`） |
| `PickSeats` | `seats` | 选角色（`pool` 可依赖前序答案） |
| `PickOption` | `option` | 闭集选项（`options` 可以是**可调用档**，按前序答案现算；`disabled` = **显示但不可选**的那几项（同形态的可调用档）⇒ 清单的 `StepPool.disabled` 让界面置灰、校验器拒收；`revealed` = 观看授予 ⇒ 选择型弹窗里牌面 + 选项同窗：韬·刘备【义起】的「看弃牌堆顶 x 张再选一种类型」是**一问**） |
| `PickProvide` | `provide` | 选一张用于"提供"的牌（转化 / 提供者） |
| `Arrange` | `arrange` | 排列（观星）；含它的流水线不下发预发动清单。规格可以是 `DeckTopArrange`（牌堆顶 / 底）或 `TwoRowArrange`（**两行交换**：具名行 + 定切分 + 不枚举候选 + 行内不调序；首个载体 = 神诸葛亮【七星】），后者见 §4.1 |

## 4. UI 触点（每项必须落到一个已存在的测试垫片）

| 触点 | 位置 | 判据 |
|---|---|---|
| 自己的牌直选（不铺左下角面板） | `candidates.js:isOwnZonePick` | 只看载荷形状，不认技能名 |
| 选项按钮 / 单选项确认（提交 / 放弃） | `options.js` + `pending.js:isSingleOptionalOption` | 候选里恰一个 `SelectOption` + `Pass` |
| 选项中文标签 | `options.js:OPTION_LABELS` | 只在**多选项**形态读得到；单选项确认读不到 |
| 逐步文案 | `SkillDef.step_requirements` | SELECT_OPTION 读 `skill_id` 冒号后那段；SELECT_CARDS 恒读 `card` |
| 一句话文案 | `SkillDef.requirement` | 提示条与预发动清单 |
| 预发动清单 | `preactivation.js` | 由流水线步骤渲染；`Arrange` 不下发；**触发技要翻 `preactivated`**（见下） |
| 选项**置灰不可选** | `preactivation.js:preactivationDisabled` | `StepPool.disabled`（`PickOption.disabled`）；画成 `.dis` + `disabled` **且不接 click**，键盘 / 合成事件那一层由 `selectPreativationOption` 再挡一次；垫片 = `tests/support/ui_smoke.mjs` 的合成载荷 |
| 舞台 / 动效 | `session.js` / `effects.js` | 见 `docs/agents/frontend-map.md` |
| 牌面标签 | `card-view.js:cardTagsNode` | 标签类型必须在 `CARD_TAG_TYPES` 登记 |
| 武将详情 / 被封锁技能图标 | `hero-detail.js` / `table.js` | |

新增 UI 形态（垫片里没有的）= **C3**：停手，另开 UI 票并走真实浏览器验收。

**触发技翻 `preactivated` 的准入条件（票 23 报障）**：触发技（被动入口）的技能按钮按
U1-06 **不接 click**，这一问的唯一入口是清单画出的浮动按钮「进入预发动」——它要求
`pending.preactivation` 有步骤**且**技能条目带 `preactivated`（读取方
`preactivation.js:preactivatedSkill`）。两个写方各说一半，缺一半的结果不是"退化成逐步
问"，而是**死路**：提示条写着"点技能【…】进入预发动"，按钮却是灰的。

故两条规矩一起守：

1. **没翻就别下发清单**：`ask_skill_activation` 只给已翻的技能带 `preactivation`
   （`SkillActivationQuery.preactivation` 的"未迁移 ⇒ 空元组"，票 23 补上）。没翻的
   触发技因此走「将提交：发动【X】」的直提态，座位 / 暗手牌照旧逐步问——不会死路。
2. **翻之前先探一次清单**：条件成立时清单渲得出来吗（首步候选是否恒非空；`manifest`
   对"读别人手牌"这类隐藏面返回空元组）。拿不准就在真局面上跑一遍 `preactivation_for`：

| 首步形状 | 清单 | 怎么做 | 例 |
|---|---|---|---|
| 选自己的手牌 / 装备区 | 渲得出 | 翻 `preactivated=True`，并补 AI 覆盖场景（`tests/test_heuristic_ai.py` 的 `_COVERAGE_CASES` 会机械要） | 【流离】【清俭】 |
| 选**别人**的公开区域（装备 / 判定），且 `available` 保证非空 | 渲得出 | 同上 | 【麒麟弓】 |
| 座位步，池由 `available` 门保证非空 | 渲得出 | 同上；也可以按"直提态 + 逐步问"不翻（【突袭】当前就是这一档） | 【突袭】 |
| 选项步（闭集恒非空） | 渲得出 | 可翻可不翻；【神速】取**不翻**（激活问走直提态，选项 / 目标 / 弃牌逐步问） | 【神速】 |
| 读别人的**手牌**（隐藏面：`reveal_hand=False` / `_hidden_face`） | 渲不出 | **别翻** | 【反馈】【利驭】 |

### 4.1 实现方式（闭集，一问一行）

ADR-014 只给了这几张表面：**牌桌（直选 / 清单 / 提示条）+ 四类询问弹窗 + fail-closed**。
票面「询问剧本」的"实现方式"一格从这里选一行；选不出 = **C3 停**（新面要走 UI 票 + 真实浏览器
验收，不在技能代码里凑）。

| 实现方式 | 答案是哪个表面 | 判据 / 入口 | 样板（可套用的模板） |
|---|---|---|---|
| 牌面直选 | 牌桌：自己的手牌 / 装备，不铺面板 | `candidates.js:isOwnZonePick` | `hero.sunquan.zhiheng` |
| 预发动清单 + 浮动按钮「进入预发动」 | 牌桌：清单展开，牌桌照常接选择 | `preactivation.js:preactivatedSkill` | `hero.daqiao.liuli` |
| 选择型弹窗 | 弹窗：闭集选项 / 枚举（花色、牌名） | `modal.js:stepDialogKind`（`choice`） | `hero.jie_xushu.jianyan` |
| 未知区域弹窗 | 弹窗：他人手牌 / 装备 / 判定里盲挑一张 | `modal.js:stepDialogKind`（`unknown`） | `hero.jie_simayi.fankui` |
| 排列弹窗 | 弹窗：牌堆顶排列（前端唯一可代答的面） | `modal.js:stepDialogKind`（`arrange`） | `hero.zhugeliang.guanxing` |
| 亮牌弹窗 | 弹窗：亮出后逐张确认 | `modal.js:stepDialogKind`（`reveal`） | `hero.guojia.yiji` |
| 小选项按钮组 | 牌桌：`SELECT_OPTION` 的按钮组（≤3 项、且没有 `option_labels`） | `pending.js:renderKind`（`options` 档） | 雌雄双股剑 / 界孙尚香【结姻】 |
| 选座位（点场上武将） | 牌桌：`SELECT_OPTION` + `option_seats`，点武将 = 选中那条 `seat:N` | `pending.js:renderKind`（`seat` 档）/ `interaction.js` | `hero.zhangliao.tuxi` |
| 单选项确认（提交 / 放弃） | 牌桌：标题取 `requirement`，**读不到**选项标签 | `pending.js:isSingleOptionalOption` | `hero.sunshangxiang.xiaoji` |
| 直提态 | 牌桌：唯一候选直接提交，不铺面（提示条只做说明） | `pending.js:directAction` | `hero.jie_zhangliao.tuxi` |
| 分配弹窗（计数式） | 弹窗：逐座位一行计数（点一次 +1）、「重置」、右端「提交」恒可点 | `modal.js:stepDialogKind`（`distribution`） | `hero.shen_zhouyu_mobile.yeyan`（huo-shenzhouyu-mobile 票 05） |
| 两行交换（排列面·**不枚举**） | 弹窗：上排「星」/ 下排「手牌」，只上下交换、行内不调序；答案由界面构造、服务端只校验形状 | `modal.js:stepDialogKind`（`arrange`）+ `projection._arrange_spec` 下发 `rows` / `enumerate: false` / `reorder: false` | `hero.shen_zhugeliang.qixing`（huo-shenzhugeliang 票 02；外壳同 `hero.jie_lidian.xunxun`） |
| 武将牌上的牌点选（弹窗） | 弹窗：一行牌（**牌面对宿主可见**），点选后提交 | `modal.js:stepDialogKind`（`heroPick`，判据 = 槽的 `ref.zone === "hero"`） | `hero.shen_zhugeliang.kuangfeng` / `.dawu`（huo-shenzhugeliang 票 02） |
| 两行观看选择面（弹窗） | 弹窗：上行被观看角色的手牌 / 下行自己的手牌；同花色置灰（两行一起算）、四色各一才可提交；右端「放弃」= 候选里的 `Pass`（超时同义），凑不出四色时只剩「放弃」 | `modal.js:stepDialogKind`（`watchTwoRows`；判据 = `SELECT_CARDS` + `spec.max_per_suit` + 槽位恰好跨两个座位且含询问者；空选择面认服务端 `dialog` 标记）；渲染 = `reveal.js:renderWatchRows`，置灰复用 `reveal.js:suitSlotState` | 框架票 93（lei-shenganning）；首个技能载体 = 神甘宁【魄袭】（`hero.shen_ganning.poxi`，票 01） |
| 多选弹窗（勾选 + 提交） | 弹窗：逐项一行勾选框（置灰项列出来但点不动）、右端「提交」；勾选是本地态，提交时拼出候选里那条 `SelectOptions` | `modal.js:stepDialogKind`（`multiChoice`）+ `options.js:renderMultiSelect` + `answer.js:multiSelectAction` | `hero.jie_xiahouyuan_mobile.shensu`（界夏侯渊移动版【神速】「至多三项」，jie-feng-batch2 票 01a / 01） |
| fail-closed | 牌桌：一句明话，不铺任何控件（不发明合法性） | `pending.js:renderUnsupported` | —（并写一句为什么没有面） |

**套不套用要写出来**：第 3 步反查出的相似技能就是模板源。套用 ⇒ 票面写它的 id 与抄哪一行；
不套用 ⇒ 写一句为什么（形状不同 / 读法不同 / 它的模板有一半本技能用不上）。**不写 = 没选**。

## 5. 新机制怎么进框架（三档判定）

先查它现在到哪一档，**别假设**：

| 档 | 现象 | 例子 |
|---|---|---|
| 无 | 全树没有对应字段 / 帧 / 时机 | （当前为空；曾登记过的"标记"已解封，见下行） |
| 只有枚举位 | `Timing` 里有名字，但没人派发、没有流程 | **减体力上限**（`MAXLIFE_DECREASE_*` 留位、`reduce_hp_max` 未迁进帧）；**废除 / 恢复槽位的时机**（「废除前 / 废除后」在补充一手里有名，`Timing` 里没有——`Engine.abolish_slots` 只做事件本身，不派发时机） |
| 已解封 | 有帧 / 钩子 / 步骤 / 状态位，能被技能用 | **标记**（`Player.markers` + `Engine.add_marker` / `remove_marker` + `marker_change` delta，账本 L800；首批载体 = 神诸葛亮【狂风】【大雾】/ 神关羽【武魂】，雷包【镇骨】用同一机制做"欠账到点结算"；**标记自身效果**自 lei-shenganning 票 94 起接线：`Marker.effect` 指向一个效果定义（`carrier = CarrierKind.MARKER`，在 `CATALOG` 里登记但**不是任何角色拥有的技能** ⇒ 武将面 / 技能按钮区都不列它）⇒ `resolve_instances` 以 `SkillSourceKind.MARKER` 产出实例，**不受技能失效影响**，寿命两条 = `clear_on_source_death` / `clear_on_source_skill_lost`；首个载体 = 神甘宁【劫营】的「营」（`hero.shen_ganning.jieying_mark`，票 01））；**横置**（`Player.chained`）；**翻面**（`Player.flipped` + 「回合开始前」跳过整回合，移动版界曹仁【据守】是首个载体）；**状态位写入口的事件派发**（chenqun-guyong 票 03 / W2：`Engine.set_flipped` / `Engine.set_chained` 惰性派发 `STATE_FLIPPED` / `STATE_CHAINED`——事件排进 `Engine._state_pending`、下一条 `_step` 开头用不压栈的临时帧派发；值变了才算事件、幂等写不算、「回合开始前翻回正面」算；无注册实例时零记录）；**废除 / 恢复槽位**（lei-batch 票 02 立、lukang-mobile 票 03 改成**逐槽**：`Player.sealed_zones` 只存 7 个槽 id（判定 `judge_1..3` + 装备四槽），`Engine.abolish_slots` / `restore_slots`（**一次调用 = 一个废除事件**，槽里牌按来源区分组先强制弃置、再落封印；`judge` / `equip` 两词只是读取面的整区兼容名 = 该区槽全封）；读点 = `check_targets` 的装备牌与延时锦囊两条系统规则、`put_equipment_card`、闪电转移；首个载体 = 雷包·张绣【雄乱】（一次废 7 槽））；**`TURN_START` 的帧外派发**（夏侯渊【神速】：`_turn_start` 改走 `_dispatch_phase_body` + 一次性清理闸）；**`GAME_START` 的帧外派发**（票 03：`_start` 改走 `_dispatch_phase_body` + `_started` 派发完才置真 ⇒ 钩子可停可续；L0 帧 `source=None` / 记录 `actor=None`，无钩子时记录逐字节不变）；**`MAXLIFE` 的「加」侧事件帧**（wuming 票 04：`Engine.raise_hp_max` 推帧 + `MAXLIFE_INCREASE_BEFORE/AFTER` 解封；**减**侧仍留枚举位，`reduce_hp_max` 未迁）；**拼点**（huo-taishici-xunyu 票 03：`RANK_COMPARE` 帧 + `RANK_COMPARE_ON` / `RANK_COMPARE_RESULT_AFTER` 解封 + 新 `MoveReason.PINDIAN` + `pindian` delta；首个载体 = 火包·OL 太史慈【天义】、荀彧【驱虎】） |

判定完：

- 已解封 ⇒ 按第 2 节选形状，正常做；
- 只有枚举位 / 无 ⇒ **C1 停**，产出架构缺口票（缺哪个原语、为什么现有五种组不出来、还要动哪些共享文件）。

## 6. 更新触发（谁改这张表）

新时机 / 新接缝 / 新修饰点 / 新询问 / 新帧型 / 新区域 / 新步骤类型，或某个机制从"枚举位"被
解封——**落地那张票**顺手改对应小节；`tests/test_skill_authoring_docs.py` 会反向核（漏登记即红）。
