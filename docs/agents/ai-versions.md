# AI 版本台账（AI versions）

**何时读**：给启发式打 tag、跑 `quick` / `promote` / `seal`、或要回答"这一版比上一版强吗、
凭什么"的时候。本文件是**版本台账**——每条 tag 的提交、启发式字节、种子批、结果 JSON 路径、
一句话理由与人类裁定。判据住 `.scratch/ai-light/spec.md`（§2.3 / §2.5 / §2.6），实机结论住
`.scratch/ai-light/acceptance.md`（本文件只放指针）。

工具在 `tools/ai_eval/`（**不进 `src/`**，与 `tools/sgs_batch/` 同层）。统一调用方式：

```bash
PYTHONPATH=tools .venv/bin/python -m ai_eval <quick|promote|seal|live> ...
```

`PYTHONPATH=tools` **不可省**：`.venv` 是 editable 只指 `src/`，漏写会
`No module named ai_eval`——响亮失败，不是静默打错版本（spec §2.9）。

## 1 · 三档评估命令与实机读数

| 档 | 命令 | 内容 | 成本 |
|---|---|---|---|
| 快检 | `quick --baseline ai-vN [--deals 128]` | commit 派生批上的**决策 diff**（不看胜负） | ≈2 min |
| 晋级 | `promote --baseline ai-vN --anchor ai-v0 --deals 768 --workers 8` | 换座配对胜率差 + 决策 diff + 分身份 + 五条件字段 | ≈26 min CPU |
| 封印 | `seal --batch sealed-1 --versions ai-v0 ai-vN --workers 8` | **1024 副写死**批（CLI 不暴露副数），**只出聚合**、不打印逐种子 | ≈17 min |
| 实机 | `live --run <stem> [--runs-dir runs]` | 一局 journal + meta 的两行读数（§2.11 两个廉价指标 + 人类超时）；**不判 verdict、不打胜率** | ≈1 s |

```bash
# 快检：改完立刻（改的是**提交**里的代码；工作树与提交不一致会打一行警告）
PYTHONPATH=tools .venv/bin/python -m ai_eval quick --baseline ai-v0 --out runs/quick-<sha>.json

# 晋级：一个批次 / 一个里程碑
PYTHONPATH=tools .venv/bin/python -m ai_eval promote --baseline ai-v0 --anchor ai-v0 \
    --deals 768 --workers 8 --out runs/ai-eval-<sha>.json

# 封印：里程碑一次性
PYTHONPATH=tools .venv/bin/python -m ai_eval seal --batch sealed-1 --versions ai-v0 ai-v1 --workers 8

# 实机：终局后读最新一局（配方见 docs/agents/acceptance-ai.md）
PYTHONPATH=tools .venv/bin/python -m ai_eval live --run <stem> --runs-dir runs
```

- `live` 只读一局录像（`<stem>.jsonl` + `<stem>.meta.json`），输出是**两行摘要 + 空报账表**，
  不写 JSON、不判 verdict、不打胜率、不打逐种子；缺文件时点名完整路径失败。

- **候选 = 提交**：`--candidate-ref`（缺省 `HEAD`）解析出的完整 sha 既派生种子批、也
  `git show` 出被评估的启发式字节。工作树与提交不一致时 CLI 在 stderr 警告一行，但**照样评估
  提交字节**——"判决批随提交而换"（spec §2.4 A）靠这条成立。
- `--out` 省略时 JSON 写 stdout；摘要行恒走 stderr。退出码：0 = 完成且无错；1 = 有对局
  报错 / worker 崩溃；2 = 参数、规格或版本加载错误。
- `--workers 1..8`（默认 1，不做 auto）；`--predicted-kind <KIND>` 可重复，给出后
  `verdict.diff_attributed` 才有值（见 §4）。
- **真实批量不进** `bash tests/run_all.sh`：128 / 768 / 1024 副只是人工 / 受管后台任务。
  门禁里跑的是 2–4 副极小批的 JSON / 退出码 / 装置对照（`tests/test_ai_eval.py`），不新增
  统计断言；`seal` 的副数在 CLI 侧写死 1024，测试靠库参数缩小批。

## 2 · 结果 JSON（`sgs.ai_eval.v1`）

顶层键：`schema` / `tier` / `candidate` / `baseline` / `anchor` / `engine_ref` /
`deck_profile` / `players` / `mode` / `seed_batch` / `design` / `vs_baseline` / `vs_anchor` /
`decision_diff` / `cases` / `miss_rate` / `verdict` / `errors`；`seal` 另加 `versions`。

- `candidate` / `baseline` / `anchor` = `{ref, heuristic_sha256}`；`candidate.ref` 是**解析后的
  提交 sha**（种子批的派生输入就是它），基线 / 原点用 tag 名。
- `seed_batch` = `{kind, deals, sha256, identity_allocation}`（封印批多 `batch`）：
  `kind` ∈ `commit_derived` / `sealed`；`sha256` = 排序后逐行 `"<seed>\n"` 的 SHA-256。
- `design` = `{duplicate_play, per_seat_streams}`：`duplicate_play` 表示逐副换座重打
  （A 局候选坐 0 号、B 局基线坐 0 号、其余四席同一个对手）；`seal` 是自身四副本，故为
  `false`。
- `vs_baseline` = `{win_rate_candidate, win_rate_baseline, diff, ci95, by_identity}`；
  `vs_anchor` = `{diff, ci95}`。`quick` 不测胜负 ⇒ 两个字段都是 `null`。**一副有效配对都
  没有**（对局全报错 / 没终局）时也写 `null`（不是全零）——未测 ≠ 通过，`non_inferior` /
  `anchor_non_inferior` 随之为 `null`。
- `decision_diff` = `{deals_differing, first_divergence_by_kind, examples}`：A / B 两臂按决策
  序号逐条比，第一个不同步的**询问类型**就是这次 diff 的归因；A/A（两侧同版本）恒为空。
- `cases` = `{total, passed, failed}`：`tests/test_ai_cases.py` 的 D 类用例。模块不存在 ⇒
  全 0 且 `verdict.cases_green = null`（未测 ≠ 通过）；strict xfail 既不算 passed 也不算
  failed，故 `total > passed + len(failed)` 时 `cases_green` 为 false。
- `miss_rate` = `{pass_with_options, unfilled_composite}`，口径见 `tools/ai_eval/misses.py`
  （票 15 的 `live` 复用同一份实现，包括**按询问类型分**的 `by_kind` 明细）。`pass_with_options`
  的分母 = 含非 Pass 选项的 AI 询问，分子 = 其中答了 `Pass` 的；`EndPhase` / 弃牌不是 `Pass`，
  只剩 `Pass` 可答的窗口不在分母。
  批量档只有决策侧车投影（`kind` / `legal_count` / `action`）⇒ `unfilled_composite` 写
  `null`（"未测"）；分母为 0 的口径也写 `null`，不写 0.0。
- `errors` = 对局报错数（聚合；封印批不点名）。`errors > 0` ⇒ CLI 退出码 1。
- `quick` 也出 `decision_diff`；给了 `--predicted-kind` 时同样填 `diff_attributed`，但不测
  胜负（`vs_baseline` / `vs_anchor` / `non_inferior` / `anchor_non_inferior` 恒 `null`）。
- `seal` 的逐版本读数在 `versions[]`（`ref` / `commit` / `heuristic_sha256` / `deals` /
  `win_rate` / `ci95` / `by_identity` / `miss_rate`）；顶层 `miss_rate` 是各版本合并后的聚合，
  顶层的比较与判定字段恒 `null`。
- `--no-cases` 不跑用例：`cases` 记 0/0/`[]`，`cases_green` 记 `null`（未测 ≠ 通过）。

## 3 · 种子带（一律登记，混用即失去判决力）

