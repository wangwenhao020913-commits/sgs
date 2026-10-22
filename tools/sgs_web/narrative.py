"""叙事契约（U1-05）：把**观察者能读懂的行动**从全知记录流里挑出来。

事件流此前是"引擎日志"（``query RESPOND`` / ``结算帧 push`` / ``#seq``），玩家读不懂。
本模块把记录流翻成**一条行动一行**的简易叙事：谁对谁做了什么、看见哪些牌，全部由
服务端判定；前端只按 ``segments[].cls`` 上色，**不做任何隐藏信息判断**。

边界与纪律：

- **只读**记录流，不读引擎状态、不写状态、不进录像。牌桌（``serve.GameRegistry.view``）
  与回放（``replay.load_run``）消费**同一个生成器**；输入既可以是引擎记录模型，也可以
  是录像里已序列化的 JSON 记录（同一份 canonical 形状）。
- **观察者过滤**：``viewer`` = 查看者座位；``None`` = 上帝视角（U2 才接入口）。
  牌堆 / 手牌这类隐藏区的牌只写张数，公开区（弃牌堆 / 处理区 / 装备区 / 判定区）才写牌名；
  身份只写在记录流里**明确亮明**的那一次（阵亡亮明）；主公的默认明置由顶栏配比 / 座位
  牌面承担，不在叙事里重复。
- **展示契约同源**：卡名 / 属性都走 :func:`sgs_web.display.card_display`（火杀 / 雷杀显示
  「杀」并按火焰 / 雷电分色）；技能名来自武将面数据与装备牌名，不在 JS 里另抄一份。
- **不编事实**：记录流里读不出的事件不写；被拒的应答、未公开的身份 / 牌面一条都不出现。

条目形状（稳定契约）::

    {"kind": "line", "seq": 119, "text": "座位1 司马懿 受到 1 点伤害",
     "segments": [{"text": "座位1", "cls": "seat"},
                  {"text": " 司马懿", "cls": "hero"},
                  {"text": " 受到 ", "cls": "act"},
                  {"text": "1", "cls": "dmg"},
                  {"text": " 点伤害", "cls": "act"}]}
    {"kind": "divider", "seq": 13, "text": "第 1 轮 · 座位0 郭嘉 的回合"}

``text`` 是 ``segments`` 的拼接（同一次构造里生成），供测试 / 无障碍读；**颜色只认
``segments[].cls``**。``seq`` 是产生这条叙事的记录序号——增量下发与回放定位都按它。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from typing import Any, Final

from sgs.data import IDENTITY_LABELS, hero_skill_id, load_content_pack, load_heroes
from sgs.engine import record_json
from sgs.state import CardRef

from .display import card_display

__all__ = ["NARRATIVE_SEGMENT_CLASSES", "NO_VIEWER", "build_narrative", "narrative_after"]

NO_VIEWER: Final[int] = -1
"""没有人类座位的**观察者**（全是 AI 的录像）：公开区照写，隐藏区只给张数。

它不是"全知"：全知是 ``viewer=None``，只留给 U2 的上帝视角开关。U1 的回放页在没有
人类座位的录像上用它，避免"查不到座位"退化成把对手手牌 / 牌堆顺序写进叙事。
"""

NARRATIVE_SEGMENT_CLASSES: Final[tuple[str, ...]] = (
    "seat",
    "hero",
    "card",
    "card-fire",
    "card-thunder",
    "skill",
    "act",
    "dmg",
    "dmg-fire",
    "dmg-thunder",
)
"""``segments[].cls`` 的闭集；CSS 的 ``.nar-*`` 类名与它逐字对应（跨线契约）。

