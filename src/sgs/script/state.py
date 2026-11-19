"""``RunState``：一轮剧本的全部可变事实（M7 票 16；依据票 04 §1 的字段表）。

**纯数据**：无时钟、无 I/O、无派生值。``available_next`` / 入口 / 出口 / Boss 结算顺序 /
遗物与出战技能的战斗效果全部**现算**（住 :mod:`sgs.script.board` 或票 17 的编译 / 结算），
落盘只写这里的事实——落盘的派生值会变成第二份真相（票 04 §0 纪律 1）。

字段与"谁写谁读"逐字段照票 04 §1：

============================  =========================================  ==================
字段                          形状                                       谁写 / 谁读
============================  =========================================  ==================
``run_id``                    ``"<seed>-<n>"``                           建档 / 寻址与陈旧判定
``revision``                  ``int``（0 起，每次真写 +1）               存档 I/O / 乐观锁
``seed``                      ``int``（∈ [0, 2**64)）                   建档 / 审计
``script_id`` · ``_version``   ``str`` · ``int``                          建档 / 展示与审计
``script_digest``             ``str``（``sha256:…``）                    建档 / 审计，不拒绝
``roster_candidates``         ``hero_id × 3``（模型放宽到 1..3）          建档 / 展示
``chosen_hero``               ``hero_id``                                建档（玩家选）
``layer_index``               ``int``                                    节点结算 / 棋盘
``flags``                     ``[str]``                                  节点结算 / 边 ``requires``
``coins``                     ``int``                                    结算 / 商店、HUD
``backpack``                  ``{slots[24], equipped_slots = 前 4 格}``    结算 / 背包端点
``relics``                    ``[skill_id × ≤6]``                        结算 / 每场开局
``node_status``               ``{"<layer_id>/<node_id>": 状态}``          结算 / 可到达集
``pending``                   ``null`` 或 ``{node_id, spec}``            进入节点 / 恢复点
============================  =========================================  ==================

两条**读法**（票面没写死、本模块按下列口径钉住，见票 16 的 ``## Answer``）：

1. **``revision`` / ``layer_index`` / ``coins`` / ``flags`` / ``node_status`` 有缺省**：
   建档那一刻这些字段是"零"，显式写出来只会让建档端点重复模型默认值。
2. **模型只钉形状，不钉语义**：``chosen_hero`` 是否在候选里、背包是否重复持有同一技能、
   ``node_status`` 的键是否指向真实节点，都是**调用方**（建档 / 背包端点 / 加载器）的判据。
   这里越权判红的话，票 17 / 19 的中间态就没法构造。

``pending.spec`` 是**该节点全部随机结果的物化**（票 04 §0 纪律 2）：进入节点时一次抽完、
节点内不再取随机。五选一见 :data:`PendingSpec` 与它下面的四个模型。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sgs.data import Identity, PveConfig

__all__ = [
    "BACKPACK_SLOTS",
    "EQUIPPED_SLOT_COUNT",
    "MAX_RELICS",
    "MAX_ROSTER_CANDIDATES",
    "REWARD_OPTION_COUNT",
    "Revealed",
    "SEED_LIMIT",
    "SHOP_RELIC_OFFERS",
    "SHOP_SKILL_OFFERS",
    "Backpack",
    "BattleSpec",
    "EventSpec",
    "NodeStatus",
    "Pending",
    "PendingSpec",
    "RevealedSpec",
    "RunState",
    "ShopSpec",
    "ShopStock",
    "UnknownSpec",
    "equipped_skills",
    "held_skills",
    "progress_key",
]

BACKPACK_SLOTS: Final = 24
"""技能背包的格数（票 02 §3）：前 :data:`EQUIPPED_SLOT_COUNT` 格 = 出战槽。"""

EQUIPPED_SLOT_COUNT: Final = 4
"""出战槽数：武将牌上的 4 条出战技能由这几格决定（票 02 §3、§8）。"""

MAX_RELICS: Final = 6
"""遗物栏上限（票 02 §5）：遗物各自独立生效、不算武将牌上的技能。"""

MAX_ROSTER_CANDIDATES: Final = 3
"""初始角色候选数（票 04 §1 的 ``×3``）：剧本库的 ``roster`` 恒为 3 名，抽样不放宽。"""

SEED_LIMIT: Final = 1 << 64
"""run seed 的值域上界（引擎边界校验 ``[0, 2**64)``，见票 03 的事实复核）。"""

SHOP_SKILL_OFFERS: Final = 6
"""商店的货架：6 个技能（票 03 §1；售完不补货）。"""

SHOP_RELIC_OFFERS: Final = 2
"""商店的货架：2 件遗物。"""

REWARD_OPTION_COUNT: Final = 3
"""一次三选一的候选张数（票 02 §3 / 票 04 §1 的 ``[[skill_id × ≤3], …]``）。"""

Id = Annotated[str, Field(min_length=1)]
"""引用用的稳定 id：非空字符串（与 ``sgs.data.scripts.Id`` 同口径，不反向依赖它）。"""


class _Frozen(BaseModel):
    """本模块所有模型的共同纪律：不可变、无多余字段（拼错的键当场判红）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class NodeStatus(str, Enum):
    """节点进度（票 04 §1）：只有"已完成 / 已关闭"两种**落地状态**。

    "当前位置"不是状态，是 :attr:`RunState.pending`——票 04 §1 的「唯一的当前」。
    """

    COMPLETED = "completed"
    """已结算（战斗胜利 / 事件走完 / 商店离开 / 问号房走完）。"""

    CLOSED = "closed"
    """遭遇战败 ⇒ 该节点关闭（不可重打、整轮继续、扣一半铜钱；票 03 §4）。"""


