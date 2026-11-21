/* SGS 前端 · 剧本编辑器（M7 票 25 / spec §4「编辑器新视图」、票 05 UI-5、票 03 §9）。
 *
 * 这一份是**作者面**：实体列表 + 新建 / 编辑 / 另存为副本 / 重命名 / 删除 + 引用选择器；
 * 大节页 = 网格（长宽可设）+ 拖放节点 + 画边 + 解除连线 + 设边条件 + 边列表；问号池加权
 * 编辑；校验面板。**判据不在前端**：整库 / `revision` / 校验 `errors` / 引用点 / 连带删除
 * 的后果全部来自票 24 的 `/api/script-library…` 回包，前端只把它们画出来（D30 的删除执行在
 * 服务端；这里只负责在删之前问一句、在删之后把面板标红）。
 *
 * 三条硬约束：
 * 1. **节点图不在这里画**：`scriptGraphRender(container, {mode: "editor", layer, nodes, edges,
 *    width, onNodeClick})` 是票 21 的唯一一份画法（D26）；本文件只把库里的大节节点**映射**
 *    成棋盘体形状（节点 → 房间对象）再喂进去，作者视角因此自动没有玩家状态色。
 * 2. **确认框不在这里发明**：删除走 `script-lobby.js:scriptConfirm` 的同一个模态壳
 *    （`data-dialog="editor-delete"`），共享引用的按钮是「取消」/「仍然删除」——第二个按钮
 *    必须存在（D30 用户 2026-11-09 裁定）。
 * 3. **删除分两类**（D30）：归属（剧本 → 大节、大节 → 节点 / 边）删除**级联、永不拒绝**；
 *    共享引用（房间 / 模板 / 技能池 / 事件 / 事件池 / 全局技能）删除**照做**，被引用点
 *    列出来但只警告——删完校验面板立刻红（红字带 `loc`）。
 *
 * 位置纪律：编辑器的视图与脚本都排在新视图 / 新脚本的最后（回放视图之后）；注释里不写
 * 那两个 `main` 的 id 字面量（烟测拿它们作字符串切片的锚点）。
 */
"use strict";

/* ---------------------------------------------------------------- 常量 */

/** 左栏的九类实体（`spec §4` 的落点表 + 票 05 UI-5；边不是左栏实体，它住大节页）。 */
const SCRIPT_EDITOR_KINDS = [
  { key: "scripts", label: "剧本" },
  { key: "layers", label: "大节" },
  { key: "nodes", label: "节点" },
  { key: "rooms", label: "房间" },
  { key: "templates", label: "敌人模板" },
  { key: "skill_pools", label: "技能池" },
  { key: "events", label: "事件" },
  { key: "event_pools", label: "事件池" },
  { key: "global_skills", label: "全局技能" },
];

const SCRIPT_EDITOR_KIND_LABELS = {
  scripts: "剧本",
  layers: "大节",
  nodes: "节点",
  rooms: "房间",
  templates: "敌人模板",
  skill_pools: "技能池",
  events: "事件",
  event_pools: "事件池",
  global_skills: "全局技能",
};

/** D30 那句原话（共享引用删除确认框里必须出现；烟测逐字断）。 */
const SCRIPT_EDITOR_DELETE_WARNING = "继续删除会让它们悬空（校验面板标红）";

/** 右栏三页签（ui-one-screen 票 07 / spec A10）：`ed.tab` 只许取这三个值之一。 */
const SCRIPT_EDITOR_TABS = ["detail", "layer", "valid"];

/** 字段挂载根 → 页签（ui-one-screen 票 09 / review 修复 B）：`loc` 指向的字段 / 控件挂在
 * 哪个根下，它就住哪块 pane —— 跳转的页签按这个切，**不按 `loc` 字符串前缀猜**。
 *
 * 与 `index.html` 三块 `.ed-pane[data-pane]` 的归属同一条口径（`test_web_static` 逐条核）：
 * 节点的 row/col/room 等输入框住 `#script-editor-form` ⇒「详情」；网格 / 边列表住「大节」；
 * 校验条目住「校验」。图上的节点没有字段，但它在 `#script-editor-graph` 里 ⇒「大节」。
 */
const SCRIPT_EDITOR_PANE_ROOTS = [
  ["script-editor-form", "detail"],
  ["script-editor-graph", "layer"],
  ["script-editor-edges", "layer"],
  ["script-editor-errors", "valid"],
];

/** 归属表（删除级联、永不拒绝）：剧本归库、大节 / 节点归剧本。 */
const SCRIPT_EDITOR_OWNED_KINDS = ["scripts", "layers", "nodes"];

/** 五个房间类型（与 `script-board.js` 的中文标签同词；这里只用于下拉框）。 */
const SCRIPT_EDITOR_ROOM_KINDS = [
  { value: "story", label: "剧情战斗" },
  { value: "battle", label: "遭遇战" },
  { value: "event", label: "事件" },
  { value: "shop", label: "商店" },
  { value: "unknown", label: "问号房" },
];

/** 四种胜负条件（`PveVictory` 的闭集）。 */
const SCRIPT_EDITOR_VICTORIES = [
  { value: "rout", label: "全歼敌人" },
  { value: "survive_rounds", label: "存活到第 N 轮之后" },
  { value: "rout_within_rounds", label: "N 轮之内全歼" },
  { value: "slay_boss", label: "击败 Boss 座位" },
];

/** 副本全局技能的三档属性区间（`Scale` 的字段表）。 */
const SCRIPT_EDITOR_SCALE_FIELDS = ["hp", "draw", "slash"];

/* ---------------------------------------------------------------- 状态 */

/** 编辑器本地态（`state.script.editor` 的唯一初始化点；全是显示态，没有规则判据）。 */
function scriptEditorState() {
  if (!state.script.editor) {
    state.script.editor = {
      loaded: false,
      source: null,
      revision: null,
      library: null,
      errors: [],
      warnings: [],
      kind: "scripts",
      scriptId: null,
      layerId: null,
      selected: null,
      mode: null,      // null | "edit" | "new" | "rename"
      draft: null,     // 正在编辑 / 新建的那一条（整条实体的深拷贝）
      drag: null,      // 拖拽中的节点 id
      focusLoc: "",    // 点校验条目之后定位到的字段位置
      // ui-one-screen 票 07：右栏页签（详情 / 大节 / 校验）——一次只显示一块 pane。
      tab: "detail",
      hint: "",
      error: "",
    };
  }
  return state.script.editor;
}

/** 库里的七张共享表 + 剧本表；大节 / 节点 / 边是剧本归属，另走 `scriptEditorTable`。 */
function scriptEditorLibrary() {
  const ed = scriptEditorState();
  return ed.library || null;
}

/** 当前剧本（`state.script.editor.scriptId` 的那一条）。 */
function scriptEditorScript() {
  const library = scriptEditorLibrary();
  if (!library) return null;
  const ed = scriptEditorState();
  return (library.scripts || []).find((script) => script.id === ed.scriptId) || null;
}

/** 当前大节（当前剧本 `layers` 里那一条）。 */
function scriptEditorLayer() {
  const script = scriptEditorScript();
  if (!script) return null;
  const ed = scriptEditorState();
  return (script.layers || []).find((layer) => layer.id === ed.layerId) || null;
}

/** 当前 kind 的列表进料：归属项从剧本 / 大节里取，其余从库表里取。 */
function scriptEditorTable(kind) {
  const library = scriptEditorLibrary();
  if (!library) return [];
  if (kind === "layers") return (scriptEditorScript() || {}).layers || [];
  if (kind === "nodes") return (scriptEditorLayer() || {}).nodes || [];
  if (kind === "edges") return (scriptEditorLayer() || {}).edges || [];
  return library[kind] || [];
}

/** 按 id 找当前 kind 的一条（边没有 id，故不支持）。 */
function scriptEditorEntity(kind, id) {
  if (!id) return null;
  return scriptEditorTable(kind).find((item) => item.id === id) || null;
}

function scriptEditorSelectedEntity() {
  const ed = scriptEditorState();
  return scriptEditorEntity(ed.kind, ed.selected);
}

/** 一个节点引用的房间 → 渲染器认得的房间对象（缺引用时退 id，不崩）。 */
function scriptEditorRoomInfo(roomId) {
  const library = scriptEditorLibrary();
  const room = ((library && library.rooms) || []).find((item) => item.id === roomId);
  if (!room) return { id: roomId, name: roomId, kind: "unknown", is_boss: false };
  return { id: room.id, name: room.name, kind: room.kind, is_boss: Boolean(room.is_boss) };
}

/** 一条实体在**库 JSON 指针**里的位置（校验面板的 `loc` 与表单字段对得上）。 */
function scriptEditorIndex(kind, id) {
  const table = scriptEditorTable(kind);
  return table.findIndex((item) => item.id === id);
}

/** 字段在库指针里的绝对路径：房间 / 模板等是 `/<表>/<下标>/<字段>`，节点另带大节前缀。 */
function scriptEditorFieldPath(kind, id, suffix) {
  const ed = scriptEditorState();
  const tail = suffix || "";
  const withTail = (base) => (tail ? `${base}/${tail}` : base);
  if (kind === "scripts") {
    return withTail(`/scripts/${scriptEditorIndex("scripts", id)}`);
  }
  if (kind === "layers") {
    const scriptIndex = (scriptEditorLibrary().scripts || [])
      .findIndex((script) => script.id === ed.scriptId);
    return withTail(`/scripts/${scriptIndex}/layers/${scriptEditorIndex("layers", id)}`);
  }
  if (kind === "nodes") {
    const library = scriptEditorLibrary();
    const scriptIndex = (library.scripts || [])
      .findIndex((script) => script.id === ed.scriptId);
    const layerIndex = ((scriptEditorScript() || {}).layers || [])
      .findIndex((layer) => layer.id === ed.layerId);
    return withTail(`/scripts/${scriptIndex}/layers/${layerIndex}/nodes/${scriptEditorIndex("nodes", id)}`);
  }
  const index = scriptEditorIndex(kind, id);
  return withTail(`/${kind}/${index}`);
}

/* ------------------------------------------------------------ 读 / 收下 */

/** 把 `/api/script-library…` 的一个回包收进本地态（读、写、删共用同一个出口）。
 *
 * 回包形状（票 24）：`{revision, source, library, errors, warnings, entity?}`；409 的
 * 冲突体是同一份库 + `error`。**只用回包里的 `library` / `errors`**——前端不镜像第二份。
 */
function scriptEditorAdopt(body) {
  const ed = scriptEditorState();
  if (body && body.library) ed.library = body.library;
  if (body && typeof body.revision === "string") ed.revision = body.revision;
  if (body && typeof body.source === "string") ed.source = body.source;
  if (body && Array.isArray(body.errors)) ed.errors = body.errors;
  ed.warnings = body && Array.isArray(body.warnings) ? body.warnings : [];
  if (body && body.entity && body.entity.id) {
    // 写端点的返回体带落盘后的那一条（剧本 version 已 +1）：把它给调用方的"落定"用。
    ed.saved = body.entity;
  }
  scriptEditorClamp();
  return ed;
}

/** 收下新库之后把选择钳回合法范围（换库 / 删掉选中项都不许留下悬空的 UI 指针）。 */
function scriptEditorClamp() {
  const ed = scriptEditorState();
  const library = scriptEditorLibrary();
  if (!library) return;
  const scripts = library.scripts || [];
  let script = scripts.find((item) => item.id === ed.scriptId) || null;
  if (!script) {
    script = scripts[0] || null;
    ed.scriptId = script ? script.id : null;
    ed.layerId = script && (script.layers || [])[0] ? script.layers[0].id : null;
  } else if (!ed.layerId) {
    // 换库 / 删掉当前大节之后：大节页要有个默认落点（否则「大节 / 节点」左栏会是空的）。
    ed.layerId = (script.layers || [])[0] ? script.layers[0].id : null;
  } else if (!(script.layers || []).some((layer) => layer.id === ed.layerId)) {
    ed.layerId = (script.layers || [])[0] ? script.layers[0].id : null;
  }
  const kind = ed.kind;
  let table = scriptEditorTable(kind);
  if (kind === "edges") table = [];
  if (ed.selected && !table.some((item) => item.id === ed.selected)) {
    ed.selected = null;
    if (ed.mode !== "new") {
      ed.mode = null;
      ed.draft = null;
    }
  }
  if (!sListHasKind(kind)) ed.kind = "scripts";
  return ed;
}

