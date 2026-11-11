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

/** 自己的手牌里牌号为 ``cardId`` 的那张**展示副本**；找不到返回 ``null``。
 *
 * 展示字段（`display_name` / `type` / `description`）只挂在快照的牌面上：`legal_actions`
 * 里的牌故意不带它们（会随提交上行，见 `core.js:cardName`）。故"这张牌有什么入口"这类
 * 只读判断要先回到快照里取牌面。
 */
function selfHandCard(cardId) {
  const hand = (state.view && state.view.snapshot && state.view.snapshot.self_hand) || [];
  return hand.find((card) => String(card.card_id) === String(cardId)) || null;
}

/** 这张手牌此刻被**强制转化**成什么（‘只能’型转化，如神关羽【武神】）。
 *
 * 只读服务端 `pending.transform_products` 的 ``forced`` 标记（声明面 =
 * `ConversionSkill.forced`，**不是**锁定与否）：手牌区据此把源牌画成产物牌面
 * （右上角「转」、悬停列出源牌），点它进转化清单。
 *
 * ‘可以’型（武圣 / 龙胆 / 国色 / 急救 / 丈八 / 羽扇）的产物**仍会下发**——预发动提示要
 * 写「将以【X】使用」——但 ``forced === false``：手牌保持**原牌**，原用途照常可用，
 * 转化只经技能预发动。缺字段 / 不是 ``true`` ⇒ 不画（fail-closed，不把原牌吞成产物）。
 */
