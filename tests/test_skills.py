"""技能机制与装备触发效果（施工票 30）。

落点与票 17 §"测试缝"一致：

- **规则函数 / 只读真源**（少量直接单测，M1 先例）：``target_is_effective`` 的青釭剑 ×
  仁王盾、``transformed_slash`` 的转化牌面事实、``legal_actions_for`` 的提供者序列、
  ``resolve_instances`` / ``instance_key`` / ``prune_skill_state`` 的实例模型。
- **库门面**（唯一"真"缝）：``Engine.from_state`` 的人为局面 + 脚本玩家，断言记录流里的
  发动询问、判定帧、伤害扣减 / 防止、牌移动与虚拟牌。

不测 ``CATALOG`` / 修饰器挂点 / ``OPS`` 注册表的内部结构（票 17 §"明确不开的缝"）；
行为全部经候选、询问、时机与 delta 流观测。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sgs.data import Color, Gender, RuleConfig, load_content_pack
from sgs.effects import (
    legal_actions_for,
    target_is_effective,
)
from sgs.engine import Engine, IllegalActionError
from sgs.resolution import Tag, has_tag
from sgs.skills import (
    CATALOG,
    instance_key,
    is_equipped,
    prune_skill_state,
    resolve_instances,
    transformed_slash,
)
from sgs.skills import catalog as catalog_module
from sgs.state import (
    ActivateSkill,
    DodgeContext,
    FrameKind,
    Pass,
    PlayCard,
    QueryKind,
    SelectCards,
    SelectOption,
    SelectOptionContext,
    UseCard,
    Zone,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _first_by_color(type_id: str, *, black: bool) -> int:
    from sgs.data import color_of_suit

    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id
        for card_id in _cards(type_id)
        if color_of_suit(PACK.deck.cards[card_id].suit) is wanted
    )


QINGGANG = _cards("qinggang_sword")[0]
RENWANG = _cards("renwang_shield")[0]
ICE_SWORD = _cards("ice_sword")[0]
QILIN = _cards("qilin_bow")[0]
DOUBLE_SWORD = _cards("double_sword")[0]
GREEN_DRAGON = _cards("green_dragon_blade")[0]
AXE = _cards("axe")[0]
EIGHT_DIAGRAMS = _cards("eight_diagrams")[0]
SERPENT = _cards("serpent_spear")[0]
CROSSBOW = _cards("crossbow")[0]
JUYING = _cards("jueying")[0]
CHITU = _cards("chitu")[0]
SLASH = _cards("slash")[0]
BLACK_SLASH = _first_by_color("slash", black=True)
RED_SLASH = _first_by_color("slash", black=False)


# ---------------------------------------------------------------------------
# 脚本玩家
# ---------------------------------------------------------------------------


class Scripted:
    """一个座位：先跑 ``handler``，返回 ``None`` 时退到最小应答（结束阶段 / 放弃）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _deck_excluding(*used: int) -> tuple[int, ...]:
    """其余全部实体牌（不够会短摸平局，构造局面必须把牌堆补满）。"""
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _judge_deck(judge_card: int, *, used: tuple[int, ...] = ()) -> tuple[int, ...]:
    """把 ``judge_card`` 放在**第 3 张**：摸牌阶段先取走前两张，判定取到的就是它。"""
    excluded = set(used) | {judge_card}
    fillers = [card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded]
    first, second = fillers[0], fillers[1]
    rest = [card_id for card_id in fillers[2:]]
    return (first, second, judge_card, *rest)


def _run(state, players, stop, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    run_until(engine, players, stop)
    return engine


def _slash_use(query, card_id: int, target: int):
    return _find(
        query,
        lambda action: isinstance(action, UseCard)
        and action.card.card_id == card_id
        and action.targets == (target,),
    )


def _select_slot_of(query, card_id: int):
    """挑**可见槽**上那张实体牌的动作（按槽位序号选中它）。

    隐藏槽（``slot.card is None``）认不出牌面，故本 helper 只服务"目标装备区"这类
    可见面的用例（票 10 的【麒麟弓】/【寒冰剑】驱动脚本）。
    """
    for index, slot in enumerate(query.slots):
        if slot.card is not None and slot.card.card_id == card_id:
            return _find(
                query, lambda action, i=index: isinstance(action, SelectCards) and action.picks == (i,)
            )
    return None


def _use_frame_popped(engine) -> bool:
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )


def _has_query(engine, kind: str, *, skill_id: str | None = None) -> bool:
    for record in engine.records:
        if record.kind != "query" or record.query.kind != kind:
            continue
        if skill_id is None or getattr(record.query, "skill_id", None) == skill_id:
            return True
    return False


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


# ---------------------------------------------------------------------------
# 青釭剑 × 仁王盾：EFFECTIVENESS 链上的 ignore_armor（票 07 §3 / 票 30）
# ---------------------------------------------------------------------------


def test_qinggang_sword_makes_a_black_slash_ignore_the_targets_renwang_shield():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(QINGGANG,)), SeatSpec(equip=(RENWANG,))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is True, (
        "青釭剑在 EFFECTIVENESS 链上先置 ignore_armor，仁王盾不再把黑杀判为无效"
    )


def test_qinggang_sword_only_ignores_armor_for_its_holders_own_slash():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(equip=(QINGGANG, RENWANG))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is False, (
        "青釭剑在目标身上：它不压制自己的防具，座位 0 的黑杀照常被仁王盾挡下"
    )


def test_qinggang_sword_does_not_change_effectiveness_for_non_slash_cards():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(QINGGANG,)), SeatSpec(equip=(RENWANG,))],
        turn_seat=0,
    )

    assert target_is_effective(state, state.card(RED_SLASH), 0, 1) is True


# ---------------------------------------------------------------------------
# 寒冰剑：防止伤害 + 依次弃两张（帧级 prevented）
# ---------------------------------------------------------------------------


def test_ice_sword_prevents_the_damage_and_discards_two_of_the_targets_cards():
    first, second = _cards("slash")[1], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)),
            SeatSpec(hand=(first, second)),
        ],
        deck=_deck_excluding(SLASH, ICE_SWORD, first, second),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert engine.state.player(1).hp == 4, "防止伤害 ⇒ 不扣体力"
    assert not any(
        record.kind == "delta" and record.delta_type == "hp_change" for record in engine.records
    ), "防止伤害不新增 delta"
    assert not any(
        record.kind == "frame" and record.frame_kind is FrameKind.LIFE_DECREASE
        for record in engine.records
    ), "防止伤害不新增时机 / 帧"
    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.ice_sword")
    assert _moves_of(engine, first) == [("hand", "discard", "DISCARD")]
    assert _moves_of(engine, second) == [("hand", "discard", "DISCARD")]


