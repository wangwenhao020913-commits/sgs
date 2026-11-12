# acceptance-map：spec 验收条目 → 断言

收口（`/implement-spec` 的第 3 步）：每条 spec 验收条目 → 一条**存在且绿**的断言；
跨票重划线（票面之间互相矛盾或票面与 `spec §3 注①` 冲突）的条目记 `[R]` 并指向
`map.md` 的「Decisions-so-far」缺口编号。

机械核对：`.venv/bin/python tools/check_acceptance.py .scratch/table-ux-overhaul`
（扫 16 张票 `## Answer` 里的全部 `test_*` 引用，逐个核"存在且绿"）。
本文件另供 `/code-review` 的 Spec 轴使用。

## §1 报障（13 项）

| # | 报障 | 断言 | 票 |
|---|---|---|---|
| 1 | 建局止于 GAME_START（不提前进 AI 回合） | `tests/test_web.py::test_the_create_response_stops_at_the_end_of_game_start` | 14 |
| 1 | 开局底稿由服务端下发（`hand_count=0` / `hp == hp_max`） | `tests/test_web.py::test_the_create_response_carries_a_pre_game_setup_snapshot` | 14 |
| 1 | init 期上限变化不提前显示（天成 t0 不显示 5/5） | `tests/test_web.py::test_a_game_start_max_hp_change_does_not_leak_into_the_opening_frame` | 14 |
| 2 | 雄乱不复活借刀目标 | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon` | 03 |
| 2 | 雄乱不复活无牌角色（拆/顺） | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_dismantle_target_with_no_cards` | 03 |
| 2 | 雄乱不复活判定区已封印角色 | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_sealed_judge_area_target` | 03 |
| 2 | 雄乱不复活空城删掉的座位 | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_kongcheng_seat` | 03 |
| 2 | 距离豁免挂点求值在附加谓词之前 | `tests/test_hero_lei.py::test_the_range_exempt_hook_is_evaluated_before_the_target_filters` | 03 |
| 3 | `view()` 不再 JSON 往返整条记录流 | `tests/test_web.py::test_the_view_payload_never_json_round_trips_the_record_stream` | 01 |
| 3 | 叙事通道按游标增量（不再整份重建） | `tests/test_web.py::test_the_view_only_derives_the_new_narrative_records` | 01 |
| 3 | 放弃的对局会被回收 | `tests/test_web.py::test_an_abandoned_session_is_pruned_when_a_new_one_is_created` | 01 |
| 3 | 事件流节点数有上限 | `tests/test_web.py::test_the_event_log_keeps_a_bounded_number_of_nodes`（烟测 U-LOG-01…） | 01 |
| 4 | 预发动无弹窗 / 自动推进 / 取消回第一步 | 烟测 `tests/support/ui_smoke.mjs`：U-PA-01a–e、U-PA-02、U-PA-03、U-PA-04、U-PA-05、U-PA-06 | 08 |
| 4 | **收口后用户实机报障**：填完却提交不了（页脚说"还没填完"） | 烟测 U-PA-07a–d（真实发出 `ActivateSkill`、页脚无该句）+ `tests/test_web_static.py::test_the_preactivation_submit_verdict_has_one_single_implementation`（判据单源） | 16 |
| 4 | 提示区只显示**当前步**的字符串问法（不再铺步骤按钮） | 烟测 U-PA-08a–e（`#t-options` 无步骤按钮、提示行只说当前步） | 16 |
| 4 | 真选项仍是按钮（雌雄二选一 / 崩坏二选一） | 烟测 U-PA-09 / 09b–d | 16 |
| 5 | 提示区单一 reset（三调用点） | `tests/test_web_static.py::test_the_ask_surface_is_cleared_by_one_single_function`（烟测 U-…） | 07 |
| 5 | 文案模板三层回退 | `tests/test_web_static.py::test_the_prompt_templates_are_loaded_with_a_documented_fallback_chain` | 07 |
| 5 | 占位符白名单拒未知占位 | `tests/test_web_static.py::test_a_template_slot_whitelist_rejects_unknown_placeholders` | 07 |
| 6 | 转化源牌级置灰（全部产物不可用才灰） | `tests/test_hero_transform.py::test_a_source_card_with_no_usable_product_is_disabled_in_the_manifest` | 12 |
| 6 | 置灰项画出来但点不动（不是删行） | 烟测 U-TF-01a / U-TF-01 / U-TF-01b | 12 |
| 6 | 源牌级判据与引擎谓词同源 | `tests/test_hero_transform.py::test_every_transform_source_is_checked_against_the_engine_predicate` | 12 |
| 6 | 渐营目标步有活池（20 张牌不退化） | `tests/test_hero_jie_yijiang.py::test_jianying_target_step_has_a_live_pool_with_many_cards` | 12 |
| 6 | 渐营可点座位 = 预览回包的池 | 烟测 U-TF-02 / U-TF-02a / U-TF-02b / U-TF-02c | 12 |
| 6 | 业炎花色置灰由**后端**算（不再是前端第二份真源） | `tests/test_web_static.py::test_the_own_zone_suit_cap_reads_the_pick_preview_instead_of_counting_suits`（烟测 U-YS-01） | 15（09 的止损在此退役） |
| 6 | 选牌预览与候选枚举**同源**（禁的正是引擎会拒的槽位） | `tests/test_web.py::test_the_pick_preview_disables_the_slots_the_engine_would_reject` | 15 |
| 6 | 选牌预览只读 / 409 / 400 | `tests/test_web.py::test_the_pick_preview_writes_nothing` / `…::test_the_pick_preview_rejects_a_stale_answers_seq` / `…::test_the_pick_preview_rejects_malformed_picks_and_the_wrong_face` | 15 |
| 6 | 选牌预览失败 ⇒ 一个都点不动（fail-closed） | 烟测 U-YS-02 | 15 |
| 7 | 雌雄弃牌面可见（`reveal_hand=True`） | `tests/test_skills.py::test_double_sword_discard_face_is_visible_to_the_target` | 09 |
| 7 | 雌雄弃牌步牌桌直选 | 烟测 U-CX-01a–d | 09 |
| 7 | 自家手牌载荷来自**真引擎 pending**（回环） | `tests/test_web.py::test_the_own_hand_direct_pick_payloads_start_from_a_real_engine_pending` | 09 |
| 8 | 无无懈/无转化者不被问 | `tests/test_nullify.py::test_a_seat_without_a_nullify_or_any_conversion_is_not_asked` | 05 |
| 8 | 有【看破】黑牌仍被问 | `tests/test_nullify.py::test_a_seat_with_kanpo_black_card_is_still_asked` | 05 |
| 8 | 有界渐营转化仍被问 | `tests/test_nullify.py::test_a_seat_with_jianying_conversion_is_still_asked` | 05 |
| 8 | 多声明按优先级、输家不耗牌 | `tests/test_nullify.py::test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card` | 05 |
| 9 | 渐营先于羽扇（两种顺序都不丢） | `tests/test_timing.py::test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing` | 04 |
| 9 | 三档逐档问（护驾 → 八卦阵 → 真闪） | `tests/test_hero_response_window.py::test_a_dodge_window_asks_hero_tier_then_equipment_tier_then_card_tier` | 04 |
| 9 | Pass = 本档不发动、进下一档 | `tests/test_timing.py::test_the_higher_tier_is_asked_first_and_a_pass_moves_to_the_next_tier` | 04 |
| 9 | 无双里八卦阵按需要牌**事件**复位 | `tests/test_hero_response_window.py::test_wushuang_a_failed_bagua_judgment_still_allows_bagua_again_after_a_real_dodge` | 04 |
| 9 | 转化提供者与真牌同档（第 2 档） | `tests/test_hero_response_window.py::test_a_conversion_provider_is_tier_two` | 04 |
| 10 | 策略位默认：引擎 OFF / 网页 ON | `tests/test_web.py::test_the_ask_policy_defaults_off_at_the_engine_and_on_in_a_web_session` | 02 |
| 10 | 预筛口是纯预览（不耗序号/不写记录） | `tests/test_web.py::test_the_prefilter_helper_does_not_consume_a_query_slot` | 02 |
| 10 | 预筛口判"除 Pass 外有无候选" | `tests/test_web.py::test_the_prefilter_helper_picks_no_candidate_beyond_pass_as_askable` | 02 |
| 10 | 引擎缺省 OFF ⇒ 旧口径照问（可复现） | `tests/test_nullify.py::test_every_alive_seat_is_asked_once_per_layer_even_without_nullify` | 05 |
| 10 | 五处 A 类窗口都过预筛 | `tests/test_card_request_windows.py::test_every_a_class_window_consults_the_ask_policy_prefilter` | 05 |
| 10 | B 类必选目标无合法目标 ⇒ 不跑、零记录 | `tests/test_skills.py::test_a_forced_skill_with_no_legal_target_is_not_run_and_records_nothing` | 02 |
| 10 | 策略进侧车 meta 且回放还原 | `tests/test_web.py::test_the_sidecar_meta_freezes_the_ask_policy_and_replay_restores_it` | 02 |
| 11 | 谦逊牌回手：该批就删行（不等下一问） | `tests/test_web.py::test_a_hero_zone_return_is_applied_to_the_live_view_of_the_same_batch` | 10 |
| 11 | 空转轮询指纹覆盖武将牌区 | `tests/test_web.py::test_the_idle_repaint_fingerprint_covers_hero_zone_rows`（烟测 U-IDLE-01） | 10 |
| 12 | 火攻展示自己手牌不再是亮牌窗 | `tests/test_web.py::test_the_human_targets_fire_attack_reveal_step_is_a_table_direct_pick_without_leaking` | 09 |
| 13 | 连营一次选齐、逐座摸牌 | `tests/test_hero_card_zone.py::test_jie_lianying_picks_all_targets_in_one_ask` | 13 |
| 13 | 一次 `SELECT_OPTIONS`（座位多选唯一入口） | `tests/test_hero_active.py::test_a_multi_seat_step_is_asked_once_in_a_single_select_options` | 13 |
| 13 | 三处手写 while 消失 | `tests/test_hero_active.py::test_ask_seats_is_the_only_multi_seat_entry` | 13 |
| 13 | 大雾 / 奋威一次选齐 | `tests/test_hero_jie_qun.py::test_dawu_and_fenwei_pick_their_targets_in_one_ask` | 13 |
| 13 | 除疠同势力置灰（预览回包收窄） | `tests/test_hero_jie_qun.py::test_chuli_grays_out_same_kingdom_seats_after_the_first_pick` | 13 |
| 13 | 除疠仍逐名问弃哪张 | `tests/test_hero_jie_qun.py::test_chuli_still_asks_one_discard_per_target_after_the_seat_step` | 13 |

