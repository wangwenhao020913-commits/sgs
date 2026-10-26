/* SGS 前端 · 询问弹窗框架（U2-06 / spec D3）+ 武将详情检视窗（U1-06 / D8）。
 *
 * 两个窗口、两条纪律：
 *
 *  - **`#t-dialog` = 询问弹窗**（唯一的询问表面容器）。标题 + 可选步骤提示 + 内容槽 +
 *    折叠按钮。**没有关闭按钮、没有 ESC 关闭、没有点背景关闭**——退出只有两条路：
 *    完成选择（提示条右端「提交 / 确认」）或该步有 Pass 时的「放弃」（同样在右端）。
 *    弹窗里**没有**提交 / 取消 / 结束出牌 / 放弃，操作型按钮永远在提示条右端（D3）。
 *    折叠只隐藏正文（标题 + 步骤提示 + 展开按钮留下），不改只读态。
 *  - **`#t-modal` = 武将详情**：唯一可叠加在询问弹窗之上的检视窗；它自己的关闭按钮 /
 *    ESC 只关它自己，底下的询问弹窗原样恢复；开合它不改变只读态。
 *
 * 生命周期：询问弹窗只在「当前步骤的表面是弹窗」时存在（`stepDialogKind`）。另外两个
 * **牌桌表面**的用户也借这个容器、都不进只读判据：本地态的预发动清单（D2 的 C 形），
 * 以及五谷整段结算的**持久展示**（无懈 / 反无懈的 RESPOND 期间只展示亮牌 + 目标条，
 * 见 `persistentRevealPending`）。同一问的重复渲染只更新正文、保留折叠；换问（新 `seq`）
 * 或表面不再是弹窗时**关旧开新 / 直接关**，
 * 关闭时清空正文——上一问的窗口 / 内容不会留在下一问里。跨步骤（弹窗步 → 牌桌步 →
 * 弹窗步）因此会先关后开；同一条技能链上的连续弹窗步（反间：花色 → 盲挑）走**链键**，
 * 容器不关、只换正文 / 标题（见 :func:`dialogKeyFor`）。
 *
 * 只读态的唯一判据仍是 `stepDialogKind()`（只看这一步的表面）：票 07–12 接四类模板时
 * **只在这一处加分支**（`DIALOG_KINDS` 已把四种稳定种类名留好），`selectionEnabled()`
 * 与全部选择入口都不用动。`state.dialog.collapsed` 不参与判据（折叠只改遮挡）。
 *
 * 遮罩是 `pointer-events: none` 的暗化层：没有自己的点击（D8），不吞指针；它由询问弹窗
 * 展开或武将详情打开时显示，折叠 / 关闭后收起。
 */
"use strict";

/** 四类询问模板的稳定种类名（spec D3）。票 06 接「排列」、票 08 接「选择型」与
 *  「未知区域（盲挑形状）」；票 09 / 11 / 12 继续在 `stepDialogKind` 里加判据即可，
 *  **不要**另写只读判据或另起容器。 */
const DIALOG_KINDS = Object.freeze({
  arrange: "arrange",
  choice: "choice",
  unknown: "unknown",
  reveal: "reveal",
});

/** 这几类弹窗步骤**没有「放弃」**（spec D3 / 票 08 / 09 / 11）：即便引擎给了 `Pass`，
 *  界面也不读它——选择型、未知区域、亮牌三类都是"点内容即完成 / 必答"。
 *
 *  `renderRightActions` / `cancelAction` / `refreshSubmit` 三处原先各写一遍同一条析取；
 *  抽成这里是为了改一处即三处一致（评审 Standards：重复谓词）。
 */
function dialogDisablesPass(kind, pending = null) {
  // A1：洛神连续判定的亮牌确认是**可选**询问——「放弃」= Pass = 停止；不能被
  // "亮牌弹窗一律没有放弃"的旧规则吞掉。
  if (kind === DIALOG_KINDS.reveal && pending && isJudgmentRevealOption(pending)) {
    return false;
  }
  return kind === DIALOG_KINDS.choice
    || kind === DIALOG_KINDS.unknown
    || kind === DIALOG_KINDS.reveal;
}

