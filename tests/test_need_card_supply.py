"""**需要（使用 / 打出）牌**时的「需求 × 供给」：两个值对象 + 匹配器 + 供需闸门。

三件事在这里钉住：

1. 两个对象：:class:`~sgs.effects.Demand`（维度可加的精确需求）与
   :class:`~sgs.effects.Supply`（候选期的供给描述），以及匹配器
   :func:`~sgs.effects.supply_satisfies`。**无花色 / 无点数**的供给**确定地不满足**
   对应维度的限制（用户 2026-11-20 裁定，spec §3 第 3 条）；``unknown=True`` 的供给在
   候选期不可知，匹配器**不判死**。
2. 需求生产者：每个需要牌窗口（``CARD_REQUEST_WINDOWS`` 里 ``providers=True`` 的 8 行）
   都产出一句需求，且这一问**留在面上的候选**至少有一条满足它（闸门不误删）。
3. 合法性层（票 02）：供需闸门只删**交给玩家的面**——已知且满足的供给照常进面，无花色
   供给在花色需求下不进面，候选期不可知（``unknown``）的供给在限制生效时失败关闭。

**票 05 / 06**（spec §0.1b）：提示区的供给行表、投影字段与"按档分组 / 行理由 / 灰行池"
那一层已随用户 2026-11-21 的裁定删除；本文件只钉**值对象 + 匹配 + 闸门**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:  # 与多数测试同一条引导（单独跑时也能 import）
    sys.path.insert(0, str(REPO_ROOT / "src"))

from sgs.data import Identity, RuleConfig, color_of_suit, load_content_pack  # noqa: E402
from sgs.effects import (  # noqa: E402
    Demand,
    Supply,
    demand_for,
    legal_actions_for,
    supplies_of_action,
    supply_is_legal,
    supply_satisfies,
)
from sgs.skills import CATALOG  # noqa: E402
from sgs.state import (  # noqa: E402
    ActivateSkill,
    DodgeContext,
    PeachContext,
    QueryKind,
    RespondContext,
    RespondMode,
    UseCard,
    WindowCause,
)

from support import SeatSpec, build_state, cards_of_type, deck_excluding  # noqa: E402

PACK = load_content_pack(deck_profile="standard_108")
"""标准 108 张档（显式选档：模块级加载必须写死档位，票 32）。"""

PACK_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 张档：**求桃自救的【酒】**只在军争档里（标准 108 没有这张牌）。"""


def _state_with(
    hand: tuple[int, ...],
    *,
    equip: tuple[int, ...] = (),
    hero: str | None = None,
    identity: Identity | None = None,
    pack=PACK,
):
    """座位 0 = 给定手牌 / 装备 / 武将 / 身份（供给样本都在它身上），座位 1 = 空手牌。

    ``identity`` 只服务**主公技**那一族（【护驾】/【激将】只在身份为主公时解析出实例，
    ``_identity_gates_out`` 那条既有门控）。
    """
    used = set(hand) | set(equip)
    return build_state(
        pack=pack,
        seats=[SeatSpec(hand=hand, equip=equip, hero=hero, identity=identity), SeatSpec()],
        deck=deck_excluding(pack, *used),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2, first_seat=0),
    )


# ---------------------------------------------------------------------------
# 1. 两个对象 + 匹配器
# ---------------------------------------------------------------------------


def test_a_supply_without_a_suit_never_satisfies_a_suit_demand():
    """§4-3：无花色供给**确定地不满足**花色需求（不是"未知"）——【八卦阵】的虚拟【闪】。

    判据来源 = spec §3 第 3 条（用户 2026-11-20 裁定）：``None`` 是"恒无"，不是"不知道"；
    "不知道"由 ``unknown=True`` 单独表达。
    """
    demand = Demand(suits=("diamond", "club"))
    bagua = Supply(product="dodge", kind="virtual")

    assert supply_satisfies(demand, bagua) is False


def test_a_real_card_supply_satisfies_a_suit_demand_only_when_its_suit_is_listed():
    """真牌供给按牌面花色判：白名单里的花色满足，别的不满足。"""
    demand = Demand(suits=("diamond", "club"))

    assert supply_satisfies(demand, Supply(product="dodge", suit="diamond", rank=8))
    assert supply_satisfies(demand, Supply(product="dodge", suit="club", rank=2))
    assert not supply_satisfies(demand, Supply(product="dodge", suit="heart", rank=3))


