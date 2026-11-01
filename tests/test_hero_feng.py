"""风包：界曹仁（移动版）【据守】、黄忠【烈弓】。

牌面（`D:\\SGS_HEROS\\神话再临-风包\\曹仁\\技能.txt`，用户录入）：

    据守：结束阶段，你可以摸三张牌，然后翻面。

三条验收（全走外部缝：询问 / 记录流 / 终局状态）：

1. **自己的结束阶段**问一次；发动 ⇒ 摸三张 + 武将牌翻到背面（各一条 ``card_move`` /
   ``flipped_change``）；
2. **不发动** ⇒ 一张不摸、状态不变；
3. **翻面是 toggle**：已经背面朝上时发动 ⇒ 翻回正面（不是"再置为背面"）。

翻面本身（跳过回合 / 不清理"直到你的下个回合开始"类作用域）在 ``tests/test_turn_loop.py``。

黄忠【烈弓】（票 03；牌面 `D:\\SGS_HEROS\\神话再临-风包\\黄忠\\技能.txt`）落在文件后半：
与【铁骑】同形的逐目标帧标签 ``Tag.UNRESPONSIVE``——被封印的目标连【闪】窗口都不出现。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:  # sgs_web 住在 tools/（与 test_web.py 同一条引导）
    sys.path.insert(0, str(_TOOLS))

from sgs.agents import HeuristicPlayer
from sgs.agents import heuristic as heuristic_scoring
from sgs.data import Identity, RuleConfig, load_content_pack, load_hero_tables
from sgs.effects import check_targets, distance, legal_actions_for
from sgs.engine import Engine, ai_stream
from sgs.skills.virtual import transformed_card
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    Phase,
    PlayCard,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
    Zone,
    project,
)
from sgs.timing import Timing
from sgs_web.display import seat_display

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
    run_queries,
    run_until,
)


# ---------------------------------------------------------------------------
# 风包·移动版：于吉移动版【蛊惑】（用户 2026-11-01 读法裁定）
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\于吉\\技能.txt`，逐字）：
#   你可以扣置一张手牌，将此牌当任意一张基本牌或普通锦囊牌使用或打出。若有其他角色质疑
#   则翻开此牌且此牌作废，若为：假，质疑者各摸一张牌；真，质疑者各失去1点体力，然后若
#   被质疑的牌的花色为♥️，此牌依旧进行结算。
#
# 用户裁定：两个入口（出牌阶段主动 / 需要牌时询问）；扣置算失去牌；质疑者 = 其他所有存活
# 角色，**从下家起逆时针**，**收齐答复后**再按同一顺序执行结果；无次数限制；
# 作废 = 牌从**效果区**进弃牌堆（扣置时已离开手牌）。
# ---------------------------------------------------------------------------

GUOHUO = "hero.yuji_mobile.guohuo"


def _manifest_row(step, prior: tuple[str, ...]):
    """命中**前序答案**的那一行子池（与前端 `preactivationPoolRow` 同一套找法）。

    先找精确匹配的 ``when``，其次 ``when=()`` 那一行（服务端把"与前序无关"的行收成它）。
    ``None`` = 本步没有候选索引（退化）。
    """
    for row in getattr(step, "pool", ()) or ():
        if tuple(row.when) == prior:
            return row
    for row in getattr(step, "pool", ()) or ():
        if not row.when:
            return row
    return None


def _guohuo_activation(query, *, declare: str = ""):
    """出牌阶段入口的**复合动作**：从预发动清单填两步（选牌 → 牌名）。

    用户 2026-11-02 的 Q3A 裁定之后，出牌阶段入口是**预发动**：骨架候选不再进
    ``legal_actions``，可用性只剩 ``pending.preactivations[GUOHUO]`` 那一份清单，故脚本
    必须像真玩家那样一次提交 ``ActivateSkill(picks, choices)``。

    牌名步的**置灰项不选**（与界面 / 校验器同一条纪律：``disabled`` 里的名字提交会被拒）；
    ``declare`` 不在这份清单里时退回第一个没置灰的名字。
    """
    steps = (getattr(query, "preactivations", None) or {}).get(GUOHUO) or ()
    if len(steps) != 2:
        return None
    card_row = _manifest_row(steps[0], ())
    if card_row is None or not card_row.options:
        return None
    picked = int(card_row.options[0])
    name_row = _manifest_row(steps[1], (str(picked),))
    if name_row is None or not name_row.options:
        return None
    blocked = tuple(getattr(name_row, "disabled", ()) or ())
    usable = [text for text in name_row.options if text not in blocked]
    chosen = declare if declare in usable else (usable[0] if usable else None)
    if chosen is None:
        return None
    return ActivateSkill(
        skill_id=GUOHUO,
        picks=(picked,),
        choices=(chosen,),
        answers_seq=query.seq,
    )


class GuohuoScript:
    """【蛊惑】的脚本应答：发动 / 声明哪个牌名 / 要不要质疑。"""

    def __init__(
        self,
        *,
        activate: bool = False,
        declare: str = "",
        doubt: bool = False,
        slash_target: int | None = None,
    ) -> None:
        self.activate = activate
        self.declare = declare
        self.doubt = doubt
        self.slash_target = slash_target
        """【借刀杀人】的 Bn（**座位号**）：只在 ``:designation`` 那一问里答
        （``seat:<slash_target>``）。与 `_WindowSeat.designation`（``(持刀者, Bn)`` 一对）
        不是一回事，故不共用名字。"""
        self.options: list[tuple[str, ...]] = []
        self.activated = False
        """出牌阶段入口**只用一次**（与 `_WindowSeat` 的"每个入口只用一次"同一套建模）。

        出牌阶段会走完一整个回合：于吉摸牌阶段摸到两张之后手里又有牌，若脚本每次都选
        【蛊惑】，同一个用例里就会发动第二次、第三次——被测的那一次（第一次）的账目因此
        被后续结算污染。故发动一次之后一律结束出牌阶段。"""

    def respond(self, query):
        kind = getattr(query.kind, "value", query.kind)
        if kind == "PLAY_PHASE":
            if self.activate and not self.activated:
                action = _guohuo_activation(query, declare=self.declare)
                if action is not None:
                    self.activated = True
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if kind == "SELECT_CARDS":
            return next(a for a in query.legal_actions if isinstance(a, SelectCards))
        if kind == "SELECT_OPTION":
            choices = [a for a in query.legal_actions if isinstance(a, SelectOption)]
            self.options.append(tuple(str(a.choice) for a in choices))
            for action in choices:
                if self.declare and str(action.choice) == self.declare:
                    return action
            if (
                self.slash_target is not None
                and str(getattr(query, "skill_id", "")).endswith(":designation")
            ):
                # 这一问的候选是**座位**（持刀者的【杀】能打谁），不是牌名。
                for action in choices:
                    if str(action.choice) == f"seat:{self.slash_target}":
                        return action
            for action in choices:
                if str(action.choice).startswith("seat:"):
                    return action
            for action in choices:
                if str(action.choice) == "guohuo_doubt" and self.doubt:
                    return action
            for action in choices:
                if str(action.choice) == "guohuo_trust" and not self.doubt:
                    return action
            return choices[0]
        # 其余询问（响应 / 濒死 / 选择面之外的一切）走"能放弃就放弃"：脚本只做它被交代的
        # 那件事，别顺手在借刀 / 万箭的无懈窗口里把自己写死的选项点上去。
        return _fallback(query)


def _run_guohuo(
    *,
    hand: tuple[int, ...],
    declare: str,
    doubters: tuple[bool, ...],
    hp: int = 2,
    hp_max: int = 3,
):
    """座位 0 = 于吉移动版（受伤，故【桃】可用）；座位 1/2 表态由 ``doubters`` 决定。"""
    used = set(hand)
    # 座位 1 / 2 的手牌从牌堆尾部取，避开座位 0 的牌（同一 card_id 不能同时挂在两个人手上）
    filler = tuple(
        index
        for index in range(len(PACK.deck.cards) - 1, -1, -1)
        if index not in used
    )[-2:]
    used |= set(filler)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=hand, hp=hp, hp_max=hp_max),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=11)
    players = [
        GuohuoScript(activate=True, declare=declare),
        GuohuoScript(doubt=doubters[0]),
        GuohuoScript(doubt=doubters[1]),
    ]
    run_queries(
        engine,
        players,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        ),
    )
    return engine, state, players


def _guohuo_deltas(engine, delta_type: str):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def test_guohuo_declares_any_normal_trick_and_voids_a_fake_non_heart_card() -> None:
    """假 + 非♥️ + **有人质疑** ⇒ 质疑者各摸一张，牌从**效果区**作废进弃牌堆（从未结算）。

    作废的前提是**有人质疑**（`04` 票 §1 的读法：真 / 假只决定质疑者的惩罚）；
    `doubters=(True, False)` 里的座位 1 就是那一位。
    """
    spade_slash = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "slash" and card.suit.value == "spade"
    )
    engine, state, players = _run_guohuo(
        hand=(spade_slash,), declare="万箭齐发", doubters=(True, False)
    )
    # 座位 1 质疑 ⇒ 假 ⇒ 摸一张
    draws = [
        record
        for record in _guohuo_deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW and record.to_zone.seat == 1
    ]
    assert draws, "假 ⇒ 质疑者要摸一张牌"
    # 作废：源牌从处理区进弃牌堆
    voided = [
        record
        for record in _guohuo_deltas(engine, "card_move")
        if record.reason is MoveReason.DISCARD
        and record.from_zone.zone is Zone.PROCESSING
        and [card.card_id for card in record.cards] == [spade_slash]
    ]
    assert voided, "非♥️的假牌要从效果区作废进弃牌堆"
    # 从未使用：这张牌**没有**任何一条 reason=USE 的移动（作废 = 不产生使用帧）
    assert not [
        record
        for record in _guohuo_deltas(engine, "card_move")
        if record.reason is MoveReason.USE
        and spade_slash in [card.card_id for card in record.cards]
    ], "作废的牌不该被使用"
    assert state.card(spade_slash).card_id in state.discard


def test_guohuo_truth_makes_doubters_lose_one_hp_and_still_resolves() -> None:
    """真 + ♥️ ⇒ 质疑者各失去 1 点体力，牌照常结算——**救它的是♥️，不是"真"**。

    用户 2026-11-02 的读法（`04` 票 §1）：有人质疑 ⇒ "翻开此牌且此牌作废"，真 / 假只决定
    质疑者的惩罚；然后**只有♥️**才依旧结算。故本用例用的必须是 ♥ 牌：非♥ 的真牌反例见
    :func:`test_guohuo_truth_but_doubted_non_heart_card_is_voided`。
    """
    peach = cards_of_type(PACK, "peach")[0]
    assert PACK.deck.cards[peach].suit.value == "heart", "这条用例的前提是♥️牌"
    engine, state, _ = _run_guohuo(hand=(peach,), declare="桃", doubters=(True, False))
    changes = _guohuo_deltas(engine, "hp_change")
    assert any(change.seat == 1 and change.amount < 0 for change in changes), (
        "真 ⇒ 质疑者失去 1 点体力"
    )
    assert state.player(0).hp > 2, "♥️ ⇒ 牌照常结算（【桃】把于吉回复）"


def test_guohuo_truth_but_doubted_non_heart_card_is_voided() -> None:
    """真 + 有人质疑 + **非♥️** ⇒ 作废：质疑者各失 1 点照记，牌从处理区进弃牌堆。

    牌面读法（用户 2026-11-02）：有人质疑 ⇒ "翻开此牌且此牌作废"，真 / 假只决定质疑者的
    惩罚；然后**只有♥️**才依旧结算。♣【杀】声明成【杀】是最直白的眼睛用例：牌名**逐字
    相等**（真），但花色不是♥️ ⇒ 依旧作废，且作废 = **从未使用**（不压 ``USE`` / ``PLAY``
    帧、目标从未被指定）。
    """
    club_slash = _card(type_id="slash", suit="club")
    engine, state, _ = _run_guohuo(
        hand=(club_slash,), declare="杀", doubters=(False, True)
    )

    changes = _guohuo_deltas(engine, "hp_change")
    assert any(change.seat == 2 and change.amount < 0 for change in changes), (
        "真 ⇒ 质疑者（座位 2）各失去 1 点体力（作废不等于无代价）"
    )
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=club_slash,
    ), "非♥️的被质疑牌要从处理区作废进弃牌堆"
    assert state.card(club_slash).card_id in state.discard
    assert _pushed(engine, FrameKind.USE) == 0, "作废 = 从未使用（不产生 USE 帧）"
    assert _pushed(engine, FrameKind.PLAY) == 0, "也没有打出帧"
    assert state.player(1).hp == 4, "作废 ⇒ 这张【杀】对目标一次都没落地"


def test_guohuo_undoubted_fake_card_still_resolves() -> None:
    """假 + 非♥️ + **无人质疑** ⇒ 照常结算（"没人质疑"就没有作废那一步）。

    牌面读法（用户 2026-11-02）：作废的前提是"有角色质疑"。♦【闪】声明成【杀】⇒ 牌名
    不等（假）、花色不是♥️，但只要没人质疑，这张牌就按声明的【杀】结算。
    """
    diamond_dodge = _card(type_id="dodge", suit="diamond")
    engine, state, _ = _run_guohuo(
        hand=(diamond_dodge,), declare="杀", doubters=(False, False)
    )

    assert state.player(1).hp == 3, "无人质疑 ⇒ 声明的【杀】照常结算（座位 1 挨 1 点）"
    assert not _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=diamond_dodge,
    ), "无人质疑 ⇒ 不作废"
    assert _pushed(engine, FrameKind.USE) == 1, "声明的那张【杀】真的用出去了"
    assert state.card(diamond_dodge).card_id in state.discard, "用完之后来源牌照常进弃牌堆"


def test_guohuo_fake_heart_card_is_voided_when_someone_doubts() -> None:
    """假 + ♥️ + **有人质疑** ⇒ **作废**（♥️ 只救"真"牌）。

    牌面那句"然后若被质疑的牌的花色为♥️，此牌依旧进行结算"是**真**这张牌的追加豁免 ——
    假牌即便♥️ 也照样作废（`04` 票 §1 五行真值表的第四行，用户 2026-11-02 实机复验时纠正）。
    """
    heart_slash = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "slash" and card.suit.value == "heart"
    )
    engine, state, _ = _run_guohuo(
        hand=(heart_slash,), declare="万箭齐发", doubters=(True, False)
    )
    assert state.player(1).hp == 4, "假牌作废 ⇒ 【万箭齐发】一次都没落地"
    assert _moves(
        engine,
        reason=MoveReason.DRAW,
        target_zone=Zone.HAND,
        card=None,
    ), "假 ⇒ 质疑者各摸一张（作废不等于无代价）"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=heart_slash,
    ), "♥️ 的假牌同样要从处理区作废进弃牌堆"
    assert _pushed(engine, FrameKind.USE) == 0, "作废 = 从未使用（不产生 USE 帧）"


def test_guohuo_is_not_offered_without_hand_cards() -> None:
    """没有手牌 ⇒ 两个入口都不出现（出牌阶段候选 + "需要牌时"的提供者候选）。

    直接读**候选面**（``legal_actions_for``）：整局驱动的写法会被自己的摸牌阶段破掉前提
    （于吉一摸到两张牌，候选就该出现——那不是"付不起"）。
    """
    trigger = _card(type_id="slash")
    state = _provider_state(trigger=trigger, hand=())

    def offered(actions) -> bool:
        return any(
            isinstance(action, ActivateSkill) and action.skill_id == GUOHUO
            for action in actions
        )

    assert not offered(
        legal_actions_for(state, 1, QueryKind.PLAY_PHASE, answers_seq=1)
    ), "出牌阶段入口：手里没牌 ⇒ 候选不出现"
    assert not offered(
        legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)
    ), "提供者入口：手里没牌 ⇒ 候选不出现"


# ---------------------------------------------------------------------------
# 蛊惑·出牌阶段的【借刀杀人】（附带指定 Bn 的那一路）
#
# 【借刀杀人】的使用要一条**附带指定**：Bn（持刀者被迫用【杀】打的那个人）由**使用者**
# 一并指定，随 `UseCard.designations` 烘进 USE 帧（W5-02a / W5-02b）。出牌阶段的清单把
# 它表达成"持刀者 A + Bn"两问；蛊惑的流水线因此多一问 = 目标步之后再问一次 Bn，答案由
# `start_use(..., designations=(Bn,))` 交给借刀的结算（`resolve_borrowed_sword` 只认这一份）。
# ---------------------------------------------------------------------------


def _borrowed_sword_state(
    *, borrowed: int, weapon: int, slash: int, dead: tuple[int, ...] = ()
):
    """座位 0 = 于吉（手里 ``borrowed`` 一张）；座位 1 持 ``weapon`` + 一张【杀】；2 / 3 空手。

    ``dead`` 里的座位**已阵亡**（用来把"持刀者的攻击范围内只剩几个候选"压到任意档）。
    """
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(borrowed,), hp=3, hp_max=3),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(alive=2 not in dead),
            SeatSpec(alive=3 not in dead),
        ],
        deck=deck_excluding(PACK, borrowed, weapon, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _run_guohuo_borrowed_sword(
    *,
    borrowed: int,
    weapon: int,
    slash: int,
    slash_target: int | None = None,
    doubters: tuple[bool, bool] = (False, False),
    dead: tuple[int, ...] = (),
):
    """于吉发动【蛊惑】声明【借刀杀人】、把座位 1 指定成持刀者、把 ``slash_target`` 指定成 Bn。

    座位 1 在借刀窗口里选 a（使用那张【杀】）；``doubters`` = 座位 2 / 3 要不要质疑
    （座位 1 是自己上阵的持刀者，一律相信）；``dead`` 透传给 :func:`_borrowed_sword_state`。
    """
    state = _borrowed_sword_state(
        borrowed=borrowed, weapon=weapon, slash=slash, dead=dead
    )
    engine = Engine.from_state(state, seed=11)
    players = [
        GuohuoScript(activate=True, declare="借刀杀人", slash_target=slash_target),
        _WindowSeat(response=slash),
        _Verdict(doubt=doubters[0]),
        _Verdict(doubt=doubters[1]),
    ]
    # 停在**这一次**结算收尾（那张扣置的牌进弃牌堆）之后：于吉的手牌里还有摸牌阶段的
    # 两张牌，再往下走它会发动第二次，用例要读的就是第一次的结论。
    run_queries(
        engine,
        players,
        lambda e: borrowed in e.state.discard and not e.stack,
    )
    return engine, state


def test_guohuo_declares_borrowed_sword_and_asks_for_the_slash_target() -> None:
    """声明【借刀杀人】⇒ 目标步之后再问一次 Bn，持刀者按它把【杀】打出去。

    池与出牌阶段清单**同一份判据**（``targets_within_attack_range``）：座位 1 持雌雄双股剑
    （攻击范围 2）⇒ 池 = 座位 0 / 2 / 3；这里答 ``seat:2``，故被指定挨刀的是座位 2、
    座位 1 用了【杀】不交武器。
    """
    borrowed = _card(type_id="borrowed_sword")
    weapon = _card(type_id="double_sword")
    slash = _card(type_id="slash")
    engine, state = _run_guohuo_borrowed_sword(
        borrowed=borrowed, weapon=weapon, slash=slash, slash_target=2
    )

    asked = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION
        and str(getattr(record.query, "skill_id", "")).endswith(":designation")
    ]
    assert asked, "声明【借刀杀人】之后要问一次「被迫出【杀】的目标」（Bn）"
    assert "seat:2" in tuple(str(action.choice) for action in asked[0].legal_actions), (
        "Bn 的候选池 = 持刀者的攻击范围（座位 0 / 2 / 3）"
    )
    assert state.player(2).hp == 3, "持刀者按指定的 Bn 使用了【杀】"
    assert weapon in state.player(1).equip, "持刀者使用了【杀】⇒ 不交出武器"
    assert borrowed in state.discard, "【借刀杀人】结算完照常进弃牌堆"


def test_guohuo_designation_step_reaches_the_web_payload() -> None:
    """网页读取方拿到的那一问：文案 = `step_requirements["designation"]`，候选是座位。

    形状与目标步**完全同形**（``SELECT_OPTION`` + ``seat:`` 选项 ⇒ 点场上武将），故这里钉
    的是**服务端读出来的那份数据**（`CODING_STANDARDS.md` §6：读取方断言走真实产出路径）：
    新加的 `step_requirements` 键真的被 ``pending_payload`` 读到，而不是退回一句话 summary；
    而"界面画成可点的座位"那一格读的是 ``option_seats``（`projection.option_seats` →
    `pending.js`），所以**它也要断**——只断 ``legal_actions`` 等于只证明服务端发了自己的键。
    """
    from sgs_web.projection import option_seats, pending_payload
    from sgs_web.session import Session

    borrowed = _card(type_id="borrowed_sword")
    weapon = _card(type_id="double_sword")
    slash = _card(type_id="slash")
    state = _borrowed_sword_state(borrowed=borrowed, weapon=weapon, slash=slash)
    engine = Engine.from_state(state, seed=11)
    session = Session(
        pack=PACK, seed=11, ai="random", timeout_seconds=None, player_count=4
    )
    session.engine = engine
    session.participants = (
        GuohuoScript(activate=True, declare="借刀杀人"),
        _Verdict(doubt=False),
        _Verdict(doubt=False),
        _Verdict(doubt=False),
    )
    run_queries(
        engine,
        session.participants,
        lambda e: e.pending is not None
        and str(getattr(e.pending, "skill_id", "")).endswith(":designation"),
    )

    payload = pending_payload(session)
    assert payload is not None
    assert payload["context"]["requirement"] == (
        "选择持刀者的【杀】打向谁（【借刀杀人】的附带指定）"
    ), "读取方必须读到分步文案（`step_requirements` 的新键）"
    assert [action["choice"] for action in payload["legal_actions"]] == [
        "seat:0",
        "seat:2",
        "seat:3",
    ], "Bn 的候选 = 持刀者的攻击范围（座位 0 / 2 / 3）"
    assert payload["option_seats"] == {"seat:0": 0, "seat:2": 2, "seat:3": 3}, (
        "界面据此把这三个座位画成**可点的**（点场上武将，不铺选项按钮）"
    )
    assert option_seats(session.pending) == payload["option_seats"], (
        "载荷里的这一格就是读取方那一份（同源，不另抄一遍判据）"
    )


def test_the_play_phase_manifest_lists_every_name_and_greys_out_the_unusable_ones() -> None:
    """出牌阶段入口 = 预发动两步清单（用户 Q3A）：**列全**牌堆名、用不了的置灰。

    读的全是**真实产出路径**：

    1. ``pending_payload``（→ `preactivation.js`）下发的牌名步：``options`` = 本牌堆真有的
       全部基本牌 + 普通锦囊（旧实现只列"此刻能用"的那几个），``disabled`` = 此刻没有合法
       目标的那些，``when`` = 第一步选中的那张牌的 id（置灰按**来源牌**现算）；
    2. 引擎自己拒收置灰的名字（``_assign_activation`` 的逐步校验，fail-closed）：界面画灰
       只是提示，提交一条置灰的声明必须被拒——写进 ``action`` 记录的下发侧。
    """
    from sgs_web.projection import pending_payload
    from sgs_web.session import Session

    dodge = _card(type_id="dodge")
    slash = _card(type_id="slash", exclude=frozenset({dodge}))
    filler = _fillers(2, exclude=frozenset({dodge, slash}))
    # 于吉已受伤（3 → 2）⇒【桃】此刻**能用**；【闪】/【无懈可击】在出牌阶段没有目标 ⇒ 置灰。
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(dodge,), hp=2, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, dodge, slash, *filler),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = _park_at_guohuo_play_phase(state)
    session = Session(
        pack=PACK, seed=7, ai="random", timeout_seconds=None, player_count=4
    )
    session.engine = engine  # 会话的 pending 直接读引擎（`Session.pending` 是只读属性）

    payload = pending_payload(session)
    assert payload is not None
    steps = payload["preactivations"][GUOHUO]
    assert [step["name"] for step in steps] == ["card", "name"], (
        "出牌阶段入口是两步：扣置哪张 → 声明成什么牌"
    )
    assert steps[0]["zones"] == ["hand"] and steps[0]["min"] == steps[0]["max"] == 1
    assert steps[1]["wording"] == (
        "声明一种牌名：基本牌或普通锦囊牌（此刻没有合法目标的名字置灰、不可选）"
    ), '读取方读到的是声明上的那一句（`step_requirements["name"]`）'

    rows = steps[1]["pool"]
    assert len(rows) == 1, f"手里只有一张来源牌 ⇒ 一行子池，实得 {rows}"
    row = rows[0]
    assert tuple(row["when"]) == (), (
        "只有一个前序组合时清单收成一行 when=()（`flow._pool_rows` 的既定收口）；"
        "多个来源牌时才会逐牌各一行——那条由 tests/test_skill_flow.py 的"
        "`test_the_disabled_set_is_recomputed_from_the_prior_answers` 钉住"
    )

    from sgs.skills.active import _guohuo_declaration_table

    table = _guohuo_declaration_table(state)
    assert row["options"] == list(table), "牌名闭集 = 本牌堆真有的全部基本牌 + 普通锦囊"
    assert "闪" in row["disabled"] and "无懈可击" in row["disabled"], (
        f"出牌阶段没有目标的牌名要置灰，实得 {row.get('disabled')}"
    )
    assert "桃" not in row["disabled"], "受伤了 ⇒【桃】能用（不置灰）"
    assert "杀" not in row["disabled"], "场上有对手 ⇒【杀】有目标（不置灰）"

    # ①b 换一个查看者：牌名与置灰项都是**公开形状**（不含询问者的任何牌面信息），照常下发；
    #     被抹掉的是"哪张来源牌对应哪一行"（`when`）。读取方 = `_mask_foreign_preactivations`。
    foreign = pending_payload(session, human_seat=1)
    assert foreign is not None
    foreign_row = foreign["preactivations"][GUOHUO][1]["pool"][0]
    assert foreign_row["options"] == row["options"], "牌名闭集是公开的"
    assert foreign_row["disabled"] == row["disabled"], "置灰项也是公开的（不含牌面身份）"
    assert tuple(foreign_row["when"]) == (), "前序答案（来源牌 id）必须被抹掉"

    # ② 置灰项靠界面挡不住：提交一条置灰的声明，引擎必须拒收（fail-closed）。
    from sgs.engine import IllegalActionError

    with pytest.raises(IllegalActionError) as raised:
        engine.submit(
            ActivateSkill(
                skill_id=GUOHUO,
                picks=(dodge,),
                choices=("闪",),
                answers_seq=engine.pending.seq,
            )
        )
    assert "闪" in str(raised.value), f"拒收原因要指名那个选项：{raised.value}"
    rejected = engine.records[-1]
    assert rejected.kind == "action" and rejected.accepted is False, (
        "置灰的牌名必须被引擎拒收（不能只靠界面画灰）"
    )
    assert engine.pending is not None, "被拒之后这一问仍挂着（技能可重新提交）"


def test_the_concealed_card_keeps_its_face_secret_until_the_reveal() -> None:
    """UI1/UI2（用户实机报障 2026-11-02）：**验明之前**，舞台上画的只是"声明的牌名"。

    处理区是全局公开区，而【蛊惑】的扣置是"背面朝上"进去的 ⇒ 不特别处理就会当场告诉
    所有人真假。这里跑真引擎到**质疑轮**（验明之前），再把引擎包进真会话，读网页那两份
    数据：① 快照处理区里那张牌带 ``concealed`` 且花色 / 点数全空、牌名 = 声明的——
    **扣置者自己也一样**（用户 2026-11-02 口径："舞台一律画声明的牌，与是谁无关"）；
    ② 记录流里"手牌 → 处理区"那条对谁（含扣置者）都只给张数。
    """
    from sgs_web.projection import concealed_guohuo_cards, visible_record
    from sgs_web.projection import visible_snapshot
    from sgs_web.session import Session

    black_slash = _card(type_id="slash", suit="club")
    strike = _card(type_id="slash", exclude=frozenset({black_slash}))
    filler = _fillers(3, exclude=frozenset({black_slash, strike}))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(strike,)),
            SeatSpec(hero="yuji_mobile", hand=(black_slash,)),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, black_slash, strike, *filler),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    session = Session(
        pack=PACK, seed=7, ai="random", timeout_seconds=None, player_count=4
    )
    session.engine = engine
    session.participants = (
        _WindowSeat(strike=strike),
        _WindowSeat(guohuo=("dodge",)),
        _WindowSeat(),
        _WindowSeat(),
    )
    run_queries(
        engine,
        session.participants,
        lambda e: e.pending is not None
        and str(getattr(e.pending, "skill_id", "")).endswith(":doubt"),
    )

    concealed = concealed_guohuo_cards(engine)
    assert concealed == {black_slash: "dodge"}, (
        f"验明之前这张扣置牌应当记为「未验明」（只公开声明的牌型）：{concealed}"
    )

    def processing_of(seat: int) -> dict:
        """以 ``seat`` 为查看者取一次舞台快照（读**真实产出路径** ``visible_snapshot``）。"""
        session.human_seat = seat
        shown = [
            card
            for card in visible_snapshot(session)["processing"]
            if card["card_id"] == black_slash
        ]
        assert shown, f"座位 {seat} 的快照处理区里应当有这张扣置牌"
        return shown[0]

    def assert_declared_card(shown: dict, who: str) -> None:
        assert shown["concealed"] is True, f"{who}：未验明的扣置牌要带只读标记"
        assert shown["display_name"] == "闪", f"{who}：舞台画的是**声明的牌名**"
        assert shown["suit"] is None and shown["rank"] is None, (
            f"{who}：花色 / 点数也是秘密（♥ 判据决定这张牌作不作废）"
        )

    assert_declared_card(processing_of(0), "别人")
    assert_declared_card(processing_of(1), "扣置者自己")

    moves = [
        record
        for record in session.records()
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [black_slash]
    ]
    assert moves, "扣置这一步确实有一条 card_move"
    def record_face(viewer: int) -> dict:
        """记录流里那条扣置移动对 ``viewer`` 下发的牌面（读真实产出路径 `visible_record`）。"""
        payload = visible_record(
            moves[0], viewer=viewer, concealed=concealed, state=state
        )
        assert payload is not None and payload["cards"], (
            f"查看者 {viewer}：扣置那条应当画**声明的牌**"
        )
        return payload["cards"][0]

    for viewer, who in ((0, "别人"), (1, "扣置者自己")):
        face = record_face(viewer)
        assert face["concealed"] is True, f"{who}：声明的牌带只读标记"
        assert face["display_name"] == "闪", f"{who}：画的是**声明的牌名**"
        assert face["suit"] is None and face["rank"] is None, (
            f"{who}：花色 / 点数依旧是秘密（♥ 判据决定作不作废）"
        )
        assert face["type"] == "dodge" and face["type"] != "slash", (
            f"{who}：不能把源牌的牌型（♣【杀】）画出来"
        )


def test_guohuo_voided_borrowed_sword_never_opens_the_holder_window() -> None:
    """假 + 非♥️ ⇒ 作废：那张借刀**从未使用**，持刀者连借刀窗口都不出现。

    Bn 那一问在**质疑之前**（目标 → 附带指定 → 质疑），故作废时 Bn 已经问过了；作废的
    意思仍是"从未使用 / 打出" ⇒ 一个 USE 帧都不压、持刀者不被问、武器与那张【杀】都还在。
    """
    # ♣【杀】声明成【借刀杀人】：牌名不等 ⇒ 假；梅花不是♥️ ⇒ 作废。
    black_slash = _card(type_id="slash", suit="club")
    weapon = _card(type_id="double_sword", exclude=frozenset({black_slash}))
    holder_slash = _card(type_id="slash", exclude=frozenset({black_slash, weapon}))
    engine, state = _run_guohuo_borrowed_sword(
        borrowed=black_slash,
        weapon=weapon,
        slash=holder_slash,
        slash_target=2,
        doubters=(True, False),
    )

    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and getattr(record.query, "cause", None) is WindowCause.BORROWED_SWORD
    ], "作废的借刀不产生借刀窗口（持刀者不被问）"
    assert weapon in state.player(1).equip, "持刀者连窗口都没开 ⇒ 武器没交出"
    assert holder_slash in state.player(1).hand, "那张【杀】也没被用掉"
    assert state.player(2).hp == 4, "没有人挨刀"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=black_slash,
    ), "非♥️的假牌要从处理区作废进弃牌堆"
    assert [
        record
        for record in _guohuo_deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW and record.to_zone.seat == 2
    ], "假 ⇒ 质疑者摸一张（作废不等于无代价）"


def test_guohuo_borrowed_sword_skips_the_ask_when_only_one_slash_target_is_in_range() -> None:
    """池只剩**一个**候选 ⇒ 这一步**不问**（同"只剩一种可声明牌名时不问"的口径）。

    座位 2 / 3 已阵亡、座位 1 的武器是【诸葛连弩】（范围 1）⇒ Bn 只能是座位 0（于吉自己）：
    直接按那个唯一候选烘进 ``designations``，持刀者的【杀】照常打过来。
    """
    borrowed = _card(type_id="borrowed_sword")
    crossbow = _card(type_id="crossbow")
    slash = _card(type_id="slash")
    engine, state = _run_guohuo_borrowed_sword(
        borrowed=borrowed, weapon=crossbow, slash=slash, dead=(2, 3)
    )

    assert not [
        record
        for record in engine.records
        if record.kind == "query"
        and str(getattr(record.query, "skill_id", "")).endswith(":designation")
    ], "只有一个候选 ⇒ 不问 Bn"
    assert state.player(0).hp == 2, "唯一可及的 Bn 就是于吉自己 ⇒ 他挨了这一刀"
    assert crossbow in state.player(1).equip, "持刀者使用了【杀】⇒ 不交武器"


# ---------------------------------------------------------------------------
# 蛊惑·"需要牌时"的提供者入口（【闪】/ 南蛮 / 万箭 / 决斗）
#
# 用户裁定的②：任何"需要使用 / 打出一张基本牌或普通锦囊牌"的窗口都能进入询问。
# 形状 = **提供者**（`CANDIDATES` 追加 `ActivateSkill` + `PROVIDERS` 注册表），
# 故扣置 / 质疑都在**受理这次响应之前**完成：作废不需要在受理后回滚。
# ---------------------------------------------------------------------------

GUOHUO_TRUST_OPTION = "guohuo_trust"
GUOHUO_DOUBT_OPTION = "guohuo_doubt"


def _card(*, type_id: str, suit: str | None = None, exclude: frozenset[int] = frozenset()) -> int:
    """牌堆里第一张符合条件的牌；``exclude`` 里的 id 跳过（同一张牌不能挂在两处）。"""
    return next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == type_id
        and (suit is None or card.suit.value == suit)
        and index not in exclude
    )


class _Strike:
    """座位 0：出牌阶段对座位 1 使用 ``card``（【杀】/ 锦囊）；其余询问取最小合法应答。"""

    def __init__(self, card: int, target: int = 1) -> None:
        self.card = card
        self.target = target

    def respond(self, query):
        if getattr(query.kind, "value", query.kind) == "PLAY_PHASE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == self.card
                    and self.target in action.targets
                ):
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return _fallback(query)


def _fallback(query):
    """没有专项策略时的最小合法应答：能放弃就放弃，否则取第一个候选。"""
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _NeedsCard:
    """于吉（座位 1）：需要【闪】/【杀】时选【蛊惑】，扣置自己唯一那张手牌；不问则放弃。

    只认**需要【闪】/【杀】**的窗口：锦囊的结算前面还隔着**无懈**的收集式窗口（票 03 起
    【蛊惑】在那扇窗口里也进候选），在那里选它就成了"扣置一张牌当【无懈可击】"，与这些
    用例要测的窗口不是同一扇。``query.required`` 正是那一格事实（``DODGE`` 询问没有它）。
    """

    def __init__(self, *, activate: bool = True) -> None:
        self.activate = activate

    def respond(self, query):
        kind = getattr(query.kind, "value", query.kind)
        if kind in ("DODGE", "RESPOND"):
            needs_card = kind == "DODGE" or getattr(query, "required", None) in ("slash", "dodge")
            if self.activate and needs_card:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == GUOHUO:
                        return action
            return _fallback(query)
        if kind == "SELECT_CARDS":
            for action in query.legal_actions:
                if isinstance(action, SelectCards):
                    return action
        return _fallback(query)


class _Verdict:
    """座位 2 / 3：质疑或相信（其他询问取最小合法应答）。"""

    def __init__(self, *, doubt: bool) -> None:
        self.doubt = doubt

    def respond(self, query):
        if getattr(query.kind, "value", query.kind) == "SELECT_OPTION":
            wanted = GUOHUO_DOUBT_OPTION if self.doubt else GUOHUO_TRUST_OPTION
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and str(action.choice) == wanted:
                    return action
        return _fallback(query)


def _provider_state(*, trigger: int, hand: tuple[int, ...]):
    """座位 0 手里有 ``trigger``；座位 1 = 于吉移动版（手牌 ``hand``）；2 / 3 各一张填充牌。"""
    used = {trigger, *hand}
    filler = [
        index for index in range(len(PACK.deck.cards) - 1, -1, -1) if index not in used
    ][-2:]
    used |= set(filler)
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(trigger,)),
            SeatSpec(hero="yuji_mobile", hand=hand, hp=3, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def _run_provider(
    *,
    trigger_type: str,
    hand: tuple[int, ...],
    doubters: tuple[bool, bool] = (False, False),
    activate: bool = True,
):
    """跑一次"需要牌"窗口：座位 0 用 ``trigger_type`` 打座位 1，于吉在窗口里选【蛊惑】。"""
    trigger = _card(type_id=trigger_type, exclude=frozenset(hand))
    state = _provider_state(trigger=trigger, hand=hand)
    engine = Engine.from_state(state, seed=7)
    players = [
        _Strike(trigger),
        _NeedsCard(activate=activate),
        _Verdict(doubt=doubters[0]),
        _Verdict(doubt=doubters[1]),
    ]
    run_queries(
        engine,
        players,
        lambda e: trigger in e.state.discard and not e.stack,
    )
    return engine, state


def _moves(engine, *, reason, source_zone=None, target_zone=None, card=None):
    """记录流里的 ``card_move``：按原因（可再按来源 / 目标区域、牌 id）筛。"""
    return [
        record
        for record in _guohuo_deltas(engine, "card_move")
        if record.reason is reason
        and (source_zone is None or record.from_zone.zone is source_zone)
        and (target_zone is None or record.to_zone.zone is target_zone)
        and (card is None or [item.card_id for item in record.cards] == [card])
    ]


def _pushed(engine, frame_kind) -> int:
    """记录流里压入过几个 ``frame_kind`` 的帧：**作废 = 不产生 USE / PLAY 帧**的直接判据。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is frame_kind
    )


