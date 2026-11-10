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

import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

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


def test_stage_card_fade_is_a_one_second_keyframe_animation_matching_the_js_constant() -> None:
    """D5 / D15：舞台卡淡出 1s，且必须由 @keyframes 驱动（新节点也能从 1 播到 0）。

    舞台项可能在**首次渲染时就已进入淡出**（回放缓冲 / 帧 pop 与产生它的记录同批）：
    新节点没有"opacity 1 → 0"的两个已生效计算值，`transition` 不会播放，卡面直接跳没。
    `animation` 从关键帧的 `from { opacity: 1 }` 起播，故对新建节点同样成立。

    时长常量在 `session.js:STAGE_FADE_MS`；CSS 与 JS 必须相等（D15）。
    目标线的 0.8s `transition` 是另一处，不动
    （`test_target_line_lifetime_is_1200_hold_plus_800_fade` 钉它）。
    """
    session_js = (STATIC_DIR / "session.js").read_text(encoding="utf-8")
    fade_ms = re.search(r"const\s+STAGE_FADE_MS\s*=\s*(\d+)\b", session_js)
    assert fade_ms is not None, "session.js 里找不到 `const STAGE_FADE_MS = ...;`"

    fade = _css_rule(".stage-card.fading")
    assert "transition" not in fade, (
        f"舞台卡淡出不能用 transition（新节点没有起点），实际：{fade}"
    )
    animation = re.search(r"animation:\s*([A-Za-z0-9_-]+)\s+([\d.]+)s", fade)
    assert animation is not None, f".stage-card.fading 必须用 animation，实际：{fade}"
    name, seconds = animation.group(1), float(animation.group(2))
    assert round(seconds * 1000) == int(fade_ms.group(1)), (
        f"CSS 动画 {seconds}s 必须等于 session.js:STAGE_FADE_MS={fade_ms.group(1)}"
    )

    keyframes = re.search(
        rf"@keyframes\s+{re.escape(name)}\s*\{{(?P<body>[^{{}}]*(?:\{{[^{{}}]*\}}[^{{}}]*)*)\}}",
        _CSS_TEXT,
    )
    assert keyframes is not None, f"找不到 @keyframes {name}"
    body = keyframes.group("body")
    assert re.search(r"(?:from|0%)\s*\{[^}]*opacity:\s*1", body), (
        f"关键帧必须从 opacity 1 开始（新节点才有起点），实际：{body}"
    )
    assert re.search(r"(?:to|100%)\s*\{[^}]*opacity:\s*0", body), (
        f"关键帧必须在 opacity 0 结束，实际：{body}"
    )


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


# ---------------------------------------------------------------------------
# U3-04：技能名飘字 / 白光（spec D9；票 04 的 CSS 设计单位钉在这里）
#
# 目标线复用 U3-03 的线层与 `drawTargetLines`，这里只钉新增的两件演出：
# 飘字（16 设计单位字、2s 上浮 20、同块 2s 内叠加 14）与白光（0.6s、粗 8 / 细 3、
# 间隔 0.12s、左上 → 右下、裁在武将块内）。
# ---------------------------------------------------------------------------

_KEYFRAMES_RE = re.compile(r"@keyframes\s+(?P<name>[\w-]+)\s*\{(?P<body>.*?)\n\}", re.S)


def _keyframes(name: str) -> str:
    """取一段 `@keyframes` 的完整正文（`_css_rule` 的 `[^}]*` 进不了嵌套块）。"""
    for match in _KEYFRAMES_RE.finditer(_CSS_TEXT):
        if match.group("name") == name:
            return match.group("body")
    raise AssertionError(f"style.css 里找不到 @keyframes {name}")


def test_skill_sweep_is_a_thick_and_thin_white_band_clipped_inside_the_hero_block() -> None:
    """D9 的白光：武将块内 `overflow: hidden`、粗 8 / 细 3 设计单位、0.6s、间隔 0.12s。"""
    block = _css_rule(".seat .hero-block")
    assert re.search(r"overflow:\s*hidden", block), (
        f"白光必须裁在武将块内（.seat .hero-block 需要 overflow: hidden）：{block}"
    )

    thick = _css_rule(".hero-block.skill-sweep::before")
    thin = _css_rule(".hero-block.skill-sweep::after")
    assert re.search(r"content:\s*\"\"", thick), f"粗白光是伪元素，必须有 content：{thick}"
    assert re.search(r"position:\s*absolute", thick), f"白光必须绝对定位在武将块内：{thick}"
    assert re.search(r"width:\s*calc\(8 \* var\(--dsu\)\)", thick), (
        f"粗白光必须是 8 设计单位（不是 CSS px），实际：{thick}"
    )
    assert re.search(r"width:\s*calc\(3 \* var\(--dsu\)\)", thin), (
        f"细白光必须是 3 设计单位，实际：{thin}"
    )
    for name, body in (("粗", thick), ("细", thin)):
        assert re.search(r"animation:\s*skill-sweep\s+\S*\.6s\b[^;]*\b1\b", body), (
            f"{name}白光必须是 0.6s、只播一次，实际：{body}"
        )
    assert re.search(r"animation-delay:\s*(?:0?\.12s|120ms)", thin), (
        f"两条白光间隔 0.12s（细带延迟），实际：{thin}"
    )
    assert "#fff" in thick.lower() or "255, 255, 255" in thick, (
        f"白光的底色必须是白色，实际：{thick}"
    )

    frames = _keyframes("skill-sweep")
    lefts = [float(value) for value in re.findall(r"left:\s*(-?\d+(?:\.\d+)?)%", frames)]
    assert len(lefts) >= 2 and lefts[0] < 0 < lefts[-1], (
        f"扫光必须左上 → 右下（left 从负百分比走到正百分比），实际：{frames}"
    )
    assert "rotate(" in frames or "rotate(" in thick, (
        "扫光必须是斜带（左上 → 右下），直上直下不满足 D9"
    )


def test_skill_float_uses_the_d9_design_units_and_stack_offset() -> None:
    """D9 的飘字：16 设计单位金色加粗、2s 上浮 20、同一武将块 2s 内叠加抬高 14。"""
    style = _css_rule(".skill-float")
    assert re.search(r"font-size:\s*calc\(16 \* var\(--dsu\)\)", style), (
        f"技能名字号必须是 16 设计单位，实际：{style}"
    )
    assert re.search(r"font-weight:\s*700", style), f"技能名必须加粗，实际：{style}"
    assert "#d9b45c" in style.lower(), f"技能名必须是金色，实际：{style}"
    assert re.search(r"animation:\s*skill-float-rise\s+2s\b", style), (
        f"技能名必须在 2s 内上浮并淡出，实际：{style}"
    )
    rise = _keyframes("skill-float-rise")
    assert re.search(r"calc\(-20 \* var\(--dsu\)\)", rise), (
        f"上浮必须走 20 设计单位（不是 CSS px），实际：{rise}"
    )
    assert re.search(r"opacity:\s*0", rise), f"上浮结束必须淡出，实际：{rise}"

    js = _EFFECTS_JS.read_text(encoding="utf-8")
    for name, value in (
        ("SKILL_FLOAT_GAP_UNITS", 8),
        ("SKILL_FLOAT_STACK_UNITS", 14),
    ):
        found = re.search(rf"const\s+{name}\s*=\s*(\d+)\b", js)
        assert found is not None and int(found.group(1)) == value, (
            f"{name} 必须是 {value} 设计单位（JS 定位与 CSS 是同一份裁定）"
        )


# ---------------------------------------------------------------------------
# U3-07 实机验收缺陷（CODING_STANDARDS.md §6 的机械那一半）：真 DOM 的
# `element.children` 是 **HTMLCollection**——只有 `length` / `item()` / 索引，
# **没有** `find` / `filter` / `map` / `forEach` / `some` / `every` / `reduce`。
#
# 舞台卡更新曾写成 `(node.children || []).find(...)`：真浏览器里每次更新都抛
# `TypeError`、舞台与表体重绘中断；烟测垫片的 `children` 是数组，故常规门禁没抓到。
# 这里扫产品脚本里的**直接调用点**（注释不算行为；`Array.from(...)` / 展开 / 下标 /
# `.length` 都是允许的写法）。**真实浏览器验收仍是这类缺陷的最终裁判**，本检查只是
# 把"同一个错型再写回来"变成机械红。
# ---------------------------------------------------------------------------

_CHILDREN_ARRAY_METHOD_RE = re.compile(
    r"\.children\s*(?:\|\|\s*\[\s*\]\s*\))?\s*\.\s*"
    r"(?P<method>find|filter|map|forEach|some|every|reduce)\b"
)

# 允许的写法：方法调用的接收者是 `Array.from(...)` 的结果，不是 `children` 本身。
# 只挖这一种精确形状——`Array.from(node.children.find(...))` 这种嵌套违规仍会被抓到。
_ALLOWED_ARRAY_FROM_CHILDREN_RE = re.compile(
    r"Array\.from\(\s*[\w.$]+\.children\s*(?:\|\|\s*\[\s*\]\s*)?\)\s*\.\s*\w+"
)


