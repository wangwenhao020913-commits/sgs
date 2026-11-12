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
from collections.abc import Callable
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


def settle_after_create(base: str, body: dict[str, Any]) -> dict[str, Any]:
    """建局之后**取一次增量**，把引擎推过 L0 边界（table-ux-overhaul 票 14）。

    建局响应止于"游戏开始时"（``GAME_START`` 派发完、第一回合之前）：那一份里
    ``pending is None`` 是**合法**态，人类第一问要由下一次取增量驱动出来——真网页客户端
    走的也是这条路（开局批播完立刻 ``refresh()``）。本助手就是那一次取增量，返回收口后的
    视图（它带 ``pending``；若该局在 L0 就走到了终局则照返回）。

    调用点只有三个**真产品服务**的建局（正局 / 火攻 tape / 酒 tape）；构造局面夹具
    （``m5_fixture_server`` / ``m6_fixture_server``）不传 ``pause_after_game_start``，
    建局即停在人类第一问上，故不经这里。
    """
    if body.get("finished") or body.get("pending") is not None:
        return body
    status, settled = call(
        f"{base}/api/sessions/{body['session_id']}"
        f"?after={body['cursor']}&logs={body['log_seq']}"
    )
    if status != 200:
        print(f"FAIL 建局后的第一次取增量失败：HTTP {status} {settled}")
        return body
    return settled


def settle_pauses(
    base: str,
    session_id: str,
    body: dict[str, Any],
    *,
    limit: int = 400,
    visit: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], int]:
    """把五谷的**逐目标暂停**取到下一个挂起 / 终局（table-ux-overhaul 票 19）。

    接入层在每个目标取牌后让出一次驱动：一次应答之后 ``pending is None`` 但还没终局是
    **合法**态，人类的下一个询问要由下一次取增量驱动出来——真网页客户端这时就是再取一次
    增量（2 s 轮询那条路）。这里逐次取，直到视图里又有挂起 / 终局。

    每次取到的视图先交给 ``visit``（可选：调用方用它收账 / 收叙事，**中间那一批记录不能丢**），
    然后返回 ``(最后一份视图, 取了几次)``；``0`` = 一次都没取（调用方传进来的 body 已经带
    挂起 / 终局，或第一次请求就失败）。构造局面夹具（``m5_fixture_server`` /
    ``m6_fixture_server``）的 tape 里没有五谷，故那几条固定 tape 恒取 0 次；真正的 8 人
    正局会撞到它。
    """
    polls = 0
    for _ in range(limit):
        if body.get("finished") or body.get("pending") is not None:
            return body, polls
        status, body = call(
            f"{base}/api/sessions/{session_id}?after={body['cursor']}&logs={body['log_seq']}"
        )
        if status != 200:
            print(f"FAIL 暂停后的取增量失败：HTTP {status} {body}")
            return body, polls
        polls += 1
        if visit is not None:
            visit(body)
    return body, polls


def _identity_win_failure(
    game_end: dict[str, Any], known: dict[int, str | None]
) -> str | None:
    """终局 ``side`` / ``winners`` 与**公开可知**身份的一致性（``None`` = 一致）。

    ``known`` 只装三类公开事实：本人身份、主公身份、``identity_reveal`` 亮明过的身份；
    暗置的对手身份不猜。规则口径与 `06`（§5「程序化约定与偏离一手」）一致：主忠胜 = 主公在胜方且胜方没有反贼 / 内奸；
    反贼胜 = 胜方没有主公 / 忠臣 / 内奸；内奸胜 = 胜方恰一人且（若已知）是内奸。
    """
    side = game_end.get("side")
    winners = tuple(game_end.get("winners") or ())
    if game_end.get("result") == "draw":
        return None if side is None and not winners else "平局却带 side / winners"
    if side is None:
        return None  # 无身份局：winner 是唯一存活座位，另有判据
    if not winners:
        return "身份局胜局必须带 winners"
    if side == "lord":
        lord_seats = [seat for seat, identity in known.items() if identity == "lord"]
        if lord_seats and lord_seats[0] not in winners:
            return "主公不在主忠胜方"
        wrong = [seat for seat in winners if known.get(seat) in ("rebel", "renegade")]
        if wrong:
            return f"主忠胜方里出现了反贼 / 内奸：{wrong}"
    elif side == "rebel":
        wrong = [
            seat for seat in winners if known.get(seat) in ("lord", "loyalist", "renegade")
        ]
        if wrong:
            return f"反贼胜方里出现了主公 / 忠臣 / 内奸：{wrong}"
    elif side == "renegade":
        if len(winners) != 1:
            return "内奸胜的胜方必须恰一人"
        identity = known.get(winners[0])
        if identity is not None and identity != "renegade":
            return f"内奸胜方的已知身份不是内奸：{identity}"
    else:
        return f"未知的 side：{side!r}"
    return None