def _verdict_seats(engine) -> list[int]:
    """质疑询问（``SELECT_OPTION`` 的「质疑 / 相信」）的应答者顺序。"""
    return [
        record.query.actor
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION
        and GUOHUO_TRUST_OPTION
        in tuple(str(action.choice) for action in record.query.legal_actions)
    ]


def test_guohuo_is_offered_in_all_six_windows_with_a_provider_branch() -> None:
    """候选面六档齐（票 03 补齐后三扇）：【闪】/ 打出 / **使用**【杀】（借刀）/
    求【桃】/ 需要【无懈可击】。/ 出牌阶段（不是"需要牌"窗口）不在这份清单里。

    每一档都读**同一份**"这扇窗口要一张什么牌"（``CANDIDATES`` 载荷的
    ``respond_required``），故这张表就是"引擎把哪些窗口接上了提供者"的清单。
    """
    hand = (_card(type_id="peach"),)
    state = _provider_state(trigger=_card(type_id="slash", exclude=frozenset(hand)), hand=hand)

    def offered(actions) -> bool:
        return any(
            isinstance(action, ActivateSkill) and action.skill_id == GUOHUO
            for action in actions
        )

    def respond(**payload) -> bool:
        return offered(
            legal_actions_for(
                state,
                1,
                QueryKind.RESPOND,
                answers_seq=1,
                payload=RespondContext(**payload),
            )
        )

    assert offered(
        legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)
    ), "需要【闪】时【蛊惑】进候选（与【八卦阵】同一姿态）"
    assert respond(
        cause=WindowCause.BARBARIAN_INVASION, required="slash", mode=RespondMode.PLAY
    ), "需要【杀】的响应窗口也进候选"
    assert respond(
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        mode=RespondMode.USE,
        use_targets=(2,),
    ), "借刀（mode=USE）有 provider 分支 ⇒ 进候选（票 03）"
    assert respond(
        cause=WindowCause.NULLIFY, required="nullify", mode=RespondMode.USE
    ), "无懈的收集式窗口也进候选（票 03）"
    assert offered(
        legal_actions_for(state, 1, QueryKind.PEACH, answers_seq=1, dying=0)
    ), "濒死求【桃】也进候选（票 03）"

    # 收集式的赢家已定（``declared``）⇒ 提供者闭嘴：产物要扣置一张牌，赢家已定时不进结算。
    assert not respond(
        cause=WindowCause.NULLIFY,
        required="nullify",
        mode=RespondMode.USE,
        declared=True,
    ), "无懈窗口已有人声明 ⇒ 不再发【蛊惑】候选"
    # 借刀的产物必须够得着 Bn：座位 3 在座位 1 的攻击范围之外 ⇒ 三张杀族牌名都不合法。
    assert not respond(
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        mode=RespondMode.USE,
        use_targets=(3,),
    ), "够不着 Bn ⇒ 借刀窗口不发候选（与真牌候选同一道判据）"


