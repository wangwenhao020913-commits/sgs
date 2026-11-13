/* 客户端动作探针：用**真实前端脚本**构造动作，打印 JSON 给 Python 侧喂 `parse_action`。
 *
 * 为什么要 Node：M5 报障的错型是"跨语言载荷形状失配"（展示卡 → `UseCard.card` →
 * 服务端 `extra="forbid"` 400）。Python 里手写一份"客户端会发什么"只能测到假想；这里用
 * `vm` 加载 `tools/sgs_web/static/` 的真脚本 + `tests/support/ui_bridge.js` 的桥，调用
 * 真实构造器，输出的就是浏览器会发的那条 JSON。
 *
 * 加载纪律与 `ui_smoke.mjs` 同姿态：脚本清单来自 `index.html` 的 `<script src>` 顺序；
 * 这里只取 `buildAction` / 预发动点击 / 两行观看点选 / 座位多选点选 的依赖闭包（core →
 * candidates → **table** → preactivation → **interaction** → pending → **options** →
 * reveal → modal → arrange → answer），不加载 `main.js`（它在末尾 `wire()`，需要 DOM）。
 * `options.js` 只为 `selectionCap`（两行面的选中上限），`table.js` 只为座位多选面的
 * "能不能新选"判据（`seatMultiPickable`，W1 评审修订后点击口 `interaction.js` 调它），
 * `interaction.js` 只为座位多选面的点选入口（`toggleSeatMultiPick`，W1），
 * `reveal.js` / `modal.js` 只为只读态判据与点选入口
 * （`selectionEnabled` → `stepDialogKind`）——判据走真的那份，不在探针里另写。
 * 两个**渲染**入口（`renderTable` / `renderPending`）换成空实现：DOM 垫片只在
 * `ui_smoke.mjs` 里有，本探针只断"点击 → 载荷"，不断渲染。桥会检查每个名字真的存在：
 * 少一个就抛错，而不是让 Python 侧收到 `undefined` 后误判。
 */

import { readFileSync } from "node:fs";
import vm from "node:vm";

const STATIC_DIR = "tools/sgs_web/static";
const NEEDED = [
  "core.js",
  "candidates.js",
  // 票 17：`candidates.js:isOwnZonePick` 调 `card-face.js:isProvide`（"提供一张 B"的判据），
  // 故客户端的 `submitOwnHandSinglePick`（窗口里"点一张自己的牌"）依赖它。页面里也是
  // candidates → card-face 的次序。
  "card-face.js",
  "table.js",
  "preactivation.js",
  "interaction.js",
  "pending.js",
  "options.js",
  "reveal.js",
  "modal.js",
  // shan-baozuoci 票 05：`modal.js:stepDialogKind` 按载荷里的 `appearance` 选面时会调
  // 外观组件的判据 / 渲染器（`isAvatarAppearanceStep` / `renderAvatarAppearance`），
  // 页面里它们紧跟 modal.js —— 这里照页面次序带进来，不然加载期就 ReferenceError。
  "avatar-appearance.js",
  "arrange.js",
  "answer.js",
];

const html = readFileSync(`${STATIC_DIR}/index.html`, "utf8");
const pageOrder = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(
  (match) => match[1],
);
const missingFromPage = NEEDED.filter((name) => !pageOrder.includes(name));
if (missingFromPage.length) {
  throw new Error(`index.html 里没有这些脚本：${missingFromPage.join(", ")}`);
}
const ordered = pageOrder.filter((name) => NEEDED.includes(name));
if (JSON.stringify(ordered) !== JSON.stringify(NEEDED)) {
  throw new Error(
    `探针加载顺序与 index.html 不一致：探针 ${NEEDED.join(" → ")}，页面 ${ordered.join(" → ")}`,
  );
}

const context = vm.createContext({ console });
context.globalThis = context;
for (const name of NEEDED) {
  vm.runInContext(readFileSync(`${STATIC_DIR}/${name}`, "utf8"), context, { filename: name });
}
vm.runInContext(readFileSync("tests/support/ui_bridge.js", "utf8"), context, {
  filename: "ui_bridge.js",
});
const ui = context.__bridge;
for (const name of [
  "buildAction",
  "buildPreativationAction",
  "bareCardRef",
  "state",
  "enterPreativation",
  "togglePreativationCard",
  "preactivationSubmitAllowed",
  "selectPreativationSeat",
  "toggleSeatMultiPick",
]) {
  if (!(name in ui)) throw new Error(`ui_bridge 没桥出 ${name}：前端脚本加载不完整`);
}