def play(
    base: str,
    *,
    seed: int,
    players: int,
    timeout: float | None,
    max_answers: int,
    deck_profile: str | None = None,
    expect_mixed: bool = False,
) -> int:
    create: dict[str, Any] = {
        "player_count": players,
        "ai": "heuristic",
        "seed": seed,
        "human_seat": 0,
        "timeout_seconds": timeout,
    }
    if deck_profile is not None:
        create["deck_profile"] = deck_profile
    status, body = call(f"{base}/api/sessions", create)
    if status != 200:
        print(f"FAIL 建局失败：HTTP {status} {body}")
        return 1
    if deck_profile is not None and body.get("deck_profile") != deck_profile:
        print(f"FAIL 建局档位不是 {deck_profile}：{body.get('deck_profile')}")
        return 1
    # 票 14：建局响应止于 GAME_START —— 取一次增量才拿到人类的第一个询问。
    body = settle_after_create(base, body)

    session_id = body["session_id"]
    cursor = body["cursor"]
    log_seq = body["log_seq"]
    answers = 0
    rejected = 0
    human_answers = 0
    queries = 0
    pauses = 0
    game_end: dict[str, Any] | None = None
    reveals: list[dict[str, Any]] = []

    def note_pause_records(payload: dict[str, Any]) -> None:
        """五谷逐目标暂停取回来的那几份视图照样要收账（终局记录可能就在里面）。"""
        nonlocal game_end
        for record in payload["records"]:
            if record["kind"] == "game_end":
                game_end = record
            if record.get("delta_type") == "identity_reveal":
                reveals.append(record)

    while not body["finished"]:
        pending = body["pending"]
        if pending is None:
            # 票 19：五谷的逐目标暂停 —— 没有 pending 是合法态，真客户端这时再取一次增量。
            body, polls = settle_pauses(base, session_id, body, visit=note_pause_records)
            if polls == 0:
                print("FAIL 未终局但也没有待应答的询问（取增量也没有推进）")
                return 1
            pauses += polls
            if pauses > 400:
                print("FAIL 超过 400 次暂停取增量仍未终局")
                return 1
            cursor = body["cursor"]
            log_seq = body["log_seq"]
            continue
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
            if record.get("delta_type") == "identity_reveal":
                reveals.append(record)

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

    # ① 混合池（M6 票 20 / spec P1-2）：默认将池是标准版 + 界版的并集，8 人身份局必须真的
    # 同时抽到两版；只跑标准池的旧判据看不出"混合局可玩"。
    snapshot = body["snapshot"]
    # 逐座位武将 id 的真源是会话顶层的 ``heroes``（`PlayerPublic.hero` 带 ``exclude``，
    # 不进投影载荷——那是为了 47 条金录像的字节不变）。
    hero_ids = [hero_id for hero_id in body.get("heroes") or () if hero_id]
    if expect_mixed:
        if not hero_ids:
            print("FAIL 混合池：快照里没有任何武将（无武将模式不是混合池）")
            return 1
        jie = [hero for hero in hero_ids if str(hero).startswith("jie_")]
        standard = [hero for hero in hero_ids if not str(hero).startswith("jie_")]
        if not jie or not standard:
            print(f"FAIL 混合池：这一局没有同时抽到标准 / 界版两版武将：{sorted(hero_ids)}")
            return 1
        print(
            f"PASS 混合池：标准 {len(standard)} 名 + 界版 {len(jie)} 名 "
            f"（{', '.join(sorted(hero_ids))}）"
        )

    # ② 终局与身份胜负一致（spec P1-2）：用**公开可知**的身份（本人 / 主公 / 已亮明）
    # 复核 game_end 的 side + winners；不可知的暗置身份不猜。
    known: dict[int, str | None] = {}
    for player in snapshot.get("players") or []:
        if player.get("seat") == snapshot.get("seat"):
            known[player["seat"]] = player.get("identity")
        elif player.get("identity") == "lord":
            known[player["seat"]] = "lord"
    for reveal in reveals:
        known[reveal["seat"]] = reveal.get("identity")
    failure = _identity_win_failure(game_end, known)
    if failure is not None:
        print(f"FAIL 终局与身份胜负不一致：{failure}（已知身份 {known}）")
        return 1
    if game_end.get("side") is not None:
        print(
            f"PASS 终局与身份胜负一致：side={game_end['side']} winners={game_end['winners']}"
        )

    # 不泄露（判据 2）：人类座位收到的响应体里，**存活**的对手身份必须是 None（主公除外——
    # 主公身份公开），且任何对手都不出现手牌牌面；牌堆只有数量。
    # 「已阵亡」是**允许**公开的三种情形之一（`06` §M4 判据 2 的原文），故这里不拦它。
    snapshot = body["snapshot"]
    seat = snapshot["seat"]
    for player in snapshot["players"]:
        if player["seat"] == seat:
            if player.get("identity") in (None, ""):
                print(f"FAIL 人类座位看不到自己的身份：{player}")
                return 1
            continue
        # 身份可见性：本人 / 主公 / 已亮明 / 阵亡 —— 存活且非主公的对手必须是 ``None``。
        if player["alive"] and player.get("identity") not in (None, "lord"):
            print(f"FAIL 存活对手的身份泄露：{player}")
            return 1
        if "self_hand" in player or "hand" in player or "hand_cards" in player:
            print(f"FAIL 对手手牌牌面泄露：{player}")
            return 1
        if not isinstance(player.get("hand_count"), int):
            print(f"FAIL 对手手牌没有数量（只有牌面 / 缺字段）：{player}")
            return 1
        if "known" in player or "revealed_cards" in player:
            print(f"FAIL 手牌区出现「已知」持久状态：{player}")
            return 1
    if "deck" in snapshot or "deck_cards" in snapshot:
        print(f"FAIL 牌堆下发了牌面 / 顺序：{sorted(snapshot)}")
        return 1
    if not isinstance(snapshot.get("deck_count"), int):
        print("FAIL 快照没有牌堆数量")
        return 1
    print(
        "PASS 快照无对手手牌牌面（deck_count=%d，存活对手身份均为空，牌堆只有数量）"
        % snapshot["deck_count"]
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
# M6 P0（启用池 16 → 19）后 seed 37 的人类在对局里活不到自己的出牌阶段、火攻用不出来；
# 用同一份固定脚本重扫到 seed 68（判据未放宽：仍是"对 AI 用火攻 + 目标没有同花色"）。
# 票 20（C1 记牌）改了 AI 的出牌打分：seed 68 里目标（座位 1）不再被别家拆掉【无懈】，
# 它自己按 D1（"害 ⇒ 对自己宣"）把火攻**无懈**掉 ⇒ 没有亮牌、没有「放弃（无可弃的同花色牌）」，
# 这条 tape 失效。按**同一份固定脚本、同一组判据**重扫到 seed 63（对 AI 用火攻 / 目标亮牌 /
# 人类无同花色 / 零伤害；探针 `.scratch/ai-light/probe20_fire_seed.py`，扫 1–259）。
# 票 21（I1 身份后验）又改了 AI 的打分：seed 63 的人类在对局里活不到自己的出牌阶段（火攻
# 用不出来）⇒ 这条 tape 再次失效。仍按**同一份固定脚本、同一组判据**（对 AI 用火攻 / 目标
# 亮牌 / 没被无懈 / 人类无同花色 / 零伤害）重扫 1–259，首个命中 **seed 93**（探针同上，判据
# 逐字未改；命中：4 / 93 / 113 / 128 / 164 / 181 / 211 / 221 / 232，取第一个未用过的）。
# 票 04（神关羽移动版进默认池，56 → 57 版本）又改了配将的抽样面：seed 4 的人类开局拿不到
# 【火攻】⇒ self 分支失效。仍按**同一份固定脚本、同一组判据**（对自己用火攻 / 自己亮牌 /
# 有同花色弃牌询问 / 恰 1 点火焰伤害 / 零非法应答）重扫 1–119，首个命中 **seed 47**
# （探针 `.scratch/shenhua-zailin/probe_fire_self_seed.py`，判据逐字未改）。
# huo-mobile-batch 票 02 改了 `_score_reforge`（按牌价扣项，避免【连环】让 AI 白扔好牌）
# ⇒ 47 又失效。仍按同一份固定脚本、同一组判据（对自己用火攻 / 自己亮牌 / 有同花色弃牌询问 /
# 恰 1 点火焰伤害 / 零非法应答）重扫 1–259，命中 4 / 37 / 44 / 105 / 119 / 200 / 225 / 259，
# 取首个未在用的 **seed 4**（探针同上，判据逐字未改）。
# 这类夹具依赖 AI 的行为面是既定性质：改打分就要照同一条口径重扫，**不许放宽判据**。
# ---------------------------------------------------------------------------

_FIRE_TAPES = (
    # (seed, 目标是不是自己, 是否期望出现同花色弃牌询问)
    (4, True, True),
    # huo-shenzhouyu-mobile 票 01（神周瑜移动版进默认池，67 → 68 版本）又改了配将的抽样面：
    # seed 93 的人类开局拿不到可用的火攻局面（整局没打出【火攻】）⇒ 这条 tape 失效。
    # 仍按**同一份固定脚本、同一组判据**（对 AI 用火攻 / 目标亮牌 / 没被无懈 / 人类无同花色 /
    # 零伤害 / 零非法应答）重扫 1–259，命中 37 / 70 / 105，取首个未在用的 **seed 37**
    # （探针 `.scratch/huo-shenzhouyu-mobile/scan_fire_seed.py`，判据逐字未改）。
    # huo-wolong-yanliang 票 01（火包 OL 卧龙诸葛进默认池，78 → 79 版本）又挪了配将抽样面：
    # seed 37 的人类整局没有可用【火攻】局面 ⇒ tape 再次失效。判据**逐字不动**
    # （对 AI 用火攻 / 目标亮牌 / 没被无懈 / 人类无同花色 / 零伤害 / 零非法应答），
    # 用同一支探针 `.scratch/huo-shenzhouyu-mobile/scan_fire_seed.py` 重扫 1–259，
    # 命中 4 / 46 / 56（4 已占用），取首个未在用的 **seed 46**。
    # jushou-lusu 票 01（一将成名-2014·沮授进默认池，89 → 90 版本）同样挪了配将抽样面：
    # seed 46 的人类整局没有可用【火攻】局面 ⇒ tape 失效。判据**逐字不动**，用同一支探针
    # 重扫 1–259，命中 16 / 65 / 81 / 99 / 105 / 107 / 116 / 128 / 176 / 225，
    # 取首个未在用的 **seed 16**（探针 `.scratch/huo-shenzhouyu-mobile/scan_fire_seed.py`）。
    (16, False, False),
)


def _is_own_hand_face(pending: dict[str, Any]) -> bool:
    """这一步的选择面是不是**人类自己的手牌**（票 09 的形状判据，与前端同源）。

    前端 `candidates.js:isOwnZonePick` 读的就是这份形状：`SELECT_CARDS` ∧ 每槽
    `ref.seat == actor` ∧ `ref.zone == "hand"` ∧ 牌面可见。票 09 ①（雌雄）与 ②（火攻亮牌步）
    都靠它落成**牌桌直选**——`dialog` 不再给 `"reveal"`。
    """
    if pending.get("kind") != "SELECT_CARDS":
        return False
    slots = pending.get("slots") or []
    if not slots:
        return False
    actor = pending.get("actor")
    return all(
        (slot.get("ref") or {}).get("seat") == actor
        and (slot.get("ref") or {}).get("zone") == "hand"
        and slot.get("card")
        for slot in slots
    )


def _fire_reveal_step(pending: dict[str, Any]) -> bool:
    """【火攻】的**亮牌步**（票 09 起是牌桌直选）：自己的可见手牌面 + 还没亮出公开牌。

    同一个技能**第二步**（同花色弃牌）的槽位形状一样，区别是它的载荷带 `reveal`
    （已公开的那一张）——故用这个键把两步分开。
    """
    return pending.get("reveal") is None and _is_own_hand_face(pending)


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
        if _fire_reveal_step(pending):
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


_FIRE_TAPE_BANNED_HEROES = (
    # 雷包（lei-batch 票 01）。
    "haozhao",
    "zhangxiu",
    "zhugezhan",
    # 神话再临·林包·移动版（lin-batch 票 01 / 02 / 03）：新武将进默认池会重排随机配将。
    "shen_caocao_mobile",
    "sunjian_mobile",
    "xuhuang_mobile",
    # lin-batch2 票 01：林包移动版曹丕移动版（seed=46 那一局不加这条就整局用不出【火攻】）。
    "caopi_mobile",
    # lin-batch2 票 02：林包移动版董卓移动版（8 体力 + 每回合崩坏 ⇒ 同样会重排随机配将；
    # 新武将进默认池就往这里追加是既定规矩）。
    "dongzhuo_mobile",
    # lin-batch2 票 03：林包移动版贾诩移动版（加武将重排随机配将 ⇒ 追加，**不重扫 seed**）。
    "jiaxu_mobile",
)
"""火攻 tape 的**禁将清单** = 「加武将之前」的默认池（版本粒度、只作用于随机配将）。

新武将进默认池时**往这里追加**（见 :func:`fire_attack_tape` 的 docstring）：tape 钉的是
火攻的界面与记录流，不是将池；重扫 seed 会把"加武将"和"tape 改坏"两件事混成一次红。
林包三将分票落地，故 02 / 03 也各追加自己那一行（`sunjian_mobile` / `xuhuang_mobile`）；
lin-batch2 票 01 的曹丕移动版同理（实测：不加 ⇒ `seed=46 目标=AI` 那条 tape 整局没有
用出【火攻】）。
"""


def fire_attack_tape(base: str) -> int:
    """两条固定种子的火攻 tape；全部经真 HTTP，任何违规立即 FAIL。

    **禁将钉死"加武将前"的将池**（lei-batch 票 01）：这两条 tape 钉的是**火攻的界面与
    记录流**（亮牌 / 同花色弃牌 / 放弃），不是将池；而随机配将会随池子变大重排整局，
    让"整局没摸到【火攻】"变成假红（真发生过：加郝昭后 seed=37 那一局不再用出火攻）。
    故这里显式禁掉后加的雷包武将，把局面钉回原来的 72 人池。**再加武将时把它加进这条
    禁将清单**，不要重扫种子。

    lin-batch（神话再临·林包·移动版，神曹操 / 孙坚 / 徐晃）同样按这条规矩追加：新武将进默认池
    会重排随机配将，tape 钉的是火攻的界面与记录流、不是将池 —— 加 `_FIRE_TAPE_BANNED_HEROES`，
    **不重扫 seed**。
    """
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
                # 见 docstring：钉死加武将前的将池（版本粒度、只作用于随机配将）。
                "banned_heroes": _FIRE_TAPE_BANNED_HEROES,
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
        # 票 14：建局响应止于 GAME_START —— 取一次增量才拿到人类的第一个询问。
        body = settle_after_create(base, body)
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
                # 票 19：五谷的逐目标暂停。这条 tape 的构造局面里没有五谷（`_m5_create`
                # 的 fixture 手牌写死），故恒取 0 次；真出现就是夹具换了，按失败报出来。
                body, polls = settle_pauses(
                    base,
                    session_id,
                    body,
                    visit=lambda payload: narrative.extend(
                        entry["text"] for entry in payload.get("narrative") or []
                    ),
                )
                if polls == 0:
                    print(f"FAIL 火攻 tape {label}：未终局但也没有待应答的询问")
                    failures += 1
                    break
                cursor = body["cursor"]
                log_seq = body["log_seq"]
                continue
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
        if target_self and not reveal_seen:
            # 票 09 ②：人类**自己**被指定时的亮牌步是**牌桌直选**（`dialog` 为空、牌面在
            # `slots[].card` 里）。这一条把"固定策略还认得出亮牌步"钉住——认不出就说明
            # 载荷形状又漂了。人类指定 AI 的那一段亮牌由 AI 做（人类载荷里没有这一步），
            # 故只在 `target_self` 这一段要求。
            print(
                f"FAIL 火攻 tape {label}：亮牌步没有被识别成牌桌直选"
                "（票 09 起的形状：SELECT_CARDS + 己方可见手牌 + dialog 为空）"
            )
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
# --card wine`` 起步，再按上面两段脚本确认（同 seed 可复现）。M6 P0（启用池 16 → 19）
# 换池后 seed 16 的对局在人类走到 0 血之前就结束了，故**两段各自选种**：
# 出牌阶段那段仍要"座位 0 首发 + 起始手牌恰有一张【酒】"（seed 16），濒死自救那段按
# 同一固定策略重扫到 seed 8（判据未放宽：人 0 血 + 窗口里的【酒】目标为自己）。
# M6 P0 票 02（启用池 19 → 22）又换池：seed 8 的对局在人类走到 0 血之前结束，按同一
# 判据重扫到 seed 50（同一固定策略、同一命中判据；出牌阶段那段 seed 16 不受影响）。
# M6 P0 票 03（启用池 22 → 25，P0 收口）再换池：seed 50 的对局在出现自救窗口前结束，
# 按同一判据从 1 起重扫到首个命中 **seed 34**（同一固定策略：出牌阶段结束 / 能放弃就
# 放弃 / 否则首个候选；命中判据未放宽：人类 0 血 + 求桃窗口里的【酒】目标恒为自己）。
# 出牌阶段那段 seed 16 仍命中，故只换濒死自救这一段。
# M6 票 12（默认池改为标 / 界混合池）再换池：seed 34 的对局在出现自救窗口前结束，
# 按同一判据从 1 起重扫到首个命中 **seed 9**（策略与命中判据逐字未变）。
# M6 票 14（界·魏 A 四名入池，版本 37 → 41）再换池：seed 9 的对局在出现自救窗口前
# 结束，按同一判据从 1 起重扫到首个命中 **seed 34**（策略与命中判据逐字未变）。
# M6 票 15（界·魏 B 两名入池，版本 41 → 43）再换池：seed 34 的对局在出现自救窗口前
# 结束，按同一判据从 1 起重扫到首个命中 **seed 20**（策略与命中判据逐字未变）。
# M6 票 18（界·群四名入池，版本 50 → 54）再换池：seed 20 的对局在出现自救窗口前结束，
# 按同一判据从 1 起重扫到首个命中 **seed 28**（同一固定策略：出牌阶段结束 / 能放弃就
# 放弃 / 否则首个候选；命中判据未放宽：人类 0 血 + 求桃窗口里的【酒】目标恒为自己）。
# 票 01（神吕蒙移动版进池，版本 57 → 58）再换池：出牌阶段那段 seed 16 的首问变成了
# 摸牌阶段的技能询问（不是人类出牌阶段），按同一判据从 1 起重扫到首个命中 **seed 65**
# （8 人 / heuristic / standard_ex_160 / 人类 0：首问 = 出牌阶段、恰一张【酒】、目标为自己）。
# 濒死自救那段暂未重扫——本池下 seed 28 仍按同一策略命中（若红，按同一判据重扫）。
# 票 01（风包魏延进池，版本 59 → 60 / 真名 33 → 34）再换池：seed 28 的对局在出现自救窗口前
# 结束，按同一判据从 1 起重扫到首个命中 **seed 4**（策略与命中判据逐字未变；出牌阶段那段
# seed 65 不受影响）。
# ---------------------------------------------------------------------------

_WINE_TAPE_SEED = 65
"""出牌阶段那段：首问就是人类出牌阶段、起始手牌里恰一张【酒】。

huo-shenzhugeliang 票 01（默认池 69/43 → 70/44）再换池：seed 65 的首问变成了 DODGE
（AI 先动手），按同一判据从 1 起重扫到首个命中 **seed 71**（8 人 / heuristic /
standard_ex_160 / 人类 0：首问 = 出牌阶段、恰一张【酒】、目标为自己）。

huo-wolong-yanliang 票 01（默认池 78/49 → 79/50，火包 OL 加卧龙诸葛）再换池：seed 71 的
首问不再是人类出牌阶段，按同一判据（同一固定策略、命中断言逐字未改）重扫，取首个未在用的
**seed 8**（探针 `.scratch/huo-wolong-yanliang/scan_wine_seed.py`）。

票 02（默认池 79/50 → 80/51，加颜良文丑）再换池：seed 8 的首问又变了；同一判据重扫命中
**46 / 65 / 71 / 84**（46 已被【火攻】tape 占用），取 **seed 65**。"""

_WINE_TAPE_NEAR_DEATH_SEED = 12
"""濒死自救那段：同一固定策略能把人类打到 0 血、此时手里还有【酒】。

huo-taishici-xunyu 票 01 / 02（默认池 70/44 → 72/46，火包移动版再加太史慈标准版 / 荀彧标准版）
再换池：seed 4 的对局在出现自救窗口前结束，按同一判据从 1 起重扫到首个命中 **seed 16**
（同一固定策略：出牌阶段结束 / 能放弃就放弃 / 否则首个候选；命中判据未放宽：人类 0 血 +
求桃窗口里的【酒】目标恒为自己）。

tao-caocao 票 01（默认池 75/49 → 76/49，自制包加韬·曹操自制版）再换池：seed 16 的对局在
出现自救窗口前结束，按同一判据从 1 起重扫到首个命中 **seed 37**（同一固定策略、同一命中
判据：人类 0 血 + 求桃窗口里的【酒】目标恒为自己）。

huo-wolong-yanliang 票 01 / 02（默认池 78/49 → 79/50 → 80/51）再换池：seed 37 的对局在出现
自救窗口前结束，按同一判据重扫（探针 `.scratch/huo-wolong-yanliang/scan_wine_near_death_seed.py`），
命中 **1 / 4 / 16 / 27 / 36 …**，取首个未在用的 **seed 27**。

lin-batch2 票 01（默认池 86/57 → 87/58，林包移动版加曹丕移动版）再换池：seed 27 的对局在
出现自救窗口前结束，按**同一判据、同一固定策略**从 1 起重扫（探针
`.scratch/lin-batch2/scan_wine_near_death_seed.py`，逐字复用 `_wine_tape_near_death` 的策略），
首个命中 **seed 1**（70 次真实 POST 推进到 0 血 + 一次【酒】自救）；seed 1 未被其余 tape 占用，
故取它。

lin-batch2 票 03（默认池 88/59 → 89/60，林包移动版加贾诩移动版）再换池：seed 1 的对局在出现
自救窗口前结束，按**同一判据、同一固定策略**从 1 起重扫（同一个探针
`.scratch/lin-batch2/scan_wine_near_death_seed.py`，策略与命中断言逐字未改），1–15 全部未命中、
首个命中 **seed 16**（25 次真实 POST 推进到 0 血 + 一次【酒】自救）；16 未被其余 tape 占用
（火攻 4 / 46、出牌阶段 65），故取它。

table-ux-overhaul 票 05（询问策略：网页会话缺省 ON）：每次 drive 问人类座位的次数变少 ⇒ 一次
响应里 AI 回合跑得更远，seed 16 在"自救成功"之后、人类被问到下一问之前就死了（快照读不到
`hp == 1`，自救本身是成功的：hp 0→1 的 `hp_change` 在记录流里）。按**同一固定策略、同一命中
判据**从 1 起重扫（本文件就是探针：`:func:`_wine_tape_near_death` 逐 seed 跑，`io.redirect_stdout`
吞掉逐条 FAIL），命中最短路径的 **seed 9**（17 次真实 POST）；9 未被其余 tape 占用
（火攻 4 / 16、出牌阶段 65）。

sp5-mayunlu-panjun 票 01 / 02（默认池 98/64 → 100/66，SP武将组5 两将）再换池：seed 9 的对局
在出现自救窗口前结束，按**同一判据、同一固定策略**从 1 起重扫（探针
`.scratch/sp5-mayunlu-panjun/scan_wine_seed.py`，逐字复用 `_wine_tape_near_death` 的策略），
首个命中 **seed 12**（7 次真实 POST 推进到 0 血 + 求桃窗口里的【酒】目标为自己、回复到 1）；12 未被其余 tape 占用（火攻 4 / 16、出牌阶段 65）。"""


def _wine_tape_create(base: str, *, seed: int = _WINE_TAPE_SEED) -> tuple[str, int, int, dict[str, Any]] | None:
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
        print(f"FAIL 酒 tape：建局失败 HTTP {status} {body}")
        return None
    if body.get("deck_profile") != "standard_ex_160":
        print(f"FAIL 酒 tape：档位不是 standard_ex_160：{body.get('deck_profile')}")
        return None
    # 票 14：建局响应止于 GAME_START —— 取一次增量才拿到人类的第一个询问。
    body = settle_after_create(base, body)
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
    created = _wine_tape_create(base, seed=_WINE_TAPE_NEAR_DEATH_SEED)
    if created is None:
        return 1
    session_id, cursor, log_seq, body = created
    session = (session_id, cursor, log_seq)
    answers = 0
    while not body.get("finished"):
        pending = body.get("pending") or {}
        if not pending:
            # 票 19：五谷的逐目标暂停 —— 没有 pending 是合法态，取增量接着走。
            body, polls = settle_pauses(base, session_id, body)
            if polls == 0:
                print("FAIL 酒 tape（濒死）：未终局但也没有待应答的询问")
                return 1
            cursor = body["cursor"]
            log_seq = body["log_seq"]
            continue
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
            # 回应里可能夹着**同一次 drive 里 AI 座位**的动作（无 actor 字段可分辨），故判据
            # 取"第一条 accepted 就是本次提交的 UseCard"（提交的动作恒排在最前）；
            # 早先版本要求恰一条，那在"救援后 drive 继续走到 AI 座位"的种子上会误判。
            if not accepted or (accepted[0].get("action") or {}).get("kind") != "UseCard":
                print(f"FAIL 酒 tape（濒死）：自救不是一条 UseCard：{accepted}")
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


# ---------------------------------------------------------------------------
# M5-09a：固定 tape（构造局面 / 不依赖随机种子；spec D12 / §6 A2）
#
# 三条 tape 都连**同一份产品 HTTP 门面**（``tests/support/m5_fixture_server.py`` 只把
# 「按种子开局」换成「按名字取构造局面」），每次询问一条真实 POST：
#   - ``fire_attack``：人类**对自己**用火攻 ⇒ 亮牌 / 同花色弃牌两步都是**人类**的真 POST，
#     随后【杀】收局；对手的暗手在整个过程中不外泄；
#   - ``reforge``：一次 ``Reforge`` 真实往返（独立 Action、弃一张摸一张、无无懈窗口）；
#   - ``wine``：出牌阶段一次【酒】真实往返（``wine_change`` / ``wine_active``）+【杀】收局。
# 与 08a / 08c 的 seed 4/37/16 tape 的区别：这三条**不经过种子扫描**，手牌 / 体力 /
# 牌堆全部在 fixture 里写死，视频证据之外的 A2 判据由本函数承担。
# ---------------------------------------------------------------------------

_M5_FIXTURES = ("fire_attack", "reforge", "wine")
_M5_DECK_PROFILE = "standard_ex_160"


def _m5_create(
    base: str, fixture: str, *, profile: str = _M5_DECK_PROFILE
) -> tuple[str, int, int, dict[str, Any]] | None:
    status, body = call(
        f"{base}/api/sessions",
        {
            "fixture": fixture,
            "player_count": 2,
            "human_seat": 0,
            "timeout_seconds": None,
        },
    )
    if status != 200:
        print(f"FAIL 固定 tape（{fixture}）：建局失败 HTTP {status} {body}")
        return None
    if body.get("deck_profile") != profile:
        print(f"FAIL 固定 tape（{fixture}）：档位不是 {profile}")
        return None
    if body.get("human_seat") != 0 or body.get("player_count") != 2:
        print(f"FAIL 固定 tape（{fixture}）：建局面不是「2 人 / 人类座 0」")
        return None
    return body["session_id"], body["cursor"], body["log_seq"], body


def _pick_action(
    pending: dict[str, Any],
    *,
    kind: str | None = None,
    card_type: str | None = None,
    targets: list[int] | None = None,
) -> dict[str, Any] | None:
    for action in pending.get("legal_actions") or []:
        if kind is not None and action.get("kind") != kind:
            continue
        if card_type is not None and (action.get("card") or {}).get("type") != card_type:
            continue
        if targets is not None and action.get("targets") != targets:
            continue
        return action
    return None


def _m5_play(
    base: str,
    fixture: str,
    choose,
    observe,
    *,
    profile: str = _M5_DECK_PROFILE,
    state: dict[str, Any] | None = None,
) -> int:
    """跑完一条固定 tape：每次人类询问一条真 POST，逐条收集记录与叙事。

    ``state`` 可由调用方传入（M6 的 tape 用它收集"这个原语真的发生了"的旗标，跑完再断言）。
    """
    created = _m5_create(base, fixture, profile=profile)
    if created is None:
        return 1
    session_id, cursor, log_seq, body = created
    answers = 0
    rejected = 0
    human_answers = 0
    records: list[dict[str, Any]] = []
    narrative: list[str] = []
    game_end: dict[str, Any] | None = None
    state = {} if state is None else state

    def collect(payload: dict[str, Any]) -> None:
        for record in payload.get("records") or []:
            records.append(record)
            if record.get("kind") == "action" and not record.get("accepted"):
                rejected_nonlocal[0] += 1
            if record.get("kind") == "game_end":
                nonlocal_game_end[0] = record
        narrative.extend(entry["text"] for entry in payload.get("narrative") or [])

    rejected_nonlocal = [0]
    nonlocal_game_end: list[dict[str, Any] | None] = [None]
    while not body.get("finished"):
        pending = body.get("pending")
        if not pending:
            # 票 19：五谷的逐目标暂停。构造局面夹具里没有五谷 ⇒ 恒取 0 次；真出现就是
            # 夹具换了。中间那几份视图照样 `collect`（终局记录可能就在里面）。
            body, polls = settle_pauses(base, session_id, body, visit=collect)
            if polls == 0:
                print(f"FAIL 固定 tape（{fixture}）：未终局但也没有待应答的询问")
                return 1
            cursor, log_seq = body["cursor"], body["log_seq"]
            if body.get("pending") is None and not body.get("finished"):
                print(f"FAIL 固定 tape（{fixture}）：取增量也没有推进出下一个询问")
                return 1
            continue
        if observe is not None and not observe("pending", body, pending, state):
            return 1
        action = choose(pending, state)
        if action is None:
            print(f"FAIL 固定 tape（{fixture}）：这一步没有固定策略可选的候选：{pending.get('kind')}")
            return 1
        if not any(action == candidate for candidate in pending.get("legal_actions") or []):
            print(f"FAIL 固定 tape（{fixture}）：提交动作不在 legal_actions 里：{action}")
            return 1
        status, body = call(
            f"{base}/api/sessions/{session_id}/answer?after={cursor}&logs={log_seq}",
            {"action": action},
        )
        if status != 200:
            print(f"FAIL 固定 tape（{fixture}）：应答被拒 HTTP {status} {body}")
            return 1
        answers += 1
        if answers > 200:
            print(f"FAIL 固定 tape（{fixture}）：200 次应答仍未终局")
            return 1
        cursor, log_seq = body["cursor"], body["log_seq"]
        for record in body.get("records") or []:
            if (
                record.get("kind") == "action"
                and record.get("accepted")
                and (record.get("action") or {}).get("answers_seq") == pending.get("seq")
            ):
                human_answers += 1
        collect(body)
        if observe is not None and not observe("answer", body, pending, state):
            return 1

    rejected = rejected_nonlocal[0]
    game_end = nonlocal_game_end[0]
    if game_end is None:
        print(f"FAIL 固定 tape（{fixture}）：终局没有 game_end 记录")
        return 1
    if rejected:
        print(f"FAIL 固定 tape（{fixture}）：记录流里有 {rejected} 条 accepted=false")
        return 1
    if human_answers != answers:
        print(
            f"FAIL 固定 tape（{fixture}）：HTTP 应答数 {answers} != "
            f"人类座位的 action 记录数 {human_answers}"
        )
        return 1
    if observe is not None and not observe("end", body, None, state):
        return 1
    print(
        f"PASS 固定 tape（{fixture}）：{answers} 次真实 POST、零非法应答，"
        f"终局 {game_end['result']}（{game_end['reason']}）"
    )
    return 0


def _fire_attack_fixed(base: str) -> int:
    """火攻：人类**自己**亮牌（真 HTTP 的 SelectCards POST）；对手的暗手不外泄。"""
    # 只用于断言亮牌归属 / 负断言「对手的暗手没有泄露」；夹具是构造局面，不参与 HTTP。
    from m5_fixture_server import build_fixture

    fixture_state = build_fixture("fire_attack").state
    opponent_hand = [
        {
            "card_id": fixture_state.card(card_id).card_id,
            "name": fixture_state.card(card_id).name,
        }
        for card_id in fixture_state.players[1].hand
    ]
    assert len(opponent_hand) == 2, "火攻夹具的对手必须恰有两张暗手（负断言用）"

    def mentions_card(value, card_id: int) -> bool:
        """载荷里有没有 ``card_id`` 这张牌面（按 ``card_id`` 键判，不扫裸整数）。"""
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("card_id") == card_id:
                    return True
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        return False

    def opponent_hand_not_leaked(body) -> bool:  # noqa: ANN001
        leaked = [
            entry for entry in opponent_hand if mentions_card(body, entry["card_id"])
        ]
        if leaked:
            print(f"FAIL 火攻固定 tape：对手未亮出的手牌泄露了：{leaked}")
            return False
        return True

    def accepted_actions(records, seq):  # noqa: ANN001
        return [
            record.get("action") or {}
            for record in records
            if record.get("kind") == "action"
            and record.get("accepted")
            and (record.get("action") or {}).get("answers_seq") == seq
        ]

    def choose(pending, state):  # noqa: ANN001
        if pending.get("kind") == "PLAY_PHASE":
            if not state.get("fire_used"):
                fire = _pick_action(pending, card_type="fire_attack", targets=[0])
                if fire is not None:
                    state["fire_used"] = True
                    return fire
            slash = _pick_action(pending, kind="UseCard", card_type="slash", targets=[1])
            if slash is not None:
                return slash
            return _pick_action(pending, kind="EndPhase")
        if pending.get("kind") == "SELECT_CARDS":
            # 亮牌步（人类选自己 1 张，必选）与弃牌步（人类弃同花色）：都取 SelectCards，
            # 弃牌步不取 Pass——这一步也要有一条真人 POST。
            return _pick_action(pending, kind="SelectCards")
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:  # noqa: ANN001
        records = body.get("records") or []
        if kind == "answer":
            for record in records:
                if record.get("delta_type") == "card_reveal":
                    state["reveal"] = record.get("card") or {}
                    state["reveal_count"] = state.get("reveal_count", 0) + 1
                    state["reveal_seat"] = record.get("seat")
            if pending is not None and _fire_reveal_step(pending):
                # A2：亮牌必须是一条**人类**的 SelectCards POST（不是 AI 亮牌）：人类座位
                # 自己的应答记录 + 紧接着的 card_reveal 公开 delta 都落在这一次 POST 的响应里。
                # 票 09 ②：亮牌步是**牌桌直选**——牌面在 `slots[].card` 里（不再是 `revealed`
                # 授予），`dialog` 为空；本段顺带钉住这两条。
                accepted = accepted_actions(records, pending.get("seq"))
                if len(accepted) != 1 or accepted[0].get("kind") != "SelectCards":
                    print(f"FAIL 火攻固定 tape：亮牌应答不是一条人类 SelectCards：{accepted}")
                    return False
                if pending.get("dialog") is not None or pending.get("revealed"):
                    print(
                        "FAIL 火攻固定 tape：己方手牌的亮牌步仍是弹窗载荷"
                        f"（dialog={pending.get('dialog')} revealed={pending.get('revealed')}）"
                    )
                    return False
                faces = {
                    (slot.get("card") or {}).get("card_id")
                    for slot in pending.get("slots") or []
                }
                revealed = [
                    (record.get("card") or {}).get("card_id")
                    for record in records
                    if record.get("delta_type") == "card_reveal"
                ]
                if len(revealed) != 1 or revealed[0] not in faces:
                    print(
                        "FAIL 火攻固定 tape：亮牌 delta 不是人类手牌里被选中/公开的那一张："
                        f"{revealed} vs {sorted(faces)}"
                    )
                    return False
                state["human_reveal"] = revealed[0]
                state["human_reveal_answered"] = True
            if pending is not None and pending.get("reveal") is not None:
                accepted = accepted_actions(records, pending.get("seq"))
                if len(accepted) != 1 or accepted[0].get("kind") != "SelectCards":
                    print(f"FAIL 火攻固定 tape：同花色弃牌不是一条人类 SelectCards：{accepted}")
                    return False
                revealed = (pending.get("reveal") or {}).get("card") or {}
                moves = [
                    move
                    for move in records
                    if move.get("delta_type") == "card_move"
                    and move.get("reason") == "DISCARD"
                    and move.get("owner") == 0
                    and (move.get("from_zone") or {}).get("zone") == "hand"
                ]
                discarded = (moves[0].get("cards") or []) if len(moves) == 1 else []
                if len(discarded) != 1 or discarded[0].get("suit") != revealed.get("suit"):
                    print(
                        "FAIL 火攻固定 tape：弃掉的牌与亮牌不同花色 / 没有弃牌："
                        f"{discarded} vs {revealed}"
                    )
                    return False
                state["discard_answered"] = True
        if kind == "pending" and pending is not None:
            if not opponent_hand_not_leaked(body):
                return False
            if _fire_reveal_step(pending):
                if pending.get("actor") != 0:
                    print(f"FAIL 火攻固定 tape：亮牌询问不是人类座位：{pending.get('actor')}")
                    return False
                # 票 09 ②：这一步是**牌桌直选**——`dialog` 为空、己方手牌的牌面不再塞进
                # `revealed`；牌面读 `slots[].card`（与前端 `isOwnZonePick` 同一份形状）。
                if pending.get("dialog") is not None:
                    print(
                        "FAIL 火攻固定 tape：亮牌步仍带弹窗标记"
                        f"（dialog={pending.get('dialog')}）——票 09 起是牌桌直选"
                    )
                    return False
                if pending.get("revealed"):
                    print(
                        "FAIL 火攻固定 tape：己方手牌的牌面仍被塞进 revealed"
                        f"（票 09 起不授予）：{pending.get('revealed')}"
                    )
                    return False
                slots = pending.get("slots") or []
                faces = [(slot.get("card") or {}).get("card_id") for slot in slots]
                if not faces or any(
                    (slot.get("ref") or {}).get("seat") != 0
                    or (slot.get("ref") or {}).get("zone") != "hand"
                    for slot in slots
                ):
                    print(f"FAIL 火攻固定 tape：亮牌步的选择面不是人类自己的手牌：{faces}")
                    return False
                state["human_faces"] = set(faces)
                state["hand_count_before_discard"] = len(slots)
                state["reveal_pending"] = True
            if pending.get("reveal") is not None:
                reveal = pending["reveal"]
                card = reveal.get("card") or {}
                if not card.get("display_name") or not card.get("description"):
                    print(f"FAIL 火攻固定 tape：亮牌没有展示契约：{card}")
                    return False
                shown = card.get("card_id")
                if shown not in state.get("human_faces", set()):
                    print(f"FAIL 火攻固定 tape：亮出的牌不在人类手牌里：{card}")
                    return False
                if state.get("human_reveal") != shown:
                    print(
                        "FAIL 火攻固定 tape：弃牌步展示的亮牌不是人类刚亮出的那张："
                        f"{shown} != {state.get('human_reveal')}"
                    )
                    return False
                target = next(
                    player for player in body["snapshot"]["players"] if player["seat"] == 0
                )
                if target.get("hand_count") != state.get("hand_count_before_discard"):
                    print(f"FAIL 火攻固定 tape：亮牌后人类手牌数不对：{target}")
                    return False
                if any(
                    key in target
                    for key in ("known", "revealed_cards", "self_hand", "hand")
                ):
                    print(f"FAIL 火攻固定 tape：手牌区出现牌面 / 已知状态：{target}")
                    return False
                state["discard_pending"] = True
        if kind == "end":
            if not state.get("reveal"):
                print("FAIL 火攻固定 tape：整局没有 card_reveal 公开记录")
                return False
            if state.get("reveal_count") != 1:
                print(f"FAIL 火攻固定 tape：card_reveal 不是恰一条：{state.get('reveal_count')}")
                return False
            if state.get("reveal_seat") != 0:
                print(f"FAIL 火攻固定 tape：亮牌者不是人类座位：{state.get('reveal_seat')}")
                return False
            if not state.get("reveal_pending") or not state.get("human_reveal_answered"):
                print("FAIL 火攻固定 tape：亮牌不是一次真实的人类 HTTP 往返")
                return False
            if not state.get("discard_pending") or not state.get("discard_answered"):
                print("FAIL 火攻固定 tape：没有走到同花色弃牌的人类应答")
                return False
            if not state.get("fire_used"):
                print("FAIL 火攻固定 tape：整局没有用出【火攻】")
                return False
            players = body["snapshot"]["players"]
            if players[0].get("hp") != 3:
                print(f"FAIL 火攻固定 tape：同花色弃牌后的火焰伤害不对：{players[0]}")
                return False
            if players[1].get("alive") is not False:
                print(f"FAIL 火攻固定 tape：收局前对手还活着：{players[1]}")
                return False
            for player in players:
                if "known" in player or "revealed_cards" in player:
                    print(f"FAIL 火攻固定 tape：结算后手牌区残留「已知」：{player}")
                    return False
        return True

    return _m5_play(base, "fire_attack", choose, observe)


def _reforge_fixed(base: str) -> int:
    """重铸：一次真实 ``Reforge`` 往返；不算使用、不开无懈窗口、弃一张摸一张。"""

    def choose(pending, state):  # noqa: ANN001
        if pending.get("kind") == "PLAY_PHASE":
            if not state.get("reforged"):
                reforge = _pick_action(pending, kind="Reforge")
                if reforge is not None:
                    return reforge
            slash = _pick_action(pending, kind="UseCard", card_type="slash", targets=[1])
            if slash is not None:
                return slash
            return _pick_action(pending, kind="EndPhase")
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:
        if kind == "answer" and not state.get("reforged"):
            records = body.get("records") or []
            accepted = [r for r in records if r.get("kind") == "action" and r.get("accepted")]
            if len(accepted) != 1 or (accepted[0].get("action") or {}).get("kind") != "Reforge":
                print(f"FAIL 重铸固定 tape：重铸应答不是恰一条 Reforge：{accepted}")
                return False
            moves = [r for r in records if r.get("delta_type") == "card_move"]
            if not any((m.get("reason") == "REFORGE") for m in moves):
                print(f"FAIL 重铸固定 tape：没有 reason=REFORGE 的移动：{moves}")
                return False
            if not any(m.get("reason") == "DRAW" and m.get("owner") == 0 for m in moves):
                print(f"FAIL 重铸固定 tape：重铸后没有摸牌：{moves}")
                return False
            if any(
                r.get("kind") == "query"
                and (r.get("query") or {}).get("kind") == "RESPOND"
                and (r.get("query") or {}).get("cause") == "NULLIFY"
                for r in records
            ):
                print("FAIL 重铸固定 tape：重铸开了无懈窗口")
                return False
            state["reforged"] = True
        return True

    code = _m5_play(base, "reforge", choose, observe)
    if code != 0:
        return code
    return 0


def _wine_fixed(base: str) -> int:
    """酒：出牌阶段一次真实 UseCard（目标为自己）+ ``wine_change`` / ``wine_active``。"""

    def choose(pending, state):  # noqa: ANN001
        if pending.get("kind") == "PLAY_PHASE":
            if not state.get("wine_used"):
                wine = _pick_action(pending, card_type="wine", targets=[0])
                if wine is not None:
                    return wine
            slash = _pick_action(pending, kind="UseCard", card_type="slash", targets=[1])
            if slash is not None:
                return slash
            return _pick_action(pending, kind="EndPhase")
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:
        if kind == "answer" and not state.get("wine_used"):
            records = body.get("records") or []
            changes = [r for r in records if r.get("delta_type") == "wine_change"]
            if not any(c.get("seat") == 0 and c.get("active") is True for c in changes):
                print(f"FAIL 酒固定 tape：没有「座位 0 置位」的 wine_change：{changes}")
                return False
            player = body["snapshot"]["players"][0]
            if player.get("wine_active") is not True:
                print(f"FAIL 酒固定 tape：投影没有 wine_active=true：{player}")
                return False
            if any(
                (a.get("card") or {}).get("type") == "wine"
                for a in (body.get("pending") or {}).get("legal_actions") or []
            ):
                print("FAIL 酒固定 tape：使用后同一回合里【酒】仍在候选里")
                return False
            state["wine_used"] = True
        return True

    code = _m5_play(base, "wine", choose, observe)
    if code != 0:
        return code
    return 0


def m5_fixed_tapes(base: str) -> int:
    """三条固定 tape（火攻 / 重铸 / 酒），逐条真 HTTP；任一失败即 FAIL。"""
    failures = 0
    for name, run in (
        ("fire_attack", _fire_attack_fixed),
        ("reforge", _reforge_fixed),
        ("wine", _wine_fixed),
    ):
        if run(base) != 0:
            failures += 1
            print(f"FAIL 固定 tape（{name}）：未通过")
    if failures:
        return 1
    print(f"PASS 固定 tape：{len(_M5_FIXTURES)} 条构造局面全部经真 HTTP 跑完（零非法应答）")
    return 0


# ---------------------------------------------------------------------------
# M6 票 20：三条**构造 tape**（觉醒 / 额外回合 / 区域放置），各至少一次真实往返。
# 局面在 `tests/support/m6_fixture_server.py`，档位 standard_108。每次人类询问都是一条
# 真 POST；``observe`` 在拿到的响应体上核对"这个原语真的发生了"。``m6_tape_handlers``
# 把同一份 ``(choose, observe)`` 交给 pytest 的 real-handler 用例复用，不另抄一份计划。
# ---------------------------------------------------------------------------

_M6_PROFILE = "standard_108"


def _m6_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [record for record in payload.get("records") or [] if record.get("kind")]


def _play_phase_use(pending: dict[str, Any], *, card_type: str, targets: list[int]):
    return _pick_action(pending, kind="UseCard", card_type=card_type, targets=targets)


def _awakening_handlers():
    """界徐庶【潜心】觉醒（强制）后，人类发动 ☆【荐言】——两次选项询问都是真往返。"""

    def choose(pending, state):  # noqa: ANN001
        kind = pending.get("kind")
        if kind == "PLAY_PHASE":
            if not state.get("slashed"):
                slash = _play_phase_use(pending, card_type="slash", targets=[1])
                if slash is not None:
                    state["slashed"] = True
                    return slash
            if state.get("awakened") and not state.get("jianyan"):
                for action in pending.get("legal_actions") or []:
                    if action.get("skill_id") == "hero.jie_xushu.jianyan":
                        state["jianyan"] = True
                        return action
            if state.get("jianyan"):
                slash = _play_phase_use(pending, card_type="slash", targets=[1])
                if slash is not None:
                    return slash
            return _pick_action(pending, kind="EndPhase")
        if kind == "SELECT_OPTION":
            skill_id = str(pending.get("skill_id") or "")
            if skill_id.startswith("jianyan:type"):
                for action in pending.get("legal_actions") or []:
                    if action.get("choice") == "basic":
                        return action
            if skill_id.startswith("jianyan:seat"):
                for action in pending.get("legal_actions") or []:
                    if action.get("choice") == "seat:0":
                        return action
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:  # noqa: ANN001
        for record in _m6_records(body):
            if record.get("delta_type") == "hp_max_change" and record.get("seat") == 0:
                state["awakened"] = True
            if (
                record.get("delta_type") == "skill_gain"
                and record.get("skill_id") == "hero.jie_xushu.jianyan"
            ):
                state["awakened"] = True
        if kind == "pending" and state.get("awakened"):
            # ☆【荐言】是**出牌阶段裸候选**（不是 SKILL_ACTIVATION 询问）：觉醒后它应当
            # 出现在候选清单里，人类就是在这条骨架上经 HTTP 发动的。
            if any(
                action.get("skill_id") == "hero.jie_xushu.jianyan"
                for action in pending.get("legal_actions") or []
            ):
                state["jianyan_query"] = True
        return True

    return choose, observe


def _extra_turn_handlers():
    """界吕蒙【博图】：弃四花色 ⇒ 人类应答"是否发动" ⇒ 额外回合（同座位连着两个回合）。"""

    def choose(pending, state):  # noqa: ANN001
        kind = pending.get("kind")
        if kind == "DISCARD":
            for action in pending.get("legal_actions") or []:
                cards = action.get("cards") or []
                if len({card.get("suit") for card in cards}) == 4:
                    return action
            return (pending.get("legal_actions") or [None])[0]
        if kind == "SKILL_ACTIVATION":
            skill_id = str(pending.get("skill_id") or "")
            if skill_id == "hero.jie_lvmeng.botu":
                for action in pending.get("legal_actions") or []:
                    if action.get("kind") == "ActivateSkill":
                        state["botu"] = True
                        return action
            return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]
        if kind == "PLAY_PHASE":
            if state.get("extra"):
                slash = _play_phase_use(pending, card_type="slash", targets=[1])
                if slash is not None:
                    return slash
            return _pick_action(pending, kind="EndPhase")
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:  # noqa: ANN001
        for record in _m6_records(body):
            if record.get("delta_type") == "turn_change" and record.get("extra"):
                state["extra"] = True
        if (body.get("snapshot") or {}).get("turn_extra"):
            state["extra"] = True
        return True

    return choose, observe


