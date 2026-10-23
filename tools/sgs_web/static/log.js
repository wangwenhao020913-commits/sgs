/* SGS 前端 · 事件流：简易（服务端叙事）/ 完整重点 / 完整全部三档的渲染。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 *
 * **简易档的纪律（U1-05）**：条目由服务端 `narrative.py` 生成并已按查看者过滤；前端
 * 只按 `segments[].cls` 上色，**不读快照 / 记录去补事实、也不判断隐藏信息**。完整两档
 * 仍是既有的记录流渲染（`describeRecord`），行为不变。
 */
"use strict";

/* ------------------------------------------------------------------ 日志 */

/** 事件流的详略；`simple` = 默认的服务端叙事，`focus` = 隐藏对手之间的响应往来。 */
const LOG_MODES = ["simple", "focus", "all"];

/** 移动原因的中文（M5-08b）：完整档也不把 `REFORGE` 英文漏给玩家；其余原因保留报文值
 *  （完整档本来就是记录流原文，不在这里另翻一套规则话术）。 */
const MOVE_REASON_LABELS = Object.freeze({ REFORGE: "重铸" });

function logMode() {
  const node = $("t-log-mode");
  const mode = node && node.value ? node.value : "simple";
  return LOG_MODES.includes(mode) ? mode : "simple";
}

/** 这条记录在**重点**模式下要不要显示。回放页用同一判据（显式传模式与查看者）。
 *
 * `mode` / `humanSeat` 是给回放页的显式参数：回放的查看者来自录像侧车 meta，而不是
 * 当前牌桌的 `state.view`。`simple` 档不消费记录流，故这里直接放行。
 */
function keepRecord(record, querySeat, mode = logMode(), humanSeat = undefined) {
  if (mode !== "focus") return true;
  // `action` 记录不带座位，故用"上一条询问的 actor"当它的座位（记录流里询问与应答严格相邻）。
  const seat = record.kind === "query" ? record.query.actor : querySeat;
  const me = humanSeat === undefined
    ? (state.view ? state.view.human_seat : null)
    : humanSeat;
  if (record.kind === "query" || record.kind === "action") return seat === me;
  if (record.kind === "delta" && record.delta_type === "deck_shuffle") return false;
  return true;
}

/** 本地回放已经走到哪条记录：叙事条目只播到这一刻（切档不会提前泄露下一批）。 */
function playbackSeqLimit() {
  const playback = state.playback;
  if (!playback) return Infinity;
  const index = playback.index - 1;
  if (index < 0) return -1;
  const record = playback.records[index];
  return record && typeof record.seq === "number" ? record.seq : -1;
}

/** 一条叙事条目 → `<li>`：分隔线只写文本；行动行把服务端的颜色类原样搬到 span 上。 */
function appendNarrativeEntry(entry, log = $("t-log")) {
  if (!log || !entry) return null;
  const item = document.createElement("li");
  if (entry.kind === "divider") {
    item.className = "nar-divider";
    item.textContent = entry.text || "";
  } else {
    item.className = "nar-line";
    for (const segment of entry.segments || []) {
      const span = document.createElement("span");
      span.className = `nar-${segment.cls || "act"}`;
      span.textContent = segment.text || "";
      item.appendChild(span);
    }
  }
  log.appendChild(item);
  return item;
}

/** 把已收到的叙事条目追加到当前 `<li>` 流里，直到记录 `seq` 达到 `limit`。 */
function appendNarrativeThrough(limit) {
  if (logMode() !== "simple") return;
  while (state.narrativeShown < state.narrative.length) {
    const entry = state.narrative[state.narrativeShown];
    if (!entry || !(entry.seq <= limit)) break;
    appendNarrativeEntry(entry);
    state.narrativeShown += 1;
  }
}

/** 一条记录 → `<li>`（牌桌与回放共用；`banner` 只用于回放当前位置）。
 *
 * 简易档不消费记录流，故 `appendLog` 只为完整档服务。`log` / `mode` / `humanSeat`
 * 让回放页显式传入自己的容器与查看者，不再复制一套 DOM 拼接。
 */
function appendLog(
  record,
  querySeat,
  { log = $("t-log"), mode = logMode(), humanSeat = undefined, banner = false } = {},
) {
  if (!keepRecord(record, querySeat, mode, humanSeat)) return false;
  const item = document.createElement("li");
  if (banner) item.className = "banner";
  item.innerHTML = describeRecord(record);
  log.appendChild(item);
  return true;
}

/** 滚到底：先写 `scrollTop`（可被烟测直接断言），再让最后一条进入视口。 */
function scrollLogToBottom(log = $("t-log")) {
  if (!log) return;
  log.scrollTop = log.scrollHeight;
  const last = log.lastChild;
  if (last) last.scrollIntoView({ block: "nearest" });
}

/** 重画整条事件流（切换详略时需要把已渲染的清掉重来）。
 *
 * 简易档只画服务端叙事（`state.narrative`）；完整两档沿用记录流渲染。**切回简易档
 * 不会重新请求**：叙事条目随视图增量一起收下，已经够整屏重画。
 */
function renderLog() {
  const log = $("t-log");
  log.innerHTML = "";
  state.narrativeShown = 0;
  if (!state.view) return;
  if (logMode() === "simple") {
    appendNarrativeThrough(playbackSeqLimit());
    scrollLogToBottom();
    return;
  }
  let querySeat = null;
  const records = state.logRecords.length ? state.logRecords : (state.view.records || []);
  for (const record of records) {
    if (record.kind === "query") querySeat = record.query.actor;
    appendLog(record, querySeat);
  }
  scrollLogToBottom();
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
      const reason = MOVE_REASON_LABELS[record.reason] || record.reason;
      // M5-09c：隐藏区（牌堆 / 手牌）之间的移动在服务端已脱敏——`cards` 为空、`card_count`
      // 给张数（公开区 / 自己经手的移动仍带牌面）。完整档只按下发值渲染，不做可见性判断。
      const moved = (record.cards || []).length
        ? cardsLabel(record.cards)
        : (record.card_count ? ` · ${record.card_count} 张牌` : "");
      return `<span class="k">#${record.seq}</span> 牌移动 ${zoneLabel(record.from_zone)} → ${zoneLabel(record.to_zone)}（${reason}）${moved}`;
    }
    if (record.delta_type === "hp_change") return `<span class="k">#${record.seq}</span> 体力 座位 ${record.seat} ${record.before} → ${record.after}`;
    if (record.delta_type === "chained_change") {
      // M5-08d：横置 / 重置的状态变化在完整档也写人话（U1-05 的简易档由服务端叙事写）。
      return `<span class="k">#${record.seq}</span> 座位 ${record.seat} ${record.chained ? "横置" : "重置"}`;
    }
    if (record.delta_type === "wine_change") {
      return `<span class="k">#${record.seq}</span> 座位 ${record.seat} ${record.active ? "进入【酒】状态" : "【酒】加伤消失"}`;
    }
    if (record.delta_type === "card_reveal") {
      return `<span class="k">#${record.seq}</span> 亮牌 座位 ${record.seat}${record.card ? cardsLabel([record.card]) : ""}`;
    }
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