function convertedForm(pending, cardId) {
  for (const product of (pending && pending.transform_products) || []) {
    if (product.forced !== true) continue;
    for (const source of product.source_cards || []) {
      if (String(source.card_id) === String(cardId)) return product;
    }
  }
  return null;
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

/** 面里的每一槽都是**应答者自己的可见区**（手牌 / 装备，牌面非空）。
 *
 * 这是 :func:`isOwnZonePick` 与亮牌否决（`modal.js:isRevealStep`）共用的**形状判据**，
 * 故意**不读** `stepDialogKind`：`stepDialogKind` 自己会调亮牌判据，读它会绕成死循环。
 * 票 09（spec §1 #12）：自己的区对本人天然可见 ⇒ 它不是"观看授予"的亮牌面。
 */
function ownZoneSlotsVisible(pending) {
  if (!pending || pending.kind !== "SELECT_CARDS") return false;
  const slots = pending.slots || [];
  if (!slots.length) return false;
  const zones = (pending.spec && pending.spec.zones) || [];
  if (!zones.every((zone) => zone === "hand" || zone === "equip")) return false;
  return slots.every((slot) => {
    const ref = slot && slot.ref;
    return Boolean(ref)
      && Number(ref.seat) === Number(pending.actor)
      && (ref.zone === "hand" || ref.zone === "equip")
      && Boolean(slot.card);
  });
}

/** **从自己的手牌 / 装备区里选牌**的牌桌步（火攻 / 铁骑 / 青囊 / 结姻等）。
 *
 * 形状全部读载荷：`SELECT_CARDS`、面里每一槽都是**应答者本人**的手牌或装备区、牌面可见，
 * 且这一步不是「提供一张 B」/ 弹窗模板。命中时不再铺左下角选择面，而是在牌桌的牌面上
 * 直接点选；单张选择点一张即提交，多张选择点选后由「提交」送出。判据只是**渲染路径**，
 * 不裁决合法性（候选仍由 `legal_actions` 给）。
 */
function isOwnZonePick(pending) {
  if (!pending || pending.kind !== "SELECT_CARDS") return false;
  if (isProvide(pending) || stepDialogKind(pending) !== null) return false;
  return ownZoneSlotsVisible(pending);
}

/** 面里的牌**已经在本人手牌快照里**（= 不是"观看授予"，票 09 ②的对称兜底）。
 *
 * ``revealed`` 的语义是发给应答者的**额外**牌面；自己的手牌在 ``self_hand`` 里本来就有
 * 同一批牌面，故投影对"槽位全是应答者自己手牌"的面**不再授予** ``revealed``
 * （`tools/sgs_web/projection.py:_own_hand_face`），``dialog`` 随之变 ``null``。前端这一条
 * 是**对称否决**：旧服务端载荷 / 合成载荷里 ``dialog`` 仍是 ``"reveal"`` 时不误开亮牌窗，
 * 直接走牌桌直选。
 *
 * **判据不能只看"己方可见槽位"**：【遗计】/【藏匿】一类**观看授予**也把牌摆在
 * ``ref: {seat: actor, zone: "hand"}`` 的可见槽上（那几张牌还没进手牌、是授予的观看面），
 * 它们**必须**继续开亮牌窗。两者的分界就是"这几张牌在不在本人手牌快照里"。
 * 读 ``pending.view`` 优先（真载荷里挂着同一份内嵌视角），合成载荷退回 ``state.view``——
 * 与 `modal.js:activationFaceSurface` 读 `state.preactivation` 同一姿态（`stepDialogKind`
 * 本来就允许读一点本地态）。
 */
function ownHandSlotsKnown(pending) {
  if (!ownZoneSlotsVisible(pending)) return false;
  const view = (pending && pending.view) || state.view;
  const hand = (view && (view.self_hand || (view.snapshot && view.snapshot.self_hand))) || [];
  if (!hand.length) return false;
  const known = new Set(hand.map((card) => String(card.card_id)));
  return (pending.slots || []).every((slot) => slot.card && known.has(String(slot.card.card_id)));
}

/** 自己区域面里的**花色上限**（`spec.max_per_suit`）：这一槽是否因同花色已满而必须置灰。
 *
 * 票 09 ③（spec §1 #6 的业炎半 / §2 Q6）：`max_per_suit` 是服务端 `SelectionSpec` 上的
 * **一条局部约束的声明**（"每种花色至多 k 张"），前端只把这条声明读成置灰 / 拒点，
 * **不**重算任何其它合法性（张数 / 区域 / 同花色以外的规则仍由候选与提交裁决）。
 * 判据与涉猎 / 两行观看手牌**同一份**（`reveal.js:suitSlotState`），不另写一套。
 *
 * **止损**：这是前端实现一遍规则的第二份真源，只允许覆盖"每花色至多 k 张"这一条；
 * 完整的"每一步都由后端算"由票 11（步骤预览端点）/ 票 12 接手，届时本函数应改读预览
 * 结果并删除（票面 §3 "要做什么" ③ 的退役条件）。
 */
function ownZoneSuitTaken(pending, index) {
  if (!isOwnZonePick(pending)) return false;
  if (!((pending.spec || {}).max_per_suit)) return false;
  return suitSlotState(pending, index) === "taken";
}

/** 只选一张的自己区域面：点一张即提交（与多张选择的"点选 + 提交"分开）。 */
function isOwnHandSinglePick(pending) {
  return isOwnZonePick(pending) && selectionCap(pending) === 1;
}

/** 这一类「自己区域内选牌」是否包含装备区（决定提示文案 / 装备槽是否接点击）。 */
function ownPickHasEquip(pending) {
  return ((pending && pending.slots) || []).some(
    (slot) => slot && slot.ref && slot.ref.zone === "equip",
  );
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

/** 牌名级清单**要玩家一步步选什么**的名词短语（只读清单形状：不认牌名、不认技能）。
 *
 * 载体是 `pending.card_preactivations[card.type]`（票 05）：清单由服务端下发，"这一步选
 * 什么"由该步自己的形状回答——`card` = 来源牌、`seat` = 座位（持有者 / 目标）、
 * `option` = 选项文本。两处**通用**入口（「使用【X】」按钮的 title 与重铸提示条）共用
 * 这一句：同一张牌可能只有来源步、也可能来源 + 目标两步，文案跟着清单走，不写死任何
 * 一张牌的形状（`options.js:renderCardPreativationEntry` / 本文件的 :func:`reforgeHint`）。
 */
function preactivationPickWording(manifest) {
  const kinds = new Set((manifest || []).map((step) => preactivationStepKind(step)));
  const picks = [];
  if (kinds.has("card")) picks.push("来源牌");
  if (kinds.has("seat")) picks.push("座位（玩家 / 目标）");
  if (kinds.has("option")) picks.push("选项");
  return picks.length ? picks.join("与") : "每一步";
}

/** 焦点手牌没有目标可点时的提示：它可能还有**重铸**这条独立动作（M5-08b / 票 05）。
 *
 * 重铸不算使用、没有目标：这类牌（【连环】授予重铸的♣牌）点牌后「提交」恒灰，出口是
 * 控件区那条 ``Reforge``。同一张牌**还**有牌名级预发动（服务端下发的清单）时，使用方向
 * 走并列的「使用【X】」入口——两句话都说清，玩家才知道该点哪个。
 */
function reforgeHint(pending) {
  const action = reforgeAction(pending, state.focusCardId);
  if (!action) return "本窗口无需指定目标。";
  const name = cardName(action.card);
  const manifest = cardPreactivationManifest(pending, selfHandCard(state.focusCardId));
  if (manifest) {
    return `点「使用【${name}】」按清单逐步选${preactivationPickWording(manifest)}；`
      + `点「重铸【${name}】」弃置并摸一张（不算使用）。`;
  }
  return `这张牌此刻只可重铸：点「重铸【${name}】」弃置并摸一张（不算使用）。`;
}

/** 目标区的一句人话（不推荐、不排序，只说"还差什么"）。 */
function pickerTargetHint(pending) {
  if (!isHandPick(pending)) return "";
  if (state.focusCardId === null) return "点一张手牌 = 选牌；点座位 = 选目标。";
  const entry = cardEntry(pending, state.focusCardId);
  if (!entry) return "";
  let suffix = "";
  if (entry.max_targets === 0) {
    suffix = reforgeHint(pending);
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
        // 目标数是一个**区间**（铁索 1–2；方天画戟 1–3）：先说最少、再说最多。
        const sizes = rest.map((combo) => combo.length);
        const least = Math.max(1, Math.min(...sizes));
        const most = Math.max(...sizes);
        const needLeast = Math.max(0, least - have);
        const needMost = Math.max(0, most - have);
        if (needMost <= 0) {
          suffix = "这组目标不是合法组合，点座位取消再选。";
        } else if (needLeast === needMost) {
          suffix = `还要选 ${needMost} 个目标（已选 ${have}）。`;
        } else {
          suffix = `还要选 ${needLeast}–${needMost} 个目标（已选 ${have}）。`;
        }
      }
    }
  }
  return `${sourceConversionHint(pending)}${suffix}`;
}

/** 这张牌的**目标是否由牌自己的规则钉死为使用者本人**：这类牌（【无中生有】【桃】、装备牌）
 *  不画座位高亮。
 *
 *  判据是服务端下发的 ``entry.self_only``（真源 = 牌行为表的 ``TargetRule.self_only``，
 *  见 `projection.py:candidate_index`），**不是**从池的形状反推。旧读法
 *  `pool.length === 1 && pool[0] === selfSeat` 是"规则自用"的**形状代理**：【帷幕】把黑色
 *  1–2 目标锦囊的池削到只剩自己之后，`pool == [selfSeat]` 与【无中生有】逐字相同，于是自己的
 *  座位既没有 `tsel` / `targetable` 也没有点击口——牌点得亮却指定不了目标（issue 19）。
 *
 *  缺字段（旧录像 / 合成载荷）按"不是自用牌"处理：多画一次座位高亮不会吞掉任何点击
 *  （点自己选中的仍是一条合法组合），比反过来安全。
 */
function targetsSelfOnly(entry) {
  return Boolean(entry) && entry.self_only === true;
}
