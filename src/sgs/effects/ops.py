"""调用型效果原语的注册表（票 09 §4 / 票 27）。

``CallOp.op`` 的取值 = 本注册表里的 **Python 函数名**：模块级、普通回调，签名统一为
``(host, frame) -> Control``，引擎只做通用派发（撞到 ``CallOp`` 就查表调用），``engine/``
里永不出现具体效果名。与票 07 的装备技能 ``CATALOG`` 同一姿态；不建参数模式 DSL，
``scalars`` 只装数字字面量。

回调可以**中途让出控制权**：提问返回 ``"stay"``（与 ``near_death_ask`` 同构，票 19 §8），
引擎把这一次效果留在帧内游标上重入；``ask`` 重入直接返回已存应答，故不会重复发问。

票 26 填了两张延时锦囊的四个回调；票 27 补齐**普通锦囊**：

- 单步：``resolve_ex_nihilo``（摸 2）、``resolve_peach_garden``（回复）、
  ``resolve_dismantlement`` / ``resolve_snatch``（选牌后弃 / 得）；
- 多步可中断：``resolve_duel``（交替打出【杀】）、``resolve_borrowed_sword``
  （持刀者使用【杀】或交出武器）、``resolve_barbarian_invasion`` / ``resolve_arrow_barrage``
  （逐目标打出【杀】/【闪】，否则受伤）；
- 五谷丰登三件套：``reveal_harvest``（B5 亮牌，走 ``timing_ops``）、``pick_harvest``
  （C4 逐目标取牌）、``finish_harvest``（使用结算结束后弃余牌）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sgs.data import Suit
from sgs.resolution import Frame
from sgs.state import (
    ActivateSkill,
    FrameKind,
    MoveReason,
    PlayCard,
    RespondMode,
    SelectionSpec,
    Slot,
    UseCard,
    WindowCause,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .behaviors import DamageNature

__all__ = ("OPS",)

LIGHTNING_DAMAGE = 3
"""【闪电】命中时的雷电伤害值（官方 `A/card_scroll.txt:217`）。"""

LIGHTNING_RANK_MIN = 2
LIGHTNING_RANK_MAX = 9
"""【闪电】命中的点数区间：♠2~9。"""

EX_NIHILO_DRAW = 2
"""【无中生有】摸牌数。"""

DUEL_DAMAGE = 1
BARBARIAN_DAMAGE = 1
ARROW_DAMAGE = 1
"""三张"否则受到 1 点伤害"的锦囊的伤害值。"""

_HAND_EQUIP_JUDGE = (Zone.HAND, Zone.EQUIP, Zone.JUDGE)

_ZONE_SELECTION_SPEC = SelectionSpec(zones=_HAND_EQUIP_JUDGE, total=(1, 1))
"""拆 / 顺的选择面规格：三区同面、取满一张（票 19 §3）。"""

_HARVEST_SELECTION_SPEC = SelectionSpec(zones=(Zone.PROCESSING,), total=(1, 1))
"""五谷丰登：从亮出的牌里取一张（处理区、可见槽）。"""


def _judge_payload(frame: Frame, *, subject: int, after: str) -> dict[str, Any]:
    """JUDGE 帧的私有载荷（票 10 §1）：``subject`` 与 ``judge_card`` **与渠道牌分列**。

    ``card`` 是从外层 RESOLVE 帧继承的渠道牌（延时锦囊本体），``judge_card`` 由
    ``judge_on`` 步骤填入；``after`` 是判定后果 op 的注册名。
    """
    return {
        "subject": subject,
        "targets": (subject,),
        "source": frame.data.get("source"),
        "card": frame.data.get("card"),
        "judge_card": None,
        "use_timing": frame.data.get("use_timing"),
        "after": after,
    }


def _open_judgment(host: Any, frame: Frame, *, after: str) -> None:
    """压出判定帧：被判定者 = 本层 RESOLVE 帧的当前目标，后果 op 按名登记。"""
    subject = host.current_target(frame)
    host.push_frame(
        FrameKind.JUDGE,
        _judge_payload(frame, subject=subject, after=after),
        timing=Timing.JUDGE_ON,
    )


def resolve_indulgence(host: Any, frame: Frame) -> None:
    """【乐不思蜀】生效后：压 JUDGE 帧，后果 op 为 :func:`after_indulgence`。"""
    _open_judgment(host, frame, after=after_indulgence.__name__)


def resolve_lightning(host: Any, frame: Frame) -> None:
    """【闪电】生效后：压 JUDGE 帧，后果 op 为 :func:`after_lightning`。"""
    _open_judgment(host, frame, after=after_lightning.__name__)


def after_skill_judgment(host: Any, frame: Frame) -> None:
    """**技能触发的判定**后果（票 10 §5.3 / 票 30）：把"红 / 黑"写回发起判定的帧。

    唯一生产者是【八卦阵】的"需要【闪】时判定"：红 ⇒ 需要被满足（视为使用【闪】）、
    黑 ⇒ 该提供者机会用尽、流程继续到真牌窗口。后果本身不在 ``effects`` 里判花色，
    只把判定牌交给引擎（``note_skill_judgment``），使"红 = 成功"这条语义与技能定义同处
    ``skills/`` 之外的中性位置。
    """
    host.note_skill_judgment(frame)


def after_indulgence(host: Any, frame: Frame) -> None:
    """【乐不思蜀】判定后果：非红桃 ⇒ 被判定者**跳过出牌阶段**（不搬牌）。"""
    judge_card = frame.data["judge_card"]
    if judge_card.suit is None or Suit(judge_card.suit) is not Suit.HEART:
        host.skip_play_phase(frame.data["subject"])


def after_lightning(host: Any, frame: Frame) -> None:
    """【闪电】判定后果：♠2~9 ⇒ 3 点**无来源**雷电伤害，本牌随后入弃牌堆；否则不动。

    "随后入弃牌堆"落在**判定阶段循环的统一收尾**（伤害帧是 JUDGE 帧的子帧，先结算完），
    故这里只把本牌标记为"已消耗"；其余情形（非 ♠2~9、以及被无懈的路径）由同一收尾
    顺延到下一个合法目标的判定区（票 11 §4）。
    """
    judge_card = frame.data["judge_card"]
    if judge_card.suit is None or Suit(judge_card.suit) is not Suit.SPADE:
        return
    if judge_card.rank is None or not LIGHTNING_RANK_MIN <= judge_card.rank <= LIGHTNING_RANK_MAX:
        return
    subject = frame.data["subject"]
    _push_damage(
        host,
        frame,
        source=None,
        target=subject,
        amount=LIGHTNING_DAMAGE,
        nature=DamageNature.THUNDER,
    )
    host.consume_delayed_card(frame)


# ---------------------------------------------------------------------------
# 普通锦囊（票 27）
# ---------------------------------------------------------------------------


def _push_damage(
    host: Any,
    frame: Frame,
    *,
    source: int | None,
    target: int,
    amount: int,
    nature: DamageNature = DamageNature.NORMAL,
) -> None:
    """压出一个 DAMAGE 子帧；渠道牌 = 外层锦囊本体（官方六要素之一）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": frame.data.get("card"),
            "nature": nature,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _zone_selection_spec() -> SelectionSpec:
    """拆 / 顺的选择面规格：三区同面、取满一张（票 19 §3）。"""
    return _ZONE_SELECTION_SPEC


