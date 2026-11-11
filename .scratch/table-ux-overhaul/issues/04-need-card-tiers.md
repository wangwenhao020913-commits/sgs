# 04: 需要牌窗口按档逐档问 + 提供者锁按事件复位

**What to build:** "需要/可以使用或打出牌"的窗口按**档位顺序逐档问**（武将档 → 装备档 → 牌档），
不再把不同档的候选混在一张面里；顺带修"提供者一辈子只问一次"的锁（【无双】里的【八卦阵】）。

**Blocked by:** 01（`view()` 的游标化；逐档问会让询问条数变多，先让每次请求便宜）
Status: resolved
Type: task
Touches: src/sgs/engine/steps.py, src/sgs/effects/actions.py, src/sgs/skills/catalog.py, src/sgs/effects/modifiers.py, src/sgs/agents/heuristic.py, src/sgs/state/protocol.py, tests/test_timing.py, tests/test_hero_response_window.py
规则原文: 三国杀详细规则.txt §4.16 L443–L466、§4.20 L541–L551
Ruling: spec §2 Q1 / Q13 / Q16 / Q17（用户 2026-11-11 签）

## 根因（spec §1 #9）

1. **不同档被混在一张面里**：`_dodge_window` 等四扇窗口把"提供者"与"真牌"一次发给玩家，
   玩家先点低档（如【朱雀羽扇】装备档 1）之后，高档（【渐营】武将档 0）被 `tier_not_downgraded`
   挡掉 ⇒ 表现为"选了一个另一个就没了"。这不是派发只问一轮、不是应答缓存、也不是条件被破坏。
2. **提供者锁按目标清、不按需要牌事件清**：`providers_used` 只在 `_PER_TARGET_FRAME_KEYS`
   （`steps.py:938-970`）里随目标推进清掉；`_accept` 只调 `reset_need_card_floor`（`actions.py:1414-1425`），
   没有清 `providers_used` ⇒ 【无双】要两张【闪】：八卦阵判定失败一次后，即使用真【闪】补上，
   第二张闪仍然不能再发动八卦阵。

## 要做什么

1. **档位表改成用户裁定（Q17）**：

   | 档 | 判据 | 谁 |
   |---|---|---|
   | 0 | 技能 ∧ **不转化** | 【护驾】标/界、【激将】标/界、【傲才】（尚未实现） |
   | 1 | 装备技 ∧ **不转化** | 【八卦阵】 |
   | 2 | **牌**（真牌）∧ **一切转化**（含转化型武将技/装备技） | 真【闪】/【桃】/【无懈】/【杀】、【蛊惑】、界【渐营】②、【龙胆】【武圣】【看破】【急救】【丈八蛇矛】… |

   **转化牌与真牌同档（第 2 档）**：官方 2018 规则集只有"武将技 > 装备技 > 使用牌"的原则，
   无一处裁定转化牌归哪档；移动版实际行为 = 同档 ⇒ 登记为**程序化约定**（账本行由票 06 落）。

2. **实现方式 = 按档迭代，不是把档混面**：`need_card_tier`（`actions.py:1352`）保留，但窗口的
   候选面按 `(档, 引擎既有顺序)` 分面，**先给最小档的面**；玩家"不发动/不出" ⇒ 进下一档；
   最后一档"不出" ⇒ 这次需要牌不满足。**同档内仍是"选一条 / 都不发动"的合并面**。
3. **【蛊惑】【渐营】② 落档 2**：它们是 `ActivateSkill` 提供者、载体是武将牌（现算 0 档），
   但产物是转化牌 ⇒ 给技能定义一个**可选覆盖位**（建议 `SkillDef.need_card_tier: int | None`，
   缺省仍按载体派生），只在这两条上写 2。**不写技能名名单**。
4. **交互形状**（Q13）：档 0 多个提供者时一张"选一条 / 都不发动"，单个时一击"是否发动"；
   档 1【八卦阵】**一击确认**（不要"先点技能、再点确认"两步）；档 2 一张面列此刻能用的牌
   （含转化清单入口），可"不出"。
5. **`providers_used` 复位点**：挪到与 `reset_need_card_floor` **同一个点**（一次需要牌事件成立处，
   `_dodge_window._accept` / 决斗的每收下一次响应），语义 = "一次杀的结算过程内，一旦以任何方式
   使用/打出了【闪】，封禁解除"。
