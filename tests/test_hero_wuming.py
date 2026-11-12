"""自制包·无名自制版【天成】（wuming 票 01）的构造局面单测。

牌面（`D:\\SGS_HEROS\\自制包\\无名\\技能.txt`，2026-11-03 修订版，逐字）::

    【天成】(锁)：游戏开始时，依次展示3张真名不在场上的随机武将牌，且每次展示你须选择：
    1.获得该武将牌上的1个技能；2.增加一点体力上限，回复一点体力，摸X张牌（X为你选择选项2的次数）

**形状** = L0 帧外时机钩子（锁定 ⇒ 不问"是否发动"）+ 手写 3 次展示循环（`SkillFlow` 是定长
元组，没有循环步）。三条陈述面都在这里钉住：

- **时点**：起始手牌发完之后、第一回合之前，且不问"是否发动"；
- **抽取**：一次性抽 3 张、真名互异且都不在场、抽到即入屏蔽集合、整局不再抽取；
- **三次展示**：一次一张（n/3 副标题）、每张的选项闭集 = 该武将非 ☆ 技能（名字 + 描述、
  已拥有同 skill id ⇒ 置灰）+ 选项 2（**具体 X**）、没有「放弃」；
- **两项效果**：授予的技能立刻能用；选项 2 的次序 = 加体力上限（`MAXLIFE` 帧）→ 回复 1 点
  （`RECOVER` 帧）→ 摸 X 张，X = 1 / 2 / 3。

判据一律走**外部缝**：询问载荷 / 投影载荷 / 记录流（时机、帧、delta 的坐标）/ 引擎状态
（``drawn_true_names`` / ``granted_skills`` / 实例解析）。帧私有游标只在"必须证明它不漏做 /
不重做"的地方通过记录流间接读（例如三次 `hp_max_change` 的 before / after 链）。

另两条断言（`01-wuming.md` 的 02 D / E 验收，构造用例落在本票）：

- ``test_a_granted_lord_skill_works_for_a_non_lord``：**获得**的主公技在非主公身上能发动；
- ``test_a_dead_heros_true_name_is_released``：阵亡武将牌移出本局后真名解除屏蔽。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from sgs.data import (  # noqa: E402
    Gender,
    Identity,
    Kingdom,
    RuleConfig,
    enabled_hero_ids,
    hero_by_id,
    hero_card_skill_ids,
    load_content_pack,
)
from sgs.engine import Engine  # noqa: E402
from sgs.skills import (  # noqa: E402
    CATALOG,
    TIANCHENG,
    TIANCHENG_MAX_HP,
    TIANCHENG_REQUIREMENT,
    TIANCHENG_REVEALS,
    blocked_true_names,
    conversion_offers,
    resolve_instances,
)
from sgs.skills import SkillSourceKind  # noqa: E402
from sgs.state import (  # noqa: E402
    ActivateSkill,
    FrameKind,
    Pass,
    QueryKind,
    SelectCards,
    SelectOption,
    Zone,
)
from sgs.timing import Timing  # noqa: E402
from sgs_web.descriptions import SKILL_DESCRIPTIONS  # noqa: E402
from sgs_web.projection import pending_payload  # noqa: E402

from support import (  # noqa: E402
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    find_action,
    ran_timing,
    run_queries,
)

PACK = load_content_pack(deck_profile="standard_108")
WUMING = "wuming"
HUANGTIAN = "hero.zhangjiao_mobile.huangtian"
"""张角移动版的主公技【黄天】（`01-wuming.md` 的 02 D 验收用它做"获得的主公技"）。"""

_OPTION_TWO_LABEL = "增加 1 点体力上限，回复 1 点体力，摸 {x} 张牌"


# ---------------------------------------------------------------------------
# 驱动：无名坐 0 号位，脚本玩家按 `picks` 逐张应答三次展示
# ---------------------------------------------------------------------------


def _all_true_names() -> tuple[str, ...]:
    """数据面的**真名投影**（按首次出现序、去重）：屏蔽全部真名 ⇒ 抽取池为空。

    这是测试从 ``enabled_hero_ids()`` 派生的期望池，**不是**迭代 ``blocked_true_names``
    得来的（那个集合只做成员判定，迭代序逐进程变化）。
    """
    names: dict[str, None] = {}
    for hero_id in enabled_hero_ids():
        hero = hero_by_id(hero_id)
        assert hero is not None
        names.setdefault(hero.true_name, None)
    return tuple(names)


def _pin_pool(engine: Engine, keep: tuple[str, ...] = ()) -> None:
    """把抽取池**钉死**成 ``keep``（其余真名全部记进"被抽走"集合）。

    ``draw_hero_cards`` 的池 = 数据面真名 − 屏蔽集合，故"抽走其余所有真名"就是"只让这几张
    进池"。没有这个夹具，三次展示摊到哪几张武将牌上全靠种子，断言只能写成"随便哪张都行"。
    """
    keep_set = set(keep)
    for name in _all_true_names():
        if name not in keep_set:
            engine.note_drawn_true_name(name)


def _true_name(hero_id: str) -> str:
    hero = hero_by_id(hero_id)
    assert hero is not None, f"抽到了数据表里没有的武将 id：{hero_id!r}"
    return hero.true_name


class _TianchengPlayer:
    """答【天成】的三次展示：``picks[k]`` 是第 k 张的答案，其余询问走最小合法应答。

    ``picks`` 的一格可以是 ``"effect"``（选项 2）、一个技能 id 的**后缀**（如 ``".wusheng"``，
    抽到哪一版都能命中）或 ``"first"``（取该问的第一个候选）。
    """

    def __init__(self, picks: tuple[str, ...] = ()) -> None:
        self.picks = tuple(picks)
        self.queries: list = []
        self._fallback = PassPlayer()

    def respond(self, query):
        if query.kind == QueryKind.SELECT_OPTION and str(
            query.skill_id or ""
        ).startswith(TIANCHENG):
            index = len(self.queries)
            self.queries.append(query)
            pick = self.picks[index] if index < len(self.picks) else "effect"
            return self._choose(query, pick)
        return self._fallback.respond(query)

    @staticmethod
    def _choose(query, pick: str):
        if pick == "effect":
            return find_action(
                query,
                lambda action: isinstance(action, SelectOption)
                and action.choice == TIANCHENG_MAX_HP,
            )
        if pick == "first":
            return query.legal_actions[0]
        if pick.startswith("."):
            return find_action(
                query,
                lambda action: isinstance(action, SelectOption)
                and str(action.choice).endswith(pick),
            )
        return find_action(
            query, lambda action: isinstance(action, SelectOption) and action.choice == pick
        )


def _wuming_engine(*, seed: int = 7, seats: int = 2) -> Engine:
    """真开局（含起始手牌）之后、第一次 ``_step`` 之前：无名坐在 0 号位。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=seats),
        pack=PACK,
        pinned_hero=WUMING,
        pinned_seat=0,
        hero_pool=(),
    )


