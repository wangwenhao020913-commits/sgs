"""PvE **逐座位合成武将**与三档数字常驻加成（M7 票 10；票 03 §3 / §10.1–§10.2）。

敌人不是武将表里的武将，而是「模板 + 抽到的技能 + 属性加成」⇒ 每座是谁、带多少加成
必须进 header（否则录像重建不出同一场），也必须在**引擎里真的生效**：

1. **进局**：``PveConfig.seats`` 逐座给合成武将（``hero=None`` + ``name`` = 模板名）；
   有武将的座位照旧拿武将牌技能，合成武将**没有**武将牌技能。
2. **三个座位常驻钩子**：摸牌数 / 造成伤害 +N / 体力上限 +N。常驻项**不落 delta**、
   查询期从座位字段算（同 M6 的连续型效果纪律），并且与遗物**共用同一条修正链**
   （座位项是链上的一项，不是第二套加成读取）。
3. **header 自证**：带 2 座合成武将的 PvE 局重建出同一条记录流，``seats`` 全在首行。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from sgs.data import (
    Gender,
    Identity,
    Kingdom,
    Mode,
    PveConfig,
    PveSeat,
    PveVictory,
    PveVictoryKind,
    RuleConfig,
    load_content_pack,
)
from sgs.effects import Modifier, ModifierPoint, damage_amount
from sgs.engine import DRAW_COUNT, Engine, dump_journal, read_journal
from sgs.state import AskPolicy, Phase, UseCard
from sgs.skills import resolve_instances
from sgs.skills.catalog import SkillDef

from support import PassPlayer, SeatSpec, build_state, cards_of_type, deck_excluding, run_turns

PACK = load_content_pack(deck_profile="standard_108")
ALLY = Identity.ALLY
ENEMY = Identity.ENEMY
SLASH = cards_of_type(PACK, "slash")[0]
"""一张实体【杀】——"造成伤害 +N"必须在**伤害链**上生效，不是只在直伤上。"""


def _seat(*, hero=None, name="", hp=4, hp_max=4, **bonuses) -> PveSeat:
    """一名座位（缺省 = 满体力合成武将）；``hp`` / ``hp_max`` 分开传（"3/4"合法）。"""
    return PveSeat(hero=hero, name=name, hp=hp, hp_max=hp_max, **bonuses)


def _rule(*seats: PveSeat, principal: int = 0, first_seat: int = 0) -> RuleConfig:
    """一局 PvE 的规则配置：座位 0 = 主角（友），其余都是敌人。"""
    identities = (ALLY, *(ENEMY for _ in seats[1:]))
    return RuleConfig(
        player_count=len(seats),
        mode=Mode.PVE,
        first_seat=first_seat,
        pve=PveConfig(
            identities=identities,
            principal=principal,
            victory=PveVictory(kind=PveVictoryKind.ROUT),
            seats=seats,
        ),
    )


# ---------------------------------------------------------------------------
# 进局：逐座位合成武将（build_initial 的 PvE 入口）
# ---------------------------------------------------------------------------


def test_a_composite_seat_opens_from_its_own_numbers_and_carries_no_hero_card():
    """座位表直接写进开局状态：体力 / 上限 / 势力 / 性别来自座位，**武将牌技能为 0**。

    这是"敌人不是武将表记录"的引擎侧兑现：``hero=None`` ⇒ 没有武将牌技能（技能全部来自
    ``grants``）；``hp_max_bonus`` 在开局就算进有效上限。
    """
    rule = _rule(
        _seat(hero="caocao", hp=3, hp_max=4, hp_max_bonus=1),
        _seat(name="黄巾力士", hp=2, hp_max=3, kingdom="qun", gender="male"),
    )

    engine = Engine.new_game(seed=1010, rule_config=rule, pack=PACK)

    principal = engine.state.player(0)
    assert (principal.hero, principal.hp, principal.hp_max) == ("caocao", 3, 5), (
        "有武将的座位：武将 id 照旧写进状态，体力上限 = hp_max + hp_max_bonus"
    )

    grunt = engine.state.player(1)
    assert (grunt.hero, grunt.hp, grunt.hp_max) == (None, 2, 3)
    assert (grunt.kingdom, grunt.gender) == (Kingdom.QUN, Gender.MALE), (
        "合成武将的势力 / 性别来自座位字段（只影响技能作用域）"
    )

    hero_skills = {
        instance.skill_id
        for instance in resolve_instances(engine.state)
        if instance.owner == 1
    }
    assert hero_skills == set(), "合成武将没有武将牌技能（技能全部来自 grants）"
    assert {
        instance.skill_id
        for instance in resolve_instances(engine.state)
        if instance.owner == 0
    }, "有武将的座位照旧产出武将牌技能"


def test_seats_and_the_hero_dealing_entry_point_are_mutually_exclusive():
    """两条"谁坐这个座位"的入口不许同时给：座位表在 header 里，``heroes=`` 在参数里。

    同时给会让"录像重建是否用同一批武将"取决于调用方怎么传，header 自证当场失效。
    """
    rule = _rule(_seat(name="黄巾力士"), _seat(name="黄巾力士2"))

    with pytest.raises(ValueError) as caught:
        Engine.new_game(seed=1011, rule_config=rule, pack=PACK, heroes=("caocao", "simayi"))

    assert "seats" in str(caught.value)


# ---------------------------------------------------------------------------
# 三个座位常驻钩子
# ---------------------------------------------------------------------------


def _pve_state(*seats: PveSeat, hand=(), deck=(), turn_seat=0, phase=Phase.PLAY):
    """人为 PvE 局面（座位表进 ``rule_config``，与真开局同一条读口）。"""
    specs = [
        SeatSpec(
            hp=spec.hp,
            hp_max=spec.hp_max,
            hand=tuple(hand) if seat == 0 else (),
            identity=ALLY if seat == 0 else ENEMY,
        )
        for seat, spec in enumerate(seats)
    ]
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck,
        turn_seat=turn_seat,
        phase=phase,
        rule_config=_rule(*seats),
    )


def test_the_draw_phase_draws_the_seat_bonus_on_top_of_the_rated_count():
    """摸牌阶段张数 = 额定 ``DRAW_COUNT`` + ``draw_bonus``（常驻项不落 delta）。"""
    state = _pve_state(
        _seat(name="黄巾力士", hp=8, hp_max=8, draw_bonus=2),
        _seat(name="黄巾力士2", hp=8, hp_max=8),
        deck=tuple(range(len(PACK.deck.cards))),
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1012)

    run_turns(engine, [PassPlayer(), PassPlayer()], turns=1)

    drawn = engine.state.player(0).hand
    assert len(drawn) == DRAW_COUNT + 2, f"额定 {DRAW_COUNT} + draw_bonus 2 = 4 张"
    assert len(engine.state.player(1).hand) == DRAW_COUNT, (
        "座位 1 没有 draw_bonus ⇒ 它那一回合只摸额定 2 张（加成逐座位算）"
    )


def test_a_slash_deals_the_seat_damage_bonus_through_the_damage_chain():
    """造成伤害 = 基础 + ``slash_damage_bonus``（**连【杀】的伤害链一起核**）。

    两个臂只差座位字段：没有加成时【杀】打 1 点，有 +1 时打 2 点——证明加成落在
    ``DAMAGE_DEAL_AMOUNT`` 这条链上，而不是只在某种直伤路径上。
    """
    def _victim_hp(slash_damage_bonus: int) -> tuple[int, Engine]:
        state = _pve_state(
            _seat(name="黄巾力士", slash_damage_bonus=slash_damage_bonus),
            _seat(name="黄巾力士2"),
            hand=(SLASH,),
            deck=deck_excluding(PACK, SLASH),
        )
        engine = Engine.from_state(state, seed=1013)
        run_turns(engine, [_SlashPlayer(1), PassPlayer()], turns=1)
        return engine.state.player(1).hp, engine

    plain, _ = _victim_hp(0)
    boosted, engine = _victim_hp(1)

    assert plain == 3, "无加成：【杀】1 点伤害"
    assert boosted == 2, "有 +1：【杀】打 2 点（走伤害链，不只直伤）"
    assert all(
        getattr(record, "delta_type", None) != "hp_max_change"
        for record in engine.records
    ), "常驻加成不落 delta：伤害数值只由链上现算得来"


def test_the_seat_damage_bonus_is_source_side_only_and_not_counted_on_conductions():
    """来源侧加成的既有口径：只在**源头伤害帧**算，传导帧不重算（`damage_amount` 的契约）。"""
    seats = (_seat(name="黄巾力士", slash_damage_bonus=2), _seat(name="黄巾力士2"))
    state = _pve_state(*seats, deck=deck_excluding(PACK, SLASH))
    ctx_card = state.card(SLASH)

    source_side = damage_amount(
        state, source=0, target=1, card=ctx_card, base=1, chain=False
    )
    conduction = damage_amount(
        state, source=0, target=1, card=ctx_card, base=1, chain=True
    )

    assert source_side == 3, "基础 1 + 座位常驻 2"
    assert conduction == 1, "传导帧的基数是上一层实际伤害，来源侧加成不再重算"


def test_the_hp_max_bonus_is_written_at_opening_and_never_moves():
    """体力上限 = ``hp_max`` + ``hp_max_bonus``，且**开局写定、局内不变**（不落 delta）。

    起点必须是**真开局**（``Engine.new_game``）：上限加成是 ``build_initial`` 写进座位的
    开局事实，不是回合里的修正——手搓局面（``build_state``）不经过那条入口。
    """
    rule = _rule(
        _seat(name="黄巾力士", hp=4, hp_max=4, hp_max_bonus=3),
        _seat(name="黄巾力士2"),
    )
    engine = Engine.new_game(seed=1014, rule_config=rule, pack=PACK)

    assert engine.state.player(0).hp_max == 7, "开局就写定（不是回合里再抬一次）"
    assert engine.state.player(0).hp == 4, "初始体力照座位表（不满上限开局合法）"

    run_turns(engine, [PassPlayer(), PassPlayer()], turns=2)

    assert engine.state.player(0).hp_max == 7, "局内不变"
    assert all(
        getattr(record, "delta_type", None) != "hp_max_change"
        for record in engine.records
    ), "常驻项不落 delta：局内一条 hp_max_change 都不许有"


class _SlashPlayer:
    """出牌阶段对指定座位打出一张【杀】；其余询问走最小合法应答（不读敌友）。"""

    def __init__(self, target: int) -> None:
        self.target = target
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            for candidate in query.legal_actions:
                if isinstance(candidate, UseCard) and candidate.card.type == "slash":
                    if self.target in candidate.targets:
                        return candidate
            return self._fallback.respond(query)
        return self._fallback.respond(query)


def test_an_unknown_hero_named_by_the_seat_table_is_rejected_fail_closed():
    """座位表点名的武将必须存在：静默当合成武将会让这一场"少一套武将牌技能"。"""
    rule = _rule(_seat(hero="nobody"), _seat(name="黄巾力士2"))

    with pytest.raises(ValueError) as caught:
        Engine.new_game(seed=1015, rule_config=rule, pack=PACK)

    assert "nobody" in str(caught.value)


# ---------------------------------------------------------------------------
# 公开面：座位表进 header，不进记录流、不泄露手牌
# ---------------------------------------------------------------------------# ---------------------------------------------------------------------------
# 与遗物共用同一条修正链（不是两套加成读取）
# ---------------------------------------------------------------------------


def test_the_seat_bonus_is_one_item_on_the_same_chain_the_relic_modifiers_ride(monkeypatch):
    """遗物修饰器与座位常驻项在**同一条链**上相加：1（基础）+ 1（遗物）+ 2（座位）= 4。

    遗物 = ``RELIC`` 实例（座位级清单里的一条技能），它的加伤走 ``DAMAGE_DEAL_AMOUNT``；
    座位项也是这条链上的一项。若有人另写一套"座位加成读取"，这个和就不再成立。
    """
    from sgs.effects import CarrierKind
    from sgs.skills import catalog as catalog_module

    def relic_bonus(state, ctx, current):
        return current + 1

    relic = SkillDef(
        skill_id="test.relic_damage",
        locked=True,
        carrier=CarrierKind.HERO,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, relic_bonus),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, relic.skill_id: relic}),
    )
    seats = (_seat(name="黄巾力士", slash_damage_bonus=2), _seat(name="黄巾力士2"))
    state = _pve_state(*seats, deck=deck_excluding(PACK, SLASH))
    state.player(0).relics.append("test.relic_damage")

    total = damage_amount(
        state, source=0, target=1, card=state.card(SLASH), base=1, chain=False
    )

    assert total == 4, "两条加成落在同一次链式计算里（遗物 +1、座位 +2）"


def test_the_seat_bonus_is_registered_on_the_modifier_chain_not_beside_it():
    """结构判据：座位项由 ``apply_modifiers`` 派发（与遗物实例的同一条循环），不是旁路。"""
    import inspect

    from sgs.effects import modifiers as modifiers_module

    source = inspect.getsource(modifiers_module.apply_modifiers)
    assert "resolve_instances(state)" in source, "链上原本就有遗物 / 技能实例"
    assert "seat_persistent_modifiers(point)" in source, (
        "座位常驻项必须是**同一条链上的一项**（不写两套加成读取）"
    )


def test_every_seat_bonus_field_is_read_in_exactly_one_place():
    """三档加成只有**一个读取点**（``sgs.skills.catalog.seat_persistent_bonus``）。

    这是"不许出现两套加成读取"的机械判据：任何别处直接读 ``.draw_bonus`` /
    ``.slash_damage_bonus`` / ``.hp_max_bonus`` 都会让这条红。
    """
    root = Path(__file__).resolve().parent.parent / "src" / "sgs"
    fields = {"draw_bonus", "slash_damage_bonus", "hp_max_bonus"}
    readers: dict[str, set[str]] = {field: set() for field in fields}

    for path in sorted(root.rglob("*.py")):
        module = f"sgs/{path.relative_to(root).as_posix()}"
        if module == "sgs/data/rule_config.py":
            # 数据层读自己字段的**校验器**不算第二个读口（它判的是取值区间，不是加成）。
            continue
        _scan_bonus_readers(ast.parse(path.read_text(encoding="utf-8")), module, fields, readers)

    assert readers == {
        field: {"sgs/skills/catalog.py:seat_persistent_bonus"} for field in fields
    }, f"三档加成必须只有一个读取点，实得 {readers}"


def _scan_bonus_readers(
    node: ast.AST, module: str, fields: set[str], readers: dict[str, set[str]]
) -> None:
    """把 ``node`` 之下的加成字段读取记到"所在函数"名下（本仓源码不嵌套定义这些函数）。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(child):
                if isinstance(inner, ast.Attribute) and inner.attr in fields:
                    readers[inner.attr].add(f"{module}:{child.name}")
            continue
        if isinstance(node, ast.Module) and isinstance(child, ast.ClassDef):
            # 数据模型里的**字段定义**是注解 / 赋值，不是读取；类体里也不该有读取点。
            _scan_bonus_readers(child, module, fields, readers)
            continue
        if isinstance(child, ast.Attribute) and child.attr in fields:
            for field in fields & {child.attr}:
                readers[field].add(f"{module}:<module>")
        _scan_bonus_readers(child, module, fields, readers)


