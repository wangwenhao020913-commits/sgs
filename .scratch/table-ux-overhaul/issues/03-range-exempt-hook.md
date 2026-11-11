# 03: 合法性分层——把"距离豁免"从 TARGET_POOL 拆出来

**What to build:** 新增"距离/攻击范围豁免"挂点，把 6 个"无距离限制"技能迁过去，
`TARGET_POOL` 只留**目标合法性增删**。目的：任何"放宽距离"的修饰器都不再能复活被判定非法的座位。

**Blocked by:** None
Status: resolved
Type: task
Touches: src/sgs/effects/modifiers.py, src/sgs/effects/actions.py, src/sgs/skills/active.py, src/sgs/skills/life_after.py, src/sgs/skills/response_window.py, src/sgs/skills/phase_body.py, tests/test_hero_lei.py, tests/test_hero_jie.py, tests/test_m5_equipment.py
Ruling: spec §2 Q2（用户 2026-11-11 签 A）

## 根因（spec §1 #2）

`check_targets`（`effects/actions.py:401-462`）的层次是：
① `TargetRule`（self_only / 目标任务数）→ ② 距离与攻击范围 → ③ 延时锦囊/封印规则 →
④ **附加谓词 `TARGET_FILTERS`**（`actions.py:445-451`，借刀 = "有武器 ∧ 攻击范围内有合法【杀】目标"）→
⑤ **`TARGET_POOL` 修饰器链**（`:452-462`）。

雄乱 `_xiongluan_distance_pool`（`skills/active.py:5233-5249`）只挡 `TargetRule.self_only`，
其余**无条件** `(*current, target)` ⇒ 它把 ④ 判为非法、以及别的技能在 ⑤ 里**删掉**的座位全部加回来：

- 【借刀杀人】⇒ 可指定无武器角色（实测池 `()`→`(1,)`；清单文案仍写"装备区有武器"；提交被接受；
  持刀者无武器 ⇒ 交给武器那步读 `None` ⇒ **整张牌空转**）；
- 【过河拆桥】/【顺手牵羊】⇒ 可指定无牌角色；
- 【乐不思蜀】⇒ 可指定判定区已封印的角色；
- 【空城】⇒ 复活被删座位（`TARGET_POOL` 链按 `skill_id` 排序，`hero.zhangxiu.xiongluan` 晚于
  `hero.jie_zhugeliang.kongcheng`；坑已登记于 `skills/phase_body.py:1209-1212`）。

根因是**一个挂点两用**：`TARGET_POOL` 既被当"补目标以豁免距离"，又被当"增删目标合法性"。

## 要做什么

1. **新挂点**：`ModifierPoint.TARGET_RANGE_EXEMPT`（命名由实现定），语义 = "本次使用可以无视距离/攻击范围"。
   求值点在 `check_targets` 的 ②，**在 ④⑤ 之前**：它只能让"仅仅被距离挡掉"的座位重新进入 ② 之后的
   ③④⑤ 全套判据，**不能直接产出池成员**。
2. **迁移 6 个消费者**（都是"无距离限制"语义，逐个确认没有顺带放宽别的）：
   - 【天义】`active.py:5006`、【雄乱】`active.py:5287`、【决堰】`active.py:6157`、
   - 【诈降】`life_after.py:208`、【烈弓】`response_window.py:775`、【神速】`phase_body.py:1301`。
3. **`TARGET_POOL` 只留合法性增删**：帷幕（`hero_lin.py:610`）、空城、方天画戟的"额外目标合法性同标准"
   （`catalog.py:3446`）、C 族剔自己（`target_pool.py:97`）、以及【雌雄】一类"删目标"。
4. **把"补目标"这个能力单列**（如果确实有合法用法，例如将来"可以对别人使用【无中生有】"）：
   给它一个**独立的挂点**，且它必须**在 ④ 之后仍被 ④ 约束**（实现上 = 补进来的座位要重跑 ③④）。
5. **写一条结构性纪律**：`TARGET_POOL` 的回调**不许**新增池成员，除非它能证明该成员通过了 ③④
   （用一条合成断言钉住）。

## 验收