function sListHasKind(kind) {
  return SCRIPT_EDITOR_KINDS.some((entry) => entry.key === kind);
}

/** 读一次整库（`GET /api/script-library`）：编辑器一次拿全，写完之后不必再 GET。 */
async function scriptEditorLoad() {
  const ed = scriptEditorState();
  let body = null;
  try {
    body = await api("/api/script-library");
  } catch (error) {
    ed.error = `读库失败：${error.message}`;
    scriptEditorRender();
    return null;
  }
  ed.error = "";
  ed.hint = "";
  scriptEditorAdopt(body);
  ed.loaded = true;
  if (!ed.selected && ed.kind === "scripts") ed.selected = ed.scriptId;
  scriptEditorRender();
  return body;
}

/** 重新读库（顶栏「重读库」）：坏库照开（D30 的编辑器宽松档）。 */
async function scriptEditorReload() {
  return scriptEditorLoad();
}

/* -------------------------------------------------------------- 渲染 */

/** 产品侧的小工具（不借测试垫片的任何东西）。 */
function scriptEditorEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function scriptEditorWalk(node, out) {
  const found = out || [];
  for (const child of (node && node.children) || []) {
    found.push(child);
    scriptEditorWalk(child, found);
  }
  return found;
}

function scriptEditorByClass(root, name) {
  return scriptEditorWalk(root, []).filter((node) => node.classList.contains(name));
}

function scriptEditorRow(...children) {
  const row = scriptEditorEl("div", "row");
  for (const child of children) if (child) row.appendChild(child);
  return row;
}

function scriptEditorField(label, control) {
  const wrap = scriptEditorEl("div", "script-editor-field");
  wrap.appendChild(scriptEditorEl("span", "dim", label));
  wrap.appendChild(control);
  return wrap;
}

function scriptEditorValue(id) {
  const node = $(id);
  return node ? String(node.value === undefined || node.value === null ? "" : node.value) : "";
}

function scriptEditorNumber(id, fallback) {
  const raw = scriptEditorValue(id).trim();
  const value = Number(raw);
  return raw !== "" && Number.isFinite(value) ? value : fallback;
}

function scriptEditorChecked(id) {
  const node = $(id);
  return Boolean(node && node.checked);
}