## §2 裁定（Q1–Q23）的落地断言

| 裁定 | 落地 | 断言 |
|---|---|---|
| Q2 距离豁免拆挂点 | `ModifierPoint.TARGET_RANGE_EXEMPT`（② 求值；`TARGET_POOL` 只留合法性增删） | 见 §1 #2 四条 + `tests/test_hero_catalog.py::test_the_range_exempt_point_carries_the_six_no_distance_consumers` / `…::test_the_target_pool_point_only_carries_legality_add_and_remove` |
| Q3 只问合格者 + 同窗呈现 | 预筛 + `window_frame` 日志合并 | 见 §1 #8 四条；前端合并键断言在 `tests/support/ui_smoke.mjs`（票 05 组） |
| Q4 会话配置 + 侧车 meta | `AskPolicy`（引擎 OFF / 网页 ON），不进 `RuleConfig` | §1 #10 三条 |
| Q5 自动推进 / 最后一步提交 / 保删弹窗 | 服务端 `can_advance`/`can_submit`/`next` 说，前端只读 | 烟测 U-PA-01c/01e、U-PA-02、U-PA-03、U-PA-06 |
| Q6/Q10 永不退化 + 预览请求 | `POST /api/sessions/<id>/preview`，同源调 `pool(chosen)`；**覆盖 = 预发动清单步 + `SELECT_CARDS` 选牌面**（后者由票 15 补齐） | 清单档：`tests/test_web.py::test_the_preview_endpoint_returns_the_same_pool_the_validator_uses` / `…::test_the_preview_endpoint_writes_nothing` / `…::test_the_preview_endpoint_rejects_a_stale_answers_seq` / `…::test_the_preview_endpoint_rejects_a_malformed_answers_prefix` / `…::test_the_preview_endpoint_is_cheap_enough_for_every_selection_change`；选牌档：见 §1 #6 四条 + `…::test_the_pick_preview_is_cheap_enough_for_every_pick` |
| Q7 建局 | L0 边界暂停 + 服务端 `setup` | 见 §1 #1 三条（+ 烟测 U-INIT-01a–g） |
| Q8 性能三处全修 | 游标化 / 回收 / 节点上限 | 见 §1 #3 四条 |
| Q11/Q18/Q23 文案模板 | `prompts.json` + 三层回退 + 占位符白名单 + localStorage 覆盖 | 见 §1 #5 三条；键名合法性 `tests/test_web_static.py::test_the_prompt_template_keys_are_real_skill_ids_and_real_step_names` |
| Q12 预发动各步表面 | 去弹窗 / 保窗清单 / 借刀全删弹窗；**控件区按钮只指真选项，不指步骤条**（缺口 #壬） | 烟测 U-PA-01/01b/01d/04/05/06 + U-PA-08（无步骤按钮）+ U-PA-09（真选项仍在） |
| Q13/Q17 三档逐档问 | 档 0/1/2 定义 + 按档迭代 | 见 §1 #9 四条；档位读法 `tests/test_timing.py::test_the_need_card_gate_reads_the_same_floor_in_all_four_windows` |
| Q15/Q20 三级置灰 | 源牌级 / 产物级 / 目标级 | §1 #6 三条 + `tests/test_hero_jie_yijiang.py::test_a_product_without_legal_targets_is_disabled_but_the_source_card_stays_enabled`；外人抹除 `tests/test_hero_transform.py::test_the_source_groups_are_masked_for_a_foreign_viewer` |
| Q16 转化牌与真牌同档 | 覆盖位 `SkillDef.need_card_tier=2`，无技能名名单 | §1 #9 转化提供者那条 + 账本行 1226 |
| Q19/Q4 引擎 OFF ⇒ 零重录 | 金录像逐字节 + 网页会话 ON | `bash tools/check_goldens.sh` 47 条；§1 #10 三条 |
| Q21 并发写者 | 单个实现者串行（本 run 一票一实现者） | 本表即过程证据（见 `map.md`） |
| Q22 票数 | 用户签的是 14 票；评审发现 `Q6` 的业炎半无人接手 ⇒ 追加票 15（缺口 #庚）；用户实机报障后又追加票 16（#壬）/ 17–18（#癸）/ 19（#子）⇒ **19 张票全 `resolved`** | `.scratch/table-ux-overhaul/issues/*.md` Status |