/** 当前**步骤**的表面若是弹窗就返回它的种类（spec D8「只读态」的唯一判据源）。
 *
 * 排列（`ARRANGE`）、选择型（服务端带 `option_labels` 的 `SELECT_OPTION`）、未知区域
 * （`SELECT_CARDS` 里"挑另一名角色三区里的一张牌"的形状）都是弹窗表面。另外两类窗口
 * **不**进这里：
 *  - 预发动清单是**牌桌表面**（D2 的 C 形：点自己的牌 / 点自己的座位），打开时牌桌
 *    照常接选择——它只是把选项搬进了窗口，不是"这一步的答案在窗内"；
 *  - 武将详情是叠加的**检视窗**：开合不改变只读态，所以这里读的是**底下那一步**的
 *    表面，而不是"哪个窗口正开着"。
 *
 * 票 07–12 的四个模板在这里加分支（槽位已写在 `DIALOG_KINDS` 里）：
 *   - 选择型（花色 / 牌名枚举）→ `DIALOG_KINDS.choice`（票 08）；
 *   - 未知区域（他人手牌 / 装备 / 判定）→ `DIALOG_KINDS.unknown`（票 08 接反间盲挑，
 *     票 09 推广到反馈 / 拆 / 顺 / 寒冰剑的"从别人的三区挑一张"形状）；
 *   - 亮牌（五谷 / 遗计）→ `DIALOG_KINDS.reveal`（票 11 / 12）。
 * 判据仍然只看 `pending`，不看 `state.dialog` / 折叠状态；"这一步是不是自己的询问"
 * 由调用方（`syncDialog`）另问一次 `isOwnPending`。
 */
function stepDialogKind(pending) {
  if (!pending) return null;
  if (pending.kind === "ARRANGE") return DIALOG_KINDS.arrange;
  // 选择型（票 08）：服务端在载荷里给了选项标签（花色是第一个复用枚举），或选项 ≥4
  // （D3 的模板判据）⇒ 画弹窗。前端不按选项值自己判断"这是不是花色"——那会在这里
  // 长出第二份枚举表。
  if (isChoiceStep(pending)) return DIALOG_KINDS.choice;
  if (isUnknownZoneStep(pending)) return DIALOG_KINDS.unknown;
  if (isRevealStep(pending)) return DIALOG_KINDS.reveal;
  return null;
}

/** 选择型模板的判据（D3）：`SELECT_OPTION` 且（有服务端选项标签 或 选项 ≥4），
 *  但**排除选座位形态**（`option_seats` 非空 = 牌桌上点武将，不是窗内列表）。 */
function isChoiceStep(pending) {
  if (!pending || pending.kind !== "SELECT_OPTION") return false;
  if (pending.option_seats && Object.keys(pending.option_seats).length) return false;
  const options = (pending.legal_actions || []).filter((action) => action.kind === "SelectOption");
  return hasServerOptionLabels(pending) || options.length >= 4;
}

/** 载荷里的选项展示标签是否可用（`option_labels` 是服务端派生的 `{值: {name, symbol, color}}`）。 */
function hasServerOptionLabels(pending) {
  const labels = pending && pending.option_labels;
  return !!labels && typeof labels === "object" && Object.keys(labels).length > 0;
}

/** 未知区域模板认得的区域（D3）：他人手牌 / 装备 / 判定三区。
 *  自己的手牌与处理区（五谷亮牌）不在此列——前者是牌桌表面，后者是亮牌模板（票 11）。 */
const UNKNOWN_ZONE_NAMES = Object.freeze(["hand", "equip", "judge"]);

/** 未知区域模板的**唯一判据**（票 08 的盲挑形状 → 票 09 推广到全部"挑别人一张牌"的步骤）。
 *
 * 认的是**一副形状**而不是技能 id，故反馈 / 过河拆桥 / 顺手牵羊 / 寒冰剑 / 反间盲挑
 * 共用同一份渲染（`renderUnknownZone`），不必逐个接线。形状全部读载荷：
 *
 *  - `SELECT_CARDS`，且 `spec.zones` 非空、只由**逐座位的三区**（手牌 / 装备 / 判定）组成；
 *  - **必须提供手牌区**：`spec.zones` 含 `hand`。这一条把"另一名角色的可见装备区"
 *    （【麒麟弓】= 只有 `equip`）留在牌桌表面（D2），也把【五谷】的 `processing` 排除；
 *  - 槽位**全来自同一个别的座位**（`ref.seat` 一致且 ≠ `actor`）——挑自己的牌（制衡 / 仁德 /
 *    青囊 / 结姻 / 离间 / 流离 / 雌雄 / 贯石斧 / 青龙）因此不进来；
 *  - 没有 `revealed`（【遗计】观看牌堆顶的亮牌）、没有 `provide_type`（"提供一张 B"）、
 *    没有排列规格——这三类各有自己的表面（票 11 / 12 / 14）。
 *
 * **不要求"至少有一个隐藏槽"是有意的**：来源手牌为空时反馈 / 拆 / 顺只剩可见的装备 /
 * 判定槽，仍是同一步、同一个模板（`renderUnknownZone` 只按实际给的区域画块）。判据只看
 * 公开形状，不写技能 id、不读 `actor` 是不是人类（`syncDialog` 另问 `isOwnPending`）。
 */