def _run_to_first_turn(engine: Engine, players) -> Engine:
    """跑到第一回合开始（``TURN_BEFORE_START``）即停：三次展示已经全部结算完。"""
    return run_queries(
        engine, players, lambda e: ran_timing(e, Timing.TURN_BEFORE_START)
    )


def _query_records(engine: Engine, skill_prefix: str = TIANCHENG) -> list:
    """记录流里 ``skill_id`` 以 ``skill_prefix`` 开头的询问（按记录序）。"""
    return [
        record
        for record in engine.records
        if record.kind == "query"
        and str(getattr(record.query, "skill_id", None) or "").startswith(skill_prefix)
    ]


def _timing(engine: Engine, timing: Timing, *, first: bool = True):
    matches = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is timing
    ]
    assert matches, f"记录流里没有 {timing.value}"
    return matches[0] if first else matches[-1]


def _deltas(engine: Engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _hand_draws_after(engine: Engine, seat: int, seq: int) -> list:
    """``seq`` 之后落进 ``seat`` 手牌的 ``card_move`` 记录（= 这之后的每一次摸牌）。"""
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.seq > seq
        and record.to_zone.zone is Zone.HAND
        and record.to_zone.seat == seat
    ]


# ---------------------------------------------------------------------------
# ① 时点：起始手牌之后、第一回合之前，锁定技不问"是否发动"
# ---------------------------------------------------------------------------


