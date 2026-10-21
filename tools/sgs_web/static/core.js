/* SGS M4 极简界面：只做两件事——渲染事件流、收集并提交应答。
 *
 * 纪律（`04` §4.1 与地图 D8；本轮裁定见 `.scratch/m4-manual-select/spec.md`）：
 *  - 前端**不做规则判定**：合法性只问"这个组合在不在候选索引里"，不读距离 / 花色 / 体力 / 存活；
 *  - **手选卡牌 + 手选目标**：点牌设**焦点**，点座位选目标，再点一次取消；
 *  - **自动选中**只在"合法组合恰好一组、且那组就是池的全部"时触发（省掉一次没有信息量的点击）；
 *  - 提交的 Action **原样取自** legal_actions（不自己拼 Action）；
 *  - 隐藏信息在传输层已被过滤；界面**不自己代答超时**，只显示倒计时（代答在服务端）。
 *
 * **跨线契约**：`pending.candidate_index` 是 `{card_id: entry}`，界面读的字段**只有这四个**——
 *
 *     entry.card          这张牌的牌面（`CardRef`）
 *     entry.pool          合法目标座位
 *     entry.min_targets   这次使用最少要选几个目标
 *     entry.max_targets   这次使用最多能选几个目标
 *     entry.combos        合法目标组合（元组的数组）
 *
 * 真源在 `tools/sgs_web/projection.py:candidate_index`；键名两边必须**逐字**一致。
 * 只断言服务端"发了这个键"是不够的——服务端断言与界面读法之间曾经失配过
 * （界面读 `entry.max` 而服务端发 `max_targets`），后果是 `needsPicker()` 恒假、
 * 唯一一条带目标的候选在未交互时就能提交。故 `ui_smoke.mjs` 的 ㉿K1–K4 两侧都钉。
 *
 * **拆分（U1-00）**：本文件是 `static/` 下按顺序加载的一组普通脚本的第一份。
 * `index.html` 的 `<script src>` 顺序 = 浏览器运行顺序 = `tests/support/ui_smoke.mjs`
 * 拼接加载的顺序；脚本之间不导出模块，`function` 与顶层 `const` / `let` 靠同一份全局
 * 词法作用域互相可见。每一份都带 `"use strict";`（拆分前整文件是严格模式）。
 * 跨文件调用只发生在 `main.js` 末尾的 `wire()` 之后——此前各脚本只声明，不执行。
 */
"use strict";

const $ = (id) => document.getElementById(id);

/** 卡名：服务端展示契约优先（火杀 / 雷杀 → 「杀」）。**不自己映射牌名**。
 *
 * 展示字段挂在**只读载体**上（`candidate_index` / 快照 / 选择面 / 授予的牌面）；
 * `legal_actions` 里的牌故意不带 `display_*`——界面会把选中的候选原样回传，
 * 而 Action 模型 `extra="forbid"`，多一个键就让提交 400。故候选的卡名按 `card_id`
 * 回查 `candidate_index`（那里是展示副本）；旧录像 / 合成载荷退回卡面自带的 `name`。
 */
function cardName(card) {
  if (!card) return "？";
  if (card.display_name) return card.display_name;
  const pending = state.view && state.view.pending;
  const index = (pending && pending.candidate_index) || {};
  const entry = index[String(card.card_id)];
  // 只认同 id 且**同名**的索引项：回放页消费的是录像里的牌，别拿当前对局的同号牌
  // （换内容包时 id 可能撞）去改它的牌名。
  const display = entry && entry.card;
  if (display && display.display_name && display.name === card.name) return display.display_name;
  return card.name || "？";
}

/** 描述全文（U1-04b）：服务端把 `descriptions.json` 的条目并入每张牌 / 每条技能。
 *
 * 文案是**内容**，只住 `tools/sgs_web/descriptions.json`；前端**不抄文案**。
 * 缺条目时显式显示「暂无描述」——不返回空串，免得界面静默留白。
 */