/* 与 `ui_smoke.mjs` 的 M5-bugfix-d 同一份借刀夹具：展示卡带满装饰字段，裸卡九键。 */
const borrowedRaw = {
  card_id: 31,
  type: "borrowed_sword",
  name: "借刀杀人",
  suit: "club",
  rank: 12,
  slot: null,
  attack_range: null,
  color: "black",
  virtual_sources: [],
};
const borrowedDisplay = {
  ...borrowedRaw,
  display_name: "借刀杀人",
  kind: "normal",
  attribute: "普通",
  source_cards: [],
  description: "借刀杀人的描述：令持刀者对其攻击范围内的一名角色使用【杀】。",
};

const card = (raw) => ({ ...raw });
const slashRaw = {
  card_id: 52,
  type: "slash",
  name: "杀",
  suit: "diamond",
  rank: 9,
  slot: null,
  attack_range: null,
  color: "red",
  virtual_sources: [],
};
const dodgeRaw = {
  card_id: 53,
  type: "dodge",
  name: "闪",
  suit: "heart",
  rank: 2,
  slot: null,
  attack_range: null,
  color: "red",
  virtual_sources: [],
};

const resetState = () => {
  ui.state.view = null;
  ui.state.preactivation = null;
  ui.state.focusCardId = null;
  ui.state.selectedTargets = [];
  ui.state.selectedOption = null;
  ui.state.picks = [];
  ui.state.convertSkill = null;
};

const borrowPending = {
  kind: "PLAY_PHASE",
  actor: 0,
  seq: 93,
  prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
  legal_actions: [{ kind: "EndPhase", answers_seq: 93 }],
  candidate_index: {},
  virtual_index: {},
  option_seats: {},
  context: null,
  preactivations: {},
  preactivation: [],
  card_preactivations: {},
};
const borrowPa = {
  skillId: null,
  cardId: 31,
  card: borrowedDisplay,
  name: "借刀杀人",
  composite: false,
  cardEntry: true,
  steps: [
    { name: "seat", zones: [], min: 1, max: 1, order: 0, pool: [{ when: [], options: ["seat:1"] }] },
    { name: "seat", zones: [], min: 1, max: 1, order: 1, pool: [{ when: [], options: ["seat:2"] }] },
  ],
  answers: [
    { values: [1], texts: ["seat:1"] },
    { values: [2], texts: ["seat:2"] },
  ],
  cursor: 2,
};
const borrowed = ui.buildPreativationAction(borrowPending, borrowPa);
if (!borrowed) throw new Error("buildPreativationAction 返回了 null（夹具没填完？）");

/* 普通 UseCard：走 buildAction 的手选路径（焦点牌 + 已选目标 → 在候选里查那一条）。 */
resetState();
ui.state.view = {
  human_seat: 0,
  pending: {
    kind: "PLAY_PHASE",
    actor: 0,
    seq: 94,
    prompt: "出牌阶段",
    legal_actions: [{ kind: "UseCard", card: card(slashRaw), targets: [2], answers_seq: 94 }],
    candidate_index: {
      52: { card: card(slashRaw), pool: [2], min_targets: 1, max_targets: 1, combos: [[2]] },
    },
    virtual_index: {},
    option_seats: {},
    context: null,
  },
};
ui.state.focusCardId = 52;
ui.state.selectedTargets = [2];
const use = ui.buildAction();
if (!use) throw new Error("buildAction 没拼出 UseCard（手选路径夹具失效）");

/* PlayCard：响应窗口唯一候选、无目标 → buildAction 的直取路径。 */
resetState();
ui.state.view = {
  human_seat: 0,
  pending: {
    kind: "RESPOND",
    actor: 0,
    seq: 95,
    cause: "BARBARIAN_INVASION",
    required: "slash",
    prompt: "请打出一张牌响应",
    legal_actions: [{ kind: "PlayCard", card: card(dodgeRaw), answers_seq: 95 }],
    candidate_index: {},
    virtual_index: {},
    option_seats: {},
    context: null,
  },
};
const play = ui.buildAction();
if (!play) throw new Error("buildAction 没拼出 PlayCard（直取路径夹具失效）");

