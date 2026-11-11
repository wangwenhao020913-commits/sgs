# 01: 性能——派生通道游标化 / 会话回收 / 前端事件流上限

**What to build:** 把"越打越卡"三处成本拆掉：服务端 `view()` 的 O(记录数) 派生、放弃的对局永不回收、
前端 `#t-log` 无上限且每条记录整桌重建。

**Blocked by:** None
Status: resolved
Type: task
Touches: tools/sgs_web/serve.py, tools/sgs_web/projection.py, tools/sgs_web/session.py, tools/sgs_web/narrative.py, tools/sgs_web/static/session.js, tools/sgs_web/static/log.js, tests/test_web.py
Ruling: `.scratch/perf-web/issues/01-runs-meta-and-session-prune.md`（当年明确不做的第 3~7 条）

## 根因（实测，spec §1 #3）

1. **`view()` 每请求 O(N)**：`skill_events_after` / `damage_events_after` / `card_events_after` / `life_loss_events_after`
   四条派生通道**只为算 `last_seq`** 就把整条记录流 `model_dump(mode="json") + json.dumps + json.loads` 一遍
   （`projection.py:2850/2894`、`:2951/2982`、`:3022/3055`、`:3080/3111`）。
   实测 4671 条记录：单次 `view()` **2.4–2.5 s**，这三处合计 **1.6 s（66%）**；N=1053 → 1.36 s；N=500 → 0.36 s。
   2 s 一拍轮询 ⇒ 服务端被打满。
2. **会话永不回收**：`KEEP_FINISHED_SESSIONS=2` + `_prune_finished`（`serve.py:264/498`）**只在 `save_if_finished`（`:488`）里跑**
   ⇒ 中途放弃的局永远留在 `GameRegistry.sessions`。实测每局峰值 +9~23 MB（RSS 47→64→87→101→110 MB / 4 局）。
   这是"同一进程第二局也卡"的机制。
3. **前端**：`#t-log` 每条记录一枚 `<li>`（`log.js:54-71,74`、`session.js:925`），4671 条 ≈ 4671 个节点；
   `scrollLogToBottom` 每步 `scrollIntoView`（`session.js:1004,1487`、`log.js:103-108`）⇒ 每步强制布局且随子树线性变贵；
   `renderTable`（`table.js:253-382`）每条记录整桌重建（一局 4671 次）。`state.narrative` / `state.logRecords` 同样只增不减。

## 要做什么

1. **派生通道改"引擎只增缓冲 + after 游标"**（不要再用 `record_json` 往返求 `last_seq`）：
   记录是 pydantic 对象，`getattr(rec, "seq")` 就够（`session.records_after` 正是这么做的，实测 0.9 ms）。
   四条通道的过滤条件改为"只在 `seq > after` 的尾部窗口里找"，不再全量 `json.loads`。
2. **`narrative_after` 不再从头重建**：`narrative.py:131-150` 现在整份重建再按 seq 过滤（176–201 ms/次）。
   给它一条按 `after` 剪枝的入口（可缓存上一批的最大 seq）。
3. **`session.records()` 不再每次 tuple 全拷**；`records_after` / `next_cursor` 保持全扫（本来就 0.9 ms），
   但**不要再被调用 7 次**——把一次 `view()` 里对记录流的多趟扫描收成一趟。
4. **会话回收扩到 create/超时/放弃**：`GameRegistry.create` 里跑一次 prune；给会话加"最后访问时刻"，
   超过阈值且已终局/久未访问的回收。保留"最近 N 局未终局"。
5. **前端事件流上限**：`#t-log` 与 `state.logRecords` / `state.narrative` 各设上限（建议 500 条滚动窗口，
   与 `loglevel` 无关），滚动条贴底逻辑不变；**不要**改 `appendLog` 的渲染形状（`test_web_static.py` 钉着）。
6. **减少整桌重建**：给 `renderTable` 一条"这一条记录没有改座位/手牌/舞台"的快速判据（例如只看 delta 类型 +
   live view 是否真变了），不改 DOM 形状。

## 验收

- 新增：`tests/test_web.py::test_the_view_payload_never_json_round_trips_the_record_stream`
  （monkeypatch `projection.record_json` 抛错 ⇒ `view()` 照常返回；与 perf-web 票 01 对 `list_runs` 的做法同型）。
- 新增：`test_an_abandoned_session_is_pruned_when_a_new_one_is_created`。
- 新增：`test_the_event_log_keeps_a_bounded_number_of_nodes`（垫片里数 `#t-log` 子节点）。
- 量化（写进 `## Answer`）：N≈4700 记录时 `view()` 从 2.4 s 降到 __ ms；空增量 poll 从 24 ms 降到 __ ms。
- 全量门禁 + `bash tests/smoke-web.sh` + `bash tools/check_goldens.sh`（本票**不应**改任何记录流）。

