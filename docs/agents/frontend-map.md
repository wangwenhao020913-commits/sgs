# 前端地图（Frontend Map）

`tools/sgs_web/static/` 的导航图：**只回答"改哪份文件"**，不复述产品行为。行为真源是
`开发文档/04-AI与交互设计.md`、各 effort 的 spec 与 `tests/`；脚本清单的**唯一真源**是
`index.html` 的 `<script src>` 顺序（页面与烟测共用）。下表按同一顺序解释职责，顺序判据
仍只在 `index.html` 与 `tests/test_web_static.py`。

## 脚本职责（按 `index.html` 顺序）

| 文件 | 职责 |
|---|---|
| `core.js` | 全局 `state`、`$`、`post` / `api`、`show` 导航、`bareCardRef`、牌名 / 描述 / 阶段 / 技能标签等共享纯函数。不含渲染与请求流程。 |
| `lobby.js` | 开局页表单（人数 / 点将 / 牌堆档位）、`POST /api/sessions`、`loadHeroCatalog` / `loadDeckCatalog`。 |
| `session.js` | 会话生命周期与派生：`applyView` / `commitView`、事件回放 `stepPlayback`、舞台三组、live view、轮询（见下节）。 |
| `candidates.js` | 候选索引的**只读**查询：`cardEntry` / `cardSelectable` / `isLegalCombo` / `autoCombo`；不重建索引。 |
| `card-view.js` | 卡面绘制（花色 / 点数 / 牌名 / 类型行 / 悬停全文），纯展示。 |
| `card-face.js` | 卡面状态的纯函数：选中 / 置灰 / 槽位、`isProvide` / `provideSlotIndex`。 |
| `seat-view.js` | 座位卡的节点形状：武将块、判定 / 装备槽、状态类与悬停全文。 |
| `table.js` | 环桌布局与表体渲染：`tableViewForRender`、`renderTable`、`renderProcessing`、`renderSkills`。 |
| `effects.js` | 动效层：目标线、行动者高亮、体力闪、技能名飘字 / 白光；寿命判定是纯函数。 |
| `preactivation.js` | 预发动清单、转化技来源模式、清单到 Action 的组装。 |
| `interaction.js` | 点选：卡面芯片、点牌设焦点、点座位选目标、弃牌点选。 |
| `pending.js` | 询问按种类分派到专用面、提示条右端动作、取消 / 不支持面 fail-closed。 |
| `options.js` | 选项按钮组、选择面槽位、「提供一张 B」、Action 文案与右端动作下标。 |
| `reveal.js` | 亮牌模板（五谷 / 遗计复用）与目标条；判据仍住 `modal.js`。 |
| `modal.js` | 询问弹窗框架与武将详情壳：`selectionEnabled`、`stepDialogKind`、`DIALOG_KINDS`。 |
| `hero-detail.js` | 武将详情正文（技能清单）与开合绑定。 |
| `arrange.js` | 观星双行排列：布局、点选移动、提交载荷。 |
| `answer.js` | 倒计时三色、`buildAction`、`submitAction`（`POST …/answer`）、`clearSelection`。 |
| `log.js` | 事件流三档（简易 = 服务端叙事 / 完整重点 / 完整全部）。 |
| `replay.js` | 回放页：录像列表、按记录重建座位全区、询问面板与播放控制。 |
| `main.js` | `wire()` 事件绑定、回放步长按钮、启动；必须是最后一份脚本。 |

`index.html` 是 DOM 骨架 + 覆盖层（`#t-target-lines` / `#t-skill-floats`）+ 脚本顺序；
`style.css` 是样式与设计单位，动效时长 / 尺寸的机械断言在 `tests/test_web_static.py`。

## 舞台 / live view / 轮询 / 动效入口

