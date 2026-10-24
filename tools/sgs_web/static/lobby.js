/* SGS 前端 · 开局页：人数 / 点将 / 建局。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 开局 */

function seedOptions() {
  const count = Number($("in-players").value);
  const select = $("in-seat");
  const previous = select.value;
  select.innerHTML = "";
  // 身份由 `_deal_identities` 按 seed 洗牌后逐座发放（座位只有下标固定）：座位 0
  // **不是**固定的主公；2 人局则是无身份局。两种情况都不该在座位号后面挂身份标签。
  for (let seat = 0; seat < count; seat += 1) {
    const option = document.createElement("option");
    option.value = String(seat);
    option.textContent = String(seat);
    select.appendChild(option);
  }
  if (previous && Number(previous) < count) select.value = previous;
}

/** 点将列表：把服务端下发的**版本平铺清单**铺成选项（`id` 是值、名称是给人看的字）。
 *
 * 目录是"按真名分组的版本"（票 13）：点将仍按**单版**选，故这里平铺一次；显示名
 * 用数据里的 `name`（标准版「关羽」/ 界版「界关羽」），两版在同一个下拉里分得清。
 */
function fillHeroOptions() {
  const select = $("in-my-hero");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  const versions = heroVersions();
  for (const hero of versions) {
    const option = document.createElement("option");
    option.value = hero.id;
    option.textContent = hero.name;
    select.appendChild(option);
  }
  const known = versions.some((hero) => hero.id === previous);
  select.value = known ? previous : (versions[0] || {}).id || "";
}

/** 拉一次点将列表；失败只提示、不挡住开局（列表空 ⇒ 开关自然点不出东西）。 */
async function loadHeroCatalog() {
  try {
    const body = await api("/api/heroes");
    state.heroCatalog = (body && body.heroes) || [];
  } catch (error) {
    state.heroCatalog = [];
    $("lobby-hint").textContent = `点将列表拉取失败：${error.message}`;
  }
  fillHeroOptions();
  syncPointjiang();
  // 目录也可能比存档先到（两个请求并行）：到货后把将池面板补画一次。
  renderHeroPool();
}

/** 开关的**唯一**落点：关了 = 置灰选将列表 + 建局不带 `my_hero`（纯随机）。 */
function syncPointjiang() {
  const on = !!($("in-pointjiang") || {}).checked;
  const select = $("in-my-hero");
  if (select) select.disabled = !on;
  syncPoolHint();  // 手选也占一个真名，可用真名数要跟着重算
  return on;
}

/** 牌堆档位列表：把服务端下发的档位铺成选项（`id` 是值，标签 / 说明也来自服务端）。 */
function fillDeckOptions() {
  const select = $("in-deck-profile");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  for (const deck of state.deckCatalog) {
    const option = document.createElement("option");
    option.value = deck.id;
    option.textContent = deck.name;
    if (deck.description) option.title = deck.description;
    select.appendChild(option);
  }
  const known = state.deckCatalog.some((deck) => deck.id === previous);
  const fallback = state.deckCatalog.find((deck) => deck.default) || state.deckCatalog[0];
  select.value = known ? previous : (fallback && fallback.id) || "";
}

/** 拉一次牌堆档位列表；失败只提示、不挡住开局（选项空 ⇒ 建局不带档位 = 默认档）。 */
async function loadDeckCatalog() {
  try {
    const body = await api("/api/decks");
    state.deckCatalog = (body && body.decks) || [];
  } catch (error) {
    state.deckCatalog = [];
    $("lobby-hint").textContent = `牌堆档位拉取失败：${error.message}`;
  }
  fillDeckOptions();
}