| 带 | 范围 | 用途 |
|---|---|---|
| 趋势带 | `104..231`（旧 128 局）、`4000..4127`、`5000..5127`（新验收）、历史 `1000..1511` / `1000..1319` / `2000..2319` / `3000..3211` | 只作诊断读数；**不得**作调参 / 判决批 |
| 实机带 | `7001–7099`（轮换：首版 7001，下一版 7002…） | 人机对局验收专用；派生批**主动跳过**。配方见 `docs/agents/acceptance-ai.md`，读数用 `ai_eval live`，结论写 `.scratch/ai-light/acceptance.md` |
| 判决批 | commit 派生，任意 64 位，批内间距 ≥ 97 | `quick` / `promote`；同一轮所有参战版本跑同一批 |
| 封印批 | 名字派生（`sealed-1`…），1024 副写死 | `seal`；只出聚合、用一次退役 |

判决批由 `sha256(candidate_commit)` 经 §2.7 的 LCG 派生，再按座位 0 的身份凑 **1:1:2:1**
（最大余数法：768 副 ⇒ 154 / 154 / 307 / 153）。派生规则在
`tools/ai_eval/seeds.py`，金值在 `tests/test_ai_eval.py`。

## 4 · 晋级五条件（`verdict`）

| 字段 | 含义 | 判据 |
|---|---|---|
| `cases_green` | D 类用例全绿 | `tests/test_ai_cases.py` 无 failed、无 strict xfail；模块缺失 ⇒ `null` |
| `non_inferior` | 对父版本没变差 | `vs_baseline.ci95[0] ≥ −δ` |
| `anchor_non_inferior` | 对原点基线没变差（防梯子漂移） | `vs_anchor.ci95[0] ≥ −δ` |
| `diff_attributed` | diff 落点 ⊆ 预注册预测 | 给出 `--predicted-kind` 时判 `first_divergence_by_kind ⊆ 预测`；未给 ⇒ `null` |
| `human_decision` | 人类裁定 | 工具恒写 `null`，由人填进台账 |

δ = 该 N 下的噪声底（spec §2.3 / §2.5）：`1.96 · sqrt(2 · 0.25 · 0.408 / N)`，
N = 768 ⇒ **0.0319**（约 3pp）、N = 128 ⇒ 0.078。绝对胜率只打印、不断言，并按身份分报。

## 5 · 版本加载契约（spec §2.6）

旧版**不 vendored**：`git show <tag>:src/sgs/agents/heuristic.py` → 落到 gitignored 的
`runs/ai-versions/<tag>.py` → `importlib` 以独立模块名（`_sgs_ai_version_*`）加载。三条硬断言：

1. 缺 tag / `git show` 失败 ⇒ 点名 ref 的错误，**没有**回退 HEAD 的分支；
2. 副本逐字节等于 `git show` 读到的字节（写完读回再比）；
3. `__file__` 指向 `runs/ai-versions/` 的副本，不指向主树（`.venv` 是 editable，`.pth` 会
   让"以为在打旧版"静默成立）。

冻结基线必须**仍可加载**：`sgs.effects` / `sgs.state` 的公开面若改，旧版加载失败即契约违约。

## 6 · 台账

一行一版，**只增不删**。字段：`tag` / `commit` / `engine_ref` / `heuristic_sha256` /
`种子批 sha256` / `deals` / `结果 JSON` / `一句话理由` / `人类裁定`。

