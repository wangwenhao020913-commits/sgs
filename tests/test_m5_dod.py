"""M5 完成定义收口：三条机械判据逐条成立（`.scratch/m5-military-expansion/spec.md` §6）。

三条判据（A1 / A2 / A3）不是新写的规则测试，而是**证据绑定**：把每条判据指到真正建立
它的测试 / 脚本上，删掉或改名建立者即红。严格性由被点名的文件自己负责（它们都在
``tests/run_all.sh`` 里跑）。

- **A1 牌名覆盖 + 牌堆分布**：11 个新牌名的构造局面实测（成对正反例）+ 160 张分布；
- **A2 端到端**：真 HTTP 打完 160 张 8 人局；火攻 / 铁索重铸 / 酒三条**构造局面固定 tape**；
  不泄露判据；全 AI 局 ≥8 个固定 160 种子；
- **A3 回归 / 不变量**：标准 47 条金录像逐字节不变 + ≥8 条 M5 金录像（构造局面）覆盖
  11 个新牌名与 §6 A3 的 10 个覆盖项。

递归禁令：本文件**不**调用 ``tests/run_all.sh``（那会在 pytest 里再起 pytest、再跑
``test_web.sh``）。M2 的八条在 ``tests/test_dod.py``、M3 的八条在 ``tests/test_dod_m3.py``、
M4 的三条在 ``tests/test_dod_m4.py``。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent


class DoDItem(NamedTuple):
    """一条 M5 完成定义：中文条目 + 建立它的用例 / 脚本 + 不可删的字面锚。"""

    title: str
    files: tuple[str, ...]
    literals: tuple[str, ...]


DOD_M5_ITEMS: dict[int, DoDItem] = {
    1: DoDItem(
        "A1 牌名覆盖 + 牌堆分布：11 个新牌名各有构造局面实测（成对正反例），"
        "standard_ex_160 的 52 张统计与总数 / 花色分布由加载期测试断言",
        (
            "tests/test_m5_attribute_damage.py",
            "tests/test_m5_wine.py",
            "tests/test_m5_fire_attack.py",
            "tests/test_m5_iron_chain.py",
            "tests/test_m5_supply_shortage.py",
            "tests/test_m5_equipment.py",
            "tests/test_m5_fan.py",
            "tests/test_m5_reforge.py",
            "tests/data/test_deck_snapshot.py",
        ),
        (
            "test_a_fire_slash_declares_fire_nature_on_the_damage_frame",
            "test_a_thunder_slash_declares_thunder_nature_on_the_damage_frame",
            "test_play_phase_wine_only_targets_itself_and_sets_wine_active_with_a_delta",
            "test_fire_attack_cannot_target_a_character_without_hand_cards",
            "test_iron_chain_never_offers_zero_three_duplicate_or_dead_targets",
            "test_supply_shortage_with_a_non_club_judgment_skips_the_draw_phase",
            "test_supply_shortage_with_a_club_judgment_draws_normally",
            "test_vine_armor_makes_a_normal_slash_ineffective_against_its_holder",
            "test_silver_lion_caps_three_damage_to_one",
            "test_ancient_scimitar_adds_one_when_the_target_has_no_hand_cards",
            "test_ancient_scimitar_does_not_add_when_the_target_holds_a_hand_card",
            "test_the_fan_offers_a_normal_slash_as_a_fire_slash_in_the_play_phase",
            "test_the_fan_converts_only_normal_slashes_never_fire_or_thunder",
            "test_reforge_moves_the_card_to_the_discard_pile_and_draws_one",
            "test_reforge_does_not_open_a_nullify_window",
            "test_hualiu_adds_one_to_every_other_seats_distance_to_its_holder",
            "test_the_160_deck_matches_the_snapshot",
            "test_the_160_deck_counts_by_type_suit_and_category",
        ),
    ),
    2: DoDItem(
        "A2 端到端：人类经真 HTTP 打完 160 张 8 人身份局、零非法应答；"
        "火攻 / 铁索重铸 / 酒三条构造局面固定 tape 各至少一次真实往返；"
        "人类载荷不泄露；全 AI 局 ≥8 个固定 160 种子",
        (
            "tests/test_web.sh",
            "tests/support/web_client.py",
            "tests/support/m5_fixture_server.py",
            "tests/test_m5_no_leak.py",
            "tests/test_all_ai_games.py",
            "tests/test_m5_ai_military.py",
        ),
        (
            "M5-09a：军争 **160 张档**的 8 人身份局",
            "固定 tape：火攻 / 重铸 / 酒（构造局面，真 HTTP）",
            "PASS 固定 tape：",
            "human_answers != answers",
            "PASS 快照无对手手牌牌面",
            "sgs-m5-fixtures listening on",
            "fixture\": fixture",
            "test_the_human_payload_of_a_full_160_game_never_leaks_hands_identities_or_deck",
            "test_the_fire_attack_reveal_is_public_but_the_unrevealed_hand_never_leaks",
            "MILITARY_SEEDS: tuple[int, ...] = ACCEPTANCE_SEEDS[:8]",
            "test_the_standard_ex_160_group_finishes_cleanly_and_replays",
        ),
    ),
    3: DoDItem(
        "A3 回归 / 不变量：标准 47 条金录像逐字节不变；≥8 条 160 档构造局面金录像覆盖"
        "11 个新牌名与 §6 A3 的 10 个覆盖项；AI 4 条构造局面 + ≥8 全 AI 种子进验收",
        (
            "tests/test_replays.py",
            "tests/replays/scenarios.py",
            "tests/replays/m5.py",
            "tests/replays/goals.py",
            "tests/replays/distribution.py",
            "tests/replays/readme_index.py",
            "tests/test_m5_ai_military.py",
        ),
        (
            "test_the_baseline_distribution_meets_every_category_minimum",
            "test_every_m5_baseline_has_an_after_the_fact_goal",
            "test_the_frozen_m1_six_stay_in_the_file_set",
            'M5_DECK_PROFILE = "standard_ex_160"',
            "M5_GOALS = {",
            '"48-fire-slash-vine-armor"',
            '"55-fan-converts-normal-slash"',
            "test_the_ai_picks_the_fire_slash_against_vine_armour",
            "test_the_ai_avoids_a_vine_armour_target_with_a_normal_slash",
            "test_the_ai_plays_wine_before_a_lethal_slash_and_conserves_it_otherwise",
            "test_the_ai_uses_iron_chain_and_fire_attack_only_when_they_pay_and_reforges_otherwise",
        ),
    ),
}
"""三条 M5 判据 → 建立它们的文件 + 字面锚（删 / 改名即红）。"""


M5_CARD_NAMES: dict[str, tuple[str, str]] = {
    "fire_slash": ("属性【杀】", "48-fire-slash-vine-armor"),
    "thunder_slash": ("属性【杀】", "49-iron-chain-thunder-conduction"),
    "wine": ("酒", "50-wine-silver-lion-cap"),
    "supply_shortage": ("兵粮寸断", "53-supply-shortage-skips-draw"),
    "fire_attack": ("火攻", "51-fire-attack-reveal-discard"),
    "iron_chain": ("铁索连环", "49-iron-chain-thunder-conduction"),
    "silver_lion": ("白银狮子", "50-wine-silver-lion-cap"),
    "vine_armor": ("藤甲", "48-fire-slash-vine-armor"),
    "ancient_scimitar": ("古锭刀", "52-scimitar-empty-hand-hualiu"),
    "vermilion_feather_fan": ("朱雀羽扇", "55-fan-converts-normal-slash"),
    "hualiu": ("骅骝", "52-scimitar-empty-hand-hualiu"),
}
"""11 个新牌名 → （中文名, 覆盖它的 M5 金录像 slug）。"""

M5_A3_ITEMS: dict[str, tuple[str, ...]] = {
    "属性": ("48-fire-slash-vine-armor", "49-iron-chain-thunder-conduction", "55-fan-converts-normal-slash"),
    "连环": ("49-iron-chain-thunder-conduction",),
    "酒": ("50-wine-silver-lion-cap",),
    "藤甲": ("48-fire-slash-vine-armor",),
    "白银": ("50-wine-silver-lion-cap",),
    "古锭刀": ("52-scimitar-empty-hand-hualiu",),
    "朱雀": ("55-fan-converts-normal-slash",),
    "火攻": ("51-fire-attack-reveal-discard",),
    "兵粮": ("53-supply-shortage-skips-draw",),
    "重铸": ("54-reforge-iron-chain",),
}
"""spec §6 A3 的 10 个覆盖项 → 覆盖它的 M5 金录像 slug。"""

M5_AI_POSITIONS: tuple[tuple[str, str], ...] = (
    ("火杀 vs 藤甲", "test_the_ai_picks_the_fire_slash_against_vine_armour"),
    ("普通杀 vs 藤甲（避开）", "test_the_ai_avoids_a_vine_armour_target_with_a_normal_slash"),
    ("酒 + 杀斩杀", "test_the_ai_plays_wine_before_a_lethal_slash_and_conserves_it_otherwise"),
    (
        "铁索 / 火攻收益与重铸",
        "test_the_ai_uses_iron_chain_and_fire_attack_only_when_they_pay_and_reforges_otherwise",
    ),
)
"""M5-07 的四条构造局面（spec §6 A3）→ ``tests/test_m5_ai_military.py`` 的用例名。"""


def _read(relative: str) -> str:
    path = ROOT / relative
    assert path.is_file(), f"落点文件不存在：{relative}"
    return path.read_text(encoding="utf-8")


def test_every_m5_definition_of_done_item_is_established_by_a_real_file() -> None:
    """把三条判据绑到真实文件与真实用例名上——删掉或改名建立者即红。"""
    assert set(DOD_M5_ITEMS) == {1, 2, 3}, "M5 恰三条判据（spec §6 A1/A2/A3）"

    for number, item in DOD_M5_ITEMS.items():
        for relative in item.files:
            _read(relative)
        text = "\n".join(_read(relative) for relative in item.files)
        for literal in item.literals:
            assert literal in text, (
                f"判据 {number} 的落点里没有『{literal}』——"
                "建立它的用例 / 脚本被删或改名即红"
            )


def test_the_eleven_new_card_names_are_covered_by_constructed_replays() -> None:
    """A1/A3 的牌名覆盖：11 个新牌名各至少一条 M5 金录像，且每条 slug 真在场景表里。"""
    from replays import goals, scenarios

    assert len(M5_CARD_NAMES) == 11, "11 个新牌名一个不能少"
    m5_source = _read("tests/replays/m5.py")
    covered: set[str] = set()
    for type_id, (_name, slug) in M5_CARD_NAMES.items():
        assert slug in scenarios.M5_SCENARIOS, f"牌名 {type_id} 的覆盖 slug 不在场景表：{slug}"
        assert slug in goals.M5_GOALS, f"牌名 {type_id} 的覆盖 slug 没有事后判据：{slug}"
        assert type_id in m5_source, f"牌名 {type_id} 没有出现在构造局面的脚本里"
        covered.add(slug)
    assert covered <= set(scenarios.M5_SCENARIOS)


def test_the_ten_a3_cover_items_map_to_the_eight_m5_replays() -> None:
    """A3 的 10 个覆盖项必须全部落到 8 条 M5 金录像上（一条可覆盖多项）。"""
    from replays import goals, scenarios

    assert len(M5_A3_ITEMS) == 10, "spec §6 A3 恰 10 个覆盖项"
    used: set[str] = set()
    for item, slugs in M5_A3_ITEMS.items():
        assert slugs, f"A3 覆盖项『{item}』没有任何金录像"
        for slug in slugs:
            assert slug in scenarios.SCENARIOS, f"A3『{item}』指向不存在的 slug：{slug}"
            assert slug in goals.M5_GOALS, f"A3『{item}』的 slug 没有判据：{slug}"
            used.add(slug)
    assert len(scenarios.M5_SCENARIOS) >= 8, "M5 金录像至少 8 条"
    assert used == set(scenarios.M5_SCENARIOS), "每条 M5 金录像都必须至少覆盖一个 A3 项"


def test_the_four_ai_positions_are_named_in_the_ai_military_suite() -> None:
    """A3 的 AI 4 条构造局面：逐条点名，删 / 改名即红。"""
    source = _read("tests/test_m5_ai_military.py")
    assert len(M5_AI_POSITIONS) == 4, "spec §6 A3 恰 4 条 AI 构造局面"
    for label, test_name in M5_AI_POSITIONS:
        assert test_name in source, f"AI 构造局面『{label}』的用例被删或改名：{test_name}"


def test_the_military_all_ai_seed_batch_is_eight_fixed_seeds_of_the_160_profile() -> None:
    """A2 的全 AI 局：≥8 个固定 160 种子（32 种子为选做，读数记在票里）。"""
    import sys

    if str(ROOT / "tests") not in sys.path:
        sys.path.insert(0, str(ROOT / "tests"))
    from support import ACCEPTANCE_SEEDS

    source = _read("tests/test_all_ai_games.py")
    assert "MILITARY_SEEDS: tuple[int, ...] = ACCEPTANCE_SEEDS[:8]" in source
    assert 'PACK_EX_160 = load_content_pack(deck_profile="standard_ex_160")' in source
    assert '@pytest.mark.parametrize("seed", MILITARY_SEEDS)' in source
    assert "test_the_standard_ex_160_group_finishes_cleanly_and_replays" in source
    assert len(ACCEPTANCE_SEEDS) >= 8, "验收种子批至少 8 个"
    assert tuple(ACCEPTANCE_SEEDS[:8]) == tuple(range(136, 144)), (
        "军争全 AI 批的种子必须是 136–143（改批 = 一次显式决定）"
    )
