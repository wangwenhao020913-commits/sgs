"""武将立绘的资源索引与 ``/art/`` 服务（hero-portraits 票 01）。

判据来自 ``.scratch/hero-portraits/issues/01-立绘资源与索引.md`` §判据。立绘是
**gitignored 的用户素材**（``pic/portraits/``），所以这里一条测试都不读本机的
``pic/``：一律用 ``SGS_ART_DIR`` 指到 ``tmp_path``——既走环境变量这条**真产品路径**，
也顺手钉住"手编作者文件后刷新即生效"（无缓存）。

两个缝：

- **模块缝** :func:`sgs_web.portraits.portrait_index`：目录树 → 画像表（作者解码）。
- **HTTP 缝**：真的 ``serve._Handler``（进程内假 socket，不开端口、不起线程）——
  ``/art/`` 的 200 / 404 / ``Cache-Control`` / 魔数 MIME 与 ``/api/portraits`` 的载荷。
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap() -> None:
    """把 ``tools/`` 与 ``src/`` 放进 ``sys.path``（与 ``tests/test_web.py`` 同一手法）。"""
    for entry in (REPO_ROOT / "tools", REPO_ROOT / "src"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


_bootstrap()

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x03fake-jpeg-tail"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake-png-tail"


# ---------------------------------------------------------------------------
# 1. 模块缝：目录树 → 画像表
# ---------------------------------------------------------------------------


def _portrait_dir(root: Path, true_name: str = "关羽", name: str = "关羽") -> Path:
    directory = root / true_name / name
    directory.mkdir(parents=True)
    return directory


def test_index_maps_the_display_name_to_a_portrait_relative_path_and_author(
    tmp_path: Path, monkeypatch
) -> None:
    """有图目录 ⇒ ``{显示名称: {portrait, author}}``；相对路径用 ``/``、相对立绘根。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "关羽.jpg").write_bytes(JPEG_BYTES)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index() == {
        "关羽": {"portrait": "关羽/关羽/关羽.jpg", "author": ""},
    }


def test_an_empty_hero_directory_is_a_slot_without_a_portrait(
    tmp_path: Path, monkeypatch
) -> None:
    """没图的武将 = 空目录（立绘空位的槽）：``portrait`` 为 ``None``，不是缺键。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    _portrait_dir(root, "左慈", "左慈")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index() == {"左慈": {"portrait": None, "author": ""}}


def test_a_portrait_sniffed_as_png_is_indexed_under_its_real_extension(
    tmp_path: Path, monkeypatch
) -> None:
    """生成工具按魔数落盘（``.jpg`` 实为 PNG 时存 ``.png``）；索引按实际文件名收。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root, "于吉", "于吉")
    (directory / "于吉.png").write_bytes(PNG_BYTES)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["于吉"]["portrait"] == "于吉/于吉/于吉.png"


def test_a_missing_art_tree_is_an_empty_index_not_an_error(
    tmp_path: Path, monkeypatch
) -> None:
    """``SGS_ART_DIR`` 指到不存在的目录 ⇒ ``{}``（出厂无立绘：页面走空位态）。"""
    from sgs_web.portraits import portrait_index

    monkeypatch.setenv("SGS_ART_DIR", str(tmp_path / "not-there"))

    assert portrait_index() == {}


def test_the_index_rescans_on_every_call_so_a_hand_edited_author_shows_up(
    tmp_path: Path, monkeypatch
) -> None:
    """无缓存：用户手编 ``作者.txt`` 后**刷新页面**即生效，不用重跑生成工具。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == ""
    (directory / "作者.txt").write_text("画师：甲", encoding="utf-8")
    assert portrait_index()["关羽"]["author"] == "画师：甲"


def test_absent_author_txt_means_an_empty_author(tmp_path: Path, monkeypatch) -> None:
    """作者 txt 不存在 ⇒ ``""``（前端据此**一个节点都不渲染**）。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    _portrait_dir(root)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == ""


def test_whitespace_only_author_txt_means_an_empty_author(
    tmp_path: Path, monkeypatch
) -> None:
    """空白（含换行 / 制表）⇒ strip 后为空 ⇒ ``""``，不是几个空格。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "作者.txt").write_text(" \n\t\r\n ", encoding="utf-8")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == ""


def test_a_gbk_encoded_author_txt_is_decoded(tmp_path: Path, monkeypatch) -> None:
    """Windows 记事本的 ANSI 档是 GBK：``utf-8-sig`` → ``utf-8`` → ``gbk`` 逐级回退。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "作者.txt").write_bytes("画师：张三".encode("gbk"))
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == "画师：张三"