| tag | commit | engine_ref | heuristic_sha256 | 种子批 sha256 | deals | 结果 JSON | 一句话理由 | 人类裁定 |
|---|---|---|---|---|---|---|---|---|
| `ai-v0` | `ae7da0ad72d75c2cad96663a8e78f6fae3921439` | `ae7da0ad72d75c2cad96663a8e78f6fae3921439` | `09a56098ffe98701c8499ee1db60e85cf13b6400ada1d9a894b7a12b231c5bd2` | `93ada125f14cfc3bbe41663363eee3b1f3db59f6da4be2e3d2033f42686611cb` | 128（commit 派生） | —（原点，未跑批） | 原点：规则工程开工前的 HEAD（票 11 批量 + 票 12 座位子流已落地，AI 语义未动） | —（原点不裁定） |
| `ai-v1` | `65609d40387e9b54c83ff14aa998d6e9d16cafb0` | `65609d40387e9b54c83ff14aa998d6e9d16cafb0` | `90ad861f387be9399124bb01d2a21b3f255c5abb687a77c80cda5f2aad0dca7a` | `29fb76e9d330b4c3a55915e6fec4f44c49e3a3eba378299fa6164d64b327793e` | 768（commit 派生） | `runs/ai-eval-65609d4-capped.json` | D1 无懈目标化（票 18）：`NULLIFY` 按"被取消的牌对目标利 / 害 × 已明关系"宣 / 不宣（嵌套与未登记牌名 `Pass`）。`quick` 59/128 全落 `RESPOND`；`promote` diff 311/768 全落 `RESPOND`，对父版本 `+0.0026`、CI `[-0.0169, 0.0235]`，对 `ai-v0` 同值 | **人类裁定通过**（agent 只记读数；`cases_green=false` 与 767/768 两条见下，不替人接受） |
| `ai-v2` | `c01cbf25327201de300034280dc95be88d779c5c` | `c01cbf25327201de300034280dc95be88d779c5c` | `6d200c6803dfbcfe71aa5b055af182f3106df05f69e90c37906e6962e243f49d` | `f294c836a49a27747736b867aefff73e031c7cda10eaf749a72394c744fc7eaa` | 768（commit 派生） | `runs/ai-eval-c01cbf2.json` | D2 斩杀优先（票 19）：`_score_attack` 加"这一下就打死"（`_damage_to >= 目标体力`）的斩杀项，与【酒】的 `_wine_enables_kill` 共用 `_lethal_to` 一条判据；藤甲把普通【杀】挡成 0 时不算，白银狮子只把伤害封在 1（够不够仍看体力）。`quick` 38/128（37 落 `PLAY_PHASE`）；`promote` diff 192/768（183 `PLAY_PHASE` + 9 条下游平分抽签漂移），对父版本 `-0.0065`、CI `[-0.0221, 0.0091]`，对 `ai-v0` `-0.0013`、CI `[-0.0247, 0.0221]`，`errors=0`；`quick` JSON `runs/quick-c01cbf2.json`。**实机 seed=7002**（HEAD `5f2d329`，行为 = 本版）：4 项必验通过（能开局 / AI 应答 / 无非法 / 到终局），**D2 局面未遇上**（按用户裁定不算失败），读数见 `.scratch/ai-light/acceptance.md` §四 | **人类裁定通过（2026-10-30）**：① `cases_green=false` 按"本项 D 用例全绿"解释、② ③ 成立、④ 10 条下游平分抽签漂移按书面说明接受、⑤ 台账理由齐全；缓存下限重取与 tag 先建一并接受（逐条见下） |
| `ai-v3` | `e2e4753464147d1bed51730236b7969c8627eedb` | `e2e4753464147d1bed51730236b7969c8627eedb` | `7768049d4b127f0da63f89349762f15dfc60015e029dd5cafa24312ca439cef4` | `e3ff03a0bfacc715b76de6735a785c578e6f019f108fc9b90342392aa2b5b1ce` | 768（commit 派生） | `runs/ai-eval-e2e4753.json` | C1 记牌 P(闪)（票 20）：`_score_attack` 的「0.25 × 手牌数（封顶 1.0）」换成「未知池超几何 P(目标有闪) 导出的期望伤害 + 躲掉也消耗一张【闪】的收益（1.0 分/张）」；档位靠守恒量反推、对不上就关闭记牌回落到旧公式。`quick` 45/128（43 `PLAY_PHASE` + 2 条下游平分抽签漂移）；`promote` diff 283/768（277 `PLAY_PHASE` + 6 条下游漂移），对父版本 `+0.0078`、CI `[-0.0104, 0.0273]`，对 `ai-v0` `+0.013`、CI `[-0.0104, 0.0352]`，`errors=0`；`quick` JSON `runs/quick-e2e4753.json`。**实机 seed=7003**（4 项必验通过；新增 2 条 D 报账——忠臣【决斗】主公后自无懈，进 v2 候选，见 `acceptance.md` §五）。**预注册「中后期更明显」被实测否定**（效果跟手牌厚度走、峰值在早期；见票 20 的 Answer），两处读数偏差（武将牌上的牌 / D2 的一致漂移）也留给人类裁定 | **人类裁定通过（2026-10-30）**：① ② ③ ④ 全部接受（①–③ 用户原话「1~3签字我都确认没问题」；④ 用户原话「确认通过，截至本票没有问题，因为以后票 27 会完善的」——武将牌读取面由票 27 补，D2 一致漂移由反向对齐测试兜） |
| `ai-v4` | `d00badb2e7e853f91b8bd32ad0147cbe0602ed16` | `d00badb2e7e853f91b8bd32ad0147cbe0602ed16` | `e85629b76c2cbb5f0b3596bdd09b304d18958e25a02cefe1baa6d06ec5172b4b` | `2ef8d79409584b82b632222dc246772e0aeaa52bcbba81a0cf51fd7ab3b4042e` | 768（commit 派生） | `runs/ai-eval-d00badb.json` | I1 身份保守档（票 21）：敌友从"主公 ↔ 反贼"的 0/1 换成**配比均匀后验**——已亮明身份（本人 / 主公 / 已阵亡 / `identity_visible`）从启发式内的 4~8 人配比字面表里减掉，存活未知座位按人头均分得 `(P(敌), P(友))`，替换约 8 个用点的 0/1（伤害目标 / 火攻 / 座位步三类 / 铁索四条分支 / 无懈宣判 / 濒死救援）；算不出就退回中性的 (0, 0)。内奸语义保留（主公对内奸恒 (0, 0)）。`quick` 61/128（34 `PLAY_PHASE` + 10 `PEACH` + 9 `RESPOND` + 7 `SELECT_OPTION` + 1 条下游平分抽签漂移）；`promote` diff 352/768（216 `PLAY_PHASE` + 62 `PEACH` + 47 `RESPOND` + 22 `SELECT_OPTION` + 5 条下游漂移），对父版本 **+0.0365**、CI **[0.0156, 0.0586]**，对 `ai-v0` **+0.0326**、CI **[0.0026, 0.0612]**，`errors=0`；分身份：忠臣 **+0.1104**（0.1948 → 0.3052）、内奸 +0.0588、主公 +0.0065、反贼 +0.0033；`quick` JSON `runs/quick-d00badb.json`。**8 人局 32 局 sanity**：32/32 跑完、0 非法、32/32 可回放；同阵营伤害 198/703（28.2%）vs `ai-v3` 194/640（30.3%），其中**主公 → 忠臣 53 vs 38（上升）**、忠臣 → 主公 17 vs 32（读数见 `acceptance.md` §六）。**实机 seed=7004**（用户驱动，5p / 160 档）：4 项必验全过（能开局 / AI 应答 / 无非法 / 到终局），人类能看出忠臣护主；新增 1 条 D 报账（档 2 出 scope，见 `acceptance.md` §七） | **人类裁定通过（2026-10-30）**：① ② ③ ④ 全部接受（用户原话「四条口径均通过」），实机 4 项必验全过（原话「必验4条没有问题，全部通过」） |
| `ai-v5` | `3b1fdcaa0946e27d5252dc668fc2098212c960e4` | `3b1fdcaa0946e27d5252dc668fc2098212c960e4` | `76df7a59ac75202db491feba39c6c5f65d187f94454ef34a72d1145b308e549a` | `61ebdb9ef9e8bd2ebf68586b6fbeef9b4689560b3cf5a88d42813182fe3172fb` | 768（commit 派生） | `runs/ai-eval-3b1fdca.json` | 武将牌上的牌进 `PlayerView`（票 27）：`PlayerPublic.hero_zone_count`（逐座位公开张数，多区求和）+ `PlayerView.hero_zone_cards`（本人区牌面，宿主可见）——两者 `exclude=True`、记录流 / 47 条金录像逐字节不变；记牌守恒量补 `Σhero_zone_count`、可见面补本人区牌面，【谦逊】放置期间不再关闭（票 20 的偏差 1）。`quick` 0/128；`promote` diff **0/768**（59/768 副出现放置、其中 11 副焦点座位真读到两个新字段——403 条询问 / 19 条含【杀】候选；分值变化没有翻转焦点座位的决策），对父版本 `0.0`、CI `[0.0, 0.0]`，对 `ai-v0` `+0.0482`、CI `[0.0208, 0.0755]`，`errors=0`；`quick` JSON `runs/quick-3b1fdca.json`。本项 D 用例（`tests/test_ai_cases.py` 的两条）全绿；投影 / 守恒单元面另在 `tests/test_queries_views_actions.py` / `tests/test_heuristic_ai.py`；实机 **seed=7006** 4 项必验全过（`.scratch/ai-light/acceptance.md` §九） | **人类裁定通过（2026-10-30）**：①–⑤ 全部签认（用户原话「其它所有需要签认的地方我都签认」）；实机 seed=7006 4 项必验全过（询问=333 / pass=10/133=7.5% / composite=12/17=70.6% / 超时 0），2 条报账——seat2 忠臣对 seat1 主公【兵粮寸断】（v2 #7 既有证据）与借刀次数跨回合（新引擎 bug 票 `w5-borrowed-sword/04`），均不阻塞本版 |
| `ai-v6` | `f6c677ff54759164337624255a8da13748dc4e45` | `f6c677ff54759164337624255a8da13748dc4e45` | `576914b62f75a2deb84e5dc0d4f0629c5a398e409643721191b5ee44a7c1e7d4` | `68eebd40d6b8752c400156e973e4d13c3e209f9ff8edabe93f323c87dd80477a` | 768（commit 派生） | `runs/ai-eval-f6c677f.json` | S1 技能表 top3（票 22）：据守 / 贯石斧 / 谦逊三条常量换成「读得出代价」的打分器——据守已翻面 ⇒ 发动（翻回正面、不跳回合）、正面 1 体力 ⇒ Pass；贯石斧**可能的斩杀 ⇒ 恒发动**（击杀不扣牌价），否则 1 点非致命伤害 − 两张最便宜牌的价；谦逊 1 体力且手里有续命牌（闪 / 桃 / 酒）⇒ Pass。`quick` 2/128 全落 `SKILL_ACTIVATION`（2 据守，`results/t22-attrib-quick.txt`：0 条越界）；`promote` diff 20/768 全落 `SKILL_ACTIVATION` （9 斧子 / 9 据守 / 2 谦逊，`results/t22-attrib-promote.txt`：0 条越界），对父版本 `−0.0013`、CI `[-0.0052, 0.0026]`，对 `ai-v0` `+0.0208`、CI `[-0.0078, 0.0482]`，`errors=0`；`quick` JSON `runs/quick-f6c677f.json`。spec 附录 A 的 71 条逐行盘点已回填（68 条保持常量 / 3 条本票修）。**实机 seed=7032**（2026-10-30 用户驱动，5p / 160 档：4 项必验全过；**谦逊 1 体力 + 【闪】**的 Pass 在真局里看到，【贯石斧】那格本局没遇到、按用户裁定不算失败；读数 `询问=393 / pass=16/158(10.1%) / composite=13/15(86.7%) / 超时 0`，`acceptance.md` §十） | **人类裁定通过（2026-10-30）**：7 条口径全部签认（用户原话「7 条口径我全部签任，没有问题」），4 项必验全过（原话「必验4项全部通过没有问题」）；两条实机报账按归口处理、不阻塞本版（① 忠臣对主公出【杀】= spec v2 候选第 7 条的**已知 AI 蠢点**，本局证据 3 次都是「主公是唯一合法【杀】目标」；② 主公杀忠臣后弃置不触发【连营】= 引擎缺口，另开 bug 票 `m2-standard-identity/33`） |

