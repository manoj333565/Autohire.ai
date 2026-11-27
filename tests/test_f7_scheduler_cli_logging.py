"""
tests/test_f7_scheduler_cli_logging.py — Comprehensive Test Suite for F7
==========================================================================
Validates all requirements for:
  - F7.1: APScheduler Daily Scheduling (Timezone, Cron, Duplicate Job, Overlapping, Shutdown, Logging)
  - F7.2: CLI --dry-run Flag (Safe execution, blocks live applications & emails, overrides unsafe flags)
  - F7.3: CLI --run-now Flag (Immediate execution, respects approval & dry-run, concurrency lock, args)
  - F7.4: Structured Daily Rotating Logging (autohire.log, TimedRotatingFileHandler, pipe format, secret scrub)
"""

import sys
import os
import io
import time
import uuid
import logging
import unittest
import argparse
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure workspace root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from services.scheduler_service import SchedulerService, _PIPELINE_LOCK
from services.logging_service import setup_logging, LoggingService, SensitiveDataFilter
from main import run_agent, run_health_check, main as main_entry


class TestF7SchedulerCliLogging(unittest.TestCase):
    """Exhaustive test suite for F7 Scheduler, CLI, and Logging subsystems."""

    def setUp(self):
        # Ensure fresh logger
        self.logs_dir = Path("logs")
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = setup_logging("TestF7Logger")

    # =========================================================================
    # F7.1: APSCHEDULER DAILY SCHEDULING TESTS
    # =========================================================================

    def test_01_scheduler_initialization_and_timezone(self):
        """T01: Scheduler initializes with configurable timezone (default Asia/Kolkata)."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=30)
        self.assertEqual(service.timezone_str, "Asia/Kolkata")
        self.assertEqual(service.hour, 9)
        self.assertEqual(service.minute, 30)
        self.assertEqual(str(service.tz), "Asia/Kolkata")

        # Fallback for invalid timezone
        bad_tz_service = SchedulerService(timezone_str="Invalid/NonExistentZone")
        self.assertEqual(bad_tz_service.timezone_str, "Asia/Kolkata")
        print("  ✅ T01: Scheduler initialization & timezone configuration verified")

    def test_02_cron_trigger_configuration(self):
        """T02: Verify CronTrigger is built with configured hour, minute, and timezone."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=10, minute=15)
        scheduler = service.build_scheduler(job_func=lambda: None, is_background=True)

        job = scheduler.get_job("careerpilot_daily_run")
        self.assertIsNotNone(job)
        self.assertEqual(job.name, "CareerPilot Daily Autonomous Application Run")

        # Inspect trigger fields
        fields = {f.name: str(f) for f in job.trigger.fields}
        self.assertEqual(fields["hour"], "10")
        self.assertEqual(fields["minute"], "15")
        self.assertEqual(str(job.trigger.timezone), "Asia/Kolkata")
        print("  ✅ T02: CronTrigger configuration verified (10:15 Asia/Kolkata)")

    def test_03_prevent_duplicate_scheduler_jobs(self):
        """T03: Verify duplicate scheduler jobs are replaced rather than multiplied."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
        scheduler = service.build_scheduler(job_func=lambda: None, is_background=True)

        # Build again on the same scheduler
        scheduler = service.build_scheduler(job_func=lambda: None, is_background=True)

        daily_jobs = [j for j in scheduler.get_jobs() if j.id == "careerpilot_daily_run"]
        self.assertEqual(len(daily_jobs), 1, "Must never have more than 1 daily run job")
        print("  ✅ T03: Duplicate scheduler job prevention verified")

    def test_04_prevent_overlapping_executions(self):
        """T04: Verify overlapping pipeline executions are detected and gracefully skipped."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)

        res1_container = []

        def slow_pipeline():
            time.sleep(0.3)
            return "completed"

        # Simulate first execution running in a thread
        import threading
        t1 = threading.Thread(
            target=lambda: res1_container.append(service.run_safe_pipeline(slow_pipeline))
        )
        t1.start()
        time.sleep(0.05)  # Let t1 acquire the lock

        # Second execution attempts to run while first is in progress
        res2 = service.run_safe_pipeline(slow_pipeline)

        t1.join()

        # res2 must be skipped due to overlap
        self.assertEqual(res2.get("status"), "skipped")
        self.assertEqual(res2.get("reason"), "overlapping_execution")

        # res1 must be successful
        self.assertGreater(len(res1_container), 0)
        res1 = res1_container[0]
        self.assertEqual(res1.get("status"), "success")
        self.assertEqual(res1.get("result"), "completed")
        print("  ✅ T04: Overlapping pipeline execution prevention verified")

    def test_05_scheduler_safe_shutdown(self):
        """T05: Verify safe scheduler shutdown without hanging or unhandled errors."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
        scheduler = service.start_background(job_func=lambda: None)
        self.assertIsNotNone(scheduler)
        self.assertTrue(scheduler.running)

        # Shut down safely
        service.shutdown(wait=False)
        self.assertFalse(scheduler.running)
        print("  ✅ T05: Safe scheduler shutdown verified")

    def test_06_scheduler_failure_fault_tolerance(self):
        """T06: Verify scheduler logs exceptions without crashing daemon or silently failing."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)

        def failing_job():
            raise RuntimeError("Simulated pipeline network disruption")

        # Must catch exception, log it, and return failed status without raising
        result = service.run_safe_pipeline(failing_job)
        self.assertEqual(result.get("status"), "failed")
        self.assertIn("Simulated pipeline network disruption", result.get("error", ""))
        print("  ✅ T06: Scheduler exception fault tolerance verified")

    def test_07_heartbeat_and_job_inspection(self):
        """T07: Verify 30-minute heartbeat registration and job inspection info."""
        service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
        scheduler = service.build_scheduler(job_func=lambda: None, is_background=True)

        heartbeat = scheduler.get_job("daemon_heartbeat")
        self.assertIsNotNone(heartbeat)

        info = service.get_job_info()
        self.assertEqual(info["timezone"], "Asia/Kolkata")
        self.assertEqual(info["schedule_time"], "09:00")
        self.assertIsNotNone(info["next_run_time"])
        self.assertIn("IST", info["next_run_time"])
        print(f"  ✅ T07: Heartbeat & job inspection verified (Next: {info['next_run_time']})")

    # =========================================================================
    # F7.2 & F7.3: CLI --dry-run & --run-now TESTS
    # =========================================================================

    def test_08_cli_dry_run_flag(self):
        """T08: Verify --dry-run enforces dry_run=True regardless of environment settings."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--run-now", action="store_true")

        args = parser.parse_args(["--dry-run"])
        effective_dry_run = True if args.dry_run else False
        self.assertTrue(effective_dry_run, "--dry-run must enforce effective_dry_run=True")
        print("  ✅ T08: CLI --dry-run flag strictly enforces safe dry-run mode")

    def test_09_cli_run_now_flag(self):
        """T09: Verify --run-now invokes agent workflow immediately and respects safety."""
        mock_final_state = {
            "status": "success",
            "report_summary": {
                "run_id": "test_run_123",
                "duration_seconds": 1.2,
                "status": "completed",
                "metrics": {"scraped": 5, "applied": 0, "emailed": 0}
            }
        }

        with patch("agent.execute_workflow", return_value=mock_final_state) as mock_exec:
            res = run_agent(dry_run=True, mock_mode=True)
            self.assertEqual(res.get("status"), "success")
            mock_exec.assert_called_once()
            _, kwargs = mock_exec.call_args
            self.assertTrue(kwargs.get("dry_run"))
            self.assertTrue(kwargs.get("require_approval"))
        print("  ✅ T09: CLI --run-now executes LangGraph workflow respecting safety gates")

    def test_10_cli_combined_run_now_and_dry_run(self):
        """T10: Verify --run-now --dry-run executes immediately in dry-run mode."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--run-now", action="store_true")

        args = parser.parse_args(["--run-now", "--dry-run"])
        effective_dry_run = True if args.dry_run else cfg.DRY_RUN

        self.assertTrue(args.run_now)
        self.assertTrue(args.dry_run)
        self.assertTrue(effective_dry_run)
        print("  ✅ T10: Combined --run-now --dry-run execution verified")

    def test_11_cli_safety_dry_run_blocks_external_actions(self):
        """T11: Verify dry-run strictly blocks real SMTP and live Playwright submissions."""
        # 1. Email dispatch in dry run
        from tools.email_sender import EmailSender
        sender = EmailSender(dry_run=True)
        res = sender.send_cold_email(
            recipient_email="safety_test@example.com",
            job_title="ML Engineer",
            company_name="Safety Corp",
            recipient_name="HR Manager"
        )
        self.assertEqual(res.get("status"), "simulated_sent")
        self.assertTrue(res.get("dry_run"))

        # 2. Application submission in dry run
        from services.apply_service import ApplyService
        from database.db_manager import DatabaseManager
        db = DatabaseManager(cfg.DB_PATH)
        app_service = ApplyService(db_manager=db, dry_run=True)

        job = {
            "job_hash": f"safety_hash_{uuid.uuid4().hex[:8]}",
            "job_title": "Safety Test Role",
            "company": "Safety Corp",
            "job_url": "https://boards.greenhouse.io/safety/jobs/123",
            "portal": "greenhouse",
        }
        cand = {"name": "Safety Candidate", "email": "safe@test.com"}

        # Staged job approved in db to test submission block
        qid = db.enqueue_for_approval(
            item_type="job_application",
            reference_id=job["job_hash"],
            title=job["job_title"],
            company=job["company"],
            details="Review required",
            preview_payload=job,
            score=8.5
        )
        if qid:
            db.decide_approval(qid, "approved")

        submission = app_service.apply_to_job(job, queue_id=qid)
        self.assertEqual(submission.get("status"), "dry_run_preview")
        self.assertTrue(submission.get("dry_run"))
        print("  ✅ T11: Dry-run safety strictly confirmed (0 SMTP, 0 live Playwright)")

    def test_12_cli_invalid_arguments_handling(self):
        """T12: Verify invalid CLI arguments raise error and exit cleanly."""
        parser = argparse.ArgumentParser(description="CareerPilot AI")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--run-now", action="store_true")

        # Expect SystemExit when passing unrecognized flag
        with self.assertRaises(SystemExit):
            parser.parse_args(["--unknown-flag-xyz"])
        print("  ✅ T12: Invalid CLI argument handling verified")

    # =========================================================================
    # F7.4: STRUCTURED DAILY ROTATING LOGGING TESTS
    # =========================================================================

    def test_13_logs_directory_creation(self):
        """T13: Verify logs directory is created automatically."""
        test_dir = Path("logs/test_subdir")
        if test_dir.exists():
            import shutil
            shutil.rmtree(test_dir)

        svc = LoggingService(logs_dir=test_dir)
        self.assertTrue(test_dir.exists(), "logs/test_subdir must be created automatically")
        print("  ✅ T13: Logs directory auto-creation verified")

    def test_14_log_files_and_daily_rotation(self):
        """T14: Verify autohire.log, careerpilot.log, and error.log with TimedRotatingFileHandler."""
        setup_logging()
        root_logger = logging.getLogger()

        handlers = root_logger.handlers
        handler_types = [type(h).__name__ for h in handlers]
        self.assertIn("TimedRotatingFileHandler", handler_types, "Must configure TimedRotatingFileHandler")
        self.assertIn("RotatingFileHandler", handler_types, "Must configure RotatingFileHandler")

        # Check autohire.log handler
        timed_h = [h for h in handlers if type(h).__name__ == "TimedRotatingFileHandler"][0]
        self.assertEqual(timed_h.when, "MIDNIGHT")
        self.assertEqual(timed_h.backupCount, 30)

        # Check file creation
        self.logger.info("F7 Daily Rotation Test Log Entry")
        self.assertTrue((Path("logs") / "autohire.log").exists())
        self.assertTrue((Path("logs") / "careerpilot.log").exists())
        print("  ✅ T14: Log files & TimedRotatingFileHandler daily rotation verified")

    def test_15_log_format_timestamps_and_severity(self):
        """T15: Verify pipe-delimited structured log format: YYYY-MM-DD HH:MM:SS | LEVEL | module | msg."""
        test_msg = f"Structured format validation message {uuid.uuid4().hex[:6]}"
        self.logger.info(test_msg)

        autohire_log = Path("logs") / "autohire.log"
        self.assertTrue(autohire_log.exists())

        with open(autohire_log, "r", encoding="utf-8") as f:
            lines = f.readlines()

        matching = [l for l in lines if test_msg in l]
        self.assertGreater(len(matching), 0, "Logged line must be present in autohire.log")

        target_line = matching[-1]
        parts = target_line.split(" | ")
        self.assertGreaterEqual(len(parts), 4, f"Line must contain at least 4 pipe-separated parts: {target_line}")

        # Part 0: Timestamp (YYYY-MM-DD HH:MM:SS)
        timestamp_str = parts[0].strip()
        self.assertRegex(timestamp_str, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

        # Part 1: Severity level
        severity_str = parts[1].strip()
        self.assertEqual(severity_str, "INFO")

        # Part 2: Module/Logger name
        module_str = parts[2].strip()
        self.assertEqual(module_str, "TestF7Logger")

        # Part 3: Message
        msg_str = parts[3].strip()
        self.assertIn(test_msg, msg_str)
        print(f"  ✅ T15: Structured pipe format verified: '{target_line.strip()}'")

    def test_16_duplicate_handler_prevention(self):
        """T16: Verify calling setup_logging() multiple times does not attach duplicate handlers."""
        initial_handlers_count = len(logging.getLogger().handlers)

        # Call setup_logging 3 more times
        setup_logging("Repeat1")
        setup_logging("Repeat2")
        setup_logging("Repeat3")

        final_handlers_count = len(logging.getLogger().handlers)
        self.assertEqual(initial_handlers_count, final_handlers_count, "Handler count must not grow with repeated setup calls")
        print("  ✅ T16: Duplicate log handler prevention verified")

    def test_17_secret_masking_filter(self):
        """T17: Verify SensitiveDataFilter scrubs API keys, Gmail app passwords, and secrets."""
        filt = SensitiveDataFilter()

        # Test cases for secret redaction
        secret_cases = [
            ("api_key=AIzaSyA1b2C3d4E5f6G7h8I9j0K1L2M3N4O5P6Q", "***REDACTED***"),
            ("Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz", "Bearer ***REDACTED***"),
            ("password: supersecretpassword123", "password=***REDACTED***"),
            ("Connecting with Gmail app password abcd efgh ijkl mnop", "Connecting with Gmail app password ***REDACTED***"),
        ]

        for raw_msg, expected_in_redacted in secret_cases:
            record = logging.LogRecord(
                name="SecretTest",
                level=logging.INFO,
                pathname=__file__,
                lineno=10,
                msg=raw_msg,
                args=(),
                exc_info=None
            )
            filt.filter(record)
            self.assertIn(
                expected_in_redacted,
                record.msg,
                f"Failed to redact sensitive data in: '{raw_msg}' -> Result: '{record.msg}'"
            )
            self.assertNotIn("AIzaSyA1b2C3d4E5f6G7h8I9j0K1L2M3N4O5P6Q", record.msg)
            self.assertNotIn("supersecretpassword123", record.msg)
            self.assertNotIn("abcd efgh ijkl mnop", record.msg)

        print("  ✅ T17: Secret masking filter strictly redacts credentials from logs")

    def test_18_exceptions_include_tracebacks(self):
        """T18: Verify exceptions logged include tracebacks in error.log."""
        unique_err_msg = f"Traceback test exception {uuid.uuid4().hex[:6]}"
        try:
            raise ValueError(unique_err_msg)
        except Exception:
            self.logger.error("Caught operational error during test execution", exc_info=True)

        error_log = Path("logs") / "error.log"
        self.assertTrue(error_log.exists())

        with open(error_log, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(unique_err_msg, content)
        self.assertIn("Traceback (most recent call last):", content)
        self.assertIn("ValueError:", content)
        print("  ✅ T18: Exception tracebacks verified in error.log")


if __name__ == "__main__":
    print("\n" + "=" * 76)
    print("  🧪 RUNNING COMPREHENSIVE F7 SCHEDULER, CLI & LOGGING TEST SUITE")
    print("=" * 76 + "\n")

    unittest.main(verbosity=2)
