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
from sgs.data import Identity, Mode, PveVictoryKind, RuleConfig, load_content_pack
from sgs.engine import (
    EVENT_SCHEMA_VERSION,
    Engine,
    JournalVersionError,
    dump_journal,
    read_journal,
    solve_pve_outcome,
)
from sgs.skills import blocked_true_names
from sgs.state import ActivateSkill, QueryKind, UseCard, WindowCause, Zone
from sgs.timing import Timing

from support import play_standard

PACK = load_content_pack(deck_profile="basic_53")
"""回放对照用的档位：M1 的 ``play_standard`` 走 53 张切片（票 16 §9）。"""
REPLAYS_DIR = Path(__file__).resolve().parent / "replays"

REPLAYS_TOTAL_LIMIT_BYTES = 52 * 1024 * 1024
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
确认落账的仍是那条普通 ``UseCard(虚拟牌, 目标)`` / ``PlayCard``。

**W5-02b（借刀翻转）后的实测**：46 条合计 **51,080,844 字节 ≈ 48.71 MiB**，最大单文件
**3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——**总预算 48 → 52 MiB 的
一次显式上调**（单文件上限仍为 4 MiB）。上调的两条理由都在实测里：一是新增两条合法基准
（45 auto-b / 46 借刀窗口内的转化【杀】，合计约 1.6 MiB），二是 20 份受影响录像的重录。
单文件绊线没有越过：10 / 11 / 38 在 02b 的候选面变化后旧种子不再命中判据（10 / 11 还会越过
4 MiB），均按本模块纪律**重新选种**而不是调单文件上限（10: 49 → 68、11: 99 → 35、
38: 364 → 388，判据与人数档不变）。

**W7-01（方天 × 借刀）后的实测**：47 条合计 **51,931,524 字节 ≈ 49.53 MiB**，最大单文件
**3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——仍在两句上限之内，
故**不动预算**。本轮只新增一条脚本专项 ``47-halberd-borrowed-sword``（约 0.81 MiB；方天的
``CANDIDATES`` 枚举换成 ``TARGET_COUNT`` + 共享组合构建子）：``30``（出牌阶段的方天）的候选
集合与顺序**逐字节不变**，``33 / 45 / 46``（无方天）同样不变，其余 42 条也没有变化——
``--update-golden`` 后 ``git status`` 只报这一个新文件。

**U2-12（遗计不可放弃）后的实测**：47 条合计 **51,663,256 字节 ≈ 49.27 MiB**，最大单文件
**3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——仍在两句上限之内，
故**不动预算**。删掉【遗计】选牌步的 ``Pass`` 后只有 35 / 41 两份基准变化：35 的 seed 109
不再命中判据（≥2 次逐点），按本模块纪律**重新选种**为 198（1112 → 804 条）；41 同种子 626
重录（1210 → 1283 条）。其余 45 条逐字节不变。

**M5-09a（军争金录像）后的实测**：55 条合计 **52,083,601 字节 ≈ 49.67 MiB**，最大单文件
**3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——仍在两句上限之内，
故**不动预算**。本轮只**新增** 8 条 160 档**构造局面**基准（48–55，合计 420,345 字节
≈ 0.40 MiB），标准 108 的 47 条逐字节不变：``git diff --diff-filter=M --name-only
dd029e5..HEAD -- 'tests/replays/*.jsonl'`` 为空即证（README / 生成器按票面同批更新，
不在"旧基准"之列）。新基准从人为局面起跑（``replays/m5.py``），不含随机发牌，
故体积只随回合数增长。

**M5-bugfix（铁索点击顺序）后的实测**：55 条合计 **52,084,021 字节 ≈ 49.67 MiB**，最大单文件
**3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——仍在两句上限之内，
故**不动预算**。出牌阶段的铁索候选按 ``preserve_target_order`` 多出**逆序**的那一条
（``[0, 1]`` 之外再有 ``[1, 0]``），于是**含铁索出牌阶段的 49 / 54** 两份 160 档构造基准由
``pytest --update-golden`` 重录：逐行比对只见那一条候选多出来（磁带上记录的动作一字未变，
``goals.py`` 的判据照旧命中）。标准 108 的 47 条与其余 6 条 M5 基准逐字节不变——出牌阶段
含铁索的只有这两条。

**table-ux-overhaul 票 04（需要牌按档逐档问）后的实测**：60 条合计 **50,610,471 字节
≈ 48.27 MiB**，最大单文件 **3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）
——仍在两句上限之内，故**不动预算**。需要牌窗口的候选面按档拆开后，一次需要牌事件可能问
2–3 次（每档一张面：「武将档 → 装备档 → 牌档」），随机驱动的行动序列也随之改变：9 条标准
108 基准（``09 / 17 / 20 / 30 / 31 / 34 / 35 / 36 / 37``）由
``pytest --update-golden`` 重录；其中 30（涨过 4 MiB 单文件绊线）与 17 / 34 / 35 / 36 / 37
（换了更长的随机局）按本模块纪律**重新选种**（30: 230 → 904、17: 117 → 163、
34: 288 → 187、35: 198 → 73、36: 169 → 167、37: 94 → 209，判据与人数档一律不变），
其余 51 条逐字节不变。

**table-ux-overhaul 票 13（座位多选收口）后的实测**：60 条合计 **50,609,283 字节
≈ 48.26 MiB**，最大单文件 **3,334,043 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）
——仍在两句上限之内，故**不动预算**。本条只动**一条** M6 构造基准：
``58-jie-luxun-hero-zone``（56,187 → 54,999 字节；151 行）—— 界陆逊【连营】的"令至多 X 名
各摸一张"由**逐座两次** ``SELECT_OPTION``（``lianying:seat:0`` / ``lianying:seat:1``）改成
**一次** ``SELECT_OPTIONS``（``lianying:seat``，不枚举档，``min=max=X``）；计划里那两步
因此合成一步"一次点齐座位 0 / 1"（`replays/m6.py` 的 ``Step.build``：不枚举档的答案由
计划组装、引擎按同一份形状谓词校验），同一帧里第二次【连营】的"重新发问"证据仍保留
（第二次是一次「放弃」）。``goals.py`` 的判据（``_m6_hero_zone``）**一字未改、一条未放宽**，
重录后仍命中；其余 59 条逐字节不变（``git status --porcelain tests/replays/`` 只报这一条），
种子也未重选（对局长度不变：153 → 151 条记录）。58 属于 M6 构造基准，**不在**
:data:`STANDARD_108_REPLAY_BYTES` 的 47 条冻结表里，故那张表无需改动。

