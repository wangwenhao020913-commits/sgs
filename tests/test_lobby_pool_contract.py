"""开局页「可用真名 ≥ 座位数」判据 ↔ 引擎 fail-closed 的**跨语言契约**（M6 review fix）。

`lobby.js:poolBlockReason` 为了"提交前先拦"必须在本地重算引擎的真名池规则——这是前端
唯一重复引擎规则的地方。本文件用 **真实 `lobby.js`**（`tests/support/pool_probe.mjs`
经 `node:vm` 加载，不另抄一份 JS）与 **真实引擎**（`deal_heroes` 的 fail-closed）对同一批
禁将 / 手选场景逐条比对：任何一侧的阈值差一（少拦或多拦）都判红，因此这条规则不能悄悄漂移。

场景覆盖三类边界：

- **无手选**：可用真名 = 未禁版本覆盖的真名数，``seats - 1`` 拦、``seats`` 放；
- **手选一个仍有可用版本的武将**：手选先占真名，总阈值不变；
- **手选一个全部版本都被禁的武将**：手选无视禁将、该真名仍算一个座位，
  故边界整体左移一位（``seats - 2`` 拦、``seats - 1`` 放）。

引擎侧的比较对象是会话配置真正走的 :func:`sgs.data.deal_heroes`（``hero_pool=()`` =
启用池，与 `GET /api/heroes` 下发给开局页的目录同源），不是测试里重算的一份规则。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

from sgs.data import HeroDealConfig, HeroDealError, HeroPick, deal_heroes  # noqa: E402
from sgs.engine import RandomSource  # noqa: E402
from sgs_web.serve import hero_catalog  # noqa: E402

PROBE = REPO_ROOT / "tests" / "support" / "pool_probe.mjs"


def _groups(catalog: list[dict]) -> list[tuple[str, list[str]]]:
    """目录 → ``(真名, 版本 id 列表)``；与 `hero_catalog` 的分组同形。"""
    return [
        (group["true_name"], [version["id"] for version in group["versions"]])
        for group in catalog
    ]


def _versions(groups: list[tuple[str, list[str]]], indices: range) -> list[str]:
    return [hero_id for index in indices for hero_id in groups[index][1]]


def _scenarios(catalog: list[dict]) -> list[dict]:
    """三组边界场景；每侧都取"刚好不够"与"刚好够"两个相邻阈值。"""
    groups = _groups(catalog)
    scenarios: list[dict] = []
    for seats in (2, 5, 8):
        # ① 无手选：留 k 个可用真名。
        for k in (seats - 1, seats):
            scenarios.append(
                {
                    "name": f"{seats}座/无手选/可用{k}",
                    "seats": seats,
                    "banned_heroes": _versions(groups, range(k, len(groups))),
                    "pick": None,
                }
            )
        # ② 手选仍有可用版本的武将：它先占真名，可用真名总数不变。
        pick = groups[0][1][0]
        for k in (seats - 1, seats):
            scenarios.append(
                {
                    "name": f"{seats}座/手选可用武将/可用{k}",
                    "seats": seats,
                    "banned_heroes": [
                        version
                        for version in groups[0][1]
                        if version != pick
                    ]
                    + _versions(groups, range(k, len(groups))),
                    "pick": pick,
                }
            )
        # ③ 手选全禁的武将：手选无视禁将、先占真名 ⇒ 边界左移一位。
        pick = groups[0][1][0]
        for k in (seats - 2, seats - 1):
            scenarios.append(
                {
                    "name": f"{seats}座/手选全禁武将/其余可用{k}",
                    "seats": seats,
                    "banned_heroes": groups[0][1]
                    + _versions(groups, range(k + 1, len(groups))),
                    "pick": pick,
                }
            )
    return scenarios


def _engine_blocks(scenario: dict) -> bool:
    """引擎侧的唯一判据：``deal_heroes`` 是否以「真名池不足」拒绝。"""
    picks = (
        ()
        if scenario["pick"] is None
        else (HeroPick(seat=0, hero=scenario["pick"]),)
    )
    config = HeroDealConfig(
        banned_heroes=tuple(scenario["banned_heroes"]),
        picks=picks,
    )
    try:
        deal_heroes(
            seat_count=scenario["seats"],
            stream=RandomSource(20261025).substream("hero"),
            hero_pool=(),
            hero_config=config,
        )
    except HeroDealError as error:
        assert "真名池不足" in str(error), (
            f"场景 {scenario['name']} 被引擎因别的原因拒绝：{error}"
        )
        return True
    return False


def _probe_results(scenarios: list[dict]) -> dict[str, bool]:
    """跑真实 `lobby.js` 的 `poolBlockReason`（node 探针），返回 场景名 → 是否拦。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    assert PROBE.is_file(), f"缺少真名池探针：{PROBE.relative_to(REPO_ROOT)}"
    payload = json.dumps(
        {"catalog": hero_catalog(), "scenarios": scenarios}, ensure_ascii=False
    )
    result = subprocess.run(
        [node, str(PROBE.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, (
        f"真名池探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:  # pragma: no cover - 探针坏了
        raise AssertionError(
            f"探针 stdout 不是合法 JSON：{error}\n{result.stdout[:2000]}"
        ) from error
    return {item["name"]: item["blocked"] for item in data["results"]}


def test_the_lobby_threshold_agrees_with_the_engine_fail_closed_boundary() -> None:
    """逐场景：`poolBlockReason` 拦 ⇔ 引擎以「真名池不足」拒绝。"""
    scenarios = _scenarios(hero_catalog())
    assert len(scenarios) == 18, "边界场景数变了：确认 seats 档位 / 三类场景都还在"

    js_blocks = _probe_results(scenarios)
    mismatches: list[str] = []
    for scenario in scenarios:
        engine_blocks = _engine_blocks(scenario)
        js_blocked = js_blocks[scenario["name"]]
        if js_blocked != engine_blocks:
            mismatches.append(
                f"{scenario['name']}：浏览器侧 blocked={js_blocked}，"
                f"引擎 fail-closed={engine_blocks}（{scenario['banned_heroes']!r}）"
            )

    assert not mismatches, "开局页与引擎的真名池阈值漂移：\n" + "\n".join(mismatches)

    # 防"两侧恰好都永远拦 / 都永远放"的空转绿：三档座位都必须各有一个放行和一个拒绝。
    for seats in (2, 5, 8):
        outcomes = {
            scenario["name"]: js_blocks[scenario["name"]]
            for scenario in scenarios
            if scenario["seats"] == seats
        }
        assert any(outcomes.values()), f"{seats} 座没有任何拒绝场景：{outcomes}"
        assert not all(outcomes.values()), f"{seats} 座没有任何放行场景：{outcomes}"
