"""前端静态清单（U1-00）：页面与烟测共用 `index.html` 的脚本顺序。

U1-00 把单文件 `tools/sgs_web/static/app.js` 拆成一组按职责命名、按顺序加载的普通脚本。
**唯一清单**是 `index.html` 里的 `<script src>` 顺序：

- 页面按它逐个加载（不引入 npm / 打包 / ES module 加载器）；
- `tests/support/ui_smoke.mjs` 按它逐份加载（同一份清单，脚本边界与浏览器一致）；
- `tests/test_web.sh` 按它取静态资源，并逐份 `node --check`。

本文件钉两件**跨文件契约**：

1. **清单契约**（存在、完整、无 `app.js`、每份都 strict）；
2. **身份配比的显示副本**：`table.js` 的 `IDENTITY_RATIOS` 必须与
   `src/sgs/data/identities.py:IDENTITY_ROSTERS` 一致（前端没有构建步骤，
   显示表只能复制一份；漂移由本测试拦住）。

不钉 DOM 行为——行为归 `tests/support/ui_smoke.mjs`（真脚本 + 真服务端响应）。
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


_RATIO_BLOCK = re.compile(r"const IDENTITY_RATIOS = \{(?P<body>.*?)\n\};", re.S)
_RATIO_LINE = re.compile(
    r"^\s*(?P<count>\d+):\s*\[\s*(?P<lord>\d+),\s*(?P<loyalist>\d+),"
    r"\s*(?P<rebel>\d+),\s*(?P<renegade>\d+)\s*\],\s*$",
    re.M,
)


def test_identity_ratio_display_copy_matches_the_engine_rosters() -> None:
    """`table.js` 的公开配比表是**显示副本**，但不得与引擎真源漂移。

    前端没有构建步骤（U1-00：普通脚本顺序加载），顶栏的公开身份配比只能在 JS 里
    再写一份；这里把那份字面量解析出来，与 `src/sgs/data/identities.py` 的
    :data:`IDENTITY_ROSTERS` 逐人数比对。改配比时两边必须同批改。
    """
    from sgs.data.identities import IDENTITY_ROSTERS, Identity

    text = (STATIC_DIR / "table.js").read_text(encoding="utf-8")
    block = _RATIO_BLOCK.search(text)
    assert block is not None, "table.js 里找不到 `const IDENTITY_RATIOS = { ... };`"
    display = {
        int(match.group("count")): [
            int(match.group("lord")),
            int(match.group("loyalist")),
            int(match.group("rebel")),
            int(match.group("renegade")),
        ]
        for match in _RATIO_LINE.finditer(block.group("body"))
    }
    order = (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE)
    expected = {
        count: [sum(1 for identity in roster if identity is label) for label in order]
        for count, roster in IDENTITY_ROSTERS.items()
    }
    assert display == expected, (
        "table.js:IDENTITY_RATIOS 与 identities.py:IDENTITY_ROSTERS 漂移："
        f"界面 {display} / 引擎 {expected}"
    )


# ---------------------------------------------------------------------------
# U3-01：舞台三组的**设计单位**（spec D3 / D4；票 01 的验收把 CSS 算式钉在这里）
# ---------------------------------------------------------------------------

_CSS_TEXT = (STATIC_DIR / "style.css").read_text(encoding="utf-8")


def _css_rule(selector: str) -> str:
    """取一条 CSS 规则的正文（选择器按字面量匹配；找不到即失败）。"""
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}", _CSS_TEXT)
    assert match is not None, f"style.css 里找不到规则 {selector}"
    return match.group("body")


def test_stage_panel_uses_the_u3_design_units() -> None:
    """舞台 40% / 上限 360 / 中心 64.47% / 两行 253，几何上不压顶座。

    设计舞台 1600×608、环桌圆心 y=370、ry=235、对手卡高 150（`table.js:RING_STAGE`）：
    顶座（90°）下沿 = 370 − 235 + 150/2 = 210。舞台中心 64.47% × 608 = 392，上限 360
    ⇒ 顶边 392 − 180 = 212 > 210（D3）。40% 宽 = 640 设计单位，横跨 480–1120，
    左右座位中心 212 / 1388、卡宽 210，故不压侧座。舞台行限两行卡面（124×2 + 5 = 253），
    超出纵向滚动。
    """
    stage = _css_rule(".stage-center")
    assert re.search(r"width:\s*40%", stage), "舞台宽必须是 40%（640 设计单位）"
    width = re.search(r"max-height:\s*calc\((\d+(?:\.\d+)?) \* var\(--dsu\)\)", stage)
    assert width is not None and float(width.group(1)) == 360.0, (
        f"舞台 max-height 必须是 360 设计单位，实际：{stage}"
    )
    top = re.search(r"top:\s*(\d+(?:\.\d+)?)%", stage)
    assert top is not None and abs(float(top.group(1)) - 64.47) < 1e-6, (
        f"舞台中心必须下移到 64.47%，实际：{stage}"
    )
    assert re.search(r"overflow:\s*hidden", stage), "舞台面板必须裁掉超出的行"

    center_y = float(top.group(1)) / 100 * 608
    top_seat_bottom = 370 - 235 + 150 / 2
    panel_top = center_y - float(width.group(1)) / 2
    assert panel_top > top_seat_bottom, (
        f"舞台顶边 {panel_top} 压住了顶座下沿 {top_seat_bottom}（D3 的几何判据）"
    )
    assert abs(center_y - 392) < 0.5, f"64.47% × 608 应 ≈ 392，实际 {center_y}"

    row = _css_rule(".stage-center .processing")
    assert re.search(r"max-height:\s*calc\(253 \* var\(--dsu\)\)", row), (
        f"舞台行必须限两行卡面（253 = 124×2 + 5），实际：{row}"
    )
    assert re.search(r"overflow-y:\s*auto", row), "超过两行必须纵向滚动，不折叠"
    assert re.search(r"align-content:\s*flex-start", row), "行从顶部排（滚动而不是把行压扁）"

    assert re.search(r"-webkit-line-clamp:\s*2", _css_rule(".stage-center .context")), (
        "底部说明最多 2 行"
    )


def test_stage_lanes_have_the_four_unit_bars_and_discard_is_desaturated() -> None:
    """左侧 4 设计单位色条区分三组、弃牌降饱和；旧的金色描边已删除（D4）。"""
    bar = _css_rule(".stage-card")
    assert re.search(r"border-left:\s*calc\(4 \* var\(--dsu\)\)", bar), (
        f"舞台卡左侧必须是 4 设计单位色条，实际：{bar}"
    )
    lanes = {
        "processing": "#d9b45c",
        "showcase": "#3f8f8a",
        "discard": "#9a9188",
    }
    for lane, color in lanes.items():
        rule = _css_rule(f'.stage-card[data-stage-lane="{lane}"]')
        assert color in rule.lower(), f"{lane} 组色条必须是 {color}，实际：{rule}"
    assert re.search(
        r'\.stage-card\[data-stage-lane="discard"\][^{]*\{[^}]*saturate\(\.55\)',
        _CSS_TEXT,
    ), "弃牌组卡面必须降饱和 saturate(.55)"
    assert ".revealed-card" not in _CSS_TEXT, (
        "旧的亮牌金色描边（.revealed-card）必须删除——色条取代它，不新增角标"
    )
    assert ".fading" in _CSS_TEXT, "淡出必须有一个可断言的类（.stage-card.fading）"


def test_stage_title_is_renamed_from_processing_area() -> None:
    """小标题「处理区」改为「舞台」（D3）；三组同流，故不再只写处理区。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'class="stage-title">\s*舞台\s*<', html), (
        "index.html 的舞台小标题必须改成「舞台」"
    )


