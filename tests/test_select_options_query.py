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
from sgs.effects import is_legal_option_set, legal_actions_for
from sgs.engine import Engine, IllegalActionError
from sgs.resolution import Frame
from sgs.state import (
    FrameKind,
    Pass,
    QueryKind,
    project as project_view,
    SelectOptions,
    SelectOptionsContext,
    SelectOptionsQuery,
    parse_action,
)
from sgs.timing import Timing

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
    enumerate_candidates: bool = True,
    seq: int = 7,
):
    ctx = SelectOptionsContext(
        skill_id="hero.jie_xiahouyuan_mobile.shensu",
        options=options,
        disabled=disabled,
        min_selected=min_selected,
        max_selected=max_selected,
        optional=optional,
        enumerate_candidates=enumerate_candidates,
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


# ---------------------------------------------------------------------------
# 不枚举档（yijiang-2014-round2 票 21）：候选不按子集枚举
# ---------------------------------------------------------------------------

SEATS = tuple(f"seat:{seat}" for seat in range(8))
"""8 人局的座位池——复现票 21 的那份面（8 个座位、上限 4）。"""


def test_the_seat_multi_select_does_not_enumerate_subsets():
    """不枚举档：8 个座位 / 上限 4 的多选面**不列举子集**，候选塌到骨架那一条。

    同一份面按子集枚举是 ``8C1+8C2+8C3+8C4 = 162`` 条 + 1 条 ``Pass`` = **163**（复现里的
    「非技能候选数 163 超过上界 128」）；不枚举档只留**一条骨架**兜底（"每一问都有至少一个
    合法应答"），答案由界面 / AI 自己组装、引擎收答案时按
    :func:`sgs.effects.is_legal_option_set` 校验形状。组合数因此与座位数脱钩。
    """
    enumerated = _actions(
        options=SEATS, min_selected=1, max_selected=4, optional=True
    )
    structural = _actions(
        options=SEATS,
        min_selected=1,
        max_selected=4,
        optional=True,
        enumerate_candidates=False,
    )

    assert len(enumerated) == 163, "对照：按子集枚举是 162 个子集 + 一条放弃（票 21 的复现数）"
    assert len(structural) <= 2, f"不枚举档的候选数必须塌下来：{[a.kind for a in structural]}"
    assert [action.kind for action in structural] == ["SelectOptions", "Pass"], (
        "骨架一条 + （可放弃时的）Pass 一条；Pass 仍恒在最后"
    )
    assert structural[0].choices == ("seat:0",), "骨架 = options 序的前 min_selected 个"


def test_the_structural_fallback_is_a_minimum_skeleton_not_an_answer_set():
    """骨架的构造规则：前 ``min_selected`` 个**可选项**（按 ``options`` 序），下限 0 ⇒ 空集。

    三件事各自钉一条：空集仍是"什么都不做"的超时兜底；置灰项**不进**骨架（它不可选，
    进骨架就等于发一条非法候选）；可选项凑不出下限时**没有**合法集合 ⇒ fail-closed，
    不发明一条越界的骨架。
    """
    empty = _actions(
        options=SEATS, min_selected=0, max_selected=4, enumerate_candidates=False
    )
    assert [action.choices for action in empty] == [()], "下限 0 ⇒ 骨架 = 空集（超时兜底不变）"

    greyed = _actions(
        options=("a", "b", "c"),
        disabled=("a",),
        min_selected=2,
        max_selected=3,
        enumerate_candidates=False,
    )
    assert [action.choices for action in greyed] == [("b", "c")], (
        "骨架只从**可选项**里取（置灰的 a 不进骨架），仍按 options 序"
    )

    with pytest.raises(ValueError, match="下限"):
        _actions(
            options=("a", "b"),
            disabled=("a", "b"),
            min_selected=1,
            max_selected=1,
            enumerate_candidates=False,
        )


# ---------------------------------------------------------------------------
# 形状闸：引擎收答案时按同一份谓词校验（不枚举档的唯一出路）
# ---------------------------------------------------------------------------


def _structural_engine(
    *,
    options: tuple[str, ...],
    disabled: tuple[str, ...] = (),
    min_selected: int = 1,
    max_selected: int | None = None,
) -> tuple[Engine, SelectOptionsQuery]:
    """经 ``Engine.ask_options`` 发一次**不枚举档**的多选询问，返回挂起的那一问。"""
    engine = Engine.from_state(_state(), seed=3)
    engine.ask_options(
        Frame(kind=FrameKind.SKILL, data={}),
        0,
        Timing.TURN_START,
        skill_id="hero.guyong.bingyi:seat",
        options=options,
        disabled=disabled,
        min_selected=min_selected,
        max_selected=max_selected,
        enumerate_candidates=False,
    )
    query = engine.pending
    assert isinstance(query, SelectOptionsQuery), f"挂起的应当是多选询问：{query!r}"
    assert query.structural is True, "不枚举档的标志必须落到询问上（引擎据此开闸）"
    return engine, query


def test_a_subset_outside_the_bounds_is_rejected():
    """形状闸：区间外的集合被 ``IllegalActionError`` 拒；区间内的（候选里没有的）照常接受。"""
    engine, query = _structural_engine(
        options=("seat:0", "seat:1", "seat:2", "seat:3"),
        min_selected=2,
        max_selected=3,
    )
    assert [action.choices for action in query.legal_actions] == [("seat:0", "seat:1")], (
        "候选里只有骨架（前 min_selected 个），**没有** 162 条子集"
    )

    with pytest.raises(IllegalActionError):
        engine.submit(SelectOptions(choices=("seat:0",), answers_seq=query.seq))
    assert engine.pending is query, "被拒的提交不改变挂起态"

    with pytest.raises(IllegalActionError):
        engine.submit(
            SelectOptions(
                choices=("seat:0", "seat:1", "seat:2", "seat:3"), answers_seq=query.seq
            )
        )

    engine.submit(
        SelectOptions(choices=("seat:1", "seat:2"), answers_seq=query.seq)
    )
    assert engine.pending is None, "区间内的集合由引擎接受（哪怕它不在骨架候选里）"


def test_an_out_of_pool_or_disabled_option_is_rejected():
    """形状闸：池外 / 置灰 / 重复的项一律拒绝——不枚举档不是"什么都收"。"""
    engine, query = _structural_engine(
        options=("seat:0", "seat:1", "seat:2"),
        disabled=("seat:2",),
        min_selected=1,
        max_selected=2,
    )

    with pytest.raises(IllegalActionError):
        engine.submit(
            SelectOptions(choices=("seat:0", "seat:9"), answers_seq=query.seq)
        )
    with pytest.raises(IllegalActionError):
        engine.submit(
            SelectOptions(choices=("seat:0", "seat:2"), answers_seq=query.seq)
        )
    with pytest.raises(IllegalActionError):
        engine.submit(
            SelectOptions(choices=("seat:0", "seat:0"), answers_seq=query.seq)
        )
    assert engine.pending is query, "三次拒绝都不改变挂起态"

    engine.submit(SelectOptions(choices=("seat:0",), answers_seq=query.seq))
    assert engine.pending is None


def test_the_predicate_is_the_single_shape_truth():
    """谓词本身（引擎与 AI / 回放共用的那一份）：四条规则逐条成立。"""
    ctx = SelectOptionsContext(
        skill_id="hero.guyong.bingyi:seat",
        options=("seat:0", "seat:1", "seat:2"),
        disabled=("seat:2",),
        min_selected=1,
        max_selected=2,
    )
    assert is_legal_option_set(ctx, ("seat:0",))
    assert is_legal_option_set(ctx, ("seat:1", "seat:0")) is True, "次序不进判据（答案是集合）"
    assert not is_legal_option_set(ctx, ()), "低于下限"
    assert not is_legal_option_set(ctx, ("seat:0", "seat:1", "seat:2")), "高于上限"
    assert not is_legal_option_set(ctx, ("seat:0", "seat:9")), "池外"
    assert not is_legal_option_set(ctx, ("seat:2",)), "置灰"
    assert not is_legal_option_set(ctx, ("seat:0", "seat:0")), "重复"


def test_the_structural_flag_stays_out_of_the_record_stream():
    """``structural`` 是 ``exclude=True`` 的本地面：询问与记录流都**不多出这个键**。

    47 条金录像靠这条保字节（新字段一旦序列化，记录流就漂）。
    """
    engine, query = _structural_engine(options=("seat:0", "seat:1"), min_selected=1)
    assert "structural" not in query.model_dump(mode="json")

    engine.submit(SelectOptions(choices=("seat:1",), answers_seq=query.seq))
    records = [record for record in engine.records if record.kind == "query"]
    assert records, "构造：记录流里至少有一条询问"
    assert "structural" not in records[-1].query.model_dump(mode="json")
    assert '"structural"' not in engine.journal()
