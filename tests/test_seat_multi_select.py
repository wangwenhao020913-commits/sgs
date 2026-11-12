"""table-ux-overhaul 票 13：**座位多选收口**（连营 / 大雾 / 突袭 / 奋威 / 除疠）。

本模块钉这一票的两条**结构性**判据，逐技能的机制断言住在各自的家文件里
（`test_hero_card_zone.py` 的【连营】、`test_hero_jie_qun.py` 的【除疠】、
`test_hero_jie_negation.py` 的【奋威】等）：

- ``test_ask_seats_is_the_only_multi_seat_entry``：全仓只有 :meth:`Engine.ask_seats`
  一处发"一次选多名角色"的询问；三处手写逐座 ``while`` 循环（连营 / 大雾 / 奋威）与
  三个逐座询问名（``lianying:seat:<n>`` / ``dawu:seat:<n>`` / ``fenwei:<n>``）已消失，
  步骤语言的 ``while`` 只剩 ``ordered`` 的那一档。
- ``test_dawu_and_fenwei_pick_their_targets_in_one_ask``：神诸葛亮【大雾】与界甘宁
  【奋威】各自只要**一次**询问就选齐全部目标（牌桌上多点、一次提交）。

用例形态：真引擎（``Engine``）+ 构造局面 + 脚本玩家；判据读**记录流**里的询问
（`Engine.records`），不读合成载荷。
"""

from __future__ import annotations

