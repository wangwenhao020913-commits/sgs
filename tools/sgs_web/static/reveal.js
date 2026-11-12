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

/** 视图级五谷状态（table-ux-overhaul 票 18 / 服务端 D 方案）：`view.harvest`。
 *
 * 服务端现在把**同一份**派生状态（`projection.session_harvest_state`）挂在**每一次视图
 * 下发**上，而不是只挂在某一次询问上。为什么需要第二个来源：五谷的整段结算里只有一部分
 * 目标会真的变成一条 pending（AI 目标的取牌在服务端 `Session.drive()` 内部同步答完），
 * 故"谁拿走了哪张"不能只等"下一条带 harvest 的询问"。
 *
 * 链键与询问载荷不一致时不认（换了一张五谷 / 换了一局）：键是链身份的唯一真源。
 * 没有 `view.harvest`（合成载荷 / 旧载荷）⇒ `null`，读取方退回 `pending.harvest`。
 */
function viewHarvestState(pending) {
  const harvest = state.view && state.view.harvest;
  if (!harvest || typeof harvest !== "object") return null;
  const own = pending && pending.harvest;
  if (own && own.key !== undefined && own.key !== null && harvest.key !== undefined
    && harvest.key !== null && String(own.key) !== String(harvest.key)) {
    return null;
  }
  return harvest;
}

/** 无询问期间的**视图级**五谷状态（table-ux-overhaul 票 19）。
 *
 * 票 19 让接入层在**每名目标取牌后**让出一次驱动：于是多出一种空窗——**没有待应答的
 * 询问**、但五谷结算帧还活着、亮牌还在处理区。这时亮牌窗不能关（关了就要等整段结算
 * 结束才看得见"谁拿走了哪张"，正是票 18 的残余缺口）。判据只读服务端派生的
 * `view.harvest`，与 `pending.harvest` **同一份**：
 *
 *   - 没有亮牌 / `active === false` ⇒ `null`（没有可展示的结算，别开窗）；
 *   - **刻意不要求 `current`**：最后一名目标取走牌之后没有当前目标，而那正是"全部拿完"
 *     要显示的那一帧——`isHarvestActive`（询问面判据）仍保留"要有当前目标"的旧口径，
 *     两者不是一回事。
 */
function idleHarvestState() {
  const harvest = state.view && state.view.harvest;
  if (!harvest || typeof harvest !== "object" || harvest.active === false) return null;
  if (!Array.isArray(harvest.revealed) || !harvest.revealed.length) return null;
  return harvest;
}

/** 亮牌模板的数据源：优先服务端派生的 **视图级** `harvest`（票 18），其次询问载荷的
 *  `harvest`，再次询问自带的 `revealed`。
 *
 * 五谷的 `harvest` 带 `title / hint / revealed / targets / current`；票 12 的遗计亮牌可以只喂
 * `pending.revealed`（两张牌堆顶，没有目标条），`pending.harvest` 里的 `revealed` 优先。
 * 不做任何规则裁决：`taken_by` / `status` / `cancelled` 都是服务端算好的事实。
 */