def test_guohuo_truth_card_in_the_dodge_window_cancels_the_slash() -> None:
    """真 + ♥️ ⇒ 质疑者各失 1 点，且【杀】被抵消——**救它的是♥️，不是"真"**。

    用户 2026-11-02 的读法（`04` 票 §1）：有人质疑 ⇒ 作废；真 / 假只决定质疑者的惩罚；
    然后只有♥️ 才依旧结算。故这里显式要求这张【闪】是 ♥ 的；同一条路的非♥ 反例见
    :func:`test_guohuo_truth_but_doubted_non_heart_dodge_is_voided`。
    """
    dodge = _card(type_id="dodge")
    assert PACK.deck.cards[dodge].suit.value == "heart", "这条用例的前提是♥️牌"
    engine, state = _run_provider(
        trigger_type="slash", hand=(dodge,), doubters=(True, False)
    )

    assert state.player(2).hp == 3, "真 ⇒ 质疑者各失去 1 点体力"
    assert state.player(1).hp == 3, "♥️ ⇒ 这张【闪】照常结算，【杀】被抵消，于吉不受伤"
    assert state.player(3).hp == 4, "相信的人不付代价"
    assert engine.state.card(dodge).card_id in engine.state.discard
    assert _pushed(engine, FrameKind.USE) == 2, "【杀】+ 虚拟【闪】各一次使用"
    assert _pushed(engine, FrameKind.PLAY) == 0, "【闪】走使用链，不是打出"


def test_guohuo_truth_but_doubted_non_heart_dodge_is_voided() -> None:
    """真 + 有人质疑 + **非♥️** ⇒ 作废：这面【闪】没交出去，【杀】照常结算。

    ♦【闪】声明成【闪】是这一格的唯一形态（108 档的黑【闪】不存在、♦ 是非♥️）：
    牌名**逐字相等**（真 ⇒ 质疑者失去 1 点），但花色不是♥️ ⇒ 作废、**从未使用**。
    """
    diamond_dodge = _card(type_id="dodge", suit="diamond")
    engine, state = _run_provider(
        trigger_type="slash", hand=(diamond_dodge,), doubters=(True, False)
    )

    assert state.player(2).hp == 3, "真 ⇒ 质疑者失去 1 点体力（作废不等于无代价）"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=diamond_dodge,
    ), "非♥️的被质疑牌要从处理区作废进弃牌堆"
    assert _pushed(engine, FrameKind.USE) == 1, "作废 = 从未使用（只有【杀】自己那一次使用）"
    assert _pushed(engine, FrameKind.PLAY) == 0, "也没有打出帧"
    assert state.player(1).hp == 2, "作废 ⇒ 这次响应没发生，【杀】照常结算（3 → 2）"


def test_guohuo_voided_dodge_does_not_cancel_the_slash() -> None:
    """假 + 非♥️ + **有人质疑** ⇒ 此牌作废：**从未使用**，窗口继续问真牌 ⇒ 【杀】照常结算。

    作废的前提是**有人质疑**（`04` 票 §1）：座位 2 质疑，这张牌才作废；没人质疑时它照常
    结算（见 `::test_guohuo_undoubted_non_heart_card_still_answers_the_dodge_window`）。
    """
    # ♣【杀】声明成【闪】：牌名不等 ⇒ 假；梅花不是♥️ ⇒ 作废。
    black_slash = _card(type_id="slash", suit="club")
    engine, state = _run_provider(
        trigger_type="slash", hand=(black_slash,), doubters=(True, False)
    )

    assert state.player(2).hp == 4, "假 ⇒ 质疑者摸一张（不是失去体力）"
    assert _moves(engine, reason=MoveReason.DRAW, target_zone=Zone.HAND, card=None), (
        "假 ⇒ 质疑者各摸一张牌"
    )
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=black_slash,
    ), "非♥️的假牌要从处理区作废进弃牌堆"
    assert _pushed(engine, FrameKind.USE) == 1, "作废 = 从未使用（只有【杀】自己那一次使用）"
    assert _pushed(engine, FrameKind.PLAY) == 0, "也没有打出帧"
    assert state.player(1).hp == 2, "作废 ⇒ 这次响应没发生，【杀】照常结算（3 → 2）"


def test_guohuo_undoubted_non_heart_card_still_answers_the_dodge_window() -> None:
    """假 + 非♥️ + **无人质疑** ⇒ 照常结算：这张【闪】交出去，【杀】被抵消。

    与 `::test_guohuo_voided_dodge_does_not_cancel_the_slash` 是同一份局面、只改"有没有人
    质疑"——`04` 票 §1 的判据两半里"无人质疑 ⇒ 结算"那一半在**提供者入口**上的眼睛用例
    （出牌阶段那一半是 `::test_guohuo_undoubted_fake_card_still_resolves`）。
    """
    # ♣【杀】声明成【闪】：牌名不等 ⇒ 假；梅花不是♥️；但**没有人质疑** ⇒ 不作废。
    black_slash = _card(type_id="slash", suit="club")
    engine, state = _run_provider(
        trigger_type="slash", hand=(black_slash,), doubters=(False, False)
    )

    assert _verdict_seats(engine) == [2, 3, 0], "质疑段照常问完（只是都答了相信）"
    assert not _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=black_slash,
    ), "无人质疑 ⇒ 不作废"
    assert _pushed(engine, FrameKind.USE) == 2, "【杀】+ 这张虚拟【闪】各一次使用"
    assert _pushed(engine, FrameKind.PLAY) == 0, "【闪】走使用链，不是打出"
    assert state.player(1).hp == 3, "这张【闪】照常结算 ⇒ 【杀】被抵消，于吉不受伤"
    assert engine.state.card(black_slash).card_id in engine.state.discard, (
        "用完之后来源牌照常进弃牌堆"
    )


def test_guohuo_fake_heart_card_does_not_answer_the_dodge_window() -> None:
    """♥️ + 假 + **有人质疑** ⇒ 作废：这张牌**没交出去**，【杀】照常结算。

    "♥️ 依旧结算"只救**真**牌（`04` 票 §1 的五行真值表第四行）；假牌作废后这次响应
    没发生，窗口继续问真牌。
    """
    heart_peach = _card(type_id="peach", suit="heart")
    engine, state = _run_provider(
        trigger_type="slash", hand=(heart_peach,), doubters=(True, False)
    )

    assert state.player(2).hp == 4, "假 ⇒ 质疑者摸一张"
    assert _moves(
        engine, reason=MoveReason.DRAW, target_zone=Zone.HAND, card=None
    ), "假 ⇒ 质疑者各摸一张牌"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=heart_peach,
    ), "♥️ 的假牌同样作废进弃牌堆"
    assert _pushed(engine, FrameKind.USE) == 1, "作废 = 从未使用（只有【杀】自己那一次使用）"
    assert state.player(1).hp == 2, "作废 ⇒ 这次响应没发生，【杀】照常结算（3 → 2）"


def test_guohuo_asks_the_doubts_from_the_next_seat_counter_clockwise() -> None:
    """质疑顺序 = 从于吉下家起逆时针（座位 1 ⇒ 2、3、0），**提问在结算之前收齐**。"""
    dodge = _card(type_id="dodge")
    engine, _state = _run_provider(
        trigger_type="slash", hand=(dodge,), doubters=(True, False)
    )

    assert _verdict_seats(engine) == [2, 3, 0], "从下家起逆时针，三人各问一次"


def test_guohuo_plays_a_slash_in_the_barbarian_window() -> None:
    """南蛮的响应是**打出**【杀】：真 + ♥️ ⇒ 需要被满足、于吉不受伤害；实体牌走 PLAY 帧。

    与【闪】那一格同一个判据（`04` 票 §1）：真只让质疑者失 1 点，**♥️** 才是"依旧结算"的
    条件。非♥ 的真牌反例见 :func:`test_guohuo_truth_but_doubted_non_heart_slash_does_not_respond`。
    """
    slash = _card(type_id="slash")
    assert PACK.deck.cards[slash].suit.value == "heart", "这条用例的前提是♥️牌"
    engine, state = _run_provider(
        trigger_type="barbarian_invasion", hand=(slash,), doubters=(True, False)
    )

    assert state.player(1).hp == 3, "打出【杀】⇒ 南蛮的需要被满足，于吉不受伤"
    assert _pushed(engine, FrameKind.PLAY) == 1, "产物按**打出**交付（PLAY 帧）"
    assert _pushed(engine, FrameKind.USE) == 1, "只有南蛮自己那一次使用"
    assert _verdict_seats(engine) == [2, 3, 0], "真 / 假都要先走完质疑段（与出牌阶段同一套）"


def test_guohuo_truth_but_doubted_non_heart_slash_does_not_respond() -> None:
    """真 + 有人质疑 + **非♥️** ⇒ 作废：南蛮的这次响应没发生，于吉照常挨 1 点。

    ♣【杀】声明成【杀】：牌名逐字相等（真 ⇒ 质疑者失 1 点），花色不是♥️ ⇒ 作废、
    **从未打出**（不压 ``PLAY`` 帧）。
    """
    club_slash = _card(type_id="slash", suit="club")
    engine, state = _run_provider(
        trigger_type="barbarian_invasion", hand=(club_slash,), doubters=(True, False)
    )

    assert state.player(2).hp == 2, (
        "真 ⇒ 质疑者失去 1 点体力（作废不等于无代价）；他自己也是南蛮的目标、手里没有"
        "【杀】⇒ 随后再挨 1 点（4 → 2）"
    )
    assert _pushed(engine, FrameKind.PLAY) == 0, "作废 = 从未打出"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=club_slash,
    ), "非♥️的被质疑牌进弃牌堆"
    assert state.player(1).hp == 2, "作废 ⇒ 南蛮照常结算（3 → 2）"


def test_guohuo_voided_slash_does_not_respond() -> None:
    """假 + 非♥️ + **有人质疑** ⇒ 作废：这次响应没发生，南蛮照常结算（于吉挨 1 点）。

    作废的前提是**有人质疑**（`04` 票 §1）。
    """
    diamond_peach = _card(type_id="peach", suit="diamond")
    engine, state = _run_provider(
        trigger_type="barbarian_invasion", hand=(diamond_peach,), doubters=(True, False)
    )

    assert _moves(engine, reason=MoveReason.DRAW, target_zone=Zone.HAND), "假 ⇒ 质疑者摸一张牌"
    assert _pushed(engine, FrameKind.PLAY) == 0, "作废 = 从未打出"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=diamond_peach,
    ), "非♥️的假牌进弃牌堆"
    assert state.player(1).hp == 2, "作废 ⇒ 南蛮照常结算（3 → 2）"


def test_guohuo_answers_the_duel_window() -> None:
    """【决斗】的响应也是**打出**【杀】：真 ⇒ 这一轮响应成立，对家接不上就挨 1 点。

    这里**没有人质疑**（``doubters=(False, False)``），故牌照样结算与 ♥️ 无关——"无人质疑
    ⇒ 结算"那半条判据正是本用例覆盖的（`04` 票 §1 的判据 = 无人质疑 **或** ♥️）。

    决斗的窗口与南蛮同形（``RESPOND`` + 杀族请求 + ``mode = PLAY``），但它是**交替**的
    帧内游标：于吉打出的这张【杀】若没记成"这次响应成立"，下一轮就会回到他自己身上。
    """
    slash = _card(type_id="slash")
    engine, state = _run_provider(
        trigger_type="duel", hand=(slash,), doubters=(False, False)
    )

    assert state.player(1).hp == 3, "打出【杀】⇒ 这一轮响应成立，于吉不受伤"
    assert state.player(0).hp == 3, "对家手上没有第二张【杀】⇒ 轮到它时挨 1 点"
    assert _pushed(engine, FrameKind.PLAY) == 1, "产物按**打出**交付（PLAY 帧）"
    assert _verdict_seats(engine) == [2, 3, 0], "质疑段照常（下家起逆时针）"


def test_guohuo_answers_the_arrow_barrage_window() -> None:
    """万箭（``required = dodge``）走同一套：真【闪】⇒ 需要被满足、不受伤害。

    同样**没有人质疑**，故这次结算靠的是"无人质疑"那半条判据（同【决斗】那条）。
    """
    dodge = _card(type_id="dodge")
    engine, state = _run_provider(
        trigger_type="arrow_barrage", hand=(dodge,), doubters=(False, False)
    )

    assert state.player(1).hp == 3, "打出【闪】⇒ 万箭的需要被满足"
    assert engine.state.card(dodge).card_id in engine.state.discard


def test_guohuo_kouchu_counts_as_losing_a_card() -> None:
    """扣置 = 手牌 → 处理区、``reason = LOSE``（触发"失去牌后"类技能的那条移动）。"""
    dodge = _card(type_id="dodge")
    engine, _state = _run_provider(
        trigger_type="slash", hand=(dodge,), doubters=(False, False)
    )

    assert _moves(
        engine,
        reason=MoveReason.LOSE,
        source_zone=Zone.HAND,
        target_zone=Zone.PROCESSING,
        card=dodge,
    ), "扣置算失去牌：手牌 → 处理区、reason = LOSE"


def test_guohuo_declining_the_provider_entry_leaves_the_window_alone() -> None:
    """不发动的应答（放弃）⇒ 不扣置、不质疑、牌一张不动（零副作用）。"""
    dodge = _card(type_id="dodge")
    engine, state = _run_provider(
        trigger_type="slash", hand=(dodge,), doubters=(False, False), activate=False
    )

    assert state.player(1).hp == 2, "没用【闪】⇒ 【杀】照常结算"
    assert state.player(2).hp == 4 and state.player(3).hp == 4, "没扣置 ⇒ 没有人被问质疑"
    assert _verdict_seats(engine) == [], "整条技能没发动，质疑段不出现"


# ---------------------------------------------------------------------------
# 蛊惑·濒死求【桃】窗口（票 03 的 C1 缺口第一段）
#
# 用户裁定的② = "任何时候需要一张基本牌或普通锦囊牌"；濒死求【桃】是其中一扇窗口，且官方
# 规则集的濒死例**逐字**写着于吉在自己的濒死结算里发动【蛊惑】用【桃】——它是这条技能的
# **必需**窗口，不是边角。
#
# 形状与【闪】/ 南蛮 / 决斗**逐字同构**：提供者在候选里是一条 ``ActivateSkill``，选中即
# 扣置 → 声明 → 质疑 → 验明；差别只在窗口怎么消费产物——求桃窗口的产物必须走
# ``recover_from_peach``（回复结算插在濒死之下），故产物由**窗口**交付，不是技能自己压帧。
# ---------------------------------------------------------------------------


def _window_required(query, kind: str) -> str | None:
    """这次询问"要一张什么牌"：``RESPOND`` 读询问上的 ``required``，另两扇窗口按询问种类定。

    与引擎侧同一句事实（``PROVIDER_REQUIRED_KEY`` / ``ctx.respond_required``）：脚本据此
    分辨"这一问是要【闪】还是要【无懈可击】"，不必靠猜候选里有什么。另两扇窗口的牌名就是
    询问种类的小写（``DODGE`` → ``dodge`` / ``PEACH`` → ``peach``），故这里不抄一份映射表。
    """
    return getattr(query, "required", None) or kind.lower()


class _WindowSeat:
    """一个座位在"需要牌"窗口里的应答：扣置入口 / 质疑表态 / 真牌（求桃 / 响应 / 无懈）。

    每个入口只用**一次**（``guohuo`` / ``rescue`` / ``response`` / ``nullify`` / ``strike``）：
    嵌套无懈会把同一批人再问一遍，而一张牌只付得起一次；这一份"只用一次"就是脚本对玩家的
    建模（"我已经决定过了"），不是引擎语义。
    """

    def __init__(
        self,
        *,
        strike: int | None = None,
        designation: tuple[int, int] | None = None,
        guohuo: tuple[str, ...] = (),
        guohuo_name: str | None = None,
        doubt: bool = False,
        rescue: int | None = None,
        response: int | None = None,
        nullify: int | None = None,
    ) -> None:
        self.strike = strike
        """出牌阶段用这张牌打座位 1（只用一次）。"""
        self.designation = designation
        """出牌阶段提交"借刀 · 持刀者 + 附带指定 Bn"（``(holder, Bn)``；只用一次）。"""
        self.guohuo = guohuo
        """在**需要这些牌名**的窗口里选【蛊惑】（``dodge`` / ``slash`` / ``peach`` / ``nullify``）。"""
        self.guohuo_name = guohuo_name
        """【蛊惑】的声明牌名：只在 ``:name`` 那一问里答（``None`` = 取第一个候选）。"""
        self.doubt = doubt
        self.rescue = rescue
        """求桃窗口打出的**真牌**（座位 2 的代救）。"""
        self.response = response
        """除求桃以外的响应窗口打出的**真牌**。"""
        self.nullify = nullify
        """无懈窗口里**声明**的那张真牌（收集式：声明不等于使用）。"""

    def respond(self, query):
        kind = getattr(query.kind, "value", query.kind)
        if kind == "PLAY_PHASE":
            if self.designation is not None:
                holder, bn = self.designation
                for action in getattr(query, "designation_actions", ()):
                    if (
                        isinstance(action, UseCard)
                        and action.targets == (holder,)
                        and action.designations == (bn,)
                    ):
                        self.designation = None
                        return action
            if self.strike is not None:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.card_id == self.strike:
                        self.strike = None
                        return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if kind in ("DODGE", "RESPOND", "PEACH"):
            required = _window_required(query, kind)
            if self.guohuo and required in self.guohuo:
                for action in query.legal_actions:
                    if isinstance(action, ActivateSkill) and action.skill_id == GUOHUO:
                        self.guohuo = ()
                        return action
            wanted = None
            if required == "nullify":
                wanted = self.nullify
            elif kind == "PEACH":
                wanted = self.rescue
            else:
                wanted = self.response
            if wanted is not None:
                for action in query.legal_actions:
                    if (
                        isinstance(action, (UseCard, PlayCard))
                        and action.card.card_id == wanted
                    ):
                        if required == "nullify":
                            self.nullify = None
                        elif kind == "PEACH":
                            self.rescue = None
                        else:
                            self.response = None
                        return action
            return _fallback(query)
        if kind == "SELECT_CARDS":
            for action in query.legal_actions:
                if isinstance(action, SelectCards):
                    return action
        if kind == "SELECT_OPTION":
            if self.guohuo_name and str(getattr(query, "skill_id", "")).endswith(":name"):
                for action in query.legal_actions:
                    if isinstance(action, SelectOption) and str(action.choice) == self.guohuo_name:
                        self.guohuo_name = None
                        return action
            wanted = GUOHUO_DOUBT_OPTION if self.doubt else GUOHUO_TRUST_OPTION
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and str(action.choice) == wanted:
                    return action
        return _fallback(query)