def test_ice_sword_clamps_to_the_targets_available_cards():
    """池不足退化：目标只有一张牌 ⇒ 只弃一张，不卡死（票 19 §6）。"""
    only = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)), SeatSpec(hand=(only,))],
        deck=_deck_excluding(SLASH, ICE_SWORD, only),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _moves_of(engine, only) == [("hand", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 4


def test_ice_sword_two_rounds_cannot_pick_the_same_card():
    """两轮**不能选同一张牌**（票 10 的守门测试）：第一轮搬走的那张不再出现在第二轮的面里。

    驱动脚本两轮都**先试着挑同一个可见槽**（``JUYING``）：只要第一轮的弃置是**立即搬走**
    （迁移前后的既定语义），第二轮的面里就没有它，脚本退到"挑第一个槽"从而拿走 ``CHITU``。
    若改成"先收集两张、提交点再一起弃"，第二轮的面里 ``JUYING`` 还在，它会被选第二遍
    ——``CHITU`` 一张都不动，断言立刻变红。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)),
            SeatSpec(equip=(JUYING, CHITU)),
        ],
        deck=_deck_excluding(SLASH, ICE_SWORD, JUYING, CHITU),
        turn_seat=0,
    )

    rounds: list[int] = []

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            rounds.append(len(query.slots))
            return _select_slot_of(query, JUYING) or _find(
                query, lambda action: isinstance(action, SelectCards)
            )
        return None

    engine = _run(
        state,
        [
            Scripted(attacker),
            Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert len(rounds) == 2, "固定两轮（目标有两张牌）"
    assert rounds[1] == 1, "第二轮的面里第一轮那张已经不在了（逐张立即搬走）"
    assert _moves_of(engine, JUYING) == [("equip", "discard", "DISCARD")]
    assert _moves_of(engine, CHITU) == [("equip", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 4, "防止伤害照旧"


def test_ice_sword_asks_nothing_when_the_target_has_no_cards():
    """目标没牌 ⇒ **不发没有候选的询问**，但"防止此伤害"照旧（票 10）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)), SeatSpec()],
        deck=_deck_excluding(SLASH, ICE_SWORD),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    engine = _run(
        state,
        [
            Scripted(attacker),
            Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.ice_sword")
    assert not _has_query(engine, "SELECT_CARDS"), "池为空 ⇒ 一个选牌询问都不发"
    assert engine.state.player(1).hp == 4, "没有牌可弃也照样防止伤害"


def test_ice_sword_round_two_asks_again_after_an_inline_suspension():
    """"每轮提交"（票 16）在这个手写循环里的形状 = **帧内游标 + 每轮独立缓存键**。

    第 1 轮立即搬走（= 提交）；搬掉的若是装备，`move_in_frame` 会内联派发【枭姬】并
    **挂起**，钩子带 ``"stay"`` 重入。重入时游标已在**搬牌之前**前进到 1，故问的是
    ``ice_sword:1``（第 2 轮、面里已经少了第 1 轮那张），而不是因入口刚置的
    ``Tag.PREVENTED`` 短路掉这次询问 —— 也不会把已提交的第 1 轮再搬一遍。
    """
    only = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(ICE_SWORD,)),
            SeatSpec(hero="sunshangxiang", hand=(only,), equip=(JUYING,)),
        ],
        deck=_deck_excluding(SLASH, ICE_SWORD, JUYING, only),
        turn_seat=0,
    )

    faces: list[tuple[int, bool]] = []  # (面内槽数, 这一步有没有 Pass 候选)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            round_index = len(faces)
            faces.append(
                (len(query.slots), any(isinstance(a, Pass) for a in query.legal_actions))
            )
            if round_index == 0:
                return _select_slot_of(query, JUYING)
            return _find(
                query, lambda action: isinstance(action, SelectCards) and action.picks == (0,)
            )
        return None

    def defender(query):
        if query.kind in ("DODGE", "SKILL_ACTIVATION"):
            # 拒绝【枭姬】：它**被问到**本身即证明"弃置装备"内联挂起过（重入的驱动源）。
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="hero.sunshangxiang.xiaoji"), (
        "第 1 轮搬装备触发【枭姬】：钩子挂起 ⇒ _ice_sword 必须重入"
    )
    assert [slots for slots, _ in faces] == [2, 1], (
        "两轮各一问：第 1 轮的面 = 手牌 + 装备；第 2 轮的面只剩手牌（第 1 轮那张已立即搬走）"
    )
    assert not any(passable for _, passable in faces), (
        "弃置两张是强制的（``optional=False``）⇒ 寒冰剑的轮内没有 Pass 候选"
    )
    assert _moves_of(engine, JUYING) == [("equip", "discard", "DISCARD")], (
        "已提交的第 1 轮不因挂起 / 重入而回滚或重放"
    )
    assert _moves_of(engine, only) == [("hand", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 4, "防止伤害照旧（``PREVENTED`` 只置一次、不短路）"


class _IceSwordHost:
    """【寒冰剑】两轮循环的最小宿主（只实现 :func:`_ice_sword` 用到的那几张面）。

    "轮内没有答案"这条出口经真实引擎走不到（``optional=False`` ⇒ 没有 ``Pass`` 候选，
    池空又由 ``has_zone_cards`` 提前挡住），故把断言直接钉在**循环本身**上：给宿主喂
    逐轮的答案，看已提交的轮在"下一轮没有答案"时是否被回滚。
    """

    def __init__(self, state, answers) -> None:
        self.state = state
        self._answers = list(answers)
        self.keys: list[str] = []
        self.moves: list[tuple[int, ...]] = []

    @staticmethod
    def suspended(value):
        return value == "SUSPEND"

    def has_zone_cards(self, seat: int) -> bool:
        return bool(self.state.player(seat).hand or self.state.player(seat).equip)

    def ask_selection(self, frame, *, owner, key, actor, timing, spec, build_face):
        del frame, owner, actor, timing, spec, build_face
        self.keys.append(key)
        return self._answers.pop(0) if self._answers else ()

    def move_in_frame(self, frame, refs, *, to_zone, reason, owner, actor):
        del frame, to_zone, reason, owner, actor
        self.moves.append(tuple(ref.card_id for ref in refs))
        return None


def test_ice_sword_a_round_without_an_answer_stops_the_loop_and_keeps_the_committed_round():
    """取消 / 无答案**只停循环**，不撤销已应用的轮次（票 16 的"每轮提交"）。

    第 1 轮答了牌（立即搬走 = 提交）、第 2 轮没有答案 ⇒ 循环收尾；已提交的第 1 轮留在
    弃牌堆里，也没有第三个缓存键。``Tag.PREVENTED`` 在入口置上（重复置入幂等）。
    """
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hand=(first, second))],
        turn_seat=0,
    )
    host = _IceSwordHost(state, [(first,), ()])
    frame = SimpleNamespace(data={"target": 1, "skill_owner": 0})

    assert catalog_module._ice_sword(host, frame) is None

    assert host.keys == ["ice_sword:0", "ice_sword:1"], "每轮一个独立缓存键"
    assert host.moves == [(first,)], "第 2 轮无答案只停循环：已提交的第 1 轮不回滚"
    assert frame.data["ice_sword_done"] == 1, "游标停在已提交的那个轮次上"
    assert frame.data["ice_sword_finished"] is True, "无答案 ⇒ 本帧收尾，不再问"
    assert has_tag(frame, Tag.PREVENTED), "入口置一次（重复置入是幂等的）"


def test_ice_sword_a_suspended_move_resumes_at_the_next_round_without_replaying_it():
    """``"stay"`` 透传 + 重入续跑：搬牌挂起时**不重放**已提交的轮，续跑从下一轮的键接上。

    宿主第 1 次 ``move_in_frame`` 返回 ``"stay"``（模拟内联挂起）。重入（同一个帧再调一次
    ``_ice_sword``）时，游标已在搬牌之前前进 ⇒ 直接问 ``ice_sword:1``（第 2 轮），已提交的
    那张不会被重放；两轮共用同一个帧标签袋。
    """
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(hand=(first, second))],
        turn_seat=0,
    )

    class _Suspending(_IceSwordHost):
        def move_in_frame(self, frame, refs, *, to_zone, reason, owner, actor):
            del frame, to_zone, reason, owner, actor
            self.moves.append(tuple(ref.card_id for ref in refs))
            return "stay" if len(self.moves) == 1 else None

    host = _Suspending(state, [(first,), (second,)])
    frame = SimpleNamespace(data={"target": 1, "skill_owner": 0})

    assert catalog_module._ice_sword(host, frame) == "stay", "搬牌挂起 ⇒ 让出控制权"
    assert host.keys == ["ice_sword:0"]
    assert host.moves == [(first,)]
    assert frame.data["ice_sword_done"] == 1
    assert "ice_sword_finished" not in frame.data, "挂起不是收尾"

    assert catalog_module._ice_sword(host, frame) is None, "续跑走完第 2 轮"
    assert host.keys == ["ice_sword:0", "ice_sword:1"], "续跑从下一轮的键接上"
    assert host.moves == [(first,), (second,)], "已提交的第 1 轮不重放"
    assert frame.data["ice_sword_finished"] is True
    assert has_tag(frame, Tag.PREVENTED)
    assert frame.data["ice_sword_finished"] is True