def test_tiancheng_fires_at_game_start_before_the_first_turn():
    """L0.3 的位置（规则 §3.9 L229–L233）+ 锁定技（不问"是否发动"）。

    判据比较**记录序本身**（`CODING_STANDARDS.md` §4：不排序、不去重）：起始手牌 <
    ``GAME_START`` < 三次展示询问 < 第一条 ``TURN_BEFORE_START``。
    """
    engine = _wuming_engine(seed=7)
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]
    _run_to_first_turn(engine, players)

    deal = _timing(engine, Timing.GAME_INITIAL_HAND_DEALING)
    start = _timing(engine, Timing.GAME_START)
    turn = _timing(engine, Timing.TURN_BEFORE_START)
    reveals = _query_records(engine)

    assert [record.query.kind for record in reveals] == ["SELECT_OPTION"] * 3, (
        "三次展示是一问一提交；锁定技不发『是否发动』（技能 id 前缀下不该有别的询问）"
    )
    assert deal.seq < start.seq < reveals[0].seq < reveals[1].seq < reveals[2].seq < turn.seq
    assert not [
        record
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SKILL_ACTIVATION"
    ], "锁定技（§四 4.12）不问『是否发动』：整条 L0 只有展示询问"
    assert start.actor is None, "『游戏开始时』没有当前回合角色（帧外派发点的 L0 口径）"
    assert CATALOG[TIANCHENG].locked is True
    assert CATALOG[TIANCHENG].requirement == TIANCHENG_REQUIREMENT, (
        "提示条文案 = 牌面原文逐字"
    )
    # 三次展示之后第一回合照常开始（不是死路）：无身份局的先手 = `first_seat`（0）。
    assert engine.state.turn_seat == 0 and engine.finished is False


# ---------------------------------------------------------------------------
# ② 抽取：一次性 3 张、真名互异且不在场上、抽到即入屏蔽集合
# ---------------------------------------------------------------------------


def test_tiancheng_draws_three_distinct_true_names_absent_from_the_table():
    """两段随机（票 07）：3 个**互不相同**的真名，都不是场上武将的真名。"""
    engine = _wuming_engine(seed=11)
    players = [_TianchengPlayer(("effect", "first", "effect")), PassPlayer()]
    _run_to_first_turn(engine, players)

    drawn = engine.state.drawn_true_names
    seat_names = {
        _true_name(player.hero)
        for player in engine.state.players
        if player.hero is not None
    }
    assert len(drawn) == TIANCHENG_REVEALS == 3
    assert len(set(drawn)) == 3, f"三个真名必须两两不同：{drawn}"
    assert "无名" in seat_names, "夹具依赖：0 号位就是无名自制版"
    assert not set(drawn) & seat_names, f"场上武将的真名不得被抽到：{set(drawn) & seat_names}"

    revealed = [record.query.hero_card.hero_id for record in _query_records(engine)]
    assert all(hero_id is not None for hero_id in revealed), "池子足够时要发满三张"
    assert tuple(_true_name(hero_id) for hero_id in revealed) == drawn, (
        "展示序 = 抽取序（一次抽 3 张，再依次展示）"
    )
    for hero_id in revealed:
        hero = hero_by_id(hero_id)
        assert hero is not None and hero.enabled, "抽到的版本必须是启用版本"


def test_tiancheng_blocks_the_drawn_true_names_for_the_rest_of_the_game():
    """抽到即入屏蔽集合（票 05 / 07）：整局不再抽到，座位真名照旧在集合里。"""
    engine = _wuming_engine(seed=13)
    players = [_TianchengPlayer(("first", "first", "first")), PassPlayer()]
    _run_to_first_turn(engine, players)

    first = engine.state.drawn_true_names
    blocked = blocked_true_names(engine.state)
    assert set(first) <= blocked, "抽到的真名立刻进屏蔽集合（重入不重抽、下一个技能也不重发）"
    assert "无名" in blocked, "座位自己的真名也在集合里（在场那半是派生的）"

    # 跑到第一回合结束之后再读一次：屏蔽不是"只在 L0 那一刻成立"。
    run_queries(engine, players, lambda e: e.state.round > 1 or e.finished)
    assert set(first) <= blocked_true_names(engine.state), "整局屏蔽"

    # 构造第二次抽取：抽到的真名与第一批无交集，且都进了集合。
    second = engine.draw_hero_cards(3)
    second_names = tuple(_true_name(hero_id) for hero_id in second)
    assert len(second_names) == 3
    assert not set(second_names) & set(first), "同一真名不会再抽到（抽到即入集合）"
    assert engine.state.drawn_true_names == (*first, *second_names), "抽走序 + 去重"
    assert set(second_names) <= blocked_true_names(engine.state)