function scriptEditorList(text) {
  return String(text || "")
    .split(/[,，、\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function scriptEditorCopy(value) {
  return value === undefined || value === null ? value : JSON.parse(JSON.stringify(value));
}

function scriptEditorInput(id, value, options) {
  const spec = options || {};
  const input = document.createElement("input");
  input.type = spec.number ? "number" : "text";
  input.id = id;
  input.value = value === undefined || value === null ? "" : String(value);
  if (spec.number) {
    input.min = String(spec.min === undefined ? 0 : spec.min);
    if (spec.max !== undefined) input.max = String(spec.max);
  }
  if (spec.path) input.setAttribute("data-field-path", spec.path);
  if (spec.disabled) input.disabled = true;
  return input;
}

function scriptEditorCheckbox(id, checked, path) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = id;
  input.checked = Boolean(checked);
  if (path) input.setAttribute("data-field-path", path);
  return input;
}

function scriptEditorSelect(id, options, value, path) {
  const select = document.createElement("select");
  select.id = id;
  for (const option of options || []) {
    const node = document.createElement("option");
    node.value = String(option.value);
    node.textContent = String(option.label === undefined ? option.value : option.label);
    select.appendChild(node);
  }
  const values = (options || []).map((option) => String(option.value));
  const chosen = values.includes(String(value)) ? String(value) : (values[0] || "");
  select.value = chosen;
  if (path) select.setAttribute("data-field-path", path);
  return select;
}

function scriptEditorButton(id, label, className) {
  const button = scriptEditorEl("button", className || "", label);
  button.type = "button";
  if (id) button.id = id;
  return button;
}

function scriptEditorOptions(kind, options) {
  const spec = options || {};
  const table = scriptEditorTable(kind);
  return table
    .filter((item) => !spec.skip || !spec.skip(item))
    .map((item) => ({
      value: item.id,
      label: `${item.name || item.id}（${item.id}）`,
    }));
}

/** 画整屏：左栏（类 / 列表）+ 右栏（表单 / 剧本结构 / 大节页 / 校验面板）。 */
function scriptEditorRender() {
  const ed = scriptEditorState();
  if (ed.library) scriptEditorClamp();
  const library = scriptEditorLibrary();
  const script = scriptEditorScript();
  const layer = scriptEditorLayer();

  const crumb = $("script-editor-crumb");
  if (crumb) {
    crumb.textContent = library
      ? `${script ? `《${script.name}》` : "剧本库"}${layer ? ` · ${layer.name}` : ""}`
      : "剧本库";
  }
  const source = $("script-editor-source");
  if (source) {
    source.textContent = ed.source === "builtin" ? "当前读的是内置库（只读进料；一次写入会落成用户库）"
      : ed.source === "user" ? "当前读的是用户库（runs/script-library.json）"
        : "";
  }
  scriptEditorRenderKinds();
  scriptEditorRenderList();
  scriptEditorRenderToolbar();
  scriptEditorRenderMessages();
  scriptEditorRenderForm();
  scriptEditorRenderTree();
  scriptEditorRenderLayerPage();
  scriptEditorRenderValidation();
  scriptEditorSyncTabs();
  return null;
}

/** 页签落到 DOM 的唯一落点：`ed.tab` → 三个键的 `.on` / `aria-selected` + 三块 pane 的
 * `hidden`（一次只显示一块）。
 *
 * 整屏重绘每帧都调它，故"页签状态"没有第二份：首帧、点页签键、校验跳转都走这一条。
 */
function scriptEditorSyncTabs() {
  const ed = scriptEditorState();
  const tab = SCRIPT_EDITOR_TABS.includes(ed.tab) ? ed.tab : "detail";
  ed.tab = tab;
  for (const key of SCRIPT_EDITOR_TABS) {
    const button = $(`script-editor-tab-${key}`);
    if (button) {
      const on = key === tab;
      button.classList.toggle("on", on);
      button.setAttribute("aria-selected", on ? "true" : "false");
    }
    const pane = $(`script-editor-pane-${key}`);
    if (pane) pane.hidden = key !== tab;
  }
  return tab;
}

/** 点页签键：**先把当前 pane 的输入收进草稿**（不落盘），再落 `ed.tab` 重绘（票 09）。
 *
 * 重绘用 `ed.draft` 铺表单，而文本框的值只在保存时才经 `scriptEditorCollect` 回写；
 * 不收就切页签 = 「详情里改了字再点大节」静默丢掉改动。
 */
function scriptEditorShowTab(key) {
  const ed = scriptEditorState();
  scriptEditorStashDraft();
  ed.tab = SCRIPT_EDITOR_TABS.includes(key) ? key : "detail";
  scriptEditorRender();
  return ed.tab;
}

/** 把当前 pane 的输入收进草稿，**不落盘**（保存仍是 `scriptEditorSave` 的事）。
 *
 * 只在真有草稿（编辑 / 新建）时收：别的态表单里没有可读的输入，读出来是空值，
 * 会把选中实体冲成一条空实体。
 */
function scriptEditorStashDraft() {
  const ed = scriptEditorState();
  if (!ed.draft || (ed.mode !== "edit" && ed.mode !== "new")) return ed.draft;
  ed.draft = scriptEditorCollect(ed.kind, ed.draft);
  return ed.draft;
}

function scriptEditorRenderKinds() {
  const box = $("script-editor-kinds");
  if (!box) return null;
  const ed = scriptEditorState();
  box.innerHTML = "";
  for (const entry of SCRIPT_EDITOR_KINDS) {
    const button = scriptEditorEl("button", "script-editor-kind", entry.label);
    button.type = "button";
    button.setAttribute("data-kind", entry.key);
    if (ed.kind === entry.key) button.classList.add("on");
    button.addEventListener("click", () => scriptEditorOpenKind(entry.key));
    box.appendChild(button);
  }
  return null;
}

function scriptEditorRenderList() {
  const box = $("script-editor-list");
  if (!box) return null;
  const ed = scriptEditorState();
  const items = scriptEditorTable(ed.kind);
  box.innerHTML = "";
  for (const item of items) {
    const label = scriptEditorRowLabel(ed.kind, item);
    const row = scriptEditorEl("button", "script-editor-row", label);
    row.type = "button";
    row.setAttribute("data-kind", ed.kind);
    row.setAttribute("data-id", String(item.id));
    if (ed.selected === item.id) row.classList.add("on");
    row.addEventListener("click", () => scriptEditorChoose(ed.kind, item.id));
    box.appendChild(row);
  }
  if (!items.length) {
    box.appendChild(scriptEditorEl("p", "dim", ed.kind === "layers"
      ? "这个剧本还没有大节：点「新建」。"
      : ed.kind === "nodes" ? "这个大节还没有节点：点「新建」或在大节页拖放。"
        : "这张表是空的：点「新建」。"));
  }
  const scope = $("script-editor-scope");
  if (scope) {
    const script = scriptEditorScript();
    const layer = scriptEditorLayer();
    const bits = [`${SCRIPT_EDITOR_KIND_LABELS[ed.kind] || ed.kind} ${items.length} 条`];
    if (script && (ed.kind === "layers" || ed.kind === "nodes")) bits.push(`剧本：${script.name}`);
    if (layer && ed.kind === "nodes") bits.push(`大节：${layer.name}`);
    scope.textContent = bits.join(" · ");
  }
  return null;
}

/** 列表行的文案：名字 + id（名字可能重复，id 才是稳定身份）。 */
function scriptEditorRowLabel(kind, item) {
  if (kind === "layers") {
    const grid = item.grid || {};
    return `大节 ${item.name} · ${grid.rows || "?"}×${grid.cols || "?"}（${item.id}）`;
  }
  if (kind === "nodes") {
    return `${item.id} · ${scriptEditorRoomInfo(item.room).name}（${item.room}）`;
  }
  if (kind === "edges") {
    return `${item.from} → ${item.to}`;
  }
  if (kind === "rooms") return `${item.name}（${item.id}）· ${item.kind}`;
  return `${item.name || item.id}（${item.id}）`;
}

function scriptEditorRenderToolbar() {
  const ed = scriptEditorState();
  const set = (id, disabled) => {
    const node = $(id);
    if (node) node.disabled = Boolean(disabled);
  };
  const hasSelected = Boolean(scriptEditorSelectedEntity());
  set("btn-script-editor-new", !ed.library);
  set("btn-script-editor-edit", !hasSelected);
  set("btn-script-editor-clone", !hasSelected);
  set("btn-script-editor-rename", !hasSelected);
  set("btn-script-editor-delete", !hasSelected);
  return null;
}

function scriptEditorRenderMessages() {
  const ed = scriptEditorState();
  const hint = $("script-editor-hint");
  if (hint) {
    hint.textContent = ed.hint
      || (ed.errors.length
        ? `${ed.errors.length} 条校验错误（点条目跳到对应字段）。`
        : "库是干净的（校验面板与加载器同一份校验函数）。");
  }
  const error = $("script-editor-error");
  if (error) {
    error.hidden = !ed.error;
    error.textContent = ed.error || "";
  }
  const focus = $("script-editor-focus");
  if (focus) {
    focus.hidden = !ed.focusLoc;
    focus.textContent = ed.focusLoc ? `已定位：${ed.focusLoc}` : "";
  }
  return null;
}

function scriptEditorRenderValidation() {
  const box = $("script-editor-errors");
  const clean = $("script-editor-clean");
  const count = $("script-editor-validation-count");
  if (!box) return null;
  const ed = scriptEditorState();
  const errors = ed.errors || [];
  box.innerHTML = "";
  for (const error of errors) {
    const row = scriptEditorEl("button", "script-editor-error");
    row.type = "button";
    row.setAttribute("data-loc", String(error.loc || ""));
    row.setAttribute("data-rule-id", String(error.rule_id || ""));
    // 规则 id 原样显示（`script_library.*` 是加载器 / 票 24 的同一批判据），前端不翻译。
    row.textContent = `${error.loc || "（没有 loc）"} · ${error.rule_id || ""}`
      + `：期望 ${error.expected || "?"}，实得 ${error.got || "?"}`;
    row.addEventListener("click", () => scriptEditorJump(error.loc));
    box.appendChild(row);
  }
  if (clean) clean.hidden = errors.length > 0;
  if (count) count.textContent = errors.length ? `共 ${errors.length} 条` : "";
  return null;
}

/* -------------------------------------------------------------- 表单 */

/** 一条实体的"新建"缺省值（模型字段齐全，免得新建时被 400 挡住）。 */
function scriptEditorDefaultDraft(kind) {
  const ed = scriptEditorState();
  const library = scriptEditorLibrary() || {};
  const firstRoom = ((library.rooms || [])[0] || {}).id || "";
  const firstPool = ((library.skill_pools || [])[0] || {}).id || "";
  const firstEventPool = ((library.event_pools || [])[0] || {}).id || "";
  switch (kind) {
    case "scripts":
      return { id: "", name: "新剧本", version: 1, roster: [], comment: "", layers: [] };
    case "layers":
      return {
        id: "", name: "新大节", grid: { rows: 3, cols: 4 },
        nodes: [], edges: [], default_scaling: [],
      };
    case "nodes":
      return { id: "", row: 0, col: 0, room: firstRoom };
    case "rooms":
      return { id: "", name: "新房间", kind: "shop", text: "" };
    case "templates":
      return {
        id: "", name: "新模板", band: "early", hp: 3, hp_max: 3,
        initial_skills: [], random: { pool: firstPool, count: [0, 0] },
        reward: { coins: 0, pick1: 0, pick2: 0, pick3: 0 },
      };
    case "skill_pools":
      return { id: "", name: "新技能池", skills: [] };
    case "events":
      return { id: "", name: "新事件", text: "", options: [] };
    case "event_pools":
      return { id: "", name: "新事件池", events: firstEventPool ? [firstEventPool] : [] };
    case "global_skills":
      return {
        id: "", name: "新全局技能", side: "enemy",
        scale: { hp: [1, 1], draw: [1, 1], slash: [1, 1] },
      };
    default:
      return { id: "" };
  }
}

function scriptEditorRenderForm() {
  const box = $("script-editor-form");
  if (!box) return null;
  const ed = scriptEditorState();
  box.innerHTML = "";
  box.removeAttribute("data-focus-loc");
  if (ed.mode === "rename") {
    box.appendChild(scriptEditorRenameForm());
    return null;
  }
  if (ed.mode === "new" || ed.mode === "edit") {
    if (ed.mode === "new" && !ed.draft) ed.draft = scriptEditorDefaultDraft(ed.kind);
    if (!ed.draft) return null;
    const body = scriptEditorEl("div", "script-editor-form-body");
    body.setAttribute("data-kind", ed.kind);
    body.setAttribute("data-mode", ed.mode);
    if (ed.mode === "new") {
      body.appendChild(scriptEditorField(
        "id（稳定引用名）",
        scriptEditorInput("script-editor-field-id", ed.draft.id, {
          path: scriptEditorFieldPath(ed.kind, ed.draft.id, "id"),
        }),
      ));
    }
    scriptEditorFillForm(body, ed.kind, ed.draft);
    const save = scriptEditorButton("btn-script-editor-save", "保存", "primary");
    save.addEventListener("click", () => scriptEditorSave());
    const cancel = scriptEditorButton("btn-script-editor-cancel", "取消", "ghost");
    cancel.addEventListener("click", () => scriptEditorCancel());
    body.appendChild(scriptEditorRow(save, cancel));
    box.appendChild(body);
    return null;
  }
  // 没有在编辑：给一条只读摘要（点「编辑」才进表单）。
  const entity = scriptEditorSelectedEntity();
  if (!entity) {
    box.appendChild(scriptEditorEl("p", "dim",
      ed.kind === "edges"
        ? "边住在大节页（下方）：画边 / 改条件 / 解除都在那里。"
        : "选左栏的一条，或点「新建」。"));
    return null;
  }
  const summary = scriptEditorEl("div", "script-editor-summary");
  summary.appendChild(scriptEditorEl("h3", "", `${SCRIPT_EDITOR_KIND_LABELS[ed.kind]} · ${entity.id}`));
  summary.appendChild(scriptEditorEl("p", "dim",
    scriptEditorRowLabel(ed.kind, entity)));
  summary.appendChild(scriptEditorEl("p", "dim", "点「编辑」改它（副本 / 重命名 / 删除也在上面）。"));
  box.appendChild(summary);
  return null;
}

/** 按 kind 铺字段（共用：所有输入都带 `data-field-path`，校验面板据此跳字段）。 */
function scriptEditorFillForm(body, kind, draft) {
  const ed = scriptEditorState();
  const path = (suffix) => scriptEditorFieldPath(kind, draft.id, suffix);
  if (kind === "scripts") {
    body.appendChild(scriptEditorField("名称", scriptEditorInput(
      "script-editor-field-name", draft.name, { path: path("name") })));
    for (let index = 0; index < 3; index += 1) {
      body.appendChild(scriptEditorField(
        `初始角色 ${index + 1}（武将 id）`,
        scriptEditorInput(`script-editor-field-roster-${index}`,
          (draft.roster || [])[index] || "", { path: path(`roster/${index}`) }),
      ));
    }
    body.appendChild(scriptEditorField("备注", scriptEditorInput(
      "script-editor-field-comment", draft.comment, { path: path("comment") })));
    return;
  }
  if (kind === "layers") {
    body.appendChild(scriptEditorField("名称", scriptEditorInput(
      "script-editor-field-name", draft.name, { path: path("name") })));
    body.appendChild(scriptEditorEl("p", "dim",
      "网格长宽在大节页改（它会重画节点图）；节点 / 边不在这里填。"));
    return;
  }
  if (kind === "nodes") {
    body.appendChild(scriptEditorField("行", scriptEditorInput(
      "script-editor-field-row", draft.row, { number: true, min: 0, path: path("row") })));
    body.appendChild(scriptEditorField("列", scriptEditorInput(
      "script-editor-field-col", draft.col, { number: true, min: 0, path: path("col") })));
    body.appendChild(scriptEditorField("房间（引用选择器）", scriptEditorSelect(
      "script-editor-field-room", scriptEditorOptions("rooms"), draft.room, path("room"))));
    return;
  }
  if (kind === "rooms") {
    scriptEditorFillRoomForm(body, draft);
    return;
  }
  if (kind === "templates") {
    scriptEditorFillTemplateForm(body, draft);
    return;
  }
  if (kind === "skill_pools") {
    body.appendChild(scriptEditorField("名称", scriptEditorInput(
      "script-editor-field-name", draft.name, { path: path("name") })));
    const box = scriptEditorEl("div", "script-editor-skills");
    for (const [index, skill] of (draft.skills || []).entries()) {
      const row = scriptEditorEl("div", "script-editor-skill-row");
      row.setAttribute("data-skill", String(skill));
      row.appendChild(scriptEditorEl("code", "", skill));
      const remove = scriptEditorButton("", "删", "ghost");
      remove.addEventListener("click", () => {
        ed.draft = scriptEditorCollect(kind, ed.draft);
        (ed.draft.skills || []).splice(index, 1);
        scriptEditorRender();
      });
      row.appendChild(remove);
      box.appendChild(row);
    }
    body.appendChild(box);
    const skillInput = scriptEditorInput("script-editor-field-skill", "", {});
    const addButton = scriptEditorButton("btn-script-editor-skill-add", "添加技能", "ghost");
    addButton.addEventListener("click", () => {
      ed.draft = scriptEditorCollect(kind, ed.draft);
      const value = scriptEditorValue("script-editor-field-skill").trim();
      if (value) (ed.draft.skills || []).push(value);
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(skillInput, addButton));
    return;
  }
  if (kind === "events") {
    scriptEditorFillEventForm(body, draft);
    return;
  }
  if (kind === "event_pools") {
    body.appendChild(scriptEditorField("名称", scriptEditorInput(
      "script-editor-field-name", draft.name, { path: path("name") })));
    const box = scriptEditorEl("div", "script-editor-refs");
    for (const [index, eventId] of (draft.events || []).entries()) {
      const row = scriptEditorEl("div", "script-editor-ref-row");
      row.setAttribute("data-ref", String(eventId));
      row.appendChild(scriptEditorSelect(
        `script-editor-event-pool-event-${index}`,
        scriptEditorOptions("events"), eventId, path(`events/${index}`)));
      const remove = scriptEditorButton("", "删", "ghost");
      remove.addEventListener("click", () => {
        ed.draft = scriptEditorCollect(kind, ed.draft);
        (ed.draft.events || []).splice(index, 1);
        scriptEditorRender();
      });
      row.appendChild(remove);
      box.appendChild(row);
    }
    body.appendChild(box);
    const add = scriptEditorButton("btn-script-editor-event-add", "添加事件", "ghost");
    add.addEventListener("click", () => {
      ed.draft = scriptEditorCollect(kind, ed.draft);
      const options = scriptEditorOptions("events");
      if (options.length) (ed.draft.events || []).push(options[0].value);
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(add));
    return;
  }
  if (kind === "global_skills") {
    body.appendChild(scriptEditorField("名称", scriptEditorInput(
      "script-editor-field-name", draft.name, { path: path("name") })));
    body.appendChild(scriptEditorField("生效侧", scriptEditorSelect(
      "script-editor-field-side",
      [
        { value: "ally", label: "友方" },
        { value: "enemy", label: "敌方" },
        { value: "all", label: "双方" },
      ],
      draft.side, path("side"))));
    const scale = draft.scale || {};
    for (const field of SCRIPT_EDITOR_SCALE_FIELDS) {
      const span = scale[field] || [1, 1];
      body.appendChild(scriptEditorField(`${field} 区间（min / max）`, scriptEditorRow(
        scriptEditorInput(`script-editor-field-scale-${field}-min`, span[0],
          { number: true, min: 0, path: path(`scale/${field}/0`) }),
        scriptEditorInput(`script-editor-field-scale-${field}-max`, span[1],
          { number: true, min: 0, path: path(`scale/${field}/1`) }),
      )));
    }
  }
}

/* -- 房间表单：房间级字段 + 事件池 / 问号池 / 战斗编排（引用选择器） -------- */

function scriptEditorFillRoomForm(body, draft) {
  const ed = scriptEditorState();
  const path = (suffix) => scriptEditorFieldPath("rooms", draft.id, suffix);
  body.appendChild(scriptEditorField("名称", scriptEditorInput(
    "script-editor-field-name", draft.name, { path: path("name") })));
  const kindSelect = scriptEditorSelect("script-editor-field-kind",
    SCRIPT_EDITOR_ROOM_KINDS, draft.kind, path("kind"));
  kindSelect.addEventListener("change", () => {
    scriptEditorChangeRoomKind(kindSelect.value);
  });
  body.appendChild(scriptEditorField("类型", kindSelect));
  body.appendChild(scriptEditorField("说明", scriptEditorInput(
    "script-editor-field-text", draft.text, { path: path("text") })));
  body.appendChild(scriptEditorField("Boss 房", scriptEditorCheckbox(
    "script-editor-field-boss", draft.is_boss, path("is_boss"))));

  if (draft.kind === "event") {
    body.appendChild(scriptEditorField("事件池（引用选择器）", scriptEditorSelect(
      "script-editor-field-event-pool",
      scriptEditorOptions("event_pools"), draft.event_pool, path("event_pool"))));
  }
  if (draft.kind === "unknown") {
    body.appendChild(scriptEditorEl("h3", "", "问号池（加权；引用的房间选择器禁选 unknown）"));
    const pool = scriptEditorEl("div", "script-editor-pools");
    (draft.pool || []).forEach((entry, index) => {
      const row = scriptEditorEl("div", "script-editor-pool-row");
      row.setAttribute("data-room", String(entry.room));
      const roomSelect = scriptEditorSelect(
        `script-editor-pool-room-${index}`,
        scriptEditorOptions("rooms", { skip: (room) => room.kind === "unknown" }),
        entry.room, path(`pool/${index}/room`));
      roomSelect.className = "script-editor-pool-room";
      row.appendChild(roomSelect);
      row.appendChild(scriptEditorEl("span", "dim", "权重"));
      const weightInput = scriptEditorInput(`script-editor-pool-weight-${index}`, entry.weight, {
        number: true, min: 1, path: path(`pool/${index}/weight`),
      });
      weightInput.className = "script-editor-pool-weight";
      row.appendChild(weightInput);
      const remove = scriptEditorButton("", "删", "ghost");
      remove.addEventListener("click", () => {
        ed.draft = scriptEditorCollect("rooms", ed.draft);
        (ed.draft.pool || []).splice(index, 1);
        scriptEditorRender();
      });
      row.appendChild(remove);
      pool.appendChild(row);
    });
    body.appendChild(pool);
    const add = scriptEditorButton("btn-script-editor-pool-add", "加一项", "ghost");
    add.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      const options = scriptEditorOptions("rooms", { skip: (room) => room.kind === "unknown" });
      if (!(ed.draft.pool || []).length && options.length) ed.draft.pool = [];
      if (options.length) ed.draft.pool.push({ room: options[0].value, weight: 1 });
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(add));
  }
  if (draft.kind === "story" || draft.kind === "battle") {
    scriptEditorFillBattleForm(body, draft);
  }
}

function scriptEditorChangeRoomKind(value) {
  const ed = scriptEditorState();
  const draft = scriptEditorCollect("rooms", ed.draft);
  draft.kind = value;
  if (value === "story" || value === "battle") {
    draft.event_pool = null;
    draft.pool = null;
    if (!draft.battle) draft.battle = scriptEditorDefaultBattle();
  } else if (value === "event") {
    draft.battle = null;
    draft.pool = null;
    if (!draft.event_pool) draft.event_pool = (scriptEditorOptions("event_pools")[0] || {}).value || null;
  } else if (value === "unknown") {
    draft.battle = null;
    draft.event_pool = null;
    if (!Array.isArray(draft.pool)) draft.pool = [];
  } else {
    draft.battle = null;
    draft.event_pool = null;
    draft.pool = null;
  }
  ed.draft = draft;
  scriptEditorRender();
}

function scriptEditorDefaultBattle() {
  return {
    allies: [],
    enemies: { draw: { band: "early", count: [1, 1] } },
    victory: { kind: "rout" },
    global_skills: [],
    extra_reward: { coins: 0, pick1: 0, pick2: 0, pick3: 0 },
  };
}

function scriptEditorFillBattleForm(body, draft) {
  const ed = scriptEditorState();
  const path = (suffix) => scriptEditorFieldPath("rooms", draft.id, suffix);
  if (!draft.battle) {
    const add = scriptEditorButton("btn-script-editor-battle-add", "添加战斗编排", "ghost");
    add.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      ed.draft.battle = scriptEditorDefaultBattle();
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(add));
    return;
  }
  const battle = draft.battle;
  body.appendChild(scriptEditorEl("h3", "", "战斗编排（引用选择器：模板 / 技能池 / 全局技能）"));
  const victory = battle.victory || { kind: "rout" };
  const victorySelect = scriptEditorSelect("script-editor-field-victory",
    SCRIPT_EDITOR_VICTORIES, victory.kind, path("battle/victory/kind"));
  victorySelect.addEventListener("change", () => scriptEditorRender());
  body.appendChild(scriptEditorField("胜利条件", victorySelect));
  if (victory.kind === "survive_rounds" || victory.kind === "rout_within_rounds") {
    body.appendChild(scriptEditorField("轮数", scriptEditorInput(
      "script-editor-field-victory-rounds", victory.rounds || 3,
      { number: true, min: 1, path: path("battle/victory/rounds") })));
  }

  // 友方座位
  body.appendChild(scriptEditorEl("h3", "", "友方座位"));
  const allies = scriptEditorEl("div", "script-editor-seats");
  (battle.allies || []).forEach((seat, index) => {
    const row = scriptEditorEl("div", "script-editor-ally-row");
    const allySelect = scriptEditorSelect(
      `script-editor-battle-ally-template-${index}`,
      scriptEditorOptions("templates"), seat.template,
      path(`battle/allies/${index}/template`));
    allySelect.className = "script-editor-ally-template";
    row.appendChild(allySelect);
    const allyProtected = scriptEditorCheckbox(
      `script-editor-battle-ally-protected-${index}`, seat.protected,
      path(`battle/allies/${index}/protected`));
    allyProtected.className = "script-editor-ally-protected";
    row.appendChild(allyProtected);
    row.appendChild(scriptEditorEl("span", "dim", "受保护"));
    const remove = scriptEditorButton("", "删", "ghost");
    remove.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      (ed.draft.battle.allies || []).splice(index, 1);
      scriptEditorRender();
    });
    row.appendChild(remove);
    allies.appendChild(row);
  });
  body.appendChild(allies);
  const addAlly = scriptEditorButton("btn-script-editor-ally-add", "加友方", "ghost");
  addAlly.addEventListener("click", () => {
    ed.draft = scriptEditorCollect("rooms", ed.draft);
    const template = (scriptEditorOptions("templates")[0] || {}).value || null;
    ed.draft.battle.allies = [...(ed.draft.battle.allies || []),
      { role: "ally", template, protected: false, boss: false }];
    scriptEditorRender();
  });
  body.appendChild(scriptEditorRow(addAlly));

  // 敌方：固定座位数组，或按档抽（`draw`）。
  body.appendChild(scriptEditorEl("h3", "", "敌方座位"));
  if (Array.isArray(battle.enemies)) {
    const rows = scriptEditorEl("div", "script-editor-seats");
    battle.enemies.forEach((seat, index) => {
      const row = scriptEditorEl("div", "script-editor-enemy-row");
      const enemySelect = scriptEditorSelect(
        `script-editor-battle-enemy-template-${index}`,
        scriptEditorOptions("templates"), seat.template,
        path(`battle/enemies/${index}/template`));
      enemySelect.className = "script-editor-enemy-template";
      row.appendChild(enemySelect);
      const enemyBoss = scriptEditorCheckbox(
        `script-editor-battle-enemy-boss-${index}`, seat.boss,
        path(`battle/enemies/${index}/boss`));
      enemyBoss.className = "script-editor-enemy-boss";
      row.appendChild(enemyBoss);
      row.appendChild(scriptEditorEl("span", "dim", "Boss"));
      const remove = scriptEditorButton("", "删", "ghost");
      remove.addEventListener("click", () => {
        ed.draft = scriptEditorCollect("rooms", ed.draft);
        (ed.draft.battle.enemies || []).splice(index, 1);
        scriptEditorRender();
      });
      row.appendChild(remove);
      rows.appendChild(row);
    });
    body.appendChild(rows);
    const addEnemy = scriptEditorButton("btn-script-editor-enemy-add", "加敌人", "ghost");
    addEnemy.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      const template = (scriptEditorOptions("templates")[0] || {}).value || null;
      ed.draft.battle.enemies = [...(ed.draft.battle.enemies || []),
        { role: "enemy", template, protected: false, boss: false }];
      scriptEditorRender();
    });
    const toDraw = scriptEditorButton("btn-script-editor-enemies-draw", "改成按档抽", "ghost");
    toDraw.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      ed.draft.battle.enemies = { draw: { band: "early", count: [1, 1] } };
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(addEnemy, toDraw));
  } else {
    const draw = battle.enemies && battle.enemies.draw ? battle.enemies.draw : { band: "early", count: [1, 1] };
    body.appendChild(scriptEditorField("档位", scriptEditorInput(
      "script-editor-battle-band", draw.band, { path: path("battle/enemies/draw/band") })));
    body.appendChild(scriptEditorField("数量区间（min / max）", scriptEditorRow(
      scriptEditorInput("script-editor-battle-count-min", (draw.count || [1, 1])[0],
        { number: true, min: 0, path: path("battle/enemies/draw/count/0") }),
      scriptEditorInput("script-editor-battle-count-max", (draw.count || [1, 1])[1],
        { number: true, min: 0, path: path("battle/enemies/draw/count/1") }),
    )));
    const toFixed = scriptEditorButton("btn-script-editor-enemies-fixed", "改成固定座位", "ghost");
    toFixed.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      ed.draft.battle.enemies = [];
      scriptEditorRender();
    });
    body.appendChild(scriptEditorRow(toFixed));
  }

  // 全局技能（房间级引用）
  body.appendChild(scriptEditorEl("h3", "", "副本全局技能 · 房间级引用"));
  const globals = scriptEditorEl("div", "script-editor-globals");
  (battle.global_skills || []).forEach((reference, index) => {
    const row = scriptEditorEl("div", "script-editor-global-row");
    const globalSelect = scriptEditorSelect(
      `script-editor-battle-global-${index}`,
      scriptEditorOptions("global_skills"), reference.skill,
      path(`battle/global_skills/${index}/skill`));
    globalSelect.className = "script-editor-global-select";
    row.appendChild(globalSelect);
    const remove = scriptEditorButton("", "删", "ghost");
    remove.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("rooms", ed.draft);
      (ed.draft.battle.global_skills || []).splice(index, 1);
      scriptEditorRender();
    });
    row.appendChild(remove);
    globals.appendChild(row);
  });
  body.appendChild(globals);
  const addGlobal = scriptEditorButton("btn-script-editor-global-add", "加全局技能", "ghost");
  addGlobal.addEventListener("click", () => {
    ed.draft = scriptEditorCollect("rooms", ed.draft);
    const skill = (scriptEditorOptions("global_skills")[0] || {}).value;
    if (skill) {
      ed.draft.battle.global_skills = [...(ed.draft.battle.global_skills || []),
        { skill, scale: null }];
    }
    scriptEditorRender();
  });
  body.appendChild(scriptEditorRow(addGlobal));
}