def test_the_seat_table_is_public_in_the_header_and_absent_from_every_delta():
    """``seats`` / 三档加成是**公开信息**（敌人模板名与档位玩家可见），但它只在 header 里。

    公开不等于可以挤进记录流：座位表是"这一场是谁"的常量事实，玩家视图照旧只读
    ``PlayerView`` 的既有字段（票 23 才加显示位）。故这条只钉"别泄进 delta"这一侧——
    记录流里既没有座位表、也没有任何新 delta 类型。
    """
    rule = _rule(
        _seat(name="黄巾力士", hp=3, hp_max=4, hp_max_bonus=1, slash_damage_bonus=1),
        _seat(name="黄巾力士2"),
    )
    engine = Engine.new_game(seed=1016, rule_config=rule, pack=PACK)
    run_turns(engine, [PassPlayer(), PassPlayer()], turns=1)

    stream = dump_journal(engine.records)
    header_line, *tail = stream.splitlines()
    assert '"seats"' in header_line, "座位表是公开信息：它在 header 首行"
    assert all("黄巾力士" not in line for line in tail), "记录流不重复播报座位表"
    assert all("slash_damage_bonus" not in line for line in tail), (
        "三档加成不进记录流：常驻项查询期现算、不落 delta"
    )


# ---------------------------------------------------------------------------
# header 自证：``seats`` 进首行，重建出同一条记录流
# ---------------------------------------------------------------------------


