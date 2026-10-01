"""Per-provider monthly spend caps that fail closed (ARCHITECTURE.md 6.2).

Each paid provider has its own cap, in USD per UTC calendar month. The default
cap is 0, so a paid provider is unusable until the user sets one. The cap and
the month's running total live in the existing ``sync_state`` table (connector
``provider_spend``), so no migration is needed:

* ``account = "<provider>:cap"``      -> ``cursor`` is the cap in USD
* ``account = "<provider>:YYYY-MM"``  -> ``cursor`` is the USD spent that month

``check`` runs BEFORE any network call and raises ``SpendCapReached`` when the
month's spend has met the cap or when the spend plus a conservative estimate of
the call's input would exceed it. The price table below is approximate; a model
missing from it is priced at ``UNKNOWN_RATE``, deliberately expensive, so an
unknown rate can only make the cap trip sooner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from ..db import Database
from .errors import SpendCapReached
from .types import Usage

CONNECTOR = "provider_spend"

#: Output tokens reserved against the cap before a call is sent (a bounded estimate).
OUTPUT_RESERVE_TOKENS = 512

#: USD per million tokens as (input, output). Longest model-id prefix wins.
#: Approximate list prices; treat results as estimates.
PRICE_TABLE: dict[str, dict[str, tuple[float, float]]] = {
    "openai_key": {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1": (2.00, 8.00),
    },
    "anthropic_key": {
        "claude-haiku": (1.00, 5.00),
        "claude-sonnet": (3.00, 15.00),
        "claude-opus": (15.00, 75.00),
    },
    "openrouter": {
        "openai/gpt-4o-mini": (0.15, 0.60),
        "openai/gpt-4o": (2.50, 10.00),
        "anthropic/claude-haiku": (1.00, 5.00),
        "anthropic/claude-sonnet": (3.00, 15.00),
        "anthropic/claude-opus": (15.00, 75.00),
    },
}

#: Applied when a model has no entry: intentionally pessimistic.
UNKNOWN_RATE: tuple[float, float] = (75.0, 150.0)


def rate_for(provider: str, model: str) -> tuple[tuple[float, float], bool]:
    """Return ``((input, output) USD per Mtok, rate_known)`` for a model."""
    table = PRICE_TABLE.get(provider, {})
    matches = [key for key in table if model.startswith(key)]
    if matches:
        return table[max(matches, key=len)], True
    return UNKNOWN_RATE, False


def estimate_cost_usd(provider: str, model: str, *, input_tokens: int, output_tokens: int) -> float:
    (rate_in, rate_out), _known = rate_for(provider, model)
    return (input_tokens * rate_in + output_tokens * rate_out) / 1_000_000


def estimate_tokens(text: str) -> int:
    """Crude over-estimate (one token per 3 characters) used for the pre-call check."""
    return len(text) // 3 + 1


class SpendTracker:
    def __init__(self, database: Database, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.database = database
        self._now = now
        self._migrated = False

    # -- cap -------------------------------------------------------------
    def get_cap(self, provider: str) -> float:
        value = self._read(f"{provider}:cap")
        return value if value is not None else 0.0

    def set_cap(self, provider: str, cap_usd: float) -> None:
        if cap_usd < 0:
            raise ValueError("spend cap must not be negative")
        self._write(f"{provider}:cap", float(cap_usd))

    # -- spend -----------------------------------------------------------
    def spent(self, provider: str) -> float:
        value = self._read(f"{provider}:{self._month()}")
        return value if value is not None else 0.0

    def check(
        self,
        provider: str,
        *,
        model: str = "",
        estimated_input_tokens: int = 0,
        reserved_output_tokens: int = OUTPUT_RESERVE_TOKENS,
    ) -> None:
        """Raise ``SpendCapReached`` unless there is room under the cap. Call before any request."""
        cap = self.get_cap(provider)
        spent = self.spent(provider)
        if cap <= 0:
            raise SpendCapReached(f"no monthly spend cap is set for {provider}")
        if spent >= cap:
            raise SpendCapReached(f"{provider} monthly cap ${cap:.2f} reached (${spent:.2f} spent)")
        # Reserve a bounded guess for the output too, so one call cannot
        # overshoot the cap by its whole answer.
        upcoming = estimate_cost_usd(
            provider, model, input_tokens=estimated_input_tokens, output_tokens=reserved_output_tokens
        )
        if spent + upcoming > cap:
            raise SpendCapReached(
                f"{provider}: this call could exceed the monthly cap ${cap:.2f} (${spent:.2f} already spent)"
            )

    def record(self, provider: str, model: str, usage: Usage | None = None, *, fallback_input_tokens: int = 0) -> float:
        """Add a finished (or sent-but-incomplete) call to the month total and return its cost."""
        if usage is not None and (usage.input_tokens or usage.output_tokens):
            input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
        else:
            input_tokens, output_tokens = fallback_input_tokens, 0
        cost = estimate_cost_usd(provider, model, input_tokens=input_tokens, output_tokens=output_tokens)
        self._add(f"{provider}:{self._month()}", cost)
        return cost

    def month(self) -> str:
        """The UTC calendar month ``spent`` is counted for, as ``YYYY-MM``."""
        return self._month()

    # -- storage ---------------------------------------------------------
    def _month(self) -> str:
        return self._now().strftime("%Y-%m")

    def _ensure(self) -> None:
        if not self._migrated:
            self.database.migrate()
            self._migrated = True

    def _read(self, account: str) -> float | None:
        self._ensure()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM sync_state WHERE connector = ? AND account = ?", (CONNECTOR, account)
            ).fetchone()
        return float(row["cursor"]) if row is not None and row["cursor"] is not None else None

    _UPSERT = """
        INSERT INTO sync_state (connector, account, cursor, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(connector, account) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at
    """

    def _write(self, account: str, value: float) -> None:
        self._ensure()
        with self.database.connect() as connection:
            connection.execute(self._UPSERT, (CONNECTOR, account, repr(value), self._now().isoformat()))

    def _add(self, account: str, amount: float) -> None:
        self._ensure()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT cursor FROM sync_state WHERE connector = ? AND account = ?", (CONNECTOR, account)
            ).fetchone()
            current = float(row["cursor"]) if row is not None and row["cursor"] is not None else 0.0
            connection.execute(self._UPSERT, (CONNECTOR, account, repr(current + amount), self._now().isoformat()))