6. **AI 适配**：`agents/heuristic.py` 的响应评分现在比较同一张面里的候选；分面后要保证它按
   "当前这一档"比较，且不因分面漏填（跑 `tests/test_heuristic_ai.py`、`tests/test_all_ai_games.py`）。

## 验收

- 新增：`test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing`
  （真引擎：先问渐营、再问羽扇；两种顺序都不丢第二条）。
- 新增：`test_a_dodge_window_asks_hero_tier_then_equipment_tier_then_card_tier`
  （曹操场景：护驾 → 八卦阵 → 真闪，三次询问）。
- 新增：`test_wushuang_a_failed_bagua_judgment_still_allows_bagua_again_after_a_real_dodge`
  （四张闪的局面：判定失败 → 真闪 → 第二张闪仍可发动八卦阵）。
- 新增：`test_a_conversion_provider_is_tier_two`（蛊惑/渐营② 与真牌同档，不先问）。
- 改写：`tests/test_timing.py:1326`（"低档关掉高档"改为"按档迭代，高档先问"）与 `:1849`。
- 记录流会变（询问条数增加）⇒ **本票自己在同一个提交里重录**：先跑 `bash tools/check_goldens.sh`
  拿到变红清单，再 `.venv/bin/pytest tests/test_replays.py --update-golden` 重录，
  复核 `check_goldens.sh` 全绿 + `goals.py` 的机制断言未改弱。
  **重录清单**（哪条基准 / 变了什么 / 为什么）写进 `## Answer`。
  理由：`.githooks/pre-commit` 会在 `src/`、`tests/` 改动时跑全量门禁，
  而 `test_golden_replay_matches_byte_for_byte` 是"真重跑引擎 + 逐字节比对"——
  金录像红就**提交不进去**，所以重录不能拖到票 06（见 `spec §3 注①`）。

## Answer

**已实现（2026-11-11）** —— 票面六条"要做什么"全部落地（engine-only，**零网页改动**），
`## 验收` 的 4 条新断言 + 2 条改写全绿；**重录按 spec §3 注① 落在同一个提交里**（见下
「重录清单」），`goals.py` 的机制断言**一条未改、一条未放宽**。

### 改动摘要

1. **档位表（Q17）落成两处消费面**：`need_card_tier()`（`src/sgs/effects/actions.py`）读
   `SkillDef.need_card_tier`（覆盖位）→ 没有覆盖位时按载体派生。真牌 / 转化牌 = `CARD_USE`(2)；
   【八卦阵】= 装备档(1)；【护驾】/【激将】= 武将档(0)。
2. **按档迭代（不是混面）——两处都改，因为"同一时机"有两个入口**：
   - **需要（使用 / 打出）牌窗口**（【闪】/ 求桃 / 打出·使用响应 / 无懈）：
     `legal_actions_for` 现在 = `_actions_for`（唯一生成口，全部档）+ `_need_card_face`
     （**只留最小档 ≥ floor** 的一张面 + `Pass`）。"`Pass` = 本档不发动、进下一档"由引擎驱动
     （`Engine.ask_query` / `ask_respond` 的逐档循环 + `next_need_card_tier`，判据与候选生成
     **同源**），**窗口一行未改**——它收到的还是一条 `Pass`（最后一档的语义原样）。
   - **同一时机的激活结算**（`skills/catalog.py:_dispatch`）：可选项面只收**当前最小档**
     （票 01 的跨档合并面作废）；`Pass` 先跑本档混合实例的强制段、再写 `pass_floor = 下一档`
     回 `start`（`pass_round += 1` 让答案缓存键换槽）；最后一档才回到票 01 §4-6 的语义。
     全**不可放弃段**的锁定侧顺序面不走"进下一档"（那张面的 `Pass` 是"剩下按默认次序跑完"）。
3. **【蛊惑】/ 界【渐营】② 落档 2**（Q16 / Q17）：`SkillDef.need_card_tier` 覆盖位 +
   `ActiveSkill.need_card_tier` 透传，只在两条声明上写 `2`。**派发逻辑里没有技能名名单**。