def test_none_means_unrestricted_and_an_empty_whitelist_satisfies_nothing():
    """值的三态：``None`` = 不限；``()`` = 记录为空 ⇒ 无法响应（连有花色的供给也不行）。"""
    real = Supply(product="dodge", suit="diamond", rank=8)

    assert supply_satisfies(Demand(), real), "全维度 None = 不限"
    assert supply_satisfies(Demand(suits=None), real)
    assert not supply_satisfies(Demand(suits=()), real)
    assert not supply_satisfies(Demand(suits=()), Supply(product="dodge", kind="virtual"))


def test_an_unknown_supply_is_not_judged_dead_in_this_ticket():
    """``unknown=True`` = 候选期不可知（【蛊惑】/【护驾】/【激将】）⇒ 本票只标出来、不判死。"""
    demand = Demand(suits=("diamond",))

    relay = Supply(product="dodge", kind="relay", unknown=True)
    preconversion = Supply(product="dodge", kind="preconversion", unknown=True)

    assert supply_satisfies(demand, relay) is True
    assert supply_satisfies(demand, preconversion) is True


def test_the_card_name_dimension_reuses_the_slash_family_key():
    """牌名维度读**既有**的族判据（``CardBehavior.count_key``），不另抄一张名单。

    【南蛮】/【决斗】要的是【杀】族：火【杀】/ 雷【杀】与普通【杀】同族（``is_slash``
    与 :func:`sgs.effects.satisfies_required` 读的是同一个 ``count_key``）。
    """
    demand = Demand(names=("slash",))

    assert supply_satisfies(demand, Supply(product="slash", suit="spade", rank=7))
    assert supply_satisfies(demand, Supply(product="fire_slash", kind="transformed"))
    assert supply_satisfies(demand, Supply(product="thunder_slash", suit="club", rank=9))
    assert not supply_satisfies(demand, Supply(product="dodge", suit="spade", rank=7))
    assert not supply_satisfies(demand, Supply(product="dodge", kind="virtual"))


def test_the_four_dimensions_without_a_producer_today_are_written_the_same_shape():
    """颜色 / 点数 / 虚拟·转化 / 转化源张数：同形上线、暂时无生产者——匹配器现在就写全。"""
    assert not supply_satisfies(
        Demand(colors=("red",)), Supply(product="dodge", suit="spade", color="black")
    )
    assert supply_satisfies(
        Demand(colors=("red",)), Supply(product="dodge", suit="heart", color="red")
    )
    assert not supply_satisfies(Demand(ranks=(7,)), Supply(product="dodge", rank=8))
    assert supply_satisfies(Demand(ranks=(7, 8)), Supply(product="dodge", rank=8))
    assert not supply_satisfies(Demand(kinds=("transformed",)), Supply(product="slash"))
    assert supply_satisfies(
        Demand(kinds=("transformed",)), Supply(product="slash", kind="transformed")
    )
    assert not supply_satisfies(
        Demand(source_counts=(2,)), Supply(product="slash", kind="transformed", source_count=1)
    )
    assert supply_satisfies(
        Demand(source_counts=(2,)), Supply(product="slash", kind="transformed", source_count=2)
    )


# ---------------------------------------------------------------------------
# 2. 需求生产者：窗口的 required + response_suits 打包成 Demand
# ---------------------------------------------------------------------------


def test_the_dodge_window_demands_the_dodge_card_and_the_recorded_suits():
    """【闪】窗口 = 牌名【闪】+ 单骑第三段收窄的花色白名单（票面 §1 的两个生产者）。"""
    demand = demand_for(
        QueryKind.DODGE, DodgeContext(response_suits=("diamond", "club"))
    )

    assert demand == Demand(names=("dodge",), suits=("diamond", "club"))


def test_the_respond_window_demands_the_declared_card_name():
    """打出 / 使用响应窗口要的牌名 = 窗口自己的 ``required``（南蛮 / 决斗 = 【杀】族）。"""
    demand = demand_for(
        QueryKind.RESPOND,
        RespondContext(cause=WindowCause.DUEL, required="slash", mode=RespondMode.PLAY),
    )

    assert demand == Demand(names=("slash",))