/* -- 敌人模板表单：initial_skills（增删）与 random{pool,count} 并列 ------------- */

function scriptEditorFillTemplateForm(body, draft) {
  const ed = scriptEditorState();
  const path = (suffix) => scriptEditorFieldPath("templates", draft.id, suffix);
  body.appendChild(scriptEditorField("名称", scriptEditorInput(
    "script-editor-field-name", draft.name, { path: path("name") })));
  body.appendChild(scriptEditorField("档位", scriptEditorInput(
    "script-editor-field-band", draft.band, { path: path("band") })));
  body.appendChild(scriptEditorField("体力", scriptEditorInput(
    "script-editor-field-hp", draft.hp, { number: true, min: 1, path: path("hp") })));
  body.appendChild(scriptEditorField("体力上限", scriptEditorInput(
    "script-editor-field-hp_max", draft.hp_max, { number: true, min: 1, path: path("hp_max") })));

  const skills = scriptEditorEl("div", "script-editor-skills");
  for (const [index, skill] of (draft.initial_skills || []).entries()) {
    const row = scriptEditorEl("div", "script-editor-skill-row");
    row.setAttribute("data-skill", String(skill));
    row.appendChild(scriptEditorEl("code", "", skill));
    const remove = scriptEditorButton("", "删", "ghost");
    remove.className = "ghost script-editor-skill-remove";
    remove.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("templates", ed.draft);
      (ed.draft.initial_skills || []).splice(index, 1);
      scriptEditorRender();
    });
    row.appendChild(remove);
    skills.appendChild(row);
  }
  body.appendChild(scriptEditorField("initial_skills（可增删；固定技能，不进随机抽取）", skills));
  const skillInput = scriptEditorInput("script-editor-field-skill", "", {
    path: path("initial_skills"),
  });
  body.appendChild(scriptEditorField("加一条初始技能（技能 id）", skillInput));
  const addSkill = scriptEditorButton("btn-script-editor-skill-add", "添加", "ghost");
  addSkill.addEventListener("click", () => {
    ed.draft = scriptEditorCollect("templates", ed.draft);
    const value = scriptEditorValue("script-editor-field-skill").trim();
    if (value) (ed.draft.initial_skills || []).push(value);
    scriptEditorRender();
  });
  body.appendChild(scriptEditorRow(addSkill));

  // random{pool, count} 与 initial_skills 并列展示（票 05 UI-5）。
  const random = draft.random || { pool: "", count: [0, 0] };
  const count = random.count || [0, 0];
  body.appendChild(scriptEditorField("random.pool（引用选择器）", scriptEditorSelect(
    "script-editor-field-random-pool", scriptEditorOptions("skill_pools"),
    random.pool, path("random/pool"))));
  body.appendChild(scriptEditorField("random.count（min / max；[0, 0] = 固定对手）", scriptEditorRow(
    scriptEditorInput("script-editor-field-random-min", count[0],
      { number: true, min: 0, path: path("random/count/0") }),
    scriptEditorInput("script-editor-field-random-max", count[1],
      { number: true, min: 0, path: path("random/count/1") }),
  )));
  const hint = scriptEditorEl("p", "script-editor-random-hint dim");
  hint.id = "script-editor-random-hint";
  hint.textContent = Number(count[0]) === 0 && Number(count[1]) === 0
    ? "固定对手：count = [0, 0]（随机池为空的模板 = 固定对手，不抽随机技能）。"
    : `随机：从随机池按 count 抽 ${count[0]}–${count[1]} 条（无放回）。`;
  body.appendChild(hint);

  const reward = draft.reward || {};
  for (const field of ["coins", "pick1", "pick2", "pick3"]) {
    body.appendChild(scriptEditorField(`奖励 ${field}`, scriptEditorInput(
      `script-editor-field-reward-${field}`, reward[field] || 0,
      { number: true, min: 0, path: path(`reward/${field}`) })));
  }
}

