/* SGS M4 极简界面：只做两件事——渲染事件流、收集并提交应答。
 *
 * 纪律（`04` §4.1 与地图 D8；本轮裁定见 `.scratch/m4-manual-select/spec.md`）：
 *  - 前端**不做规则判定**：合法性只问"这个组合在不在候选索引里"，不读距离 / 花色 / 体力 / 存活；
 *  - **手选卡牌 + 手选目标**：点牌设**焦点**，点座位选目标，再点一次取消；
 *  - **自动选中**只在"合法组合恰好一组、且那组就是池的全部"时触发（省掉一次没有信息量的点击）；
 *  - 提交的 Action **原样取自** legal_actions（不自己拼 Action）；
 *  - 隐藏信息在传输层已被过滤；界面**不自己代答超时**，只显示倒计时（代答在服务端）。
 *    **唯一例外**：观星排列（`ARRANGE`）到时限时前端主动提交一次**当前排列**——服务端
 *    看不到客户端摆放的顺序（spec D9.3 / 票 07）；其余询问到时限仍是零请求。
 *
 * **跨线契约**：`pending.candidate_index` 是 `{card_id: entry}`，界面读的字段**只有这几个**——
 *
 *     entry.card          这张牌的牌面（`CardRef`）
 *     entry.pool          合法目标座位
 *     entry.min_targets   这次使用最少要选几个目标
 *     entry.max_targets   这次使用最多能选几个目标
 *     entry.combos        合法目标组合（元组的数组）
 *     entry.self_only     这张牌的目标由**牌自己的规则**钉死为使用者本人（不画座位高亮）
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

/** `CardRef` 的字段闭集（M5-bugfix）：与服务端 ``sgs.state.CardRef`` 逐字一致。
 *
 * 跨语言没有共享常量的通道，故这是**唯一**的 JS 副本；改服务端模型 = 两头一起改。
 * 提交 ``UseCard.card`` / ``PlayCard.card`` / ``SubstituteJudgeCard.card`` 时只能带这九个键
 * ——服务端模型是
 * ``extra="forbid"``，多一个 ``display_name`` / ``attribute`` / ``description`` 就是
 * ``UseCard.card.attribute Extra inputs are not permitted``（借刀预发动的报障原样）。
 */
const CARD_REF_KEYS = Object.freeze([
  "card_id",
  "type",
  "name",
  "suit",
  "rank",
  "slot",
  "attack_range",
  "color",
  "virtual_sources",
]);

/** 展示卡 → **纯 CardRef**：只保留 :data:`CARD_REF_KEYS` 里、且原对象**真有**的键。
 *
 * 展示字段（``display_name`` / ``attribute`` / ``description`` / ``kind`` /
 * ``source_cards`` …）只挂在只读载体上，**绝不随提交上行**。缺省字段不补——服务端的
 * ``CardRef`` 自己带缺省值，界面不发明第二份牌面。
 */
function bareCardRef(card) {
  if (!card) return card;
  const out = {};
  for (const key of CARD_REF_KEYS) {
    if (Object.prototype.hasOwnProperty.call(card, key) && card[key] !== undefined) {
      out[key] = card[key];
    }
  }
  return out;
}

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

/** 技能按钮上的**名字**（报障 2026-11-01）：底部技能区是固定 **3 列 × 6 行**的等大网格，
 *  格子宽度不再由名字长度决定，故按钮只见名字的前 :data:`SKILL_NAME_MAX_CHARS` 个字，
 *  超出截断加省略号；完整名字 / 类别 / 状态仍由 `renderSkills` 写进 `title` 与 `aria-label`，
 *  截断不丢信息（悬停 / 读屏照样读得到）。
 *
 *  按**字**切（`Array.from`）而不是按 UTF-16 码元：代理对不会被切一半。
 *  名字缺失时返回空串（fail-visible：按钮上就是空的，不编名字）。 */
const SKILL_NAME_MAX_CHARS = 3;

function shortSkillName(name) {
  const chars = Array.from(String(name === undefined || name === null ? "" : name));
  if (chars.length <= SKILL_NAME_MAX_CHARS) return chars.join("");
  return `${chars.slice(0, SKILL_NAME_MAX_CHARS).join("")}…`;
}

