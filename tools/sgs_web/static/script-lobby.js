/* SGS 前端 · 剧本模式的开局页（M7 票 20 / spec §4、票 04 §6）。
 *
 * 这是开局页上的**增量**一段：既有三块（开一局表单 / 将池预设 / 已有录像）一个都没删，
 * 这里只加三个入口、一行摘要、坏档的修复面与两个确认框。加载顺序见 index.html。
 *
 * **判据全在服务端**：三态（`missing` / `ok` / `broken`）、摘要的五个数、坏档的 `reason`、
 * `.bak` 能不能恢复（`bak_available`）、`revision`（乐观锁）、`value_table_locked` 都是
 * `GET /api/script-save` 的原样字段。前端**不猜坏在哪**，也不把坏档当空档
 * （坏档 ⇒ 「继续」置灰 + 红字 + 修复按钮，票 04 §5）。
 *
 * 三个动作各是一条端点：
 *   「新剧本」→ `POST /api/script-roster`（只读）→ 选剧本 → 3 选 1 → `POST /api/script-runs`
 *      （**3 选 1 期间不落盘**：关页面 = 没建过；有旧档时先三选一，带旧 `revision` 才算覆盖）
 *   「继续剧本」→ `POST /api/script-runs/<run_id>/resume`（棋盘体交给票 21 渲染）
 *   「放弃剧本」/「放弃存档」→ `DELETE /api/script-save[?expected_revision=N]`
 *   「从我上一份恢复」→ `POST /api/script-save/restore`
 *
 * 退出确认框（战斗中）只调 `suspend` 一个端点：**前端不判断自己在不在战斗里**，服务端回的
 * `suspended` 才是事实（票 04 §6 / 票 19 §3.5）。它的入口归牌桌（票 21 / 23），本票交出
 * `scriptExitConfirm()` 与那两个键。
 */
"use strict";

/* ---------------------------------------------------------------- 三态 */

/** `.bak` 也不可用那句明话（票 04 §5）。文案**只在这一份 JS 里**写：`index.html` 的同名
 *  文本只是无脚本时的兜底，渲染时由下面这行覆盖成同一个串（烟测垫片读不到静态文本）。 */
const SCRIPT_BACKUP_UNAVAILABLE = "备份也不可用（只能放弃存档）";

/** `GET /api/script-save` → 三态回包，装进 `state.script.save` 并重画。
 *
 * 失败时**保留上一份**（网络错误不是"没有档"：把 `missing` 造出来会让用户以为进度没了）。
 */
async function scriptLoadSave() {
  try {
    const body = await api("/api/script-save");
    scriptApplySave(body);
  } catch (error) {
    scriptRender();
    scriptHint(`剧本存档读取失败：${error.message}`);
  }
  return state.script.save;
}

/** 把一份存档回包装进本地态并重画。
 *
 * 同形的体有四处来源：`GET /api/script-save`、`DELETE` 的 `{save}`、`suspend` 的 `{save}`、
 * 以及 409 冲突体（`GET` 的整份载荷 + `error`）——故"认不认"只看有没有 `status` 这个三态键。
 */
function scriptApplySave(body) {
  if (body && typeof body.status === "string") {
    state.script.save = body;
    if (body.run_id) state.script.runId = body.run_id;
    if (Number.isInteger(body.revision)) state.script.revision = body.revision;
    // 存档没了 / 换了一轮：上一条棋盘体不再属于这份档（票 21 重画时不许沿用旧底稿）。
    if (!body.run_id || (state.script.board && state.script.board.run_id !== body.run_id)) {
      state.script.board = null;
    }
  }
  scriptRender();
  // 票 21：棋盘体跟着存档走——存档没了（放弃 / 通关删档）就把棋盘收干净，
  // 有棋盘体时这里不动它（重画由 `script-board.js:scriptBoardRender` 负责）。
  if (typeof scriptBoardSync === "function") scriptBoardSync();
  return state.script.save;
}