# ---------------------------------------------------------------------------
# 麒麟弓：造成伤害时可弃目标一张坐骑
# ---------------------------------------------------------------------------


def test_qilin_bow_discards_a_mount_of_the_damage_target():
    mount = JUYING
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(QILIN,)), SeatSpec(equip=(mount,))],
        deck=_deck_excluding(SLASH, QILIN, mount),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.qilin_bow")
    assert _moves_of(engine, mount) == [("equip", "discard", "DISCARD")]
    assert engine.state.player(1).hp == 3, "麒麟弓只弃坐骑，不防止伤害"


def test_qilin_bow_offers_only_the_targets_mounts():
    """选取面**只**铺目标的坐骑槽（票 10）：同区的武器 / 防具不进面，也不能被弃。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(QILIN,)),
            SeatSpec(equip=(QINGGANG, JUYING)),
        ],
        deck=_deck_excluding(SLASH, QILIN, QINGGANG, JUYING),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        if query.kind == "SELECT_CARDS":
            return _select_slot_of(query, JUYING) or _find(
                query, lambda action: isinstance(action, SelectCards)
            )
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    step = _cost_step_query(engine, actor=0)
    assert _offered_card_ids(step) == [JUYING], "面里只有坐骑（武器【青釭剑】不在其中）"
    assert _moves_of(engine, JUYING) == [("equip", "discard", "DISCARD")]
    assert _moves_of(engine, QINGGANG) == [], "同区的非坐骑一张都不动"
    assert QINGGANG in engine.state.player(1).equip


def test_declining_a_non_locked_activation_skips_the_hook():
    """答 ``Pass``（不发动）⇒ 技能不生效（`GLOSSARY.md` 的**发动**）。"""
    mount = JUYING
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(QILIN,)), SeatSpec(equip=(mount,))],
        deck=_deck_excluding(SLASH, QILIN, mount),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.qilin_bow")
    assert not _has_query(engine, "SELECT_CARDS"), "不发动 ⇒ 不构造弃坐骑的选取询问"
    assert mount in engine.state.player(1).equip
    assert engine.state.player(1).hp == 3, "不发动麒麟弓 ⇒ 照常受伤"


def test_the_slash_canceled_seam_only_fires_for_the_slash_owner():
    """【青龙偃月刀】只对**持有者自己**的【杀】被抵消时生效（票 12 §4）。"""
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hand=(dodge,), equip=(GREEN_DRAGON,)),
        ],
        deck=_deck_excluding(SLASH, dodge, GREEN_DRAGON),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade"), (
        "被抵消的是座位 0 的【杀】，座位 1 的青龙偃月刀不得被问"
    )
    slash_actions = [
        record
        for record in engine.records
        if record.kind == "action"
        and getattr(record.action, "kind", None) == "UseCard"
        and record.action.card.type == "slash"
    ]
    assert len(slash_actions) == 1, "没有续杀"
    assert engine.state.player(1).hp == 4, "闪抵消，未受伤"


def test_renwang_shield_blocks_a_transformed_black_slash():
    """两张黑牌的丈八蛇矛【杀】仍有黑色，被【仁王盾】挡（U-4 的裁定）。"""
    from sgs.data import color_of_suit

    blacks = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    ]
    state = build_state(
        pack=PACK, seats=[SeatSpec(equip=(RENWANG,)), SeatSpec()], turn_seat=0
    )
    card = transformed_slash(state, (blacks[0], blacks[1]))

    assert card.color is Color.BLACK
    assert target_is_effective(state, card, 1, 0) is False, "转化黑杀照常被仁王盾挡"


# ---------------------------------------------------------------------------
# 雌雄双股剑：SELECT_OPTION（选择权在目标）+ 性别判据
# ---------------------------------------------------------------------------


def test_double_sword_offers_the_choice_to_the_target_of_opposite_gender():
    other = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(other,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, other),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == "SELECT_OPTION":
            return _find(query, lambda action: isinstance(action, SelectOption) and action.choice == "draw_one")
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    option_query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_OPTION"
    )
    assert option_query.options == ("discard_one", "draw_one")
    assert option_query.actor == 1, "选择权在目标"
    assert any(
        record.kind == "action"
        and getattr(record.action, "kind", None) == "SelectOption"
        and record.action.choice == "draw_one"
        for record in engine.records
    )


def test_double_sword_hides_the_discard_option_when_the_target_has_no_hand_cards():
    """付不起的组合不进候选（票 19 §6）：目标无手牌 ⇒ 只剩"令你摸一张"。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    option_query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_OPTION"
    )
    assert option_query.options == ("draw_one",)
    assert not _has_query(engine, "SELECT_CARDS"), (
        "只剩「令其摸一张」这一支 ⇒ 没有「选一张手牌」的步骤（票 17 复现核对第 2 条）"
    )


def test_double_sword_does_not_trigger_for_same_gender_targets():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(gender=Gender.MALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD),
        turn_seat=0,
    )

    engine = _run(
        state,
        [
            Scripted(lambda q: _slash_use(q, SLASH, 1) if q.kind == "PLAY_PHASE" else None),
            Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert not _has_query(engine, "SELECT_OPTION")
    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.double_sword")


def test_double_sword_discards_the_chosen_card_to_the_discard_pile():
    """目标答"弃一张手牌" ⇒ 那张牌真进弃牌堆，且使用者**不**摸牌（票 17 复现核对第 3 条）。

    与上面那条"令其摸一张"的用例互斥：一次使用只冻结一份应答，故两条分支各一条用例。
    这条同时钉住"选中的牌立即搬走"——只断言"问了"会让"答了却什么都不发生"（票 13 的
    【流离】事故）照样通过。
    """
    other = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(other,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, other),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == "SELECT_OPTION":
            return _find(
                query,
                lambda action: isinstance(action, SelectOption)
                and action.choice == "discard_one",
            )
        if query.kind == "SELECT_CARDS":
            return _find(query, lambda action: isinstance(action, SelectCards))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    option_query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "SELECT_OPTION"
    )
    assert option_query.options == ("discard_one", "draw_one")
    assert option_query.actor == 1, "选择权在目标"
    assert _moves_of(engine, other) == [("hand", "discard", "DISCARD")], (
        "目标选中的那张手牌必须真的落进弃牌堆（答了却无事发生是票 13 的【流离】事故）"
    )
    assert not any(
        record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "draw"
        and record.to_zone.seat == 0
        and record.seq > option_query.seq
        for record in engine.records
    ), "目标是弃牌而不是让使用者摸牌；询问之后使用者这条链上不该有摸牌"


class _DoubleSwordHost:
    """【雌雄双股剑】逐目标循环的最小宿主（只实现 ``_double_sword`` 用到的那几张面）。

    多目标的【杀】在本引擎里只可能由【方天画戟】造出（与【雌雄双股剑】同槽、装不到一起），
    故"逐目标循环"无法经一次真实使用驱动；这里直接给出手写的 USE 帧数据（形状与引擎派发时
    写进帧的 ``source`` / ``targets`` 同源），把断言钉在**循环本身**上。
    """

    def __init__(self, state, choices: dict[int, str]) -> None:
        self.state = state
        self._choices = choices
        self.options: list[tuple[int, tuple[str, ...]]] = []
        self.selections: list[int] = []
        self.moves: list[tuple[int, ...]] = []
        self.draws: list[tuple[int, int]] = []

    @staticmethod
    def suspended(value):
        return value == "SUSPEND"

    def ask_option(self, frame, actor, timing, *, skill_id, options):
        del frame, timing, skill_id
        self.options.append((actor, tuple(options)))
        return SimpleNamespace(choice=self._choices[actor])

    def ask_selection(self, frame, *, owner, key, actor, timing, spec, build_face):
        del frame, owner, key, timing, spec, build_face
        self.selections.append(actor)
        return (self.state.player(actor).hand[0],)

    def move_in_frame(self, frame, refs, *, to_zone, reason, owner, actor):
        del frame, to_zone, reason, owner, actor
        self.moves.append(tuple(ref.card_id for ref in refs))
        return None

    def draw_for_hand(self, frame, seat, count):
        del frame
        self.draws.append((seat, count))


def test_double_sword_asks_each_opposite_gender_target_once():
    """【雌雄双股剑】逐目标推进：**只问异性**、每个目标恰好一次、已处理的不重复问（票 11）。

    目标表 = ``(1, 2, 3)``：座位 1 / 3 是女性、座位 2 是男性（与使用者同性）⇒ 座位 2 一个
    询问都不发；座位 1 答"弃一张"、座位 3 答"令使用者摸一张" ⇒ 各走各的提交动作。
    """
    first, same_gender, second = _cards("peach")[0], _cards("slash")[1], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(first,), gender=Gender.FEMALE),
            SeatSpec(hand=(same_gender,), gender=Gender.MALE),
            SeatSpec(hand=(second,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, first, same_gender, second),
        turn_seat=0,
    )
    host = _DoubleSwordHost(state, choices={1: "discard_one", 3: "draw_one"})
    frame = SimpleNamespace(data={"source": 0, "targets": (1, 2, 3)})

    assert catalog_module._double_sword(host, frame) is None

    assert [actor for actor, _ in host.options] == [1, 3], (
        "逐个异性目标、按目标序：同性的座位 2 一个询问都不发，每个目标只问一次"
    )
    assert host.options[0][1] == ("discard_one", "draw_one")
    assert host.selections == [1], "只有答'弃一张'的目标才进选牌步"
    assert host.moves == [(first,)], "选中的那张立即搬走（逐轮提交）"
    assert host.draws == [(0, 1)], "答'令使用者摸一张' ⇒ 使用者摸一张"
    assert frame.data["double_sword_cursor"] == 3, "游标推进到目标表末尾"

    # 重入（同一个帧再跑一遍）：已处理完的目标不再被问第二次（游标是唯一进度真源）。
    assert catalog_module._double_sword(host, frame) is None
    assert [actor for actor, _ in host.options] == [1, 3]


