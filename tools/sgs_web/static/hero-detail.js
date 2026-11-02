/* SGS 前端 · 武将详情模态（U1-06）。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 *
 * 票面（spec §3.1–3.2）：
 *  - 右键**武将块** = 详情；右键装备 / 判定 / 手牌不动作，牌桌整体屏蔽浏览器右键菜单；
 *  - 详情里武将技能在上、分割线、装备技能在下；技能多 / 描述长时滚轮翻阅；
 *  - 关闭按钮 / ESC 只关它自己（U2-06 起它是**唯一**可叠加的检视窗：盖在询问弹窗上，
 *    关掉后底下的询问弹窗原样恢复）；倒计时不暂停、窗口内不显示；
 *  - **任何时候**都能开：询问态、牌桌事件回放（`state.playback`）、无 pending 的快照态；
 *    回放**页**没有牌桌武将块、也不绑定这个入口；
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

/** 一条技能行：名称 / ☆ / 类别 / **三态徽标** / 描述全文。
 *
 * M6 票 19（spec「UI：☆ 技能标识、失效 / 失去标识」）：
 * - ☆ = 觉醒后**获得**的技能（服务端 `star`）；
 * - `state = suppressed` ⇒ 徽标「失效」+ `.state-suppressed`："有但被挡住"；
 * - `state = lost` ⇒ 徽标「失去」+ `.state-lost`（CSS 划掉名字）："没了"；
 * - `active` ⇒ 不挂徽标（不制造噪声）。
 *
 * 判据全读服务端字段，本函数不裁决技能是否生效。
 */
function detailSkillRow(skill) {
  const row = document.createElement("div");
  const state = (skill && skill.state) || "active";
  const stateLabel = skillStateLabel(skill);
  row.className = `detail-skill state-${state}`;
  row.setAttribute("data-skill-id", skill.skill_id);
  row.setAttribute("data-state", state);
  const head = document.createElement("div");
  head.className = "skill-head";
  const name = document.createElement("span");
  name.className = "skill-name";
  name.textContent = skill.name || skill.skill_id;
  head.appendChild(name);
  const star = skillStarLabel(skill);
  if (star) {
    const mark = document.createElement("span");
    mark.className = "skill-star";
    mark.textContent = star;
    mark.setAttribute("title", "☆ 技能：觉醒后获得");
    head.appendChild(mark);
  }
  const type = document.createElement("span");
  type.className = "skill-type";
  type.textContent = skillKindLabel(skill);
  head.appendChild(type);
  if (stateLabel) {
    const badge = document.createElement("span");
    badge.className = `skill-state ${state}`;
    badge.textContent = stateLabel;
    badge.setAttribute(
      "title",
      state === "lost" ? "失去：技能已不在，且不可恢复" : "失效：技能还在，作用域到期即恢复",
    );
    head.appendChild(badge);
  }
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
  // U2-06 起详情窗口不再承载询问内容（`#t-options` 留在 `#t-dialog` 里），正文清空不会
  // 摘掉共享节点。滚动位置只在**同一座位**的重新渲染时保留；换座位从顶部开始。
  const first = body.children[0];
  const previousSeat = first && first.getAttribute ? first.getAttribute("data-seat") : null;
  const sameSeat = previousSeat !== null
    && String(state.modal.heroSeat) === previousSeat;
  const scrollTop = sameSeat ? (Number(body.scrollTop) || 0) : 0;
  body.innerHTML = "";
  // 与 renderTable 同一份视图：牌桌事件回放期间读 live view（pending 收起、快照已派生），
  // 不读旧的 state.view，否则详情会停在上一问的画面。
  const view = tableViewForRender();
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
    + ` · 手牌：${handReading(player)}`
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

/** 打开一个座位的详情：只记本地座位号并交给 `syncHeroModal` 开窗。
 *
 * 牌桌事件回放期间也开：读 `tableViewForRender()`（live view），不是拒绝 `state.playback`。
 * **不动**询问弹窗（`#t-dialog`）的任何状态：详情是叠加的检视窗，关掉它时底下原样恢复。
 */
function openHeroDetail(seat) {
  // 牌桌事件回放期间 `tableViewForRender` 把 pending 收起，但武将块仍是当前可见的
  // 座位；详情应跟这张表走（`state.liveView`），不能因为 playback 就拒绝。
  const view = tableViewForRender();
  if (!view || !view.snapshot) return;
  if (seat === null || seat === undefined) return;
  if (!playerAt(view, seat)) return;
  state.modal.heroSeat = Number(seat);
  syncHeroModal();
}

/** 关闭详情：清掉本地座位号、收起 `#t-modal`。
 *
 * 不提交任何 action、不动 `state.preactivation` / `state.arrange` / 询问弹窗的折叠状态
 * ——排列的「收起」因此保留：关闭详情后仍只给「查看排列」入口。
 */
function closeHeroDetail() {
  state.modal.heroSeat = null;
  syncHeroModal();
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