## §4 金录像（行为有意变更 ⇒ 重录与实现同提交）

| 条目 | 证据 |
|---|---|
| 47 条标准 108 基准逐字节不变（`bash tools/check_goldens.sh`） | 每次提交的 hook + 收口独立复跑：`PASS: 47 条标准 108 金录像逐字节不变` |
| 重录只发生在**行为票自己的提交**里 | 04：重录 9/60（09/20/31 同种子；17/34/35/36/37 重选种；30 越 4 MiB 绊线）；13：重录 1/60（`58-jie-luxun-hero-zone`，同种子，两问合一问）。两次都在自己的提交内完成 |
| 机制断言不被重录削弱 | `tests/replays/goals.py` 在 `3a37f57...HEAD` 上 **0 行改动**（`git diff --stat … -- tests/replays/goals.py` 为空） |
| 冻结表 / 索引同批更新 | `tests/test_replays.py` 的 `STANDARD_108_REPLAY_BYTES` + `tests/replays/README.md`（04 与 13 各一次） |

## §5 验收总则（过程条目）

| 条目 | 证据 | 状态 |
|---|---|---|
| 每票 `## Answer` 写：改动摘要 / 会红的既有断言 / 新增断言清单 / 门禁结果 | 19 张票全部有 `## Answer`；`tests/test_agent_skills.sh` 的 Answer 契约 PASS | ✅ |
| 新增断言落在真实产出路径 | 票 09 的"回环"用例从真引擎 pending 取载荷；票 11 的同源断言直接调校验器；票 13 的除疠越池用例真跑执行器 | ✅ |
| 前端行为变更过 `bash tests/smoke-web.sh` | 票 07/08/09/10/11/12/13/14 的 Answer 均记录 smoke-web PASS（票 14 1635→1690+ PASS / 0 FAIL） | ✅ |
| 改 DOM API 形状的票要走 `bsk` 实机 | **未执行**：本机 bsk 的 cooperate 握手一直失败（所有 `browser_interact` / `navigate` 被 "requires the context returned by the latest cooperate sync" 拒绝）。已按仓规改为"配方 + 结束 turn 交用户"：票 07/08/09/10/13/14 的 `## Answer` 各带配方；功能面由 smoke-web + `tests/test_action_contract.py` 覆盖 | `[R]` 见 `map.md` 缺口 #戊 |
| 新机制过 `docs/agents/ai-adaptation.md` §1 | 票 02/03/04/05/11/12/13 的 Answer 逐条判"不适用/已登记"；票 11 判不适用（只读预览通道不改 `QueryKind`/动作面），票 13 的 `_DYNAMIC_POOL_SEAT_STEPS` 登记为 AI 侧新纪律 | ✅ |
| 全量门禁收口跑一次 | 收口独立复跑 `bash tests/run_all.sh`（结果见交付报告） | ✅ |