/* 票 93：两行观看手牌（`SELECT_CARDS` + `spec.max_per_suit` + 槽位跨两座）。
 * 用**真实**的 `reveal.js:toggleWatchPick` 按**倒序**点四张（3 → 1 → 2 → 0）；送出的
 * `SelectCards.picks` 必须是**面内位置升序**（与点击顺序无关）——与 `buildAction` 的
 * 既有规范化同一条纪律。 */
context.renderTable = () => {};
context.renderPending = () => {};
resetState();
const watchCard = (cardId, name, suit, color) => ({
  card_id: cardId,
  type: name === "杀" ? "slash" : "dodge",
  name,
  suit,
  rank: 7,
  slot: null,
  attack_range: null,
  color,
  virtual_sources: [],
});
const watchPending = {
  kind: "SELECT_CARDS",
  actor: 0,
  seq: 98,
  prompt: "请从选择面里选择牌。",
  legal_actions: [{ kind: "SelectCards", picks: [], answers_seq: 98 }],
  candidate_index: {},
  virtual_index: {},
  option_seats: {},
  context: null,
  preactivations: {},
  preactivation: [],
  slots: [
    { ref: { zone: "hand", seat: 1 }, card: watchCard(61, "杀", "spade", "black") },
    { ref: { zone: "hand", seat: 0 }, card: watchCard(62, "闪", "heart", "red") },
    { ref: { zone: "hand", seat: 1 }, card: watchCard(63, "酒", "diamond", "red") },
    { ref: { zone: "hand", seat: 0 }, card: watchCard(64, "桃", "club", "black") },
  ],
  spec: { zones: ["hand"], total: [4, 4], max_per_suit: 1 },
  revealed: [],
  dialog: "watchTwoRows",
};
ui.state.view = { human_seat: 0, pending: watchPending };
ui.state.selectedOption = 0;
for (const index of [3, 1, 2, 0]) ui.toggleWatchPick(watchPending, index);
const watch = ui.buildAction();
if (!watch) throw new Error("buildAction 没拼出两行观看面的 SelectCards（点击夹具失效）");

/* 复合技能动作：两条步骤（选牌 → 选项）→ `ActivateSkill(picks, choices)`（票 04 §3）。
 * 与借刀那条的区别只在载荷形状：`picks` 是实体牌 id、`choices` 是选项文本（不是座位），
 * 这是蛊惑的两步清单（扣置哪张 → 声明成什么牌）拼出来的那条。 */
resetState();
const guohuoPending = {
  kind: "PLAY_PHASE",
  actor: 0,
  seq: 96,
  prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
  legal_actions: [{ kind: "EndPhase", answers_seq: 96 }],
  candidate_index: {},
  virtual_index: {},
  option_seats: {},
  context: null,
  preactivations: {
    "hero.yuji_mobile.guohuo": [
      { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
        pool: [{ when: [], options: ["52"] }], wording: "扣置一张手牌" },
      { name: "name", zones: [], min: 1, max: 1, order: 1,
        pool: [{ when: ["52"], options: ["杀", "闪"], disabled: ["闪"] }],
        wording: "声明一种牌名" },
    ],
  },
  preactivation: [],
  card_preactivations: {},
};
const guohuoPa = {
  skillId: "hero.yuji_mobile.guohuo",
  name: "蛊惑",
  composite: true,
  steps: guohuoPending.preactivations["hero.yuji_mobile.guohuo"],
  answers: [
    { values: [52], texts: ["52"] },
    { values: ["杀"], texts: ["杀"] },
  ],
  cursor: 2,
};
const composite = ui.buildPreativationAction(guohuoPending, guohuoPa);
if (!composite) throw new Error("buildPreativationAction 没拼出复合 ActivateSkill（两步清单夹具失效）");

