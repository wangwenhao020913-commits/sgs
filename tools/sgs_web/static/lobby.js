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
  // ui-one-screen 票 03：右栏「我的武将 + 技能全文」跟着这个下拉走。换人 / 换座位 / 开关
  // 点将都要重画，而 `main.js:wire()` 只给 `in-my-hero` 挂了 `syncPoolHint`，故这里补挂
  // 一次（幂等；`fillHeroOptions` 是目录到货时的那一次，早于点将开关的第一次重画）。
  wireLobbyHeroPanel();
  renderLobbyHeroPanel();
  syncLobbyToolrow();
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

/** 开关的**唯一**落点：关了 = 置灰选将列表 + 建局不带 `my_hero`（纯随机）＋ 右栏换成空态。 */
function syncPointjiang() {
  const on = !!($("in-pointjiang") || {}).checked;
  const select = $("in-my-hero");
  if (select) select.disabled = !on;
  syncPoolHint();  // 手选也占一个真名，可用真名数要跟着重算
  // ui-one-screen 票 03：点将关着 ⇒ 右栏是「随机发将」空态（空态不编数据）；
  // 工具行「点将 W」那句也在这里跟着开关 / 人选走。
  renderLobbyHeroPanel();
  syncLobbyToolrow();
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

/** 按真名分组的将池面板：一行一个真名、行内 1–2 个版本格，禁用的格子灰掉。
 *
 * 版本格那一块住在 L 窗里（票 03 搬的；票 04 会重建它）——**窗没开时也可能被调**，故
 * `#hero-pool` 找不到也要把左栏的摘要（统计行 / 名字块 / 工具行那句）重画完。
 */
function renderHeroPool() {
  const box = $("hero-pool");
  if (box) {
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
  }
  renderPoolSummary();
  syncLobbyToolrow();
  syncPoolHint();
  return null;
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
 *
 * 这是前端唯一重复引擎规则的地方，故阈值由 `tests/test_lobby_pool_contract.py` 逐边界
 * 比对真实的 `poolBlockReason` 与引擎 `deal_heroes` 的 fail-closed：改动本函数要让那条
 * 契约测试继续红绿一致，别只改这一边。
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
    // 换会话 + 回放 + 切页只有一份实现（M7 票 23 起剧本战斗也从这条路口进牌桌）。
    startSessionView(body);
    $("lobby-hint").textContent = "";
  } catch (error) {
    $("lobby-hint").textContent = `建局失败：${error.message}`;
  }
}

/* ------------------------------------- 开局页一屏（ui-one-screen 票 03 / spec A5）
 *
 * 版式：常驻工具行（三颗入口键，位置永不变）→ 左栏「开一局」+「将池摘要」/ 右栏
 * 「我的武将 + 技能全文」。数据全来自票 01 的 `/api/heroes` version 展示面（`kingdom` /
 * `hp` / `hp_max` / `skills[]`）与既有 `state.heroCatalog` / `state.heroPools`——前端不查表、
 * 不派生第二份数据。判据是「内容长短不改按钮位置」（原型 `geometry-tables.md` 表 C）：
 * 名字块与技能全文各有自己的滚动区，两侧面板与左侧表单不动。
 */

/** 势力枚举值 → 中文标签（**全仓唯一一份**：票 04 的将池窗、票 06 的技能浮层复用这张表）。
 *
 * 键顺序 = 界面上的分组顺序（魏 / 蜀 / 吴 / 群 / 神）：将池摘要的武将名块按它分桶。
 * 服务端给的是 `kingdom.value`（`wei` / `shu` / `wu` / `qun` / `shen`）；未知值不编标签
 * （落到「其他」桶——枚举变了宁可显示得难看，也不猜成某个势力）。
 */
const KINGDOM_LABELS = { wei: "魏", shu: "蜀", wu: "吴", qun: "群", shen: "神" };

/** 势力枚举值 → 中文标签；未知 / 缺字段 ⇒ 空串（不编名字）。 */
function kingdomLabel(kingdom) {
  return KINGDOM_LABELS[kingdom] || "";
}