const DESCRIPTION_FALLBACK = "暂无描述";

function cardDescription(card) {
  if (!card) return DESCRIPTION_FALLBACK;
  return card.description || DESCRIPTION_FALLBACK;
}

function skillDescription(skill) {
  if (!skill) return DESCRIPTION_FALLBACK;
  return skill.description || DESCRIPTION_FALLBACK;
}

/** 技能类别徽标（W4-fix-3 / U1-06）：**触发技**由服务端投影判（`ui.passive` = 有 hooks /
 * 接缝且没有主动入口），界面不按 `skill_type` 猜——`NORMAL` 里既有主动技也有被动技。
 * 技能按钮区与武将详情共用这一份文案，两处不会各自漂。 */
function skillKindLabel(skill) {
  const ui = (skill && skill.ui) || {};
  const passive = Boolean(ui.passive);
  if (skill && skill.carrier === "EQUIPMENT") {
    return skill.locked ? "装备技 / 锁定" : "装备技";
  }
  if (skill && skill.locked) return "锁定技 / 被动";
  if (skill && skill.skill_type === "LORD") return "主公技";
  return passive ? "被动技" : "主动技";
}

/** `Phase` 枚举的字面值 → 中文阶段名。
 *
 * 枚举值（`prepare` / `judge` / `draw` / `play` / `discard` / `finish`）是**稳定报文值**，
 * 改它就是改契约（还会波及记录流与基准），所以映射只住表现层这一处。
 * `null` 段也覆盖到：`phase` 可能为 `null`（回合之外）。
 */
const PHASE_LABELS = {
  prepare: "准备阶段",
  judge: "判定阶段",
  draw: "摸牌阶段",
  play: "出牌阶段",
  discard: "弃牌阶段",
  finish: "结束阶段",
};

function phaseLabel(phase) {
  if (!phase) return "—";
  return PHASE_LABELS[phase] || phase;
}

/** 走手选的询问类（出牌阶段 / 响应窗口 / 【闪】窗口 / 求桃窗口）。
 *
 * 弃牌阶段的交互是**另一条**手选路（点手牌收进待弃置集合，没有目标要选），
 * 由 :func:`isDiscardPick` 单独认出来——它不进这里，免得 `pickerSeats` 去读候选索引。
 * 其余（排列 / 选择面 / 技能发动 / 单选）保留旧的候选列表按钮。
 *
 * 求桃（W4-fix-3）：`legal_actions` 里是**真【桃】**的使用（急救转化桃已收回到技能入口，
 * `spec.md` §1 裁定 A），故"点手牌【桃】= 选中对应候选"；目标恒为濒死者，由候选的
 * `combos` 自动选中。**只加可点，不改候选面**。
 */
const HAND_PICK_KINDS = ["PLAY_PHASE", "RESPOND", "DODGE", "PEACH"];

/** `seat:` 前缀 = **跨语言副本**（W6-07 / 评审 J4）。
 *
 * 规范声明住 `sgs.skills.flow.SEAT_PREFIX`（经公开入口 `sgs.skills` 给接入层用）；JS 与
 * Python 之间没有共享常量的通道，故这里是**唯一**的 JS 副本，全文件只此一处字面量。
 * 改前缀 = 两头一起改。
 */
const SEAT_PREFIX = "seat:";

/** `"free"` = 自由切分的**线上取值**（`DeckTopArrange.split` 的 `FREE_SPLIT`，W6-07 / J5）。
 *
 * 与 Python 的 `sgs.skills.FREE_SPLIT` 同一个值、同一处语义（投影原样转发）；同
 * `SEAT_PREFIX`，跨语言只能各留一份常量，别在别处再写字符串字面量。
 */
const FREE_SPLIT = "free";

