# 自制武将样板：一张复用已有技能的武将牌

## 这是什么

`examples/diy-hero/heroes.diy.json` 是一份**最小但真能用**的武将表：一张自制武将牌
`diy_hero`（群 · 男 · 4 体力），它唯一的技能【武圣】不是新写的 Python，而是**显式引用**了界关羽
名下那条已经登记好的定义（`hero.jie_guanyu.wusheng`）。

它演示的是"只改数据"这条路（路线 A）的全部要点：

- **不写技能实现**：条目用 `skill_id` 指向 `CATALOG` 里**已经登记**的技能；
- **`id` 只是行键**：条目里的 `id: "wusheng"` 是这张表自己的稳定键（`granted_by` / 形状表读它），
  不参与拼全限定技能 id——全限定 id 直接取 `skill_id`；
- **`skill_type` 要跟着被引用的技能**：写错就是加载错误
  （`heroes.skill_id_type_mismatch`），不是"运行时再看看"。

本样板**刻意不进官方池**（不进 `HERO_SUPPLEMENT_PACK_IDS`）：所以官方池的计数一处不动，
`tests/test_hero_diy_example.py` 里有一条测试盯着这件事。

## 怎么把它接进一局

两条路，按你的场景选一条：

**① 拷进加载面（会动官方池计数）**

1. 把 `examples/diy-hero/heroes.diy.json` 拷到 `src/sgs/data/files/`，文件名改成
   `heroes.<你的包 id>.json`（文件名里的包 id 与文件里的 `pack_id` 保持一致）。
2. 在 `src/sgs/data/loader.py` 的 `HERO_SUPPLEMENT_PACK_IDS` 里追加你的包 id。
3. **这一步会改变官方池的 15 处计数 / 7 张登记表**（启用武将数 / 真名数 / 技能数 / 形态格，
   写死在 `tests/test_hero_catalog.py`、`tests/test_hero_dealing.py`、`tests/test_web.py`、
   `tests/support/ui_smoke.mjs` 里）——先跑 `tools/pool_census.py --check` 看差在哪个站点的哪个
   数字，再手工改。
4. 门禁：`bash tests/run_all.sh`。

**② 只在测试里注入（不进池，计数一处不动）**

加载面是函数、武将表是纯数据：测试里把 `sgs.data.loader.load_hero_tables` 换成"既有表 +
你的表"，就能在**不注册、不动任何计数**的前提下把这张牌坐进一局。完整例子就是
`tests/test_hero_diy_example.py` 的 `test_the_example_pack_can_seat_a_hero`——`monkeypatch`
换加载面 + `build_state` 坐一局 + 两条技能清单断言。

## 怎么改

- **换一条技能**：把 `skill_id` 改成 `CATALOG` 里已登记的任一条（`tools/dump_catalog.py` 可反
  查）；`skill_type` 跟着那条技能改；`name` 是你自己的文本，别整段抄官方牌面（版权边界见
  `docs/agents/diy-heroes.md` 第 6 节）。
- **加第二张牌**：在 `heroes` 数组里再加一项；`id` 全局唯一，`true_name` + `version` 的组合也
  唯一（同一真名的同一版本只能有一条记录）。
- **需要一条新机制**（不是"复用已有技能"）：这时必须写 `SkillDef`（路线 B），走
  `docs/agents/diy-heroes.md` 的六问流水线；本样板帮不到你。
- **先自检**：改完跑 `.venv/bin/pytest -q tests/test_hero_diy_example.py`；三条新报错
  （`heroes.skill_id_not_found` / `heroes.skill_id_type_mismatch` / `heroes.skill_id_duplicate`）
  的含义见 `docs/agents/diy-heroes.md` 第 5 节。
