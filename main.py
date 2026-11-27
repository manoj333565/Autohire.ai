"""
main.py — Master Execution Entry Point for CareerPilot AI / Autohire.ai
Supports CLI commands, background scheduler, health checks, and dashboard integration.

Usage:
  python main.py --health       # Run comprehensive environment health check
  python main.py --dry-run      # Execute agent workflow in safe dry-run mode
  python main.py --run-now      # Execute agent workflow (respects approval gates)
  python main.py --dashboard    # Start real-time monitoring web dashboard
  python main.py --schedule     # Start daily APScheduler daemon (Asia/Kolkata)
  python main.py --test-email   # Validate SMTP connection and configuration
  python main.py                # Default: Start dashboard + daily scheduler
"""
import sys
import os
import argparse
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure workspace root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from services.logging_service import setup_logging

logger = setup_logging("CareerPilot.Main")


def print_banner():
    """Display an executive startup banner."""
    print("=" * 72)
    print("  🚀 CAREERPILOT AI — AUTONOMOUS AI JOB APPLICATION AGENT")
    print(f"  Candidate:     {cfg.CANDIDATE_NAME}")
    print(f"  Contact Email: {cfg.PERSONAL_EMAIL}")
    print(f"  Database:      {cfg.DB_PATH}")
    print(f"  Safety Mode:   {'[DRY-RUN SAFE MODE: ON]' if cfg.DRY_RUN else '[LIVE SUBMISSION MODE: ON]'}")
    print(f"  Human Review:  {'[REQUIRED]' if cfg.REQUIRE_HUMAN_APPROVAL else '[AUTO-APPROVE: DISABLED]'}")
    print(f"  Timezone:      {cfg.TIMEZONE} | Scheduled: {cfg.SCHEDULE_HOUR:02d}:{cfg.SCHEDULE_MINUTE:02d}")
    print("=" * 72)