def progress_key(layer_id: str, node_id: str) -> str:
    """存档进度键 ``"<layer_id>/<node_id>"``（票 03 §8.2 / 票 04 §7 的示例）。

    ``Node.id`` 是**层内**编号，故 ``node_status`` 的键与 ``pending.node_id`` 都带层前缀；
    同一个函数管两头，免得两处各拼一次前缀。
    """
    return f"{layer_id}/{node_id}"


class Backpack(_Frozen):
    """技能背包（票 02 §3）：24 格的**按格内容**，前 4 格是出战槽。

    存"按格内容"而不是一坨列表：交换 / 移动 / 丢弃后的位置要原样恢复（票 02 追加 §1）。
    同一技能不可重复持有是**调用方**的判据（见模块文档读法 3），模型只管格子与槽。
    """

    slots: Annotated[
        tuple[Id | None, ...],
        Field(min_length=BACKPACK_SLOTS, max_length=BACKPACK_SLOTS),
    ]
    """24 格；``None`` = 空格（格数钉死，位置才有意义）。"""

    equipped_slots: Annotated[
        tuple[int, ...],
        Field(min_length=EQUIPPED_SLOT_COUNT, max_length=EQUIPPED_SLOT_COUNT),
    ] = (0, 1, 2, 3)
    """4 个出战槽的**格号**：恒为前 4 格（票 02 §3、票 35 收紧）。"""

    @model_validator(mode="after")
    def _the_equipped_slots_are_the_front_four(self) -> "Backpack":
        """出战槽恒为**前 4 格** ``(0, 1, 2, 3)``（票 02 §3；spec「前 4 格是出战槽」）。

        UI 从不移动出战槽（票 22 M7-22c：交换 / 移动只动 ``slots``，``equipped_slots`` 不动），
        而 :func:`equipped_skills` 按 ``equipped_slots`` 的**次序**读出战技能——放行别的四格
        或换序，等于让一次写请求悄悄改掉技能的出战顺序。越界 / 重复也因此一并被这条判据拒掉。
        """
        if self.equipped_slots != (0, 1, 2, 3):
            raise ValueError(
                "出战槽恒为前 4 格 (0, 1, 2, 3)："
                f"实得 {list(self.equipped_slots)}"
            )
        return self


def equipped_skills(backpack: Backpack) -> tuple[str, ...]:
    """出战槽里**有内容**的那几条（按 ``equipped_slots`` 的次序，跳过空格）。

    它是"这一场武将牌上多出来的技能"（票 02 §2：只有出战槽里的 4 条进战斗）与
    ``PveConfig.grants`` 的**唯一**读法：编译（票 17）与 Boss 的"失去 4 条出战技能"
    都经它读，免得两处各写一份"哪几格算出战"。
    """
    return tuple(
        skill_id
        for skill_id in (backpack.slots[index] for index in backpack.equipped_slots)
        if skill_id is not None
    )