def test_the_peach_window_demands_a_peach_and_a_wine_only_for_the_dying_seat():
    """求桃窗口：【桃】；**濒死者自己**被问时【酒】同档（与真牌 / 提供者两条既有路径同读）。

    这是本票的一处**刻意的读法**：窗口自己的登记行已写明「（濒死自救时含【酒】）」，
    真牌路径由 ``TargetRule.near_death_self_only``、提供者路径由
    ``_GUOHUO_SELF_RESCUE_NAMES`` 各表达一次；需求若只说【桃】，自救的那张【酒】
    就会被展示层错误地置灰。
    """
    assert demand_for(QueryKind.PEACH, PeachContext(), dying=1, asker=1) == Demand(
        names=("peach", "wine")
    )
    assert demand_for(QueryKind.PEACH, PeachContext(), dying=1, asker=0) == Demand(
        names=("peach",)
    )


def test_a_window_that_is_not_a_need_card_window_has_no_demand():
    """四扇需要牌窗口之外（含改判）⇒ 没有需求（投影的两个键因此是 ``None``）。

    判据复用 ``actions._window_floor`` 那一族（票面 §2），不在这里另判一遍窗口。
    """
    assert demand_for(QueryKind.PLAY_PHASE) is None
    assert demand_for(QueryKind.DISCARD) is None
    assert demand_for(QueryKind.SELECT_OPTION) is None
    assert demand_for(
        QueryKind.RESPOND, RespondContext(cause=WindowCause.REJUDGE, mode=RespondMode.REJUDGE)
    ) is None


def test_an_empty_recorded_suit_list_stays_an_empty_whitelist():
    """``response_suits=()``（记录为空）原样进需求 ⇒ 什么也满足不了（三态不塌成 ``None``）。"""
    demand = demand_for(QueryKind.DODGE, DodgeContext(response_suits=()))

    assert demand is not None
    assert demand.suits == ()
    assert not supply_satisfies(demand, Supply(product="dodge", suit="diamond", rank=8))


# ---------------------------------------------------------------------------
# 3. 按档供给：从全档候选反推（票面 §4-1 / §4-2）
# ---------------------------------------------------------------------------

_DODGES = cards_of_type(PACK, "dodge")
_SLASHES = cards_of_type(PACK, "slash")
_PEACHES = cards_of_type(PACK, "peach")
_NULLIFIES = cards_of_type(PACK, "nullify")


def _window_setup(key: str):
    """8 扇登记窗口各一份**最小局面 + 候选载荷**（测试样例，不是第二份窗口清单）。

    ``key`` 取自 :data:`sgs.skills.CARD_REQUEST_WINDOWS`（票面 §0.1-1：``providers=True``
    的全部 8 行），下面这张表只回答"这扇窗口在测试里怎么摆一张能响应的牌"。
    """
    if key == "dodge":
        return (
            _state_with((_DODGES[0],)),
            QueryKind.DODGE,
            DodgeContext(),
            {},
        )
    if key == "arrow_barrage":
        return (
            _state_with((_DODGES[0],)),
            QueryKind.RESPOND,
            RespondContext(
                cause=WindowCause.ARROW_BARRAGE,
                required="dodge",
                mode=RespondMode.PLAY,
            ),
            {},
        )
    if key == "peach":
        return (
            _state_with((_PEACHES[0],)),
            QueryKind.PEACH,
            PeachContext(),
            {"dying": 0, "asker": 0},
        )
    if key in {"barbarian_invasion", "duel"}:
        cause = (
            WindowCause.BARBARIAN_INVASION if key == "barbarian_invasion" else WindowCause.DUEL
        )
        return (
            _state_with((_SLASHES[0],)),
            QueryKind.RESPOND,
            RespondContext(cause=cause, required="slash", mode=RespondMode.PLAY),
            {},
        )
    if key in {"borrowed_sword", "luanwu"}:
        cause = WindowCause.BORROWED_SWORD if key == "borrowed_sword" else WindowCause.LUANWU
        return (
            _state_with((_SLASHES[0],)),
            QueryKind.RESPOND,
            RespondContext(
                cause=cause,
                required="slash",
                mode=RespondMode.USE,
                use_targets=(1,),
            ),
            {},
        )
    if key == "nullify":
        return (
            _state_with((_NULLIFIES[0],)),
            QueryKind.RESPOND,
            RespondContext(
                cause=WindowCause.NULLIFY,
                required="nullify",
                mode=RespondMode.USE,
            ),
            {},
        )
    raise AssertionError(f"登记表里没有测试样例的窗口：{key}")