/** 摘要行：剧本名 · 大节 `layer_index+1`/总数 · 已结算节点数 · 铜钱 · `saved_at`。
 *
 * 五个数原样印服务端的 `summary`；空档返回空串（摘要行整块不画，不留空壳）。
 */
function scriptSummaryText(save) {
  const summary = (save && save.summary) || null;
  if (!summary) return "";
  const layer = `${Number(summary.layer_index) + 1}/${summary.layer_count}`;
  return `${summary.script_name} · 大节 ${layer} · 已结算 ${summary.settled_nodes} 个节点`
    + ` · 铜钱 ${summary.coins} · ${save.saved_at}`;
}

/** 三态的**纯派生**：三个入口的可点态与哪几块可见都读这里，不在各处再判一次。
 *
 * `canContinue` 的判据是票 04 §6 的原话——`missing` / `broken` ⇒ 置灰；
 * `canRestore` 是"`.bak` 存在且过了三段门"（`bak_available`，坏备份不是退路）。
 */
function scriptLobbyView(save) {
  const payload = save || state.script.save;
  const status = payload ? payload.status : "missing";
  const ok = status === "ok";
  const broken = status === "broken";
  const busy = Boolean(state.script.busy);
  return {
    status,
    ok,
    broken,
    busy,
    canContinue: ok && !busy,
    hasSave: ok || broken,
    canRestore: broken && Boolean(payload && payload.bak_available) && !busy,
    summary: ok ? scriptSummaryText(payload) : "",
    reason: broken ? String((payload && payload.reason) || "") : "",
    locked: Boolean(payload && payload.value_table_locked),
  };
}

/* -------------------------------------------------------------- 画三态 */

/** 提示行（`#script-hint`）：状态描述 + 动作回执共用这一格，始终是**最新一句**。 */
function scriptHint(text) {
  const node = $("script-hint");
  if (node) node.textContent = text || "";
  return text || "";
}

/** 三态描述（每次重画都先落这一句，动作回执在重画**之后**覆盖它）。 */
function scriptStatusHint(view) {
  if (view.broken) return "存档坏了：先「从我上一份恢复」或「放弃存档」。";
  if (view.ok) return "有剧本进度（「继续剧本」回到棋盘，或「放弃剧本」删掉它）。";
  return "还没有剧本存档（「新剧本」开始一轮）。";
}

/** 画三块：三个入口的可点态 / 摘要行 / 坏档面，再画 3 选 1 的那一段。 */
function scriptRender() {
  if (!state.script) return null;
  const view = scriptLobbyView();
  const busy = view.busy;
  const set = (id, apply) => {
    const target = $(id);
    if (target) apply(target);
  };
  set("btn-script-new", (node) => { node.disabled = busy; });
  set("btn-script-continue", (node) => { node.disabled = !view.canContinue; });
  set("btn-script-abandon", (node) => {
    node.hidden = !view.hasSave;
    node.disabled = busy;
  });
  // 摘要行（`ok` 才有内容）：缺省整块不画，别留一行空壳冒充"有进度"。
  set("script-summary", (node) => {
    node.hidden = !view.summary;
    node.textContent = view.summary;
  });
  // 坏档面：红字 = 服务端的 `reason`；`.bak` 不可用 ⇒ 「恢复」不画、补一句明话。
  set("script-broken", (node) => { node.hidden = !view.broken; });
  set("script-broken-reason", (node) => {
    node.hidden = !view.broken;
    node.textContent = view.reason;
  });
  set("btn-script-restore", (node) => {
    node.hidden = !view.canRestore;
    node.disabled = busy;
  });
  set("btn-script-discard", (node) => {
    node.hidden = !view.broken;
    node.disabled = busy;
  });
  set("script-broken-backup", (node) => {
    node.hidden = !view.broken || view.canRestore;
    node.textContent = SCRIPT_BACKUP_UNAVAILABLE;
  });
  scriptRenderPicker();
  scriptHint(scriptStatusHint(view));
  return null;
}

