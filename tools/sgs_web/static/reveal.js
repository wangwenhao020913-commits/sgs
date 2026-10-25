/* SGS 前端 · 亮牌模板（U2-11 / spec D3 / D4）：五谷丰登 + 遗计复用。
 *
 * 内容槽画**全部亮出的牌**（含已被拿走的）：位置保持亮牌顺序；被拿走的牌置灰、牌面仍可读、
 * 角标写拿牌者座位号。左下角是**目标条**（五谷才有）：按拿牌顺序排列全部目标座位号，四态
 * ——未轮到（灰）/ 已拿（勾）/ 当前选择（高亮）/ 无效（红）。红只认「最终没拿到牌」：被
 * 无懈且未被反无懈救回 ⇒ 红；反无懈救回的目标照常拿牌 ⇒ 正常。将来"先被指定、后被取消"
 * 的目标由服务端 `cancelled` 位标红，前端只读这个位。
 *
 * **两种读法**（同一渲染器，判据全读载荷）：
 *   - **五谷式一击提交**：`pending.harvest` 带目标条的状态 ⇒ 点一张亮牌 = 一次提交；
 *   - **遗计式多选提交**（U2-12）**：服务端只喂 `pending.dialog = "reveal"` + `pending.revealed`
 *     （没有 `harvest`）⇒ 牌面是**可切换的选中态**（`.sel`），点 1~2 张后由提示条右端
 *     「提交」送出唯一一次提交；弹窗内没有第二个提交点。
 *
 * **入口**（票 12 复用）：
 *   - 判据：`modal.js:isRevealStep`（五谷读 `pending.harvest`；遗计读 `pending.dialog`）；
 *   - 渲染：本文件的 `renderReveal(pending, opts)`；数据源优先 `pending.harvest`，其次
 *     `pending.revealed`。没有 `harvest.targets` 时**不画目标条**（遗计两牌亮牌就是这条路径）。
 *   - 五谷一击提交：`submitRevealPick(pending, slotIndex)` 送出一条 `SelectCards`（`picks` 填
 *     该牌在 `pending.slots` 里的位置）；遗计多选：`toggleRevealPick` 只改本地选择，
 *     提交走 `pending.js:refreshSubmit` / `answer.js:submitAction` 的既有唯一提交点。
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
  // A1：洛神连续判定的当前判定牌由服务端派生字段给出，优先于其它亮牌来源。
  const judged = pending && pending.judgment_reveal
    ? pending.judgment_reveal.card
    : null;
  const cards = judged
    ? [judged]
    : (Array.isArray(harvest.revealed)
      ? harvest.revealed
      : (Array.isArray(pending && pending.revealed) ? pending.revealed : []));
  const targets = Array.isArray(harvest.targets) ? harvest.targets : [];
  return {
    cards,
    targets,
    current: harvest.current === undefined ? null : harvest.current,
    hint: harvest.hint || null,
    title: harvest.title || null,
  };
}

/** 遗计式**多选**亮牌（U2-12）：服务端显式标了 `dialog: "reveal"`，且这一步不是五谷的
 *  `harvest` 状态。判据吃**载荷**：前端不写技能 id，也不从"有没有目标条"猜规则。
 *
 * 这一步的出口是提示条右端的「提交」（选 1~2 张后），不是点一张就送——与五谷的
 * 一击提交刻意分开（同一个渲染器，两种读法）。
 */
function isPickRevealStep(pending) {
  if (!pending || pending.dialog !== DIALOG_KINDS.reveal) return false;
  if (pending.harvest && pending.harvest.active !== false) return false;
  // 只有**允许选多张**的亮牌步才走「切换选中 + 右端提交」（遗计 1~2 张）。
  // 火攻的亮牌步（M5-08a）是 `total=[1,1]`：点一张即提交，与五谷的同一种读法；
  // 这个判据只读服务端下发的 `spec.total`，不是第二份规则。
  return selectionCap(pending) > 1;
}

/** 亮牌这一步的步骤提示（弹窗标题条里也镜像一份，折叠后仍可见）。
 *
 * 规则话术读载荷：`context.requirement`（服务端给「从亮出的牌里选一张获得」）优先，
 * 其次 `harvest.hint`（同一个常量的下发副本）。前端只补一句"怎么点 / 在等谁"的交互说明。
 * 两处都缺（合成载荷 / 旧载荷）时只说**中性**的"从亮出的牌里选择"——不抄服务端的
 * `HARVEST_REQUIREMENT`，否则"前端不抄第二份文案"的声明就成了假话。
 * 公开处理区的牌面是可见的，所以这里**不写**「隐藏区域的槽位只有位置」。
 */