def _harvest_selection_spec() -> SelectionSpec:
    """五谷丰登：从亮出的牌里取一张（处理区、可见槽）。"""
    return _HARVEST_SELECTION_SPEC


def resolve_ex_nihilo(host: Any, frame: Frame) -> None:
    """【无中生有】：当前目标摸两张牌（短摸 ⇒ 平局，与摸牌阶段同口径）。"""
    host.draw_for_hand(frame, host.current_target(frame), EX_NIHILO_DRAW)


def resolve_peach_garden(host: Any, frame: Frame) -> None:
    """【桃园结义】：当前目标回复 1 点体力。

    未受伤者已在 C1 被第 2 层"无效"终止（``effectiveness = ("wounded",)``），
    根本走不到这里，故本步不做二次判断。
    """
    target = host.current_target(frame)
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": frame.data.get("source"),
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _take_one_zone_card(
    host: Any, frame: Frame, *, key: str, owner_seat: int, timing: Timing
) -> Any:
    """在 ``owner_seat`` 的三区同面上选一张牌；池为空 ⇒ 直接返回空元组（不发询问）。

    返回 ``"stay"`` 哨兵（经 ``ask_selection`` 转成 ``SUSPEND``）时调用方据此让出控制权。
    """
    if not host.has_zone_cards(owner_seat):
        return ()
    spec = _zone_selection_spec()
    return host.ask_selection(
        frame,
        key=f"{key}:{owner_seat}",
        actor=frame.data["source"],
        timing=timing,
        spec=spec,
        build_face=lambda: host.selection_face(owner_seat, spec.zones),
    )


