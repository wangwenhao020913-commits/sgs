"""回放与基准：日志反序列化、版本闸门、行动序列投影与全量逐行比对（票 10 / 票 15 / 票 19）。

落点 = 出料边界（``sgs.engine.read_journal`` / ``sgs.agents.tape_from_journal``）
与主缝（``Engine.new_game`` + ``TapePlayer`` + ``dump_journal``）。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from replays import distribution, goals, scenarios
from sgs.agents import TapeError, TapePlayer, tape_from_journal
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import (
    EVENT_SCHEMA_VERSION,
    Engine,
    JournalVersionError,
    dump_journal,
    read_journal,
)
from sgs.state import ActivateSkill, QueryKind

from support import play_standard

PACK = load_content_pack(deck_profile="basic_53")
"""回放对照用的档位：M1 的 ``play_standard`` 走 53 张切片（票 16 §9）。"""
REPLAYS_DIR = Path(__file__).resolve().parent / "replays"

REPLAYS_TOTAL_LIMIT_BYTES = 48 * 1024 * 1024
REPLAY_SINGLE_LIMIT_BYTES = 4 * 1024 * 1024
"""``tests/replays/*.jsonl``（**基准记录流**，不含 README / 生成器代码）的体积预算（retro-02）：
总量 48 MiB、单文件 4 MiB。

**这是绊线**：它照样会让门禁变红，所以每次增长都看得见；但调高阈值本身是被允许的**显式**动作，
不是不可动的硬门——后续里程碑的基准可能合法地更大，只是不许悄悄长大。
阈值 = retro-02 落地时的实测值 + 合理余量；实测基线（2026-10，票 32 后 34 条基准）：
34 条 ``*.jsonl`` 合计 **33,586,853 字节 ≈ 32.03 MiB**，最大单文件
**2,986,975 字节 ≈ 2.85 MiB**（``30-halberd-three-targets.jsonl``）；票 14 加 8 条武将
专项基准后 42 条合计 **37,480,092 字节 ≈ 35.74 MiB**，最大单文件 **1,019,785 字节
≈ 0.97 MiB**（``38-hero-lost-cards.jsonl``），仍在上限内。

**伤害后技能修复（本轮）后的实测**：42 条合计 **41,087,553 字节 ≈ 39.18 MiB**，最大单文件
**2,986,975 字节 ≈ 2.85 MiB**（``30-halberd-three-targets.jsonl``）——仍在既有两句上限之内，
故**不动预算**。本轮改动是修 bug：【遗计】【奸雄】原先"任何人受伤都触发"（见
``damage_after._hurt_owner``），修好后郭嘉 / 曹操不再因别人挨打而白拿牌，四条基准
（35 / 36 / 37 / 41）的对局走向随之改变，全部由 ``pytest --update-golden`` 重录；
其中 35 的判据（【遗计】逐点 ≥2 次）在新行为下不再命中原种子 150，按本模块纪律
**重新选种**为 109（记录条数 3111 → 925），而不是放宽判据。体积根源是记录流内联
完整 ``Query`` / ``PlayerView``（随弃牌堆增长），不是种子没挑短——挑选已按"记录条数最少"
做过（``seed_search.py``）。门禁每次都要读入并逐行回放全部体积
（``test_golden_replay_matches_byte_for_byte``）。

**票 13（【反馈】【流离】条件自校）后的实测**：42 条合计 **44,584,106 字节 ≈ 42.52 MiB**，
最大单文件 **2,999,427 字节 ≈ 2.86 MiB**（``42-hero-transform.jsonl``）——仍在两句上限之内，
故**不动预算**。条件收紧**减少**询问 ⇒ 含【反馈】的 35 / 36 / 37 与含【流离】的 42 四条基准
的对局走向改变，由 ``pytest --update-golden`` 重录（四条在 ``goals.py`` 里的判据——逐点伤害 /
改判循环 / 势力求助 / 转化族——重录后仍命中，判据未放宽，种子也未重选；41 未受影响）。

**W4-5（预发动金录像重录）后的实测**：44 条合计 **45,735,934 字节 ≈ 43.62 MiB**，最大单文件
**3,229,429 字节 ≈ 3.08 MiB**（``42-hero-transform.jsonl``）——仍在两句上限之内，故**不动预算**。
本轮把 8 条技能 / 5 条转化 / 反间的预发动接入落到记录流：出牌阶段的 ``ActivateSkill`` 骨架被删
（可用性只在 ``preactivations`` 清单里），转化技的区域默认改变候选面，随机驱动的行动序列因此
偏移——7 条基准（36 / 37 / 38 / 39 / 41 / 42 / 43）由 ``pytest --update-golden`` 重录；其中 38 的
判据（【连营】+【枭姬】+ 顶替）在新行为下不再命中原种子 23，按本模块纪律**重新选种**为 579
（记录条数 1486 → 897），而不是放宽判据；另新增脚本专项 44（取消零副作用，约 140 KiB）。
40 仍逐字节不变。

**W4-fix-2（转化技只经预发动）后的实测**：44 条合计 **42,680,689 字节 ≈ 40.70 MiB**，最大单文件
**2,986,975 字节 ≈ 2.85 MiB**（``30-halberd-three-targets.jsonl``）——仍在两句上限之内，故**不动预算**。
本轮把转化候选从 ``legal_actions`` 收回（用户裁定 A），随机驱动的可选面变小 ⇒ 9 条基准变动：
``11 / 38 / 40`` 重新选种（旧种子不再命中判据 / 11 的记录涨过单文件预算），``29 / 42`` 改**脚本驱动**
（随机驱动产不出转化动作，脚本按预发动清单确认一次），``37 / 39 / 43 / 44`` 同种子重录（首个分歧
都是"查询少了转化候选"，其后是下游动作序列 / 记录条数的变化）。``goals.py`` 一字未改，
确认落账的仍是那条普通 ``UseCard(虚拟牌, 目标)`` / ``PlayCard``。"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _journal_with_version(version: str) -> str:
    """拿一条真实记录流，只替换首行的版本值（保持 canonical 形状）。"""
    text = play_standard(seed=7).journal()
    line, rest = text.split("\n", 1)
    payload = json.loads(line)
    payload["event_schema_version"] = json.loads(version)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" + rest


# ---------------------------------------------------------------------------
# 日志反序列化 = 唯一入口（票 15 §2）
# ---------------------------------------------------------------------------


def test_read_journal_is_the_symmetric_deserialiser_of_dump_journal(tmp_path):
    text = play_standard(seed=7).journal()
    path = _write(tmp_path / "game.jsonl", text)

    records = read_journal(path)

    assert records[0].kind == "header"
    assert records[0].event_schema_version == EVENT_SCHEMA_VERSION
    assert dump_journal(records) == text, "唯一序列化器与唯一反序列化器互为逆"


def test_read_journal_rejects_an_unknown_event_schema_version_and_reports_both(tmp_path):
    path = _write(tmp_path / "future.jsonl", _journal_with_version("99"))

    with pytest.raises(JournalVersionError) as caught:
        read_journal(path)

    assert caught.value.file_version == 99
    assert caught.value.supported_versions == (EVENT_SCHEMA_VERSION,)
    message = str(caught.value)
    assert "99" in message and str(EVENT_SCHEMA_VERSION) in message, "拒绝时必须报双方版本"
    assert str(path) in message


def test_read_journal_says_whether_the_file_is_too_old_or_too_new(tmp_path):
    """票 16 §4：报错补方向词，好让"旧引擎读新文件"与"新引擎读旧文件"一眼分清。"""
    old = _write(tmp_path / "old.jsonl", _journal_with_version("1"))
    with pytest.raises(JournalVersionError) as stale:
        read_journal(old)
    assert "过旧" in str(stale.value), "低于最低支持版本 ⇒ 文件过旧"

    new = _write(tmp_path / "new.jsonl", _journal_with_version("3"))
    with pytest.raises(JournalVersionError) as ahead:
        read_journal(new)
    assert "过新" in str(ahead.value), "高于最高支持版本 ⇒ 文件过新"


def test_the_repository_contains_no_pre_v2_journals():
    """票 16 §4 的机械断言：仓库里每个 ``*.jsonl`` 的 header 版本都是当前版本。"""
    for path in _golden_files():
        header = read_journal(path)[0]
        assert header.event_schema_version == EVENT_SCHEMA_VERSION, (
            f"{path.name} 仍是 v{header.event_schema_version}；v1 残留即红"
        )


def test_v2_card_moves_carry_zone_coordinates_instead_of_redundant_seat_fields():
    """票 16 §3 的永久形状断言：区域坐标化、无 ``from_seat`` / ``to_seat``、归属在场。"""
    from sgs.engine import CardMoveRecord

    assert "from_seat" not in CardMoveRecord.model_fields
    assert "to_seat" not in CardMoveRecord.model_fields

    records = read_journal(REPLAYS_DIR / "01-slash-dodge-cancel.jsonl")
    moves = [
        record
        for record in records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    assert moves, "这条基准必有牌移动"
    for move in moves:
        dumped = move.model_dump(mode="json")
        assert set(dumped["from_zone"]) == {"zone", "seat"}, "区域引用是坐标对象"
        assert set(dumped["to_zone"]) == {"zone", "seat"}
        assert "owner" in dumped, "归属字段保留"

    query = next(record for record in records if record.kind == "query")
    for player in query.query.view.players:
        dumped = player.model_dump(mode="json")
        assert "equip" in dumped and "judge" in dumped
        assert "hand_limit" not in dumped, "手牌上限是派生函数，不是公开字段"


def test_no_baseline_records_a_move_whose_source_and_destination_zone_are_the_same():
    """**没有**一条 ``card_move`` 的 ``from_zone == to_zone``（code-review 复核的机械不变量）。

    同区移动（``hand → hand`` / ``discard → discard``）在一次"记录流重入"里是可观测的缺陷
    形状：``MOVE_AFTER`` 钩子提问把移动挂起、恢复后承载帧原地重跑同一个 op，第二遍
    ``move_card`` 就会把**已经搬到目标区**的牌"再搬一次"——来源坐标与目标坐标因此相同。
    真正的移动不可能以自己为来源，故这条判据既**完备**（缺陷必然被记下来）又**不误报**
    （跨座位的 ``hand → hand`` 交给是合法的，两个区域坐标不同，不在此列）。

    逐份基准都查：军争 hero 基准（M3）先出现过 5 条幻影，判据必须覆盖整份文件集合，
    而不是只钉住当时出问题的那两条。
    """
    violations: list[str] = []
    for path in _golden_files():
        for record in read_journal(path):
            if record.kind != "delta" or record.delta_type != "card_move":
                continue
            if record.from_zone == record.to_zone:
                violations.append(
                    f"{path.name}: seq={record.seq} "
                    f"{record.from_zone.zone.value} → {record.to_zone.zone.value} "
                    f"（牌 {[card.card_id for card in record.cards]}，reason={record.reason.value}）"
                )

    assert not violations, (
        "基准里出现了同区移动（重入重放搬牌的幻影 delta）：\n" + "\n".join(violations)
    )


def test_read_journal_treats_a_missing_version_as_a_structural_error(tmp_path):
    line, rest = play_standard(seed=7).journal().split("\n", 1)
    payload = json.loads(line)
    del payload["event_schema_version"]
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" + rest
    path = _write(tmp_path / "no-version.jsonl", text)

    with pytest.raises(Exception) as caught:
        read_journal(path)

    assert not isinstance(caught.value, JournalVersionError), "缺版本号是结构错误，不是版本不符"


def test_read_journal_treats_a_non_integer_version_as_a_structural_error(tmp_path):
    path = _write(tmp_path / "string-version.jsonl", _journal_with_version('"1"'))

    with pytest.raises(Exception) as caught:
        read_journal(path)

    assert not isinstance(caught.value, JournalVersionError), "非整数版本号是结构错误，不是版本不符"


# ---------------------------------------------------------------------------
# tape 投影与回放执行者（票 10 §7 / 票 05 §6）
# ---------------------------------------------------------------------------


def test_tape_from_journal_returns_the_seed_and_the_actions_in_line_order(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())

    seed, tape = tape_from_journal(read_journal(path))

    assert seed == 7
    assert tape == tuple(
        record.action for record in engine.records if record.kind == "action"
    )
    assert tape, "随机玩家的一局里必然有应答"


def test_replaying_the_tape_reproduces_the_journal_byte_for_byte(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)
    player = TapePlayer(tape)
    replayed.run([player, player])

    assert dump_journal(replayed.records) == engine.journal()


def test_tape_player_reports_a_divergence_when_the_tape_runs_out(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)

    with pytest.raises(TapeError):
        replayed.run([TapePlayer(tape[:-1])] * 2)


def test_tape_player_reports_a_stale_answers_seq(tmp_path):
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))
    stale = (tape[0].model_copy(update={"answers_seq": tape[0].answers_seq + 1}), *tape[1:])

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)

    with pytest.raises(TapeError):
        replayed.run([TapePlayer(stale)] * 2)


# ---------------------------------------------------------------------------
# Golden Replay：录制入口、全量逐行比对与索引互等（票 10 / 票 19）
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """录制模式遍历场景清单；验证模式遍历基准文件（票 10 §6）。"""
    if "slug" not in metafunc.fixturenames:
        return
    if metafunc.config.getoption("--update-golden"):
        slugs = list(scenarios.SCENARIOS)
    else:
        slugs = [path.stem for path in _golden_files()]
    metafunc.parametrize("slug", sorted(slugs))


def _golden_files(directory: Path = REPLAYS_DIR) -> list[Path]:
    """``directory`` 下的基准文件（默认 = 仓库的 ``tests/replays/``）。

    目录是入参，好让体积预算**复用**"哪些文件算基准"这一个定义（retro-02），
    而不是在别处再 glob 一次。
    """
    return sorted(directory.glob("*.jsonl"))


_README_ROW = re.compile(r"^\|\s*`([0-9]{2}-[a-z0-9-]+)`\s*\|")


def _readme_slugs() -> set[str]:
    text = (REPLAYS_DIR / "README.md").read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in (_README_ROW.match(line) for line in text.splitlines())
        if match is not None
    }


def test_golden_replay_matches_byte_for_byte(slug: str, tmp_path: Path, update_golden: bool) -> None:
    """**真的重跑引擎**再逐行比对；禁止把文件与它自身比较（票 10 §1）。"""
    path = REPLAYS_DIR / f"{slug}.jsonl"
    if update_golden:
        _write(path, scenarios.record(slug))

    expected = path.read_text(encoding="utf-8")
    records = read_journal(path)
    header = records[0]
    # 回放以 header 为准重开一局：header 声明的 pack / deck 必须能被当前数据加载，
    # 且**就是本次运行选中的档**（不是包默认档——多档并存后两者可以不同，票 16 §8）。
    pack = load_content_pack(header.pack_id, deck_profile=header.deck_profile)
    assert pack.deck_profile == header.deck_profile, (
        "header 声明的 pack / deck 必须能被当前数据加载"
    )
    seed, tape = tape_from_journal(records)
    scenario = scenarios.SCENARIOS[slug]
    # 武将**不是** header 的一部分（武将不是牌、没有 delta）：专项基准的逐座位武将以
    # ``scenarios.SCENARIOS`` 为唯一真源（与"档位 / 人数"同一姿态，票 14 §8）。
    engine = Engine.new_game(
        seed=seed,
        rule_config=header.rule_config,
        pack=pack,
        heroes=scenario.heroes,
        hero_pool=scenario.hero_pool,
    )
    player = TapePlayer(tape)
    engine.run([player] * header.rule_config.player_count)

    actual = dump_journal(engine.records)
    if actual != expected:
        artifact = tmp_path / f"{slug}.actual.jsonl"
        artifact.write_text(actual, encoding="utf-8")
        raise AssertionError(f"{_first_divergence(expected, actual)}\n本次实际记录流：{artifact}")

    if slug in goals.M1_GOALS or slug in goals.M2_GOALS or slug in goals.M3_GOALS:
        # 门禁期**真的**验证这条基准在证它声称的机制（票 32 §1；M2 用"随机驱动 + 事后判据"
        # 替代 M1 的录制期脚本，故判据必须在这里也生效，而不是只在 --update-golden 时；
        # M3 的专项基准同一口径，票 14 §8；脚本专项基准由票 17 起登记在 M1_GOALS）。
        assert goals.goal_for(slug)(engine), (
            f"{slug}：回放后的记录流没有命中它在 goals.py 里声称的机制——"
            f"基准与判据脱节（要么换种子，要么修判据）"
        )


def test_readme_index_matches_the_golden_file_set() -> None:
    assert _readme_slugs() == {path.stem for path in _golden_files()}, (
        "索引表与基准文件集合互相等于（防孤儿基准 / 孤儿行）"
    )


_README_FULL_ROW = re.compile(
    r"^\|\s*`([0-9]{2}-[a-z0-9-]+)`\s*\|[^|]*\|([^|]*)\|([^|]*)\|([^|]*)\|"
)


def test_the_readme_declares_the_class_deck_and_player_count_of_each_baseline() -> None:
    """票 32 §1：README 的「类 / 档位 / 人数」三列必须与真源逐条相等。"""
    declared: dict[str, tuple[str, str, str]] = {}
    for line in (REPLAYS_DIR / "README.md").read_text(encoding="utf-8").splitlines():
        match = _README_FULL_ROW.match(line)
        if match is not None:
            slug, classes, deck, players = match.groups()
            declared[slug] = (classes.strip(), deck.strip().strip("`"), players.strip())

    expected = {
        slug: (
            "、".join(distribution.SLUG_CLASSES[slug]),
            scenario.deck_profile,
            str(scenario.player_count),
        )
        for slug, scenario in scenarios.SCENARIOS.items()
    }

    assert declared == expected, "README 的类 / 档位 / 人数三列与 distribution / scenarios 不一致"


def test_the_readme_index_table_is_exactly_what_the_generator_produces() -> None:
    """票 32 §1：索引表由 ``replays/readme_index.py`` 生成，手改表格行即漂移。"""
    from replays import readme_index

    text = (REPLAYS_DIR / "README.md").read_text(encoding="utf-8")

    assert readme_index.replace_table(text) == text, (
        "README 的索引表与 readme_index.table() 不一致；"
        "用 `python -m replays.readme_index --write` 重新生成"
    )


def test_the_readme_index_names_cover_exactly_the_baseline_set() -> None:
    """票 32 §1：中文名 / 规则条目必须逐条登记，不得有孤儿或漏项。"""
    from replays import readme_index

    assert set(readme_index.NAMES) == set(scenarios.SCENARIOS), (
        "索引表的中文名 / 规则条目必须逐条登记，不得有孤儿或漏项"
    )


FROZEN_BASELINES = (
    "01-slash-dodge-cancel",
    "02-slash-hit-damage",
    "03-near-death-self-peach",
    "04-near-death-other-peach",
    "05-near-death-death",
    "06-deck-reshuffle",
)
"""票 12 定格的六条 M1 基准。

**故意写死、不从 ``scenarios.SCENARIOS`` 派生**：那张表是录制源，若从这里派生，新增 /
删除一条基准就会连同场景一起被"自动追认"，钉不住票 12 的六条。新增基准是对票据的改动，
必须先改票，再改这里。"""


def test_the_frozen_m1_six_stay_in_the_file_set() -> None:
    """M1 六条必须在基准文件集合里原样保留（票 32 §1：回归网不因 M2/M3 扩容而消失）。"""
    assert set(FROZEN_BASELINES) <= {path.stem for path in _golden_files()}
    assert set(FROZEN_BASELINES) == set(scenarios.M1_SCENARIOS)


M3_BASELINES = tuple(scenarios.M3_SCENARIOS)
"""M3 的 8 条武将技能专项基准（票 14 §8）。"""

SCRIPTED_BASELINES = tuple(scenarios.SCRIPTED_SCENARIOS)
"""票 17 起的脚本驱动专项基准（与 M1 六条同一驱动，但不属于票 12 定格的回归网）。"""

M2_BASELINES = tuple(
    name
    for name in distribution.SLUG_CLASSES
    if name not in set(FROZEN_BASELINES)
    and name not in set(M3_BASELINES)
    and name not in set(SCRIPTED_BASELINES)
)
"""M2 的 28 条基准：分布表里除 M1 六条、M3 八条与脚本专项以外的全部 slug（票 32 §1）。"""


def test_the_baseline_distribution_meets_every_category_minimum() -> None:
    """每类达标：八类必需表逐类计数 ≥ 最少条数（票 17 §1 的机械载体）。"""
    slugs = {path.stem for path in _golden_files()}
    counts = Counter(
        category
        for slug in slugs
        for category in distribution.SLUG_CLASSES.get(slug, ())
    )

    shortfalls = {
        category: (counts[category], minimum)
        for category, minimum in distribution.CATEGORY_MINIMUMS.items()
        if counts[category] < minimum
    }
    assert not shortfalls, f"分布判据未达标（实得 / 最少）：{shortfalls}"
    assert counts[distribution.M1_CLASS] == len(FROZEN_BASELINES)


def test_every_baseline_is_classified_and_the_registry_has_no_orphan() -> None:
    """无未分类基准：文件集合 == 类表键集 == 场景表键集（三个集合互相等于）。"""
    files = {path.stem for path in _golden_files()}

    assert files == set(distribution.SLUG_CLASSES), (
        "每个基准文件都必须在 distribution.SLUG_CLASSES 里登记；反之不得有孤儿登记"
    )
    assert files == set(scenarios.SCENARIOS), "场景表与基准文件集合互相等于"
    assert set(M2_BASELINES) == files - set(FROZEN_BASELINES) - set(M3_BASELINES) - set(
        SCRIPTED_BASELINES
    ), "M2 恰为 44 − 6 − 8 − 2 = 28 条"
    assert set(M3_BASELINES) == set(scenarios.M3_SCENARIOS) == set(goals.M3_GOALS), (
        "M3 的 8 条专项基准必须与场景表、判据表三方相等"
    )
    assert set(SCRIPTED_BASELINES) == set(scenarios.SCRIPTED_SCENARIOS), (
        "票 17 起的脚本专项基准必须与场景表相等"
    )


def test_every_m2_baseline_has_an_after_the_fact_goal() -> None:
    """28 条 M2 基准都必须有事后判据（goals.py 的登记不得漏）。"""
    assert set(M2_BASELINES) == set(goals.M2_GOALS), (
        "M2 基准集合与 goals.M2_GOALS 的键集必须相等"
    )


def test_every_scripted_baseline_has_an_after_the_fact_goal() -> None:
    """脚本专项基准也必须独立复读一遍记录流（票 17）：脚本宣布命中 ≠ 记录流里有那回事。"""
    assert set(SCRIPTED_BASELINES) == set(goals.M1_GOALS), (
        "脚本专项基准集合与 goals.M1_GOALS 的键集必须相等"
    )


def test_the_preativation_cancel_equals_the_not_activating_path() -> None:
    """W4-5：**取消零副作用**——取消与"从不进预发动"两条路径的后续记录逐字节相等。

    录制期 ``scenarios._record_scripted`` 已判一次；这里是**门禁期**的复算：同一颗种子
    重跑两遍，取消路径会按清单填好复合动作并交给引擎同一个校验器（``validate_activation``），
    然后**不提交**。两条记录流必须一字不差——取消是接入层本地态，读清单 / 填表 / 取消都
    不得在记录流里留下任何东西。
    """
    cancel, plain = scenarios.preactivation_cancel_pair()

    assert dump_journal(cancel.records) == dump_journal(plain.records), (
        "取消与不发动两条路径的后续记录必须一致（取消不得留下记录 / 副作用）"
    )
    offered = [
        record
        for record in cancel.records
        if record.kind == "query"
        and record.query.kind == QueryKind.PLAY_PHASE
        and goals.PREACTIVATION_CANCEL_SKILL in record.query.preactivations
    ]
    assert offered, "取消专项的前提：出牌阶段询问上必须真的随附【制衡】清单"
    assert not [
        record
        for record in cancel.records
        if record.kind == "action"
        and record.accepted
        and isinstance(record.action, ActivateSkill)
        and record.action.skill_id == goals.PREACTIVATION_CANCEL_SKILL
    ], "取消（不提交）⇒ 记录流里没有该技能的 ActivateSkill"


M1_FROZEN_TRAJECTORIES: dict[str, tuple[int, int, str]] = {
    "01-slash-dodge-cancel": (
        101,
        77,
        "ddd9a0ab0ea5e5545a38f3046ad7ccf4e6abf1bf3eb155ce8d6cb000fbe5b3dc",
    ),
    "02-slash-hit-damage": (
        4,
        78,
        "7aea8dbe8df17e414e8f19762b2656cf725aadbad0f603c4af5e32c7c6dd212f",
    ),
    "03-near-death-self-peach": (
        28,
        113,
        "d5c0e99a3eed1a12d82a3a1331c9b819dbd1472e862f36d5c287b1d05a7745b9",
    ),
    "04-near-death-other-peach": (
        9,
        78,
        "cc96e111b75f96ac654e779c6059172e853b552ccb12b83a071deb9e751c8fd5",
    ),
    "05-near-death-death": (
        1,
        39,
        "a86a689998208b0ae6db912b79966f6611e91115b0f1da7a1b1a14f6de633938",
    ),
    "06-deck-reshuffle": (
        2026,
        84,
        "405ea88079d63f9446b6d94f3e3c99fa6a7593ad166433c14de3bf07b005bd0a",
    ),
}
"""M1 六条基准的**决策轨迹**摘要 ``(seed, len(tape), sha256(tape))``（票 16 §3）。

v2 升版证伪了"重录后差异只有 header 版本字段"：区域引用、``CardRef.slot``、
``PlayerPublic`` 新字段都落在 body。**替代判据**就是这张表——``tape`` 是决策内容、
delta 是它的确定性函数，冻结 tape 即冻结语义；hash 定义 = ``json.dumps`` 的
``ensure_ascii=False, sort_keys=True, separators=(",", ":")`` 后取 sha256。

值在**重录前**从 v1 文件算定并冻结；重录后逐条相等才说明升级没改语义。
失败诊断先比对 git 历史里的 v1 文件，而不是放宽这里的常量。"""

V2_CARD_FACE_ADDITIONS = ("slot", "attack_range", "color", "virtual_sources")
"""v2 给 ``CardRef`` 新增的**牌面事实**字段；进记录流但不承载任何决策。

票 16 §3 的字面定义（``model_dump`` 全量）是在 v1 的 ``CardRef`` 上算定的，而 v2 §2
给每张牌加了 ``slot``（``CardType.subcategory`` 的纯函数，无决策信息），票 29 再按同一
姿态加了 ``attack_range``（``CardType.attack_range`` 的纯函数，仅武器非空），票 30 加
``color``（由花色派生的纯函数）与 ``virtual_sources``（转化牌的来源实体牌，六条 M1
基准里恒空）。把新增牌面事实剔掉，六条冻结摘要才真正回答"升版有没有改语义"——
**登记为对票 16 §3 的就地细化**：本票不在静态表上覆盖那六个数，而是让定义与它们相容。"""


def _decision_dump(payload):
    """递归剔除 v2 新增的牌面事实字段，得到与 v1 ``CardRef`` 同形的决策载荷。"""
    if isinstance(payload, dict):
        return {
            key: _decision_dump(value)
            for key, value in payload.items()
            if key not in V2_CARD_FACE_ADDITIONS
        }
    if isinstance(payload, list):
        return [_decision_dump(item) for item in payload]
    return payload


def _tape_digest(tape) -> str:
    payload = json.dumps(
        [_decision_dump(action.model_dump(mode="json")) for action in tape],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_the_frozen_m1_decision_trajectories_are_unchanged() -> None:
    mismatches = []
    for slug, (seed, length, digest) in M1_FROZEN_TRAJECTORIES.items():
        actual_seed, tape = tape_from_journal(read_journal(REPLAYS_DIR / f"{slug}.jsonl"))
        actual = (actual_seed, len(tape), _tape_digest(tape))
        if actual != (seed, length, digest):
            mismatches.append(f"{slug}: 期望 {(seed, length, digest)}，实得 {actual}")

    assert not mismatches, (
        "M1 的决策轨迹变了（升版不得改语义；先比对 git 里的 v1 文件）：\n"
        + "\n".join(mismatches)
    )


def test_every_declared_scenario_is_recorded() -> None:
    assert set(scenarios.SCENARIOS) == {path.stem for path in _golden_files()}


def test_the_divergence_report_names_the_first_line_seq_kind_and_field() -> None:
    expected = (
        '{"cards":[],"delta_type":"card_move","from_zone":"hand","kind":"delta",'
        '"owner":0,"reason":"USE","seq":7,"to_zone":"deck"}\n'
    )
    actual = (
        '{"cards":[],"delta_type":"card_move","from_zone":"hand","kind":"delta",'
        '"owner":0,"reason":"USE","seq":7,"to_zone":"discard"}\n'
    )

    report = _first_divergence(expected, actual)

    assert "第 1 行" in report
    assert "seq=7" in report and "kind=delta" in report
    assert "to_zone" in report and "deck" in report and "discard" in report


def test_the_divergence_report_flags_a_length_mismatch() -> None:
    longer = _first_divergence('{"kind":"timing"}\n', '{"kind":"timing"}\n{"kind":"timing"}\n')
    shorter = _first_divergence('{"kind":"timing"}\n{"kind":"timing"}\n', '{"kind":"timing"}\n')

    assert "第 2 行" in longer and "多出" in longer
    assert "第 2 行" in shorter and "缺失" in shorter


def _first_divergence(expected: str, actual: str) -> str:
    """报首个分歧的行号 + ``seq`` / ``kind`` + 首个不同字段路径与两侧取值（票 10 §2）。"""
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for index in range(min(len(expected_lines), len(actual_lines))):
        if expected_lines[index] != actual_lines[index]:
            return _describe_line(index + 1, expected_lines[index], actual_lines[index])
    if len(expected_lines) != len(actual_lines):
        line_no = min(len(expected_lines), len(actual_lines)) + 1
        if len(actual_lines) > len(expected_lines):
            return f"首个分歧：第 {line_no} 行是首个多出的行（基准只有 {len(expected_lines)} 行）"
        return f"首个分歧：第 {line_no} 行是首个缺失的行（基准有 {len(expected_lines)} 行）"
    return "两个记录流逐行相同"


def _describe_line(line_no: int, expected_line: str, actual_line: str) -> str:
    expected = json.loads(expected_line)
    actual = json.loads(actual_line)
    where = (
        f"seq={expected.get('seq', '（信封）')} kind={expected.get('kind', '（信封）')}"
    )
    diff = _first_field_diff(expected, actual, "")
    if diff is None:  # pragma: no cover - 字节不同但解析相同：只能是空白差异
        return f"首个分歧：第 {line_no} 行 {where}（逐行字节不同）"
    path, left, right = diff
    return (
        f"首个分歧：第 {line_no} 行 {where}；"
        f"字段 {path or '/'}：期望 {_truncate(left)}，实得 {_truncate(right)}"
    )


def _first_field_diff(left: object, right: object, path: str):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in [*left, *(key for key in right if key not in left)]:
            child = f"{path}.{key}" if path else key
            if key not in left:
                return (child, None, right[key])
            if key not in right:
                return (child, left[key], None)
            found = _first_field_diff(left[key], right[key], child)
            if found is not None:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        for index in range(min(len(left), len(right))):
            found = _first_field_diff(left[index], right[index], f"{path}[{index}]")
            if found is not None:
                return found
        if len(left) != len(right):
            return (f"{path}.length", len(left), len(right))
        return None
    if left != right:
        return (path, left, right)
    return None


def _truncate(value: object, limit: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


# ---------------------------------------------------------------------------
# 体积预算绊线（retro-02）
# ---------------------------------------------------------------------------


def _mib(size: int) -> str:
    return f"{size / 1024 / 1024:.2f} MiB（{size} 字节）"


def _size_budget_violations(
    directory: Path,
    *,
    total_limit: int = REPLAYS_TOTAL_LIMIT_BYTES,
    single_limit: int = REPLAY_SINGLE_LIMIT_BYTES,
) -> list[str]:
    """把 ``directory`` 下的基准记录流（``*.jsonl``）与体积预算对照，返回可读的越界条目
    （空列表 = 在预算内；README / 生成器代码不计入，那部分增长可忽略且不稳定）。

    纯函数：目录与两条上限都是入参，故诊断路径可以拿小阈值直接验，不必真造 48 MiB 数据。
    """
    sizes = [(path, path.stat().st_size) for path in _golden_files(directory)]
    total = sum(size for _, size in sizes)
    largest = max((size for _, size in sizes), default=0)

    over: list[str] = []
    if total > total_limit:
        over.append(f"总量 {_mib(total)} > 总预算 {_mib(total_limit)}")
    over.extend(
        f"{path.name}：{_mib(size)} > 单文件上限 {_mib(single_limit)}"
        for path, size in sizes
        if size > single_limit
    )
    if not over:
        return []

    return [
        "golden replay 体积超出预算（绊线：增长必须被看见，不是硬门）：",
        *(f"- {line}" for line in over),
        f"实测：{len(sizes)} 条基准共 {_mib(total)}，最大单文件 {_mib(largest)}。",
        "改预算 = 一次显式决定：确认增长合理后，在同一次改动里更新 "
        "REPLAYS_TOTAL_LIMIT_BYTES / REPLAY_SINGLE_LIMIT_BYTES 并把新的实测值记进注释。",
    ]


def test_the_size_budget_names_the_offending_file_and_the_way_out(tmp_path: Path) -> None:
    """超预算时必须报出：当前值、超出的文件、以及"改预算 = 一次显式决定"（retro-02）。"""
    oversized = tmp_path / "35-a-baseline-that-grew.jsonl"
    oversized.write_bytes(b"x" * (REPLAY_SINGLE_LIMIT_BYTES + 1))
    (tmp_path / "01-slash-dodge-cancel.jsonl").write_bytes(b"x" * 16)

    violations = _size_budget_violations(tmp_path)

    assert violations, "单文件超过上限必须绊线"
    report = "\n".join(violations)
    assert oversized.name in report, "失败消息必须点名超出的文件"
    assert "单文件上限" in report, "失败消息必须写明被突破的是哪条预算"
    assert "显式决定" in report, "失败消息必须写明改预算 = 一次显式决定"


def test_the_size_budget_trips_on_the_total_even_when_no_single_file_is_huge(tmp_path: Path) -> None:
    """总量与单文件是两条独立预算：一堆平庸文件堆起来同样要绊线（retro-02）。"""
    for index in range(3):
        (tmp_path / f"0{index}-small.jsonl").write_bytes(b"x" * 10)

    violations = _size_budget_violations(tmp_path, total_limit=25, single_limit=1024)

    report = "\n".join(violations)
    assert "总量" in report, "总量越界必须绊线"
    assert "单文件上限" not in report, "没有单文件越界时不得误报单文件预算"


def test_the_size_budget_stays_quiet_within_the_limits(tmp_path: Path) -> None:
    (tmp_path / "01-slash-dodge-cancel.jsonl").write_bytes(b"x" * 16)

    assert _size_budget_violations(tmp_path) == []


def test_the_golden_replays_fit_within_their_size_budget() -> None:
    """绊线本体：``tests/replays/`` 的全部 ``*.jsonl`` 必须在预算内（retro-02）。

    超了不要急着调大常量——先看是"合法新增基准"还是"记录流无谓变胖"；两者都要在
    同一次改动里显式记账（见常量处的实测基线）。
    """
    violations = _size_budget_violations(REPLAYS_DIR)

    assert not violations, "\n".join(violations)