| `ai-v7` | `b098a7732cbbcbc546d78127484fe8485b3f7586` | `b098a7732cbbcbc546d78127484fe8485b3f7586` | `9a45c204d25e9a2ce18c4fe7ed587ad972f713b02469427623d40fb72084187b` | `45eee43b44664440af0b8b7a86465ed41893981b06226f8c86b02c5555d40db2` | 768（commit 派生） | `runs/ai-eval-b098a77.json` | C2 记牌批 2（票 23）：`_card_value(None)` 与四处「未知牌 × 1.0」（苦肉 / 界苦肉 / 克己 / 势斩）换成**未知池期望牌价**（档位表逐项加权、装备按槽位价；空池 ⇒ 0.0，记牌关闭 ⇒ 1.0）；单目标、当回合结算的锦囊按**目标手里有【无懈可击】的概率**折成期望（拆 / 顺 / 决斗 / 借刀 / 火攻 / 铁索；零 / 负收益不打折）；斩杀分按 **P(救回)** 折成期望（濒死者自救【桃】/【酒】＋ 它那一侧的【桃】× 关系权重 P(敌)/P(友)）。频率读数（128 副、焦点座位）：单目标锦囊 320 次里 **223 次被折价**（p 中位数 0.071、max 0.413）、斩杀目标 131 个里 **108 个「救得回来」**（p 中位数 0.184、max 0.709）、选牌步 575 次里 **251 次指到无牌面槽**（期望牌价 3.11 而不是 1.0）。`quick` 84/128（52 `PLAY_PHASE` + 30 `SELECT_CARDS` + 1 `DODGE` + 1 `RESPOND`；2 条越界均为下游漂移）；`promote` diff 504/768（341 `PLAY_PHASE` + 145 `SELECT_CARDS` + 5 `SELECT_OPTION` + 4 `DODGE` + 3 `ARRANGE` + 2 `RESPOND` + 2 `PEACH` + 1 `DISCARD` + 1 `SKILL_ACTIVATION`），对父版本 `+0.0078`、CI `[-0.0234, 0.0378]`，对 `ai-v0` `+0.0195`、CI `[-0.0104, 0.0495]`，`errors=0`；分身份：主公 0.0、忠臣 +0.0195、反贼 +0.0033、内奸 +0.0131；**160 档另跑 32 副** diff 19/32（13 `PLAY_PHASE` + 5 `SELECT_CARDS` + 1 `SELECT_OPTION`，`errors=0`）；`quick` JSON `runs/quick-b098a77.json`。**未实机**（批末一局待用户驱动，见下第 8 条） | **人类裁定通过（2026-10-30）**：10 条口径全部签认（用户原话「10条口径全部验收」）+ 实机 4 项必验全过（「必验4项没有问题，全部通过」「实机感受没有问题」）；实机读数 `pass=3/149(2.0%) / composite=24/36(66.7%) / 超时 0`，两条报账（铁索 v2 #10 / 恂恂排列 UI 另开票）均不阻塞本版 |

- `ai-v0` 是**原点基线**（spec §2.6）：`promote --anchor ai-v0` 用它防梯子漂移。
- 上表的**种子批**列 = **该版评估所用的批**：`ai-v0` 行是 `derive_batch(ai-v0 commit, 128)`
  的**参考摘要**（`quick` 批大小，没跑过）；`ai-v1` 起每一行是各自 `promote` 实际跑的 768 副
  commit 派生批（`derive_batch(<该版 commit>, 768)`）。各行不可直接比大小，比的是"这一版的
  读数来自哪一批"。
- 后续版本在跑完 `quick` → `promote` → 实机（可选）后追加一行；被接受的 tag 必须能被版本
  加载器加载。实机结论只放指针：`.scratch/ai-light/acceptance.md`。
- **`ai-v1` 的两条口径说明**（读 JSON 前先看这两条；它们也是 §2.3 五条件里两条的**待裁定项**）：
  1. `cases` = **8/9**：工具口径要求"模块里无 failed、无 strict xfail"（§2.3 ① / §2.8），
     而 `tests/test_ai_cases.py` 里仍有 D3【遗计】的 `xfail(strict=True)`（票 24，本票未
     开工）⇒ `verdict.cases_green` 记 `false`。D1 自己的 6 条用例全绿；"本项 D 用例全绿"
     是否算满足 ① 由**人类裁定**，台账不代填。
  2. 结果 JSON 由**带应答上限的同一套库**跑出（`.scratch/ai-light/promote_capped_t18.py`；
     该上限**不写进 JSON**——JSON 只有 `errors=4`）：768 副里恰有 1 颗种子
     （`5667218070898781`）命中一个**既有**的界孙尚香【结姻】空转（`ai-v0` 下同样复现），
     不设上限时 stock 的 `python -m ai_eval promote --deals 768` 永远等这一副。上限触发后
     按工具既有口径记成"该副对局错误、整对退出配对" ⇒ `errors = 4`、**767 对有效配对**
     （768 − 1）。被接受前请按"767/768"读 `non_inferior` / `anchor_non_inferior`。

【人类裁定结果：满足】
- `ai-v1` 的 tag 是为**版本留存**先建的（之后所有票的父版本都能被版本加载器复现）；它不是
  人类签认。人类裁定为"拒绝"时按停手线回退（tag 只增不删，台账追加拒绝理由）。
- **`ai-v2` 的三条口径说明**（**人类裁定通过（2026-10-30）**：三条都按下面的读法接受，记录如下）：
  1. `cases` = **13/14**：同样红在票 24 的 D3【遗计】`xfail(strict=True)`（本票未开工），
     不是 D2；D2 自己的 4 条用例全绿。工具口径（模块里无 xfail）⇒ `cases_green=false`。
     **人类裁定**：按"本项 D 用例全绿"解释 ①，即满足。
  2. `diff_attributed = false`：`quick` 38/128 里 37 条落 `PLAY_PHASE`、1 条落
     `SELECT_CARDS`；`promote` 192/768 里 183 条落 `PLAY_PHASE`、9 条落
     `SELECT_CARDS` / `DISCARD` / `ARRANGE` / `DODGE` / `PEACH` / `RESPOND`。**书面说明（spec
     §2.3）**：这 10 条都是**下游平分抽签漂移**——D2 只改 `_score_attack` 与
     `_wine_enables_kill`（都只在 `PLAY_PHASE` 用），但某个 `PLAY_PHASE` 应答的"平分候选数"
     从 n 变成 1 时，`randbelow(1)` **不取随机数**而 `randbelow(n>1)` 会取（`RandomSource.randbelow`
     的 `bits == 0` 早退）⇒ 该座位后续的平分抽签整体错位；分叉点的动作都是**同一打分器里的同分
     候选**（如两张【杀】/两张【闪】/两个摸闪技）。探针证据：`.scratch/ai-light/probe19_tie2.py`
     （`SELECT_CARDS` 那副：第 88 次 `randbelow` 上界 2 vs 4、第 97 次 1 vs 2、第 115 次
     同界不同值）与 `probe19_promote.py`（9 条逐条列出）。**人类裁定**：按书面说明接受 ④。
  3. 本版还**重取了缓存命中率下限**（`tests/test_engine_cache.py` ⑤，12832/12961 →
     12686/12818，即 0.99005 → 0.98970）：⑤ 读的是真实 AI 对局，命中率随行为面漂；缓存
     实现与键函数未改（键覆盖 ①–④ 绿、miss 129 → 132），判据形态不变（实测值、零余量、
     少 1 命中即红）。**数值确实变低了 0.035 pp**——这是本票唯一一条"数值变差"的判据改动，
     理由与影响面写进票 19 的 `## Answer`。**人类裁定**：接受这次按新行为面的重取。
- `ai-v2` 的 `promote` 是 **stock CLI** 跑的（`errors=0`、768/768 有效配对）：票 18 的
  767/768 那颗【结姻】空转种子已由 `.scratch/jieyin-seat-pool/issues/01-jieyin-seat-pool.md`
  （commit `d2de162`）修掉，本批不再需要应答上限。
- 票 19 的**评审修订**只改了两轴评审点出的注释口径（白银狮子的伤害说法）、把两个 6.5 绑成
  一个名字（`_LETHAL_STRIKE_VALUE = _WINE_LETHAL_VALUE`，行为仍是 6.5）并补了两条测试
  （② 的后半步、0 手牌口径的护栏）：**行为不变**，`ai-v2` 的 heuristic 字节仍按 `c01cbf2`
  读（评估的就是那版）；再往后的票不要把 HEAD 的 sha 与台账行混着读。