## 会红的既有断言（预期为 0，若有要在 Answer 里解释）

- `tests/test_web_static.py` 里对 `log.js` / `session.js` 的静态断言（事件流形状、`scrollLogToBottom` 调用点）。
- 若 `view()` 的键集变了（例如删掉某个派生键）⇒ `test_web.py` 的载荷断言会红，**不许删键**。

## Answer

**已实现（2026-11-11，提交见本票落点；12 files）** —— 票面六条全部落地，`## 验收` 三条新断言 + 一条补充断言全绿，
金录像逐字节不变。

### 落地（按票面六条）

1. **派生通道改"只增缓冲 + after 游标"**（`projection.py`）
   - 新增 `_record_payload()`：`visible_record` 从 `json.loads(record_json(record))` 改成直接
     `record.model_dump(mode="json")`——省掉一次 `json.dumps` + 一次 `json.loads`（每条记录两次）。
   - 新增 `_record_last_seq()` / `_action_field()`：四条派生通道（skill / damage / card / life_loss）
     的"这批的上界"和 `skill_events` 的动作字段都改成**只读属性**（`getattr` / `.get`），
     不再 `json.loads(record_json(...))` 整条流一遍。
   - `skill_events_after` 新增 `query_actors=`：`serve.view` 把**一趟扫描**算好的全量应答者表传进来
     （应答指向的询问可能早于本批游标，故不能只看 tail）；缺省时仍从传入记录现算（单测 / 静态录像）。
   - 四条通道的 `records` 现在收**本批尾部窗口**（`seq > after`）：上界由窗口自身求，
     尾部非空时与"整条流最大 seq"逐条相同，尾部为空时本批本来就没有可下发的事件。
2. **叙事增量**（`narrative.py`）
   - 新增 `NarrativeCursor`：攒住 `_Narrator` 的状态，首见喂整条流、之后只喂**还没派生过的**记录
     （先读 `seq` 再决定要不要 `model_dump`）；`entries_after(after)` 只过滤缓存条目。
   - `build_narrative` / `narrative_after` 保持旧签名与旧结果（内部改用 cursor，仍是一次性纯函数）。
   - 缓存挂在 `Session.narrative_cursor`（随会话一起丢，不需要第二张注册表）；键
     （viewer / heroes / pack_id）变了就重建。
3. **一次 `view()` 只扫一趟记录流**（`session.py` + `serve.py`）
   - 新增 `Session.window(after) -> RecordWindow`：一趟给出 `tail` / `cursor` / `query_actors` / `records`。
     `view()` 从 7 趟（records / records_after / next_cursor / 应答者表 / 四条通道）收成 1 趟。
   - `records()` 不再 `tuple(tuple(...))` 双拷（只留引擎快照的**一份**元组）；`records_after` / `next_cursor`
     仍是全扫（票面允许，实测 0.9~2.4 ms），只是不再被 `view()` 调用 7 次。
4. **会话回收扩到 create / 超时 / 放弃**（`serve.py`）
   - `Session.last_access` + `Session.touch(now)`：GET / answer / create 三条入口都经 `mark_pending`
     打戳（时钟仍由调用方传入，`session.py` 不 import time）。
   - `GameRegistry.create` 登记完新局后跑一次 `_prune_abandoned`；`advance_clock`（每次取增量）顺带扫一眼。
   - 口径：未终局 + 空闲 ≥ `ABANDONED_SESSION_SECONDS`（30 min）才收，且**最近 `KEEP_LIVE_SESSIONS`
     （2）局永不按空闲收**；已终局仍走旧口径（落盘后保留最近 `KEEP_FINISHED_SESSIONS` 局）。
     未落盘的已终局会话不在回收面内（它们马上会被 `save_if_finished` 写成文件）。
5. **前端事件流上限**（`log.js` + `session.js`）
   - `LOG_ITEM_LIMIT = 500`：`#t-log` 的 `<li>`（`appendNarrativeEntry` / `appendLog` 两条生产口共用
     `trimLogItems()`，从头部摘）与 `state.logRecords` / `state.narrative`（`trimStreamState()`，
     推入方调用；`narrativeShown` 是下标、跟着前移）**同一个上限**，与 `loglevel` 无关。
     滚动条贴底（`scrollLogToBottom`）与 `appendLog` 的渲染形状（`<li>` + `innerHTML`）一个字没改。