/* 预发动座位步的**点击顺序**（huo-mobile-batch 票 06 / 验收报障 ③）：
 * 庞统移动版【连环】把一张♣手牌转化成【铁索连环】；产物声明了
 * `preserve_target_order` ⇒ 两种点击顺序在服务端是**两条不同候选**，界面必须把点击
 * 顺序**原样**带进 `choices`。这里点 3 再点 1，期望 `["seat:3", "seat:1"]`——任何按座位序
 * 归一化的写法（排序 / 转集合）都会把它变成升序那条，服务端就只能按座位序结算。
 *
 * 清单形状与服务端 `conversion_preactivations` 同源（来源步 + `min 1 / max 2` 座位步）；
 * 只喂形状，不替服务端算候选（合法性仍由引擎的确认解析裁决）。渲染不是本用例的对象：
 * 两个渲染入口换成空实现，**点击处理器与只读态判据走真的那份**。 */
const lianhuanManifest = [
  {
    name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
    pool: [{ when: [], options: ["52"] }], wording: "选择一张♣手牌",
  },
  {
    name: "seat", zones: [], min: 1, max: 2, order: 1,
    pool: [{ when: [], options: ["seat:0", "seat:1", "seat:2", "seat:3"] }],
    wording: "选择 1 至 2 名角色",
  },
];
const lianhuanPending = {
  kind: "PLAY_PHASE",
  actor: 0,
  seq: 97,
  prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
  legal_actions: [{ kind: "EndPhase", answers_seq: 97 }],
  candidate_index: {},
  virtual_index: {},
  option_seats: {},
  context: null,
  preactivations: { "hero.pangtong_mobile.lianhuan": lianhuanManifest },
  preactivation: [],
  card_preactivations: {},
};
const lianhuanSkill = {
  skill_id: "hero.pangtong_mobile.lianhuan",
  name: "连环",
  preactivated: false,
};

resetState();
context.renderTable = () => {};
context.renderPending = () => {};
ui.state.playback = null;
ui.state.view = { human_seat: 0, pending: lianhuanPending };
ui.enterPreativation(lianhuanSkill, lianhuanManifest);
ui.togglePreativationCard(52); // 来源步：那张♣手牌
/* 票 08（table-ux-overhaul）：清单不再有「下一步」按钮——`min == max` 的单值步填满即
 * **自动**进下一步（没有预览通道时按清单形状本地判，见 `preactivation.js:preactivationAdvanceNow`），
 * 最后一步（这里是 1..2 的多选座位步）填满后停在原地，等底部「提交」。
 * 故这里不再直调 `advancePreativation`：来源步的点牌本身就把它推进了，多选座位步只用
 * 提交判据验"可以交"。 */
if (!ui.state.preactivation || ui.state.preactivation.cursor !== 1) {
  throw new Error("来源步点牌后没有自动进座位步（票 08 的自动推进失效）");
}
ui.selectPreativationSeat(3); // 点击顺序：先 3
ui.selectPreativationSeat(1); // 再 1
if (!ui.preactivationSubmitAllowed(ui.state.preactivation)) {
  throw new Error("多选座位步填满后「提交」不可用（票 08：最后一步等玩家点提交）");
}
const clickOrdered = ui.buildPreativationAction(lianhuanPending, ui.state.preactivation);
if (!clickOrdered) throw new Error("buildPreativationAction 没拼出有序目标的 ActivateSkill（点击夹具失效）");

/* 票 06：未知区域弹窗的**多选档**（【定乱】展示别人的至多 3 张牌，`total=(1,3)`）。
 *
 * 载荷**逐字**抄票面 §2.1：三槽 = 两张「?」手牌 + 装备区可见的青釭剑，七条候选 =
 * 三槽的全部非空组合。界面的读法由两条断言钉住：
 *   - 点两张牌**一次提交都不发**（`submits === 0`）——多选档不保留"点一张即提交"；
 *   - 按**点击顺序 2 → 0** 点完之后，`buildAction()` 造出 `picks = [0, 2]`（升序规范化，
 *     `state.picks` 记的是点击顺序），逐字段命中候选里的 `[0, 2]`——顺序反过来才真的钉住
 *     `answer.js:buildAction` 的规范化（点 0 → 2 时点击顺序恰好已是升序，断言空转）。
 * 对照组是 `cap === 1` 的旧载荷（反间式盲挑）：点一张**恰一次**提交（不回归）。
 * 提交本身被换成计数器（不发 HTTP）：本探针只断"点点击 → 载荷"，请求的落点在烟测。
 */