# ---------------------------------------------------------------------------
# ③ 三次展示：一次一张 + n/3 副标题 + 选项闭集
# ---------------------------------------------------------------------------


def test_tiancheng_one_hero_at_a_time_with_an_n_of_m_header():
    """「依次展示」= 一次一张：每问只有一个武将块、副标题 1/3 → 3/3、问与答严格交替。"""
    engine = _wuming_engine(seed=17)
    players = [_TianchengPlayer(("first", "first", "first")), PassPlayer()]
    _run_to_first_turn(engine, players)

    reveals = _query_records(engine)
    cards = [record.query.hero_card for record in reveals]
    assert [(card.index, card.total) for card in cards] == [(1, 3), (2, 3), (3, 3)], (
        "副标题 = 第 k/3 张；分母恒为 3"
    )
    assert len({card.hero_id for card in cards}) == 3, "三张武将牌互不相同"
    for record, card in zip(reveals, cards):
        assert record.query.hero_card is not None
        skill_rows = tuple(hero_card_skill_ids(card.hero_id)) if card.hero_id else ()
        assert tuple(record.query.options) == (*skill_rows, TIANCHENG_MAX_HP), (
            "选项闭集 = 该武将牌的非 ☆ 技能（数据文件序）+ 选项 2（固定最后）"
        )

    # 一次一张：每条询问都在它的应答之后才发下一条（记录序，不排序）。
    actions = [record for record in engine.records if record.kind == "action"]
    answered = {record.answers_seq: record.seq for record in actions}
    order = []
    for record in reveals:
        order.append(record.seq)
        order.append(answered[record.seq])
    assert order == sorted(order), "问 → 答 → 问 → 答：不会一次铺开三张"


def test_tiancheng_shows_skill_name_and_description_and_greys_the_owned_skill():
    """技能行 = 技能名 + `descriptions.json` 原文；**已拥有（同 skill id）⇒ 置灰**。"""
    engine = _wuming_engine(seed=5)
    _pin_pool(engine, ("关羽",))
    # 关羽的两个版本共用"武圣"这个名字、但 skill id 不同；两版都先授予，抽到哪版都置灰。
    assert engine.grant_skill(0, "hero.guanyu.wusheng") is True
    assert engine.grant_skill(0, "hero.jie_guanyu.wusheng") is True
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]

    run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and str(e.pending.skill_id or "").startswith(TIANCHENG),
    )
    payload = pending_payload(engine.pending, human_seat=0)

    card = payload["hero_card"]
    assert card["total"] == 3 and card["index"] == 1
    assert card["name"] in {"关羽", "界关羽"} and card["kingdom_label"] == "蜀"
    assert card["kingdom"] == "shu", "势力徽记取数据表"
    assert card["hp_max"] == 4, "武将块画**原值**（`hp_max/hp_max` 的点数条）"

    rows = payload["option_details"]
    assert rows[-1]["choice"] == TIANCHENG_MAX_HP, "选项 2 固定最后"
    wusheng = [row for row in rows if str(row["skill_id"] or "").endswith(".wusheng")]
    assert len(wusheng) == 1, f"关羽牌上必须有【武圣】一行：{rows}"
    row = wusheng[0]
    assert row["text"] == "武圣", "技能名取数据表 `HeroSkill.name`"
    assert row["description"] == SKILL_DESCRIPTIONS[row["choice"]], "描述 = descriptions.json 原文"
    assert len(row["description"]) > 20, "不截断"
    assert row["disabled"] is True, "已拥有同 skill id ⇒ 置灰（保留原位）"
    assert row["choice"] not in [
        action.choice
        for action in engine.pending.legal_actions
        if isinstance(action, SelectOption)
    ], "置灰项只留在载荷里，不在候选里"

    effect_row = rows[-1]
    assert effect_row["text"] == _OPTION_TWO_LABEL.format(x=1), "选项 2 的 X 是具体数字"
    assert effect_row["description"] is None and effect_row["skill_id"] is None
    assert effect_row["disabled"] is False