from pathlib import Path

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.skills.shen_zhugeliang import star_zone_key
from sgs.state import (
    ActivateSkill,
    EndPhase,
    HeroCardZone,
    Pass,
    QueryKind,
    SelectCards,
    SelectOptions,
    UseCard,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "sgs"

DAWU = "hero.shen_zhugeliang.dawu"
QIXING = "hero.shen_zhugeliang.qixing"
FENWEI = "hero.jie_ganning.fenwei"
BARBARIAN = "barbarian_invasion"

SEAT_ENTRY = "def ask_seats("
"""座位多选**唯一入口**的定义（引擎侧一处）。"""

SEAT_CALLERS = (
    "engine/game.py",  # 定义（同时是唯一写 min_selected / max_selected 的地方）
    "skills/flow.py",  # 步骤语言：hi > 1 ∧ not ordered
    "skills/hero_zone.py",  # 界陆逊【连营】
    "skills/shen_zhugeliang.py",  # 神诸葛亮【大雾】
    "skills/negation.py",  # 界甘宁【奋威】
    "skills/phase_body.py",  # 界顾雍【秉壹】（本模板的首个载体）
)
"""``ask_seats`` 的**全部**出现处（定义 + 五个调用点）。多一处 / 少一处都要改这张表。"""


def _source(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


def _python_files() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py"))


def test_ask_seats_is_the_only_multi_seat_entry() -> None:
    """全仓只有 ``Engine.ask_seats`` 一处发座位多选；三处手写逐座循环已消失。

    这是"至多 N 名只写一次"的**结构判据**（票面验收第一条）：``min_selected`` /
    ``max_selected`` / ``enumerate_candidates=False`` 三条载荷只在那个 helper 里出现，
    三条手写技能不再各自拼一遍座位询问。
    """
    game = _source("engine/game.py")
    assert game.count(SEAT_ENTRY) == 1, "座位多选的入口只允许定义一次"
    body = game.split(SEAT_ENTRY, 1)[1].split("\n    def ", 1)[0]
    assert "min_selected=lo" in body and "max_selected=hi" in body, (
        "区间由 helper 内部固定（调用方只给 lo / hi）"
    )
    assert "enumerate_candidates=False" in body, "座位多选恒走不枚举档（子集数组合爆炸）"

    mentions = {
        str(path.relative_to(SRC)): path.read_text(encoding="utf-8").count("ask_seats(")
        for path in _python_files()
        if "ask_seats(" in path.read_text(encoding="utf-8")
    }
    assert set(mentions) == set(SEAT_CALLERS), (
        "``ask_seats`` 只许这五个消费者：多一处 = 又长出一份手写座位多选；"
        f"实得 {sorted(mentions)}"
    )
    assert all(count == 1 for count in mentions.values()), (
        f"每个文件恰好一处（定义 / 一次调用）：{mentions}"
    )

    for legacy in (
        'f"lianying:seat:',
        'f"dawu:seat:',
        'f"fenwei:',
    ):
        offenders = [
            str(path.relative_to(SRC))
            for path in _python_files()
            if legacy in path.read_text(encoding="utf-8")
        ]
        assert not offenders, f"逐座询问名 {legacy} 还在 {offenders}（应改成一次问齐）"

    assert "while len(chosen) < hi" in _source("skills/flow.py"), (
        "ordered 的那一档仍保留逐座循环（目前没有消费者落在它上面）"
    )
    for relative in ("skills/hero_zone.py", "skills/shen_zhugeliang.py", "skills/negation.py"):
        assert "while len(chosen)" not in _source(relative), (
            f"{relative} 的手写逐座循环应已删掉"
        )


# ---------------------------------------------------------------------------
# 打桩玩家：一次选齐 / 最小应答
# ---------------------------------------------------------------------------


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


class _Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答（放弃 / 结束阶段 / 首个候选）。"""

    def __init__(self, handler=None) -> None:
        self.handler = handler
        self.queries: list = []
        self._fallback = PassPlayer()

    def respond(self, query):
        self.queries.append(query)
        if self.handler is not None:
            action = self.handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _activate(query, skill_id: str):
    return _find(
        query,
        lambda action: isinstance(action, ActivateSkill) and action.skill_id == skill_id,
    )


def _queries_of(engine: Engine, kind: QueryKind, *, prefix: str = "") -> list:
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == kind
        and str(getattr(record.query, "skill_id", "")).startswith(prefix)
    ]


# ---------------------------------------------------------------------------
# 大雾 / 奋威：各一次询问选齐目标
# ---------------------------------------------------------------------------


def test_dawu_and_fenwei_pick_their_targets_in_one_ask() -> None:
    """【大雾】与【奋威】都只问**一次**座位多选，答案是一个集合（票 13 的收口断言）。

    两段各起一个真引擎：

    - **大雾**：神诸葛亮（0 号位）结束阶段发动【大雾】、弃 1 枚星 ⇒ 一次选人问「谁获得
      大雾」；区间 ``[1, 1]``（k = 星数），答案选自己。
    - **奋威**：界甘宁（1 号位）对【南蛮入侵】的两个目标发动【奋威】⇒ 一次选人问「令谁
      无效」；区间 ``[1, 2]``（任意名、至少一名），答案选两名目标。

    两次都断言 ``SELECT_OPTIONS`` ⇒ 界面会进牌桌多选面、一次提交（``option_seats`` 非空）。
    """
    # --- 大雾 ---
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="shen_zhugeliang", hp=3, hp_max=3),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=deck_excluding(PACK, *cards_of_type(PACK, "peach")[:2]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    state.player(0).hero_cards[star_zone_key(0)] = HeroCardZone(
        skill_id=QIXING,
        owner=0,
        cards=list(cards_of_type(PACK, "peach")[:2]),
    )
    dawu_engine = Engine.from_state(state, seed=7)

    def dawu_driver(query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _activate(query, DAWU)
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        if query.kind == QueryKind.SELECT_OPTIONS:
            return SelectOptions(choices=("seat:0",), answers_seq=query.seq)
        return None

    run_turns(dawu_engine, [_Scripted(dawu_driver), PassPlayer()], turns=1)

    dawu_asks = _queries_of(dawu_engine, QueryKind.SELECT_OPTIONS, prefix="dawu:")
    assert len(dawu_asks) == 1, "一次问齐：弃 1 枚星只问一次『谁获得大雾』"
    dawu_ask = dawu_asks[0]
    assert tuple(dawu_ask.options) == ("seat:0", "seat:1")
    assert (dawu_ask.min_selected, dawu_ask.max_selected) == (1, 1), "k = 星数"
    marked = [
        record.name
        for record in dawu_engine.records
        if record.kind == "delta" and record.delta_type == "marker_change" and record.count
    ]
    assert marked == ["大雾"], "选中的人各得一枚「大雾」（一次提交、逐座结算）"

    # --- 奋威 ---
    barbarian = cards_of_type(PACK, BARBARIAN)[0]
    fenwei_state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,), identity=Identity.LORD),
            SeatSpec(hero="jie_ganning", identity=Identity.LOYALIST),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.RENEGADE),
        ],
        deck=deck_excluding(PACK, barbarian),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    fenwei_engine = Engine.from_state(fenwei_state, seed=3)

    def user_driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == barbarian,
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return None

    def fenwei_driver(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == FENWEI:
            return _activate(query, FENWEI)
        if query.kind == QueryKind.SELECT_OPTIONS and str(query.skill_id).startswith(
            "fenwei:"
        ):
            return SelectOptions(choices=("seat:2", "seat:3"), answers_seq=query.seq)
        return None

    run_until(
        fenwei_engine,
        [
            _Scripted(user_driver),
            _Scripted(fenwei_driver),
            PassPlayer(),
            PassPlayer(),
        ],
        # 牌结算完 = 没被无效的座位 1（界甘宁自己）吃下【南蛮入侵】的伤害。
        lambda e: e.state.player(1).hp < 4,
    )

    fenwei_asks = _queries_of(fenwei_engine, QueryKind.SELECT_OPTIONS, prefix="fenwei:")
    assert len(fenwei_asks) == 1, "一次问齐：两个目标在一问里点齐"
    fenwei_ask = fenwei_asks[0]
    assert set(fenwei_ask.options) == {"seat:1", "seat:2", "seat:3"}, (
        "池 = 这张【南蛮入侵】的全部目标（含界甘宁本人，不含使用者）"
    )
    assert (fenwei_ask.min_selected, fenwei_ask.max_selected) == (1, 3), "任意名（至少一名）"
    assert (fenwei_engine.state.player(2).hp, fenwei_engine.state.player(3).hp) == (4, 4), (
        "被【奋威】无效的目标不产生效果"
    )
    assert fenwei_engine.state.player(1).hp == 3, "没被选中的目标照常结算（没有【杀】⇒ 掉 1 点）"
    assert ran_timing(fenwei_engine, Timing.USE_AFTER_DESIGNATE_TARGET)


def test_the_ai_fills_the_whole_seat_set_in_one_answer() -> None:
    """AI 适配（票 13）：一次问齐的座位多选由 AI **自己拼集合**（不枚举档的组装路径）。

    座位多选恒走不枚举档（``structural``）：候选里只有一条骨架，故 AI 必须按同一份形状
    谓词自己构造答案（``heuristic._assembled_option_set``）。判据读**真询问**：AI 的答案
    是一个 ``SelectOptions`` 集合、长度落在 ``[min, cap]`` 内，且引擎**接受**它。
    """
    from sgs.agents import HeuristicPlayer
    from sgs.engine import ai_stream

    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="shen_zhugeliang", hp=3, hp_max=3),
            SeatSpec(hp=4, hp_max=4),
        ],
        deck=deck_excluding(PACK, *cards_of_type(PACK, "peach")[:2]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    state.player(0).hero_cards[star_zone_key(0)] = HeroCardZone(
        skill_id=QIXING,
        owner=0,
        cards=list(cards_of_type(PACK, "peach")[:1]),
    )
    engine = Engine.from_state(state, seed=7)

    def driver(query):
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _activate(query, DAWU)
        if query.kind == QueryKind.SELECT_CARDS:
            return SelectCards(picks=(0,), answers_seq=query.seq)
        return None

    run_until(
        engine,
        [_Scripted(driver), PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SELECT_OPTIONS
        and str(e.pending.skill_id).startswith("dawu:"),
    )
    query = engine.pending
    assert query is not None and query.kind == QueryKind.SELECT_OPTIONS

    action = HeuristicPlayer(ai_stream(7, 0)).respond(query)

    assert isinstance(action, SelectOptions), "AI 交的是**一个集合**（不是逐座单选）"
    assert action.answers_seq == query.seq
    assert 1 <= len(action.choices) <= 1, "k = 1 ⇒ 恰好一名（cap 由引擎给）"
    assert set(action.choices) <= set(query.options), "集合的成员必须来自服务端下发的池"
    engine.submit(action)  # 引擎接受 ⇒ 形状谓词满足（拒绝会抛 IllegalActionError）


# ---------------------------------------------------------------------------
# P5（票 13 的评审）：**池按已选收缩**的座位步 —— AI 自建集合的上限只在这一档重夹。
#
# 不枚举档的候选只是骨架，集合由 `heuristic._assembled_option_set` 自己拼。对普通座位步
# 上限 = `cap_of`（= min(max_selected, 可选项数)）；只有"询问只带首问快照、真正的约束住
# 在 `pool(chosen)`"的那一档（除疠）会先按公开势力收窄，再按**收窄后的**池重夹一次——
# 否则 AI 会交一条服务端铺不出来的集合（引擎按前缀池复核后整步取消，人类看到的是
# "技能发动了、什么都没发生"）。
# ---------------------------------------------------------------------------

CHULI_SKILL = "hero.jie_huatuo.chuli"


def _chuli_pending_query():
    """真引擎停在【除疠】的座位多选上：1 / 2 同属魏、3 属蜀。"""
    from sgs.data import Kingdom

    hand = tuple(cards_of_type(PACK, "slash")[:4])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(hand[0],), hero="jie_huatuo", kingdom=Kingdom.QUN),
            SeatSpec(hand=(hand[1],), kingdom=Kingdom.WEI),
            SeatSpec(hand=(hand[2],), kingdom=Kingdom.WEI),
            SeatSpec(hand=(hand[3],), kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=13)

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _activate(query, CHULI_SKILL)
        return None

    run_until(
        engine,
        [_Scripted(driver), PassPlayer(), PassPlayer(), PassPlayer()],
        lambda e: e.pending is not None
        and e.pending.kind == QueryKind.SELECT_OPTIONS
        and str(e.pending.skill_id).startswith("chuli:"),
    )
    query = engine.pending
    assert query is not None and query.kind == QueryKind.SELECT_OPTIONS
    return query


def test_the_ai_never_offers_two_same_kingdom_seats_for_a_contracting_pool() -> None:
    """【除疠】：AI 自建集合必须按**公开势力**收窄，且仍落在形状谓词的区间内。

    询问带的池是**首问快照**（`seat:1` / `seat:2` 同属魏，两个都在面上）；服务端
    `pool(chosen)` 会拒掉"两个同势力"的集合。AI 因此自己按势力收窄——收窄之后 `cap`
    跟着**实际参与构造的池**走（P5：这一档之外的询问上那一步是恒等）。
    """
    from sgs.agents import heuristic
    from sgs.effects import is_legal_option_set

    query = _chuli_pending_query()
    assert {"seat:1", "seat:2", "seat:3"} <= set(query.options), (
        f"前提：首问快照里两个魏将都在面上：{query.options}"
    )

    assembled = heuristic._assembled_option_set(query)

    assert is_legal_option_set(query, assembled.choices), (
        f"构造器交了一条越界集合：{assembled.choices}（区间 = "
        f"[{query.min_selected}, {query.max_selected}]）"
    )
    seats = [int(str(choice).removeprefix("seat:")) for choice in assembled.choices]
    kingdoms = [query.view.players[seat].kingdom for seat in seats]
    assert len(kingdoms) == len(set(kingdoms)), (
        f"AI 同时选了同一势力的两名：{assembled.choices}（服务端的 pool(chosen) 会拒）"
    )
    assert assembled.choices, "除疠至少选一名（min_selected = 1）"