/* -- 事件表单：文本 + 选项（条件与效果保留，效果不在这一票编辑） --------------- */

function scriptEditorFillEventForm(body, draft) {
  const ed = scriptEditorState();
  const path = (suffix) => scriptEditorFieldPath("events", draft.id, suffix);
  body.appendChild(scriptEditorField("名称", scriptEditorInput(
    "script-editor-field-name", draft.name, { path: path("name") })));
  body.appendChild(scriptEditorField("正文", scriptEditorInput(
    "script-editor-field-text", draft.text, { path: path("text") })));
  const box = scriptEditorEl("div", "script-editor-options");
  (draft.options || []).forEach((option, index) => {
    const row = scriptEditorEl("div", "script-editor-option-row");
    row.appendChild(scriptEditorInput(`script-editor-option-text-${index}`, option.text, {
      path: path(`options/${index}/text`),
    }));
    row.appendChild(scriptEditorEl("span", "dim", "需要"));
    row.appendChild(scriptEditorInput(`script-editor-option-flags-${index}`,
      ((option.requires || {}).flags || []).join(","),
      { path: path(`options/${index}/requires/flags`) }));
    row.appendChild(scriptEditorEl("span", "dim", "不要"));
    row.appendChild(scriptEditorInput(`script-editor-option-not-flags-${index}`,
      ((option.requires || {}).not_flags || []).join(","),
      { path: path(`options/${index}/requires/not_flags`) }));
    const remove = scriptEditorButton("", "删", "ghost");
    remove.addEventListener("click", () => {
      ed.draft = scriptEditorCollect("events", ed.draft);
      (ed.draft.options || []).splice(index, 1);
      scriptEditorRender();
    });
    row.appendChild(remove);
    box.appendChild(row);
  });
  body.appendChild(box);
  const add = scriptEditorButton("btn-script-editor-option-add", "加一个选项", "ghost");
  add.addEventListener("click", () => {
    ed.draft = scriptEditorCollect("events", ed.draft);
    (ed.draft.options || []).push({ text: "", requires: { flags: [], not_flags: [] }, effects: [] });
    scriptEditorRender();
  });
  body.appendChild(scriptEditorRow(add));
}

/* -- 重命名表单（改 id 会一起改写引用点；名字可以单独改） --------------------- */

function scriptEditorRenameForm() {
  const ed = scriptEditorState();
  const entity = scriptEditorSelectedEntity();
  const box = scriptEditorEl("div", "script-editor-form-body");
  box.setAttribute("data-kind", ed.kind);
  box.setAttribute("data-mode", "rename");
  if (!entity) return box;
  box.appendChild(scriptEditorField("新 id（稳定引用名；引用点一起改写）", scriptEditorInput(
    "script-editor-rename-id", entity.id, {})));
  if (ed.kind !== "nodes") {
    box.appendChild(scriptEditorField("显示名", scriptEditorInput(
      "script-editor-rename-name", entity.name || "", {})));
  }
  const confirm = scriptEditorButton("btn-script-editor-rename-confirm", "确认重命名", "primary");
  confirm.addEventListener("click", () => scriptEditorRenameConfirm());
  const cancel = scriptEditorButton("btn-script-editor-rename-cancel", "取消", "ghost");
  cancel.addEventListener("click", () => scriptEditorCancel());
  box.appendChild(scriptEditorRow(confirm, cancel));
  box.appendChild(scriptEditorEl("p", "dim",
    "改 id = 改稳定身份：服务端会把全部引用点（节点 → 房间、座位 → 模板、事件池 → 事件…）"
      + "一起改写，断链数为 0。只改显示名就留 id 不变。"));
  return box;
}

/* ------------------------------------------------------------ 动作 */

/** 收下 `GET /api/script-library` 之后，按 kind 选择一行（链上 / 大节页共用）。 */
function scriptEditorOpenKind(kind) {
  const ed = scriptEditorState();
  ed.kind = sListHasKind(kind) ? kind : "scripts";
  ed.mode = null;
  ed.draft = null;
  ed.selected = ed.kind === "scripts" ? ed.scriptId : null;
  ed.hint = "";
  scriptEditorRender();
  return ed.kind;
}

function scriptEditorChoose(kind, id) {
  const ed = scriptEditorState();
  ed.kind = sListHasKind(kind) ? kind : ed.kind;
  ed.selected = id || null;
  ed.mode = null;
  ed.draft = null;
  if (kind === "layers") ed.layerId = id;
  if (kind === "scripts") {
    const script = scriptEditorEntity("scripts", id);
    ed.scriptId = id;
    ed.layerId = script && (script.layers || [])[0] ? script.layers[0].id : null;
  }
  ed.hint = "";
  scriptEditorRender();
  return ed.selected;
}

function scriptEditorEdit() {
  const ed = scriptEditorState();
  const entity = scriptEditorSelectedEntity();
  if (!entity) return null;
  ed.draft = scriptEditorCopy(entity);
  ed.mode = "edit";
  // 表单住「详情」页签（票 07）：点「编辑」就把人带过去，页签不许把表单挡在看不见的地方。
  ed.tab = "detail";
  ed.hint = "";
  scriptEditorRender();
  return ed.draft;
}

function scriptEditorNew() {
  const ed = scriptEditorState();
  if (!ed.library) return null;
  if (ed.kind === "nodes" && !scriptEditorLayer()) {
    ed.error = "新建节点前先选一个剧本大节。";
    scriptEditorRender();
    return null;
  }
  ed.mode = "new";
  ed.selected = null;
  ed.draft = scriptEditorDefaultDraft(ed.kind);
  // 新建表单也住「详情」页签（票 07）：从「大节」/「校验」点新建也看得见输入框。
  ed.tab = "detail";
  ed.hint = "";
  scriptEditorRender();
  return ed.draft;
}

function scriptEditorCancel() {
  const ed = scriptEditorState();
  ed.mode = null;
  ed.draft = null;
  ed.hint = "";
  scriptEditorRender();
  return null;
}

/** 新建 / 编辑的保存：一条 POST / PUT，回包即落盘后的整库（写完之后不必再 GET）。 */
async function scriptEditorSave() {
  const ed = scriptEditorState();
  const kind = ed.kind;
  if (!sListHasKind(kind)) return null;
  const draft = ed.draft;
  if (!draft) return null;
  const entity = scriptEditorCollect(kind, draft);
  const creating = ed.mode === "new";
  if (creating && !String(entity.id || "").trim()) {
    ed.error = "新建必须填 id（稳定引用名）。";
    scriptEditorRender();
    return null;
  }
  if (!creating) entity.id = draft.id;
  const path = scriptEditorPath(kind, creating ? "" : entity.id);
  const body = await scriptEditorSend(
    creating ? "POST" : "PUT",
    path,
    { expected_revision: ed.revision, entity },
  );
  if (!body) {
    scriptEditorRender();
    return null;
  }
  const saved = body.entity || entity;
  ed.selected = saved.id;
  if (kind === "scripts") ed.scriptId = saved.id;
  if (kind === "layers") ed.layerId = saved.id;
  ed.mode = "edit";
  ed.draft = scriptEditorCopy(saved);
  ed.hint = `已保存${SCRIPT_EDITOR_KIND_LABELS[kind]}「${saved.name || saved.id}」。`
    + scriptEditorWarningNote();
  scriptEditorRender();
  return body;
}

function scriptEditorWarningNote() {
  const ed = scriptEditorState();
  return ed.warnings && ed.warnings.length
    ? `引用点悬空 ${ed.warnings.length} 处（校验面板已标红，运行期会拒绝启动该剧本）。`
    : "";
}

/** 另存为副本：服务端重写 id / 名称（副本与原件互不影响）。 */
async function scriptEditorClone() {
  const ed = scriptEditorState();
  const entity = scriptEditorSelectedEntity();
  if (!entity) return null;
  const body = await scriptEditorSend(
    "POST", `${scriptEditorPath(ed.kind, entity.id)}/clone`,
    { expected_revision: ed.revision },
  );
  if (!body || !body.entity) {
    scriptEditorRender();
    return null;
  }
  const copy = body.entity;
  ed.selected = copy.id;
  if (ed.kind === "layers") ed.layerId = copy.id;
  if (ed.kind === "scripts") {
    ed.scriptId = copy.id;
    ed.layerId = (copy.layers || [])[0] ? copy.layers[0].id : null;
  }
  ed.mode = "edit";
  ed.draft = scriptEditorCopy(copy);
  ed.hint = `已另存为副本「${copy.name || copy.id}」（id = ${copy.id}）。`;
  scriptEditorRender();
  return body;
}

function scriptEditorRename() {
  const ed = scriptEditorState();
  if (!scriptEditorSelectedEntity()) return null;
  ed.mode = "rename";
  ed.hint = "";
  scriptEditorRender();
  return ed.mode;
}

