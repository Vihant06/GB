"""
Test discovery and runner for RepoMind test suite.
Discovers and executes all test_* functions in tests/*.py files.
"""

import importlib
import inspect
import os
import sys
import time
import traceback
from pathlib import Path

# Ensure root directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def run_tests():
    tests_dir = BASE_DIR / "tests"
    test_files = sorted(tests_dir.glob("test_*.py"))

    passed = 0
    failed = 0
    errors = []

    print(f"\n==================== Running RepoMind Test Suite ====================")
    start_time = time.time()

    for file_path in test_files:
        module_name = f"tests.{file_path.stem}"
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            print(f"FAILED to import {module_name}: {e}")
            traceback.print_exc()
            failed += 1
            continue

        test_funcs = [
            (name, func)
            for name, func in inspect.getmembers(mod, inspect.isfunction)
            if name.startswith("test_")
        ]

        for name, func in test_funcs:
            test_desc = f"{file_path.stem}::{name}"
            try:
                func()
                print(f"PASSED  {test_desc}")
                passed += 1
            except Exception as e:
                print(f"FAILED  {test_desc}")
                traceback.print_exc()
                errors.append((test_desc, str(e)))
                failed += 1

    elapsed = time.time() - start_time
    print(f"\n======================================================================")
    print(f"Results: {passed} passed, {failed} failed in {elapsed:.2f}s")
    if failed > 0:
        print("\nFailures:")
        for desc, err in errors:
            print(f" - {desc}: {err}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED!\n")


if __name__ == "__main__":
    run_tests()
