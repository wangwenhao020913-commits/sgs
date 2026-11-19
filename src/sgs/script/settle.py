"""结算：奖励发落 / `pick k` / **Boss 结算顺序** / 失败三态（M7 票 17）。

依据 **票 03 §4（奖励与失败）**、**票 02 §3（抽卡与兜底）、§8（Boss 顺序）、§9（失败）**。

结算**不再取随机**：该节点的一切随机在进入时已物化进 ``pending.spec``（票 04 §0 纪律 2），
这里只是"按 spec 发账"。于是"续档不重抽"是**同义反复**，而不是一条靠纪律维持的承诺。

一次结算 = 一个 :class:`SettleResult`：**有序的事件序列** + 新的 ``RunState``（``None`` =
整轮结束）。事件序列是钉住顺序的手段——Boss 房的顺序用**事件序列**断言，不用"最终状态
相等"糊过去（票 17 的验收）。

Boss 房（``is_boss``）的顺序**钉死**（D18 / 票 02 §8）：

1. 先**失去本场 4 条出战技能**（从 run 彻底删掉；池里仍在，以后还能再抽到）——**数据关不掉**；
2. **两次三选一**（每轮 3 张候选）；
3. **1 件遗物**（无货 ⇒ 折 :data:`sgs.data.RELIC_PRICE` 铜钱）；
4. **15 铜钱**；
5. 再追加房间的 ``extra_reward``（铜钱 + 它的 ``pick k`` 轮次）。

非 Boss 战斗：**全队模板奖励累加**（敌方座位的模板；票 03 §4）+ 房间 ``extra_reward``。

失败三态（票 02 §8、§9）：

- ``kind: story`` 或 ``is_boss`` ⇒ **整轮结束**（``run=None``，调用方删档），不发奖；
- ``kind: battle`` ⇒ 该节点 ``closed``（不可重打）、整轮继续、**失去一半铜钱**（向下取整）、
  不发奖。

铜钱记账的口径：**整笔收支**各发一条 :class:`CoinsGained` / :class:`CoinsLost`（模板累加 /
Boss 15 / ``extra_reward`` / 事件 / 失败扣半 / 商店付款）；**单步折价**（池空 3、重复或满包
折稀有度价、遗物无货折 10）记在那一步自己的事件里（:class:`PickOffered.coins` /
:class:`RelicOffered.coins`），不另发一条——否则同一笔钱在事件序列里出现两次。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sgs.data import (
    RARITY_PRICES,
    RELIC_PRICE,
    CoinsEffect,
    GrantRelicEffect,
    GrantSkillEffect,
    Layer,
    Node,
    Reward,
    Room,
    RoomKind,
    Script,
    ScriptLibrary,
    SetFlagsEffect,
    SkillValueTable,
    skill_value,
)

from . import lookup, rewards
from .board import next_layer_index, requirement_met
from .errors import ScriptError
from .state import (
    BACKPACK_SLOTS,
    MAX_RELICS,
    Backpack,
    BattleSpec,
    EventSpec,
    Id,
    NodeStatus,
    RunState,
    ShopSpec,
    UnknownSpec,
    equipped_skills,
    progress_key,
)

__all__ = [
    "CoinsGained",
    "CoinsLost",
    "FlagsSet",
    "LoseEquipped",
    "NodeClosed",
    "PickOffered",
    "RelicOffered",
    "RelicsGained",
    "RunEnded",
    "SettleEvent",
    "SettleOrder",
    "SettleResult",
    "SkillsGained",
    "settle_node",
]

CoinReason = Literal["rewards", "boss", "extra", "event", "convert"]
"""铜钱收入的来由（``convert`` = 重复持有 / 满包 / 池空这类折价）。"""


class _Frozen(BaseModel):
    """本模块模型的共同纪律：不可变、无多余字段（拼错的键当场判红）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# 结算事件（一次结算 = 一条有序序列；结算弹窗与序列断言都读它）
# ---------------------------------------------------------------------------


class LoseEquipped(_Frozen):
    """Boss 房第一步：失去本场 4 条出战技能（``skills`` 可能为空）。"""

    kind: Literal["lose_equipped"] = "lose_equipped"
    skills: tuple[Id, ...]


