/* SGS 前端 · 拼点弹窗（huo-taishici-xunyu 票 04 / 用户 2026-11-04 的 R7）。
 *
 * **全场可见的只读展示**：拼点是不使用、不打出的对抗事件，结算完就有一段"发生了什么"
 * 需要讲清楚——谁的哪张牌 vs 谁的哪张牌、每一对赢 / 没赢 / 都没赢。它不是询问面：
 * 不接选择、不挡牌桌、没有提交按钮（选牌仍走牌桌表面「点自己的手牌」，服务端照旧发
 * `SelectCards` ⇒ 用户 R2「直接手选 1 张牌即可」）。
 *
 * 三条纪律：
 *
 *  - **判据只看记录流**：入口是 `pindian` delta（`delta_type`），不认技能 id、不认座位；
 *    弹窗内容（标题 = 发动拼点的技能名 / 参与者 / 两张牌 / 结果）全部读服务端给的事实。
 *    「都没赢」读 `tied` 位，前端**不比较点数**——点数比较是规则，规则只有引擎一份。
 *  - **停留 3 秒是展示时长**（用户口径：1vN 时全部比较完停 3 秒，不是每对 3 秒；服务端
 *    不阻塞）：`window.setTimeout` 一次，关窗只由它或下一条拼点触发；不排进回放步长。
 *  - **不画半张弹窗**：载荷缺标题 / 参与者 / 牌（旧记录或半截记录）⇒ 整条跳过，不抛错。
 *
 * 入口：`session.js:stepPlayback` 每条记录调一次 :func:`applyPindianDelta`；AI 打 AI 的
 * 拼点同样走这条路（人类不是参与者时也弹，只读）。
 */
"use strict";

/** 结果标签的闭集（用户口径：每个槽位上方只写「赢 / 没赢」；平点 ⇒ 两个槽位都「没赢」，
 *  悬停补一句「平点：双方都没赢」）。`tie` 只做 `data-verdict` 的第三档，不印成文案。 */
const PINDIAN_VERDICTS = Object.freeze({ win: "赢", lose: "没赢", tie: "都没赢" });

/** 弹窗停留时长（毫秒）：**全部**比较完统一停 3 秒（R7 / 用户 2026-11-04）。 */
const PINDIAN_HOLD_MS = 3000;

/** 一条 `pindian` delta → 弹窗载荷；不是拼点记录就是 `null`（唯一判据 = `delta_type`）。
 *
 * 载荷形状与服务端 `state/deltas.py:PindianDelta` 一一对应：`cards[0]` 是发起者的拼点牌，
 * 其后按 `targets` 序一一对应；`won` / `tied` 与 `targets` 对齐。`tied` 缺失（旧记录）按
 * 空元组处理 ⇒ 那一对画「没赢」，不猜平点。
 */
/** 第 ``index`` 个**生效点数**；不是数字 ⇒ ``null``（旧记录 / 缺字段 ⇒ 不画）。 */
function _pindianRank(ranks, index) {
  const value = ranks[index];
  return typeof value === "number" ? value : null;
}

function pindianPlan(record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "pindian") return null;
  const cards = Array.isArray(record.cards) ? record.cards : [];
  const targets = Array.isArray(record.targets) ? record.targets : [];
  const won = Array.isArray(record.won) ? record.won : [];
  const tied = Array.isArray(record.tied) ? record.tied : [];
  // ``ranks`` = **生效点数**（与 ``cards`` 对齐；tao-caocao 票 03）。它只在"这一次拼点
  // 被技能调过点数"之后才出现在新记录里；缺省空元组 ⇒ 不画点数（旧记录不猜）。
  const ranks = Array.isArray(record.ranks) ? record.ranks : [];
  if (!record.title || !targets.length) return null;
  if (cards.length < targets.length + 1) return null;
  return {
    title: String(record.title),
    initiator: record.initiator,
    initiatorCard: cards[0],
    initiatorRank: _pindianRank(ranks, 0),
    // 每一**对**的两个结果：发起者的「赢 / 没赢」= `won`；被拼点者的 = 反过来，
    // **平点两人都没赢**（`tied`）——所以被拼点者的标签不能简单取反，必须读 `tied`。
    pairs: targets.map((seat, index) => {
      const isTie = Boolean(tied[index]);
      const isWin = !isTie && Boolean(won[index]);
      const targetWin = !isTie && !isWin;
      return {
        seat,
        card: cards[index + 1],
        rank: _pindianRank(ranks, index + 1),
        state: isTie ? "tie" : (isWin ? "win" : "lose"),
        initiatorVerdict: isWin ? PINDIAN_VERDICTS.win : PINDIAN_VERDICTS.lose,
        targetVerdict: targetWin ? PINDIAN_VERDICTS.win : PINDIAN_VERDICTS.lose,
        title: isTie ? "平点：双方都没赢" : "",
      };
    }),
  };
}

/** 座位号 + 武将名（"0号位 太史慈"）：名字与座位卡同源（`heroDisplayName` 读目录）。
 *
 * 目录没加载（裸询问 / 旧页面）时只写座位号，不把 hero id 当名字印出来。
 */