context.renderTable = () => {};
context.renderPending = () => {};
/* `selectionSnapshot` 住 `session.js`（本探针不加载那份重脚本：它按 `index.html` 顺序
 * 只取构造 / 点击的依赖闭包）。它是 409 回滚用的**本地快照**，不进载荷 ⇒ 这里给个空实现；
 * 真正的回滚路径由 `ui_smoke.mjs` 的 U2 段覆盖。 */
context.selectionSnapshot = () => null;
let submitCalls = 0;
context.submitAction = async () => { submitCalls += 1; };

const qinggangSword = {
  card_id: 39, type: "qinggang_sword", name: "青釭剑", suit: "spade", rank: 6,
  slot: "weapon", attack_range: 2, color: "black", virtual_sources: [],
};
const unknownZonePending = {
  kind: "SELECT_CARDS", actor: 0, seq: 91,
  prompt: "请从选择面里选择一张牌。",
  legal_actions: [[0], [0, 1], [0, 1, 2], [0, 2], [1], [1, 2], [2]].map(
    (picks) => ({ kind: "SelectCards", picks, answers_seq: 91 }),
  ),
  candidate_index: {}, virtual_index: {}, option_seats: {},
  context: null, revealed: null, preactivations: {}, preactivation: [],
  slots: [
    { ref: { zone: "hand", seat: 1 }, card: null },
    { ref: { zone: "hand", seat: 1 }, card: null },
    { ref: { zone: "equip", seat: 1 }, card: qinggangSword },
  ],
  spec: {
    zones: ["hand", "equip"], total: [1, 3],
    per_zone: [], optional: false, sequential: false,
  },
};
resetState();
ui.state.sessionId = "probe";
ui.state.playback = null;
ui.state.view = { human_seat: 0, pending: unknownZonePending };
// 桥对缺失项容错：红要落在断言上，不能是加载期的 TypeError。
if (typeof ui.toggleUnknownZonePick === "function") {
  ui.toggleUnknownZonePick(unknownZonePending, 2); // 点击顺序：先 2
  ui.toggleUnknownZonePick(unknownZonePending, 0); // 再 0（载荷须升序规范化）
}
const unknownMultiSubmits = submitCalls;
const unknownMulti = ui.buildAction();

const blindUnknownPending = {
  kind: "SELECT_CARDS", actor: 0, seq: 92,
  prompt: "请从选择面里选择一张牌。",
  legal_actions: [
    ...[0, 1, 2, 3, 4].map((index) => ({ kind: "SelectCards", picks: [index], answers_seq: 92 })),
    { kind: "Pass", answers_seq: 92 },
  ],
  candidate_index: {}, virtual_index: {}, option_seats: {},
  context: null, revealed: null, preactivations: {}, preactivation: [],
  slots: [0, 1, 2, 3, 4].map(() => ({ ref: { seat: 1, zone: "hand" }, card: null })),
  spec: { zones: ["hand"], total: [1, 1], per_slot: [], optional: true, sequential: false },
};
resetState();
submitCalls = 0;
ui.state.sessionId = "probe";
ui.state.playback = null;
ui.state.view = { human_seat: 0, pending: blindUnknownPending };
if (typeof ui.submitUnknownPick === "function") {
  await ui.submitUnknownPick(blindUnknownPending, 2);
}
const blindSubmits = submitCalls;
const blindPicked = ui.buildAction();
ui.state.sessionId = null;

/* W1：**座位多选面**（`.scratch/chenqun-guyong/issues/05-seat-multi-select.md`）——一次点选多个
 * 场上武将、一次提交。载荷形状取自服务端**真实**产出路径：`SelectOptionsQuery` 的
 * `model_dump` + `projection.option_seats`（`options` 全是 `seat:N` ⇒ 非空），区间 1..2 的候选
 * 由 `effects/actions.py:_select_options` 全量枚举（`tests/test_action_contract.py` 那边用真
 * `legal_actions_for` 逐条核这份 `legal_actions`，不在这里抄规则）。用**真实**的
 * `interaction.js:toggleSeatMultiPick` 按**点击顺序 2 → 0** 点两个场上武将：
 *
 *   - 点座位只切换选中态 ⇒ 两次点击**一次提交都不发**（这一面只有一个提交点：「提交」键）；
 *   - `buildAction()` 造出的 `SelectOptions.choices` 必须按 `pending.options` 原序
 *     （`["seat:0", "seat:2"]`，与点击顺序无关）——引擎按 `options` 序的位掩码枚举候选，
 *     点击顺序若原样上行，逐字段自检会找不到候选（与两行观看面的 picks 升序同一条纪律）；
 *   - 两个座位在**一条** `SelectOptions` 里一起上行（不是"选一个提交一次"的循环面）。
 */