function revealHintText(pending, interactive) {
  const data = revealData(pending);
  const requirement = (pending && pending.context && pending.context.requirement)
    || data.hint
    || "从亮出的牌里选择";
  // A1：洛神连续判定的亮牌确认——卡片只读，出口在右端「提交（继续）/ 放弃（停止）」。
  if (isJudgmentRevealOption(pending)) {
    return `${requirement}：点右端「提交」继续，或「放弃」停止。`;
  }
  if (isPickRevealStep(pending)) {
    if (!interactive) return `${requirement}：等待其他座位响应…`;
    const total = (pending.spec && pending.spec.total) || [1, 1];
    const least = Math.max(1, Number(total[0]) || 1);
    const most = Math.max(least, Number(total[1]) || least);
    const howMany = least === most ? `选 ${least} 张` : `选 ${least}~${most} 张`;
    return `${requirement}：${howMany}后点右端「提交」（已选 ${state.picks.length} 张）。`;
  }
  if (interactive) return `${requirement}：点一张亮牌即提交。`;
  const current = data.current;
  if (current === null || current === undefined) return `${requirement}：等待其他座位拿牌…`;
  return `${requirement}：等待${current}号位拿牌…`;
}

/** 五谷**持久展示**（无懈 / 反无懈的 RESPOND 窗口）的步骤提示。
 *
 * 只读 `harvest.hint`（公开处理区的规则话术），**不读** `context.requirement`：那一步的
 * 上下文属于无懈窗口（"需要使用【无懈可击】"），不是亮牌窗要说的"在等谁拿牌"。
 * 缺 `hint` 时用中性句，不抄服务端的 `HARVEST_REQUIREMENT`。
 */
function harvestWaitHint(pending) {
  const data = revealData(pending);
  const requirement = data.hint || "从亮出的牌里选择";
  const current = data.current;
  return current === null || current === undefined
    ? `${requirement}：等待其他座位拿牌…`
    : `${requirement}：等待${current}号位拿牌…`;
}

/** 亮牌模板的正文：全部亮牌（按亮牌顺序）+ 可选的目标条。
 *
 * `opts.interactive` 缺省 = 只有**自己的**询问才可点（与服务端"谁应答谁铺控件"同一判据）；
 * AI 目标拿牌时传 `interactive: false`：牌与目标条照画，但没有 click。
 *
 * `opts.container` 缺省 = 提示区的 `#t-options`（正常弹窗内容）。五谷的**持久展示**
 * （无懈 / 反无懈的 RESPOND 窗口）把它改成 `#t-dialog-reveal`，并同时传
 * `promptHint: false` / `bodyClass: false`：不抢提示条的步骤提示、不给隐藏的正文槽加
 * 内边距（那一步的 `#t-options` 仍留在提示区，牌桌照常可操作）。
 *
 * 遗计式多选（:func:`isPickRevealStep`）的牌面 click = **切换选中**（`.sel`），不提交；
 * 提交点只有提示条右端的「提交」。
 */
function renderReveal(pending, opts = {}) {
  const box = opts.container || $("t-options");
  const data = revealData(pending);
  const multi = isPickRevealStep(pending);
  const interactive = opts.interactive !== false && isOwnPending(pending);
  if (opts.promptHint !== false) {
    const hint = $("t-target-hint");
    if (hint) hint.textContent = revealHintText(pending, interactive);
  }

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
      onClick: pickable
        ? () => {
          if (multi) toggleRevealPick(pending, slotIndex);
          else submitRevealPick(pending, slotIndex);
        }
        : undefined,
    });
    node.classList.add("reveal-card");
    node.setAttribute("data-taken-by", takenBy === null ? "" : String(takenBy));
    if (slotIndex >= 0) node.setAttribute("data-slot-index", String(slotIndex));
    if (multi && pickable && state.picks.includes(slotIndex)) {
      // 多选读法：选中态与选择面同一种 `.sel`（CSS 已有一份，不另造样式）。
      node.classList.add("sel");
      node.setAttribute("data-picked", "true");
    }
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
  if (opts.bodyClass !== false) {
    const body = $("t-dialog-body");
    if (body) body.classList.add("reveal-body");
  }
}

/** 遗计式多选：点一张亮牌 = **切换**它在本次选择里的位置（`picks`），**不提交**。
 *
 * 上限读服务端 `spec.total`（与选择面同一条"当前状态的纯函数"纪律），只做越界拒绝；
 * 选好后由提示条右端的「提交」走 :func:`refreshSubmit` / :func:`submitAction` 那条既有提交点。
 * 五谷的 `harvest` 亮牌不走这里（它是一击提交）。
 */
function toggleRevealPick(pending, slotIndex) {
  if (state.playback || state.submitBusy) return;
  if (!isPickRevealStep(pending)) return;
  const at = state.picks.indexOf(slotIndex);
  if (at >= 0) {
    state.picks.splice(at, 1);
  } else {
    if (state.picks.length >= selectionCap(pending)) return;
    state.picks.push(slotIndex);
    // 引擎的 `SelectCards` 候选按面内位置**升序**枚举；提交前自检逐字段比对，故选中序
    // 与点击顺序无关、必须保持升序（先取消再重选也不会造出 `[1,0]` 这种查不到的答案）。
    state.picks.sort((a, b) => a - b);
  }
  state.lastRejectReason = "";
  renderPending(state.view);
}

/** 亮牌：点一张 = **一次提交**（送出的仍是 `legal_actions` 里那条 `SelectCards` 骨架，
 *  `picks` 填该牌在 `pending.slots` 里的位置——与未知区域 / 反间盲挑同一条提交纪律）。
 *  被拿走的牌没有 click；AI 拿牌时不铺任何 click。遗计式多选不调用本函数（见上）。
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