def held_skills(backpack: Backpack) -> tuple[str, ...]:
    """整包里**有内容**的技能（含出战槽，按格子顺序）。

    学习池的减法（票 02 §3 的"背包 ∪ 出战"）读它：出战槽本来就在包里，所以一处就够。
    """
    return tuple(skill_id for skill_id in backpack.slots if skill_id is not None)


# ---------------------------------------------------------------------------
# pending.spec：该节点全部随机结果的物化（票 04 §1 的表，五选一）
# ---------------------------------------------------------------------------


class BattleSpec(_Frozen):
    """战斗节点的物化结果（剧情 / 意外 / Boss 共用一份形状）。

    ``rewards`` 的第 i 项 = 第 i 次 k 选 1 的候选（``pick k`` 累加后的总次数）；
    候选 ≤3 张（满包 / 池不足的兜底在票 17）。
    """

    kind: Literal["battle"]
    battle_seed: int = Field(ge=0, lt=SEED_LIMIT)
    """该场战斗的种子（票 04 §1 改判为**存**：冻结值比每次重算更抗代码变动）。"""

    pve: PveConfig
    """冻结的 PvE 配置（座位 / 胜负 / 授予 / 全局技能都在里面，票 01 §2）。"""

    rewards: tuple[Annotated[tuple[Id, ...], Field(max_length=REWARD_OPTION_COUNT)], ...] = ()
    """逐次三选一的候选项（``[[skill_id × ≤3], …]``）。"""

    enemy_templates: tuple[Id, ...] = ()
    """本场敌方座位各自的**模板 id**（按敌方座位序）。

    它进 ``pending`` 而不是每次从房间重算：按档抽的敌人是**抽出来的**（``script.draw:<节点>``），
    结算时的"全队模板奖励累加"（票 03 §4）要能读到同一批模板。固定敌人也照填——
    两种敌人来源在结算侧只有一种读法。
    """

    boss_relic: Id | None = None
    """Boss 房结算时**自动发**的那 1 件遗物（票 02 §8）。

    ``None`` = 无货 ⇒ 结算折 :data:`sgs.data.RELIC_PRICE` 铜钱；非 Boss 房恒 ``None``。
    它在进入节点时抽定（"进入节点 = 抽完"，票 04 §0 纪律 2），结算不再取随机。
    """

    @model_validator(mode="after")
    def _enemy_templates_line_up_with_the_seats(self) -> "BattleSpec":
        """填了就必须**逐座对齐**敌方座位（空 = 没填：非战斗来源的合成 spec）。

        错位一格会把奖励算到别人头上，故长度必须自证；``pve`` 的座位表与阵营表由
        ``PveConfig`` 自己保证等长，这里只补"模板数 = 敌方座位数"。
        """
        if not self.enemy_templates:
            return self
        expected = sum(
            1 for identity in self.pve.identities if identity is Identity.ENEMY
        )
        if len(self.enemy_templates) != expected:
            raise ValueError(
                f"enemy_templates 的长度必须等于敌方座位数：实得 "
                f"{len(self.enemy_templates)} vs {expected}"
            )
        return self


class EventSpec(_Frozen):
    """事件节点的物化结果（票 03 §5）：抽到的事件 + 后果里的随机。

    ``outcome_rolls`` 的键是**选项下标**、值是该选项后果的随机结果；载荷形状由票 17
    定义（本票只钉"抽完即物化、续档不重抽"这一层）。
    """

    kind: Literal["event"]
    event_id: Id
    outcome_rolls: dict[int, Any] = Field(default_factory=dict)


class ShopStock(_Frozen):
    """商店库存（票 03 §1）：6 技能 + 2 遗物，售完不补货。

    只钉**上限**：价值表里可卖的条目不足 6 / 2 时库存可以更短（"有几张给几张"，
    票 02 §3 的口径），生成目标仍是 :data:`SHOP_SKILL_OFFERS` / :data:`SHOP_RELIC_OFFERS`。
    """

    skills: Annotated[tuple[Id, ...], Field(max_length=SHOP_SKILL_OFFERS)] = ()
    relics: Annotated[tuple[Id, ...], Field(max_length=SHOP_RELIC_OFFERS)] = ()


class ShopSpec(_Frozen):
    """商店节点的物化结果（票 04 §1）：库存生成一次、进档、重进不刷新。"""

    kind: Literal["shop"]
    stock: ShopStock


