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
| `ai-v1` | `65609d40387e9b54c83ff14aa998d6e9d16cafb0` | `65609d40387e9b54c83ff14aa998d6e9d16cafb0` | `90ad861f387be9399124bb01d2a21b3f255c5abb687a77c80cda5f2aad0dca7a` | `29fb76e9d330b4c3a55915e6fec4f44c49e3a3eba378299fa6164d64b327793e` | 768（commit 派生） | `runs/ai-eval-65609d4-capped.json` | D1 无懈目标化（票 18）：`NULLIFY` 按"被取消的牌对目标利 / 害 × 已明关系"宣 / 不宣（嵌套与未登记牌名 `Pass`）。`quick` 59/128 全落 `RESPOND`；`promote` diff 311/768 全落 `RESPOND`，对父版本 `+0.0026`、CI `[-0.0169, +0.0235]`，对 `ai-v0` 同值 | agent 收口：接受（D1 判据）；人类复核待定 |

- `ai-v0` 是**原点基线**（spec §2.6）：`promote --anchor ai-v0` 用它防梯子漂移。
- 上表的种子批 = `derive_batch(ai-v0 的 commit, 128)` 的摘要（`quick` 的批大小）；它是**参考
  值**，不是跑过的读数。
- 后续版本在跑完 `quick` → `promote` → 实机（可选）后追加一行；被接受的 tag 必须能被版本
  加载器加载。实机结论只放指针：`.scratch/ai-light/acceptance.md`。
- **`ai-v1` 的两条口径说明**（读 JSON 前先看这两条）：
  1. `cases` = **8/9**：唯一未绿是 `tests/test_ai_cases.py` 里 D3【遗计】的
     `xfail(strict=True)`（票 24，本票未开工），故工具把 `verdict.cases_green` 记 `false`；
     D1 的 6 条用例全绿。按 §2.3 ① 的本意（本项 D 用例全绿）成立，按工具的模块级口径记录
     为 false——两条都写在这里，不藏。
  2. 结果 JSON 由**带应答上限的同一套库**跑出（`.scratch/ai-light/promote_capped_t18.py`）：
     768 副里恰有 1 颗种子（`5667218070898781`）命中一个**既有**的界孙尚香【结姻】空转
     （`ai-v0` 下同样复现），不设上限时 promote 永远等这一副。上限触发后按工具的既有口径
     记成"该副对局错误、整对退出配对" ⇒ `errors = 4`、**767 对有效配对**（768 − 1）。
- 封印批名字与摘要也记在这张表下方的说明里（`sealed-1` = `0d9cc40a450f208ddf0711555279104b72498b6581d97d4e6ca0847d324d5127`，1024 副）。