def test_a_utf8_author_txt_with_a_bom_loses_only_the_bom(
    tmp_path: Path, monkeypatch
) -> None:
    """记事本存 UTF-8 会带 BOM：``utf-8-sig`` 先试，作者文本里不许留 ``\\ufeff``。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "作者.txt").write_bytes("画师：甲".encode("utf-8-sig"))
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    author = portrait_index()["关羽"]["author"]
    assert author == "画师：甲"
    assert "\ufeff" not in author


def test_a_multiline_author_txt_is_returned_verbatim(
    tmp_path: Path, monkeypatch
) -> None:
    """多行 ⇒ 原样返回（含换行）；前端零正则、零拼接，读到的就是 txt 的全部正文。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    text = "画师：甲\n协力：乙\n（2026 春）\n"
    (directory / "作者.txt").write_text(text, encoding="utf-8")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == text


def test_the_author_filename_is_matched_case_insensitively(
    tmp_path: Path, monkeypatch
) -> None:
    """Windows 上扩展名可能被写成 ``.TXT``；文件名按大小写不敏感认。"""
    from sgs_web.portraits import portrait_index

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "作者.TXT").write_text("画师：甲", encoding="utf-8")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    assert portrait_index()["关羽"]["author"] == "画师：甲"


# ---------------------------------------------------------------------------
# 2. HTTP 缝：真的 ``serve._Handler``（进程内假 socket；不开端口、不起线程）
# ---------------------------------------------------------------------------


def _handler_socket(raw: bytes):
    """假 socket：只补 stdlib handler 需要的 ``makefile`` / ``sendall``（同 test_web.py）。"""
    import io as _io

    class _Buffer(_io.BytesIO):
        def close(self):  # handler.finish() 会关流；响应还要读，故忽略关闭
            pass

    class _Socket:
        def __init__(self, payload: bytes) -> None:
            self._reader = _Buffer(payload)
            self._writer = _Buffer()

        def makefile(self, mode, *args, **kwargs):  # noqa: ANN002, ANN003 - 基类签名
            return self._reader if "r" in mode else self._writer

        def sendall(self, data: bytes) -> None:
            self._writer.write(data)

    return _Socket(raw)


def _raw_get(path: str, runs_dir: Path) -> tuple[int, dict[str, str], bytes]:
    """把一条 ``GET`` 交给真的 ``_Handler``，返回 ``(状态码, 小写头, 响应体)``。"""
    from sgs_web.serve import GameRegistry, _Handler

    raw = f"GET {path} HTTP/1.1\r\nHost: test\r\n\r\n".encode("ascii")
    fake = _handler_socket(raw)
    handler_cls = type(
        "_BoundHandler",
        (_Handler,),
        {"registry": GameRegistry(runs_dir=runs_dir), "quiet": True},
    )
    handler_cls(fake, ("127.0.0.1", 0), None)
    head, _, body = fake._writer.getvalue().partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(b":")
        headers[name.strip().decode("ascii").lower()] = value.strip().decode("latin-1")
    return status, headers, body


def _art_path(*segments: str) -> str:
    """``/art/<真名>/<显示名称>/<文件名>``（浏览器发的是百分号编码的中文）。"""
    return "/art/" + "/".join(quote(segment) for segment in segments)


def test_art_serves_a_hit_with_the_image_bytes_and_a_day_long_cache(
    tmp_path: Path, monkeypatch
) -> None:
    """命中 ⇒ 原图字节 + ``image/jpeg`` + ``max-age=86400``（其余静态资源仍是 no-store）。"""
    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "关羽.jpg").write_bytes(JPEG_BYTES)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    status, headers, body = _raw_get(
        _art_path("关羽", "关羽", "关羽.jpg"), tmp_path / "runs"
    )

    assert status == 200
    assert headers["content-type"] == "image/jpeg"
    assert headers["cache-control"] == "public, max-age=86400"
    assert body == JPEG_BYTES