**M7 票 07（header v3）后的实测**：60 条合计 **50,791,313 字节 ≈ 48.44 MiB**，最大单文件
**3,334,072 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——仍在两句上限之内，故
**不动预算**。本条不是行为改变：``rule_config`` 的 dump 多了 ``mode`` 与 ``pve``，于是
**全部 60 条**的首行都变、其余逐字节不变（逐文件核过"除首行外 sha256 相同"）——
23 条无身份局各涨 32 字节、37 条身份局各涨 29 字节（``"identity"`` 比 ``"no_identity"``
短 3 字节），合计 +1,809 字节；
``goals.py`` 判据与种子一个都没动；47 条冻结表的 sha256 因此一次重算（票 01 §2 授权的
**唯一一次** re-freeze）。票 12 的 6 条 PvE 基准是**新增**，不碰这 60 条。

**M7 票 12（6 条 PvE 基准）后的实测**：66 条合计 **50,989,205 字节 ≈ 48.63 MiB**，最大单文件
**3,334,072 字节 ≈ 3.18 MiB**（``10-identity-7p-lord-win.jsonl``）——新增的 6 条只占
**197,892 字节 ≈ 193 KiB**（构造局面、短局：2–5 座、1–2 轮），仍在 52 MiB 总预算与 4 MiB
单文件上限之内，故**不动预算**（"优先吃现有余量"；总余量仍有 ≈3.37 MiB）。这是**纯新增**：
``git status --porcelain tests/replays/`` 只报 6 个新 ``*.jsonl`` + ``pve.py``，既有 60 条
逐字节不变（47 条冻结表的 ``(字节数, sha256)`` 一条未改，``tools/check_goldens.sh`` 同批 PASS）。
六条的种子 / 终局类 / 座位见 :data:`PVE_OUTCOME_CLASSES`。
"""


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
    assert caught.value.supported_versions == (2, EVENT_SCHEMA_VERSION), (
        "支持集合是 {2, 3}：v2 档继续可播（M7 票 07），更旧 / 更新的档一律拒"
    )
    message = str(caught.value)
    assert "99" in message and str(EVENT_SCHEMA_VERSION) in message, "拒绝时必须报双方版本"
    assert str(path) in message


def test_read_journal_says_whether_the_file_is_too_old_or_too_new(tmp_path):
    """票 16 §4：报错补方向词，好让"旧引擎读新文件"与"新引擎读旧文件"一眼分清。"""
    old = _write(tmp_path / "old.jsonl", _journal_with_version("1"))
    with pytest.raises(JournalVersionError) as stale:
        read_journal(old)
    assert "过旧" in str(stale.value), "低于最低支持版本 ⇒ 文件过旧"

    new = _write(tmp_path / "new.jsonl", _journal_with_version(str(EVENT_SCHEMA_VERSION + 1)))
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


def test_replaying_a_no_hero_tape_keeps_the_drawn_true_names_empty(tmp_path):
    """票 05（E5）：无武将模式的一局里 ``drawn_true_names`` 一直是**空元组**。

    "没有任何初始化点"的判据：同一盘磁带重跑一遍，新字段仍空、派生集合仍空，而记录流
    逐字节不变。有武将的座位同样不写这个字段（在场那一半是派生的，见
    ``tests/test_hero_dealing.py::test_the_blocked_true_names_are_the_drawn_half_plus_the_seated_half``）。
    """
    engine = play_standard(seed=7)
    path = _write(tmp_path / "game.jsonl", engine.journal())
    seed, tape = tape_from_journal(read_journal(path))

    replayed = Engine.new_game(seed=seed, rule_config=RuleConfig(), pack=PACK)
    player = TapePlayer(tape)
    replayed.run([player, player])

    assert replayed.state.drawn_true_names == ()
    assert blocked_true_names(replayed.state) == frozenset()
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
    # 武将 / **构造局面**都不是 header 的一部分：逐座位武将与 M5 的初始手牌 / 装备 /
    # 牌堆顶以 ``scenarios.SCENARIOS``（及其 ``replays/m5.py`` 构造器）为唯一真源
    # （与"档位 / 人数"同一姿态，票 14 §8 / M5-09a）。回放与录制走**同一条**开局入口。
    engine = scenarios.engine_for(
        scenario, seed=seed, pack=pack, rule_config=header.rule_config
    )
    player = TapePlayer(tape)
    engine.run([player] * header.rule_config.player_count)

    actual = dump_journal(engine.records)
    if actual != expected:
        artifact = tmp_path / f"{slug}.actual.jsonl"
        artifact.write_text(actual, encoding="utf-8")
        raise AssertionError(f"{_first_divergence(expected, actual)}\n本次实际记录流：{artifact}")

    if (
        slug in goals.M1_GOALS
        or slug in goals.M2_GOALS
        or slug in goals.M3_GOALS
        or slug in goals.M5_GOALS
        or slug in goals.PVE_GOALS
    ):
        # 门禁期**真的**验证这条基准在证它声称的机制（票 32 §1；M2 用"随机驱动 + 事后判据"
        # 替代 M1 的录制期脚本，故判据必须在这里也生效，而不是只在 --update-golden 时；
        # M3 的专项基准同一口径，票 14 §8；脚本专项基准由票 17 起登记在 M1_GOALS）。
        assert goals.goal_for(slug)(engine), (
            f"{slug}：回放后的记录流没有命中它在 goals.py 里声称的机制——"
            f"基准与判据脱节（要么换种子，要么修判据）"
        )


def test_no_golden_replay_offers_a_non_dodge_card_in_a_dodge_window() -> None:
    """B1 的**金录像扫描**（防"重录时又把 bug 录回去"）。

    扫描全部金录像里的两类【闪】窗口——独立 ``DODGE`` 询问与 ``RESPOND`` +
    ``required="dodge"``（【万箭齐发】）——断言每条真牌候选的牌面类型都是【闪】。
    这条判据不依附某一条基准：删掉 / 重录 / 新增任何一条录像，泄漏都会被它抓住。
    """
    offenders: list[str] = []
    for path in _golden_files():
        for record in read_journal(path):
            if record.kind != "query":
                continue
            query = record.query
            is_dodge_window = query.kind is QueryKind.DODGE or (
                query.kind is QueryKind.RESPOND
                and query.required == DODGE_RESPONSE_CARD
            )
            if not is_dodge_window:
                continue
            for action in query.legal_actions:
                if not isinstance(action, (UseCard, PlayCard)):
                    continue
                if action.card.type != DODGE_RESPONSE_CARD:
                    offenders.append(
                        f"{path.stem}:seq={query.seq}:{action.kind}"
                        f"({action.card.name}:{action.card.type})"
                    )

    assert not offenders, (
        "金录像的【闪】窗口里出现非【闪】候选（B1 回归）：" + "、".join(offenders)
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

M5_BASELINES = tuple(scenarios.M5_SCENARIOS)
"""M5 的 8 条**构造局面**基准（M5-09a / spec §6 A3）：覆盖 11 个新牌名与 10 个覆盖项。"""

M6_BASELINES = tuple(scenarios.M6_SCENARIOS)
"""M6 的 5 条**界局构造**基准（票 20）：覆盖 spec §投影与记录流 的新原语。"""

PVE_BASELINES = tuple(scenarios.PVE_SCENARIOS)
"""M7 票 12 的 6 条 **PvE 基准**：四类逐场终局各一条 + 队友局（2v3）+ 保护队友阵亡（2v2）。"""

M2_BASELINES = tuple(
    name
    for name in distribution.SLUG_CLASSES
    if name not in set(FROZEN_BASELINES)
    and name not in set(M3_BASELINES)
    and name not in set(SCRIPTED_BASELINES)
    and name not in set(M5_BASELINES)
    and name not in set(M6_BASELINES)
    and name not in set(PVE_BASELINES)
)
"""M2 的 27 条基准：分布表里除 M1 六条、M3 八条、脚本专项、M5 八条、M6 五条与 PvE 六条
以外的全部 slug（票 32 §1）。

