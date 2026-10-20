"""Context/slot planning rules for engines shared by several apps."""

from __future__ import annotations

from core.context_policy import (
    ACTION_REJECT,
    ACTION_RELOAD,
    ACTION_SERVE,
    REASON_CONTEXT,
    REASON_FITS,
    REASON_INSUFFICIENT,
    REASON_SHRINK,
    REASON_SLOTS,
    plan_context,
)


def test_32k_request_on_four_16k_slots_shrinks_to_one_slot():
    """The reported bug: 65K/4 slots could not serve a 32K request."""
    plan = plan_context(
        min_context=32768,
        clients=1,
        capability=131072,
        current_per_slot=16384,
        current_slots=4,
    )
    assert plan.action == ACTION_RELOAD
    assert plan.reason == REASON_CONTEXT
    # 32K on ONE slot, not 32K on four (= 128K of KV memory).
    assert plan.per_slot == 32768
    assert plan.slots == 1
    assert plan.total == 32768


def test_two_apps_with_the_same_need_get_two_slots():
    plan = plan_context(
        min_context=32768,
        clients=2,
        capability=131072,
        current_per_slot=32768,
        current_slots=1,
    )
    assert plan.action == ACTION_RELOAD
    assert plan.reason == REASON_SLOTS
    assert plan.per_slot == 32768
    assert plan.slots == 2


def test_apps_with_different_needs_share_the_largest_minimum():
    """A 32K app and an 8K app share one engine: per-slot is the 32K one."""
    plan = plan_context(
        min_context=32768,  # max(8K, 32K) as computed by the caller
        optimal_context=65536,
        clients=2,
        capability=131072,
        current_per_slot=0,
        current_slots=0,
    )
    assert plan.action == ACTION_RELOAD
    assert plan.per_slot >= 32768
    # The optimal upgrade must never cost a slot.
    assert plan.slots >= 1
    assert plan.total <= 131072


def test_optimal_never_costs_a_slot():
    without = plan_context(min_context=32768, clients=2, capability=131072)
    with_optimal = plan_context(
        min_context=32768,
        optimal_context=65536,
        clients=2,
        capability=131072,
    )
    assert with_optimal.slots == without.slots
    assert with_optimal.per_slot >= without.per_slot


def test_small_request_serves_the_loaded_engine_without_reloading():
    plan = plan_context(
        min_context=8192,
        clients=1,
        capability=131072,
        current_per_slot=32768,
        current_slots=1,
    )
    assert plan.action == ACTION_SERVE
    assert plan.per_slot == 32768
    assert plan.slots == 1


def test_adding_a_client_keeps_the_context_already_in_use():
    plan = plan_context(
        min_context=8192,
        clients=2,
        capability=131072,
        current_per_slot=32768,
        current_slots=1,
    )
    assert plan.action == ACTION_RELOAD
    # Never shrink the context under a running client just to add a slot.
    assert plan.per_slot >= 32768
    assert plan.slots == 2


def test_capability_limits_slots_instead_of_context():
    plan = plan_context(
        min_context=65536,
        clients=4,
        capability=131072,
    )
    assert plan.action == ACTION_RELOAD
    assert plan.per_slot == 65536
    assert plan.slots == 2  # 131072 // 65536
    assert plan.total <= 131072


def test_minimum_above_model_capability_is_rejected():
    plan = plan_context(min_context=200000, clients=1, capability=131072)
    assert plan.action == ACTION_REJECT
    assert plan.reason == REASON_INSUFFICIENT


def test_idle_shrink_returns_to_one_slot():
    plan = plan_context(
        clients=1,
        capability=131072,
        current_per_slot=32768,
        current_slots=4,
        idle_shrink=True,
    )
    assert plan.action == ACTION_RELOAD
    assert plan.reason == REASON_SHRINK
    assert plan.slots == 1
    assert plan.per_slot >= 32768  # never less than the running client uses


def test_idle_shrink_is_a_no_op_when_already_minimal():
    plan = plan_context(
        clients=1,
        capability=131072,
        current_per_slot=32768,
        current_slots=1,
        idle_shrink=True,
    )
    assert plan.action == ACTION_SERVE
    assert plan.reason == REASON_FITS


def test_no_loaded_engine_uses_the_minimum_context():
    plan = plan_context(min_context=16384, clients=1, capability=131072)
    assert plan.action == ACTION_RELOAD
    assert plan.per_slot == 16384
    assert plan.slots == 1
