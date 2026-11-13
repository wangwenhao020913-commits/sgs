---
name: hero-skill
description: "给 SGS 仓新增或修改武将技能：先交「询问剧本」等用户签，再按读法 + 形状落形；命中歧义硬停。"
disable-model-invocation: true
---

# 武将技能：入口

**判据就在本仓**：`docs/agents/skill-authoring.md`。**第一件事**是读它，读完再开口：

```
docs/agents/skill-authoring.md
```

那是总入口：它的读单会把四份子文档（`readings.md` / `shapes.md` / `edge-cases.md` /
`batch-ticket.md`）和另外四份非技能文档（`开发文档/04` §4、`开发文档/06` §5、
`GLOSSARY.md`、`docs/agents/ai-adaptation.md`）交给你。**不要凭本文件动工。**

三条不可省（细则仍以文档为准）：

1. **开工 = 写 `src/` 或 `tests/` 的第一个字节。** 六问产出不是许可：把规范压成票面的
   `### 询问剧本` 交给我，**结束本轮等一句「同意」**（`skill-authoring.md` §2.1）。
   一将一票、整批一次签；一次给多个武将走 §3.1。
2. **命中 `skill-authoring.md` §4 的硬停判据 ⇒ 立即停**：不写实现、不写账本、不写测试，
   交一张八项裁定票（§5）；本票其余技能继续做，被摘出的记进「摘出记录」。
3. **账本、金录像、读法都由我签**，你只提案（§4 E1：金录像红了先停，不许自行重录）。

自身体检与门禁节奏照 `skill-authoring.md` §6 / §3 跑，本文件不重抄。

判据版本：与 `docs/agents/skill-authoring.md` 同仓（硬停判据恰 13 条）