def _strip_js_comments(source: str) -> str:
    """去掉 `//` 与 `/* … */` 注释，字符串 / 模板串原样保留。

    注释里的旧写法是历史说明，不是调用点；字符串里的 `http://` 也不是注释起点。
    产品脚本里没有含 `//` 或 `/*` 的正则字面量，故扫描器不区分正则字面量。
    注释按原换行数换行，报错行号仍指向原文。
    """
    out: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(source):
        char = source[index]
        if quote is not None:
            out.append(char)
            if char == "\\" and index + 1 < len(source):
                out.append(source[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "\"'`":
            quote = char
            out.append(char)
            index += 1
            continue
        if source.startswith("//", index):
            newline = source.find("\n", index)
            if newline < 0:
                break
            out.append("\n")
            index = newline + 1
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                break
            out.extend("\n" * source.count("\n", index, end + 2))
            index = end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _children_array_method_hits(source: str) -> list[str]:
    """`element.children` 上直接调数组方法的每一处（`行 N: 片段`）。"""
    allowed = _ALLOWED_ARRAY_FROM_CHILDREN_RE.sub("", _strip_js_comments(source))
    return [
        f"行 {allowed.count(chr(10), 0, match.start()) + 1}: {match.group(0).strip()}"
        for match in _CHILDREN_ARRAY_METHOD_RE.finditer(allowed)
    ]


def test_product_scripts_never_call_array_methods_on_children() -> None:
    """产品 `static/*.js` 不得在 `element.children` 上直接调数组方法。

    真 DOM 的 `children` 是 HTMLCollection（活集合），数组方法只存在于真数组上；
    需要遍历 / 查找时先 `Array.from(...)` 或展开。旧写法在真浏览器里抛
    `TypeError: … .find is not a function`，烟测垫片（children 是数组）看不见。
    """
    offenders: list[str] = []
    for path in sorted(STATIC_DIR.glob("*.js")):
        for hit in _children_array_method_hits(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{hit}")
    assert not offenders, (
        "`element.children` 是 HTMLCollection（只有 length / item / 索引），没有数组方法；"
        "请先 `Array.from(...)` / 展开成数组再调用：\n" + "\n".join(offenders)
    )


def test_the_children_guard_catches_the_old_bug_and_allows_array_forms() -> None:
    """守卫自身正反两面：旧写法判红；`Array.from` / 展开 / 下标 / `.length` / 注释放行。"""
    bug = (
        "const line = (node.children || []).find(f);"
        " node.children.filter(g); box.children.map(h);"
    )
    assert len(_children_array_method_hits(bug)) == 3, _children_array_method_hits(bug)

    allowed = (
        "Array.from(node.children || []).find(f);"
        " [...node.children].filter(g);"
        " node.children[0]; box.children.length;"
        " for (const child of (root && root.children) || []) {}"
    )
    assert _children_array_method_hits(allowed) == []

    commented = "// node.children.find(f)\n/* (node.children || []).map(g) */\n"
    assert _children_array_method_hits(commented) == []

    nested = "Array.from(node.children.find(f))"
    assert len(_children_array_method_hits(nested)) == 1, (
        "`Array.from(...)` 里嵌套的直接调用仍是违规，守卫不得整段放行"
    )


def test_ui_bridge_exposes_the_raw_view_and_submit_entries() -> None:
    """烟测桥必须同时挂 `applyViewRaw` / `submitActionRaw`（文件头承诺的两个中间态入口）。

    `applyView` / `submitAction` 默认先把回放冲刷完；要观察"提交之后、整批还没播完"的中间态
    （M6 报障 1 的五谷亮窗、报障 2 的开局批）只能走 raw 入口。`submitActionRaw` 曾只活在
    桥的注释里、实际没挂——红命令第一次跑就崩在 `ui.submitActionRaw is not a function`。
    """
    source = (REPO_ROOT / "tests" / "support" / "ui_bridge.js").read_text(encoding="utf-8")
    assert "out.applyViewRaw = out.applyView;" in source, (
        "ui_bridge.js 要把原始 applyView 挂成 applyViewRaw"
    )
    assert re.search(r"out\.submitActionRaw\s*=\s*rawSubmit;", source), (
        "ui_bridge.js 要把原始 submitAction 挂成 submitActionRaw（与 applyViewRaw 同姿态）"
    )


# ---------------------------------------------------------------------------
# U4-01：飞行层（spec D1 / D2 / D3 / D5 / D6；原型 anim-proto.* 提升为产品文件）
#
# 机械的两半：
# 1. **原型残渣**：`anim.js` / `anim.ini` 是产品文件，不得再带 `?proto=1` 门、demo 循环、
#    `__animProto` 探针、`debug_scale`，也不得再用全局 monkey-patch 接管 `applyEffectDelta` /
#    `stepPlayback` / `commitView`（钩子只有一个显式入口，见 `session.js:stepPlayback`）。
# 2. **钩子次序**：源点在 `applyEffectDelta` 之后、重绘之前量；飞行器在重绘之后同步起飞。
#    次序断言直接比 `stepPlayback` 函数体里的**位置**，不排序、不归一化（CODING_STANDARDS §4）。
# ---------------------------------------------------------------------------

_ANIM_JS = STATIC_DIR / "anim.js"
_SESSION_JS = STATIC_DIR / "session.js"
_ANIM_INI = STATIC_DIR / "anim.ini"

_D8_INI_KEYS = {
    "base_step",
    "flight_use",
    "flight_judge_reveal",
    "flight_draw",
    "flight_gain",
    "flight_discard",
    "flight_place_equip",
    "flight_place_judge",
    "flight_replace_out",
    "flight_stage_out",
    "fan_ms",
    "fan_px",
    "phase_float_ms",
}

_MONKEY_PATCH_RE = re.compile(r"\b(applyEffectDelta|stepPlayback|commitView)\s*=")
_STEP_PLAYBACK_RE = re.compile(r"function stepPlayback\(immediate\) \{(?P<body>.*?)\n\}", re.S)


def test_pindian_popup_is_a_read_only_overlay_driven_by_the_record_stream() -> None:
    """拼点弹窗（huo-taishici-xunyu 票 04）：新脚本进清单、停留 3 秒、只由记录流驱动。

    这条钉的是**接线**（渲染行为与 1v1 / 1vN / 平点三态由 `ui_smoke.mjs` 的票 04 组钉）：
    `pindian.js` 在 `index.html` 清单里、排在 `main.js` 之前；停留常量是 3000；入口只有
    `session.js:stepPlayback` 里的一次 `applyPindianDelta(record)`；它是**只读展示面**，
    不进询问面的种类表（`modal.js:DIALOG_KINDS` 里没有 `pindian`）。
    """
    srcs = _script_srcs()
    assert "pindian.js" in srcs, f"index.html 必须加载拼点弹窗脚本：{srcs}"
    assert srcs.index("pindian.js") < srcs.index("main.js"), "pindian.js 必须排在 main.js 之前"
    # `cardFaceNode` 是它唯一的卡面来源（card-view.js），故必须排在 card-view.js 之后。
    assert srcs.index("card-view.js") < srcs.index("pindian.js"), "pindian.js 要用 cardFaceNode"

    pindian = (STATIC_DIR / "pindian.js").read_text(encoding="utf-8")
    assert "const PINDIAN_HOLD_MS = 3000;" in pindian, "停留时长必须是 3 秒（R7 用户口径）"
    assert 'record.delta_type !== "pindian"' in pindian, "判据只看 delta_type"
    # 生效点数（tao-caocao 票 03）：`ranks` 只读服务端给的数字、不比较、不自己加。
    assert "pindian-point" in pindian, "被技能调过点数时要画出生效点数"
    assert "Number(" not in pindian and "parseInt(" not in pindian, (
        "前端不解析 / 不比较点数：点数原样来自 delta 的 ranks"
    )
    style = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".pindian-point" in style, "生效点数的那一行要有样式"

    session = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    assert body.count("applyPindianDelta(record)") == 1, (
        "拼点弹窗只该由 stepPlayback 的记录流驱动一次："
        f"{body.count('applyPindianDelta(record)')}"
    )

    modal = (STATIC_DIR / "modal.js").read_text(encoding="utf-8")
    start = modal.find("const DIALOG_KINDS = Object.freeze({")
    assert start >= 0, "modal.js 里找不到 DIALOG_KINDS"
    kinds = modal[start : modal.find("});", start)]
    assert "pindian" not in kinds, "拼点弹窗不是询问面：不得进 DIALOG_KINDS"


def test_anim_scripts_are_wired_and_the_prototype_is_gone() -> None:
    """`anim.js` 进清单、排在 `main.js` 之前；原型文件与运行时接管全部移除。"""
    srcs = _script_srcs()
    assert "anim.js" in srcs, f"index.html 必须加载飞行层 anim.js：{srcs}"
    assert srcs.index("anim.js") < srcs.index("main.js"), "anim.js 必须排在 main.js 之前"
    assert "anim-proto.js" not in srcs, f"index.html 不得再加载原型：{srcs}"
    assert not (STATIC_DIR / "anim-proto.js").exists(), "原型 anim-proto.js 应已删除"
    assert not (STATIC_DIR / "anim-proto.ini").exists(), "原型 anim-proto.ini 应已更名"
    assert _ANIM_INI.is_file(), "产品时间表 anim.ini 缺失"

    body = _strip_js_comments(_ANIM_JS.read_text(encoding="utf-8"))
    assert "__animProto" not in body, "原型探针 __animProto 不进产品文件"
    assert "proto=1" not in body, "不得保留 ?proto=1 门（产品文件常态生效）"
    assert "debug_scale" not in body, "debug_scale（调试倍率）必须整体移除"
    assert "eventDelayMs" not in body, (
        "节奏（state.eventDelayMs）住 core.js / main.js / session.js；anim.js 只发布 ini 表，"
        "不碰播放步长（票 02 的裁定，见 U4-02 的静态断言）"
    )
    hits = _MONKEY_PATCH_RE.findall(body)
    assert not hits, f"飞行层不得再 monkey-patch 全局入口（实际：{hits}）"


def test_session_playback_hook_measures_source_before_render_and_launches_after() -> None:
    """唯一的钩子：源点先量、重绘后量落点并同步起飞（D2）。"""
    text = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(text)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    plan = body.find("animPlanFlight(record")
    launch = body.find("animLaunchFlight(")
    assert plan >= 0, "stepPlayback 里没有显式调用 animPlanFlight(record, …)"
    assert launch >= 0, "stepPlayback 里没有显式调用 animLaunchFlight(...)"
    # 锚点只认 `applyEffectDelta(record, state.effectCards` 这一段前缀：入口现在带第 4 个
    # 实参（同一条记录共用一次 `stageNow()`，见 U3-01t 的抖动修复），尾部括号不再是契约。
    delta = body.find("applyEffectDelta(record, state.effectCards")
    assert delta >= 0 and plan > delta, (
        "起点必须在 applyEffectDelta 之后量（此时舞台卡 / 手牌 / 槽位还在原处）"
    )
    assert plan < body.find("applyLiveDelta(record"), "起点必须在重绘（applyLiveDelta / render）之前量"
    assert plan < body.find("renderTable()"), "起点必须在 renderTable 之前量"
    assert launch > body.find("renderTable()"), "落点必须在 renderTable 重绘之后量"
    assert launch > body.find("renderProcessing()"), "落点必须在 renderProcessing 重绘之后量"


# ---------------------------------------------------------------------------
# damage-fx 票 03：剑光演出的两个纯静态脚本进清单
#
# 参考实现（`.scratch/damage-fx/reference/`）搬进 `static/` 时只有两处必须变：
# 1. `"use strict";` 从 IIFE **内部**挪到文件第一条语句（U1-00 的清单契约）；
# 2. 全局名照旧（`window.SgsDamageFX` / `window.SgsSfx`），不改成 ESM。
# 两份都必须是**纯静态**脚本：零依赖、零外部资源、不改 DOM、不发请求——它们是画布 / 声音
# 生成器，接管牌桌是票 05 的事（`effects.js`）。
# ---------------------------------------------------------------------------

_DAMAGE_FX_GLOBALS = {"fx-core.js": "SgsDamageFX", "sfx.js": "SgsSfx"}


def test_damage_fx_scripts_keep_their_global_names_and_stay_static() -> None:
    """两份脚本都在清单里、排在 `main.js` 之前、按普通脚本导出全局名，且不发请求。"""
    srcs = _script_srcs()
    assert srcs[-1] == "main.js", "清单契约的先决条件：main.js 仍是最后一份"
    for name, global_name in _DAMAGE_FX_GLOBALS.items():
        assert name in srcs, f"index.html 必须加载 {name}：{srcs}"
        assert srcs.index(name) < srcs.index("main.js"), f"{name} 必须排在 main.js 之前"
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert re.search(rf"global\.{global_name}\s*=\s*\{{", text), (
            f"{name} 必须把入口挂在 window.{global_name}（参考实现的全局名，不改成 ESM）"
        )
        assert not re.search(r"\b(?:fetch|XMLHttpRequest)\s*\(", text), (
            f"{name} 是纯静态脚本：不发请求（素材库那套壳子不进牌桌）"
        )
        assert not re.search(r"\bdocument\.(?:querySelector|getElementById)\b", text), (
            f"{name} 不接管 DOM（覆盖层与挂载是票 05 / effects.js 的事）"
        )
    fx_core = (STATIC_DIR / "fx-core.js").read_text(encoding="utf-8")
    block = re.search(r"var VARIANTS = \{(?P<body>.*?)\n  \};", fx_core, re.S)
    assert block is not None, "fx-core.js 里找不到 VARIANTS 表"
    keys = {int(match.group(1)) for match in re.finditer(r"^    (\d): \{$", block.group("body"), re.M)}
    assert keys == {1, 2, 3, 4}, f"VARIANTS 必须恰好四档（1~4），实际：{sorted(keys)}"


def _ini_entries() -> dict[str, str]:
    """`anim.ini` 的 `key = value` 全表（剥注释；缺 `=` 即红）。"""
    entries: dict[str, str] = {}
    for raw in _ANIM_INI.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"\s*[#;].*$", "", raw).strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        assert separator, f"anim.ini 行不是 key = value：{raw}"
        entries[key.strip()] = value.strip()
    return entries


def test_anim_ini_lists_the_d8_durations_without_debug_scale() -> None:
    """`anim.ini` = D8 的 13 个键（外加 damage-fx 票 04 的 19 个）；`debug_scale` 不进产品。"""
    text = _ANIM_INI.read_text(encoding="utf-8")
    assert "debug_scale" not in text, "debug_scale 必须从 anim.ini 整体移除（含键与注释）"
    entries = _ini_entries()
    # 同一份 ini 也住着 damage-fx 的 19 个键（票 04：用户改一份文件就能调两套时长），
    # 这里只钉 D8 那一族，19 个键由下面那条用例钉。
    shared = {key for key in entries if not key.startswith("damage_fx_")}
    assert shared == _D8_INI_KEYS, (
        f"anim.ini 的 D8 键必须与 D8 表一致：缺 {sorted(_D8_INI_KEYS - shared)}；"
        f"多 {sorted(shared - _D8_INI_KEYS)}"
    )
    for key, value in entries.items():
        float(value)  # 解析失败即红（页面此时会退回默认）
    assert float(entries["flight_stage_out"]) == 0, (
        "处理区 → 弃牌堆不飞：flight_stage_out 必须是 0"
    )


# ---------------------------------------------------------------------------
# damage-fx 票 04：四档各自的游戏内时长（anim.ini 的 19 个键 + fx-core 的 game 档）
#
# 票面要求：每档的四个时长（total / flight / impact / fade）**必须能各自独立改**，
# 不许用一条公式把四档绑死；单档总时长上限 2.2s。这里钉三件：
# 1. `anim.ini` 的键集合与不变式（`flight ≤ impact ≤ total ≤ 2200`、`impact + fade ≤ total`）；
# 2. 真 `anim.js` 解析器把 19 个键读进 `animConfig`、**不**报"未知键"（否则用户改了没生效）；
# 3. 真 `fx-core.js` 的 `SgsDamageFX.gameTiming` 解算与四条钳制（含缺键 / 非法值回默认）。
# ---------------------------------------------------------------------------

_DAMAGE_FX_VARIANTS = (1, 2, 3, 4)
_DAMAGE_FX_TIMING_FIELDS = ("total", "flight", "impact", "fade")
_DAMAGE_FX_INI_KEYS = {
    f"damage_fx_{variant}_{field}"
    for variant in _DAMAGE_FX_VARIANTS
    for field in _DAMAGE_FX_TIMING_FIELDS
} | {"damage_fx_3_strobe", "damage_fx_4_strobe", "damage_fx_4_split"}
"""票 04 的 19 个键：四档 × 四个时长 + 3/4 点的屏闪 + 4 点的劈屏错位。"""


def test_anim_ini_lists_the_damage_fx_durations_per_variant() -> None:
    """19 个键齐全、可解析，且每档都满足 `flight ≤ impact ≤ total ≤ 2200`、`impact + fade ≤ total`。"""
    entries = _ini_entries()
    damage = {key: value for key, value in entries.items() if key.startswith("damage_fx_")}
    assert set(damage) == _DAMAGE_FX_INI_KEYS, (
        f"damage-fx 的时长键必须与票面 19 个一致：缺 {sorted(_DAMAGE_FX_INI_KEYS - set(damage))}；"
        f"多 {sorted(set(damage) - _DAMAGE_FX_INI_KEYS)}"
    )
    values = {key: float(value) for key, value in damage.items()}
    for variant in _DAMAGE_FX_VARIANTS:
        total = values[f"damage_fx_{variant}_total"]
        flight = values[f"damage_fx_{variant}_flight"]
        impact = values[f"damage_fx_{variant}_impact"]
        fade = values[f"damage_fx_{variant}_fade"]
        assert 0 < impact < total <= 2200, (
            f"{variant} 点档：命中必须落在演出之内、总时长 ≤ 2200（挡住牌桌），"
            f"实际 impact={impact} / total={total}"
        )
        assert 0 < flight < impact, f"{variant} 点档：剑光必须在命中之前飞完"
        assert impact + fade <= total, (
            f"{variant} 点档：刀痕必须在摘层之前散尽（impact + fade ≤ total），"
            f"实际 {impact} + {fade} > {total}"
        )
    # 四档总时长各自独立：一条公式绑死的话四个值会相等（票 04 的核心交付）。
    totals = [values[f"damage_fx_{variant}_total"] for variant in _DAMAGE_FX_VARIANTS]
    assert len(set(totals)) == len(_DAMAGE_FX_VARIANTS), (
        f"四档总时长必须各自独立可改，实际：{totals}"
    )
    assert values["damage_fx_3_strobe"] > 0 and values["damage_fx_4_strobe"] > values["damage_fx_3_strobe"], (
        "屏闪只有 3 / 4 点有，且 4 点更长（spec §演出口径）"
    )
    assert values["damage_fx_4_split"] > 0, "只有 4 点有劈屏错位"


_DAMAGE_FX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ini = readFileSync("tools/sgs_web/static/anim.ini", "utf8");
const noteNode = { textContent: "", hidden: true };
const context = vm.createContext({
  console,
  setTimeout,
  clearTimeout,
  fetch: async () => ({ ok: true, text: async () => ini }),
  document: {
    getElementById: (id) => (id === "t-anim-note" ? noteNode : null),
    createElement: () => ({
      style: {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      appendChild() {},
      setAttribute() {},
    }),
  },
});
context.globalThis = context;
context.window = context;
context.$ = (id) => context.document.getElementById(id);
// 与页面一致的加载方式：每份脚本单独编译，同一个 context 的全局词法作用域跨脚本可见。
for (const name of ["anim.js", "fx-core.js", "effects.js"]) {
  vm.runInContext(readFileSync(`tools/sgs_web/static/${name}`, "utf8"), context, { filename: name });
}
const report = await vm.runInContext(
  `(async () => {
     await animReady;
     const fx = SgsDamageFX;
     const resolved = {};
     for (const variant of [1, 2, 3, 4]) resolved[variant] = fx.gameTiming(variant, {});
     const clamps = {
       total: fx.gameTiming(1, { damage_fx_1_total: 9999 }).ms.total,
       impact: fx.gameTiming(2, { damage_fx_2_total: 400, damage_fx_2_impact: 900 }).ms.impact,
       flight: fx.gameTiming(3, { damage_fx_3_impact: 300, damage_fx_3_flight: 800 }).ms.flight,
       fade: fx.gameTiming(4, { damage_fx_4_total: 1000, damage_fx_4_impact: 800, damage_fx_4_fade: 900 }).ms.fade,
       missing: fx.gameTiming(1, { damage_fx_1_total: 1600 }).ms,
       junk: fx.gameTiming(2, { damage_fx_2_total: "abc", damage_fx_2_flight: -5 }).ms,
       tunedTotal: fx.gameTiming(4, { damage_fx_4_total: 1600 }).timeline.DURATION,
       exportUntouched: fx.TIMELINE.DURATION,
     };
     return JSON.stringify({
       note: $("t-anim-note").textContent,
       animConfigKeys: Object.keys(animConfig),
       defaults: fx.DEFAULT_TIMING,
       maxTotal: fx.MAX_TOTAL_MS,
       resolved,
       clamps,
       effectsTiming: {
         1: damageFxTiming(1),
         4: damageFxTiming(4),
         resolvedFromEffects: fx.gameTiming(4, damageFxTiming(4)).timeline.DURATION,
       },
     });
   })()`,
  context,
  { filename: "damage_fx_probe.js" },
);
process.stdout.write(report);
"""


def _damage_fx_probe() -> dict:
    """跑真 `anim.js` + 真 `fx-core.js`（node vm），拿回两份时间表的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "damage_fx_probe.mjs"
        probe.write_text(_DAMAGE_FX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"damage-fx 探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def damage_fx_probe() -> dict:
    """一次探针跑完，两条用例共用（node 启动约 100ms，不必跑两遍）。"""
    return _damage_fx_probe()


def test_the_damage_fx_durations_reach_anim_config_without_unknown_key_notes(damage_fx_probe: dict) -> None:
    """`anim.js` 的解析器把 19 个键读进 `animConfig`，**不**把它们当未知键删掉 / 报错。

    这是用户改 ini 能不能生效的判据：`anim.js:animLoadConfig` 对不在默认表里的键会
    `delete` + 弹一行"未知键"，`damage_fx_*` 必须走前缀登记那条路。
    """
    probe = damage_fx_probe
    assert "未知键" not in probe["note"], (
        f"damage_fx_* 不得被当成未知键（页面提示：{probe['note']}）"
    )
    missing = _DAMAGE_FX_INI_KEYS - set(probe["animConfigKeys"])
    assert not missing, f"这 19 个键必须进 animConfig，缺：{sorted(missing)}"


def test_effects_hands_the_damage_fx_timing_over_from_the_live_ini_table(damage_fx_probe: dict) -> None:
    """`effects.js:damageFxTiming` 只搬数字：有键的档给全六个量，没键的档不硬造。

    这一层不兜底、不钳制（默认与钳制都在 `fx-core.js`）：缺 `damage_fx_1_strobe` 时返回的
    表里就**没有**这个键，fx-core 退 `VARIANTS[1].strobe = 0`。票 05 的播放器读它。
    """
    probe = damage_fx_probe
    ini = {
        key: float(value)
        for key, value in _ini_entries().items()
        if key.startswith("damage_fx_")
    }
    four = probe["effectsTiming"]["4"]
    assert set(four) == {
        f"damage_fx_4_{field}" for field in _DAMAGE_FX_TIMING_FIELDS
    } | {"damage_fx_4_strobe", "damage_fx_4_split"}, f"4 点档的六个量都要搬过去：{sorted(four)}"
    for key, value in four.items():
        assert value == ini[key], f"{key} 必须原样来自 anim.ini"
    one = probe["effectsTiming"]["1"]
    assert set(one) == {f"damage_fx_1_{field}" for field in _DAMAGE_FX_TIMING_FIELDS}, (
        f"1 点档没有 strobe / split 的键，就不该硬造：{sorted(one)}"
    )
    assert probe["effectsTiming"]["resolvedFromEffects"] == pytest.approx(
        ini["damage_fx_4_total"] / 1000
    ), "把搬过来的表交给 fx-core，解算出同一段 4 点档时长"


def test_the_game_profile_honours_anim_ini_and_clamps_its_numbers(damage_fx_probe: dict) -> None:
    """`SgsDamageFX.gameTiming`：默认值 = `DEFAULT_TIMING` = ini；四条钳制与缺键 / 垃圾值兜底。"""
    probe = damage_fx_probe
    ini = {
        key: float(value)
        for key, value in _ini_entries().items()
        if key.startswith("damage_fx_")
    }
    assert set(probe["defaults"]) == _DAMAGE_FX_INI_KEYS, (
        "fx-core 的内置默认必须恰好覆盖这 19 个键："
        f"缺 {sorted(_DAMAGE_FX_INI_KEYS - set(probe['defaults']))}"
    )
    assert {key: int(value) for key, value in ini.items()} == probe["defaults"], (
        "内置默认必须与磁盘上的 anim.ini 同值（否则删一行 ini 会静默改变四档时长）"
    )
    assert probe["maxTotal"] == 2200

    for variant in _DAMAGE_FX_VARIANTS:
        entry = probe["resolved"][str(variant)]
        total = ini[f"damage_fx_{variant}_total"] / 1000
        flight = ini[f"damage_fx_{variant}_flight"] / 1000
        impact = ini[f"damage_fx_{variant}_impact"] / 1000
        fade = ini[f"damage_fx_{variant}_fade"] / 1000
        timeline = entry["timeline"]
        assert timeline["DURATION"] == pytest.approx(total), "整段寿命 = total（到点摘层）"
        assert timeline["IMPACT"] == pytest.approx(impact)
        assert timeline["LAUNCH"] == pytest.approx(impact - flight), "出鞘 = 命中 - 飞行"
        assert timeline["NUM_LIFE"] == pytest.approx(total - impact), "飘字活到整段结束"
        assert 0 < timeline["CHARGE"] < timeline["LAUNCH"], "剑意凝聚必须在出鞘之前"
        assert entry["variant"]["flight"] == pytest.approx(flight)
        assert entry["variant"]["fadeStart"] == pytest.approx(impact + 0.25 * fade), (
            "淡出起点 = 命中 + fade 的 25%"
        )
        assert entry["variant"]["fadeEnd"] == pytest.approx(impact + fade), (
            "刀痕散尽 = 命中 + fade（不能照搬 export 档的 4.6 → 9.2 秒）"
        )
    assert probe["resolved"]["3"]["variant"]["strobe"] == pytest.approx(0.16)
    assert probe["resolved"]["4"]["variant"]["strobe"] == pytest.approx(0.44)
    assert probe["resolved"]["4"]["variant"]["split"] == 44
    assert probe["resolved"]["1"]["variant"]["strobe"] == 0
    assert probe["resolved"]["2"]["variant"]["split"] == 0

    clamps = probe["clamps"]
    assert clamps["total"] == 2200, "单档总时长上限 2200（超过会挡住牌桌）"
    assert clamps["impact"] == 400, "impact ≤ total"
    assert clamps["flight"] == 300, "flight ≤ impact"
    assert clamps["fade"] == 200, "impact + fade ≤ total（否则层摘了刀痕还没散）"
    assert clamps["missing"] == {
        "total": 1600,
        "flight": 130,
        "impact": 330,
        "fade": 700,
        "strobe": 0,
        "split": 0,
    }, "只改一个键，其余退回内置默认"
    assert clamps["junk"]["total"] == 1650 and clamps["junk"]["flight"] == 125, (
        "非法值（非数字 / 负数）退回内置默认"
    )
    assert clamps["tunedTotal"] == pytest.approx(1.6), "把 4 点档 total 改成 1600 ⇒ 整段 1.6s"
    assert clamps["exportUntouched"] == 12, "export 档的 12 秒链一字不动（截图 / 导片依赖它）"


def test_anim_flight_layer_is_a_full_shell_overlay_above_the_effect_layers() -> None:
    """D1 / D12：覆盖整个牌桌壳、不吃指针、裁在壳内、层级高于目标线与技能飘字；只加运动不加光。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<div\b[^>]*\bid=\"t-anim-flights\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少飞行层 #t-anim-flights"
    assert re.search(r'class="[^"]*anim-layer', tag.group(0)), (
        f"飞行层必须带 class=anim-layer：{tag.group(0)}"
    )
    assert "aria-hidden" in tag.group(0), "飞行层是纯视觉层，必须 aria-hidden"
    assert html.index('id="table-shell"') < html.index('id="t-anim-flights"'), (
        "飞行层必须住在 .table-shell 里"
    )
    note = re.search(r"<[^>]*\bid=\"t-anim-note\"[^>]*>", html)
    assert note is not None, "index.html 里缺少一行提示的 #t-anim-note（D8 的 ini 回退提示）"

    layer = _css_rule(".anim-layer")
    assert re.search(r"position:\s*absolute", layer), f"飞行层必须绝对定位：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"飞行层必须铺满壳（inset: 0）：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"飞行层不能吃指针：{layer}"
    assert re.search(r"overflow:\s*hidden", layer), f"飞行层必须裁在壳内：{layer}"

    def z_index(selector: str) -> int:
        found = re.search(r"z-index:\s*(\d+)", _css_rule(selector))
        assert found is not None, f"{selector} 里找不到 z-index"
        return int(found.group(1))

    assert z_index(".anim-layer") > z_index(".target-lines") > 0
    assert z_index(".anim-layer") > z_index(".skill-floats") > 0
    assert z_index(".anim-layer") < z_index(".modal")

    flight = _css_rule(".anim-flight")
    assert re.search(r"position:\s*absolute", flight), (
        f"飞行器位置写死成 left/top + translate，CSS 必须绝对定位：{flight}"
    )
    assert re.search(r"pointer-events:\s*none", flight), f"飞行器是牌面副本，不能吃指针：{flight}"
    # D12：飞行器只加运动、不加光。卡面自己的 `.card.face` 阴影是同一张牌面的既有样式；
    # 飞行器副本不得再叠 filter / box-shadow / text-shadow / outline（先剥掉 CSS 注释）。
    flight_decl = re.sub(r"/\*.*?\*/", "", flight, flags=re.S)
    assert not re.search(r"\b(?:box-shadow|filter|text-shadow|outline)\b", flight_decl), (
        f"飞行器只加运动、不加光（D12），不得新增阴影 / 滤镜 / 描边：{flight_decl}"
    )
    assert re.search(r"will-change:\s*transform,\s*opacity", flight), (
        f"飞行器只应声明 transform / opacity 的动画：{flight}"
    )


# ---------------------------------------------------------------------------
# U4-02：节奏公式与五档步长（spec D7 / D8）
#
# 机械的三半：
# 1. 档位表 = 30/50/100/200/300，缺省 100（旧的 50–500 作废）；
# 2. `stepPlayback` 的收尾排程读 `delayOf(record)`，不再直接用固定步长；
# 3. 回放页 D15 不接飞行 / 不接按记录时长的节奏公式，仍只用基准步长。
# 行为（纯函数语义 / 按钮循环 / ini 只决定初始档）归 `ui_smoke`。
# ---------------------------------------------------------------------------

_CORE_JS = STATIC_DIR / "core.js"
_REPLAY_JS = STATIC_DIR / "replay.js"


def test_delay_steps_are_the_five_step_table_with_a_100ms_default() -> None:
    core = _strip_js_comments(_CORE_JS.read_text(encoding="utf-8"))
    steps = re.search(r"const DELAY_STEPS_MS = \[([^\]]*)\]", core)
    assert steps is not None, "core.js 里找不到 DELAY_STEPS_MS 档位表"
    values = [int(item.strip()) for item in steps.group(1).split(",") if item.strip()]
    assert values == [30, 50, 100, 200, 300], f"五档步长必须按 30/50/100/200/300 排：{values}"
    default = re.search(r"eventDelayMs:\s*(\d+)", core)
    assert default is not None, "core.js 里找不到 state.eventDelayMs 的缺省值"
    assert int(default.group(1)) == 100, f"事件回放缺省步长必须是 100ms：{default.group(1)}"
    assert "50, 100, 200, 300, 400, 500" not in core, "旧的 50–500 六档口径必须作废"


def test_playback_schedules_the_next_step_from_delay_of_the_record() -> None:
    text = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(text)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    schedule = [
        line.strip() for line in match.group("body").splitlines()
        if "setTimeout(() => stepPlayback(false)" in line
    ]
    assert len(schedule) == 1, f"stepPlayback 收尾应只有一处排程：{schedule}"
    assert "delayOf(record)" in schedule[0], f"排程必须读 delayOf(record)：{schedule[0]}"
    assert "state.eventDelayMs" not in schedule[0], f"排程不得再直接用固定步长：{schedule[0]}"

    body = _strip_js_comments(text)
    assert re.search(r"function delayOf\(record, base = state\.eventDelayMs\)", body), (
        "session.js 必须提供纯函数 delayOf(record, base = state.eventDelayMs)"
    )
    assert re.search(r"Math\.round\(base \+ durationOf\(record\) \* base / 100\)", body), (
        "delayOf 必须按 D7：base + dur × (base / 100)"
    )


def test_replay_page_keeps_using_the_base_step_without_flight_pacing() -> None:
    replay = _strip_js_comments(_REPLAY_JS.read_text(encoding="utf-8"))
    assert "state.eventDelayMs" in replay, "回放页（D15 不接飞行）仍按基准步长排程"
    assert "delayOf(" not in replay, "回放页不接飞行，不得读按记录时长的节奏公式"


def test_delay_button_titles_describe_the_new_five_step_range() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    for button_id in ("btn-delay", "btn-r-delay"):
        tag = re.search(rf'<button\b[^>]*\bid="{button_id}"[^>]*>', html)
        assert tag is not None, f"index.html 里找不到 #{button_id}"
        assert "0.03–0.3" in tag.group(0), (
            f"#{button_id} 的 title 必须写新的 0.03–0.3 秒五档：{tag.group(0)}"
        )


# ---------------------------------------------------------------------------
# U4-04：阶段 / 回合飘字（spec D10）
#
# 机械的三半：
# 1. **层与飘字**：整壳覆盖、不吃指针、裁在壳内；层级夹在技能名飘字（16）与飞行器（18）
#    之间（弹窗 60 仍在最上）。尺寸 / 内边距 / 圆角走设计单位（`--dsu`），不新造光晕（D12）。
# 2. **建法**：层由 `anim.js` 动态建出（不动 `index.html`）；寿命读**同一份** `animConfig`
#    （`phase_float_ms`，内置缺省 1200），启动时只调一次 `animLoadConfig()`。
# 3. **钩子**：`stepPlayback` 在重绘之前调一次 `animPlanPhase(record)`，与
#    `animPlanFlight(record)` 相邻两行（CODING_STANDARDS §4：直接比函数体里的位置）。
# 行为（六个标签 / 锚点 / 夹边 / 不占步长 / 与技能飘字共存）归烟测 `U4-04-*`。
# ---------------------------------------------------------------------------


def test_anim_phase_layer_sits_between_the_skill_floats_and_the_flight_layer() -> None:
    """D10 的层：与飞行层同形的整壳覆盖层，层级 16 与 18 之间。"""
    layer = _css_rule(".anim-phase-layer")
    assert re.search(r"position:\s*absolute", layer), f"阶段飘字层必须绝对定位：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"阶段飘字层必须铺满壳（inset: 0）：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"阶段飘字层不能吃指针：{layer}"
    assert re.search(r"overflow:\s*hidden", layer), f"阶段飘字层必须裁在壳内：{layer}"

    def z_index(selector: str) -> int:
        found = re.search(r"z-index:\s*(\d+)", _css_rule(selector))
        assert found is not None, f"{selector} 里找不到 z-index"
        return int(found.group(1))

    assert z_index(".anim-layer") > z_index(".anim-phase-layer") > z_index(".skill-floats") > 0
    assert z_index(".anim-phase-layer") > z_index(".target-lines") > 0
    assert z_index(".anim-phase-layer") < z_index(".modal")


def test_anim_phase_float_is_a_design_unit_pill_without_a_new_glow() -> None:
    """飘字本身：位置由 JS 写死（left = 中心 / top = 底边），尺寸走 `--dsu`，不加光晕（D12）。

    配色（与技能名飘字同族的金色）是判断题，留给评审 / 票 07 的实机截图；这里只钉
    "有字色 + 有浅底 + 没有 box-shadow 这类新光"。
    """
    style = _css_rule(".anim-phase-float")
    assert re.search(r"position:\s*absolute", style), f"飘字必须绝对定位：{style}"
    assert re.search(r"transform:\s*translate\(-50%,\s*-100%\)", style), (
        f"left 读成水平中心、top 读成底边需要 translate(-50%, -100%)：{style}"
    )
    assert re.search(r"pointer-events:\s*none", style), f"飘字不能吃指针：{style}"
    assert re.search(r"font-size:\s*calc\(15 \* var\(--dsu\)\)", style), (
        f"字号必须走设计单位（15 × --dsu）：{style}"
    )
    assert re.search(r"padding:\s*calc\(2 \* var\(--dsu\)\) calc\(9 \* var\(--dsu\)\)", style), (
        f"内边距必须走设计单位（纵向 2 / 横向 9）：{style}"
    )
    assert re.search(r"border-radius:\s*calc\(\d+ \* var\(--dsu\)\)", style), (
        f"圆角必须走设计单位：{style}"
    )
    assert re.search(r"color:\s*#", style), f"飘字必须有字色：{style}"
    assert not re.search(r"box-shadow", style), f"D12 只加运动不加光：飘字不得加 box-shadow：{style}"


def test_anim_js_builds_the_phase_layer_and_reuses_the_single_ini_load() -> None:
    """层由 `anim.js` 建（不改 index.html）；寿命读同一份 `animConfig`，不第二次载入 ini。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="t-anim-phase"' not in html, "阶段飘字层应由 anim.js 动态建出，不进 index.html"
    anim = _strip_js_comments(_ANIM_JS.read_text(encoding="utf-8"))
    assert 'id = "t-anim-phase"' in anim, "anim.js 必须建出稳定的层 id #t-anim-phase"
    assert 'className = "anim-phase-layer"' in anim, "层必须带 class=anim-phase-layer"
    assert "PHASE_FLOAT_LABELS" in anim, "六个阶段标签应是 anim.js 里的一张表（D10）"
    assert "phase_float_ms" in anim, "寿命必须读 animConfig.phase_float_ms"
    assert anim.count("animLoadConfig();") == 1, (
        "阶段飘字不得第二次载入 ini：复用启动时的那一次 animLoadConfig()"
    )
    assert re.search(r"phase_float_ms:\s*1200", anim), "内置缺省 phase_float_ms 必须是 1200"


def test_session_playback_plans_the_phase_float_next_to_the_flight_hook() -> None:
    """`stepPlayback` 里 `animPlanPhase(record)` 恰好一处，与 `animPlanFlight(record, …)` 相邻。"""
    text = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(text)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    assert body.count("animPlanPhase(") == 1, (
        f"stepPlayback 里的阶段飘字入口应只有一处：{body.count('animPlanPhase(')}"
    )
    lines = [line.strip() for line in body.splitlines()]
    flight_at = [index for index, line in enumerate(lines) if "animPlanFlight(record" in line]
    phase_at = [index for index, line in enumerate(lines) if "animPlanPhase(record)" in line]
    assert len(flight_at) == 1 and len(phase_at) == 1, (
        f"两个动效入口各应有一处：flight={flight_at} phase={phase_at}"
    )
    assert phase_at[0] == flight_at[0] + 1, (
        "阶段飘字必须与飞行层**相邻**规划（同一处、同一时机）："
        f"flight@{flight_at[0]} phase@{phase_at[0]}"
    )
    # 锚点与上一条同源：只认前缀，不认尾部括号（入口现在带第 4 个实参 `now`）。
    delta = body.find("applyEffectDelta(record, state.effectCards")
    phase = body.find("animPlanPhase(record)")
    assert delta >= 0 and phase > delta, "飘字规划必须在 applyEffectDelta 之后（与飞行层同一时机）"
    assert phase < body.find("applyLiveDelta(record"), "飘字必须在重绘之前规划（读旧布局的锚点）"
    assert phase < body.find("renderTable()"), "飘字必须在 renderTable 之前规划"


# ---------------------------------------------------------------------------
# U4-05：弃牌堆面板（spec D11）
#
# 机械的两半：
# 1. **顶栏与挂载**：「弃牌 N」是 button（计数口仍是内层 `#t-discard`，顶栏顺序 /
#    样式不变）；面板有自己的窗口 + 遮罩 + 标题 + 关闭按钮。`#t-modal-backdrop`
#    仍是那个**没有** click 的暗化层（U2 的固定断言），"点背景 = 关闭"只属于这里。
# 2. **读取方**：面板正文从与表体**同一份**视图画（`tableViewForRender()`），挂在
#    `renderTable()` 末尾自动重画；牌面只读（`cardFaceNode(..., { static: true })`），
#    不读来源座位 / 区域。
# 行为（行数 / 倒序 / 空态 / 关闭出口 / live 数据源 / 自动刷新）归 `ui_smoke`。
# ---------------------------------------------------------------------------

_MODAL_JS = STATIC_DIR / "modal.js"
_TABLE_JS = STATIC_DIR / "table.js"


def test_discard_panel_has_its_own_button_backdrop_and_modal_mount() -> None:
    """顶栏按钮 + 独立遮罩 + 独立窗口；旧 `#t-modal-backdrop` 的点击语义不被改写。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    button = re.search(
        r'<button\b[^>]*\bid="btn-discard"[^>]*>(?P<body>.*?)</button>', html, re.S)
    assert button is not None, "index.html 里缺少顶栏按钮 #btn-discard（D11）"
    assert re.search(r'\btype="button"', button.group(0)), (
        f"#btn-discard 必须是 type=\"button\"（不提交表单）：{button.group(0)}"
    )
    assert 'id="t-discard"' in button.group("body"), (
        "计数口必须仍是内层 #t-discard（renderTable 只写这个 span）"
    )

    topbar = html[html.index('id="table-topbar"'):html.index('id="table-stage"')]
    order = ["t-deck", "btn-discard", "t-discard", "btn-settings",
             "nav-lobby", "nav-table", "nav-replay"]
    positions = [topbar.index(f'id="{name}"') for name in order]
    assert positions == sorted(positions), (
        "顶栏右端顺序必须保持 牌堆 / 弃牌 / 设置 / 导航三键："
        f"{dict(zip(order, positions))}"
    )

    backdrop = re.search(r'<div\b[^>]*\bid="t-discard-backdrop"[^>]*>', html)
    assert backdrop is not None, "index.html 里缺少弃牌堆自己的遮罩 #t-discard-backdrop"
    modal = re.search(r'<div\b[^>]*\bid="t-discard-modal"[^>]*>', html)
    assert modal is not None, "index.html 里缺少弃牌堆面板 #t-discard-modal"
    assert re.search(r'class="[^"]*\bmodal\b', modal.group(0)), (
        f"面板必须复用 modal.js 的壳（.modal / .modal-window CSS）：{modal.group(0)}"
    )
    for node_id in ("t-discard-title", "t-discard-close", "t-discard-body"):
        assert re.search(rf'\bid="{node_id}"', html), f"index.html 里缺少 #{node_id}"
    body = re.search(r'<div\b[^>]*\bid="t-discard-body"[^>]*>', html)
    assert body is not None and re.search(r'class="[^"]*\bmodal-body\b', body.group(0)), (
        "面板正文必须复用 .modal-body（可滚正文）"
    )
    assert html.index('id="t-discard-backdrop"') < html.index('id="t-discard-modal"'), (
        "遮罩必须在窗口之前（窗口压在遮罩上）"
    )

    modal_js = _strip_js_comments(_MODAL_JS.read_text(encoding="utf-8"))
    assert '$("btn-discard")' in modal_js and re.search(
        r'addEventListener\("click", openDiscardPanel\)', modal_js), (
        "modal.js 必须把 #btn-discard 接到 openDiscardPanel"
    )
    assert '$("t-discard-backdrop")' in modal_js and re.search(
        r'addEventListener\("click", closeDiscardPanel\)', modal_js), (
        "弃牌堆自己的遮罩必须接 click → closeDiscardPanel"
    )
    assert '$("t-discard-close")' in modal_js and re.search(
        r'addEventListener\("click", closeDiscardPanel\)', modal_js), (
        "关闭按钮必须接 closeDiscardPanel"
    )
    assert not re.search(r'\$\("t-modal-backdrop"\)\.addEventListener\(', modal_js), (
        "#t-modal-backdrop 必须保持没有 click（U2 断言：点背景不是询问弹窗 / 详情的出口）"
    )
    assert "discardPanelOpen" in modal_js, "开合状态必须住 modal.js（U4-05 的裁定）"


def test_discard_panel_reads_the_table_view_and_refreshes_from_render_table() -> None:
    """正文读 `tableViewForRender().snapshot.discard`；`renderTable()` 末尾自动重画。"""
    table = _strip_js_comments(_TABLE_JS.read_text(encoding="utf-8"))
    render_panel = re.search(
        r"function renderDiscardPanel\(\) \{(?P<body>.*?)\n\}", table, re.S)
    assert render_panel is not None, "table.js 里找不到 renderDiscardPanel()"
    body = render_panel.group("body")
    assert "tableViewForRender()" in body, (
        "面板必须与表体读同一份视图（回放期 = live view，其余 = 权威视图）"
    )
    assert re.search(r"snapshot\.discard", body), "面板必须读 snapshot.discard"
    assert re.search(r"cardFaceNode\(card, \{ static: true \}\)", body), (
        "每行必须是只读牌面（cardFaceNode(card, { static: true })），不接点击"
    )
    assert re.search(r"\.reverse\(\)", body), "时间倒序 = 反转 snapshot 的置入序"

    render_table = re.search(r"function renderTable\(\) \{(?P<body>.*?)\n\}", table, re.S)
    assert render_table is not None, "table.js 里找不到 renderTable()"
    table_body = render_table.group("body")
    assert table_body.count("renderDiscardPanel();") == 1, (
        "renderTable 末尾必须恰好调一次 renderDiscardPanel()（关着时 no-op，不轮询）"
    )
    assert table_body.index("renderDiscardPanel();") > table_body.index("renderPromptSkills(view);"), (
        "自动刷新的接缝必须在 renderTable 的末尾"
    )


def test_discard_panel_uses_the_shared_modal_shell_with_its_own_clickable_backdrop() -> None:
    """CSS：面板几何沿用 .modal；自己的遮罩可点（旧遮罩仍不可点），窗口在它之上。"""
    backdrop = _css_rule(".discard-backdrop")
    assert re.search(r"pointer-events:\s*auto", backdrop), (
        f"弃牌堆自己的遮罩必须吃指针（点是关闭出口），实际：{backdrop}"
    )
    assert re.search(r"position:\s*absolute", backdrop), f"遮罩必须绝对定位：{backdrop}"
    legacy = _css_rule(".modal-backdrop")
    assert re.search(r"pointer-events:\s*none", legacy), (
        f"#t-modal-backdrop 必须保持 pointer-events: none（U2 的固定断言）：{legacy}"
    )

    def z_index(selector: str) -> int:
        found = re.search(r"z-index:\s*(\d+)", _css_rule(selector))
        assert found is not None, f"{selector} 里找不到 z-index"
        return int(found.group(1))

    assert z_index(".discard-modal") > z_index(".modal"), (
        "弃牌堆面板是用户主动翻出来的顶层检视窗，必须叠在询问 / 详情窗之上"
    )
    assert z_index(".discard-backdrop") > z_index(".modal"), "遮罩也必须压住询问 / 详情窗"
    assert re.search(r"overflow-y:\s*auto", _css_rule(".modal-body")), (
        "N 多时必须纯滚动：正文沿用 .modal-body 的 overflow-y: auto"
    )
    body = _css_rule(".discard-body")
    assert re.search(r"flex-wrap:\s*wrap", body), f"牌面行必须换行铺（不做分组 / 分页）：{body}"


# ---------------------------------------------------------------------------
# U4-06（spec D13）：判定 / 装备槽放得下四字牌名；用户 2026-11-07 起装备槽有牌时不写槽名
# ---------------------------------------------------------------------------
# 1258px 视口（--dsu = 0.786）下：对手装备槽内容宽 ≈ 36 设计单位（侧栏 100 的两列），
# 四字牌名 @10 = 40 ⇒ 必须允许折行（两行 = 22 设计单位，42 高的槽放得下）；判定槽内容宽
# ≈ 21（侧栏三等分），四字牌名折两行 @9 + 8 的「判定」标记 = ~34 < 36（判定行从 33 放宽到
# 36，自己的 48 → 52：D13 明确允许「放宽槽」）。槽名与牌名都在 DOM 里
# （`tests/support/ui_smoke.mjs` 的 U4-06e），这里钉不靠 tooltip 的机械条件。


def test_slot_labels_leave_room_for_four_character_card_names() -> None:
    slot = _css_rule(".mini-slot")
    assert re.search(
        r"padding:\s*calc\(1 \* var\(--dsu\)\) calc\(1 \* var\(--dsu\)\)", slot
    ), f"槽内边距必须走设计单位（列宽预算是按设计单位算的）：{slot}"

    value = _css_rule(".mini-slot .slot-v")
    assert re.search(r"white-space:\s*normal", value), (
        f"牌名必须允许折行（四字 / 五字全名不能只截断）：{value}"
    )
    assert re.search(r"overflow-wrap:\s*anywhere", value), (
        f"中文没有空格，窄槽要能逐字折行：{value}"
    )
    assert re.search(r"text-wrap:\s*balance", value), (
        f"两行要均分（3+1 的「方天画/戟」不算可读）：{value}"
    )
    assert not re.search(r"font-size:", value), (
        f"装备槽牌名沿用槽字号（10 / 13），靠折行而不是缩字号：{value}"
    )

    assert re.search(r"calc\(36 \* var\(--dsu\)\)", _css_rule(".seat .judge-row")), (
        "判定行放宽到 36：折两行 + 槽名 + 内边距要真的放得下"
    )
    assert re.search(r"height:\s*calc\(36 \* var\(--dsu\)\)", _css_rule(".mini-slot.judge-slot")), (
        "判定槽本身也要同高，否则行高与槽高对不上"
    )
    assert re.search(r"calc\(52 \* var\(--dsu\)\)", _css_rule(".self .judge-row")), (
        "自己判定行 52（原 48）：字 13 的两行 + 12 的槽名才放得下"
    )
    assert re.search(
        r"height:\s*calc\(52 \* var\(--dsu\)\)", _css_rule(".self .mini-slot.judge-slot")
    ), "自己判定槽同 52"
    judge_label = _css_rule(".mini-slot.judge-slot.filled .slot-k")
    assert re.search(r"font-size:\s*calc\(8 \* var\(--dsu\)\)", judge_label), (
        f"判定槽的「判定」标记要收一档高度（折两行后仍放得下）：{judge_label}"
    )
    assert re.search(r"line-height:\s*1\.1\b", judge_label), judge_label
    judge_value = _css_rule(".mini-slot.judge-slot.filled .slot-v")
    assert re.search(r"font-size:\s*calc\(9 \* var\(--dsu\)\)", judge_value), judge_value
    assert re.search(r"line-height:\s*1\.15", judge_value), (
        f"折行的两行要有行距（line-height 1 会叠字）：{judge_value}"
    )

    self_label = _css_rule(".self .mini-slot.judge-slot.filled .slot-k")
    assert re.search(r"font-size:\s*calc\(12 \* var\(--dsu\)\)", self_label), self_label
    assert re.search(r"line-height:\s*1\.1\b", self_label), self_label
    self_value = _css_rule(".self .mini-slot.judge-slot.filled .slot-v")
    assert re.search(r"font-size:\s*calc\(13 \* var\(--dsu\)\)", self_value), self_value
    assert re.search(r"line-height:\s*1\.15", self_value), self_value


def test_mini_slots_show_the_equip_label_only_when_empty() -> None:
    """用户 2026-11-07：装备槽**有牌时不写**武器 / 防具 / 攻马 / 防马，空槽才写；判定槽常写。

    行为断言在 `tests/support/ui_smoke.mjs` 的 U1-02h2 / U4-06e；这里钉源码里的分支形状，
    防止"顺手把 label 又挂回满槽"。
    """
    seat_view = _strip_js_comments((STATIC_DIR / "seat-view.js").read_text(encoding="utf-8"))
    node = re.search(r"function miniSlotNode\(options\) \{(?P<body>.*?)\n\}", seat_view, re.S)
    assert node is not None, "seat-view.js 里找不到 miniSlotNode()"
    body = node.group("body")
    assert "if (judge || !card) node.appendChild(label);" in body, (
        "槽名的挂载必须按「判定槽常显 / 装备槽只在空槽」的条件来"
    )
    assert body.find("node.appendChild(label)") < body.find("if (!card) return node;"), (
        "空槽仍要画出槽名（判定 / 武器 / 防具 / 攻马 / 防马）"
    )
    assert 'label.textContent = judge ? "判定" : EQUIP_SLOT_LABELS[slot] || "空"' in body, (
        "槽名的文字必须来自「判定」/ 装备槽标签（不新建一份映射）"
    )


def test_the_card_face_name_fits_four_characters_without_an_ellipsis() -> None:
    """D13：手牌 / 舞台 / 处理区共用 88 设计单位宽的卡面，四字牌名（乐不思蜀等）放得下。

    可读内宽不是 88：卡面 `border: 1px`（`box-sizing: border-box`）与 `.cname` 的
    left/right 各吃一份。故内宽 = 88 − 左右边框 − 左右内缩，四字 + 四份字距必须 ≤ 它。
    U4-06b 的 2px 内缩 + .02em 只算出 81.6 ≤ 84，却漏掉 1px 边框，实机 1258 壳宽仍出
    省略号；U4-07 实机验收把内缩收到 1px、字距收到 .01em，并把边框计入本判据。
    """
    rule = _css_rule(".card.face .cname")
    face_rule = _css_rule(".card.face")
    size = re.search(r"font-size:\s*calc\(([\d.]+) \* var\(--dsu\)\)", rule)
    spacing = re.search(r"letter-spacing:\s*([\d.]+)em", rule)
    assert size is not None, f"`.cname` 的字号必须走设计单位：{rule}"
    assert spacing is not None, f"`.cname` 的字距必须是 em 值：{rule}"
    inset = re.findall(r"left:\s*([\d.]+)px;\s*right:\s*([\d.]+)px", rule)
    border = re.search(r"border:\s*([\d.]+)px", face_rule)
    assert inset, f"`.cname` 的 left/right 必须是 px 值（内宽判据只认这一种形态）：{rule}"
    assert border is not None, f"`.card.face` 的边框必须写具体 px（内宽要减掉它）：{face_rule}"
    font = float(size.group(1))
    em = float(spacing.group(1))
    inner = 88 - 2 * float(border.group(1)) - float(inset[0][0]) - float(inset[0][1])
    four_characters = 4 * font + 4 * em * font
    assert font <= 20, f"字号必须 ≤ 20 设计单位，实际 {font}"
    assert em <= 0.02, f"字距必须 ≤ .02em，实际 {em}"
    assert four_characters <= inner, (
        f"四字牌名 {four_characters:.1f} 设计单位放不进卡面可读内宽 {inner:.0f}"
        f"（{font} 号 / {em}em / 内缩 {inset[0][0]}px / 边框 {border.group(1)}px）"
    )


def test_skill_bar_is_a_three_by_six_grid_of_equal_cells_with_name_only() -> None:
    """报障（2026-11-01）：技能按钮区放不下，技能一多就"显示不全"。

    旧形态是 `display:flex; flex-wrap:wrap`：按钮按名字宽度自己撑、逐行换行，面板只有
    10.5% 宽 ⇒ 技能多起来后面的被挤出可视区。现在钉死成**固定 3 列 × 6 行**的等大网格
    （用户口径：这一块要显示 3×6）：
      * 列宽 `repeat(3, minmax(0, 1fr))`、行高 `repeat(6, minmax(0, 1fr))` ⇒ 每格等宽等高；
      * `min-width: 0` 是省略号的前提（网格项默认 `min-width:auto`，长内容会顶宽列）；
      * 格子里**只放技能名**（前 3 个字，超出省略）：`.kind` 副行在这里隐藏，
        `renderSkills` 用 `shortSkillName` 截断名字（完整名字走 `aria-label` / `title`）。
    """
    bar = _css_rule("#t-skills")
    assert re.search(r"display:\s*grid", bar), f"技能区必须是网格：{bar}"
    assert re.search(r"grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\)", bar), (
        f"列数钉死 3 列、等宽：{bar}"
    )
    assert re.search(r"grid-template-rows:\s*repeat\(6, minmax\(0, 1fr\)\)", bar), (
        f"行数钉死 6 行、等高（3×6 = 18 格）：{bar}"
    )
    assert re.search(r"overflow-y:\s*auto", bar), "超过 18 条要能滚动，而不是被裁掉"
    assert not re.search(r"flex-wrap:\s*wrap", bar), (
        f"网格路线不再用 flex 换行（换行会按名字宽度撑格子）：{bar}"
    )

    cell = _css_rule("#t-skills .skill")
    assert re.search(r"min-width:\s*0", cell), f"省略号的前提被拿掉了：{cell}"
    assert re.search(r"width:\s*100%", cell) and re.search(r"height:\s*100%", cell), (
        f"每格必须填满自己的网格单元（等大）：{cell}"
    )
    assert re.search(r"overflow:\s*hidden", cell), cell
    assert re.search(r"font-size:\s*calc\(\d+ \* var\(--dsu\)\)", cell), (
        f"字号走设计单位（随壳宽缩放）：{cell}"
    )
    kind_line = _css_rule("#t-skills .skill .kind")
    assert re.search(r"display:\s*none", kind_line), (
        f"类别 / 徽标副行在按钮格子里隐藏（只显示技能名）：{kind_line}"
    )

    # 截断住 `renderSkills`：名字走 `shortSkillName`，副行仍在节点里（读屏 / 悬停不丢信息）。
    table_js = _strip_js_comments((STATIC_DIR / "table.js").read_text(encoding="utf-8"))
    render = re.search(r"function renderSkills\(view\) \{(?P<body>.*?)\n\}", table_js, re.S)
    assert render is not None, "table.js 里找不到 renderSkills()"
    body = render.group("body")
    assert "shortSkillName(skill.name)" in body, (
        "按钮上的名字必须过 shortSkillName（前 3 个字 + 省略号）"
    )
    assert '<span class="kind">' in body, (
        "`.kind` 副行仍要下发（CSS 隐藏它，而不是 JS 删掉它）"
    )
    assert 'button.setAttribute("aria-label"' in body, (
        "截断之后完整名字 / 类别 / 徽标必须进 aria-label（读屏与悬停读得到）"
    )

    core_js = _strip_js_comments((STATIC_DIR / "core.js").read_text(encoding="utf-8"))
    helper = re.search(r"function shortSkillName\(name\) \{(?P<body>.*?)\n\}", core_js, re.S)
    assert helper is not None, "core.js 里找不到 shortSkillName()"
    assert "SKILL_NAME_MAX_CHARS = 3" in core_js, "截断长度必须是 3 个字（用户口径）"
    assert "Array.from" in helper.group("body"), (
        "按**字**切（Array.from），不按 UTF-16 码元——代理对不会被切一半"
    )


# ---------------------------------------------------------------------------
# 票 08（B-ui / 票 02 §4.2 B7–B8）：展示武将牌的选项弹窗 = 选择型外壳的**新正文段**
# ---------------------------------------------------------------------------


def _js_function(source: str, name: str) -> str:
    """取一个顶层函数的正文（产品脚本的函数都以行首 `}` 收尾）。"""
    match = re.search(rf"function {name}\((?P<args>[^)]*)\) \{{(?P<body>.*?)\n\}}", source, re.S)
    assert match is not None, f"找不到函数 {name}()"
    return match.group("body")


def test_the_hero_card_dialog_is_a_new_body_section_of_the_choice_shell() -> None:
    """新 DOM 形状的静态部分（行为归 `ui_smoke.mjs`，真浏览器归票面「复现配方」）。

    票 08 的硬约束是**只加正文段**（后续票各加自己的种类名）：

    - 本票不新增外壳：展示武将牌这一步判成 `choice`（第七条 / 第八条由后续票加）；
    - 判据只看载荷形状：`isShowHeroCardStep` 读 `pending.hero_card`（不认技能 id）；
      `hero_id=null` 的"这一次没有武将牌"也有这个键 ⇒ 仍进弹窗，只是不画武将块；
    - 新正文段的节点 / 类名：`.hero-card`（段）→ `.hero-card-sub`（第 k/m 张）+
      `.hero-card-block`（势力徽记 / `.hero-name` / 体力点数条）+ 逐行
      `.hero-card-row`（`.hero-card-desc` 原文、置灰 `.dis`）。形状参照
      `seat-view.js:heroBlockNode`，点数条因此**复用** `hpBarNode`；
    - 标题读既有技能名（`context.name` 优先、`build_skills` 的同一 `skill_id` 兜底），
      不新增标题键；正文里没有「放弃」（这一步没有 Pass）。
    """
    modal = _strip_js_comments(_MODAL_JS.read_text(encoding="utf-8"))

    kinds = re.search(r"const DIALOG_KINDS = Object\.freeze\(\{(?P<body>.*?)\}\)", modal, re.S)
    assert kinds is not None, "modal.js 里找不到 DIALOG_KINDS"
    keys = re.findall(r"^\s*(\w+):\s*\"", kinds.group("body"), re.M)
    assert keys == [
        "arrange", "choice", "unknown", "reveal", "suit", "distribution",
        # jie-feng-batch2 票 01a：多选勾选面（界夏侯渊【神速】「至多三项」）——第七条。
        "multiChoice",
        # huo-shenzhugeliang 票 02：武将牌上的牌点选（星牌弹窗）——第八条。
        "heroPick",
        # lei-shenganning 票 93：两行观看手牌（上行目标 / 下行自己，四色各一）——第九条。
        "watchTwoRows",
    ], (
        "票 08 只加正文段（展示武将牌复用 choice）；第七条由 jie-feng-batch2 票 01a 加"
        "（多选勾选面）；第八条由 huo-shenzhugeliang 票 02 加（武将牌上的牌点选）；"
        f"第九条由 lei-shenganning 票 93 加（两行观看手牌）：{keys}"
    )

    predicate = _js_function(modal, "isShowHeroCardStep")
    assert 'pending.kind !== "SELECT_OPTION"' in predicate, (
        f"武将牌正文段只属于选项询问：{predicate}"
    )
    assert "pending.hero_card" in predicate, (
        f"判据必须读载荷的 hero_card（不认技能 id）：{predicate}"
    )
    assert re.search(r"isShowHeroCardStep\(pending\)\) return DIALOG_KINDS\.choice", modal), (
        "stepDialogKind 必须把展示武将牌这一步判成既有 choice 外壳"
    )

    title = _js_function(modal, "heroCardTitle")
    assert "pending.context" in title and "context.name" in title, (
        f"标题优先读 query_context 的技能名：{title}"
    )
    assert "skill_id === skillId" in title or "item.skill_id === skillId" in title, (
        f"没有 context（409 裸 Query / 合成载荷）时退回 build_skills 的同一 skill_id：{title}"
    )
    assert '"选择"' in title, "两个来源都没有时退回中性文案，不留空标题"
    assert re.search(r"isShowHeroCardStep\(pending\)\) return heroCardTitle\(pending\)", modal), (
        "选择型的标题分支必须接上 heroCardTitle（不新增标题键）"
    )

    content_mode = _js_function(modal, "applyDialogContentMode")
    assert re.search(
        r"kind === DIALOG_KINDS\.choice && isShowHeroCardStep\(pending\)", content_mode
    ), f"applyDialogContentMode 必须只为展示武将牌加新正文段：{content_mode}"
    assert "renderHeroCardBody(pending)" in content_mode, content_mode
    assert re.search(r"body\.appendChild\(options\)", content_mode), (
        "新正文段画进共享的 #t-options 后仍整体搬进 #t-dialog-body（不改正文归属）"
    )

    render = _js_function(modal, "renderHeroCardBody")
    assert '"hero-card"' in render, f"新正文段的根节点类名 .hero-card：{render}"
    assert '"hero-card-sub"' in render, f"副标题「第 k/m 张」：{render}"
    assert "heroCardBlockNode(card)" in render, "有武将牌时画武将块"
    assert "heroCardRowNode(pending, row)" in render, "逐行画选项（顺序 = option_details 顺序）"
    assert "pending.option_details" in render, (
        f"选项行读服务端派生的 option_details（不自己拼技能名 / 描述）：{render}"
    )
    assert "放弃" not in render and "Pass" not in render, (
        "正文段不画「放弃」：这一步没有 Pass（右端的收起由既有 choice 判据负责）"
    )

    block = _js_function(modal, "heroCardBlockNode")
    assert '"hero-card-block"' in block and '"hero-card-head"' in block, block
    assert "kingdom" in block and "kingdom_label" in block, (
        f"势力徽记 = .kingdom + 服务端 kingdom_label：{block}"
    )
    assert '"hero-name"' in block and "card.name" in block, (
        f"名称读服务端下发的 name：{block}"
    )
    assert "hpBarNode({ hp: hpMax, hp_max: hpMax })" in block, (
        "体力点数条复用 seat-view.js:heroBlockNode 的同一形状（hp_max/hp_max）："
        f"{block}"
    )
    assert "hero-card-desc" not in block, "武将块**不含**技能清单（技能在下面的选项行里）"

    row = _js_function(modal, "heroCardRowNode")
    assert '"hero-card-row' in row, row
    assert '"hero-card-desc"' in row and "row.description" in row, (
        f"技能行要画描述正文（服务端原文、不截断）：{row}"
    )
    assert '"hero-card-row dis"' in row, f"置灰行画 .dis：{row}"
    assert "node.disabled = disabled" in row, f"置灰行同时 disabled：{row}"
    assert re.search(
        r"if \(disabled\) \{.*?return node;\s*\}\s*node\.addEventListener\(\"click\"",
        row,
        re.S,
    ), f"置灰行**不接** click（可点行才绑处理器）：{row}"
    assert "submitDialogChoice(pending, index)" in row, (
        f"可点行 = 选中 legal_actions 里那条候选（一击提交，不手拼 Action）：{row}"
    )
    assert "heroCardChoiceIndex(pending, choice)" in row, (
        "候选下标从 legal_actions 里按 choice 找（置灰项不在候选里）"
    )

    # CSS：新正文段的类名都要有自己的规则；描述不截断（没有 line-clamp / ellipsis）。
    section = _css_rule(".hero-card")
    assert re.search(r"display:\s*flex", section), f"正文段竖向铺：{section}"
    sub = _css_rule(".hero-card-sub")
    assert re.search(r"font-size:", sub) and re.search(r"color:\s*var\(--dim\)", sub), (
        f"副标题是弱化的一行：{sub}"
    )
    block_rule = _css_rule(".hero-card-block")
    assert re.search(r"border", block_rule), f"武将块要有自己的框（与座位块同形）：{block_rule}"
    name_rule = _css_rule(".hero-card .hero-name")
    assert re.search(r"font-size:", name_rule), (
        f"`.seat .hero-name` 的样式不覆盖弹窗，名称要自己的字号：{name_rule}"
    )
    row_rule = _css_rule(".hero-card-row")
    assert re.search(r"text-align:\s*left", row_rule), f"选项行左对齐：{row_rule}"
    assert re.search(r"cursor:\s*pointer", row_rule), f"可点行显示可点：{row_rule}"
    dis_rule = _css_rule(".hero-card-row.dis")
    assert re.search(r"cursor:\s*not-allowed", dis_rule), f"置灰行不可点：{dis_rule}"
    desc_rule = _css_rule(".hero-card-desc")
    assert re.search(r"white-space:\s*pre-wrap", desc_rule), (
        f"描述按原文换行铺（不截断）：{desc_rule}"
    )
    assert "line-clamp" not in desc_rule and "text-overflow" not in desc_rule, (
        f"描述不许省略 / 截断：{desc_rule}"
    )


# ---------------------------------------------------------------------------
# 票 93（lei-shenganning）：两行观看手牌的静态判据
# ---------------------------------------------------------------------------


def test_the_two_row_watch_face_reads_the_shape_and_max_per_suit_from_the_payload() -> None:
    """行为归 `ui_smoke.mjs`（票 93-W1..W14 / B1 / B2），这里只钉静态判据：

    - `DIALOG_KINDS` 的第八种 `watchTwoRows`，`stepDialogKind` 只加一条分支；
    - 判据读载荷形状：`SELECT_CARDS` + `spec.max_per_suit` + 恰好两个座位（含自己）；
      正常面的观看授予证据 = `revealed` 非空 **或** 服务端显式标记 `dialog`（非应答者的
      `revealed` 已被抹空，§3 B4）；空槽位只认显式标记（凑不出四色的空选择面，fail-closed）；
    - 置暗 / 上限**复用**涉猎的 `suitSlotState`，它读 `spec.max_per_suit`——前端不写死
      "同花色一张"（票面 §2.2 / DoD）；提交下限读 `spec.total`；
    - 空面 / 重复槽位的两句提示**只写一份**（`WATCH_EMPTY_MESSAGE` /
      `WATCH_DUPLICATE_MESSAGE`，`pending.js:refreshSubmit` 与步骤提示共用）；
    - 正文 = 两行（`.watch-row`，行序目标在上、自己在下）+ `cardFaceNode`（自带 `tags`）；
      空选择面画 `revealed` 的整副手牌；行内横向滚动（`flex-wrap: nowrap`）。
    """
    modal = _strip_js_comments(_MODAL_JS.read_text(encoding="utf-8"))
    reveal = _strip_js_comments((STATIC_DIR / "reveal.js").read_text(encoding="utf-8"))
    pending = _strip_js_comments((STATIC_DIR / "pending.js").read_text(encoding="utf-8"))

    predicate = _js_function(modal, "isWatchTwoRowsStep")
    assert 'pending.kind !== "SELECT_CARDS"' in predicate, predicate
    assert "spec.max_per_suit" in predicate, f"判据读面自己的花色上限：{predicate}"
    assert "seats.size !== 2" in predicate, f"两行 = 恰好两个座位：{predicate}"
    assert "seats.has(Number(pending.actor))" in predicate, (
        f"两行里必须有一行是自己的手牌：{predicate}"
    )
    assert "pending.dialog === DIALOG_KINDS.watchTwoRows" in predicate, (
        f"空选择面只认服务端显式标记（fail-closed）：{predicate}"
    )
    assert "(pending.revealed || []).length" in predicate, (
        f"正常面仍要观看授予的证据（revealed 非空或显式标记）：{predicate}"
    )
    assert re.search(
        r"isWatchTwoRowsStep\(pending\)\) return DIALOG_KINDS\.watchTwoRows", modal
    ), "stepDialogKind 必须在涉猎 / 亮牌之前加这一条分支"

    suit_state = _js_function(reveal, "suitSlotState")
    assert "max_per_suit" in suit_state, (
        f"置暗 / 上限读服务端 spec.max_per_suit，不写死：{suit_state}"
    )
    toggle = _js_function(reveal, "toggleWatchPick")
    assert "suitSlotState(pending, index)" in toggle, (
        f"点选 / 置灰复用涉猎那一份（不另写一套）：{toggle}"
    )

    required = _js_function(reveal, "watchRequiredCount")
    assert "spec" in required and "total" in required, (
        f"提交下限读 spec.total，不写死：{required}"
    )
    assert "WATCH_DEFAULT_TOTAL" in required, (
        f"缺省兜底也要是一个具名常量（当前面的 4 张），不在函数体里裸写：{required}"
    )

    render = _js_function(reveal, "renderWatchRows")
    assert '"watch-row"' in render, f"两行各自一个 .watch-row 段：{render}"
    assert '"data-watch-row"' in render, "行角色写进 data-watch-row（测垫片按它找行）"
    assert "cardFaceNode(" in render, "牌面走 cardFaceNode（tags 由它照画）"
    assert "unknownSlotNode()" in render, "非应答者的槽位画「?」、不接 click"
    assert "suitSlotState(pending, index)" in render, render
    assert "toggleWatchPick(pending, index)" in render, "点牌 = 切换选中（不是一击提交）"
    assert "row.entries" in render and "row.cards" in render, (
        "正常面读槽位、空选择面读 revealed 的整副手牌"
    )

    rows = _js_function(reveal, "watchRows")
    assert "revealed" in rows, f"空选择面的整副手牌来自 revealed：{rows}"
    assert '"target"' in rows and '"self"' in rows, "行角色 = 目标 / 自己"
    assert "Number(pending.actor)" in rows, "自己那一行按询问者认"
    assert "return [{" in rows, (
        "空选择面仍要返回上行的行数据（非应答者牌面被抹空时只留行标签，不能整个不画）："
        f"{rows}"
    )

    # 文案只住一处：两句提示都是常量，`pending.js:refreshSubmit` 与步骤提示都读它们。
    assert 'const WATCH_EMPTY_MESSAGE = "凑不出四种花色' in reveal, reveal
    assert 'const WATCH_DUPLICATE_MESSAGE = "选择面里出现了重复的牌' in reveal, reveal
    assert "WATCH_EMPTY_MESSAGE" in pending and "WATCH_DUPLICATE_MESSAGE" in pending, (
        "页脚 t-choice 与步骤提示必须读同一组常量（不各写一份文案）"
    )
    assert reveal.count("凑不出四种花色") == 1, (
        "空面文案在产品脚本里只允许出现在常量定义那一处"
    )

    hint = _js_function(reveal, "watchRowsHint")
    assert "WATCH_EMPTY_MESSAGE" in hint, f"空选择面必须明说只能放弃：{hint}"
    assert "已选" in hint, f"提示条给已选计数：{hint}"

    rule = _css_rule(".watch-row .cards")
    assert re.search(r"flex-wrap:\s*nowrap", rule), f"行内不换行：{rule}"
    assert re.search(r"overflow-x:\s*auto", rule), f"超过一屏横向滚动：{rule}"


def test_the_self_panel_ships_the_flipped_layer_node() -> None:
    """lin-batch2 票 04（用户 2026-11-08 签）：自己的武将块带与环上座位同一套翻面蒙灰层。

    环上的层由 `seat-view.js:flippedLayerNode` 在 `heroBlockNode` 里**动态**建出；自己的
    底部面板是**常驻静态节点**（与 `#t-self-chain` / `#t-self-dead` 同姿态），故节点必须在
    `index.html` 里、且默认 `hidden` —— 渲染时只切 `hidden`（`table.js:renderSelfSeat`）。
    三态（横置虚线 / 翻面蒙灰 / 阵亡牌）按 z-index 分层，互不遮挡。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = html[html.index('id="t-self-hero"'):html.index('id="t-self-zones"')]
    tag = re.search(r'<div\b[^>]*\bid="t-self-flipped"[^>]*>', block)
    assert tag is not None, "自己的武将块里缺少翻面蒙灰层 #t-self-flipped"
    assert re.search(r'class="flipped-layer"', tag.group(0)), tag.group(0)
    assert re.search(r"\bhidden\b", tag.group(0)), tag.group(0)
    assert re.search(r'title="[^"]*翻面', tag.group(0)), tag.group(0)
    assert (
        html.index('id="t-self-chain"')
        < html.index('id="t-self-flipped"')
        < html.index('id="t-self-dead"')
    ), "翻面层应排在横置虚线之后、阵亡牌之前（三态 CSS 分层互不遮挡）"


# ---------------------------------------------------------------------------
# damage-fx 票 05：前端消费 —— 覆盖层、队列 / 排播、座位矩形映射
#
# 机械的四半：
# 1. **覆盖层**：`index.html` 的 `#t-damage-fx` 铺满牌桌壳、不吃指针、层级夹在飞行层
#    （18）与弹窗（60）之间；层里那张 canvas 由 `effects.js` 建 / 摘，CSS 只负责铺满。
# 2. **触发点**：只有 `seq` 游标——`stepPlayback` 里紧挨 `fireSkillEventsThrough(record.seq)`
#    的下一行；`applyLiveDelta` / `commitView` 里**没有**（硬约束：不许在回放派生里触发）。
# 3. **排播口径**：相邻错开 120ms、同时在播上限 3 条、只挂 `NORMAL`（spec §演出口径）。
# 4. **两处 fx-core 缺口**（票 04 收口时登记的）：飘字用服务端 `amount` 而不是档位；
#    座位矩形换算成舞台坐标（`u` / 航线 / 两张牌的落点），且**缺省舞台逐字节不变**。
#
# 行为（层寿命 / 错开 / 上限 / 版式作废）归烟测 `U-DMG-01..06`。
# ---------------------------------------------------------------------------

_DAMAGE_FX_LAYER_Z = 19


def _layer_z_index(selector: str) -> int:
    found = re.search(r"z-index:\s*(\d+)", _css_rule(selector))
    assert found is not None, f"{selector} 里找不到 z-index：{_css_rule(selector)}"
    return int(found.group(1))


def test_the_damage_fx_layer_is_a_full_shell_overlay_above_the_flight_layer() -> None:
    """票面 §2：整壳覆盖、`pointer-events:none`、层级在飞行层之上、弹窗之下。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<div\b[^>]*\bid=\"t-damage-fx\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少伤害演出层 #t-damage-fx"
    assert re.search(r'class="[^"]*damage-fx-layer', tag.group(0)), (
        f"层必须带 class=damage-fx-layer：{tag.group(0)}"
    )
    assert "aria-hidden" in tag.group(0), "伤害演出是纯视觉层，必须 aria-hidden"
    assert html.index('id="table-shell"') < html.index('id="t-damage-fx"'), (
        "伤害演出层必须住在 .table-shell 里（座位矩形按壳内坐标量）"
    )

    layer = _css_rule(".damage-fx-layer")
    assert re.search(r"position:\s*absolute", layer), f"层必须绝对定位：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"层必须铺满壳（inset: 0）：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"层不能吃指针：{layer}"
    assert re.search(r"overflow:\s*hidden", layer), f"层必须裁在壳内：{layer}"
    assert _layer_z_index(".damage-fx-layer") == _DAMAGE_FX_LAYER_Z, (
        "层级取 19：飞行层（18）之上、弹窗（60）之下"
    )
    assert _layer_z_index(".anim-layer") < _layer_z_index(".damage-fx-layer"), (
        "剑光要压在飞行层之上"
    )
    assert _layer_z_index(".damage-fx-layer") < _layer_z_index(".modal"), (
        "永远让位给询问弹窗"
    )

    canvas = _css_rule(".damage-fx-layer canvas")
    assert re.search(r"width:\s*100%", canvas) and re.search(r"height:\s*100%", canvas), (
        f"层里的 canvas 必须铺满整块壳（内部缩放由 JS 换算）：{canvas}"
    )


def test_damage_fx_playback_is_scheduled_from_the_seq_cursor_with_the_spec_numbers() -> None:
    """票面 §1 / §4：队列进 `applyView`、触发点紧挨技能事件、口径 120ms / 3 条 / 只挂 NORMAL。"""
    effects = _EFFECTS_JS.read_text(encoding="utf-8")
    session = _SESSION_JS.read_text(encoding="utf-8")

    assert re.search(r"^const DAMAGE_FX_STAGGER_MS = 120;$", effects, re.M), (
        "相邻开演错开 120ms（spec §演出口径）"
    )
    assert re.search(r"^const DAMAGE_FX_CONCURRENCY = 3;$", effects, re.M), (
        "同时在播上限 3 条（超出丢队列尾）"
    )
    assert re.search(r'^const DAMAGE_FX_NATURE = "NORMAL";$', effects, re.M), (
        "只挂无属性伤害；火焰 / 雷电显式跳过"
    )

    apply_view = _js_function(session, "applyView")
    assert "body.damage_events" in apply_view, "applyView 必须把载荷收进队列"
    assert "state.damageEvents" in apply_view, "队列住 state.damageEvents"

    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    skill = body.find("fireSkillEventsThrough(record.seq)")
    damage = body.find("fireDamageEventsThrough(record.seq)")
    assert skill >= 0 and damage > skill, (
        "伤害演出必须挂在**同一条 seq 游标**上，且紧挨技能事件的下一行（票面 §1）"
    )
    assert ";" not in body[skill + len("fireSkillEventsThrough(record.seq)") : damage].replace(
        "fireSkillEventsThrough(record.seq);", ""
    ).replace(";", ""), "两行之间不留别的语句（只有注释）"
    for name in ("applyLiveDelta", "commitView"):
        assert "fireDamageEventsThrough" not in _js_function(session, name), (
            f"{name} 里不许触发伤害演出（触发点只能是 seq 游标）"
        )

    clear = _js_function(effects, "clearTransientEffects")
    assert "clearDamageFx()" in clear, "换局 / 换会话必须连同覆盖层一起摘掉"
    assert "state.damageEvents = []" in clear, "换局清空队列（照 state.skillEvents 的姿态）"

    sync = _js_function(effects, "syncTransientEffects")
    assert "damageDropStalePlays()" in sync, (
        "renderTable 重建座位后要按矩形判一次：版式变了就摘层（别演在半空）"
    )
    window_actions = _js_function(effects, "startDamagePlay")
    assert "damageFxTiming(plan.variant)" in window_actions, (
        "时长从 anim.ini 那一份搬过来（票 04 的接线口），不在 JS 里硬编码"
    )
    assert "setTimeout(() => finishDamagePlay(play), play.duration)" in window_actions, (
        "摘层用 create() 返回的 duration 定时，不靠 rAF 自然停（票 04 的交接）"
    )
    assert "fx.draw(" not in _js_function(effects, "finishDamagePlay"), (
        "摘层不靠再画一帧"
    )


_DAMAGE_FX_STAGE_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const calls = [];
/* 画字的**实际位置**：`drawNumber` 是 translate 到字心再 fillText(0,0)，故要跟一下原点
 * （`save` / `restore` 成栈；`scale` / `rotate` 不改原点）。R3 的验收要靠它把
 * "分析出来的包围盒"与"真的画到哪"对上。 */
let origin = { x: 0, y: 0 };
const originStack = [];
const gradient = { addColorStop() {} };
/* 属性要**真的**记下来：`effects.js` 的牌桌档靠"有没有全屏不透明填充"判，
 * 而那是 `fillStyle` / `globalCompositeOperation` 决定的（方法调用看不出来）。 */
const props = { globalCompositeOperation: "source-over", globalAlpha: 1, fillStyle: "" };
const ctx = new Proxy({}, {
  get(_target, prop) {
    if (prop in props) return props[prop];
    if (prop === "measureText") return () => ({ width: 10 });
    if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
    if (prop === "save") return () => { originStack.push({ ...origin }); };
    if (prop === "restore") return () => { origin = originStack.pop() || { x: 0, y: 0 }; };
    if (prop === "setTransform") return () => { origin = { x: 0, y: 0 }; };
    if (prop === "translate") {
      return (x, y) => { origin.x += Number(x) || 0; origin.y += Number(y) || 0; };
    }
    if (prop === "fillText" || prop === "strokeText") {
      return (text) => calls.push({ op: prop, text, font: props.font, x: origin.x, y: origin.y });
    }
    if (prop === "drawImage") {
      return (image, ...args) => calls.push({ op: "drawImage", w: image && image.width, args });
    }
    if (prop === "fillRect") {
      return (_x, _y, w, h) => calls.push({
        op: "fillRect", w, h, fill: props.fillStyle,
        composite: props.globalCompositeOperation, alpha: props.globalAlpha,
      });
    }
    return () => {};
  },
  set(_target, prop, value) { props[prop] = value; return true; },
});
const makeCanvas = () => ({ width: 0, height: 0, getContext: () => ctx });
const context = vm.createContext({
  __calls: calls,
  console, Date, JSON, Object, Array, Number, String, Math, isFinite, parseInt,
  document: { createElement: () => makeCanvas() },
  Path2D: class { moveTo() {} lineTo() {} closePath() {} },
});
context.globalThis = context;
context.window = context;
vm.runInContext(readFileSync("tools/sgs_web/static/fx-core.js", "utf8"), context,
                { filename: "fx-core.js" });

const report = vm.runInContext(`(() => {
  const stage = {
    src: { cx: 620, cy: 360, w: 230, h: 320 },
    dst: { cx: 1080, cy: 360, w: 230, h: 320 },
  };
  const timing1 = {
    damage_fx_1_total: 1500, damage_fx_1_flight: 130,
    damage_fx_1_impact: 330, damage_fx_1_fade: 700,
  };
  const timing4 = {
    damage_fx_4_total: 2100, damage_fx_4_flight: 110,
    damage_fx_4_impact: 340, damage_fx_4_fade: 950,
  };
  const draw = (opts, at) => {
    __calls.length = 0;
    const fx = SgsDamageFX.create(document.createElement("canvas"), opts);
    fx.draw(at);
    return { fx, calls: __calls.slice() };
  };
  const cards = (calls) => calls.filter((call) => call.op === "drawImage" && call.w !== 1920);
  /** 全屏填充（铺满 1920×1080 的那一张）：牌桌档一次都不许有"不透明"的。 */
  const fullScreen = (calls) => calls.filter(
    (call) => call.op === "fillRect" && call.w >= 1920 && call.h >= 1080);

  // 参考舞台（不传 stage / amount / bare）：素材库那条链的回归钉。
  const plain = draw({ variant: 1, dpr: 1 }, 0.70);
  // 牌桌档：只出特效（不画纸底 / 样例武将牌），飘字仍是服务端真实值。
  const bare1 = draw({
    variant: 1, profile: "game", timing: timing1, amount: 3, dpr: 1, stage, bare: true,
  }, 0.80);
  // 实机验收 R2 的**真机读数**：武将块约 250×240 CSS px、覆盖层约 660×371 CSS px、dpr 1.5。
  // 牌桌档的坐标就是覆盖层的 CSS px，故几何直接按这两块算（不再有 1920→目标 的缩小）。
  const screenStage = {
    src: { cx: 180, cy: 190, w: 250, h: 240 },
    dst: { cx: 480, cy: 190, w: 250, h: 240 },
  };
  // 派发者真机读数：体力条（.hp-bar）在牌高的 34.7%~42.3% 之间 ⇒ 下沿 101.5px。
  const hpBar = { top: 0.347 * 240, bottom: 0.423 * 240 };
  const screen = draw({
    variant: 1, profile: "game", timing: timing1, amount: 1, dpr: 1.5,
    stage: screenStage, bare: true, view: { width: 660, height: 371 },
    numberKeepOut: hpBar.bottom,
  }, 0.80);
  // 大牌桌（1920 宽、武将块 630×600）：构图比例应跟着牌走，不受落屏下限牵动。
  const big = draw({
    variant: 1, profile: "game", timing: timing1, amount: 1, dpr: 1,
    stage: { src: { cx: 400, cy: 500, w: 630, h: 600 }, dst: { cx: 1400, cy: 500, w: 630, h: 600 } },
    bare: true, view: { width: 1920, height: 1080 },
  }, 0.80);
  // 牌高的扫掠（R3 的两条不变式必须在任何牌高下都成立，不只是真机那一档）：
  const sweep = [];
  for (const h of [60, 87, 105, 150, 240, 400, 644]) {
    const w = Math.round(h * 0.75);
    const base = {
      variant: 1, profile: "game", timing: timing1, amount: 1, dpr: 1, bare: true,
      view: { width: 800, height: 700 },
      stage: { src: { cx: 200, cy: 350, w, h }, dst: { cx: 600, cy: 350, w, h } },
    };
    // 一次不带硬线（走兜底比例），一次带 DOM 硬线（体力条下沿 = 42.3% 牌高）
    for (const [kind, keepOut] of [["fallback", null], ["hpBar", 0.423 * h]]) {
      const opts = { ...base };
      if (keepOut !== null) opts.numberKeepOut = keepOut;
      sweep.push({ h, kind, hpBarBottom: keepOut, metrics: SgsDamageFX.create(
        document.createElement("canvas"), opts).metrics });
    }
  }
  // 牌桌：座位矩形 + 服务端的真实伤害值（7 点仍按 4 档演）
  const tuned = draw({
    variant: 4, profile: "game", timing: timing4, amount: 7, dpr: 1, stage,
  }, 0.50);
  // 4 点档的屏闪 + 劈屏窗口：素材库档会铺纸底 / 黑缝 / 整屏去色，牌桌档一样都不许有
  const plain4 = draw({ variant: 4, profile: "game", timing: timing4, amount: 3, dpr: 1 }, 0.39);
  const bare4 = draw({
    variant: 4, profile: "game", timing: timing4, amount: 3, dpr: 1, stage, bare: true,
  }, 0.39);

  const texts = (calls) => calls.filter((call) => call.op === "fillText").map((call) => call.text);
  /** 飘字那一笔的**实际字号**（font = '700 <N>px …'），用来看落屏是否 1:1。 */
  const numberFont = (calls, text) => {
    const hit = calls.filter(
      (call) => call.op === "fillText" && call.text === text && /\\d+px/.test(String(call.font)));
    if (!hit.length) return null;
    return Number(/(\\d+(?:\\.\\d+)?)px/.exec(String(hit[hit.length - 1].font))[1]);
  };
  /** 画到画布上的最后一笔某个文案的位置（字心）。 */
  const drawnAt = (calls, text) => {
    const hit = calls.filter((call) => call.op === "fillText" && call.text === text);
    return hit.length ? { x: hit[hit.length - 1].x, y: hit[hit.length - 1].y } : null;
  };
  const fallback = SgsDamageFX.create(document.createElement("canvas"), { variant: 2, dpr: 1 });
  const degenerate = SgsDamageFX.create(document.createElement("canvas"), {
    variant: 1, dpr: 1, stage: { src: { w: 0, h: 0 } },
  });

  return JSON.stringify({
    plain: {
      stage: plain.fx.stage, number: plain.fx.number, duration: plain.fx.duration,
      bare: plain.fx.bare, cards: cards(plain.calls).map((call) => call.args),
      width: plain.fx.width, height: plain.fx.height,
      numberOffset: plain.fx.numberOffset, metrics: plain.fx.metrics,
    },
    bare: {
      stage: bare1.fx.stage, number: bare1.fx.number, duration: bare1.fx.duration,
      bare: bare1.fx.bare, draws: bare1.calls.filter((call) => call.op === "drawImage").length,
      cards: cards(bare1.calls).length, texts: texts(bare1.calls),
      fullScreen: fullScreen(bare1.calls),
    },
    bare4: { fullScreen: fullScreen(bare4.calls), cards: cards(bare4.calls).length },
    plain4: { fullScreen: fullScreen(plain4.calls) },
    tuned: {
      stage: tuned.fx.stage, number: tuned.fx.number, duration: tuned.fx.duration,
      timeline: tuned.fx.timeline, cards: cards(tuned.calls).map((call) => call.args),
      texts: texts(tuned.calls),
    },
    screen: {
      width: screen.fx.width, height: screen.fx.height,
      stage: screen.fx.stage, metrics: screen.fx.metrics, bare: screen.fx.bare,
      numberOffset: screen.fx.numberOffset, duration: screen.fx.duration,
      numberFont: numberFont(screen.calls, "-1"),
      numberDraw: drawnAt(screen.calls, "-1"),
      hpBar: hpBar,
      cardTop: screen.fx.stage.dst.cy - screen.fx.stage.dst.h / 2,
      floor: SgsDamageFX.COMPOSITION_FLOOR, screenMin: SgsDamageFX.SCREEN_MIN,
    },
    sweep: sweep,
    big: { stage: big.fx.stage, metrics: big.fx.metrics, floor: SgsDamageFX.COMPOSITION_FLOOR },
    fallback: { number: fallback.number, bare: fallback.bare, numberOffset: fallback.numberOffset },
    degenerate: degenerate.stage,
  });
})()`, context);
process.stdout.write(report);
"""


@pytest.fixture(scope="module")
def damage_fx_stage_probe() -> dict:
    """真 `fx-core.js`（node vm）跑一遍：座位矩形映射 + `amount` 飘字 + 参考舞台不变。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "damage_fx_stage_probe.mjs"
        probe.write_text(_DAMAGE_FX_STAGE_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"damage-fx 座位探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def test_the_damage_fx_number_comes_from_the_server_amount(damage_fx_stage_probe: dict) -> None:
    """票面 §6.1：档位只决定强度，飘字画**服务端**的 `amount`（`amount ≥ 5` 仍按 4 档演）。"""
    probe = damage_fx_stage_probe
    assert probe["tuned"]["number"] == "-7", (
        f"7 点伤害要画 -7（档位封顶在 4 档，数字不封顶）：{probe['tuned']['number']}"
    )
    assert "-7" in probe["tuned"]["texts"], (
        f"真的画到了画布上：{sorted(set(probe['tuned']['texts']))[:8]}"
    )
    assert probe["tuned"]["duration"] == pytest.approx(2.1), "4 点档的时长仍来自 anim.ini"
    assert probe["tuned"]["timeline"]["DURATION"] == pytest.approx(2.1)
    assert probe["fallback"]["number"] == "-2", (
        "不传 amount ⇒ 与参考实现一致（该档的 dmg）"
    )
    assert probe["plain"]["number"] == "-1"
    assert probe["plain"]["bare"] is False, "缺省不是牌桌档：素材库 / 截图仍是完整舞台"
    assert probe["bare"]["number"] == "-3", "牌桌档（bare）的飘字同样用服务端 amount"


def test_the_damage_fx_stage_maps_the_two_seat_rects_without_touching_the_export_chain(
    damage_fx_stage_probe: dict,
) -> None:
    """票面 §6.2：两张牌的矩形换成量出来的座位矩形，画布铺满、内部缩放；缺省舞台不变。"""
    probe = damage_fx_stage_probe
    plain = probe["plain"]
    assert plain["duration"] == 12, "export 档的 12 秒链一字不动"
    assert plain["stage"]["src"] == {"cx": 520, "cy": 545, "w": 460, "h": 644}
    assert plain["stage"]["dst"] == {"cx": 1400, "cy": 545, "w": 460, "h": 644}
    assert plain["stage"]["u"] == 1
    assert plain["stage"]["a"] == {"x": 752, "y": 582}
    assert plain["stage"]["b"] == {"x": 1332, "y": 516}
    assert plain["cards"] == [
        [216, 149, 608, 792],
        [1096, 149, 608, 792],
    ], f"参考舞台的贴图位置 / 尺寸不许漂：{plain['cards']}"

    tuned = probe["tuned"]["stage"]
    stage = {"src": {"cx": 620, "cy": 360, "w": 230, "h": 320},
             "dst": {"cx": 1080, "cy": 360, "w": 230, "h": 320}}
    assert tuned["src"] == stage["src"] and tuned["dst"] == stage["dst"], (
        "两张牌的矩形原样就是覆盖层量出来的那两块"
    )
    # 构图比例 = 按参考牌 460×644 contain 的最小比例（320 / 644）
    assert tuned["u"] == pytest.approx(320 / 644), f"构图比例按参考牌等比缩：{tuned['u']}"
    # 剑光航线跟着两张牌走（参考舞台的比例：A 在出剑牌右缘外、B 在中剑牌内偏左上）
    assert tuned["a"]["x"] == pytest.approx(620 + 0.5043 * 230, abs=0.02)
    assert tuned["a"]["y"] == pytest.approx(360 + 0.05745 * 320, abs=0.02)
    assert tuned["b"]["x"] == pytest.approx(1080 - 0.14783 * 230, abs=0.02)
    assert tuned["b"]["y"] == pytest.approx(360 - 0.04503 * 320, abs=0.02)

    pad_x = 74 * 230 / 460
    pad_y = 74 * 320 / 644
    expected = [
        [620 - 115 - pad_x, 360 - 160 - pad_y, 230 + pad_x * 2, 320 + pad_y * 2],
        [1080 - 115 - pad_x, 360 - 160 - pad_y, 230 + pad_x * 2, 320 + pad_y * 2],
    ]
    cards = probe["tuned"]["cards"]
    assert len(cards) == 2, f"两张武将牌各贴一次：{cards}"
    for drawn, want in zip(cards, expected):
        assert drawn == pytest.approx(want, abs=0.02), (
            f"两张牌要贴进量出来的矩形（阴影按同一比例外扩）：{cards}"
        )

    assert probe["degenerate"]["src"] == {"cx": 520, "cy": 545, "w": 460, "h": 644}, (
        "量不到布局（0 宽 / 缺字段）⇒ 退回参考舞台，不让页面报错"
    )


def test_the_table_profile_draws_the_effects_only_without_covering_the_table(
    damage_fx_stage_probe: dict,
) -> None:
    """票 05 返工（实机验收）：牌桌档 `bare` 只出特效——牌桌全程透得出来。

    实机（Edge 156 + bsk 0.4.2）上被打回的是**不透明覆盖**：宣纸底铺满整个牌桌壳、
    真牌上叠了两张参考实现的样例武将牌（关羽 / 吕布）。故 `bare: true` 时：

    - 一次 `drawImage` 都不做（纸底与两张样例牌都不画）；
    - 4 点档的"缝隙黑底"与"整屏去色"这两张不透明度 1 的全屏填充也一并免掉
      （`saturation` 合成喂给透明层只会得到一张不透明的灰，等于盖住牌桌）；
    - 剑光 / 刀痕 / 墨点 / 飘字照旧，座位矩形与飘字数值不受影响。

    `?still` / `?export` 那条素材库链**逐字节不变**（缺省不 bare，见上面两条用例）。
    """
    probe = damage_fx_stage_probe
    bare = probe["bare"]
    assert bare["bare"] is True
    assert bare["draws"] == 0, f"牌桌档一次贴图都不该做（纸底 / 样例武将牌）：{bare['draws']}"
    assert bare["cards"] == 0, "参考实现的样例武将牌不许画到真实武将块上"
    assert bare["texts"] and "-3" in bare["texts"], "飘字照旧画（数值来自服务端 amount）"
    assert bare["stage"]["src"] == {"cx": 620, "cy": 360, "w": 230, "h": 320}
    assert bare["stage"]["dst"] == {"cx": 1080, "cy": 360, "w": 230, "h": 320}
    assert bare["duration"] == pytest.approx(1.5), "摘层时长仍来自 anim.ini 的 total"

    def opaque_full_screen(fills: list[dict]) -> list[dict]:
        """判定"盖住牌桌"的那两张：缝隙黑底（`#0d0c0b`）与整屏去色（`saturation`）。"""
        return [
            fill for fill in fills
            if fill["fill"] == "#0d0c0b" or fill["composite"] == "saturation"
        ]

    assert opaque_full_screen(bare["fullScreen"]) == [], (
        f"牌桌档不许有全屏不透明填充：{bare['fullScreen']}"
    )
    assert opaque_full_screen(probe["bare4"]["fullScreen"]) == [] and probe["bare4"]["cards"] == 0, (
        "4 点档（屏闪 + 劈屏窗口）同样不许有全屏填充、不许贴样例牌"
    )
    # 反过来钉住素材库档：同一时刻它**应该**有那两张全屏填充（否则就是两档串了）。
    kinds = {
        "split" if fill["fill"] == "#0d0c0b" else "strobe"
        for fill in opaque_full_screen(probe["plain4"]["fullScreen"])
    }
    assert kinds == {"split", "strobe"}, (
        f"?still / ?export 那条链必须保持原样（缝隙黑底 + 整屏去色）：{kinds}"
    )


def test_the_table_profile_renders_at_target_size_with_legible_sizes(
    damage_fx_stage_probe: dict,
) -> None:
    """票 05 R2（实机返工）：渲染尺度按**目标设备尺寸**，且落屏尺寸有下限。

    真机读数：武将块约 250×240 CSS px、覆盖层约 660×371 CSS px、dpr 1.5。旧实现把
    1920×1080 舞台整块缩到 660 宽（≈ 3 倍降采样）再叠上构图比例 0.37，1 点档的刀痕
    落屏只剩 ~2 设备 px，被抹成一条灰铅笔线。现在：

    - 画布直接按覆盖层的 CSS 尺寸建（内部 1:1 合成，没有整块降采样）；
    - 座位矩形就是覆盖层坐标，不再做 `1920 → 目标` 的换算；
    - 构图比例 `u` 有**落屏下限**（`COMPOSITION_FLOOR`），保证刀痕墨身 ≥ 4 CSS px、
      飘字字高 ≥ 40 CSS px、剑光主体一眼认得出；
    - 飘字从牌心略下起飘，不压真实武将块顶部的体力条；素材库那份偏移一个数都不动。
    """
    probe = damage_fx_stage_probe
    screen = probe["screen"]

    # 1) 画布 = 覆盖层的 CSS 尺寸（内部 1:1，无整块缩放）
    assert (screen["width"], screen["height"]) == (660, 371), (
        f"牌桌档按目标尺寸建画布：{screen['width']}×{screen['height']}"
    )
    assert screen["bare"] is True, "牌桌档必须只出特效"

    # 2) 座位矩形就是覆盖层的 CSS px，构图比例抬到落屏下限
    assert screen["stage"]["src"] == {"cx": 180, "cy": 190, "w": 250, "h": 240}
    assert screen["stage"]["dst"] == {"cx": 480, "cy": 190, "w": 250, "h": 240}
    card_scale = min(250 / 460, 240 / 644)
    assert screen["stage"]["u"] == pytest.approx(screen["floor"]), (
        f"小牌上构图比例必须抬到落屏下限：u={screen['stage']['u']} floor={screen['floor']}"
    )
    assert screen["floor"] > card_scale, "下限必须真的抬起来（否则 1 点档还是铅笔线）"
    assert screen["screenMin"]["scar"] >= 4, "下限尺寸不许低于验收线（刀痕 4 CSS px）"
    assert screen["screenMin"]["number"] >= 40, "下限尺寸不许低于验收线（飘字字高 40 CSS px）"

    # 3) 三样落屏尺寸都达线
    metrics = screen["metrics"]
    assert metrics["scar"] >= 4, f"刀痕墨身 ≥ 4 CSS px：{metrics['scar']}"
    assert metrics["number"] >= 40, f"飘字字高 ≥ 40 CSS px：{metrics['number']}"
    assert metrics["sword"] >= 5, f"剑光主体 ≥ 5 CSS px：{metrics['sword']}"

    # 4) 飘字的**实际绘制字号**与落屏尺寸同源（画布即目标尺寸 ⇒ 1:1，不再被缩小）
    assert screen["numberFont"] == pytest.approx(metrics["number"] / 0.7, rel=0.01), (
        f"画到画布上的字号 = numSize：{screen['numberFont']} vs {metrics['number'] / 0.7}"
    )

    # 5) R4：飘字的**包围盒**与中剑方 `.hp-bar`（体力条）**不相交**，且留 ≥2% 牌高的余量。
    #    判据是 DOM 真值（覆盖层量 `.hp-bar` 的下沿传进来），不是"牌高百分之几"。
    card = screen["metrics"]["card"]
    box = screen["metrics"]["numberBox"]
    bar = screen["hpBar"]
    assert screen["metrics"]["keepOut"] == pytest.approx(bar["bottom"]), (
        f"硬线必须就是体力条下沿（DOM 真值）：{screen['metrics']['keepOut']} vs {bar['bottom']}"
    )
    assert box["top"] >= bar["bottom"] + 0.02 * card["h"] - 1e-6, (
        f"飘字包围盒必须与体力条矩形不相交且留 2% 牌高余量：top={box['top']:.2f} "
        f"< 体力条下沿 {bar['bottom']:.2f} + {0.02 * card['h']:.2f}（牌高 {card['h']}）"
    )
    assert box["bottom"] > bar["top"], "飘字必须在体力条**下面**（不是从上面压下来）"

    assert box["bottom"] <= card["h"], f"包围盒不许溢出牌底：{box}"
    assert box["height"] <= 0.35 * card["h"] + 1e-6, (
        f"字高 ≤ 牌高 35%：{box['height']:.2f} > {0.35 * card['h']:.2f}（牌高 {card['h']}）"
    )
    assert box["height"] >= 40 - 1e-6, f"真机这一档字高仍 ≥ 40 CSS px：{box['height']}"

    # 5b) 分析出来的包围盒必须与**真的画到哪**对得上（否则判据是自说自话）
    drawn = screen["numberDraw"]
    assert drawn is not None, "牌桌档必须真的把飘字画出来"
    assert screen["cardTop"] + box["top"] - 1e-6 <= drawn["y"] <= screen["cardTop"] + box["bottom"] + 1e-6, (
        f"画出来的字心 {drawn['y']:.1f} 必须落在包围盒里 "
        f"[{screen['cardTop'] + box['top']:.1f}, {screen['cardTop'] + box['bottom']:.1f}]"
    )

    # 5c) 牌高扫掠：两条不变式在任何牌高下都成立（上限在小牌上优先于 40px 下限，
    #     否则小牌块上必然二选一：这是 R3 明确的口径，写在 `create` 的注释里）。
    #     每档跑两次：不带硬线（走兜底比例）与带 DOM 硬线（体力条下沿 = 42.3% 牌高）。
    assert {entry["kind"] for entry in probe["sweep"]} == {"fallback", "hpBar"}
    for entry in probe["sweep"]:
        card_h = entry["h"]
        box = entry["metrics"]["numberBox"]
        keep_out = entry["metrics"]["keepOut"]
        assert keep_out is not None and keep_out > 0, f"牌高 {card_h}：硬线必须算得出来"
        if entry["kind"] == "hpBar":
            assert keep_out == pytest.approx(entry["hpBarBottom"]), (
                f"牌高 {card_h}：量到体力条就必须用它（不被兜底比例覆盖）：{keep_out}"
            )
        else:
            assert keep_out == pytest.approx(0.45 * card_h), (
                f"牌高 {card_h}：量不到体力条时才走兜底（牌高 45%）：{keep_out}"
            )
        assert box["top"] >= keep_out + 0.02 * card_h - 1e-6, (
            f"牌高 {card_h}：包围盒顶 {box['top']:.2f} 越过了硬线 {keep_out:.2f} + 2%"
        )
        assert box["bottom"] <= card_h + 1e-6, f"牌高 {card_h}：包围盒溢出牌底 {box}"
        assert box["height"] <= 0.35 * card_h + 1e-6, (
            f"牌高 {card_h}：字高 {box['height']:.2f} > 35% 上限 {0.35 * card_h:.2f}"
        )
        floor = 40 if 40 <= 0.35 * card_h else 0.35 * card_h
        assert box["height"] >= floor - 1e-6, (
            f"牌高 {card_h}：字高 {box['height']:.2f} 低于可用下限 {floor:.2f}"
        )

    # 6) 大牌桌（1920 宽、630×600 的武将块）：构图跟着牌走，不受下限牵动
    assert probe["big"]["stage"]["u"] == pytest.approx(min(630 / 460, 600 / 644))
    assert probe["big"]["metrics"]["scar"] > screen["metrics"]["scar"]

    # 7) 素材库那条链：画布仍是 1920×1080，飘字偏移一个数都不动
    assert (probe["plain"]["width"], probe["plain"]["height"]) == (1920, 1080)
    assert probe["plain"]["numberOffset"]["dy"] == pytest.approx(55 / 644)
    assert probe["plain"]["numberOffset"]["rise"] == pytest.approx(92 / 644)


# ---------------------------------------------------------------------------
# damage-fx 票 06：伤害音效 —— 解锁、对齐、静音缺省
#
# 票面只要求三件可机械判的事：
# 1. **静音缺省**：`localStorage['sgs.sfx']` 缺省 `'off'`，顶栏「♪」是唯一开关；
# 2. **解锁**：一次性 `pointerdown`，`resume()` 与手势**同一个 tick**（不许经过 await / 计时器）；
# 3. **对齐**：`tick` 的 `t` 就是 `fx.draw(t)` 的那个 `seconds`，cue 时刻取该档的 `fx.timeline`；
#    导出链（`renderAt(t)`）与素材库那条链一声不响。
#
# 行为（按钮 / 记忆 / 同一秒 / 摘层即收声 / 无 AudioContext 降级）归烟测 `U-SFX-01..05`；
# 电平（真的出声）归 `?audiotest=1` + `OfflineAudioContext`（`SgsSfx.renderOffline`）。
# ---------------------------------------------------------------------------

_MAIN_JS = STATIC_DIR / "main.js"


def _js_function_with_defaults(source: str, name: str) -> str:
    """同 :func:`_js_function`，但容得下带默认值的形参（`now = stageNow()`）。

    `_js_function` 的 `[^)]*` 会在默认值里的那个 `)` 上提前收口；`renderDamageFrame` 与
    `stageNow` 这类哨兵默认值是全仓写法，故这里按"行首 `}` 收尾"取正文。
    """
    start = source.find(f"function {name}(")
    assert start >= 0, f"找不到函数 {name}()"
    end = source.find("\n}", start)
    assert end >= 0, f"函数 {name}() 没有行首收尾的 }}"
    return source[start:end]


def test_the_sfx_toggle_is_the_single_topbar_switch_and_defaults_to_off() -> None:
    """顶栏「♪ 音效 关」：`type=button`、`aria-pressed=false`、位置在设置之后导航之前。"""
    html = INDEX_HTML.read_text(encoding="utf-8")
    button = re.search(r'<button\b[^>]*\bid="btn-sfx"[^>]*>(?P<body>.*?)</button>', html, re.S)
    assert button is not None, "index.html 里缺少顶栏音效开关 #btn-sfx（票 06 §做法 2）"
    assert re.search(r'\btype="button"', button.group(0)), (
        f"#btn-sfx 必须是 type=\"button\"：{button.group(0)}"
    )
    assert 'aria-pressed="false"' in button.group(0), "缺省关：aria-pressed 必须是 false"
    assert "♪" in button.group("body") and "音效 关" in button.group("body"), (
        f"缺省文案必须是「♪ 音效 关」：{button.group('body')}"
    )
    assert html.count('id="btn-sfx"') == 1, "音效开关只许有一枚（不做角落 chip，不做第二处开关）"
    assert "sfx-chip" not in html, "参考实现的右下角 chip 不进牌桌"

    topbar = html[html.index('id="table-topbar"'):html.index('id="table-stage"')]
    order = [
        "t-deck", "btn-discard", "t-discard", "btn-settings", "btn-sfx",
        "nav-lobby", "nav-table", "nav-replay",
    ]
    positions = [topbar.index(f'id="{name}"') for name in order]
    assert positions == sorted(positions), (
        f"顶栏右端顺序必须是 牌堆 / 弃牌 / 设置 / 音效 / 导航三键：{dict(zip(order, positions))}"
    )

    rule = _css_rule(".sfx-btn")
    assert re.search(r"opacity:\s*\.?\d", rule), f"关着时要收着（半透明）：{rule}"
    assert re.search(r"\.sfx-btn\.on\s*\{", _CSS_TEXT), "开着要有独立的 .on 档（亮起）"


def test_the_damage_sfx_state_machine_is_gesture_unlocked_and_remembers_the_switch() -> None:
    """缺省静音 / 一次性手势解锁 / 开关记忆：三条都在代码结构上钉住。"""
    effects = _strip_js_comments(_EFFECTS_JS.read_text(encoding="utf-8"))
    main = _strip_js_comments(_MAIN_JS.read_text(encoding="utf-8"))

    assert re.search(r'^const DAMAGE_SFX_KEY = "sgs\.sfx";$', effects, re.M), (
        "持久化键必须是 localStorage['sgs.sfx']（票面 §做法 2）"
    )
    assert re.search(r'^const DAMAGE_SFX_OFF = "off";$', effects, re.M), "缺省值必须是 'off'"
    assert re.search(r'^const DAMAGE_SFX_ON = "on";$', effects, re.M), "开着记 'on'"

    remembered = _js_function(effects, "damageSfxRemembered")
    assert "=== DAMAGE_SFX_ON" in remembered, (
        "只有 'on' 算开；缺键 / 'off' / 别的值一律静音（缺省静音不许被杂值绕过）"
    )
    sync = _js_function(effects, "damageSfxSync")
    assert "damageSfxRemembered()" in sync, "启动时读一次记忆"
    assert "damageSfxRemember(" not in sync, "启动时**不写**记忆（没点过就不留键）"
    assert "damageSfxSyncButton()" in sync, "启动时把文案刷成记忆里的状态"

    wire_body = _js_function(main, "wire")
    assert 'document.addEventListener("pointerdown", unlockAudio, { once: true })' in wire_body, (
        "解锁挂在**一次性** pointerdown 上（票面 §做法 1：点哪里都算手势）"
    )
    assert '$("btn-sfx").addEventListener("click", damageSfxToggle)' in wire_body, (
        "顶栏「♪」是唯一开关"
    )
    assert "damageSfxSync()" in wire_body, "wire() 里先读记忆再接线"
    assert "damageSfxAudiotestRequested()" in wire_body and "damageSfxAudiotest()" in wire_body, (
        "?audiotest=1 在装配末尾触发（离线电平自检）"
    )

    unlock = _js_function(main, "unlockAudio")
    assert "damageSfxUnlock()" in unlock, "手势处理器同步交给 effects.js"
    assert "removeEventListener" in unlock, "一次性：跑过就把自己摘掉"
    assert "await" not in unlock and "setTimeout" not in unlock and "fetch" not in unlock, (
        "resume() 必须在手势的**同一个 tick**：不许经过 await / 计时器 / 回调"
    )

    unlock_fx = _js_function(effects, "damageSfxUnlock")
    assert "if (!damageSfxOn) return false;" in unlock_fx, (
        "开关关着时连 AudioContext 都不建（缺省静音是零代价的）"
    )
    ensure = _js_function(effects, "damageSfxEnsureLanes")
    assert "DAMAGE_FX_CONCURRENCY" in ensure, (
        "声道数 = 同时在播上限（3）：一条声道一路时钟，否则三条并发会互相吞 cue"
    )
    assert "player.start()" in ensure, "建声道的那一次就要 start()（同一个 tick 里 resume）"
    set_enabled = _js_function(effects, "damageSfxSetEnabled")
    assert "if (damageSfxOn) damageSfxEnsureLanes();" in set_enabled, (
        "点开「♪」本身就是手势：声道在这一下建"
    )
    assert "damageSfxRemember(damageSfxOn)" in set_enabled, "开关状态写进 localStorage"
    assert "damageSfxSyncButton()" in set_enabled, "文案 / aria-pressed / .on 同处更新"


def test_the_damage_sfx_tick_shares_the_play_clock_and_the_export_chain_stays_silent() -> None:
    """`tick` 吃演出自己的 `seconds`（与 `fx.draw` 同一个变量）；导出 / 素材库链一声不响。"""
    effects = _strip_js_comments(_EFFECTS_JS.read_text(encoding="utf-8"))

    frame = _js_function_with_defaults(effects, "renderDamageFrame")
    assert "play.fx.draw(seconds)" in frame, "画面用 `seconds`（一次性算好的演出秒数）"
    assert "damageSfxTick(play, seconds)" in frame, (
        "音效必须吃**同一个** `seconds`——另起墙钟会漂（票面 §做法 3）"
    )
    assert "Date.now" not in frame and "performance.now" not in frame, (
        "演出帧里不许再读墙钟"
    )

    tick = _js_function(effects, "damageSfxTick")
    assert "lane.player.tick(seconds, voice)" in tick, "tick 的 t 就是演出秒数"
    for wall_clock in ("Date.now", "stageNow", "performance.now", "requestAnimationFrame"):
        assert wall_clock not in tick, f"音效时钟里不许出现 {wall_clock}（不另起墙钟）"
    assert effects.count("damageSfxTick(") == 2, (
        "tick 只有一个驱动点（定义 + renderDamageFrame 里那一次）"
    )

    voice = _js_function(effects, "damageSfxVoice")
    assert "play.fx && play.fx.timing" in voice, (
        "档位参数取 anim.ini 解算出来的毫秒表（票 04），不另写一份数字"
    )
    for field in ("flight", "strobe", "split"):
        assert f'timing.{field}' in voice, f"sfx.js 要的 {field} 必须从 timing 折算"

    start = _js_function(effects, "damageSfxStart")
    assert "damageSfxTimeline(play.fx && play.fx.timeline)" in start, (
        "cue 时刻取**这一档**的 fx.timeline（anim.ini 解算出来的那一份）"
    )
    assert "play.slot" in start, "一条并发演出一条声道（槽位唯一）"

    assert "damageSfxStop(play)" in _js_function(effects, "finishDamagePlay"), (
        "摘层即收声"
    )
    assert "damageSfxStopAll()" in _js_function(effects, "clearDamageFx"), (
        "换局 / 版式作废：在播的演出一起收声"
    )

    # 导出链（`renderAt(t)` / `?export=1` / 素材库）一声不响：fx-core 根本不认识 SgsSfx，
    # 牌桌也不提供 `renderAt` 这条路。
    fx_core = (STATIC_DIR / "fx-core.js").read_text(encoding="utf-8")
    assert "SgsSfx" not in fx_core, "画面那份脚本不许碰音效（导出链只驱动画面）"
    assert "renderAt" not in effects, "牌桌不提供 renderAt（导出是素材库那条链的事）"

    audiotest = _js_function(effects, "damageSfxAudiotest")
    assert "renderOffline" in audiotest, "自检走 SgsSfx.renderOffline（纯函数式）"
    assert "gameTiming(" in audiotest, "四档各自的时间轴都渲染一遍"
    assert 'setAttribute("data-audio"' in audiotest, "电平写进 <html data-audio>"
    assert "unavailable" in audiotest, "没有 OfflineAudioContext ⇒ unavailable（不抛错）"
    requested = _js_function(effects, "damageSfxAudiotestRequested")
    assert "audiotest=1" in requested, "只认 ?audiotest=1"


def test_the_sfx_mixer_table_is_on_top_with_six_separate_voices_and_no_audio_files() -> None:
    """调音台：`VOICE` 表在文件顶部单独一段，六个声音是六个独立函数，仓库里没有音频文件。"""
    text = (STATIC_DIR / "sfx.js").read_text(encoding="utf-8")
    block = re.search(r"var VOICE = \{(?P<body>.*?)\n  \};", text, re.S)
    assert block is not None, "sfx.js 顶部必须有一张 `var VOICE = { … }` 调音台"
    keys = re.findall(r"^    (\w+):", block.group("body"), re.M)
    assert keys == ["riser", "shing", "whoosh", "impact", "strobe", "split"], (
        f"调音台必须是这六行（六个声音各一行）：{keys}"
    )
    for voice in keys:
        assert re.search(rf"^  function {voice}\(", text, re.M), (
            f"{voice} 必须是独立函数（用户要逐一替换，不许合并成一个大函数）"
        )
    assert text.index("var VOICE") < text.index("function riser("), (
        "调音台必须在六个函数**之前**（改一个数就生效，不用碰合成代码）"
    )
    assert text.index("function scheduleAll(") > text.index("function split("), (
        "排播在六个声音之后（表 → 声音 → 排播 → 播放器的次序）"
    )

    audio_suffixes = {".mp3", ".ogg", ".wav", ".m4a", ".flac", ".aac"}
    found: list[Path] = []
    for root in (
        REPO_ROOT / "tools", REPO_ROOT / "src", REPO_ROOT / "tests",
        REPO_ROOT / "docs", REPO_ROOT / "开发文档",
    ):
        if not root.exists():
            continue
        found.extend(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in audio_suffixes
        )
    assert not found, (
        f"音效全部现场合成，仓库里不得出现音频文件（票面 §音效以后怎么改）：{[str(p) for p in found[:5]]}"
    )