def _fillers(count: int, *, exclude: frozenset[int]) -> tuple[int, ...]:
    """牌堆尾部的 ``count`` 张填充牌（避开 ``exclude``：同一张牌不能挂在两处）。"""
    return tuple(
        index
        for index in range(len(PACK.deck.cards) - 1, -1, -1)
        if index not in exclude
    )[:count]


def _run_window(*, seats: list[SeatSpec], players: list, stop) -> tuple[Engine, object]:
    """造局面、装脚本、推进到 ``stop``：三个新窗口的公共驱动。"""
    used = frozenset(card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge))
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *sorted(used)),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(engine, players, stop)
    return engine, state


def _askers(engine, kind: str) -> list[int]:
    """某类询问的**应答者顺序**（读数比集合更严：窗口的推进次序就是它）。"""
    return [query.actor for query in _queries(engine, kind)]


def _closed(engine, frame_kind) -> bool:
    """记录流里某个帧已经出栈（= 那一整段结算跑完）。"""
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is frame_kind
        for record in engine.records
    )


def test_guohuo_saves_itself_in_the_peach_window() -> None:
    """濒死者自己是于吉：扣置 → 质疑 → ♥ ⇒ 这张【桃】照常回复。

    真（牌名相等）与 ♥ 同时成立：**♥ 才是"依旧结算"的条件**，"真"只让质疑者各失 1 点
    （`04` 票 §1 的读法）；【桃】的回复由**求桃窗口**建立（``recover_from_peach``：RECOVER
    帧先于使用帧的回复结算），故这里断的是"回复真的发生了"，不是"技能压了一个使用帧"。
    """
    slash = _card(type_id="slash")
    heart_peach = _card(type_id="peach", suit="heart")
    filler = _fillers(2, exclude=frozenset({slash, heart_peach}))
    seats = [
        SeatSpec(hand=(slash,)),
        SeatSpec(hero="yuji_mobile", hand=(heart_peach,), hp=1, hp_max=3),
        SeatSpec(hand=(filler[0],)),
        SeatSpec(hand=(filler[1],)),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(strike=slash),
            _WindowSeat(guohuo=("peach",)),
            _WindowSeat(),
            _WindowSeat(doubt=True),
        ],
        stop=lambda e: _closed(e, FrameKind.NEAR_DEATH),
    )

    assert _askers(engine, "PEACH") == [0, 1], (
        "座位 0 放弃后问到濒死的于吉自己；他自救成功 ⇒ 窗口到此为止"
    )
    assert state.player(1).hp == 1 and state.player(1).alive, "真 / ♥ ⇒ 【桃】照常回复 1 点"
    assert state.player(3).hp == 3, "真 ⇒ 质疑者（座位 3）各失去 1 点体力"
    assert engine.state.card(heart_peach).card_id in engine.state.discard, (
        "产物照常走使用链 ⇒ 来源牌进弃牌堆"
    )
    assert _pushed(engine, FrameKind.USE) == 2, "【杀】+ 这张虚拟【桃】各一次使用"
    assert _pushed(engine, FrameKind.PLAY) == 0, "【桃】是使用，不是打出"


def _peach_name_options(engine) -> list[tuple[str, ...]]:
    """求桃窗口里【蛊惑】那一问的候选牌名（``:name`` 的 SELECT_OPTION）。"""
    return [
        tuple(str(action.choice) for action in record.query.legal_actions)
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION
        and str(getattr(record.query, "skill_id", "")).endswith(":name")
    ]


def test_guohuo_offers_wine_and_peach_when_yuji_himself_is_dying() -> None:
    """濒死自救：【酒】与【桃】同档（用户 2026-11-02 裁定）。

    真牌路径由 ``TargetRule.near_death_self_only`` 表达同一件事（`effects/actions.py:_peach`），
    【蛊惑】的声明表读同一份语境 ⇒ 濒死者本人被问时两个名字都在；【酒】还**不受**出牌阶段
    那一次的限制。产物按真牌结算（这里声明的是手里那张真【酒】⇒ 真 ⇒ 回复 1）。
    """
    pack = MILITARY
    wine = next(i for i, c in enumerate(pack.deck.cards) if c.type == "wine")
    slash = next(i for i, c in enumerate(pack.deck.cards) if c.type == "slash")
    filler = tuple(
        i
        for i in range(len(pack.deck.cards) - 1, -1, -1)
        if i not in {wine, slash}
    )[-2:]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(hero="yuji_mobile", hand=(wine,), hp=1, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(pack, wine, slash, *filler),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [
            _WindowSeat(strike=slash),
            _WindowSeat(guohuo=("peach",), guohuo_name="酒"),
            _WindowSeat(),
            _WindowSeat(doubt=False),
        ],
        lambda e: _closed(e, FrameKind.NEAR_DEATH),
    )

    assert _peach_name_options(engine) == [("桃", "酒")], (
        f"濒死者本人被问 ⇒ 桃 / 酒 都在（顺序按牌堆），实得 {_peach_name_options(engine)}"
    )
    assert state.player(1).hp == 1 and state.player(1).alive, (
        "声明的【酒】按真牌结算 ⇒ 濒死自救回复 1（1 → 0 → 1）"
    )


def test_guohuo_offers_only_peach_when_someone_else_is_dying() -> None:
    """别人濒死时【酒】**不**进声明表（酒只能自救）——与真牌路径同一条语境判据。

    观测面用"声明表只剩一个名字 ⇒ 这一步不问"这条既定口径：座位 0（于吉，**不是**濒死者）
    在求桃窗口里扣置一张真【桃】当【桃】。若【酒】被错误地并进来，就会多出一问
    （``:name`` 有两项，脚本会按 ``guohuo_name`` 选【酒】），产物牌型随即变成 ``wine``。
    故这里断两件事：**没有** ``:name`` 那一问、产出的虚拟牌是 ``peach``。
    """
    pack = MILITARY
    slash = next(i for i, c in enumerate(pack.deck.cards) if c.type == "slash")
    peach = next(i for i, c in enumerate(pack.deck.cards) if c.type == "peach")
    filler = tuple(
        i for i in range(len(pack.deck.cards) - 1, -1, -1) if i not in {peach, slash}
    )[-2:]
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(slash, peach)),
            SeatSpec(hp=1, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(pack, peach, slash, *filler),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    run_queries(
        engine,
        [
            _WindowSeat(strike=slash, guohuo=("peach",), guohuo_name="酒"),
            _WindowSeat(),
            _WindowSeat(),
            _WindowSeat(doubt=False),
        ],
        lambda e: _closed(e, FrameKind.NEAR_DEATH),
    )

    assert not _peach_name_options(engine), (
        "别人濒死 ⇒ 声明表只剩【桃】⇒ 这一步不问；"
        "多出这一问就说明【酒】被错误地并了进来（两项 ⇒ 引擎才会问）"
    )
    assert state.player(1).hp == 1 and state.player(1).alive, "于吉用蛊惑把座位 1 救回 1 点"


def _park_at_guohuo_play_phase(state, *, seed: int = 7):
    """把引擎开到**于吉自己的出牌阶段**那一问（预发动清单就挂在这条询问上）。"""
    engine = Engine.from_state(state, seed=seed)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer(), PassPlayer()],
        lambda e: e.pending is not None
        and getattr(e.pending.kind, "value", e.pending.kind) == "PLAY_PHASE"
        and e.pending.actor == 0,
    )
    return engine


def _guohuo_query_with_grayed_names(query, card: int, *names: str, grayed: str):
    """把真询问的清单换成一份**合成**清单：牌名闭集 = ``names``，``grayed`` 置灰。

    与 `tests/support/ui_smoke.mjs` 的合成载荷同一姿态：这里要钉的是"AI 读到 ``disabled``
    之后还选不选它"，而真实牌堆的声明表**第一项恒可用**（桃园结义对所有人合法），造不出
    "第一个候选就被置灰"的局面。清单的形状由 `flow.manifest` 决定，这里照它逐字摆一份。
    """
    from sgs.state import Step, StepPool

    steps = (
        Step(
            name="card",
            zones=(Zone.HAND,),
            min=1,
            max=1,
            order=0,
            pool=(StepPool(when=(), options=(str(card),)),),
            wording="扣置一张手牌",
        ),
        Step(
            name="name",
            min=1,
            max=1,
            order=1,
            pool=(
                StepPool(
                    when=(str(card),), options=tuple(names), disabled=(grayed,)
                ),
            ),
            wording="声明一种牌名",
        ),
    )
    return query.model_copy(update={"preactivations": {GUOHUO: steps}})


def test_the_ai_skips_the_grayed_names_in_the_play_phase_manifest() -> None:
    """AI 面（`ai-adaptation.md` §1 第 2 条）：清单里**置灰的名字**一个都不许选。

    置灰 = "在闭集里、此刻不可选"（蛊惑的牌名步：列全牌堆名，把没有合法目标的那几个画灰；
    票 04 §3）。服务端仍会 fail-closed 拒收，但 AI 填出来的复合动作必须**先在候选面**上就是
    合法的——否则它在 32 种子基准组（`tests/test_all_ai_games.py` 的"零非法应答"）里可能反复
    提交被拒的动作。

    断的是**填表那一面**（`_fill_activation`，与 `_score_peach` 同一条"判据面"先例）：
    "选哪一步"由别的打分器决定，本条只问"填出来的那个牌名会不会是被置灰的那个"。
    """
    from sgs.agents import heuristic as heuristic_scoring

    dodge = _card(type_id="dodge")
    filler = _fillers(2, exclude=frozenset({dodge}))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(dodge,), hp=3, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
        ],
        deck=deck_excluding(PACK, dodge, *filler),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = _park_at_guohuo_play_phase(state)
    query = _guohuo_query_with_grayed_names(
        engine.pending, dodge, "桃园结义", "五谷丰登", "杀", grayed="桃园结义"
    )
    steps = query.preactivations[GUOHUO]

    action = heuristic_scoring._fill_activation(
        query, GUOHUO, steps, heuristic_scoring._visible_cards(query.view)
    )

    assert action is not None, "这一步填得出来（候选面上有能用的名字）"
    row = steps[1].pool[0]
    assert action.choices and action.choices[0] in row.options, (
        f"声明的牌名要在闭集里：{action.choices}"
    )
    assert action.choices[0] not in row.disabled, (
        f"AI 填了置灰的牌名 {action.choices[0]!r}（置灰 {row.disabled}）：置灰项不是候选"
    )
    assert action.picks == (dodge,), "代价步照样付那张牌"


def test_the_ai_uses_guohuo_to_save_itself_in_the_peach_window() -> None:
    """AI 面（`ai-adaptation.md` §1 第 2 条）：启发式要能给**提供者候选**打分。

    手里只有一张♥【杀】（当【桃】是假牌）⇒ 真【桃】候选一条都没有，能自救的只有【蛊惑】。
    本局面**没有人质疑**（`_WindowSeat` 缺省相信）⇒ 假牌也照常结算（五行真值表第一格）；
    若有人质疑，假 + ♥ 现在同样作废（第四格，见 `::test_guohuo_fake_heart_card_is_voided_when_someone_doubts`）。``_score_peach`` 对 ``ActivateSkill`` 与真【桃】同价（``_peach_save_value``），
    故 AI 必须选它；打分退化成 0 的实现在这里会与「放弃」打平、把于吉送死。
    """
    heart_slash = _card(type_id="slash", suit="heart")
    filler = _fillers(3, exclude=frozenset({heart_slash}))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(heart_slash,), hp=1, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
            SeatSpec(hand=(filler[2],)),
        ],
        deck=deck_excluding(PACK, heart_slash, *filler),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": 0,
            "channel": None,
            "nature": "NORMAL",
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    players = [
        HeuristicPlayer(ai_stream(7, 0)),
        _WindowSeat(),
        _WindowSeat(),
        _WindowSeat(),
    ]
    run_queries(
        engine,
        players,
        lambda e: _closed(e, FrameKind.NEAR_DEATH),
    )

    assert _askers(engine, "PEACH")[:1] == [0], "濒死的于吉自己先被问"
    assert state.player(0).hp == 1 and state.player(0).alive, (
        "AI 选了【蛊惑】自救：♥ 的假【桃】依旧结算，于吉回到 1 点体力"
    )
    assert [query.actor for query in _queries(engine, "SELECT_CARDS")] == [0], (
        "AI 付了扣置这一步（选牌面），不是空过"
    )
    # 打分的**判据面**（不是靠平分抽签撞出来的）：提供者候选必须**严格**高于「放弃」，
    # 否则 0 分与 Pass 打平，由 ai:<seat> 子流抽签决定救不救自己。
    peach = _queries(engine, "PEACH")[0]
    provider = next(a for a in peach.legal_actions if isinstance(a, ActivateSkill))
    give_up = next(a for a in peach.legal_actions if isinstance(a, Pass))
    assert heuristic_scoring._score_peach(peach, provider) > heuristic_scoring._score_peach(
        peach, give_up
    ), "濒死自救那一档要给【蛊惑】正分（ai-adaptation §1 第 2 条）"


def test_guohuo_voided_peach_lets_the_next_asker_act() -> None:
    """作废 ⇒ 需要没被满足（``satisfied=False``），窗口接着问**下一个座位**。

    于吉扣置一张♣【杀】声明成【桃】：牌名不等 ⇒ 假，且♣不是♥ ⇒ 作废。作废的牌**从未使用**
    （不压使用帧），窗口因此不会误以为有人救了——座位 2 的真【桃】才是这次自救。
    """
    slash = _card(type_id="slash")
    fake = _card(type_id="slash", suit="club", exclude=frozenset({slash}))
    rescue = _card(type_id="peach", exclude=frozenset({slash, fake}))
    filler = _fillers(1, exclude=frozenset({slash, fake, rescue}))
    seats = [
        SeatSpec(hand=(slash,)),
        SeatSpec(hero="yuji_mobile", hand=(fake,), hp=1, hp_max=3),
        SeatSpec(hand=(rescue,)),
        SeatSpec(hand=(filler[0],)),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(strike=slash),
            _WindowSeat(guohuo=("peach",)),
            _WindowSeat(rescue=rescue),
            _WindowSeat(doubt=True),
        ],
        stop=lambda e: _closed(e, FrameKind.NEAR_DEATH),
    )

    assert _askers(engine, "PEACH") == [0, 1, 2], (
        "作废 ⇒ 于吉这一问按「放弃」收尾，窗口问下一个座位（座位 2 出真【桃】后停止）"
    )
    assert state.player(1).hp == 1 and state.player(1).alive, "座位 2 的真【桃】救了于吉"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=fake,
    ), "非♥️的假牌要从处理区作废进弃牌堆"
    assert _pushed(engine, FrameKind.USE) == 2, (
        "作废的那张从未使用：只有【杀】与座位 2 的【桃】两次使用"
    )
    assert _pushed(engine, FrameKind.PLAY) == 0, "求桃窗口里没有人是「打出」"


# ---------------------------------------------------------------------------
# 蛊惑·借刀窗口（票 03 的第二段）与无懈的收集式窗口（第三段）
# ---------------------------------------------------------------------------


def test_guohuo_answers_the_borrowed_sword_window() -> None:
    """被迫使用【杀】（借刀）：真 ⇒ 这张【杀】打向 Bn，持刀者**不交刀**。

    窗口的形状与【闪】/ 南蛮不同：它是 ``RespondMode.USE``，且**目标由窗口给定**
    （``frame.data["designations"][0]`` = Bn）。故产物由窗口交付成
    ``start_use(产物, source=持刀者, targets=(Bn,))``——技能自己不看 Bn。
    """
    borrowed = _card(type_id="borrowed_sword")
    weapon = _card(type_id="qinggang_sword")  # 射程 2 ⇒ Bn（座位 3）够得着
    slash = _card(type_id="slash", suit="heart")
    fillers = _fillers(2, exclude=frozenset({borrowed, weapon, slash}))
    seats = [
        SeatSpec(hand=(borrowed,)),
        SeatSpec(hero="yuji_mobile", hand=(slash,), equip=(weapon,), hp=3, hp_max=3),
        SeatSpec(hand=(fillers[0],)),
        SeatSpec(hand=(fillers[1],)),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(designation=(1, 3)),
            _WindowSeat(guohuo=("slash",)),
            _WindowSeat(),
            _WindowSeat(),
        ],
        stop=lambda e: borrowed in e.state.discard and not e.stack,
    )

    windows = [
        query
        for query in _queries(engine, "RESPOND")
        if query.cause is WindowCause.BORROWED_SWORD
    ]
    assert [query.actor for query in windows] == [1], (
        "借刀窗口问的是**持刀者**（座位 1），且只问一次"
    )
    assert any(
        isinstance(action, ActivateSkill) and action.skill_id == GUOHUO
        for action in windows[0].legal_actions
    ), "候选里真有【蛊惑】——这条用例测的是提供者那条路"
    assert weapon in state.player(1).equip, "用了【杀】⇒ 持刀者不交出武器"
    assert state.player(3).hp == 3, "产物【杀】打向 Bn（座位 3），伤害真的落地"
    assert state.player(1).hp == 3, "持刀者自己是使用者，不受伤"
    assert _pushed(engine, FrameKind.USE) == 2, "借刀 + 产物【杀】各一次使用"
    assert _pushed(engine, FrameKind.PLAY) == 0, "借刀窗口的产物是**使用**，不是打出"
    assert _moves(
        engine,
        reason=MoveReason.LOSE,
        source_zone=Zone.HAND,
        target_zone=Zone.PROCESSING,
        card=slash,
    ), "扣置算失去牌：手牌 → 处理区"


def test_guohuo_voided_slash_hands_the_weapon_over() -> None:
    """借刀窗口里作废（假 + 非♥️ + 有人质疑）⇒ 响应没发生；持刀者没有真【杀】⇒ 走 b 交刀。

    作废的前提是**有人质疑**（`04` 票 §1 的读法）：座位 2 表态质疑，这张♦【桃】声明成
    【杀】（假、非♥️）才作废；没人质疑时它照常结算（那正是另一条用例）。
    """
    borrowed = _card(type_id="borrowed_sword")
    weapon = _card(type_id="qinggang_sword")
    fake = _card(type_id="peach", suit="diamond")
    fillers = _fillers(2, exclude=frozenset({borrowed, weapon, fake}))
    seats = [
        SeatSpec(hand=(borrowed,)),
        SeatSpec(hero="yuji_mobile", hand=(fake,), equip=(weapon,), hp=3, hp_max=3),
        SeatSpec(hand=(fillers[0],)),
        SeatSpec(hand=(fillers[1],)),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(designation=(1, 3)),
            _WindowSeat(guohuo=("slash",)),
            _WindowSeat(doubt=True),
            _WindowSeat(),
        ],
        stop=lambda e: borrowed in e.state.discard and not e.stack,
    )

    assert weapon not in state.player(1).equip, "作废 ⇒ 这次响应没发生，武器照规则交出"
    assert state.card(weapon).card_id in state.player(0).hand, "b 支：武器进使用者手牌"
    assert state.player(3).hp == 4, "没有【杀】打向 Bn ⇒ Bn 不受伤"
    assert _pushed(engine, FrameKind.USE) == 1, "只有借刀自己那一次使用"
    assert _moves(
        engine,
        reason=MoveReason.DISCARD,
        source_zone=Zone.PROCESSING,
        card=fake,
    ), "非♥️的假牌要从处理区作废进弃牌堆"


