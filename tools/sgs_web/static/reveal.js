/* SGS 前端 · 亮牌模板（U2-11 / spec D3 / D4）：五谷丰登 + 遗计复用。
 *
 * 内容槽画**全部亮出的牌**（含已被拿走的）：位置保持亮牌顺序；被拿走的牌置灰、牌面仍可读、
 * 角标写拿牌者座位号。左下角是**目标条**（五谷才有）：按拿牌顺序排列全部目标座位号，四态
 * ——未轮到（灰）/ 已拿（勾）/ 当前选择（高亮）/ 无效（红）。红只认「最终没拿到牌」：被
 * 无懈且未被反无懈救回 ⇒ 红；反无懈救回的目标照常拿牌 ⇒ 正常。将来"先被指定、后被取消"
 * 的目标由服务端 `cancelled` 位标红，前端只读这个位。
 *
 * **入口**（票 12 复用）：
 *   - 判据：`modal.js:isRevealStep`（五谷读 `pending.harvest`；遗计亮牌可喂 `pending.dialog
 *     = "reveal"` + `pending.revealed`）；
 *   - 渲染：本文件的 `renderReveal(pending, opts)`；数据源优先 `pending.harvest`，其次
 *     `pending.revealed`。没有 `harvest.targets` 时**不画目标条**（遗计两牌亮牌就是这条路径）。
 *   - 一击提交：`submitRevealPick(pending, slotIndex)` 送出一条 `SelectCards`（`picks` 填
 *     该牌在 `pending.slots` 里的位置）。
 *
 * 只读态的唯一判据仍住在 `modal.js:stepDialogKind`（`DIALOG_KINDS.reveal`）；本文件只在
 * `renderSlots` 判定为亮牌时画正文，不改任何选择入口。
 */
"use strict";

/** 亮牌模板的数据源：优先服务端派生的 `harvest`，其次询问自带的 `revealed`。
 *
 * 五谷的 `harvest` 带 `title / hint / revealed / targets / current`；票 12 的遗计亮牌可以只喂
 * `pending.revealed`（两张牌堆顶，没有目标条），`pending.harvest` 里的 `revealed` 优先。
 * 不做任何规则裁决：`taken_by` / `status` / `cancelled` 都是服务端算好的事实。
 */
function revealData(pending) {
  const harvest = pending && pending.harvest && typeof pending.harvest === "object"
    ? pending.harvest
    : {};
  const cards = Array.isArray(harvest.revealed)
    ? harvest.revealed
    : (Array.isArray(pending && pending.revealed) ? pending.revealed : []);
  const targets = Array.isArray(harvest.targets) ? harvest.targets : [];
  return {
    cards,
    targets,
    current: harvest.current === undefined ? null : harvest.current,
    hint: harvest.hint || null,
    title: harvest.title || null,
  };
}

/** 亮牌这一步的步骤提示（弹窗标题条里也镜像一份，折叠后仍可见）。
 *
 * 规则话术读载荷：`context.requirement`（服务端给「从亮出的牌里选一张获得」）优先，
 * 其次 `harvest.hint`（同一个常量的下发副本）。前端只补一句"怎么点 / 在等谁"的交互说明。
 * 公开处理区的牌面是可见的，所以这里**不写**「隐藏区域的槽位只有位置」。
 */
function revealHintText(pending, interactive) {
  const data = revealData(pending);
  const requirement = (pending && pending.context && pending.context.requirement)
    || data.hint
    || "从亮出的牌里选一张获得";
  if (interactive) return `${requirement}：点一张亮牌即提交。`;
  const current = data.current;
  if (current === null || current === undefined) return `${requirement}：等待其他座位拿牌…`;
  return `${requirement}：等待${current}号位拿牌…`;
}

/** 亮牌模板的正文：全部亮牌（按亮牌顺序）+ 可选的目标条。
 *
 * `opts.interactive` 缺省 = 只有**自己的**询问才可点（与服务端"谁应答谁铺控件"同一判据）；
 * AI 目标拿牌时传 `interactive: false`：牌与目标条照画，但没有 click。
 */