async function scriptEditorRenameConfirm() {
  const ed = scriptEditorState();
  const entity = scriptEditorSelectedEntity();
  if (!entity) return null;
  const newId = scriptEditorValue("script-editor-rename-id").trim();
  if (!newId) {
    ed.error = "重命名必须填新 id。";
    scriptEditorRender();
    return null;
  }
  const payload = { expected_revision: ed.revision, new_id: newId };
  const nameInput = $("script-editor-rename-name");
  if (nameInput) payload.name = String(nameInput.value);
  const body = await scriptEditorSend(
    "POST", `${scriptEditorPath(ed.kind, entity.id)}/rename`, payload);
  if (!body) {
    scriptEditorRender();
    return null;
  }
  const saved = body.entity || { ...entity, id: newId };
  ed.selected = saved.id;
  if (ed.kind === "layers") ed.layerId = saved.id;
  if (ed.kind === "scripts") {
    ed.scriptId = saved.id;
    ed.layerId = (saved.layers || [])[0] ? saved.layers[0].id : null;
  }
  ed.mode = "edit";
  ed.draft = scriptEditorCopy(saved);
  ed.hint = `已重命名「${saved.name || saved.id}」（引用点一起改写）。`;
  scriptEditorRender();
  return body;
}

/** 删除：D30 的两类。共享引用**先查引用点**再问；归属项直接问（连带后果写在正文里）。 */
async function scriptEditorDelete() {
  const ed = scriptEditorState();
  const entity = scriptEditorSelectedEntity();
  if (!entity) return null;
  const owned = SCRIPT_EDITOR_OWNED_KINDS.includes(ed.kind);
  let references = [];
  if (!owned) {
    let body = null;
    try {
      body = await api(`${scriptEditorPath(ed.kind, entity.id)}/references`);
    } catch (error) {
      ed.error = `查引用点失败：${error.message}`;
      scriptEditorRender();
      return null;
    }
    references = (body && body.references) || [];
  }
  scriptEditorConfirmDelete(entity, references, owned);
  return references;
}

function scriptEditorConfirmDelete(entity, references, owned) {
  const ed = scriptEditorState();
  const label = SCRIPT_EDITOR_KIND_LABELS[ed.kind] || ed.kind;
  const name = entity.name || entity.id;
  let text = "";
  if (owned) {
    if (ed.kind === "nodes") {
      const layer = scriptEditorLayer() || { edges: [] };
      const count = (layer.edges || []).filter(
        (edge) => edge.from === entity.id || edge.to === entity.id).length;
      text = `删掉节点「${entity.id}」会连带删掉 ${count} 条连线（入边 / 出边一起，服务端级联删除）。`;
    } else if (ed.kind === "layers") {
      const script = scriptEditorScript() || { name: "", layers: [] };
      text = `删掉大节「${name}」会从《${script.name || script.id}》的 layers 里摘掉它`
        + "（归属级联：节点 / 边随大节一起走，不是先删整个剧本）。";
    } else {
      const script = entity;
      text = `删掉剧本「${name}」会连带删掉它的 ${(script.layers || []).length} 个大节`
        + "（归属级联；不需要先逐个删大节）。";
    }
  } else if (references.length) {
    text = `${label}「${name}」还被这些引用点引用；${SCRIPT_EDITOR_DELETE_WARNING}。`;
  } else {
    text = `${label}「${name}」没有被引用；删除之后不会影响别的实体。`;
  }
  const body = references.map((reference) => {
    const row = scriptEditorEl("div", "script-editor-reference");
    row.setAttribute("data-loc", String(reference.loc || ""));
    row.setAttribute("data-rule-id", String(reference.rule_id || ""));
    row.textContent = `${reference.loc || "（没有 loc）"} · ${reference.rule_id || ""}`
      + `：期望 ${reference.expected || "?"}，实得 ${reference.got || "?"}`;
    return row;
  });
  const destructive = owned ? "删除" : "仍然删除";
  return scriptConfirm({
    kind: "editor-delete",
    dialog: "editor-delete",
    // 删除确认本来就有「取消」（= 什么都不删）：和撞旧档 / 退出确认同一条规则——保留关窗键
    // （票 10 / review 修复 C。只有三选一 / 遗物 / 结算结果这类强制选择面才不画）。
    dismissible: true,
    title: `删除${label}？`,
    text,
    body,
    actions: [
      {
        label: "取消",
        ghost: true,
        run: () => { scriptCloseConfirm(); return null; },
      },
      {
        label: destructive,
        primary: true,
        run: () => scriptEditorDeleteConfirmed(entity),
      },
    ],
  });
}

async function scriptEditorDeleteConfirmed(entity) {
  const ed = scriptEditorState();
  const path = `${scriptEditorPath(ed.kind, entity.id)}`
    + `?expected_revision=${encodeURIComponent(String(ed.revision || ""))}`;
  scriptCloseConfirm();
  const body = await scriptEditorSend("DELETE", path, null);
  if (!body) {
    scriptEditorRender();
    return null;
  }
  const warnings = ed.warnings || [];
  const label = SCRIPT_EDITOR_KIND_LABELS[ed.kind] || ed.kind;
  ed.selected = null;
  ed.mode = null;
  ed.draft = null;
  if (ed.kind === "layers" && ed.layerId === entity.id) {
    const script = scriptEditorScript();
    ed.layerId = script && (script.layers || [])[0] ? script.layers[0].id : null;
  }
  if (ed.kind === "scripts" && ed.scriptId === entity.id) {
    ed.scriptId = (scriptEditorLibrary().scripts || [])[0]
      ? scriptEditorLibrary().scripts[0].id : null;
    ed.layerId = null;
  }
  ed.hint = `已删除${label}「${entity.name || entity.id}」。`
    + (warnings.length
      ? `引用点悬空 ${warnings.length} 处（校验面板已标红，运行期会拒绝启动该剧本）。`
      : "");
  scriptEditorRender();
  return body;
}

/* ------------------------------------------------------ 大节页 / 边 / 网格 */

function scriptEditorRenderTree() {
  const box = $("script-editor-tree");
  if (!box) return null;
  const ed = scriptEditorState();
  const script = ed.kind === "scripts" ? scriptEditorScript() : null;
  box.hidden = !script;
  box.innerHTML = "";
  if (!script) return null;
  for (const [index, layer] of (script.layers || []).entries()) {
    const row = scriptEditorEl("button", "script-editor-tree-layer",
      `大节 ${index + 1}/${(script.layers || []).length} · ${layer.name}（${layer.id}）`);
    row.type = "button";
    row.setAttribute("data-layer-id", layer.id);
    row.addEventListener("click", () => {
      ed.scriptId = script.id;
      ed.layerId = layer.id;
      ed.kind = "layers";
      ed.selected = layer.id;
      ed.mode = null;
      ed.draft = null;
      scriptEditorRender();
    });
    box.appendChild(row);
    for (const node of layer.nodes || []) {
      const room = scriptEditorRoomInfo(node.room);
      const line = scriptEditorEl("div", "script-editor-tree-node",
        `${node.id} · 行 ${node.row} 列 ${node.col} → ${room.name}`);
      line.setAttribute("data-node-id", node.id);
      line.setAttribute("data-room", node.room);
      box.appendChild(line);
    }
  }
  return null;
}

function scriptEditorRenderLayerPage() {
  const panel = $("script-editor-layer");
  if (!panel) return null;
  const ed = scriptEditorState();
  const layer = scriptEditorLayer();
  // 「大节」页签拥有网格 / 节点 / 连线：页签开着就画（不受左栏选中哪类实体影响）。
  const visible = Boolean(layer)
    && (ed.tab === "layer" || ed.kind === "layers" || ed.kind === "nodes");
  panel.hidden = !visible;
  if (!visible) {
    const graph = $("script-editor-graph");
    if (graph) {
      graph.innerHTML = "";
      graph.style.height = "0px";
    }
    return null;
  }
  const script = scriptEditorScript() || {};
  const index = (script.layers || []).findIndex((item) => item.id === layer.id);
  const title = $("script-editor-layer-title");
  if (title) title.textContent = `大节 ${index + 1}/${(script.layers || []).length} · ${layer.name}`;
  const hint = $("script-editor-layer-hint");
  if (hint) {
    hint.textContent = `网格 ${layer.grid.rows}×${layer.grid.cols} · 节点 ${(layer.nodes || []).length}`
      + ` · 连线 ${(layer.edges || []).length}（拖节点改坐标；改网格 / 画边 / 解除都在这一页）`;
  }
  const rowsInput = $("script-editor-grid-rows");
  if (rowsInput) rowsInput.value = String(layer.grid.rows);
  const colsInput = $("script-editor-grid-cols");
  if (colsInput) colsInput.value = String(layer.grid.cols);

  const graph = $("script-editor-graph");
  if (!graph) return null;
  const nodes = (layer.nodes || []).map((node) => ({
    id: node.id,
    row: node.row,
    col: node.col,
    room: scriptEditorRoomInfo(node.room),
  }));
  const result = scriptGraphRender(graph, {
    mode: "editor",
    layer: { rows: layer.grid.rows, cols: layer.grid.cols },
    nodes,
    edges: layer.edges || [],
    width: scriptGraphWidth(graph),
    onNodeClick: (node) => scriptEditorChoose("nodes", node.id),
  });
  scriptEditorWireGraph(layer, result ? result.layout : null);
  scriptEditorRenderEdgeControls(layer);
  return result;
}

/** 网格命中格 + 节点拖拽（坐标 → `(row, col)` 的换算在前端；落库由服务端校验）。 */
function scriptEditorWireGraph(layer, layout) {
  const graph = $("script-editor-graph");
  if (!graph || !layout) return null;
  const ed = scriptEditorState();
  const cells = scriptEditorEl("div", "script-editor-cells");
  for (let row = 0; row < layout.rows; row += 1) {
    for (let col = 0; col < layout.cols; col += 1) {
      const cell = scriptEditorEl("div", "script-editor-cell");
      cell.setAttribute("data-row", String(row));
      cell.setAttribute("data-col", String(col));
      cell.style.position = "absolute";
      cell.style.left = `${layout.left + col * layout.cellW}px`;
      cell.style.top = `${layout.top + row * layout.cellH}px`;
      cell.style.width = `${layout.cellW}px`;
      cell.style.height = `${layout.cellH}px`;
      cell.addEventListener("dragover", (event) => {
        if (event && event.preventDefault) event.preventDefault();
      });
      cell.addEventListener("drop", async (event) => {
        if (event && event.preventDefault) event.preventDefault();
        await scriptEditorDropNode(row, col);
      });
      cells.appendChild(cell);
    }
  }
  graph.appendChild(cells);
  // 节点本身是拖拽起点；落到别的节点格上也接受（落点由格子的坐标决定）。
  for (const node of (layer.nodes || [])) {
    const cell = scriptEditorByClass(graph, "script-node").find(
      (item) => item.getAttribute("data-node-id") === node.id);
    if (!cell) continue;
    const button = scriptEditorByClass(cell, "script-node-button")[0];
    if (button) {
      button.setAttribute("draggable", "true");
      button.addEventListener("dragstart", (event) => {
        ed.drag = node.id;
        if (event && event.dataTransfer && event.dataTransfer.setData) {
          event.dataTransfer.setData("text/plain", node.id);
        }
      });
    }
    cell.addEventListener("dragover", (event) => {
      if (event && event.preventDefault) event.preventDefault();
    });
    cell.addEventListener("drop", async (event) => {
      if (event && event.preventDefault) event.preventDefault();
      await scriptEditorDropNode(node.row, node.col);
    });
  }
  return cells;
}