def test_art_mime_follows_the_magic_bytes_not_the_extension(
    tmp_path: Path, monkeypatch
) -> None:
    """资源包里有 5 个 `.jpg` 实为 PNG 容器 ⇒ 发 ``image/png``（别按扩展名猜）。"""
    root = tmp_path / "portraits"
    directory = _portrait_dir(root, "于吉", "于吉")
    (directory / "于吉.jpg").write_bytes(PNG_BYTES)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    status, headers, body = _raw_get(
        _art_path("于吉", "于吉", "于吉.jpg"), tmp_path / "runs"
    )

    assert status == 200
    assert headers["content-type"] == "image/png"
    assert body == PNG_BYTES


def test_art_returns_404_with_a_non_html_body_for_an_empty_slot(
    tmp_path: Path, monkeypatch
) -> None:
    """空目录（无图武将）⇒ 404；**不许**回落 ``index.html``（``<img>`` 拿到 HTML 只会坏图）。"""
    root = tmp_path / "portraits"
    _portrait_dir(root, "关羽", "界关羽")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    status, headers, body = _raw_get(
        _art_path("关羽", "界关羽", "界关羽.jpg"), tmp_path / "runs"
    )

    assert status == 404
    assert "text/html" not in headers["content-type"]
    assert b"<html" not in body.lower()


def test_art_returns_404_when_the_art_tree_does_not_exist(
    tmp_path: Path, monkeypatch
) -> None:
    """立绘根不存在（出厂无立绘）⇒ 全部 404，页面自然走空位态。"""
    monkeypatch.setenv("SGS_ART_DIR", str(tmp_path / "not-there"))

    status, headers, body = _raw_get(
        _art_path("关羽", "关羽", "关羽.jpg"), tmp_path / "runs"
    )

    assert status == 404
    assert "text/html" not in headers["content-type"]
    assert b"<html" not in body.lower()


def test_art_refuses_traversal_outside_the_portrait_root(
    tmp_path: Path, monkeypatch
) -> None:
    """``../``（含百分号编码的 ``%2e%2e%2f``）⇒ 404，且绝不吐根外的文件。"""
    root = tmp_path / "portraits"
    root.mkdir()
    (tmp_path / "secret.jpg").write_bytes(JPEG_BYTES)
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    for path in ("/art/../secret.jpg", "/art/%2e%2e%2fsecret.jpg"):
        status, headers, body = _raw_get(path, tmp_path / "runs")
        assert status == 404, path
        assert "text/html" not in headers["content-type"], path
        assert body != JPEG_BYTES, path
        assert b"<html" not in body.lower(), path


def test_api_portraits_returns_the_index_and_is_never_cached(
    tmp_path: Path, monkeypatch
) -> None:
    """``GET /api/portraits`` ⇒ ``{"heroes": portrait_index()}``，``no-store``（会现扫）。"""
    import json

    root = tmp_path / "portraits"
    directory = _portrait_dir(root)
    (directory / "关羽.jpg").write_bytes(JPEG_BYTES)
    (directory / "作者.txt").write_text("画师：甲", encoding="utf-8")
    monkeypatch.setenv("SGS_ART_DIR", str(root))

    status, headers, body = _raw_get("/api/portraits", tmp_path / "runs")

    assert status == 200
    assert headers["cache-control"] == "no-store"
    assert json.loads(body) == {
        "heroes": {"关羽": {"portrait": "关羽/关羽/关羽.jpg", "author": "画师：甲"}}
    }


def test_api_portraits_returns_an_empty_object_without_a_portrait_tree(
    tmp_path: Path, monkeypatch
) -> None:
    """``SGS_ART_DIR`` 指到一个**存在但没有立绘树**的目录 ⇒ ``{"heroes": {}}``（不抛）。"""
    import json

    empty = tmp_path / "empty-art"
    empty.mkdir()
    monkeypatch.setenv("SGS_ART_DIR", str(empty))

    status, headers, body = _raw_get("/api/portraits", tmp_path / "runs")

    assert status == 200
    assert json.loads(body) == {"heroes": {}}
    assert headers["cache-control"] == "no-store"


