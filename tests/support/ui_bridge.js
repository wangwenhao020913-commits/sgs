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
    "cardUsable", "cardCandidates", "clearSelection", "groupActions", "renderTable", "actionLabel", "renderSkills",
    "PHASE_LABELS", "keepRecord", "timingLabel",
    // 手选（票 01）：候选索引 + 焦点/目标 + 自动选中 + 渲染路径分派
    "cardIndex", "cardEntry", "cardSelectable", "isLegalCombo", "autoCombo",
    "pickerSeats", "pickerTargetHint", "toggleFocus", "selectTarget",
    "renderKind", "resetSelection", "isHandPick", "sameCombo", "HAND_PICK_KINDS", "needsPicker",
    // 本轮报障：公开处理区（五谷亮牌）+ 询问上下文 + 遗计"交给谁"的座位点选
    "selectOptionSeat",
    // 报障 2 / 4：转化技来源索引（【龙胆】）与转化模式切换（W4-2 的宽松保留）
    "cardAliases", "toggleConvertSkill", "providePickStays", "prunePreativationAnswers",
    "seatChoices",
    // 票 01：技能流水线的「取消」（候选里有 Pass 就送出去，否则只清本地选择）
    "cancelAction", "cancelOrClear",
    // W4-1 / W4-2：预发动清单（本地态、复合动作、取消零提交、被动入口）
    "preactivationManifest", "enterPreativation", "togglePreativationCard",
    "advancePreativation", "selectPreativationSeat", "selectPreativationOption",
    "jumpPreativationStep", "preactivationReady", "buildPreativationAction",
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
    // W4-6：回放页（全区域增量重建 / 询问面板 / play-pause / 步长 / 空壳引导）
    "renderEffectCards", "replayStep", "replaySeek", "toggleReplayPlay", "stopReplayPlay",
    "renderRunList", "runRow",
    // 票 14：「提供一张 B」的就地渲染 + 置灰纯函数（cardFace）
    "isProvide", "provideSlotIndex", "cardFace", "activateCard", "renderProvide", "toggleProvidePick",
    // 票 15：选择面的张数上限（读服务端 spec.total）+ 槽位点选（【遗计】一次两张 / 取消）
    "selectionCap", "toggleSlotPick",
    // W6-03：观星双行排列（规格 / 布局 / 点选移动 / 提交载荷）
    // W6-07：牌面只读 `pending.revealed`（`arrangeCards` 是唯一读取口）
    "renderArrange", "arrangeCards", "arrangeSpec", "arrangeAction", "arrangeKey", "initialArrange",
    "arrangeRowOf", "arrangeMoveTo", "arrangePick", "arrangeDrop",
    // 票 19：开局点将——列表拉取 + 开关（关了 = 置灰 + 建局不带 my_hero）
    "loadHeroCatalog", "fillHeroOptions", "syncPointjiang",
    // U1-03：提示条右端动作的候选下标 + 倒计时的三色纯函数 / 渲染（烟测只读这三口）
    "rightActionIndex", "countdownPhase", "renderCountdown",
  ];
  const out = {};
  for (const name of names) {
    const value = globalThis.eval(`typeof ${name} === "undefined" ? undefined : ${name}`);
    if (typeof value !== "undefined") out[name] = value;
  }
  // 原始入口（不自动冲刷回放）：观察中间态的用例用它。
  out.applyViewRaw = out.applyView;
  const rawSubmit = out.submitAction;
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