/* -------------------------------------------------------- 选剧本 / 3 选 1 */

/** 选中的剧本（`POST /api/script-roster` 的 `scripts[]` 里那一项，自带 3 名候选）。 */
function scriptChosenScript() {
  const wanted = state.script.scriptId;
  return (state.script.scripts || []).find((item) => item.id === wanted) || null;
}

/** 一格候选（选剧本 / 3 选 1 共用）：带 `data-*` 的按钮，点一下选中，选中态 `.on`。 */
function scriptChoiceButton(dataset, label, chosen) {
  const button = document.createElement("button");
  button.type = "button";
  button.classList.add("script-choice");
  if (chosen) button.classList.add("on");
  button.textContent = label;
  for (const [name, value] of Object.entries(dataset)) {
    button.setAttribute(name, String(value));
  }
  return button;
}

/** 3 选 1 那一段：`#script-picker` / `#script-choices` / 「开档」的可点态。 */
function scriptRenderPicker() {
  const picker = $("script-picker");
  if (!picker) return null;
  const picking = Boolean(state.script.pickerOpen);
  picker.hidden = !picking;
  const script = scriptChosenScript();
  const hint = $("script-picker-hint");
  if (hint) {
    hint.textContent = !picking ? ""
      : script
        ? "第 2 步 / 2：3 选 1 —— 选一名初始角色（此刻还没落盘，关页面等于没建过）"
        : `第 1 步 / 2：选一部剧本（库里共 ${(state.script.scripts || []).length} 部）`;
  }
  const box = $("script-choices");
  if (box) {
    box.innerHTML = "";
    if (picking && !script) {
      for (const item of state.script.scripts || []) {
        const button = scriptChoiceButton(
          { "data-script-id": item.id }, item.name || item.id, false);
        button.title = `选中《${item.name || item.id}》（还不会建档）`;
        button.addEventListener("click", () => scriptChooseScript(item.id));
        box.appendChild(button);
      }
    } else if (picking && script) {
      for (const heroId of state.script.roster || []) {
        const button = scriptChoiceButton(
          { "data-hero-id": heroId },
          // 名字走武将目录（`heroDisplayName`，服务端 `GET /api/heroes` 是唯一名单来源）；
          // 目录里没有的 id（例如剧本主角「能人志士」不在启用池）**不编名字**，原样印 id。
          heroDisplayName(heroId),
          state.script.hero === heroId,
        );
        button.title = `选中 ${heroDisplayName(heroId)} 作为这一轮的角色`;
        button.addEventListener("click", () => scriptChooseHero(heroId));
        box.appendChild(button);
      }
    }
  }
  const go = $("btn-script-picker-go");
  if (go) {
    go.disabled = !(picking && script && state.script.hero) || Boolean(state.script.busy);
  }
  const cancel = $("btn-script-picker-cancel");
  if (cancel) cancel.hidden = !picking;
  return null;
}

/** 打开 3 选 1 的第一步：`POST /api/script-roster` 一次拿全（剧本清单 + 各自候选）。
 *
 * 这一步**不写档**（票 19 §3.1：候选是作者编排的数据，端点不消耗随机数）——所以
 * 从这里到按「开档」之前的任何操作都等于没发生过。
 */
async function scriptOpenPicker() {
  if (state.script.busy) return null;
  state.script.busy = true;
  scriptRender();
  let body = null;
  let failure = "";
  try {
    body = await post("/api/script-roster", {});
  } catch (error) {
    failure = `剧本清单读取失败：${error.message}`;
  } finally {
    state.script.busy = false;
  }
  if (body) {
    state.script.scripts = Array.isArray(body.scripts) ? body.scripts : [];
    state.script.scriptId = null;
    state.script.roster = [];
    state.script.hero = null;
    state.script.pickerOpen = true;
  } else {
    state.script.pickerOpen = false;
  }
  scriptRender();
  if (failure) scriptHint(failure);
  return body;
}