| 入口 | 文件 | 用途 |
|---|---|---|
| `applyEffectDelta(record, …)` | `session.js` | 一条记录 → 舞台项：处理中 / 展示 / 弃牌的落位、换组与淡出锚点。 |
| `applyRevealDelta(record, …)` | `session.js` | `card_reveal` / `pending.reveal` → 亮牌展示项。 |
| `mergeEffectCards(cards)` | `session.js` | 把 `snapshot.processing` 合并回舞台，保留承载帧 kind 与来源区。 |
| `stageLane(item, now)` | `session.js` | **纯函数**寿命判定：`visible` / `fading` / `gone`；测试直接喂 `now`。 |
| `tableViewForRender()` | `table.js` | 回放期读逐事件派生的 `state.liveView`（`pending` 收起），其余读权威视图。 |
| `stepPlayback(immediate)` | `session.js` | 回放一条记录：日志 → 效果 / 亮牌 → `applyLiveDelta` → 重绘 → 动效 → 技能事件。 |
| `applyLiveDelta(record, view)` | `session.js` | 把**可派生**的可见变化写进 live view（体力 / 手牌数 / 装备 / 阶段 …）。 |
| `poll()` | `session.js` | 2s 静默轮询；idle 只更新时钟，不重绘、不清选择；失败静默。 |
| `drawTargetLines(actor, targets, now)` | `effects.js` | 每个目标一条金色 SVG 线（跳过自己 / 阵亡）；端点只读一次布局。 |
| `playSkillEvent(event, now)` | `effects.js` | 技能名飘字 + 白光；`targets` 非空才画线。 |
| `fireSkillEventsThrough(seq)` | `effects.js` | 回放到某条记录时释放 `state.skillEvents` 队列。 |
| `syncTransientEffects()` | `effects.js` | 座位重建后补回仍在播的高亮 / 体力闪 / 白光；顺手收掉过期目标线。 |

## 页面层

- **开局页** `#page-lobby`：`lobby.js` + `core.js` 的目录请求；建局成功后 `applyView`。
- **牌桌** `#page-table`：`session.js` 驱动数据，`table.js` / `seat-view.js` / `card-view.js`
  画表体，`pending.js` / `options.js` / `modal.js` / `reveal.js` 画询问面。
- **回放页** `#page-replay`：`replay.js` 单独重建座位全区；**不经过** `stepPlayback`，也不播
  目标线 / 技能名 / 白光 / 高亮 / 体力闪。

## 测试 / 烟测入口

- `tests/support/ui_smoke.mjs`：`node:vm` + 最小 DOM 垫片，按 `index.html` 顺序跑**真脚本**，
  喂真服务端响应与合成载荷。垫片的 `El.children` 是 **HTMLCollection 形状的活视图**
  （`length` / 下标 / `item` / 迭代，无数组方法）——测试遍历一律 `Array.from(...)` 或展开；
  集合断言用 `setEquals`，纯函数断言经 `ui.stageLane` 喂 `now`。
- `tests/support/ui_bridge.js`：把前端入口按名字挂到 `globalThis.__bridge`（烟测里叫 `ui`），
  缺项容错；`applyView` / `submitAction` 默认先把回放跑完，要看中间态用
  `applyViewRaw` / `submitActionRaw` 配 `stepPlayback`。
- `tests/smoke-web.sh`：临时服务 + `ui_smoke.mjs` + 收尾（约 4 秒）。**给人用的快回路**，
  不在 `tests/run_all.sh` 里。
- `tests/test_web_static.py`：静态清单（脚本顺序 / strict）、身份配比副本、CSS 设计单位，
  以及 `children` 数组方法禁令。
- `tests/test_web.py`：接入层纯函数与真会话 / HTTP 用例（不 `serve_forever`）。
- `tests/test_web.sh`：端到端门禁——真服务、完整 8 人局、超时代答、静态资源 `node --check`，
  并跑一遍 `ui_smoke.mjs`。

## 实机验收

改动 DOM API 的 UI 票在 `resolved` 前走一遍真实浏览器（`bsk`）：配方见
`docs/agents/acceptance-web.md`；评审的判断题口径见 `CODING_STANDARDS.md` §4 / §6。
