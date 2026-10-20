"""前端静态清单（U1-00）：页面与烟测共用 `index.html` 的脚本顺序。

U1-00 把单文件 `tools/sgs_web/static/app.js` 拆成一组按职责命名、按顺序加载的普通脚本。
**唯一清单**是 `index.html` 里的 `<script src>` 顺序：

- 页面按它逐个加载（不引入 npm / 打包 / ES module 加载器）；
- `tests/support/ui_smoke.mjs` 按它逐份加载（同一份清单，脚本边界与浏览器一致）；
- `tests/test_web.sh` 按它取静态资源，并逐份 `node --check`。

本文件只钉**清单契约**（存在、完整、无 `app.js`、每份都 strict），不钉 DOM 行为——
行为归 `tests/support/ui_smoke.mjs`（真脚本 + 真服务端响应）。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO_ROOT / "tools" / "sgs_web" / "static"
INDEX_HTML = STATIC_DIR / "index.html"

# `"use strict";` 必须出现在文件**第一条语句**的位置：前面只允许空白与注释。
_LEADING_COMMENT = re.compile(r"\A(?:\s|/\*.*?\*/|//[^\n]*\n)*", re.S)


class _ScriptCollector(HTMLParser):
    """按出现顺序收集 `<script src="…">`（行内脚本没有 src，不在此列）。"""

    def __init__(self) -> None:
        super().__init__()
        self.srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        src = dict(attrs).get("src")
        if src:
            self.srcs.append(src)


def _script_srcs() -> list[str]:
    collector = _ScriptCollector()
    collector.feed(INDEX_HTML.read_text(encoding="utf-8"))
    collector.close()
    return collector.srcs


def test_index_html_lists_the_split_scripts() -> None:
    """页面引用的是**多份**同目录普通脚本，而不是单文件 `app.js`。

    顺序由 `ui_smoke.mjs` 按这份清单拼接来真正走一遍；这里只钉清单本身的形状。
    """
    srcs = _script_srcs()
    assert len(srcs) >= 2, f"应拆成多份脚本，实际清单：{srcs}"
    assert "app.js" not in srcs, "拆分后不得再加载 app.js（唯一清单里不应出现旧单文件）"
    assert len(srcs) == len(set(srcs)), f"同一份脚本被重复加载：{srcs}"
    assert srcs[-1] == "main.js", f"启动脚本 main.js 必须最后加载（wire() 在它末尾）：{srcs}"
    for src in srcs:
        assert "/" not in src and "\\" not in src, f"脚本应是与 index.html 同目录的相对名：{src}"
        assert src.endswith(".js"), f"清单里混进了非 JS 资源：{src}"


def test_every_static_script_is_listed_and_present() -> None:
    """静态目录里的每份脚本都在清单里；清单里的每份脚本都真实存在（无孤儿、无缺件）。"""
    listed = set(_script_srcs())
    on_disk = {path.name for path in STATIC_DIR.glob("*.js")}
    assert listed == on_disk, (
        "index.html 的脚本清单与 static/*.js 不一致："
        f"清单缺 {sorted(on_disk - listed)}；文件缺 {sorted(listed - on_disk)}"
    )
    assert not (STATIC_DIR / "app.js").exists(), "旧的单文件 app.js 应已删除，不留转发壳"


def test_every_script_is_a_classic_strict_script() -> None:
    """每份脚本都能单独按普通脚本运行：首条语句是 `"use strict";`，且没有 ESM 语法。"""
    for name in _script_srcs():
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        body = _LEADING_COMMENT.sub("", text, count=1)
        assert body.startswith('"use strict";'), f"{name} 的首条语句必须是 \"use strict\";（保持拆分前的严格模式）"
        assert not re.search(r"^\s*(?:import|export)\b", text, re.M), (
            f"{name} 使用了 ES module 语法；U1-00 只允许按顺序加载的普通脚本"
        )
