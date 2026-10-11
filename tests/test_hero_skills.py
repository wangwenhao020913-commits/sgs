"""武将技能的**载体、身份门控与链序**（票 01）。

落点与票 17 §"测试缝"一致：只经 ``sgs.skills`` 的公开面（``resolve_instances`` /
``instance_key`` / ``prune_skill_state`` / ``lord_skill_allowed``）与引擎门面
（``Engine.from_state`` 的人为局面 + 脚本玩家）观测行为，不测内部字段。

本文件按 `spec.md` §Testing Decisions 的约定**新建**（不往装备技能的既有测试文件里再堆）；
票 01 只落「载体与身份门控」一节，票 03–11 的族级测试各占其后一节。

票 01 尚未登记任何武将技能（那是票 12），故这里的 ``hero.caocao.hujia`` 用**注入的假定义**
表达——试的是"载体 + 门控"这套机器，而不是曹操的技能行为；注入点在
``sgs.skills.catalog.CATALOG``（既有 ``test_skills.py`` 的先例）。
"""

from __future__ import annotations

from types import MappingProxyType

from sgs.data import Identity, load_content_pack
from sgs.effects import CandidateScope, CarrierKind, Modifier, ModifierPoint, legal_actions_for
from sgs.engine import Engine
from sgs.skills import (
    CATALOG,
    LORD_SKILL_IDS,
    Hook,
    SkillDef,
    SkillType,
    instance_key,
    lord_skill_allowed,
    lord_skill_ids,
    prune_skill_state,
    resolve_instances,
)
from sgs.skills import catalog as catalog_module
from sgs.state import ActivateSkill, Pass, QueryKind, UseCard
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")

HUJIA = "hero.caocao.hujia"
JIANXIONG = "hero.caocao.jianxiong"

CROSSBOW = cards_of_type(PACK, "crossbow")[0]
SLASH = cards_of_type(PACK, "slash")[0]


def _hero_def(
    skill_id: str = HUJIA,
    *,
    locked: bool = False,
    skill_type: SkillType = SkillType.LORD,
    hooks=(),
    modifiers=(),
) -> SkillDef:
    return SkillDef(
        skill_id=skill_id,
        locked=locked,
        carrier=CarrierKind.HERO,
        skill_type=skill_type,
        hooks=hooks,
        modifiers=modifiers,
    )


def _install(monkeypatch, *definitions: SkillDef) -> None:
    """把假武将定义并进 ``catalog.CATALOG``（模块级名字，故直接替换映射）。"""
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, **{d.skill_id: d for d in definitions}}),
    )


def _offer_activation(state, ctx, current):
    """``CANDIDATES(PLAY)`` 回调：把本技能作为裸发动候选追加进池（票 04 的形状）。"""
    del state
    if ctx.scope is not CandidateScope.PLAY or ctx.owner != ctx.subject:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


_OFFER = Modifier(ModifierPoint.CANDIDATES, _offer_activation)
"""把"裸发动"追加进出牌阶段候选的修饰器（候选集合断言用）。"""


def _noop_hook(host, frame):
    del host, frame
    return None


# ---------------------------------------------------------------------------
# 载体解析：武将牌是与装备区并列的载体
# ---------------------------------------------------------------------------


def test_a_lord_hero_skill_is_resolved_as_a_hero_card_instance(monkeypatch):
    _install(monkeypatch, _hero_def())
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.LORD), SeatSpec()],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert [instance.skill_id for instance in instances] == [HUJIA]
    assert instances[0].owner == 0
    assert instances[0].source.kind.value == "HERO_CARD"
    assert instances[0].definition.priority_class is not None
    assert instance_key(instances[0]) == f"0:{HUJIA}:HERO_CARD:{HUJIA}"


def test_a_hero_skill_takes_the_hero_priority_class_and_sorts_before_equipment(monkeypatch):
    _install(monkeypatch, _hero_def())
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.LORD, equip=(CROSSBOW,)), SeatSpec()],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert [instance.skill_id for instance in instances] == [HUJIA, "equip.crossbow"], (
        "武将档（HERO=0）先于装备档（EQUIPMENT=1），由链序直接表达，不靠特判"
    )


def test_resolving_without_heroes_does_not_consult_the_hero_source(monkeypatch):
    """无武将模式：即便技能表里有武将定义，``hero=None`` 也一个字都不产出。

    判据取 M2 的逐字节口径——两人各持一张诸葛连弩时，实例集合与实例键**恰等于**
    M2 的 ``test_skill_instances_are_keyed_by_owner_skill_and_source`` 那一份。
    """
    _install(monkeypatch, _hero_def())
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,)), SeatSpec(equip=(CROSSBOW,))],
        turn_seat=0,
    )

    instances = resolve_instances(state)

    assert [instance_key(instance) for instance in instances] == [
        f"0:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
        f"1:equip.crossbow:EQUIPMENT_CARD:{CROSSBOW}",
    ]


def test_prune_skill_state_keeps_state_of_a_live_hero_skill(monkeypatch):
    _install(monkeypatch, _hero_def())
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.LORD), SeatSpec()],
        turn_seat=0,
    )
    key = f"0:{HUJIA}:HERO_CARD:{HUJIA}"
    state.skill_state[key] = 1

    prune_skill_state(state)

    assert state.skill_state == {key: 1}, "来源（武将牌）仍在 ⇒ per-instance 状态不被清"