# ---------------------------------------------------------------------------
# 八卦阵：需要【闪】的提供者序列 + 惰性判定（帧内接缝）
# ---------------------------------------------------------------------------


def test_eight_diagrams_is_offered_once_in_the_dodge_window():
    state = build_state(
        pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(EIGHT_DIAGRAMS,))], turn_seat=0
    )

    offered = legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)
    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == "equip.eight_diagrams"
        for action in offered
    ), "提供者序列把八卦阵追加进需要【闪】的候选"

    used = legal_actions_for(
        state,
        1,
        QueryKind.DODGE,
        answers_seq=1,
        payload=DodgeContext(providers_used=("equip.eight_diagrams",)),
    )
    assert not any(isinstance(action, ActivateSkill) for action in used), (
        "每提供者只问一次：问过之后不再提供"
    )


def test_a_red_eight_diagrams_judgment_satisfies_the_dodge():
    red = _first_by_color("slash", black=False)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(equip=(EIGHT_DIAGRAMS,))],
        deck=_judge_deck(red, used=(SLASH, EIGHT_DIAGRAMS)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert engine.state.player(1).hp == 4, "红色判定满足需要 ⇒ 【杀】被抵消"
    assert any(
        record.kind == "timing" and record.timing is Timing.JUDGE_CARD_AFTER_EFFECT
        for record in engine.records
    ), "选中才惰性压判定帧"


def test_a_black_eight_diagrams_judgment_falls_through_to_the_real_dodge_window():
    black = _first_by_color("slash", black=True)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,)), SeatSpec(equip=(EIGHT_DIAGRAMS,))],
        deck=_judge_deck(black, used=(SLASH, EIGHT_DIAGRAMS)),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        return None

    dodges = {"count": 0}

    def defender(query):
        if query.kind == "DODGE":
            dodges["count"] += 1
            if dodges["count"] == 1:
                return _find(query, lambda action: isinstance(action, ActivateSkill))
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )

    assert dodges["count"] == 2, "黑色判定 ⇒ 该提供者机会用尽、重问真牌窗口"
    second = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "DODGE"
    ][1]
    assert not any(isinstance(action, ActivateSkill) for action in second.legal_actions)
    assert engine.state.player(1).hp == 3, "没闪 ⇒ 照常受伤"


# ---------------------------------------------------------------------------
# 青龙偃月刀 / 贯石斧：【杀】被抵消时的帧内接缝
# ---------------------------------------------------------------------------


def test_green_dragon_blade_uses_another_slash_on_the_same_target():
    second_slash = _cards("slash")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, second_slash), equip=(GREEN_DRAGON,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, second_slash, GREEN_DRAGON, dodge),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1) or _slash_use(query, second_slash, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    dodges = {"used": False}

    def defender(query):
        if query.kind == "DODGE" and not dodges["used"]:
            dodges["used"] = True
            return _find(query, lambda action: isinstance(action, UseCard))
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade")
    assert engine.state.player(1).hp == 3, "续杀无距离限制、命中造成 1 点伤害"
    assert engine.state.player(0).card_use_counts.get("slash", 0) == 1, (
        "续杀不计次数（票 12）：本回合【杀】的计数只算最初那张"
    )


def test_axe_discards_two_cards_to_keep_the_damage_after_a_dodge():
    extra = _cards("peach")[0]
    extra2 = _cards("peach")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra, extra2), equip=(AXE,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, extra, extra2, AXE, dodge),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            # 票 09：成本已不在发动候选里（恒一条裸骨架），两张牌改由发动后的 SELECT_CARDS 步问。
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.axe")
    assert engine.state.player(1).hp == 3, "弃两张后此【杀】依然造成伤害"
    discarded = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DISCARD"
        and record.from_zone.seat == 0
    ]
    assert sum(len(record.cards) for record in discarded) == 2, "成本恰是两张牌"
    assert AXE in engine.state.player(0).equip, "武器本身不能当成本"

    resolve_id = next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.RESOLVE
    )
    timings = [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.frame_id == resolve_id
    ]
    assert Timing.RESOLVE_EFFECT not in timings, "贯石斧跳『生效时』"
    assert Timing.RESOLVE_AFTER_EFFECT in timings, "直进『生效后』"


# ---------------------------------------------------------------------------
# 青龙偃月刀 / 贯石斧的**成本步**（票 09）：候选恒一条骨架，成本由流水线步骤问
#
# 迁移前：成本被**烘进发动询问的候选**（青龙 = 每张手牌【杀】一条；
# 贯石斧 = 手牌 ∪ 装备区的 C(n,2) 条两张组合）。迁移后发动询问回到裸"是否发动"，
# "挑哪张 / 弃哪两张"改由发动后的 ``SELECT_CARDS`` 步问 —— 形状与票 04【制衡】同源。
# ---------------------------------------------------------------------------


def _activation_actions(engine, skill_id: str):
    """记录流里 ``skill_id`` 那条 ``SKILL_ACTIVATION`` 询问的候选。"""
    for record in engine.records:
        if (
            record.kind == "query"
            and record.query.kind == "SKILL_ACTIVATION"
            and record.query.skill_id == skill_id
        ):
            return record.query.legal_actions
    raise AssertionError(f"记录流里没有 {skill_id} 的 SKILL_ACTIVATION 询问")


def _activation_offers(engine, skill_id: str) -> list[ActivateSkill]:
    """那条发动询问里**真候选**（不含 ``Pass``）的形状 —— 票 09 起恒一条裸骨架。"""
    return [
        action
        for action in _activation_actions(engine, skill_id)
        if isinstance(action, ActivateSkill)
    ]


