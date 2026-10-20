/* SGS 前端 · 牌桌：座位 / 手牌 / 处理区 / 技能按钮渲染。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 牌桌 */

function renderTable() {
  const view = state.view;
  if (!view) return;
  const snapshot = view.snapshot;
  $("t-phase").textContent = phaseLabel(snapshot.phase);
  const turnSeat = snapshot.turn_seat;
  $("t-turn").textContent = turnSeat === null || turnSeat === undefined
    ? "—"
    : `座位 ${turnSeat}${turnSeat === snapshot.seat ? "（我）" : ""}`;
  // 牌堆 / 弃牌堆**数量**（服务端 `PlayerView` 只给牌堆数量、`discard` 按置入序给牌面，
  // 都是公开事实；这里只画数量，不泄露牌堆顺序）。
  $("t-deck").textContent = String(snapshot.deck_count ?? 0);
  $("t-discard").textContent = String((snapshot.discard || []).length);

  const pendingNow = view.pending || null;
  const handPick = isHandPick(pendingNow);
  const seats = pickerSeats(pendingNow);
  const seatOf = (seat) => seats.find((item) => item.seat === seat) || null;
  const focusEntry = state.focusCardId === null ? null : cardEntry(pendingNow, state.focusCardId);
  // 池就是"自己"一个座位（【无中生有】【桃】、装备牌）：不画座位高亮，免得看着像要打自己
  const selfOnly = targetsSelfOnly(focusEntry, snapshot.seat);
  // `SELECT_OPTION` 的"选座位"形态（【遗计】的"交给谁"，服务端下发 `option_seats`）：
  // 座位是**可点的**——点它就等于选中那条 `seat:N` 候选。选项串的解析在服务端。
  const optionSeats = (pendingNow && pendingNow.option_seats) || {};
  const optionChoiceAt = (seat) =>
    Object.keys(optionSeats).find((choice) => Number(optionSeats[choice]) === Number(seat)) || null;
  // 预发动（W4-1）：当前的选目标步 ⇒ 可点的座位来自**清单**（服务端已按前序选择算好）。
  const preStep = state.preactivation && state.preactivation.cursor < state.preactivation.steps.length
    ? state.preactivation.steps[state.preactivation.cursor]
    : null;
  const preSeats = preStep && preactivationStepKind(preStep) === "seat"
    ? (preactivationOptions(state.preactivation, state.preactivation.cursor)
      || snapshot.players.filter((player) => player.alive).map((player) => `${SEAT_PREFIX}${player.seat}`))
    : null;

  const ring = $("t-seats");
  ring.innerHTML = "";
  for (const player of snapshot.players) {
    const box = document.createElement("div");
    box.className = "seat";
    if (player.seat === snapshot.seat) box.classList.add("me");
    if (!player.alive) box.classList.add("dead");
    if (player.seat === snapshot.turn_seat) box.classList.add("turn");
    const seatState = seatOf(player.seat);
    const optionChoice = optionChoiceAt(player.seat);
    const optionSelected = optionChoice !== null
      && state.selectedOption !== null
      && (pendingNow.legal_actions[state.selectedOption] || {}).choice === optionChoice;
    if (state.preactivation) {
      if (preSeats && preSeats.includes(`${SEAT_PREFIX}${player.seat}`)) {
        const answer = state.preactivation.answers[state.preactivation.cursor];
        const required = preactivationRequiredSeat(
          state.preactivation, pendingNow, state.preactivation.cursor);
        if (required === `${SEAT_PREFIX}${player.seat}`) box.classList.add("req");
        if (answer && answer.values.includes(player.seat)) box.classList.add("tsel");
        else box.classList.add("targetable");
      }
    } else if (handPick && !selfOnly) {
      if (seatState && seatState.selected) box.classList.add("tsel");
      else if (seatState && seatState.isTargetable) box.classList.add("targetable");
    } else if (optionChoice !== null) {
      // 选座位形态（【遗计】"交给谁"）：座位可点，选中的那座画成确定态。
      if (optionSelected) box.classList.add("tsel");
      else box.classList.add("targetable");
    }
    const hero = view.heroes[player.seat] || "（无武将）";
    const identity = player.identity ? ` · ${identityLabel(player.identity)}` : "";
    box.setAttribute("data-seat", String(player.seat));
    box.innerHTML = `
      <div class="top">
        <span class="who">座位 ${player.seat}${player.seat === snapshot.seat ? "（我）" : ""}</span>
        <span class="hp">${"●".repeat(Math.max(0, player.hp))}${"○".repeat(Math.max(0, player.hp_max - Math.max(0, player.hp)))}</span>
      </div>
      <div class="small">${hero}${identity}</div>
      <div class="small">手牌 ${player.hand_count}　装备 ${player.equip.length}　判定 ${player.judge.length}</div>
      <div class="small">${player.alive ? "" : "已阵亡"}</div>`;
    // 装备区 / 判定区是**公开信息且逐座位**（`PlayerPublic.equip/judge` 早就下发了）：
    // 只写数量会让"谁的判定区里有乐不思蜀"看不见，进而把别人的判定误读成自己的。
    for (const [zone, cards] of [["equip", player.equip || []], ["judge", player.judge || []]]) {
      if (!cards.length) continue;
      const row = document.createElement("div");
      row.className = "cards mini";
      row.setAttribute("data-zone", zone);
      row.setAttribute("data-seat", String(player.seat));
      for (const card of cards) {
        // 「提供一张 B」面（票 14）：**本人自己**可见区域里的候选牌**就地**亮起、可点
        // （【武圣】的来源可以是装备 / 判定区的红牌）；别人的区与其余窗口保持静态不可点。
        // 预发动（W4-1）的选牌步同样在本人的装备区就地高亮（【制衡】/【贯石】的代价牌）。
        const ownInteractive = player.seat === snapshot.seat
          && (isProvide(pendingNow) || state.preactivation);
        if (!ownInteractive) {
          row.appendChild(cardChip(card, false, true));
          continue;
        }
        const face = cardFace(pendingNow, card);
        row.appendChild(
          cardChip(card, face.selected, false, face.selectable, () => activateCard(pendingNow, card, face)),
        );
      }
      box.appendChild(row);
    }
    if (state.preactivation) {
      if (preSeats && preSeats.includes(`${SEAT_PREFIX}${player.seat}`)) {
        box.addEventListener("click", () => selectPreativationSeat(player.seat));
      }
    } else if (seatState && !selfOnly) {
      if (seatState.selected || seatState.isTargetable) {
        box.addEventListener("click", () => selectTarget(player.seat));
      }
    } else if (optionChoiceAt(player.seat)) {
      // 选座位形态：只在座位上点，不在手牌上点（本窗口没有"选牌"这一步）。
      box.addEventListener("click", () => selectOptionSeat(player.seat));
    }
    ring.appendChild(box);
  }

  // 快照到达时把处理区并进 `state.effectCards`（同一张牌保留回放期间的使用者标注）；
  // 回放期间不并——那份是逐事件派生的，快照要等播完才提交。
  if (!state.playback) state.effectCards = mergeEffectCards(snapshot.processing || []);
  renderProcessing();

  $("t-hand-count").textContent = `${snapshot.self_hand.length} 张`;
  const hand = $("t-hand");
  hand.innerHTML = "";
  for (const card of snapshot.self_hand) {
    // 置灰 / 选中**只**由 :func:`cardFace` 从"当前询问 + 这张牌"算出（票 14 §D）：
    // 弃牌、技能手选、提供 B、普通手选四条路都在那一个函数里分流，这里不再各写一份。
    const face = cardFace(pendingNow, card);
    hand.appendChild(
      cardChip(card, face.selected, false, face.selectable, () => activateCard(pendingNow, card, face)),
    );
  }
  $("t-hero").textContent = view.heroes[snapshot.seat] || "（无武将）";
  renderSkills(view);
}