/** 技能**三态**的徽标文案（M6 票 19；`player.skills[].state` 是稳定报文值）。
 *
 * - `suppressed` = **失效**：有但被挡住（实例还在，作用域到期恢复）；
 * - `lost` = **失去**：没了（实例不再存在且不可恢复）；
 * - `active`（或缺字段）= 不出徽标，不制造噪声；未登记的值同样不出字（fail-closed）。
 *
 * 文案只住这一处：武将详情与技能按钮区读同一份，两处不会各写一套。
 */
const SKILL_STATE_LABELS = { suppressed: "失效", lost: "失去" };

function skillStateLabel(skill) {
  return SKILL_STATE_LABELS[(skill && skill.state) || ""] || "";
}

/** ☆ 技能标记（M6 票 19）：觉醒后由某条觉醒技**获得**的技能（服务端 `star`）。
 *  未觉醒时它根本不在清单里（载体解析的事实），故这里只负责"出现之后怎么标"。 */
function skillStarLabel(skill) {
  return skill && skill.star ? "☆" : "";
}

/** 武将 id → 中文名：目录（`GET /api/heroes`，标准 + 界版的启用并集）是服务端唯一的
 *  名单来源（M6 票 19）。服务端已把 `hero_name` 翻好；这里只服务**旧载荷 / 合成视图**
 *  （没有 `hero_name`）与回放页的座位名——缺目录 / 未知 id 时退回 id，**不编名字**。 */
