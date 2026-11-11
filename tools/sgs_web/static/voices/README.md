# 卡牌语音素材（`static/voices/`）

SGS OL 的**卡牌使用 / 打出**语音（chain-death-voices 票 03）。素材由用户提供，
源目录是工作区的 `SGS_OL_Cardvioces/SGS_OL_Cardvioces/`（不入仓：那是用户自己的源目录）。

## 搬了什么、没搬什么

- **搬进仓**：160 卡牌档（`standard_ex_160`，覆盖 108 档）需要的 **20 类 × 男 / 女 = 40 条**，
  按 `<card_type>_<gender>.mp3` 命名（ASCII，见下表）。合计 40 个文件、约 638 KB。
- **没搬**：`国战/` / `特殊/` / `其他/`（78 条，约 1.5 MB）不在 160 档内，留在源目录。
  `其他/` 8 条（`card_11215_1..4` / `card_11223_3_1..2` / `card_20054_1..2`）连映射表都不进——
  文件名认不出牌名，不猜（spec §不做）。
- **唯一真源是 `static/sfx-voices.js` 顶部的 `CARD_VOICES` 表**：本文件是它的同一张表（同一批
  43 行），静态用例 `tests/test_web_static.py` 逐行比对两份，漂移即红。

## 覆盖表（160 档全部 43 类；`—` = 明确静默，`null`）

| `card_type` | 牌名 | 男声 | 女声 | 说明 |
|---|---|---|---|---|
| `slash` | 杀 | `slash_male.mp3` | `slash_female.mp3` | 有语音 |
| `dodge` | 闪 | `dodge_male.mp3` | `dodge_female.mp3` | 有语音 |
| `peach` | 桃 | — | — | **待补素材**（先静默，见下） |
| `dismantlement` | 过河拆桥 | `dismantlement_male.mp3` | `dismantlement_female.mp3` | 有语音 |
| `snatch` | 顺手牵羊 | `snatch_male.mp3` | `snatch_female.mp3` | 有语音 |
| `ex_nihilo` | 无中生有 | `ex_nihilo_male.mp3` | `ex_nihilo_female.mp3` | 有语音 |
| `nullify` | 无懈可击 | `nullify_male.mp3` | `nullify_female.mp3` | 有语音 |
| `duel` | 决斗 | `duel_male.mp3` | `duel_female.mp3` | 有语音 |
| `barbarian_invasion` | 南蛮入侵 | `barbarian_invasion_male.mp3` | `barbarian_invasion_female.mp3` | 有语音 |
| `arrow_barrage` | 万箭齐发 | `arrow_barrage_male.mp3` | `arrow_barrage_female.mp3` | 有语音 |
| `peach_garden` | 桃园结义 | `peach_garden_male.mp3` | `peach_garden_female.mp3` | 有语音 |
| `borrowed_sword` | 借刀杀人 | `borrowed_sword_male.mp3` | `borrowed_sword_female.mp3` | 有语音 |
| `harvest` | 五谷丰登 | `harvest_male.mp3` | `harvest_female.mp3` | 有语音 |
| `indulgence` | 乐不思蜀 | `indulgence_male.mp3` | `indulgence_female.mp3` | 有语音 |
| `lightning` | 闪电 | `lightning_male.mp3` | `lightning_female.mp3` | 有语音 |
| `crossbow` | 诸葛连弩 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `qinggang_sword` | 青釭剑 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `double_sword` | 雌雄双股剑 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `green_dragon_blade` | 青龙偃月刀 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `serpent_spear` | 丈八蛇矛 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `axe` | 贯石斧 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `halberd` | 方天画戟 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `qilin_bow` | 麒麟弓 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `ice_sword` | 寒冰剑 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `eight_diagrams` | 八卦阵 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `renwang_shield` | 仁王盾 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `jueying` | 绝影 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `dilu` | 的卢 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `zhuahuang_feidian` | 爪黄飞电 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `chitu` | 赤兔 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `dawan` | 大宛 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `zixing` | 紫骍 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `fire_slash` | 火杀 | `fire_slash_male.mp3` | `fire_slash_female.mp3` | 有语音 |
| `thunder_slash` | 雷杀 | `thunder_slash_male.mp3` | `thunder_slash_female.mp3` | 有语音 |
| `wine` | 酒 | `wine_male.mp3` | `wine_female.mp3` | 有语音 |
| `supply_shortage` | 兵粮寸断 | `supply_shortage_male.mp3` | `supply_shortage_female.mp3` | 有语音 |
| `fire_attack` | 火攻 | `fire_attack_male.mp3` | `fire_attack_female.mp3` | 有语音 |
| `iron_chain` | 铁索连环 | `iron_chain_male.mp3` | `iron_chain_female.mp3` | 有语音 |
| `silver_lion` | 白银狮子 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `vine_armor` | 藤甲 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `ancient_scimitar` | 古锭刀 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `vermilion_feather_fan` | 朱雀羽扇 | — | — | 装备：SGS OL 无此语音（**预期终态**） |
| `hualiu` | 骅骝 | — | — | 装备：SGS OL 无此语音（**预期终态**） |

## 补【桃】的语音（只做两件事，代码一行不改）

1. 把 `peach_male.mp3` / `peach_female.mp3` 放进本目录；
2. 把 `sfx-voices.js` 顶部 `CARD_VOICES.peach` 的两格从 `null` 改成文件名。

静态用例的「43 类全登记 + 表里每条文件都存在 + 目录里没有孤儿」会跟着转绿；
`README.md` 这张表同步改那两格。

## 静默是正常行为的两种

- **【桃】`peach`**：等用户找到素材再加（用户 2026-11-11 确认）。
- **22 种装备**：SGS OL 本来就没有装备卡语音——装备的**使用**照常出一条 `card_events`，
  但映射表里是 `null`，因此一声不出。这是**预期终态**，不是缺口、不需要补。
