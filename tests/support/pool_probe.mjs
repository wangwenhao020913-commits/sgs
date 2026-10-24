/* 开局页真名池判据探针：用**真实** `lobby.js` 的 `poolBlockReason` 回答一组场景。
 *
 * 为什么需要它：`lobby.js` 为了"提交前先拦"必须在本地重算引擎的"可用真名 ≥ 座位数"，
 * 这是唯一一处跨语言重复的规则。`tests/test_lobby_pool_contract.py` 把本探针的输出与
 * 引擎 `deal_heroes` 的 fail-closed 边界逐场景比对——两边一旦漂移就判红。
 *
 * 输入（stdin，JSON）：
 *   { "catalog": [{"true_name": ..., "versions": [{"id": ..., "name": ...}]}],
 *     "scenarios": [{"name": ..., "seats": ..., "banned_heroes": [...],
 *                    "pick": "hero_id" | null}] }
 * 输出（stdout，JSON）：{ "results": [{"name": ..., "blocked": bool, "reason": str|null}] }
 *
 * 加载纪律与 `ui_smoke.mjs` / `action_probe.mjs` 同姿态：脚本顺序来自 `index.html`，
 * `ui_bridge.js` 按名字桥出 `poolBlockReason`；这里只补 `$` / `state` 两个最小垫片
 * （`poolBlockReason` 只读三个表单值 + 本地将池态，不触 DOM 渲染）。
 */

import { readFileSync } from "node:fs";
import vm from "node:vm";

const STATIC_DIR = "tools/sgs_web/static";
const html = readFileSync(`${STATIC_DIR}/index.html`, "utf8");
const pageOrder = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map(
  (match) => match[1],
);
if (!pageOrder.includes("lobby.js")) {
  throw new Error("index.html 里没有 lobby.js");
}

const input = JSON.parse(readFileSync(0, "utf8"));
if (!Array.isArray(input.catalog) || !Array.isArray(input.scenarios)) {
  throw new Error("探针输入缺 catalog / scenarios 数组");
}

const inputs = {
  "in-players": { value: "8" },
  "in-pointjiang": { checked: false },
  "in-my-hero": { value: "" },
};
const context = vm.createContext({
  console,
  $: (id) => inputs[id] || null,
  state: { heroCatalog: [], heroPools: null },
});
context.globalThis = context;
for (const name of ["lobby.js"]) {
  vm.runInContext(readFileSync(`${STATIC_DIR}/${name}`, "utf8"), context, {
    filename: name,
  });
}
vm.runInContext(readFileSync("tests/support/ui_bridge.js", "utf8"), context, {
  filename: "ui_bridge.js",
});
const ui = context.__bridge;
if (typeof ui.poolBlockReason !== "function") {
  throw new Error("ui_bridge 没桥出 poolBlockReason：lobby.js 加载不完整");
}

const results = input.scenarios.map((scenario) => {
  ui.state.heroCatalog = input.catalog;
  ui.state.heroPools = {
    slots: [(scenario.banned_heroes || []).slice()],
    active: 0,
    selected: 0,
  };
  inputs["in-players"].value = String(scenario.seats);
  inputs["in-pointjiang"].checked = Boolean(scenario.pick);
  inputs["in-my-hero"].value = scenario.pick || "";
  const reason = ui.poolBlockReason();
  return {
    name: scenario.name,
    blocked: reason !== null,
    reason,
  };
});

process.stdout.write(JSON.stringify({ results }));
