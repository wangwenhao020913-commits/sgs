"""参与者视角投影：``project(state, seat) -> PlayerView``（票 05 §5）。

纯函数、每次构造询问时现算、不缓存；内部一律按座位序输出，不依赖任何无序容器的迭代。
身份可见性也在这里落地（票 13 §6）：投影时就把不可见的身份抹成 ``None``，
使"身份不泄露"是结构事实而不是约定。

ai-view 票 06 起，**公开面**也在这里进视角：``project()`` 把
``GameState.public_surface`` 的两份**有界**数据（聚合计数 + 最近 K 条事实）拷成
``PlayerView.public_counts`` / ``public_history``，并在拷贝时把载荷裁成**公开形态**
（隐藏牌面 / 牌堆顺序 / 未亮明的身份 / 隐藏步骤的答案值都留在引擎侧）。
裁剪的四个判据各有唯一真源：区域公开性（既有区域规则）、票 12 的持久公开事实、本投影的
``_visible_identity``、票 05 的可见性表——这里只做汇总，不另立规则。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from sgs.data import CardTagType, Identity

from .deltas import CardMoveDelta
from .models import (
    CardRef,
    FactKind,
    GameState,
    MoveReason,
    Player,
    PublicFact,
    Zone,
    fact_class,
)
from .protocol import (
    ActivateSkill,
    PlayerPublic,
    PlayerView,
    PublicEvent,
    PublicMove,
)

__all__ = ["project"]


def _visible_identity(player: Player, *, viewer: int) -> Identity | None:
    """``viewer`` 能看到 ``player`` 的身份吗（票 13 §6）。

    可见 ⇔ 本人 ∨ 主公（开局立即亮明）∨ **已亮明字段为真**（U1-04）∨ 已死亡
    （阵亡即亮明）；其余存活者不可见。

    主公与阵亡仍按既有口径派生（字段是 U1-04 新增的"亮明"入口，不替换它们）；字段让
    M5 的亮明效果只需写状态、不用改投影。
    """
    if (
        player.seat == viewer
        or player.identity is Identity.LORD
        or player.identity_visible
        or not player.alive
    ):
        return player.identity
    return None


def _hero_zone_facts(
    state: GameState, player: Player, *, with_faces: bool
) -> tuple[int, tuple[CardRef, ...]]:
    """一个座位武将牌上的 ``(公开张数, 本人牌面)``（票 27）。

    - **张数公开**：同一座位多个放置技能实例的区**求和**（跨座位的区算宿主）；
    - **牌面只给宿主**：与 M6 的 2026-10 口径一致
      （``tools/sgs_web/visibility.py::hero_zone_ref_visible``），未登记 / 畸形的区同一默认；
      他人视角只拿张数（``with_faces=False``，不构造 ``CardRef``）。
    - 区之间**没有规则先后**：这里按区键排序只为可重现，顺序不是契约（AI 只数张数）。
    """
    count = 0
    cards: list[CardRef] = []
    for skill_instance in sorted(player.hero_cards):
        zone = state.hero_zone(player.seat, skill_instance)
        count += len(zone)
        if with_faces:
            cards.extend(state.refs(zone))
    return count, tuple(cards)


_PUBLIC_MOVE_ZONES = frozenset({Zone.DISCARD, Zone.EQUIP, Zone.JUDGE})
"""**牌面进出即公开**的区域（区域规则，不是新规则）：弃牌堆 / 装备区 / 判定区。

牌堆 / 手牌 / 武将牌上的牌区都不是公开区：前两个只给张数，第三个只给宿主
（2026-10 口径，见 ``tools/sgs_web/visibility.py::hero_zone_ref_visible`` 与本模块的
``_hero_zone_facts``）。``processing`` 也不是"一律公开"：扣置进去的牌由票 12 的持久公开
事实回答（见 :func:`_move_faces_public`）。

