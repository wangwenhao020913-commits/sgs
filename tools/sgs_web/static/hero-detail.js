/* SGS 前端 · 武将详情模态（U1-06）。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 *
 * 票面（spec §3.1–3.2）：
 *  - 右键**武将块** = 详情；右键装备 / 判定 / 手牌不动作，牌桌整体屏蔽浏览器右键菜单；
 *  - 详情里武将技能在上、分割线、装备技能在下；技能多 / 描述长时滚轮翻阅；
 *  - 背景 / ESC 可关；打开时其他窗口不可操作（模态），但倒计时不暂停、模态内不显示；
 *  - 阵亡武将也能点开。
 *
 * 纪律：
 *  - 技能清单读服务端为**每个座位**下发的 `player.skills`（`projection.seat_skill_display`）；
 *    它是只读展示清单（武将牌 + 装备区 + 虚拟装备），不带可用性 / 当前询问 / 本人手牌；
 *  - 详情**不裁决规则**：只画名字、类别、服务端下发的描述全文；
 *  - 打开详情不改任何询问 / 选择态，关闭也不提交 action——它是纯本地一层窗口。
 */
"use strict";

/** 一个座位的详情技能清单。
 *
 * 新载荷每个座位都带 `player.skills`（含对手与阵亡者）；旧载荷 / 合成视图没有这个键时
 * **只对自己的座位**退回顶层 `view.skills`（旧形状里它只含自己的技能），其余返回空——
 * 前端不自己从武将 id 猜别人的技能（那是第二条真源）。
 */
function detailSkillsFor(player, view) {
  if (player && Array.isArray(player.skills)) return player.skills;
  const ownSeat = view && view.snapshot ? view.snapshot.seat : null;
  if (player && ownSeat !== null && Number(player.seat) === Number(ownSeat)) {
    return view.skills || [];
  }
  return [];
}

/** 右键接线：**武将块**才开详情；其余节点由牌桌外壳统一屏蔽浏览器菜单。
 *
 * `seatOf` 可以是座位号，也可以是一个取座位号的函数（自己的静态块用后者）。
 */
function bindHeroDetailContext(node, seatOf) {
  if (!node) return;
  const resolve = typeof seatOf === "function" ? seatOf : () => seatOf;
  node.addEventListener("contextmenu", (event) => {
    if (event && event.preventDefault) event.preventDefault();
    if (event && event.stopPropagation) event.stopPropagation();
    openHeroDetail(resolve());
  });
}

function detailSkillRow(skill) {
  const row = document.createElement("div");
  row.className = "detail-skill";
  row.setAttribute("data-skill-id", skill.skill_id);
  const head = document.createElement("div");
  head.className = "skill-head";
  const name = document.createElement("span");
  name.className = "skill-name";
  name.textContent = skill.name || skill.skill_id;
  const type = document.createElement("span");
  type.className = "skill-type";
  type.textContent = skillKindLabel(skill);
  head.appendChild(name);
  head.appendChild(type);
  const text = document.createElement("p");
  text.textContent = skillDescription(skill);
  row.appendChild(head);
  row.appendChild(text);
  return row;
}

function detailSection(className, title, skills, emptyText) {
  const section = document.createElement("section");
  section.className = `detail-section ${className}`;
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.appendChild(heading);
  if (!skills.length) {
    const empty = document.createElement("div");
    empty.className = "detail-empty";
    empty.textContent = emptyText;
    section.appendChild(empty);
    return section;
  }
  for (const skill of skills) section.appendChild(detailSkillRow(skill));
  return section;
}