## §6 不变量（跨票）

| 不变量 | 断言 |
|---|---|
| 候选 = 接受（前端不猜） | 票 11 同源断言 + 票 12 差集断言 + 票 13 `test_chuli_refuses_a_same_kingdom_set_the_pool_never_offered`（越池提交被引擎整步取消）+ 票 15 `test_the_pick_preview_disables_the_slots_the_engine_would_reject`（选牌面同源） |
| 记录流 = 权威事实（文案不进 `Query.prompt`） | `tests/test_replays.py::test_golden_replay_matches_byte_for_byte`（47 条冻结字节）+ 重录只发生两次且均为行为变更 |
| 派生通道只增 + `after` 游标 | `test_the_view_payload_never_json_round_trips_the_record_stream` + `test_skill_events_follow_the_record_cursor_and_only_cover_accepted_activations` |
| 前端不猜：拿不到池 ⇒ 点不了 | 烟测 U-PV-02 / U-PV-02a–c（fail-closed）+ 票 08 "退化不自动推进" + U-TF-02b（池外点不动） |
| 引擎缺省 OFF ⇒ 旧行为逐字节可复现 | `bash tools/check_goldens.sh` 全绿 + §1 #10 的 OFF/ON 成对断言 |

## 跨票重划线（`[R]`，裁定落在 `map.md` Decisions-so-far）