def test_a_pve_game_with_two_composite_seats_rebuilds_the_same_record_stream_from_its_header(
    tmp_path,
):
    """带 2 座合成武将（含三档加成）的局：header 往返 → 重开一局 + 磁带放完 = 同一条流。

    这是票 01 §2 那条裁定（PvE 的构成必须在 header 里）对**座位表**的兑现：重建出来的局
    里体力 / 上限 / 加成都在（不是"记录流碰巧一样"）。
    """
    from sgs.agents import TapePlayer, tape_from_journal

    rule = _rule(
        _seat(hero="caocao", hp=3, hp_max=4, hp_max_bonus=1, draw_bonus=1),
        _seat(
            name="黄巾力士",
            hp=2,
            hp_max=3,
            slash_damage_bonus=2,
            kingdom="qun",
            gender="male",
        ),
    )
    engine = Engine.new_game(
        seed=20261120, rule_config=rule, pack=PACK, ask_policy=AskPolicy()
    )
    engine.run([PassPlayer(), PassPlayer()])
    expected = dump_journal(engine.records)

    path = tmp_path / "pve_seats.jsonl"
    path.write_text(expected, encoding="utf-8")
    first_line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert first_line["rule_config"]["pve"]["seats"][1]["name"] == "黄巾力士", (
        "seats 全在 header 首行（录像不必读别处就能重建同一场）"
    )
    assert first_line["rule_config"]["pve"]["seats"][1]["slash_damage_bonus"] == 2

    records = read_journal(path)
    header = records[0]
    assert header.rule_config.pve.seats == rule.pve.seats, (
        "座位表必须经由 header 往返（写读对称）"
    )

    seed, tape = tape_from_journal(records)
    replay = Engine.new_game(
        seed=seed,
        rule_config=header.rule_config,
        pack=load_content_pack(header.pack_id, deck_profile=header.deck_profile),
        ask_policy=AskPolicy(),
    )
    rebuilt = replay.state.player(1)
    assert (rebuilt.hero, rebuilt.hp, rebuilt.hp_max) == (None, 2, 3), (
        "重建出来的局里合成武将的体力 / 上限必须在"
    )
    replay.run([TapePlayer(tape)] * header.rule_config.player_count)

    assert dump_journal(replay.records) == expected