这一档是**状态侧**的区域规则（与接入层那份同源、但**不含查看者洞**）：公开面是"场面
上所有人都能感知的事实"，不是"某个座位看得到什么"，故本人手牌 / 宿主视角那两档只活在
``tools/sgs_web/visibility.py``（网页下发）与 ``self_hand`` / ``hero_zone_cards``。
"""


class _PayloadRule(str, Enum):
    """一条**变化事实**的载荷在视图里的处置（ai-view 票 06；闭集四值）。"""

    PUBLIC = "public"
    """载荷本身是公开事实（体力 / 护甲 / 横置 / 亮牌 / 拼点 / 回合切换…）——照发。"""

    MOVE = "move"
    """牌移动：牌面按区域 + 票 12 的公开事实**逐张**裁（见 :func:`_move_faces_public`）。"""

    IDENTITY = "identity"
    """身份发放：按查看者裁（本人 / 主公 / 已亮明 / 阵亡可见，见 :func:`_visible_identity`）。"""

    WITHHELD = "withheld"
    """整个载荷不下发（牌堆顺序 / 标签可见性逐类型声明）；事实本身仍可读。"""


_DELTA_PAYLOAD_RULES: dict[str, _PayloadRule] = {
    # 牌移动与身份发放各有一档自己的规则；其余明确"公开"。
    "card_move": _PayloadRule.MOVE,
    "identity_deal": _PayloadRule.IDENTITY,
    # 洗牌：载荷是**洗后牌序**，只给引擎（票 04 的 ``deck_shuffle`` 口径）。
    "deck_shuffle": _PayloadRule.WITHHELD,
    # 标签增删：载荷带牌面，而标签可见性是逐类型声明（HIDDEN / FACE_VISIBLE / PUBLIC）；
    # 本投影不重复实现那份裁剪 ⇒ 一律不给载荷。
    "tag_add": _PayloadRule.WITHHELD,
    "tag_remove": _PayloadRule.WITHHELD,
    # 以下载荷本身是公开事实（亮牌 / 拼点 / 体力 / 护具 / 标记 / 技能三态 / 回合切换…）。
    "armor_change": _PayloadRule.PUBLIC,
    "card_reveal": _PayloadRule.PUBLIC,
    "chained_change": _PayloadRule.PUBLIC,
    "flipped_change": _PayloadRule.PUBLIC,
    "hp_change": _PayloadRule.PUBLIC,
    "hp_max_change": _PayloadRule.PUBLIC,
    "identity_reveal": _PayloadRule.PUBLIC,
    "marker_change": _PayloadRule.PUBLIC,
    "pindian": _PayloadRule.PUBLIC,
    "skill_gain": _PayloadRule.PUBLIC,
    "skill_lose": _PayloadRule.PUBLIC,
    "skill_suppress": _PayloadRule.PUBLIC,
    "turn_change": _PayloadRule.PUBLIC,
    "wine_change": _PayloadRule.PUBLIC,
}
"""``delta_type -> 载荷处置``（ai-view 票 06）：**闭集登记，未登记即 KeyError**。

