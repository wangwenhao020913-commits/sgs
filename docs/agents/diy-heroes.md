# 自制武将（DIY Heroes）：外部作者入口

**读者**：仓外的人——你还没有本仓的提交权限（甚至还没 clone），但你想给 SGS 加一张自己的武将牌。

**本文是出口**：选路 → 把入口 skill 装进你的 agent → 改哪几个文件 → 跑什么门禁 → 版权边界 →
提案流程。**一条技能该怎么读、怎么落形、什么时候必须停，不在这里**——判据全在
`docs/agents/skill-authoring.md`（六问流水线 + 13 条硬停判据 + 一将一票），本文只负责"门在哪"。

**一句话现状**：说明书写得很全（`docs/agents/skill-authoring.md` + 四份子文档 + 三份只读工具），
一直缺的是"外人从哪进"。本文补这一块；"今天能做什么、不能做什么"在第 1 节写死，不美化。

## 1. 两条路：先选一条

| 路 | 你要写什么 | 要写 Python 吗 | 今天成立吗 |
|---|---|---|---|
| **A. 只改数据**（复用已登记的技能） | 武将表 JSON + 包注册一行 | 不写**新**技能实现 | 成立：条目可用**显式 `skill_id`** 复用 `CATALOG` 里**任何**已登记的技能（含**别的武将**名下的，见下面的「诚实边界」） |
| **B. 写新技能**（新的 `SkillDef`） | 技能实现 + 登记 + 数据引用 + 测试 | 要 | 成立：走 `docs/agents/skill-authoring.md` 的六问流水线 |

先记住一件事：**不写新技能实现**的极限是"这张牌挂的全是已经写好的技能"——路线 A 就是这
个极限。路线 A 的"只改数据"指的是**不写技能实现**；显式 `skill_id`（票 04）让"给你的新
武将挂一条别人已经写好的技能"成立。想要**一条全新的机制**，仍只有路线 B。

### 路线 A：只改数据，复用已登记的技能

1. **武将表**：编辑 `src/sgs/data/files/heroes.homebrew.json`（自制武将的既有包），或照它的
   形状在同一个目录里新建一份（文件名形如 heroes.你的包 id.json；`schema_version` /
   `pack_id` / `heroes` 三段结构照抄，`enabled: true` 才会进池）。
2. **一行注册**：新建包要在 `src/sgs/data/loader.py` 的 `HERO_SUPPLEMENT_PACK_IDS` 里追加
   你的 `pack_id`；改现有包不用加这一行。
3. **技能定义必须已经存在**：加载期只对 `enabled: true` 的武将核技能引用闭合——引用不在
   `CATALOG` 里就报 `[hero.skill_missing]`（规则来源见 `src/sgs/data/loader.py` 的
   `hero_skill_id` 与引用校验那一段）。

> **诚实边界（diy-heroes 票 04 起）**：技能条目可以只写**短 id**，对外技能 id 由
> `hero_skill_id` 一处派生为 `hero.<武将 id>.<短 id>`；也可以写**显式 `skill_id`**，直接指向
> 技能目录 `CATALOG`（`src/sgs/skills/catalog.py` 的模块导入期闭集）里**任何一条已登记**的
> 技能——包括**别的武将**名下的。把界关羽的【武圣】挂到你的自制武将牌上，条目就这么写：
>
> ```json
> {"id": "wusheng", "skill_id": "hero.jie_guanyu.wusheng", "name": "武圣", "skill_type": "NORMAL"}
> ```
>
> 派生只读数据层唯一的那一处（`hero_card_skill_id`）：`id` 仍是条目的行键（不参与拼
> 全限定 id），`skill_type` 要与被引用定义的 `SkillType` 对账。显式引用**只做引用、
> 不造新技能**，三条 fail-closed 校验：id 不在 `CATALOG` 里报
> `[heroes.skill_id_not_found]`；类型对不上报 `[heroes.skill_id_type_mismatch]`；同一武将内
> 两条条目解析到同一条技能报 `[heroes.skill_id_duplicate]`（**跨武将**复用同一条不算撞车）。
> ⇒ 路线 A 今天能做出"只改数据、不写技能实现"的新武将牌；要一条**新机制**，仍须走路线 B。

### 路线 B：写新技能（新的 `SkillDef`）

1. **技能实现**：进对应的**族模块**（例如 `src/sgs/skills/heroes.py`）写一条
   `SkillDef`，并放进该族的 `*_skill_definitions()`；`src/sgs/skills/catalog.py` 的 `CATALOG`
   在导入期把各族清单并成**唯一**登记处。