- 新增（`tests/test_hero_lei.py`，雄乱组现只有 `self_only` 一例）：
  - `test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon`
  - `test_xiongluan_never_resurrects_a_dismantle_target_with_no_cards`
  - `test_xiongluan_never_resurrects_a_sealed_judge_area_target`
  - `test_xiongluan_never_resurrects_a_kongcheng_seat`
  - 四条都断言**两处**：`check_targets` 与 `parse_phase` 的 `card_preactivations`（借刀那条）。
- 新增：`test_the_range_exempt_hook_is_evaluated_before_the_target_filters`（合成修饰器，证明层次）。
- 回归：6 个迁移技能的既有用例必须**结果不变**（`tests/test_hero_jie.py`、`tests/test_hero_feng.py`、
  `tests/test_hero_huo.py`、`tests/test_m5_equipment.py` 等）。

## 会红的既有断言

- 6 个技能里凡是用"池比原来大"来断言的用例；逐条核，**只允许池变小的方向**（本票只会让池更严）。
- 金录像：`TARGET_POOL` 的回调只在有该技能时改变行为 ⇒ 预计 0 条基准受影响（跑 `check_goldens.sh` 确认）。

## Answer

**已实现（2026-11-11，提交见本票落点；engine-only，零网页改动）** —— 票面五条"要做什么"全部落地，
`## 验收` 的 5 条新断言 + 1 条迁移面名单断言全绿，6 个迁移技能的既有用例**结果不变**（未改任何既有断言），
47 条标准金录像**逐字节不变**。

### 改动摘要

1. **新挂点** `ModifierPoint.TARGET_RANGE_EXEMPT`（`src/sgs/effects/modifiers.py`）：语义 = **本次使用
   可以无视距离 / 攻击范围**。`current` 从**空元组**起，回调**追加**被豁免的座位（集合语义、只许加）。
   求值点在 `check_targets` 的 **②**、**在 ③④⑤ 之前**：被豁免的座位只是**跳过**"攻击范围 +
   额定距离"两条判据，随后照常走 ③ 延时 / 封印 → ④ `TARGET_FILTERS` → ⑤ `TARGET_POOL` ⇒ 它
   **不可能复活**被后面几层判非法的座位，也**不产出**池成员（池成员仍由 ② 的座位环产出）。
2. **`check_targets` 的层次写死成 ①→⑤**（`src/sgs/effects/actions.py`），并新增一个关键字
   `distance_exempt: bool = False` = **调用方**声明"这一次使用距离整条豁免"，只给"技能侧自算池"的
   载体用（唯一读者【神速】）：跳过的仍是同一个 ②，③④⑤ 照常，不存在第二条放宽路径。
3. **迁移 6 个消费者**：【天义】【雄乱】【决堰】（`skills/active.py`）、【诈降】（`skills/life_after.py`）、
   【烈弓】（`skills/response_window.py`）→ 新挂点；【神速】（`skills/phase_body.py`）→
   `check_targets(..., distance_exempt=True)`（不再在技能侧摊"全体其他存活"再喂 `TARGET_POOL`）。
   逐个核过"只放宽距离"：天义 / 诈降 / 决堰 / 武圣 / 武神 = 全体其他存活；烈弓 = 距 ≤ 点数者
   （逐座位，正是新挂点取**集合**的原因）；决堰的旧两条修饰器（`TARGET_POOL` 并入 + `DISTANCE_LIMIT`
   返回 `None`）合并成一条新回调（新挂点同时覆盖攻击范围与额定距离两条轴，行为逐值相同，含
   "决堰先于断粮、`None` 不许收窄"那条也随之不再适用）。
4. **`TARGET_POOL` 现在只剩合法性增删**：空城（标准 / 界）×1 + 谦逊 + 帷幕 = **4 条剔除自己** +
   方天画戟的**空操作**（"额外目标合法性同标准"）。雄乱回调里 lei-batch 票 04 的
   `TargetRule.self_only` 守卫**删除**：自用牌在 ① 就返回、走不到 ②，守卫已是结构上不可达的死代码。