/** 第一步：选剧本 ⇒ 换成第二步的 3 名角色候选（本地切换，不再请求）。 */
function scriptChooseScript(scriptId) {
  const item = (state.script.scripts || []).find((entry) => entry.id === scriptId);
  if (!item) return null;
  state.script.scriptId = item.id;
  state.script.roster = (item.roster_candidates || []).slice();
  state.script.hero = null;
  scriptRender();
  return item;
}

/** 第二步：3 选 1。只改本地态——**仍然不落盘**。 */
function scriptChooseHero(heroId) {
  if (!(state.script.roster || []).includes(heroId)) return null;
  state.script.hero = heroId;
  scriptRender();
  return heroId;
}

/** 取消 3 选 1：什么都不发生（不建档、不删旧档），下一次「新剧本」重新读清单。 */
function scriptClosePicker() {
  state.script.pickerOpen = false;
  state.script.scriptId = null;
  state.script.roster = [];
  state.script.hero = null;
  state.script.overwrite = false;
  scriptRender();
  scriptHint("已取消：没有建档，旧档原样保留。");
  return null;
}

/* ------------------------------------------------------------ 五个动作 */

/** 棋盘体收进本地态（建 / 续档的同一个出口）：`save` 一并刷新，`board` 留给票 21。 */
function scriptAdoptBoard(board) {
  state.script.board = board || null;
  if (board && board.run_id) state.script.runId = board.run_id;
  if (board && board.save) scriptApplySave(board.save);
  return board;
}

/** 「新剧本」：有旧档先三选一；坏档先修 / 放弃（服务端也拒绝在坏档上建档）。
 *
 * 坏档那条**不是**前端自己发明的规则：`status` 本来就是前端要读的三态之一（「继续」的置灰
 * 判据），服务端的 `create_run` 对坏档同样 409——这里只是把用户往修复面引，不静默试一次。
 */
async function scriptNewScript() {
  if (state.script.busy) return null;
  const view = scriptLobbyView();
  if (view.broken) {
    scriptHint("存档坏了：先「从我上一份恢复」或「放弃存档」，再开新剧本。");
    return null;
  }
  if (view.ok) {
    scriptConfirm({
      kind: "overwrite",
      title: "已有剧本进度",
      text: "当前进度会被覆盖（不可恢复）",
      actions: [
        {
          label: "继续旧档",
          primary: true,
          run: () => { scriptCloseConfirm(); return scriptContinueRun(); },
        },
        {
          label: "覆盖新开",
          run: () => {
            scriptCloseConfirm();
            // 覆盖的凭据 = 刚才那份档的 revision（服务端 revision 对不上就 409）。
            state.script.overwrite = true;
            return scriptOpenPicker();
          },
        },
        {
          label: "取消",
          ghost: true,
          run: () => {
            scriptCloseConfirm();
            scriptHint("已取消：旧档原样保留，什么都没有改动。");
            return null;
          },
        },
      ],
    });
    return null;
  }
  state.script.overwrite = false;
  return scriptOpenPicker();
}

/** 「开档」：唯一的一次建档写（`POST /api/script-runs`）。
 *
 * 有旧档（覆盖）时带 `expected_revision`；没有则**不带**（服务端把它当"第一次建档"）。
 */