def test_tiancheng_never_offers_a_pass():
    """牌面已改「须」：三次展示都**必须**选一项——没有 `Pass`、没有「放弃」出口。"""
    engine = _wuming_engine(seed=19)
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]
    _run_to_first_turn(engine, players)

    reveals = _query_records(engine)
    assert len(reveals) == 3
    for record in reveals:
        query = record.query
        assert not [action for action in query.legal_actions if isinstance(action, Pass)], (
            "『须』⇒ 没有放弃出口（`optional=False` 的候选里不放 `Pass`）"
        )
        assert TIANCHENG_MAX_HP in query.options, "选项 2 恒可执行（满体力 / 全是置灰行也照给）"
    # 三问都答得出去，游戏照常进入第一回合（不是死路）。
    assert ran_timing(engine, Timing.TURN_BEFORE_START)
    assert engine.finished is False


# ---------------------------------------------------------------------------
# ④ 效果 ①：获得技能，当场生效
# ---------------------------------------------------------------------------


def test_tiancheng_grants_the_chosen_skill_and_it_works_immediately():
    """点技能行即提交：`grant_skill`（第四种来源）⇒ 立刻出现在出牌阶段的转化清单里。"""
    red = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit in ("heart", "diamond")
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hp_max=2, hero=WUMING, hand=(red,)),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=deck_excluding(PACK, red),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=5)
    _pin_pool(engine, ("关羽",))
    players = [_TianchengPlayer((".wusheng", "effect", "effect")), PassPlayer()]

    run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 0,
    )

    granted = engine.state.player(0).granted_skills
    assert len(granted) == 1 and granted[0].endswith(".wusheng"), (
        f"技能行点下去就授予（同 skill id 那条来源）：{granted}"
    )
    skill_id = granted[0]
    assert [record for record in _deltas(engine, "skill_gain")], "授予落一条 skill_gain"
    instances = [
        instance
        for instance in resolve_instances(engine.state)
        if instance.owner == 0 and instance.skill_id == skill_id
    ]
    assert len(instances) == 1 and instances[0].source.kind is SkillSourceKind.GRANTED

    # **立刻生效**：【武圣】的转化在同一个出牌阶段就可用（红色手牌 → （杀)）。
    assert any(
        offer_skill == skill_id for _card, offer_skill in conversion_offers(engine.state, 0, "slash")
    ), "获得的技能立刻参与源筛选"
    assert skill_id in engine.pending.transform_preactivations, (
        "出牌阶段的转化清单里要有刚获得的那条技能"
    )
    assert red in {
        int(option)
        for step in engine.pending.transform_preactivations[skill_id]
        if step.name == "card"
        for pool in step.pool
        for option in pool.options
    }, "红牌可以被【武圣】当【杀】"


# ---------------------------------------------------------------------------
# ⑤ 效果 ②：加体力上限 → 回复 → 摸 X 张（X = 1 / 2 / 3）
# ---------------------------------------------------------------------------


def test_tiancheng_adds_one_max_hp_recovers_one_then_draws_x_in_that_order():
    """三张全选选项 2：上限 2 → 5、回复各 1、摸 1 + 2 + 3 = 6 张（次序逐张可读）。"""
    engine = _wuming_engine(seed=23)
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]
    _run_to_first_turn(engine, players)

    player = engine.state.player(0)
    assert (player.hp, player.hp_max) == (5, 5), "上限 +3、回复 +3（`hp_limit` 夹满）"
    assert len(player.hand) == 4 + 1 + 2 + 3, "起始手牌 4 + 摸 1 + 2 + 3（第一回合还没摸牌）"

    maxlife = _deltas(engine, "hp_max_change")
    hp = _deltas(engine, "hp_change")
    assert [(record.before, record.after) for record in maxlife] == [(2, 3), (3, 4), (4, 5)]
    assert [(record.before, record.after, record.amount) for record in hp] == [
        (2, 3, 1),
        (3, 4, 1),
        (4, 5, 1),
    ]
    start = _timing(engine, Timing.GAME_START)
    draws = _hand_draws_after(engine, 0, start.seq)
    assert len(draws) == 3, f"三次选项 2 各一次摸牌：{len(draws)}"

    for index in range(3):
        assert maxlife[index].seq < hp[index].seq < draws[index].seq, (
            "次序 = 加体力上限 → 回复 1 点 → 摸 X 张（`01-wuming.md` 的 O3）"
        )

    # 选项 2 的文本逐张给具体 X（1 / 2 / 3），不出现占位符。
    labels = [
        record.query.option_texts[TIANCHENG_MAX_HP] for record in _query_records(engine)
    ]
    assert labels == [_OPTION_TWO_LABEL.format(x=x) for x in (1, 2, 3)]
    assert all("摸 X 张牌" not in label for label in labels), "X 必须写成具体数字"