2. **数据引用**：武将表里给这条技能写一条短 id 条目；条目的 `skill_type` 会与定义一侧对账。
3. **文案**：`tools/sgs_web/descriptions.json` 的 `skills` 段键集必须**恰好等于** `CATALOG`
   （缺一条红、多一条也红，见 `tests/test_web.py`）；写你自己组织的描述文本，别整段抄官方牌面。
4. **过程**：先走 `docs/agents/skill-authoring.md` 的六问，把 `### 询问剧本` 交给维护者签；
   命中 13 条硬停判据之一就停，交裁定票（见第 7 节）。

## 2. 装 skill 到你的 agent

入口 skill 就在仓里：`skills/hero-skill/SKILL.md`（frontmatter 里 `name: hero-skill`）。它**只
指路**——判据全在本仓文档，所以它必须与文档同仓分发；装法按你的 agent 选一行：

| 你的 agent | 怎么装 | 装完怎么用 |
|---|---|---|
| **DSH** | 把 `skills/hero-skill/SKILL.md` 所在的那个目录整个拷到 DSH 的用户级 skills 目录：`cp -r skills/hero-skill "$HOME/.dsh/skills/"` | 新会话里 `/hero-skill` 即可触发 |
| **Claude Code** | 同一个目录拷进你项目的 .claude/skills/hero-skill/（或用户级 skills 目录） | 同为 `SKILL.md` + YAML frontmatter，可直接复用 |
| **Codex / Cursor 等没有 skill 机制的** | 在项目的 `AGENTS.md`（或等价规则文件）里加一行指针："改 SGS 武将技能前先读 `docs/agents/skill-authoring.md`" | agent 会按指针自己读文档 |
| **纯人** | 什么都不装，直接读 `docs/agents/skill-authoring.md` | — |

## 3. 最小环境

```bash
bash tools/setup-dev.sh                                  # 新机器一次性（幂等）：换行口径 + git hook + .venv + 环境预检
.venv/bin/pytest -q tests/test_skill_authoring_docs.py    # 文档与代码是否还同步（几秒）
bash tests/run_all.sh                                     # 唯一门禁（≈8 分钟）；改了 src/ tests/ 走全量档
```

- 平台：**Linux / macOS / WSL**（门禁是 bash + `.venv/bin/` 路径，Windows 原生跑不了）；Python 3.14+。
- `setup-dev.sh` 不跑 = **门禁静默不跑**：git hook 是本地配置，不随 clone 走。
- 门禁只认仓内 `.venv/bin/pytest`，不回退系统 pytest：别用裸 `pytest` / `python`（本机可能不在 PATH）。
- 只改文档时，提交 hook 走**快速文档检查**档；改了源码或测试才跑全量
  （分级在 `tools/gate-staged.sh`）。

## 4. 要改的文件

| 文件 / 步骤 | 路线 A（只改数据） | 路线 B（写新技能） |
|---|---|---|
| 武将表 JSON（`src/sgs/data/files/heroes.homebrew.json` 一类） | 要：加武将条目，`enabled: true` | 要：同左，技能引用新短 id |
| 包注册（`HERO_SUPPLEMENT_PACK_IDS`，在 `src/sgs/data/loader.py`） | 新建包才加一行 | 新建包才加一行 |
| 技能定义（`src/sgs/skills/catalog.py` 的 `CATALOG`） | **不写**：必须已存在 | 要：族模块里写 `SkillDef` + 登记 |
| `tools/sgs_web/descriptions.json` | 不用改（引用的技能已有条目） | 要：`skills` 段加一条，键集与 `CATALOG` 相等 |
| 测试 | 要一份：包能加载 + 武将进池（参考 `tests/test_hero_dealing.py` 的池断言） | 要：单文件回归（红→绿）+ 边界断言（`docs/agents/skill-authoring/edge-cases.md`） |

**照抄现成样板**：`examples/diy-hero/heroes.diy.json` 是一张只复用已登记技能的自制牌
（1 张武将 / 1 条显式 `skill_id`），旁边的 `examples/diy-hero/README.md` 写了两种接法——
拷进 `src/sgs/data/files/` 再注册一行（会动下面的池计数），或只在测试里注入、不进池。
它本身**不进**官方池，所以池计数仍是 15 处。

