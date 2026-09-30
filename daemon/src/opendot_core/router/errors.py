"""Router errors."""

from __future__ import annotations


class NoModelForTier(Exception):
    """The catalog has no usable model for the tier, nor for any tier above it."""


class HandOff(Exception):
    """The top tier already failed: hand the task to the user with what was tried."""
