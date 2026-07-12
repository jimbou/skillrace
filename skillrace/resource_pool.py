"""Process-local resource limits shared by concurrent campaign workers."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterator


class ResourceCancelled(RuntimeError):
    """A caller cancelled while waiting for one or more resource slots."""


class ResourceTimeout(TimeoutError):
    """A resource set could not be acquired before its deadline."""


class ResourceOrderError(RuntimeError):
    """Nested acquisition would invert the pool's global lock order."""


class ResourcePool:
    """Bound API, Docker, and external-agent concurrency without lock inversion.

    Requests involving multiple resources are always acquired in the same global
    order, independent of the order supplied by a worker.  Any partially acquired
    set is released when cancellation, timeout, or an exception occurs.
    """

    _ORDER = ("api", "docker", "agent")

    def __init__(
        self,
        *,
        api: int,
        docker: int,
        agent: int,
        poll_interval: float = 0.05,
    ) -> None:
        limits = {"api": api, "docker": docker, "agent": agent}
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
               for value in limits.values()):
            raise ValueError("resource limits must be positive integers")
        if poll_interval <= 0:
            raise ValueError("resource poll interval must be positive")
        self._limits = limits
        self._semaphores = {
            name: threading.BoundedSemaphore(value) for name, value in limits.items()
        }
        self._poll_interval = float(poll_interval)
        self._stats_lock = threading.Lock()
        self._local = threading.local()
        self._active = {name: 0 for name in self._ORDER}
        self._peak = {name: 0 for name in self._ORDER}

    @property
    def limits(self) -> dict[str, int]:
        return dict(self._limits)

    @property
    def agent_capacity(self) -> int:
        return self._limits["agent"]

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._stats_lock:
            return {
                name: {
                    "capacity": self._limits[name],
                    "active": self._active[name],
                    "peak": self._peak[name],
                }
                for name in self._ORDER
            }

    def _mark_acquired(self, name: str) -> None:
        with self._stats_lock:
            self._active[name] += 1
            self._peak[name] = max(self._peak[name], self._active[name])

    def _mark_released(self, name: str) -> None:
        with self._stats_lock:
            self._active[name] -= 1
            if self._active[name] < 0:
                raise RuntimeError(f"resource accounting underflow for {name}")

    def _acquire(
        self,
        name: str,
        *,
        cancel_event: threading.Event | None,
        deadline: float | None,
    ) -> None:
        semaphore = self._semaphores[name]
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ResourceCancelled(f"cancelled while waiting for {name}")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise ResourceTimeout(f"timed out waiting for {name}")
            if cancel_event is None and deadline is None:
                acquired = semaphore.acquire()
            else:
                wait = self._poll_interval
                if remaining is not None:
                    wait = min(wait, max(remaining, 0.0))
                acquired = semaphore.acquire(timeout=wait)
            if acquired:
                self._mark_acquired(name)
                return

    @contextlib.contextmanager
    def slots(
        self,
        *resources: str,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> Iterator[None]:
        if timeout is not None and timeout < 0:
            raise ValueError("resource timeout must be non-negative")
        unknown = set(resources) - set(self._ORDER)
        if unknown:
            raise ValueError(f"unknown resources: {', '.join(sorted(unknown))}")
        requested = [name for name in self._ORDER if name in resources]
        held = list(getattr(self._local, "held", []))
        requested_orders = [self._ORDER.index(name) for name in requested]
        if set(held).intersection(requested_orders):
            raise ResourceOrderError(
                "nested resource request includes a resource already held"
            )
        if held and requested_orders and min(requested_orders) < max(held):
            raise ResourceOrderError(
                "nested resource request violates global acquisition order"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        acquired: list[str] = []
        try:
            for name in requested:
                self._acquire(name, cancel_event=cancel_event, deadline=deadline)
                acquired.append(name)
                held.append(self._ORDER.index(name))
                self._local.held = held
            yield
        finally:
            for name in reversed(acquired):
                order = self._ORDER.index(name)
                if not held or held[-1] != order:
                    raise RuntimeError("resource nesting accounting mismatch")
                held.pop()
                self._local.held = held
                self._mark_released(name)
                self._semaphores[name].release()

    def api_slot(self, **kwargs):
        return self.slots("api", **kwargs)

    def docker_slot(self, **kwargs):
        return self.slots("docker", **kwargs)

    def agent_slot(self, **kwargs):
        return self.slots("agent", **kwargs)
