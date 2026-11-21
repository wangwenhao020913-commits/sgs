#!/usr/bin/env python3
"""生成武将立绘资源文件夹（只读外部资源包，只写 ``pic/``）。

口径（用户 2026-11-21 裁决，见 ``.scratch/hero-portraits/DECISIONS.md``）：

- 匹配规则 A+B+C：名字精确 → 去掉「移动版 / 标准版 / 自制版 / OL版 / 版」字尾 →
  「·」前缀只取末段。**不借真名**（界 / 神 / SP 缺图就是缺图，不拿标准版的图顶）。
- 目录形状：``<真名>/<显示名称>/<显示名称>.<扩展名>``；没图也建**空目录**（立绘空位的槽）。
  扩展名按**魔数**定（资源包里有 5 个 ``.jpg`` 实为 PNG 容器）。
- 落点：``SGS/pic/portraits/``（``pic/`` 已 gitignored —— 立绘是厂商版权素材，不进公开仓）。
- 幂等：重跑会重建这棵树；``作者.txt`` 是**用户手编**的，跨重建按原字节保留。
- ``pic/portraits/index.json`` 是**生成期快照**（人看用；服务端每次现扫目录，不读它）。

用法::

    .venv/bin/python tools/build-portraits.py
    .venv/bin/python tools/build-portraits.py --pack /mnt/d/SGS_HEROS
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # SGS/
DEFAULT_PACK = Path("/mnt/d/SGS_HEROS")  # 外部资源包（Windows 上是 D:\SGS_HEROS）
OUT = ROOT / "pic" / "portraits"

sys.path.insert(0, str(ROOT / "tools"))
from sgs_web import descriptions as desc_mod  # noqa: E402  （描述文案的唯一来源）
from sgs_web.portraits import AUTHOR_FILENAME  # noqa: E402  （作者文件名的唯一来源）

KINGDOM_LABELS = {"wei": "魏", "shu": "蜀", "wu": "吴", "qun": "群", "shen": "神"}
SUFFIXES = ("移动版", "标准版", "自制版", "OL版", "版")


def noise(text: str) -> str:
    """目录名的噪音：``√`` 后缀与空白。"""
    return re.sub(r"[√\s]", "", text)


def load_heroes() -> list[dict]:
    """仓内全部武将（标准表 + 各版本包，文件顺序即数据顺序）。"""
    heroes: list[dict] = []
    for path in sorted((ROOT / "src" / "sgs" / "data" / "files").glob("heroes.*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        heroes.extend(document.get("heroes") or [])
    return heroes


def build_pack_index(pack: Path) -> dict[str, Path]:
    """规范目录名 → 形象图路径（443 个目录里 427 个有图）。"""
    index: dict[str, Path] = {}
    for hero_dir in sorted(pack.glob("*/*")):
        if not hero_dir.is_dir():
            continue
        image = hero_dir / "形象.jpg"
        if image.is_file():
            index.setdefault(noise(hero_dir.name), image)
    return index


def match(hero: dict, pack: dict[str, Path]) -> tuple[Path | None, str]:
    """A+B+C 匹配；返回 ``(图路径, 规则)``（规则写进快照，供人核对）。"""
    name = noise(hero["name"])
    if name in pack:
        return pack[name], "A"
    for suffix in SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            stripped = name[: -len(suffix)]
            if stripped in pack:
                return pack[stripped], "B"
            break
    if "·" in name:
        tail = name.split("·")[-1]
        if tail in pack:
            return pack[tail], "C"
    return None, "-"


def sniff_ext(path: Path) -> str:
    """按魔数定扩展名（资源包里有 5 个 ``.jpg`` 实为 PNG 容器）。"""
    head = path.read_bytes()[:8]
    if head.startswith(b"\x89PNG"):
        return ".png"
    return ".jpg"


def snapshot_authors(out: Path) -> dict[str, bytes]:
    """重建前把用户手编的作者文件按**相对路径 → 原字节**存下来（大小写不敏感）。"""
    wanted = AUTHOR_FILENAME.lower()
    if not out.is_dir():
        return {}
    return {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.name.lower() == wanted
    }


def restore_authors(out: Path, authors: dict[str, bytes]) -> int:
    """把作者文件写回**仍然存在**的武将目录（数据里已删掉的武将不复活目录）。"""
    restored = 0
    for relative, raw in authors.items():
        target = out / relative
        if not target.parent.is_dir():
            continue
        target.write_bytes(raw)
        restored += 1
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build-portraits",
        description="把外部资源包里的武将形象图整理成 pic/portraits/<真名>/<显示名称>/。",
    )
    parser.add_argument(
        "--pack",
        type=Path,
        default=DEFAULT_PACK,
        help=f"外部资源包根目录（缺省 {DEFAULT_PACK}；里面是 <组>/<武将>/形象.jpg）。",
    )
    args = parser.parse_args(argv)

    pack = build_pack_index(args.pack)
    if not pack:
        # **先判后删**：资源包不在（换机器忘挂盘）时绝不能把已有立绘树清空。
        print(f"资源包里没有形象图：{args.pack}（未改动 {OUT}）", file=sys.stderr)
        return 1
    heroes = load_heroes()
    print(f"资源包目录 {len(pack)} 个有图；仓库武将 {len(heroes)} 个")

    authors = snapshot_authors(OUT)
    if OUT.exists():
        shutil.rmtree(OUT)
    rows = []
    hit = miss = 0
    for hero in heroes:
        source, rule = match(hero, pack)
        hero_dir = OUT / hero["true_name"] / hero["name"]
        hero_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "id": hero["id"],
            "name": hero["name"],
            "true_name": hero["true_name"],
            "kingdom": hero["kingdom"],
            "kingdom_label": KINGDOM_LABELS.get(hero["kingdom"], ""),
            "hp_max": hero["hp_max"],
            "gender": hero.get("gender"),
            "version": hero.get("version"),
            "enabled": bool(hero.get("enabled", True)),
            "skills": [
                {
                    "id": skill["id"],
                    "name": skill["name"],
                    "skill_type": skill.get("skill_type", "NORMAL"),
                    "desc": desc_mod.skill_description(f"hero.{hero['id']}.{skill['id']}"),
                    "desc_trim": desc_mod.display_skill_description(
                        f"hero.{hero['id']}.{skill['id']}"
                    ),
                }
                for skill in hero.get("skills") or []
            ],
            "portrait": None,
            "rule": rule,
        }
        if source is None:
            miss += 1
        else:
            hit += 1
            target = hero_dir / f"{hero['name']}{sniff_ext(source)}"
            shutil.copyfile(source, target)
            record["portrait"] = target.relative_to(OUT).as_posix()
        rows.append(record)

    restored = restore_authors(OUT, authors)
    (OUT / "index.json").write_text(
        json.dumps(
            {"schema_version": 1, "root": str(OUT), "heroes": rows},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    total = sum(
        (OUT / row["portrait"]).stat().st_size for row in rows if row["portrait"]
    )
    print(f"有图 {hit} / 空位 {miss} / 共 {len(rows)}")
    print(f"落盘 {total / 1048576:.1f} MB → {OUT}")
    if authors:
        print(f"保留作者文件 {restored}/{len(authors)} 份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
