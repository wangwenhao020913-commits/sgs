"""规则配置对象（票 09）：本局在**规则层**做出的显式选择。

只有两类内容：**模式级参数**与**存疑点开关**（M2 仍为空清单）。
内容包、牌堆档位、种子、schema 版本都**不属于**它——它们各答一问。
被排除的机制在配置里**根本不存在**（不给恒假的布尔开关），任何超出 M1 的字段或取值
都在构造期 fail-closed；失败与数据校验同类型 :class:`~sgs.data.DataLoadError`。

M7 票 07 把**模式账**落进这里：``mode`` 由派生属性变成字段（缺省仍按座位数派生，既有
调用点与 60 条录像不用写它），并新增 ``pve`` 一节——PvE 的阵营 / 主角 / 胜负条件必须进
header，否则录像重建不出同一场（票 01 §2）。字段表**一次到位**，后续票只往
``grants`` / ``seats`` 里填语义与校验，不再改表（改表就是第二次 header 变动）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import DataLoadError, LoadError
from .identities import IDENTITY_ROSTERS, NO_IDENTITY_LAYOUTS, Identity, Mode
from .models import Gender, Kingdom

__all__ = [
    "PveConfig",
    "PveSeat",
    "PveVictory",
    "PveVictoryKind",
    "RuleConfig",
    "SkillGrant",
    "validate_rule_config",
]

_MODE_LABELS: Mapping[Mode, str] = {
    Mode.NO_IDENTITY: "无身份局",
    Mode.IDENTITY: "身份局",
    Mode.PVE: "PvE",
}
"""错误消息里的模式中文名（判定照旧比枚举成员，这张表只喂报错与日志）。"""


class PveVictoryKind(str, Enum):
    """PvE 的**逐场**胜利条件（票 01 §4 的表；败因见 :func:`sgs.engine.victory.solve_pve_outcome`）。"""

    ROUT = "rout"
    """全歼敌人。"""

    SURVIVE_ROUNDS = "survive_rounds"
    """存活到第 N 轮之后（``rounds`` = N）。"""

    ROUT_WITHIN_ROUNDS = "rout_within_rounds"
    """N 轮之内全歼敌人（``rounds`` = N），超时即败。"""

    SLAY_BOSS = "slay_boss"
    """击败指定 Boss 座位（``PveConfig.boss``）。"""


_ROUND_BASED: frozenset[PveVictoryKind] = frozenset(
    {PveVictoryKind.SURVIVE_ROUNDS, PveVictoryKind.ROUT_WITHIN_ROUNDS}
)
"""带轮次上限的两类条件：``rounds`` 必填且 > 0，其余两类必须为空。"""

_PVE_SIDES: frozenset[Identity] = frozenset({Identity.ALLY, Identity.ENEMY})
"""PvE 的阵营**值域**：身份局的四值在这里没有位置（票 01 §1「只许 ALLY / ENEMY」）。"""


class PveVictory(BaseModel):
    """逐场胜负条件：条件本身 + 它的参数（``rounds``）。

    ``N`` 与条件**放在一起**而不是另起一个 ``PveConfig.rounds`` 字段：``SLAY_BOSS`` /
    ``ROUT`` 没有轮次参数，独立字段会带着一个"此刻无意义"的值进 header（票 07 的字段表
    只有 ``victory`` 这一格）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PveVictoryKind
    rounds: int | None = None
    """轮次上限 N；只在 :data:`_ROUND_BASED` 的两类条件上允许出现。"""


class SkillGrant(BaseModel):
    """战斗开局授予的一项技能 / 遗物（位置由票 01 §9 钉在 header 里）。

    字段形状照票 03 §3 / 票 09：``{seat, kind, id}``。**校验矩阵归票 09**（每座技能 ≤4、
    遗物 ≤6、``id`` 在 ``CATALOG`` 里、遗物只授主角）——本票只落骨架，不预设那些语义。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    seat: int
    """获得授予的座位。"""

    kind: Literal["skill", "relic"]
    """``skill`` = 出战技能（写进技能的授予来源），``relic`` = 遗物（独立遗物栏）。"""

    id: str
    """技能 / 遗物的 catalog id。"""


class PveSeat(BaseModel):
    """PvE 的**逐座位合成武将**与属性加成（票 03 §3 / 票 10：那里叫 ``SeatSpec``）。

    敌人不是武将表里的武将而是「模板 + 抽到的技能 + 属性加成」，故这些事实必须逐座
    进 header。**校验矩阵（长度 / 数值区间 / 与 ``identities`` 对位）归票 10**——本票只落
    骨架，不预设合成武将的取值规则。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    hero: str | None = None
    """武将 id；``None`` ⇒ 合成武将（此时 ``name`` 承载模板名）。"""

    name: str = ""
    """模板名 / 显示名；有武将时可留空（显示取自武将表）。"""

    hp: int
    hp_max: int
    hp_max_bonus: int = 0
    draw_bonus: int = 0
    slash_damage_bonus: int = 0
    kingdom: Kingdom | None = None
    gender: Gender | None = None


