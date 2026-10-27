"use strict";

/* ============================================================================
 * PROTOYPE（一次性验证用，不是产品代码）· 飞行层 + 阶段名飘字 + 节奏公式
 *
 * 回答的问题（不是实现）：
 *   1. 起 / 落点锚点在真实牌桌上够不够用（含 闪电 判定区→判定区、跨座位、跨区）
 *   2. 起飞瞬间量一次坐标（不跟随节点重建）能不能扛住"每条事件整表重绘"
 *   3. 「delay = base_step + 时长 × (base_step / 100)」的节奏观感
 *   4. 阶段名飘字放"判定 / 装备区上方空隙"会不会和武将技能飘字打架
 *
 * 只在 URL 带 ?proto=1 时启动：不带参数时本文件什么都不做（烟测 / 正常对局零影响）。
 * 时长表在同目录 anim-proto.ini，改完刷新即生效。
 *
 * 用法：http://127.0.0.1:8080/?proto=1
 * 合成探针（控制台）：
 *   __animProto.fly({zone:"judge",seat:0}, {zone:"judge",seat:2}, {card_id:90001,name:"闪电"})
 *   __animProto.fly({zone:"equip",seat:1}, {zone:"hand",seat:0}, {card_id:90002,name:"赤兔"})
 *   __animProto.phase(0, "闪电移动到座位 2")
 * ========================================================================== */