def test_tiancheng_uses_the_max_life_frame_for_the_increase():
    """加体力上限走 `MAXLIFE` 事件帧（票 04）：帧记录坐标 = 两个时机 + `hp_max_change`。"""
    engine = _wuming_engine(seed=29)
    players = [_TianchengPlayer(("effect", "first", "first")), PassPlayer()]
    _run_to_first_turn(engine, players)

    pushes = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind is FrameKind.MAXLIFE
    ]
    pops = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.MAXLIFE
    ]
    assert len(pushes) == 1 and len(pops) == 1, "只选了一次选项 2 ⇒ 恰好一个 MAXLIFE 帧"
    frame_id = pushes[0].frame_id
    assert pops[0].frame_id == frame_id

    (delta,) = _deltas(engine, "hp_max_change")
    assert delta.frame_id == frame_id, "`hp_max_change` 的 frame_id 指向该帧（票 04 C2）"
    assert (delta.seat, delta.before, delta.after, delta.amount) == (0, 2, 3, 1)

    timings = [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing
        in (Timing.MAXLIFE_INCREASE_BEFORE, Timing.MAXLIFE_INCREASE_AFTER)
    ]
    assert [record.timing for record in timings] == [
        Timing.MAXLIFE_INCREASE_BEFORE,
        Timing.MAXLIFE_INCREASE_AFTER,
    ]
    assert all(record.frame_id == frame_id for record in timings), "两个『加』时机都挂在这一帧上"
    assert timings[0].seq < delta.seq < timings[1].seq, "前 → 落上限 → 后"
    # 加上限**不同步体力**：回复是第二段（RECOVER 帧），不在这条 delta 里。
    assert engine.state.player(0).hp == 3, "2 + 回复 1（帧外那段）"


def test_tiancheng_still_reveals_three_times_with_option_two_only_when_no_hero_is_left():
    """可抽真名 0 个 ⇒ **仍展示 3 次**：每次没有武将牌、只给选项 2（第四轮裁定 1）。"""
    engine = _wuming_engine(seed=31)
    _pin_pool(engine, ())
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]

    run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and str(e.pending.skill_id or "").startswith(TIANCHENG),
    )
    payload = pending_payload(engine.pending, human_seat=0)
    card = payload["hero_card"]
    assert card["hero_id"] is None and card["name"] is None and card["hp_max"] is None, (
        "缺的那几次**没有武将块**（不是把 hero_card 键整个省掉）"
    )
    assert (card["index"], card["total"]) == (1, 3), "分母恒为 3"
    assert [row["choice"] for row in payload["option_details"]] == [TIANCHENG_MAX_HP], (
        "只有选项 2 一行"
    )
    assert payload["option_details"][0]["text"] == _OPTION_TWO_LABEL.format(x=1)

    _run_to_first_turn(engine, players)
    reveals = _query_records(engine)
    assert len(reveals) == 3, "一定展示 3 次"
    for index, record in enumerate(reveals):
        shown = record.query.hero_card
        assert shown.hero_id is None and (shown.index, shown.total) == (index + 1, 3)
        assert tuple(record.query.options) == (TIANCHENG_MAX_HP,)
        assert not [action for action in record.query.legal_actions if isinstance(action, Pass)]


# ---------------------------------------------------------------------------
# ⑥ 02 D / E 的验收（构造用例落在本票）
# ---------------------------------------------------------------------------