6. **减少整桌重建**（`session.js`）
   - 新增 `recordTouchesTableView(record)`：上一行 `applyLiveDelta(record, liveView)` 之后，只有判据为真
     才 `renderTable()`，否则只 `renderProcessing()`。判据只放行**肯定改快照**的记录：
     询问 / 应答 / 结算帧 / 终局 / `card_reveal`+`pindian` / 非 `TURN_*`·`PHASE_*`·`DEATH_BEFORE_START`
     的时机 ⇒ 不重建；`live_only` 标签 delta / 未知 kind / `header` ⇒ 一律重建（拿不准就多画一次）。
     判据与 `applyLiveDelta` 的分支同源，**DOM 形状不变**。

### 验收 → 断言

| 验收条目 | 钉住它的断言 |
|---|---|
| `view()` 不再 JSON 文本往返整条记录流 | `tests/test_web.py::test_the_view_payload_never_json_round_trips_the_record_stream`（monkeypatch `projection.record_json` 抛错，`view()` 照常返回且 `records` / `snapshot` / `pending` 齐全） |
| 放弃的对局在建新局时被回收 | `tests/test_web.py::test_an_abandoned_session_is_pruned_when_a_new_one_is_created`（久未访问的未终局局被收、`get()` 抛 KeyError；近期访问过的与新建的都留着） |
| 叙事只派生新增那一段 | `tests/test_web.py::test_the_view_only_derives_the_new_narrative_records`（首屏建缓存后，第二趟 `view()` 喂给 `narrative._payload` 的条数 == 本批记录数，且结果与整份重建逐条相同） |
| `#t-log` 节点有上限 | `tests/test_web.py::test_the_event_log_keeps_a_bounded_number_of_nodes`（`tests/support/log_cap_probe.mjs` 把**真 `log.js`** 放进最小 DOM 垫片，两条生产口各喂 2×上限，数 `#t-log` 子节点） |
| 两份本地序列也上限、`narrativeShown` 跟移 | `tests/support/ui_smoke.mjs` 票01L1–L4（真会话 + 真 `$("t-log")`） |
| 回放步按"改不改表体"决定是否整桌重建 | `tests/support/ui_smoke.mjs` 票01T1–T3（T3 = 真回放一步后座位节点**同一份 DOM 节点**） |
| 派生通道内容不变 | 既有断言全绿：`test_skill_events_follow_the_record_cursor_and_only_cover_accepted_activations`、`tests/test_web_damage_events.py`、`tests/test_web_card_events.py`、`test_web.py` 里 life_loss / narrative 的整组契约 |

### 量化（`bash tools/baseline-worktree.sh` 开的基线树 vs 本树，同 seed 同停止点，取 3 次最小）

| N | `view(after=-1)` 基线 → 现在 | 空增量 poll 基线 → 现在 |
|---|---|---|
| **4745**（票面的 ≈4700） | **1877 ms → 263 ms**（首屏，含建叙事缓存）／**82 ms**（缓存命中再来一次） | **1797 ms → 12.2 ms** |
| 6612（seed 7 整局） | **2673 ms → 120 ms** | **2862 ms → 11.2 ms** |

- 四条通道的单项（N=6612，全量作 tail）：skill 585.9→26.8 ms、damage 552.8→3.1、card 569.6→2.9、
  life_loss 669.4→2.6；`visible_record` 全量 128.7→38.0 ms；`narrative_after`（整份重建）189.9 ms
  在轮询里**不再发生**。
- 前端：一局 6612 条记录 ⇒ `#t-log` ≤ 500 个 `<li>`（**−92%** 节点）；整桌重建 6612 → **1250** 次
  （**−81%**，按同一判据对真实下发载荷逐条计数）。
- 会话：一局放弃后不再常驻；内存里未终局会话的上界 = 最近 2 局 + 真正还在轮询的局（30 min 空闲即收）。

### 刻意选择的读法（评审请核这几条）

1. **`session.records()` 只去掉"第二次全拷"**：它现在返回引擎快照那一份元组（`tuple(_records)` 一次）。
   真正的零拷贝要给引擎加"只读视图"属性（`src/sgs/engine/game.py` 不在本票 Touches），故本票改为
   **热路径完全不调用它**（`view()` 只走 `Session.window()`，一趟扫描），把"每次 view 全拷两份"消掉。
2. **回收阈值写在套餐常量里**：`ABANDONED_SESSION_SECONDS = 30 min`、`KEEP_LIVE_SESSIONS = 2`。
   票面只说"超过阈值"，这是执行者定的数：30 min 比任何一次真的思考 / 看录像都长，又远短于"人走了进程还开着"。
   被回收的会话再取 ⇒ 404（前端现有提示"取增量失败"，本票不新增 UI）。