function heroDisplayName(heroId) {
  if (!heroId) return "";
  for (const group of state.heroCatalog || []) {
    for (const version of group.versions || []) {
      if (version.id === heroId) return version.name;
    }
  }
  return String(heroId);
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
 * 其余询问各走自己的专用面（选择面 / 排列 / 技能直提 / 小选项组），**没有**候选列表逃生梯。
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

/** 事件回放的步长档位（毫秒）：每一条事件在事件流 / 效果区停留的**基准**。
 *
 * 报障 3：AI 一回合的结算在一次响应里全部返回，界面**一次性**画完，于是【杀】【闪】
 * 【桃】在效果区一闪而过。改成逐条回放后，牌会在效果区停满一个步长。
 * **严格顺序**（用户裁定）：这一批播完才提交新的快照与询问，故播放期间禁掉提交。
 *
 * U4-02（spec D7 / D8）：整条记录的间隔不再等于固定步长，而是
 * `base + dur(记录) × (base / 100)`（`session.js:delayOf`）——`base` 既是**下限**，
 * 也是该条动画的**缩放因子**（50ms 档 = 0.5×，300ms 档 = 3×）；没有动画的记录只吃 `base`。
 *
 * 步长改成牌桌上的按钮（0.03–0.3 秒五档，点一下换一档）；缺省 **0.1 秒**，
 * **不记住**——刷新/换局回默认（`anim.ini` 的 `base_step` 只决定加载时的初始档），
 * 故它只住内存（`state.eventDelayMs`），不落 localStorage。
 */
const DELAY_STEPS_MS = [30, 50, 100, 200, 300];

const state = {
  sessionId: null,
  cursor: -1,
  logSeq: 0,
  view: null,          // 服务端最近一次下发的会话视图（**权威**提交视图）
  liveView: null,      // U3-05：回放期间逐事件派生的视图副本（D11；提交时被权威快照覆盖）
  focusCardId: null,   // **焦点**：正在挑目标的那张候选牌（再点一次取消）
  selectedTargets: [], // 已选目标座位
  selectedOption: null,// 内部选中态：选中的 legal_action 下标（技能 / 小选项 / 右端动作）
  picks: [],           // 手选槽位下标 / 弃牌 card_id / 提供面的面内位置
  // 排列（W6-03 / 票 01）：{key,cards,count,free,fixed,top,bottom,selected,dragging,
  // dragNode,nodes,preview,geometry}；后四个是拖拽期的纯 DOM 引用 / 折叠几何，不进载荷。
  arrange: null,
  preactivation: null, // 预发动（W4-1）：{skillId,name,steps,answers,cursor}；本地态，取消零提交
  convertSkill: null,  // 转化技"挑来源牌"模式（【龙胆】）：正在等玩家点一张可转化的手牌
  playback: null,      // 事件回放：{body, records, index, querySeat, live}
  eventDelayMs: 100,   // 回放步长（牌桌按钮切档）；缺省 0.1s **不随档位表首位走**
  effectCards: [],     // 效果区里当前可见的牌；回放期间由 card_move delta 逐事件派生
  revealedCards: [],   // M5-08a：公开亮牌（card_reveal delta / pending.reveal）；按帧在舞台显示
  targetLines: [],     // U3-03：正在播的目标线（{node, from, to, enteredAt}）；由 effects.js 维护
  skillEvents: [],     // U3-04：随视图下发、尚未到 seq 的技能发动（{seq, seat, skill_id, name, targets}）
  // chain-death-voices 票 03：随视图下发、尚未到 seq 的卡牌使用 / 打出事实
  // （{seq, seat, card_type, card_name, gender, virtual}）——回放到那条记录的 seq 才出声。
  cardEvents: [],
  processingProducts: {}, // U1-07：来源实体牌组 → 正在结算的虚拟 / 转化产物（快照派生）
  timer: null,
  // U1-03：倒计时的本地锚点。服务端的 `pending_since` 是单调时钟，浏览器不能直接比；
  // 这里只记**第一次见到这个询问序号**的本地时刻，轮询刷新同一询问时不重置。
  pendingClock: { seq: null, receivedAt: 0 },
  // U2-07（spec D9.3）：观星排列到时限**由前端代答一次当前排列**——本仓「前端不代答」
  // 纪律的唯一例外。这里只记"已经自动提交过哪一问"（`pending.seq`）：重复的倒计时
  // tick / 重复渲染不会发第二次；网络失败也不重试（服务端超时兜底）。
  arrangeAutoSeq: null,
  // 票 01：涉猎的"提交 / 放弃 / 超时"同义——到时限前由前端提交**当前选择**（与观星同一
  // 个提前量）。`suitAutoSeq` 记"已经自动提交过哪一问"；`suitResult` 是提交成功后本地的
  // 2.5 秒结果态（`{cards, picked, until, title}`，仅发动者的屏幕）。
  suitAutoSeq: null,
  // 票 05：计数式分配（`DISTRIBUTE`）的本地计数 `{seq, counts: {seat: n}}`；
  // 超时由前端把**当前计数**提交出去（与服务端全 0 兜底同一条语义）。
  distribution: null,
  distributionAutoSeq: null,
  suitResult: null,
  suitResultTimer: null,
  // U2-06：询问弹窗（`#t-dialog`）的本地态。`kind` = `stepDialogKind()` 的种类或
  // `"preactivation"`（预发动清单借同一个容器，但仍是牌桌表面）；`key` = 当前窗口实例
  // （同一问的重复渲染保留折叠，换问关旧开新）；`collapsed` 只改遮挡、不参与只读判据。
  // `persistent` = 五谷整段结算的**持久展示**（review-fix / D4）：这一步的表面是牌桌
  // （无懈 / 反无懈的 RESPOND）但结算帧还活着，窗口只展示亮牌 + 目标条；它不进只读判据。
  // `hintOverride` = 持久展示时标题条要镜像的步骤提示（普通模式为 null，读 `#t-target-hint`）。
  dialog: {
    open: false,
    collapsed: false,
    kind: null,
    key: null,
    persistent: false,
    hintOverride: null,
  },
  // U1-06：武将详情是**唯一**可叠加的检视窗（独立的 `#t-modal`），`heroSeat` 记座位：
  // 它独立于询问，右键任何武将块都能开；开合不改变只读态。
  modal: { heroSeat: null },
  runs: [],
  replay: null,
  heroCatalog: [],     // 将池目录：按真名分组的启用版本（`GET /api/heroes`；票 13 起分组）
  heroPools: null,     // 将池预设的本地编辑态 `{slots, active, selected}`（票 13；服务端存档在 runs/）
  deckCatalog: [],     // 牌堆档位列表（`GET /api/decks`）；标签 / 说明也由服务端下发
  logRecords: [],
  lastQuerySeat: null, // 事件流里"上一条询问的 actor"（action 记录靠它归属）
  narrative: [],       // U1-05：服务端下发的叙事条目（简易事件流；按 seq 升序）
  narrativeShown: 0,   // 已经铺进 DOM 的叙事条目数（增量 / 切换详略时接着铺）
  lastRejectReason: "",   // 已收到的全部下发记录（切换详略时重画用）
  // U2-03：提交在飞时冻结提示区四键。`actionFreeze` = 点击前四键的 disabled / hidden /
  // 文案快照（失败时原样恢复）；`submitBusy` = 有 POST 在飞（挡住重复提交与轮询重绘）。
  submitBusy: false,
  actionFreeze: null,
  // table-ux-overhaul 票 15：自家手牌选牌面的**合法性预览**本地态（`candidates.js` 的
  // `ownPickPreview*` 是唯一读写方）。`seq` = 这份态属于哪一问；`asked` = 回包是按哪一组
  // picks 问的（升序逗号分隔，`null` = 还没有回包）；`disabled` = 服务端回的 `slot:N`
  // 禁用集；`key` / `version` / `timer` 是"同一状态只问一次 + 丢弃过期回包"的那套账；
  // `error` / `retryAt` 是 fail-closed 态与重问间隔。**不进任何载荷**。
  ownPickPreview: {
    seq: null, asked: null, key: null, disabled: null,
    canSubmit: false, canAdvance: false, error: false,
    version: 0, timer: null, retryAt: 0,
  },
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

/** 三个页面各有一条顶栏、同一组导航三键（U2-01 / spec D7）。
 *
 * 同一页面里的 DOM id 必须唯一，故三套顶栏各用自己的前缀；**数组顺序就是顶栏里的
 * 显示顺序**（开局 / 牌桌 / 回放），三页必须共用这一张表，改导航只改这里。
 * 牌桌沿用原有的 `nav-*`（旧全局 header 的 id），开局 / 回放页用 `<page>-nav-*`。
 */
const NAV_BUTTONS = {
  lobby: ["nav-lobby", "lobby-nav-lobby", "replay-nav-lobby"],
  table: ["nav-table", "lobby-nav-table", "replay-nav-table"],
  replay: ["nav-replay", "lobby-nav-replay", "replay-nav-replay"],
};

/** 「牌桌」能否点：只认有没有会话（建局成功后 `state.sessionId` 非空；刷新即丢，不落盘）。
 *
 * 三处顶栏的禁用态因此只有一个来源；`show()` 与建局成功都经这条函数同步。
 */
function syncNavState() {
  const enabled = state.sessionId !== null && state.sessionId !== undefined;
  for (const id of NAV_BUTTONS.table) {
    const button = $(id);
    if (button) button.disabled = !enabled;
  }
}

/** 页面级提示：全局 header 的 `#status` 随 U2-01 删除，提示就近写到**当前页面**已有的
 * 文本位（开局页 = 建局提示，牌桌 = 提示条右下的选择说明，回放页 = 空壳引导）。
 *
 * 只用于跨页可见的错误 / 终局提示；各页面自己的局部提示仍走各自的节点。
 */
function showPageNotice(text) {
  const target = $("view-replay").hidden === false ? $("r-empty-guide")
    : $("view-table").hidden === false ? $("t-choice")
      : $("lobby-hint");
  if (!target) return;
  // 票 07：牌桌那一格是**有寿命**的页脚（唯一写入口在 `pending.js:setAskChoice`），
  // 逾期自己消失——否则一次跨页错误会一直挂在询问条右端，直到下一次重绘。
  if (target === $("t-choice")) {
    setAskChoice(text, ASK_CHOICE_VERDICT_TTL_MS);
    return;
  }
  target.textContent = text;
}

function show(view) {
  for (const name of ["lobby", "table", "replay"]) {
    $(`view-${name}`).hidden = name !== view;
    // 当前页的导航键在三处顶栏里同步高亮；其它两页的同一位置保持普通态。
    for (const id of NAV_BUTTONS[name]) {
      const button = $(id);
      if (!button) continue;
      const current = name === view;
      button.classList.toggle("on", current);
      if (current) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
  }
  syncNavState();
  // 离开回放页就停掉播放：定时器不该在看不见的页面上继续跑。
  if (view !== "replay") stopReplayPlay();
  // U1-03 / U2-06：牌桌的三段布局不在屏幕上时，询问弹窗与武将详情都收起（不在别的页面上
  // 留一层遮罩）；回到牌桌时按当前询问重新挂上（预发动 / 排列的本地态不因切页丢失）。
  if (view === "table") {
    if (state.view) {
      syncDialog();
      syncHeroModal();
    }
  } else {
    closeModalForView();
  }
}
