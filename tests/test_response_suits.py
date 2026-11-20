"""**这一次使用的响应牌面收窄**（feng-guanyu 票 04；首个载体 = 锋·关羽【单骑】）。

牌面：「此牌只能被你"义云"记录的花色的牌响应」。三条判据在这里各钉一条：

1. **真牌**：响应候选只留花色落白名单的牌；空白名单（``()``）= 一张都不留（"记录为空 ⇒
   无法响应"）；``None`` = 不限（所有既有窗口）。
2. **转化候选**：按**产物花色**收窄（单源继承来源牌花色）；多源转化 / 纯虚拟牌的花色是
   ``None`` ⇒ 永远匹配不上（用户 2026-11-20 裁定："无花色的牌不算同花色，也无法响应"）。
3. **提供者**：供给**有声明**的（【八卦阵】的无色虚拟【闪】）按**产物牌面**判 ⇒ 花色需求下
   不满足、不进面（票 02 起是**算**出来的，不是"提供者一律禁"）；供给**没声明**的提供者
   （【武翊】一类）在收窄需求下同样失败关闭（票 07 修回）。不限制时逐字不变。

另外钉住"帧载荷三态"那一层：USE 帧上的 ``RESPONSE_SUITS_KEY`` 缺省不落键（既有帧载荷
逐键不变），有值时随 ``use_push_resolve`` 继承进 RESOLVE 帧，``Engine.respond_context``
从承载帧读出同一份事实。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:  # 与多数测试同一条引导（单独跑时也能 import）
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sgs.data import load_content_pack  # noqa: E402
from sgs.effects import CandidateScope, ModifierContext, legal_actions_for  # noqa: E402
from sgs.engine import Engine  # noqa: E402
from sgs.resolution import Frame  # noqa: E402
from sgs.skills.virtual import virtual_candidates  # noqa: E402
from sgs.state import (  # noqa: E402
    ActivateSkill,
    DodgeContext,
    FrameKind,
    Pass,
    PlayCard,
    QueryKind,
    RESPONSE_SUITS_KEY,
    RespondContext,
    RespondMode,
    UseCard,
    WindowCause,
)

from support import SeatSpec, build_state, cards_of_type, deck_excluding  # noqa: E402

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 张档（显式选档：模块级加载必须写死档位，票 32）。"""


def _probe_state():
    """只为读牌面：``card_id`` 就是数据表下标，与局面无关。"""
    return build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])


def _dodges_by_suit() -> dict[str, int]:
    """各花色一张【闪】的 ``card_id``（真牌响应那一趟的样本）。"""
    out: dict[str, int] = {}
    for card in _probe_state().cards:
        if card.type == "dodge" and card.suit is not None:
            out.setdefault(card.suit, card.card_id)
    return out


def _state_with_hand(hand: tuple[int, ...], *, equip: tuple[int, ...] = ()):
    used = set(hand) | set(equip)
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, equip=equip), SeatSpec()],
        deck=deck_excluding(PACK, *used),
        turn_seat=0,
    )


def _dodge_cards(state, *, response_suits) -> list[int]:
    actions = legal_actions_for(
        state,
        0,
        QueryKind.DODGE,
        answers_seq=0,
        payload=DodgeContext(response_suits=response_suits),
    )
    return [
        action.card.card_id
        for action in actions
        if isinstance(action, UseCard) and action.card.type == "dodge"
    ]


# ---------------------------------------------------------------------------
# 1. 真牌：按花色收窄；空集全禁；None 不限
# ---------------------------------------------------------------------------


def test_real_response_cards_are_narrowed_to_the_recorded_suits():
    """白名单只放行该花色的【闪】：同手牌的另一张【闪】不出现。"""
    suits = _dodges_by_suit()
    heart, diamond = suits["heart"], suits["diamond"]
    state = _state_with_hand((heart, diamond))

    assert set(_dodge_cards(state, response_suits=None)) == {heart, diamond}, (
        "不限时两张【闪】都在候选里"
    )
    assert _dodge_cards(state, response_suits=("heart",)) == [heart]
    assert _dodge_cards(state, response_suits=("diamond",)) == [diamond]
    assert _dodge_cards(state, response_suits=("club",)) == []


def test_an_empty_whitelist_blocks_every_response_and_leaves_only_pass():
    """``()`` = 记录为空 ⇒ 没有合法牌 ⇒ 候选只剩 ``Pass``（窗口按"无候选不问"收口）。"""
    suits = _dodges_by_suit()
    state = _state_with_hand((suits["heart"], suits["diamond"]))

    actions = legal_actions_for(
        state,
        0,
        QueryKind.DODGE,
        answers_seq=0,
        payload=DodgeContext(response_suits=()),
    )

    assert [type(action) for action in actions] == [Pass]


def test_the_respond_window_reads_the_same_whitelist():
    """【决斗】/无懈那一档（``RESPOND``）用同一个字段：真【杀】按花色收窄。"""
    probe = _probe_state()
    slashes: dict[str, int] = {}
    for card in probe.cards:
        if card.type == "slash" and card.suit is not None:
            slashes.setdefault(card.suit, card.card_id)
    heart, spade = slashes["heart"], slashes["spade"]
    state = _state_with_hand((heart, spade))

    ctx = RespondContext(
        cause=WindowCause.DUEL,
        required="slash",
        target=1,
        mode=RespondMode.PLAY,
        response_suits=("heart",),
    )
    actions = legal_actions_for(state, 0, QueryKind.RESPOND, answers_seq=0, payload=ctx)
    cards = [
        a.card.card_id
        for a in actions
        if isinstance(a, (UseCard, PlayCard))
    ]

    assert cards == [heart]
    ctx_unlimited = RespondContext(
        cause=WindowCause.DUEL, required="slash", target=1, mode=RespondMode.PLAY
    )
    unlimited = legal_actions_for(
        state, 0, QueryKind.RESPOND, answers_seq=0, payload=ctx_unlimited
    )
    assert {
        a.card.card_id for a in unlimited if isinstance(a, (UseCard, PlayCard))
    } == {heart, spade}


