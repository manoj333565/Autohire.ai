"""
services/scheduler_service.py — APScheduler Daemon & Background Job Service (F7)
=================================================================================
Coordinates recurring scheduled execution of the CareerPilot autonomous workflow:
  - APScheduler cron-based daily scheduling (Asia/Kolkata timezone default)
  - Configurable daily execution time from configuration or environment
  - Strict prevention of duplicate scheduler instances and jobs
  - Concurrency lock to prevent overlapping pipeline executions
  - Graceful signal handling (SIGINT, SIGTERM) and safe programmatic shutdown
  - Comprehensive logging of startup, shutdown, next execution, success, and failures
  - 30-minute interval heartbeat for daemon health monitoring
  - Fault tolerance: pipeline exceptions are captured and logged without killing the daemon
"""

import signal
import logging
import threading
from datetime import datetime
from typing import Dict, Any, Optional, Callable

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config as cfg

logger = logging.getLogger("CareerPilot.SchedulerService")

# Concurrency lock to prevent overlapping pipeline executions across threads/triggers
_PIPELINE_LOCK = threading.Lock()
_IS_PIPELINE_RUNNING = False
_GLOBAL_SCHEDULER_INSTANCE = None


class SchedulerService:
    """
    Automated background scheduler service for daily agent workflows.
    Ensures safe, non-overlapping, and configurable APScheduler job execution.
    """

    def __init__(
        self,
        timezone_str: Optional[str] = None,
        hour: Optional[int] = None,
        minute: Optional[int] = None,
        enabled: Optional[bool] = None,
    ):
        global _GLOBAL_SCHEDULER_INSTANCE

        self.timezone_str = (
            timezone_str
            or getattr(cfg, "SCHEDULE_TIMEZONE", None)
            or getattr(cfg, "TIMEZONE", "Asia/Kolkata")
        )
        self.hour = int(hour if hour is not None else getattr(cfg, "SCHEDULE_HOUR", 9))
        self.minute = int(minute if minute is not None else getattr(cfg, "SCHEDULE_MINUTE", 0))
        self.enabled = (
            getattr(cfg, "SCHEDULER_ENABLED", True) if enabled is None else bool(enabled)
        )

        try:
            self.tz = pytz.timezone(self.timezone_str)
        except Exception:
            logger.warning(f"[SchedulerService] Invalid timezone '{self.timezone_str}', defaulting to Asia/Kolkata")
            self.timezone_str = "Asia/Kolkata"
            self.tz = pytz.timezone("Asia/Kolkata")

        self._scheduler: Optional[Any] = None
        self._is_background: bool = False
        _GLOBAL_SCHEDULER_INSTANCE = self

    @classmethod
    def get_instance(cls) -> Optional["SchedulerService"]:
        """Get the active scheduler service singleton if available."""
        return _GLOBAL_SCHEDULER_INSTANCE

    @staticmethod
    def is_pipeline_running() -> bool:
        """Check whether a pipeline execution is currently in progress."""
        global _IS_PIPELINE_RUNNING
        return _IS_PIPELINE_RUNNING

    def run_safe_pipeline(self, job_func: Callable[[], Any], dry_run: bool = True) -> Dict[str, Any]:
        """
        Execute the pipeline with strict concurrency protection and fault logging.
        Guarantees that overlapping executions are skipped gracefully.
        """
        global _IS_PIPELINE_RUNNING

        acquired = _PIPELINE_LOCK.acquire(blocking=False)
        if not acquired or _IS_PIPELINE_RUNNING:
            logger.warning(
                "[SchedulerService] ⚠️ Pipeline execution already in progress. "
                "Skipping overlapping execution to prevent duplicate processing."
            )
            return {"status": "skipped", "reason": "overlapping_execution"}

        try:
            _IS_PIPELINE_RUNNING = True
            start_time = datetime.now(self.tz)
            logger.info(
                f"[SchedulerService] 🚀 Scheduled daily pipeline execution started at "
                f"{start_time.strftime('%Y-%m-%d %H:%M:%S %Z')} (Dry-Run: {dry_run})"
            )

            result = job_func()

            next_run_str = self.get_next_run_time_str()
            logger.info(
                f"[SchedulerService] ✅ Scheduled daily pipeline completed successfully. "
                f"Next scheduled execution: {next_run_str}"
            )
            return {"status": "success", "result": result}

        except Exception as e:
            logger.error(
                f"[SchedulerService] ❌ Scheduled pipeline execution failed with error: {e}",
                exc_info=True
            )
            return {"status": "failed", "error": str(e)}

        finally:
            _IS_PIPELINE_RUNNING = False
            _PIPELINE_LOCK.release()

    def build_scheduler(
        self,
        job_func: Callable[[], Any],
        is_background: bool = False,
        dry_run: bool = True
    ):
        """
        Configure and return an APScheduler instance with the daily job and heartbeat.
        Uses replace_existing=True and max_instances=1 to prevent duplicate or overlapping jobs.
        """
        if not self.enabled:
            logger.warning("[SchedulerService] SCHEDULER_ENABLED is False; building inert scheduler.")

        self._is_background = is_background
        SchedulerClass = BackgroundScheduler if is_background else BlockingScheduler

        # Reuse existing scheduler instance if already created and compatible
        if self._scheduler and type(self._scheduler) is SchedulerClass:
            scheduler = self._scheduler
        else:
            scheduler = SchedulerClass(timezone=self.tz)

        # Safe wrapped callback
        wrapped_callback = lambda: self.run_safe_pipeline(job_func=job_func, dry_run=dry_run)

        # Clean up any pre-existing jobs with the same IDs to guarantee zero duplicates
        if scheduler.get_job("careerpilot_daily_run"):
            try:
                scheduler.remove_job("careerpilot_daily_run")
            except Exception:
                pass
        if hasattr(scheduler, "_pending_jobs"):
            scheduler._pending_jobs = [
                pj for pj in scheduler._pending_jobs if pj[0].id != "careerpilot_daily_run"
            ]

        # 1. Main Daily Cron Job (Asia/Kolkata configurable daily time)
        scheduler.add_job(
            func=wrapped_callback,
            trigger=CronTrigger(hour=self.hour, minute=self.minute, timezone=self.tz),
            id="careerpilot_daily_run",
            name="CareerPilot Daily Autonomous Application Run",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600
        )

        # Clean up any pre-existing heartbeat job
        if scheduler.get_job("daemon_heartbeat"):
            try:
                scheduler.remove_job("daemon_heartbeat")
            except Exception:
                pass
        if hasattr(scheduler, "_pending_jobs"):
            scheduler._pending_jobs = [
                pj for pj in scheduler._pending_jobs if pj[0].id != "daemon_heartbeat"
            ]

        # 2. 30-Minute Heartbeat Job for health monitoring
        def _heartbeat():
            job = scheduler.get_job("careerpilot_daily_run")
            next_run = (
                job.next_run_time.strftime("%Y-%m-%d %H:%M %Z")
                if job and getattr(job, "next_run_time", None)
                else "None"
            )
            logger.info(
                f"💓 [HEARTBEAT] CareerPilot Daemon Active | Timezone: {self.timezone_str} | "
                f"Next scheduled run: {next_run} | In Progress: {self.is_pipeline_running()}"
            )

        scheduler.add_job(
            func=_heartbeat,
            trigger=IntervalTrigger(minutes=30, timezone=self.tz),
            id="daemon_heartbeat",
            name="CareerPilot Daemon Health Heartbeat",
            replace_existing=True,
            coalesce=True,
            max_instances=1
        )

        self._scheduler = scheduler

        trigger = CronTrigger(hour=self.hour, minute=self.minute, timezone=self.tz)
        now = datetime.now(self.tz)
        next_fire = trigger.get_next_fire_time(None, now)
        next_str = next_fire.strftime("%Y-%m-%d %H:%M:%S %Z") if next_fire else "None"

        logger.info(
            f"[SchedulerService] Registered daily run at {self.hour:02d}:{self.minute:02d} {self.timezone_str} "
            f"(First trigger: {next_str}) and 30-minute heartbeat."
        )
        return scheduler

    def get_next_run_time_str(self) -> str:
        """Calculate and return formatted next execution time string."""
        if self._scheduler:
            job = self._scheduler.get_job("careerpilot_daily_run")
            if job and getattr(job, "next_run_time", None):
                return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S %Z")

        trigger = CronTrigger(hour=self.hour, minute=self.minute, timezone=self.tz)
        now = datetime.now(self.tz)
        next_fire = trigger.get_next_fire_time(None, now)
        return next_fire.strftime("%Y-%m-%d %H:%M:%S %Z") if next_fire else "None"

    def get_job_info(self) -> Dict[str, Any]:
        """Inspect scheduled jobs and calculate next run time and execution status."""
        next_run = self.get_next_run_time_str()

        if not self._scheduler:
            return {
                "timezone": self.timezone_str,
                "schedule_time": f"{self.hour:02d}:{self.minute:02d}",
                "status": "configured_not_running",
                "next_run_time": next_run,
                "is_running": self.is_pipeline_running(),
                "scheduler_enabled": self.enabled,
                "jobs_count": 0,
            }

        return {
            "timezone": self.timezone_str,
            "schedule_time": f"{self.hour:02d}:{self.minute:02d}",
            "status": "running" if getattr(self._scheduler, "running", False) else "paused",
            "next_run_time": next_run,
            "jobs_count": len(self._scheduler.get_jobs()),
            "is_running": self.is_pipeline_running(),
            "scheduler_enabled": self.enabled,
        }

    def start_background(self, job_func: Callable[[], Any], dry_run: bool = True):
        """Start scheduler in background mode (non-blocking). Returns the scheduler instance."""
        if not self.enabled:
            logger.warning("[SchedulerService] SCHEDULER_ENABLED is False; background scheduler not started.")
            return None

        if self._scheduler and getattr(self._scheduler, "running", False):
            logger.warning("[SchedulerService] Scheduler is already running. Preventing duplicate background instance.")
            return self._scheduler

        scheduler = self.build_scheduler(job_func=job_func, is_background=True, dry_run=dry_run)
        logger.info(
            f"[SchedulerService] Starting background scheduler daemon (Timezone: {self.timezone_str}, "
            f"Scheduled: {self.hour:02d}:{self.minute:02d})..."
        )
        scheduler.start()
        return scheduler

    def start_blocking(self, job_func: Callable[[], Any], dry_run: bool = True):
        """Start scheduler in blocking daemon mode with POSIX/Windows signal handling."""
        if not self.enabled:
            logger.warning("[SchedulerService] SCHEDULER_ENABLED is False; blocking scheduler not started.")
            return

        if self._scheduler and getattr(self._scheduler, "running", False):
            logger.warning("[SchedulerService] Scheduler is already running. Preventing duplicate blocking instance.")
            return

        scheduler = self.build_scheduler(job_func=job_func, is_background=False, dry_run=dry_run)

        def _signal_handler(signum, frame):
            logger.info("Received termination signal. Shutting down CareerPilot scheduler daemon...")
            self.shutdown(wait=False)

        try:
            signal.signal(signal.SIGINT, _signal_handler)
            signal.signal(signal.SIGTERM, _signal_handler)
        except (ValueError, AttributeError):
            # When executed inside non-main thread
            pass

        next_run = self.get_next_run_time_str()
        logger.info(
            f"[SchedulerService] Starting blocking scheduler daemon. Next execution at {next_run}. "
            f"Press Ctrl+C to terminate cleanly."
        )

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("[SchedulerService] Scheduler daemon terminated cleanly.")
            self.shutdown(wait=False)

    def shutdown(self, wait: bool = False):
        """Gracefully stop and clean up the active scheduler instance."""
        if self._scheduler and getattr(self._scheduler, "running", False):
            logger.info("[SchedulerService] Shutting down active scheduler instance...")
            try:
                self._scheduler.shutdown(wait=wait)
            except Exception as e:
                logger.warning(f"[SchedulerService] Error during scheduler shutdown: {e}")
        logger.info("[SchedulerService] Scheduler service shut down safely.")
