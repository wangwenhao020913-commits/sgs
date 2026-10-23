/* 客户端动作探针：用**真实前端脚本**构造动作，打印 JSON 给 Python 侧喂 `parse_action`。
 *
 * 为什么要 Node：M5 报障的错型是"跨语言载荷形状失配"（展示卡 → `UseCard.card` →
 * 服务端 `extra="forbid"` 400）。Python 里手写一份"客户端会发什么"只能测到假想；这里用
 * `vm` 加载 `tools/sgs_web/static/` 的真脚本 + `tests/support/ui_bridge.js` 的桥，调用
 * 真实构造器，输出的就是浏览器会发的那条 JSON。
 *
 * 加载纪律与 `ui_smoke.mjs` 同姿态：脚本清单来自 `index.html` 的 `<script src>` 顺序；
 * 这里只取 `buildAction` 依赖闭包（core → candidates → preactivation → pending → arrange
 * → answer），不加载 `main.js`（它在末尾 `wire()`，需要 DOM）。桥会检查每个名字真的存在：
 * 少一个就抛错，而不是让 Python 侧收到 `undefined` 后误判。
 */

import { readFileSync } from "node:fs";
import vm from "node:vm";

const STATIC_DIR = "tools/sgs_web/static";
const NEEDED = [
  "core.js",
  "candidates.js",
  "preactivation.js",
  "pending.js",
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
for (const name of ["buildAction", "buildPreativationAction", "bareCardRef", "state"]) {
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
      { name: "use_card", action: use },
      { name: "play_card", action: play },
    ],
    decorated,
  }),
);