(function () {
  let gated = false;
  try {
    gated = /[?&]proto=1\b/.test(String(window.location && window.location.search));
  } catch (error) {
    gated = false;
  }
  // 垫片 / 烟测里没有 location 或没有真 DOM：直接退出。
  if (!gated) return;
  if (typeof state === "undefined" || typeof document === "undefined") return;
  if (typeof applyEffectDelta !== "function" || typeof stepPlayback !== "function") return;

  /* ------------------------------------------------------------ 配置 + ini */

  const DEFAULTS = {
    base_step: 100,
    debug_scale: 1,
    flight_use: 180,
    flight_judge_reveal: 180,
    flight_draw: 220,
    flight_gain: 300,
    flight_discard: 300,
    flight_place_equip: 320,
    flight_place_judge: 320,
    flight_replace_out: 280,
    flight_stage_out: 0,
    fan_ms: 120,
    fan_px: 8,
    phase_float_ms: 1200,
  };
  const config = Object.assign({}, DEFAULTS);
  const configNotes = [];

  function parseIni(text) {
    const out = {};
    for (const rawLine of String(text || "").split(/\r?\n/)) {
      const line = rawLine.replace(/\s*[#;].*$/, "").trim();
      if (!line) continue;
      const at = line.indexOf("=");
      if (at < 0) continue;
      const key = line.slice(0, at).trim();
      const value = Number(line.slice(at + 1).trim());
      if (!Number.isFinite(value)) {
        configNotes.push(`${key} 不是数字，已忽略`);
        continue;
      }
      out[key] = value;
    }
    return out;
  }

  function loadConfig() {
    if (typeof fetch !== "function") return;
    fetch("anim-proto.ini", { cache: "no-store" })
      .then((response) => (response.ok ? response.text() : ""))
      .then((text) => {
        const parsed = parseIni(text);
        const unknown = Object.keys(parsed).filter((key) => !(key in DEFAULTS));
        Object.assign(config, parsed);
        for (const key of unknown) configNotes.push(`未知键 ${key}（拼错了？）`);
        notify(`飞行时间表已载入（${Object.keys(parsed).length} 项）`);
      })
      .catch(() => notify("anim-proto.ini 取不到，用内置默认"));
  }

  /* ------------------------------------------------------------ 小工具 */

  function byId(id) {
    return document.getElementById(id);
  }

  function rectOf(node) {
    if (!node || typeof node.getBoundingClientRect !== "function") return null;
    const rect = node.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height)) return null;
    return rect;
  }

  function mySeat() {
    const view = state.liveView || state.view;
    const seat = view && view.snapshot ? view.snapshot.seat : null;
    return seat === null || seat === undefined ? 0 : Number(seat);
  }

  function seatNode(seat) {
    const number = Number(seat);
    if (number === mySeat()) return byId("t-self-seat");
    return document.querySelector(`.seat.opponent[data-seat="${number}"]`);
  }

  function zonesNode(seat) {
    const number = Number(seat);
    if (number === mySeat()) return byId("t-self-zones");
    const node = seatNode(number);
    return node ? node.querySelector(".side-block") : null;
  }

  /** 区域坐标 → 锚点节点。`card` 用来把装备 / 判定落到**具体槽**，而不是整个格子。
   *
   * 摘取顺序 = 有这张牌就找这张牌的节点（来源侧）→ 否则按 slot / 第一个空槽（落点侧）
   * → 最后退回整个区域容器。三个缺项都不致命，只是精度下降。
   */
  function anchorFor(ref, card) {
    if (!ref) return null;
    const zone = ref.zone;
    const seat = Number(ref.seat);
    if (zone === "deck") {
      const node = byId("t-deck");
      return node ? node.parentElement : null;
    }
    if (zone === "discard") {
      const node = byId("t-discard");
      return node ? node.parentElement : null;
    }
    if (zone === "processing") {
      const box = byId("t-processing");
      if (!box) return null;
      if (card && card.card_id !== undefined) {
        const exact = box.querySelector(`[data-stage-key="${card.card_id}"]`);
        if (exact) return exact;
      }
      return box;
    }
    if (zone === "hand") {
      if (seat === mySeat()) {
        const hand = byId("t-hand");
        if (!hand) return null;
        if (card && card.card_id !== undefined) {
          const exact = hand.querySelector(`[data-card-id="${card.card_id}"]`);
          if (exact) return exact;
        }
        return hand;
      }
      const node = seatNode(seat);
      return node ? node.querySelector(".seat-foot span") : null;
    }
    if (zone === "equip") {
      const grid = document.querySelector(`.equip-grid[data-seat="${seat}"]`);
      if (!grid) return null;
      if (card && card.card_id !== undefined) {
        const exact = grid.querySelector(`[data-card-id="${card.card_id}"]`);
        if (exact) return exact;
      }
      if (card && card.slot) {
        const bySlot = grid.querySelector(`[data-slot="${card.slot}"]`);
        if (bySlot) return bySlot;
      }
      return grid;
    }
    if (zone === "judge") {
      const row = document.querySelector(`.judge-row[data-seat="${seat}"]`);
      if (!row) return null;
      if (card && card.card_id !== undefined) {
        const exact = row.querySelector(`[data-card-id="${card.card_id}"]`);
        if (exact) return exact;
      }
      const empty = row.querySelector(".judge-slot.empty");
      return empty || row;
    }
    return null;
  }

  /* ------------------------------------------------------------ 飞行层 */

  const LAYER_ID = "t-anim-flights";
  const PHASE_ID = "t-anim-phase";

  function layer(id, zIndex) {
    let node = byId(id);
    if (node) return node;
    const shell = byId("table-shell");
    if (!shell) return null;
    node = document.createElement("div");
    node.id = id;
    node.className = "anim-proto-layer";
    node.setAttribute("aria-hidden", "true");
    node.style.cssText =
      "position:absolute;inset:0;pointer-events:none;overflow:hidden;" + `z-index:${zIndex};`;
    shell.appendChild(node);
    return node;
  }

  function notify(text) {
    let node = byId("t-anim-proto-note");
    if (!node) {
      node = document.createElement("div");
      node.id = "t-anim-proto-note";
      node.style.cssText =
        "position:fixed;left:8px;bottom:8px;z-index:9999;padding:4px 8px;border-radius:6px;"
        + "background:rgba(32,26,18,.82);color:#f5efe3;font:12px/1.4 system-ui;pointer-events:none";
      document.body.appendChild(node);
    }
    node.textContent = `PROTOTYPE · ${text}`;
  }

  function flightNode(card, sourceNode) {
    // 有牌面就用真卡面（与舞台 / 手牌同一份绘制）；隐藏区只给牌背。
    if (card && card.card_id !== undefined && typeof cardFaceNode === "function") {
      const node = cardFaceNode(card, { static: true });
      node.classList.add("proto-flight-card");
      node.style.pointerEvents = "none";
      return node;
    }
    if (sourceNode && sourceNode.classList && sourceNode.classList.contains("card")) {
      const clone = sourceNode.cloneNode(true);
      clone.classList.add("proto-flight-card");
      clone.style.pointerEvents = "none";
      return clone;
    }
    const back = document.createElement("div");
    back.className = "card face unknown proto-flight-card";
    back.textContent = "?";
    back.style.pointerEvents = "none";
    return back;
  }

  /** 一次飞行：起点 / 终点都在**起飞那一刻**量一次，之后不跟随任何重绘。 */
  function spawnFlight(fromNode, toNode, card, index, count, duration) {
    const flyLayer = layer(LAYER_ID, 18);
    const shell = byId("table-shell");
    if (!flyLayer || !shell) return;
    const shellRect = rectOf(shell);
    const fromRect = rectOf(fromNode);
    const toRect = rectOf(toNode);
    if (!shellRect || !fromRect || !toRect) return;

    const node = flightNode(card, fromNode);
    // 飞行卡**保持卡面自己的尺寸**：锚点只决定中心点。之前拿锚点宽高当卡面宽高，
    // 判 / 装备槽（几十像素）会把牌面压扁、牌名溢出到卡外。
    node.style.position = "absolute";
    node.style.left = "0";
    node.style.top = "0";
    node.style.margin = "0";
    node.style.transformOrigin = "50% 50%";
    node.style.willChange = "transform, opacity";
    node.style.filter = "drop-shadow(0 6px 10px rgba(43,36,28,.28))";
    flyLayer.appendChild(node);
    const ownRect = rectOf(node) || { width: 88, height: 124 };
    const width = ownRect.width || 88;
    const height = ownRect.height || 124;
    // 小锚点（牌堆 / 弃牌计数）出来的牌，按自身尺寸略缩一点，免得盖满整行顶栏。
    const fromScale = fromRect.width < width * 0.6 ? 0.72 : 1;

    const startX = fromRect.left - shellRect.left + fromRect.width / 2 - width / 2;
    const startY = fromRect.top - shellRect.top + fromRect.height / 2 - height / 2;
    const endX = toRect.left - shellRect.left + toRect.width / 2 - width / 2;
    const endY = toRect.top - shellRect.top + toRect.height / 2 - height / 2;

    // N 张同时起飞时沿垂直方向散开，避免 10 张叠成一张。
    const spread = Number(config.fan_px) || 0;
    const offset = count > 1 ? (index - (count - 1) / 2) * spread : 0;
    const dx = endX - startX;
    const dy = endY - startY;
    const length = Math.max(1, Math.hypot(dx, dy));
    const normalX = (-dy / length) * offset;
    const normalY = (dx / length) * offset;

    node.style.left = `${startX + normalX}px`;
    node.style.top = `${startY + normalY}px`;

    const total = Math.max(0, Number(duration) || 0);
    const fan = count > 1 ? (Number(config.fan_ms) || 0) / count : 0;
    const startFraction = count > 1 ? (fan * index) / Math.max(1, total + fan * count) : 0;
    const anim = node.animate(
      [
        { transform: `translate(0px, 0px) scale(${fromScale})`, opacity: 1, offset: 0 },
        {
          transform: `translate(${dx * 0.55 + normalX * 0.2}px, ${dy * 0.55 + normalY * 0.2}px) scale(${(1 + fromScale) / 2})`,
          opacity: 1,
          offset: 0.55,
        },
        { transform: `translate(${dx}px, ${dy}px) scale(.55)`, opacity: 0.05, offset: 1 },
      ],
      {
        duration: total,
        delay: total * startFraction,
        easing: "cubic-bezier(.32,.72,.35,1)",
        fill: "forwards",
      },
    );
    anim.onfinish = () => {
      if (node.parentNode) node.parentNode.removeChild(node);
    };
    anim.oncancel = () => {
      if (node.parentNode) node.parentNode.removeChild(node);
    };
    return node;
  }

  /* ------------------------------------------------------------ 记录分类 */

  const BOX_LABELS = {
    PREPARE: "准备阶段",
    JUDGE: "判定阶段",
    DRAW: "摸牌阶段",
    PLAY: "出牌阶段",
    DISCARD: "弃牌阶段",
    FINISH: "结束阶段",
  };

  function flightKindOf(record) {
    if (!record || record.kind !== "delta" || record.delta_type !== "card_move") return null;
    const from = record.from_zone || {};
    const to = record.to_zone || {};
    const reason = record.reason;
    if (to.zone === "hand") return reason === "DRAW" ? "draw" : "gain";
    if (to.zone === "processing") return reason === "JUDGE" ? "judge_reveal" : "use";
    if (to.zone === "equip") return from.zone === "equip" ? "replace_out" : "place_equip";
    if (to.zone === "judge") return "place_judge";
    if (to.zone === "discard") {
      if (from.zone === "processing") return "stage_out";
      if (from.zone === "equip") return "replace_out";
      return "discard";
    }
    return null;
  }

  function rawDurationOf(kind) {
    if (!kind) return 0;
    const value = Number(config[`flight_${kind}`]);
    return Number.isFinite(value) ? value : 0;
  }

  function scale() {
    const base = Number(config.base_step) || 100;
    return base / 100;
  }

  function cardCountOf(record) {
    if (typeof liveMoveCount === "function") return Math.max(1, liveMoveCount(record));
    const cards = (record && record.cards) || [];
    return Math.max(1, cards.length || Number(record && record.card_count) || 1);
  }

  /** 这条记录要额外等多久（毫秒）：动画时长 × debug_scale，再算上 N 张的错峰。 */
  function durationOf(record) {
    const kind = flightKindOf(record);
    if (!kind) return 0;
    const count = cardCountOf(record);
    const fan = count > 1 ? Number(config.fan_ms) || 0 : 0;
    const raw = rawDurationOf(kind) + fan;
    return Math.round(raw * (Number(config.debug_scale) || 1));
  }

  /* ------------------------------------------------------------ 钩子：起飞 */

  const originalApplyEffectDelta = applyEffectDelta;

  applyEffectDelta = function protoApplyEffectDelta(record, cards) {
    const out = originalApplyEffectDelta.apply(this, arguments);
    try {
      planFlight(record);
    } catch (error) {
      notify(`飞行失败：${error && error.message}`);
    }
    return out;
  };
  try {
    window.applyEffectDelta = applyEffectDelta;
  } catch (error) {
    /* 某些环境里全局绑定不可写：钩子失效时下面会提示 */
  }

  function planFlight(record) {
    const kind = flightKindOf(record);
    if (!kind) return;
    const duration = durationOf(record);
    if (!duration) return;
    const from = record.from_zone || {};
    const to = record.to_zone || {};
    const cards = (record.cards || []).slice();
    const count = cardCountOf(record);
    // 起飞前量一次**源**：这一步还没重绘，手牌 / 舞台卡都还在原处。
    const fromNode = anchorFor(from, cards[0]);
    if (!fromNode) {
      notify(`找不到起点 ${from.zone}@${from.seat}`);
      return;
    }
    const fromRect = rectOf(fromNode);
    // 落点要等这一条记录重绘之后才有（新槽位 / 新舞台卡）：推到本轮任务之后量。
    window.setTimeout(() => {
      try {
        for (let index = 0; index < count; index += 1) {
          const card = cards[index] || (cards.length ? cards[cards.length - 1] : null);
          const toNode = anchorFor(to, card);
          if (!toNode) {
            notify(`找不到落点 ${to.zone}@${to.seat}`);
            break;
          }
          spawnFlight(
            { getBoundingClientRect: () => fromRect },
            toNode,
            card,
            index,
            count,
            duration,
          );
        }
      } catch (error) {
        notify(`落点失败：${error && error.message}`);
      }
    }, 0);
  }

  /* ------------------------------------------------------------ 钩子：阶段飘字 */

  function showPhaseFloat(seat, text) {
    const phaseLayer = layer(PHASE_ID, 17);
    const shell = byId("table-shell");
    const target = zonesNode(seat);
    if (!phaseLayer || !shell || !target) return null;
    const shellRect = rectOf(shell);
    const targetRect = rectOf(target);
    if (!shellRect || !targetRect) return null;
    const dsu = Number.parseFloat(
      getComputedStyle(shell).getPropertyValue("--dsu").trim() || "1",
    ) || 1;
    const node = document.createElement("div");
    node.className = "proto-phase-float";
    node.textContent = text;
    node.style.cssText =
      "position:absolute;transform:translate(-50%,-100%);white-space:nowrap;"
      + "font-size:calc(14 * var(--dsu,1px));font-weight:700;color:#8a6b2f;"
      + "text-shadow:0 1px 2px rgba(255,253,244,.9),0 0 6px rgba(255,253,244,.85);"
      + "padding:2px calc(8 * var(--dsu,1px));border-radius:999px;"
      + "background:rgba(255,252,242,.82);border:1px solid rgba(217,180,92,.55)";
    const left = targetRect.left - shellRect.left + targetRect.width / 2;
    const top = targetRect.top - shellRect.top - 6 * dsu;
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
    phaseLayer.appendChild(node);
    // 贴边的座位（自己的判定 / 装备区在最右）会把"XX阶段开始"顶出壳外：夹进壳内。
    const own = node.getBoundingClientRect();
    const overRight = own.right - (shellRect.right - 6);
    if (overRight > 0) node.style.left = `${left - overRight}px`;
    const overLeft = shellRect.left + 6 - own.left;
    if (overLeft > 0) node.style.left = `${left + overLeft}px`;
    node.animate(
      [
        { opacity: 0, transform: "translate(-50%,-100%) translateY(6px)" },
        { opacity: 1, transform: "translate(-50%,-100%) translateY(0)", offset: 0.18 },
        { opacity: 1, transform: "translate(-50%,-100%) translateY(0)", offset: 0.7 },
        { opacity: 0, transform: "translate(-50%,-110%)" },
      ],
      {
        duration: Math.max(
          200,
          (Number(config.phase_float_ms) || 1200) * (Number(config.debug_scale) || 1),
        ),
        easing: "ease-out",
        fill: "forwards",
      },
    ).onfinish = () => {
      if (node.parentNode) node.parentNode.removeChild(node);
    };
    return node;
  }

  function planPhase(record) {
    if (!record || record.kind !== "timing") return;
    const timing = String(record.timing || "");
    const match = /^PHASE_([A-Z]+)_START$/.exec(timing);
    if (!match) return;
    const label = BOX_LABELS[match[1]];
    if (!label) return;
    const seat = record.actor === null || record.actor === undefined ? mySeat() : Number(record.actor);
    showPhaseFloat(seat, `${label}开始`);
  }

  /* ------------------------------------------------------------ 钩子：节奏 */

  const originalStepPlayback = stepPlayback;

  stepPlayback = function protoStepPlayback(immediate) {
    const playback = state.playback;
    if (playback && playback.records && playback.index < playback.records.length) {
      const record = playback.records[playback.index];
      const base = Number(config.base_step) || 100;
      const extra = durationOf(record);
      // 公式：基准步长 + 动画时长 × (基准 / 100)。基准同时是下限与动画缩放因子。
      state.eventDelayMs = Math.round(base + extra * (base / 100));
      try {
        planPhase(record);
      } catch (error) {
        notify(`阶段飘字失败：${error && error.message}`);
      }
    }
    return originalStepPlayback.apply(this, arguments);
  };
  try {
    window.stepPlayback = stepPlayback;
  } catch (error) {
    /* 同上 */
  }

  /* ------------------------------------------------------------ 合成探针 */

  window.__animProto = {
    config,
    notes: configNotes,
    reload: loadConfig,
    fly(from, to, card) {
      const fromNode = anchorFor(from, null);
      const toNode = anchorFor(to, card || null);
      if (!fromNode || !toNode) return { ok: false, from: Boolean(fromNode), to: Boolean(toNode) };
      const duration = Math.round(
        Math.max(rawDurationOf("place_equip"), 240) * (Number(config.debug_scale) || 1),
      );
      spawnFlight(fromNode, toNode, card || null, 0, 1, duration);
      return { ok: true, duration };
    },
    phase(seat, text) {
      return Boolean(showPhaseFloat(seat, text || "测试阶段开始"));
    },
    anchors(seat) {
      return {
        seat: seatNode(seat),
        zones: zonesNode(seat),
        equip: document.querySelector(`.equip-grid[data-seat="${seat}"]`),
        judge: document.querySelector(`.judge-row[data-seat="${seat}"]`),
      };
    },
  };

  loadConfig();

  /* ------------------------------------------------------------ demo（?demo=1） */

  /** 合成三件真实对局里难以凑齐的事，**循环播放**（间隔 4s），便于随时截图：
   *  A 阶段飘字（判定 / 装备区上方）
   *  B 判定区 → 判定区（闪电换座位，跨座位同区）
   *  C 装备槽 → 手牌（顺 / 借刀，跨区跨座位）
   *  D 牌堆 → 手牌 ×3（摸牌的多张一起）
   * 只在 ?demo=1 时启动。
   */
  function demoDur(key) {
    const raw = Number(config[`flight_${key}`]) || 300;
    return Math.round(raw * (Number(config.debug_scale) || 1));
  }

  const DEMO_BEATS = [
    () => showPhaseFloat(mySeat(), "摸牌阶段开始"),
    () => {
      const from = anchorFor({ zone: "judge", seat: mySeat() }, null);
      const to = anchorFor({ zone: "judge", seat: (mySeat() + 2) % 4 }, null);
      if (!from || !to) return notify("demo B：判定区锚点缺失");
      spawnFlight(from, to, { card_id: 990001, name: "闪电", type: "trick" }, 0, 1, demoDur("place_judge"));
    },
    () => {
      const from = anchorFor({ zone: "equip", seat: (mySeat() + 1) % 4 }, null);
      const to = anchorFor({ zone: "hand", seat: mySeat() }, null);
      if (!from || !to) return notify("demo C：装备槽 / 手牌锚点缺失");
      spawnFlight(from, to, { card_id: 990002, name: "赤兔", type: "trick", slot: "horse_defense" }, 0, 1, demoDur("gain"));
    },
    () => {
      const from = anchorFor({ zone: "deck", seat: null }, null);
      const to = anchorFor({ zone: "hand", seat: mySeat() }, null);
      if (!from || !to) return notify("demo D：牌堆 / 手牌锚点缺失");
      for (let index = 0; index < 3; index += 1) {
        spawnFlight(from, to, { card_id: 990100 + index, name: "闪", type: "basic" }, index, 3, demoDur("draw"));
      }
    },
  ];

  function runDemo() {
    let beat = 0;
    const tick = () => {
      try {
        DEMO_BEATS[beat % DEMO_BEATS.length]();
      } catch (error) {
        notify(`demo 失败：${error && error.message}`);
      }
      beat += 1;
      notify(`demo 循环：A 飘字 / B 闪电 / C 装备→手牌 / D 摸 3 张（每 4s 一拍）· 当前 ${["A", "B", "C", "D"][(beat - 1) % 4]}`);
      window.setTimeout(tick, 4000);
    };
    tick();
  }

  let demoStarted = false;
  function maybeStartDemo() {
    if (demoStarted) return;
    let wantsDemo = false;
    try {
      wantsDemo = /[?&]demo=1\b/.test(String(window.location.search));
    } catch (error) {
      wantsDemo = false;
    }
    if (!wantsDemo) return;
    const view = state.view;
    if (!view || !view.snapshot || !byId("t-seats")) return;
    if (!byId("t-seats").children.length) return;
    demoStarted = true;
    runDemo();
  }

  const originalCommitView = commitView;
  commitView = function protoCommitView(body) {
    const out = originalCommitView.apply(this, arguments);
    maybeStartDemo();
    return out;
  };
  try {
    window.commitView = commitView;
  } catch (error) {
    /* 同上 */
  }

  notify("存活：飞行层 + 阶段飘字 + 节奏公式已接管（?proto=1）");
})();
