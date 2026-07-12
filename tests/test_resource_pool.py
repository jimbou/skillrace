from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from skillrace.resource_pool import (
    ResourceCancelled,
    ResourceOrderError,
    ResourcePool,
    ResourceTimeout,
)


def test_named_resource_limits_and_peak_accounting_are_never_exceeded():
    pool = ResourcePool(api=3, docker=2, agent=2)
    lock = threading.Lock()
    active = 0
    observed_peak = 0

    def work(_):
        nonlocal active, observed_peak
        with pool.agent_slot():
            with lock:
                active += 1
                observed_peak = max(observed_peak, active)
            time.sleep(0.01)
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(work, range(12)))

    assert observed_peak == 2
    assert pool.snapshot()["agent"] == {"capacity": 2, "active": 0, "peak": 2}


def test_multi_resource_acquisition_uses_one_global_order_without_deadlock():
    pool = ResourcePool(api=1, docker=1, agent=1)

    def first():
        with pool.slots("api", "docker"):
            time.sleep(0.01)
            return "first"

    def reverse_request():
        with pool.slots("docker", "api"):
            return "second"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(first), executor.submit(reverse_request)]
        assert [future.result(timeout=1) for future in futures] == ["first", "second"]


def test_cancellation_releases_resources_acquired_before_the_blocked_slot():
    pool = ResourcePool(api=1, docker=1, agent=1, poll_interval=0.005)
    cancelled = threading.Event()
    outcome = []

    def waiter():
        try:
            with pool.slots("api", "docker", cancel_event=cancelled):
                raise AssertionError("cancelled waiter entered critical section")
        except ResourceCancelled:
            outcome.append("cancelled")

    with pool.docker_slot():
        thread = threading.Thread(target=waiter)
        thread.start()
        deadline = time.monotonic() + 1
        while pool.snapshot()["api"]["active"] != 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert pool.snapshot()["api"]["active"] == 1
        cancelled.set()
        thread.join(timeout=1)
        assert not thread.is_alive()
        assert pool.snapshot()["api"]["active"] == 0

    assert outcome == ["cancelled"]
    assert all(item["active"] == 0 for item in pool.snapshot().values())


def test_timeout_and_invalid_limits_are_explicit():
    with pytest.raises(ValueError, match="positive"):
        ResourcePool(api=0, docker=1, agent=1)

    pool = ResourcePool(api=1, docker=1, agent=1, poll_interval=0.005)
    entered = threading.Event()
    release = threading.Event()

    def hold_api():
        with pool.api_slot():
            entered.set()
            release.wait(timeout=1)

    thread = threading.Thread(target=hold_api)
    thread.start()
    assert entered.wait(timeout=1)
    try:
        with pytest.raises(ResourceTimeout):
            with pool.api_slot(timeout=0.01):
                pass
    finally:
        release.set()
        thread.join(timeout=1)


def test_nested_reverse_order_is_rejected_before_it_can_deadlock():
    pool = ResourcePool(api=1, docker=1, agent=1)

    with pool.docker_slot():
        with pytest.raises(ResourceOrderError, match="order"):
            with pool.api_slot():
                pass


def test_nested_same_resource_reacquisition_is_rejected_immediately():
    pool = ResourcePool(api=1, docker=1, agent=1)

    with pool.agent_slot():
        with pytest.raises(ResourceOrderError, match="already held"):
            with pool.agent_slot(timeout=1):
                pass
