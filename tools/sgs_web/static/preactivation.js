/* SGS 前端 · 预发动：清单驱动的技能步骤与转化技来源模式。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/** 这条询问对应的、**已翻预发动**的技能条目；`null` = 未翻 / 不是这个技能的问。
 *
 * 只看技能声明上的开关（`skills[*].preactivated`），**不**看清单是否渲得出来：清单为空
 * 时该技能此刻不可用（清单入口不给点），但这一问仍是它的"是否发动"——界面要把它画成
 * "只能放弃"，而不是退回"发动技能 XXX"的骨架候选。
 */
function preactivatedSkill(pending) {
  if (!pending || pending.kind !== "SKILL_ACTIVATION") return null;
  return (((state.view && state.view.skills) || []).find(
    (item) => item.skill_id === pending.skill_id && item.preactivated,
  ) || null);
}

/** 被动入口的"放弃"：预发动清单已经下发、但玩家还没进入本地态。 */
function isPassivePreativation(pending) {
  return preactivatedSkill(pending) !== null;
}

/** 进入 / 退出转化技模式（【龙胆】：点技能 → 点一张可转化的手牌 → 照常选目标提交）。
 *
 * **宽松保留**（W4-2 / `spec.md` §1.5 的重选规则）：切换模式**不**清空已选的东西——
 * 已选源牌 / 焦点仍在当前模式的合法集合里就保留，不合法才清空。预发动中途切模式也不
 * 撤销清单（只是把不再合法的已选答案剔掉）。
 */