def _cost_step_query(engine, *, actor: int = 0):
    """成本步的 ``SELECT_CARDS`` 询问（``actor`` = 发动者）。

    **不**按 ``query.skill_id`` 找：被动技的帧没有 ``skill_id``（派发环只写
    ``running_skill``），故该字段是 ``None``——文案由投影读帧的 ``running_skill`` 取
    （`projection._skill_of_query` 的第二档）。测试按"这一步是哪个询问"定位。
    """
    for record in engine.records:
        if (
            record.kind == "query"
            and record.query.kind == "SELECT_CARDS"
            and record.query.actor == actor
        ):
            return record.query
    raise AssertionError(f"记录流里没有 actor={actor} 的 SELECT_CARDS 询问")


def _offered_card_ids(query) -> list[int]:
    """选择面可见槽上的实体牌 id（隐藏槽是 ``None``）。"""
    return [slot.card.card_id for slot in query.slots if slot.card is not None]


def _slash_cost_driver(*, use: int, activation=None):
    """出牌阶段对座位 1 使用 ``use`` 的脚本；座位 1 用【闪】抵消（第一次）。"""

    def driver(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, use, 1)
        if query.kind == "SKILL_ACTIVATION" and activation is not None:
            return activation(query)
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    return driver


def test_green_dragon_offer_is_a_single_skeleton_candidate_however_many_slashes():
    """票 09：发动候选不再"每张手牌【杀】一条"，恒一条骨架 —— 手里 6 张【杀】也一样。"""
    slashes = tuple(_cards("slash")[:6])
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=slashes, equip=(GREEN_DRAGON,)), SeatSpec(hand=(dodge,))],
        deck=_deck_excluding(*slashes, GREEN_DRAGON, dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(_slash_cost_driver(use=slashes[0])), Scripted(None)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    offers = _activation_offers(engine, "equip.green_dragon_blade")
    assert len(offers) == 1, "每张【杀】一条候选的枚举消失：候选恒一条"
    assert offers[0].picks == () and offers[0].choices == (), "载荷为空，改由发动后的步骤问"

    step = _cost_step_query(engine)
    offered = _offered_card_ids(step)
    assert offered and all(card_id in slashes for card_id in offered), (
        "【青龙】的选取面只铺【杀】（手牌里的【桃】【万箭齐发】都不进面）"
    )
    assert step.spec.total == (1, 1), "这一步恰要一张；候选数是恒定的，不随【杀】张数增长"
    assert engine.state.player(1).hp == 3, "续杀照常造成伤害"


def test_axe_offer_is_a_single_skeleton_candidate_however_many_cards():
    """票 09：发动候选不再枚举 C(手牌 + 装备, 2)，恒一条骨架 —— 8 张牌也一样。"""
    others = _cards("peach")[:7]
    hand = (SLASH, *others)
    assert len(hand) == 8, "手牌 8 张 ⇒ 旧形状 C(8, 2) = 28 条两张组合"
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, equip=(AXE,)), SeatSpec(hand=(dodge,))],
        deck=_deck_excluding(*hand, AXE, dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(_slash_cost_driver(use=SLASH)), Scripted(None)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    offers = _activation_offers(engine, "equip.axe")
    assert len(offers) == 1, "两张组合的枚举消失：候选恒一条"
    assert offers[0].picks == () and offers[0].choices == (), "载荷为空，改由发动后的步骤问"

    step = _cost_step_query(engine)
    assert step.spec.total == (2, 2), "成本步恰要两张（手牌 ∪ 装备区）"
    assert len(step.slots) > 2, "选择面是此刻真铺的牌（上限由它解析出来）"
    assert engine.state.player(1).hp == 3, "弃两张后此【杀】依然造成伤害"


def test_green_dragon_declining_the_activation_asks_for_nothing_and_stays_put():
    """发动询问答 ``Pass`` ⇒ 零副作用，选牌步一个都不发（票 09 的取消出口）。"""
    second_slash = _cards("slash")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, second_slash), equip=(GREEN_DRAGON,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, second_slash, GREEN_DRAGON, dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [
            Scripted(
                _slash_cost_driver(
                    use=SLASH,
                    activation=lambda query: _find(query, lambda a: isinstance(a, Pass)),
                )
            ),
            Scripted(None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade")
    assert not _has_query(engine, "SELECT_CARDS"), "不发动 ⇒ 不构造续杀的选取询问"
    assert second_slash in engine.state.player(0).hand, "成本没动"


def test_axe_declining_the_activation_asks_for_nothing_and_stays_put():
    """发动询问答 ``Pass`` ⇒ 零副作用，选牌步一个都不发（票 09 的取消出口）。"""
    extra, extra2 = _cards("peach")[0], _cards("peach")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra, extra2), equip=(AXE,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, extra, extra2, AXE, dodge),
        turn_seat=0,
    )

    engine = _run(
        state,
        [
            Scripted(
                _slash_cost_driver(
                    use=SLASH,
                    activation=lambda query: _find(query, lambda a: isinstance(a, Pass)),
                )
            ),
            Scripted(None),
        ],
        lambda engine: _use_frame_popped(engine),
    )

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.axe")
    assert not _has_query(engine, "SELECT_CARDS"), "不发动 ⇒ 不构造弃两张的选取询问"
    assert AXE in engine.state.player(0).equip, "成本没动"
    assert engine.state.player(1).hp == 4, "没发动 ⇒ 闪抵消，未受伤"


def _dodge_driver(query):
    """座位 1 的应答脚本：闪窗口打出【闪】，其余不插手。"""
    if query.kind == "DODGE":
        return _find(query, lambda action: isinstance(action, UseCard))
    return None


def test_green_dragon_without_a_slash_in_hand_is_never_asked():
    """付不起（手里没有第二张【杀】）⇒【青龙偃月刀】**连发动询问都不发**（票 12 §6）。

    票 09 票面明写的覆盖缺口：``available=False`` 时 :func:`~sgs.skills.catalog._dispatch`
    直接 ``continue`` —— 既不进候选、也不构造 ``SKILL_ACTIVATION``。既有用例只覆盖
    "非持有者不问"与"发动询问答 ``Pass``"，没有覆盖判据本身为假这一档。

    牌堆前两张显式放【桃】：自己的回合会摸 2 张，不能让它们碰巧是【杀】——
    那样"手里没有第二张【杀】"这个前提就没了（判据读的是**摸牌后**的手牌）。
    """
    dodge = _cards("dodge")[0]
    fillers = tuple(_cards("peach")[:2])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH,), equip=(GREEN_DRAGON,)), SeatSpec(hand=(dodge,))],
        deck=(*fillers, *_deck_excluding(SLASH, GREEN_DRAGON, dodge, *fillers)),
        turn_seat=0,
    )

    engine = _run(
        state,
        [Scripted(_slash_cost_driver(use=SLASH)), Scripted(_dodge_driver)],
        lambda engine: _use_frame_popped(engine),
    )

    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.green_dragon_blade"), (
        "手里没有第二张【杀】⇒ 付不起 ⇒ 连发动询问都不发（票 09 的 available 判据）"
    )
    assert not _has_query(engine, "SELECT_CARDS"), "没有发动询问 ⇒ 更没有成本步询问"
    assert engine.state.player(1).hp == 4, "闪抵消，且没有续杀"
    assert GREEN_DRAGON in engine.state.player(0).equip