/** 效果区 / 处理区：画 `state.effectCards`（回放期间逐事件派生、快照到达时并进来），
 * 并把**描述文本**（`#t-context`）同步成它的说明——两者同源，故不会一个变一个不变。
 *
 * 描述文本曾经是服务端下发的"本窗口：谁对谁使用…"，只在询问到达时更新，回放期间会残留；
 * 现在它跟着效果区走：有牌写「处理中：【牌】（谁在使用）」，牌全走了**立即清空**。 */
function renderProcessing() {
  renderEffectCards($("t-processing"), state.effectCards || []);
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeEffect(state.effectCards || []);
}

/** 把一组"处理区牌"画进任意容器（牌桌 / 回放页共用同一份渲染）。
 *
 * 回放页的效果区**复用** :func:`applyEffectDelta` 的逐事件派生，渲染也复用这一处——
 * "怎么画处理区"只有一份实现，两个页面不会漂开。
 */
function renderEffectCards(box, cards) {
  if (!box) return;
  box.innerHTML = "";
  if (!cards || !cards.length) {
    const empty = document.createElement("span");
    empty.className = "dim";
    empty.textContent = "（空）";
    box.appendChild(empty);
    return;
  }
  for (const card of cards) box.appendChild(cardChip(card, false, true));
}