def test_guohuo_leaves_a_nullify_window_whose_winner_is_already_set() -> None:
    """收集式窗口的**赢家已定** ⇒ 提供者不再发候选（``respond_declared`` 的产品路径）。

    座位 2 先声明一张真【无懈可击】（首个声明者 = 赢家），座位 3 的于吉随后被问：此时扣置
    一张手牌换来的产物**不会进结算**，故那一层的候选里不许有【蛊惑】——否则玩家点下去就是
    白扣一张牌（牌离开手牌、牌面还已经公开过）。

    **阳性对照**：无懈自己那一层是**新窗口**（``response_declared`` 重新为空），于吉在
    那里被问时【蛊惑】照常进候选——两条一起才说明"候选缺席"不是别的原因（手里没牌 /
    牌堆里没有无懈之类）。脚本始终不选它，故质疑段一次都不出现。
    """
    draw2 = _card(type_id="ex_nihilo")
    real_nullify = _card(type_id="nullify", exclude=frozenset({draw2}))
    spare = _card(type_id="peach", exclude=frozenset({draw2, real_nullify}))
    filler = _fillers(1, exclude=frozenset({draw2, real_nullify, spare}))
    seats = [
        SeatSpec(hand=(draw2,)),
        SeatSpec(hand=(filler[0],)),
        SeatSpec(hand=(real_nullify,)),
        SeatSpec(hero="yuji_mobile", hand=(spare,), hp=3, hp_max=3),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(strike=draw2),
            _WindowSeat(),
            _WindowSeat(nullify=real_nullify),
            _WindowSeat(),
        ],
        stop=lambda e: draw2 in e.state.discard and not e.stack,
    )

    yuji_windows = [
        query
        for query in _queries(engine, "RESPOND")
        if query.cause is WindowCause.NULLIFY and query.actor == 3
    ]

    def offers_guohuo(query) -> bool:
        return any(
            isinstance(action, ActivateSkill) and action.skill_id == GUOHUO
            for action in query.legal_actions
        )

    assert len(yuji_windows) == 2, "于吉被问了两次：原窗口（赢家已定）+ 无懈自己那一层"
    assert not offers_guohuo(yuji_windows[0]), (
        "赢家已定 ⇒ 【蛊惑】不进候选（点下去就是白扣一张牌）"
    )
    assert offers_guohuo(yuji_windows[1]), (
        "阳性对照：无懈自己那一层是新窗口，声明还没人下 ⇒ 照常进候选"
    )
    assert _verdict_seats(engine) == [], "脚本始终没选【蛊惑】⇒ 质疑段根本不出现"
    assert engine.state.card(spare).card_id in state.player(3).hand, "那张手牌一张没动"


def test_guohuo_nullifies_only_after_the_doubt_round() -> None:
    """无懈窗口：**质疑在声明前收齐**，赢家才真的使用那张无懈。

    收集式的模型是"先声明、收齐后只有首个声明者使用"，而提供者的契约是"答完就已经把牌
    付清了"。两边拼起来的要求就是本用例断的这一条：质疑段必须**整段**跑在"声明"之前
    （否则作废就没有回滚路径了），而扣置的那张牌只由**赢家**使用。
    """
    draw2 = _card(type_id="ex_nihilo")
    heart = _card(type_id="slash", suit="heart")  # 真 / ♥ 都可以：声明的是【无懈可击】
    real_nullify = _card(type_id="nullify", exclude=frozenset({draw2, heart}))
    filler = _fillers(1, exclude=frozenset({draw2, heart, real_nullify}))
    seats = [
        SeatSpec(hand=(draw2,)),
        SeatSpec(hero="yuji_mobile", hand=(heart,), hp=3, hp_max=3),
        SeatSpec(hand=(real_nullify,)),
        SeatSpec(hand=(filler[0],)),
    ]
    engine, state = _run_window(
        seats=seats,
        players=[
            _WindowSeat(strike=draw2),
            _WindowSeat(guohuo=("nullify",)),
            _WindowSeat(nullify=real_nullify),
            _WindowSeat(),
        ],
        stop=lambda e: draw2 in e.state.discard and not e.stack,
    )

    doubts = [
        index
        for index, record in enumerate(engine.records)
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and GUOHUO_TRUST_OPTION
        in tuple(str(action.choice) for action in record.query.legal_actions)
    ]
    uses = [
        index
        for index, record in enumerate(engine.records)
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.USE
    ]
    assert doubts, "【蛊惑】进了无懈窗口的候选，质疑段因此出现"
    assert uses and doubts[-1] < uses[-1], (
        "质疑在**声明之前**收齐：最后一次质疑询问早于赢家那次使用"
    )
    assert len(state.player(0).hand) == 2, (
        "手里只剩摸牌阶段的两张：被抵消的【无中生有】没有再摸两张"
    )
    assert engine.state.card(real_nullify).card_id in state.player(2).hand, (
        "败者不耗牌：座位 2 的声明在【蛊惑】之后，赢家已定 ⇒ 那张无懈留在手里"
    )
    assert engine.state.card(heart).card_id in engine.state.discard, (
        "赢家那张无懈照常走使用链 ⇒ 扣置的来源牌进弃牌堆"
    )


PACK = load_content_pack(deck_profile="standard_108")

JUSHSOU = "hero.jie_caoren_mobile.jushou"
QINGJIAN = "hero.jie_xiahoudun.qingjian"
HERO = "jie_caoren_mobile"


class Scripted:
    """是否发动【据守】由标志决定；清俭一律 Pass（本文件只借它制造摸牌挂起）。"""

    def __init__(self, *, activate: bool) -> None:
        self.activate = activate
        self.queries = []
        self.qingjian = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == QINGJIAN:
            self.qingjian.append(query)
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JUSHSOU:
            self.queries.append(query)
            if self.activate:
                return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
            return next(a for a in query.legal_actions if isinstance(a, Pass))
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _run(
    *,
    activate: bool,
    flip_before_finish: bool = False,
    deck=tuple(range(10, 40)),
    grant_qingjian: bool = False,
):
    """造一个"座位 0 是界曹仁移动版"的局面，跑到一个完整回合结束。

    ``flip_before_finish``：先在**出牌阶段开始后**把武将牌翻到背面（此刻"回合开始前"已经
    过去，故不会触发跳过），再看结束阶段的据守怎么把自己翻回来。
    ``grant_qingjian``：给座位 0 授予【清俭】——据守摸三张会触发它并挂起（票 25 的回归）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=HERO, hand=(40, 41, 42)), SeatSpec()],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    if grant_qingjian:
        state.player(0).granted_skills.append(QINGJIAN)
    engine = Engine.from_state(state, seed=7)
    player = Scripted(activate=activate)
    players = [player, Scripted(activate=False)]

    def stop(e) -> bool:
        return any(
            record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
            for record in e.records
        )

    if flip_before_finish:
        run_queries(
            engine,
            players,
            lambda e: any(
                record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
                for record in e.records
            ),
        )
        engine.set_flipped(None, 0, True)
    run_queries(engine, players, stop)
    return engine, player


def _draws(engine) -> list[int]:
    """座位 0 摸到的牌（按顺序）。"""
    return [
        card.card_id
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason.value == "DRAW"
        and record.to_zone.seat == 0
        for card in record.cards
    ]


def _flips(engine) -> list[tuple[int, bool]]:
    return [
        (record.seat, record.flipped)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "flipped_change"
    ]


def test_jushou_draws_three_then_turns_the_hero_card_over():
    """正例：结束阶段问一次；发动 ⇒ 摸三张（牌堆顶三张）+ 翻面。"""
    engine, player = _run(activate=True)

    assert len(player.queries) == 1, "结束阶段问一次「是否发动」"
    assert player.queries[0].skill_id == JUSHSOU
    assert engine.state.player(0).flipped is True, "翻到背面朝上"
    assert _flips(engine) == [(0, True)], "翻面落一条 delta"
    # 摸牌阶段 2 张 + 结束阶段 3 张 = 5 张，且结束阶段那三张就是牌堆顶接下来的三张。
    drawn = _draws(engine)
    assert len(drawn) == 5, f"摸牌阶段 2 张 + 据守 3 张，实得 {drawn}"
    assert drawn[2:] == list(deck_top := list(range(10, 40))[2:5]), "据守摸的是牌堆顶三张"


def test_declining_jushou_leaves_hand_and_hero_card_untouched():
    """反例：不发动 ⇒ 一张不摸、不翻面。"""
    engine, player = _run(activate=False)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False
    assert _flips(engine) == [], "不发动不落翻面 delta"


def test_jushou_toggles_a_back_facing_hero_card_back_to_the_front():
    """翻面是 **toggle**（规则集 13.1）：背面朝上时发动 ⇒ 翻回正面。"""
    engine, player = _run(activate=True, flip_before_finish=True)

    assert len(player.queries) == 1
    assert engine.state.player(0).flipped is False, "背面 ⇒ 正面"
    assert _flips(engine) == [(0, True), (0, False)], "造局一条、据守一条"


def test_jushou_still_flips_after_a_qingjian_ask_suspends_the_draw():
    """票 25 回归：摸三张触发【清俭】挂起，续跑后**仍要翻面**（旧实现只摸不翻）。

    Q1（摸牌算获得）之后，据守的"先摸三张"会内联派发清俭并挂起。挂起时若把"本次据守
    已走完"的游标当成两段都完成，续跑会直接返回——手牌摸到了、武将牌却没翻。判据 =
    清俭真的问了一次，且翻面 delta 照旧存在。
    """
    engine, player = _run(activate=True, grant_qingjian=True)

    assert len(player.qingjian) == 1, "据守的摸牌触发清俭一次（Pass）"
    assert len(player.queries) == 1, "据守自己的发动询问仍只有一次"
    assert engine.state.player(0).flipped is True, "续跑之后翻面仍然发生"
    assert _flips(engine) == [(0, True)], "翻面只落一条 delta（不重翻）"
    assert len(_draws(engine)) == 5, "摸牌阶段 2 张 + 据守 3 张（清俭 Pass 不改变张数）"


# ---------------------------------------------------------------------------
# 风包·黄忠【烈弓】（票 03）：零步流水线 + 逐目标帧标签
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\黄忠\\技能.txt`）：
#
#     烈弓：当你于出牌阶段内使用【杀】指定一名角色为目标后，若该角色的手牌数不小于你的
#           体力值或不大于你的攻击范围，你可以令其不能使用【闪】响应此【杀】。
#
# 形状 = 【铁骑】同形的逐目标帧标签（``Tag.UNRESPONSIVE``）：被封的目标连【闪】窗口都
# 不出现（【八卦阵】的"视为使用【闪】"一并不出现——账本「视为使用也是使用」）。
# ---------------------------------------------------------------------------

LIEGONG = "hero.huangzhong.liegong"
HUANGZHONG = "huangzhong"
EIGHT_DIAGRAMS = "equip.eight_diagrams"


def _cards(type_id: str) -> list[int]:
    """某牌名的全部 ``card_id``（按数据表顺序）。"""
    return cards_of_type(PACK, type_id)


def _candidates(query):
    """一条询问的全部候选：``legal_actions`` ∪ 附带指定池（借刀杀人的"a 出【杀】"只在这里）。"""
    return (*query.legal_actions, *getattr(query, "designation_actions", ()))


def _find(query, predicate):
    for action in _candidates(query):
        if predicate(action):
            return action
    return None


def _queries(engine, kind: str):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _liegong_asks(engine):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == LIEGONG
    ]


class Liegong:
    """一个座位的最小脚本：出牌阶段按 ``card`` 出牌，其余询问按标志应答。

    ``activate`` = 【烈弓】问到时发动（否则放弃）；``dodge`` = 有【闪】就打出；
    持刀者窗口（借刀杀人）一律选"出【杀】"那一支。
    """

    def __init__(
        self,
        *,
        card: int | None = None,
        targets: tuple[int, ...] = (1,),
        designations: tuple[int, ...] = (),
        activate: bool = False,
        dodge: bool = False,
    ) -> None:
        self.card = card
        self.targets = tuple(targets)
        self.designations = tuple(designations)
        self.activate = activate
        self.dodge = dodge
        self.played = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.card is not None and not self.played:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == self.card
                and tuple(action.targets) == self.targets
                and tuple(action.designations) == self.designations,
            )
            if action is not None:
                self.played = True
                return action
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == LIEGONG:
            if self.activate:
                return _find(
                    query, lambda action: isinstance(action, ActivateSkill)
                ) or query.legal_actions[0]
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        if getattr(query, "cause", None) is WindowCause.BORROWED_SWORD:
            return (
                _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == "slash",
                )
                or _find(query, lambda action: isinstance(action, Pass))
                or query.legal_actions[0]
            )
        if query.kind == "DODGE":
            if self.dodge:
                return _find(
                    query,
                    lambda action: isinstance(action, UseCard)
                    and action.card.type == "dodge",
                ) or query.legal_actions[0]
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        if query.kind == "PLAY_PHASE":
            return (
                _find(query, lambda action: isinstance(action, EndPhase))
                or query.legal_actions[0]
            )
        if query.kind in ("RESPOND", "PEACH"):
            return _find(query, lambda action: isinstance(action, Pass)) or query.legal_actions[0]
        return query.legal_actions[0]


def _liegong_state(
    *,
    hp: int = 4,
    equip: tuple[int, ...] = (),
    target_hand: tuple[int, ...] = (),
    card: int,
):
    """座位 0 = 黄忠（手里有 ``card``）、座位 1 = 目标；牌堆去掉已发到座位上的牌。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp, equip=equip, hand=(card,), hero=HUANGZHONG),
            SeatSpec(hand=target_hand),
        ],
        deck=deck_excluding(PACK, card, *equip, *target_hand),
        turn_seat=0,
    )


def _run_slash(state, *, card: int, activate: bool = False, dodge: bool = False):
    """座位 0 对座位 1 使用 ``card``；跑到那张牌进弃牌堆、结算栈清空。"""
    players = [
        Liegong(card=card, activate=activate),
        Liegong(dodge=dodge),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: card in e.state.discard and not e.stack,
    )
    return engine


