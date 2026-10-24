"""叶子原语：只改状态、返回 delta，**绝不**压栈 / 发询问 / 暂停 / 访问结算栈（票 07 §1）。

两个口：``move_card``（移动牌）与 ``apply_hp``（写体力，唯一改体力的口）。
洗牌是"明确的随机置换"，走 ``deck_shuffle`` 记录而不是 ``move_card``（票 08 §5）：
``draw_from_deck`` 在牌堆耗尽时把弃牌堆重洗成新牌堆，并把这次置换作为 delta 交回调用方。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sgs.state import (
    CardMoveDelta,
    CardRef,
    DeckShuffleDelta,
    GameState,
    HpChangeDelta,
    MoveReason,
    SlotKind,
    Zone,
    ZoneRef,
)

__all__ = [
    "DrawResult",
    "EffectContext",
    "Randomness",
    "apply_hp",
    "draw_from_deck",
    "locate",
    "move_card",
    "place_on_deck",
    "substitute_judge_card",
    "swap_judge_card",
]


class Randomness(Protocol):
    """唯一随机源暴露给效果层的最小接口（票 08 §2）；效果层不 import ``random``。"""

    def shuffle(self, items: list[int]) -> None: ...

    def randbelow(self, n: int) -> int: ...

    def randint(self, a: int, b: int) -> int: ...


@dataclass(frozen=True, slots=True)
class EffectContext:
    """一次结算的主体与层级信息；原语只读它，不改它。"""

    actor: int
    source: int | None = None
    targets: tuple[int, ...] = ()
    card: CardRef | None = None
    frame_id: int = 0
    depth: int = 0
    rng: Randomness | None = None


@dataclass(frozen=True, slots=True)
class DrawResult:
    """一次摸牌的结果：自顶向下的牌面事实 + 途中最多一次重洗（票 08 §4 / 票 23）。

    ``shuffle`` 非空时调用方必须先把它 emit 成一条 ``deck_shuffle`` 记录，
    再做这张/这些牌的 ``move_card``——记录流里的次序因此与真实发生次序一致。
    """

    cards: tuple[CardRef, ...]
    shuffle: DeckShuffleDelta | None = None


def locate(state: GameState, card_id: int) -> ZoneRef:
    """找出这张牌现在的**区域坐标**（一张实体牌任一时刻只在一个区域）。"""
    if card_id in state.deck:
        return ZoneRef(Zone.DECK)
    for player in state.players:
        if card_id in player.hand:
            return ZoneRef(Zone.HAND, player.seat)
        if card_id in player.equip:
            return ZoneRef(Zone.EQUIP, player.seat)
        if card_id in player.judge:
            return ZoneRef(Zone.JUDGE, player.seat)
        # 武将牌上的牌是**真区域**（M6 票 10）：坐标带 （座位, 技能实例），故逐区找，
        # 同一座位的两个放置区不会互相遮挡。
        for instance, zone in player.hero_cards.items():
            if card_id in zone.cards:
                return ZoneRef(Zone.HERO, player.seat, instance)
    if card_id in state.discard:
        return ZoneRef(Zone.DISCARD)
    if card_id in state.processing:
        return ZoneRef(Zone.PROCESSING)
    raise ValueError(f"牌 {card_id} 不在任何区域；牌守恒被破坏")


def _place_into_equipment(state: GameState, ref: ZoneRef, cards: tuple[CardRef, ...]) -> None:
    """把装备牌放进目标座位的装备区，按槽位枚举序保持列表序（票 08 §2 / 票 12 §Q2）。

    "同槽至多一张"本身是**规则层**的候选过滤（`17.10` 明文"不是合法目标"）；
    这里只加一条**编程错误兜底**：一般"置入"撞已占槽位即抛——替换必须由调用方
    先把旧牌移出（`4.10` 的替换是合法行为，但它是"新牌 PUT + 旧牌 LOSE"两条移动）。
    """
    if ref.seat is None:
        raise ValueError("装备区按座位分装，区域坐标必须带座位")
    equip = state.player(ref.seat).equip
    occupied = [state.card(card_id).slot for card_id in equip]
    for card in cards:
        if card.slot is None:
            raise ValueError(f"牌 {card.card_id} 没有装备槽位，不能置入装备区")
        if card.slot in occupied:
            raise ValueError(
                f"装备槽位 {card.slot.value} 已有牌；替换必须由调用方先移出旧牌"
            )
        occupied.append(card.slot)
    equip.extend(card.card_id for card in cards)
    order = list(SlotKind)
    equip.sort(key=lambda card_id: order.index(state.card(card_id).slot))


def move_card(
    state: GameState,
    ctx: EffectContext,
    cards: tuple[CardRef, ...],
    *,
    to_zone: ZoneRef,
    reason: MoveReason,
    owner: int | None,
) -> CardMoveDelta:
    """把一批牌从各自（同一个）来源区域移到目标区域坐标，返回一条变化事件。

    ``owner`` 是这次移动的**归属角色**（判定牌 = 被判定者、弃置 / 使用 = 弃置者 /
    使用者、摸牌 = 摸牌者、装备置入 = 持有者）；"无角色主体"的移动（如终局收尾）
    传 ``None``。``ctx`` 只提供结算主体信息，本原语不读结算栈。
    """
    card_ids = tuple(card.card_id for card in cards)
    if not card_ids:
        raise ValueError("空的一批牌不构成一次移动操作")

    source = locate(state, card_ids[0])
    for card_id in card_ids[1:]:
        if locate(state, card_id) != source:
            raise ValueError("一次 move_card 只能有一个来源区域")

    source_list = state.zone(source)
    # 成员判定用元组而非 set：本项目禁一切无序容器参与顺序（票 08 §7）。
    source_list[:] = [card_id for card_id in source_list if card_id not in card_ids]

    if source.zone is Zone.JUDGE:
        # 离开判定区即清掉"这张实体牌代表哪张虚拟延时锦囊"的映射（【国色】）：映射只在
        # 判定阶段被读一次，留着会让同一张实体牌日后被误判成那张虚拟牌。
        for card_id in card_ids:
            state.virtual_judge.pop(card_id, None)

    if to_zone.zone is Zone.EQUIP:
        _place_into_equipment(state, to_zone, cards)
    else:
        state.zone(to_zone).extend(card_ids)
    if to_zone.zone is Zone.DISCARD:
        # 本回合"置入弃牌堆的牌含哪几种花色"（M6 票 11 的【博图】判据）：记在**唯一的搬牌
        # 原语**里，而不是各调用点自己记——裁定是"计入本回合**所有来源**置入的牌"
        # （`06`（§5「程序化约定与偏离一手」）），任何一条没接上的来源都会让技能静默不
        # 发动。花色与来源 / 原因无关。
        state.note_discard_suits(cards)

    return CardMoveDelta(
        cards=cards,
        from_zone=source,
        to_zone=to_zone,
        reason=reason,
        owner=owner,
    )


def _place_new_judge_card(
    state: GameState, ctx: EffectContext, new_card: CardRef, subject: int
) -> CardMoveDelta:
    """改判的公共第一步：改判牌进处理区、归属 = 被判定者（票 03 §4 的"新牌→PROCESSING"）。"""
    return move_card(
        state,
        ctx,
        (new_card,),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.JUDGE,
        owner=subject,
    )


def substitute_judge_card(
    state: GameState,
    ctx: EffectContext,
    *,
    old_card: CardRef,
    new_card: CardRef,
    subject: int,
) -> tuple[CardMoveDelta, CardMoveDelta]:
    """改判「**代替**」：新判定牌进处理区、原判定牌**当刻**进弃牌堆（票 03 §4 / 票 10 §4）。

    两条移动各发一条 ``card_move``（来源区不同，一次 ``move_card`` 只能有一个来源）；
    二者都取 ``reason = JUDGE``、归属 = **被判定者**，与判定牌的两条移动同口径。
    """
    incoming = _place_new_judge_card(state, ctx, new_card, subject)
    outgoing = move_card(
        state,
        ctx,
        (old_card,),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.JUDGE,
        owner=subject,
    )
    return (incoming, outgoing)


def swap_judge_card(
    state: GameState,
    ctx: EffectContext,
    *,
    old_card: CardRef,
    new_card: CardRef,
    subject: int,
    rejudger: int,
) -> tuple[CardMoveDelta, CardMoveDelta]:
    """改判「**替换**」：新判定牌进处理区、原判定牌归**改判者**（票 03 §4 / 票 10 §4）。

    与 :func:`substitute_judge_card` 的唯一差别是原判定牌的去向（改判者手牌而非弃牌堆），
    故其 ``reason`` 取 ``LOSE``（判定区 / 处理区里的牌被移走）、归属 = 改判者。
    """
    incoming = _place_new_judge_card(state, ctx, new_card, subject)
    outgoing = move_card(
        state,
        ctx,
        (old_card,),
        to_zone=ZoneRef(Zone.HAND, rejudger),
        reason=MoveReason.LOSE,
        owner=rejudger,
    )
    return (incoming, outgoing)


def apply_hp(state: GameState, ctx: EffectContext, seat: int, value: int) -> HpChangeDelta:
    """把座位 ``seat`` 的体力**写成** ``value``，返回一条变化事件（唯一改体力的口）。

    ``value`` 由调用帧算好（扣减帧 = ``hp - amount``；回复帧 = ``min(hp + amount, hp_max)``）；
    本原语只做**上限**检查——越过体力上限是编程错误，帧必须先夹住。

    **下限不在这里**：官方明说"角色的体力有可能小于 0"，体力降至 0 即进入濒死结算，
    是规则上的中间态（濒死流程归票 21）。因此这里允许写到 0 以下；调用方不得据此
    猜测"死亡"，死亡由濒死 → 死亡事件裁定。
    """
    player = state.player(seat)
    if value > player.hp_max:
        raise ValueError(f"体力 {value} 超过上限 {player.hp_max}；回复帧必须先夹住")
    before = player.hp
    player.hp = value
    return HpChangeDelta(seat=seat, before=before, after=value, amount=value - before)


def place_on_deck(
    state: GameState, cards: tuple[CardRef, ...], *, to_top: bool
) -> None:
    """把一批牌**置于牌堆顶 / 底**（`03` §5「效果原语（Effect Primitives）清单」的原语；票 10）。

    ``to_top=True`` ⇒ 这批牌成为新的牌堆顶，**元组顺序即自顶向下**（``cards[0]`` 在最上）；
    ``to_top=False`` ⇒ 依次接在**原有牌堆底之下**（追加到 ``state.deck`` 尾部），
    故"先给的仍在上面"。"底"取"原有牌堆底**之下**"这一读法，见票 10 §验收 1。

    **返回 ``None``，不产出 delta**：这批牌本来就是牌堆顶（"观看牌堆顶"只看不移），
    排列是**同一区域内部**的重排、不是六区之间的移动。若为此发一条带 ``cards`` 的
    ``card_move``，被授予的牌面就会随记录流泄露给其它座位/复盘者（票 10 §验收 2、
    `spec.md` 用户故事 47）；这次排列的记录面因此只有 `ArrangeCards` 行动本身——
    它带的是**面内位置**，牌面只出现在被授予的那一次询问里。

    ``cards`` 为空是合法的（切分点落在两端）：什么都不做。
    """
    if not cards:
        return
    card_ids = tuple(card.card_id for card in cards)
    for card_id in card_ids:
        if card_id not in state.deck:
            raise ValueError(f"牌 {card_id} 不在牌堆里，不能置于牌堆顶 / 底")
    state.deck[:] = [card_id for card_id in state.deck if card_id not in card_ids]
    if to_top:
        state.deck[:0] = card_ids
    else:
        state.deck.extend(card_ids)


def draw_from_deck(state: GameState, n: int, rng: Randomness) -> DrawResult:
    """自牌堆顶取至多 ``n`` 张；牌堆耗尽而弃牌堆非空时先重洗，再继续（票 08 §4 / 票 23）。

    返回的 ``cards`` 是自顶向下的牌面事实，**不改这批牌的位置**（由 ``move_card`` 真正移动）；
    ``shuffle`` 是途中发生的那次重洗（每取一张前检查，故一次调用最多一次），
    记录内容 = 洗后新牌堆的顺序，**不记洗前**，也**不**另发 ``card_move``（票 08 §5）。

    返回长度 ``< n`` 就是"短摸"信号：需要摸牌而两堆皆空（票 08 §6）。
    """
    if n < 0:
        raise ValueError(f"摸牌张数不能为负，得到 {n!r}")
    taken: list[int] = []
    shuffle: DeckShuffleDelta | None = None
    while len(taken) < n:
        if len(taken) < len(state.deck):
            # 已取的那些牌还在 state.deck 的前缀里（尚未 move_card 走），故游标即 len(taken)。
            taken.append(state.deck[len(taken)])
            continue
        if not state.discard:
            break
        shuffle = _reshuffle_into_deck(state, rng)
    return DrawResult(cards=state.refs(taken), shuffle=shuffle)


def _reshuffle_into_deck(state: GameState, rng: Randomness) -> DeckShuffleDelta:
    """把弃牌堆洗混后接到牌堆尾，返回一条 ``deck_shuffle``（规则 10.4 / 票 08 §5）。

    已经摸出的牌仍在 ``state.deck`` 的前缀里，故**只**置换新并入的那一段：
    记录里的 ``cards`` 因此恰是洗后的**新牌堆**顺序；``move_card`` 随后移走前缀，
    牌堆剩下的就是这段新牌堆减去本次继续摸走的那几张。
    """
    shuffled = list(state.discard)
    rng.shuffle(shuffled)
    state.discard.clear()
    state.deck.extend(shuffled)
    return DeckShuffleDelta(
        cards=state.refs(shuffled),
        from_zone=ZoneRef(Zone.DISCARD),
        to_zone=ZoneRef(Zone.DECK),
    )