/** 技能按钮：三态由**数据**决定（`locked` 表类型、`available` 表此刻能不能发动）。
 *
 * 纪律：不发动的技能**不给点**——它是"引擎在时机上自动派发/弹询问"的东西，
 * 界面上点它没有意义。
 *
 * **预发动（W4-1 / W4-2）**：`skill.preactivated` 的技能只剩**清单入口**——点它进入
 * 清单驱动的本地态（确认 = 复合动作、取消零提交），不再去 `legal_actions` 里找
 * 骨架候选（已翻的技能连骨架都没有）。未翻的技能照旧：转化技亮点选来源牌，其余
 * 点一下 = 选中那条 `ActivateSkill` 候选。
 */
function renderSkills(view) {
  const box = $("t-skills");
  if (!box) return;
  box.innerHTML = "";
  const skills = view.skills || [];
  if (!skills.length) {
    box.innerHTML = '<span class="dim">（无武将，或本局未发放武将）</span>';
    return;
  }
  const pending = view.pending;
  for (const skill of skills) {
    const ui = skill.ui || {};
    const convertCards = ui.convert_sources || [];
    const preactivated = Boolean(skill.preactivated);
    // 清单有两类消费者（W4-2 / W4-3）：已翻到预发动的技能（`preactivated`）= **复合动作**
    // 清单；转化技 = **本地入口**清单（确认仍是普通候选 `UseCard(虚拟牌, 目标)`）。
    // 两者都由 `pending.preactivations[skill_id]` 下发，界面据 `preactivated` 分派。
    const manifest = preactivationManifest(pending, skill.skill_id);
    // 已翻预发动：**可用性只剩清单**（清单渲不出来 = 此刻不可用，与服务端同一判据）。
    // 转化技：候选或清单任一存在即点得动（清单为空 ⇒ 此刻没有可转化的使用 / 响应）。
    const usable = preactivated
      ? Boolean(manifest)
      : (Boolean(skill.available) || convertCards.length > 0 || Boolean(manifest));
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skill";
    if (skill.locked) button.classList.add("locked");
    if (usable) button.classList.add("ready");
    button.disabled = !usable;
    button.setAttribute("data-skill-id", skill.skill_id);
    button.setAttribute("data-available", String(skill.available));
    button.setAttribute("data-usable", String(usable));
    button.setAttribute("data-locked", String(skill.locked));
    if (preactivated) button.setAttribute("data-preactivated", "true");
    if (convertCards.length) button.setAttribute("data-convert", String(convertCards.length));
    if (manifest) button.setAttribute("data-preactivation", String(manifest.length));
    // 技能类别徽标（W4-fix-3）：**触发技**由服务端投影判（`ui.passive` = 有 hooks /
    // 接缝且没有主动入口），界面不按 `skill_type` 猜——`NORMAL` 里既有主动技也有被动技。
    const passive = Boolean(ui.passive);
    const kind = skill.carrier === "EQUIPMENT"
      ? (skill.locked ? "装备技 / 锁定" : "装备技")
      : (skill.locked
        ? "锁定技 / 被动"
        : (skill.skill_type === "LORD" ? "主公技" : (passive ? "被动技" : "主动技")));
    const badge = preactivated
      ? (manifest ? " · 预发动" : "")
      : (manifest ? " · 预发动" : (convertCards.length ? " · 点它转化" : (skill.available ? " · 可发动" : "")));
    button.innerHTML = `${skill.name}<span class="kind">${kind}${badge}</span>`;
    if (state.playback) {
      // 回放期间不接任何操作（严格顺序：这批事件播完才轮到下一次询问）。
    } else if (usable && pending && manifest) {
      // 清单驱动的本地态（W4-2 复合动作 / W4-3 转化技本地入口）：取消 = 什么都不提交
      // （被动入口 = 放弃）。转化技在"提供一张 B"的面里没有清单，仍走下面的转化模式。
      button.addEventListener("click", () => enterPreativation(skill, manifest));
    } else if (usable && pending && convertCards.length) {
      // 转化技（【龙胆】）：点它进入"挑一张来源牌"的模式（再点一次退出）。
      button.addEventListener("click", () => toggleConvertSkill(skill));
    } else if (skill.available && pending) {
      const index = pending.legal_actions.findIndex((a) => a.kind === "ActivateSkill" && a.skill_id === skill.skill_id);
      if (index >= 0) {
        button.addEventListener("click", () => {
          state.selectedOption = index;
          state.focusCardId = null;
          state.selectedTargets = [];
          state.lastRejectReason = "";
          renderTable();
          renderPending(state.view);
        });
      }
    }
    box.appendChild(button);
  }
}
