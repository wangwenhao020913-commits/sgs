"""客户端动作 → 服务端 ``parse_action`` 的**回环契约**（M5-bugfix 的类级门禁）。

报障原样：借刀预发动的确认被服务端 400——

    UseCard.card.attribute Extra inputs are not permitted

根因不是某一条规则，而是"客户端拼的载荷"与"服务端模型"之间**只有实机点击才能发现**
的失配：``preactivation.js`` 把快照里的**展示卡**（``display_name`` / ``attribute`` /
``description`` / ``kind`` / ``source_cards`` …）原样塞进 ``UseCard.card``，而
``CardRef`` 是 ``extra="forbid"`` 的严格模型。修复是 ``core.js:bareCardRef`` 这个唯一投影口。

本测试把"客户端纯构造器"与"服务端解析器"接成一条回路：

1. ``tests/support/action_probe.mjs`` 用 ``vm`` 加载**真实的**前端脚本（不抄一份 JS），
   经 ``ui_bridge.js`` 取 ``buildPreativationAction`` / ``buildAction`` / ``bareCardRef``，
   产出借刀预发动、普通 ``UseCard``、``PlayCard`` 三条载荷 + 一条**装饰卡**载荷；
2. 这里把每条载荷喂给 :func:`sgs.state.protocol.parse_action`：桥的产出必须被接受，
   装饰卡载荷必须被拒绝（**服务端模型是唯一真源**，不放宽它来迁就界面）；
3. 装饰卡载荷同时证明"没有 ``bareCardRef`` 就会 400"——这是合成反例，不是假设。

原有的 ``ui_smoke`` d1/d2 与真 HTTP 的
``test_a_real_http_borrowed_sword_preactivation_needs_a_bare_card_ref`` 继续覆盖渲染 /
提交路径；本文件只钉**跨语言载荷形状**。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.state.protocol import parse_action  # noqa: E402

CARD_REF_KEYS = {
    "card_id",
    "type",
    "name",
    "suit",
    "rank",
    "slot",
    "attack_range",
    "color",
    "virtual_sources",
}
"""与 ``core.js:CARD_REF_KEYS`` 逐字一致（跨语言没有共享常量的通道）。"""

DECORATION_KEYS = ("display_name", "attribute", "description", "kind", "source_cards")
"""展示层字段：只读载体上可以有，**绝不**随动作上行。"""

PROBE = REPO_ROOT / "tests" / "support" / "action_probe.mjs"


def _probe_payload() -> dict:
    """跑 Node 探针并解析它打印的 JSON（失败时把 stderr 带进断言消息）。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    assert PROBE.is_file(), f"缺少客户端动作探针：{PROBE.relative_to(REPO_ROOT)}"
    result = subprocess.run(
        [node, str(PROBE.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"动作探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:  # pragma: no cover - 探针坏了
        raise AssertionError(
            f"探针 stdout 不是合法 JSON：{error}\n{result.stdout[:2000]}"
        ) from error


def _actions_by_name(payload: dict) -> dict[str, dict]:
    return {case["name"]: case["action"] for case in payload["cases"]}


def test_the_client_builders_round_trip_through_the_server_action_parser() -> None:
    """两条预发动 / UseCard / PlayCard：桥的产出全部被 ``parse_action`` 接受。

    ``two_step_preactivation`` 是票 04 §3 加的那条：两步清单（选牌 → 选项）拼出的
    ``ActivateSkill(picks, choices)``——`picks` 是实体牌 id、`choices` 是**选项文本**
    （不是座位），与借刀那条（``UseCard`` + ``designations``）是两种不同的复合动作。
    """
    payload = _probe_payload()
    actions = _actions_by_name(payload)
    assert set(actions) == {
        "borrowed_sword_preactivation",
        "two_step_preactivation",
        "seat_order_preactivation",
        "use_card",
        "play_card",
    }, f"探针用例集合变了：{sorted(actions)}"

    borrowed = actions["borrowed_sword_preactivation"]
    assert borrowed["kind"] == "UseCard"
    assert set(borrowed["card"]) == CARD_REF_KEYS, (
        "借刀预发动的 card 必须是**九键闭集**（展示字段一个都不许上行）："
        f"{sorted(borrowed['card'])}"
    )
    assert all(key not in borrowed["card"] for key in DECORATION_KEYS)
    action = parse_action(borrowed)
    assert action.kind == "UseCard"
    assert action.card.card_id == 31
    assert action.card.type == "borrowed_sword"
    assert action.card.name == "借刀杀人"
    assert action.targets == (1,)
    assert action.designations == (2,)
    assert action.answers_seq == 93

    composite = actions["two_step_preactivation"]
    assert composite["kind"] == "ActivateSkill"
    assert set(composite) == {"kind", "skill_id", "picks", "choices", "answers_seq"}, (
        f"复合动作的载荷只有这五格（展示字段一个都不许上行）：{sorted(composite)}"
    )
    assert composite["picks"] == [52] and composite["choices"] == ["杀"]
    parsed_composite = parse_action(composite)
    assert parsed_composite.skill_id == "hero.yuji_mobile.guohuo"
    assert parsed_composite.picks == (52,)
    assert parsed_composite.choices == ("杀",)
    assert parsed_composite.answers_seq == 96

    use = actions["use_card"]
    assert use["kind"] == "UseCard" and set(use["card"]) == CARD_REF_KEYS
    parsed_use = parse_action(use)
    assert parsed_use.card.card_id == 52
    assert parsed_use.targets == (2,)

    play = actions["play_card"]
    assert play["kind"] == "PlayCard" and set(play["card"]) == CARD_REF_KEYS
    parsed_play = parse_action(play)
    assert parsed_play.card.card_id == 53


def test_a_conversion_preactivation_carries_the_click_order_into_choices() -> None:
    """预发动座位步的**点击顺序**原样进 ``choices``（huo-mobile-batch 票 06 / 报障 ③）。

    探针用**真实**的 ``preactivation.js:selectPreativationSeat`` 在【连环】产物的
    1–2 目标步上先点座位 3、再点座位 1；载荷的 ``choices`` 必须保持这个先后。服务端的
    :func:`sgs.skills.resolve_conversion_confirmation` 对声明了
    ``preserve_target_order`` 的产物按 ``tuple(choices) == tuple(action.targets)`` 匹配
    ——界面排一次序（或这里转成集合），就永远落到座位序那条候选：实机报障即此。
    """
    payload = _probe_payload()
    action = _actions_by_name(payload)["seat_order_preactivation"]

    assert action["kind"] == "ActivateSkill"
    assert action["skill_id"] == "hero.pangtong_mobile.lianhuan"
    assert action["picks"] == [52], "来源牌就是被点的那张♣手牌"
    assert action["choices"] == ["seat:3", "seat:1"], (
        "点击顺序原样上行（先点 3 再点 1）；按座位序归一化会变成 ['seat:1', 'seat:3']"
    )

    parsed = parse_action(action)
    assert parsed.picks == (52,)
    assert parsed.choices == ("seat:3", "seat:1"), "服务端模型也按序列读这份顺序"


def test_a_decorated_card_payload_is_rejected_by_the_server_model() -> None:
    """合成反例：**同一张卡**的展示形态不能被 ``parse_action`` 接受（= 报障的 400）。

    这不是"假设服务端会拒"：它就是实机报障的载荷形状。桥必须先过 ``bareCardRef``，
    才让上一条测试变绿；两条一起才说明"修的是客户端，服务端模型没有被放宽"。
    """
    payload = _probe_payload()
    decorated = payload["decorated"]
    assert decorated["kind"] == "UseCard"
    for key in DECORATION_KEYS:
        assert key in decorated["card"], f"合成反例缺展示字段 {key}：{decorated['card']}"
    assert set(decorated["card"]) > CARD_REF_KEYS

    with pytest.raises(ValidationError):
        parse_action(decorated)

    # 同一张卡的**裸**形态必须被接受——证明上面拒的是装饰字段，不是这张牌 / 这个动作。
    bare = {key: value for key, value in decorated["card"].items() if key in CARD_REF_KEYS}
    accepted = parse_action({**decorated, "card": bare})
    assert accepted.card.card_id == decorated["card"]["card_id"]


def test_the_probe_prints_only_the_contract_payload() -> None:
    """探针 stdout 只有一份 JSON 对象（前端脚本加载期不得打印 / 泄漏上下文）。"""
    payload = _probe_payload()
    assert set(payload) == {"cases", "decorated"}, sorted(payload)
    assert isinstance(payload["cases"], list) and len(payload["cases"]) == 5