W5-02b：``33-borrowed-sword-use-slash`` 随借刀入口的翻转移入脚本专项（随机驱动产不出借刀
动作），故 M2 由 28 条变为 27 条。W7-01 的 ``47-halberd-borrowed-sword`` 同属脚本专项
（新增），M2 仍是 27 条。M7 票 12 的 6 条 PvE 基准**不挤占** M2 的 27 条（各自的表）。
"""

PVE_OUTCOME_CLASSES: dict[str, tuple[PveVictoryKind, str, str]] = {
    "61-pve-rout-win": (PveVictoryKind.ROUT, "ally", "enemies_routed"),
    "62-pve-survive-rounds-win": (
        PveVictoryKind.SURVIVE_ROUNDS,
        "ally",
        "rounds_survived",
    ),
    "63-pve-rout-within-rounds-win": (
        PveVictoryKind.ROUT_WITHIN_ROUNDS,
        "ally",
        "enemies_routed_in_time",
    ),
    "64-pve-slay-boss-win": (PveVictoryKind.SLAY_BOSS, "ally", "boss_slain"),
    "65-pve-ally-team-rout": (PveVictoryKind.ROUT, "ally", "enemies_routed"),
    "66-pve-protected-ally-death": (PveVictoryKind.ROUT, "enemy", "protected_ally_dead"),
}
"""**故意写死、不从 header 派生**：每条 PvE 基准的（逐场条件 / 获胜侧 / 胜因）。