【人类裁定结果：满足（ai-v2，2026-10-30）】
- 用户原话「三条我都接受，通过」——点名的三条 = ① `cases_green` 按"本项 D 用例全绿"解释 /
  ④ 10 条下游漂移按书面说明接受 / ③ 缓存下限按新行为面重取。另两条随"通过"一并关闭：
  **tag 先建**（保留、不回退；tag 只增不删）与**"目标 0 手牌"不做硬门**（按 spec §6.1 D2
  的"改什么"实现，第 5 条用例钉住；将来要改硬门另开修正）。
- 结论指针：`.scratch/ai-light/acceptance.md` §四（实机 seed=7002，4 项必验通过、D2 局面未
  遇上）+ `runs/ai-eval-c01cbf2.json`（五条件读数）。
- 封印批名字与摘要也记在这张表下方的说明里（`sealed-1` = `0d9cc40a450f208ddf0711555279104b72498b6581d97d4e6ca0847d324d5127`，1024 副）。

**`ai-v3` 的口径说明（agent 只记读数；①–④ 已由人类签认，见文末裁定块）**：
1. `cases` = **17/18**：红在票 24 的 D3【遗计】`xfail(strict=True)`（本票未开工），不是 C1；
   C1 自己的 2 条 D 用例全绿。工具口径（模块里无 xfail）⇒ `cases_green=false`。与 `ai-v1` /
   `ai-v2` 同一条待裁定项：**按"本项 D 用例全绿"解释五条件①**是否满足，由人类签认。
2. `diff_attributed = false`：`quick` 45/128 里 43 条落 `PLAY_PHASE`、2 条落 `DODGE` /
   `RESPOND`；`promote` 283/768 里 277 条落 `PLAY_PHASE`、6 条落 `SELECT_CARDS` ×2 /
   `SELECT_OPTION` ×2 / `DODGE` / `RESPOND`。**书面说明（spec §2.3）**：这 8 条都是**下游平分
   抽签漂移**——首分叉之前该座位有一个 `PLAY_PHASE` 的平分候选数从 2 变成 1，
   `RandomSource.randbelow(1)` 不取随机数、`randbelow(n>1)` 会取 ⇒ 后续平分抽签整体错位；
   分叉点的动作都是同一打分器里的同价候选（两张同价【杀】/【闪】/响应牌、同一选项面）。探针：
   `.scratch/ai-light/probe20_promote.py`（逐副重算，6 条种子全列）与
   `.scratch/ai-light/probe20_attrib.py`（逐种子核出首分叉前的平分候选数变化）。
3. **预注册「中后期更明显」不成立**：`.scratch/ai-light/probe20_where.py` 对候选侧 128 副的
   每个焦点 `PLAY_PHASE` 同算新 / 旧最高分集合：2123 个决策里 60 个（2.8%）变了偏好；按手牌数
   0–1 张 0%、2–4 张 1%、5 张 5%、6 张 15%、7 张 16%、8 张 18%；按可见【闪】占比 <20% 6%、
   20–40% / 40–60% / ≥60% 各 2%；首分叉 `deck>=70` 33/45。**效果跟手牌厚度走、峰值在早期**，
   不是 spec §6.3 预期的"中后期牌族耗尽 / 手牌变薄"。两种收口（保留 / 调
   `_DODGE_CONSUMED_VALUE` 让开局增益≈0）见票 20 的 Answer——后者是照着预注册调参，未做。
4. **武将牌上的牌**：票面"算进未知池"与 spec §6.3 / D2 的"对不上就关闭（不猜）"不可兼得；本版
   按 spec 落地（有牌上武将牌 ⇒ 守恒量少几张 ⇒ 关闭记牌），影响面与改法见票 20 的 Answer。
5. **D2 的"档位表被改动"只覆盖表自身不自洽**：一致地改表（total 与逐项一起改）运行期看不出
   来，由反向对齐测试兜；登记在票 20 的 Answer「偏差 2」。
6. **评审修正（第二轮）**：本版是 `e2e4753`（不是首版 `7d88b73`）。首版把"躲掉也消耗一张牌"
   的收益写成 1.0 **伤害点**，导致 1 点伤害的期望恒为 1、P(闪) 不进普通【杀】；两轴评审都
   点出这一点后改成**分数**单位（`_DODGE_CONSUMED_VALUE = 1.0` 分/张）并重跑 quick / promote。
   台账的 commit / `heuristic_sha256` / 种子批都按 `e2e4753` 读。
7. **tag 先于人类签认**（票 18 / 19 同先例）；`verdict.human_decision` 仍是 `null`，人签在台账。
   被拒绝时按停手线回退（tag 只增不删，台账追加拒绝理由）。
8. 票 20 还改了 `tests/support/web_client.py` 的火攻 tape 种子（68 → 63，判据未放宽）：C1 改了
   AI 出牌打分后 seed 68 的目标不再被别家拆掉【无懈】、自己把人类火攻无懈掉；按同一判据重扫的
   探针是 `.scratch/ai-light/probe20_fire_seed.py`，理由写在该文件的注释里。

9. **实机（批次末一局，seed 7003，用户驱动）**：4 项必验通过、`崩溃=0 非法=0`、
   `人类超时=0`；读数与报账住 `.scratch/ai-light/acceptance.md` §五。**新增 2 条 D 报账**
   （同一手）：忠臣对已明主公出【决斗】，随后在自己那张【决斗】的 NULLIFY 窗口宣【无懈可击】。
   归因：有目标的牌对已明盟友没有负分 + D1 的 `_nullify_declares` 没有"我是使用者"的判据
   （`RespondQuery` 不带 `source`，属读点缺口）。按 spec §6.8 进 **v2 候选第 7 / 8 条**，
   不在本图开票；实机层不否决本票。

【人类裁定结果（ai-v3，2026-10-30）：满足（①–④ 全部通过）】
- 用户原话「1~3签字我都确认没问题」（2026-10-30）。① `cases_green=false` 按"本项 D 用例
  全绿"解释；② 8 条非 `PLAY_PHASE` 首分叉按**下游平分抽签漂移**的书面说明接受；
  ③ 预注册「中后期更明显」被实测否定后**保留本实现**（不按预注册反向调参）。
- **④ 由人类确认通过**（2026-10-30，用户原话「票 20 第④点我确认通过，截至本票没有问题，
  因为以后票 27 会完善的」）：① 武将牌上的牌走 fail-closed（关闭记牌）而不是票面"算进未知池"
  ——**接受现状**，读取面由 **票 27**（`Blocked by: 21`；张数公开 + 本人牌面，见
  `.scratch/ai-light/issues/27-hero-zone-count-in-view.md`）补齐；② D2 的"档位表被改动"
  运行期只覆盖表自身不自洽——**接受**，一致漂移由反向对齐测试兜。
- tag `ai-v3` 已建（版本留存）；结论指针：票 20 的 `## Answer` + `runs/ai-eval-e2e4753.json`
  + `.scratch/ai-light/acceptance.md` §五（实机 seed=7003）。

- **`ai-v3` 的评审收尾**（`src/sgs/agents/heuristic.py` 的 `_score_attack` docstring 里一处旧
  函数名 `_expected_attack_points` → `_expected_attack_score`）：**行为不变**（纯注释），
  `ai-v3` 的 heuristic 字节仍按 `e2e4753` 读（`7768049d…`），版本加载器加载的也是那版；
  再往后的票不要把 HEAD 的 sha 与台账行混着读。

**`ai-v4` 的口径说明（agent 只记读数；四条已由人类签认，见文末裁定块）**：
1. `cases` = **20/21**：红在票 24 的 D3【遗计】`xfail(strict=True)`（本票未开工），不是 I1；
   I1 自己的 2 条 D 用例（`test_i1_a_dead_loyalist_leaves_the_lord_facing_an_all_enemy_field` /
   `test_i1_the_renegade_keeps_the_lord_off_the_priority_list`）全绿。工具口径（模块里无
   xfail）⇒ `cases_green=false`，与 `ai-v1` / `ai-v2` / `ai-v3` 同一条待裁定项。
