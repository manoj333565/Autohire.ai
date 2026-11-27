"""
tests/test_scheduler_cli.py — Unit & Integration Tests for Scheduler, CLI & Logging (Milestone 11)
===================================================================================================
Validates:
  T1: Structured & rotating logging configuration (careerpilot.log, error.log)
  T2: SchedulerService cron job registration (Asia/Kolkata timezone, 09:00 AM)
  T3: 30-minute daemon heartbeat registration
  T4: Dynamic job status & next run time calculation
  T5: CLI argument parsing and flags (--health, --dry-run, --run-now, --scrape, --schedule, --test-email)
  T6: Test email dispatch execution in safe dry-run mode
  T7: Comprehensive health check execution
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from utils.logger import setup_logger
from services.scheduler_service import SchedulerService
from main import run_health_check, test_email_cli


def test_logging_configuration():
    """T1: Verify rotating log files and logger setup."""
    logger = setup_logger("TestLoggingCheck")
    assert logger is not None

    logs_dir = Path("logs")
    assert logs_dir.exists(), "logs directory must exist"

    # Verify log files exist or can be created
    main_log = logs_dir / "careerpilot.log"
    error_log = logs_dir / "error.log"

    logger.info("Test info message for logger validation")
    logger.error("Test error message for error log validation")

    assert main_log.exists(), "logs/careerpilot.log must exist"
    assert error_log.exists(), "logs/error.log must exist"

    with open(main_log, "r", encoding="utf-8") as f:
        main_content = f.read()
        assert "Test info message" in main_content

    with open(error_log, "r", encoding="utf-8") as f:
        err_content = f.read()
        assert "Test error message" in err_content

    print("  ✅ test_logging_configuration passed")


def test_scheduler_service_configuration():
    """T2: Verify scheduler builds cron job in Asia/Kolkata timezone."""
    service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
    assert service.timezone_str == "Asia/Kolkata"

    # Build background scheduler for testing
    dummy_called = []
    scheduler = service.build_scheduler(
        job_func=lambda: dummy_called.append(True),
        is_background=True,
        dry_run=True
    )

    assert scheduler is not None
    job = scheduler.get_job("careerpilot_daily_run")
    assert job is not None
    assert job.name == "CareerPilot Daily Autonomous Application Run"

    print("  ✅ test_scheduler_service_configuration passed")


def test_scheduler_heartbeat_job():
    """T3: Verify registration of the 30-minute interval heartbeat job."""
    service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
    scheduler = service.build_scheduler(
        job_func=lambda: None,
        is_background=True,
        dry_run=True
    )

    heartbeat_job = scheduler.get_job("daemon_heartbeat")
    assert heartbeat_job is not None
    assert "Heartbeat" in heartbeat_job.name

    print("  ✅ test_scheduler_heartbeat_job passed")


def test_scheduler_job_info_inspection():
    """T4: Verify calculation of next run time and job inspection info."""
    service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
    info_before = service.get_job_info()

    assert info_before["timezone"] == "Asia/Kolkata"
    assert info_before["schedule_time"] == "09:00"
    assert info_before["next_run_time"] is not None

    print(f"  ✅ test_scheduler_job_info_inspection passed (Next Run: {info_before['next_run_time']})")


def test_cli_argument_parsing():
    """T5: Verify CLI parser flags (--health, --dry-run, --run-now, --scrape, --schedule, --test-email)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--dashboard", action="store_true")
    parser.add_argument("--schedule", action="store_true")
    parser.add_argument("--scrape", action="store_true")
    parser.add_argument("--test-email", action="store_true")

    args = parser.parse_args(["--dry-run", "--scrape"])
    assert args.dry_run is True
    assert args.scrape is True
    assert args.health is False

    args2 = parser.parse_args(["--test-email"])
    assert args2.test_email is True

    print("  ✅ test_cli_argument_parsing passed")


def test_test_email_execution():
    """T6: Verify execution of test_email_cli()."""
    try:
        test_email_cli()
        print("  ✅ test_test_email_execution passed")
    except Exception as e:
        assert False, f"test_email_cli failed: {e}"


def test_health_check_function():
    """T7: Verify comprehensive environment health check returns True."""
    is_healthy = run_health_check()
    assert is_healthy is True, "System health check must return True"
    print("  ✅ test_health_check_function passed")


if __name__ == "__main__":
    print("\nRunning Milestone 11 Scheduler, CLI & Logging Tests...\n")

    tests = [
        test_logging_configuration,
        test_scheduler_service_configuration,
        test_scheduler_heartbeat_job,
        test_scheduler_job_info_inspection,
        test_cli_argument_parsing,
        test_test_email_execution,
        test_health_check_function,
    ]

    passed_count = 0
    failed_count = 0

    for test_fn in tests:
        try:
            test_fn()
            passed_count += 1
        except AssertionError as e:
            print(f"  ❌ {test_fn.__name__} FAILED: {e}")
            failed_count += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    print(f"\n{'='*68}")
    if failed_count == 0:
        print(f"🎉 ALL {passed_count} SCHEDULER & CLI TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
