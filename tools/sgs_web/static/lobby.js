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

/** 点将列表：把服务端下发的启用池铺成选项（`id` 是值、名称是给人看的字）。 */
function fillHeroOptions() {
  const select = $("in-my-hero");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  for (const hero of state.heroCatalog) {
    const option = document.createElement("option");
    option.value = hero.id;
    option.textContent = hero.name;
    select.appendChild(option);
  }
  const known = state.heroCatalog.some((hero) => hero.id === previous);
  select.value = known ? previous : (state.heroCatalog[0] || {}).id || "";
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
}

/** 开关的**唯一**落点：关了 = 置灰选将列表 + 建局不带 `my_hero`（纯随机）。 */
function syncPointjiang() {
  const on = !!($("in-pointjiang") || {}).checked;
  const select = $("in-my-hero");
  if (select) select.disabled = !on;
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

async function startGame(event) {
  event.preventDefault();
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