/** 把拖拽中的节点落到 `(row, col)`：一条 PUT 改坐标（节点别的字段原样带回去）。 */
async function scriptEditorDropNode(row, col) {
  const ed = scriptEditorState();
  const nodeId = ed.drag;
  if (!nodeId) return null;
  const layer = scriptEditorLayer();
  if (!layer) return null;
  const node = (layer.nodes || []).find((item) => item.id === nodeId);
  if (!node) return null;
  ed.drag = null;
  if (Number(node.row) === Number(row) && Number(node.col) === Number(col)) return null;
  const entity = { ...scriptEditorCopy(node), row: Number(row), col: Number(col) };
  const body = await scriptEditorSend("PUT", scriptEditorPath("nodes", node.id), {
    expected_revision: ed.revision,
    entity,
  });
  if (body) {
    ed.hint = `节点「${node.id}」挪到第 ${Number(row) + 1} 行第 ${Number(col) + 1} 列。`;
    scriptEditorRender();
  }
  return body;
}

/** 网格长宽：一条 PUT 整份大节（节点 / 边原样带回去，不许被默认值冲掉）。 */
async function scriptEditorSaveGrid() {
  const ed = scriptEditorState();
  const layer = scriptEditorLayer();
  if (!layer) return null;
  const rows = Math.max(1, Math.round(scriptEditorNumber("script-editor-grid-rows", layer.grid.rows)));
  const cols = Math.max(1, Math.round(scriptEditorNumber("script-editor-grid-cols", layer.grid.cols)));
  const entity = scriptEditorCopy(layer);
  entity.grid = { rows, cols };
  const body = await scriptEditorSend("PUT", scriptEditorPath("layers", layer.id), {
    expected_revision: ed.revision,
    entity,
  });
  if (body) {
    ed.hint = `网格改成 ${rows}×${cols}。`;
    scriptEditorRender();
  }
  return body;
}

/** 边列表：`from → to` + 条件 + 「设条件」/「解除」（与画边对称）。 */
function scriptEditorRenderEdgeControls(layer) {
  const ed = scriptEditorState();
  const from = $("script-editor-edge-from");
  const to = $("script-editor-edge-to");
  const nodes = (layer.nodes || []).map((node) => ({ value: node.id, label: node.id }));
  if (from) {
    const current = from.value;
    from.innerHTML = "";
    for (const option of nodes) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      from.appendChild(node);
    }
    from.value = nodes.some((option) => option.value === current) ? current
      : (nodes[0] || {}).value || "";
  }
  if (to) {
    const current = to.value;
    to.innerHTML = "";
    for (const option of nodes) {
      const node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      to.appendChild(node);
    }
    to.value = nodes.some((option) => option.value === current) ? current
      : (nodes[1] || nodes[0] || {}).value || "";
  }
  const box = $("script-editor-edges");
  if (!box) return null;
  box.innerHTML = "";
  for (const [edgeIndex, edge] of (layer.edges || []).entries()) {
    const requires = edge.requires || {};
    const row = scriptEditorEl("div", "script-editor-edge");
    row.setAttribute("data-from", edge.from);
    row.setAttribute("data-to", edge.to);
    // 边行的 `loc` 锚点（票 09）：`/scripts/<i>/layers/<j>/edges/<k>/…` 的校验条目跳到这一行，
    // 它住「大节」页签 ⇒ 页签按所在 pane 切（不按 loc 前缀猜）。
    row.setAttribute("data-field-path",
      scriptEditorFieldPath("layers", layer.id, `edges/${edgeIndex}`));
    const label = scriptEditorEl("span", "script-editor-edge-label", `${edge.from} → ${edge.to}`);
    row.appendChild(label);
    const condition = scriptEditorEl("span", "script-editor-edge-cond",
      scriptGraphEdgeLabel(edge) || "无标签条件");
    row.appendChild(condition);
    row.appendChild(scriptEditorEl("span", "dim", "需要"));
    const flags = scriptEditorInput(`script-editor-edge-flags-${edge.from}-${edge.to}`,
      (requires.flags || []).join(","), {});
    flags.className = "script-editor-edge-flags";
    row.appendChild(flags);
    row.appendChild(scriptEditorEl("span", "dim", "不要"));
    const notFlags = scriptEditorInput(`script-editor-edge-not-flags-${edge.from}-${edge.to}`,
      (requires.not_flags || []).join(","), {});
    notFlags.className = "script-editor-edge-not-flags";
    row.appendChild(notFlags);
    const apply = scriptEditorEl("button", "ghost script-editor-edge-apply", "设条件");
    apply.type = "button";
    apply.addEventListener("click", () => scriptEditorSetEdgeRequires(
      edge.from, edge.to, flags.value, notFlags.value));
    row.appendChild(apply);
    const unlink = scriptEditorEl("button", "ghost script-editor-edge-unlink", "解除");
    unlink.type = "button";
    unlink.addEventListener("click", () => scriptEditorUnlinkEdge(edge.from, edge.to));
    row.appendChild(unlink);
    box.appendChild(row);
  }
  return null;
}

async function scriptEditorDrawEdge() {
  const ed = scriptEditorState();
  const layer = scriptEditorLayer();
  if (!layer) return null;
  const from = scriptEditorValue("script-editor-edge-from");
  const to = scriptEditorValue("script-editor-edge-to");
  if (!from || !to) return null;
  const body = await scriptEditorSend(
    "POST", `${scriptEditorPath("edges")}`,
    { expected_revision: ed.revision, entity: { from, to, requires: { flags: [], not_flags: [] } } },
  );
  if (body) {
    ed.hint = `画了一条边：${from} → ${to}。`;
    scriptEditorRender();
  }
  return body;
}

async function scriptEditorSetEdgeRequires(from, to, flags, notFlags) {
  const ed = scriptEditorState();
  const entity = {
    from,
    to,
    requires: { flags: scriptEditorList(flags), not_flags: scriptEditorList(notFlags) },
  };
  const body = await scriptEditorSend(
    "PUT", `${scriptEditorPath("edges")}/${encodeURIComponent(from)}/${encodeURIComponent(to)}`,
    { expected_revision: ed.revision, entity },
  );
  if (body) {
    ed.hint = `已设 ${from} → ${to} 的边条件。`;
    scriptEditorRender();
  }
  return body;
}

async function scriptEditorUnlinkEdge(from, to) {
  const ed = scriptEditorState();
  const path = `${scriptEditorPath("edges")}/${encodeURIComponent(from)}/${encodeURIComponent(to)}`
    + `?expected_revision=${encodeURIComponent(String(ed.revision || ""))}`;
  const body = await scriptEditorSend("DELETE", path, null);
  if (body) {
    ed.hint = `已解除连线：${from} → ${to}（别的边一条不动）。`;
    scriptEditorRender();
  }
  return body;
}

/* ------------------------------------------------------------ 收集 / 写 */

