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


def test_anim_ini_lists_the_d8_durations_without_debug_scale() -> None:
    """`anim.ini` = D8 的 13 个键；`flight_stage_out = 0`；`debug_scale` 不进产品。"""
    text = _ANIM_INI.read_text(encoding="utf-8")
    assert "debug_scale" not in text, "debug_scale 必须从 anim.ini 整体移除（含键与注释）"
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = re.sub(r"\s*[#;].*$", "", raw).strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        assert separator, f"anim.ini 行不是 key = value：{raw}"
        entries[key.strip()] = value.strip()
    assert set(entries) == _D8_INI_KEYS, (
        f"anim.ini 的键必须与 D8 表一致：缺 {sorted(_D8_INI_KEYS - set(entries))}；"
        f"多 {sorted(set(entries) - _D8_INI_KEYS)}"
    )
    for key, value in entries.items():
        float(value)  # 解析失败即红（页面此时会退回默认）
    assert float(entries["flight_stage_out"]) == 0, (
        "处理区 → 弃牌堆不飞：flight_stage_out 必须是 0"
    )


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