与票 04 的 ``_DELTA_SUBJECT_FIELD`` 同一姿态：新增 delta 类型必须在这里想清"它的载荷能不能
进公开面"，而不是靠"默认照发"悄悄流进视图（``tests/test_public_view.py`` 与
``sgs.state.deltas`` 的模型集**双向**核对）。措辞**不写语义**：``PUBLIC`` 说的是"这条事实
在桌面上可见"，不是"这条事实对谁有利"。
"""


def _move_faces_public(
    move: CardMoveDelta,
    card_id: int,
    *,
    public_faces: frozenset[int],
    concealed: frozenset[int],
) -> bool:
    """一张牌在这次移动里**牌面是否公开**（ai-view 票 06 的唯一判据）。

    两件事按顺序回答，**不另写第二套区域规则**：

    1. 票 12 的**交换两腿**（``HAND → PROCESSING, LOSE`` 扣置 / ``PROCESSING → HAND, PUT``
       回手）读同一份**持久公开事实**（``CardTagType.PUBLIC_FACE``）——交换的牌面公开性
       不是区域给的，故这里不看不问区；
    2. 其余按**区域**：进 / 出公开区 ⇒ 牌面公开；只进出隐藏区 ⇒ 只给张数。
       进处理区的其余入口（使用 / 装备 / 判定 / 亮牌）是公开的；**扣置**的那几张按
       :attr:`PlayerView.processing_concealed`（引擎按移动形状维护的同一份事实）。
       离开处理区时若公开性读不到（集合在离开那一刻就清了），**fail-closed**：不给牌面。

    与接入层的**已知差别**（方向是"少给"，不是"多给"）：票 12 的回手腿在接入层读
    ``processing_entry_faces``（记录流重建的"进区那刻是否公开"，含"在处理区里被
    ``card_reveal`` 亮出"这一档）。投影不读记录流，故那一档**读不到 ⇒ 按不公开处理**；
    当前没有'扣置后被亮出、再经回手腿进手牌'的流程（【蛊惑】验明后直接进弃牌堆）。
    牌面公开性只会从隐藏变公开、不会反向，故这条差别不会让视图多出任何一张牌面。

    入参都是**投影期**的读数（``state`` 的一格 / 引擎传进来的集合），故这是纯函数——
    同一次 ``project`` 调用的两个座位拿到同一份裁剪结果。
    """
    from_zone = move.from_zone
    to_zone = move.to_zone
    reason = move.reason
    if to_zone.zone is Zone.PROCESSING:
        if from_zone.zone is Zone.HAND and reason is MoveReason.LOSE:
            return card_id in public_faces
        return card_id not in concealed
    if to_zone.zone in _PUBLIC_MOVE_ZONES:
        return True
    if from_zone.zone is Zone.PROCESSING:
        if to_zone.zone is Zone.HAND and reason is MoveReason.PUT:
            return card_id in public_faces
        return False
    return from_zone.zone in _PUBLIC_MOVE_ZONES


def _move_public_cards(
    payload: CardMoveDelta,
    *,
    public_faces: frozenset[int],
    concealed: frozenset[int],
) -> tuple[tuple[CardRef, ...], int]:
    """一次移动里**牌面公开**的牌 + 扣置张数（ai-view 票 06）。

    虚拟牌（转化 / 声称的产物）按**来源实体牌**判：来源不公开 ⇒ 这张虚拟牌整个不给
    （它的花色 / 点数是从来源继承的，给出去等于泄露来源）；来源公开 ⇒ 给产物**但剥掉**
    ``virtual_sources``——"由哪几张实体牌变来"不进视图（声称类的真值正住在这里）。
    """
    cards: list[CardRef] = []
    hidden = 0
    for card in payload.cards:
        sources = tuple(int(source) for source in card.virtual_sources)
        if sources:
            public = all(
                _move_faces_public(
                    payload, source, public_faces=public_faces, concealed=concealed
                )
                for source in sources
            )
            if public:
                cards.append(card.model_copy(update={"virtual_sources": ()}))
            else:
                hidden += 1
            continue
        if _move_faces_public(
            payload, int(card.card_id), public_faces=public_faces, concealed=concealed
        ):
            cards.append(card)
        else:
            hidden += 1
    return tuple(cards), hidden


def _public_move(
    payload: CardMoveDelta,
    *,
    public_faces: frozenset[int],
    concealed: frozenset[int],
) -> PublicMove:
    """一条 ``card_move`` 事实的公开形态（ai-view 票 06）。"""
    cards, hidden = _move_public_cards(
        payload, public_faces=public_faces, concealed=concealed
    )
    return PublicMove(
        reason=payload.reason,
        from_zone=payload.from_zone,
        to_zone=payload.to_zone,
        cards=cards,
        hidden_count=hidden,
    )


def _answer_is_hidden(fact: PublicFact) -> bool:
    """这条步骤答案的边界值登记为 ``HIDDEN`` 吗（票 05 的表；结果在**写点**已解析）。

    ``sgs.state`` 是 ``sgs.skills`` 的下层（子包边界纪律禁止深层跨子包 import），故
    "读表"住在引擎的写点（``Engine._note_public_answer`` → ``PublicFact.answer_hidden``），
    投影只读这一格——裁剪的**决定**仍只有一处（票 05 的表），只是读它的人是引擎。
    """
    return bool(fact.answer_hidden)


def _public_payload(
    fact: PublicFact,
    *,
    seat: int,
    state: GameState,
    public_faces: frozenset[int],
    concealed: frozenset[int],
) -> object:
    """把一条公开事实的载荷裁成**公开形态**（ai-view 票 06）。

    裁掉的是**只有引擎 / 记录流才有的那一半**：隐藏牌面、牌堆顺序、未亮明的身份、
    隐藏步骤的答案值。四者的判据各有唯一来源（区域公开性 / 票 12 的公开事实 / 本投影的
    ``_visible_identity`` / 票 05 的可见性表），这里只做汇总。
    """
    payload = fact.payload
    if fact.kind is FactKind.ANSWER:
        if _answer_is_hidden(fact):
            return None
        if isinstance(payload, ActivateSkill) and payload.picks:
            # 预发动 / 复合动作的 ``picks`` 是**实体牌 id**（W4-1），普通发动里是面内位置
            # ——两者在载荷上不可分，故一律裁掉；``choices`` 是选项 id，照发。
            return payload.model_copy(update={"picks": ()})
        return payload
    # 变化事实按**登记表**裁：未登记的类型 KeyError（fail-closed，新增 delta 必须先想清
    # "它的载荷能不能进公开面"，不能静默流进视图）。
    rule = _DELTA_PAYLOAD_RULES[payload.delta_type]
    if rule is _PayloadRule.MOVE:
        return _public_move(payload, public_faces=public_faces, concealed=concealed)
    if rule is _PayloadRule.IDENTITY:
        player = state.player(int(payload.seat))
        visible = _visible_identity(player, viewer=seat)
        return payload if visible == payload.identity else None
    if rule is _PayloadRule.WITHHELD:
        return None
    return payload


def _public_event(
    fact: PublicFact,
    *,
    seat: int,
    state: GameState,
    public_faces: frozenset[int],
    concealed: frozenset[int],
) -> PublicEvent:
    """把缓冲里的一条事实投影成视图事件（锚点 / 类别 / 起因照搬，载荷裁一层）。"""
    return PublicEvent(
        seq=fact.seq,
        kind=fact.kind,
        fact_class=fact_class(fact),
        actor=fact.actor,
        targets=fact.targets,
        window=fact.window,
        cause=fact.cause,
        payload=_public_payload(
            fact,
            seat=seat,
            state=state,
            public_faces=public_faces,
            concealed=concealed,
        ),
    )


def project(
    state: GameState,
    seat: int,
    *,
    concealed_processing: Iterable[int] = (),
) -> PlayerView:
    """投影出 ``seat`` 这一参与者能看到的棋盘信息。

    武将 id 与势力是**公开信息**（标准版武将牌明置；`04`（§2「信息可见性」）），故逐座位照搬；
    ``identity`` 仍走 :func:`_visible_identity` 的过滤（票 02 §可见性 8：过滤逻辑一字不改）。

    武将牌上的牌（票 27）：逐座位给**张数**（公开，守恒量的一项）；牌面只给查看者自己当
    宿主的区（:func:`_hero_zone_facts`），与 M6 的 2026-10 口径一致。两者都 ``exclude``，
    不进记录流。

    被废除的区域（lei 票 05）：``sealed_zones`` 照搬状态里的闭集（公开事实，不做可见性
    过滤）；它同样 ``exclude``，由 ``sgs_web.projection.visible_snapshot`` 补进网页载荷。

    ``concealed_processing``（ai-view 票 12）是**处理区里牌面不公开**的牌 id，由引擎按
    **移动形状**维护（``Engine.processing_concealed``）：交换 / 扣置进去的那几张。它只
    抄成 :attr:`PlayerView.processing_concealed`（``exclude``、不进记录流），
    ``processing`` 本身仍是全量——本票不改 AI 的读点 / 守恒量。纯状态参数：投影不读
    记录流，也不为它做任何判定（形状规则只有一处，住引擎的移动入口）。

    公开面（ai-view 票 06）：``public_counts`` / ``public_history`` 是
    :attr:`GameState.public_surface` 的两份**有界**切片，拷进视图时载荷先过
    :func:`_public_payload` 的公开性裁剪——**投影是裁剪的唯一时机**（票 04 的缓冲保持为
    原始事实面）。四个判据各有唯一真源，这里只做汇总：区域公开性 + 票 12 的持久公开事实
    （牌面）、**牌堆顺序不下发**（``deck_shuffle``）、:func:`_visible_identity`（身份）、
    票 05 的可见性表（步骤答案）。
    """
    hero_zone_facts = {
        player.seat: _hero_zone_facts(
            state, player, with_faces=player.seat == seat
        )
        for player in state.players
    }
    players = tuple(
        PlayerPublic(
            seat=player.seat,
            hp=player.hp,
            hp_max=player.hp_max,
            armor=player.armor,
            hand_count=len(player.hand),
            alive=player.alive,
            equip=state.refs(player.equip),
            judge=state.refs(player.judge),
            sealed_zones=player.sealed_zones,
            hero_zone_count=hero_zone_facts[player.seat][0],
            chained=player.chained,
            flipped=player.flipped,
            markers=player.markers,
            wine_active=player.wine_active,
            identity=_visible_identity(player, viewer=seat),
            hero=player.hero,
            kingdom=player.kingdom,
        )
        for player in state.players
    )
    self_hand = state.refs(state.player(seat).hand)
    # 排序只为可重现（集合无序；契约是"哪些牌"，不是顺序）。只收**此刻真的在处理区**里的
    # 牌：字段名字就是它的契约（终局清栈等直调 `move_card` 的收尾不会再回写集合，这里把
    # "处理区里不存在"的残留挡在视图外）。
    in_processing = set(state.processing)
    processing_concealed = tuple(
        sorted(
            {
                int(card_id)
                for card_id in concealed_processing
                if int(card_id) in in_processing
            }
        )
    )
    surface = state.public_surface
    # 牌面公开性的两份**投影期读数**：票 12 的持久公开事实（明置）与只有处理区才读的
    # 扣置集合。两者都只读一次、逐条事实复用（``_move_faces_public`` 是纯函数）。
    public_faces = frozenset(
        int(card_id)
        for card_id in state.card_tags
        if state.has_card_tag(int(card_id), CardTagType.PUBLIC_FACE)
    )
    concealed = frozenset(processing_concealed)
    return PlayerView(
        seat=seat,
        players=players,
        self_hand=self_hand,
        deck_count=len(state.deck),
        discard=state.refs(state.discard),
        processing=state.refs(state.processing),
        turn_seat=state.turn_seat,
        phase=state.phase,
        hero_zone_cards=hero_zone_facts[seat][1],
        processing_concealed=processing_concealed,
        public_counts=tuple(surface.counts),
        public_history=tuple(
            [
                _public_event(
                    fact,
                    seat=seat,
                    state=state,
                    public_faces=public_faces,
                    concealed=concealed,
                )
                for fact in surface.recent
            ]
        ),
    )