def test_liegong_seals_the_dodge_window_when_the_hand_count_is_not_less_than_hp():
    """第一分支：手牌数 **不小于当前体力值**（黄忠 2 点体力、目标 2 张手牌）⇒ 无【闪】窗口。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = _liegong_state(hp=2, card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=True)

    assert len(_liegong_asks(engine)) == 1, "自己的出牌阶段里问一次「是否发动」"
    assert _queries(engine, "DODGE") == [], "被封 ⇒ 该目标连【闪】询问都不出现"
    assert engine.state.player(1).hp == 3, "不可响应 ⇒ 照常受伤"


def test_liegong_seals_the_dodge_window_when_the_hand_count_is_within_the_attack_range():
    """第二分支：手牌数 **不大于攻击范围**（贯石斧范围 3、目标 2 张手牌、黄忠满体力 4）。"""
    slash, axe, dodge1, dodge2 = (
        _cards("slash")[0],
        _cards("axe")[0],
        _cards("dodge")[0],
        _cards("dodge")[1],
    )
    state = _liegong_state(hp=4, equip=(axe,), card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=True)

    assert len(_liegong_asks(engine)) == 1
    assert _queries(engine, "DODGE") == [], "2 ≤ 攻击范围 3 ⇒ 封【闪】"
    assert engine.state.player(1).hp == 3


def test_liegong_is_not_offered_when_neither_branch_holds():
    """两个分支都不成立（目标 3 张手牌、黄忠满体力 4 且无武器 ⇒ 范围 1）⇒ **不问**、窗口照常。"""
    slash = _cards("slash")[0]
    target_hand = tuple(_cards("dodge")[:3])
    state = _liegong_state(hp=4, card=slash, target_hand=target_hand)

    engine = _run_slash(state, card=slash, activate=True, dodge=True)

    assert _liegong_asks(engine) == [], "条件不成立 ⇒ 连「是否发动」都不发"
    assert len(_queries(engine, "DODGE")) == 1, "窗口照常出现"
    assert engine.state.player(1).hp == 4, "【闪】照常抵消"


def test_declining_liegong_leaves_the_dodge_window_open():
    """条件成立但答「不发动」⇒ 窗口照常、目标能闪掉（正面对照，断言不空转）。"""
    slash, dodge1, dodge2 = _cards("slash")[0], _cards("dodge")[0], _cards("dodge")[1]
    state = _liegong_state(hp=2, card=slash, target_hand=(dodge1, dodge2))

    engine = _run_slash(state, card=slash, activate=False, dodge=True)

    assert len(_liegong_asks(engine)) == 1, "条件成立 ⇒ 仍问一次，只是答了不发动"
    assert len(_queries(engine, "DODGE")) == 1, "不发动 ⇒ 窗口照常"
    assert engine.state.player(1).hp == 4, "【闪】照常抵消"


def test_liegong_is_not_offered_in_another_characters_play_phase():
    """「于出牌阶段内」= **自己的**出牌阶段：借刀杀人逼出的【杀】不触发（此刻是别人的出牌阶段）。

    造局：座位 0（当前回合）打【借刀杀人】指定持刀的座位 1 = 黄忠（2 点体力）、刀下亡魂是
    座位 2（2 张手牌）。若只看"出牌阶段"，黄忠的条件此刻**成立**（2 ≥ 2）；不触发的唯一
    理由是**这不是他自己的出牌阶段**。
    """
    borrowed, crossbow, slash = (
        _cards("borrowed_sword")[0],
        _cards("crossbow")[0],
        _cards("slash")[0],
    )
    dodge1, dodge2 = _cards("dodge")[0], _cards("dodge")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hp=2, hand=(slash,), equip=(crossbow,), hero=HUANGZHONG),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=deck_excluding(PACK, borrowed, crossbow, slash, dodge1, dodge2),
        turn_seat=0,
    )
    players = [
        Liegong(card=borrowed, targets=(1,), designations=(2,)),
        Liegong(),
        Liegong(dodge=True),
    ]
    engine = run_until(
        Engine.from_state(state, seed=1),
        players,
        lambda e: slash in e.state.discard and borrowed in e.state.discard and not e.stack,
    )

    assert _liegong_asks(engine) == [], "别人的出牌阶段里被迫使用的【杀】不触发烈弓"
    assert len(_queries(engine, "DODGE")) == 1, "座位 2 的【闪】窗口照常出现"
    assert engine.state.player(2).hp == 4, "【闪】照常抵消"


MILITARY = load_content_pack(deck_profile="standard_ex_160")
"""军争档（160 张）：火【杀】只在这一档里有；标准 108 档没有它。"""


def _military_cards(type_id: str) -> list[int]:
    return cards_of_type(MILITARY, type_id)


def test_liegong_triggers_on_a_fire_slash_but_not_on_a_duel():
    """牌名按【杀】**家族**判据：火【杀】照常触发；【决斗】不是【杀】⇒ 不问。"""
    fire_slash = _military_cards("fire_slash")[0]
    dodge1, dodge2 = _military_cards("dodge")[0], _military_cards("dodge")[1]
    fire_state = build_state(
        pack=MILITARY,
        seats=[
            SeatSpec(hp=2, hand=(fire_slash,), hero=HUANGZHONG),
            SeatSpec(hand=(dodge1, dodge2)),
        ],
        deck=deck_excluding(MILITARY, fire_slash, dodge1, dodge2),
        turn_seat=0,
    )
    fire_engine = _run_slash(fire_state, card=fire_slash, activate=True)

    assert len(_liegong_asks(fire_engine)) == 1, "火【杀】同族 ⇒ 照常问"
    assert _queries(fire_engine, "DODGE") == [], "封【闪】"
    assert fire_engine.state.player(1).hp == 3

    duel, filler1, filler2 = (
        _cards("duel")[0],
        _cards("peach")[0],
        _cards("peach")[1],
    )
    duel_state = _liegong_state(hp=2, card=duel, target_hand=(filler1, filler2))
    duel_engine = _run_slash(duel_state, card=duel)

    assert _liegong_asks(duel_engine) == [], "【决斗】不是【杀】⇒ 不问"
    assert duel_engine.state.player(1).hp == 3, "【决斗】照常结算（目标没有【杀】）"


def test_liegong_seals_every_qualified_target_with_one_question():
    """【方天画戟】多目标：两个目标各按自己的手牌数判定，**「是否发动」仍只有一次**（票 23 的答案缓存裁定）。"""
    slash, halberd = _cards("slash")[0], _cards("halberd")[0]
    target_one = tuple(_cards("dodge")[:4])
    target_two = tuple(_cards("peach")[:2])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(halberd,), hero=HUANGZHONG),
            SeatSpec(hand=target_one),
            SeatSpec(hand=target_two),
        ],
        deck=deck_excluding(PACK, slash, halberd, *target_one, *target_two),
        turn_seat=0,
    )

    def to_play_phase(engine) -> bool:
        return any(
            record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
            for record in engine.records
        )

    players = [Liegong(card=slash, targets=(1, 2), activate=True), Liegong(), Liegong()]
    engine = Engine.from_state(state, seed=1)
    run_queries(engine, players, to_play_phase)
    # 方天画戟要"【杀】是最后一张手牌"：摸牌阶段过完后把座位 0 的手牌换成只有这张【杀】。
    hand = engine.state.player(0).hand
    hand.clear()
    hand.append(slash)
    run_until(
        engine,
        players,
        lambda e: slash in e.state.discard and not e.stack,
    )

    assert len(_liegong_asks(engine)) == 1, "多目标只问一次（同一帧第二次触发沿用第一次答案）"
    assert _queries(engine, "DODGE") == [], "两个目标都封【闪】（4 ≥ 4 / 2 ≤ 范围 4）"
    assert engine.state.player(1).hp == 3
    assert engine.state.player(2).hp == 3


def test_liegong_also_seals_the_eight_diagrams_provider():
    """封的是一个**响应窗口**，不只是真牌：目标装了【八卦阵】也拿不到「视为使用【闪】」的机会。"""
    slash, eight = _cards("slash")[0], _cards("eight_diagrams")[0]
    dodge, peach = _cards("dodge")[0], _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=2, hand=(slash,), hero=HUANGZHONG),
            SeatSpec(equip=(eight,), hand=(dodge, peach)),
        ],
        deck=deck_excluding(PACK, slash, eight, dodge, peach),
        turn_seat=0,
    )

    engine = _run_slash(state, card=slash, activate=True)

    assert _queries(engine, "DODGE") == [], "窗口根本不出现 ⇒ 【八卦阵】的提供者候选也没有"
    assert engine.state.player(1).hp == 3


# ---------------------------------------------------------------------------
# 票 04 / 票 05 / 票 06 / 票 07：神关羽移动版【武神】【武魂】
# ---------------------------------------------------------------------------

SHEN_GUANYU = "shen_guanyu_mobile"
WUSHEN = "hero.shen_guanyu_mobile.wushen"
WUHUN = "hero.shen_guanyu_mobile.wuhun"


def _heart(type_id: str) -> int:
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit == "heart"
    )


def _not_heart(type_id: str) -> int:
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit != "heart"
    )


def _shen_play_state(hand: tuple[int, ...]):
    """4 人身份局、座位 0 = 神关羽、出牌阶段；其余座位给距离 2 / 3 的对照。"""
    seats = [
        SeatSpec(hero=SHEN_GUANYU, hand=hand, hp=3, hp_max=4, identity=Identity.LORD),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.LOYALIST),
        SeatSpec(identity=Identity.RENEGADE),
    ]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4),
    )


def _play_actions(state):
    return legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)


def _as_slash(actions, source: int):
    return [
        action
        for action in actions
        if getattr(action, "card", None) is not None
        and action.card.type == "slash"
        and action.card.is_virtual
        and action.card.virtual_sources == (source,)
    ]


def test_wushen_turns_a_heart_hand_card_into_a_plain_slash_and_forbids_its_old_use():
    """①：红桃手牌只能当【杀】；它原来的用途（这里是【桃】）从候选里消失。"""
    heart_peach = _heart("peach")
    black_peach = _not_heart("peach")
    state = _shen_play_state((heart_peach, black_peach))
    actions = _play_actions(state)

    assert _as_slash(actions, heart_peach), "红桃手牌 ⇒ 一张普通【杀】的转化候选"
    assert not any(
        isinstance(action, UseCard) and action.card.card_id == heart_peach
        for action in actions
    ), "红桃【桃】不能按原用途使用"
    assert any(
        isinstance(action, UseCard) and action.card.card_id == black_peach
        for action in actions
    ), "非红桃【桃】照常"


def test_wushen_heart_card_product_is_a_plain_slash():
    """①的产物是**普通【杀】**（不是原牌名）：红桃【杀】也走"当普通【杀】"。

    移动版 Q&A 的"红桃火杀按普通杀"由同一条声明覆盖（产物 ``type = "slash"``）；
    火【杀】只在 160 档牌堆里，108 档用普通【杀】钉同一条产物声明。
    """
    heart_slash = _heart("slash")
    state = _shen_play_state((heart_slash,))
    products = _as_slash(_play_actions(state), heart_slash)
    assert products and products[0].card.type == "slash"
    assert products[0].card.name == "杀"


def test_wushen_heart_slash_ignores_distance():
    """②：红桃【杀】的目标池豁免距离（座位 2 在 4 人局距离 3 > 攻击范围 1）。"""
    heart_slash = _heart("slash")
    state = _shen_play_state((heart_slash,))
    product = transformed_card(state, (heart_slash,), "slash")
    pool = check_targets(state, 0, product)
    assert 2 in pool and 3 in pool, "无距离限制 ⇒ 所有其他存活角色都在池里"


def test_wushen_heart_dodge_cannot_answer_a_dodge_window():
    """①：红桃【闪】不能当【闪】打出；非红桃【闪】照常。"""
    heart_dodge = _heart("dodge")
    black_dodge = _not_heart("dodge")
    state = _shen_play_state((heart_dodge, black_dodge))
    actions = legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=1)
    used = {
        action.card.card_id
        for action in actions
        if isinstance(action, UseCard)
    }
    assert heart_dodge not in used
    assert black_dodge in used


def test_wushen_heart_peach_cannot_save_the_dying_holder():
    """①：红桃【桃】不能当【桃】自救（濒死窗口）。"""
    heart_peach = _heart("peach")
    black_peach = _not_heart("peach")
    state = _shen_play_state((heart_peach, black_peach))
    actions = legal_actions_for(state, 0, QueryKind.PEACH, answers_seq=1, dying=0)
    used = {
        action.card.card_id
        for action in actions
        if isinstance(action, UseCard)
    }
    assert heart_peach not in used
    assert black_peach in used


def test_wushen_heart_card_can_be_played_as_a_slash_in_response():
    """①：南蛮 / 决斗的响应窗口里，红桃手牌仍能当【杀】**打出**。"""
    heart_peach = _heart("peach")
    state = _shen_play_state((heart_peach,))
    ctx = RespondContext(
        cause=WindowCause.BARBARIAN_INVASION,
        required="slash",
        mode=RespondMode.PLAY,
    )
    actions = legal_actions_for(
        state, 0, QueryKind.RESPOND, answers_seq=1, payload=ctx
    )
    assert any(
        action.card.is_virtual and action.card.virtual_sources == (heart_peach,)
        for action in actions
    ), "红桃手牌当【杀】打出"


# ---- 武魂：标记 / 死亡时 -------------------------------------------------

KINDS = {
    FrameKind.DAMAGE,
    FrameKind.DEATH,
    FrameKind.NEAR_DEATH,
    FrameKind.LIFE_DECREASE,
    FrameKind.JUDGE,
}


class _PassOrFirst:
    def __init__(self):
        self.queries = []

    def respond(self, query):
        self.queries.append(query)
        if query.kind in ("RESPOND", "DODGE", "PEACH"):
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return query.legal_actions[0]


def _four_seat_state(*, shen_hp=1, dead_seats=(), deck=None):
    seats = [
        SeatSpec(identity=Identity.LORD),
        SeatSpec(identity=Identity.REBEL, hp=shen_hp, hero=SHEN_GUANYU),
        SeatSpec(identity=Identity.LOYALIST),
        SeatSpec(identity=Identity.RENEGADE),
    ]
    for seat in dead_seats:
        seats[seat] = SeatSpec(
            identity=seats[seat].identity, alive=False
        )
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck if deck is not None else tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


def _run_damage(state, *, source, target, amount=1, markers=()):
    engine = Engine.from_state(state, seed=7)
    for marker in markers:
        engine.add_marker(None, **marker)
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": Timing.PHASE_PLAY_IDLE,
            "target": target,
            "channel": None,
            "nature": "NORMAL",
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    players = [_PassOrFirst() for _ in range(len(state.players))]
    for _ in range(20_000):
        if engine.pending is None and not any(
            frame.kind in KINDS for frame in engine.stack.frames
        ):
            break
        if engine.pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    return engine, players


def _marker(seat: int, *, count: int = 1, source_seat: int = 1) -> dict:
    return {
        "seat": seat,
        "name": "梦魇",
        "source_seat": source_seat,
        "source_skill_id": WUHUN,
        "count": count,
        "clear_on_source_death": True,
    }


def _timings(engine, timing):
    return [
        record
        for record in engine.records
        if getattr(record, "kind", None) == "timing"
        and getattr(record, "timing", None) is timing
    ]


def _judge_targets(engine):
    return [
        record.actor
        for record in _timings(engine, Timing.JUDGE_ON)
    ]


def test_wuhun_gives_one_marker_per_damage_point_from_another_character():
    """①逐点：一次 2 点伤害给来源 2 枚（per_point）。"""
    state = _four_seat_state(shen_hp=4)
    engine, _players = _run_damage(state, source=0, target=1, amount=2)
    markers = engine.state.player(0).markers
    assert len(markers) == 1
    assert (markers[0].name, markers[0].count) == ("梦魇", 2)
    assert markers[0].source_seat == 1 and markers[0].source_skill_id == WUHUN


def test_wuhun_does_not_mark_self_or_sourceless_damage():
    for source in (1, None):
        state = _four_seat_state(shen_hp=4)
        engine, _players = _run_damage(state, source=source, target=1)
        assert all(not player.markers for player in engine.state.players)


def test_wuhun_marker_is_public_with_its_source_label():
    state = _four_seat_state(shen_hp=4)
    engine = Engine.from_state(state, seed=7)
    engine.add_marker(
        None,
        0,
        name="梦魇",
        source_seat=1,
        source_skill_id=WUHUN,
        count=2,
        clear_on_source_death=True,
    )
    view = project(engine.state, 0)
    payload = seat_display(
        view.players[0], heroes=load_hero_tables(), players=view.players
    )
    marker = payload["markers"][0]
    assert (marker["name"], marker["count"]) == ("梦魇", 2)
    assert "神关羽" in marker["source_label"] and "武魂" in marker["source_label"]


def test_wuhun_without_marks_does_nothing_on_death():
    state = _four_seat_state()
    engine, _players = _run_damage(state, source=0, target=1)
    assert engine.state.player(1).alive is False
    assert _judge_targets(engine) == []
    assert engine.state.player(2).alive and engine.state.player(3).alive


def test_wuhun_unique_max_marks_judges_without_a_choice():
    slash = cards_of_type(PACK, "slash")[0]
    deck = (slash,) + tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id != slash
    )
    state = _four_seat_state(deck=deck)
    engine, players = _run_damage(
        state, source=0, target=1, markers=(_marker(2, count=2),)
    )
    assert _judge_targets(engine) == [2]
    assert not any(query.kind == "SELECT_OPTION" for player in players for query in player.queries)
    assert engine.state.player(2).alive is False, "非桃 / 桃园结义 ⇒ 直接死亡"
    assert engine.state.player(3).alive


def test_wuhun_tie_lets_the_dying_holder_pick_one():
    deck = (cards_of_type(PACK, "slash")[0],) + tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id != cards_of_type(PACK, "slash")[0]
    )
    state = _four_seat_state(deck=deck)
    engine, players = _run_damage(
        state,
        source=0,
        target=1,
        markers=(_marker(2, count=1), _marker(3, count=1)),
    )
    tie = [
        query
        for player in players
        for query in player.queries
        if query.kind == "SELECT_OPTION" and query.skill_id == "wuhun:pick"
    ]
    assert len(tie) == 1
    assert tie[0].actor == 1, "平票由已经阵亡的神关羽选"
    assert set(tie[0].options) == {"seat:2", "seat:3"}
    assert _judge_targets(engine) == [2], "选中的座位进行判定"
    assert engine.state.player(2).alive is False


def test_wuhun_peach_or_peach_garden_saves_the_judged_character():
    peach = cards_of_type(PACK, "peach")[0]
    deck = (peach,) + tuple(
        card_id for card_id in range(len(PACK.deck.cards)) if card_id != peach
    )
    state = _four_seat_state(deck=deck)
    engine, _players = _run_damage(
        state, source=0, target=1, markers=(_marker(2),)
    )
    assert _judge_targets(engine) == [2]
    assert engine.state.player(2).alive is True


def test_wuhun_ignores_marks_from_another_source():
    state = _four_seat_state()
    engine, _players = _run_damage(
        state,
        source=0,
        target=1,
        markers=(_marker(2, source_seat=0),),
    )
    assert _judge_targets(engine) == []
    assert engine.state.player(2).alive is True


def test_wuhun_does_not_fire_when_the_game_is_already_decided():
    state = _four_seat_state(dead_seats=(3,))
    engine, _players = _run_damage(
        state, source=0, target=1, markers=(_marker(2),)
    )
    assert engine.pending_outcome is not None, "反贼全灭 ⇒ 神关羽死亡即判胜"
    assert _judge_targets(engine) == [], "已判胜 ⇒ 武魂不触发"
    assert engine.state.player(2).alive is True


# ---------------------------------------------------------------------------
# 神吕蒙移动版【涉猎】【攻心】（用户裁定 2026-10-31）
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\神吕蒙\\技能.txt`，逐字）：
#
#     涉猎：摸牌阶段，你可以改为亮出牌堆顶的五张牌，然后获得其中每种花色的牌各一张。
#     攻心：出牌阶段限一次，你可以观看一名其他角色的手牌，然后你可以展示其中一张♥️牌并
#           选择一项：1.弃置此牌；2.将此牌置于牌堆顶。
#
# 用户补充（同轮命令，是解释）：涉猎可取 0~4 张、每花色至多一张、未获得的置入弃牌堆、
# 亮不满即短摸平局；攻心的"可以展示"包含"直接放弃"。
# ---------------------------------------------------------------------------

SHEN_LVMENG = "shen_lvmeng_mobile"
SHELIE = "hero.shen_lvmeng_mobile.shelie"
GONGXIN = "hero.shen_lvmeng_mobile.gongxin"


def _deltas(engine, delta_type: str):
    return [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == delta_type
    ]


def _slot_of(query, card_id: int) -> int:
    for index, slot in enumerate(query.slots):
        if slot.card is not None and slot.card.card_id == card_id:
            return index
    raise AssertionError(f"选择面里没有牌 {card_id}：{query.slots}")


def _pick(query, card_ids: tuple[int, ...]) -> SelectCards:
    return SelectCards(
        picks=tuple(_slot_of(query, card_id) for card_id in card_ids),
        answers_seq=query.seq,
    )


def _first_suit(suit: str, *, excluded=()) -> int:
    return next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in excluded and PACK.deck.cards[card_id].suit == suit
    )


def _shelie_deck(top: tuple[int, ...]) -> tuple[int, ...]:
    """把 ``top`` 依次放在牌堆顶，其余牌接在后面（与界裸衣的测试同形）。"""
    used = set(top)
    return (*top, *(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used))


def _shelie_slots(query) -> tuple[int, ...]:
    return tuple(slot.card.card_id for slot in query.slots if slot.card is not None)


def _shelie_queries(engine):
    return [
        query
        for query in _queries(engine, QueryKind.SELECT_CARDS.value)
        if query.spec.max_per_suit is not None
    ]


class _SheliePlayer:
    """摸牌阶段发动涉猎；在选牌面里每选中的花色各拿第一张（``suits`` 为空 = 一张不拿）。"""

    def __init__(self, suits: tuple[str, ...] = ()) -> None:
        self.suits = tuple(suits)
        self.activations = []
        self.picks = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION.value and query.skill_id == SHELIE:
            self.activations.append(query)
            return next(a for a in query.legal_actions if isinstance(a, ActivateSkill))
        if query.kind == QueryKind.SELECT_CARDS.value and query.spec.max_per_suit is not None:
            self.picks.append(query)
            seen: set[str] = set()
            slots = []
            for index, slot in enumerate(query.slots):
                suit = slot.card.suit if slot.card is not None else None
                if suit is None or suit not in self.suits or suit in seen:
                    continue
                seen.add(suit)
                slots.append(index)
            return SelectCards(picks=tuple(sorted(slots)), answers_seq=query.seq)
        if query.kind == QueryKind.PLAY_PHASE.value:
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        return query.legal_actions[0]


def _shelie_run(
    *,
    top: tuple[int, ...],
    suits: tuple[str, ...],
    discard: tuple[int, ...] = (),
):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=_shelie_deck(top),
        discard=discard,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer(suits=suits)
    run_until(
        engine,
        [player, PassPlayer()],
        lambda e: e.finished or ran_timing(e, Timing.PHASE_PLAY_START),
    )
    return engine, player


def test_shelie_reveals_five_and_takes_one_per_chosen_suit():
    """亮出五张（公开处理区）⇒ 每花色各取一张；未获得的置入弃牌堆；本阶段不摸牌。"""
    heart = _first_suit("heart")
    diamond = _first_suit("diamond", excluded={heart})
    club = _first_suit("club", excluded={heart, diamond})
    spade = _first_suit("spade", excluded={heart, diamond, club})
    heart2 = _first_suit("heart", excluded={heart, diamond, club, spade})
    top = (heart, diamond, club, spade, heart2)
    engine, player = _shelie_run(top=top, suits=("heart", "spade"))

    assert len(player.activations) == 1, "摸牌阶段问一次「是否发动」"
    asked = player.picks
    assert len(asked) == 1
    query = asked[0]
    assert query.actor == 0
    assert query.spec.total == (0, 4)
    assert query.spec.max_per_suit == 1
    assert [slot.ref.zone for slot in query.slots] == [Zone.PROCESSING] * 5
    assert set(_shelie_slots(query)) == set(top), "五张都在处理区、牌面公开"
    # 候选生成期已按花色收窄：任何候选都不能同时拿两张红桃。
    for action in query.legal_actions:
        if not isinstance(action, SelectCards):
            continue
        suits = [PACK.deck.cards[query.slots[index].card.card_id].suit for index in action.picks]
        assert len(suits) == len(set(suits)), f"候选 {action.picks} 同花色出现两次"
    assert any(isinstance(a, SelectCards) and len(a.picks) == 0 for a in query.legal_actions), (
        "0 张也是一个合法候选（放弃 = 提交 0 张）"
    )

    hand = engine.state.player(0).hand
    assert heart in hand and spade in hand
    assert not ({diamond, club, heart2} & set(hand))
    assert {diamond, club, heart2} <= set(engine.state.discard), "未获得的置入弃牌堆"
    assert len(hand) == 2, "改为亮牌 ⇒ 本阶段一张都不摸（不是 2 + 2）"
    assert engine.state.processing == [], "亮牌结算完离开处理区"


def test_shelie_can_take_zero_and_discards_all_five():
    heart = _first_suit("heart")
    diamond = _first_suit("diamond", excluded={heart})
    club = _first_suit("club", excluded={heart, diamond})
    spade = _first_suit("spade", excluded={heart, diamond, club})
    heart2 = _first_suit("heart", excluded={heart, diamond, club, spade})
    top = (heart, diamond, club, spade, heart2)
    engine, _player = _shelie_run(top=top, suits=())

    assert engine.state.player(0).hand == []
    assert set(top) <= set(engine.state.discard), "一张不拿 ⇒ 五张全部置入弃牌堆"


def test_shelie_discards_and_gains_use_put_not_discard():
    heart = _first_suit("heart")
    spade = _first_suit("spade", excluded={heart})
    club = _first_suit("club", excluded={heart, spade})
    diamond = _first_suit("diamond", excluded={heart, spade, club})
    heart2 = _first_suit("heart", excluded={heart, spade, club, diamond})
    top = (heart, spade, club, diamond, heart2)
    engine, _player = _shelie_run(top=top, suits=("club",))

    moves = [
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_move"
        and record.from_zone.zone is Zone.PROCESSING
    ]
    gained = [
        record
        for record in moves
        if record.to_zone.zone is Zone.HAND
        and {card.card_id for card in record.cards} == {club}
    ]
    leftover = [
        record
        for record in moves
        if record.to_zone.zone is Zone.DISCARD
        and {card.card_id for card in record.cards} == {heart, spade, diamond, heart2}
    ]
    assert len(gained) == 1 and gained[0].reason is MoveReason.PUT
    assert len(leftover) == 1 and leftover[0].reason is MoveReason.PUT, (
        "未获得的牌是规则性置入弃牌堆（PUT），不是弃置（DISCARD）"
    )


def test_shelie_short_reveal_ends_the_game_in_a_draw():
    """短摸（两堆合计不足 5 张）⇒ 游戏直接结束、判平局（用户裁定）。"""
    top = (
        _first_suit("heart"),
        _first_suit("diamond"),
        _first_suit("club"),
        _first_suit("spade"),
    )
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=top,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer()
    run_until(engine, [player, PassPlayer()], lambda e: e.finished)

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"
    assert player.picks == [], "短摸不再发选牌询问"


def test_shelie_offers_nothing_without_a_deck():
    """没有可亮的牌时同样按短摸结束（0 < 5），不进入选牌步。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec()],
        deck=(),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    player = _SheliePlayer()
    run_until(engine, [player, PassPlayer()], lambda e: e.finished)
    assert engine.finished and engine.records[-1].reason == "both_decks_empty"
    assert player.picks == []