2. `diff_attributed = false`：`quick` 61/128 里 1 条落 `DISCARD`、`promote` 352/768 里
   2 条 `DISCARD` + 3 条 `SELECT_CARDS` 不在预注册集合（`SELECT_OPTION` / `PLAY_PHASE` /
   `PEACH` / `RESPOND`）。**书面说明（spec §2.3 ④）**：5 条都是**下游平分抽签漂移**——首分叉
   之前该座位有一条询问的"最高分候选数"变了、**选中的动作没变**（`RandomSource.randbelow(1)`
   不取随机数、`randbelow(n>1)` 会取 ⇒ 后续平分抽签整体错位）。逐条（`probe21_promote.py`
   找种子，`probe21_attrib.py` 逐种子核）：

   | seed | 首分叉 | 首分叉**之前**改变的平分候选数（new/old） | 选中的动作（两边相同） |
   |---|---|---|---|
   | `3314977869622878074` | `SELECT_CARDS@10` | `PLAY_PHASE#5` 1/2 | `card_id 55 → 座位 4` |
   | `4514200405074212640` | `SELECT_CARDS@13` | `SELECT_OPTION#7` 2/1、`#9` 1/4 | `seat:0` / `Pass` |
   | `5114203757752737980` | `DISCARD@83` | `PLAY_PHASE#79` 1/2 | `card_id 88 → 座位 3` |
   | `7913102898836559292` | `SELECT_CARDS@13` | `PLAY_PHASE#10` 1/2 | `card_id 83 → 座位 1` |
   | `11949779806123291935` | `DISCARD@105` | `PLAY_PHASE#65` 1/2 | `card_id 43 → 座位 3` |

   两条 `DISCARD` 的分叉动作本身同价：一条都是"弃两张【闪】"、另一条都是"弃一张【闪】"
   （只是**具体哪张**不同）；三条 `SELECT_CARDS` 是同一批同分牌里 `picks` 的位置不同。
3. **缓存命中率下限按新行为面重取**（`tests/test_engine_cache.py` ⑤，同票 19 的口径）：
   `12643 / 12788 = 0.98866`（上一版 `12686 / 12818 = 0.98970`；缓存实现与键函数一字未动，
   键覆盖 ①–④ 全绿）。数值再降 0.104 pp，理由写进该文件的 docstring 与票 21 的 `## Answer`。
4. **8 人局 32 局 sanity（spec §6.4，只作 sanity、不作晋升主判据）**：32/32 跑完、终局与身份
   胜负一致 32/32、零非法应答、32/32 可回放。唯一的负面读数是**误伤结构变了**：
   `lord → loyalist` 53 次（`ai-v3` 38）、`loyalist → lord` 17 次（`ai-v3` 32）、
   `loyalist → loyalist` 34（32）、`rebel → rebel` 94（92）；总同阵营伤害 198/703 = 28.2%
   （`ai-v3` 194/640 = 30.3%）。这正是 spec §6.4 预判的风险（8 人局主公眼里 5/7 是敌人），
   幅度约 **+0.6 次/局**，交人类裁定（读数住 `.scratch/ai-light/acceptance.md` §六，
   探针 `.scratch/ai-light/probe21_8p.py`）。
5. **夹具随行为面重取（判据未放宽）**：火攻 tape 的"对 AI"那一段 seed 63 → **93**（同一份
   固定脚本、同一组判据，重扫 1–259 的首个命中；探针 `probe20_fire_seed.py`，理由写进
   `tests/support/web_client.py` 的注释）；`tests/support/ui_smoke.mjs` 的 U1-05o2 从
   "固定五个颜色类名"改成**从载荷派生**（申报的类都落到 span 上）——原判据把前端烟测绑在
   **这一局出现了谁发动技能**上，I1 换了走向就抖红；类名闭集与生成仍由 `test_web.py` 的
   叙事契约钉住。
6. **tag 先于人类签认**（票 18 / 19 / 20 同先例）；`verdict.human_decision` 仍是 `null`，
   人签在台账。被拒绝时按停手线回退（tag 只增不删，台账追加拒绝理由）。

- **`ai-v4` 的评审收尾**（`src/sgs/agents/heuristic.py` 的**两处 docstring**：
  `_AI_READ_POINTS["PlayerPublic"]["alive"]` 补上"身份后验的分母"这条新读法；
  `_identity_relation` / `_ally_identities` 把"已亮明档"的口径写准——敌人一侧与票 21 之前
  逐项一致、**盟友一侧按阵营宽一档**（旧 `_is_revealed_ally` 只认主公，今天只有
  `identity_visible` 露牌时才走到））：**行为不变**（纯注释），`ai-v4` 的 heuristic 字节仍按
  `d00badb` 读（`e85629b7…`），版本加载器加载的也是那版；再往后的票不要把 HEAD 的 sha 与
  台账行混着读。同一次评审还补了 `test_the_identity_relation_counts_a_revealed_teammate_as_an_ally`
  与真对局的 `skill` 色断言（`tests/`，不进启发式字节）。

**`ai-v6` 的口径说明（agent 只记读数；待人类签认后回填裁定块）**：

1. **`cases_green=false` 只因票 24 的 D3 xfail**（`test_yiji_gives_to_the_revealed_lord_not_self_or_enemy`）：
   与 `ai-v1`–`ai-v5` 同一条待裁定项——按"本项 D 用例全绿"解释五条件①（35/36：唯一 xfail 是票 24；
   本项 3 条先红后绿 + 7 条护栏 + 1 条夹具自检全绿）。
2. **贯石斧的两条判据**：① **斩杀恒发动**（不扣牌价）——票面验收写的是「目标 1 体力 ⇒ 发动」，
   没有给斩杀加成本条件，与 D2 斩杀项同口径；② **可能目标集合**（`_salvage_targets`）=
   存活的非自己座位里 `P(友) < 1` 的那些（**不是已亮明盟友**）：未知 / 中立都算，已亮明盟友不算。
   `SkillActivationQuery` 不带目标，故任一"可能的目标"是 1 体力就按斩杀计价——比"永不发动"
   接近牌面（这一刀已经打出去了）、比"任意局面都发动"保守；不补历史字段（ADR-011 的读点纪律）。
   20 条 promote 首分叉里 9 条是这条（父版本「弃两张发动」→ 候选「Pass」）。
3. **据守「已翻面」格用构造询问**：引擎里唯一的翻面来源就是据守自己，而背面朝上的角色在
   回合开始前必被翻回正面并跳过整个回合（规则集 13.2 / `Engine._turn_before_start`）⇒ 真实对局
   走不到"自己回合的结束阶段还翻着面"。用例用真投影（`project`）+ 真询问模型 + 真打分器构造；
   这是**防御性分支**，留给日后的第二个翻面来源。
4. **谦逊的续命牌闭集含【桃】/【酒】**（票面只点名【闪】）：同一条语义的成员——1 体力时扣上
   武将牌就用不出来；杀 / 无懈不算（救不了 1 体力时的命）。已有用例钉【桃】那一格。
5. **频率列的口径**：spec 附录 A 的 48 局诊断按候选重读（同协议、不同 AI 版本）；票 10 的旧数
   （据守 23 / 贯石斧 5 / 谦逊 10）只作"当时排序"的留档。`0/0` 的读法见附录 A 的口径段
   （候选注入 / 转化 / 改判类没有"是否发动"询问）。
6. **未实机**：按 spec §6.9「实机按批次末」，本票是第二批中间项（I1 → **S1** → C2），故台账
   写"未实机（批末补）"——排程，不是跳过判据。
7. **两轴评审的行为修正（绿提交重做过一次）**：`code-review` 的 Standards / Spec 两轴发现
   3 处行为偏差（斧子斩杀被成本条件收紧、可能目标集合比文档窄、谦逊闭集无测试）与 6 处记账面
   问题；修正后**重做绿提交**（首轮绿提交被替换，最终绿提交 `f6c677f`，`tag ai-v6` 指向它），
   quick / promote / 频率 / 归因全部按新提交重跑。修正清单见票 22 的 `## Answer`
   「两轴评审的处置」。