/** 详情正文：座位头（势力 / 名称 / 身份 / 座位 · 存活 / 手牌 / 体力）+ 两段技能。 */
function renderHeroDetailBody() {
  const body = $("t-modal-body");
  if (!body) return;
  // `#t-options` 可能被预发动 / 排列模态搬进了正文；详情替换正文前先把它送回提示条，
  // 否则 `innerHTML = ""` 会把这个共享节点从文档里摘掉（之后 `$("t-options")` 找不到）。
  showOptionsHome();
  // 滚动位置只在**同一座位**的重新渲染时保留；换座位（右键另一块武将）从顶部开始。
  const first = body.children[0];
  const previousSeat = first && first.getAttribute ? first.getAttribute("data-seat") : null;
  const sameSeat = previousSeat !== null
    && String(state.modal.heroSeat) === previousSeat;
  const scrollTop = sameSeat ? (Number(body.scrollTop) || 0) : 0;
  body.innerHTML = "";
  const view = state.view;
  const seat = state.modal.heroSeat;
  if (!view || !view.snapshot || seat === null) return;
  const player = playerAt(view, seat);
  if (!player) return;

  const root = document.createElement("div");
  root.className = "hero-detail";
  root.setAttribute("data-seat", String(player.seat));
  const head = document.createElement("div");
  head.className = "detail-head";
  if (player.kingdom_label) {
    const kingdom = document.createElement("span");
    kingdom.className = `kingdom ${player.kingdom || ""}`;
    kingdom.textContent = player.kingdom_label;
    kingdom.setAttribute("title", `势力：${player.kingdom_label}`);
    head.appendChild(kingdom);
  }
  const name = document.createElement("span");
  name.className = "hero-name";
  name.textContent = heroNameOf(player, view);
  head.appendChild(name);
  const badge = identityBadgeView(player, (view.snapshot.players || []).length > 2);
  if (badge.label) {
    const identity = document.createElement("span");
    identity.className = badge.className;
    identity.textContent = badge.label;
    identity.setAttribute("title", badge.title);
    head.appendChild(identity);
  }
  const meta = document.createElement("span");
  meta.className = "detail-meta";
  meta.textContent = `座位 ${player.seat} · ${player.alive ? "存活" : "阵亡"}`
    + ` · 手牌 ${Number(player.hand_count) || 0}`
    + ` · 体力 ${Math.max(0, Number(player.hp) || 0)}/${Math.max(0, Number(player.hp_max) || 0)}`;
  head.appendChild(meta);
  root.appendChild(head);

  const skills = detailSkillsFor(player, view);
  const heroSkills = skills.filter((skill) => skill.carrier !== "EQUIPMENT");
  const equipSkills = skills.filter((skill) => skill.carrier === "EQUIPMENT");
  root.appendChild(detailSection("hero-skills", "武将技能", heroSkills, "（无武将技能）"));
  const divider = document.createElement("div");
  divider.className = "detail-divider";
  divider.setAttribute("role", "separator");
  root.appendChild(divider);
  root.appendChild(detailSection("equip-skills", "装备技能", equipSkills, "当前没有装备技能。"));
  body.appendChild(root);
  // 同一座位保留滚动位置；换座位显式回顶部（清空正文在真 DOM 里会归零，垫片不会）。
  body.scrollTop = scrollTop;
}

/** 打开一个座位的详情：只记本地座位号并交给共享模态（`syncModal`）统一开窗。
 *
 * 回放期间不开（回放页没有这份详情数据，且播放是严格顺序的）。
 * **不动** `state.modal.dismissed`：它属于底下的排列模态（“收起后给查看排列入口”）。
 */
function openHeroDetail(seat) {
  if (state.playback || !state.view || !state.view.snapshot) return;
  if (seat === null) return;
  if (!playerAt(state.view, seat)) return;
  state.modal.heroSeat = Number(seat);
  syncModal();
}

/** 关闭详情：清掉本地座位号，再把位置交还给可能还开着的询问模态（预发动 / 排列）。
 *
 * 不提交任何 action、不动 `state.preactivation` / `state.arrange` / `state.modal.dismissed`
 * （排列的“收起”状态因此保留：关闭详情后仍只给「查看排列」入口）。
 * 刻意**不清** `state.modal.kind`：调用 `syncModal` 时它仍是 `"hero"`（`hideModalDom`
 * 只把 `open` 置 false），`syncModal` 据此看到 `wasHero`、不重置底下的 dismissed。
 */
function closeHeroDetail() {
  state.modal.heroSeat = null;
  hideModalDom();
  showOptionsHome();
  syncModal();
}

/** 牌桌的右键接线：外壳屏蔽浏览器菜单；武将块（自己的静态块 + 环上每个对手块）开详情。
 *  两类块共用 :func:`bindHeroDetailContext`，对手块在 `seat-view.js:heroBlockNode` 里接。
 */
function wireHeroDetail() {
  const shell = $("table-shell");
  if (shell) {
    shell.addEventListener("contextmenu", (event) => {
      if (event && event.preventDefault) event.preventDefault();
    });
  }
  const ownSeat = () => (state.view && state.view.snapshot ? state.view.snapshot.seat : null);
  bindHeroDetailContext($("t-self-hero"), ownSeat);
}