# ---- 攻心（与界吕蒙 ☆攻心共用同一条流水线）-------------------------------


def _gongxin_engine(target_hand: tuple[int, ...]):
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=SHEN_LVMENG, hand=()),
            SeatSpec(hand=target_hand),
        ],
        deck=deck_excluding(PACK, *target_hand),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return Engine.from_state(state, seed=7)


def _heart_and_black() -> tuple[int, int]:
    heart = _first_suit("heart")
    black = next(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit in {"spade", "club"}
    )
    return heart, black


class _GongxinPlayer:
    """发动攻心 → 选座位 1 →（可选）选红桃 → 选处置。"""

    def __init__(self, *, heart: int | None, option: str | None) -> None:
        self.heart = heart
        self.option = option
        self.watch = []

    def respond(self, query):
        if query.kind == QueryKind.SKILL_ACTIVATION.value:
            # 神吕蒙同时有【涉猎】：本组只测【攻心】，摸牌阶段一律不发动涉猎。
            return next(
                (a for a in query.legal_actions if isinstance(a, Pass)),
                query.legal_actions[0],
            )
        if query.kind == QueryKind.PLAY_PHASE.value:
            return next(
                (a for a in query.legal_actions
                 if isinstance(a, ActivateSkill) and a.skill_id == GONGXIN),
                None,
            ) or next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if query.kind == QueryKind.SELECT_OPTION.value and query.skill_id == "gongxin:seat":
            return next(
                a for a in query.legal_actions
                if isinstance(a, SelectOption) and a.choice == "seat:1"
            )
        if query.kind == QueryKind.SELECT_CARDS.value:
            self.watch.append(query)
            if self.heart is None:
                return next(a for a in query.legal_actions if isinstance(a, Pass))
            return _pick(query, (self.heart,))
        if query.kind == QueryKind.SELECT_OPTION.value and query.skill_id == "gongxin:option":
            if self.option is None:
                return query.legal_actions[0]
            return next(
                a for a in query.legal_actions
                if isinstance(a, SelectOption) and a.choice == self.option
            )
        return query.legal_actions[0]


def test_gongxin_mobile_watches_the_whole_hand_and_discards_the_shown_heart():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="discard")
    run_until(engine, [player, PassPlayer()], lambda e: heart in e.state.discard)

    assert len(player.watch) == 1
    assert {card.card_id for card in player.watch[0].revealed} == {black, heart}
    assert [slot.card.card_id for slot in player.watch[0].slots if slot.card] == [heart]
    assert [
        (delta.seat, delta.card.card_id)
        for delta in _deltas(engine, "card_reveal")
    ] == [(1, heart)], "展示是公开事实"
    assert heart in engine.state.discard
    assert heart not in engine.state.player(1).hand


def test_gongxin_mobile_can_put_the_heart_on_top_of_the_deck():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="deck_top")
    run_until(engine, [player, PassPlayer()], lambda e: e.state.deck and e.state.deck[0] == heart)
    assert engine.state.deck[0] == heart
    assert heart not in engine.state.player(1).hand


def test_gongxin_mobile_views_the_hand_even_without_a_heart():
    """用户裁定：目标有手牌就必须能观看——没有红桃也发空面询问，出口是「放弃」。"""
    black_cards = tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit in {"spade", "club"}
    )[:2]
    engine = _gongxin_engine(black_cards)
    player = _GongxinPlayer(heart=None, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)

    assert len(player.watch) == 1, "观看这一步无条件发生"
    query = player.watch[0]
    assert query.slots == (), "没有红桃 ⇒ 可选槽为空"
    assert {card.card_id for card in query.revealed} == set(black_cards), "整副手牌仍授予"
    assert [a.kind for a in query.legal_actions] == ["Pass"], "唯一出口 = 放弃"
    assert _deltas(engine, "card_reveal") == []


def test_gongxin_mobile_may_pass_with_a_heart_and_keeps_the_card():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=None, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    assert heart in engine.state.player(1).hand, "放弃 ⇒ 不展示、不动目标的牌"
    assert _deltas(engine, "card_reveal") == []


def test_gongxin_mobile_timeout_defaults_to_the_first_option():
    """二选一不可放弃、超时取第一个合法项 = 弃置此牌（服务端 ``fallback_action`` 同一判据）。"""
    heart, black = _heart_and_black()
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=SHEN_LVMENG, hand=()), SeatSpec(hand=(black, heart))],
        deck=deck_excluding(PACK, black, heart),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    player = _GongxinPlayer(heart=heart, option=None)
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    # 脚本里 option=None 会去候选里找 choice=None ⇒ 落回第一个候选（= discard）。
    assert heart in engine.state.discard


def test_gongxin_mobile_is_once_per_play_phase():
    heart, black = _heart_and_black()
    engine = _gongxin_engine((black, heart))
    player = _GongxinPlayer(heart=heart, option="discard")
    run_until(engine, [player, PassPlayer()], lambda e: e.state.turn_seat == 1)
    play_queries = [
        query
        for query in _queries(engine, QueryKind.PLAY_PHASE.value)
        if query.actor == 0
    ]
    assert len(play_queries) >= 1
    assert not any(
        isinstance(action, ActivateSkill) and action.skill_id == GONGXIN
        for action in play_queries[-1].legal_actions
    ), "本阶段已经用过一次 ⇒ 不再进候选"



# ---------------------------------------------------------------------------
# 魏延【狂骨】（`feng-weiyan` 票 01）：锁定技，造伤后按「扣减体力前」的距离回复 1 点体力
#
# 牌面（`D:\SGS_HEROS\神话再临-风包\魏延\技能.txt`，逐字）：
#
#     狂骨：锁定技，当你对一名角色造成1点伤害后，若其扣减体力前你计算与其的距离不大于 1，
#           你回复 1 点体力。
#
# 全部走**外部缝**（记录流 / 终局状态）：一条【杀】或一张【南蛮入侵】打出伤害，看魏延的体力、
# `RECOVER` 帧与 `hp_change`。距离这一侧的关键用例是
# `test_kuanggu_uses_the_distance_before_the_hp_deduction`——目标被这一击打死、又带着 +1
# 防御马时，只有"扣减体力前定格"的实现才会给出"不触发"（现算会算成 1 而误回血）。
#
# 两条判据（`edge-cases.md` 的 H2 / H3）：**持有者自校**（伤害必须由魏延造成：别人打人、
# 魏延自己挨打都不触发）与**锁定技不问**（记录流里一条 `SKILL_ACTIVATION` 都没有）。
# "无来源伤害（【闪电】）"与"失去体力（【苦肉】）"是结构排除：前者 `source` 为 `None`、
# 与持有者永不相等；后者走 `LIFE_DECREASE` 帧、根本不到伤害帧的这两个时机。
# ---------------------------------------------------------------------------

KUANGGU = "hero.weiyan.kuanggu"
WEIYAN = "weiyan"

_KUANGGU_SLASH = cards_of_type(PACK, "slash")[0]

_KUANGGU_PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档：只有它带【古锭刀】（对无手牌目标 +1）——"2 点伤害 ⇒ 逐点两次"的载体。"""

_KUANGGU_QINGLONG = cards_of_type(PACK, "green_dragon_blade")[0]
"""青龙偃月刀：攻击范围 3——用来把"够不着"的目标变成能使用【杀】的目标（距离 2 仍合法）。"""

_KUANGGU_CHITU = cards_of_type(PACK, "chitu")[0]
"""赤兔（−1 进攻马）：**自己**计算与其他角色的距离 −1。"""

_KUANGGU_DILU = cards_of_type(PACK, "dilu")[0]
"""的卢（+1 防御马）：**其他角色**计算与你的距离 +1。"""


def _kuanggu_use_chain_done(engine) -> bool:
    """这张牌的使用链整个跑完（USE 帧已出栈 + 结算栈空）。

    与 `tests/test_use_and_resolve.py::_use_chain_done` 同姿态：被动局面不会自然收局，故只跑到
    被测链结束为止——死亡结算在链内，濒死 / 死亡 / 造成伤害后都已在记录里。
    """
    popped = any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )
    return popped and not engine.stack


def _kuanggu_find_play(player, query):
    """该座位要打的那张牌（【杀】要求目标正是 ``target``；锦囊只看牌名）。"""
    for action in query.legal_actions:
        if not isinstance(action, UseCard) or action.card.type != player.type_id:
            continue
        if player.type_id == "slash" and action.targets != (player.target,):
            continue
        return action
    return None


class _KuangguActor:
    """座位 0（或指定座位）：打一次指定牌名的牌，其余询问取最小合法应答。"""

    def __init__(self, *, target: int, type_id: str = "slash") -> None:
        self.target = target
        self.type_id = type_id
        self.used = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                action = _kuanggu_find_play(self, query)
                if action is not None:
                    self.used = True
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _kuanggu_engine(
    seats, *, used=(), target=1, type_id="slash", turn_seat=0, seed=17, pack=PACK
):
    """造局面并跑到那一张牌的使用链结束；返回 ``(engine, state)``。"""
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck_excluding(pack, *used),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=seed)
    players = [PassPlayer() for _ in seats]
    players[turn_seat] = _KuangguActor(target=target, type_id=type_id)
    run_queries(engine, players, _kuanggu_use_chain_done)
    return engine, state


def _kuanggu_hp_changes(engine, seat: int):
    """某座位的 ``hp_change`` delta 序列（回复 = before < after）。"""
    return [record for record in _deltas(engine, "hp_change") if record.seat == seat]


def _kuanggu_timings(engine) -> list:
    return [record.timing for record in engine.records if record.kind == "timing"]


def test_kuanggu_recovers_one_after_dealing_damage_within_distance():
    """距离 1 造成 1 点伤害 ⇒ 回复 1 点；锁定技不问（一条 `SKILL_ACTIVATION` 都没有）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(1).hp == 3, "【杀】照常造成 1 点伤害"
    assert state.player(0).hp == 4, "距离 1 ⇒ 狂骨回复 1 点体力"
    assert not [
        query
        for query in _queries(engine, QueryKind.SKILL_ACTIVATION.value)
        if query.skill_id == KUANGGU
    ], "锁定技不问是否发动"


def test_kuanggu_recovery_goes_through_a_recover_frame():
    """回复走**回复体力事件**（`RECOVER_VALUE` / `RECOVER_AFTER`）+ 一条 `hp_change`。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    timings = _kuanggu_timings(engine)
    assert Timing.RECOVER_VALUE in timings and Timing.RECOVER_AFTER in timings
    assert [(change.before, change.after) for change in _kuanggu_hp_changes(engine, 0)] == [
        (3, 4)
    ]


def test_kuanggu_does_not_fire_outside_distance_one():
    """距离 2 ⇒ 不回复；伤害照常结算（条件只管狂骨，不改伤害）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_QINGLONG,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(),
            SeatSpec(hp=4),
            SeatSpec(),
        ],
        target=2,
        used=(_KUANGGU_SLASH, _KUANGGU_QINGLONG),
    )

    assert distance(state, 0, 2) == 2, "四座环上 0 与 2 相距 2"
    assert state.player(2).hp == 3, "伤害照常结算"
    assert state.player(0).hp == 3, "距离 2 ⇒ 狂骨不回复"


def test_kuanggu_ignores_damage_it_did_not_deal():
    """别人造成的伤害不触发——**魏延自己挨打也不回血**（条件必须自校持有者是伤害来源）。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3),
            SeatSpec(hand=(_KUANGGU_SLASH,)),
            SeatSpec(),
            SeatSpec(),
        ],
        target=0,
        turn_seat=1,
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(0).hp == 2, "吃 1 点伤害但没回复（伤害不是我造成的）"
    assert Timing.RECOVER_VALUE not in _kuanggu_timings(engine)


def test_kuanggu_triggers_once_per_damage_point():
    """「1 点伤害」⇒ 逐点：古锭刀造 2 点 ⇒ 两次触发、共回复 2 点（两帧）。"""
    pack = _KUANGGU_PACK_160
    scimitar = cards_of_type(pack, "ancient_scimitar")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=2, equip=(scimitar,), hand=(slash,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        pack=pack,
        used=(slash, scimitar),
    )

    assert state.player(1).hp == 2, "古锭刀对**没有手牌**的目标 +1 ⇒ 2 点伤害"
    assert state.player(0).hp == 4, "两点各回复 1 点"
    assert [
        (change.before, change.after) for change in _kuanggu_hp_changes(engine, 0)
    ] == [(2, 3), (3, 4)], "两帧逐点各一次，不是一次回复 2 点"


def test_kuanggu_still_recovers_when_the_lethal_damage_kills_a_character_within_distance():
    """致死也在触发面上：造成伤害后在濒死 / 死亡**之后**跑，距离按扣减体力前照样是 1。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=3, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=1),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert not state.player(1).alive, "1 点伤害打死 1 体力的目标"
    assert state.player(0).hp == 4, "目标死了也照常回复（距离在扣减体力前定格）"


def test_kuanggu_uses_the_distance_before_the_hp_deduction():
    """**本技能的核心边界**：相邻 + 【的卢】⇒ 扣减体力前距离 2 ⇒ 打死了也不触发。

    这一条能红：若在"造成伤害后"现算距离，死者（装备已弃、装备技不再解析）会算成 1 ⇒ 误回血。
    下面同时断言"现算值确实是 1"，把"为什么必须在扣减体力前定格"留在测试里。
    """
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_QINGLONG,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(hp=1, equip=(_KUANGGU_DILU,)),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH, _KUANGGU_QINGLONG, _KUANGGU_DILU),
    )

    assert not state.player(1).alive, "这一击把目标打死了"
    assert state.player(0).hp == 3, "扣减体力前距离 2（相邻 + 的卢）⇒ 狂骨不回复"
    assert state.player(1).equip == [], "死亡结算已弃置的卢"
    assert distance(state, 0, 1) == 1, "死者现算反而够得着——正是必须定格的原因"


def test_kuanggu_judges_each_damaged_target_separately():
    """一次【南蛮入侵】打三个目标：逐目标各判距离（相邻的两个触发、相距 2 的不触发）。"""
    barbarian = cards_of_type(PACK, "barbarian_invasion")[0]
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=1, hand=(barbarian,)),
            SeatSpec(hp=4),
            SeatSpec(hp=4),
            SeatSpec(hp=4),
        ],
        type_id="barbarian_invasion",
        used=(barbarian,),
    )

    assert [state.player(seat).hp for seat in (1, 2, 3)] == [3, 3, 3], "三个目标各 1 点伤害"
    assert state.player(0).hp == 3, "相邻的 1 / 3 号各触发一次（1 → 3），2 号距离 2 不触发"