def test_static_content_types_cover_png_and_jpeg(
    tmp_path: Path, monkeypatch
) -> None:
    """``_CONTENT_TYPES`` 补齐图片类型（``/art/`` 自己按魔数判，但这张表别处也会用）。"""
    from sgs_web import serve

    monkeypatch.setattr(serve, "STATIC_ROOT", tmp_path)
    (tmp_path / "probe.png").write_bytes(PNG_BYTES)
    (tmp_path / "probe.jpg").write_bytes(JPEG_BYTES)

    png_status, png_headers, _ = _raw_get("/probe.png", tmp_path / "runs")
    jpg_status, jpg_headers, _ = _raw_get("/probe.jpg", tmp_path / "runs")

    assert (png_status, png_headers["content-type"]) == (200, "image/png")
    assert (jpg_status, jpg_headers["content-type"]) == (200, "image/jpeg")


# ---------------------------------------------------------------------------
# 3. 生成工具：A+B+C 匹配、幂等、作者文件跨重建保留
# ---------------------------------------------------------------------------


def _load_build_portraits():
    """按路径加载 ``tools/build-portraits.py``（文件名带连字符，不能当模块 import）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_portraits_under_test", REPO_ROOT / "tools" / "build-portraits.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_pack(pack: Path) -> None:
    """最小资源包（``<组>/<目录名>/形象.jpg``）：三张图分别命中 A / B / C 三条规则。"""
    for group, name in (("组一", "关羽"), ("组二", "于吉"), ("组三", "于禁")):
        directory = pack / group / name
        directory.mkdir(parents=True)
        (directory / "形象.jpg").write_bytes(JPEG_BYTES)


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_build_portraits_applies_a_b_c_and_writes_the_index_snapshot(
    tmp_path: Path,
) -> None:
    """A 精确 / B 去版字尾 / C 去「·」前缀；没图 ⇒ 空目录 + 快照里 ``portrait: None``。"""
    import json

    module = _load_build_portraits()
    pack = tmp_path / "pack"
    _fake_pack(pack)
    out = tmp_path / "pic" / "portraits"
    module.OUT = out

    assert module.main(["--pack", str(pack)]) == 0

    rows = {
        row["name"]: row
        for row in json.loads((out / "index.json").read_text(encoding="utf-8"))["heroes"]
    }
    assert (rows["关羽"]["rule"], rows["关羽"]["portrait"]) == (
        "A",
        "关羽/关羽/关羽.jpg",
    )
    assert (rows["于吉移动版"]["rule"], rows["于吉移动版"]["portrait"]) == (
        "B",
        "于吉/于吉移动版/于吉移动版.jpg",
    )
    assert (rows["锋·于禁"]["rule"], rows["锋·于禁"]["portrait"]) == (
        "C",
        "于禁/锋·于禁/锋·于禁.jpg",
    )
    assert rows["界关羽"]["portrait"] is None
    assert (out / "关羽" / "界关羽").is_dir()
    assert not any((out / "关羽" / "界关羽").iterdir()), "没图的武将是空目录"


def test_rebuilding_portraits_is_idempotent_and_keeps_author_files(
    tmp_path: Path,
) -> None:
    """重跑产出逐字节相同；用户手编的 ``作者.txt`` 跨重建原样保留（否则重跑就丢作者）。"""
    module = _load_build_portraits()
    pack = tmp_path / "pack"
    _fake_pack(pack)
    out = tmp_path / "pic" / "portraits"
    module.OUT = out

    assert module.main(["--pack", str(pack)]) == 0
    author = out / "关羽" / "关羽" / "作者.txt"
    author.write_bytes("画师：甲\n".encode("utf-8"))
    before = _tree_snapshot(out)

    assert module.main(["--pack", str(pack)]) == 0

    assert author.read_bytes() == "画师：甲\n".encode("utf-8")
    assert _tree_snapshot(out) == before, "第二跑应与第一跑（含作者文件）逐字节相同"


def test_a_missing_resource_pack_does_not_wipe_the_existing_tree(
    tmp_path: Path,
) -> None:
    """资源包不在（换机器忘挂盘）⇒ 非零退出、**先判后删**，已有立绘树一字不动。"""
    module = _load_build_portraits()
    out = tmp_path / "pic" / "portraits"
    existing = out / "关羽" / "关羽" / "关羽.jpg"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(JPEG_BYTES)
    module.OUT = out

    assert module.main(["--pack", str(tmp_path / "no-such-pack")]) == 1
    assert existing.read_bytes() == JPEG_BYTES