class PickOffered(_Frozen):
    """一次 `k 选 1`（``candidates`` 空 = 池空；``chosen`` 空 = 放弃）。"""

    kind: Literal["pick"] = "pick"
    round: int
    """第几轮（整场结算内 0 起，与 ``spec.rewards`` 的下标一一对应）。"""

    candidates: tuple[Id, ...]
    chosen: Id | None
    coins: int = 0
    """这一轮折成的铜钱：池空 = 3；重复持有 / 满包 = 该技能的稀有度价；放弃 = 0。"""


class RelicOffered(_Frozen):
    """Boss 房第三步：那 1 件遗物（``relic=None`` ⇒ 无货，折 10 铜钱）。"""

    kind: Literal["relic"] = "relic"
    relic: Id | None
    coins: int = 0


class SkillsGained(_Frozen):
    """获得技能（进背包的第一个空格，即出战槽优先，票 02 §2）。"""

    kind: Literal["skills"] = "skills"
    skills: tuple[Id, ...]


class RelicsGained(_Frozen):
    """获得遗物（进遗物栏，≤6）。"""

    kind: Literal["relics"] = "relics"
    relics: tuple[Id, ...]


class FlagsSet(_Frozen):
    """设 run 级标签（房间 ``on_clear`` / 事件选项）。"""

    kind: Literal["flags"] = "flags"
    flags: tuple[Id, ...]


class CoinsGained(_Frozen):
    """一笔铜钱收入（模板累加 / Boss 15 / ``extra_reward`` / 事件 / 折价）。"""

    kind: Literal["coins"] = "coins"
    amount: int
    reason: CoinReason


class CoinsLost(_Frozen):
    """一笔铜钱支出（失败扣半 / 事件花费 / 商店付款）。"""

    kind: Literal["coins_lost"] = "coins_lost"
    amount: int


class NodeClosed(_Frozen):
    """意外战败：该节点关闭（不可重打），整轮继续。"""

    kind: Literal["node_closed"] = "node_closed"
    node_id: str


class RunEnded(_Frozen):
    """整轮结束（通关 / 剧情战败 / Boss 战败）：调用方据此删档。"""

    kind: Literal["run_ended"] = "run_ended"
    reason: Literal["cleared", "story_loss", "boss_loss"]


SettleEvent = Annotated[
    CoinsGained
    | CoinsLost
    | FlagsSet
    | LoseEquipped
    | NodeClosed
    | PickOffered
    | RelicOffered
    | RelicsGained
    | RunEnded
    | SkillsGained,
    Field(discriminator="kind"),
]
"""结算事件的封闭集（按 ``kind`` 判别）：每种对应结算弹窗里的一个块。"""


class SettleOrder(_Frozen):
    """一次结算的输入：赢没赢 + 三选一的逐轮选择 + 事件选项 + 商店购买。

    ``won`` 只对战斗房有意义（其余房间忽略）；``picks`` / ``option`` / ``purchases``
    各自只属于一种房间，**多传即拒**（结算指令的形状由房间类型决定）。
    """

    won: bool = True
    """战斗房的胜负：由服务端从记录流 + ``solve_pve_outcome`` 判，**不接受客户端自报**。"""

    picks: tuple[Id | None, ...] = ()
    """每轮选中的技能 id（``None`` = 放弃）；短于轮次数 ⇒ 缺的那些按放弃算。"""

    option: int | None = None
    """事件选项下标（``None`` = 没选：事件节点必须选一个才谈得上结算）。"""

    purchases: tuple[Id, ...] = ()
    """商店购买清单（按顺序结算；同一件买两次 ⇒ 拒）。"""