5. **另加迁移界【武圣】② / 【武神】②**（`skills/heroes.py`，不在票面六条名单里）：它们是同一缺陷类的
   `TARGET_POOL` 并集回调，而票面第 3 条要求 `TARGET_POOL` 只留合法性增删——不迁这两条，第 3 条与
   下面那条名单断言都不成立。迁移后行为除"不再复活"之外逐值不变（`tests/test_hero_jie.py` 的两条
   既有 `check_targets` 断言原样通过），`resolve_instances` 的链序巧合（旧写法靠
   `hero.jie_guanyu.wusheng` 恰好排在空城之前才没出事）也随之作废。
6. **第 4 条（"补目标"独立挂点）不落**：今天没有合法消费者（票面写的是"如果确实有合法用法"），
   新增机制就是投机；纪律改落 docstring + `docs/agents/skill-authoring/shapes.md` §3.3
   （"回调不许新增池成员，除非能证明该成员已通过 ③④；将来要在 ④ 之后重跑 ③④ 或另立挂点"）。
7. **第 5 条（结构性纪律）** 落在三处：`ModifierPoint.TARGET_POOL` 的 docstring、`shapes.md` §3.3、
   以及 `tests/test_hero_catalog.py::test_the_target_pool_point_only_carries_legality_add_and_remove`
   ——把全体 `TARGET_POOL` 消费者钉成**显式名单**（新增一条必须先想清楚它属于"距离"还是"合法性"）。
   层次本身由合成修饰器用例钉住（见下）。

### 验收 → 断言（全部存在且绿）

| 验收条目 | 断言 |
|---|---|
| 借刀杀人不再能指定无武器角色（④ 判非法者不复活） | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon` |
| 拆 / 顺不再能指定无牌角色（④） | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_dismantle_target_with_no_cards` |
| 【乐不思蜀】不再落到判定区已封印的座位（③） | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_sealed_judge_area_target` |
| 【空城】删掉的座位不被加回（⑤，且断的是**链序复现**那条：座位 = 界诸葛亮） | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_kongcheng_seat` |
| 挂点在 ④⑤ **之前**（合成修饰器证明层次） | `tests/test_hero_lei.py::test_the_range_exempt_hook_is_evaluated_before_the_target_filters` |
| `TARGET_POOL` 只留合法性增删（第 3 条 + 第 5 条的名单面） | `tests/test_hero_catalog.py::test_the_target_pool_point_only_carries_legality_add_and_remove` |
| 6 个迁移技能的挂点不漂回 `TARGET_POOL` | `tests/test_hero_catalog.py::test_the_range_exempt_point_carries_the_six_no_distance_consumers` |
| 6 个迁移技能的既有用例结果不变 | `tests/test_hero_jie.py` / `test_hero_feng.py` / `test_hero_huo.py` / `test_hero_lin.py` / `test_hero_tao.py` / `test_hero_transform.py` / `test_m5_equipment.py` / `test_normal_tricks.py` 全绿 |
| 金录像零变化 | `bash tools/check_goldens.sh` → PASS：47 条标准 108 金录像逐字节不变 |

四条雄乱用例的**两处观测面**：`check_targets`（池本身）与 `Engine` 建出来的**出牌阶段询问**
（真实产出路径）。借刀那条断三处：`check_targets` 池空、`card_preactivations` 无 `borrowed_sword`
键、提交那份"旧实现会接受"的附带指定被 `IllegalActionError` 拒（首因"候选池"）；拆 / 乐 / 空城
三条断同一份询问的 `legal_actions`（因为这些牌**没有** `card_preactivations` 这个字段）。

### 会红的既有断言

**零**。四类复活是**新增**断言的红：旧代码上分别红成 `(1,)`（借刀）、`(1, 2)`（拆）、`(2, 1)`（乐）、
`(1, 2)`（空城），合成层次用例在新挂点未实现时红在 `AttributeError: TARGET_RANGE_EXEMPT`、加枚举
之后红在池 `(1, 5) != (1, 4, 5)`（逐条看过失败原因再动手）。迁移只让池**变小或不变**，故没有
"用池比原来大来断言"的既有用例需要改。

### 门禁结果（本机实测）

