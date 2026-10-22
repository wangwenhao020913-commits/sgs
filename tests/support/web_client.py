"""`tests/test_web.sh` 的 JSON 客户端助手（bash 里解析 JSON 不现实，故这一小段住 Python）。

它**不是**测试：被 shell 门禁调用，走真 HTTP 打完一整局并把关键事实打成一行结论。
用法见 ``tests/test_web.sh``（``--port <N> --seed <N> --players <N> [--timeout <S>]``；
``--tape fire_attack`` = M5-08a 的固定火攻 tape，见 :func:`fire_attack_tape`）。

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


# ---------------------------------------------------------------------------
# M5-08a：固定【火攻】tape（spec D12 / §6 A2）
#
# 两条固定种子，都走**真 HTTP**：每次询问一条 POST、提交的动作原样取自该询问的
# ``legal_actions``；断言亮牌 / 同花色弃牌 / 放弃 / 无同花色自动放弃的可观测事实。
#   - seed 4：人类**对自己**用火攻 ⇒ 自己亮牌 + 弃同花色 ⇒ 恰 1 点火焰伤害；
#   - seed 37：人类**对 AI** 用火攻 ⇒ AI 亮牌；人类没有同花色 ⇒ 引擎不发弃牌询问，
#     事件流写「放弃（无可弃的同花色牌）」。
# 种子由 ``tools/acceptance_seed.py --players 8 --deck-profile standard_ex_160 --card
# fire_attack`` 起步、再用固定脚本在这两个种子上确认（同 seed 可复现）。
# ---------------------------------------------------------------------------

_FIRE_TAPES = (
    # (seed, 目标是不是自己, 是否期望出现同花色弃牌询问)
    (4, True, True),
    (37, False, False),
)


def _fire_choice(pending: dict[str, Any], *, target_self: bool, fa_used: bool) -> tuple[dict[str, Any], str]:
    """火攻 tape 的固定策略：返回（原样取自 ``legal_actions`` 的动作, 这一步的名字）。"""
    actions = pending.get("legal_actions") or []
    kind = pending.get("kind")
    if kind == "PLAY_PHASE" and not fa_used:
        for action in actions:
            card = action.get("card") or {}
            targets = action.get("targets") or []
            if action.get("kind") != "UseCard" or card.get("type") != "fire_attack":
                continue
            if target_self and targets == [0]:
                return action, "fire_attack"
            if not target_self and targets and targets != [0]:
                return action, "fire_attack"
    if kind == "SELECT_CARDS":
        if pending.get("dialog") == "reveal":
            for action in actions:
                if action.get("kind") == "SelectCards" and len(action.get("picks") or []) == 1:
                    return action, "reveal"
        if pending.get("reveal"):
            for action in actions:
                if action.get("kind") == "SelectCards":
                    return action, "discard"
            for action in actions:
                if action.get("kind") == "Pass":
                    return action, "discard_pass"
    for action in actions:
        if action.get("kind") == "Pass":
            return action, "pass"
    for action in actions:
        if action.get("kind") == "EndPhase":
            return action, "end"
    return actions[0], "first"


def fire_attack_tape(base: str) -> int:
    """两条固定种子的火攻 tape；全部经真 HTTP，任何违规立即 FAIL。"""
    failures = 0
    for seed, target_self, expect_discard in _FIRE_TAPES:
        label = f"seed={seed} 目标={'自己' if target_self else 'AI'}"
        status, body = call(
            f"{base}/api/sessions",
            {
                "player_count": 8,
                "ai": "heuristic",
                "seed": seed,
                "human_seat": 0,
                "timeout_seconds": None,
                "deck_profile": "standard_ex_160",
            },
        )
        if status != 200:
            print(f"FAIL 火攻 tape {label}：建局失败 HTTP {status} {body}")
            failures += 1
            continue
        if body.get("deck_profile") != "standard_ex_160":
            print(f"FAIL 火攻 tape {label}：档位不是 standard_ex_160：{body.get('deck_profile')}")
            failures += 1
            continue
        session_id = body["session_id"]
        cursor = body["cursor"]
        log_seq = body["log_seq"]
        fa_used = False
        resolving = False
        target_seat: int | None = None
        damage = 0
        reveal_seen = False
        discard_seen = False
        discard_pass_seen = False
        reveal_records: list[dict[str, Any]] = []
        narrative: list[str] = []
        answers = 0
        rejected = 0
        while not body["finished"]:
            pending = body["pending"]
            if pending is None:
                print(f"FAIL 火攻 tape {label}：未终局但也没有待应答的询问")
                failures += 1
                break
            action, step = _fire_choice(pending, target_self=target_self, fa_used=fa_used)
            if not any(action == candidate for candidate in pending["legal_actions"]):
                print(f"FAIL 火攻 tape {label}：提交动作不在 legal_actions 里：{action}")
                failures += 1
                break
            if step == "fire_attack":
                fa_used = True
                resolving = True
                target_seat = int((action.get("targets") or [0])[0])
                damage = 0
            if step == "reveal":
                reveal_seen = True
            if step == "discard":
                discard_seen = True
            if step == "discard_pass":
                discard_seen = True
                discard_pass_seen = True
            status, body = call(
                f"{base}/api/sessions/{session_id}/answer?after={cursor}&logs={log_seq}",
                {"action": action},
            )
            if status != 200:
                print(f"FAIL 火攻 tape {label}：应答被拒 HTTP {status} {body}")
                failures += 1
                break
            answers += 1
            if answers > 4000:
                print(f"FAIL 火攻 tape {label}：超过 4000 次应答仍未终局")
                failures += 1
                break
            cursor = body["cursor"]
            log_seq = body["log_seq"]
            for record in body["records"]:
                if record["kind"] == "action" and not record.get("accepted"):
                    rejected += 1
                if record.get("delta_type") == "card_reveal":
                    reveal_records.append(record)
                if (
                    resolving
                    and record.get("delta_type") == "hp_change"
                    and record.get("seat") == target_seat
                ):
                    damage += int(record.get("amount", 0))
            narrative.extend(entry["text"] for entry in body.get("narrative") or [])
            # 火攻结算结束 = 同一回合又回到人类的出牌阶段询问（期间的体力变化才归这一次火攻）。
            next_pending = body.get("pending") or {}
            if resolving and next_pending.get("kind") == "PLAY_PHASE" and answers:
                resolving = False
        if rejected:
            print(f"FAIL 火攻 tape {label}：记录流里有 {rejected} 条 accepted=false")
            failures += 1
            continue
        if not fa_used:
            print(f"FAIL 火攻 tape {label}：整局没有用出【火攻】（tape 失效）")
            failures += 1
            continue
        if not reveal_records:
            print(f"FAIL 火攻 tape {label}：没有 card_reveal 公开记录")
            failures += 1
            continue
        revealed_ids = [record["card"]["card_id"] for record in reveal_records]
        if any("display_name" not in record["card"] for record in reveal_records):
            print(f"FAIL 火攻 tape {label}：亮牌记录没有展示字段（中央舞台画不出牌面）")
            failures += 1
            continue
        if expect_discard:
            if not discard_seen:
                print(f"FAIL 火攻 tape {label}：有同花色却没有弃牌询问 / 弃牌")
                failures += 1
                continue
            if damage != -1:
                print(f"FAIL 火攻 tape {label}：同花色弃牌后的火焰伤害不对（{damage}）")
                failures += 1
                continue
            print(
                f"PASS 火攻 tape {label}：亮牌 {revealed_ids} + 同花色弃牌 ⇒ 1 点火焰伤害"
                f"（{answers} 次真实 POST，零非法应答）"
            )
        else:
            if discard_seen:
                print(f"FAIL 火攻 tape {label}：没有同花色却出现了弃牌询问（死步骤）")
                failures += 1
                continue
            if damage != 0:
                print(f"FAIL 火攻 tape {label}：放弃却造成了伤害（{damage}）")
                failures += 1
                continue
            text = "\n".join(narrative)
            if "放弃（无可弃的同花色牌）" not in text:
                print(f"FAIL 火攻 tape {label}：事件流没有「放弃（无可弃的同花色牌）」")
                failures += 1
                continue
            print(
                f"PASS 火攻 tape {label}：亮牌 {revealed_ids} + 无同花色 ⇒ 零伤害、"
                f"事件流写「放弃（无可弃的同花色牌）」（{answers} 次真实 POST，零非法应答）"
            )
    return 1 if failures else 0


def probe_skills(base: str) -> int:
    """建局响应必须带**本人**技能清单（刀 4）；引擎的可见面里没有技能。"""
    status, body = call(
        f"{base}/api/sessions",
        {"player_count": 5, "ai": "random", "seed": 20261012, "timeout_seconds": None},
    )
    if status != 200:
        print(f"FAIL 建局失败：HTTP {status} {body}")
        return 1
    skills = body.get("skills")
    if not skills:
        print(f"FAIL 响应体里没有 skills：{sorted(body.keys())}")
        return 1
    need = {"skill_id", "name", "skill_type", "locked", "carrier", "available"}
    for entry in skills:
        if not need <= set(entry):
            print(f"FAIL 技能条目字段不全：{entry}")
            return 1
        if not isinstance(entry["locked"], bool) or not isinstance(entry["available"], bool):
            print(f"FAIL locked / available 必须是布尔：{entry}")
            return 1
    print(f"PASS 建局响应带本人技能清单（{len(skills)} 条：{[s['name'] for s in skills]}）")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--players", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--max-answers", type=int, default=4000)
    parser.add_argument("--probe-skills", action="store_true")
    parser.add_argument("--tape", choices=("fire_attack",), default=None)
    args = parser.parse_args(argv)
    if args.probe_skills:
        return probe_skills(args.base)
    if args.tape == "fire_attack":
        return fire_attack_tape(args.base)
    if args.seed is None:
        parser.error("--seed 必填（除非用 --probe-skills）")
    return play(
        args.base,
        seed=args.seed,
        players=args.players,
        timeout=args.timeout,
        max_answers=args.max_answers,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