/** 版本 id → 目录里那条 version（带票 01 的展示面）；未知 id ⇒ null。 */
function heroVersionById(heroId) {
  if (!heroId) return null;
  for (const group of state.heroCatalog) {
    for (const version of group.versions) {
      if (version.id === heroId) return version;
    }
  }
  return null;
}

/** 右栏要画的那个版本：`#in-my-hero` 的当前值；点将关着 ⇒ null（随机发将，空态）。 */
function lobbyHeroPick() {
  if (!($("in-pointjiang") || {}).checked) return null;
  return heroVersionById(($("in-my-hero") || {}).value);
}

/** 换人 / 换座位 / 换点将开关都要重画右栏。`main.js:wire()` 只给下拉挂了 `syncPoolHint`，
 *  故这条补挂在本文件里（目录到货时挂一次，幂等；下拉节点本身不会被重建）。 */
let lobbyHeroPanelWired = false;

function wireLobbyHeroPanel() {
  if (lobbyHeroPanelWired) return null;
  lobbyHeroPanelWired = true;
  for (const id of ["in-my-hero", "in-seat", "in-players"]) {
    const node = $(id);
    if (node) node.addEventListener("change", () => renderLobbyHeroPanel());
  }
  return null;
}

/** 座位下拉的当前值（0 基下标，与表单选项文案同源）；空 ⇒ 「—」。 */
function seatChoice() {
  const select = $("in-seat");
  const value = select ? String(select.value) : "";
  return value === "" ? "—" : value;
}

/** 右栏上半：势力徽记 / 名字 / 体力点（`hp / hp_max`，pips 按上限画、按当前体力点亮）/ 一句说明。 */
function renderLobbyHeroPanel() {
  const version = lobbyHeroPick();
  const kingdom = $("lobby-hero-kingdom");
  const name = $("lobby-hero-name");
  const hpLabel = $("lobby-hero-hp-label");
  const pips = $("lobby-hero-hp");
  const hint = $("lobby-hero-hint");
  const seat = $("lobby-hero-seat");
  const hand = $("lobby-hero-handlimit");
  const hp = version ? Number(version.hp) || 0 : 0;
  const hpMax = version ? Number(version.hp_max) || 0 : 0;
  if (kingdom) {
    // 徽记复用既有 `.kingdom`（势力底色是表里既有那套，不新增颜色变量）；标签只有
    // `KINGDOM_LABELS` 一份。`data-kingdom` 留枚举值原样，便于对着载荷核对。
    kingdom.className = version && kingdomLabel(version.kingdom)
      ? `kingdom ${version.kingdom}` : "kingdom";
    kingdom.textContent = version ? kingdomLabel(version.kingdom) : "";
    if (version && version.kingdom) kingdom.setAttribute("data-kingdom", version.kingdom);
    else kingdom.removeAttribute("data-kingdom");
  }
  if (name) name.textContent = version ? version.name : "随机发将";
  // 体力一律印 `hp / hp_max` 两个数：神甘宁那种 3/6 的初始体力和上限不是一回事。
  if (hpLabel) hpLabel.textContent = version ? `${hp} / ${hpMax} 体力` : "";
  if (pips) {
    pips.innerHTML = "";
    for (let at = 0; at < hpMax; at += 1) {
      const pip = document.createElement("span");
      pip.className = at < hp ? "pip" : "pip off";
      pips.appendChild(pip);
    }
  }
  if (hint) {
    hint.textContent = version
      ? "点将已开：这一栏跟着「我的武将」走，切下拉即可换人。"
      : "点将关着：本局随机发将，技能全文要等点将开了才看得到。";
  }
  if (seat) seat.textContent = `座位 ${seatChoice()}`;
  // 手牌上限 = 当前体力（规则恒定，不是第二份数据）。
  if (hand) hand.textContent = version ? `手牌上限 ${hp}` : "手牌上限 —";
  renderLobbySkills(version);
  return null;
}

