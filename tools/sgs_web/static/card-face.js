/* SGS 前端 · 卡面：选中 / 置灰 / 槽位派生与点牌分派。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------- 提供一张 B（票 14） */

/** 这一步是不是**"提供一张 B"**（票 14）：``provide_type`` 非空 = B 的牌名 id。
 *
 * 服务端只在**成本 / 续用 / 被问使用或打出**这类"提供一张 B"的询问上下发它
 * （`flow.PickProvide`）。这种面**就地**渲染：候选牌在本人手牌 / 装备区 / 判定区
 * 直接亮起，不新开选择区（用户裁定 7）。
 */
function isProvide(pending) {
  return Boolean(pending) && pending.kind === "SELECT_CARDS" && Boolean(pending.provide_type);
}

/** "提供一张 B"面上这张牌对应的**槽位序号**（票 14）；``-1`` = 这张牌不在候选面里。
 *
 * 面上位置（不是牌 id）才是提交要的答案：虚拟 B 背后没有实体牌，服务端的 ``picks``
 * 是面内下标。两类命中：
 *
 * 1. **实体 B**：槽上的牌面就是这张牌（手牌 / 装备 / 判定区里的【杀】）；
 * 2. **转化来源**：服务端 ``virtual_index`` 按来源实体牌给出面内那张虚拟 B 的 id
 *    （【龙胆】的【闪】/【武圣】的红牌）。预发动（点了技能按钮）后只认那条技能的方向。
 */
function provideSlotIndex(pending, cardId) {
  if (!isProvide(pending)) return -1;
  const slots = pending.slots || [];
  for (let index = 0; index < slots.length; index += 1) {
    const face = slots[index].card;
    if (face && String(face.card_id) === String(cardId)) return index;
  }
  let aliases = cardAliases(pending, cardId);
  if (state.convertSkill !== null) {
    aliases = aliases.filter((alias) => (alias.skill_ids || []).includes(state.convertSkill));
  }
  for (const alias of aliases) {
    const index = slots.findIndex(
      (slot) => slot.card && String(slot.card.card_id) === String(alias.card_id),
    );
    if (index >= 0) return index;
  }
  return -1;
}

/** 这张牌此刻的 ``{selectable, selected, picked, slotIndex}``——**唯一**的置灰 / 选中派生
 *  （票 14 §D）。``selected`` = 焦点牌；``picked`` = 已收进待弃置 / 面内已选 / 预发动已选。
 *
 * 输入只有"当前询问 + 这张牌"：没有一处缓存上一问的置灰，:func:`renderTable` 每次
 * 整体重算，`state.picks` 又在新询问到达时由 :func:`resetSelection` 清空 ⇒
 * **进询问 / 换询问 / 退出询问**三个时点各自只看当前状态，不叠加、不粘滞（用户逐字要求）。
 * 无询问（退出询问）按"候选索引里有没有它"退回可不用/打出，而不是保留上一问的亮面。
 */