4. **交互形状（Q13）**：档 0 的单提供者面 = 一条 `ActivateSkill` + `Pass`（一击"是否发动"）；
   档 1【八卦阵】同样一击确认；档 2 才是"牌一张面（含转化提供者）"。引擎侧只负责"一档一张
   面 + `Pass` 换档"，形状由候选数派生（前端零改动）。
5. **`providers_used` 复位点**：新增 `reset_need_card_event(frame, seat)`（floor + 提供者封禁
   **同一个点**一起清），替换 `_dodge_window._accept`（`steps.py`）、`_duel_accepted`（`ops.py`）、
   `_peach_use`（`steps.py`）三处"需要牌事件成立"的落点。两条【无双】路径（真牌 / 八卦阵）都
   经 `_accept`，故都复位。
6. **AI 适配**：`agents/heuristic.py` 的响应分值**不需要重标定**——引擎保证一张面只有一档，
   `max(scores)` 天然是"按当前这一档比较"，每档都有完整合法面（`Pass` 恒在）⇒ 不会漏填。
   只在该常量处补了这条语义的说明。`tests/test_heuristic_ai.py`（含 SKILL_VALUES 三向同步）与
   `tests/test_all_ai_games.py` 全绿，**零改动**。

### 验收 → 断言（全部存在且绿）

| 验收条目 | 断言 |
|---|---|
| 渐营先于羽扇问（两种顺序都不丢第二条） | `tests/test_timing.py::test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing`（真引擎、真武将 + 真装备：三张面 = 渐营 / 羽扇；Pass 与发动渐营两种应答都走到羽扇） |
| 需要【闪】的窗口按档逐档问（护驾 → 八卦阵 → 真闪） | `tests/test_hero_response_window.py::test_a_dodge_window_asks_hero_tier_then_equipment_tier_then_card_tier`（三张面、每面只有一档；盟友的【闪】不动） |
| 八卦阵判定失败后用真闪补上，第二张仍可发动八卦阵 | `tests/test_hero_response_window.py::test_wushuang_a_failed_bagua_judgment_still_allows_bagua_again_after_a_real_dodge`（三张面：八卦阵黑 → 真闪 → 八卦阵红 ⇒ 抵消） |
| 转化提供者与真牌同档（不先问） | `tests/test_hero_response_window.py::test_a_conversion_provider_is_tier_two`（【蛊惑】端到端一张面；界【渐营】② 走求桃窗口的候选生成口，两者档值都 = `CARD_USE`） |
| 改写 `tests/test_timing.py:1326`（低档关高档 → 按档迭代、高档先问） | `tests/test_timing.py::test_the_higher_tier_is_asked_first_and_a_pass_moves_to_the_next_tier`（旧名 `test_a_lower_tier_pick_closes_the_higher_tier_for_the_rest_of_the_pass` 本分支已不存在：改名而非新增） |
| 改写 `tests/test_timing.py:1849`（接缝同样逐档问） | `tests/test_timing.py::test_the_seam_path_carries_tiers_and_the_same_gate` |

### 会红的既有断言（都在本提交里改掉，逐条说明"为什么它必然红"）

1. `tests/test_timing.py::test_a_lower_tier_pick_closes_the_higher_tier_for_the_rest_of_the_pass`（旧名，本分支已不存在）→ 改名 + 重写：旧名描述的"先点低档 ⇒ 高档关掉"是**被修掉的**行为。
2. `tests/test_timing.py::test_the_seam_path_carries_tiers_and_the_same_gate`：接缝面不再跨档
   （首问只有武将档，Pass 之后才是装备档）。
3. `tests/test_timing.py::test_a_lower_tier_provider_closes_the_higher_tier_in_the_need_card_window`：
   同一形状（名字保留）：三张面 武将档(Pass) → 装备档(黑) → 牌档。
4. `tests/test_timing.py::test_a_failed_higher_tier_provider_leaves_the_lower_tier_open`：
   面数 2 → 3（多出的第三张是牌档；机制未变）。
5. `tests/test_timing.py::test_a_need_card_face_labels_every_candidate_by_its_deliverer`：
   重写为**逐档面**读数（分面后一张面里不可能有两档；【蛊惑】现在是 `CARD_USE`）。