function renderReveal(pending, opts = {}) {
  const box = $("t-options");
  const data = revealData(pending);
  const interactive = opts.interactive !== false && isOwnPending(pending);
  const hint = $("t-target-hint");
  if (hint) hint.textContent = revealHintText(pending, interactive);

  const wrap = document.createElement("div");
  wrap.className = "reveal";
  wrap.classList.add("reveal");
  const row = document.createElement("div");
  row.className = "cards reveal-cards";
  row.classList.add("cards", "reveal-cards");
  const slots = (pending && pending.slots) || [];
  for (const entry of data.cards) {
    const takenBy = entry && entry.taken_by !== undefined && entry.taken_by !== null
      ? Number(entry.taken_by)
      : null;
    const slotIndex = slots.findIndex(
      (slot) => slot && slot.card && String(slot.card.card_id) === String(entry.card_id),
    );
    const pickable = takenBy === null && interactive && slotIndex >= 0;
    const node = cardFaceNode(entry, {
      static: !pickable,
      onClick: pickable ? () => { submitRevealPick(pending, slotIndex); } : undefined,
    });
    node.classList.add("reveal-card");
    node.setAttribute("data-taken-by", takenBy === null ? "" : String(takenBy));
    if (takenBy !== null) {
      // 被拿走的牌：**留在原位**、置灰、牌面仍可读（cardFaceNode 照旧给 title 描述），
      // 右下角标写拿牌者座位号。
      node.classList.add("taken");
      const badge = document.createElement("span");
      badge.className = "taken-by";
      badge.classList.add("taken-by");
      badge.textContent = `座${takenBy}`;
      badge.setAttribute("title", `已被 ${takenBy} 号位拿走`);
      node.appendChild(badge);
    }
    row.appendChild(node);
  }
  wrap.appendChild(row);

  if (data.targets.length) {
    // 目标条（D4）：按**拿牌顺序**（服务端 `targets` 的顺序）排列；四态 + 保留的取消位。
    const bar = document.createElement("div");
    bar.className = "reveal-targets";
    bar.classList.add("reveal-targets");
    bar.setAttribute("data-current", data.current === null ? "" : String(data.current));
    const label = document.createElement("span");
    label.className = "reveal-targets-label";
    label.classList.add("reveal-targets-label");
    label.textContent = "目标";
    bar.appendChild(label);
    for (const target of data.targets) {
      // "先被指定、后被取消"（界甘宁【奋威】，本仓暂无）与"被无懈"同一种红。
      const stateName = target.cancelled ? "invalid" : (target.status || "pending");
      const chip = document.createElement("span");
      chip.className = `reveal-target is-${stateName}`;
      chip.classList.add("reveal-target", `is-${stateName}`);
      chip.setAttribute("data-seat", String(target.seat));
      chip.setAttribute("data-state", stateName);
      chip.textContent = `座${target.seat}`;
      if (stateName === "taken") chip.textContent += "✓";
      else if (stateName === "invalid") chip.textContent += "✕";
      else if (stateName === "current") chip.textContent += "◀";
      bar.appendChild(chip);
    }
    wrap.appendChild(bar);
  }
  box.appendChild(wrap);
  const body = $("t-dialog-body");
  if (body) body.classList.add("reveal-body");
}

/** 亮牌：点一张 = **一次提交**（送出的仍是 `legal_actions` 里那条 `SelectCards` 骨架，
 *  `picks` 填该牌在 `pending.slots` 里的位置——与未知区域 / 反间盲挑同一条提交纪律）。
 *  被拿走的牌没有 click；AI 拿牌时不铺任何 click。
 */
async function submitRevealPick(pending, slotIndex) {
  if (state.playback || state.submitBusy) return;
  if (stepDialogKind(pending) !== DIALOG_KINDS.reveal) return;
  const actions = (pending && pending.legal_actions) || [];
  const at = actions.findIndex((action) => action.kind === "SelectCards");
  if (at < 0) return;
  const before = selectionSnapshot();
  state.selectedOption = at;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [slotIndex];
  state.preactivation = null;
  state.lastRejectReason = "";
  if (!state.sessionId) {
    renderPending(state.view);
    return;
  }
  await submitAction(before);
}