def _hero_zone_handlers():
    """界陆逊【谦逊】：人类应答后全部手牌置于武将牌上；同帧【连营】也由人类应答。"""

    def choose(pending, state):  # noqa: ANN001
        kind = pending.get("kind")
        if kind == "PLAY_PHASE":
            if state.get("qianxun"):
                slash = _play_phase_use(pending, card_type="slash", targets=[1])
                if slash is not None:
                    return slash
            return _pick_action(pending, kind="EndPhase")
        if kind == "SKILL_ACTIVATION":
            skill_id = str(pending.get("skill_id") or "")
            if skill_id in ("hero.jie_luxun.qianxun", "hero.jie_luxun.lianying"):
                for action in pending.get("legal_actions") or []:
                    if action.get("kind") == "ActivateSkill":
                        state[skill_id] = True
                        return action
            return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]
        if kind == "SELECT_OPTION":
            skill_id = str(pending.get("skill_id") or "")
            if skill_id.startswith("lianying:") and not state.get("lianying_seat"):
                for action in pending.get("legal_actions") or []:
                    if action.get("choice") == "seat:0":
                        state["lianying_seat"] = True
                        return action
            return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]
        if kind == "SELECT_OPTIONS":
            # table-ux-overhaul 票 13：【连营】一次问齐 ⇒ 骨架候选（= 池里第一个座位，
            # 也就是"令自己摸"）就是本 tape 要的那份答案；不另造集合。
            if str(pending.get("skill_id") or "").startswith("lianying:"):
                skeleton = _pick_action(pending, kind="SelectOptions")
                if skeleton is not None:
                    state["lianying_seat"] = True
                    return skeleton
            return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]
        return _pick_action(pending, kind="Pass") or (pending.get("legal_actions") or [None])[0]

    def observe(kind, body, pending, state) -> bool:  # noqa: ANN001
        for record in _m6_records(body):
            if (
                record.get("delta_type") == "card_move"
                and (record.get("to_zone") or {}).get("zone") == "hero"
            ):
                state["placed"] = True
                state["qianxun"] = True
        if kind == "pending" and state.get("placed"):
            players = (body.get("snapshot") or {}).get("players") or []
            seat_zero = next(
                (player for player in players if player.get("seat") == 0), None
            )
            if seat_zero is not None:
                state["hero_zones"] = state.get("hero_zones") or bool(
                    seat_zero.get("hero_zones")
                )
        return True

    return choose, observe