class SettleResult(_Frozen):
    """一次结算的结果：有序事件 + 新的 run（``None`` = 整轮结束）。"""

    events: tuple[SettleEvent, ...]
    run: RunState | None
    outcome: Literal["continue", "cleared", "failed"]
    """``continue`` = 整轮继续；``cleared`` = 通关；``failed`` = 整轮失败。"""


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def settle_node(
    *,
    run: RunState,
    library: ScriptLibrary,
    value_table: SkillValueTable,
    order: SettleOrder | None = None,
) -> SettleResult:
    """结算 ``run.pending`` 里的那个节点（战斗 / 事件 / 商店 / 问号房揭晓后的内层）。

    :raises ScriptError: 没有待结算的节点 / 快照与 run 对不上 / 选项或购买不合法 /
        铜钱不够 / 结算指令与房间类型对不上。
    """
    request = SettleOrder() if order is None else order
    if run.pending is None:
        raise ScriptError(
            rule_id="script.settle.no_pending",
            loc=run.script_id,
            reason="没有待结算的节点：结算只发生在进入节点之后（票 04 §2 的 S_done）",
        )
    script = lookup.script_of(library, run.script_id)
    layer = lookup.layer_of(script, run.layer_index)
    node = lookup.pending_node_of(layer, run.pending)
    room = lookup.room_of(library, node.room)
    spec = run.pending.spec
    if isinstance(spec, UnknownSpec):
        room = lookup.room_of(library, spec.revealed.room)
        spec = spec.revealed.spec
    ledger = _Ledger(run=run, value_table=value_table)
    if isinstance(spec, BattleSpec):
        return _settle_battle(
            ledger=ledger,
            room=room,
            spec=spec,
            order=request,
            script=script,
            layer=layer,
            node=node,
            library=library,
        )
    if isinstance(spec, EventSpec):
        return _settle_event(
            ledger=ledger, spec=spec, order=request, script=script,
            layer=layer, node=node, room=room, library=library,
        )
    if isinstance(spec, ShopSpec):
        return _settle_shop(
            ledger=ledger, spec=spec, order=request, script=script,
            layer=layer, node=node, room=room,
        )
    raise ScriptError(
        rule_id="script.settle.unknown_spec",
        loc=run.pending.node_id,
        reason=f"pending.spec 的 kind = {spec.kind!r} 不在可结算的四类里",
    )


# ---------------------------------------------------------------------------
# 战斗：胜利发奖 / 失败三态
# ---------------------------------------------------------------------------


def _settle_battle(
    *,
    ledger: "_Ledger",
    room: Room,
    spec: BattleSpec,
    order: SettleOrder,
    script: Script,
    layer: Layer,
    node: Node,
    library: ScriptLibrary,
) -> SettleResult:
    _reject_foreign_fields(order, picks=True)
    if not order.won:
        return _settle_defeat(ledger=ledger, room=room, node=node, layer=layer, order=order)

    battle = room.battle
    assert battle is not None, "只有带 battle 载荷的房间会被编译 / 结算成一场战斗"
    if room.is_boss:
        lost = equipped_skills(ledger.run.backpack)
        ledger.drop_equipped()
        ledger.events.append(LoseEquipped(skills=lost))
        mine = (rewards.BOSS_PICK_SIZE,) * rewards.BOSS_PICK_ROUNDS
        template_reward = Reward()
    else:
        template_reward = rewards.enemy_reward(
            enemy_templates=spec.enemy_templates, library=library
        )
        mine = rewards.round_sizes(template_reward)
    extra = rewards.round_sizes(battle.extra_reward)
    choices = _choices(order.picks, len(mine) + len(extra))

    index = 0
    if room.is_boss:
        for _ in range(len(mine)):
            ledger.pick(index=index, candidates=_candidates(spec, index), choice=choices[index])
            index += 1
        ledger.offer_boss_relic(spec.boss_relic)
        ledger.coins_gained(rewards.BOSS_COINS, "boss")
    else:
        ledger.coins_gained(template_reward.coins, "rewards")
        for _ in range(len(mine)):
            ledger.pick(index=index, candidates=_candidates(spec, index), choice=choices[index])
            index += 1
    ledger.coins_gained(battle.extra_reward.coins, "extra")
    for _ in range(len(extra)):
        ledger.pick(index=index, candidates=_candidates(spec, index), choice=choices[index])
        index += 1

    return _finish(ledger=ledger, script=script, layer=layer, node=node, room=room)


def _candidates(spec: BattleSpec, index: int) -> tuple[Id, ...]:
    """第 ``index`` 轮的候选（spec 里更短 = 空轮 ⇒ 结算时折 3 铜钱）。"""
    if index < len(spec.rewards):
        return spec.rewards[index]
    return ()


def _choices(picks: Sequence[Id | None], rounds: int) -> tuple[Id | None, ...]:
    """把玩家给的选择补齐到轮次数（缺的按放弃算）；多给 ⇒ 拒。"""
    if len(picks) > rounds:
        raise ScriptError(
            rule_id="script.settle.too_many_picks",
            loc=f"picks[{rounds}]",
            reason=f"本场只有 {rounds} 轮 k 选 1，收到 {len(picks)} 个选择",
        )
    return tuple(picks) + (None,) * (rounds - len(picks))


