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

/** 结果标签的闭集（用户口径：赢 / 没赢；平点双方都没赢）。 */
const PINDIAN_VERDICTS = Object.freeze({ win: "赢", lose: "没赢", tie: "都没赢" });

/** 弹窗停留时长（毫秒）：**全部**比较完统一停 3 秒（R7 / 用户 2026-11-04）。 */
const PINDIAN_HOLD_MS = 3000;

/** 一条 `pindian` delta → 弹窗载荷；不是拼点记录就是 `null`（唯一判据 = `delta_type`）。
 *
 * 载荷形状与服务端 `state/deltas.py:PindianDelta` 一一对应：`cards[0]` 是发起者的拼点牌，
 * 其后按 `targets` 序一一对应；`won` / `tied` 与 `targets` 对齐。`tied` 缺失（旧记录）按
 * 空元组处理 ⇒ 那一对画「没赢」，不猜平点。
 */
function pindianPlan(record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "pindian") return null;
  const cards = Array.isArray(record.cards) ? record.cards : [];
  const targets = Array.isArray(record.targets) ? record.targets : [];
  const won = Array.isArray(record.won) ? record.won : [];
  const tied = Array.isArray(record.tied) ? record.tied : [];
  if (!record.title || !targets.length) return null;
  if (cards.length < targets.length + 1) return null;
  return {
    title: String(record.title),
    initiator: record.initiator,
    initiatorCard: cards[0],
    pairs: targets.map((seat, index) => {
      const isTie = Boolean(tied[index]);
      const isWin = !isTie && Boolean(won[index]);
      return {
        seat,
        card: cards[index + 1],
        state: isTie ? "tie" : (isWin ? "win" : "lose"),
        verdict: isTie
          ? PINDIAN_VERDICTS.tie
          : (isWin ? PINDIAN_VERDICTS.win : PINDIAN_VERDICTS.lose),
      };
    }),
  };
}

/** 一个槽位：座位号 + 一张**正常卡面**（`card-view.js:cardFaceNode`，静态不可点）。 */
function pindianSlot(seat, card, role) {
  const slot = document.createElement("div");
  slot.className = `pindian-slot ${role}`;
  slot.setAttribute("data-seat", String(seat));
  slot.setAttribute("data-role", role);
  const label = document.createElement("span");
  label.className = "pindian-seat";
  label.textContent = `${seat}号位`;
  slot.appendChild(label);
  if (card) slot.appendChild(cardFaceNode(card, { static: true }));
  return slot;
}

/** 一对：被拼点者的扣置牌 + 结果标签（发起者的牌由 :func:`renderPindian` 只画一次）。 */
function pindianPair(pair) {
  const row = document.createElement("div");
  row.className = "pindian-pair";
  row.setAttribute("data-seat", String(pair.seat));
  row.setAttribute("data-verdict", pair.state);
  row.appendChild(pindianSlot(pair.seat, pair.card, "target"));
  const verdict = document.createElement("span");
  verdict.className = `pindian-verdict ${pair.state}`;
  verdict.textContent = pair.verdict;
  row.appendChild(verdict);
  return row;
}

/** 把载荷画进 `#t-pindian`：左 1 个固定槽 = 发起者、右 1..N 行 = 各被拼点者。 */
function renderPindian(plan) {
  const title = $("t-pindian-title");
  if (title) title.textContent = plan.title;
  const body = $("t-pindian-body");
  if (!body) return;
  body.innerHTML = "";
  body.appendChild(pindianSlot(plan.initiator, plan.initiatorCard, "initiator"));
  const versus = document.createElement("span");
  versus.className = "pindian-vs";
  versus.textContent = "vs";
  body.appendChild(versus);
  const rows = document.createElement("div");
  rows.className = "pindian-targets";
  plan.pairs.forEach((pair) => rows.appendChild(pindianPair(pair)));
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
