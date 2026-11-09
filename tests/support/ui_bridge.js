/* 把前端脚本的入口挂到 globalThis.__bridge，**对缺失项容错**。
 *
 * 为什么要容错：`/tdd` 的红必须落在**断言**上。若桥硬引用一个尚未实现的函数，红会变成
 * 加载期的 ReferenceError，测试就分不清"功能没写"与"桥写错了"。
 *
 * 做法：用 `eval` 按**名字**去作用域链上取——`const` / `function` 的绑定在 vm 的词法作用域里，
 * 未必是 vm 全局对象的属性（用 `name in globalThis` 会漏），而 `eval` 看得见它们；
 * 取不到就是 undefined，不抛错。
 *
 * **回放（报障 3）**：前端的 `applyView` / `submitAction` 现在会先播事件再提交视图。
 * 烟测不跑真实计时器（`window.setTimeout` 是空实现），故这里把这两个入口包一层：
 * 默认**立刻把回放跑完**（等价于"等动画播完再断言"），需要观察中间态的用例改用
 * `applyViewRaw` / `submitActionRaw` 配合 `stepPlayback`。
 */
{
  const names = [
    "state", "applyView", "renderPending", "buildAction", "submitAction",
    "loadRuns", "openReplay", "renderReplay", "renderLog", "phaseLabel",
    "cardUsable", "cardCandidates", "clearSelection", "renderTable", "actionLabel", "renderSkills",
    "ACTION_KIND_LABELS", "PHASE_LABELS", "keepRecord", "timingLabel",
    // 手选（票 01）：候选索引 + 焦点/目标 + 自动选中 + 渲染路径分派
    "cardIndex", "cardEntry", "cardSelectable", "isLegalCombo", "autoCombo",
    "pickerSeats", "pickerTargetHint", "toggleFocus", "selectTarget",
    "renderKind", "resetSelection", "isHandPick", "sameCombo", "HAND_PICK_KINDS", "needsPicker",
    // 本轮报障：公开处理区（五谷亮牌）+ 询问上下文 + 遗计"交给谁"的座位点选
    "selectOptionSeat",
    // W1：座位多选面——判据（`SELECT_OPTIONS` ∧ `option_seats` 非空）+ 点座位切换选中。
    // 探针用它点两个场上武将、断"一次提交送全部选中座位"；烟测用它断置灰 / 上限 / 重绘不丢。
    "isSeatMultiStep", "toggleSeatMultiPick",
    // 报障 2 / 4：转化技来源索引（【龙胆】）与转化模式切换（W4-2 的宽松保留）
    "cardAliases", "toggleConvertSkill", "providePickStays", "prunePreativationAnswers",
    "seatChoices",
    // 票 01：技能流水线的「取消」（候选里有 Pass 就送出去，否则只清本地选择）
    "cancelAction", "cancelOrClear",
    // W4-1 / W4-2：预发动清单（本地态、复合动作、取消零提交、被动入口）
    "preactivationManifest", "enterPreativation", "togglePreativationCard",
    "advancePreativation", "selectPreativationSeat", "selectPreativationOption",
    "jumpPreativationStep", "preactivationReady", "buildPreativationAction",
    // 票 08：清单"填完没有"的唯一判据（ready / 步骤提示 / 重选落点同读）
    "firstUnfilledPreativationStep",
    "preactivationStepKind", "preactivationOptions", "preactivationCardOptions",
    "preactivatedSkill", "isPassivePreativation", "renderPreativationEntry", "setActionLabels",
    // W7-01b：清单座位步多选（max > min）+ 借刀窗口首项（Bn）预选 / 必选
    "preactivationSeatMulti", "preactivationRequiredSeat", "applyPreativationDefaults",
    // W7-03（D4）：逐来源上限——命中池行的 `max` 收住座位步的可选数
    "preactivationPoolRow", "preactivationStepCap", "preactivationSeatCap",
    // W5-02b：手牌入口的预发动（借刀）——点手牌 ⇒ 两步清单 ⇒ 带 designations 的 UseCard
    "cardPreactivationManifest", "enterCardPreativation", "alternativeLabel",
    // 报障 3：事件回放（步长按钮 0.1–0.5s，默认 0.1）+ 效果区逐事件派生 + 描述文本
    "flushPlayback", "stepPlayback", "renderProcessing", "applyEffectDelta", "DELAY_STEPS_MS", "describeEffect",
    // U3-01 / D5：舞台项寿命的纯函数——烟测直接喂 `now` 断 visible / fading / gone（D15）
    "stageLane",
    // U4-03 / D9：批量停留的纯函数（0.5×(N+1) 秒）——烟测直接喂 N（缺省按 1）。
    "stageHoldMs",
    // W4-6：回放页（全区域增量重建 / 询问面板 / play-pause / 步长 / 空壳引导）
    "renderEffectCards", "replayStep", "replaySeek", "toggleReplayPlay", "stopReplayPlay",
    "renderRunList", "runRow",
    // 票 14：「提供一张 B」的就地渲染 + 置灰纯函数（cardFace）
    "isProvide", "provideSlotIndex", "cardFace", "cardFaceNode", "activateCard", "renderProvide", "toggleProvidePick",
    // 票 15：选择面的张数上限（读服务端 spec.total）+ 槽位点选（【遗计】一次两张 / 取消）
    "selectionCap", "toggleSlotPick",
    // 弃牌阶段的点手牌收进待弃置集合（U2-05 只读态要逐入口钉 no-op）
    "toggleDiscardPick",
    // W6-03：观星双行排列（规格 / 布局 / 点选移动 / 提交载荷）
    // W6-07：牌面只读 `pending.revealed`（`arrangeCards` 是唯一读取口）
    "renderArrange", "arrangeCards", "arrangeSpec", "arrangeAction", "arrangeKey", "initialArrange",
    "arrangeRowOf", "arrangeMoveTo", "arrangePick", "arrangeDrop",
    // 票 19：开局点将——列表拉取 + 开关（关了 = 置灰 + 建局不带 my_hero）
    "loadHeroCatalog", "fillHeroOptions", "syncPointjiang",
    // 用户 2026-11-07：装备 / 判定槽的简略牌面（花色点数 / 转）与跟随鼠标的卡牌预览
    "miniSlotNode", "showCardPreview", "moveCardPreview", "hideCardPreview",
    // 票 13：将池预设——5 槽切换 / 编辑 / 保存，真名分组渲染，真名池不足的拒绝判据
    "heroVersions", "heroPools", "loadHeroPools", "saveHeroPool", "currentBans",
    "renderPoolSlots", "renderHeroPool", "selectPoolSlot", "togglePoolBan",
    "poolBlockReason", "syncPoolHint",
    // M5-01b：开局页牌堆档位——列表拉取（选项/文案全来自服务端 GET /api/decks）
    "loadDeckCatalog", "fillDeckOptions",
    // U1-03：提示条右端动作的候选下标 + 倒计时的三色纯函数 / 渲染（烟测只读这三口）
    "rightActionIndex", "countdownPhase", "renderCountdown",
    // U2-07（票 07）：排列超时自动提交（唯一例外）+ 提交 / 放弃按当前排列执行。
    "arrangeHasPass", "ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS", "arrangeAutoSubmitDue",
    "arrangeAutoSubmitRemain", "maybeAutoSubmitArrange", "submitRightAction",
    // U2-01（D7）：三页共用一条顶栏的导航态（show 是高亮 / 禁用 / 切页的唯一落点）
    "show", "syncNavState", "NAV_BUTTONS",
    // U2-05（D8）：只读态的唯一判据（selectionEnabled）与"这一步的表面"（stepDialogKind）。
    // U2-06：询问弹窗的四种模板种类名（票 07–12 只往 stepDialogKind 里加分支）。
    // U2-08：未知区域模板的渲染口（装备分组只由载荷决定，模板级用例直接喂载荷）。
    "selectionEnabled", "stepDialogKind", "DIALOG_KINDS", "renderUnknownZone",
    // 票 06：未知区域弹窗的**多选档**——点一张 = 切换选中（`toggleUnknownZonePick`），
    // 一击即提交的旧档仍是 `submitUnknownPick`（`cap === 1` 不回归，探针两侧都跑）。
    "toggleUnknownZonePick", "submitUnknownPick",
    // U2-11（D4 / D10）：亮牌模板（五谷 + 票 12 遗计复用）——判据 / 数据 / 渲染 / 一击提交。
    // `isRevealStep` 住在 modal.js（只读判据源），渲染口在 reveal.js；烟测直接喂合成载荷。
    "isRevealStep", "revealData", "revealHintText", "renderReveal", "submitRevealPick",
    // 票 93：两行观看手牌——判据 / 行拆分 / 置灰 / 点选 / 渲染 / 提交下限（读 spec）。
    "isWatchTwoRowsStep", "watchRows", "watchRowLabel", "watchRequiredCount",
    "watchDuplicateCardId", "toggleWatchPick", "watchRowsHint", "renderWatchRows",
    "WATCH_EMPTY_MESSAGE", "WATCH_DUPLICATE_MESSAGE",
    // M5-08a：火攻 —— 亮牌步（亮牌模板 / 一击提交）+ 同花色弃牌（点自己的手牌）+ 舞台亮牌
    "isPickRevealStep", "isOwnHandSinglePick", "renderOwnHandPick", "submitOwnHandSinglePick",
    "applyRevealDelta", "stageRevealCards", "renderRevealedCards", "describeReveals",
    // M5-08b：铁索 1–2 目标（沿用点座位多选）+ 重铸入口（独立 Reforge，一击即提交）
    "reforgeAction", "renderReforgeEntry", "submitReforge", "describeRecord",
    // M5-bugfix（借刀预发动）：展示卡 → 纯 CardRef 的唯一投影口
    "bareCardRef",
    // M6 票 19：混合池武将名（目录兜底）/ 技能三态徽标 / 逐事件派生的轮次与额外回合
    "heroDisplayName", "skillStateLabel", "applyLiveDelta",
    // U4-01（D1/D2/D3/D5/D6）：飞行层——分类表时长 / ini 载入 / 起飞前量源 / 重绘后起飞。
    "animDefaults", "animConfig", "animParseIni", "animLoadConfig",
    "animReady", "animOnConfig", "animPlanFlight", "animLaunchFlight", "durationOf",
    // U4-02（D7/D8）：节奏 = base + 该条动画 × base/100（纯函数，烟测直接喂 {base, record}）。
    "delayOf",
    // huo-taishici-xunyu 票 04：拼点弹窗——载荷派生 / 回放入口 / 停留常量（只读展示面）。
    "pindianPlan", "applyPindianDelta", "showPindian", "hidePindian",
    "PINDIAN_HOLD_MS", "PINDIAN_VERDICTS",
  ];
  const out = {};
  for (const name of names) {
    const value = globalThis.eval(`typeof ${name} === "undefined" ? undefined : ${name}`);
    if (typeof value !== "undefined") out[name] = value;
  }
  // 原始入口（不自动冲刷回放）：观察中间态的用例用它。
  out.applyViewRaw = out.applyView;
  const rawSubmit = out.submitAction;
  // 文件头注释早就写了 `applyViewRaw` / `submitActionRaw` 这一对；后者此前没挂上，
  // 于是"提交之后、整批回放还没播完"的中间态在烟测里观察不到（M6 报障的红命令要用）。
  out.submitActionRaw = rawSubmit;
  out.applyView = (body) => {
    out.applyViewRaw(body);
    if (out.flushPlayback) out.flushPlayback();
  };
  out.submitAction = async (...args) => {
    const result = rawSubmit ? await rawSubmit(...args) : undefined;
    if (out.flushPlayback) out.flushPlayback();
    return result;
  };
  globalThis.__bridge = out;
}