M6_TAPES = ("awakening", "extra_turn", "hero_zone")
"""三条 tape 的名字（与 ``tests/support/m6_fixture_server.FIXTURES`` 的键一致）。"""

M6_TAPE_EXPECTATIONS: dict[str, tuple[str, ...]] = {
    "awakening": ("awakened", "jianyan_query", "jianyan"),
    "extra_turn": ("botu", "extra"),
    "hero_zone": ("placed", "qianxun", "hero_zones"),
}
"""每条 tape 必须命中的观察旗标（人类载荷上核对；缺一即 FAIL）。"""

M6_TAPE_FAILURES: dict[str, dict[str, str]] = {
    "awakening": {
        "awakened": "记录流里没有【潜心】觉醒（hp_max_change / skill_gain）",
        "jianyan_query": "觉醒后人类没有被问【荐言】的发动",
        "jianyan": "人类没有发动 ☆【荐言】",
    },
    "extra_turn": {
        "botu": "人类没有应答【博图】的发动",
        "extra": "没有落 turn_change(extra=True) / turn_extra",
    },
    "hero_zone": {
        "placed": "没有牌被置于武将牌上（HERO 区域）",
        "qianxun": "【谦逊】的发动没有被人类应答",
        "hero_zones": "查看者的快照里看不到武将牌上的牌区",
    },
}