def test_a_granted_lord_skill_works_for_a_non_lord():
    """**获得**的主公技在非主公身上能发动：无名从张角移动版的武将牌上拿到【黄天】。"""
    dodge = cards_of_type(PACK, "dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hp_max=2, hero=WUMING, identity=Identity.REBEL, kingdom=Kingdom.QUN),
            SeatSpec(
                hp=4,
                hp_max=4,
                hand=(dodge,),
                identity=Identity.REBEL,
                kingdom=Kingdom.QUN,
            ),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LORD, kingdom=Kingdom.WEI),
            SeatSpec(hp=4, hp_max=4, identity=Identity.LOYALIST, kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, dodge),
        rule_config=RuleConfig(player_count=4),
    )
    engine = Engine.from_state(state, seed=3)
    _pin_pool(engine, ("张角",))
    # jie-feng-batch2 票 02 之后池里有两个「张角」（移动版 / 界移动版），抽到哪一版都可能；
    # 按**后缀**挑（与【武圣】那一档同一手法），后面断言的是**抽中的那一条**主公技。
    players = [
        _TianchengPlayer((".huangtian", "effect", "effect")),
        PassPlayer(),
        PassPlayer(),
        PassPlayer(),
    ]

    run_queries(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
        and e.pending.actor == 1,
    )
    drawn = players[0].queries[0]
    picked = next(
        action.choice
        for action in drawn.legal_actions
        if isinstance(action, SelectOption) and str(action.choice).endswith(".huangtian")
    )
    action = find_action(
        engine.pending,
        lambda item: isinstance(item, ActivateSkill) and item.skill_id == picked,
    )
    assert action is not None, "座位 1 的出牌阶段应能发动无名获得的主公技【黄天】"
    granted = [
        instance
        for instance in resolve_instances(engine.state)
        if instance.owner == 0 and instance.skill_id == picked
    ]
    assert len(granted) == 1 and granted[0].source.kind is SkillSourceKind.GRANTED, (
        "主公技的身份门控只作用于武将牌载体（票 06）"
    )

    engine.submit(action)
    run_queries(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == QueryKind.SELECT_CARDS,
    )
    engine.submit(SelectCards(answers_seq=engine.pending.seq, picks=(0,)))
    run_queries(engine, players, lambda e: dodge in e.state.player(0).hand)
    assert dodge not in engine.state.player(1).hand, "交出的【闪】进了获得者的手牌"


def test_a_dead_heros_true_name_is_released():
    """阵亡武将牌移出本局后真名解除屏蔽（票 05 的派生口径；下一次抽取就能抽到它）。"""
    engine = Engine.from_state(
        build_state(
            pack=PACK,
            seats=[
                SeatSpec(hp=2, hp_max=2, hero=WUMING),
                SeatSpec(hp=3, hp_max=3, hero="diaochan"),
            ],
            deck=deck_excluding(PACK),
            turn_seat=0,
        ),
        seed=37,
    )
    players = [_TianchengPlayer(("effect", "effect", "effect")), PassPlayer()]
    _pin_pool(engine, ("貂蝉",))  # 除了貂蝉，其余真名全部"已抽走" ⇒ 池子里只剩她

    _run_to_first_turn(engine, players)
    assert "貂蝉" in blocked_true_names(engine.state), "在场武将的真名在屏蔽集合里"
    assert engine.draw_hero_cards(1) == (), "池子里只剩她在场 ⇒ 抽不到"

    declare_death(engine, 1)
    run_queries(
        engine,
        players,
        lambda e: not e.state.player(1).alive
        and not e.state.player(1).death_time_skills_active,
    )
    assert "貂蝉" not in blocked_true_names(engine.state), (
        "武将牌移出本局（死亡结算收尾）后真名解除屏蔽"
    )
    drawn = engine.draw_hero_cards(1)
    assert len(drawn) == 1 and _true_name(drawn[0]) == "貂蝉", (
        "下一次抽取真的能抽到它（与【天成】读同一个 `draw_hero_cards`）"
    )


# ---------------------------------------------------------------------------
# ⑦ 数据面 / 目录（票 01 的"数据面"一条腿）
# ---------------------------------------------------------------------------


def test_the_wuming_hero_sheet_and_its_catalog_entry_are_registered():
    """数据面：`heroes.homebrew.json` 的条目 + `CATALOG` 的定义 + 描述全文都在。"""
    hero = hero_by_id(WUMING)
    assert hero is not None
    assert (hero.name, hero.true_name) == ("无名自制版", "无名")
    assert (hero.kingdom, hero.gender, hero.hp_max) == (Kingdom.QUN, Gender.MALE, 2)
    assert hero.version == "homebrew" and hero.enabled is True
    assert hero_card_skill_ids(WUMING) == (TIANCHENG,)
    definition = CATALOG[TIANCHENG]
    assert definition.locked is True
    assert [hook.timing for hook in definition.hooks] == [Timing.GAME_START]
    assert TIANCHENG in SKILL_DESCRIPTIONS, "描述全文只有一份（服务端 descriptions.json）"
    # 2026-11-12 用户裁定（表现层白名单 A）：描述文案可自由改，只核条目存在与非空。
    assert SKILL_DESCRIPTIONS[TIANCHENG]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
