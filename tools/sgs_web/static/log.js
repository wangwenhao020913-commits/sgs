/* SGS 前端 · 事件流：重点 / 全部两档的过滤与渲染。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 日志 */

/** 事件流的详略；`focus` = 隐藏对手之间的响应往来（一局 268 次 RESPOND 会把阶段埋掉）。 */
const LOG_MODES = ["focus", "all"];

function logMode() {
  const node = $("t-log-mode");
  const mode = node && node.value ? node.value : "focus";
  return LOG_MODES.includes(mode) ? mode : "focus";
}

/** 这条记录在**重点**模式下要不要显示。回放页不走这个函数（那边是上帝视角、逐条看）。 */
function keepRecord(record, querySeat) {
  if (logMode() === "all") return true;
  // `action` 记录不带座位，故用"上一条询问的 actor"当它的座位（记录流里询问与应答严格相邻）。
  const seat = record.kind === "query" ? record.query.actor : querySeat;
  const me = state.view ? state.view.human_seat : null;
  if (record.kind === "query" || record.kind === "action") return seat === me;
  if (record.kind === "delta" && record.delta_type === "deck_shuffle") return false;
  return true;
}

function appendLog(record, querySeat) {
  if (!keepRecord(record, querySeat)) return;
  const item = document.createElement("li");
  item.innerHTML = describeRecord(record);
  $("t-log").appendChild(item);
}

/** 重画整条事件流（切换详略时需要把已渲染的清掉重来）。 */
function renderLog() {
  const log = $("t-log");
  log.innerHTML = "";
  if (!state.view) return;
  let querySeat = null;
  for (const record of state.view.records || []) {
    if (record.kind === "query") querySeat = record.query.actor;
    appendLog(record, querySeat);
  }
}

function describeRecord(record) {
  if (record.kind === "header") return `<span class="k">开局</span> 种子 ${record.seed} · ${record.rule_config.player_count} 人`;
  if (record.kind === "timing") {
    const who = record.actor !== null && record.actor !== undefined ? ` · 座位 ${record.actor}` : "";
    return `<span class="k">#${record.seq}</span> ${timingLabel(record.timing)}${who}`;
  }
  if (record.kind === "query") return `<span class="k">#${record.seq}</span> 询问 ${record.query.kind} → 座位 ${record.query.actor}`;
  if (record.kind === "action") {
    return `<span class="k">#${record.seq}</span> 应答 ${describeAction(record.action)} ${record.accepted ? "（接受）" : "（被拒）"}`;
  }
  if (record.kind === "frame") return `<span class="k">#${record.seq}</span> 结算帧 ${record.op} ${record.frame_kind}（深度 ${record.depth}）`;
  if (record.kind === "game_end") return `<span class="k">#${record.seq}</span> 终局 ${record.result} · 获胜座位 [${(record.winners || []).join("、")}] · ${record.reason}`;
  if (record.kind === "delta") {
    if (record.delta_type === "card_move") {
      return `<span class="k">#${record.seq}</span> 牌移动 ${zoneLabel(record.from_zone)} → ${zoneLabel(record.to_zone)}（${record.reason}）${cardsLabel(record.cards)}`;
    }
    if (record.delta_type === "hp_change") return `<span class="k">#${record.seq}</span> 体力 座位 ${record.seat} ${record.before} → ${record.after}`;
    if (record.delta_type === "identity_deal") return `<span class="k">#${record.seq}</span> 身份 座位 ${record.seat}（只有自己 / 主公 / 阵亡者公开）`;
    if (record.delta_type === "identity_reveal") return `<span class="k">#${record.seq}</span> 亮明身份 座位 ${record.seat}`;
    return `<span class="k">#${record.seq}</span> 洗牌`;
  }
  return `<span class="k">#${record.seq || ""}</span> ${record.kind}`;
}

/** 一条应答的人话。牌桌下发的 `action` 可能只有 `kind`（对手的应答被投影裁掉了细节），
 *  故先判"展开所需字段在不在"，再交给 :func:`actionLabel`——不为裁过的载荷猜内容。
 *
 *  预发动确认在记录流里的形态就是复合 `ActivateSkill`（`picks` / `choices` 都在）；
 *  回放页的事件流把它摊开，用户才看得见"确认发动"选了什么。
 */
function describeAction(action) {
  if (!action) return "？";
  if ((action.kind === "UseCard" || action.kind === "PlayCard") && !action.card) return action.kind;
  if (action.kind === "ArrangeCards" && !action.order) return action.kind;
  if (action.kind === "SelectOption" && typeof action.choice !== "string") return action.kind;
  let text = actionLabel(action);
  if (action.kind === "ActivateSkill" && action.choices && action.choices.length) {
    text += ` → ${action.choices.join("、")}`;
  }
  return text;
}

/** 时机枚举名 → 中文。只做**通用裁剪**（前缀 + 阶段专名），不逐条手抄 83 个名字。 */
const TIMING_PREFIXES = [
  ["PHASE_PREPARE", "准备阶段"],
  ["PHASE_JUDGE", "判定阶段"],
  ["PHASE_DRAW", "摸牌阶段"],
  ["PHASE_PLAY", "出牌阶段"],
  ["PHASE_DISCARD", "弃牌阶段"],
  ["PHASE_FINISH", "结束阶段"],
  // 判定帧的三个时点：逐字译出来，"这次判定属于哪个座位"才读得清
  // （事件流的人话里带 `· 座位 N`，而 N 就是被判定者）。
  ["JUDGE_CARD_BEFORE_EFFECT", "判定牌生效前"],
  ["JUDGE_CARD_AFTER_EFFECT", "判定牌生效后"],
  ["JUDGE_ON", "判定开始"],
  ["JUDGE_", "判定"],
  ["TURN_", "回合"],
  ["GAME_", "开局"],
];

function timingLabel(timing) {
  if (!timing) return "时机";
  for (const [prefix, label] of TIMING_PREFIXES) {
    if (timing === prefix || timing.startsWith(prefix)) {
      const rest = timing.slice(prefix.length).replace(/^_/, "");
      const suffix = { "": "", START: "开始", END: "结束", IDLE: "空闲", BEFORE: "前", AFTER: "后" }[rest];
      return suffix === undefined ? `${label} ${rest}` : `${label}${suffix}`;
    }
  }
  return timing;
}

function zoneLabel(ref) {
  if (!ref) return "（初次）";
  const names = { deck: "牌堆", hand: "手牌", discard: "弃牌堆", processing: "处理区", equip: "装备区", judge: "判定区" };
  const where = ref.seat === null || ref.seat === undefined ? "" : `座位${ref.seat} `;
  return `${where}${names[ref.zone] || ref.zone}`;
}

function cardsLabel(cards) {
  if (!cards || !cards.length) return "";
  return ` · ${cards.map((card) => card.name).join("、")}`;
}