/** 右栏下半：技能全文。描述**原样印服务端的文本**（spec §8 R7：前端零正则、零截断）。 */
function renderLobbySkills(version) {
  const box = $("lobby-skill-list");
  if (!box) return null;
  box.innerHTML = "";
  if (!version) {
    box.appendChild(lobbyNote("点将关着（随机发将）：技能全文要等点将开了才看得到。"));
    return null;
  }
  const skills = Array.isArray(version.skills) ? version.skills : [];
  if (!skills.length) {
    box.appendChild(lobbyNote("这个武将在数据表里没有技能。"));
    return null;
  }
  for (const skill of skills) {
    const block = document.createElement("div");
    block.className = "skillblock";
    const head = document.createElement("p");
    head.className = "sn";
    head.textContent = skill.name || skill.id || "";
    const desc = document.createElement("p");
    desc.className = "sd";
    desc.textContent = skill.description || "";
    block.appendChild(head);
    block.appendChild(desc);
    box.appendChild(block);
  }
  return null;
}

/** 空态 / 说明的一行（同一处造节点，省得各处手搓）。 */
function lobbyNote(text) {
  const note = document.createElement("p");
  note.className = "dim";
  note.textContent = text;
  return note;
}

/* ------------------------------------------------------------- 将池摘要 */

/** 将池摘要的四个数：**唯一**的计算口（工具行那句与左栏统计行同源，不各算一份）。
 *
 * 口径：`names` = 至少一版可用的**真名**数、`versions` = 可被随机抽到的**版本**数、
 * `banned` = 当前槽位禁掉的版本数、`slot` = 正在编辑的槽位（1 基，与按钮文案同源）。
 * 禁将只作用于随机配将，故"可用"就是"没被禁"；手选无视禁将，不在这个口径里。
 */
function poolSummaryStats() {
  const banned = new Set(currentBans());
  let names = 0;
  let versions = 0;
  let bannedCount = 0;
  for (const group of state.heroCatalog) {
    let available = 0;
    for (const version of group.versions) {
      if (banned.has(version.id)) {
        bannedCount += 1;
      } else {
        available += 1;
        versions += 1;
      }
    }
    if (available > 0) names += 1;
  }
  return { slot: heroPools().selected + 1, names, versions, banned: bannedCount };
}

/** 摘要的公共半句（两句都从这一处拼，数字口径不会漂）。 */
function poolSummaryCore() {
  const stats = poolSummaryStats();
  return `槽位 ${stats.slot} · ${stats.names} 真名 / ${stats.versions} 版本`
    + ` · 已禁 ${stats.banned} 个版本`;
}

/** 「点将 W」那一格：关着 = 随机发将；开着 = 下拉里的版本显示名。 */
function poolPickLabel() {
  if (!($("in-pointjiang") || {}).checked) return "随机";
  const version = heroVersionById(($("in-my-hero") || {}).value);
  return (version && version.name) || "—";
}

/** 工具行右端那句将池摘要（**只是显示**，不带动作）。槽位 / 禁将 / 点将一变就重算。 */
function syncLobbyToolrow() {
  const node = $("lobby-pool-summary");
  if (node) node.textContent = `将池：${poolSummaryCore()} · 点将 ${poolPickLabel()}`;
  return null;
}

/** 左栏下半：槽位按钮（`renderPoolSlots` 画的）下面是统计行 + 按势力分组的名字块。
 *
 * 名字块只是**摘要**：已禁版本仍列出、划掉变灰；**不**在这里画版本格（那是将池窗的事，
 * 票 04 重建）。名字再多也只让 `.pool-chips` 自己滚，两栏版式不动。
 */
function renderPoolSummary() {
  const stats = $("pool-stats");
  if (stats) stats.textContent = `当前槽位：${poolSummaryCore()}`;
  const box = $("pool-chips");
  if (!box) return null;
  box.innerHTML = "";
  const banned = new Set(currentBans());
  const buckets = new Map();
  for (const group of state.heroCatalog) {
    for (const version of group.versions) {
      const key = KINGDOM_LABELS[version.kingdom] ? version.kingdom : "other";
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(version);
    }
  }
  for (const key of [...Object.keys(KINGDOM_LABELS), "other"]) {
    const list = buckets.get(key);
    if (!list || !list.length) continue;
    const line = document.createElement("span");
    line.className = "chipline";
    line.textContent = key === "other" ? "其他" : KINGDOM_LABELS[key];
    box.appendChild(line);
    for (const version of list) {
      const isBanned = banned.has(version.id);
      const chip = document.createElement("span");
      chip.className = isBanned ? "chiphero banned" : "chiphero";
      chip.textContent = version.name;
      chip.title = isBanned
        ? `已禁用：${version.name}（到将池窗里恢复）`
        : `可被随机抽到：${version.name}`;
      box.appendChild(chip);
    }
  }
  return null;
}