def _registered_need_card_keys() -> tuple[str, ...]:
    """``providers=True`` 的登记行（票面 §0.1-1 的 8 行；判据来自登记表本身）。"""
    from sgs.skills.catalog import CARD_REQUEST_WINDOWS

    return tuple(row.key for row in CARD_REQUEST_WINDOWS if row.providers)


def test_the_registry_has_the_eight_provider_windows_the_ticket_names():
    """§0.1-1：``providers=True`` 恰有 8 行（票面点名的 dodge / … / nullify 全在里面）。"""
    keys = _registered_need_card_keys()

    assert len(keys) == 8, keys
    assert {
        "dodge",
        "arrow_barrage",
        "peach",
        "barbarian_invasion",
        "duel",
        "borrowed_sword",
        "luanwu",
        "nullify",
    } == set(keys)


@pytest.mark.parametrize("key", _registered_need_card_keys())
def test_every_provider_window_yields_a_demand_a_kept_candidate_satisfies(key):
    """§4-1：**每个**需要牌窗口都产出一句需求，且面上的候选至少有一条满足它（逐行一条）。

    这一条是"8 扇窗口都接上了需求生产者、且闸门没有把这一问的唯一候选误删"的端到端钉子：
    面上的供给都从**真实候选**反推（``supplies_of_action``），不是另摆一份样例。
    """
    state, kind, payload, extra = _window_setup(key)
    dying = extra.get("dying")

    demand = demand_for(kind, payload, **extra)
    face = legal_actions_for(
        state, 0, kind, answers_seq=0, dying=dying, payload=payload
    )
    supplies = _supplies(face, state=state, kind=kind, payload=payload, dying=dying)

    assert demand is not None, f"{key} 没有产出一句需求"
    assert supplies, f"{key} 的面上没有任何供给"
    assert any(supply_satisfies(demand, supply) for supply in supplies), (
        f"{key} 的面上没有一条供给满足自己的需求"
    )


def _supplies(actions, *, state, kind, payload=None, dying=None, seat: int = 0) -> list[Supply]:
    """一组候选动作的**全部**供给（闸门读的同一个展开点 ``supplies_of_action``）。"""
    return [
        supply
        for action in actions
        for supply in (
            supplies_of_action(
                action,
                state=state,
                seat=seat,
                kind=kind,
                payload=payload,
                dying=dying,
            )
            or ()
        )
    ]


def test_a_hand_card_supply_carries_the_card_face_facts():
    """§4-2（真牌）：``kind="real"`` + 花色 / 点数 / 颜色——全部读候选的 ``CardRef``，
    供给层不另算一遍牌面。"""
    card_id = _DODGES[0]
    card = PACK.deck.cards[card_id]
    state = _state_with((card_id,))
    payload = DodgeContext()

    supplies = _supplies(
        legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=0, payload=payload),
        state=state,
        kind=QueryKind.DODGE,
        payload=payload,
    )

    assert supplies == [
        Supply(
            product="dodge",
            suit=card.suit,
            rank=card.rank,
            color=color_of_suit(card.suit),
            kind="real",
            source_count=1,
        )
    ]


def test_a_single_source_conversion_inherits_the_product_face_and_counts_one_source():
    """§4-2（单源转化）：【龙胆】的【杀】当【闪】⇒ ``kind="transformed"``、继承产物
    花色 / 点数、``source_count=1``（``CardRef`` 的既有事实，不重算）。"""
    red_slash = next(
        cid for cid in _SLASHES if color_of_suit(PACK.deck.cards[cid].suit) == "red"
    )
    card = PACK.deck.cards[red_slash]
    state = _state_with((red_slash,), hero="zhaoyun")
    payload = DodgeContext()

    supplies = _supplies(
        legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=0, payload=payload),
        state=state,
        kind=QueryKind.DODGE,
        payload=payload,
    )
    transformed = [supply for supply in supplies if supply.kind == "transformed"]

    assert transformed == [
        Supply(
            product="dodge",
            suit=card.suit,
            rank=card.rank,
            color=color_of_suit(card.suit),
            kind="transformed",
            source_count=1,
        )
    ]


