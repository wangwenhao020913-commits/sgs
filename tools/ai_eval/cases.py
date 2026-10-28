"""D 类用例读数（票 13；spec §2.2 / §2.8 的 ``cases``）。

用例本体住 ``tests/test_ai_cases.py``（票 14 落地），这里只做**机械读数**：起一个子进程跑
``pytest``，读它写的 JUnit XML，把"过 / 挂 / 仍是已知红（strict xfail）"分开数。

为什么不 import 用例模块：``strict xfail`` 的语义只在 pytest 里有（"已知红"不是异常，
是 xfail 标记）；自己实现会把"先红后绿"的机械性丢掉。子进程还保证用例里的引擎状态不
污染评估进程。

**模块不存在 = 未测**（``measured=False``，``green=None``）——不是"通过"，也不是"挂"。
只有 ``total > 0``、无 failed、无已知红才算 ``green=True``。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path

from .errors import AiEvalRunError

__all__ = ["CASES_PATH", "CasesResult", "run_cases"]

CASES_PATH = Path("tests") / "test_ai_cases.py"
"""D 类用例的唯一落点（spec §2.2：新建文件，不往 4400 行的 test_heuristic_ai.py 里塞）。"""

CASE_RUN_TIMEOUT = 300
"""子进程上限（秒）：一条活锁要么在 pytest 自己的 timeout 里红，要么在这里被点名。"""


@dataclass(frozen=True)
class CasesResult:
    """``cases`` 的三字段 + 两个判 green 用的读数（known_red / measured 不进 JSON）。"""

    total: int = 0
    passed: int = 0
    failed: tuple[str, ...] = ()
    known_red: int = 0
    measured: bool = False

    @property
    def green(self) -> bool | None:
        """D 类用例全绿？未测 ⇒ ``None``；仍有 strict xfail ⇒ ``False``。"""
        if not self.measured:
            return None
        return self.total > 0 and not self.failed and self.known_red == 0

    def to_dict(self) -> dict[str, object]:
        """§2.8 的 ``cases`` 对象（未测时全 0，不写 null——字段必须齐备）。"""
        return {"total": self.total, "passed": self.passed, "failed": list(self.failed)}


def _parse_junit(path: Path) -> CasesResult:
    tree = ElementTree.parse(path)
    passed = 0
    failed: list[str] = []
    known_red = 0
    total = 0
    for case in tree.getroot().iter("testcase"):
        total += 1
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(name)
            continue
        if case.find("skipped") is None:
            passed += 1
        else:
            # strict xfail = "已知红"；其它 skip 也不算绿——测量面的敌人是"看起来通过"。
            known_red += 1
    return CasesResult(
        total=total,
        passed=passed,
        failed=tuple(failed),
        known_red=known_red,
        measured=True,
    )


def _tail(text: str, limit: int = 400) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return "…" + stripped[-limit:]


def run_cases(
    repo_root: Path,
    *,
    python: str | None = None,
    timeout: float = CASE_RUN_TIMEOUT,
) -> CasesResult:
    """跑 ``tests/test_ai_cases.py`` 并读数；模块不存在返回未测结果（不抛错）。"""
    root = Path(repo_root)
    target = root / CASES_PATH
    if not target.exists():
        return CasesResult()
    with tempfile.TemporaryDirectory(prefix="sgs-ai-cases-") as directory:
        report = Path(directory) / "cases.xml"
        try:
            completed = subprocess.run(
                [
                    python or sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--tb=no",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                    f"--junit-xml={report}",
                    str(CASES_PATH),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise AiEvalRunError(f"D 类用例超过 {timeout}s 未结束") from error
        except OSError as error:
            raise AiEvalRunError(f"起不了 pytest 子进程：{error}") from error
        if completed.returncode == 5:  # 一条用例都没收集到
            return CasesResult()
        if completed.returncode not in (0, 1) or not report.exists():
            raise AiEvalRunError(
                "D 类用例跑不起来（pytest 退出码 "
                f"{completed.returncode}）：{_tail(completed.stdout + completed.stderr)}"
            )
        return _parse_junit(report)
