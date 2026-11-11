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
import sys
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
# damage-fx 票 09：伤害排播（队列 / 排播闸门 / 座位几何 / 画布生命周期）从 `effects.js`
# 搬进这份脚本；`effects.js` 只剩动效层（目标线 / 高亮 / 体力闪 / 技能名 / 白光）。
_DAMAGE_FX_JS = STATIC_DIR / "damage-fx.js"


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

# 住在同一份 `anim.ini` 里、但**默认值与钳制都不在本文件**的外来键前缀（`anim.js` 只搬数字）：
# damage-fx 的三族 + chain-death-voices 的铁索。`test_anim_ini_lists_the_d8_durations_...` 用
# 它把"不属于任何演出家族的键"过滤出来，逐族键表归各自的用例钉。
_ANIM_FOREIGN_PREFIXES = ("damage_fx_", "chain_fx_", "death_fx_", "hp_loss_fx_", "heal_fx_")

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

_DAMAGE_FX_GLOBALS = {
    "fx-core.js": "SgsDamageFX",
    "sfx.js": "SgsSfx",
    # damage-fx-natures 票 01：雷电家族与剑光同姿态（普通脚本、全局名、不碰 DOM）。
    "fx-thunder.js": "SgsThunderFX",
    "sfx-thunder.js": "SgsThunderSfx",
    # damage-fx-natures 票 03：火焰家族逐字同形。
    "fx-fire.js": "SgsFireFX",
    "sfx-fire.js": "SgsFireSfx",
}


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
    """`anim.ini` = D8 的 13 个键（外加各演出家族的外来键）；`debug_scale` 不进产品。"""
    text = _ANIM_INI.read_text(encoding="utf-8")
    assert "debug_scale" not in text, "debug_scale 必须从 anim.ini 整体移除（含键与注释）"
    entries = _ini_entries()
    # 同一份 ini 也住着各演出家族的外来键（用户改一份文件就能调所有时长），这里只钉 D8
    # 那一族：伤害（19 + 雷电 20 + 火焰 20 + 三条并发表）由下面的用例钉，铁索的两个键由
    # `test_the_chain_fx_module_shape_and_its_two_timing_keys` 钉。
    shared = {key for key in entries if not key.startswith(_ANIM_FOREIGN_PREFIXES)}
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
#
# **票 10 起键表不再写在测试里**：`SgsDamageFX.TIMING_KEYS`（= `DEFAULT_TIMING` 的键）是
# 唯一真源，这些用例一律从上面那个 node 探针（`damage_fx_probe`）派生期望值，再与磁盘上的
# `anim.ini` 对表。三处各写一份名单的时代，加字段漏改一处只会静默不生效。
# ---------------------------------------------------------------------------

_DAMAGE_FX_VARIANTS = (1, 2, 3, 4)


# 剑光的键：`damage_fx_<档>_<字段>`（雷电是 `damage_fx_thunder_<档>_<字段>`，不在此列）。
_SWORD_TIMING_KEY = re.compile(r"^damage_fx_\d+_")
# 雷的键同理：`damage_fx_thunder_<档>_<字段>`——**不带档位号**的 `damage_fx_thunder_concurrency`
# （票 02 的并发表）不是时长键，别混进这张对照表。
_THUNDER_TIMING_KEY = re.compile(r"^damage_fx_thunder_\d+_")
# 火的键同理：`damage_fx_fire_<档>_<字段>`（`damage_fx_fire_concurrency` 是并发表，不是时长键）。
_FIRE_TIMING_KEY = re.compile(r"^damage_fx_fire_\d+_")


def _sword_ini_entries() -> dict[str, str]:
    """`anim.ini` 里**剑光**那一族的键（雷电的 `damage_fx_thunder_*` 由自己那条用例钉）。"""
    return {key: value for key, value in _ini_entries().items() if _SWORD_TIMING_KEY.match(key)}


def test_anim_ini_lists_the_damage_fx_durations_per_variant(damage_fx_probe: dict) -> None:
    """19 个键齐全、可解析，且每档都满足 `flight ≤ impact ≤ total ≤ 2200`、`impact + fade ≤ total`。

    键表**不是**写在测试里的第二份清单：期望值来自 `fx-core.js` 的 `DEFAULT_TIMING`
    （票 10 的单一真源），这里只把它与磁盘上的 `anim.ini` 对表。
    """
    damage = _sword_ini_entries()
    expected = set(damage_fx_probe["timingKeys"])
    assert set(damage) == expected, (
        f"damage-fx 的时长键必须与 fx-core 的 `DEFAULT_TIMING` 一致："
        f"缺 {sorted(expected - set(damage))}；多 {sorted(set(damage) - expected)}"
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
const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 每份脚本单独编译、同一个 context 的全局词法作用域跨脚本可见——与页面一致的加载方式。
 * 每次都起**新的** sandbox：`fx-core.js` 全是 `var` / 函数声明，同一 context 载两遍会互相
 * 覆盖，而下面那条反证必须从零起一份打过补丁的。 */
function makeSandbox(iniText = ini) {
  const noteNode = { textContent: "", hidden: true };
  const context = vm.createContext({
    console,
    setTimeout,
    clearTimeout,
    fetch: async () => ({ ok: true, text: async () => iniText }),
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
  return context;
}

function load(context, name, source) {
  vm.runInContext(source ?? readFileSync(staticPath(name), "utf8"), context, { filename: name });
}

/* ------------------------------------------------- ① 真脚本：正常那一份 ---- */
const context = makeSandbox();
for (const name of ["anim.js", "fx-core.js", "fx-thunder.js", "fx-fire.js", "effects.js", "damage-fx.js"]) load(context, name);
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
       // 票 10：字段名名单的单一真源（由 DEFAULT_TIMING 的键生成，冻结数组）。
       timingFields: fx.TIMING_FIELDS,
       timingKeys: fx.TIMING_KEYS,
       timingFrozen: [Object.isFrozen(fx.TIMING_FIELDS), Object.isFrozen(fx.TIMING_KEYS)],
       resolved,
       clamps,
       damageFxTiming: {
         1: damageFxTiming(null, 1),
         4: damageFxTiming(null, 4),
         resolvedFromDamageFx: fx.gameTiming(4, damageFxTiming(null, 4)).timeline.DURATION,
       },
       // 票 03：三个家族全开——null / 缺字段 / NORMAL / THUNDER / FIRE / 未登记属性都播。
       naturePlayable: {
         null: damageFxNaturePlayable(null),
         undefined: damageFxNaturePlayable(undefined),
         NORMAL: damageFxNaturePlayable("NORMAL"),
         FIRE: damageFxNaturePlayable("FIRE"),
         THUNDER: damageFxNaturePlayable("THUNDER"),
         ICE: damageFxNaturePlayable("ICE"),
       },
       // 票 01：属性 → 画面模块（分派查表只住 damage-fx.js）。
       rendererFor: {
         thunder: damageFxRendererFor("THUNDER") === SgsThunderFX,
         fire: damageFxRendererFor("FIRE") === SgsFireFX,
         normal: damageFxRendererFor(null) === SgsDamageFX,
         explicitNormal: damageFxRendererFor("NORMAL") === SgsDamageFX,
         unknown: damageFxRendererFor("ICE") === SgsDamageFX,
       },
       fieldsFor: {
         sword: damageFxTimingFields(null),
         thunder: damageFxTimingFields("THUNDER"),
         fire: damageFxTimingFields("FIRE"),
       },
       timingThunder: {
         1: damageFxTiming("THUNDER", 1),
         3: damageFxTiming("THUNDER", 3),
       },
       timingFire: {
         1: damageFxTiming("FIRE", 1),
         3: damageFxTiming("FIRE", 3),
       },
       cap: damageFxCapFor("THUNDER"),
       // §1 / §6：逐属性**驱动真函数**（不是整段源码 grep），供服务端枚举那条用例读。
       natureDispatch: (() => {
         const out = {};
         for (const name of ["NORMAL", "FIRE", "THUNDER"]) {
           const api = damageFxRendererFor(name);
           const spec = damageFxFlashSpec(name);
           out[name] = {
             kind: damageFxNatureKind(name),
             playable: damageFxNaturePlayable(name),
             renderer: api === SgsThunderFX ? "SgsThunderFX"
               : api === SgsFireFX ? "SgsFireFX"
               : api === SgsDamageFX ? "SgsDamageFX" : "none",
             flash: spec ? spec.className : null,
           };
         }
         return out;
       })(),
       // 雷电 / 火焰都是已接的家族：模块缺件时不回落剑光（fail-open 只给未登记属性）。
       thunderWithoutModule: (() => {
         const saved = window.SgsThunderFX;
         window.SgsThunderFX = undefined;
         const missing = damageFxRendererFor("THUNDER");
         window.SgsThunderFX = saved;
         return missing === null;
       })(),
       fireWithoutModule: (() => {
         const saved = window.SgsFireFX;
         window.SgsFireFX = undefined;
         const missing = damageFxRendererFor("FIRE");
         window.SgsFireFX = saved;
         return missing === null;
       })(),
     });
   })()`,
  context,
  { filename: "damage_fx_probe.js" },
);

/* ------------- ② 票 02：并发表走**真 ini 文本** → `animLoadConfig`（每个变体一份新 sandbox） ---- */
/* 每次都从零起一份 sandbox（= 用户改完 ini 刷新页面）：`animLoadConfig` 只做 `Object.assign`，
 * 上一份表里留下的键不会因为这一份删了而消失，所以"缺键回退"只能从零解析。这条链同时证明：
 * 新键没被 `anim.js` 的未知键过滤器删掉、解析出的就是生效值、钳制落在读者侧。 */
const iniPatch = (patch) => {
  let text = ini;
  for (const [key, value] of Object.entries(patch)) {
    const line = new RegExp("^" + key + "\\s*=.*$", "m");
    text = text.replace(line, value === null ? "" : key + " = " + value);
  }
  return text;
};

async function capsWithIni(text) {
  const ctx = makeSandbox(text);
  for (const name of ["anim.js", "fx-core.js", "fx-thunder.js", "fx-fire.js", "effects.js", "damage-fx.js"]) {
    load(ctx, name);
  }
  return JSON.parse(await vm.runInContext(`(async () => {
    await animReady;
    return JSON.stringify({
      caps: {
        normal: damageFxCapFor(null),
        explicitNormal: damageFxCapFor("NORMAL"),
        thunder: damageFxCapFor("THUNDER"),
        fire: damageFxCapFor("FIRE"),
        unknown: damageFxCapFor("ICE"),
      },
      keys: Object.keys(animConfig),
      note: $("t-anim-note").textContent,
    });
  })()`, ctx, { filename: "caps_probe.js" }));
}

const capsReal = {
  ini: await capsWithIni(ini),
  fireFive: await capsWithIni(iniPatch({ damage_fx_fire_concurrency: 5 })),
  clampedLow: await capsWithIni(iniPatch({
    damage_fx_concurrency: 0,
    damage_fx_thunder_concurrency: -2,
  })),
  clampedHigh: await capsWithIni(iniPatch({
    damage_fx_concurrency: 9,
    damage_fx_thunder_concurrency: 2.6,
  })),
  junk: await capsWithIni(iniPatch({ damage_fx_thunder_concurrency: "abc" })),
  missing: await capsWithIni(iniPatch({
    damage_fx_concurrency: null,
    damage_fx_thunder_concurrency: null,
    damage_fx_fire_concurrency: null,
  })),
};

/* ------------------- ② 反证：把 fx-core 的一个字段删掉，读取面必须跟着少一个 ---- */
/* `damage_fx_4_split` 是 `split` 字段**唯一**的键：删掉它，`DEFAULT_TIMING` 里就没有
 * `split`，由键生成的 `TIMING_FIELDS` 随之少一个 —— `damage-fx.js` 也就不再去读
 * `damage_fx_4_split`。这正是票 10 要证明的"真的收成了一处"。 */
const source = readFileSync(staticPath("fx-core.js"), "utf8");
const patchedSource = source.replace(/(\n|,)\s*damage_fx_4_split:\s*\d+/, "");
const patched = makeSandbox();
load(patched, "anim.js");
load(patched, "fx-core.js", patchedSource);
load(patched, "fx-thunder.js");
load(patched, "fx-fire.js");
load(patched, "effects.js");
load(patched, "damage-fx.js");
const negative = await vm.runInContext(
  `(async () => {
     await animReady;
     return JSON.stringify({
       fields: SgsDamageFX.TIMING_FIELDS,
       keys: SgsDamageFX.TIMING_KEYS,
       four: damageFxTiming(null, 4),
       // 缺键的画面并不会变：split 退回 VARIANTS[4].split。所以"静默不生效"只能靠名单比对抓。
       resolvedSplit: SgsDamageFX.gameTiming(4, damageFxTiming(null, 4)).ms.split,
     });
   })()`,
  patched,
  { filename: "damage_fx_negative.js" },
);

process.stdout.write(JSON.stringify({
  ...JSON.parse(report),
  capsReal,
  negative: { patched: patchedSource !== source, ...JSON.parse(negative) },
}));
"""


def _damage_fx_probe() -> dict:
    """跑真 `anim.js` + 真 `fx-core.js` + `effects.js` + `damage-fx.js`（node vm），拿回实测结果。"""
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
    missing = set(probe["timingKeys"]) - set(probe["animConfigKeys"])
    assert not missing, f"这些键必须进 animConfig（否则用户改了不生效），缺：{sorted(missing)}"


def test_effects_hands_the_damage_fx_timing_over_from_the_live_ini_table(damage_fx_probe: dict) -> None:
    """`damage-fx.js:damageFxTiming` 只搬数字：有键的档给全六个量，没键的档不硬造。

    票 09 起这个函数住 `damage-fx.js`（测试函数名保留原名：票 04 的 `## Answer` 与
    acceptance-map 都引用它，重命名会让 `check_acceptance` 红）。

    这一层不兜底、不钳制（默认与钳制都在 `fx-core.js`）：缺 `damage_fx_1_strobe` 时返回的
    表里就**没有**这个键，fx-core 退 `VARIANTS[1].strobe = 0`。票 05 的播放器读它。
    """
    probe = damage_fx_probe
    ini = {key: float(value) for key, value in _sword_ini_entries().items()}
    four = probe["damageFxTiming"]["4"]
    # 期望值 = fx-core 导出的字段名名单（票 10 的单一真源），测试里不再另写一份六个名字。
    assert set(four) == {f"damage_fx_4_{field}" for field in probe["timingFields"]}, (
        f"4 点档的六个量都要搬过去（名单 = SgsDamageFX.TIMING_FIELDS）：{sorted(four)}"
    )
    for key, value in four.items():
        assert value == ini[key], f"{key} 必须原样来自 anim.ini"
    one = probe["damageFxTiming"]["1"]
    assert set(one) == {key for key in ini if key.startswith("damage_fx_1_")}, (
        f"1 点档没有 strobe / split 的键，就不该硬造：{sorted(one)}"
    )
    assert probe["damageFxTiming"]["resolvedFromDamageFx"] == pytest.approx(
        ini["damage_fx_4_total"] / 1000
    ), "把搬过来的表交给 fx-core，解算出同一段 4 点档时长"


def test_the_damage_fx_timing_field_names_have_a_single_source(damage_fx_probe: dict) -> None:
    """票 10：六个字段名的名单只有 `fx-core.js` 一份，读取面与测试都从它派生。

    三件事：
    1. 名单确实由 `DEFAULT_TIMING` 的键生成，且是冻结数组（读方只许读）；
    2. 读取面（票 09 起住 `damage-fx.js`）里**没有**第二份手写清单——它完全等于导出的
       字段名；
    3. **反证**：把 `DEFAULT_TIMING` 里的 `damage_fx_4_split` 删掉，读取面随之少一个
       `damage_fx_4_split`，而磁盘上的 `anim.ini` 仍有这一行 ⇒ 键表比对会红。
       缺键时画面照样是 `VARIANTS[4].split = 44`——正是这种"静默不生效"，只能靠名单兜住。
    """
    probe = damage_fx_probe
    fields = probe["timingFields"]
    defaults = probe["defaults"]

    # 1) 由 `DEFAULT_TIMING` 的键生成（`damage_fx_<档>_<字段>`），次序按首次出现。
    derived: list[str] = []
    for key in defaults:
        field = key.split("_", 3)[3]
        if field not in derived:
            derived.append(field)
    assert list(fields) == derived, (
        f"`TIMING_FIELDS` 必须由 `DEFAULT_TIMING` 的键生成：期望 {derived}，实际 {list(fields)}"
    )
    assert list(probe["timingKeys"]) == list(defaults), (
        "`TIMING_KEYS` 必须就是 `DEFAULT_TIMING` 的键"
    )
    assert probe["timingFrozen"] == [True, True], (
        "两个导出必须是冻结数组（读方只许读，不许再各改一份）"
    )

    # 2) 读取面那一侧没有第二份手写清单：任何数组字面量里都凑不出两个字段名。
    #    票 09 起读取面住 `damage-fx.js`（`damageFxTimingFields` / `damageFxTiming`）。
    reader = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    assert re.search(r"\.TIMING_FIELDS\b", reader), (
        "damage-fx.js 必须从 `SgsDamageFX.TIMING_FIELDS` 读字段名，而不是自己写一份"
    )
    for array in re.findall(r"\[[^\[\]]*\]", reader):
        repeated = [field for field in fields if f'"{field}"' in array]
        assert len(repeated) < 2, f"damage-fx.js 里又手写了一份字段名清单：{array.strip()}"

    # 3) 反证：删掉 fx-core 里唯一的 `split` 键 ⇒ 读取面跟着少一个。
    negative = probe["negative"]
    assert negative["patched"] is True, (
        "反证没生效：补丁正则没匹配到 `damage_fx_4_split`（`DEFAULT_TIMING` 的排版变了？）"
    )
    assert "split" in fields and "damage_fx_4_split" in probe["timingKeys"], (
        "前提：`split` 在导出的名单里"
    )
    assert "split" not in negative["fields"], (
        "删掉唯一的 `split` 键后，由键生成的字段名单必须少一个"
    )
    assert "damage_fx_4_split" not in negative["keys"], "键表也必须跟着少一个"
    assert "damage_fx_4_split" not in negative["four"], (
        "damage-fx.js 的读取面必须跟着 fx-core 走：键删了就不该再去读它"
    )
    ini_keys = set(_sword_ini_entries())
    assert "damage_fx_4_split" in ini_keys, "前提：磁盘 anim.ini 仍有这一行（否则反证无意义）"
    assert negative["resolvedSplit"] == 44, (
        "缺键时画面仍是 VARIANTS[4].split = 44：这种失败不报错，只能由键表比对抓住"
    )


# ---------------------------------------------------------------------------
# damage-fx-natures 票 02：节奏参数可配（三属性并发表）
#
# 牌桌要的不是"同时播 3 条"这个数，而是**这个数可以随时长一起调**：演出时长住在 `anim.ini`
# 且可改，"时长变长了、还能同时播几条"必须跟着能改。分属性是 spec 的选择（Q8B）：火 2.0s 的
# 天陨与剑光 1.5s 的一刀，答案本来就不该相同；一个全局值会把三者绑死。
#
# 本条钉**读的一侧**（键表 / 钳制 / 读口唯一）；排播与声道池的行为归烟测 `U-DMG-12/13`、
# `U-SFX-06`。
# ---------------------------------------------------------------------------

# `anim.ini` 的三个并发表键（缺一即"某个家族悄悄回到写死的 3"）。
_CAP_KEYS = (
    ("damage_fx_concurrency", "无属性"),
    ("damage_fx_thunder_concurrency", "雷电"),
    ("damage_fx_fire_concurrency", "火焰"),
)


def test_the_concurrency_cap_is_read_per_nature_from_anim_ini(damage_fx_probe: dict) -> None:
    """票 02 §1：三键齐全、按属性读、钳在读者侧；`damageFxCapFor` 是唯一的 cap 口。

    每个变体都是**一份真 ini 文本 → 全新 sandbox → `animLoadConfig()`**（= 用户改完文件刷新
    页面），所以这条链同时证明：新键没被 `anim.js` 的未知键过滤器删掉、解析出的就是生效值、
    钳制确实在读者侧（`animTiming` 只搬数字）。

    - 初值 3、各带一行注释；缺键 / 非数字 → 3（解析器跳过非法值，读取侧再兜一层）；
      `0` / 负数 → 1；`> 8` → 8；小数取整（`cap` 是条数，`2.6` ⇒ 3）；
    - **分属性**：火调到 5 ⇒ 火 5 条、无属性仍 3 条（一个全局值会把三者绑死）；
    - 读口唯一：三个键名只在家族表里出现一次；`animTiming(...)` 只被 `damageFxCapFor` /
      `damageFxTiming` 两个函数读，且都在**调用时**解析（`anim.ini` 是异步加载的）。
    """
    ini_text = _ANIM_INI.read_text(encoding="utf-8")
    entries = _ini_entries()
    header = ini_text.index("并发表")
    # 三个键都在「并发表」那一块里，且各带一行注释（用户改得动，改完刷新即生效）。
    start = ini_text.rindex("# ====", 0, header)
    last_key_at = max(ini_text.index(key) for key, _family in _CAP_KEYS)
    block = ini_text[start:ini_text.index("# ====", last_key_at)]
    for key, family in _CAP_KEYS:
        assert key in entries, f"anim.ini 缺少并发表键 {key}（{family}的 cap）"
        assert re.search(rf"^{key}\s*=\s*3\s+#\s*\S+", ini_text, re.M), (
            f"{key} 必须初值 3 且带一行注释（{family}）：{entries[key]}"
        )
        assert key in block, f"{key} 要住在「并发表」那一块里"
    for phrase in ("1~8", "缺省", "钳"):
        assert phrase in block, f"并发表的注释要写清钳制口径（找 {phrase}）：\n{block}"

    caps = damage_fx_probe["capsReal"]
    base = {"normal": 3, "explicitNormal": 3, "thunder": 3, "fire": 3, "unknown": 3}
    # 主路径：真 ini 的三个键**活着**（没被当成未知键删掉），且就是生效值。
    assert caps["ini"]["caps"] == base, f"三键初值都是 3：{caps['ini']['caps']}"
    for key, family in _CAP_KEYS:
        assert key in caps["ini"]["keys"], f"{key}（{family}）必须进 animConfig（否则用户改了不生效）"
    assert "未知键" not in caps["ini"]["note"], caps["ini"]["note"]

    assert caps["fireFive"]["caps"] == {**base, "fire": 5}, (
        f"火调到 5：火 5 条、无属性仍 3 条（分属性的意义）：{caps['fireFive']['caps']}"
    )
    low = caps["clampedLow"]["caps"]
    assert (low["normal"], low["explicitNormal"], low["unknown"]) == (1, 1, 1), (
        f"0 / 负数 → 1（不能把演出关死）：{low}"
    )
    assert low["thunder"] == 1 and low["fire"] == 3, f"钳制按属性各自生效：{low}"
    high = caps["clampedHigh"]["caps"]
    assert high["normal"] == 8 and high["unknown"] == 8, f"9 → 8（spec §多目标 1–8）：{high}"
    assert high["thunder"] == 3, f"2.6 取整到 3（cap 是条数）：{high}"
    assert caps["junk"]["caps"] == base, (
        f"非数字 → 解析器跳过该行 ⇒ 缺键 ⇒ 3（ini 里写错不该让伤害不演）：{caps['junk']['caps']}"
    )
    assert "不是数字" in caps["junk"]["note"], caps["junk"]["note"]
    for key in ("damage_fx_concurrency", "damage_fx_fire_concurrency"):
        assert key in caps["junk"]["keys"], "前提：非法值只跳过那一行，另两个键照旧"
    assert "damage_fx_thunder_concurrency" not in caps["junk"]["keys"], (
        "非法值不该进 animConfig（读取侧因此走缺键兜底）"
    )
    assert caps["missing"]["caps"] == base, "三个键都缺 ⇒ 全部回退缺省 3（不报错、不静音）"
    for key, _family in _CAP_KEYS:
        assert key not in caps["missing"]["keys"], f"缺行时 animConfig 里不该有 {key}"

    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    for key, family in _CAP_KEYS:
        assert damage_fx.count(key) == 1, (
            f"cap 键名只在家族表里写一次（{family}）——读的一侧统一走 damageFxCapFor"
        )
    readers = [
        name for name in _DAMAGE_FX_FUNCTIONS
        if "animTiming(" in _js_function_with_defaults(damage_fx, name)
    ]
    assert readers == ["damageFxTiming", "damageFxCapFor"], (
        f"只有这两个函数读 anim.ini 的时长 / 并发表（都在**调用时**解析）：{readers}"
    )
    # 计数口径按**家族**归一（null / NORMAL / 未登记属性同族），闸门只认这一个计数。
    live = _js_function(damage_fx, "damageFxLiveOf")
    assert "damageFxNatureKind(" in live and "damagePlays" in live, (
        "在播计数走 damageFxNatureKind 归一、只数 damagePlays"
    )
    gate = _js_function(damage_fx, "pumpDamageQueue")
    assert "damageFxLiveOf(" in gate and "damageFxCapFor(" in gate, (
        "闸门按属性判满：liveOf(nature) < capFor(nature) 才开演（行为归烟测 U-DMG-12/12b/13）"
    )
    # 票 08：cap 改成**该家族的排队上限**（在播 + 排队），判在**入队**处；闸门只判"能不能上"。
    fire = _js_function(damage_fx, "fireDamageEventsThrough")
    assert "damageFxPendingOf(" in fire and "damageFxCapFor(" in fire, (
        "超出该家族排队上限的事件在入队处就丢掉（串行后 live 恒为 1，按 live 判 cap 等于失效）"
    )
    pending = _js_function(damage_fx, "damageFxPendingOf")
    assert "damageFxLiveOf(" in pending and "damageQueue" in pending, (
        "排队计数 = 在播 + 队列（damageFxLiveOf 归一，队列按同一判据分类）"
    )


def test_the_game_profile_honours_anim_ini_and_clamps_its_numbers(damage_fx_probe: dict) -> None:
    """`SgsDamageFX.gameTiming`：默认值 = `DEFAULT_TIMING` = ini；四条钳制与缺键 / 垃圾值兜底。"""
    probe = damage_fx_probe
    ini = {key: float(value) for key, value in _sword_ini_entries().items()}
    # 期望值来自磁盘上的 `anim.ini`（独立于 fx-core 的那一侧），不是第三份手写清单。
    assert set(probe["defaults"]) == set(ini), (
        "fx-core 的内置默认必须恰好覆盖 anim.ini 的 damage_fx_* 键："
        f"缺 {sorted(set(ini) - set(probe['defaults']))}；"
        f"多 {sorted(set(probe['defaults']) - set(ini))}"
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
    """收尾的**唯一出口**是 `schedulePlaybackStep(delayOf(record))`（票 07 起暂停门就在那儿）。

    票 07 把"排下一步"从 `stepPlayback` 里挪进 `schedulePlaybackStep`（那一步才有机会先问
    "演出要不要冻住记录流"）：这里只钉出口唯一、仍读 `delayOf(record)`、且 `immediate` 分支
    绕开暂停门。暂停的行为归烟测 `U-DMG-14*`。
    """
    text = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(text)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    schedule = [
        line.strip() for line in body.splitlines()
        if "schedulePlaybackStep(" in line and not line.strip().startswith("//")
    ]
    assert schedule == ["if (!immediate) schedulePlaybackStep(delayOf(record));"], (
        "stepPlayback 的排程只剩 schedulePlaybackStep(delayOf(record)) 一个出口、"
        f"且只在非 immediate 侧（`immediate === true` 永远不暂停）：{schedule}"
    )
    # `immediate` 路径不许被暂停拖住：flushPlayback 的连跑 / 烟测的 step() 都走这条。
    assert "setTimeout" not in _strip_js_comments(body), (
        "stepPlayback 收尾不许自己排计时器：出口只有 schedulePlaybackStep（票 07）"
    )
    # 首步也要过暂停门：用户口径是"动画**演出期间**暂停"，上一批的演出还有尾巴时新批照样要等。
    apply_view = _js_function(text, "applyView")
    assert "schedulePlaybackStep(state.eventDelayMs)" in apply_view, (
        "applyView 的新批首步也走同一个排程口（否则演出尾巴里送来的新记录照旧推进）"
    )
    assert "setTimeout(() => stepPlayback(false)" not in _strip_js_comments(text), (
        "整个 session.js 里不许再有绕开暂停门的排程（两个出口都归 schedulePlaybackStep）"
    )

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


# ---------------------------------------------------------------------------
# damage-fx-natures 票 07：3 / 4 档演出期间暂停前端记录回放
#
# 判据 = 在播表（`damage-fx.js:damagePlays`），暂停门 = 回放排程（`session.js` 的
# `schedulePlaybackStep`）。两件机械事实：
# 1. **缝的方向**：`session.js` 不认识伤害层（不读 `damagePlays`、不认档位号），伤害层也
#    不推进回放——它在文件末尾往登记表里放一条**只读**判据 `damageFxPlaybackHold`；
# 2. **"该暂停"是登记表，不是写死"只有伤害 3 档"**（用户 2026-11-10：以后 回复体力 3 档、
#    以及可能的其它重演出同形）。`session.js` 只认"有没有人要求暂停 + 原因串"。
# 行为（3 / 4 档冻住、1 / 2 档照走、作废 / 换局解除）归烟测 `U-DMG-14*`。
# ---------------------------------------------------------------------------


def test_the_playback_hold_is_a_registry_not_a_hardcoded_damage_tier() -> None:
    session = _strip_js_comments(_SESSION_JS.read_text(encoding="utf-8"))
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))

    assert re.search(r"^const PLAYBACK_HOLD_POLL_MS = 120;$", session, re.M), (
        "暂停期间的轮询间隔必须是 120ms 的具名常量（与演出的错开 120ms 只是同值巧合）"
    )
    # 用户 2026-11-10（票 09）：**上限取消**——3 / 4 档在播期间不许"等够久就放行"，也不许在
    # 暂停路径里读墙钟（墙钟 = 上限的实现方式，留个口子就会被重新加回来）。
    for gone in (
        "PLAYBACK_HOLD_MAX_MS", "playbackHoldExpired", "playbackHoldAbandoned",
        "playbackHoldTimeouts", "playback_hold_timeout", "data-playback-hold-timeouts",
        "playbackHoldStartedAt",
    ):
        assert gone not in session, f"暂停上限必须整体移除（票 09）：{gone}"
    wake = _js_function(session, "stepPlaybackWhenFree")
    for clock in ("stageNow", "Date.now", "performance.now"):
        assert clock not in wake, f"没有上限就不该在暂停路径里读墙钟（票 09）：{clock}"
    assert "console.warn" not in wake, "超时告警随上限一起删掉（票 09）"

    # 登记表：任何脚本都能登记一条只读判据；重名就地替换，取首条非空原因。
    register = _js_function(session, "registerPlaybackHold")
    assert "playbackHoldProbes[name] = probe;" in register, (
        "登记就落在排播侧的那张表里（同名就地替换）"
    )
    reason = _js_function(session, "playbackHoldReason")
    assert "Object.keys(playbackHoldProbes)" in reason, "判据表是唯一的问答处（逐条问过去）"
    assert "return String(reason);" in reason, (
        "命中就返回**原因串**（实机 / 烟测读数用），排播侧不解读它"
    )
    assert "playbackHoldProbes[name]();" in reason, "每次被问都重读判据（不缓存在排计时器那一刻）"

    # 唤醒体：先问判据再生效，暂停 / 超时 / 放空三条分支都在这一处。
    wake = _js_function(session, "stepPlaybackWhenFree")
    assert wake.index("playbackHoldReason()") < wake.index("stepPlayback(false)"), (
        "每次被唤醒都要**重新读**判据，再决定推不推进（不是在排计时器那一刻算一次）"
    )
    assert "schedulePlaybackStep(PLAYBACK_HOLD_POLL_MS)" in wake, (
        "仍要暂停 ⇒ 走**同一个**排程口再等一轮（不另抄一行 setTimeout）"
    )
    # 唤醒体只有两条分支：放行 / 继续等（票 09 删掉了第三条"到点放弃"）。
    assert wake.count("stepPlayback(false)") == 1, (
        "暂停分支不许自己推进记录：唯一放行点在判据为空那一支"
    )
    schedule = _js_function(session, "schedulePlaybackStep")
    assert "window.setTimeout(stepPlaybackWhenFree" in schedule, (
        "排下一步只排这一个唤醒体（暂停门在它的开头）"
    )

    # 读数：通用一份（页面根）、伤害侧一份（覆盖层）。两份都读 DOM，不开 DevTools 作用域。
    mark = _js_function(session, "markPlaybackHold")
    assert 'setAttribute("data-playback-hold"' in mark, (
        "排播侧把「为什么暂停」写进 `<html data-playback-hold>`（与 `?audiotest` 的 data-audio 同姿态）"
    )
    assert mark.count("setAttribute(") == 1, "暂停读数只有原因串一条（票 09 删掉了计数）"
    damage_mark = _js_function(damage_fx, "damageFxMark")
    assert 'setAttribute("data-damage-fx-hold"' in damage_mark, (
        "伤害侧在覆盖层上标「我在要求暂停」（实机 / 烟测一眼读）"
    )
    for name in ("flushPlayback", "finishPlayback"):
        assert "resetPlaybackHold()" in _js_function(session, name), (
            f"{name} 要连暂停状态与读数一起收掉（否则换局 / 冲刷后读数挂在页面上）"
        )

    # 缝的方向：session.js 不读伤害层的在播表、也不认档位；伤害侧只登记一条只读判据。
    assert "damagePlays" not in session, (
        "session.js 不许读伤害层的在播表：判据由登记表给（票 07 的通用口）"
    )
    assert "damageFxPlaybackHold" not in session, (
        "session.js 连伤害侧判据的名字都不该认识（按名直呼就等于写死了伤害这一个来源）"
    )
    assert "DAMAGE_FX_HOLD_MIN_VARIANT" not in session, (
        "档位阈值住伤害层；排播侧不许出现任何档位号"
    )
    hold = _js_function(damage_fx, "damageFxPlaybackHold")
    assert "damagePlays" in hold and "damageQueue" in hold, (
        "判据读在播表**与排队**（票 08 起重演出串行：队列没播完窗口就没结束）"
    )
    assert "damageFxVariantOf(" in hold, "档位换算读唯一的口（damageFxVariantOf）"
    assert "DAMAGE_FX_HOLD_MIN_VARIANT" in hold, "档位阈值读具名常量"
    assert re.search(r"^const DAMAGE_FX_HOLD_MIN_VARIANT = 3;$", damage_fx, re.M), (
        "3 / 4 档才要暂停（1 / 2 档 1.5 / 1.65s 窗口短，普通伤害不该被冻住）"
    )
    assert damage_fx.count('registerPlaybackHold("damage", damageFxPlaybackHold);') == 1, (
        "伤害侧只在文件末尾登记一条；同名重复登记会就地替换（页面里本文件只加载一遍）"
    )
    # 登记是**加载时**的一次动作，所以清单次序就是这条缝的前提（缺了要喊，不能悄悄失效）。
    srcs = _script_srcs()
    assert srcs.index("session.js") < srcs.index("damage-fx.js"), (
        "damage-fx.js 在加载时就调 registerPlaybackHold ⇒ session.js 必须排在它之前"
    )
    assert "没有排播侧的 registerPlaybackHold" in damage_fx, (
        "排播侧缺失时要喊一声（否则切了脚本次序 ⇒ 暂停悄悄失效，没人看见）"
    )
    assert "registerPlaybackHold" not in _strip_js_comments(
        _EFFECTS_JS.read_text(encoding="utf-8")
    ), "动效层不参与回放暂停（它没有要冻住记录流的重演出）"


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


def _function_definition_order(
    source: str, names: tuple[str, ...]
) -> tuple[list[str], list[str]]:
    """按**源码里的出现位置**返回这些顶层函数的真实次序，以及缺失的那些。

    `[name for name in 期望表 if re.search(...)]` 那种写法是**从期望表过滤**：把文件里的函数
    顺序重排它照样绿——`CODING_STANDARDS.md` §4「断次序的断言不许先把次序归一化」正是禁止这个。
    这里排序键取源码下标，比较的就是次序本身。
    """
    found: list[tuple[int, str]] = []
    missing: list[str] = []
    for name in names:
        at = source.find(f"function {name}(")
        if at < 0:
            missing.append(name)
        else:
            found.append((at, name))
    return [name for _at, name in sorted(found)], missing


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
    """票面 §1 / §4：队列进 `applyView`、触发点紧挨技能事件、口径 120ms / 按属性的 cap。

    票 09 起排播住 `damage-fx.js`：常量与 `startDamagePlay` / `finishDamagePlay` 从那里读，
    触发点与清场仍在 `session.js`（调用方）。

    票 02 起"同时在播上限 3 条"不是常量的全部故事：生效值按属性读 `anim.ini` 的并发表。
    这里只钉两件机械事实——**缺省常量仍是 3**、cap 在**调用时**才解析（键表 / 钳制 / 分属性
    归 `test_the_concurrency_cap_is_read_per_nature_from_anim_ini`）。
    """
    damage_fx = _DAMAGE_FX_JS.read_text(encoding="utf-8")
    effects = _EFFECTS_JS.read_text(encoding="utf-8")
    session = _SESSION_JS.read_text(encoding="utf-8")

    assert re.search(r"^const DAMAGE_FX_STAGGER_MS = 120;$", damage_fx, re.M), (
        "相邻开演错开 120ms（spec §演出口径）"
    )
    assert re.search(r"^const DAMAGE_FX_CONCURRENCY = 3;$", damage_fx, re.M), (
        "缺省 cap 仍是 3：并发表缺键 / 非数字时回退到它（生效值按属性读 anim.ini）"
    )
    assert "animTiming(" in _js_function(damage_fx, "damageFxCapFor"), (
        "cap 的读取入口唯一（damageFxCapFor）且**调用时**才解析 anim.ini"
    )
    assert re.search(r"^const DAMAGE_FX_CAP_MIN = 1;$", damage_fx, re.M), (
        "钳制范围 1–8：0 / 负数 → 1（票 02）"
    )
    assert re.search(r"^const DAMAGE_FX_CAP_MAX = 8;$", damage_fx, re.M), "> 8 → 8（票 02）"
    assert re.search(r'^const DAMAGE_FX_NATURE = "NORMAL";$', damage_fx, re.M), (
        "无属性的规范名（null / 缺字段也是它；家族表用它当 NORMAL 的键）"
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

    # 换局清场（票 09）：原来藏在 `effects.js:clearTransientEffects` 的最后两行——那是
    # 「动效 → 伤害」的反向钩子；现在由换局的调用方 `commitView` 显式说。
    # 结构断言读**剥掉注释**的源码：注释里提到旧调用点不算调用点。
    effects_code = _strip_js_comments(effects)
    clear = _js_function(effects_code, "clearTransientEffects")
    assert "clearDamageFx()" not in clear, (
        "动效层不许再调伤害层的清理（票 09 的反向钩子已挪到 session.js:commitView）"
    )
    assert "state.damageEvents" not in clear, "伤害队列不归动效层清"
    commit = _js_function(session, "commitView")
    assert "clearTransientEffects();" in commit, "换局先发动效层自己的清理"
    assert "clearDamageFx();" in commit, "换局必须连同伤害覆盖层一起摘掉（票 05 的硬约束）"
    assert "state.damageEvents = []" in commit, (
        "换局清空队列（照 state.skillEvents 的姿态）；顺序不敏感，但必须同一处"
    )

    # 版式重检（票 09）：由重绘的调用方说，且**在**动效层的座位补类之后（原来住在
    # `syncTransientEffects` 里）。
    sync = _js_function(effects_code, "syncTransientEffects")
    assert "damageDropStalePlays()" not in sync, (
        "动效层不许再调伤害层（票 09）：版式重检挪到 table.js:renderTable"
    )
    table = (STATIC_DIR / "table.js").read_text(encoding="utf-8")
    render = _js_function(table, "renderTable")
    assert "damageDropStalePlays();" in render, (
        "renderTable 重建座位后要按矩形判一次：版式变了就摘层（别演在半空）"
    )
    assert render.index("syncTransientEffects();") < render.index("damageDropStalePlays();"), (
        "版式重检排在座位补类之后（座位节点必须先建好，矩形才是新的）"
    )
    window_actions = _js_function(damage_fx, "startDamagePlay")
    assert "damageFxTiming(nature, plan.variant)" in window_actions, (
        "时长从 anim.ini 那一份搬过来（按属性取字段表），不在 JS 里硬编码"
    )
    assert "setTimeout(() => finishDamagePlay(play), play.duration)" in window_actions, (
        "摘层用 create() 返回的 duration 定时，不靠 rAF 自然停（票 04 的交接）"
    )
    assert "fx.draw(" not in _js_function(damage_fx, "finishDamagePlay"), (
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

  /* 票 06：命中后 1ms 那一帧。刀痕的长度 cutLen = max(3, floor(111 × k))、
   * k = eOut(age / 0.15) ⇒ 命中后约 1.8ms 内 cutLen 只有 3；命中白闪的 0.16s 窗口也从
   * 这一帧算起。两件事都靠这一帧读。包一层 try/catch：修前这里会抛 TypeError，要把那句
   * 错误当**读数**带回去，而不是让整份探针 exit 非零（那样红的信息只剩 stderr 一行）。 */
  const impactFrame = (opts) => {
    __calls.length = 0;
    try {
      const fx = SgsDamageFX.create(document.createElement("canvas"), opts);
      fx.draw(fx.timeline.IMPACT + 0.001);
      return { calls: __calls.slice(), error: null };
    } catch (error) {
      return { calls: [], error: String((error && error.message) || error) };
    }
  };
  /** 白色整屏填充（命中白闪 / 屏闪亮帧）：fillStyle 是 rgba(255,255,255,…) 或 #ffffff。 */
  const whiteFullScreen = (calls) => fullScreen(calls).filter((call) => {
    const fill = String(call.fill).toLowerCase();
    return /^rgba?\\(\\s*255\\s*,\\s*255\\s*,\\s*255/.test(fill) || fill === "#ffffff";
  });
  /** 只认命中白闪那一笔（postProcess 的 rgba(255,255,255,…)）；3 / 4 点档的灰阶屏闪
   *  亮帧是 #ffffff + globalAlpha，两套效果在读数上分开。 */
  const hitFlashOnly = (fills) => fills.filter(
    (call) => String(call.fill).toLowerCase().startsWith("rgba(255,255,255,"));
  const tableImpact = {};
  for (const id of [1, 2, 3, 4]) {
    const frame = impactFrame({ variant: id, profile: "game", dpr: 1, stage, bare: true });
    const white = whiteFullScreen(frame.calls);
    tableImpact[id] = { error: frame.error, white, hitFlash: hitFlashOnly(white) };
  }
  // 导出档（bare: false）：12 秒链的冻结面，1 点档的白闪照旧。
  const exportImpact = impactFrame({ variant: 1, dpr: 1 });
  const exportWhite = whiteFullScreen(exportImpact.calls);

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
  // 评审修复：无来源（source == null）的"从画面外飞入"形状——覆盖层把来源矩形整块挪到
  // 画面左外（cx = -dst.w / 2 - 200，与 effects.js:damageFxPlanFor 同形）。这里读解出来的
  // 航线起点 stage.a.x，看它是不是真的在画面外。
  const view660 = { width: 660, height: 371 };
  const onscreen = draw({
    variant: 1, profile: "game", timing: timing1, amount: 1, dpr: 1, bare: true,
    view: view660, stage: screenStage,
  }, 0.80);
  const offstage = draw({
    variant: 1, profile: "game", timing: timing1, amount: 1, dpr: 1, bare: true,
    view: view660,
    stage: {
      src: {
        cx: -screenStage.dst.w / 2 - 200, cy: screenStage.dst.cy,
        w: screenStage.dst.w, h: screenStage.dst.h,
      },
      dst: screenStage.dst,
    },
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
    hitFlash: {
      table: tableImpact,
      exportOne: {
        error: exportImpact.error, white: exportWhite, hitFlash: hitFlashOnly(exportWhite),
      },
    },
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
    onscreen: { stage: onscreen.fx.stage },
    offstage: { stage: offstage.fx.stage },
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


def test_the_sword_first_hit_flash_starts_at_two_damage(damage_fx_stage_probe: dict) -> None:
    """票 06 §1：牌桌档 1 点**没有**整屏白闪（2 点起才有）；导出档照旧。

    判据是命中后 1ms（白闪 0.16s 窗口的第一帧）那一帧的 `fillRect(0, 0, w, h)` + 白色
    填充：1 点档一次都不许有；2 点档必须有；`bare: false` 的导出档（12 秒链 / 静帧哈希
    是参考素材的冻结面）1 点档也照旧有。
    """
    probe = damage_fx_stage_probe["hitFlash"]
    table = probe["table"]
    assert table["1"]["white"] == [], (
        f"1 点档不许有整屏白闪（掉 1 点血整块牌桌亮一下过头了）：{table['1']['white']}"
    )
    assert table["2"]["hitFlash"], (
        f"2 点档的命中白闪必须还在（0.16s 内衰减）；这一帧读数：{table['2']}"
    )
    # 3 / 4 点档另有一套灰阶屏闪（亮帧也是整屏白），这里只认命中白闪那一笔：
    # 它不许跟着 1 点档一起被砍掉。
    for variant in ("3", "4"):
        assert table[variant]["hitFlash"], (
            f"{variant} 点档的命中白闪不许跟着 1 点档一起消失：{table[variant]}"
        )
    assert probe["exportOne"]["error"] is None, probe["exportOne"]["error"]
    assert probe["exportOne"]["hitFlash"], (
        f"导出档（bare: false）是冻结面：1 点档的白闪照旧，不许跟着牌桌档一起改："
        f"{probe['exportOne']}"
    )


def test_the_sword_scar_survives_the_growth_window(damage_fx_stage_probe: dict) -> None:
    """票 06 §2：命中后 ~2ms 的 `cutLen = 3` 窗口里，刀痕毛刺的下标不许越界。

    `cutLen = max(3, floor(111 × k))`、`k = eOut(age / 0.15)` ⇒ `cutLen = 3` 只在命中后
    约 1.8ms 内；而 `r = rng(seed × 7 + 13)` 与 `seed = 1700 + vId × 977` 都是确定的 ⇒
    四档在这一帧全崩（`Cannot read properties of undefined`），不是概率测试那种偶发。
    """
    table = damage_fx_stage_probe["hitFlash"]["table"]
    assert sorted(table) == ["1", "2", "3", "4"], sorted(table)
    for variant, frame in table.items():
        assert frame["error"] is None, (
            f"{variant} 点档在命中后 1ms 画一帧就崩了：{frame['error']}"
        )


# ---------------------------------------------------------------------------
# damage-fx 票 06：伤害音效 —— 解锁、对齐、静音缺省
#
# 票 08 把这套状态机从 `effects.js` 抽进 `sfx-ui.js`（定义 / 记忆 / 声道池 / `?audiotest`），
# `effects.js` 只留四个调用点。下面的用例按**搬走后的落点**读脚本：行为断言不动，
# 只把读取源从 `effects.js` 换成 `sfx-ui.js`（另有一条单向缝用例，见 §票 08）。
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
# damage-fx 票 08：音效 UI（开关 / 记忆 / 声道池 / ?audiotest）从 `effects.js` 搬进这份脚本。
_SFX_UI_JS = STATIC_DIR / "sfx-ui.js"


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
    """缺省静音 / 一次性手势解锁 / 开关记忆：三条都在代码结构上钉住。

    damage-fx 票 08 起这套状态机住 `sfx-ui.js`（原来是 `effects.js` 的尾巴）；`main.js` 那侧的
    接线不动——跨文件全局函数，调用时解析。

    票 02 补第四条结构口径：**声道池随 cap 增长**（起始 = 该属性 cap，槽位超出时就地补建），
    行为归烟测 `U-SFX-06`。
    """
    sfx_ui = _strip_js_comments(_SFX_UI_JS.read_text(encoding="utf-8"))
    main = _strip_js_comments(_MAIN_JS.read_text(encoding="utf-8"))

    assert re.search(r'^const DAMAGE_SFX_KEY = "sgs\.sfx";$', sfx_ui, re.M), (
        "持久化键必须是 localStorage['sgs.sfx']（票面 §做法 2）"
    )
    assert re.search(r'^const DAMAGE_SFX_OFF = "off";$', sfx_ui, re.M), "缺省值必须是 'off'"
    assert re.search(r'^const DAMAGE_SFX_ON = "on";$', sfx_ui, re.M), "开着记 'on'"

    remembered = _js_function(sfx_ui, "damageSfxRemembered")
    assert "=== DAMAGE_SFX_ON" in remembered, (
        "只有 'on' 算开；缺键 / 'off' / 别的值一律静音（缺省静音不许被杂值绕过）"
    )
    sync = _js_function(sfx_ui, "damageSfxSync")
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
    assert "damageSfxUnlock()" in unlock, "手势处理器同步交给 sfx-ui.js"
    assert "removeEventListener" in unlock, "一次性：跑过就把自己摘掉"
    assert "await" not in unlock and "setTimeout" not in unlock and "fetch" not in unlock, (
        "resume() 必须在手势的**同一个 tick**：不许经过 await / 计时器 / 回调"
    )

    unlock_fx = _js_function(sfx_ui, "damageSfxUnlock")
    assert "if (!damageSfxOn) return false;" in unlock_fx, (
        "开关关着时连 AudioContext 都不建（缺省静音是零代价的）"
    )
    ensure = _js_function(sfx_ui, "damageSfxEnsureLanes")
    assert "damageFxCapFor(" in ensure, (
        "池的起始长度 = 该属性的在播上限（票 02：cap 按属性读 anim.ini 的并发表）"
    )
    assert "player.start()" in ensure, "建声道的那一次就要 start()（同一个 tick 里 resume）"
    # 票 02 §4：池要能**按需增长**——槽号是跨家族的，本族的槽可能比本族 cap 大。
    assert re.search(r"^function damageSfxEnsureLanes\(engine, need\) \{", sfx_ui, re.M), (
        "damageSfxEnsureLanes 必须接受「至少要几条」（旧签名只按 cap 建一次就定长）"
    )
    assert re.search(r"while \(lanes\.length < (?:want|Math\.max)", ensure), (
        "按所需条数增长（cap 只是起始长度）"
    )
    start = _js_function(sfx_ui, "damageSfxStart")
    assert "damageSfxEnsureLanes(engine, slot + 1)" in start, (
        "槽位超出池长时**就地补建**（旧代码在这里 return null ⇒ 静默无声）"
    )
    assert "damageFxCapFor(" in ensure and "damageSfxOn" in start, (
        "开关关着时只复用已有声道，不凭空建 AudioContext"
    )
    set_enabled = _js_function(sfx_ui, "damageSfxSetEnabled")
    assert "if (damageSfxOn) damageSfxEnsureModules();" in set_enabled, (
        "点开「♪」本身就是手势：给页面上**已加载**的音效模块各建一套声道"
    )
    assert "damageSfxRemember(damageSfxOn)" in set_enabled, "开关状态写进 localStorage"
    assert "damageSfxSyncButton()" in set_enabled, "文案 / aria-pressed / .on 同处更新"


def test_the_damage_sfx_tick_shares_the_play_clock_and_the_export_chain_stays_silent() -> None:
    """`tick` 吃演出自己的 `seconds`（与 `fx.draw` 同一个变量）；导出 / 素材库链一声不响。

    票 08 起音效函数住 `sfx-ui.js`；票 09 起驱动点（`renderDamageFrame`）与两个收声点
    （`finishDamagePlay` / `clearDamageFx`）住 `damage-fx.js`——断言分两份读。
    """
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    sfx_ui = _strip_js_comments(_SFX_UI_JS.read_text(encoding="utf-8"))

    frame = _js_function_with_defaults(damage_fx, "renderDamageFrame")
    assert "play.fx.draw(seconds)" in frame, "画面用 `seconds`（一次性算好的演出秒数）"
    assert "damageSfxTick(play, seconds)" in frame, (
        "音效必须吃**同一个** `seconds`——另起墙钟会漂（票面 §做法 3）"
    )
    assert "Date.now" not in frame and "performance.now" not in frame, (
        "演出帧里不许再读墙钟"
    )

    tick = _js_function(sfx_ui, "damageSfxTick")
    assert "lane.player.tick(seconds, voice)" in tick, "tick 的 t 就是演出秒数"
    for wall_clock in ("Date.now", "stageNow", "performance.now", "requestAnimationFrame"):
        assert wall_clock not in tick, f"音效时钟里不许出现 {wall_clock}（不另起墙钟）"
    assert damage_fx.count("damageSfxTick(") == 1, (
        "tick 只有一个驱动点（damage-fx.js:renderDamageFrame 里那一次）；定义在 sfx-ui.js"
    )
    assert sfx_ui.count("damageSfxTick(") == 1, (
        "sfx-ui.js 里只有 damageSfxTick 的定义，不许有第二个驱动点"
    )

    voice = _js_function(sfx_ui, "damageSfxVoice")
    assert "play.fx && play.fx.timing" in voice, (
        "档位参数取 anim.ini 解算出来的毫秒表（票 04），不另写一份数字"
    )
    for field in ("flight", "strobe", "split"):
        assert f'timing.{field}' in voice, f"sfx.js 要的 {field} 必须从 timing 折算"

    start = _js_function(sfx_ui, "damageSfxStart")
    assert "damageSfxTimeline(play.fx && play.fx.timeline)" in start, (
        "cue 时刻取**这一档**的 fx.timeline（anim.ini 解算出来的那一份）"
    )
    assert "play.slot" in start, "一条并发演出一条声道（槽位唯一）"

    assert "damageSfxStop(play)" in _js_function(damage_fx, "finishDamagePlay"), (
        "摘层即收声"
    )
    assert "damageSfxStopAll()" in _js_function(damage_fx, "clearDamageFx"), (
        "换局 / 版式作废：在播的演出一起收声"
    )

    # 导出链（`renderAt(t)` / `?export=1` / 素材库）一声不响：fx-core 根本不认识 SgsSfx，
    # 牌桌也不提供 `renderAt` 这条路。
    fx_core = (STATIC_DIR / "fx-core.js").read_text(encoding="utf-8")
    assert "SgsSfx" not in fx_core, "画面那份脚本不许碰音效（导出链只驱动画面）"
    assert "renderAt" not in damage_fx, "排播不提供 renderAt（导出是素材库那条链的事）"

    audiotest = _js_function(sfx_ui, "damageSfxAudiotest")
    assert "renderOffline" in audiotest, "自检走 SgsSfx.renderOffline（纯函数式）"
    assert "gameTiming(" in audiotest, "四档各自的时间轴都渲染一遍"
    assert 'setAttribute("data-audio"' in audiotest, "电平写进 <html data-audio>"
    assert "unavailable" in audiotest, "没有 OfflineAudioContext ⇒ unavailable（不抛错）"
    requested = _js_function(sfx_ui, "damageSfxAudiotestRequested")
    assert "audiotest=1" in requested, "只认 ?audiotest=1"


# 票 08 的四个调用点（伤害排播 → 音效）：函数名不变，跨文件全局调用（调用时才解析）。
_SFX_UI_CALL_SITES = (
    "damageSfxStart(play)",
    "damageSfxStop(play)",
    "damageSfxTick(play, seconds)",
    "damageSfxStopAll()",
)

# 票 08 搬走的 20 个函数 + 票 01 的按模块分池（顺序与文件一致）。
_SFX_UI_FUNCTIONS = (
    "damageSfxStore",
    "damageSfxRemembered",
    "damageSfxRemember",
    "damageSfxButtonText",
    "damageSfxSyncButton",
    "damageSfxEngineFor",
    "damageSfxBaseTimeline",
    "damageSfxTimeline",
    "damageSfxEnsureLanes",
    "damageSfxEnsureModules",
    "damageSfxMute",
    "damageSfxSync",
    "damageSfxUnlock",
    "damageSfxSetEnabled",
    "damageSfxToggle",
    "damageSfxVoice",
    "damageSfxStart",
    "damageSfxTick",
    "damageSfxStop",
    "damageSfxStopAll",
    "damageSfxAudiotestRequested",
    "damageSfxAudiotest",
)


def test_the_sfx_ui_module_is_a_one_way_seam_out_of_the_damage_playback() -> None:
    """票 08：音效 UI 独立成一份脚本，缝只朝一个方向（伤害排播 → 音效）。

    - 22 个函数与四个调用点**分家**：定义在 `sfx-ui.js`；票 09 把调用点连同排播一起搬到
      `damage-fx.js`，`effects.js` 一侧一个都不剩；票 01 的声道池按**音效模块**分组
      （`damageSfxLanes[engine.key]`），池长读 `damageFxCapFor(nature)`；
    - 加载次序：`sfx.js` 之后、`damage-fx.js` 之后、`main.js` 之前（票面写「`sfx.js` 之后、
      `effects.js` 之前」，但 `effects.js` 原本就排在 `sfx.js` 之前，见 `index.html`；
      实际落点是音效组末尾——两个常量这时已就位，比「之前」更稳）；
    - 反方向**只许读常量**：`sfx-ui.js` 不读伤害排播层的在播表（`damagePlays`），
      也不出现伤害排播的函数名；`effects.js` 里不再留任何音效实现。
    """
    srcs = _script_srcs()
    assert "sfx-ui.js" in srcs, f"index.html 的脚本清单里必须有 sfx-ui.js：{srcs}"
    assert srcs.index("sfx.js") < srcs.index("sfx-ui.js"), "音效 UI 必须排在 sfx.js 之后"
    assert srcs.index("damage-fx.js") < srcs.index("sfx-ui.js") < srcs.index("main.js"), (
        "音效 UI 排在 damage-fx.js 之后、main.js 之前（它读 damage-fx.js 的两个常量）"
    )

    effects = _strip_js_comments(_EFFECTS_JS.read_text(encoding="utf-8"))
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    sfx_ui = _strip_js_comments(_SFX_UI_JS.read_text(encoding="utf-8"))

    # 次序按**源码下标**排，不从期望表过滤（CODING_STANDARDS §4）：表里写的"顺序与文件一致"
    # 必须真的被断到，否则重排文件也照样绿。
    moved, missing = _function_definition_order(sfx_ui, _SFX_UI_FUNCTIONS)
    assert not missing, f"这些音效函数必须住在 sfx-ui.js：缺 {sorted(missing)}"
    assert moved == list(_SFX_UI_FUNCTIONS), (
        "定义的**次序**必须与这张表一致（表就是文件里的出现次序）："
        f"实际 {moved}"
    )
    for owner, text in (("effects.js", effects), ("damage-fx.js", damage_fx)):
        left = [name for name in _SFX_UI_FUNCTIONS if re.search(rf"^function {name}\(", text, re.M)]
        assert not left, f"这些音效函数还留在 {owner}（票 08 要求搬走）：{left}"

    for site in _SFX_UI_CALL_SITES:
        assert site in damage_fx, (
            f"damage-fx.js 的调用点必须原样保留（只换定义位置）：{site}"
        )
        assert site not in effects, f"effects.js 里不该再有音效调用点（票 09 已搬走）：{site}"

    assert "damagePlays" not in sfx_ui, (
        "sfx-ui.js 不许读伤害排播层的在播表 damagePlays（换局清场收自己的 damageSfxBound）"
    )
    for damage_name in (
        "startDamagePlay",
        "finishDamagePlay",
        "pumpDamageQueue",
        "fireDamageEventsThrough",
        "damageDropStalePlays",
        "clearDamageFx",
    ):
        assert damage_name not in sfx_ui, (
            f"sfx-ui.js 不许调伤害排播层（{damage_name}）：缝只朝一个方向"
        )
    # 唯一的例外是**纯读**的 cap 口：池长必须与排播层同源（票 01 收进 `damageFxCapFor`，
    # 票 02 让它按属性读 anim.ini 的并发表）。它不是运行状态，也不反向驱动排播。
    assert "damageFxCapFor(" in sfx_ui, "声道池长读 damage-fx.js 的 cap 口（唯一真源）"
    assert "damageFxNatureKind(" in sfx_ui, "属性归类读 damage-fx.js 的唯一判据"
    # 只有这两个**纯读**助手可以跨过来：它们不接受 play、不读写排播状态，也不反向驱动。
    _PURE_READS = {"damageFxCapFor", "damageFxNatureKind"}
    for name in _DAMAGE_FX_FUNCTIONS:
        if name in _PURE_READS:
            continue
        assert name not in sfx_ui, f"sfx-ui.js 不许出现伤害层的其它函数（{name}）"
    # 池按模块分组：一个模块一套（`{SgsSfx: [...], SgsThunderSfx: [...]}`），
    # 不能再假设"一个数组装完三套"。
    assert "damageSfxLanes[engine.key]" in sfx_ui, "声道池必须按键 = 音效模块名"
    assert "damageSfxLanes.length" not in sfx_ui, "声道池不再是单个数组"
    ensure_modules = _js_function(sfx_ui, "damageSfxEnsureModules")
    assert "Object.keys(DAMAGE_SFX_ENGINES)" in ensure_modules, (
        "家族清单只有 DAMAGE_SFX_ENGINES 一张表（不再写第三份 NORMAL/THUNDER/FIRE 字面量）"
    )
    assert "if (!engine.sfx) continue;" in ensure_modules, (
        "只给**已加载**的模块建池（没有 SgsFireSfx 就跳过，不凭空建）"
    )
    assert 'THUNDER: { key: "SgsThunderSfx", fx: "SgsThunderFX" }' in sfx_ui, (
        "雷的音效模块与画面模块成对登记（表是唯一真源）"
    )
    ensure_lanes = _js_function(sfx_ui, "damageSfxEnsureLanes")
    assert "damageSfxBaseTimeline(engine.fx)" in ensure_lanes, (
        "占位时间轴按该属性的**画面模块**取（SgsThunderFX / SgsDamageFX）"
    )
    base = _js_function(sfx_ui, "damageSfxBaseTimeline")
    assert "gameTiming(1).timeline" in base, "占位时间轴 = 该画面模块 1 点档的 game 时间轴"
    for leaked in ("localStorage", "btn-sfx", "audiotest", "SgsSfx"):
        assert leaked not in effects, (
            f"effects.js 里不该再有音效实现（{leaked}）：开关 / 记忆 / 自检都搬去 sfx-ui.js"
        )


# ---------------------------------------------------------------------------
# damage-fx 票 09：伤害排播独立成 `damage-fx.js`（拆分第二步）
#
# 票 08 把音效搬走后 `effects.js` 还剩"伤害排播 + 座位几何 + 画布生命周期"（~590 行），
# 它与动效层**双向耦合**。本票先解耦、再搬文件：
#   - 两个反向钩子挪到调用方：`table.js:renderTable` → `damageDropStalePlays()`；
#     `session.js:commitView` → `clearDamageFx()`（+ 清 `state.damageEvents`）；
#   - 排播整体搬进 `damage-fx.js`，它只借 `effects.js` 三个"读真实布局"的小工具
#     （`rectOf` / `heroBlockFor` / `descendantWithClass`）与 `session.js:stageNow`。
# 下面这条用例钉的是**缝的方向**：搬走的清单、调用点留在哪、以及反向零调用。
# 行为（错开 / 上限 / 版式作废 / 换局无残影）归烟测 `U-DMG-01..06`。
# ---------------------------------------------------------------------------

# 票面 §3 的"剩余部分" + 票 01 的属性分派 / 着色闪：按 `damage-fx.js` 里的出现次序。
_DAMAGE_FX_FUNCTIONS = (
    "damageFxNatureKind",
    "damageFxFamilyFor",
    "damageFxRendererFor",
    "damageFxTimingFields",
    "damageFxTiming",
    "damageFxNaturePlayable",
    "damageFxCapFor",
    "damageFxVariantOf",
    "damageFxLayer",
    "damageFxDpr",
    "damageFxView",
    "damageFxBoxOf",
    "damageFxNumberKeepOut",
    "damageFxBoxKey",
    "damageFxPlanFor",
    "damageFxStageFor",
    "damageFxSlot",
    "damageFxMountCanvas",
    "damageFxMark",
    "damageFxFlashSpec",
    "damageFlashSync",
    "damageFlashPush",
    "damageFlashPop",
    "damageGrayscaleSync",
    "damageGrayscalePush",
    "damageGrayscalePop",
    "startDamagePlay",
    "damageDropPlayTimers",
    "finishDamagePlay",
    "damageClearCanvas",
    "renderDamageFrame",
    "damageRequestFrame",
    "damageStopFrame",
    "damageFxLiveOf",
    "damageFxPlaybackHold",
    "damageFxPendingOf",
    "damageFxHeavyLive",
    "pumpDamageQueue",
    "fireDamageEventsThrough",
    "damageDropStalePlays",
    "clearDamageFx",
)

# 反向不许出现的动效层入口（伤害层不认识动效层的任何内部结构）。
_EFFECTS_LAYER_ENTRIES = (
    "syncTransientEffects",
    "clearTransientEffects",
    "applyRecordEffects",
    "playSkillEvent",
    "drawTargetLines",
    "markActing",
    "flashHeroHp",
)


def test_the_damage_playback_module_is_a_one_way_seam_out_of_the_effects_layer() -> None:
    """票 09：排播独立成一份脚本，缝只朝一个方向（伤害 → 动效）。

    - 36 个函数与全部模块级状态都在 `damage-fx.js`，`effects.js` 里一个都不剩；
    - 加载次序：`effects.js` 之后（它借三个布局小工具）、`sfx-ui.js` 之前（音效读它的纯读口
      `DAMAGE_FX_MAX_VARIANT` / `damageFxCapFor` / `damageFxNatureKind`）、`main.js` 之前；
    - 反向**零调用**：`effects.js` 不出现伤害层的任何函数名 / 状态名；`damage-fx.js` 不出现
      动效层的入口名；
    - 两个反向钩子（票面 §2）确实住在调用方，而不是换了个文件名继续互调。
    """
    srcs = _script_srcs()
    assert "damage-fx.js" in srcs, f"index.html 的脚本清单里必须有 damage-fx.js：{srcs}"
    assert srcs.index("effects.js") < srcs.index("damage-fx.js") < srcs.index("main.js"), (
        "damage-fx.js 排在 effects.js 之后（依赖方向）、main.js 之前"
    )
    assert srcs.index("damage-fx.js") < srcs.index("sfx-ui.js"), (
        "sfx-ui.js 读 damage-fx.js 的纯读口（DAMAGE_FX_MAX_VARIANT / damageFxCapFor / "
        "damageFxNatureKind）"
    )

    effects = _strip_js_comments(_EFFECTS_JS.read_text(encoding="utf-8"))
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    table = _strip_js_comments((STATIC_DIR / "table.js").read_text(encoding="utf-8"))
    session = _strip_js_comments(_SESSION_JS.read_text(encoding="utf-8"))

    # 同 §4：次序按源码下标排，不从期望表过滤。
    moved, missing = _function_definition_order(damage_fx, _DAMAGE_FX_FUNCTIONS)
    assert not missing, f"这些排播函数必须住在 damage-fx.js：缺 {sorted(missing)}"
    assert moved == list(_DAMAGE_FX_FUNCTIONS), (
        "定义的**次序**必须与这张表一致（表就是文件里的出现次序）："
        f"实际 {moved}"
    )
    left = [
        name for name in _DAMAGE_FX_FUNCTIONS
        if re.search(rf"^function {name}\(", effects, re.M)
    ]
    assert not left, f"这些排播函数还留在 effects.js（票 09 要求搬走）：{left}"

    for constant in (
        "const DAMAGE_FX_STAGGER_MS = 120;",
        "const DAMAGE_FX_CONCURRENCY = 3;",
        'const DAMAGE_FX_NATURE = "NORMAL";',
        "const DAMAGE_FX_MAX_VARIANT = 4;",
        "let damageQueue = [];",
        "const damagePlays = [];",
    ):
        assert constant in damage_fx, f"模块级状态 / 口径常量必须跟着搬：{constant}"
        assert constant not in effects, f"effects.js 里不该再留：{constant}"

    # 反方向零调用：动效层不认识伤害层。
    for name in _DAMAGE_FX_FUNCTIONS:
        assert name not in effects, (
            f"effects.js 不许调伤害层（{name}）：缝只朝一个方向（票 09 的解耦）"
        )
    for state_name in ("damagePlays", "damageQueue", "damageGrayPlays", "state.damageEvents"):
        assert state_name not in effects, (
            f"effects.js 不许读伤害层的运行状态（{state_name}）"
        )
    # 伤害层也不许反过来认动效层的入口（它只许用 rectOf / heroBlockFor / descendantWithClass）。
    for name in _EFFECTS_LAYER_ENTRIES:
        assert name not in damage_fx, (
            f"damage-fx.js 不许调动效层（{name}）：缝只朝一个方向"
        )
    for helper in ("rectOf(", "heroBlockFor(", "descendantWithClass("):
        assert helper in damage_fx, f"伤害层只借这三个布局小工具（走 effects.js）：{helper}"

    # 票面 §2 的两个反向钩子：住在调用方，且调用点在重绘 / 换局那一侧。
    render = _js_function(table, "renderTable")
    assert "syncTransientEffects();" in render and "damageDropStalePlays();" in render, (
        "renderTable 里先补动效层的类、再让伤害层按矩形重检版式（两个都是显式的）"
    )
    commit = _js_function(session, "commitView")
    assert "clearTransientEffects();" in commit and "clearDamageFx();" in commit, (
        "换局时两个清理都由 commitView 显式调（谁的状态谁收，不再互调）"
    )


def test_the_sfx_mixer_table_is_on_top_with_six_separate_voices_and_no_audio_files() -> None:
    """调音台：`VOICE` 表在文件顶部单独一段，六个声音是六个独立函数；**音频文件只在白名单里**。

    chain-death-voices 票 03 改了后半条**口径**（用户给了 SGS OL 的卡牌语音素材）：

    - 伤害音效（`sfx.js` / `sfx-thunder.js` / `sfx-fire.js` / 铁索 / 阵亡）仍**全部现场合成**，
      一行音频文件都不读——六声音调音台那半因此**原样保留**；
    - 卡牌语音是**用户素材**（本仓新增的资产类）：mp3 只许落在
      `tools/sgs_web/static/voices/`，其余目录（`src/` / `tests/` / `docs/` / `开发文档/`
      照旧全扫）出现音频文件仍判红；
    - `voices/` 目录里的每条都被映射表引用、表里的每个文件名都有文件——那两条由
      `test_the_card_voice_table_covers_the_160_deck_and_the_assets_match_it` 钉。
    """
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
    whitelist = STATIC_DIR / "voices"
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
    outside = [path for path in found if whitelist not in path.parents]
    assert not outside, (
        "伤害音效仍**全部现场合成**：白名单之外不得出现音频文件"
        f"（只许 `static/voices/`，用户素材）：{[str(p) for p in outside[:5]]}"
    )
    assert found, "白名单里应当有卡牌语音素材（40 条）——本条不是把「有音频」也禁掉"
    for path in found:
        assert path.suffix.lower() == ".mp3", (
            f"卡牌语音素材只收 mp3（映射表与 MIME 都只认它）：{path}"
        )


def test_the_heavy_damage_plays_are_serialized_and_exclusive() -> None:
    """票 08：3 / 4 档（= 会暂停回放的那些）**串行且独占牌桌**，轻演出照旧错开 120ms。

    用户 2026-11-10 原话："我的演出彻底结束后（4 档 2000ms）才继续下一个事件，期间都是暂停的"。
    三件机械事实：闸门里有"重演出在播 ⇒ 一个都不开"；摘层处**续泵**（交接 = 同一 tick，不是
    再等一个错开）；档位换算只有一个口（开演 / 闸门 / 暂停判据读同一个函数）。
    行为归烟测 `U-DMG-15*`（两条 3 档雷串行）与 `U-DMG-03*`（轻演出照旧错开）。
    """
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))

    gate = _js_function(damage_fx, "pumpDamageQueue")
    assert "damageFxHeavyLive()" in gate, (
        "闸门先看「有没有重演出在播」：有就一个都不开（轻演出也不许抢跑）"
    )
    assert "if (heavy && damagePlays.length) break;" in gate, (
        "重演出独占牌桌：它在播时下一条排队等摘层"
    )
    assert "DAMAGE_FX_HOLD_MIN_VARIANT" in gate, "重演出的档位阈值读具名常量（与暂停判据同一个）"
    assert "DAMAGE_FX_STAGGER_MS" in gate, "轻演出仍按 120ms 错开（票 08 没动它们）"
    assert "heavy ? 0 : DAMAGE_FX_STAGGER_MS" in gate, (
        "重演出不排错开：它要等摘层，错开只会让下一段窗口白等"
    )

    finish = _js_function(damage_fx, "finishDamagePlay")
    assert "pumpDamageQueue();" in finish, (
        "摘层那一刻续泵——串行的交接点（否则队列里剩下的重演出永远不开演）"
    )

    hold = _js_function(damage_fx, "damageFxPlaybackHold")
    assert "damageQueue" in hold, "串行窗口内回放一直冻着：排队里的重演出也算"
    plan = _js_function(damage_fx, "damageFxPlanFor")
    assert "damageFxVariantOf(" in plan, "开演的档位读唯一换算口"
    assert "Math.min(DAMAGE_FX_MAX_VARIANT" not in plan, (
        "档位换算只剩 damageFxVariantOf 一处（plan 里不再手写第二份）"
    )
    variant_of = _js_function(damage_fx, "damageFxVariantOf")
    assert "DAMAGE_FX_MAX_VARIANT" in variant_of and "Math.round" in variant_of, (
        "damageFxVariantOf：取整 + 封顶 4"
    )
    assert damage_fx.count("DAMAGE_FX_MAX_VARIANT") >= 2, "封顶常量仍只有一处定义"


def test_the_damage_nature_check_treats_null_as_normal(damage_fx_probe: dict) -> None:
    """`nature: null` ≡ 无属性 ⇒ 必须播；票 01 / 03 起 `THUNDER`、`FIRE` 也播。

    三层文档同一句话：`game.py:note_damage`「`None` = 牌面没声明，等同无属性」、
    `projection.py:damage_events_after`「缺声明保持 `None`，与"无属性"同义」、
    `active.py:_quhu_burn`「无渠道牌、无属性」显式写 `"nature": None`。

    早先的 `event.nature !== DAMAGE_FX_NATURE` 把 `null` 当成"火焰 / 雷电"一起丢了——
    【驱虎】拼点那 1 点无属性伤害永远不演剑光。这里读的是**真 `damage-fx.js` 的判定函数**
    （探针按页面顺序加载 `anim.js` + `fx-core.js` + `fx-thunder.js` + `fx-fire.js` +
    `effects.js` + `damage-fx.js`），不是另抄一份。
    """
    playable = damage_fx_probe["naturePlayable"]
    assert playable["null"] is True, f"`null` 必须视同无属性（会播）：{playable}"
    assert playable["undefined"] is True, f"缺字段同样是无属性：{playable}"
    assert playable["NORMAL"] is True, f"显式 NORMAL 照旧播：{playable}"
    assert playable["THUNDER"] is True, f"雷电票 01 接进来了，必须播：{playable}"
    assert playable["FIRE"] is True, f"火焰票 03 接进来了，必须播：{playable}"
    assert playable["ICE"] is True, f"未登记属性 fail-open 回落无属性：{playable}"


def test_the_damage_fx_nature_dispatch_is_single_sourced(damage_fx_probe: dict) -> None:
    """票 01 / 03：属性 → 画面模块的查表只有 `damageFxRendererFor` 一处，未知属性 fail-open。

    顺带钉住字段表：剑 6 个 / 雷 6 个 / 火 6 个，各自由自己的 `DEFAULT_TIMING` 生成——
    `damage-fx.js` 里不许再写第二份名单（`damageFxTiming` 只按 `TIMING_PREFIX` + 字段名拼键）。
    """
    probe = damage_fx_probe
    renderer = probe["rendererFor"]
    assert renderer["thunder"] is True, "THUNDER 必须交给 SgsThunderFX"
    assert renderer["fire"] is True, "FIRE 必须交给 SgsFireFX（票 03 开闸）"
    assert renderer["normal"] is True and renderer["explicitNormal"] is True, (
        "null / NORMAL 都回 SgsDamageFX"
    )
    assert renderer["unknown"] is True, "未登记属性 fail-open 回落剑光（spec §2）"
    assert probe["thunderWithoutModule"] is True, (
        "雷电是已接家族：SgsThunderFX 缺件时**不**回落剑光（fail-open 只给未登记属性）"
    )
    assert probe["fireWithoutModule"] is True, (
        "火焰同理：SgsFireFX 缺件就不播，不把火杀演成无属性剑光"
    )

    fields = probe["fieldsFor"]
    assert fields["sword"] == ["total", "flight", "impact", "fade", "strobe", "split"]
    assert fields["thunder"] == ["total", "flight", "impact", "fade", "strobe", "flash"], (
        f"雷电的字段集由它自己的 DEFAULT_TIMING 推导：{fields['thunder']}"
    )
    assert fields["fire"] == [
        "total", "flight", "impact", "fade", "boom", "strobe", "flash",
    ], (
        f"火的字段集由它自己的 DEFAULT_TIMING 推导（不需要剑光的 split，但多一个 boom）："
        f"{fields['fire']}"
    )
    thunder = probe["timingThunder"]
    assert set(thunder["1"]) == {
        "damage_fx_thunder_1_total", "damage_fx_thunder_1_flight",
        "damage_fx_thunder_1_impact", "damage_fx_thunder_1_fade",
    }, f"1 点雷没有 strobe / flash，不该硬造：{sorted(thunder['1'])}"
    assert set(thunder["3"]) == {f"damage_fx_thunder_3_{field}" for field in fields["thunder"]}
    fire = probe["timingFire"]
    assert set(fire["1"]) == {
        "damage_fx_fire_1_total", "damage_fx_fire_1_flight",
        "damage_fx_fire_1_impact", "damage_fx_fire_1_fade",
    }, f"1 点火没有爆燃 / 屏闪，不该硬造 strobe / flash / boom：{sorted(fire['1'])}"
    assert set(fire["3"]) == {
        "damage_fx_fire_3_total", "damage_fx_fire_3_flight",
        "damage_fx_fire_3_impact", "damage_fx_fire_3_boom",
        "damage_fx_fire_3_strobe", "damage_fx_fire_3_flash",
    }, f"3 点火有爆燃 / 屏闪，但尾段是 boom 不是 fade：{sorted(fire['3'])}" 
    assert probe["cap"] == 3, "cap 缺省 3（生效值按属性读 anim.ini，见票 02）"


def test_the_server_damage_natures_are_the_ones_the_frontend_dispatches(
    damage_fx_probe: dict,
) -> None:
    """服务端的 `DamageNature` = 前端分派表认识的属性名（`null` 另算"无属性"）。

    `api/damage_events_after` 原样下发 `Engine.damage_events` 的 `nature`（`None` 保持
    `None`）；属性名的真源是 `src/sgs/effects/behaviors.py:DamageNature`。这条把两边对在
    一起：前端分派表每加一族，服务端枚举里必须真有这个值（反之枚举加值也逼前端跟上）。
    """
    behaviors = (REPO_ROOT / "src" / "sgs" / "effects" / "behaviors.py").read_text(encoding="utf-8")
    start = behaviors.find("class DamageNature(str, Enum):")
    assert start >= 0, "behaviors.py 里找不到 DamageNature 枚举"
    end = behaviors.find("\nclass ", start + 1)
    body = behaviors[start:] if end < 0 else behaviors[start:end]
    natures = set(re.findall(r'=\s*"([A-Z]+)"', body))
    assert natures == {"NORMAL", "FIRE", "THUNDER"}, f"服务端的伤害属性取值变了：{sorted(natures)}"

    # 每个枚举值都**调一次真函数**（不是 grep 源码）：家族归一、能不能播、画面模块、闪档。
    dispatch = damage_fx_probe["natureDispatch"]
    expected = {
        "NORMAL": {
            "kind": "NORMAL", "playable": True,
            "renderer": "SgsDamageFX", "flash": None,
        },
        "THUNDER": {
            "kind": "THUNDER", "playable": True,
            "renderer": "SgsThunderFX", "flash": "fx-flash-thunder",
        },
        "FIRE": {
            "kind": "FIRE", "playable": True,
            "renderer": "SgsFireFX", "flash": "fx-flash-fire",
        },
    }
    for nature in natures:
        assert nature in dispatch, f"前端分派表不认识服务端属性 {nature}"
        assert dispatch[nature] == expected[nature], (
            f"{nature} 的分派结果与期望不一致：{dispatch[nature]} != {expected[nature]}"
        )


def test_the_damage_fx_offstage_sword_path_starts_outside_the_screen(
    damage_fx_stage_probe: dict,
) -> None:
    """评审修复：`source == null` 的"从画面外飞入"补**几何断言**（不只有代码路径）。

    覆盖层在无来源时把来源矩形整块挪到画面左外（`cx = -dst.w / 2 - 200`，与
    `effects.js:damageFxPlanFor` 同形），fx-core 解出的航线起点 `stage.a.x` 必须 < 0；
    有来源时起点在画面内。烟测 `U-DMG-06` / `U-DMG-06b` 从**真实产出路径**读
    `#t-damage-fx` 的 `data-damage-fx-sword-x` 钉同一件事。
    """
    probe = damage_fx_stage_probe
    offstage = probe["offstage"]
    onstage = probe["onscreen"]
    assert offstage["stage"]["a"]["x"] < 0, (
        f"无来源 ⇒ 航线起点在画面外：a.x = {offstage['stage']['a']['x']}"
    )
    assert offstage["stage"]["src"]["cx"] < 0, (
        f"来源块的矩形整块在画面外：src.cx = {offstage['stage']['src']['cx']}"
    )
    assert onstage["stage"]["a"]["x"] > 0, (
        f"有来源 ⇒ 起点在画面内：a.x = {onstage['stage']['a']['x']}"
    )
    assert onstage["stage"]["a"]["x"] > offstage["stage"]["a"]["x"], "两者必须真的不同"


# ---------------------------------------------------------------------------
# damage-fx-natures 票 01：属性家族（第一族 = 雷电）
#
# 三件事分开钉：
# 1. **清单 / 静态契约**：两份新脚本进清单、按家族顺序、仍是严格模式普通脚本（上面两条
#    用例已按 `_DAMAGE_FX_GLOBALS` 覆盖），雷的键块住进 `anim.ini`；
# 2. **画面模块的形状**：`SgsThunderFX` 与 `SgsDamageFX` 同形（`create` / `gameTiming` /
#    `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS`），双时间轴、四条钳制、`bare` 只出
#    特效、无来源走「天降」；
# 3. **整屏着色闪**：`.fx-flash` 是着色绝对定位子层，**不是** `#table-shell` 的 `filter`。
#
# 行为（层出现 / 天降 / 混批 / 屏闪窗口 / 音效）归烟测 `U-DMG-02*` / `U-DMG-09*`。
# ---------------------------------------------------------------------------

_THUNDER_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 记录型 2D 上下文：雷的 `create()` / `draw()` 全走真代码，像素不参与断言。 */
function makeCtx() {
  const gradient = { addColorStop() {} };
  return new Proxy({}, {
    get(target, prop) {
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop in target) return target[prop];
      const fn = () => {};
      target[prop] = fn;
      return fn;
    },
    set(target, prop, value) { target[prop] = value; return true; },
  });
}

function makeCanvas() {
  const ctx = makeCtx();
  return { width: 0, height: 0, getContext: () => ctx };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout,
  document: {
    getElementById: () => null,
    createElement: (tag) => (tag === "canvas" ? makeCanvas() : {
      style: {}, setAttribute() {}, appendChild() {},
    }),
  },
});
context.globalThis = context;
context.window = context;

vm.runInContext(readFileSync(staticPath("fx-thunder.js"), "utf8"), context,
                { filename: "fx-thunder.js" });

const report = vm.runInContext(`(() => {
  const th = SgsThunderFX;
  const dst = { cx: 600, cy: 300, w: 240, h: 160 };
  const src = { cx: 200, cy: 300, w: 240, h: 160 };
  const make = (opts) => th.create(document.createElement("canvas"), opts);
  const game = (id, extra) => make(Object.assign({
    variant: id, profile: "game", amount: id, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src, dst }, numberKeepOut: 70,
  }, extra || {}));
  const shapes = {};
  const drawn = { sky: "ok", export: "ok" };
  for (const id of [1, 2, 3, 4]) {
    const fx = game(id);
    shapes[id] = {
      keys: Object.keys(fx).sort(),
      duration: fx.duration,
      profile: fx.profile,
      timing: fx.timing,
      timeline: fx.timeline,
      metrics: fx.metrics,
      stage: { sky: fx.stage.sky, a: fx.stage.a, b: fx.stage.b, u: fx.stage.u },
    };
    drawn[id] = "ok";
    try {
      for (const t of [0, 0.1, 0.3, 0.6, 1.0, 1.4, 1.7, 1.99]) fx.draw(t);
    } catch (error) { drawn[id] = String(error && error.stack || error); }
  }
  const sky = game(3, { stage: { src: null, dst } });
  try { for (const t of [0, 0.3, 0.9, 1.6]) sky.draw(t); } catch (error) { drawn.sky = String(error); }
  const onstage = game(1);
  const six = make({
    variant: 4, profile: "game", amount: 6, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src, dst },
  });
  const exported = make({ variant: 4, profile: "export", amount: 6, dpr: 1 });
  try {
    for (const t of [0, 0.5, 1.0, 2.0, 2.2, 3.0, 5.0]) exported.draw(t);
  } catch (error) { drawn.export = String(error && error.stack || error); }
  return JSON.stringify({
    fields: th.TIMING_FIELDS, keys: th.TIMING_KEYS, defaults: th.DEFAULT_TIMING,
    maxTotal: th.MAX_TOTAL_MS, prefix: th.TIMING_PREFIX,
    frozen: [Object.isFrozen(th.TIMING_FIELDS), Object.isFrozen(th.TIMING_KEYS)],
    shapes,
    sky: { sky: sky.stage.sky, a: sky.stage.a, src: sky.stage.src },
    onstage: { sky: onstage.stage.sky, a: onstage.stage.a, b: onstage.stage.b },
    six: { amount: six.amount, variant: six.variant, timeline: six.timeline },
    export: { duration: exported.duration, profile: exported.profile, timeline: exported.timeline },
    clamps: {
      total: th.gameTiming(1, { damage_fx_thunder_1_total: 9999 }).ms.total,
      impact: th.gameTiming(2, { damage_fx_thunder_2_total: 400, damage_fx_thunder_2_impact: 900 }).ms.impact,
      flight: th.gameTiming(3, { damage_fx_thunder_3_impact: 300, damage_fx_thunder_3_flight: 800 }).ms.flight,
      fade: th.gameTiming(4, { damage_fx_thunder_4_total: 1000, damage_fx_thunder_4_impact: 800, damage_fx_thunder_4_fade: 900 }).ms.fade,
      flash: th.gameTiming(3, { damage_fx_thunder_3_flash: 2 }).ms.flash,
      junk: th.gameTiming(2, { damage_fx_thunder_2_total: "abc", damage_fx_thunder_2_flight: -5 }).ms,
      missing: th.gameTiming(1, { damage_fx_thunder_1_total: 1600 }).ms,
    },
    drawn,
  });
})()`, context, { filename: "thunder_probe.js" });
process.stdout.write(report);
"""


def _thunder_probe() -> dict:
    """跑真 `fx-thunder.js`（node vm + 记录型 2D 上下文），拿回解算 / 实例 / 天降的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "thunder_probe.mjs"
        probe.write_text(_THUNDER_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"雷电探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def thunder_probe() -> dict:
    """一次探针跑完，四条用例共用。"""
    return _thunder_probe()


def test_the_thunder_family_scripts_are_listed_next_to_their_sword_twins() -> None:
    """票 01 §7：清单插两行——`fx-thunder.js` 紧跟 `fx-core.js`、`sfx-thunder.js` 紧跟
    `sfx.js`，两份都在 `sfx-ui.js` 之前（音效 UI 的声道池要读它们的 `gameTiming`）。"""
    srcs = _script_srcs()
    for name in ("fx-thunder.js", "sfx-thunder.js"):
        assert name in srcs, f"index.html 的脚本清单里必须有 {name}：{srcs}"
    assert srcs.index("fx-core.js") < srcs.index("fx-thunder.js") < srcs.index("sfx.js"), (
        "画面家族：fx-core.js → fx-thunder.js，都排在音效组之前"
    )
    assert srcs.index("sfx.js") < srcs.index("sfx-thunder.js") < srcs.index("sfx-ui.js"), (
        "音效家族：sfx.js → sfx-thunder.js → sfx-ui.js（音效 UI 最后）"
    )
    assert srcs.index("fx-thunder.js") < srcs.index("sfx-ui.js") < srcs.index("main.js")


def test_the_thunder_renderer_keeps_the_sword_renderer_shape(thunder_probe: dict) -> None:
    """`SgsThunderFX` 与 `SgsDamageFX` 同形：排播层不认第二种形状。

    - 五件导出（`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` /
      `TIMING_KEYS`）与剑光同名；字段名由 `DEFAULT_TIMING` 生成，`flash` 取代 `split`；
    - `create()` 返回 `draw / duration / timeline / timing / metrics.*`；
    - 每档：`duration = total`（秒）、`LAUNCH = impact − flight`、`IMPACT = impact`；
    - 牌桌档的飘字：字高 ≤ 35% 牌高、包围盒顶 ≥ 体力条下沿 + 2% 牌高（与剑光同一判据）。
    """
    probe = thunder_probe
    assert probe["prefix"] == "damage_fx_thunder_"
    assert probe["maxTotal"] == 2200
    assert probe["fields"] == ["total", "flight", "impact", "fade", "strobe", "flash"], (
        "雷电不需要剑光的 split（劈屏），但要有 flash（整屏着色闪的强度）"
    )
    assert probe["keys"] == list(probe["defaults"]), "TIMING_KEYS 必须就是 DEFAULT_TIMING 的键"
    assert probe["frozen"] == [True, True], "两个导出是冻结数组（读方只许读）"
    derived: list[str] = []
    for key in probe["defaults"]:
        field = key[len(probe["prefix"]):].split("_", 1)[1]
        if field not in derived:
            derived.append(field)
    assert list(probe["fields"]) == derived, (
        f"`TIMING_FIELDS` 必须由 `DEFAULT_TIMING` 的键生成：期望 {derived}，实际 {list(probe['fields'])}"
    )

    for variant in ("1", "2", "3", "4"):
        shape = probe["shapes"][variant]
        for key in (
            "draw", "duration", "timeline", "timing", "metrics", "stage",
            "variant", "profile", "amount", "bare", "width", "height",
        ):
            assert key in shape["keys"], f"{variant} 档的实例缺少 {key}：{shape['keys']}"
        timing = shape["timing"]
        timeline = shape["timeline"]
        assert timing["total"] <= probe["maxTotal"], (
            f"{variant} 档总时长必须 ≤ SgsFireFX.MAX_TOTAL_MS（{probe['maxTotal']}）"
        )
        assert shape["duration"] == pytest.approx(timing["total"] / 1000)
        assert timeline["DURATION"] == pytest.approx(shape["duration"])
        assert timeline["IMPACT"] == pytest.approx(timing["impact"] / 1000)
        assert timeline["LAUNCH"] == pytest.approx((timing["impact"] - timing["flight"]) / 1000)
        assert 0 < timeline["LAUNCH"] < timeline["IMPACT"] < timeline["DURATION"]
        assert timing["strobe"] >= 0
        assert 0 < timing["flash"] <= 1
        metrics = shape["metrics"]
        assert metrics["number"] > 0
        assert metrics["numberBox"]["height"] == pytest.approx(metrics["number"])
        assert metrics["number"] <= 0.35 * metrics["card"]["h"] + 0.05, (
            "字高不得超过牌高 35%（压住体力读数）"
        )
        assert metrics["numberBox"]["top"] >= metrics["keepOut"] + 0.02 * metrics["card"]["h"] - 0.05, (
            "飘字包围盒的顶边不得压到体力条（留 2% 牌高余量）"
        )
        assert probe["drawn"][variant] == "ok", f"{variant} 档逐帧绘制抛错：{probe['drawn'][variant]}"

    assert probe["shapes"]["1"]["timing"]["strobe"] == 0, "1 / 2 档没有整屏闪"
    assert probe["shapes"]["2"]["timing"]["strobe"] == 0
    assert probe["shapes"]["3"]["timing"]["strobe"] == 160
    assert probe["shapes"]["4"]["timing"]["strobe"] == 260
    # 档位只决定强度，飘字写真实点数（amount ≥ 5 仍播 4 档）。
    assert probe["six"]["variant"] == 4
    assert probe["six"]["amount"] == 6, "飘字数字来自服务端 amount，不是档位号"


def test_anim_ini_lists_the_thunder_durations_per_variant(thunder_probe: dict) -> None:
    """雷键块与 `SgsThunderFX.DEFAULT_TIMING` 逐键同值，且四条不变式成立。

    键表**不在测试里写第二份**：`SgsThunderFX.TIMING_KEYS` 是唯一真源，这里只与磁盘上的
    `anim.ini` 对表（同剑光票 10 的姿态）。
    """
    entries = {
        key: value for key, value in _ini_entries().items()
        if _THUNDER_TIMING_KEY.match(key)
    }
    expected = set(thunder_probe["keys"])
    assert set(entries) == expected, (
        f"雷的时长键必须与 fx-thunder 的 DEFAULT_TIMING 一致："
        f"缺 {sorted(expected - set(entries))}；多 {sorted(set(entries) - expected)}"
    )
    values = {key: float(value) for key, value in entries.items()}
    for variant in _DAMAGE_FX_VARIANTS:
        total = values[f"damage_fx_thunder_{variant}_total"]
        flight = values[f"damage_fx_thunder_{variant}_flight"]
        impact = values[f"damage_fx_thunder_{variant}_impact"]
        fade = values[f"damage_fx_thunder_{variant}_fade"]
        assert 0 < impact < total <= 2200, (
            f"{variant} 点雷：命中必须落在演出之内、总时长 ≤ 2200，实际 impact={impact} / total={total}"
        )
        assert 0 < flight < impact, f"{variant} 点雷：雷光必须在命中之前飞到"
        assert impact + fade <= total, (
            f"{variant} 点雷：墨痕必须在摘层之前散尽（impact + fade ≤ total），"
            f"实际 {impact} + {fade} > {total}"
        )
    totals = [values[f"damage_fx_thunder_{variant}_total"] for variant in _DAMAGE_FX_VARIANTS]
    assert len(set(totals)) == len(_DAMAGE_FX_VARIANTS), f"四档总时长必须各自独立可改：{totals}"
    assert values["damage_fx_thunder_3_strobe"] > 0
    assert values["damage_fx_thunder_4_strobe"] > values["damage_fx_thunder_3_strobe"], (
        "4 点雷的整屏闪比 3 点更长"
    )
    for variant in (3, 4):
        flash = values[f"damage_fx_thunder_{variant}_flash"]
        assert 0 < flash <= 1, f"{variant} 点雷的 flash 是 0..1 的强度（不是毫秒）：{flash}"
    assert {key: float(value) for key, value in entries.items()} == {
        key: float(value) for key, value in thunder_probe["defaults"].items()
    }, "内置默认必须与磁盘上的 anim.ini 同值（否则删一行 ini 会静默改变四档时长）"


def test_the_thunder_export_chain_keeps_the_material_package_timeline(
    thunder_probe: dict,
) -> None:
    """导出档（profile 缺省）照抄素材页的 5~6 秒链——node 探针逐帧导静帧靠它。

    牌桌档（game）只改**秒数**，不改画法：同一个 `draw(t)`，时间轴上的关键点由 `gameTiming`
    解出来。这条只钉导出档与四条钳制。
    """
    export = thunder_probe["export"]
    assert export["profile"] == "export"
    assert export["duration"] == pytest.approx(6.0), "4 档的素材页时长 6.0s"
    timeline = export["timeline"]
    assert timeline["DURATION"] == pytest.approx(6.0)
    assert timeline["LAUNCH"] == pytest.approx(0.84)
    assert timeline["IMPACT"] == pytest.approx(1.00)
    assert timeline["NUM_AT"] == pytest.approx(1.24)
    assert timeline["SKY_AT"] == pytest.approx(2.10)
    assert timeline["EXPLODE"] == pytest.approx(2.20)
    assert timeline["NUM2_AT"] == pytest.approx(2.48)
    assert timeline["STORM_IN"] == pytest.approx(1.04)
    assert timeline["STORM_OUT1"] == pytest.approx(5.80)
    assert thunder_probe["drawn"]["export"] == "ok"

    clamps = thunder_probe["clamps"]
    assert clamps["total"] == 2200, "单档总时长上限 2200（超过会挡住牌桌）"
    assert clamps["impact"] == 400, "impact ≤ total"
    assert clamps["flight"] == 300, "flight ≤ impact"
    assert clamps["fade"] == 200, "impact + fade ≤ total（否则层摘了墨痕还没散）"
    assert clamps["flash"] == 1, "flash 钳在 0..1"
    assert clamps["junk"]["total"] == 1500 and clamps["junk"]["flight"] == 115, (
        "非法值（非数字 / 负数）退回内置默认"
    )
    assert clamps["missing"] == {
        "total": 1600, "flight": 110, "impact": 300, "fade": 700,
        "strobe": 0, "flash": 0.6,
    }, "只改一个键，其余退回内置默认"


def test_the_thunder_sky_branch_starts_above_the_screen(thunder_probe: dict) -> None:
    """无来源（`src === null`）⇒「天降」：不画来源雷印，起笔锚在**画面上方**。

    与剑光的"从画面左侧外飞入"是两种形状：剑光的 `a.x < 0`（烟测 `U-DMG-06`），雷的
    `a.y < 0`（烟测 `U-DMG-09`）。默认 108 牌堆那 2 张【闪电】就是这个形状。
    """
    sky = thunder_probe["sky"]
    assert sky["sky"] is True, "无来源必须走天降分支（stage.sky）"
    assert sky["src"] is None, "天降不猜来源座位（src 保持 null）"
    assert sky["a"]["y"] < 0, f"雷从画面上方进入：a.y = {sky['a']['y']}"
    assert sky["a"]["x"] > 0, f"起笔落在目标牌正上方：a.x = {sky['a']['x']}"
    assert thunder_probe["drawn"]["sky"] == "ok"

    onstage = thunder_probe["onstage"]
    assert onstage["sky"] is False, "有来源时不走天降"
    assert onstage["a"]["y"] > 0 and onstage["b"]["y"] > 0, "两个锚点都在画面内"


# ---------------------------------------------------------------------------
# damage-fx-natures 票 03：属性家族（第二族 = 火焰）
#
# 与票 01 的雷电拆成同样的三块：
# 1. **清单 / 静态契约**：两份新脚本进清单、按家族顺序、仍是严格模式普通脚本（上面
#    `_DAMAGE_FX_GLOBALS` 那条用例已覆盖全局名与"不发请求 / 不碰 DOM"），火的键块进 `anim.ini`；
# 2. **画面模块的形状**：`SgsFireFX` 与 `SgsThunderFX` / `SgsDamageFX` 同形（`create` /
#    `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS`），双时间轴、四条钳制、
#    `bare` 只出特效、`src === null` 走"画面外进场"；
# 3. **整屏暖白闪**：`.fx-flash-fire`（票 01 已建的着色子层通道，本票只开火那一档）。
#
# 行为（层出现 / 混批 / 屏闪窗口 / 音效）归烟测 `U-DMG-17*` / `U-SFX-07*`。
# ---------------------------------------------------------------------------

_FIRE_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 记录型 2D 上下文：火的 `create()` / `draw()` 全走真代码，像素不参与断言；只把
 * `fillText` 的文本记下来——"飘字在正确的那一段画面里才出现"是行为判据，不是像素判据。 */
function makeCtx() {
  const gradient = { addColorStop() {} };
  const target = { texts: [] };
  return new Proxy(target, {
    get(obj, prop) {
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "fillText") return (text) => { obj.texts.push(String(text)); };
      if (prop in obj) return obj[prop];
      const fn = () => {};
      obj[prop] = fn;
      return fn;
    },
    set(obj, prop, value) { obj[prop] = value; return true; },
  });
}

function makeCanvas() {
  const ctx = makeCtx();
  return { width: 0, height: 0, getContext: () => ctx };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout,
  document: {
    getElementById: () => null,
    createElement: (tag) => (tag === "canvas" ? makeCanvas() : {
      style: {}, setAttribute() {}, appendChild() {},
    }),
  },
});
context.globalThis = context;
context.window = context;

vm.runInContext(readFileSync(staticPath("fx-fire.js"), "utf8"), context,
                { filename: "fx-fire.js" });

const report = vm.runInContext(`(() => {
  const fx = SgsFireFX;
  const dst = { cx: 600, cy: 300, w: 240, h: 160 };
  const src = { cx: 200, cy: 300, w: 240, h: 160 };
  const make = (opts) => fx.create(document.createElement("canvas"), opts);
  const game = (id, extra) => make(Object.assign({
    variant: id, profile: "game", amount: id, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src, dst }, numberKeepOut: 70,
  }, extra || {}));
  const shapes = {};
  const drawn = { export: "ok", offstage: "ok" };
  for (const id of [1, 2, 3, 4]) {
    const inst = game(id);
    shapes[id] = {
      keys: Object.keys(inst).sort(),
      duration: inst.duration,
      profile: inst.profile,
      timing: inst.timing,
      timeline: inst.timeline,
      metrics: inst.metrics,
      stage: { a: inst.stage.a, b: inst.stage.b, u: inst.stage.u, offstage: inst.stage.offstage },
    };
    drawn[id] = "ok";
    try {
      const dur = inst.duration;
      for (let step = 0; step <= 16; step += 1) inst.draw((dur * step) / 16);
    } catch (error) { drawn[id] = String(error && error.stack || error); }
  }
  const off = game(1, { stage: { src: null, dst } });
  try { for (const t of [0, 0.2, 0.6, 1.2]) off.draw(t); } catch (error) { drawn.offstage = String(error); }
  /* 3 点档：飘字只该在"最后一击 → 收尾"那一段出现（画面内部按素材页秒数画，靠 warp 对齐；
   * 单位一旦错位，飘字就会跑到爆炸之前——票 03 实机目视时真踩过这一条）。
   * 采样点从**时间轴自己**算（命中前 / 最后一击到飘字之间 / 飘字之后），不写死秒数：
   * 改 anim.ini 或排期比例时，这条断言不跟着红。 */
  const numCanvas = document.createElement("canvas");
  const numClock = fx.gameTiming(3);
  const threeT = numClock.timeline;
  const numFx = fx.create(numCanvas, {
    variant: 3, profile: "game", amount: 3, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src, dst }, numberKeepOut: 70,
  });
  const numCtx = numCanvas.getContext("2d");
  const marks = {
    beforeImpact: Math.max(0, threeT.IMPACT - Math.max(0.02, threeT.IMPACT * 0.4)),
    beforeNumber: (threeT.LAST_IMPACT + threeT.NUM_AT) / 2,
    afterNumber: (threeT.NUM_AT + threeT.DURATION) / 2,
  };
  const numberAt = {};
  for (const key of Object.keys(marks)) {
    numCtx.texts = [];
    numFx.draw(marks[key]);
    numberAt[key] = numCtx.texts.indexOf("-3") >= 0;
  }
  const six = make({
    variant: 4, profile: "game", amount: 6, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src, dst },
  });
  const exported = make({ variant: 4, profile: "export", amount: 6, dpr: 1 });
  try {
    for (const t of [0, 0.5, 1.0, 1.5, 2.2, 3.0, 5.0, 6.1]) exported.draw(t);
  } catch (error) { drawn.export = String(error && error.stack || error); }
  return JSON.stringify({
    fields: fx.TIMING_FIELDS, keys: fx.TIMING_KEYS, defaults: fx.DEFAULT_TIMING,
    maxTotal: fx.MAX_TOTAL_MS, prefix: fx.TIMING_PREFIX,
    frozen: [Object.isFrozen(fx.TIMING_FIELDS), Object.isFrozen(fx.TIMING_KEYS)],
    shapes,
    offstage: { stage: { a: off.stage.a, b: off.stage.b, offstage: off.stage.offstage }, drawn: drawn.offstage },
    six: { amount: six.amount, variant: six.variant, timeline: six.timeline },
    numberAt: numberAt,
    export: { duration: exported.duration, profile: exported.profile, timeline: exported.timeline },
    clamps: {
      total: fx.gameTiming(1, { damage_fx_fire_1_total: 9999 }).ms.total,
      impact: fx.gameTiming(2, { damage_fx_fire_2_total: 400, damage_fx_fire_2_impact: 900 }).ms.impact,
      flight: fx.gameTiming(3, { damage_fx_fire_3_impact: 300, damage_fx_fire_3_flight: 800 }).ms.flight,
      fade: fx.gameTiming(2, { damage_fx_fire_2_total: 1000, damage_fx_fire_2_impact: 800, damage_fx_fire_2_fade: 900 }).ms.fade,
      boom: fx.gameTiming(3, { damage_fx_fire_3_total: 3000, damage_fx_fire_3_impact: 300, damage_fx_fire_3_boom: 2000 }).ms.boom,
      boomClamped: fx.gameTiming(3, { damage_fx_fire_3_total: 1200, damage_fx_fire_3_impact: 300, damage_fx_fire_3_boom: 5000 }).ms.boom,
      flash: fx.gameTiming(3, { damage_fx_fire_3_flash: 2 }).ms.flash,
      junk: fx.gameTiming(2, { damage_fx_fire_2_total: "abc", damage_fx_fire_2_flight: -5 }).ms,
      missing: fx.gameTiming(1, { damage_fx_fire_1_total: 1600 }).ms,
    },
    drawn,
  });
})()`, context, { filename: "fire_probe.js" });
process.stdout.write(report);
"""


def _fire_probe() -> dict:
    """跑真 `fx-fire.js`（node vm + 记录型 2D 上下文），拿回解算 / 实例 / 画面外进场的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "fire_probe.mjs"
        probe.write_text(_FIRE_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"火焰探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def fire_probe() -> dict:
    """一次探针跑完，四条用例共用。"""
    return _fire_probe()


def test_the_fire_family_scripts_are_listed_next_to_their_thunder_twins() -> None:
    """票 03 §4：清单插两行，顺序 = 画面 `fx-core → fx-thunder → fx-fire`、音效
    `sfx → sfx-thunder → sfx-fire`，全部在 `sfx-ui.js` 之前（音效 UI 的声道池要读它们的
    `gameTiming`）。spec §硬约束把这条顺序逐名列出来了。"""
    srcs = _script_srcs()
    for name in ("fx-fire.js", "sfx-fire.js"):
        assert name in srcs, f"index.html 的脚本清单里必须有 {name}：{srcs}"
    order = [
        "fx-core.js", "fx-thunder.js", "fx-fire.js",
        "sfx.js", "sfx-thunder.js", "sfx-fire.js",
        "sfx-ui.js", "main.js",
    ]
    positions = [srcs.index(name) for name in order]
    assert positions == sorted(positions), (
        f"家族脚本必须按 {order} 的顺序加载，实际：{srcs}"
    )


def test_the_fire_renderer_keeps_the_sword_renderer_shape(fire_probe: dict) -> None:
    """`SgsFireFX` 与 `SgsDamageFX` / `SgsThunderFX` 同形：排播层不认第二种形状。

    - 五件导出（`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` /
      `TIMING_KEYS`）与剑、雷同名；字段名由 `DEFAULT_TIMING` 生成；
    - `create()` 返回 `draw / duration / timeline / timing / metrics.*`；
    - 每档：`duration = total`（秒）、`IMPACT = impact`、`LAUNCH = IMPACT − FLIGHT`，
      火羽串与爆炸的时刻按 spec 的三档 / 四档形状落在命中之后；
    - 牌桌档的飘字：字高 ≤ 35% 牌高、包围盒顶 ≥ 体力条下沿 + 2% 牌高（同剑 / 雷的判据）。
    """
    probe = fire_probe
    assert probe["prefix"] == "damage_fx_fire_"
    assert probe["maxTotal"] == 4500
    assert probe["fields"] == [
        "total", "flight", "impact", "fade", "boom", "strobe", "flash",
    ], (
        "火焰不需要剑光的 split（劈屏），但要有 flash（整屏着色闪的强度）与 boom"
        "（3 / 4 档爆炸 / 陨星那一段；1 / 2 档只有 fade）"
    )
    assert probe["keys"] == list(probe["defaults"]), "TIMING_KEYS 必须就是 DEFAULT_TIMING 的键"
    assert probe["frozen"] == [True, True], "两个导出是冻结数组（读方只许读）"
    derived: list[str] = []
    for key in probe["defaults"]:
        field = key[len(probe["prefix"]):].split("_", 1)[1]
        if field not in derived:
            derived.append(field)
    assert list(probe["fields"]) == derived, (
        f"`TIMING_FIELDS` 必须由 `DEFAULT_TIMING` 的键生成：期望 {derived}，实际 {list(probe['fields'])}"
    )

    # 箭数来自素材包 README §二 的四档表（独立于 `TIERS.count` 的第二处真源）。
    counts = {"1": 3, "2": 6, "3": 9, "4": 12}
    for variant in ("1", "2", "3", "4"):
        shape = probe["shapes"][variant]
        for key in (
            "draw", "duration", "timeline", "timing", "metrics", "stage",
            "variant", "profile", "amount", "bare", "width", "height",
        ):
            assert key in shape["keys"], f"{variant} 档的实例缺少 {key}：{shape['keys']}"
        timing = shape["timing"]
        timeline = shape["timeline"]
        assert timing["total"] <= probe["maxTotal"], (
            f"{variant} 档总时长必须 ≤ SgsFireFX.MAX_TOTAL_MS（{probe['maxTotal']}）"
        )
        assert shape["duration"] == pytest.approx(timing["total"] / 1000)
        assert timeline["DURATION"] == pytest.approx(shape["duration"])
        assert timeline["IMPACT"] == pytest.approx(timing["impact"] / 1000)
        assert timeline["LAUNCH"] == pytest.approx(timeline["IMPACT"] - timeline["FLIGHT"])
        assert 0 < timeline["LAUNCH"] < timeline["IMPACT"] < timeline["LAST_IMPACT"] < timeline["DURATION"], (
            f"{variant} 档的火羽串必须落在演出之内：{timeline}"
        )
        assert timeline["COUNT"] == counts[variant], "箭数来自四档表"
        assert timeline["GAP"] > 0, "相邻两支箭之间有间隔（不是同时落地）"
        assert timeline["NUM_AT"] > timeline["LAST_IMPACT"], "飘字在最后一击之后"
        assert timeline["NUM_AT"] < timeline["DURATION"]
        metrics = shape["metrics"]
        assert metrics["number"] > 0
        assert metrics["numberBox"]["height"] == pytest.approx(metrics["number"])
        assert metrics["number"] <= 0.35 * metrics["card"]["h"] + 0.05, (
            "字高不得超过牌高 35%（压住体力读数）"
        )
        assert metrics["numberBox"]["top"] >= metrics["keepOut"] + 0.02 * metrics["card"]["h"] - 0.05, (
            "飘字包围盒的顶边不得压到体力条（留 2% 牌高余量）"
        )
        assert probe["drawn"][variant] == "ok", f"{variant} 档逐帧绘制抛错：{probe['drawn'][variant]}"
        if variant in ("3", "4"):
            # 3 / 4 档是时停窗口：`boom` 就是爆炸 / 陨星那一段，必须真的占满这么长
            # （票 03 返工的直接判据——用户实机后嫌这一段太短）。
            assert timing["boom"] > 0, f"{variant} 档必须有 boom（爆炸 / 陨星段时长）"
            assert timing["fade"] == 0, f"{variant} 档的尾段由 boom 管，不再读 fade"
            tail_ms = (timeline["DURATION"] - timeline["LAST_IMPACT"]) * 1000
            assert tail_ms >= 0.9 * timing["boom"], (
                f"{variant} 档的爆炸 / 陨星段必须真的占满 boom："
                f"尾段 {tail_ms:.0f}ms / boom {timing['boom']}ms"
            )
            blast_ms = (timeline["DURATION"] - timeline["BLAST"]) * 1000
            assert blast_ms >= 1500, (
                f"{variant} 档的爆炸本身太短（返工后应 ≥1.5s）：{blast_ms:.0f}ms"
            )
        else:
            assert timing["boom"] == 0, f"{variant} 档没有爆炸段，不该有 boom"
            assert timing["fade"] > 0, f"{variant} 档的尾段是焦痕淡出（fade）"

    # 飘字只在"最后一击 → 收尾"那一段出现：画面按素材页秒数画、`draw()` 把牌桌秒数映射回去，
    # 单位一旦错位（把牌桌秒数写进 `numAt`）飘字就会跑到爆炸之前——这条从 public `draw()` 读。
    # 三个采样点由时间轴自己算（见探针）：命中前 / 最后一击到飘字之间 / 飘字之后。
    number = probe["numberAt"]
    assert number["beforeImpact"] is False, f"命中之前不许有飘字：{number}"
    assert number["beforeNumber"] is False, f"最后一击之后、飘字时刻之前仍然没有：{number}"
    assert number["afterNumber"] is True, f"飘字时刻之后到收尾之间必须出现：{number}"

    # 只有 3 / 4 档爆燃 / 天陨；1 / 2 档只有火羽与火星。
    for variant in ("1", "2"):
        assert probe["shapes"][variant]["timeline"]["BLAST"] == 0, "1 / 2 档没有爆炸"
        assert probe["shapes"][variant]["timeline"]["SKY_AT"] == 0, "1 / 2 档没有流星"
    three = probe["shapes"]["3"]["timeline"]
    assert three["BLAST"] > 0 and three["BLAST"] == pytest.approx(three["LAST_IMPACT"]), (
        "3 档：最后一支箭落地即爆燃"
    )
    assert three["EXPLODE"] == 0, "3 档没有流星"
    four = probe["shapes"]["4"]["timeline"]
    assert four["LAST_IMPACT"] < four["SKY_AT"] < four["BLAST"], (
        f"4 档：十二箭钉住 → 火云流星 → 天陨爆炸：{four}"
    )
    assert four["BLAST"] == pytest.approx(four["EXPLODE"]), "4 档的爆炸就是流星命中"
    assert probe["shapes"]["1"]["timing"]["strobe"] == 0, "1 / 2 档没有整屏闪"
    assert probe["shapes"]["2"]["timing"]["strobe"] == 0
    assert probe["shapes"]["3"]["timing"]["strobe"] == 120
    assert probe["shapes"]["4"]["timing"]["strobe"] == 160
    # 档位只决定强度，飘字写真实点数（amount ≥ 5 仍播 4 档）。
    assert probe["six"]["variant"] == 4
    assert probe["six"]["amount"] == 6, "飘字数字来自服务端 amount，不是档位号"


def test_anim_ini_lists_the_fire_durations_per_variant(fire_probe: dict) -> None:
    """火键块与 `SgsFireFX.DEFAULT_TIMING` 逐键同值，且两套不变式成立。

    键表**不在测试里写第二份**：`SgsFireFX.TIMING_KEYS` 是唯一真源，这里只与磁盘上的
    `anim.ini` 对表（同剑光票 10 / 雷电票 01 的姿态）。

    字段集是**分档**的（模块级的 `TIMING_FIELDS`）：1 / 2 档是轻演出（总时长 + 焦痕淡出
    `fade`），3 / 4 档是**时停演出**（总时长 + 爆炸 / 陨星段 `boom`）。票 03 返工的核心判据
    就在 3 / 4 档的 `boom` 与 `total` 上：尾段必须真的给足（用户实机后嫌 1.7 / 2.0s 太短）。
    """
    entries = {
        key: value for key, value in _ini_entries().items()
        if _FIRE_TIMING_KEY.match(key)
    }
    expected = set(fire_probe["keys"])
    assert set(entries) == expected, (
        f"火的时长键必须与 fx-fire 的 DEFAULT_TIMING 一致："
        f"缺 {sorted(expected - set(entries))}；多 {sorted(set(entries) - expected)}"
    )
    values = {key: float(value) for key, value in entries.items()}
    max_total = fire_probe["maxTotal"]
    for variant in _DAMAGE_FX_VARIANTS:
        total = values[f"damage_fx_fire_{variant}_total"]
        flight = values[f"damage_fx_fire_{variant}_flight"]
        impact = values[f"damage_fx_fire_{variant}_impact"]
        assert 0 < impact < total <= max_total, (
            f"{variant} 点火：命中必须落在演出之内、总时长 ≤ {max_total}，"
            f"实际 impact={impact} / total={total}"
        )
        assert 0 < flight < impact, f"{variant} 点火：火羽必须在命中之前飞到"
        if variant in (3, 4):
            boom = values[f"damage_fx_fire_{variant}_boom"]
            assert boom >= 1500, (
                f"{variant} 点火：爆炸 / 陨星段（boom）必须给足（返工下限 1500ms），实际 {boom}"
            )
            assert impact + boom <= total, (
                f"{variant} 点火：爆炸段必须在摘层之前结束（impact + boom ≤ total），"
                f"实际 {impact} + {boom} > {total}"
            )
            assert f"damage_fx_fire_{variant}_fade" not in values, (
                f"{variant} 点火由 boom 管尾段，不该再留一个 fade 键（两处真源）"
            )
        else:
            fade = values[f"damage_fx_fire_{variant}_fade"]
            assert fade > 0, f"{variant} 点火：焦痕总得有个淡出窗口"
            # 上界不在这里判：给得比剩下的时间还长时由**读者侧**钳到 `total − impact`，
            # 有效的钳制由 `test_the_fire_export_chain…` 的 `clamps["fade"]` 钉。
            assert f"damage_fx_fire_{variant}_boom" not in values, (
                f"{variant} 点火没有爆炸段，不该有 boom 键"
            )
    totals = [values[f"damage_fx_fire_{variant}_total"] for variant in _DAMAGE_FX_VARIANTS]
    assert len(set(totals)) == len(_DAMAGE_FX_VARIANTS), (
        f"四档总时长必须各自独立可改（一条公式绑死的话四个值会相等）：{totals}"
    )
    # 这四个数是 spec §演出口径 的缺省（票 03 返工后 3 / 4 档按用户要求加长：时停窗口里的
    # 箭串 + 爆炸 / 陨星段）。改 ini 等于改用户可见的节奏，可以；换掉缺省值先改 spec。
    assert totals == [2000, 2200, 2600, 3600], (
        f"spec §游戏档缺省时长（返工后按用户实机定稿的值）：{totals}"
    )
    assert values["damage_fx_fire_3_strobe"] > 0
    assert values["damage_fx_fire_4_strobe"] > values["damage_fx_fire_3_strobe"], (
        "4 点火的整屏闪比 3 点更长"
    )
    for variant in (3, 4):
        flash = values[f"damage_fx_fire_{variant}_flash"]
        assert 0 < flash <= 1, f"{variant} 点火的 flash 是 0..1 的强度（不是毫秒）：{flash}"
    assert values == {
        key: float(value) for key, value in fire_probe["defaults"].items()
    }, "内置默认必须与磁盘上的 anim.ini 同值（否则删一行 ini 会静默改变四档时长）"


def test_the_fire_export_chain_keeps_the_material_package_timeline(fire_probe: dict) -> None:
    """导出档（profile 缺省）照抄素材页的 4.9~6.2 秒链——node 探针逐帧导静帧靠它。

    牌桌档（game）只换**节拍**：`gameTiming` 把同一批关键点（第一箭命中 / 最后一击 /
    爆炸 / 飘字 / 收尾）压到 `anim.ini` 的秒数上，画法一行不改。这条钉导出档与四条钳制。
    """
    export = fire_probe["export"]
    assert export["profile"] == "export"
    assert export["duration"] == pytest.approx(6.2), "4 档的素材页时长 6.2s"
    timeline = export["timeline"]
    assert timeline["DURATION"] == pytest.approx(6.2)
    assert timeline["LAUNCH"] == pytest.approx(0.58)
    assert timeline["IMPACT"] == pytest.approx(0.85)
    assert timeline["LAST_IMPACT"] == pytest.approx(1.51)
    assert timeline["COUNT"] == 12
    assert timeline["GAP"] == pytest.approx(0.06)
    assert timeline["SKY_AT"] == pytest.approx(1.70)
    assert timeline["BLAST"] == pytest.approx(2.14)
    assert timeline["EXPLODE"] == pytest.approx(2.14)
    assert timeline["NUM_AT"] == pytest.approx(2.52)
    assert fire_probe["drawn"]["export"] == "ok"

    clamps = fire_probe["clamps"]
    assert clamps["total"] == 4500, (
        "单档总时长上限 4500（火比雷宽：3 / 4 档是时停窗口里的两段戏；仍有上限）"
    )
    assert clamps["impact"] == 400, "impact ≤ total"
    assert clamps["flight"] == 300, "flight ≤ impact"
    assert clamps["fade"] == 200, "1 / 2 档：impact + fade ≤ total（否则层摘了焦痕还没散）"
    assert clamps["boom"] == 2000, "3 / 4 档：boom 装得下就照给定值放行"
    assert 0 < clamps["boomClamped"] < 1200, (
        f"boom 给过头时被钳到「total − 最后一击」之内：{clamps['boomClamped']}"
    )
    assert clamps["flash"] == 1, "flash 钳在 0..1"
    assert clamps["junk"]["total"] == 2200 and clamps["junk"]["flight"] == 220, (
        "非法值（非数字 / 负数）退回内置默认"
    )
    assert clamps["missing"] == {
        # 只改 total：其余字段退回内置默认；1 档的 fade（1500）被钳到剩下的 1300。
        "total": 1600, "flight": 240, "impact": 300, "fade": 1300,
        "boom": 0, "strobe": 0, "flash": 0.6,
    }, "只改一个键，其余退回内置默认（fade 按剩下的时间钳）"


def test_the_fire_without_source_starts_outside_the_screen(fire_probe: dict) -> None:
    """`source == null` 的火 ⇒ 沿用剑光先例：来源整块挪到画面左外（不猜来源，也不做"天降"）。

    票面 §1：库里三个火焰生产者（火杀 / 火攻 / 业炎）都带来源，`source: null` 的火焰不存在
    ——这条路径只为"真遇到 null"不抛错；几何判据与剑光一致（起笔 `a.x < 0`、落点仍在牌上）。
    牌桌的真实入口由 `damage-fx.js:damageFxPlanFor` 合成那块画面外矩形（`U-DMG-06` 同一条）。
    """
    offstage = fire_probe["offstage"]
    stage = offstage["stage"]
    assert stage["offstage"] is True, "无来源必须被认出来（stage.offstage）"
    assert stage["a"]["x"] < 0, f"火羽从画面左外射入：a.x = {stage['a']['x']}"
    assert stage["b"]["x"] > 0 and stage["b"]["y"] > 0, "落点仍在目标牌上"
    assert offstage["drawn"] == "ok", f"无来源那一分支逐帧绘制抛错：{offstage['drawn']}"


_FIRE_SFX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 假 Web Audio：node 没有 AudioContext，而 `SgsFireSfx` 的排程代码必须整条走一遍。
 * 每个节点只有 connect / start / stop 与参数 setter；顺手数一数**建了多少个音源**——
 * 四档的 cue 表差别很大（箭数 + 爆燃 + 天陨），节点数能把"排程真的按档展开"读出来。 */
function makeParam() {
  const p = {
    value: 0,
    setValueAtTime(v) { p.value = v; return p; },
    exponentialRampToValueAtTime(v) { p.value = v; return p; },
    linearRampToValueAtTime(v) { p.value = v; return p; },
    setTargetAtTime(v) { p.value = v; return p; },
  };
  return p;
}

function makeNode(kind, graph) {
  const node = {
    kind,
    connect() { return node; },
    disconnect() {},
    start() { graph.started += 1; },
    stop() {},
  };
  if (kind === "gain") node.gain = makeParam();
  if (kind === "oscillator") { node.frequency = makeParam(); node.type = "sine"; }
  if (kind === "bufferSource") { node.buffer = null; node.loop = false; }
  if (kind === "filter") { node.frequency = makeParam(); node.Q = makeParam(); node.type = "lowpass"; }
  if (kind === "compressor") {
    node.threshold = makeParam(); node.knee = makeParam(); node.ratio = makeParam();
    node.attack = makeParam(); node.release = makeParam();
  }
  if (kind === "panner") node.pan = makeParam();
  return node;
}

function makeGraph() {
  const graph = { nodes: 0, started: 0 };
  const ctx = {
    sampleRate: 44100,
    currentTime: 0,
    state: "running",
    destination: { connect() {} },
    resume() { return Promise.resolve(); },
    createGain() { graph.nodes += 1; return makeNode("gain", graph); },
    createOscillator() { graph.nodes += 1; return makeNode("oscillator", graph); },
    createBufferSource() { graph.nodes += 1; return makeNode("bufferSource", graph); },
    createBiquadFilter() { graph.nodes += 1; return makeNode("filter", graph); },
    createDynamicsCompressor() { graph.nodes += 1; return makeNode("compressor", graph); },
    createStereoPanner() { graph.nodes += 1; return makeNode("panner", graph); },
    createBuffer(channels, length) {
      const data = new Float32Array(Math.max(1, length));
      return {
        numberOfChannels: channels, length: data.length, sampleRate: 44100,
        getChannelData: () => data,
      };
    },
  };
  return { ctx, graph };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout, Math, Array, Float32Array, Object, JSON,
  document: { createElement: () => ({ width: 0, height: 0, getContext: () => null }) },
});
context.globalThis = context;
context.window = context;
/* 假图的构造器在外面写，但要在沙箱里调（vm 的脚本看不到外层变量）。 */
context.makeGraph = makeGraph;
vm.runInContext(readFileSync(staticPath("fx-fire.js"), "utf8"), context, { filename: "fx-fire.js" });
vm.runInContext(readFileSync(staticPath("sfx-fire.js"), "utf8"), context, { filename: "sfx-fire.js" });

const report = vm.runInContext(`(() => {
  const fx = window.SgsFireFX;
  const sfx = window.SgsFireSfx;
  const out = {};
  /* 模块级的 AudioContext 图只建一次（GRAPH 是缓存的），故四档共用一张假图、
   * 按每个档开始前的节点数取**增量**。 */
  const made = makeGraph();
  window.AudioContext = function AudioContext() { return made.ctx; };
  for (const id of [1, 2, 3, 4]) {
    const before = { nodes: made.graph.nodes, sources: made.graph.started };
    const clock = fx.gameTiming(id);
    const timeline = Object.assign({}, clock.timeline);
    const lane = sfx.create(timeline);
    let error = null;
    let started = false;
    try {
      started = lane.start();
      const frames = 24;
      for (let step = 1; step <= frames; step += 1) {
        lane.tick((clock.timeline.DURATION * step) / frames, { dmg: id });
      }
      lane.mute(true);
      lane.mute(false);
      lane.stop();
    } catch (e) { error = String(e && e.stack || e); }
    out[id] = {
      started, error,
      nodes: made.graph.nodes - before.nodes,
      sources: made.graph.started - before.sources,
      clock: {
        count: clock.timeline.COUNT,
        blast: clock.timeline.BLAST,
        explode: clock.timeline.EXPLODE,
        duration: clock.timeline.DURATION,
      },
    };
  }
  return JSON.stringify(out);
})()`, context, { filename: "fire_sfx_probe.js" });
process.stdout.write(report);
"""


def _fire_sfx_probe() -> dict:
    """跑真 `sfx-fire.js` 的排程（node vm + 假 Web Audio），四档各走一整段 tick。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "fire_sfx_probe.mjs"
        probe.write_text(_FIRE_SFX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"火焰音效探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


def test_the_fire_sfx_lane_schedules_every_tier_without_a_real_audio_context() -> None:
    """`SgsFireSfx` 的排程代码整条可跑：四档都能建图、排完、收声，且档位越高音源越多。

    垫片里没有 Web Audio，烟测的 `U-SFX-07` 用的是**桩模块**（只验接线）；这条补上
    **真模块**这一侧：真 `create` / `start` / `tick` / `stop` 在假图（记录型
    `AudioContext`）上跑一遍，抓 11 个音色函数里的拼写 / 参数错误。声音本身（听感）仍只能
    在浏览器里听——`renderOffline` 接进 `?audiotest=1` 不在本票（见票 03 §已知缺口）。
    """
    probe = _fire_sfx_probe()
    for variant in ("1", "2", "3", "4"):
        tier = probe[variant]
        assert tier["error"] is None, f"{variant} 档排程抛错：{tier['error']}"
        assert tier["started"] is True, f"{variant} 档 start() 建出了共享图"
        assert tier["nodes"] > 0 and tier["sources"] > 0, f"{variant} 档一个音源都没排出来"
    assert probe["4"]["sources"] > probe["1"]["sources"], (
        "4 档（十二箭 + 火云 + 天陨）排出的音源必须明显多于 1 档（三箭）："
        f"{probe['4']['sources']} vs {probe['1']['sources']}"
    )
    assert probe["3"]["clock"]["blast"] > 0 and probe["3"]["clock"]["explode"] == 0, (
        "3 档：排出了爆燃、没有流星"
    )
    assert probe["4"]["clock"]["explode"] > probe["4"]["clock"]["blast"] - 1e-9, (
        "4 档：流星的命中就是爆炸时刻"
    )

def test_the_attribute_flash_is_a_colored_sublayer_not_a_table_shell_filter() -> None:
    """spec §整屏效果：火 / 雷的整屏闪走**着色子层**，不是 `#table-shell` 的 `filter`。

    理由（既有注记）：`filter` 会在表壳上建 containing block，把两个 `position: fixed` 的
    子孙（`#t-anim-note` z 9999 / `#t-card-preview` z 55）重新锚定。子层必须：
    绝对定位铺满、不吃指针、`mix-blend-mode: screen`、层级在覆盖画布（19）之上、卡牌预览
    （55）之下；窗口时长复用该档 `strobe`，强度读 `flash`。
    """
    damage_fx = _DAMAGE_FX_JS.read_text(encoding="utf-8")
    sync = _js_function(damage_fx, "damageFlashSync")
    assert 'shell.appendChild(damageFlashNode)' in sync, (
        "着色子层必须挂进 #table-shell（`filter` 之外的合法做法）"
    )
    assert "filter" not in sync, "属性闪不许碰表壳的 filter"
    assert "document.createElement(\"div\")" in sync, "子层是一个普通 div"
    assert "aria-hidden" in sync, "纯视觉层不进无障碍树"
    assert "damageFxFlashSpec" in damage_fx
    spec = _js_function(damage_fx, "damageFxFlashSpec")
    assert "damageFxFamilyFor(nature).flash" in spec, (
        "着色档从家族表取（唯一真源），不在函数里再写一份 if"
    )
    assert "fx-flash-thunder" in damage_fx and "fx-flash-fire" in damage_fx, (
        "两族各有一个颜色档（雷 = 冷白、火 = 暖白）"
    )
    # 无属性那条链仍然是**唯一**写 `#table-shell` filter 的地方。
    assert damage_fx.count("shell.style.filter") == 1, (
        "表壳的 filter 只许灰阶窗口写（属性闪走子层）"
    )
    grayscale = _js_function(damage_fx, "damageGrayscaleSync")
    assert "DAMAGE_FX_GRAYSCALE" in grayscale
    start = _js_function(damage_fx, "startDamagePlay")
    assert "damageGrayscalePush" in start and "damageFlashPush" in start, (
        "两种窗口互斥：家族表挑灰阶 / 着色闪的推入函数（同一段计时器）"
    )
    assert "family.grayscale" in start, "互斥判据读家族表（不是再写一条 if）"
    assert "Number.isFinite(flashValue)" in start and "0.6" in start, (
        "强度读该档 flash（缺省 0.6；0 是合法的『不闪』，不许被 || 吃掉）"
    )
    # 闪的**时机**也住在家族表：剑 / 雷对准命中，火对准爆炸（3 档爆燃 / 4 档天陨）——
    # 用户 2026-11-11 实机反馈：火在箭串中途闪会让人以为伤害已经结算完了。
    assert 'screenAt: "BLAST"' in damage_fx, "火的整屏闪必须对准 BLAST（爆炸），不是 IMPACT"
    assert 'screenAt: "IMPACT"' in damage_fx, "剑 / 雷的窗口仍从命中开始（既有口径不动）"
    assert "family.screenAt" in start, "窗口起点读家族表（时机的唯一真源）"
    assert "screenMs + strobeMs" in start, "窗口长度仍是该档 strobe（起点换了、长度没换）"

    rule = _css_rule(".fx-flash")
    assert re.search(r"position:\s*absolute", rule), f"子层必须绝对定位：{rule}"
    assert re.search(r"inset:\s*0\b", rule), f"子层必须铺满表壳：{rule}"
    assert re.search(r"pointer-events:\s*none", rule), f"子层不能吃指针：{rule}"
    assert re.search(r"mix-blend-mode:\s*screen", rule), f"子层要透出牌桌：{rule}"
    assert _layer_z_index(".fx-flash") > _layer_z_index(".damage-fx-layer"), (
        "子层在覆盖画布之上"
    )
    assert _layer_z_index(".fx-flash") < _layer_z_index(".card-preview"), (
        "子层在卡牌预览（55）之下"
    )
    assert _css_rule(".fx-flash-thunder"), "缺冷白档的 CSS"
    assert _css_rule(".fx-flash-fire"), "缺暖白档的 CSS"
    assert "rgb(226, 240, 255)" in _css_rule(".fx-flash-thunder"), "雷 = 冷白"
    assert "rgb(255, 243, 224)" in _css_rule(".fx-flash-fire"), "火 = 暖白"


# ---------------------------------------------------------------------------
# damage-fx-natures 票 04：真会话夹具（闪电 → 连环传导；Python 产出 / JS 消费）
#
# 夹具是**证据**，不是可以随手改的输入：由 `tools/gen_damage_fixture.py` 跑真引擎产出。
# 本文件只钉"夹具 = 今天的引擎产出"这一件事：夹具存在 / 是合法 JSON（`records` 与
# `damage_events` 两键）+ 重跑生成器**逐字节**相等。剧本形状（同批多条 / 无来源 / 雷电 /
# 事件锚点 / `hp_change`）归生成器自己的自检，消费侧（层出现 / 串行 / 时停）归烟测
# `U-DMG-18*`——两边都不在这里抄第二份。
#
# 重录只走 `--update`（默认只打印，不写盘）。
# ---------------------------------------------------------------------------

THUNDER_FIXTURE = REPO_ROOT / "tests" / "support" / "fixtures" / "thunder-conduction.json"


def test_the_thunder_conduction_fixture_is_what_todays_engine_produces() -> None:
    """夹具存在、是合法 JSON（`records` + `damage_events` 两键），且重跑生成器**逐字节**相等。

    夹具是"今天的引擎会产出什么"的冻结证据：引擎行为（判定区间 / 传导名单 / 事件字段）变了
    就这里红——先判断是"行为真的变了"（照票面改判据）还是"夹具该重录"
    （`tools/gen_damage_fixture.py --update`）。剧本形状（3 条无来源雷电 / 一目标一帧 / 事件
    锚点落在窗口里 / 三条 `hp_change`）由生成器自己的自检钉住，不在这里抄第二份。
    """
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_damage_fixture.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    committed = THUNDER_FIXTURE.read_bytes()
    payload = json.loads(committed)
    assert isinstance(payload.get("records"), list) and payload["records"], "夹具缺 records"
    assert isinstance(payload.get("damage_events"), list), "夹具缺 damage_events"
    assert completed.stdout == committed, (
        "重跑生成器与提交的夹具不一致：先判断是引擎行为变了（那就照着改判据 / 票面），"
        "还是夹具该重录（tools/gen_damage_fixture.py --update）"
    )


# ---------------------------------------------------------------------------
# chain-death-voices 票 01：铁索连环（常态铁索贴图 + link / break 演出，**无时停**）
#
# 机械的四半：
# 1. **脚本清单 / 严格脚本 / 顺序 / 全局名**：`fx-chain.js` 跟 `fx-fire.js`、`sfx-chain.js` 跟
#    `sfx-fire.js`、两份都在 `sfx-ui.js` 之前；全局名 `SgsChainFX` / `SgsChainSfx`；不发请求、
#    不自己取 DOM（纯画布 / 声音生成器）。
# 2. **`SgsChainFX` 的形状与双时间轴**：`create` / `bakeStatic` / `MODES` / `STATIC` /
#    `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS` / `MAX_TOTAL_MS` 都在；
#    字段名由 `DEFAULT_TIMING` **生成**；`anim.ini` 的两个键与它逐键同值；钳制 800–2400。
# 3. **`.chain-line` 被整体替换成 `.chain-mark`**（虚线 ⇒ 铁索贴图画布）：全仓静态脚本里
#    不再有 `.chain-line`；`#t-self-chain` 是常驻 canvas 且 `hidden` 协议不变；CSS 四条
#    （absolute / 外扩 / pointer-events / z-index 4）+ 入链期间的抑制规则。
# 4. **排播钩子**：`stepPlayback` 里四条演出钩子的真实次序是
#    **伤害 → 卡牌语音 → 铁索 → 阵亡**（跨票次序由
#    `::test_the_playback_hooks_fire_in_damage_card_chain_death_order` 一次钉住）；
#    `renderTable` 末尾 `chainFxSync()`；
#    `commitView` 换会话时 `clearChainFx()`；`state.chainSeq` 游标 / 120ms 错开 / 200ms 同座位
#    / 在播上限 8；**两个文件里都没有 `registerPlaybackHold`**（无时停）。
#
# 行为（入链 / 解锁 / 重播保护 / 同批两条 / 版式作废 / 音效）归烟测 `U-CHAIN-01..`。
# ---------------------------------------------------------------------------

_FX_CHAIN_JS = STATIC_DIR / "fx-chain.js"
_SFX_CHAIN_JS = STATIC_DIR / "sfx-chain.js"

# chain-death-voices 票 01：本 effort 新加的两个全局名（票 02 / 03 会往这张表继续加）。
# 票 02 追加阵亡的两份（`SgsDeathFX` / `SgsDeathSfx`）；票 03 追加卡牌语音（`SgsCardVoice`）。
_FX_EXTRA_GLOBALS = {
    "fx-chain.js": "SgsChainFX",
    "sfx-chain.js": "SgsChainSfx",
    "fx-death.js": "SgsDeathFX",
    "sfx-death.js": "SgsDeathSfx",
    "sfx-voices.js": "SgsCardVoice",
}


def test_the_fx_extra_globals_are_listed_in_order_and_stay_static() -> None:
    """新演出家族的脚本：清单里有、顺序对、导全局名、不发请求、不自己取 DOM。"""
    srcs = _script_srcs()
    for name, global_name in _FX_EXTRA_GLOBALS.items():
        assert name in srcs, f"index.html 必须加载 {name}：{srcs}"
        assert (STATIC_DIR / name).is_file(), f"static/ 里缺少 {name}"
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        # 首条语句仍是 `"use strict";`（总清单用例也扫，这里就近再说一次）。
        assert _LEADING_COMMENT.sub("", text, count=1).startswith('"use strict";'), (
            f"{name} 的首条语句必须是 \"use strict\";"
        )
        assert re.search(rf"global\.{global_name}\s*=\s*\{{", text), (
            f"{name} 必须把入口挂在 window.{global_name}（与素材包 / 伤害家族同一姿态）"
        )
        assert not re.search(r"\b(?:fetch|XMLHttpRequest)\s*\(", text), (
            f"{name} 是纯静态脚本：不发请求"
        )
        assert not re.search(r"\bdocument\.(?:querySelector|getElementById)\b", text), (
            f"{name} 不接管 DOM（覆盖层与挂载是排播层的事）"
        )
    # 顺序：画面跟 fx-fire.js、音效跟 sfx-fire.js，两份都在 sfx-ui.js 之前。
    assert srcs.index("fx-fire.js") < srcs.index("fx-chain.js"), (
        f"fx-chain.js 必须排在 fx-fire.js 之后：{srcs}"
    )
    assert srcs.index("sfx-fire.js") < srcs.index("sfx-chain.js"), (
        f"sfx-chain.js 必须排在 sfx-fire.js 之后：{srcs}"
    )
    assert srcs.index("fx-chain.js") < srcs.index("sfx-ui.js") < srcs.index("main.js"), (
        "fx-chain.js 要在 sfx-ui.js 之前（音效读画面模块的时间表）"
    )
    assert srcs.index("sfx-chain.js") < srcs.index("sfx-ui.js"), (
        "sfx-chain.js 要在 sfx-ui.js 之前（后者按名惰性建声道）"
    )
    assert srcs.index("fx-chain.js") < srcs.index("sfx-chain.js"), (
        "画面在前、音效在后（音效的 cue 时刻从画面模块的 MODES 读）"
    )
    # 票 02：阵亡的画面紧挨 fx-chain（都在 sfx.js 之前）、音效紧挨 sfx-chain。
    assert srcs.index("fx-chain.js") < srcs.index("fx-death.js") < srcs.index("sfx.js"), (
        f"fx-death.js 必须紧跟 fx-chain.js、排在 sfx.js 之前：{srcs}"
    )
    assert srcs.index("sfx-chain.js") < srcs.index("sfx-death.js") < srcs.index("sfx-ui.js"), (
        f"sfx-death.js 必须紧跟 sfx-chain.js、排在 sfx-ui.js 之前：{srcs}"
    )
    assert srcs.index("fx-death.js") < srcs.index("sfx-death.js"), (
        "画面在前、音效在后（阵亡的音效报点从 SgsDeathFX.PARAMS 读）"
    )
    # 票 03：卡牌语音紧跟 sfx-death.js、排在 sfx-ui.js 之前（后者在同一手势 / 开关里
    # 把 unlock / mute 单向灌进来）。
    assert srcs.index("sfx-death.js") < srcs.index("sfx-voices.js") < srcs.index("sfx-ui.js"), (
        f"sfx-voices.js 必须紧跟 sfx-death.js、排在 sfx-ui.js 之前：{srcs}"
    )


def test_the_chain_fx_family_never_registers_a_playback_hold() -> None:
    """用户口径：铁索的入链 / 解锁**都没有时停**（与素材包 / 阵亡那套建议相反）。

    逐文件扫 `registerPlaybackHold`：出现即红。这条与伤害家族（3 / 4 档才登记一条）是
    **两条相反的口径**，所以必须各钉各的——不能靠"登记表里只有伤害"这种间接事实。
    """
    for name in ("fx-chain.js", "sfx-chain.js"):
        text = _strip_js_comments((STATIC_DIR / name).read_text(encoding="utf-8"))
        assert "registerPlaybackHold" not in text, (
            f"{name} 不许登记回放暂停：铁索演出无时停（spec §硬约束）"
        )
        assert "playbackHold" not in text, f"{name} 连暂停判据的名字都不该认识"
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    assert 'registerPlaybackHold("damage"' in damage_fx, (
        "反向对照：伤害家族仍只登记自己那一条（本条不是把登记表整个禁掉）"
    )


_CHAIN_FX_PROBE = r"""
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
for (const name of ["anim.js", "fx-chain.js"]) {
  vm.runInContext(readFileSync(`tools/sgs_web/static/${name}`, "utf8"), context, { filename: name });
}

const report = await vm.runInContext(
  `(async () => {
     await animReady;
     const FX = SgsChainFX;
     const g = (mode, raw) => FX.gameTiming(mode, raw || {});
     return JSON.stringify({
       note: $("t-anim-note").textContent,
       animConfigKeys: Object.keys(animConfig),
       chainFromIni: {
         link: animTiming("chain_fx_link_total"),
         break: animTiming("chain_fx_break_total"),
       },
       shape: {
         create: typeof FX.create,
         bakeStatic: typeof FX.bakeStatic,
         gameTiming: typeof FX.gameTiming,
         cues: typeof FX.cues,
         modes: Object.keys(FX.MODES || {}),
         static: FX.STATIC,
         geom: typeof FX.GEOM,
         colors: typeof FX.COLORS,
       },
       durations: { link: FX.MODES.link.duration, break: FX.MODES.break.duration },
       defaults: FX.DEFAULT_TIMING,
       timingFields: FX.TIMING_FIELDS,
       timingKeys: FX.TIMING_KEYS,
       frozen: [Object.isFrozen(FX.TIMING_FIELDS), Object.isFrozen(FX.TIMING_KEYS)],
       prefix: FX.TIMING_PREFIX,
       maxTotal: FX.MAX_TOTAL_MS,
       minTotal: FX.MIN_TOTAL_MS,
       resolved: {
         link: g("link").total,
         break: g("break").total,
         linkSeconds: g("link").seconds,
         breakSeconds: g("break").seconds,
         fromIni: g("link", { chain_fx_link_total: animTiming("chain_fx_link_total") }).total,
       },
       clamps: {
         low: g("link", { chain_fx_link_total: 100 }).total,
         high: g("break", { chain_fx_break_total: 9000 }).total,
         junk: g("link", { chain_fx_link_total: "abc" }).total,
         missing: g("break", {}).total,
       },
     });
   })()`,
  context,
  { filename: "chain_fx_probe.js" },
);
console.log(report);
"""


def _chain_fx_probe() -> dict:
    """跑真 `anim.js` + 真 `fx-chain.js`（node vm），拿回形状 / 时间轴 / 钳制的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "chain_fx_probe.mjs"
        probe.write_text(_CHAIN_FX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"铁索探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def chain_fx_probe() -> dict:
    """一次探针跑完，形状 / 时间轴 / 钳制几条用例共用。"""
    return _chain_fx_probe()


_CHAIN_MODES = ("link", "break")


def test_the_chain_fx_module_shape_and_its_two_timing_keys(chain_fx_probe: dict) -> None:
    """`SgsChainFX` 的形状、双时间轴的单一真源与钳制（spec §演出口径·牌桌档缺省）。

    行为的一半在 node 探针里驱动真函数（`gameTiming` 的钳制 / 缺键 / 垃圾值），另一半与磁盘上的
    `anim.ini` 对表。字段名**不是**写在测试里的第二份清单：期望值来自 `DEFAULT_TIMING`。
    """
    probe = chain_fx_probe
    shape = probe["shape"]
    for key in ("create", "bakeStatic", "gameTiming", "cues"):
        assert shape[key] == "function", f"SgsChainFX 缺 {key}()"
    assert set(_CHAIN_MODES) <= set(shape["modes"]), f"MODES 缺 link / break：{shape['modes']}"
    assert "hold" in shape["modes"], (
        "hold = 常态（入链末帧）那一档：`bakeStatic` 与它共用 frameHold，不是另一套画法"
    )
    assert shape["geom"] == "object" and shape["colors"] == "object", "GEOM / COLORS 要导出"
    static = shape["static"]
    for field in ("overL", "overR", "entry", "deg", "cardRatio"):
        assert isinstance(static.get(field), (int, float)), f"STATIC 缺 {field}：{static}"
    assert abs(float(static["overL"]) - 0.105) < 1e-9, f"左沿外扩 10.5%：{static}"
    assert abs(float(static["overR"]) - 0.092) < 1e-9, f"右沿外扩 9.2%：{static}"
    assert probe["durations"] == {"link": 1.36, "break": 1.24}, (
        f"素材页基准时长：{probe['durations']}"
    )

    # `DEFAULT_TIMING`：每模式一张子表，键就是 anim.ini 的字段名。
    defaults = probe["defaults"]
    assert set(defaults) == set(_CHAIN_MODES), f"DEFAULT_TIMING 的模式：{sorted(defaults)}"
    assert defaults["link"] == {"total": 1360}, f"link 缺省 1360ms：{defaults['link']}"
    assert defaults["break"] == {"total": 1240}, f"break 缺省 1240ms：{defaults['break']}"
    # 字段名由 `DEFAULT_TIMING` 生成（票面：字段名只有一处真源）。**次序就是契约**：
    # `TIMING_FIELDS` / `TIMING_KEYS` 是冻结**数组**、消费方按它迭代，故这里直接比序列，
    # 不先 `sorted()`（`CODING_STANDARDS.md` §4：断次序的断言不许先把次序归一化）。
    from_defaults = list(
        dict.fromkeys(field for mode in defaults.values() for field in mode)
    )
    assert list(probe["timingFields"]) == from_defaults, (
        "TIMING_FIELDS 必须按 DEFAULT_TIMING 的模式 / 字段声明次序生成（不是集合相等）："
        f"{probe['timingFields']} vs {from_defaults}"
    )
    expected_keys = [
        f"{probe['prefix']}{mode}_{field}"
        for mode, fields in defaults.items()
        for field in fields
    ]
    assert list(probe["timingKeys"]) == expected_keys, (
        "TIMING_KEYS 必须按前缀 + 模式 + 字段的声明次序生成（不是集合相等）："
        f"{probe['timingKeys']} vs {expected_keys}"
    )
    assert probe["frozen"] == [True, True], "TIMING_FIELDS / TIMING_KEYS 冻结（别被就地改写）"
    assert probe["prefix"] == "chain_fx_", f"anim.ini 前缀：{probe['prefix']}"
    assert probe["maxTotal"] == 2400 and probe["minTotal"] == 800, (
        f"钳制 800–2400ms：min={probe['minTotal']} max={probe['maxTotal']}"
    )

    # 磁盘上的 `anim.ini` 与 `DEFAULT_TIMING` **逐键同值**（用户改一份文件调时长）。
    ini = {
        key: float(value)
        for key, value in _ini_entries().items()
        if key.startswith("chain_fx_")
    }
    assert set(ini) == set(probe["timingKeys"]), (
        f"anim.ini 的键必须与 TIMING_KEYS 一致：缺 {sorted(set(probe['timingKeys']) - set(ini))}；"
        f"多 {sorted(set(ini) - set(probe['timingKeys']))}"
    )
    for mode in _CHAIN_MODES:
        key = f"chain_fx_{mode}_total"
        assert ini[key] == defaults[mode]["total"], (
            f"{key} 必须与 DEFAULT_TIMING 同值：ini={ini[key]} default={defaults[mode]['total']}"
        )
    # 新键真的进了 animConfig，且没被当成未知键删掉 / 报错。
    anim_js = _ANIM_JS.read_text(encoding="utf-8")
    prefixes = re.search(r"^const ANIM_FOREIGN_KEY_PREFIXES = \[(?P<body>[^\]]*)\];$", anim_js, re.M)
    assert prefixes is not None, "anim.js 里找不到 ANIM_FOREIGN_KEY_PREFIXES"
    assert '"chain_fx_"' in prefixes.group("body"), (
        f"anim.js 必须把 chain_fx_ 登记成外来键（否则用户的改动被当未知键删掉）：{prefixes.group('body')}"
    )
    assert "未知键" not in probe["note"], f"chain_fx_* 不得被当成未知键（页面提示：{probe['note']}）"
    for key in probe["timingKeys"]:
        assert key in probe["animConfigKeys"], f"anim.js 没把 {key} 读进 animConfig"
    for mode in _CHAIN_MODES:
        assert probe["chainFromIni"][mode] == defaults[mode]["total"], (
            f"animTiming(chain_fx_{mode}_total) 应等于缺省值：{probe['chainFromIni']}"
        )

    # `gameTiming`：缺省 = DEFAULT_TIMING，ini 值活着，越界被夹（800–2400），垃圾值退默认。
    resolved = probe["resolved"]
    assert resolved["link"] == 1360 and resolved["break"] == 1240, f"缺省解算：{resolved}"
    assert abs(resolved["linkSeconds"] - 1.36) < 1e-9 and abs(resolved["breakSeconds"] - 1.24) < 1e-9
    assert resolved["fromIni"] == 1360, "带真 ini 值的解算也要落到 1360"
    clamps = probe["clamps"]
    assert clamps["low"] == 800, f"低于 800ms 被夹到 800：{clamps['low']}"
    assert clamps["high"] == 2400, f"高于 2400ms 被夹到 2400：{clamps['high']}"
    assert clamps["junk"] == 1360, f"非法值退回 DEFAULT_TIMING：{clamps['junk']}"
    assert clamps["missing"] == 1240, f"缺键退回 DEFAULT_TIMING：{clamps['missing']}"


_CHAIN_MARK_Z = 4


def test_the_chain_mark_replaces_the_dashed_line_everywhere() -> None:
    """`.chain-line` **整体替换**成 `.chain-mark`（spec §硬约束）：不是"再画一条铁索"。

    三处一起换：`seat-view.js:chainMarkNode()`（对手座位）、`index.html` 的常驻
    `#t-self-chain`（自己的座位）、`style.css` 的两条虚线规则。也钉住**保留**的那几样：
    `.chained` 状态类、`#t-self-chain` 的常驻节点 + `hidden` 协议、`title="横置（连环状态）"`、
    z-index ≥ 3（铁索画在阵亡蒙灰之上）。
    """
    stray = []
    for path in sorted(STATIC_DIR.glob("*")):
        if path.suffix not in {".js", ".css", ".html", ".ini"}:
            continue
        if ".chain-line" in path.read_text(encoding="utf-8"):
            stray.append(path.name)
    assert not stray, f".chain-line 必须整体替换成 .chain-mark，这些文件里还有：{stray}"

    seat_view = (STATIC_DIR / "seat-view.js").read_text(encoding="utf-8")
    node = _js_function(seat_view, "chainMarkNode")
    assert 'createElement("canvas")' in node, f"chainMarkNode 必须造一块 canvas：{node}"
    assert '"chain-mark"' in node, f"class = chain-mark：{node}"
    assert '"横置（连环状态）"' in node, f"悬停全文照旧：{node}"
    assert "if (player.chained) block.appendChild(chainMarkNode());" in seat_view, (
        "武将块里仍按 `player.chained` 挂铁索画布（.chained 状态类保留）"
    )
    assert "chainLineNode" not in seat_view, "旧名字 chainLineNode 必须整体换掉"

    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<canvas\b[^>]*\bid=\"t-self-chain\"[^>]*>", html)
    assert tag is not None, "自己的座位：#t-self-chain 必须是常驻 <canvas>"
    assert re.search(r'class="chain-mark"', tag.group(0)), tag.group(0)
    assert re.search(r"\bhidden\b", tag.group(0)), f"节点常驻、只切 hidden：{tag.group(0)}"
    assert 'title="横置（连环状态）"' in tag.group(0), tag.group(0)

    table = (STATIC_DIR / "table.js").read_text(encoding="utf-8")
    assert 'if ($("t-self-chain")) $("t-self-chain").hidden = !player.chained;' in table, (
        "自己的座位仍只切 `hidden`（节点常驻的既有协议）"
    )

    rule = _css_rule(".chain-mark")
    assert re.search(r"position:\s*absolute", rule), f"铁索贴图必须绝对定位：{rule}"
    assert re.search(r"top:\s*0", rule) and re.search(r"bottom:\s*0", rule), (
        f"纵向铺满武将块：{rule}"
    )
    assert re.search(r"left:\s*-10\.5%", rule), f"左沿外扩 10.5%（STATIC.overL）：{rule}"
    assert re.search(r"width:\s*119\.7%", rule), (
        f"宽 = 1 + 10.5% + 9.2%（两端都探出牌沿）：{rule}"
    )
    assert re.search(r"pointer-events:\s*none", rule), f"不吃指针：{rule}"
    assert _layer_z_index(".chain-mark") == _CHAIN_MARK_Z, (
        f"铁索 z-index = 4：在阵亡蒙灰（1）之上、与「阵亡」标签同层（spec §演出口径）"
    )
    assert _layer_z_index(".chain-mark") >= 3, "spec §硬约束：z-index ≥ 3"

    suppress = _css_rule('.seat[data-chain-fx="link"] .chain-mark')
    assert re.search(r"visibility:\s*hidden", suppress), (
        f"入链演出期间藏掉常态铁索（用户口径：演完才挂）：{suppress}"
    )

    # 铁索要探出牌沿：武将块的 `overflow: hidden`（白光 / 牌区的既有裁切，别改那条规则）会把
    # 两端的链头正好切在牌沿上——横置的**对手**座位必须放开裁切，否则"两端都探出"永远不成立。
    assert re.search(r"overflow:\s*hidden", _css_rule(".seat .hero-block")), (
        "既有口径：白光仍裁在武将块内（这一条不许改）"
    )
    open_rule = _css_rule(".seat.opponent.chained .hero-block")
    assert re.search(r"overflow:\s*visible", open_rule), (
        f"横置的对手座位要放开裁切，铁索两端才探得出去：{open_rule}"
    )
    # 自己的武将块只有半个卡宽：外扩会被座位卡裁掉左端、并压到右边的装备槽上 ⇒ 贴图收回牌内。
    self_rule = _css_rule(".self .chain-mark")
    assert re.search(r"left:\s*0", self_rule) and re.search(r"width:\s*100%", self_rule), (
        f"自己的铁索贴图必须收在武将块内（不探出）：{self_rule}"
    )


def test_the_chain_layer_is_its_own_overlay_next_to_the_damage_layer() -> None:
    """`#t-chain-fx` / `.chain-fx-layer` 是**独立**的覆盖层（spec §硬约束）。

    不许往 `#t-damage-fx` 里塞画布：`U-DMG-01c` 正向断言"伤害层里只有一张 canvas"，两张
    演出共用一层会让那条断言失去意义，也会让两套排播互相摘层。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<div\b[^>]*\bid=\"t-chain-fx\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少铁索演出层 #t-chain-fx"
    assert re.search(r'class="[^"]*chain-fx-layer', tag.group(0)), tag.group(0)
    assert "aria-hidden" in tag.group(0), "铁索演出是纯视觉层，必须 aria-hidden"
    assert html.index('id="table-shell"') < html.index('id="t-chain-fx"'), (
        "铁索层必须住在 .table-shell 里（座位矩形按壳内坐标量）"
    )
    assert html.index('id="t-damage-fx"') < html.index('id="t-chain-fx"'), (
        "挨着 #t-damage-fx（排在它之后，两者同 z-index 档）"
    )
    # 伤害层仍是**空**的常驻 div：铁索层不在它里面（各自摘层，互不干扰）。
    assert re.search(r'<div\b[^>]*\bid="t-damage-fx"[^>]*></div>', html), (
        "伤害演出层必须仍是空的常驻 div（铁索层不许塞进它里面）"
    )

    layer = _css_rule(".chain-fx-layer")
    assert re.search(r"position:\s*absolute", layer), f"层必须绝对定位：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"层必须铺满壳：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"层不能吃指针：{layer}"
    assert re.search(r"overflow:\s*hidden", layer), f"层必须裁在壳内：{layer}"
    assert _layer_z_index(".chain-fx-layer") == _layer_z_index(".damage-fx-layer"), (
        "铁索层与伤害层同一 z-index 档（spec §硬约束）"
    )
    assert _layer_z_index(".chain-fx-layer") < _layer_z_index(".modal"), "永远让位给询问弹窗"


def test_the_chain_playback_is_scheduled_from_the_seq_cursor_without_holding() -> None:
    """票面 §做法 1：触发点、游标、三条排播口径、两个重绘 / 换局钩子。

    「无时停」已由 `test_the_chain_fx_family_never_registers_a_playback_hold` 逐文件钉住；
    这里钉的是**反过来**的那一半：演出之外的三处次序必须由排播层自己兜住。
    """
    chain = _strip_js_comments(_FX_CHAIN_JS.read_text(encoding="utf-8"))
    session = _SESSION_JS.read_text(encoding="utf-8")
    table = (STATIC_DIR / "table.js").read_text(encoding="utf-8")

    # 排播口径（spec §演出口径·铁索连环）：错开 120ms、同座位 200ms、在播上限 8。
    assert re.search(r"^  var CHAIN_FX_MAX = 8;$", chain, re.M), "在播上限 8（超出丢队尾）"
    assert re.search(r"^  var CHAIN_FX_STAGGER_MS = 120;$", chain, re.M), "相邻错开 120ms"
    assert re.search(r"^  var CHAIN_FX_SAME_SEAT_MS = 200;$", chain, re.M), (
        "同座位 200ms 内的新一条先收掉旧的"
    )
    assert "chainQueue.length >= CHAIN_FX_MAX" in chain or "chainPlays.length >= CHAIN_FX_MAX" in chain, (
        "上限判在入队 / 排播闸门上（超出丢队尾）"
    )

    # 排播函数住在 IIFE 里（比顶层多缩进两格）：剥掉那一层再按顶层函数的形状取正文。
    body_src = re.sub(r"^  ", "", chain, flags=re.M)

    # 判据只看 delta_type（不认技能 id、不认座位之外的东西），座位取 record.seat。
    fire = _js_function(body_src, "fireChainEventsThrough")
    assert 'record.delta_type !== "chained_change"' in fire, fire
    assert "record.seat" in fire, "座位 = record.seat"
    assert "kind !== \"delta\"" in fire, "只看 delta 记录"

    # seq 游标：`state.chainSeq` 首批初始化成该批最大 seq（只切常态、不演）。
    prime = _js_function(body_src, "chainPrimeSeq")
    assert "stateRef.chainSeq" in prime and "playback.records" in prime, prime
    assert "seq > max" in prime, "取该批最大 seq"
    assert "seq > Number(state.chainSeq)" in fire, "之后只演 seq 更大的那几条"
    clear = _js_function(body_src, "clearChainFx")
    assert "state.chainSeq = null" in clear, "换局把游标清回 null（新局首批仍是历史）"

    # 入链"演完才挂常态"：开演时清掉常态贴图、同步时按抑制态重贴。
    start = _js_function(body_src, "startChainPlay")
    assert "chainClearMark" in start, "入链开演时先清常态贴图（演完才挂）"
    sync = _js_function(body_src, "chainFxSyncBody") if re.search(
        r"function chainFxSyncBody\(", chain
    ) else _js_function(chain, "chainFxSync")
    assert "chainLinkLive(seat)" in sync and "chainClearMark(mark)" in sync, (
        "入链期间不挂常态，收尾后由同一条同步补上"
    )
    assert "chainPaintMark(mark, block)" in sync, "平时按当前矩形画常态贴图"
    # 每次重绘都重贴：`renderTable` 末尾显式调（座位的调用方，不是本文件反向抓事件）。
    step_at = table.index("damageDropStalePlays();")
    sync_at = table.index("chainFxSync();")
    assert step_at < sync_at, "chainFxSync() 排在 damageDropStalePlays() 之后（同一处重绘钩子）"
    assert "chainFxSync" in table, "renderTable 必须重贴常态贴图"

    # 触发点：在伤害演出**之后**（与卡牌语音 / 阵亡的相邻次序由跨票次序用例一次钉住）。
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    damage_at = body.find("fireDamageEventsThrough(record.seq)")
    chain_at = body.find("fireChainEventsThrough(record)")
    assert damage_at >= 0 and chain_at > damage_at, (
        "铁索演出必须挂在伤害演出之后（票面 §做法 1）；真实相邻次序是 伤害 → 卡牌语音 → 铁索，"
        "见 ::test_the_playback_hooks_fire_in_damage_card_chain_death_order"
    )
    commit = _js_function(session, "commitView")
    assert "clearChainFx()" in commit, "换局要清铁索的队列 / 画布 / 计时器 / 声音"
    assert "clearDamageFx();" in commit and commit.index("clearDamageFx();") < commit.index("clearChainFx();"), (
        "两层各清各的（铁索紧跟着伤害层的清场）"
    )


def test_the_chain_sfx_is_a_one_shot_scheduler_behind_the_same_switch() -> None:
    """票面 §做法 3：`SgsChainSfx` 是**一次性排程器**，由 `sfx-ui.js` 在同一手势 / 开关里
    惰性建 **1 路**；`fx-chain.js` 只调 `chainSfxPlay` / `chainSfxStop` / `chainSfxMute`。

    三条既有口径不动：缺省静音 / `localStorage['sgs.sfx']` / 静音时不建 `AudioContext`。
    `?audiotest=1` 不扩（spec §不做）——铁索是排程器，没有逐档电平可听。
    """
    sfx_ui = (STATIC_DIR / "sfx-ui.js").read_text(encoding="utf-8")
    sfx_chain = (STATIC_DIR / "sfx-chain.js").read_text(encoding="utf-8")
    chain = _FX_CHAIN_JS.read_text(encoding="utf-8")

    # 排程器的接口（与素材包一致）。
    for name in ("unlock", "play", "stop", "mute"):
        assert re.search(rf"^\s+{name}: function", sfx_chain, re.M), (
            f"SgsChainSfx 的播放器缺 {name}()"
        )
    assert re.search(r"global\.SgsChainSfx = \{", sfx_chain), "音效入口挂 window.SgsChainSfx"
    assert "renderOffline" in sfx_chain, "离线电平读数照素材包留着（?audiotest 不扩，但仍可单跑）"

    # 一路声道：惰性建、跟着同一个手势 / 开关。
    assert re.search(r"^let chainSfxLane = null;$", sfx_ui, re.M), "铁索只有一路声道（惰性建）"
    ensure = _js_function(sfx_ui, "chainSfxEnsure")
    assert 'window.SgsChainSfx' in ensure, "按全局名惰性取模块（缺件就跳过，不报错）"
    assert "unlock" in ensure, "解锁必须走模块自己的 unlock()（手势同一个 tick）"
    modules = _js_function(sfx_ui, "damageSfxEnsureModules")
    assert "chainSfxEnsure" in modules, (
        "铁索声道要在**同一个**手势 / 开关里建（damageSfxEnsureModules 顺手建它）"
    )
    play = _js_function(sfx_ui, "chainSfxPlay")
    assert "damageSfxOn" in play and "return null" in play, (
        "「♪」关着时一声都不出、也不建 AudioContext（`SgsChainSfx.create` 都不许走到）"
    )
    assert "lane.player.play(" in play, "开着一场一声（play(mode, 0, 1)）"
    # 收尾停声认主人（评审修复）：一个 bus、后开的一声顶掉前一条 ⇒ 同批两条链差 120ms 时，
    # 先收尾的那条不许把后一条还在演的那声掐断。
    stop = _js_function(sfx_ui, "chainSfxStop")
    assert "chainSfxOwnerToken" in stop and "owner !== undefined" in stop, (
        "chainSfxStop(owner) 必须带 owner 守卫；不带参 = 换局 / 关「♪」的强制收声"
    )
    assert "play.sfxToken = chainSfxPlay(play.mode)" in chain, (
        "开演时记下这一声的主人（chainSfxPlay 返回的 owner token）"
    )
    assert "chainSfxStop(play.sfxToken)" in chain, (
        "fx-chain.js 收尾必须交回 token（只有当前声音的主人能停）"
    )
    assert "chainSfxStop();" in chain, (
        "换局 / 关「♪」的强制收声仍不带 owner（clearChainFx 走那一档）"
    )
    mute = _js_function(sfx_ui, "damageSfxMute")
    assert "chainSfxMute" in mute, "开关要灌到铁索那一路（与伤害声道同一次静音）"
    assert "SgsChainSfx" not in sfx_ui.split("DAMAGE_SFX_ENGINES = {")[1].split("};")[0], (
        "铁索**不进**伤害声道池：它是排程器，不是逐帧 tick 的播放器"
    )
    audio = _js_function(sfx_ui, "damageSfxAudiotest")
    assert "ChainSfx" not in audio and "chainSfx" not in audio, (
        "?audiotest=1 不扩到铁索（spec §不做）"
    )

    # 画面侧只调这三个口（单向缝），不自己碰 AudioContext。
    for name in ("chainSfxPlay", "chainSfxStop"):
        assert f"typeof {name} === \"function\"" in chain, (
            f"fx-chain.js 要按 typeof 守卫调 {name}（缺 sfx-ui.js 的探针里仍是一份独立排播）"
        )
    assert "AudioContext" not in chain, "画面侧不碰音频 API（音效层的事）"


# ---------------------------------------------------------------------------
# chain-death-voices 票 02：阵亡演出（`fx-death.js` / `sfx-death.js`）
#
# 判据来自票面 §验收断言 2 与 spec §演出口径「阵亡：三条覆盖规则 + 三条反向钩子」：
# 1. **`SgsDeathFX` 的形状与单时间轴**：`create` / `PARAMS` / `gameTiming` /
#    `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS` / `MAX_TOTAL_MS` 都在；
#    `PARAMS.duration = 3.05` 是基准；`anim.ini` 的 `death_fx_total` 与 `DEFAULT_TIMING`
#    逐键同值；钳制 1200–3200；各节拍按 `k = total / 3050` 等比缩放。
# 2. **两个文件里都没有 `registerPlaybackHold`**（用户口径：阵亡无时停；素材包 §3 的 hold
#    建议**不采纳**）。
# 3. **三条覆盖规则是加法**：`.seat.dead` / `.seat.dead .hero-block::after` / `.dead-tag`
#    三条基线**逐字未改**；`data-death-fx="hold"/"gone"` 只覆盖。
# 4. **`#t-death-fx` 是独立覆盖层**：与 `#t-damage-fx` 同 z-index 档，且不复用伤害层。
# 5. **排播钩子**：`stepPlayback` 里 `fireDeathEventsThrough(record.seq)` 在
#    `fireDamageEventsThrough(record.seq)` 之后；`renderTable` 末尾 `deathFxSync()`；
#    `commitView` 换会话时 `clearDeathFx()`；在播上限 2、第 3 条丢队尾；每条开演前重测 stage。
# 6. **音效**：`SgsDeathSfx` 是一次性排程器，`sfx-ui.js` 在同一个手势 / 开关里惰性建 1 路。
#
# 行为（hold / gone / 收尾 / 无时停 / 两条同批 / 第 3 条丢掉 / 轻伤害叠播 / 自己的座位 /
# 版式作废 / 换局 / 音效）归烟测 `U-DEATH-01..`。
# ---------------------------------------------------------------------------

_FX_DEATH_JS = STATIC_DIR / "fx-death.js"
_SFX_DEATH_JS = STATIC_DIR / "sfx-death.js"


def test_the_death_fx_family_never_registers_a_playback_hold() -> None:
    """用户口径：阵亡**无时停**（素材包 `SGS-INTEGRATION.md` §3 的 hold 建议**不采纳**）。

    逐文件扫 `registerPlaybackHold`：出现即红。与铁索那条同形，但**各钉各的**——两张票
    改的是不同的文件，"另一份也没登记"不是这一份的证据。反向对照仍是伤害家族（3 / 4 档
    登记一条），避免把登记表整个禁掉。
    """
    for name in ("fx-death.js", "sfx-death.js"):
        text = _strip_js_comments((STATIC_DIR / name).read_text(encoding="utf-8"))
        assert "registerPlaybackHold" not in text, (
            f"{name} 不许登记回放暂停：阵亡演出无时停（spec §硬约束 / 用户口径）"
        )
        assert "playbackHold" not in text, f"{name} 连暂停判据的名字都不该认识"
    damage_fx = _strip_js_comments(_DAMAGE_FX_JS.read_text(encoding="utf-8"))
    assert 'registerPlaybackHold("damage"' in damage_fx, (
        "反向对照：伤害家族仍只登记自己那一条（本条不是把登记表整个禁掉）"
    )


_DEATH_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ini = readFileSync("tools/sgs_web/static/anim.ini", "utf8");
const noteNode = { textContent: "", hidden: true };
/* `fx-death.js` 的 `create()` 要一张**真 canvas + 2D 上下文**（烘焙碎片贴图 / 画每一帧）。
 * 这里给一个记录型的空实现（与伤害舞台探针同一套）：方法全在、不画像素，但把 `fillText`
 * 记下来——"牌桌档不落印（label: ''）"这条只能靠它读。 */
const calls = [];
const originStack = [];
let origin = { x: 0, y: 0 };
const gradient = { addColorStop() {} };
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
    if (prop === "scale") return () => {};
    if (prop === "fillText") return (text) => calls.push({ op: "fillText", text, font: props.font });
    if (prop === "drawImage") return (image) => calls.push({ op: "drawImage", w: image && image.width });
    return () => {};
  },
  set(_target, prop, value) { props[prop] = value; return true; },
});
const makeCanvas = () => ({ width: 0, height: 0, getContext: () => ctx });
const context = vm.createContext({
  __calls: calls,
  console, Date, JSON, Object, Array, Number, String, Math, isFinite, parseInt,
  fetch: async () => ({ ok: true, text: async () => ini }),
  document: {
    getElementById: (id) => (id === "t-anim-note" ? noteNode : null),
    createElement: () => makeCanvas(),
  },
});
context.globalThis = context;
context.window = context;
context.$ = (id) => context.document.getElementById(id);
for (const name of ["anim.js", "fx-death.js"]) {
  vm.runInContext(readFileSync(`tools/sgs_web/static/${name}`, "utf8"), context, { filename: name });
}

const report = await vm.runInContext(
  `(async () => {
     await animReady;
     const FX = SgsDeathFX;
     const g = (raw) => FX.gameTiming(raw || {});
     const textsOf = (fx, at) => {
       __calls.length = 0;
       fx.draw(at);
       return __calls.filter((call) => call.op === "fillText").map((call) => call.text);
     };
     /* 落印是**烘成贴图**的（bakeStamp 在 create 时画一次），所以"有没有印"要在建实例
      * 那一步读：牌桌档 label 传空串 ⇒ 一次都不烘。 */
     const creationTexts = (opts) => {
       __calls.length = 0;
       const fx = make(opts);
       return { fx, texts: __calls.filter((call) => call.op === "fillText").map((call) => call.text) };
     };
     const tableStage = { cx: 720, cy: 405, w: 250, h: 240 };
     const view = { w: 1440, h: 810 };
     const make = (opts) => FX.create(document.createElement("canvas"), opts);
     /* 牌桌档：bare + radius 0 + label '' + 2200ms。 */
     const tableSetup = creationTexts({
       bare: true, radius: 0, label: "", dpr: 1, view, stage: tableStage,
       timing: { death_fx_total: animTiming("death_fx_total") },
     });
     const table = tableSetup.fx;
     /* 素材页档：3.05s（不压时间）+ 默认 label（会落印）。 */
     const materialSetup = creationTexts({ dpr: 1, timing: { death_fx_total: 3050 } });
     const material = materialSetup.fx;
     const defaults = make({ dpr: 1 });
     return JSON.stringify({
       note: $("t-anim-note").textContent,
       animConfigKeys: Object.keys(animConfig),
       deathFromIni: animTiming("death_fx_total"),
       shape: {
         create: typeof FX.create,
         gameTiming: typeof FX.gameTiming,
         seatOf: typeof FX.deathSeatOf,
         fire: typeof FX.fireDeathEventsThrough,
         sync: typeof FX.deathFxSync,
         clear: typeof FX.clearDeathFx,
         params: typeof FX.PARAMS,
         colors: typeof FX.COLORS,
         maxPlays: FX.MAX_PLAYS,
         defaults: FX.DEFAULT_TIMING,
         fields: FX.TIMING_FIELDS,
         keys: FX.TIMING_KEYS,
         frozen: [Object.isFrozen(FX.TIMING_FIELDS), Object.isFrozen(FX.TIMING_KEYS)],
         prefix: FX.TIMING_PREFIX,
         minTotal: FX.MIN_TOTAL_MS,
         maxTotal: FX.MAX_TOTAL_MS,
       },
       params: {
         duration: FX.PARAMS.duration,
         waves: FX.PARAMS.waves,
         holdAt: FX.PARAMS.holdAt,
         burstAt: FX.PARAMS.burstAt,
         labelAt: FX.PARAMS.labelAt,
         label: FX.PARAMS.label,
       },
       resolved: {
         plain: g(),
         fromIni: g({ death_fx_total: animTiming("death_fx_total") }),
         basis: g().basis,
         speed: g().speed,
       },
       clamps: {
         low: g({ death_fx_total: 100 }).total,
         high: g({ death_fx_total: 9000 }).total,
         junk: g({ death_fx_total: "abc" }).total,
         missing: g({}).total,
       },
       table: {
         duration: table.duration,
         total: table.total,
         speed: table.speed,
         width: table.width,
         height: table.height,
         burstAt: table.burstAt,
         holdAt: table.holdAt,
         labelAt: table.labelAt,
         pause: table.burstAt - table.holdAt,
         shards: table.shardCount,
         cracks: table.crackCount,
         label: table.tier.label,
         noStamp: tableSetup.texts.includes("阵亡") === false,
         drawsToEnd: (textsOf(table, table.duration + 10), true),
         drawsAtZero: (textsOf(table, 0), true),
       },
       material: {
         duration: material.duration,
         burstAt: material.burstAt,
         holdAt: material.holdAt,
         labelAt: material.labelAt,
         width: material.width,
         height: material.height,
         stamp: materialSetup.texts.includes("阵亡"),
       },
       defaultCreate: { duration: defaults.duration, total: defaults.total },
     });
   })()`,
  context,
  { filename: "death_fx_probe.js" },
);
console.log(report);
"""


def _death_fx_probe() -> dict:
    """跑真 `anim.js` + 真 `fx-death.js`（node vm），拿回形状 / 时间轴 / 钳制 / 缩放实测值。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "death_fx_probe.mjs"
        probe.write_text(_DEATH_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    assert result.returncode == 0, (
        f"阵亡探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def death_fx_probe() -> dict:
    """一次探针跑完，形状 / 时间轴 / 钳制 / 缩放几条用例共用。"""
    return _death_fx_probe()


def test_the_death_fx_module_shape_and_its_timing_key(death_fx_probe: dict) -> None:
    """`SgsDeathFX` 的形状、单时间轴的单一真源与钳制（spec §演出口径·牌桌档缺省）。

    一半在 node 探针里驱动真函数（`gameTiming` 的钳制 / 缺键 / 垃圾值、`create` 的缩放），
    另一半与磁盘上的 `anim.ini` 对表。字段名**不是**写在测试里的第二份清单：期望值来自
    `DEFAULT_TIMING`。
    """
    probe = death_fx_probe
    shape = probe["shape"]
    for key in ("create", "gameTiming", "seatOf", "fire", "sync", "clear"):
        assert shape[key] == "function", f"SgsDeathFX 缺 {key}()"
    assert shape["params"] == "object" and shape["colors"] == "object", "PARAMS / COLORS 要导出"
    assert shape["maxPlays"] == 2, f"在播上限 2（spec §演出口径）：{shape['maxPlays']}"

    # 素材页基准：3.05s 与四个节拍（票面 §做法 1：`PARAMS.duration = 3.05` 作基准）。
    params = probe["params"]
    assert params["duration"] == pytest.approx(3.05), f"素材页基准 3.05s：{params['duration']}"
    assert params["waves"] == [0.16, 0.52, 0.98], f"三波裂缝：{params['waves']}"
    assert params["holdAt"] == pytest.approx(1.40)
    assert params["burstAt"] == pytest.approx(1.74)
    assert params["labelAt"] == pytest.approx(2.26)
    assert params["label"] == "阵亡", "素材页缺省落印（牌桌档才传空串）"

    # `DEFAULT_TIMING`：字段名由它生成（票面：字段名只有一处真源）。
    defaults = shape["defaults"]
    assert defaults == {"total": 2200}, f"缺省 2200ms：{defaults}"
    assert list(shape["fields"]) == sorted(defaults), (
        f"TIMING_FIELDS 必须由 DEFAULT_TIMING 生成：{shape['fields']}"
    )
    expected_keys = sorted(f"{shape['prefix']}{field}" for field in defaults)
    assert sorted(shape["keys"]) == expected_keys, (
        f"TIMING_KEYS 必须由前缀 + 字段生成：{shape['keys']} vs {expected_keys}"
    )
    assert shape["frozen"] == [True, True], "TIMING_FIELDS / TIMING_KEYS 冻结（别被就地改写）"
    assert shape["prefix"] == "death_fx_", f"anim.ini 前缀：{shape['prefix']}"
    assert shape["maxTotal"] == 3200 and shape["minTotal"] == 1200, (
        f"钳制 1200–3200ms：min={shape['minTotal']} max={shape['maxTotal']}"
    )

    # 磁盘上的 `anim.ini` 与 `DEFAULT_TIMING` **逐键同值**（用户改一份文件调时长）。
    ini = {
        key: float(value)
        for key, value in _ini_entries().items()
        if key.startswith("death_fx_")
    }
    assert set(ini) == set(shape["keys"]), (
        f"anim.ini 的键必须与 TIMING_KEYS 一致：缺 {sorted(set(shape['keys']) - set(ini))}；"
        f"多 {sorted(set(ini) - set(shape['keys']))}"
    )
    assert ini["death_fx_total"] == defaults["total"], (
        f"death_fx_total 必须与 DEFAULT_TIMING 同值：ini={ini['death_fx_total']} "
        f"default={defaults['total']}"
    )
    # 新键真的进了 animConfig，且没被当成未知键删掉 / 报错。
    anim_js = _ANIM_JS.read_text(encoding="utf-8")
    prefixes = re.search(r"^const ANIM_FOREIGN_KEY_PREFIXES = \[(?P<body>[^\]]*)\];$", anim_js, re.M)
    assert prefixes is not None, "anim.js 里找不到 ANIM_FOREIGN_KEY_PREFIXES"
    assert '"death_fx_"' in prefixes.group("body"), (
        f"anim.js 必须把 death_fx_ 登记成外来键（否则用户的改动被当未知键删掉）："
        f"{prefixes.group('body')}"
    )
    assert "未知键" not in probe["note"], f"death_fx_* 不得被当成未知键（页面提示：{probe['note']}）"
    for key in shape["keys"]:
        assert key in probe["animConfigKeys"], f"anim.js 没把 {key} 读进 animConfig"
    assert probe["deathFromIni"] == defaults["total"], (
        f"animTiming(death_fx_total) 应等于缺省值：{probe['deathFromIni']}"
    )

    # `gameTiming`：缺省 = DEFAULT_TIMING，ini 值活着，越界被夹（1200–3200），垃圾值退默认。
    resolved = probe["resolved"]
    assert resolved["plain"]["total"] == 2200, f"缺省解算：{resolved['plain']}"
    assert resolved["plain"]["seconds"] == pytest.approx(2.2)
    assert resolved["plain"]["timing"] == {"death_fx_total": 2200}
    assert resolved["basis"] == pytest.approx(3.05), "basis = 素材页基准 3.05s"
    assert resolved["fromIni"]["total"] == 2200, "带真 ini 值的解算也要落到 2200"
    clamps = probe["clamps"]
    assert clamps["low"] == 1200, f"低于 1200ms 被夹到 1200：{clamps['low']}"
    assert clamps["high"] == 3200, f"高于 3200ms 被夹到 3200：{clamps['high']}"
    assert clamps["junk"] == 2200, f"非法值退回 DEFAULT_TIMING：{clamps['junk']}"
    assert clamps["missing"] == 2200, f"缺键退回 DEFAULT_TIMING：{clamps['missing']}"


def test_the_table_death_profile_scales_every_beat_by_the_same_k(death_fx_probe: dict) -> None:
    """票面 §做法 1：牌桌档压到 2.2s 时各节拍**等比缩放**（`k = total / 3050`）。

    素材页 3.05s 那一档逐拍不变（`burstAt` 1.74 / `holdAt` 1.40 / `labelAt` 2.26）；
    牌桌档三个节拍同乘 `k`，于是 `hold → burst` 的停顿约 0.25s（素材包 §8 的 0.2s 下限仍守）。
    牌桌档的另外三条口径也在这一条里：`bare`（不画纸底 / 样例牌）、`radius: 0`（直角武将块）、
    `label: ""`（**不落印**——游戏自己已经有「阵亡」标签）。
    """
    probe = death_fx_probe
    table = probe["table"]
    material = probe["material"]
    k = 2200 / 3050
    assert table["duration"] == pytest.approx(2.2), f"牌桌档整段 2.2s：{table['duration']}"
    assert table["total"] == 2200
    assert table["speed"] == pytest.approx(3.05 / 2.2), f"speed = 基准 / 牌桌秒：{table['speed']}"
    assert table["burstAt"] == pytest.approx(1.74 * k), f"burstAt 等比缩：{table['burstAt']}"
    assert table["holdAt"] == pytest.approx(1.40 * k), f"holdAt 等比缩：{table['holdAt']}"
    assert table["labelAt"] == pytest.approx(2.26 * k), f"labelAt 等比缩：{table['labelAt']}"
    assert table["pause"] == pytest.approx(0.34 * k), f"停顿 = 0.34s × k：{table['pause']}"
    assert table["pause"] >= 0.2, f"素材包 §8：停顿不许低于 0.2s（实际 {table['pause']}）"
    assert table["label"] == "", "牌桌档 label 传空串（不落印）"
    assert table["noStamp"] is True, "牌桌档一笔「阵亡」都不许画（标签是游戏自己那份）"
    assert table["drawsAtZero"] and table["drawsToEnd"], "draw(t) 在两端都要能跑（不抛错）"
    # 画布按覆盖层的 CSS 尺寸建（内部 1:1 合成）：牌桌档的两块读数来自 view。
    assert (table["width"], table["height"]) == (1440, 810), (
        f"画布尺寸 = 覆盖层尺寸：{table['width']}x{table['height']}"
    )
    assert table["shards"] > 0 and table["cracks"] > 0, "碎片 / 裂缝都烘出来了（自检读数）"

    # 素材页那一档：3.05s，节拍**逐拍不动**，默认落印。
    assert material["duration"] == pytest.approx(3.05)
    assert material["burstAt"] == pytest.approx(1.74)
    assert material["holdAt"] == pytest.approx(1.40)
    assert material["labelAt"] == pytest.approx(2.26)
    assert (material["width"], material["height"]) == (1920, 1080), "缺省舞台仍是 1920×1080"
    assert material["stamp"] is True, "素材页档照旧落印（label 缺省 = 阵亡）"

    # 不传 timing ⇒ 牌桌档缺省（2200ms），不是素材页的 3.05s。
    assert probe["defaultCreate"]["total"] == 2200
    assert probe["defaultCreate"]["duration"] == pytest.approx(2.2)


# 三条基线样式**逐字**冻结（票面：演出只做覆盖、不改基线；摘属性后必须回到原样）。
_BASELINE_DEATH_RULES = (
    ".seat.dead, .seat.flipped { opacity: .72; }",
    ".seat.dead .hero-block::after,\n"
    ".flipped-layer {\n"
    "  position: absolute; inset: 0; z-index: 1;\n"
    "  background: rgba(120, 116, 108, .35); pointer-events: none;\n"
    "}",
    '.seat.dead .hero-block::after { content: ""; }',
    ".dead-tag {\n"
    "  position: absolute; inset: 0; z-index: 4;\n"
    "  display: flex; align-items: center; justify-content: center;\n"
    "  pointer-events: none;\n"
    "}",
)


def test_the_death_override_rules_are_additive_and_the_baseline_stays_verbatim() -> None:
    """票面 §做法 2：三条覆盖规则是**加法**，基线三条**逐字未改**。

    `renderTable` 在演出之前就把牌画成"已阵亡"，而这套演出前 1.7s 牌还得是活的 ⇒
    `hold` 压住置灰与标签、`gone` 藏掉牌本身，收尾摘属性回到基线。
    覆盖规则**不许**依赖 `.seat .hero-block { overflow: hidden }` 的裁切（那条被白光烟测钉着）。
    """
    css = _CSS_TEXT
    for rule in _BASELINE_DEATH_RULES:
        assert rule in css, f"阵亡的基线样式被改了（必须逐字保留）：{rule!r}"

    # 保持式：牌看着还活着。
    hold = _css_rule('.seat[data-death-fx="hold"]')
    assert re.search(r"opacity:\s*1\b", hold), f"hold 期间席位不置灰：{hold}"
    hidden = _css_rule('.seat[data-death-fx="hold"] .dead-tag')
    assert re.search(r"display:\s*none", hidden), f"hold 期间「阵亡」标签藏掉：{hidden}"
    # 蒙灰伪元素与标签写在**同一条**规则里（两个选择器共用 `display: none`）。
    assert '.seat[data-death-fx="hold"] .hero-block::after,' in css, (
        "hold 期间蒙灰伪元素也要藏掉（与标签同一条规则）"
    )
    assert re.search(r"display:\s*none", hidden), f"hold 期间蒙灰伪元素藏掉：{hidden}"
    # 碎了之后：牌本身藏掉（不靠"盖一张纸色矩形"）。
    gone = _css_rule('.seat[data-death-fx="gone"] .hero-block')
    assert re.search(r"visibility:\s*hidden", gone), f"gone 期间牌本身藏掉：{gone}"
    assert "overflow" not in gone and "display" not in gone, (
        f"gone 不许改裁切 / 显示方式（`overflow: hidden` 是白光那条基线）：{gone}"
    )
    # 白光那条基线一个字不动（它被烟测的白闪用例钉着）。
    assert re.search(r"overflow:\s*hidden", _css_rule(".seat .hero-block")), (
        "既有口径：白光仍裁在武将块内（这一条不许改）"
    )
    # 属性由排播层贴 / 摘；两个方向都要有（贴 = 开演 / 重绘，摘 = 收尾 / 作废 / 换局）。
    death = _strip_js_comments(_FX_DEATH_JS.read_text(encoding="utf-8"))
    assert '"data-death-fx"' in death, "排播层要贴这个属性"
    assert "setAttribute(\"data-death-fx\", phase)" in death, "贴属性（hold / gone）"
    assert "removeAttribute(\"data-death-fx\")" in death, "摘属性（收尾 / 作废 / 换局）"


def test_the_death_layer_is_its_own_overlay_next_to_the_damage_layer() -> None:
    """`#t-death-fx` / `.death-fx-layer` 是**独立**的覆盖层（spec §硬约束）。

    不许往 `#t-damage-fx` 里塞画布：`U-DMG-01c` 正向断言"伤害层里只有一张 canvas"；
    也不与铁索共层（两套排播各自摘层）。同一 z-index 档，永远让位给询问弹窗。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<div\b[^>]*\bid=\"t-death-fx\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少阵亡演出层 #t-death-fx"
    assert re.search(r'class="[^"]*death-fx-layer', tag.group(0)), tag.group(0)
    assert "aria-hidden" in tag.group(0), "阵亡演出是纯视觉层，必须 aria-hidden"
    assert html.index('id="table-shell"') < html.index('id="t-death-fx"'), (
        "阵亡层必须住在 .table-shell 里（座位矩形按壳内坐标量）"
    )
    assert html.index('id="t-damage-fx"') < html.index('id="t-death-fx"'), (
        "挨着 #t-damage-fx（排在它之后，两者同 z-index 档）"
    )
    # 伤害层仍是**空**的常驻 div：阵亡层不在它里面（各自摘层，互不干扰）。
    assert re.search(r'<div\b[^>]*\bid="t-damage-fx"[^>]*></div>', html), (
        "伤害演出层必须仍是空的常驻 div（阵亡层不许塞进它里面）"
    )

    layer = _css_rule(".death-fx-layer")
    assert re.search(r"position:\s*absolute", layer), f"层必须绝对定位：{layer}"
    assert re.search(r"inset:\s*0\b", layer), f"层必须铺满壳：{layer}"
    assert re.search(r"pointer-events:\s*none", layer), f"层不能吃指针：{layer}"
    assert re.search(r"overflow:\s*hidden", layer), f"层必须裁在壳内：{layer}"
    assert _layer_z_index(".death-fx-layer") == _layer_z_index(".damage-fx-layer"), (
        "阵亡层与伤害层同一 z-index 档（spec §硬约束）"
    )
    assert _layer_z_index(".death-fx-layer") == _layer_z_index(".chain-fx-layer"), (
        "三张演出层同档（互不摘层）"
    )
    assert _layer_z_index(".death-fx-layer") < _layer_z_index(".modal"), "永远让位给询问弹窗"


def test_the_death_playback_is_scheduled_from_the_seq_cursor_without_holding() -> None:
    """票面 §做法 1：触发判据、游标、排播口径、三个钩子。

    「无时停」已由 `test_the_death_fx_family_never_registers_a_playback_hold` 逐文件钉住；
    这里钉的是**反过来**的那一半：演出之外的三处次序必须由排播层自己兜住。
    """
    death = _strip_js_comments(_FX_DEATH_JS.read_text(encoding="utf-8"))
    session = _SESSION_JS.read_text(encoding="utf-8")
    table = (STATIC_DIR / "table.js").read_text(encoding="utf-8")

    # 排播口径（spec §演出口径·阵亡）：在播上限 2、第 3 条丢队尾（不排队等）。
    assert re.search(r"^  var DEATH_FX_MAX = 2;$", death, re.M), "在播上限 2（超出丢队尾）"
    assert "deathPlays.length >= DEATH_FX_MAX" in death, "超限判在触发点上（不排队）"

    # 排播函数住在 IIFE 里（比顶层多缩进两格）：剥掉那一层再按顶层函数的形状取正文。
    body_src = re.sub(r"^  ", "", death, flags=re.M)

    # 判据只看记录（kind / timing / actor），**不认 hp_change**。
    seat = _js_function(body_src, "deathSeatOf")
    assert 'record.kind !== "timing"' in seat, seat
    assert 'record.timing !== "DEATH_BEFORE_START"' in seat, seat
    assert "record.actor" in seat, "座位 = record.actor（两种死法同形）"
    assert "hp_change" not in death, "不许用 hp_change 到 0 去猜（护甲全吸收 / 濒死被救会猜错）"
    fire = _js_function(body_src, "fireDeathEventsThrough")
    assert "deathSeatOf(record)" in fire, fire
    assert "deathCursor" in fire, "seq 游标（同一条记录只演一次）"
    assert "startDeathPlay" in fire, "交给排播闸门"

    # 每条开演前**重新量**一次 stage（版式可能刚变；量不到就不演）。
    start = _js_function(body_src, "startDeathPlay")
    assert "deathStageOf(seat, layerRect)" in start, start
    assert "deathDroppedCount += 1" in start, "量不到就丢，并记一笔读数"
    assert 'setAttribute("data-death-fx", phase)' in body_src, "开演 / 到 burstAt 都贴属性"
    assert "window.setTimeout(function () { finishDeathPlay(play); }, play.duration)" in start, (
        "摘层挂在**演出自己的计时器**上（不能等 commit / 下一批：无时停）"
    )
    assert "deathSfxPlay(0, " in start, "开演时同一声（单向缝：只调 sfx-ui 的口）"

    # 到 `burstAt` 换 `gone`：牌本身藏掉。
    gone = _js_function(body_src, "deathMarkGone")
    assert "deathSetSeatFlag(play.seat, \"gone\")" in gone, gone

    # 三个反向钩子。
    sync_at = table.index("damageDropStalePlays();")
    assert sync_at < table.index("deathFxSync();"), (
        "deathFxSync() 排在 damageDropStalePlays() 之后（同一处重绘钩子）"
    )
    commit = _js_function(session, "commitView")
    assert "clearDeathFx()" in commit, "换局要清阵亡的队列 / 画布 / 计时器 / 属性 / 声音"
    assert "clearDamageFx();" in commit and commit.index("clearDamageFx();") < commit.index("clearDeathFx();"), (
        "两层各清各的（阵亡紧跟着伤害层的清场）"
    )

    # 触发点：紧挨伤害演出的后面（同一条 seq 游标）。
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    damage_at = body.find("fireDamageEventsThrough(record.seq)")
    death_at = body.find("fireDeathEventsThrough(record.seq)")
    assert damage_at >= 0 and death_at > damage_at, (
        "阵亡演出必须挂在伤害演出之后（票面 §做法 1）"
    )
    # 属性必须**清得干净**：收尾 / 作废 / 换局三处都有摘属性的路径。
    finish = _js_function(body_src, "finishDeathPlay")
    assert "deathSetSeatFlag(play.seat, null)" in finish, finish
    assert "deathDropTimers(play)" in finish, "收尾要把两个计时器都摘掉（不留残留）"
    clear = _js_function(body_src, "clearDeathFx")
    assert "removeAttribute(\"data-death-fx\")" in clear, "换局把所有座位的属性都清掉"
    assert "deathDropTimers(play)" in clear, "换局也要摘计时器（不然还有一拍会回来）"


def test_the_death_sfx_is_a_one_shot_scheduler_behind_the_same_switch() -> None:
    """票面 §做法 3：`SgsDeathSfx` 是**一次性排程器**，由 `sfx-ui.js` 在同一手势 / 开关里
    惰性建 **1 路**；`fx-death.js` 只调 `deathSfxPlay` / `deathSfxStop` / `deathSfxMute`。

    三条既有口径不动：缺省静音 / `localStorage['sgs.sfx']` / 静音时不建 `AudioContext`。
    `?audiotest=1` 不扩（spec §不做）——阵亡是排程器，没有逐档电平可听。
    """
    sfx_ui = (STATIC_DIR / "sfx-ui.js").read_text(encoding="utf-8")
    sfx_death = (STATIC_DIR / "sfx-death.js").read_text(encoding="utf-8")
    death = _FX_DEATH_JS.read_text(encoding="utf-8")

    # 排程器的接口（与素材包一致）。
    for name in ("unlock", "play", "stop", "mute"):
        assert re.search(rf"^\s+{name}: function", sfx_death, re.M), (
            f"SgsDeathSfx 的播放器缺 {name}()"
        )
    assert re.search(r"global\.SgsDeathSfx = \{", sfx_death), "音效入口挂 window.SgsDeathSfx"
    assert "renderOffline" in sfx_death, "离线电平读数照素材包留着（?audiotest 不扩，但仍可单跑）"
    assert "SgsDeathFX" in sfx_death, "报点从画面模块的 PARAMS 读（不另抄一份时间表）"

    # 一路声道：惰性建、跟着同一个手势 / 开关。
    assert re.search(r"^let deathSfxLane = null;$", sfx_ui, re.M), "阵亡只有一路声道（惰性建）"
    ensure = _js_function(sfx_ui, "deathSfxEnsure")
    assert 'window.SgsDeathSfx' in ensure, "按全局名惰性取模块（缺件就跳过，不报错）"
    assert "unlock" in ensure, "解锁必须走模块自己的 unlock()（手势同一个 tick）"
    modules = _js_function(sfx_ui, "damageSfxEnsureModules")
    assert "deathSfxEnsure" in modules, (
        "阵亡声道要在**同一个**手势 / 开关里建（damageSfxEnsureModules 顺手建它）"
    )
    play = _js_function(sfx_ui, "deathSfxPlay")
    assert "damageSfxOn" in play and "return null" in play, (
        "「♪」关着时一声都不出、也不建 AudioContext（`SgsDeathSfx.create` 都不许走到）"
    )
    assert "lane.player.play(" in play, "开演一声（play(fromT, speed)）"
    mute = _js_function(sfx_ui, "damageSfxMute")
    assert "deathSfxMute" in mute, "开关要灌到阵亡那一路（与伤害声道同一次静音）"
    assert "SgsDeathSfx" not in sfx_ui.split("DAMAGE_SFX_ENGINES = {")[1].split("};")[0], (
        "阵亡**不进**伤害声道池：它是排程器，不是逐帧 tick 的播放器"
    )
    audio = _js_function(sfx_ui, "damageSfxAudiotest")
    assert "DeathSfx" not in audio and "deathSfx" not in audio, (
        "?audiotest=1 不扩到阵亡（spec §不做）"
    )

    # 画面侧只调这三个口（单向缝），不自己碰 AudioContext。
    for name in ("deathSfxPlay", "deathSfxStop"):
        assert f"typeof {name} === \"function\"" in death, (
            f"fx-death.js 要按 typeof 守卫调 {name}（缺 sfx-ui.js 的探针里仍是一份独立排播）"
        )
    assert "AudioContext" not in death, "画面侧不碰音频 API（音效层的事）"


def test_the_new_families_never_touch_the_full_screen_flash() -> None:
    """spec §硬约束：铁索 / 阵亡 / 卡牌语音**都没有整屏闪**——不碰 `.fx-flash` / 表壳 `filter`。

    `.fx-flash` 是**属性伤害**的整屏闪通道（ADR-021 的着色子层）；`#table-shell` 的 `filter`
    是**无属性** 3 / 4 档的灰阶窗口。两张新覆盖层与语音各占一条通道，把它们接到整屏闪上
    等于让一次横置 / 一次阵亡顺手闪一下全屏——三套演出都不做，故逐文件钉住零引用。
    """
    for name in (
        "fx-chain.js",
        "sfx-chain.js",
        "fx-death.js",
        "sfx-death.js",
        "sfx-voices.js",
    ):
        text = _strip_js_comments((STATIC_DIR / name).read_text(encoding="utf-8"))
        for token in ("fx-flash", "table-shell", "style.filter"):
            assert token not in text, (
                f"{name} 不许碰整屏闪 / 表壳 filter（spec §硬约束：这三套演出都没有整屏闪）："
                f"命中 {token!r}"
            )


# ---------------------------------------------------------------------------
# chain-death-voices 票 03：卡牌语音（`sfx-voices.js` + `static/voices/` + 三条接线）
#
# 机械的四半：
# 1. **映射表是唯一真源**：`CARD_VOICES` 覆盖 160 档全部 43 类（期望牌型清单从 `sgs.data` 读，
#    不手抄），有素材的恰好 20 类、`null` 的恰好 23 类（22 种装备是预期终态 + 【桃】待补）；
#    108 档的 32 类全在表内（160 是超集，两份 profile 各跑一次）。
# 2. **资产对表**：表里每个文件名都在 `static/voices/` 下存在；目录里没有孤儿（每条都被引用）；
#    `voices/README.md` 是同一张表（逐行比对）。
# 3. **接线**（单向缝 + 同一游标）：`session.js` 收进 `state.cardEvents`、在
#    `fireDamageEventsThrough` 之后调 `fireCardEventsThrough`、换局 `SgsCardVoice.stopAll()`；
#    `sfx-ui.js` 把 `unlock` / `mute` 单向灌进去，语音模块不读开关的内部状态。
# 4. **播放口径的静态半边**：静音先于建 `Audio`、每条 `new Ctor(src)` 自起一路、`stopAll`
#    才 `pause`；`.mp3` 的 MIME 在 `serve.py` 的 `_CONTENT_TYPES` 里。
#
# 行为（到点播 / 两条重叠 / 转化与虚拟 / 开关 / 未知与 null / 换局）归烟测 `U-VOICE-01..`。
# ---------------------------------------------------------------------------

_SFX_VOICES_JS = STATIC_DIR / "sfx-voices.js"
_VOICES_DIR = STATIC_DIR / "voices"

# 期望牌型清单**从数据读**（`sgs.data` 的牌型表 + 各牌堆档的 `expect.by_type`），不在测试里
# 手抄一份 43 行的名单——漂了就是这条用例红，而不是"测试跟着抄"。
_DECK_PROFILES = ("standard_108", "standard_ex_160")

_VOICE_README_ROW = re.compile(
    r"^\| `(?P<type>[a-z_]+)` \| (?P<name>[^|]+?) \| (?P<male>[^|]+?) \| (?P<female>[^|]+?) "
    r"\| (?P<note>[^|]+?) \|\s*$",
    re.M,
)


def _inner_js_function(source: str, name: str) -> str:
    """取**缩进一层**的函数正文（`sfx-voices.js` 的函数都住在 IIFE 里，收尾是行首两个空格 + `}`）。"""
    match = re.search(rf"^  function {name}\((?P<args>[^)]*)\) \{{(?P<body>.*?)\n  \}}", source, re.S | re.M)
    assert match is not None, f"sfx-voices.js 里找不到 IIFE 内的函数 {name}()"
    return match.group("body")


def _readme_voices() -> dict[str, dict[str, str | None]]:
    """`voices/README.md` 那张表（`—` = `null`）；形状与 `CARD_VOICES` 逐键可比。"""
    def cell(text: str) -> str | None:
        value = text.strip().strip("`")
        return None if value in ("—", "-", "") else value

    rows: dict[str, dict[str, str | None]] = {}
    for match in _VOICE_README_ROW.finditer(
        (_VOICES_DIR / "README.md").read_text(encoding="utf-8")
    ):
        if match.group("type") == "card_type":  # 表头那一行
            continue
        rows[match.group("type")] = {
            "male": cell(match.group("male")),
            "female": cell(match.group("female")),
        }
    return rows


_CARD_VOICE_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const played = [];
class FakeAudio {
  constructor(src) {
    this.src = src;
    this.volume = 1;
    this.paused = false;
    played.push(this);
  }
  play() { return Promise.resolve(); }
  pause() { this.paused = true; }
  addEventListener() {}
}

const window = { Audio: FakeAudio };
const context = vm.createContext({ window, console, Date });
context.globalThis = context;
vm.runInContext(readFileSync("tools/sgs_web/static/sfx-voices.js", "utf8"), context, {
  filename: "sfx-voices.js",
});
const report = vm.runInContext(`(() => {
  const V = SgsCardVoice;
  const mutedByDefault = V.play({ card_type: "slash", gender: "male" }) === null;
  V.unlock();
  V.mute(false);
  const noGender = V.play({ card_type: "slash" });
  const noGenderSrc = noGender && noGender.src;
  const female = V.play({ card_type: "slash", gender: "female" });
  const femaleSrc = female && female.src;
  /* 评审修复 FIX 6：card_name / virtual 的读取方 = last() 读数（不只是载荷测试）。
   * 这一条是转化 / 纯虚拟的产物事实（闪当杀），触发仍只按 type / gender 查表。 */
  const product = V.play({
    card_type: "slash", gender: "male", card_name: "杀（龙胆）", virtual: true,
  });
  const productSrc = product && product.src;
  const last = V.last();
  const silentRow = V.play({ card_type: "peach", gender: "male" });
  const unknownType = V.play({ card_type: "no_such_card_type" });
  const counted = V.count();
  V.mute(true);
  return JSON.stringify({
    table: V.CARD_VOICES,
    types: Object.keys(V.CARD_VOICES),
    api: ["play", "stopAll", "mute", "unlock", "last", "count"].map((k) => typeof V[k]),
    bareGlobal: typeof SgsCardVoice,
    fallback: {
      mutedByDefault: mutedByDefault,
      noGenderSrc: noGenderSrc,
      femaleSrc: femaleSrc,
      silentRowIsNull: silentRow === null,
      unknownTypeIsNull: unknownType === null,
      counted: counted,
    },
    product: {
      src: productSrc,
      last: last,
      lastKeys: last ? Object.keys(last).sort() : [],
    },
  });
})()`, context);
console.log(report);
"""


def _card_voice_probe() -> dict:
    """跑真 `sfx-voices.js`（node vm + 假 `Audio`），拿回映射表与模块形状。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "card_voice_probe.mjs"
        probe.write_text(_CARD_VOICE_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"卡牌语音探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def card_voice_probe() -> dict:
    """一次探针跑完，映射表 / 资产 / README 三条用例共用。"""
    return _card_voice_probe()


def test_the_card_voice_table_covers_the_160_deck_and_the_assets_match_it(
    card_voice_probe: dict,
) -> None:
    """映射表 = 160 档全部 43 类；有素材恰好 20 类、`null` 恰好 23 类；资产与表一一对应。"""
    from sgs.data import load_content_pack

    pack = load_content_pack(deck_profile="standard_108")
    card_types = {card_type.id for card_type in pack.card_types}
    equipment = {
        card_type.id
        for card_type in pack.card_types
        if str(getattr(card_type.category, "value", card_type.category)) == "equipment"
    }
    table = card_voice_probe["table"]
    assert set(table) == card_types, (
        "映射表必须覆盖牌型表的全部 43 类（不许漏项）："
        f"缺 {sorted(card_types - set(table))}；多 {sorted(set(table) - card_types)}"
    )
    assert len(equipment) == 22, f"装备牌型应为 22 种：{sorted(equipment)}"

    # 每份牌堆档的牌型都落在表内（160 是超集；108 的 32 类也必须全在）。
    for profile in _DECK_PROFILES:
        deck_types = set(pack.decks[profile].expect.by_type)
        assert deck_types <= set(table), (
            f"{profile} 的牌型里有 {sorted(deck_types - set(table))} 不在映射表里"
        )
    assert len(set(pack.decks["standard_108"].expect.by_type)) == 32
    assert len(set(pack.decks["standard_ex_160"].expect.by_type)) == 43

    voiced = {
        type_id
        for type_id, row in table.items()
        if isinstance(row.get("male"), str) or isinstance(row.get("female"), str)
    }
    silent = set(table) - voiced
    assert len(voiced) == 20, f"有素材的必须恰好 20 类（40 条 = 20 × 男 / 女）：{sorted(voiced)}"
    assert len(silent) == 23, f"null 的必须恰好 23 类：{sorted(silent)}"
    # `null` 的 23 类 = 22 种装备（**预期终态**，SGS OL 本就没有装备卡语音）+ 【桃】（待补素材）。
    assert silent == equipment | {"peach"}, (
        "静默的只有「22 种装备 + 【桃】」两类：装备是预期终态、桃是待补素材；"
        f"多出来的静默项：{sorted(silent - equipment - {'peach'})}"
    )
    assert "peach" in silent, "【桃】先静默等素材（用户 2026-11-11）"
    for type_id in voiced:
        row = table[type_id]
        assert row["male"] == f"{type_id}_male.mp3", f"{type_id} 的男声文件名：{row['male']}"
        assert row["female"] == f"{type_id}_female.mp3", f"{type_id} 的女声文件名：{row['female']}"
    for type_id in silent:
        assert table[type_id] == {"male": None, "female": None}, (
            f"{type_id} 没有素材 ⇒ 两格都显式 null（不许漏项）：{table[type_id]}"
        )

    # 资产对表：表里每个文件名都存在；目录里没有孤儿（每条 mp3 都被表引用）。
    referenced = {
        row[gender] for row in table.values() for gender in ("male", "female") if row[gender]
    }
    on_disk = {path.name for path in _VOICES_DIR.glob("*.mp3")}
    assert referenced <= on_disk, (
        f"映射表引用了不存在的素材：{sorted(referenced - on_disk)}"
    )
    assert on_disk <= referenced, f"目录里的孤儿素材（没被表引用）：{sorted(on_disk - referenced)}"
    assert len(on_disk) == 40, f"素材必须恰好 40 条（20 类 × 男 / 女）：{len(on_disk)}"

    # README 是同一张表（逐键比对，漂移即红）。
    readme = _readme_voices()
    assert readme == table, (
        "voices/README.md 的覆盖表必须与 sfx-voices.js 的 CARD_VOICES 逐格相同："
        f"README 缺 {sorted(set(table) - set(readme))}；多 {sorted(set(readme) - set(table))}；"
        f"值不同 {sorted(k for k in set(table) & set(readme) if table[k] != readme[k])}"
    )


def test_the_card_voice_module_plays_each_asset_on_its_own_behind_the_switch(
    card_voice_probe: dict,
) -> None:
    """模块形状与播放口径的静态半边：静音先于建 `Audio`、每条自起一路、`stopAll` 才 `pause`。"""
    assert card_voice_probe["api"] == ["function"] * 6, (
        f"模块的六个入口都要导出：{card_voice_probe['api']}"
    )
    assert card_voice_probe["bareGlobal"] == "object", (
        "顶层的 `var SgsCardVoice = window.SgsCardVoice;` 桥接要在（session.js 按裸名调它）"
    )
    voices = _SFX_VOICES_JS.read_text(encoding="utf-8")
    assert voices.index("var CARD_VOICES") < voices.index("function play("), (
        "映射表必须在播放函数**之前**（表 → 查表 → 播的次序）"
    )
    play = _inner_js_function(voices, "play")
    muted_at = play.index("if (muted) return null;")
    build_at = play.index("new Ctor(src)")
    assert muted_at < build_at, "静音检查必须在建 `Audio` 之前（关「♪」⇒ 一条都不建、不发请求）"
    assert "stopAudio" not in play and "stopAll" not in play, (
        "播放里不许停任何一条：后一条从不打断前一条（用户口径「可以重复、不会被打断」）"
    )
    assert "pending.catch(function () {})" in play, "自动播放被拒的 Promise 要静默吞掉"
    assert "audio.volume = VOLUME" in play, "音量只有一个数（响度归一不在本票）"
    stop = _inner_js_function(voices, "stopAll")
    assert "stopAudio(" in stop, "`stopAll` 才 pause 存活的那几路"
    mute = _inner_js_function(voices, "mute")
    assert "stopAll()" in mute, "关「♪」= 停掉全部存活语音并清表"

    # `gender` 空（无武将模式 / 老载荷）⇒ 前端回落 `male`：服务端**不猜**（`card_events` 下发
    # `null`），回落只发生在选文件名这一处（spec §Solution 3 / §演出口径「卡牌语音」）。
    # 探针驱动真 `play()`：缺省静音不建、无性别走男声、`female` 走女声、空表项 / 未知类型静默。
    fallback = card_voice_probe["fallback"]
    assert fallback["mutedByDefault"] is True, "缺省静音：第一条 `play()` 连 Audio 都不建"
    assert fallback["noGenderSrc"] == "voices/slash_male.mp3", (
        f"gender 空必须回落 male：{fallback['noGenderSrc']}"
    )
    assert fallback["femaleSrc"] == "voices/slash_female.mp3", (
        f"gender=female 走女声：{fallback['femaleSrc']}"
    )
    assert fallback["silentRowIsNull"] is True, "表项 null（【桃】待补）⇒ 一声不出、返回 null"
    assert fallback["unknownTypeIsNull"] is True, "未知牌型 ⇒ 静默，不退回别的牌型"
    assert fallback["counted"] == 3, (
        f"count() 只数真的出了声的三条（无性别 + 女声 + 产物读数那条）：{fallback['counted']}"
    )

    # 评审修复 FIX 6：`card_name` / `virtual` 的**读取方**就是 `last()` 读数（不再只是载荷
    # 测试在读）；形状即 `{type, gender, name, virtual, src, at}`，触发仍只按 type / gender。
    product = card_voice_probe["product"]
    assert product["src"] == "voices/slash_male.mp3", (
        f"产物那条按产物牌型 + 性别查表（不读来源）：{product['src']}"
    )
    assert product["lastKeys"] == ["at", "gender", "name", "src", "type", "virtual"], (
        f"last() 的读数形状必须是 {{type, gender, name, virtual, src, at}}：{product['lastKeys']}"
    )
    assert (product["last"]["type"], product["last"]["gender"]) == ("slash", "male")
    assert product["last"]["name"] == "杀（龙胆）", (
        f"last().name 要带出事件里的产物牌名（card_name）：{product['last']}"
    )
    assert product["last"]["virtual"] is True, (
        f"last().virtual 要带出事件里的转化 / 虚拟事实：{product['last']}"
    )
    assert isinstance(product["last"]["at"], int) and product["last"]["at"] > 0, (
        f"last().at 仍是毫秒时间戳：{product['last']}"
    )

    # `.mp3` 的 MIME（缺了会回 `application/octet-stream`）。
    if str(REPO_ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "tools"))
    from sgs_web.serve import _CONTENT_TYPES

    assert _CONTENT_TYPES.get(".mp3") == "audio/mpeg", (
        f"serve.py 的 _CONTENT_TYPES 必须给 .mp3 一个 audio/mpeg：{_CONTENT_TYPES}"
    )


def test_the_card_voice_wiring_is_one_way_from_sfx_ui_and_rides_the_same_seq_cursor() -> None:
    """接线（票面 §做法 3）：同一 seq 游标、换局收声、开关与手势**单向**从 `sfx-ui.js` 灌入。"""
    session = _SESSION_JS.read_text(encoding="utf-8")
    sfx_ui = (STATIC_DIR / "sfx-ui.js").read_text(encoding="utf-8")
    core = (STATIC_DIR / "core.js").read_text(encoding="utf-8")
    voices = _SFX_VOICES_JS.read_text(encoding="utf-8")

    # 1. 待触发队列（与 `state.damageEvents` 同姿态）与它的两处生命周期。
    assert re.search(r"^  cardEvents: \[\],", core, re.M), "core.js 的 state 要有 cardEvents: []"
    apply_view = _js_function(session, "applyView")
    assert "for (const event of body.card_events || []) state.cardEvents.push(event);" in apply_view, (
        "applyView 必须把载荷收进队列"
    )
    assert "state.cardEvents = [];" in apply_view, "换会话要清空（上一局的事实不属于新局）"
    commit = _js_function(session, "commitView")
    assert "SgsCardVoice.stopAll();" in commit, "换局收声：上一局的语音不拖进新局"
    assert "state.cardEvents = [];" in commit

    # 2. 触发点 = 与伤害演出**同一条 seq 游标**（紧挨它的下一行）。
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = match.group("body")
    damage_at = body.find("fireDamageEventsThrough(record.seq)")
    voice_at = body.find("fireCardEventsThrough(record.seq)")
    assert damage_at >= 0 and voice_at > damage_at, (
        "卡牌语音必须挂在伤害演出的下一行（同一游标、同一批记录）"
    )

    # 3. 排播钩子住在语音模块（与伤害 / 铁索同姿态）：到点逐条播、其余留队。
    hook = re.search(
        r"^var fireCardEventsThrough = function \(seq\) \{(?P<body>.*?)\n\};", voices, re.S | re.M
    )
    assert hook is not None, "sfx-voices.js 要提供顶层 `var fireCardEventsThrough` 排播钩子"
    hook_body = hook.group("body")
    assert "state.cardEvents" in hook_body and "SgsCardVoice.play(event)" in hook_body
    assert "setTimeout" not in hook_body, "语音不排播：到点直接播，可重叠"

    # 4. 单向缝：开关 / 手势从 sfx-ui.js 灌进去；语音模块不读它的内部状态。
    modules = _js_function(sfx_ui, "damageSfxEnsureModules")
    assert "cardVoiceUnlock()" in modules, "手势要在同一个 tick 里灌进去"
    mute = _js_function(sfx_ui, "damageSfxMute")
    assert "cardVoiceMute(" in mute, "开关要灌进去（关 = 停掉全部存活语音）"
    unlock = _js_function(sfx_ui, "damageSfxUnlock")
    assert "cardVoiceUnlock()" in unlock, "手势解锁在同一处（缺省静音时的第一次点击也算）"
    for leaked in ("damageSfxOn", "damageSfxMute", "damageSfxSetEnabled", "localStorage"):
        assert leaked not in _strip_js_comments(voices), (
            f"语音模块不许读 sfx-ui.js 的内部状态（{leaked}）：缝只朝一个方向"
        )


def test_the_playback_hooks_fire_in_damage_card_chain_death_order() -> None:
    """`stepPlayback` 的演出钩子次序 = **回复 → 伤害 → 失去体力 → 卡牌语音 → 铁索 → 阵亡**。

    票 01 / 票 03 落笔时各自写过「紧挨 `fireDamageEventsThrough(record.seq)` 的下一行」；
    后来卡牌语音插在伤害与铁索之间、阵亡又接在铁索之后——于是「下一行」只对卡牌语音成立。
    这里按**真实相邻次序**一次钉住，并禁止相邻两条之间再插别的 `fire*EventsThrough`
    钩子（acceptance-map §1.1 的载体）：次序被改、或又插一条演出钩子，这条就红。

    vital-fx 票 02 有意加了两条（这是本票的交付，不是漂移）：**回复**的钩子接在
    `applyRecordEffects` 之后（它读记录本身），**失去体力**的钩子紧挨伤害那条（同一个 seq
    游标）。两条都钉进来，下一次再插钩子照样红。
    """
    session = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    # 注释不算调用点（`CODING_STANDARDS.md` §1）：先剥注释，再数真实的 `fire*EventsThrough(`。
    body = _strip_js_comments(match.group("body"))

    calls = [
        found.group(1) for found in re.finditer(r"(fire[A-Za-z]+EventsThrough)\(", body)
    ]
    assert "fireDamageEventsThrough" in calls, (
        f"伤害演出的钩子不在 stepPlayback 里：{calls}"
    )
    # 从**回复**那一条起切（技能演出钩子排在它之后、伤害之前，不在这条的契约里）。
    order = calls[calls.index("fireVitalEventsThrough"):]
    assert order == [
        "fireVitalEventsThrough",
        "fireSkillEventsThrough",
        "fireDamageEventsThrough",
        "fireHpLossEventsThrough",
        "fireCardEventsThrough",
        "fireChainEventsThrough",
        "fireDeathEventsThrough",
    ], (
        "stepPlayback 的演出钩子必须依次是 回复 → 技能 → 伤害 → 失去体力 → 卡牌语音 → "
        f"铁索 → 阵亡，且相邻两条之间不许插别的 `fire*EventsThrough`，实际：{calls}"
    )


# ---------------------------------------------------------------------------
# vital-fx 票 01：失去体力事实只落进前端 state（本票不排播、不画）
# ---------------------------------------------------------------------------


def test_life_loss_facts_land_in_the_frontend_state_without_any_playback() -> None:
    """`applyView` 收 `body.life_loss_events` ⇒ `state.lifeLossEvents`；换局清空。

    票面 §做法 5：本票**只做事件源**——不排播、不画、不加演出钩子（排播层 `vital-fx.js`
    是票 02，本票不许凭空多出消费方）。所以这里钉三处接线，再反向钉住"伤害 / 舞台两层都
    不认识这个队列"与"session.js 里没有第二个读它的地方"。

    **票 02 已经接上消费方**（`vital-fx.js:fireHpLossEventsThrough`），但消费方**不在**
    `session.js` 里读这张表（钩子只传 seq），所以这条用例一个字不用改：它钉的仍是
    "事实落进 state 这几处接线"与"伤害 / 动效 / 舞台三层不认识这个队列"。
    """
    session = _SESSION_JS.read_text(encoding="utf-8")
    apply_view = _js_function(session, "applyView")

    assert "if (!Array.isArray(state.lifeLossEvents)) state.lifeLossEvents = [];" in apply_view, (
        "`applyView` 要照 `state.damageEvents` 的姿态惰性初始化队列"
    )
    assert (
        "for (const event of body.life_loss_events || []) state.lifeLossEvents.push(event);"
        in apply_view
    ), "`applyView` 必须把载荷里的 `body.life_loss_events` 收进队列"
    assert "state.lifeLossEvents = [];" in apply_view, (
        "换会话要清空（上一局的事实不属于新局）"
    )

    commit = _js_function(session, "commitView")
    assert "state.lifeLossEvents = [];" in commit, (
        "换局的另一处同样清空（与 `state.damageEvents` 同一块，别只在 applyView 清一半）"
    )

    body = _strip_js_comments(session)
    touched = [line.strip() for line in body.splitlines() if "lifeLossEvents" in line]
    assert len(touched) == 4, (
        "`session.js` 里只许有这几处读写（换会话复位 / 惰性初始化 + push / commit 清空）："
        f"本票不排播、不画，多出来的引用就是提前接上了还没到的层：{touched}"
    )

    for name in ("effects.js", "damage-fx.js", "fx-core.js", "table.js"):
        source = _strip_js_comments((STATIC_DIR / name).read_text(encoding="utf-8"))
        assert "lifeLossEvents" not in source, (
            f"{name} 不认识 `state.lifeLossEvents`：失去体力的排播 / 画面是票 02 / 03，"
            "伤害 / 动效两层一个字都不动"
        )


# ---------------------------------------------------------------------------
# vital-fx 票 02：体力演出的排播缝（`vital-fx.js`）
#
# 机械的四半（行为归烟测 `U-VITAL-02..`）：
# 1. **脚本清单 / 层 / 样式**：`vital-fx.js` 紧挨 `damage-fx.js`（且排在 `sfx-ui.js` 之前，
#    后者要读 `vitalFxCapFor` 定声道池长）；`#t-vital-fx.vital-fx-layer` 是壳里又一张透明
#    覆盖层（z 与伤害层同档、DOM 在后 ⇒ 画在伤害层之上），**不挂 `filter`、不碰 `.fx-flash`**。
# 2. **家族表 + 三个只读口**（node 探针驱动真函数）：`vitalFxRendererFor` /
#    `vitalFxCapFor`（1–8、缺省 2、钳在读者侧）/ `vitalFxHoldFor`（HP_LOSS 恒 false、
#    HEAL 3+ true）。
# 3. **两条事件源 + 一把闸门 + 时停登记**：`stepPlayback` 里
#    `fireHpLossEventsThrough(record.seq)` 紧挨伤害那条、`fireVitalEventsThrough(record)`
#    在 `applyRecordEffects` 之后；`registerPlaybackHold("vital", vitalFxPlaybackHold)`；
#    三处收尾钩子（`renderTable` / `commitView` / 换局清 `state.lifeLossEvents`）。
# 4. **音效接缝**：`VITAL_SFX_ENGINES` + 四个 `vitalSfx*` 调用点，缺件静默（票 03 / 04 之前
#    两个模块都不存在，这一条今天就生效）。
# ---------------------------------------------------------------------------

_VITAL_FX_JS = STATIC_DIR / "vital-fx.js"

# vital-fx 票 03 / 04 的四个模块（画面 / 音效各两份）。这张表是"新模块必须登记"的落点：
# 它逐行断言"清单里有、磁盘上有、挂了全局名、不发请求、不接管 DOM"。
# 票 03 追加失去体力两行；票 04 追加回复体力两行（spec §硬约束把四个全局名逐名列了出来）。
_VITAL_FX_GLOBALS: dict[str, str] = {
    "fx-hp-loss.js": "SgsHpLossFX",
    "sfx-hp-loss.js": "SgsHpLossSfx",
    "fx-heal.js": "SgsHealFX",
    "sfx-heal.js": "SgsHealSfx",
}


def test_the_vital_fx_playback_layer_is_listed_next_to_damage_fx() -> None:
    """`vital-fx.js` 进清单、紧挨 `damage-fx.js`，且仍是一份**独立**的普通严格脚本。

    「独立」是 spec §硬约束：它有自己的家族表 / cap 口 / 队列 / 时停判据，**不许**借伤害
    家族的三个函数（`damageFxNatureKind` / `damageFxRendererFor` / `damageFxCapFor`）——
    那样两条家族的 cap / 错开 / 灰阶口径会互相打架。三个借来的只读小工具（布局 / 时钟）
    都按 `typeof` 在**调用时**解析，与 `damage-fx.js` 同一个方向。
    """
    srcs = _script_srcs()
    assert "vital-fx.js" in srcs, f"index.html 必须加载 vital-fx.js：{srcs}"
    assert (STATIC_DIR / "vital-fx.js").is_file(), "static/ 里缺少 vital-fx.js"
    assert srcs.index("vital-fx.js") == srcs.index("damage-fx.js") + 1, (
        f"vital-fx.js 必须紧挨 damage-fx.js（同姿态的独立排播层）：{srcs}"
    )
    assert srcs.index("vital-fx.js") < srcs.index("sfx-ui.js"), (
        "vital-fx.js 要排在 sfx-ui.js 之前（后者读 vitalFxCapFor 定声道池长）"
    )

    text = _VITAL_FX_JS.read_text(encoding="utf-8")
    assert _LEADING_COMMENT.sub("", text, count=1).startswith('"use strict";'), (
        "vital-fx.js 的首条语句必须是 \"use strict\";"
    )
    assert not re.search(r"^\s*(?:import|export)\b", text, re.M), (
        "vital-fx.js 是普通脚本：不许 ESM"
    )
    body = _strip_js_comments(text)
    assert not re.search(r"\b(?:fetch|XMLHttpRequest)\s*\(", body), "vital-fx.js 不发请求"
    assert not re.search(r"\bdocument\.(?:querySelector|getElementById)\b", body), (
        "vital-fx.js 不自己取 DOM：层用 `$(\"t-vital-fx\")`，布局工具借 effects.js"
    )
    for name in ("damageFxNatureKind", "damageFxRendererFor", "damageFxCapFor"):
        assert name not in body, (
            f"vital-fx.js 不许调 {name}：两条家族不共用户口（spec §硬约束）"
        )
    assert re.search(r'^const VITAL_FX_FAMILIES = \{$', body, re.M), (
        "家族表必须是这一份文件里的唯一查表处（`const VITAL_FX_FAMILIES = {`）"
    )
    assert re.search(r'^  HP_LOSS:\s+\{', body, re.M) and re.search(r'^  HEAL:\s+\{', body, re.M), (
        "家族表有且只有 HP_LOSS / HEAL 两行"
    )
    assert 'registerPlaybackHold("vital", vitalFxPlaybackHold);' in body, (
        "文件末尾要把 `vitalFxPlaybackHold` 登记进排播侧的暂停表（登记名 = vital）"
    )
    assert '$("t-vital-fx")' in body, "层节点按 id 取（`$` 是 core.js 的读取口）"


def test_vital_fx_scripts_keep_their_global_names_and_stay_static() -> None:
    """票 03 / 04 的四个模块（画面 / 音效）逐行登记在 `_VITAL_FX_GLOBALS` 里。

    票 02 建这张表时它是空的（四个模块还不存在）；票 03 补上失去体力的两行、票 04 补上回复体力
    的两行之后，它逐行断言"清单里有、磁盘上有、顺序在 `sfx-ui.js` 之前、挂了全局名、不发请求、
    不接管 DOM"。四个模块现在都在清单里（票 04 起 `fx-heal.js` / `sfx-heal.js` 允许且必须加载）。
    """
    srcs = _script_srcs()
    for name, global_name in _VITAL_FX_GLOBALS.items():
        assert name in srcs, f"index.html 必须加载 {name}：{srcs}"
        assert (STATIC_DIR / name).is_file(), f"static/ 里缺少 {name}"
        assert srcs.index(name) < srcs.index("sfx-ui.js"), (
            f"{name} 必须在 sfx-ui.js 之前（音效 / 声道池按名取它）"
        )
        text = (STATIC_DIR / name).read_text(encoding="utf-8")
        assert _LEADING_COMMENT.sub("", text, count=1).startswith('"use strict";'), (
            f"{name} 的首条语句必须是 \"use strict\";"
        )
        assert re.search(rf"global\.{global_name}\s*=\s*\{{", text), (
            f"{name} 必须把入口挂在 window.{global_name}"
        )
        assert not re.search(r"\b(?:fetch|XMLHttpRequest)\s*\(", text), (
            f"{name} 是纯静态脚本：不发请求"
        )
        assert not re.search(r"\bdocument\.(?:querySelector|getElementById)\b", text), (
            f"{name} 不接管 DOM（覆盖层与挂载是排播层的事）"
        )


def test_the_vital_fx_layer_is_its_own_overlay_next_to_the_damage_layer() -> None:
    """`#t-vital-fx` / `.vital-fx-layer` 是**独立**的覆盖层，且**没有**整屏闪。

    - 层：壳里的常驻空 div、`pointer-events: none`、z 与 `.damage-fx-layer` 同档（19）；
      DOM 排在 `#t-damage-fx` 之后 ⇒ 同 z 下画在伤害层之上（spec §做法 4）；
    - **不碰 `.fx-flash`、不挂 `filter`**：素材页这两套演出没有整屏闪（spec §硬约束）；
    - 层里只放**一张**可见 canvas（跨演出复用），离屏舞台按槽复用、数量随在播数增长。
    """
    html = INDEX_HTML.read_text(encoding="utf-8")
    tag = re.search(r"<div\b[^>]*\bid=\"t-vital-fx\"[^>]*>", html)
    assert tag is not None, "index.html 里缺少体力演出层 #t-vital-fx"
    assert re.search(r'class="[^"]*vital-fx-layer', tag.group(0)), tag.group(0)
    assert "aria-hidden" in tag.group(0), "体力演出是纯视觉层，必须 aria-hidden"
    assert html.index('id="table-shell"') < html.index('id="t-vital-fx"'), (
        "体力层必须住在 .table-shell 里（座位矩形按壳内坐标量）"
    )
    assert html.index('id="t-damage-fx"') < html.index('id="t-vital-fx"'), (
        "层排在 #t-damage-fx 之后（同 z-index 档 ⇒ 体力画在伤害之上）"
    )
    assert re.search(r'<div\b[^>]*\bid="t-vital-fx"[^>]*></div>', html), (
        "层必须仍是空的常驻 div（画布由排播层挂 / 摘）"
    )

    rule = _css_rule(".vital-fx-layer")
    for pattern, why in (
        (r"position:\s*absolute", "绝对定位"),
        (r"inset:\s*0", "铺满表壳"),
        (r"pointer-events:\s*none", "不吃指针"),
        (r"overflow:\s*hidden", "画布裁在壳内"),
    ):
        assert re.search(pattern, rule), f"层必须{why}：{rule}"
    assert _layer_z_index(".vital-fx-layer") == _layer_z_index(".damage-fx-layer") == 19, (
        "体力层与伤害层同 z-index 档（19）：DOM 在后 ⇒ 画在上面"
    )
    assert _layer_z_index(".vital-fx-layer") < _layer_z_index(".card-preview"), (
        "层在卡牌预览（55）之下"
    )
    assert re.search(r"\.vital-fx-layer canvas\s*\{[^}]*width:\s*100%", _CSS_TEXT), (
        "层里那张 canvas 按层的 CSS 尺寸铺满（view 与 dpr 由 JS 算）"
    )

    body = _strip_js_comments(_VITAL_FX_JS.read_text(encoding="utf-8"))
    assert "fx-flash" not in body, "这两套演出没有整屏闪：不碰 `.fx-flash`"
    assert not re.search(r"\.style\.filter\b|\bfilter\s*:", body), (
        "这两套演出没有整屏闪：不挂 `filter`（spec §硬约束；`Array.filter` 不算）"
    )
    assert "grayscale" not in body, "没有整屏灰阶"


def test_anim_ini_registers_the_vital_fx_concurrency_keys() -> None:
    """`hp_loss_fx_concurrency` / `heal_fx_concurrency`（1–8、缺省 2）进 `anim.ini`。

    两份键与三个伤害并发表同一口径：缺键 / 非数字回退缺省 2、`0` / 负数钳到 1、`> 8` 钳到 8
    ——钳制在**读者侧**（`vitalFxCapFor`），`anim.js` 只搬数字。新前缀必须同批登记为外来键，
    否则 `anim.js` 会把它们当未知键删掉、用户改了不生效。
    """
    entries = _ini_entries()
    for key, family in (("hp_loss_fx_concurrency", "HP_LOSS"), ("heal_fx_concurrency", "HEAL")):
        assert key in entries, f"anim.ini 缺少并发表键 {key}（{family} 的 cap）"
        assert float(entries[key]) == 2, f"{key} 的缺省是 2（spec §多目标）：{entries[key]}"
    ini_text = _ANIM_INI.read_text(encoding="utf-8")
    for key, family in (("hp_loss_fx_concurrency", "HP_LOSS"), ("heal_fx_concurrency", "HEAL")):
        assert re.search(rf"^{key}\s*=\s*2\s+#\s*\S+", ini_text, re.M), (
            f"{key} 必须带一行注释（用户改得动）：{entries[key]}"
        )
    assert "并发表" in ini_text, "并发表那一块的口径要写清（1–8 / 缺省 2 / 钳在读者侧）"

    anim_js = _ANIM_JS.read_text(encoding="utf-8")
    prefixes = re.search(r"^const ANIM_FOREIGN_KEY_PREFIXES = \[(?P<body>[^\]]*)\];$", anim_js, re.M)
    assert prefixes is not None, "anim.js 里找不到 ANIM_FOREIGN_KEY_PREFIXES"
    for prefix in ("hp_loss_fx_", "heal_fx_"):
        assert f'"{prefix}"' in prefixes.group("body"), (
            f"anim.js 必须把 {prefix} 登记成外来键（默认值 / 钳制在票 03 / 04 的模块里）"
        )


_VITAL_FX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const ini = readFileSync("tools/sgs_web/static/anim.ini", "utf8");

/* 每份脚本单独编译、同一个 context 的全局词法作用域跨脚本可见——与页面一致的加载方式。
 * 每个 ini 变体起**新的** sandbox（= 用户改完文件刷新页面）：`animLoadConfig` 只做
 * `Object.assign`，上一份表里留下的键不会因为这一份删了而消失。 */
function makeSandbox(iniText = ini) {
  const noteNode = { textContent: "", hidden: true };
  const context = vm.createContext({
    console,
    setTimeout,
    clearTimeout,
    fetch: async () => ({ ok: true, text: async () => iniText }),
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
  for (const name of ["anim.js", "vital-fx.js"]) {
    vm.runInContext(readFileSync(`tools/sgs_web/static/${name}`, "utf8"), context, { filename: name });
  }
  return context;
}

const context = makeSandbox();
const report = await vm.runInContext(
  `(async () => {
     await animReady;
     const row = (name) => {
       const spec = VITAL_FX_FAMILIES[name];
       return {
         renderer: spec.renderer,
         fallback: spec.fallback,
         holdsAt: String(spec.holdsAt),
         cap: spec.cap,
         stagger: spec.stagger,
       };
     };
     // 画面模块按**全局名**调用时解析：缺件 ⇒ null（不播，事件仍被消费）。
     const missing = vitalFxRendererFor("HP_LOSS");
     const unknownFamily = vitalFxFamilyFor("POISON");
     const stub = {
       create() {},
       TIMING_PREFIX: "hp_loss_fx_",
       TIMING_FIELDS: ["total", "fade"],
     };
     window.SgsHpLossFX = stub;
     const resolvedAtCallTime = vitalFxRendererFor("HP_LOSS") === stub;
     // 时间轴字段表：前缀 / 字段名都从画面模块读，缺键不硬造，非数字跳过。
     Object.assign(animConfig, {
       hp_loss_fx_3_total: 1800,
       hp_loss_fx_3_fade: 250,
       hp_loss_fx_2_total: "abc",
     });
     const timing = {
       three: vitalFxTiming("HP_LOSS", 3),
       two: vitalFxTiming("HP_LOSS", 2),
     };
     delete window.SgsHpLossFX;
     const afterDelete = vitalFxRendererFor("HP_LOSS");
     const withoutModule = vitalFxTiming("HP_LOSS", 3);
     // 时停判据：在播表 + 队列都算（回复 3+ 档串行，交接缝隙也不许漏冻）。
     const holdEmpty = vitalFxPlaybackHold();
     vitalPlays.push({ family: "HEAL", variant: 3 });
     const holdLiveHeal = vitalFxPlaybackHold();
     vitalPlays.length = 0;
     vitalQueue.push({ family: "HEAL", amount: 3 });
     const holdQueuedHeal = vitalFxPlaybackHold();
     vitalQueue.length = 0;
     vitalPlays.push({ family: "HP_LOSS", variant: 3 });
     const holdLiveHpLoss = vitalFxPlaybackHold();
     vitalPlays.length = 0;
     vitalQueue.push({ family: "HEAL", amount: 2 });
     const holdQueuedHealLight = vitalFxPlaybackHold();
     vitalQueue.length = 0;
     return JSON.stringify({
       families: Object.keys(VITAL_FX_FAMILIES),
       hpLoss: row("HP_LOSS"),
       heal: row("HEAL"),
       missing,
       unknownFamily,
       resolvedAtCallTime,
       afterDelete,
       timing,
       withoutModule,
       hold: {
         empty: holdEmpty,
         liveHeal: holdLiveHeal,
         queuedHeal: holdQueuedHeal,
         liveHpLoss: holdLiveHpLoss,
         queuedHealLight: holdQueuedHealLight,
         hpLossAlways: [vitalFxHoldFor("HP_LOSS", 1), vitalFxHoldFor("HP_LOSS", 3)],
         healByTier: [
           vitalFxHoldFor("HEAL", 1),
           vitalFxHoldFor("HEAL", 2),
           vitalFxHoldFor("HEAL", 3),
           vitalFxHoldFor("HEAL", 4),
         ],
         unknown: vitalFxHoldFor("POISON", 3),
       },
       variant: {
         one: vitalFxVariantOf(1),
         two: vitalFxVariantOf(2),
         three: vitalFxVariantOf(3),
         four: vitalFxVariantOf(4),
         zero: vitalFxVariantOf(0),
         junk: vitalFxVariantOf("abc"),
       },
       caps: {
         hpLoss: vitalFxCapFor("HP_LOSS"),
         heal: vitalFxCapFor("HEAL"),
         unknown: vitalFxCapFor("POISON"),
       },
       constants: {
         stagger: VITAL_FX_STAGGER_MS,
         concurrency: VITAL_FX_CONCURRENCY,
         capMin: VITAL_FX_CAP_MIN,
         capMax: VITAL_FX_CAP_MAX,
         maxVariant: VITAL_FX_MAX_VARIANT,
       },
       note: $("t-anim-note").textContent,
       animConfigKeys: Object.keys(animConfig),
     });
   })()`,
  context,
  { filename: "vital_fx_probe.js" },
);
console.log(report);
"""


def _vital_fx_probe() -> dict:
    """跑真 `anim.js` + 真 `vital-fx.js`（node vm），拿回家族表 / 三个只读口 / 时停判据。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "vital_fx_probe.mjs"
        probe.write_text(_VITAL_FX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"vital-fx 探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def vital_fx_probe() -> dict:
    """一次探针跑完，家族表 / cap / 时停几条用例共用。"""
    return _vital_fx_probe()


def test_the_vital_fx_family_table_is_the_only_lookup_and_its_rows_say_everything(
    vital_fx_probe: dict,
) -> None:
    """票面 §1：家族表两行、字段齐全；`holdsAt` 是时停的唯一真源。

    - `renderer` / `fallback` 存**全局名**（调用时解析）——两个画面模块是票 03 / 04 的文件，
      今天都不存在；
    - `holdsAt`：失去体力 `Infinity`（永不冻）、回复 `3`（3+ 档冻）；
    - `cap`：`anim.ini` 的键名，只在这张表里写一次；
    - `fallback: null` = 模块缺件就不播（与雷 / 火同姿态），事件仍被消费。
    """
    probe = vital_fx_probe
    assert probe["families"] == ["HP_LOSS", "HEAL"], f"家族表只有两行：{probe['families']}"
    assert probe["hpLoss"] == {
        "renderer": "SgsHpLossFX", "fallback": None, "holdsAt": "Infinity",
        "cap": "hp_loss_fx_concurrency", "stagger": True,
    }, f"失去体力那一行：{probe['hpLoss']}"
    assert probe["heal"] == {
        "renderer": "SgsHealFX", "fallback": None, "holdsAt": "3",
        "cap": "heal_fx_concurrency", "stagger": True,
    }, f"回复那一行：{probe['heal']}"

    # 全局名调用时解析：现在都没有模块 ⇒ 不播；装上就是它；卸掉又变回 null。
    assert probe["missing"] is None, "缺件必须返回 null（不播，事件仍被消费）"
    assert probe["unknownFamily"] is None, "未登记的家族没有 fallback"
    assert probe["resolvedAtCallTime"] is True, "`vitalFxRendererFor` 必须在调用时解析全局名"
    assert probe["afterDelete"] is None, "模块不在时不许记缓存（缺件 → 不播）"

    # 三条硬口径常量（票面 §1 / §2 / §3）。
    assert probe["constants"] == {
        "stagger": 120, "concurrency": 2, "capMin": 1, "capMax": 8, "maxVariant": 3,
    }, f"常量：{probe['constants']}"
    assert probe["variant"] == {
        "one": 1, "two": 2, "three": 3, "four": 3, "zero": 1, "junk": 1,
    }, f"档位换算（3+ 一律 3 档）：{probe['variant']}"
    # cap 键名只在家族表里写一次（读的一侧是 `vitalFxCapFor`）。
    body = _strip_js_comments(_VITAL_FX_JS.read_text(encoding="utf-8"))
    for key in ("hp_loss_fx_concurrency", "heal_fx_concurrency"):
        assert body.count(f'"{key}"') == 1, f"{key} 只许在家族表里出现一次：{body.count(key)}"


def test_the_vital_fx_hold_is_per_family_and_counts_plays_and_queue(vital_fx_probe: dict) -> None:
    """票面 §3：`vitalFxHoldFor(family, variant)` 的矩阵 + 原因串。

    - `HP_LOSS` **恒 false**（用户口径：失去体力不冻回放），1 档到 3+ 档都一样；
    - `HEAL` **3+ 档 true**、1 / 2 档 false；
    - `vitalFxPlaybackHold()` 返回原因串 `"heal:3"`，**在播表与队列都算**（票面 §3 / trap：
      重演出串行后"上一条摘层、下一条还没起"的缝隙也不许漏冻）。
    """
    hold = vital_fx_probe["hold"]
    assert hold["hpLossAlways"] == [False, False], f"失去体力永不冻：{hold['hpLossAlways']}"
    assert hold["healByTier"] == [False, False, True, True], (
        f"回复 1 / 2 档不冻、3+ 档冻：{hold['healByTier']}"
    )
    assert hold["unknown"] is False, "未登记家族不冻"
    assert hold["empty"] == "", "没有在播 / 排队 ⇒ 空串（不要求暂停）"
    assert hold["liveHeal"] == "heal:3", f"3+ 档回复在播：{hold['liveHeal']}"
    assert hold["queuedHeal"] == "heal:3", (
        f"排队里的 3+ 档回复也算（串行交接的缝隙不许漏冻）：{hold['queuedHeal']}"
    )
    assert hold["liveHpLoss"] == "", (
        f"失去体力在播也不许要求暂停（`hp-loss` 这个原因串永远不返回）：{hold['liveHpLoss']}"
    )
    assert hold["queuedHealLight"] == "", "排队的 2 档回复不冻"
    # 原因串里的数字来自家族表（不另写一份 3）。
    body = _strip_js_comments(_VITAL_FX_JS.read_text(encoding="utf-8"))
    reason = _js_function(body, "vitalFxHoldReason")
    assert "spec.holdsAt" in reason, f"原因串必须从家族表取阈值：{reason}"
    assert "heal:3" not in body, "别在别处硬写 `heal:3`（唯一真源是家族表的 holdsAt）"


def test_the_vital_fx_cap_is_read_per_family_from_anim_ini(vital_fx_probe: dict) -> None:
    """票面 §1：cap 键只写在家族表里、读的一侧是 `vitalFxCapFor`，**钳在读者侧**。

    每个变体都是**一份真 ini 文本 → 全新 sandbox → `animLoadConfig()`**（= 用户改完文件刷新
    页面），这条链同时证明：新键没被 `anim.js` 的未知键过滤器删掉、解析出的就是生效值。
    缺键 / 非数字 → 2；`0` / 负数 → 1；`> 8` → 8；小数取整（cap 是条数）。
    """
    caps = vital_fx_probe["caps"]
    assert caps == {"hpLoss": 2, "heal": 2, "unknown": 2}, (
        f"真 ini 的缺省都是 2（spec §多目标）；未登记家族也走缺省：{caps}"
    )
    # 静态口径：读口唯一、调 animTiming（不是自己缓存一份数字）。
    cap_for = _js_function(_VITAL_FX_JS.read_text(encoding="utf-8"), "vitalFxCapFor")
    assert "animTiming(" in cap_for, "cap 的读取入口必须**调用时**解析 anim.ini"
    assert "VITAL_FX_CONCURRENCY" in cap_for, "缺键 / 非数字回退缺省 2"
    assert "VITAL_FX_CAP_MIN" in cap_for and "VITAL_FX_CAP_MAX" in cap_for, "钳制在读者侧"
    assert "Math.round" in cap_for, "cap 是条数：小数取整"


def test_the_vital_fx_timing_reader_derives_fields_from_the_renderer_module(
    vital_fx_probe: dict,
) -> None:
    """票面 §1：`vitalFxTiming` 只搬数字，字段名 / 前缀来自画面模块（票 03 / 04 的 `DEFAULT_TIMING`）。

    缺键不硬造、非数字跳过；画面模块缺件 ⇒ 空表（`create()` 根本不会被调到）。
    """
    timing = vital_fx_probe["timing"]
    assert timing["three"] == {"hp_loss_fx_3_total": 1800, "hp_loss_fx_3_fade": 250}, (
        f"字段名从前缀 + TIMING_FIELDS 得来、值原样搬：{timing['three']}"
    )
    assert timing["two"] == {}, f"非数字跳过、缺键不硬造：{timing['two']}"
    assert vital_fx_probe["withoutModule"] == {}, (
        "画面模块缺件 ⇒ 时间轴空表（与 `renderer: null` 一致：不播）"
    )


def test_the_vital_fx_playback_hooks_are_wired_into_session_and_table() -> None:
    """票面 §2 / §4：两条事件源的触发点 + 三处收尾钩子。

    - **失去体力**：`fireHpLossEventsThrough(record.seq)` 与伤害事件**共用同一条 seq 游标**，
      紧挨 `fireDamageEventsThrough(record.seq)` 的下一行；两条盲刷路径（无记录 / 批尾兜底）
      也照伤害那两处写成 `Infinity`；
    - **回复**：`fireVitalEventsThrough(record)` 在 `applyRecordEffects(record, …)` **之后**
      （一条 `hp_change` 记录就是回复的事实源）；
    - **收尾**：`table.js:renderTable` 末尾 `vitalDropStalePlays()`（紧挨伤害那条）、
      `session.js:commitView` 换会话时 `clearVitalFx()`，`state.lifeLossEvents = []` 仍由
      `commitView` 自己清（票 01 落的那一行）。
    """
    session = _SESSION_JS.read_text(encoding="utf-8")
    match = _STEP_PLAYBACK_RE.search(session)
    assert match is not None, "session.js 里找不到 stepPlayback(immediate) 的函数体"
    body = _strip_js_comments(match.group("body"))

    damage_call = "fireDamageEventsThrough(record.seq);"
    hp_call = "fireHpLossEventsThrough(record.seq);"
    assert damage_call in body and hp_call in body, (
        "失去体力的排播钩子必须挂在伤害事件同一条 seq 游标上"
    )
    damage_at = body.index(damage_call)
    hp_at = body.index(hp_call)
    assert body[damage_at + len(damage_call):hp_at].strip() == "", (
        "`fireHpLossEventsThrough(record.seq)` 必须紧挨 `fireDamageEventsThrough(record.seq)` 的下一行"
    )
    apply_at = body.index("applyRecordEffects(record")
    heal_at = body.index("fireVitalEventsThrough(record);")
    assert apply_at < heal_at, "回复的排播钩子在 `applyRecordEffects` 之后调"
    assert heal_at < body.index("fireSkillEventsThrough(record.seq);"), (
        "回复钩子与其它演出钩子同一段（`applyRecordEffects` 之后、别的演出之前）"
    )

    # 两条盲刷路径照伤害层的姿态（无记录时的纯刷新 / 批尾兜底）。
    apply_view = _js_function(session, "applyView")
    assert "fireHpLossEventsThrough(Infinity);" in apply_view, (
        "纯刷新没有回放时，失去体力事实按响应立即消费（照 fireDamageEventsThrough 的姿态）"
    )
    finish = _js_function(session, "finishPlayback")
    assert "fireHpLossEventsThrough(Infinity);" in finish, "批尾兜底同样消费到点的事实"

    # 收尾钩子（反向零调用：排播层不自己挂到动效层里）。
    table = _TABLE_JS.read_text(encoding="utf-8")
    render = _strip_js_comments(_js_function(table, "renderTable"))
    drop = "damageDropStalePlays();"
    vital_drop = "vitalDropStalePlays();"
    assert drop in render and vital_drop in render, "`renderTable` 末尾要按矩形作废体力在播演出"
    assert render[render.index(drop) + len(drop):render.index(vital_drop)].strip() == "", (
        "`vitalDropStalePlays()` 紧挨 `damageDropStalePlays()`（两层各判各的，两行挨着）"
    )

    commit = _js_function(session, "commitView")
    assert "clearVitalFx();" in commit, "换局要清体力演出的队列 / 画布 / 计时器 / 声音"
    assert commit.index("clearDamageFx();") < commit.index("clearVitalFx();"), (
        "两层各清各的（体力层的清场挨着伤害层那一段）"
    )
    assert commit.index("clearVitalFx();") < commit.index("state.lifeLossEvents = [];"), (
        "先清演出、再清事实队列（与 `state.damageEvents` 同姿态）"
    )


def test_the_vital_sfx_seam_sits_behind_the_same_switch_and_degrades_silently() -> None:
    """票面 §5：`VITAL_SFX_ENGINES` + 四个 `vitalSfx*` 调用点，三条既有口径逐字沿用。

    「同一套开关」= **缺省静音** / **一次性手势** / **没有 `AudioContext` 就静默降级**；
    声道池按模块分组的机制复用伤害那张表（`damageSfxLanes` 按 key 分桶、池长 = `vitalFxCapFor`、
    槽位超出补建）。本票交付时两个音效模块（票 03 / 04）**都还不存在**：`vitalSfxEnsureModules`
    必须逐行走 `if (!engine.sfx) continue;`——不抛错、不建 `AudioContext`、也不留空桶
    （行为由烟测 `U-VITAL-08` 钉，这里钉接线与缺件分支的位置）。
    """
    sfx_ui = (STATIC_DIR / "sfx-ui.js").read_text(encoding="utf-8")
    vital = (STATIC_DIR / "vital-fx.js").read_text(encoding="utf-8")

    # 1) 引擎表：全局名照票面（票 03 / 04 的文件名不在这张表里）。
    assert re.search(r"^const VITAL_SFX_ENGINES = \{$", sfx_ui, re.M), (
        "sfx-ui.js 里缺少 `const VITAL_SFX_ENGINES = {`"
    )
    assert 'HP_LOSS: { key: "SgsHpLossSfx", fx: "SgsHpLossFX" },' in sfx_ui, "失去体力的音效 / 画面模块名"
    assert 'HEAL: { key: "SgsHealSfx", fx: "SgsHealFX" },' in sfx_ui, "回复的音效 / 画面模块名"

    # 2) 四个调用点 + 池长读唯一的 cap 口（不另写一份 2）。
    lanes = _js_function(sfx_ui, "vitalSfxEnsureLanes")
    assert "damageSfxLanes[key]" in lanes, "复用伤害那张按模块分桶的声道池"
    assert "vitalFxCapFor(engine.family)" in lanes, "池的起始长度 = 该家族的 cap（唯一读口）"
    assert "engine.sfx.create(timeline)" in lanes, "没有模块就一条声道都不建"
    for name in ("vitalSfxStart", "vitalSfxTick", "vitalSfxStop", "vitalSfxStopAll"):
        assert re.search(rf"^function {name}\(", sfx_ui, re.M), f"sfx-ui.js 缺 {name}()"
        assert name in vital, f"vital-fx.js 要在四个点调 {name}（单向缝：它只接 play 对象）"
    # 3) 缺件静默：`vitalSfxEnsureModules` 先跳过没加载的模块，`damageSfxEnsureModules` 顺手调它。
    ensure = _js_function(sfx_ui, "vitalSfxEnsureModules")
    assert "if (!engine.sfx) continue;" in ensure, (
        "模块缺件必须**先跳过**再谈声道（本票两个模块都不存在，这条是今天唯一的分支）"
    )
    assert ensure.index("if (!engine.sfx) continue;") < ensure.index("vitalSfxEnsureLanes"), (
        "跳过要发生在建声道之前（不许先建池再跳过）"
    )
    modules = _js_function(sfx_ui, "damageSfxEnsureModules")
    assert "vitalSfxEnsureModules()" in modules, (
        "体力两个模块要在**同一个手势**里建（damageSfxEnsureModules 顺手调它）"
    )
    mute = _js_function(sfx_ui, "damageSfxMute")
    assert "vitalSfxMute" in mute, "开关要灌到体力那两路（与伤害声道同一次静音）"

    # 4) 单向缝：排播层不碰音频 API，也不读开关的内部状态。
    body = _strip_js_comments(vital)
    assert "AudioContext" not in body, "vital-fx.js 不碰音频 API（音效层的事）"
    assert "localStorage" not in body, "开关的记忆归 sfx-ui.js（排播层不认识 sgs.sfx）"


# ---------------------------------------------------------------------------
# vital-fx 票 03：失去体力家族（画面 `SgsHpLossFX` + 音效 `SgsHpLossSfx`，**无时停**）
#
# 与 damage-fx-natures 票 01 / 03 同样的三块：
# 1. **清单 / 静态契约**：两份新脚本进清单（画面紧跟 `fx-fire.js`、音效紧跟 `sfx-fire.js`），
#    都是严格模式普通脚本（`_VITAL_FX_GLOBALS` 那条用例已覆盖全局名 / 不发请求 / 不碰 DOM）；
# 2. **画面模块的形状与双时间轴**：`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` /
#    `TIMING_KEYS` / `MAX_TOTAL_MS` 与剑 / 雷 / 火同形；字段名由 `DEFAULT_TIMING` 生成；导出档
#    照素材页的 2.65 / 2.90 / 3.35 秒链；牌桌档把素材页的四个拍子（`spotAt` / 第一次 `detachAt` /
#    `numAt` / `duration`）按**锚点**压到 ini 的 `spot` / `break` / `num` / `total` 上（`warp`），
#    钳制 `spot ≤ break ≤ num ≤ total ≤ 2200` 在模块内部；
# 3. **音效车道适配器**：`create(timeline)` → `{start, stop, mute, tick}`（与 `SgsSfx` 同形），
#    cue 时刻从时间轴读，四档排程在假 Web Audio 上整条走一遍。
#
# **档位判据只有一处**（spec §档位）：`vital-fx.js:vitalFxVariantOf(amount)`。素材包的
# `SgsHpLossFX.tierFor()` 不许作为第二份判据活下来——画面模块只认排播层交给它的 `variant`。
# 行为（层出现 / 三档读数 / 飘字 / 摘层时刻 / 全程不冻 / 伤害不漏墨）归烟测 `U-VITAL-09*`。
# ---------------------------------------------------------------------------

_HP_LOSS_JS = STATIC_DIR / "fx-hp-loss.js"
_HP_LOSS_SFX_JS = STATIC_DIR / "sfx-hp-loss.js"

# `hp_loss_fx_<档>_<字段>`。**不带档位号**的 `hp_loss_fx_concurrency`（票 02 的并发表）不是
# 时长键，别混进这张对照表。
_HP_LOSS_TIMING_KEY = re.compile(r"^hp_loss_fx_\d+_")

_HP_LOSS_FX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 记录型 2D 上下文：`create()` / `draw()` 全走真代码，像素不参与断言。所有离屏画布的
 * `fillText` 收进**同一份**日志：飘字是烘在贴图上的（`bakeNumber` 用 fillText 烘、`draw()`
 * 只 drawImage），所以"飘字写真实点数"只能从这份日志读。 */
const texts = [];
function makeCtx() {
  const gradient = { addColorStop() {} };
  const target = { texts };
  return new Proxy(target, {
    get(obj, prop) {
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "fillText") return (text) => { texts.push(String(text)); };
      if (prop in obj) return obj[prop];
      const fn = () => {};
      obj[prop] = fn;
      return fn;
    },
    set(obj, prop, value) { obj[prop] = value; return true; },
  });
}

function makeCanvas() {
  const ctx = makeCtx();
  return { width: 0, height: 0, getContext: () => ctx };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout,
  document: {
    getElementById: () => null,
    createElement: (tag) => (tag === "canvas" ? makeCanvas() : {
      style: {}, setAttribute() {}, appendChild() {},
    }),
  },
});
context.globalThis = context;
context.window = context;
/* 外层变量在 vm 的脚本里看不见：把文本日志挂到沙箱全局上（探针里叫 `texts`）。 */
context.texts = texts;

vm.runInContext(readFileSync(staticPath("fx-hp-loss.js"), "utf8"), context,
                { filename: "fx-hp-loss.js" });

const report = vm.runInContext(`(() => {
  const fx = SgsHpLossFX;
  const dst = { cx: 600, cy: 300, w: 240, h: 160 };
  const make = (opts) => fx.create(document.createElement("canvas"), opts);
  const game = (id, amount, extra) => make(Object.assign({
    variant: id, profile: "game", amount, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src: null, dst }, numberKeepOut: 70,
  }, extra || {}));
  const shapes = {};
  const drawn = {};
  for (const id of [1, 2, 3]) {
    const inst = game(id, id);
    shapes[id] = {
      keys: Object.keys(inst).sort(),
      duration: inst.duration,
      profile: inst.profile,
      timing: inst.timing,
      timeline: inst.timeline,
      metrics: inst.metrics,
      variant: inst.variant,
      amount: inst.amount,
      bare: inst.bare,
      width: inst.width,
      height: inst.height,
    };
    try {
      for (let step = 0; step <= 24; step += 1) inst.draw((inst.duration * step) / 24);
      drawn[id] = "ok";
    } catch (error) { drawn[id] = String(error && error.stack || error); }
  }
  /* 不传 stage（覆盖层量不到布局 / node 探针）：退参考舞台，不抛错。 */
  const fallback = make({
    variant: 3, profile: "game", amount: 3, bare: true, dpr: 1,
    view: { width: 1440, height: 810 },
  });
  const fallbackStage = fallback.stage;
  try {
    for (const t of [0, 0.4, 0.9, 1.5, 1.8]) fallback.draw(t);
    drawn.fallback = "ok";
  } catch (error) { drawn.fallback = String(error && error.stack || error); }
  /* 导出档：照素材页的 2.65 / 2.90 / 3.35 秒链（node 探针逐帧导静帧靠它）。 */
  const exported = {};
  for (const id of [1, 2, 3]) {
    const inst = make({ variant: id, profile: "export", amount: id + 1, dpr: 1 });
    exported[id] = { duration: inst.duration, timeline: inst.timeline, profile: inst.profile };
    try {
      for (let step = 0; step <= 24; step += 1) inst.draw((inst.duration * step) / 24);
      drawn["export" + id] = "ok";
    } catch (error) { drawn["export" + id] = String(error && error.stack || error); }
  }
  /* 真实点数：amount = 4 仍按 3 档演，飘字是 −4（不是档位号 3 / 也不是 1）。 */
  texts.length = 0;
  const four = game(3, 4);
  const fourTexts = texts.slice();
  four.draw(four.timeline.NUM + 0.02);
  function ordered(ms) {
    return ms.spot >= 0 && ms.spot <= ms.break && ms.break <= ms.num
      && ms.num <= ms.total && ms.total <= fx.MAX_TOTAL_MS;
  }
  const messy = (id) => ({
    ["hp_loss_fx_" + id + "_spot"]: 5000,
    ["hp_loss_fx_" + id + "_break"]: 5000,
    ["hp_loss_fx_" + id + "_num"]: 5000,
    ["hp_loss_fx_" + id + "_total"]: 9000,
  });
  return JSON.stringify({
    prefix: fx.TIMING_PREFIX,
    maxTotal: fx.MAX_TOTAL_MS,
    fields: fx.TIMING_FIELDS,
    keys: fx.TIMING_KEYS,
    defaults: fx.DEFAULT_TIMING,
    frozen: [Object.isFrozen(fx.TIMING_FIELDS), Object.isFrozen(fx.TIMING_KEYS)],
    surface: Object.keys(fx).sort(),
    hasTierFor: Object.prototype.hasOwnProperty.call(fx, "tierFor"),
    tiers: { 1: fx.TIERS[1].duration, 2: fx.TIERS[2].duration, 3: fx.TIERS[3].duration },
    shapes: shapes,
    exported: exported,
    fallback: {
      stage: fallbackStage,
      duration: fallback.duration,
      timeline: fallback.timeline,
    },
    four: {
      variant: four.variant,
      amount: four.amount,
      texts: fourTexts,
      numberText: four.metrics.numberText,
    },
    clamps: {
      total: fx.gameTiming(1, { hp_loss_fx_1_total: 9999 }).ms.total,
      spot: fx.gameTiming(1, { hp_loss_fx_1_total: 1000, hp_loss_fx_1_spot: 5000 }).ms.spot,
      breakBelowSpot: fx.gameTiming(1, { hp_loss_fx_1_break: 10 }).ms.break,
      numAboveTotal: fx.gameTiming(1, { hp_loss_fx_1_total: 900, hp_loss_fx_1_num: 5000 }).ms.num,
      junk: fx.gameTiming(2, { hp_loss_fx_2_total: "abc", hp_loss_fx_2_spot: -1 }).ms,
      missing: fx.gameTiming(1, { hp_loss_fx_1_total: 900 }).ms,
      three: fx.gameTiming(3).ms,
    },
    ordered: [1, 2, 3].map((id) => ordered(fx.gameTiming(id).ms)),
    orderedMessy: [1, 2, 3].map((id) => ordered(fx.gameTiming(id, messy(id)).ms)),
    warp: {
      1: fx.gameTiming(1).warp,
      2: fx.gameTiming(2).warp,
      3: fx.gameTiming(3).warp,
    },
    drawn: drawn,
  });
})()`, context, { filename: "hp_loss_probe.js" });
process.stdout.write(report);
"""


def _hp_loss_fx_probe() -> dict:
    """跑真 `fx-hp-loss.js`（node vm + 记录型 2D 上下文），拿回解算 / 实例 / 导出链的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "hp_loss_probe.mjs"
        probe.write_text(_HP_LOSS_FX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"失去体力画面探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def hp_loss_fx_probe() -> dict:
    """一次探针跑完，形状 / 钳制 / 导出链几条用例共用。"""
    return _hp_loss_fx_probe()


def test_the_hp_loss_family_scripts_are_listed_next_to_their_fire_twins() -> None:
    """票 03 §4：`fx-hp-loss.js` 紧跟 `fx-fire.js`、`sfx-hp-loss.js` 紧跟 `sfx-fire.js`，
    两份都在 `sfx-ui.js` 之前（音效 UI 的声道池按名取它们的 `gameTiming`）。

    spec §硬约束把这条次序逐名列出来了（损伤组 → 体力组 → 音效组 → `sfx-ui.js` → `main.js`）。
    """
    srcs = _script_srcs()
    for name in ("fx-hp-loss.js", "sfx-hp-loss.js"):
        assert name in srcs, f"index.html 的脚本清单里必须有 {name}：{srcs}"
    assert srcs.index("fx-hp-loss.js") == srcs.index("fx-fire.js") + 1, (
        f"fx-hp-loss.js 必须紧跟 fx-fire.js（画面组按家族顺序）：{srcs}"
    )
    assert srcs.index("sfx-hp-loss.js") == srcs.index("sfx-fire.js") + 1, (
        f"sfx-hp-loss.js 必须紧跟 sfx-fire.js（音效组按家族顺序）：{srcs}"
    )
    assert srcs.index("fx-hp-loss.js") < srcs.index("sfx.js"), (
        "画面组整组排在音效组之前（总谱对：fx-core / fx-thunder / fx-fire / fx-hp-loss）"
    )
    assert srcs.index("sfx-hp-loss.js") < srcs.index("sfx-ui.js"), (
        "sfx-ui.js 的声道池读它的 gameTiming ⇒ 必须排在它之前"
    )
    assert srcs.index("fx-hp-loss.js") < srcs.index("sfx-hp-loss.js"), (
        "画面在音效之前（spec §硬约束的清单顺序）"
    )


def test_the_hp_loss_renderer_keeps_the_fire_renderer_shape(hp_loss_fx_probe: dict) -> None:
    """`SgsHpLossFX` 与 `SgsFireFX` / `SgsThunderFX` 同形：排播层不认第二种形状。

    - 导出面：`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_PREFIX` / `TIMING_FIELDS` /
      `TIMING_KEYS` / `MAX_TOTAL_MS`，字段名由 `DEFAULT_TIMING` 生成；
    - **没有 `tierFor`**（档位判据只有 `vital-fx.js:vitalFxVariantOf` 一处）；
    - `create()` 返回 `draw / duration / timeline / timing / metrics / stage`；
    - 牌桌档：`duration = total`（秒）、`SPOT < BREAK < IMPACT < NUM < DURATION`（牌脚凝墨 →
      断开 → 落地 → 飘字 → 摘层），`COUNT` = 该档的墨珠数；
    - 真实点数：`amount: 4` 按 3 档演，飘字是 `−4`；
    - 量不到座位矩形 ⇒ 退参考舞台，逐帧 draw 不抛错。
    """
    probe = hp_loss_fx_probe
    assert probe["prefix"] == "hp_loss_fx_"
    assert probe["maxTotal"] == 2200, "单档总时长上限 2200（失去体力不冻回放，压在 2s 级）"
    assert probe["fields"] == ["total", "spot", "break", "num"], (
        f"四个字段（都是**从开演算的绝对毫秒**，不是间隔）：{probe['fields']}"
    )
    assert probe["keys"] == list(probe["defaults"]), "TIMING_KEYS 必须就是 DEFAULT_TIMING 的键"
    assert probe["frozen"] == [True, True], "TIMING_FIELDS / TIMING_KEYS 是冻结数组"
    derived: list[str] = []
    for key in probe["defaults"]:
        field = key[len(probe["prefix"]):].split("_", 1)[1]
        if field not in derived:
            derived.append(field)
    assert list(probe["fields"]) == derived, (
        f"`TIMING_FIELDS` 必须由 `DEFAULT_TIMING` 的键生成：期望 {derived}，实际 {list(probe['fields'])}"
    )
    for name in (
        "create", "gameTiming", "DEFAULT_TIMING", "TIMING_PREFIX",
        "TIMING_FIELDS", "TIMING_KEYS", "MAX_TOTAL_MS",
    ):
        assert name in probe["surface"], f"SgsHpLossFX 缺少导出 {name}：{probe['surface']}"
    assert probe["hasTierFor"] is False, (
        "档位判据只有 `vitalFxVariantOf` 一处：模块不许再导出一份 tierFor（spec §档位）"
    )
    source = _strip_js_comments(_HP_LOSS_JS.read_text(encoding="utf-8"))
    assert "tierFor" not in source, "画面模块里不许残留 tierFor（素材包的第二份判据）"

    counts = {"1": 1, "2": 2, "3": 3}
    for variant in ("1", "2", "3"):
        shape = probe["shapes"][variant]
        for key in (
            "draw", "duration", "timeline", "timing", "metrics", "stage",
            "variant", "profile", "amount", "bare", "width", "height",
        ):
            assert key in shape["keys"], f"{variant} 档的实例缺少 {key}：{shape['keys']}"
        timing = shape["timing"]
        timeline = shape["timeline"]
        assert shape["profile"] == "game" and shape["bare"] is True
        assert shape["width"] == 1440 and shape["height"] == 810, "牌桌档按 view 建画布"
        assert timing["total"] <= probe["maxTotal"], (
            f"{variant} 档总时长必须 ≤ SgsHpLossFX.MAX_TOTAL_MS（{probe['maxTotal']}）"
        )
        assert shape["duration"] == pytest.approx(timing["total"] / 1000)
        assert timeline["DURATION"] == pytest.approx(shape["duration"])
        assert timeline["COUNT"] == counts[variant], "墨珠数来自三档表"
        assert timeline["SPOT"] == pytest.approx(timing["spot"] / 1000)
        assert timeline["BREAK"] == pytest.approx(timing["break"] / 1000)
        assert timeline["NUM"] == pytest.approx(timing["num"] / 1000)
        assert 0 < timeline["SPOT"] < timeline["BREAK"] < timeline["IMPACT"] < timeline["NUM"], (
            f"{variant} 档的拍子必须按「湿 → 断 → 坠 → 数」排：{timeline}"
        )
        assert timeline["IMPACT"] <= timeline["NUM"] < timeline["DURATION"]
        metrics = shape["metrics"]
        assert metrics["number"] > 0
        assert metrics["numberBox"]["height"] == pytest.approx(metrics["number"])
        assert metrics["number"] <= 0.35 * metrics["card"]["h"] + 0.05, (
            "字高不得超过牌高 35%（压住体力读数）"
        )
        assert metrics["numberBox"]["top"] >= metrics["keepOut"] + 0.02 * metrics["card"]["h"] - 0.05, (
            "飘字包围盒的顶边不得压到体力条（留 2% 牌高余量）"
        )
        assert 0 < metrics["inkAlpha"] < 1, (
            "bare 下墨是实色（透明覆盖层上的 multiply）：必须有一个 < 1 的起始透明度"
            f"（最终值以实机为准）：{metrics['inkAlpha']}"
        )
        assert probe["drawn"][variant] == "ok", f"{variant} 档逐帧绘制抛错：{probe['drawn'][variant]}"

    four = probe["four"]
    assert four["variant"] == 3, "amount ≥ 3 按 3 档演"
    assert four["amount"] == 4, "飘字数字来自服务端 amount，不是档位号"
    assert four["numberText"] in ("-4", "\u22124"), (
        f"飘字必须是真实点数（amount = 4 ⇒ −4）：{four['numberText']!r}"
    )
    assert "4" in four["texts"], (
        f"字身要用 fillText 画出来（负号是另外一笔短横）：{four['texts']}"
    )
    assert "3" not in four["texts"], (
        f"不许把档位号当点数写（amount = 4 时写 -3 是错的）：{four['texts']}"
    )

    fallback = probe["fallback"]
    assert fallback["stage"]["dst"]["w"] > 0 and fallback["stage"]["dst"]["h"] > 0
    assert probe["drawn"]["fallback"] == "ok", f"退参考舞台那一分支绘制抛错：{probe['drawn']['fallback']}"


def test_the_hp_loss_export_chain_keeps_the_material_package_beats(hp_loss_fx_probe: dict) -> None:
    """导出档照素材页的 2.65 / 2.90 / 3.35 秒链（`numAt` 不变）；牌桌档只把**拍子**压到 ini 上。

    「比例不变」的判据是 `warp` 的锚点：素材页的 `spotAt` / 第一次 `detachAt` / `numAt` /
    `duration` 各自对到 ini 的 `spot` / `break` / `num` / `total` 上，**段内线性**——墨珠
    「湿 → 挂 → 断 → 坠 → 洇 → 数」的相对节奏因此跟着一起压，不是逐拍各压一个系数。
    """
    probe = hp_loss_fx_probe
    assert probe["tiers"] == {"1": 2.65, "2": 2.90, "3": 3.35}, (
        f"导出档三档时长照素材页：{probe['tiers']}"
    )
    expected = {
        "1": {"duration": 2.65, "SPOT": 0.06, "BREAK": 0.60, "NUM": 0.86, "COUNT": 1},
        "2": {"duration": 2.90, "SPOT": 0.05, "BREAK": 0.56, "NUM": 0.92, "COUNT": 2},
        "3": {"duration": 3.35, "SPOT": 0.04, "BREAK": 0.52, "NUM": 0.90, "COUNT": 3},
    }
    for variant, want in expected.items():
        export = probe["exported"][variant]
        assert export["profile"] == "export"
        assert export["duration"] == pytest.approx(want["duration"])
        timeline = export["timeline"]
        assert timeline["DURATION"] == pytest.approx(want["duration"])
        assert timeline["SPOT"] == pytest.approx(want["SPOT"])
        assert timeline["BREAK"] == pytest.approx(want["BREAK"])
        assert timeline["NUM"] == pytest.approx(want["NUM"]), "导出档的 numAt 不变"
        assert timeline["COUNT"] == want["COUNT"]
        assert timeline["BREAK"] < timeline["IMPACT"] < timeline["NUM"] < timeline["DURATION"]
        assert probe["drawn"]["export" + variant] == "ok", (
            f"导出档 {variant} 逐帧绘制抛错：{probe['drawn']['export' + variant]}"
        )

    # 牌桌档的锚点：素材页秒 → 牌桌秒（段内线性，端点精确落在 ini 的毫秒上）。
    anchors = {"1": (0.06, 0.60, 0.86, 2.65), "2": (0.05, 0.56, 0.92, 2.90), "3": (0.04, 0.52, 0.90, 3.35)}
    for variant in ("1", "2", "3"):
        ms = probe["shapes"][variant]["timing"]
        pairs = probe["warp"][variant]
        assert pairs[0] == {"e": 0, "g": 0}, f"{variant} 档锚点从 (0, 0) 开始：{pairs}"
        spot_at, detach_at, num_at, duration = anchors[variant]
        want = [
            {"e": spot_at, "g": pytest.approx(ms["spot"] / 1000)},
            {"e": detach_at, "g": pytest.approx(ms["break"] / 1000)},
            {"e": num_at, "g": pytest.approx(ms["num"] / 1000)},
            {"e": duration, "g": pytest.approx(ms["total"] / 1000)},
        ]
        assert len(pairs) == len(want) + 1, f"{variant} 档锚点对：{pairs}"
        for got, expected_pair in zip(pairs[1:], want):
            assert got["e"] == pytest.approx(expected_pair["e"]), (
                f"{variant} 档的素材页端点必须逐拍对上：{pairs}"
            )
            assert got["g"] == expected_pair["g"], f"{variant} 档的牌桌端点：{pairs}"


def test_the_hp_loss_timing_table_clamps_inside_the_module(hp_loss_fx_probe: dict) -> None:
    """缺键 / 非法值退 `DEFAULT_TIMING`；钳制在**模块内部**：`spot ≤ break ≤ num ≤ total ≤ 2200`。

    三档缺省 = spec §游戏档缺省时长（绝对毫秒，不是间隔）：1 = 1200 / 80 / 320 / 560，
    2 = 1500 / 80 / 360 / 640，3 = 1800 / 80 / 380 / 720。
    """
    probe = hp_loss_fx_probe
    assert probe["clamps"]["three"] == {"total": 1800, "spot": 80, "break": 380, "num": 720}, (
        f"3 档缺省按 spec：{probe['clamps']['three']}"
    )
    assert probe["ordered"] == [True, True, True], "三档缺省都在钳制范围内"
    assert probe["orderedMessy"] == [True, True, True], (
        "四个字段全给非法大数时仍须守住 spot ≤ break ≤ num ≤ total ≤ 2200"
    )
    clamps = probe["clamps"]
    assert clamps["total"] == 2200, "total 超出 MAX_TOTAL_MS 即钳到 2200"
    assert clamps["spot"] == 1000, "spot ≤ total"
    assert clamps["breakBelowSpot"] == 80, "break 小于 spot 时被抬到 spot（不是退默认）"
    assert clamps["numAboveTotal"] == 900, "num ≤ total"
    assert clamps["junk"] == {"total": 1500, "spot": 80, "break": 360, "num": 640}, (
        f"非法值（非数字 / 负数）退回内置默认：{clamps['junk']}"
    )
    assert clamps["missing"] == {"total": 900, "spot": 80, "break": 320, "num": 560}, (
        f"只改 total，其余退回内置默认：{clamps['missing']}"
    )
    # 缺省值本身是 spec 的压缩表（改 ini 等于改用户可见的节奏；换掉缺省先改 spec）。
    defaults = probe["defaults"]
    assert [defaults[f"hp_loss_fx_{v}_total"] for v in (1, 2, 3)] == [1200, 1500, 1800]
    assert [defaults[f"hp_loss_fx_{v}_spot"] for v in (1, 2, 3)] == [80, 80, 80]
    assert [defaults[f"hp_loss_fx_{v}_break"] for v in (1, 2, 3)] == [320, 360, 380]
    assert [defaults[f"hp_loss_fx_{v}_num"] for v in (1, 2, 3)] == [560, 640, 720]


def test_anim_ini_lists_the_hp_loss_durations_per_variant(hp_loss_fx_probe: dict) -> None:
    """`anim.ini` 的 `hp_loss_fx_<档>_<字段>` 键块与 `SgsHpLossFX.DEFAULT_TIMING` 逐键同值。

    键表**不在测试里写第二份**：`SgsHpLossFX.TIMING_KEYS` 是唯一真源，这里只与磁盘上的
    `anim.ini` 对表（同剑光 / 雷电 / 火焰 / 铁索 / 阵亡的姿态）。
    """
    entries = {
        key: value for key, value in _ini_entries().items()
        if _HP_LOSS_TIMING_KEY.match(key)
    }
    expected = set(hp_loss_fx_probe["keys"])
    assert set(entries) == expected, (
        f"失去体力的时长键必须与 fx-hp-loss 的 DEFAULT_TIMING 一致："
        f"缺 {sorted(expected - set(entries))}；多 {sorted(set(entries) - expected)}"
    )
    values = {key: float(value) for key, value in entries.items()}
    max_total = hp_loss_fx_probe["maxTotal"]
    for variant in (1, 2, 3):
        total = values[f"hp_loss_fx_{variant}_total"]
        spot = values[f"hp_loss_fx_{variant}_spot"]
        break_at = values[f"hp_loss_fx_{variant}_break"]
        num = values[f"hp_loss_fx_{variant}_num"]
        assert 0 < spot < break_at < num < total <= max_total, (
            f"{variant} 档的四个绝对毫秒必须递增且 total ≤ {max_total}："
            f"spot={spot} / break={break_at} / num={num} / total={total}"
        )
    totals = [values[f"hp_loss_fx_{variant}_total"] for variant in (1, 2, 3)]
    assert totals == [1200, 1500, 1800], f"spec §游戏档缺省时长：{totals}"
    ini_text = _ANIM_INI.read_text(encoding="utf-8")
    assert "体力演出 · 失去体力" in ini_text, "键块要有自己的标题（用户改得动）"
    assert values == {
        key: float(value) for key, value in hp_loss_fx_probe["defaults"].items()
    }, "内置默认必须与磁盘上的 anim.ini 同值（否则删一行 ini 会静默改变三档节奏）"


def test_the_hp_loss_module_never_freezes_playback_and_has_no_screen_flash() -> None:
    """失去体力**永不冻回放**（用户口径），也没有整屏闪——两者都是"这一层不该有的东西"。

    - 不登记 `registerPlaybackHold`（时停真源是家族表的 `holdsAt: Infinity`）；
    - 不碰 `.fx-flash` / `#table-shell` / 任何 CSS `filter`（素材页也没有）；
    - `bare` 下的墨用**一个 < 1 的起始透明度**（`BARE_INK_ALPHA`）：透明覆盖层上的
      `multiply` 只对画布内已画的东西生效，墨会变实色——**最终色值以实机为准**，
      票面不预设数字，所以这里只钉"有一个可调的单一常量、注释写明了要实机校"。
    """
    source = _strip_js_comments(_HP_LOSS_JS.read_text(encoding="utf-8"))
    assert "registerPlaybackHold" not in source, "失去体力永不冻回放：不登记暂停判据"
    assert "fx-flash" not in source, "不能碰整屏闪的着色子层"
    assert "table-shell" not in source, "不碰表壳（也就不会有 filter / containing block）"
    assert not re.search(r"\.style\.filter\b", source), "不许给任何节点挂 CSS filter"
    match = re.search(r"var BARE_INK_ALPHA = ([0-9.]+);", source)
    assert match is not None, "bare 下的墨色必须是一个**命名常量**（可调、可实机校）"
    assert 0 < float(match.group(1)) < 1, (
        f"BARE_INK_ALPHA 必须在 (0, 1) 之间（1 = 不透明的实色墨）：{match.group(1)}"
    )
    assert "实机" in _HP_LOSS_JS.read_text(encoding="utf-8"), (
        "注释里要写明最终色值以实机为准（票面 §1 的 bare 口径）"
    )


_HP_LOSS_SFX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 假 Web Audio：node 没有 AudioContext / OfflineAudioContext，而排程代码必须整条走一遍。
 * 每个节点只有 connect / start / stop 与参数 setter；`start(when)` 的**时刻**记下来——
 * "cue 从时间轴读"就靠它判（不是在音效模块里再写死一份秒数）。
 * 两个入口各用一张图：实时车道（`start` / `tick`）与离线渲染（`renderOffline`，`exact` 模式
 * 会给出精确的 `when`）。 */
function makeParam() {
  const p = {
    value: 0,
    setValueAtTime(v) { p.value = v; return p; },
    exponentialRampToValueAtTime(v) { p.value = v; return p; },
    linearRampToValueAtTime(v) { p.value = v; return p; },
    setTargetAtTime(v) { p.value = v; return p; },
  };
  return p;
}

function makeNode(kind, graph) {
  const node = {
    kind,
    connect() { return node; },
    disconnect() {},
    start(when) {
      graph.started += 1;
      if (typeof when === "number" && isFinite(when)) graph.whens.push(when);
    },
    stop() {},
  };
  if (kind === "gain") node.gain = makeParam();
  if (kind === "oscillator") { node.frequency = makeParam(); node.type = "sine"; }
  if (kind === "bufferSource") { node.buffer = null; node.loop = false; }
  if (kind === "filter") { node.frequency = makeParam(); node.Q = makeParam(); node.type = "lowpass"; }
  if (kind === "compressor") {
    node.threshold = makeParam(); node.knee = makeParam(); node.ratio = makeParam();
    node.attack = makeParam(); node.release = makeParam();
  }
  return node;
}

function makeGraph() {
  const graph = { nodes: 0, started: 0, whens: [] };
  const ctx = {
    sampleRate: 44100,
    currentTime: 0,
    state: "running",
    destination: { connect() {} },
    resume() { return Promise.resolve(); },
    createGain() { graph.nodes += 1; return makeNode("gain", graph); },
    createOscillator() { graph.nodes += 1; return makeNode("oscillator", graph); },
    createBufferSource() { graph.nodes += 1; return makeNode("bufferSource", graph); },
    createBiquadFilter() { graph.nodes += 1; return makeNode("filter", graph); },
    createDynamicsCompressor() { graph.nodes += 1; return makeNode("compressor", graph); },
    createBuffer(channels, length) {
      const data = new Float32Array(Math.max(1, length));
      return {
        numberOfChannels: channels, length: data.length, sampleRate: 44100,
        getChannelData: () => data,
      };
    },
  };
  return { ctx, graph };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout, Math, Array, Float32Array, Object, JSON, Promise,
  document: { createElement: () => ({ width: 0, height: 0, getContext: () => null }) },
});
context.globalThis = context;
context.window = context;
/* 假图的构造器在外面写，但要在沙箱里调（vm 的脚本看不到外层变量）。 */
context.makeGraph = makeGraph;
vm.runInContext(readFileSync(staticPath("fx-hp-loss.js"), "utf8"), context, { filename: "fx-hp-loss.js" });
vm.runInContext(readFileSync(staticPath("sfx-hp-loss.js"), "utf8"), context, { filename: "sfx-hp-loss.js" });

const report = await vm.runInContext(`(async () => {
  const fx = window.SgsHpLossFX;
  const sfx = window.SgsHpLossSfx;

  /* 离线渲染的假图：start(when) 记的就是时间轴上的绝对秒数（exact 模式）。 */
  let offline = null;
  window.OfflineAudioContext = function OfflineAudioContext() {
    const made = makeGraph();
    made.ctx.startRendering = () => Promise.resolve({
      numberOfChannels: 2,
      length: 128,
      sampleRate: 44100,
      /* 填一个常数：renderOffline 的电平统计要跑通（"有波形、未削波"这一层由真
       * OfflineAudioContext 在浏览器里证；垫片只证**链路**与窗口口径）。 */
      getChannelData: () => new Float32Array(128).fill(0.1),
    });
    offline = made.graph;
    return made.ctx;
  };

  /* 实时车道：假 AudioContext + 逐帧 tick（抓排程代码里的拼写 / 参数错误）。 */
  const live = makeGraph();
  window.AudioContext = function AudioContext() { return live.ctx; };

  const out = {};
  for (const id of [1, 2, 3]) {
    const clock = fx.gameTiming(id);
    const timeline = Object.assign({}, clock.timeline);
    const lane = sfx.create(timeline);
    let error = null;
    let started = false;
    const before = { nodes: live.graph.nodes, sources: live.graph.started };
    try {
      started = lane.start();
      const frames = 24;
      for (let step = 1; step <= frames; step += 1) {
        lane.tick((clock.timeline.DURATION * step) / frames, { dmg: id });
      }
      lane.mute(true);
      lane.mute(false);
      lane.stop();
    } catch (e) { error = String(e && e.stack || e); }

    /* 离线：同一份时间轴，exact 模式给出精确 cue 时刻。 */
    let offlineError = null;
    let levels = null;
    try {
      levels = await sfx.renderOffline(id, timeline, clock.timeline.DURATION + 0.3);
    } catch (e) { offlineError = String(e && e.stack || e); }

    out[id] = {
      started, error,
      nodes: live.graph.nodes - before.nodes,
      sources: live.graph.started - before.sources,
      offlineError,
      offlinePeak: levels ? levels.total.peak : null,
      whens: (offline ? offline.whens.slice().sort((a, b) => a - b) : []),
      timeline: {
        spot: clock.timeline.SPOT,
        break: clock.timeline.BREAK,
        impact: clock.timeline.IMPACT,
        num: clock.timeline.NUM,
        duration: clock.timeline.DURATION,
        count: clock.timeline.COUNT,
      },
    };
    offline = null;
  }
  return JSON.stringify({
    surface: Object.keys(sfx).sort(),
    tiers: out,
  });
})()`, context, { filename: "hp_loss_sfx_probe.js" });
process.stdout.write(report);
"""


def test_the_hp_loss_sfx_lane_schedules_every_tier_without_a_real_audio_context() -> None:
    """`SgsHpLossSfx` 是**车道适配器**（`create(timeline)` → `{start, stop, mute, tick}`）：
    三档都能建图、按时间轴排完、收声，且 cue 时刻**跟着时间轴走**。

    烟测里没有 `AudioContext`（垫片故意不建），所以这条补上**真模块**这一侧：真
    `create` / `start` / `tick` / `stop` 在假图（记录型 `AudioContext`）上跑一遍，抓音色函数里
    的拼写 / 参数错误。声音本身（听感）仍只能在浏览器里听——`renderOffline` 接进
    `?audiotest=1` 不在本票（见 spec §已知缺口 5）。
    """
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "hp_loss_sfx_probe.mjs"
        probe.write_text(_HP_LOSS_SFX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"失去体力音效探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    probe = json.loads(result.stdout)
    for name in ("create", "renderOffline"):
        assert name in probe["surface"], f"SgsHpLossSfx 缺少 {name}：{probe['surface']}"
    for variant in ("1", "2", "3"):
        tier = probe["tiers"][variant]
        assert tier["error"] is None, f"{variant} 档排程抛错：{tier['error']}"
        assert tier["started"] is True, f"{variant} 档 start() 建出了共享图"
        assert tier["nodes"] > 0 and tier["sources"] > 0, f"{variant} 档一个音源都没排出来"
        assert tier["offlineError"] is None, f"{variant} 档离线排程抛错：{tier['offlineError']}"
        assert tier["offlinePeak"] and tier["offlinePeak"] > 0, (
            f"{variant} 档离线渲染一个采样都没有（峰值 {tier['offlinePeak']}）"
        )
        timeline = tier["timeline"]
        whens = tier["whens"]
        assert whens, f"{variant} 档没有任何 start(when) 时刻"
        assert whens[0] == pytest.approx(timeline["spot"], abs=0.02), (
            f"{variant} 档的第一声是纸吸水（在时间轴的 SPOT）：{whens[:3]}"
        )
        assert any(
            timeline["impact"] - 0.02 <= when <= timeline["impact"] + 0.04 for when in whens
        ), f"{variant} 档缺少落地那一声（IMPACT = {timeline['impact']}）：{whens}"
        assert any(
            timeline["num"] - 0.02 <= when <= timeline["num"] + 0.02 for when in whens
        ), f"{variant} 档缺少飘字那一声（NUM = {timeline['num']}）：{whens}"
        assert max(whens) <= timeline["duration"] + 0.05, (
            f"{variant} 档不许排到摘层之后（DURATION = {timeline['duration']}）：{max(whens)}"
        )
    assert probe["tiers"]["3"]["sources"] > probe["tiers"]["1"]["sources"], (
        "3 档（三颗墨珠 + 墨气）排出的音源必须多于 1 档（一颗）："
        f"{probe['tiers']['3']['sources']} vs {probe['tiers']['1']['sources']}"
    )


# ---------------------------------------------------------------------------
# vital-fx 票 04：回复体力家族（画面 `SgsHealFX` + 音效 `SgsHealSfx`，**3+ 档时停**）
#
# 与票 03 同样的三块（画面模块与音效模块是同两个形状，只换素材与字段）：
# 1. **清单 / 静态契约**：两份新脚本进清单（画面紧跟 `fx-hp-loss.js`、音效紧跟
#    `sfx-hp-loss.js`），都是严格模式普通脚本（`_VITAL_FX_GLOBALS` 那条用例覆盖全局名 /
#    不发请求 / 不碰 DOM）；
# 2. **画面模块的形状与双时间轴**：`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_FIELDS` /
#    `TIMING_KEYS` / `MAX_TOTAL_MS` 与雷 / 火 / 失去体力同形；字段名由 `DEFAULT_TIMING` 生成；
#    导出档照素材页的 3.20 / 3.55 / 4.45 秒链；牌桌档把素材页的拍子按**锚点**压到 ini 的
#    `total` / `gather` / `hold` / `burst` / `num` 上（`warp`），钳制
#    `gather + hold ≤ burst ≤ num ≤ total ≤ 4500` 在模块内部；
# 3. **音效车道适配器**：`create(timeline)` → `{start, stop, mute, tick}`（与 `SgsSfx` 同形），
#    cue 时刻从时间轴读，三档排程在假 Web Audio 上整条走一遍。
#
# **只 3+ 档有 `gather` / `hold` / `burst`**（与 `fx-thunder.js` 的 `strobe` / `flash` 同姿态：
# 1 / 2 档不硬造这三个键），`TIMING_FIELDS` 由 `DEFAULT_TIMING` 的键生成、没有第二处名单。
# **档位判据只有一处**（spec §档位）：`vital-fx.js:vitalFxVariantOf(amount)`——画面模块只认
# 排播层交给它的 `variant`，素材包的 `tierFor()` 不许作为第二份判据活下来。
# 行为（层出现 / 三档读数 / 飘字 / 摘层时刻 / **3+ 档时停** / 兜底绿闪）归烟测 `U-VITAL-1x`；
# 时停的真源是家族表的 `holdsAt: 3`（票 02 的用例已逐档钉死），本块只钉"模块自己不登记暂停"。
# ---------------------------------------------------------------------------

_HEAL_JS = STATIC_DIR / "fx-heal.js"
_HEAL_SFX_JS = STATIC_DIR / "sfx-heal.js"

# `heal_fx_<档>_<字段>`。**不带档位号**的 `heal_fx_concurrency`（票 02 的并发表）不是时长键，
# 别混进这张对照表。
_HEAL_TIMING_KEY = re.compile(r"^heal_fx_\d+_")

_HEAL_FX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 记录型 2D 上下文（与票 03 的探针同一张垫片）：`create()` / `draw()` 全走真代码，像素不参与
 * 断言。所有离屏画布的 `fillText` 收进**同一份**日志：飘字是烘在贴图上的（`bakeNumber` 用
 * fillText 烘、`draw()` 只 drawImage），所以"飘字写真实点数"只能从这份日志读。 */
const texts = [];
function makeCtx() {
  const gradient = { addColorStop() {} };
  const target = { texts };
  return new Proxy(target, {
    get(obj, prop) {
      if (prop === "createRadialGradient" || prop === "createLinearGradient") return () => gradient;
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "fillText") return (text) => { texts.push(String(text)); };
      if (prop in obj) return obj[prop];
      const fn = () => {};
      obj[prop] = fn;
      return fn;
    },
    set(obj, prop, value) { obj[prop] = value; return true; },
  });
}

function makeCanvas() {
  const ctx = makeCtx();
  return { width: 0, height: 0, getContext: () => ctx };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout,
  document: {
    getElementById: () => null,
    createElement: (tag) => (tag === "canvas" ? makeCanvas() : {
      style: {}, setAttribute() {}, appendChild() {},
    }),
  },
});
context.globalThis = context;
context.window = context;
context.texts = texts;

vm.runInContext(readFileSync(staticPath("fx-heal.js"), "utf8"), context,
                { filename: "fx-heal.js" });

const report = vm.runInContext(`(() => {
  const fx = SgsHealFX;
  const dst = { cx: 600, cy: 300, w: 240, h: 160 };
  const make = (opts) => fx.create(document.createElement("canvas"), opts);
  const game = (id, amount, extra) => make(Object.assign({
    variant: id, profile: "game", amount, bare: true, dpr: 1,
    view: { width: 1440, height: 810 }, stage: { src: null, dst }, numberKeepOut: 70,
  }, extra || {}));
  const shapes = {};
  const drawn = {};
  for (const id of [1, 2, 3]) {
    const inst = game(id, id);
    shapes[id] = {
      keys: Object.keys(inst).sort(),
      duration: inst.duration,
      profile: inst.profile,
      timing: inst.timing,
      timeline: inst.timeline,
      metrics: inst.metrics,
      variant: inst.variant,
      amount: inst.amount,
      bare: inst.bare,
      width: inst.width,
      height: inst.height,
    };
    try {
      for (let step = 0; step <= 24; step += 1) inst.draw((inst.duration * step) / 24);
      drawn[id] = "ok";
    } catch (error) { drawn[id] = String(error && error.stack || error); }
  }
  /* 不传 stage（覆盖层量不到布局 / node 探针 / 导出档）：退参考舞台，不抛错。 */
  const fallback = make({
    variant: 3, profile: "game", amount: 3, bare: true, dpr: 1,
    view: { width: 1440, height: 810 },
  });
  try {
    for (const t of [0, 0.4, 0.9, 1.5, 1.8]) fallback.draw(t);
    drawn.fallback = "ok";
  } catch (error) { drawn.fallback = String(error && error.stack || error); }
  /* 导出档：照素材页的 3.20 / 3.55 / 4.45 秒链（node 探针逐帧导静帧靠它）。 */
  const exported = {};
  for (const id of [1, 2, 3]) {
    const inst = make({ variant: id, profile: "export", amount: id + 1, dpr: 1 });
    exported[id] = { duration: inst.duration, timeline: inst.timeline, profile: inst.profile };
    try {
      for (let step = 0; step <= 24; step += 1) inst.draw((inst.duration * step) / 24);
      drawn["export" + id] = "ok";
    } catch (error) { drawn["export" + id] = String(error && error.stack || error); }
  }
  /* 真实点数：amount = 4 仍按 3 档演，飘字是 +4（不是档位号 3 / 也不是 1）。 */
  texts.length = 0;
  const four = game(3, 4);
  const fourTexts = texts.slice();
  four.draw(four.timeline.NUM + 0.02);
  const fourDrawnTexts = texts.slice();
  function ordered(ms) {
    if (!(ms.total >= 0 && ms.total <= fx.MAX_TOTAL_MS)) return false;
    if (!(ms.num >= 0 && ms.num <= ms.total)) return false;
    if (ms.gather === undefined) return true;
    return ms.gather >= 0 && ms.gather + ms.hold <= ms.burst && ms.burst <= ms.num;
  }
  function messy(id) {
    const out = {};
    out["heal_fx_" + id + "_total"] = 9000;
    out["heal_fx_" + id + "_num"] = 5000;
    if (id === 3) {
      out["heal_fx_3_gather"] = 5000;
      out["heal_fx_3_hold"] = 5000;
      out["heal_fx_3_burst"] = 5000;
    }
    return out;
  }
  const clamps = {
    total: fx.gameTiming(1, { heal_fx_1_total: 9999 }).ms.total,
    numAboveTotal: fx.gameTiming(1, { heal_fx_1_total: 900, heal_fx_1_num: 5000 }).ms.num,
    junk: fx.gameTiming(2, { heal_fx_2_total: "abc", heal_fx_2_num: -1 }).ms,
    missing: fx.gameTiming(1, { heal_fx_1_total: 900 }).ms,
    three: fx.gameTiming(3).ms,
    one: fx.gameTiming(1).ms,
    two: fx.gameTiming(2).ms,
    oneKeys: Object.keys(fx.gameTiming(1).ms),
    twoKeys: Object.keys(fx.gameTiming(2).ms),
    threeKeys: Object.keys(fx.gameTiming(3).ms),
    gatherOverTotal: fx.gameTiming(3, { heal_fx_3_total: 1000, heal_fx_3_gather: 5000 }).ms,
    holdOverTotal: fx.gameTiming(3, { heal_fx_3_gather: 600, heal_fx_3_hold: 5000 }).ms,
    burstBelow: fx.gameTiming(3, {
      heal_fx_3_total: 2000, heal_fx_3_gather: 500, heal_fx_3_hold: 100, heal_fx_3_burst: 10,
    }).ms,
    numBelowBurst: fx.gameTiming(3, { heal_fx_3_burst: 900, heal_fx_3_num: 100 }).ms,
    zero: fx.gameTiming(3, { heal_fx_3_total: 0 }).timeline,
  };
  return JSON.stringify({
    prefix: fx.TIMING_PREFIX,
    maxTotal: fx.MAX_TOTAL_MS,
    fields: fx.TIMING_FIELDS,
    keys: fx.TIMING_KEYS,
    defaults: fx.DEFAULT_TIMING,
    frozen: [Object.isFrozen(fx.TIMING_FIELDS), Object.isFrozen(fx.TIMING_KEYS)],
    surface: Object.keys(fx).sort(),
    hasTierFor: Object.prototype.hasOwnProperty.call(fx, "tierFor"),
    hasVariantOf: Object.prototype.hasOwnProperty.call(fx, "variantOf"),
    tiers: { 1: fx.TIERS[1].duration, 2: fx.TIERS[2].duration, 3: fx.TIERS[3].duration },
    shapes: shapes,
    exported: exported,
    fallback: {
      stage: fallback.stage,
      duration: fallback.duration,
      timeline: fallback.timeline,
    },
    four: {
      variant: four.variant,
      amount: four.amount,
      texts: fourTexts,
      drawnTexts: fourDrawnTexts,
      numberText: four.metrics.numberText,
    },
    clamps: clamps,
    ordered: [1, 2, 3].map((id) => ordered(fx.gameTiming(id).ms)),
    orderedMessy: [1, 2, 3].map((id) => ordered(fx.gameTiming(id, messy(id)).ms)),
    warp: {
      1: fx.gameTiming(1).warp,
      2: fx.gameTiming(2).warp,
      3: fx.gameTiming(3).warp,
    },
    drawn: drawn,
  });
})()`, context, { filename: "heal_probe.js" });
process.stdout.write(report);
"""


def _heal_fx_probe() -> dict:
    """跑真 `fx-heal.js`（node vm + 记录型 2D 上下文），拿回解算 / 实例 / 导出链的实测结果。"""
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "heal_probe.mjs"
        probe.write_text(_HEAL_FX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"回复体力画面探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def heal_fx_probe() -> dict:
    """一次探针跑完，形状 / 钳制 / 导出链几条用例共用。"""
    return _heal_fx_probe()


def test_the_heal_family_scripts_are_listed_next_to_their_hp_loss_twins() -> None:
    """票 04 §3：`fx-heal.js` 紧跟 `fx-hp-loss.js`、`sfx-heal.js` 紧跟 `sfx-hp-loss.js`。

    spec §硬约束把这条次序逐名列出来了（`fx-fire` → `fx-hp-loss` → `fx-heal` → 音效组 →
    `sfx-ui.js` → `main.js`）。"跟着失去体力那一份"而不是"跟着火那一份"：两个体力家族是
    **并列**的两份，画面 / 音效各自按家族顺序排，票 03 的
    `srcs.index("fx-hp-loss.js") == srcs.index("fx-fire.js") + 1` 因此仍然成立。
    """
    srcs = _script_srcs()
    for name in ("fx-heal.js", "sfx-heal.js"):
        assert name in srcs, f"index.html 的脚本清单里必须有 {name}：{srcs}"
    assert srcs.index("fx-heal.js") == srcs.index("fx-hp-loss.js") + 1, (
        f"fx-heal.js 必须紧跟 fx-hp-loss.js（画面组按家族顺序）：{srcs}"
    )
    assert srcs.index("sfx-heal.js") == srcs.index("sfx-hp-loss.js") + 1, (
        f"sfx-heal.js 必须紧跟 sfx-hp-loss.js（音效组按家族顺序）：{srcs}"
    )
    assert srcs.index("fx-fire.js") < srcs.index("fx-hp-loss.js") < srcs.index("fx-heal.js"), (
        f"spec §硬约束的画面顺序：fx-fire → fx-hp-loss → fx-heal：{srcs}"
    )
    assert srcs.index("fx-heal.js") < srcs.index("sfx.js"), (
        "画面组整组排在音效组之前（总谱对：fx-core / fx-thunder / fx-fire / fx-hp-loss / fx-heal）"
    )
    assert srcs.index("sfx-heal.js") < srcs.index("sfx-ui.js"), (
        "sfx-ui.js 的声道池读它的 gameTiming ⇒ 必须排在它之前"
    )
    assert srcs.index("fx-heal.js") < srcs.index("sfx-heal.js"), (
        "画面在音效之前（spec §硬约束的清单顺序）"
    )


def test_the_heal_renderer_keeps_the_hp_loss_renderer_shape(heal_fx_probe: dict) -> None:
    """`SgsHealFX` 与 `SgsHpLossFX` / `SgsFireFX` 同形：排播层不认第二种形状。

    - 导出面：`create` / `gameTiming` / `DEFAULT_TIMING` / `TIMING_PREFIX` / `TIMING_FIELDS` /
      `TIMING_KEYS` / `MAX_TOTAL_MS`，字段名由 `DEFAULT_TIMING` 生成（唯一真源）；
    - **没有 `tierFor` / `variantOf`**（档位判据只有 `vital-fx.js:vitalFxVariantOf` 一处）；
    - `create()` 返回 `draw / duration / timeline / timing / metrics / stage`；
    - 牌桌档：`duration = total`（秒）；`total / num` 全档都有，`gather / hold / burst`
      **只有 3 档**（1 / 2 档不硬造这三个键）；
    - 真实点数：`amount: 4` 按 3 档演，飘字是 `+4`；
    - 量不到座位矩形 ⇒ 退参考舞台，逐帧 draw 不抛错。
    """
    probe = heal_fx_probe
    assert probe["prefix"] == "heal_fx_"
    assert probe["maxTotal"] == 4500, "单档总时长上限 4500（3+ 档是时停窗口，4.5s 是它的窗口）"
    assert probe["fields"] == ["total", "num", "gather", "hold", "burst"], (
        f"字段名由 DEFAULT_TIMING 的键生成（`total / num` 全档、`gather / hold / burst` 只 3 档）："
        f"{probe['fields']}"
    )
    assert probe["keys"] == list(probe["defaults"]), "TIMING_KEYS 必须就是 DEFAULT_TIMING 的键"
    assert probe["frozen"] == [True, True], "TIMING_FIELDS / TIMING_KEYS 是冻结数组"
    derived: list[str] = []
    for key in probe["defaults"]:
        field = key[len(probe["prefix"]):].split("_", 1)[1]
        if field not in derived:
            derived.append(field)
    assert list(probe["fields"]) == derived, (
        f"`TIMING_FIELDS` 必须由 `DEFAULT_TIMING` 的键生成：期望 {derived}，实际 {list(probe['fields'])}"
    )
    for name in (
        "create", "gameTiming", "DEFAULT_TIMING", "TIMING_PREFIX",
        "TIMING_FIELDS", "TIMING_KEYS", "MAX_TOTAL_MS",
    ):
        assert name in probe["surface"], f"SgsHealFX 缺少导出 {name}：{probe['surface']}"
    assert probe["hasTierFor"] is False and probe["hasVariantOf"] is False, (
        "档位判据只有 `vitalFxVariantOf` 一处：模块不许再导出一份 tierFor / variantOf（spec §档位）"
    )
    source = _strip_js_comments(_HEAL_JS.read_text(encoding="utf-8"))
    assert "tierFor" not in source and "variantOf" not in source, (
        "画面模块里不许残留第二份档位判据（素材包的 tierFor / 自造的 variantOf）"
    )

    fields_of_tier = {
        "1": {"total", "num"},
        "2": {"total", "num"},
        "3": {"total", "num", "gather", "hold", "burst"},
    }
    for variant in ("1", "2", "3"):
        shape = probe["shapes"][variant]
        for key in (
            "draw", "duration", "timeline", "timing", "metrics", "stage",
            "variant", "profile", "amount", "bare", "width", "height",
        ):
            assert key in shape["keys"], f"{variant} 档的实例缺少 {key}：{shape['keys']}"
        timing = shape["timing"]
        timeline = shape["timeline"]
        assert shape["profile"] == "game" and shape["bare"] is True
        assert shape["width"] == 1440 and shape["height"] == 810, "牌桌档按 view 建画布"
        assert set(timing) == fields_of_tier[variant], (
            f"{variant} 档的字段集：`total / num` 全档、3 档才多 gather / hold / burst —— "
            f"{sorted(timing)}"
        )
        assert timing["total"] <= probe["maxTotal"], (
            f"{variant} 档总时长必须 ≤ SgsHealFX.MAX_TOTAL_MS（{probe['maxTotal']}）"
        )
        assert shape["duration"] == pytest.approx(timing["total"] / 1000)
        assert timeline["DURATION"] == pytest.approx(shape["duration"])
        assert timeline["TIER"] == int(variant), "时间轴带上档位号（音效按档选音色）"
        assert 0 < timeline["GLOW"] < timeline["NUM"] < timeline["DURATION"]
        assert timeline["NUM"] == pytest.approx(timing["num"] / 1000, abs=1e-3), (
            "飘字时刻读 ini 的 num；3+ 档 `num` 与 `burst` 同刻（票 07）时 `warpPairs` "
            f"把牌桌锚点抬 0.1ms（严格递增），读数差 ≤ 1ms：{timeline['NUM']} vs {timing['num']}"
        )
        if variant == "3":
            assert timeline["GATHER"] == pytest.approx(timing["gather"] / 1000), "聚气**结束**时刻"
            assert timeline["BURST"] == pytest.approx(timing["burst"] / 1000), "迸发时刻"
            assert timeline["HOLD"] == pytest.approx(timing["hold"] / 1000), "白住时长"
            assert timeline["GATHER"] + timeline["HOLD"] == pytest.approx(timeline["BURST"]), (
                f"聚气结束 + 白住时长 = 迸发时刻：{timeline}"
            )
            assert timeline["GATHER"] < timeline["BURST"] <= timeline["NUM"], (
                f"拍子必须按「聚气 → 白住 → 迸发 → 飘字」排：{timeline}"
            )
        else:
            assert timeline["GATHER"] == 0 and timeline["BURST"] == 0 and timeline["HOLD"] == 0, (
                f"{variant} 档没有聚气 / 白住 / 迸发这三拍：{timeline}"
            )
        metrics = shape["metrics"]
        assert metrics["number"] > 0
        assert metrics["numberBox"]["height"] == pytest.approx(metrics["number"])
        assert metrics["number"] <= 0.35 * metrics["card"]["h"] + 0.05, (
            "字高不得超过牌高 35%（压住体力读数）"
        )
        assert metrics["numberBox"]["top"] >= metrics["keepOut"] + 0.02 * metrics["card"]["h"] - 0.05, (
            "飘字包围盒的顶边不得压到体力条（留 2% 牌高余量）"
        )
        assert 0 < metrics["whiteAlpha"] < 1, (
            "白化的峰值必须是**半透明白**叠在覆盖层上（1 = 不透明板，牌桌被盖住）："
            f"{metrics['whiteAlpha']}"
        )
        assert probe["drawn"][variant] == "ok", f"{variant} 档逐帧绘制抛错：{probe['drawn'][variant]}"

    four = probe["four"]
    assert four["variant"] == 3, "amount ≥ 3 按 3 档演"
    assert four["amount"] == 4, "飘字数字来自服务端 amount，不是档位号"
    assert four["numberText"] == "+4", f"飘字必须是真实点数（amount = 4 ⇒ +4）：{four['numberText']!r}"
    assert "+4" in four["texts"], f"字身要用 fillText 画出来：{four['texts']}"
    assert "3" not in four["texts"], (
        f"不许把档位号当点数写（amount = 4 时写 +3 是错的）：{four['texts']}"
    )

    fallback = probe["fallback"]
    assert fallback["stage"]["dst"]["w"] > 0 and fallback["stage"]["dst"]["h"] > 0
    assert probe["drawn"]["fallback"] == "ok", f"退参考舞台那一分支绘制抛错：{probe['drawn']['fallback']}"


def test_the_heal_export_chain_keeps_the_material_package_beats(heal_fx_probe: dict) -> None:
    """导出档照素材页的 3.20 / 3.55 / 4.45 秒链（`gather` 0.22→1.24、`hold` 0.30、`burst` 1.54）。

    牌桌档只把**拍子**压到 ini 上：「比例不变」的判据是 `warp` 的锚点——素材页的
    `gather.at` / 聚气结束 / `burst.at` / `numAt` / `duration` 各自对到 ini 的
    `gather` / `burst` / `num` / `total` 上，**段内线性**。
    """
    probe = heal_fx_probe
    assert probe["tiers"] == {"1": 3.20, "2": 3.55, "3": 4.45}, (
        f"导出档三档时长照素材页：{probe['tiers']}"
    )
    expected = {
        "1": {"duration": 3.20, "GLOW": 0.16, "NUM": 0.54, "TIER": 1},
        "2": {"duration": 3.55, "GLOW": 0.16, "NUM": 0.54, "TIER": 2},
        "3": {
            "duration": 4.45, "GLOW": 0.16, "NUM": 1.60, "TIER": 3,
            "GATHER_START": 0.22, "GATHER": 1.24, "HOLD": 0.30, "BURST": 1.54,
        },
    }
    for variant, want in expected.items():
        export = probe["exported"][variant]
        assert export["profile"] == "export"
        assert export["duration"] == pytest.approx(want["duration"])
        timeline = export["timeline"]
        assert timeline["DURATION"] == pytest.approx(want["duration"])
        for key in ("GLOW", "NUM", "TIER"):
            assert timeline[key] == pytest.approx(want[key]), f"{variant} 档导出档的 {key}: {timeline}"
        if variant == "3":
            for key in ("GATHER_START", "GATHER", "HOLD", "BURST"):
                assert timeline[key] == pytest.approx(want[key]), (
                    f"3 档导出档的 {key}：{timeline}"
                )
            assert timeline["GATHER"] == pytest.approx(timeline["GATHER_START"] + 1.02), (
                "素材页的聚气段是 0.22 → 1.24（1.02s）"
            )
            assert timeline["GATHER"] + timeline["HOLD"] == pytest.approx(timeline["BURST"])
        else:
            assert timeline["BURST"] == 0 and timeline["GATHER"] == 0 and timeline["HOLD"] == 0
        assert probe["drawn"]["export" + variant] == "ok", (
            f"导出档 {variant} 逐帧绘制抛错：{probe['drawn']['export' + variant]}"
        )

    # 牌桌档的锚点：素材页秒 → 牌桌秒（段内线性，端点精确落在 ini 的毫秒上）。
    anchors = {
        "1": [(0.54, 0.350), (3.20, 1.100)],
        "2": [(0.54, 0.350), (3.55, 1.300)],
        # 牌桌端点读 ini 的缺省（票 07：gather 2000 / burst 2500 / num 2500 / total 4500）。
        # 聚气起点按素材页的比例压成 2000 × 0.22 / 1.24 ≈ 355ms；burst 与 num 同刻时
        # `warpPairs` 仍把牌桌端点抬 0.1ms（游戏侧锚点必须严格递增）⇒ 1.60 对 2.5001。
        "3": [
            (0.22, 2000 * (0.22 / 1.24) / 1000),
            (1.24, 2.000), (1.54, 2.500), (1.60, 2500 / 1000 + 1e-4), (4.45, 4.500),
        ],
    }
    for variant in ("1", "2", "3"):
        pairs = probe["warp"][variant]
        assert pairs[0] == {"e": 0, "g": 0}, f"{variant} 档锚点从 (0, 0) 开始：{pairs}"
        want_pairs = anchors[variant]
        assert len(pairs) == len(want_pairs) + 1, f"{variant} 档锚点对：{pairs}"
        for got, (export_at, game_at) in zip(pairs[1:], want_pairs):
            assert got["e"] == pytest.approx(export_at), (
                f"{variant} 档的素材页端点必须逐拍对上：{pairs}"
            )
            assert got["g"] == pytest.approx(game_at), f"{variant} 档的牌桌端点：{pairs}"

    # 聚气起点的口径：`gather` 是聚气**结束**的时刻，起点的比例与素材页一致。
    source = _HEAL_JS.read_text(encoding="utf-8")
    assert "gather" in source and "聚气" in source, "聚气起点的换算要在注释里写清（票面 §1）"


def test_the_heal_timing_table_clamps_inside_the_module(heal_fx_probe: dict) -> None:
    """缺键 / 非法值退 `DEFAULT_TIMING`；钳制在**模块内部**：
    `gather + hold ≤ burst ≤ num ≤ total ≤ 4500`。

    三档缺省 = 用户 2026-11-11 实机手调的口径（票 07；spec §游戏档缺省时长）：1 = 1100
    （num 350）、2 = 1300（num 350）、3 = 4500（gather 2000 / hold 500 / burst 2500 /
    num 2500；聚气 44.4% / 白住 11.1% / 迸发 55.6% / 余韵 44.4%，尾巴与聚气等长）。
    `SGS-INTEGRATION` §6.2 的示例表仍是不动比例的参考；失去体力与伤害的 2200 不动。
    `gather` 是聚气**结束**的时刻、`hold` 是白住**时长**、`burst` 是迸发**时刻** —— 所以
    `2000 + 500 = 2500 = burst` 是这张表自洽的判据（票面 §1 的"聚气 ≤ 白住起点 ≤ 迸发"）。
    """
    probe = heal_fx_probe
    clamps = probe["clamps"]
    assert clamps["one"] == {"total": 1100, "num": 350}, f"1 档缺省：{clamps['one']}"
    assert clamps["two"] == {"total": 1300, "num": 350}, f"2 档缺省：{clamps['two']}"
    assert clamps["three"] == {
        "total": 4500, "gather": 2000, "hold": 500, "burst": 2500, "num": 2500,
    }, f"3 档缺省按用户实机手调的口径（票 07）：{clamps['three']}"
    assert clamps["three"]["gather"] + clamps["three"]["hold"] == clamps["three"]["burst"], (
        f"2000 + 500 = 2500 = 迸发时刻（`gather` 是聚气结束、`hold` 是白住时长）：{clamps['three']}"
    )
    assert clamps["oneKeys"] == ["total", "num"] and clamps["twoKeys"] == ["total", "num"], (
        "1 / 2 档**只有** total / num（不硬造 gather / hold / burst）："
        f"{clamps['oneKeys']} / {clamps['twoKeys']}"
    )
    assert clamps["threeKeys"] == ["total", "num", "gather", "hold", "burst"], (
        f"3 档五个字段齐全：{clamps['threeKeys']}"
    )
    assert probe["ordered"] == [True, True, True], "三档缺省都在钳制范围内"
    assert probe["orderedMessy"] == [True, True, True], (
        "五个字段全给非法大数时仍须守住 gather + hold ≤ burst ≤ num ≤ total ≤ 4500"
    )
    assert clamps["total"] == 4500, "total 超出 MAX_TOTAL_MS 即钳到 4500（票 06 的新上限）"
    assert clamps["numAboveTotal"] == 900, "num ≤ total"
    assert clamps["junk"] == {"total": 1300, "num": 350}, (
        f"非法值（非数字 / 负数）退回内置默认：{clamps['junk']}"
    )
    assert clamps["missing"] == {"total": 900, "num": 350}, (
        f"只改 total，其余退回内置默认：{clamps['missing']}"
    )
    assert clamps["gatherOverTotal"] == {
        "total": 1000, "gather": 1000, "hold": 0, "burst": 1000, "num": 1000,
    }, f"gather 超上限时钳到 total，白住与迸发跟着收在 total：{clamps['gatherOverTotal']}"
    assert clamps["holdOverTotal"] == {
        "total": 4500, "gather": 600, "hold": 3900, "burst": 4500, "num": 4500,
    }, f"hold 不许把迸发顶出 total：{clamps['holdOverTotal']}"
    assert clamps["burstBelow"] == {
        "total": 2000, "gather": 500, "hold": 100, "burst": 600, "num": 2000,
    }, f"burst 小于聚气结束时被抬到 gather + hold（不是退默认）；num 的缺省 2500 超过 total ⇒ 钳到 total：{clamps['burstBelow']}"
    assert clamps["numBelowBurst"] == {
        "total": 4500, "gather": 2000, "hold": 500, "burst": 2500, "num": 2500,
    }, f"num 不许早于迸发：{clamps['numBelowBurst']}"
    assert clamps["zero"]["DURATION"] == 0, "total = 0（用户关掉这一档）⇒ 一条空时间轴"
    # 缺省值本身是实机手感的口径（票 07；改 ini 等于改用户可见的节奏；换掉缺省先改 spec）。
    defaults = probe["defaults"]
    assert [defaults[f"heal_fx_{v}_total"] for v in (1, 2, 3)] == [1100, 1300, 4500]
    assert [defaults[f"heal_fx_{v}_num"] for v in (1, 2, 3)] == [350, 350, 2500]
    assert defaults["heal_fx_3_gather"] == 2000
    assert defaults["heal_fx_3_hold"] == 500
    assert defaults["heal_fx_3_burst"] == 2500
    assert not any(key.startswith(("heal_fx_1_gather", "heal_fx_2_gather")) for key in defaults), (
        f"1 / 2 档不许有 gather / hold / burst 的键：{sorted(defaults)}"
    )


def test_anim_ini_lists_the_heal_durations_per_variant(heal_fx_probe: dict) -> None:
    """`anim.ini` 的 `heal_fx_<档>_<字段>` 键块与 `SgsHealFX.DEFAULT_TIMING` 逐键同值。

    键表**不在测试里写第二份**：`SgsHealFX.TIMING_KEYS` 是唯一真源，这里只与磁盘上的
    `anim.ini` 对表（同剑光 / 雷电 / 火焰 / 铁索 / 阵亡 / 失去体力的姿态）。
    """
    entries = {
        key: value for key, value in _ini_entries().items()
        if _HEAL_TIMING_KEY.match(key)
    }
    expected = set(heal_fx_probe["keys"])
    assert set(entries) == expected, (
        f"回复体力的时长键必须与 fx-heal 的 DEFAULT_TIMING 一致："
        f"缺 {sorted(expected - set(entries))}；多 {sorted(set(entries) - expected)}"
    )
    values = {key: float(value) for key, value in entries.items()}
    max_total = heal_fx_probe["maxTotal"]
    for variant in (1, 2):
        total = values[f"heal_fx_{variant}_total"]
        num = values[f"heal_fx_{variant}_num"]
        assert 0 < num < total <= max_total, (
            f"{variant} 档的绝对毫秒必须递增且 total ≤ {max_total}：num={num} / total={total}"
        )
    total = values["heal_fx_3_total"]
    gather = values["heal_fx_3_gather"]
    hold = values["heal_fx_3_hold"]
    burst = values["heal_fx_3_burst"]
    num = values["heal_fx_3_num"]
    assert 0 < gather < burst <= num < total <= max_total, (
        f"3 档的四个绝对毫秒必须递增（迸发与飘字同刻允许相等）且 total ≤ {max_total}："
        f"gather={gather} / burst={burst} / num={num} / total={total}"
    )
    assert gather + hold == burst, (
        f"聚气结束 + 白住时长 = 迸发时刻（2000 + 500 = 2500）：{gather} + {hold} != {burst}"
    )
    assert hold / total == pytest.approx(0.111, abs=0.01), (
        f"白住占整段的 11.1%（票 07 实机手调：500 / 4500）：{hold / total:.3f}"
    )
    assert gather / total == pytest.approx(0.444, abs=0.01), (
        f"聚气占整段的 44.4%（票 07 实机手调：2000 / 4500）：{gather / total:.3f}"
    )
    totals = [values[f"heal_fx_{variant}_total"] for variant in (1, 2, 3)]
    assert totals == [1100, 1300, 4500], (
        f"spec §游戏档缺省时长（票 06 改判 3+ = 4500，票 07 定拍子）：{totals}"
    )
    ini_text = _ANIM_INI.read_text(encoding="utf-8")
    assert "体力演出 · 回复体力" in ini_text, "键块要有自己的标题（用户改得动）"
    assert values == {
        key: float(value) for key, value in heal_fx_probe["defaults"].items()
    }, "内置默认必须与磁盘上的 anim.ini 同值（否则删一行 ini 会静默改变三档节奏）"


def test_the_heal_module_never_freezes_playback_itself_and_has_no_screen_flash() -> None:
    """回复的 3+ 档时停**不写在这份文件里**：真源是家族表的 `holdsAt: 3`（票 02）。

    - 画面模块不登记 `registerPlaybackHold`（登记在 `vital-fx.js` 文件末尾）；
    - 没有整屏闪：不碰 `.fx-flash`、不碰 `#table-shell`、不挂 CSS `filter`；
    - `bare` 下的白化是**半透明白**叠在覆盖层上（不隐藏牌面），峰值是**具名常量**
      `BARE_WHITE_PEAK`（导出档另有一个同值的 `EXPORT_WHITE_PEAK`：两条路径各一具名常量，
      不是散落的字面量），注释写明素材页的 α 是照浅色样例牌调的、**最终值以实机为准**。
    """
    source = _strip_js_comments(_HEAL_JS.read_text(encoding="utf-8"))
    assert "registerPlaybackHold" not in source, (
        "回复 3+ 档的时停登记在 vital-fx.js（家族表 holdsAt），画面模块不碰暂停表"
    )
    assert "PlaybackHold" not in source, "画面模块不认识排播侧的暂停判据"
    assert "fx-flash" not in source, "不能碰整屏闪的着色子层"
    assert "table-shell" not in source, "不碰表壳（也就不会有 filter / containing block）"
    assert not re.search(r"\.style\.filter\b", source), "不许给任何节点挂 CSS filter"
    assert "getComputedStyle" not in source, "不读 DOM 样式（覆盖层与布局是排播层的事）"
    match = re.search(r"var BARE_WHITE_PEAK = ([0-9.]+);", source)
    assert match is not None, "bare 下的白化峰值必须是一个**命名常量**（可调、可实机校）"
    assert 0 < float(match.group(1)) < 1, (
        f"BARE_WHITE_PEAK 必须在 (0, 1) 之间（1 = 不透明的白板）：{match.group(1)}"
    )
    raw = _HEAL_JS.read_text(encoding="utf-8")
    assert "实机" in raw, "注释里要写明最终白化的 α 以实机为准（票面 §1 的 bare 口径）"
    assert "样例牌" in raw or "真实牌面" in raw, "注释里要写明素材页的 α 是照样例牌调的"
    # 牌桌档不画宣纸底与样例牌（bare 口径）：两份贴图只在导出档取。
    assert "bare ? null" in source, "宣纸 / 样例牌必须按 bare 分支跳过"


_HEAL_SFX_PROBE = r"""
import { readFileSync } from "node:fs";
import vm from "node:vm";

const staticPath = (name) => `tools/sgs_web/static/${name}`;

/* 假 Web Audio（与票 03 那张垫片同源，多一个 `createDelay`：回复的磬 / 泛音要一条反馈延迟
 * 留尾）。每个节点只有 connect / start / stop 与参数 setter；`start(when)` 的**时刻**记下来
 * ——"cue 从时间轴读"就靠它判。 */
function makeParam() {
  const p = {
    value: 0,
    setValueAtTime(v) { p.value = v; return p; },
    exponentialRampToValueAtTime(v) { p.value = v; return p; },
    linearRampToValueAtTime(v) { p.value = v; return p; },
    setTargetAtTime(v) { p.value = v; return p; },
  };
  return p;
}

function makeNode(kind, graph) {
  const node = {
    kind,
    connect() { return node; },
    disconnect() {},
    start(when) {
      graph.started += 1;
      if (typeof when === "number" && isFinite(when)) graph.whens.push(when);
    },
    stop() {},
  };
  if (kind === "gain") node.gain = makeParam();
  if (kind === "oscillator") { node.frequency = makeParam(); node.type = "sine"; }
  if (kind === "bufferSource") { node.buffer = null; node.loop = false; }
  if (kind === "filter") { node.frequency = makeParam(); node.Q = makeParam(); node.type = "lowpass"; }
  if (kind === "delay") node.delayTime = makeParam();
  if (kind === "compressor") {
    node.threshold = makeParam(); node.knee = makeParam(); node.ratio = makeParam();
    node.attack = makeParam(); node.release = makeParam();
  }
  return node;
}

function makeGraph() {
  const graph = { nodes: 0, started: 0, whens: [] };
  const ctx = {
    sampleRate: 44100,
    currentTime: 0,
    state: "running",
    destination: { connect() {} },
    resume() { return Promise.resolve(); },
    createGain() { graph.nodes += 1; return makeNode("gain", graph); },
    createOscillator() { graph.nodes += 1; return makeNode("oscillator", graph); },
    createBufferSource() { graph.nodes += 1; return makeNode("bufferSource", graph); },
    createBiquadFilter() { graph.nodes += 1; return makeNode("filter", graph); },
    createDelay() { graph.nodes += 1; return makeNode("delay", graph); },
    createDynamicsCompressor() { graph.nodes += 1; return makeNode("compressor", graph); },
    createBuffer(channels, length) {
      const data = new Float32Array(Math.max(1, length));
      return {
        numberOfChannels: channels, length: data.length, sampleRate: 44100,
        getChannelData: () => data,
      };
    },
  };
  return { ctx, graph };
}

const context = vm.createContext({
  console, setTimeout, clearTimeout, Math, Array, Float32Array, Object, JSON, Promise,
  document: { createElement: () => ({ width: 0, height: 0, getContext: () => null }) },
});
context.globalThis = context;
context.window = context;
context.makeGraph = makeGraph;
vm.runInContext(readFileSync(staticPath("fx-heal.js"), "utf8"), context, { filename: "fx-heal.js" });
vm.runInContext(readFileSync(staticPath("sfx-heal.js"), "utf8"), context, { filename: "sfx-heal.js" });

const report = await vm.runInContext(`(async () => {
  const fx = window.SgsHealFX;
  const sfx = window.SgsHealSfx;

  let offline = null;
  window.OfflineAudioContext = function OfflineAudioContext() {
    const made = makeGraph();
    made.ctx.startRendering = () => Promise.resolve({
      numberOfChannels: 2,
      length: 128,
      sampleRate: 44100,
      getChannelData: () => new Float32Array(128).fill(0.1),
    });
    offline = made.graph;
    return made.ctx;
  };

  const live = makeGraph();
  window.AudioContext = function AudioContext() { return live.ctx; };

  const out = {};
  for (const id of [1, 2, 3]) {
    const clock = fx.gameTiming(id);
    const timeline = Object.assign({}, clock.timeline);
    const lane = sfx.create(timeline);
    let error = null;
    let started = false;
    const before = { nodes: live.graph.nodes, sources: live.graph.started };
    try {
      started = lane.start();
      const frames = 24;
      for (let step = 1; step <= frames; step += 1) {
        lane.tick((clock.timeline.DURATION * step) / frames, { dmg: id });
      }
      lane.mute(true);
      lane.mute(false);
      lane.stop();
    } catch (e) { error = String(e && e.stack || e); }

    let offlineError = null;
    let levels = null;
    try {
      levels = await sfx.renderOffline(id, timeline, clock.timeline.DURATION + 0.3);
    } catch (e) { offlineError = String(e && e.stack || e); }

    out[id] = {
      started, error,
      nodes: live.graph.nodes - before.nodes,
      sources: live.graph.started - before.sources,
      offlineError,
      offlinePeak: levels ? levels.total.peak : null,
      offlineRms: levels ? levels.total.rms : null,
      whens: (offline ? offline.whens.slice().sort((a, b) => a - b) : []),
      timeline: {
        glow: clock.timeline.GLOW,
        gatherStart: clock.timeline.GATHER_START,
        gather: clock.timeline.GATHER,
        hold: clock.timeline.HOLD,
        burst: clock.timeline.BURST,
        num: clock.timeline.NUM,
        duration: clock.timeline.DURATION,
        tier: clock.timeline.TIER,
      },
    };
    offline = null;
  }
  return JSON.stringify({
    surface: Object.keys(sfx).sort(),
    tiers: out,
  });
})()`, context, { filename: "heal_sfx_probe.js" });
process.stdout.write(report);
"""


def test_the_heal_sfx_lane_schedules_every_tier_without_a_real_audio_context() -> None:
    """`SgsHealSfx` 是**车道适配器**（`create(timeline)` → `{start, stop, mute, tick}`）：
    三档都能建图、按时间轴排完、收声，且 cue 时刻**跟着时间轴走**。

    素材页的 3+ 档是「吸气（1.02s）→ 留白（~0.3s）→ 落点 → 长余韵」；牌桌档压到牌桌时长
    （3+ 档 4.5s，票 06）之后，这三拍的位置**由 `SgsHealFX.create()` 写进时间轴**（`GATHER_START / GATHER / HOLD / BURST`），
    音效模块不另写一份秒数——所以判据是"吸气落在聚气起点、落点落在迸发、飘字落在 NUM"。
    烟测里没有 `AudioContext`（垫片故意不建），这一条补上**真模块**这一侧。
    """
    node = shutil.which("node")
    assert node is not None, "本测试需要 node（门禁的 ui_smoke 同源依赖）"
    with tempfile.TemporaryDirectory() as workdir:
        probe = Path(workdir) / "heal_sfx_probe.mjs"
        probe.write_text(_HEAL_SFX_PROBE, encoding="utf-8")
        result = subprocess.run(
            [node, str(probe)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert result.returncode == 0, (
        f"回复体力音效探针失败（exit {result.returncode}）：\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    probe = json.loads(result.stdout)
    for name in ("create", "renderOffline"):
        assert name in probe["surface"], f"SgsHealSfx 缺少 {name}：{probe['surface']}"
    for variant in ("1", "2", "3"):
        tier = probe["tiers"][variant]
        assert tier["error"] is None, f"{variant} 档排程抛错：{tier['error']}"
        assert tier["started"] is True, f"{variant} 档 start() 建出了共享图"
        assert tier["nodes"] > 0 and tier["sources"] > 0, f"{variant} 档一个音源都没排出来"
        assert tier["offlineError"] is None, f"{variant} 档离线排程抛错：{tier['offlineError']}"
        assert tier["offlinePeak"] and tier["offlinePeak"] > 0, (
            f"{variant} 档离线渲染一个采样都没有（峰值 {tier['offlinePeak']}）"
        )
        timeline = tier["timeline"]
        whens = tier["whens"]
        assert whens, f"{variant} 档没有任何 start(when) 时刻"
        assert whens[0] == pytest.approx(timeline["glow"], abs=0.02), (
            f"{variant} 档的第一声是起光（在时间轴的 GLOW）：{whens[:3]}"
        )
        assert any(
            timeline["num"] - 0.02 <= when <= timeline["num"] + 0.02 for when in whens
        ), f"{variant} 档缺少飘字那一声（NUM = {timeline['num']}）：{whens}"
        assert max(whens) <= timeline["duration"] + 0.05, (
            f"{variant} 档不许排到摘层之后（DURATION = {timeline['duration']}）：{max(whens)}"
        )
        if variant == "3":
            assert any(
                timeline["gatherStart"] - 0.02 <= when <= timeline["gatherStart"] + 0.02
                for when in whens
            ), f"3 档缺少吸气的起点（GATHER_START = {timeline['gatherStart']}）：{whens}"
            assert any(
                timeline["burst"] - 0.02 <= when <= timeline["burst"] + 0.06 for when in whens
            ), f"3 档缺少迸发那一声（BURST = {timeline['burst']}）：{whens}"
    assert probe["tiers"]["3"]["sources"] > probe["tiers"]["1"]["sources"], (
        "3 档（吸气 + 停顿 + 落点 + 余韵）排出的音源必须多于 1 档（一声清磬）："
        f"{probe['tiers']['3']['sources']} vs {probe['tiers']['1']['sources']}"
    )