context.renderTable = () => {};
context.renderPending = () => {};
resetState();
submitCalls = 0;
const seatOptionChoices = ["seat:0", "seat:1", "seat:2"];
const seatMultiLegal = [
  ["seat:0"], ["seat:1"], ["seat:2"],
  ["seat:0", "seat:1"], ["seat:0", "seat:2"], ["seat:1", "seat:2"],
].map((choices) => ({ kind: "SelectOptions", choices, answers_seq: 99 }));
const seatMultiPending = {
  kind: "SELECT_OPTIONS", actor: 0, seq: 99,
  prompt: "请选择若干项（可多选）。",
  legal_actions: seatMultiLegal,
  candidate_index: {}, virtual_index: {},
  option_seats: { "seat:0": 0, "seat:1": 1, "seat:2": 2 },
  // 置灰项为空 ⇒ 服务端**不落盘** `disabled`（`_QueryBase._serialize`，票 07）；界面按
  // "没有这个键 = 没有置灰"读（`pending.js:seatMultiDisabledSet`）。置灰行的用例在烟测里。
  options: seatOptionChoices,
  min_selected: 1, max_selected: 2, option_texts: {}, context: null, revealed: [],
  preactivations: {}, preactivation: [],
};
ui.state.sessionId = "probe";
ui.state.playback = null;
ui.state.view = { human_seat: 0, pending: seatMultiPending };
if (typeof ui.toggleSeatMultiPick === "function") {
  ui.toggleSeatMultiPick(2); // 点击顺序：先 2
  ui.toggleSeatMultiPick(0); // 再 0（载荷须按 options 原序，不是点击序）
}
const seatMultiSubmits = submitCalls;
const seatMulti = ui.buildAction();
if (!seatMulti) throw new Error("buildAction 没拼出座位多选面的 SelectOptions（点击夹具失效）");
ui.state.sessionId = null;

/* 票 21：**不枚举档**的座位多选面（`pending.structural`）——候选里只有一条**骨架**（前
 * `min_selected` 个座位 + 放弃），集合由界面按本地选择自己拼出来（服务端按
 * `is_legal_option_set` 校验形状：池内 / 无重复 / 无置灰 / 长度在 `[min, max]` 内）。
 *
 * 与上面那一档的差别只在 `legal_actions` 与 `structural`：点击顺序仍是 2 → 0，上行必须按
 * `options` 原序（`["seat:0", "seat:2"]`）——而且它**不在** `legal_actions` 里（那一档根本
 * 不枚举子集）。前端因此走 `submitAction` 的"不枚举档"让开，不逐字段自检候选。
 */
resetState();
submitCalls = 0;
const structuralSeatPending = {
  kind: "SELECT_OPTIONS", actor: 0, seq: 101,
  prompt: "请选择若干项（可多选）。",
  legal_actions: [
    { kind: "SelectOptions", choices: ["seat:0"], answers_seq: 101 },
    { kind: "Pass", answers_seq: 101 },
  ],
  candidate_index: {}, virtual_index: {},
  option_seats: { "seat:0": 0, "seat:1": 1, "seat:2": 2 },
  options: seatOptionChoices,
  min_selected: 1, max_selected: 2, option_texts: {}, context: null, revealed: [],
  preactivations: {}, preactivation: [],
  structural: true,
};
ui.state.sessionId = "probe";
ui.state.playback = null;
ui.state.view = { human_seat: 0, pending: structuralSeatPending };
if (typeof ui.toggleSeatMultiPick === "function") {
  ui.toggleSeatMultiPick(2); // 点击顺序：先 2
  ui.toggleSeatMultiPick(0); // 再 0（上行仍按 options 原序）
}
const structuralSeatSubmits = submitCalls;
const structuralSeat = ui.buildAction();
if (!structuralSeat) {
  throw new Error("不枚举档的座位多选面没拼出 SelectOptions（骨架被当成答案了）");
}
ui.state.sessionId = null;