function isUnknownZoneStep(pending) {
  if (!pending || pending.kind !== "SELECT_CARDS") return false;
  const slots = pending.slots || [];
  if (!slots.length) return false;
  if (pending.actor === null || pending.actor === undefined) return false;
  // 亮牌（遗计 / 观看牌堆顶）与"提供一张 B"各有自己的模板，绝不抢。
  if ((pending.revealed || []).length) return false;
  if (pending.provide_type) return false;
  if (pending.arrange) return false;
  const zones = (pending.spec && pending.spec.zones) || [];
  if (!zones.length) return false;
  if (!zones.every((zone) => UNKNOWN_ZONE_NAMES.includes(zone))) return false;
  if (!zones.includes("hand")) return false;
  const actor = Number(pending.actor);
  let seat = null;
  for (const slot of slots) {
    const ref = slot && slot.ref;
    if (!ref || ref.seat === null || ref.seat === undefined) return false;
    if (!UNKNOWN_ZONE_NAMES.includes(ref.zone)) return false;
    const at = Number(ref.seat);
    if (seat === null) seat = at;
    else if (at !== seat) return false;
  }
  return seat !== null && seat !== actor;
}

/** 五谷结算帧还在、且还有当前目标：`pending.harvest` 的**活跃判据**（票 11 / D4）。
 *
 * 只认服务端派生的字段（`active` / `revealed` / `current`），不看询问种类；亮牌步与
 * 无懈窗口共用它。`revealed` 为空或没有 `current` ⇒ 结算末尾 / 没亮牌，不算活跃。
 */
function isHarvestActive(pending) {
  const harvest = pending && pending.harvest;
  if (!harvest || typeof harvest !== "object" || harvest.active === false) return false;
  if (!Array.isArray(harvest.revealed) || !harvest.revealed.length) return false;
  return harvest.current !== null && harvest.current !== undefined;
}

/** 亮牌模板的**唯一判据**（票 11 五谷；票 12 的遗计两牌亮牌复用同一渲染器）。
 *
 * 两个来源，都是**载荷**而不是技能 id：
 *
 *  - ``pending.harvest``：服务端为五谷派生的结算状态（U2-11 / D10）。它只在五谷的
 *    ``SELECT_CARDS`` 取牌步骤上开窗（``spec.zones`` 含 ``processing`` 或槽位全在处理区），
 *    且必须还有**当前目标**（``harvest.current`` 非空）——结算末尾没有当前目标时不开。
 *  - ``pending.dialog === "reveal"``：服务端给**观看牌面**的选牌步打上的**显式模板标记**
 *    （U2-12：【遗计】的牌堆顶两张没有五谷的 target 列表；``projection.pending_payload``
 *    以``SELECT_CARDS`` + 授予牌面这个**形状**派生，前端不认技能 id）。
 *
 * 判据只看"这一步是不是亮牌面"，不看 actor 是不是人类（`syncDialog` 另问 `isOwnPending`；
 * 五谷的 AI 拿牌阶段要显示在人类屏幕上，见 `syncDialog` 的 harvest 例外）。
 */
function isRevealStep(pending) {
  if (!pending) return false;
  // A1：洛神连续判定的继续询问 = 亮牌弹窗；`final:true` 是关窗帧，不画。
  if (pending.judgment_reveal) {
    const cards = pending.judgment_reveal.cards;
    return pending.kind === "SELECT_OPTION"
      && pending.judgment_reveal.final !== true
      && Array.isArray(cards) && cards.length > 0;
  }
  if (pending.kind !== "SELECT_CARDS") return false;
  if (pending.dialog === DIALOG_KINDS.reveal) return true;
  if (!isHarvestActive(pending)) return false;
  const zones = (pending.spec && pending.spec.zones) || [];
  if (zones.includes("processing")) return true;
  const slots = pending.slots || [];
  return slots.length > 0 && slots.every((slot) => slot && slot.ref && slot.ref.zone === "processing");
}

/** 五谷整段结算的**持久展示**（review-fix / D4）：这一步的表面不是弹窗（无懈 / 反无懈的
 *  `RESPOND` 是**牌桌表面**），但结算帧还活着、还有当前目标 ⇒ 亮牌 + 目标条继续显示。
 *
 * 与 :func:`isRevealStep` 分开是**只读判据的要求**：`stepDialogKind` 仍只看这一步的表面，
 * 无懈窗口因此照常可出牌 / 放弃；持久展示不进 `selectionEnabled()`。返回 `null` = 不需要
 * 持久展示（正常取牌步由 `isRevealStep` 走弹窗的既有生命周期）。
 */