def test_kuanggu_at_full_hp_recovers_nothing():
    """满体力 ⇒ 不回复（规则 §4.15 L426）：连回复事件都不产生，零 delta。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(hero=WEIYAN, hp=4, hand=(_KUANGGU_SLASH,)),
            SeatSpec(hp=4),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_KUANGGU_SLASH,),
    )

    assert state.player(1).hp == 3
    assert state.player(0).hp == 4
    assert _kuanggu_hp_changes(engine, 0) == [], "满体力不写体力"
    assert Timing.RECOVER_VALUE not in _kuanggu_timings(engine), "也不该压回复帧"


def test_kuanggu_reads_the_mount_chain():
    """距离读既有的 `DISTANCE` 链：**赤兔（−1）**让座位距离 2 变 1 ⇒ 触发。"""
    engine, state = _kuanggu_engine(
        [
            SeatSpec(
                hero=WEIYAN, hp=3, equip=(_KUANGGU_CHITU,), hand=(_KUANGGU_SLASH,)
            ),
            SeatSpec(),
            SeatSpec(hp=4),
            SeatSpec(),
        ],
        target=2,
        used=(_KUANGGU_SLASH, _KUANGGU_CHITU),
    )

    assert distance(state, 0, 2) == 1, "赤兔把座位距离 2 压成 1（也是【杀】能指定它的原因）"
    assert state.player(2).hp == 3
    assert state.player(0).hp == 4, "计算距离 1 ⇒ 狂骨回复 1 点"


# ---------------------------------------------------------------------------
# 夏侯渊【神速】（`feng-xiahouyuan` 票 01）：回合开始时一至两项 → 跳阶段 + 无距离【杀】
#
# 牌面（`D:\SGS_HEROS\神话再临-风包\夏侯渊\技能.txt`，逐字）：
#
#     神速：回合开始时，你可以选择一至两项：
#           【1】跳过判定阶段和摸牌阶段；
#           【2】跳过出牌阶段并弃置一张装备牌。
#           你每以此法选择一项，便视为你使用一张无距离限制的普通【杀】。
#
# 全部走**外部缝**（询问 / 记录流 / 终局状态）：
#
# - **时点**：「回合开始时」是帧外时机，本技能是它补上派发点后的第一个消费者
#   （`tests/test_hero_catalog.py` 的 `_owner_probes` 因此不必给探针——钩子无条件，
#   "是不是我的回合"由激活计划按帧的 ``source`` 判）；
# - **跳阶段**：`PHASE_JUDGE` / `PHASE_DRAW` / `PHASE_PLAY_START` 记录在场与否 + 判定区的牌保留；
# - **虚拟【杀】**：`USE_DESIGNATE_TARGET` 记录的目标（打得到距离 2 的座位 = 无距离限制）。
#
# 三条容易写错的边界各有一条断言：【2】付不起时**取消整项**（R4）、
# 回合开始时的【杀】**不占**出牌阶段次数（W5-04）、空城目标**不进**池。
# ---------------------------------------------------------------------------

SHENSU = "hero.xiahouyuan.shensu"
XIAHOUYUAN = "xiahouyuan"
XIAHOUDUN = "xiahoudun"
GANGLIE = "hero.xiahoudun.ganglie"

_SHENSU_ARMOR = cards_of_type(PACK, "eight_diagrams")[0]
"""八卦阵（防具）：【2】的成本——**手牌**里的装备牌。"""

_SHENSU_HORSE = cards_of_type(PACK, "dilu")[0]
"""的卢（+1 防御马）：【2】的成本——**装备区**里的装备牌（"装备牌"含两个区）。"""

_SHENSU_INDULGENCE = cards_of_type(PACK, "indulgence")[0]
"""【乐不思蜀】：判定区里的牌（"跳过判定阶段时保留"的载体）。"""

_SHENSU_HAND = tuple(cards_of_type(PACK, "dodge")[:2])
"""手牌里的两张【闪】：不参与任何判据，只用来撑手牌数 / 给出牌阶段用。"""

_SHENSU_SPADE = next(
    card_id
    for card_id, card in enumerate(PACK.deck.cards)
    if card.suit == "spade"
)


def _shensu_option(query, choice: str):
    """这条 ``SELECT_OPTION`` 询问里 ``choice`` 对应的候选。"""
    for action in query.legal_actions:
        if isinstance(action, SelectOption) and action.choice == choice:
            return action
    raise AssertionError(
        f"没有选项 {choice!r}："
        f"{[a.choice for a in query.legal_actions if isinstance(a, SelectOption)]}"
    )


def _shensu_cards_action(query, card_ids):
    """这条 ``SELECT_CARDS`` 询问里"恰好选中 ``card_ids``"的那条候选。"""
    wanted = set(card_ids)
    for action in query.legal_actions:
        if not isinstance(action, SelectCards):
            continue
        picked = {
            query.slots[index].card.card_id
            for index in action.picks
            if query.slots[index].card is not None
        }
        if picked == wanted:
            return action
    raise AssertionError(f"选择面里选不出 {sorted(wanted)}：{[s.card for s in query.slots]}")


def _shensu_fallback(query):
    """其余询问取最小合法应答：有 Pass 就 Pass，否则第一条。"""
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _ShensuActor:
    """座位 0（夏侯渊）的脚本：答【神速】的每一问；可选在出牌阶段再出一张真【杀】。"""

    def __init__(
        self,
        *,
        activate: bool = True,
        items: str | None = None,
        targets=(),
        picks=(),
        play_slash=(),
        ganglie_choice: str | None = None,
    ) -> None:
        self.activate = activate
        self.items = items
        self.targets = list(targets)
        self.picks = tuple(picks)
        self.play_slash = list(play_slash)
        self.ganglie_choice = ganglie_choice
        self.items_query = None
        self.target_queries = []
        self.cards_queries = []

    def respond(self, query):
        skill = str(getattr(query, "skill_id", "") or "")
        if query.kind == "SKILL_ACTIVATION" and skill == SHENSU:
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return _shensu_fallback(query)
        if query.kind == "SELECT_OPTION" and skill.endswith(":items"):
            self.items_query = query
            return _shensu_option(query, self.items)
        if query.kind == "SELECT_OPTION" and ":target:" in skill:
            self.target_queries.append(query)
            return _shensu_option(query, f"seat:{self.targets.pop(0)}")
        if query.kind == "SELECT_OPTION" and self.ganglie_choice is not None and skill == "ganglie:choice":
            return _shensu_option(query, self.ganglie_choice)
        if query.kind == "SELECT_CARDS":
            self.cards_queries.append(query)
            return _shensu_cards_action(query, self.picks)
        if query.kind == "PLAY_PHASE":
            if self.play_slash:
                target = self.play_slash.pop(0)
                for action in query.legal_actions:
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == "slash"
                        and action.targets == (target,)
                    ):
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return _shensu_fallback(query)


class _GanglieActor:
    """座位 1（夏侯惇）：受伤害后发动【刚烈】，其余取最小合法应答。"""

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == GANGLIE:
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        return _shensu_fallback(query)


def _shensu_engine(seats, *, used=(), top=None, players=None, turn_seat=0, seed=29):
    """造「座位 0 是夏侯渊、从自己的回合起点开跑」的局面，跑到弃牌阶段开始。"""
    deck = tuple(card_id for card_id in deck_excluding(PACK, *used) if card_id != top)
    if top is not None:
        deck = (top, *deck)
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=seed)
    actors = list(players) if players is not None else []
    while len(actors) < len(seats):
        actors.append(PassPlayer())
    run_queries(engine, actors, lambda e: ran_timing(e, Timing.PHASE_DISCARD_START))
    return engine, state


def _shensu_targets(engine):
    """本局每一次"使用并指定目标"的目标元组（按记录序）。"""
    return [
        record.targets
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_DESIGNATE_TARGET
    ]


def _shensu_uses(engine) -> int:
    """本局压过几个 ``USE`` 帧。"""
    return sum(
        1
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.USE
    )


def _shensu_activation_queries(engine):
    return [
        query
        for query in _queries(engine, "SKILL_ACTIVATION")
        if query.skill_id == SHENSU
    ]


def test_shensu_asks_once_at_own_turn_start_and_declines_cleanly():
    """自己的回合开始时问一次；放弃 ⇒ 什么都不跳、一张【杀】都没有。"""
    actor = _ShensuActor(activate=False)
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert len(_shensu_activation_queries(engine)) == 1
    assert ran_timing(engine, Timing.PHASE_JUDGE), "没发动 ⇒ 判定阶段照常"
    assert ran_timing(engine, Timing.PHASE_DRAW), "没发动 ⇒ 摸牌阶段照常"
    assert _shensu_uses(engine) == 0
    assert len(state.player(0).hand) == 4, "没发动 ⇒ 正常摸两张"


def test_shensu_does_not_trigger_on_another_turn_start():
    """别人的回合开始时**不问**（自校：只有当前回合角色的那一份实例被问）。

    先手座位只能在**无身份局**（2 人）里配置（身份局的先手 = 主公座位），故这条用例
    单独造一个 2 人局：座位 1 先手，夏侯渊在座位 0。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=XIAHOUYUAN), SeatSpec(hand=_SHENSU_HAND)],
        deck=deck_excluding(PACK, *_SHENSU_HAND),
        turn_seat=1,
        phase=Phase.PREPARE,
        rule_config=RuleConfig(
            player_count=2, starting_hand_size=4, first_seat=1
        ),
    )
    engine = Engine.from_state(state, seed=29)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: ran_timing(e, Timing.PHASE_DISCARD_START),
    )

    assert not _shensu_activation_queries(engine)
    assert ran_timing(engine, Timing.TURN_START), "回合确实开始了，只是不是他的"


def test_shensu_option_one_skips_judge_and_draw_and_uses_one_slash():
    """选【1】：判定 + 摸牌阶段整体越过、准备阶段照常，换来一张无距离【杀】。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert not ran_timing(engine, Timing.PHASE_JUDGE), "判定阶段被跳过"
    assert not ran_timing(engine, Timing.PHASE_DRAW), "摸牌阶段被跳过"
    assert ran_timing(engine, Timing.PHASE_PREPARE), "只跳两个阶段，准备阶段照常"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3, "【杀】造成 1 点伤害"
    assert len(state.player(0).hand) == 2, "摸牌阶段被跳过 ⇒ 一张都没摸"


def test_shensu_skipped_judge_phase_keeps_the_judge_zone_cards():
    """跳过的判定阶段**不消费**判定区里的牌（账本行；规则 §11.5）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(1,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, judge=(_SHENSU_INDULGENCE,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_INDULGENCE,),
        players=[actor],
    )

    assert state.player(0).judge == [_SHENSU_INDULGENCE], "延时锦囊留在判定区"
    assert not ran_timing(engine, Timing.PHASE_JUDGE)
    assert not [
        record
        for record in engine.records
        if record.kind == "frame" and record.frame_kind is FrameKind.JUDGE
    ], "一张判定都没开"


def test_shensu_slash_ignores_distance():
    """【杀】的目标**不受攻击范围限制**：座位 2 在距离 2、攻击范围 1 之外，仍可指定。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        players=[actor],
    )

    assert distance(state, 0, 2) == 2, "座位 2 与座位 0 的距离是 2（攻击范围 1 够不着）"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3


def test_shensu_slash_respects_cannot_be_targeted():
    """「无距离限制」只豁免距离：**不能成为目标**的锁定技照常生效（空城）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(2,))
    _engine, _state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(hero="jie_zhugeliang", hand=()),
            SeatSpec(),
        ],
        players=[actor],
    )

    assert actor.target_queries, "有合法目标 ⇒ 照常发问"
    choices = [
        action.choice
        for action in actor.target_queries[0].legal_actions
        if isinstance(action, SelectOption)
    ]
    assert "seat:1" not in choices, "空城的界诸葛亮不能成为【杀】的目标"
    assert "seat:2" in choices


def test_shensu_without_a_legal_target_skips_only_the_slash():
    """一个合法目标都没有 ⇒ 不发空询问：这一张【杀】不发生，跳过阶段照旧生效。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw")
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN),
            SeatSpec(hero="jie_zhugeliang", hand=()),
        ],
        players=[actor],
    )

    assert not actor.target_queries, "不发没有候选的询问"
    assert _shensu_uses(engine) == 0
    assert not ran_timing(engine, Timing.PHASE_DRAW), "跳过阶段照旧"
    assert ran_timing(engine, Timing.PHASE_PLAY_START)


def test_shensu_option_two_disappears_without_equipment():
    """没有装备牌 ⇒ 选项步只剩【1】（不能执行的项不出现）。"""
    actor = _ShensuActor(items="shensu_skip_judge_draw", targets=(1,))
    _engine, _state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=_SHENSU_HAND),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=_SHENSU_HAND,
        players=[actor],
    )

    assert actor.items_query is not None
    choices = [
        action.choice
        for action in actor.items_query.legal_actions
        if isinstance(action, SelectOption)
    ]
    assert choices == ["shensu_skip_judge_draw"]


def test_shensu_option_two_discards_one_equipment_and_skips_the_play_phase():
    """选【2】：弃一张**装备牌**（手牌 / 装备区都铺）→ 跳过出牌阶段 → 一张【杀】。"""
    actor = _ShensuActor(
        items="shensu_skip_play_equip", targets=(2,), picks=(_SHENSU_HORSE,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(
                hero=XIAHOUYUAN,
                hand=(_SHENSU_ARMOR, *_SHENSU_HAND),
                equip=(_SHENSU_HORSE,),
            ),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR, _SHENSU_HORSE),
        players=[actor],
    )

    assert actor.cards_queries, "弃装备是一问"
    face = {
        slot.card.card_id
        for slot in actor.cards_queries[0].slots
        if slot.card is not None
    }
    assert face == {_SHENSU_ARMOR, _SHENSU_HORSE}, "手牌 + 装备区的装备牌都在面上"
    assert _SHENSU_HORSE in state.discard, "选中的装备区牌进了弃牌堆"
    assert not ran_timing(engine, Timing.PHASE_PLAY_START), "出牌阶段被跳过"
    assert _shensu_targets(engine) == [(2,)]
    assert state.player(2).hp == 3
    # 次序（用户裁定 2026-11-01）：选完【2】**立刻**付代价，付完才问【杀】的目标。
    asks = [
        (record.seq, record.query.kind, str(getattr(record.query, "skill_id", "") or ""))
        for record in engine.records
        if record.kind == "query"
    ]
    pay_at = next(i for i, (_s, kind, _sid) in enumerate(asks) if kind == "SELECT_CARDS")
    target_at = next(i for i, (_s, _k, sid) in enumerate(asks) if ":target:" in sid)
    assert pay_at < target_at, f"弃装备必须早于选目标：{asks}"


def test_shensu_both_options_use_two_slashes_in_order():
    """两项都选：先【1】的一张【杀】、再弃装备、【2】的一张【杀】（固定次序）。"""
    actor = _ShensuActor(
        items="shensu_both", targets=(2, 3), picks=(_SHENSU_ARMOR,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(_SHENSU_ARMOR, *_SHENSU_HAND)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR,),
        players=[actor],
    )

    assert not ran_timing(engine, Timing.PHASE_JUDGE)
    assert not ran_timing(engine, Timing.PHASE_DRAW)
    assert not ran_timing(engine, Timing.PHASE_PLAY_START)
    assert _shensu_uses(engine) == 2
    assert _shensu_targets(engine) == [(2,), (3,)]
    assert state.player(2).hp == 3 and state.player(3).hp == 3

    # 次序（用户裁定 2026-11-01）：**先付【2】的代价**（弃装备）⇒ 再【1】的【杀】⇒ 再【2】的【杀】。
    uses = [
        record.seq
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_DESIGNATE_TARGET
    ]
    moves = [
        record.seq
        for record in engine.records
        if record.kind == "delta"
        and getattr(record, "delta_type", None) == "card_move"
        and record.to_zone.zone is Zone.DISCARD
        and any(card.card_id == _SHENSU_ARMOR for card in record.cards)
    ]
    assert len(uses) == 2 and len(moves) == 1
    assert moves[0] < uses[0] < uses[1], "装备是第一张【杀】**之前**付掉的代价"


def test_shensu_pays_option_two_before_any_slash():
    """【2】的弃牌是**选择的代价**：选完**立刻**付，付完才开始执行结果。

    用户裁定（2026-11-01）：「第 2 个选项需要立刻付出代价才能选择成功；选择完毕后依次执行
    跳过对应阶段」。故询问次序必须是 选项 → **弃装备** → 第一张【杀】的目标，而不是
    "先选目标出杀、再回头弃牌"。
    """
    actor = _ShensuActor(
        items="shensu_both", targets=(2, 3), picks=(_SHENSU_ARMOR,)
    )
    engine, _state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(_SHENSU_ARMOR, *_SHENSU_HAND)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(_SHENSU_ARMOR,),
        players=[actor],
    )

    asks = [
        (record.seq, record.query.kind, str(getattr(record.query, "skill_id", "") or ""))
        for record in engine.records
        if record.kind == "query"
    ]
    items_at = next(i for i, (_seq, _kind, sid) in enumerate(asks) if sid.endswith(":items"))
    pay_at = next(i for i, (_seq, kind, _sid) in enumerate(asks) if kind == "SELECT_CARDS")
    first_target_at = next(
        i for i, (_seq, _kind, sid) in enumerate(asks) if ":target:" in sid
    )
    assert items_at < pay_at < first_target_at, (
        f"次序必须是 选项 → 弃装备 → 选目标：{asks}"
    )

    # 更硬的一条：弃牌那条 delta 必须早于**第一张【杀】**的使用。
    move = next(
        record
        for record in engine.records
        if record.kind == "delta"
        and getattr(record, "delta_type", None) == "card_move"
        and record.to_zone.zone is Zone.DISCARD
        and any(card.card_id == _SHENSU_ARMOR for card in record.cards)
    )
    first_use = next(
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.USE_DESIGNATE_TARGET
    )
    assert move.seq < first_use.seq, "代价先付：弃装备早于第一张【杀】的使用"




def test_shensu_slash_does_not_consume_the_play_phase_slash_limit():
    """回合开始时的那张【杀】**不占**出牌阶段的次数（规则 §17.10 A2 / 账本 W5-04）。"""
    actor = _ShensuActor(
        items="shensu_skip_judge_draw", targets=(1,), play_slash=(1,)
    )
    engine, state = _shensu_engine(
        [
            SeatSpec(hero=XIAHOUYUAN, hand=(cards_of_type(PACK, "slash")[0],)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        used=(cards_of_type(PACK, "slash")[0],),
        players=[actor],
    )

    assert _shensu_uses(engine) == 2, "出牌阶段还能出第二张【杀】（额度没被回合开始时那张占掉）"
    assert state.player(1).hp == 2, "两张【杀】各造成 1 点伤害"
    assert state.player(0).card_use_counts.get("slash", 0) == 1, (
        "计数只数到出牌阶段的那一张：回合开始时的虚拟【杀】用 counts=False"
    )


def test_shensu_hero_is_in_the_default_pool():
    """夏侯渊进默认启用池（风包、魏、4 体力、神速）。"""
    from sgs.data import enabled_hero_ids, hero_by_id

    assert XIAHOUYUAN in enabled_hero_ids()
    hero = hero_by_id(XIAHOUYUAN)
    assert hero is not None
    assert hero.true_name == "夏侯渊"
    assert hero.kingdom.value == "wei"
    assert hero.hp_max == 4
    assert [skill.id for skill in hero.skills] == ["shensu"]


def test_a_no_target_declaration_never_ships_the_source_card_in_the_record_window() -> None:
    """②（用户 2026-11-02 实机复验）：**不用指定目标**的声明，记录流也不许带源牌牌面。

    【蛊惑】扣置的那张牌在验明之前只有"声明的牌名"是公开的。有目标步时，人会被目标询问
    挡住、那一批记录赶在验明之前下发，掩码按**当下的结算栈**算就够了；而**无目标**的声明
    （无中生有 / 万箭齐发一类）在同一批记录里走完"扣置 → 质疑 → 验明 → 结算 / 作废"——
    等这批记录下发时栈上早已空了，只看栈就会把扣置那条 `card_move` 连同**源牌牌面**发出去，
    前端把它画进"处理中"的舞台格（用户看到的就是"验明之前亮了源牌"）。

    读的是**真实产出路径**：`GameRegistry.view()` —— 它自己算窗口、自己算掩码。
    """
    from sgs_web.serve import GameRegistry
    from sgs_web.session import Session

    slash = _card(type_id="slash", suit="club")
    filler = _fillers(3, exclude=frozenset({slash}))
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="yuji_mobile", hand=(slash,), hp=3, hp_max=3),
            SeatSpec(hand=(filler[0],)),
            SeatSpec(hand=(filler[1],)),
            SeatSpec(hand=(filler[2],)),
        ],
        deck=deck_excluding(PACK, slash, *filler),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = _park_at_guohuo_play_phase(state)
    session = Session(
        pack=PACK, seed=7, ai="random", timeout_seconds=None, player_count=4
    )
    session.engine = engine
    registry = GameRegistry(runs_dir="/tmp/unused")

    cursor = registry.view(session, after=-1, logs_after=0)["cursor"]
    session.submit_answer(_guohuo_activation(engine.pending, declare="无中生有"))
    payload = registry.view(session, after=cursor, logs_after=0)

    kouchu = [
        record
        for record in payload["records"]
        if record.get("kind") == "delta"
        and record.get("delta_type") == "card_move"
        and record.get("to_zone", {}).get("zone") == "processing"
        and record.get("reason") == "LOSE"
    ]
    assert kouchu, f"扣置那条 card_move 应当在本窗口里：{payload['records']}"
    assert kouchu[0]["card_count"] == 1
    shown = kouchu[0]["cards"]
    assert len(shown) == 1, f"验收方要画的是**声明的牌**，实得 {shown}"
    assert shown[0]["card_id"] == slash, "牌位仍是扣置的那一张（舞台按 card_id 认项）"
    assert shown[0]["concealed"] is True
    assert shown[0]["display_name"] == "无中生有", "画的是**声明的牌名**"
    assert shown[0]["type"] == "ex_nihilo", "不是源牌的牌型（否则等于当场亮牌）"
    assert shown[0]["suit"] is None and shown[0]["rank"] is None, (
        "花色 / 点数同样是秘密（♥ 判据决定作不作废）"
    )
    reveal = [
        record
        for record in payload["records"]
        if record.get("kind") == "delta" and record.get("delta_type") == "card_reveal"
    ]
    assert reveal and reveal[0]["card"]["card_id"] == slash, (
        "同一批记录里紧接着就是公开亮出——那一张从此是公开事实，照常下发"
    )
