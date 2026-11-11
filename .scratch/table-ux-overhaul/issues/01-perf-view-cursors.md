# 01: 性能——派生通道游标化 / 会话回收 / 前端事件流上限

**What to build:** 把"越打越卡"三处成本拆掉：服务端 `view()` 的 O(记录数) 派生、放弃的对局永不回收、
前端 `#t-log` 无上限且每条记录整桌重建。

**Blocked by:** None
Status: open
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