function persistentRevealPending(pending) {
  if (!isHarvestActive(pending)) return null;
  if (stepDialogKind(pending) !== null) return null;
  return pending;
}

/** 只读态判据（spec D8）：当前步骤的表面是弹窗 ⇒ 牌桌选择入口一律 no-op。
 *
 * 与既有的 `state.playback` 判据同型，但只回答"现在能不能选"：检视（悬停 / 右键详情 /
 * 折叠 / 顶栏 / 事件流）与操作（提示条右端的四键）都不走这里。所有选择入口
 * （点手牌 / 点座位 / 点槽位 / 提供 B / 弃牌 / 预发动选牌选座）都先问它一次；
 * 键盘回车 / 空格触发的 click 走同一批函数，故同一判据。
 * 折叠（`state.dialog.collapsed`）只改遮挡，不改只读态——判据只看这一步的表面。
 */
function selectionEnabled(view = state.view) {
  if (state.playback) return false;
  return stepDialogKind(view ? view.pending : null) === null;
}

/** 当前是否需要询问弹窗：本地态（预发动清单）优先，其次看这一步的表面，最后才是
 *  五谷的**持久展示**（表面是牌桌、但结算帧还活着）。`null` = 不需要。 */
function dialogWantedKind(pending) {
  if (state.preactivation) return "preactivation";
  const stepKind = stepDialogKind(pending);
  if (stepKind !== null) return stepKind;
  return persistentRevealPending(pending) ? DIALOG_KINDS.reveal : null;
}

/** 弹窗实例键：**同一键 = 同一「步」的重复渲染**（保留折叠、只换内容）；
 *  换键 = 新的一问（关旧开新、清正文、折叠复位）。
 *
 * 排列按询问序号；预发动按询问序号 + 技能（同一问里换步骤不清窗口）。
 *
 * **两步链（票 08 / 反间：花色 → 盲挑）**：这是同一条技能链上的两个**服务端驱动**询问，
 * 但 `seq` 每步都会变，`kind` 也不同（choice → unknown）。按 `${kind}:${seq}` 建键会把
 * 它们拆成"关旧开新"两次生命周期，屏幕上等于闪一下牌桌步。故这两个模板改用**链键**：
 * 服务端下发的 `context.name`（技能展示名）+ 应答者；同一技能的同一次询问链因此共用
 * 一个窗口，只换正文 / 标题 / 步骤提示。`context` 缺失（409 的裸 Query / 合成载荷）时
 * 退回 `kind:seq`（fail-visible，不猜链）。
 *
 * **不要**把折叠状态写进键。
 */
function dialogKeyFor(kind, pending) {
  const seq = pending && pending.seq !== undefined && pending.seq !== null ? pending.seq : "";
  if (kind === "preactivation") {
    const pa = state.preactivation;
    const skill = pa ? (pa.skillId || pa.cardId || "") : "";
    return `preactivation:${seq}:${skill}`;
  }
  if (kind === DIALOG_KINDS.choice || kind === DIALOG_KINDS.unknown) {
    const chain = pending && pending.context && pending.context.name;
    if (chain) return `chain:${chain}:${pending.actor ?? ""}`;
  }
  // 亮牌（票 11 / 12）：五谷的整段结算跨 X 个目标、每个目标一个新 `seq`，按 `${kind}:${seq}`
  // 会每拿一张就关窗重开。链键取**服务端派生**的 `harvest.key`（帧 + 牌 + 使用者），
  // 整段结算不变；没有它（合成载荷 / 非五谷亮牌）才退回 `kind:seq`，fail-visible 不猜链。
  if (kind === DIALOG_KINDS.reveal) {
    // A1：洛神连续判定用服务端给出的循环键；同一循环内的每张判定牌共用这个窗口。
    const chain = pending && pending.judgment_reveal && pending.judgment_reveal.chain_key;
    if (chain) return `judge-reveal:${chain}`;
    const key = pending && pending.harvest && pending.harvest.key;
    if (key) return `reveal:${key}`;
  }
  return `${kind}:${seq}`;
}

/** 标题：模板自报；票 08 / 09 / 11 / 12 接进来时换成各自模板的文案。
 *
 * 亮牌模板（票 11 / 12）的标题读**载荷**：五谷是 `harvest.title`（服务端从使用的牌面翻成
 * 「五谷丰登」），遗计是 `context.name`（服务端从技能面翻成「遗计」）；前端不抄牌名 / 技能名。
 * 两者都没有时退回中性的「亮牌」。 */