6. `tests/test_timing.py::test_the_need_card_gate_reads_the_same_floor_in_all_four_windows`：
   重写为"同一个 `floor` ⇒ 同一张面（四扇窗口逐一看）"，并加"floor 超过全部档 ⇒ 只剩 Pass"。
7. `tests/test_hero_response_window.py::test_wushuang_a_red_eight_diagrams_counts_as_one_of_the_two_dodges`：
   第二张是**新的需要牌事件** ⇒ 八卦阵再次上脸（旧断言"第二张不再给八卦阵"正是本票要修的 bug）。
8. `tests/test_hero_response_window.py::test_wushuang_a_real_dodge_first_still_offers_the_provider_for_the_second`：
   三张面（先 Pass 装备档才轮到真牌）。
9. `tests/test_hero_response_window.py::test_wushuang_a_black_eight_diagrams_does_not_count`：
   三张面 + 判定黑之后档进到牌档（仍是"黑 = 零张"）。
10. `tests/test_hero_huo.py::test_bazhen_is_offered_once_per_dodge_window`（旧名，本分支已不存在）
    → 改名 `test_bazhen_is_offered_again_in_the_second_dodge_event`：它把**被修的 bug** 写成了断言
    （"同一窗口里八阵只出现一次"）；改成断"第二张=新事件 ⇒ 八阵再次可点、两次判红即抵消"。
11. `tests/test_writeback_landing.py`：`开发文档/06` §5 引用的旧测试名随之更新（只改**引用**；
    那一行的 "Pass = 结束本座位的这个时机" 这类**账本措辞**仍留给票 06 收口）。

### 重录清单（哪条基准 / 变了什么 / 为什么）

**行为有意变更**（spec §3 注① / §4）：需要牌窗口一次事件可能问 2–3 次（每档一张面），同一
时机的激活结算也不再跨档 ⇒ 询问条数与候选面改变。**只有**需要牌窗口 / 激活面涉及的基准
改变，其余逐字节不变。

| 基准 | 变了什么 | 为什么 |
|---|---|---|
| `09-identity-6p-renegade-win` | 同种子重录（2931297 → 2936011 字节） | 分面改变了随机档的候选面与随机流消耗，下游动作序列偏移 |
| `17-nullify-nested-two` | **重新选种** 117 → 163（917417 → 724162） | 旧局涨到 1963 条 / 1.7 MB；"记录条数最少"的命中候选（1065 条），判据不变 |
| `20-nullify-lightning-defers` | 同种子重录（706210 → 780102） | 同 09 |
| `30-halberd-three-targets` | **重新选种** 230 → 904（2858367 → 3324619） | 旧局 4188 条 / 4.3 MiB **越过单文件 4 MiB 绊线**；904 命中同一判据（方天 3 目标）、人数档仍 6（2845 条）；`seed_search.RARE_GOAL_TIERS` 为此把该条的声明搜索空间放宽到 1000（判据 / 人数不动） |
| `31-play-card-response` | 同种子重录（2700271 → 2651417） | 同 09 |
| `34-dismantle-or-snatch-equip-judge` | **重新选种** 288 → 187（802508 → 378817） | 旧局涨到 2083 条；187 命中同一判据（746 条） |
| `35-hero-damage-points` | **重新选种** 198 → 73（465008 → 527522） | 旧局涨到 1592 条；73 命中同一判据（905 条） |
| `36-hero-rejudge-cycle` | **重新选种** 169 → 167（1228848 → 437144） | 旧局涨到 2114 条；167 命中同一判据（853 条） |
| `37-hero-lord-request` | **重新选种** 94 → 209（1430299 → 625069） | 94 在新行为下**不再命中**判据（势力求助窗口没开）；209 命中同一判据（948 条） |

- **面**：重录面 = `tests/replays/*.jsonl` 共 60 条；变 9 条、其余 51 条逐字节不变
  （`git status --porcelain tests/replays/` 只报这 9 条）。`goals.py` **一字未改**。
- **体积**：60 条合计 **50,610,471 字节 ≈ 48.27 MiB**（上限 52 MiB），最大单文件
  **3,334,043 字节 ≈ 3.18 MiB**（上限 4 MiB）⇒ **不动预算**；冻结表
  （`tests/test_replays.py:STANDARD_108_REPLAY_BYTES`）与 `tests/replays/README.md`
  的「重录登记」一节同批更新（索引表由 `python -m replays.readme_index --write` 重生成，
  结构与人数未变 ⇒ 表本身无 diff）。
