/* SGS 前端 · 候选索引：牌 → 目标池 / 组合的只读查询与提示。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------ 候选索引 */

/** 这次询问的候选索引：**只读服务端下发的那一份**（`pending.candidate_index`）。
 *
 * 前端**不自己从 `legal_actions` 重建索引**——那是一份会漂移的第二实现，
 * 而"池 ⊆ 候选、候选 ⊆ 索引"这条不变量已经由 `tests/test_web.py` 钉在服务端那一侧。
 * 空索引是合法状态（`Pass` / `SelectOption` 这类没有"牌 → 目标"的询问就是空的）。
 */
function cardIndex(pending) {
  return (pending && pending.candidate_index) || {};
}

/** 这张**实体手牌**能转化出的虚拟牌候选（服务端 `pending.virtual_index`）。
 *
 * 转化技（【龙胆】）的候选里带的 ``card`` 是虚拟牌（``card_id < 0``），而手牌区渲染的是
 * 实体牌；服务端按"实体来源 id → [{card_id, skill_ids}]"下发了别名索引，界面只读它。
 */
function cardAliases(pending, cardId) {
  const map = (pending && pending.virtual_index) || {};
  return map[String(cardId)] || [];
}

/** 这张牌此刻的候选条目：先看它自己，再看**转化别名**（单来源、必要时限定当前技能）。
 *
 * 返回的 ``entry.card`` 可能是虚拟牌——它才是要提交的那张；焦点仍是玩家点的**实体牌**，
 * 故 :func:`buildAction` 按 ``entry.card.card_id`` 找候选，而不是按焦点 id。
 */
function cardEntry(pending, cardId) {
  const direct = cardIndex(pending)[String(cardId)] || null;
  if (direct) return direct;
  let aliases = cardAliases(pending, cardId);
  if (state.convertSkill !== null) {
    aliases = aliases.filter((alias) => (alias.skill_ids || []).includes(state.convertSkill));
  }
  if (aliases.length !== 1) return null;
  return cardIndex(pending)[String(aliases[0].card_id)] || null;
}

/** 这张牌此刻点不点得动：候选索引里有它才有候选（唯一判据，不读规则）。 */
function cardSelectable(pending, cardId) {
  return cardEntry(pending, cardId) !== null;
}

function sameCombo(a, b) {
  if (a.length !== b.length) return false;
  const left = a.slice().sort((x, y) => x - y);
  const right = b.slice().sort((x, y) => x - y);
  return left.every((v, i) => v === right[i]);
}

/** 组合是否恰好是候选索引里的一组（**唯一的合法性判据**）。 */
function isLegalCombo(pending, cardId, targets) {
  const entry = cardEntry(pending, cardId);
  if (!entry) return false;
  return entry.combos.some((combo) => sameCombo(combo, targets || []));
}

/** **自动选中**：候选组合恰好只有一组、且那组就是池的全部 ⇒ 返回那一组；否则 ``null``。
 *
 * 判据只读候选索引，不引用任何规则知识。反例：借刀杀人的被迫【杀】是"池 4、目标数 1、
 * 组合 4 条"——此时**必须手选**，否则会把 4 个座位全自动选中。
 */
function autoCombo(pending, cardId) {
  const entry = cardEntry(pending, cardId);
  if (!entry || entry.combos.length !== 1) return null;
  const combo = entry.combos[0];
  const pool = entry.pool.slice().sort((x, y) => x - y);
  const sorted = combo.slice().sort((x, y) => x - y);
  if (pool.length !== sorted.length) return null;
  if (!pool.every((seat, i) => seat === sorted[i])) return null;
  // 空组合（无目标使用：【闪】【无懈】）**不算自动选中**——没有目标可填，
  // 返回 `[]` 会让"已自动选中"这句话为假（`[]` 是 truthy，调用方会当成填好了）。
  return combo.length === 0 ? null : combo.slice();
}

function isHandPick(pending) {
  return Boolean(pending) && HAND_PICK_KINDS.includes(pending.kind);
}

/** 弃牌阶段：点手牌 = 收进 / 移出待弃置集合（`picks` 装 **card_id**，与 `DiscardCards.cards` 同键）。 */
function isDiscardPick(pending) {
  return Boolean(pending) && pending.kind === "DISCARD";
}