def _move_lost_cards(
    host: Any, frame: Frame, card_ids: tuple[int, ...], *, to_zone: ZoneRef
) -> None:
    """拆 / 顺的公共第二步：被选中的牌以 ``LOSE`` 离开原区（票 08 的来源口径）。"""
    if not card_ids:
        return
    host.move_in_frame(
        frame,
        host.state.refs(card_ids),
        to_zone=to_zone,
        reason=MoveReason.LOSE,
        owner=frame.data["source"],
        actor=frame.data["source"],
    )


def resolve_dismantlement(host: Any, frame: Frame) -> Any:
    """【过河拆桥】：当前目标**区域里的一张牌**进弃牌堆（三区同面，不另发区域询问）。"""
    target = host.current_target(frame)
    picked = _take_one_zone_card(
        host, frame, key="dismantlement", owner_seat=target, timing=Timing.RESOLVE_AFTER_EFFECT
    )
    if host.suspended(picked):
        return "stay"
    _move_lost_cards(host, frame, picked, to_zone=ZoneRef(Zone.DISCARD))
    return None


def resolve_snatch(host: Any, frame: Frame) -> Any:
    """【顺手牵羊】：当前目标的**一张牌**进使用者的手牌（距离 1 由目标规则保证）。"""
    target = host.current_target(frame)
    picked = _take_one_zone_card(
        host, frame, key="snatch", owner_seat=target, timing=Timing.RESOLVE_AFTER_EFFECT
    )
    if host.suspended(picked):
        return "stay"
    _move_lost_cards(host, frame, picked, to_zone=ZoneRef(Zone.HAND, frame.data["source"]))
    return None


def resolve_duel(host: Any, frame: Frame) -> Any:
    """【决斗】：由目标开始，双方轮流**打出【杀】**；未打出者受到对方 1 点伤害。

    交替状态存在帧私有 ``op`` 里；每次提问让出控制权后重入，一问一答地推进（票 19 §8
    的帧内游标范式）。打出【杀】走 L6 的 PLAY 帧（票 19 §2）。
    """
    source = frame.data["source"]
    op = frame.data.setdefault("op", {})
    if "duel_turn" not in op:
        op["duel_turn"] = host.current_target(frame)
    turn = op["duel_turn"]
    other = source if turn != source else host.current_target(frame)
    action = host.ask_respond(
        frame,
        turn,
        Timing.RESOLVE_AFTER_EFFECT,
        cause=WindowCause.DUEL,
        required="slash",
        target=None,
        window_frame=frame.frame_id,
    )
    if host.suspended(action):
        return "stay"
    if isinstance(action, PlayCard):
        host.play_card(action.card, source=turn, timing=Timing.PLAY_DECLARE)
        op["duel_turn"] = other
        return "stay"
    _push_damage(host, frame, source=other, target=turn, amount=DUEL_DAMAGE)
    return None


