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


# ---------------------------------------------------------------------------
# M5-08c：固定【酒】tape（spec D12 / §6 A2）
#
# 一个固定种子（160 档 / 8 人 / 人类座位 0）跑两段**真 HTTP**：
#   ① 出牌阶段：人类起始手牌就有【酒】；提交一次 = 恰一条 UseCard（目标为自己）+
#      wine_change，下一问里【酒】不再进候选；
#   ② 濒死自救：同一种子按固定策略（出牌阶段结束 / 能放弃就放弃 / 否则首个候选）
#      打到人类 0 血，求桃窗口里的【酒】目标恒为自己；提交后回复 1、wine_active 仍为
#      false、零 wine_change。
# 种子 16 由 ``tools/acceptance_seed.py --players 8 --deck-profile standard_ex_160
# --card wine`` 起步，再按上面两段脚本确认（同 seed 可复现）。
# ---------------------------------------------------------------------------

_WINE_TAPE_SEED = 16


def _wine_tape_create(base: str) -> tuple[str, int, int, dict[str, Any]] | None:
    status, body = call(
        f"{base}/api/sessions",
        {
            "player_count": 8,
            "ai": "heuristic",
            "seed": _WINE_TAPE_SEED,
            "human_seat": 0,
            "timeout_seconds": None,
            "deck_profile": "standard_ex_160",
        },
    )
    if status != 200:
        print(f"FAIL 酒 tape：建局失败 HTTP {status} {body}")
        return None
    if body.get("deck_profile") != "standard_ex_160":
        print(f"FAIL 酒 tape：档位不是 standard_ex_160：{body.get('deck_profile')}")
        return None
    return body["session_id"], body["cursor"], body["log_seq"], body


def _wine_tape_answer(
    base: str, session: tuple[str, int, int], action: dict[str, Any]
) -> tuple[int, Any]:
    session_id, cursor, log_seq = session
    return call(
        f"{base}/api/sessions/{session_id}/answer?after={cursor}&logs={log_seq}",
        {"action": action},
    )


def _wine_offers(pending: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        action
        for action in pending.get("legal_actions") or []
        if action.get("kind") == "UseCard"
        and (action.get("card") or {}).get("type") == "wine"
    ]


def _wine_tape_play(base: str) -> int:
    """出牌阶段：一次真实 UseCard【酒】往返 + 候选 / wine_change / 下一问读数。"""
    created = _wine_tape_create(base)
    if created is None:
        return 1
    session_id, cursor, log_seq, body = created
    session = (session_id, cursor, log_seq)
    pending = body.get("pending") or {}
    offers = _wine_offers(pending)
    if pending.get("kind") != "PLAY_PHASE" or pending.get("actor") != 0 or len(offers) != 1:
        print(
            "FAIL 酒 tape（出牌阶段）：首问不是人类出牌阶段 / 没有唯一一张【酒】："
            f"{pending.get('kind')}"
        )
        return 1
    wine_action = offers[0]
    if wine_action.get("targets") != [0]:
        print(f"FAIL 酒 tape（出牌阶段）：【酒】的目标不是自己：{wine_action.get('targets')}")
        return 1
    entry = (pending.get("candidate_index") or {}).get(str(wine_action["card"]["card_id"]))
    if entry is None or entry.get("pool") != [0] or entry.get("combos") != [[0]]:
        print(f"FAIL 酒 tape（出牌阶段）：候选索引不是「只对自己」：{entry}")
        return 1

    status, body = _wine_tape_answer(base, session, wine_action)
    if status != 200:
        print(f"FAIL 酒 tape（出牌阶段）：应答被拒 HTTP {status} {body}")
        return 1
    records = body.get("records") or []
    accepted = [
        record for record in records
        if record.get("kind") == "action" and record.get("accepted")
    ]
    if len(accepted) != 1:
        print(f"FAIL 酒 tape（出牌阶段）：本次应答的 action 记录不是恰一条：{len(accepted)}")
        return 1
    action = accepted[0].get("action") or {}
    # 投影对**所有** action 记录只下发 kind / answers_seq（`visible_record`）：具体牌与
    # 目标不回放；这里能钉的是"恰一条被接受的 UseCard"，具体牌 / 目标由提交体与
    # wine_change / 手牌读数共同证明。
    if action.get("kind") != "UseCard":
        print(f"FAIL 酒 tape（出牌阶段）：记录流里的动作不是 UseCard：{action}")
        return 1
    changes = [record for record in records if record.get("delta_type") == "wine_change"]
    if len(changes) != 1 or changes[0].get("seat") != 0 or changes[0].get("active") is not True:
        print(f"FAIL 酒 tape（出牌阶段）：wine_change 不是「座位 0 置位」恰一条：{changes}")
        return 1
    next_pending = body.get("pending") or {}
    if _wine_offers(next_pending):
        print("FAIL 酒 tape（出牌阶段）：使用后同一回合里【酒】仍在候选里")
        return 1
    if (body.get("snapshot") or {}).get("players", [{}])[0].get("wine_active") is not True:
        print("FAIL 酒 tape（出牌阶段）：投影没有下发 wine_active=true")
        return 1
    narrative = "\n".join(entry["text"] for entry in body.get("narrative") or [])
    if "使用【酒】" not in narrative:
        print(f"FAIL 酒 tape（出牌阶段）：事件流读不出「使用【酒】」：{narrative}")
        return 1
    print(
        "PASS 酒 tape（出牌阶段）：1 次真实 POST 的 UseCard【酒】目标为自己、"
        "wine_change=true、下一问不再有【酒】、事件流「使用【酒】」"
    )
    return 0