/* -------------------------------------------------------- 将池预设（M6 票 13）
 *
 * spec D5 / D6：开局页常驻 **5 个将池预设槽位**；选中某个槽位即**实时编辑**该槽位的
 * 禁将集合（版本粒度：点一格 = 禁掉这一版，禁用即灰），点「保存」**覆盖**该槽位并置为
 * active，下次打开自动加载 active。禁将**只作用于随机配将**，手选无视它。
 *
 * 本地态只住页面：`slots` 是每个槽位的禁将数组，`selected` 是正在编辑的槽位；服务端
 * 存档的 `active` 是**上次保存**的槽位。改槽位 / 划格子只改本地，保存才落盘。
 * 真名池不足（可用真名 < 座位数）时开局页**先拦**：实时提示 + 提交时不发建局请求，
 * 服务端（引擎侧）对同一条件也 fail-closed——两层判据一致。
 */

/** 存档请求失败时的兜底槽位数（服务端才是真源；这里只保证面板不空、开局仍可用）。 */
const HERO_POOL_FALLBACK_SLOTS = 5;

/** 将池面板的本地编辑态（缺省 5 空槽、选中 0）；用户交互前也可能被读，故惰性初始化。 */
function heroPools() {
  if (!state.heroPools) {
    const slots = [];
    for (let at = 0; at < HERO_POOL_FALLBACK_SLOTS; at += 1) slots.push([]);
    state.heroPools = { slots, active: 0, selected: 0 };
  }
  return state.heroPools;
}

/** 目录里的**版本平铺清单**（点将下拉 / 手选真名回查用）：组 → 版本，并带上真名。 */
function heroVersions() {
  const out = [];
  for (const group of state.heroCatalog) {
    for (const version of group.versions) {
      out.push({
        id: version.id,
        name: version.name,
        version: version.version,
        true_name: group.true_name,
      });
    }
  }
  return out;
}

/** 当前选中槽位的禁将集合（**编辑中的实时值**：保存前不代表已落盘的存档）。 */
function currentBans() {
  const pools = heroPools();
  return pools.slots[pools.selected] || [];
}

/** 把服务端存档 / 响应装进本地态：槽位数组复制一份（不共享服务端响应的引用）。 */
function applyHeroPools(presets) {
  const rawSlots = (presets && presets.slots) || [];
  const count = rawSlots.length || HERO_POOL_FALLBACK_SLOTS;
  const slots = [];
  for (let at = 0; at < count; at += 1) {
    const slot = rawSlots[at] || {};
    slots.push((slot.banned_heroes || []).slice());
  }
  let active = Number.isInteger(presets && presets.active) ? presets.active : 0;
  active = Math.min(Math.max(active, 0), slots.length - 1);
  // 启动 / 保存后选中的就是 active（"自动加载上次保存的将池"）。
  state.heroPools = { slots, active, selected: active };
}

/** 5 个槽位按钮：点一下就切到那个槽位编辑（不落盘；active 用 ★ 标出）。 */
function renderPoolSlots() {
  const box = $("pool-slots");
  if (!box) return;
  const pools = heroPools();
  box.innerHTML = "";
  for (let at = 0; at < pools.slots.length; at += 1) {
    const button = document.createElement("button");
    button.type = "button";
    button.classList.add("tab", "pool-slot");
    if (at === pools.selected) button.classList.add("on");
    if (at === pools.active) button.classList.add("active");
    button.textContent = `槽位 ${at + 1}`;
    button.title = at === pools.active
      ? "上次保存的槽位（下次打开自动加载）"
      : `编辑槽位 ${at + 1}（点保存才覆盖它）`;
    button.setAttribute("aria-current", at === pools.selected ? "true" : "false");
    button.addEventListener("click", () => selectPoolSlot(at));
    box.appendChild(button);
  }
  const save = $("btn-pool-save");
  if (save) save.textContent = `保存到槽位 ${pools.selected + 1}`;
}

/** 切槽位：只是换编辑对象，**不保存**（保存是显式动作）。 */
function selectPoolSlot(index) {
  const pools = heroPools();
  if (!Number.isInteger(index) || index < 0 || index >= pools.slots.length) return;
  pools.selected = index;
  renderPoolSlots();
  renderHeroPool();
}