function dialogTitleFor(kind, pending) {
  if (kind === "preactivation") {
    const pa = state.preactivation;
    return pa ? `预发动【${pa.name}】` : "预发动";
  }
  if (kind === DIALOG_KINDS.arrange) return "排列";
  if (kind === DIALOG_KINDS.choice) return "选择";
  if (kind === DIALOG_KINDS.unknown) return "选择一张牌";
  if (kind === DIALOG_KINDS.reveal) {
    const title = pending && pending.harvest && pending.harvest.title;
    const contextName = pending && pending.context && pending.context.name;
    return title || contextName || "亮牌";
  }
  return "询问";
}

/* ------------------------------------------------------------ 暗化层 / 显隐 */

/** 把提示区的 `#t-options` 搬回浮动选项行（真 DOM 的 appendChild = 移动）。 */
function showOptionsHome() {
  const home = $("t-options-home");
  const options = $("t-options");
  if (home && options) home.appendChild(options);
}

/** 武将详情窗口是否开着：真源是 `state.modal.heroSeat`（不是 DOM 的 hidden 属性）。
 *
 * 开窗判据与 `syncHeroModal` 同一条（无视图时它不显示；牌桌事件回放不阻止），故这里也读
 * 同一组条件；`#t-modal.hidden` 只是这条状态的渲染结果，不能反过来当判据（评审 Standards：
 * DOM 读法）。
 */
function heroDetailVisible() {
  // 牌桌事件回放（`state.playback`）不阻止打开：`renderHeroDetailBody` 读同一个
  // `tableViewForRender()`。这里只排除没有视图的首页。
  return state.modal.heroSeat !== null && !!state.view;
}

/** 暗化层 / 页面态的唯一开关：询问弹窗**展开**或武将详情打开时显示。
 *
 * 折叠只改遮挡 ⇒ 只剩标题条时暗化层一并收起（牌桌重新亮起来，但仍是只读）；
 * 详情叠在弹窗上时保持暗化。遮罩没有 click 监听（D8：点背景不是关闭出口）。
 * **五谷持久展示不暗化**：这一步的表面是牌桌（无懈 / 反无懈的 RESPOND），界面必须
 * 看起来可操作——亮牌窗只占中央，不宣称"现在不能选"。
 */
function syncModalChrome() {
  const dialog = $("t-dialog");
  const occluding = (!!dialog && dialog.hidden === false && !state.dialog.collapsed
      && !state.dialog.persistent)
    || heroDetailVisible();
  const backdrop = $("t-modal-backdrop");
  if (backdrop) backdrop.hidden = !occluding;
  const shell = $("table-shell");
  if (shell) shell.classList.toggle("modal-open", occluding);
}

/* ------------------------------------------------------------ 折叠 */

/** 五谷持久展示的专用正文节点（`#t-dialog-reveal`，静态标记在 index.html）。
 *
 * 它**不在** `#t-dialog-body` 里：持久展示时正文槽整个收起，`#t-options` 留在提示区
 * （无懈窗口的选项 / 手牌选择用），亮牌内容只画在这个节点里。 */
function dialogRevealNode() {
  return $("t-dialog-reveal");
}

/** 清空持久亮牌节点并收起（关闭 / 切回正常弹窗内容时调用）。 */
function clearDialogReveal() {
  const node = dialogRevealNode();
  if (!node) return;
  node.innerHTML = "";
  node.hidden = true;
}

/** 这一屏是不是**持久亮牌正文**：正文槽整个收起、折叠只隐藏亮牌节点。
 *
 * 两个条件缺一不可，判据在这里只写一次（`applyDialogCollapseChrome` 与
 * `applyDialogContentMode` 都读它）：
 *
 * - `state.dialog.persistent` = **五谷的结算帧还活着**——结算期间表面会在牌桌与取牌步
 *   之间来回换，这个标记只说明"帧还在"，不说明"这一屏在放亮牌"；
 * - `state.dialog.kind === reveal` = **这一屏真的是亮牌**。
 *
 * 只看前者（票 23 报障）：五谷取牌之后紧跟【清俭】的"是否发动"，载荷里 `harvest` 仍在
 * ⇒ `persistent` 被算成 true ⇒ 玩家点「进入预发动」后弹窗换了标题，正文槽却仍按亮牌
 * 模式整体隐藏：步骤条与「下一步」全在看不见的正文里，屏幕上只剩空白大白框 + 灰按钮。
 */
function dialogRevealMode() {
  return state.dialog.persistent && state.dialog.kind === DIALOG_KINDS.reveal;
}