def resolve_borrowed_sword(host: Any, frame: Frame) -> Any:
    """【借刀杀人】：持刀者**对合法目标使用【杀】**，否则把武器交给使用者。

    持刀者的候选由 ``RespondMode.USE`` 生成（目标 = 其攻击范围内的合法【杀】目标）；
    主动放弃（``Pass``）即"交出装备区里的武器牌"。第 0 层已保证目标有武器。

    **Bn 由谁选（U-27-1，程序化约定）**：官方 `A/card_scroll.txt:56` 写"你在选择 An 为
    目标的同时选择……An 使用【杀】的合法目标 Bn"（B 由**使用者**指定）；M2 按票 19 §10
    不建独立的"选座位"询问，故把 Bn 烘进**持刀者**响应窗口的候选
    （``UseCard(slash, targets=(Bn,))``）。官方的第二次合法性检测因此由构造保证。
    """
    holder = host.current_target(frame)
    source = frame.data["source"]
    action = host.ask_respond(
        frame,
        holder,
        Timing.RESOLVE_AFTER_EFFECT,
        cause=WindowCause.BORROWED_SWORD,
        required="slash",
        target=holder,
        window_frame=frame.frame_id,
        mode=RespondMode.USE,
        use_targets=host.attack_range_targets(holder),
    )
    if host.suspended(action):
        return "stay"
    if isinstance(action, UseCard):
        host.start_use(
            action.card,
            source=holder,
            targets=action.targets,
            use_timing=Timing.RESOLVE_AFTER_EFFECT,
        )
        return None
    weapon = host.weapon_of(holder)
    if weapon is not None:
        host.move_in_frame(
            frame,
            (weapon,),
            to_zone=ZoneRef(Zone.HAND, source),
            reason=MoveReason.LOSE,
            owner=source,
            actor=holder,
        )
    return None


def _resist_or_damage(
    host: Any, frame: Frame, *, cause: WindowCause, required: str, amount: int
) -> Any:
    """"需打出【杀】/【闪】，否则受到使用者造成的 1 点伤害"的共用主体（南蛮 / 万箭）。

    ``target = None``：这是**打出窗口**（票 19 §3 的描述符口径），被问者由 ``actor`` 给出。
    【万箭齐发】的"需要【闪】"是票 10 §5.5 的**第二个提供者序列**：与 M1 的 ``DODGE``
    共用同一套嵌法——【八卦阵】作为 ``ActivateSkill`` 进候选、每提供者只问一次、
    选中才惰性压判定帧、红 ⇒ 视为打出【闪】、黑 ⇒ 继续到真牌窗口。
    """
    target = host.current_target(frame)
    providers_used: tuple[str, ...] = ()
    if required == "dodge":
        if "skill_result" in frame.data:
            if frame.data.pop("skill_result"):
                return None  # 红 ⇒ 视为打出【闪】，需要被满足、不受伤害
        providers_used = tuple(frame.data.get("providers_used", ()))
    action = host.ask_respond(
        frame,
        target,
        Timing.RESOLVE_AFTER_EFFECT,
        cause=cause,
        required=required,
        target=None,
        window_frame=frame.frame_id,
        providers_used=providers_used,
    )
    if host.suspended(action):
        return "stay"
    if isinstance(action, ActivateSkill):
        frame.data.setdefault("providers_used", []).append(action.skill_id)
        host.open_skill_judgment(frame, target, action.skill_id)
        return "stay"
    if isinstance(action, PlayCard):
        host.play_card(action.card, source=target, timing=Timing.PLAY_DECLARE)
        return None
    _push_damage(host, frame, source=frame.data["source"], target=target, amount=amount)
    return None


def resolve_barbarian_invasion(host: Any, frame: Frame) -> Any:
    """【南蛮入侵】：当前目标打出【杀】，否则受使用者 1 点伤害。"""
    return _resist_or_damage(
        host,
        frame,
        cause=WindowCause.BARBARIAN_INVASION,
        required="slash",
        amount=BARBARIAN_DAMAGE,
    )