def test_a_multi_source_conversion_has_no_face_and_counts_its_sources():
    """§4-2（多源转化）：【丈八蛇矛】两张手牌当【杀】⇒ 花色 / 点数恒 ``None``、
    ``source_count >= 2``（spec §3 第 3 条：这类供给确定地不满足花色 / 点数限制）。"""
    serpent = cards_of_type(PACK, "serpent_spear")[0]
    first, second = _PEACHES[:2]
    state = _state_with((first, second), equip=(serpent,))
    payload = RespondContext(
        cause=WindowCause.DUEL, required="slash", mode=RespondMode.PLAY
    )

    supplies = _supplies(
        legal_actions_for(state, 0, QueryKind.RESPOND, answers_seq=0, payload=payload),
        state=state,
        kind=QueryKind.RESPOND,
        payload=payload,
    )
    multi = [
        supply
        for supply in supplies
        if supply.kind == "transformed" and supply.source_count >= 2
    ]

    assert multi, f"没有多源转化供给：{supplies}"
    assert multi[0].product == "slash"
    assert multi[0].suit is None and multi[0].rank is None, (
        "多源转化的产物**花色 / 点数恒无**（票面 §4-2）；颜色按多源同色规则另行派生，"
        "不在本票射程"
    )


def test_the_five_provider_declarations_land_on_the_skill_definitions():
    """§1：今天落四条（八个技能定义）——【八卦阵】/【八阵】、 【蛊惑】、【护驾】/【激将】。

    ``Supply`` 是**定义**的属性（与 ``need_card_tier`` 同一姿态），不是局面属性；闸门读
    的就是这一份声明。票 03 让 relay 一族**可判**（``unknown=False`` 的**需求传递**，见
    :func:`~sgs.effects.supply._mismatch` 的 relay 分支）：四条声明是 relay。票 04 再把最后
    一条——【蛊惑】的**预转化**——改成可判：声明只说"我是预转化"（``product=None``：能声明
    什么由牌堆 + 窗口要的牌名现算），逐张来源牌的供给由
    :func:`~sgs.effects.supply.supplies_of_action` 展开。于是**今天没有真实声明落在
    ``unknown`` 上**。
    """
    from sgs.skills import CATALOG

    virtual_dodge = Supply(product="dodge", kind="virtual")
    for skill_id in ("equip.eight_diagrams", "hero.wolong_zhugeliang.bazhen"):
        assert CATALOG[skill_id].supply == virtual_dodge, skill_id

    guohuo = CATALOG["hero.yuji_mobile.guohuo"].supply
    assert guohuo is not None
    assert (guohuo.kind, guohuo.unknown, guohuo.product) == ("preconversion", False, None), (
        "预转化是**可判**的供给；产物牌名不写进声明（展开时按牌堆 + 窗口现算）"
    )

    for skill_id in (
        "hero.caocao.hujia",
        "hero.jie_caocao.hujia",
        "hero.liubei.jijiang",
        "hero.jie_liubei.jijiang",
    ):
        relay = CATALOG[skill_id].supply
        assert relay is not None, skill_id
        assert (relay.kind, relay.unknown) == ("relay", False), skill_id


def test_an_unknown_provider_supply_is_reported_but_never_greyed():
    """候选期不可知的供给照报（``unknown=True``），匹配器**不判死**。

    票 04 之后**没有真实声明**走这一档（票 03 把 relay 变成可判、票 04 把【蛊惑】的预转化
    变成逐张可判）⇒ 本条拿**构造**的 ``Supply(unknown=True)`` 钉住匹配器对它的口径
    （票 01 冻结的行为：匹配器不判死，合法性由闸门另行回答——见
    ``tests/test_need_card_relay.py::test_the_relay_branch_is_scoped_to_relay_and_leaves_the_other_kinds_alone``）。
    声明清空**不改**这条口径：它是"以后再有候选期不可知的供给"的兜底。
    """
    demand = Demand(names=("dodge",), suits=("diamond",))

    guohuo = Supply(product="dodge", kind="preconversion", unknown=True)

    assert supply_satisfies(demand, guohuo) is True


