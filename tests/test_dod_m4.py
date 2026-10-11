"""M4 完成定义收口：三条 DoD 逐条机械成立（`.scratch/m4-web-ui/spec.md` §Acceptance）。

三条判据：

1. **端到端人类局**——经真 HTTP 跑完一局 8 人身份局，人类座位逐个回答自己的每一次询问；
2. **不泄露**——给人类座位下发的任何字节里，对手只有手牌数量与公开区、身份只三种可见情形；
3. **回放闭环**——该局记录流经引擎重放逐行相同，且逐座位武将能从侧车元数据放回原位。

本文件把这些判据绑到**建立它们的用例 / 脚本**上：文件名与用例名被点名，删掉或改名即红。
三者的**严格性**由它们自己负责（`tests/test_web.sh` 与 `tests/test_web.py` 都在门禁里跑）。

递归禁令：本文件**不**调用 ``tests/run_all.sh``（那会在 pytest 里再起 pytest）。
M2 的八条登记在 ``tests/test_dod.py``，M3 的八条在 ``tests/test_dod_m3.py``。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
MILESTONES_DOC = ROOT / "开发文档" / "06-里程碑与风险.md"
DOC_GATE = TESTS_DIR / "test_dev_docs.sh"


class DoDItem(NamedTuple):
    """一条 M4 完成定义：中文条目 + 建立它的用例 / 脚本。"""

    title: str
    files: tuple[str, ...]
    literals: tuple[str, ...]


DOD_M4_ITEMS: dict[int, DoDItem] = {
    1: DoDItem(
        "端到端人类局：经真 HTTP 跑完一局 8 人身份局，零非法应答，"
        "且每次询问都真的经过 HTTP 往返",
        ("tests/test_web.sh", "tests/support/web_client.py", "tests/test_web.py"),
        (
            "PASS: M4 端到端门禁全部通过",
            "人类座位经真 HTTP 打完一局",
            "test_the_halt_driven_journal_is_byte_identical_to_a_one_shot_run",
            "test_the_session_drives_a_whole_game_to_the_end",
        ),
    ),
    2: DoDItem(
        "不泄露：人类座位收到的任何响应里，对手只有手牌数量与公开区，"
        "身份只出现本人 / 主公 / 已阵亡三种可见情形，牌堆只有数量无顺序",
        ("tests/test_web.py", "tests/support/web_client.py"),
        (
            "test_the_snapshot_never_exposes_other_hands_or_hidden_identity",
            "test_non_human_queries_are_redacted_in_the_wire_form",
            "test_the_human_seat_learns_its_own_identity",
            "PASS 快照无对手手牌牌面",
        ),
    ),
    3: DoDItem(
        "回放闭环：该局记录流经引擎重放逐行相同，且逐座位武将能从侧车元数据放回原位",
        ("tests/test_web.py", "tools/sgs_web/replay.py"),
        (
            "test_the_replay_closes_the_loop_from_the_sidecar_meta",
            "test_the_journal_round_trips_through_the_file",
            "test_the_three_hero_pool_states_stay_distinguishable",
            "def replay_journal(",
        ),
    ),
}

M4_LEDGER_ROWS: dict[str, str] = {
    "**等待人类 = 挂起驱动，不进引擎**": "M4 票 00",
    "**超时代答是纯函数且不进记录流**": "M4 票 00",
    "**逐座位武将从侧车元数据补**": "M4 票 00",
    "**回放页取上帝视角**": "M4 票 00",
}
"""`06` 第 5 节的四行 M4 账本：锚定串 → 票号列里必须出现的来源。"""

M4_GATE_CHECKS: tuple[str, ...] = (
    "M4：接入层落点",
    "M4：判据 1 端到端人类局",
    "M4：判据 2 不泄露",
    "M4：判据 3 回放闭环",
    "M4：ADR-012 入表",
    "M4：ADR-012 变更说明块",
    "M4 账本：挂起驱动",
    "M4 账本：超时纯函数",
    "M4 账本：武将侧车",
    "M4 账本：回放上帝视角",
)
"""文档门禁里 M4 那一段的断言描述（逐条必须出现）。"""


def test_every_m4_definition_of_done_item_is_established_by_a_real_file() -> None:
    """把三条判据绑到真实文件与真实用例名上——删掉或改名建立者即红。"""
    assert set(DOD_M4_ITEMS) == {1, 2, 3}, "M4 恰三条判据（spec Acceptance）"

    for number, item in DOD_M4_ITEMS.items():
        for relative in item.files:
            target = ROOT / relative
            assert target.is_file(), f"判据 {number} 的落点文件不存在：{relative}"
        text = "\n".join((ROOT / relative).read_text(encoding="utf-8") for relative in item.files)
        for literal in item.literals:
            assert literal in text, (
                f"判据 {number} 的落点里没有『{literal}』——建立它的用例 / 脚本被删或改名即红"
            )


def test_the_three_m4_criteria_are_written_in_the_milestones_doc() -> None:
    """`06` §M4 的三条机械判据与 scope 落点就位，且**指向对应的落点文件**。"""
    text = MILESTONES_DOC.read_text(encoding="utf-8")

    assert "### M4 可用界面 + 完整标准局" in text, "`06` 里 M4 那一节的标题变了"
    for literal in (
        "**端到端人类局**",
        "**不泄露**",
        "**回放闭环**",
        "接入层落点 = **`tools/sgs_web/`**",
        "**挂起驱动**",
        "**托管（AI 代打）保留为功能、不进判据**",
    ):
        assert literal in text, f"`06` §M4 缺『{literal}』"


def test_adr_012_is_in_the_milestones_doc_and_remands_adr_005() -> None:
    """ADR-012 入表 + 变更说明块 + **显式改判 ADR-005**（WebSocket 与 React 两项）。"""
    text = MILESTONES_DOC.read_text(encoding="utf-8")

    assert "| ADR-012 |" in text, "ADR-012 不在 `06` 第 4 节的表内"
    assert "**ADR-012 变更说明" in text, "ADR-012 缺变更说明块"
    assert "**改判 ADR-005 的 WebSocket 与 React 两项**" in text, "ADR-012 没有登记对 ADR-005 的改判"
    assert "规则文档 = **无**（接入层形态不涉及规则文本）" in text, "ADR-012 缺连带同步：规则文档"
    assert "配置项 = **无**（超时是接入层的会话参数" in text, "ADR-012 缺连带同步：配置项"
    assert "相关测试 = `tests/test_web.sh`（端到端人类局）" in text, "ADR-012 缺连带同步：相关测试"


def test_the_four_m4_ledger_rows_carry_all_five_fields() -> None:
    """`06` 第 5 节的四行 M4 账本：五字段齐全、票号列引到本 effort 的票 00。"""
    lines = MILESTONES_DOC.read_text(encoding="utf-8").splitlines()

    assert len(M4_LEDGER_ROWS) == 4, "M4 规则级账本恰好四行"
    for marker, expected_reference in M4_LEDGER_ROWS.items():
        matches = [line for line in lines if line.startswith("|") and marker in line]
        assert len(matches) == 1, f"账本行锚定串『{marker}』命中 {len(matches)} 行（应为 1）"
        cells = [cell.strip() for cell in matches[0].strip().strip("|").split("|")]
        assert len(cells) == 5, (
            f"账本行『{marker}』的字段数为 {len(cells)}（应为 5：裁定 / 来源类型 / "
            f"一手出处与偏离点 / 票号 / 落地位置）"
        )
        assert all(cells), f"账本行『{marker}』有空字段：{cells}"
        assert expected_reference in cells[3], (
            f"账本行『{marker}』的票号列缺『{expected_reference}』：{cells[3]}"
        )


def test_the_doc_gate_whitelists_the_m4_milestone_and_the_m4_checks() -> None:
    """文档门禁含 `06:M4` 白名单，且 M4 那一段的断言逐条在位。"""
    text = DOC_GATE.read_text(encoding="utf-8")

    assert 'MILESTONE_WHITELIST="|' in text, "文档门禁没有里程碑白名单"
    _, _, rest = text.partition('MILESTONE_WHITELIST="|')
    whitelist = f"{rest.split('"', 1)[0]}|"
    assert "06:M4|" in whitelist, "文档门禁缺 `06:M4` 白名单"
    for description in M4_GATE_CHECKS:
        assert description in text, f"文档门禁没有断言『{description}』"


def test_the_glossary_carries_the_three_m4_terms() -> None:
    """本轮 domain-modeling 的三个新词条在位（挂起驱动 / 录像 / 侧车元数据）。"""
    text = (ROOT / "GLOSSARY.md").read_text(encoding="utf-8")

    for term in ("**挂起驱动**：", "**录像**：", "**侧车元数据**："):
        assert term in text, f"术语表缺 M4 词条『{term}』"


def test_the_engine_tree_is_not_touched_by_the_web_layer() -> None:
    """M4 的第一纪律：接入层住在 `tools/sgs_web/`，**不得** import 引擎私有实现。

    静态判据 = 只允许导入 ``sgs`` 的公开子包入口（``sgs.engine`` / ``sgs.state`` /
    ``sgs.agents`` / ``sgs.data``），不得出现 ``sgs.engine.game`` 这类深层导入
    （那会让接入层与引擎内部结构耦合，并绕过子包的公开面）。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "tools" / "sgs_web").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not (stripped.startswith("from sgs.") or stripped.startswith("import sgs.")):
                continue
            module = stripped.split()[1]
            parts = module.split(".")
            if len(parts) > 2:
                offenders.append(f"{path.name}:{lineno} 导入了 {module}")
    assert not offenders, "接入层出现对引擎的深层导入：" + "；".join(offenders)