| # | 条目 | 裁定 |
|---|---|---|
| 甲 | 票 13 会改 `58-jie-luxun-hero-zone` 的记录流，而票 06 是纯文档且先于它 | 由票 13 在**自己的提交**里重录（照 `spec §3 注①`），票 06 的账本写成通式 |
| 乙 | 票 11"不再下发 `disabled`" vs 票 12 要 `disabled` | 11 退役逐组合静态行 + 退化分支；`disabled` 字段保留，由票 12 重新定义为源牌级 / 产物级 |
| 丙 | `开发文档/04` 在票 06 的 Touches 里但 06 未改 | 由票 08 收口（§4 预发动 + §1 N = 合格者数） |
| 丁 | 票 10 与票 14 都写"补 `hp_max_change` 派生" | 派生归票 10；票 14 只做服务端 `setup` + 删倒推 |
| 戊 | spec §5"改 DOM 形状要走 bsk" | 本机 bsk 不可驱动（cooperate 握手失败）⇒ 以冒烟 + 真 HTTP tape 覆盖，配方交用户实机补验 |

## §7 `/code-review` 双轴评审：findings 与处置

评审面 = `git diff 3a37f57...HEAD`（排除 `tests/replays/*.jsonl` 与 `.scratch/`）。
两轴各自独立出报告，处置如下（修复提交 `eb1c50a`；规格缺口另开票 15，提交 `ba65987`）。