def test_the_self_rescue_wine_is_a_legal_supply_only_for_the_dying_seat():
    """求桃窗口的两份需求：【桃】与（濒死者自己）【桃】/【酒】——自救的那张【酒】不能判死。

    供给从**真实候选**反推（``supplies_of_action``）：【酒】确实是这一问面上的候选，而它只
    在"自己濒死"那份需求下满足——故它不会被闸门误删。
    """
    wine = cards_of_type(PACK_160, "wine")[0]
    peach = cards_of_type(PACK_160, "peach")[0]
    state = _state_with((peach, wine), pack=PACK_160)
    payload = PeachContext()

    own = demand_for(QueryKind.PEACH, payload, dying=0, asker=0)
    other = demand_for(QueryKind.PEACH, payload, dying=1, asker=0)
    supplies = _supplies(
        legal_actions_for(
            state, 0, QueryKind.PEACH, answers_seq=0, dying=0, payload=payload
        ),
        state=state,
        kind=QueryKind.PEACH,
        payload=payload,
        dying=0,
    )
    wine_supply = [supply for supply in supplies if supply.product == "wine"]
    assert wine_supply, f"求桃候选里没有【酒】：{supplies}"
    assert supply_satisfies(own, wine_supply[0]) is True
    assert supply_satisfies(other, wine_supply[0]) is False, "别人濒死时【酒】不能自救"


# ---------------------------------------------------------------------------
# 4. 合法性层（need-card-supply 票 02）：供需闸门只删交给玩家的面
# ---------------------------------------------------------------------------


def _providers(actions) -> list[str]:
    """一组候选里的提供者 id（``ActivateSkill`` 的 ``skill_id``，保序）。"""
    return [
        action.skill_id for action in actions if isinstance(action, ActivateSkill)
    ]


def _dodge_face(state, *, response_suits):
    """座位 0 在【闪】窗口里**交给玩家的那一面**（分面 + 供需闸门之后）。"""
    return legal_actions_for(
        state,
        0,
        QueryKind.DODGE,
        answers_seq=0,
        payload=DodgeContext(response_suits=response_suits),
    )


def _redeclare_supply(monkeypatch, skill_id: str, supply: Supply) -> None:
    """把 ``skill_id`` 的**声明供给**临时换一条（测试内的最小声明，不新造技能）。

    ``CATALOG`` 是只读映射（``MappingProxyType``）⇒ 整体换掉模块上那一份。两个读取口
    （``effects.supply._declared_supply`` 的 ``from sgs.skills import CATALOG`` 与
    ``effects.actions.need_card_tier`` 的 ``catalog_module.CATALOG``）读的都是**模块属性**，
    故换一份即同时生效；``resolve_instances`` 的缓存以 ``CATALOG`` **身份**为键，整体替换
    时自动作废（该函数 docstring 的既有保证），不会吃到旧声明。monkeypatch 负责还原。
    """
    from dataclasses import replace

    from sgs.skills import catalog as catalog_module

    declared = {
        **catalog_module.CATALOG,
        skill_id: replace(catalog_module.CATALOG[skill_id], supply=supply),
    }
    monkeypatch.setattr(catalog_module, "CATALOG", declared)
    monkeypatch.setattr("sgs.skills.CATALOG", declared)


def test_a_known_supply_that_satisfies_the_demand_is_what_lets_the_provider_into_the_face(
    monkeypatch,
):
    """§3-2 后半：**已知且满足**的供给要能进面——把【八卦阵】的声明临时改成 ♥ 虚拟【闪】。

    这条钉的是"**算**出来的"：同一个局面、同一个提供者，只改声明里的花色，进不进面就跟着
    翻面。若候选仍按"限制生效 ⇒ 提供者一律禁"处理，两次都是空——那不是匹配出来的。
    """
    bagua = cards_of_type(PACK, "eight_diagrams")[0]
    state = _state_with((_DODGES[0],), equip=(bagua,))

    # 原声明（无色）⇒ 花色需求下确定地不满足（spec §3 第 3 条）。
    assert _providers(_dodge_face(state, response_suits=("heart",))) == []

    _redeclare_supply(
        monkeypatch,
        "equip.eight_diagrams",
        Supply(product="dodge", suit="heart", kind="virtual"),
    )

    assert _providers(_dodge_face(state, response_suits=("heart",))) == [
        "equip.eight_diagrams"
    ], "供给花色落在白名单里 ⇒ 这一档进面"
    assert _providers(_dodge_face(state, response_suits=("diamond",))) == [], (
        "同一份局面换白名单就不进面 ⇒ 判据来自供给 × 需求的匹配"
    )