/** 折叠按钮 / 重开入口的文案与可用态（只读判据不在这里）。
 *
 *  两种正文二选一：普通模式 = `#t-dialog-body`（折叠只隐藏它）；持久展示 =
 *  `#t-dialog-reveal`（正文槽收起，折叠隐藏亮牌节点）。 */
function applyDialogCollapseChrome() {
  const body = $("t-dialog-body");
  const reveal = dialogRevealNode();
  const button = $("t-dialog-collapse");
  if (dialogRevealMode()) {
    if (body) body.hidden = true;
    if (reveal) reveal.hidden = state.dialog.collapsed;
  } else {
    if (body) body.hidden = state.dialog.collapsed;
    if (reveal) reveal.hidden = true;
  }
  if (button) {
    button.textContent = state.dialog.collapsed ? "展开" : "收起";
    button.setAttribute("aria-expanded", String(!state.dialog.collapsed));
  }
  // 排列保留既有的「查看排列」收起态入口（提示条右端）；折叠后点了就是展开。
  showReopenButton(
    state.dialog.kind,
    state.dialog.collapsed && state.dialog.kind === DIALOG_KINDS.arrange,
  );
}

/** 弹窗正文的归属：**正常内容**（`#t-options` 搬进正文槽）还是**五谷持久展示**
 *  （亮牌 + 目标条画进 `#t-dialog-reveal`，`#t-options` 留在提示区）。
 *
 *  正常模式不在这里重画内容——`renderPending` 的渲染器已经把内容写进 `#t-options`，
 *  这里只决定它挂在哪。持久模式则每帧重画一次：目标条 / 置灰要跟着结算走。
 *  步骤提示在持久模式读亮牌自己的文案（不覆盖提示条里无懈窗口的提示）。
 */
function applyDialogContentMode(kind) {
  const body = $("t-dialog-body");
  const options = $("t-options");
  const reveal = dialogRevealNode();
  // 与 `applyDialogCollapseChrome` **同一条判据**（票 23：两处各算一遍就会一边藏正文、
  // 一边以为还在放亮牌）。这里的 `kind` 是本次要应用的正文种类，`state.dialog.kind`
  // 在 `openDialogDom` / `updateDialogDom` 里已先写好，故两者一致。
  const persistent = dialogRevealMode() && kind === DIALOG_KINDS.reveal;
  const pending = state.view ? state.view.pending : null;
  state.dialog.hintOverride = null;
  if (persistent && pending) {
    showOptionsHome();
    clearDialogReveal();
    if (reveal) {
      reveal.hidden = state.dialog.collapsed;
      renderReveal(pending, {
        container: reveal,
        interactive: false,
        promptHint: false,
        bodyClass: false,
      });
    }
    state.dialog.hintOverride = harvestWaitHint(pending);
    return;
  }
  clearDialogReveal();
  if (options && body && options.parentNode !== body) body.appendChild(options);
}

/** 折叠 / 展开：**只**隐藏正文，不动只读判据、不动任何选择态、不提交。 */
function setDialogCollapsed(collapsed) {
  if (!state.dialog.open) return;
  state.dialog.collapsed = Boolean(collapsed);
  applyDialogCollapseChrome();
  syncModalChrome();
}

function toggleDialogCollapse() {
  if (!state.dialog.open) return;
  setDialogCollapsed(!state.dialog.collapsed);
}

/** 「查看排列」入口 = 展开（不是关闭 / 重开一个本地态）。 */
function expandDialog() {
  if (!state.dialog.open || !state.dialog.collapsed) return;
  setDialogCollapsed(false);
}

/** 强制选择被收起后，右端出现重开入口；询问弹窗默认用它自己的「展开」。 */
function showReopenButton(kind, visible) {
  const reopen = $("btn-show-modal");
  if (!reopen) return;
  reopen.hidden = !visible;
  if (visible) reopen.textContent = kind === DIALOG_KINDS.arrange ? "查看排列" : "继续";
}

/* ------------------------------------------------------------ 询问弹窗 */

/** 步骤提示：模板把"现在要做什么"写在提示条的 `#t-target-hint`；折叠后它必须还在，
 *  所以这里镜像一份到标题条。空文本时整个提示隐藏（"可选"的步骤提示）。
 *  五谷的持久展示例外：那一步的提示条属于无懈窗口，亮牌窗读自己的提示
 *  （`state.dialog.hintOverride`），不抢提示条的文案。 */
function syncDialogHint() {
  const hint = $("t-dialog-hint");
  if (!hint) return;
  const source = $("t-target-hint");
  const text = state.dialog.hintOverride !== null
    ? state.dialog.hintOverride
    : (source ? source.textContent : "");
  hint.textContent = text;
  hint.hidden = !text;
}