function toggleConvertSkill(skill) {
  if (state.playback) return;
  const next = state.convertSkill === skill.skill_id ? null : skill.skill_id;
  state.convertSkill = next;
  const pending = state.view ? state.view.pending : null;
  if (state.preactivation) {
    prunePreativationAnswers(state.preactivation, pending);
    state.lastRejectReason = "";
    schedulePreativationPreview();
    renderTable();
    renderPending(state.view);
    return;
  }
  if (pending) {
    state.picks = state.picks.filter((index) => providePickStays(pending, index, next));
    if (state.focusCardId !== null && !cardSelectable(pending, state.focusCardId)) {
      state.focusCardId = null;
      state.selectedTargets = [];
    }
  } else {
    state.picks = [];
    state.focusCardId = null;
    state.selectedTargets = [];
  }
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 这个面内位置在**新转化模式**下还合法吗（`mode = null` = 不限定方向）。
 *
 * 实体 B 不属于任何转化方向 ⇒ 恒合法；虚拟 B 只有在 `virtual_index` 把它归给 `mode`
 * 时才合法（服务端按来源谓词逐张归属，界面只读那份数据）。
 *
 * **"虚拟牌"的判据读序列化后仍在的那个字段**：`CardRef.is_virtual` 是 pydantic 的
 * `@property`，**不进** `model_dump`——界面收到的牌面里没有它。故这里用
 * `virtual_sources`（实体牌恒为空元组、转化牌恒非空）判，"读一个永远 undefined 的键"
 * 会让这一支恒真、切换模式永远不清空（`CODING_STANDARDS.md` §1 的读取方纪律）。
 */
function providePickStays(pending, index, mode) {
  if (mode === null) return true;
  const slot = ((pending && pending.slots) || [])[index];
  if (!slot || !slot.card) return false;
  const sources = slot.card.virtual_sources || [];
  if (!sources.length) return true;
  if (sources.length !== 1) return false;
  return cardAliases(pending, sources[0]).some(
    (alias) => String(alias.card_id) === String(slot.card.card_id)
      && (alias.skill_ids || []).includes(mode),
  );
}

/** 切换转化模式后按**宽松版**收窄清单里的已选答案：仍合法则保留、不合法才清空。 */
function prunePreativationAnswers(pa, pending) {
  pa.steps.forEach((step, index) => {
    const answer = pa.answers[index];
    if (!answer || !answer.values.length) return;
    const options = preactivationOptions(pa, index);
    if (options === null || options === undefined) return; // 退化清单：全都点得动
    const blocked = preactivationDisabled(pa, index) || [];
    const kept = answer.values.filter(
      (value) => options.includes(String(value)) && !blocked.includes(String(value)),
    );
    answer.values = kept;
    answer.texts = preactivationAnswerTexts(step, kept);
  });
}

/** 选项步超过这么多项就**多列紧凑铺**（票 04 复验：蛊惑的牌名有十几个，单列要滚动）。
 *
 *  阈值只与**候选个数**有关，不认技能名：少选项的技能（三两下就点完的那种）保持原来的
 *  单列按钮形态，只有"长清单"才换成网格。
 */
const OPTION_COMPACT_MIN = 6;

/* ------------------------------------------------------------ 预发动（W4-1） */

/** 一条技能此刻的预发动清单（票 01 / 02）：**逐候选**表优先，单份 / 单技能表兜底。
 *
 * 四种载体，读取顺序就是"离这一问最近的那一份"：
 *
 *  1. `pending.activation_preactivations[skill_id]` —— **逐候选**清单（票 01 的
 *     `SkillActivationQuery.activation_preactivations`；票 02 的读取面）。同一时机的
 *     N ≥ 2 条技能候选各有自己的一份，界面按**选中的那条候选**取；单候选面上它与
 *     第 3 条同源，故旧面的渲染结果一字不变。
 *  2. `pending.preactivations[skill_id]` —— 主动入口的表（出牌阶段 / 转化技，W4-1）。
 *  3. `pending.preactivation` —— 被动入口的**单份**清单（W4-1；票 01 起它只是**代表
 *     候选**那一份，多候选面上不能拿它替代第 1 条）。
 *  4. `null` —— 这一问没有这个技能的清单。
 *
 * 载荷键与询问模型字段同名（``preactivations`` / ``preactivation`` /
 * ``activation_preactivations``，W5-00 统一拼写）；服务端 `pending_payload` 下发同样的键。
 * 这条跨线契约由 `tests/test_web.py` 与 `tests/support/ui_smoke.mjs` 两侧各钉一次。
 */
function preactivationManifest(pending, skillId) {
  if (!pending) return null;
  const perCandidate = pending.activation_preactivations || {};
  if (perCandidate[skillId] && perCandidate[skillId].length) return perCandidate[skillId];
  const table = pending.preactivations || {};
  if (table[skillId] && table[skillId].length) return table[skillId];
  if (
    pending.kind === "SKILL_ACTIVATION"
    && pending.skill_id === skillId
    && pending.preactivation
    && pending.preactivation.length
  ) return pending.preactivation;
  return null;
}

/** 这张手牌此刻的**两步预发动清单**（W5-02b 借刀杀人；`null` = 无）。
 *
 * 载体 = `pending.card_preactivations[card.type]`（键是**卡牌类型 id**，如 `borrowed_sword`；
 * 两张借刀同型）。它由服务端从"附带指定池"派生并**原样下发**，界面不重算池。
 *
 * 与技能清单的区别只在**提交形状**：技能清单确认成 `ActivateSkill`，这张表确认成
 * `UseCard(card, targets=(A,), designations=(Bn,))`——故两步共用同一个步骤渲染器。
 */
function cardPreactivationManifest(pending, card) {
  if (!pending || !card) return null;
  const table = pending.card_preactivations || {};
  const steps = table[card.type];
  return steps && steps.length ? steps : null;
}

/** 这一步选什么：`card` = 区域里的牌；`seat` = `seat:N` 闭集；`option` = 其余闭集文本。
 *
 * **类型是服务端下发的规则级事实**（`Step.kind`，清单渲染器按步骤**声明类型**填），界面
 * 不按载荷形状反推（`CODING_STANDARDS.md` §1：字段的读取方要读对）。反例（票 18 的评审
 * Fix 2）：目标由牌名决定的清单里，同一个座位步有一行是"没有可选目标"
 * （`options = []`、`max = 0`）；旧实现拿 `pool[0].options[0]` 猜类型，这一行排在**最前**
 * 时整步被读成选项步 ⇒ :func:`preactivationNoChoiceStep` 恒假、空步不会被跳过，玩家卡在
 * 一步点不动的东西上——正是票 18 要消掉的那条死路。
 *
 * 退路只给**旧载荷**（缺 `kind` 的合成夹具 / 退化清单）：判据点看**全部行**而不是第一行，
 * 任一行有 `seat:N` 即座位步；一行候选都没有时按"非选牌步的默认真实形态"判成座位步
 * （选项步的候选是常量、不会全空；`pool = []` 的退化座位步也走这里）。
 */
function preactivationStepKind(step) {
  if (step.kind === "card" || step.kind === "seat" || step.kind === "option") {
    return step.kind;
  }
  if (step.zones && step.zones.length) return "card";
  if (!step.pool || !step.pool.length) return "seat";
  let sawOption = false;
  for (const row of step.pool) {
    for (const option of (row.options || [])) {
      if (typeof option !== "string") continue;
      if (option.startsWith(SEAT_PREFIX)) return "seat";
      sawOption = true;
    }
  }
  return sawOption ? "option" : "seat";
}

/** 命中**前序答案**的那一行池：先找精确匹配 `when` 的，其次 `when=()` 的（与前序无关）。
 *  `null` = 服务端退化掉了候选索引（或本步没有池）。 */
function preactivationPoolRow(pa, index) {
  const step = pa && pa.steps[index];
  if (!step || !step.pool || !step.pool.length) return null;
  const prior = [];
  for (let i = 0; i < index; i += 1) prior.push(...(pa.answers[i] ? pa.answers[i].texts : []));
  const exact = step.pool.find(
    (row) => row.when.length === prior.length && row.when.every((value, at) => value === prior[at]),
  );
  const any = step.pool.find((row) => row.when.length === 0);
  return exact || any || null;
}

/** 一步的候选：**先读步骤预览回包**（服务端现算），再退回清单里的静态池行。
 *
 *  预览（票 11）是"这一刻能点谁"的唯一真源：同一步内的动态约束（【除疠】势力互异、
 *  【业炎】同花色）静态行根本表达不了。`pa.preview` 只在一件事上无效——**失败过**
 *  （`pa.previewError`）：那时既不自动推进也不许提交，候选退回"没有"（fail-closed，
 *  取不到就是点不了，绝不兜底成"全都点得动"，spec §6 #5）。
 *
 *  `null` = 这一步**没有**候选索引：界面必须当成"点不了"。
 */
function preactivationOptions(pa, index) {
  if (pa && pa.preview && !pa.previewError && pa.preview.cursor === index) {
    return (pa.preview.options || []).map(String);
  }
  const row = preactivationPoolRow(pa, index);
  return row ? (row.options || []) : null;
}

/** 本步**显示但不可选**的候选（服务端 `StepPool.disabled` / 预览回包的 `disabled`）。
 *
 *  "列出来"与"能选"是两件事：蛊惑的牌名弹窗要列全牌堆名（用户 Q1A），而"此刻没有合法
 *  目标"的那几个只能画灰。**界面只管画灰**——合法性由服务端校验器裁决（它拒收 disabled
 *  里的项）。`null` = 没有置灰信息（与"空置灰表"同义）。
 */
function preactivationDisabled(pa, index) {
  if (pa && pa.preview && !pa.previewError && pa.preview.cursor === index) {
    return (pa.preview.disabled || []).map(String);
  }
  const row = preactivationPoolRow(pa, index);
  return row ? (row.disabled || []) : null;
}

/**
 * 这一步的**合法来源组合**（huo-mobile-batch 票 10）：`null` = 服务端没下发这一格。
 *
 * 载荷 = `Step.groups`（每个元素是一组能**一起提交**的来源牌 id，服务端从候选池派生）。
 * 缺省 / 空 ⇒ `null`：旧载荷的语义是"**每个组合都合法**"，界面照旧按 `options` 全都点得动。
 * 界面只读这份数据——**不**自己判花色 / 牌名（判据只住在服务端）。
 */
function preactivationGroups(pa, index) {
  const step = pa && pa.steps[index];
  const groups = (step && Array.isArray(step.groups)) ? step.groups : [];
  if (!groups.length) return null;
  return groups.map((group) => (group || []).map(String));
}

/** 已选来源 `values` 是否还能**落进**某一组合法来源（`null` groups ⇒ 恒真）。
 *
 * 子集语义（不是"恰好相等"）：选牌步是多选，挑到一半时已选的那几张必须还能凑进某一组，
 * 否则下一张点什么都救不回来——卡面据此置灰。填满之后的合法性由同一判据复核：`count`
 * 张 ⊆ 组 ⇒ 与组相等（服务端下发的每组都是 `count` 张）。
 */
function preactivationGroupHolds(pa, index, values) {
  const groups = preactivationGroups(pa, index);
  if (groups === null) return true;
  const picked = new Set((values || []).map(String));
  return groups.some((group) => [...picked].every((value) => group.includes(value)));
}

/** 再点一张 `cardId` 之后还能**落进**某组合法来源吗（票 10 §5 的第一条）。
 *
 * 已选中的牌不归这条管（再点 = 取消，恒可点）：调用方先判"是否已选"。 */
function preactivationGroupAccepts(pa, index, values, cardId) {
  return preactivationGroupHolds(pa, index, [...(values || []), cardId]);
}

/** 这一步的来源是否**合法**（票 10）：非选牌步 / 没有 `groups` 的步恒真。 */
function preactivationStepLegal(pa, index) {
  const step = pa && pa.steps[index];
  if (!step || preactivationStepKind(step) !== "card") return true;
  const answer = pa.answers[index];
  return preactivationGroupHolds(pa, index, answer ? answer.values : []);
}

/** 清单里**全部**选牌步的来源是不是都合法（`preactivationReady` 只看填完没有）。
 *
 * 提交 / 下一步 / 提示条都读这一处：陈旧的来源 / 合成载荷也走同一条闸，绝不把注定
 * 400 的组合发出去（票 10 §4 的死路正是这样来的）。 */
function preactivationSourcesLegal(pa) {
  if (!pa) return true;
  for (let index = 0; index < pa.steps.length; index += 1) {
    if (!preactivationStepLegal(pa, index)) return false;
  }
  return true;
}

/** 「这组来源不能一起转化」的**通用**文案（票 10）：不认牌名 / 花色，只说换一张牌。
 *
 * 服务端不下发"为什么"的规则文案（花色是牌面事实，不该由界面讲规则）；界面只把
 * "这一组提交不了"说清楚，出口是取消再重选。 */
const PREACTIVATION_GROUP_HINT = "这组来源不能一起转化，换一张牌试试。";

/** 一步**允许选几个**（W7-03 / D4）：座位步的 `step.max` 是**全局**最大值，而同一个技能
 *  的不同来源组上限可以不同（方天只放宽"最后一张手牌"的转化源）⇒ 逐来源上限由命中池行的
 *  `max` 带出，这里取两者较小。行上没有 `max`（既有清单 / 服务端退化）⇒ 沿用 `step.max`。
 *  `null` = 没有上限（`max = null` 的"全部可选"形状）。
 */
function preactivationStepCap(pa, index) {
  const step = pa && pa.steps[index];
  if (!step) return null;
  const globalMax = Number.isInteger(step.max) ? step.max : null;
  const row = preactivationPoolRow(pa, index);
  const rowMax = row && Number.isInteger(row.max) ? row.max : null;
  if (rowMax === null) return globalMax;
  if (globalMax === null) return rowMax;
  return Math.min(globalMax, rowMax);
}

/** 当前这一步的允许数（座位步用；`null` = 不限）。 */
function preactivationSeatCap(pa) {
  return pa ? preactivationStepCap(pa, pa.cursor) : null;
}

function preactivationAnswerTexts(step, values) {
  const kind = preactivationStepKind(step);
  if (kind === "seat") return values.map((seat) => `${SEAT_PREFIX}${seat}`);
  return values.map((value) => String(value));
}

/* ------------------------------------------------ 步骤预览（table-ux-overhaul 票 11）

 * 合法性只有一处算（服务端算、前端只读）。每变一次选择（点牌 / 点座位 / 点选项 / 取消 /
 * 回第一步）就发一次 `POST …/preview`，拿回"这一步此刻还能点谁 / 能不能进下一步 /
 * 能不能提交"，界面只画那份数据——同一步内的动态约束（势力互异、花色上限）因此与校验器
 * 同一份判据。
 *
 * 三条纪律：
 *  1. **去抖**（80–120 ms）：连点只发最后一次；一次选择变化最多一个往返。
 *  2. **本地版本号**：过期回包一律丢弃（`pa.previewVersion` 只增）。
 *  3. **fail-closed**：取不到（失败 / 超时 / 409）⇒ 这一步**不自动推进、不许提交**，
 *     提示"取不到候选，请重试"；**绝不**退回"全都点得动"（spec §6 #5）。
 */

const PREACTIVATION_PREVIEW_DEBOUNCE_MS = 90;
/** 预览请求的墙钟上限：超时按**失败**处理（与 HTTP 失败同一条 fail-closed 路径）。 */
const PREACTIVATION_PREVIEW_TIMEOUT_MS = 3000;
/** 取不到候选时的提示（fail-closed 的话术，测试按它断言）。 */
const PREACTIVATION_PREVIEW_ERROR = "取不到候选，请重试";

/** 这一问的清单能不能走**步骤预览**。
 *
 * 只有服务端在询问载荷里声明了 `preview: true` 才问（票 11 的"新信息通道"开关）：
 * 合成载荷 / 没有流水线的转化清单 / 借刀的附带指定清单都不带这个键，界面照旧读清单里的
 * 静态池——旧载荷因此逐字段、逐行为不变。
 */
function preactivationPreviewEnabled(pending) {
  return Boolean(pending && pending.preview === true && state.sessionId);
}

/** 本地答案 → 预览请求的 `answers`（**清单步名的前缀**，含正在编辑的那一步）。
 *
 * 服务端的光标规则与这里的本地光标同源：选牌 / 提供 / 多选座位步要显式点「下一步」，
 * 故"最后一条答案"可以就是玩家还在挑的那一步——它的 `values` 是**本步已选**，服务端据此
 * 排除已选目标（除疠的势力互异就是这么变灰的）。光标之后的一律不发（服务端也只认前缀）。
 */
function preactivationPreviewAnswers(pa) {
  const out = [];
  const upto = Math.min(pa.cursor, pa.steps.length - 1);
  for (let index = 0; index <= upto; index += 1) {
    const answer = pa.answers[index];
    out.push({
      name: String(pa.steps[index].name),
      values: (answer ? answer.values : []).slice(),
    });
  }
  return out;
}

/** 发一次预览（去抖 + 版本号）。失败 / 超时 ⇒ fail-closed 态 + 提示。 */
function schedulePreativationPreview() {
  const pa = state.preactivation;
  const pending = state.view ? state.view.pending : null;
  if (!pa || !pending || state.playback) return;
  if (!pa.skillId) return; // 手牌入口（借刀）的清单没有技能 → 预览端点不服务它
  if (!preactivationPreviewEnabled(pending)) return;
  const version = (pa.previewVersion || 0) + 1;
  pa.previewVersion = version;
  // 新一轮尝试先清掉旧的失败态：界面照旧用**上一份**真候选（服务端算过的那份），
  // 但在这一问出结果之前不许提交 / 自动推进（见 `preactivationSubmitAllowed`）。
  pa.previewError = false;
  const seq = pending.seq;
  const body = {
    answers_seq: seq,
    skill_id: pa.skillId,
    answers: preactivationPreviewAnswers(pa),
  };
  if (pa.previewTimer) window.clearTimeout(pa.previewTimer);
  pa.previewTimer = window.setTimeout(() => {
    pa.previewTimer = null;
    if (state.preactivation !== pa) return; // 中途退出预发动：连请求都不发
    requestPreativationPreview(pa, body, version, seq);
  }, PREACTIVATION_PREVIEW_DEBOUNCE_MS);
}

/** 真发请求。回包按**版本号 + 询问序号**双重核对：任一过期就丢弃（迟到的旧回包不许改界面）。 */
async function requestPreativationPreview(pa, body, version, seq) {
  const path = `/api/sessions/${state.sessionId}/preview`;
  let timer = null;
  const timeout = new Promise((resolve, reject) => {
    timer = window.setTimeout(
      () => reject(new Error(`${PREACTIVATION_PREVIEW_ERROR}（超时）`)),
      PREACTIVATION_PREVIEW_TIMEOUT_MS,
    );
  });
  try {
    const answer = await Promise.race([post(path, body), timeout]);
    if (timer) window.clearTimeout(timer);
    if (state.preactivation !== pa || pa.previewVersion !== version) return; // 过期的回包
    const now = state.view ? state.view.pending : null;
    if (!now || now.seq !== seq) return; // 询问换了：这一份不再描述界面上的那一问
    pa.preview = answer;
    pa.previewToken = version;
    pa.previewError = false;
    if (preactivationShouldAutoAdvance(pa, answer)) {
      // 光标跟着**服务端**说的走（不是 +1：多选步停在原地、单值步前进一步都由它定）。
      pa.cursor = answer.cursor;
      applyPreativationDefaults(pa, state.view ? state.view.pending : null);
      schedulePreativationPreview(); // 下一步的候选接着问（版本号自增，旧回包作废）
    }
    renderTable();
    renderPending(state.view);
  } catch (error) {
    if (timer) window.clearTimeout(timer);
    if (state.preactivation !== pa || pa.previewVersion !== version) return;
    // fail-closed：拿不到候选 ⇒ 这一步不许推进、不许提交；提示一条可重试的话术。
    pa.preview = null;
    pa.previewToken = null;
    pa.previewError = true;
    state.lastRejectReason = "";
    renderTable();
    renderPending(state.view);
  }
}

/** 手里这份回包描述的是**当下**这一步吗（最新一版 + 光标对得上）。
 *
 * 一旦又排了新的一版（`previewVersion` 自增），旧回包就只是"上一刻的快照"：候选照旧可以用
 * （服务端算过的那一份），但"能不能进下一步 / 能不能提交"要退回清单的本地判据——否则
 * 刚点完一张牌的那 100 ms 里，「下一步」会因为上一份回包说 `can_advance: false` 而点不动。
 */
function preactivationPreviewCurrent(pa, index) {
  return Boolean(
    pa && pa.preview && !pa.previewError
      && pa.previewToken === pa.previewVersion && pa.preview.cursor === index,
  );
}

/** 「下一步」能不能点：回包说 `can_advance`（前缀每一步都满足）时听服务端的。
 *
 * 预览失败 ⇒ 恒假（fail-closed，这一步既不推进也不许提交）；回包过期 / 没有预览通道 ⇒
 * 退回清单的本地判据（"填满了没有"是形状，不是规则——提交时服务端仍会再验一次）。
 */
function preactivationAdvanceAllowed(pa, index) {
  if (!pa) return false;
  if (pa.previewError) return false;
  if (preactivationPreviewCurrent(pa, index)) return Boolean(pa.preview.can_advance);
  return preactivationStepFilled(pa, index) && preactivationStepLegal(pa, index);
}

/** 能不能提交：回包说 `can_submit`（**整条清单**都满足）时听服务端的。
 *
 * 预览失败 ⇒ 恒假。回包过期 / 没有预览通道 ⇒ 清单本地的 `preactivationReady`（行为不变）。
 */
function preactivationSubmitAllowed(pa) {
  if (!pa) return false;
  if (pa.previewError) return false;
  if (preactivationPreviewCurrent(pa, pa.cursor)) return Boolean(pa.preview.can_submit);
  return preactivationReady(pa) && preactivationSourcesLegal(pa);
}

/** 这一步要不要玩家**自己点「下一步」**（服务端 `preview_step` 的 `explicit` 同一判据）。
 *
 * 选牌 / 提供步总要显式确认（多选、上限可能是"任意张"），座位步只在 ``max > min``
 * （1..max 多选）时要；选项步选完就走。
 */
function preactivationNeedsExplicitStep(step) {
  if (!step) return true;
  const kind = preactivationStepKind(step);
  if (kind === "card") return true;
  if (kind === "seat") return preactivationSeatMulti(step);
  return false;
}

/** 选择变化后**要不要当场**自动进下一步（单值座位步 / 选项步的既有形状）。
 *
 * 两类情形一律**不**当场推进：
 *
 * - **有预览通道**（票 11）：推进与否由**服务端的回包**说了算（回包里的 `can_advance` +
 *   `next`，见 `requestPreativationPreview`）——界面不自己算"这一步填完了没有"；
 * - **预览失败**（fail-closed）：这一步不自动推进、也不许提交，提示"取不到候选，请重试"。
 *
 * 没有预览通道的旧载荷（合成载荷 / 没有流水线的清单）保持既有行为：选完就走。
 */
function preactivationAutoAdvanceAllowed(pa) {
  if (!pa) return false;
  if (pa.previewError) return false;
  return !preactivationPreviewEnabled(state.view ? state.view.pending : null);
}

/** 服务端回包说"可以离开这一步了"吗：`can_advance` 且它给的光标**往前**了。
 *
 * **判据全在服务端**：`can_advance` = 已填的前缀每一步都满足，`cursor` = 服务端认为的
 * 当前步（单值步答完就 +1，多选 / 选牌步停在原地）。前端只补一条**交互形状**：
 * 选牌 / 多选座位步要玩家自己点「下一步」（与清单渲染的按钮同一判据）——所以这里只在
 * 光标确实往前走时才动，且**不**自己算"填满没有"。
 */
function preactivationShouldAutoAdvance(pa, answer) {
  if (!pa || !answer) return false;
  if (answer.can_advance !== true) return false;
  if (!Number.isInteger(answer.cursor) || answer.cursor <= pa.cursor) return false;
  return !preactivationNeedsExplicitStep(pa.steps[pa.cursor]);
}

/** 进入预发动：**不压帧、不计次数**，只把清单与本地答案摊开（W4-1）。
 *
 * `composite` 只区分**文案**（"复合动作" / "这次的使用 / 响应"）：W4-fix-2 起两类入口的
 * 确认形状都是 `ActivateSkill`，转化技那条由**服务端**解析成普通的 `UseCard(虚拟牌, 目标)`
 * ——界面不再去候选里查它。
 */
function enterPreativation(skill, manifest) {
  state.preactivation = {
    skillId: skill.skill_id,
    name: skill.name,
    composite: Boolean(skill.preactivated),
    steps: manifest,
    answers: manifest.map(() => ({ values: [], texts: [] })),
    cursor: 0,
  };
  state.convertSkill = null;
  state.picks = [];
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.lastRejectReason = "";
  state.preactivation.preview = null;
  state.preactivation.previewToken = null;
  state.preactivation.previewError = false;
  state.preactivation.previewVersion = 0;
  applyPreativationDefaults(state.preactivation, state.view ? state.view.pending : null);
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 点一张**强制转化**的手牌（‘只能’型，神关羽【武神】）：进入它的清单，并把来源步预选成这张牌。
 *
 * 强制 = 源牌只能当产物使用 / 打出（服务端 `transform_products[*].forced` 回答）；
 * 没有"是否发动"可问，故点牌就是"就用它"，剩下的只是选目标。判据只读服务端下发的
 * 清单（哪条技能的来源步池里有这张牌），不按技能 id / 牌名分支；找不到清单就 no-op（
 * 让既有的"用不出"提示照旧出现，不 fail-open）。
 */
function enterConvertedForm(pending, card) {
  const skills = (state.view && state.view.skills) || [];
  const skill = skills.find((item) => {
    const manifest = preactivationManifest(pending, item.skill_id);
    return Array.isArray(manifest) && manifest.length > 0
      && preactivationStepKind(manifest[0]) === "card"
      && (manifest[0].pool || []).some(
        (row) => (row.options || []).includes(String(card.card_id)),
      );
  });
  if (!skill) return;
  enterPreativation(skill, preactivationManifest(pending, skill.skill_id));
  const pa = state.preactivation;
  if (!pa || !pa.steps.length) return;
  pa.answers[0].values = [card.card_id];
  pa.answers[0].texts = preactivationAnswerTexts(pa.steps[0], [card.card_id]);
  if (pa.steps.length > 1) pa.cursor = 1;
  applyPreativationDefaults(pa, pending);
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 进入**手牌入口**的预发动（W5-02b 借刀）：点手牌 ⇒ 两步清单（持刀者 A → Bn）。
 *
 * 与技能清单**共用**步骤渲染器 / 答案结构 / 重选与取消；区别只在确认：
 * `buildPreativationAction` 见到 `cardId` 就拼 `UseCard(card, targets=(A,), designations=(Bn,))`
 * ——牌是**实体牌**，不拼虚拟牌 id，也不去 `legal_actions` 里找那条（它只在服务端池里）。
 */
function enterCardPreativation(pending, card, manifest) {
  state.preactivation = {
    skillId: null,
    cardId: card.card_id,
    card,
    name: cardName(card),
    composite: false,
    cardEntry: true,
    steps: manifest,
    answers: manifest.map(() => ({ values: [], texts: [] })),
    cursor: 0,
  };
  state.convertSkill = null;
  state.picks = [];
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.lastRejectReason = "";
  applyPreativationDefaults(state.preactivation, state.view ? state.view.pending : null);
  renderTable();
  renderPending(state.view);
}

/** 当前这一步是不是选牌步；是则返回它的候选 card_id 集合（`null` = 退化，全都可点）。 */
function preactivationCardOptions(pending) {
  const pa = state.preactivation;
  if (!pa || pa.cursor >= pa.steps.length) return undefined;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "card") return undefined;
  return preactivationOptions(pa, pa.cursor);
}

/** 预发动选牌步里，``zone`` 区的 ``card`` 是否可点。
 *
 * 预发动**不限于自己的区域**：【麒麟弓】的第一步只铺**目标**装备区的坐骑槽（服务端
 * 清单的 ``zones=["equip"]`` + pool 只给那张坐骑的 card_id）。牌桌表面因此要把目标座位
 * 的装备槽接成可点——判据只读清单的区 / 候选，不按座位写死。
 *
 * ``options === null``（服务端退化掉候选索引）时退化为"该区的牌全都可点"；合法性仍由
 * 服务端校验器裁决（与 :func:`togglePreativationCard` 同一姿态）。
 *
 * ``groups``（票 10）：已选的牌恒可点（再点 = 取消），其余牌还要能**落进**某组合法来源
 * ——与 :func:`cardFace` 的选牌支路同一判据（卡面与牌桌表面不能各判一套）。
 */
function preactivationCardPickable(pending, card, zone) {
  if (state.playback || !card) return false;
  const pa = state.preactivation;
  if (!pa || pa.cursor >= pa.steps.length) return false;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "card") return false;
  const zones = step.zones || [];
  if (!zones.includes(zone)) return false;
  const options = preactivationCardOptions(pending);
  if (options === undefined) return false;
  // `null` = 服务端没给候选 ⇒ 点不了（fail-closed）；`undefined` = 这一步不是选牌步（上面已拦）。
  if (options === null || !options.includes(String(card.card_id))) return false;
  const answer = pa.answers[pa.cursor];
  const values = answer ? answer.values : [];
  return values.includes(card.card_id)
    || preactivationGroupAccepts(pa, pa.cursor, values, card.card_id);
}

/** 点一张牌：多选步里点已选 = 取消该张、点新牌 = 追加（用户裁定的重选规则）。 */
function togglePreativationCard(cardId) {
  const pending = state.view ? state.view.pending : null;
  const pa = state.preactivation;
  if (!selectionEnabled() || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "card") return;
  const options = preactivationOptions(pa, pa.cursor);
  if (options === null || !options.includes(String(cardId))) return;
  const answer = pa.answers[pa.cursor];
  const at = answer.values.indexOf(cardId);
  // 票 10：**追加**要过合法来源组合（取消恒可）；卡面的 `.dis` 是第一道，这里是
  // 键盘 / 合成事件 / 陈旧界面走同一条路的第二道（不多算，只读同一份 `groups`）。
  if (at < 0 && !preactivationGroupAccepts(pa, pa.cursor, answer.values, cardId)) return;
  if (at >= 0) answer.values.splice(at, 1);
  else if (step.max === null || step.max === undefined || answer.values.length < step.max) {
    answer.values.push(cardId);
  }
  answer.texts = preactivationAnswerTexts(step, answer.values);
  state.lastRejectReason = "";
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/**
 * 选牌步的"下一步"：这一步按 `(min, cap)` 填完、且来源是**合法组合**才放行。
 *
 * 填完的判据与 chip ✓ / ready 同一处（:func:`preactivationStepFilled`）；合法来源组合
 * 另判（票 10）：让 cursor 走到末尾就是 §4 的死路——提交键亮着、手牌全灰、每一按都 400。
 */
function advancePreativation() {
  const pa = state.preactivation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  // 判据由服务端给（预览回包的 `can_advance`）：取不到候选时恒假 ⇒ 这一步不许推进。
  if (!preactivationAdvanceAllowed(pa, pa.cursor)) return;
  pa.cursor += 1;
  applyPreativationDefaults(pa, state.view ? state.view.pending : null);
  state.lastRejectReason = "";
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 座位步是不是**多选**：`max` 是整数且大于 `min`（W7-01b 的 1..max 目标步）。
 *
 * 单选座位步（`min == max == 1`）保持既有交互——点一下就写单值并自动进下一步；
 * 多选座位步改成"点座位 = 选 / 取消 + 下一步"，与选牌步同一个 `advancePreativation`。
 */
function preactivationSeatMulti(step) {
  return Number.isInteger(step.max) && step.max > step.min;
}

/** 借刀窗口目标步的**必选首项** = Bn（候选排序的自然结果；无则 `null`）。
 *
 * 引擎的成员判定是最终裁判（不含 Bn 的组合被拒）；这里只做引导——进入该步时预选、
 * 标成必选、点它不取消。**不新增询问、不新增协议字段**。
 */
function preactivationRequiredSeat(pa, pending, index) {
  if (!pa || !pending || pending.cause !== "BORROWED_SWORD") return null;
  const step = pa.steps[index];
  if (!step || preactivationStepKind(step) !== "seat") return null;
  const options = preactivationOptions(pa, index);
  if (!options || !options.length) return null;
  return options[0];
}

/** 座位步的**这一行**是不是"没有可选目标"（票 18）：服务端下发 ``StepPool.max = 0``。

 *  产物的目标由**牌名**决定时（【桃】/【酒】固定选自己、2 人局的【杀】只有一个对手），
 *  这一步没有可问的东西。服务端用``max = 0``（而不是把整条步拿掉）表达"这一行不需要选"：
 *  同一条清单里"【杀】要选目标、【桃】不要"可以并存。界面据此把这一步当成**已填**并在
 *  进入时自动跳过——判据只读服务端下发的这一格，不按技能 / 牌名分支。
 */
function preactivationNoChoiceStep(pa, index) {
  const step = pa && pa.steps[index];
  if (!step || preactivationStepKind(step) !== "seat") return false;
  const row = preactivationPoolRow(pa, index);
  return Boolean(row) && Number.isInteger(row.max) && row.max === 0;
}

/** 进入座位步时把**必选首项**（借刀窗口的 Bn）预先放进答案：省掉一次没有信息量的点击。
 *
 * 只在答案为空时填——玩家取消其它座位不会把它抖回来（首项本身也不可取消）。
 *
 * **没有可选目标的行直接跳过**（票 18）：``StepPool.max === 0`` 的座位步不画空步，
 * 光标越过它、答案保持空（服务端按同一份 0 收尾，复合动作里不带 choices）。
 */
function applyPreativationDefaults(pa, pending) {
  if (!pa) return;
  while (pa.cursor < pa.steps.length) {
    if (preactivationNoChoiceStep(pa, pa.cursor)) {
      pa.answers[pa.cursor] = { values: [], texts: [] };
      pa.cursor += 1;
      continue;
    }
    const required = preactivationRequiredSeat(pa, pending, pa.cursor);
    if (required === null) return;
    const answer = pa.answers[pa.cursor];
    if (answer.values.length) return;
    answer.values = [Number(required.slice(SEAT_PREFIX.length))];
    answer.texts = [required];
    return;
  }
}

/** 点场上武将：选座位步的答案。
 *
 * - **多选座位步**（W7-01b）：点座位 = 选 / 取消，`min..max` 计数，之后点「下一步」；
 *   借刀窗口的必选首项（Bn）**预选且不可取消**。
 * - **单选座位步**：既有形状——写单值并自动进下一步。
 */
function selectPreativationSeat(seat) {
  const pending = state.view ? state.view.pending : null;
  const pa = state.preactivation;
  if (!selectionEnabled() || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "seat") return;
  const options = preactivationOptions(pa, pa.cursor);
  const text = `${SEAT_PREFIX}${seat}`;
  // 拿不到候选 = 点不了（fail-closed，spec §6 #5：不许兜底成"全都点得动"）。
  if (options === null || !options.includes(text)) return;
  if (preactivationSeatMulti(step)) {
    const answer = pa.answers[pa.cursor];
    const at = answer.values.indexOf(seat);
    const required = preactivationRequiredSeat(pa, pending, pa.cursor);
    if (at >= 0) {
      if (text === required) return;
      answer.values.splice(at, 1);
    } else {
      const cap = preactivationSeatCap(pa);
      if (cap === null || answer.values.length < cap) answer.values.push(seat);
    }
    answer.texts = preactivationAnswerTexts(step, answer.values);
    state.lastRejectReason = "";
    schedulePreativationPreview();
    renderTable();
    renderPending(state.view);
    return;
  }
  pa.answers[pa.cursor] = { values: [seat], texts: [text] };
  // 单值座位步的既有形状是"点完自动进下一步"；预览失败时**不许**自动推进（fail-closed）。
  if (preactivationAutoAdvanceAllowed(pa)) {
    pa.cursor += 1;
    applyPreativationDefaults(pa, pending);
  }
  state.lastRejectReason = "";
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 点选项步的一项；选完自动进下一步。 */
function selectPreativationOption(text) {
  const pa = state.preactivation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "option") return;
  const options = preactivationOptions(pa, pa.cursor);
  // 拿不到候选 = 点不了（fail-closed）；置灰项在闭集里但**不可选**（第二道：键盘 / 合成事件）。
  if (options === null || !options.includes(text)) return;
  const blocked = preactivationDisabled(pa, pa.cursor);
  if (blocked && blocked.includes(text)) return;
  pa.answers[pa.cursor] = { values: [text], texts: [text] };
  if (preactivationAutoAdvanceAllowed(pa)) {
    pa.cursor += 1;
    applyPreativationDefaults(pa, state.view ? state.view.pending : null);
  }
  state.lastRejectReason = "";
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 重选第 K 步：清空 K..N（用户裁定），把光标拨回去。
 *
 * **不许跳过空步**（票 08）：落点钳到 `firstUnfilledPreativationStep`——前面的步骤还空着
 * 时，点后面的步骤 chip 只会落在那个空步上，不再出现"光标在末尾、前面还有空格"的坏态
 * （标题说填完了 / 页脚说没填完 / 确认禁用 / 补选时一点就清空后面）。清空仍从**落点**
 * 开始：落点之前填好的答案不动。
 */
function jumpPreativationStep(index) {
  const pa = state.preactivation;
  if (state.playback || !pa) return;
  const first = firstUnfilledPreativationStep(pa);
  const ceiling = first === null ? pa.steps.length : first;
  const landing = Math.max(0, Math.min(index, ceiling));
  for (let at = landing; at < pa.steps.length; at += 1) {
    pa.answers[at] = { values: [], texts: [] };
  }
  pa.cursor = landing;
  applyPreativationDefaults(pa, state.view ? state.view.pending : null);
  state.lastRejectReason = "";
  schedulePreativationPreview();
  renderTable();
  renderPending(state.view);
}

/** 这一步是否已按 `(min, cap)` 填齐（`cap` = 逐来源上限，`null` = 不限）。
 *
 * "这一步填完没有"**只算这一处**：`firstUnfilledPreativationStep`、步骤 chip 的 ✓、
 * `advancePreativation` 与「下一步」的可点态都读它（票 08）。
 */
function preactivationStepFilled(pa, index) {
  const step = pa && pa.steps[index];
  if (!step) return false;
  // 没有可选目标的一行（票 18）：服务端说"这一步在这个牌名下没有要选的" ⇒ 视为已填，
  // 否则目标由牌名决定的牌名（【桃】/【酒】）永远点不亮「确认发动」。
  if (preactivationNoChoiceStep(pa, index)) return true;
  const answer = pa.answers[index];
  const count = answer ? answer.values.length : 0;
  if (count < step.min) return false;
  const cap = preactivationStepCap(pa, index);
  return cap === null || count <= cap;
}

/** 第一个**没填完**的步骤下标；全部填完 ⇒ `null`。
 *
 * 清单**每一步**填完没有的**唯一判据**（票 08）：`preactivationReady`（= 光标到末尾 **且**
 * 它返回 `null`）/ 步骤提示 / 重选落点三处都读它，不再各算一套。光标**不许越过**它——
 * 越过去就是"光标在末尾、前面还有空格"，标题与页脚会各说一套，确认按钮禁用且补不回来。
 */
function firstUnfilledPreativationStep(pa) {
  if (!pa) return null;
  for (let index = 0; index < pa.steps.length; index += 1) {
    if (!preactivationStepFilled(pa, index)) return index;
  }
  return null;
}

/** 清单是否已按每一步的 `(min, max)` 填完。 */
function preactivationReady(pa) {
  if (!pa || pa.cursor < pa.steps.length) return false;
  return firstUnfilledPreativationStep(pa) === null;
}

/** 预发动的提交载荷：`ActivateSkill(picks, choices)`（**不**在 `legal_actions` 里拼候选）。
 *
 * 两类入口同一个形状（W4-fix-2 收口后**不再**去候选里查那条 `UseCard`）：
 *
 * - **复合动作**（已翻到预发动的技能）：`picks` = 代价牌、`choices` = 目标座位；
 * - **转化技**（武圣 / 龙胆 / 国色 / 急救 / 丈八）：`picks` = **来源牌**、`choices` = 目标
 *   座位（响应 / 求桃语境没有目标步，`choices` 为空）。虚拟牌的 id 与那条普通使用 / 打出
 *   动作都由**服务端**按同一份清单声明解析，界面不拼牌、也不要求它出现在候选里。
 */
function buildPreativationAction(pending, pa) {
  // 提交的可否由服务端给（预览回包的 `can_submit`）；预览失败 ⇒ 恒不可提交（fail-closed）。
  if (!pending || !pa || !preactivationSubmitAllowed(pa)) return null;
  // 票 10：来源不是合法组合 ⇒ **拼不出**这次提交（陈旧 / 合成载荷的最后一道闸；
  // 服务端仍然会 400——这只是不让界面主动发一条注定被拒的动作）。
  if (!preactivationSourcesLegal(pa)) return null;
  // 手牌入口（W5-02b 借刀）：两步座位答案 → 一次带**附带指定**的使用。
  // 牌是实体牌（不拼虚拟 id），`targets` = 持刀者 A、`designations` = 使用者指定的 Bn。
  // `pa.card` 是**展示卡**（快照里带 display_name / attribute / description …），
  // 提交前必须过 `bareCardRef` 收成纯 CardRef——服务端的 Action 模型是 extra="forbid"
  // （M5-bugfix：借刀预发动曾被拒为 `UseCard.card.attribute Extra inputs are not
  // permitted`）。其余字段（targets / designations）本来就只有报文字段。
  if (pa.cardEntry) {
    const holder = pa.answers[0] ? pa.answers[0].values[0] : undefined;
    const designate = pa.answers[1] ? pa.answers[1].values[0] : undefined;
    if (!Number.isInteger(holder) || !Number.isInteger(designate)) return null;
    return {
      kind: "UseCard",
      card: bareCardRef(pa.card),
      targets: [holder],
      designations: [designate],
      answers_seq: pending.seq,
    };
  }
  const picks = [];
  const choices = [];
  pa.steps.forEach((step, index) => {
    const answer = pa.answers[index];
    const kind = preactivationStepKind(step);
    if (kind === "card") picks.push(...answer.values);
    else choices.push(...answer.texts);
  });
  return {
    kind: "ActivateSkill",
    skill_id: pa.skillId,
    picks,
    choices,
    answers_seq: pending.seq,
  };
}

/** 这一步的**服务端文案**（W4-fix-3）：`Step.wording`；空 / 缺失 ⇒ `""`。
 *
 * 声明侧 = `SkillDef.step_requirements`（由 `flow.manifest` 填进清单），故界面不自己
 * 编文案：离间第 2/3 步的"决斗使用者 / 决斗目标"就是服务端下发的这两句。
 */
function preactivationWording(step) {
  return (step && typeof step.wording === "string") ? step.wording : "";
}

function preactivationStepLabel(step) {
  const wording = preactivationWording(step);
  if (wording) return wording;
  const kind = preactivationStepKind(step);
  if (kind === "card") return "选牌";
  if (kind === "seat") return "选目标";
  return "选一项";
}

/** 这一步的**问法**（票 07）：三层回退的唯一实现住 `prompts.js:promptQuestionForStep`
 *  （模板层逐级累加 → 服务端 `Step.wording` → 前端通用文案）。
 *
 *  步骤**条**仍用 :func:`preactivationStepLabel`（它要短），这里给的是提示行那一整句：
 *  `#t-target-hint` 与页脚 `#t-choice` 都读这一份，两处不会各说一套。
 */
function preactivationStepQuestion(pa, step, index) {
  if (typeof promptQuestionForStep === "function") return promptQuestionForStep(pa, step, index);
  return preactivationStepLabel(step);
}

function preactivationStepHint(pa, pending) {
  if (!pa) return "";
  // fail-closed 的话术优先：取不到候选时既不能推进也不能提交，玩家要知道"重试"是出路。
  if (pa.previewError) return `${PREACTIVATION_PREVIEW_ERROR}。`;
  // 票 10：已选来源凑不成合法组合 ⇒ 说清"换一张牌"，而不是继续画"第 k 步…"。
  if (!preactivationSourcesLegal(pa)) return PREACTIVATION_GROUP_HINT;
  // 光标越过清单末尾时没有"当前步"：清单还有空步就**指回第一个空步**，不能说
  // 「清单已填完」（票 08：判据与 `preactivationReady` 同源；标题 `#t-target-hint`
  // 与页脚 `#t-choice` 都读这一份提示，不再一处看 cursor、一处看 ready）。
  // 这是兜底支路：跳步已被钳住，正常交互走不到"光标在末尾、前面还有空格"。
  const at = pa.cursor < pa.steps.length ? pa.cursor : firstUnfilledPreativationStep(pa);
  if (at === null) {
    return pa.cardEntry ? "清单已填完，可以确认使用。" : "清单已填完，可以确认发动。";
  }
  const step = pa.steps[at];
  // 票 07：这一句问法走**三层回退的唯一实现**（模板层 → `Step.wording` → 前端通用文案）。
  // 模板层缺键时它逐字等于 `Step.wording`（改前的输出），故没有模板的技能行为不变。
  const what = preactivationStepQuestion(pa, step, at);
  if (preactivationStepKind(step) === "card") {
    const count = pa.answers[at].values.length;
    return `第 ${at + 1}/${pa.steps.length} 步：${what}（${step.min}–${step.max} 张，已选 ${count} 张）。`;
  }
  if (preactivationStepKind(step) === "seat") {
    const count = pa.answers[at].values.length;
    const required = preactivationRequiredSeat(pa, pending, at);
    const requiredText = required === null ? "" : "；借刀指定的目标（必选）";
    if (preactivationSeatMulti(step)) {
      const cap = preactivationStepCap(pa, at);
      if (cap !== null && cap <= step.min) {
        return `第 ${at + 1}/${pa.steps.length} 步：${what}（点场上武将多选，已选 ${count} / ${cap} 个${requiredText}）。`;
      }
      const upper = cap === null ? "max" : cap;
      return `第 ${at + 1}/${pa.steps.length} 步：${what}（点场上武将多选，已选 ${count} / ${step.min}–${upper} 个${requiredText}）。`;
    }
    return `第 ${at + 1}/${pa.steps.length} 步：${what}（点场上武将选择）。`;
  }
  return `第 ${at + 1}/${pa.steps.length} 步：${what}`;
}

/** 清单已经选中的来源牌会以哪张**产物**使用（U1-07）：读服务端 `transform_products`。
 *
 * 转化技的清单只说"选哪些来源牌"；选完（或单源选到一张）时提示条要能写
 * 「将以【杀】使用」。产物的匹配键是**来源牌组**（`session.js:sourceGroupKey`），
 * 故多源要把两张都选齐才命中；同一组来源能产出多张不同产物时 fail-closed、不猜。
 */
function preactivationProductHint(pa, pending) {
  const products = (pending && pending.transform_products) || [];
  const chosen = [];
  for (let index = 0; index < pa.steps.length; index += 1) {
    if (preactivationStepKind(pa.steps[index]) !== "card") continue;
    chosen.push(...(pa.answers[index].values || []));
  }
  if (!chosen.length) return "";
  const key = sourceGroupKey(chosen);
  const matches = products.filter(
    (product) => sourceGroupKey(product.virtual_sources || []) === key,
  );
  if (matches.length !== 1) return "";
  return `将以【${cardName(matches[0])}】使用。`;
}

/** 清单驱动的预发动界面（W4-1）：步骤条 + 当前的选牌 / 选目标 / 选项。 */
function renderPreativation(pending) {
  const pa = state.preactivation;
  const box = $("t-options");
  box.innerHTML = "";
  // 选项很多时改用**紧凑多列**（票 04 的复验意见）：蛊惑的牌名弹窗有十几个名字，单列
  // 排下来要滚动才找得着。判据只看**候选个数**（不认技能名）：超过这个数就多列铺，
  // 少的时候保持原来的单列按钮。子节点形状不变（仍是 `#t-options` 的直接子节点）。
  box.classList.remove("opt-compact");
  const hint = $("t-target-hint");
  if (hint) hint.textContent = `${preactivationProductHint(pa, pending)}${preactivationStepHint(pa, pending)}`;
  const row = document.createElement("div");
  row.className = "cards";
  pa.steps.forEach((step, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "opt";
    if (index === pa.cursor) chip.classList.add("sel");
    const done = preactivationStepFilled(pa, index);
    chip.textContent = `${index + 1}. ${preactivationStepLabel(step)}${done ? " ✓" : ""}`;
    chip.setAttribute("data-step", String(index));
    chip.addEventListener("click", () => jumpPreativationStep(index));
    row.appendChild(chip);
  });
  box.appendChild(row);
  if (pa.cursor >= pa.steps.length) {
    $("btn-cancel").disabled = false;
    return;
  }
  const step = pa.steps[pa.cursor];
  const kind = preactivationStepKind(step);
  if (kind === "option") {
    const options = preactivationOptions(pa, pa.cursor) || [];
    const blocked = preactivationDisabled(pa, pa.cursor) || [];
    box.classList.toggle("opt-compact", options.length > OPTION_COMPACT_MIN);
    for (const option of options) {
      const grayed = blocked.includes(String(option));
      const button = document.createElement("button");
      button.type = "button";
      button.className = "opt";
      button.textContent = String(option);
      if (grayed) {
        // 置灰（票 04 §3）：画出来给玩家看（"这个名字此刻用不了"），不接 click。
        button.classList.add("dis");
        button.disabled = true;
        button.title = "此刻没有合法目标";
      } else {
        button.addEventListener("click", () => selectPreativationOption(option));
      }
      box.appendChild(button);
    }
  }
  if (kind === "seat") {
    // 座位步的候选在**模态正文里也铺一遍**（U1-03 / 评审 P0）：模态窗口居中后可能挡住
    // 环上某几个座位，只靠点场上武将就点不到被挡的那个。两份入口调同一个
    // `selectPreativationSeat`；牌桌不再 inert（D8），环上高亮照旧。
    // 候选只读服务端（预览回包 / 清单的静态行）。取不到 ⇒ **一个都不画**：兜底成
    // "所有存活座位"就是在猜合法性（spec §6 #5 明禁），而且会把 400 直接送到玩家脸上。
    const seats = (preactivationOptions(pa, pa.cursor) || [])
      .map((value) => Number(String(value).slice(SEAT_PREFIX.length)))
      .filter((seat) => Number.isInteger(seat));
    const answer = pa.answers[pa.cursor];
    for (const seat of seats) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "opt seat-opt";
      button.setAttribute("data-seat", String(seat));
      const chosen = answer.values.includes(seat);
      if (chosen) button.classList.add("sel");
      const hero = (state.view && state.view.heroes && state.view.heroes[seat]) || "";
      button.textContent = `座位 ${seat}${hero ? ` ${hero}` : ""}${chosen ? " ✓" : ""}`;
      button.addEventListener("click", () => selectPreativationSeat(seat));
      box.appendChild(button);
    }
  }
  if (kind === "card" || (kind === "seat" && preactivationSeatMulti(step))) {
    // 多选座位步与选牌步共用同一个「下一步」入口（W7-01b）：单选座位步仍由点座位 / 点
    // 模态里的座位按钮直接自动进下一步，不铺按钮。
    const next = document.createElement("button");
    next.type = "button";
    next.className = "opt";
    next.textContent = "下一步";
    next.disabled = !preactivationAdvanceAllowed(pa, pa.cursor);
    next.addEventListener("click", advancePreativation);
    box.appendChild(next);
  }
  $("btn-cancel").disabled = false;
}
