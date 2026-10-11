"""`tests/test_web.sh` 的 JSON 客户端助手（bash 里解析 JSON 不现实，故这一小段住 Python）。

它**不是**测试：被 shell 门禁调用，走真 HTTP 打完一整局并把关键事实打成一行结论。
用法见 ``tests/test_web.sh``（``--port <N> --seed <N> --players <N> [--timeout <S>]``）。

判据（`spec.md` §Acceptance 第 1 条）：
  * 出现 ``game_end``；
  * 每条 action 记录 ``accepted == true``、**零** ``accepted == false``；
  * ``POST /answer`` 的次数 == 记录流里人类座位的 action 记录数（"每次询问都真的过了 HTTP"）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def call(url: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, {"error": body}


def play(base: str, *, seed: int, players: int, timeout: float | None, max_answers: int) -> int:
    status, body = call(
        f"{base}/api/sessions",
        {
            "player_count": players,
            "ai": "heuristic",
            "seed": seed,
            "human_seat": 0,
            "timeout_seconds": timeout,
        },
    )
    if status != 200:
        print(f"FAIL 建局失败：HTTP {status} {body}")
        return 1

    session_id = body["session_id"]
    cursor = body["cursor"]
    log_seq = body["log_seq"]
    answers = 0
    rejected = 0
    human_answers = 0
    queries = 0
    game_end: dict[str, Any] | None = None

    while not body["finished"]:
        pending = body["pending"]
        if pending is None:
            print("FAIL 未终局但也没有待应答的询问")
            return 1
        queries += 1
        # 从候选里取第一个**合法**候选——前端也是这么做的（原样取自 legal_actions）。
        action = pending["legal_actions"][0]
        status, body = call(
            f"{base}/api/sessions/{session_id}/answer?after={cursor}&logs={log_seq}",
            {"action": action},
        )
        if status != 200:
            print(f"FAIL 应答被拒：HTTP {status} {body}")
            return 1
        answers += 1
        if answers > max_answers:
            print(f"FAIL 超过 {max_answers} 次应答仍未终局")
            return 1
        cursor = body["cursor"]
        log_seq = body["log_seq"]
        for record in body["records"]:
            if record["kind"] == "action":
                if record.get("accepted"):
                    if record["action"]["answers_seq"] == pending["seq"]:
                        human_answers += 1
                else:
                    rejected += 1
            if record["kind"] == "game_end":
                game_end = record

    if game_end is None:
        print("FAIL 终局没有 game_end 记录")
        return 1
    if rejected:
        print(f"FAIL 记录流里有 {rejected} 条 accepted=false")
        return 1
    if human_answers != answers:
        print(f"FAIL HTTP 应答数 {answers} != 记录流里人类座位的 action 记录数 {human_answers}")
        return 1
    if queries != answers:
        print(f"FAIL 询问数 {queries} != 应答数 {answers}")
        return 1

    print(
        "PASS 人类座位经真 HTTP 打完一局："
        f"session={session_id} seed={seed} 人数={players} 询问={queries} "
        f"结果={game_end['result']} 获胜座位={game_end['winners']} 原因={game_end['reason']}"
    )

    # 不泄露（判据 2）：人类座位收到的响应体里，**存活**的对手身份必须是 None（主公除外——
    # 主公身份公开），且任何对手都不出现手牌牌面；牌堆只有数量。
    # 「已阵亡」是**允许**公开的三种情形之一（`06` §M4 判据 2 的原文），故这里不拦它。
    snapshot = body["snapshot"]
    for player in snapshot["players"]:
        if player["seat"] == snapshot["seat"]:
            continue
        if player["alive"] and player.get("identity") not in (None, "lord"):
            print(f"FAIL 存活对手的身份泄露：{player}")
            return 1
        if "self_hand" in player or "hand" in player:
            print(f"FAIL 对手手牌牌面泄露：{player}")
            return 1
    print(
        "PASS 快照无对手手牌牌面（deck_count=%d，存活对手身份均为空）" % snapshot["deck_count"]
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--max-answers", type=int, default=4000)
    args = parser.parse_args(argv)
    return play(
        args.base,
        seed=args.seed,
        players=args.players,
        timeout=args.timeout,
        max_answers=args.max_answers,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
