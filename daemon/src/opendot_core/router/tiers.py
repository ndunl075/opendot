"""Tiers, jobs and the section 7.1 routing table (pure data)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

Effort = Literal["low", "medium", "high"]
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high")
"""Runtime never uses xhigh or above, so the ladder stops at high."""


class Tier(StrEnum):
    cheap = "cheap"
    mid = "mid"
    top = "top"


TIER_ORDER: tuple[Tier, ...] = (Tier.cheap, Tier.mid, Tier.top)

FAMILY_TIERS: dict[str, Tier] = {"luna": Tier.cheap, "terra": Tier.mid, "sol": Tier.top}
"""Family words only; never a full model name. Astra is deliberately absent (section 7.2)."""


class Job(StrEnum):
    sort = "sort"
    summarize = "summarize"
    chat = "chat"
    plan = "plan"
    tool_step = "tool_step"
    review = "review"
    deep = "deep"


JOB_TABLE: dict[Job, tuple[Tier, Effort]] = {
    Job.sort: (Tier.cheap, "low"),
    Job.summarize: (Tier.cheap, "low"),
    Job.chat: (Tier.cheap, "low"),
    Job.plan: (Tier.cheap, "medium"),
    Job.tool_step: (Tier.cheap, "medium"),
    Job.review: (Tier.mid, "medium"),
    Job.deep: (Tier.top, "high"),
}


def bump_effort(effort: Effort) -> Effort:
    """One step higher, capped at high."""
    return EFFORTS[min(EFFORTS.index(effort) + 1, len(EFFORTS) - 1)]


def next_tier_up(tier: Tier) -> Tier | None:
    i = TIER_ORDER.index(tier)
    return TIER_ORDER[i + 1] if i + 1 < len(TIER_ORDER) else None