写死 = 一次显式决定：改这六个数意味着改票 01 §4 的覆盖表。四类条件各有一条
（:data:`PveVictoryKind` 四个值全到齐）、两侧都有（``66`` 是败局：受保护队友阵亡，
主角还活着）。"""


def _replay_baseline(slug: str):
    """按 header 重开一局并回放磁带；返回 ``(engine, header)``。

    与 ``test_golden_replay_matches_byte_for_byte`` 走**同一条**开局入口
    （``scenarios.engine_for`` + header 里的 ``rule_config``），只是不在这里再比一次字节
    （字节比对已由参数化的那条用例逐条做过）。
    """
    records = read_journal(REPLAYS_DIR / f"{slug}.jsonl")
    header = records[0]
    pack = load_content_pack(header.pack_id, deck_profile=header.deck_profile)
    seed, tape = tape_from_journal(records)
    engine = scenarios.engine_for(
        scenarios.SCENARIOS[slug], seed=seed, pack=pack, rule_config=header.rule_config
    )
    engine.run([TapePlayer(tape)] * header.rule_config.player_count)
    return engine, header


def test_every_pve_baseline_has_an_after_the_fact_goal() -> None:
    """6 条 PvE 基准各有一条事后判据（场景表 / 判据表 / 冻结表三方相等）。"""
    assert set(PVE_BASELINES) == set(scenarios.PVE_SCENARIOS) == set(goals.PVE_GOALS)
    assert set(PVE_OUTCOME_CLASSES) == set(PVE_BASELINES), (
        "冻结的终局表必须逐条覆盖 6 条 PvE 基准（新增第七条时先改票 12，再改这里）"
    )


def test_every_pve_baseline_carries_its_pve_section_in_the_header() -> None:
    """票 12 备注：``rule_config`` 里必须**含 ``pve``**，否则它只是碰巧带敌人的身份局。

    同时钉住"header 自证"：``pve.seats`` 的体力上限 / 阵营与重开出来的座位一致
    （座位数 ≤ 5 是票 01 §7 的硬上限）。
    """
    for slug in PVE_BASELINES:
        engine, header = _replay_baseline(slug)
        config = header.rule_config
        pve = config.pve
        assert config.mode is Mode.PVE, f"{slug}：header 的 mode 必须是 pve"
        assert pve is not None, f"{slug}：header 里没有 pve 一节"
        assert len(pve.identities) == config.player_count == len(pve.seats), (
            f"{slug}：identities / seats / player_count 三方同长"
        )
        assert config.player_count <= 5, f"{slug}：票 01 §7 的座位上限是 5"
        assert {Identity.ALLY, Identity.ENEMY} <= set(pve.identities), (
            f"{slug}：PvE 只许 ally / enemy 两档身份"
        )
        assert [
            (player.seat, player.hp_max, player.identity) for player in engine.state.players
        ] == [
            (index, seat.hp_max + seat.hp_max_bonus, identity)
            for index, (seat, identity) in enumerate(zip(pve.seats, pve.identities, strict=True))
        ], f"{slug}：header 的座位表必须与重开出来的座位逐座相等（体力上限 / 阵营）"


def test_every_pve_baseline_ends_where_the_pure_solver_says() -> None:
    """票 12 的验收：**每条** PvE 基准的终局 = 拿终局存活集合与轮次重算的 ``solve_pve_outcome``。

    判据不是"跑完了就算"：终局记录（``side`` / ``winners`` / ``reason``）必须与纯函数
    逐字段相等，且 ``game_end`` 是记录流最后一条（PvE 不另造平局、不写第二个轮次计数源）。
    """
    for slug in PVE_BASELINES:
        engine, header = _replay_baseline(slug)
        pve = header.rule_config.pve
        assert pve is not None
        alive = frozenset(player.seat for player in engine.state.players if player.alive)
        expected = solve_pve_outcome(
            config=pve, alive=alive, round_no=engine.state.round
        )
        assert expected is not None, f"{slug}：终局存活集合重算不出胜负"
        tail = engine.records[-1]
        assert engine.finished, f"{slug}：局必须真的收掉"
        assert tail.kind == "game_end", f"{slug}：终局记录必须是最后一条，实得 {tail.kind}"
        assert (tail.side, tail.winners, tail.reason) == (
            expected.side,
            expected.winners,
            expected.reason,
        ), f"{slug}：宣布的终局与 solve_pve_outcome 不一致"
        assert engine.state.round <= 5, f"{slug}：票 01 §7 的短局上限是 5 轮"
        assert goals.goal_for(slug)(engine), (
            f"{slug}：回放后的记录流没有命中它在 goals.py 里声称的那件事"
        )


def test_every_pve_baseline_keeps_its_declared_outcome_class() -> None:
    """四类逐场条件各一条、两侧都有：冻结表 = 每条基准真的打出来的终局类。"""
    assert {kind for kind, _, _ in PVE_OUTCOME_CLASSES.values()} == set(PveVictoryKind), (
        "票 01 §4 的四类逐场条件（ROUT / SURVIVE_ROUNDS / ROUT_WITHIN_ROUNDS / "
        "SLAY_BOSS）必须各有一条基准"
    )
    assert {side for _, side, _ in PVE_OUTCOME_CLASSES.values()} == {"ally", "enemy"}, (
        "胜与败两侧都要出现（保护队友阵亡那条是败局）"
    )

    for slug, (kind, side, reason) in PVE_OUTCOME_CLASSES.items():
        engine, header = _replay_baseline(slug)
        pve = header.rule_config.pve
        assert pve is not None
        assert pve.victory.kind is kind, f"{slug}：header 的逐场条件不是 {kind.value}"
        tail = engine.records[-1]
        assert (tail.side, tail.reason) == (side, reason), (
            f"{slug}：终局不是冻结表写的（{side} / {reason}）"
        )


def test_the_ally_game_baseline_has_two_allies_and_the_teammate_acts() -> None:
    """队友局（2v3）：两名友方座位里有一名 AI 队友，它**真的行动过**且算在胜方里。"""
    slug = "65-pve-ally-team-rout"
    engine, header = _replay_baseline(slug)
    pve = header.rule_config.pve
    assert pve is not None
    allies = pve.seats_of(Identity.ALLY)
    enemies = pve.seats_of(Identity.ENEMY)
    assert (len(allies), len(enemies)) == (2, 3), "队友局是 2 友 × 3 敌"
    teammate = next(seat for seat in allies if seat != pve.principal)

    turns = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    ]
    assert pve.principal in turns and teammate in turns, (
        f"主角与队友都要真的行动过（回合序：{turns}）"
    )
    assert any(
        record.kind == "query"
        and record.query.actor == teammate
        and record.query.kind == QueryKind.PLAY_PHASE
        for record in engine.records
    ), "队友的出牌阶段必须真的被问过（不是只坐在座位上）"
    assert engine.records[-1].winners == allies, "胜方含 AI 队友座位（不是只有主角）"


def test_the_protected_ally_baseline_loses_although_the_principal_lives() -> None:
    """保护队友阵亡：主角还活着，这一场仍然判败（``protected_ally_dead``）。"""
    slug = "66-pve-protected-ally-death"
    engine, header = _replay_baseline(slug)
    pve = header.rule_config.pve
    assert pve is not None
    assert pve.protected_ally is not None, "这条基准必须有受保护队友"
    assert pve.protected_ally != pve.principal, "受保护队友不是主角自己"
    tail = engine.records[-1]
    assert (tail.side, tail.reason) == ("enemy", "protected_ally_dead")
    assert engine.state.player(pve.principal).alive is True, "主角必须还活着（败因不是主角阵亡）"
    assert engine.state.player(pve.protected_ally).alive is False, "受保护队友真的死了"


def test_the_baseline_distribution_meets_every_category_minimum() -> None:
    """每类达标：十一类必需表逐类计数 ≥ 最少条数（票 17 §1 的机械载体）。"""
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
    assert counts[distribution.M5_CLASS] == len(M5_BASELINES), "M5 军争机制类必须恰 8 条"
    assert counts[distribution.M6_CLASS] == len(M6_BASELINES), "M6 界局机制类必须恰 5 条"
    assert counts[distribution.PVE_CLASS] == len(PVE_BASELINES), "PvE 类必须恰 6 条"


def test_every_baseline_is_classified_and_the_registry_has_no_orphan() -> None:
    """无未分类基准：文件集合 == 类表键集 == 场景表键集（三个集合互相等于）。"""
    files = {path.stem for path in _golden_files()}

    assert files == set(distribution.SLUG_CLASSES), (
        "每个基准文件都必须在 distribution.SLUG_CLASSES 里登记；反之不得有孤儿登记"
    )
    assert files == set(scenarios.SCENARIOS), "场景表与基准文件集合互相等于"
    assert set(M2_BASELINES) == files - set(FROZEN_BASELINES) - set(M3_BASELINES) - set(
        SCRIPTED_BASELINES
    ) - set(M5_BASELINES) - set(M6_BASELINES) - set(PVE_BASELINES), (
        "M2 恰为 66 − 6 − 8 − 6 − 8 − 5 − 6 = 27 条"
    )
    assert set(M5_BASELINES) == set(scenarios.M5_SCENARIOS) == set(goals.M5_GOALS), (
        "M5 的 8 条构造基准必须与场景表、判据表三方相等"
    )
    assert set(M3_BASELINES) == set(scenarios.M3_SCENARIOS) == set(goals.M3_GOALS), (
        "M3 的 8 条专项基准必须与场景表、判据表三方相等"
    )
    assert set(SCRIPTED_BASELINES) == set(scenarios.SCRIPTED_SCENARIOS), (
        "票 17 起的脚本专项基准必须与场景表相等"
    )
    assert set(M6_BASELINES) == set(scenarios.M6_SCENARIOS) == set(goals.M6_GOALS), (
        "M6 的 5 条界局构造基准必须与场景表、判据表三方相等"
    )
    assert set(PVE_BASELINES) == set(scenarios.PVE_SCENARIOS) == set(goals.PVE_GOALS), (
        "M7 的 6 条 PvE 基准必须与场景表、判据表三方相等"
    )


def test_every_m2_baseline_has_an_after_the_fact_goal() -> None:
    """27 条 M2 基准都必须有事后判据（goals.py 的登记不得漏）。"""
    assert set(M2_BASELINES) == set(goals.M2_GOALS), (
        "M2 基准集合与 goals.M2_GOALS 的键集必须相等"
    )


def test_every_scripted_baseline_has_an_after_the_fact_goal() -> None:
    """脚本专项基准也必须独立复读一遍记录流（票 17）：脚本宣布命中 ≠ 记录流里有那回事。"""
    assert set(SCRIPTED_BASELINES) == set(goals.M1_GOALS), (
        "脚本专项基准集合与 goals.M1_GOALS 的键集必须相等"
    )


def test_every_m5_baseline_has_an_after_the_fact_goal() -> None:
    """M5 的 8 条构造基准各有一条事后判据，且判据覆盖 A3 的分派表。"""
    assert set(M5_BASELINES) == set(goals.M5_GOALS), (
        "M5 构造基准集合与 goals.M5_GOALS 的键集必须相等"
    )
    assert {scenarios.SCENARIOS[slug].deck_profile for slug in M5_BASELINES} == {
        "standard_ex_160"
    }, "M5 的每条基准都必须是 standard_ex_160 档"


def test_every_m6_baseline_has_an_after_the_fact_goal() -> None:
    """M6 的 5 条界局构造基准各有事后判据，且判据覆盖 spec §投影与记录流 的新原语。"""
    assert set(M6_BASELINES) == set(goals.M6_GOALS), (
        "M6 构造基准集合与 goals.M6_GOALS 的键集必须相等"
    )
    required = {
        "skill_suppress",
        "hp_max_change",
        "skill_gain",
        "tag_add",
        "tag_remove",
        "turn_change",
    }
    covered: set[str] = set()
    zones: set[Zone] = set()
    for slug in M6_BASELINES:
        for record in read_journal(REPLAYS_DIR / f"{slug}.jsonl"):
            if record.kind != "delta":
                continue
            covered.add(record.delta_type)
            if record.delta_type == "card_move":
                zones.update((record.from_zone.zone, record.to_zone.zone))
    assert required <= covered, f"M6 金录像没有覆盖全部新原语：缺 {sorted(required - covered)}"
    assert Zone.HERO in zones, "M6 金录像必须有一条 HERO 区域的移动（武将牌上的牌）"


def test_the_preactivation_cancel_equals_the_not_activating_path() -> None:
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


def test_the_halberd_borrowed_baseline_records_a_three_target_slash() -> None:
    """W7-01 的脚本专项 47：回放后的记录流里，借刀窗口真的给出 Bn + 2 个额外目标。

    与 ``goals.M1_GOALS`` 的判据同源（回放后独立复读一次），这里再把**形状**写成人可读的
    断言：窗口候选恒含 Bn；结算的【杀】``designations`` 为空、Bn 与额外目标都在 ``targets``；
    外层借刀仍是 ``targets=(A,)`` + ``designations=(Bn,)``。
    """
    slug = "47-halberd-borrowed-sword"
    records = read_journal(REPLAYS_DIR / f"{slug}.jsonl")
    pack = load_content_pack(records[0].pack_id, deck_profile=records[0].deck_profile)
    seed, tape = tape_from_journal(records)
    scenario = scenarios.SCENARIOS[slug]
    engine = Engine.new_game(
        seed=seed,
        rule_config=records[0].rule_config,
        pack=pack,
        heroes=scenario.heroes,
        hero_pool=scenario.hero_pool,
    )
    engine.run([TapePlayer(tape)] * records[0].rule_config.player_count)

    assert goals.goal_for(slug)(engine), (
        "47 的判据必须在**回放后**仍命中，而不是只由 --update-golden 时的脚本宣布"
    )
    window_record = next(
        record
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.BORROWED_SWORD
    )
    window = window_record.query
    offers = {
        action.targets
        for action in window.legal_actions
        if isinstance(action, UseCard) and action.card.type == "slash"
    }
    assert offers == {(0,), (0, 2), (0, 3), (0, 2, 3)}, "Bn=0 + 0..2 个其它合法目标"

    accepted = [
        record.action
        for record in engine.records
        if record.kind == "action" and record.accepted and isinstance(record.action, UseCard)
    ]
    outer = next(action for action in accepted if action.card.type == "borrowed_sword")
    assert (outer.targets, outer.designations) == ((1,), (0,)), "外层借刀的主体分列不变"
    holder = next(
        record.action
        for record in engine.records
        if record.kind == "action"
        and record.seq > window_record.seq
        and record.accepted
        and isinstance(record.action, UseCard)
        and record.action.card.type == "slash"
    )
    assert holder.targets == (0, 2, 3), "Bn 与两个额外目标都在 targets 里"
    assert holder.designations == (), "持刀者的【杀】没有附带指定"


M1_FROZEN_TRAJECTORIES: dict[str, tuple[int, int, str]] = {
    "01-slash-dodge-cancel": (
        101,
        77,
        "a42aa191b3e74ac8be9be0f4e88d159e6a22a3b90672fe43aca6586687006e86",
    ),
    "02-slash-hit-damage": (
        4,
        78,
        "e798214e3887658eed1c950a4814c0411146459f557dc4b585c98a866a68e8a2",
    ),
    "03-near-death-self-peach": (
        28,
        113,
        "b020c476cda9a42c69d761519c29dc77d6ca843812b5375b8acf3d2949b096b8",
    ),
    "04-near-death-other-peach": (
        9,
        78,
        "9414bb5e81c0d66964144bf3587c3b6ff89636a442f86818bb0047d2ea54b297",
    ),
    "05-near-death-death": (
        1,
        39,
        "e690d5cf915107abd277042d02bb6b7245cf71e2bb1210f656baf2ba2cd2037c",
    ),
    "06-deck-reshuffle": (
        2026,
        84,
        "270aa4a47892799bff31966f2b5da8dd03fa2d4dd03430b863e18d6083847ac1",
    ),
}
"""M1 六条基准的**决策轨迹**摘要 ``(seed, len(tape), sha256(tape))``（票 16 §3）。