function revealData(pending) {
  const own = pending && pending.harvest && typeof pending.harvest === "object"
    ? pending.harvest
    : {};
  // 票 18：视图级那一份与询问载荷**同一份派生**，但更新一些（每个视图下发都带）——
  // "视图级优先、询问载荷兜底"只按链键收口，不是第二份判据。
  const harvest = viewHarvestState(pending) || own;
  // A1：洛神连续判定的**累积**判定牌由服务端派生字段给出，优先于其它亮牌来源。
  const judged = pending && pending.judgment_reveal
    && Array.isArray(pending.judgment_reveal.cards)
    ? pending.judgment_reveal.cards
    : null;
  const cards = judged && judged.length
    ? judged
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

/** 五谷状态的**内容指纹**（table-ux-overhaul 票 18）：链键 + 当前目标 + 每张牌的获得者 +
 *  目标条四态。用途只有一个——判断"这一拍要不要重画"。
 *
 *  空转轮询每 2 秒一拍，重建亮牌节点会丢掉悬停 / 焦点；同一份状态 ⇒ 同一个指纹 ⇒ 不重画
 *  （票面「再问一次（无人新拿）不重复变」）。纯函数：只读服务端派生字段。
 */
function harvestFingerprint(harvest) {
  if (!harvest || typeof harvest !== "object") return "";
  const taken = (harvest.revealed || []).map((card) => {
    const by = card && card.taken_by;
    return by === undefined || by === null ? "" : String(by);
  }).join(",");
  const targets = (harvest.targets || []).map((target) => {
    const name = target && target.cancelled ? "invalid" : ((target && target.status) || "pending");
    return `${target && target.seat}:${name}`;
  }).join(",");
  return `${harvest.key || ""}|${harvest.current}|${taken}|${targets}`;
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
  // 票 01：亮牌步的候选里真有 `Pass`（攻心）⇒ 「放弃」是合法出口；没有可点的红桃时
  // 弹窗只展示整副手牌，提示直接指向「放弃」。
  if (pending && pending.dialog === DIALOG_KINDS.reveal && hasPassAction(pending)) {
    if (!interactive) return `${requirement}：等待其他座位响应…`;
    if (!((pending.slots || []).length)) {
      return `${requirement}：没有可展示的红桃牌，点右端「放弃」结束。`;
    }
    return `${requirement}：点一张红桃牌即展示；也可以点右端「放弃」。`;
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
 * `promptHint: false`：不抢提示条的步骤提示（那一步的 `#t-options` 仍留在提示区，
 * 牌桌照常可操作）。
 *
 * 正文不再需要给目标条预留高度（票 18）：条是 `.reveal` 的普通弹性子项，跟着牌面行排。
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

/* ------------------------------------------------------------ 按花色取牌（涉猎） */

/** 判据：**公开亮牌 + 每种花色至多 k 张**的选牌步（神吕蒙移动版【涉猎】）。
 *
 * 只吃载荷形状，不认技能 id：`SELECT_CARDS` ∧ `spec.max_per_suit` 有值 ∧ 槽位全在处理区
 * （公开亮牌，`_mask_watched_faces` 对 processing 槽位不抹牌面 ⇒ 非发动者也看得到五张）。
 * 与亮牌模板（`dialog = "reveal"`）分开：那一步是"观看授予 / 五谷目标条"，没有花色槽。
 */
function isSuitPickStep(pending) {
  if (!pending || pending.kind !== "SELECT_CARDS") return false;
  const spec = pending.spec || {};
  if (!spec.max_per_suit) return false;
  const slots = pending.slots || [];
  if (!slots.length) return false;
  return slots.every((slot) => slot.ref && slot.ref.zone === "processing");
}

/** 一个槽此刻的状态：`picked`（已选，可再点取消）/ `taken`（同花色已被选走 ⇒ 置灰）/
 *  `free`。判据只读 `state.picks` 与槽位牌面的花色。 */
function suitSlotState(pending, index) {
  if (state.picks.includes(index)) return "picked";
  const slot = pending.slots[index];
  const suit = slot && slot.card ? slot.card.suit : null;
  if (suit === null) return "free";
  const cap = Number((pending.spec || {}).max_per_suit) || 1;
  const same = state.picks.filter((at) => {
    const other = pending.slots[at];
    return other && other.card && other.card.suit === suit;
  }).length;
  return same >= cap ? "taken" : "free";
}

/** 点一张：已选 ⇒ 取消；同花色未满 ⇒ 选中；同花色已满 ⇒ 无动作（界面已置灰）。 */
function toggleSuitPick(pending, index) {
  if (state.playback || state.submitBusy) return;
  if (!isSuitPickStep(pending)) return;
  const at = state.picks.indexOf(index);
  if (at >= 0) {
    state.picks.splice(at, 1);
  } else if (suitSlotState(pending, index) === "free") {
    state.picks.push(index);
    state.picks.sort((a, b) => a - b);
  } else {
    return;
  }
  state.lastRejectReason = "";
  renderPending(state.view);
}

/** 这一步的步骤提示：规则话术读 `context.requirement`（服务端），前端只补"怎么点"。 */
function suitPickHint(pending, interactive) {
  const requirement = (pending && pending.context && pending.context.requirement) || "";
  const prefix = requirement ? `${requirement}：` : "";
  if (!interactive) return `${prefix}等待该座位选择…`;
  return `${prefix}点选每种花色至多一张（已选 ${state.picks.length} 张）；`
    + "点已选的牌可取消；点「提交」或右端「放弃」都按当前选择结算。";
}

/** 正文：一行亮出的牌；已选高亮、同花色其余置灰；非发动者只读。 */
function renderSuitPick(pending, opts = {}) {
  const box = opts.container || $("t-options");
  const interactive = opts.interactive !== false && isOwnPending(pending);
  if (opts.promptHint !== false) {
    const hint = $("t-target-hint");
    if (hint) hint.textContent = suitPickHint(pending, interactive);
  }
  const wrap = document.createElement("div");
  wrap.className = "reveal suit-pick";
  wrap.classList.add("reveal", "suit-pick");
  const row = document.createElement("div");
  row.className = "cards reveal-cards";
  row.classList.add("cards", "reveal-cards");
  (pending.slots || []).forEach((slot, index) => {
    const entry = slot.card;
    if (!entry) return;
    const status = suitSlotState(pending, index);
    const pickable = interactive && status !== "taken";
    const node = cardFaceNode(entry, {
      static: !pickable,
      onClick: pickable ? () => toggleSuitPick(pending, index) : undefined,
    });
    node.classList.add("reveal-card");
    node.setAttribute("data-slot-index", String(index));
    node.setAttribute("data-suit", String(entry.suit || ""));
    if (status === "picked") {
      node.classList.add("sel");
      node.setAttribute("data-picked", "true");
    }
    if (status === "taken") {
      node.classList.add("dim");
      node.setAttribute("data-dim", "true");
    }
    row.appendChild(node);
  });
  wrap.appendChild(row);
  box.appendChild(wrap);
}

/** 提交后的 2.5 秒结果态（仅发动者本地）：获得的牌保持高亮、其余置灰。 */
function renderSuitResult() {
  const result = state.suitResult;
  const box = $("t-options");
  if (!result || !box) return;
  box.innerHTML = "";
  const hint = $("t-target-hint");
  if (hint) hint.textContent = "已提交：亮起的牌被获得，置灰的牌置入弃牌堆。";
  const wrap = document.createElement("div");
  wrap.className = "reveal suit-pick";
  wrap.classList.add("reveal", "suit-pick");
  const row = document.createElement("div");
  row.className = "cards reveal-cards";
  row.classList.add("cards", "reveal-cards");
  (result.cards || []).forEach((entry, index) => {
    const node = cardFaceNode(entry, { static: true });
    node.classList.add("reveal-card");
    node.setAttribute("data-slot-index", String(index));
    node.setAttribute("data-suit", String(entry.suit || ""));
    if ((result.picked || []).includes(index)) {
      node.classList.add("sel");
    } else {
      node.classList.add("dim");
      node.setAttribute("data-dim", "true");
    }
    row.appendChild(node);
  });
  wrap.appendChild(row);
  box.appendChild(wrap);
}

/** 结果态是否还在展示（2.5 秒窗口）。 */
function suitResultActive() {
  const result = state.suitResult;
  return !!result && Date.now() < result.until;
}

/* ------------------------------------------------------------ 两行观看手牌（票 93） */

/** 空选择面（凑不出四色）/ 重复槽位的提示文案：**唯一一份**。
 *
 *  `watchRowsHint`（步骤提示）与 `pending.js:refreshSubmit`（页脚 `t-choice`）读同一组常量，
 *  两处不会各写一份、各自漂。 */
const WATCH_EMPTY_MESSAGE = "凑不出四种花色，只能点右端「放弃」结束。";
const WATCH_DUPLICATE_MESSAGE = "选择面里出现了重复的牌（数据异常），这一步无法提交。";

/** 两行面的**行数据**：按 `slots[].ref.seat` 分两行（行序 = 目标在上、自己在下的固定次序，
 *  行内顺序 = 载荷顺序）。空选择面（凑不出四色）没有槽位可分 ⇒ 只画上行：整副被观看的
 *  手牌在 `revealed` 里（与【攻心】"空选择面也要看牌"同一口径）。
 *
 *  **空面的 `revealed` 约定只含上行那一手**（被观看角色的手牌）——正常面的 `revealed` 是
 *  两副手牌的并集（同一份观看授予），空面没有座位形状可分，故由技能侧只授予上行那一手。
 *  非应答者的 `revealed` 已被抹空 ⇒ 仍画上行的标签、0 张牌（位置不是已知事实）。
 *
 *  这是从载荷到行的**纯拆分**：不认技能名、不猜牌面，判据仍住 `modal.js:isWatchTwoRowsStep`。
 *  行的牌面**不另开一份源**：正常面读 `slots[].card`（抹后的载荷里它就是 `null`），空面读
 *  `revealed`。
 */
function watchRows(pending) {
  const slots = (pending && pending.slots) || [];
  if (!slots.length) {
    return [{
      role: "target",
      seat: null,
      entries: [],
      cards: ((pending && pending.revealed) || []).slice(),
    }];
  }
  const groups = [];
  const bySeat = new Map();
  slots.forEach((slot, index) => {
    const seat = slot && slot.ref ? slot.ref.seat : null;
    const key = seat === null || seat === undefined ? "?" : String(seat);
    let group = bySeat.get(key);
    if (!group) {
      group = { seat, entries: [] };
      bySeat.set(key, group);
      groups.push(group);
    }
    group.entries.push({ slot, index });
  });
  const actor = Number(pending.actor);
  const target = groups.find((group) => Number(group.seat) !== actor) || null;
  const self = groups.find((group) => Number(group.seat) === actor) || null;
  // 目标在上、自己在下的**固定行序**（用户口径）；判据保证两组都存在，退化面照载荷顺序画。
  const ordered = [];
  if (target) ordered.push({ ...target, role: "target" });
  if (self) ordered.push({ ...self, role: "self" });
  return ordered.map((row) => ({
    ...row,
    cards: row.entries.map((entry) => entry.slot && entry.slot.card).filter(Boolean),
  }));
}

/** 行标签（用户 2026-11-07 的措辞）：应答者看到「座N·目标手牌」/「你的手牌」；
 *  非应答者只画公开形状，两行都写「座N·手牌」——那不是"你的"手牌。 */
function watchRowLabel(row, own) {
  if (row.seat === null || row.seat === undefined) return "目标手牌";
  if (!own) return `座${row.seat}·手牌`;
  return row.role === "self" ? "你的手牌" : `座${row.seat}·目标手牌`;
}

/** 提交至少要几张：读服务端 `spec.total` 的下限（当前形状 = 4；缺省兜底也按 4，见
 *  `WATCH_DEFAULT_TOTAL`）。四色各一由置灰保证，不写"同花色一张"的常量。 */
const WATCH_DEFAULT_TOTAL = [4, 4];

function watchRequiredCount(pending) {
  const total = (pending && pending.spec && pending.spec.total) || WATCH_DEFAULT_TOTAL;
  return Math.max(1, Number(total[0]) || 1);
}

/** 同一 `card_id` 在面里出现两次 = 引擎数据错误（两张牌物理上分属两个座位）：画两次也不
 *  该能提交（票面 §3 B2）。返回重复的 `card_id`（字符串）或 `null`。 */
function watchDuplicateCardId(pending) {
  const seen = new Set();
  for (const slot of (pending && pending.slots) || []) {
    const card = slot && slot.card;
    if (!card || card.card_id === null || card.card_id === undefined) continue;
    const key = String(card.card_id);
    if (seen.has(key)) return key;
    seen.add(key);
  }
  return null;
}

/** 点一张：已选 ⇒ 取消；同花色未满 ⇒ 选中；同花色已满 ⇒ 无动作（界面已置灰）。
 *
 *  槽位状态与【涉猎】**同一份**判据 `suitSlotState`（`picked` / `taken` / `free`；上限读
 *  `spec.max_per_suit`，**两行一起算**）——这里不另写一套，也不写死"同花色一张"。 */
function toggleWatchPick(pending, index) {
  if (state.playback || state.submitBusy) return;
  if (!isWatchTwoRowsStep(pending)) return;
  const at = state.picks.indexOf(index);
  if (at >= 0) {
    state.picks.splice(at, 1);
  } else if (suitSlotState(pending, index) === "free") {
    if (state.picks.length >= selectionCap(pending)) return;
    state.picks.push(index);
    // 引擎的 `SelectCards` 候选按面内位置**升序**枚举；选中序与点击顺序无关、必须保持升序
    // （与 `toggleSuitPick` / `answer.js:buildAction` 同一条纪律）。
    state.picks.sort((left, right) => left - right);
  } else {
    return;
  }
  state.lastRejectReason = "";
  renderPending(state.view);
}

/** 这一步的步骤提示：规则话术读 `context.requirement`（服务端），前端只补"怎么点"。
 *  空选择面（凑不出四色）**必须明说**只能放弃，否则玩家会一直点牌、永远到不了四张。 */
function watchRowsHint(pending, interactive) {
  const requirement = (pending && pending.context && pending.context.requirement) || "";
  const prefix = requirement ? `${requirement}：` : "";
  if (!interactive) return `${prefix}等待该座位选择…`;
  if (!((pending && pending.slots) || []).length) {
    return `${prefix}${WATCH_EMPTY_MESSAGE}`;
  }
  if (watchDuplicateCardId(pending)) {
    return `${prefix}${WATCH_DUPLICATE_MESSAGE}`;
  }
  const required = watchRequiredCount(pending);
  return `${prefix}点牌选中 / 再点取消；必须四种花色各一张（已选 ${state.picks.length}/${required}）`
    + "才能提交；点右端「放弃」结束。";
}

/** 正文：两行牌（上行目标手牌 / 下行自己的手牌），每行 = 行标签 + 一行 `cardFaceNode`
 *  （卡面自带 `tags`，来源技能标记照画）。已选 ⇒ `.sel`；同花色其余牌（两行都算）⇒ `.dim`
 *  且不接 click；非应答者的牌面是「?」、不给 click。
 *
 *  `opts.interactive === false` = 非应答者（牌面照画 / 位置照画，一律不接 click）。
 *  行内超过一屏时**横向滚动**（`.watch-row .cards` 的 `flex-wrap: nowrap`），不换行成
 *  第二行——"两行"是面的语义（票面 §3 B3）。
 */
function renderWatchRows(pending, opts = {}) {
  const own = isOwnPending(pending);
  const interactive = opts.interactive !== false && own;
  const hint = $("t-target-hint");
  if (hint) hint.textContent = watchRowsHint(pending, interactive);
  const wrap = document.createElement("div");
  wrap.className = "reveal watch-two-rows";
  for (const row of watchRows(pending)) {
    const section = document.createElement("section");
    section.className = "watch-row";
    section.setAttribute("data-watch-row", row.role === "self" ? "self" : "target");
    if (row.seat !== null && row.seat !== undefined) {
      section.setAttribute("data-seat", String(row.seat));
    }
    const head = document.createElement("div");
    head.className = "watch-row-head";
    head.textContent = watchRowLabel(row, own);
    section.appendChild(head);
    const cards = document.createElement("div");
    cards.className = "cards reveal-cards";
    if (row.entries.length) {
      for (const entry of row.entries) {
        const slot = entry.slot;
        const index = entry.index;
        const status = suitSlotState(pending, index);
        const pickable = interactive && status !== "taken";
        let node;
        if (slot && slot.card) {
          node = cardFaceNode(slot.card, {
            static: !pickable,
            onClick: pickable ? () => toggleWatchPick(pending, index) : undefined,
          });
        } else {
          // 非应答者 / 牌面被抹：位置照画、牌面「?」、不给 click（票面 §3 B4）。
          node = unknownSlotNode();
        }
        node.classList.add("reveal-card");
        node.setAttribute("data-slot-index", String(index));
        if (slot && slot.card && slot.card.suit) {
          node.setAttribute("data-suit", String(slot.card.suit));
        }
        if (status === "picked") {
          node.classList.add("sel");
          node.setAttribute("data-picked", "true");
        }
        if (status === "taken") {
          node.classList.add("dim");
          node.setAttribute("data-dim", "true");
        }
        cards.appendChild(node);
      }
    } else {
      // 空选择面：整副被观看的手牌只读显示（没有可点的槽，出口只有「放弃」）。
      // 非应答者这里没有牌面（`revealed` 已被抹空）⇒ 只留行标签。
      for (const card of row.cards) {
        const node = cardFaceNode(card, { static: true });
        node.classList.add("reveal-card");
        cards.appendChild(node);
      }
    }
    section.appendChild(cards);
    wrap.appendChild(section);
  }
  $("t-options").appendChild(wrap);
}
