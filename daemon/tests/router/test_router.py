from __future__ import annotations

import pytest

from opendot_core.providers.chatgpt_plan import parse_family
from opendot_core.providers.types import ModelInfo
from opendot_core.router import (
    EscalationTracker,
    HandOff,
    Job,
    NoModelForTier,
    Router,
    Tier,
)


def cat(*ids: str) -> list[ModelInfo]:
    return [ModelInfo(id=i, family=parse_family(i)) for i in ids]


REAL = cat("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol")


def test_discovery_real_catalog():
    r = Router(REAL)
    assert r.route(Job.chat).model == "gpt-5.6-luna"
    assert r.route(Job.review).model == "gpt-5.6-terra"
    assert r.route(Job.deep).model == "gpt-5.6-sol"


def test_family_fallback_when_field_missing():
    r = Router([ModelInfo(id=i) for i in ("x-luna", "x-terra", "x-sol")])
    assert r.route(Job.chat).model == "x-luna"


def test_duplicate_family_picks_highest_version():
    r = Router(cat("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-sol"))
    assert r.route(Job.deep).model == "gpt-6-sol"


def test_duplicate_same_version_lexicographic():
    r = Router(cat("gpt-5.6-luna", "b-5-sol", "a-5-sol"))
    assert r.route(Job.deep).model == "b-5-sol"


def test_astra_never_automatic():
    r = Router(cat("gpt-5.6-astra", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"))
    for job in Job:
        assert "astra" not in r.route(job).model
    with pytest.raises(NoModelForTier):
        Router(cat("gpt-5.6-astra")).route(Job.chat)


def test_astra_only_via_override():
    r = Router(REAL + cat("gpt-5.6-astra"), overrides={Tier.top: "gpt-5.6-astra"})
    assert r.route(Job.deep).model == "gpt-5.6-astra"


def test_missing_mid_uses_top():
    r = Router(cat("gpt-5.6-luna", "gpt-5.6-sol"))
    route = r.route(Job.review)
    assert (route.model, route.tier) == ("gpt-5.6-sol", Tier.top)
    assert route.needs_approval


def test_missing_cheap_uses_mid():
    r = Router(cat("gpt-5.6-terra", "gpt-5.6-sol"))
    route = r.route(Job.chat)
    assert (route.model, route.tier, route.effort) == ("gpt-5.6-terra", Tier.mid, "low")
    assert not route.needs_approval
    assert r.route(Job.chat, failed_at=route.tier).model == "gpt-5.6-sol"


def test_missing_top_and_above_raises():
    r = Router(cat("gpt-5.6-luna"))
    assert r.route(Job.chat).model == "gpt-5.6-luna"
    with pytest.raises(NoModelForTier):
        r.route(Job.deep)
    with pytest.raises(NoModelForTier):
        Router([]).route(Job.chat)


def test_overrides_win_per_tier():
    r = Router(REAL, overrides={Tier.cheap: "custom-cheap"})
    assert r.route(Job.chat).model == "custom-cheap"
    assert r.route(Job.review).model == "gpt-5.6-terra"


def test_override_fills_missing_tier():
    r = Router(cat("gpt-5.6-sol"), overrides={Tier.cheap: "mine"})
    assert r.route(Job.sort).model == "mine"


TABLE = [
    (Job.sort, Tier.cheap, "low"),
    (Job.summarize, Tier.cheap, "low"),
    (Job.chat, Tier.cheap, "low"),
    (Job.plan, Tier.cheap, "medium"),
    (Job.tool_step, Tier.cheap, "medium"),
    (Job.review, Tier.mid, "medium"),
    (Job.deep, Tier.top, "high"),
]


@pytest.mark.parametrize(("job", "tier", "effort"), TABLE)
def test_table_rows(job, tier, effort):
    route = Router(REAL).route(job)
    assert (route.tier, route.effort) == (tier, effort)


def test_table_covers_all_jobs():
    assert {j for j, _, _ in TABLE} == set(Job)


def test_escalation_one_step_no_skip():
    r = Router(REAL)
    a = r.route(Job.chat, failed_at=Tier.cheap)
    assert (a.tier, a.model, a.effort) == (Tier.mid, "gpt-5.6-terra", "low")
    b = r.route(Job.chat, failed_at=Tier.mid)
    assert (b.tier, b.model) == (Tier.top, "gpt-5.6-sol")
    c = r.route(Job.tool_step, failed_at=Tier.cheap)
    assert c.tier is Tier.mid and c.effort == "medium"


def test_escalation_effort_bump():
    r = Router(REAL)
    assert r.route(Job.chat, failed_at=Tier.cheap, effort_bump=True).effort == "medium"
    assert r.route(Job.plan, failed_at=Tier.cheap, effort_bump=True).effort == "high"
    assert r.route(Job.plan, failed_at=Tier.cheap).effort == "medium"
    assert r.route(Job.deep, failed_at=Tier.mid, effort_bump=True).effort == "high"


def test_top_approval_gating():
    assert Router(REAL).route(Job.deep).needs_approval
    assert Router(REAL).route(Job.chat, failed_at=Tier.mid).needs_approval
    assert not Router(REAL, auto_top=True).route(Job.deep).needs_approval
    assert not Router(REAL).route(Job.chat).needs_approval
    assert not Router(REAL).route(Job.chat, failed_at=Tier.cheap).needs_approval


def test_handoff_when_top_failed():
    r = Router(REAL)
    for job in Job:
        with pytest.raises(HandOff):
            r.route(job, failed_at=Tier.top)


def test_effort_never_exceeds_high():
    r = Router(REAL)
    for job in Job:
        for failed in (None, Tier.cheap, Tier.mid):
            for bump in (False, True):
                effort = r.route(job, failed_at=failed, effort_bump=bump).effort
                assert effort in ("low", "medium", "high")


def test_escalation_tracker():
    r = Router(REAL)
    t = EscalationTracker()
    assert not t.has_escalated("s1")
    route = t.escalate(r, "s1", Job.chat, Tier.cheap)
    assert route.tier is Tier.mid and t.has_escalated("s1")
    with pytest.raises(RuntimeError):
        t.escalate(r, "s1", Job.chat, Tier.mid)
    assert t.escalate(r, "s2", Job.chat, Tier.cheap).tier is Tier.mid
    with pytest.raises(RuntimeError):
        t.record("s2")


def test_tracker_failed_route_not_recorded():
    t = EscalationTracker()
    with pytest.raises(HandOff):
        t.escalate(Router(REAL), "s", Job.chat, Tier.top)
    assert not t.has_escalated("s")


def test_determinism():
    ids = ["gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"]
    outs = {
        tuple((x.model, x.tier) for x in (Router(cat(*p)).route(j) for j in Job)) for p in (ids, ids[::-1], sorted(ids))
    }
    assert len(outs) == 1
