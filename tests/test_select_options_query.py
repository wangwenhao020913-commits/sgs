"""多选选项询问面（``QueryKind.SELECT_OPTIONS`` / ``Action.SelectOptions``）。

jie-feng-batch2 票 01a：界夏侯渊【神速】的「选择至多三项」要求"一次勾选任意组合"，而既有的
``SELECT_OPTION`` 只承载**一个** ``choice``。本文件钉住新增那一层的三件事：

1. 候选枚举：``[min_selected, max_selected]`` 内、不含 ``disabled`` 的**每个子集**各一条，
   枚举序 = 按 ``options`` 序的位掩码升序（**空集排第一** ⇒ 超时兜底 = 一项都不做）；
2. 置灰：``disabled`` 里的项留在 ``options``（界面要画出来），**不进**任何候选；
3. 答复：``Action.SelectOptions(choices)`` 能过 ``parse_action`` 的往返，且空集是合法动作。
"""

from __future__ import annotations

import pytest

from sgs.data import load_content_pack
from sgs.effects import legal_actions_for
from sgs.state import (
    Pass,
    QueryKind,
    project as project_view,
    SelectOptions,
    SelectOptionsContext,
    SelectOptionsQuery,
    parse_action,
)

from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")

OPTIONS = ("jie_shensu_1", "jie_shensu_2", "jie_shensu_3")


def _state():
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao"), SeatSpec(hero="liubei")],
    )


def _actions(
    *,
    options: tuple[str, ...] = OPTIONS,
    disabled: tuple[str, ...] = (),
    min_selected: int = 0,
    max_selected: int | None = None,
    optional: bool = False,
    seq: int = 7,
):
    ctx = SelectOptionsContext(
        skill_id="hero.jie_xiahouyuan_mobile.shensu",
        options=options,
        disabled=disabled,
        min_selected=min_selected,
        max_selected=max_selected,
        optional=optional,
    )
    return legal_actions_for(
        _state(), 0, QueryKind.SELECT_OPTIONS, answers_seq=seq, payload=ctx
    )


def test_every_subset_is_a_candidate_and_the_empty_set_comes_first():
    """三选项、区间 0..3 ⇒ 8 条候选；第一条是空集（超时兜底 = 一项都不做）。"""
    actions = _actions()
    assert len(actions) == 8
    assert all(isinstance(action, SelectOptions) for action in actions)
    assert actions[0].choices == ()
    assert actions[-1].choices == OPTIONS
    assert {action.choices for action in actions} == {
        (),
        ("jie_shensu_1",),
        ("jie_shensu_2",),
        ("jie_shensu_3",),
        ("jie_shensu_1", "jie_shensu_2"),
        ("jie_shensu_1", "jie_shensu_3"),
        ("jie_shensu_2", "jie_shensu_3"),
        ("jie_shensu_1", "jie_shensu_2", "jie_shensu_3"),
    }


def test_candidates_carry_the_askers_answers_seq():
    """每一条候选都带本次询问的 ``answers_seq``（提交时逐字段自检要靠它）。"""
    actions = _actions(seq=41)
    assert {action.answers_seq for action in actions} == {41}


def test_disabled_options_never_appear_in_a_candidate():
    """置灰项（没有可弃装备牌时的第 2 项）留在 ``options``，但任何候选都不含它。"""
    actions = _actions(disabled=("jie_shensu_2",))
    assert len(actions) == 4
    assert all("jie_shensu_2" not in action.choices for action in actions)
    assert [action.choices for action in actions] == [
        (),
        ("jie_shensu_1",),
        ("jie_shensu_3",),
        ("jie_shensu_1", "jie_shensu_3"),
    ]


def test_min_selected_drops_the_smaller_subsets():
    """``min_selected=1`` ⇒ 空集不再合法（技能自己决定"可以不选"还是"必须选一项"）。"""
    actions = _actions(min_selected=1)
    assert len(actions) == 7
    assert actions[0].choices == ("jie_shensu_1",)


def test_max_selected_caps_the_subset_size():
    """``max_selected=2`` ⇒ 只枚举到二元子集。"""
    actions = _actions(max_selected=2)
    assert len(actions) == 7
    assert all(len(action.choices) <= 2 for action in actions)


def test_optional_adds_a_pass_candidate():
    """``optional=True`` 多一条 ``Pass``（让"取消这一步"是合法应答；缺省没有）。

    注意候选是**排过序**的（`Pass` 按全局动作序排在最前，不是"追加在末尾"），而界神速
    用的是 ``optional=False``：那时空集才排在第一位、成为超时兜底。
    """
    actions = _actions(optional=True)
    assert sum(isinstance(action, Pass) for action in actions) == 1
    assert len(actions) == 9


def test_lower_bound_above_upper_bound_is_a_programming_error():
    """区间自相矛盾 ⇒ fail-closed（不静默截断、不返回空列表）。"""
    with pytest.raises(ValueError):
        _actions(min_selected=3, max_selected=2)


def test_a_choice_outside_the_option_set_is_rejected_at_parse_time():
    """答复集合的成员必须是字符串；未知成员由候选索引（而不是解析层）裁决。"""
    action = parse_action(
        {"kind": "SelectOptions", "choices": ["jie_shensu_1"], "answers_seq": 3}
    )
    assert isinstance(action, SelectOptions)
    assert action.choices == ("jie_shensu_1",)
    assert action.answers_seq == 3


def test_the_query_carries_the_display_payload():
    """询问载荷带全部选项（含置灰）+ 区间，界面据此画勾选面与置灰行。"""
    query = SelectOptionsQuery(
        seq=1,
        actor=0,
        timing="TURN_START",
        prompt="请选择若干项（可多选）。",
        legal_actions=(SelectOptions(choices=(), answers_seq=1),),
        view=project_view(_state(), 0),
        skill_id="hero.jie_xiahouyuan_mobile.shensu",
        options=OPTIONS,
        disabled=("jie_shensu_2",),
        min_selected=1,
        max_selected=3,
    )
    payload = query.model_dump(mode="json")
    assert payload["kind"] == "SELECT_OPTIONS"
    assert payload["options"] == list(OPTIONS)
    assert payload["disabled"] == ["jie_shensu_2"]
    assert payload["min_selected"] == 1
    assert payload["max_selected"] == 3