/* 票 15：界【渐营】① 的「是否发动」默认 = **发动**（用户 2026-11-09 逐字）。
 *
 * 服务端只对**显式声明**的技能下发 `pending.default_choice === "activate"`；玩家没点任何
 * 候选（`selectedOption = null`）时，`buildAction()` 必须返回候选里那条裸 `ActivateSkill`
 * ——「提交」就是同意，不必先点技能按钮。对照组 = **逐字段相同但不带该键**的载荷：
 * `buildAction()` 仍是 null（其余「是否发动」苦肉 / 洛神 / … 一字不变）。
 *
 * 只喂形状：合法性仍由引擎按 `legal_actions` 裁决，探针不另算规则。 */
resetState();
const jianyingPending = {
  kind: "SKILL_ACTIVATION", actor: 0, seq: 100,
  prompt: "是否发动【渐营】？", skill_id: "hero.jie_jushou_mobile.jianying",
  legal_actions: [
    { kind: "ActivateSkill", skill_id: "hero.jie_jushou_mobile.jianying", answers_seq: 100, picks: [], choices: [] },
    { kind: "Pass", answers_seq: 100 },
  ],
  candidate_index: {}, virtual_index: {}, option_seats: {},
  context: null, preactivations: {}, preactivation: [],
  default_choice: "activate",
};
ui.state.view = { human_seat: 0, pending: jianyingPending };
ui.state.selectedOption = null;
const defaultActivate = ui.buildAction();
if (!defaultActivate) throw new Error("buildAction 没把 default_choice 变成那条 ActivateSkill");

// 对照组：同形载荷但**没有**这个键（服务端对未登记技能就是不发键，不是发 null）。
const otherActivationPending = { ...jianyingPending, seq: 101 };
delete otherActivationPending.default_choice;
ui.state.view = { human_seat: 0, pending: otherActivationPending };
ui.state.selectedOption = null;
const noDeclaredDefault = ui.buildAction();

/* 参数模式（yijiang-2014-round2 票 17）：把**真实**询问载荷（Python 侧用
 * `sgs_web.projection.pending_payload` 投影出来的那份）喂给同一批构造器，打印客户端会发
 * 的那一条动作。这是"窗口里点技能 → 选牌 → 提交"回环的客户端半边：
 *
 *   - `selected_option`：`selectSkillCandidate` 写的那一格（窗口里的 `ActivateSkill`
 *     候选 = 提供者入口，点技能按钮就是选它）；
 *   - `click_slot`：**真实点击入口** `submitOwnHandSinglePick`（自己手牌 / 装备区单张，
 *     一击即选中；它自己写 `state.picks` + 候选骨架的指向）；
 *   - `seat_multi_picks`：**真实点击入口** `toggleSeatMultiPick` 按点击顺序逐个点座位
 *     （座位多选面；`structural` 档与枚举档都是这一口，界面自己拼集合）。
 *
 * 合成载荷（无会话）⇒ 点击只留内部态、不发请求，正是本探针要的"点击 → 载荷"。 */