def test_axe_with_only_one_card_is_never_asked():
    """付不起（手牌 ∪ 装备区只有 1 张）⇒【贯石斧】**连发动询问都不发**（票 12 §6）。

    与【青龙】同一条判据：``_axe_plan`` 的 ``available`` 读与成本步**同源**的槽位数
    （``zone_pool_size ≥ 2``）。**为什么走【借刀杀人】而不走自己的出牌阶段**：正常回合的
    摸牌阶段会给斧主补 2 张手牌，接缝处池恒 ≥ 2（"付不起"在自己回合里造不出来）；
    借刀窗口里斧主用掉唯一一张【杀】后手牌为空、装备区只剩武器自己 ⇒ 池 = 1。
    先手必须钉在座位 1（借刀使用者）：否则引擎的 ``_first_seat`` 会把回合还给座位 0、
    先给它摸 2 张，"只有 1 张"的前提当场失效。
    """
    from sgs.state import WindowCause

    borrowed = _cards("borrowed_sword")[0]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(AXE,)),
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, AXE, borrowed, dodge),
        turn_seat=1,
        rule_config=RuleConfig(first_seat=1),
    )

    def _borrowed_user(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == borrowed
                and action.targets == (0,),
            )
        return None

    def _axe_owner(query):
        if getattr(query, "cause", None) is WindowCause.BORROWED_SWORD:
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.type == "slash"
                and action.targets == (2,),
            )
        return None

    engine = _run(
        state,
        [Scripted(_axe_owner), Scripted(_borrowed_user), Scripted(_dodge_driver)],
        lambda engine: SLASH in engine.state.discard,
    )

    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.axe"), (
        "手牌 ∪ 装备区不足两张 ⇒ 付不起 ⇒ 连发动询问都不发"
    )
    assert not _has_query(engine, "SELECT_CARDS"), "没有发动询问 ⇒ 更没有成本步询问"
    assert engine.state.player(2).hp == 4, "闪抵消，未受伤"
    assert AXE in engine.state.player(0).equip


def test_serpent_spear_transformed_play_ignores_the_slash_limit():
    """打出不占使用次数（票 19 §2）：次数用尽仍可从候选里打出转化【杀】。"""
    from sgs.state import RespondContext, RespondMode, WindowCause

    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 99  # 次数已用尽

    played = legal_actions_for(
        state,
        0,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BARBARIAN_INVASION, required="slash", mode=RespondMode.PLAY
        ),
    )
    used = legal_actions_for(
        state,
        0,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.BORROWED_SWORD,
            required="slash",
            mode=RespondMode.USE,
            use_targets=(1,),
        ),
    )

    assert any(
        isinstance(action, PlayCard) and action.card.card_id < 0 for action in played
    ), "打出不计次数 ⇒ 转化【杀】仍在候选中"
    assert not any(
        isinstance(action, UseCard) and action.card.card_id < 0 for action in used
    ), "使用仍受次数限制"


def test_eight_diagrams_is_offered_in_the_arrow_barrage_dodge_window():
    """票 10 §5.5：两个"需要【闪】"seam 共用同一套嵌法（``DODGE`` 与【万箭齐发】）。"""
    from sgs.state import RespondContext, RespondMode, WindowCause

    state = build_state(
        pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(EIGHT_DIAGRAMS,))], turn_seat=0
    )

    actions = legal_actions_for(
        state,
        1,
        QueryKind.RESPOND,
        answers_seq=1,
        payload=RespondContext(
            cause=WindowCause.ARROW_BARRAGE, required="dodge", mode=RespondMode.PLAY
        ),
    )

    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == "equip.eight_diagrams"
        for action in actions
    )


def test_a_cost_that_cannot_be_paid_produces_no_activation_candidate():
    """付不起 ⇒ 候选为空、不构造询问（票 19 §6 的候选生成契约）。"""
    from sgs.effects import legal_actions_for
    from sgs.state import SkillActivationContext

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    empty_face = SkillActivationContext(
        skill_id="equip.axe", optional=True, min_picks=2, max_picks=2, slots=()
    )

    actions = legal_actions_for(
        state, 0, QueryKind.SKILL_ACTIVATION, answers_seq=1, payload=empty_face
    )

    assert not any(isinstance(action, ActivateSkill) for action in actions)
    assert all(isinstance(action, Pass) for action in actions)


# ---------------------------------------------------------------------------
# 丈八蛇矛：转化牌 / 虚拟牌
# ---------------------------------------------------------------------------


def test_a_transformed_slash_is_virtual_has_no_rank_and_inherits_a_common_color():
    from sgs.data import color_of_suit

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    blacks = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.BLACK
    ]
    reds = [
        card_id
        for card_id in _cards("slash")
        if color_of_suit(PACK.deck.cards[card_id].suit) is Color.RED
    ]
    black_one, black_two = blacks[0], blacks[1]
    red_one = reds[0]

    same_color = transformed_slash(state, (black_one, black_two))
    mixed = transformed_slash(state, (black_one, red_one))

    assert same_color.is_virtual and same_color.card_id < 0
    assert same_color.rank is None and same_color.suit is None
    assert same_color.color is Color.BLACK, "同色 ⇒ 有颜色但无花色"
    assert same_color.virtual_sources == tuple(sorted((black_one, black_two)))
    assert mixed.color is None, "异色 ⇒ 无色"
    assert transformed_slash(state, (black_one, black_two)).card_id == same_color.card_id, (
        "同一组来源恒得同一个负 id（确定性）"
    )


def test_virtual_card_id_namespaces_do_not_collide():
    from sgs.skills.virtual import virtual_card_id, virtual_dodge

    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)

    assert virtual_dodge(state).card_id != transformed_slash(state, (0, 1)).card_id, (
        "纯虚拟牌与来源 (0,1) 的转化牌不得撞 id"
    )
    assert virtual_card_id(kind=0, sources=(0, 1), card_count=108) != virtual_card_id(
        kind=1, sources=(), card_count=108
    )


def test_serpent_spear_offers_a_transformed_slash_candidate():
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    virtual = [
        action
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id < 0
    ]

    assert virtual, "装备丈八蛇矛且有两张手牌 ⇒ 追加转化【杀】候选"
    assert virtual[0].card.virtual_sources == (first, second)


def test_serpent_spear_consumes_both_source_cards_and_stays_out_of_conservation():
    first, second = _cards("peach")[0], _cards("peach")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(first, second), equip=(SERPENT,)), SeatSpec()],
        deck=_deck_excluding(first, second, SERPENT),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            # W4-fix-2：转化候选已从 `legal_actions` 收回，按**清单**确认（服务端解析成
            # 那条普通 `UseCard(虚拟牌, 目标)`）。
            steps = query.transform_preactivations.get("equip.serpent_spear")
            if steps:
                target = steps[1].pool[0].options[0].removeprefix("seat:")
                return ActivateSkill(
                    skill_id="equip.serpent_spear",
                    picks=(first, second),
                    choices=(f"seat:{target}",),
                    answers_seq=query.seq,
                )
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(lambda q: _find(q, lambda a: isinstance(a, Pass)) if q.kind == "DODGE" else None)],
        lambda engine: _use_frame_popped(engine),
    )

    both = [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [first, second]
    ]
    assert both == [
        ("hand", "processing", "USE"),
        ("processing", "discard", "USE"),
    ], "一条 delta 带两张来源牌（票 12 §Q21）"
    assert engine.state.player(1).hp == 3

    everywhere = [
        *engine.state.deck,
        *engine.state.discard,
        *engine.state.processing,
        *(card_id for player in engine.state.players for card_id in player.hand),
        *(card_id for player in engine.state.players for card_id in player.equip),
        *(card_id for player in engine.state.players for card_id in player.judge),
    ]
    assert sorted(everywhere) == list(range(len(PACK.deck.cards))), (
        "虚拟牌不参与牌守恒：两张来源实体牌仍恰在一处"
    )


# ---------------------------------------------------------------------------
# 技能实例模型（票 07 §6）：键 / 去重 / 来源清理 / 虚拟装备并集
# ---------------------------------------------------------------------------


def test_skill_instances_are_keyed_by_owner_skill_and_source():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,)), SeatSpec(equip=(CROSSBOW,))],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert len(instances) == 2, "两个人各拿同一个技能 ⇒ 两个实例，互不串状态"
    keys = sorted(instance_key(instance) for instance in instances)
    assert keys == [
        f"0:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
        f"1:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
    ]


def test_a_virtual_grant_is_deduped_behind_the_earliest_surviving_source():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(CROSSBOW,), virtual_equipment=("equip.crossbow",)),
            SeatSpec(),
        ],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert [instance.owner for instance in instances] == [0]
    assert instances[0].source.card_id == CROSSBOW, "实体装备来源在虚拟授予之前 ⇒ 胜出"


def test_prune_skill_state_drops_state_whose_source_is_gone():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    state.skill_state["0:equip.crossbow:EQUIPMENT_CARD:0"] = 3

    prune_skill_state(state)

    assert state.skill_state == {}, "来源（装备）已不在 ⇒ per-instance 状态随 source 清理"