# ---------------------------------------------------------------------------
# U3-03：使用牌目标线 / 行动者高亮 / 体力闪（spec D8.1 / D13；票 03 的验收把
# CSS 设计单位与层级钉在这里）
# ---------------------------------------------------------------------------

_EFFECTS_JS = STATIC_DIR / "effects.js"


def test_target_line_layer_is_full_shell_above_seats_below_modals() -> None:
    """目标线层 = 覆盖整个牌桌壳的 SVG：`inset: 0`、无 viewBox、不吃指针、层级正确。

    D8.1 的层级要求「在座位之上、弹窗之下」；自己的武将块住底部面板（z-index 10），
    故线层还要在它之上，否则连到自己的线会被面板背景盖住。端点只在画线时读一次布局
    （JS 注释 / `ui_smoke` 的“窗口尺寸变化不影响已在播的线”），这里只钉静态几何。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<svg\b[^>]*\bid=\"t-target-lines\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少目标线层的 SVG（id=t-target-lines）"
    assert "viewBox" not in tag.group(0), (
        f"目标线层不能有 viewBox（坐标就是壳内 CSS 像素），实际：{tag.group(0)}"
    )
    assert re.search(r'class="target-lines"', tag.group(0)), "目标线层必须带 class=target-lines"
    assert html.index('id="table-shell"') < html.index('id="t-target-lines"'), (
        "目标线层的 SVG 必须住在 .table-shell 里面"
    )

    layer = _css_rule(".target-lines")
    assert re.search(r"position:\s*absolute", layer), f"线层必须绝对定位，实际：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"线层必须铺满壳（inset: 0），实际：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"线层不能吃指针，实际：{layer}"

    def z_index(selector: str) -> int:
        body = _css_rule(selector)
        found = re.search(r"z-index:\s*(\d+)", body)
        assert found is not None, f"{selector} 里找不到 z-index，实际：{body}"
        return int(found.group(1))

    seat_z = z_index(".ring .seat")
    bottom_z = z_index(".table-bottom")
    line_z = z_index(".target-lines")
    modal_z = z_index(".modal")
    assert seat_z < line_z < modal_z, (
        f"线层必须在环上座位（{seat_z}）之上、弹窗（{modal_z}）之下，实际 {line_z}"
    )
    assert bottom_z < line_z, (
        f"线层必须在自己的底部面板（{bottom_z}）之上，否则连到自己的线被面板盖住，实际 {line_z}"
    )


def test_target_line_is_gold_two_and_half_units_with_ten_unit_arrow() -> None:
    """D8.1 的样式：线宽 2.5 设计单位、箭头 10 设计单位、统一金色 `#d9b45c`。"""
    assert _EFFECTS_JS.is_file(), "缺少 effects.js（目标线 / 高亮 / 体力闪的渲染口）"
    line = _css_rule(".target-line line")
    assert re.search(r"stroke:\s*#d9b45c", line), f"目标线必须统一金色，实际：{line}"
    assert re.search(r"stroke-width:\s*calc\(2\.5 \* var\(--dsu\)\)", line), (
        f"线宽必须是 2.5 设计单位（不是 CSS px），实际：{line}"
    )
    arrow = _css_rule(".target-line polygon")
    assert "#d9b45c" in arrow.lower(), f"箭头必须同色，实际：{arrow}"

    js = _EFFECTS_JS.read_text(encoding="utf-8")
    assert re.search(r"const\s+TARGET_ARROW_SIZE\s*=\s*10\b", js), (
        "箭头尺寸常量必须是 10 设计单位"
    )
    assert re.search(r"TARGET_ARROW_SIZE\s*\*\s*designUnit", js), (
        "箭头尺寸必须按设计单位换算（10 × 1 设计单位），不能写死 CSS px"
    )


def test_target_line_lifetime_is_1200_hold_plus_800_fade() -> None:
    """D8.1 / D15：2s = 保持 1.2s + 淡出 0.8s；淡出靠 `.fading` + 0.8s 过渡。"""
    assert _EFFECTS_JS.is_file(), "缺少 effects.js（目标线 / 高亮 / 体力闪的渲染口）"
    js = _EFFECTS_JS.read_text(encoding="utf-8")
    hold = re.search(r"const\s+TARGET_LINE_HOLD_MS\s*=\s*(\d+)\b", js)
    fade = re.search(r"const\s+TARGET_LINE_FADE_MS\s*=\s*(\d+)\b", js)
    assert hold is not None and int(hold.group(1)) == 1200, "线保持必须是 1.2 秒"
    assert fade is not None and int(fade.group(1)) == 800, "线淡出必须是 0.8 秒"
    style = _css_rule(".target-line")
    assert re.search(r"transition:\s*opacity\s*\.8s", style), (
        f"线淡出过渡必须是 0.8s，实际：{style}"
    )
    assert re.search(r"opacity:\s*0", _css_rule(".target-line.fading")), "淡出类必须把线画成透明"


def test_acting_highlight_and_hp_flash_use_the_d13_durations() -> None:
    """D13：行动者描边 0.5s；体力闪 0.4s，掉血红 / 回复绿。"""
    acting = _css_rule(".seat.acting")
    assert re.search(r"\.5s", acting), f"行动者描边必须是 0.5s，实际：{acting}"
    flash = _css_rule(".hero-block.hp-flash")
    assert re.search(r"\.4s", flash), f"体力闪必须是 0.4s，实际：{flash}"
    down = _css_rule(".hero-block.hp-flash-down")
    up = _css_rule(".hero-block.hp-flash-up")
    assert "#b3261e" in down.lower(), f"掉血必须红，实际：{down}"
    assert "#2e7d32" in up.lower(), f"回复必须绿，实际：{up}"

    js = _EFFECTS_JS.read_text(encoding="utf-8")
    for name, value in (("ACTING_HIGHLIGHT_MS", 500), ("HP_FLASH_MS", 400)):
        found = re.search(rf"const\s+{name}\s*=\s*(\d+)\b", js)
        assert found is not None and int(found.group(1)) == value, (
            f"{name} 必须是 {value}ms（CSS 时长与类移除共用一份）"
        )