async function scriptCreateRun() {
  const scriptId = state.script.scriptId;
  const hero = state.script.hero;
  if (!scriptId || !hero || state.script.busy) return null;
  const payload = { script_id: scriptId, chosen_hero: hero };
  if (state.script.overwrite && Number.isInteger(state.script.revision)) {
    payload.expected_revision = state.script.revision;
  }
  state.script.busy = true;
  scriptRender();
  let board = null;
  let failure = "";
  try {
    board = await post("/api/script-runs", payload);
  } catch (error) {
    failure = `建档失败：${error.message}`;
    if (error.body) scriptApplySave(error.body);
  } finally {
    state.script.busy = false;
  }
  if (board) {
    state.script.pickerOpen = false;
    state.script.overwrite = false;
    scriptAdoptBoard(board);
    scriptRender();
    // 票 21：建局成功后直接进棋盘（棋盘体的唯一渲染口在 `script-board.js`）。
    scriptBoardShow(board);
  } else {
    scriptRender();
    scriptHint(failure);
  }
  return board;
}

/** 「继续剧本」：一条 `resume`，棋盘体落进 `state.script.board`（票 21 的渲染输入）。 */
async function scriptContinueRun() {
  if (state.script.busy) return null;
  const runId = state.script.runId || (state.script.save && state.script.save.run_id);
  if (!runId) return null;
  state.script.busy = true;
  scriptRender();
  let board = null;
  let failure = "";
  try {
    board = await post(`/api/script-runs/${encodeURIComponent(runId)}/resume`, {});
  } catch (error) {
    failure = `继续失败：${error.message}`;
    if (error.body) scriptApplySave(error.body);
  } finally {
    state.script.busy = false;
  }
  if (board) {
    scriptAdoptBoard(board);
    scriptRender();
    // 票 21：「继续剧本」= 读档回棋盘（不重建引擎）。
    scriptBoardShow(board);
  } else {
    scriptRender();
    scriptHint(failure);
  }
  return board;
}

/** 「放弃剧本」/「放弃存档」：`DELETE /api/script-save`（删两份 + 解锁价值表）。
 *
 * `ok` 一态必须带 `expected_revision`（票 04 §7）；`broken` 没有 revision 可对，故不带。
 */
async function scriptAbandonSave() {
  if (state.script.busy) return null;
  const save = state.script.save || {};
  const ok = save.status === "ok";
  if (!ok && save.status !== "broken") return null;
  const query = ok && Number.isInteger(save.revision) ? `?expected_revision=${save.revision}` : "";
  state.script.busy = true;
  scriptRender();
  let body = null;
  let failure = "";
  try {
    body = await api(`/api/script-save${query}`, { method: "DELETE" });
  } catch (error) {
    failure = `删除存档失败：${error.message}`;
    if (error.body) scriptApplySave(error.body);
  } finally {
    state.script.busy = false;
  }
  if (body) {
    state.script.overwrite = false;
    scriptApplySave(body.save || {});
    scriptRender();
    scriptHint("剧本文档已删除（价值表解锁）。");
  } else {
    scriptRender();
    scriptHint(failure);
  }
  return body;
}

/** 「从我上一份恢复」：`POST /api/script-save/restore`（写回 `.bak` 的原字节、不改 revision）。 */
async function scriptRestoreBackup() {
  if (state.script.busy) return null;
  state.script.busy = true;
  scriptRender();
  let body = null;
  let failure = "";
  try {
    body = await post("/api/script-save/restore", {});
  } catch (error) {
    failure = `恢复失败：${error.message}`;
    if (error.body) scriptApplySave(error.body);
  } finally {
    state.script.busy = false;
  }
  if (body) {
    scriptApplySave(body.save || {});
    scriptRender();
    scriptHint("已从上一份备份恢复（revision 不变）。");
  } else {
    scriptRender();
    scriptHint(failure);
  }
  return body;
}

/* ---------------------------------------------------------- 确认框 */

