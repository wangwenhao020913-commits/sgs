# acceptance-map：spec 验收条目 → 断言

说明：本文件在实现期由 `/implement-spec` 收口维护；每行 = 一条 spec 验收条目 → 一条**存在且会绿**的断言。
`tools/check_acceptance.py .scratch/table-ux-overhaul` 只读核这张表引用的 `test_*`（模块名 / 省略号占位不算引用）。

## spec §5 / 各票验收

| spec 条目 | 断言（实现时落到这里） | 状态 |
|---|---|---|
| #3 性能：`view()` 不再 JSON 往返整条记录流 | `tests/test_web.py::test_the_view_payload_never_json_round_trips_the_record_stream` | 待实现（票 01） |
| #3 性能：放弃的对局会被回收 | `tests/test_web.py::test_an_abandoned_session_is_pruned_when_a_new_one_is_created` | 待实现（票 01） |
| #3 性能：事件流节点数有上限 | `tests/support/ui_smoke.mjs`（U-LOG-01） | 待实现（票 01） |
| #10 策略位（管道）默认值：引擎 OFF / 网页 ON | `tests/test_web.py::test_the_ask_policy_defaults_off_at_the_engine_and_on_in_a_web_session` | 待实现（票 02） |
| #10 预筛口是纯预览（不耗序号、不写记录） | `tests/test_web.py::test_the_prefilter_helper_does_not_consume_a_query_slot` | 待实现（票 02） |
| #10 A 类窗口无合法候选时不问（**策略 ON**） | `tests/test_nullify.py::test_a_seat_without_a_nullify_or_any_conversion_is_not_asked` | 待实现（票 05） |
| #10 引擎缺省 OFF ⇒ 旧口径照问（可复现） | `tests/test_nullify.py::test_auto_skip_off_still_asks_a_pass_only_window` | 待实现（票 05） |
| #10 B 类无合法目标直接跳过 | `tests/test_skills.py::test_a_forced_skill_with_no_legal_target_is_not_run_and_records_nothing` | 待实现（票 02） |
| #10 策略进侧车 meta 且回放还原 | `tests/test_web.py::test_the_sidecar_meta_freezes_the_ask_policy_and_replay_restores_it` | 待实现（票 02） |
| #2 雄乱不复活借刀/拆顺/封印/空城 | `tests/test_hero_lei.py::test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon` | 待实现（票 03） |
| #2 距离豁免挂点求值在附加谓词之前 | `tests/test_hero_lei.py::test_the_range_exempt_hook_is_evaluated_before_the_target_filters` | 待实现（票 03） |
| #9 渐营先于羽扇、两者都能发动 | `tests/test_timing.py::test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing` | 待实现（票 04） |
| #9 三档逐档问 | `tests/test_timing.py::test_a_dodge_window_asks_hero_tier_then_equipment_tier_then_card_tier` | 待实现（票 04） |
| #9 无双里八卦阵按事件复位 | `tests/test_hero_response_window.py::test_wushuang_a_failed_bagua_judgment_still_allows_bagua_again_after_a_real_dodge` | 待实现（票 04） |
| #9/Q16 转化提供者落第 2 档 | `tests/test_timing.py::test_a_conversion_provider_is_tier_two` | 待实现（票 04） |
| #8 无无懈者不被问 | `tests/test_nullify.py::test_a_seat_without_a_nullify_or_any_conversion_is_not_asked` | 待实现（票 05） |
| #8 有【看破】黑牌仍被问 | `tests/test_nullify.py::test_a_seat_with_kanpo_black_card_is_still_asked` | 待实现（票 05） |
| #8 多声明按优先级、输家不耗牌 | `tests/test_nullify.py::test_two_declarations_keep_the_higher_priority_one_and_the_loser_keeps_the_card` | 待实现（票 05） |
| 金录像重录后仍绿 | `tests/test_replays.py::test_the_47_standard_108_replays_keep_their_frozen_bytes` | 待实现（票 06） |
| #5 提示区单一 reset | `tests/test_web_static.py::test_the_ask_surface_is_cleared_by_one_single_function` | 待实现（票 07） |
| #5 文案模板回退链 | `tests/test_web_static.py::test_the_prompt_templates_are_loaded_with_a_documented_fallback_chain` | 待实现（票 07） |
| #4 预发动无弹窗 + 自动推进 + 取消回第一步 | `tests/support/ui_smoke.mjs`（U-PA-01…U-PA-06） | 待实现（票 08） |
| #7 雌雄弃牌面可见 | `tests/test_skills.py::test_double_sword_discard_face_is_visible_to_the_target` | 待实现（票 09） |
| #12 火攻不再是亮牌窗 | `tests/test_web.py`（火攻载荷 `dialog is None`） | 待实现（票 09） |
| #6 业炎花色置灰 | `tests/support/ui_smoke.mjs`（U-YS-01） | 待实现（票 09） |
| #11 谦逊牌区同批消失 | `tests/test_web.py::test_a_hero_zone_return_is_applied_to_the_live_view_of_the_same_batch` | 待实现（票 10） |
| Q10 预览与校验器同源 | `tests/test_web.py::test_the_preview_endpoint_returns_the_same_pool_the_validator_uses` | 待实现（票 11） |
| Q10 预览只读 | `tests/test_web.py::test_the_preview_endpoint_writes_nothing` | 待实现（票 11） |
| #6 转化源牌/产物级置灰 | `tests/test_hero_transform.py::test_a_product_without_legal_targets_is_disabled_but_the_source_card_stays_enabled` | 待实现（票 12） |
| #6 渐营动态池 | `tests/test_hero_jie_yijiang.py::test_jianying_target_step_has_a_live_pool_with_many_cards` | 待实现（票 12） |
| #13 连营一次选齐 | `tests/test_hero_card_zone.py::test_jie_lianying_picks_all_targets_in_one_ask` | 待实现（票 13） |
| #13 除疠势力互异置灰 | `tests/test_hero_jie_qun.py::test_chuli_grays_out_same_kingdom_seats_after_the_first_pick` | 待实现（票 13） |
| #1 建局止于 GAME_START | `tests/test_web.py::test_the_create_response_stops_at_the_end_of_game_start` | 待实现（票 14） |
| #1 开局底稿由服务端下发 | `tests/test_web.py::test_the_create_response_carries_a_pre_game_setup_snapshot` | 待实现（票 14） |
| #1 init 期上限变化不提前显示 | `tests/test_web.py::test_a_game_start_max_hp_change_does_not_leak_into_the_opening_frame` | 待实现（票 14） |

## 跨票不变量（每条都要有断言）

| 不变量 | 断言 |
|---|---|
| 候选 = 接受（前端不猜） | 票 11 的同源断言 + 票 12 的差集断言 |
| 记录流不含显示层文案 | 现有 `tests/test_replays.py` 的字节判据 + 票 07 的"只做显示覆盖"静态断言 |
| 任何新事件通道按 after 游标 | 票 01 的 `record_json` monkeypatch 判据 |
| 拿不到池 ⇒ 点不了（fail-closed） | 票 11 的预览失败烟测 `U-PV-02` + 票 08 的"退化不自动推进" |
| **引擎缺省 OFF ⇒ 金录像逐字节不变** | 票 02 / 05 的硬验收：`bash tools/check_goldens.sh` 必须绿（见 `spec §3 注①`） |
| **重录只与行为改动同提交** | 票 04 的 `## Answer` 必须带重录清单 + `goals.py` 机制断言未改弱 |
