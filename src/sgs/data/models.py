"""内容包的数据模型（只装身份字段）。

结构层用 pydantic v2：``frozen=True`` + ``extra="forbid"``（字段名写错必须报错，
不能静默忽略）。``颜色`` 由花色派生、``属性`` 与行为字段（使用时机 / 目标规则 /
次数限制 / 效果列表）都不在这里 —— 前者军争才引入，后者归 effects 层（票 07）。

牌实例的 ``id`` **不在这里**：数据只给牌堆清单，实例化由 state/engine 在开局时做，
所以数据文件可以有同型同花色同点数的两张牌。

M3 票 01 追加**武将面**（``Kingdom`` / ``SkillType`` / ``HeroSkill`` / ``Hero`` /
``HeroTable``）：数据文件只装"武将是什么"（id / 名称 / 势力 / 性别 / 体力上限 /
技能清单 / 版本 / 启用），技能**行为**仍住 ``skills/`` 的 Python（ADR-009）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CARD_TAG_TYPES",
    "DEFAULT_SLOT_LIMIT",
    "CardTagClear",
    "CardTagReader",
    "CardTagSpec",
    "CardTagType",
    "CardTagVisibility",
    "Category",
    "CardType",
    "Color",
    "ContentPack",
    "Deck",
    "DeckCard",
    "DeckExpect",
    "Gender",
    "Hero",
    "HeroDealConfig",
    "HeroPick",
    "HeroSkill",
    "HeroTable",
    "Kingdom",
    "PlacementDecl",
    "PlacementReturn",
    "PlacementVisibility",
    "SkillType",
    "Suit",
    "card_tag_spec",
    "color_of_suit",
]

DEFAULT_SLOT_LIMIT = 2
"""同一 (花色,点数) 槽位的**默认**上限；``Deck.slot_limit`` 可逐档覆盖。