这个闭集在**四处**各有一份副本：本常量、``tests/test_web.py`` 的契约断言、
``tests/support/ui_smoke.mjs`` 的 ``colorClasses``、``static/style.css`` 的 ``.nar-*``。
新增一种颜色要四处同批改；任何一处单独漂移都不会被编译期拦住。
"""

PUBLIC_ZONES: Final[frozenset[str]] = frozenset(
    {"discard", "processing", "equip", "judge"}
)
"""牌面一旦进入这些区域就是公开事实；手牌 / 牌堆仍是隐藏区。"""

_SIDE_LABELS: Final[dict[str, str]] = {
    "lord": "主公方",
    "rebel": "反贼方",
    "renegade": "内奸方",
}

_PHASE_BY_TIMING: Final[dict[str, str]] = {
    "PHASE_PREPARE": "准备阶段",
    "PHASE_JUDGE": "判定阶段",
    "PHASE_DRAW": "摸牌阶段",
    "PHASE_PLAY_START": "出牌阶段",
    "PHASE_DISCARD_START": "弃牌阶段",
    "PHASE_FINISH": "结束阶段",
}

_IDENTITY_BY_VALUE: Final[dict[str, str]] = {
    getattr(identity, "value", str(identity)): label
    for identity, label in IDENTITY_LABELS.items()
}


def build_narrative(
    records: Iterable[object],
    *,
    viewer: int | None,
    heroes: Sequence[str | None] = (),
    pack_id: str,
) -> list[dict[str, Any]]:
    """把一局记录流翻成**观察者视角**的叙事条目（按 ``seq`` 升序）。

    ``viewer`` = 查看者座位（牌桌 = 人类座位；回放 = 录像的人类座位）；
    :data:`NO_VIEWER` = 没有人类座位的观察者（只写公开区，隐藏区只给张数）；
    ``None`` = 全知（U2 的上帝视角；U1 没有入口）。
    ``heroes`` = 逐座位武将 id（``Session.hero_assignments`` / 录像 meta），只用于把座位
    换成武将中文名；空序列时只写座位号。``pack_id`` 决定武将名 / 装备技能名 / 牌名的数据源。
    """
    hero_table, skill_names = _name_tables(pack_id)
    seat_names = {
        seat: name
        for seat, hero_id in enumerate(heroes)
        if hero_id is not None and (name := hero_table.get(str(hero_id))) is not None
    }
    state = _Narrator(
        viewer=viewer,
        seat_names=seat_names,
        skill_names=skill_names,
    )
    for raw in records:
        record = _payload(raw)
        if record.get("kind") == "header":
            continue
        state.absorb(record)
    return state.entries


def narrative_after(
    records: Iterable[object],
    *,
    after: int,
    viewer: int | None,
    heroes: Sequence[str | None] = (),
    pack_id: str,
) -> list[dict[str, Any]]:
    """``seq > after`` 的叙事条目（HTTP 增量游标与记录流共用同一个 ``seq``）。

    重新生成再过滤，而不是在服务端维护第二份游标：记录流本身就是单调的，生成器是纯函数。
    分隔线的 ``seq`` 取**它所引出的那一行**，故跨批次时不会丢上下文。
    """
    return [
        entry
        for entry in build_narrative(
            records, viewer=viewer, heroes=heroes, pack_id=pack_id
        )
        if entry["seq"] > after
    ]


# ---------------------------------------------------------------------------
# 记录流 → 条目
# ---------------------------------------------------------------------------


class _Narrator:
    """一次生成过程中的**只读派生状态**（轮次 / 阶段 / 待判定的伤害）。

    这里只记"读记录流时需要记住的上下文"，不复制任何引擎事实：轮次由 ``TURN_START``
    的座位重复累计（与顶栏 ``roundNumber`` 同口径），阶段由 ``PHASE_*`` 时机给出。
    """

    def __init__(
        self,
        *,
        viewer: int | None,
        seat_names: Mapping[int, str],
        skill_names: Mapping[str, str],
    ) -> None:
        self.viewer = viewer
        self.seat_names = seat_names
        self.skill_names = skill_names
        self.entries: list[dict[str, Any]] = []
        self.started = False
        self.round = 0
        self.seen_turn_seats: set[int] = set()
        self.phase: str | None = None
        self.phase_dirty = False
        self.pending_damage: tuple[int, str] | None = None
        self.damage_class = "dmg"
        self.last_query_actor: int | None = None
        # M5-08a：【火攻】亮牌（`card_reveal`）按**帧**记「亮出后有没有弃牌 / 放弃」。
        # 引擎在"无同花色"时根本不发弃牌询问（自动放弃），故这条只能从记录流派生：
        # 同一帧里出现 Pass 动作 = 显式放弃；出现手牌 → 弃牌堆的移动 = 弃置；
        # 两者都没有 ⇒ 自动放弃（无可弃的同花色牌）。纯展示派生，不写状态。
        self.reveal_frames: dict[int, dict[str, bool]] = {}

    # -- 单条记录 -----------------------------------------------------------

    def absorb(self, record: dict[str, Any]) -> None:
        seq = record.get("seq")
        if not isinstance(seq, int):
            return
        kind = record.get("kind")
        if kind == "query":
            query = record.get("query") or {}
            actor = query.get("actor")
            self.last_query_actor = int(actor) if isinstance(actor, int) else None
        elif kind == "timing":
            self._timing(record, seq)
        elif kind == "delta":
            self._delta(record, seq)
        elif kind == "action":
            self._action(record, seq)
        elif kind == "game_end":
            self._game_end(record, seq)

    # -- 时机 ---------------------------------------------------------------

    def _timing(self, record: dict[str, Any], seq: int) -> None:
        timing = str(record.get("timing") or "")
        if timing == "TURN_START":
            self._turn_start(record, seq)
            return
        if not self.started:
            return
        phase = _PHASE_BY_TIMING.get(timing)
        if phase is not None:
            self.phase = phase
            self.phase_dirty = True
            return
        if timing == "DAMAGE_DEAL":
            self.damage_class = _damage_class(record.get("cards") or ())
            return
        if timing == "DAMAGE_TAKE":
            actor = record.get("actor")
            if actor is None:
                return
            cards = record.get("cards") or ()
            self.pending_damage = (
                int(actor),
                _damage_class(cards) if cards else self.damage_class,
            )
            return
        if timing == "JUDGE_ON":
            cards = record.get("cards") or ()
            if cards:
                self._line(
                    seq,
                    [
                        *self._seat(record.get("actor")),
                        _act(" 判定"),
                        *_cards(cards),
                    ],
                )
            return
        if timing == "NEAR_DEATH_ENTER":
            self._line(seq, [*self._seat(record.get("actor")), _act(" 濒死")])
            return
        if timing == "DEATH_ON":
            self._line(seq, [*self._seat(record.get("actor")), _act(" 阵亡")])
            return
        if timing == "RESOLVE_END":
            self._fire_attack_end(record, seq)
            return
        if timing in ("DAMAGE_END", "TURN_END"):
            self.pending_damage = None

    def _turn_start(self, record: dict[str, Any], seq: int) -> None:
        seat = record.get("actor")
        if seat is None:
            return
        seat = int(seat)
        # 与 `static/table.js:roundNumber` 同一口径：同一座位再次先手 = 进入下一轮。
        if not self.started:
            self.round = 1
        elif seat in self.seen_turn_seats:
            self.round += 1
            self.seen_turn_seats.clear()
        self.seen_turn_seats.add(seat)
        self.started = True
        self.pending_damage = None
        self.phase = None
        self.phase_dirty = False
        turn = f"座位{seat}"
        if hero := self.seat_names.get(seat):
            turn += f" {hero}"
        self.entries.append(_divider(seq, f"第 {self.round} 轮 · {turn} 的回合"))

    # -- 增量 ---------------------------------------------------------------

    def _delta(self, record: dict[str, Any], seq: int) -> None:
        delta_type = record.get("delta_type")
        if delta_type == "hp_change":
            self._hp_change(record, seq)
        elif delta_type == "card_move":
            self._card_move(record, seq)
        elif delta_type == "card_reveal":
            self._card_reveal(record, seq)
        elif delta_type == "identity_reveal":
            label = _IDENTITY_BY_VALUE.get(str(record.get("identity")))
            if label is None:
                return
            self._line(
                seq,
                [
                    *self._seat(record.get("seat")),
                    _act(" 亮明身份："),
                    _act(label),
                ],
            )

    def _card_reveal(self, record: dict[str, Any], seq: int) -> None:
        """一张手牌成为公开事实（M5-06 的【火攻】亮牌）：写一行，并记住它所在帧。"""
        if not self.started:
            return
        seat = record.get("seat")
        card = record.get("card")
        if seat is None or not isinstance(card, dict):
            return
        self._line(seq, [*self._seat(seat), _act(" 亮出"), *_cards((card,))])
        frame_id = record.get("frame_id")
        if isinstance(frame_id, int):
            self.reveal_frames[frame_id] = {"passed": False, "discarded": False}

    def _mark_reveal_action(self, kind: str) -> None:
        """亮牌之后、结算结束之前的 ``Pass`` ⇒ 显式放弃（有同花色也可以放弃）。"""
        if kind != "Pass":
            return
        for state in self.reveal_frames.values():
            state["passed"] = True

    def _fire_attack_end(self, record: dict[str, Any], seq: int) -> None:
        """【火攻】结算结束：把「亮出之后发生了什么」补成一行。

        - 弃了一张同花色牌 ⇒ 弃置行已经由 ``card_move`` 写过，这里不再补；
        - 显式放弃 ⇒ 写「放弃」；
        - 两者都没有 ⇒ 引擎没发弃牌询问（没有同花色）⇒「放弃（无可弃的同花色牌）」。
        """
        frame_id = record.get("frame_id")
        state = self.reveal_frames.pop(frame_id, None) if isinstance(frame_id, int) else None
        if state is None or state["discarded"]:
            return
        source = record.get("source")
        if state["passed"]:
            self._line(seq, [*self._subject(source), _act(" 放弃")])
        else:
            self._line(
                seq,
                [*self._subject(source), _act(" 放弃（无可弃的同花色牌）")],
            )

    def _hp_change(self, record: dict[str, Any], seq: int) -> None:
        if not self.started:
            return
        seat = record.get("seat")
        if seat is None:
            return
        seat = int(seat)
        amount = int(record.get("amount", 0))
        if amount == 0:
            return
        pending = self.pending_damage
        self.pending_damage = None
        if amount > 0:
            self._line(
                seq,
                [
                    *self._seat(seat),
                    _act(" 回复 "),
                    _number(amount, "dmg"),
                    _act(" 点体力"),
                ],
            )
            return
        if pending is not None and pending[0] == seat:
            self._line(
                seq,
                [
                    *self._seat(seat),
                    _act(" 受到 "),
                    _number(-amount, pending[1]),
                    _act(" 点伤害"),
                ],
            )
            return
        self._line(
            seq,
            [
                *self._seat(seat),
                _act(" 失去 "),
                _number(-amount, "dmg"),
                _act(" 点体力"),
            ],
        )

    def _card_move(self, record: dict[str, Any], seq: int) -> None:
        if not self.started:
            return  # 开局发牌不进叙事：牌桌从第一个回合开始讲
        cards = record.get("cards") or ()
        if not cards:
            return
        to_zone = record.get("to_zone") or {}
        to = to_zone.get("zone")
        frame_id = record.get("frame_id")
        if (
            to == "discard"
            and isinstance(frame_id, int)
            and frame_id in self.reveal_frames
        ):
            self.reveal_frames[frame_id]["discarded"] = True
        if to == "hand":
            self._gain(record, seq, cards)
            return
        if to == "discard":
            # 使用 / 判定牌离开处理区已有各自的行；其余弃置（弃牌阶段、拆牌、死亡清场）
            # 是公开事实，牌名可以写。
            if record.get("reason") in ("USE", "JUDGE"):
                return
            self._discard(record, seq, cards)
            return
        # 进处理区 / 装备区 / 判定区 / 牌堆：使用牌那一条 action 已讲，牌堆顺序不公开。

    def _gain(
        self, record: dict[str, Any], seq: int, cards: Sequence[Mapping[str, Any]]
    ) -> None:
        to_zone = record.get("to_zone") or {}
        seat = to_zone.get("seat")
        if seat is None:
            return
        segments = [*self._subject(seat), _act(" 获得")]
        if _cards_visible(record, self.viewer):
            segments.extend(_cards(cards))
        else:
            segments.append(_act(f" {len(cards)} 张牌"))
        self._line(seq, segments)

    def _discard(
        self, record: dict[str, Any], seq: int, cards: Sequence[Mapping[str, Any]]
    ) -> None:
        from_zone = record.get("from_zone") or {}
        owner = record.get("owner")
        origin = from_zone.get("seat")
        if owner is None:
            owner = origin
        if owner is None:
            self._line(seq, [_act("弃置"), *_cards(cards)])
            return
        if origin is not None and int(origin) != int(owner):
            # 过河拆桥这类：牌从别人的区域离开，写成"武将 的【牌】被弃置"。
            segments = [
                *self._subject(origin),
                _act(" 的"),
                *_cards(cards),
                _act("被弃置"),
            ]
        else:
            segments = [*self._subject(owner), _act(" 弃置"), *_cards(cards)]
        self._line(seq, segments)

    # -- 应答 ---------------------------------------------------------------

    def _action(self, record: dict[str, Any], seq: int) -> None:
        if not record.get("accepted"):
            return
        action = record.get("action") or {}
        kind = action.get("kind")
        actor = self.last_query_actor
        if actor is None:
            return
        self._mark_reveal_action(str(kind))
        if kind == "ActivateSkill":
            name = self._skill_name(str(action.get("skill_id") or ""))
            self._line(
                seq,
                [*self._subject(actor), _act(" 发动"), _span(f"【{name}】", "skill")],
            )
            return
        card = action.get("card")
        if not isinstance(card, dict):
            return
        if kind == "UseCard":
            segments = [*self._subject(actor), _act(" 使用"), *_cards((card,))]
            targets = tuple(int(target) for target in action.get("targets") or ())
            if targets and targets != (actor,):
                segments.append(_act(" → "))
                for index, target in enumerate(targets):
                    if index:
                        segments.append(_act("、"))
                    segments.append(_span(f"座位{target}", "seat"))
            self._line(seq, segments)
        elif kind == "PlayCard":
            self._line(seq, [*self._subject(actor), _act(" 打出"), *_cards((card,))])

    # -- 终局 ---------------------------------------------------------------

    def _game_end(self, record: dict[str, Any], seq: int) -> None:
        result = record.get("result")
        side = record.get("side")
        winners = tuple(int(seat) for seat in record.get("winners") or ())
        segments: list[dict[str, Any]] = [_act("对局结束")]
        if result == "draw":
            segments.append(_act(" · 平局"))
        else:
            label = _SIDE_LABELS.get(str(side)) if side is not None else None
            if label is not None:
                segments.append(_act(f" · {label}获胜"))
        if winners:
            segments.append(_act(" · 获胜座位："))
            for index, seat in enumerate(winners):
                if index:
                    segments.append(_act("、"))
                segments.append(_span(f"座位{seat}", "seat"))
        self._line(seq, segments)

    # -- 小工具 -------------------------------------------------------------

    def _seat(self, seat: object) -> list[dict[str, str]]:
        if seat is None:
            return []
        index = int(seat)
        segments = [_span(f"座位{index}", "seat")]
        if name := self.seat_names.get(index):
            segments.append(_span(f" {name}", "hero"))
        return segments

    def _subject(self, seat: object) -> list[dict[str, str]]:
        """行动者：有武将名时只写武将名（票面示例 ``郭嘉 发动【遗计】``），没有才写座位号。

        受动的一侧（受伤 / 阵亡 / 判定）仍用 :meth:`_seat` 写"座位N 武将"，让"谁在场上的
        哪个位置"一眼可见。
        """
        if seat is None:
            return []
        index = int(seat)
        if name := self.seat_names.get(index):
            return [_span(name, "hero")]
        return [_span(f"座位{index}", "seat")]

    def _skill_name(self, skill_id: str) -> str:
        base = skill_id.split(":", 1)[0]
        if name := self.skill_names.get(base):
            return name
        return base.rsplit(".", 1)[-1] or "技能"

    def _line(self, seq: int, segments: list[dict[str, str]]) -> None:
        if self.phase_dirty and self.phase is not None:
            self.entries.append(_divider(seq, self.phase))
            self.phase_dirty = False
        self.entries.append(_line(seq, segments))


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _payload(record: object) -> dict[str, Any]:
    """记录模型 / 已序列化 JSON → 同一种 dict（生成器只认这一种形状）。"""
    if isinstance(record, Mapping):
        return dict(record)
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return json.loads(record_json(record))


def _span(text: str, cls: str) -> dict[str, str]:
    return {"text": text, "cls": cls}


def _act(text: str) -> dict[str, str]:
    return _span(text, "act")


def _number(amount: int, cls: str) -> dict[str, str]:
    return _span(str(amount), cls)


def _display(card: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """一张记录里的牌 → 展示契约；坏牌面返回 ``None``（调用方显式占位，不猜牌名）。"""
    try:
        return card_display(CardRef.model_validate(dict(card)))
    except (ValueError, TypeError):
        return None


def _cards(cards: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """牌名分段：火杀 / 雷杀显示「杀」并按展示契约的 ``attribute`` 分色。"""
    segments: list[dict[str, str]] = []
    for index, card in enumerate(cards):
        if index:
            segments.append(_act("、"))
        display = _display(card)
        if display is None:
            segments.append(_span("【？】", "card"))
            continue
        segments.append(_span(f"【{display['display_name']}】", _card_class(display)))
    return segments


def _card_class(display: Mapping[str, Any]) -> str:
    return {"火焰": "card-fire", "雷电": "card-thunder"}.get(
        str(display.get("attribute")), "card"
    )


def _damage_class(cards: Sequence[Mapping[str, Any]]) -> str:
    """伤害数字的颜色：火伤红 / 雷伤蓝；读的是伤害来源牌的展示属性。"""
    for card in cards:
        display = _display(card)
        if display is None:
            continue
        if display.get("attribute") == "火焰":
            return "dmg-fire"
        if display.get("attribute") == "雷电":
            return "dmg-thunder"
    return "dmg"


def _cards_visible(record: Mapping[str, Any], viewer: int | None) -> bool:
    """这条牌移动的**牌面身份**对查看者是否公开。

    公开区（弃牌堆 / 处理区 / 装备区 / 判定区）里出现过的牌是公开事实；只进出隐藏区
    （牌堆 / 手牌）的牌只给张数。查看者本人的手牌与他经手转交的牌仍可见——那不是泄露。
    """
    if viewer is None:
        return True
    to_zone = record.get("to_zone") or {}
    from_zone = record.get("from_zone") or {}
    to = to_zone.get("zone")
    source = from_zone.get("zone")
    if to in PUBLIC_ZONES or source in PUBLIC_ZONES:
        return True
    if to == "hand" and to_zone.get("seat") == viewer:
        return True
    if source == "hand" and from_zone.get("seat") == viewer:
        return True
    return False


def _divider(seq: int, text: str) -> dict[str, Any]:
    return {"kind": "divider", "seq": seq, "text": text}


def _line(seq: int, segments: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "kind": "line",
        "seq": seq,
        "text": "".join(segment["text"] for segment in segments),
        "segments": segments,
    }


@lru_cache(maxsize=8)
def _name_tables(pack_id: str) -> tuple[dict[str, str], dict[str, str]]:
    """``pack_id`` → （武将 id → 中文名，技能 id → 中文名）。

    数据文件是只读进料；同一进程里反复生成叙事时不必每次重读 / 重校验。装备技能的
    中文名取它所依附的**装备牌名**（与 ``build_skills`` 同一条派生）。
    """
    heroes = load_heroes(pack_id)
    hero_names = {hero.id: hero.name for hero in heroes.heroes}
    skill_names: dict[str, str] = {}
    for hero in heroes.heroes:
        for skill in hero.skills:
            skill_names[hero_skill_id(hero.id, skill.id)] = skill.name
    for card_type in load_content_pack(pack_id).card_types:
        skill_names[f"equip.{card_type.id}"] = card_type.name
    return hero_names, skill_names