【人类裁定结果（ai-v6，2026-10-30）：通过（7 条口径全部签认 + 实机 4 项必验全过）】

- 用户原话：「必验4项全部通过没有问题」（能开局 / AI 应答 / 零非法 / 到终局），
  「7 条口径我全部签任，没有问题」——①`cases_green` 的解释、②斧子的「可能目标集合」读法、
  ③击杀不扣牌价、④据守「已翻面」格用构造询问、⑤谦逊续命牌闭集含【桃】/【酒】、
  ⑥频率列按候选重读、⑦未实机（本局补上）——**全部签认**。
- **实机读数**（`ai_eval live --run 7032-5p-20261030-172231 --runs-dir .scratch/ai-light/runs`）：

  ```text
  实机 seed=7032 5p identity 160 人类=seat0(反贼) 终局=反贼胜 获胜者=[0,1]
  询问=393 AI漏动作 pass=16/158(10.1%) composite=13/15(86.7%) 人类超时=0
  ```

- **本票语义的观察**：谦逊（座位 3 界陆逊）在 1 体力且手里有【闪】时 **Pass**、其余情况正常
  发动——与 3 条 D 用例同向；【贯石斧】本局没遇到（座位 1 起始手牌有斧子，但她的【杀】没被
  【闪】抵消过），按用户裁定不算失败。
- **报账 1（已知 AI 蠢点，spec v2 候选第 7 条，不阻塞）**：忠臣（座位 4）对主公（座位 3）
  出【杀】三次（seq 1395 / 2680 / 3005）。三次都是**主公为唯一合法【杀】目标**（座位 0 有
  【的卢】+【赤兔】、座位 1/2 超出范围）；根因是 I1 的既有口径「打盟友没有额外惩罚」。
  正好落在 v2 候选第 7 条已写的 D 谓词上，本局把证据从 seed=7003 扩到 7032。
- **报账 2（引擎缺口，另开 bug 票 `m2-standard-identity/33`，不阻塞）**：主公杀死忠臣后弃置
  所有牌，但主公是界陆逊时**没有触发【连营】**（seq 3245/3246 搬走手牌与装备，之后无
  `lianying` 询问；同一局其余九次"失去最后手牌"都正常发问）。根因：
  `_lord_discards_everything` / `discard_dead_cards` 直接调 `move_card`、不派发 `MOVE_AFTER`
  ——`Engine.move_card` 的 docstring 已登记为票 07 §范围外的**已知缺口**。与票 22 的三条技能
  语义无关，已按实机报账开票。
- 结论指针：票 22 的 `## Answer` + `runs/ai-eval-f6c677f.json` + `.scratch/ai-light/acceptance.md`
  §十（实机读数与两条报账的逐条归因）。

**`ai-v7` 的口径说明（agent 只记读数；待人类签认后回填裁定块）**：

1. **`cases_green=false` 只因票 24 的 D3 xfail**（`test_yiji_gives_to_the_revealed_lord_not_self_or_enemy`）：
   与 `ai-v1`–`ai-v6` 同一条待裁定项——按"本项 D 用例全绿"解释五条件①（35/36：唯一 xfail 是票 24；
   C2 本项 8 条单元用例 + 1 条牌价覆盖门全绿）。
2. **三条判据的落地与读法**（本票最需要签认的一条）：
   - **未知牌**：`_unknown_card_expectation` = 未知池**逐牌名剩余张数 × 牌价**的加权平均；
     牌价 = 明写价 → 【杀】族价 → **装备槽位价**（`_POOL_EQUIPMENT_SLOTS` 字面表）→ 未登记的
     锦囊价。落点 = `_card_value(None, view=...)`（没有牌面的选牌槽）+ 四处"未知牌 × 1.0"的
     技能打分器（苦肉 / 界苦肉 / 克己 / 势斩）。**空池 ⇒ 0.0**（票面明写不许回魔数 1.0）；
     **记牌关闭 ⇒ 1.0**（回到票 23 之前）。
   - **【无懈可击】（锦囊目标）**：`_nullification_probability`（目标手里至少一张的超几何）+
     `_expected_trick_score` = `value × (1 − p) + 1.0 × p`（被取消也消耗对方一张牌）；
     **零 / 负收益的锦囊不打折**（给零收益的【铁索】一个正的补偿价，等于教 AI 为钓无懈白打
     一张牌）。**只数目标本人**：宽口径（盟友 × 关系权重）整版试过并**回退**（见第 9 条）。
     只对**单目标、当回合结算**的锦囊生效；多目标与延时锦囊保持常量分。
   - **【桃】（濒死 / 救援）**：`_rescue_probability` = 濒死者**自己**的【桃】/【酒】＋
     **它那一侧其他座位**的【桃】× **关系权重**（`_side_willingness`：用我对两席的
     `(P(敌), P(友))` 推"它那一侧"；**我自己不算**——我不会去救我刚要杀的人）。落点 =
     `_score_attack` 的斩杀项（`_expected_lethal_score`）。
3. **关系权重落在哪一侧**：票面写「无懈：锦囊目标的期望。关系权重用 `P(敌)` / `P(友)`」——
   本版的关系权重落在**救援**一侧；无懈一侧按目标本人收窄（第 9 条记录了宽口径的实测与回退）。
   这一条（无懈侧收窄）是**需要签认的读法**，不是漏做。
4. **边界（不是漏做）**：① **贯石斧的斩杀判据不折桃**（`_AXE_KILL_VALUE`；ai-v6 的裁定是
   "击杀恒发动、不扣牌价"，本票"一次只改一个量"）；② 【桃】top-off / 给濒死盟友留牌不在本票
   （spec 的 v2 候选）；③ 多目标 / 延时锦囊的无懈期望不打折。
5. **`diff_attributed = false` 的书面说明（spec §2.3 ④）**：预注册集合 = spec §6.1 C2 的四个
   （`PLAY_PHASE` / `DISCARD` / `SELECT_CARDS` / `PEACH`）**＋ `SKILL_ACTIVATION`**。越界 16 条
   （`quick` 2 + `promote` 14）**全部**是票 19–22 记过的**下游平分抽签漂移**——首分叉**之前**
   该座位有一条询问的"最高分候选数"变了、**选中的动作没变**（`randbelow(1)` 不取随机数、
   `randbelow(n>1)` 会取 ⇒ 后续平分抽签整体错位）。探针
   `.scratch/ai-light/probe23_drift.py` 对 14 条 promote 种子逐条核（`unexplained=0`；
   `quick` 那 2 条是同一批种子的子集）：逐条形态见下表（"首分叉之前改变的平分候选数"）。

   | seed | 首分叉 | 首分叉之前改变的平分候选数（new/old） |
   |---|---|---|
   | `1993754648812739176` | `DODGE@54` | `PLAY_PHASE#42` 1/4 |
   | `2669738819577465331` | `DODGE@49` | `PLAY_PHASE#24` 2/3 |
   | `3026004556264303857` | `ARRANGE@40` | `PLAY_PHASE#3` 3/4、`#27` 4/5、`SELECT_CARDS#30` 1/2 |
   | `3705237532806593554` | `SELECT_OPTION@9`（`jie_jizhi:limit`） | `PLAY_PHASE#7` 1/4 |
   | `5457887703985276578` | `DODGE@26` | `PLAY_PHASE#18` 1/2、`#21` 2/3 |
   | `7893805329013966869` | `ARRANGE@44` | `PLAY_PHASE#32` 2/4、`SELECT_CARDS#34` 1/2 |
   | `8957941052003042385` | `SELECT_OPTION@24`（`fanjian:choice`） | `PLAY_PHASE#16` 1/4 |
   | `11808867467746528712` | `SELECT_OPTION@54`（`yijue:heal`） | `PLAY_PHASE#28` 1/4 |
   | `12555397892655255475` | `SELECT_OPTION@53`（`lianying:seat:0`） | `PLAY_PHASE#33` 2/4、`SELECT_CARDS#37` 1/2 |
   | `12901108034294625984` | `RESPOND@13` | `PLAY_PHASE#3` 1/2 |
   | `13728138323678028576` | `ARRANGE@85` | `PLAY_PHASE#74` 1/2 |
   | `15363649856517724370` | `SELECT_OPTION@29`（`fenwei:0`） | `PLAY_PHASE#16` 1/3 |
   | `17385976979338922382` | `RESPOND@45` | `PLAY_PHASE#35` 1/2 |
   | `17975530960331571698` | `DODGE@80` | `SELECT_CARDS#56` 1/2 |

   两条 `SELECT_OPTION` 的分叉动作本身同价（`jie_jizhi:limit` = 弃 / 不弃判定牌；`fanjian:choice`
   = 花色四选一，四条分支期望相同）；`ARRANGE` 是同一组牌的排列（置顶 / 置底的分割点相同）。
   `.scratch/ai-light/probe23_attrib.py --deals 768` 的 `by_kind` / 越界数 / 种子批 sha256 与
   promote 的结果 JSON **逐项相等**（504 副、14 条越界、批 `45eee43b4466`）。
