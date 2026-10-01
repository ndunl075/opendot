"""Tier discovery and routing (ARCHITECTURE.md section 7). Pure code: no I/O, no model calls."""

from __future__ import annotations

import re
from dataclasses import dataclass

from opendot_core.providers.chatgpt_plan import parse_family
from opendot_core.providers.types import ModelInfo

from .errors import HandOff, NoModelForTier
from .tiers import (
    FAMILY_TIERS,
    JOB_TABLE,
    TIER_ORDER,
    Effort,
    Job,
    Tier,
    bump_effort,
    next_tier_up,
)


@dataclass(frozen=True)
class Route:
    model: str
    effort: Effort
    tier: Tier
    """The tier the model actually belongs to (after any move up for a missing tier)."""
    needs_approval: bool


def _version_key(model_id: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", model_id))


def _pick(candidates: list[str]) -> str:
    # Deterministic: highest version number in the id, then lexicographically greatest id.
    return max(candidates, key=lambda m: (_version_key(m), m))


def discover_tiers(catalog: list[ModelInfo]) -> dict[Tier, str]:
    """Map tiers to model ids by family word. If a family has several models, the one with
    the highest version number in its id wins, then the lexicographically greatest id.
    Astra (and any unknown family) is never mapped."""
    found: dict[Tier, list[str]] = {}
    for info in catalog:
        family = (info.family or parse_family(info.id) or "").lower()
        tier = FAMILY_TIERS.get(family)
        if tier is not None:
            found.setdefault(tier, []).append(info.id)
    return {tier: _pick(ids) for tier, ids in found.items()}


class Router:
    def __init__(
        self,
        catalog: list[ModelInfo],
        overrides: dict[Tier, str] | None = None,
        auto_top: bool = False,
    ) -> None:
        self.auto_top = auto_top
        self.overrides: dict[Tier, str] = {Tier(tier): model for tier, model in (overrides or {}).items()}
        self.refresh(catalog)

    def refresh(self, catalog: list[ModelInfo]) -> None:
        """Re-read the account's catalog (for example after the first sign-in). Overrides still win."""
        self.tiers = discover_tiers(catalog)
        # User overrides win per tier (this is the only way to reach Astra).
        self.tiers.update(self.overrides)

    def _resolve(self, tier: Tier) -> tuple[Tier, str]:
        """Model for a tier, using the next tier UP when it is missing."""
        t: Tier | None = tier
        while t is not None:
            if t in self.tiers:
                return t, self.tiers[t]
            t = next_tier_up(t)
        raise NoModelForTier(f"no model available for tier {tier.value} or any tier above it")

    def route(self, job: Job, *, failed_at: Tier | None = None, effort_bump: bool = False) -> Route:
        tier, effort = JOB_TABLE[Job(job)]
        if failed_at is not None:
            failed_at = Tier(failed_at)
            up = next_tier_up(failed_at)
            if up is None:
                raise HandOff("the top tier already failed; hand the task to the user")
            tier = up
            if effort_bump:
                effort = bump_effort(effort)
        effective, model = self._resolve(tier)
        if failed_at is not None and TIER_ORDER.index(effective) <= TIER_ORDER.index(failed_at):
            raise HandOff("no higher tier is available; hand the task to the user")
        return Route(
            model=model,
            effort=effort,
            tier=effective,
            needs_approval=effective is Tier.top and not self.auto_top,
        )


class EscalationTracker:
    """Remembers per-step escalation so a step escalates at most once."""

    def __init__(self) -> None:
        self._escalated: set[str] = set()

    def has_escalated(self, step_id: str) -> bool:
        return step_id in self._escalated

    def record(self, step_id: str) -> None:
        if step_id in self._escalated:
            raise RuntimeError(f"step {step_id!r} already escalated once")
        self._escalated.add(step_id)

    def escalate(
        self,
        router: Router,
        step_id: str,
        job: Job,
        failed_at: Tier,
        *,
        effort_bump: bool = False,
    ) -> Route:
        """Route the escalation for a step, raising if the step already escalated."""
        if step_id in self._escalated:
            raise RuntimeError(f"step {step_id!r} already escalated once")
        route = router.route(job, failed_at=failed_at, effort_bump=effort_bump)
        self._escalated.add(step_id)
        return route