/** 划 / 恢复一个**版本**格：禁将粒度是版本（spec D5），一行真名可以只剩一版可用。 */
function togglePoolBan(heroId) {
  const bans = currentBans();
  const at = bans.indexOf(heroId);
  if (at >= 0) bans.splice(at, 1);
  else bans.push(heroId);
  renderHeroPool();
}

/** 按真名分组的将池面板：一行一个真名、行内 1–2 个版本格，禁用的格子灰掉。 */
function renderHeroPool() {
  const box = $("hero-pool");
  if (!box) return;
  const banned = new Set(currentBans());
  box.innerHTML = "";
  for (const group of state.heroCatalog) {
    const row = document.createElement("div");
    row.classList.add("pool-row");
    row.setAttribute("data-true-name", group.true_name);
    const label = document.createElement("span");
    label.classList.add("pool-name");
    label.textContent = group.true_name;
    row.appendChild(label);
    let available = 0;
    for (const version of group.versions) {
      const isBanned = banned.has(version.id);
      if (!isBanned) available += 1;
      const cell = document.createElement("button");
      cell.type = "button";
      cell.classList.add("pool-version");
      if (isBanned) cell.classList.add("banned");
      cell.textContent = version.name;
      cell.setAttribute("data-hero-id", version.id);
      cell.setAttribute("aria-pressed", isBanned ? "false" : "true");
      cell.title = isBanned
        ? `已禁用：${version.name}（再点一下恢复）`
        : `可被随机抽到：${version.name}（点一下禁用）`;
      cell.addEventListener("click", () => togglePoolBan(version.id));
      row.appendChild(cell);
    }
    if (available === 0) row.classList.add("all-banned");
    box.appendChild(row);
  }
  syncPoolHint();
}

/** 手选的那个版本（开关关着 = null）；容量判据要它的**真名**（手选先占真名）。 */
function heroPick() {
  if (!($("in-pointjiang") || {}).checked) return null;
  const id = ($("in-my-hero") || {}).value;
  return heroVersions().find((version) => version.id === id) || null;
}

/** 开局的**客户端**拦截判据：可用真名（含手选的真名）< 座位数 ⇒ 返回可读原因。
 *
 * 与引擎的判据同义：真名池 = 至少一版未被禁的真名；手选先占真名（即使它两版都被禁，
 * 玩家点名要的版本照发，那个真名也算一个座位）。目录没拉到时返回 null——客户端不瞎猜，
 * 服务端仍会 fail-closed。
 */
function poolBlockReason() {
  if (!state.heroCatalog.length) return null;
  const seats = Number(($("in-players") || {}).value) || 0;
  if (!seats) return null;
  const banned = new Set(currentBans());
  const usable = new Set();
  for (const group of state.heroCatalog) {
    if (group.versions.some((version) => !banned.has(version.id))) usable.add(group.true_name);
  }
  const picked = heroPick();
  if (picked) usable.add(picked.true_name);
  if (usable.size >= seats) return null;
  return `可用真名不足：当前将池只有 ${usable.size} 个可用真名，本局需要 ${seats} 个座位`
    + `（把某个真名的全部版本都禁掉会把它移出可用池；手选的真名始终算一个）。`;
}

/** 将池面板的提示行：正常时是用法提示，真名池不足时变成可读的拒绝原因。 */
function syncPoolHint() {
  const hint = $("pool-hint");
  if (!hint) return;
  const reason = poolBlockReason();
  hint.textContent = reason
    || "点版本格 = 禁掉该版本（灰）；禁将只影响随机配将，手选不受影响。";
  hint.classList.toggle("warn", Boolean(reason));
}