### Standards 轴（2 hard + 5 judgement）

| # | finding | 处置 |
|---|---|---|
| S1 | `GLOSSARY.md` 的「预发动」词条带票号 / 源码路径 / "由服务端说"实现细节，违 `CODING_STANDARDS` §2（术语表只写定义 + `_Avoid_`） | **已修**：改纯定义；顺带核了「需要牌档位」「声明·生效」两条同类泄露并收口 |
| S2 | `RespondContext.skip_if_empty` 只被写、无人读（违 §1「字段作落点前先打开读取方」），docstring 却在宣称它被读 | **已修（unify）**：新增 `Engine.respond_context` 建唯一一份上下文，`should_ask_respond` 读 `ctx.skip_if_empty`；三处重复参数消失 ⇒ 也解掉 S7 的 Data Clump。口径记进 `map.md` 缺口 #辛 |
| S3 | `heuristic._seat_step_key` 复刻 `flow._flow_option_id` 的键形；`_DYNAMIC_POOL_SEAT_STEPS` 是只登记在被 gitignore 的 `.scratch` 里的新封闭集 | **已修**：键形下沉为 `sgs.state.step_option_key`（两侧共用）；清单登记进 `docs/agents/ai-adaptation.md` §4，并加"每个键必须是真动态池步骤"的真询问断言 |
| S4 | `NON_TABLE_DELTAS` 是 `applyLiveDelta` 那支 switch 的反向副本，正确性依赖一句注释 | **已修**：加静态同步断言（新增 delta 类型不进任一侧即判红） |
| S5 | `session.js` 的 `liveHeroZoneKey` 在客户端拼 / 拆 `owner:skill_id`，并对投影从不发出的形状留了分支 | **接受（不修）**：直接读服务端键要给投影 `hero_zones[]` 加 `skill_instance`，会连带改夹具形状绑定与 `test_web.py` 的夹具形状用例——超出局部前端修改；判为可接受的技术债 |
| S6 | `records_after` / `next_cursor` 是只剩测试调用的 Middle Man | **接受（不修）**：它们是 `Session` 的公开读口，票 01 明确保留其语义；删掉会让会话窗口 API 变窄而无收益 |
| S7 | `should_ask_respond` 的 10 参 Data Clump 复刻 `respond_candidates` | **已修**（随 S2 的 unify） |

### Spec 轴（3 missing/partial + 1 bug + 1 creep）

