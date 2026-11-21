/* 回放派生的 Node 探针（table-ux-overhaul 票 10 / 票 14）：把**真的** `session.js` 放进最小
 * 垫片，用真记录流驱动 `applyLiveDelta`，把回放期 live view 的逐条读数打印成 JSON 给 Python 侧断。
 *
 * 为什么不用 `ui_smoke.mjs`：那条要起真服务、跑整局（60–70 s），而本探针只断"一条记录之后
 * live view 长什么样"——把它留给烟测的 U-QX-01 / U-IDLE-01（那里有 DOM 垫片，能断重绘）。
 * 加载纪律与 `log_cap_probe.mjs` / `action_probe.mjs` 同姿态：脚本从
 * `tools/sgs_web/static/` 读**真文件**，垫片只补 `state` / `document` / `$`。
 *
 * 输入（stdin，JSON）：
 *   {"base": <一份真视图载荷>, "records": [<记录>…], "idleSnapshot": <快照>,
 *    "opening": true}
 * 输出（stdout，JSON）：
 *   {"frames": [{"index", "seq", "players": {seat: {hp, hp_max, hand_count, hero_zones}}, …}, …],
 *    "fingerprint": {"base": "…", "idle": "…", "changed": true},
 *    "idle": {"isIdle": true}}
 *
 * `frames[0]` 是**派生前**的底稿（index = -1），其余每条记录一帧——断言因此能说"在该条之后
 * 就变了"，而不是"等 commit"。
 *
 * ``opening: true``（票 14）= 底稿走**开局批**那条路（`openingLiveView`，读载荷里的 `setup`），
 * 与 `applyView` 在 `opening` 分支里的取法同一个函数；不给则照票 10 的老口径取
 * `cloneLiveView`（中途批）。
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const STATIC_DIR = "tools/sgs_web/static";

/* 垫片只补 `session.js` 真的会碰的那几格：`state`（core.js 的全局）、`$` / `document`
 * （`markPlaybackHold` 的读数口）。本探针不调任何渲染入口（`renderTable` 一类）。 */
const state = { view: null, liveView: null, cursor: null };

const documentShim = {
  getElementById: () => null,
  documentElement: { setAttribute() {} },
  createElement: () => ({}),
};

const context = vm.createContext({
  state,
  document: documentShim,
  $: () => null,
  console,
});
context.globalThis = context;

vm.runInContext(readFileSync(`${STATIC_DIR}/session.js`, "utf8"), context, {
  filename: "session.js",
});

/* 桥只按名字取（缺项 = undefined，不抛加载期错误）：红必须落在断言上。
 * `typeof` 对未声明的名字是安全的（脚本是严格模式，直接引用才会抛）。 */
vm.runInContext(
  `globalThis.__probe = {
     cloneLiveView: typeof cloneLiveView === "function" ? cloneLiveView : null,
     openingLiveView: typeof openingLiveView === "function" ? openingLiveView : null,
     applyLiveDelta: typeof applyLiveDelta === "function" ? applyLiveDelta : null,
     mergeLiveDeltas: typeof mergeLiveDeltas === "function" ? mergeLiveDeltas : null,
     displayFactRecords: typeof displayFactRecords === "function" ? displayFactRecords : null,
     attachDisplayFacts: typeof attachDisplayFacts === "function" ? attachDisplayFacts : null,
     applyDisplayFact: typeof applyDisplayFact === "function" ? applyDisplayFact : null,
     viewFingerprint: typeof viewFingerprint === "function" ? viewFingerprint : null,
     snapshotFingerprint: typeof snapshotFingerprint === "function" ? snapshotFingerprint : null,
     pollIsIdle: typeof pollIsIdle === "function" ? pollIsIdle : null,
   };`,
  context,
);
const probe = context.__probe;

/* 一帧读数：座位的体力 / 上限 / 手牌数 / 武将牌上的牌区（区键 + 张数 + 牌面）/
 * 自己的手牌面。区键 = 记录里的 `owner:skill_id`（与 `skill_instance` 同形），断言按它认区。 */