/** 表单 + 草稿 → 一条完整实体（列表结构住草稿，标量字段读 DOM）。 */
function scriptEditorCollect(kind, base) {
  const ed = scriptEditorState();
  const draft = scriptEditorCopy(base || {});
  const value = (id) => scriptEditorValue(id);
  const number = (id, fallback) => scriptEditorNumber(id, fallback);
  if (kind === "scripts") {
    draft.name = value("script-editor-field-name");
    draft.comment = value("script-editor-field-comment");
    draft.roster = [0, 1, 2]
      .map((index) => value(`script-editor-field-roster-${index}`).trim())
      .filter(Boolean);
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "layers") {
    draft.name = value("script-editor-field-name");
    if (!$("script-editor-layer").hidden) {
      draft.grid = {
        rows: Math.max(1, Math.round(number("script-editor-grid-rows", draft.grid.rows))),
        cols: Math.max(1, Math.round(number("script-editor-grid-cols", draft.grid.cols))),
      };
    }
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "nodes") {
    draft.row = Math.max(0, Math.round(number("script-editor-field-row", draft.row)));
    draft.col = Math.max(0, Math.round(number("script-editor-field-col", draft.col)));
    draft.room = value("script-editor-field-room") || draft.room;
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "rooms") {
    draft.name = value("script-editor-field-name");
    draft.kind = value("script-editor-field-kind") || draft.kind;
    draft.text = value("script-editor-field-text");
    draft.is_boss = scriptEditorChecked("script-editor-field-boss");
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    if (draft.kind === "event") {
      draft.event_pool = value("script-editor-field-event-pool") || null;
      draft.battle = null;
      draft.pool = null;
    } else if (draft.kind === "unknown") {
      draft.battle = null;
      draft.event_pool = null;
      draft.pool = scriptEditorByClass($("script-editor-form"), "script-editor-pool-row")
        .map((row) => {
          const select = scriptEditorByClass(row, "script-editor-pool-room")[0];
          const weight = scriptEditorByClass(row, "script-editor-pool-weight")[0];
          return {
            room: select ? String(select.value) : "",
            weight: Math.max(1, Number(weight ? weight.value : 1) || 1),
          };
        })
        .filter((entry) => entry.room);
    } else if (draft.kind === "story" || draft.kind === "battle") {
      draft.event_pool = null;
      draft.pool = null;
      draft.battle = scriptEditorCollectBattle(draft.battle);
    } else {
      draft.battle = null;
      draft.event_pool = null;
      draft.pool = null;
    }
    return draft;
  }
  if (kind === "templates") {
    draft.name = value("script-editor-field-name");
    draft.band = value("script-editor-field-band");
    draft.hp = Math.max(1, Math.round(number("script-editor-field-hp", draft.hp)));
    draft.hp_max = Math.max(1, Math.round(number("script-editor-field-hp_max", draft.hp_max)));
    draft.random = {
      pool: value("script-editor-field-random-pool") || (draft.random || {}).pool,
      count: [
        Math.max(0, Math.round(number("script-editor-field-random-min",
          ((draft.random || {}).count || [0, 0])[0]))),
        Math.max(0, Math.round(number("script-editor-field-random-max",
          ((draft.random || {}).count || [0, 0])[1]))),
      ],
    };
    draft.initial_skills = (draft.initial_skills || []).map(String);
    const reward = draft.reward || {};
    draft.reward = {
      coins: Math.max(0, Math.round(number("script-editor-field-reward-coins", reward.coins || 0))),
      pick1: Math.max(0, Math.round(number("script-editor-field-reward-pick1", reward.pick1 || 0))),
      pick2: Math.max(0, Math.round(number("script-editor-field-reward-pick2", reward.pick2 || 0))),
      pick3: Math.max(0, Math.round(number("script-editor-field-reward-pick3", reward.pick3 || 0))),
    };
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "skill_pools") {
    draft.name = value("script-editor-field-name");
    draft.skills = (draft.skills || []).map(String);
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "events") {
    draft.name = value("script-editor-field-name");
    draft.text = value("script-editor-field-text");
    draft.options = (draft.options || []).map((option, index) => {
      const text = value(`script-editor-option-text-${index}`);
      const flags = value(`script-editor-option-flags-${index}`);
      const notFlags = value(`script-editor-option-not-flags-${index}`);
      return {
        text,
        requires: { flags: scriptEditorList(flags), not_flags: scriptEditorList(notFlags) },
        effects: option.effects || [],
      };
    });
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "event_pools") {
    draft.name = value("script-editor-field-name");
    draft.events = (draft.events || []).map(String);
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  if (kind === "global_skills") {
    draft.name = value("script-editor-field-name");
    draft.side = value("script-editor-field-side") || draft.side;
    const scale = {};
    for (const field of SCRIPT_EDITOR_SCALE_FIELDS) {
      const span = ((draft.scale || {})[field]) || [1, 1];
      scale[field] = [
        Math.max(0, Math.round(number(`script-editor-field-scale-${field}-min`, span[0]))),
        Math.max(0, Math.round(number(`script-editor-field-scale-${field}-max`, span[1]))),
      ];
    }
    draft.scale = scale;
    if (ed.mode === "new") draft.id = value("script-editor-field-id").trim();
    return draft;
  }
  return draft;
}

/** 战斗编排的收集：引用选择器的值原样回去（座位 role 由列表决定）。 */
function scriptEditorCollectBattle(base) {
  const draft = scriptEditorCopy(base || scriptEditorDefaultBattle());
  const victory = scriptEditorValue("script-editor-field-victory") || "rout";
  const rounds = Math.max(1, Math.round(scriptEditorNumber(
    "script-editor-field-victory-rounds", (draft.victory || {}).rounds || 3)));
  draft.victory = victory === "survive_rounds" || victory === "rout_within_rounds"
    ? { kind: victory, rounds }
    : { kind: victory };
  const form = $("script-editor-form");
  const allyRows = scriptEditorByClass(form, "script-editor-ally-row");
  draft.allies = allyRows.map((row) => {
    const select = scriptEditorByClass(row, "script-editor-ally-template")[0];
    const protectedBox = scriptEditorByClass(row, "script-editor-ally-protected")[0];
    return {
      role: "ally",
      template: select ? String(select.value) : null,
      protected: Boolean(protectedBox && protectedBox.checked),
      boss: false,
    };
  });
  const drawBand = $("script-editor-battle-band");
  if (drawBand) {
    const min = Math.max(0, Math.round(scriptEditorNumber("script-editor-battle-count-min", 1)));
    const max = Math.max(0, Math.round(scriptEditorNumber("script-editor-battle-count-max", 1)));
    draft.enemies = { draw: { band: scriptEditorValue("script-editor-battle-band"), count: [min, max] } };
  } else {
    const enemyRows = scriptEditorByClass(form, "script-editor-enemy-row");
    draft.enemies = enemyRows.map((row) => {
      const select = scriptEditorByClass(row, "script-editor-enemy-template")[0];
      const bossBox = scriptEditorByClass(row, "script-editor-enemy-boss")[0];
      return {
        role: "enemy",
        template: select ? String(select.value) : null,
        protected: false,
        boss: Boolean(bossBox && bossBox.checked),
      };
    });
  }
  const globalRows = scriptEditorByClass(form, "script-editor-global-row");
  draft.global_skills = globalRows.map((row) => {
    const select = scriptEditorByClass(row, "script-editor-global-select")[0];
    return { skill: select ? String(select.value) : "", scale: null };
  });
  return draft;
}

/** 一条写请求：成功 / 失败都落进统一的面（`error` 红字 + `errors` 面板）。 */
async function scriptEditorSend(method, path, payload) {
  const ed = scriptEditorState();
  ed.error = "";
  ed.warnings = [];
  const options = { method };
  if (payload !== null && payload !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(payload);
  }
  let body = null;
  try {
    body = await api(path, options);
  } catch (error) {
    ed.error = String(error && error.message ? error.message : "请求失败");
    if (error && error.body && error.body.library) scriptEditorAdopt(error.body);
    return null;
  }
  scriptEditorAdopt(body);
  return body;
}

/** kind → 端点路径（票 24 的清单；DELETE 的 revision 走查询串，由调用方拼）。 */
function scriptEditorPath(kind, id) {
  const ed = scriptEditorState();
  const encoded = (value) => encodeURIComponent(String(value));
  if (kind === "layers") {
    return `/api/script-library/scripts/${encoded(ed.scriptId)}/layers`
      + (id ? `/${encoded(id)}` : "");
  }
  if (kind === "nodes") {
    return `/api/script-library/scripts/${encoded(ed.scriptId)}/layers/${encoded(ed.layerId)}/nodes`
      + (id ? `/${encoded(id)}` : "");
  }
  if (kind === "edges") {
    return `/api/script-library/scripts/${encoded(ed.scriptId)}/layers/${encoded(ed.layerId)}/edges`;
  }
  return `/api/script-library/${kind}` + (id ? `/${encoded(id)}` : "");
}

/* ------------------------------------------------------------ 校验跳字段 */

/** 点校验条目：按 `loc` 选中引用方实体、**切到拥有该字段的页签**、打开它的编辑态，
 * 再把字段 / 节点标出来并滚到可见处。
 *
 * `loc` 是加载器的 JSON 指针（票 14 / 票 24 原样下发）；前端**不猜**它在服务端模型里的
 * 语义，只做"指针前缀 → 当前表 / 实体 / 控件"的机械映射。页签切换与定位在**同一个函数体**
 * 里，且落点必须过 pane 判据（票 09）：只有当前页签那一块里的字段 / 节点才标 `.on`、才滚 ——
 * "标记落在看不见的 pane 里、提示却说已定位"这个状态不在这个形状里。
 */
function scriptEditorJump(loc) {
  const ed = scriptEditorState();
  const pointer = String(loc || "");
  ed.focusLoc = pointer;
  const target = scriptEditorLocTarget(pointer);
  if (target) {
    ed.kind = target.kind;
    if (target.scriptId) ed.scriptId = target.scriptId;
    if (target.layerId) ed.layerId = target.layerId;
    ed.selected = target.id;
    const entity = scriptEditorSelectedEntity();
    if (entity) {
      ed.mode = "edit";
      ed.draft = scriptEditorCopy(entity);
    } else {
      ed.mode = null;
      ed.draft = null;
    }
  }
  ed.hint = `已定位到字段 ${pointer}。`;
  // ① 先把目标实体的字段 / 节点铺进各自的 pane：页签要问"这个字段住哪一块"，字段得先在 DOM 里。
  scriptEditorRender();
  // ② 字段 / 节点住哪块 pane，页签就落哪块（票 09：不按 loc 前缀猜）。
  ed.tab = scriptEditorTabForLoc(pointer);
  // ③ 页签落到 DOM（`scriptEditorSyncTabs` 是唯一落点），那一块才真的可见。
  scriptEditorSyncTabs();
  // ④ 只标 / 只滚**当前那一块**里的落点：字段与节点常常不在同一块 pane 里。
  for (const landing of [scriptEditorFindField(pointer), scriptEditorFindNode(pointer)]) {
    if (!landing || scriptEditorPaneOf(landing) !== ed.tab) continue;
    landing.classList.add("on");
    landing.scrollIntoView({ block: "center" });
  }
  return pointer;
}

/** 一个控件住在哪块 pane：它在哪个**字段挂载根**（`SCRIPT_EDITOR_PANE_ROOTS`）的子树里。
 * 找不到 ⇒ `null`（不在编辑器右栏里）。 */
function scriptEditorPaneOf(node) {
  if (!node) return null;
  for (const [rootId, pane] of SCRIPT_EDITOR_PANE_ROOTS) {
    const root = $(rootId);
    if (root && (root === node || scriptEditorWalk(root, []).includes(node))) return pane;
  }
  return null;
}

/** 在右栏的字段挂载根里找 `loc` 指的那个字段 / 控件：先精确 `data-field-path`，再退最长前缀。
 * 返回 `{field, pane}`；`null` = 这个 `loc` 在前端没有可编辑的控件（例如整条大节的 loc）。
 */
function scriptEditorFindLocAnchor(loc) {
  const pointer = String(loc || "");
  const pathOf = (node) => node.getAttribute("data-field-path");
  for (const [rootId, pane] of SCRIPT_EDITOR_PANE_ROOTS) {
    const root = $(rootId);
    if (!root) continue;
    const fields = scriptEditorWalk(root, []).filter(
      (node) => node.getAttribute && node.getAttribute("data-field-path"));
    const exact = fields.find((node) => pathOf(node) === pointer);
    if (exact) return { field: exact, pane };
    const prefix = fields
      .filter((node) => pointer.startsWith(`${pathOf(node)}/`))
      .sort((left, right) => pathOf(right).length - pathOf(left).length)[0];
    if (prefix) return { field: prefix, pane };
  }
  return null;
}

/** JSON 指针 → 右栏页签：**问字段自己的家**（`scriptEditorFindLocAnchor` 的 pane），不猜前缀。
 *
 * 节点的 row/col/room 住详情表单 ⇒「详情」；边的 `from` / 条件住大节页签的边列表 ⇒「大节」；
 * 没有可编辑字段但有图上的节点（整条节点的 loc）⇒ 节点在「大节」。
 */
function scriptEditorTabForLoc(loc) {
  const anchor = scriptEditorFindLocAnchor(loc);
  if (anchor) return anchor.pane;
  if (scriptEditorFindNode(loc)) return "layer";
  return "detail";
}

/** JSON 指针 → `{kind, id, scriptId?, layerId?}`（只认票 14 那几种前缀）。 */
function scriptEditorLocTarget(loc) {
  const segments = String(loc || "").split("/").filter(Boolean);
  if (!segments.length) return null;
  const library = scriptEditorLibrary() || {};
  const indexOf = (list, position) => {
    const number = Number(position);
    return Number.isInteger(number) ? (list || [])[number] || null : null;
  };
  if (segments[0] === "scripts") {
    const script = indexOf(library.scripts, segments[1]);
    if (!script) return null;
    if (segments[2] === "layers") {
      const layer = indexOf(script.layers, segments[3]);
      if (!layer) return null;
      if (segments[4] === "nodes") {
        const node = indexOf(layer.nodes, segments[5]);
        return node ? { kind: "nodes", id: node.id, scriptId: script.id, layerId: layer.id } : null;
      }
      return { kind: "layers", id: layer.id, scriptId: script.id, layerId: layer.id };
    }
    return { kind: "scripts", id: script.id, scriptId: script.id, layerId: null };
  }
  const kind = segments[0];
  if (!SCRIPT_EDITOR_KINDS.some((entry) => entry.key === kind)) return null;
  const entity = indexOf(library[kind], segments[1]);
  return entity ? { kind, id: entity.id } : null;
}

/** 跳转要标 / 滚的那一个元素：`loc` 有可锚的字段 / 控件就用它，没有就退表单本身
 * （整块表单留在「详情」里，提示说的是"字段"而高亮的是它所在的块）。 */
function scriptEditorFindField(loc) {
  const anchor = scriptEditorFindLocAnchor(loc);
  return anchor ? anchor.field : ($("script-editor-form") || null);
}

/** 在「大节」页签的节点图里找 `loc` 指的那个节点（`data-node-id` 由共用渲染器挂上）。
 * 节点 / 连线的字段住 `layers` 下，但它们在**图**上；跳转后高亮那个节点才是"定位到它"。 */
function scriptEditorFindNode(loc) {
  const graph = $("script-editor-graph");
  if (!graph) return null;
  const target = scriptEditorLocTarget(loc);
  if (!target || target.kind !== "nodes") return null;
  return scriptEditorWalk(graph, []).find(
    (node) => node.classList && node.classList.contains("script-node-button")
      && node.getAttribute("data-node-id") === target.id,
  ) || null;
}

/* -------------------------------------------------------------- 接线 */

/** 编辑器入口（开局页剧本面板）→ 读库 / 切页；本函数不自动读库（首屏仍是开局页）。 */
async function scriptEditorShow() {
  show("view-script-editor");
  const ed = scriptEditorState();
  if (!ed.library) return scriptEditorLoad();
  scriptEditorRender();
  return ed.library;
}

/** 接线（`main.js:wire()` 里调一次）：静态键 + 三个导航键。 */
function wireScriptEditor() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  };
  on("btn-script-editor", () => scriptEditorShow());
  on("btn-script-editor-reload", () => scriptEditorReload());
  on("btn-script-editor-new", () => scriptEditorNew());
  on("btn-script-editor-edit", () => scriptEditorEdit());
  on("btn-script-editor-clone", () => scriptEditorClone());
  on("btn-script-editor-rename", () => scriptEditorRename());
  on("btn-script-editor-delete", () => scriptEditorDelete());
  on("btn-script-editor-grid-save", () => scriptEditorSaveGrid());
  on("btn-script-editor-edge-draw", () => scriptEditorDrawEdge());
  for (const tab of SCRIPT_EDITOR_TABS) {
    on(`script-editor-tab-${tab}`, () => scriptEditorShowTab(tab));
  }
  on("script-editor-nav-lobby", () => { show("lobby"); loadRuns(); });
  on("script-editor-nav-table", () => show("table"));
  on("script-editor-nav-replay", () => {
    show("replay");
    loadRuns();
    renderReplay();
  });
  return null;
}