def _settle_defeat(
    *, ledger: "_Ledger", room: Room, node: Node, layer: Layer, order: SettleOrder
) -> SettleResult:
    """失败三态（票 02 §8、§9）：剧情 / Boss ⇒ 整轮结束；意外战斗 ⇒ 关节点 + 扣半。"""
    if order.picks:
        raise ScriptError(
            rule_id="script.settle.picks_on_defeat",
            loc=progress_key(layer.id, node.id),
            reason="战败不发奖励（票 02 §9）：这一场没有 k 选 1 可选",
        )
    key = progress_key(layer.id, node.id)
    if room.kind is RoomKind.STORY or room.is_boss:
        ledger.events.append(RunEnded(reason="boss_loss" if room.is_boss else "story_loss"))
        return SettleResult(events=tuple(ledger.events), run=None, outcome="failed")
    status = dict(ledger.run.node_status)
    status[key] = NodeStatus.CLOSED
    lost = ledger.coins // 2
    ledger.events.append(NodeClosed(node_id=key))
    if lost:
        ledger.coins -= lost
        ledger.events.append(CoinsLost(amount=lost))
    run = ledger.run.model_copy(
        update={"node_status": status, "coins": ledger.coins, "pending": None}
    )
    return SettleResult(events=tuple(ledger.events), run=run, outcome="continue")


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------


def _settle_event(
    *,
    ledger: "_Ledger",
    spec: EventSpec,
    order: SettleOrder,
    script: Script,
    layer: Layer,
    node: Node,
    room: Room,
    library: ScriptLibrary,
) -> SettleResult:
    _reject_foreign_fields(order, option=True)
    event = lookup.event_of(library, spec.event_id)
    if order.option is None:
        raise ScriptError(
            rule_id="script.settle.event_option_required",
            loc=spec.event_id,
            reason=f"事件 {spec.event_id!r} 必须选一个选项才能结算",
        )
    if not 0 <= order.option < len(event.options):
        raise ScriptError(
            rule_id="script.settle.event_option_out_of_range",
            loc=f"{spec.event_id}/options[{order.option}]",
            reason=(
                f"事件 {spec.event_id!r} 只有 {len(event.options)} 个选项："
                f"下标 {order.option} 越界"
            ),
        )
    option = event.options[order.option]
    if not requirement_met(option.requires, ledger.flags):
        raise ScriptError(
            rule_id="script.settle.event_option_locked",
            loc=f"{spec.event_id}/options[{order.option}]",
            reason=(
                f"选项 {order.option} 的标签条件不成立（需要 "
                f"{list(option.requires.flags)}、不能有 {list(option.requires.not_flags)}）"
            ),
        )
    for effect in option.effects:
        if isinstance(effect, CoinsEffect):
            if effect.amount >= 0:
                ledger.coins_gained(effect.amount, "event")
            else:
                ledger.spend(-effect.amount)
        elif isinstance(effect, SetFlagsEffect):
            ledger.set_flags(effect.flags)
        elif isinstance(effect, GrantSkillEffect):
            ledger.offer_skill(effect.skill)
        elif isinstance(effect, GrantRelicEffect):
            ledger.offer_relic(effect.relic)
    return _finish(ledger=ledger, script=script, layer=layer, node=node, room=room)


# ---------------------------------------------------------------------------
# 商店
# ---------------------------------------------------------------------------


def _settle_shop(
    *,
    ledger: "_Ledger",
    spec: ShopSpec,
    order: SettleOrder,
    script: Script,
    layer: Layer,
    node: Node,
    room: Room,
) -> SettleResult:
    _reject_foreign_fields(order, purchases=True)
    bought: list[Id] = []
    for item in order.purchases:
        if item in bought:
            raise ScriptError(
                rule_id="script.settle.duplicate_purchase",
                loc=item,
                reason=f"同一件东西只买一次：{item!r} 已经在这一次的购买清单里",
            )
        if item in spec.stock.skills:
            ledger.buy_skill(item)
        elif item in spec.stock.relics:
            ledger.buy_relic(item)
        else:
            raise ScriptError(
                rule_id="script.settle.not_in_stock",
                loc=item,
                reason=f"{item!r} 不在这个商店的库存里（库存进档、售完不补货，票 03 §1）",
            )
        bought.append(item)
    return _finish(ledger=ledger, script=script, layer=layer, node=node, room=room)


# ---------------------------------------------------------------------------
# 完成节点：状态 / 标签 / 大节推进 / 通关
# ---------------------------------------------------------------------------


