"""Decide the (per-slot context, parallel slots) pair for a shared engine.

llama-server fixes both numbers when the process starts:

    per_slot = ctx_size / parallel_slots

so sharing one loaded model between several apps always means a compromise:

* every slot gets the SAME per-slot context — you cannot give one client 32K
  and another 8K inside one process;
* a bigger per-slot costs KV memory on EVERY slot;
* an extra slot costs one more per-slot worth of KV memory.

The policy below keeps that compromise as cheap as possible:

1. ``per_slot`` is the largest *minimum* context any client that shares the
   engine declared.  A client that declares no minimum never raises it.
2. ``slots`` is the number of clients that actually need to run at the same
   time, capped by what the model can hold (``capability // per_slot``).
3. ``optimal_context`` is a free upgrade only: it is applied when a reload is
   already necessary for another reason and buying it does not cost a slot.
4. A minimum above what the model can ever hold is rejected up front instead of
   being silently truncated into a context-length error later.

Everything here is pure arithmetic so the decisions can be unit tested and
replayed from logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_PER_SLOT = 2048
DEFAULT_CAPABILITY = 32768

ACTION_SERVE = 'serve'
ACTION_RELOAD = 'reload'
ACTION_REJECT = 'reject'

REASON_FITS = 'fits'
REASON_CONTEXT = 'context'
REASON_SLOTS = 'slots'
REASON_SHRINK = 'shrink'
REASON_INSUFFICIENT = 'context_below_minimum'


@dataclass(frozen=True)
class ContextPlan:
    """Result of planning one engine load."""

    action: str
    per_slot: int
    slots: int
    total: int
    reason: str

    @property
    def needs_reload(self) -> bool:
        return self.action == ACTION_RELOAD

    @property
    def rejected(self) -> bool:
        return self.action == ACTION_REJECT

    def as_dict(self) -> dict[str, Any]:
        return {
            'action': self.action,
            'per_slot': int(self.per_slot),
            'slots': int(self.slots),
            'total': int(self.total),
            'reason': self.reason,
        }


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def _capability_of(capability: int) -> int:
    try:
        value = int(capability or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = DEFAULT_CAPABILITY
    return max(MIN_PER_SLOT, value)


def max_slots_for(capability: int, per_slot: int, *, hard_max: int = 16) -> int:
    """How many slots of ``per_slot`` the model can hold at once."""
    cap = _capability_of(capability)
    per_slot = max(MIN_PER_SLOT, int(per_slot or MIN_PER_SLOT))
    return max(1, min(int(hard_max), max(1, cap // per_slot)))


def plan_context(
    *,
    min_context: int = 0,
    optimal_context: int = 0,
    clients: int = 1,
    max_slots: int = 16,
    capability: int = 0,
    current_per_slot: int = 0,
    current_slots: int = 0,
    idle_shrink: bool = False,
) -> ContextPlan:
    """Return what this engine should be loaded as.

    ``min_context`` is the hardest requirement of the clients sharing the
    engine — the reload is triggered by it.  ``optimal_context`` is only a
    preference.  ``current_*`` describe the live engine (0 when nothing is
    loaded).  ``idle_shrink`` asks for the smallest safe shape (used by the
    idle watchdog, which must never grow anything).
    """
    cap = _capability_of(capability)
    try:
        declared_min = int(min_context or 0)
    except (TypeError, ValueError):
        declared_min = 0
    try:
        optimal = int(optimal_context or 0)
    except (TypeError, ValueError):
        optimal = 0
    try:
        live_per_slot = int(current_per_slot or 0)
    except (TypeError, ValueError):
        live_per_slot = 0
    try:
        live_slots = max(0, int(current_slots or 0))
    except (TypeError, ValueError):
        live_slots = 0
    try:
        live_clients = max(1, int(clients or 1))
    except (TypeError, ValueError):
        live_clients = 1
    hard_max = max(1, min(16, int(max_slots or 16)))

    if declared_min > cap:
        # The model physically cannot hold this request.  Fail now, with the
        # numbers, instead of letting llama-server truncate it later.
        return ContextPlan(
            action=ACTION_REJECT,
            per_slot=cap,
            slots=1,
            total=cap,
            reason=REASON_INSUFFICIENT,
        )

    needed = _clamp(declared_min, MIN_PER_SLOT, cap) if declared_min else MIN_PER_SLOT
    desired_slots = max(1, min(hard_max, live_clients if live_clients > 1 else 1))

    def _slots_that_fit(per_slot: int) -> int:
        return max_slots_for(cap, per_slot, hard_max=hard_max)

    if idle_shrink:
        # Smallest safe shape: one slot, never below a live per-slot that a
        # client is already using.
        per_slot = max(needed, live_per_slot, MIN_PER_SLOT) if live_per_slot else needed
        per_slot = _clamp(per_slot, MIN_PER_SLOT, cap)
        if live_slots <= 1 and live_per_slot and live_per_slot <= per_slot:
            return ContextPlan(ACTION_SERVE, live_per_slot or per_slot, 1, (live_per_slot or per_slot), REASON_FITS)
        return ContextPlan(ACTION_RELOAD, per_slot, 1, per_slot, REASON_SHRINK)

    if live_per_slot and live_per_slot >= needed:
        # Enough context for everybody.  Only concurrency may be short.
        if live_slots >= desired_slots:
            return ContextPlan(
                ACTION_SERVE,
                live_per_slot,
                live_slots,
                live_per_slot * live_slots,
                REASON_FITS,
            )
        slots = min(desired_slots, _slots_that_fit(live_per_slot))
        if slots <= live_slots:
            # Capability stops us from adding slots: share what exists.
            return ContextPlan(
                ACTION_SERVE,
                live_per_slot,
                live_slots,
                live_per_slot * live_slots,
                REASON_FITS,
            )
        # Reload is already being paid for: spend the free room on `optimal`.
        per_slot = max(needed, live_per_slot)
        if optimal > per_slot:
            per_slot = min(optimal, cap // slots)
        per_slot = _clamp(per_slot, needed, cap)
        slots = max(1, min(slots, _slots_that_fit(per_slot)))
        return ContextPlan(
            ACTION_RELOAD,
            per_slot,
            slots,
            per_slot * slots,
            REASON_SLOTS,
        )

    # A client needs more context than the engine carries.  Fewer slots first:
    # 32K on one slot beats 32K on four (a quarter of the KV memory).
    per_slot = needed
    slots = max(1, min(desired_slots, _slots_that_fit(per_slot)))
    if live_per_slot and live_per_slot >= per_slot and live_slots >= slots:
        return ContextPlan(ACTION_SERVE, live_per_slot, live_slots, live_per_slot * live_slots, REASON_FITS)
    return ContextPlan(
        ACTION_RELOAD,
        per_slot,
        slots,
        per_slot * slots,
        REASON_CONTEXT,
    )