/** 事件回放的步长档位（毫秒）：每一条事件在事件流 / 效果区停留这么久。
 *
 * 报障 3：AI 一回合的结算在一次响应里全部返回，界面**一次性**画完，于是【杀】【闪】
 * 【桃】在效果区一闪而过。改成逐条回放后，牌会在效果区停满一个步长。
 * **严格顺序**（用户裁定）：这一批播完才提交新的快照与询问，故播放期间禁掉提交。
 *
 * 步长改成牌桌上的按钮（0.05–0.5 秒，点一下换一档）；缺省 0.1 秒，**不记住**——
 * 刷新/换局回默认，故它只住内存（`state.eventDelayMs`），不落 localStorage。
 */
const DELAY_STEPS_MS = [50, 100, 200, 300, 400, 500];

const state = {
  sessionId: null,
  cursor: -1,
  logSeq: 0,
  view: null,          // 服务端最近一次下发的会话视图
  focusCardId: null,   // **焦点**：正在挑目标的那张候选牌（再点一次取消）
  selectedTargets: [], // 已选目标座位
  selectedOption: null,// 旧路径：选中的 legal_action 下标（弃牌 / 排列 / 单选 / 技能）
  picks: [],           // 手选槽位下标 / 弃牌 card_id / 提供面的面内位置
  arrange: null,       // 排列（W6-03）：{key,cards,count,free,fixed,top,bottom,selected,dragging}
  preactivation: null, // 预发动（W4-1）：{skillId,name,steps,answers,cursor}；本地态，取消零提交
  convertSkill: null,  // 转化技"挑来源牌"模式（【龙胆】）：正在等玩家点一张可转化的手牌
  playback: null,      // 事件回放：{body, records, index, querySeat}
  eventDelayMs: 100,   // 回放步长（牌桌按钮切档）；缺省 0.1s **不随档位表首位走**
  effectCards: [],     // 效果区里当前可见的牌；回放期间由 card_move delta 逐事件派生
  timer: null,
  // U1-03：倒计时的本地锚点。服务端的 `pending_since` 是单调时钟，浏览器不能直接比；
  // 这里只记**第一次见到这个询问序号**的本地时刻，轮询刷新同一询问时不重置。
  pendingClock: { seq: null, receivedAt: 0 },
  // U1-03：共享模态的本地态（kind = preactivation / arrange / hero / null；dismissed 只对
  // 强制选择（排列）生效——关掉模态后仍可从提示条右端重新打开）。U1-06 的武将详情用
  // `heroSeat` 记座位：它独立于询问，右键任何武将块都能开。
  modal: { kind: null, open: false, dismissed: false, heroSeat: null },
  runs: [],
  replay: null,
  heroCatalog: [],     // 点将列表（`GET /api/heroes` 的启用池）；开局页的选项来源
  logRecords: [],
  lastQuerySeat: null, // 事件流里"上一条询问的 actor"（action 记录靠它归属）
  narrative: [],       // U1-05：服务端下发的叙事条目（简易事件流；按 seq 升序）
  narrativeShown: 0,   // 已经铺进 DOM 的叙事条目数（增量 / 切换详略时接着铺）
  lastRejectReason: "",   // 已收到的全部下发记录（切换详略时重画用）
};

/* ------------------------------------------------------------------ 网络 */

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = { error: text }; }
  if (!response.ok) {
    const error = new Error((body && body.error) || `HTTP ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

const post = (path, payload) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload || {}),
});

/* ------------------------------------------------------------------ 视图 */

function show(view) {
  for (const name of ["lobby", "table", "replay"]) {
    $(`view-${name}`).hidden = name !== view;
    $(`nav-${name}`).classList.toggle("on", name === view);
  }
  // 离开回放页就停掉播放：定时器不该在看不见的页面上继续跑。
  if (view !== "replay") stopReplayPlay();
  // U1-03：牌桌的三段布局不在屏幕上时，共享模态也收起（不在别的页面上留一层遮罩）；
  // 回到牌桌时按当前询问重新挂上（预发动 / 排列的本地态不因切页丢失）。
  if (view === "table") {
    if (state.view) syncModal();
  } else {
    closeModalForView();
  }
}