def run_health_check() -> bool:
    """Execute a comprehensive environment and dependency integrity check."""
    print_banner()
    logger.info("Executing comprehensive CareerPilot AI health check...")
    healthy = True

    # 1. Environment & Config
    logger.info("Checking configuration parameters:")
    logger.info(f"  - Configured Candidate: {cfg.CANDIDATE_NAME}")
    logger.info(f"  - Target Roles ({len(cfg.TARGET_ROLES)}): {', '.join(cfg.TARGET_ROLES[:4])}...")
    logger.info(f"  - Verified Skills: Proficient={len(cfg.VERIFIED_SKILLS.get('proficient', []))}, "
                f"Intermediate={len(cfg.VERIFIED_SKILLS.get('intermediate', []))}, "
                f"Learning={len(cfg.VERIFIED_SKILLS.get('learning', []))}")

    # 2. Filesystem & Datasets
    logger.info("Checking critical files and datasets:")
    files_to_check = [
        ("Candidate Profile", cfg.PROFILE_PATH, True),
        ("Candidate Resume", cfg.RESUME_PATH, True),
        ("Tracking Database", cfg.DB_PATH, True),
        ("HR Contacts Source PDF", cfg.PDF_HR_PATH, False),
    ]

    for label, path_str, mandatory in files_to_check:
        p = Path(path_str)
        if p.exists():
            size_kb = p.stat().st_size / 1024
            logger.info(f"  ✅ [FOUND] {label}: {p.name} ({size_kb:.1f} KB)")
        else:
            if mandatory:
                logger.error(f"  ❌ [MISSING] {label} at: {path_str}")
                healthy = False
            else:
                logger.warning(f"  ⚠️ [OPTIONAL MISSING] {label} at: {path_str}")

    # 3. Database Check
    try:
        import sqlite3
        conn = sqlite3.connect(cfg.DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        logger.info(f"  ✅ Database connected successfully. Existing tables: {tables}")
        for t in tables:
            cnt = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            logger.info(f"     - Table '{t}': {cnt} records")
        conn.close()
    except Exception as e:
        logger.error(f"  ❌ Database verification failed: {e}")
        healthy = False

    # 4. API & Integration Flags
    logger.info("Checking integration status:")
    logger.info(f"  - Google Gemini AI: {'✅ Configured' if cfg.USE_GEMINI else '⚠️ Not configured (dry-run/mock only)'}")
    logger.info(f"  - Gmail SMTP/IMAP:  {'✅ Configured' if cfg.USE_GMAIL else '⚠️ Not configured (local dry-run only)'}")
    logger.info(f"  - Mem0 Cloud:       {'✅ Configured' if cfg.USE_MEM0 else 'ℹ️ Optional (using SQLite memory)'}")
    logger.info(f"  - Tavily Search:    {'✅ Configured' if cfg.USE_TAVILY else 'ℹ️ Optional'}")
    logger.info(f"  - Apollo API:       {'✅ Configured' if cfg.USE_APOLLO else 'ℹ️ Optional'}")

    if healthy:
        logger.info("🎉 All critical foundation checks PASSED! System is operational.")
    else:
        logger.error("⚠️ Health check encountered errors. Review logs above.")

    return healthy


def run_agent(dry_run: bool = True, mock_mode: Optional[bool] = None):
    """Execute the full LangGraph Multi-Agent Workflow with concurrency protection."""
    from services.scheduler_service import _PIPELINE_LOCK

    acquired = _PIPELINE_LOCK.acquire(blocking=False)
    if not acquired:
        logger.warning(
            "⚠️ Pipeline execution already in progress. "
            "Skipping overlapping execution to prevent duplicate processing."
        )
        print("\n⚠️ Pipeline execution already in progress. Skipping overlapping run.\n")
        return {"status": "skipped", "reason": "overlapping_execution"}

    try:
        print_banner()
        if mock_mode is None:
            mock_mode = cfg.MOCK_MODE
        mode = "[DRY-RUN TEST]" if dry_run else "[LIVE OPERATION]"
        logger.info(f"Starting CareerPilot LangGraph Agent Workflow in {mode} (Mock Mode: {mock_mode})...")
        logger.info(f"Profile: {cfg.CANDIDATE_NAME} | Roles: {len(cfg.TARGET_ROLES)} | Match Threshold: {cfg.MATCH_SCORE_THRESHOLD}")

        from agent import execute_workflow
        final_state = execute_workflow(
            dry_run=dry_run,
            require_approval=cfg.REQUIRE_HUMAN_APPROVAL,
            mock_mode=mock_mode
        )

        summary = final_state.get("report_summary", {})
        metrics = summary.get("metrics", {})
        print("\n" + "=" * 72)
        print("  🏁 CAREERPILOT AI WORKFLOW EXECUTION COMPLETE")
        print(f"  Run ID:        {summary.get('run_id')} ({summary.get('duration_seconds')}s)")
        print(f"  Status:        {summary.get('status')}")
        print(f"  Jobs Scraped:  {metrics.get('scraped', 0)}")
        print(f"  Passed Filter: {metrics.get('passed_filters', 0)}")
        print(f"  Jobs Scored:   {metrics.get('scored', 0)}")
        print(f"  Staged Review: {metrics.get('staged_approvals', 0)}")
        print(f"  Applications:  {metrics.get('applied', 0)} ({'Simulated Dry-Run' if dry_run else 'Submitted'})")
        print(f"  Emails Staged: {metrics.get('emailed', 0)}")
        print(f"  PDF HR Drip:   {metrics.get('pdf_drip_sent', 0)} contacts")
        print("=" * 72 + "\n")
        return final_state
    finally:
        _PIPELINE_LOCK.release()


def start_dashboard():
    """Start the real-time monitoring web dashboard (implemented in Phase 12)."""
    print_banner()
    logger.info(f"Launching CareerPilot Dashboard on http://localhost:{cfg.DASHBOARD_PORT}...")
    try:
        from dashboard.app import app
        app.run(
            host="127.0.0.1",
            port=cfg.DASHBOARD_PORT,
            debug=False,
            use_reloader=False,
        )
    except ImportError:
        logger.warning("Dashboard module will be fully assembled in Phase 12. Placeholder server ready.")


def start_scheduler():
    """Start APScheduler daily trigger daemon (Asia/Kolkata timezone)."""
    print_banner()
    from services.scheduler_service import SchedulerService
    service = SchedulerService(
        timezone_str=cfg.SCHEDULE_TIMEZONE,
        hour=cfg.SCHEDULE_HOUR,
        minute=cfg.SCHEDULE_MINUTE,
        enabled=cfg.SCHEDULER_ENABLED
    )
    service.start_blocking(
        job_func=lambda: run_agent(dry_run=cfg.DRY_RUN),
        dry_run=cfg.DRY_RUN
    )


def run_scraper_cli(mock_mode: Optional[bool] = None):
    """Execute multi-portal job scraping across all portals."""
    if mock_mode is None:
        mock_mode = cfg.MOCK_MODE
    print_banner()
    logger.info(f"Executing Multi-Portal Job Scrapers (Mock Mode: {mock_mode})...")
    from scrapers.scraper_engine import ScraperEngine
    engine = ScraperEngine(mock_mode=mock_mode)
    results = engine.run_all(max_jobs_per_portal=5)
    print("\n" + "=" * 72)
    print(f"  📊 SCRAPING COMPLETE: {results['total_scraped']} normalized jobs aggregated")
    print("=" * 72)
    for portal, stats in results["portal_stats"].items():
        status_icon = "✅" if stats["status"] == "success" else "❌"
        print(f"  {status_icon} {portal.upper():<16} : {stats['jobs_found']} jobs ({stats['elapsed_seconds']}s)")
    print("=" * 72 + "\n")


def test_email_cli():
    """Validate email configuration, template rendering, and resume attachment."""
    print_banner()
    logger.info(f"Validating email dispatcher for: {cfg.GMAIL_ADDRESS} -> {cfg.PERSONAL_EMAIL}")
    from tools.email_sender import EmailSender
    sender = EmailSender(dry_run=True)
    res = sender.send_cold_email(
        recipient_email=cfg.PERSONAL_EMAIL,
        job_title="AI/ML Engineer",
        company_name="Evaluation Co",
        recipient_name="Candidate Evaluator"
    )
    print("\n" + "=" * 72)
    print("  📧 EMAIL DISPATCH TEST SUCCESSFUL (Dry-Run)")
    print(f"  Recipient:   {res['recipient']}")
    print(f"  Subject:     {res['subject']}")
    print(f"  Attachment:  {'Attached' if res['has_attachment'] else 'Missing'}")
    print("=" * 72 + "\n")


def main():
    parser = argparse.ArgumentParser(description="CareerPilot AI — Autonomous AI Job Application Agent")
    parser.add_argument("--health", action="store_true", help="Run comprehensive environment health check")
    parser.add_argument("--dry-run", action="store_true", help="Run agent workflow in safe dry-run mode")
    parser.add_argument("--run-now", action="store_true", help="Run agent workflow immediately")
    parser.add_argument("--dashboard", action="store_true", help="Start monitoring web dashboard")
    parser.add_argument("--schedule", action="store_true", help="Start background scheduler daemon")
    parser.add_argument("--scrape", action="store_true", help="Run multi-portal job scrapers (mock/live)")
    parser.add_argument("--mock-mode", action="store_true", help="Force mock data for scrapers and AI")
    parser.add_argument("--test-email", action="store_true", help="Test SMTP email dispatch")

    args = parser.parse_args()

    # Determine effective dry_run mode:
    # If --dry-run is supplied, it strictly forces True (overrides any unsafe settings).
    # Otherwise, it strictly respects cfg.DRY_RUN from configuration (defaulting to True).
    effective_dry_run = True if args.dry_run else cfg.DRY_RUN

    # Determine mock_mode from CLI flags or config
    effective_mock_mode = True if args.mock_mode else cfg.MOCK_MODE

    if args.health:
        sys.exit(0 if run_health_check() else 1)
    elif args.run_now or args.dry_run:
        # Handles:
        # 1. python main.py --dry-run           -> immediate execution in dry_run=True mode
        # 2. python main.py --run-now           -> immediate execution respecting cfg.DRY_RUN
        # 3. python main.py --run-now --dry-run -> immediate execution in dry_run=True mode
        logger.info(
            f"CLI workflow invocation: run_now={args.run_now}, dry_run={args.dry_run} -> "
            f"effective_dry_run={effective_dry_run}, mock_mode={effective_mock_mode}"
        )
        res = run_agent(dry_run=effective_dry_run, mock_mode=effective_mock_mode)
        sys.exit(0 if (isinstance(res, dict) and res.get("status") != "failed") else 1)
    elif args.dashboard:
        start_dashboard()
    elif args.schedule:
        if not cfg.SCHEDULER_ENABLED:
            logger.warning("SCHEDULER_ENABLED is False in configuration. Scheduler daemon will not start.")
            print("\n⚠️ SCHEDULER_ENABLED is False in configuration. Exiting.\n")
            sys.exit(0)
        start_scheduler()
    elif args.scrape:
        run_scraper_cli(mock_mode=effective_mock_mode)
    elif args.test_email:
        test_email_cli()
    else:
        # Default behavior: run health check, then report status / instructions
        healthy = run_health_check()
        if healthy and cfg.SCHEDULER_ENABLED:
            print("\n💡 Run 'python main.py --run-now --dry-run' to execute pipeline immediately in safe mode.")
            print("💡 Run 'python main.py --schedule' to start daily background scheduler.\n")


if __name__ == "__main__":
    main()