const frameOf = (view, index, seq) => {
  const snapshot = view.snapshot || {};
  const seat = Number(snapshot.seat);
  const players = {};
  for (const player of snapshot.players || []) {
    players[Number(player.seat)] = {
      hp: player.hp,
      hp_max: player.hp_max,
      hand_count: player.hand_count,
      sealed_zones: (player.sealed_zones || []).slice(),
      hero_tags: (player.hero_tags || []).map((tag) => tag.tag || tag.label),
      hand_limit: player.hand_limit,
      hero_zones: (player.hero_zones || []).map((zone) => ({
        key: `${Number(zone.owner)}:${String(zone.skill_id)}`,
        skill_id: zone.skill_id,
        skill_name: zone.skill_name,
        owner: zone.owner,
        count: zone.count,
        card_ids: (zone.cards || []).map((card) => String(card && card.card_id)),
      })),
    };
  }
  /* ui-live-refresh 票 02：**派生显示字段**的逐帧读数（没有 delta 的那几格）。 */
  const skills = {};
  for (const entry of view.skills || []) {
    if (!entry || !entry.appearance) continue;
    const appearance = entry.appearance;
    skills[entry.skill_id] = (appearance.cards || []).map((card) => card.hero_id);
  }
  const heroTags = {};
  const handLimits = {};
  for (const player of snapshot.players || []) {
    heroTags[Number(player.seat)] = (player.hero_tags || []).map((tag) => tag.tag || tag.label);
    handLimits[Number(player.seat)] = player.hand_limit;
  }
  return {
    index,
    seq: seq === undefined ? null : seq,
    seat,
    players,
    sealed: Object.fromEntries(
      Object.entries(players).map(([key, player]) => [key, player.sealed_zones]),
    ),
    hero_tags: heroTags,
    hand_limit: handLimits,
    appearance: skills,
    self_hand: (snapshot.self_hand || []).map((card) => String(card && card.card_id)),
  };
};

const input = JSON.parse(readFileSync(0, "utf8"));
const base = input.base;
if (!base || !base.snapshot) throw new Error("探针输入缺少 base（一份真视图载荷）");
if (typeof probe.cloneLiveView !== "function") {
  throw new Error("session.js 没有 cloneLiveView：live view 的生产入口不在，探针无从驱动");
}
if (typeof probe.applyLiveDelta !== "function") {
  throw new Error("session.js 没有 applyLiveDelta：探针无从驱动");
}

state.view = base;
state.cursor = base.cursor === undefined ? null : base.cursor;
/* 与 `applyView` 同一条生产路径：中途批从容器的深拷贝起算（`cloneLiveView`），
 * 开局批走 `openingLiveView`（票 14：读载荷里的 `setup` 底稿）。 */
if (input.opening) {
  if (typeof probe.openingLiveView !== "function") {
    throw new Error("session.js 没有 openingLiveView：开局底稿的生产入口不在，探针无从驱动");
  }
  state.liveView = probe.openingLiveView(base);
} else {
  state.liveView = probe.cloneLiveView(base);
}
/* 与 `applyView` + `stepPlayback` 同一条生产路径（ui-live-refresh 修复 2）：标签 delta 按
 * `seq` 并进记录流；**显示事实挂到记录上**、在**那条记录之后**应用（不占回放步）。 */
let stream = input.records || [];
if (typeof probe.mergeLiveDeltas === "function") {
  stream = probe.mergeLiveDeltas(stream, input.liveDeltas || []);
}
if (typeof probe.attachDisplayFacts === "function" && typeof probe.displayFactRecords === "function") {
  probe.attachDisplayFacts(stream, probe.displayFactRecords({ display_facts: input.displayFacts || [] }));
}
const frames = [frameOf(state.liveView, -1, null)];
for (const [index, record] of stream.entries()) {
  probe.applyLiveDelta(record, state.liveView);
  for (const fact of (record && record.displayFacts) || []) {
    probe.applyDisplayFact(fact, state.liveView);
  }
  frames.push(frameOf(state.liveView, index, record && record.seq));
}

const fingerprint = {};
/* 空转指纹（ui-live-refresh 票 01）：摘要吃**整份视图**（`viewFingerprint`），不再只吃
 * `snapshot`。老输入 `idleSnapshot` 仍支持——它等价于"只有快照变了"的那一份视图。 */
if (typeof probe.viewFingerprint === "function") {
  fingerprint.base = probe.viewFingerprint(base);
  const idleView = input.idleView
    || (input.idleSnapshot ? { ...base, snapshot: input.idleSnapshot } : null);
  if (idleView) fingerprint.idle = probe.viewFingerprint(idleView);
  fingerprint.changed = idleView ? fingerprint.base !== fingerprint.idle : false;
} else if (typeof probe.snapshotFingerprint === "function") {
  fingerprint.base = probe.snapshotFingerprint(base.snapshot);
  if (input.idleSnapshot) {
    fingerprint.idle = probe.snapshotFingerprint(input.idleSnapshot);
  }
  fingerprint.changed = fingerprint.idle !== undefined
    ? fingerprint.base !== fingerprint.idle
    : false;
}

const idle = {};
if (typeof probe.pollIsIdle === "function" && (input.idleView || input.idleSnapshot)) {
  /* 空转判据只读 `state.view`（上一份提交视图）与响应里的 `cursor` / `pending`：
   * 与 `poll()` 一样把 base 当"当前视图"喂进去。 */
  const incoming = input.idleView || { ...base, snapshot: input.idleSnapshot };
  idle.isIdle = Boolean(probe.pollIsIdle({
    cursor: incoming.cursor === undefined ? state.cursor : incoming.cursor,
    pending: incoming.pending || null,
    snapshot: incoming.snapshot,
  }));
}

process.stdout.write(`${JSON.stringify({ frames, fingerprint, idle })}\n`);
