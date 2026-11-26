"""
RepoMind request-scoped API budget and concurrency tracker.
Uses contextvars.ContextVar to guarantee complete isolation across concurrent FastAPI requests.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
import time
from typing import Generator, Literal

from app.config import EXT_CONFIG
from app.database import record_external_usage_and_check_cap


# Global process-wide pacing lock for Firecrawl's free-tier rate limit (10 RPM)
_GLOBAL_FIRECRAWL_LOCK = threading.Lock()
_GLOBAL_LAST_FIRECRAWL_TS: float = 0.0


@dataclass
class RunBudgetTracker:
    """Request-isolated budget tracking."""
    exa_calls: int = 0
    context7_calls: int = 0
    firecrawl_pages: int = 0
    cache_hits: dict[str, int] = field(
        default_factory=lambda: {"exa": 0, "context7": 0, "firecrawl": 0}
    )
    exa_disabled: bool = False
    total_exa_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_cache_hit(self, service: str) -> None:
        with self._lock:
            if service in self.cache_hits:
                self.cache_hits[service] += 1

    def was_used(self, service: str) -> bool:
        with self._lock:
            billed = {
                "exa": self.exa_calls,
                "context7": self.context7_calls,
                "firecrawl": self.firecrawl_pages,
            }.get(service, 0)
            return (billed + self.cache_hits.get(service, 0)) > 0

    def try_consume(
        self,
        service: Literal["exa", "context7", "firecrawl"],
        units: int = 1,
        actor_id: str = "default_user",
        cost_usd: float = 0.0,
        enforce_daily_cap: bool = True,
    ) -> tuple[bool, str]:
        """
        Atomically check and record both per-request budget and application-wide daily SQLite caps.
        """
        with self._lock:
            # 1. Per-request budget checks
            if service == "exa":
                if self.exa_disabled:
                    return False, "Exa disabled for this run after 402 Payment Required."
                if self.exa_calls + units > EXT_CONFIG.max_exa_calls_per_run:
                    return False, f"Exa per-run budget exceeded ({EXT_CONFIG.max_exa_calls_per_run} calls)."
            elif service == "context7":
                if self.context7_calls + units > EXT_CONFIG.max_context7_calls_per_run:
                    return False, f"Context7 per-run budget exceeded ({EXT_CONFIG.max_context7_calls_per_run} calls)."
            elif service == "firecrawl":
                if self.firecrawl_pages + units > EXT_CONFIG.max_firecrawl_pages_per_run:
                    return False, f"Firecrawl per-run page budget exceeded ({EXT_CONFIG.max_firecrawl_pages_per_run} pages)."

            # 2. Daily SQLite cap enforcement
            if enforce_daily_cap:
                allowed, msg = record_external_usage_and_check_cap(
                    service=service,
                    units=units,
                    cost_usd=cost_usd,
                    actor_id=actor_id,
                )
                if not allowed:
                    return False, msg

            # 3. Update request-scoped counters
            if service == "exa":
                self.exa_calls += units
                self.total_exa_usd += cost_usd
            elif service == "context7":
                self.context7_calls += units
            elif service == "firecrawl":
                self.firecrawl_pages += units

            return True, ""

    def pace_firecrawl(self) -> None:
        """Global throttle across all threads/requests for Firecrawl."""
        global _GLOBAL_LAST_FIRECRAWL_TS
        with _GLOBAL_FIRECRAWL_LOCK:
            now = time.time()
            wait = EXT_CONFIG.firecrawl_min_interval_sec - (now - _GLOBAL_LAST_FIRECRAWL_TS)
            if wait > 0 and _GLOBAL_LAST_FIRECRAWL_TS > 0:
                time.sleep(wait)
            _GLOBAL_LAST_FIRECRAWL_TS = time.time()

    def try_firecrawl_slot(self) -> bool:
        """Use the shared Firecrawl rate gate without making an interactive query wait."""
        global _GLOBAL_LAST_FIRECRAWL_TS
        if not _GLOBAL_FIRECRAWL_LOCK.acquire(blocking=False):
            return False
        try:
            now = time.time()
            if now - _GLOBAL_LAST_FIRECRAWL_TS < EXT_CONFIG.firecrawl_min_interval_sec:
                return False
            _GLOBAL_LAST_FIRECRAWL_TS = now
            return True
        finally:
            _GLOBAL_FIRECRAWL_LOCK.release()


# Request context variable
_CURRENT_BUDGET: contextvars.ContextVar[RunBudgetTracker | None] = contextvars.ContextVar(
    "current_budget", default=None
)


def get_current_budget() -> RunBudgetTracker:
    """Retrieve the current request's budget tracker, or a local fallback if outside request context."""
    budget = _CURRENT_BUDGET.get()
    if budget is None:
        # Fallback for standalone/test execution outside an explicit request scope
        budget = RunBudgetTracker()
        _CURRENT_BUDGET.set(budget)
    return budget


@contextmanager
def request_budget_scope(tracker: RunBudgetTracker | None = None) -> Generator[RunBudgetTracker, None, None]:
    """Context manager for setting up and tearing down request-scoped budget tracker."""
    new_tracker = tracker or RunBudgetTracker()
    token = _CURRENT_BUDGET.set(new_tracker)
    try:
        yield new_tracker
    finally:
        _CURRENT_BUDGET.reset(token)
