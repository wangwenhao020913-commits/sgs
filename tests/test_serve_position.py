"""构造局面注入服务（hero-flow-v2 票 03）：JSON 口径、注册表、以及一次真实 HTTP 往返。

为什么不是"起进程再测"：`tests/test_web.sh` 已经用真进程验过同一套 fixture 门面；
这里要钉的是**本工具自己的翻译层**（牌名 / 身份缺省 / 阶段 / 报错路径）与"注入的局面
真的进了下发给客户端的载荷"，所以用进程内的真 `_Handler`（不开端口、不起线程）加上
一个只服务一次请求的真 `handle_request`——与 `tests/test_web.py` 的 `_post_with_the_real_handler`
同一条缝。
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest


def _tool():
    """按文件路径加载 `tools/serve-position.py`（它有连字符，不能当模块 import）。"""
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "serve-position.py"
    spec = importlib.util.spec_from_file_location("serve_position", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # 必须先登记进 sys.modules：模块里有 `from __future__ import annotations`，
    # dataclass 解析字符串注解时要回查 `sys.modules[cls.__module__]`。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _two_seats(**overrides):
    doc = {
        "deck_profile": "standard_108",
        "human_seat": 0,
        "turn_seat": 0,
        "seats": [
            {"hero": "sunquan", "hp": 4, "hp_max": 4, "hand": ["slash"]},
            {"hero": "lvbu", "hp": 4, "hp_max": 4, "hand": ["dodge"]},
        ],
    }
    doc.update(overrides)
    return doc


def test_a_position_document_becomes_a_game_state() -> None:
    """座位 / 体力 / 手牌 / 装备 / 阶段 / 回合座位都照文档落进 ``GameState``。"""
    tool = _tool()
    position = tool.parse_position(
        {
            "deck_profile": "standard_108",
            "seed": 7,
            "turn_seat": 1,
            "phase": "play",
            "seats": [
                {
                    "hero": "zhangchunhua_mobile",
                    "hp": 1,
                    "hp_max": 3,
                    "hand": ["dodge", "peach"],
                    "equip": ["crossbow"],
                    "kingdom": "wei",
                },
                {"hero": "xusheng_mobile", "hp": 4, "hp_max": 4},
            ],
        }
    )

    state = position.state
    assert position.seed == 7
    assert state.turn_seat == 1
    assert state.phase is not None and state.phase.value == "play"
    hero, hp, hp_max, equip = (
        state.players[0].hero,
        state.players[0].hp,
        state.players[0].hp_max,
        state.players[0].equip,
    )
    assert (hero, hp, hp_max) == ("zhangchunhua_mobile", 1, 3)
    assert len(state.players[0].hand) == 2
    assert [state.cards[index].type for index in equip] == ["crossbow"]
    # 两份座位各自的手牌不许撞成同一张实例（牌守恒的起点）。
    every = [*state.players[0].hand, *state.players[1].hand, *equip]
    assert len(every) == len(set(every))


def test_a_bad_card_name_reports_the_field_path() -> None:
    """报错要指到座位与牌区——否则验收时只能看见"局面没起来"。"""
    tool = _tool()
    doc = _two_seats()
    doc["seats"][0]["hand"] = ["fire_attack"]  # 军争牌，standard_108 里没有
    with pytest.raises(ValueError) as error:
        tool.parse_position(doc)
    assert "seats[0].hand" in str(error.value)
    assert "fire_attack" in str(error.value)


def test_identity_defaults_fill_lord_and_rebels_for_a_four_seat_table() -> None:
    """>2 座位缺身份时补"座位 0 主公、其余反贼"，并且必须在说明里写明补过。"""
    tool = _tool()
    position = tool.parse_position(
        {
            "seats": [
                {"hero": "sunquan", "hp": 4, "hp_max": 4},
                {"hero": "lvbu", "hp": 4, "hp_max": 4},
                {"hero": "guanyu", "hp": 4, "hp_max": 4},
                {"hero": "zhouyu", "hp": 3, "hp_max": 3},
            ]
        }
    )
    identities = [player.identity.value for player in position.state.players]
    assert identities == ["lord", "rebel", "rebel", "rebel"]
    assert "身份缺省已补" in position.note


def test_the_registry_builds_a_session_from_the_injected_position(tmp_path) -> None:
    """注册表建出来的会话：引擎状态就是注入的那份，人类座位拿到询问。"""
    tool = _tool()
    position = tool.parse_position(_two_seats())
    registry = tool.PositionRegistry(position, runs_dir=tmp_path)
    session = registry.create({})

    seats = session.engine.state.players
    assert [seat.hero for seat in seats] == ["sunquan", "lvbu"]
    assert seats[0].hp == 4
    assert session.pending is not None or session.finished, "建局后要么挂起一问，要么已终局"


def test_the_position_server_answers_one_real_request(tmp_path) -> None:
    """真绑端口、真发 HTTP：建局响应里能读到注入的武将（`handle_request` 只服务一次）。"""
    tool = _tool()
    position = tool.parse_position(_two_seats())
    server = tool.make_position_server(position, 0, runs_dir=tmp_path)
    host, port = server.server_address[:2]
    worker = threading.Thread(target=server.handle_request, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request(
            "POST",
            "/api/sessions",
            body=json.dumps({"player_count": 2}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read()
        connection.close()
    finally:
        worker.join(10)
        server.server_close()

    assert response.status == 200, payload[:400]
    assert b"sunquan" in payload, "下发载荷里必须能读到注入的武将"