**加进官方池就要对账 15 处计数。** `HERO_SUPPLEMENT_PACK_IDS` 里加一行会改动启用武将数、
真名数、技能数与形态格，而它们被**写死**在 15 个站点、7 张登记表里（`tests/test_hero_catalog.py` /
`tests/test_hero_dealing.py` / `tests/test_web.py` / `tests/support/ui_smoke.mjs`）。先跑：

```bash
.venv/bin/python tools/pool_census.py --check
```

一致时输出以「一致（15 处站点 / 7 张登记表）」结尾、退出 0；不一致时它把**该改哪个站点的哪个
数字**逐条列出来。票 06 计划把这一步自动化（`--write`），落地前先用手跑。

## 5. 常见错误

| 报什么 | 为什么 | 怎么修 |
|---|---|---|
| `[hero.skill_missing]` | 短 id 派生的 `hero.<武将 id>.<短 id>` 不在 `CATALOG` 里（墙 1） | 用**同一武将 id** 下已登记的短 id；或改写显式 `skill_id` 指向 `CATALOG` 里任何已登记的技能；真要一条新技能就写 `SkillDef`（路线 B） |
| `[heroes.skill_id_not_found]` | 显式 `skill_id` 指向的技能不在 `CATALOG` 里（拼错，或想引用的技能还没实现） | 改成真实存在的全限定 id（`tools/dump_catalog.py` 可反查）；新技能走路线 B |
| `[heroes.skill_id_type_mismatch]` | 条目声明的 `skill_type` 与被引用定义的 `SkillType` 不一致（只对显式引用查） | 照被引用的技能改条目：限定 / 觉醒技别写成 `NORMAL` |
| `[heroes.skill_id_duplicate]` | 同一武将的两条条目解析到**同一个**全限定技能 id | 删掉重复条目，或让它指向另一条技能（**跨武将**复用同一条不算撞车） |
| `[heroes.placement_missing]` | 技能定义标了"往武将牌上放牌"（`SkillDef.places_on_hero_card`），数据侧没写 `placement` 三项声明 | 补 `visibility` / `return_timing` / `extra_effect` 三项（必写、无默认） |
| `[heroes.granted_by_unknown]` | 条目的 `granted_by` 指向的短 id 不在**同一武将**的技能清单里 | 改成同武将的觉醒技 / 限定技短 id（☆ 技能只能由一次性技能获得） |
| `[decks.unknown_profile]` | 内容包的 `/deck_profile` 不是它自己 `decks` 里的一档（改档位名没同步最常见） | 让 `/deck_profile` 等于 `decks` 的某个键；照 `src/sgs/data/files/standard.json` 的写法 |
| 「白名单目录之外出现音频即红」 | `tests/test_web_static.py` 全仓扫音频后缀（mp3 / ogg / wav …），只允许卡牌语音落在 `tools/sgs_web/static/voices/README.md` 所在的那个目录里 | 素材放进该目录并登进映射表；否则不要提交音频（伤害音效一律现场合成） |

## 6. 版权边界

照 `NOTICE`：

- **DIY 数据写你自己的文本**：技能名 / 描述 / 称号用你自己的话组织；本仓不接收**官方牌面整段
  复制**、卡图、语音。
- **第三方素材自备**：仓里唯一收着的音频是卡牌语音（口径见 `tools/sgs_web/static/voices/README.md`），
  其余音效 / 视觉全部由 CSS / Canvas / Web Audio 现场生成。你要加素材，自己负责授权，并遵守
  第 5 节那条白名单。
- **许可分工**：代码与原创文档是 MIT；游戏素材与规则文本归各自权利人，仓里只作学习 / 研究引用，
  **不得商用**（详见 `NOTICE`）。

## 7. 提案流程

- **一将一票**：一个武将 = 一张票 = 一次门禁 = 一次提交。票面骨架照
  `docs/agents/skill-authoring/batch-ticket.md`；`Status:` / `Touches:` 等字段约定照
  `docs/agents/issue-tracker.md`。
- **签了才动工**：把六问的产出与 `### 询问剧本` 摆成票面的一张表，维护者签过之后才写
  源码 / 测试的第一个字节（规则见 `docs/agents/skill-authoring.md` §2.1）。
- **PR 前 `bash tests/run_all.sh` 必须绿**（唯一判据）。金录像变红**先停**，不许自行重录
  （硬停 E1）；拿不准的口径不要猜，交一张裁定票（§5 那八项）。
- **不接受"只贴一张武将图"的 PR**：没有数据、没有测试、没有门禁通过，就不是一个可交付的武将。