/** 确认框（撞旧档三选一 / 退出战斗两选一共用）：标题 + 一句正文 + 场景现铺的按钮。 */
function scriptConfirm(spec) {
  const dialog = $("script-confirm");
  const backdrop = $("script-confirm-backdrop");
  if (!dialog) return null;
  state.script.confirm = (spec && spec.kind) || "confirm";
  $("script-confirm-title").textContent = (spec && spec.title) || "";
  $("script-confirm-text").textContent = (spec && spec.text) || "";
  const box = $("script-confirm-actions");
  const buttons = [];
  if (box) {
    box.innerHTML = "";
    for (const action of (spec && spec.actions) || []) {
      const button = document.createElement("button");
      button.type = "button";
      if (action.primary) button.classList.add("primary");
      if (action.ghost) button.classList.add("ghost");
      button.textContent = action.label;
      // 箭头**返回** `run()` 的 promise：烟测的 `clickAndWait` 会等它跑完再进下一块。
      button.addEventListener("click", () => action.run());
      box.appendChild(button);
      buttons.push(button);
    }
  }
  dialog.hidden = false;
  if (backdrop) backdrop.hidden = false;
  return buttons;
}

/** 收起确认框（两个场景的「取消」与每个动作开头都走这里）。 */
function scriptCloseConfirm() {
  state.script.confirm = null;
  const dialog = $("script-confirm");
  if (dialog) dialog.hidden = true;
  const backdrop = $("script-confirm-backdrop");
  if (backdrop) backdrop.hidden = true;
  return null;
}

/** 退出确认框（战斗中）：「退出后这场战斗要重打；棋盘进度已保存。」确认 / 取消。
 *
 * 入口归牌桌（票 21 / 23 的「离开棋局」）。**这里不判断是不是在战斗里**：确认后的动作就是
 * 那一条 `suspend`，服务端回 `suspended` 说它到底作废了一个会话没有。
 */
function scriptExitConfirm(after) {
  const runId = state.script.runId || (state.script.save && state.script.save.run_id) || null;
  return scriptConfirm({
    kind: "exit",
    title: "退出战斗",
    text: "退出后这场战斗要重打；棋盘进度已保存。",
    actions: [
      {
        label: "确认",
        primary: true,
        run: () => {
          scriptCloseConfirm();
          // `after` = 调用方的收尾（票 21 的「离开棋局」用它回开局页；入口归棋盘）。
          return scriptSuspend(runId).then((body) => {
            if (body && typeof after === "function") after(body);
            return body;
          });
        },
      },
      {
        label: "取消",
        ghost: true,
        run: () => { scriptCloseConfirm(); return null; },
      },
    ],
  });
}

/** 退出战斗 = **只**调 `POST …/suspend`（不写档、不带 `expected_revision`，票 19 §3.5）。 */
async function scriptSuspend(runId) {
  const target = runId || state.script.runId
    || (state.script.save && state.script.save.run_id) || null;
  if (!target || state.script.busy) return null;
  state.script.busy = true;
  scriptRender();
  let body = null;
  let failure = "";
  try {
    body = await post(`/api/script-runs/${encodeURIComponent(target)}/suspend`, {});
  } catch (error) {
    failure = `退出失败：${error.message}`;
    if (error.body) scriptApplySave(error.body);
  } finally {
    state.script.busy = false;
  }
  if (body) {
    scriptApplySave(body.save || {});
    scriptRender();
    scriptHint(body.suspended
      ? "已退出：这场战斗作废（重进要点「继续剧本」重打该场），棋盘进度已保存。"
      : "棋盘进度已保存（当时不在战斗里）。");
  } else {
    scriptRender();
    scriptHint(failure);
  }
  return body;
}

/* -------------------------------------------------------------- 接线 */

/** 接线 + 首屏取一次三态（`main.js:wire()` 末尾调一次）。 */
function wireScriptLobby() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  };
  on("btn-script-new", () => scriptNewScript());
  on("btn-script-continue", () => scriptContinueRun());
  on("btn-script-abandon", () => scriptAbandonSave());
  on("btn-script-restore", () => scriptRestoreBackup());
  on("btn-script-discard", () => scriptAbandonSave());
  on("btn-script-picker-go", () => scriptCreateRun());
  on("btn-script-picker-cancel", () => scriptClosePicker());
  scriptRender();
  return scriptLoadSave();
}