标准版含 EX 的 108 张档位在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位各有三张，故该档显式写 3；
基本 53 张档位沿用默认 2。它是"未声明时取几"，不是全局放宽（票 09 §3）。
"""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Suit(str, Enum):
    """花色；颜色由它派生（heart/diamond → 红，spade/club → 黑）。"""

    HEART = "heart"
    DIAMOND = "diamond"
    SPADE = "spade"
    CLUB = "club"


class Color(str, Enum):
    """牌的**颜色**（票 12 §Q4 的取值表）：由花色派生，是【仁王盾】一类效果的判据。

    无色（``None``）在 M2 只会由**转化牌 / 虚拟牌**产生（票 30）；实体牌恒有颜色。
    真源是花色到颜色的映射（:func:`color_of_suit`），故没有第二份可漂移的数据。
    """

    RED = "red"
    BLACK = "black"


_RED_SUITS = frozenset({Suit.HEART, Suit.DIAMOND})
_BLACK_SUITS = frozenset({Suit.SPADE, Suit.CLUB})


def color_of_suit(suit: Suit | str) -> Color | None:
    """由花色派生颜色；未知花色返回 ``None``（fail-open 到"无色"，不做静默猜测）。

    接受 ``Suit`` 或它的字符串值：``CardRef.suit`` 是字符串（记录面形状），判定后果与
    装备效果都经本函数解析，故两处不会各自重抄 ``"heart"`` 字面量。
    """
    try:
        resolved = Suit(suit)
    except ValueError:
        return None
    if resolved in _RED_SUITS:
        return Color.RED
    if resolved in _BLACK_SUITS:
        return Color.BLACK
    return None  # pragma: no cover - Suit 是闭集，本条只为全函数性保留


class Gender(str, Enum):
    """角色的**性别**（票 05 §7.1 的【雌雄双股剑】判据）。

    性别在官方里是**武将牌**的事实；M2 一个武将都不落，故没有任何数据源会填它，
    字段默认 ``None`` = "未知"。装备效果里的"异性"判据因此只有在测试人为指定时成立
    （M3 加武将时由武将数据填值，接口与形状都不变）。
    """

    MALE = "male"
    FEMALE = "female"


class Kingdom(str, Enum):
    """武将的**势力**（`GLOSSARY.md`「势力」；M3 票 01 新增）。

    标准版四势力取英文值，中文只出现在渲染层：势力作用域（"其他同势力角色"）判定
    一律比枚举成员，不把中文串塞进状态。扩展的神 / 晋等按"有数据才加"进枚举——
    神势力随神关羽移动版（票 08）入表，与四势力并列为 5 大势力。
    """

    WEI = "wei"
    SHU = "shu"
    WU = "wu"
    QUN = "qun"
    SHEN = "shen"


class SkillType(str, Enum):
    """技能的**类型**（`GLOSSARY.md`「技能类型」；`03` §4「技能描述模型」的闭集；M3 票 01 新增）。

    闭集 = ``NORMAL``（普通技）/ ``LORD``（主公技）/ ``LIMITED``（限定技）/
    ``AWAKENING``（觉醒技）。标准版 25 名只落前两类；``LIMITED`` 从 M6 票 05（界张飞
    【替身】）、``AWAKENING`` 从 M6 票 06（界徐庶【潜心】/ 界吕蒙【勤学】）起有真实载体。
    数据侧的取值是**声明**（供界面与加载期对账）：限定技的派发判据读
    :class:`~sgs.skills.catalog.SkillDef` 上的同一个字段；觉醒技的"强制发动"也读定义上的
    ``skill_type``。类型与 ``locked``（锁定 / 非锁定）**正交**：限定 / 觉醒技仍各自可以是
    锁定或非锁定（本批两条觉醒技都是非锁定，仍受"所有非锁定技失效"覆盖）。
    """

    NORMAL = "NORMAL"
    LORD = "LORD"
    LIMITED = "LIMITED"
    AWAKENING = "AWAKENING"


class PlacementVisibility(str, Enum):
    """一个放置区的**可见性**（M6 票 10；spec D14 的三项声明之一）。

    - ``PUBLIC``：放置区公开——牌面是公开事实（界陆逊【谦逊】的武将牌上的牌是明置的，
      spec §Further Notes 第 3 条）；
    - ``HIDDEN``：只有放置技能的持有者（以及被扣牌的目标本人）可见；M6 无载体。

    取值与 :class:`CardTagVisibility` 同形是**有意的两套词汇**：标签的可见性裁的是
    "牌上的持久事实"，放置区的可见性裁的是"整个区"；两者由不同的读取方（投影的标签
    裁剪 vs 区域的牌面裁剪）消费，合并会让"某区公开、某牌上的标签隐藏"无法表达。
    """

    PUBLIC = "public"
    HIDDEN = "hidden"


class PlacementReturn(str, Enum):
    """一个放置区的**归还时机**（M6 票 10；spec D14 的三项声明之一）。

    - ``TURN_END``：当前回合结束时整批回到放置技能的持有者手里（【谦逊】"于当前回合
      结束时获得之"）；
    - ``NEVER``：不自动归还（如「田」/「权」这类留在武将牌上、由技能自己消费的牌；
      M6 无载体，只登记形状）。

    归还**给谁**不是这条声明的一部分：两项都用"放置技能的持有者"——【谦逊】的"你"就是
    持有者本人；将来有"扣在目标身上、回合末由目标获得"的载体（【破军】）时，再把收件人
    加进声明（那一票才需要它，M6 的落点不预造字段）。
    """

    TURN_END = "turn_end"
    NEVER = "never"


class PlacementDecl(_FrozenModel):
    """一个**放置类技能**在数据里的三项声明（M6 票 10；spec D14 / S8 登记表检查）。

    三项都**必写、无默认值**：缺一项是数据错误（``structure.missing``），不是"按默认
    语义补"——这正是"放置类技能必须显式想清楚三件事"的机械保证。
    """

    visibility: PlacementVisibility
    return_timing: PlacementReturn
    extra_effect: bool
    """放置之外还有没有效果（如【权计】同时 +手牌上限）。

    ``True`` 表示"搬牌只是这条技能的一部分、归还 / 后续由技能自己挂接"——引擎因此
    **不**替它做自动归还（即使 ``return_timing`` 是 ``TURN_END``），避免替一条有附加
    流程的技能抢跑。M6 唯一载体【谦逊】为 ``False``。
    """


class HeroSkill(_FrozenModel):
    """武将牌上的一条技能**条目**：引用技能定义，不装行为。

    ``id`` 是**短 id**（如 ``hujia``）；对外的技能 id = ``hero.<武将 id>.<短 id>``
    （票 01 §Data / 票 12），由 :func:`sgs.data.hero_skill_id` 一处派生。

    条目自带的 ``skill_type`` 是数据侧的声明，加载期会与 ``skills.CATALOG`` 里那份
    定义的 ``SkillType`` 对账（**仅对启用的武将**，见 :mod:`sgs.data.loader`）；
    身份门控的规范落点仍读定义上的字段，不读这里。

    ``granted_by``（M6 票 06）是 **☆ 标记**：非 ``None`` = 这条技能由同武将的某条
    **觉醒技**（短 id）获得。☆ 技能的实例**只**来自"获得"来源
    （``Player.granted_skills``），武将牌载体不产出它
    （:func:`sgs.data.hero_card_skill_ids` 已把它排除），故未觉醒时"没有这个技能"
    是载体解析的结构事实。它是数据字段而不是从名字里的 "☆" 派生："由哪条觉醒技获得"
    本身是行为事实，加载期会校验它指向同一武将的觉醒技。
    """

    id: str
    name: str
    skill_type: SkillType
    granted_by: str | None = None
    placement: PlacementDecl | None = None
    """把牌置于武将牌上的**三项声明**（M6 票 10；``None`` = 不是放置类技能）。

    与 :attr:`skill_type` 同姿态：数据侧声明、加载期与 ``skills.CATALOG`` 交叉核对
    （代码上的 ``SkillDef.places_on_hero_card`` 是另一半）。三项缺一项是**结构错误**；
    代码与数据不一致是**语义错误**——登记表检查因此不空转。
    """


class Hero(_FrozenModel):
    """一名武将：**是什么**（身份事实），不含任何行为。

    ``version`` 区分同名武将的不同版本（标准版 / 界限突破版；ADR-006：界版数据分开存、
    MVP 不启用），本文件只有 ``"standard"``。``enabled`` 是**启用门控**：只有启用的武将
    可被发放，且其技能引用必须在 ``skills.CATALOG`` 里闭合；未启用的只做结构与唯一性校验
    （"技能还没实现"不该拦住整张表）。两个字段都**必写**——"没声明"与"声明为假"是两件事。
    """

    id: str
    name: str
    true_name: str = Field(min_length=1)
    """**真名**（M6 票 04；`GLOSSARY.md`「真名」）：同名武将不同版本共用的那个名字。

    它是**数据字段**，不从显示名派生：4 名只有界版的武将没有可剥的"界"前缀（spec
    §Further Notes 第 6 条），从 `name` 反推必然漏。标准 25 名的真名由票 12 补齐；
    票 12 起两张表都带真名，本字段因此收紧为**必写**（缺字段 / 空串都是结构错误）——
    它是真名分组与真名池的唯一分组键，静默缺一个会让真名池少一个名字。
    真名配将（先抽真名、再抽版本）与"同一真名一局只出现一版"都读这一份。
    """
    kingdom: Kingdom
    gender: Gender
    hp_max: int
    skills: tuple[HeroSkill, ...]
    version: str
    """版本标记：标准版 ``"standard"`` / 界限突破·OL 口径 ``"jie-ol"``（ADR-006 的 OL 口径）/
    移动版 ``"jie-mobile"`` / 独立系列包 ``"feng"``（神话再临·风包，票 03 起）。

    版本是**数据字段**（不是从 id 前缀派生）：同一真名的标准 / 界两版是两个独立武将 id
    （spec §Further Notes 第 6 条），版本字段让"抽到哪一版"可在数据面回答。系列包（风包）
    没有同名版本问题，标记仍取包名——一个真名一局只占一个座位，判据只读 ``true_name``。
    """
    enabled: bool


class HeroTable(_FrozenModel):
    """一份已通过加载期校验的**武将表**（``heroes.<pack_id>.json``）。

    与内容包分开成文件、分开校验：武将面按其自己的 ``schema_version`` 升版，
    与牌堆档位 / 记录流 / 规则版本互不牵动。
    """

    schema_version: int
    pack_id: str
    heroes: tuple[Hero, ...]

    def by_id(self, hero_id: str) -> Hero | None:
        """按 id 取武将；未知 id 返回 ``None``（fail-closed 的调用方自行决定）。"""
        for hero in self.heroes:
            if hero.id == hero_id:
                return hero
        return None


class HeroPick(_FrozenModel):
    """一处**手选**：某个座位指定一名武将**版本**（M6 票 12；`GLOSSARY.md`「手选」）。

    手选无视**禁将**（玩家点名要的版本即使被划掉也照发），但它**先占真名**：同一局里
    别的座位不能再抽到这个真名的任何版本。故 ``hero`` 是版本级 id、真名由数据表派生，
    存两个字段会让"手选的是不是同一个真名"多一个可漂移的真源。
    """

    seat: int = Field(ge=0)
    hero: str = Field(min_length=1)


class HeroDealConfig(_FrozenModel):
    """**真名配将**的会话配置：禁将集合 + 手选（M6 票 12；spec D4 / D5）。

    它是"开局前做过的选择"，与种子一起冻结进**会话配置**（票 13 落到 web 侧）：不进
    记录流、不碰 header，故 47 条标准金录像逐字节不变（与 ``hero_assignments`` 同一条
    纪律）。引擎只接受并遵守它——真名池不足、手选冲突这类裁定都由
    :func:`sgs.data.deal_heroes` 在构造期 fail-closed。

    ``banned_heroes`` 的粒度是**版本**：禁掉标准刘备而留着界版，"刘备"这个真名照样能
    被抽到，只是这一局只能落到界版。``picks`` 的顺序不参与分配（座位号在条目里），
    但它是配置的一部分，故同种子同配置必然复现同一配将结果。
    """

    banned_heroes: tuple[str, ...] = ()
    picks: tuple[HeroPick, ...] = ()


class Category(str, Enum):
    """卡牌大类；M1 数据只用 ``basic``，其余枚举位随 M2 引入锦囊/装备。"""

    BASIC = "basic"
    TRICK = "trick"
    EQUIPMENT = "equipment"


class CardType(_FrozenModel):
    """牌名级定义：这张牌**是什么**。行为字段整体归 effects 层。

    ``attack_range`` / ``delayed`` 是**牌面事实**（票 09 §6）：前者是"距离 ≤ 攻击范围"
    L3 校验的取值来源、只属武器；后者是延时锦囊"置入判定区"的声明面。两者都带默认值，
    故基本 53 张档位一字不改即可通过。

    数据档里【寒冰剑】的 ``attack_range`` 以 **2** 落地。该数值的**来源类型 = 无一手源**
    （本机没有该武器牌面的一手抓取物，取社区通说；票 05 §U-2 / 票 12）：它只标注来源强度，
    引用时不得当成一手事实。
    """

    id: str
    name: str
    category: Category
    subcategory: str | None = None
    attack_range: int | None = None
    delayed: bool = False


class DeckCard(_FrozenModel):
    """牌堆里的一张牌：只写身份引用与花色点数，不重复牌名级属性。"""

    type: str
    suit: Suit
    rank: int


class DeckExpect(_FrozenModel):
    """牌堆的声明式分布期望；加载期语义校验要求它与实况相符。

    ``total`` / ``by_type`` 必写；``by_suit`` / ``by_category`` 可选，缺失即不校验
    —— 基本 53 张档位的花色分布是 13/19/7/14，写不出"每花色 27"，故不写这两项。
    三项都沿用**双向穷尽**语义：实况出现但未声明的键同样报错。
    """

    total: int
    by_type: dict[str, int]
    by_suit: dict[str, int] | None = None
    by_category: dict[str, int] | None = None


class Deck(_FrozenModel):
    """一个牌堆档位；``slot_limit`` 是**声明式分档字段**（默认 2）。

    标准版含 EX 的 108 张在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位上各有三张，故该档显式写 3；
    这是档位属性而不是全局放宽，否则基本 53 张"每槽至多两张"的机械保护会失效。
    """

    slot_limit: int = DEFAULT_SLOT_LIMIT
    expect: DeckExpect | None = None
    cards: tuple[DeckCard, ...]


class ContentPack(_FrozenModel):
    """一个已通过加载期校验的内容包（含全部牌堆档位）。"""

    schema_version: int
    pack_id: str
    deck_profile: str
    card_types: tuple[CardType, ...]
    decks: dict[str, Deck]

    @property
    def deck(self) -> Deck:
        """本内容包默认选中的牌堆档位（校验保证 ``deck_profile`` 必存在）。"""
        return self.decks[self.deck_profile]


# ---------------------------------------------------------------------------
# 牌标签的类型登记（M6 票 09；spec D12 / §标签）
# ---------------------------------------------------------------------------
#
# **标签 = 挂在*一张牌*上的持久事实**（不是座位状态、也不是帧私有标志）：
# 牌在牌堆顶也能带，故它的生存期与"牌在哪个区域"无关。每个标签实例记
# （**标签类型**、**来源实例**），类型本身在下面这张登记表里声明四项——
# 读取方 / 生存期（清除触发）/ 可见性 / 是否随来源实例消失。
#
# 登记表是**数据层**的声明面：`sgs.state.CardTag` 只装值，读取方（手牌计数、投影）
# 与清除点（引擎回合末、技能三态）都按这里的声明做事，不各写一份"哪些标签算数"的名单。
# 未登记的标签类型在 ``tests/test_tags.py`` 里判红（与 ``test_card_family_sites`` 同规格）。


class CardTagReader(str, Enum):
    """标签的**读取方**（D12 四项之一；闭集，新增读取方要先在这里登记）。

    M6 只有一处读取方：``HAND_COUNT`` = 弃牌阶段的**手牌计数**
    （``sgs.effects.hand_count``：手牌中不带"排除计数"标签的张数）。手牌**上限**
    （``sgs.effects.hand_limit``）是另一个查询，两者互不替换（spec D11）。
    """

    HAND_COUNT = "hand_count"


class CardTagClear(str, Enum):
    """标签的**生存期 / 清除触发**（D12 四项之一）。

    三个取值对应 spec §标签 点名的三类形状；清除点只有一个执行入口
    （``Engine.clear_card_tags``），各载体的触发点调它：

    - ``TURN_END``：回合末（``Engine._clear_turn_effects``）——洛神式"本回合不计入"；
    - ``LEAVE_ZONE``：该牌离开标签声明所在的那个区域时——黠慧式"在手牌期间"；
    - ``USED_OR_PLAYED``：该牌被使用或打出时——遗珠式"到被使用 / 打出为止"。

    与"是否随来源实例消失"是**两个轴**：前者按时间 / 事件到期，后者按来源技能存亡撤销。
    """

    TURN_END = "turn_end"
    LEAVE_ZONE = "leave_zone"
    USED_OR_PLAYED = "used_or_played"


class CardTagVisibility(str, Enum):
    """标签的**可见性**（D12 四项之一）：按它裁剪玩家载荷。

    - ``HIDDEN``：引擎内事实，**任何**玩家载荷（含本人）都不带；
    - ``FACE_VISIBLE``：只有**看得到这张牌牌面**的查看者收到（自己的手牌、公开区、
      观战 / 全知）；看不到牌面的人连标签都收不到。M6 三类标签用这一档；
    - ``PUBLIC``：公开事实（如明置的牌上的标记），可随载荷下发（M6 无载体）。
    """

    HIDDEN = "hidden"
    FACE_VISIBLE = "face_visible"
    PUBLIC = "public"


class CardTagType(str, Enum):
    """标签类型的**闭集**（M6 票 09；spec §标签 点名的三类形状）。

    - ``LUOSHEN_HAND``：**本回合不计入**（洛神式；M6 唯一活载体 = 界甄姬【洛神】）；
    - ``ZONE_HAND``：**在某区域（手牌）期间不计入**（黠慧式；M6 无载体，只登记形状）；
    - ``UNTIL_PLAYED``：**到被使用 / 打出为止**（遗珠式；M6 无载体，只登记形状）。
    """

    LUOSHEN_HAND = "luoshen_hand"
    ZONE_HAND = "zone_hand"
    UNTIL_PLAYED = "until_played"


class CardTagSpec(_FrozenModel):
    """一个标签类型的**四项声明**（D12）+ 载体锚点。

    ``carrier`` 是 M6 的活载体技能 id（无载体 = ``None``）：登记表据此回答"这一类形状
    此刻由谁生产"，``tests/test_tags.py`` 会把它与 ``CATALOG`` 对账。
    """

    reader: CardTagReader
    clear_trigger: CardTagClear
    visibility: CardTagVisibility
    disappears_with_source: bool
    carrier: str | None = None


CARD_TAG_TYPES: Mapping[CardTagType, CardTagSpec] = MappingProxyType(
    {
        # 洛神式：界甄姬【洛神】以此法获得的牌"不计入本回合的手牌上限"。回合末到期；
        # 失去 / 失效【洛神】时随来源实例一并清除（计数立刻恢复）。
        CardTagType.LUOSHEN_HAND: CardTagSpec(
            reader=CardTagReader.HAND_COUNT,
            clear_trigger=CardTagClear.TURN_END,
            visibility=CardTagVisibility.FACE_VISIBLE,
            disappears_with_source=True,
            carrier="hero.jie_zhenji.luoshen",
        ),
        # 黠慧式（M6 无载体，为后续铺路）："在手牌期间不计入"。清除触发 = 离开声明区域
        # （手牌）——它不随回合末清，也不因技能存亡撤销以外的方式消失。
        CardTagType.ZONE_HAND: CardTagSpec(
            reader=CardTagReader.HAND_COUNT,
            clear_trigger=CardTagClear.LEAVE_ZONE,
            visibility=CardTagVisibility.FACE_VISIBLE,
            disappears_with_source=True,
        ),
        # 遗珠式（M6 无载体，为后续铺路）："到被使用 / 打出为止"。清除触发 = 这张牌
        # 自己被打出 / 使用；故回合末清理**不**碰它，来源技能存亡也不撤回它
        # （第四项声明因此与上面两类相反——形状测试逐项钉住）。
        CardTagType.UNTIL_PLAYED: CardTagSpec(
            reader=CardTagReader.HAND_COUNT,
            clear_trigger=CardTagClear.USED_OR_PLAYED,
            visibility=CardTagVisibility.FACE_VISIBLE,
            disappears_with_source=False,
        ),
    }
)
"""标签类型登记表（唯一真源）：未登记的类型读不到声明，读取 / 写入都 fail-closed。"""


def card_tag_spec(tag_type: CardTagType) -> CardTagSpec:
    """取一个标签类型的四项声明；**未登记**时显式失败（不静默按"不排除"处理）。

    这是读取方 / 清除点 / 写入口共用的唯一访问器：登记表少一行，行为面立刻是异常而不是
    静默降级——测试的合成反例（``tests/test_tags.py``）只是把这条再钉一次。
    """
    spec = CARD_TAG_TYPES.get(tag_type)
    if spec is None:
        raise KeyError(
            f"标签类型 {tag_type!r} 未在 CARD_TAG_TYPES 登记："
            "D12 要求每个类型声明读取方 / 清除触发 / 可见性 / 是否随来源实例消失"
        )
    return spec