function pindianSeatLabel(seat) {
  const heroes = (tableViewForRender() || state.view || {}).heroes || {};
  const name = heroDisplayName(heroes[seat]);
  const suffix = name && name !== String(heroes[seat]) ? ` ${name}` : "";
  return `${seat}号位${suffix}`;
}

/** 一个**玩家块**：上方结果（赢 / 没赢）、中间一张**正常卡面**、下方"座位号 + 武将名"。
 *
 * 卡面走 `card-view.js:cardFaceNode`（与手牌 / 舞台 / 亮牌同一套渲染）：花色点数、虚拟 /
 * 转化角标、**牌上的标签**（`card.tags`，服务端下发）都由它画，这里不另做牌面。
 */
function pindianPlayer({ seat, card, role, verdict, state: verdictState, title = "", point = null }) {
  const player = document.createElement("div");
  player.className = `pindian-player ${role}`;
  player.setAttribute("data-seat", String(seat));
  player.setAttribute("data-role", role);
  player.setAttribute("data-verdict", verdictState);
  const top = document.createElement("span");
  top.className = `pindian-verdict ${verdictState}`;
  top.textContent = verdict;
  if (title) top.setAttribute("title", title);
  player.appendChild(top);

  const slot = document.createElement("div");
  slot.className = `pindian-slot ${role}`;
  slot.setAttribute("data-seat", String(seat));
  slot.setAttribute("data-role", role);
  if (card) slot.appendChild(cardFaceNode(card, { static: true }));
  player.appendChild(slot);

  // **生效点数**（服务端给的 `pindian` delta 的 `ranks`）：只有新记录才画。牌面上的点数
  // 是牌自己的事实，被技能调过之后两者会不一样——不画出来就成了"5 点输给 4 点"的错觉。
  // 前端不比较点数、也不自己加：数字原样来自服务端。
  if (typeof point === "number") {
    const rankNode = document.createElement("span");
    rankNode.className = "pindian-point";
    rankNode.setAttribute("data-seat", String(seat));
    rankNode.textContent = `点数 ${point}`;
    player.appendChild(rankNode);
  }

  const who = document.createElement("span");
  who.className = "pindian-who";
  who.textContent = pindianSeatLabel(seat);
  player.appendChild(who);
  return player;
}

/** 把载荷画进 `#t-pindian`：左 1 个固定槽 = 发起者、右 1..N 行 = 各被拼点者。
 *
 * 1v1 就是两块并排（各自上方赢 / 没赢、下方座位号 + 名字）；1vN 时发起者的牌只画一次。
 */
function renderPindian(plan) {
  const title = $("t-pindian-title");
  if (title) title.textContent = plan.title;
  const body = $("t-pindian-body");
  if (!body) return;
  body.innerHTML = "";
  // 多对时发起者的结果不止一个：**全赢**才写「赢」，否则写「没赢」（不猜单对）。
  const wins = plan.pairs.filter((pair) => pair.state === "win").length;
  const initiatorState = plan.pairs.length && wins === plan.pairs.length ? "win" : "lose";
  body.appendChild(pindianPlayer({
    seat: plan.initiator,
    card: plan.initiatorCard,
    role: "initiator",
    verdict: initiatorState === "win" ? PINDIAN_VERDICTS.win : PINDIAN_VERDICTS.lose,
    state: initiatorState,
    title: wins && wins < plan.pairs.length ? "与多名角色拼点：并非每一对都赢" : "",
    point: plan.initiatorRank,
  }));
  const versus = document.createElement("span");
  versus.className = "pindian-vs";
  versus.textContent = "vs";
  body.appendChild(versus);
  const rows = document.createElement("div");
  rows.className = "pindian-targets";
  plan.pairs.forEach((pair) => {
    rows.appendChild(pindianPlayer({
      seat: pair.seat,
      card: pair.card,
      role: "target",
      verdict: pair.targetVerdict,
      state: pair.state === "tie" ? "tie" : (pair.targetVerdict === PINDIAN_VERDICTS.win ? "win" : "lose"),
      title: pair.title,
      point: pair.rank,
    }));
  });
  body.appendChild(rows);
}

let pindianTimer = null;

/** 关窗（唯一的关闭路径：停留到期，或下一条拼点重开时先清计时器）。 */
function hidePindian() {
  if (pindianTimer !== null) {
    window.clearTimeout(pindianTimer);
    pindianTimer = null;
  }
  const box = $("t-pindian");
  if (box) box.hidden = true;
}

/** 开窗：整份重画、清掉上一条的计时器、重新排 3 秒。只读，不改任何选择态。 */
function showPindian(plan) {
  const box = $("t-pindian");
  if (!box || !plan) return;
  renderPindian(plan);
  box.hidden = false;
  if (pindianTimer !== null) window.clearTimeout(pindianTimer);
  pindianTimer = window.setTimeout(hidePindian, PINDIAN_HOLD_MS);
}

/** 回放入口：一条记录 → 是拼点就弹；返回 `true` 便于烟测断言"这条确实进了弹窗"。 */
function applyPindianDelta(record) {
  const plan = pindianPlan(record);
  if (!plan) return false;
  showPindian(plan);
  return true;
}