function cardFace(pending, card) {
  const slotIndex = provideSlotIndex(pending, card.card_id);
  if (slotIndex >= 0) {
    const total = (pending.spec && pending.spec.total) || [1, 1];
    const picked = state.picks.includes(slotIndex);
    return {
      selectable: picked || state.picks.length < total[1],
      selected: false, picked, slotIndex,
    };
  }
  if (isOwnHandSinglePick(pending)) {
    // 单张自己区域面（火攻 / 铁骑等同花色弃牌）：只有面里那几张可点；点一张即提交。
    const slots = pending.slots || [];
    const slotIndex = slots.findIndex(
      (slot) => slot.card && String(slot.card.card_id) === String(card.card_id),
    );
    return { selectable: slotIndex >= 0, selected: false, picked: false, slotIndex };
  }
  if (isOwnZonePick(pending)) {
    // 多张自己区域面（结姻两张 / 贯石斧两张等）：点牌收进 `picks`，点「提交」送出。
    const slots = pending.slots || [];
    const slotIndex = slots.findIndex(
      (slot) => slot.card && String(slot.card.card_id) === String(card.card_id),
    );
    const picked = slotIndex >= 0 && state.picks.includes(slotIndex);
    const total = (pending.spec && pending.spec.total) || [1, 1];
    return {
      selectable: slotIndex >= 0 && (picked || state.picks.length < Math.max(1, total[1])),
      selected: false,
      picked,
      slotIndex,
    };
  }
  if (isDiscardPick(pending)) {
    const picked = state.picks.includes(card.card_id);
    return {
      selectable: picked || state.picks.length < (pending.count || 0),
      selected: false, picked, slotIndex: -1,
    };
  }
  if (state.preactivation) {
    const options = preactivationCardOptions(pending);
    const answer = state.preactivation.answers[state.preactivation.cursor];
    return {
      selectable: options === undefined ? false : (options === null || options.includes(String(card.card_id))),
      selected: false,
      picked: Boolean(answer && answer.values.includes(card.card_id)),
      slotIndex: -1,
    };
  }
  // 手牌入口的预发动（W5-02b 借刀）：点它进入两步清单。同一张牌**还**有技能授予的重铸时
  // 点牌先给焦点（见 :func:`activateCard`，票 05）——故有无重铸都要画选中态：焦点落在这张
  // 牌上却没有视觉反馈会让玩家以为没点中。
  if (cardPreactivationManifest(pending, card)) {
    return {
      selectable: true,
      selected: state.focusCardId === card.card_id,
      picked: false,
      slotIndex: -1,
    };
  }
  // **强制转化**的手牌（‘只能’型，神关羽【武神】）：手牌区画的是产物（「转」+ 属性色），
  // 点它 = 进入该技能的转化清单（来源步预选这张牌），再点座位提交。没有直接候选也点得动。
  if (pending && convertedForm(pending, card.card_id)) {
    return { selectable: true, selected: false, picked: false, slotIndex: -1 };
  }
  const selectable = pending
    ? (isHandPick(pending) ? cardSelectable(pending, card.card_id) : cardUsable(pending, card.card_id))
    : false;
  return {
    selectable, selected: state.focusCardId === card.card_id, picked: false, slotIndex: -1,
  };
}

/** 点一张牌：按 :func:`cardFace` 给出的**当前模式**分派（提供 B / 弃牌 / 预发动 / 设焦点）。
 *
 * 牌桌选择入口的**总闸**：只读态（D8，当前步骤的表面是弹窗）里一次都不分派；被分派到的
 * 四个函数也各自再问一次 `selectionEnabled()`（它们还有别的调用方，例如键盘 / 烟测）。
 *
 * **牌名级预发动与重铸并存**（票 05 / 缺陷 A）：`cardPreactivationManifest` 原先排在这里、
 * 无条件抢在 `toggleFocus` 之前，于是一张♣【借刀杀人】永远只进清单、设不上 `focusCardId`，
 * 而重铸入口只认焦点 ⇒ 「重铸」永远不出现。现在只有**没有**重铸候选的牌才由点牌进清单；
 * 两者都有的牌点牌 = 设焦点，清单入口由 `options.js:renderReforgeEntry` 并列渲染。
 */
function activateCard(pending, card, face) {
  if (!selectionEnabled()) return;
  if (isProvide(pending)) {
    if (face.slotIndex >= 0) toggleProvidePick(face.slotIndex);
    return;
  }
  if (isOwnHandSinglePick(pending)) {
    if (face.slotIndex >= 0) submitOwnHandSinglePick(pending, face.slotIndex);
    return;
  }
  if (isOwnZonePick(pending)) {
    if (face.slotIndex >= 0) {
      toggleSlotPick(pending, face.slotIndex);
      renderTable();
      renderPending(state.view);
    }
    return;
  }
  if (isDiscardPick(pending)) { toggleDiscardPick(card.card_id); return; }
  if (state.preactivation) { togglePreativationCard(card.card_id); return; }
  const manifest = cardPreactivationManifest(pending, card);
  if (manifest) {
    // 票 05 / 缺陷 A：一张♣【借刀杀人】同时有**牌名级预发动**（两步清单）与技能授予的
    // **重铸**（【连环】）时，清单先手会把重铸入口挡死——重铸入口只认 `focusCardId`
    // （`options.js:renderReforgeEntry`），而进清单永远设不上焦点。有重铸候选就给焦点，
    // 清单改由控件区并列的「使用【X】」入口进入（`renderReforgeEntry`，落法 = 并存）；
    // 没有重铸候选的牌维持"点牌 = 进清单"（借刀的本行不变）。
    if (reforgeAction(pending, card.card_id) === null) {
      enterCardPreativation(pending, card, manifest);
      return;
    }
  }
  // 强制转化（‘只能’型）：有产物、且这张牌此刻没有直接候选 ⇒ 点它 = 走转化清单。
  if (convertedForm(pending, card.card_id) && !cardSelectable(pending, card.card_id)) {
    enterConvertedForm(pending, card);
    return;
  }
  toggleFocus(card.card_id);
}