def resolve_arrow_barrage(host: Any, frame: Frame) -> Any:
    """【万箭齐发】：当前目标打出【闪】，否则受使用者 1 点伤害。"""
    return _resist_or_damage(
        host,
        frame,
        cause=WindowCause.ARROW_BARRAGE,
        required="dodge",
        amount=ARROW_DAMAGE,
    )


def reveal_harvest(host: Any, frame: Frame) -> None:
    """【五谷丰登】**指定目标后**：亮出牌堆顶 X 张（X = 目标数）到处理区。

    亮牌在 B5 而非 C 段（`A/card_scroll.txt:189`）。牌堆 + 弃牌堆仍不足 X 张 ⇒
    **执行动作未完整执行**：标记 ``harvest_incomplete``，`use_after_designate_target`
    据此终止这次使用结算（**不判平局**，`A/card_scroll.txt:194`）。
    """
    targets = tuple(frame.data.get("targets", ()))
    if not targets:
        frame.data["use_terminated"] = True
        return
    revealed = host.draw_open(frame, len(targets), owner=frame.data.get("source"))
    frame.data["revealed"] = tuple(card.card_id for card in revealed)
    if len(revealed) < len(targets):
        frame.data["use_terminated"] = True


def pick_harvest(host: Any, frame: Frame) -> Any:
    """【五谷丰登】生效后：当前目标从**剩余**亮牌里取一张进手牌。

    被无效 / 被无懈而跳过者不占份额（票 15 §5）：无效者根本走不到 C4、被无懈者归票 28。
    """
    if frame.data.get("use_terminated"):
        return None
    target = host.current_target(frame)
    revealed = tuple(frame.data.get("revealed", ()))

    def build_face() -> tuple[tuple[Slot, ...], tuple[int, ...]]:
        return (
            tuple(
                Slot(ref=ZoneRef(Zone.PROCESSING), card=host.state.card(card_id))
                for card_id in revealed
            ),
            revealed,
        )

    picked = host.ask_selection(
        frame,
        key=f"harvest:{target}",
        actor=target,
        timing=Timing.RESOLVE_AFTER_EFFECT,
        spec=_harvest_selection_spec(),
        build_face=build_face,
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    chosen = picked[0]
    host.move_in_frame(
        frame,
        host.state.refs((chosen,)),
        to_zone=ZoneRef(Zone.HAND, target),
        reason=MoveReason.PUT,
        owner=target,
        actor=target,
    )
    frame.data["revealed"] = tuple(card_id for card_id in revealed if card_id != chosen)
    return None


def finish_harvest(host: Any, frame: Frame) -> None:
    """【五谷丰登】**使用结算结束后**：把亮牌中剩余的牌置入弃牌堆。"""
    revealed = tuple(frame.data.get("revealed", ()))
    remaining = tuple(card_id for card_id in revealed if card_id in host.state.processing)
    if not remaining:
        return
    host.move_in_frame(
        frame,
        host.state.refs(remaining),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=None,
        actor=frame.data.get("source"),
    )


OPS: dict[str, Callable[[Any, Frame], Any]] = {
    resolve_indulgence.__name__: resolve_indulgence,
    resolve_lightning.__name__: resolve_lightning,
    after_indulgence.__name__: after_indulgence,
    after_lightning.__name__: after_lightning,
    after_skill_judgment.__name__: after_skill_judgment,
    resolve_ex_nihilo.__name__: resolve_ex_nihilo,
    resolve_peach_garden.__name__: resolve_peach_garden,
    resolve_dismantlement.__name__: resolve_dismantlement,
    resolve_snatch.__name__: resolve_snatch,
    resolve_duel.__name__: resolve_duel,
    resolve_borrowed_sword.__name__: resolve_borrowed_sword,
    resolve_barbarian_invasion.__name__: resolve_barbarian_invasion,
    resolve_arrow_barrage.__name__: resolve_arrow_barrage,
    reveal_harvest.__name__: reveal_harvest,
    pick_harvest.__name__: pick_harvest,
    finish_harvest.__name__: finish_harvest,
}
"""操作名 → Python 回调；加载器按名做成员校验，``apply_effects`` 按名通用派发。"""