def _wine_tape_near_death(base: str) -> int:
    """濒死自救：固定策略打到 0 血，窗口里点一次【酒】= 回复 1、无 wine_change。"""
    created = _wine_tape_create(base)
    if created is None:
        return 1
    session_id, cursor, log_seq, body = created
    session = (session_id, cursor, log_seq)
    answers = 0
    while not body.get("finished"):
        pending = body.get("pending") or {}
        if not pending:
            print("FAIL 酒 tape（濒死）：未终局但也没有待应答的询问")
            return 1
        offers = _wine_offers(pending)
        if pending.get("kind") == "PEACH" and pending.get("actor") == 0 and offers:
            if (body.get("snapshot") or {}).get("players", [{}])[0].get("hp") != 0:
                print("FAIL 酒 tape（濒死）：自救窗口里人类不是 0 血")
                return 1
            if offers[0].get("targets") != [0]:
                print(f"FAIL 酒 tape（濒死）：自救的【酒】目标不是自己：{offers[0].get('targets')}")
                return 1
            status, body = _wine_tape_answer(base, session, offers[0])
            if status != 200:
                print(f"FAIL 酒 tape（濒死）：自救应答被拒 HTTP {status} {body}")
                return 1
            player = (body.get("snapshot") or {}).get("players", [{}])[0]
            if player.get("hp") != 1:
                print("FAIL 酒 tape（濒死）：【酒】自救没有回复到 1")
                return 1
            if player.get("wine_active") is not False:
                print("FAIL 酒 tape（濒死）：自救产生了加伤状态")
                return 1
            records = body.get("records") or []
            if any(record.get("delta_type") == "wine_change" for record in records):
                print("FAIL 酒 tape（濒死）：自救产生了 wine_change")
                return 1
            accepted = [
                record for record in records
                if record.get("kind") == "action" and record.get("accepted")
            ]
            if len(accepted) != 1 or (accepted[0].get("action") or {}).get("kind") != "UseCard":
                print(f"FAIL 酒 tape（濒死）：自救不是恰一条 UseCard：{accepted}")
                return 1
            narrative = "\n".join(entry["text"] for entry in body.get("narrative") or [])
            if "使用【酒】" not in narrative or "回复 1 点体力" not in narrative:
                print(f"FAIL 酒 tape（濒死）：事件流读不出使用【酒】/ 回复 1：{narrative}")
                return 1
            print(
                f"PASS 酒 tape（濒死自救）：{answers} 次真实 POST 推进到 0 血，窗口里一次"
                " UseCard【酒】回复 1、无 wine_change、事件流可读"
            )
            return 0
        action = None
        if pending.get("kind") == "PLAY_PHASE":
            action = next(
                (item for item in pending.get("legal_actions") or [] if item.get("kind") == "EndPhase"),
                None,
            )
        if action is None:
            action = next(
                (item for item in pending.get("legal_actions") or [] if item.get("kind") == "Pass"),
                None,
            )
        if action is None:
            action = (pending.get("legal_actions") or [None])[0]
        if action is None:
            print("FAIL 酒 tape（濒死）：这一步没有可提交的候选")
            return 1
        status, body = _wine_tape_answer(base, session, action)
        if status != 200:
            print(f"FAIL 酒 tape（濒死）：应答被拒 HTTP {status} {body}")
            return 1
        cursor, log_seq = body["cursor"], body["log_seq"]
        session = (session_id, cursor, log_seq)
        answers += 1
        if answers > 200:
            print("FAIL 酒 tape（濒死）：200 次应答内没有走到人类的自救窗口")
            return 1
    print("FAIL 酒 tape（濒死）：对局在出现自救窗口前结束")
    return 1


def wine_tape(base: str) -> int:
    """两段固定【酒】tape（出牌阶段 / 濒死自救），全部经真 HTTP。"""
    return _wine_tape_play(base) or _wine_tape_near_death(base)


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
    parser.add_argument("--tape", choices=("fire_attack", "wine"), default=None)
    args = parser.parse_args(argv)
    if args.probe_skills:
        return probe_skills(args.base)
    if args.tape == "fire_attack":
        return fire_attack_tape(args.base)
    if args.tape == "wine":
        return wine_tape(args.base)
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
