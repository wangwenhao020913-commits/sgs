"""声明式输入的加载与校验：内容包与规则配置。

公开接缝只在此再导出，外部不得 import 本子包的深层路径。

票 03 §6 冻结的三个名字是 ``load_content_pack`` / ``ContentPack`` / ``DataLoadError``；
此外只多两个经确认的接缝：``validate_content_pack``（坏数据的注入点，主缝进不去）、
``LoadError``（``DataLoadError.errors`` 的元素类型，供断言与展示）。
票 09 把 ``RuleConfig`` 也判在本子包（"声明式输入的加载与校验"），随票 18 的信封落地，
故公开面再增 :class:`RuleConfig` 与其进料边界 :func:`validate_rule_config`。
票 24 再增身份与模式的规则常数（:class:`Identity` / :class:`Mode` / :func:`roster_for`）：
它们与规则配置同层，都是"进料侧"的不可变事实。
票 26 把 :class:`Suit`（枚举本就存在）纳入公开面：判定后果按花色判（【乐不思蜀】非红桃 /
【闪电】黑桃），行为层需要花色这一**牌面事实**的枚举真源来解析 ``CardRef.suit`` 的字符串，
不能各自重抄 ``"heart"`` 字面量。票 29 以同姿态把 :class:`Color` 与 :func:`color_of_suit`
纳入公开面：【仁王盾】的"黑色【杀】对你无效"要按**颜色**判，而颜色由花色派生。
票 01（M3）把**武将面**纳入公开面：:class:`Hero` / :class:`HeroSkill` / :class:`HeroTable` /
:class:`Kingdom` / :class:`SkillType` 与其加载 / 只读查询（``load_heroes`` / ``validate_heroes`` /
``hero_skill_id`` / ``hero_skill_ids``）——数据文件只装"武将是什么"，
技能行为仍住 ``skills/``（ADR-009）。
票 02（M3）再增**发放**与**启用池**两个只读面：:func:`deal_heroes`（开局按座位序决定
谁坐哪，走 ``hero`` 子流）与 :func:`enabled_hero_ids`（启用门控的只读查询）。

M6 票 02 把**牌名大类** :class:`Category` 与按 ``pack_id`` 的只读查询
:func:`card_category` 纳入公开面：【集智】的非延时锦囊与【奇才】的锦囊都按它判族
（"这张牌是锦囊吗"），而不是各自抄一份牌名白名单。
"""

from .errors import DataLoadError, LoadError
from .heroes import HeroDealError, deal_heroes
from .identities import IDENTITY_LABELS, IDENTITY_ROSTERS, Identity, Mode, roster_for
from .loader import (
    HERO_SUPPLEMENT_PACK_IDS,
    card_category,
    enabled_hero_ids,
    hero_card_skill_ids,
    hero_skill_id,
    hero_skill_ids,
    load_content_pack,
    load_hero_tables,
    load_heroes,
    validate_content_pack,
    validate_heroes,
)
from .models import (
    Category,
    Color,
    ContentPack,
    Gender,
    Hero,
    HeroSkill,
    HeroTable,
    Kingdom,
    SkillType,
    Suit,
    color_of_suit,
)
from .rule_config import RuleConfig, validate_rule_config

__all__ = (
    "Category",
    "Color",
    "ContentPack",
    "DataLoadError",
    "Gender",
    "Hero",
    "HeroDealError",
    "HeroSkill",
    "HeroTable",
    "HERO_SUPPLEMENT_PACK_IDS",
    "IDENTITY_LABELS",
    "IDENTITY_ROSTERS",
    "Identity",
    "Kingdom",
    "LoadError",
    "Mode",
    "RuleConfig",
    "SkillType",
    "Suit",
    "card_category",
    "color_of_suit",
    "deal_heroes",
    "enabled_hero_ids",
    "hero_card_skill_ids",
    "hero_skill_id",
    "hero_skill_ids",
    "load_content_pack",
    "load_hero_tables",
    "load_heroes",
    "roster_for",
    "validate_content_pack",
    "validate_heroes",
    "validate_rule_config",
)