def _finish(*, ledger: "_Ledger", script: Script, layer: Layer, node: Node, room: Room) -> SettleResult:
    """把"这个节点走完了"一次落：``node_status`` / 房间标签 / 大节推进。"""
    key = progress_key(layer.id, node.id)
    status = dict(ledger.run.node_status)
    status[key] = NodeStatus.COMPLETED
    ledger.set_flags(room.on_clear.set_flags)
    following = next_layer_index(script, layer_index=ledger.run.layer_index, node_status=status)
    run = ledger.run.model_copy(
        update={
            "node_status": status,
            "flags": tuple(ledger.flags),
            "coins": ledger.coins,
            "backpack": Backpack(
                slots=tuple(ledger.slots),
                equipped_slots=ledger.run.backpack.equipped_slots,
            ),
            "relics": tuple(ledger.relics),
            "pending": None,
            "layer_index": ledger.run.layer_index if following is None else following,
        }
    )
    if following is None:
        ledger.events.append(RunEnded(reason="cleared"))
        return SettleResult(events=tuple(ledger.events), run=None, outcome="cleared")
    return SettleResult(events=tuple(ledger.events), run=run, outcome="continue")


# ---------------------------------------------------------------------------
# 内部：指令形状 / 账本
# ---------------------------------------------------------------------------


def _reject_foreign_fields(
    order: SettleOrder, *, picks: bool = False, option: bool = False, purchases: bool = False
) -> None:
    """拒掉"这种房间没有这个字段"的输入：结算指令与房间类型必须对得上。"""
    wrong = [
        name
        for name, given, allowed in (
            ("picks", bool(order.picks), picks),
            ("option", order.option is not None, option),
            ("purchases", bool(order.purchases), purchases),
        )
        if given and not allowed
    ]
    if wrong:
        raise ScriptError(
            rule_id="script.settle.unexpected_choice",
            loc=",".join(wrong),
            reason=f"这个节点的结算不接受 {wrong}（房间类型决定结算指令的形状）",
        )