**yijiang-2011-xusheng-chunhua 票 93 重算过一次**：摘要把 ``answers_seq`` 也算进动作序列，
而该票把「扣减体力后」挪到濒死之前、记录 Seq 因此整体重排（用户 2026-11-20 签的重录）。
动作序列本身**逐条相同**（去掉 ``answers_seq`` 后比过），故"升版不得改语义"这条仍然成立——
它管的是决策，不是记录坐标。

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


STANDARD_108_REPLAY_BYTES: dict[str, tuple[int, str]] = {
    "01-slash-dodge-cancel": (579763, "60141e822957b8baf8c917b60dcb029d3c51afcec818c8ea97254cfa1b434c22"),
    "02-slash-hit-damage": (593949, "1b047f5983e226f715513df713c6d5326058a00c413828708ea7083f2ad9e9eb"),
    "03-near-death-self-peach": (746189, "3068ef7cd9e3224729b83574c41c15a9ab879a5097331a8a0b5640cb15f315e6"),
    "04-near-death-other-peach": (559372, "9e22a87333be5a5533f0edfa04c54c75dab3774b5bc8a4f9c7172c08045b0620"),
    "05-near-death-death": (237314, "4a382dea6a41a8c0789cbca5d4cad9ae2a97012d7ba4e5dd366e46d940b6946f"),
    "06-deck-reshuffle": (616118, "d5da7ae46b1013c32a1ab552a63a0110a7c1d92b73f7f2e6ddf5b6eccc1c4412"),
    "07-identity-4p-rebel-win": (416164, "d268c872072cd78a2a220ffd0bc0e6b720c28931b41eb357925111239138e64a"),
    "08-identity-5p-lord-win": (1089595, "9433b741e8ee29d16f4091e5d351f77575627d47cce0120d0688c452a7111a7b"),
    "09-identity-6p-renegade-win": (2936170, "2dc9c417f01ed5b97e7f7a114bfb27ba98311534a6e986ad5ab772dc69461255"),
    "10-identity-7p-lord-win": (3334202, "6d7c503bdacc6a63e31e013d721b2325156f2bbf7773be4da126defa4cbace28"),
    "11-identity-8p-rebel-win": (1883495, "4e433b5385742385494cd1fe49af0a0428088c67e7f8758fe43b7a518ae85765"),
    "12-indulgence-skips-play": (1217851, "c0399bef899f97715a23686eb83a0860646c9c4f0c2ccc14a963ba2839033dd4"),
    "13-indulgence-heart-misses": (2371262, "2aa79720dac62e9bc07f41a5ce8c13fd01f18a4343dfcfac361ba5375b10ff4e"),
    "14-lightning-thunder-damage": (531051, "d01a169539c19219c37319056133b3fc29f8ed829d8703bf458f6c728ffb1193"),
    "15-lightning-defers-next-seat": (576188, "cf655f33839a2a1909d62b606722140e32dd2a65f5ff8685c3fcaaa91b9589aa"),
    "16-nullify-cancels-a-trick": (1701335, "52390418de497d804007480add8591140dfc99d19701d49fadf1b41d258908d4"),
    "17-nullify-nested-two": (724323, "29028e630c51960bf0a4b5109a809657cdf8eb7c2157b76d24a9522aafc8e617"),
    "18-nullify-nested-three": (2087830, "414d7999de1ea0a212b8f78be89fe9be3ea7f988f5f1aa22f192036707212b39"),
    "19-nullify-on-multi-target": (708392, "7f3e4296cff293d9838bdabdfd9ff104fb028924ffc8214e629c21aad4c84356"),
    "20-nullify-lightning-defers": (780263, "30d0c5065f190b29c7fee7aea92e819952bdd0641b30eb62eb87ae6813f3b2b8"),
    "21-rebel-killed-reward": (1178795, "29cd0ee5cd3159fd92fef49a32565abd555c596b0b7a268b6ff06bee25add95d"),
    "22-lord-kills-loyalist": (1589118, "39f7390903866442d02dbff196bc3f04748805df302fb201d7e411c4e337085e"),
    "23-two-deaths-revealed": (1483504, "868f43403ec0dabcfe35835e4df4d01215915c03b79043351335d8b5400120a7"),
    "24-multi-player-peach-window": (1042474, "d6021cb333f18bf573cb8e56b18ac64a0c4e37de6effddd6827c74f858ca7120"),
    "25-rollback-same-start": (305970, "ff54254c3e0df445d807fb4791bddaf19cfa740e4ce4c443375ebb38ce9c374e"),
    "26-rollback-start-advances": (750646, "ca54c5ed53b28a9bac041050d9219597815623d91d283fc21c911b5ccb691590"),
    "27-slash-beyond-adjacent": (805281, "deaccaf112365904f34f768b06b938a5d918c50ecca930c8b2cc2c57ed547e99"),
    "28-equipment-replacement": (687007, "cc4e97653d1384af2c38f55c78da894aafc40a710580c657dd0801e834fbbb71"),
    "29-serpent-spear-virtual-slash": (361266, "c9c20659a4b8f77f147f6bf905f570e9a67ee7f78b0f0b5052f31bfb08971e69"),
    "30-halberd-three-targets": (3324778, "472508691dd4d3a39b3e338a757350cffe05885d75d5d1f1915c088257a58652"),
    "31-play-card-response": (2651576, "6852cb2a09af8e2b6bd6a8c8e84b7087bd6e4368adb0fa7c427582efca58e18a"),
    "32-five-grains-selection": (605348, "2dcdbb4ae59255a4de72c17bba9474eac4696d5a8d87e7d710d96f831fb02ef5"),
    "33-borrowed-sword-use-slash": (1149409, "0f10cf725d9d0c899f1385698037d8dea67c7fd0da750e9e58c94a6a4c8869ac"),
    "34-dismantle-or-snatch-equip-judge": (378977, "d4fb456c3d9d4be2e605b84e033301a5aa5ef54f6f2d00213992cfcb6665047f"),
    "35-hero-damage-points": (530858, "cc146250c2cbf32cb837a59f8d42a4c338551e0d3c51fe75f567601538f0a62f"),
    "36-hero-rejudge-cycle": (441726, "fd81696b013ed0fbae9cd7c95d17a5218e4de4c218f20f0da1f58dcab3d0a216"),
    "37-hero-lord-request": (625226, "103cfbd4e8cd1d431bd587670e9cf42db40b37b15cae574e8d71262ca3d0f10a"),
    "38-hero-lost-cards": (863467, "103a19f1685116a1ba76399c21eeaed7c289a8d99bbbaaadcda28e5234809406"),
    "39-hero-target-pool": (2600917, "be7009c2284cddfde2fa01b465d273a12135fbeae296db02a85726627db11f8d"),
    "40-hero-wushuang": (361421, "2bd60b9c3f018063f2ea20b12cd765178d6d41ab4a582de5df0bac6b246ca43b"),
    "41-hero-guanxing": (818627, "ba2b1abb56953374e921c8795a276cda07daaabb2da08ac87fb74222480b5246"),
    "42-hero-transform": (835248, "ac52b138412959c41fadc7080c65f8dc7e92dade0449b64d6018f47ec46da7b9"),
    "43-double-sword-opposite-gender": (392910, "87a1d1228cecd1d5c66761d00efca9907e16b8880cee858e84faf62086df6e9d"),
    "44-preactivation-cancel-no-side-effect": (193712, "52b83b1b01c34d499c762fac62f04e13a1b66bfcf09c58862a9adac1d3c9d9f4"),
    "45-borrowed-sword-auto-handover": (485144, "ddcb3c7d8ef2f770b2cd3cef7bb02e84c9c4449e2443cfae0dde02e0f7e97bdd"),
    "46-borrowed-sword-transform-slash": (1146558, "00bc56d369e0fffb393f0cf60bbc79720c5f913aa1759f7c9b57827c0b4866a9"),
    "47-halberd-borrowed-sword": (850838, "5912ddd668abe8caf1e7f4932e728cea9bf282de6efd7aa28c3cea891517aae9"),
}
"""标准 108 的 **47 条金录像**逐文件 ``(字节数, sha256)``（spec D14 / M5-09a）。

**故意写死**：M5 只许**新增**军争基准，标准 108 的 47 条必须逐字节不变。这张表把
"不变"钉成门禁判据——任何重录、格式化或手改都会让对应条目变红；改它 = 一次显式决定
（本 effort 的 D14 明确禁止，**例外是下面逐次登记、经用户批准的几次重基线**，其判据与
覆盖都在下面写明）。``M1_FROZEN_TRAJECTORIES`` 钉的是六条的**决策轨迹**，
这张表钉的是 47 条的**文件字节**，两者互补。

**唯一一次显式重基线（2026-11-03，票 06，用户批准）**：``38-hero-lost-cards`` 的字节由
``(857328, 3ed0138…5ec251)`` 改为 ``(857045, 8e7dad3…524a9)``。原因是修「使用装备牌」的顶替
路径：「失去装备后」的钩子（本盘是【枭姬】）让出之后，新装备的 ``PUT`` 必须等它结算完才
入槽；旧录像恰好录下了错的次序（新装备先入槽、再问【枭姬】），且旧实现吞掉 ``"stay"`` 时
多记了一条 ``MOVE_AFTER``。重录**只**重跑这一条（``scenarios.record("38-hero-lost-cards")``），
其余 46 条一字未动，判据不放宽；38 在 ``goals.py`` 里的判据（D 族失去牌后）重录后仍命中。
**重录 = 放弃一条覆盖**（`CODING_STANDARDS.md` §5）：旧录像原先钉住的那条**次序**覆盖由两条
直接测试接手——`tests/test_m5_equipment.py::test_replacing_silver_lion_recovers_before_the_new_equipment_enters`
（顶替白银狮子：旧装备离场 → 回复 → 新装备入槽，逐条比记录流序列）与
`tests/test_hero_move_after.py::test_xiaoji_fires_on_replacement_even_though_the_equipment_is_not_discarded`
（顶替算失去装备、但不算弃置 ⇒【枭姬】恰好触发一次，且新装备的 ``PUT`` 排在【枭姬】之后）。

**第二次显式重基线（2026-11-11，table-ux-overhaul 票 04，用户已签 spec §3 注① / §4）**：
「需要（使用 / 打出）牌」的窗口改**按档逐档问**（武将档 → 装备档 → 牌档）并修
``providers_used`` 按事件复位后，同一时机的候选面与询问条数**有意改变**，故
``09 / 17 / 20 / 30 / 31 / 34 / 35 / 36 / 37`` 九条逐字节不同；其余 38 条一字未动。
按本文件所在模块的纪律：这九条的 ``goals.py`` 判据**一条未改、一条未放宽**，其中
``30``（单文件涨过 4 MiB 绊线）与 ``17 / 34 / 35 / 36 / 37``（短局换长局）按
``scenarios.py`` 记录的纪律**重新选种**，``29 / 42`` 等未受影响的基准不动。重录清单与
"哪条基准 / 变了什么 / 为什么"写在票面 ``## Answer`` 与 ``tests/replays/README.md``
的「重录登记」一节。
**第三次显式重基线（2026-11-13，sp5-pangtong-liuqi 报障修复，用户已签）**：
「摸牌」对 `MOVE_AFTER` 可见——**摸牌阶段的摸牌**与**反贼奖惩摸牌**改走移动入口
（原先用 `move_card` 旁路），否则「获得牌 / 摸牌后」类技能（【清俭】【展骥】）在这些路径上
静默不触发。记录流因此**只多出** `MOVE_AFTER` 时机记录（每次摸牌一条），**query / action /
delta / frame 一条不变**（逐条核对过：35 +8、36 +11、38 +16、39 +36、40 +4、41 +7、43 +12、
50 +3、58 +1）。七条在冻结表里的（`35 / 36 / 38 / 39 / 40 / 41 / 43`）按新字节重基线；
`50 / 58` 不在本表、由逐文件比对覆盖。`goals.py` 判据**一字未改、一条未放宽**；其余 40 条
标准 108 基准逐字节不变（惰性守卫：没有 MOVE_AFTER 提供者时一条记录都不落）。重录清单见
`tests/replays/README.md` 的「重录登记：摸牌对 MOVE_AFTER 可见」一节。

**第四次显式重基线（2026-11-17，yijiang-2014-round2 票 25，用户批准）**：青釭剑的
「无视防具」改成"三条伤害链 + 帧标签"后，【闪】窗口的**候选面**在"青釭剑杀八卦阵"的
局面上**有意改变**（`ActivateSkill(equip.eight_diagrams)` 不再进 `legal_actions`）。
`13-indulgence-heart-misses` 恰好录到这一手（座位 3 装青釭剑、座位 2 装八卦阵），旧字节
`(2230634, bd4fedb…)` → `(2371103, 1e45de5…)`，只重录这一条；其余 46 条一字未动。
票 25 原先写的「标签不进记录流 ⇒ 47 条逐字节不变」只对**标签本身**成立，对**候选面**
不成立——旧录像录下的正是缺陷行为。`goals.py` 判据**一字未改、一条未放宽**（回放后
【乐不思蜀】未命中判据仍命中）。重录清单见 `tests/replays/README.md` 的
「重录登记：青釭剑封住【八卦阵】提供者」一节。

**票 01 §2 授权的唯一一次 re-freeze（2026-11-19，M7 票 07）**：``rule_config`` 的 dump 从此含
``mode`` 与 ``pve`` 两个键，于是**全部 60 条**基准的**首行**都变了——47 条冻结表里的逐条
``(字节数, sha256)`` 因此一次重算：23 条无身份局各涨 32 字节
（``"mode":"no_identity",`` 21 + ``"pve":null,`` 11）、37 条身份局各涨 29 字节
（``"mode":"identity",`` 18 + 同 11），合计 +1,809 字节。这不是"某条行为变了"：逐文件核过
**除首行外的每一行都与改前逐字节相同**（``git show HEAD:<file> | tail -n +2`` 的 sha256
与现文件同一段相等），``goals.py`` 的判据一条未改、一条未放宽，种子一个也没重选。
M5 的 8 条与 M6 的 5 条同一批重录（它们不在 47 条冻结表里，由逐文件比对覆盖）；
60 条合计 50,791,313 字节 ≈ 48.44 MiB（比改前多 1,809 字节），仍在 52 MiB 总预算与
4 MiB 单文件上限之内，故**不动预算**。这就是票 01 §2 说的"唯一一次 re-freeze"：
``mode`` 只进 header 一次，票 09 / 10 往 ``pve`` 里填语义时标准局仍是 ``pve: null``。
重录清单见 `tests/replays/README.md` 的「重录登记：header v3（mode / pve）」一节。
"""


def test_the_47_standard_108_replays_keep_their_frozen_bytes() -> None:
    """D14：标准 108 的 47 条金录像逐字节不变（增删 / 重录旧基准即红）。"""
    files = {path.stem: path for path in _golden_files()}
    assert len(STANDARD_108_REPLAY_BYTES) == 47, "标准基准恰 47 条"
    assert set(STANDARD_108_REPLAY_BYTES).isdisjoint(M5_BASELINES), (
        "M5 的 8 条是新基准，不得混进标准 108 的冻结表"
    )

    missing = set(STANDARD_108_REPLAY_BYTES) - set(files)
    assert not missing, f"标准 108 基准文件缺失：{sorted(missing)}"

    mismatches: list[str] = []
    for slug, (size, digest) in STANDARD_108_REPLAY_BYTES.items():
        data = files[slug].read_bytes()
        actual = (len(data), hashlib.sha256(data).hexdigest())
        if actual != (size, digest):
            mismatches.append(f"{slug}: 期望 {(size, digest)}，实得 {actual}")
    assert not mismatches, (
        "标准 108 金录像的字节变了（D14：本 effort 不许重录旧基准；先比对 git 基线 "
        "dd029e5）：\n" + "\n".join(mismatches)
    )