class RevealedSpec(_Frozen):
    """问号房**揭晓后的内层**（票 03 §2 / 票 04 §1 的 ``revealed``）。

    票 04 §1 写的是 ``revealed:<内层 spec>``——这里多一个 ``room``：内层社区要"按内层房间
    的流程走"（票 22）、结算要按房间语义分（Boss / 剧情失败 / 文案），而内层 spec 本身
    （战斗种子 / 事件 id / 库存）**认不出**抽中的是哪个房间。故揭晓结果 = 房间 id + 物化
    spec 两件套；这是本票对字段表的**唯一一处扩展**（见票 16 ``## Answer``）。
    """

    room: Id
    """抽中的内层房间（只能是非 ``unknown`` 房间：问号池禁止递归，票 03 §2）。"""

    spec: Annotated[
        BattleSpec | EventSpec | ShopSpec,
        Field(discriminator="kind"),
    ]
    """内层房间的物化结果（与三类房间各自的 ``pending.spec`` 同一份形状）。"""


Revealed = Annotated[BattleSpec | EventSpec | ShopSpec, Field(discriminator="kind")]
"""揭晓内层 spec 的类型别名（票 04 §1 的 ``<内层 spec>``）。"""


class UnknownSpec(_Frozen):
    """问号房（票 03 §2）：进入即揭晓，揭晓结果进档、重进不重抽。"""

    kind: Literal["unknown"]
    revealed: RevealedSpec


PendingSpec = Annotated[
    BattleSpec | EventSpec | ShopSpec | UnknownSpec,
    Field(discriminator="kind"),
]
"""``pending.spec`` 的四类**节点**形状（第五类"揭晓后的内层"是
:class:`RevealedSpec`，嵌在 :class:`UnknownSpec` 里——问号池禁止递归，故它不收 ``unknown``）。"""


class Pending(_Frozen):
    """当前节点（票 04 §1）：进入时写、结算时清，也是崩溃 / 退出的恢复点。"""

    node_id: Id
    """进度键 ``"<layer_id>/<node_id>"``（与 ``node_status`` 的键同形）。"""

    spec: PendingSpec


class RunState(_Frozen):
    """一轮剧本的全部可变事实（字段表见模块文档）。"""

    run_id: str
    """``"<seed>-<n>"``：同一 seed 的建档序号。**不是**会话 id（票 04 §4）。"""

    revision: int = Field(default=0, ge=0)
    """0 起、**每次真写 +1**；内容没变就跳过写、不 +1（票 04 §2）。"""

    seed: int = Field(ge=0, lt=SEED_LIMIT)
    """run seed：审计 / 重算自证；续档**不用**它取随机（结果已冻结）。"""

    script_id: Id
    script_version: int = Field(ge=1)
    """``script.version``：**不参与拒绝**，只做展示与审计（票 03 §7）。"""

    script_digest: str
    """``script_digest``：审计指纹，**不参与拒绝**（票 03 §7）。"""

    roster_candidates: Annotated[
        tuple[Id, ...],
        Field(min_length=MAX_ROSTER_CANDIDATES, max_length=MAX_ROSTER_CANDIDATES),
    ]
    """建档时抽出的 3 名初始角色候选（票 04 §1 的 ``×3``：剧本库的 ``roster`` 恒为 3，
    见 ``sgs.data`` 的 ``script_library.roster_size``）。"""

    chosen_hero: Id
    """玩家选定的初始角色（整轮固定）。"""

    layer_index: int = Field(default=0, ge=0)
    """当前大节下标（进入下一大节时 +1）。"""

    flags: tuple[Id, ...] = ()
    """run 级标签：节点完成可设、边的 ``requires`` 读它（票 03 §2）。"""

    coins: int = Field(default=0, ge=0)
    """铜钱（商店 / 奖励 / 失败扣半；扣半与折价都不让它变负）。"""

    backpack: Backpack
    relics: Annotated[tuple[Id, ...], Field(max_length=MAX_RELICS)] = ()
    """遗物栏（≤6）：每场开局按 ``grants`` 的 ``relic`` 生效（票 02 §5）。"""

    node_status: dict[Id, NodeStatus] = Field(default_factory=dict)
    """节点进度，键是 :func:`progress_key` 的形式。"""

    pending: Pending | None = None
    """唯一的"当前"：非空 = 正在这个节点里（含战斗中）。"""