class _Ledger:
    """结算过程中的可变账本：铜钱 / 背包格 / 遗物 / 标签 / 事件序列。

    结算结束才用 :meth:`RunState.model_copy` 落回不可变状态——中间态不外泄，
    于是"结算到一半"这个概念只活在这一个对象里。
    """

    def __init__(self, *, run: RunState, value_table: SkillValueTable) -> None:
        self.run = run
        self.value_table = value_table
        self.slots: list[str | None] = list(run.backpack.slots)
        self.coins = run.coins
        self.relics: list[str] = list(run.relics)
        self.flags: list[str] = list(run.flags)
        self.events: list[Any] = []

    # -- 基本读写 ---------------------------------------------------------

    def held(self) -> tuple[str, ...]:
        return tuple(slot for slot in self.slots if slot is not None)

    def owns(self, skill_id: str) -> bool:
        return skill_id in self.held()

    def free_slot(self) -> int | None:
        for index, slot in enumerate(self.slots):
            if slot is None:
                return index
        return None

    def price_of(self, skill_id: str) -> int:
        return RARITY_PRICES[skill_value(self.value_table, skill_id).rarity]

    def coins_gained(self, amount: int, reason: CoinReason) -> None:
        if amount == 0:
            return
        self.coins += amount
        self.events.append(CoinsGained(amount=amount, reason=reason))

    def spend(self, amount: int) -> None:
        if amount == 0:
            return
        if amount > self.coins:
            raise ScriptError(
                rule_id="script.settle.not_enough_coins",
                loc=f"coins={self.coins}",
                reason=f"铜钱不够：要花 {amount}，身上只有 {self.coins}",
            )
        self.coins -= amount
        self.events.append(CoinsLost(amount=amount))

    def set_flags(self, flags: Sequence[str]) -> None:
        fresh = [flag for flag in flags if flag not in self.flags]
        if not fresh:
            return
        self.flags.extend(fresh)
        self.events.append(FlagsSet(flags=tuple(fresh)))

    # -- 奖励落袋 ---------------------------------------------------------

    def drop_equipped(self) -> None:
        """Boss 第一步：把出战槽里的技能从 run 里**彻底删掉**（池里仍在）。"""
        for index in self.run.backpack.equipped_slots:
            self.slots[index] = None

    def pick(self, *, index: int, candidates: Sequence[Id], choice: Id | None) -> None:
        """一次 `k 选 1`：池空折 3、可放弃、重复持有 / 满包折该技能的稀有度价。"""
        if not candidates:
            self.coins += rewards.EMPTY_POOL_COINS
            self.events.append(
                PickOffered(
                    round=index, candidates=(), chosen=None, coins=rewards.EMPTY_POOL_COINS
                )
            )
            return
        if choice is None:
            self.events.append(PickOffered(round=index, candidates=tuple(candidates), chosen=None))
            return
        if choice not in candidates:
            raise ScriptError(
                rule_id="script.settle.pick_not_offered",
                loc=f"round={index}",
                reason=f"第 {index} 轮没有 {choice!r} 这张候选：可选的只有 {list(candidates)}",
            )
        coins = self._hand_over_skill(choice)
        self.events.append(
            PickOffered(round=index, candidates=tuple(candidates), chosen=choice, coins=coins)
        )

    def offer_boss_relic(self, relic: Id | None) -> None:
        """Boss 第三步：那 1 件遗物（无货 ⇒ 折 10 铜钱）。

        遗物本身由 :class:`RelicOffered` 这一条事件表达，**不**再另发
        :class:`RelicsGained`（同 :class:`PickOffered` 带 ``chosen`` 的口径）：否则同一件
        遗物在事件序列里出现两次，顺序断言会变成"数两条"。
        """
        if relic is None:
            self.coins += RELIC_PRICE
            self.events.append(RelicOffered(relic=None, coins=RELIC_PRICE))
            return
        self.relics.append(relic)
        self.events.append(RelicOffered(relic=relic))

    def offer_skill(self, skill_id: str) -> None:
        """事件给的技能：进包（`SkillsGained`）；重复持有 / 满包 ⇒ 折稀有度价（票 02 §3）。"""
        coins = self._hand_over_skill(skill_id)
        if coins:
            self.events.append(CoinsGained(amount=coins, reason="convert"))
            return
        self.events.append(SkillsGained(skills=(skill_id,)))

    def offer_relic(self, relic_id: str) -> None:
        """事件给的遗物：重复或满栏 ⇒ 丢弃（遗物**不可**折铜钱，票 02 §4）。"""
        if relic_id in self.relics or len(self.relics) >= MAX_RELICS:
            return
        self.relics.append(relic_id)
        self.events.append(RelicsGained(relics=(relic_id,)))

    def buy_skill(self, skill_id: str) -> None:
        if self.owns(skill_id):
            raise ScriptError(
                rule_id="script.settle.already_owned",
                loc=skill_id,
                reason=f"{skill_id!r} 已经在背包里：商店不卖已拥有（票 02 §3）",
            )
        if self.free_slot() is None:
            raise ScriptError(
                rule_id="script.settle.backpack_full",
                loc=skill_id,
                reason="背包 24 格全满：满包时商店技能置灰（票 02 §5 的护栏）",
            )
        self.spend(self.price_of(skill_id))
        self._append_skill(skill_id)
        self.events.append(SkillsGained(skills=(skill_id,)))

    def buy_relic(self, relic_id: str) -> None:
        if relic_id in self.relics:
            raise ScriptError(
                rule_id="script.settle.already_owned",
                loc=relic_id,
                reason=f"{relic_id!r} 已经在遗物栏里：商店不卖已拥有",
            )
        if len(self.relics) >= MAX_RELICS:
            raise ScriptError(
                rule_id="script.settle.relic_slots_full",
                loc=relic_id,
                reason=f"遗物栏上限 {MAX_RELICS} 件：装不下了（票 02 §6）",
            )
        self.spend(RELIC_PRICE)
        self.relics.append(relic_id)
        self.events.append(RelicsGained(relics=(relic_id,)))

    # -- 内部 -------------------------------------------------------------

    def _hand_over_skill(self, skill_id: str) -> int:
        """收下一条技能：装得下就进背包，装不下就折成它的稀有度价、返回折到的铜钱。"""
        if self.owns(skill_id) or self.free_slot() is None:
            coins = self.price_of(skill_id)
            self.coins += coins
            return coins
        self._append_skill(skill_id)
        return 0

    def _append_skill(self, skill_id: str) -> None:
        index = self.free_slot()
        assert index is not None, "只有确认过空格才走到这里"
        assert len(self.slots) == BACKPACK_SLOTS
        self.slots[index] = skill_id