/** 打开询问弹窗：标题 + 正文节点 + 暗化层都只在这里写一次。
 *
 * 正文归属由 :func:`applyDialogContentMode` 决定：普通模板把提示区的 `#t-options`
 * 整体搬进内容槽（内容仍由各自的渲染器画）；五谷持久展示则画进 `#t-dialog-reveal`、
 * `#t-options` 留在提示区。打开总是展开态（折叠复位）——由 `syncDialog()` 在"换问"时
 * 调用，不会抹掉同一问的折叠。
 */
function openDialogDom(kind) {
  const dialog = $("t-dialog");
  const body = $("t-dialog-body");
  const title = $("t-dialog-title");
  if (!dialog || !body) return;
  if (title) title.textContent = dialogTitleFor(kind, state.view ? state.view.pending : null);
  state.dialog.open = true;
  state.dialog.kind = kind;
  state.dialog.collapsed = false;
  state.dialog.hintOverride = null;
  dialog.hidden = false;
  applyDialogContentMode(kind);
  applyDialogCollapseChrome();
  syncDialogHint();
  syncModalChrome();
}

/** 同一问的重复渲染（或两步链的下一步）：更新标题 / 步骤提示 / 正文归属，**保留折叠**。
 *
 * 不清正文：内容由 `renderPending` 的渲染器就地重画（`#t-options` 是共享节点）；
 * 清正文只发生在关闭（换问 / 表面不再是弹窗）时，见 :func:`closeDialogDom`。
 * 两步链会在这里从 `choice` 切到 `unknown`，故 `state.dialog.kind` 也要跟着换——
 * 它决定折叠按钮旁边的重开文案，不能停在上一模板的种类上。
 * 五谷的持久展示（RESPOND）↔ 正常取牌步也走这里：**键相同**（`reveal:${harvest.key}`），
 * 只换正文归属，窗口不闪。
 */
function updateDialogDom(kind) {
  const dialog = $("t-dialog");
  const title = $("t-dialog-title");
  if (!dialog || dialog.hidden) {
    openDialogDom(kind);
    return;
  }
  if (title) title.textContent = dialogTitleFor(kind, state.view ? state.view.pending : null);
  state.dialog.kind = kind;
  applyDialogContentMode(kind);
  applyDialogCollapseChrome();
  syncDialogHint();
  syncModalChrome();
}

/** 关闭询问弹窗：隐藏窗口、清空正文、把 `#t-options` 送回提示区、折叠复位。
 *
 * **清正文是生命周期的一部分**：跨步骤后重新打开时不能留下上一问的内容。
 */
function closeDialogDom() {
  const dialog = $("t-dialog");
  const body = $("t-dialog-body");
  const title = $("t-dialog-title");
  const hint = $("t-dialog-hint");
  if (dialog) dialog.hidden = true;
  showOptionsHome();
  clearDialogReveal();
  if (body) {
    body.innerHTML = "";
    body.scrollTop = 0;
    body.hidden = false;
    body.classList.remove("reveal-body");
  }
  if (title) title.textContent = "";
  if (hint) {
    hint.textContent = "";
    hint.hidden = true;
  }
  state.dialog.open = false;
  state.dialog.collapsed = false;
  state.dialog.kind = null;
  state.dialog.key = null;
  state.dialog.persistent = false;
  state.dialog.hintOverride = null;
  showReopenButton(null, false);
  syncModalChrome();
}

/** 在每次 `renderPending` 末尾调用：按"当前步骤的表面"决定开 / 关 / 换内容。
 *
 * - 表面不是弹窗、也没有五谷的持久展示（且没有预发动本地态）⇒ 关；
 * - 同一问的重复渲染（键相同）⇒ 只更新正文 / 提示，保留折叠；
 * - 换问（键不同）⇒ 先关旧（清正文、折叠复位），再开新。
 *
 * 五谷整段结算跨两类步骤：取牌步是弹窗表面（正常亮牌窗），无懈 / 反无懈的 `RESPOND`
 * 是**牌桌表面**（`persistentRevealPending`）。两类的键同为 `reveal:${harvest.key}`，
 * 故窗口不关，只切换正文归属；`RESPOND` 期间 `selectionEnabled()` 仍看 `stepDialogKind`
 * （= null）⇒ 无懈照常可出牌 / 放弃。
 *
 * 末尾顺带刷新一次叠加的武将详情（开着的才画）：它独立于询问弹窗，但不该在轮询 / 刷新时
 * 停在旧数据上；同座位重画保留滚动位置（U1-06）。
 */