/* ------------------------------------------- 开局页的三扇面板窗（spec A2 / A5）
 *
 * 三块内容（剧本 S / 将池预设 L / 录像 M）窗没开时住在 `#lobby-window-stash` 里；工具行
 * 三键一点就把对应的那一块搬进**同一扇**全局面板窗（`panel-window.js:panelOpen`，单槽位
 * 不叠）。搬的是**节点本身**（id 与内部结构原样）：`script-lobby.js` 照旧往 `#script-panel`
 * 里铺、`replay.js` 照旧往 `#run-list` 里铺——本文件只负责"搬到谁家"。
 *
 * 为什么开窗前先把三块都收回寄存格：`panelOpen` 会清空正文槽（再开一扇 = 覆盖），上一扇
 * 窗的内容会被摘掉。三块由这里统一持有引用，开窗前收回寄存格 ⇒ 任何一块都不会变成游离
 * 节点（游离节点上的 `document.getElementById` 找不到 id，`scriptRender` / `renderHeroPool`
 * 就会静默失效）。关窗键也走这里，收窗后三块回到寄存格。
 */
const LOBBY_WINDOWS = {
  script: {
    size: "S", title: "剧本", dialog: "script", panel: "script-panel",
    render: () => { if (typeof scriptRender === "function") scriptRender(); },
  },
  pool: {
    size: "L", title: "将池预设", dialog: "pool", panel: "hero-pool-panel", layout: "split",
    render: () => { renderPoolSlots(); renderHeroPool(); },
  },
  runs: {
    size: "M", title: "已有录像", dialog: "runs", panel: "run-list",
    render: () => { if (typeof loadRuns === "function") loadRuns(); },
  },
};

/** 三块节点的引用（首次开窗时按 id 抓一次；之后即使被搬进窗里也还认得）。 */
let lobbyWindowNodeCache = null;

function lobbyWindowNodes() {
  if (!lobbyWindowNodeCache) lobbyWindowNodeCache = {};
  for (const kind of Object.keys(LOBBY_WINDOWS)) {
    if (!lobbyWindowNodeCache[kind]) {
      lobbyWindowNodeCache[kind] = $(LOBBY_WINDOWS[kind].panel);
    }
  }
  return lobbyWindowNodeCache;
}

/** 把三块全部收回寄存格（窗没开时它们都该住在这里）。 */
function restashLobbyWindows() {
  const stash = $("lobby-window-stash");
  if (!stash) return null;
  for (const node of Object.values(lobbyWindowNodes())) {
    if (node && node.parentNode !== stash) stash.appendChild(node);
  }
  return null;
}

/** 打开（或覆盖）三扇窗里的那一扇：S / L / M 同一个壳，单槽位不叠。
 *
 * 开窗前先重画那一块：它可能刚从寄存格回来（上一次开窗被覆盖时被摘掉过），
 * 重画一次保证窗里看到的是**当前**状态，而不是上次关窗时的残留。
 */
function openLobbyWindow(kind) {
  const spec = LOBBY_WINDOWS[kind] || LOBBY_WINDOWS.script;
  restashLobbyWindows();
  if (typeof spec.render === "function") spec.render();
  const node = lobbyWindowNodes()[kind];
  return panelOpen({
    size: spec.size,
    title: spec.title,
    body: node ? [node] : [],
    layout: spec.layout,
    dialog: spec.dialog,
  });
}

(function wireLobbyWindows() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  };
  on("btn-lobby-script", () => openLobbyWindow("script"));
  on("btn-lobby-pool", () => openLobbyWindow("pool"));
  on("btn-lobby-runs", () => openLobbyWindow("runs"));
  // 将池摘要面板里的第二个入口（同一扇 L 窗）。
  on("btn-pool-open", () => openLobbyWindow("pool"));
  // 窗的关闭键：面板收起后把三块送回寄存格（否则它们会停在不可见的正文槽里）。
  on("script-confirm-close", restashLobbyWindows);
})();
