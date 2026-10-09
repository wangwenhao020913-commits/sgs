"""规则配置对象（票 09）：本局在**规则层**做出的显式选择。

只有两类内容：**模式级参数**与**存疑点开关**（M2 仍为空清单）。
内容包、牌堆档位、种子、schema 版本都**不属于**它——它们各答一问。
被排除的机制在配置里**根本不存在**（不给恒假的布尔开关），任何超出 M1 的字段或取值
都在构造期 fail-closed；失败与数据校验同类型 :class:`~sgs.data.DataLoadError`。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import DataLoadError, LoadError
from .identities import Mode

__all__ = ["RuleConfig", "validate_rule_config"]


class RuleConfig(BaseModel):
    """M1 的规则配置：三个字段，值域即冻结面。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    player_count: Literal[2, 4, 5, 6, 7, 8] = 2
    """模式第一参数；值域 = 2（无身份局）与 4~8（身份局座位数）。

    3 不在值域内，故**加载期**失败（票 13 §1：模式由座位数派生，没有 3 人配置）。
    """

    starting_hand_size: int = Field(default=4, ge=1)
    """起始手牌张数（票 13 §2：模式级配置项，不写死在引擎里）。"""

    first_seat: int = Field(default=0, ge=0)
    """先手座位（票 13 §2：座位序、可配置、**不随机**）。

    身份局里语义被主公座位取代（票 13 §1）：先手 = 主公座位，故此处必须保持默认 ``0``。
    """

    @property
    def mode(self) -> Mode:
        """模式由座位数**派生**（票 13 §1）：2 = 无身份局、4~8 = 身份局。

        只读属性、不进 schema、不参与序列化——这样"配比不是配置项"在类型上成立，
        2 人局 header 也仍是恰好三键。
        """
        return Mode.NO_IDENTITY if self.player_count == 2 else Mode.IDENTITY

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