const liveInput = process.argv[2];
if (liveInput) {
  const fixture = JSON.parse(readFileSync(liveInput, "utf8"));
  resetState();
  ui.state.sessionId = "";
  ui.state.playback = null;
  ui.state.view = { human_seat: fixture.human_seat, pending: fixture.pending };
  if (fixture.selected_option !== null && fixture.selected_option !== undefined) {
    ui.state.selectedOption = Number(fixture.selected_option);
  }
  if (fixture.click_slot !== null && fixture.click_slot !== undefined) {
    ui.submitOwnHandSinglePick(fixture.pending, Number(fixture.click_slot));
  }
  if (fixture.seat_multi_picks) {
    /* 座位多选面的半边（评审 P1-b 的真实载荷回环）：按 ``seat_multi_picks`` 的**点击顺序**
     * 逐个点场上武将（`interaction.js:toggleSeatMultiPick`，经 ui_bridge 桥出），再让
     * `buildAction()` 按 `pending.options` 原序拼出那条 `SelectOptions`。两个座位界面的
     * 唯一差别只在载荷的 `structural`：这一档候选里只有骨架，集合由界面自己拼，
     * 故这里不另写分支——判据全在前端那一份实现里。 */
    for (const seat of fixture.seat_multi_picks) {
      ui.toggleSeatMultiPick(Number(seat));
    }
  }
  if (fixture.enter_preactivation) {
    /* 复合动作的半边（yijiang-2014-round2 票 20）：**点已翻预发动的技能**（= 进入清单）
     * → 按 ``click_seats`` 逐个点座位 → 拼 ``ActivateSkill(picks, choices)``。
     * 入口都是真实的前端函数（`enterPreativation` / `selectPreativationSeat` /
     * `buildPreativationAction`，经 `ui_bridge` 桥出），探针不另抄一份点击逻辑。 */
    const skillId = String(fixture.enter_preactivation);
    const manifest = ((fixture.pending.preactivations || {})[skillId]) || [];
    ui.enterPreativation(
      { skill_id: skillId, name: "", preactivated: true },
      manifest,
    );
    for (const seat of fixture.click_seats || []) {
      ui.selectPreativationSeat(Number(seat));
    }
    const preactivationAction = ui.buildPreativationAction(
      fixture.pending, ui.state.preactivation,
    );
    process.stdout.write(JSON.stringify({ action: preactivationAction }));
    process.exit(0);
  }
  const action = ui.buildAction();
  process.stdout.write(JSON.stringify({ action }));
  process.exit(0);
}

/* 合成反例：同一张借刀卡的**展示形态**原样塞进 UseCard.card（实机 400 的形状）。 */
const decorated = {
  kind: "UseCard",
  card: borrowedDisplay,
  targets: [1],
  designations: [2],
  answers_seq: 93,
};

process.stdout.write(
  JSON.stringify({
    cases: [
      { name: "borrowed_sword_preactivation", action: borrowed },
      { name: "two_step_preactivation", action: composite },
      { name: "seat_order_preactivation", action: clickOrdered },
      { name: "use_card", action: use },
      { name: "play_card", action: play },
      { name: "watch_two_rows", action: watch },
      // 票 06：未知区域弹窗的两档（多选 = 点选后提交 / 单选 = 一击即提交）。
      {
        name: "unknown_zone_multipick",
        action: unknownMulti,
        legal_actions: unknownZonePending.legal_actions,
        submits: unknownMultiSubmits,
      },
      {
        name: "unknown_zone_single_pick",
        action: blindPicked,
        legal_actions: blindUnknownPending.legal_actions,
        submits: blindSubmits,
      },
      // W1：座位多选面 —— 连同**载荷**一起打印，Python 侧用真引擎的候选枚举 / 投影核它。
      {
        name: "seat_multi_select",
        action: seatMulti,
        legal_actions: seatMultiPending.legal_actions,
        submits: seatMultiSubmits,
        payload: {
          kind: seatMultiPending.kind,
          options: seatMultiPending.options,
          disabled: seatMultiPending.disabled,
          option_seats: seatMultiPending.option_seats,
          min_selected: seatMultiPending.min_selected,
          max_selected: seatMultiPending.max_selected,
          answers_seq: seatMultiPending.seq,
        },
      },
      // 票 21：**不枚举档**的同一张座位多选面 —— 候选只有骨架，界面自己拼集合。
      {
        name: "seat_multi_select_structural",
        action: structuralSeat,
        legal_actions: structuralSeatPending.legal_actions,
        submits: structuralSeatSubmits,
        payload: {
          kind: structuralSeatPending.kind,
          options: structuralSeatPending.options,
          disabled: [],
          option_seats: structuralSeatPending.option_seats,
          min_selected: structuralSeatPending.min_selected,
          max_selected: structuralSeatPending.max_selected,
          answers_seq: structuralSeatPending.seq,
          structural: structuralSeatPending.structural,
        },
      },
      // 票 15：「是否发动」的默认候选 —— 显式声明的技能提交 = 发动；不带该键的对照组仍是 null。
      {
        name: "default_activate",
        action: defaultActivate,
        legal_actions: jianyingPending.legal_actions,
        without_declaration: noDeclaredDefault,
      },
    ],
    decorated,
  }),
);
