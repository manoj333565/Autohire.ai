"""
tests/run_all_tests.py — Master Test Runner for CareerPilot AI / Autohire.ai
===========================================================================
Executes all 19 offline unit, service, integration, hardening, and upgrade test suites.
"""

import sys
import unittest
import logging
from pathlib import Path

# Add root directory to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Suppress verbose debug logs during test run
logging.basicConfig(level=logging.ERROR)

TEST_MODULES = [
    "test_foundation",
    "test_database",
    "test_profile_service",
    "test_resume_intelligence",
    "test_filter_service",
    "test_scoring_service",
    "test_gemini_engine",
    "test_scrapers",
    "test_scraper_reliability",
    "test_apply_service",
    "test_email_service",
    "test_memory_service",
    "test_report_service",
    "test_scheduler_cli",
    "test_agent_workflow",
    "test_dashboard_api",
    "test_integration_end_to_end",
    "test_system_hardening",
    "test_autonomous_upgrade",
]


def run_all_suites():
    total_run = 0
    total_failures = 0
    total_errors = 0
    results_summary = []

    loader = unittest.TestLoader()

    for mod_name in TEST_MODULES:
        mod_path = f"tests/{mod_name}.py"
        try:
            # Import module directly
            import importlib.util
            spec = importlib.util.spec_from_file_location(mod_name, ROOT_DIR / "tests" / f"{mod_name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            suite = loader.loadTestsFromModule(mod)
            runner = unittest.TextTestRunner(verbosity=0)
            res = runner.run(suite)

            total_run += res.testsRun
            total_failures += len(res.failures)
            total_errors += len(res.errors)

            status = "✅ PASS" if res.wasSuccessful() else f"❌ FAIL ({len(res.failures)}F, {len(res.errors)}E)"
            results_summary.append((mod_name, res.testsRun, status))

        except Exception as e:
            total_errors += 1
            results_summary.append((mod_name, 0, f"❌ IMPORT ERROR: {e}"))

    print("\n" + "=" * 70)
    print("       AUTOHIRE.AI — MASTER TEST SUITE EXECUTION REPORT")
    print("=" * 70)
    for name, cnt, status in results_summary:
        print(f"  {status:<18} | {name:<32} ({cnt} tests)")
    print("=" * 70)
    print(f"  TOTAL TESTS RUN : {total_run}")
    print(f"  FAILURES        : {total_failures}")
    print(f"  ERRORS          : {total_errors}")
    print(f"  OVERALL RESULT  : {'🎉 ALL SUITES PASSED (100%)' if total_failures == 0 and total_errors == 0 else '⚠️ ISSUES DETECTED'}")
    print("=" * 70)

    return total_failures == 0 and total_errors == 0


if __name__ == "__main__":
    success = run_all_suites()
    sys.exit(0 if success else 1)
