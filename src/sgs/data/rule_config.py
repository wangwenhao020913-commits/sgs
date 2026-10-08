"""规则配置对象（票 09）：本局在**规则层**做出的显式选择。

只有两类内容：**模式级参数**与**存疑点开关**（M1 为空清单）。
内容包、牌堆档位、种子、schema 版本都**不属于**它——它们各答一问。
被排除的机制在配置里**根本不存在**（不给恒假的布尔开关），任何超出 M1 的字段或取值
都在构造期 fail-closed；失败与数据校验同类型 :class:`~sgs.data.DataLoadError`。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from .errors import DataLoadError, LoadError
from .models import _FrozenModel

__all__ = ["RuleConfig", "validate_rule_config"]


class RuleConfig(_FrozenModel):
    """M1 的规则配置：三个字段，值域即冻结面。"""

    player_count: Literal[2] = 2
    """模式第一参数；M1 的裁剪只在 2 人下成立，值 3 必须**加载期**失败。"""

    starting_hand_size: int = Field(default=4, ge=1)
    """起始手牌张数（票 13 §2：模式级配置项，不写死在引擎里）。"""

    first_seat: int = Field(default=0, ge=0)
    """先手座位（票 13 §2：座位序、可配置、**不随机**）。"""

    @model_validator(mode="after")
    def _first_seat_within_seats(self) -> "RuleConfig":
        if not 0 <= self.first_seat < self.player_count:
            raise ValueError("first_seat 必须落在 [0, player_count) 内")
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
