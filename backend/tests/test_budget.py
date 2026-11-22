"""
Tests for app.budget request isolation and concurrency safety.
"""

import concurrent.futures
import tempfile
from pathlib import Path
from app.budget import RunBudgetTracker, get_current_budget, request_budget_scope


def test_budget_request_scope_isolation():
    b1 = RunBudgetTracker()
    b2 = RunBudgetTracker()

    with request_budget_scope(b1):
        curr = get_current_budget()
        assert curr is b1
        curr.try_consume("exa", units=1, enforce_daily_cap=False)
        assert curr.exa_calls == 1

        with request_budget_scope(b2):
            curr2 = get_current_budget()
            assert curr2 is b2
            assert curr2.exa_calls == 0
            curr2.try_consume("context7", units=2, enforce_daily_cap=False)
            curr2.record_cache_hit("firecrawl")
            assert curr2.context7_calls == 2
            assert curr2.cache_hits["firecrawl"] == 1

        # Back in outer scope b1
        curr_after = get_current_budget()
        assert curr_after is b1
        assert curr_after.exa_calls == 1
        assert curr_after.context7_calls == 0
        assert curr_after.cache_hits["firecrawl"] == 0


def test_concurrent_threads_budget_isolation():
    def worker(worker_id: int):
        with request_budget_scope():
            b = get_current_budget()
            for _ in range(worker_id):
                b.record_cache_hit("exa")
            # Consume 1 call per worker (within max_context7_calls_per_run=2)
            consumed, _ = b.try_consume("context7", units=1, enforce_daily_cap=False)
            assert consumed
            if worker_id % 2 == 1:
                b.exa_disabled = True

            # Verify isolated state
            assert b.cache_hits["exa"] == worker_id
            assert b.context7_calls == 1
            assert b.exa_disabled == (worker_id % 2 == 1)
            return b.cache_hits["exa"], b.context7_calls, b.exa_disabled

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(1, 6)]
        for i, fut in enumerate(futures, 1):
            hits, calls, disabled = fut.result()
            assert hits == i
            assert calls == 1
            assert disabled == (i % 2 == 1)