def test_the_eight_diagrams_provider_is_rejected_by_the_matcher_and_keeps_its_supply_row():
    """§3-3：八卦阵在花色限制下不进面，理由是**匹配器**。

    断言的是**关系**（``supply_satisfies(需求, 八卦阵的声明) is False``）而不是"它被禁了"：
    放开 `unknown` 的票 03 / 04 不会让这条关系变假（无花色**确定地**不满足花色需求）。

    名字里的 "keeps its supply row" 是票 06 之前的历史面：供给行随展示层一起删除
    （spec §0.1b），名字作为既有落点（票 02 / `开发文档/06` 账本）的锚保留。
    """
    bagua = cards_of_type(PACK, "eight_diagrams")[0]
    state = _state_with((_DODGES[0],), equip=(bagua,))
    payload = DodgeContext(response_suits=("heart",))
    demand = demand_for(QueryKind.DODGE, payload)
    declared = CATALOG["equip.eight_diagrams"].supply

    assert demand is not None and declared is not None
    assert supply_satisfies(demand, declared) is False, (
        "八卦阵的供给 = 无色虚拟【闪】⇒ 花色需求下不满足（关系层，不是禁令层）"
    )
    assert "equip.eight_diagrams" not in _providers(_dodge_face(state, response_suits=("heart",)))


def test_an_unknown_provider_stays_out_of_the_face_while_a_restriction_is_active():
    """§3-2 前半 / 票 04 §2b-3：限制生效时"算出来一张都不行" ⇒ **仍不进面**（失败关闭不变）。

    ``unknown`` 的失败关闭住在**合法性闸门**（``effects.supply.supply_is_legal``）里，不在
    匹配器里（匹配器对它"不判死"是票 01 的口径）——票 02 建立、票 03 / 04 都**没改**
    闸门一个字。

    **票 04 之后这一条的分母又变了一次**（票 03 把它缩到只剩【蛊惑】，票 04 把【蛊惑】的
    预转化也变成**逐张可判**了）⇒ 今天的真实声明里**一条 ``unknown`` 都不剩**，故本条拆成
    两层读：

    ①**闸门层**：``unknown`` 的失败关闭仍在（合成供给样本照旧关掉）——它不再替某条技能
      作证，但仍是"以后再有候选期不可知的供给"的兜底；
    ②**真窗口层**：真断言的落点从"unknown 一刀切"变成**逐张匹配的结论**——于吉手里没有一张
      能预转化出可响应【闪】的牌时【蛊惑】不进面（票面 §2："判据从'有需求就全禁'变成
      '算出来一张都不行'"），有一张匹配的牌时才进面。

    名字沿用票 02 的那一个（``unknown_provider_...``）：它仍是这条判据的历史入口；逐张的
    完整收口（三行置灰 / 上游禁令 / 信息卫生 / S16）在
    `tests/test_need_card_preconversion.py`。
    """
    guohuo = "hero.yuji_mobile.guohuo"
    declared = CATALOG[guohuo].supply
    assert declared is not None and declared.unknown is False, (
        f"{guohuo}：票 04 起预转化是**可判**的供给（不再是 unknown）"
    )
    # ① 闸门层：unknown 的失败关闭仍在（票 02 的判据一字未改；今天已无真实载体）。
    assert (
        supply_is_legal(
            Demand(names=("dodge",), suits=("heart",)),
            Supply(product="dodge", kind="preconversion", unknown=True),
        )
        is False
    ), "unknown + 收窄需求 ⇒ 闸门仍失败关闭"

    # ② 真窗口层：♣ 的【杀】预转化不出 ♥ / ♦ 的【闪】⇒ 限制生效时不进面。
    club_slash = _card_of_suit("slash", "club")
    diamond_slash = _card_of_suit("slash", "diamond")
    no_match = _state_with((club_slash,), hero="yuji_mobile")
    assert guohuo not in _providers(_dodge_face(no_match, response_suits=("heart",))), (
        f"{guohuo}：手里没有一张 ♥ 的牌 ⇒ 算出来一张也不行 ⇒ 不进面"
    )
    # 有一张 ♦ 的牌时，同一个限制照样可以"算出来"⇒ 进面（票 02 之前它被一刀切禁掉）。
    matches = _state_with((diamond_slash,), hero="yuji_mobile")
    assert guohuo in _providers(_dodge_face(matches, response_suits=("diamond",))), (
        f"{guohuo}：有一张 ♦ 的牌 ⇒ 预转化供给满足需求 ⇒ 进面"
    )
    # 不限花色的窗口里它照常进面（票 01 之前的行为逐条不变）。
    assert guohuo in _providers(_dodge_face(no_match, response_suits=None))