def test_is_equipped_reads_the_physical_equipment_zone():
    state = build_state(
        pack=PACK, seats=[SeatSpec(equip=(SERPENT,)), SeatSpec()], turn_seat=0
    )

    from sgs.state import SlotKind

    assert is_equipped(state, 0, SlotKind.WEAPON) is True
    assert is_equipped(state, 0, SlotKind.ARMOR) is False
    assert is_equipped(state, 1, SlotKind.WEAPON) is False


def test_virtual_equipment_unions_with_physical_equipment_without_occupying_a_zone(monkeypatch):
    """虚拟装备不占牌守恒、与实体装备并集决定"已装备"（票 07 §6 / 票 30）。

    M2 没有虚拟装备的生产者，故用 monkeypatch 装一个带 ``virtual_slot`` 的假技能，
    只验证**并集判定**与"不进装备区"两条结构事实。
    """
    from types import MappingProxyType

    from sgs.effects import CarrierKind
    from sgs.skills import catalog as catalog_module
    from sgs.skills.catalog import SkillDef
    from sgs.state import SlotKind

    fake = SkillDef(
        skill_id="test.virtual_armor",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        virtual_slot=SlotKind.ARMOR,
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, fake.skill_id: fake}),
    )
    state = build_state(
        pack=PACK, seats=[SeatSpec(virtual_equipment=(fake.skill_id,))], turn_seat=0
    )

    assert is_equipped(state, 0, SlotKind.ARMOR) is True, "虚拟授予也算已装备"
    assert is_equipped(state, 0, SlotKind.WEAPON) is False
    assert state.player(0).equip == [], "虚拟装备不是牌、不进装备区"
    assert all(
        instance.source.kind.value == "VIRTUAL_GRANT"
        for instance in resolve_instances(state)
    )


def test_every_equipment_card_has_a_skill_and_vice_versa():
    """``CATALOG`` 的两半：装备技能 = 17 张装备牌名，武将技能 = 票 03 起的 H 族。

    票 03 之前武将那半是空的，故这条断言当时写作"``CATALOG`` 恰等于装备集合"；
    现在改成按 ``carrier`` 分区（载体是技能定义上的字段），M3 的后续票再往武将那半
    加技能时这条**不会**跟着改。
    """
    from sgs.effects import CarrierKind
    from sgs.skills import EQUIPMENT_CARD_TYPE_IDS

    equipment = {
        skill_id
        for skill_id, definition in CATALOG.items()
        if definition.carrier is CarrierKind.EQUIPMENT
    }
    heroes = {
        skill_id
        for skill_id, definition in CATALOG.items()
        if definition.carrier is CarrierKind.HERO
    }

    assert equipment == {f"equip.{card_type}" for card_type in EQUIPMENT_CARD_TYPE_IDS}
    assert not equipment & heroes, "两类载体的技能 id 前缀各一套，不相交"
    assert set(CATALOG) == equipment | heroes
    assert {
        "hero.guanyu.wusheng",
        "hero.zhaoyun.longdan",
        "hero.daqiao.guose",
        "hero.huatuo.jijiu",
    } <= heroes, "票 03 的 H 族四条技能已在目录里"


def test_the_new_query_and_action_kinds_round_trip_through_the_journal(tmp_path):
    """v2 报文白名单：``SKILL_ACTIVATION`` / ``SELECT_OPTION`` 随询问与行动落盘、可反序列化。"""
    from sgs.engine import dump_journal, read_journal

    other = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), equip=(DOUBLE_SWORD,), gender=Gender.MALE),
            SeatSpec(hand=(other,), gender=Gender.FEMALE),
        ],
        deck=_deck_excluding(SLASH, DOUBLE_SWORD, other),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION":
            return _find(query, lambda action: isinstance(action, ActivateSkill))
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == "SELECT_OPTION":
            return _find(query, lambda action: isinstance(action, SelectOption))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: _use_frame_popped(engine),
    )
    text = engine.journal()
    path = tmp_path / "game.jsonl"
    path.write_text(text, encoding="utf-8")

    assert _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.double_sword")
    assert _has_query(engine, "SELECT_OPTION")
    assert dump_journal(read_journal(path)) == text


# ---------------------------------------------------------------------------
# W4-2：【青龙偃月刀】/【贯石斧】的预发动（清单 + 复合动作）
# ---------------------------------------------------------------------------


def _activation_query_of(engine, skill_id: str):
    return next(
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == skill_id
    )


def test_green_dragon_preactivation_costs_a_converted_slash_source():
    """清单的一步 = **一张【杀】**；转化来源（【龙胆】的【闪】）以**来源实体牌 id** 出现。

    W4-2 的口径：复合动作的 ``picks`` 是"代价牌 id"，而虚拟【杀】背后没有实体牌——界面
    点的是那张【闪】。故清单候选 / 校验器都用来源 id，效果体拿到的仍是虚拟【杀】。
    """
    dodge_cost = _cards("dodge")[0]
    defender_dodge = _cards("dodge")[1]
    green_dragon = _cards("green_dragon_blade")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, dodge_cost), equip=(green_dragon,), hero="zhaoyun"),
            SeatSpec(hand=(defender_dodge,)),
        ],
        deck=_deck_excluding(SLASH, dodge_cost, defender_dodge, green_dragon),
        turn_seat=0,
    )
    captured: dict[str, object] = {}

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if (
            query.kind == "SKILL_ACTIVATION"
            and query.skill_id == "equip.green_dragon_blade"
        ):
            captured["steps"] = tuple(query.preactivation)
            return ActivateSkill(
                skill_id="equip.green_dragon_blade",
                picks=(dodge_cost,),
                answers_seq=query.seq,
            )
        return None

    dodged = {"used": False}

    def defender(query):
        if query.kind == "DODGE" and not dodged["used"]:
            dodged["used"] = True
            return _find(query, lambda action: isinstance(action, UseCard))
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    steps = captured["steps"]
    assert [step.name for step in steps] == ["card"]
    assert steps[0].zones == (Zone.HAND, Zone.EQUIP), (
        "提供步的区域 = 手牌 ∪ 装备（W4-3 的区域默认；W4 评审处置 C.6 让 zones 与池同源）"
    )
    assert (steps[0].min, steps[0].max) == (1, 1)
    assert set(_step_options(steps[0])) == {str(dodge_cost)}, (
        "【龙胆】把【闪】转成【杀】⇒ 候选是**来源**那张【闪】，不是虚拟牌的负 id"
    )

    assert dodge_cost not in engine.state.player(0).hand, "来源牌真的离手（虚拟【杀】用它当来源）"
    assert engine.state.player(1).hp == 3, "续杀命中"


def _step_options(step) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(option for row in step.pool for option in row.options)
    )


def test_axe_preactivation_costs_exactly_two_cards_as_one_composite():
    """【贯石斧】：清单一步 = 两张牌（手牌 ∪ 装备区）；确认 = 一个复合动作。"""
    extra, extra2 = _cards("peach")[0], _cards("peach")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra, extra2), equip=(AXE,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, extra, extra2, AXE, dodge),
        turn_seat=0,
    )
    captured: dict[str, object] = {}

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _slash_use(query, SLASH, 1)
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == "equip.axe":
            captured["steps"] = tuple(query.preactivation)
            captured["hand"] = tuple(card.card_id for card in query.view.self_hand)
            return ActivateSkill(
                skill_id="equip.axe",
                picks=(extra, extra2),
                answers_seq=query.seq,
            )
        return None

    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    engine = _run(
        state,
        [Scripted(attacker), Scripted(defender)],
        lambda engine: engine.state.player(1).hp < 4,
    )

    steps = captured["steps"]
    assert [step.name for step in steps] == ["card"]
    assert steps[0].zones == (Zone.HAND, Zone.EQUIP)
    assert (steps[0].min, steps[0].max) == (2, 2)
    assert set(_step_options(steps[0])) == {str(card_id) for card_id in captured["hand"]}, (
        "同一局面下池 = **其余牌**（手牌；装备区只剩武器自己）；武器本身被排除"
    )
    assert str(AXE) not in _step_options(steps[0]), "武器本身不能当成本（按装备卡 id 排除）"

    assert engine.state.player(1).hp == 3, "弃两张后此【杀】依然造成伤害"
    assert AXE in engine.state.player(0).equip, "武器本身不能当成本"
    cost_moves = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DISCARD"
        and record.from_zone is not None
        and record.from_zone.seat == 0
    ]
    assert sorted(
        card.card_id for record in cost_moves for card in record.cards
    ) == sorted((extra, extra2)), "恰好两张手牌当成本"