- `.venv/bin/pytest tests/test_hero_lei.py` → **81 passed**（含 5 条新增 + 10 条雄乱组）
- `.venv/bin/pytest tests/test_hero_jie.py tests/test_hero_catalog.py tests/test_hero_feng.py tests/test_hero_lin.py tests/test_hero_huo.py tests/test_m5_equipment.py` → **605 passed**
- `.venv/bin/pytest tests/test_hero_tao.py tests/test_hero_transform.py tests/test_normal_tricks.py` → **159 passed**
- `.venv/bin/pytest tests/test_web.py` → **304 passed**；`.venv/bin/pytest tests/test_heuristic_ai.py tests/test_ai_adaptation.py tests/test_ai_cases.py` → **218 passed**
- `.venv/bin/pytest tests/test_skill_authoring_docs.py` → 18 passed；`tests/test_card_family_sites.py tests/test_negation_sites.py tests/test_source_discipline.py` → 36 passed
- `bash tools/check_goldens.sh` → **PASS：47 条标准 108 金录像逐字节不变**
- 全量门禁 = 本提交的 pre-commit hook（`tests/run_all.sh --quiet`）绿。

### 刻意选择的读法（逐条）

1. **挂点名与取值**：`TARGET_RANGE_EXEMPT`，`current` = **被豁免的座位元组**（空元组起步、只许追加），
   不是布尔。理由：【烈弓】① 的豁免**逐目标**成立（距 ≤ 点数者才放宽），布尔表达不了；集合取值
   也让"只重新放行被距离挡掉的座位"成为一个可断言的集合差。它同时覆盖**攻击范围**与**额定距离**
   两条轴——【决堰】的"使用**牌**无距离限制"因此只需一条回调。
2. **"只重新放行被距离挡掉的座位"怎么证**：结构上，终池 = ({通过 ①②} ∩ ③④⑤) = 无豁免时的终池
   ∪ (只被距离挡掉的座位 ∩ ③④⑤)。合成用例给了两个方向的读数：距离 2 的座位 4（区域里有牌）
   **进池**（挂点确实在 ② 生效）、距离 3 的座位 3（无牌，④ 判非法）与距离 2 的座位 2（贾诩【帷幕】，
   ⑤ 删掉）**不进池**（④ / ⑤ 在挂点之后照常跑）。少任何一半，断言都会给出另一个元组。
3. **【神速】为什么不挂修饰器**：它的目标池是技能侧自算（那张**纯虚拟**【杀】不经过常规候选生成），
   而纯虚拟牌不带"这条技能"的标记 ⇒ 修饰器无法把豁免限定在"这一张"上（挂上去会把这名武将之后
   所有【杀】都放宽）。故给它 `distance_exempt=True` 这一档**调用方声明**：跳过的仍是同一个 ②。
4. **票面的 `parse_phase` 在树上不存在**（本仓只有 `parse_action` / `_play_phase`）：按"出牌阶段询问里
   `card_preactivations` 的写方"读 — `Engine._build_query` → `_play_phase` 的 `designation_actions`
   → `skills/flow.py:card_preactivations`。故"第二处"断言落在 `Engine` 建出来的 `PLAY_PHASE` query 上；
   而 `card_preactivations` **只有附带指定的牌（借刀）才有**，另外三张牌上断它只会得到恒空的绿，
   所以它们改断同一份询问的 `legal_actions` 候选面（同样是真实产出路径，不是合成载荷）。
5. **界武圣② / 武神② 一并迁移**（票面六条之外）：见"改动摘要"第 5 条；不迁则第 3 条不成立。
6. **第 4 条不落机制**（"补目标"独立挂点）：无合法消费者；纪律留在 docstring + shapes.md。
7. **AI 适配不适用**：没有新增 `QueryKind` / 动作面 / 信息通道（候选**形状**不变，只是池更严）⇒
   `docs/agents/ai-adaptation.md` §1 与 `tests/test_ai_adaptation.py` 未动。
8. **账本 / ADR 写回留给票 06**：`开发文档/06-里程碑与风险.md` 与 `GLOSSARY.md` 未动（本票只改代码与
   技能制作文档 §3.3），`shapes.md` 的 §3.3 表新增 `TARGET_RANGE_EXEMPT` 行并重写 `TARGET_POOL` 行。
9. **`Acceptance-map` / `map.md` / 别的票未动**（`map.md` 的未提交改动属于 orchestrator）。