def test_a_rejected_real_card_and_conversion_are_both_kept_off_the_face():
    """§1c-7：真牌 / 转化被需求拒掉时**都不进面**（同一份匹配判据）。

    【赵云】的【龙胆】把 ♦【杀】转成 ♦【闪】；花色需求是 ♥ ⇒ 真【闪】与转化【闪】都不进面。
    对照（同一局面、不限花色）两张候选都在面上——故这里删掉的是"被需求拒掉的"，不是
    "本来就没有的"。
    """
    diamond_dodge = _card_of_suit("dodge", "diamond")
    diamond_slash = _card_of_suit("slash", "diamond")
    state = _state_with((diamond_dodge, diamond_slash), hero="zhaoyun")
    payload = DodgeContext(response_suits=("heart",))
    demand = demand_for(QueryKind.DODGE, payload)
    assert demand is not None

    unrestricted = legal_actions_for(
        state, 0, QueryKind.DODGE, answers_seq=0, payload=DodgeContext()
    )
    supplies = _supplies(
        unrestricted, state=state, kind=QueryKind.DODGE, payload=DodgeContext()
    )

    assert len([action for action in unrestricted if isinstance(action, UseCard)]) == 2, (
        "对照：不限花色时真【闪】与【龙胆】转化【闪】都在面上"
    )
    assert {supply.kind for supply in supplies} == {"real", "transformed"}, (
        f"真牌与转化各有一条供给：{supplies}"
    )
    assert all(supply.suit == "diamond" for supply in supplies)
    assert all(supply_satisfies(demand, supply) is False for supply in supplies), (
        "♦ 的供给在 ♥ 白名单下都不满足"
    )
    face = _dodge_face(state, response_suits=("heart",))
    assert not any(isinstance(action, UseCard) for action in face), (
        "面里一张都不该有：被需求拒掉的真牌与转化都不进面"
    )


def _card_of_suit(type_id: str, suit: str) -> int:
    """标准 108 档里某牌名某花色的第一张 ``card_id``（构造被拒样本用）。"""
    return next(
        card_id
        for card_id in cards_of_type(PACK, type_id)
        if PACK.deck.cards[card_id].suit == suit
    )


def test_the_respond_demand_carries_the_same_suit_restriction_as_the_dodge_one():
    """票 02 的接线：``RESPOND`` 与 ``DODGE`` 同读承载帧的 ``response_suits``（三态不塌）。

    票 01 只把这一维接进了 ``DODGE``；合法性闸门在 ``RESPOND`` 窗口上必须与今天
    ``_response_suit_allowed`` 的结论逐条相同，故它得先看得见这份事实。
    """
    restricted = RespondContext(
        cause=WindowCause.DUEL,
        required="slash",
        mode=RespondMode.PLAY,
        response_suits=("heart",),
    )
    plain = RespondContext(
        cause=WindowCause.DUEL, required="slash", mode=RespondMode.PLAY
    )
    empty = RespondContext(
        cause=WindowCause.DUEL,
        required="slash",
        mode=RespondMode.PLAY,
        response_suits=(),
    )

    assert demand_for(QueryKind.RESPOND, restricted) == Demand(
        names=("slash",), suits=("heart",)
    )
    assert demand_for(QueryKind.RESPOND, plain) == Demand(names=("slash",))
    assert demand_for(QueryKind.RESPOND, empty) == Demand(names=("slash",), suits=())