def m6_tape_handlers(name: str):
    """一条 M6 tape 的 ``(choose, observe)``；HTTP 客户端与 pytest 共用同一份计划。"""
    return {
        "awakening": _awakening_handlers,
        "extra_turn": _extra_turn_handlers,
        "hero_zone": _hero_zone_handlers,
    }[name]()


def _run_m6_tape(base: str, name: str) -> int:
    choose, observe = m6_tape_handlers(name)
    state: dict[str, Any] = {}
    code = _m5_play(base, name, choose, observe, profile=_M6_PROFILE, state=state)
    if code != 0:
        return code
    missing = [flag for flag in M6_TAPE_EXPECTATIONS[name] if not state.get(flag)]
    if missing:
        for flag in missing:
            print(f"FAIL M6 固定 tape（{name}）：{M6_TAPE_FAILURES[name][flag]}")
        return 1
    return 0


def m6_fixed_tapes(base: str) -> int:
    """三条 M6 构造 tape（觉醒 / 额外回合 / 区域放置），逐条真 HTTP；任一失败即 FAIL。"""
    failures = 0
    for name in M6_TAPES:
        if _run_m6_tape(base, name) != 0:
            failures += 1
            print(f"FAIL M6 固定 tape（{name}）：未通过")
    if failures:
        return 1
    print("PASS M6 固定 tape：3 条构造局面全部经真 HTTP 跑完（觉醒 / 额外回合 / 区域放置）")
    return 0


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
    parser.add_argument("--deck-profile", default=None, help="牌堆档位（缺省 = 内容包默认档）")
    parser.add_argument("--max-answers", type=int, default=4000)
    parser.add_argument("--probe-skills", action="store_true")
    parser.add_argument(
        "--expect-mixed",
        action="store_true",
        help="断言这一局是标准版 + 界版的混合池（8 人身份局的两版武将都在场）",
    )
    parser.add_argument(
        "--tape", choices=("fire_attack", "wine", "m5_fixed", "m6_fixed"), default=None
    )
    args = parser.parse_args(argv)
    if args.probe_skills:
        return probe_skills(args.base)
    if args.tape == "fire_attack":
        return fire_attack_tape(args.base)
    if args.tape == "wine":
        return wine_tape(args.base)
    if args.tape == "m5_fixed":
        return m5_fixed_tapes(args.base)
    if args.tape == "m6_fixed":
        return m6_fixed_tapes(args.base)
    if args.seed is None:
        parser.error("--seed 必填（除非用 --probe-skills）")
    return play(
        args.base,
        seed=args.seed,
        players=args.players,
        timeout=args.timeout,
        max_answers=args.max_answers,
        deck_profile=args.deck_profile,
        expect_mixed=args.expect_mixed,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