| # | finding | 处置 |
|---|---|---|
| P1 | `spec §2 Q6` 的"业炎花色由后端算"未兑现——前端 `ownZoneSuitTaken` 仍是第二份真源，且无票接手 | **已修（新开票 15）**：预览端点扩到 `SELECT_CARDS`（`pick_completions` 复用同一个组合枚举器），前端改读回包 `disabled`、止损判据删除；`spec §6` 不变量 1 自本票起成立 |
| P2 | `Q18` 的"每（技能, 步骤）一对 `ask`/`done`"只做到 2%（142 ask / 3 done），缺 `done` 被静默跳过 | **部分处置 + 裁定**：机制（三层回退 / 累加 / 键名合法性）已实现并钉住；按 Q23「我先出初稿、用户改字」+ §7 风险（文案普查是大头），**其余 139 步的 `done` 措辞属用户改字那一遍**——裁定见 `map.md` 缺口 #己 |
| P3 | `Q2` 的第 6 个消费者【神速】仍走 `check_targets(..., distance_exempt=True)`，是挂点之外的第二条豁免路径 | **已修**：布尔改为喂同一份豁免求值（一个实现、一份座位集），并加"两条路径同样座位集"的断言；`shapes.md` §3.3 措辞同步 |
| P4 | `flow.preview_step` 的 fail-closed 只清候选、没清 `can_advance`，与 docstring 与 `§6` 不变量 5 矛盾 | **已修**：错误分支把 `can_advance` / `can_submit` 一并置假，并加真形状断言 |
| P5 | `heuristic._assembled_option_set` 把 `cap = min(cap, len(options))` 夹到**所有** `SELECT_OPTIONS`（票面只要求除疠的动态池） | **已修（收敛作用域）**：只有登记的动态池步骤再夹，并加真询问断言（AI 自建集合不得含同势力两名） |

## §8 收口后用户实机报障（票 16–19；不在 spec §1 的 13 项里）

| 报障 | 落地 / 断言 | 票 | 状态 |
|---|---|---|---|
| 预发动填完却提交不了（标题说已填完、页脚说还没填完） | 烟测 U-PA-07a–d（真实发出 `ActivateSkill`、页脚无该句）+ `tests/test_web_static.py::test_the_preactivation_submit_verdict_has_one_single_implementation` | 16 | ✅ |
| 提示区被步骤按钮挤占：只要当前一步、要字符串 | 烟测 U-PA-08a–e（`#t-options` 无步骤按钮、提示行只说当前步）+ U-PA-09（真选项仍是按钮） | 16 | ✅ |
| 【大雾】【狂风】在准备阶段被问 | `tests/test_hero_huo.py::test_dawu_and_kuangfeng_are_never_offered_at_turn_start`（修复前 256 次 TURN_START 面 → 0；基线 `3a37f57` 上同样 256 次 ⇒ 陈年缺陷）+ `::test_the_turn_start_expiry_still_runs_without_a_question` | 17 | ✅ |
| 五谷亮牌面：目标条压住牌面 | 烟测 U-HV-02（`reveal-targets` 进流）+ `test_web_static` 静态判据 | 18 | ✅ |
| 五谷：被拿走的牌不刷新 | 服务端 `view.harvest`（`tests/test_web.py` 的真会话视图断言）+ 烟测 U-HV-01/01b/01c/03a/03b/04 | 18 | ✅（人类自己取牌后可见） |
| 五谷：**AI 目标**取牌仍要等结算结束才跳变（逐目标可见） | 引擎驱动判据 `Engine.run(stop_when=)` + `Session._harvest_pick_pause_due`（同一份 `harvest_state` 派生、进度 =（链键, 已拿数））+ 一次响应最多一个暂停点（走 `submit_answer` 的返回值）+ 客户端空窗窗寿命（`harvestStateWithoutPending` / `dialogRevealSource`）（不落会话标志）。断言：`tests/test_web.py::test_each_harvest_target_pick_is_observable_in_a_view` / `::test_the_harvest_pause_stops_once_per_target`（3 名目标 ⇒ 恰好 3 次中间视图）/ `::test_the_harvest_pause_is_pure_driving_throttling`（记录流逐字节不变）+ 烟测 U-HV-05a–d + 按新节奏改写的 U2-R11/12/13 / U-HV-04 / M6-B1 | 19 | ✅ |