- **`check_goldens.sh`**：重录**前** PASS（文件仍是旧字节，冻结表自然也匹配；"真重跑引擎"的红
  由 `test_golden_replay_matches_byte_for_byte` 的全部 9 条给出）→ 重录后冻结表红 → 更新
  冻结表 → **重跑 PASS：47 条标准 108 金录像逐字节不变**。
  `bash tools/check_goldens.sh <base-ref>`（判"相对 base 无既有 .jsonl 被改"）**本票必然红**：
  那 9 条就是被授权的重录；门禁只跑不带 base-ref 的那一档（`tests/test_goldens.sh` 亦然）。

### 刻意选择的读法 / 留给后续票

1. **"进下一档"由引擎而不是窗口驱动**：窗口（`_dodge_window` / 决斗 / 无懈…）仍只认"我收到的
   那一条应答"，故最后一档 `Pass` = "这次需要牌不满足"的既有分支一行未改；新增的分面只在
   `legal_actions_for` + `search` 一处。`next_need_card_tier` 与 `legal_actions_for` 读**同一个**
   `_actions_for`，不可能出现"说还有下一档但那一问是空面"（`17` 的实测：第二张没有牌时窗口直接收尾，
   不发 Pass-only 的空询问）。
2. **激活结算的 `Pass` 与票 01 的话术**：票 01 的"`Pass` = 结束本座位的这个时机"被本票收窄为
   "**本档**不发动"；最后一档、以及锁定侧的顺序面仍是旧语义。账本那一行的措辞（`开发文档/06` §5）
   留给票 06 改；本票只改它引用的测试名（否则 `test_writeback_landing` 红）。
3. **`Q16` 的程序化约定账本行**：留在票 06（本票只在 `SkillDef.need_card_tier` / `need_card_tier`
   的 docstring 里写明口径）。
4. **AI 适配**：`docs/agents/ai-adaptation.md` §1 逐条"不适用"——没有新增 `QueryKind`、没有新增
   AI 要自建的候选面、`_AI_READ_POINTS` 的字段集不变（分面只改**每问给哪些已存在字段**的值）；
   `tests/test_ai_adaptation.py` 未动且绿。
5. **`seed_search --verify` 的一条既有噪声**：它对 `29-serpent-spear-virtual-slash` 报"不再命中"
   ——该条是**脚本驱动**，`verify_recorded` 用随机驱动重跑，**HEAD 上同样报**（已在基线 worktree
   实测），与本票无关、也不在门禁里。
6. **票 05 需要知道的**：无懈窗口的"只问合格者"建在 `should_ask_respond` / `respond_candidates`
   的预览上，而预览现在读的是**当前这一档**的面（`legal_actions_for` 分面后）——"合格者"判据
   （除 `Pass` 外还有候选）会因此按档成立：一个只有低档提供者的座位在**它的那一档**会被问，
   在更高档的面里不会被重复问；若票 05 想"整扇窗口一次问完"，要显式跨档取并集（预览可以连续
   调 `next_need_card_tier`），不要假设一次预览就覆盖全部档。另外 `reset_need_card_event` 是
   floor + `providers_used` 的唯一复位点，05 接窗口时不许在别处再清这两个键。

### 门禁结果（本机实测）

- `.venv/bin/pytest -q tests/test_timing.py tests/test_hero_response_window.py` → **75 passed**
- `.venv/bin/pytest -q tests/test_heuristic_ai.py tests/test_all_ai_games.py tests/test_ask_points.py` → **194 passed**
- `.venv/bin/pytest -q tests/test_replays.py tests/test_seed_search.py tests/test_writeback_landing.py` → **135 passed**
- `bash tools/check_goldens.sh` → **PASS：47 条标准 108 金录像逐字节不变**（重录 + 冻结表更新后）
- `.venv/bin/pytest -q tests/test_ai_adaptation.py` → 18 passed；`bash tests/test_dev_docs.sh` → PASS
- 全量门禁 = 本提交的 pre-commit hook（`tests/run_all.sh --quiet`）绿。