class PveConfig(BaseModel):
    """PvE 的一场战斗：阵营、主角、逐场胜负条件、Boss、开局授予与逐座位合成武将。

    全部**进 header**（票 01 §2 的裁定）：阵营与胜负条件不进记录流的话，录像重建不出
    同一场。``identities`` 只收 :attr:`~sgs.data.Identity.ALLY` /
    :attr:`~sgs.data.Identity.ENEMY`——身份局的四值在这里没有位置（那四条规则在 PvE
    全部不生效）。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identities: tuple[Identity, ...]
    """逐座位的阵营（长度 = ``player_count``，由 :class:`RuleConfig` 交叉校验）。"""

    principal: int
    """主角座位：它阵亡即本场失败（网页层把人类座位映射进来）。"""

    protected_ally: int | None = None
    """受保护队友：它阵亡同样判败；必须是一个 ``ALLY`` 座位且不是主角。"""

    victory: PveVictory
    """逐场胜负条件（四类之一，见 :class:`PveVictoryKind`）。"""

    boss: int | None = None
    """Boss 座位：只属于 ``SLAY_BOSS``，且必须是 ``ENEMY``。"""

    grants: tuple[SkillGrant, ...] = ()
    """战斗开局的技能 / 遗物授予（语义与校验归票 09）。"""

    seats: tuple[PveSeat, ...] = ()
    """逐座位合成武将与属性加成（语义与校验归票 10）。"""

    def seats_of(self, side: Identity) -> tuple[int, ...]:
        """属于 ``side`` 的座位（座位升序，**含已阵亡者**）。

        胜负的 ``winners`` 要列出全侧座位（与身份局同口径），故这里不筛存活。
        """
        return tuple(seat for seat, identity in enumerate(self.identities) if identity is side)

    @model_validator(mode="after")
    def _only_the_two_campaign_sides(self) -> "PveConfig":
        """``identities`` 只收友 / 敌：主公之类落到两侧之外会被胜负判定静默漏掉（fail-open）。"""
        for seat, identity in enumerate(self.identities):
            if identity not in _PVE_SIDES:
                raise ValueError(
                    f"identities[{seat}] 必须是 ally / enemy（实得 {identity.value}）："
                    "身份局的四值只在身份局里成立"
                )
        return self

    @model_validator(mode="after")
    def _both_sides_exist(self) -> "PveConfig":
        if not self.seats_of(Identity.ALLY) or not self.seats_of(Identity.ENEMY):
            raise ValueError("PvE 阵营至少要有 1 名友方（ally）与 1 名敌方（enemy）")
        return self

    @model_validator(mode="after")
    def _principal_leads_the_ally_side(self) -> "PveConfig":
        if not 0 <= self.principal < len(self.identities):
            raise ValueError("principal 必须落在 [0, len(identities)) 内")
        if self.identities[self.principal] is not Identity.ALLY:
            raise ValueError("principal 必须是 ally 座位（主角阵亡 = 本场失败）")
        return self

    @model_validator(mode="after")
    def _protected_ally_is_a_second_ally(self) -> "PveConfig":
        if self.protected_ally is None:
            return self
        if not 0 <= self.protected_ally < len(self.identities):
            raise ValueError("protected_ally 必须落在 [0, len(identities)) 内")
        if self.protected_ally == self.principal:
            raise ValueError("protected_ally 不能是 principal 自己")
        if self.identities[self.protected_ally] is not Identity.ALLY:
            raise ValueError("protected_ally 必须是 ally 座位")
        return self

    @model_validator(mode="after")
    def _boss_belongs_to_the_victory_condition(self) -> "PveConfig":
        if self.victory.kind is not PveVictoryKind.SLAY_BOSS:
            if self.boss is not None:
                raise ValueError("boss 只属于 slay_boss 这类胜利条件")
            return self
        if self.boss is None:
            raise ValueError("slay_boss 必须带上 boss 座位")
        if not 0 <= self.boss < len(self.identities):
            raise ValueError("boss 必须落在 [0, len(identities)) 内")
        if self.identities[self.boss] is not Identity.ENEMY:
            raise ValueError("boss 必须是 enemy 座位")
        return self

    @model_validator(mode="after")
    def _round_limit_belongs_to_the_victory_condition(self) -> "PveConfig":
        rounds = self.victory.rounds
        if self.victory.kind in _ROUND_BASED:
            if rounds is None:
                raise ValueError(f"{self.victory.kind.value} 必须带上 rounds（轮次上限 N）")
            if rounds < 1:
                raise ValueError("rounds 必须是正整数轮次")
        elif rounds is not None:
            raise ValueError(f"{self.victory.kind.value} 不接受 rounds")
        return self


class RuleConfig(BaseModel):
    """M1 的规则配置：模式级参数 + 模式账 + PvE 那一节，值域即冻结面。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player_count: Literal[2, 3, 4, 5, 6, 7, 8] = 2
    """模式第一参数；值域 = 2~8。

    人数**不是**模式判据（用户 2026-11-19 裁定）：能不能用这个人数，取决于本模式
    的构成表里有没有这一档布局（见 :meth:`_mode_layout_exists`）。
    """

    starting_hand_size: int = Field(default=4, ge=1)
    """起始手牌张数（票 13 §2：模式级配置项，不写死在引擎里）。"""

    first_seat: int = Field(default=0, ge=0)
    """先手座位（票 13 §2：座位序、可配置、**不随机**）。

    身份局里语义被主公座位取代（票 13 §1）：先手 = 主公座位，故此处必须保持默认 ``0``。
    """

    mode: Mode = Mode.NO_IDENTITY
    """本局的模式（票 07 起是**字段**，进 header）。

    缺省不写时由 :meth:`_derive_mode_from_the_seat_count` 按座位数派生——2 = 无身份局、
    其余 = 身份局——既有调用点与 60 条录像因此不用改一行。PvE 必须显式声明：
    3 人局既可能是身份局也可能是 PvE，人数推不出模式。
    """

    pve: PveConfig | None = None
    """PvE 的逐场配置；标准局恒 ``None``（header 里就是 ``pve: null``）。"""

    @model_validator(mode="before")
    @classmethod
    def _derive_mode_from_the_seat_count(cls, data: Any) -> Any:
        """缺省模式 = 座位数的函数（票 13 §1 的旧口径，票 07 保留为**缺省值**）。

        v2 录像与既有调用点都不写 ``mode``；这里把缺省补成派生值，于是"读旧 header"
        与"写新 header"走的是同一条推导，不需要另建一个 v1 式读取器。
        """
        if isinstance(data, Mapping) and "mode" not in data:
            count = data.get("player_count", 2)
            derived = Mode.NO_IDENTITY if count == 2 else Mode.IDENTITY
            return {**data, "mode": derived}
        return data

    @model_validator(mode="after")
    def _first_seat_within_seats(self) -> "RuleConfig":
        if not 0 <= self.first_seat < self.player_count:
            raise ValueError("first_seat 必须落在 [0, player_count) 内")
        return self

    @model_validator(mode="after")
    def _first_seat_follows_the_mode(self) -> "RuleConfig":
        if self.mode is Mode.IDENTITY and self.first_seat != 0:
            raise ValueError("身份局的先手是主公座位，first_seat 不得预设（票 13 §1）")
        return self

    @model_validator(mode="after")
    def _mode_layout_exists(self) -> "RuleConfig":
        """fail-closed：**本 mode 的构成表里有没有这个人数的布局**（票 07 的校验矩阵）。

        无身份局的构成表是 :data:`~sgs.data.NO_IDENTITY_LAYOUTS`（只有 2 人档）、身份局是
        :data:`~sgs.data.IDENTITY_ROSTERS`（4~8）；PvE 的布局由 ``pve.identities`` 自证，
        故它只看长度对不对得上（下一条）。判据不写"2 / 4~8"这类人数区间：人数不是模式判据。
        """
        if self.mode is Mode.PVE:
            return self
        layouts = NO_IDENTITY_LAYOUTS if self.mode is Mode.NO_IDENTITY else IDENTITY_ROSTERS
        if self.player_count in layouts:
            return self
        raise ValueError(
            f"{_MODE_LABELS[self.mode]}没有 {self.player_count} 人布局"
            f"（构成表只有 {sorted(layouts)} 人档）；"
            "人数不是模式判据——要这个人数请显式声明能容纳它的 mode（如 pve）"
        )

    @model_validator(mode="after")
    def _pve_section_matches_the_mode(self) -> "RuleConfig":
        """PvE 这一节与模式**互为条件**：缺一即拒，且布局长度必须自证（票 07）。"""
        if self.pve is not None and self.mode is not Mode.PVE:
            raise ValueError(f"pve 配置只属于 PvE 模式（本局 mode = {self.mode.value}）")
        if self.mode is Mode.PVE:
            if self.pve is None:
                raise ValueError("PvE 模式必须带 pve（阵营 / 主角 / 胜负条件要进 header 自证）")
            if len(self.pve.identities) != self.player_count:
                raise ValueError(
                    f"pve.identities 的长度必须等于 player_count："
                    f"实得 {len(self.pve.identities)} vs {self.player_count}"
                )
        return self


def validate_rule_config(raw: Mapping[str, Any]) -> RuleConfig:
    """从进料边界构造规则配置；不合法即抛聚合的 :class:`DataLoadError`。

    与内容包加载同姿态：错误带位置与期望/实得，一次报完，绝不半接受。
    """
    try:
        return RuleConfig.model_validate(dict(raw))
    except ValidationError as exc:
        errors = []
        for detail in exc.errors():
            path = "/" + "/".join(str(part) for part in detail["loc"]) if detail["loc"] else "/"
            errors.append(
                LoadError(
                    file="<rule_config>",
                    loc=path,
                    expected="符合报错说明的取值（见 rule_config 约束）",
                    got=detail.get("msg", "非法取值"),
                    rule_id=f"rule_config.{detail['type']}",
                )
            )
        raise DataLoadError(errors) from exc