6. **缓存命中率下限按新行为面重取**（`tests/test_engine_cache.py` ⑤，同票 19 / 21 的口径）：
   `14540 / 14734 = 0.98683`（上一条 `12643 / 12788 = 0.98866`；缓存实现与键函数一字未动，
   键覆盖 ①–④ 全绿）。调用数 12788 → **14734**（同种子下对局走向不同、更长），命中率再降
   **0.183 pp**；判据形态不变（实测值、零余量、少 1 次命中即红）。
7. **夹具重取（判据未放宽）**：`tests/test_all_ai_games.py` 的界魏 B 覆盖种子 `ACCEPTANCE_SEEDS[2]`
   （138）→ `ACCEPTANCE_SEEDS[3]`（139）——138 这一局不再发动界【天妒】。扫描器
   `.scratch/ai-light/seed_scan_t23.py` 逐局核"四条技能都真的发动"，判据仍是**四条都要发动**。
8. **未实机**：C2 是第二批的**末项**（spec §6.2：I1 → S1 → C2），按 §5.6「按批次末跑一局」本版
   应补一局实机；由用户驱动（配方 `docs/agents/acceptance-ai.md`，实机带 7001–7099 轮换到
   **7033**），结论回填 `.scratch/ai-light/acceptance.md` §十一 与本节裁定块。**不阻塞**本行的
   读数与五条件，但**阻塞**"本版已验收"的结论。
9. **第二轮（宽口径无懈）试过并回退——按 spec §6.7 的停手线**：宽口径把"目标那一侧"整版接进
   无懈期望（盟友 × 关系权重，与救援同一台机器），提交 `4eaa439` 上 `promote` 768 副对父版本
   **−0.0143**、CI `[-0.0456, 0.0143]`（CI 下界低于 δ=0.0319）⇒ `non_inferior=false`，
   **按停手线回退到"只数目标本人"**（`runs/ai-eval-4eaa439.json` 留档）。同期保留的修正：
   ① **零 / 负收益锦囊不打折**（评审自纠：别教 AI 为钓无懈白打一张牌）；
   ② **`_side_willingness` 排除我自己**（我不会救我刚要杀的人——这一条同时修掉了宽口径下
   "我的【桃】把自己算成救援者"的错账）；③ 装备按**槽位价**计价 + 每个档位牌名必须有登记价的
   覆盖门。**`heuristic_sha256` 与所有读数按 `b098a77` 读**，`4eaa439` 不是本版。
10. **tag 先于人类签认**（票 18–22 同先例）；`verdict.human_decision` 仍是 `null`，人签在台账。
   被拒绝时按停手线回退（tag 只增不删，台账追加拒绝理由）。

【人类裁定结果（ai-v7，2026-10-30）：通过（10 条口径全部签认 + 实机 4 项必验全过）】

- 用户原话：「必验4项没有问题，全部通过」「实机感受没有问题」「10条口径全部验收」——
  ①`cases_green=false` 的解释（口径 1）、②三条判据的落地与读法（口径 2）、
  ③关系权重落在救援一侧、无懈侧收窄（口径 3）、④边界：斧子不折桃 / 多目标与延时锦囊不打折
  （口径 4）、⑤16 条越界首分叉 = 下游平分抽签漂移（口径 5）、⑥缓存下限重取
  （口径 6）、⑦界魏 B 覆盖种子 138 → 139（口径 7）、⑧未实机（本局已补，口径 8）、
  ⑨**第二轮宽口径无懈试过并回退**（口径 9）、⑩tag 先于签认（口径 10）——**全部签认**。
- **实机读数**（`ai_eval live --run 7033-5p-20261030-204425 --runs-dir .scratch/ai-light/runs`）：

  ```text
  实机 seed=7033 5p identity 160 人类=seat0(反贼) 终局=内奸胜 获胜者=[3]
  询问=330 AI漏动作 pass=3/149(2.0%) composite=24/36(66.7%) 人类超时=0
  ```

- **报账 1（AI 蠢点，spec v2 候选 #10，不阻塞）**：终局单挑时大乔把【铁索连环】只指向界吕布
  一人（没有第二个横置角色 ⇒ 这次使用只花掉一张牌）。已登记 v2 候选 #10（附"真判据 = 横置链上
  的其他存活座位数 == 0"的重裁提醒）。
- **报账 2（UI bug，另开票，不阻塞）**：排列面（界李典【恂恂】固定 2+2）上下两行锁死、不能拖动
  交换；根因 = `tools/sgs_web/static/arrange.js:arrangeMoveTo` 对固定切分拒绝跨行移动。
  新票：`.scratch/arrange-swap/issues/01-arrange-cross-row-swap.md`（用户手动启用）。
- 结论指针：票 23 的 `## Answer` + `runs/ai-eval-b098a77.json` + `.scratch/ai-light/acceptance.md`
  §十一（实机读数与两条报账的逐条归因）。
- `tag ai-v7` 已建；`verdict.human_decision` 仍是 `null`（工具恒写 null，人签在本块）。

【人类裁定结果（ai-v4，2026-10-30）：满足（全部签认，无遗留待签项）】
- 用户原话「四条口径均通过」（指台账行的 ①–④：`cases_green` 解释 / 5 条越界首分叉的下游
  漂移归因 / 缓存下限重取 / 8 人局误伤结构），并「必验4条没有问题，全部通过」——实机
  seed=7004 的能开局 / AI 应答 / 无非法 / 到终局四项。
- **收口时的兜底签认**（2026-10-30，用户原话）：「本票 21 全部通过……**没签认的地方我全部
  签认通过**」⇒ 两轴评审里"留"的两条判断项（阵营→敌/友 的两张 if 级联、`(P(敌), P(友))`
  位置元组）与其余所有未签项**一并签认**；本行不再有遗留待签项。
- 人类可观察到的本票效果：**忠臣会保护主公**（实机 7004 里座位 1 的忠臣对座位 3 的主公
  用过河拆桥拆掉反贼贴的【乐不思蜀】）。
- **观察到的局限（不是本票的缺陷）**：主公**不能从"谁保护过我"反推暗置身份**——同一局
  seq=511 主公仍对忠臣出【决斗】。这条属 spec §6.4 的**档 2（行为 / 历史推断）**，本票明写
  出 scope（`PlayerView` 无历史）；已按 §6.8 登记为 **v2 候选第 9 条**，与票 27 同类：
  先开协议票（把公开行动摘要 / 关系计数放进 `PlayerView`），再谈实现。
- 结论指针：票 21 的 `## Answer` + `runs/ai-eval-d00badb.json` +
  `.scratch/ai-light/acceptance.md` §六 / §七（8 人 sanity 与实机 seed=7004）。
- tag `ai-v4` 已建（版本留存）；`verdict.human_decision` 仍是 `null`（工具恒写 null，人签在
  台账）。