3. **`create` 里"先登记、再回收"**：这样"最近 2 局未终局"含刚建的那局；否则"有一个放弃的局 + 开新局"
   永远只有 2 局、永不回收（票面目标落空）。**已终局但未落盘**的会话不参与空闲回收（丢了就真丢了）。
4. **派生通道的上界改用"本批窗口"**：`last_seq` 由传入的 tail 求。尾部为空 ⇒ 无上界；这在真实流程里
   等价（引擎的事件锚点 `seq` 一定 ≤ 已写记录的最大 seq，尾部为空时 `seq > after` 恒假），
   静态录像 / 合成夹具的"事件超上界先压住"语义也原样保留（它们传的就是整条流）。
5. **叙事缓存挂在 `Session` 上**：`Session.narrative_cursor`（`Any`）由 `serve` 建 / 填，session 只持有。
   选它而不是注册表里第二张表：会话回收时缓存跟着走，不会漏。**并发边界**：服务端是
   `ThreadingHTTPServer`，同一会话的两次 `view()` 理论上可并发；本缓存与引擎 `submit` / 会话状态
   同姿态——假定"一局一个驱动方"（前端 `submitBusy` + 轮询互斥，地图 D15 的单机单人类口径）。
   与既有共享状态（引擎、`sessions` 表）同级，不新增锁。
6. **`renderTable` 判据只放行"肯定不改快照"的记录**：`card_reveal` / `pindian` 是纯展示；
   `hp_max_change` / `skill_gain` / `skill_lose` / `skill_suppress` 目前**也没有** `applyLiveDelta`
   分支，但本票**仍然重建**（票 14 要给 `hp_max_change` 补派生，届时这条判据不需要改）。
   `timing` 只放行 `TURN_*` / `PHASE_*` / `DEATH_BEFORE_START` 三种（它们是 `applyLiveDelta` 唯一
   处理的时机）；`header` / 未知 kind 一律重建。
7. **`record_json` 兜底仍在**：`_record_payload` 对没有 `model_dump` 的非 pydantic 对象保留
   `json.loads(record_json(record))`；真实记录全是 pydantic，故"禁 `record_json` 仍返回"成立。
8. **AI 适配契约 §1 不适用**：本票没有新增 `QueryKind` / 候选形状 / 动作面 / 信息通道
   （记录流与派生通道的**内容**逐条不变，只换推导方式），故 `tests/test_ai_adaptation.py` 零改动。

### 门禁

- **会红的既有断言：0 条**（票面"会红的既有断言（预期为 0）"成立；`view()` 键集未变、未删键）。
- `.venv/bin/pytest tests/test_web.py`：437 passed（含新增 4 条；另跑了 `test_web_static` / `test_m5_no_leak` /
  `test_web_damage_events` / `test_web_card_events` / `test_replays` / `test_hero_feng` / `test_action_contract` /
  `test_lobby_pool_contract` / `test_round_limit` / `test_wire_contract` / `test_judgment_reveal`，全绿）。
- `bash tests/smoke-web.sh`：PASS（含新增 票01L1–L4 / 票01T1–T3 七条）。
- `bash tools/check_goldens.sh`：**PASS：47 条标准 108 金录像逐字节不变**（本票不改任何记录流）。
- `bash tests/test_web.sh`（端到端 + 409 + 跨机可达）：PASS。
- `bash tests/test_dev_docs.sh`：PASS。
- 提交时 pre-commit 全量门禁：`bash tests/run_all.sh --quiet`（结果见提交落点）。

### 实机验收配方（DOM 形状未变，但事件流节点数 / 重绘频率改了，建议照一遍）

1. `bash tools/check_env.sh` → `bash tools/serve-dev.sh start` → 打开打印的 URL。
2. 8 人局开局后连点几手直到事件流 ≥ 100 条，再展开「事件流」面板（`#t-log-toggle`）：
   面板仍贴底、最新一条在底部可见；DevTools `$('#t-log').children.length` **始终 ≤ 500**。
3. 同一局里切「完整全部」档再切回「简易」：两种档都重新铺、节点数仍 ≤ 500（不回到 4700 级）。
4. 回放页（`/` 开局页 → 录像列表）打开一局长录像，拖动进度条：事件流照常逐条出现
   （`#r-log` 不在本票上限内）、牌桌座位正常刷新。
5. 可选：Network 面板看 `GET /api/sessions/<id>?after=<cursor>` 的空增量响应，
   本机实测 ≈ 10–12 ms（改前 ≈ 1.8–2.9 s）。