# ---------------------------------------------------------------------------
# 身份门控：非主公的武将牌上根本没有这个技能（三处零出现）
# ---------------------------------------------------------------------------


def test_a_non_lord_holding_a_lord_skill_produces_no_instance(monkeypatch):
    _install(monkeypatch, _hero_def())
    for identity in (Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE, None):
        state = build_state(
            pack=PACK,
            seats=[SeatSpec(hero="caocao", identity=identity), SeatSpec()],
            turn_seat=0,
        )

        assert HUJIA not in [instance.skill_id for instance in resolve_instances(state)], identity


def test_a_non_lord_lord_skill_is_absent_from_the_candidate_set(monkeypatch):
    _install(monkeypatch, _hero_def(modifiers=(_OFFER,)))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.REBEL), SeatSpec()],
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=0)

    assert not [a for a in actions if isinstance(a, ActivateSkill) and a.skill_id == HUJIA]


def test_a_lord_lord_skill_reaches_the_candidate_set(monkeypatch):
    _install(monkeypatch, _hero_def(modifiers=(_OFFER,)))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.LORD), SeatSpec()],
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=0)

    assert [a for a in actions if isinstance(a, ActivateSkill) and a.skill_id == HUJIA], (
        "主公的护驾在出牌阶段作为候选出现（本票只验门控，不验求助窗口——那是票 09）"
    )


def test_a_non_lord_lord_skill_never_reaches_the_journal(monkeypatch):
    skill = _hero_def(hooks=(Hook(Timing.DAMAGE_DEAL, _noop_hook),))

    engine = _run_slash_use(monkeypatch, identity=Identity.REBEL, skill=skill)

    assert not _has_activation_query(engine, HUJIA)


def test_a_lord_lord_skill_reaches_the_journal(monkeypatch):
    skill = _hero_def(hooks=(Hook(Timing.DAMAGE_DEAL, _noop_hook),))

    engine = _run_slash_use(monkeypatch, identity=Identity.LORD, skill=skill)

    assert _has_activation_query(engine, HUJIA), (
        "主公的护驾实例存在 ⇒ 伤害时点派发到它、发出 SKILL_ACTIVATION 询问（记录流可见）"
    )


# ---------------------------------------------------------------------------
# 门控谓词与派生集合（票 01 §载体接入 #8）
# ---------------------------------------------------------------------------


def test_lord_skill_allowed_reads_the_definition_field(monkeypatch):
    _install(monkeypatch, _hero_def())

    assert lord_skill_allowed(Identity.LORD, HUJIA) is True
    assert lord_skill_allowed(Identity.REBEL, HUJIA) is False
    assert lord_skill_allowed(Identity.LORD, "equip.crossbow") is False
    assert lord_skill_allowed(Identity.LORD, "no_such_skill") is False


def test_lord_skill_ids_are_derived_from_the_skill_definitions(monkeypatch):
    """注入一条主公技定义 ⇒ 派生集合**非空且含它**（手抄空集过不了这条）。

    票 09 起真实 ``CATALOG`` 已有三条主公技（护驾 / 激将 / 救援），故期望集合要含它们；
    注入点仍是同一条派生路径（``SkillDef.skill_type``）。
    """
    _install(monkeypatch, _hero_def())

    assert lord_skill_ids() == frozenset(
        {HUJIA, "hero.liubei.jijiang", "hero.sunquan.jiuyuan"}
    )


def test_the_lord_skill_ids_snapshot_matches_the_shipped_catalog():
    """``LORD_SKILL_IDS`` 是同一派生对真实 ``CATALOG`` 的快照，不是第三份名单。"""
    assert LORD_SKILL_IDS == lord_skill_ids(CATALOG)


def test_a_non_lord_skill_is_not_gated_by_identity(monkeypatch):
    """门控只针对主公技：普通武将技对任何身份都产出（``skill_type`` 的用途是分类，不是门）。"""
    _install(monkeypatch, _hero_def(JIANXIONG, skill_type=SkillType.NORMAL))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="caocao", identity=Identity.REBEL), SeatSpec()],
        turn_seat=0,
    )

    assert [instance.skill_id for instance in resolve_instances(state)] == [JIANXIONG]


# ---------------------------------------------------------------------------
# 记录流用的最小驱动（与 test_skills.py 同形，刻意不共用那个文件）
# ---------------------------------------------------------------------------


class _Scripted:
    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _use_frame_popped(engine) -> bool:
    from sgs.state import FrameKind

    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )


def _has_activation_query(engine, skill_id: str) -> bool:
    return any(
        record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and getattr(record.query, "skill_id", None) == skill_id
        for record in engine.records
    )


def _run_slash_use(monkeypatch, *, identity, skill):
    """座位 0 的曹操对座位 1 用一张【杀】；伤害时点会派发 ``skill`` 的钩子。"""
    _install(monkeypatch, skill)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(SLASH,), hero="caocao", identity=identity),
            SeatSpec(),
        ],
        deck=_deck_excluding(SLASH),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == SLASH
                and action.targets == (1,),
            )
        return None

    def dodge_passer(query):
        if query.kind != "DODGE":
            return None
        return _find(query, lambda action: isinstance(action, Pass))

    engine = Engine.from_state(state, seed=1)
    run_until(engine, [_Scripted(attacker), _Scripted(dodge_passer)], _use_frame_popped)
    return engine
