/* 把 app.js 的入口挂到 globalThis.__bridge，**对缺失项容错**。
 *
 * 为什么要容错：`/tdd` 的红必须落在**断言**上。若桥硬引用一个尚未实现的函数，红会变成
 * 加载期的 ReferenceError，测试就分不清"功能没写"与"桥写错了"。
 *
 * 做法：用 `eval` 按**名字**去作用域链上取——`const` / `function` 的绑定在 vm 的词法作用域里，
 * 未必是 vm 全局对象的属性（用 `name in globalThis` 会漏），而 `eval` 看得见它们；
 * 取不到就是 undefined，不抛错。
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
  ];
  const out = {};
  for (const name of names) {
    const value = globalThis.eval(`typeof ${name} === "undefined" ? undefined : ${name}`);
    if (typeof value !== "undefined") out[name] = value;
  }
  globalThis.__bridge = out;
}