function syncDialog() {
  const pending = state.view ? state.view.pending : null;
  // 只有**自己的**询问才在自己的屏幕上开弹窗：目标座位是 AI 时（人类是周瑜、目标是 AI），
  // 花色 / 盲挑这两问会出现在视图里，但它们不是给人类答的——不在这里开一个人工弹窗
  // （牌桌仍按 `selectionEnabled` 只读，等 AI 答完下一步）。
  //
  // **五谷例外**（票 11 / D4）：亮牌是公开处理区，AI 目标拿牌时弹窗也要留在人类屏幕上。
  // 判据只认服务端派生的 `harvest`（不是"所有 reveal"）——遗计（票 12）的观看是私有授予，
  // 仍然只在本人应答时开窗。持久展示（无懈窗口）同样对两个座位都显示：亮牌是公开事实。
  const own = pending ? isOwnPending(pending) : false;
  const persistent = persistentRevealPending(pending) !== null;
  const harvestStepped = !!pending && isHarvestActive(pending)
    && stepDialogKind(pending) === DIALOG_KINDS.reveal;
  const publicHarvest = harvestStepped || persistent;
  const kind = state.playback || (!own && !publicHarvest) ? null : dialogWantedKind(pending);
  if (!kind) {
    closeDialogDom();
    syncHeroModal();
    return;
  }
  const key = dialogKeyFor(kind, pending);
  if (key !== state.dialog.key || !state.dialog.open) {
    closeDialogDom();
    state.dialog.key = key;
    state.dialog.persistent = persistent;
    openDialogDom(kind);
    syncHeroModal();
    return;
  }
  state.dialog.persistent = persistent;
  updateDialogDom(kind);
  syncHeroModal();
}

/* ------------------------------------------------------------ 武将详情窗口 */

/** 武将详情 = 叠加的检视窗：只按 `state.modal.heroSeat` 开 / 关 `#t-modal`。
 *  它不碰询问弹窗的正文、折叠与只读判据；关闭后底下的询问弹窗原样恢复。 */
function syncHeroModal() {
  const modal = $("t-modal");
  if (!modal) return;
  // 与 renderTable 走同一份视图：牌桌事件回放期间是 live view；回放页 / 首页没有
  // 武将详情数据，仍收起。
  const view = tableViewForRender();
  if (state.modal.heroSeat === null || !view) {
    modal.hidden = true;
    const body = $("t-modal-body");
    if (body) {
      body.innerHTML = "";
      body.scrollTop = 0;
    }
    syncModalChrome();
    return;
  }
  const title = $("t-modal-title");
  const player = playerAt(view, state.modal.heroSeat);
  if (title) title.textContent = player ? `武将详情 · ${heroNameOf(player, view)}` : "武将详情";
  renderHeroDetailBody();
  modal.hidden = false;
  syncModalChrome();
}

/* ------------------------------------------------------------ 键盘 / 切页 / 装配 */

/** document 的 keydown 入口：ESC **只**关武将详情（检视窗）。
 *
 * 询问弹窗没有 ESC 出口（D3）：没开详情时这里什么都不做——不是"先 preventDefault 再
 * 找不到可关的东西"，ESC 在询问弹窗上就是 no-op。 */
function handleModalKey(event) {
  if (!event || (event.key !== "Escape" && event.key !== "Esc")) return;
  if (state.modal.heroSeat === null) return;
  if (event.preventDefault) event.preventDefault();
  closeHeroDetail();
}

/** 离开牌桌页 / 回放期间：收起两个窗口（不提交任何 action）。 */
function closeModalForView() {
  closeDialogDom();
  state.modal.heroSeat = null;
  syncHeroModal();
}

function wireModal() {
  // 遮罩**不接 click**：`pointer-events: none` 的暗化层没有自己的点击，穿透到牌桌的
  // 点击由 `selectionEnabled()` 判据拦下（D8：「点背景 = 关闭」不存在）。
  const collapse = $("t-dialog-collapse");
  if (collapse) collapse.addEventListener("click", toggleDialogCollapse);
  // 「查看排列」= 展开折叠的排列弹窗（不是重新开一个本地态，也不清已摆的顺序）。
  const reopen = $("btn-show-modal");
  if (reopen) reopen.addEventListener("click", expandDialog);
  // 武将详情自己的关闭按钮：只关细节，不动询问弹窗。
  const close = $("t-modal-close");
  if (close) close.addEventListener("click", closeHeroDetail);
  document.addEventListener("keydown", handleModalKey);
  // 初始两个窗口都收起（真浏览器由 HTML 的 hidden 属性保证；DOM 垫片不解析属性）。
  closeDialogDom();
  syncHeroModal();
}