def test_axe_cannot_pay_with_its_own_equipment_card():
    """【贯石斧】不能弃置**自身**（W4-fix-3 用户裁定，**偏离官方 FAQ**）。

    代价步的池按"这张技能对应的装备卡 id"排除武器本身（不写死牌名）；校验器读同一份
    池 ⇒ 把武器当成本提交会被拒，且零状态改动。
    """
    extra, extra2 = _cards("peach")[0], _cards("peach")[1]
    dodge = _cards("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra, extra2), equip=(AXE,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, extra, extra2, AXE, dodge),
        turn_seat=0,
    )
    def defender(query):
        if query.kind == "DODGE":
            return _find(query, lambda action: isinstance(action, UseCard))
        return None

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, SLASH, 1)
        return None

    engine = _run(
        state,
        [Scripted(driver), Scripted(defender)],
        lambda running: running.pending is not None
        and running.pending.kind == "SKILL_ACTIVATION"
        and running.pending.skill_id == "equip.axe",
    )
    query = engine.pending
    assert query is not None

    steps = tuple(query.preactivation)
    assert str(AXE) not in _step_options(steps[0]), "清单里也不铺武器自己"

    baseline = json.dumps(engine.state.model_dump(mode="json"), sort_keys=True)
    with pytest.raises(IllegalActionError) as caught:
        engine.submit(
            ActivateSkill(
                skill_id="equip.axe",
                picks=(AXE, extra),  # 武器本身当成本 ⇒ 不合法
                answers_seq=query.seq,
            )
        )

    assert "不在可选范围内" in caught.value.message
    assert json.dumps(engine.state.model_dump(mode="json"), sort_keys=True) == baseline, (
        "拒绝后零状态改动"
    )
    assert engine.pending is query, "询问原样还在"
    rejected = engine.records[-1]
    assert rejected.accepted is False and "预发动校验失败" in rejected.reason
    assert AXE in engine.state.player(0).equip, "拒绝后武器仍装备着"


def test_axe_with_only_its_own_card_plus_one_other_is_never_asked():
    """池排除自身后，"手牌 1 张 + 武器自己"= **付不起** ⇒ 连发动询问都不发。

    【借刀杀人】窗口里斧主用掉唯一一张【杀】后，手里还有 1 张牌、装备区只剩武器自己。
    旧口径（武器可弃自身）池 = 2 ⇒ 会发询问；W4-fix-3 起池 = 1（只有那张手牌）。
    """
    from sgs.state import WindowCause

    borrowed = _cards("borrowed_sword")[0]
    dodge = _cards("dodge")[0]
    extra = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH, extra), equip=(AXE,)),
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(dodge,)),
        ],
        deck=_deck_excluding(SLASH, AXE, borrowed, dodge, extra),
        turn_seat=1,
        rule_config=RuleConfig(first_seat=1),
    )

    def _borrowed_user(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == borrowed
                and action.targets == (0,),
            )
        return None

    def _axe_owner(query):
        if getattr(query, "cause", None) is WindowCause.BORROWED_SWORD:
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.type == "slash"
                and action.targets == (2,),
            )
        return None

    engine = _run(
        state,
        [Scripted(_axe_owner), Scripted(_borrowed_user), Scripted(_dodge_driver)],
        lambda engine: SLASH in engine.state.discard,
    )

    assert not _has_query(engine, "SKILL_ACTIVATION", skill_id="equip.axe"), (
        "排除自身后池 = 1（只剩那张手牌）⇒ 付不起 ⇒ 连发动询问都不发"
    )
    assert not _has_query(engine, "SELECT_CARDS"), "没有发动询问 ⇒ 更没有成本步询问"
    assert AXE in engine.state.player(0).equip
    assert extra in engine.state.player(0).hand


# ---------------------------------------------------------------------------
# W4-fix-3：求桃窗口的收口形状（真【桃】可直接点 / 急救只经技能入口）
# ---------------------------------------------------------------------------

JIJIU = "hero.huatuo.jijiu"


def _real_red(type_id: str) -> int:
    return _first_by_color(type_id, black=False)


def test_the_peach_window_offers_only_real_peaches_and_a_hand_peach_rescues():
    """求桃窗口（W4-fix-2 收口后）：候选面 = **真牌**的使用 + 放弃，转化【桃】只经【急救】清单。

    这条同时钉三件事：
    1. 真【桃】在候选里、可被"点手牌"选中（前端 ``PEACH`` 已进 ``HAND_PICK_KINDS``）；
    2. ``legal_actions`` 里**没有**转化【桃】（``virtual_sources`` 非空的使用）；
    3. 选项**不再重复**：同一张牌名 + 同一目标只有一条（旧形状是真桃 + 急救桃两条）。
    """
    real_peach = _real_red("peach")
    red_dodge = _real_red("dodge")
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(hand=(real_peach, red_dodge), hero="huatuo"),
        ],
        deck=_deck_excluding(SLASH, real_peach, red_dodge),
        turn_seat=0,
    )
    captured: dict[str, object] = {}

    def attacker(query):
        if query.kind == QueryKind.PLAY_PHASE:
            return _slash_use(query, SLASH, 1)
        return None

    def huatuo(query):
        """华佗：点手牌【桃】= 选中候选里那条真牌使用（不点急救的转化入口）。"""
        if query.kind != QueryKind.PEACH:
            return None
        captured["query"] = query
        return _find(
            query,
            lambda action: isinstance(action, UseCard)
            and action.card.card_id == real_peach
            and action.targets == (1,),
        )

    engine = _run(
        state,
        [Scripted(attacker), Scripted(None), Scripted(huatuo)],
        lambda running: running.state.player(1).hp == 1
        and real_peach in running.state.discard,
    )

    query = captured["query"]
    assert query.actor == 2, "求桃询问问到回合外的华佗"
    assert query.legal_actions, "候选面不为空"

    uses = [action for action in query.legal_actions if isinstance(action, UseCard)]
    assert [action.card.card_id for action in uses] == [real_peach], (
        "真【桃】是唯一的使用候选（转化【桃】不在候选面）"
    )
    assert all(action.card.type == "peach" and not action.card.is_virtual for action in uses)
    assert not any(
        action.card.is_virtual for action in query.legal_actions if getattr(action, "card", None)
    ), "候选里没有虚拟牌（急救只经技能入口）"
    assert any(isinstance(action, Pass) for action in query.legal_actions), "放弃仍在"

    labels = [
        (action.card.name, tuple(action.targets))
        for action in query.legal_actions
        if isinstance(action, (UseCard, PlayCard))
    ]
    assert len(labels) == len(set(labels)), f"选项不得重复：{labels}"
    assert labels == [("桃", (1,))], "选项 = 一条『使用【桃】→ 座位 1』"

    steps = query.transform_preactivations[JIJIU]
    assert [step.name for step in steps] == ["card"], "急救无目标步（濒死者由窗口固定）"
    assert set(_step_options(steps[0])) == {str(real_peach), str(red_dodge)}, (
        "急救的来源 = 两张红色牌；它只出现在技能清单里，不进候选面"
    )

    assert engine.state.player(1).hp == 1, "真【桃】把濒死者救回 1 点"
    assert real_peach in engine.state.discard, "用掉的是手牌里的那张真【桃】"