/** 拉存档（5 槽 + active）；失败也把面板画成 5 个空槽，不挡住开局。 */
async function loadHeroPools() {
  let failure = null;
  try {
    const body = await api("/api/hero-presets");
    applyHeroPools(body && body.presets);
  } catch (error) {
    applyHeroPools(null);
    failure = `将池存档读取失败：${error.message}`;
  }
  renderPoolSlots();
  renderHeroPool();
  if (failure && $("pool-hint")) $("pool-hint").textContent = failure;
}

/** 保存当前槽位（**覆盖**它并置为 active）；服务端回读的该槽集合是唯一真源。
 *
 * 只回填**刚保存的那一槽**：别的槽位可能还有没保存的本地编辑，保存一槽不该把它们
 * 悄悄抹掉（切回那一槽时看到的仍是自己改到一半的样子；刷新才回到存档）。
 */
async function saveHeroPool() {
  const hint = $("pool-hint");
  const pools = heroPools();
  if (hint) hint.textContent = "正在保存将池…";
  try {
    const body = await post("/api/hero-presets", {
      slot: pools.selected,
      banned_heroes: currentBans().slice(),
    });
    const saved = (body && body.presets) || null;
    const slot = saved && saved.slots ? saved.slots[pools.selected] : null;
    if (slot) pools.slots[pools.selected] = (slot.banned_heroes || []).slice();
    if (saved && Number.isInteger(saved.active)) pools.active = saved.active;
    renderPoolSlots();
    renderHeroPool();
    if (hint) hint.textContent = `已保存到槽位 ${pools.selected + 1}（下次打开自动加载）`;
  } catch (error) {
    if (hint) hint.textContent = `将池保存失败：${error.message}`;
  }
}

async function startGame(event) {
  event.preventDefault();
  // 真名池不足 ⇒ 开局页**先拦**（与引擎侧同一判据）：不发建局请求、不创建会话。
  const blocked = poolBlockReason();
  if (blocked) {
    $("lobby-hint").textContent = blocked;
    return;
  }
  const seedRaw = $("in-seed").value.trim();
  const timeout = Number($("in-timeout").value);
  // 点将（票 19）：开关关着 ⇒ `my_hero: null`，服务端走**纯随机**那条老路径。
  const myHero = syncPointjiang() ? $("in-my-hero").value || null : null;
  $("lobby-hint").textContent = "正在建局…";
  try {
    const body = await post("/api/sessions", {
      player_count: Number($("in-players").value),
      ai: $("in-ai").value,
      human_seat: Number($("in-seat").value),
      timeout_seconds: timeout > 0 ? timeout : null,
      seed: seedRaw === "" ? null : Number(seedRaw),
      my_hero: myHero,
      // 禁将（M6 票 13）：送的是**当前选中槽位正在编辑的集合**（不一定是已保存的
      // active）；服务端把它冻结进这一局的会话配置，开局后再改存档不影响本局。
      banned_heroes: currentBans().slice(),
      // 牌堆档位（M5-01b）：选项由 `GET /api/decks` 铺出来；没铺到（拉取失败）就不带，
      // 服务端按内容包默认档（standard_108）建局——与改动前那条路一字不差。
      deck_profile: $("in-deck-profile").value || null,
    });
    state.sessionId = body.session_id;
    state.cursor = -1;
    state.logSeq = 0;
    // 先把上一局还在播的事件收尾，再清空；否则旧记录会漏进新一局的轮次计数。
    if (state.playback) flushPlayback();
    // 新的一局：事件流与轮次计数都从零开始（顶栏轮次按 TURN_START 累计）。
    state.logRecords = [];
    state.narrative = [];
    state.narrativeShown = 0;
    state.lastQuerySeat = null;
    $("t-log").innerHTML = "";
    applyView(body);
    show("table");   // 同时放开三处顶栏的「牌桌」（state.sessionId 已非空）
    $("lobby-hint").textContent = "";
  } catch (error) {
    $("lobby-hint").textContent = `建局失败：${error.message}`;
  }
}