/** **只从自己的手里选一张**的牌桌步（M5-08a 的【火攻】同花色弃牌）。
 *
 * 形状全部读载荷：`SELECT_CARDS`、**服务端下发了一个已亮出的牌**（`pending.reveal`，
 * 火攻弃牌步的公开事实）、上限 1、面里每一槽都是**应答者本人**的手牌且牌面可见，且这一步
 * 不是「提供一张 B」/ 弹窗模板。命中时点一张手牌 = **一次提交**（与亮牌模板的「选完即提交」
 * 同一口径），右端「放弃」是另一个合法出口；没有多选态要清、也没有第二个提交点。
 * 判据只是**渲染路径**，不裁决合法性（候选仍由 `legal_actions` 给）；`pending.reveal`
 * 把"可选一张自己的手牌"里这一类（青囊 / 结姻 / 雌雄的普通选牌）挡在外面。
 */
function isOwnHandSinglePick(pending) {
  if (!pending || pending.kind !== "SELECT_CARDS") return false;
  if (!pending.reveal || !pending.reveal.card) return false;
  if (isProvide(pending) || stepDialogKind(pending) !== null) return false;
  const slots = pending.slots || [];
  if (!slots.length || selectionCap(pending) !== 1) return false;
  return slots.every((slot) => {
    const ref = slot && slot.ref;
    return Boolean(ref)
      && Number(ref.seat) === Number(pending.actor)
      && ref.zone === "hand"
      && Boolean(slot.card);
  });
}

/** 出牌阶段 / 响应窗口下"哪些座位此刻可点"（**数据**给的，不是 CSS 猜的）。
 *
 * 返回**池里的每一个座位**并标出它此刻是什么状态，而不是只返回"可点的那些"——
 * 已选目标必须留在里面，否则它就没法被再点一次取消（点击路径只在"池里"的座位上生效）。
 */
function pickerSeats(pending) {
  if (!isHandPick(pending)) return [];
  if (state.focusCardId === null) return [];
  const entry = cardEntry(pending, state.focusCardId);
  if (!entry) return [];
  if (entry.max_targets === 0) return [];   // 无目标使用（【闪】【无懈】）：没有座位可点
  const selected = state.selectedTargets;
  const candidates = entry.combos.filter((combo) => selected.every((seat) => combo.includes(seat)));
  return entry.pool.map((seat) => ({
    seat,
    selected: selected.includes(seat),
    isTargetable: candidates.some((combo) => combo.includes(seat) && !selected.includes(seat)),
  }));
}

/** 选中的源牌会以哪张**产物**使用（U1-07）：转化技的候选是虚拟牌，手牌里点的是
 * 源实体牌。返回一句「将以【杀】使用」；不是转化来源时返回空串。
 *
 * **只看候选索引**（服务端 `candidate_index` + `virtual_index`）：来源牌的判据是
 * 它对应的候选带 ``virtual_sources``，本函数不读规则、不自己拼牌名。焦点牌本身
 * 就是候选（没有转化）时不写这句。
 */
function sourceConversionHint(pending) {
  if (!pending || state.focusCardId === null) return "";
  const entry = cardEntry(pending, state.focusCardId);
  const product = entry && entry.card;
  if (!product || !product.virtual_sources || !product.virtual_sources.length) return "";
  if (String(product.card_id) === String(state.focusCardId)) return "";
  return `将以【${cardName(product)}】使用。`;
}

/** 目标区的一句人话（不推荐、不排序，只说"还差什么"）。 */
function pickerTargetHint(pending) {
  if (!isHandPick(pending)) return "";
  if (state.focusCardId === null) return "点一张手牌 = 选牌；点座位 = 选目标。";
  const entry = cardEntry(pending, state.focusCardId);
  if (!entry) return "";
  let suffix = "";
  if (entry.max_targets === 0) {
    suffix = "本窗口无需指定目标。";
  } else if (autoCombo(pending, state.focusCardId)) {
    suffix = "目标由规则唯一确定，已自动选中（点牌可整张取消）。";
  } else {
    const have = state.selectedTargets.length;
    if (isLegalCombo(pending, state.focusCardId, state.selectedTargets)) {
      suffix = "目标已选好，可以提交。";
    } else {
      const rest = entry.combos.filter(
        (combo) => state.selectedTargets.every((seat) => combo.includes(seat)),
      );
      if (!rest.length) {
        suffix = "这组目标不是合法组合，点座位取消再选。";
      } else {
        const need = Math.max(...rest.map((combo) => combo.length)) - have;
        suffix = need > 0
          ? `还要选 ${need} 个目标（已选 ${have}）。`
          : "这组目标不是合法组合，点座位取消再选。";
      }
    }
  }
  return `${sourceConversionHint(pending)}${suffix}`;
}

/** 目标座位是否**就是自己或空集**：这类牌（【无中生有】【桃】、装备牌）不画座位高亮。 */
function targetsSelfOnly(entry, selfSeat) {
  if (!entry) return false;
  return entry.pool.length === 1 && entry.pool[0] === selfSeat;
}