# ---------------------------------------------------------------------------
# 2. 转化候选：按**产物花色**判；无花色的产物永远匹配不上
# ---------------------------------------------------------------------------


def _conversion_actions(state, *, response_suits, sources, target="dodge"):
    ctx = ModifierContext(
        subject=0,
        owner=0,
        answers_seq=0,
        scope=CandidateScope.DODGE,
        respond_required="dodge",
        response_suits=response_suits,
    )
    return virtual_candidates(
        state,
        ctx,
        target_type_id=target,
        sources=sources,
        scopes=(CandidateScope.DODGE,),
    )


def test_conversion_candidates_are_filtered_by_the_product_suit():
    """单源产物继承来源花色 ⇒ 按它判；多源产物花色恒无 ⇒ 白名单里永远不出现。"""
    suits = _dodges_by_suit()
    heart, diamond = suits["heart"], suits["diamond"]
    state = _state_with_hand((heart, diamond))

    unlimited = _conversion_actions(
        state, response_suits=None, sources=((heart,), (diamond,), (heart, diamond))
    )
    assert len(unlimited) == 3, "不限时三种来源组合都能构造（多源那组花色为 None）"
    assert unlimited[-1].card.suit is None, "多源转化产物的花色恒为 None（§16.3）"

    restricted = _conversion_actions(
        state, response_suits=("heart",), sources=((heart,), (diamond,), (heart, diamond))
    )
    assert [action.card.suit for action in restricted] == ["heart"]
    assert restricted[0].card.virtual_sources == (heart,)


def test_a_suitless_product_never_answers_a_restricted_use_even_with_an_empty_whitelist():
    """``()`` 时连"花色为 None 的产物"也不能响应（用户 2026-11-20 的追加口径）。"""
    suits = _dodges_by_suit()
    state = _state_with_hand((suits["heart"], suits["diamond"]))
    assert (
        _conversion_actions(
            state,
            response_suits=(),
            sources=((suits["heart"],), (suits["heart"], suits["diamond"])),
        )
        == []
    )


# ---------------------------------------------------------------------------
# 3. 提供者：有声明 ⇒ 按产物牌面判；没声明 ⇒ 收窄需求下失败关闭
# ---------------------------------------------------------------------------


def test_a_suitless_provider_supply_never_answers_a_restricted_use():
    """【八卦阵】在无限制窗口里照常提供；一旦花色限制生效，它**不满足**（无花色）。

    这条钉的是**匹配器**的结论，不是"限制生效 ⇒ 所有 ``ActivateSkill`` 都不进面"：八卦阵
    的声明供给是 ``Supply(product="dodge", kind="virtual")``（花色 / 点数恒为 ``None``），
    花色需求下确定地不满足（spec §3 第 3 条）。票 03 起【护驾】/【激将】这类**有声明**的
    提供者在同一份限制下**会**进面（relay 在牌名之后不判死），故旧名字
    （``test_providers_are_fail_closed_while_the_restriction_is_active``）说的是另一件事，
    由票 07 按"名字必须说清它钉住什么"改掉；断言本身一字未改（不放宽）。
    """
    bagua = cards_of_type(PACK, "eight_diagrams")[0]
    suits = _dodges_by_suit()
    state = _state_with_hand((suits["heart"],), equip=(bagua,))

    def providers(response_suits):
        actions = legal_actions_for(
            state,
            0,
            QueryKind.DODGE,
            answers_seq=0,
            payload=DodgeContext(response_suits=response_suits),
        )
        return [action for action in actions if isinstance(action, ActivateSkill)]

    assert providers(None), "不限时【八卦阵】应该出现在【闪】候选里"
    assert providers(("heart",)) == []
    assert providers(()) == []


# ---------------------------------------------------------------------------
# 4. 帧载荷：缺省不落键；有值随 use_push_resolve 继承进 RESOLVE 帧
# ---------------------------------------------------------------------------


def test_the_use_frame_carries_the_whitelist_and_the_context_reads_it():
    """USE 帧写键（缺省不写）；``respond_context`` 从承载帧读出同一份事实。"""
    suits = _dodges_by_suit()
    state = _state_with_hand((suits["heart"],))
    engine = Engine.from_state(state, seed=5)

    plain = engine.start_use(
        state.card(state.player(0).hand[0]), source=0, targets=(1,)
    )
    assert RESPONSE_SUITS_KEY not in plain.data, "缺省不落键 ⇒ 既有帧载荷逐键不变"

    restricted = engine.start_use(
        state.card(state.player(0).hand[0]),
        source=0,
        targets=(1,),
        response_suits=("heart",),
    )
    assert restricted.data[RESPONSE_SUITS_KEY] == ("heart",)

    # RESOLVE 帧的载荷 = ``dict(USE 帧 data)``（``steps.use_push_resolve``）⇒ 同一份事实。
    resolve = Frame(kind=FrameKind.RESOLVE, data=dict(restricted.data))
    ctx = engine.respond_context(
        resolve, 1, cause=WindowCause.DUEL, required="slash"
    )
    assert ctx.response_suits == ("heart",)

    empty = Frame(kind=FrameKind.RESOLVE, data={RESPONSE_SUITS_KEY: ()})
    assert (
        engine.respond_context(empty, 1, cause=WindowCause.DUEL, required="slash")
        .response_suits
        == ()
    )
    assert engine.respond_context(plain, 1, cause=WindowCause.DUEL).response_suits is None
